"""Grid-cell mouth pins keyed by turn angle, and lip-landmark placement (the User 2026-09-29).

PINS. "Save per-tile mouth pins ... as overrides of the library default ... a table keyed by turn angle,
not by frame" + "per cell, by angle": `<pose-dir>/mouth-pins.json`

    {"schema": "mouth-pins/1", "cells": {"<library>/<station>/<class>": {"rows": [
        {"yaw": -30.0, "u": 2.0, "v": -3.0, "at": "...", "by": "..."}, ...]}}}

u/v are the nose_u/nose_v units (dest pixels along her own head axes, +u her left, +v down, at the
s = 357 reference - the same scale `_nudge_px` uses). A cell's pin at a frame's yaw is the rows linearly
interpolated by yaw and held flat past the end rows; a cell with no rows is the library default. A row
saved within HALF_DEG of an existing one replaces it.

LIPS. "when the 45d flag triggers the nose pin switches to mouth": a similarity (scale, rotation,
translation) from the tile's own lip marks - outer corners and the lip line (the midpoint of the inner
lip midpoints) - onto the dest frame's, least squares over the three points. Marks come from
`<stem>.lipmarks.json` (scripts/pose-lip-marks.py), in the film's native pixels; a tile's marks are its
source frame's minus `crop_xy`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

PINS_NAME = "mouth-pins.json"
HALF_DEG = 0.5
NUDGE_REF_S = 357.0
# A PIN BELONGS TO ITS ANGLES (the User 2026-10-05, "let's fix for good"): held flat past the end rows, one row saved
# at one yaw shifted that shape at EVERY angle (05:20 clicks at 7.36 deg: WOO -5 ... None +5 on every work/desk
# frame, the wall's too). HOUSE_MOUTH_PIN_SPAN=N (deg): past the outermost rows a pin fades linearly to nothing
# over N degrees, so a lone row is a local fix. 0 (default) = held flat, today's rule byte for byte.
try:
    PIN_SPAN = max(0.0, float(os.environ.get("HOUSE_MOUTH_PIN_SPAN", "0") or 0))
except ValueError:
    PIN_SPAN = 0.0


def cell_key(library: str, station: str, klass: str) -> str:
    return "%s/%s/%s" % (library, station, klass)


def load(pose_dir: Path) -> dict:
    try:
        d = json.loads((Path(pose_dir) / PINS_NAME).read_text())
    except (OSError, ValueError):
        return {"schema": "mouth-pins/1", "cells": {}}
    if not isinstance(d.get("cells"), dict):
        d["cells"] = {}
    return d


def _write(pose_dir: Path, d: dict) -> None:
    p = Path(pose_dir) / PINS_NAME
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1) + "\n")
    os.replace(tmp, p)


def save_row(pose_dir: Path, cell: str, yaw: float, u: float, v: float, by: str = "") -> dict:
    d = load(pose_dir)
    rows = [r for r in (d["cells"].get(cell) or {}).get("rows") or []
            if abs(float(r.get("yaw", 1e9)) - float(yaw)) > HALF_DEG]
    rows.append({"yaw": round(float(yaw), 2), "u": round(float(u), 2), "v": round(float(v), 2),
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": str(by or "")})
    rows.sort(key=lambda r: float(r["yaw"]))
    d["schema"] = "mouth-pins/1"
    d["cells"][cell] = {"rows": rows}
    _write(pose_dir, d)
    return d["cells"][cell]


def drop_row(pose_dir: Path, cell: str, yaw: float | None = None) -> dict:
    """Remove the row nearest `yaw` (within HALF_DEG), or the whole cell when yaw is None."""
    d = load(pose_dir)
    if yaw is None:
        d["cells"].pop(cell, None)
    else:
        rows = [r for r in (d["cells"].get(cell) or {}).get("rows") or []
                if abs(float(r.get("yaw", 1e9)) - float(yaw)) > HALF_DEG]
        if rows:
            d["cells"][cell] = {"rows": rows}
        else:
            d["cells"].pop(cell, None)
    _write(pose_dir, d)
    return d["cells"].get(cell) or {"rows": []}


def pin_at(rows: list | None, yaw: float | None, span: float | None = None) -> tuple[float, float] | None:
    """(u, v) for this yaw from a cell's rows, or None (no rows / no yaw = the library default). With a span
    (PIN_SPAN, or the argument), past the end rows the pin fades to (0, 0) over `span` degrees."""
    if not rows or yaw is None:
        return None
    sp = PIN_SPAN if span is None else max(0.0, float(span))
    rs = sorted(((float(r["yaw"]), float(r["u"]), float(r["v"])) for r in rows), key=lambda r: r[0])
    y = float(yaw)
    if y <= rs[0][0] or y >= rs[-1][0]:
        end = rs[0] if y <= rs[0][0] else rs[-1]
        if sp <= 0.0:
            return end[1], end[2]
        k = max(0.0, 1.0 - abs(y - end[0]) / sp)
        return end[1] * k, end[2] * k
    for (y0, u0, v0), (y1, u1, v1) in zip(rs, rs[1:]):
        if y0 <= y <= y1:
            k = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)
            return u0 + k * (u1 - u0), v0 + k * (v1 - v0)
    return rs[-1][1], rs[-1][2]


def nudge_px(u: float, v: float, uhat, vhat, s: float) -> np.ndarray:
    """Dest-pixel shift for a (u, v) pin: along her unit head axes, scaled by s / 357."""
    uh = np.asarray(uhat, dtype=np.float64).ravel()[:2]
    vh = np.asarray(vhat, dtype=np.float64).ravel()[:2]
    uh = uh / max(1e-9, float(np.linalg.norm(uh)))
    vh = vh / max(1e-9, float(np.linalg.norm(vh)))
    return (float(u) * uh + float(v) * vh) * (float(s) / NUDGE_REF_S)


# ---- lip landmarks ---------------------------------------------------------------------------------

def lip_points(marks: dict | None, scale: float = 1.0, origin=(0.0, 0.0)) -> np.ndarray | None:
    """[corner image-left, corner image-right, lip-line centre] as a 3x2 array, (p - origin) * scale."""
    if not marks:
        return None
    try:
        cl = np.asarray(marks["cl"], dtype=np.float64)
        cr = np.asarray(marks["cr"], dtype=np.float64)
        mid = (np.asarray(marks["up"], dtype=np.float64) + np.asarray(marks["lo"], dtype=np.float64)) / 2.0
    except (KeyError, TypeError, ValueError):
        return None
    o = np.asarray(origin, dtype=np.float64)
    return (np.vstack([cl, cr, mid]) - o) * float(scale)


def anchor_points(marks: dict | None, scale: float = 1.0, origin=(0.0, 0.0)) -> np.ndarray | None:
    """[corner, corner, lip-line centre, nose tip, chin] (lipmarks/5), else the three lip points."""
    P = lip_points(marks, scale, origin)
    if P is None:
        return None
    try:
        extra = np.vstack([np.asarray(marks["nose"], dtype=np.float64), np.asarray(marks["chin"], dtype=np.float64)])
    except (KeyError, TypeError, ValueError):
        return P
    return np.vstack([P, (extra - np.asarray(origin, dtype=np.float64)) * float(scale)])


def pin_to(M: np.ndarray, src_pt, dst_pt) -> np.ndarray:
    """Translate the landing so src_pt lands exactly on dst_pt (rotation and scale kept). THE MOUTH IS PINNED TO
    THE MOUTH (the User 2026-09-30, Crissa heads down: "too high and so shortening her face" - the five-point fit
    split the difference between her squashed nose-lip-chin and the tile's, lifting the lips toward her nose)."""
    M = np.asarray(M, dtype=np.float64).copy()
    M[:, 2] += np.asarray(dst_pt, dtype=np.float64).ravel()[:2] - apply(M, src_pt)[0]
    return M


def jaw_stretch(M: np.ndarray, src_pts: np.ndarray, dst_pts: np.ndarray, lo: float = 0.85, hi: float = 1.2) -> np.ndarray:
    """THE JAW AXIS (the User 2026-09-29: "is there a 3rd axis ... jaw falls behind"): stretch the landed tile
    along HER nose->chin direction about her lip line, by her lip-to-chin distance over the landed tile's
    (clamped lo..hi). Width is left alone, so the head-scale size (the AH fix) holds. Needs 5 anchor points."""
    if src_pts is None or dst_pts is None or len(src_pts) < 5 or len(dst_pts) < 5:
        return M
    landed = apply(M, src_pts)
    c = dst_pts[2]
    v = dst_pts[4] - dst_pts[3]
    nv = float(np.linalg.norm(v))
    if nv < 1e-6:
        return M
    v = v / nv
    her = float(np.dot(dst_pts[4] - c, v))
    tile = float(np.dot(landed[4] - landed[2], v))
    if abs(tile) < 1e-6 or her <= 0:
        return M
    k = float(np.clip(her / tile, lo, hi))
    A = np.eye(2) + (k - 1.0) * np.outer(v, v)
    L = np.asarray(M, dtype=np.float64)
    lin = A @ L[:, :2]
    t = A @ (L[:, 2] - c) + c
    return np.hstack([lin, t[:, None]])


def similarity(src: np.ndarray, dst: np.ndarray, scale: float | None = None) -> np.ndarray | None:
    """2x3 similarity (Umeyama, no reflection) taking src points onto dst, least squares. With `scale`
    the size is fixed and only rotation + translation are fitted (the lips landing takes its size from
    the head: an open AH pulls the corners in, and a 45 tile on a shallower turn has them closer than
    hers, so a corner-fitted scale inflated the AH - the User 2026-09-29)."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if src.shape != dst.shape or src.shape[0] < 2:
        return None
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    va = float((a ** 2).sum())
    if va < 1e-9:
        return None
    C = b.T @ a / src.shape[0]
    U, S, Vt = np.linalg.svd(C)
    D = np.diag([1.0, np.sign(np.linalg.det(U @ Vt)) or 1.0])
    R = U @ D @ Vt
    k = float(scale) if scale else float(np.trace(np.diag(S) @ D)) / (va / src.shape[0])
    t = md - k * (R @ ms)
    return np.hstack([k * R, t[:, None]])


def carry(was, now, warped, a):
    """The picture that stood on film frame `was`, moved to where her head is on frame `now` (2026-10-02): the
    similarity between her own points on the two frames. `was` / `now` are (kind, clip, frame, Nx2). Unchanged
    when it is the same frame, another clip, another kind of points, or more than three frames apart (a cut
    is not a move). The outgoing half of a cross-dissolve is carried by this, so it does not trail a turn."""
    if was is None or now is None or was[:2] != now[:2] or was[2] == now[2] or abs(int(was[2]) - int(now[2])) > 3:
        return warped, a
    T = similarity(was[3], now[3])
    if T is None or warped is None:
        return warped, a
    import cv2
    T = np.asarray(T, dtype=np.float32)
    h, w = warped.shape[:2]
    out_w = cv2.warpAffine(warped, T, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    out_a = a
    if isinstance(a, np.ndarray):
        out_a = cv2.warpAffine(a, T, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        if out_a.ndim != a.ndim:
            out_a = out_a.reshape(a.shape)
    return out_w, out_a


def apply(M: np.ndarray, p) -> np.ndarray:
    p = np.asarray(p, dtype=np.float64).reshape(-1, 2)
    return p @ np.asarray(M)[:, :2].T + np.asarray(M)[:, 2]


def hull_points(marks: dict | None, scale: float = 1.0, origin=(0.0, 0.0)) -> np.ndarray | None:
    """Her face outline as an Nx2 array, (p - origin) * scale: lipmarks/3 `outline` (the mesh filled,
    concave), else the lipmarks/2 convex `hull`."""
    h = (marks or {}).get("outline") or (marks or {}).get("hull")
    if not h or len(h) < 3:
        return None
    return (np.asarray(h, dtype=np.float64) - np.asarray(origin, dtype=np.float64)) * float(scale)


def face_polys(marks: dict | None, scale: float = 1.0, origin=(0.0, 0.0)) -> list:
    """Her face as polygons to fill together: the outline (else the hull) and the outer lip loop."""
    out = []
    P = hull_points(marks, scale, origin)
    if P is not None:
        out.append(P)
    L = (marks or {}).get("lips_outer")
    if L and len(L) >= 3:
        out.append((np.asarray(L, dtype=np.float64) - np.asarray(origin, dtype=np.float64)) * float(scale))
    return out


def fill(shape_hw, polys) -> np.ndarray | None:
    """uint8 0/255 mask of the union of polygons (a single Nx2 array counts as one)."""
    import cv2
    if polys is None:
        return None
    if isinstance(polys, np.ndarray):
        polys = [polys]
    polys = [q for q in polys if q is not None and len(q) >= 3]
    if not polys:
        return None
    a = np.zeros((int(shape_hw[0]), int(shape_hw[1])), np.uint8)
    for q in polys:
        cv2.fillPoly(a, [np.round(np.asarray(q)).astype(np.int32).reshape(-1, 1, 2)], 255)
    return a


def match_skin(warped: np.ndarray, base: np.ndarray, alpha: np.ndarray, lips: np.ndarray | None,
               sigma_px: float = 8.0, lo: float = 0.7, hi: float = 1.4) -> np.ndarray:
    """HER shading carried onto the landed tile (the User 2026-09-30, bun-wobble: a desk tile lit evenly on a
    heads-down, shadowed chin read as a lighter patch floating on her face - on the mean it was 6 % DARKER, so no
    single gain fixes it). A smooth per-pixel, per-channel ratio of her skin to the tile's skin, each a
    normalised blur over the painted skin (alpha > 0.3, outside her lip loop), clamped lo..hi, multiplied into
    the tile. The tile keeps its own detail and lips; the light across them becomes hers."""
    import cv2
    sel = (np.asarray(alpha) > 0.3)
    if lips is not None:
        sel &= np.asarray(lips) < 128
    if sel.sum() < 30:
        return warped
    m = sel.astype(np.float32)
    sg = max(1.0, float(sigma_px))
    den = cv2.GaussianBlur(m, (0, 0), sg) + 1e-3
    out = warped.astype(np.float32)
    for c in range(3):
        bn = cv2.GaussianBlur(base[..., c].astype(np.float32) * m, (0, 0), sg) / den
        wn = cv2.GaussianBlur(out[..., c] * m, (0, 0), sg) / den
        r = np.where(wn > 8.0, bn / np.maximum(wn, 1.0), 1.0)
        out[..., c] *= np.clip(r, lo, hi)
    return np.clip(out, 0, 255).astype(np.uint8)


def match_skin_roi(warped: np.ndarray, base: np.ndarray, alpha: np.ndarray, lips: np.ndarray | None,
                   sigma_px: float = 8.0, lo: float = 0.7, hi: float = 1.4) -> np.ndarray:
    """match_skin on the painted box grown by 3 sigma, not the whole frame (2026-10-16: 16 ms full frame at 736x400,
    the pitch fold's every paint). Outside the box the tile is returned untouched (alpha 0 there paints nothing)."""
    ys, xs = np.nonzero(np.asarray(alpha) > 0)
    if not len(ys):
        return warped
    g = int(np.ceil(3.0 * max(1.0, float(sigma_px))))
    H, W = warped.shape[:2]
    y0, y1 = max(0, int(ys.min()) - g), min(H, int(ys.max()) + 1 + g)
    x0, x1 = max(0, int(xs.min()) - g), min(W, int(xs.max()) + 1 + g)
    out = np.array(warped, copy=True)
    sub = match_skin(warped[y0:y1, x0:x1], base[y0:y1, x0:x1], np.asarray(alpha)[y0:y1, x0:x1],
                     None if lips is None else np.asarray(lips)[y0:y1, x0:x1], sigma_px, lo, hi)
    out[y0:y1, x0:x1] = sub
    return out


def mouth_region(shape_hw, lips_poly, grow_px: float) -> np.ndarray | None:
    """uint8 0/255: her outer lip loop grown by grow_px - the only place a lips landing paints when it is
    limited to the mouth (the User 2026-09-29, bun-wobble: the tile's own jaw and neck showed as a grey haze
    that did not follow hers - "the jaw has momentum"). Her filmed jaw and chin stay hers."""
    import cv2
    a = fill(shape_hw, lips_poly)
    if a is None:
        return None
    g = int(round(max(0.0, float(grow_px))))
    if g:
        a = cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1)))
    return a


def face_mask(shape_hw, tile_hull_on_dest, dest_hull, feather_px: float, grow_px: float = 0.0,
              region: np.ndarray | None = None) -> np.ndarray | None:
    """Alpha 0..1 over the frame: inside BOTH the tile's face outline (landed on the dest) and her own,
    grown by `grow_px` then feathered by `feather_px`. The tile's background and anything past her edge go
    transparent. Grown, not eroded: on a deep profile the mesh hull runs 2-4 px inside her real nose tip
    and lips (measured on the 90 takes, 2026-09-29), so cutting on it clips the front of her mouth."""
    import cv2
    # HER outline grows (the mesh runs inside her real lips on a profile); the TILE's outline shrinks by the
    # feather, never grows - grown, it painted the tile's own dark top past its jaw, toned grey-blue (Crissa's
    # bun-wobble, 2026-09-30)
    f = max(0.5, float(feather_px))
    g = int(round(max(0.0, float(grow_px))))
    e = int(round(f))
    m = None
    for polys, grow in ((tile_hull_on_dest, -e), (dest_hull, g)):
        a = fill(shape_hw, polys)
        if a is None:
            continue
        if grow > 0:
            a = cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
        elif grow < 0:
            a = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * -grow + 1, 2 * -grow + 1)))
        m = a if m is None else np.minimum(m, a)
    if m is None:
        return None
    if region is not None:
        m = np.minimum(m, region)
    m = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), f)
    return m


# THE TRIANGLE LANDING ON A SIDE TURN (the User 2026-10-02): the regular Bezier cheek warp is kept for front shots; on a
# side turn the tile is landed by three of her own points - nose tip, chin, and a near-cheek point read off her face
# outline - found the same way on the tile's source frame and on the frame being painted. Nothing here is fitted to a face.
def cheek_point(nose, chin, outline, side: int | None = None) -> tuple:
    """(point, side): where the line through the middle of nose->chin, square to it, leaves her face outline. `side` is
    +1 / -1 along that line; None takes the side with more face on it (the near cheek). (None, None) when the outline
    cannot answer."""
    try:
        N = np.asarray(nose, dtype=np.float64).ravel()[:2]
        C = np.asarray(chin, dtype=np.float64).ravel()[:2]
        P = np.asarray(outline, dtype=np.float64).reshape(-1, 2)
    except (TypeError, ValueError):
        return None, None
    v = C - N
    n = float(np.linalg.norm(v))
    if n < 1e-6 or len(P) < 3:
        return None, None
    v = v / n
    u = np.array([-v[1], v[0]])
    mid = (N + C) / 2.0
    A, B = P, np.roll(P, -1, axis=0)
    e = B - A
    den = u[0] * e[:, 1] - u[1] * e[:, 0]
    w = A - mid
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
        r = (w[:, 0] * u[1] - w[:, 1] * u[0]) / den
    ok = (np.abs(den) > 1e-9) & (r >= 0.0) & (r <= 1.0)
    if not ok.any():
        return None, None
    t = t[ok]
    far = {1: float(t.max()) if (t > 0).any() else 0.0, -1: float(-t.min()) if (t < 0).any() else 0.0}
    if side not in (1, -1):
        side = 1 if far[1] >= far[-1] else -1
    if far[side] <= 1e-6:
        return None, None
    return mid + side * far[side] * u, side


def tri_affine(src3, dst3) -> np.ndarray | None:
    """The 2x3 affine taking three tile points exactly onto three of hers; None when either triangle is flat."""
    S = np.asarray(src3, dtype=np.float64).reshape(3, 2)
    D = np.asarray(dst3, dtype=np.float64).reshape(3, 2)
    def area(T):
        return abs(float((T[1, 0] - T[0, 0]) * (T[2, 1] - T[0, 1]) - (T[1, 1] - T[0, 1]) * (T[2, 0] - T[0, 0]))) / 2.0
    if area(S) < 1.0 or area(D) < 1.0:
        return None
    A = np.hstack([S, np.ones((3, 1))])
    try:
        X = np.linalg.solve(A, D)             # rows: x, y, 1 -> dest
    except np.linalg.LinAlgError:
        return None
    return X.T.copy()


def turn_weight(yaw, start: float, blend: float) -> float:
    """0 = the front landing, 1 = the triangle; a straight ramp over the `blend` degrees that END at `start`."""
    try:
        a = abs(float(yaw))
    except (TypeError, ValueError):
        return 0.0
    if blend is None or float(blend) <= 0:
        return 1.0 if a >= float(start) else 0.0
    return float(np.clip((a - (float(start) - float(blend))) / float(blend), 0.0, 1.0))


def cover_mask(shape_hw, tile_polys_on_dest, her_polys, grow_px: float, feather_px: float) -> np.ndarray | None:
    """Alpha 0..1: inside the tile's own face outline (landed) AND hers, both grown by `grow_px` - the margin past
    the mesh's inward run on a profile - then feathered. The tile's room and anything past her face stay out; her
    whole mouth is inside."""
    import cv2
    g = int(round(max(0.0, float(grow_px))))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1)) if g else None
    m = None
    for polys in (tile_polys_on_dest, her_polys):
        a = fill(shape_hw, polys)
        if a is None:
            return None
        if k is not None:
            a = cv2.dilate(a, k)
        m = a if m is None else np.minimum(m, a)
    return cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), max(0.5, float(feather_px)))


# THE REAL PROFILE EDGE (the User 2026-10-02, far turns: "shoot the jaw out forward just a few pixels ... sunken jaw").
# On a deep profile the tracked nose tip and chin sit INSIDE her real edge, and by more on a small filmed face than
# on a tile's close take (measured: nose 0.087 s vs 0.039 s, chin 0.039 vs 0.024), so a landing of tracked point on
# tracked point leaves the tile's mouth and jaw that much too far back. Where a tracked point lies on her face
# outline's front, the skin-to-room edge in front of it is read off the picture itself and the triangle uses that.
def front_dir(nose, chin, face_pts) -> np.ndarray | None:
    """Unit vector across nose->chin pointing to the FRONT of her face (the side her outline is not on)."""
    N = np.asarray(nose, dtype=np.float64).ravel()[:2]
    C = np.asarray(chin, dtype=np.float64).ravel()[:2]
    v = C - N
    n = float(np.linalg.norm(v))
    if n < 1e-6:
        return None
    v = v / n
    u = np.array([-v[1], v[0]])
    F = np.asarray(face_pts, dtype=np.float64).reshape(-1, 2)
    if len(F) and float(((F - (N + C) / 2.0) @ u).mean()) > 0:
        u = -u
    return u


def front_weight(pt, u_front, outline, tol_px: float) -> float:
    """1 when the tracked point is on the front of her outline, falling straight to 0 as the face in front of it
    grows to 2 x tol_px: how much of a profile-edge reading applies to this point on this frame."""
    p = np.asarray(pt, dtype=np.float64).ravel()[:2]
    P = np.asarray(outline, dtype=np.float64).reshape(-1, 2)
    A, B = P, np.roll(P, -1, axis=0)
    e = B - A
    den = u_front[0] * e[:, 1] - u_front[1] * e[:, 0]
    w = A - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
        r = (w[:, 0] * u_front[1] - w[:, 1] * u_front[0]) / den
    ok = (np.abs(den) > 1e-9) & (r >= 0.0) & (r <= 1.0) & (t > 0)
    if not ok.any():
        return 1.0
    return float(np.clip(1.0 - float(t[ok].max()) / max(1e-6, 2.0 * float(tol_px)), 0.0, 1.0))


def on_front_edge(pt, u_front, outline, tol_px: float) -> bool:
    """Is this tracked point on the front of her outline - no more than tol_px of face in front of it?"""
    p = np.asarray(pt, dtype=np.float64).ravel()[:2]
    P = np.asarray(outline, dtype=np.float64).reshape(-1, 2)
    A, B = P, np.roll(P, -1, axis=0)
    e = B - A
    den = u_front[0] * e[:, 1] - u_front[1] * e[:, 0]
    w = A - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
        r = (w[:, 0] * u_front[1] - w[:, 1] * u_front[0]) / den
    ok = (np.abs(den) > 1e-9) & (r >= 0.0) & (r <= 1.0) & (t > 0)
    return (not ok.any()) or float(t[ok].max()) <= float(tol_px)


def edge_ahead(img, pt, u_front, s: float, back: float = 0.04, ahead: float = 0.12) -> float:
    """How far in front of a tracked point (px, never negative) the strongest colour edge sits, looking from
    `back` x s behind it to `ahead` x s in front, on a picture smoothed over 0.008 s. The search window is in head
    sizes, so it holds for any face or zoom."""
    import cv2
    p = np.asarray(pt, dtype=np.float64).ravel()[:2]
    r = int(np.ceil((ahead + 0.02) * s)) + 3
    x0, y0 = int(max(0, p[0] - r)), int(max(0, p[1] - r))
    roi = img[y0:int(p[1] + r) + 1, x0:int(p[0] + r) + 1]
    if roi.size == 0:
        return 0.0
    lab = cv2.GaussianBlur(cv2.cvtColor(np.ascontiguousarray(roi[..., :3]), cv2.COLOR_BGR2LAB).astype(np.float32),
                           (0, 0), max(0.8, 0.008 * s))
    ts = np.arange(-back * s, ahead * s, 0.5)
    xs = np.clip(p[0] - x0 + ts * u_front[0], 0, roi.shape[1] - 1).astype(np.float32)
    ys = np.clip(p[1] - y0 + ts * u_front[1], 0, roi.shape[0] - 1).astype(np.float32)
    prof = cv2.remap(lab, xs[None, :], ys[None, :], cv2.INTER_LINEAR)[0]
    g = np.linalg.norm(np.diff(prof, axis=0), axis=1)
    if not len(g) or float(g.max()) <= 0:
        return 0.0
    return max(0.0, float(ts[int(np.argmax(g))] + 0.25))


def lip_size_maps(shape_hw, M: np.ndarray, anchor, v, k_along: float, k_across: float, lips_mask: np.ndarray,
                  reach_px: float) -> tuple:
    """Remap maps (x, y) that draw the tile as M lands it, except that the MOUTH is drawn k_along x k_across its
    size about `anchor` (her lip front): full strength over `lips_mask` (her lip area, uint8), fading smoothly to
    none at `reach_px` from it. The lips change size; the jaw, the cheek and everything the patch covers stay
    where the landing put them (the User 2026-10-02: "make the lips smaller but the circle the same size")."""
    import cv2
    H, W = int(shape_hw[0]), int(shape_hw[1])
    v = np.asarray(v, dtype=np.float64)
    u = np.array([-v[1], v[0]])
    a = np.asarray(anchor, dtype=np.float64).ravel()[:2]
    d = cv2.distanceTransform((lips_mask == 0).astype(np.uint8), cv2.DIST_L2, 5)
    w = np.clip(1.0 - d / max(1.0, float(reach_px)), 0.0, 1.0)
    w = w * w * (3.0 - 2.0 * w)                                  # smooth at both ends
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    px, py = xx - a[0], yy - a[1]
    al = px * v[0] + py * v[1]                                   # along nose->chin, across it
    ac = px * u[0] + py * u[1]
    al2 = al * (1.0 + w * (1.0 / max(1e-6, float(k_along)) - 1.0))
    ac2 = ac * (1.0 + w * (1.0 / max(1e-6, float(k_across)) - 1.0))
    qx = a[0] + al2 * v[0] + ac2 * u[0]
    qy = a[1] + al2 * v[1] + ac2 * u[1]
    L = np.vstack([np.asarray(M, dtype=np.float64), [0.0, 0.0, 1.0]])
    Li = np.linalg.inv(L)
    mx = (Li[0, 0] * qx + Li[0, 1] * qy + Li[0, 2]).astype(np.float32)
    my = (Li[1, 0] * qx + Li[1, 1] * qy + Li[1, 2]).astype(np.float32)
    return mx, my


# THE BEND (the User 2026-10-02: "bend the plane here to mimic a wrapped mask ... an odd deg 3 or 5th deg polynomial
# for the arch, non sym"). Her mouth lies on the dental arch, not on a flat card. A tile filmed at one turn and
# painted at another is the same arch seen from two angles, and that is not a stretch: measured across from the
# lip centre, toward the front of her face, a point of the arch at frontal offset x and arch depth d(x) sits at
#     x cos(yaw) - d(x) sin(yaw)
# so the half toward the camera barely changes and the far half folds away. d(x) is the house's own arch
# (head_frame.dental_arch6) on the face's own cheek half-chord. Everything is in head sizes; nothing is fitted.
def arch_across(x0, yaw_deg: float, half_chord: float):
    """Across offset from the lip centre (toward the front positive), head sizes, of arch points at frontal offset x0."""
    from head_frame import dental_arch6
    x0 = np.asarray(x0, dtype=np.float64)
    hc = float(half_chord)
    d = hc * dental_arch6(np.clip(x0 / hc, -1.0, 1.0))
    t = np.radians(abs(float(yaw_deg)))
    return x0 * np.cos(t) - d * np.sin(t)


def arch_reproject(yaw_her: float, yaw_tile: float, half_chord: float, tol: float, degrees=(3, 5)) -> dict | None:
    """The across re-projection from HER angle to the TILE's, as a polynomial with no constant term (the lip centre
    lands on the lip centre) fitted to the exact arch over the part of it in view at her angle: degree 3, and 5 only
    where the cubic's worst error exceeds `tol` (head sizes - half a painter pixel is the caller's choice). Returns
    {"c": coefficients for powers 1..n, "lo", "hi": the range in view, "deg", "err"}."""
    hc = float(half_chord)
    if hc <= 0:
        return None
    x0 = np.linspace(-hc, hc, 2001)
    a = arch_across(x0, yaw_her, hc)
    b = arch_across(x0, yaw_tile, hc)
    g = np.gradient(a)
    i0 = len(x0) // 2
    lo = i0
    while lo > 0 and g[lo - 1] > 0:
        lo -= 1
    hi = i0
    while hi < len(x0) - 1 and g[hi + 1] > 0:
        hi += 1
    if hi - lo < 8:
        return None
    aa, bb = a[lo:hi + 1], b[lo:hi + 1]
    out = None
    for deg in degrees:
        A = np.stack([aa ** n for n in range(1, int(deg) + 1)], axis=1)
        c, *_ = np.linalg.lstsq(A, bb, rcond=None)
        err = float(np.abs(A @ c - bb).max())
        out = {"c": c, "lo": float(aa[0]), "hi": float(aa[-1]), "deg": int(deg), "err": err}
        if err <= float(tol):
            break
    return out


def arch_eval(rp: dict, x):
    """The re-projection at across offsets x (head sizes); past the range in view it runs on straight at the slope
    it had at that edge, so a far cheek never folds back over the mouth."""
    c = np.asarray(rp["c"], dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    xc = np.clip(x, rp["lo"], rp["hi"])
    y = np.zeros_like(xc)
    dy = np.zeros_like(xc)
    for n, cn in enumerate(c, start=1):
        y = y + cn * xc ** n
        dy = dy + n * cn * xc ** (n - 1)
    return y + dy * (x - xc)


def bend_maps(shape_hw, rp: dict, anchor_her, v_her, front_her, s_her: float, anchor_tile, v_tile, front_tile,
              s_tile: float, k_along: float = 1.0, k_across: float = 1.0, lips_mask=None, reach_px: float = 0.0) -> tuple:
    """Remap maps (x, y), dest -> tile: along her nose->chin line the tile is drawn at the head-size ratio; across
    it by the arch re-projection `rp`; both about the lip anchors. k_along / k_across resize the MOUTH only, as
    lip_size_maps does (full over `lips_mask`, fading out by `reach_px`)."""
    import cv2
    H, W = int(shape_hw[0]), int(shape_hw[1])
    a = np.asarray(anchor_her, dtype=np.float64).ravel()[:2]
    vh = np.asarray(v_her, dtype=np.float64)
    fh = np.asarray(front_her, dtype=np.float64)
    at = np.asarray(anchor_tile, dtype=np.float64).ravel()[:2]
    vt = np.asarray(v_tile, dtype=np.float64)
    ft = np.asarray(front_tile, dtype=np.float64)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    px, py = xx - a[0], yy - a[1]
    al = px * vh[0] + py * vh[1]
    ac = px * fh[0] + py * fh[1]
    if lips_mask is not None and (float(k_along) != 1.0 or float(k_across) != 1.0):
        d = cv2.distanceTransform((np.asarray(lips_mask) == 0).astype(np.uint8), cv2.DIST_L2, 5)
        w = np.clip(1.0 - d / max(1.0, float(reach_px)), 0.0, 1.0)
        w = w * w * (3.0 - 2.0 * w)
        al = al * (1.0 + w * (1.0 / max(1e-6, float(k_along)) - 1.0))
        ac = ac * (1.0 + w * (1.0 / max(1e-6, float(k_across)) - 1.0))
    al_t = al * (float(s_tile) / float(s_her))
    ac_t = arch_eval(rp, ac / float(s_her)) * float(s_tile)
    mx = (at[0] + al_t * vt[0] + ac_t * ft[0]).astype(np.float32)
    my = (at[1] + al_t * vt[1] + ac_t * ft[1]).astype(np.float32)
    return mx, my


def bend_affine(rp: dict, anchor_her, v_her, front_her, s_her: float, anchor_tile, v_tile, front_tile, s_tile: float) -> np.ndarray:
    """The 2x3 affine the bend equals at the lip anchor (tile -> dest): what places the tile's outline, its jaw cut
    and the like, which need one landing and not a map."""
    ks = float(s_her) / float(s_tile)
    c1 = float(np.asarray(rp["c"])[0])
    vh, fh = np.asarray(v_her, dtype=np.float64), np.asarray(front_her, dtype=np.float64)
    vt, ft = np.asarray(v_tile, dtype=np.float64), np.asarray(front_tile, dtype=np.float64)
    lin = ks * np.outer(vh, vt) + (ks / max(1e-6, c1)) * np.outer(fh, ft)
    t = np.asarray(anchor_her, dtype=np.float64).ravel()[:2] - lin @ np.asarray(anchor_tile, dtype=np.float64).ravel()[:2]
    return np.hstack([lin, t[:, None]])
