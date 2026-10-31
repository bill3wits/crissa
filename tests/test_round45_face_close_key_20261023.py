"""Round 45 (.101 round 43, the User: "how do I close her face tile? no x or button"): the frameless face window's bar
gets an x at its end. It asks the player to close the window (closed on purpose: never reopened), else the page closes
itself. Kill CRISSA_FACE_CLOSE_KEY=0 (no key)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_face  # noqa: E402
import crissa_home  # noqa: E402
import crissa_window as W  # noqa: E402

PAGE = (ROOT / "display" / "crissa-face.html").read_text(encoding="utf-8")
COND = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def test_the_bar_has_a_close_key_shown_by_the_player():
    bar = PAGE.split('<div id="bar"', 1)[1].split("</div>", 1)[0]
    assert 'id="b-close"' in bar and bar.rstrip().endswith("</button>") and "&times;" in bar
    assert '$("b-close").hidden = !d.close_key' in PAGE
    assert "JSON.stringify({close: true})" in PAGE and "if (!ok) window.close();" in PAGE
    assert "close_key=crissa_face.close_key_on()" in COND


def test_the_player_closes_it_on_purpose():
    branch = COND.split('if body.get("close") and crissa_face.close_key_on():', 1)[1].split("return self._json", 1)[0]
    assert "crissa_face.note_closed()" in branch and "crissa_window.close_face(" in branch


def test_close_face_closes_her_face_page_only(monkeypatch):
    sent = []
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(W, "up", lambda p=None: True)
    monkeypatch.setattr(W, "pages", lambda p=None, timeout=1.0: [
        {"id": "wall", "type": "page", "url": "http://127.0.0.1:8100/wall"},
        {"id": "face", "type": "page", "url": "http://127.0.0.1:8100/face"}])
    monkeypatch.setattr(W, "_browser_ws", lambda p: "ws://x")
    monkeypatch.setattr(W, "cdp", lambda ws, calls, *a: sent.extend(calls) or [{}])
    assert W.close_face("http://127.0.0.1:8100/face", env={}) is True
    assert sent == [("Target.closeTarget", {"targetId": "face"})]


def test_the_kill():
    assert crissa_face.close_key_on({}) is True
    assert crissa_face.close_key_on({"CRISSA_FACE_CLOSE_KEY": "0"}) is False
