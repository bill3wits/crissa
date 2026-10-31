#!/usr/bin/env python3
"""Migrate ECAPA embeddings from museum voices/ into house-id people/<id>/voice/.

Does not import Crissa. Reads JSON prints that already have embedding_ecapa.
"""
from __future__ import annotations

try:
    from . import paths as _paths
except ImportError:  # run as a file, not a module
    import paths as _paths

import json
import sys
from pathlib import Path

HOUSE = _paths.identity_root() / "people"
MUSEUM = _paths.STATE / "id" / "museum-voices"


def migrate(person: str, max_n: int = 25) -> int:
    person = person.lower().strip()
    out_dir = HOUSE / person / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)
    # clear only numbered json we create (keep .gitkeep)
    srcs = []
    for p in sorted(MUSEUM.glob(f"{person}_*.json")):
        d = json.loads(p.read_text())
        emb = d.get("embedding_ecapa")
        if not emb or len(emb) < 64:
            continue
        angle = d.get("angle_class") or d.get("mode") or ""
        # prefer on_axis first
        rank = 0 if "on" in str(angle).lower() else 1
        srcs.append((rank, p, d, emb, angle))
    srcs.sort(key=lambda x: (x[0], x[1].name))
    n = 0
    for _, p, d, emb, angle in srcs[:max_n]:
        n += 1
        rec = {
            "person_id": person,
            "modality": "voice",
            "model": d.get("encoder_ecapa") or "speechbrain/spkrec-ecapa-voxceleb",
            "dim": int(d.get("ecapa_dim") or len(emb)),
            "encoding": emb,
            "source": "museum_migrate",
            "legacy_file": p.name,
            "angle_class": angle or None,
            "note": d.get("note"),
        }
        (out_dir / f"{n:03d}.json").write_text(json.dumps(rec) + "\n")
    return n


def main():
    people = sys.argv[1:]
    if not people:
        # This house's default list lives in house-local.json, never in code.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from crissa_id.house_enroll.local_values import local

        people = list(local("migrate_people", []))
    if not people:
        sys.exit("usage: migrate_ecapa_voices.py <person_id> [...]")
    for person in people:
        n = migrate(person)
        print(f"{person}: wrote {n} voice prints → {HOUSE / person / 'voice'}")


if __name__ == "__main__":
    main()
