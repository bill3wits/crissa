"""Round 27: a short line is one Pocket piece (.101 2026-10-16: her first clause came out as two pieces and the second
started 0.58 s after the first ended, twice - "just the one pause")."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEAK = Path(__file__).resolve().parents[1] / "scripts" / "house-speak.py"
_spec = importlib.util.spec_from_file_location("house_speak_r27", _SPEAK)
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)


def test_her_first_clauses_are_one_piece_in_a_pack(monkeypatch):
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(hs, "ONE_PIECE_MAX", 80)
    assert hs.one_piece("Here's a simple recipe to bake chocolate chip cookies.")      # 55 characters
    assert not hs.one_piece("x" * 81) and not hs.one_piece("")


def test_never_on_the_house_and_the_kill(monkeypatch):
    monkeypatch.setattr(hs, "_HOUSE", True)
    assert not hs.one_piece("A short line.")
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(hs, "ONE_PIECE_MAX", 0)
    assert not hs.one_piece("A short line.")


def test_the_stream_asks_pocket_for_it_whole():
    src = _SPEAK.read_text()
    i = src.index("def voice_stream_play(")
    assert '{"chunk_first_max": 0} if one_piece(said)' in src[i:i + 1500]
