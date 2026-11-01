"""Round 40 N16 (.101 round 38 ~10:36): the picture cache switched itself off on a region MISMATCH and the plate read
"Building Crissa... 0 %" for good, every picture drawn fresh; before it the memory tier was full and evicting. A cache
that is off says so (restart the Wall); a full cache is not a build. Kill CRISSA_PLATE_CACHE_STATE=0."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_build as zb  # noqa: E402
import crissa_home  # noqa: E402
import crissa_wall_key as wk  # noqa: E402

BUILT = {"running": False, "loops_total": 10, "loops_built": 10, "name": "Crissa"}
JUMPY_ALL_FRESH = {"clip": "listen", "n": 524, "fresh": 524, "skipped": 40}     # .101's lap after the MISMATCH


def test_a_cache_that_is_off_says_restart_not_building(monkeypatch):
    monkeypatch.delenv("CRISSA_PLATE_CACHE_STATE", raising=False)
    v = zb.plate_view(dict(BUILT, lap=dict(JUMPY_ALL_FRESH, cache_off="a region answer differed")))
    assert v["show"] and v["title"] == "Picture cache off" and "restart the Wall" in v["line"]
    text = wk.building_text(v)
    assert "Building" not in text and "%" not in text and wk.BUILD_LAG_LINE not in text
    # while her loops are still being built, too: it is not a build that will finish
    v = zb.plate_view(dict(BUILT, running=True, loops_built=3, loops_counted=True,
                           lap=dict(JUMPY_ALL_FRESH, cache_off="x")))
    assert v["title"] == "Picture cache off"


def test_a_full_cache_is_not_a_build(monkeypatch):
    monkeypatch.delenv("CRISSA_PLATE_CACHE_STATE", raising=False)
    lap = {"clip": "listen", "n": 72, "fresh": 72, "skipped": 24}   # round 45: a cold lap (the plate follows drops)
    assert zb.plate_view(dict(BUILT, lap=lap))["show"]                # jumpy, as round 29
    assert zb.plate_view(dict(BUILT, lap=dict(lap, full=True))) == {"show": False}
    # the build's own count still shows while her loops are being built
    v = zb.plate_view(dict(BUILT, running=True, loops_built=3, loops_counted=True, lap=dict(lap, full=True)))
    assert v["show"] and v["title"].startswith("Building")


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("CRISSA_PLATE_CACHE_STATE", "0")
    v = zb.plate_view(dict(BUILT, lap=dict(JUMPY_ALL_FRESH, cache_off="x", full=True)))
    assert v["title"].startswith("Building") and v.get("jumpy")


def test_the_lap_record_carries_the_cache_state(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    zb.note_lap("listen", 524, 524, 21.0, home=tmp_path, skipped=40, cache_off="a region answer differed", full=True)
    rec = json.loads((zb.root(tmp_path) / "lap.json").read_text())
    assert rec["cache_off"] == "a region answer differed" and rec["full"] is True
    zb.note_lap("listen", 120, 1, 5.0, home=tmp_path, skipped=0)
    rec = json.loads((zb.root(tmp_path) / "lap.json").read_text())
    assert "cache_off" not in rec and "full" not in rec


def test_the_wall_reads_its_cache_counts_into_the_lap():
    import house_wall_ascii as hw
    hw._cache_note({"dead": "a region answer differed", "bytes_held": 6589 * 2 ** 20, "cap": 6656 * 2 ** 20})
    assert hw._CACHE_NOTE == {"dead": "a region answer differed", "full": True}
    hw._cache_note({"dead": None, "bytes_held": 100, "cap": 6656 * 2 ** 20})
    assert hw._CACHE_NOTE == {"dead": "", "full": False}
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'cache_off=_CACHE_NOTE["dead"], full=_CACHE_NOTE["full"]' in src and "_cache_note(cs)" in src
