"""Crissa restarts its own lanes (2026-10-06): krew's keeper only reads the ear through the player, so the launcher
brings back a lane that exits or stops answering /health, says why (logs/lanes.log), backs off, never gives up unless
the settings say so, serves the Control Panel's Restart, and the panel shows each lane (lib/crissa_watch.py,
scripts/crissa.py, scripts/crissa-panel-server.py)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_watch as zw  # noqa: E402


class P:
    _n = 1000

    def __init__(self):
        P._n += 1
        self.pid, self.returncode = P._n, None


class G:
    """A stand-in process group."""

    def __init__(self, names):
        self.procs = {n: P() for n in names}
        self.spawned, self.ports, self.stopped = [], [], []

    def alive(self, n):
        return self.procs[n].returncode is None

    def die(self, n, code=1):
        self.procs[n].returncode = code

    def spawn(self, n, argv, env, port=None):
        self.spawned.append(n)
        self.ports.append(port)
        self.procs[n] = P()

    def stop_one(self, n):
        self.stopped.append(n)
        self.procs[n].returncode = -15


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


KIDS = {"stt": {"argv": ["stt"], "env": {}, "port": 18190}, "asr": {"argv": ["asr"], "env": {}, "port": 18096},
        "ear": {"argv": ["ear"], "env": {}, "port": None}}


def _watch(tmp_path, cfg=None, answers=None):
    g, clock, said = G(KIDS), Clock(), []
    ans = answers if answers is not None else {}
    w = zw.Watch(KIDS, g, cfg=dict(zw.load(tmp_path), **(cfg or {})), home=tmp_path, log=said.append, now=clock,
                 check=lambda url, timeout: ans.get(url.split(":")[2].split("/")[0], (True, "answered 200")),
                 tail=lambda n: "Traceback ... boom")
    return w, g, clock, said, ans


def test_a_lane_that_exits_comes_back_with_its_reason_and_backs_off(tmp_path):
    w, g, clock, said, _ = _watch(tmp_path)
    g.die("stt", 3)
    w.tick()
    assert g.spawned == ["stt"] and g.ports == [18190]
    log = (tmp_path / "logs" / "lanes.log").read_text()
    assert "stt: went down: exited (code 3); its log ends: Traceback ... boom" in log
    assert "stt: restarted (restart 1): exited (code 3)" in log
    g.die("stt", 3)
    clock.t += 1
    w.tick()
    assert g.spawned == ["stt"]                       # back-off: 2 s before the second restart
    clock.t += 2
    w.tick()
    assert g.spawned == ["stt", "stt"]
    st = json.loads((tmp_path / "lanes.json").read_text())
    assert st["lanes"]["stt"]["restarts"] == 2 and st["launcher_pid"] == os.getpid()


def test_a_hung_lane_is_restarted_after_its_health_fails(tmp_path):
    w, g, clock, said, ans = _watch(tmp_path)
    w.tick()                                          # answers once: it is up
    ans["18190"] = (False, "URLError: timed out")
    for _ in range(3):
        clock.t += 10
        w.tick()
    assert g.stopped == ["stt"] and g.spawned == ["stt"]
    assert "its /health did not answer 3 times in a row (URLError: timed out)" in (tmp_path / "logs" / "lanes.log").read_text()


def test_a_lane_that_never_loads_is_restarted_after_its_grace(tmp_path):
    w, g, clock, said, ans = _watch(tmp_path, answers={"18096": (False, "ConnectionRefusedError")})
    for _ in range(11):                               # inside its 120 s to load: not judged
        w.tick()
        clock.t += 10
    assert g.spawned == []
    for _ in range(3):
        clock.t += 10
        w.tick()
    assert g.spawned == ["asr"] and "never answered /health in its 120 s to load" in w.st["asr"]["last_why"]


def test_the_panels_restart_and_give_up(tmp_path):
    w, g, clock, said, _ = _watch(tmp_path, cfg={"give_up_after": 1})
    zw.request_restart("ear", tmp_path)
    w.tick()
    assert g.spawned == ["ear"] and w.st["ear"]["last_why"] == "asked from the Control Panel"
    g.die("asr")
    w.tick()                                          # first fall: restarted
    g.die("asr")
    clock.t += 100
    w.tick()                                          # second in a row: given up, said so
    assert g.spawned == ["ear", "asr"] and w.st["asr"]["state"] == "given_up"
    assert "restart it from the Control Panel" in (tmp_path / "logs" / "lanes.log").read_text()
    zw.request_restart("asr", tmp_path)
    w.tick()
    assert g.spawned == ["ear", "asr", "asr"]          # the panel can always bring it back


def test_settings_say_no_hidden_limits():
    cfg = json.loads((ROOT / "config" / "crissa-watch.json").read_text())
    assert cfg["give_up_after"] == 0 and {"asr", "stt", "ear"} <= set(cfg["restart"])
    src = (ROOT / "scripts" / "crissa.py").read_text()
    assert "watch.tick()" in src and "crissa_watch.Watch(" in src and "def stop_one" in src


def _panel(tmp_path):
    spec = importlib.util.spec_from_file_location("crissa_panel_t", ROOT / "scripts" / "crissa-panel-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CFG = {"_state": str(tmp_path), "port": 18097}
    return m


def test_the_panel_shows_each_lane_and_asks_the_launcher(tmp_path):
    w, g, clock, said, _ = _watch(tmp_path)
    g.die("stt", 3)
    w.tick()
    m = _panel(tmp_path)
    rows = {r["unit"]: r for r in m.lane_rows()}
    assert rows["lane:stt"]["section"] == "Crissa lanes" and rows["lane:stt"]["restarts"] == 1
    assert rows["lane:stt"]["health"] == "warn" and "exited (code 3)" in rows["lane:stt"]["note"]
    assert rows["lane:asr"]["health"] == "ok"
    code, out = m.do_action({"action": "restart", "unit": "lane:asr"})
    assert out["ok"] and (tmp_path / "lanes-restart" / "asr").exists()
    assert "restarted (restart 1)" in "\n".join(m.lane_journal("stt", 50)["lines"])
    st = json.loads((tmp_path / "lanes.json").read_text())
    st["launcher_pid"] = 2 ** 22 + 12345                # a launcher that is gone
    (tmp_path / "lanes.json").write_text(json.dumps(st))
    assert "the launcher is not running" in m.lane_rows()[0]["note"]
    code, out = m.do_action({"action": "restart", "unit": "lane:asr"})
    assert not out["ok"] and "not running" in out["note"]
