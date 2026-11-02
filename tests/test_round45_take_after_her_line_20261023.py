"""Round 45 (.101 round 43 R43-1: "count 1/4 take 1: voiced 48% peak 806 - heard 0 number words ('Hi, I'm Lila. I'll
set Chrissa up with y')"): in an IDs run the routine's vid-facing won the page and the page's own welcome followed it,
over count 1's first take. Now (1) before every GO beep the routine waits while a film of the page's is still on the air
(its own length + slack at most), and (2) the page holds its own lines while the routine is in its takes, and an IDs run
whose takes began drops the welcome. Kill CRISSA_TAKE_AFTER_HER_LINE=0 (both halves). Both routine copies."""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
PKGS = [("crissa_presence", "presence", "crissa_presence.enroll_panel"), ("crissa_id", "id", "crissa_id.house_enroll")]
PAGE = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")


@pytest.fixture(params=PKGS, ids=["presence", "id"])
def vr(request, monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CRISSA_TAKE_AFTER_HER_LINE", raising=False)
    top, mdir, base = request.param
    for k in [k for k in sys.modules if k == top or k.startswith(top + ".")]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.syspath_prepend(str(ROOT / "modules" / mdir))
    mod = importlib.import_module(base + ".voice_register")
    monkeypatch.setattr(mod, "_cancelled", lambda: False)
    monkeypatch.setattr(mod, "films_on", lambda: True)
    monkeypatch.setattr(mod, "FILM_POLL_S", 0.0)
    marks = []
    monkeypatch.setattr(mod.cues, "mark_spoken", lambda k: marks.append(k))
    monkeypatch.setattr(mod.cues, "wait_for_beep", lambda lead=0.0: marks.append("beep") or 0.0)
    mod._marks = marks
    return mod


def _air(mod, monkeypatch, aheads):
    seq = list(aheads)
    monkeypatch.setattr(mod, "_film_said_at", lambda: (1, seq.pop(0) if len(seq) > 1 else seq[0], 0.0))


def test_the_beep_waits_while_her_line_sounds(vr, monkeypatch):
    _air(vr, monkeypatch, [2.5, 1.2, 0.3, 0.0])
    vr._beep_wait(0.1)
    assert vr._marks == [0.0, "beep"], "the beep keeps its gap from her line's end"


def test_nothing_on_the_air_is_no_wait(vr, monkeypatch):
    _air(vr, monkeypatch, [0.0])
    vr._beep_wait(0.1)
    assert vr._marks == ["beep"]


def test_the_wait_ends_at_the_films_own_length(vr, monkeypatch):
    _air(vr, monkeypatch, [0.05])                    # a page that never says it ended: its length + slack, then on
    monkeypatch.setattr(vr, "FILM_WAIT_SLACK_S", 0.05)
    assert 0.0 < vr._her_line_clear() < 2.0


def test_every_beep_of_the_routine_goes_through_it(vr):
    src = Path(vr.__file__).read_text(encoding="utf-8")
    assert "cues.wait_for_beep(PRE_BEEP_S)" not in src and src.count("_beep_wait(PRE_BEEP_S)") >= 6


def test_the_kill_is_round_44(vr, monkeypatch):
    monkeypatch.setenv("CRISSA_TAKE_AFTER_HER_LINE", "0")
    _air(vr, monkeypatch, [5.0])
    vr._beep_wait(0.1)
    assert vr._marks == ["beep"]


def test_the_page_holds_its_own_lines_while_a_take_runs():
    tick = PAGE.split("function opTick()", 1)[1].split("\nfunction ", 1)[0]
    hold = tick.index("OP_TAKE_PHASES.includes")
    assert tick.index("return opLine(bus.id") < hold < tick.index('return opLine("welcome")')
    assert hold < tick.index('opLine("already"') and hold < tick.index('opLine("wait")')
    assert re.search(r'const OP_TAKE_PHASES = \["watch", "read", "turn", "thanks"\]', PAGE)
    assert 'S.snap.ids && (S.op.played || []).length && !S.op.said.has("welcome:")' in tick


def test_the_view_carries_the_switch(monkeypatch):
    import crissa_init as zi
    monkeypatch.delenv("CRISSA_TAKE_AFTER_HER_LINE", raising=False)
    assert zi.one_page_view({})["takes_hold"] is True
    monkeypatch.setenv("CRISSA_TAKE_AFTER_HER_LINE", "0")
    assert zi.one_page_view({})["takes_hold"] is False
    assert set(("watch", "read", "turn", "thanks")) <= set(zi.REGISTER_PHASES)
