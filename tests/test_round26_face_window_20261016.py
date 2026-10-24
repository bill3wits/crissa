"""Round 26, her face window (.101 10-16): it did not reopen where it was dragged, grew on every reopen, came up
fullscreen at a start and as a tabbed window with an OS frame; the User asked for the tile alone with the Wall tile's
own bar (- size +, FT, BLUR, WALL) waking on any move of the pointer, and a snap home."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import crissa_face as F  # noqa: E402
import crissa_window as Z  # noqa: E402


# ---- placed through the browser, checked, set again --------------------------------------------------------------------
class _CDP:
    """A browser whose window takes bounds only once it is normal."""

    def __init__(self, state="fullscreen"):
        self.state, self.b, self.sent = state, {"left": 0, "top": 0, "width": 1920, "height": 1080}, []

    def __call__(self, ws, calls, timeout=4.0):
        out = []
        for name, args in calls:
            self.sent.append((name, args))
            if name == "Browser.getWindowForTarget":
                out.append({"windowId": 7, "bounds": dict(self.b, windowState=self.state)})
            elif name == "Browser.setWindowBounds":
                bb = args["bounds"]
                if bb.get("windowState"):
                    self.state = bb["windowState"]
                elif self.state == "normal":
                    self.b.update({k: bb[k] for k in ("left", "top", "width", "height")})
                out.append({})
        return out


def test_a_fullscreen_window_is_made_normal_then_placed_and_checked(monkeypatch):
    c = _CDP()
    monkeypatch.setattr(Z, "cdp", c)
    monkeypatch.setattr(Z, "_browser_ws", lambda p: "ws://x")
    ok = Z._place(9222, {"id": "T"}, {"x": 1500, "y": 240, "w": 338, "h": 600}, sleep=lambda s: None)
    assert ok and c.state == "normal" and c.b == {"left": 1500, "top": 240, "width": 338, "height": 600}


def test_the_saved_place_is_the_browsers_and_a_drop_near_home_snaps(monkeypatch, tmp_path):
    monkeypatch.setattr(Z, "face_bounds", lambda url, env=None: {"x": 1570, "y": 250, "w": 338, "h": 600})
    monkeypatch.setattr(Z, "screen_size", lambda *a, **k: (1920, 1080))
    placed = []
    monkeypatch.setattr(Z, "place_face", lambda url, b, env=None: placed.append(b) or True)
    got = F.keep({"x": 1, "y": 2, "w": 400, "h": 700}, "http://127.0.0.1:8100", home=tmp_path)
    assert got["by"] == "browser" and got["snapped"] and got["bounds"] == {"x": 1550, "y": 240, "w": 338, "h": 600}
    assert json.loads((tmp_path / F.FILE).read_text())["x"] == 1550


def test_far_from_home_it_stays_where_it_was_let_go(monkeypatch, tmp_path):
    monkeypatch.setattr(Z, "face_bounds", lambda url, env=None: {"x": 400, "y": 100, "w": 338, "h": 600})
    monkeypatch.setattr(Z, "screen_size", lambda *a, **k: (1920, 1080))
    got = F.keep(None, "http://127.0.0.1:8100", home=tmp_path)
    assert not got["snapped"] and got["bounds"]["x"] == 400
    assert F.snapped({"x": 1560, "y": 250, "w": 338, "h": 600}, (1920, 1080), {"CRISSA_FACE_SNAP_PX": "0"}) is None


def test_no_browser_answer_keeps_the_pages_numbers(monkeypatch, tmp_path):
    monkeypatch.setattr(Z, "face_bounds", lambda url, env=None: None)
    monkeypatch.setattr(Z, "screen_size", lambda *a, **k: (1920, 1080))
    got = F.keep({"x": 10, "y": 20, "w": 338, "h": 600}, "http://127.0.0.1:8100", home=tmp_path)
    assert got["by"] == "page" and got["bounds"]["x"] == 10


def test_a_bar_press_starts_from_the_browsers_bounds(monkeypatch, tmp_path):
    monkeypatch.setattr(Z, "face_bounds", lambda url, env=None: {"x": 1000, "y": 200, "w": 334, "h": 594})
    monkeypatch.setattr(Z, "screen_size", lambda *a, **k: (1920, 1080))
    monkeypatch.setattr(Z, "place_face", lambda url, b, env=None: True)
    monkeypatch.setattr(F, "save", lambda b, home=None, shape_=None: True)
    nb = F.resize_window("http://127.0.0.1:8100", {"x": 0, "y": 0, "w": 999, "h": 999}, "face", 1)
    assert nb["h"] == round(594 * F.SIZE_STEP)


# ---- no frame ----------------------------------------------------------------------------------------------------------
def test_the_frame_is_taken_off_her_face_window_only(monkeypatch):
    out = {"xprop -root _NET_CLIENT_LIST_STACKING": "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x10, 0x20, 0x30",
           "xprop -id 0x10 WM_CLASS": 'WM_CLASS(STRING) = "crissa", "Crissa"',
           "xprop -id 0x20 WM_CLASS": 'WM_CLASS(STRING) = "crissa", "Crissa"',
           "xprop -id 0x30 WM_CLASS": 'WM_CLASS(STRING) = "gnome-terminal", "Gnome-terminal"',
           "xprop -id 0x10 _NET_WM_NAME": '_NET_WM_NAME(UTF8_STRING) = "Crissa face"',
           "xprop -id 0x20 _NET_WM_NAME": '_NET_WM_NAME(UTF8_STRING) = "Crissa Wall"'}
    sent = []
    got = Z._frameless({"DISPLAY": ":0"}, run=lambda a: out.get(" ".join(a), ""), send=lambda x, d: sent.append(x) or True,
                       sleep=lambda s: None, plat="linux")
    assert got == "frameless 0x10" and sent == [0x10]
    assert Z._frameless({"DISPLAY": ":0", "CRISSA_FACE_FRAMELESS": "0"}) == "off"


def test_the_page_is_titled_so_it_can_be_found():
    assert "<title>%s</title>" % Z.FACE_TITLE in (ROOT / "display" / "crissa-face.html").read_text()


def test_it_opens_as_an_app_window_when_her_browser_runs():
    src = (ROOT / "lib" / "crissa_window.py").read_text()
    blk = src.split("def show_face(", 1)[1].split("\ndef ", 1)[0]
    assert "if _open_app(url, e, p_, **kw):" in blk and "placed = _place(p_, page, bounds)" in blk
    assert 'out["frame"] = _frameless(e)' in blk


# ---- the bar ----------------------------------------------------------------------------------------------------------
def test_the_bar_is_the_wall_tiles():
    page = (ROOT / "display" / "crissa-face.html").read_text()
    bar = page.split('<div id="bar"', 1)[1].split("</div>", 1)[0]
    order = [bar.index(k) for k in ('id="b-minus"', 'id="size"', 'id="b-plus"', 'id="b-shape"', 'id="b-veil"',
                                    'id="b-wall"')]
    assert order == sorted(order)
    assert ">FT<" in bar and ">BLUR<" in bar and ">WALL<" in bar
    assert "d.ptr !== lastPtr" in page and "wake()" in page


def test_the_veil_is_the_wall_tiles_three(monkeypatch, tmp_path):
    import house_wall_prefs as P
    monkeypatch.setattr(P, "PATH", tmp_path / "prefs.json")
    assert F.veil() == "off"
    assert F.set_veil("blur") == "blur" and P.load()["blur"] is True and P.load()["ft_fade"] is False
    assert F.set_veil("fade") == "fade" and F.veil() == "fade"
    assert F.set_veil("nonsense") == "off" and F.veil() == "off"


def test_the_screen_pointer_wakes_the_bar(monkeypatch):
    seq = [(1, 1), (1, 1), (5, 9)]
    monkeypatch.setitem(F._PTR, "asked", 1e18)

    def q():
        if not seq:
            F._PTR["asked"] = 0.0
            return (5, 9)
        return seq.pop(0)
    F._PTR.update(xy=None, moved=0.0)
    F._ptr_loop(query=q, sleep=lambda s: None)
    assert F._PTR["moved"] > 0 and F._PTR["xy"] == (5, 9)
    monkeypatch.setenv("CRISSA_FACE_POINTER", "0")
    assert F.pointer_moved_at() == 0.0


def test_the_player_serves_them():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert "ptr=crissa_face.pointer_moved_at()," in src and "veil=crissa_face.veil()" in src
    assert 'crissa_face.keep(body["save"], "http://127.0.0.1:%d" % PORT)' in src
    assert 'crissa_face.set_veil(body["veil"])' in src


def test_the_desk_walls_tile_carries_the_wall_key():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'p["wall_key"] = WALL_KEY_ON' in src and 'elif name == "wall_off":' in src
    assert '{"action": "wall_close", "by": "her tile"}' in src
    rsrc = (ROOT / "lib" / "house_wall_ascii_render.py").read_text()
    assert 'quiet_chip(cx0 + sw + gap2 + mw + gap2 + bw + gap2, ly, "WALL", "wall_off"' in rsrc
