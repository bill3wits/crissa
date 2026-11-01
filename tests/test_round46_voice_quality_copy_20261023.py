"""Round 46: the voice module's quality.py carries the ear's ghost rule (its same-named copy): a short line that says
"subscribe" or a web address is no longer dropped unless it is the ghost's own shape. Kill CRISSA_EAR_GHOST_NARROW=0."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(rel):
    mod_dir, pkg = rel.split("/")[1], rel.split("/")[2]
    sys.path.insert(0, str(ROOT / "modules" / mod_dir))
    return importlib.import_module(pkg + ".quality")


def test_both_copies_keep_a_real_line_and_drop_the_ghost(monkeypatch):
    monkeypatch.delenv("CRISSA_EAR_GHOST_NARROW", raising=False)
    for rel in ("modules/ear/crissa_ear/quality.py", "modules/voice/crissa_voice/quality.py"):
        q = _load(rel)
        assert not q.is_hallucination("Crissa, subscribe me to the newsletter"), rel
        assert q.is_hallucination("Please subscribe to my channel"), rel
        monkeypatch.setenv("CRISSA_EAR_GHOST_NARROW", "0")
        assert q.is_hallucination("Crissa, subscribe me to the newsletter"), rel
        monkeypatch.delenv("CRISSA_EAR_GHOST_NARROW")
