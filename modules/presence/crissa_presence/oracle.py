"""Oracle — decide ADD or SUB, describe what crossed, hand over one frame.

the User 2026-08-09:
  "Oracle has only two jobs add/sub, when decided send best pic to cache."
  "It should explain along the entire domain what it sees relative to which
   human is here. It can certainly keep track of our known pets."

Two design rules, both learned the hard way today:

**Oracle is blind to Presence.** It never reads the roster to decide who left.
The voice chain hard-filters candidates by roster and the face chain
hard-filters by gender, and both throw away correct answers because a prior
said the person could not be there — the User's 32 embeddings are never even
dot-producted on a soft-F misfire. Presence has been wrong 172 times for
another person; feeding it back into perception makes the error self-confirming.
Oracle reports what the pixels show. Inspector matches that against the cache.

**A body that is not a person is still a fact.** A dog sat in the doorway at
14:20:52 today and the system, having no vocabulary for it, recorded a
another person exit. Oracle names what it saw — its height relative to the doorway
is measured and reported, not silently thresholded away — and lets Inspector
decide whether the human count moves.

Evidence comes from the tape (`tape.py`), not the 20-second ring, so the
crossing is actually inside the window rather than clamped out of it.
"""
from __future__ import annotations

import os
from typing import Any

# A standing adult fills most of the door opening; the dog measured ~0.20.
# Reported, never used to silently discard — Inspector decides.
PERSON_MIN_H = float(os.environ.get("RAIL_ORACLE_PERSON_MIN_H", "0.45"))
# Motion must move this fraction of frame width across the window to be a
# crossing rather than someone pausing in the doorway.
MIN_TRAVEL = float(os.environ.get("RAIL_ORACLE_MIN_TRAVEL", "0.06"))


def _blobs(img, ref, *, min_h_frac: float = 0.10) -> list[dict[str, Any]]:
    """Foreground bodies in one frame, exposure-normalised against a ref.

    Same technique body_signature uses for clothing: raw brightness is useless
    under the hallway-light swings, so the reference is gain-matched first.
    """
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    gr = cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    mg, mr = float(g.mean()), float(gr.mean())
    if mr > 1.0:
        gr = np.clip(gr.astype(np.float32) * (mg / mr), 0, 255).astype("uint8")
    mask = (cv2.absdiff(g, gr) > 26).astype("uint8")
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    # A torso, an arm and a leg arrive as separate components once anything
    # occludes them, and a hallway light-patch arrives as a fourth. Closing
    # with a body-scale kernel merges the parts of ONE person while leaving
    # two people who are genuinely apart as two. Measured 2026-08-09: a 9px
    # kernel reported 7 "bodies" for the User walking in alone.
    k = max(9, int(0.035 * w) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    areas = [stats[i][4] for i in range(1, n)]
    big = max(areas) if areas else 0
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bh < min_h_frac * h or area < 0.004 * h * w:
            continue
        # Anything much smaller than the dominant blob in the same frame is a
        # fragment or a light patch, not a second occupant.
        if big and area < 0.30 * big:
            continue
        out.append({
            "cx": float(cent[i][0]) / w,
            "cy": float(cent[i][1]) / h,
            "h_frac": float(bh) / h,
            "w_frac": float(bw) / w,
            "area_frac": float(area) / (h * w),
            "bbox": [int(x), int(y), int(x + bw), int(y + bh)],
        })
    out.sort(key=lambda b: -b["area_frac"])
    return out


def judge(
    frames: list[dict[str, Any]],
    *,
    door_ref_path: str | None = None,
    engine: Any = None,
) -> dict[str, Any]:
    """Read an event window and return one judgement PER BODY.

    `frames` are tape rows ({ts, path}) spanning the whole event. Returns
    {"bodies": [...], "add": n, "sub": n, ...} where each body carries its own
    direction, so a double entry yields two ADDs and a null yields one ADD and
    one SUB — never a single blended verdict. Each event impacts one ID.
    """
    import cv2

    if not frames:
        return {"ok": False, "reason": "no_frames", "bodies": []}

    ref = cv2.imread(door_ref_path) if door_ref_path else None
    if ref is None:
        first = cv2.imread(frames[0]["path"])
        ref = first
    if ref is None:
        return {"ok": False, "reason": "no_reference", "bodies": []}

    track: list[dict[str, Any]] = []
    peak = 0
    for r in frames:
        img = cv2.imread(r["path"])
        if img is None:
            continue
        bs = _blobs(img, ref)
        peak = max(peak, len(bs))
        for b in bs:
            track.append(dict(b, ts=r["ts"], path=r["path"]))

    if not track:
        return {"ok": True, "bodies": [], "peak_bodies": 0,
                "note": "no body crossed this window"}

    # Single-body case is the honest one to automate. With two or more
    # concurrent bodies the association problem is real, and guessing it is
    # how one event ends up moving two IDs — so it is surfaced for the VLM
    # rather than resolved here.
    if peak > 1:
        best = max(track, key=lambda b: b["area_frac"])
        return {
            "ok": True, "peak_bodies": peak, "needs_vlm": True,
            "reason": "multiple concurrent bodies — association not guessed",
            "best_frame": best["path"], "bodies": [],
        }

    track.sort(key=lambda b: b["ts"])
    x0, x1 = track[0]["cx"], track[-1]["cx"]
    travel = x1 - x0
    tallest = max(b["h_frac"] for b in track)
    best = max(track, key=lambda b: b["area_frac"])

    if abs(travel) < MIN_TRAVEL:
        direction, why = None, f"moved {travel:+.3f} of frame width — no crossing"
    else:
        # The door sits camera-right of the room. Motion toward the door edge
        # is outbound; away from it is inbound.
        direction = "sub" if travel > 0 else "add"
        why = f"centroid moved {travel:+.3f} across {len(track)} observations"

    face_px = None
    if engine is not None:
        img = cv2.imread(best["path"])
        if img is not None:
            try:
                from .face_engine import FaceEngine
                for f in engine.detect(img):
                    bb = FaceEngine.bbox(f)
                    if bb is None:
                        continue
                    px = int(min(float(bb[2]) - float(bb[0]),
                                 float(bb[3]) - float(bb[1])))
                    face_px = max(face_px or 0, px)
            except Exception:
                pass

    body = {
        "direction": direction,
        "why": why,
        "travel": round(travel, 4),
        "height_frac": round(tallest, 3),
        "stature": "person-height" if tallest >= PERSON_MIN_H else "low-body",
        "likely": "human" if tallest >= PERSON_MIN_H else "pet-or-low",
        "face_px": face_px,
        "observations": len(track),
        "first_ts": track[0]["ts"],
        "last_ts": track[-1]["ts"],
        "best_frame": best["path"],
    }
    return {
        "ok": True,
        "peak_bodies": peak,
        "bodies": [body],
        "add": 1 if direction == "add" else 0,
        "sub": 1 if direction == "sub" else 0,
    }
