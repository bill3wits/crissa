"""One look at one camera frame: is a face there, and (optionally) whose (2026-10-01).

Detector and landmarks: MediaPipe Face Landmarker. Identity: AuraFace's recognition
model on a 112x112 crop aligned by five points (eye centres, nose tip, mouth corners)
taken from the landmarker's mesh. Every import is inside the call and every failure is
a quiet `None`: a machine with no face engine installed runs the Wall exactly as before.

SKELETON: the alignment template and the preprocessing follow the ArcFace convention
the model was trained with; they are not yet proved on a labelled set (see the README).
"""
from __future__ import annotations

import os

from . import setup

# MediaPipe Face Mesh indices: right-eye corners, left-eye corners, nose tip, mouth corners
EYE_R, EYE_L, NOSE, MOUTH_R, MOUTH_L = (33, 133), (362, 263), 1, 61, 291
# the 112x112 five-point template ArcFace-family recognisers are trained on
TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))

_LM = {"obj": None, "tried": False, "why": ""}
_REC = {"obj": None, "tried": False}


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


def _landmarker():
    if _LM["tried"]:
        return _LM["obj"]
    _LM["tried"] = True
    try:
        mp = import_mediapipe()
    except Exception as exc:  # noqa: BLE001  quiet, but never silent about WHY (2026-10-06, crissatest)
        _LM["obj"], _LM["why"] = None, import_reason(exc)
        return None
    try:
        if not setup.LANDMARKER.is_file():
            _LM["why"] = "no landmarker model yet (run crissa-oracle.py --install)"
            return None
        opts = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(setup.LANDMARKER)), num_faces=4)
        _LM["obj"] = mp.tasks.vision.FaceLandmarker.create_from_options(opts)
    except Exception as exc:  # noqa: BLE001
        _LM["obj"], _LM["why"] = None, "the landmarker did not load: %s: %s" % (type(exc).__name__, str(exc)[:160])
    return _LM["obj"]


def import_reason(exc: BaseException) -> str:
    """Why the face engine could not be imported, in words a person can act on. MediaPipe imports sounddevice,
    which needs the PortAudio library: without it the import fails with an OSError that names neither."""
    text = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    low = text.lower()
    if "portaudio" in low or "sounddevice" in low:
        return ("mediapipe could not import because sounddevice could not load PortAudio (%s). Install it: "
                "Debian/Ubuntu `sudo apt install libportaudio2`, Fedora `sudo dnf install portaudio`, "
                "macOS `brew install portaudio`." % text)
    if isinstance(exc, ImportError) and "mediapipe" in low:
        return "mediapipe is not installed in this Python (pip install -r modules/oracle/requirements.txt)"
    return "mediapipe could not import (%s)" % text


def engine_reason() -> str:
    """'' when the face engine is ready, else why not (shown by the service as `engine_why`)."""
    return "" if _landmarker() is not None else (_LM.get("why") or "the face engine is not available")


def _recogniser():
    if _REC["tried"]:
        return _REC["obj"]
    _REC["tried"] = True
    try:
        if not (setup.auraface_wanted() and setup.AURAFACE.is_file()):
            return None
        import onnxruntime as ort
        so = ort.SessionOptions()
        # two threads, as presence's own recogniser (RAIL_FACE_ORT_THREADS): a look a second must not take every core
        so.intra_op_num_threads = max(1, int(os.environ.get("CRISSA_ORACLE_ORT_THREADS", "2") or 2))
        _REC["obj"] = ort.InferenceSession(str(setup.AURAFACE), so, providers=["CPUExecutionProvider"])
    except Exception:
        _REC["obj"] = None
    return _REC["obj"]


def five_points(landmarks, w: int, h: int) -> list[tuple[float, float]]:
    """Eye centres, nose tip and mouth corners in pixels, from one face's normalised mesh."""
    def px(i):
        return landmarks[i].x * w, landmarks[i].y * h

    def mid(a, b):
        (ax, ay), (bx, by) = px(a), px(b)
        return (ax + bx) / 2.0, (ay + by) / 2.0
    return [mid(*EYE_R), mid(*EYE_L), px(NOSE), px(MOUTH_R), px(MOUTH_L)]


def embed(frame_bgr, points) -> "list[float] | None":
    """The 512-number identity of the face at `points`, unit length; None without the recogniser."""
    rec = _recogniser()
    if rec is None:
        return None
    try:
        import cv2
        import numpy as np
        M, _ = cv2.estimateAffinePartial2D(np.array(points, dtype=np.float32), np.array(TEMPLATE, dtype=np.float32))
        if M is None:
            return None
        crop = cv2.warpAffine(frame_bgr, M, (112, 112), borderValue=0.0)
        x = (cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) - 127.5) / 127.5
        out = rec.run(None, {rec.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0][0]
        n = float(np.linalg.norm(out))
        return (out / n).tolist() if n > 0 else None
    except Exception:
        return None


def similarity(a, b) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def look(frame_bgr, enrolled: "list[list[float]] | None" = None) -> "dict | None":
    """{"seen": bool, "faces": n, "sim": best similarity to the enrolled user or None}; None = could not look."""
    lm = _landmarker()
    if lm is None or frame_bgr is None:
        return None
    try:
        import cv2
        import mediapipe as mp
        h, w = frame_bgr.shape[:2]
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)))
        faces = res.face_landmarks or []
    except Exception:
        return None
    out = {"seen": bool(faces), "faces": len(faces), "sim": None, "embedding": None,
           "centres": [(f[NOSE].x, f[NOSE].y) for f in faces]}
    if faces:
        best = None
        for f in faces:
            e = embed(frame_bgr, five_points(f, w, h))
            if e is None:
                continue
            s = max(similarity(e, ref) for ref in enrolled) if enrolled else -1.0
            if best is None or s > best:
                best, out["embedding"] = s, e          # the best face's numbers travel to the presence service
        out["sim"] = best if (enrolled and best is not None) else None
    return out
