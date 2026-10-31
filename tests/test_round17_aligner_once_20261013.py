"""Round 17 (2026-10-13, .101 RESULTS-20261013-0630 "Addendum 08:40"): ONE ALIGNER, THE LAUNCHER'S.

.101 saw: after Open Agent the player had started its own aligner (crissa-align.py --port 8195 --parent <player pid>,
in crissa-player.service's cgroup), so crissa-aligner.service failed "Address already in use" every 2 s: 3,453 restarts
by 08:28. Alignment worked (the player's copy answered :8195/health), and the watch read the lane up.

The cause: the player boots while Close Agent holds the aligner lane (a start with the close still recorded, or the
player restarted while closed), lib/crissa_aligner.boot() finds nothing on :8195 and starts its own; Open Agent then
starts the lane's unit into the taken port; its unit's Restart=on-failure + RestartSec=2 never trips systemd's default
start limit (5 starts in 10 s), and the watch reads a unit waiting to restart as alive.

Pinned here: the player never starts one while the launcher runs the lane (CRISSA_ALIGNER_ONE_OWNER=0 is round 16); the
aligner binds before it warms and ends a taken port with 3, which its unit counts as clean (CRISSA_ALIGN_PORT_TAKEN_EXIT=0);
the watch and Open Agent hold a lane whose port another copy serves, and start its own when that copy stops
(CRISSA_LANE_PORT_SERVED=0). The real scenarios run the real aligner server (its MFA warm stubbed), the real launcher
Group and agent_pass, the real watch and the real player-side module, on a free loopback port.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_home  # noqa: E402
import crissa_procs  # noqa: E402
import crissa_service as S  # noqa: E402
import crissa_watch as WT  # noqa: E402

ALIGN = ROOT / "scripts" / "crissa-align.py"
KILLS = ("CRISSA_ALIGNER_ONE_OWNER", "CRISSA_LANE_PORT_SERVED", "CRISSA_ALIGN_PORT_TAKEN_EXIT", "CRISSA_ALIGNER_LANE")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _answers(port: int) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=0.5) as r:
            return bool(json.loads(r.read() or b"{}").get("ok"))
    except (OSError, ValueError):
        return False


def _until(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@pytest.fixture
def pack(monkeypatch, tmp_path):
    for k in KILLS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    return tmp_path


@pytest.fixture
def L(monkeypatch, pack):
    mod = _load("crissa_launcher_r17_aligner", ROOT / "scripts" / "crissa.py")
    monkeypatch.setattr(mod, "_log", lambda *a, **k: None)
    mod.AGENT_HELD.clear()
    mod.LATE_LANES.clear()
    return mod


@pytest.fixture
def A(monkeypatch, pack):
    """The player's side (lib/crissa_aligner) as a pack sees it, installed and on."""
    monkeypatch.setenv("CRISSA_HOME", str(pack / "home"))
    monkeypatch.delenv("CRISSA_BETTER_MOUTHS", raising=False)
    import crissa_aligner
    importlib.reload(crissa_home)
    mod = importlib.reload(crissa_aligner)
    monkeypatch.setattr(mod.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(mod, "installed", lambda: True)
    monkeypatch.setattr(mod, "better", lambda: True)
    yield mod
    mod.stop()
    monkeypatch.undo()
    importlib.reload(crissa_home)
    importlib.reload(crissa_aligner)


@pytest.fixture
def nowarm(tmp_path):
    """The real scripts/crissa-align.py with its MFA warm stubbed (no model, no MFA child): the server, its bind, its
    exit codes and its --parent watch are the real ones."""
    # a real module load (runpy.run_path hands back a COPY of the globals, so a patch there never reaches main()), and
    # guarded: a multiprocessing child re-imports the main module, and an unguarded one would serve the port again
    w = tmp_path / "align_nowarm.py"
    w.write_text("import importlib.util, sys\n"
                 "if __name__ == '__main__':\n"
                 "    spec = importlib.util.spec_from_file_location('crissa_align_nowarm', %r)\n"
                 "    m = importlib.util.module_from_spec(spec)\n"
                 "    spec.loader.exec_module(m)\n"
                 "    m._warm = lambda: m._M.update(warm=True)\n"
                 "    sys.exit(m.main(sys.argv[1:]))\n" % str(ALIGN))
    return w


class _Health(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        raw = b'{"ok": true, "engine": "mfa", "warm": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def other_copy():
    """Something healthy on a port: another aligner (the player's own, on .101)."""
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Health)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield port
    srv.shutdown()
    srv.server_close()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ── the aligner server: bind first, a taken port ends with 3 ────────────────────────────────────────────────────────
def test_the_real_aligner_ends_a_taken_port_with_3_before_it_warms(other_copy, tmp_path):
    env = dict(os.environ, HOUSE_ALIGN_MFA_BUNDLE=str(tmp_path / "no-model"))
    env.pop("CRISSA_ALIGN_PORT_TAKEN_EXIT", None)
    r = subprocess.run([sys.executable, str(ALIGN), "--port", str(other_copy)], env=env, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 3, (r.returncode, r.stderr[-400:])
    assert "already in use" in r.stderr and "not starting a second one" in r.stderr
    assert "warm" not in r.stdout and "could not warm" not in r.stderr, "no MFA warm on a taken port"
    assert "Traceback" not in r.stderr


def test_the_kill_puts_back_round_16s_traceback_and_exit_1(other_copy, tmp_path):
    env = dict(os.environ, HOUSE_ALIGN_MFA_BUNDLE=str(tmp_path / "no-model"), CRISSA_ALIGN_PORT_TAKEN_EXIT="0")
    r = subprocess.run([sys.executable, str(ALIGN), "--port", str(other_copy)], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 1 and "Address already in use" in r.stderr, (r.returncode, r.stderr[-400:])


def test_the_server_warms_only_after_its_port_is_its_own():
    src = ALIGN.read_text(encoding="utf-8")
    body = src[src.index("    try:\n        srv = ThreadingHTTPServer"):]
    assert body.index("srv = ThreadingHTTPServer") < body.index("threading.Thread(target=_warm"), \
        "bind, then warm: a taken port costs no MFA warm"


# ── its unit: 3 is a clean end, never retried every 2 s ─────────────────────────────────────────────────────────────
def test_the_aligners_unit_counts_a_taken_port_as_a_clean_end(pack):
    t = S.desk_unit_text("aligner", ["/py", "crissa-align.py"], {}, home=pack)
    assert "SuccessExitStatus=3\n" in t and "Restart=on-failure\nRestartSec=2\n" in t
    assert "SuccessExitStatus" not in S.desk_unit_text("asr", ["/py"], {}, home=pack), "the aligner's alone"
    assert "SuccessExitStatus" not in S.desk_unit_text("aligner", ["/py"], {"CRISSA_ALIGN_PORT_TAKEN_EXIT": "0"},
                                                       home=pack), "the kill: round 16's unit"


def test_why_the_unit_looped_unseen_systemds_window_never_trips_on_a_2_s_delay():
    """systemd's ratelimit_below (a fixed window from the first start): 5 starts in 10 s. A unit that fails at once and
    waits RestartSec=2 starts every 2.0 s + its run: the 6th start always lands after the window. .101's measured cycle
    was ~2.2 s (3,453 restarts from about 06:23 to 08:28)."""
    def trips(cycle, burst=5, interval=10.0, n=4000):
        begin, num = None, 0
        for i in range(n):
            ts = i * cycle
            if begin is None or ts - begin > interval:
                begin, num = ts, 1
                continue
            num += 1
            if num > burst:
                return True
        return False
    assert not trips(2.17) and not trips(2.01), "RestartSec=2 never reaches the default start limit"
    assert trips(1.9), "only a cycle under 2 s would have"


# ── the player: never a second copy while the launcher runs the lane ────────────────────────────────────────────────
def test_the_launcher_tells_the_player_it_runs_the_aligner(L):
    def args(**kw):
        base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False)
        base.update(kw)
        return types.SimpleNamespace(**base)
    by = {k["name"]: k for k in L.plan(args(with_aligner=True), env_in={})["children"]}
    assert by["player"]["env"]["CRISSA_ALIGNER_LANE"] == "1" and "aligner" in by
    by = {k["name"]: k for k in L.plan(args(), env_in={})["children"]}
    assert "CRISSA_ALIGNER_LANE" not in by["player"]["env"], "no lane: the player's own copy is the design"


def test_the_player_never_starts_one_while_the_lane_is_the_launchers(A, monkeypatch):
    started = []
    monkeypatch.setattr(A.subprocess, "Popen", lambda *a, **k: started.append(a) or types.SimpleNamespace(
        pid=1, poll=lambda: None))
    monkeypatch.setattr(A, "running", lambda: False)
    monkeypatch.setattr(A, "_port_free", lambda: True)
    (A.ROOT).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CRISSA_ALIGNER_LANE", "1")
    A.boot()
    assert A.start() is False and started == [] and A._STATE["proc"] is None
    assert A.status()["owner"] == "launcher"
    assert A.set_better(True)["ok"] and started == [], "the Connect page's tick does not start a copy either"
    monkeypatch.setenv("CRISSA_ALIGNER_ONE_OWNER", "0")             # the kill: round 16
    A.boot()
    assert len(started) == 1 and A.status()["owner"] == "player"
    A._STATE["proc"] = None                                         # a stand-in: nothing for stop() to signal


def test_a_player_with_no_lane_starts_its_own_as_before(A, monkeypatch):
    started = []
    monkeypatch.setattr(A.subprocess, "Popen", lambda *a, **k: started.append(a) or types.SimpleNamespace(
        pid=1, poll=lambda: None))
    monkeypatch.setattr(A, "running", lambda: False)
    monkeypatch.setattr(A, "_port_free", lambda: True)
    (A.ROOT).mkdir(parents=True, exist_ok=True)
    A.boot()                     # by hand, or Init's install after the launcher planned its lanes
    assert len(started) == 1 and A.status()["owner"] == "player"
    A._STATE["proc"] = None


def test_the_house_is_untouched(monkeypatch):
    import crissa_aligner as A0
    monkeypatch.setattr(A0.crissa_home, "is_house", lambda *a, **k: True)
    monkeypatch.setenv("CRISSA_ALIGNER_LANE", "1")
    monkeypatch.setattr(A0, "running", lambda: False)
    st = A0.status()
    assert A0.applicable() is False and "owner" not in st, "the house's /v1/mouths answer has the keys it had"
    assert A0.start() is False and A0._STATE["proc"] is None


# ── the watch: a port another copy serves is held, said once, its own started when that copy stops ─────────────────
def _stub_watch(pack, taken, healthy, kids=None, cfg=None):
    class G:
        def __init__(self):
            self.procs = {"aligner": types.SimpleNamespace(pid=11, returncode=1, stopped_clean=False)}
            self.spawned, self.marks = [], []
            self.table = types.SimpleNamespace(mark=lambda n, **kw: self.marks.append((n, kw)), write=lambda: None,
                                               child=lambda n: {})

        def alive(self, n):
            return self.procs[n].returncode is None

        def spawn(self, n, argv, env, port=None):
            self.spawned.append(n)
            self.procs[n] = types.SimpleNamespace(pid=12, returncode=3, stopped_clean=False)   # exits 3 at once

        def stop_one(self, n):
            self.procs[n].returncode = -15
    g, clock, said, asked = G(), Clock(), [], []

    def check(url, timeout):
        asked.append(url)
        return (True, "answered 200") if healthy["v"] else (False, "ConnectionRefusedError")
    kids = kids or {"aligner": {"argv": ["py", "crissa-align.py"], "env": {}, "port": 18195}}
    c = dict(WT.load(pack), restart=list(kids), health={"aligner": "/health"}, **(cfg or {}))
    w = WT.Watch(kids, g, cfg=c, home=pack, log=said.append, now=clock, check=check, tail=lambda n: "port in use",
                 taken=lambda port: taken["v"])
    return w, g, clock, said, asked


def test_a_lane_whose_port_another_copy_serves_is_held_and_said_once(pack):
    taken, healthy = {"v": True}, {"v": True}
    w, g, clock, said, asked = _stub_watch(pack, taken, healthy)
    for _ in range(1200):                         # ten minutes of the launcher's loop (0.5 s a pass)
        clock.t += 0.5
        w.tick()
    assert g.spawned == [], "never started into a port another copy serves"
    assert w.st["aligner"]["state"] == "served" and w.st["aligner"]["restarts"] == 0
    log = (pack / "logs" / "lanes.log").read_text()
    assert log.count("another copy already answers on its port :18195") == 1 and "restarted" not in log
    assert 55 <= len(asked) <= 62, "looked at again every health_every_s (10 s), not every pass: %d" % len(asked)
    assert json.loads((pack / WT.STATUS).read_text())["lanes"]["aligner"]["state"] == "served"
    assert g.marks and g.marks[-1][1]["state"] == "served", "the launcher's table says so too"
    taken["v"] = healthy["v"] = False             # that copy stops (its player restarted)
    clock.t += 10
    w.tick()
    assert g.spawned == ["aligner"], "this lane's own, at once"
    assert "the copy that served its port :18195 stopped answering" in w.st["aligner"]["last_why"]


def test_a_taken_port_that_does_not_answer_health_is_not_served(pack):
    w, g, clock, said, asked = _stub_watch(pack, {"v": True}, {"v": False})
    clock.t += 1
    w.tick()
    assert g.spawned == ["aligner"] and w.st["aligner"]["state"] != "served", "the back-off's business, as before"


def test_the_kill_starts_it_again_into_the_taken_port(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_LANE_PORT_SERVED", "0")
    w, g, clock, said, asked = _stub_watch(pack, {"v": True}, {"v": True})
    for _ in range(240):                          # two minutes
        clock.t += 0.5
        w.tick()
    assert len(g.spawned) >= 4, "round 16: started again on the back-off (2, 5, 15, 30 s)"
    assert w.st["aligner"]["state"] != "served"


def test_open_agent_asks_the_watch_before_it_starts_a_held_lane(L, pack):
    class Tab:
        def __init__(self):
            self.children = {}

        def child(self, n):
            return self.children.setdefault(n, {"name": n, "restarts": 0})

        def mark(self, n, **kw):
            self.child(n).update(kw)

        def exited(self, n, rc, expected=False, why=""):
            self.child(n).update(state="stopped" if expected else "exited", why=why)

        def write(self):
            pass

    class G:
        def __init__(self):
            self.procs = {"aligner": types.SimpleNamespace(pid=5, returncode=None, poll=lambda: None)}
            self.specs = {"aligner": (["py", "crissa-align.py"], {}, 18195)}
            self.table, self.spawned = Tab(), []

        def alive(self, n):
            return self.procs[n].returncode is None

        def stop_one(self, n):
            self.procs[n].returncode = 0
            return 0

        def spawn(self, n, argv, env, port=None):
            self.spawned.append(n)
    g, st = G(), {"closed": False}
    WT.set_closed(WT.AGENT, True, by="the desk Wall", home=pack)
    L.agent_pass(g, pack, st, close_window=lambda: "")
    WT.set_closed(WT.AGENT, False, home=pack)
    resets = []
    watch = types.SimpleNamespace(served=lambda n: "answered 200", reset_lanes=lambda lanes, why: resets.append(lanes))
    out = L.agent_pass(g, pack, st, watch=watch)
    assert g.spawned == [] and "aligner already served on :18195" in out
    assert g.table.children["aligner"]["state"] == "served" and resets == [[]]
    # a watch with no such question (an older stand-in) starts it as before
    st2 = {"closed": False}
    WT.set_closed(WT.AGENT, True, by="the desk Wall", home=pack)
    g.procs["aligner"] = types.SimpleNamespace(pid=6, returncode=None, poll=lambda: None)
    L.agent_pass(g, pack, st2, close_window=lambda: "")
    WT.set_closed(WT.AGENT, False, home=pack)
    L.agent_pass(g, pack, st2, watch=types.SimpleNamespace(reset_lanes=lambda lanes, why: None))
    assert g.spawned == ["aligner"]


# ── the panel: served is not red ────────────────────────────────────────────────────────────────────────────────────
def test_the_panels_say_served_and_never_red(pack, monkeypatch):
    t = crissa_procs.tile("aligner", {"state": "served", "why": "another copy already answers on its port :8195"},
                          True, "")
    assert t["health"] == "ok" and t["sub"] == "served" and ":8195" in t["note"]
    monkeypatch.setenv("CRISSA_HOME", str(pack))
    panel = _load("crissa_panel_r17_aligner", ROOT / "scripts" / "crissa-panel-server.py")
    import crissa_panel_conf as conf
    cfg = conf.load()
    cfg["_state"] = str(pack)
    cfg["os_power"] = dict(cfg.get("os_power") or {}, state_dir=str(pack / "panel"))
    panel.setup(cfg)
    monkeypatch.setattr(panel.crissa_watch, "read_status", lambda home: {"launcher_alive": True, "lanes": {
        "aligner": {"state": "served", "restarts": 0, "last_why": WT.SERVED_WHY % (8195, "answered 200")}}})
    row = {r["lane"]: r for r in panel.lane_rows()}["aligner"]
    assert row["health"] == "ok" and "another copy already answers" in row["note"]


# ── the .101 sequence, real processes ───────────────────────────────────────────────────────────────────────────────
def _real(L, A, pack, nowarm, monkeypatch):
    """The launcher's Group (process mode) running the real aligner server as its lane, the real watch, and the
    player's side pointed at the same server and port."""
    port = _free_port()
    kid = {"argv": [sys.executable, str(nowarm), "--port", str(port)], "env": {}, "port": port}
    tab = crissa_procs.Table(pack, os.getpid(), ["test"])
    g = L.Group(pack / "logs", tab)
    clock = Clock()
    w = WT.Watch({"aligner": kid}, g, cfg=dict(WT.load(pack), restart=["aligner"], health={"aligner": "/health"}),
                 home=pack, log=lambda m: None, now=clock)
    env_dir = pack / "aligner-env" / "bin"
    env_dir.mkdir(parents=True)
    (env_dir / "python").write_text("#!/bin/sh\nexec %s \"$@\"\n" % sys.executable)
    (env_dir / "python").chmod(0o755)
    monkeypatch.setattr(A, "ENV", pack / "aligner-env")
    monkeypatch.setattr(A, "SERVER", nowarm)
    monkeypatch.setattr(A, "PORT", port)
    monkeypatch.setattr(A, "ROOT", pack / "aligner")
    (pack / "aligner").mkdir()
    return port, kid, g, w, clock


def _loop(L, g, w, pack, st, clock, seconds, step=5.0):
    for _ in range(int(seconds / step)):
        clock.t += step
        L.agent_pass(g, pack, st, watch=w)
        w.tick()
        time.sleep(0.05)


def _alive_copies(g, A) -> list:
    out = []
    if g.alive("aligner"):
        out.append(("lane", g.procs["aligner"].pid))
    p = A._STATE.get("proc")
    if p is not None and p.poll() is None:
        out.append(("player", p.pid))
    return out


def test_close_then_open_leaves_one_aligner_the_lanes(L, A, pack, nowarm, monkeypatch):
    port, kid, g, w, clock = _real(L, A, pack, nowarm, monkeypatch)
    try:
        spawned = []
        real_spawn = g.spawn
        g.spawn = lambda *a, **k: spawned.append(a[0]) or real_spawn(*a, **k)
        g.spawn("aligner", kid["argv"], kid["env"], port)
        assert _until(lambda: _answers(port)), "the lane's aligner answers"
        st = {"closed": False}
        WT.set_closed(WT.AGENT, True, by="the Crissa menu", home=pack)        # Close Agent
        _loop(L, g, w, pack, st, clock, 10)
        assert _until(lambda: not _answers(port)) and not g.alive("aligner")
        monkeypatch.setenv("CRISSA_ALIGNER_LANE", "1")                       # the player's env from plan()
        A.boot()                                                             # the player (re)boots while closed
        assert A._STATE["proc"] is None and not _answers(port), "no copy of its own"
        WT.set_closed(WT.AGENT, False, home=pack)                            # Open Agent
        _loop(L, g, w, pack, st, clock, 5)
        assert _until(lambda: _answers(port))
        _loop(L, g, w, pack, st, clock, 120)
        copies = _alive_copies(g, A)
        assert [c[0] for c in copies] == ["lane"], copies
        assert spawned == ["aligner", "aligner"], "started at the start and by Open Agent, nothing else"
        assert w.st["aligner"]["restarts"] == 0 and w.st["aligner"]["state"] == "up"
    finally:
        g.stop()


def test_round_16s_sequence_is_held_not_looped_and_the_lane_takes_over(L, A, pack, nowarm, monkeypatch):
    """With the player's guard killed (an older player, or CRISSA_ALIGNER_ONE_OWNER=0) the player's copy takes :8195
    while the agent is closed, as on .101. Open Agent and the watch see it served: no second start, no loop. When the
    player's copy goes (its player restarts) the lane's own starts: still one aligner."""
    monkeypatch.setenv("CRISSA_ALIGNER_ONE_OWNER", "0")
    port, kid, g, w, clock = _real(L, A, pack, nowarm, monkeypatch)
    try:
        spawned = []
        real_spawn = g.spawn
        g.spawn = lambda *a, **k: spawned.append(a[0]) or real_spawn(*a, **k)
        g.spawn("aligner", kid["argv"], kid["env"], port)
        assert _until(lambda: _answers(port))
        st = {"closed": False}
        WT.set_closed(WT.AGENT, True, by="the Crissa menu", home=pack)
        _loop(L, g, w, pack, st, clock, 10)
        assert _until(lambda: not _answers(port))
        monkeypatch.setenv("CRISSA_ALIGNER_LANE", "1")
        A.boot()
        assert A._STATE["proc"] is not None and _until(lambda: _answers(port)), "round 16: the player's own copy"
        WT.set_closed(WT.AGENT, False, home=pack)
        _loop(L, g, w, pack, st, clock, 600)                                 # ten minutes after Open Agent
        assert spawned == ["aligner"], "never started into the copy's port: %s" % spawned
        assert w.st["aligner"]["state"] == "served" and [c[0] for c in _alive_copies(g, A)] == ["player"]
        log = (pack / "logs" / "lanes.log").read_text()
        assert log.count("another copy already answers on its port") == 1
        A.stop()                                                             # the player's copy goes
        assert _until(lambda: not _answers(port))
        _loop(L, g, w, pack, st, clock, 15)
        assert _until(lambda: _answers(port)), "the lane's own took over"
        assert spawned == ["aligner", "aligner"] and [c[0] for c in _alive_copies(g, A)] == ["lane"]
    finally:
        A.stop()
        g.stop()


def test_with_every_kill_the_lane_is_started_into_the_taken_port_again(L, A, pack, nowarm, monkeypatch):
    """Round 16, with the watch's own back-off in place of systemd's 2 s: the real server exits 3 on each start."""
    monkeypatch.setenv("CRISSA_ALIGNER_ONE_OWNER", "0")
    monkeypatch.setenv("CRISSA_LANE_PORT_SERVED", "0")
    port, kid, g, w, clock = _real(L, A, pack, nowarm, monkeypatch)
    try:
        spawned = []
        real_spawn = g.spawn
        g.spawn = lambda *a, **k: spawned.append(a[0]) or real_spawn(*a, **k)
        A.boot()
        assert _until(lambda: _answers(port))
        st = {"closed": True}
        WT.set_closed(WT.AGENT, True, by="the Crissa menu", home=pack)
        L.AGENT_HELD.add("aligner")
        L.LATE_LANES["aligner"] = dict(kid, name="aligner")
        WT.set_closed(WT.AGENT, False, home=pack)
        for _ in range(120):                                                 # two minutes, one pass a second
            clock.t += 1.0
            L.agent_pass(g, pack, st, watch=w)
            w.tick()
            _until(lambda: "aligner" not in g.procs or not g.alive("aligner"), 5.0)
        assert len(spawned) >= 4, "started again on the watch's back-off (2, 5, 15, 30, 60 s): %s" % spawned
        assert g.procs["aligner"].poll() == 3, "the real server ends a taken port with 3"
    finally:
        A.stop()
        g.stop()
