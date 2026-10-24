"""Round 18, barge-in only on interrupt words, and side talk (the User 2026-10-13: "Interrupt words only: barge-in
straight from the ASR's first words, with no voice check, but only on interrupt words (her name, stop, wait, hold on,
one second, pause...)"; then, approving the list: "Id like to talk with someone else while she speaks without
interrupting her").

What .101 heard in round 17 (RESULTS-20261013-1530 item 3): round 17 cut her on "Chapter One." (him finishing his own
request), "Thank you much." and the keyboard, which the ASR wrote "I don't know."; each time she went on by herself.

The ear's looks (crissa_ear/barge.py: words only), the bridge's side talk (crissa_bridge/barge.py Air, ear_bridge
_side_talk_turn), the talk settings' `barge` section and the kit's feed (room speech on the Wall's log). No model, sound
or network: stubs and 127.0.0.1 only. The look fixture is what the kit's ASR decoded from synthetic speech (round 17,
Kokoro stock voices), never a person.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "modules" / "ear", ROOT / "lib", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_home  # noqa: E402
import crissa_talk  # noqa: E402
from crissa_bridge import barge as BB  # noqa: E402
from crissa_bridge import ear_bridge as eb  # noqa: E402
from crissa_bridge import own_voice  # noqa: E402
from crissa_ear import barge as EB  # noqa: E402
from crissa_ear import self_echo  # noqa: E402

FIX = json.loads((Path(__file__).parent / "fixtures" / "round17-barge-looks-20261013.json").read_text())
APPROVED = ["stop", "wait", "hold on", "hang on", "one second", "one sec", "pause", "quiet", "enough", "cancel",
            "never mind", "excuse me"]
HER = "Sure. The weather tomorrow looks mild in the morning, with clouds building after lunch and a chance of light rain."
ENV = ("CRISSA_BARGE_WORDS_ONLY", "CRISSA_SIDE_TALK", "CRISSA_BARGE_FROM_ASR", "CRISSA_BARGE_BACKCHANNEL")


@pytest.fixture(autouse=True)
def kit(monkeypatch, tmp_path):
    """A pack (this tree's manifest is the house's), the shipped talk settings, no environment overrides."""
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(EB, "HOME", tmp_path / "home")
    EB.reset_settings()
    yield
    EB.reset_settings()


# ── the list ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_list_he_approved_is_the_shipped_setting_in_every_copy():
    shipped = json.loads((ROOT / "config" / "crissa-talk.json").read_text())["barge"]
    assert shipped["words"] == APPROVED and shipped["words_only"] is True and shipped["side_talk"] is True
    assert crissa_talk.FALLBACK["barge"]["words"] == APPROVED
    assert list(EB.DEFAULT_WORDS) == APPROVED and EB.barge_settings()["words"] == APPROVED
    for left_out in ("okay", "ok", "yes", "no", "sorry", "and", "hey", "actually"):
        assert left_out not in APPROVED
    assert EB.words_only_on() and EB.side_talk_on()


def test_an_install_changes_the_list_in_its_own_talk_file(tmp_path):
    assert EB.judge_words("Hold up.", HER)["strength"] < 2
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    crissa_talk.save({"barge": {"words": APPROVED + ["hold up"]}}, home=home)
    assert "hold up" in EB.barge_settings()["words"]                 # re-read when the file changes, no restart
    assert EB.judge_words("Hold up.", HER)["strength"] == 2


# ── the ear: what cuts ────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("heard", [
    "Wait.", "Stop!", "STOP.", "Hold on.", "Hang on, is that right?", "One second.", "One sec.", "Pause.",
    "Be quiet.", "That's enough.", "Cancel that.", "Never mind.", "Nevermind.", "Excuse me, can I ask something?",
    "Kreza Wansan.",                                       # .101 round 15: "Crissa, one second" as the ear wrote it
    "Okay. Stop there.", "Wait one second.",               # .101 round 17: both cut (why: stop)
])
def test_an_interrupt_word_cuts(heard):
    v = EB.judge_words(heard, HER)
    assert v["strength"] == 2 and v["why"] in ("stop", "named"), v


@pytest.mark.parametrize("heard", [
    "Crissa, wait.", "Hey Chrissa.", "Hey, Krista.", "Okay, Chris.", "Please continue, Chris.", "Chris, what time is it?",
    "Hey, Prison.",
])
def test_her_name_and_its_near_names_cut_when_they_call_her(heard):
    v = EB.judge_words(heard, HER)
    assert v["strength"] == 2 and v["why"] == "named", v


@pytest.mark.parametrize("heard", [
    "Chapter One.", "Thank you much.", "I don't know.",     # .101 round 17's three false cuts
    "Can you say that again?", "Did you feed the cat?", "Sorry, I'm.", "No, I meant the other one.", "Okay.", "Yes.",
    "Mm-hmm.", "I told Chris about it yesterday", "Christmas dinner was late", "Zoe, come here.",
    "The stopwatch is on the waiter's tray",                # word boundaries: never a part of a word
])
def test_anything_else_never_cuts(heard):
    assert EB.judge_words(heard, HER)["strength"] < 2


@pytest.mark.parametrize("heard,her", [
    ("I'm Crissa.", "Hi, I'm Crissa. I can read to you or answer a question."),
    ("Wait", "they were waiting at the gate for the morning train"),
    ("Hold on.", "she was holding on to the rail with both hands"),
    ("And voluntarily pause.", "whenever I find myself involuntarily pausing before coffin warehouses"),   # fixture
    ("Stop.", "the bus will stop at the corner"),
])
def test_her_own_words_never_cut_her(heard, her):
    assert EB.judge_words(heard, her)["strength"] < 2


def test_a_real_interrupt_is_not_taken_for_a_likeness_of_her_word():
    """Measured: hers_flags' loose likeness gave "cancel" to her "chance" (four letters in a row)."""
    assert EB.judge_words("Cancel that.", HER)["strength"] == 2


def test_the_first_word_of_a_two_word_interrupt_only_holds_the_cap():
    for heard in ("Hold", "Hang.", "One", "Never", "Excuse", "Hey"):
        v = EB.judge_words(heard, HER)
        assert v["strength"] == 1 and v["why"] == "prefix", (heard, v)


def test_side_talk_keeps_its_whole_decode():
    """A look with someone's words that do not cut still marks the stretch as someone's (alien), so the whole
    utterance is decoded and its words can reach the log."""
    v = EB.judge_words("Did you feed the cat?", HER)
    assert v["strength"] == 0 and v["why"] == "side_talk" and v["alien"] >= 2


def test_the_fixture_her_echo_never_cuts_and_every_interrupt_barge_does():
    """Replayed on round 17's looks (what the ASR decoded on one core): 241 looks of her echo alone, none cuts; the
    barges with an interrupt word (b01 wait, b02 hold on, b05 stop, b08 enough, b11 never mind) cut within 1.4 s of
    their onset, at both echo levels; the other seven never cut (side talk)."""
    looks = list(FIX["echo_bent"]) + [lk for run in FIX["echo_runs"] for lk in run["looks"]]
    assert len(looks) == 241
    assert not [lk["text"] for lk in looks if EB.judge_words(lk["text"], lk["her"])["strength"] >= 2]
    interrupt = {"b01", "b02", "b05", "b08", "b11"}
    for seq in FIX["barges"]:
        cut = next((lk["rel"] for lk in seq["looks"] if EB.judge_words(lk["text"], lk["her"])["strength"] >= 2), None)
        if seq["clip"] in interrupt:
            assert cut is not None and 0.0 < cut <= 1.4, (seq["clip"], seq["regime"], cut)
        else:
            assert cut is None, (seq["clip"], seq["regime"])


def test_no_voice_check_hold_on_from_anyone_cuts():
    """The look's verdict reads words only: "Hold on." cuts whoever said it."""
    import inspect
    assert list(inspect.signature(EB.judge_words).parameters) == ["heard", "spoken"]
    L = self_echo.Lines()
    t0 = time.time() - 5
    L.note(t0, None, HER)
    assert EB.words_over_her("Hold on.", t0 + 2, t0 + 3, lines=L, now=t0 + 3)["cut"] is True
    assert EB.words_over_her("Thank you much.", t0 + 2, t0 + 3, lines=L, now=t0 + 3)["cut"] is False


class _Probe:
    def __init__(self, texts):
        self.texts = list(texts)
        self.told: list = []
        self.p = EB.Probe(look=lambda pcm, sr: {"text": self.texts.pop(0) if self.texts else "", "ms": 1.0},
                          judge=lambda text, t0, t1: EB.judge_words(text, HER),
                          tell=lambda body: (self.told.append(body), {"cut": True})[1])


def test_two_prefix_looks_never_cut_and_the_whole_phrase_does():
    import numpy as np
    st = _Probe(["Hold", "Hold the"])
    eid = st.p.begin()
    for k in range(2):
        st.p.offer(eid, np.zeros(9600, np.float32), 16000, 1.0, 1.6 + 0.3 * k)
        assert st.p.idle()
    assert not st.p.fired(eid) and not st.told
    st2 = _Probe(["Hold", "Hold on"])
    eid = st2.p.begin()
    for k in range(2):
        st2.p.offer(eid, np.zeros(9600, np.float32), 16000, 1.0, 1.6 + 0.3 * k)
        assert st2.p.idle()
    assert st2.p.fired(eid) and st2.told[0]["why"] == "stop"


def test_the_kill_switch_is_round_17s_any_word_cut(monkeypatch):
    monkeypatch.setenv("CRISSA_BARGE_WORDS_ONLY", "0")
    assert not EB.words_only_on() and not EB.side_talk_on()
    assert EB.judge_words("Can you say that again?", HER)["strength"] == 2
    monkeypatch.delenv("CRISSA_BARGE_WORDS_ONLY")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)       # the house: never
    assert not EB.words_only_on() and not BB.words_only_on() and not BB.side_talk_on()


# ── the bridge: side talk ─────────────────────────────────────────────────────────────────────────────────────────────

def test_air_says_what_is_over_her_answer():
    air = BB.Air()
    t0 = 1000.0
    air.answer_begin(t0)                          # his turn is being answered; her mouth has not opened
    assert air.verdict(t0 + 0.5, t0 + 1) == (False, "not_over_her_answer")
    air.line_on(t0 + 2.0)                         # her first line
    assert air.verdict(t0 + 1.0, t0 + 3) == (False, "not_over_her_answer")     # began before her answer: his turn
    assert air.verdict(t0 + 2.3, t0 + 3) == (False, "his_turn_going_on")       # within START_GRACE_S of it
    assert air.verdict(t0 + 4.0, t0 + 5) == (True, "over_her_answer")
    air.line_off(t0 + 6.0)                        # between her pieces (or a tool's wait): still her answer
    assert air.verdict(t0 + 7.0, t0 + 8) == (True, "over_her_answer")
    air.line_on(t0 + 9.0)
    air.line_off(t0 + 12.0)
    air.answer_end(t0 + 12.1)                     # the turn is answered
    assert air.verdict(t0 + 11.5, t0 + 13) == (False, "answering_her_end")     # an answer to her closing words
    assert air.verdict(t0 + 10.0, t0 + 13) == (True, "over_her_answer")        # decoded after: still side talk
    assert air.verdict(t0 + 13.0, t0 + 14) == (False, "after_her_answer")      # the session's rules
    air.line_on(t0 + 20.0, answering=False)       # a wake's "Yeah?" is not an answer
    assert air.verdict(t0 + 20.8, t0 + 21) == (False, "after_her_answer")


class _Stop(BaseException):
    pass


def _main18(monkeypatch, tmp_path, turns, *, speaking=True, busy=True, first_line_ago=6.0, owner_pid="o",
            end_answer_ago=None):
    """ear_bridge.main() on stub turns while she answers a turn (Air: her answer's first line `first_line_ago` s ago)."""
    monkeypatch.setattr(eb, "HOME", tmp_path)
    monkeypatch.setattr(eb.heard_roll, "HOME", tmp_path)
    monkeypatch.setattr(eb.cancels, "HOME", tmp_path)
    monkeypatch.setattr(eb, "_SAY", own_voice.SayLog())
    events, sent, aborts, over = [], [], [], []
    ticks = {"n": 0, "served": None}

    def get(url, timeout=1.5):
        if url == eb.STT_TURNS:
            ticks["n"] += 1
            if ticks["n"] <= 2:
                return {"turns": []}
            if ticks["n"] <= 4:
                ticks["served"] = ticks["served"] or time.time()
                return {"turns": turns}
            if time.time() - ticks["served"] < 1.0:          # long enough for the session's gather to flush
                return {"turns": []}
            raise _Stop()
        return {}

    monkeypatch.setattr(BB, "STATE", BB.Barge())
    air = BB.Air()
    now = time.time()
    air.answer_begin(now - first_line_ago - 1.0)
    air.line_on(now - first_line_ago)
    if end_answer_ago is not None:
        air.line_off(now - end_answer_ago)
        air.answer_end(now - end_answer_ago)
    monkeypatch.setattr(BB, "AIR", air)
    for k, v in {"_get": get, "_log": events.append, "_reset_speech_logs": lambda: None,
                 "start_abort_listener": lambda *a: None, "voice_on_cached": lambda: True, "voice_on": lambda: True,
                 "_tool_hold_active": lambda: None, "request_session_look": lambda **kw: {},
                 "own_voice_gate_paths": lambda: [], "is_mouth_echo": lambda t: False,
                 "house_mouth_on_air": lambda *a, **k: False, "user_id_from_mode": lambda: owner_pid,
                 "user_voice_prints": lambda u: 3, "resume_pending": lambda now=None: False,
                 "is_oracle_feed": lambda t: False, "is_spoken_via": lambda t: True,
                 "clear_session_board": lambda: None, "listening_off": lambda why: None,
                 "schedule_dispatch": lambda u, m: sent.append((u, m)),
                 "_over_line_run": lambda u, m: over.append(u),
                 "abort_mouth": lambda reason="": (aborts.append(reason), setattr(eb, "_speaking", False)),
                 "pa_yield_for_hear": lambda *a, **k: None, "pending_phase": lambda: "",
                 "render_skips_crissa": lambda: False, "ear_still_hearing": lambda *a, **k: False,
                 "PA_PLAY": tmp_path / "announce-play.json", "QUESTION_OPENS": False, "POLL": 0.01,
                 "SEED_WAIT_S": 5.0, "_speaking": speaking, "_speak_proc": None, "_dispatch_busy": busy,
                 "_session_extend_until": time.time() + 60}.items():
        monkeypatch.setattr(eb, k, v)
    monkeypatch.setattr(eb.own_voice, "mode", lambda: "off")
    with pytest.raises(_Stop):
        eb.main()
    roll = tmp_path / "heard-roll.jsonl"
    rows = [json.loads(x) for x in roll.read_text().splitlines()] if roll.exists() else []
    return {"events": events, "sent": sent, "aborts": aborts, "over": over, "roll": rows}


def _turn(text, pid="o", start_ago=2.0, dur=1.2, **kw):
    now = time.time()
    return {"ts": now + 1.0, "text": text, "person_id": pid, "via": "ear",
            "speech_start_ts": now - start_ago, "speech_end_ts": now - start_ago + dur,
            "stamp": {"person_id": pid, "voice_emb_sim": 0.9, "source": "voice_emb"}, **kw}


def test_side_talk_over_her_line_is_no_cut_no_turn_and_one_room_row(monkeypatch, tmp_path):
    got = _main18(monkeypatch, tmp_path, [_turn("Did you feed the cat this morning?")])
    assert got["aborts"] == [] and got["sent"] == [] and got["over"] == []           # no cut, no turn, no over-line ask
    assert [e for e in got["events"] if e.get("event") == "side_talk"]
    assert not [e for e in got["events"] if e.get("event") in ("session_utt", "coalesce", "barge_cut")]
    assert len(got["roll"]) == 1
    row = got["roll"][0]
    assert row["text"] == "Did you feed the cat this morning?" and row["lane"] == "room" and row["why"] == "side talk"
    assert row["who"] == "o"


@pytest.mark.parametrize("text", ["Chapter One.", "Thank you much.", "I don't know."])
def test_the_three_false_cuts_of_101_are_side_talk(monkeypatch, tmp_path, text):
    got = _main18(monkeypatch, tmp_path, [_turn(text, pid=None)])
    assert got["aborts"] == [] and got["sent"] == [] and [r["lane"] for r in got["roll"]] == ["room"]


def test_her_name_and_wait_cuts_her(monkeypatch, tmp_path):
    got = _main18(monkeypatch, tmp_path, [_turn("Crissa, wait.")])
    assert got["aborts"] == ["name_interrupt"] and got["sent"] == []                 # stopped, nothing asked
    assert not [e for e in got["events"] if e.get("event") == "side_talk"] and got["roll"] == []


@pytest.mark.parametrize("pid", ["o", "guest2", None])
def test_hold_on_from_anyone_cuts_her_and_she_stays_stopped(monkeypatch, tmp_path, pid):
    got = _main18(monkeypatch, tmp_path, [_turn("Hold on.", pid=pid)])
    assert got["aborts"] == ["barge_turn"] and got["sent"] == []
    assert any(e.get("event") == "barge_stop" for e in got["events"])
    assert BB.STATE.cur["state"] == "took"                                           # no "go on" by herself
    assert not [e for e in got["events"] if e.get("event") == "side_talk"]


def test_an_interrupt_word_between_her_pieces_stops_the_rest(monkeypatch, tmp_path):
    """She is answering (dispatch busy) but no line is on the air this moment: "Wait, stop." still cuts (before it
    was queued and answered after her answer)."""
    got = _main18(monkeypatch, tmp_path, [_turn("Wait, stop.", pid="guest2")], speaking=False)
    assert got["aborts"] == ["barge_turn"] and got["sent"] == []


def test_side_talk_between_her_pieces_is_side_talk_too(monkeypatch, tmp_path):
    got = _main18(monkeypatch, tmp_path, [_turn("Pass me the salt.")], speaking=False)
    assert got["aborts"] == [] and got["sent"] == [] and [r["lane"] for r in got["roll"]] == ["room"]


def test_his_own_sentence_going_on_as_she_began_is_his_turn(monkeypatch, tmp_path):
    """Speech that began before her answer's first line (he was still finishing his request) is not side talk."""
    got = _main18(monkeypatch, tmp_path, [_turn("from Moby Dick, chapter one", start_ago=3.0)], first_line_ago=2.0)
    assert not [e for e in got["events"] if e.get("event") == "side_talk"] and got["roll"] == []
    assert [u for u, _m in got["sent"]] == ["from Moby Dick, chapter one"]


def test_after_her_answer_the_session_rules_apply(monkeypatch, tmp_path):
    got = _main18(monkeypatch, tmp_path, [_turn("What about Sunday?", start_ago=1.0)], speaking=False, busy=False,
                  end_answer_ago=3.0)
    assert not [e for e in got["events"] if e.get("event") == "side_talk"]
    assert [u for u, _m in got["sent"]] == ["What about Sunday?"]


def test_side_talk_switch_off_is_the_old_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_SIDE_TALK", "0")
    got = _main18(monkeypatch, tmp_path, [_turn("Did you feed the cat this morning?")])
    assert got["aborts"] == [] and got["roll"] == []
    assert [u for u, _m in got["sent"]] == ["Did you feed the cat this morning?"]    # to the over-line ask, as before


def test_words_only_off_is_round_17(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_BARGE_WORDS_ONLY", "0")
    got = _main18(monkeypatch, tmp_path, [_turn("Did you feed the cat this morning?")])
    assert got["aborts"] == ["barge_turn"] and got["roll"] == []


def test_setup_lines_keep_todays_path(monkeypatch, tmp_path):
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
    got = _main18(monkeypatch, tmp_path, [_turn("Did you feed the cat this morning?")])
    assert got["roll"] == [] and not [e for e in got["events"] if e.get("event") == "side_talk"]


def test_speak_blocking_marks_her_answer_on_the_air():
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text()
    assert "asr_barge.AIR.line_on(answering=bool(_dispatch_busy))" in src
    assert src.count("asr_barge.AIR.line_off()") == 2                               # the voice's end and the typed one
    assert "asr_barge.AIR.answer_begin()" in src and "asr_barge.AIR.answer_end()" in src


# ── the Wall's log: side talk is room speech ──────────────────────────────────────────────────────────────────────────

def test_the_kit_feed_shows_side_talk_as_room_speech(tmp_path, monkeypatch):
    from _kit import load as load_kit
    F = load_kit("scripts/voiceonly-home-server.py", "vo_feed_r18")
    monkeypatch.setattr(F, "HEARD_ROLL", tmp_path / "heard-roll.jsonl")
    now = time.time()
    rows = [{"ts": now - 5, "who": "o", "text": "Did you feed the cat?", "why": "side talk", "kind": "heard",
             "lane": "room"},
            {"ts": now - 3, "who": "o", "text": "Keep reading", "why": "queued: she answers after this", "kind": "heard"}]
    (tmp_path / "heard-roll.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    heard = F.recent_heard_roll(now=now)
    side = [h for h in heard if h.get("lane") == "room"][0]
    assert side["text"] == "Did you feed the cat?" and side["log_room"] is True and side["log_user"] is False
    other = [h for h in heard if not h.get("lane")][0]
    assert other["log_user"] is True and "·" in other["text"]                       # unchanged
    ear = {"who": "?", "text": "Did you feed the cat?", "ts": now - 5.2, "source": "id_off", "kind": "speech"}
    chat = F.one_row_chat([ear], heard, None)
    side_rows = [c for c in chat if c["text"] == "Did you feed the cat?"]
    assert len(side_rows) == 1 and side_rows[0]["kind"] == "speech" and side_rows[0]["log_room"] is True
    assert side_rows[0]["replaces"][0][1] == "Did you feed the cat?"                 # it took the ear's own row



import pytest as _pytest_r29


@_pytest_r29.fixture(autouse=True)
def _round29_old_rule(monkeypatch):
    """This file pins round 18's barge rules; round 29 builds .101's three-word barge and words-over-her-line-to-her-model
    (the User 2026-10-17), tested in tests/ear/test_round29_101_fixes_20261017.py. Here they run under their kill
    switches, and Chris/Krista stay her-name-near words (address-only off)."""
    monkeypatch.setenv("CRISSA_BARGE_WORDS_TO_CUT", "0")
    monkeypatch.setenv("EAR_BRIDGE_SIDE_TALK_ASK", "0")
    monkeypatch.setenv("EAR_BRIDGE_WAKE_ADDRESS_ONLY", "0")
