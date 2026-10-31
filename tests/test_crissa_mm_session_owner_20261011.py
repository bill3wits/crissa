"""MM (.101 2026-10-11): a question after a bare "Hey Crissa" was dropped as `session_skip_other`.

A short turn often carries no voice print (own_voice `no_emb`), so the session's owner was whatever the short wake was
stamped with, and a follow-up stamped differently was skipped: a None wake refused the User's own stamped question
(05:15:35, 05:16:07, 05:21:04), and the User's wake refused his next unstamped one (05:18:11). It was intermittent
because it only needed the two stamps to differ; the 05:21:46 answer passed because that wake landed in a session a
None wake had opened. The rule now: no stamp is never another voice, and the User's own voice takes a session an
unknown voice opened. The house's ear bridge (rail-voice) has no owner rule, so the house never had this.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))


@pytest.fixture
def eb(monkeypatch):
    import crissa_bridge.ear_bridge as eb
    monkeypatch.setattr(eb, "SESSION_OWNER_ONLY", True)
    monkeypatch.setattr(eb, "SESSION_NONE_FOLLOWS", True)
    return eb


@pytest.mark.parametrize("owner, speaker, follows, takes", [
    (None, "owner1", True, True),        # 05:15:35: a None wake, then the User's stamped question
    ("owner1", None, True, False),       # 05:18:11: the User's wake, then his unstamped question
    ("owner1", "owner1", True, False),
    (None, None, True, False),
    ("s2", "owner1", True, True),        # a scratch voice opened it: the User takes it
    ("owner1", "s2", False, False),      # another voice in the room is still not her conversation
    (None, "s2", False, False),
    ("kim", "owner1", False, False),     # a known other person's session stays theirs
])
def test_the_101_sequences(eb, owner, speaker, follows, takes):
    assert eb.session_follow_ok(owner, speaker, "What is two plus two?", "owner1") is follows
    assert eb.session_takes_owner(owner, speaker, "owner1") is takes


def test_the_user_is_matched_as_the_gate_matches_him_and_no_user_takes_nothing(eb):
    assert eb.session_takes_owner(None, "Owner1", "owner1")
    assert not eb.session_takes_owner(None, "owner1", None) and not eb.session_follow_ok(None, "owner1", "hm", None)
    assert eb.session_follow_ok("kim", "owner1", "Crissa, two plus two?", "owner1"), "speech that names her"


def test_the_kill_switch_is_the_old_rule(eb, monkeypatch):
    monkeypatch.setattr(eb, "SESSION_NONE_FOLLOWS", False)
    assert not eb.session_follow_ok(None, "owner1", "What is two plus two?", "owner1")
    assert not eb.session_follow_ok("owner1", None, "What is two plus two?", "owner1")
    assert eb.session_follow_ok(None, None, "and tomorrow?", "owner1")
    assert not eb.session_takes_owner(None, "owner1", "owner1")
    monkeypatch.setattr(eb, "SESSION_OWNER_ONLY", False)
    assert eb.session_follow_ok("kim", "s9", "anything", None)


def test_the_follow_up_passes_the_user_and_says_when_he_takes_the_session():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
    i = src.index("if not session_follow_ok(session_owner, _spk, text, _user):")
    assert src.index("_user = user_id_from_mode() if SESSION_NONE_FOLLOWS else None") < i
    j = src.index('_log({"event": "session_owner_taken", "pid": _spk, "was": session_owner})')
    assert i < j < src.index("session_owner = _spk", j)

