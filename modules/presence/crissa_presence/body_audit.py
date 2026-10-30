"""Delta-body audit — count bodies as foreground vs empty-room refs.

the User 2026-08-07: "the back of my head should count as a body as it is delta
from refs" + "count the delta bodies in the room from clock face 8, 10, 11,
12, 1, 2, down 15 degrees — gets that chair in front of the camera."

A body here is a large foreground blob against a reference frame captured at
the SAME preset (pan + video_sweep tilt) while the room was verifiably empty. No face
needed: a back of a head, a shoulder in zone 11 at the door-FOV edge, or a
person sunk into the stop-1 chair all move pixels the empty ref does not have.
The chair itself is IN the ref, so chair-alone = no delta; person-in-chair =
delta. Faces play no part, so screen faces (zone 9) and portraits cannot vote.

Door-FOV edge doctrine (the User; see Argus SOUL "Door FOV edges"): the door FOV
boundary clips stations 11/12 and the edge of 1 — a blob TOUCHING the frame
edge on the door side at those stations is counted as a partial body
("shoulder" inference), never discarded for being cut off.

Usage:
    POST :8191/v1/body_audit                  -> run audit vs stored refs
    POST :8191/v1/body_audit {"capture_refs": true}
        -> (re)capture refs; requires presence headcount==0 or {"force": true}

Findings are evidence (log + response), not SoT writes: v1 emits an
`occupancy_surplus` / `occupancy_deficit` note the conflict path and the
inspector can consume ("Provisional scoring": observe before it gates).
"""
from __future__ import annotations

from . import paths as _paths
from .room_heading import door_home_pose

import json
import os
import time
from pathlib import Path
from typing import Any

_REPO = _paths.ROOT
REFS_DIR = Path(
    os.environ.get("RAIL_BODY_AUDIT_REFS", str(_REPO / "data" / "body_audit_refs"))
)
AUDIT_STATIONS = [
    int(s)
    for s in os.environ.get("RAIL_BODY_AUDIT_STATIONS", "6,8,10,11,12").split(",")
    if s.strip()
]
# Degrees (firmware units = deg * 3600). the User: seated rightmost may need -20.
AUDIT_TILT = int(os.environ.get("RAIL_BODY_AUDIT_TILT", "-20"))
# stations whose view is clipped by the door FOV — edge blobs count as partial
DOOR_EDGE_STATIONS = {11, 12, 1}
MIN_BLOB_FRAC = float(os.environ.get("RAIL_BODY_AUDIT_MIN_BLOB", "0.02"))
DIFF_THR = int(os.environ.get("RAIL_BODY_AUDIT_DIFF_THR", "26"))
SETTLE = float(os.environ.get("RAIL_BODY_AUDIT_SETTLE", "0.9"))
# Re-shoot refs when older than this and the room is verified empty (light drift)
REF_MAX_AGE_SEC = float(os.environ.get("RAIL_BODY_AUDIT_REF_MAX_AGE", "2700"))


def _tilt_units(tilt_deg: int | float) -> int:
    """Clock-station tilt in absolute units (3600 per degree)."""
    return int(round(float(tilt_deg) * 3600.0))


def _presets(
    svc,
    *,
    stations: list[int] | None = None,
    tilt_deg: int | float | None = None,
    include_chair: bool = True,
) -> list[dict[str, Any]]:
    """Count-check presets: clock seats + chair nearly under camera (sitter).

    Body-count only — no FaceFinder. Face/auth is a separate USER path.

    Chair (camera-stops ``chair`` pan ~300k) is easy to miss at door tilt;
    it gets a steeper down-tilt (sit) so a person in that seat still deltas.
    """
    from .look_around import load_geometry
    from .room_sweep import _stops, _load_camera_stops

    want = list(stations) if stations else list(AUDIT_STATIONS)
    t_deg = AUDIT_TILT if tilt_deg is None else int(tilt_deg)
    t_abs = _tilt_units(t_deg)
    # Sitter in the chair under/near camera needs more pitch down
    t_chair_deg = int(os.environ.get("RAIL_BODY_AUDIT_CHAIR_TILT", str(min(t_deg, -25))))
    t_chair_abs = _tilt_units(t_chair_deg)

    geom = load_geometry()
    by_station: dict[int | str, dict[str, Any]] = {}
    for stop_name, pan, stop_tilt, station in _stops(geom):
        if station is None:
            continue
        st = int(station)
        if st in want and st not in by_station:
            # Prefer per-stop calibrated tilt (e.g. seats_1_2 −15°) when set
            use_tilt = int(stop_tilt) if stop_tilt else t_abs
            use_deg = (
                int(round(use_tilt / 3600.0))
                if stop_tilt
                else t_deg
            )
            by_station[st] = {
                "station": st,
                "label": f"station_{st}",
                "pan": int(pan),
                "tilt": use_tilt,
                "tilt_deg": use_deg,
                "door_edge": st in DOOR_EDGE_STATIONS,
                "stop": stop_name,
            }

    # Always add calibrated chair pan (near-under camera) — the User: sitter lives here
    if include_chair:
        cfg = _load_camera_stops()
        by_id: dict[str, dict] = {}
        for s in cfg.get("stops") or []:
            if isinstance(s, dict) and s.get("id"):
                by_id[str(s["id"])] = s
        chair = by_id.get("chair") or {}
        chair_pan = int(chair["pan"]) if chair.get("pan") is not None else None   # a room with no chair stop has none
        # Don't duplicate if some station already uses this pan
        if chair_pan is not None and not any(abs(int(p["pan"]) - chair_pan) < 5000 for p in by_station.values()):
            by_station["chair"] = {
                "station": "chair",
                "label": "chair_below_cam",
                "pan": chair_pan,
                "tilt": t_chair_abs,
                "tilt_deg": t_chair_deg,
                "door_edge": False,
                "note": "sitter chair nearly under camera — steeper tilt",
            }

    # Order: desk/user first, then mid, door, chair last before return
    order: list[int | str] = [s for s in want if s in by_station]
    if "chair" in by_station and "chair" not in order:
        order.append("chair")
    return [by_station[s] for s in order if s in by_station]


def _diff_bodies(ref_bgr, cur_bgr, door_edge: bool) -> dict[str, Any]:
    """Luma-normalized absdiff -> blobs. Returns count + edge-partial notes."""
    import cv2
    import numpy as np

    h, w = cur_bgr.shape[:2]
    if ref_bgr.shape[:2] != (h, w):
        ref_bgr = cv2.resize(ref_bgr, (w, h))
    g_ref = cv2.GaussianBlur(cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    g_cur = cv2.GaussianBlur(cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    # PTZ repeatability: the same preset lands a few px off, turning strong
    # edges (door frame, wall corner) into sliver "bodies". Phase-correlate
    # and shift the current frame onto the ref before diffing.
    try:
        (dx, dy), _resp = cv2.phaseCorrelate(
            np.float32(g_ref), np.float32(g_cur)
        )
        if abs(dx) < 60 and abs(dy) < 60 and (abs(dx) > 0.5 or abs(dy) > 0.5):
            M = np.float32([[1, 0, -dx], [0, 1, -dy]])
            g_cur = cv2.warpAffine(
                g_cur, M, (g_cur.shape[1], g_cur.shape[0]),
                borderMode=cv2.BORDER_REPLICATE,
            )
    except Exception:
        pass
    # exposure normalization: scale current mean to ref mean
    m_ref, m_cur = float(g_ref.mean()), float(g_cur.mean())
    if m_cur > 1.0:
        g_cur = np.clip(g_cur.astype(np.float32) * (m_ref / m_cur), 0, 255).astype(
            "uint8"
        )
    d = cv2.absdiff(g_ref, g_cur)
    _, mask = cv2.threshold(d, DIFF_THR, 255, cv2.THRESH_BINARY)
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=2)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8), iterations=1)
    n, labels, stats, _c = cv2.connectedComponentsWithStats(mask)
    min_area = MIN_BLOB_FRAC * h * w
    bodies = 0
    partials = 0
    blobs: list[dict[str, Any]] = []
    for i in range(1, n):
        x, y, bw, bh, area = (int(stats[i][k]) for k in range(5))
        if area < min_area:
            continue
        touches_edge = x <= 2 or (x + bw) >= (w - 2)
        # Registration slivers: a thin strip hugging a frame edge is residual
        # PTZ offset on a high-contrast edge, not a body. A person at the FOV
        # edge still shows a shoulder several times wider than this.
        if touches_edge and bw < 0.06 * w:
            continue
        if bh < 0.08 * h and bw < 0.10 * w:
            continue  # small debris (shadow flicker, screen glow)
        # Illumination plausibility (2026-08-08): six-hour-old refs turned
        # afternoon sun into "bodies" — a full-frame wash, a floor band, a
        # window column. No single person is a third of the frame, nor a
        # short full-width stripe. Refs auto-refresh (see run_audit), these
        # are the safety net when they cannot.
        if area >= 0.28 * h * w:
            continue  # whole-scene light change
        if bw >= 0.70 * w and bh >= 0.80 * h:
            continue  # frame-wide wash
        if bh <= 0.30 * h and bw >= 0.45 * w:
            continue  # horizontal light band (floor/wall)
        if bh < 0.18 * h:
            continue  # too short to be a body at these presets
        kind = "body"
        if door_edge and touches_edge and area < 3 * min_area:
            # door-FOV edge clip: shoulder/partial — still counts (the User)
            kind = "partial_edge"
            partials += 1
        bodies += 1
        blobs.append(
            {
                "bbox": [x, y, x + bw, y + bh],
                "area_frac": round(area / (h * w), 4),
                "kind": kind,
            }
        )
    return {
        "bodies": bodies,
        "partials": partials,
        "blobs": blobs,
        "diff_mean": round(float(d.mean()), 2),
    }


def _ref_path(station: int | str, tilt_deg: int | float | None = None) -> Path:
    """Prefer ref for requested tilt; fall back to legacy tilt-15 files on disk."""
    t = int(AUDIT_TILT if tilt_deg is None else tilt_deg)
    key = str(station)
    primary = REFS_DIR / f"st{key}_tilt{t}.jpg"
    if primary.is_file():
        return primary
    # Existing captures are st{N}_tilt-15.jpg — reuse until empty-room re-cap
    legacy = REFS_DIR / f"st{key}_tilt-15.jpg"
    if legacy.is_file():
        return legacy
    # Chair may share desk empty-ref until dedicated capture
    if key == "chair":
        for alt in (
            REFS_DIR / f"stchair_tilt{t}.jpg",
            REFS_DIR / "stchair_tilt-15.jpg",
            REFS_DIR / "st1_tilt-15.jpg",
            REFS_DIR / f"st2_tilt{t}.jpg",
            REFS_DIR / "st2_tilt-15.jpg",
        ):
            if alt.is_file():
                return alt
    return primary


# Sweep pans → empty refs (tilt −15). Desk/hour 6 is firmware pan 0.
# 2026-10-25: no room's pans in code - the sweep refs a room has are listed in its own camera-stops.json
# ("sweep_refs": [[pan, hour], ...], written where the refs were captured); none listed = no ref match.
def _sweep_refs() -> tuple:
    try:
        from .grabber import _load_stops
        rows = _load_stops().get("sweep_refs") or []
        return tuple((int(p), int(h)) for p, h in rows)
    except Exception:
        return ()


_SWEEP_REF_BY_PAN = _sweep_refs()
# Video-sweep frame library (cron product). Station stills are aliases harvested
# from this set — the subtract looks up the closest pan in the video.
SWEEP_DIRNAME = "sweep"
_SWEEP_FRAME_RE = None


def sweep_dir(root: Path | None = None) -> Path:
    return (root or REFS_DIR) / SWEEP_DIRNAME


def _frame_re():
    global _SWEEP_FRAME_RE
    if _SWEEP_FRAME_RE is None:
        import re

        _SWEEP_FRAME_RE = re.compile(r"_pan(-?\d+)\.jpg$", re.I)
    return _SWEEP_FRAME_RE


def load_sweep_index(
    d: Path | str | None = None,
) -> list[tuple[int, int | None, Path]]:
    """[(pan, tilt|None, path), ...] from an empty-room video sweep."""
    root = Path(d) if d else sweep_dir()
    if not root.is_dir():
        return []
    man = root / "manifest.json"
    if man.is_file():
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
            out: list[tuple[int, int | None, Path]] = []
            for fr in data.get("frames") or []:
                if not isinstance(fr, dict):
                    continue
                pan = fr.get("pan")
                name = fr.get("file")
                if pan is None or not name:
                    continue
                p = root / str(name)
                if not p.is_file():
                    continue
                tilt = fr.get("tilt")
                try:
                    tilt_i = int(tilt) if tilt is not None else None
                except (TypeError, ValueError):
                    tilt_i = None
                out.append((int(pan), tilt_i, p))
            if out:
                return out
        except Exception:
            pass
    import re

    rx = re.compile(r"_pan(-?\d+)(?:_t(-?\d+))?\.jpg$", re.I)
    out = []
    for p in sorted(root.glob("v*_pan*.jpg")):
        m = rx.search(p.name)
        if m:
            tilt_i = int(m.group(2)) if m.group(2) is not None else None
            out.append((int(m.group(1)), tilt_i, p))
    return out


def station_for_pan(pan: int) -> int | None:
    best_st: int | None = None
    best_d = 10**9
    for p0, st in _SWEEP_REF_BY_PAN:
        d = abs(int(pan) - int(p0))
        if d < best_d:
            best_d = d
            best_st = int(st)
    return best_st if best_d <= 140000 else None


def nearest_in_index(
    index: list[tuple[int, int | None, Path]] | list[tuple[int, Path]],
    pan: int,
    tilt: int | None = None,
) -> tuple[Path | None, int]:
    """Closest frame. If tilt is given, same-tilt beats a nearer different pitch."""
    best: tuple[Path | None, int] = (None, 10**9)
    for row in index:
        if len(row) == 2:
            p0, path = row  # type: ignore[misc]
            t0 = None
        else:
            p0, t0, path = row  # type: ignore[misc]
        d = abs(int(pan) - int(p0))
        if tilt is not None and t0 is not None and abs(int(t0) - int(tilt)) > 3600:
            d += 400000  # prefer same pitch (sitter holds vs main arc)
        if d < best[1]:
            best = (path, d)
    return best


def _video_tilt_deg() -> int:
    from .sweep_pose import video_sweep_pose

    return int(video_sweep_pose()["tilt_deg"])


def harvest_station_stills(
    sweep_root: Path | str,
    dest_dir: Path | str,
    *,
    tilt_deg: int | None = None,
) -> list[dict[str, Any]]:
    """Copy the video frame nearest each clock station into stN_tilt{deg}.jpg.

    These are aliases for the count-check UI — diffs use the full sweep index.
    """
    if tilt_deg is None:
        tilt_deg = _video_tilt_deg()
    import shutil

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    index = load_sweep_index(sweep_root)
    out: list[dict[str, Any]] = []
    if not index:
        return out
    main_tilt = _tilt_units(int(tilt_deg))
    for p0, st in _SWEEP_REF_BY_PAN:
        src, d = nearest_in_index(index, int(p0), tilt=main_tilt)
        if src is None:
            continue
        name = f"st{st}_tilt{int(tilt_deg)}.jpg"
        shutil.copy2(src, dest / name)
        out.append(
            {
                "station": st,
                "pan": int(p0),
                "d_pan": int(d),
                "src": src.name,
                "dest": name,
            }
        )
    from .sweep_pose import video_sweep_pose

    for sit in video_sweep_pose().get("sitters") or []:
        src, d = nearest_in_index(index, int(sit["pan"]), tilt=int(sit["tilt"]))
        if src is None:
            continue
        st = sit.get("station") if sit.get("station") is not None else sit.get("id")
        name = f"st{st}_tilt{int(sit['tilt_deg'])}.jpg"
        shutil.copy2(src, dest / name)
        out.append(
            {
                "station": st,
                "pan": int(sit["pan"]),
                "d_pan": int(d),
                "src": src.name,
                "dest": name,
            }
        )
    return out


def nearest_ref_for_pan(
    pan: int, *, tilt_deg: int | None = None
) -> tuple[Path | None, int | None, int]:
    """Closest empty-room ref for a sweep frame pan. Returns (path, station, d_pan).

    Prefers the empty VIDEO sweep (closest pan). Falls back to station stills.
    """
    if tilt_deg is None:
        tilt_deg = _video_tilt_deg()
    index = load_sweep_index()
    if index:
        want_tilt = _tilt_units(int(tilt_deg)) if tilt_deg is not None else None
        path, d = nearest_in_index(index, int(pan), tilt=want_tilt)
        if path is not None and d <= 140000:
            return path, station_for_pan(int(pan)), d
    best: tuple[Path | None, int | None, int] = (None, None, 10**9)
    for p0, st in _SWEEP_REF_BY_PAN:
        d = abs(int(pan) - int(p0))
        if d >= best[2]:
            continue
        rp = _ref_path(st, tilt_deg=tilt_deg)
        if rp.is_file():
            best = (rp, st, d)
    if best[0] is None or best[2] > 140000:
        return None, None, best[2]
    return best


def write_ref_diff(
    live_path: str | Path,
    out_path: str | Path,
    *,
    pan: int | None = None,
    tilt_deg: int | None = None,
) -> dict[str, Any]:
    """Subtract empty ref from a live/sweep JPEG. Writes a heat+mask JPEG.

    Returns blob fraction + which ref was used. No SoT write.
    """
    import cv2
    import numpy as np

    if tilt_deg is None:
        tilt_deg = _video_tilt_deg()
    live_path = Path(live_path)
    img = cv2.imread(str(live_path))
    if img is None:
        return {"ok": False, "error": "no_live"}
    if pan is None:
        stem = live_path.stem
        pan = int(stem.split("_pan")[-1]) if "_pan" in stem else int(door_home_pose()["pan"])
    ref_p, station, d_pan = nearest_ref_for_pan(int(pan), tilt_deg=tilt_deg)
    if ref_p is None:
        return {"ok": False, "error": "no_ref", "pan": pan}
    ref = cv2.imread(str(ref_p))
    if ref is None:
        return {"ok": False, "error": "ref_unreadable", "ref": str(ref_p)}
    h, w = img.shape[:2]
    r = cv2.resize(ref, (w, h)) if ref.shape[:2] != (h, w) else ref
    a = cv2.GaussianBlur(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    b = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    ma, mb = float(a.mean()), float(b.mean())
    if mb > 1.0:
        b = np.clip(b.astype(np.float32) * (ma / mb), 0, 255).astype("uint8")
    d = cv2.absdiff(a, b)
    _t, mask = cv2.threshold(d, DIFF_THR, 255, cv2.THRESH_BINARY)
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=2)
    heat = cv2.applyColorMap(d, cv2.COLORMAP_HOT)
    overlay = cv2.addWeighted(img, 0.45, heat, 0.55, 0)
    overlay[mask > 0] = cv2.addWeighted(overlay, 0.6, np.full_like(overlay, (0, 220, 255)), 0.4, 0)[mask > 0]
    frac = float((mask > 0).mean())
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), overlay, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return {
        "ok": True,
        "pan": int(pan),
        "station": station,
        "d_pan": int(d_pan),
        "ref": ref_p.name,
        "frac": round(frac, 4),
        "diff_mean": round(float(d.mean()), 2),
        "out": str(out_path),
    }


LAST_DIR = Path(
    os.environ.get(
        "RAIL_BODY_AUDIT_LAST",
        str(_REPO / "data" / "body_audit_last"),
    )
)
LAST_JSON = LAST_DIR / "last_count_check.json"


# ── Uncounted-body check (the User 2026-08-08) ────────────────────────────────
# A person can be IN the room without ever crossing the door classifier —
# 14:19 today: someone in a robe passed the camera, the pack landed kind=None,
# roster stayed 0. No camera movement needed to catch it: diff the live door-
# home view against an empty door-home ref. Measured separation on that pack:
# empty 2.5-2.8%, person 34-40%, live-empty 0.0%.
DOOR_REF = REFS_DIR / "door_home_live.jpg"
UNCOUNTED_MIN_FRAC = float(os.environ.get("RAIL_UNCOUNTED_BODY_FRAC", "0.15"))
UNCOUNTED_ENABLED = os.environ.get("RAIL_UNCOUNTED_BODY", "1") == "1"
_DOOR_REF_CACHE: tuple[float, Any] = (0.0, None)


def door_body_frac(frame_bgr) -> float:
    """Largest foreground blob (fraction of frame) vs the empty door-home ref."""
    global _DOOR_REF_CACHE
    import cv2
    import numpy as np

    if not UNCOUNTED_ENABLED or not DOOR_REF.is_file():
        return -1.0
    m = DOOR_REF.stat().st_mtime
    if m != _DOOR_REF_CACHE[0]:
        _DOOR_REF_CACHE = (m, cv2.imread(str(DOOR_REF)))
    ref = _DOOR_REF_CACHE[1]
    if ref is None or frame_bgr is None:
        return -1.0
    h, w = frame_bgr.shape[:2]
    r = cv2.resize(ref, (w, h)) if ref.shape[:2] != (h, w) else ref
    a = cv2.GaussianBlur(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    b = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    ma, mb = float(a.mean()), float(b.mean())
    if mb > 1.0:
        b = np.clip(b.astype(np.float32) * (ma / mb), 0, 255).astype("uint8")
    d = cv2.absdiff(a, b)
    _t, mask = cv2.threshold(d, DIFF_THR, 255, cv2.THRESH_BINARY)
    mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=2)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8), iterations=1)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask)
    return max(
        (float(stats[i][4]) / (h * w) for i in range(1, n)), default=0.0
    )


def refresh_door_ref(frame_bgr) -> bool:
    """Store the current (verified-empty) door-home view as the ref."""
    import cv2

    try:
        REFS_DIR.mkdir(parents=True, exist_ok=True)
        ok, jpg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            return False
        tmp = DOOR_REF.with_suffix(".tmp")
        tmp.write_bytes(jpg.tobytes())
        tmp.replace(DOOR_REF)
        return True
    except Exception:
        return False


def capture_refs(svc, *, force: bool = False) -> dict[str, Any]:
    """Empty-room VIDEO (same path as count). Refuses when occupied."""
    from .camera_call import _raw_post, hold
    from .sweep_pose import video_sweep_pose

    st = svc.get()
    if not force and (st.headcount or 0) != 0:
        return {
            "ok": False,
            "error": f"room not empty (headcount={st.headcount}) — pass force "
            "only after an eye-check",
        }
    pose = video_sweep_pose()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = REFS_DIR / f"_stage_sweep_{stamp}"
    staging.mkdir(parents=True, exist_ok=True)
    body = {
        "video": True,
        "two_way": False,
        "start_pan": pose["start_pan"],
        "end_pan": pose["end_pan"],
        "tilt": pose["tilt"],
        "zoom": pose["zoom"],
        "budget_sec": 40,
        "fps": 30,
        "archive_max": 360,
        "out_dir": str(staging),
    }
    try:
        with hold(owner="room-refs", ttl_sec=180.0, reason="empty_ref_video"):
            sweep = _raw_post("/v1/smooth_scan", body, 70.0)
    except Exception as e:
        return {"ok": False, "error": f"video_sweep:{e}"[:200], "pose": pose}
    if not sweep.get("ok") or str(sweep.get("mode") or "") != "one_pan_video":
        return {
            "ok": False,
            "error": sweep.get("error") or "video_sweep_failed",
            "mode": sweep.get("mode"),
            "pose": pose,
        }
    n = len(load_sweep_index(staging))
    if n < 8:
        return {"ok": False, "error": f"short_video n={n}", "pose": pose}
    dest = sweep_dir()
    super_d = REFS_DIR / f"_superseded-{stamp}"
    super_d.mkdir(parents=True, exist_ok=True)
    if dest.is_dir():
        dest.rename(super_d / "sweep")
    staging.rename(dest)
    harvested = harvest_station_stills(dest, REFS_DIR, tilt_deg=int(pose["tilt_deg"]))
    return {
        "ok": True,
        "mode": "video",
        "n_frames": n,
        "tilt_deg": pose["tilt_deg"],
        "sitters": pose.get("sitters") or [],
        "harvested": harvested,
        "sweep_dir": str(dest),
        "wall_sec": sweep.get("wall_sec"),
    }


def score_live_sweep(
    live_dir: Path | str,
    ref_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Diff a live video sweep against the empty-ref video. No camera. No SoT."""
    import cv2
    import numpy as np

    live = load_sweep_index(live_dir)
    refs = load_sweep_index(ref_dir or sweep_dir())
    if not live:
        return {"ok": False, "error": "no_live_frames"}
    if not refs:
        return {"ok": False, "error": "need_empty_video_ref", "n_live": len(live)}
    per: list[int] = []
    cards: list[dict[str, Any]] = []
    LAST_DIR.mkdir(parents=True, exist_ok=True)
    for i, (pan, tilt, path) in enumerate(live):
        ref_p, d_pan = nearest_in_index(refs, int(pan), tilt=tilt)
        # 400000 is the same-tilt penalty in nearest_in_index. A match that
        # large is a different pitch — level still vs −5° empty-chair ref
        # scored 1 body on 2026-08-18 while the User was at the glass.
        if ref_p is None or int(d_pan) >= 400000:
            continue
        cur = cv2.imread(str(path))
        ref = cv2.imread(str(ref_p))
        if cur is None or ref is None:
            continue
        # "Is this frame looking at the door" — against the configured door, not
        # a literal. At the re-measured door pan the old constant still passed only because the
        # 18000 drift fits inside the 40000 tolerance; the next move would not.
        from .seat_shot import door_pose as _dpose

        res = _diff_bodies(ref, cur,
                           door_edge=abs(int(pan) - _dpose()["pan"]) < 40000)
        n_b = int(res.get("bodies") or 0)
        per.append(n_b)
        live_name = f"live_v{i:03d}_pan{pan}.jpg"
        try:
            (LAST_DIR / live_name).write_bytes(path.read_bytes())
        except Exception:
            live_name = ""
        cards.append(
            {
                "i": i,
                "pan": int(pan),
                "tilt": tilt,
                "bodies": n_b,
                "d_pan": int(d_pan),
                "ref": ref_p.name,
                "photo": live_name,
                "photo_url": f"/v1/count_match/photo/{live_name}" if live_name else None,
            }
        )
    bodies_est = max(per) if per else 0
    return {
        "ok": True,
        "n_live": len(live),
        "n_scored": len(per),
        "bodies_estimate": bodies_est,
        "bodies_sum_raw": sum(per),
        "cards": cards,
        "per_frame": per,
    }


def _run_video_audit(svc) -> dict[str, Any]:
    """Same VIDEO path as empty-ref capture. Diff vs that library. No SoT."""
    from .camera_call import _raw_post, hold
    from .sweep_pose import video_sweep_pose

    pose = video_sweep_pose()
    refs = load_sweep_index()
    if not refs:
        return {
            "ok": False,
            "error": "need_empty_video_ref",
            "hint": "empty the room; cron/M&S shoots the same video as this count",
            "pose": pose,
            "facefinder": False,
        }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    live_dir = LAST_DIR / f"live_sweep_{stamp}"
    live_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "video": True,
        "two_way": False,
        "start_pan": pose["start_pan"],
        "end_pan": pose["end_pan"],
        "tilt": pose["tilt"],
        "zoom": pose["zoom"],
        "budget_sec": 40,
        "fps": 30,
        "archive_max": 360,
        "out_dir": str(live_dir),
    }
    try:
        with hold(owner="count-video", ttl_sec=180.0, reason="body_count_video"):
            sweep = _raw_post("/v1/smooth_scan", body, 70.0)
    except Exception as e:
        return {"ok": False, "error": f"video_sweep:{e}"[:200], "facefinder": False}
    if not sweep.get("ok") or str(sweep.get("mode") or "") != "one_pan_video":
        return {
            "ok": False,
            "error": sweep.get("error") or "video_sweep_failed",
            "mode": sweep.get("mode"),
            "facefinder": False,
        }
    scored = score_live_sweep(live_dir)
    if not scored.get("ok"):
        return {**scored, "facefinder": False, "pose": pose}
    st = svc.get()
    present = [pp.name or pp.slot_id for pp in st.people]
    roster_n = len(present)
    bodies_est = int(scored.get("bodies_estimate") or 0)
    verdict = "match"
    if bodies_est > roster_n:
        verdict = "occupancy_deficit"
    elif bodies_est < roster_n:
        verdict = "occupancy_surplus"
    out = {
        "ok": True,
        "schema": "count_check/v1",
        "purpose": "error_check_body_count_only",
        "mode": "video",
        "facefinder": False,
        "law": (
            "Count VIDEO is the same path as empty-ref VIDEO "
            f"(main {pose['tilt_deg']}° + sitter holds). "
            "Diff = body. Estimate = max(frame), not sum. Evidence only."
        ),
        "ts": time.time(),
        "pose": pose,
        "scan_dir": str(live_dir),
        "cards": scored.get("cards") or [],
        "bodies_estimate": bodies_est,
        "bodies_total": bodies_est,
        "bodies_sum_raw": scored.get("bodies_sum_raw") or 0,
        "roster": present,
        "roster_n": roster_n,
        "sot_headcount": int(getattr(st, "headcount", None) or roster_n),
        "verdict": verdict,
        "missing_refs": [],
        "tilt_deg": pose["tilt_deg"],
        "tilt": pose["tilt"],
        "n_live": scored.get("n_live"),
        "wall_sec": sweep.get("wall_sec"),
        "match_game": {
            "url": "/v1/count_match",
            "prompt": "How many distinct people in the room? (not sum of frames)",
            "system_guess": bodies_est,
            "sot": roster_n,
        },
    }
    try:
        ( _REPO / "data" / "body_audit_log.jsonl").open("a").write(json.dumps(out) + "\n")
    except Exception:
        pass
    try:
        LAST_JSON.write_text(json.dumps(out, indent=2))
    except Exception:
        pass
    return out


def run_audit(
    svc,
    *,
    stations: list[int] | None = None,
    tilt_deg: int | float | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Error-check sweep: body COUNT only vs empty-room refs.

    Default mode=video: same gimbal path as empty-ref capture.
    mode=stations: old settled clock-station stills.
    """
    want = (mode or os.environ.get("RAIL_BODY_AUDIT_MODE") or "video").strip().lower()
    if want != "stations":
        return _run_video_audit(svc)
    return _run_station_audit(svc, stations=stations, tilt_deg=tilt_deg)


def _run_station_audit(
    svc,
    *,
    stations: list[int] | None = None,
    tilt_deg: int | float | None = None,
) -> dict[str, Any]:
    """Legacy settled-station count. Prefer the video path."""
    import cv2
    import numpy as np

    from .room_sweep import _post, _snapshot

    t_deg = AUDIT_TILT if tilt_deg is None else int(tilt_deg)
    presets = _presets(svc, stations=stations, tilt_deg=t_deg)
    # Light drift: refs shot this morning read afternoon sun as bodies. When
    # the room is VERIFIED empty and the refs are stale, re-shoot them first
    # so "empty" always means empty *in this light* (the User 2026-08-08).
    try:
        st0 = svc.get()
        oldest = min(
            (
                _ref_path(p["station"]).stat().st_mtime
                for p in presets
                if _ref_path(p["station"]).is_file()
            ),
            default=0.0,
        )
        if (
            (st0.headcount or 0) == 0
            and getattr(st0, "count_certain", False)
            and (time.time() - oldest) > REF_MAX_AGE_SEC
        ):
            capture_refs(svc)
    except Exception:
        pass
    missing = [
        p["station"]
        for p in presets
        if not _ref_path(p["station"], tilt_deg=t_deg).is_file()
    ]
    lease = _post(
        "/v1/lease",
        {"owner": "body_audit_count_only", "ttl_sec": 90},
        timeout=8.0,
    )
    if not lease.get("ok"):
        return {"ok": False, "error": "camera_leased", "lease": lease}
    lease_id = lease["lease_id"]
    stations_out: dict[str, Any] = {}
    per_station_bodies: list[int] = []
    sum_raw = 0
    cards: list[dict[str, Any]] = []
    LAST_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # Always release FaceFinder / facelock before count peeks
        try:
            _post("/v1/facelock/release", {}, timeout=3.0)
        except Exception:
            pass
        for p in presets:
            st_key = p["station"]
            rp = _ref_path(st_key, tilt_deg=int(p.get("tilt_deg") or t_deg))
            aim = _post(
                "/v1/aim",
                {
                    "pan": p["pan"],
                    "tilt": p["tilt"],
                    "zoom": 100,
                    "lease_id": lease_id,
                    "hold_sec": SETTLE + 0.3,
                },
            )
            if not aim.get("ok"):
                continue
            time.sleep(SETTLE)
            try:
                jpeg = _snapshot()
            except Exception:
                continue
            # Always keep live still for match game (even without empty ref)
            live_name = f"live_st{st_key}.jpg"
            try:
                (LAST_DIR / live_name).write_bytes(jpeg)
            except Exception:
                live_name = ""
            need_ref = not rp.is_file()
            n_b = 0
            res: dict[str, Any] = {
                "bodies": 0,
                "partials": 0,
                "blobs": [],
                "need_ref": need_ref,
            }
            if not need_ref:
                cur = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                ref = cv2.imread(str(rp))
                if cur is not None and ref is not None:
                    res = _diff_bodies(ref, cur, p["door_edge"])
                    n_b = int(res.get("bodies") or 0)
                else:
                    res["need_ref"] = True
            stations_out[str(st_key)] = {
                **res,
                "label": p.get("label") or str(st_key),
                "pan": p["pan"],
                "tilt_deg": p.get("tilt_deg"),
                "photo": live_name,
                "photo_url": f"/v1/count_match/photo/{live_name}" if live_name else None,
                "note": p.get("note") or ("needs empty-room ref" if need_ref else ""),
            }
            if not need_ref:
                per_station_bodies.append(n_b)
                sum_raw += n_b
            cards.append(
                {
                    "station": st_key,
                    "label": p.get("label") or str(st_key),
                    "bodies": n_b if not need_ref else "?",
                    "photo_url": f"/v1/count_match/photo/{live_name}" if live_name else None,
                    "tilt_deg": p.get("tilt_deg"),
                    "note": p.get("note")
                    or ("needs empty-room ref — eye-count only" if need_ref else ""),
                }
            )
        # Door home after count check
        try:
            _post(
                "/v1/door_home",
                {"lease_id": lease_id},
                timeout=8.0,
            )
        except Exception:
            pass
    finally:
        # Releasing the lease says "I am done holding it" — it does not MOVE
        # anything. This used to be the whole cleanup, so a body audit left the
        # camera parked on whichever station it stopped at, zoomed, facefinder
        # still hunting. the User 2026-08-16: "it has been pointing at me for over a
        # minute." One shared contract now, so no tool can forget a step.
        try:
            from . import room_sweep as _rs
            from .seat_shot import go_home

            go_home(_rs, lease_id)
        except Exception:
            pass
        try:
            _post("/v1/lease/release", {"lease_id": lease_id}, timeout=5.0)
        except Exception:
            pass
    # Max across stations = "at least this many" (no double-count same body)
    bodies_est = max(per_station_bodies) if per_station_bodies else 0
    st = svc.get()
    present = [pp.name or pp.slot_id for pp in st.people]
    roster_n = len(present)
    verdict = "match"
    if bodies_est > roster_n:
        verdict = "occupancy_deficit"  # more bodies than roster — missed enter
    elif bodies_est < roster_n:
        verdict = "occupancy_surplus"  # roster holds ghosts — missed exit
    out = {
        "ok": True,
        "schema": "count_check/v1",
        "purpose": "error_check_body_count_only",
        "facefinder": False,
        "law": (
            "Count-only error sweep: empty-ref delta blobs, no FaceFinder. "
            "FF reserved for specific USER auth. Estimate = max(station), "
            "not sum (STONE: no double-count). Chair under cam uses steeper tilt. "
            "Evidence only — not SoT write. Match game: human stamps true total."
        ),
        "ts": time.time(),
        "stations": stations_out,
        "cards": cards,
        "bodies_estimate": bodies_est,
        "bodies_total": bodies_est,  # alias (old field name; was sum — corrected)
        "bodies_sum_raw": sum_raw,  # debug only — overcounts multi-FOV
        "roster": present,
        "roster_n": roster_n,
        "sot_headcount": int(getattr(st, "headcount", None) or roster_n),
        "verdict": verdict,
        "missing_refs": missing,
        "tilt_deg": t_deg,
        "tilt": _tilt_units(t_deg),
        "presets": [
            {
                "station": p["station"],
                "pan": p["pan"],
                "tilt_deg": p.get("tilt_deg"),
                "label": p.get("label"),
            }
            for p in presets
        ],
        "match_game": {
            "url": "/v1/count_match",
            "prompt": "How many distinct people in the room? (not sum of station cards)",
            "system_guess": bodies_est,
            "sot": roster_n,
        },
    }
    try:
        log_path = _REPO / "data" / "body_audit_log.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(out) + "\n")
    except Exception:
        pass
    try:
        LAST_JSON.write_text(json.dumps(out, indent=2))
    except Exception:
        pass
    return out


def count_check(
    svc,
    *,
    tilt_deg: int | float | None = None,
    stations: list[int] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Named entrypoint for operator: body-count error check (no FF, no ID)."""
    return run_audit(svc, stations=stations, tilt_deg=tilt_deg, mode=mode)


def last_count_check() -> dict[str, Any]:
    if LAST_JSON.is_file():
        try:
            return json.loads(LAST_JSON.read_text())
        except Exception:
            pass
    return {"ok": False, "error": "no_count_check_yet", "hint": "POST /v1/count_check first"}


def stamp_count_truth(
    *,
    bodies: int,
    note: str = "",
    stamped_by: str = "user",
    system_guess: int | None = None,
    sot_headcount: int | None = None,
) -> dict[str, Any]:
    """Human gold for body count match game — append-only jsonl."""
    row = {
        "schema": "count_stamp/v1",
        "stamp_id": f"cnt_{int(time.time() * 1000)}",
        "bodies_truth": int(bodies),
        "system_guess": system_guess,
        "sot_headcount": sot_headcount,
        "note": (note or "")[:300],
        "stamped_by": stamped_by,
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "facefinder": False,
        "law": "human body count is train truth for count-check; not SoT write",
    }
    try:
        p = _REPO / "data" / "count_stamps.jsonl"
        with open(p, "a") as f:
            f.write(json.dumps(row) + "\n")
        row["ok"] = True
        row["path"] = str(p)
    except Exception as e:
        row["ok"] = False
        row["error"] = str(e)[:120]
    return row


COUNT_MATCH_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Body count match game</title>
<style>
:root{--bg:#0b0d10;--card:#12151a;--bd:#2a2f36;--tx:#e8eaed;--mu:#9aa0a6;--ok:#81c995;--bad:#f28b82;--acc:#8ab4f8}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);margin:0;padding:1rem}
h1{font-size:1.15rem;margin:0 0 .35rem}
.sub{color:var(--mu);font-size:.85rem;margin-bottom:1rem;max-width:52rem;line-height:1.4}
.row{display:flex;flex-wrap:wrap;gap:.75rem;align-items:flex-start}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:.6rem;width:200px}
.card img{width:100%;height:140px;object-fit:contain;background:#000;border-radius:6px}
.card .nm{font-weight:700;margin-top:.35rem}
.card .bdg{font-size:.8rem;color:var(--acc)}
.chip{display:inline-block;background:#1a2332;border:1px solid var(--bd);border-radius:999px;padding:.2rem .6rem;margin:.15rem;font-size:.85rem}
.chip b{color:var(--ok)}
.chip.warn b{color:var(--bad)}
button,input{font:inherit}
button{cursor:pointer;background:#1a3a2a;color:var(--tx);border:1px solid #2a5a3a;border-radius:8px;padding:.45rem .8rem;margin:.2rem}
button.primary{background:#0d652d;border-color:#3d9a5a;font-weight:700}
button:disabled{opacity:.5}
input[type=number]{width:4rem;padding:.35rem;border-radius:6px;border:1px solid var(--bd);background:#0e1218;color:var(--tx)}
.bar{margin:1rem 0;padding:.75rem;border:1px solid var(--bd);border-radius:10px;background:var(--card);max-width:40rem}
.law{color:#fdd663;font-size:.78rem;margin-top:.5rem}
#status{color:var(--mu);font-size:.85rem;min-height:1.2em}
</style></head><body>
<h1>Body count match game</h1>
<p class="sub">
  System peeks clock seats + <b>chair under camera</b> (steeper tilt for sitters).
  <b>No FaceFinder</b> — only empty-ref body deltas.
  Your job: how many <b>distinct people</b> are in the room? (not sum of cards).
  FF stays for specific USER auth only.
</p>
<div>
  <button type="button" class="primary" id="btnRun">Run count-check sweep</button>
  <button type="button" id="btnReload">Reload last</button>
</div>
<div id="summary" style="margin:.75rem 0"></div>
<div class="row" id="cards"></div>
<div class="bar">
  <div>True body count (you):
    <input type="number" id="truth" min="0" max="20" value="0"/>
    <button type="button" class="primary" id="btnStamp">Stamp truth</button>
  </div>
  <div style="margin-top:.4rem">
    Quick:
    <button type="button" data-n="0">0</button>
    <button type="button" data-n="1">1</button>
    <button type="button" data-n="2">2</button>
    <button type="button" data-n="3">3</button>
    <button type="button" data-n="4">4</button>
    <button type="button" data-n="5">5</button>
  </div>
  <input type="text" id="note" placeholder="note (optional)" style="width:min(28rem,100%);margin-top:.5rem;padding:.4rem;border-radius:6px;border:1px solid var(--bd);background:#0e1218;color:var(--tx)"/>
  <p class="law">Stamp is append-only gold for count models — does not rewrite live SoT headcount.</p>
</div>
<div id="status"></div>
<script>
let last = null;
async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
function render(d) {
  last = d;
  const sum = document.getElementById('summary');
  if (!d || !d.ok) {
    sum.innerHTML = '<span class="chip warn">no data — run sweep</span>';
    document.getElementById('cards').innerHTML = '';
    return;
  }
  const est = d.bodies_estimate, sot = d.sot_headcount, v = d.verdict;
  sum.innerHTML =
    `<span class="chip">system max <b>${est}</b></span>` +
    `<span class="chip">SoT <b>${sot}</b> (${(d.roster||[]).join(', ')||'—'})</span>` +
    `<span class="chip ${v==='match'?'':'warn'}">verdict <b>${v}</b></span>` +
    `<span class="chip">FF <b>off</b></span>` +
    `<span class="chip">tilt <b>${d.tilt_deg}°</b></span>`;
  const cards = d.cards || [];
  document.getElementById('cards').innerHTML = cards.map(c => {
    const img = c.photo_url
      ? `<img src="${c.photo_url}?t=${d.ts||Date.now()}" alt="${c.label}"/>`
      : `<div style="height:140px;background:#000;border-radius:6px"></div>`;
    return `<div class="card">${img}
      <div class="nm">${c.label}</div>
      <div class="bdg">bodies in FOV: <b>${c.bodies}</b> · tilt ${c.tilt_deg ?? '?'}°</div>
      <div style="font-size:.7rem;color:var(--mu)">${c.note||''}</div>
    </div>`;
  }).join('') || '<p class="sub">No station cards (missing empty refs? capture when room empty)</p>';
  document.getElementById('truth').value = est;
}
document.getElementById('btnRun').onclick = async () => {
  const st = document.getElementById('status');
  st.textContent = 'Sweeping seats + chair (no FaceFinder)…';
  document.getElementById('btnRun').disabled = true;
  try {
    const d = await api('/v1/count_check', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({tilt_deg: -20}),
    });
    render(d);
    st.textContent = d.ok ? 'Done — pick true headcount' : (d.error || 'failed');
  } catch (e) {
    st.textContent = String(e);
  } finally {
    document.getElementById('btnRun').disabled = false;
  }
};
document.getElementById('btnReload').onclick = async () => {
  render(await api('/v1/count_match/last'));
};
document.querySelectorAll('button[data-n]').forEach(b => {
  b.onclick = () => { document.getElementById('truth').value = b.dataset.n; };
});
document.getElementById('btnStamp').onclick = async () => {
  const bodies = parseInt(document.getElementById('truth').value, 10);
  const note = document.getElementById('note').value || '';
  const r = await api('/v1/count_match/stamp', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      bodies,
      note,
      stamped_by: 'user',
      system_guess: last && last.bodies_estimate,
      sot_headcount: last && last.sot_headcount,
    }),
  });
  document.getElementById('status').textContent = r.ok
    ? `Stamped truth=${bodies} (guess was ${last && last.bodies_estimate})`
    : (r.error || 'stamp failed');
};
api('/v1/count_match/last').then(render).catch(() => {});
</script>
</body></html>
"""
