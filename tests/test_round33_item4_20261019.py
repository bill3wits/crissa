"""Round 33 item 4 (.101 10-19, the User: "the render window is hidden still and on house repositions to the middle"):
the player's layout said "centred beside the page, in view", but only the browser Wall's page read it. Her face window
(Face only / the Wall off) now moves into that box once her sheet is up, comes forward, and goes back when the card
comes down. A pack only; kill CRISSA_FACE_BESIDE_CARD=0."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_face as F  # noqa: E402
import crissa_home  # noqa: E402
import crissa_layout  # noqa: E402

SCREEN = (1920, 1080)
LAY = crissa_layout.decide(True, cfg={"move": True, "anchor": "center"}, screen=SCREEN)


@pytest.fixture
def pack(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.delenv("CRISSA_FACE_BESIDE_CARD", raising=False)
    monkeypatch.delenv("CRISSA_FACE_ONLY", raising=False)
    monkeypatch.setattr(F, "_BESIDE", {"back": None})
    return tmp_path


def _calls():
    log = []
    kw = dict(face=lambda u: {"x": 1500, "y": 240, "w": 334, "h": 594},
              place=lambda u, b: log.append(("place", u, b)) or True,
              bring=lambda u: log.append(("bring", u)), screen=SCREEN)
    return log, kw


def test_the_box_is_left_of_the_sheet_and_in_view():
    b = F.beside_bounds(LAY["box"], SCREEN)
    sheet_x = 1920 - 0.02 * 1920 - min(0.40 * 1920, 0.936 * 1080 * 8.5 / 11.0)
    assert b["x"] >= 0 and b["x"] + b["w"] <= sheet_x            # never under the sheet
    assert abs(b["w"] / b["h"] - 9 / 16) < 0.01 and b["y"] >= 0 and b["y"] + b["h"] <= 1080


def test_up_moves_her_and_brings_her_forward_down_puts_her_back(pack):
    F.save({"x": 1500, "y": 240, "w": 334, "h": 594}, pack)
    log, kw = _calls()
    assert F.beside_card("http://p", True, LAY, pack, **kw) == "beside the card"
    assert log[0][0] == "place" and log[0][1] == "http://p/face" and log[0][2]["x"] < 1500 and log[1][0] == "bring"
    F.save(log[0][2], pack)                                       # the page posts the middle meanwhile
    log.clear()
    assert F.beside_card("http://p", False, None, pack, **kw) == "back"
    assert log == [("place", "http://p/face", {"x": 1500, "y": 240, "w": 334, "h": 594})]
    assert json.loads((pack / F.FILE).read_text())["x"] == 1500     # her kept place is never the middle
    assert F.beside_card("http://p", False, None, pack, **kw) == "not moved"


def test_no_face_window_no_box_or_the_kill_does_nothing(pack, monkeypatch):
    log, kw = _calls()
    assert F.beside_card("http://p", True, LAY, pack, **dict(kw, face=lambda u: None)) == "no face window"
    assert F.beside_card("http://p", True, {"render": "home"}, pack, **kw) == "no box"
    monkeypatch.setenv("CRISSA_FACE_BESIDE_CARD", "0")
    assert F.beside_card("http://p", True, LAY, pack, **kw) == "off"
    monkeypatch.delenv("CRISSA_FACE_BESIDE_CARD")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert F.beside_card("http://p", True, LAY, pack, **kw) == "off"
    assert log == []


def test_the_player_calls_it_after_the_sheet_and_on_the_cards_way_down():
    c = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    tool = c.split("def display_tool", 1)[1].split("\ndef ", 1)[0]
    assert "face_beside_card(True)" in tool and tool.index("show_sheet(") < tool.index("face_beside_card(True)")
    lay = c.split("def layout_now", 1)[1].split("\ndef ", 1)[0]
    assert "face_beside_card(False)" in lay
    fn = c.split("def face_beside_card", 1)[1].split("\ndef ", 1)[0]
    assert "crissa_home.is_house()" in fn and "threading.Thread(" in fn
