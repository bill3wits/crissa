"""Clip-open fit + path. Talking frame is classify, then remap.

Layer A: rest pins / even r0. Not a speed cache.
Layer B: prechoice path. live_lookup returns a path, not maps.
Layer C: two-tier dental cache keyed by dest *frame*, not viseme.

The retired dest-pixel map cache crawled because a viseme (or "current
frame" without identity) reused another frame's chin after a nod. A,B,C
come from that dest frame's mouth corners and chin. Key (loop, frame,
version) and a nod changes the key.

Tier 1: (loop_id, frame_index, version) → dental dict (A, B, C, …).
Tiny. Safe to precompute on play() next to _pl_tiles.

Tier 2: LRU (loop, frame, viseme, rel, p, pin, version) → dest-pixel
maps. Speech only touches a handful of frames per class per wrap.

HOUSE_MOUTH_DENTAL_CACHE=0 rolls both off. HOUSE_MOUTH_DENTAL_MAPS
bounds the LRU (default 256). Version is head.json mtime_ns+size so a
re-encode / re-landmark misses. Dest-nose EMA is a function of the loop
frames — do not add random liveliness jitter into cached A,B,C or maps;
apply jitter after lookup, or it repeats as a pattern on every wrap.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import os
import threading

import numpy as np

from head_frame import (
    _dental_even_weights,
    cheeks_dest_solve,
    cheeks_maps_from_solve,
    crop_maps,
    uncrop_maps,
    _from_uv,
    dental_r0_sag_image,
    dental_r0_y,
    diamond_anchors,
    ema_nose,
    fit_dental_r0,
    warp_dental_maps,
)
from mouth_playlist import bin_yaw


@dataclass
class Scene:
    rest: dict
    sag_px: float
    c2: float
    c6: float
    a1: float
    cheek_from: str | None
    nose: np.ndarray
    L: np.ndarray
    R: np.ndarray
    chin: np.ndarray


def _yaw_deg(fr: dict) -> float:
    pose = fr.get("pose") if fr else None
    try:
        return float(np.asarray(pose).ravel()[1]) if pose is not None else 0.0
    except (IndexError, TypeError, ValueError):
        return 0.0


def _mouth_w(fr: dict) -> float:
    m = fr.get("mouth") if fr else None
    if m is None:
        return 0.0
    v = np.asarray(m, dtype=np.float64).ravel()
    return float(v[2]) if v.size >= 3 else 0.0


def _dental_mid_xy(dental: dict) -> np.ndarray | None:
    if not dental:
        return None
    y0 = float(dental_r0_y(0.0, dental))
    return _from_uv(0.0, y0, dental["Cxy"], dental["ex"], dental["ey"])


def build_scene(rest_head: dict) -> Scene:
    """Layer A. Fit once on dest rest. Odds stay 0 except a tiny a1."""
    r0 = fit_dental_r0(rest_head)
    sag = abs(dental_r0_sag_image(r0)) if r0 is not None else 0.0
    c2, c6 = _dental_even_weights(r0) if r0 is not None else (0.7, 0.3)
    nose, L, R, chin = diamond_anchors(rest_head)
    a1 = 0.0
    yb = bin_yaw(_yaw_deg(rest_head))
    mid = 0.5 * (np.asarray(L, dtype=np.float64) + np.asarray(R, dtype=np.float64))
    dmid = _dental_mid_xy(r0)
    # Off-front bins are L/R. Face-on 0/l/r keep a1=0.
    if yb in ("L", "R") and dmid is not None:
        if float(np.linalg.norm(dmid[:2] - mid[:2])) > 3.0:
            a1 = 0.02
    return Scene(
        rest=rest_head, sag_px=float(sag), c2=float(c2), c6=float(c6),
        a1=float(a1), cheek_from=rest_head.get("cheek_from"),
        nose=np.asarray(nose, dtype=np.float64).ravel()[:2],
        L=np.asarray(L, dtype=np.float64).ravel()[:2],
        R=np.asarray(R, dtype=np.float64).ravel()[:2],
        chin=np.asarray(chin, dtype=np.float64).ravel()[:2],
    )


def choose_path(klass: str | None, dst: dict, *, warp: str,
                sag_px: float = 0.0, cheek_from: str | None = None) -> str:
    """Layer B. Path only. sag≥8 must not select diamond (harvest-a rest 21.84).

    |w|<8 is dest mouth-box width (degenerate). Locked-axis |w| is tiny
    when face-on (W_MIN_PX) — that is not this guard.
    """
    w = str(warp or "dental").strip().lower()
    if w and w not in ("auto", "dental", "affine", "triad", "cheeks",
                       "plane", "diamond"):
        w = "dental"
    yaw = abs(_yaw_deg(dst))
    mw = _mouth_w(dst)
    if yaw >= 35.0 or mw < 8.0:
        if w == "cheeks" or (w == "auto" and cheek_from == "106"):
            return "cheeks"
        if w in ("auto", "triad", "diamond", "dental", "affine", "plane"):
            return "triad"
        return w
    if w == "auto":
        return "affine"
    if w == "diamond":
        return "diamond"
    return w


def live_lookup(scene: Scene | None, klass: str | None, dst: dict,
                warp: str = "dental") -> str:
    """Talking frame: classify. Returns a path, not maps."""
    sag = scene.sag_px if scene is not None else 0.0
    cheek = scene.cheek_from if scene is not None else None
    return choose_path(klass, dst, warp=warp, sag_px=sag, cheek_from=cheek)


# ---- Layer C: dest-frame dental cache --------------------------------------
# A,B,C are per dest frame. Maps are per (frame, viseme, p). Not per viseme.

_LOCK = threading.Lock()
_R0: dict[tuple, dict | None] = {}
_MAPS: OrderedDict[tuple, tuple] = OrderedDict()
_STATS = {"r0_hit": 0, "r0_miss": 0, "maps_hit": 0, "maps_miss": 0}
# 2026-09-14 (the User: "freeze those coefficients in cache to save compute"): three
# more LRUs beside the dental pair, all keyed by dest FRAME identity like Tier 2.
#   _SOLVE  (loop, frame, version, nose½px, scale, wh)            → cheeks_dest_solve (float32 t, h)
#   _CHEEKS (…, rel, pin dials, tile_hw)                           → the PINNED maps, cropped to their ROI, + the pin record
#   _PIN    (loop, frame, version, rel-or-hub, M rounded 0.01)     → (dx, dy, ncc) of pin_nose_pixel
# HOUSE_MOUTH_CHEEKS_CACHE=0 / HOUSE_MOUTH_PIN_CACHE=0 roll them off; HOUSE_MOUTH_CHEEKS_MAPS,
# HOUSE_MOUTH_CHEEKS_SOLVES, HOUSE_MOUTH_PIN_SHIFTS bound them (256 / 1600 / 32768 since
# 2026-09-19; HOUSE_POLY_CACHE_SIZING=0 → 256 / 256 / 4096).
_SOLVE: "OrderedDict[tuple, dict]" = OrderedDict()
_CHEEKS: "OrderedDict[tuple, tuple]" = OrderedDict()
_PIN: "OrderedDict[tuple, tuple]" = OrderedDict()
_DIAMOND: "OrderedDict[tuple, tuple]" = OrderedDict()
_STATS.update({"solve_hit": 0, "solve_miss": 0, "cheeks_hit": 0, "cheeks_miss": 0,
               "pin_hit": 0, "pin_miss": 0, "diamond_hit": 0, "diamond_miss": 0})
_R0_CAP = 8192
# BYTES, READ OFF THE PROCESS (2026-09-19): the bench was raised on a guessed 0.47 MB a
# close-up map and a cropped cheeks map is the solve's whole strip box — 1.45 MB — so the
# fill was 3x the guess. Every array-holding store keeps a running byte total (put, evict,
# drop, reset); mouth_cache_stats() reports it, so a cap is sized from the glass.
# _TRI (loop, frame, kind, version, rel, band, dials) -> a turn landing ("land") or its pasted picture ("paste"), every
# frame-sized array cropped to the union of its non-zero pixels (the User 2026-10-18, the phone turn at under 4 fps:
# "use our cached strategy"). The class only chooses the tile, so a frame held on the Composer is 19 entries.
# HOUSE_MOUTH_TRI_CACHE=0 rolls it off; HOUSE_MOUTH_TRI_CACHE_MB bounds it (1024).
_TRI: "OrderedDict[tuple, dict]" = OrderedDict()
_STATS.update({"tri_hit": 0, "tri_miss": 0})
_BYTES: dict[int, int] = {id(s): 0 for s in (_MAPS, _SOLVE, _CHEEKS, _DIAMOND, _TRI)}


def _nbytes(obj) -> int:
    """Array bytes inside a cached value (tuples / dicts of ndarrays); scalars are 0."""
    if isinstance(obj, np.ndarray):
        return int(obj.nbytes)
    if isinstance(obj, dict):
        return sum(_nbytes(v) for v in obj.values())
    if isinstance(obj, (tuple, list)):
        return sum(_nbytes(v) for v in obj)
    return 0


def _acct(store, val, sign: int) -> None:
    sid = id(store)
    if sid in _BYTES:
        _BYTES[sid] += sign * _nbytes(val)


def dental_cache_on() -> bool:
    """HOUSE_MOUTH_DENTAL_CACHE=0 is the rollback."""
    return os.environ.get("HOUSE_MOUTH_DENTAL_CACHE", "1").strip().lower() not in (
        "0", "false", "no", "off")


def dental_maps_limit() -> int:
    try:
        return max(0, int(os.environ.get("HOUSE_MOUTH_DENTAL_MAPS", "256")))
    except (TypeError, ValueError):
        return 256


def dental_cache_stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def reset_dental_cache() -> None:
    """Every mouth cache, not only the dental pair (the name predates them)."""
    with _LOCK:
        _R0.clear()
        _MAPS.clear()
        _SOLVE.clear()
        _CHEEKS.clear()
        _PIN.clear()
        _DIAMOND.clear()
        _TRI.clear()
        _CHEEKS_Z.clear()
        _Z_BYTES[0] = 0
        _AHEAD_PUT.clear()
        for k in _STATS:
            _STATS[k] = 0
        for k in _BYTES:
            _BYTES[k] = 0


reset_mouth_caches = reset_dental_cache


def reset_tile_caches() -> None:
    """What /v1/reload drops (2026-09-19): the stores a TILE enters — dental maps, cheeks
    maps, pins, diamonds — and the dental r0 (kw/kh are not in its key). The cheeks
    SOLVES stay: a solve is a function of the dest frame (loop, frame, head.json version,
    EMA'd nose, scale, size) and never of a tile, and a re-pick or install changes tiles
    only. A film swap and a repin still call reset_mouth_caches(). 229 reloads on
    09-18 wiped the wall's solves every few minutes; no cap could matter under that.
    HOUSE_POLY_CACHE_SIZING=0 → the full reset, as before."""
    if not cache_sizing_on():
        reset_dental_cache()
        return
    with _LOCK:
        for store in (_R0, _MAPS, _CHEEKS, _PIN, _DIAMOND, _TRI):
            store.clear()
            if id(store) in _BYTES:
                _BYTES[id(store)] = 0
        _CHEEKS_Z.clear()                        # the compressed tier holds what _CHEEKS holds, packed
        _Z_BYTES[0] = 0
        _AHEAD_PUT.clear()
        for k in _STATS:
            if not k.startswith("solve_"):
                _STATS[k] = 0


def drop_loop(loop_id) -> int:
    """Drop every cached entry of ONE loop, all six stores (every key starts with the
    loop id). For a bench leaving a clip for good (the User 2026-09-18 "can we clear it at
    clip end or change"): a bench holds one clip and never comes back on its own, so a
    long take's stale frames and dial combos would otherwise sit in the LRU against the
    next clip. Never on a wrap (the next wrap is where the hits come from) and never on
    the wall (it bounces work <-> flourish all day; the LRU ages an unplayed clip out).
    Returns the number of entries dropped."""
    lid = str(loop_id)
    n = 0
    with _LOCK:
        for store in (_R0, _MAPS, _SOLVE, _CHEEKS, _PIN, _DIAMOND, _TRI):
            dead = [k for k in store if k and k[0] == lid]
            for k in dead:
                _acct(store, store.pop(k), -1)
            n += len(dead)
        dead = [k for k in _CHEEKS_Z if k and k[0] == lid]
        for k in dead:
            _Z_BYTES[0] -= len(_CHEEKS_Z.pop(k))
        n += len(dead)
    return n


def nose_ema_track(dests, k: float, zero_lag: bool = False) -> list:
    """The EMA'd dest nose of every loop frame, as CONTIGUOUS play makes it (2026-09-19).

    `dests` is the loop's unpacked head frames (None where a frame has no head). The EMA
    restarts at frame 0 and after every headless frame, exactly as _dest_for_paste does
    when painting runs frame after frame — so under contiguous play the result is
    bit-identical to the live EMA. What changes is a SKIP: the live EMA snapped to the raw
    nose on any |di| > 1 (a dropped frame, a line starting mid-lap), so one frame came
    back on another half pixel and the solve cache grew another key (replay on the real
    tracks: 1.34 keys a frame at 2 % dropped frames on crissa-close-harvest; the bench read
    322 keys / 241 frames). Looked up by frame index it is one nose, one key, per frame.
    Returns [(raw_nose_xy, pinned_xy) | None, ...].

    `zero_lag` (2026-10-02, off by default): a one-way EMA trails a moving head by (1-k)/k frames of
    travel (k 0.35: 1.86 frames - measured 6-9 px behind her on a 3-5 px/frame move). The whole track
    is known before a frame is painted, so each run of frames is smoothed forwards AND backwards and
    the two are averaged: the same quieting of a still head, no trail on a moving one."""
    if zero_lag and float(k) > 0.0:
        return _nose_track_zero_lag(dests, float(k))
    out: list = []
    prev = None
    prev_i = None
    for i, d in enumerate(dests or ()):
        if d is None or d.get("nose") is None:
            out.append(None)
            continue
        nose = d["nose"]
        if prev_i is None or i - prev_i > 1:
            pinned = ema_nose(None, nose, 0.0)
        else:
            pinned = ema_nose(prev, nose, k)
        out.append((np.asarray(nose, dtype=np.float64).ravel()[:2].copy(), pinned))
        prev, prev_i = pinned, i
    return out


def _nose_track_zero_lag(dests, k: float) -> list:
    """nose_ema_track with no trail: forward and backward EMA over each unbroken run, averaged."""
    raw = [None if (d is None or d.get("nose") is None) else np.asarray(d["nose"], dtype=np.float64).ravel()[:2].copy()
           for d in (dests or ())]
    out: list = [None] * len(raw)
    i = 0
    while i < len(raw):
        if raw[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < len(raw) and raw[j + 1] is not None:
            j += 1
        run = raw[i:j + 1]
        pad = min(len(run) - 1, 12)          # the run continued past each end by odd reflection (as filtfilt pads),
        ext = ([2 * run[0] - run[t] for t in range(pad, 0, -1)] + run      # so a head already moving at a run's
               + [2 * run[-1] - run[-1 - t] for t in range(1, pad + 1)])   # first or last frame is not trailed either
        fwd, prev = [], None
        for x in ext:
            prev = ema_nose(prev, x, k)
            fwd.append(prev)
        prev = None
        for t in range(len(ext) - 1, -1, -1):
            prev = ema_nose(prev, ext[t], k)
            if pad <= t < pad + len(run):
                out[i + t - pad] = (raw[i + t - pad], (fwd[t] + prev) / 2.0)
        i = j + 1
    return out


def loop_version(path) -> tuple[int, int]:
    """head.json identity. Re-encode / re-landmark changes this."""
    try:
        st = Path(path).stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, 0)


def p_key_from_dental(dental: dict | None) -> tuple[float, float, float]:
    """Phrase sag recipe. Same p every class; a new phrase must miss."""
    if not dental:
        return (0.0, 0.0, 0.0)
    return (
        round(float(dental.get("A") or 0.0), 8),
        round(float(dental.get("B") or 0.0), 8),
        round(float(dental.get("C") or 0.0), 8),
    )


def dental_r0_cached(loop_id, frame_i: int, version, dst: dict | None,
                     kw: float = 0.28, kh: float = 0.18,
                     scale: float = 1.0) -> dict | None:
    """Tier 1. Fit dest corners+chin. Nose pin / EMA do not belong here."""
    if dst is None:
        return None
    if not dental_cache_on():
        return fit_dental_r0(dst, kw, kh, scale)
    key = (str(loop_id), int(frame_i), version)
    with _LOCK:
        if key in _R0:
            _STATS["r0_hit"] += 1
            return _R0[key]
    fitted = fit_dental_r0(dst, kw, kh, scale)
    with _LOCK:
        _R0[key] = fitted
        _STATS["r0_miss"] += 1
        while len(_R0) > _R0_CAP:
            _R0.pop(next(iter(_R0)))
    return fitted


def warm_dental_r0(loop_id, version, heads, *, kw: float = 0.28,
                   kh: float = 0.18, scale: float = 1.0) -> int:
    """Precompute A,B,C for every dest frame of this loop. Next to _pl_tiles."""
    if not dental_cache_on():
        return 0
    n = 0
    for i, fr in enumerate(heads or ()):
        if fr is None:
            continue
        dental_r0_cached(loop_id, i, version, fr, kw=kw, kh=kh, scale=scale)
        n += 1
    return n


def dental_maps_cached(*, loop_id, frame_i: int, version, viseme, rel, p_key,
                       src, dst, wh, M=None, scale: float = 1.0,
                       tile_hw=None, dental=None, pin=None,
                       kw: float = 0.28, kh: float = 0.18):
    """Tier 2 LRU. Frame identity is in the key so a nod cannot reuse maps."""
    if not dental_cache_on() or dental_maps_limit() <= 0:
        return warp_dental_maps(src, dst, wh, M=M, scale=scale, kw=kw, kh=kh,
                                tile_hw=tile_hw, dental=dental)
    pin_t = (0.0, 0.0) if pin is None else (
        round(float(pin[0]), 4), round(float(pin[1]), 4))
    p_t = tuple(p_key) if p_key is not None else (0.0, 0.0, 0.0)
    thw = (int(tile_hw[0]), int(tile_hw[1])) if tile_hw is not None else (0, 0)
    key = (
        str(loop_id), int(frame_i), version, str(viseme or ""), str(rel or ""),
        p_t, pin_t, (int(wh[0]), int(wh[1])), thw, round(float(scale), 4),
    )
    with _LOCK:
        hit = _MAPS.get(key)
        if hit is not None:
            _MAPS.move_to_end(key)
            _STATS["maps_hit"] += 1
            return hit
    maps = warp_dental_maps(src, dst, wh, M=M, scale=scale, kw=kw, kh=kh,
                            tile_hw=tile_hw, dental=dental)
    if maps is None:
        return None
    with _LOCK:
        _STATS["maps_miss"] += 1
        _lru_put(_MAPS, key, maps, dental_maps_limit())
    return maps


# ---------------------------------------------------------------- cheeks + pin (2026-09-14)

def _env_on(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def cheeks_cache_on() -> bool:
    """HOUSE_MOUTH_CHEEKS_CACHE=0 is the rollback (solves and maps both)."""
    return _env_on("HOUSE_MOUTH_CHEEKS_CACHE")


def pin_cache_on() -> bool:
    """HOUSE_MOUTH_PIN_CACHE=0 is the rollback for the pixel-pin shift LRU."""
    return _env_on("HOUSE_MOUTH_PIN_CACHE")


def diamond_cache_on() -> bool:
    """HOUSE_MOUTH_DIAMOND_CACHE=0 is the rollback."""
    return _env_on("HOUSE_MOUTH_DIAMOND_CACHE")


def cheeks_maps_limit() -> int:
    return _env_int("HOUSE_MOUTH_CHEEKS_MAPS", 256)


def diamond_maps_limit() -> int:
    return _env_int("HOUSE_MOUTH_DIAMOND_MAPS", 256)


def cache_sizing_on() -> bool:
    """HOUSE_POLY_CACHE_SIZING=0 restores the 09-14 caps (256 solves / 4096 pins) and makes
    /v1/reload drop every store again. MODULE-WIDE, not wall-only: every conductor that
    imports this (wall :8100, bench :8110, Lila's :8101) reads the same default; a unit's
    own Environment= cap still beats it (the bench sets its own)."""
    return _env_on("HOUSE_POLY_CACHE_SIZING")


def cheeks_solve_limit() -> int:
    # 1600 >= every frame the wall can talk on (listen 259 + work 120 + 4 desk flourishes
    # x 249 + laptop-closeup 163 = 1 538, ~0.53 GB measured 2026-09-19; 2.4 GB if every
    # entry were a 1.5 MB close-up). 256 was smaller than listen alone: a cyclic scan
    # evicted each solve one step before its frame came round.
    return _env_int("HOUSE_MOUTH_CHEEKS_SOLVES", 1600 if cache_sizing_on() else 256)


def pin_cache_limit() -> int:
    # ~1 KB an entry; the match is 4.8 of a desk miss's 7.9 ms, so a pin that outlives
    # its map's eviction is the cheapest hit there is (32 MB at the cap)
    return _env_int("HOUSE_MOUTH_PIN_SHIFTS", 32768 if cache_sizing_on() else 4096)


def _per(n: int, d: int) -> float | None:
    return round(n / d, 2) if d else None


def mouth_cache_stats() -> dict:
    """Counts, hits, BYTES (MB, running totals) and KEYS PER FRAME. A solve key carries
    no tile, so solve_keys_per_frame > 1 is the same dest frame arriving with another
    half-pixel EMA'd nose — hits are capped below 100 % at any size while it is > 1.
    maps_keys_per_pair is the same for (frame x tile). Solve hits alone are not a KPI:
    a solve is only consulted on a maps miss."""
    with _LOCK:
        s_frames = len({k[:2] for k in _SOLVE})
        m_pairs = len({(k[0], k[1], k[3]) for k in _CHEEKS})
        mb = lambda st: round(_BYTES[id(st)] / 1e6, 1)  # noqa: E731
        return {"on": cheeks_cache_on(), "pin_cache": pin_cache_on(),
                "maps_limit": cheeks_maps_limit(), "solves_limit": cheeks_solve_limit(),
                "pin_limit": pin_cache_limit(),
                "diamond_cache": diamond_cache_on(), "diamond_limit": diamond_maps_limit(),
                "solves": len(_SOLVE), "maps": len(_CHEEKS), "pins": len(_PIN),
                "diamonds": len(_DIAMOND),
                "solves_mb": mb(_SOLVE), "maps_mb": mb(_CHEEKS),
                "diamonds_mb": mb(_DIAMOND), "dental_maps_mb": mb(_MAPS),
                "maps_z": len(_CHEEKS_Z), "maps_z_mb": round(_Z_BYTES[0] / 1e6, 1),
                "maps_z_limit_mb": round(z_limit_bytes() / 1e6),
                "solve_frames": s_frames, "solve_keys_per_frame": _per(len(_SOLVE), s_frames),
                "maps_pairs": m_pairs, "maps_keys_per_pair": _per(len(_CHEEKS), m_pairs),
                "tri_cache": tri_cache_on(), "tri": len(_TRI), "tri_mb": mb(_TRI),
                "tri_limit_mb": round(tri_limit_bytes() / 1e6),
                **{k: v for k, v in _STATS.items()
                   if k.startswith(("solve_", "cheeks_", "pin_", "diamond_", "tri_"))}}


def nose_key(dst: dict | None) -> tuple[float, float]:
    """The EMA'd dest nose to half a pixel: a wrap reproduces the same EMA
    sequence (the EMA resets on a jump), so the same frame keys the same."""
    if not dst or dst.get("nose") is None:
        return (0.0, 0.0)
    n = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2]
    return (round(float(n[0]) * 2.0) / 2.0, round(float(n[1]) * 2.0) / 2.0)


def _lru_put(store: "OrderedDict", key, val, cap: int) -> None:
    """Caller holds _LOCK. Keeps the store's byte total (_BYTES) exact."""
    old = store.pop(key, None)
    if old is not None:
        _acct(store, old, -1)
    store[key] = val
    _acct(store, val, +1)
    while cap > 0 and len(store) > cap:
        _k, gone = store.popitem(last=False)
        _acct(store, gone, -1)


def tri_cache_on() -> bool:
    """HOUSE_MOUTH_TRI_CACHE=0 is the rollback (module-wide, like the others)."""
    return os.environ.get("HOUSE_MOUTH_TRI_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def tri_limit_bytes() -> int:
    return _env_int("HOUSE_MOUTH_TRI_CACHE_MB", 1024) * 1_000_000


def _crop_frame_arrays(d: dict, hw) -> dict:
    """Every array of frame size (hw) in d cut to the union of their non-zero pixels; an all-zero set is one empty box.
    _uncrop_frame_arrays puts them back byte for byte (outside the box every one of them was 0)."""
    big = [k for k, v in d.items() if isinstance(v, np.ndarray) and v.shape[:2] == tuple(hw)]
    if not big:
        return {"_box": None, **d}
    nz = np.zeros(tuple(hw), dtype=bool)
    for k in big:
        v = d[k]
        nz |= (v != 0).any(axis=-1) if v.ndim == 3 else (v != 0)
    ys, xs = np.nonzero(nz)
    box = (0, 0, 0, 0) if not len(ys) else (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
    out = {k: v for k, v in d.items() if k not in big}
    for k in big:
        out[k] = (d[k][box[0]:box[1], box[2]:box[3]].copy(), d[k].dtype, d[k].shape)
    out["_box"] = (box, big)
    return out


def _uncrop_frame_arrays(c: dict) -> dict:
    meta = c.get("_box")
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in c.items() if k != "_box"}     # a caller's dbg edit stays its own
    if meta is None:
        return out
    box, big = meta
    for k in big:
        part, dt, shape = c[k]
        full = np.zeros(shape, dtype=dt)
        full[box[0]:box[1], box[2]:box[3]] = part
        out[k] = full
    return out


def tri_cached(key: tuple, hw, build):
    """A turn landing or pasted picture for this (loop, frame, kind, ...) key: cached cropped, handed back whole and
    new (a caller may write into it). build() -> dict | None; None is never cached (a refused frame retries)."""
    if not tri_cache_on() or tri_limit_bytes() <= 0:
        return build()
    with _LOCK:
        hit = _TRI.get(key)
        if hit is not None:
            _TRI.move_to_end(key)
            _STATS["tri_hit"] += 1
    if hit is not None:
        return _uncrop_frame_arrays(hit)
    val = build()
    with _LOCK:
        _STATS["tri_miss"] += 1
        if val is not None:
            c = _crop_frame_arrays(val, hw)
            old_v = _TRI.pop(key, None)
            if old_v is not None:
                _acct(_TRI, old_v, -1)
            _TRI[key] = c
            _acct(_TRI, c, +1)
            while _TRI and _BYTES[id(_TRI)] > tri_limit_bytes():
                _k, gone = _TRI.popitem(last=False)
                _acct(_TRI, gone, -1)
    return val


def cheeks_solve_cached(loop_id, frame_i: int, version, dst: dict | None,
                        wh, scale: float = 1.0) -> dict | None:
    """Tier 1 for warp=cheeks: the dest frame's per-pixel Bézier solve
    (float32 t and h — 1.5e-5 px off the float64 one). None = no chord."""
    if dst is None:
        return None
    if not cheeks_cache_on() or cheeks_solve_limit() <= 0:
        return cheeks_dest_solve(dst, wh, scale=scale)
    key = (str(loop_id), int(frame_i), version, nose_key(dst),
           round(float(scale), 4), (int(wh[0]), int(wh[1])))
    with _LOCK:
        hit = _SOLVE.get(key)
        if hit is not None:
            _SOLVE.move_to_end(key)
            _STATS["solve_hit"] += 1
            return hit[0]
    sol = cheeks_dest_solve(dst, wh, scale=scale)
    if sol is not None:
        sol = dict(sol, t=np.asarray(sol["t"], dtype=np.float32),
                   h=np.asarray(sol["h"], dtype=np.float32))
    with _LOCK:
        _STATS["solve_miss"] += 1
        _lru_put(_SOLVE, key, (sol,), cheeks_solve_limit())
    return sol


def cheeks_maps_cached(*, loop_id, frame_i: int, version, rel, dst: dict | None,
                       wh, scale: float, pin_key, tile_hw, build, tag=None):
    """Tier 2 for warp=cheeks: the PINNED maps for (frame × tile), stored
    cropped to their ROI (~0.2 MB, not 2.4). `build()` → (maps, extra) does
    the whole thing on a miss (solve → tile → pixel pin); `extra` is any small
    picklable record (the pin's shift and ncc) handed back verbatim on a hit.
    Returns (maps, extra) or (None, None) when the tile refuses the frame.

    Lookup order (2026-10-13): memory → the compressed memory tier (_CHEEKS_Z) → disk → build. `tag` (the sheet's
    class) lets the ahead stage (lib/maps_ahead.py) learn which key this frame used for that class, so on the next
    pass it brings the entry into memory before the paint asks."""
    if not cheeks_cache_on() or cheeks_maps_limit() <= 0:
        return build()
    thw = (int(tile_hw[0]), int(tile_hw[1])) if tile_hw is not None else (0, 0)
    key = (str(loop_id), int(frame_i), version, str(rel or ""), nose_key(dst),
           round(float(scale), 4), tuple(pin_key or ()), (int(wh[0]), int(wh[1])), thw)
    with _LOCK:
        hit = _CHEEKS.get(key)
        if hit is not None:
            _CHEEKS.move_to_end(key)
            _STATS["cheeks_hit"] += 1
            if key in _AHEAD_PUT:
                _AHEAD_PUT.discard(key)
                _STATS["cheeks_ahead_used"] = _STATS.get("cheeks_ahead_used", 0) + 1
    disk, kb = _MAPS_DISK, None
    if disk is not None and (hit is None or (tag is not None and _AHEAD is not None)):
        try:
            kb = repr(key).encode() + b"|" + _MAPS_SALT(str(loop_id), str(rel or ""))
        except Exception:  # noqa: BLE001 - the disk tier never breaks a paint
            kb = None
    if tag is not None and _AHEAD is not None:
        try:
            _AHEAD.note(str(loop_id), int(frame_i), str(tag), key, kb)
        except Exception:  # noqa: BLE001 - the ahead stage never breaks a paint
            pass
    if hit is not None:
        roi, extra = hit
        return uncrop_maps(roi, wh), extra
    got = _z_take(key)
    if got is not None:
        roi, extra = got
        with _LOCK:
            _STATS["cheeks_z_hit"] = _STATS.get("cheeks_z_hit", 0) + 1
            _lru_put(_CHEEKS, key, (roi, extra), cheeks_maps_limit())
        return uncrop_maps(roi, wh), extra
    if disk is not None and kb is not None:
        # the disk tier (lib/maps_disk.py, 2026-10-12): what a past process built for this exact key and salt
        try:
            both = disk.get_both(kb)
        except Exception:  # noqa: BLE001 - the disk tier never breaks a paint
            both = None
        if both is not None:
            (roi, extra), packed = both
            with _LOCK:
                _STATS["cheeks_disk_hit"] = _STATS.get("cheeks_disk_hit", 0) + 1
                _lru_put(_CHEEKS, key, (roi, extra), cheeks_maps_limit())
            if packed is not None:
                z_put(key, packed)
            return uncrop_maps(roi, wh), extra
    maps, extra = build()
    roi = crop_maps(maps) if maps is not None else None
    with _LOCK:
        _STATS["cheeks_miss"] += 1
        if roi is not None:
            _lru_put(_CHEEKS, key, (roi, extra), cheeks_maps_limit())
    if disk is not None and kb is not None and roi is not None:
        disk.put(kb, (roi, extra), key)          # the writer packs it and hands the bytes to z_put
    return maps, extra


# THE COMPRESSED MEMORY TIER (the User 2026-10-13, "compressing ... so all of it"): every map the disk tier writes
# or reads also lives here as lib/maps_pack bytes (2.3x smaller, lossless), capped by BYTES. A miss in _CHEEKS
# unpacks from here (~0.2 ms) instead of reading a file (1-30 ms under load). Filled off the paint thread (the disk
# writer packs, the ahead stage unpacks). HOUSE_MOUTH_MAPS_Z_MB (default 1536; 0 = off). Needs the disk tier.
_CHEEKS_Z: "OrderedDict" = OrderedDict()
_Z_BYTES = [0]


def z_limit_bytes() -> int:
    return _env_int("HOUSE_MOUTH_MAPS_Z_MB", 1536) * 1000 * 1000


def z_put(key, packed: bytes) -> None:
    cap = z_limit_bytes()
    if cap <= 0 or not packed:
        return
    with _LOCK:
        old = _CHEEKS_Z.pop(key, None)
        if old is not None:
            _Z_BYTES[0] -= len(old)
        _CHEEKS_Z[key] = packed
        _Z_BYTES[0] += len(packed)
        while _Z_BYTES[0] > cap and _CHEEKS_Z:
            _k, gone = _CHEEKS_Z.popitem(last=False)
            _Z_BYTES[0] -= len(gone)


def _z_take(key):
    """The unpacked (roi, extra) from the compressed tier, or None."""
    with _LOCK:
        packed = _CHEEKS_Z.get(key)
        if packed is not None:
            _CHEEKS_Z.move_to_end(key)
    if packed is None:
        return None
    try:
        import maps_pack
        return maps_pack.unpack(packed)
    except Exception:  # noqa: BLE001 - a bad entry is a miss
        with _LOCK:
            gone = _CHEEKS_Z.pop(key, None)
            if gone is not None:
                _Z_BYTES[0] -= len(gone)
        return None


# THE AHEAD STAGE'S HOOK (lib/maps_ahead.py, 2026-10-13): `_AHEAD.note(loop, frame, tag, key, kb)` learns, and
# fetch_ahead() brings one entry into memory from the compressed tier or the disk before the paint asks.
_AHEAD = None
_AHEAD_PUT: set = set()


def set_ahead(stage) -> None:
    global _AHEAD
    _AHEAD = stage


def fetch_ahead(key, kb) -> str:
    """Called on the ahead stage's thread: 'hot' (already in memory), 'z' / 'disk' (brought in), or 'none'."""
    with _LOCK:
        if key in _CHEEKS:
            return "hot"
    got = _z_take(key)
    how = "z"
    if got is None and kb is not None and _MAPS_DISK is not None:
        try:
            both = _MAPS_DISK.get_both(kb)
        except Exception:  # noqa: BLE001
            both = None
        if both is not None:
            got, packed = both
            how = "disk"
            if packed is not None:
                z_put(key, packed)
    if got is None:
        return "none"
    with _LOCK:
        if key not in _CHEEKS:
            _lru_put(_CHEEKS, key, got, cheeks_maps_limit())
            _AHEAD_PUT.add(key)
            if len(_AHEAD_PUT) > 4 * max(1, cheeks_maps_limit()):
                _AHEAD_PUT.intersection_update(_CHEEKS.keys())
            _STATS["cheeks_ahead_" + how] = _STATS.get("cheeks_ahead_" + how, 0) + 1
    return how


_MAPS_DISK = None               # lib/maps_disk.MapsDisk, set by the conductor (set_maps_disk); None = memory only
_MAPS_SALT = None               # (loop_id, rel) -> bytes: the live dials and the film's/tile's own files


def set_maps_disk(tier, salt) -> None:
    """Give the cheeks maps a disk tier (None turns it off). `salt(loop_id, rel)` returns bytes that name everything
    a map depends on beyond the memory key: the memory tier is a process's own and is dropped on a dial change, the
    disk outlives both."""
    global _MAPS_DISK, _MAPS_SALT
    _MAPS_DISK, _MAPS_SALT = (tier, salt) if tier is not None else (None, None)
    if tier is not None:
        tier.on_packed = z_put


def m_key_of(M) -> tuple:
    """A landing matrix as a cache key: 2x3 to 0.01 px. Shared with
    pin_shift_cached's rule so the two agree on what "the same landing" is.
    Empty tuple for None, which keys distinctly from any real matrix."""
    if M is None:
        return ()
    try:
        return tuple(float(v) for v in np.round(np.asarray(M, dtype=np.float64).ravel(), 2))
    except (TypeError, ValueError):
        return ()


def diamond_maps_cached(*, loop_id, frame_i: int, version, rel, dst: dict | None,
                        wh, scale: float, tile_hw, viseme, m_key, build):
    """warp=diamond's maps for (frame x tile x viseme x landing), ROI-cropped.

    MEASURED 2026-09-15: warp_diamond_maps builds full-frame maps in 61.3 ms
    against 0.95 ms for plane and 0.86 + 0.53 ms for the two cheeks tiers —
    four times the whole 16 ms budget at 60 fps, and it was the one warp with
    no cache at all, so the mode could be selected but could not hold frame
    rate. A loop tuned in the Composer plays the same frames over and over,
    so this warms in one pass and the mode becomes usable.

    The landing M is in the key (rounded, like pin_shift_cached) because it
    carries fit, pin and the nudge; the viseme is in it because diamond grows
    AH/EE. `build()` -> maps, where maps may be (mx, my) or (mx, my, alpha) —
    crop_maps/uncrop_maps carry the alpha through verbatim.
    """
    if not diamond_cache_on() or diamond_maps_limit() <= 0:
        return build()
    thw = (int(tile_hw[0]), int(tile_hw[1])) if tile_hw is not None else (0, 0)
    key = (str(loop_id), int(frame_i), version, str(rel or ""), nose_key(dst),
           round(float(scale), 4), str(viseme or ""), tuple(m_key or ()),
           (int(wh[0]), int(wh[1])), thw)
    with _LOCK:
        hit = _DIAMOND.get(key)
        if hit is not None:
            _DIAMOND.move_to_end(key)
            _STATS["diamond_hit"] += 1
    if hit is not None:
        return uncrop_maps(hit[0], wh)
    maps = build()
    roi = crop_maps(maps) if maps is not None else None
    with _LOCK:
        _STATS["diamond_miss"] += 1
        _lru_put(_DIAMOND, key, (roi,), diamond_maps_limit())
    return maps


def pin_shift_cached(loop_id, frame_i: int, version, rel, M, nudge, compute):
    """The pixel pin's answer for (frame × tile × landing): (dx, dy, ncc).
    The landing M (2×3, to 0.01 px) is in the key, so a different take-width
    fit or EMA'd nose is a different question. `compute()` → (dx, dy, ncc)."""
    if not pin_cache_on() or pin_cache_limit() <= 0:
        return compute()
    try:
        m_t = m_key_of(M)
    except Exception:  # noqa: BLE001
        return compute()
    key = (str(loop_id), int(frame_i), version, str(rel or ""), m_t, tuple(nudge or ()))
    with _LOCK:
        hit = _PIN.get(key)
        if hit is not None:
            _PIN.move_to_end(key)
            _STATS["pin_hit"] += 1
            return hit
    got = compute()
    with _LOCK:
        _STATS["pin_miss"] += 1
        _lru_put(_PIN, key, tuple(got), pin_cache_limit())
    return got
