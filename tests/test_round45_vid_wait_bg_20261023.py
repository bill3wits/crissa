"""Round 45 (.101 rounds 43 and 44: "Waiting for this computer's setup to finish" never showed - the walk leaves This
computer at once, so step_id is already voice_id while Voice ID waits). The line is keyed on the background check's own
state: This computer answered, its background work not done (machine.background_done), no take begun. Kill
CRISSA_VID_WAIT_BG=0 (round 43's step rule)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import pytest  # noqa: E402

import crissa_init as zi  # noqa: E402

LINE = "Waiting for this computer's setup to finish"


@pytest.fixture(autouse=True)
def _parallel(monkeypatch):
    monkeypatch.delenv("CRISSA_VID_WAIT_SAYS", raising=False)
    monkeypatch.delenv("CRISSA_VID_WAIT_BG", raising=False)
    monkeypatch.setattr(zi, "setup_parallel", lambda st=None: True)      # a pack's first boot: This computer in the back
    monkeypatch.setitem(zi._GLASS, "phase", "idle")


def _st(step="voice_id", **machine):
    m = {"name_taken": {"display": "Sam"}, "mic_heard": True, "permission": "allow"}
    m.update(machine)
    return {"status": "running", "step_id": step, "steps_done": ["connect", "machine"], "machine": m}


def test_the_walk_on_voice_id_says_it_waits_while_the_background_runs():
    assert zi.one_page_sections(_st())["vid"].get("wait") == LINE         # .101's case: step_id is voice_id
    assert zi.one_page_sections(_st("machine"))["vid"].get("wait") == LINE


def test_not_once_the_background_is_done_nor_once_a_take_began(monkeypatch):
    assert "wait" not in zi.one_page_sections(_st(background_done="2026-10-23T08:00:00"))["vid"]
    monkeypatch.setitem(zi._GLASS, "phase", "turn")
    assert "wait" not in zi.one_page_sections(_st())["vid"]


def test_not_before_this_computer_is_answered():
    assert "wait" not in zi.one_page_sections(_st("machine", permission="", state="ask"))["vid"]


def test_not_on_a_walk_with_no_background():
    assert "wait" not in zi.one_page_sections(dict(_st(), step_id="face"))["vid"]


def test_the_kill_is_round_43(monkeypatch):
    monkeypatch.setenv("CRISSA_VID_WAIT_BG", "0")
    assert "wait" not in zi.one_page_sections(_st())["vid"]
    assert zi.one_page_sections(_st("machine"))["vid"].get("wait") == LINE
