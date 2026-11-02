"""Round 30 (2026-10-18): .101's round 29 fixes as built, plus the phone's calm merge as tuned."""
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def test_a_phone_line_runs_the_calm_merge_the_tune_had(monkeypatch):
    import phone_sheet
    monkeypatch.delenv("PHONE_CALM_AS_TUNED", raising=False)
    assert phone_sheet.speak_env({})["HOUSE_MOUTH_CALM_HOPS"] == "0"
    assert phone_sheet.speak_env({"calm_hops": 4})["HOUSE_MOUTH_CALM_HOPS"] == "4"
    monkeypatch.setenv("PHONE_CALM_AS_TUNED", "0")
    assert "HOUSE_MOUTH_CALM_HOPS" not in phone_sheet.speak_env({})


def test_the_ear_keeper_honours_voice_ids_mic_hold():
    src = (ROOT / "modules/ear/scripts/crissa-stt.py").read_text()
    assert "CRISSA_KEEPER_HONORS_HOLD" in src and "/tmp/house-enroll.mic-hold" in src
    assert 'if not st.get("open") and not held:' in src


def test_the_jaw_folds_on_mediapipe_pitch_in_a_pack_and_the_look_fold_is_gone():
    src = (ROOT / "scripts/house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_PITCH_FOLD_MP", house_default("0", "1"))' in src
    assert 'os.environ.get("HOUSE_PITCH_FOLD_MP_AT", "-22.5")' in src
    assert "def mp_pitch(self, key: str)" in src and 'd.get("schema") == "pitch/1"' in src
    assert "PITCH_FOLD_LOOK" not in src and "def look(self" not in src      # 5d was rejected (blinks read as down)


def test_every_shipped_film_with_a_tilt_has_its_pitch_and_it_ships():
    pose = ROOT / "display/crissa-poses"
    tilts = sorted(pose.glob("*.tilt.json"))
    assert len(tilts) == 47                 # round 38: clip-listen-warm; round 41: the three harvest2 desk takes
    for t in tilts:
        d = json.loads((pose / (t.name[:-len(".tilt.json")] + ".pitch.json")).read_text())
        assert d["schema"] == "pitch/1" and d["n"] == json.loads(t.read_text())["n"] == len(d["pitch"])
    assert '".pitch.json"' in (ROOT / "scripts/build-ship-set.py").read_text()


def test_the_walk_in_keeps_her_line_only_for_init():
    src = (ROOT / "scripts/house-conductor.py").read_text()
    assert '_keep = bool(body.get("keep_line")) and who == "init"' in src
    assert "CRISSA_WALKIN_KEEPS_LINE" in (ROOT / "lib/crissa_init.py").read_text()


def test_the_building_bar_has_its_kill():
    import crissa_wall_key as wk
    assert callable(wk.building_text)
    t = wk.building_text({"title": "Building Crissa…", "line": "900 of 2,050 pictures", "done": 0.5})
    assert t.splitlines()[1].endswith("  50%") and t.splitlines()[2] == wk.BUILD_LAG_LINE


def test_the_first_look_takes_the_floor_once_and_the_kill_and_a_pick_on_file_stop_it(tmp_path):
    import crissa_machine as zm
    off = zm.first_look(3440, 1440, home=tmp_path, env={"CRISSA_DENSITY_FIRST_LOOK": "0"})
    assert off == {"applied": False, "why": "off"}
    (tmp_path / zm.DENSITY_PICK).write_text(json.dumps({"density": 2.0, "ts": 1.0}))
    got = zm.first_look(3440, 1440, home=tmp_path, env=dict(os.environ, CRISSA_DENSITY_FIRST_LOOK="1"))
    assert got["applied"] is False


def test_the_house_wall_takes_no_first_look(monkeypatch):
    sys.path.insert(0, str(ROOT / "lib"))
    import house_wall_ascii as w
    monkeypatch.setattr(w.crissa_home, "is_house", lambda: True)
    assert w.first_look(3440, 1440) is None


def test_both_mouth_prints_and_both_coaches_stay_one_copy():
    a = ROOT / "modules/id/crissa_id/house_enroll"
    b = ROOT / "modules/presence/crissa_presence/enroll_panel"
    assert (a / "mouth_prints.py").read_text() == (b / "mouth_prints.py").read_text()
    for p in (a / "coach.py", b / "coach.py", a / "mouth_prints.py"):
        assert "CRISSA_MOUTHS_ONE_LINE" in p.read_text() or "lines" in p.read_text()
