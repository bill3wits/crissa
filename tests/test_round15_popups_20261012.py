"""Round 15 (.101, the User 2026-10-12: "format update to popups, more like menu maybe"): every Crissa pop-up on the
desk Wall is drawn on the Wall itself in the menu's muted panel, and the browser page asks its confirms inside its menu.
The Tk half runs on a private virtual display only:
    xvfb-run -a env CRISSA_PANELS_XVFB=1 python3 -m pytest tests/test_round15_popups_20261012.py"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_wall_key as K  # noqa: E402

XVFB = bool(os.environ.get("CRISSA_PANELS_XVFB"))
needs_x = pytest.mark.skipif(not XVFB, reason="needs CRISSA_PANELS_XVFB=1 under xvfb-run (never the User's screen)")
PAGE = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")


# ---- pure ----------------------------------------------------------------------------------------------------------
def test_a_pop_up_goes_above_the_key_or_across_the_work_area():
    assert K.panel_place("key", (0, 0, 1920, 1080), (300, 200), key_xy=(16, 1040)) == (16, 834)
    assert K.panel_place("key", (0, 0, 1920, 1080), (300, 2000), key_xy=(16, 1040)) == (16, 0)    # never above the top
    assert K.panel_place("center", (0, 32, 1920, 1040), (400, 300)) == (760, 32 + 246)


def test_pop_ups_are_on_the_wall_unless_switched_off(monkeypatch):
    monkeypatch.delenv("CRISSA_CRISSA_PANELS_ON_WALL", raising=False)
    assert K.panels_on() is True
    monkeypatch.setenv("CRISSA_CRISSA_PANELS_ON_WALL", "0")
    assert K.panels_on() is False


def test_the_walls_keys_go_to_the_open_pop_up_first():
    seen = []
    K._S["panel"] = {"close": lambda: seen.append("close"), "keys": {"return": lambda: seen.append("ok")}}
    try:
        assert K.panel_key("return") is True and K.panel_key("minus") is False and K.panel_key("escape") is True
        assert seen == ["ok", "close"]
    finally:
        K._S["panel"] = None
    assert K.panel_key("escape") is False                        # nothing open: the key is the Wall's


def test_the_wall_lets_the_whole_glass_take_clicks_while_one_is_open():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'if ui["menu"] or (crissa_key.get("panel_open") and crissa_key["panel_open"]()):' in src
    assert 'close=lambda: _close_wall("Close the Wall"), canvas=cv,' in src      # round 16 adds the keyboard hooks


def test_no_crissa_pop_up_is_a_window_of_its_own_by_default():
    src = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
    for fn in ("def _dialog(", "def lip_sync_dialog(", "def voice_id_verbal_dialog(", "def menu_panel("):
        body = src[src.index(fn):]
        body = body[:body.index("\ndef ", 5)]
        assert "if not panels_on():" in body and "tk.Toplevel(" not in body, fn
        assert "focus_force" not in body, fn                    # never takes the focus from another window


def test_the_browser_menu_asks_inside_itself():
    assert "confirm(" not in PAGE.replace("menuConfirm(", "").replace("confirm: true", "")
    assert 'menuConfirm(box, "All OS Off"' in PAGE and 'menuConfirm(box, "Close Agent"' in PAGE
    assert ".cmenu .lip-row button { display: inline-block; width: auto; flex: 1; text-align: center; border: 1px solid #242A37;" in PAGE


# ---- Tk, on a private virtual display ------------------------------------------------------------------------------------
@pytest.fixture
def root():
    import tkinter as tk
    r = tk.Tk()
    r.geometry("1280x720+0+0")
    r.configure(bg=K.GROUND)
    K._S.update(panel=None, menu=None, wa=(0, 0, 1280, 720), on_box=None)
    r.update()
    yield r
    K._S.update(panel=None, menu=None, on_box=None)
    r.destroy()


def _pump(r, s=0.2):
    end = time.time() + s
    while time.time() < end:
        r.update()
        time.sleep(0.02)


def _toplevels(r):
    return [w for w in r.winfo_children() if w.winfo_class() == "Toplevel"]


@needs_x
def test_a_note_is_drawn_on_the_wall_and_escape_closes_it(root, monkeypatch):
    monkeypatch.delenv("CRISSA_CRISSA_PANELS_ON_WALL", raising=False)
    boxes = []
    K._S["on_box"] = lambda: boxes.append(K.panel_box())
    ran = []
    fr = K._dialog(root, "All OS Off", ["All OS Off? Everything of Crissa stops."], [("Cancel", None),
                                                                                     ("All OS Off", lambda: ran.append(1))])
    _pump(root)
    assert fr.winfo_class() == "Frame" and fr.master is root and not _toplevels(root)
    assert K.panel_open() and boxes and boxes[-1] is not None
    x0, y0, x1, y1 = K.panel_box()
    assert 0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 720
    assert K.panel_key("return") and ran == [1] and not K.panel_open()        # the default is the confirm
    K._dialog(root, "Help", ["one line"], [("Close", None)])
    _pump(root)
    assert K.panel_key("escape") and not K.panel_open() and boxes[-1] is None


@needs_x
def test_lip_sync_on_the_wall_steps_and_shows_the_answer(root):
    posts = []

    def post(body):
        posts.append(body)
        return {"ok": True, "lip_sync": {"value": "+1 half frame (+20.8 ms)", "halves": 1, "min_halves": -3,
                                         "max_halves": 3, "range": "-3 to +3"}}
    fr = K.lip_sync_dialog(root, "http://127.0.0.1:9", {"value": "0", "halves": 0, "min_halves": -3, "max_halves": 3},
                           post=post, speak=lambda t: {"ok": True})
    _pump(root)
    assert fr.winfo_class() == "Frame" and not _toplevels(root)
    assert K.panel_key("plus")
    _pump(root, 0.6)
    assert posts == [{"step": 1, "action": "lip_sync"}]
    texts = []

    def walk(w):
        for c in w.winfo_children():
            if c.winfo_class() == "Label":
                texts.append(c.cget("text"))
            walk(c)
    walk(fr)
    assert "Now: +1 half frame (+20.8 ms)" in texts and "-  later" in texts and "Done" in texts
    assert K.panel_key("escape") and not K.panel_open()


@needs_x
def test_the_voice_id_password_box_is_on_the_wall(root):
    posts = []
    vi = {"ok": False, "password": True, "name": "user"}
    K.voice_id_verbal_dialog(root, "http://127.0.0.1:9", vi, say=lambda *a: None, post=lambda b: posts.append(b) or {"ok": True})
    _pump(root)
    assert not _toplevels(root) and K.panel_open()
    entries = []

    def walk(w):
        for c in w.winfo_children():
            if c.winfo_class() == "Entry":
                entries.append(c)
            walk(c)
    walk(K._S["panel"]["frame"])
    assert len(entries) == 1 and entries[0].cget("show") == "•"
    entries[0].insert(0, "secret")
    assert K.panel_key("return")
    _pump(root, 0.6)
    assert not K.panel_open() and posts and posts[0]["password"] == "secret"


@needs_x
def test_the_menu_is_on_the_wall_above_the_key_and_a_click_off_it_closes_it(root):
    import tkinter as tk
    key = tk.Label(root, text=K.KEY_TEXT)
    key.place(x=16, y=680)
    _pump(root)
    did = []
    K.menu_panel(root, key, K.menu_items({}, False), lambda v: did.append(v))
    _pump(root)
    assert not _toplevels(root) and K.panel_open()
    x0, y0, x1, y1 = K.panel_box()
    assert y1 <= 680 and x0 == 16

    class Ev:
        x_root, y_root = root.winfo_rootx() + 900, root.winfo_rooty() + 100
    assert K.gate_click(Ev) == "break" and not K.panel_open() and K._S.get("menu") is None


@needs_x
def test_switched_off_a_pop_up_is_its_own_window_again(root, monkeypatch):
    monkeypatch.setenv("CRISSA_CRISSA_PANELS_ON_WALL", "0")
    K._dialog(root, "Help", ["one line"], [("Close", None)])
    _pump(root)
    assert _toplevels(root) and not K.panel_open()
