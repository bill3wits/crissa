"""Round 35, 2026-10-19 (.101 round 33: Setup set 0 ms twice - ear 195.2, glass wall-tk 106.7 sampled behind Setup's
window - his eye +375): once she has walked in, Setup's value is worked out again with the glass she is seen on now."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_lipsync as LS  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("CRISSA_LIPSYNC_FULL_WALL", "CRISSA_SETUP_LIPSYNC_MEASURE", "CRISSA_LIPSYNC", "CRISSA_SETUP_LIPSYNC_GLASS",
              "CRISSA_SETUP_LIPSYNC_GLASS_COUNT", "CRISSA_SETUP_LIPSYNC_GLASS_REF_MS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setenv("CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS", "0")   # round 35's formula (round 36 adds the Wall's path)
    return tmp_path


def _setup_as_on_101(home):
    res = {"ok": True, "heard_ms": 215.2, "ear_ms": 195.2, "lines": 3, "of": 3}
    return LS.from_setup(res, home=home, glass={"who": "wall-tk", "median_ms": 106.7})


def test_the_full_wall_glass_replaces_the_behind_the_window_sample(home):
    first = _setup_as_on_101(home)
    assert first["outcome"] == "set" and abs(first["lead_ms"]) < 21, ".101's 0 ms"
    rep = LS.reglass({"who": "wall-tk", "median_ms": 384.0}, home)
    assert rep["outcome"] == "set" and rep["reglass"] == {"was": 106.7, "now": 384.0, "who": "wall-tk"}
    assert 250 <= rep["lead_ms"] <= 300, rep["lead_ms"]          # 90 - 195.2 + 384 = 278.8, to the half frame
    assert abs(LS.offset_ms(home) - rep["lead_ms"]) < 0.01


def test_his_own_value_is_kept(home):
    _setup_as_on_101(home)
    LS.write_ms(375.0, home, meta={"by": "user"})
    rep = LS.reglass({"who": "wall-tk", "median_ms": 384.0}, home)
    assert rep["outcome"] == "kept" and abs(LS.offset_ms(home) - 375.0) < 0.01


def test_nothing_to_redo_and_the_kill(home, monkeypatch):
    assert LS.reglass({"median_ms": 300.0}, home)["outcome"] == "none"
    _setup_as_on_101(home)
    assert LS.reglass(None, home)["outcome"] == "none"
    monkeypatch.setenv("CRISSA_LIPSYNC_FULL_WALL", "0")
    assert LS.reglass({"median_ms": 384.0}, home)["outcome"] == "off"


def test_init_asks_after_the_walk_in_before_her_opener():
    src = (ROOT / "lib" / "crissa_init.py").read_text()
    i = src.index("        drawn = _entrance_then_up()\n    if entered:\n        _lip_sync_full_wall(run_id)")
    # round 40 N22: the opener carries its file rendered ahead
    assert i < src.index("ran, _ = speak_turn(lambda: _crissa_says(line, wav), what=\"Crissa's opener\")")
