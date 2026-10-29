"""Round 14 (.101, the User 2026-10-12): her lines always log and her echo does not, SPEAKER names her while she
talks, the Crissa key and menu wear the muted look and the key goes with the idle chrome, and under the whole frame
the -/+ zoom her film on both glasses with AR on or off (AR's hole scans the wallpaper's whole plane 1:1)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from _kit import kit  # noqa: E402  the kit's copy (2026-10-24: a release clone has no crissa/overlay; GitHub ran this)
PAGES = [ROOT / "display" / "voiceonly-home.html", kit("display/voiceonly-home.html")]


# ---- her lines -----------------------------------------------------------------------------------------------------
def _feed(R, chat, now=10.0):
    roll = R.Roll(since=0.0)
    roll.feed(chat, now=now)
    return roll


def test_her_lines_are_hers_and_the_room_chip_never_hides_them(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.setattr(R, "ROLL_HER_CLASS", True)
    roll = _feed(R, [{"who": "crissa", "text": "Pick up the big blue map.", "ts": 1.0},
                     {"who": "stranger", "text": "the radio is on", "ts": 2.0}])
    cls = {ln["text"]: ln["cls"] for ln in roll.lines}
    assert cls == {"Pick up the big blue map.": "her", "the radio is on": "other"}
    room_off = {"log_room": False}
    shown = [ln["text"] for ln in roll.lines if R.Roll.shown(ln, room_off)]
    assert shown == ["Pick up the big blue map."]


def test_her_echo_is_not_a_line_in_a_pack_and_anyone_elses_words_stay(monkeypatch):
    import house_wall_ascii_render as R
    monkeypatch.setattr(R, "ROLL_DROP_ECHO", True)
    chat = [{"who": "stranger", "text": "My mum made fung jam.", "ts": 1.0},             # the echo comes first
            {"who": "crissa", "text": "My mum made fung jam. Wash my lipstick on every P B and Z.", "ts": 0.5},
            {"who": "", "text": "wash my lipstick on every p b and z", "ts": 2.0},
            {"who": "stranger", "text": "My mum made fung jam. Hey Crissa, stop!", "ts": 3.0},
            {"who": "user", "text": "My mum made fung jam.", "ts": 4.0, "log_user": True}]
    roll = _feed(R, chat)
    texts = sorted((ln["who"], ln["text"]) for ln in roll.lines)
    assert texts == sorted([("crissa", "My mum made fung jam. Wash my lipstick on every P B and Z."),
                            ("stranger", "My mum made fung jam. Hey Crissa, stop!"),
                            ("user", "My mum made fung jam.")])


def test_the_house_keeps_every_line(monkeypatch):
    import house_wall_ascii_render as R
    import crissa_home
    monkeypatch.delenv("HOUSE_WALL_ROLL_ECHO", raising=False)
    monkeypatch.delenv("HOUSE_WALL_ROLL_HER_CLASS", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert R._roll_drop_echo() is False
    assert R._roll_her_class() is False                      # the house's desk Wall keeps today's chips
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert R._roll_her_class() is True and R._roll_drop_echo() is True
    monkeypatch.setattr(R, "ROLL_DROP_ECHO", False)
    roll = _feed(R, [{"who": "crissa", "text": "Hello there friend.", "ts": 1.0},
                     {"who": "stranger", "text": "Hello there friend.", "ts": 2.0}])
    assert len(roll.lines) == 2


def test_speaker_names_her_while_she_talks_in_a_pack(monkeypatch):
    import house_wall_ascii_render as R
    import crissa_home
    assert R.she_is_speaking({"chat": [{"who": "crissa", "text": "hi", "live": True}]})
    assert R.she_is_speaking({"hear": {"speaking": True}})
    assert not R.she_is_speaking({"chat": [{"who": "crissa", "text": "hi", "live": False}]})
    assert not R.she_is_speaking({"chat": [{"who": "user", "text": "hi", "live": True}]})
    monkeypatch.delenv("HOUSE_WALL_SPEAKER_NAMES_HER", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert R.speaker_names_her() is True
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert R.speaker_names_her() is False                     # the house keeps its 09-07 rule
    src = (ROOT / "lib" / "house_wall_ascii_render.py").read_text()
    assert "if speaker_names_her() and she_is_speaking(house):\n                who = crissa_home.her_name()" in src


def test_the_kit_page_drops_her_echo_and_names_her(monkeypatch):
    # one page since the fold (2026-10-28): the echo rule is both editions'; the speaker row names her in a pack only
    # (the feed writes "" on the house, whose row holds - 09-07)
    kit = PAGES[1].read_text(encoding="utf-8")
    assert PAGES[0] == PAGES[1]
    assert "herSaidNote(ded);" in kit and "herEcho(t)" in kit and 'var SPEAKER_NAMES_HER = "__SPEAKER_NAMES_HER__";' in kit
    house = kit
    rule = 'if(typeof SPEAKER_NAMES_HER==="string" && SPEAKER_NAMES_HER) who = SPEAKER_NAMES_HER;\n    else return;'
    assert rule in kit and rule in house                                     # one shared block, the edition decides


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_the_pages_echo_rule_is_the_walls(tmp_path):
    kit = PAGES[1].read_text(encoding="utf-8")
    a, b = kit.index("var HER_SAID=[];"), kit.index("var ROLL_CROSS_S  = 55;")
    js = kit[a:b] + """
herSaidNote([{who:"crissa", text:"My mum made fung jam. Wash my lipstick on every P B and Z."}]);
console.log(JSON.stringify([herEcho("My mum made fung jam."), herEcho("wash my lipstick on every p b and z"),
  herEcho("My mum made fung jam. Hey Crissa, stop!"), herEcho("")]));
"""
    f = tmp_path / "e.js"
    f.write_text(js)
    out = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout)
    assert out == [True, True, False, False]


# ---- the Crissa key and menu ------------------------------------------------------------------------------------------
class _Key:
    def __init__(self):
        self.seen = []

    def configure(self, **kw):
        self.seen.append(("cfg", kw))

    def place_forget(self):
        self.seen.append(("forget",))

    def place(self, **kw):
        self.seen.append(("place", kw))

    def lift(self):
        self.seen.append(("lift",))

    def after(self, _ms, fn, *a):
        fn(*a)                                     # the fade's steps, at once


def test_the_key_fades_out_with_the_idle_chrome_and_comes_back(monkeypatch):
    import crissa_wall_key as K
    monkeypatch.delenv("CRISSA_CRISSA_KEY_HIDES", raising=False)
    boxes = []
    key = _Key()
    K._S.update(key=key, dim=None, menu=None, box=(16, 900, 120, 924), shown_box=(16, 900, 120, 924),
                on_box=lambda: boxes.append(K._S["box"]))
    K.dim(True)
    assert ("forget",) in key.seen and K._S["box"] is None and boxes == [None]
    last_cfg = [kw for k, *kw in key.seen if k == "cfg"][-1][0]
    assert last_cfg["fg"] == K.GROUND and last_cfg["bg"] == K.GROUND        # faded into the ground before it went
    key.seen.clear()
    K.dim(False)
    assert key.seen[1] == ("place", {"x": 16, "y": 900}) and K._S["box"] == (16, 900, 120, 924)
    assert [kw for k, *kw in key.seen if k == "cfg"][-1][0]["fg"] == K.KEY_FG
    K._S.update(menu=object())
    key.seen.clear()
    K.dim(True)                                    # never while its menu is open
    assert key.seen == []
    K._S.update(menu=None)


def test_the_fade_runs_from_the_keys_face_to_the_ground():
    import crissa_wall_key as K
    assert K.fade_colours(1.0) == {"fg": K.KEY_FG, "bg": K.KEY_BG, "bd": K.KEY_BD}
    assert K.fade_colours(0.0) == {"fg": K.GROUND, "bg": K.GROUND, "bd": K.GROUND}


def test_the_keys_face_is_the_rail_keys():
    import crissa_wall_key as K
    import house_wall_ascii_render as R
    assert K.KEY_FG == "#%02X%02X%02X" % R._RAIL_FG[:3]
    assert K.INK2 == "#%02X%02X%02X" % R.INK2 and K.HAIR == "#%02X%02X%02X" % R.HAIR
    assert K.INK3 == "#%02X%02X%02X" % R.INK3 and K.GROUND2 == "#%02X%02X%02X" % R.GROUND2


def test_the_menu_panel_rows():
    import crissa_wall_key as K
    rows = K.menu_rows(K.menu_items({"mic": {"label": "Microphone: on", "on": True},
                                     "unavailable": [{"label": "Turbo", "why": "no GPU"}]}, False))
    kinds = [r[0] for r in rows]
    assert kinds[0] == "places" and len(rows[0][1]) == 3                   # 2026-10-28: the places row leads
    first = next(r for r in rows if r[0] == "item")
    assert first[2] == "mic" and first[4] is True                           # the microphone's state is on
    assert "sep" in kinds and kinds[-1] == "item" and rows[-1][2] == "stop"
    assert all(not (a == "sep" and b == "sep") for a, b in zip(kinds, kinds[1:]))
    assert [r for r in rows if r[0] == "note"] and all(not r[3] for r in rows if r[0] == "note")


def test_the_browser_menu_is_the_same_muted_look():
    import crissa_wall_key as K
    css = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    block = css[css.index(".cmenu {"):css.index(".cmenu .help")]
    for colour in (K.INK2, K.HAIR, K.INK3, K.SAGE, K.INK):
        assert colour.upper() in block.upper(), colour
    assert "const IDLE_MS = 1000;" in css and ".ckey.idle { opacity: 0; pointer-events: none; }" in css
    assert "#99A0A9" in css[css.index(".ckey {"):css.index(".cmenu {")]


# ---- whole-frame zoom on both glasses, AR on or off -------------------------------------------------------------------
def test_the_desk_walls_zoom_keys_are_the_film_zoom_under_whole_with_ar_on_too():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'elif name in ("ar_zoom_lo", "ar_zoom_hi") and wall_frame.mode() == "whole":' in src
    assert "        if not own or wall_frame.mode() != \"whole\":" in src
    body = src[src.index("    def paint_ui():"):src.index("    def paint_ui():") + 1200]
    assert 'if wall_frame.mode() == "whole":' in body and "zoom_is_frame" in body


def test_both_pages_drop_ar_from_the_whole_frame_gate():
    blocks = []
    for p in PAGES:
        s = p.read_text(encoding="utf-8")
        assert 'wfL = (crissaOwnFrame && WALL_FRAME.mode==="whole")' in s
        assert 'if(crissaCh === true && WALL_FRAME.mode==="whole"){' in s
        a = s.index('if(crissaCh === true && WALL_FRAME.mode==="whole"){')
        blocks.append(s[a:s.index("if(crissaCh === true){", a)])
    assert blocks[0] == blocks[1]


def _js_function(src: str, name: str) -> str:
    a = src.index("function %s(" % name)
    i = src.index("{", a)
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[a:j + 1]
    raise ValueError(name)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_the_ar_hole_shows_the_wallpapers_own_plane(tmp_path):
    import wall_frame as WF
    s = PAGES[1].read_text(encoding="utf-8")
    head = s[s.index("var WF_ZOOM_MIN="):s.index("function wfRnd(x)")]
    js = (head + _js_function(s, "wfRnd") + "\n" + _js_function(s, "wfClampZoom") + "\n" + _js_function(s, "wfFaceOf")
          + "\n" + _js_function(s, "wallFrameLayout") + "\n" + _js_function(s, "fovSourceFromBounds") + "\n")
    out = []
    face = (0.5268, 0.4796)
    for sw_, sh_, z in ((2560, 1440, 1.0), (3440, 1440, 1.5), (2560, 1440, 2.25)):
        L = WF.layout(736, 400, sw_, sh_, z, face, 1, 1)
        fc, fr = L["face"]
        win = (int(fc - 300), int(fr - 200), 600, 400)              # a window centred on her face on the wallpaper
        js_case = ("var kioskBounds={ok:true, fullscreen:false, sw:%d, sh:%d, x:%d, y:%d, w:%d, h:%d};"
                   "var WALL_FRAME={mode:'whole', face:[%r,%r]}; var FRAME_ZOOM=%r; var FOV_ZOOM=1;"
                   "console.log(JSON.stringify(fovSourceFromBounds(736, 400, true, 1, true)));"
                   % (sw_, sh_, win[0], win[1], win[2], win[3], face[0], face[1], z))
        f = tmp_path / ("h%d.js" % len(out))
        f.write_text(js + js_case)
        got = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout)
        cx, cy = got["sx"] + got["sw"] / 2, got["sy"] + got["sh"] / 2
        out.append((cx / 736, cy / 400, got["sw"] / got["sh"]))
    for cx, cy, aspect in out:
        assert abs(cx - face[0]) < 0.01 and abs(cy - face[1]) < 0.01          # the hole shows her face, where it is
        assert aspect == pytest.approx(600 / 400, rel=0.01)                   # never stretched
