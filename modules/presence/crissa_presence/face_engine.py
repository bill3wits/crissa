"""Face detect + embed - MediaPipe Face Landmarker + AuraFace, the same API the rail had (2026-10-09, licence seam).

The house's engine was a detector-and-recogniser bundle whose licence does not allow it to ship (non-commercial). This file
keeps the class the whole rail calls - `get_face_engine()`, `FaceEngine.detect(frame) -> [Face]`, the static
`embed / det_score / bbox / gender` - and fills it with the pack's own engine, the one `modules/oracle` runs:

    detector + landmarks   MediaPipe Face Landmarker (Apache-2.0); the five-point alignment and the 112x112 crop
                           follow modules/oracle/crissa_oracle/face.py
    identity               AuraFace v1 recognition model (Apache-2.0, fal/AuraFace-v1, glintr100.onnx only),
                           512 numbers, unit length - a DIFFERENT embedding space from the house's, so an identity
                           store minted on the house is never read here (enrollments._same_family) and every pack
                           enrols afresh.
    gender                 none: AuraFace carries no attribute head. gender() answers (None, None), and every
                           gender-split path in the rail already treats None as "no split".

Thresholds: the match bar is the one measured on this engine (modules/oracle/crissa_oracle/modes.py, 2026-10-08:
genuine min 0.540 / impostor max 0.354 on AuraFace, bar 0.45, CRISSA_FACE_MATCH overrides) - see sweep.MATCH_SIM.

Models are fetched on first use into <state home>/oracle/models (shared with the oracle module, so one download
serves both); `install()` does it. Every import is inside the call: a machine with no engine runs the rest.
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import numpy as np

from . import paths as _paths

FAMILY = "auraface-v1"                   # the engine family stamped into every record this engine writes
DIM = 512
# the same files, in the same place, as modules/oracle/crissa_oracle/setup.py
MODELS = _paths.HOME / "oracle" / "models"
LANDMARKER = MODELS / "face_landmarker.task"
LANDMARKER_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
                  "float16/1/face_landmarker.task")
AURAFACE_REPO = "fal/AuraFace-v1"
AURAFACE_REV = "af6d057c9b0ec4071d4c49c80e3539258798b609"
AURAFACE_FILE = "glintr100.onnx"
AURAFACE = MODELS / "auraface" / AURAFACE_FILE
# MediaPipe Face Mesh indices: right-eye corners, left-eye corners, nose tip, mouth corners
EYE_R, EYE_L, NOSE, MOUTH_R, MOUTH_L = (33, 133), (362, 263), 1, 61, 291
TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))

_ENGINE_LOCK = threading.Lock()
_ENGINE: "FaceEngine | None" = None
_DEFAULT_THREADS = int(os.environ.get("RAIL_FACE_ORT_THREADS", "8"))


def import_mediapipe():
    """mediapipe, imported so the face engine never needs a sound server (the User 2026-10-10: in a session with no
    PulseAudio, or a machine without PortAudio, `import mediapipe` died in sounddevice's PortAudio start, though the
    face tasks never use audio). sounddevice is tried first on its own; when it cannot start, a stand-in that refuses
    any use takes its name before mediapipe is imported."""
    import sys
    import types
    if "mediapipe" not in sys.modules and "sounddevice" not in sys.modules:
        try:
            import sounddevice  # noqa: F401
        except Exception:  # noqa: BLE001  OSError (PortAudio), ImportError (no package): the face engine needs neither
            stub = types.ModuleType("sounddevice")

            def _no_audio(*_a, **_k):
                raise OSError("no audio in the face engine (sounddevice stand-in: PortAudio did not start)")
            stub.__getattr__ = lambda _name: _no_audio      # type: ignore[attr-defined]
            sys.modules["sounddevice"] = stub
    import mediapipe as mp
    return mp


def auraface_url() -> str:
    return "https://huggingface.co/%s/resolve/%s/%s" % (AURAFACE_REPO, AURAFACE_REV, AURAFACE_FILE)


def installed() -> bool:
    return LANDMARKER.is_file() and AURAFACE.is_file()


def _fetch(url: str, dest: Path, timeout: float = 300.0) -> None:
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def install(fetch=_fetch, log=print) -> dict:
    """Fetch the two model files that are missing. The packages are the env's (crissa-presence.py --install)."""
    if not LANDMARKER.is_file():
        log("presence: fetching the face landmarker ...")
        fetch(LANDMARKER_URL, LANDMARKER)
    if not AURAFACE.is_file():
        log("presence: fetching the AuraFace recognition model ...")
        fetch(auraface_url(), AURAFACE)
    return {"landmarker": LANDMARKER.is_file(), "auraface": AURAFACE.is_file()}


def get_face_engine(det_size: int = 640, threads: int | None = None) -> "FaceEngine":
    """Process-wide engine - one load, reuse forever (the landmarker and the ONNX session are both costly)."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = FaceEngine(det_size=det_size, threads=_DEFAULT_THREADS if threads is None else threads)
        return _ENGINE


class Face:
    """One detection, with the attributes the rail reads: bbox [x1, y1, x2, y2], det_score, kps (5x2),
    normed_embedding (512, unit length, lazily computed), and no sex/gender."""
    __slots__ = ("bbox", "det_score", "kps", "_frame", "_engine", "_emb", "_tried")

    def __init__(self, bbox, det_score: float, kps, frame, engine):
        self.bbox = np.asarray(bbox, dtype=np.float32)
        self.det_score = float(det_score)
        self.kps = np.asarray(kps, dtype=np.float32)
        self._frame, self._engine, self._emb, self._tried = frame, engine, None, False

    @property
    def normed_embedding(self):
        if not self._tried:
            self._tried = True
            self._emb = self._engine.embed_at(self._frame, self.kps)
            self._frame = None                   # drop the frame once the numbers exist
        return self._emb

    @property
    def embedding(self):
        return self.normed_embedding


class FaceEngine:
    """detect(frame_bgr) -> [Face]; embed(face) -> L2-normalised 512-d vector."""

    def __init__(self, det_size: int = 640, threads: int | None = None):
        self.det_size = int(det_size)
        self.threads = _DEFAULT_THREADS if threads is None else int(threads)
        self.family = FAMILY
        self._lm = None
        self._rec = None
        self._why = ""
        self._load()

    def _load(self) -> None:
        try:
            mp = import_mediapipe()
            if not LANDMARKER.is_file():
                raise RuntimeError("no landmarker model yet (crissa-presence.py --install)")
            opts = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(LANDMARKER)), num_faces=8)
            self._lm = mp.tasks.vision.FaceLandmarker.create_from_options(opts)
        except Exception as exc:  # noqa: BLE001
            self._why = "the face landmarker did not load: %s: %s" % (type(exc).__name__, str(exc)[:160])
            return
        try:
            import onnxruntime as ort
            if not AURAFACE.is_file():
                raise RuntimeError("no recognition model yet (crissa-presence.py --install)")
            so = ort.SessionOptions()
            so.intra_op_num_threads = self.threads
            so.inter_op_num_threads = 1
            self._rec = ort.InferenceSession(str(AURAFACE), sess_options=so, providers=["CPUExecutionProvider"])
        except Exception as exc:  # noqa: BLE001
            self._why = "the recognition model did not load: %s: %s" % (type(exc).__name__, str(exc)[:160])

    @property
    def ready(self) -> bool:
        return self._lm is not None and self._rec is not None

    @property
    def why(self) -> str:
        return self._why

    def _landmarks(self, frame_bgr) -> list:
        """The landmarker's face meshes for one picture (normalised to that picture)."""
        import cv2
        import mediapipe as mp
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        return list(self._lm.detect(img).face_landmarks or [])

    def _faces(self, meshes, frame_bgr, w: int, h: int) -> list:
        out = []
        for lms in meshes:
            xs = [p.x * w for p in lms]
            ys = [p.y * h for p in lms]
            bbox = [max(0.0, min(xs)), max(0.0, min(ys)), min(float(w), max(xs)), min(float(h), max(ys))]
            kps = _five_points(lms, w, h)
            # the landmarker has no detection score; a whole mesh inside the frame is a confident face
            inside = sum(1 for x, y in zip(xs, ys) if 0 <= x < w and 0 <= y < h) / float(max(len(xs), 1))
            out.append(Face(bbox, 0.5 + 0.5 * inside, kps, frame_bgr, self))
        return out

    def detect(self, frame_bgr, rescue: bool = False):
        """The faces in a still. `rescue` (enrolment's callers only): a still with none is searched again on crops
        (A SMALL FACE IS LOOKED FOR ON CROPS)."""
        if self._lm is None or frame_bgr is None:
            return []
        try:
            h, w = frame_bgr.shape[:2]
            out = self._faces(self._landmarks(frame_bgr), frame_bgr, w, h)
            if out or not rescue or not crop_rescue_on():
                return out
            for x0, y0, cw, ch in rescue_crops(w, h):
                meshes = self._landmarks(np.ascontiguousarray(frame_bgr[y0:y0 + ch, x0:x0 + cw]))
                mapped = [[_Mark((x0 + p.x * cw) / w, (y0 + p.y * ch) / h) for p in lms] for lms in meshes]
                for f in self._faces(mapped, frame_bgr, w, h):
                    if all(_iou(f.bbox, g.bbox) < 0.3 for g in out):
                        out.append(f)
                if out:
                    break
            return out
        except Exception:  # noqa: BLE001
            return []

    def embed_at(self, frame_bgr, kps):
        if self._rec is None or frame_bgr is None:
            return None
        try:
            import cv2
            M, _ = cv2.estimateAffinePartial2D(np.asarray(kps, dtype=np.float32), np.array(TEMPLATE, dtype=np.float32))
            if M is None:
                return None
            crop = cv2.warpAffine(frame_bgr, M, (112, 112), borderValue=0.0)
            x = (cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) - 127.5) / 127.5
            out = self._rec.run(None, {self._rec.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0][0]
            e = np.asarray(out, dtype=np.float32)
            n = float(np.linalg.norm(e))
            return None if n < 1e-6 else e / n
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def embed(face):
        e = getattr(face, "normed_embedding", None)
        if e is None:
            e = getattr(face, "embedding", None)
        if e is None:
            return None
        e = np.asarray(e, dtype=np.float32)
        n = float(np.linalg.norm(e))
        return None if n < 1e-6 else e / n

    @staticmethod
    def det_score(face) -> float:
        return float(getattr(face, "det_score", 0.0) or 0.0)

    @staticmethod
    def bbox(face):
        b = getattr(face, "bbox", None)
        if b is None:
            return None
        return [float(x) for x in b]

    @staticmethod
    def gender(face) -> tuple[str | None, float | None]:
        """AuraFace has no attribute head: no soft M/F. Callers treat None as 'no split'."""
        return None, None


# A SMALL FACE IS LOOKED FOR ON CROPS (round 21, .101 2026-10-14 round 20: a visitor's Voice ID and Face photos read
# no_face at a normal desk distance until they leaned in). The landmarker finds a face with a short-range detector at a
# small input size, so a face that fills little of the frame is not found; the house's head tracks hit the same with a
# small turned face (2026-09-29) and a crop search found 342 of 361 frames against 193. A still with no face is searched
# again on its centre (60 % and 40 %) and its four overlapping 60 % quarters, each scaled up to the detector's input by
# the landmarker itself; a face found there is mapped back to the whole still. Only enrolment asks for it (store_face's
# store and probe), never a room or door watch, so no other frame pays for the extra looks. Kill:
# CRISSA_FACE_CROP_RESCUE=0.
class _Mark:
    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float):
        self.x, self.y = x, y


def crop_rescue_on() -> bool:
    return os.environ.get("CRISSA_FACE_CROP_RESCUE", "1").strip().lower() not in ("0", "off", "false", "no")


def rescue_crops(w: int, h: int) -> list[tuple[int, int, int, int]]:
    """(x0, y0, width, height) of the crops a still with no face is searched on, in order."""
    out = []
    for f in (0.6, 0.4):
        cw, ch = int(w * f), int(h * f)
        out.append(((w - cw) // 2, (h - ch) // 2, cw, ch))
    cw, ch = int(w * 0.6), int(h * 0.6)
    for x0 in (0, w - cw):
        for y0 in (0, h - ch):
            out.append((x0, y0, cw, ch))
    return out


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _five_points(landmarks, w: int, h: int) -> list[tuple[float, float]]:
    def px(i):
        return landmarks[i].x * w, landmarks[i].y * h

    def mid(a, b):
        (ax, ay), (bx, by) = px(a), px(b)
        return (ax + bx) / 2.0, (ay + by) / 2.0
    return [mid(*EYE_R), mid(*EYE_L), px(NOSE), px(MOUTH_R), px(MOUTH_L)]
