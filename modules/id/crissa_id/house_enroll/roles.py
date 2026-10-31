"""User vs Regular enrollee roles.

Initialization is **User only**. Family / office mates are **Regulars**,
added on-demand (`add-regular`) after User is done — never required for init.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import index_path, person_dir
from .person import ensure_person, list_people, load_index, save_index
from .site import Site


def user_id(site: Site) -> str:
    """Primary User for this office — only person required for init."""
    enroll = site.raw.get("enroll") or {}
    uid = (enroll.get("user_id") or "").strip().lower()
    if uid:
        return uid
    # person.json role=user or tier=command
    for p in list_people(site.root):
        pj = person_dir(p["id"], site.root) / "person.json"
        if not pj.is_file():
            continue
        try:
            rec = json.loads(pj.read_text())
        except Exception:
            continue
        if str(rec.get("role") or "").lower() == "user":
            return p["id"]
        if str(rec.get("tier") or "").lower() == "command":
            return p["id"]
    # fallback first active in index
    people = [p for p in list_people(site.root) if p.get("active", True)]
    if people:
        return people[0]["id"]
    return "user"


def is_primary_user(site: Site, person_id: str) -> bool:
    """True only for the office User — desk monitor enroll is OK for them alone."""
    return str(person_id or "").strip().lower() == user_id(site)


def mark_role(person_id: str, role: str, *, root: Path | None = None, display: str | None = None) -> Path:
    """role: user | regular | guest"""
    role = role.strip().lower()
    if role not in ("user", "regular", "guest"):
        raise ValueError("role must be user|regular|guest")
    d = ensure_person(person_id, display=display, root=root)
    pj = d / "person.json"
    rec: dict[str, Any] = {}
    if pj.is_file():
        try:
            rec = json.loads(pj.read_text())
        except Exception:
            rec = {}
    rec["id"] = person_id.strip().lower()
    if display:
        rec["display"] = display
    rec.setdefault("display", person_id.replace("-", " ").title())
    rec["role"] = role
    if role == "user":
        rec.setdefault("tier", "command")
    elif role == "guest":
        rec.setdefault("tier", "guest")
    else:
        rec.setdefault("tier", "family")
    rec["schema"] = rec.get("schema") or "house_person/v1"
    rec["active"] = rec.get("active", True)
    pj.write_text(json.dumps(rec, indent=2) + "\n")

    # index stamp
    idx = load_index(root)
    found = False
    for row in idx.get("people") or []:
        if row.get("id") == rec["id"]:
            row["role"] = role
            row["display"] = rec["display"]
            found = True
            break
    if not found:
        idx.setdefault("people", []).append(
            {"id": rec["id"], "display": rec["display"], "active": True, "role": role}
        )
    idx["schema"] = "house_people/v1"
    save_index(idx, root)
    return d


def is_user_ready(site: Site, min_voice: int = 1) -> bool:
    from .person import voice_bank_files

    uid = user_id(site)
    return len(voice_bank_files(uid, site.root)) >= min_voice


# Spoken / coach copy — keep warm, not salesy
HANDOFF_INSPECTOR = (
    "Inspector would be happy to take it from here — learning you from the room over time. "
    "If you would like to manually register another family member now, "
    "we can ensure the highest quality recordings."
)

ADD_REGULAR_PROMPT = (
    "Add a Regular now? Say yes to enroll another person at the desk with full quality, "
    "or no to leave the rest to Inspector."
)
