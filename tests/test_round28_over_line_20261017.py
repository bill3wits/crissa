"""Round 28 (.101 2026-10-17 05:14, ASK 9): "Could you display on the screen?" said over her line came back PAUSE from her
model, she went PAUSED and every later ask was dropped. A request over her line is never a pause."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_bridge import over_line as OL  # noqa: E402
from crissa_bridge.wake import stop_only  # noqa: E402


def _decide(user, reply):
    return OL.decide(user, {"said": "Here is a simple white bread recipe"}, lambda msgs: reply, stop_only=stop_only)


def test_101s_request_is_answered_after_her_line(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_OVER_LINE_REQUEST_PAUSE", raising=False)
    got = _decide("Could you display on the screen?", "PAUSE\nUnderstood, here's a simple white bread recipe.")
    # round 29 (.101's fix, the User 2026-10-17): a request the model wanted to stop for stops her and is done now
    # (round 28 turned that PAUSE into CONTINUE, answered after her line)
    assert got["action"] == "STOP"


def test_a_hold_still_pauses(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_OVER_LINE_REQUEST_PAUSE", raising=False)
    for words in ("One second.", "Wait.", "Hang on", "Hold on a sec"):
        assert _decide(words, "PAUSE\nSure.")["action"] == "PAUSE", words


def test_stop_and_continue_are_the_models(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_OVER_LINE_REQUEST_PAUSE", raising=False)
    assert _decide("No, a different recipe.", "STOP\nOkay.")["action"] == "STOP"
    assert _decide("Nice.", "CONTINUE")["action"] == "CONTINUE"


def test_the_prompt_says_it_and_the_kill(monkeypatch):
    sys_msg = OL.messages("x", {})[0]["content"]
    assert "a question or a request is never PAUSE" in sys_msg
    monkeypatch.setenv("EAR_BRIDGE_OVER_LINE_REQUEST_PAUSE", "1")
    assert _decide("Could you display on the screen?", "PAUSE")["action"] == "PAUSE", "round 27"
