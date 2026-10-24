"""Round 27, Face only (.101 2026-10-16): the menu's Face only left the wallpaper up ("we make menu button match is
all": it must do what her tile's WALL key does), and the frameless face window could not be dragged."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_menu as M  # noqa: E402
import crissa_window as W  # noqa: E402


def test_face_only_closes_the_wall_like_the_tile_key(monkeypatch, tmp_path):
    monkeypatch.delenv("CRISSA_FACE_ONLY_CLOSES_WALL", raising=False)
    seen = []
    monkeypatch.setattr(M.crissa_home, "is_house", lambda: False)
    monkeypatch.setattr(M, "_menu_gate_name", lambda *a, **k: "")
    monkeypatch.setattr(M, "wall_state", lambda home=None: {"present": True, "closed": False})
    monkeypatch.setattr(M, "wall_close", lambda home=None, by="": seen.append(by) or {"ok": True, "closed": True})
    got = M.act("face", {}, home=tmp_path)
    assert got["closed"] is True and seen == ["Face only"]


def test_no_desk_wall_or_the_kill_opens_the_window_as_before(monkeypatch):
    src = (ROOT / "lib" / "crissa_menu.py").read_text()
    i = src.index('if action == "face":')
    body = src[i:src.index('if action == "stop":', i)]
    assert 'wall_state(home).get("present")' in body and "CRISSA_FACE_ONLY_CLOSES_WALL" in body
    assert body.index("wall_close(") < body.index("open_window(")


def test_the_page_drags_its_window_and_keeps_the_place_after():
    page = (ROOT / "display" / "crissa-face.html").read_text()
    assert 'view.addEventListener("pointerdown"' in page and "move:" in page
    assert re.search(r"pointerup.*endDrag", page) and "keep();" in page.split("const endDrag")[1]
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    i = src.index('if head == "/v1/face-window"')
    assert 'body.get("move")' in src[i:i + 2500] and "move_face(" in src[i:i + 2500]


def test_move_face_sets_only_the_place_and_never_in_a_test(monkeypatch):
    assert W.move_face("http://127.0.0.1:1/face", 10, 20, env={"PYTEST_CURRENT_TEST": "x"}) is False
    calls = []
    monkeypatch.setattr(W, "enabled", lambda e: True)
    monkeypatch.setattr(W, "_in_test", lambda e: False)
    monkeypatch.setattr(W, "up", lambda p: True)
    monkeypatch.setattr(W, "port", lambda e: 9)
    monkeypatch.setattr(W, "face_page", lambda u, p: {"id": "T"})
    monkeypatch.setattr(W, "_browser_ws", lambda p: "ws://b")

    def cdp(ws, cmds, *a):
        calls.append(cmds)
        return [{"windowId": 7}] if cmds[0][0] == "Browser.getWindowForTarget" else [{}]
    monkeypatch.setattr(W, "cdp", cdp)
    W._MOVE.clear()
    assert W.move_face("u", 100, 200, env={}) and W.move_face("u", 110, 205, env={})
    sets = [c[0][1] for c in calls if c[0][0] == "Browser.setWindowBounds"]
    assert sets == [{"windowId": 7, "bounds": {"left": 100, "top": 200}},
                    {"windowId": 7, "bounds": {"left": 110, "top": 205}}]
    assert sum(1 for c in calls if c[0][0] == "Browser.getWindowForTarget") == 1      # one lookup a drag
