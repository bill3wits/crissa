"""Laptop-cam → webcam analog switch, and the fade back to the room."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from camera_switch import CUT_S, FADE_S, fade_frame, switch_frame  # noqa: E402


def _desk() -> np.ndarray:
    img = np.zeros((40, 80, 3), dtype=np.uint8)
    img[:, :40] = (40, 80, 160)
    img[:, 40:] = (20, 140, 40)
    return img


def _webcam() -> np.ndarray:
    img = np.zeros((40, 80, 3), dtype=np.uint8)
    img[:] = (180, 90, 40)
    img[10:30, 25:55] = (40, 40, 200)
    return img


def test_switch_keeps_shape_and_is_quiet_at_the_edges():
    src = _desk()
    a = switch_frame(src, 0.0, CUT_S)
    b = switch_frame(src, CUT_S / 2, CUT_S)
    c = switch_frame(src, CUT_S, CUT_S)
    assert a.shape == src.shape == b.shape == c.shape
    assert a.dtype == np.uint8
    # Mid-switch is the squiggle (channel split + snow); the start still
    # reads as the desk. The last ~14% flashes to black for the cut.
    assert abs(int(a.mean()) - int(src.mean())) < 8
    assert int(b.mean()) != int(src.mean())
    assert int(c.mean()) < 8


def test_fade_is_src_then_dst():
    src, dst = _desk(), _webcam()
    a = fade_frame(src, dst, 0.0, FADE_S)
    b = fade_frame(src, dst, FADE_S / 2, FADE_S)
    c = fade_frame(src, dst, FADE_S, FADE_S)
    assert np.array_equal(a, src)
    assert np.array_equal(c, dst)
    # Midpoint is between the two, not equal to either.
    assert not np.array_equal(b, src) and not np.array_equal(b, dst)
    mid = (src.astype(np.float32) + dst.astype(np.float32)) / 2.0
    assert abs(int(b.mean()) - int(mid.mean())) < 8


def test_house_conductor_wires_the_half_second_cut():
    hc = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert "from camera_switch import fade_frame, switch_frame" in hc
    assert "USER_TALK_CUT_S" in hc and "USER_TALK_FADE_S" in hc
    assert 'kind="cut"' in hc and 'kind="fade"' in hc
    assert "_arm_cam" in hc and "_poll_ft_release" in hc
    assert 'if head == "/v1/ft-desk"' in hc
    wall = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "USER_TALK_CUT_S" not in wall  # the cut is the conductor, not a wall popup
    assert 'pref.get("ft_bar")' in wall
    src = (ROOT / "lib" / "conductor.py").read_text()
    assert "USER_TALK_FADE_S" in src
    cam = (ROOT / "lib" / "camera_switch.py").read_text()
    assert "TVChannelChange" in cam or "channel-switch" in cam
    assert "snow" in cam
