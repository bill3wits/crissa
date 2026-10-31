"""Round 36, 2026-10-20 (.101 round 35 FAIL 3): after her walk-in Setup read the Tk Wall's glass at 275.1 ms and set
+208.3; his eye on that Wall wanted +375. The desk Wall's paint and screen, past its reading, are counted: 8 half frames."""
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
              "CRISSA_SETUP_LIPSYNC_GLASS_COUNT", "CRISSA_SETUP_LIPSYNC_GLASS_REF_MS", "CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS",
              "HOUSE_MOUTH_LATENCY_MS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: False)
    return tmp_path


def _as_on_101(home):
    # Setup's window: +104.2 at glass 174.1 -> ear 90 + 174.1 - 104.2 = 159.9 (to the half frame); after the walk-in 275.1
    res = {"ok": True, "heard_ms": 180.0, "ear_ms": 156.8, "lines": 3, "of": 3}
    LS.from_setup(res, home=home, glass={"who": "wall-tk", "median_ms": 174.1})
    return LS.reglass({"who": "wall-tk", "median_ms": 275.1}, home)


def test_his_eye_on_the_desk_wall(home):
    rep = _as_on_101(home)
    assert rep["outcome"] == "set" and rep.get("wall_tk_extra_ms") == 166.7
    assert abs(rep["lead_ms"] - 375.0) < 0.01, rep["lead_ms"]
    assert "desk Wall's paint and screen" in rep["log"]


def test_another_reader_gets_nothing_added(home):
    res = {"ok": True, "heard_ms": 200.0, "ear_ms": 182.9, "lines": 3, "of": 3}
    rep = LS.from_setup(res, home=home, glass={"who": "page", "median_ms": 383.9})
    assert "wall_tk_extra_ms" not in rep and abs(rep["lead_ms"] - 291.667) < 0.01, rep["lead_ms"]


def test_the_kill_is_round_35(home, monkeypatch):
    monkeypatch.setenv("CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS", "0")
    rep = _as_on_101(home)
    assert abs(rep["lead_ms"] - 208.333) < 0.01, rep["lead_ms"]                                       # .101's +208.3
