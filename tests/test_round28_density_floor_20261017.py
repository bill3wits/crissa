"""Round 28 (.101 2026-10-17, ASK 1): Setup's probe found nothing that fit while its installs had the CPU, the pick was
null and the Wall stayed at 1.0 on a 3440x1440 glass. A null pick on a wide glass takes the floor for its width, then
round 27's one step finer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_prefs as WP  # noqa: E402
import crissa_machine as M  # noqa: E402

STAGE_101 = {"W": 3440, "H": 1440, "bar_ms": 41.7, "pick": None,
             "candidates": [{"density": d} for d in (5.0, 4.0, 3.0, 2.0, 1.5, 1.0)]}


def test_the_floor_by_width(monkeypatch):
    monkeypatch.delenv("CRISSA_DENSITY_FLOOR", raising=False)
    th = M.thresholds()
    assert M.density_floor(STAGE_101, th) == 2.0
    assert M.density_floor({"W": 2560}, th) == 1.5
    assert M.density_floor({"W": 1440}, th) is None, "a small glass keeps the default"
    monkeypatch.setenv("CRISSA_DENSITY_FLOOR", "0")
    assert M.density_floor(STAGE_101, th) is None


def test_101s_null_pick_starts_finer_than_round_26(tmp_path, monkeypatch):
    for k in ("CRISSA_DENSITY_FLOOR", "CRISSA_DENSITY_ONE_FINER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(WP, "PATH", tmp_path / "house-wall-ascii.prefs.json")
    monkeypatch.setattr(M.crissa_home, "is_house", lambda: True)
    out = M.apply_density(dict(STAGE_101), tmp_path, post=lambda q: None)
    assert out["applied"] and "floor" in out["why"]
    assert WP.load()["density"] == M.finer(2.0) > 2.044, "never the coarsest; above round 26's 2.044"
    assert json.loads((tmp_path / M.DENSITY_PICK).read_text())["density"] == M.finer(2.0)


def test_the_config_carries_the_floor():
    cfg = json.loads((ROOT / "config" / "crissa-machine-tiers.json").read_text())
    assert cfg["density_floor_by_width"] == M.DEFAULTS["density_floor_by_width"]
