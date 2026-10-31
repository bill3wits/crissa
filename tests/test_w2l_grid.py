"""The render grid: the model may run slower than the player.

These never load the ONNX. A fake session stands in for it so the grid, the
hold and the drift guard are tested as arithmetic — the model's OUTPUT is
eye-checked in bench/lipsync-keepup, which is not a job for CI.
"""
from __future__ import annotations

import base64
import time

import numpy as np
import pytest

w2l = pytest.importorskip("w2l")

SR_IN = 24000
PLAYER_FPS = 24.0


class FakeSess:
    """Answers the shape Wav2Lip answers, with an obviously-drawn jaw."""

    provider, ok, err, load_s = "FakeProvider", True, "", 0.0

    def __init__(self) -> None:
        self.n = 0

    def infer(self, vid, mel):
        self.n += 1
        out = np.zeros((vid.shape[0], 3, w2l.FACE, w2l.FACE), np.float32)
        out[:, :, w2l.FACE // 2:, :] = 1.0
        return out


def make_live(fps: float, restorer=None) -> "w2l.Live":
    """The REAL constructor with a stand-in session — so adding a field to
    Live can never silently skip it here."""
    return w2l.Live(device="cpu", fps=fps, restorer=restorer, sess=FakeSess())


def speech(seconds: float = 3.0) -> bytes:
    n = int(SR_IN * seconds)
    return (np.sin(np.arange(n) * 0.05) * 8000).astype("<i2").tobytes()


def run(live, t0, frames: int, mouth=(330, 210, 70, 34)):
    frame = np.full((400, 736, 3), 120, np.uint8)
    whys = []
    for i in range(frames):
        _, why = live.paint(frame, mouth, t0 + i / PLAYER_FPS)
        whys.append(why)
    return whys


def test_grid_runs_at_eight_while_player_runs_at_24():
    live = make_live(8.0)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(3.0)).decode()]], SR_IN)
    whys = run(live, t0, 72)                      # 3 s of player frames
    assert not any(whys), whys
    # 8 fps over 3 s, and the player's 72 frames all got a jaw.
    assert live.gens == 24
    assert live.gens + live.holds == 72
    assert live.sess.n == live.gens               # a hold asks no model


def test_zero_grid_still_generates_every_frame():
    """The 2026-08-30 behaviour is an escape hatch, not a deleted branch."""
    live = make_live(0.0)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(2.0)).decode()]], SR_IN)
    run(live, t0, 48)
    assert live.holds == 0
    assert live.gens == 48


def test_hold_refuses_a_head_that_moved():
    """A jaw pasted where the face no longer is belongs to the strip."""
    live = make_live(8.0)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(2.0)).decode()]], SR_IN)
    frame = np.full((400, 736, 3), 120, np.uint8)
    _, why = live.paint(frame, (330, 210, 70, 34), t0)
    assert why == "" and live.gens == 1
    # inside the grid period, a small move still holds
    out, why = live.paint(frame, (334, 213, 70, 34), t0 + 0.02)
    assert why == "" and live.holds == 1
    assert (out != frame).any()
    # a big one hands the frame back untouched, with the reason
    out, why = live.paint(frame, (330 + 90, 210, 70, 34), t0 + 0.03)
    assert "head moved" in why
    assert not (out != frame).any()


def test_hold_does_not_outlive_the_sound():
    """Past the last sample she has stopped talking: the strip closes the jaw."""
    live = make_live(8.0)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(1.0)).decode()]], SR_IN)
    frame = np.full((400, 736, 3), 120, np.uint8)
    live.paint(frame, (330, 210, 70, 34), t0 + 0.5)
    _, why = live.paint(frame, (330, 210, 70, 34), t0 + 1.5)
    assert "does not cover now" in why


def test_new_turn_never_opens_on_the_last_turn_s_jaw():
    live = make_live(8.0)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(1.0)).decode()]], SR_IN)
    live.paint(np.full((400, 736, 3), 120, np.uint8), (330, 210, 70, 34), t0 + 0.1)
    assert live._gen is not None
    live.feed([], SR_IN)                          # she stopped
    assert live._gen is None and live._gen_box is None


class FakeRestorer:
    """Paints the crop flat white so "did the restorer run" is unmissable."""

    def __init__(self) -> None:
        self.n = 0
        self.ms = 1.0

    def restore(self, bgr):
        self.n += 1
        return np.full_like(bgr, 255)


def test_restorer_runs_on_generates_only_and_holds_inherit_it():
    """The whole economy of the stage: 8 restores a second, not 24, and a held
    frame must not be blurrier than the generate it came from."""
    fr = FakeRestorer()
    live = make_live(8.0, restorer=fr)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(3.0)).decode()]], SR_IN)
    whys = run(live, t0, 72)
    assert not any(whys), whys
    assert live.gens == 24 and live.holds == 48
    # one restore per generate, none per hold
    assert fr.n == live.gens
    # the cached jaw is the RESTORED one, so holds are not softer
    assert live._gen_full is True


def test_no_restorer_is_unchanged():
    live = make_live(8.0)
    t0 = time.time()
    live.feed([[t0, base64.b64encode(speech(2.0)).decode()]], SR_IN)
    run(live, t0, 48)
    assert live.gens == 16 and live._gen_full is False
