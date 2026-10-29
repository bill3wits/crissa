"""Round 28 (.101 2026-10-17, FIXES-20261017-from-101, built in as-is): the settings .101 ran become a pack's defaults,
and the house keeps its own until it sets them; the face window's drag no longer empties its own state."""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text()
SPEAK = (ROOT / "scripts" / "house-speak.py").read_text()
KEYS = ("HOUSE_PITCH_FOLD_HOLD_S", "HOUSE_TURN_BAND_HOLD", "HOUSE_PITCH_LIFT", "HOUSE_PITCH_FOLD_REPICK",
        "HOUSE_PITCH_FOLD_IN", "HOUSE_PITCH_FOLD_OUT", "HOUSE_SPEAK_STAMP_HOLD")


def _conductor_consts(house: bool) -> dict:
    lines = [ln for ln in CONDUCTOR.splitlines()
             if re.match(r"^(PITCH_FOLD_HOLD_S|TURN_BAND_HOLD|PITCH_LIFT|PITCH_FOLD_REPICK) = ", ln)]
    assert len(lines) == 4, lines
    ns = {"os": os, "house_default": (lambda h, p: h if house else p)}
    exec("\n".join(lines), ns)
    return ns


def _clean(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_a_pack_runs_what_101_ran(monkeypatch):
    _clean(monkeypatch)
    ns = _conductor_consts(house=False)
    assert ns["PITCH_FOLD_HOLD_S"] == 0.0 and ns["TURN_BAND_HOLD"] == 0.0   # round 29: no hold in a pack either
    assert ns["PITCH_LIFT"] is True and ns["PITCH_FOLD_REPICK"] is True


def test_the_house_keeps_its_mouths(monkeypatch):
    _clean(monkeypatch)
    ns = _conductor_consts(house=True)
    assert ns["PITCH_FOLD_HOLD_S"] == 0.0 and ns["TURN_BAND_HOLD"] == 0.0
    assert ns["PITCH_LIFT"] is False and ns["PITCH_FOLD_REPICK"] is False
    monkeypatch.setenv("HOUSE_PITCH_LIFT", "1")
    assert _conductor_consts(house=True)["PITCH_LIFT"] is True, "an env still wins"


def _fold(monkeypatch, house: bool):
    import crissa_home
    import mouth_playlist
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: house)
    try:
        m = importlib.reload(mouth_playlist)
        return m.PITCH_FOLD_IN, m.PITCH_FOLD_OUT
    finally:
        monkeypatch.undo()
        importlib.reload(mouth_playlist)


def test_a_pack_folds_at_one_angle(monkeypatch):
    _clean(monkeypatch)
    assert _fold(monkeypatch, house=False) == (-13.0, -13.0)   # round 29: -13 on the raw tilt


def test_the_house_folds_where_it_did(monkeypatch):
    _clean(monkeypatch)
    assert _fold(monkeypatch, house=True) == (-22.0, -21.0)


def test_one_angle_still_folds_and_unfolds():
    import mouth_playlist as M
    assert M.pitch_fold(False, -18.0, -17, -17) is True
    assert M.pitch_fold(True, -16.0, -17, -17) is False
    assert M.pitch_fold(True, None, -17, -17) is True, "no pitch keeps the last answer"


def test_no_stamp_hold_by_default():
    assert 'os.environ.get("HOUSE_SPEAK_STAMP_HOLD", "0")' in SPEAK
    assert 'os.environ.get("HOUSE_SPEAK_STAMP_HOLD", "first")' not in SPEAK


def test_a_drag_copies_its_state():
    src = (ROOT / "lib" / "crissa_window.py").read_text()
    assert "m = dict(_MOVE) if" in src and "m = _MOVE if" not in src
