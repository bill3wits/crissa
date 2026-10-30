"""Round 27: the first look one step finer (the User 2026-10-16: "let's load on higher res ascii (just one)")."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_prefs as WP  # noqa: E402
import crissa_machine as M  # noqa: E402


def test_the_101_pick_goes_one_click_finer(monkeypatch):
    monkeypatch.delenv("CRISSA_DENSITY_ONE_FINER", raising=False)
    assert M.finer(2.044) == round(2.044 + WP.nudge_step(2.044), 3) == 2.146
    assert M.finer(WP.DENSITY_MAX) == WP.DENSITY_MAX
    monkeypatch.setenv("CRISSA_DENSITY_ONE_FINER", "0")
    assert M.finer(2.044) == 2.044


def test_the_glass_and_the_build_take_the_finer_one(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_DENSITY_ONE_FINER", raising=False)
    monkeypatch.setattr(WP, "PATH", tmp_path / "house-wall-ascii.prefs.json")
    monkeypatch.setattr(M.crissa_home, "is_house", lambda: True)          # no record server to ask
    out = M.apply_density({"pick": 2.044}, tmp_path, post=lambda q: None)
    assert out["applied"] and WP.load()["density"] == 2.146
    assert json.loads((tmp_path / M.DENSITY_PICK).read_text())["density"] == 2.146
