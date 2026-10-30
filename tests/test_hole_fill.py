"""The hole's smooth fill (HOUSE_MOUTH_HOLE_FILL=smooth, the User 2026-10-16: the seam down Crissa's chin on 9:16)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import pose_mouth as PM  # noqa: E402


def _two_tone(h=200, w=240):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, : w // 2] = (180, 190, 210)      # lit left cheek
    img[:, w // 2:] = (110, 120, 140)       # shadowed right cheek
    mask = np.zeros((h, w), np.uint8)
    mask[30:170, 40:200] = 255              # a big hole across both
    return img, mask


def test_the_default_is_telea():
    assert PM.HOLE_FILL in ("telea", "smooth")
    import os
    if "HOUSE_MOUTH_HOLE_FILL" not in os.environ:
        assert PM.HOLE_FILL == "telea"


def test_smooth_keeps_the_known_pixels():
    img, mask = _two_tone()
    out = PM.smooth_fill(img, mask)
    keep = mask == 0
    assert np.array_equal(out[keep], img[keep])


def test_smooth_has_no_step_down_the_middle():
    img, mask = _two_tone()
    row = PM.smooth_fill(img, mask)[100, 40:200].astype(int).sum(1)
    assert np.abs(np.diff(row)).max() <= 12, "the fill ramps across the hole, it does not meet in a line"
    assert row[0] > row[-1], "lit side stays lighter"


def test_the_painter_takes_the_smooth_fill_when_asked(monkeypatch):
    img, _ = _two_tone()
    monkeypatch.setattr(PM, "HOLE_FILL", "smooth")
    out = PM.hide_clip_jaw(img, (80, 60, 80, 60))
    band = out[60:150, 40:200].astype(int).sum(2)
    assert np.abs(np.diff(band, axis=1)).max() <= 30
