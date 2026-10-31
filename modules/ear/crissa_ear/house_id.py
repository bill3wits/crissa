"""Read-only identity helpers: the identity store's people/<id>/ (2026-10-10: ONE store).

The store is the one Init's Voice ID writes and the presence and id modules read: CRISSA_IDENTITY, else
HOUSE_ID_ROOT, else <crissa home>/identity (crissa_id.paths.identity_root, crissa_presence.paths.IDENTITY). Until
2026-10-10 the ear read <crissa home>/ear/identity, a tree nothing wrote, so a fresh install's prints never reached
the matcher. Voice emb load walks people/<id>/voice/*.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .home import EAR, HOME

GENERIC_USER = "user"
_USER_CACHE: dict = {}


def house_id_root() -> Path:
    env = (os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    return HOME / "identity"


def user_id(ttl: float = 5.0) -> str:
    """The User's id, by the house's rule (lib/house_user, crissa_presence.house_user): HOUSE_USER_ID, else
    RAIL_PRIMARY_USER, else the one person in the identity store whose person.json says role "user" (the person
    Init's Voice ID made the User), else the generic "user". Read again at most every `ttl` seconds, so a User Init
    enrols while the ear runs is the User on the next turn."""
    env = (os.environ.get("HOUSE_USER_ID") or os.environ.get("RAIL_PRIMARY_USER") or "").strip().lower()
    if env:
        return env
    import time as _t
    now = _t.monotonic()
    if _USER_CACHE.get("at") is not None and now - _USER_CACHE["at"] < ttl:
        return _USER_CACHE["id"]
    uid = GENERIC_USER
    try:
        for pj in sorted(people_dir().glob("*/person.json")):
            rec = json.loads(pj.read_text(encoding="utf-8")) or {}
            if rec.get("active") is False:
                continue
            if str(rec.get("role") or "").strip().lower() == "user":
                uid = str(rec.get("id") or pj.parent.name).strip().lower() or GENERIC_USER
                break
    except Exception:  # noqa: BLE001 - an unreadable store is the generic User
        uid = GENERIC_USER
    _USER_CACHE.update(at=now, id=uid)
    return uid


def mouths_root() -> Path:
    env = (os.environ.get("HOUSE_MOUTHS_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    return EAR / "mouths"


def people_dir() -> Path:
    return house_id_root() / "people"


def index_ids() -> list[str]:
    """Active person ids from _index.json or directory names."""
    d = people_dir()
    idx = d / "_index.json"
    if idx.is_file():
        try:
            data = json.loads(idx.read_text())
            out = []
            for p in data.get("people") or []:
                if p.get("active", True) and p.get("id"):
                    out.append(str(p["id"]).lower().strip())
            if out:
                return out
        except Exception:
            pass
    if not d.is_dir():
        return []
    return sorted(
        p.name.lower()
        for p in d.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_"))
    )


def voice_bank_counts() -> dict[str, int]:
    """id -> live matching prints (same encoder filter as stamp).

    A glob of voice/*.json mixed ECAPA 192-d numbered files with WeSpeaker
    ws_enroll_* and reported 23 for the User while the live bank was 7.
    """
    from .voice_bank import load_voice_bank

    return {k: len(v) for k, v in load_voice_bank().items()}
