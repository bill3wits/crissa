"""Round 26: heads up for her opener (.101 10-16: the walk-in ended before her first line and the opener was said on
`work` / `flourish-notes`, heads down; the User: "needs to be heads up for the opener")."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_init as zi  # noqa: E402


def test_once_the_walk_in_has_landed_she_is_put_heads_up(monkeypatch):
    seen = {}
    monkeypatch.setattr(zi, "wait_drawn", lambda key, frames=12, cap_s=8.0, not_key="": seen.update(
        key=key, frames=frames, not_key=not_key) or True)
    played = []
    monkeypatch.setattr(zi, "play", lambda key, stay=False: played.append((key, stay)) or True)
    monkeypatch.setattr(zi, "pack_cfg", lambda: {"landing": {"steps": [{"cue": "listen"}]}})
    assert zi._entrance_then_up(cap_s=1.0) is True
    assert seen == {"key": "", "frames": 1, "not_key": zi.CRISSA_ENTRANCE} and played == [("listen", True)]


def test_a_walk_in_never_seen_ending_leaves_her_be(monkeypatch):
    monkeypatch.setattr(zi, "wait_drawn", lambda *a, **k: False)
    played = []
    monkeypatch.setattr(zi, "play", lambda key, stay=False: played.append(key) or True)
    assert zi._entrance_then_up(cap_s=0.1) is False and played == []


def test_the_handover_starts_it_and_the_kill(monkeypatch):
    src = (ROOT / "lib" / "crissa_init.py").read_text()
    assert "if entered and opener_up_on() and not after:" in src and "target=_entrance_then_up" in src
    monkeypatch.setenv("CRISSA_OPENER_HEADS_UP", "0")
    assert zi.opener_up_on() is False
