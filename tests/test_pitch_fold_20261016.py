"""The pitch fold (the User 2026-10-16): a forward frame tilted past -22 wears the face's desk tiles, back above -21."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import mouth_playlist as MPL  # noqa: E402

POSE = ROOT / "display" / "crissa-poses"


def test_band_holds_at_the_edge():
    seq = [-18, -21.5, -23, -21.5, -21.2, -20.9, -21.5, -22.1]
    out, f = [], False
    for p in seq:
        f = MPL.pitch_fold(f, p)
        out.append(f)
    assert out == [False, False, True, True, True, False, False, True]


def test_no_pitch_keeps_the_last_answer():
    assert MPL.pitch_fold(True, None) is True
    assert MPL.pitch_fold(False, None) is False


def test_defaults_and_kill(monkeypatch):
    assert MPL.PITCH_FOLD is True and MPL.PITCH_FOLD_IN == -22.0 and MPL.PITCH_FOLD_OUT == -21.0
    assert MPL.PITCH_FOLD_FROM == "forward" and MPL.PITCH_FOLD_TO == "desk"
    src = (ROOT / "lib" / "mouth_playlist.py").read_text()
    assert 'os.environ.get("HOUSE_PITCH_FOLD", "1")' in src   # on with the skin match (2026-10-16)


def test_station_lookup_reads_the_bin_not_the_rows():
    pl = {"frames": [{"pitch_bin": "forward", "yaw_bin": "0"}],
          "index": {"desk/0/AH": ["a.png", "b.png"], "desk/0/rest": "r.png", "forward/0/AH": "f.png"}}
    assert MPL.station_lookup(pl, "desk", "AH") == ["a.png", "b.png"]
    assert MPL.station_lookup(pl, "desk", "rest-open") == "r.png"
    assert MPL.station_lookup(pl, "desk", "EE") is None
    assert MPL.station_lookup(None, "desk", "AH") is None


def test_listen_back_folds_where_101_measured():
    pl = json.loads((POSE / "clip-listen-back.playlist.json").read_text())
    f, folded = False, []
    for i, r in enumerate(pl["frames"]):
        f = MPL.pitch_fold(f, r.get("pitch"))
        if f:
            folded.append(i)
    assert folded and 200 <= folded[0] <= 210 and 270 <= folded[-1] <= 285, folded
    assert len(folded) >= 71


def test_work_holds_every_class_at_the_desk():
    pl = json.loads((POSE / "clip-work.playlist.json").read_text())
    missing = [c for c in ("rest", "MBP", "FV", "TDN", "EE", "AH", "OH", "WOO") if MPL.station_lookup(pl, "desk", c) is None]
    assert missing == []


def test_painter_wiring():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert "def pitch_fold_source(self, face_key" in src
    i = src.index("# THE PITCH FOLD (the User 2026-10-16)")
    j = src.index('band_moved = band != getattr(self, "_pl_band", None)')
    assert i < j, "the fold sets bins before the rest-follow compare"
    assert "station_lookup(self.lib.playlist(fold_key), fold_to or PITCH_FOLD_TO, vis)" in src  # round 28: the lift
    # a fold with no desk tile falls back to the loop's own
    assert re.search(r"if self\._pl_hit is None:\n\s+fold_key = None\n\s+self\._pl_hit = playlist_lookup\(pl", src)
    # the fold never runs under a turn band
    assert "if PITCH_FOLD and not band and _frs and bins and bins[0] in (" in src
    assert "(PITCH_FOLD_FROM, PITCH_FOLD_TO) if PITCH_LIFT else (PITCH_FOLD_FROM,)):" in src, "no lift: forward frames only"


def test_folded_tile_takes_her_skin_light_and_keeps_its_lips():
    import numpy as np
    import mouth_pins as MP
    H, W = 60, 80
    base = np.full((H, W, 3), (120, 140, 190), np.uint8)            # her skin (BGR)
    tile = np.full((H, W, 3), (100, 160, 180), np.uint8)            # the desk take: greener, warmer
    lips = np.zeros((H, W), np.uint8)
    lips[25:35, 25:55] = 255
    tile[25:35, 25:55] = (90, 80, 200)                              # its red lips
    alpha = np.ones((H, W), np.float32)
    out = MP.match_skin(tile, base, alpha, lips, sigma_px=6.0).astype(float)
    skin = out[5:15, 5:20].reshape(-1, 3).mean(0)
    assert np.abs(skin - (120, 140, 190)).max() < 3
    lip = out[28:32, 35:45].reshape(-1, 3).mean(0)
    assert lip[2] - lip[1] > 60                                     # still red against green


def test_skin_match_wiring():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'PITCH_FOLD_SKIN = os.environ.get("HOUSE_PITCH_FOLD_SKIN", "1")' in src
    assert "elif PITCH_FOLD_SKIN and getattr(self, \"_pl_fold\", False) and a_cut is not None:" in src
    assert "self._pl_fold = bool(fold_key)" in src
    assert "def _fold_lips(self, hw)" in src


def test_roi_match_equals_the_full_frame_inside_the_tile():
    import numpy as np
    import mouth_pins as MP
    rng = np.random.default_rng(3)
    H, W = 200, 300
    base = rng.integers(60, 200, (H, W, 3)).astype(np.uint8)
    tile = rng.integers(60, 200, (H, W, 3)).astype(np.uint8)
    a = np.zeros((H, W), np.float32)
    a[80:130, 100:180] = 1
    lips = np.zeros((H, W), np.uint8)
    lips[100:110, 120:160] = 255
    full = MP.match_skin(tile, base, a, lips, sigma_px=6.0)
    roi = MP.match_skin_roi(tile, base, a, lips, sigma_px=6.0)
    inside = a > 0
    assert np.abs(full[inside].astype(int) - roi[inside].astype(int)).max() <= 1
