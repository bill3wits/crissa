"""Ada/Crissa: speak phonetically, type and log the real name."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parents[3] / "lib" / "speakable.py"
    spec = importlib.util.spec_from_file_location("house_speakable_test", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_for_display_keeps_ada():
    sp = _load()
    assert "Ada" in sp.for_display("Assigned to Ada.")
    assert "ā′də" not in sp.for_display("Assigned to Ada.")
    assert "A-da" not in sp.for_display("Assigned to Ada.")


def test_for_speech_says_ada_phonetic():
    sp = _load()
    said = sp.for_speech("Assigned to Ada.")
    assert "Ada" not in said
    assert "ā′də" in said


def test_for_display_restores_leaked_phonetics():
    sp = _load()
    assert sp.for_display("task for ā′də and Zoh-ee") == "task for Ada and Crissa"
    assert "Ada" in sp.for_display("filed for A-da'")


def test_for_display_keeps_crissa():
    sp = _load()
    assert "Crissa" in sp.for_display("Hey Crissa")
    assert "Zoh-ee" not in sp.for_display("Hey Crissa")
