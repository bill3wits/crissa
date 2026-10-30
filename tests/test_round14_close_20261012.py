"""Round 14 (2026-10-12): Close Agent vs All OS Off, and every Crissa page in her own instance.

The User (.101, 09:2x): "We should turn that side into Close Agent vs OS off" and "make them match wherever possible" -
the same names and behaviour as the house. The house's names: "All OS Off" is its Control Panel key (lib/house_os_power:
every house service off but the panel; "Turn On All Services" brings them back) and its Mode chip's word for house-mode
off (lib/house_wall_ascii_render GPU_LABELS); the pack's Control Panel already says "All OS Off" (lib/crissa_os_power,
lifted from the house). The house has no word for letting the agent go while the OS stays: "Close Agent" is the User's.
  - Close Agent: Crissa the agent goes - every lane she listens or speaks with stops and stays stopped, her agent pages
    close (her Wall, Setup, Connect) - while the desk Wall, the player, the feed and the Control Panel stay. Open Agent
    (the menus, the Control Panel, her dock icon) brings her back. The watcher that keeps her Wall window open while the
    desk Wall is off stands down meanwhile.
  - All OS Off (the menus' item, "Stop Crissa" until today): everything of Crissa off; HH is the same from a terminal.
  - Every Crissa page (her local pages and the store) opens in her own instance, so Stop closes it; with no browser of
    her family the default browser opens it, and the log says so.
Stand-ins: a fake lane group, the acceptance's stand-in browser (her profile, a DevTools port with pages), tmp homes.
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
import crissa_menu as M  # noqa: E402
import crissa_procs as P  # noqa: E402
import crissa_watch as WT  # noqa: E402
import crissa_window as W  # noqa: E402

PLAYER = "http://127.0.0.1:8100"
PANEL = "http://127.0.0.1:8097/"
STORE = "https://store.zoeyos.dev/store/?from=http%3A%2F%2F127.0.0.1%3A8100"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def pack(monkeypatch, tmp_path):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    return tmp_path


# ── the names ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_all_os_off_is_the_houses_own_name_and_close_agent_the_users():
    assert M.LABELS["stop"] == "All OS Off" and M.LABELS["agent_close"] == "Close Agent"
    assert M.LABELS["agent_open"] == "Open Agent"
    render = (ROOT / "lib" / "house_wall_ascii_render.py").read_text()
    assert '"off": "All OS Off"' in render, "the house's Mode chip"
    assert "All OS Off / All OS On" in (ROOT / "lib" / "house_os_power.py").read_text(), "the house's Control Panel key"
    import crissa_panel_conf
    assert '"switch_name": "All OS Off"' in (ROOT / "lib" / "crissa_panel_conf.py").read_text()
    assert "All OS Off" in M.STOP_ASK and "plain desktop" in M.STOP_ASK
    assert "the Wall stays as your desktop" in M.AGENT_ASK


# ── Close Agent in the watch ────────────────────────────────────────────────────────────────────────────────────────
class _Proc:
    def __init__(self, alive=True):
        self.returncode = None if alive else 1

    def poll(self):
        return self.returncode


class FakeGroup:
    def __init__(self, names, dead=()):
        self.procs = {n: _Proc(n not in dead) for n in names}
        self.spawned, self.stopped = [], []
        self.specs = {n: (["x", n], {}, None) for n in names}
        self.table = types.SimpleNamespace(exited=lambda *a, **k: None)

    def alive(self, name):
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def stop_one(self, name):
        self.stopped.append(name)
        self.procs[name].returncode = -15
        return -15

    def spawn(self, name, argv, env, port=None):
        self.spawned.append(name)
        self.procs[name] = _Proc(True)


def _kid():
    return {"argv": [sys.executable, "-c", "pass"], "env": {}, "port": None}


def test_the_watch_holds_her_listening_and_speaking_lanes_while_the_agent_is_closed(pack):
    cfg = dict(WT.FALLBACK, restart=["stt", "ear", "panel"], closable=["wall"], backoff_s=[0], health={})
    g = FakeGroup(["stt", "ear", "panel"], dead=("stt", "ear", "panel"))
    w = WT.Watch({n: _kid() for n in ("stt", "ear", "panel")}, g, cfg=cfg, home=pack, log=lambda m: None)
    WT.set_closed(WT.AGENT, True, by="the Crissa menu", home=pack)
    for _ in range(3):
        w.tick()
    assert g.spawned == ["panel"], "the panel is the OS's: still brought back; her ear and STT are held"
    assert w.st["stt"]["state"] == "closed" and "Close Agent (the Crissa menu)" in w.st["stt"]["last_why"]
    WT.set_closed(WT.AGENT, False, home=pack)
    w.tick()
    assert sorted(g.spawned) == ["ear", "panel", "stt"], "open again: the watch is the watch again"


# ── Close Agent in the launcher ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def L(monkeypatch, pack):
    mod = _load("crissa_launcher_r14c", ROOT / "scripts" / "crissa.py")
    monkeypatch.setattr(mod, "_log", lambda *a, **k: None)
    mod.AGENT_HELD.clear()
    mod.LATE_LANES.clear()
    return mod


def test_close_agent_stops_her_lanes_once_closes_her_pages_and_leaves_the_os(L, pack):
    g = FakeGroup(["player", "wall", "feed", "panel", "stt", "ear", "pocket", "presence"])
    closes = []
    st = {"closed": False}
    WT.set_closed(WT.AGENT, True, by="the Control Panel", home=pack)
    did = L.agent_pass(g, pack, st, close_window=lambda: closes.append(1) or "closed /wall")
    # round 15: her eyes (presence here) go with her too; the player, the Wall, the feed and the panel stay
    assert sorted(g.stopped) == ["ear", "pocket", "presence", "stt"], "the player, the Wall, the feed, the panel stay"
    assert closes == [1] and st["closed"] is True and "her window: closed /wall" in did
    L.agent_pass(g, pack, st, close_window=lambda: closes.append(1) or "")
    assert closes == [1] and sorted(g.stopped) == ["ear", "pocket", "presence", "stt"], "once; nothing restarts behind it"
    WT.set_closed(WT.AGENT, False, home=pack)
    did = L.agent_pass(g, pack, st)
    assert sorted(g.spawned) == ["ear", "pocket", "presence", "stt"] and st["closed"] is False and L.AGENT_HELD == set()


def test_a_start_with_the_agent_closed_starts_her_lanes_only_on_open_agent(L, pack):
    g = FakeGroup(["player", "wall"])
    L.LATE_LANES["stt"] = {"argv": ["x", "stt"], "env": {}, "port": 8190}
    L.AGENT_HELD.add("stt")                       # what the start loop records for a lane it did not start
    st = {"closed": True}
    WT.set_closed(WT.AGENT, False, home=pack)
    L.agent_pass(g, pack, st)
    assert g.spawned == ["stt"] and g.specs["stt"] == (["x", "stt"], {}, 8190)
    src = (ROOT / "scripts" / "crissa.py").read_text()
    loop = src.split("CLOSE AGENT HOLDS ACROSS STARTS")[0][-400:]
    assert "crissa_watch.AGENT_LANES" in loop and "crissa_watch.agent_closed(crissa_home.HOME)" in loop


def test_the_watcher_that_keeps_her_wall_window_open_stands_down_after_close_agent(L, pack, monkeypatch):
    monkeypatch.setattr(L, "desk_is_off", lambda g, home=None: True)
    env = {"DISPLAY": ":0"}
    st = {"at": 0.0, "off": 5}
    WT.set_closed(WT.AGENT, True, by="x", home=pack)
    assert L.keep_window(PLAYER, object(), st, pack, env=env, ensure=lambda *a, **k: "started") == "agent closed"
    WT.set_closed(WT.AGENT, False, home=pack)
    st["at"] = 0.0
    monkeypatch.setattr(L, "init_needed", lambda env=None: False)
    assert L.keep_window(PLAYER, object(), st, pack, env=env, ensure=lambda *a, **k: "started") == "started", \
        "the watcher itself is wanted (\"just like it did\")"


def test_her_dock_icon_opens_the_agent_again(L, pack, monkeypatch):
    WT.set_closed(WT.AGENT, True, by="x", home=pack)
    monkeypatch.setattr(crissa_home, "HOME", pack)
    monkeypatch.setattr(L, "_player_up", lambda url, timeout=1.0: True)
    monkeypatch.setattr(L, "setup_due", lambda url, timeout=2.0: False)
    monkeypatch.setattr(L, "desk_wall_on", lambda *a, **k: False)
    rc = L.window_cmd(types.SimpleNamespace(port_offset=0), env={}, sleep=lambda s: None,
                      show=lambda url, front=True, env=None: {"ok": True, "how": ["front"]})
    assert rc == 0 and not WT.agent_closed(pack)


# ── Close Agent / All OS Off in the menus and the Control Panel ──────────────────────────────────────────────────────
def test_the_menu_closes_and_opens_the_agent_and_says_which(pack):
    assert M.agent_state(pack) == {"closed": False, "by": "", "action": "agent_close", "label": "Close Agent",
                                   "ask": M.AGENT_ASK}
    got = M.act("agent_close", {"by": "her window"}, home=pack)
    assert got["ok"] and WT.agent_closed(pack)["by"] == "her window"
    assert M.agent_state(pack)["label"] == "Open Agent" and M.agent_state(pack)["action"] == "agent_open"
    assert M.act("agent_open", {}, home=pack)["ok"] and not WT.agent_closed(pack)
    assert M.act("stop", {}, home=pack)["error"] == 'All OS Off needs {"confirm": true}'


def test_the_menus_and_the_panel_carry_close_agent_and_all_os_off(pack, monkeypatch):
    import crissa_wall_key as K
    items = K.menu_items({"agent": M.agent_state(pack), "stop": {"label": M.LABELS["stop"]}}, fullscreen=False)
    assert ("Close Agent", "agent_close", True) in items and ("All OS Off", "stop", True) in items
    assert not any(vb == "wall_close" for _l, vb, _on in items), "the menus no longer close the desk Wall"
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert 'id: "m-agent"' in page and 'if (wl.action && wl.closed)' in page and 'st.label || "All OS Off"' in page
    html = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert 'id="bAgent"' in html and 'fetch("/v1/agent"' in html
    monkeypatch.setenv("CRISSA_HOME", str(pack))
    panel = _load("crissa_panel_r14c", ROOT / "scripts" / "crissa-panel-server.py")
    monkeypatch.setattr(M, "agent_state", lambda home=None: {"closed": False, "label": "Close Agent"})
    assert panel.agent_view()["ok"] is True
    calls = []
    monkeypatch.setattr(M, "agent_close", lambda home=None, by="": calls.append(by) or {"ok": True})
    assert panel.agent_set("agent_close")["ok"] and calls == ["the Control Panel"]
    assert panel.agent_set("nope")["ok"] is False


# ── every Crissa page in her own instance ───────────────────────────────────────────────────────────────────────────
def test_her_pages_are_this_installs_local_pages_and_the_store(pack, monkeypatch):
    for url in (PLAYER + "/connect", PANEL, "http://localhost:8099/panel"):
        assert W.is_her_page(url), url
    # the Store opens in the person's own default browser (the User 2026-10-29); the switch brings back the 10-12 way
    assert not W.is_her_page(STORE)
    monkeypatch.setenv("CRISSA_STORE_IN_HER_WINDOW", "1")
    assert W.is_her_page(STORE)
    monkeypatch.delenv("CRISSA_STORE_IN_HER_WINDOW")
    for url in ("https://example.com/", "http://127.0.0.1:5000/", "file:///etc/passwd", "https://store.zoeyos.dev.evil/"):
        assert not W.is_her_page(url), url
    assert W.is_her_page("http://127.0.0.1:8180/connect", {"CRISSA_PORT_OFFSET": "80"})


@pytest.fixture
def browser(pack, monkeypatch):
    acc = _load("accept_r14c", ROOT / "scripts" / "public-loops-accept.py")
    f = pack / "fake-browser"
    f.write_text(acc._FAKE_BROWSER.replace("@PYTHON@", sys.executable), encoding="utf-8")
    f.chmod(0o755)
    kids = []

    def start(app: str, profile: Path | None = None) -> dict:
        port = _free_port()
        p = subprocess.Popen([str(f), "--user-data-dir=%s" % (profile or pack / "browser"),
                              "--remote-debugging-port=%d" % port, "--app=" + app],
                             env=dict(os.environ, FAKE_BROWSER_LOG=str(pack / "browser.log")), start_new_session=True)
        kids.append(p)
        end = time.time() + 10
        while not W.up(port) and time.time() < end:
            time.sleep(0.05)
        return {"port": port, "env": {"CRISSA_PORT_BROWSER": str(port)}, "proc": p}
    yield types.SimpleNamespace(start=start, urls=lambda port: [x["url"] for x in W.pages(port)])
    for p in kids:
        if p.poll() is None:
            p.kill()
            p.wait(5)


def test_the_store_connect_and_the_panel_open_as_pages_of_her_own_instance(browser, pack):
    b = browser.start(PLAYER + "/wall")
    for url in (STORE, PLAYER + "/connect", PANEL):
        got = W.open_page(url, env=b["env"], home=pack, popen=object())
        assert got["ok"] and got["how"][0] == "opened", (url, got)
    assert browser.urls(b["port"]) == [PLAYER + "/wall", STORE, PLAYER + "/connect", PANEL]
    got = W.open_page(PANEL, env=b["env"], home=pack, popen=object())
    assert got["ok"] and got["how"] == ["front"] and len(browser.urls(b["port"])) == 4, "the same page: brought forward"


def test_with_no_browser_of_her_family_the_caller_is_told_to_use_the_default_browser(pack):
    got = W.open_page(STORE, env={"DISPLAY": ":0", "CRISSA_PORT_BROWSER": str(_free_port())}, home=pack,
                      popen=object(), which=lambda n: None, plat="linux")
    assert got["why"] == "no browser"


def test_close_agent_closes_her_agent_pages_and_keeps_the_panel_and_the_store(browser, pack):
    b = browser.start(PLAYER + "/wall")
    for url in (STORE, PLAYER + "/connect", PANEL):
        W.open_page(url, env=b["env"], home=pack, popen=object())
    got = W.close_agent_pages(PLAYER, env=b["env"], home=pack)
    assert got["ok"] and got["how"] == ["closed /wall", "closed /connect"], got
    assert browser.urls(b["port"]) == [STORE, PANEL] and b["proc"].poll() is None


def test_close_agent_never_touches_a_browser_on_another_profile(browser, pack):
    b = browser.start(PLAYER + "/wall", profile=pack / "someone-elses")
    got = W.close_agent_pages(PLAYER, env=b["env"], home=pack)
    assert got["how"][0].startswith("not hers") and browser.urls(b["port"]) == [PLAYER + "/wall"]


def test_the_desk_walls_links_go_to_her_instance_and_say_so_when_they_cannot(monkeypatch):
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    body = src.split("def open_url(url: str) -> bool:")[1].split("\ndef ")[0]
    assert body.index("open_own_window(url)") < body.index("open_her_page(url)") < body.index("webbrowser.open(url)")
    her = src.split("def open_her_page(url: str):")[1].split("\ndef ")[0]
    assert "crissa_window.is_her_page(url)" in her and "crissa_window.open_page(url, front=True)" in her
    assert "has no Brave, Chromium or Google Chrome" in her and "default" in her
