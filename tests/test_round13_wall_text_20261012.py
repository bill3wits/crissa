"""Round 13 (.101, 2026-10-12): the log column stays at the left edge, and her lines carry her name, not her key."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _roll(R, lines, now=100.0):
    roll = R.Roll(since=0.0)
    roll.feed([{"who": w, "text": t, "ts": 1.0 + i} for i, (w, t) in enumerate(lines)], now=now)
    return roll


def _geom(R, monkeypatch, W=3440, H=1440):
    monkeypatch.setenv("HOUSE_WALL_U_FIT", "1")
    return R.Geom(W, H)


def _line_x0(R, g, roll, now, prefs=None):
    lay = roll.draw(g, now=now, prefs=prefs)
    bb = lay.getbbox()
    return None if bb is None else bb[0]


@pytest.mark.parametrize("W,H", [(3440, 1440), (2560, 1440)])
def test_every_line_is_at_the_left_edge_and_runs_under_the_dial(monkeypatch, W, H):
    """round 14 (the User 2026-10-12, final): "Scoot it to left justify", "better under dials than over ascii" -
    round 13's glide is gone; a pack's roll is the house's: the left edge, behind the dial (the HUD's layer is above)"""
    import house_wall_ascii_render as R
    g = _geom(R, monkeypatch, W, H)
    assert R.ROLL_CLEAR_DIAL is False or R._roll_clear_dial() is False
    monkeypatch.setattr(R, "ROLL_CLEAR_DIAL", False)
    dial, _ = g.oracle_box()
    roll = _roll(R, [("crissa", "a line at the floor, beside the dial")], now=10.0)
    roll.lines[0]["at"] = 10.0
    assert _line_x0(R, g, roll, 10.0) < g.page_left + 0.5 * g.u
    t_up = 10.0 + (dial + 6 * g.u) / (g.H / R.ROLL_CROSS_S)
    assert _line_x0(R, g, roll, t_up) < g.page_left + 0.5 * g.u


def test_the_10_10_rule_is_opt_in(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.setenv("HOUSE_WALL_ROLL_CLEAR_DIAL", "1")
    assert R._roll_clear_dial() is True
    monkeypatch.setenv("HOUSE_WALL_ROLL_CLEAR_DIAL", "")
    assert R._roll_clear_dial() is False
    g = _geom(R, monkeypatch)
    monkeypatch.setattr(R, "ROLL_CLEAR_DIAL", True)
    dial, _ = g.oracle_box()
    roll = _roll(R, [("crissa", "a line")], now=10.0)
    roll.lines[0]["at"] = 10.0
    assert _line_x0(R, g, roll, 10.0) >= g.page_left + dial


def test_the_house_roll_never_clears_the_dial(monkeypatch):
    import house_wall_ascii_render as R
    g = _geom(R, monkeypatch)
    monkeypatch.setattr(R, "ROLL_CLEAR_DIAL", False)
    roll = _roll(R, [("crissa", "a line at the floor")], now=10.0)
    roll.lines[0]["at"] = 10.0
    assert _line_x0(R, g, roll, 10.0) < g.page_left + 0.5 * g.u


def test_her_lines_carry_her_name_and_the_wire_keeps_the_key(monkeypatch):
    import house_wall_ascii_render as R
    import crissa_home
    R._her_label.cache_clear()
    monkeypatch.setattr(crissa_home, "her_name", lambda paths=None: "Crissa")
    try:
        assert R.roll_label("crissa") == "CRISSA"
        assert R.roll_label("lila") == "LILA" and R.roll_label("stranger") == "STRANGER" and R.roll_label("") == ""
        roll = _roll(R, [("crissa", "hello")])
        assert roll.lines[0]["who"] == "crissa"                 # the key is untouched for every reader
    finally:
        R._her_label.cache_clear()


def test_on_the_house_her_label_is_what_it_was(monkeypatch):
    import house_wall_ascii_render as R
    import crissa_home
    R._her_label.cache_clear()
    monkeypatch.setattr(crissa_home, "her_name", lambda paths=None: "Crissa")    # the house's config says its own name
    try:
        assert R.roll_label("crissa") == "crissa".upper()
    finally:
        R._her_label.cache_clear()


def test_the_roll_does_not_read_the_film_geometry():
    """the AR width and the frame zoom cannot move the log column: the roll's draw takes neither"""
    import inspect
    import house_wall_ascii_render as R
    src = inspect.getsource(R.Roll.draw)
    for name in ("hole_zoom", "frame_zoom", "fov", "crop_box", "wall_frame", "shift"):
        assert name not in src, name
