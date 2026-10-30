"""Round 14, density 5 on .101 (the User: "look amazing"; 1667x428 cells, 19.5-25 pictures/s, 12 % of mouth rows skipped):
the exposure curve by table (house_wall_ascii_render._exposure). Every luma cell is one of 256 values, so the stretch,
the floor and the 0.80 curve are worked out for those 256 and looked up - the same numpy operations on the same float32
inputs. Pinned: the grid is the per-cell chain's, cell for cell, kiosk and whole, with and without her mouth box, at
three densities; the kill puts the per-cell chain back. docs/checkpoints/round14-prefill-20261012 (the d5 numbers).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii_render as R  # noqa: E402
from test_wall_ascii_cache import _jpeg, _mouth, _scene  # noqa: E402


def _per_cell(lv, lo, hi):
    FLOOR = 0.42
    v = (lv - lo) / (hi - lo)
    v = np.where(v <= FLOOR, 0.0, (v - FLOOR) / (1 - FLOOR))
    return np.clip(v, 0, 1) ** 0.80


@pytest.mark.parametrize("seed", range(6))
def test_the_table_is_the_per_cell_chain_for_any_stretch(seed):
    rng = np.random.default_rng(seed)
    sm = rng.integers(0, 256, (428, 1667), dtype=np.uint8)
    lv = sm.astype(np.float32) / 255.0
    for lo, hi in ((np.float64(0.0), np.float64(1.0)), (np.percentile(lv, 2), np.percentile(lv, 98)),
                   (np.float64(0.31), np.float64(0.35)), (np.float64(0.5), np.float64(0.54))):
        assert np.array_equal(R._exposure(sm, lv, lo, hi), _per_cell(lv, lo, hi))


@pytest.mark.parametrize("density", (0.859, 2.08, 5.0))
def test_the_glyph_grid_is_unchanged_kiosk_and_whole_with_and_without_her_mouth(density, monkeypatch):
    g = R.Geom(3440, 1440, density=density)
    box = (330.0, 220.0, 60.0, 22.0)
    for k in range(3):
        jp = _jpeg(_mouth(_scene(3000 + k), (330, 220, 60, 22), k))
        for frame in (None, ("whole", 1.0, 0.5171, 0.5487), ("whole", 2.0, 0.5171, 0.5487)):
            for mouth in (None, box):
                monkeypatch.setattr(R, "CURVE_TABLE", True)
                a = R.ascii_idx(R._open(jp), g, True, 0.0, None, None, mouth=mouth, frame=frame)
                monkeypatch.setattr(R, "CURVE_TABLE", False)
                b = R.ascii_idx(R._open(jp), g, True, 0.0, None, None, mouth=mouth, frame=frame)
                assert np.array_equal(a, b), (density, frame, mouth)


def test_the_kill_is_read_at_import():
    src = (ROOT / "lib" / "house_wall_ascii_render.py").read_text()
    assert 'CURVE_TABLE = os.environ.get("HOUSE_WALL_CURVE_TABLE", "1")' in src
    assert src.count("v = _exposure(small_u8, lv, lo, hi)") == 2
