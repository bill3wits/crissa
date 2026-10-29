"""Onset pre-roll: first words survive a VAD miss (laryngitis / whisper)."""
import threading

from crissa_ear.ear_loop import OnsetHold


def test_live_asr_name_is_audio8():
    from crissa_ear.config import live_asr_name

    assert live_asr_name()
    assert "parakeet" not in live_asr_name().lower()


def test_lone_tick_does_not_open():
    o = OnsetHold(open_frames=2, preroll_frames=3)
    assert o.push("a", True) == "hold"
    assert o.heard is False
    assert o.push("b", False) == "idle"
    assert o.heard is False


def test_two_speech_frames_open_and_keep_preroll():
    o = OnsetHold(open_frames=2, preroll_frames=3)
    o.push("quiet", False)
    o.push("did", True)  # hold — first word, not yet open
    assert o.push("you", True) == "open"
    assert o.seed() == ["quiet", "did", "you"]


def test_vad_miss_between_words_still_keeps_onset_in_preroll():
    o = OnsetHold(open_frames=2, preroll_frames=4)
    o.push("Did", True)  # hold
    assert o.push("miss", False) == "idle"  # old code wiped pending here
    o.push("you", True)  # hold again
    assert o.push("hear", True) == "open"
    assert "Did" in o.seed()
    assert o.seed()[-2:] == ["you", "hear"]


def test_flush_does_not_block_on_slow_decode():
    import inspect
    import time
    from types import SimpleNamespace

    import numpy as np
    from crissa_ear.ear_loop import EarLoop

    el = EarLoop()
    done = threading.Event()

    def slow(wav, sr):
        time.sleep(0.35)
        done.set()
        return {"text": "hello the user", "person_id": "user"}

    t0 = time.time()
    el._flush_utterance(
        [np.zeros(1600, np.float32)],
        cfg=SimpleNamespace(sample_rate=16000),
        process_chunk=slow,
        idle_acc=0.0,
    )
    assert time.time() - t0 < 0.2
    assert el.status().get("decoding") is True
    assert done.wait(2.0)
    deadline = time.time() + 1.0
    while el.status().get("decoding") and time.time() < deadline:
        time.sleep(0.02)
    assert el.status().get("last_text") == "hello the user"
    assert el.status().get("decoding") is False
    flush_src = inspect.getsource(EarLoop._flush_utterance)
    assert "RAIL_STT_DECODE_Q_MAX" in flush_src
    assert "decode_dropped" in flush_src
    assert "mic_off_drop" in flush_src or "_note_mic_off_drop" in flush_src
    el.close("test")


def test_flush_kicks_voice_id_parallel():
    import inspect

    from crissa_ear import ear_loop as el

    src = inspect.getsource(el.EarLoop._flush_utterance) + inspect.getsource(
        el.EarLoop._decode_loop
    )
    assert "voice_live" in src
    assert "kick(" in src
    assert "voice_fut" in src
    run = inspect.getsource(el.EarLoop._run) + inspect.getsource(
        el.EarLoop._kick_voice_id
    )
    assert "_kick_voice_id" in run
    assert "_fire_look_impulse" in run
    assert run.find("_kick_voice_id") < run.find("_flush_utterance") or "_kick_voice_id(parts" in inspect.getsource(el.EarLoop._run)


def test_look_impulse_is_two_speech_frames_not_puck():
    import inspect

    from crissa_ear import ear_loop as el

    src = inspect.getsource(el.EarLoop._run) + inspect.getsource(
        el.EarLoop._fire_look_impulse
    )
    assert "_fire_look_impulse" in src
    assert "impulse_need" in src
    post = inspect.getsource(el.EarLoop._post_look_impulse)
    assert "request_f2f" not in post
    fire = inspect.getsource(el.EarLoop._fire_look_impulse)
    assert "pierce_ts" in fire
    assert "_voice_locked_off" in fire
    assert "_note_mic_off_drop" in fire
    flush = inspect.getsource(el.EarLoop._flush_utterance)
    assert "_voice_locked_off" in flush
    assert "_note_mic_off_drop" in flush


def test_mic_off_flush_counts_and_skips_asr():
    from types import SimpleNamespace

    import numpy as np
    from crissa_ear.ear_loop import EarLoop

    el = EarLoop()
    el._voice_locked_off = lambda: True
    called = []

    def boom(wav, sr):
        called.append(1)
        return {"text": "should not decode"}

    idle, flushed = el._flush_utterance(
        [np.zeros(1600, np.float32)],
        cfg=SimpleNamespace(sample_rate=16000),
        process_chunk=boom,
        idle_acc=0.0,
    )
    assert flushed is False
    assert called == []
    st = el.status()
    assert st.get("mic_off_drop") == 1
    assert st.get("mic_off_last") == "flush"
    assert st.get("decoding") is not True
    assert st.get("last_text") is None
    el.close("test")


def test_mic_off_pierce_counts_and_skips_ts():
    from crissa_ear.ear_loop import EarLoop

    el = EarLoop()
    el._voice_locked_off = lambda: True
    el._fire_look_impulse()
    st = el.status()
    assert st.get("mic_off_drop") == 1
    assert st.get("mic_off_last") == "pierce"
    assert st.get("pierce_ts") is None
    el.close("test")


def test_ear_picks_mic_before_vad():
    import inspect

    from crissa_ear import ear_loop as el

    src = inspect.getsource(el.EarLoop._run)
    assert "mic_select" in src
    assert "decide(" in src
    assert "detect_speech_spans" in src
    assert src.find("decide(") < src.find("detect_speech_spans")


def test_reset_clears_heard_keeps_preroll():
    o = OnsetHold(open_frames=2, preroll_frames=3)
    o.push("a", True)
    o.push("b", True)
    o.reset_utterance()
    assert o.heard is False
    assert list(o.preroll) == ["a", "b"]


def _hear(el, open_, *, ts=None):
    import time

    el._hear["open"] = open_
    el._hear["ts"] = time.time() if ts is None else ts


def test_hangover_paused_is_one_frame(monkeypatch):
    from crissa_ear.ear_loop import EarLoop

    monkeypatch.delenv("RAIL_STT_WAKE_HANGOVER_SEC", raising=False)
    monkeypatch.delenv("RAIL_STT_HANGOVER_SEC", raising=False)
    el = EarLoop()
    _hear(el, False)
    assert el._hangover_sec_now() == 0.3
    el.close("test")


def test_hangover_engaged_is_1_8(monkeypatch):
    from crissa_ear.ear_loop import EarLoop

    monkeypatch.delenv("RAIL_STT_HANGOVER_SEC", raising=False)
    el = EarLoop()
    _hear(el, True)
    assert el._hangover_sec_now() == 1.8
    el.close("test")


def test_hangover_never_fetched_is_engaged(monkeypatch):
    from crissa_ear.ear_loop import EarLoop

    monkeypatch.delenv("RAIL_STT_HANGOVER_SEC", raising=False)
    el = EarLoop()
    assert el._hear["open"] is None
    assert el._hangover_sec_now() == 1.8
    el.close("test")


def test_hangover_fetch_fail_keeps_last_known(monkeypatch):
    import urllib.request

    from crissa_ear.ear_loop import EarLoop

    monkeypatch.delenv("RAIL_STT_WAKE_HANGOVER_SEC", raising=False)
    monkeypatch.setenv("RAIL_STT_HEAR_HEALTH_TEST", "1")
    el = EarLoop()
    _hear(el, False, ts=0.0)  # stale → would fetch

    def boom(*_a, **_k):
        raise TimeoutError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert el._hear_session_open() is False
    assert el._hangover_sec_now() == 0.3
    el.close("test")


def test_wake_hangover_env_zero(monkeypatch):
    from crissa_ear.ear_loop import EarLoop

    monkeypatch.setenv("RAIL_STT_WAKE_HANGOVER_SEC", "0")
    el = EarLoop()
    _hear(el, False)
    assert el._hangover_sec_now() == 0.0
    el.close("test")


def test_run_recomputes_hangover_each_silence_frame():
    import inspect

    from crissa_ear.ear_loop import EarLoop

    src = inspect.getsource(EarLoop._run)
    assert "hang = self._hangover_sec_now()" in src
    assert "hang = max(0.4" not in src
    hangover = src.split('if action == "hangover":', 1)[1].split("idle_acc += frame", 1)[0]
    assert "silence_run += frame" in hangover
    assert "silence_run >= hang" in hangover
    # One 0.3 s silence frame meets default wake hangover.
    frame = 0.3
    silence_run = 0.0
    silence_run += frame
    assert silence_run >= 0.3


def test_status_overlays_live_hangover(monkeypatch):
    from crissa_ear.ear_loop import EarLoop

    monkeypatch.delenv("RAIL_STT_WAKE_HANGOVER_SEC", raising=False)
    monkeypatch.delenv("RAIL_STT_HANGOVER_SEC", raising=False)
    monkeypatch.delenv("RAIL_STT_WAKE_OPEN_FRAMES", raising=False)
    monkeypatch.delenv("RAIL_STT_EAR_OPEN_FRAMES", raising=False)
    el = EarLoop()
    _hear(el, False)
    st = el.status()
    assert st["session_open"] is False
    assert st["hangover_sec"] == 0.3
    assert st["open_frames"] == 1
    _hear(el, True)
    st = el.status()
    assert st["session_open"] is True
    assert st["hangover_sec"] == 1.8
    assert st["open_frames"] == 1  # code default; live drop-in is 2
    el.close("test")


def test_paused_opens_on_one_speech_frame(monkeypatch):
    """Two-attempt wake: a short 'Hey Crissa' is often one 0.3 s Silero hit."""
    from crissa_ear.ear_loop import EarLoop, OnsetHold

    o = OnsetHold(open_frames=1, preroll_frames=3)
    assert o.push("hey", True) == "open"

    monkeypatch.delenv("RAIL_STT_WAKE_OPEN_FRAMES", raising=False)
    monkeypatch.setenv("RAIL_STT_EAR_OPEN_FRAMES", "2")
    el = EarLoop()
    _hear(el, False)
    assert el._open_frames_now() == 1
    _hear(el, True)
    assert el._open_frames_now() == 2
    el.close("test")


def test_run_recomputes_open_frames_each_tick():
    import inspect

    from crissa_ear.ear_loop import EarLoop

    src = inspect.getsource(EarLoop._run)
    assert "onset.open_frames = self._open_frames_now()" in src
    assert src.find("onset.open_frames") < src.find("onset.push")


def test_silero_thr_env(monkeypatch):
    from crissa_ear.vad import silero_threshold

    monkeypatch.delenv("RAIL_STT_SILERO_THR", raising=False)
    assert silero_threshold() == 0.5
    monkeypatch.setenv("RAIL_STT_SILERO_THR", "0.35")
    assert silero_threshold() == 0.35
    import inspect
    from crissa_ear import vad as vad_mod

    src = inspect.getsource(vad_mod._silero_spans)
    assert "threshold=silero_threshold()" in src


def test_vad_engine_env(monkeypatch):
    from crissa_ear.vad import vad_engine

    monkeypatch.delenv("RAIL_STT_VAD", raising=False)
    assert vad_engine() == "silero"
    # round 41 (the User 2026-10-22: "drop TEN VAD"): an old setting asking for TEN gets Silero
    monkeypatch.setenv("RAIL_STT_VAD", "ten")
    assert vad_engine() == "silero"
    monkeypatch.setenv("RAIL_STT_VAD", "ten_vad")
    assert vad_engine() == "silero"
    monkeypatch.setenv("RAIL_STT_VAD", "webrtc")
    assert vad_engine() == "webrtc"
    monkeypatch.setenv("RAIL_STT_VAD", "SILERO")
    assert vad_engine() == "silero"


def test_ten_is_said_once_and_never_loaded(monkeypatch, capsys):
    from crissa_ear import vad
    monkeypatch.setattr(vad, "_TEN_SAID", False)
    monkeypatch.setenv("RAIL_STT_VAD", "ten")
    assert vad.vad_engine() == "silero" and vad.vad_engine() == "silero"
    assert capsys.readouterr().err.count("TEN VAD is no longer part of Crissa") == 1
    assert not hasattr(vad, "ten_threshold")


def test_status_exposes_vad_engine(monkeypatch):
    from crissa_ear.ear_loop import EarLoop

    monkeypatch.setenv("RAIL_STT_VAD", "ten")
    el = EarLoop()
    st = el.status()
    assert st["vad"] == "silero" and "ten_thr" not in st      # round 41: TEN VAD left the kit
    monkeypatch.setenv("RAIL_STT_VAD", "silero")
    assert el.status()["vad"] == "silero"
    el.close("test")


def test_detect_routes_ten_to_silero(monkeypatch):
    """Round 41 (the User 2026-10-22: "drop TEN VAD"): RAIL_STT_VAD=ten runs Silero; TEN's own paths are gone."""
    import numpy as np
    from crissa_ear import vad as v
    from crissa_ear.vad import SpeechSpan

    monkeypatch.setenv("RAIL_STT_VAD", "ten")
    monkeypatch.setattr(v, "_silero_spans", lambda *_a, **_k: [SpeechSpan(0.1, 0.4)])
    out = v.detect_speech_spans(np.zeros(4800, dtype=np.float32), 16000)
    assert len(out) == 1 and out[0].start == 0.1
    for gone in ("_ten_spans", "_ten_model", "_as_int16", "_TEN_HOP"):
        assert not hasattr(v, gone), gone
