"""Measured face yaw from faceid 5 keypoints. No extra model.

0 = facing the lens. Sign: nose toward image-right eye is positive.
Use to BIN and de-duplicate — not degrees.
"""
from __future__ import annotations

import os
from typing import Any

# THE FRONT BAND IS A DESK CAMERA'S (round 19, .101 2026-10-14: the User looking at the camera read yaw -0.156 and was
# binned three_quarter_L - a desk camera often sits off to one side). |yaw| under FRONT_MAX is front: 0.18, was 0.06.
# CRISSA_FACE_FRONT_MAX=0.06 is round 18's band.
FRONT_MAX = float(os.environ.get("CRISSA_FACE_FRONT_MAX", "0.18") or 0.18)

WANT_BINS = (
    "profile_L",
    "three_quarter_L",
    "front",
    "three_quarter_R",
    "profile_R",
)


def yaw_ratio(kps) -> float | None:
    """Nose vs eye-midpoint along the eye line, in IOD units."""
    try:
        import numpy as np

        pts = np.asarray(kps, dtype=float)
        if pts.shape[0] < 3 or pts.shape[1] < 2:
            return None
        le, re, no = pts[0, :2], pts[1, :2], pts[2, :2]
        eye_mid = (le + re) / 2.0
        iod = float(np.linalg.norm(re - le)) or 1.0
        ax = (re - le) / iod
        return float(np.dot(no - eye_mid, ax) / iod)
    except Exception:
        return None


def yaw_bin(ratio: float | None) -> str | None:
    """Signed storage bin. slight folds into three_quarter for the want set."""
    if ratio is None:
        return None
    a = abs(float(ratio))
    if a < FRONT_MAX:
        return "front"
    side = "_L" if ratio < 0 else "_R"
    if a <= 0.45:
        return "three_quarter" + side
    return "profile" + side


def yaw_from_face(face: Any) -> tuple[float | None, str | None, list | None]:
    kps = getattr(face, "kps", None)
    if kps is None:
        return None, None, None
    try:
        pts = [[float(p[0]), float(p[1])] for p in kps]
    except Exception:
        return None, None, None
    y = yaw_ratio(pts)
    return y, yaw_bin(y), pts


def best_per_bin(recs: list[dict]) -> list[dict]:
    """Highest det_score per yaw bin. Drop unbinned."""
    best: dict[str, dict] = {}
    for r in recs:
        b = r.get("bin")
        if not b:
            continue
        prev = best.get(b)
        if prev is None or float(r.get("det_score") or 0) > float(
            prev.get("det_score") or 0
        ):
            best[b] = r
    order = {b: i for i, b in enumerate(WANT_BINS)}
    return sorted(best.values(), key=lambda r: order.get(r.get("bin"), 99))
