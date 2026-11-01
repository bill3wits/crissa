"""Round 31 (2026-10-18, .101 twice after a fresh Setup: decoding true, decode_queued 2, chunks 0, every stt thread
idle, every later utterance dropped as decode_queue_full until a restart). Each close() queued a stop marker even when
no decode thread was alive to take it; Setup closes the ear many times, so markers piled up, every new decode thread
took one and exited, and once two utterances waited the drop path returned before anything restarted a decoder."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_ear import ear_loop as EL  # noqa: E402


def _ear(monkeypatch):
    ear = EL.EarLoop()
    monkeypatch.setattr(ear, "_voice_locked_off", lambda: False)
    monkeypatch.setattr(ear, "_barge_dropped", lambda *a, **k: None)
    return ear


def _reopen(ear):
    """What open() does to the decoder, without a microphone: clear the stop, reset the counts, ensure the decoder."""
    with ear._lock:
        ear._stop.clear()
        ear._state.update(open=True, chunks=0, decoding=False, decode_queued=0)
    ear._ensure_decoder()


def _say(ear, done, n=4):
    cfg = SimpleNamespace(sample_rate=16000)
    pc = lambda wav, sr, voice_fut=None: (done.append(1), {"text": "hey"})[1]   # noqa: E731
    for _ in range(n):
        ear._flush_utterance([np.zeros(8000, dtype=np.float32)], cfg=cfg, process_chunk=pc, idle_acc=0.0)
        time.sleep(0.15)
    t0 = time.time()
    while time.time() - t0 < 3.0 and len(done) < 1:
        time.sleep(0.05)


def test_closes_with_no_decoder_do_not_strand_the_ear(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    ear = _ear(monkeypatch)
    for _ in range(5):                 # Setup / Voice ID closing an ear that has no live decode thread
        ear.close("mic_hold")
    _reopen(ear)
    done = []
    _say(ear, done)
    st = ear.status()
    assert done, "no utterance decoded after the closes (the stall .101 saw)"
    assert st["decode_queued"] < 2 or ear._decode_thread.is_alive()
    ear.close("end")


def test_a_full_queue_with_no_decoder_restarts_one(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    ear = _ear(monkeypatch)
    with ear._lock:
        ear._state.update(open=True, decoding=True, decode_queued=2)     # stranded: counted, nobody decoding
    ear._decode_q.put((np.zeros(8000, dtype=np.float32), 16000, 0.5,
                       lambda wav, sr, voice_fut=None: {"text": "a"}, {}, None))
    done = []
    _say(ear, done, n=2)
    assert done, "the drop path must restart a decoder that is gone"
    ear.close("end")


def test_the_kill_keeps_the_old_drop(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    monkeypatch.setenv("RAIL_STT_DECODE_HEAL", "0")
    ear = _ear(monkeypatch)
    with ear._lock:
        ear._state.update(open=True, decoding=True, decode_queued=2)
    done = []
    _say(ear, done, n=2)
    assert not done and ear._state.get("decode_dropped") == 2
