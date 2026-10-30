"""Round 17, barge-in straight from the ASR (the User 2026-10-13: "for barge, can we send signal DIRECTLY from ASR on word
penetration? She can always start up again. Also, be sure barge streams to her. No check on voice signature before barge
makes it more natural. Be sure not to interfere with Auth but do allow the cutoff as close as is possible to the barge
words." - "people do not continue speaking until they figure out who interrupted you know?").

The ear's looks (crissa_ear/barge.py), the ASR's look lane (modules/voice/scripts/crissa-asr.py), and the bridge's cut,
turn and resume (crissa_bridge/barge.py + ear_bridge). No model, sound or network: stubs and 127.0.0.1 only. The look
fixture is what the kit's ASR decoded from synthetic speech (Kokoro stock voices, 2026-10-13), never a person.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from crissa_bridge import barge as BB
from crissa_bridge import ear_bridge as eb
from crissa_ear import barge as EB
from crissa_ear import ear_loop, self_echo

ROOT = Path(__file__).resolve().parents[2]
FIX = json.loads((Path(__file__).parent / "fixtures" / "round17-barge-looks-20261013.json").read_text())


@pytest.fixture(autouse=True)
def _round_17_rule(monkeypatch):
    """Round 17's any-word cut is round 18's kill switch path (CRISSA_BARGE_WORDS_ONLY=0); round 18's interrupt words
    and side talk are tests/ear/test_round18_barge_words_20261013.py."""
    monkeypatch.setenv("CRISSA_BARGE_WORDS_ONLY", "0")


# ── the ear: is it hers? words only ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("look", FIX["echo_bent"], ids=[x["text"] for x in FIX["echo_bent"]])
def test_her_echo_bent_by_the_asr_never_cuts_her(look):
    """The looks of her own echo that round 16's line match took for someone's: none is strong (a cut by itself)."""
    v = EB.judge_words(look["text"], look["her"])
    assert v["strength"] <= 1, v


def test_no_echo_sequence_of_the_fixture_cuts_her():
    """Whole runs of looks at her echo alone (the probe's rule: strong at once, weak only twice in a row)."""
    for run in FIX["echo_runs"]:
        prev = None
        for lk in run["looks"]:
            v = EB.judge_words(lk["text"], lk["her"])
            s, own, n = v["strength"], set(v.get("own") or ()), len(v.get("words") or ())
            cut = s == 2 or (s == 1 and prev is not None and prev[0] >= 1 and lk["t"] - prev[1] <= EB.CONFIRM_S + 1e-6
                             and (bool(own - prev[2]) or n > prev[3]))
            assert not cut, (run["line"], lk["text"])
            prev = (s, lk["t"], own, n)


@pytest.mark.parametrize("seq", FIX["barges"], ids=[x["clip"] + "-" + x["regime"] for x in FIX["barges"]])
def test_every_barge_of_the_fixture_cuts_within_its_first_looks(seq):
    prev, cut_at = None, None
    for lk in seq["looks"]:
        v = EB.judge_words(lk["text"], lk["her"])
        s, own, n = v["strength"], set(v.get("own") or ()), len(v.get("words") or ())
        if s == 2 or (s == 1 and prev is not None and prev[0] >= 1 and lk["rel"] - prev[1] <= EB.CONFIRM_S + 1e-6
                      and (own - prev[2] or n > prev[3])):
            cut_at = lk["rel"]
            break
        prev = (s, lk["rel"], own, n)
    assert cut_at is not None and cut_at <= 1.6, seq["clip"]


HER = "Sure. The weather tomorrow looks mild in the morning, with clouds building after lunch and a chance of light rain."


@pytest.mark.parametrize("heard,want", [
    ("Wait.", 2), ("Hold on.", 2), ("Stop.", 2), ("No, I.", 2), ("Never mind.", 2),
    ("Actually.", 1),                       # one word of someone's: weak, needs the next look to add to it
    ("Sorry, I'm.", 2), ("Excuse me.", 2), ("Hey.", 2),   # asking for the floor
    ("A quick question about.", 2),
    ("Mm-hmm.", 0), ("Yeah.", 0), ("Okay.", 0),            # a listener's words never cut
    ("The weather tomorrow looks mild.", 0),
    ("With class.", 1),                     # her "with clouds" bent: one word, so never a cut by itself
])
def test_what_cuts_and_what_does_not(heard, want):
    assert EB.judge_words(heard, HER)["strength"] == want


def test_a_word_asking_for_the_floor_is_hers_when_she_is_saying_it():
    assert EB.judge_words("Sorry, I'm late.", "Sorry, I'm late with the weather today.")["strength"] == 0


def test_her_name_cuts_unless_she_is_saying_it():
    assert EB.judge_words("Crissa, wait.", HER)["strength"] == 2
    assert EB.judge_words("Chris, what time is it?", HER)["why"] == "named"
    mine = "Hi, I'm Crissa. I can read to you or answer a question."
    assert EB.judge_words("I'm Crissa.", mine)["strength"] == 0


def test_a_backchannel_cuts_when_its_switch_is_off(monkeypatch):
    monkeypatch.setenv("CRISSA_BARGE_BACKCHANNEL", "0")
    assert EB.judge_words("Yeah.", HER)["strength"] == 1              # someone's word, like any single one
    assert EB.judge_words("Yeah, okay.", HER)["strength"] == 2


def test_with_nothing_of_hers_known_every_word_is_someones():
    assert EB.judge_words("Wait what", "")["strength"] == 2
    assert EB.judge_words("Mm-hmm.", "")["strength"] == 0


def test_the_cut_reads_no_voice_print():
    """Rule 2: no speaker ID, no her-voice print, no guest/owner decision before the cut."""
    assert list(inspect.signature(EB.judge_words).parameters) == ["heard", "spoken"]
    assert "stamp" not in inspect.signature(EB.words_over_her).parameters
    src = (ROOT / "modules/ear/crissa_ear/barge.py").read_text()
    for word in ("voice_emb", "her_prints", "voice_live", "person_id", "stamp[", "tts_veto", "voice_bank"):
        assert word not in src, word


def test_words_over_her_reads_her_line_from_the_gate_files(tmp_path):
    L = self_echo.Lines()
    t0 = time.time() - 5
    L.note(t0, None, HER)
    assert EB.words_over_her("Looks mild in the morning.", t0 + 2, t0 + 3, lines=L, now=t0 + 3)["cut"] is False
    assert EB.words_over_her("Wait, what was that?", t0 + 2, t0 + 3, lines=L, now=t0 + 3)["cut"] is True
    assert EB.words_over_her("Thank you for watching.", t0 + 2, t0 + 3, lines=L, now=t0 + 3)["why"] == "hallucination"


# ── the ear: the probe ────────────────────────────────────────────────────────────────────────────────────────────────

class _Probe:
    """A Probe with stub look, judge and bridge."""

    def __init__(self, texts, strengths=None, answer=None, slow=0.0):
        self.texts = list(texts)
        self.strengths = dict(strengths or {})
        self.answer = {"cut": True} if answer is None else answer
        self.told: list = []
        self.windows: list = []

        def look(pcm, sr):
            self.windows.append(len(pcm) // 2)
            if slow:
                time.sleep(slow)
            return {"text": self.texts.pop(0) if self.texts else "", "ms": 1.0}

        def judge(text, t0, t1):
            s = self.strengths.get(text, 2 if text else 0)
            return {"strength": s, "cut": s >= 2, "why": "stub", "own": text.split()}

        def tell(body):
            self.told.append(body)
            return dict(self.answer) if body.get("phase") == "cut" else {"ok": True}

        self.p = EB.Probe(look=look, judge=judge, tell=tell)


def _wav(s=0.6):
    return np.zeros(int(16000 * s), np.float32)


def _wait(pred, s=3.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_a_strong_look_cuts_her_once():
    st = _Probe(["Wait, what?"])
    eid = st.p.begin()
    assert st.p.offer(eid, _wav(), 16000, 1.0, 1.6, onset_ts=1.0)
    assert _wait(lambda: st.p.fired(eid))
    cuts = [b for b in st.told if b["phase"] == "cut"]
    assert len(cuts) == 1 and cuts[0]["id"] == eid and cuts[0]["text"] == "Wait, what?"
    assert not st.p.wants(eid) and st.p.offer(eid, _wav(), 16000, 1.0, 1.9) is False


def test_one_weak_word_needs_the_next_look():
    st = _Probe(["Sorry", "Sorry I missed"], strengths={"Sorry": 1, "Sorry I missed": 1})
    eid = st.p.begin()
    st.p.offer(eid, _wav(), 16000, 1.0, 1.6)
    assert st.p.idle() and not st.p.fired(eid)
    st.p.offer(eid, _wav(0.9), 16000, 1.0, 1.9)
    assert _wait(lambda: st.p.fired(eid))
    assert st.told[-1]["why"] == "one_word_twice"


def test_the_same_weak_word_twice_is_a_bend_decoded_twice():
    st = _Probe(["With class", "With class"], strengths={"With class": 1})
    eid = st.p.begin()
    for k in range(2):
        st.p.offer(eid, _wav(), 16000, 1.0, 1.6 + 0.3 * k)
        assert st.p.idle()
    assert not st.p.fired(eid) and not st.told


def test_a_weak_word_alone_never_cuts():
    st = _Probe(["Outs", "", "Building after lunch"], strengths={"Outs": 1, "Building after lunch": 0})
    eid = st.p.begin()
    for k in range(3):
        st.p.offer(eid, _wav(), 16000, 1.0, 1.6 + 0.3 * k)
        assert st.p.idle()
    assert not st.p.fired(eid) and not st.told


def test_a_bridge_that_will_not_cut_stops_the_looks():
    st = _Probe(["Wait"], answer={"cut": False, "why": "setup"})
    eid = st.p.begin()
    st.p.offer(eid, _wav(), 16000, 1.0, 1.6)
    assert st.p.idle()
    assert not st.p.fired(eid) and not st.p.wants(eid) and st.p.stats["refused"] == 1


def test_the_newest_window_is_looked_at_never_a_queue():
    st = _Probe(["", "", ""], slow=0.15)
    eid = st.p.begin()
    st.p.offer(eid, _wav(0.6), 16000, 1.0, 1.6)
    time.sleep(0.03)
    for s in (0.9, 1.2):                   # both land while the first look runs: only the newest is looked at
        st.p.offer(eid, _wav(s), 16000, 1.0, 1.0 + s)
    assert st.p.idle()
    assert st.windows == [int(16000 * 0.6), int(16000 * 1.2)]


def test_an_episode_that_never_opened_tells_the_bridge_once():
    st = _Probe(["Wait"])
    eid = st.p.begin()
    st.p.offer(eid, _wav(), 16000, 1.0, 1.6)
    assert _wait(lambda: st.p.fired(eid))
    st.p.end(eid, "idle")
    st.p.end(eid, "idle")
    assert _wait(lambda: [b for b in st.told if b["phase"] == "end"])
    time.sleep(0.05)
    ends = [b for b in st.told if b["phase"] == "end"]
    assert len(ends) == 1 and ends[0]["turn"] is False and ends[0]["id"] == eid


def test_a_cut_after_its_episode_went_idle_tells_the_end_itself():
    st = _Probe(["Wait"], slow=0.1)
    eid = st.p.begin()
    st.p.offer(eid, _wav(), 16000, 1.0, 1.6)
    time.sleep(0.02)
    st.p.end(eid, "idle")                    # the VAD went idle while the look ran
    assert st.p.idle()
    assert _wait(lambda: [b for b in st.told if b["phase"] == "end"])
    assert [b["phase"] for b in st.told] == ["cut", "end"]


def test_an_utterance_for_the_decode_is_ended_by_the_decode():
    st = _Probe(["Wait"])
    eid = st.p.begin()
    st.p.offer(eid, _wav(), 16000, 1.0, 1.6)
    assert _wait(lambda: st.p.fired(eid))
    st.p.end(eid, "decode")
    time.sleep(0.05)
    assert [b["phase"] for b in st.told] == ["cut"]


# ── the ear: the frame loop ───────────────────────────────────────────────────────────────────────────────────────────

def _run_loop(monkeypatch, *, speech, probe=None, armed=True, on_air=True, env_off=False):
    """EarLoop._run on stub frames: `speech` = one bool per 0.3 s frame. Returns (decode metas, frames fed)."""
    if env_off:
        monkeypatch.setenv("CRISSA_BARGE_FROM_ASR", "0")
    monkeypatch.setenv("RAIL_STT_HANGOVER_SEC", "0.6")
    monkeypatch.setenv("RAIL_STT_EAR_OPEN_FRAMES", "2")              # the kit's (crissa_ear/settings.py)
    monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: on_air)
    monkeypatch.setattr(ear_loop, "detect_speech_spans", lambda wav, sr, **kw: [1] if float(wav[0]) > 0 else [])
    from crissa_ear import voice_live
    monkeypatch.setattr(voice_live, "kick", lambda pcm, sr: None)
    el = ear_loop.EarLoop()
    monkeypatch.setattr(el, "_hear_session_open", lambda: True)
    monkeypatch.setattr(el, "_barge_armed", lambda: armed)
    if probe is not None:
        monkeypatch.setattr(el, "_barge_probe", lambda cfg: (setattr(el, "_probe", probe) or probe))
    fed = {"i": 0}
    done = threading.Event()

    def cap(*, seconds, cfg, listen_lock):
        i = fed["i"]
        fed["i"] += 1
        if probe is not None:
            probe.idle()
        if i >= len(speech) + 12:
            done.set()
            time.sleep(0.05)
        on = speech[i] if i < len(speech) else False
        return np.full(4800, 0.1 if on else 0.0, np.float32), None

    monkeypatch.setattr(el, "_capture_frame", cap)
    metas = []

    def pc(wav, sr, voice_fut=None):
        m = dict(ear_loop.decode_meta())
        metas.append({**m, "dur": round(len(wav) / sr, 1)})
        return {"text": "his words" if m.get("barge_id") else "", "reason": "stub"}

    from types import SimpleNamespace
    el.open(cfg=SimpleNamespace(sample_rate=16000, mic="auto", live_ear_url=""), process_chunk=pc,
            listen_lock=threading.Lock(), chunk_sec=12, idle_timeout_sec=600, max_open_sec=3600, frame_sec=0.3,
            hangover_sec=0.6, max_utter_sec=20)
    assert done.wait(10)
    _wait(lambda: not el.status().get("decoding"))
    el.close("test")
    return metas, el


def test_the_utterance_that_cut_her_is_whole_and_carries_its_id(monkeypatch):
    """Rule 4: after the cut no on-air 4 s cap chops it; its decode carries barge_id; the bridge hears the end. (The
    frames here come faster than real time, so the trim, which reads the clock, is off: its own test is below.)"""
    monkeypatch.setenv("CRISSA_BARGE_TRIM", "0")
    st = _Probe(["Wait, what was that"] + [""] * 40)
    metas, _el = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 20, probe=st.p)
    assert len(metas) == 1 and metas[0]["dur"] >= 6.0, metas
    assert metas[0].get("barge_id") and st.p.fired(metas[0]["barge_id"])
    assert _wait(lambda: [b for b in st.told if b["phase"] == "end"])
    end = [b for b in st.told if b["phase"] == "end"][0]
    assert end["turn"] is True and end["id"] == metas[0]["barge_id"]


def test_her_echo_alone_keeps_the_on_air_cap_and_is_not_decoded_twice(monkeypatch):
    """Her echo alone: the on-air cap still chops it, and every piece the looks covered and found hers is not decoded
    again whole (SKIP_ECHO); with that switch off each piece is decoded as before."""
    st = _Probe([""] * 60)
    metas, el = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 20, probe=st.p)
    assert metas == [] and el.status()["barge_echo_skipped"] >= 2 and not st.told
    monkeypatch.setenv("CRISSA_BARGE_SKIP_ECHO", "0")
    st2 = _Probe([""] * 60)
    metas, el = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 20, probe=st2.p)
    assert len(metas) >= 2 and all(m.get("barge_episode") and not m.get("barge_id") for m in metas)


def test_a_look_that_heard_someone_keeps_the_whole_decode(monkeypatch):
    st = _Probe(["Sorry"] + [""] * 60, strengths={"Sorry": 1})                     # weak, never confirmed: no cut
    metas, _el = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 6, probe=st.p)
    assert len(metas) == 1 and not metas[0].get("barge_id") and not st.told


def test_the_first_look_starts_at_the_first_speech_frame(monkeypatch):
    """Before the utterance opens (two speech frames): the first look covers one frame before it and the hold frame."""
    st = _Probe([""] * 60)
    _run_loop(monkeypatch, speech=[False] * 3 + [True] * 3, probe=st.p)
    assert st.windows and st.windows[0] == 2 * 4800


def test_no_looks_when_she_is_not_on_the_air_or_the_switch_is_off(monkeypatch):
    st = _Probe([""] * 60)
    metas, _ = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 6, probe=st.p, armed=False)
    assert not st.windows and all(not m.get("barge_episode") for m in metas)
    metas, el = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 20, env_off=True)
    assert el._probe is None and len(metas) >= 2                                  # round 17 before this: the cap


# ── the ASR's look lane ───────────────────────────────────────────────────────────────────────────────────────────────

def _asr():
    spec = importlib.util.spec_from_file_location("crissa_asr_r17b", ROOT / "modules/voice/scripts/crissa-asr.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _RT:
    def __init__(self, asr):
        self.asr, self.calls = asr, []

    def transcribe(self, *, audio_path, max_new_tokens, **kw):
        self.calls.append({"tokens": max_new_tokens, "decode_lock": self.asr.DECODE_LOCK.locked(),
                           "probe_lock": self.asr.PROBE_LOCK.locked()})
        return "wait"


def test_a_look_never_waits_behind_a_decode(monkeypatch, tmp_path):
    asr = _asr()
    monkeypatch.setattr(asr.tempfile, "tempdir", str(tmp_path))
    rt = _RT(asr)
    monkeypatch.setitem(asr.RT, "rt", rt)
    pcm = np.zeros(16000, "<i2").tobytes()
    asr.DECODE_LOCK.acquire()                # a whole utterance is decoding
    try:
        out = asr.transcribe(pcm, 16000, probe=True)
    finally:
        asr.DECODE_LOCK.release()
    assert out["probe"] is True and out["text"] == "wait"
    assert rt.calls[-1] == {"tokens": asr.PROBE_TOKENS, "decode_lock": True, "probe_lock": True}
    asr.transcribe(pcm, 16000)
    assert rt.calls[-1]["tokens"] == 128 and rt.calls[-1]["probe_lock"] is False


def test_the_look_lane_switch_off_queues_a_look_like_any_decode(monkeypatch, tmp_path):
    asr = _asr()
    monkeypatch.setattr(asr.tempfile, "tempdir", str(tmp_path))
    rt = _RT(asr)
    monkeypatch.setitem(asr.RT, "rt", rt)
    monkeypatch.setenv("CRISSA_ASR_PROBE_LANE", "0")
    out = asr.transcribe(np.zeros(16000, "<i2").tobytes(), 16000, probe=True)
    assert "probe" not in out and rt.calls[-1]["tokens"] == 128 and rt.calls[-1]["probe_lock"] is False


# ── the bridge: the cut ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def bridge(monkeypatch, tmp_path):
    aborts, logs, sent = [], [], []
    monkeypatch.setattr(eb, "HOME", tmp_path)
    monkeypatch.setattr(eb, "abort_mouth", lambda reason="": (aborts.append(reason), setattr(eb, "_speaking", False)))
    monkeypatch.setattr(eb, "_log", logs.append)
    monkeypatch.setattr(eb, "_speaking", True)
    monkeypatch.setattr(eb, "_speak_proc", None)
    monkeypatch.setattr(eb, "_dispatch_busy", True)
    monkeypatch.setattr(eb, "_session_extend_until", 0.0)
    monkeypatch.setattr(eb, "schedule_dispatch", lambda u, m: sent.append((u, m)))
    monkeypatch.setattr(eb, "ear_still_hearing", lambda *a, **k: False)

    def no_identity(*a, **k):
        raise AssertionError("the cut touched identity or auth")

    for name in ("evaluate_turn", "authorize", "request_ensure_user_auth", "_post_json", "_get"):
        monkeypatch.setattr(eb, name, no_identity)
    monkeypatch.setattr(BB, "STATE", BB.Barge())
    BB.STATE.note_turn({"person_id": "user", "stamp": {"voice_emb_sim": 0.7}, "session_id": "s1"}, "bau")
    return {"aborts": aborts, "logs": logs, "sent": sent}


def test_the_ears_word_stops_her_at_once_and_touches_no_identity(bridge):
    got = eb.barge_from_ear({"phase": "cut", "id": "b1", "text": "Wait, what", "onset_ts": time.time() - 0.8})
    assert got["cut"] is True and bridge["aborts"] == ["barge_asr"]
    assert BB.STATE.cur["state"] == "cut" and BB.STATE.cur["partial"] == "Wait, what"
    assert eb._session_extend_until > time.time() and eb.hear_session_open()
    again = eb.barge_from_ear({"phase": "cut", "id": "b1", "text": "Wait, what"})
    assert again.get("again") and bridge["aborts"] == ["barge_asr"]
    ev = [e for e in bridge["logs"] if e.get("event") == "barge_cut"][0]
    assert ev["since_onset_s"] is not None and ev["answering"] is True


def test_out_of_scope_lines_are_not_cut(bridge, monkeypatch, tmp_path):
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))      # Setup walks: Setup's lines
    assert eb.barge_scope() == (False, "setup")
    assert eb.barge_from_ear({"phase": "cut", "id": "b2", "text": "Wait"})["cut"] is False
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "done"}))
    monkeypatch.setattr(eb, "_speaking", False)                                     # the announcer, Lila: not hers
    assert eb.barge_scope() == (False, "not_speaking")
    monkeypatch.setenv("CRISSA_BARGE_FROM_ASR", "0")
    monkeypatch.setattr(eb, "_speaking", True)
    assert eb.barge_scope() == (False, "off")
    assert not bridge["aborts"]


def test_a_false_cut_goes_on_by_itself_after_the_pause(bridge):
    eb.barge_from_ear({"phase": "cut", "id": "b3", "text": "With class"})
    t = time.time()
    got = eb.barge_from_ear({"phase": "end", "id": "b3", "turn": False, "why": "self_echo"})
    assert got["next"] == "resume"
    eb._dispatch_busy = False                                # her stopped answer has finished (the fixture restores it)
    eb._barge_tick(t + BB.RESUME_S - 0.2)
    assert not bridge["sent"]
    eb._barge_tick(t + BB.RESUME_S + 0.05)
    assert len(bridge["sent"]) == 1
    text, meta = bridge["sent"][0]
    assert text == BB.RESUME_TEXT and meta["barge_resume"] is True and meta["resume_level"] == "bau"
    assert meta["person_id"] == "user" and meta["session_id"] == "s1"
    eb._barge_tick(t + 5)
    assert len(bridge["sent"]) == 1                                                  # once


def test_no_resume_when_someone_speaks_again_or_she_was_not_answering(bridge):
    eb.barge_from_ear({"phase": "cut", "id": "b4", "text": "Noise"})
    eb.barge_from_ear({"phase": "end", "id": "b4", "turn": False})
    BB.STATE.other_turn()
    eb._dispatch_busy = False
    eb._barge_tick(time.time() + 2)
    assert not bridge["sent"] and BB.STATE.cur["state"] == "dropped"
    eb._speaking, eb._dispatch_busy = True, False            # a wake's "Yeah?": no turn is being answered
    eb.barge_from_ear({"phase": "cut", "id": "b5", "text": "Noise"})
    eb.barge_from_ear({"phase": "end", "id": "b5", "turn": False})
    eb._barge_tick(time.time() + 2)
    assert not bridge["sent"] and BB.STATE.cur["state"] == "took"


def test_a_resume_waits_while_she_is_busy_and_gives_up(bridge, monkeypatch):
    eb.barge_from_ear({"phase": "cut", "id": "b6", "text": "Noise"})
    eb.barge_from_ear({"phase": "end", "id": "b6", "turn": False})
    t = time.time()
    monkeypatch.setattr(eb, "ear_still_hearing", lambda *a, **k: True)             # someone is talking
    eb._dispatch_busy = False
    eb._barge_tick(t + 1)
    assert not bridge["sent"] and BB.STATE.cur["state"] == "resume"
    eb._barge_tick(t + BB.RESUME_S + BB.RESUME_GIVE_UP_S + 1)
    assert BB.STATE.cur["state"] == "dropped" and not bridge["sent"]


def test_no_outcome_leaves_her_stopped(bridge):
    eb.barge_from_ear({"phase": "cut", "id": "b7", "text": "Wait"})
    eb._barge_tick(time.time() + BB.END_WAIT_S + 1)
    assert BB.STATE.cur["state"] == "lost" and not bridge["sent"]


def test_the_health_says_when_a_cut_is_possible(bridge):
    import http.client
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert eb.start_abort_listener(port)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    c.request("GET", "/health")
    h = json.loads(c.getresponse().read())
    assert h["barge"] is True
    c.request("POST", "/v1/barge", body=json.dumps({"phase": "cut", "id": "b8", "text": "Wait"}),
              headers={"Content-Type": "application/json"})
    assert json.loads(c.getresponse().read())["cut"] is True and bridge["aborts"] == ["barge_asr"]


# ── the bridge: the turn that cut her, through main() ────────────────────────────────────────────────────────────────

class _Stop(BaseException):
    pass


def _main(monkeypatch, tmp_path, turns, *, speaking=False, cut_id=None, owner_pid="o", gate_paths=()):
    monkeypatch.setattr(eb, "HOME", tmp_path)
    monkeypatch.setattr(eb.heard_roll, "HOME", tmp_path)
    monkeypatch.setattr(eb.cancels, "HOME", tmp_path)
    events, sent, aborts = [], [], []
    ticks = {"n": 0}

    def get(url, timeout=1.5):
        if url == eb.STT_TURNS:
            ticks["n"] += 1
            if ticks["n"] <= 2:
                return {"turns": []}
            if ticks["n"] <= 4:
                return {"turns": turns}
            raise _Stop()
        return {}

    monkeypatch.setattr(BB, "STATE", BB.Barge())
    for k, v in {"_get": get, "_log": events.append, "_reset_speech_logs": lambda: None,
                 "start_abort_listener": lambda *a: None, "voice_on_cached": lambda: True, "voice_on": lambda: True,
                 "_tool_hold_active": lambda: None, "request_session_look": lambda **kw: {},
                 "own_voice_gate_paths": lambda: list(gate_paths), "is_mouth_echo": lambda t: False,
                 "house_mouth_on_air": lambda *a, **k: False, "user_id_from_mode": lambda: owner_pid,
                 "user_voice_prints": lambda u: 3, "resume_pending": lambda now=None: False,
                 "is_oracle_feed": lambda t: False, "is_spoken_via": lambda t: True,
                 "clear_session_board": lambda: None, "listening_off": lambda why: None,
                 "schedule_dispatch": lambda u, m: sent.append((u, m)),
                 "abort_mouth": lambda reason="": (aborts.append(reason), setattr(eb, "_speaking", False)),
                 "pa_yield_for_hear": lambda *a, **k: None, "pending_phase": lambda: "",
                 "render_skips_crissa": lambda: False, "ear_still_hearing": lambda *a, **k: False,
                 "PA_PLAY": tmp_path / "announce-play.json", "QUESTION_OPENS": False, "POLL": 0.01,
                 "SEED_WAIT_S": 5.0, "_speaking": speaking, "_speak_proc": None, "_dispatch_busy": False,
                 "_session_extend_until": time.time() + 60}.items():
        monkeypatch.setattr(eb, k, v)
    monkeypatch.setattr(eb.own_voice, "mode", lambda: "off")
    if cut_id:
        BB.STATE.note_turn({"person_id": owner_pid, "stamp": {}}, "bau")
        BB.STATE.cut(cut_id, partial="w", answering=True)
    with pytest.raises(_Stop):
        eb.main()
    return events, sent, aborts


def _turn(text, pid="o", **kw):
    return {"ts": time.time() + 1.0, "text": text, "person_id": pid, "via": "ear",
            "stamp": {"person_id": pid, "voice_emb_sim": 0.9, "source": "voice_emb"}, **kw}


def test_the_turn_that_cut_her_is_sent_at_once_as_his_turn(monkeypatch, tmp_path):
    """Rule 4: through the normal turn path, no gather wait; rule 5: the normal path attributes it (its pid)."""
    events, sent, _ = _main(monkeypatch, tmp_path, [_turn("What was that word you just said?", barge_id="b9")],
                            cut_id="b9")
    assert [u for u, _m in sent] == ["What was that word you just said?"]
    assert sent[0][1]["person_id"] == "o" and not sent[0][1].get("barge_resume")
    assert any(e.get("event") == "session_utt" and e.get("barge") for e in events)
    assert BB.STATE.cur["state"] == "took"


def test_a_stop_over_her_line_stops_her_and_asks_nothing(monkeypatch, tmp_path):
    events, sent, _ = _main(monkeypatch, tmp_path, [_turn("Stop.", barge_id="b10")], cut_id="b10")
    assert sent == [] and any(e.get("event") == "barge_stop" for e in events)
    assert BB.STATE.cur["state"] == "took"


def test_another_voice_is_worked_out_after_and_she_goes_on(monkeypatch, tmp_path):
    """Someone else's words cut her (no voice check before the cut); the normal path does not take them as her
    conversation, so she goes on from where she stopped."""
    events, sent, _ = _main(monkeypatch, tmp_path, [_turn("Did you feed the cat?", pid="guest2", barge_id="b11")],
                            cut_id="b11")
    assert not [u for u, _m in sent if u != BB.RESUME_TEXT]
    assert any(e.get("event") == "barge_aside" for e in events) and BB.STATE.cur["state"] == "resume"


def test_a_whole_turn_over_her_line_cuts_her_without_the_over_line_ask(monkeypatch, tmp_path):
    """The looks missed it: the whole decode cuts her (abort), and the turn is sent - never to the over-line model."""
    over = []
    monkeypatch.setattr(eb, "_over_line_run", lambda u, m: over.append(u))
    events, sent, aborts = _main(monkeypatch, tmp_path, [_turn("Tell me about the second one instead.")],
                                 speaking=True)
    assert aborts == ["barge_turn"] and [u for u, _m in sent] == ["Tell me about the second one instead."]
    assert not over


def test_the_switch_off_is_the_old_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_BARGE_FROM_ASR", "0")
    events, sent, aborts = _main(monkeypatch, tmp_path, [_turn("Tell me about the second one instead.")],
                                 speaking=True)
    assert aborts == [] and not any(e.get("event") == "barge_cut" for e in events)


# ── the bridge: the resume is "go on" at the cut turn's level, with no tools and no voice ─────────────────────────────

def test_a_resume_reuses_the_level_and_grants_nothing(monkeypatch, tmp_path):
    calls = {"reply": None, "posts": [], "requests": [], "memory": []}

    def reply(text, **kw):
        calls["reply"] = (text, kw)
        return ""

    def no_auth(*a, **k):
        raise AssertionError("a resume is never authorized afresh")

    for k, v in {"_get": lambda u, timeout=1.5: {"headcount": 1, "people": []}, "authorize": no_auth,
                 "evaluate_turn": lambda *a, **k: {"user_voice": True, "tools_ok": True},
                 "_post_json": lambda *a, **k: calls["posts"].append(a), "_memory_append":
                 lambda *a, **k: calls["memory"].append(a), "crissa_reply": reply, "_log": lambda ev: None,
                 "_gateway": lambda p: {"profile": "crissa", "url": "http://127.0.0.1:9/v1", "key": "", "model": "m",
                                        "voice": "crissa", "session_header": ""}}.items():
        monkeypatch.setattr(eb, k, v)
    monkeypatch.setattr(eb.cancels, "note_request", lambda *a, **k: calls["requests"].append(a))
    monkeypatch.setattr(BB, "STATE", BB.Barge())
    eb.dispatch(BB.RESUME_TEXT, {"person_id": "o", "stamp": {"voice_emb_sim": 0.9}, "barge_resume": True,
                                 "resume_level": "bau"})
    text, kw = calls["reply"]
    assert text == BB.RESUME_TEXT and kw["level"] == "bau" and kw["user_voice"] is False     # tool lock: no voice
    assert BB.RESUME_NOTE in kw["system_note"]
    assert calls["posts"] == [] and calls["requests"] == [] and calls["memory"] == []
    assert BB.STATE._turn is None                                     # a resume is not a new turn to go on with


def test_the_cut_keeps_where_she_was_for_go_on(monkeypatch, tmp_path):
    """Rule 6: the real abort_mouth writes the cancel record (what she had said, the sentence she was in), as a name
    stop always did, so "go on" (and her own resume) starts there."""
    monkeypatch.setattr(eb.cancels, "path", lambda: tmp_path / "cancel.json")
    monkeypatch.setattr(eb.cancels, "log_path", lambda: tmp_path / "cancels.jsonl")
    monkeypatch.setattr(eb, "HOME", tmp_path)
    monkeypatch.setattr(eb, "TTS_GATE", tmp_path / "tts-gate.json")
    monkeypatch.setattr(eb, "COSY_ABORT", "http://127.0.0.1:9/v1/stop")
    monkeypatch.setattr(eb, "_log", lambda ev: None)
    monkeypatch.setattr(eb.heard_roll, "note", lambda *a, **k: None)
    monkeypatch.setattr(BB, "STATE", BB.Barge())
    line = ("The weather tomorrow looks mild in the morning. Clouds build after lunch. A chance of light rain comes "
            "by the evening, so take a light jacket if you are heading out.")
    t0 = time.time() - 5.0                                   # 5 s in at 15 characters a second: inside sentence three
    eb.cancels.note_request("What's the weather tomorrow?", now=t0 - 1)
    eb._SAY.begin(line, now=t0)
    monkeypatch.setattr(eb, "_speaking", True)
    monkeypatch.setattr(eb, "_speak_proc", None)
    monkeypatch.setattr(eb, "_dispatch_busy", True)
    assert eb.barge_from_ear({"phase": "cut", "id": "b20", "text": "Wait"})["cut"] is True
    rec = json.loads((tmp_path / "cancel.json").read_text())
    assert rec["why"] == "barge_asr" and rec["last_sentence"] == "Clouds build after lunch."
    assert rec["next_sentence"].startswith("A chance of light rain")
    assert eb._speaking is False and json.loads((tmp_path / "tts-gate.json").read_text())["notice"] == "interrupted"



def test_a_whole_turn_of_her_own_bent_words_does_not_cut_her(monkeypatch, tmp_path):
    """The turn-level cut judges words as a look does: her own line come back bent ("with class" for "with clouds")
    leaves her talking and goes the old way."""
    line = "Sure. The weather tomorrow looks mild in the morning, with clouds building after lunch."
    gate = tmp_path / "house-tts-gate.json"
    gate.write_text(json.dumps({"speaking": True, "last_reply": line, "who": "crissa", "air_at": time.time() - 3.0,
                                "air_until": None, "updated_ts": time.time() - 3.0}))
    now = time.time()
    t = _turn("Looks mild in the morning, with class.", speech_start_ts=now - 1.5, speech_end_ts=now - 0.2)
    events, sent, aborts = _main(monkeypatch, tmp_path, [t], speaking=True, gate_paths=[gate])
    assert aborts == [] and not any(e.get("event") == "barge_cut" for e in events)


def test_the_whole_decode_of_the_utterance_that_cut_her_only_takes_her_words_out():
    """Measured: "Can you say that again?" over her reading came back from the whole decode dropped as "words like her
    line" (no voice print to keep it), and the cut became a false one. With barge=True only her runs go."""
    L = self_echo.Lines()
    t0 = time.time() - 6
    line = "Whenever I find myself growing grim about the mouth; whenever it is a damp, drizzly November in my soul."
    L.note(t0, None, line)
    got = self_echo.subtract("Growing grim about the mouth. Can you say that again?", t0 + 1, t0 + 4, now=t0 + 5,
                             lines=L, barge=True)
    assert got["action"] == "rewrite" and got["text"].lower().startswith("can you say that again")
    echo = self_echo.subtract("Growing grim about the mouth.", t0 + 1, t0 + 4, now=t0 + 5, lines=L, barge=True)
    assert echo["action"] == "drop"                                                  # nothing left: a false cut
    assert self_echo.subtract("Can you say that again?", t0 + 1, t0 + 4, now=t0 + 5, lines=L,
                              barge=True)["action"] == "keep"


def test_no_looks_while_the_microphone_is_locked_off(monkeypatch):
    st = _Probe([""] * 20)
    monkeypatch.setattr(ear_loop.EarLoop, "_voice_locked_off", lambda self: True)
    _run_loop(monkeypatch, speech=[False] * 2 + [True] * 6, probe=st.p)
    assert st.windows == []


def test_a_look_still_deciding_holds_the_on_air_cap(monkeypatch):
    """Measured: her echo's stretch hit the 4 s cap while the look that heard "hang on" was still decoding, and the
    person's words were split across two utterances. A look in flight holds the cap (CAP_HOLD_S), so the cut lands and
    the utterance stays whole; with no hold the cap splits it."""

    def run(hold):
        monkeypatch.setattr(EB, "CAP_HOLD_S", hold)
        monkeypatch.setenv("CRISSA_BARGE_SKIP_ECHO", "0")          # count every decode
        gate, state = threading.Event(), {"block": False, "fed": 0, "release_at": None}
        told = []

        def look(pcm, sr):
            if state["block"]:
                state["block"] = False
                gate.wait(5)
                return {"text": "Hang on", "ms": 1.0}
            return {"text": "", "ms": 1.0}

        def judge(text, t0, t1):
            s = 2 if text else 0
            return {"strength": s, "cut": s >= 2, "why": "stub", "own": text.split(), "words": text.split()}

        p = EB.Probe(look=look, judge=judge, tell=lambda b: (told.append(b), {"cut": True})[1])
        speech = [False] * 2 + [True] * 22
        monkeypatch.setenv("RAIL_STT_HANGOVER_SEC", "0.6")
        monkeypatch.setenv("RAIL_STT_EAR_OPEN_FRAMES", "2")
        monkeypatch.setattr(ear_loop, "her_air_now", lambda now=None: True)
        monkeypatch.setattr(ear_loop, "detect_speech_spans", lambda wav, sr, **kw: [1] if float(wav[0]) > 0 else [])
        from crissa_ear import voice_live
        monkeypatch.setattr(voice_live, "kick", lambda pcm, sr: None)
        el = ear_loop.EarLoop()
        monkeypatch.setattr(el, "_hear_session_open", lambda: True)
        monkeypatch.setattr(el, "_barge_armed", lambda: True)
        monkeypatch.setattr(el, "_barge_probe", lambda cfg: (setattr(el, "_probe", p) or p))
        done = threading.Event()

        def cap(*, seconds, cfg, listen_lock):
            i = state["fed"]
            state["fed"] += 1
            if i == 13:                         # ~4 s into the stretch: this frame's look is still out at the cap
                p.idle()                        # (the frame before's look first, so the block falls on this one)
                state["block"] = True
            elif i in (14, 15):
                time.sleep(0.05)
            elif i == 16:
                gate.set()
                p.idle()
            else:
                p.idle()
            if i >= len(speech) + 10:
                done.set()
                time.sleep(0.05)
            on = speech[i] if i < len(speech) else False
            return np.full(4800, 0.1 if on else 0.0, np.float32), None

        monkeypatch.setattr(el, "_capture_frame", cap)
        metas = []
        monkeypatch.setattr(ear_loop.EarLoop, "_voice_locked_off", lambda self: False)

        def pc(wav, sr, voice_fut=None):
            metas.append(dict(ear_loop.decode_meta(), dur=round(len(wav) / sr, 1)))
            return {"text": "words" if ear_loop.decode_meta().get("barge_id") else ""}

        from types import SimpleNamespace
        el.open(cfg=SimpleNamespace(sample_rate=16000, mic="auto", live_ear_url=""), process_chunk=pc,
                listen_lock=threading.Lock(), chunk_sec=12, idle_timeout_sec=600, max_open_sec=3600, frame_sec=0.3,
                hangover_sec=0.6, max_utter_sec=20)
        assert done.wait(10)
        _wait(lambda: not el.status().get("decoding"))
        el.close("test")
        return metas

    held = run(1.2)
    assert [m for m in held if m.get("barge_id")] and len([m for m in held if m.get("barge_episode")]) == 1, held
    split = run(0.0)
    assert len([m for m in split if m.get("barge_episode")]) >= 2, split


def test_no_stretch_of_hers_in_the_window_falls_back_on_her_newest_line():
    """Measured: an ear running 50 s late (or her pace slower than the open row's cap) found no stretch of hers for the
    window, so her own last sentence ("The stock has made it.") counted as someone's words and cut her."""
    line = ("Let it simmer for twenty minutes before you add the cream, and season it at the very end, once you can taste "
            "how salty the stock has made it.")
    L = self_echo.Lines()
    t0 = time.time() - 60.0                                  # far past len/11 + 4 s: the open row has run out
    L.note(t0, None, line)
    assert L.during(t0 + 55, t0 + 56, now=t0 + 56) == ""
    v = EB.words_over_her("The stock has made it.", t0 + 55, t0 + 56, lines=L, now=t0 + 56)
    assert v["cut"] is False and v["strength"] == 0
    assert EB.words_over_her("Wait, what was that?", t0 + 55, t0 + 56, lines=L, now=t0 + 56)["cut"] is True


READING = json.loads((Path(__file__).parent / "fixtures" / "round16-101-20261013.json").read_text())["reading"]


def test_a_line_cut_short_is_compared_at_her_pace(monkeypatch):
    """A reading cut 10 s in: its length over 10 s is no pace of hers (70 characters a second), and the stretch compared
    with what was heard then was a part of her line she never reached (round 16's own-pace rule). Capped at MAX_CPS
    the stretch is where she was."""
    L = self_echo.Lines()
    t0 = time.time() - 30
    L.note(t0, t0 + 10.0, READING)
    assert "drizzly November" in L.during(t0 + 7, t0 + 9, now=t0 + 20)
    monkeypatch.setattr(self_echo, "MAX_CPS", 0.0)                                   # round 16
    assert "drizzly November" not in L.during(t0 + 7, t0 + 9, now=t0 + 20)
    monkeypatch.setattr(self_echo, "MAX_CPS", 25.0)
    monkeypatch.setenv("CRISSA_BARGE_FROM_ASR", "0")                                 # the way back
    assert "drizzly November" not in L.during(t0 + 7, t0 + 9, now=t0 + 20)


def test_a_bent_anchor_still_places_the_word_after_it():
    """Measured: "Dreisley, no." for "drizzly No-vember": the bent anchor matched drizzly, so "no" is her next word cut
    short, not a stop."""
    her = "whenever it is a damp, drizzly November in my soul; whenever I find myself involuntarily pausing"
    assert EB.judge_words("Dreisley, no.", her)["strength"] == 0
    assert EB.judge_words("No, I meant the other one.", her)["strength"] == 2


def test_words_that_are_not_hers_keep_the_whole_decode_even_when_they_do_not_cut(monkeypatch):
    """Measured: "Hang on" was lost under her RAW echo, the looks heard only "Is that right?" (no cut: function and
    listener's words), and the stretch was skipped as her echo, so the person's words were never decoded at all."""
    her = "Stir in two cloves of garlic, a spoonful of flour, and a pinch of thyme."
    v = EB.judge_words("Is that right?", her)
    assert v["strength"] == 0 and v["alien"] >= 2
    assert EB.judge_words("A spoonful of flour.", her)["alien"] == 0
    st = _Probe(["Is that right?"] + [""] * 40)
    st.p._judge = lambda text, t0, t1: EB.judge_words(text, her)
    metas, _el = _run_loop(monkeypatch, speech=[False] * 2 + [True] * 6, probe=st.p)
    assert len(metas) == 1 and not st.told


def test_the_utterance_that_cut_her_is_decoded_from_where_someone_was_first_heard(monkeypatch):
    """Measured over her RAW echo: 3.7 s of her echo, then "Wait, go back a bit" decoded as her words alone. The decode
    starts TRIM_LEAD_S before the first window in which someone was heard."""
    seen = []

    class P:
        def settle(self, eid, timeout=0): pass
        def fired(self, eid): return True
        def heard_from(self, eid): return 1000.0 + 3.7
        def tell_end(self, eid, turn, why=""): seen.append(("end", turn))

    el = ear_loop.EarLoop()
    el._probe = P()
    sr = 16000
    wav = np.arange(int(5.7 * sr), dtype=np.float32)
    meta = {"barge_episode": "b1", "speech_start_ts": 1000.0, "speech_end_ts": 1005.7}

    def pc(w, s, voice_fut=None):
        seen.append(("decode", len(w), dict(ear_loop.decode_meta())))
        return {"text": "Wait, go back a bit."}

    el._decode_q.put((wav, sr, 5.7, pc, meta, None))
    el._decode_q.put(None)
    el._stop.set()       # a stop marker ends the loop only with the stop flag, as close() sends it (round 31 heal)
    el._decode_loop()
    kind, n, m = seen[0]
    assert kind == "decode" and n == len(wav) - int((3.7 - EB.TRIM_LEAD_S) * sr)
    assert m["barge_id"] == "b1" and abs(m["speech_start_ts"] - (1000.0 + 3.4)) < 0.01
    monkeypatch.setenv("CRISSA_BARGE_TRIM", "0")
    seen.clear()
    el._decode_q.put((wav, sr, 5.7, pc, dict(meta), None))
    el._decode_q.put(None)
    el._stop.set()       # a stop marker ends the loop only with the stop flag, as close() sends it (round 31 heal)
    el._decode_loop()
    assert seen[0][1] == len(wav)


def test_a_word_waiting_for_its_confirming_look_holds_the_on_air_cap_longer():
    p = EB.Probe(look=lambda pcm, sr: {"text": ""}, judge=lambda *a: {"strength": 0}, tell=lambda b: None)
    eid = p.begin()
    assert not p.holds_cap(eid, 4.2, 4.0)                                  # her echo alone: the 4 s cap
    p._prev = {eid: (1, 3.0, frozenset({"sorry"}), 1)}                      # the last look: one word of someone's
    assert p.holds_cap(eid, 4.2, 4.0) and p.holds_cap(eid, 7.9, 4.0) and not p.holds_cap(eid, 8.1, 4.0)
    p._prev = {eid: (0, 3.3, frozenset(), 3)}                               # back to her words: the cap again
    assert not p.holds_cap(eid, 4.2, 4.0)


def test_a_stop_whose_words_the_decode_lost_keeps_her_stopped(bridge):
    """Measured over her RAW echo: the look heard "Stop.", the whole decode heard only her ("The weekend looks
    drier."). She was asked to stop: she does not go on over the person."""
    eb.barge_from_ear({"phase": "cut", "id": "b30", "text": "Stop."})
    got = eb.barge_from_ear({"phase": "end", "id": "b30", "turn": False, "cut_why": "stop"})
    assert got["next"] == "stopped" and BB.STATE.cur["state"] == "took"
    eb._dispatch_busy = False
    eb._barge_tick(time.time() + 5)
    assert not bridge["sent"]
