"""Turn fill (the User 2026-09-29): band settings, the profile take's front fold, the turn lookup,
and the separate .mouth.turnfill.json writer. The conductor switch defaults OFF."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import mouth_playlist as mp  # noqa: E402


def test_front_fold_on_a_profile_take(monkeypatch):
    monkeypatch.setattr(mp, "PROFILE_FRONT_FOLD", True)
    assert mp.frame_station("profile", -5) == "forward"
    assert mp.frame_station("profile", -30) == "profile"
    assert mp.frame_station("profile", None) == "profile"
    assert mp.frame_station("forward", -45) == "profile"
    assert mp.frame_station("desk", -5) == "desk"


def test_front_fold_kill(monkeypatch):
    monkeypatch.setattr(mp, "PROFILE_FRONT_FOLD", False)
    assert mp.frame_station("profile", -5) == "profile"


def test_front_fold_is_off_by_default_and_profile_is_a_station():
    assert mp.PROFILE_FRONT_FOLD is False
    assert mp.station_of({"pose_class": "profile"}) == "profile"


def test_turn_band_edges(monkeypatch):
    monkeypatch.setattr(mp, "TURN_FRONT_MAX", 15.0)
    monkeypatch.setattr(mp, "TURN_P45_MAX", 50.0)
    monkeypatch.setattr(mp, "TURN_FILL_SIDE", -1)
    monkeypatch.setattr(mp, "TURN_FILL_BANDS", ("45",))
    assert mp.turn_band(-5) is None
    assert mp.turn_band(-15) == "45"
    assert mp.turn_band(-49.9) == "45"
    assert mp.turn_band(-50) is None          # the 90's band is not allowed yet
    assert mp.turn_band(-60, ("45", "90")) == "90"
    assert mp.turn_band(20) is None           # the -r libraries serve her right only
    assert mp.turn_band(None) is None
    monkeypatch.setattr(mp, "TURN_FILL_SIDE", 0)
    assert mp.turn_band(20) == "45"


def test_turn_lookup_reads_the_profile_bin():
    pl = {"index": {"profile/0/AH": ["a.png"], "profile/0/rest": "r.png", "forward/0/OH": "f.png"}}
    assert mp.turn_lookup(pl, "AH") == "a.png"
    assert mp.turn_lookup(pl, "rest-open") == "r.png"
    assert mp.turn_lookup(pl, "OH") is None
    assert mp.turn_lookup(None, "AH") is None


def _load_script():
    spec = importlib.util.spec_from_file_location("tf", ROOT / "scripts" / "pose-mouth-turnfill.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_turnfill_writes_a_separate_file(tmp_path, monkeypatch):
    tf = _load_script()
    monkeypatch.setattr(tf, "POSE", tmp_path)
    monkeypatch.setattr(tf, "turn_band", lambda y: "45" if y is not None and -50 < y <= -15 else None)
    yaws = [0, -20, -30, 10, -60]
    head = {"src_wh": [736, 400],
            "frames": [{"pose": [0, y, 0], "mouth": [100, 100, 40, 14]} for y in yaws]}
    live = {"schema": "mouth_track/v1", "file": "clip-x.mp4", "n": 5, "fps": 24,
            "frames": [[100, 100, 40, 14], None, None, None, None]}
    (tmp_path / "clip-x.head.json").write_text(json.dumps(head))
    (tmp_path / "clip-x.mouth.json").write_text(json.dumps(live))
    rc, msg = tf.fill("clip-x", write=True)
    assert rc == 0
    out = json.loads((tmp_path / "clip-x.mouth.turnfill.json").read_text())
    assert out["frames"][1] and out["frames"][2]          # in band: filled
    assert out["frames"][3] and out["frames"][4]          # every blank frame with a head gets its box
    assert out["turnfill"]["filled"] == {"45": [1, 2], "front": [3, 4]}
    assert json.loads((tmp_path / "clip-x.mouth.json").read_text()) == live   # live untouched


def test_conductor_switch_defaults_off():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_MOUTH_TURN_FILL", "0")' in src
    assert src.count(".mouth.turnfill.json") >= 1


def test_mirror_band_for_her_left(monkeypatch):
    monkeypatch.setattr(mp, "TURN_FRONT_MAX", 15.0)
    monkeypatch.setattr(mp, "TURN_P45_MAX", 50.0)
    monkeypatch.setattr(mp, "TURN_FILL_SIDE", -1)
    monkeypatch.setattr(mp, "TURN_FILL_BANDS", ("45", "90"))
    monkeypatch.setattr(mp, "TURN_FILL_MIRROR", False)
    assert mp.turn_band(30) is None
    monkeypatch.setattr(mp, "TURN_FILL_MIRROR", True)
    assert mp.turn_band(30) == "45m"
    assert mp.turn_band(70) == "90m"
    assert mp.turn_band(-30) == "45"
    assert mp.turn_band(10) is None


def test_mirror_head_is_its_own_inverse_and_flips_yaw():
    import numpy as np
    import head_frame as hf
    R = hf_rot = None
    y = np.radians(-30.0)
    R = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rec = {"src_wh": [169, 125], "s": 100.0, "R": list(R.ravel()), "t": [652.0, 266.0, 8.0],
           "pose": [-9.9, -30.0, -0.7], "u": [165.0, -2.0], "v": [2.4, 162.6], "A": [84.0, 57.0],
           "nose": [70.0, 21.0], "chin": [85.0, 92.0], "open": 0.4, "cheek_l": [25.0, 32.0],
           "cheek_r": [145.0, 31.0], "mouth_in_tile": [54.0, 34.0, 62.0, 25.0], "crop_xy": [568, 259]}
    m = hf.mirror_head(rec)
    assert m["nose"][0] == 169 - 70
    assert m["cheek_l"] == [169 - 145.0, 31.0] and m["cheek_r"] == [169 - 25.0, 32.0]
    assert m["mouth_in_tile"][0] == 169 - 54 - 62
    assert m["pose"][1] == 30.0 and m["pose"][2] == 0.7
    Rm = np.array(m["R"]).reshape(3, 3)
    assert abs(Rm[0, 2] + R[0, 2]) < 1e-6 and abs(Rm[1, 1] - 1) < 1e-6
    u, v = np.array(m["u"]), np.array(m["v"])
    assert np.sign(u[0] * v[1] - u[1] * v[0]) == np.sign(165.0 * 162.6 + 2.0 * 2.4)   # still right-handed
    back = hf.mirror_head(m)
    for k in ("nose", "A", "chin", "cheek_l", "cheek_r", "mouth_in_tile", "u", "v", "pose", "t"):
        assert np.allclose(back[k], rec[k]), k
    assert np.allclose(back["R"], rec["R"])


def test_conductor_mirror_wiring():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'MIRROR_TAG = "#mirror"' in src
    assert "cv2.flip(img, 1)" in src
    assert "return mirror_head(self.tile_head(base_rel))" in src


def test_profile_take_facing_front_keeps_filmed_mouth():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_PROFILE_FRONT_FILMED", "1")' in src
    i = src.index('self.skip = "profile take facing front"')
    assert src.index("abs(float(_y)) < TURN_FRONT_MAX") < i < src.index("return self._fade_out(base)", i)


def test_other_side_prefers_its_own_take_then_the_mirror():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    i = src.index("    def turn_source(self, band")
    body = src[i:i + 1800]
    assert body.index("TURN_LIBRARIES_OTHER.get(base)") < body.index("MIRROR_TAG")
    assert "self._pl_hit, _turn_lib, _ = self.lib.turn_source(band, vis, self.key)" in src
    assert mp.TURN_LIBRARIES_OTHER == {"45": "profile-45-l", "90": "profile-90-l"}


def test_the_turn_lookup_is_set_before_the_cell_reads_it():
    # 2026-09-29 10:59: the bench painted `UnboundLocalError: _turn_lib` on every note - the cell key read it
    # before the lookup that sets it. Order pinned.
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    i = src.index("                    _turn_lib = None")
    assert i < src.index("self._pl_cell = MP.cell_key(str(_turn_lib or band)")


def test_one_angle_for_the_tile_and_the_landing():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    i = src.index("    def _lips_and_pins(")
    assert 'yaw = getattr(self, "_pl_yaw", None)' in src[i:i + 1500]
    assert "self._pl_yaw = float(_fr0[\"yaw\"])" in src
    assert mp.TURN_FRONT_MAX == 12.0


def test_a_turn_tile_never_crosses_faces():
    # the wall plays Crissa and Lila from one conductor (2026-09-30): the library row must be on the clip's face
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    i = src.index("    def _turn_row(")
    assert "face_of(k) != want" in src[i:i + 500]
    assert "self.lib.turn_source(band, vis, self.key)" in src


def test_turnfill_gaps_keeps_every_box_and_fills_only_the_blanks(tmp_path, monkeypatch):
    """--gaps (round 18, 2026-10-13): a head track rescued after the fill (flourish-glance 45-54) gets its blank frames
    boxed, and every box the first fill wrote stays byte for byte, with its record."""
    tf = _load_script()
    monkeypatch.setattr(tf, "POSE", tmp_path)
    monkeypatch.setattr(tf, "turn_band", lambda y: "45" if y is not None and -90 < y <= -27 else None)
    yaws = [0, -40, -60, -70, -30]
    head = {"src_wh": [736, 400],
            "frames": [{"pose": [0, y, 0], "mouth": [100 + k, 100, 40, 14]} for k, y in enumerate(yaws)]}
    first = {"schema": "mouth_track/v1", "file": "clip-x.mp4", "n": 5, "fps": 24, "note": "turn fill of clip-x",
             "frames": [[1, 2, 3, 4], [5, 6, 7, 8], None, None, [9, 9, 9, 9]],
             "turnfill": {"base": "clip-x.mouth.turn.json", "filled": {"45": [1, 4]}}}
    (tmp_path / "clip-x.head.json").write_text(json.dumps(head))
    (tmp_path / "clip-x.mouth.turnfill.json").write_text(json.dumps(first))
    rc, msg = tf.fill("clip-x", write=True, gaps=True)
    assert rc == 0, msg
    out = json.loads((tmp_path / "clip-x.mouth.turnfill.json").read_text())
    assert out["frames"][0] == [1, 2, 3, 4] and out["frames"][1] == [5, 6, 7, 8] and out["frames"][4] == [9, 9, 9, 9]
    assert out["frames"][2] and out["frames"][3]
    assert out["turnfill"]["base"] == "clip-x.mouth.turn.json" and out["note"] == "turn fill of clip-x"
    assert out["turnfill"]["filled"]["45"] == [1, 2, 3, 4]
    assert out["turnfill"]["gaps"]["filled"] == {"45": [2, 3]}
    rc, msg = tf.fill("clip-x", write=True, gaps=True)           # a second pass has nothing to do and writes nothing
    assert rc == 0 and "no gap" in msg
    assert json.loads((tmp_path / "clip-x.mouth.turnfill.json").read_text()) == out


def test_turnfill_gaps_needs_an_existing_fill(tmp_path, monkeypatch):
    tf = _load_script()
    monkeypatch.setattr(tf, "POSE", tmp_path)
    (tmp_path / "clip-y.head.json").write_text(json.dumps({"frames": []}))
    rc, msg = tf.fill("clip-y", write=True, gaps=True)
    assert rc == 2 and "--gaps needs" in msg
    assert not (tmp_path / "clip-y.mouth.turnfill.json").exists()


def test_flourish_glance_turn_has_no_blank_frame_with_a_head():
    """.101 round 17: the look up and to her right painted no mouth on frames 45-54 (the 09-30 fill predates the 10-05
    head rescue). Every blank frame of a shipped turn fill whose head track has the frame is a gap."""
    pose = ROOT / "display" / "crissa-poses"
    fill = json.loads((pose / "clip-flourish-glance.mouth.turnfill.json").read_text())
    head = json.loads((pose / "clip-flourish-glance.head.json").read_text())["frames"]
    gaps = [i for i, b in enumerate(fill["frames"]) if not b and i < len(head) and head[i]]
    assert gaps == []
    assert fill["turnfill"]["gaps"]["filled"] == {"45": list(range(45, 55))}
