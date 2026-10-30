"""Round 14 (2026-10-12): the dock opens her Control Panel while the desk Wall is up.

The User: "agree on control panel. Only if they want the wall it should be available. Make it auto open CP if that is
better." On .101 the Crissa dock icon (scripts/crissa.py --window) opened the browser Wall in her window while the desk
Wall ran, so two glasses painted her (her Brave at a whole core). Rules:
  1. desk Wall running: the dock opens (or brings forward) HER window on the Control Panel, never a second Wall;
  2. the browser Wall stays available on request: "Open the Wall in this window" on the Control Panel;
  3. no desk Wall (--no-desk, closed, down, a Mac): the dock opens the Wall as before;
  4. Crissa not running: the dock starts her as before, then rules 1/3/5;
  5. Setup due: Setup in front, as before.
A stand-in browser (the acceptance's: her profile, a DevTools port that follows Page.navigate) and the launcher's own
table stand in for the real ones; nothing runs a real browser or display.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_home  # noqa: E402
import crissa_procs as P  # noqa: E402
import crissa_watch  # noqa: E402
import crissa_window as W  # noqa: E402

PLAYER = "http://127.0.0.1:8100"
PANEL = "http://127.0.0.1:8097/"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _table(home: Path, children: dict, beat_age: float = 0.0, state: str = "running"):
    P._write(P.table_path(home), {"schema": P.SCHEMA, "launcher": {"pid": os.getpid(), "beat": time.time() - beat_age,
                                                                    "state": state}, "children": children})


@pytest.fixture
def L(monkeypatch, tmp_path):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    mod = _load("crissa_launcher_r14", ROOT / "scripts" / "crissa.py")
    monkeypatch.setattr(mod, "_log", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_notify", lambda text: None)
    return mod


# ── is the desk Wall up? the launcher's own table ───────────────────────────────────────────────────────────────────
def test_the_desk_wall_is_up_only_when_its_lane_runs_and_nobody_closed_it(L, tmp_path):
    me = os.getpid()
    _table(tmp_path, {"player": {"state": "running", "pid": me}, "wall": {"state": "running", "pid": me}})
    assert L.desk_wall_on(tmp_path) is True
    _table(tmp_path, {"player": {"state": "running", "pid": me}})
    assert L.desk_wall_on(tmp_path) is False, "--no-desk: no wall lane"
    for st in ("exited", "stopped", "down", "closed", "waiting"):
        _table(tmp_path, {"wall": {"state": st, "pid": me}})
        assert L.desk_wall_on(tmp_path) is False, st
    _table(tmp_path, {"wall": {"state": "restarting", "pid": 0}})
    assert L.desk_wall_on(tmp_path) is True, "a Wall the watch is bringing back is still the desk's"
    _table(tmp_path, {"wall": {"state": "running", "pid": me}}, beat_age=P.BEAT_STALE_S + 5)
    assert L.desk_wall_on(tmp_path) is False, "no live launcher"
    _table(tmp_path, {"wall": {"state": "running", "pid": me}}, state="stopped")
    assert L.desk_wall_on(tmp_path) is False
    _table(tmp_path, {"wall": {"state": "running", "pid": me}})
    crissa_watch.set_closed("wall", True, by="test", home=tmp_path)
    assert L.desk_wall_on(tmp_path) is False, "closed on purpose"
    assert L.desk_wall_on(tmp_path / "never-started") is False


# ── the dock press: which page ───────────────────────────────────────────────────────────────────────────────────────
def _press(L, monkeypatch, *, running=True, due=False, desk=True, panel_up=True, env=None):
    calls = {"show": [], "panel": [], "started": []}
    monkeypatch.setattr(L, "_player_up", lambda url, timeout=1.0: running or bool(calls["started"]))
    monkeypatch.setattr(L, "setup_due", lambda url, timeout=2.0: due)
    monkeypatch.setattr(L, "desk_wall_on", lambda *a, **k: desk)
    monkeypatch.setattr(L, "_panel_up", lambda url, timeout=1.0: panel_up)
    monkeypatch.setattr(L, "start_for_window", lambda a, run=None, popen=None: calls["started"].append(1) or "service")
    monkeypatch.setattr(sys.modules["crissa_init"] if "crissa_init" in sys.modules else __import__("crissa_init"),
                        "mark_browser_opened", lambda url, by="": None)
    rc = L.window_cmd(types.SimpleNamespace(port_offset=0), env=dict(env or {}), sleep=lambda s: None, wait_s=1.0,
                      show=lambda url, front=True, env=None: calls["show"].append(url) or {"ok": True, "how": ["front"]},
                      show_panel=lambda url, player, env=None: calls["panel"].append((url, player)) or {"ok": True,
                                                                                                       "how": ["front"]})
    return rc, calls


def test_rule_1_desk_wall_up_the_dock_shows_the_control_panel_never_a_second_wall(L, monkeypatch):
    rc, c = _press(L, monkeypatch)
    assert rc == 0 and c["panel"] == [(PANEL, PLAYER)] and c["show"] == []


def test_rule_1_no_control_panel_answering_opens_nothing(L, monkeypatch):
    rc, c = _press(L, monkeypatch, panel_up=False)
    assert rc == 1 and c["panel"] == [] and c["show"] == [], "never the Wall beside the desk Wall"


def test_rule_3_no_desk_wall_the_dock_opens_her_wall_as_before(L, monkeypatch):
    rc, c = _press(L, monkeypatch, desk=False)
    assert rc == 0 and c["show"] == [PLAYER + "/wall"] and c["panel"] == []


def test_rule_4_not_running_she_is_started_then_the_rules_apply(L, monkeypatch):
    rc, c = _press(L, monkeypatch, running=False)
    assert rc == 0 and c["started"] == [1] and c["panel"] == [(PANEL, PLAYER)]


def test_rule_5_setup_due_wins(L, monkeypatch):
    rc, c = _press(L, monkeypatch, due=True)
    assert rc == 0 and c["show"] == [PLAYER + "/init"] and c["panel"] == []


def test_the_kill_switch_is_the_old_dock(L, monkeypatch):
    rc, c = _press(L, monkeypatch, env={L.DOCK_PANEL_SWITCH: "0"})
    assert rc == 0 and c["show"] == [PLAYER + "/wall"] and c["panel"] == []


# ── her window moves to the Control Panel (a stand-in browser that follows Page.navigate) ───────────────────────────
@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    acc = _load("accept_r14", ROOT / "scripts" / "public-loops-accept.py")
    f = tmp_path / "fake-browser"
    f.write_text(acc._FAKE_BROWSER.replace("@PYTHON@", sys.executable), encoding="utf-8")
    f.chmod(0o755)
    kids = []

    def start(app: str) -> dict:
        port = _free_port()
        p = subprocess.Popen([str(f), "--user-data-dir=%s" % (tmp_path / "browser"),
                              "--remote-debugging-port=%d" % port, "--app=" + app],
                             env=dict(os.environ, FAKE_BROWSER_LOG=str(tmp_path / "browser.log")),
                             start_new_session=True)
        kids.append(p)
        end = time.time() + 10
        while not W.up(port) and time.time() < end:
            time.sleep(0.05)
        return {"port": port, "env": {"CRISSA_PORT_BROWSER": str(port)}, "proc": p}

    def words() -> list:
        return [json.loads(ln)["what"] for ln in (tmp_path / "browser.log").read_text().splitlines()]
    yield types.SimpleNamespace(start=start, words=words)
    for p in kids:
        if p.poll() is None:
            p.kill()
            p.wait(5)


def test_her_wall_page_is_moved_to_the_control_panel_one_page_no_second_wall(browser):
    b = browser.start(PLAYER + "/wall")
    got = W.show_panel(PANEL, PLAYER, env=b["env"], popen=object())
    assert got["ok"] and got["launched"] is False and got["how"][:2] == ["moved from /wall", "navigated"], got
    ps = W.pages(b["port"])
    assert [p["url"] for p in ps] == [PANEL], "one page, on the Control Panel"
    assert "navigate " + PANEL in browser.words() and "front " + PANEL in browser.words()


def test_a_control_panel_already_shown_is_brought_forward_as_it_is(browser):
    b = browser.start(PANEL.rstrip("/") + "/panel")
    got = W.show_panel(PANEL, PLAYER, env=b["env"], popen=object())
    assert got["ok"] and got["how"] == ["front"], got
    assert not any(w.startswith("navigate") for w in browser.words()), "her own panel page is not reloaded"


def test_not_running_her_window_starts_on_the_control_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    started = []
    got = W.show_panel(PANEL, PLAYER, env={"DISPLAY": ":0", "CRISSA_PORT_BROWSER": str(_free_port())},
                       popen=lambda argv, **k: started.append(argv) or types.SimpleNamespace(pid=1),
                       which={"brave-browser": "/usr/bin/brave-browser"}.get, plat="linux", home=tmp_path,
                       wait_s=0.0, sleep=lambda s: None, run=lambda *a, **k: types.SimpleNamespace(stdout=""))
    assert started and "--app=" + PANEL in started[0], got


def test_off_on_the_house_and_under_pytest(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert W.show_panel(PANEL, PLAYER, env={})["why"] == "off"
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert W.show_panel(PANEL, PLAYER, env={})["why"] == "test"


# ── rule 2: the Wall on request, from the Control Panel ──────────────────────────────────────────────────────────────
def test_the_control_panel_offers_open_the_wall_in_this_window(monkeypatch):
    monkeypatch.setenv("CRISSA_PORT_OFFSET", "80")
    monkeypatch.delenv("CRISSA_PORT_PLAYER", raising=False)
    panel = _load("crissa_panel_r14", ROOT / "scripts" / "crissa-panel-server.py")
    assert panel.wall_page() == "http://127.0.0.1:8180/wall"
    assert panel.wall_view()["page"] == "http://127.0.0.1:8180/wall"
    html = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert '<a class="key" id="bWallHere" target="_top" hidden' in html and "Open the Wall in this window" in html
    paint = html.split("function paintWall(){")[1].split("\nfunction ")[0]
    assert "h.href=WALL.page" in paint and "h.hidden=!WALL.page" in paint


def test_the_fake_browser_follows_navigation_for_the_acceptance():
    src = (ROOT / "scripts" / "public-loops-accept.py").read_text()
    fake = src.split("_FAKE_BROWSER = r\"\"\"")[1].split('"""')[0]
    assert 'pg["url"] = str(prm.get("url") or pg["url"])' in fake and '"url": x["url"]' in fake
