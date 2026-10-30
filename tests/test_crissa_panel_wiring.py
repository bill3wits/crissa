"""Every tile can show its logs, restart what it stands for, and name why it is down (the User 2026-10-06: "every service
must be visible and controllable in the Control Panel and failures must be named").

  * Logs: a page at a time, newest last, with an explicit marker for what is above it and the token that reads it;
    the whole log as text on its own route. No line is cut and the number of lines asked for is not capped.
  * Restart: a unit's restart is CHECKED (a new run, still up after a settle, its port answering) and a fall-over is
    named; a timer's Restart is "Run now"; the panel restarting itself answers first.
  * Failure named: a down unit's tile says systemd's Result (exit code, signal, start limit, ...) and the journal line
    that says what went wrong - never just a red lamp.
  * The Crissa launcher's children (player, Kokoro, Chatterbox Turbo / Multilingual, Pocket, ASR, STT, ear, aligner)
    are tiles too (lib/crissa_procs): their log files, a Restart the launcher carries out on that child alone, and the
    exit code + log line when one stops.

Harness: tests/test_crissa_panel.py's fresh install and stand-in systemd. Launcher tests start throwaway python
children (silent: they open no audio device) in a tmp state home and kill every one by PID at the end.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import test_crissa_panel as base

ROOT = base.ROOT
sys.path.insert(0, str(ROOT / "lib"))
import crissa_procs as P  # noqa: E402


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_w", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _kill_all(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


# ---- reading a log a page at a time ---------------------------------------------------------------------------------
def test_a_log_reads_a_page_at_a_time_with_a_marker_and_never_cuts_a_line(tmp_path):
    f = tmp_path / "turbo.log"
    long_line = "x" * 20000
    f.write_text("".join(f"line {i}\n" for i in range(1000)) + long_line + "\n" + "last\n")
    a = P.read_log(f, 100)
    assert a["lines"][-1] == "last" and a["lines"][-2] == long_line          # a 20 000-character line arrives whole
    assert len(a["lines"]) == 100 and a["lines"][0] == "line 902"
    assert a["earlier"] == a["start"] > 0 and "earlier in turbo.log not shown" in a["marker"]
    b = P.read_log(f, 100, a["earlier"])
    assert b["lines"][0] == "line 802" and b["lines"][-1] == "line 901"      # the page right above, no gap, no overlap
    seen, before = [], None
    while True:
        page = P.read_log(f, 300, before)
        seen = page["lines"] + seen
        if "earlier" not in page:
            break
        before = page["earlier"]
    assert seen == f.read_text().splitlines()                                 # every line, in order, by paging back
    whole = P.read_log(f, 50000)                                              # the count asked for is not capped
    assert len(whole["lines"]) == 1002 and "marker" not in whole


def test_the_reason_a_child_stopped_is_its_exit_and_the_line_that_says_why(tmp_path):
    log = tmp_path / "turbo.log"
    log.write_text("--- 2026-10-06 10:00:00 start\nloading model\nTraceback (most recent call last):\n"
                   '  File "x.py", line 3, in <module>\nRuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB\n')
    assert P.exit_reason(1, log) == "exit 1: RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB"
    assert P.exit_reason(-9, log).startswith("killed by SIGKILL: RuntimeError")
    assert P.exit_reason(0, None) == "exited cleanly (exit 0)"
    assert P.pick_reason(["ok", "serving on :8186"]) == "serving on :8186"


# ---- the launcher carries out the panel's Restart / Stop / Start on one child -----------------------------------
LISTENER = ("import socket, sys, time\n"
            "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen(5); print('serving', flush=True); time.sleep(600)\n")
CRASH = "import sys; print('loading voice', flush=True); raise RuntimeError('CUDA out of memory: tried to allocate 2 GiB')\n"


def _wait(fn, what, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.1)
    raise AssertionError("timed out: " + what)


def test_the_launcher_restarts_stops_and_starts_one_child_and_names_a_crash(tmp_path, monkeypatch):
    z = _launcher()
    monkeypatch.setattr(z, "STOP_WAIT_S", 2.0)
    home = tmp_path / "home"
    table = P.Table(home, os.getpid(), ["crissa.py"])
    g = z.Group(home / "logs", table)
    pids = []
    port = _free_port()
    try:
        player = g.spawn("player", [sys.executable, "-c", LISTENER, str(_free_port())], {}, None)
        turbo = g.spawn("turbo", [sys.executable, "-c", LISTENER, str(port)], {}, port)
        ear = g.spawn("ear", [sys.executable, "-c", "import time; time.sleep(600)"], {}, None)
        pids += [player.pid, turbo.pid, ear.pid]
        _wait(lambda: z.crissa_ports.in_use(port), "turbo listening")
        v = P.view(home)
        by = {t["proc"]: t for t in v["tiles"]}
        assert v["present"] and v["launcher"]["alive"]
        assert by["turbo"]["unit"] == "proc:turbo" and by["turbo"]["health"] == "ok" and by["turbo"]["section"] == "Voices"
        assert by["turbo"]["label"].startswith("Chatterbox Turbo") and by["turbo"]["log"].endswith("turbo.log")

        # Restart: that child alone, a new pid, its port answers, the others untouched
        r = P.request(home, "turbo", "restart")
        assert r["ok"] and r["job"]
        threads = []
        for req in P.take_requests(home):
            z.handle_request(g, home, req, threads)
        for t in threads:
            t.join(60)
        done = P.report(home, "turbo", r["job"])
        assert done["state"] == "done" and done["ok"], done
        assert "answering" in done["note"] and g.procs["turbo"].pid != turbo.pid
        pids.append(g.procs["turbo"].pid)
        assert turbo.poll() is not None and g.alive("player") and g.alive("ear")
        # a Restart from the panel is planned: the tile's count is for restarts nobody asked for (round 4, 2026-10-10)
        assert table.child("turbo")["planned_restarts"] == 1 and table.child("turbo")["restarts"] == 0

        # Stop: parked, not a crash; Start brings it back
        r = P.request(home, "ear", "stop")
        for req in P.take_requests(home):
            z.handle_request(g, home, req, threads)
        for t in threads:                 # a Stop runs beside the heartbeat loop since 2026-10-24 (CRISSA_STOP_OFF_LOOP)
            t.join(60)
        assert P.report(home, "ear", r["job"])["ok"] and not g.alive("ear")
        t = {x["proc"]: x for x in P.view(home)["tiles"]}["ear"]
        assert t["health"] == "warn" and t.get("parked") and "stopped from the panel" in t["note"]
        r = P.request(home, "ear", "start")
        for req in P.take_requests(home):
            z.handle_request(g, home, req, threads)
        for th in threads:
            th.join(30)
        assert P.report(home, "ear", r["job"])["ok"] and g.alive("ear")
        pids.append(g.procs["ear"].pid)

        # the player is the Wall itself: Restart yes, Stop never
        r = P.request(home, "player", "stop")
        for req in P.take_requests(home):
            z.handle_request(g, home, req, threads)
        rep = P.report(home, "player", r["job"])
        assert not rep["ok"] and "the Wall itself" in rep["note"] and g.alive("player")

        # a crash is named: exit code and the line that says why, on the tile
        g.specs["turbo"] = ([sys.executable, "-c", CRASH], {}, port)
        r = P.request(home, "turbo", "restart")
        for req in P.take_requests(home):
            z.handle_request(g, home, req, threads)
        for th in threads:
            th.join(60)
        rep = P.report(home, "turbo", r["job"])
        assert not rep["ok"] and "exit 1: RuntimeError: CUDA out of memory" in rep["note"], rep
        table.exited("turbo", g.procs["turbo"].poll())          # what the launcher's loop records
        t = {x["proc"]: x for x in P.view(home)["tiles"]}["turbo"]
        assert t["health"] == "bad" and t["note"] == "exit 1: RuntimeError: CUDA out of memory: tried to allocate 2 GiB"

        # the launcher gone: every tile says so and how to start it; a request is refused with that reason
        table.stop("signal 15")
        v = P.view(home)
        assert not v["launcher"]["alive"] and all(x["health"] == "bad" for x in v["tiles"])
        assert "Crissa was stopped" in v["tiles"][0]["note"] and "python3 scripts/crissa.py" in v["tiles"][0]["note"]
        refused = P.request(home, "player", "restart")
        assert not refused["ok"] and "Crissa was stopped" in refused["error"]
        assert not P.request(home, "../etc", "restart")["ok"]                 # names are a fixed shape
    finally:
        g.stop()
        _kill_all(pids + [p.pid for p in g.procs.values()])


def test_a_silent_launcher_is_named_by_its_heartbeat(tmp_path):
    home = tmp_path / "home"
    table = P.Table(home, 999999, [])
    table.started("pocket", 999999, None, home / "logs/pocket.log", ["x"])
    tab = P.load(home)
    tab["launcher"]["beat"] = time.time() - 120
    (home / "run/procs.json").write_text(json.dumps(tab))
    v = P.view(home)
    assert not v["launcher"]["alive"] and "stopped answering 2 min ago (pid 999999)" in v["tiles"][0]["note"]


def test_the_voices_section_gets_its_childs_keys_log_and_crash_reason():
    voices = {"section": "Voices", "tiles": [{"id": "turbo", "label": "Chatterbox", "state": "off", "note": "off"},
                                             {"id": "pocket", "label": "Pocket", "state": "ok", "note": "on"},
                                             {"id": "multilingual", "label": "Chatterbox Multilingual V3", "offer": True,
                                              "state": "off", "note": "offered"}]}
    procs = [{"proc": "turbo", "unit": "proc:turbo", "log": "/l/turbo.log", "health": "bad", "note": "exit 1: OOM"},
             {"proc": "pocket", "unit": "proc:pocket", "log": "/l/pocket.log", "health": "ok", "note": ""}]
    used = P.wire_voices(voices, procs)
    t, p, m = voices["tiles"]
    assert used == {"turbo", "pocket"}
    assert t["unit"] == "proc:turbo" and t["state"] == "bad" and t["note"] == "exit 1: OOM" and t["log"] == "/l/turbo.log"
    assert p["unit"] == "proc:pocket" and p["state"] == "ok"
    assert "unit" not in m                                                    # an offer has no process: nothing to press


# ---- the panel server: unit tiles -----------------------------------------------------------------------------------
@pytest.fixture
def wired(tmp_path):
    root = tmp_path / "install"
    units = base.fresh_units(root)
    units["user"]["crissa-voice.service"] = base.svc(exec_path=f"{root}/modules/voice/scripts/crissa-turbo.py")
    units["user"]["crissa-broken.service"] = base.svc(active="failed", exec_path=f"{root}/scripts/x.py",
                                                      Result="exit-code", ExecMainStatus="2", InvocationID="r0")
    units["user"]["crissa-crashy.service"] = base.svc(exec_path=f"{root}/scripts/y.py", InvocationID="r0")
    units["user"]["crissa-night.service"]["InvocationID"] = "r0"
    m = base.Machine(tmp_path, units, extra_cfg={"restart_settle_s": 0.2, "restart_port_s": 3})
    jf = tmp_path / "journal.json"
    jf.write_text(json.dumps({
        "crissa-broken.service": ["starting", "Traceback (most recent call last):", '  File "x.py", line 1',
                                  "ModuleNotFoundError: No module named 'kokoro'"],
        "crissa-crashy.service": ["starting", "OSError: [Errno 98] Address already in use"]}))
    m.env["FAKE_JOURNAL_FILE"] = str(jf)
    st = m.systemd()
    st["fall_over"] = ["crissa-crashy.service"]
    m.sysd.write_text(json.dumps(st))
    s = base.Server(m)
    yield s, m
    s.stop()


def _report(s, job, timeout=30):
    return _wait(lambda: (lambda r: r if r.get("state") == "done" else None)(s.get(f"/v1/restart-report?job={job}")),
                 "restart report", timeout)


def test_a_down_unit_names_its_reason_on_the_tile(wired):
    s, m = wired
    d = s.get("/v1/services")
    t = {r["unit"]: r for r in d["services"]}["crissa-broken"]
    assert t["health"] == "bad"
    assert t["note"] == "failed · exit 2 · ModuleNotFoundError: No module named 'kokoro'"
    m.set_units("user", **{"crissa-voice.service": {"ActiveState": "inactive", "SubState": "dead", "Result": "success"}})
    time.sleep(3.2)
    t = {r["unit"]: r for r in s.get("/v1/services")["services"]}["crissa-voice"]
    assert t["health"] == "bad" and t["note"].startswith("stopped - nothing failed; Start brings it back")


def test_restart_is_checked_and_a_fall_over_is_named(wired):
    s, m = wired
    code, r = s.post("/v1/action", {"action": "restart", "unit": "crissa-voice"})
    assert code == 200 and r["ok"] and r["job"]
    rep = _report(s, r["job"])
    assert rep["ok"] and rep["note"] == "crissa-voice restarted · running"
    code, r = s.post("/v1/action", {"action": "restart", "unit": "crissa-crashy"})
    rep = _report(s, r["job"])
    assert not rep["ok"] and "exit 1" in rep["note"] and "Address already in use" in rep["note"], rep
    code, r = s.post("/v1/action", {"action": "restart", "unit": "crissa-ghost"})
    assert code == 400 and not r["ok"]


def test_a_timers_restart_is_run_now_and_the_panel_restarting_itself_answers_first(wired):
    s, m = wired
    m.set_units("user", **{"crissa-night.timer": {"Triggers": "crissa-night.service"}})
    code, r = s.post("/v1/action", {"action": "restart", "unit": "crissa-night.timer"})
    assert r["ok"] and r.get("run_now"), r
    rep = _report(s, r["job"])
    assert rep["ok"] and "crissa-night ran" in rep["note"]
    assert ["user", "start", "crissa-night.service"] in m.systemd()["calls"]
    code, r = s.post("/v1/action", {"action": "restart", "unit": "crissa-panel"})
    assert r["ok"] and r.get("self") and "page reconnects" in r["note"]
    _wait(lambda: ["user", "restart", "crissa-panel.service"] in m.systemd()["calls"], "the deferred self restart")


def test_a_units_journal_pages_back_with_a_marker_and_the_whole_log_is_one_route(wired):
    s, m = wired
    s.stop()
    m.env["FAKE_JOURNAL_N"] = "250"
    s2 = base.Server(m)
    try:
        a = s2.get("/v1/journal?unit=crissa-tts&lines=100")
        assert a["ok"] and len(a["lines"]) == 100 and a["earlier"] and "not shown" in a["marker"]
        assert a["lines"][-1].endswith("fake journal line for crissa-tts.service")
        b = s2.get(f"/v1/journal?unit=crissa-tts&lines=100&before={a['earlier']}")
        c = s2.get(f"/v1/journal?unit=crissa-tts&lines=100&before={b['earlier']}")
        assert len(b["lines"]) == 100 and len(c["lines"]) == 50 and "earlier" not in c
        allof = c["lines"] + b["lines"] + a["lines"]
        assert len(set(allof)) == 250                                          # no gap, no overlap
        assert len(s2.get("/v1/journal?unit=crissa-tts&lines=all")["lines"]) == 250
        assert len(s2.get("/v1/journal?unit=crissa-tts&lines=5000")["lines"]) == 250   # not clamped
        whole = s2.get("/v1/log-file?unit=crissa-tts")
        assert isinstance(whole, bytes) and whole.decode().count("\n") == 250
        # a timer's tape carries the service it starts
        m.set_units("user", **{"crissa-night.timer": {"Triggers": "crissa-night.service"}})
        t = s2.get("/v1/journal?unit=crissa-night.timer&lines=10")
        assert t["units"] == ["crissa-night.timer", "crissa-night.service"]
        allp = s2.get("/v1/journal?unit=all&lines=20")
        assert allp["earlier"] and len(allp["lines"]) >= 20
    finally:
        s2.stop()


# ---- the panel server: the launcher's children ----------------------------------------------------------------------
def _fake_launcher(m, n_lines=500, beat=None):
    home = m.state
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "turbo.log").write_text("".join(f"turbo line {i}\n" for i in range(n_lines)))
    (logs / "pocket.log").write_text("serving\n")
    t = P.Table(home, os.getpid(), ["crissa.py"])
    t.started("turbo", os.getpid(), None, logs / "turbo.log", [sys.executable, str(m.root / "scripts/crissa-panel-server.py")])
    t.started("pocket", os.getpid(), None, logs / "pocket.log", [sys.executable, "-c", "pass"])
    t.started("multilingual", os.getpid(), None, logs / "multilingual.log", [sys.executable, "-c", "pass"])
    t.exited("multilingual", 1, why="exit 1: RuntimeError: no CUDA GPU")
    return t


def test_the_launchers_children_are_tiles_with_logs_restart_and_reasons(wired):
    s, m = wired
    t = _fake_launcher(m)
    time.sleep(3.2)
    d = s.get("/v1/services")
    by = {r["unit"]: r for r in d["services"]}
    assert d["launcher"]["present"] and d["launcher"]["alive"]
    assert by["proc:turbo"]["section"] == "Voices" and by["proc:turbo"]["health"] == "ok"
    assert by["proc:multilingual"]["health"] == "bad" and by["proc:multilingual"]["note"] == "exit 1: RuntimeError: no CUDA GPU"
    assert by["proc:pocket"]["label"].startswith("Pocket")
    assert d["fleet"]["down"] >= 1
    j = s.get("/v1/journal?unit=proc:turbo&lines=100")
    assert j["lines"][-1] == "turbo line 499" and j["earlier"] and "turbo.log not shown" in j["marker"]
    j2 = s.get(f"/v1/journal?unit=proc:turbo&lines=100&before={j['earlier']}")
    assert j2["lines"][0] == "turbo line 300" and j2["lines"][-1] == "turbo line 399"
    whole = s.get("/v1/log-file?unit=proc:turbo")
    assert whole.decode().count("\n") == 500
    sc = s.get("/v1/script?unit=proc:turbo")
    assert sc["ok"] and "started by the Crissa launcher" in sc["unit_file"] and sc["script"].endswith("crissa-panel-server.py")
    code, r = s.post("/v1/action", {"action": "restart", "unit": "proc:turbo"})
    assert code == 200 and r["ok"] and r["job"]
    assert (m.state / "run/requests/turbo.json").is_file()                   # queued for the launcher
    assert s.get(f"/v1/restart-report?job={r['job']}")["state"] == "queued"
    req = P.take_requests(m.state)[0]
    P.finish(m.state, "turbo", req["job"], True, "turbo restarted · :8186 answering (pid 4242)")
    rep = _report(s, r["job"])
    assert rep["ok"] and rep["note"].startswith("turbo restarted")
    code, r = s.post("/v1/action", {"action": "restart", "unit": "proc:nothere"})
    assert code == 400 and not r["ok"]
    t.stop("signal 15")
    time.sleep(3.2)
    by = {r["unit"]: r for r in s.get("/v1/services")["services"]}
    assert by["proc:turbo"]["health"] == "bad" and "Crissa was stopped" in by["proc:turbo"]["note"]
    code, r = s.post("/v1/action", {"action": "restart", "unit": "proc:turbo"})
    assert not r["ok"] and "Crissa was stopped" in r["note"]


def test_a_panel_without_the_launcher_has_no_launcher_tiles(server):
    d = server.get("/v1/services")
    assert not d["launcher"]["present"] and not any(r["unit"].startswith("proc:") for r in d["services"])


server = base.server
machine = base.machine


# ---- the page --------------------------------------------------------------------------------------------------------
def test_the_page_pages_back_shows_why_offers_the_whole_log_and_says_how_a_restart_went():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    jsdom = next((p for p in base.JSDOM if p and Path(p).is_dir()), "jsdom")
    out = subprocess.run([node, str(ROOT / "tests/js/crissa_panel_wiring_sim.js")], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ, JSDOM_PATH=jsdom))
    if out.returncode == 3:
        pytest.skip("jsdom not installed")
    assert out.returncode == 0, out.stderr
    g = json.loads(out.stdout)
    assert g["first"]["marker_shown"] and g["first"]["earlier_key"] and g["first"]["lines"] == 3
    assert g["first"]["whole_href"] == "/v1/log-file?unit=proc%3Aturbo"
    assert g["first"]["why"] == "Down: exit 1: RuntimeError: CUDA out of memory"
    assert g["earlier"]["lines"] == 5 and not g["earlier"]["earlier_key"] and g["earlier"]["follow_key"]
    assert g["earlier"]["first_line"] == "turbo line 0"
    assert g["restart"]["posted"] == {"action": "restart", "unit": "proc:turbo"}
    assert g["restart"]["toast"] == "turbo restarted · :8186 answering (pid 4242)"
    assert g["timer"]["key"] == "Run now"
