"""Her working day beside the flourish knob (the User 2026-10-04): 9 to 5, leaving off; hours wrap round the clock."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_prefs as P  # noqa: E402


def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PATH", tmp_path / "prefs.json")


def test_defaults_are_nine_to_five_and_staying(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    assert P.work_day() == {"start": 9, "end": 17, "leaves": False}


def test_hours_step_and_wrap(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    for _ in range(16):
        P.nudge("work_end", P.HOUR_STEP)
    assert P.load()["work_end"] == 9                      # 17 + 16 wraps to 9
    P.nudge("work_start", -P.HOUR_STEP)
    assert P.load()["work_start"] == 8


def test_leaving_toggles(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    assert P.toggle_leaves()["leaves_at_end"] is True
    assert P.toggle_leaves()["leaves_at_end"] is False


def test_a_bad_file_falls_back(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    (tmp_path / "prefs.json").write_text('{"work_start": "noon", "work_end": 30, "leaves_at_end": 1}')
    assert P.work_day() == {"start": 9, "end": 6, "leaves": True}


def test_the_older_keys_are_untouched(monkeypatch, tmp_path):
    _tmp(monkeypatch, tmp_path)
    p = P.load()
    assert p["flourish_pct"] == 35 and p["density"] == P.DEFAULTS["density"]


def test_the_working_day_is_classified_wall_only_in_the_glass_contract():
    """X-hi's review (2026-10-04): the five Advanced controls are the wall's own (docs/glass-contract.yaml), never a
    Brave action, and their prefs are in the wall_only partition."""
    import yaml
    c = yaml.safe_load((ROOT / "docs" / "glass-contract.yaml").read_text())
    five = {"leaves_at_end", "work_start_lo", "work_start_hi", "work_end_lo", "work_end_hi"}
    assert five <= set(c["wallpaper_only"]["hits"])
    assert not five & set(c["shared_actions"]) and not five & set(c["brave_only"]["actions"])
    assert {"work_start", "work_end", "leaves_at_end"} <= set(c["prefs"]["wall_only"])
    assert not {"work_start", "work_end", "leaves_at_end"} & set(c["prefs"]["shared"])
