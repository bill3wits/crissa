"""The house's User id, from the record Init writes - never a name in code (the User 2026-10-02, Init step 5).

HOUSE_USER_ID, else RAIL_PRIMARY_USER, else the one person in the identity store (HOUSE_ID_ROOT) whose person.json
says role "user", else the generic "user".
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
from pathlib import Path

GENERIC = "user"


def user_id() -> str:
    uid = (os.environ.get("HOUSE_USER_ID") or os.environ.get("RAIL_PRIMARY_USER") or "").strip().lower()
    if uid:
        return uid
    root = Path(os.environ.get("HOUSE_ID_ROOT") or str(_paths.IDENTITY))
    try:
        for pj in sorted((root / "people").glob("*/person.json")):
            if (json.loads(pj.read_text(encoding="utf-8")) or {}).get("role") == "user":
                return pj.parent.name.lower()
    except Exception:  # noqa: BLE001 - an unreadable store is the generic User
        pass
    try:
        from .enroll_store import bad_id, read_enroll
        owner = str(read_enroll().get("owner") or "").strip().lower()
        if owner and not bad_id(owner):
            return owner               # the owner Init enrolled (<state home>/enroll.json), 2026-10-09
    except Exception:  # noqa: BLE001
        pass
    return GENERIC


def enrolled() -> bool:
    """A User is enrolled once the identity store holds a person whose role is "user" (an env id alone is not one).
    Before that - first boot - there is nobody to sign in as (the User 2026-10-03). Unreadable = enrolled."""
    root = Path(os.environ.get("HOUSE_ID_ROOT") or str(_paths.IDENTITY))
    try:
        for pj in sorted((root / "people").glob("*/person.json")):
            if (json.loads(pj.read_text(encoding="utf-8")) or {}).get("role") == "user":
                return True
        from .enroll_store import read_enroll
        return bool((read_enroll().get("faces") or []))     # Init's enrollment counts (2026-10-09)
    except Exception:  # noqa: BLE001 - never read a broken store as "no User yet"
        return True
