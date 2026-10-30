"""scripts/pose-bow-map.py + the painter's bow map (.101's bowed-harvest ask, 10-24): a frame bowed past the library's
tiles wears the bow band that reaches it, a frame nothing reaches keeps her filmed mouth, and the switch is off."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    s = importlib.util.spec_from_file_location("pbm", ROOT / "scripts" / "pose-bow-map.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def _clip(d, stem, pitch, yaw, index=None):
    (d / ("clip-%s.pitch.json" % stem)).write_text(json.dumps({"pitch": pitch}))
    (d / ("clip-%s.head.json" % stem)).write_text(json.dumps({"frames": [{"pose": [p, y, 0]} for p, y in zip(pitch, yaw)]}))
    if index is not None:
        (d / ("clip-%s.playlist.json" % stem)).write_text(json.dumps({"bins": ["desk/0"], "index": index}))


def _lib(tmp_path, bow_pitch=(-34.0, -38.0), bow_yaw=(-14.0, -14.0)):
    d = tmp_path / "poses"
    d.mkdir()
    (d / "inventory.json").write_text(json.dumps({"clips": {
        "harv": {"role": "oneshot", "file": "clip-harv.mp4"},
        "bow-x": {"role": "oneshot", "file": "clip-bow-x.mp4", "bow": True},
        "wob": {"role": "flourish", "file": "clip-wob.mp4"}}}))
    _clip(d, "harv", [-10.0, -20.0, -30.0], [0.0, 0.0, 0.0])                     # today's tiles: -30..-10 at yaw 0
    _clip(d, "bow-x", list(bow_pitch), list(bow_yaw),
          {"desk/0/rest": "t/bow-x_f0000_desk_0.png", "desk/0/AH": "t/bow-x_f0001_desk_0.png"})
    idx = {"desk/0/rest": "t/harv_f0000_desk_0.png", "desk/0/AH": "t/harv_f0002_desk_0.png"}
    # wob: 3 normal, 4 deep at yaw -14 (the bow band), 4 deep at yaw +40 (nothing), 3 normal
    _clip(d, "wob", [-20.0] * 3 + [-36.0] * 4 + [-36.0] * 4 + [-20.0] * 3,
          [0.0] * 3 + [-13.0] * 4 + [40.0] * 4 + [0.0] * 3, idx)
    return d


def test_a_bowed_frame_wears_its_band_and_a_lost_one_is_filmed(tmp_path):
    bands, out = _mod().frame_map(str(_lib(tmp_path)), 10.0, 3)
    assert set(bands) == {"bow-x"}
    assert out["wob"] == [None] * 3 + ["bow-x"] * 4 + ["filmed"] * 4 + [None] * 3


def test_a_dip_shorter_than_the_run_is_an_outlier(tmp_path):
    m = _mod()
    assert m.smooth([None, None, None, "filmed", None, None, None], 3) == [None] * 7
    assert m.smooth(["bow-x"] * 4 + [None] * 2 + ["bow-x"] * 4, 3) == ["bow-x"] * 10


def test_no_band_past_its_own_tiles_pitch(tmp_path):
    d = _lib(tmp_path, bow_pitch=(-31.0, -33.0))                 # the band stops at -33: -36 is not stretched onto it
    _, out = _mod().frame_map(str(d), 10.0, 3)
    assert out["wob"][3:7] == ["filmed"] * 4


def test_the_cli_writes_and_clears_its_files(tmp_path):
    d = _lib(tmp_path)
    (d / "clip-gone.bow.json").write_text("{}")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "pose-bow-map.py"), "--dir", str(d)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads((d / "clip-wob.bow.json").read_text())
    assert got["schema"] == "bow-map/1" and got["n"] == 14 and got["bands"] == ["bow-x"]
    assert not (d / "clip-gone.bow.json").exists()


def test_the_painter_reads_the_map_only_when_switched_on():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    m = re.search(r'^MOUTH_BOW = os\.environ\.get\("HOUSE_MOUTH_BOW", "(\d)"\)', src, re.M)
    assert m and m.group(1) == "0"
    assert '"bow-map/1"' in src and 'if MOUTH_BOW:' in src


def test_the_base_is_the_clips_own_index_not_every_tile(tmp_path):
    """The User 2026-10-27 (the double chin): a clip's "today's tiles" are the tiles its own playlist index names.
    wob's index names harv frame 0 (-10) and frame 2 (-30); a -20 frame still reaches (inside that span), and a frame at
    -36 does not, although the library-wide set would say the same. Give wob an index of LIGHT tiles only (-10) and the
    -20 frames are past its own tiles: they go to a band that holds them or stay filmed."""
    m = _mod()
    d = _lib(tmp_path, bow_pitch=(-18.0, -22.0), bow_yaw=(0.0, 0.0))          # a straight band around -20
    light = {"desk/0/rest": "t/harv_f0000_desk_0.png", "desk/0/AH": "t/harv_f0000_desk_0.png"}
    _clip(d, "wob", [-20.0] * 3 + [-36.0] * 4 + [-36.0] * 4 + [-20.0] * 3,
          [0.0] * 3 + [-13.0] * 4 + [40.0] * 4 + [0.0] * 3, light)
    _, out = m.frame_map(str(d), 10.0, 3)
    assert out["wob"][:3] == ["bow-x"] * 3 and out["wob"][-3:] == ["bow-x"] * 3
    assert m.own_tiles(str(d), "wob", m.cov.roles(str(d))) == [(-10.0, 0.0), (-10.0, 0.0)]


def test_two_bands_holding_a_frame_the_nearer_in_depth_wins(tmp_path):
    """bow-a -32..-29 and bow-g -36..-30 both hold -31; by yaw alone the deeper band won (the double chin, 10-27)."""
    m = _mod()
    d = _lib(tmp_path)
    (d / "inventory.json").write_text(json.dumps({"clips": {
        "harv": {"role": "oneshot", "file": "clip-harv.mp4"},
        "bow-a": {"role": "oneshot", "file": "clip-bow-a.mp4", "bow": True},
        "bow-g": {"role": "oneshot", "file": "clip-bow-g.mp4", "bow": True},
        "wob": {"role": "flourish", "file": "clip-wob.mp4"}}}))
    _clip(d, "bow-a", [-29.0, -32.0], [4.0, 4.0], {"desk/0/rest": "t/bow-a_f0000_desk_0.png", "desk/0/AH": "t/bow-a_f0001_desk_0.png"})
    _clip(d, "bow-g", [-30.0, -36.0], [0.0, 0.0], {"desk/0/rest": "t/bow-g_f0000_desk_0.png", "desk/0/AH": "t/bow-g_f0001_desk_0.png"})
    _clip(d, "wob", [-31.0] * 4 + [-35.0] * 4, [0.0] * 8,
          {"desk/0/rest": "t/harv_f0000_desk_0.png", "desk/0/AH": "t/harv_f0001_desk_0.png"})   # own tiles -10..-20
    _, out = m.frame_map(str(d), 10.0, 3)
    assert out["wob"] == ["bow-a"] * 4 + ["bow-g"] * 4


def test_the_downs_meet_the_fronts_and_carry_the_bottom_one_switch_each_way(tmp_path):
    """The User 2026-10-28 (Crissa desk v2's bow): the fronts tilt down to where they come off, the downs carry every
    deeper frame - reaching up to meet the fronts (--band-up) and past their own deepest tile (--band-slack) - so the
    bow switches once each way and never falls to the filmed mouth. Both 0 = the band's own span, as before."""
    d = _lib(tmp_path, bow_pitch=(-31.0, -33.0), bow_yaw=(0.0, 0.0))
    (d / "clip-wob.pitch.json").write_text(json.dumps({"pitch": [-20.0] * 3 + [-27.0] * 4 + [-36.0] * 4 + [-27.0] * 4}))
    (d / "clip-wob.head.json").write_text(json.dumps({"frames": [{"pose": [0.0, 0.0, 0]}] * 15}))
    m = _mod()
    _, out = m.frame_map(str(d), 10.0, 3)
    assert out["wob"][3:11] == [None] * 4 + ["filmed"] * 4            # today: fronts to -30, the bottom is filmed
    _, out = m.frame_map(str(d), 10.0, 3, 0.0, 4.0, None, 5.0)
    assert out["wob"] == [None] * 7 + ["bow-x"] * 4 + [None] * 4      # -36 is 3 past the band's -33: carried
    _, out = m.frame_map(str(d), 10.0, 3, 0.0, 4.0, {"other"}, 5.0)
    assert "wob" not in out                                           # --clips maps only what it names


def test_in_meet_mode_a_band_never_takes_a_frame_above_the_fronts(tmp_path):
    """2026-10-28: --band-up 90 let the down band take v2-fl-lean-back's head-UP frames. A frame higher than the fronts'
    tiles (or turned at their pitch) is not a bow: it stays filmed, as it would with no band at all."""
    d = _lib(tmp_path, bow_pitch=(-31.0, -33.0), bow_yaw=(0.0, 0.0))
    (d / "clip-wob.pitch.json").write_text(json.dumps({"pitch": [-20.0] * 3 + [10.0] * 4 + [-36.0] * 4 + [-20.0] * 4}))
    (d / "clip-wob.head.json").write_text(json.dumps({"frames": [{"pose": [0.0, 0.0, 0]}] * 15}))
    _, out = _mod().frame_map(str(d), 10.0, 3, 0.0, 90.0, None, 90.0)
    assert out["wob"][3:7] == ["filmed"] * 4 and out["wob"][7:11] == ["bow-x"] * 4
    (d / "clip-wob.head.json").write_text(json.dumps({"frames": [{"pose": [0.0, 0.0, 0]}] * 3 + [{"pose": [-20.0, 40.0, 0]}] * 4
                                                     + [{"pose": [0.0, 0.0, 0]}] * 8}))
    (d / "clip-wob.pitch.json").write_text(json.dumps({"pitch": [-20.0] * 7 + [-36.0] * 4 + [-20.0] * 4}))
    _, out = _mod().frame_map(str(d), 10.0, 3, 0.0, 90.0, None, 90.0)
    assert out["wob"][3:7] == [None] * 4                              # turned at the fronts' pitch: the turn library's


def test_the_tier_map_covers_each_frame_by_its_own_angle(tmp_path):
    """the User 10-28: 'measure the angles in intake, then assign ranks to its json to cover them'. Two tiers (a forward
    take's tiles at -2, a working take's at -14); a clip whose own tiles are the working ones gets the forward tier when
    she lifts, its own below, and past every documented span (deeper or thrown back) she keeps her filmed mouth."""
    d = tmp_path / "poses"
    d.mkdir()
    (d / "inventory.json").write_text(json.dumps({"clips": {
        "fwd": {"role": "oneshot", "file": "clip-fwd.mp4", "tier": True, "library": "hub"},
        "hub": {"role": "loop", "file": "clip-hub.mp4"},
        "wrk": {"role": "oneshot", "file": "clip-wrk.mp4", "tier": True},
        "loop": {"role": "flourish", "file": "clip-loop.mp4"}}}))
    _clip(d, "fwd", [-1.0, -3.0], [0.0, 0.0], {"forward/0/rest": "t/fwd_f0000_forward_0.png", "forward/0/AH": "t/fwd_f0001_forward_0.png"})
    _clip(d, "wrk", [-13.0, -15.0], [0.0, 0.0], {"desk/0/rest": "t/wrk_f0000_desk_0.png", "desk/0/AH": "t/wrk_f0001_desk_0.png"})
    _clip(d, "hub", [-4.0, -4.0], [0.0, 0.0])                         # her rest -4: the floor is -29
    pitch = [-2.0] * 4 + [-14.0] * 4 + [-25.0] * 4 + [-35.0] * 4 + [20.0] * 4
    _clip(d, "loop", pitch, [0.0] * 20, {"desk/0/rest": "t/wrk_f0000_desk_0.png", "desk/0/AH": "t/wrk_f0001_desk_0.png"})
    tiers, out = _mod().tier_map(str(d), 10.0, 3)
    assert set(tiers) == {"fwd", "wrk"}
    assert out["loop"] == ["fwd"] * 4 + [None] * 4 + ["wrk"] * 4 + ["filmed"] * 8      # carried to the floor, none past it
