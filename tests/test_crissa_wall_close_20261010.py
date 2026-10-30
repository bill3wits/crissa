"""A Wall the person closes stays closed (the User 2026-10-10, .101: `systemctl --user stop crissa-wall.service` and the
launcher's watch started the Wall again within a second, full screen; only stopping every unit gave the desktop back).

In process mode and in service mode: a deliberate close (Ctrl+Q / Close the Wall record it; a clean exit, a SIGTERM, or
its unit stopped by systemctl) is never restarted and is recorded in the state home, where it holds across launcher
starts until the person opens the Wall again; a crash is restarted as before. The process-mode cases run real child
processes through the launcher's own Group; the service-mode cases drive the launcher's UnitProc with a stand-in
systemctl that answers as systemd does for a stopped unit (Result=success) and a crashed one (failed)."""
from __future__ import annotations

import importlib.util
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_service as S  # noqa: E402
import crissa_watch as W  # noqa: E402

CFG = dict(W.FALLBACK, restart=["wall", "panel"], closable=["wall"], backoff_s=[0], health={})


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_close", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_until(watch, group, name, s=4.0):
    end = time.time() + s
    while time.time() < end:
        watch.tick()
        time.sleep(0.05)


def _kid(code):
    return {"argv": [sys.executable, "-c", code], "env": {}, "port": None}


# ---- process mode: real children through the launcher's Group --------------------------------------------------
def test_process_mode_a_deliberate_close_stays_closed_and_a_crash_is_restarted(tmp_path):
    z = _launcher()
    g = z.Group(tmp_path / "logs", None, use_units=False)
    kids = {"wall": _kid("import time; time.sleep(0.3)"),                  # exits 0: closed (Ctrl+Q's exit)
            "panel": _kid("import os, signal, time; time.sleep(0.3); os.kill(os.getpid(), signal.SIGSEGV)")}
    for n, k in kids.items():
        g.spawn(n, k["argv"], k["env"])
    watch = W.Watch(kids, g, cfg=CFG, home=tmp_path, log=lambda m: None)
    _run_until(watch, g, "wall", 2.0)
    assert watch.st["wall"]["state"] == "closed" and watch.st["wall"]["restarts"] == 0
    assert W.closed(tmp_path, "wall")["by"] == "it exited cleanly (closed)"
    assert watch.st["panel"]["restarts"] >= 1                               # a crash (SIGSEGV) is restarted
    # a crash of the Wall itself is restarted too
    W.set_closed("wall", False, home=tmp_path)
    g.spawn("wall", [sys.executable, "-c", "import sys, time; time.sleep(0.2); sys.exit(3)"], {})
    watch2 = W.Watch({"wall": _kid("import time; time.sleep(30)")}, g, cfg=CFG, home=tmp_path, log=lambda m: None)
    _run_until(watch2, g, "wall", 1.5)
    assert watch2.st["wall"]["restarts"] >= 1 and not W.closed(tmp_path, "wall")
    g.stop()


def test_process_mode_ctrl_q_and_the_menus_close_are_recorded_and_sigterm_counts_as_on_purpose(tmp_path):
    z = _launcher()
    g = z.Group(tmp_path / "logs", None, use_units=False)
    g.spawn("wall", [sys.executable, "-c", "import time; time.sleep(30)"], {})
    watch = W.Watch({"wall": _kid("")}, g, cfg=CFG, home=tmp_path, log=lambda m: None)
    g.stop_one("wall")                                                       # SIGTERM: someone stopped it
    _run_until(watch, g, "wall", 1.0)
    assert watch.st["wall"]["state"] == "closed" and watch.st["wall"]["restarts"] == 0
    assert W.closed(tmp_path, "wall")["by"] == "it was stopped (SIGTERM)"
    W.set_closed("wall", False, home=tmp_path)
    W.set_closed("wall", True, by="Ctrl+Q", home=tmp_path)                  # what the Tk Wall writes on Ctrl+Q
    assert W.closed(tmp_path, "wall") == {"closed": True, "by": "Ctrl+Q", "at": W.closed(tmp_path, "wall")["at"]}
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert 'root.bind("<Control-q>", lambda e: _close_wall("Ctrl+Q"))' in src
    assert 'crissa_watch.set_closed("wall", True, by=by, home=crissa_home.HOME)' in src
    g.stop()


# ---- service mode: the unit's own answer --------------------------------------------------------------------------
class Systemd:
    """systemctl --user is-active / show for one unit, as systemd answers them."""

    def __init__(self):
        self.state, self.result, self.calls = "active", "success", []

    def __call__(self, argv, timeout=0):
        self.calls.append(argv)
        verb = argv[2]
        if verb == "is-active":
            return (0 if self.state == "active" else 3), self.state + "\n", ""
        if verb == "show" and "Result" in argv:
            return 0, self.result + "\n", ""
        if verb == "show":
            return 0, "4242\n", ""
        return 0, "", ""


class UnitGroup:
    def __init__(self, run):
        self.procs = {"wall": S.UnitProc("crissa-wall.service", run, 4242)}
        self.spawned = []

    def alive(self, name):
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def stop_one(self, name):
        pass

    def spawn(self, name, argv, env, port=None):
        self.spawned.append(name)


def test_service_mode_a_unit_stopped_by_systemctl_stays_stopped(tmp_path):
    sd = Systemd()
    g = UnitGroup(sd)
    watch = W.Watch({"wall": _kid("")}, g, cfg=CFG, home=tmp_path, log=lambda m: None)
    watch.tick()
    assert watch.st["wall"]["state"] == "up"
    sd.state, sd.result = "inactive", "success"                              # systemctl --user stop crissa-wall.service
    for _ in range(5):
        watch.tick()
    assert g.spawned == [] and watch.st["wall"]["state"] == "closed"
    assert g.procs["wall"].returncode == 0 and g.procs["wall"].stopped_clean is True
    assert W.closed(tmp_path, "wall")["by"] == "its unit was stopped (systemctl stop)"


def test_service_mode_a_crashed_unit_is_started_again(tmp_path):
    sd = Systemd()
    g = UnitGroup(sd)
    watch = W.Watch({"wall": _kid("")}, g, cfg=CFG, home=tmp_path, log=lambda m: None)
    sd.state, sd.result = "failed", "core-dump"
    watch.tick()
    assert g.spawned == ["wall"] and not W.closed(tmp_path, "wall")
    sd2 = Systemd()
    g2 = UnitGroup(sd2)
    watch2 = W.Watch({"wall": _kid("")}, g2, cfg=CFG, home=tmp_path, log=lambda m: None)
    sd2.state, sd2.result = "inactive", "exit-code"                          # ended badly, not restarted by systemd
    watch2.tick()
    assert g2.spawned == ["wall"]


def test_the_close_holds_across_launcher_starts_until_it_is_opened_again(tmp_path, monkeypatch):
    """A closed Wall is not started by the next launcher start; Open the Wall (menu, panel) or --open-wall does."""
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    i = src.index('if kid["name"] == "wall" and crissa_watch.closed(crissa_home.HOME, "wall"):')
    assert 'LATE_LANES["wall"] = kid' in src[i:i + 700] and "continue" in src[i:i + 900]
    assert 'crissa_watch.set_closed("wall", False, home=crissa_home.HOME)' in src
    z = _launcher()
    assert z.main.__code__ is not None
    import crissa_home
    import crissa_menu as M
    import crissa_procs as P
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    P._write(P.table_path(tmp_path), {"launcher": {"pid": 1, "beat": time.time(), "state": "running"},
                                       "children": {"wall": {"state": "closed"}}})
    got = M.wall_close(tmp_path, by="the Control Panel")
    assert got["ok"] and W.closed(tmp_path, "wall")["by"] == "the Control Panel"
    assert P.take_requests(tmp_path)[0]["action"] == "stop"
    assert M.wall_state(tmp_path)["action"] == "wall_open" and M.wall_state(tmp_path)["label"] == "Open the Wall"
    got = M.wall_open(tmp_path)
    assert got["ok"] and not W.closed(tmp_path, "wall") and P.take_requests(tmp_path)[0]["action"] == "start"
    assert M.wall_state(tmp_path)["label"] == "Close the Wall"


def test_the_menu_and_the_panel_carry_close_and_open(tmp_path):
    wall_page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    panel_page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    panel = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    import crissa_wall_key as K
    # 2026-10-12 (the User, "Close Agent vs OS off"): the menus close the agent, not the Wall; they still open a
    # closed Wall, and the Control Panel keeps both Wall keys
    assert ("Close Agent", "agent_close", True) in K.menu_items({}, fullscreen=False)
    assert not any(vb == "wall_close" for _l, vb, _on in K.menu_items({}, fullscreen=False))
    assert 'if (wl.action && wl.closed)' in wall_page and 'menuPost({action: wl.action})' in wall_page
    assert 'id: "m-wall"' in wall_page
    assert 'id="bWall"' in panel_page and 'fetch("/v1/wall"' in panel_page
    assert 'if path == "/v1/wall":' in panel and "crissa_menu.wall_close(by=\"the Control Panel\")" in panel
