"""Round 36, 2026-10-20 (the User via house-rails-b3: "yes go with +375 as the start"): a kit machine with no lip-sync.json
starts at +375 ms, Setup's measurement is a report and a suggestion (never written), and his own menu value wins and
survives a restart. The code's defaults stay 0 / Setup writes (the house, the older tests); the kit's env file sets both."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_lipsync as LS  # noqa: E402

BASE = 104.167                                   # Crissa's library lead (dial +20.8 + zero +83.3)
ON_101 = {"ok": True, "heard_ms": 215.2, "ear_ms": 195.2, "lines": 3, "of": 3}
GLASS = {"who": "wall-tk", "median_ms": 275.1}


@pytest.fixture
def kit(tmp_path, monkeypatch):
    for k in ("CRISSA_LIPSYNC_FULL_WALL", "CRISSA_SETUP_LIPSYNC_MEASURE", "CRISSA_LIPSYNC", "CRISSA_SETUP_LIPSYNC_GLASS",
              "CRISSA_SETUP_LIPSYNC_GLASS_COUNT", "CRISSA_SETUP_LIPSYNC_GLASS_REF_MS",
              "CRISSA_SETUP_LIPSYNC_WALL_TK_EXTRA_MS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: False)
    env = json.loads((ROOT / "config" / "crissa-mouth.json").read_text(encoding="utf-8"))["env"]
    for k in ("CRISSA_LIPSYNC_DEFAULT_MS", "CRISSA_LIPSYNC_SETUP_WRITES"):
        monkeypatch.setenv(k, env[k])            # what the launcher puts on the player (scripts/crissa.py mouth_env)
    LS._CACHE.clear()
    return tmp_path


def test_the_kit_ships_the_start_and_report_only_setup():
    spec = importlib.util.spec_from_file_location("crissa_launcher_r36", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = mod.mouth_env("crissa")
    assert env["CRISSA_LIPSYNC_DEFAULT_MS"] == "375" and env["CRISSA_LIPSYNC_SETUP_WRITES"] == "0"
    assert LS.halves_of(375.0) == 18


def test_a_fresh_kit_machine_starts_at_375(kit):
    assert not LS.path(kit).exists()
    assert LS.offset_ms(kit) == 375.0
    v = LS.view(BASE, kit)
    assert v["halves"] == 18 and "kit's start" in v["value"]
    assert LS.total_ms(BASE, LS.offset_ms(kit)) == pytest.approx(479.167)


def test_the_code_default_and_the_house_stay_at_0(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_LIPSYNC_DEFAULT_MS", raising=False)
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: False)
    LS._CACHE.clear()
    assert LS.offset_ms(tmp_path) == 0.0
    monkeypatch.setenv("CRISSA_LIPSYNC_DEFAULT_MS", "375")
    monkeypatch.setattr(LS.crissa_home, "is_house", lambda *a, **k: True)
    assert LS.offset_ms(tmp_path) == 0.0, "the house never takes the kit's start"


def test_setup_measures_and_keeps_375(kit):
    rep = LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=GLASS)
    assert rep["outcome"] == "measured" and rep["lead_ms"] == 375.0
    assert rep["log"].startswith("lip sync: Setup measured +") and ", keeping +375 ms (" in rep["log"]
    assert not LS.path(kit).exists() and LS.offset_ms(kit) == 375.0, "Setup wrote nothing"
    # the menu offers Setup's number as a suggestion
    v = LS.view(BASE, kit)
    assert v["suggest_ms"] == rep["suggest_ms"] and "a suggestion" in v["value"]
    # round 42 (R41-3): the re-measure after her walk-in, on the glass she is seen on, is the one kept
    again = LS.reglass({"who": "wall-tk", "median_ms": 384.0}, kit, base_ms=BASE)
    assert again["outcome"] == "set" and again["landed"] and LS.offset_ms(kit) == again["lead_ms"] != 375.0


def test_his_hand_value_wins_and_survives_a_restart(kit):
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=GLASS)
    got = LS.act({"step": -1}, BASE, kit)
    assert got["ok"] and got["lip_sync"]["halves"] == 17
    LS._CACHE.clear()                            # a restart: nothing held in the process
    assert LS.offset_ms(kit) == pytest.approx(354.167) and LS.record(kit)["by"] == "hand"
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=GLASS)      # a later Setup still writes nothing
    assert LS.offset_ms(kit) == pytest.approx(354.167)
    # his 0 is his: kept as a file, never read back as the start
    assert LS.act({"halves": 0}, BASE, kit)["ok"]
    LS._CACHE.clear()
    assert LS.path(kit).exists() and LS.offset_ms(kit) == 0.0
    # reset goes back to the start, kept as his own (round 17: a later Setup keeps it)
    assert LS.act({"reset": True}, BASE, kit)["ok"]
    LS._CACHE.clear()
    assert LS.offset_ms(kit) == 375.0 and LS.record(kit)["by"] == "hand"


def test_setup_writes_again_under_the_kill(kit, monkeypatch):
    monkeypatch.setenv("CRISSA_LIPSYNC_SETUP_WRITES", "1")
    rep = LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=GLASS)
    assert rep["outcome"] == "set" and LS.path(kit).exists()


def test_a_path_past_the_ceiling_is_said_once_and_nothing_stops(kit):
    """The User 2026-10-20: "Keep the 600 ceiling"; a slower machine gets a plain sentence, never an exit."""
    slow = {"ok": True, "heard_ms": 215.2, "ear_ms": 195.2, "lines": 3, "of": 3}
    rep = LS.from_setup(slow, home=kit, base_ms=BASE, glass={"who": "wall-tk", "median_ms": 900.0})
    assert rep["outcome"] == "measured" and rep["slow"] and rep["slow_ms"] > LS.LINE_MAX_MS
    assert "This display and speaker path is slow (measured +" in rep["line"] and "game mode" in rep["line"]
    assert "slow path:" in rep["log"] and LS.offset_ms(kit) == 375.0
    v = LS.view(BASE, kit)
    assert v["slow"].startswith("This display and speaker path is slow") and v["slow"] in v["value"]
    # a path inside the ceiling says nothing of the kind
    rep = LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=GLASS)
    assert not rep.get("slow") and "slow" not in LS.view(BASE, kit)


def test_the_walk_in_remeasure_says_a_slow_path_too(kit):
    LS.from_setup(ON_101, home=kit, base_ms=BASE, glass=GLASS)            # Setup, with the library lead
    rep = LS.reglass({"who": "wall-tk", "median_ms": 900.0}, kit)          # Init passes no base: Setup's is reused
    assert rep["outcome"] == "measured" and rep["slow"] and LS.offset_ms(kit) == 375.0
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert 'if note.get("outcome") in ("set", "kept", "measured") and note.get("line"):' in src
