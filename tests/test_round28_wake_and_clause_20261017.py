"""Round 28 (.101 2026-10-17 05:31, ASK 16): "Hey, Crissa." then "Help me plan dinner." a second later - her "Yeah?" went
on the air over his ask, and her 88-character question was said as 71 characters and then, 6 s later, "you have on
hand?". No ack when he goes on speaking after the wake; a first sentence keeps its last words."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_bridge import ttfs  # noqa: E402

CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text()
EAR = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()


def _first(full: str, step: int = 4) -> str | None:
    for k in range(10, len(full) + 1, step):
        c = ttfs.first_speakable_clause(full[:k])
        if c:
            return c
    return None


def test_101s_question_is_said_whole(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_CLAUSE_CUT_CHARS", raising=False)
    q = "Understood, what are you in the mood for tonight or what ingredients do you have on hand?"
    assert _first(q + " I can suggest a few.") == q


def test_a_long_run_on_is_cut_at_its_comma(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_CLAUSE_CUT_CHARS", raising=False)
    s = ("I looked at the three options you sent over this morning and the second one, which uses the slower cooker, "
         "seems best for tonight given the time")
    got = _first(s)
    assert got and got.endswith("slower cooker,") and len(got) <= 120


def test_the_kill_is_round_27s_cut(monkeypatch):
    monkeypatch.setenv("EAR_BRIDGE_CLAUSE_CUT_CHARS", "72")
    q = "Understood, what are you in the mood for tonight or what ingredients do you have on hand?"
    assert _first(q) == "Understood, what are you in the mood for tonight or what ingredients do"


def _ear_fn(name):
    src = EAR[EAR.index("def ack_wait_s"):EAR.index("def bare_wake_ack")]
    ns = {"os": __import__("os"), "time": __import__("time"), "ear_still_hearing": lambda: False}
    exec(src, ns)
    return ns[name]


def test_he_goes_on_speaking():
    asked = _ear_fn("asked_after_wake")
    t = {"now": 0.0}
    clock = lambda: t["now"]  # noqa: E731

    def sleep(s):
        t["now"] += s
    calls = iter([False, False, True])
    assert asked(1.2, hearing=lambda: next(calls), sleep=sleep, clock=clock) is True
    t["now"] = 0.0
    assert asked(1.2, hearing=lambda: False, sleep=sleep, clock=clock) is False, "a bare name: the ack is said"
    assert asked(0.0, hearing=lambda: True) is False, "the kill"


def test_both_ack_paths_ask_first():
    body = EAR[EAR.index("def bare_wake_ack"):EAR.index("# ---- round 14 (2026-10-12)")]
    assert body.count("asked_after_wake(ack_wait_s())") == 2
    assert "_post_json(CONDUCTOR_GREET_CANCEL" in body
    assert body.index("if not addressed and (meta.get(\"has_ask\") or asked_after_wake") < body.index("speak_blocking(")   # round 29: .101's has_ask


def test_the_conductor_holds_its_greet_in_a_pack():
    assert 'WAKE_GREET_HOLD_S = float(os.environ.get("HOUSE_WAKE_GREET_HOLD_S", house_default("0", "0.8")) or 0)' in CONDUCTOR
    assert len(re.findall(r'if head == "/v1/greet-cancel":', CONDUCTOR)) == 1
    assert 'STATE["vocative_ts"] = time.time()' in CONDUCTOR
    assert 'if float(STATE.get("greet_cancel_ts") or 0.0) >= woke > 0:' in CONDUCTOR
