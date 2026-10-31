"""The owner's enrollment as numbers (2026-10-09): what Init writes and what /v1/enroll and /v1/see read.

`<state home>/enroll.json` = {"schema", "owner", "faces": [[...], ...]}, mode 0600, never a picture. The same file
Init (lib/crissa_init) and the oracle module (modules/oracle) read, so one enrollment serves the whole install. The
presence service's identity bank (enrollments.load_enrolled) folds it in as the owner's person.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

ENROLL_SCHEMA = "crissa-enroll/1"
MAX_FACES = 8
DIM_MIN = 8
DIM_MAX = 4096
PICTURE_KEYS = {"image", "jpeg", "jpg", "png", "photo", "picture", "wav", "image_b64", "webp"}
PICTURE_SUFFIX = re.compile(r"\.(jpe?g|png|webp|gif|bmp|wav|mp4)$", re.I)
BAD_ID = re.compile(r"^(unknown|crowd)(?:[:_].*)?$|^__|^(?:u|f)\d+$", re.I)


def owner_slug(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(name or ""))
    return "-".join(w.lower() for w in words)[:40].strip("-")


def bad_id(uid: str) -> bool:
    s = str(uid or "").strip().lower()
    return (not s) or bool(BAD_ID.match(s))


def _finite_row(row: Any) -> list[float] | None:
    if not isinstance(row, list) or not (DIM_MIN <= len(row) <= DIM_MAX):
        return None
    try:
        nums = [float(x) for x in row]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in nums):
        return None
    return nums


def vectors_from(raw: Any) -> list[list[float]] | None:
    """A list of number-lists, or one number-list. None when the shape is wrong."""
    if isinstance(raw, dict):
        raw = raw.get("faces", raw.get("vectors"))
    if not isinstance(raw, list) or not raw:
        return None
    if raw and isinstance(raw[0], (int, float)):
        raw = [raw]
    out: list[list[float]] = []
    for row in raw:
        nums = _finite_row(row)
        if nums is None:
            return None
        out.append(nums)
        if len(out) > MAX_FACES:
            return None
    return out


def accept_faces(body: Any) -> tuple[list[list[float]] | None, str]:
    """Numbers only. A picture key or a picture path is refused."""
    if not isinstance(body, dict):
        vecs = vectors_from(body)
        return (vecs, "") if vecs is not None else (None, "a list of numbers")
    if PICTURE_KEYS & {str(k).lower() for k in body}:
        return None, "a picture is not kept"
    path = str(body.get("path") or "")
    if path:
        if PICTURE_SUFFIX.search(path):
            return None, "a picture is not kept"
        if not path.lower().endswith(".json"):
            return None, "a json file of numbers"
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return None, "that file could not be read (%s)" % type(exc).__name__
        if isinstance(loaded, dict) and (PICTURE_KEYS & {str(k).lower() for k in loaded}):
            return None, "a picture is not kept"
        vecs = vectors_from(loaded)
        return (vecs, "") if vecs is not None else (None, "a list of numbers")
    vecs = vectors_from(body.get("faces") if "faces" in body else body.get("vectors"))
    return (vecs, "") if vecs is not None else (None, "a list of numbers")


def read_enroll(path: Path | None = None) -> dict:
    from . import paths as _paths
    try:
        doc = json.loads(Path(path or _paths.ENROLL_FILE).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write_enroll(home: Path, owner: str, faces: list[list[float]]) -> dict:
    """enroll.json, mode 0600. The owner slug and number-lists. No picture."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    doc = {"schema": ENROLL_SCHEMA, "owner": owner_slug(owner) or str(owner or "").strip().lower(),
           "faces": faces, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    path = home / "enroll.json"
    tmp = path.with_name(".enroll.json.%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return doc
