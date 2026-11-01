"""The stamp's fast path paints the same bytes (the User 2026-10-12, "go ahead with the stamp speedup").

lib/stamp_fast.py drops composite_stamp's spare float64 copies and lets a film frame's second semiframe reuse the
first one's inpaint and arch band. Every case here compares against today's code, byte for byte.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pose_mouth as P  # noqa: E402
import stamp_fast as S  # noqa: E402
from test_composite_roi import _base_and_warped, _dest  # noqa: E402


@pytest.fixture
def fast(monkeypatch):
    def set_on(on: bool):
        monkeypatch.setattr(S, "ON", on)
        S._inpaints.clear()
        S._arches.clear()
    yield set_on
    S._inpaints.clear()
    S._arches.clear()


def _today_tail(warped_u8, dst_u8, ww, aa, tone):
    patch = warped_u8.astype(np.float32)
    dstp = dst_u8.astype(np.float32)
    patch = P.brighten_and_tone(patch, dstp, ww, tone=tone)
    return (dstp * (1 - aa) + patch * aa).astype(np.uint8)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("tone", [True, False])
def test_the_tail_matches_brighten_tone_and_blend(seed, tone):
    rng = np.random.default_rng(seed)
    h, w = int(rng.integers(20, 160)), int(rng.integers(20, 160))
    warped = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    dst = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    a = np.clip(rng.normal(0.6, 0.5, (h, w)), 0, 1).astype(np.float32)
    a[rng.random((h, w)) < 0.3] = 0
    if seed == 5:
        a[:] = 0
        a[0, :4] = 1.0                       # tot <= 8: no brighten, no tone
    big = rng.integers(0, 256, (h + 9, w + 7, 3), dtype=np.uint8)
    big[4:4 + h, 3:3 + w] = dst
    dst_view = big[4:4 + h, 3:3 + w]         # a strided ROI view, as composite_stamp passes it
    a3 = a[..., None]
    got = S.brighten_tone_blend(warped, dst_view, a3[..., 0], a3, tone)
    assert got is not None
    assert np.array_equal(got, _today_tail(warped, dst_view, a3[..., 0], a3, tone))


def test_a_float64_alpha_is_left_to_today():
    z = np.zeros((4, 4, 3), np.uint8)
    assert S.brighten_tone_blend(z, z, np.ones((4, 4)), np.ones((4, 4, 1)), True) is None


def test_inpaint_is_telea_and_a_repeat_is_remembered(fast):
    fast(True)
    rng = np.random.default_rng(1)
    patch = rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)
    mask = np.zeros((60, 80), np.uint8)
    cv2.ellipse(mask, (40, 30), (20, 10), 0, 0, 360, 255, -1)
    want = cv2.inpaint(patch, mask, 3, cv2.INPAINT_TELEA)
    a = S.inpaint(patch, mask, 3)
    b = S.inpaint(patch.copy(), mask.copy(), 3)
    assert np.array_equal(a, want) and np.array_equal(b, want)
    assert S.stats["inpaint_hit"] >= 1
    b[:] = 0                                  # the caller owns its copy
    assert np.array_equal(S.inpaint(patch, mask, 3), want)
    mask[30, 40] = 0                          # a different mask is a different answer
    assert np.array_equal(S.inpaint(patch, mask, 3), cv2.inpaint(patch, mask, 3, cv2.INPAINT_TELEA))


@pytest.mark.parametrize("hole", ["arch", "ellipse"])
def test_composite_stamp_is_byte_identical_on_and_twice(fast, hole):
    dest = _dest()
    base, warped, box = _base_and_warped(dest)
    a = P.square_alpha(warped, dest)
    fast(False)
    want = P.composite_stamp(base.copy(), box, warped, dest, a=a, hole=hole)
    fast(True)
    first = P.composite_stamp(base.copy(), box, warped, dest, a=a, hole=hole)
    second = P.composite_stamp(base.copy(), box, warped, dest, a=a, hole=hole)    # the frame's other semiframe
    assert np.array_equal(first, want) and np.array_equal(second, want)
    if hole == "arch":
        assert S.stats["arch_hit"] >= 1


def test_off_on_the_house_unless_asked(monkeypatch):
    monkeypatch.setenv("HOUSE_MOUTH_STAMP_FAST", "0")
    assert S._on() is False
    monkeypatch.setenv("HOUSE_MOUTH_STAMP_FAST", "1")
    assert S._on() is True
    monkeypatch.delenv("HOUSE_MOUTH_STAMP_FAST")
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert S._on() is False
