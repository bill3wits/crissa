"""Round 16 (.101 2026-10-13 00:52/00:54: the Close Agent box opened "under the User's terminal window"; Return and
Escape could not reach it). Every Crissa pop-up takes the menu's place above the key, a pop-up opened by his click has
the keyboard while it is up (and gives it back), a note that arrives later never takes it. The Tk half runs on a
private virtual display only:
    xvfb-run -a env CRISSA_PANELS_XVFB=1 python3 -m pytest tests/test_round16_confirm_20261013.py"""
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
KEY_SRC = (ROOT / "lib" / "crissa_wall_key.py").read_text(encoding="utf-8")
WALL_SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_state():
    keep = dict(K._S)
    K._S.update(panel=None, menu=None, kbd_fns=None, kbd_on=False, shown_box=None, box=None, log=None)
    yield
    K._S.clear()
    K._S.update(keep)


# ---- pure ----------------------------------------------------------------------------------------------------------
def test_the_switch(monkeypatch):
    monkeypatch.delenv("CRISSA_CRISSA_POPUP_FRONT", raising=False)
    assert K.popup_front() is True
    monkeypatch.setenv("CRISSA_CRISSA_POPUP_FRONT", "0")
    assert K.popup_front() is False


def test_the_key_anchor_holds_while_the_key_is_faded_out():
    K._S.update(box=None, shown_box=(16, 980, 120, 1004))         # gone with the idle chrome: box None
    assert K.key_anchor() == (16, 980)
    K._S.update(shown_box=None, box=(20, 900, 90, 920))
    assert K.key_anchor() == (20, 900)
    K._S.update(box=None)
    assert K.key_anchor() is None


def test_the_keyboard_is_asked_once_and_given_back_once():
    seen = []
    K._S["kbd_fns"] = (lambda: seen.append("grant"), lambda: seen.append("release"))
    K._kbd(True)
    K._kbd(True)
    K._S["panel"] = {"kbd": True}
    K._kbd_settle()                                                 # a pop-up that wants it replaced the last
    assert seen == ["grant"]
    K._S["panel"] = {"kbd": False}                                  # a note: it gives the keyboard back
    K._kbd_settle()
    K._kbd(False)
    assert seen == ["grant", "release"]


def test_a_failing_grant_is_logged_and_the_pop_up_still_works():
    logs = []

    def boom():
        raise OSError("no display")
    K._S.update(kbd_fns=(boom, lambda: None), log=logs.append)
    K._kbd(True)
    assert logs and "keyboard grant failed" in logs[0]


def test_a_note_from_a_request_never_takes_the_keyboard():
    body = KEY_SRC[KEY_SRC.index("    def say(title, text):"):]
    body = body[:body.index("\n\n")]
    assert '_dialog(root, title, [text], [("Close", None)], keyboard=False)' in body


def test_the_wall_hands_its_keyboard_hooks_in_wallpaper_mode_only():
    assert "keyboard=(_wall_kbd_grant, _wall_kbd_release) if mode == \"wallpaper\" else None)" in WALL_SRC
    grant = WALL_SRC[WALL_SRC.index("            def _wall_kbd_grant(ts: int = 0):"):WALL_SRC.index("            def _wall_kbd_release():")]
    assert "shape.input_hint(xid_, True)" in grant and "shape.activate(me_, ts)" in grant and "_NET_ACTIVE_WINDOW" in grant
    rel = WALL_SRC[WALL_SRC.index("            def _wall_kbd_release():"):]
    rel = rel[:rel.index("            crissa_wall_key.install(")]
    assert "shape.input_hint(xid_, False)" in rel and "root.lower()" in rel and 'shape.activate(kbd_["prev"])' in rel
    # the wall's own default stays: no keyboard, no focus-raise over the desktop icons
    assert "    def no_focus(self, xid):" in WALL_SRC and "self.input_hint(xid, False)" in WALL_SRC


def test_no_crissa_pop_up_is_a_window_unless_switched_off():
    for fn in ("def _dialog(", "def lip_sync_dialog(", "def voice_id_verbal_dialog(", "def menu_panel("):
        body = KEY_SRC[KEY_SRC.index(fn):]
        body = body[:body.index("\ndef ", 5)]
        assert "if not panels_on():" in body and "tk.Toplevel(" not in body, fn


# ---- Tk, on a private virtual display ------------------------------------------------------------------------------------
def _pump(r, s=0.25):
    end = time.time() + s
    while time.time() < end:
        r.update()
        time.sleep(0.02)


def _labels(w, out=None):
    out = [] if out is None else out
    for c in w.winfo_children():
        if c.winfo_class() == "Label":
            out.append(c)
        _labels(c, out)
    return out


@pytest.fixture
def wall(monkeypatch):
    import tkinter as tk
    monkeypatch.delenv("CRISSA_CRISSA_POPUP_FRONT", raising=False)
    monkeypatch.delenv("CRISSA_CRISSA_PANELS_ON_WALL", raising=False)
    monkeypatch.setenv("CRISSA_CRISSA_KEY_HIDES", "0")
    monkeypatch.setattr(K, "workarea", lambda W, H, run=None: (0, 0, 1280, 720))
    posts, kb, logs = [], [], []
    monkeypatch.setattr(K, "_post", lambda feed, body, timeout=10.0: posts.append(body) or {"ok": True})
    monkeypatch.setattr(K, "_get", lambda feed, timeout=3.0: {})
    r = tk.Tk()
    r.geometry("1280x720+0+0")
    r.configure(bg=K.GROUND)
    key = K.install(r, feed="http://127.0.0.1:9", open_url=lambda u: None, log=logs.append, W=1280, H=720,
                    keyboard=(lambda: kb.append("grant"), lambda: kb.append("release")))
    _pump(r, 0.5)
    yield r, key, posts, kb, logs
    K.close_panel()
    r.destroy()


def _choose(r, label):
    rows = [lb for lb in _labels(K._S["panel"]["frame"]) if lb.cget("text") == label]
    assert rows, (label, [lb.cget("text") for lb in _labels(K._S["panel"]["frame"])])
    rows[0].event_generate("<Button-1>")
    _pump(r)


@needs_x
@pytest.mark.parametrize("item,title,body", [("Close Agent…", "Close Agent", {"action": "agent_close", "by": "the desk Wall"}),
                                             ("All OS Off…", "All OS Off", {"action": "stop", "confirm": True})])
def test_the_confirm_takes_the_menus_place_and_return_confirms(wall, item, title, body):
    r, key, posts, kb, logs = wall
    key.event_generate("<Button-1>")
    _pump(r)
    menu_box = K.panel_box()
    kx, ky = K.key_anchor()
    assert menu_box and menu_box[0] == kx and menu_box[3] <= ky            # the menu, above the key
    _choose(r, item)
    p = K._S["panel"]
    assert p and p["title"] == title and p["frame"].winfo_class() == "Frame" and p["frame"].master is r
    x0, y0, x1, y1 = p["box"]
    assert x0 == kx and y1 <= ky and y0 >= menu_box[1]                     # inside the menu's place, never the middle
    assert kb == ["grant"]                                                  # the menu handed the keyboard on
    assert any(("pop-up %r on the Wall" % title) in ln and "keyboard" in ln for ln in logs)
    r.event_generate("<Return>")                                            # the Wall's own key binding
    _pump(r, 0.6)
    assert posts == [body] and not K.panel_open()
    assert kb == ["grant", "release"]


@needs_x
def test_escape_closes_the_confirm_and_gives_the_keyboard_back(wall):
    r, key, posts, kb, logs = wall
    key.event_generate("<Button-1>")
    _pump(r)
    _choose(r, "Close Agent…")
    assert K._S["panel"]["title"] == "Close Agent"
    r.event_generate("<Escape>")
    _pump(r)
    assert not K.panel_open() and posts == [] and kb == ["grant", "release"]


@needs_x
def test_a_late_note_shows_above_the_key_without_the_keyboard(wall):
    r, key, posts, kb, logs = wall
    K._dialog(r, "Close Agent", ["The launcher did not answer."], [("Close", None)], keyboard=False)
    _pump(r)
    x0, y0, x1, y1 = K.panel_box()
    kx, ky = K.key_anchor()
    assert x0 == kx and y1 <= ky and kb == []


@needs_x
def test_switched_off_it_is_round_15(wall, monkeypatch):
    r, key, posts, kb, logs = wall
    monkeypatch.setenv("CRISSA_CRISSA_POPUP_FRONT", "0")
    K._dialog(r, "Help", ["one line"], [("Close", None)])
    _pump(r)
    x0, y0, x1, y1 = K.panel_box()
    assert (x0, y0) == K.panel_place("center", (0, 0, 1280, 720), (x1 - x0, y1 - y0)) and kb == []
