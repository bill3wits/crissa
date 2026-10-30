"""Round 42 (.101's R41-3, the User 10-22: "perfect at 270 again"). Setup logged "measured +270.8 ms, keeping +375 ms"
on the re-measure after her walk-in (reglass, the glass she is seen on): the right number, kept as a suggestion only.
That measurement now lands. Setup's own first sample (the Wall behind its window) still only reports, the person's own
value is kept, a path past the 600 ms ceiling is said and left alone, the house is never touched. The kit's start stays
375 until the glass measure lands. Kill CRISSA_LIPSYNC_GLASS_LANDS=0 (reglass reports, round 41)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_lipsync as LS  # noqa: E402

BASE = 104.167
ON_101 = {"ok": True, "heard_ms": 215.2, "ear_ms": 195.2, "lines": 3, "of": 3}
BEHIND = {"who": "wall-tk", "median_ms": 106.7}           # Setup's own sample, the Wall behind its window
SEEN = {"who": "wall-tk", "median_ms": 384.0}             # after the walk-in: the glass she is seen on


@pytest.fixture
def kit(tmp_path, monkeypatch):
    for k in ("CRISSA_LIPSYNC_FULL_WALL", "CRISSA_SETUP_LIPSYNC_MEASURE", "CRISSA_LIPSYNC", "CRISSA_SETUP_LIPSYNC_GLASS",
              "CRISSA_SETUP_LIPSYNC_GLASS_COUNT", "CRISSA_SETUP_LIPSYNC_GLASS_REF_MS",
              "CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS", "CRISSA_LIPSYNC_GLASS_LANDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: False)
    env = json.loads((ROOT / "config" / "crissa-mouth.json").read_text(encoding="utf-8"))["env"]
    for k in ("CRISSA_LIPSYNC_DEFAULT_MS", "CRISSA_LIPSYNC_SETUP_WRITES"):
        monkeypatch.setenv(k, env[k])
    LS._CACHE.clear()
    return tmp_path


def test_setup_reports_then_the_glass_measure_lands(kit):
    first = LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=BEHIND)
    assert first["outcome"] == "measured" and LS.offset_ms(kit) == 375.0, "the start stays until the glass lands"
    rep = LS.reglass(SEEN, kit)
    assert rep["outcome"] == "set" and rep["landed"]
    assert LS.offset_ms(kit) == rep["lead_ms"] == LS.ms_of(rep["halves"]) != 375.0
    assert LS.record(kit)["by"] == "setup"
    assert rep["log"].startswith("Lip sync set to +") and "keeping" not in rep["log"]
    # a later Setup on the same machine reports again; its walk-in lands again
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=BEHIND)
    assert LS.offset_ms(kit) == rep["lead_ms"]
    again = LS.reglass({"who": "wall-tk", "median_ms": 405.0}, kit)
    assert again["outcome"] == "set" and LS.offset_ms(kit) == again["lead_ms"]


def test_his_own_value_is_kept(kit):
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=BEHIND)
    assert LS.act({"step": -1}, BASE, kit)["ok"]
    mine = LS.offset_ms(kit)
    rep = LS.reglass(SEEN, kit)
    assert rep["outcome"] != "set" and not rep.get("landed") and LS.offset_ms(kit) == mine


def test_a_slow_path_is_said_and_left(kit):
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=BEHIND)
    rep = LS.reglass({"who": "wall-tk", "median_ms": 900.0}, kit)
    assert rep["outcome"] == "measured" and rep["slow"] and LS.offset_ms(kit) == 375.0


def test_the_kill_reports_only(kit, monkeypatch):
    monkeypatch.setenv("CRISSA_LIPSYNC_GLASS_LANDS", "0")
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=BEHIND)
    rep = LS.reglass(SEEN, kit)
    assert rep["outcome"] == "measured" and LS.offset_ms(kit) == 375.0 and ", keeping +375 ms (" in rep["log"]


def test_the_house_is_never_touched(kit, monkeypatch):
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=BEHIND)
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: True)
    assert LS.reglass(SEEN, kit)["outcome"] == "house" and not LS.path(kit).exists()
