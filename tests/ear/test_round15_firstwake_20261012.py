"""Round 15 first wake (.101 fresh install, ASK-20261012-1425 addendum 14:28): "The first 'Hey Crissa' is lost, every
time she has just started." 14:24:34 "Hey, Chrisa." was heard with pid null (own_voice guest_unsure, why no_emb),
paused and skipped; 14:24:45 "Hey, Chrissa." -> pid user, "Yeah, <name>?". The same after Open Agent at 13:33:58
(media: true).

Why, on the ear's real path: a fresh ear's media gate has not heard a person yet (phase characterize; it goes back to
dead after 8 s of quiet), and a short wake has no voice print (under RAIL_VOICE_MIN_SEC, or the encoder still cold), so
the sole person's stamp is held as `media_hold_sole` - an identity question - and .101's kit (crissa-925694a1) read
that hold as media against the wake. ea64764e4 (on this branch's base) reads the wake by the media gate's own verdict;
these tests replay the exact shapes through the ear's stamping, and the voice print is now warm before the ear opens.
"""
from __future__ import annotations

import inspect
import threading
import time

import numpy as np
import pytest

from crissa_bridge import own_voice
from crissa_bridge.hear_gate import classify_hear, media_turn, wake_media
from crissa_ear import media_gate, server, voice_embed
from crissa_ear.pipeline import Transcript

UID = "user"


def _sole_stamp(dur):
    """What stamp_turn writes for the sole person on a fixed camera when the clip has no voice print (no_emb)."""
    return {"person_id": UID, "source": "sole_present", "certain": True, "id_status": "confirmed", "conf": 0.7,
            "voice_emb_sim": None, "is_user": None, "is_user_reason": "no_emb", "speech_duration_s": dur,
            "presence": {"headcount": 1, "people": [{"name": UID, "certain": True}]}}


class _Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


@pytest.fixture
def ear(monkeypatch):
    """The ear's attach_stamp with a fresh media gate, the presence of one person, and no network."""
    monkeypatch.setattr(media_gate, "_state", media_gate.MediaGateState())
    clock = _Clock(1_000_000.0)
    monkeypatch.setattr(media_gate, "time", clock)
    monkeypatch.setattr(server, "fetch_presence", lambda *a, **k: {"headcount": 1, "people": [{"name": UID}]})
    monkeypatch.setattr(server, "_maybe_post_sound", lambda st: None)
    monkeypatch.setattr(server, "stamp_turn", lambda text, presence, **k: _sole_stamp(k.get("speech_duration_s")))
    monkeypatch.delenv("EAR_WAKE_SOLE_HOLD_BLOCKS", raising=False)

    def hear(text, dur=1.1, at=None):
        if at is not None:
            clock.t = at
        r = server.attach_stamp(Transcript(text=text, confidence=0.9, engine="test", duration_s=dur, rms_db=-28.0))
        r.stamp.setdefault("speech_duration_s", dur)
        return {"text": text, "person_id": r.person_id, "stamp": r.stamp, "ts": clock.t, "duration_s": dur}

    hear.clock = clock
    return hear


def test_the_first_wake_of_a_fresh_ear_wakes_her(ear):
    t = ear("Hey, Chrisa.")                                   # 14:24:34, the ear just started
    st = t["stamp"]
    assert t["person_id"] is None and st["source"] == "media_hold_sole" and st["media_suppressed_id"] == UID
    assert st["media_gate"]["phase"] == "characterize" and st["media_gate"]["verdict"] == "unsure"
    assert media_turn(st) is True and wake_media(st) is False
    g = classify_hear(t, session_open=False, uid=UID)
    assert g["unpause"] is True and g["paused"] is False and g["crissa_hears"] is True
    assert g["to_her"] is False and g["log_user"] is False     # a guest at worst: the levels decide what she may do
    v = own_voice.verdict(t, own_voice.SayLog(), guest=True, now=t["ts"] + 0.5)
    assert v["rule"] == "guest_unsure" and v["why"] == "no_emb" and own_voice.drops(v["rule"]) is False


def test_the_rule_dot_101_ran_is_the_reported_skip(ear, monkeypatch):
    """crissa-925694a1 read the identity hold as media: the same stamp, paused and not heard (EAR_WAKE_SOLE_HOLD_BLOCKS=1
    is that rule kept as the kill switch)."""
    t = ear("Hey, Chrisa.")
    monkeypatch.setenv("EAR_WAKE_SOLE_HOLD_BLOCKS", "1")
    g = classify_hear(t, session_open=False, uid=UID)
    assert g["paused"] is True and g["crissa_hears"] is False and g["person_id"] is None


def test_the_second_wake_once_the_gate_has_heard_a_person(ear):
    """14:24:45, 11 s later: once the gate has called the room human, the sole person's stamp stands - pid user, as
    .101 logged ("Yeah, <name>?")."""
    ear("Hey, Chrisa.")
    media_gate._state.phase, media_gate._state.verdict = "clear_human", "human"
    t = ear("Hey, Chrissa.", at=ear.clock.t + 3.0)
    assert t["person_id"] == UID and t["stamp"]["source"] == "sole_present"
    g = classify_hear(t, session_open=False, uid=UID)
    assert g["unpause"] is True and g["user"] is True


def test_the_first_wake_after_quiet_wakes_her_too(ear):
    """The gate goes back to dead after DEAD_AIR_SEC of quiet, so the first wake after any pause is held the same way
    (not only after a start) - and wakes her."""
    ear("Hey, Chrisa.")
    media_gate._state.phase, media_gate._state.verdict = "clear_human", "human"
    t = ear("Hey, Chrissa.", at=ear.clock.t + media_gate.DEAD_AIR_SEC + 30.0)
    assert t["stamp"]["source"] == "media_hold_sole" and t["stamp"]["media_gate"]["phase"] == "characterize"
    assert classify_hear(t, session_open=False, uid=UID)["unpause"] is True


def test_open_agent_media_true_wakes_her(ear):
    """13:33:58, 20 s after Open Agent: the hold was logged `media: true`; only a real media verdict holds a wake."""
    t = ear("Hey, Chrisa.")
    assert classify_hear(t, session_open=False, uid=UID)["media"] is True
    assert classify_hear(t, session_open=False, uid=UID)["crissa_hears"] is True
    tv = dict(t, stamp=dict(t["stamp"], source="media_gate", media_gate={"verdict": "media", "media_on": True}))
    assert classify_hear(tv, session_open=False, uid=UID)["unpause"] is False


# ── the voice print is warm before the ear opens ────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_warm(monkeypatch):
    monkeypatch.setattr(voice_embed, "_warm", {"state": "cold", "ms": None, "error": None})
    monkeypatch.setattr(voice_embed, "_warm_done", threading.Event())
    monkeypatch.setattr(voice_embed, "WARM_ON", True)
    return voice_embed


def test_the_print_is_built_and_run_once_at_start(fresh_warm, monkeypatch):
    seen = []
    monkeypatch.setattr(fresh_warm, "available", lambda: True)
    monkeypatch.setattr(fresh_warm, "embed_wav16k", lambda w: (seen.append(len(w)), np.ones(4, np.float32))[1])
    assert fresh_warm.start_warm() is True
    got = fresh_warm.wait_warm(5.0)
    assert got["state"] == "warm" and seen == [fresh_warm.SR] and got["ms"] is not None
    assert fresh_warm.start_warm() is True and seen == [fresh_warm.SR], "once per process"


def test_no_encoder_is_said_not_waited_on(fresh_warm, monkeypatch):
    monkeypatch.setattr(fresh_warm, "available", lambda: False)
    fresh_warm.start_warm()
    assert fresh_warm.wait_warm(5.0)["state"] == "none"


def test_the_switch_off_builds_on_first_use_as_before(fresh_warm, monkeypatch):
    monkeypatch.setattr(fresh_warm, "WARM_ON", False)
    monkeypatch.setattr(fresh_warm, "available", lambda: pytest.fail("the switch is off: nothing is built at start"))
    assert fresh_warm.start_warm() is False
    got = fresh_warm.wait_warm(5.0)
    assert got["state"] == "off" and got["waited_ms"] < 100


def test_a_slow_build_is_waited_for_at_most_the_bound(fresh_warm, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(fresh_warm, "available", lambda: gate.wait(5.0))
    monkeypatch.setattr(fresh_warm, "embed_wav16k", lambda w: np.ones(4, np.float32))
    fresh_warm.start_warm()
    got = fresh_warm.wait_warm(0.2)
    assert got["state"] == "warming" and got["waited_ms"] < 2000
    gate.set()
    assert fresh_warm.wait_warm(5.0)["state"] == "warm"


def test_one_session_however_many_ask_at_once(monkeypatch):
    made = []
    cache: dict = {}

    def slow_session(model):                   # the real one caches per process but builds unlocked
        key = str(model)
        if key in cache:
            return cache[key]
        time.sleep(0.05)
        made.append(key)
        cache[key] = object()
        return cache[key]

    monkeypatch.setattr(voice_embed, "ORT_ENABLED", True)
    monkeypatch.setattr(voice_embed.W, "session", slow_session)
    threads = [threading.Thread(target=voice_embed._get_ort) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(made) == 1


def test_the_ear_starts_the_warm_up_first_and_opens_only_after_it():
    src = inspect.getsource(server.serve)
    i_start, i_engines = src.index("_ve.start_warm()"), src.index("tr.warm()")
    i_wait, i_http = src.index("_ve.wait_warm()"), src.index("_QuietServer((host, port)")
    assert i_start < i_engines < i_wait < i_http, "the print warms beside the engines, and the server binds after it"
    assert '"voice_warm": _voice_warm()' in inspect.getsource(server.make_handler)
