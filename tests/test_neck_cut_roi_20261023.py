"""The neck cut on the patch's own box (2026-10-23, the v2 desk bench at 1280x720): the chin and jaw cuts made whole-frame
passes every paint (c_alpha_blend 5 -> 29 ms, 62 % of the mouth steps skipped). They only multiply the patch's alpha, so
they run on its live box grown by the feather's reach - the same pixels (measured on two benches, 12 held frames, 0 px
apart), 29 -> 10.5 ms. Kill HOUSE_MOUTH_CUT_ROI=0."""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _chin_keep():
    body = SRC.split("    def _chin_keep(", 1)[1].split("\n    def ", 1)[0]
    lines = ("def _chin_keep(" + body).splitlines()
    code = "\n".join(l[4:] if l.startswith("    ") else l for l in lines)
    ns = {"np": np, "paint_wh": lambda: (320, 180)}
    exec(code, ns)
    return ns["_chin_keep"]


class _Fake:
    i = 7

    def _turn_cfg(self):
        return {"chin_cut": {"lip_half": 0.5, "chin_half": 0.22, "fade": 0.06}}

    def _tri_M(self, i, rel):
        # anchors: [.., .., lip, nose, chin] in paint pixels, head size s
        return {"da": [None, None, (160.0, 100.0), (161.0, 70.0), (159.0, 140.0)], "s": 90.0}


def test_the_taper_on_a_box_is_the_whole_frames_taper_cut_out():
    ck = _chin_keep()
    full = ck(_Fake(), "t.png", {})
    rb = (60, 170, 90, 240)
    box = ck(_Fake(), "t.png", {}, rb)
    assert full.shape == (180, 320) and box.shape == (110, 150)
    assert np.array_equal(full[60:170, 90:240], box)


def test_the_cuts_run_on_the_box_and_have_a_kill():
    assert 'MOUTH_CUT_ROI = os.environ.get("HOUSE_MOUTH_CUT_ROI", "1")' in SRC
    paste = SRC.split("_fk = self._front_keep(rel)", 1)[1].split("if tri is not None and a_cut is not None:", 1)[0]
    assert "live_bbox(np.asarray(a_cut) > 0, pad=_pad)" in paste
    assert 'int(np.ceil(4.0 * float(_fk[2]["fe"]))) + 2' in paste       # past the jaw feather's blur
    assert "_a0 = _afull[_R].copy()" in paste                            # the uncut alpha, not a view of the cut one
    assert "maps[0][_R]" in paste and "_Mr[0, 2] -= _ox" in paste and "_al - np.array([_ox, _oy]" in paste
