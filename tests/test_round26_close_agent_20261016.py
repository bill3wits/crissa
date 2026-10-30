"""Round 26: Close Agent closes her pages until they are closed (the round 25 and round 26 acceptances under a parallel
test run: "Close Agent did not close the agent alone", her Wall and Connect pages open 36 s later)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_window as W  # noqa: E402
import crissa_watch as WT  # noqa: E402

PLAYER = "http://127.0.0.1:8180"


def _browser(monkeypatch, open_pages, close_takes=1, up_answers=None):
    """A browser on her port whose closes take effect only from the `close_takes`-th try."""
    st = {"closes": 0, "pages": list(open_pages), "ups": list(up_answers or [])}
    monkeypatch.setattr(W, "enabled", lambda e=None: True)
    monkeypatch.setattr(W, "close_on_stop", lambda e=None: True)
    monkeypatch.setattr(W, "port", lambda e=None: 9333)
    monkeypatch.setattr(W, "up", lambda p=None, timeout=0.6: st["ups"].pop(0) if st["ups"] else True)
    monkeypatch.setattr(W, "_hers_on_port", lambda p, home=None, plat=None: True)
    monkeypatch.setattr(W, "pages", lambda p=None, timeout=1.0: list(st["pages"]))
    monkeypatch.setattr(W, "_browser_ws", lambda p: "ws://x")

    def cdp(ws, calls, timeout=4.0):
        st["closes"] += 1
        if st["closes"] >= close_takes:
            ids = {c[1]["targetId"] for c in calls}
            st["pages"] = [p for p in st["pages"] if p["id"] not in ids]
        return [{} for _ in calls]
    monkeypatch.setattr(W, "cdp", cdp)
    return st


PAGES = [{"id": "a", "url": PLAYER + "/wall", "type": "page"}, {"id": "b", "url": PLAYER + "/connect", "type": "page"},
         {"id": "c", "url": "http://127.0.0.1:8177/", "type": "page"}]


def test_a_busy_browser_is_asked_again_until_her_pages_are_gone(monkeypatch):
    st = _browser(monkeypatch, PAGES, close_takes=2)
    got = W.close_agent_pages(PLAYER, sleep=lambda s: None)
    assert got["ok"] and got["how"] == ["closed /wall", "closed /connect"] and st["closes"] == 2
    assert [p["id"] for p in st["pages"]] == ["c"], "the Control Panel stays"


def test_a_slow_first_answer_is_not_taken_as_not_running(monkeypatch):
    st = _browser(monkeypatch, PAGES, up_answers=[False, True])
    got = W.close_agent_pages(PLAYER, sleep=lambda s: None)
    assert got["ok"] and "closed /wall" in got["how"] and st["closes"] == 1


def test_still_open_after_every_try_says_retry(monkeypatch):
    _browser(monkeypatch, PAGES, close_takes=99)
    got = W.close_agent_pages(PLAYER, tries=2, sleep=lambda s: None)
    assert got["ok"] is False and got["retry"] and "still open after 2 tries" in got["how"][-1]


def _launcher(monkeypatch):
    spec = importlib.util.spec_from_file_location("crissa_launcher_r26c", ROOT / "scripts" / "crissa.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "_log", lambda *a, **k: None)
    m.AGENT_HELD.clear()
    return m


class _G:
    procs: dict = {}

    def alive(self, n):
        return False


def test_the_launcher_tries_again_on_its_next_passes_then_stops(monkeypatch, tmp_path):
    L = _launcher(monkeypatch)
    WT.set_closed(WT.AGENT, True, by="the acceptance", home=tmp_path)
    st, said = {"closed": False}, ["still open after 3 tries", "still open after 3 tries", "closed /wall"]
    calls = []

    def close():
        calls.append(1)
        return said.pop(0)
    for _ in range(5):
        L.agent_pass(_G(), tmp_path, st, close_window=close)
    assert len(calls) == 3 and st["window_done"] is True
    st2 = {"closed": False}
    calls.clear()
    for _ in range(L.WINDOW_TRIES + 3):
        L.agent_pass(_G(), tmp_path, st2, close_window=lambda: calls.append(1) or "could not close it (OSError)")
    assert len(calls) == L.WINDOW_TRIES, "it gives up after WINDOW_TRIES passes"
