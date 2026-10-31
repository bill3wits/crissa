"""The mouth is data now. These pin that it is the SAME data.

Moving MOUTH_FRAC and friends out of lib/pose_mouth.py and into
avatars/<name>/manifest.json is only safe if the numbers did not move with
them. The hand-measured values below are the historical literals, from
hub-up.png on 2026-08-29; they are the regression net for the move.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

# The literals as they stood in lib/pose_mouth.py before 2026-09-01.
HIST_FACE_UP = (287, 104, 180, 180)
HIST_BOX_UP = (351, 238, 54, 20)
HIST_DRIFT_PX = 4.0
HIST_OPEN_PX = 8
HIST_ZFRAME = {"cx": 0.0, "cy": 0.0, "cw": 0.675, "ch": 0.700}


def test_manifest_reproduces_the_hand_measured_face():
    import pose_mouth as pm
    assert tuple(pm.MOUTH_FACE_UP) == HIST_FACE_UP
    assert tuple(pm.MOUTH_BOX_UP) == HIST_BOX_UP
    assert pm.MOUTH_DRIFT_PX == HIST_DRIFT_PX
    assert pm.MOUTH_OPEN_PX == HIST_OPEN_PX
    assert pm.ZFRAME == HIST_ZFRAME


def test_mouth_frac_is_derived_not_stored():
    """A hand-edited convenience value must never beat the measured boxes."""
    import avatar
    fx, fy, fw, fh = HIST_FACE_UP
    mx, my, mw, mh = HIST_BOX_UP
    want = ((mx - fx) / fw, (my - fy) / fh, mw / fw, mh / fh)
    got = avatar.mouth_frac()
    assert all(abs(a - b) < 1e-9 for a, b in zip(got, want))


def test_a_missing_avatar_raises_rather_than_falling_back():
    """The bug being replaced was a second avatar silently wearing the first
    one's face. A default-on-missing would reintroduce it."""
    import avatar
    avatar.load.cache_clear()
    with pytest.raises(FileNotFoundError):
        avatar.load("no-such-avatar-here")


def test_the_paste_ramp_refits_itself_to_a_different_face(tmp_path, monkeypatch):
    """FORMULAS IN CODE, MEASUREMENTS IN DATA — the whole point of the move.

    w2l's paste band is written in mouth-heights off MOUTH_FRAC, so a face with
    a mouth somewhere else must move the band with no code change. If this ever
    fails, someone has re-hardcoded the geometry.
    """
    import avatar
    src = json.loads((ROOT / "avatars" / "crissa" / "manifest.json").read_text())
    # a taller mouth, sitting higher in the face
    src["face"]["mouth_box_up"] = [351, 200, 54, 40]
    d = tmp_path / "other"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(src))

    avatar.load.cache_clear()
    monkeypatch.setenv("HOUSE_AVATARS_DIR", str(tmp_path))
    monkeypatch.setenv("HOUSE_AVATAR", "other")
    for m in ("pose_mouth", "w2l"):
        sys.modules.pop(m, None)
    import w2l

    top = 96 * w2l.MOUTH_FRAC[1]
    h = 96 * w2l.MOUTH_FRAC[3]
    assert abs(w2l._M_TOP96 - top) < 1e-6
    assert abs(w2l._M_H96 - h) < 1e-6
    assert abs(w2l.PASTE_HI - (top - w2l.RAMP_ABOVE * h)) < 1e-6
    # and it must actually differ from Crissa's band, or the test proves nothing
    assert abs(w2l.PASTE_HI - 71.5) > 1.0

    avatar.load.cache_clear()
    for m in ("pose_mouth", "w2l"):
        sys.modules.pop(m, None)
