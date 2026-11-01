"""Law 3 — mouth spatial budget (docs/POSE-ATTACH-LAW.md).

The mouth box is the clip's attach the way the hub PNG is the body's.
Harvested from the clip (Haar face → lip box via the hub-up fraction).
Independent of BEAT.maxWait 1.2 s (that number is wait / slide / skip only).

Measured 2026-08-29 on hub-up.png (Haar alt2) + visemes/aa.jpg:

  hub-up face (287, 104, 180, 180)  lip box (351, 238, 54, 20)
  closed bottom 258; aa.jpg bottom 265 → open hangs 7 px; allowance 8 px
  8 px is PROVISIONAL: aa.jpg is the retired phoneme take (different face
  / scale). Redo when the viseme bank is harvested from talking-head.
  talking-head +10 with-aa is not earned headroom. Quote +18 closed.
  talking-head drift 11.0/11.0 px over all 123 frames (not 6.9/11.9 4th-frame).
  ZFRAME y-cut = round(0.700 * 400) = 280
  drift budget 4 px (~1/5 of a ~20 px mouth). Past that, a painted mouth slides.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# MEASUREMENTS COME FROM THE AVATAR MANIFEST, NOT FROM HERE.
#
# Until 2026-09-01 the numbers below were literals in this file, hand-measured
# off Crissa's hub-up.png on 2026-08-29. They describe ONE face. Any second
# avatar would have imported this module and silently worn her mouth geometry,
# which is not a config bug — it is unshippable.
#
# The names are unchanged and still module-level, so every existing caller
# (w2l, pose-hub-check, the bench) keeps working untouched. What changed is
# where the numbers come from: avatars/<HOUSE_AVATAR>/manifest.json.
#
# Formulas stay in code. w2l's paste ramp is the model to copy — it is written
# in mouth-heights off MOUTH_FRAC, so it re-fits itself to any face whose
# manifest is right, with no constant to update.
from avatar import load as _avatar_load, mouth_frac as _avatar_mouth_frac  # noqa: E402
import fast_reduce as _fr
import stamp_fast as _sf  # one film frame's two semiframes share the hole (2026-10-12)  # noqa: E402  the paint's reductions, bit-identical (round 11, 2026-10-12)

_AV = _avatar_load()
ZFRAME = dict(_AV["zframe"])
ZFRAME.pop("note", None)
MOUTH_DRIFT_PX = float(_AV["mouth"]["drift_px"])
MOUTH_OPEN_PX = int(_AV["mouth"]["open_px"])
MOUTH_OPEN_PX_PROVISIONAL = bool(_AV["mouth"].get("open_px_provisional", False))
MOUTH_FACE_UP = tuple(_AV["face"]["mouth_face_up"])
MOUTH_BOX_UP = tuple(_AV["face"]["mouth_box_up"])
_FX, _FY, _FW, _FH = MOUTH_FACE_UP
_MX, _MY, _MW, _MH = MOUTH_BOX_UP
MOUTH_FRAC = _avatar_mouth_frac()

_CASC_ALT2: cv2.CascadeClassifier | None = None
_CASC_DEF: cv2.CascadeClassifier | None = None


def zframe_y_cut(frame_h: int = 400) -> int:
    return int(round((ZFRAME["cy"] + ZFRAME["ch"]) * frame_h))


def zframe_x_cut(frame_w: int = 736) -> int:
    return int(round((ZFRAME["cx"] + ZFRAME["cw"]) * frame_w))


def _cascades() -> tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]:
    global _CASC_ALT2, _CASC_DEF
    if _CASC_ALT2 is None:
        root = cv2.data.haarcascades
        _CASC_ALT2 = cv2.CascadeClassifier(root + "haarcascade_frontalface_alt2.xml")
        _CASC_DEF = cv2.CascadeClassifier(root + "haarcascade_frontalface_default.xml")
    return _CASC_ALT2, _CASC_DEF


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def detect_face(
    gray: np.ndarray, prev: tuple[int, int, int, int] | None = None
) -> tuple[int, int, int, int] | None:
    alt2, default = _cascades()
    fs = alt2.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(48, 48))
    if len(fs) == 0:
        fs = default.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40)
        )
    if len(fs) == 0:
        return None
    hits = [tuple(int(v) for v in r) for r in fs if r[2] >= 80 and r[3] >= 80]
    if not hits:
        return None
    if prev is None:
        hits = sorted(
            hits,
            key=lambda r: abs((r[0] + r[2] / 2) - 377) + abs((r[1] + r[3] / 2) - 194),
        )
        return hits[0]
    px, py, pw, ph = prev
    pcx, pcy = px + pw / 2, py + ph / 2
    hits = sorted(
        hits,
        key=lambda r: abs(r[0] + r[2] / 2 - pcx) + abs(r[1] + r[3] / 2 - pcy),
    )
    best = hits[0]
    # Profile / door-handle hits jump. Drop them rather than invent a box.
    if abs(best[0] + best[2] / 2 - pcx) + abs(best[1] + best[3] / 2 - pcy) > 120:
        return None
    return best


def mouth_from_face(face: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = face
    mx = int(round(x + MOUTH_FRAC[0] * w))
    my = int(round(y + MOUTH_FRAC[1] * h))
    mw = max(8, int(round(MOUTH_FRAC[2] * w)))
    mh = max(6, int(round(MOUTH_FRAC[3] * h)))
    return mx, my, mw, mh


def box_in_zframe(
    box: tuple[int, int, int, int],
    frame_w: int = 736,
    frame_h: int = 400,
) -> bool:
    """True when the whole box is inside the ASCII ZFRAME crop."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return False
    if x < 0 or y < 0:
        return False
    if x + w > zframe_x_cut(frame_w):
        return False
    if y + h > zframe_y_cut(frame_h):
        return False
    return True


def box_in_clip(
    box: tuple[int, int, int, int],
    frame_w: int = 736,
    frame_h: int = 400,
) -> bool:
    """True when the whole box is on the 736×400 clip (hi-res feed)."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return False
    if x < 0 or y < 0:
        return False
    if x + w > frame_w or y + h > frame_h:
        return False
    return True


def interpolate_short_gaps(
    boxes: list[list[int] | None],
    max_gap: int = 2,
) -> list[list[int] | None]:
    """Fill 1-frame Haar flicker only. Long gaps (door / profile) stay null.

    This is not fill-forward: both neighbours are real hits. max_gap=2 fills
    a single missing frame only.
    """
    hits = [i for i, b in enumerate(boxes) if b is not None]
    out = list(boxes)
    for a, b_i in zip(hits, hits[1:]):
        gap = b_i - a - 1
        if gap < 1 or gap >= max_gap:
            continue
        ba, bb = boxes[a], boxes[b_i]
        if ba is None or bb is None:
            continue
        for k in range(1, gap + 1):
            t = k / (gap + 1)
            out[a + k] = [int(round(ba[j] * (1.0 - t) + bb[j] * t)) for j in range(4)]
    return out


def harvest_boxes(
    frames: list[np.ndarray],
) -> list[tuple[int, int, int, int] | None]:
    """One mouth box per frame. None when Haar misses (profile / occluded)."""
    out: list[tuple[int, int, int, int] | None] = []
    prev: tuple[int, int, int, int] | None = None
    for fr in frames:
        face = detect_face(to_gray(fr), prev)
        if face is None:
            out.append(None)
            continue
        prev = face
        out.append(mouth_from_face(face))
    return out


def _filled(boxes: list[tuple[int, int, int, int] | None]) -> list[tuple[int, int, int, int]]:
    """Museum only. Do not write filled boxes for a miss — null is meaningful."""
    filled: list[tuple[int, int, int, int]] = []
    last = MOUTH_BOX_UP
    for b in boxes:
        if b is not None:
            last = b
        filled.append(last)
    return filled


def sidecar_frames(
    boxes: list[tuple[int, int, int, int] | None],
    frame_w: int = 736,
    frame_h: int = 400,
) -> tuple[list[list[int] | None], int | None, int | None]:
    """Per-frame box or null.

    first_sighting = first in-crop hit (often the hub rest).
    reentry = first in-crop hit after a gap — voice/MoveNet mark when she
    comes back on-glass.
    """
    out: list[list[int] | None] = []
    first: int | None = None
    reentry: int | None = None
    seen_null = False
    for i, b in enumerate(boxes):
        # Hi-res feed is the whole clip (FT popup / 736×400 composite).
        # Do not ZFRAME-null a mouth the window can see (nod/notes hang to y~309;
        # FT_SRC runs to y=374). ASCII still crops ZFRAME; that is the ramp.
        if b is None or not box_in_clip(b, frame_w, frame_h):
            out.append(None)
            seen_null = True
            continue
        rec = [int(b[0]), int(b[1]), int(b[2]), int(b[3])]
        out.append(rec)
        if first is None:
            first = i
        if seen_null and reentry is None:
            reentry = i
    return out, first, reentry


def mouth_report(
    frames: list[np.ndarray],
    *,
    sidecar: Path | None = None,
    open_px: int = MOUTH_OPEN_PX,
    drift_px: float = MOUTH_DRIFT_PX,
) -> dict[str, Any]:
    if not frames:
        return {"ok": False, "error": "no frames"}
    h, w = frames[0].shape[:2]
    zcut = zframe_y_cut(h)
    xcut = zframe_x_cut(w)
    boxes = harvest_boxes(frames)
    good = [b for b in boxes if b is not None]
    if not good:
        return {"ok": False, "error": "no face hits", "n": len(frames), "hits": 0}
    box0 = boxes[0] if boxes[0] is not None else good[0]
    cxs = [b[0] + b[2] / 2 for b in good]
    cys = [b[1] + b[3] / 2 for b in good]
    tops = [b[1] for b in good]
    bots = [b[1] + b[3] for b in good]
    dx = float(max(cxs) - min(cxs))
    dy = float(max(cys) - min(cys))
    y0, y1 = int(min(tops)), int(max(bots))
    clear = zcut - y1
    clear_open = clear - int(open_px)
    need_sidecar = dx > drift_px or dy > drift_px
    sidecar_n = None
    sidecar_name = None
    sidecar_ok = False
    if sidecar is not None and sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            frames_sc = data.get("frames") or []
            sidecar_n = len(frames_sc)
            sidecar_name = sidecar.name
            sidecar_ok = sidecar_n == len(frames) and sidecar_n > 0
        except (OSError, json.JSONDecodeError, TypeError):
            sidecar_ok = False
            sidecar_name = sidecar.name
    pass_clear = clear_open >= 0
    pass_drift = (not need_sidecar) or sidecar_ok
    # Fixed rect also has to stay in the wall crop horizontally.
    min_x = min(b[0] for b in good)
    max_x2 = max(b[0] + b[2] for b in good)
    pass_x = min_x >= 0 and max_x2 <= xcut
    return {
        "ok": True,
        "box": [int(box0[0]), int(box0[1]), int(box0[2]), int(box0[3])],
        "drift_dx": round(dx, 1),
        "drift_dy": round(dy, 1),
        "drift_px": drift_px,
        "band_y": [y0, y1],
        "clear_px": int(clear),
        "open_px": int(open_px),
        "open_px_provisional": bool(MOUTH_OPEN_PX_PROVISIONAL),
        "clear_open_px": int(clear_open),
        "zcut": zcut,
        "xcut": xcut,
        "n": len(frames),
        "hits": len(good),
        "miss": len(frames) - len(good),
        "need_sidecar": need_sidecar,
        "sidecar": sidecar_name,
        "sidecar_n": sidecar_n,
        "sidecar_ok": sidecar_ok,
        "pass_clear": pass_clear,
        "pass_drift": pass_drift,
        "pass_x": pass_x,
        # Clause 3 is ASCII visibility, not a harvest defect. The viseme
        # composite is 736x400 and the sidecar is full-clip; a mouth below
        # zcut is the desk pose, not a miss. Composer and FaceTime both see
        # the jaw. Reported as pass_clear, never fatal.
        # Amended 2026-08-30 — docs/POSE-ATTACH-LAW.md clause 3.
        "pass": bool(pass_drift),
        "min_x": int(min_x),
        "max_x2": int(max_x2),
    }


def write_sidecar(
    path: Path,
    frames: list[np.ndarray],
    fps: float,
    dest: Path | None = None,
) -> Path:
    h, w = (frames[0].shape[0], frames[0].shape[1]) if frames else (400, 736)
    raw = harvest_boxes(frames)
    frames_out, first_sighting, reentry = sidecar_frames(raw, w, h)
    frames_out = interpolate_short_gaps(frames_out, max_gap=2)
    visible = next((b for b in frames_out if b is not None), None)
    dest = dest or path.with_suffix(".mouth.json")
    head: dict[str, Any] = {
        "schema": "mouth_track/v1",
        "file": path.name,
        "n": len(frames_out),
        "fps": round(float(fps), 3),
        "first_sighting": first_sighting,
        "reentry": reentry,
    }
    if visible is not None:
        head["box"] = visible
    chunks = json.dumps(head, indent=2).rstrip()
    if chunks.endswith("}"):
        chunks = chunks[:-1].rstrip() + ","
    frame_lines = ",\n".join("    " + json.dumps(fr) for fr in frames_out)
    dest.write_text(
        chunks + '\n  "frames": [\n' + frame_lines + "\n  ]\n}\n",
        encoding="utf-8",
    )
    return dest


def sidecar_path(clip_path: Path) -> Path:
    return clip_path.with_suffix(".mouth.json")


def sidecar_from_head(head: dict, file: str, fps: float = 24.0) -> dict:
    """A mouth_track/v1 from a head track's per-frame `mouth` (2026-09-16).

    Haar misses close-ups, and the up-close pack's sidecars were a static box
    in the film's native 1280×720 pixels, not the 736×400 painter space every
    other sidecar uses. pose-head-frame fits after decode to CLIP_W×CLIP_H and
    keeps the 2d106 mouth box per frame, so the track is already there."""
    w, h = 736, 400
    sw = head.get("src_wh")
    if isinstance(sw, (list, tuple)) and len(sw) == 2:
        w, h = int(sw[0]), int(sw[1])
    boxes: list = []
    for rec in head.get("frames") or []:
        m = (rec or {}).get("mouth") if isinstance(rec, dict) else None
        if isinstance(m, (list, tuple)) and len(m) == 4:
            boxes.append((float(m[0]), float(m[1]), float(m[2]), float(m[3])))
        else:
            boxes.append(None)
    frames_out, first, reentry = sidecar_frames(boxes, w, h)
    frames_out = interpolate_short_gaps(frames_out, max_gap=2)
    visible = next((b for b in frames_out if b is not None), None)
    out: dict[str, Any] = {
        "schema": "mouth_track/v1",
        "file": str(file),
        "n": len(frames_out),
        "fps": round(float(fps), 3),
        "first_sighting": first,
        "reentry": reentry,
        "tracker": "head-track-2d106",
        "note": "per-frame 2d106 mouth from <stem>.head.json (painter space %dx%d)" % (w, h),
    }
    if visible is not None:
        out["box"] = visible
    out["frames"] = frames_out
    return out


def write_sidecar_dict(dest: Path, sc: dict) -> Path:
    """Write a sidecar in the same one-frame-per-line layout write_sidecar uses."""
    head = {k: v for k, v in sc.items() if k != "frames"}
    chunks = json.dumps(head, indent=2).rstrip()
    if chunks.endswith("}"):
        chunks = chunks[:-1].rstrip() + ","
    frame_lines = ",\n".join("    " + json.dumps(fr) for fr in sc.get("frames") or [])
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(chunks + '\n  "frames": [\n' + frame_lines + "\n  ]\n}\n", encoding="utf-8")
    tmp.replace(dest)
    return dest


def strip_lip_alpha(tile: np.ndarray, rect: tuple | None = None,
                    nose_y: float | None = None) -> np.ndarray:
    """Cut the mouth out of a viseme tile. HxW float 0..1.

    The tile is cheeks + lips + cavity. Pasting the whole square (or a fat
    ellipse of it) left her CLIP jaw visible through the same pixels — two
    mouths. This keeps lips, hole and teeth; the cheek border stays 0.
    rect=None and nose_y=None is bit-identical to the hard-coded
    (0.50, 0.44) / 0.36 ellipse. nose_y clamps out the tile's own nose
    (rows y <= nose_y) so a second nose cannot paint.
    """
    h, w = tile.shape[:2]
    lab = cv2.cvtColor(tile, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, A = lab[:, :, 0], lab[:, :, 1]
    e = max(2, h // 16)
    border_L = np.concatenate(
        [L[:e].ravel(), L[-e:].ravel(), L[:, :e].ravel(), L[:, -e:].ravel()]
    )
    border_A = np.concatenate(
        [A[:e].ravel(), A[-e:].ravel(), A[:, :e].ravel(), A[:, -e:].ravel()]
    )
    mL = float(np.median(border_L))
    mA = float(np.median(border_A))
    yy, xx = np.mgrid[0:h, 0:w]
    # Upper-lip registration: the mouth sits upper-mid in the tile, wider than
    # it is tall. Outside this ellipse is cheek and must not paste.
    if rect is not None and len(rect) == 4:
        rx, ry, rw, rh = [float(v) for v in rect]
        cx, cy = rx + rw / 2.0, ry + rh / 2.0
        ax, ay = max(1.0, rw / 2.0), max(1.0, rh / 2.0)
        ell = ((xx - cx) / ax) ** 2 + ((yy - cy) / ay) ** 2 <= 1.0
    else:
        ell = ((xx - 0.50 * w) / (0.36 * w)) ** 2 + ((yy - 0.44 * h) / (0.36 * h)) ** 2 <= 1.0
    lips = (A > mA + 8) & ell
    hole = (L < mL - 16) & ell
    teeth = (L > mL + 10) & ell & (np.abs(yy - 0.50 * h) < 0.18 * h)
    raw = (lips | hole | teeth).astype(np.uint8) * 255
    raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    raw = cv2.GaussianBlur(raw, (7, 7), 0)
    alpha = raw.astype(np.float32) / 255.0
    if nose_y is not None:
        cut = int(math.floor(float(nose_y))) + 1
        if cut > 0:
            alpha[:min(cut, h)] = 0.0
    return alpha


def live_bbox(mask: np.ndarray, pad: int = 0) -> tuple[int, int, int, int] | None:
    """Alpha-live bbox (crop_maps −1 style). Inclusive-exclusive, padded.

    The mouth *box* is smaller than the feather + nose cut. Cropping to the
    box clips the feather and fails bit-identity on edge pixels.
    """
    m = np.asarray(mask)
    if m.ndim == 3:
        m = _fr.any_channel(m) if _fr.ON else m.max(axis=2)
    rows = np.flatnonzero(m.any(axis=1))
    cols = np.flatnonzero(m.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    h, w = m.shape[:2]
    p = max(0, int(pad))
    y0 = max(0, int(rows[0]) - p)
    y1 = min(h, int(rows[-1]) + 1 + p)
    x0 = max(0, int(cols[0]) - p)
    x1 = min(w, int(cols[-1]) + 1 + p)
    return x0, y0, x1, y1


def _composite_roi_on(roi: bool | None = None) -> bool:
    if roi is not None:
        return bool(roi)
    return os.environ.get("HOUSE_MOUTH_COMPOSITE_ROI", "1").strip().lower() not in (
        "0", "false", "no", "off")


def square_alpha(warped: np.ndarray, dst: dict, plane: tuple | None = None,
                 arch: dict | None = None, nose_keep: bool = True,
                 roi: bool | None = None
                 ) -> np.ndarray:
    """Harvested rectangle, nose-cut, feathered. Jaw stays.

    Do NOT multiply by dest rest ellipse/arch falloff — that hole is sized
    to the filmed closed mouth and would clip an /ee/ back to rest (the
    aperture pin). The square keeps its own width and height. Dest hole
    (hide_clip_jaw) is what follows the dental U.

    Desk / talking-head / mouthzoom share dest `s`. Edge is
    `STAMP_EDGE_S * s` (2× the nose-tip cap). A fixed 6 px on a 200 px
    close-up stamp was a rectangle through the nose. The lid above dest
    nose (bridge / eyes) ramps to 0 so the rectangle does not cut the face.
    /ee/ starts at dest nose, so that ramp is a no-op there.

    ROI (default): distanceTransform / nose-keep run on the alpha-live
    bbox, not the mouth box. Kill ``HOUSE_MOUTH_COMPOSITE_ROI=0``.
    """
    from head_frame import STAMP_EDGE_S, dest_nose_keep
    H, W = warped.shape[:2]
    s = float((dst or {}).get("s") or 0.0)
    px = max(6.0, STAMP_EDGE_S * s) if s > 0 else 6.0
    use_roi = _composite_roi_on(roi)
    x0 = y0 = 0
    x1, y1 = W, H
    wroi = warped
    if use_roi:
        live = _fr.any_channel(warped) if warped.ndim == 3 else warped > 0
        box = live_bbox(live, pad=int(math.ceil(3.0 + px)) + 2)
        if box is None:
            return np.zeros((H, W), np.float32)
        x0, y0, x1, y1 = box
        wroi = warped[y0:y1, x0:x1]
    have = _fr.sum_gt_f32(wroi, 12.0).astype(np.uint8)
    # Raised /ee/ commissures ARE the have-edge. dist on the raw mask
    # feathers them to 0. Dilate 3px for the transform only, then multiply
    # back by have so the square does not grow into laugh lines.
    # distanceTransform's L2 chamfer is 1-ULP extent-dependent (measured
    # f1 rest: 35 px inside the blob at pad 17). Run it on the full have
    # so the crop matches the reference path.
    if use_roi:
        have_full = np.zeros((H, W), np.uint8)
        have_full[y0:y1, x0:x1] = have
        have_d = cv2.dilate(have_full, np.ones((7, 7), np.uint8), 1)
        dist = cv2.distanceTransform(have_d, cv2.DIST_L2, 3)[y0:y1, x0:x1]
    else:
        have_d = cv2.dilate(have, np.ones((7, 7), np.uint8), 1)
        dist = cv2.distanceTransform(have_d, cv2.DIST_L2, 3)
    ys, xs = np.where(have > 0)
    a = np.clip(dist / px, 0.0, 1.0).astype(np.float32) * have.astype(np.float32)
    # Same grids as the full-frame path: arange(H/W, float32) sliced, so
    # (float32 - python float) / python float promotes identically.
    # np.arange(y0, y1, dtype=float32) is a different sequence and was a
    # 1-ULP miss that truncated to ±2 in the uint8 blend.
    yy = np.arange(H, dtype=np.float32)[y0:y1, None]
    xx = np.arange(W, dtype=np.float32)[None, x0:x1]
    if dst is not None and xs.size:
        y_top = float(ys.min() + y0)
        ny = float(np.asarray(dst["nose"]).ravel()[1])
        span = ny - y_top
        if span > 4.0:
            a = a * np.clip((yy - y_top) / span, 0.0, 1.0)
    if nose_keep:
        a = a * dest_nose_keep(xx, yy, dst).astype(np.float32)
    if not use_roi:
        return a
    out = np.zeros((H, W), dtype=a.dtype)
    out[y0:y1, x0:x1] = a
    return out


# THE HOLE'S FILL (the User 2026-10-16, Crissa 9:16 phone library: a hard vertical seam down her chin under every
# painted shape). TELEA floods a hole from its rim, so on a big face (9:16 at 720x1280, s ~ 337) the lit left rim and the
# shadowed right rim meet in a straight line down the middle, and the arch reaches below where the square alpha is solid,
# so the line shows through the tile's fade. `smooth` fills with a pull-push membrane (weighted downsample, then
# upsample): no meeting line at any size. Default `telea` = every house face byte-for-byte as before.
HOLE_FILL = os.environ.get("HOUSE_MOUTH_HOLE_FILL", "telea").strip().lower()


def _pull_push(img: np.ndarray, known: np.ndarray) -> np.ndarray:
    I = img.astype(np.float32)
    W = known.astype(np.float32)
    h, w = W.shape
    if min(h, w) <= 3:
        tot = float(W.sum())
        mean = ((I * W[..., None]).sum((0, 1)) / tot) if tot > 0 else I.reshape(-1, I.shape[2]).mean(0)
        return I * W[..., None] + mean[None, None, :] * (1 - W[..., None])
    hs, ws = (h + 1) // 2, (w + 1) // 2
    Is = cv2.resize(I * W[..., None], (ws, hs), interpolation=cv2.INTER_AREA)
    Wsm = cv2.resize(W, (ws, hs), interpolation=cv2.INTER_AREA)
    Ic = _pull_push(Is / np.maximum(Wsm, 1e-6)[..., None], np.minimum(1.0, Wsm * 2.0))
    up = cv2.resize(Ic, (w, h), interpolation=cv2.INTER_LINEAR)
    return I * W[..., None] + up * (1 - W[..., None])


def smooth_fill(patch: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill mask > 0 in a BGR patch from the pixels around it, smoothly (HOUSE_MOUTH_HOLE_FILL=smooth)."""
    return np.clip(_pull_push(patch, (mask == 0)), 0, 255).astype(np.uint8)


def hide_clip_jaw(img: np.ndarray, box: tuple[float, float, float, float],
                  plane: tuple | None = None, hole: str = "ellipse",
                  shear: float | None = None, cusp: float | None = None,
                  arch: dict | None = None, cover: np.ndarray | None = None,
                  dst: dict | None = None, cover_dilate: int = 5
                  ) -> np.ndarray:
    """Erase the clip's own mouth so only visemes remain.

    Inpaint a lip-shaped hole over the sidecar box. TELEA from surrounding
    skin; the viseme tile is then drawn into the hole. Without this pass the
    filmed closed jaw shows through the paste (the User: I still see both).
    plane=None is bit-identical to today (axis-aligned, Haar box).
    plane=(cx, cy, ax, ay, deg) is the head-frame hinge.
    hole='ellipse' is live. hole='sine' / hole='arch' are bench competitors
    (docs/designs/CURVED-SINE-PLANE-20260903.md,
    docs/designs/CURVED-ARCH-PLANE-20260903.md).
    """
    x, y, w, h = box
    out = img.copy()
    hh, ww = out.shape[:2]
    mask = np.zeros((hh, ww), np.uint8)
    if str(hole) == "arch" and arch is not None:
        from head_frame import arch_mask
        mask = _sf.arch_mask(hh, ww, arch, arch_mask) if _sf.ON else arch_mask(hh, ww, arch)
    elif plane is not None and len(plane) >= 4 and str(hole) == "sine":
        from head_frame import SINE_CUSP_N, SINE_SHEAR_A, curved_mask
        a = SINE_SHEAR_A if shear is None else float(shear)
        n = SINE_CUSP_N if cusp is None else float(cusp)
        mask = curved_mask(hh, ww, plane, shear=a, cusp=n)
    elif plane is not None and len(plane) >= 4:
        cx = int(round(float(plane[0])))
        cy = int(round(float(plane[1])))
        ax = max(2, int(round(float(plane[2]))))
        ay = max(2, int(round(float(plane[3]))))
        deg = float(plane[4]) if len(plane) > 4 else 0.0
        cv2.ellipse(mask, (cx, cy), (ax, ay), deg, 0, 360, 255, -1)
    else:
        cx = int(round(x + w / 2.0))
        cy = int(round(y + h * 0.62))
        ax = max(2, int(round(w * 0.70)))
        ay = max(2, int(round(max(h * 1.20, w * 0.36))))
        cv2.ellipse(mask, (cx, cy), (ax, ay), 0, 0, 360, 255, -1)
    if cover is not None:
        # Do not TELEA dest chin the square will not replace — that is
        # the short-chin smear. Dilate so the seam sits inside the tile.
        cov = (np.asarray(cover) > 0).astype(np.uint8) * 255
        if cov.shape[:2] != mask.shape[:2]:
            cov = cv2.resize(cov, (mask.shape[1], mask.shape[0]),
                             interpolation=cv2.INTER_NEAREST)
        d = max(0, int(cover_dilate))
        # The arch band is ~1% of the frame; dilate / nose-keep / AND on the
        # whole picture measured 2.8 ms of that. Crop to the band window plus
        # a `d` halo — dilation reads at most d px away, so the result inside
        # the window is bit-identical, and outside it `mask` is already 0.
        win = None
        if str(hole) == "arch" and arch is not None:
            from head_frame import arch_window
            win = arch_window(hh, ww, arch)
        if win is None:
            if d > 0:
                k = 2 * d + 1
                cov = cv2.dilate(cov, np.ones((k, k), np.uint8), 1)
            if dst is not None:
                from head_frame import dest_nose_keep
                yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
                cov = np.where(dest_nose_keep(xx, yy, dst), cov, np.uint8(0))
            mask = cv2.bitwise_and(mask, cov)
        else:
            wx0, wy0, wx1, wy1 = win
            hx0, hy0 = max(0, wx0 - d), max(0, wy0 - d)
            hx1, hy1 = min(ww, wx1 + d), min(hh, wy1 + d)
            sub = cov[hy0:hy1, hx0:hx1]
            if d > 0:
                k = 2 * d + 1
                sub = cv2.dilate(sub, np.ones((k, k), np.uint8), 1)
            sub = sub[wy0 - hy0:wy1 - hy0, wx0 - hx0:wx1 - hx0]
            if dst is not None:
                from head_frame import dest_nose_keep
                yy, xx = np.mgrid[wy0:wy1, wx0:wx1].astype(np.float32)
                sub = np.where(dest_nose_keep(xx, yy, dst), sub, np.uint8(0))
            band = mask[wy0:wy1, wx0:wx1]
            mask = np.zeros_like(mask)
            mask[wy0:wy1, wx0:wx1] = cv2.bitwise_and(band, sub)
    ext = _fr.nonzero_extent(mask)
    if ext is None:
        return out
    pad = 8
    y0 = max(0, ext[0] - pad)
    x0 = max(0, ext[2] - pad)
    y1 = min(hh, ext[1] + pad + 1)
    x1 = min(ww, ext[3] + pad + 1)
    patch = out[y0:y1, x0:x1]
    m = mask[y0:y1, x0:x1]
    if HOLE_FILL == "smooth":
        out[y0:y1, x0:x1] = smooth_fill(patch, m)
        return out
    out[y0:y1, x0:x1] = _sf.inpaint(patch, m, 3) if _sf.ON else cv2.inpaint(patch, m, 3, cv2.INPAINT_TELEA)
    return out


def tone_b(patch: np.ndarray, dst: np.ndarray, w: np.ndarray,
           on: bool = True) -> np.ndarray:
    """Shift a patch's b* to its destination's. Brightness is already matched
    by a scalar gain; colour was not matched at all.

    Measured 2026-09-01. The clip library splits into three LIGHTING groups,
    and a tile crossing groups landed with a b* (yellow-axis) gap of 8.64
    against the face around it, versus 1.73 for a tile from the same group.
    Matching b* closes it to 1.88 — a cross-group tile becomes indistinguish-
    able from a same-group one, so ONE bank serves every clip and the User does not
    have to mark a second and third lighting setup.

    a* IS DELIBERATELY LEFT ALONE. Its gap is ~7 even within a group, because a
    mouth is redder than the face around it; matching a* would drain the red
    out of her lips to make them agree with her chin. Measured both ways: b*
    alone gets the identical fix (8.64 -> 1.88) and leaves a* at 7.13.
    """
    if not on:
        return patch
    ww = np.clip(np.asarray(w, dtype=np.float64), 0.0, 1.0)
    tot = float(ww.sum())
    if tot <= 8.0:
        return patch
    try:
        pl = cv2.cvtColor(np.clip(patch, 0, 255).astype(np.uint8),
                          cv2.COLOR_BGR2LAB).astype(np.float32)
        dl = cv2.cvtColor(np.clip(dst, 0, 255).astype(np.uint8),
                          cv2.COLOR_BGR2LAB).astype(np.float32)
        # float64 weighted sums so a crop equals the full array (zeros don't
        # contribute; float32 reduction order was a 1-ULP miss → uint8 ±2).
        shift = float((dl[..., 2].astype(np.float64) * ww).sum() / tot) - float(
            (pl[..., 2].astype(np.float64) * ww).sum() / tot)
        pl[..., 2] = np.clip(pl[..., 2] + shift, 0, 255)
        return cv2.cvtColor(pl.astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)
    except cv2.error:
        return patch


def brighten_and_tone(patch: np.ndarray, dst: np.ndarray, w: np.ndarray,
                      tone: bool = True) -> np.ndarray:
    """Brightness gain then b*, the pair the painter always applies together.

    Lifted out of house-conductor._paste_head on 2026-09-04 so a bench cannot
    draw a different mouth from the live one. A crossfade bench written the
    day before skipped both and painted blue-purple lips that the live paste
    never produces — the picture was wrong about the pipeline, and a wrong
    picture is worse than no picture.
    """
    ww = np.clip(np.asarray(w, dtype=np.float64), 0.0, 1.0)
    tot = float(ww.sum())
    if tot <= 8.0:
        return patch
    a3 = max(tot * 3, 1)
    dstf = np.asarray(dst, dtype=np.float64)
    patchf = np.asarray(patch, dtype=np.float64)
    dm = float((dstf * ww[..., None]).sum() / a3) or 1.0
    pm = float((patchf * ww[..., None]).sum() / a3) or 1.0
    patch = np.clip(np.asarray(patch, dtype=np.float32) * max(0.5, min(2.0, dm / pm)), 0, 255)
    return tone_b(patch, dst, w, on=tone)


def xfade_mix(prev_warped: np.ndarray, prev_a: np.ndarray,
              warped: np.ndarray, a: np.ndarray, t: float
              ) -> tuple[np.ndarray, np.ndarray]:
    """Cross-dissolve two landed tiles (the outgoing and the incoming) at
    blend t in [0, 1], premultiplied so a pixel only one tile covers is not
    darkened by the other's empty border. t=0 is the outgoing tile exactly,
    t=1 the incoming. The conductor's `xfade_ms` dial (2026-09-12) drives
    it on every tile switch — the jump from one viseme sprite to the next
    was a hard cut; nothing in the live path blended across time."""
    t = float(min(1.0, max(0.0, t)))
    if t <= 0.0:
        return prev_warped, prev_a
    if t >= 1.0:
        return warped, a
    a0 = np.asarray(prev_a, dtype=np.float32)
    a1 = np.asarray(a, dtype=np.float32)
    if a0.ndim == 3:
        a0 = a0[..., 0]
    if a1.ndim == 3:
        a1 = a1[..., 0]
    # Only the union of the two footprints is mixed (a full-frame mix
    # measured 10.2 ms at 736×400; the tiles cover ~2% of it).
    rows = np.flatnonzero((a0 > 0).any(axis=1) | (a1 > 0).any(axis=1))
    cols = np.flatnonzero((a0 > 0).any(axis=0) | (a1 > 0).any(axis=0))
    out_w = np.zeros_like(warped)
    out_a = np.zeros(a1.shape, np.float32)
    if rows.size == 0 or cols.size == 0:
        return out_w, out_a
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    w0 = a0[y0:y1, x0:x1] * (1.0 - t)
    w1 = a1[y0:y1, x0:x1] * t
    am = w0 + w1
    den = np.maximum(am, 1e-6)[..., None]
    mixed = (prev_warped[y0:y1, x0:x1].astype(np.float32) * w0[..., None]
             + warped[y0:y1, x0:x1].astype(np.float32) * w1[..., None]) / den
    mixed = np.where(am[..., None] > 1e-6, mixed, 0.0)
    out_w[y0:y1, x0:x1] = np.clip(mixed, 0, 255).astype(warped.dtype)
    out_a[y0:y1, x0:x1] = am
    return out_w, out_a


def band_alpha(warped: np.ndarray, dst: dict, band: np.ndarray,
               soft_px: float = 2.0, nose_keep: bool = True) -> np.ndarray:
    """Alpha for a tile that is then cropped to a band (the hub-rest paste
    is the loop's own frame 0 cropped to the arch band). Feather on the
    FULL footprint first, then the softened band — feathering the cropped
    tile measured 0.27 at the lip centre with edge_s 0.34 (band ~45×50 px
    never reaches the ramp width), a lipless TELEA patch after every word
    (the User 2026-09-13 01:12, "the second B in bob"). 0.98 with this."""
    from head_frame import STAMP_EDGE_S
    a = square_alpha(warped, dst, nose_keep=nose_keep)
    bm = (np.asarray(band) > 0).astype(np.uint8)
    # Feather INSIDE the band: opaque along its spine, 0 at its rim. A hard
    # (2 px) rim read as a U-shaped "shield" popping on the first rest frame
    # (the User 2026-09-13 01:5x). The ramp is the edge dial, capped so the
    # spine always reaches 1 (the band is ~20–35 px thick).
    dist = cv2.distanceTransform(bm, cv2.DIST_L2, 3)
    s = float((dst or {}).get("s") or 0.0)
    # a short rim only: the band is the hole, the body must stay opaque
    ramp = min(max(3.0, 0.4 * STAMP_EDGE_S * s), 5.0) if s > 0 else 4.0
    rim = np.clip(dist / ramp, 0.0, 1.0).astype(np.float32)
    return (a * rim).astype(np.float32)


def xfade_ease(t: float) -> float:
    """Smoothstep: no visible start or stop to the dissolve."""
    t = float(min(1.0, max(0.0, t)))
    return t * t * (3.0 - 2.0 * t)


def composite_stamp(base: np.ndarray, box, warped: np.ndarray, dst: dict,
                    a: np.ndarray | None = None, *, hole: str = "arch",
                    tone: bool = True, nose_keep: bool = True,
                    kw: float = 0.28, kh: float = 0.18, scale: float = 1.0,
                    roi: bool | None = None, cover_alpha: bool = False,
                    hole_within: np.ndarray | None = None
                    ) -> np.ndarray:
    """Arch hole + square (or provided) alpha + tone.

    hole_within (2026-09-29, lips landings on a profile): the hole is erased only inside this mask (her own
    face) and is not grown - the 5 px cover dilation and the plane inpaint filled her filmed lips' front
    with the wall behind a profile, a pale blob in front of her mouth.

    cover_alpha (2026-09-17): the hole is erased only where the provided
    alpha will paint. The jaw window paints less than the landed rectangle,
    and a hole sized by the rectangle left TELEA skin below the window — the
    pale oval on her chin on the close bank. False = before.

    One object for live `_paste_head`, fadebench, phrase_sheet. Desk /
    talking-head / mouthzoom all go through here. Haar `_paste_playlist`
    is the rollback, not a second kit.

    ROI (default): brighten / square_alpha / have / blend run on the
    alpha-live bbox. ``hide_clip_jaw`` stays full-frame (hole is dest
    coords). Kill ``HOUSE_MOUTH_COMPOSITE_ROI=0``.
    """
    from head_frame import dest_nose_keep, fit_arch, inpaint_plane
    use_roi = _composite_roi_on(roi)
    plane = inpaint_plane(dst, kw, kh, scale)
    arch = fit_arch(dst, kw, kh, scale) if hole in ("arch", "strip") else None
    H, W = warped.shape[:2]
    if hole == "strip" and a is not None:
        a0 = a[..., 0] if getattr(a, "ndim", 2) == 3 else a
        if use_roi:
            rb = live_bbox(a0 > 0, pad=2)
            have = np.zeros((H, W), dtype=bool)
            if rb is not None:
                x0, y0, x1, y1 = rb
                sub = a0[y0:y1, x0:x1] > 0.45
                if nose_keep:
                    yy = np.arange(y0, y1, dtype=np.float32)[:, None]
                    xx = np.arange(x0, x1, dtype=np.float32)[None, :]
                    sub = sub & dest_nose_keep(xx, yy, dst)
                have[y0:y1, x0:x1] = sub
        else:
            have = a0 > 0.45
            if nose_keep:
                yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
                have = have & dest_nose_keep(xx, yy, dst)
        dilate, jaw_dst = 0, dst
    else:
        if a is None:
            a = square_alpha(warped, dst, plane=plane, arch=arch,
                             nose_keep=nose_keep, roi=use_roi)
        if use_roi:
            rb = live_bbox(_fr.any_channel(warped) if warped.ndim == 3 else warped > 0, pad=2)
            have = np.zeros((H, W), dtype=bool)
            if rb is not None:
                x0, y0, x1, y1 = rb
                have[y0:y1, x0:x1] = _fr.sum_gt_f32(warped[y0:y1, x0:x1], 12.0)
        else:
            have = _fr.sum_gt_f32(warped, 12.0)
        if cover_alpha:
            have = have & ((a[..., 0] if a.ndim == 3 else a) > 0.45)
        dilate, jaw_dst = 5, None
        if hole_within is not None:
            have = have & (np.asarray(hole_within) > 127)
            dilate = 0
    out = hide_clip_jaw(base, box, plane=plane,
                        hole=("arch" if arch is not None else "ellipse"),
                        arch=arch, cover=have, dst=jaw_dst,
                        cover_dilate=dilate)
    if a.ndim == 2:
        a = a[..., None]
    w = a[..., 0]
    if use_roi:
        rb = live_bbox(w > 0, pad=0)
        if rb is None:
            return out
        x0, y0, x1, y1 = rb
        if _sf.ON:
            done = _sf.brighten_tone_blend(warped[y0:y1, x0:x1], out[y0:y1, x0:x1], w[y0:y1, x0:x1],
                                           a[y0:y1, x0:x1], tone)
            if done is not None:
                out[y0:y1, x0:x1] = done
                return out
        patch = warped[y0:y1, x0:x1].astype(np.float32)
        dstp = out[y0:y1, x0:x1].astype(np.float32)
        ww = w[y0:y1, x0:x1]
        aa = a[y0:y1, x0:x1]
        patch = brighten_and_tone(patch, dstp, ww, tone=tone)
        out[y0:y1, x0:x1] = (dstp * (1 - aa) + patch * aa).astype(np.uint8)
        return out
    patch = warped.astype(np.float32)
    dstp = out.astype(np.float32)
    patch = brighten_and_tone(patch, dstp, w, tone=tone)
    return (dstp * (1 - a) + patch * a).astype(np.uint8)

