"""The pack's own records of Init, beside the identity store (2026-10-09).

The player, the presence service and the oracle read three small files under the state home (the shapes the
retired stand-in lib/crissa_init wrote; kept byte-compatible):

    init.json     {schema: "crissa-init/1", done, at, version, steps_done, service[, connect][, cancelled_at]}; missing =
                  first boot. connect = {"state": "tested"|"later", "at"} (Init v2's Connect, 2026-10-10)
    user.json     {schema: "crissa-user/1", name, person_id, role: "user", at}
    enroll.json   {schema: "crissa-enroll/1", owner, faces: [[...512 numbers...], ...], updated_at}, mode 0600

The identity store (people/<id>/face/*.json, people/<id>/voice/*.json) is the enrolment itself; enroll.json carries
the same face numbers so lib/crissa_presence and modules/oracle keep one enrolment. No picture, no voice sample here.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import paths as _paths

MARKER_SCHEMA = "crissa-init/1"
USER_SCHEMA = "crissa-user/1"
ENROLL_SCHEMA = "crissa-enroll/1"
VERSION = 1
MAX_FACES = 12


def home() -> Path:
    return _paths.STATE


def marker_path() -> Path:
    return home() / "init.json"


def user_path() -> Path:
    return home() / "user.json"


def enroll_path() -> Path:
    return home() / "enroll.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read(p: Path) -> dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict[str, Any], mode: int | None = None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("." + p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, p)
    if mode is not None:
        os.chmod(p, mode)


def marker() -> dict[str, Any]:
    return _read(marker_path())


def first_boot() -> bool:
    """No marker = a first boot. A marker that is not done (a cancelled walk) is still a first boot."""
    return not bool(marker().get("done"))


def write_marker(*, done: bool, steps_done: list[str] | None = None, service: Any = None,
                 cancelled: bool = False, connect: dict[str, Any] | None = None) -> dict[str, Any]:
    d = marker()
    d.update(schema=MARKER_SCHEMA, done=bool(done), at=_now(), version=VERSION,
             steps_done=list(steps_done or d.get("steps_done") or []))
    if service is not None:
        d["service"] = service
    if connect is not None:
        d["connect"] = connect           # {"state": "tested"|"later", "at"}: how Connect ended (the User 2026-10-10)
    if cancelled:
        d["cancelled_at"] = _now()
    _write(marker_path(), d)
    return d


def slug(name: str) -> str:
    s = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(name or "").strip())
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")[:48]


def write_user(name: str, person_id: str = "", **fields: Any) -> dict[str, Any]:
    d = _read(user_path())
    d.update(schema=USER_SCHEMA, name=str(name or "").strip(), person_id=str(person_id or slug(name)),
             role="user", at=_now(), **fields)
    _write(user_path(), d)
    return d


def user() -> dict[str, Any]:
    return _read(user_path())


def write_enroll(owner: str, faces: list[list[float]]) -> dict[str, Any]:
    d = {"schema": ENROLL_SCHEMA, "owner": slug(owner), "faces": [list(map(float, v)) for v in faces][-MAX_FACES:],
         "updated_at": _now()}
    _write(enroll_path(), d, mode=0o600)
    return d


def add_face(owner: str, vector: list[float]) -> int:
    """One more face for the owner (the enrol tools call this beside each people/<id>/face/*.json they write)."""
    d = _read(enroll_path())
    faces = [v for v in (d.get("faces") or []) if isinstance(v, list)] if slug(d.get("owner") or "") == slug(owner) else []
    faces.append(list(map(float, vector)))
    write_enroll(owner, faces)
    return min(len(faces), MAX_FACES)


def enrolled_faces() -> list[list[float]]:
    return [v for v in (_read(enroll_path()).get("faces") or []) if isinstance(v, list)]


def sync_from_store(person_id: str, root: Path | None = None) -> int:
    """enroll.json from the identity store's own prints for the primary User (role "user" in person.json): the
    same AuraFace numbers Register stored through the presence service land where the player and the oracle read
    them. Another person's prints never go there. Returns the number of faces written (0 = not the User / none)."""
    pid = str(person_id or "").strip().lower()
    root = Path(root) if root is not None else _paths.identity_root()
    person = root / "people" / pid
    rec = _read(person / "person.json")
    if not pid or rec.get("role") != "user":
        return 0
    faces: list[list[float]] = []
    for f in sorted((person / "face").glob("*.json")):
        d = _read(f)
        enc = d.get("encoding") or d.get("embedding")
        if isinstance(enc, list) and len(enc) >= 8 and str(d.get("model") or "").startswith("auraface"):
            try:
                faces.append([float(x) for x in enc])
            except (TypeError, ValueError):
                continue
    if faces:
        write_enroll(pid, faces[-MAX_FACES:])
    return min(len(faces), MAX_FACES)
