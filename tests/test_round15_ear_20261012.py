"""Round 15 ear (the User 2026-10-12, .101 ASK-20261012-1308, addenda 13:15-13:26): his words over her line are never
dropped for being said over her, her name's spellings stop her, words without her name go to her model with an action
to take, the glass's log shows what was heard and not sent, her name reads "Crissa" where it addresses her, and a cancel
leaves a record the next turn carries. Checkpoint docs/checkpoints/round15-ear-20261012/README.md."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))
sys.path.insert(0, str(ROOT / "lib"))

READING = ("Call me Ishmael. Some years ago, never mind how long precisely, having little or no money in my purse, and "
           "nothing particular to interest me on shore, I thought I would sail about a little and see the watery part "
           "of the world. It is a way I have of driving off the spleen and regulating the circulation.")
# .101's decodes, 13:10-13:15 (bridge.log / ear/turns.jsonl)
KREZA = "Kreza Wansan."
ONE_SECOND = "One second."
KRISA = "Krisa, one second."
CHRIS_A = "Chris, a one second."
HEY_CHRIS = "Hey, Chris! Stop."
COULD_STOP = "I could just stop."


def _ov():
    return importlib.import_module("crissa_bridge.own_voice")


def _wake():
    return importlib.import_module("crissa_bridge.wake")


def _turn(text, s0, s1, pid="sam"):
    return {"text": text, "ts": s1 + 2.0, "duration_s": s1 - s0, "speech_start_ts": s0, "speech_end_ts": s1,
            "person_id": pid, "stamp": {"person_id": pid, "source": "sole_present"}}


# ---- (a) said over her is not her voice ------------------------------------------------------------------------------------
@pytest.mark.parametrize("heard", [KREZA, ONE_SECOND, COULD_STOP, "What book was that?"])
def test_a_his_words_over_her_line_are_not_dropped_for_being_over_it(heard):
    ov = _ov()
    say = ov.SayLog()
    now = 1791810000.0
    say.note(now - 60.0, None, READING)                                # her reading, on the air the whole time
    got = ov.verdict(_turn(heard, now - 5.0, now - 4.0), say, guest=False, now=now)
    assert got["rule"] is None, got                                    # 13:14:31: on_air_not_user, air_share 1.0


def test_a_her_own_words_bent_by_the_ear_are_still_hers():
    ov = _ov()
    say = ov.SayLog()
    now = 1791810000.0
    say.note(now - 10.0, None, READING)       # round 16: the words she was saying then (her reading's opening, 1 s in)
    got = ov.verdict(_turn("Some years ago never mind how long precisely having little money", now - 9, now - 5),
                     say, guest=False, now=now)
    assert got["rule"] in ("said_it_herself", "on_air_not_user")
    got = ov.verdict(_turn("Sum years a go never mind how long precisely having little or no money in my purse",
                           now - 9, now - 5), say, guest=False, now=now)
    assert got["rule"] in ("said_it_herself", "on_air_not_user")


# ---- (c) her name as the ear wrote it today stops her -----------------------------------------------------------------------
@pytest.mark.parametrize("heard", [KREZA, KRISA, CHRIS_A, HEY_CHRIS, "Tressa, wait.", "Hey Chrisa, stop", "Christmas, stop!"])
def test_c_todays_spellings_stop_her_and_ask_nothing(heard):
    w = _wake()
    got = w.barge_match(heard)
    assert got["hit"] and got["profile"] == "crissa", got
    assert w.stop_only(got["residual"]), got


@pytest.mark.parametrize("said", ["Christmas dinner was great", "Put it in the crisper drawer", "Christa called",
                                  "It's a crisis", "I told Chris about it", ONE_SECOND])
def test_c_ordinary_words_are_no_barge_in_and_wake_nobody(said):
    w = _wake()
    assert w.barge_match(said)["hit"] is False
    assert w.wake_match(said)["hit"] is False


# ---- (b') words without her name go to her model with an action -------------------------------------------------------------
def _ol():
    return importlib.import_module("crissa_bridge.over_line")


def test_b_the_model_is_told_what_she_is_saying_and_where_and_chooses():
    ol = _ol()
    where = {"said": READING[:120], "next_sentence": "It is a way I have of driving off the spleen."}
    msgs = ol.messages(ONE_SECOND, where, request="Read me the opening of Moby-Dick")
    body = msgs[1]["content"]
    assert "Read me the opening of Moby-Dick" in body and READING[100:120] in body and '"One second."' in body
    assert all(a in msgs[0]["content"] for a in ("PAUSE", "STOP", "CONTINUE"))
    assert ol.decide(ONE_SECOND, where, lambda m: "PAUSE\nSure, take your time.")["action"] == "PAUSE"
    got = ol.decide("And then?", where, lambda m: "CONTINUE")
    assert got == {"action": "CONTINUE", "say": "", "by": "model"}
    assert ol.parse("stop: Okay.") == ("STOP", "Okay.")


def test_b_no_answer_in_time_a_stop_or_hold_pauses_and_anything_else_waits(monkeypatch):
    ol = _ol()
    w = _wake()
    got = ol.decide(ONE_SECOND, {}, lambda m: None, stop_only=w.stop_only)
    assert got == {"action": "PAUSE", "say": "", "by": "fallback"}
    assert ol.decide("What book is this?", {}, lambda m: None, stop_only=w.stop_only)["action"] == "CONTINUE"
    monkeypatch.setenv("EAR_BRIDGE_OVER_LINE", "stop")
    assert ol.decide("wait", {}, lambda m: "CONTINUE", stop_only=w.stop_only)["action"] == "PAUSE"


def _eb(tmp_path, monkeypatch):
    eb = importlib.import_module("crissa_bridge.ear_bridge")
    monkeypatch.setattr(eb.cancels, "HOME", tmp_path)
    monkeypatch.setattr(eb.heard_roll, "HOME", tmp_path)
    return eb


def test_b_paused_by_her_model_she_stops_and_the_log_says_so(tmp_path, monkeypatch):
    eb = _eb(tmp_path, monkeypatch)
    stops, spoken = [], []
    monkeypatch.setattr(eb, "_over_line_ask", lambda msgs: "PAUSE\nSure.")
    monkeypatch.setattr(eb, "abort_mouth", lambda reason="": stops.append(reason))
    monkeypatch.setattr(eb, "speak_blocking", lambda t, **k: spoken.append(t))
    monkeypatch.setattr(eb, "_speaking", True)
    eb._over_line_run(ONE_SECOND, {"person_id": "sam"})
    assert stops == ["over_line_pause"] and spoken == ["Sure."]
    row = json.loads((tmp_path / "heard-roll.jsonl").read_text().splitlines()[-1])
    assert row["text"] == ONE_SECOND and row["why"] == "said over her line: she paused"


def test_b_continue_queues_his_words_behind_her_line(tmp_path, monkeypatch):
    eb = _eb(tmp_path, monkeypatch)
    monkeypatch.setattr(eb, "_over_line_ask", lambda msgs: "CONTINUE")
    monkeypatch.setattr(eb, "abort_mouth", lambda reason="": pytest.fail("she was stopped"))
    monkeypatch.setattr(eb, "_dispatch_busy", True)
    monkeypatch.setattr(eb, "_pending", None)
    eb._over_line_run("What book is this?", {"person_id": "sam"})
    assert eb._pending and eb._pending[0] == "What book is this?"


def test_b_over_her_line_goes_to_the_model_before_any_house_command():
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text(encoding="utf-8")
    i = src.index("def schedule_dispatch(")
    body = src[i:i + 3000]
    assert body.index("_over_line_run") < body.index("match_control(user)")
    assert "not wake_match(user).get(\"hit\") and _over_line_owner(presence, stamp, meta)" in body


# ---- (d)/(e) the glass's log shows what was heard and not sent, and the session's end ---------------------------------------
def _feed(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("vo_feed_r15", ROOT / "scripts" / "voiceonly-home-server.py")
    F = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(F)
    monkeypatch.setattr(F, "HEARD_ROLL", tmp_path / "heard-roll.jsonl")
    return F


def test_d_e_heard_rows_reach_the_feed_marked_not_sent(tmp_path, monkeypatch):
    hr = importlib.import_module("crissa_bridge.heard_roll")
    monkeypatch.setattr(hr, "HOME", tmp_path)
    now = time.time()
    hr.note("sam", KREZA, "not sent: taken for her own voice", now=now)
    hr.note("crissa", "Listening off. Say Crissa to talk again.", "timeout", kind="status", now=now)
    hr.note("crissa", "Cancelled 412 characters into ...", "cancelled", kind="cancel", now=now)
    F = _feed(tmp_path, monkeypatch)
    rows = F.recent_heard_roll(now=now + 1)
    assert rows[0]["text"] == KREZA + " · not sent: taken for her own voice" and rows[0]["log_user"] is True
    assert rows[1]["text"] == "Listening off. Say Crissa to talk again." and rows[1]["kind"] == "status"
    assert rows[2]["kind"] == "cancel"
    assert F.recent_heard_roll(now=now + 3600) == []                     # ten minutes, then off the roll
    src = (ROOT / "scripts/voiceonly-home-server.py").read_text(encoding="utf-8")
    assert "chat.extend(recent_heard_roll())" in src


def test_e_every_session_end_says_listening_off():
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text(encoding="utf-8")
    assert src.count('_log({"event": "session_end"') == src.count("listening_off(") - 1 == 4


# ---- (f) her name reads "Crissa" where it addresses her ---------------------------------------------------------------------
@pytest.mark.parametrize("heard,on_air,shown", [
    (KRISA, False, "Crissa, one second."),                  # a measured spelling: always
    (KRISA, True, "Crissa, one second."),
    (KREZA, True, "Crissa Wansan."),                       # over her line, name first + a hold
    (CHRIS_A, True, "Crissa, a one second."),
    (HEY_CHRIS, True, "Hey, Crissa! Stop."),
    (CHRIS_A, False, CHRIS_A),                             # not over her line: Chris stays Chris
    ("Christmas, stop!", True, "Crissa, stop!"),
    ("Christmas dinner was great", True, "Christmas dinner was great"),
    ("Put it in the crisper drawer", True, "Put it in the crisper drawer"),
    ("Christa called", True, "Christa called"),
    ("It's a crisis", True, "It's a crisis"),
    ("Hey Chrissa, what time is it?", False, "Hey Crissa, what time is it?"),
])
def test_f_her_name_is_written_crissa_only_where_it_addresses_her(heard, on_air, shown):
    assert _wake().address_name(heard, on_air=on_air) == shown


def test_f_the_ear_keeps_what_it_heard_beside_the_shown_name(monkeypatch):
    srv = importlib.import_module("crissa_ear.server")
    EL = importlib.import_module("crissa_ear.ear_loop")
    monkeypatch.setattr(EL, "her_air_now", lambda now=None: True)
    d = {"text": KREZA}
    srv._shown_name(d, KREZA)
    assert d == {"text": "Crissa Wansan.", "heard_as": KREZA}


# ---- (g) the cancel record --------------------------------------------------------------------------------------------------
def test_g_a_cancel_says_where_she_stopped_and_the_next_turn_carries_it(tmp_path, monkeypatch):
    c = importlib.import_module("crissa_bridge.cancels")
    monkeypatch.setattr(c, "HOME", tmp_path)
    now = 1791810000.0
    c.note_request("Read me the opening of Moby-Dick", now=now - 30.0)
    first = "Call me Ishmael. Some years ago, never mind how long precisely, having little or no money in my purse."
    second = ("And nothing particular to interest me on shore, I thought I would sail about a little and see the "
              "watery part of the world.")
    rows = [{"start": now - 28.0, "end": now - 20.0, "text": first}, {"start": now - 3.0, "end": None, "text": second}]
    rec = c.record("name_interrupt", rows, now=now)                     # 3 s (45 characters) into the second piece
    assert rec["request"] == "Read me the opening of Moby-Dick" and rec["title"] == "Read me the opening of Moby-Dick"
    assert rec["last_sentence"] == "Some years ago, never mind how long precisely, having little or no money in my purse."
    assert rec["next_sentence"].startswith("And nothing particular")
    assert rec["spoken_chars"] > len(first) and rec["summary"].startswith("Cancelled ")
    on_disk = json.loads((tmp_path / "cancel.json").read_text())
    assert on_disk["last_sentence"] == rec["last_sentence"] and (tmp_path / "logs" / "cancels.jsonl").is_file()
    note = c.context_note()
    assert "[Context from your interrupted spoken reply]" in note and "You stopped after:" in note
    assert c.context_note() == ""                                        # once
    c.note_title("Moby-Dick, chapter 1")
    assert c.record("over_line_pause", rows, now=now)["title"] == "Moby-Dick, chapter 1"


def test_g_abort_mouth_writes_the_record_only_while_she_speaks(tmp_path, monkeypatch):
    eb = _eb(tmp_path, monkeypatch)
    say = eb.own_voice.SayLog()
    now = time.time()
    eb.cancels.note_request("Read me the opening", now=now - 10)
    say.note(now - 2.0, None, "Call me Ishmael. Some years ago, never mind how long precisely.")   # 30 chars in
    monkeypatch.setattr(eb, "_SAY", say)
    monkeypatch.setattr(eb, "_speaking", True)
    eb.abort_mouth("name_interrupt")
    rec = json.loads((tmp_path / "cancel.json").read_text())
    assert rec["why"] == "name_interrupt" and rec["last_sentence"] == "Call me Ishmael."
    rows = (tmp_path / "heard-roll.jsonl").read_text().splitlines()
    assert json.loads(rows[-1])["kind"] == "cancel"
    (tmp_path / "cancel.json").unlink()
    eb.abort_mouth("idle")                                              # not speaking: no record
    assert not (tmp_path / "cancel.json").exists()


def test_the_new_modules_ship_in_the_ear_pack():
    pk = json.loads((ROOT / "config/crissa-packages.json").read_text(encoding="utf-8"))
    files = pk.get("packages", pk)["crissa-ear"]["files"]
    for m in ("cancels.py", "heard_roll.py", "over_line.py"):
        assert "modules/ear/crissa_bridge/" + m in files


def test_f_the_measured_ordinary_sentences_still_wake_nobody_after_the_shown_name():
    """The round-1 wake fixture (tests/ear/bridge/fixtures/crissa-wake-asr.json, its `nowake` rows): writing her name
    "Crissa" where it addresses her never makes an ordinary sentence wake her."""
    w = _wake()
    rows = json.loads((ROOT / "tests/ear/bridge/fixtures/crissa-wake-asr.json").read_text(encoding="utf-8"))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    nowake = [r["heard"] for r in rows if r.get("kind") == "nowake"]
    assert nowake
    woke = [h for h in nowake if w.wake_match(w.address_name(h, on_air=False))["hit"]]
    assert woke == []



import pytest as _pytest_r29


@_pytest_r29.fixture(autouse=True)
def _round29_old_rule(monkeypatch):
    """This file pins the rule before Chris/Krista as an address (round 29); the new default is tested in tests/ear/test_round29_101_fixes_20261017.py.
    Here it runs under its kill switch (EAR_BRIDGE_WAKE_ADDRESS_ONLY=0)."""
    monkeypatch.setenv("EAR_BRIDGE_WAKE_ADDRESS_ONLY", "0")
