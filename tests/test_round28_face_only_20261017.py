"""Round 28 (.101 2026-10-17, ASK 18c/19): Face only's picture froze after a player restart and never came back (a
stalled MJPEG raises no error), its page crashed grey and stayed gone, and a drag flashed several pictures per move."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_face as F  # noqa: E402

PAGE = (ROOT / "display" / "crissa-face.html").read_text()
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text()


def _reset():
    F._LIVE.update(seen=0.0, closed=True, reopened=0.0, url="", watch=True)   # watch=True: no thread in a test


def test_a_page_that_went_silent_is_reopened():
    _reset()
    F.note_seen("http://p", now=100.0)
    assert not F.reopen_due(now=105.0)
    assert F.reopen_due(now=111.0), "silent past STALE_S"
    F._LIVE["reopened"] = 111.0
    assert not F.reopen_due(now=130.0), "once a minute at most"


def test_a_close_on_purpose_is_never_reopened():
    _reset()
    F.note_seen("http://p", now=100.0)
    F.note_closed()
    assert not F.reopen_due(now=500.0)
    F.note_seen(now=600.0)
    assert not F._LIVE["closed"], "a page that polls again is live again"


def test_a_page_never_seen_is_not_opened():
    _reset()
    assert not F.reopen_due(now=1e9)


def test_the_kill(monkeypatch):
    monkeypatch.setenv("CRISSA_FACE_REOPEN", "0")
    assert F.reopen_on() is False


def test_the_wiring():
    assert 'crissa_face.note_seen("http://127.0.0.1:%d" % PORT,' in CONDUCTOR
    assert 'if body.get("closed"):\n                crissa_face.note_closed()' in CONDUCTOR
    assert "note_closed()                                   # round 28" in (ROOT / "lib" / "crissa_face.py").read_text()
    assert 'navigator.sendBeacon("/v1/face-window"' in PAGE and '"pagehide"' in PAGE


def test_a_stalled_picture_reconnects_and_a_drag_moves_once_a_frame():
    assert "const STALL_MS = 4000" in PAGE and 'film.src = "/frame.mjpg?" + Date.now(); }\n  }, 1000);' in PAGE
    assert "requestAnimationFrame(() => { frameAsked = false; sendMove(); })" in PAGE
