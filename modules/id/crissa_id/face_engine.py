"""The id module's face engine: MediaPipe Face Landmarker + AuraFace, the oracle module's own code (2026-10-09).

The house enrols faces with a detector family that does not ship (non-commercial licence). The pack enrols with
exactly what modules/oracle/crissa_oracle/face.py looks with: the landmarker finds the face and its mesh, the five
points (eye centres, nose tip, mouth corners) align a 112x112 crop, AuraFace's recognition model (Apache-2.0,
`glintr100.onnx` alone) gives the 512 numbers. One engine, so an enrolment here is what the oracle and the presence
service compare against; the match threshold is theirs too (lib/crissa_presence.MATCH, measured 2026-10-08).

Every import is inside the call and every failure is a quiet record with `ok: False` and a reason. The record keeps
the keys the enrol tools and the presence loader read: encoding, dim, model, det_score, bbox, yaw, bin, kps.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import paths as _paths

MODEL = "auraface-v1"
DIM = 512
_ORACLE = _paths.MODULE.parent / "oracle"
_FACE: dict[str, Any] = {"mod": None, "setup": None, "tried": False, "why": ""}


def _oracle():
    """crissa_oracle.face and .setup, from the oracle module beside this one; None (with why) when absent."""
    if _FACE["tried"]:
        return _FACE["mod"], _FACE["setup"]
    _FACE["tried"] = True
    try:
        if str(_ORACLE) not in sys.path:
            sys.path.insert(0, str(_ORACLE))
        from crissa_oracle import face, setup  # type: ignore
        _FACE.update(mod=face, setup=setup)
    except Exception as exc:  # noqa: BLE001
        _FACE["why"] = "the oracle module is not installed beside this one (%s: %s)" % (type(exc).__name__, str(exc)[:120])
    return _FACE["mod"], _FACE["setup"]


def python() -> str:
    """The interpreter that carries the engine: the id env's when it is built, else the running one."""
    try:
        from . import setup
        py = setup.env_python()
        if py.exists():
            return str(py)
    except Exception:  # noqa: BLE001
        pass
    return sys.executable


def reason() -> str:
    """'' when the engine can look and name a face, else why not (in words a person can act on)."""
    face, setup = _oracle()
    if face is None:
        return _FACE["why"]
    why = face.engine_reason()
    if why:
        return why
    if not (setup.AURAFACE.is_file()):
        return "no recognition model yet (python3 modules/id/scripts/crissa-id.py --install)"
    return ""


def ready() -> bool:
    return reason() == ""


def threshold() -> float:
    """The presence service's match bar, read from its code, never a second number here."""
    try:
        try:
            from . import crissa_presence as zp
        except ImportError:
            sys.path.insert(0, str(_paths.PACK / "lib"))
            import crissa_presence as zp  # noqa: E402
        return float(zp.MATCH)
    except Exception:  # noqa: BLE001
        return 0.45


def _yaw_ratio(kps) -> float | None:
    """Nose vs the eye midpoint along the eye line, in IOD units (house_enroll.face_yaw's own rule)."""
    try:
        (lx, ly), (rx, ry), (nx, ny) = kps[0], kps[1], kps[2]
    except (TypeError, IndexError, ValueError):
        return None
    ex, ey = rx - lx, ry - ly
    iod = (ex * ex + ey * ey) ** 0.5 or 1.0
    ax, ay = ex / iod, ey / iod
    mx, my = (lx + rx) / 2.0, (ly + ry) / 2.0
    return float(((nx - mx) * ax + (ny - my) * ay) / iod)


def _yaw_bin(ratio) -> str | None:
    """house_enroll.face_yaw's bins, its front band too (round 19: CRISSA_FACE_FRONT_MAX, 0.18)."""
    if ratio is None:
        return None
    a = abs(float(ratio))
    if a < float(os.environ.get("CRISSA_FACE_FRONT_MAX", "0.18") or 0.18):
        return "front"
    side = "_L" if ratio < 0 else "_R"
    return ("three_quarter" if a <= 0.45 else "profile") + side


def embed_bgr(frame) -> dict[str, Any]:
    """One record for the largest face in a BGR frame."""
    face, setup = _oracle()
    if face is None:
        return {"ok": False, "error": "import: " + _FACE["why"]}
    os.environ.setdefault("CRISSA_ORACLE_AURAFACE", "1")       # the id module always wants the identity model
    lm = face._landmarker()
    if lm is None:
        return {"ok": False, "error": face.engine_reason() or "no landmarker"}
    try:
        import cv2
        import mediapipe as mp
        h, w = frame.shape[:2]
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        faces = res.face_landmarks or []
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "look: %s: %s" % (type(exc).__name__, str(exc)[:120])}
    if not faces:
        return {"ok": False, "error": "no_face", "faces": 0}

    def box(f):
        xs = [p.x * w for p in f]
        ys = [p.y * h for p in f]
        return [min(xs), min(ys), max(xs), max(ys)]

    best = max(faces, key=lambda f: (lambda b: (b[2] - b[0]) * (b[3] - b[1]))(box(f)))
    pts = face.five_points(best, w, h)
    enc = face.embed(frame, pts)
    if enc is None:
        return {"ok": False, "error": "no_embedding", "faces": len(faces),
                "detail": "the recognition model is not there (run the id install)" if not setup.AURAFACE.is_file() else ""}
    kps = [[float(x), float(y)] for x, y in pts]
    yaw = _yaw_ratio(kps)
    b = box(best)
    return {"ok": True, "dim": len(enc), "encoding": [float(x) for x in enc], "model": MODEL,
            # the landmarker has no detector score; a face it meshes is a face (1.0), the gate's bar is the box size
            "det_score": 1.0, "bbox": [float(x) for x in b], "yaw": yaw, "bin": _yaw_bin(yaw), "kps": kps,
            "faces": len(faces)}


def embed_jpeg(path: str | Path) -> dict[str, Any]:
    try:
        import cv2
        img = cv2.imread(str(path))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "import: %s" % exc, "path": str(path)}
    if img is None:
        return {"ok": False, "error": "imread failed", "path": str(path)}
    rec = embed_bgr(img)
    rec["path"] = str(path)
    return rec


def embed_jpegs(paths: list[str | Path]) -> list[dict[str, Any]]:
    return [embed_jpeg(p) for p in paths]


def faces_in(path: str | Path) -> dict[str, Any]:
    """How many faces, and the largest one's box: the reference guard's question (ref_guard.check_ref)."""
    face, _setup = _oracle()
    if face is None:
        return {"ok": False, "error": _FACE["why"], "faces": 0}
    try:
        import cv2
        img = cv2.imread(str(path))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "faces": 0}
    if img is None:
        return {"ok": False, "error": "no_image", "faces": 0}
    rec = embed_bgr(img)
    if rec.get("ok") or rec.get("error") == "no_embedding":
        return {"ok": True, "faces": int(rec.get("faces") or 1), "bbox": rec.get("bbox"), "det_score": rec.get("det_score")}
    return {"ok": rec.get("error") == "no_face", "faces": 0, "error": rec.get("error")}


def main(argv=None) -> int:
    """stdin: a JSON list of JPEG paths -> stdout: one JSON list of records (the enrol tools' child-process shape)."""
    raw = sys.stdin.read()
    try:
        paths = json.loads(raw)
    except ValueError:
        paths = [p for p in raw.split() if p]
    if not isinstance(paths, list):
        paths = [paths]
    print(json.dumps(embed_jpegs(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
