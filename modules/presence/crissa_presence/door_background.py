"""What is furniture and what walked in — by subtraction, not by blacklist.

the User 2026-08-15: *"just give it refs dont block pixels"* … *"blank out all
existing things and new folks will just float in and out. No hallway pics at
all."*

The problem this replaces: the door view looks through the open doorway at a
hallway wall with framed family portraits on it. Those portraits detect as
faces. Two defences existed and both were wrong in the same way — they described
the *portraits* instead of describing *the room*:

  * a rectangular pixel mask (`face_zone_masks.json`, `door_home`) covering
    x 0.62-0.76, y 0.10-0.52. On 2026-08-15 it rejected the User's own entry: his
    face centre landed at cx 0.624, **four thousandths of frame width** inside
    the left edge, so FaceID produced no face for a 74x115 px portrait-quality
    shot. The mask's own label records that it had already been re-measured
    once when the camera tilt changed — a coordinate patch that has to be
    re-measured is a patch that will break again.
  * an embedding blacklist of the portrait faces. Hand-maintained, and it only
    reached sim 0.67 against the live portrait — under its own kill threshold.

Subtraction needs neither. A median of empty-door frames IS the room; anything
that differs from it is a person. The portraits are in the background by
construction, so they never become candidates at all, and nothing has to be
enumerated, masked or re-measured when the camera moves — the background is
rebuilt from frames, not from coordinates.

Measured the day it was written, on the frames that motivated it:

    the User's face at the door      74x115 px    92.3% foreground   -> person
    another person's face in the hall  39x51  px    91.0% foreground   -> person
    the framed portrait          12x15  px     0.0% foreground   -> furniture

91% against 0% is not a threshold that needs tuning.

Scope: this answers "is this face a physical object in the room". It says
nothing about who the person is; naming stays with the bank.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .refs import BODY_AUDIT_DIR, LIVE_DIR

REF_DIR = LIVE_DIR
# Nightly room-reference-capture writes these at rest (zoom 200, door home).
# The old glob door-empty-*.jpg was never a capture output — measured 2026-08-20
# live REF_DIR had 0 matches, so subtraction was None and wall prints minted U*.
# Seat/desk stills stay out: different aim, they smear the median.
_REST_CLOSED = frozenset(
    {
        "home-empty-closed.jpg",
        "room-empty-stop-door.jpg",
        "door_home_live.jpg",
    }
)
_REST_OPEN = frozenset({"home-empty-open.jpg"})
# Legacy alias only if capture ever writes it at REF_DIR top-level.
REF_GLOB = os.environ.get("RAIL_DOOR_BG_GLOB", "door-empty-*.jpg")
# Per-pixel absolute grey difference that counts as "changed".
DIFF_THR = int(os.environ.get("RAIL_DOOR_BG_DIFF", "30"))
# Fraction of a face box that must be foreground for it to be a real face.
# The measured gap is 91% vs 0%, so this sits in open space.
MIN_FOREGROUND = float(os.environ.get("RAIL_DOOR_BG_MIN_FG", "0.25"))
# One nightly rest frame is the room. Five was a leftover of the dead glob.
MIN_REFS = int(os.environ.get("RAIL_DOOR_BG_MIN_REFS", "1"))
# Never load archive folders. Nightly promotes into REF_DIR top-level; anything
# older than this is not the room as it is now (the User 2026-08-20).
MAX_AGE_SEC = float(os.environ.get("RAIL_DOOR_BG_MAX_AGE_SEC", str(90 * 86400)))
# Rest camera is already the empty FOV (prints on the door in every frame).
# Ring is that stream (grabber 30fps law). Nightly stills seed once at boot.
REST_RING_MAX = int(os.environ.get("RAIL_DOOR_BG_REST_N", "24"))
REST_UPDATE_MEAN = float(os.environ.get("RAIL_DOOR_BG_REST_UPDATE", "18"))

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "bg_gray": None, "n": 0}
_REST_RING: deque = deque(maxlen=max(4, REST_RING_MAX))
# Faces that sit still in rest FOV are prints, not people.
_PIC_TRACKS: list[dict[str, Any]] = []
PIC_HITS = int(os.environ.get("RAIL_DOOR_BG_PIC_HITS", "6"))
PIC_SEC = float(os.environ.get("RAIL_DOOR_BG_PIC_SEC", "1.2"))
PIC_IOU = float(os.environ.get("RAIL_DOOR_BG_PIC_IOU", "0.45"))


def _fresh(path: Path, now: float) -> bool:
    try:
        age = now - path.stat().st_mtime
    except OSError:
        return False
    return 0.0 <= age <= MAX_AGE_SEC


def _top_level_jpgs(root: Path) -> list[Path]:
    """Live capture outputs only. Never walk _superseded / _stale / _poisoned."""
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        for p in root.iterdir():
            if not p.is_file():
                continue
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            if p.suffix.lower() not in (".jpg", ".jpeg"):
                continue
            out.append(p)
    except OSError:
        return []
    return out


def _ref_paths() -> list[Path]:
    """Nightly rest-frame empties. Closed preferred (prints on the door)."""
    now = time.time()
    found: list[Path] = []
    seen: set[str] = set()
    glob_pat = REF_GLOB
    for root in (REF_DIR, BODY_AUDIT_DIR):
        for p in _top_level_jpgs(root):
            if not _fresh(p, now):
                continue
            name = p.name
            take = name in _REST_CLOSED or name in _REST_OPEN
            if not take and glob_pat:
                try:
                    take = p.match(glob_pat)
                except Exception:
                    take = False
            if not take:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(p)
    closed = [p for p in found if p.name in _REST_CLOSED]
    if closed:
        return sorted(closed)
    return sorted(found)


def _iou(a: Any, b: Any) -> float:
    try:
        ax1, ay1, ax2, ay2 = (float(v) for v in a)
        bx1, by1, bx2, by2 = (float(v) for v in b)
    except (TypeError, ValueError):
        return 0.0
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(
        0.0, by2 - by1
    )
    union = area - inter
    return inter / union if union > 0 else 0.0


def note_rest_face_boxes(bboxes: list[Any], *, at_door: bool, now: float | None = None) -> None:
    """Track rest-FOV faces. Bolted-on prints stay put; walkers do not."""
    if not at_door:
        return
    now = float(now if now is not None else time.time())
    boxes = [b for b in (bboxes or []) if b is not None]
    with _LOCK:
        for t in _PIC_TRACKS:
            t["seen"] = False
        for bb in boxes:
            hit = None
            best = PIC_IOU
            for t in _PIC_TRACKS:
                s = _iou(bb, t["bbox"])
                if s >= best:
                    best, hit = s, t
            if hit is None:
                _PIC_TRACKS.append(
                    {"bbox": list(bb), "hits": 1, "first": now, "last": now, "seen": True}
                )
            else:
                hit["bbox"] = list(bb)
                hit["hits"] = int(hit["hits"]) + 1
                hit["last"] = now
                hit["seen"] = True
        _PIC_TRACKS[:] = [t for t in _PIC_TRACKS if t["seen"] or now - float(t["last"]) < 4.0]


def is_still_picture(bbox: Any, now: float | None = None) -> bool:
    """True when this face has sat still in rest FOV long enough to be a print."""
    if bbox is None:
        return False
    now = float(now if now is not None else time.time())
    with _LOCK:
        for t in _PIC_TRACKS:
            if _iou(bbox, t["bbox"]) < PIC_IOU:
                continue
            if int(t["hits"]) >= PIC_HITS and (now - float(t["first"])) >= PIC_SEC:
                return True
    return False


def adopt_empty_bgr(img_bgr: Any) -> dict[str, Any]:
    """THIS frame is empty. Prints/screens in it are not bodies. Replaces the ring."""
    import cv2
    import numpy as np

    if img_bgr is None:
        return {"ok": False, "reason": "no_frame"}
    g = (
        cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        if len(img_bgr.shape) == 3
        else img_bgr
    )
    ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    raw = bytes(buf) if ok else b""
    live = REF_DIR
    live.mkdir(parents=True, exist_ok=True)
    for name in ("home-empty-closed.jpg", "room-empty-stop-door.jpg", "door_home_live.jpg"):
        (live / name).write_bytes(raw)
    try:
        BODY_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        (BODY_AUDIT_DIR / "door_home_live.jpg").write_bytes(raw)
    except OSError:
        pass
    with _LOCK:
        _REST_RING.clear()
        _REST_RING.append(g)
        _CACHE.update({"key": "adopt", "bg_gray": g, "n": 1})
    return {"ok": True, "shape": [int(g.shape[0]), int(g.shape[1])], "bytes": len(raw)}


def note_rest_frames(frames: list[Any], *, at_door: bool) -> None:
    """Ingest rest-camera frames. Prints stay; a walker is not added to the bg."""
    if not at_door or not frames:
        return
    import cv2
    import numpy as np

    with _LOCK:
        need_seed = not _REST_RING
    seed = _file_background_gray(force=True) if need_seed else None
    with _LOCK:
        if seed is not None and not _REST_RING:
            _REST_RING.append(seed)
        med = _CACHE.get("bg_gray")
        if med is None and _REST_RING:
            med = np.median(np.stack(list(_REST_RING)), axis=0).astype(np.uint8)
    for img in frames:
        if img is None:
            continue
        try:
            if len(img.shape) == 3:
                g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                g = img
        except Exception:
            continue
        with _LOCK:
            if _REST_RING and g.shape != _REST_RING[0].shape:
                continue
            if med is not None and g.shape == med.shape:
                if float(cv2.absdiff(g, med).mean()) > REST_UPDATE_MEAN:
                    continue  # person / motion — keep the empty rest
            _REST_RING.append(g)
            _CACHE["key"] = None
            med = g if med is None else med


def background_gray(force: bool = False) -> Any:
    """Median of the rest stream. Nightly stills only if the ring is empty."""
    import numpy as np

    with _LOCK:
        if _REST_RING:
            bg = np.median(np.stack(list(_REST_RING)), axis=0).astype(np.uint8)
            _CACHE.update({"key": "rest", "bg_gray": bg, "n": len(_REST_RING)})
            return bg
    return _file_background_gray(force=force)


def _file_background_gray(force: bool = False) -> Any:
    import cv2
    import numpy as np

    paths = _ref_paths()
    if len(paths) < MIN_REFS:
        return None
    key = str((len(paths), paths[0].name, paths[-1].name))
    with _LOCK:
        if not force and _CACHE["key"] == key and _CACHE["bg_gray"] is not None:
            return _CACHE["bg_gray"]
    imgs = []
    shape = None
    for p in paths:
        try:
            g = cv2.imdecode(
                np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE
            )
        except OSError:
            continue
        if g is None:
            continue
        if shape is None:
            shape = g.shape
        elif g.shape != shape:
            continue  # a different aim or resolution is not this room
        imgs.append(g)
    if len(imgs) < MIN_REFS:
        return None
    bg = np.median(np.stack(imgs), axis=0).astype(np.uint8)
    with _LOCK:
        _CACHE.update({"key": key, "bg_gray": bg, "n": len(imgs)})
    return bg


def foreground_mask(img_bgr: Any) -> Any:
    """Pixels that differ from the empty room. None when there is no model."""
    import cv2
    import numpy as np

    bg = background_gray()
    if bg is None:
        return None
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if g.shape != bg.shape:
        return None  # camera moved or resized — this model does not describe it
    m = (cv2.absdiff(g, bg) > DIFF_THR).astype(np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def foreground_fraction(img_bgr: Any, bbox: Any, mask: Any = None) -> float:
    """How much of a face box changed against the empty room. -1.0 = unknown.

    -1.0 is not 0.0. No background model means no opinion, and the caller must
    fall through to its other checks rather than treat the face as furniture.
    """
    m = foreground_mask(img_bgr) if mask is None else mask
    if m is None or bbox is None:
        return -1.0
    try:
        x0, y0, x1, y1 = (int(float(v)) for v in bbox)
    except (TypeError, ValueError):
        return -1.0
    h, w = m.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return -1.0
    roi = m[y0:y1, x0:x1]
    return float(roi.mean()) if roi.size else -1.0


def is_furniture(img_bgr: Any, bbox: Any, mask: Any = None) -> bool:
    """True only when the model is confident this face is part of the room.

    Unknown (-1.0) is never furniture. A missing or stale background must not
    silently start rejecting real people — that is the failure this module was
    written to end.
    """
    frac = foreground_fraction(img_bgr, bbox, mask=mask)
    if frac < 0:
        return False
    return frac < MIN_FOREGROUND
