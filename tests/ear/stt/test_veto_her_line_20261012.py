"""Round 13 (2026-10-12, .101): her own line heard back through the room speakers skips the decode in a pack.

.101's ear decoded 6 of 11 turns between 08:29 and 08:32 that were her own replies (the 13.2 s Moby-Dick reading took
5.4 s at ~7 cores) and the player's mouth lost half its rows. The player's own gate (<home>/house-tts-gate.json) names
her air window; an utterance over it, MIN_SEC or longer, skips unless the best ENROLLED person scores >= NEAR_USER
(every turn of the User's that overlapped her air on .101 scored 0.43-0.68; her echo's median was 0.16)."""
from __future__ import annotations

import json

import pytest

from crissa_ear import veto_first as vf

NOW = 1_791_808_281.0
EMB = [0.1] * 256


@pytest.fixture
def speak_gate(tmp_path, monkeypatch):
    path = tmp_path / "house-tts-gate.json"
    monkeypatch.setenv("CRISSA_SPEAK_GATE", str(path))
    monkeypatch.delenv("CRISSA_EAR_VETO_HER", raising=False)
    monkeypatch.delenv("RAIL_STT_VETO_BEFORE_DECODE", raising=False)

    def write(**kw):
        g = {"schema": "house_tts_gate/v1", "who": "crissa", "speaking": False, "updated_ts": NOW - 1.0}
        g.update(kw)
        path.write_text(json.dumps(g))
        return g
    return write


def _decide(dur=13.2, person=0.16, owner=None):
    return vf.decide(gate={"who": "crissa", "speaking": False, "updated_ts": NOW - 30.0}, dur=dur, emb=EMB, now=NOW,
                     user_sim_fn=lambda e: owner, veto_fn=lambda v, e: None, person_sim_fn=lambda e: person)


def test_her_reading_over_her_air_skips_the_decode(speak_gate):
    """The 08:31:21 turn: 13.2 s of her Moby-Dick reading while her line was on air."""
    speak_gate(speaking=True, air_at=NOW - 20.0, air_until=NOW + 25.0)
    d = _decide()
    assert d["skip"] is True and d["why"] == "her_line" and d["who"] == "her"


def test_the_user_talking_over_her_line_still_decodes(speak_gate):
    """His turns that overlapped her air on .101 scored 0.43-0.68 against his enrolled prints."""
    speak_gate(speaking=True, air_at=NOW - 20.0, air_until=NOW + 25.0)
    d = _decide(person=0.43)
    assert d["skip"] is False and d["why"] == "near"


def test_a_short_piece_of_her_line_still_decodes(speak_gate):
    speak_gate(speaking=True, air_at=NOW - 5.0, air_until=NOW + 5.0)
    assert _decide(dur=vf.MIN_SEC - 0.5)["why"] == "short"


def test_just_after_her_line_is_still_her_tail_within_the_slack(speak_gate):
    speak_gate(speaking=False, air_at=NOW - 12.0, air_until=NOW - 4.0)
    assert vf.her_on_air(5.0, NOW) is True
    speak_gate(speaking=False, air_at=NOW - 60.0, air_until=NOW - 40.0)
    assert vf.her_on_air(5.0, NOW) is False
    assert _decide()["why"] == "not_on_air"


def test_the_announcer_and_a_bench_are_not_her_line(speak_gate):
    for who in ("announcer", "crissa@bench"):
        speak_gate(who=who, speaking=True, air_at=NOW - 5.0, air_until=NOW + 5.0)
        assert vf.her_on_air(5.0, NOW) is False


def test_the_switch_puts_the_old_scope_back(speak_gate, monkeypatch):
    speak_gate(speaking=True, air_at=NOW - 20.0, air_until=NOW + 25.0)
    monkeypatch.setenv("CRISSA_EAR_VETO_HER", "0")
    assert _decide()["why"] == "not_on_air"


def test_no_gate_file_is_not_her_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_SPEAK_GATE", str(tmp_path / "absent.json"))
    assert vf.her_on_air(13.0, NOW) is False


def test_a_pack_owner_guard_reads_the_best_enrolled_person(monkeypatch):
    """A pack names its person at Init, so the roster "user" is never in its bank and the old guard read None (no guard
    at all): the bench and announcer scopes now fall back to the best enrolled person too."""
    import crissa_ear.voice_bank as vb

    def rank(emb, bank=None, *, roster=None, top_k=3):
        return [] if roster == ["user"] else [("pat", 0.61)][:top_k]
    monkeypatch.setattr(vb, "rank_voice", rank)
    assert vf._user_sim(EMB) == pytest.approx(0.61)
    assert vf._best_person_sim(EMB) == pytest.approx(0.61)
