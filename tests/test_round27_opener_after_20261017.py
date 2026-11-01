"""Round 27: her opener starts after the walk-in (.101 2026-10-16: the first two lines were said while she walked in,
her mouth unpainted; the User: "I missed the first two sentences")."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_init as zi  # noqa: E402

SRC = (ROOT / "lib" / "crissa_init.py").read_text()


def test_her_first_line_waits_for_her_to_land_heads_up():
    i = SRC.index("def _team_end_two(")
    body = SRC[i:SRC.index("def _team_end_walk(", i)]
    up = body.index("drawn = _entrance_then_up()")
    assert body.index("_close_setup_window()") < up < body.index('"Crissa introduces herself"')
    assert up < body.index("_crissa_says(line)")


def test_the_kill_is_round_26(monkeypatch):
    monkeypatch.delenv("CRISSA_OPENER_AFTER_WALK_IN", raising=False)
    assert zi.opener_after_on() is True
    monkeypatch.setenv("CRISSA_OPENER_AFTER_WALK_IN", "0")
    assert zi.opener_after_on() is False
