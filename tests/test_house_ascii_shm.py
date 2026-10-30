"""Wallpaper shm hole crop. No display required."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_ascii_shm as shm  # noqa: E402


def test_write_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(shm, "path", lambda: tmp_path / "bg")
    src = Image.new("RGB", (64, 32), (10, 20, 30))
    src.putpixel((40, 10), (200, 100, 50))
    shm.write_bg(src)
    got = shm.read_bg()
    assert got.size == (64, 32)
    assert got.getpixel((40, 10)) == (200, 100, 50)


def test_crop_screen_is_the_window_slice(tmp_path, monkeypatch):
    monkeypatch.setattr(shm, "path", lambda: tmp_path / "bg")
    src = Image.new("RGB", (100, 80), (1, 2, 3))
    src.paste((9, 8, 7), (20, 10, 50, 40))
    shm.write_bg(src)
    sl = shm.crop_screen(20, 10, 30, 30)
    assert sl.size == (30, 30)
    assert sl.getpixel((0, 0)) == (9, 8, 7)
