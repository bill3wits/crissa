"""The kit's copy of a file in this tree (the Crissa merge, 2026-10-11).

One tree holds the house and the kit. A file the house must keep byte for byte (a prod-guard page it serves) keeps the
house's bytes at its path; the kit's version lives under crissa/overlay/<path>, named in config/crissa-packages.json
`edition_overlay`, and scripts/crissa-export.py lays it over the path in every pack. A test about what a pack SHIPS
reads kit(rel); a test about the house reads ROOT / rel.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def overlay() -> dict:
    try:
        d = json.loads((ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (d.get("edition_overlay") or {}).items() if not str(k).startswith("_")}


def kit(rel: str) -> Path:
    """The path of the bytes a pack ships for `rel`."""
    return ROOT / overlay().get(rel, rel)


def load(rel: str, name: str):
    """Import the kit's copy of a shipped .py as a pack runs it: the overlay's bytes laid over `rel` (what the export
    does), so the module's __file__ is ROOT / rel and every path it derives from its own place resolves in this tree,
    not under crissa/overlay/. Tracebacks name the overlay file, whose lines they are."""
    import importlib.util

    src = kit(rel)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(src.read_text(encoding="utf-8"), str(src), "exec", dont_inherit=True), mod.__dict__)  # noqa: S102
    return mod
