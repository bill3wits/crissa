"""Roster + bank status for this identity-store tree."""

from __future__ import annotations

import json

from .paths import house_id_root
from .person import face_bank_files, list_people, voice_bank_files
from .site import load_site


def print_status() -> int:
    root = house_id_root()
    print(f"HOUSE_ID_ROOT={root}")
    try:
        site = load_site(root)
        print(f"site={site.site_id}  ({site.display})")
        print(f"mic={site.mic}")
        print(f"room_refs={site.room_refs_dir()}")
    except Exception as e:
        print(f"site: {e}")

    print("\npeople:")
    rows = list_people(root)
    if not rows:
        print("  (none)")
        return 0
    for p in rows:
        pid = p["id"]
        active = p.get("active", True)
        v = voice_bank_files(pid, root)
        f = face_bank_files(pid, root)
        peaks = []
        for vf in v:
            try:
                peaks.append(str(json.loads(vf.read_text()).get("peak", "?")))
            except Exception:
                peaks.append("?")
        flag = "" if active else " [inactive]"
        print(
            f"  {pid:12} face={len(f):2}  voice={len(v):2}"
            f"  peaks=[{','.join(peaks)}]{flag}"
        )
    return 0
