"""Round 15 (2026-10-12, .101 fresh install): her Setup window stays in front of the desk Wall.

The User: "i am trapped in init screen on .101. nothing will front the Wallpaper so no init screen for setup. I did see
the brave tile flash at the top but it was hidden", and "they all show on the menu but wont front the wallpaper when
clicked". Cause: the desk Wall set itself -topmost and lifted itself every half second while Init ran (the house's
2026-09-12 rule, from when the Wall drew the whole walk); a pack's Setup has run in HER window since 2026-10-11, so the
topmost Wall covered it and nothing could come above it. Now a pack whose Setup runs in her window never puts the Wall
on top; the house's Init (drawn on its Wall) and a pack with no browser of her family keep the old rule.
house_wall_ascii itself is read from its source here (its own test file is out of runs while a leak is traced).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_home  # noqa: E402
import crissa_window as W  # noqa: E402


def test_a_pack_whose_setup_runs_in_her_window_never_puts_the_wall_on_top():
    assert W.wall_on_top_for_setup("running", house=False, in_window=True, env={}) is False
    assert W.wall_on_top_for_setup("done", house=False, in_window=True, env={}) is False


def test_the_house_and_a_pack_without_her_window_keep_the_old_rule():
    assert W.wall_on_top_for_setup("running", house=True, in_window=True, env={}) is True, "the house draws its Init"
    assert W.wall_on_top_for_setup("running", house=False, in_window=False, env={}) is True, "Firefox only: the plates"
    assert W.wall_on_top_for_setup("idle", house=True, in_window=False, env={}) is False
    assert W.wall_on_top_for_setup("running", house=False, in_window=True, env={W.WALL_TOP_SWITCH: "1"}) is True


def test_setup_runs_in_her_window_where_a_browser_of_her_family_is_found(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert W.setup_in_own_window({}, which={"brave-browser": "/usr/bin/brave-browser"}.get, plat="linux") is True
    assert W.setup_in_own_window({}, which={"firefox": "/usr/bin/firefox"}.get, plat="linux") is False
    assert W.setup_in_own_window({W.SWITCH: "0"}, which={"brave-browser": "/usr/bin/brave-browser"}.get,
                                 plat="linux") is False
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert W.setup_in_own_window({}, which={"brave-browser": "/usr/bin/brave-browser"}.get, plat="linux") is False


def test_the_walls_wizard_rule_asks_it_in_a_pack_and_is_the_only_root_topmost():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    pump = src.split("THE WALL ON TOP WHILE THE WIZARD RUNS")[1].split("stale = st.stale()")[0]
    assert "if want and not crissa_home.is_house():" in pump
    assert "crissa_window.wall_on_top_for_setup(" in pump and "crissa_window.setup_in_own_window()" in pump
    assert "the wall stays at the wallpaper level, under it" in pump
    assert src.count('root.attributes("-topmost"') == 1, "the wizard rule is the only place the Wall goes on top"
    assert "overrideredirect" not in src, "the Wall's own window is never override-redirect"
