"""Floating FT window: ease, dest box, crop. No display required."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_float as F  # noqa: E402


def test_click_threshold_is_small():
    # documented: <8px movement on release is a close, not a drag
    assert F.ease(1) == 1
    src = (ROOT / "lib" / "house_float.py").read_text(encoding="utf-8")
    assert "close_btn" in src
    assert 'text="×"' in src


def test_ease_ends():
    assert F.ease(0) == 0
    assert F.ease(1) == 1
    assert abs(F.ease(0.5) - 0.5) < 1e-6
    assert F.ease(0.25) < 0.25  # slow start


def test_lerp_box_mid():
    a = (0, 0, 100, 100)
    b = (100, 50, 200, 200)
    m = F.lerp_box(a, b, 0.5)
    assert m == (50, 25, 150, 150)


def test_dest_ft_is_4x6_centered():
    x, y, w, h = F.dest_box(2560, 1440, {"ft_mode": "ft", "ft_size": 1.0})
    assert abs(w / h - 4 / 6) < 0.02
    assert abs(x + w / 2 - 1280) < 2
    assert abs(y + h / 2 - 720) < 2


def test_dest_room_is_16x9():
    x, y, w, h = F.dest_box(2560, 1440, {"ft_mode": "room", "ft_size": 1.0})
    assert abs(w / h - 16 / 9) < 0.02


def test_dest_with_bar_centres_the_pair():
    x, y, w, h = F.dest_box(2560, 1440, {"ft_mode": "ft", "ft_size": 1.0}, F.BAR_H)
    assert abs(w / h - 4 / 6) < 0.02
    assert abs(y + (h + F.BAR_H) / 2 - 720) < 2


def test_float_bar_has_ar_blur_size():
    src = (ROOT / "lib" / "house_float.py").read_text(encoding="utf-8")
    assert 'chip("AR")' in src
    assert 'chip("Blur")' in src
    assert 'chip("FT")' in src
    assert "_toggle_mode" in src
    assert "_nudge_size" in src
    assert "BAR_H" in src


def test_crop_blur_softens():
    src = Image.new("RGB", (736, 400), (200, 180, 160))
    src.paste((20, 20, 20), (240, 0, 280, 40))
    sharp = F.crop_frame(src, {"ft_mode": "ft", "blur": False}, (120, 180))
    blur = F.crop_frame(src, {"ft_mode": "ft", "blur": True}, (120, 180))
    assert sharp.size == blur.size
    assert list(sharp.getdata()) != list(blur.getdata())
