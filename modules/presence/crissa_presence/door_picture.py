"""Is the live picture the door? (the User 2026-10-06)

10-06 04:00-09:35 the camera's readback said door (pan 432000, tilt 0, zoom 100) while the picture sat on the User's
seat: the firmware does not report what FaceFinder / head-framing moved, and head-framing's GET reads 1 whether or not
a SET 0 took. So a return to the door is verified by the PICTURE: ORB features of the newest frame matched (RANSAC
inliers) against data/body_audit_refs/door_home_live.jpg and the desk stop. Measured 10-06 at the door: door 162-294
inliers, every other stop 3-25.

Switch RAIL_DOOR_PICTURE_VERIFY (default 1; 0 = no picture check after a home). Numbers only - no frame is written.
"""
from __future__ import annotations

from . import paths as _paths

import os
from pathlib import Path

REFS = Path(os.environ.get("RAIL_DOOR_PICTURE_REFS",
                           str(_paths.DATA / "body_audit_refs")))
DOOR_REF = "door_home_live.jpg"
OTHER_REFS = ("st6_tilt-5.jpg", "stchair_tilt-20.jpg", "st8_tilt-5.jpg")
DOOR_MIN = int(os.environ.get("RAIL_DOOR_PICTURE_MIN", "100"))
RATIO = 3.0
_CACHE: dict = {}


def enabled() -> bool:
    """On by default WHERE ITS PICTURES ARE (the User 2026-10-10, .101): the door and stop pictures are the house's own
    room, recorded on the house; a pack ships none, so on a fresh install every home tried four missing files and
    OpenCV warned about each one (camera.log). With no door picture there is nothing to compare: the check is off,
    quietly, and the home is judged by the readback as before."""
    if os.environ.get("RAIL_DOOR_PICTURE_VERIFY", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return (REFS / DOOR_REF).is_file()


def _gray(cv2, np, jpg_or_path):
    if isinstance(jpg_or_path, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(jpg_or_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    elif not Path(jpg_or_path).is_file():
        return None                      # a picture this install does not have: no OpenCV warning, nothing to compare
    else:
        img = cv2.imread(str(jpg_or_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return cv2.resize(img, (960, int(960 * img.shape[0] / img.shape[1])))


def _inliers(cv2, np, a_kd, b_kd) -> int:
    (ka, da), (kb, db) = a_kd, b_kd
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return len(good) // 2
    src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    _h, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    return int(mask.sum()) if mask is not None else 0


def check(jpg: bytes) -> dict:
    """{"ok": bool, "door": n, "best_other": n, "is_door": bool} - ok False when it cannot judge (never a fault)."""
    try:
        import cv2
        import numpy as np
        orb = cv2.ORB_create(2000)
        live = _gray(cv2, np, jpg)
        if live is None:
            return {"ok": False, "error": "no frame"}
        refs = _CACHE.get("refs")
        if refs is None:
            refs = {}
            for name in (DOOR_REF, *OTHER_REFS):
                g = _gray(cv2, np, REFS / name)
                if g is not None:
                    refs[name] = orb.detectAndCompute(g, None)
            _CACHE["refs"] = refs
        if DOOR_REF not in refs:
            return {"ok": False, "error": f"no {DOOR_REF}"}
        lk = orb.detectAndCompute(live, None)
        door = _inliers(cv2, np, lk, refs[DOOR_REF])
        other = max([_inliers(cv2, np, lk, refs[n]) for n in OTHER_REFS if n in refs] or [0])
        return {"ok": True, "door": door, "best_other": other,
                "is_door": door >= DOOR_MIN and door >= RATIO * max(other, 1)}
    except Exception as e:  # noqa: BLE001 - a picture check never breaks a home
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:120]}
