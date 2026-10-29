"""The per-class prep table in the sheet (2026-09-25).

The User: "build the per-class prep table into the sheet and then compare to the STANDARDIZED
padding it comes up with for the natural speech ones." Measured on 136 word-first shapes across
nine hand-checked keys: a first shape forms ~1 f before its sound; closures 1 f, hisses 2 f, the
open shapes on the sound; a pause lengthens the tail. One number per class, not per take.
"""
from __future__ import annotations
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import mouth_timing as mt  # noqa: E402

R = mt.REST


def _show(seq):
    return "".join("." if c == R else c[0] for c in seq)


def test_the_table_pads_a_words_first_shape_by_its_class_into_rest_only():
    seq = [R] * 10 + ["MBP"] * 4 + ["AH"] * 4 + [R] * 6 + ["SH/CH"] * 3 + [R] * 5
    table = {"MBP": 2, "SH/CH": 4}
    out = mt.class_prep(seq, [(10, 18), (24, 27)], table, 0, 8)
    assert _show(out[8:18]) == "MMMMMMAAAA", "MBP took 2 hops of the silence in front"
    assert _show(out[20:27]) == "SSSSSSS", "SH/CH took 4"
    assert out[18:20] == [R, R], "the rest between the words it did not need stays"
    assert seq[10] == "MBP" and _show(seq[8:10]) == "..", "the input is not touched"


def test_a_class_not_in_the_table_and_a_scheduled_hop_are_left_alone():
    seq = [R] * 4 + ["AH"] * 4 + ["MBP"] * 4
    out = mt.class_prep(seq, [(4, 8), (8, 12)], {"MBP": 4}, 0, 8)
    assert out == seq, "AH has no prep; MBP runs straight on from AH — nothing to take"
    seq2 = [R] * 1 + ["MBP"] * 4
    assert mt.class_prep(seq2, [(1, 5)], {"MBP": 4}, 0, 8) == ["MBP"] * 5, "asked 4, took the 1 there was"


def test_a_pause_adds_the_bonus_and_an_interior_gap_does_not():
    seq = [R] * 12 + ["TH"] * 3 + [R] * 3 + ["TH"] * 3
    out = mt.class_prep(seq, [(12, 15), (18, 21)], {"TH": 2}, 2, sent_gap=8)
    assert _show(out[8:15]) == "TTTTTTT", "sentence-initial: 2 (class) + 2 (pause) = 4 hops"
    assert _show(out[15:21]) == ".TTTTT", "interior gap of 3: class 2 only"


def test_off_by_default_and_the_env_arms_it_with_the_frame_table(monkeypatch):
    for k in ("HOUSE_MOUTH_CLASS_PREP", "HOUSE_MOUTH_CLASS_PREP_TABLE", "HOUSE_MOUTH_CLASS_PREP_PAUSE_F"):
        monkeypatch.delenv(k, raising=False)
    m = importlib.reload(mt)
    assert m.CLASS_PREP_ON is False, "house-speak is exec'd from disk per line: an ON default goes live at commit"
    assert m._class_prep_table()["MBP"] == 1 * m.SLOTS_PER_FRAME and m._class_prep_table()["SH/CH"] == 2 * m.SLOTS_PER_FRAME
    assert m._class_prep_pause_hops() == 1 * m.SLOTS_PER_FRAME
    monkeypatch.setenv("HOUSE_MOUTH_CLASS_PREP", "1")
    monkeypatch.setenv("HOUSE_MOUTH_CLASS_PREP_TABLE", "MBP:3,WOO:0.5")
    monkeypatch.setenv("HOUSE_MOUTH_CLASS_PREP_PAUSE_F", "2")
    m = importlib.reload(mt)
    assert m.CLASS_PREP_ON is True
    t = m._class_prep_table()
    assert t["MBP"] == 3 * m.SLOTS_PER_FRAME and t["WOO"] == 1 and t["TH"] == 1 * m.SLOTS_PER_FRAME
    assert m._class_prep_pause_hops() == 2 * m.SLOTS_PER_FRAME
    src = (ROOT / "lib" / "mouth_timing.py").read_text(encoding="utf-8")
    fn = src.split("def schedule(", 1)[1].split("\n# ---", 1)[0]
    assert fn.index("seq = edge_pad(") < fn.index("if CLASS_PREP_ON:") < fn.index("return smooth("), "after the pads, before the smooth"
    monkeypatch.delenv("HOUSE_MOUTH_CLASS_PREP"); monkeypatch.delenv("HOUSE_MOUTH_CLASS_PREP_TABLE"); monkeypatch.delenv("HOUSE_MOUTH_CLASS_PREP_PAUSE_F")
    importlib.reload(mt)
