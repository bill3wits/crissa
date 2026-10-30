"""Crissa's gain follows the line (the User 2026-09-30): OFF by default, Crissa only, starts at the old gain, walks
toward the line's own speech median no faster than the rate. Synthetic PCM only."""
from __future__ import annotations

import array
import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SR = 24000


@pytest.fixture
def hs():
    spec = importlib.util.spec_from_file_location("house_speak_follow", ROOT / "scripts" / "house-speak.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def tone(seconds: float, db: float) -> bytes:
    amp = 32767 * 10 ** (db / 20) * math.sqrt(2)
    return array.array("h", (int(amp * math.sin(2 * math.pi * 200 * i / SR)) for i in range(int(SR * seconds)))).tobytes()


def test_it_is_off_by_default_and_crissa_only(hs, monkeypatch):
    assert hs.FOLLOW_LINE is False
    assert hs.follow_rider(tone(1, -15), SR, 0.3) is None                 # off: the old fixed gain, byte for byte
    monkeypatch.setattr(hs, "FOLLOW_LINE", True)
    monkeypatch.setattr(hs, "_AIR_WHO", "lila")
    assert hs.follow_rider(tone(1, -15), SR, 0.3) is None                 # another voice: untouched
    monkeypatch.setattr(hs, "_AIR_WHO", "crissa@bench")
    assert isinstance(hs.follow_rider(tone(1, -15), SR, 0.3), hs.GainRider)


def test_a_hot_opening_no_longer_sets_the_whole_line(hs):
    """Her opening 4 dB over the rest: the fixed gain cuts the rest 4 dB too far; the rider walks back to target."""
    prefill, rest = tone(1.0, -14.0), tone(16.0, -18.0)
    g0 = 10 ** ((-26.0 - -14.0) / 20)                                     # what the prefill measure picks: -12 dB
    r = hs.GainRider(g0, SR, target_db=-26.0, allow_cut=True, prefill=prefill)
    gains = [20 * math.log10(r.feed(rest[i:i + 4800])) for i in range(0, len(rest), 4800)]
    assert gains[0] > -12.0 - 0.1 and gains[0] < -11.8                    # starts where the old gain was
    steps = [b - a for a, b in zip(gains, gains[1:])]
    assert max(abs(x) for x in steps) <= 1.5 * 0.1 + 1e-9                 # never faster than 1.5 dB a second
    assert abs(gains[-1] - (-26.0 - -17.0)) < 1.5                         # settled near target - median of all heard


def test_the_floor_and_cap_still_hold(hs):
    loud = hs.GainRider(1.0, SR, target_db=-26.0, allow_cut=False, prefill=tone(1, -10))
    assert loud.want_db() == 0.0                                          # no cut when cutting is not allowed
    quiet = hs.GainRider(1.0, SR, target_db=-26.0, allow_cut=True, prefill=tone(1, -33))   # +7 wanted
    assert abs(quiet.want_db() - 20 * math.log10(hs._PLAY_MAX_GAIN)) < 1e-9   # the +6 dB cap


def test_silence_does_not_move_the_gain(hs):
    r = hs.GainRider(0.25, SR, target_db=-26.0, allow_cut=True)
    assert r.feed(bytes(9600)) == pytest.approx(0.25)


def test_the_stream_uses_the_rider_only_when_there_is_one():
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    assert "rider = follow_rider(bytes(prefill), sr, play_gain)" in src
    assert "if rider is not None:" in src and "play_gain = rider.feed(chunk)" in src
    assert "out, _ = boost_play_pcm(chunk, sr, gain=play_gain)" in src      # off: the same line as before
