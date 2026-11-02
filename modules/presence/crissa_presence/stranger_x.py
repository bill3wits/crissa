"""Sequential Security strangers X1, X2, … — photos, not occupancy.

the User 2026-08-26: unnamed entry and speech snaps live under <records>
as X# folders. Same face is scanned into the same X# until the User IDs them
(if ever). Each photo is its own file so a log row can point at one still.
Not a roster slot. Not AUTH. Not a count.
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

_LOCK = threading.Lock()
SCHEMA = "house_stranger/v1"
# Same unknown as an existing X-slot. Enrolled naming stays at FaceID 0.60.
MATCH_SIM = float(os.environ.get("RAIL_STRANGER_MATCH_SIM", "0.40"))  # same face again, on AuraFace: inside the measured gap (sweep.py)
DEDUP_SEC = float(os.environ.get("RAIL_STRANGER_DEDUP_SEC", "2.0"))
MAX_FACE_JSON = 8


def stranger_root() -> Path:
    raw = os.environ.get("RAIL_STRANGER_ROOT")
    if raw:
        return Path(raw).expanduser()
    return _paths.RECORDS / "strangers"


def is_stranger_id(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return bool(n.startswith("x") and n[1:].isdigit())


def display_of(xid: str) -> str:
    n = (xid or "").strip().lower()
    if n.startswith("x") and n[1:].isdigit():
        return f"X{n[1:]}"
    return (xid or "").upper()


def _index_path(root: Path) -> Path:
    return root / "index.json"


def _load_index(root: Path) -> dict[str, Any]:
    p = _index_path(root)
    if not p.is_file():
        return {"schema": SCHEMA, "next": 1, "open": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("schema", SCHEMA)
            data.setdefault("next", 1)
            data.setdefault("open", [])
            return data
    except (OSError, ValueError):
        pass
    return {"schema": SCHEMA, "next": 1, "open": []}


def _save_index(root: Path, idx: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = _index_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_index_path(root))


def _norm(emb: Any) -> np.ndarray | None:
    """Unit vector or None. None/NaN/dim<2 must not become array([nan])."""
    if emb is None:
        return None
    try:
        e = np.asarray(emb, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if e.size < 2 or not np.isfinite(e).all():
        return None
    n = float(np.linalg.norm(e))
    if n < 1e-6:
        return None
    return e / n


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def _folder(root: Path, xid: str) -> Path:
    return root / display_of(xid)


def _load_embs(folder: Path) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    face = folder / "face"
    if not face.is_dir():
        return out
    for p in sorted(face.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        e = _norm(rec.get("encoding") or rec.get("embedding"))
        if e is not None:
            out.append(e)
    return out


def match_xid(emb: Any, *, root: Path | None = None) -> tuple[str | None, float]:
    """Best open X# for this face. None if new."""
    e = _norm(emb)
    if e is None:
        return None, -1.0
    root = root or stranger_root()
    if not root.is_dir():
        return None, -1.0
    best_id: str | None = None
    best = -1.0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name.lower()
        if not is_stranger_id(name):
            continue
        pj = child / "person.json"
        try:
            rec = json.loads(pj.read_text(encoding="utf-8")) if pj.is_file() else {}
        except (OSError, ValueError):
            rec = {}
        if rec.get("identified"):
            continue
        for fe in _load_embs(child):
            s = _cosine(e, fe)
            if s > best:
                best = s
                best_id = name if name.startswith("x") else f"x{child.name[1:]}"
    if best_id and best >= MATCH_SIM:
        return best_id, round(best, 4)
    return None, round(best, 4)


def list_strangers(*, root: Path | None = None) -> list[dict[str, Any]]:
    """Open X# folders for Inspector. Not occupancy."""
    root = root or stranger_root()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not is_stranger_id(child.name):
            continue
        rec: dict[str, Any] = {}
        pj = child / "person.json"
        try:
            rec = json.loads(pj.read_text(encoding="utf-8")) if pj.is_file() else {}
        except (OSError, ValueError):
            rec = {}
        xid = str(rec.get("id") or child.name).strip().lower()
        rows.append(
            {
                "id": xid,
                "display": rec.get("display") or display_of(xid),
                "role": "stranger",
                "identified": rec.get("identified"),
                "n_events": int(rec.get("n_events") or 0),
                "kinds": list(rec.get("kinds") or []),
                "opened_ts": rec.get("opened_ts"),
                "last_ts": rec.get("last_ts"),
                "last_kind": rec.get("last_kind"),
                "photo": f"/v1/strangers/{xid}/photo.jpg" if _last_photo(child) else None,
            }
        )
    return rows


def _last_photo(folder: Path) -> Path | None:
    photos = folder / "photos"
    if not photos.is_dir():
        return None
    jpgs = sorted(photos.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    return jpgs[-1] if jpgs else None


def photo_bytes(xid: str, *, root: Path | None = None) -> bytes | None:
    n = str(xid or "").strip().lower()
    if not is_stranger_id(n):
        return None
    folder = _folder(root or stranger_root(), n)
    p = _last_photo(folder)
    if p is None:
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def _static(emb: np.ndarray) -> bool:
    try:
        from .static_faces import killbook

        hit, _ = killbook().is_static(emb)
        return bool(hit)
    except Exception:
        return False


def _embed_jpeg(jpeg: bytes) -> tuple[np.ndarray | None, Any, str | None]:
    """Largest usable face in a still. Returns (emb, bbox, sex)."""
    if not jpeg:
        return None, None, None
    try:
        import cv2

        from .entry_face import FACEID_MIN_PX
        from .face_engine import FaceEngine, get_face_engine
        from .static_faces import too_small_to_name

        eng = get_face_engine()
        im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return None, None, None
        dets = list(eng.detect(im) or [])
        best = None
        best_area = 0.0
        for f in dets:
            bbox = FaceEngine.bbox(f)
            if bbox is None or too_small_to_name(bbox):
                continue
            w = float(bbox[2]) - float(bbox[0])
            h = float(bbox[3]) - float(bbox[1])
            if min(w, h) < FACEID_MIN_PX:
                continue
            area = w * h
            if area <= best_area:
                continue
            emb = FaceEngine.embed(f)
            if emb is None:
                continue
            e = _norm(emb)
            if e is None:
                continue
            sx, _ = FaceEngine.gender(f)
            best = (e, [int(v) for v in bbox], sx if sx in ("M", "F") else None)
            best_area = area
        if best is None:
            return None, None, None
        return best
    except Exception:
        return None, None, None


def record_event(
    *,
    kind: str,
    jpeg: bytes | None = None,
    crop: bytes | None = None,
    emb: Any = None,
    bbox: Any = None,
    sex: str | None = None,
    source: str = "faceid",
    event_id: str | None = None,
    named_sim: float | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Write one still into an X# folder. Groups by face. Never counts."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get(
        "RAIL_STRANGER_ROOT"
    ):
        return {"ok": False, "skipped": "pytest"}
    kind_s = str(kind or "enter").strip().lower()
    if kind_s not in ("enter", "speech", "hold_auth"):
        kind_s = "enter"
    e = _norm(emb)
    # Tight crops do not re-detect (measured 0/8 faceid face crops).
    # Fallback is the full frame only. Callers should pass the emb they have.
    if e is None and jpeg:
        e, bbox2, sex2 = _embed_jpeg(jpeg)
        if bbox is None:
            bbox = bbox2
        if sex is None:
            sex = sex2
    if e is None:
        return {"ok": False, "reason": "no_face"}
    if _static(e):
        return {"ok": False, "skipped": "static"}
    still = crop or jpeg
    if not still:
        return {"ok": False, "reason": "no_jpeg"}
    root = root or stranger_root()
    now = time.time()
    with _LOCK:
        xid, xsim = match_xid(e, root=root)
        idx = _load_index(root)
        if not xid:
            n = int(idx.get("next") or 1)
            xid = f"x{n}"
            idx["next"] = n + 1
            open_ids = [str(x).lower() for x in (idx.get("open") or [])]
            if xid not in open_ids:
                open_ids.append(xid)
            idx["open"] = open_ids
            _save_index(root, idx)
            xsim = None
        folder = _folder(root, xid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "photos").mkdir(exist_ok=True)
        (folder / "face").mkdir(exist_ok=True)
        pj = folder / "person.json"
        rec: dict[str, Any] = {}
        if pj.is_file():
            try:
                rec = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rec = {}
        last_ts = float(rec.get("last_ts") or 0)
        last_kind = str(rec.get("last_kind") or "")
        last_eid = str(rec.get("last_event_id") or "")
        if event_id and last_eid == str(event_id):
            return {
                "ok": True,
                "skipped": "same_event",
                "id": xid,
                "display": display_of(xid),
            }
        if last_ts and (now - last_ts) < DEDUP_SEC and last_kind == kind_s:
            return {
                "ok": True,
                "skipped": "dedup",
                "id": xid,
                "display": display_of(xid),
            }
        seq = int(rec.get("n_events") or 0) + 1
        ms = int(now * 1000)
        stem = f"{seq:03d}_{kind_s}_{ms}"
        photo_rel = f"photos/{stem}.jpg"
        (folder / photo_rel).write_bytes(still)
        crop_rel = None
        if crop and crop is not still:
            crop_rel = f"photos/{stem}_crop.jpg"
            (folder / crop_rel).write_bytes(crop)
        elif crop:
            crop_rel = photo_rel
        n_face = len(list((folder / "face").glob("*.json")))
        if n_face < MAX_FACE_JSON:
            face_name = f"{n_face + 1:03d}.json"
            (folder / "face" / face_name).write_text(
                json.dumps(
                    {
                        "person_id": xid,
                        "modality": "face",
                        "model": "faceid",
                        "dim": int(e.size),
                        "encoding": [float(x) for x in e.tolist()],
                        "source": source,
                        "kind": kind_s,
                        "ts": now,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        kinds = list(rec.get("kinds") or [])
        if kind_s not in kinds:
            kinds.append(kind_s)
        rec.update(
            {
                "id": xid,
                "display": display_of(xid),
                "role": "stranger",
                "identified": rec.get("identified"),
                "opened_ts": rec.get("opened_ts") or now,
                "last_ts": now,
                "last_kind": kind_s,
                "last_event_id": event_id,
                "n_events": seq,
                "kinds": kinds,
            }
        )
        pj.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        row = {
            "seq": seq,
            "kind": kind_s,
            "label": (
                "Hold Auth req failed" if kind_s == "hold_auth"
                else "repeated verbal attempts" if kind_s == "speech"
                else kind_s
            ),
            "ts": now,
            "ts_local": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "photo": photo_rel,
            "crop": crop_rel,
            "source": source,
            "event_id": event_id,
            "xid": xid,
            "display": display_of(xid),
            "bbox": bbox,
            "sex": (str(sex).upper()[:1] if sex in ("M", "F", "m", "f") else None),
            "named_sim": named_sim,
            "group_sim": xsim,
        }
        with (folder / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    try:
        from .oracle_event import stamp_stranger

        stamp_stranger(
            xid,
            kind=kind_s,
            seq=seq,
            photo=photo_rel,
            source=source,
            event_id=event_id,
            sex=sex,
        )
    except Exception:
        pass
    return {
        "ok": True,
        "id": xid,
        "display": display_of(xid),
        "seq": seq,
        "kind": kind_s,
        "photo": photo_rel,
        "new": xsim is None,
        "group_sim": xsim,
    }
