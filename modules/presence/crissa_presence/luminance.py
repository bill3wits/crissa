"""Room luminance / lights-out — one threshold shared with the door clipper.

Clipper (scripts/event-clip.py):
  CLIP_DARK_LUM default 55 — median of per-frame mean grey (160×90).

Rail publishes the same measure on room_presence/v1 so Fable can sleep voice
and freeze training when lights_out without a second number.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any

import numpy as np

# Same env as event-clip; RAIL_DARK_LUM aliases for rail-only deploys.
DARK_LUM = float(
    os.environ.get("RAIL_DARK_LUM")
    or os.environ.get("CLIP_DARK_LUM")
    or "55"
)
# Temporal median of recent frame means (reduces flash / single bad read)
LUM_SAMPLES = int(os.environ.get("RAIL_LUM_SAMPLES", "5"))

_samples: deque[float] = deque(maxlen=max(1, LUM_SAMPLES))


def gray_mean_bgr(frame_bgr: Any, *, size: tuple[int, int] = (160, 90)) -> float | None:
    """Mean luminance of a BGR frame on the clipper's 160×90 grey grid."""
    if frame_bgr is None:
        return None
    try:
        import cv2

        small = cv2.resize(frame_bgr, size, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return float(np.asarray(gray, dtype=np.float32).mean())
    except Exception:
        return None


def gray_mean_jpeg(jpeg: bytes) -> float | None:
    if not jpeg:
        return None
    try:
        import cv2

        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return gray_mean_bgr(frame)
    except Exception:
        return None


def observe(mean_gray: float | None) -> dict[str, Any]:
    """Push one sample; return lights_out + luminance payload for presence/health."""
    if mean_gray is not None and mean_gray == mean_gray:  # not NaN
        _samples.append(float(mean_gray))
    if not _samples:
        return {
            "lights_out": False,
            "luminance": None,
            "threshold": DARK_LUM,
            "n_samples": 0,
            "note": "no_frame_yet",
        }
    # Clipper: median of sample means
    lum = float(np.median(list(_samples)))
    return {
        "lights_out": bool(lum < DARK_LUM),
        "luminance": round(lum, 2),
        "threshold": DARK_LUM,
        "n_samples": len(_samples),
        "last_sample": round(float(_samples[-1]), 2),
        "measure": "median_of_frame_mean_gray_160x90",
        "env": "CLIP_DARK_LUM|RAIL_DARK_LUM",
    }


def reset() -> None:
    _samples.clear()


def lights_block(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Canonical presence/health nested object."""
    p = payload if payload is not None else observe(None)
    return {
        "lights_out": bool(p.get("lights_out")),
        "luminance": p.get("luminance"),
        "threshold": p.get("threshold", DARK_LUM),
        "n_samples": p.get("n_samples", 0),
        "measure": p.get("measure") or "median_of_frame_mean_gray_160x90",
        "auth_available": not bool(p.get("lights_out")),
        "refusal": (
            "I can't verify who's there in the dark"
            if p.get("lights_out")
            else None
        ),
        "note": (
            "Dark = no face-auth grade. Ear stays open; voice/assistant sleep is Fable. "
            "Same threshold as door clipper CLIP_DARK_LUM."
            if p.get("lights_out")
            else p.get("note")
        ),
    }
