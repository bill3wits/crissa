"""Round 17: cut the two fixed 2-second waits between the person and her (the User 2026-10-13: "yes cut the waits").

Measured (checkpoint round17-id-off-20261013): end of his speech -> her first sound p50 5.06 s on one core, about 4 s
of it two fixed waits - the ear's 2.0 s hangover that closed his utterance (RAIL_STT_HANGOVER_SEC) and the bridge's
2.0 s quiet before her mouth opened (EAR_BRIDGE_SPEAK_QUIET_SEC). In a pack (checkpoint round17-waits-20261013):

1. The talk settings carry `turn` (config/crissa-talk.json, lib/crissa_talk.FALLBACK, both modules' DEFAULTS agree);
   the house keeps its waits; the kills (CRISSA_EAR_SPECULATE=0, CRISSA_SPEAK_GAP_S) and overrides.
2. The ear (crissa_ear/turn_end.py): the conversation's hangover is turn.end_s (0.6 s), a paused sentence closes as
   two chunks (one under the kill), and `speech_at` says when he was last heard - never her own line on the air.
3. The bridge (crissa_bridge/speculate.py): held / final / restart / refuse / commit; the poll thread's own lines.
4. The bridge's wiring: the mouth's gate, schedule_dispatch's restart, the gather, the poll's before/after order.
5. End to end in the bridge: her turn on half a sentence is cancelled silently when he goes on, the whole utterance is
   asked again and answered once; nothing of the first is said or kept.
6. A speculative turn never runs a tool (speculate.tool_guard): a stub gateway that streams a tool frame and runs the
   tool only while the bridge still reads (the contract the revise turn and Security's tool check rely on). On the
   speculative ask the read stops before the tool runs; the same words are asked once final and the tool runs exactly
   once, after final; a tool on a final turn runs once with no restart; he goes on during the wait: the whole
   utterance, once; her own "go on" after a barge-in cut is no speculation.

No model, network, microphone, camera or sound: every service is a stub or a monkeypatch.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "modules" / "ear", ROOT / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_home  # noqa: E402
import crissa_talk  # noqa: E402
from crissa_bridge import ear_bridge as eb  # noqa: E402
from crissa_bridge import speculate  # noqa: E402
from crissa_ear import ear_loop, turn_end  # noqa: E402

ENV = ("CRISSA_EAR_SPECULATE", "CRISSA_SPEAK_GAP_S", "CRISSA_EAR_END_S", "CRISSA_TURN_FINAL_S")


@pytest.fixture()
def pack(monkeypatch):
    """A pack (this tree's manifest is the house's), no environment overrides, and no turn in flight."""
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    speculate.reset(); turn_end.reset()
    _ig = sys.modules.get("crissa_bridge.id_gate")
    if _ig is not None:                            # round 20: no face grace or turn words left by an earlier test
        _ig.grace_end("test"); _ig.turn_text("")
    speculate.finished()
    monkeypatch.setitem(speculate._POLL, "thread", None)
    yield
    speculate.finished()
    speculate.reset(); turn_end.reset()


def _quiet(age: float = 5.0) -> dict:
    return {"in_utterance": False, "decoding": False, "speech_at": time.time() - age}


# ---- 1: the settings -----------------------------------------------------------------------------------------------

def test_1_the_talk_file_and_every_copy_agree():
    shipped = json.loads((ROOT / "config" / "crissa-talk.json").read_text(encoding="utf-8"))["turn"]
    want = {"speculate": True, "end_s": 0.6, "final_s": 2.0, "gap_s": 0.3}
    assert {k: shipped[k] for k in want} == want and "_doc" in shipped
    assert crissa_talk.FALLBACK["turn"] == want
    assert speculate.DEFAULTS == want and turn_end.DEFAULTS == want
    assert crissa_talk.load(env={})["turn"] == want                     # the shipped file, merged
    assert crissa_talk.validate({"turn": {"gap_s": 0.5, "speculate": False}}) == \
        {"turn": {"gap_s": 0.5, "speculate": False}}
    assert ("turn", "gap_s") in crissa_talk.ENV and crissa_talk.ENV[("turn", "gap_s")] == "CRISSA_SPEAK_GAP_S"


def test_1_the_house_keeps_both_waits(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert turn_end.on() is False and turn_end.engaged_hangover(2.0) == 2.0 and turn_end.state() == {}
    assert speculate.on() is False and speculate.gap(2.0) == 2.0 and speculate.flush(0.3) == 0.3
    assert speculate.held(status={"in_utterance": True}) is False and speculate.final_ok() is True
    speculate.begin("half", {}, 0)
    assert speculate.restart("more", {}, vocative=False, token=0) is None   # no speculation: the old coalesce


def test_1_a_pack_ends_his_turn_sooner_and_speaks_sooner(pack):
    assert turn_end.on() is True and turn_end.engaged_hangover(2.0) == 0.6
    assert speculate.on() is True and speculate.gap(2.0) == 0.3 and speculate.final_s() == 2.0
    assert speculate.flush(0.3) == 0.0                                  # dispatched as it lands
    assert speculate.flush(5.0, cut_mid_sentence=True) == 5.0           # a block cut at the cap still gathers


def test_1_the_kills_and_overrides(pack, monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_EAR_SPECULATE", "0")
    assert turn_end.engaged_hangover(2.0) == 2.0 and speculate.on() is False and speculate.flush(0.3) == 0.3
    assert speculate.gap(2.0) == 0.3                                    # the gap is its own switch
    monkeypatch.setenv("CRISSA_SPEAK_GAP_S", "2")
    assert speculate.gap(2.0) == 2.0                                    # round 16's quiet
    monkeypatch.delenv("CRISSA_EAR_SPECULATE")
    monkeypatch.setenv("CRISSA_EAR_END_S", "0.9")
    monkeypatch.setenv("CRISSA_TURN_FINAL_S", "1.4")
    assert turn_end.engaged_hangover(2.0) == 0.9 and speculate.final_s() == 1.4
    monkeypatch.setenv("CRISSA_EAR_END_S", "0.05")
    assert turn_end.engaged_hangover(2.0) == 0.3                        # never under one frame
    # an install's talk.json (Init writes it) sits over the shipped file and is re-read when it changes
    monkeypatch.delenv("CRISSA_SPEAK_GAP_S")
    monkeypatch.setattr(speculate, "HOME", tmp_path)
    (tmp_path / "talk.json").write_text(json.dumps({"turn": {"gap_s": 0.5}}))
    assert speculate.gap(2.0) == 0.5
    (tmp_path / "talk.json").write_text(json.dumps({"turn": {"gap_s": 0.45, "speculate": False}}))
    assert speculate.gap(2.0) == 0.45 and speculate.on() is False


# ---- 2: the ear --------------------------------------------------------------------------------------------------

def test_2_speech_at_is_his_last_frame_never_hers(pack, monkeypatch):
    monkeypatch.setitem(turn_end._LAST, "speech_at", 0.0)
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: False)
    turn_end.note_frame(False, now=100.0)
    assert turn_end._LAST["speech_at"] == 0.0
    turn_end.note_frame(True, now=101.0)
    assert turn_end.state() == {"speech_at": 101.0, "speculate": True}
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: True)    # her own line coming back
    turn_end.note_frame(True, now=102.0)
    assert turn_end._LAST["speech_at"] == 101.0


def _run_ear(monkeypatch, frames: list) -> tuple[list, dict]:
    """The ear's own loop over `frames` (True = a speech frame): the VAD and the microphone are stubs."""
    q = list(frames)
    lock = threading.Lock()

    def record_seconds(seconds, mic="auto", sample_rate=16000):
        with lock:
            sp = q.pop(0) if q else False
        if not q:
            time.sleep(0.01)
        return np.full(int(seconds * sample_rate), 0.5 if sp else 0.0, np.float32)

    monkeypatch.setattr(ear_loop.audio_mod, "record_seconds", record_seconds)
    monkeypatch.setattr(ear_loop, "detect_speech_spans",
                        lambda wav, sr, **k: [(0.0, 0.3)] if float(np.max(wav)) > 0.25 else [])
    from crissa_ear import mic_select
    monkeypatch.setattr(mic_select, "close_device", lambda: None)
    monkeypatch.setattr(ear_loop.EarLoop, "_voice_locked_off", lambda self: False)
    monkeypatch.setattr(ear_loop.EarLoop, "_hear_session_open", lambda self: True)
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: False)
    monkeypatch.delenv("RAIL_STT_HANGOVER_SEC", raising=False)
    chunks = []
    from crissa_ear.config import Config
    ear = ear_loop.EarLoop()
    ear.open(cfg=Config(), process_chunk=lambda wav, sr, voice_fut=None: chunks.append(len(wav) / sr) or {"text": ""},
             listen_lock=threading.Lock(), frame_sec=0.3, hangover_sec=2.0, max_utter_sec=30.0,
             idle_timeout_sec=1e6, max_open_sec=1e6)
    t = time.time()
    while (q or ear.status().get("decoding")) and time.time() - t < 10:
        time.sleep(0.02)
    time.sleep(0.2)
    st = ear.status()
    ear.close()
    return chunks, st


SENTENCE = [True] * 3 + [False] * 2 + [True] * 3 + [False] * 9          # a 0.6 s pause inside, then the end


def test_2_a_pause_of_two_frames_closes_his_utterance_in_a_pack(pack, monkeypatch):
    chunks, st = _run_ear(monkeypatch, SENTENCE)
    assert len(chunks) == 2                                            # 0.6 s: the end of a turn here
    assert st["hangover_sec"] == 0.6 and st["speculate"] is True and st["speech_at"] > 0


def test_2_under_the_kill_the_sentence_stays_one_utterance(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_EAR_SPECULATE", "0")
    chunks, st = _run_ear(monkeypatch, SENTENCE)
    assert len(chunks) == 1 and st["hangover_sec"] == 2.0 and "speech_at" not in st


# ---- 3: the bridge's speculation -------------------------------------------------------------------------------------

def test_3_held_reads_the_ear(pack):
    now = time.time()
    assert speculate.held(now, {"in_utterance": True}) is True
    assert speculate.held(now, {"decoding": True}) is True
    assert speculate.held(now, {"speech_at": now - 1.6}) is True       # heard 1.6 s ago: final_s is 2.0
    assert speculate.held(now, {"speech_at": now - 2.2}) is False
    assert speculate.held(now, {}) is False                            # the ear's absence never holds her


def test_3_final_is_decided_by_the_poll_and_goes_stale(pack):
    speculate.begin("what is the weather", {"kind": "session"}, 7)
    assert speculate.final_ok() is False                               # no poll has looked yet
    speculate.after_poll({"in_utterance": True})
    assert speculate.final_ok() is False
    speculate.after_poll(_quiet())
    assert speculate.final_ok() is True
    assert speculate.final_ok(now=time.time() + speculate.STALE_S + 0.1) is False   # the poll stopped: not trusted
    speculate.after_poll({"decoding": True})                           # he went on: taken back
    assert speculate.final_ok() is False
    speculate.after_poll({})                                           # the ear does not answer: not held
    assert speculate.final_ok() is True
    assert speculate.commit(7) is True and speculate.final_ok() is True and not speculate.uncommitted()


def test_3_restart_cancels_once_and_refuses_its_mouth(pack):
    speculate.begin("Put the board up on the wall", {"kind": "session", "person_id": None}, 7)
    assert speculate.restart("and tell me what is open", {"stamp": {"x": 1}}, vocative=True, token=7) is None
    got = speculate.restart("and tell me what is open", {"stamp": {"x": 1}}, vocative=False, token=7)
    assert got[0] == "Put the board up on the wall and tell me what is open"
    assert got[1]["stamp"] == {"x": 1} and got[1]["kind"] == "session" and got[1]["spec_restart"] == 1
    assert speculate.restart("again", {}, vocative=False, token=8) is None   # cancelled already: the old coalesce
    assert speculate.refuse(7) and speculate.refuse(8) and not speculate.refuse(9)
    assert speculate.commit(8) is False and speculate.final_ok() is False
    speculate.finished()                                               # its dispatch ended: nothing in flight
    assert not speculate.refuse(8) and speculate.final_ok() is True


def test_3_a_committed_turn_is_no_speculation(pack):
    speculate.begin("What time is it", {}, 3)
    speculate.after_poll(_quiet())
    assert speculate.commit(3) is True
    assert speculate.restart("and the date", {}, vocative=False, token=3) is None


def test_3_the_polls_own_line_never_waits_on_the_poll(pack, monkeypatch):
    speculate.before_poll()                                            # this thread is the poll
    speculate.begin("half a sentence", {}, 5)
    assert speculate.final_ok() is True                                # a house command's answer said on the poll
    assert speculate.commit(5) is True and speculate.uncommitted()     # ...is not her turn's commit
    got = speculate.restart("rest", {}, vocative=False, token=5)
    assert got is not None and speculate.refuse(5) is False            # nor is it refused for the cancelled turn
    out = {}
    t = threading.Thread(target=lambda: out.update(r=speculate.refuse(5)))
    t.start(); t.join()
    assert out["r"] is True                                            # her turn's own thread is


# ---- 4: the wiring ---------------------------------------------------------------------------------------------------

def test_4_the_mouths_gate_waits_for_a_final_turn(pack, monkeypatch):
    status = {"v": {"in_utterance": True}}
    monkeypatch.setattr(speculate, "ear_status", lambda: dict(status["v"]))
    monkeypatch.setattr(eb, "_last_user_speech", time.time() - 5)
    tok = eb._cancel_token
    speculate.begin("half", {}, tok)
    speculate.after_poll(status["v"])
    out = {}
    th = threading.Thread(target=lambda: out.update(ok=eb._wait_speak_quiet(tok)))
    th.start()
    time.sleep(0.3)
    assert th.is_alive()                                               # he is still talking: her mouth waits
    status["v"] = _quiet()
    stop = time.time() + 2.0
    while th.is_alive() and time.time() < stop:
        speculate.after_poll(status["v"])                              # the bridge's poll
        time.sleep(0.05)
    th.join(1.0)
    assert out.get("ok") is True and not speculate.uncommitted()


def test_4_the_gate_refuses_a_cancelled_turn_and_moves_the_token(pack, monkeypatch):
    monkeypatch.setattr(speculate, "ear_status", lambda: _quiet())
    monkeypatch.setattr(eb, "_last_user_speech", time.time() - 5)
    tok = eb._cancel_token
    speculate.begin("half", {}, tok)
    assert speculate.restart("rest", {}, vocative=False, token=tok) is not None
    # a turn that read the token after the cancel moved it (tok + 1) is refused too, and the token moves past it
    monkeypatch.setattr(eb, "_cancel_token", tok + 1)
    assert eb._wait_speak_quiet(tok + 1) is False and eb._cancel_token == tok + 2


def test_4_schedule_dispatch_restarts_a_speculation_and_coalesces_after_her_mouth(pack, monkeypatch):
    logs = []
    monkeypatch.setattr(eb, "_log", lambda ev: logs.append(ev))
    monkeypatch.setattr(eb, "_get", lambda url, timeout=1.0: {})
    monkeypatch.setattr(eb, "try_handle_utterance", lambda *a, **k: False)
    monkeypatch.setattr(eb.id_gate, "gating", lambda: False)
    monkeypatch.setattr(eb.id_gate, "house_command", lambda *a, **k: False)
    notes = []
    monkeypatch.setattr(eb.heard_roll, "note", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(eb, "_dispatch_busy", True)
    monkeypatch.setattr(eb, "_pending", None)
    tok = eb._cancel_token
    speculate.begin("Put the board up on the wall", {"kind": "session"}, tok)
    eb.schedule_dispatch("and tell me what is open", {"kind": "session"})
    assert eb._cancel_token == tok + 1                                 # her turn in flight stops, silently
    assert eb._pending[0] == "Put the board up on the wall and tell me what is open"
    assert [e["event"] for e in logs] == ["spec_restart"] and notes == []   # no "queued" line on the Wall
    # her mouth already opened: the old rule (his words wait behind her line)
    speculate.finished()
    monkeypatch.setattr(eb, "_pending", None)
    speculate.begin("What time is it", {}, eb._cancel_token)
    speculate.after_poll(_quiet())
    assert speculate.commit(eb._cancel_token)
    eb.schedule_dispatch("and the date", {"kind": "session"})
    assert eb._pending[0] == "and the date" and logs[-1]["event"] == "coalesce" and notes


def test_4_the_poll_reads_the_ear_before_its_fetch_and_decides_after_its_turns():
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text(encoding="utf-8")
    main = src.split("\ndef main() -> int:")[1]
    a = main.index("_spec_st = speculate.before_poll()")
    b = main.index("data = _get(STT_TURNS, timeout=4.0)")
    c = main.index("schedule_dispatch(user, m)")                       # the gather's dispatch
    d = main.index("speculate.after_poll(_spec_st)")
    assert a < b < c < d
    assert "need_quiet = speculate.flush(need_quiet, cut_mid_sentence)" in main
    fin = src.split("def _finish_dispatch(")[1].split("\ndef ")[0]
    assert fin.index("speculate.finished()") < fin.index("schedule_dispatch(u2, m2)")


# ---- 5: end to end in the bridge -------------------------------------------------------------------------------------

def test_5_half_a_sentence_is_cancelled_silently_and_the_whole_one_answered_once(pack, monkeypatch):
    status = {"v": {"in_utterance": True}}                             # he is still talking when her turn starts
    monkeypatch.setattr(speculate, "ear_status", lambda: dict(status["v"]))
    logs, said, asked = [], [], []
    monkeypatch.setattr(eb, "_log", lambda ev: logs.append(ev))
    monkeypatch.setattr(eb, "_get", lambda url, timeout=1.0: {"headcount": 1, "people": []})
    monkeypatch.setattr(eb, "try_handle_utterance", lambda *a, **k: False)
    monkeypatch.setattr(eb.id_gate, "gating", lambda: False)
    monkeypatch.setattr(eb.id_gate, "house_command", lambda *a, **k: False)
    monkeypatch.setattr(eb, "authorize", lambda turn, presence: "bau")
    monkeypatch.setattr(eb, "evaluate_turn", lambda *a, **k: {"auth": True, "level": "bau"})
    monkeypatch.setattr(eb, "_gateway", lambda p="crissa": {"url": "http://127.0.0.1:9/v1/chat/completions",
                                                          "model": "stub", "key": "", "profile": "crissa",
                                                          "voice": "crissa"})
    monkeypatch.setattr(eb, "HISTORY", False)
    monkeypatch.setattr(eb, "HOUSE_MEMORY", ROOT / "tests" / "no-such-house-memory.py")   # a pack ships none
    monkeypatch.setattr(eb, "DOC_WINDOW", ROOT / "tests" / "no-such-doc-window.sh")
    monkeypatch.setattr(eb, "_dispatch_busy", False)
    monkeypatch.setattr(eb, "_pending", None)
    monkeypatch.setattr(eb, "_last_user_speech", time.time() - 5)
    eb._set_hear_session(True)

    def fake_stream(url, payload, **kw):
        user = [m for m in payload["messages"] if m.get("role") == "user"][-1]["content"]
        asked.append(user)
        acc = "You asked: %s. That is all." % user.split("\n")[-1][:60]
        kw["on_delta"](acc)
        stop = time.time() + 1.0
        while time.time() < stop and not (kw.get("stop") and kw["stop"]()):
            time.sleep(0.02)
        return acc
    monkeypatch.setattr(eb, "_stream_chat_text", fake_stream)

    def fake_speak(text, *, skip_quiet=False, **kw):
        tok = eb._cancel_token
        if not skip_quiet and not eb._wait_speak_quiet(tok):
            return
        if eb._cancel_token == tok:
            said.append(text)
    monkeypatch.setattr(eb, "speak_blocking", fake_speak)

    ticking = {"on": True}

    def poll():                                                        # the bridge's poll: final or not, every 50 ms
        while ticking["on"]:
            speculate.after_poll(dict(status["v"]))
            time.sleep(0.05)
    tp = threading.Thread(target=poll, daemon=True)
    tp.start()
    try:
        eb.schedule_dispatch("Put the board up on the wall", {"kind": "session", "session_id": "t"})
        t = time.time()
        while not asked and time.time() - t < 3:
            time.sleep(0.02)
        time.sleep(0.2)
        assert said == []                                              # her clause waits: he is still talking
        eb.schedule_dispatch("and tell me which cards are open", {"kind": "session", "session_id": "t"})
        status["v"] = _quiet()                                         # then he stops
        t = time.time()
        while not said and time.time() - t < 6:
            time.sleep(0.05)
        t = time.time()
        while eb._dispatch_busy and time.time() - t < 6:
            time.sleep(0.05)
    finally:
        ticking["on"] = False
        tp.join(1.0)
        eb._set_hear_session(False)
    assert len(asked) == 2 and "and tell me which cards are open" in asked[1] and "Put the board up" in asked[1]
    assert said and "tell me which cards" in said[0]                    # her first clause is the whole one's
    assert not any(s.startswith("You asked: Put the board up on the wall.") for s in said)   # nothing of the half
    ev = [e.get("event") for e in logs]
    assert ev.count("spec_restart") == 1
    replies = [e for e in logs if e.get("event") == "reply"]
    assert len(replies) == 1 and "tell me which cards" in replies[0]["reply"]   # her reply, once, for the whole


# ---- 6: a speculative turn never runs a tool ---------------------------------------------------------------------------

def test_6_the_guard_stops_the_read_before_a_tool_until_final(pack):
    marker = lambda n: ("ran", n)  # noqa: E731
    g = speculate.tool_guard(marker)
    assert g(3) == ("ran", 3)                                          # no turn in flight: the turn's own tool
    speculate.begin("put the board up", {}, 4)
    with pytest.raises(speculate.SpecTool, match="not_final"):
        g(5)                                                           # not final yet: the read stops, no tool
    assert speculate.tool_restart(4) == ("put the board up", {"spec_at_final": True, "text": "put the board up"})
    with pytest.raises(speculate.SpecTool, match="cancelled"):
        g(6)                                                           # a cancelled turn never acts either
    speculate.finished()
    speculate.begin("what time is it", {}, 5)
    speculate.after_poll(_quiet())
    assert g(7) == ("ran", 7)                                          # final: the tool is the turn's
    assert speculate.restart("and the date", {}, vocative=False, token=5) is None   # never a silent cancel after it
    assert speculate.STATS["tool_before_final"] == 0


def test_6_the_house_and_the_kill_leave_on_tool_alone(pack, monkeypatch):
    marker = lambda n: n  # noqa: E731
    monkeypatch.setenv("CRISSA_EAR_SPECULATE", "0")
    assert speculate.tool_guard(marker) is marker
    monkeypatch.delenv("CRISSA_EAR_SPECULATE")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert speculate.tool_guard(marker) is marker


def test_6_her_go_on_after_a_barge_in_cut_is_no_speculation(pack):
    speculate.begin("Go on.", {"barge_resume": True, "resume_level": "bau"}, 9)
    assert speculate._CUR["rec"] is None
    assert speculate.restart("and more", {}, vocative=False, token=9) is None   # the barge-in's own rules


def test_6_before_ask_waits_for_final_and_gives_up_or_yields(pack):
    speculate.begin("put the board up", {"spec_at_final": True}, 1)
    clock = {"t": 0.0}

    def tick(_dt):
        clock["t"] += 0.05
        if clock["t"] > 0.3:
            speculate.after_poll(_quiet())
    assert speculate.before_ask(now_fn=lambda: clock["t"], sleep=tick) is True
    assert speculate._CUR["rec"]["tools_ok"] is True and speculate.STATS["tool_asked_at_final"] >= 1
    speculate.finished()
    speculate.begin("put the board up", {"spec_at_final": True}, 1)
    speculate.restart("on the left", {}, vocative=False, token=1)       # he went on while it waited
    assert speculate.before_ask(now_fn=lambda: 0.0, sleep=lambda d: None) is False


def _tool_bridge(monkeypatch, status, *, run_delay=0.0, first_s=0.0):
    """The bridge's real dispatch, gate and guard against a stub gateway that streams a tool frame and then RUNS the
    tool only if the bridge is still reading (a raise from on_tool = the bridge closed the stream: the agent stops)."""
    monkeypatch.setattr(speculate, "ear_status", lambda: dict(status["v"]))
    logs, said, asked, ran = [], [], [], []
    monkeypatch.setattr(eb, "_log", lambda ev: logs.append(ev))
    monkeypatch.setattr(eb, "_get", lambda url, timeout=1.0: {"headcount": 1, "people": []})
    monkeypatch.setattr(eb, "try_handle_utterance", lambda *a, **k: False)
    monkeypatch.setattr(eb.id_gate, "gating", lambda: False)
    monkeypatch.setattr(eb.id_gate, "mode", lambda: "off")              # no face check at the tool: the guard alone
    monkeypatch.setattr(eb.id_gate, "house_command", lambda *a, **k: False)
    monkeypatch.setattr(eb, "authorize", lambda turn, presence: "bau")
    monkeypatch.setattr(eb, "evaluate_turn", lambda *a, **k: {"auth": True, "level": "bau"})
    monkeypatch.setattr(eb, "claim_checked", lambda content, **kw: content)
    monkeypatch.setattr(eb, "_gateway", lambda p="crissa": {"url": "http://127.0.0.1:9/v1/chat/completions",
                                                          "model": "stub", "key": "", "profile": "crissa",
                                                          "voice": "crissa"})
    monkeypatch.setattr(eb, "HISTORY", False)
    monkeypatch.setattr(eb, "HOUSE_MEMORY", ROOT / "tests" / "no-such-house-memory.py")
    monkeypatch.setattr(eb, "DOC_WINDOW", ROOT / "tests" / "no-such-doc-window.sh")
    monkeypatch.setattr(eb, "_dispatch_busy", False)
    monkeypatch.setattr(eb, "_pending", None)
    monkeypatch.setattr(eb, "_last_user_speech", time.time() - 5)

    def gateway(url, payload, **kw):
        user = [m for m in payload["messages"] if m.get("role") == "user"][-1]["content"].split("\n")[-1]
        asked.append((time.time(), user))
        time.sleep(first_s)                        # the model's first token
        acc = "Sure, one moment."
        kw["on_delta"](acc)
        kw["on_tool"](len(acc))                    # the tool call's frame; a raise is the bridge closing the stream
        time.sleep(run_delay)
        ran.append((time.time(), user))            # still read: the agent runs the tool
        acc += " The board is up for: %s." % user[:60]
        kw["on_delta"](acc)
        return acc
    monkeypatch.setattr(eb, "_stream_chat_text", gateway)

    def fake_speak(text, *, skip_quiet=False, **kw):
        tok = eb._cancel_token
        if not skip_quiet and not eb._wait_speak_quiet(tok):
            return
        if eb._cancel_token == tok:
            said.append((time.time(), text))
    monkeypatch.setattr(eb, "speak_blocking", fake_speak)
    return logs, said, asked, ran


def _poller(status):
    ticking = {"on": True}

    def poll():                                    # the bridge's poll: final or not, every 50 ms
        while ticking["on"]:
            speculate.after_poll(dict(status["v"]))
            time.sleep(0.05)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    return ticking, th


def _settle(cond, s=6.0):
    t = time.time()
    while not cond() and time.time() - t < s:
        time.sleep(0.05)


def test_6_a_tool_asked_before_final_runs_exactly_once_after_final(pack, monkeypatch):
    status = {"v": {"in_utterance": True}}                             # he may still go on
    logs, said, asked, ran = _tool_bridge(monkeypatch, status)
    s0 = dict(speculate.STATS)
    eb._set_hear_session(True)
    ticking, tp = _poller(status)
    try:
        eb.schedule_dispatch("Put the board up on the wall", {"kind": "session", "session_id": "t"})
        _settle(lambda: len(asked) >= 1)
        time.sleep(0.4)
        assert ran == [] and said == []                                # the read stopped before the tool; nothing said
        t_final = time.time()
        status["v"] = _quiet()                                         # his turn is final now
        _settle(lambda: ran and said and not eb._dispatch_busy)
    finally:
        ticking["on"] = False
        tp.join(1.0)
        eb._set_hear_session(False)
    assert len(ran) == 1 and ran[0][0] >= t_final                      # exactly once, after final
    assert len(asked) == 2 and asked[0][1] == asked[1][1]              # the same words, asked again once final
    assert said and said[0][0] >= t_final and said[0][1] == "Sure, one moment."
    ev = [e.get("event") for e in logs]
    assert ev.count("spec_tool") == 1 and ev.count("reply") == 1
    assert speculate.STATS["tool_restarted"] - s0["tool_restarted"] == 1
    assert speculate.STATS["tool_asked_at_final"] - s0["tool_asked_at_final"] == 1
    assert speculate.STATS["tool_before_final"] == s0["tool_before_final"]


def test_6_a_tool_on_a_final_turn_runs_once_with_no_restart(pack, monkeypatch):
    status = {"v": _quiet()}                                           # he has stopped: final from the first poll
    logs, said, asked, ran = _tool_bridge(monkeypatch, status, first_s=0.3)
    eb._set_hear_session(True)
    ticking, tp = _poller(status)
    try:
        time.sleep(0.15)
        speculate.after_poll(_quiet())
        eb.schedule_dispatch("What is on the board", {"kind": "session", "session_id": "t"})
        _settle(lambda: ran and said and not eb._dispatch_busy)
    finally:
        ticking["on"] = False
        tp.join(1.0)
        eb._set_hear_session(False)
    assert len(asked) == 1 and len(ran) == 1 and said
    assert "spec_tool" not in [e.get("event") for e in logs]


def test_6_he_goes_on_while_the_tool_turn_waits_the_whole_utterance_runs_it_once(pack, monkeypatch):
    status = {"v": {"in_utterance": True}}
    logs, said, asked, ran = _tool_bridge(monkeypatch, status)
    eb._set_hear_session(True)
    ticking, tp = _poller(status)
    try:
        eb.schedule_dispatch("Put the board up", {"kind": "session", "session_id": "t"})
        _settle(lambda: any(e.get("event") == "spec_tool" for e in logs))
        _settle(lambda: speculate._CUR["rec"] is not None and speculate._CUR["rec"].get("ask_at_final"), 3.0)
        eb.schedule_dispatch("on the left wall", {"kind": "session", "session_id": "t"})
        status["v"] = _quiet()
        _settle(lambda: ran and said and not eb._dispatch_busy)
    finally:
        ticking["on"] = False
        tp.join(1.0)
        eb._set_hear_session(False)
    assert len(ran) == 1 and ran[0][1].endswith("Put the board up on the left wall")
    assert [u for _, u in asked][-1].endswith("Put the board up on the left wall")
    assert all(not u.endswith("Put the board up") or i == 0 for i, (_, u) in enumerate(asked))
