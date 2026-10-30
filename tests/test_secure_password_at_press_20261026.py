"""The Secure password is asked where he pressed, large, and says which password (the User's walk of the 1.0.1
candidate, 2026-10-26: "the sudo password is really hard to see esp on mine as it takes it in the menu (2nd menu) left
bottom and Secure is top right - not in menu"). Real Tk under Xvfb only, never the User's screen."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "lib"))
from test_round17_agent_os_20261013 import _pump, needs_x, wall  # noqa: E402,F401  (the fixture)
import crissa_wall_key as K  # noqa: E402


def test_the_place_is_under_the_press_and_on_the_work_area():
    wa = (0, 0, 1280, 720)
    assert K.panel_place("at", wa, (300, 120), (1200, 20)) == (980, 26)      # top-right lamp: under it, not off the edge
    assert K.panel_place("at", wa, (300, 120), (640, 700)) == (490, 574)     # near the bottom: above it instead
    assert K.panel_place("at", wa, (300, 120), (10, 20))[0] == 0             # never off the left


@needs_x
def test_the_hold_password_opens_at_the_lamp_large_with_the_keyboard(wall):
    r, key, kb, logs, K_ = wall
    got = []
    K_.password_popup(r, "Hold Secure", [K_.LOGIN_PASSWORD_WORDS + ": holds Secure until the next click."], got.append,
                      at=(1200, 20))
    _pump(r)
    x0, y0, x1, y1 = K_.panel_box()
    assert y0 >= 20 and x1 <= 1280 and x0 > 640 and kb == ["grant"]           # top right, not over the key
    body = K_._S["panel"]["frame"].winfo_children()[0]
    sizes = [abs(int(str(w.cget("font")).split()[-1].strip("}"))) for w in body.winfo_children()
             if w.winfo_class() == "Label" and "sudo" in str(w.cget("text"))]
    assert sizes and sizes[0] > K_.MENU_FONT[1]                              # larger than the menu's type
    entry = next(w for w in body.winfo_children()
                 if w.winfo_class() == "Frame" and any(c.winfo_class() == "Entry" for c in w.winfo_children()))
    e = next(c for c in entry.winfo_children() if c.winfo_class() == "Entry")
    e.insert(0, "not-a-real-password")
    r.event_generate("<Return>")
    _pump(r, 0.5)
    assert got == ["not-a-real-password"] and kb == ["grant", "release"]


@needs_x
def test_the_kill_keeps_it_above_the_key(wall, monkeypatch):
    r, key, kb, logs, K_ = wall
    monkeypatch.setenv("CRISSA_POPUP_AT_PRESS", "0")
    K_.password_popup(r, "Hold Secure", ["Account password: holds Secure until the next click."], lambda s: None,
                      at=(1200, 20))
    _pump(r)
    x0, y0, x1, y1 = K_.panel_box()
    kx, ky = K_.key_anchor()
    assert x0 == kx and y1 <= ky
    K_.close_panel()


def test_the_wall_passes_the_press_and_names_the_password():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "secure_double(at=(int(ev.x), int(ev.y)))" in src and "LOGIN_PASSWORD_WORDS" in src
    assert "the one sudo asks for" in K.LOGIN_PASSWORD_WORDS
