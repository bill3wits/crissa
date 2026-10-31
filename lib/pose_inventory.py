"""Character-kit inventory — SoT for pose keys (not the HTML).

Attach law (the User 2026-08-29, no exceptions): loops close on their tails;
slides are a reversible file pair; interiors free and one-way. Not a
palindrome. Law 3: mouth box when visible in ZFRAME; omit when not.
See docs/POSE-ATTACH-LAW.md.

display/crissa-poses/inventory.json is the dump of measured wall state.
GET /v1/pose adds etag + content-hash src. Glass consumes that; do not
edit voiceonly-home.html to add a clip.

etag is sha256 over inventory bytes plus each mp4's sha256, so a drop-in
file changes etag and src without a kiosk reload once glass rebuilds on hash.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POSE_DIR = Path(os.environ.get("HOUSE_POSE_DIR", str(ROOT / "display" / "crissa-poses")))   # HOUSE_POSE_DIR: a second face is a second library (2026-09-11)
INV_PATH = POSE_DIR / "inventory.json"
ROLES = ("loop", "slide", "glance", "flourish", "oneshot")
SHA_LEN = 12
# One-shot specialist cue. An agent posts emote-surprise / emote-woe; glass
# plays when she is already on that hub. Not a second scheduler.
CUE_PATH = Path("/tmp/voiceonly-pose-cue.json")
_cue_lock = threading.Lock()

_inv_cache: dict[str, Any] | None = None
_inv_mtime: float | None = None
_sha_cache: dict[str, tuple[float, str]] = {}


def _sha256_file(path: Path) -> str:
    st = path.stat()
    key = str(path)
    hit = _sha_cache.get(key)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _sha_cache[key] = (st.st_mtime, digest)
    return digest


def load(path: Path | None = None) -> dict[str, Any]:
    """Raw inventory.json (no etag/src)."""
    global _inv_cache, _inv_mtime
    p = path or INV_PATH
    mtime = p.stat().st_mtime
    if _inv_cache is not None and _inv_mtime == mtime and path is None:
        return _inv_cache
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema") != "character_kit/v1":
        raise ValueError(f"bad inventory schema: {data.get('schema')}")
    clips = data.get("clips") or {}
    if not clips:
        raise ValueError("inventory clips empty")
    for key, row in clips.items():
        role = row.get("role")
        if role not in ROLES:
            raise ValueError(f"{key}: role {role!r} not in {ROLES}")
        if not row.get("file"):
            raise ValueError(f"{key}: missing file")
        mouth = row.get("mouth")
        if mouth is not None:
            if (
                not isinstance(mouth, list)
                or len(mouth) != 4
                or not all(isinstance(v, (int, float)) for v in mouth)
            ):
                raise ValueError(f"{key}: mouth must be [x,y,w,h]")
    if path is None:
        _inv_cache = data
        _inv_mtime = mtime
    return data


def pose_clips(inv: dict[str, Any] | None = None) -> dict[str, str]:
    """Thin key → filename map (replaces the old POSE_CLIPS dict)."""
    data = inv if inv is not None else load()
    return {k: str(row["file"]) for k, row in data["clips"].items()}


def pose_payload(pose_dir: Path | None = None, inv: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET /v1/pose body. Hashes live files; inventory itself is not mutated."""
    data = inv if inv is not None else load()
    root = pose_dir or POSE_DIR
    clips_out: dict[str, Any] = {}
    file_shas: list[str] = []
    for key, row in data["clips"].items():
        fname = str(row["file"])
        path = root / fname
        sha = _sha256_file(path) if path.is_file() else "missing"
        file_shas.append(f"{key}:{sha}")
        rec = {
            "file": fname,
            "src": f"/pose/{key}?v={sha[:SHA_LEN]}",
            "role": row["role"],
            "start": row.get("start"),
            "end": row.get("end"),
            "scheduled": bool(row.get("scheduled")),
            "element": bool(row.get("element", True)),
            "preload": row.get("preload") or "auto",
        }
        if "pool" in row:
            rec["pool"] = row["pool"]
        if row.get("mouth") is not None:
            rec["mouth"] = [int(v) for v in row["mouth"]]
        if row.get("mouth_sidecar"):
            rec["mouth_sidecar"] = str(row["mouth_sidecar"])
        if row.get("child_path"):
            rec["child_path"] = str(row["child_path"])
        if row.get("undo"):
            rec["undo"] = str(row["undo"])
        if row.get("keeper"):
            rec["keeper"] = True
        if row.get("category"):
            rec["category"] = str(row["category"])
        if row.get("label"):
            rec["label"] = str(row["label"])
        clips_out[key] = rec
    etag_src = (INV_PATH.read_bytes() if INV_PATH.is_file() else b"") + (
        "\n".join(file_shas).encode()
    )
    etag = hashlib.sha256(etag_src).hexdigest()[:16]
    out = {
        "schema": data["schema"],
        "agent": data["agent"],
        "etag": etag,
        "size": data.get("size") or [736, 400],
        "fps": data.get("fps") or 24,
        "hubs": data.get("hubs") or {},
        "zframe": data.get("zframe") or {},
        "pools": data.get("pools") or {},
        "clips": clips_out,
        "cue": cue_get(),
    }
    if data.get("child_paths"):
        out["child_paths"] = data["child_paths"]
    return out


def cue_allowed(key: str, inv: dict[str, Any] | None = None) -> bool:
    """Only emote-* specialists with an on-wall element. Not the fidget roll."""
    data = inv if inv is not None else load()
    if not str(key).startswith("emote-"):
        return False
    row = (data.get("clips") or {}).get(key)
    if not isinstance(row, dict):
        return False
    return bool(row.get("element", True))


def cue_set(key: str) -> dict[str, Any]:
    key = str(key or "").strip()
    if not cue_allowed(key):
        return {"ok": False, "error": "not an emote key", "key": key}
    rec = {
        "key": key,
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
    }
    with _cue_lock:
        CUE_PATH.write_text(json.dumps(rec), encoding="utf-8")
    rec["ok"] = True
    return rec


def cue_get() -> dict[str, Any] | None:
    with _cue_lock:
        if not CUE_PATH.is_file():
            return None
        try:
            rec = json.loads(CUE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    if not isinstance(rec, dict) or not rec.get("key") or not rec.get("id"):
        return None
    return {"key": str(rec["key"]), "id": str(rec["id"])}


def cue_ack(cid: str) -> bool:
    cid = str(cid or "")
    with _cue_lock:
        if not CUE_PATH.is_file():
            return True
        try:
            rec = json.loads(CUE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            CUE_PATH.unlink(missing_ok=True)
            return True
        if str(rec.get("id") or "") == cid or not cid:
            CUE_PATH.unlink(missing_ok=True)
            return True
    return False
