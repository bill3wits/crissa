"""Round 45 (.101 round 44, the User: "When the screens are loading she needs about +500 lead; once complete we go to the
270"): every line adds the desk Wall's live glass lag less the lag the value was set on, in half frames (kill
CRISSA_LIPSYNC_GLASS_FOLLOW=0); and a first boot times her lines when this machine has no lip-sync measurement (kill
CRISSA_LIPSYNC_MEASURE_WANTED=0)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import pytest  # noqa: E402

import crissa_home  # noqa: E402
import crissa_lipsync as L  # noqa: E402

H = L.HALF_FRAME_MS


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    for k in ("CRISSA_LIPSYNC_GLASS_FOLLOW", "CRISSA_LIPSYNC_GLASS_STAT", "CRISSA_LIPSYNC_GLASS_WARM_N", "CRISSA_LIPSYNC"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(L._GLASS, "warm", None)
    monkeypatch.setitem(L._GLASS, "warm_at", 0.0)
    monkeypatch.setitem(L._GLASS, "live", None)
    return tmp_path


def _wall(median, p90=None, fresh=True):
    return {"wall-tk": {"median_ms": median, "p90_ms": p90 if p90 is not None else median + 50, "fresh": fresh}}


def test_the_value_follows_the_glass_it_was_set_on(home):
    L.write_ms(270.833, home, meta={"by": "hand", "glass_ref": {"median_ms": 110.0, "p90_ms": 150.0}})
    gf = L.glass_follow(_wall(214.0, 365.0), home)
    assert gf["halves"] == 5 and gf["ms"] == L.ms_of(5) and gf["how"] == "set on"     # +104 -> +104.2: his +375
    assert L.glass_follow(_wall(110.0), home)["ms"] == 0.0
    assert L.glass_follow(_wall(214.0, fresh=False), home)["ms"] == 0.0               # no fresh desk Wall reading


def test_with_no_reference_the_warm_glass_is_learned(home):
    L.write_ms(270.833, home, meta={"by": "hand"})
    assert L.glass_follow(_wall(214.0), home)["ms"] == 0.0           # nothing warm yet: the kit's start was set cold
    for _ in range(5):
        L.note_glass("wall-tk", {"median_ms": 97.0, "p90_ms": 120.0}, warm=True, home=home)
    L.note_glass("wall-tk", {"median_ms": 300.0}, warm=False, home=home)              # a cold reading never counts
    assert L.warm_glass(home)["median_ms"] == 97.0 and json.loads(L.warm_path(home).read_text())["readings"]
    gf = L.glass_follow(_wall(214.0), home)
    assert gf["how"] == "warm" and gf["halves"] == L.halves_of(214.0 - 97.0)


def test_a_press_keeps_the_glass_it_was_made_on(home):
    L.note_glass("wall-tk", {"median_ms": 162.0, "p90_ms": 189.0}, warm=False, home=home)
    L.act({"lead_ms": 375.0}, 0.0, home)
    assert L.record(home)["glass_ref"] == {"median_ms": 162.0, "p90_ms": 189.0}


def test_the_dials_and_the_kill(home, monkeypatch):
    L.write_ms(270.833, home, meta={"by": "hand", "glass_ref": {"median_ms": 110.0, "p90_ms": 150.0}})
    monkeypatch.setenv("CRISSA_LIPSYNC_GLASS_STAT", "p90")
    assert L.glass_follow(_wall(214.0, 365.0), home)["halves"] == L.halves_of(215.0)
    monkeypatch.setenv("CRISSA_LIPSYNC_GLASS_FOLLOW", "0")
    assert L.glass_follow(_wall(214.0, 365.0), home)["ms"] == 0.0
    monkeypatch.delenv("CRISSA_LIPSYNC_GLASS_FOLLOW")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert L.glass_follow(_wall(214.0, 365.0), home)["ms"] == 0.0                     # never the house


def test_the_player_adds_it_to_every_line():
    c = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    eff = c.split("def effective_sheet_lead_ms()", 1)[1].split("\ndef ", 1)[0]
    assert "crissa_lipsync.glass_follow(glass_lag_state())" in eff and "if not IS_BENCH:" in eff
    rep = c.split("def glass_lag_report(", 1)[1].split("\ndef ", 1)[0]
    assert "crissa_lipsync.note_glass(who, rec," in rep


def test_a_first_boot_times_her_lines_with_no_lip_sync_measure(home, monkeypatch):
    import crissa_init as zi
    monkeypatch.setattr(zi, "_home", lambda: home)
    monkeypatch.setattr(zi, "mouths_wanted", lambda: (False, "her prints hold (6 in use)"))
    monkeypatch.delenv("CRISSA_LIPSYNC_MEASURE_WANTED", raising=False)
    want, why = zi.mouths_or_lip_wanted()
    assert want and "no lip sync measurement" in why
    (home / L.SETUP_FILE).write_text(json.dumps({"outcome": "none", "why": "2 of 6 lines clear"}))
    assert zi.mouths_or_lip_wanted()[0] and "2 of 6 lines clear" in zi.mouths_or_lip_wanted()[1]
    (home / L.SETUP_FILE).write_text(json.dumps({"outcome": "measured", "ear_ms": 120}))
    assert zi.mouths_or_lip_wanted() == (False, "her prints hold (6 in use)")
    (home / L.SETUP_FILE).unlink()
    monkeypatch.setenv("CRISSA_LIPSYNC_MEASURE_WANTED", "0")
    assert zi.mouths_or_lip_wanted()[0] is False


def test_reglass_says_why_there_is_nothing_to_redo(home):
    assert "Setup recorded none" in L.reglass({"who": "wall-tk", "median_ms": 100.0}, home)["why"]
    (home / L.SETUP_FILE).write_text(json.dumps({"outcome": "none", "why": "nothing was recorded: no microphone"}))
    assert "no microphone" in L.reglass({"who": "wall-tk", "median_ms": 100.0}, home)["why"]
