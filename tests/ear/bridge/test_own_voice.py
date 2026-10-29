"""own_voice: Crissa must not answer herself. Replays 2026-09-30 09:15-09:16, when she heard three of her own
replies as a guest and answered each (fixtures: rail-stt's turns with their stamps, the bridge's own events)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from crissa_bridge import own_voice as ov

FIX = Path(__file__).parent / "fixtures"


def _ts(s: str) -> float:
    return dt.datetime.fromisoformat(s).timestamp()


def replay():
    """The say log as the bridge would have kept it (each `speak` event is the END of a clause; the first
    after a ttfs_clause is that clause, the next is the rest of the reply), and the turns in order."""
    events = json.loads((FIX / "own-voice-20260930-bridge.json").read_text())["events"]
    say = ov.SayLog()
    clause, reply, spoke_clause = None, "", False
    for e in events:
        if e["event"] == "ttfs_clause":
            clause, spoke_clause = e["clause"], False
        elif e["event"] == "reply":
            reply = e["reply"]
        elif e["event"] == "speak":
            end = _ts(e["ts"])
            start = end - e["ms"] / 1000.0
            if clause and not spoke_clause:
                text, spoke_clause = clause, True
            else:
                text = reply[len(clause or ""):].strip() if reply.startswith(clause or "\x00") else ""
                clause = None
            say.add(start, end, text)
    turns = json.loads((FIX / "own-voice-20260930-turns.json").read_text())["turns"]
    return say, turns


def verdicts():
    say, turns = replay()
    return {t["text"]: ov.verdict(t, say, guest=not (t.get("person_id") or t["stamp"].get("person_id")),
                                  now=t["ts"] + 0.1) for t in turns}


def test_her_three_own_lines_are_caught_whatever_their_length():
    v = verdicts()
    for line in ("There we go, love.", "You welcome love.", "I don't know that name, love, as."):
        assert v[line]["rule"] == "said_it_herself", (line, v[line])
        assert v[line]["match"] >= ov.MATCH
    # rail-stt had no embedding for the two short ones: the fingerprint veto could not have caught them
    _say, turns = replay()
    stamps = {t["text"]: t["stamp"] for t in turns}
    assert stamps["There we go, love."]["tts_veto"] is None and stamps["There we go, love."]["is_user_reason"] == "no_emb"


def test_his_own_line_before_she_spoke_passes():
    assert verdicts()["Love you, my."] == {"rule": None}


def test_a_line_that_is_not_what_she_said_is_not_matched_by_text():
    v = verdicts()
    assert v["Me too."]["rule"] is None                    # heard while she said "Love you too": not a match
    assert v["Marlowe."]["rule"] != "said_it_herself"


def test_rule_3_would_also_drop_a_short_unidentified_line_with_no_embedding():
    """The cost of "guests only pass when the voice is confidently not Crissa": `Marlowe.` had no embedding.
    This is why the filter starts report-only."""
    v = verdicts()["Marlowe."]
    assert v == {"rule": "guest_unsure", "why": "no_emb", "in_her_window": True}   # heard as her last clause ended


def test_the_long_remainder_is_hers_by_text_or_by_voice():
    v = verdicts()["Not in the record. Not in the room. Not anywhere on record here."]
    assert v["rule"] in ("said_it_herself", "voice_is_crissa")


# ---------------------------------------------------------------- the rules one by one
def _turn(text, ts=100.0, dur=2.0, **stamp):
    return {"text": text, "ts": ts, "duration_s": dur, "person_id": stamp.pop("pid", None),
            "stamp": {"person_id": None, "tts_veto": None, "is_user_reason": "uncertain", "voice_emb_sim": 0.2, **stamp}}


def _said(text, start=95.0, end=99.0):
    s = ov.SayLog()
    s.add(start, end, text)
    return s


@pytest.mark.parametrize("heard", ["Love.", "There we go, love.", "there we go", "There we go love love you"])
def test_rule_1_any_length_inside_her_window(heard):
    v = ov.verdict(_turn(heard), _said("There we go, love. Love you too."), guest=True, now=100.1)
    assert v["rule"] == "said_it_herself"


def test_rule_1_needs_her_window():
    said = _said("There we go, love.", start=60.0, end=64.0)        # 34 s ago: the room may say it now
    assert ov.verdict(_turn("There we go, love."), said, guest=False, now=100.1) == {"rule": None}
    just_after = _said("There we go, love.", start=94.0, end=97.2)  # ended 0.8 s before the audio began
    assert ov.verdict(_turn("There we go, love.", ts=100.0, dur=1.0), just_after, guest=False, now=100.1)["rule"] == "said_it_herself"
    too_late = _said("There we go, love.", start=90.0, end=94.0)    # ended 4 s before: past the 1.5 s tail
    assert ov.verdict(_turn("There we go, love.", ts=100.0, dur=1.0), too_late, guest=False, now=100.1) == {"rule": None}


def test_rule_1_does_not_eat_a_barge_in():
    said = _said("I don't know that name, love. Not in the roster, not in the room.")
    assert ov.verdict(_turn("Crissa stop, check the board instead.", pid="user"), said, guest=False, now=100.1) == {"rule": None}


def test_rule_1_is_positional_not_a_bag_of_words():
    said = _said("The board has twelve open cards and the render window is closed until tonight.")
    assert ov.text_match("the window until cards", said.spoken_during(95, 100, now=100)) < ov.MATCH


@pytest.mark.parametrize("stamp", [{"pid": "crissa_her_tts"}, {"source": "tts_echo_veto"},
                                   {"tts_veto": {"veto": True, "tts_fp_sim": 0.72}}])
def test_rule_2_her_voice_is_always_rejected_mouth_idle_or_not(stamp):
    v = ov.verdict(_turn("Hey Crissa, can you hear me?", **stamp), ov.SayLog(), guest=False, now=100.1)
    assert v["rule"] == "voice_is_crissa"


def test_rule_3_a_guest_passes_only_when_confidently_not_crissa():
    quiet = ov.SayLog()
    assert ov.verdict(_turn("Is anyone home?"), quiet, guest=True, now=100.1) == {"rule": None}         # has an embedding, no veto
    assert ov.verdict(_turn("Hi.", is_user_reason="no_emb", voice_emb_sim=0.01), quiet, guest=True, now=100.1)["rule"] == "guest_unsure"
    assert ov.verdict(_turn("Hi.", tts_sim=0.42), quiet, guest=True, now=100.1) == {"rule": "guest_unsure", "why": "tts_sim", "tts_sim": 0.42}
    assert ov.verdict(_turn("Hi.", tts_sim=0.12, is_user_reason="no_emb"), quiet, guest=True, now=100.1) == {"rule": None}
    # an identified person is not a guest: rule 3 never applies to him
    assert ov.verdict(_turn("Hi.", pid="user", is_user_reason="no_emb"), quiet, guest=False, now=100.1) == {"rule": None}


def test_the_default_drops_in_a_pack(monkeypatch):
    # round 13 (the User 2026-10-12, .101's echo loop): this module runs only in a pack, where report mode let her
    # own lines reach the chat as the User's; the default is drop. A bad word still falls back to report.
    assert ov.mode() == "drop"
    monkeypatch.setattr(ov, "MODE", "nonsense")
    assert ov.mode() == "report"
    monkeypatch.setattr(ov, "MODE", "drop")
    assert ov.mode() == "drop"


def test_the_bridge_checks_before_the_turn_touches_any_state():
    src = (Path(__file__).resolve().parents[3] / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    check, state = src.index("own_voice.verdict("), src.index('globals()["_last_user_speech"] = now')
    assert check < state
    assert "_SAY.begin(text)" in src and "_SAY.end()" in src
    assert "_ov_drop = own_voice.drops(ov.get(\"rule\"))" in src
    blk = src[src.index("if _ov_drop:"):][:600]                    # round 15: a note on the log, then continue
    assert "\n                            continue" in blk
    assert "idle_wake=_idle and _wk, wake=_wk, user_voice=_ov_user" in src           # round 13: wake + his voice too


# ---------------------------------------------------------------- armed per rule (the User 2026-09-30)
def test_armed_means_rules_1_and_2_drop_and_rule_3_only_reports(monkeypatch):
    # round 13 adds rule 4, on_air_not_user, armed by default; rule 3 still only reports
    assert ov.ARMED == {"said_it_herself", "voice_is_crissa", "on_air_not_user"}
    monkeypatch.setattr(ov, "MODE", "report")
    assert not any(ov.drops(r) for r in ov.RULES)                       # report mode: nothing drops
    monkeypatch.setattr(ov, "MODE", "drop")
    assert ov.drops("said_it_herself") and ov.drops("voice_is_crissa")
    assert not ov.drops("guest_unsure") and not ov.drops(None)
    monkeypatch.setattr(ov, "MODE", "off")
    assert not ov.drops("said_it_herself")


def test_armed_on_the_replay_drops_her_lines_and_keeps_his(monkeypatch):
    monkeypatch.setattr(ov, "MODE", "drop")
    dropped = {text: ov.drops(v["rule"]) for text, v in verdicts().items()}
    assert dropped == {
        "Love you, my.": False, "There we go, love.": True, "Me too.": False, "You welcome love.": True,
        "Marlowe.": False,                                   # rule 3 would drop it; rule 3 only reports
        "I don't know that name, love, as.": True,
        "Not in the record. Not in the room. Not anywhere on record here.": True}


def test_a_wake_vocative_with_her_mouth_idle_survives_rule_2():
    """Measured 2026-09-09 06:24: "Hey Zoe, can you hear me?" scored 0.51 on her fingerprint, nothing on air."""
    hey = _turn("Hey Crissa, can you hear me?", source="tts_echo_veto", tts_veto={"veto": True, "tts_fp_sim": 0.51})
    assert ov.verdict(hey, ov.SayLog(), guest=True, now=100.1, idle_wake=True) == {"rule": None}
    assert ov.verdict(hey, ov.SayLog(), guest=True, now=100.1, idle_wake=False)["rule"] == "voice_is_crissa"
    # the exception is for rule 2 only: a wake phrase she is saying herself right now is still hers
    said = _said("Hey Crissa, can you hear me? That is what you said.")
    assert ov.verdict(hey, said, guest=True, now=100.1, idle_wake=True)["rule"] == "said_it_herself"
