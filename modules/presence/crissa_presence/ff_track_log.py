"""FaceFinder's correction, measured (the User 2026-10-02).

The firmware FaceFinder is engaged from a stop (the desk stop, a seat) and released a couple of seconds later,
and the caller re-aims straight after. Nothing recorded what the tracking moved: the HTTP audit line reads
`pan_absolute` and showed the same pan before and after all 19 desk engagements of 09-30..10-02, which is either
no movement or a firmware that does not report its own tracking motion. So both are logged, at engage and at
release (release is logged BEFORE the release itself, so before any re-aim):

  pan / tilt / zoom   the V4L2 controls as the camera reports them
  shift               the scene's own move between the engage frame and the release frame (ORB + RANSAC,
                      pixels; positive dx = the picture moved right) - real motion whatever the controls say

One JSON line per engagement in `data/ff_track.jsonl`. No user, seat or machine value lives here: the device is
the grabber's, degrees are left to whoever holds a measured px/deg (Init / config).

Switch, read on every call (no restart to flip; the grabber needs one restart to carry this code):
  RAIL_FF_TRACK_LOG=1|0 (env wins), else the file `data/ff_track_log.on`. OFF by default.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

_ROOT = _paths.ROOT
SWITCH_FILE = Path(os.environ.get("RAIL_FF_TRACK_LOG_FILE", str(_ROOT / "data" / "ff_track_log.on")))
LOG = Path(os.environ.get("RAIL_FF_TRACK_LOG_PATH", str(_ROOT / "data" / "ff_track.jsonl")))
CTRLS = ("pan_absolute", "tilt_absolute", "zoom_absolute")

_lock = threading.Lock()
_start: dict | None = None


def enabled() -> bool:
    raw = os.environ.get("RAIL_FF_TRACK_LOG", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    return SWITCH_FILE.exists()


def _ctrls(read: Callable[[str], int | None]) -> dict:
    out = {}
    for name in CTRLS:
        try:
            out[name.split("_")[0]] = read(name)
        except Exception:  # noqa: BLE001 - a log never breaks the camera
            out[name.split("_")[0]] = None
    return out


def shift(jpeg_a: bytes | None, jpeg_b: bytes | None) -> dict | None:
    """Similarity transform frame a -> frame b: dx, dy (px), scale, rotation, inliers. None when unmeasurable."""
    if not jpeg_a or not jpeg_b:
        return None
    import cv2
    import numpy as np

    a = cv2.imdecode(np.frombuffer(jpeg_a, np.uint8), cv2.IMREAD_GRAYSCALE)
    b = cv2.imdecode(np.frombuffer(jpeg_b, np.uint8), cv2.IMREAD_GRAYSCALE)
    if a is None or b is None:
        return None
    if a.shape != b.shape:
        a = cv2.resize(a, (b.shape[1], b.shape[0]))
    orb = cv2.ORB_create(3000)
    ka, da = orb.detectAndCompute(a, None)
    kb, db = orb.detectAndCompute(b, None)
    if da is None or db is None:
        return None
    m = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db), key=lambda x: x.distance)[:400]
    if len(m) < 20:
        return None
    pa = np.float32([ka[x.queryIdx].pt for x in m])
    pb = np.float32([kb[x.trainIdx].pt for x in m])
    M, inl = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=3)
    if M is None:
        return None
    return {"dx": round(float(M[0, 2]), 1), "dy": round(float(M[1, 2]), 1),
            "scale": round(float(np.hypot(M[0, 0], M[1, 0])), 4),
            "rot_deg": round(float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), 2),
            "inliers": int(inl.sum()) if inl is not None else 0, "frame_wh": [int(b.shape[1]), int(b.shape[0])]}


def _write(row: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def engaged(read: Callable[[str], int | None], frame: Callable[[], bytes | None], *, client: str = "") -> None:
    """FaceFinder was just engaged: remember where the camera is and what it sees."""
    global _start
    if not enabled():
        return
    try:
        with _lock:
            _start = {"ts": time.time(), "ctrl": _ctrls(read), "jpeg": frame(), "client": client}
    except Exception:  # noqa: BLE001
        _start = None


def releasing(read: Callable[[str], int | None], frame: Callable[[], bytes | None], *, client: str = "") -> dict | None:
    """FaceFinder is about to be released (before the release and any re-aim): log the move. Returns the row."""
    global _start
    if not enabled():
        return None
    try:
        with _lock:
            st, _start = _start, None
        if st is None:
            return None              # released without an engage we saw (already off, or the switch flipped mid-way)
        end = _ctrls(read)
        row = {"ts_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["ts"])),
               "dur_s": round(time.time() - st["ts"], 2), "start": st["ctrl"], "end": end,
               "ctrl_delta": {k: (end[k] - st["ctrl"][k]) if end.get(k) is not None and st["ctrl"].get(k) is not None
                              else None for k in end},
               "shift": shift(st["jpeg"], frame()), "client": client or st.get("client") or ""}
        _write(row)
        return row
    except Exception:  # noqa: BLE001 - a log never breaks the camera
        return None
