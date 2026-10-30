"""Finished mouth patches, kept: a repeated film frame x tile is copied, not painted (the User 2026-10-13, "lets get
there then"; and 2026-10-24, "every painted patch for each loop frame and tile could be built ahead": the disk tier,
lib/mouth_patch_disk, filled by the conductor's own build at install).

Every step of a paint is a pure function of the film frame, the tile, the map, the pin, the dials and the library's
files; the conductor builds the key from all of them (house-conductor.py Playhead._patch_key) and keeps only a paint
that read no state from the frame before (no dissolve, no turn, no seek mid-paint).

An entry is the region where the finished frame differs from the film frame (its bbox and those pixels), a blake2b of
the film frame's pixels there, and the paint's side record (what it leaves on the playhead for /v1/state). A hit is
used only when the film frame's pixels under the bbox hash the same, so a frame that is not the stored one is painted
as today. Copying the region back gives the frame byte for byte what the paint would have made (the tests, and
HOUSE_MOUTH_PATCH_VERIFY=1, which paints every hit as well and counts mismatches).

Measured 2026-10-13 on a close-up with a dissolve on: no gain (a quarter of passes eligible). 2026-10-24 on the v2 desk
bench with xfade 0: the same line six times went from 132 of 285 semiframes painted (composite p50 43.6 ms) to 303
with 5 skipped (p50 1.3 ms) once the pairs were kept; 885 verified hits, 0 mismatches.

Bytes-capped LRU (HOUSE_MOUTH_PATCH_MB, else config/mouth-patch.json `memory_mb`). On: HOUSE_MOUTH_PATCH_CACHE=1|0;
unset = on in a pack, off on the house (crissa_home.is_house).
"""
from __future__ import annotations

import collections
import hashlib
import os
import threading

import numpy as np

_lock = threading.Lock()
_store: collections.OrderedDict = collections.OrderedDict()
_bytes = [0]
stats = {"hit": 0, "miss": 0, "put": 0, "refused": 0, "verify_ok": 0, "verify_bad": 0, "evicted": 0, "disk_hit": 0,
         "unsafe": 0, "blend": 0}
disk = None            # the disk tier (lib/mouth_patch_disk.DiskTier), set by the conductor when it is on
disk_writes = False    # True only in the install's build: the glass reads the disk, its paint never writes it


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_MOUTH_PATCH_CACHE", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return limit_bytes(env) > 0
    try:
        import crissa_home
        return (not crissa_home.is_house()) and limit_bytes(env) > 0
    except Exception:  # noqa: BLE001 - no state home to ask: today's paint
        return False


def verify_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("HOUSE_MOUTH_PATCH_VERIFY", "")).strip().lower() in ("1", "true", "yes", "on")


_CFG: list = []


def memory_mb_dial() -> float:
    """config/mouth-patch.json `memory_mb` (with the measurement that set it); 0 when the file is missing."""
    if not _CFG:
        from pathlib import Path
        import json
        try:
            _CFG.append(json.loads((Path(__file__).resolve().parents[1] / "config" / "mouth-patch.json").read_text(
                encoding="utf-8")))
        except (OSError, ValueError):
            _CFG.append({})
    try:
        return float(_CFG[0].get("memory_mb", 0.0))
    except (TypeError, ValueError):
        return 0.0


def limit_bytes(env=None) -> int:
    """The memory tier's cap: HOUSE_MOUTH_PATCH_MB, else the config's dial."""
    env = os.environ if env is None else env
    try:
        mb = float(env["HOUSE_MOUTH_PATCH_MB"]) if env.get("HOUSE_MOUTH_PATCH_MB") not in (None, "") else memory_mb_dial()
    except ValueError:
        mb = memory_mb_dial()
    return int(max(0.0, mb) * (1 << 20))


def digest(*parts) -> bytes:
    """A key from values: arrays by dtype, shape and bytes; dicts by sorted items; everything else by repr."""
    h = hashlib.blake2b(digest_size=20)

    def feed(v):
        if isinstance(v, np.ndarray):
            a = np.ascontiguousarray(v)
            h.update(b"A" + repr((a.dtype.str, a.shape)).encode())
            h.update(memoryview(a).cast("B"))
        elif isinstance(v, dict):
            h.update(b"D%d" % len(v))
            for k in sorted(v, key=repr):
                h.update(repr(k).encode())
                feed(v[k])
        elif isinstance(v, (list, tuple)):
            h.update(b"L%d" % len(v))
            for x in v:
                feed(x)
        elif isinstance(v, (np.floating, np.integer)):
            h.update(repr(v.item()).encode())
        else:
            h.update(b"R" + repr(v).encode())
    for p in parts:
        feed(p)
    return h.digest()


_CODE = []


def code_print() -> str:
    """The code that paints, as one print: the conductor and every module in this tree's lib/ (all of them, so the
    Wall's process and the install's build get the same print whatever each imported), plus numpy's and OpenCV's
    versions. A kept patch from other code is never served (the disk tier names its folder by it)."""
    if not _CODE:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        h = hashlib.blake2b(digest_size=10)
        for f in [root / "scripts" / "house-conductor.py"] + sorted((root / "lib").rglob("*.py")):
            try:
                h.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes())
            except OSError:
                pass
        try:
            import cv2
            h.update(cv2.__version__.encode())
        except Exception:  # noqa: BLE001
            pass
        h.update(np.__version__.encode())
        _CODE.append(h.hexdigest())
    return _CODE[0]


def lerp_frames(base: np.ndarray, f0: np.ndarray, f1: np.ndarray, t: float) -> np.ndarray:
    """The dissolve from two finished frames (2026-10-25): where either differs from the film frame, the pixel is
    (1-t) f0 + t f1, rounded; everywhere else the film frame, untouched. This IS the live dissolve's picture: the
    premultiplied mix (pose_mouth.xfade_mix) stamped alpha-over gives base(1-a0(1-t)-a1 t) + w0 a0 (1-t) + w1 a1 t
    = (1-t)(base(1-a0) + w0 a0) + t(base(1-a1) + w1 a1). Only the union box of the two patches is touched."""
    t = float(min(1.0, max(0.0, t)))
    if t <= 0.0:
        return f0
    if t >= 1.0:
        return f1
    d = (f0 != base).any(axis=2) | (f1 != base).any(axis=2) if base.ndim == 3 else ((f0 != base) | (f1 != base))
    rows = np.flatnonzero(d.any(axis=1))
    if rows.size == 0:
        return base
    cols = np.flatnonzero(d.any(axis=0))
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    out = base.copy()
    mixed = (f0[y0:y1, x0:x1].astype(np.float32) * (1.0 - t) + f1[y0:y1, x0:x1].astype(np.float32) * t)
    out[y0:y1, x0:x1] = np.clip(np.rint(mixed), 0, 255).astype(base.dtype)
    stats["blend"] += 1
    return out


def _base_digest(base: np.ndarray, bbox) -> bytes:
    y0, y1, x0, x1 = bbox
    return hashlib.blake2b(np.ascontiguousarray(base[y0:y1, x0:x1]).tobytes(), digest_size=16).digest()


def take(key: bytes, base: np.ndarray):
    """(the finished frame for `key` on this film frame, the paint's side record), or None (a miss: paint as today)."""
    with _lock:
        ent = _store.get(key)
        if ent is not None:
            _store.move_to_end(key)
    if ent is None and disk is not None:
        ent = disk.get(key)
        if ent is not None:
            stats["disk_hit"] += 1
            _keep(key, ent)
    if ent is None:
        stats["miss"] += 1
        return None
    bbox, bdig, roi, side = ent
    if bbox is not None and _base_digest(base, bbox) != bdig:
        stats["refused"] += 1
        return None
    out = base.copy()
    if bbox is not None:
        y0, y1, x0, x1 = bbox
        out[y0:y1, x0:x1] = roi
    stats["hit"] += 1
    return out, side


def entry_of(base: np.ndarray, out: np.ndarray, side=None):
    """What a paint made on this film frame, as an entry: (bbox, film digest there, the pixels, the side record)."""
    if out is None or base is None or out.shape != base.shape or out.dtype != base.dtype:
        return None
    diff = out != base
    if diff.ndim == 3:
        diff = diff.any(axis=2)
    ys = np.flatnonzero(diff.any(axis=1))
    if ys.size == 0:
        return (None, b"", None, side)
    xs = np.flatnonzero(diff.any(axis=0))
    bbox = (int(ys[0]), int(ys[-1]) + 1, int(xs[0]), int(xs[-1]) + 1)
    roi = np.ascontiguousarray(out[bbox[0]:bbox[1], bbox[2]:bbox[3]]).copy()
    return (bbox, _base_digest(base, bbox), roi, side)


def put(key: bytes, base: np.ndarray, out: np.ndarray, side=None) -> None:
    """Keep what this paint made on this film frame (memory, and the disk tier when it is on)."""
    ent = entry_of(base, out, side)
    if ent is None:
        return
    _keep(key, ent)
    if disk is not None and disk_writes:
        disk.put(key, ent)


def _keep(key: bytes, ent) -> None:
    cap = limit_bytes()
    size = _size(ent)
    if cap <= 0 or size > cap:
        return
    with _lock:
        old = _store.pop(key, None)
        if old is not None:
            _bytes[0] -= _size(old)
        _store[key] = ent
        _bytes[0] += size
        stats["put"] += 1
        while _bytes[0] > cap and _store:
            _k, gone = _store.popitem(last=False)
            _bytes[0] -= _size(gone)
            stats["evicted"] += 1


def _size(ent) -> int:
    roi = ent[2]
    return (roi.nbytes + 128) if roi is not None else 64


def clear() -> None:
    with _lock:
        _store.clear()
        _bytes[0] = 0


def state() -> dict:
    with _lock:
        n, b = len(_store), _bytes[0]
    return dict(stats, entries=n, mb=round(b / 1e6, 1), limit_mb=round(limit_bytes() / 1e6, 1),
                on=enabled(), verify=verify_on(), disk=(disk.state() if disk is not None else None))
