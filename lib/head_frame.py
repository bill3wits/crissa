"""Rigid head frame — the User's ray, plus the two axes that come with it.

A landmark model's own pose output keeps only Euler angles; this solves the full
rigid frame (s, R, t) from the 68 landmarks itself and keeps it. The solve needs
no face-model package at runtime (see the credit above estimate_affine_matrix_3d23d).

Formulas here. Measurements in avatars/<name>/manifest.json.

    RIGID = upper face only (brows, nose bridge, eyes). Jaw and mouth
    are excluded so a dropped jaw cannot inflate the ruler s.

    ez = R[:,2]  the ray: back-of-head centre -> nose tip
    u, v         image-space projections of the across/down face axes
    A            nose-base <-> chin midpoint (DEBUG only — not the paste origin)

The paste latches on the NOSE. Axes are LOCKED: length s from the rigid
fit, not the jaw. Six endpoints in 3-D, origin at the nose:

    nose ± u     ex  (across the face)
    nose ± v     ey  (down the face)
    nose ± w     ez  (the ray), w = s * R[0:2, 2]

Live warp is a CURVED cheek–nose–cheek strip: quadratic Bézier through
left zygoma, nose, right zygoma (2d106 16/32), extruded down the face
along v. A smile / little twitch-up lifts the cheeks and the tile follows.
Chin and mouth edges are not pins. Triad homography is the fallback when
cheeks are missing or degenerate. kw/kh size the HINGE.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# iBUG/dlib 68: jaw 0-16, brows 17-26, nose 27-35, eyes 36-47, mouth 48-67.
# Upper face only — articulation-invariant s.
RIGID = tuple(list(range(17, 27)) + list(range(27, 31)) + list(range(36, 48)))
CHIN, NOSE_BASE = 8, 33
# Zygoma at nose height. Measured work f0: 2d106 i=16 a=-0.46 b≈0, i=32 a=+0.36.
CHEEK_L_106, CHEEK_R_106 = 16, 32
# 68 fallback sits a little low (jaw 3/13, b≈-0.24).
CHEEK_L_68, CHEEK_R_68 = 3, 13
SCHEMA = "head_frame/v2"
CLIP_W, CLIP_H = 736, 400
# PAINTER SIZE FROM THE LIBRARY (the User 2026-10-15, Crissa phone S0.1): a library may paint at its own size (a
# 405x720 vertical cut). frame_wh(pose_dir) = the inventory's `size`, else the `frame` of the avatar manifest that
# names this library, else (CLIP_W, CLIP_H). OFF (the default) is CLIP_W x CLIP_H verbatim. The flag is read on every
# call and nothing module-level is set from it (a test process and a bench are different processes).
# Kill: HOUSE_FRAME_FROM_LIBRARY=0 (or unset).
FRAME_FROM_LIBRARY_ENV = "HOUSE_FRAME_FROM_LIBRARY"


def frame_from_library(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(FRAME_FROM_LIBRARY_ENV, "0")).strip().lower() in ("1", "on", "true", "yes")


def _wh_pair(v) -> tuple[int, int] | None:
    try:
        if isinstance(v, dict):
            w, h = int(v.get("w") or 0), int(v.get("h") or 0)
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            w, h = int(v[0]), int(v[1])
        else:
            return None
    except (TypeError, ValueError):
        return None
    return (w, h) if w > 0 and h > 0 else None


@lru_cache(maxsize=16)
def _library_wh(pose_dir: str, inv_mtime: float) -> tuple[int, int]:
    p = Path(pose_dir)
    try:
        got = _wh_pair(json.loads((p / "inventory.json").read_text(encoding="utf-8")).get("size"))
        if got:
            return got
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import avatar
        got = _wh_pair((avatar._manifest_for(p) or {}).get("frame"))
        if got:
            return got
    except Exception:
        pass
    return (CLIP_W, CLIP_H)


def frame_wh(pose_dir=None, env=None) -> tuple[int, int]:
    """The painter's (w, h) for a library; (CLIP_W, CLIP_H) while HOUSE_FRAME_FROM_LIBRARY is off."""
    if pose_dir is None or not frame_from_library(env):
        return (CLIP_W, CLIP_H)
    p = Path(pose_dir)
    try:
        mt = (p / "inventory.json").stat().st_mtime
    except OSError:
        mt = 0.0
    return _library_wh(str(p.resolve()), mt)


# Hinge (inpaint ellipse) covers the CLIP mouth, not the tile.
# Upper edge stays this fraction of s below the dest nose so her nose survives.
# Lower edge goes this fraction of s past the dest chin so the filmed jaw dies.
# 0.04 was ~5 px on work — TELEA then reconstructed a ghost under the chin.
# 0.12 is ~14 px (submental), still above the laptop; dest nose stays outside.
INPAINT_GAP_S = 0.08
INPAINT_PAST_CHIN_S = 0.12
# Image of the ray (px). Below this a 4th pin is degenerate — face-on, w≈0.
# Prechoice wrap-guard `|w|<8` is dest MOUTH BOX width, not this. Locked-axis
# |w| is tiny when face-on; using it as a profile test inverts the table.
W_MIN_PX = 8.0


# estimate_affine_matrix_3d23d, P2sRt and matrix2angle are adapted from InsightFace,
# python-package/insightface/utils/transform.py (https://github.com/deepinsight/insightface),
# by the InsightFace contributors, released under the MIT License. They trace back to
# face3d (https://github.com/YadiraF/face3d). See THIRD-PARTY-NOTICES.md.
def estimate_affine_matrix_3d23d(X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Least-squares affine P such that Y ≈ P @ [X|1]. (3, 4)."""
    X_homo = np.hstack((X, np.ones((X.shape[0], 1), dtype=X.dtype)))
    return np.linalg.lstsq(X_homo, Y, rcond=None)[0].T


def P2sRt(P: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    t = P[:, 3]
    R1, R2 = P[0, :3], P[1, :3]
    s = (float(np.linalg.norm(R1)) + float(np.linalg.norm(R2))) / 2.0
    r1 = R1 / (np.linalg.norm(R1) + 1e-12)
    r2 = R2 / (np.linalg.norm(R2) + 1e-12)
    r3 = np.cross(r1, r2)
    R = np.vstack((r1, r2, r3))
    return s, orthonormal(R), t


def orthonormal(R: np.ndarray) -> np.ndarray:
    """Polar factor, det +1."""
    U, _, Vt = np.linalg.svd(R)
    Rn = U @ Vt
    if np.linalg.det(Rn) < 0:
        U = U.copy()
        U[:, -1] *= -1
        Rn = U @ Vt
    return Rn


def matrix2angle(R: np.ndarray) -> tuple[float, float, float]:
    """Pitch, yaw, roll in degrees, from R in ZYX order."""
    sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
    if sy < 1e-6:
        pitch = math.atan2(-R[1, 2], R[1, 1])
        yaw = math.atan2(-R[2, 0], sy)
        roll = 0.0
    else:
        pitch = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(-R[2, 0], sy)
        roll = math.atan2(R[1, 0], R[0, 0])
    return (pitch * 180 / math.pi, yaw * 180 / math.pi, roll * 180 / math.pi)


def residual_deg(R_t: np.ndarray, R_s: np.ndarray) -> float:
    """Geodesic angle between two rotations, degrees. Not the warp cap."""
    dR = R_t @ R_s.T
    tr = float(np.trace(dR))
    c = max(-1.0, min(1.0, (tr - 1.0) / 2.0))
    return abs(math.degrees(math.acos(c)))


def ray_deg(R_t: np.ndarray, R_s: np.ndarray) -> float:
    """Out-of-plane angle between two rays (ez), degrees.

    warp_cap_deg gates on this, not residual_deg. In-plane roll the affine
    does exactly; a geodesic cap of 6° refuses most talking pairs.
    """
    a = np.asarray(R_t, dtype=np.float64)[:, 2]
    b = np.asarray(R_s, dtype=np.float64)[:, 2]
    na = float(np.linalg.norm(a)) + 1e-12
    nb = float(np.linalg.norm(b)) + 1e-12
    c = max(-1.0, min(1.0, float(np.dot(a, b) / (na * nb))))
    return abs(math.degrees(math.acos(c)))


@lru_cache(maxsize=1)
def mp_meanshape_path():
    """The MediaPipe mean shape for the face being processed: HOUSE_FACE_MEANSHAPE (a path), else the
    library's own (lib/data/meanshape_68_mp_<face>.json, <face> from HOUSE_POSE_DIR's "<face>-poses"),
    else the generic canonical-mesh shape. Each face's shape is learned from MediaPipe's landmarks on
    its own films, at the scale of the face's earlier head tracks."""
    import os
    from pathlib import Path
    data = Path(__file__).resolve().parent / "data"
    got = os.environ.get("HOUSE_FACE_MEANSHAPE", "").strip()
    if got:
        return Path(got)
    pose = Path(os.environ.get("HOUSE_POSE_DIR", "") or "crissa-poses").name
    face = pose[:-len("-poses")] if pose.endswith("-poses") else ""
    own = data / ("meanshape_68_mp_%s.json" % face)
    return own if face and own.is_file() else data / "meanshape_68_mp.json"


def face_engine() -> str:
    """The landmark / head-pose engine: HOUSE_FACE_ENGINE, else this install's own default (the avatar
    manifest's `face_engine`; a shipped pack does not carry the key), else mediapipe (2026-10-01)."""
    import os
    import json
    env = os.environ.get("HOUSE_FACE_ENGINE", "").strip().lower()
    if env:
        return env
    try:
        import crissa_home
        v = str(json.loads(crissa_home.MANIFEST.read_text(encoding="utf-8")).get("face_engine") or "").strip().lower()
        if v:
            return v
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    return "mediapipe"


POINT_KEYS = ("A", "chin", "cheek_l", "cheek_r")


def station_scales() -> dict:
    """{station: factor} from the face's MediaPipe mean-shape file (`station_scale`), or {} under
    another face engine, with HOUSE_FACE_STATION_SCALE=0, or when the file has none. It corrects a
    station whose head scale reads large and low (Crissa's close-up: 0.92)."""
    import os
    import json
    if face_engine() != "mediapipe":
        return {}
    if os.environ.get("HOUSE_FACE_STATION_SCALE", "1").strip().lower() in ("0", "off", "false", "no"):
        return {}
    try:
        got = json.loads(mp_meanshape_path().read_text()).get("station_scale") or {}
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in got.items() if 0.5 <= float(v) <= 1.5}


def scale_about_nose(rec: dict | None, k: float) -> dict | None:
    """A head record scaled by k about its own nose: s, u, v, t and every image point (A, chin,
    cheeks, the mouth box). `open` is a ratio and stays. Marks the record with `station_scale`."""
    if not rec or k == 1.0 or not rec.get("nose"):
        return rec
    nx, ny = float(rec["nose"][0]), float(rec["nose"][1])
    out = dict(rec)

    def pt(p):
        return [round(nx + k * (float(p[0]) - nx), 3), round(ny + k * (float(p[1]) - ny), 3)]
    for key in POINT_KEYS:
        if isinstance(rec.get(key), (list, tuple)) and len(rec[key]) >= 2:
            out[key] = pt(rec[key])
    if isinstance(rec.get("s"), (int, float)):
        out["s"] = round(float(rec["s"]) * k, 4)
    for key in ("u", "v"):
        if isinstance(rec.get(key), (list, tuple)):
            out[key] = [round(float(x) * k, 3) for x in rec[key]]
    if isinstance(rec.get("t"), (list, tuple)) and len(rec["t"]) == 3:
        xy = pt(rec["t"][:2])
        out["t"] = [xy[0], xy[1], round(float(rec["t"][2]) * k, 3)]
    m = rec.get("mouth")
    if isinstance(m, (list, tuple)) and len(m) == 4:
        cx, cy = pt((m[0] + m[2] / 2.0, m[1] + m[3] / 2.0))
        w, hh = float(m[2]) * k, float(m[3]) * k
        out["mouth"] = [round(cx - w / 2, 2), round(cy - hh / 2, 2), round(w, 2), round(hh, 2)]
    out["station_scale"] = k
    return out


SMOOTH_VEC_KEYS = ("nose", "A", "chin", "cheek_l", "cheek_r", "mouth", "t", "u", "v", "pose")
SMOOTH_NUM_KEYS = ("s", "open")


def track_smooth_window() -> int:
    """Frames in the head-track smoother; 0 = off. MediaPipe only (its Tasks landmarker keeps no temporal filter,
    so every frame is a fresh estimate and a close-up's nose jitters frame to frame without it).
    HOUSE_FACE_SMOOTH_WIN (NEW, odd, default 5); HOUSE_FACE_SMOOTH=0 turns it off."""
    import os
    if face_engine() != "mediapipe":
        return 0
    if os.environ.get("HOUSE_FACE_SMOOTH", "1").strip().lower() in ("0", "off", "false", "no"):
        return 0
    try:
        n = int(os.environ.get("HOUSE_FACE_SMOOTH_WIN", "5"))
    except ValueError:
        n = 5
    return n if n >= 3 and n % 2 == 1 else 0


def _sg_weights(win: int, order: int = 2) -> np.ndarray:
    half = win // 2
    x = np.arange(-half, half + 1, dtype=np.float64)
    A = np.vander(x, order + 1, increasing=True)
    return np.linalg.pinv(A)[0]                       # the fitted value at the centre


def track_cuts(frames: list, ratio: float = 4.0, floor_px: float = 8.0) -> set:
    """Frame indices that start a new shot: the nose jumps more than `ratio` x the larger of its neighbouring
    steps (and more than `floor_px`; a 3 px tracker step is jitter to smooth, not a cut). A real move ramps up and down; a join between takes (up-close-harvest-4 is
    five takes, jumps of ~19 px between still frames) does not. The smoother never blends across one."""
    def nose(r):
        return np.asarray(r["nose"], dtype=np.float64) if r and r.get("nose") else None
    steps = []
    for k in range(1, len(frames)):
        a, b = nose(frames[k - 1]), nose(frames[k])
        steps.append(float(np.linalg.norm(b - a)) if a is not None and b is not None else None)
    cuts = set()
    for k, v in enumerate(steps):
        if v is None or v <= floor_px:
            continue
        nb = [x for x in (steps[k - 1] if k else None, steps[k + 1] if k + 1 < len(steps) else None) if x is not None]
        if v > ratio * max(nb + [floor_px / ratio]):
            cuts.add(k + 1)
    return cuts


def smooth_track(frames: list, win: int = 5) -> list:
    """Centred Savitzky-Golay (quadratic) over each run of consecutive tracked frames: offline, so no lag, and
    a quadratic keeps a fast move's peak where a plain average rounds it off. Runs shorter than the window stay
    raw; the window shrinks symmetrically at a run's ends, and a shot cut (`track_cuts`) ends a run. R is smoothed entry-wise and re-orthonormalised.
    None frames stay None."""
    out = [dict(r) if r else r for r in frames]
    n = len(frames)
    cuts = track_cuts(frames)
    i = 0
    while i < n:
        if not frames[i]:
            i += 1
            continue
        j = i + 1
        while j < n and frames[j] and j not in cuts:
            j += 1
        run = frames[i:j]
        if len(run) >= win:
            for k in range(len(run)):
                h = min(win // 2, k, len(run) - 1 - k)
                if h < 1:
                    continue
                wts = _sg_weights(2 * h + 1)
                seg = run[k - h:k + h + 1]
                rec = out[i + k]
                for key in SMOOTH_NUM_KEYS:
                    if all(isinstance(r.get(key), (int, float)) for r in seg):
                        rec[key] = round(float(np.dot(wts, [r[key] for r in seg])), 4)
                for key in SMOOTH_VEC_KEYS:
                    vals = [r.get(key) for r in seg]
                    if all(isinstance(v, (list, tuple)) and len(v) == len(vals[0]) for v in vals):
                        arr = np.asarray(vals, dtype=np.float64)
                        rec[key] = [round(float(v), 3) for v in wts @ arr]
                if all(isinstance(r.get("R"), (list, tuple)) and len(r["R"]) == 9 for r in seg):
                    M = (wts @ np.asarray([r["R"] for r in seg], dtype=np.float64)).reshape(3, 3)
                    U, _, Vt = np.linalg.svd(M)
                    Rm = U @ Vt
                    if np.linalg.det(Rm) < 0:
                        U[:, -1] *= -1
                        Rm = U @ Vt
                    rec["R"] = [round(float(v), 6) for v in Rm.ravel()]
        i = j
    return out


def meanshape_68() -> np.ndarray:
    """The 68-point mean shape the fit solves against, for the engine that made the landmarks:
    HOUSE_FACE_ENGINE=mediapipe reads lib/data/meanshape_68_mp.json (MediaPipe's canonical mesh,
    scaled so s keeps its units). Any other engine's mean shape is the install's own
    (head_frame_house, which no pack carries, 2026-10-01): where that module is absent the
    MediaPipe shape is used, quietly."""
    if face_engine() != "mediapipe":
        try:
            import head_frame_house
        except ImportError:
            pass
        else:
            return head_frame_house.meanshape_68()
    import json
    return np.asarray(json.loads(mp_meanshape_path().read_text())["points"], dtype=np.float64)


def fit(L: np.ndarray, M: np.ndarray | None = None,
        cheeks106: np.ndarray | None = None) -> dict[str, Any] | None:
    """Rigid frame from 68×3 landmarks (image xy + relative z).

    Cheeks (smile pins) come from 2d106 16/32 when given; else 68 3/13.
    Returns None if the face is unusable. Never fill-forward.
    """
    L = np.asarray(L, dtype=np.float64)
    if L.shape != (68, 3) or not np.isfinite(L).all():
        return None
    if M is None:
        M = meanshape_68()
    M = np.asarray(M, dtype=np.float64)
    idx = np.array(RIGID, dtype=int)
    P = estimate_affine_matrix_3d23d(M[idx], L[idx])
    s, R, t = P2sRt(P)
    if not np.isfinite(s) or s <= 1e-6:
        return None
    pitch, yaw, roll = matrix2angle(R)
    u = (s * R[0:2, 0]).astype(np.float64)  # (R00, R10) * s
    v = (s * R[0:2, 1]).astype(np.float64)
    nose = L[NOSE_BASE, :2]
    chin = L[CHIN, :2]
    A = (nose + chin) / 2.0
    open_u = float(np.linalg.norm(chin - nose) / s)
    cheek_l = L[CHEEK_L_68, :2].astype(np.float64)
    cheek_r = L[CHEEK_R_68, :2].astype(np.float64)
    cheek_from = "68"
    if cheeks106 is not None:
        G = np.asarray(cheeks106, dtype=np.float64)
        if G.ndim == 2 and G.shape[0] > CHEEK_R_106 and np.isfinite(G[CHEEK_L_106, :2]).all():
            cheek_l = G[CHEEK_L_106, :2].astype(np.float64)
            cheek_r = G[CHEEK_R_106, :2].astype(np.float64)
            cheek_from = "106"
    return {
        "s": float(s),
        "R": R.astype(np.float64),
        "t": np.asarray(t, dtype=np.float64),
        "pose": (float(pitch), float(yaw), float(roll)),
        "u": u,
        "v": v,
        "A": A,
        "nose": nose,
        "chin": chin,
        "cheek_l": cheek_l,
        "cheek_r": cheek_r,
        "cheek_from": cheek_from,
        "open": open_u,
        "ray": R[:, 2].astype(np.float64),
    }


def corners(fr: dict, kw: float, kh: float, scale: float = 1.0) -> np.ndarray:
    """Hinge quad in image px. Covers the CLIP mouth. Does NOT size the tile.

    Centered on A (filmed jaw), not the nose. The warp origin is the nose;
    this quad is hole geometry. Do not nose-center it — that eats her nose
    and misses the mouth.
    """
    A, u, v = fr["A"], fr["u"] * scale, fr["v"] * scale
    return np.array([
        A + kw * u - kh * v,
        A + kw * u + kh * v,
        A - kw * u + kh * v,
        A - kw * u - kh * v,
    ], dtype=np.float64)


def locked_axes(fr: dict, scale: float = 1.0
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Image-space ± axes of the locked 3-D triad. Length s, not the jaw.

    u = s R[:,0] (across), v = s R[:,1] (down), w = s R[:,2] (the ray).
    """
    s = float(fr["s"]) * scale
    R = np.asarray(fr["R"], dtype=np.float64)
    u = np.asarray(fr["u"], dtype=np.float64) * scale
    v = np.asarray(fr["v"], dtype=np.float64) * scale
    w = (s * R[0:2, 2]).astype(np.float64)
    return u, v, w


def triad_endpoints(fr: dict, scale: float = 1.0) -> np.ndarray:
    """Six endpoints in image xy, origin at the nose pin.

    Rows: +ex, −ex, +ey, −ey, +ez, −ez. Chin is not one of them.
    """
    nose = np.asarray(fr["nose"], dtype=np.float64)
    u, v, w = locked_axes(fr, scale)
    return np.array([
        nose + u, nose - u,
        nose + v, nose - v,
        nose + w, nose - w,
    ], dtype=np.float64)


def frame_matrix(fr: dict, scale: float = 1.0) -> np.ndarray:
    """3×3: plane (a, b, 1) -> image pixels.

    Realtime clone of the dest x,y,z triad, pinned at the noses:
    dest_px = dest_nose + a·u_dst + b·v_dst, with u,v the LOCKED axes.
    """
    u, v, _w = locked_axes(fr, scale)
    nose = np.asarray(fr["nose"], dtype=np.float64)
    return np.array([
        [u[0], v[0], nose[0]],
        [u[1], v[1], nose[1]],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)


def warp_affine_2x3(src: dict, dst: dict, scale: float = 1.0) -> np.ndarray:
    """OpenCV 2×3 mapping src image coords -> dest image coords.

    xy only: dest_px = dest_nose + a·u_dst + b·v_dst. The ray is unused.
    Live paste is warp_triad_3x3. This stays for the Haar/A-anchor rollback.

    warpAffine's default (no WARP_INVERSE_MAP) is the forward map:
    dest_px = M @ src_px. Measured OpenCV 4.13: the inverse convention
    paints an empty frame and the 0.35 hinge floor then blends black —
    the dark smear on the jaw.
    """
    Ms = frame_matrix(src, 1.0)
    Md = frame_matrix(dst, scale)
    T = Md @ np.linalg.inv(Ms)
    return T[:2].astype(np.float32)


def triad_pins(fr: dict, scale: float = 1.0) -> np.ndarray:
    """Four image points: origin, +x, +y, +z. Nose is the origin."""
    nose = np.asarray(fr["nose"], dtype=np.float64)
    u, v, w = locked_axes(fr, scale)
    return np.array([nose, nose + u, nose + v, nose + w], dtype=np.float64)


def _homography_4(src_pts: np.ndarray, dst_pts: np.ndarray) -> np.ndarray | None:
    """3×3 H, dest ~ H @ src, last row scaled to 1. None if degenerate."""
    src_pts = np.asarray(src_pts, dtype=np.float64).reshape(4, 2)
    dst_pts = np.asarray(dst_pts, dtype=np.float64).reshape(4, 2)
    A = np.zeros((8, 9), dtype=np.float64)
    for i in range(4):
        x, y = src_pts[i]
        u, v = dst_pts[i]
        A[2 * i] = (-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u)
        A[2 * i + 1] = (0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v)
    try:
        _u, _s, vt = np.linalg.svd(A)
    except np.linalg.LinAlgError:
        return None
    H = vt[-1].reshape(3, 3)
    if abs(H[2, 2]) < 1e-12:
        return None
    H = H / H[2, 2]
    if not np.isfinite(H).all():
        return None
    return H


def _embed_affine(M2: np.ndarray) -> np.ndarray:
    H = np.eye(3, dtype=np.float64)
    H[:2] = np.asarray(M2, dtype=np.float64)
    return H


def warp_triad_3x3(src: dict, dst: dict, scale: float = 1.0) -> np.ndarray:
    """3×3 homography src image -> dest image.

    Scale dest.s/src.s. Pin dest nose. Send src (nose, +x, +y, +z) onto
    dest (nose, +x, +y, +z). xy-only affine when the ray is too short
    in the image (face-on, |w| < W_MIN_PX) — a 2-D picture has no pixel
    along a ray that points into the camera.
    """
    _us, _vs, ws = locked_axes(src, 1.0)
    _ud, _vd, wd = locked_axes(dst, scale)
    if (float(np.linalg.norm(ws)) < W_MIN_PX
            or float(np.linalg.norm(wd)) < W_MIN_PX):
        return _embed_affine(warp_affine_2x3(src, dst, scale))
    H = _homography_4(triad_pins(src, 1.0), triad_pins(dst, scale))
    if H is None:
        return _embed_affine(warp_affine_2x3(src, dst, scale))
    return H


def cheek_source(fr: dict) -> str:
    """Which ruler drew this frame's cheeks: "106", "68", or "u" (guessed).

    Recorded before 2026-09-04 means no `cheek_from` field; every such
    frame in the tree came off the 68 jaw fallback, so that is the read.
    """
    if fr.get("cheek_l") is None or fr.get("cheek_r") is None:
        return "u"
    return str(fr.get("cheek_from") or "68")


def cheeks_of(fr: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Left zygoma, nose, right zygoma. Estimate from u when unrecorded."""
    nose = np.asarray(fr["nose"], dtype=np.float64)
    cl, cr = fr.get("cheek_l"), fr.get("cheek_r")
    if cl is not None and cr is not None:
        return (np.asarray(cl, dtype=np.float64),
                nose,
                np.asarray(cr, dtype=np.float64))
    u = np.asarray(fr["u"], dtype=np.float64)
    return nose - 0.45 * u, nose, nose + 0.45 * u


def bezier_pins(fr: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(L, control, R) for a quadratic that PASSES THROUGH the nose.

    B(t) = (1-t)^2 L + 2(1-t)t P + t^2 R has P as a control point, not a
    point on the curve: B(0.5) = (L + R)/4 + P/2. Feeding the nose in as P
    leaves the curve short of it by half the nose-to-chord offset (measured
    2026-09-04: 1.3 px on clip-work, 10.9 px on clip-speak-phoneme), which
    unlatches the nose the clone is supposed to be pinned on. P = 2N - (L+R)/2
    is the control point whose curve hits N at t = 0.5 exactly.
    """
    L, N, R = cheeks_of(fr)
    return L, 2.0 * N - 0.5 * (L + R), R


def cheeks_dest_solve(dst: dict, wh: tuple[int, int], scale: float = 1.0) -> dict | None:
    """The DEST half of warp_cheeks_maps — a function of the frame only.

    For every dest pixel in the strip's ROI: the Bézier parameter t (the
    quadratic solved per pixel) and the signed height h off the curve. No
    tile enters here, so a loop frame's solve can be frozen and every tile
    painted on that frame pays only the source half (poly_cache
    cheeks_solve_cached, 2026-09-14: "freeze those coefficients"). Returns
    None when there is no cheek chord to read or the ROI is empty.
    """
    W, H = int(wh[0]), int(wh[1])
    Ld, Pd, Rd = bezier_pins(dst)
    Nd = np.asarray(dst["nose"], dtype=np.float64)
    if float(np.linalg.norm(Rd - Ld)) < 8.0:
        return None
    vd = np.asarray(dst["v"], dtype=np.float64) * float(scale)
    nd = vd / (np.linalg.norm(vd) + 1e-12)
    tau = np.array([-nd[1], nd[0]], dtype=np.float64)
    pad = 0.40 * float(dst["s"]) * float(scale)
    chin = np.asarray(dst["chin"], dtype=np.float64)
    xs = np.array([Ld[0], Nd[0], Rd[0], chin[0]])
    ys = np.array([Ld[1], Nd[1], Rd[1], chin[1]])
    x0 = max(0, int(np.floor(xs.min() - pad)))
    x1 = min(W, int(np.ceil(xs.max() + pad)) + 1)
    y0 = max(0, int(np.floor(ys.min() - pad)))
    y1 = min(H, int(np.ceil(ys.max() + pad)) + 1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    Aq = float(np.dot(Ld - 2.0 * Pd + Rd, tau))
    Bq = float(2.0 * np.dot(Pd - Ld, tau))
    ptau = xx * tau[0] + yy * tau[1]
    Cq = float(np.dot(Ld, tau)) - ptau
    if abs(Aq) < 1e-9:
        t = np.where(np.abs(Bq) > 1e-9, -Cq / (Bq + 1e-12), 0.5)
    else:
        disc = np.maximum(Bq * Bq - 4.0 * Aq * Cq, 0.0)
        srt = np.sqrt(disc)
        t1 = (-Bq + srt) / (2.0 * Aq)
        t2 = (-Bq - srt) / (2.0 * Aq)
        in1 = (t1 >= -0.25) & (t1 <= 1.25)
        in2 = (t2 >= -0.25) & (t2 <= 1.25)
        t = np.where(in1 & (~in2 | (np.abs(t1 - 0.5) <= np.abs(t2 - 0.5))), t1, t2)
    t = np.clip(t, -0.25, 1.25)
    omt = 1.0 - t
    Bd = (omt * omt)[..., None] * Ld + (2.0 * omt * t)[..., None] * Pd + (t * t)[..., None] * Rd
    p = np.stack((xx, yy), axis=-1).astype(np.float64)
    h = ((p - Bd) * nd).sum(axis=-1)
    return {"box": (x0, y0, x1, y1), "t": t, "h": h,
            "vd_norm": float(np.linalg.norm(vd)), "wh": (W, H)}


def cheeks_maps_from_solve(solve: dict | None, src: dict, dst: dict
                           ) -> tuple[np.ndarray, np.ndarray] | None:
    """The SOURCE half: dest-sized remap maps for one tile on a dest solve.
    Same ruler rule as warp_cheeks_maps (None when the two frames' cheeks
    came off different rulers)."""
    if solve is None or cheek_source(src) != cheek_source(dst):
        return None
    Ls, Ps, Rs = bezier_pins(src)
    vs = np.asarray(src["v"], dtype=np.float64)
    ns = vs / (np.linalg.norm(vs) + 1e-12)
    t = np.asarray(solve["t"], dtype=np.float64)
    omt = 1.0 - t
    h = np.asarray(solve["h"], dtype=np.float64)
    scale_h = float(np.linalg.norm(vs) / (float(solve["vd_norm"]) + 1e-12))
    Bs = (omt * omt)[..., None] * Ls + (2.0 * omt * t)[..., None] * Ps + (t * t)[..., None] * Rs
    src_p = Bs + (h * scale_h)[..., None] * ns
    W, H = int(solve["wh"][0]), int(solve["wh"][1])
    x0, y0, x1, y1 = solve["box"]
    mx = np.full((H, W), -1.0, np.float32)
    my = np.full((H, W), -1.0, np.float32)
    mx[y0:y1, x0:x1] = src_p[..., 0].astype(np.float32)
    my[y0:y1, x0:x1] = src_p[..., 1].astype(np.float32)
    return mx, my


def warp_cheeks_maps(src: dict, dst: dict, wh: tuple[int, int],
                     scale: float = 1.0
                     ) -> tuple[np.ndarray, np.ndarray] | None:
    """Dest-sized remap maps: curved cheek–nose–cheek strip.

    p = Bézier(L, P, R; t) + h · v̂, P chosen so the curve passes THROUGH
    the nose (see bezier_pins). Inverse is a quadratic per pixel. h is in
    dest pixels; src uses |v_src|/|v_dst| so s carries scale.
    None → caller falls back to the triad homography. Returns None when the
    two frames' cheeks came off different rulers, or when there is no cheek
    chord to read. Since 2026-09-14 this is cheeks_dest_solve (the frame)
    followed by cheeks_maps_from_solve (the tile), bit-identical.
    """
    # Both sides must be measured with the SAME ruler. 2d106 zygoma, the 68
    # jaw fallback and the 0.45u guess disagree on |R-L|/|u| by up to 2x, and
    # this map scales the tile by exactly that ratio: measured 2026-09-04 a
    # 68-cheek tile on a guessed-cheek profile dest drew the mouth at 0.27x
    # width (clip-talk-profile-90) with the nose 52 px off. A pair that does
    # not match is not a smile we can read; hand it back to the caller.
    if cheek_source(src) != cheek_source(dst):
        return None
    return cheeks_maps_from_solve(cheeks_dest_solve(dst, wh, scale=scale), src, dst)


def crop_maps(maps):
    """Storage form of a dest-sized remap pair: (x0, y0, mx_roi, my_roi, *rest)
    — the bounding box of the pixels that map somewhere (the −1 sentinel is
    'outside'). A 736×400 float32 pair is 2.4 MB; the strip's ROI is ~0.2 MB.
    None when nothing maps."""
    if maps is None:
        return None
    mx, my = maps[0], maps[1]
    # the sentinel is exactly −1; a real coordinate just off the tile's edge
    # (−0.3) still samples pixel 0 bilinearly, so it must survive the crop
    have = (mx != -1.0) | (my != -1.0)
    rows = np.flatnonzero(have.any(axis=1))
    cols = np.flatnonzero(have.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    return (x0, y0, np.ascontiguousarray(mx[y0:y1, x0:x1]),
            np.ascontiguousarray(my[y0:y1, x0:x1])) + tuple(maps[2:])


def uncrop_maps(roi, wh: tuple[int, int]):
    """crop_maps inverse: the full dest-sized pair again, −1 outside the ROI.
    remap(uncrop(crop(maps))) is bit-identical to remap(maps)."""
    if roi is None:
        return None
    x0, y0, mxr, myr = roi[0], roi[1], roi[2], roi[3]
    W, H = int(wh[0]), int(wh[1])
    h, w = mxr.shape[:2]
    mx = np.full((H, W), -1.0, np.float32)
    my = np.full((H, W), -1.0, np.float32)
    mx[y0:y0 + h, x0:x0 + w] = mxr
    my[y0:y0 + h, x0:x0 + w] = myr
    return (mx, my) + tuple(roi[4:])


def mouth_quad(box, M) -> np.ndarray:
    """Tile-space AABB through a 2x3 warp -> the four dest points."""
    x, y, w, h = [float(v) for v in box]
    P = np.array([[x, y, 1.0], [x + w, y, 1.0],
                  [x + w, y + h, 1.0], [x, y + h, 1.0]], dtype=np.float64).T
    return (np.asarray(M, dtype=np.float64) @ P).T


def _dest_tile_roi(M, tile_hw, wh, extra: float = 24.0, dst: dict | None = None
                   ) -> tuple[int, int, int, int]:
    """Dest bbox of the tile under 2x3 M, padded. Dest-sized maps fill this."""
    th, tw = int(tile_hw[0]), int(tile_hw[1])
    W, H = int(wh[0]), int(wh[1])
    P = np.array([[0.0, 0.0, 1.0], [float(tw - 1), 0.0, 1.0],
                  [float(tw - 1), float(th - 1), 1.0],
                  [0.0, float(th - 1), 1.0]], dtype=np.float64).T
    D = (np.asarray(M, dtype=np.float64) @ P).T
    xs = list(D[:, 0])
    ys = list(D[:, 1])
    if dst is not None:
        for key in ("nose", "chin"):
            p = dst.get(key)
            if p is None:
                continue
            q = np.asarray(p, dtype=np.float64).ravel()
            if q.size >= 2:
                xs.append(float(q[0]))
                ys.append(float(q[1]))
    pad = int(max(8, extra))
    x0 = max(0, int(np.floor(min(xs))) - pad)
    y0 = max(0, int(np.floor(min(ys))) - pad)
    x1 = min(W, int(np.ceil(max(xs))) + pad)
    y1 = min(H, int(np.ceil(max(ys))) + pad)
    if x1 <= x0:
        x0, x1 = 0, W
    if y1 <= y0:
        y0, y1 = 0, H
    return x0, y0, x1, y1


def apply_width_k(M, k, tile_box, cap: float = 0.35):
    """Scale a 2x3 warp about the landed mouth centre. Height untouched.

    k=1 is a no-op. A k outside `cap` is refused (wrong boxes, not a lens).
    """
    M = np.asarray(M, dtype=np.float64)
    try:
        k = float(k)
    except (TypeError, ValueError):
        return M.astype(np.float32)
    if tile_box is None or abs(k - 1.0) < 1e-6:
        return M.astype(np.float32)
    if not (1.0 - cap) <= k <= (1.0 + cap):
        return M.astype(np.float32)
    q = mouth_quad(tile_box, M)
    lo, hi = q.min(axis=0), q.max(axis=0)
    if float(hi[0] - lo[0]) <= 2.0:
        return M.astype(np.float32)
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    S = np.array([[k, 0.0, cx * (1.0 - k)],
                  [0.0, 1.0, 0.0],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    return (S @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2].astype(np.float32)


# Classes whose mouth width is the lens, not the viseme. EE/WOO/AH in the
# median pull k toward "make everything dest_w", which is the per-tile bug.
def apply_uniform_k(M, k, tile_box, cap: float = 0.35):
    """Scale a 2x3 warp about the landed mouth centre on BOTH axes.

    The bounded paste (the User 2026-09-10, "too large … as she tilts down it
    grows"): a wrong s ratio between takes (flourish-listen-close read s
    357 against mouthzoom's 302 for the same framing) blew the mouth up
    30% in height while width-only k held the width. Same refusal rules as
    apply_width_k.
    """
    M = np.asarray(M, dtype=np.float64)
    try:
        k = float(k)
    except (TypeError, ValueError):
        return M.astype(np.float32)
    if tile_box is None or abs(k - 1.0) < 1e-6:
        return M.astype(np.float32)
    if not (1.0 - cap) <= k <= (1.0 + cap):
        return M.astype(np.float32)
    q = mouth_quad(tile_box, M)
    lo, hi = q.min(axis=0), q.max(axis=0)
    if float(hi[0] - lo[0]) <= 2.0:
        return M.astype(np.float32)
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    S = np.array([[k, 0.0, cx * (1.0 - k)],
                  [0.0, k, cy * (1.0 - k)],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    return (S @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2].astype(np.float32)


def pin_upper_lip(M, tile_box, dst: dict):
    """Translate a 2x3 warp so the tile's upper-lip top-centre lands on the
    dest mouth box top-centre. Returns (M', (dx, dy)). A per-frame pin that
    does not trust the nose: on flourish-listen-close the nose pin left the
    lip 14 px high (p95 27 px), on work 1 px (2026-09-10)."""
    M = np.asarray(M, dtype=np.float64)
    if tile_box is None or not dst or dst.get("mouth") is None:
        return M.astype(np.float32), (0.0, 0.0)
    q = mouth_quad(tile_box, M)
    dm = np.asarray(dst["mouth"], dtype=np.float64).ravel()
    if dm.size < 4:
        return M.astype(np.float32), (0.0, 0.0)
    dx = float(dm[0] + dm[2] / 2.0 - q[:, 0].mean())
    dy = float(dm[1] - q[:, 1].min())
    T = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]], dtype=np.float64)
    return (T @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2].astype(np.float32), (dx, dy)


def pin_nose_and_lip(M, tile_box, dst: dict, cap: float = 0.5):
    """Keep the nose pin, scale along the face's vertical about the nose row so
    the tile's upper-lip top lands on the dest mouth box top. Returns (M', k).
    A 2-D tilt: nose→lip foreshortens as she pitches, and this is the only
    thing a flat tile can do about it (the User 2026-09-10). k outside `cap`
    is refused, like apply_width_k. Cap 0.5, not 0.35: at 0.35 the
    head-up frames of the close-up loop (f60, pitch −16) were refused and
    fell back to the nose pin, which is exactly where the lip was worst."""
    M = np.asarray(M, dtype=np.float64)
    if tile_box is None or not dst or dst.get("mouth") is None or dst.get("nose") is None:
        return M.astype(np.float32), 1.0
    nose = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2]
    v = np.asarray(dst.get("v", (0.0, 1.0)), dtype=np.float64).ravel()[:2]
    vh = v / max(1e-6, float(np.linalg.norm(v)))
    q = mouth_quad(tile_box, M)
    top = np.array([float(q[:, 0].mean()), float(q[:, 1].min())])
    dm = np.asarray(dst["mouth"], dtype=np.float64).ravel()
    want = np.array([dm[0] + dm[2] / 2.0, dm[1]])
    landed = float(np.dot(top - nose, vh))
    target = float(np.dot(want - nose, vh))
    if landed <= 2.0 or target <= 2.0:
        return M.astype(np.float32), 1.0
    k = target / landed
    if not (1.0 - cap) <= k <= (1.0 + cap):
        return M.astype(np.float32), 1.0
    ny = float(nose[1])
    S = np.array([[1.0, 0.0, 0.0], [0.0, k, ny * (1.0 - k)], [0.0, 0.0, 1.0]], dtype=np.float64)
    return (S @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2].astype(np.float32), float(k)


def pin_nose_pixel(M, tile: np.ndarray, src: dict, dst: dict, base: np.ndarray,
                   search_s: float = 0.14, patch_s: float = 0.30, min_ncc: float = 0.90,
                   max_shift_s: float = 0.10):
    """Unify the nose by PIXELS, not landmarks (the User 2026-09-10: "pin the
    pixel from the nose on the other"). Warp the tile with M, cut the patch
    around the landed nose, template-match it into the dest frame within
    ±search_s·s of the landmark, and translate M by the best shift.
    Returns (M', (dx, dy), ncc). No shift when the match is weak."""
    M = np.asarray(M, dtype=np.float64)
    if tile is None or base is None or not dst or dst.get("nose") is None:
        return M.astype(np.float32), (0.0, 0.0), 0.0
    H, W = base.shape[:2]
    s = float(dst.get("s") or 100.0)
    nose = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2]
    warped = cv2.warpAffine(tile, M.astype(np.float32), (W, H), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    pw, ph = max(12, int(patch_s * s)), max(10, int(patch_s * 0.75 * s))
    # the patch sits ON the nose: centred at the landmark, biased up so the
    # tip and nostrils are in it and the upper lip is not
    cx, cy = int(round(nose[0])), int(round(nose[1] - 0.06 * s))
    x0, y0 = cx - pw // 2, cy - ph // 2
    x1, y1 = x0 + pw, y0 + ph
    # clip to what the tile actually holds: the heads-down harvest tiles
    # (88×75 px) start just above the nose, so the wanted patch ran off the
    # top and the pin silently did nothing on the work loop (2026-09-10)
    import fast_reduce                                   # bit-identical, a pack's paint (round 11, 2026-10-12)
    have = fast_reduce.sum_gt(warped, 12)
    ext = fast_reduce.nonzero_extent(have.view(np.uint8))
    if ext is None:
        return M.astype(np.float32), (0.0, 0.0), 0.0
    x0, x1 = max(x0, ext[2] + 1, 0), min(x1, ext[3], W)
    y0, y1 = max(y0, ext[0] + 1, 0), min(y1, ext[1], H)
    pw, ph = x1 - x0, y1 - y0
    if pw < 10 or ph < 8:
        return M.astype(np.float32), (0.0, 0.0), 0.0
    patch = warped[y0:y1, x0:x1]
    if have[y0:y1, x0:x1].mean() < 0.9:                  # still off the tile
        return M.astype(np.float32), (0.0, 0.0), 0.0
    r = max(4, int(search_s * s))
    sx0, sy0 = max(0, x0 - r), max(0, y0 - r)
    sx1, sy1 = min(W, x0 + pw + r), min(H, y0 + ph + r)
    win = base[sy0:sy1, sx0:sx1]
    if win.shape[0] < ph or win.shape[1] < pw:
        return M.astype(np.float32), (0.0, 0.0), 0.0
    g = lambda im: cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (0, 0), 1.2)
    res = cv2.matchTemplate(g(win), g(patch), cv2.TM_CCOEFF_NORMED)
    _, ncc, _, loc = cv2.minMaxLoc(res)
    if ncc < min_ncc:
        # 2026-09-11: on the angled desk framing (s 87, 63-px tiles) a 0.84
        # match shifted the tile 12 px up onto her nose; the real matches
        # on work / close-up read 0.97–0.99. Below min_ncc the landmark pin
        # stands.
        return M.astype(np.float32), (0.0, 0.0), float(ncc)
    dx, dy = float(sx0 + loc[0] - x0), float(sy0 + loc[1] - y0)
    if (dx * dx + dy * dy) ** 0.5 > max_shift_s * s:
        # a match that wants to move the tile more than a tenth of the head
        # is a wrong match (close-up head-up frames needed 0.06–0.08 s)
        return M.astype(np.float32), (0.0, 0.0), float(ncc)
    T = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]], dtype=np.float64)
    return (T @ np.vstack([M, [0.0, 0.0, 1.0]]))[:2].astype(np.float32), (dx, dy), float(ncc)


def shift_maps(maps, dx: float, dy: float):
    """Translate a dest-sized remap pair by (dx, dy): the warped tile lands dx
    right, dy down. Nearest-neighbour, so the -1 'outside' sentinel never blends
    into a real source coordinate."""
    if maps is None or (not dx and not dy):
        return maps
    mx, my = maps[0], maps[1]
    H, W = mx.shape[:2]
    T = np.array([[1.0, 0.0, float(dx)], [0.0, 1.0, float(dy)]], dtype=np.float32)
    mx2 = cv2.warpAffine(mx, T, (W, H), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=-1)
    my2 = cv2.warpAffine(my, T, (W, H), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=-1)
    return (mx2, my2) + tuple(maps[2:])


def pin_maps_nose_pixel(maps, tile: np.ndarray, src: dict, dst: dict, base: np.ndarray, **kw):
    """pin=nose-pixel for a map warp (warp=cheeks). The Bézier lands the tile by
    2d106 landmarks only; the pixel pin that affine gets never ran on it (the User
    2026-09-13: "had to do with proper pinning"). Warp the tile through the maps,
    match its landed nose into the frame (pin_nose_pixel, same thresholds), and
    translate the maps by the shift. Returns (maps', (dx, dy), ncc)."""
    if maps is None or tile is None or base is None:
        return maps, (0.0, 0.0), 0.0
    warped = cv2.remap(tile, maps[0], maps[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    I = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    _M, (dx, dy), ncc = pin_nose_pixel(I, warped, src, dst, base, **kw)
    return shift_maps(maps, dx, dy), (dx, dy), ncc


TAKE_WIDTH_CLASSES = frozenset({"rest", "MBP", "FV"})


def take_width_k(landed_widths, dest_w: float, cap: float = 0.35) -> float:
    """One k for a source take onto this dest. Median of dest_w / landed_w.

    Per-TILE k (fit_mouth_width) cannot tell a lens from a rounded mouth:
    harvest EE/WOO onto work f0 both become dest_w. The lens error is a
    property of the take (mouthzoom 1.23x, profile-45 1.21x). Median over
    that take's rest/MBP/FV widths is the lens; EE stays wide, WOO stays narrow.
    """
    try:
        dest_w = float(dest_w)
    except (TypeError, ValueError):
        return 1.0
    ks = []
    for w in landed_widths or ():
        try:
            fw = float(w)
        except (TypeError, ValueError):
            continue
        if fw > 2.0 and dest_w > 2.0:
            ks.append(dest_w / fw)
    if not ks:
        return 1.0
    ks.sort()
    k = float(ks[len(ks) // 2])
    if not (1.0 - cap) <= k <= (1.0 + cap):
        return 1.0
    return k


def fit_mouth_width(M, tile_box, dest_mouth, cap: float = 0.35):
    """Per-TILE width pin. Collapses EE and WOO onto dest_w.

    Kept as the A/B of `fit=width`. Live correction that preserves
    articulation is `take_width_k` + `apply_width_k` (`fit=take`).
    Height is still untouched. Scales about the mouth centre.
    """
    M = np.asarray(M, dtype=np.float64)
    if tile_box is None or dest_mouth is None:
        return M.astype(np.float32)
    d = np.asarray(dest_mouth, dtype=np.float64).ravel()
    if d.size < 4 or float(d[2]) <= 2.0:
        return M.astype(np.float32)
    q = mouth_quad(tile_box, M)
    got = float(q[:, 0].max() - q[:, 0].min())
    if got <= 2.0:
        return M.astype(np.float32)
    return apply_width_k(M, float(d[2]) / got, tile_box, cap=cap)


def hole_falloff(xx: np.ndarray, yy: np.ndarray, plane: tuple,
                 arch: dict | None = None) -> np.ndarray:
    """0..1 weight of the hole that hide_clip_jaw actually cut.

    When arch is set this is the dental band, not the ellipse. The live
    painter used to floor on the ellipse even with hole=arch, so the
    extra ~20% of the band the inpaint had erased was bare TELEA.
    """
    if arch is not None:
        return arch_falloff(xx, yy, arch)
    cx, cy, ax, ay, deg = (float(plane[0]), float(plane[1]), float(plane[2]),
                           float(plane[3]),
                           float(plane[4]) if len(plane) > 4 else 0.0)
    th = math.radians(deg)
    dx, dy = xx - cx, yy - cy
    xr = (dx * math.cos(th) + dy * math.sin(th)) / max(ax, 1.0)
    yr = (-dx * math.sin(th) + dy * math.cos(th)) / max(ay, 1.0)
    return np.clip(1.0 - np.sqrt(xr * xr + yr * yr), 0.0, 1.0)


def to_tile(fr: dict, origin_xy: tuple[float, float]) -> dict:
    """Shift A/nose/chin into tile-local pixels. u, v, R, s unchanged."""
    ox, oy = float(origin_xy[0]), float(origin_xy[1])
    off = np.array([ox, oy], dtype=np.float64)
    out = dict(fr)
    out["A"] = np.asarray(fr["A"], dtype=np.float64) - off
    out["nose"] = np.asarray(fr["nose"], dtype=np.float64) - off
    out["chin"] = np.asarray(fr["chin"], dtype=np.float64) - off
    if fr.get("cheek_l") is not None:
        out["cheek_l"] = np.asarray(fr["cheek_l"], dtype=np.float64) - off
    if fr.get("cheek_r") is not None:
        out["cheek_r"] = np.asarray(fr["cheek_r"], dtype=np.float64) - off
    return out


TILE_LOCAL = ("A", "nose", "chin", "cheek_l", "cheek_r")


def png_origin(frame: np.ndarray, png: np.ndarray, max_mse: float = 1.0
               ) -> tuple[int, int] | None:
    """Where a tile PNG sits in its own film frame, exactly (2026-09-17).

    A tile is a lossless crop of the decoded frame, so the squared-difference
    minimum is 0 at its origin. None when the picture is not a crop of this
    frame (another decode, a resized tile) — the caller keeps what it had."""
    if png is None or frame is None:
        return None
    if png.shape[0] > frame.shape[0] or png.shape[1] > frame.shape[1]:
        return None
    r = cv2.matchTemplate(frame, png, cv2.TM_SQDIFF)
    mn, _, loc, _ = cv2.minMaxLoc(r)
    if mn / float(png.size) > max_mse:
        return None
    return int(loc[0]), int(loc[1])


def rebase_tile_head(head: dict, origin_xy: tuple[int, int],
                     png_wh: tuple[int, int]) -> tuple[dict, tuple[int, int]]:
    """Move a tile head block from the crop it was fitted on onto the PNG's
    own origin. Returns (new head, (dx, dy) moved). The head step re-detects
    and re-crops; a PNG kept from an earlier harvest was cut from another
    window (up-close-harvest-1: 117/133 px), and the painter reads the block
    as PNG pixels — her nose tip was pinned to the mouth."""
    cx, cy = (head.get("crop_xy") or (0, 0))[:2]
    dx, dy = int(cx) - int(origin_xy[0]), int(cy) - int(origin_xy[1])
    out = dict(head)
    for k in TILE_LOCAL:
        if out.get(k) is not None:
            out[k] = [round(float(out[k][0]) + dx, 2), round(float(out[k][1]) + dy, 2)]
    m = out.get("mouth_in_tile")
    if isinstance(m, (list, tuple)) and len(m) == 4:
        out["mouth_in_tile"] = [round(float(m[0]) + dx, 2), round(float(m[1]) + dy, 2),
                                m[2], m[3]]
    out["crop_xy"] = [int(origin_xy[0]), int(origin_xy[1])]
    out["src_wh"] = [int(png_wh[0]), int(png_wh[1])]
    out["origin"] = "png"          # verified against the picture (pose_intake.tile_marks)
    return out, (dx, dy)


def pack(fr: dict, src_wh: tuple[int, int] | None = None) -> dict:
    """JSON-safe record. R flattened row-major 9."""
    R = np.asarray(fr["R"], dtype=float)
    rec = {
        "s": round(float(fr["s"]), 4),
        "R": [round(float(x), 6) for x in R.ravel().tolist()],
        "t": [round(float(x), 3) for x in np.asarray(fr["t"]).ravel()[:3]],
        "pose": [round(float(x), 2) for x in fr["pose"]],
        "u": [round(float(x), 3) for x in np.asarray(fr["u"])],
        "v": [round(float(x), 3) for x in np.asarray(fr["v"])],
        "A": [round(float(x), 2) for x in np.asarray(fr["A"])],
        "nose": [round(float(x), 2) for x in np.asarray(fr["nose"])],
        "chin": [round(float(x), 2) for x in np.asarray(fr["chin"])],
        "open": round(float(fr["open"]), 4),
    }
    if fr.get("cheek_l") is not None:
        rec["cheek_l"] = [round(float(x), 2) for x in np.asarray(fr["cheek_l"])]
    if fr.get("cheek_r") is not None:
        rec["cheek_r"] = [round(float(x), 2) for x in np.asarray(fr["cheek_r"])]
    # Which estimator drew them. 2d106 zygoma and the 68 jaw fallback are
    # NOT the same measurement (measured 2026-09-04: |R-L|/|u| is 0.81 for
    # 106, 1.74 for 68 on the profile takes). Warping one onto the other
    # scales the tile by that ratio, so the pair must never be mixed.
    if fr.get("cheek_from"):
        rec["cheek_from"] = str(fr["cheek_from"])
    if src_wh is not None:
        rec["src_wh"] = [int(src_wh[0]), int(src_wh[1])]
    return rec


def shift_nose(fr: dict, du: float = 0.0, dv: float = 0.0, px: bool = False) -> dict:
    """Offset the paste pin in face UV. Does not mutate `fr`.

    `px=False`: `du`/`dv` are fractions of the locked axes (`u`/`v` already
    length `s`). `px=True`: `du`/`dv` are PIXELS along the unit axes — the
    same nudge on every framing (the User 2026-09-10: 0.03·s was 3.5 px on the
    desk and 11 px on the close-up, "always off to the right").
    **+u is her left** (work f0: u.x > 0 → image right → viewer's right).
    **+v is down the face.** Her-left nostril tear: small +du. Hopping
    independent of the nose: EMA dest, not a bigger disk (NOSE_KEEP_S).
    """
    if not fr or (abs(float(du)) < 1e-12 and abs(float(dv)) < 1e-12):
        return fr
    out = dict(fr)
    nose = np.asarray(out["nose"], dtype=np.float64).ravel()[:2].copy()
    u = np.asarray(out["u"], dtype=np.float64).ravel()[:2]
    v = np.asarray(out["v"], dtype=np.float64).ravel()[:2]
    if px:
        u = u / max(1e-6, float(np.linalg.norm(u)))
        v = v / max(1e-6, float(np.linalg.norm(v)))
    out["nose"] = nose + float(du) * u + float(dv) * v
    return out


def turn_correct(fr: dict, depth: float, chord_off_deg: float, max_yaw: float = 20.0) -> dict:
    """The dest frame as a TURNED head should be landed on. Does not mutate `fr`.

    The User 2026-09-27, lila-sit-down-hair f205: "Why doesn't the tilt seem to pin?" ...
    "i thought we'd figure it out with math". Two things go wrong as she turns, both geometry:

    SIDEWAYS. Her mouth sits in FRONT of the line between her cheeks, so a turn swings it
    along u by depth * s * sin(yaw). The slope is known; only `depth` (a fraction of s) is
    fitted. It is NOT one clean number: film by film on Lila it reads 0.13 (hair touch),
    0.09 (chin), 0.07 (work), 0.04 (reach); pooled over six films under 20 deg it is 0.09 and
    the sideways spread halves (1.08 -> 0.58 px). Something else moves with the turn too.

    TILT. The warp hangs the tile on the cheek line, and on a turn that line tilts more than
    the head does (4-9 deg over). Facing the camera the two differ by a constant of the face
    (`chord_off_deg`: 1.5-1.7 deg on her tiles and her frontal frames alike), so the cheeks
    are turned about the nose until the line sits that far off u again.

    `yaw` is clamped to +-max_yaw: past it a front-facing tile is the wrong picture, and
    those frames are blank. A frame with no cheeks or no pose comes back as it is."""
    try:
        if not fr or fr.get("cheek_l") is None or fr.get("cheek_r") is None or not fr.get("pose"):
            return fr
        yaw = max(-float(max_yaw), min(float(max_yaw), float(fr["pose"][1])))
        u = np.asarray(fr["u"], dtype=np.float64).ravel()[:2]
        s = float(fr["s"])
        uh = u / max(1e-6, float(np.linalg.norm(u)))
        nose = np.asarray(fr["nose"], dtype=np.float64).ravel()[:2]
        cl = np.asarray(fr["cheek_l"], dtype=np.float64).ravel()[:2]
        cr = np.asarray(fr["cheek_r"], dtype=np.float64).ravel()[:2]
        chord = cr - cl
        off = math.degrees(math.atan2(u[1], u[0]) - math.atan2(chord[1], chord[0]))
        off = (off + 180.0) % 360.0 - 180.0
        a = math.radians(off - float(chord_off_deg))
        ca, sa = math.cos(a), math.sin(a)
        rot = np.array([[ca, -sa], [sa, ca]])
        shift = -float(depth) * s * math.sin(math.radians(yaw)) * uh
        out = dict(fr)
        out["cheek_l"] = nose + rot @ (cl - nose) + shift
        out["cheek_r"] = nose + rot @ (cr - nose) + shift
        out["nose"] = nose + shift
        return out
    except Exception:  # noqa: BLE001 - a correction that fails is no correction
        return fr


def ema_nose(prev, nose, k: float = 0.35):
    """3–5 frame pin smooth. k=0.35 ≈ 3 frames. k<=0 copies through."""
    n = np.asarray(nose, dtype=np.float64).ravel()[:2].copy()
    if prev is None or float(k) <= 0.0:
        return n
    p = np.asarray(prev, dtype=np.float64).ravel()[:2]
    kk = max(0.0, min(1.0, float(k)))
    return p * (1.0 - kk) + n * kk


def unpack(rec: dict | None) -> dict | None:
    if not rec or rec.get("s") is None:
        return None
    try:
        R = np.array(rec["R"], dtype=np.float64).reshape(3, 3)
        fr = {
            "s": float(rec["s"]),
            "R": R,
            "t": np.array(rec.get("t") or [0, 0, 0], dtype=np.float64),
            "pose": tuple(float(x) for x in rec["pose"]),
            "u": np.array(rec["u"], dtype=np.float64),
            "v": np.array(rec["v"], dtype=np.float64),
            "A": np.array(rec["A"], dtype=np.float64),
            "nose": np.array(rec["nose"], dtype=np.float64),
            "chin": np.array(rec["chin"], dtype=np.float64),
            "open": float(rec["open"]),
            "ray": R[:, 2],
        }
        if rec.get("cheek_l") is not None:
            fr["cheek_l"] = np.array(rec["cheek_l"], dtype=np.float64)
        if rec.get("cheek_r") is not None:
            fr["cheek_r"] = np.array(rec["cheek_r"], dtype=np.float64)
        if rec.get("cheek_from"):
            fr["cheek_from"] = str(rec["cheek_from"])
        if rec.get("mouth") is not None:
            fr["mouth"] = np.array(rec["mouth"], dtype=np.float64)
        if rec.get("mouth_in_tile") is not None:
            fr["mouth_in_tile"] = [float(v) for v in rec["mouth_in_tile"]]
    except (KeyError, TypeError, ValueError):
        return None
    return fr


def ellipse_contains(plane: tuple, xy) -> bool:
    """True if xy is inside the OpenCV ellipse (cx, cy, ax, ay, deg)."""
    X, Y = _plane_xy(plane, xy)
    if X is None:
        return False
    return X * X + Y * Y <= 1.0


# the User 2026-09-03: sheared sine as a competing hole. Ellipse is live.
#   (x)   (1  a) (t)
#   (y) = (0  b) (sin(t)^n)
# a = shear, b = height (ay), n = tooth-cusp. Filled with u ∈ [-1, 1] on the
# sine. Same pins as inpaint_plane; the curve tapers the balloon corners.
SINE_SHEAR_A = 0.0
SINE_CUSP_N = 2.0


def _plane_xy(plane: tuple, xy) -> tuple[float, float] | tuple[None, None]:
    cx, cy, ax, ay, deg = (float(plane[0]), float(plane[1]), float(plane[2]),
                           float(plane[3]), float(plane[4]) if len(plane) > 4 else 0.0)
    if ax < 1e-6 or ay < 1e-6:
        return None, None
    th = math.radians(deg)
    dx = float(xy[0]) - cx
    dy = float(xy[1]) - cy
    xr = dx * math.cos(th) + dy * math.sin(th)
    yr = -dx * math.sin(th) + dy * math.cos(th)
    return xr / ax, yr / ay


def curved_contains(plane: tuple, xy, shear: float = SINE_SHEAR_A,
                    cusp: float = SINE_CUSP_N) -> bool:
    """True if xy is inside the sheared-sine hole. the User's curved plane."""
    X, Y = _plane_xy(plane, xy)
    if X is None:
        return False
    n = max(0.2, float(cusp))
    T = (math.pi / 2.0) * (X - float(shear) * Y + 1.0)
    if T < 0.0 or T > math.pi:
        return False
    bump = abs(math.sin(T)) ** n
    return abs(Y) <= bump + 1e-6


def curved_mask(h: int, w: int, plane: tuple, shear: float = SINE_SHEAR_A,
                cusp: float = SINE_CUSP_N) -> np.ndarray:
    """uint8 255 inside the sheared-sine hole, dest-sized."""
    cx, cy, ax, ay, deg = (float(plane[0]), float(plane[1]), float(plane[2]),
                           float(plane[3]), float(plane[4]) if len(plane) > 4 else 0.0)
    n = max(0.2, float(cusp))
    th = math.radians(deg)
    yy, xx = np.mgrid[0:int(h), 0:int(w)]
    dx, dy = xx - cx, yy - cy
    xr = (dx * math.cos(th) + dy * math.sin(th)) / max(ax, 1e-6)
    yr = (-dx * math.sin(th) + dy * math.cos(th)) / max(ay, 1e-6)
    T = (np.pi / 2.0) * (xr - float(shear) * yr + 1.0)
    bump = np.abs(np.sin(np.clip(T, 0.0, math.pi))) ** n
    inside = (T >= 0.0) & (T <= math.pi) & (np.abs(yr) <= bump + 1e-6)
    return np.where(inside, np.uint8(255), np.uint8(0))


def curved_falloff(xx: np.ndarray, yy: np.ndarray, plane: tuple,
                   shear: float = SINE_SHEAR_A,
                   cusp: float = SINE_CUSP_N) -> np.ndarray:
    """1 at the centre, 0 on the sine edge. Same role as the ellipse 1-r."""
    cx, cy, ax, ay, deg = (float(plane[0]), float(plane[1]), float(plane[2]),
                           float(plane[3]), float(plane[4]) if len(plane) > 4 else 0.0)
    n = max(0.2, float(cusp))
    th = math.radians(deg)
    dx, dy = xx - cx, yy - cy
    xr = (dx * np.cos(th) + dy * np.sin(th)) / max(ax, 1.0)
    yr = (-dx * np.sin(th) + dy * np.cos(th)) / max(ay, 1.0)
    T = (np.pi / 2.0) * (xr - float(shear) * yr + 1.0)
    bump = np.abs(np.sin(np.clip(T, 0.0, math.pi))) ** n
    bump = np.maximum(bump, 1e-6)
    u = np.abs(yr) / bump
    return np.where((T >= 0.0) & (T <= math.pi), np.clip(1.0 - u, 0.0, 1.0), 0.0)


def curved_polyline(plane: tuple, shear: float = SINE_SHEAR_A,
                    cusp: float = SINE_CUSP_N, npts: int = 80) -> np.ndarray:
    """Closed outline of the sheared-sine hole, image px, int32 (N,1,2)."""
    cx, cy, ax, ay, deg = (float(plane[0]), float(plane[1]), float(plane[2]),
                           float(plane[3]), float(plane[4]) if len(plane) > 4 else 0.0)
    n = max(0.2, float(cusp))
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    ts = np.linspace(0.0, math.pi, max(8, int(npts)))
    bump = np.abs(np.sin(ts)) ** n
    rings = []
    for u in (1.0, -1.0):
        Y = u * bump
        X = (2.0 * ts / math.pi) - 1.0 + float(shear) * Y
        xr, yr = X * ax, Y * ay
        xs = cx + xr * c - yr * s
        ys = cy + xr * s + yr * c
        rings.append(np.stack([xs, ys], axis=-1))
    poly = np.vstack([rings[0], rings[1][::-1]])
    return poly.reshape(-1, 1, 2).astype(np.int32)


# the User 2026-09-03 exact curved plane: dental-arch parabola + power-series
# offset along the normal. We do not have CBCT jaw-bone pixels — feature
# points are the dest mouth box + chin + the inpaint nose-gap / past-chin
# pins (ASSUMED stand-in). s is face-across x, not arc length.
# Degree 2: k0 + k1 s + k2 s². Degree 3 Runge-oscillates on the 5 mouth
# samples (measured harvest-a f20: k_lo s³ ≈ 421, f_lo(0.5) = −134).
ARCH_POWER = 2


def _poly_ls(s, d, deg: int) -> np.ndarray:
    s = np.asarray(s, dtype=np.float64).ravel()
    d = np.asarray(d, dtype=np.float64).ravel()
    deg = max(0, min(int(deg), max(0, s.size - 1)))
    A = np.vander(s, N=deg + 1, increasing=True)
    k, *_ = np.linalg.lstsq(A, d, rcond=None)
    return k.astype(np.float64)


def _poly_eval(k: np.ndarray, s) -> np.ndarray:
    k = np.asarray(k, dtype=np.float64)
    s = np.asarray(s, dtype=np.float64)
    v = np.zeros_like(s, dtype=np.float64)
    for c in k[::-1]:
        v = v * s + c
    return v


def _face_basis(fr: dict, kw: float, kh: float, scale: float = 1.0):
    """Origin at the mouth, ex across, ey toward chin. Same ey as inpaint_plane."""
    nose = np.asarray(fr["nose"], dtype=np.float64)
    chin = np.asarray(fr["chin"], dtype=np.float64)
    s = float(fr["s"]) * scale
    u = np.asarray(fr["u"], dtype=np.float64) * scale
    deg = math.degrees(math.atan2(float(u[1]), float(u[0])))
    rad = math.radians(deg)
    ey = np.array([-math.sin(rad), math.cos(rad)], dtype=np.float64)
    if float(np.dot(chin - nose, ey)) < 0:
        deg = deg + 180.0
        rad = math.radians(deg)
        ey = np.array([-math.sin(rad), math.cos(rad)], dtype=np.float64)
    ex = np.array([math.cos(rad), math.sin(rad)], dtype=np.float64)
    box = _mouth_box(fr, kw, kh, scale)
    C = np.array([box[0] + 0.5 * box[2], box[1] + 0.5 * box[3]], dtype=np.float64)
    return C, ex, ey, s, float(deg % 360.0), box


def _mouth_box(fr: dict, kw: float, kh: float, scale: float) -> tuple:
    m = fr.get("mouth")
    if m is not None:
        mm = np.asarray(m, dtype=float).ravel()
        if mm.size >= 4 and float(mm[2]) > 2 and float(mm[3]) > 2:
            return float(mm[0]), float(mm[1]), float(mm[2]), float(mm[3])
    cr = corners(fr, kw, kh, scale)
    xs, ys = cr[:, 0], cr[:, 1]
    return float(xs.min()), float(ys.min()), float(xs.max() - xs.min()), float(
        ys.max() - ys.min())


def _to_uv(xy, C, ex, ey) -> tuple[float, float]:
    d = np.asarray(xy, dtype=np.float64) - C
    return float(np.dot(d, ex)), float(np.dot(d, ey))


def _from_uv(s, y, C, ex, ey) -> np.ndarray:
    return np.asarray(C, dtype=np.float64) + float(s) * ex + float(y) * ey


def fit_dental_r0(fr: dict, kw: float = 0.28, kh: float = 0.18,
                  scale: float = 1.0, vertex=None) -> dict | None:
    """the User r0: Y = A X^6 + B X^2 + C on dest mouth-corners + a chin vertex.

    Corner pins are the dest mouth-box floor (left, right). The vertex is
    dest chin by default, or the affine-landed tile chin when the warp
    must expand an open jaw / scale into the image.

    Even powers only. Do NOT fit the mouth AABB floor — three collinear
    box-bottom points sag ~3 px (invisible). Corners+chin sag ~14 px on
    work f0. Measured 2026-09-04.
    """
    try:
        C, ex, ey, s, deg, box = _face_basis(fr, kw, kh, scale)
    except (KeyError, TypeError, ValueError):
        return None
    mx, my, mw, mh = box
    chin = np.asarray(
        vertex if vertex is not None else fr["chin"], dtype=np.float64
    ).ravel()[:2]
    pts = [(mx, my + mh), (mx + mw, my + mh), (float(chin[0]), float(chin[1]))]
    uv = [_to_uv(p, C, ex, ey) for p in pts]
    xs = np.array([p[0] for p in uv], dtype=np.float64)
    ys = np.array([p[1] for p in uv], dtype=np.float64)
    if xs.size < 3:
        return None
    M = np.stack([xs ** 6, xs ** 2, np.ones_like(xs)], axis=1)
    coef, *_ = np.linalg.lstsq(M, ys, rcond=None)
    A, B, C0 = (float(coef[0]), float(coef[1]), float(coef[2]))
    x_hi = float(max(np.max(np.abs(xs)), 0.5 * mw, 4.0))
    sag = dental_r0_sag_uv(A, B, C0, x_hi)
    return {
        "A": A, "B": B, "C": C0,
        "Cxy": C, "ex": ex, "ey": ey, "x_hi": x_hi,
        "sag_uv": sag, "box": box, "s": s,
    }


def dental_r0_y(s, dental: dict) -> np.ndarray:
    s = np.asarray(s, dtype=np.float64)
    return dental["A"] * s ** 6 + dental["B"] * s ** 2 + dental["C"]


def dental_r0_sag_uv(A: float, B: float, C0: float, x_hi: float) -> float:
    """Y(0) - Y(x_hi) in face-UV. Chin hangs down in image when this is +."""
    y0 = A * 0.0 + B * 0.0 + C0
    y1 = A * (x_hi ** 6) + B * (x_hi ** 2) + C0
    return float(y0 - y1)


def dental_r0_sag_image(dental: dict) -> float:
    """IMAGE mid_y - corner_y of r0 at ±x_hi. Work f0 corners+chin ≈ 14 px."""
    C, ex, ey = dental["Cxy"], dental["ex"], dental["ey"]
    x_hi = dental["x_hi"]
    p0 = _from_uv(0.0, dental_r0_y(0.0, dental), C, ex, ey)
    pl = _from_uv(-x_hi, float(dental_r0_y(-x_hi, dental)), C, ex, ey)
    pr = _from_uv(x_hi, float(dental_r0_y(x_hi, dental)), C, ex, ey)
    return float(p0[1] - 0.5 * (pl[1] + pr[1]))


def dental_r0_polyline(dental: dict, npts: int = 48) -> np.ndarray:
    """r0 in image px, (N,1,2) int32, for the sheet overlay."""
    C, ex, ey = dental["Cxy"], dental["ex"], dental["ey"]
    xs = np.linspace(-dental["x_hi"], dental["x_hi"], max(8, int(npts)))
    ys = dental_r0_y(xs, dental)
    pts = np.stack([C[0] + xs * ex[0] + ys * ey[0],
                    C[1] + xs * ex[1] + ys * ey[1]], axis=-1)
    return pts.reshape(-1, 1, 2).astype(np.int32)


def dental_pixel_p(xn, sag: float, c2: float, c6: float):
    """Even arcade. p(0)=0. xn is (x-mid)/(W/2). sag is p(±1) in px."""
    xn = np.asarray(xn, dtype=np.float64)
    return float(sag) * (float(c2) * xn ** 2 + float(c6) * xn ** 6)


# Dest head scale `s` is the one ruler. Nose cap is 0.05 s (~6 px on desk).
# Stamp edge is 2× that so desk / talking-head / mouthzoom share a formula.
NOSE_KEEP_S = 0.05
STAMP_EDGE_S = 0.10
# LIVE DIALS (2026-09-12, the tile-tuning session). The conductor sets these
# module attributes from /v1/mouth-tune; square_alpha / the dental warps read
# them at call time, so no restart. STAMP_EDGE_S is the alpha ramp from the
# tile's have-edge inward (× dest s, floor 6 px). SAG_K scales the dental
# sag of warp=dental / warp=plane: 1.0 is the corners+chin r0 as fitted
# (measured on Lila talk-down: 20 px of dest sag → the 0.20·tile-height cap,
# where DENTAL-ARCH.md says "a few percent of tile height is already a lot");
# 0 is a flat affine inside the same path.
SAG_K = 1.0


def dest_nose_keep(xx, yy, dst: dict, scale: float = 1.0) -> np.ndarray:
    """True where we may paint. Dest nose stays hers.

    Cap on the tip only (`NOSE_KEEP_S` × dest `s`, ~6 px on desk), not a
    disk into the mouth. A 14 px disk reached dest mouth (work f0 nose
    242, mouth top 250, disk y=256) and killed EE/SH midline upper lip.
    Raised commissures sit above dest nose in y, beside it — outside this
    cap. Everything below dest_nose_y+2 is the upper-lip piece and stays
    valid.
    """
    nose = np.asarray(dst["nose"], dtype=np.float64).ravel()
    s = float(dst.get("s") or 100.0) * float(scale)
    r = max(4.0, NOSE_KEEP_S * s)
    nx, ny = float(nose[0]), float(nose[1])
    dx = np.asarray(xx, dtype=np.float64) - nx
    dy = np.asarray(yy, dtype=np.float64) - ny
    on_tip = (dx * dx + dy * dy) < (r * r)
    below = np.asarray(yy, dtype=np.float64) > (ny + 2.0)
    return (~on_tip) | below


def _dental_even_weights(dental: dict) -> tuple[float, float]:
    """How much of corner sag is X² vs X⁶. Falls back 0.7 / 0.3."""
    x_hi = max(float(dental.get("x_hi") or 0.0), 1e-6)
    t2 = abs(float(dental.get("B") or 0.0)) * (x_hi ** 2)
    t6 = abs(float(dental.get("A") or 0.0)) * (x_hi ** 6)
    tot = t2 + t6
    if tot < 1e-12:
        return 0.7, 0.3
    return t2 / tot, t6 / tot


def warp_dental_maps(src: dict, dst: dict, wh: tuple[int, int],
                     M: np.ndarray | None = None, scale: float = 1.0,
                     kw: float = 0.28, kh: float = 0.18,
                     tile_hw: tuple[int, int] | None = None,
                     dental: dict | None = None,
                     ) -> tuple[np.ndarray, np.ndarray] | None:
    """Pixel warp *inside* the affine rectangle. The quad does not move.

    the User: even arcade y = a2 x² + a6 x⁶. For 2D viseme tiles, inverse-map
    samples, do not bend vertices:

        I'(x, y) = I(x, y - p(x))
        p(x) = sag · (c2 xn² + c6 xn⁶),  xn = (x − mid) / (W/2),  p(0) = 0

    Same normalized p on every tile so the arch does not crawl. Midline is
    the viseme. Ends drop (k2>0). One resample: affine inverse then p.

    `dental` is this dest frame's r0 (corners+chin). Omit to fit here.
    Live path caches it in poly_cache keyed by (loop, frame), not viseme.
    """
    if M is None:
        M = warp_affine_2x3(src, dst, scale=scale)
    M = np.asarray(M, dtype=np.float64)
    Minv = np.linalg.inv(np.vstack([M, [0.0, 0.0, 1.0]]))
    W, H = int(wh[0]), int(wh[1])
    if tile_hw is None:
        return None
    th, tw = int(tile_hw[0]), int(tile_hw[1])
    cx = 0.5 * (tw - 1)
    half = max(0.5 * tw, 1.0)
    if dental is None:
        dental = fit_dental_r0(dst, kw, kh, scale)
    if dental is None:
        sag_tile, sag_dest, c2, c6 = 0.0, 0.0, 0.7, 0.3
    else:
        sag_dest = abs(dental_r0_sag_image(dental))
        # dest px per tile-x px (column 0 of the 2x3).
        px = float(np.hypot(M[0, 0], M[1, 0]))
        sag_tile = sag_dest / max(px, 1e-6)
        sag_tile = min(sag_tile, 0.20 * float(th)) * float(SAG_K)
        c2, c6 = _dental_even_weights(dental)
    x0, y0, x1, y1 = _dest_tile_roi(M, (th, tw), (W, H), extra=sag_dest + 16.0,
                                   dst=dst)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    flat = np.stack([xx.ravel(), yy.ravel(), np.ones(xx.size)], axis=0)
    src_h = Minv @ flat
    w = src_h[2] + 1e-12
    sh, sw = yy.shape
    sx = (src_h[0] / w).reshape(sh, sw)
    sy = (src_h[1] / w).reshape(sh, sw)
    xn = (sx - cx) / half
    p = dental_pixel_p(xn, sag_tile, c2, c6)
    # Inverse: y = y_src + p(x)  →  y_src = y - p. Ends drop.
    mx_r = sx
    my_r = sy - p
    # Affine rectangle. Dest nose is a disk, not a y half-plane — an /ee/
    # spread on dest rest has raised corners above dest nose in image y.
    valid = ((sx >= -1.0) & (sx <= float(tw)) & (sy >= -1.0) & (sy <= float(th))
             & dest_nose_keep(xx, yy, dst, scale))
    # paste_box RETIRED 2026-09-19 (the User: "old would be wrong anyway, delete"):
    # a 68-commissure crop hand-set on two tiles on 09-05, dropped by the 09-13
    # re-fit, in pre-09-17 tile coordinates. No tile carries it; nothing reads it.
    mx = np.full((H, W), -1.0, np.float32)
    my = np.full((H, W), -1.0, np.float32)
    mx[y0:y1, x0:x1] = np.where(valid, mx_r, -1.0).astype(np.float32)
    my[y0:y1, x0:x1] = np.where(valid, my_r, -1.0).astype(np.float32)
    return mx, my


def warp_dental_plane_maps(src: dict, dst: dict, wh: tuple[int, int],
                           M: np.ndarray | None = None, scale: float = 1.0,
                           kw: float = 0.28, kh: float = 0.18,
                           tile_hw: tuple[int, int] | None = None
                           ) -> tuple[np.ndarray, np.ndarray] | None:
    """Bend dest vertices along dest v. Footprint is a U, not a square.

    The 3-D fork of warp_dental_maps. Forward:

        dest = affine(src) + p(xn) · v̂
        p(xn) = sag · (c2 xn² + c6 xn⁶),  xn from tile x,  p(0) = 0

    Inverse: dest_flat = dest − p v̂, then affine inverse. Two iterations
    so a rolled v (x component) does not leave a shear.

    Sag is a drop of the SIDES. Do not pin the bottom to dest rest chin
    (that squashed open visemes). Do not resample onto the arch band
    (height pin). Do NOT also run warp_dental_maps — that double-bends.
    """
    if M is None:
        M = warp_affine_2x3(src, dst, scale=scale)
    M = np.asarray(M, dtype=np.float64)
    Minv = np.linalg.inv(np.vstack([M, [0.0, 0.0, 1.0]]))
    W, H = int(wh[0]), int(wh[1])
    if tile_hw is None:
        return None
    th, tw = int(tile_hw[0]), int(tile_hw[1])
    cx = 0.5 * (tw - 1)
    half = max(0.5 * tw, 1.0)
    dental = fit_dental_r0(dst, kw, kh, scale)
    v = np.asarray(dst["v"], dtype=np.float64).ravel()[:2]
    if float(v[1]) < 0:
        v = -v
    vn = float(np.hypot(v[0], v[1])) + 1e-12
    vhx, vhy = float(v[0] / vn), float(v[1] / vn)
    if dental is None:
        sag_dest, c2, c6 = 0.0, 0.7, 0.3
    else:
        sag_dest = abs(dental_r0_sag_image(dental))
        py = float(np.hypot(M[0, 1], M[1, 1]))
        dest_h = py * float(th)
        sag_dest = min(sag_dest, 0.20 * max(dest_h, 1.0)) * float(SAG_K)
        c2, c6 = _dental_even_weights(dental)
    x0, y0, x1, y1 = _dest_tile_roi(M, (th, tw), (W, H), extra=sag_dest + 16.0,
                                   dst=dst)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    sh, sw = yy.shape

    def _inv(px, py):
        flat = np.stack([px.ravel(), py.ravel(), np.ones(px.size)], axis=0)
        src_h = Minv @ flat
        wv = src_h[2] + 1e-12
        return (src_h[0] / wv).reshape(sh, sw), (src_h[1] / wv).reshape(sh, sw)

    sx, sy = _inv(xx, yy)
    xn = (sx - cx) / half
    p = dental_pixel_p(xn, sag_dest, c2, c6)
    sx, sy = _inv(xx - p * vhx, yy - p * vhy)
    xn = (sx - cx) / half
    p = dental_pixel_p(xn, sag_dest, c2, c6)
    sx, sy = _inv(xx - p * vhx, yy - p * vhy)
    valid = ((sx >= -1.0) & (sx <= float(tw)) & (sy >= -1.0) & (sy <= float(th))
             & dest_nose_keep(xx, yy, dst, scale))
    mx = np.full((H, W), -1.0, np.float32)
    my = np.full((H, W), -1.0, np.float32)
    mx[y0:y1, x0:x1] = np.where(valid, sx, -1.0).astype(np.float32)
    my[y0:y1, x0:x1] = np.where(valid, sy, -1.0).astype(np.float32)
    return mx, my


def diamond_anchors(fr: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(nose, jaw_L, jaw_R, chin) in this frame's px.

    L/R are back-jaw x (mouth ± 0.12 s), at the lip line so the waist is
    the mouth. A ± mouth_w/2 (lip commissures) clipped AH to ~0.5 of the
    affine box. Waist below the lips thins the diamond at the commissures.
    Not 2d106 52:72 (laugh lines). No 68 jaw on harvest tiles.
    """
    nose = np.asarray(fr["nose"], dtype=np.float64).ravel()[:2]
    chin = np.asarray(fr["chin"], dtype=np.float64).ravel()[:2]
    A = np.asarray(fr["A"], dtype=np.float64).ravel()[:2]
    u = np.asarray(fr["u"], dtype=np.float64).ravel()[:2]
    un = u / (float(np.hypot(u[0], u[1])) + 1e-12)
    s = float(fr.get("s") or 100.0)
    m = np.asarray(fr.get("mouth"), dtype=np.float64).ravel() if fr.get("mouth") is not None else np.zeros(0)
    mit = fr.get("mouth_in_tile")
    if m.size >= 4 and float(m[2]) > 4.0:
        mx, my, mw, mh = (float(m[0]), float(m[1]), float(m[2]), float(m[3]))
        lip_y = my + 0.55 * mh
        mouth_w = mw
        cx = mx + 0.5 * mw
    elif mit is not None and len(mit) >= 4 and float(mit[2]) > 4.0:
        mouth_w = float(mit[2])
        lip_y = float(mit[1]) + 0.55 * float(mit[3])
        cx = float(mit[0]) + 0.5 * mouth_w
    else:
        mouth_w = 0.36 * s
        lip_y = float(A[1])
        cx = float(A[0])
    extra = 0.12 * s
    cl, cr = fr.get("cheek_l"), fr.get("cheek_r")
    if cl is not None and cr is not None:
        lx = float(np.asarray(cl).ravel()[0])
        rx = float(np.asarray(cr).ravel()[0])
        half = 0.5 * abs(rx - lx)
    else:
        half = 0.5 * mouth_w + extra
    mid = np.array([cx, lip_y], dtype=np.float64)
    return nose, mid - half * un, mid + half * un, chin


def push_rest_anchors(rest: dict, dst: dict, scale: float = 1.0):
    """Dest strip from rest jaw pins through frame_matrix(dst).

    Origin is dest nose. Mouth center is rest k along dest v, not this
    frame's lip box. L/R are rest jaw pins rotated by dest u. No
    per-frame cheek-x / chin-drop. No AH/EE grow on dest.
    """
    M = warp_affine_2x3(rest, dst, scale=scale)
    _n, L, R, c = diamond_anchors(rest)
    P = _aff_pts(M, np.stack([L, R, c]))
    nose = np.asarray(dst["nose"], dtype=np.float64).ravel()[:2]
    return nose, P[0], P[1], P[2]


def _in_tri(xx, yy, p0, p1, p2) -> np.ndarray:
    ax, ay = float(p0[0]), float(p0[1])
    bx, by = float(p1[0]), float(p1[1])
    cx, cy = float(p2[0]), float(p2[1])
    v0x, v0y = cx - ax, cy - ay
    v1x, v1y = bx - ax, by - ay
    v2x = xx - ax
    v2y = yy - ay
    dot00 = v0x * v0x + v0y * v0y
    dot01 = v0x * v1x + v0y * v1y
    dot02 = v0x * v2x + v0y * v2y
    dot11 = v1x * v1x + v1y * v1y
    dot12 = v1x * v2x + v1y * v2y
    inv = 1.0 / (dot00 * dot11 - dot01 * dot01 + 1e-12)
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv
    return (u >= -0.03) & (v >= -0.03) & (u + v <= 1.03)


def _aff_pts(M, pts) -> np.ndarray:
    P = np.asarray(pts, dtype=np.float64)
    h = np.hstack([P, np.ones((P.shape[0], 1))])
    return (np.asarray(M, dtype=np.float64) @ h.T).T


def _aff_inv_pts(M, pts) -> np.ndarray:
    """Dest pixels → tile pixels through inverse of 2x3 affine."""
    M = np.asarray(M, dtype=np.float64)
    M3 = M if M.shape == (3, 3) else np.vstack([M, [0.0, 0.0, 1.0]])
    Minv = np.linalg.inv(M3)
    P = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    h = np.hstack([P, np.ones((P.shape[0], 1))])
    q = (Minv @ h.T).T
    w = q[:, 2] + 1e-12
    return np.stack([q[:, 0] / w, q[:, 1] / w], axis=1)


# Recopy of Downloads/mouth_diamond_arch.py wrap_arch_strip (the User 2026-09-04).
# s walks the poly6 arch (jaw L→R), not the L–R chord. t is ⊥ to the curve.
ARCH6 = np.array([0.00, 0.00, 0.42, 0.00, -0.18, 0.00, 0.08], dtype=np.float64)


def dental_arch6(x: np.ndarray, coeffs: np.ndarray = ARCH6) -> np.ndarray:
    y = np.zeros_like(x, dtype=np.float64)
    p = np.ones_like(x)
    for a in coeffs:
        y += a * p
        p *= x
    return y


# House wrap visemes. Same numbers as mouth_diamond_arch.VISEMES.
WRAP_VISEME = {
    "REST": {},
    "MBP": {"jaw": 0.02, "width": 0.92},
    "FV": {"jaw": 0.12, "upper": 0.15, "lower": 0.25},
    "TH": {"jaw": 0.18, "lower": 0.2},
    "TDN": {"jaw": 0.20, "upper": 0.08},
    "L": {"jaw": 0.22, "upper": 0.1},
    "R": {"jaw": 0.28, "round": 0.2},
    "KG": {"jaw": 0.30},
    "SZ": {"jaw": 0.16, "width": 1.08},
    "SH": {"jaw": 0.18, "round": 0.35, "width": 0.95},
    "EE": {"jaw": 0.28, "width": 1.18, "upper": 0.12},
    "EH": {"jaw": 0.38, "width": 1.10},
    "ER": {"jaw": 0.32, "round": 0.25},
    "AH": {"jaw": 0.78, "width": 1.38},
    "OH": {"jaw": 0.55, "round": 0.7, "width": 0.82},
    "OO": {"jaw": 0.32, "round": 0.95, "width": 0.62},
    "Y": {"jaw": 0.26, "width": 1.12, "upper": 0.1},
    "H": {"jaw": 0.20},
}
WRAP_CLASS = {
    "rest": "REST", "rest-open": "REST",
    "MBP": "MBP", "FV": "FV", "TH": "TH",
    "TDN": "TDN", "L": "L", "R": "R", "KG": "KG",
    "S/Z": "SZ", "SH/CH": "SH",
    "EE": "EE", "EH": "EH", "ER": "ER",
    "AH": "AH", "OH": "OH", "WOO": "OO", "Y": "Y", "H": "H",
}


def wrap_viseme_kwargs(name: str | None) -> dict:
    key = WRAP_CLASS.get(str(name or "REST"), str(name or "REST"))
    return dict(WRAP_VISEME.get(key, WRAP_VISEME["REST"]))


def wrap_arch_strip(nose, chin, L, R, *, jaw: float = 0.0, round: float = 0.0,
                    width: float = 1.0, upper: float = 0.0, lower: float = 0.0,
                    n_arch: int = 65) -> dict[str, np.ndarray]:
    """s walks poly6 (jaw L→R). t is ⊥ to that curve. Not a facing chord.

    Dental bow: p(0)=0, ends drop (mid toward nose, like the dashed poly6).
    No nose/chin diamond pins — those were the beige convex clip.
    """
    nose = np.asarray(nose, dtype=np.float64).ravel()[:2]
    chin = np.asarray(chin, dtype=np.float64).ravel()[:2]
    L = np.asarray(L, dtype=np.float64).ravel()[:2]
    R = np.asarray(R, dtype=np.float64).ravel()[:2]
    axis = R - L
    ln = float(np.linalg.norm(axis))
    if ln < 1e-6:
        axis = np.array([1.0, 0.0], dtype=np.float64)
        ln = 1.0
    else:
        axis = axis / ln
    nrm = np.array([-axis[1], axis[0]], dtype=np.float64)
    if nrm[1] < 0:
        nrm = -nrm
    mid = 0.5 * (L + R)
    nose_chin = float(np.linalg.norm(chin - nose))
    open_px = float(jaw) * 0.62 * nose_chin
    spread = 0.5 * ln * float(width) + float(jaw) * 0.22 * ln
    drop = float(jaw) * 0.28 * nose_chin
    Lj = mid - axis * spread + nrm * drop
    Rj = mid + axis * spread + nrm * drop
    chin_j = chin + nrm * (open_px * 0.55)
    w = float(np.linalg.norm(Rj - Lj))
    s = np.linspace(-1.0, 1.0, n_arch)
    amp = 0.22 * w + 0.06 * open_px
    z = dental_arch6(s)
    z = z - z[0]
    wrap_k = 0.18
    x_scale = 1.0 - wrap_k * (z / (np.max(np.abs(z)) + 1e-8))
    chord = 0.5 * (Lj + Rj)
    # +nrm (chin): z_mid < 0 bows the mid toward the nose. Dental ∩, not ∪.
    arch_pts = (chord + (s * 0.5 * w * x_scale)[:, None] * axis
                + z[:, None] * nrm * amp)
    tang = np.gradient(arch_pts, axis=0)
    tang = tang / (np.linalg.norm(tang, axis=1, keepdims=True) + 1e-8)
    col = np.stack([-tang[:, 1], tang[:, 0]], axis=1)
    if float(np.dot(col[n_arch // 2], nrm)) < 0:
        col = -col
    round_px = float(round) * 0.12 * w
    arch_pts = arch_pts + col * (-round_px * (1.0 - s ** 2))[:, None]
    up_h = 0.38 * w + float(upper) * 0.10 * w + 0.10 * open_px
    lo_h = 0.42 * w + float(lower) * 0.14 * w + 0.75 * open_px
    upper_e = arch_pts - col * up_h
    lower_e = arch_pts + col * lo_h
    return {
        "arch": arch_pts, "upper": upper_e, "lower": lower_e,
        "L": Lj, "R": Rj, "chin": chin_j, "col": col, "s": s,
        "open_px": open_px,
    }


def _closest_s(arch: np.ndarray, pts: np.ndarray):
    """Nearest sample on the wrapped arch. Returns (index, delta)."""
    d = pts[:, None, :] - arch[None, :, :]
    dist2 = (d ** 2).sum(axis=2)
    idx = dist2.argmin(axis=1)
    nearest = arch[idx]
    return idx, pts - nearest


def _lerp_strip(strip: dict, ss: np.ndarray):
    """Continuous s ∈ [-1,1] → arch / col / upper / lower on the strip."""
    s_vals = np.asarray(strip["s"], dtype=np.float64)
    ss = np.clip(np.asarray(ss, dtype=np.float64), float(s_vals[0]), float(s_vals[-1]))
    idx = np.searchsorted(s_vals, ss, side="right") - 1
    idx = np.clip(idx, 0, s_vals.size - 2)
    s0, s1 = s_vals[idx], s_vals[idx + 1]
    u = ((ss - s0) / (s1 - s0 + 1e-12))[:, None]
    def mix(key):
        a = np.asarray(strip[key], dtype=np.float64)
        return a[idx] + u * (a[idx + 1] - a[idx])
    C = mix("arch")
    col = mix("col")
    col = col / (np.linalg.norm(col, axis=1, keepdims=True) + 1e-12)
    return C, col, mix("upper"), mix("lower")


def wrap_st_src_xy(dest_xy: np.ndarray, posed: dict, src_strip: dict):
    """dest (s,t) on posed strip samples src_strip (s,t). Continuous s.

    Nearest-of-33 bins made fat columns (AH jaw throw). Interpolate the
    polyline, then eval src in *src* pixels (tile wh), not dest pixels.
    """
    arch = np.asarray(posed["arch"], dtype=np.float64)
    a = arch[:-1]
    b = arch[1:]
    ab = b - a
    ab2 = (ab ** 2).sum(axis=1) + 1e-12
    pts = np.asarray(dest_xy, dtype=np.float64)
    tseg = ((pts[:, None, :] - a[None, :, :]) * ab[None, :, :]).sum(-1) / ab2[None, :]
    tseg = np.clip(tseg, 0.0, 1.0)
    closest = a[None, :, :] + tseg[:, :, None] * ab[None, :, :]
    d2 = ((pts[:, None, :] - closest) ** 2).sum(-1)
    j = d2.argmin(axis=1)
    i = np.arange(pts.shape[0])
    u = tseg[i, j]
    C = closest[i, j]
    s_vals = np.asarray(posed["s"], dtype=np.float64)
    ss = s_vals[:-1][j] + u * (s_vals[1:][j] - s_vals[:-1][j])
    col = (np.asarray(posed["col"], dtype=np.float64)[:-1][j]
           + u[:, None] * (np.asarray(posed["col"], dtype=np.float64)[1:][j]
                           - np.asarray(posed["col"], dtype=np.float64)[:-1][j]))
    col = col / (np.linalg.norm(col, axis=1, keepdims=True) + 1e-12)
    upper = (np.asarray(posed["upper"], dtype=np.float64)[:-1][j]
             + u[:, None] * (np.asarray(posed["upper"], dtype=np.float64)[1:][j]
                             - np.asarray(posed["upper"], dtype=np.float64)[:-1][j]))
    lower = (np.asarray(posed["lower"], dtype=np.float64)[:-1][j]
             + u[:, None] * (np.asarray(posed["lower"], dtype=np.float64)[1:][j]
                             - np.asarray(posed["lower"], dtype=np.float64)[:-1][j]))
    signed = ((pts - C) * col).sum(axis=1)
    up_h = np.linalg.norm(upper - C, axis=1) + 1e-6
    lo_h = np.linalg.norm(lower - C, axis=1) + 1e-6
    t = np.where(signed < 0, signed / up_h, signed / lo_h)
    t = np.clip(t, -1.2, 1.2)
    sC, s_col, s_up, s_lo = _lerp_strip(src_strip, ss)
    r_h = np.where(t < 0, np.linalg.norm(s_up - sC, axis=1),
                   np.linalg.norm(s_lo - sC, axis=1))
    src_xy = sC + s_col * (t * r_h)[:, None]
    inside = np.abs(t) <= 1.0
    return src_xy, t, inside


def warp_diamond_maps(src: dict, dst: dict, wh: tuple[int, int],
                      M: np.ndarray | None = None, scale: float = 1.0,
                      kw: float = 0.28, kh: float = 0.18,
                      tile_hw: tuple[int, int] | None = None,
                      viseme: str | None = None,
                      dst_rest: dict | None = None
                      ) -> tuple[np.ndarray, np.ndarray] | None:
    """Dest-sized maps, tile-xy values. Rest pins via frame_matrix(dst).

    remap(tile, mx, my) lands a soft |t| strip in dest — not warpAffine of
    the harvested AABB. Dest nose stays dest (alpha=0, mx=-1) so TELEA
    does not fill a dest_nose_keep hole. No AH/EE grow on dest.
    |yaw|≥35° returns None — affine+triad, not this wrap. ARCH6 stays.
    """
    if tile_hw is None:
        return None
    pose = dst.get("pose") if dst else None
    try:
        yaw = abs(float(np.asarray(pose).ravel()[1])) if pose is not None else 0.0
    except (IndexError, TypeError, ValueError):
        yaw = 0.0
    if yaw >= 35.0:
        return None
    W, H = int(wh[0]), int(wh[1])
    th, tw = int(tile_hw[0]), int(tile_hw[1])
    rest = dst_rest if dst_rest is not None else dst
    d_nose, dL, dR, d_chin = push_rest_anchors(rest, dst, scale=scale)
    dest_posed = wrap_arch_strip(d_nose, d_chin, dL, dR)
    _sn, sL, sR, _sc = diamond_anchors(src)
    tile_posed = wrap_arch_strip(src["nose"], src["chin"], sL, sR)
    pad = 24
    box = np.vstack([dest_posed["arch"], dest_posed["upper"], dest_posed["lower"],
                     dest_posed["L"][None, :], dest_posed["R"][None, :],
                     dest_posed["chin"][None, :]])
    x0 = max(0, int(box[:, 0].min()) - pad)
    y0 = max(0, int(box[:, 1].min()) - pad)
    x1 = min(W, int(box[:, 0].max()) + pad)
    y1 = min(H, int(box[:, 1].max()) + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    dest_xy = np.stack([xx.ravel(), yy.ravel()], axis=1)
    src_xy, t, inside = wrap_st_src_xy(dest_xy, dest_posed, tile_posed)
    sx, sy = src_xy[:, 0], src_xy[:, 1]
    # Inset the harvested PNG. Painting sy=0 / sx=0 is the AABB edge.
    mrg = 6.0
    keep = (inside & (sx >= mrg) & (sx <= float(tw - 1) - mrg)
            & (sy >= mrg) & (sy <= float(th - 1) - mrg)
            & dest_nose_keep(xx.ravel(), yy.ravel(), dst, scale))
    # Soft |t| over the whole strip. A 0.15 cliff left the crop AABB opaque.
    fall = np.clip(1.0 - np.abs(t), 0.0, 1.0)
    edge = np.minimum(np.minimum(sx, sy),
                      np.minimum(float(tw - 1) - sx, float(th - 1) - sy))
    a = (fall * np.clip(edge / 8.0, 0.0, 1.0)).astype(np.float32)
    a = np.where(keep, a, 0.0).astype(np.float32)
    mx = np.full((H, W), -1.0, np.float32)
    my = np.full((H, W), -1.0, np.float32)
    alpha = np.zeros((H, W), np.float32)
    mx[y0:y1, x0:x1] = np.where(keep, sx, -1.0).astype(np.float32).reshape(yy.shape)
    my[y0:y1, x0:x1] = np.where(keep, sy, -1.0).astype(np.float32).reshape(yy.shape)
    alpha[y0:y1, x0:x1] = a.reshape(yy.shape)
    return mx, my, alpha


def fit_parabola(xs, ys) -> np.ndarray:
    """Least-squares y = ax² + bx + c. Returns (a, b, c)."""
    xs = np.asarray(xs, dtype=np.float64).ravel()
    ys = np.asarray(ys, dtype=np.float64).ravel()
    if xs.size < 3:
        raise ValueError("parabola needs ≥3 points")
    A = np.stack([xs * xs, xs, np.ones_like(xs)], axis=1)
    abc, *_ = np.linalg.lstsq(A, ys, rcond=None)
    return abc.astype(np.float64)


def _parabola(x, abc):
    a, b, c = abc
    return a * x * x + b * x + c


def _normal_yplus(x, abc):
    """Unit normal of y=ax²+bx+c, flipped so +y (chin) is positive."""
    a, b, _c = abc
    ty = 2.0 * a * x + b
    n = np.array([-ty, 1.0], dtype=np.float64)
    n /= math.hypot(float(n[0]), float(n[1])) + 1e-12
    if n[1] < 0:
        n = -n
    return n


def fit_arch(fr: dict, kw: float = 0.28, kh: float = 0.18, scale: float = 1.0,
             power: int | None = None) -> dict | None:
    """Dental-arch hole: r(s)=r0(s)+f(s)n(s). r0 is a LS parabola.

    Feature points = dest mouth box + chin + nose-gap / past-chin pins.
    Not jaw-bone pixels (we have none). power defaults to ARCH_POWER.
    """
    try:
        C, ex, ey, s, deg, box = _face_basis(fr, kw, kh, scale)
    except (KeyError, TypeError, ValueError):
        return None
    mx, my, mw, mh = box
    chin = np.asarray(fr["chin"], dtype=np.float64)
    nose = np.asarray(fr["nose"], dtype=np.float64)
    gap_s, past_s = _inpaint_s()
    gap, past = gap_s * s, past_s * s
    yaw = abs(float((fr.get("pose") or (0, 0, 0))[1]))
    if yaw >= 35.0:
        past = max(past, 0.22 * s)

    def uv(p):
        return _to_uv(p, C, ex, ey)

    # r0 = lip/arch line, not the chin — chin is an OFFSET sample.
    arch_xy = [
        (mx, my + mh), (mx + 0.5 * mw, my + mh), (mx + mw, my + mh),
        (mx, my + 0.5 * mh), (mx + mw, my + 0.5 * mh),
    ]
    lo_xy = [
        (mx, my + mh), (mx + 0.5 * mw, my + mh), (mx + mw, my + mh),
        tuple(chin),
        tuple(chin + past * ey),
    ]
    hi_xy = [
        (mx, my), (mx + 0.5 * mw, my), (mx + mw, my),
        tuple(nose + gap * ey),
    ]
    arch_uv = [uv(p) for p in arch_xy]
    lo = [uv(p) for p in lo_xy]
    hi = [uv(p) for p in hi_xy]
    xs = np.array([p[0] for p in arch_uv], dtype=np.float64)
    ys = np.array([p[1] for p in arch_uv], dtype=np.float64)
    if xs.size < 3:
        return None
    abc = fit_parabola(xs, ys)
    x_hi = max(4.0, 0.5 * mw, float(np.max(np.abs(xs))))
    power = ARCH_POWER if power is None else max(0, int(power))

    def signed(p):
        x, y = p
        y0 = _parabola(x, abc)
        n = _normal_yplus(x, abc)
        return x, float((y - y0) * n[1])

    def series(pts):
        raw = [signed(p) for p in pts]
        ss = np.array([p[0] / x_hi for p in raw], dtype=np.float64)
        dd = np.array([p[1] for p in raw], dtype=np.float64)
        return _poly_ls(ss, dd, power), raw

    k_lo, lo_raw = series(lo)
    k_hi, hi_raw = series(hi)

    def cover(k, raw, side):
        k = np.array(k, dtype=np.float64, copy=True)
        for x, d in raw:
            sn = x / x_hi
            f = float(_poly_eval(k, np.array([sn]))[0])
            if side == "lo" and f < d:
                k[0] += (d - f)
            elif side == "hi" and f > d:
                k[0] += (d - f)
        return k

    k_lo = cover(k_lo, lo_raw, "lo")
    k_hi = cover(k_hi, hi_raw, "hi")
    # Same pins as the ellipse: stop `gap` below dest nose, `past` below chin.
    d_nose = signed(uv(nose))[1]
    d_past = signed(uv(tuple(chin + past * ey)))[1]
    hi_min = float(d_nose) + max(gap, 2.0)
    lo_max = max(float(d_past), 8.0)
    if float(_poly_eval(k_hi, np.array([0.0]))[0]) >= -0.5:
        k_hi = np.array([min(hi_min, -2.0)], dtype=np.float64)
    if float(_poly_eval(k_lo, np.array([0.0]))[0]) <= 0.5:
        k_lo = np.array([lo_max], dtype=np.float64)
    return {
        "C": C, "ex": ex, "ey": ey, "abc": abc, "x_hi": float(x_hi),
        "k_lo": k_lo, "k_hi": k_hi, "deg": deg, "s": float(s),
        "lo_max": float(lo_max), "hi_min": float(hi_min),
    }


def _arch_sd(x, y, arch: dict):
    """(s_norm, signed distance along n) for face-coords (x, y)."""
    abc = arch["abc"]
    y0 = _parabola(x, abc)
    n = _normal_yplus(x, abc)
    d = (y - y0) * n[1]   # (0, y-y0) · n ; s is face-across x (ASSUMED)
    return x / arch["x_hi"], d


def arch_contains(arch: dict, xy) -> bool:
    if not arch:
        return False
    x, y = _to_uv(xy, arch["C"], arch["ex"], arch["ey"])
    if abs(x) > arch["x_hi"] * 1.05:
        return False
    sn, d = _arch_sd(x, y, arch)
    lo = min(max(float(_poly_eval(arch["k_lo"], np.array([sn]))[0]), 0.0),
             arch["lo_max"])
    hi = max(min(float(_poly_eval(arch["k_hi"], np.array([sn]))[0]), 0.0),
             arch["hi_min"])
    return hi <= d <= lo


def arch_window(h: int, w: int, arch: dict) -> tuple[int, int, int, int] | None:
    """Image bbox containing every pixel `arch_mask` can set. None if empty.

    Derived from the mask's OWN test, not from `arch_polyline` — the polyline
    samples linspace(-x_hi, x_hi) and rounds to int32, while the mask admits
    |x| <= x_hi * 1.05, so a polyline bbox can clip the commissures.

    |x| <= 1.05 x_hi, and hi <= (y - y0(x)) * ny <= lo with ny = 1/sqrt(ty²+1),
    so y - y0 spans [hi_min, lo_max] * max(1/ny). Bound y0 by the parabola's
    extremes on the interval (endpoints, plus the vertex when it is inside).
    Rigorous, not sampled. Pad 2 px after rounding.
    """
    C, ex, ey = arch["C"], arch["ex"], arch["ey"]
    abc = arch["abc"]
    X = float(arch["x_hi"]) * 1.05
    cand = [-X, X]
    if abs(float(abc[0])) > 1e-12:
        xv = -float(abc[1]) / (2.0 * float(abc[0]))
        if -X <= xv <= X:
            cand.append(xv)
    y0s = [float(_parabola(np.array([c], dtype=np.float64), abc)[0]) for c in cand]
    ty = max(abs(2.0 * float(abc[0]) * (-X) + float(abc[1])),
             abs(2.0 * float(abc[0]) * X + float(abc[1])))
    inv_ny = math.sqrt(ty * ty + 1.0)
    y_top = min(y0s) + float(arch["hi_min"]) * inv_ny
    y_bot = max(y0s) + float(arch["lo_max"]) * inv_ny
    px, py = [], []
    for cx in (-X, X):
        for cy in (y_top, y_bot):
            px.append(C[0] + cx * ex[0] + cy * ey[0])
            py.append(C[1] + cx * ex[1] + cy * ey[1])
    x0 = max(0, int(math.floor(min(px))) - 2)
    y0 = max(0, int(math.floor(min(py))) - 2)
    x1 = min(int(w), int(math.ceil(max(px))) + 3)
    y1 = min(int(h), int(math.ceil(max(py))) + 3)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def arch_mask(h: int, w: int, arch: dict) -> np.ndarray:
    """Dest-sized uint8 band. Evaluated only inside `arch_window`.

    Full-frame mgrid over 294,400 px for a ~62x59 band measured 8.83 ms —
    36% of the paste. Outside the window the old full-frame mask was already
    0, so this is bit-identical, not an approximation.
    """
    out = np.zeros((int(h), int(w)), np.uint8)
    win = arch_window(h, w, arch)
    if win is None:
        return out
    wx0, wy0, wx1, wy1 = win
    C, ex, ey = arch["C"], arch["ex"], arch["ey"]
    yy, xx = np.mgrid[wy0:wy1, wx0:wx1]
    dx, dy = xx - C[0], yy - C[1]
    x = dx * ex[0] + dy * ex[1]
    y = dx * ey[0] + dy * ey[1]
    abc = arch["abc"]
    y0 = _parabola(x, abc)
    ty = 2.0 * abc[0] * x + abc[1]
    nlen = np.sqrt(ty * ty + 1.0) + 1e-12
    ny = 1.0 / nlen
    ny = np.where(ny < 0, -ny, ny)
    d = (y - y0) * ny
    sn = x / arch["x_hi"]
    lo = np.clip(_poly_eval(arch["k_lo"], sn), 0.0, arch["lo_max"])
    hi = np.clip(_poly_eval(arch["k_hi"], sn), arch["hi_min"], 0.0)
    inside = (np.abs(x) <= arch["x_hi"] * 1.05) & (d >= hi) & (d <= lo)
    out[wy0:wy1, wx0:wx1] = np.where(inside, np.uint8(255), np.uint8(0))
    return out


def arch_falloff(xx: np.ndarray, yy: np.ndarray, arch: dict) -> np.ndarray:
    C, ex, ey = arch["C"], arch["ex"], arch["ey"]
    dx, dy = xx - C[0], yy - C[1]
    x = dx * ex[0] + dy * ex[1]
    y = dx * ey[0] + dy * ey[1]
    abc = arch["abc"]
    y0 = _parabola(x, abc)
    ty = 2.0 * abc[0] * x + abc[1]
    nlen = np.sqrt(ty * ty + 1.0) + 1e-12
    ny = np.abs(1.0 / nlen)
    d = (y - y0) * ny
    sn = x / arch["x_hi"]
    lo = np.clip(_poly_eval(arch["k_lo"], sn), 1e-6, arch["lo_max"])
    hi = np.clip(_poly_eval(arch["k_hi"], sn), arch["hi_min"], -1e-6)
    u = np.where(d >= 0.0, d / lo, d / hi)
    return np.where(np.abs(x) <= arch["x_hi"] * 1.05, np.clip(1.0 - u, 0.0, 1.0), 0.0)


def arch_polyline(arch: dict, npts: int = 48) -> np.ndarray:
    xs = np.linspace(-arch["x_hi"], arch["x_hi"], max(8, int(npts)))
    abc = arch["abc"]
    C, ex, ey = arch["C"], arch["ex"], arch["ey"]
    rings = []
    for which in ("k_hi", "k_lo"):
        pts = []
        for x in xs:
            y0 = _parabola(x, abc)
            n = _normal_yplus(x, abc)
            f = float(_poly_eval(arch[which], np.array([x / arch["x_hi"]]))[0])
            if which == "k_lo":
                f = min(max(f, 0.0), arch["lo_max"])
            else:
                f = max(min(f, 0.0), arch["hi_min"])
            px = x + f * n[0]
            py = y0 + f * n[1]
            img = C + px * ex + py * ey
            pts.append(img)
        rings.append(np.asarray(pts, dtype=np.float64))
    poly = np.vstack([rings[0], rings[1][::-1]])
    return poly.reshape(-1, 1, 2).astype(np.int32)


def _inpaint_s() -> tuple[float, float]:
    """Gap/past from the avatar manifest; constants are the Crissa-measured default."""
    try:
        from avatar import head_geom
        g = head_geom()
        gap = float(g.get("inpaint_gap_s") or INPAINT_GAP_S)
        past = float(g.get("inpaint_past_chin_s") or INPAINT_PAST_CHIN_S)
        return gap, past
    except Exception:
        return INPAINT_GAP_S, INPAINT_PAST_CHIN_S


def inpaint_plane(fr: dict, kw: float, kh: float, scale: float = 1.0
                  ) -> tuple[float, float, float, float, float]:
    """(cx, cy, ax, ay, deg) for hide_clip_jaw.

    Hole covers the CLIP mouth (dest chin) and stops below the dest nose.
    Warp origin is the nose; this ellipse is not. Centering on the tip
    eats forehead. Chin here is hole size, not a tile pin.
    """
    nose = np.asarray(fr["nose"], dtype=float)
    chin = np.asarray(fr["chin"], dtype=float)
    s = float(fr["s"]) * scale
    u = np.asarray(fr["u"], dtype=float) * scale
    deg = math.degrees(math.atan2(float(u[1]), float(u[0])))
    rad = math.radians(deg)
    ey = np.array([-math.sin(rad), math.cos(rad)])
    if float(np.dot(chin - nose, ey)) < 0:
        deg = deg + 180.0
        rad = math.radians(deg)
        ey = np.array([-math.sin(rad), math.cos(rad)])
    L_ey = max(0.0, float(np.dot(chin - nose, ey)))
    gap_s, past_s = _inpaint_s()
    # Profile wide-opens: dest chin is still the filmed (often narrower)
    # jaw, so 0.12 s past it left the viseme's lower lip hanging off the
    # hole. |yaw| ≥ 35° is the L/R profile bins.
    yaw = abs(float((fr.get("pose") or (0, 0, 0))[1]))
    if yaw >= 35.0:
        past_s = max(past_s, 0.22)
    gap = gap_s * s
    past = past_s * s
    ay = max(2.0, 0.5 * (L_ey + past - gap))
    C = nose + (gap + ay) * ey
    cr = corners(fr, kw, kh, scale)
    span = float(np.linalg.norm(cr[0] - cr[3]))
    ax = max(2.0, 0.70 * span / 2.0)
    return (float(C[0]), float(C[1]), float(ax), float(ay), float(deg % 360.0))


def mirror_head(rec: dict | None) -> dict | None:
    """A tile's head block for its picture flipped left-right (the turn fill's mirror, the User 2026-09-29).

    Tile pixels x -> W - x; the cheeks swap names; the head's own axes stay right-handed (u keeps
    pointing to the virtual face's left = image right, v down); R -> M R M with M = diag(-1, 1, 1), so
    yaw and roll change sign and pitch stays; t follows the crop. `open` and `s` are unchanged."""
    if not rec or not rec.get("src_wh"):
        return rec
    W = float(rec["src_wh"][0])
    out = dict(rec)

    def px(p):
        return [round(W - float(p[0]), 3), float(p[1])] + [float(x) for x in list(p)[2:]]
    for key in ("A", "nose", "chin"):
        if isinstance(rec.get(key), (list, tuple)) and len(rec[key]) >= 2:
            out[key] = px(rec[key])
    cl, cr = rec.get("cheek_l"), rec.get("cheek_r")
    out.pop("cheek_l", None)
    out.pop("cheek_r", None)
    if isinstance(cr, (list, tuple)) and len(cr) >= 2:
        out["cheek_l"] = px(cr)
    if isinstance(cl, (list, tuple)) and len(cl) >= 2:
        out["cheek_r"] = px(cl)
    for key in ("mouth_in_tile", "mouth"):
        m = rec.get(key)
        if isinstance(m, (list, tuple)) and len(m) == 4:
            out[key] = [round(W - float(m[0]) - float(m[2]), 3), float(m[1]), float(m[2]), float(m[3])]
    if isinstance(rec.get("u"), (list, tuple)) and len(rec["u"]) >= 2:
        out["u"] = [float(rec["u"][0]), -float(rec["u"][1])]
    if isinstance(rec.get("v"), (list, tuple)) and len(rec["v"]) >= 2:
        out["v"] = [-float(rec["v"][0]), float(rec["v"][1])]
    if isinstance(rec.get("R"), (list, tuple)) and len(rec["R"]) == 9:
        R = np.array(rec["R"], dtype=np.float64).reshape(3, 3)
        M = np.diag([-1.0, 1.0, 1.0])
        out["R"] = [round(float(x), 6) for x in (M @ R @ M).ravel()]
    if isinstance(rec.get("pose"), (list, tuple)) and len(rec["pose"]) == 3:
        p = [float(x) for x in rec["pose"]]
        out["pose"] = [p[0], -p[1], -p[2]]
    t, c = rec.get("t"), rec.get("crop_xy")
    if isinstance(t, (list, tuple)) and len(t) == 3:
        cx = float((c or (0, 0))[0])
        out["t"] = [round(2 * cx + W - float(t[0]), 3), float(t[1]), float(t[2])]
    out["mirrored"] = True
    return out
