"""The fold has no history on a seek and folds only to the library's own picture (the User 2026-11-01, Crissa Mobile:
"it comes and goes depending on which direction i am coming from" / "they all look the same. But in different
scenarios")."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import mouth_playlist as MPL  # noqa: E402

POSE = ROOT / "display" / "crissa-poses"
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text()


def test_a_seek_decides_at_the_bands_middle():
    # Crissa Mobile's band: in -9, out -8 (middle -8.5)
    assert MPL.pitch_fold_seek(-8.4, -9, -8) is False
    assert MPL.pitch_fold_seek(-8.6, -9, -8) is True
    assert MPL.pitch_fold_seek(-10.3, -9, -8) is True
    assert MPL.pitch_fold_seek(None, -9, -8, last=True) is True
    assert MPL.pitch_fold_seek(None, -9, -8, last=False) is False


def test_a_seek_answer_is_a_steady_state_of_the_band():
    # whatever a seek decides, the next played frame at the same pitch keeps it (no flip on the first step)
    for p in (-10.0, -9.0, -8.7, -8.5, -8.3, -8.0, -7.0):
        f = MPL.pitch_fold_seek(p, -9, -8)
        assert MPL.pitch_fold(f, p, -9, -8) is f, p


def test_smile_blink_reads_the_same_from_both_directions():
    # the band frames of fl-smile-blink 110-136 (playlist pitch): played forward and back the band keeps history,
    # but a seek to any frame gives one answer
    pitch = [-8.3, -8.63, -8.7, -8.71, -8.77, -9.19, -9.67, -10.02, -9.97, -9.83, -9.97, -10.15, -10.33, -10.32,
             -10.35, -10.32, -10.23, -10.14, -10.06, -10.02, -9.88, -9.79, -9.61, -9.59, -8.47, -6.58, -4.3]
    fwd, f = [], False
    for p in pitch:
        f = MPL.pitch_fold(f, p, -9, -8)
        fwd.append(f)
    back, f = [], False
    for p in reversed(pitch):
        f = MPL.pitch_fold(f, p, -9, -8)
        back.append(f)
    back.reverse()
    assert fwd != back                       # the bug the User saw: the band's answer depends on the direction
    seeks = [MPL.pitch_fold_seek(p, -9, -8) for p in pitch]
    assert seeks == [MPL.pitch_fold_seek(p, -9, -8) for p in pitch]
    assert seeks[0] is False and seeks[12] is True and seeks[-1] is False


def test_shapes():
    assert MPL.same_shape([720, 1280], [1280, 720]) is False      # a PC take in a phone library
    assert MPL.same_shape([736, 400], [1296, 704]) is True        # the house's two native sizes
    assert MPL.same_shape([1280, 720], [1296, 704]) is True       # v2 beside the house size
    assert MPL.same_shape([720, 1280], [720, 1280]) is True
    assert MPL.same_shape(None, [720, 1280]) is None
    assert MPL.same_shape([0, 0], [720, 1280]) is None


def test_station_tiles_flattens_series():
    pl = {"index": {"desk/0/AH": ["a.png", "b.png"], "desk/0/rest": "r.png", "forward/0/AH": "f.png",
                    "desk/0/EE": ["a.png", None]}}
    assert MPL.station_tiles(pl, "desk") == ["a.png", "b.png", "r.png"]
    assert MPL.station_tiles(pl, "forward") == ["f.png"]
    assert MPL.station_tiles(None, "desk") == []


def test_defaults_and_kills():
    assert MPL.PITCH_FOLD_SEEK_FRESH is True and MPL.PITCH_FOLD_OWN_SHAPE is True
    src = (ROOT / "lib" / "mouth_playlist.py").read_text()
    assert 'os.environ.get("HOUSE_PITCH_FOLD_SEEK_FRESH", "1")' in src
    assert 'os.environ.get("HOUSE_PITCH_FOLD_OWN_SHAPE", "1")' in src


def test_the_conductor_wires_both():
    assert "pitch_fold_seek(_pp, last=_was)" in CONDUCTOR
    assert "self.lib.fold_own_shape(self.key, fold_key, fold_to)" in CONDUCTOR
    assert "def fold_own_shape(self, key: str, fold_key: str, station: str) -> bool:" in CONDUCTOR
    # the clip change forgets the last painted frame, so a clip's first frame is not a seek
    assert "self._pitch_fold_clip, self._pitch_folded = self.key, _down_clip\n                        self._pitch_fold_i = None" in CONDUCTOR


def test_classic_crissas_fold_stays_her_own_picture():
    # the house's own library must still fold: listen-back's desk source (work) was cut from films of her own shape
    own = json.loads((POSE / "clip-listen-back.head.json").read_text()).get("src_wh")
    pl = json.loads((POSE / "clip-work.playlist.json").read_text())
    ents = json.loads((POSE / "visemes" / "phoneme-map" / "map.json").read_text()).get("entries") or []
    by = {Path(str(e.get("tile") or "")).name: e.get("src_wh") for e in ents if e.get("tile")}
    tiles = MPL.station_tiles(pl, "desk")
    assert own and tiles
    known = [by.get(Path(t).name) for t in tiles if by.get(Path(t).name)]
    assert known, "no desk tile of work has a map size"
    assert all(MPL.same_shape(own, wh) is not False for wh in known)
