"""Round 45 (.101 round 43 R43-2: her letter on the browser Wall opened as a full Brave window - tab strip, address bar,
bookmark star, sidebar). With her browser already running, the sheet was Target.createTarget's new window, a plain
tabbed one. It now asks for an app window as her face window does (round 26), and falls back to the plain window only
when no app page comes. Kill CRISSA_SHEET_APP_WINDOW=0."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import pytest  # noqa: E402

import crissa_home  # noqa: E402
import crissa_window as W  # noqa: E402

PLAYER = "http://127.0.0.1:8100"


@pytest.fixture
def browser(monkeypatch):
    b = {"pages": [{"id": "wall", "type": "page", "url": PLAYER + "/wall"}], "apps": [], "cdp": [], "app_opens": True}
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.delenv("CRISSA_SHEET_APP_WINDOW", raising=False)
    monkeypatch.setitem(W.SHEET, "target", None)
    monkeypatch.setattr(W, "PAGE_WAIT_S", 0.3)
    monkeypatch.setattr(W, "up", lambda p=None: True)
    monkeypatch.setattr(W, "pages", lambda p=None, timeout=1.0: list(b["pages"]))

    def open_app(url, e, p_, kill="CRISSA_FACE_APP_WINDOW", **kw):
        if W._off(e.get(kill, "1")):
            return False
        b["apps"].append((url, kill))
        if b["app_opens"]:
            b["pages"].append({"id": "app", "type": "page", "url": url})
        return True

    def cdp(ws, calls):
        b["cdp"].append([c[0] for c in calls])
        if calls[0][0] == "Target.createTarget":
            b["pages"].append({"id": "tab", "type": "page", "url": calls[0][1]["url"]})
            return [{"targetId": "tab"}]
        return [{}]
    monkeypatch.setattr(W, "_open_app", open_app)
    monkeypatch.setattr(W, "cdp", cdp)
    monkeypatch.setattr(W, "_browser_ws", lambda p: "ws://x")
    monkeypatch.setattr(W, "_bring", lambda p_, page, url, front: {"ok": True, "how": ["brought"], "page": page["id"]})
    monkeypatch.setattr(W, "_keep_above", lambda e: "above")
    return b


def test_the_letter_opens_as_an_app_window_when_her_browser_runs(browser):
    out = W.show_sheet(PLAYER, None, None, env={}, popen=object())
    assert out["ok"] and out["page"] == "app" and out["opened"]
    assert browser["apps"] == [(PLAYER + "/letter", "CRISSA_SHEET_APP_WINDOW")]
    assert not any("Target.createTarget" in c for c in browser["cdp"]), "never the plain tabbed window"


def test_an_app_window_that_never_comes_falls_back_to_the_plain_window(browser):
    browser["app_opens"] = False
    out = W.show_sheet(PLAYER, None, None, env={}, popen=object())
    assert out["ok"] and out["page"] == "tab"


def test_the_kill_is_round_44(browser):
    out = W.show_sheet(PLAYER, None, None, env={"CRISSA_SHEET_APP_WINDOW": "0"}, popen=object())
    assert out["page"] == "tab" and browser["apps"] == []


def test_the_face_window_keeps_its_own_kill():
    src = (ROOT / "lib" / "crissa_window.py").read_text(encoding="utf-8")
    assert 'kill: str = "CRISSA_FACE_APP_WINDOW"' in src
