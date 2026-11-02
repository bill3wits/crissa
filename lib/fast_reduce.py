"""The paint's per-pixel reductions without numpy's last-axis reduce (round 11, the .101 lips, 2026-10-12).

On .101 (a CPU-only laptop, the round-10 kit) the player's speaking paint took 55-77 ms p50 against a 41.7 ms film
frame, so while she spoke the feed carried 12-16 pictures a second, the desk Wall saw no two pictures of one film frame
(its patch count read 0 in every window) and two of every three mouth rows were never painted. Profiled under a
.101-like budget (docs/checkpoints/round11-lips-20261012), 44 % of that paint was four full-frame reductions over the
warped tile's three channels: `warped.max(axis=2) > 0` twice (pose_mouth.square_alpha and composite_stamp, the ROI's own
"where is the tile" test) and `warped.sum(axis=2) > 12` (head_frame.pin_nose_pixel). numpy reduces a size-3 last axis
one pixel at a time: 4.5 ms for the max and 2.6-2.8 ms for the sum on a 736x400 frame, where the same answer from the
three channel planes takes 0.2 ms.

Every helper returns exactly what the expression it replaces returns (tests pin each against it, dtypes included), so
a paint is bit-identical; only its time changes. On in a pack, off on the house (its paint is already inside a film
frame, and the house changes nothing without its restart window); HOUSE_MOUTH_FAST_REDUCE=1|0 forces it.
"""
from __future__ import annotations

import os

import cv2
import numpy as np


def _on() -> bool:
    raw = os.environ.get("HOUSE_MOUTH_FAST_REDUCE", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001 - no state home to ask: today's reductions
        return False


ON = _on()


def any_channel(img: np.ndarray) -> np.ndarray:
    """`img.max(axis=2) > 0` for an H x W x C picture (bool H x W)."""
    if ON and img.ndim == 3 and 1 < img.shape[2] <= 4:
        m = np.maximum(img[..., 0], img[..., 1])
        for c in range(2, img.shape[2]):
            np.maximum(m, img[..., c], out=m)
        return m > 0
    return img.max(axis=2) > 0


def sum_gt(img: np.ndarray, thr: float) -> np.ndarray:
    """`img.sum(axis=2) > thr` (and `img.astype(np.float32).sum(axis=2) > thr`: the same for uint8) for H x W x 3."""
    if ON and img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 3 and img.size:
        a = np.ascontiguousarray(img)
        s = cv2.add(cv2.add(a[..., 0], a[..., 1], dtype=cv2.CV_16U), a[..., 2], dtype=cv2.CV_16U)
        return s > thr
    return img.sum(axis=2) > thr


def sum_gt_f32(img: np.ndarray, thr: float) -> np.ndarray:
    """`img.astype(np.float32).sum(axis=2) > thr`: exact for uint8 (765 at most), today's expression otherwise."""
    if ON and img.dtype == np.uint8:
        return sum_gt(img, thr)
    return img.astype(np.float32).sum(axis=2) > thr


def nonzero_extent(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(y_min, y_max, x_min, x_max) of a 2-D mask's nonzero cells, inclusive, as np.nonzero's min/max; None if none."""
    if ON and mask.ndim == 2 and mask.size:
        m = mask if mask.dtype == np.uint8 else (np.asarray(mask) != 0).astype(np.uint8)
        x, y, w, h = cv2.boundingRect(m)
        if w == 0 or h == 0:
            return None
        return y, y + h - 1, x, x + w - 1
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
