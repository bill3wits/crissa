"""Round 16 ear (.101 fresh install, ASK-20261013-0030, addenda 00:40-00:50): the User's voice taken for hers, stops
that took 5-10 s, a pause that would not lift, "Hey Krista" that did not wake her, and prints of her own voice.
Today's exact decodes are the fixture (fixtures/round16-101-20261013.json)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crissa_bridge import hear_gate, own_voice, wake
from crissa_bridge import ear_bridge as B
from crissa_ear import ear_loop, her_prints, self_echo, stamp

FIX = json.loads((Path(__file__).parent / "fixtures" / "round16-101-20261013.json").read_text())
READ = FIX["reading"]
UID = "user"
T0 = 1_800_000_000.0


# ── (a) only what she had on the air in the last few seconds ──────────────────────────────────────────────────────────

def _gate(tmp_path, **g):
    p = tmp_path / "house-tts-gate.json"
    p.write_text(json.dumps(g))
    return p


def _reading_then(tmp_path, ending):
    """Her reading on the air from T0 (house-speak's gate), then how it ended."""
    L = self_echo.Lines()
    L.refresh(now=T0 + 1, paths=[_gate(tmp_path, speaking=True, last_reply=READ, updated_ts=T0, air_at=T0)])
    if ending == "cancel":        # the bridge's stop: speaking false, no words, notice interrupted
        L.refresh(now=T0 + 31, paths=[_gate(tmp_path, speaking=False, last_reply="", notice="interrupted",
                                            updated_ts=T0 + 30)])
    elif ending == "next line":   # the closing write never seen: the next line replaced it
        L.refresh(now=T0 + 41, paths=[_gate(tmp_path, speaking=True, last_reply="Sure, picking up after that.",
                                            updated_ts=T0 + 40, air_at=T0 + 40)])
    return L


@pytest.mark.parametrize("ending", ["cancel", "next line", "nothing"])
@pytest.mark.parametrize("row", FIX["dropped_as_hers"], ids=[r["text"] for r in FIX["dropped_as_hers"]])
def test_the_users_lines_are_his_after_the_reading_stopped(tmp_path, monkeypatch, row, ending):
    L = _reading_then(tmp_path, ending)
    at = T0 + 120                                  # minutes later, as on .101
    got = self_echo.subtract(row["text"], at, at + 1.2, now=at + 2, lines=L)
    assert got["action"] == "keep", got


def test_the_old_reference_is_the_reported_drop(tmp_path, monkeypatch):
    """Round 15's reference (CRISSA_SELF_ECHO_FRESH=0) kept the stopped reading on the air: the logged drops come back."""
    monkeypatch.setattr(self_echo, "FRESH", False)
    L = _reading_then(tmp_path, "cancel")
    at = T0 + 120
    dropped = [r["text"] for r in FIX["dropped_as_hers"]
               if self_echo.subtract(r["text"], at, at + 1.2, now=at + 2, lines=L)["action"] == "drop"]
    assert {"What book was that?", "Thank you.", "Hang on."} <= set(dropped)


def test_a_long_line_is_compared_only_where_she_is(tmp_path):
    """Still reading: her echo of the stretch she is saying is hers; words like her opening, minutes behind, are not."""
    L = self_echo.Lines()
    L.note(T0, None, READ)
    at = T0 + READ.index("There is nothing") / 15.0             # where she is, at her pace
    assert self_echo.subtract("There is nothing surprising in this.", at, at + 2, now=at + 3,
                              lines=L)["action"] == "drop"
    assert "growing grim" not in L.during(at, at + 2, now=at + 3)
    assert self_echo.subtract("Whenever I find myself growing grim.", at, at + 2, now=at + 3,
                              lines=L)["action"] == "keep"


def test_short_lines_and_stops_are_not_her_echo(tmp_path):
    L = self_echo.Lines()
    L.note(T0, None, "There is nothing surprising in this. If they but knew it.")
    for text in ("Stop.", "Hang on.", "Thank you.", "Okay, stop there."):
        assert self_echo.subtract(text, T0 + 1, T0 + 2, now=T0 + 3, lines=L)["action"] in ("keep", "rewrite"), text
    # her own short line, bent, still is hers
    L2 = self_echo.Lines()
    L2.note(T0, T0 + 2, "Yeah, I'm here.")
    assert self_echo.subtract("Yeah, I'm here.", T0 + 0.5, T0 + 2, now=T0 + 3, lines=L2)["action"] == "drop"


def test_her_own_name_in_her_own_sentence_is_hers():
    L = self_echo.Lines()
    L.note(T0, T0 + 3, "I'm not Krista, but I'm here.")
    assert self_echo.subtract("I'm not Krista, but I'm here.", T0 + 0.2, T0 + 3, now=T0 + 4,
                              lines=L)["action"] == "drop"


def test_the_bridge_say_log_closes_on_a_cancel_and_bars_short_lines():
    say = own_voice.SayLog()
    say.note(T0, None, READ)
    row = FIX["stop_on_air"]
    turn = {"text": row["text"], "ts": T0 + 41, "speech_start_ts": T0 + 39.6, "speech_end_ts": T0 + 40.2,
            "person_id": row["pid"], "stamp": {"person_id": row["pid"]}}
    # 00:40:29: "Stop." over her reading - never her echo now (a stop word she was not saying, and a short line)
    assert own_voice.verdict(turn, say, guest=False, now=T0 + 41)["rule"] is None
    say.close_all(T0 + 30)                         # the stop's notice
    assert say.spoken_during(T0 + 100, T0 + 102, now=T0 + 103) == ""
    hang = dict(turn, text="Hang on.", ts=T0 + 103, speech_start_ts=T0 + 100, speech_end_ts=T0 + 101)
    assert own_voice.verdict(hang, say, guest=False, now=T0 + 103)["rule"] is None


def test_the_bridge_say_log_sync_closes_on_the_gate_notice(tmp_path):
    say = own_voice.SayLog()
    g = tmp_path / "tts-gate.json"
    g.write_text(json.dumps({"speaking": True, "last_reply": READ, "updated_ts": T0}))
    say.sync_gates([g], now=T0 + 1)
    g.write_text(json.dumps({"speaking": False, "last_reply": "", "notice": "interrupted", "updated_ts": T0 + 30}))
    say.sync_gates([g], now=T0 + 31)
    assert all(r["end"] is not None and r["end"] <= T0 + 30 for r in say.rows())


def test_the_old_say_log_is_the_reported_drop(monkeypatch):
    monkeypatch.setattr(own_voice, "FRESH", False)
    say = own_voice.SayLog()
    say.note(T0, None, READ)
    row = FIX["stop_on_air"]
    turn = {"text": row["text"], "ts": T0 + 121, "speech_start_ts": T0 + 119.6, "speech_end_ts": T0 + 120.2,
            "person_id": row["pid"], "stamp": {"person_id": row["pid"]}}
    v = own_voice.verdict(turn, say, guest=False, now=T0 + 121)
    assert v["rule"] == "on_air_not_user" and v["air_share"] == 1.0 and v["like_her"] == row["like_her"]


def test_a_gate_line_never_closes_the_bridges_own_reply():
    say = own_voice.SayLog()
    say.begin("A reply the bridge is saying.", now=T0)
    say.note(T0 + 1, None, "a piece of it on the speaker")
    assert [r for r in say.rows() if r.get("src") == "say"][0]["end"] is None


# ── (b) her name and a stop stop her at once ──────────────────────────────────────────────────────────────────────────

def _sole_hold():
    return {"person_id": None, "source": "media_hold_sole", "media_suppressed_id": UID,
            "media_suppressed_source": "sole_present", "is_user": None, "is_user_reason": "no_emb",
            "media_gate": {"phase": "characterize", "verdict": "unsure"}}


def test_crissa_stop_with_no_voice_print_stops_her_at_once():
    row = FIX["name_stop"]
    assert B.name_interrupt_hit(row["text"], speaking=True, busy=False, stamp=_sole_hold(), person_id=None) is True
    assert B.name_interrupt_hit("Crissa, stop.", speaking=True, busy=False, stamp={}, person_id="s1") is True
    assert B.name_interrupt_hit("Chris, stop.", speaking=True, busy=False, stamp={}, person_id="s1") is True
    # a voice the ear could not name may stop her, never ask her something over her line; a TV holds nothing
    assert B.name_interrupt_hit("Crissa, what time is it?", speaking=True, busy=False, stamp={},
                                person_id="s1") is False
    tv = {"source": "media_gate", "media_gate": {"verdict": "media", "media_on": True}}
    assert B.name_interrupt_hit("Crissa, stop.", speaking=True, busy=False, stamp=tv) is False


def test_the_old_interrupt_is_the_reported_slow_route(monkeypatch):
    monkeypatch.setattr(B, "NAME_STOP_ANY_VOICE", False)
    assert B.name_interrupt_hit("Crissa, stop.", speaking=True, busy=False, stamp=_sole_hold()) is False


def test_a_short_line_over_hers_closes_sooner(monkeypatch):
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: True)
    assert ear_loop.on_air_hangover(1.8, 1.1) == ear_loop.ON_AIR_HANGOVER_S == 0.6
    assert ear_loop.on_air_hangover(1.8, 5.0) == 1.8                  # a longer sentence of his keeps the session's
    assert ear_loop.on_air_hangover(0.3, 1.1) == 0.3                  # never longer than it was
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: False)
    assert ear_loop.on_air_hangover(1.8, 1.1) == 1.8
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: True)
    monkeypatch.setattr(ear_loop, "ON_AIR_HANGOVER_S", 0.0)
    assert ear_loop.on_air_hangover(1.8, 1.1) == 1.8


# ── (c) a pause lifts on his word ─────────────────────────────────────────────────────────────────────────────────────

def _turn(text, pid=None, st=None):
    return {"text": text, "person_id": pid, "stamp": st if st is not None else {"person_id": pid}}


def test_okay_continue_under_the_identity_hold_reaches_her_in_session():
    g = hear_gate.classify_hear(_turn("Okay. Continue.", None, _sole_hold()), session_open=True, uid=UID)
    assert g["crissa_hears"] is True and g["sole_user"] is True and g["media"] is True


@pytest.mark.parametrize("text,pid,st", [
    ("Okay. Continue.", None, "hold"),
    ("Hey, Chris. Go ahead and start again.", UID, None),
    ("Start where you left off.", UID, None),
    ("Go on.", None, "hold"),
    ("Carry on please.", UID, None),
])
def test_a_continue_lifts_a_fresh_pause(text, pid, st):
    t = _turn(text, pid, _sole_hold() if st == "hold" else None)
    assert hear_gate.classify_hear(t, session_open=False, uid=UID)["paused"] is True       # no pause to lift
    g = hear_gate.classify_hear(t, session_open=False, uid=UID, resume_pending=True)
    assert g["resume"] is True and g["unpause"] is True and g["paused"] is False and g["crissa_hears"] is True


def test_a_continue_from_anyone_else_does_not_lift_it(monkeypatch):
    for t in (_turn("Go on.", "s1"), _turn("Go on.", "guest"), _turn("Go on.", None, {})):
        assert hear_gate.classify_hear(t, session_open=False, uid=UID, resume_pending=True)["paused"] is True
    assert hear_gate.classify_hear(_turn("I will go ahead and eat.", UID), session_open=False, uid=UID,
                                   resume_pending=True)["paused"] is True
    monkeypatch.setenv("EAR_BRIDGE_PAUSE_LIFT", "0")
    assert hear_gate.classify_hear(_turn("Go on.", UID), session_open=False, uid=UID,
                                   resume_pending=True)["paused"] is True


def test_a_pause_is_fresh_for_ten_minutes(monkeypatch):
    monkeypatch.setattr(B.cancels, "latest", lambda: {"ts": T0, "why": "over_line_pause"})
    assert B.resume_pending(T0 + 30) is True and B.resume_pending(T0 + 601) is False
    monkeypatch.setattr(B.cancels, "latest", lambda: {})
    assert B.resume_pending(T0) is False


# ── (d) "Hey" and a near-name from the User ───────────────────────────────────────────────────────────────────────────

def test_hey_krista_from_the_user_is_her_name():
    for row in FIX["near_name"]:
        out = wake.user_near_wake(row["text"]) or row["text"]
        assert out == "Hey, Crissa." and wake.wake_match(out)["hit"] is True
    # words after the name: hers only while her pause is fresh or she is on the air (he may be talking to a Chris)
    assert wake.user_near_wake("Hey Chris, read me the next page") is None
    assert wake.user_near_wake("Hey Chris, read me the next page", ask_ok=True) == "Hey Crissa, read me the next page"
    assert wake.address_name("Hey, Krista.", user_voice=True) == "Hey, Crissa."
    assert wake.address_name("Hey, Krista.") == "Hey, Krista."                         # not his voice: as said


def test_a_mention_or_another_greeting_never_wakes_her():
    for text in ("I told Krista hey.", "Okay Krista.", "Krista, come here.", "So hey Chris was there.",
                 "We met Christa yesterday."):
        assert wake.user_near_wake(text) is None, text
    rows = json.loads((Path(__file__).parent / "bridge" / "fixtures" / "crissa-wake-asr.json").read_text())["rows"]
    for r in rows:
        if r.get("kind") == "nowake":
            assert wake.user_near_wake(r["heard"]) is None, r["heard"]


def test_the_ear_shows_his_hey_krista_as_crissa(monkeypatch):
    from crissa_ear import server
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: False)
    monkeypatch.setattr("crissa_ear.house_id.user_id", lambda ttl=5.0: UID)
    d = {"text": "Hey, Krista.", "person_id": UID, "stamp": {"person_id": UID}}
    server._shown_name(d, "Hey, Krista.")
    assert d["text"] == "Hey, Crissa." and d["heard_as"] == "Hey, Krista."
    d2 = {"text": "Hey, Krista.", "person_id": "s1", "stamp": {"person_id": "s1"}}
    server._shown_name(d2, "Hey, Krista.")
    assert d2["text"] == "Hey, Krista."


# ── (e) prints of her voice ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def prints(monkeypatch, tmp_path):
    root = tmp_path / "mouths"
    monkeypatch.setenv("HOUSE_MOUTHS_ROOT", str(root))
    monkeypatch.setattr(her_prints, "EAR", tmp_path)
    monkeypatch.setattr(her_prints, "HOME", tmp_path)
    monkeypatch.setattr(her_prints, "_speaker", lambda: "speaker-a")
    monkeypatch.setattr(her_prints, "_CTX_CACHE", {"at": 0.0, "ctx": None})
    monkeypatch.setattr(her_prints, "CTX_TTL_S", 0.0)
    (tmp_path / "voice-tier.json").write_text(json.dumps({"tier": "pocket", "announcer": "pocket"}))
    stamp._reset_tts_fp_cache()
    yield root
    stamp._reset_tts_fp_cache()


def _vec(i):
    v = [0.0] * 256
    v[i] = 1.0
    return v


def test_a_print_recorded_while_the_ear_runs_is_used_on_the_next_turn(prints):
    assert stamp.tts_veto_check(_vec(3)) is None
    her_prints.write_print("crissa_her_tts", _vec(3), model="wespeaker-resnet221", root=prints)
    v = stamp.tts_veto_check(_vec(3))
    assert v and v["veto"] is True and v["tts_fp_count"] == 1
    rep = her_prints.status()
    assert rep["used"] == 1 and rep["ask"] is False and rep["kinds_in_use"] == ["crissa_her_tts"]


def test_a_new_voice_tier_or_speaker_makes_the_prints_stale(prints, monkeypatch, tmp_path):
    her_prints.write_print("crissa_her_tts", _vec(3), model="wespeaker-resnet221", root=prints)
    her_prints.write_print("announcer", _vec(5), model="wespeaker-resnet221", root=prints)
    assert stamp.tts_veto_check(_vec(3)) and stamp.tts_veto_check(_vec(5))
    (tmp_path / "voice-tier.json").write_text(json.dumps({"tier": "audio8", "announcer": "pocket"}))
    assert stamp.tts_veto_check(_vec(3)) is None and stamp.tts_veto_check(_vec(5))
    rep = her_prints.status()
    assert rep["ask"] is True and rep["stale"][0]["why"].startswith("her voice changed")
    monkeypatch.setattr(her_prints, "_speaker", lambda: "speaker-b")
    assert stamp.tts_veto_check(_vec(5)) is None
    assert {s["why"].split(" (")[0] for s in her_prints.status()["stale"]} == {"her voice changed",
                                                                              "the speaker changed"}
    monkeypatch.setenv("CRISSA_HER_PRINTS_STALE", "0")
    assert stamp.tts_veto_check(_vec(3)) and stamp.tts_veto_check(_vec(5))


def test_a_print_is_only_of_her_voices(prints):
    with pytest.raises(ValueError):
        her_prints.write_print("user", _vec(1), model="wespeaker-resnet221", root=prints)



import pytest as _pytest_r29


@_pytest_r29.fixture(autouse=True)
def _round29_old_rule(monkeypatch):
    """This file pins round 18's barge rules; round 29 builds .101's three-word barge and words-over-her-line-to-her-model
    (the User 2026-10-17), tested in tests/ear/test_round29_101_fixes_20261017.py. Here they run under their kill
    switches, and Chris/Krista stay her-name-near words (address-only off)."""
    monkeypatch.setenv("CRISSA_BARGE_WORDS_TO_CUT", "0")
    monkeypatch.setenv("EAR_BRIDGE_SIDE_TALK_ASK", "0")
    monkeypatch.setenv("EAR_BRIDGE_WAKE_ADDRESS_ONLY", "0")
