"""Hotfix after round 46 (.101 round 46 "first lines while loading", the User: "Both lips were equally late during
initial speech while loading"): (1) with no value set on a glass and no warm glass, the follow keys to this process's
first desk-Wall reading, from her first line (kill CRISSA_LIPSYNC_FOLLOW_FIRST=0); (2) the re-measure's set-on glass is
the median over the window its measure ran in, not the one reading at the write (kill CRISSA_LIPSYNC_REF_WINDOW=0)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import pytest  # noqa: E402

import crissa_home  # noqa: E402
import crissa_lipsync as L  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    for k in ("CRISSA_LIPSYNC_GLASS_FOLLOW", "CRISSA_LIPSYNC_FOLLOW_FIRST", "CRISSA_LIPSYNC_REF_WINDOW",
              "CRISSA_LIPSYNC_GLASS_STAT", "CRISSA_LIPSYNC"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (("warm", None), ("warm_at", 0.0), ("live", None), ("first", None), ("ring", []),
                 ("measure_from", None)):
        monkeypatch.setitem(L._GLASS, k, v)
    return tmp_path


def _wall(median):
    return {"wall-tk": {"median_ms": median, "p90_ms": median + 40, "fresh": True}}


def test_a_first_boot_follows_from_its_first_reading(home):
    assert L.glass_follow(_wall(300.0), home)["ms"] == 0.0           # no reading yet: nothing to follow from
    L.note_glass("wall-tk", {"median_ms": 120.0, "p90_ms": 160.0}, warm=False, home=home, now=10.0)
    L.note_glass("wall-tk", {"median_ms": 330.0, "p90_ms": 400.0}, warm=False, home=home, now=12.0)
    gf = L.glass_follow(_wall(330.0), home)
    assert gf["how"] == "its first reading" and gf["halves"] == L.halves_of(210.0)


def test_a_warm_glass_or_a_set_on_glass_still_wins(home):
    L.note_glass("wall-tk", {"median_ms": 120.0}, warm=False, home=home)
    L.write_ms(270.833, home, meta={"by": "hand", "glass_ref": {"median_ms": 200.0}})
    assert L.glass_follow(_wall(300.0), home)["how"] == "set on"


def test_the_first_reading_kill(home, monkeypatch):
    L.note_glass("wall-tk", {"median_ms": 120.0}, warm=False, home=home)
    monkeypatch.setenv("CRISSA_LIPSYNC_FOLLOW_FIRST", "0")
    assert L.glass_follow(_wall(330.0), home)["ms"] == 0.0


def test_the_set_on_glass_is_the_measures_window(home):
    for t, m in ((1.0, 400.0), (10.0, 152.0), (12.0, 338.0), (14.0, 198.0), (16.0, 180.0)):
        L.note_glass("wall-tk", {"median_ms": m, "p90_ms": m + 30}, warm=False, home=home, now=t)
    L.mark_measure_start(9.0)
    w = L.glass_window(9.0, 20.0)
    assert w["n"] == 4 and w["median_ms"] == 189.0                  # 152, 180, 198, 338 - never the 400 before it
    assert L.glass_window(None) == {} and L.glass_window(30.0, 40.0) == {}


def test_reglass_keeps_the_window_median(home, monkeypatch):
    for t, m in ((10.0, 152.0), (12.0, 338.0), (14.0, 198.0)):
        L.note_glass("wall-tk", {"median_ms": m}, warm=False, home=home, now=t)
    L.mark_measure_start(9.0)
    monkeypatch.setattr(L.time, "time", lambda: 15.0)
    res = {"ok": True, "ear_ms": 120.0, "heard_ms": 140.0, "lines": 3, "of": 3}
    L.from_setup(res, home=home, base_ms=0.0, glass={"who": "wall-tk", "median_ms": 194.4}, lands=True)
    ref = L.record(home).get("glass_ref") or {}
    assert ref.get("window") is True and ref["median_ms"] == 198.0, ref     # 152, 198, 338: not the 194.4 snapshot
    src = (ROOT / "lib" / "crissa_lipsync.py").read_text(encoding="utf-8")
    assert "win = glass_window(_GLASS.get(\"measure_from\")) if ref_window_on(env) else {}" in src


def test_the_window_kill(home, monkeypatch):
    monkeypatch.setenv("CRISSA_LIPSYNC_REF_WINDOW", "0")
    assert L.ref_window_on() is False


def test_setup_marks_the_window_where_her_timing_lines_start():
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert src.count("_lip_measure_from(asked)") == 3
