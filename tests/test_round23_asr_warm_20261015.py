"""Round 23, the ASR warm (.101 10-15: `warm={'ark-asr': False}` on all 11 starts - the ear asked once, before the ASR
service had loaded, so her first heard line after a start was cold) and the ear keeper's wording ("Connection refused"
once per start read like an error). No ASR here: the engine is stood in."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_ear import pipeline as P  # noqa: E402


class _Ear:
    def __init__(self, answers):
        self.answers, self.lines = list(answers), []

    def health(self):
        return self.answers.pop(0) if self.answers else True

    def transcribe(self, audio, sr=16000):
        self.lines.append((len(audio), sr, int(np.abs(audio).max())))


def _pipe(ear):
    p = P.Transcriber.__new__(P.Transcriber)
    p.live_ear, p.whisper = ear, None
    return p


def test_an_asr_up_at_start_is_warmed_at_once(monkeypatch):
    ear = _Ear([True])
    out = _pipe(ear).warm()
    assert list(out.values())[0] is True and ear.lines == [(16000, 16000, 0)]


def test_an_asr_still_loading_is_asked_again_then_warmed(monkeypatch):
    started = []
    monkeypatch.setattr(P.threading, "Thread", lambda target, name, daemon: type(
        "T", (), {"start": lambda self: started.append(target)})())
    ear = _Ear([False, False, False, True])
    p = _pipe(ear)
    out = p.warm()
    assert out.get("later") is True and ear.lines == [] and len(started) == 1
    assert p._warm_later(sleep=lambda s: None) is True and ear.lines == [(16000, 16000, 0)]


def test_the_wait_has_a_kill(monkeypatch):
    monkeypatch.setenv("CRISSA_ASR_WARM_WAIT_S", "0")
    out = _pipe(_Ear([False])).warm()
    assert "later" not in out


def test_the_keeper_says_it_is_waiting_once():
    src = (ROOT / "modules" / "ear" / "scripts" / "crissa-stt.py").read_text()
    assert "waiting for the ear's own port to open" in src and "if not waited:" in src
