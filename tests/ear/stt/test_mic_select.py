"""Shure near-field beats room Seeed only when it is actually close-talk."""
from __future__ import annotations

import numpy as np

from crissa_ear.mic_select import NEAR, ROOM, decide, rms_db


def _tone(db: float, n: int = 4800) -> np.ndarray:
    amp = 10.0 ** (db / 20.0)
    return np.full(n, amp, dtype=np.float32)


def test_quiet_shure_does_not_steal_room():
    # Measured idle: Shure −49, Seeed −50. Must stay on the puck.
    out = decide(near_db=-48.8, room_db=-49.4)
    assert out["mic"] == ROOM
    assert out["near_hot"] is False


def test_wake_pop_is_not_close_talk():
    # First Shure grab after SUSPENDED was −36. Still below −28.
    out = decide(near_db=-36.4, room_db=-49.1)
    assert out["mic"] == ROOM


def test_close_talk_wins():
    out = decide(near_db=-18.0, room_db=-32.0)
    assert out["mic"] == NEAR
    assert out["why"] == "near_field"
    assert out["near_hot"] is True


def test_room_speech_louder_than_shure_bleed_stays_room():
    out = decide(near_db=-34.0, room_db=-28.0)
    assert out["mic"] == ROOM


def test_hold_does_not_splice():
    opened = decide(near_db=-16.0, room_db=-40.0)
    assert opened["mic"] == NEAR
    held = decide(near_db=-55.0, room_db=-30.0, held=NEAR)
    assert held["mic"] == NEAR
    assert held["why"] == "held"


def test_rms_empty_is_floor():
    assert rms_db(None) == -120.0
    assert rms_db(np.zeros(0, np.float32)) == -120.0
    assert rms_db(_tone(-20.0)) < -19.0
    assert rms_db(_tone(-20.0)) > -21.0
