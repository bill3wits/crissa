#!/usr/bin/env python3
"""Restore quarantined WeSpeaker prints into live people/<id>/voice/.

The 2026-08-24 Inspector purge moved the live WeSpeaker bank (dim 256) into
people/_quarantine-inspector-purge-20260824/. rail-stt HOUSE_VOICE_ENCODER is
wespeaker, so load_voice_bank() dropped every remaining print (ECAPA-192).

This copies **only** WeSpeaker family json (dim 256, encoding present) into
the live voice dir. Nested voice/quarantine/*.json is flattened — the loader
does not recurse. ECAPA numbered takes (peak 32768 GUI museum) stay put.

Does not delete the quarantine tree.
"""
from __future__ import annotations

try:
    from . import paths as _paths
except ImportError:  # run as a file, not a module
    import paths as _paths

import json
import shutil
import sys
from pathlib import Path

HOUSE_ID = _paths.identity_root()
PURGE = HOUSE_ID / "people" / "_quarantine-inspector-purge-20260824"
SKIP_IDS = {
    "crissa_her_tts",
    "crissa-her-tts",
    "tts_echo",
    "announcer",
    "crew_tts",
    "s1",
    "s2",
}


def _family(rec: dict) -> str:
    model = str(rec.get("model") or rec.get("encoder") or rec.get("family") or "").lower()
    if "wespeaker" in model or model.startswith("ws/") or "campplus" in model:
        return "wespeaker"
    if "ecapa" in model or "speechbrain" in model or "spkrec" in model:
        return "ecapa"
    dim = rec.get("dim")
    if dim == 256:
        return "wespeaker"
    if dim == 192:
        return "ecapa"
    return "unknown"


def iter_wespeaker(purge: Path):
    for person in sorted(p for p in purge.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if person.name.lower() in SKIP_IDS:
            continue
        for f in sorted(person.joinpath("voice").rglob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if rec.get("role") == "tts_filter":
                continue
            if rec.get("modality") not in (None, "voice"):
                continue
            enc = rec.get("encoding") or rec.get("embedding") or rec.get("embedding_ecapa")
            dim = rec.get("dim") or (len(enc) if enc else 0)
            if _family(rec) != "wespeaker" or int(dim) != 256 or not enc:
                continue
            yield person.name.lower(), f, rec


def restore(purge: Path = PURGE, people: Path | None = None, *, dry: bool = False) -> dict:
    people = people or (HOUSE_ID / "people")
    copied: list[dict] = []
    skipped: list[dict] = []
    if not purge.is_dir():
        raise SystemExit(f"quarantine missing: {purge}")
    for pid, src, rec in iter_wespeaker(purge):
        dest_dir = people / pid / "voice"
        dest = dest_dir / src.name
        item = {
            "person_id": pid,
            "src": str(src),
            "dest": str(dest),
            "model": rec.get("model"),
            "dim": rec.get("dim"),
            "source": rec.get("source"),
        }
        if dest.is_file():
            item["skipped"] = "exists"
            skipped.append(item)
            continue
        if dry:
            item["skipped"] = "dry"
            copied.append(item)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(item)
    counts: dict[str, int] = {}
    for row in copied:
        if row.get("skipped"):
            continue
        counts[row["person_id"]] = counts.get(row["person_id"], 0) + 1
    return {"ok": True, "copied": copied, "skipped": skipped, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    dry = "--dry" in (argv or sys.argv[1:])
    out = restore(dry=dry)
    print(json.dumps(out, indent=2))
    return 0 if out.get("counts") or out.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
