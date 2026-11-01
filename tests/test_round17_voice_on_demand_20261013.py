"""A voice engine runs only while a role uses it (round 17, 2026-10-13; lib/crissa_voice_demand.py).

.101 (the CPU-only test laptop, 2026-10-11): Pocket in use (0.9 GB RSS), Kokoro (1.1 GB) and Audio8 (1.8 GB) resident
and idle - about 3.8 GB for one voice. In round 16 her replies, Setup and the announcer all spoke on Pocket. The rule:
the engines the roles use start with Crissa; every other installed engine is held ("installed, not running"), started
when a line's ladder falls back to it, and stopped again after an idle time. Kill CRISSA_VOICE_ENGINES_ON_DEMAND=0.

Stand-ins only: tmp homes, a fake launcher table, fake groups, the launcher's spawn loop lifted from its source. Nothing
starts, stops or restarts a real unit or process, and nothing makes a sound.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_home  # noqa: E402
import crissa_procs  # noqa: E402
import crissa_tts as T  # noqa: E402
import crissa_voice_demand as VD  # noqa: E402
import crissa_voice_panel as VP  # noqa: E402
import crissa_watch as W  # noqa: E402

from _kit import kit  # noqa: E402  - the kit's copy of a shipped file (the edition overlay)


# Audio8 as a VOICE is behind its switch since 2026-10-17 (the User: Audio8 is her ear, Pocket her voice; lib/
# crissa_voice_tiers.not_a_voice). These tests pin the Audio8-voice path that the switch keeps; the rule itself is
# tests/test_audio8_not_a_voice_20261017.py.
@pytest.fixture(autouse=True)
def _audio8_may_speak(monkeypatch):
    monkeypatch.setenv("CRISSA_AUDIO8_TTS", "1")

LAUNCHER = ROOT / "scripts" / "crissa.py"

# .101's pick after its machine card (Pocket cleared the bars; Audio8 and Kokoro were timed over them): her voice
# Pocket, the announcer's order brought in line by fit_announcer, Audio8 installed as the announcer's first choice
REC_101 = {"schema": "crissa-voice-tier/1", "main": "pocket", "chain": ["pocket", "audio8", "kokoro", "text"],
           "announcer_chain": ["pocket", "audio8", "kokoro", "text"], "picked_by": "user"}
REC_ROBUST = {"main": "audio8", "chain": ["audio8", "pocket", "text"],
              "announcer_chain": ["audio8", "pocket", "kokoro", "text"]}
REC_GPU = {"main": "turbo", "chain": ["turbo", "pocket", "audio8", "text"],
           "announcer_chain": ["audio8", "pocket", "kokoro", "text"]}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def pack(monkeypatch, tmp_path):
    """A pack (never the house), the rule on, its state home a tmp folder."""
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.delenv(VD.SWITCH, raising=False)
    monkeypatch.delenv("CRISSA_TTS", raising=False)
    return tmp_path


def _table(home: Path, children: dict, beat: float | None = None) -> None:
    """A launcher table as the launcher writes it (lib/crissa_procs.Table), with a fresh heartbeat."""
    d = crissa_procs.run_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    crissa_procs.req_dir(home).mkdir(parents=True, exist_ok=True)
    tab = {"schema": crissa_procs.SCHEMA, "launcher": {"pid": 1, "state": "running", "beat": beat or time.time()},
           "children": {n: dict({"name": n}, **c) for n, c in children.items()}}
    crissa_procs.table_path(home).write_text(json.dumps(tab))


# ---- 1. the map: which engine each role uses --------------------------------------------------------------------------

@pytest.mark.parametrize("rec, named, use", [
    (REC_101, "", {"pocket"}),                         # .101: her, Lila/Setup and the announcer all on Pocket
    (REC_ROBUST, "", {"audio8"}),                      # a robust CPU box: Audio8 for her and the announcer
    (REC_GPU, "", {"turbo", "audio8"}),                # a GPU box: Turbo for her, Audio8 announces
    ({"main": "pocket", "chain": ["pocket", "kokoro", "text"]}, "", {"pocket"}),   # no announcer order: her voice
    (REC_101, "turbo", {"turbo", "pocket"}),           # a named GPU voice: Pocket announces (crissa_tts.config)
    (REC_101, "kokoro", {"kokoro"}),                   # a named Kokoro says every line
    (None, "", None),                                  # no pick to read: nothing is held (today's behaviour)
    ({}, "", None),
])
def test_each_role_uses_the_engine_the_pick_names(rec, named, use):
    assert VD.in_use(rec, named) == use
    if use is not None:
        r = VD.roles(rec, named)
        assert r["her"] == r["lila"], "Lila and Setup speak with her voice"


def test_on_101_kokoro_and_audio8_are_held_and_pocket_runs(pack):
    held = VD.standby_at_start(["tts", "pocket", "audio8"], REC_101)
    assert held == {"tts", "audio8"}, "Kokoro's lane is tts; Pocket is every role's voice there"
    assert VD.who_uses(REC_101, "", "pocket") == "her replies, Lila and Setup, the announcer"
    assert VD.who_uses(REC_101, "", "audio8") == ""


def test_the_named_engine_is_read_from_the_environment_then_agent_json(pack, monkeypatch):
    (pack / "agent.json").write_text(json.dumps({"tts": {"engine": "Kokoro"}}))
    assert VD.named_engine(env={}, home=pack) == "kokoro"
    assert VD.named_engine(env={"CRISSA_TTS": "pocket"}, home=pack) == "pocket"
    assert VD.named_engine(env={"CRISSA_TTS": "house"}, home=pack) == "", "not an engine name"


# ---- 2. the kill switch and the house -------------------------------------------------------------------------------

def test_the_kill_switch_holds_nothing_and_starts_nothing(pack, monkeypatch):
    monkeypatch.setenv(VD.SWITCH, "0")
    assert VD.on() is False
    assert VD.standby_at_start(["tts", "pocket", "audio8"], REC_101) == set()
    _table(pack, {"audio8": {"state": "standby"}})
    assert VD.ensure("audio8", pack, request=lambda: pytest.fail("no start with the switch off")) is None
    assert VD.held_engines(pack) == set()
    assert not VD.use_path("audio8", pack).exists(), "off: not even a use stamp"


def test_the_house_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    monkeypatch.delenv(VD.SWITCH, raising=False)
    assert VD.on() is False
    assert VD.standby_at_start(["tts", "pocket", "audio8"], REC_101) == set()
    _table(tmp_path, {"audio8": {"state": "standby"}})
    assert VD.ensure("audio8", tmp_path, request=lambda: pytest.fail("never on the house")) is None
    z = _load("crissa_launcher_r17_house", LAUNCHER)
    g = _FakeGroup(tmp_path, {})
    assert z.voice_demand_pass(g, tmp_path, {"tier": REC_101}, now=1e9) == []
    # the seam: the house's own names keep the house's path, with or without a line's demand
    monkeypatch.setattr(T, "_house", lambda: True)
    assert T.config({"CRISSA_TTS": "house"}, home=tmp_path, demand=True) == T.config({"CRISSA_TTS": "house"},
                                                                                     home=tmp_path)


# ---- 3. idle engines are not started at boot (the launcher's spawn loop, lifted from its source) -----------------------

def _spawn_loop(src: str) -> str:
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run")
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.For) and getattr(n.target, "id", "") == "kid"
                and "children" in ast.unparse(n.iter))
    return textwrap.dedent("\n".join(src.splitlines()[loop.lineno - 1:loop.end_lineno]))


class _Table:
    def __init__(self):
        self.marked: dict = {}
        self.data = {"children": {}}

    def mark(self, name, **kw):
        self.marked[name] = kw
        self.child(name).update(kw)

    def child(self, name):
        return self.data["children"].setdefault(name, {"name": name})

    def exited(self, name, rc, expected=False, why=""):
        self.child(name).update(state="stopped" if expected else "exited", why=why)

    def write(self):
        pass


def test_a_held_voice_is_planned_but_not_started_at_boot(pack):
    z = _load("crissa_launcher_r17_boot", LAUNCHER)
    a = types.SimpleNamespace(tier=REC_101, tts="", with_tts=True, with_pocket=True, with_audio8=True,
                              with_turbo=False, with_multilingual=False, args=[])
    a.standby = z.voice_standby(a, env={})
    assert a.standby == {"tts", "audio8"}
    spawned, table, late, held = [], _Table(), {}, set()
    kids = [{"name": n, "argv": ["python3", n], "env": {}, "port": p}
            for n, p in (("tts", 8189), ("pocket", 8188), ("audio8", 8187), ("asr", 8096))]
    ns = {"a": a, "p": {"children": kids}, "table": table, "LATE_LANES": late, "STANDBY": held, "AGENT_HELD": set(),
          "g": types.SimpleNamespace(spawn=lambda name, argv, env, port: spawned.append(name)),
          "crissa_watch": types.SimpleNamespace(closed=lambda home, lane: {}, AGENT_LANES=W.AGENT_LANES,
                                                agent_closed=lambda home=None: {}, AGENT_WHY=W.AGENT_WHY),
          "crissa_home": types.SimpleNamespace(HOME=pack), "_log": lambda *a, **k: None, "json": json}
    exec(compile(_spawn_loop(LAUNCHER.read_text(encoding="utf-8")), str(LAUNCHER), "exec"), ns)   # noqa: S102
    assert spawned == ["pocket", "asr"], "Kokoro and Audio8 are not started: no role uses them on .101"
    assert held == {"tts", "audio8"} and set(late) == {"tts", "audio8"}, "known to the request queue"
    assert table.marked["audio8"]["state"] == "standby" and "installed, not running" in table.marked["audio8"]["why"]


def test_the_kill_switch_starts_every_installed_voice_at_boot(pack, monkeypatch):
    monkeypatch.setenv(VD.SWITCH, "0")
    z = _load("crissa_launcher_r17_off", LAUNCHER)
    a = types.SimpleNamespace(tier=REC_101, tts="", with_tts=True, with_pocket=True, with_audio8=True)
    assert z.voice_standby(a, env={VD.SWITCH: "0"}) == set()


def test_the_plan_says_which_lanes_are_held(pack):
    z = _load("crissa_launcher_r17_plan", LAUNCHER)
    a = types.SimpleNamespace(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False,
                              with_aligner=False, standby={"tts", "audio8"})
    assert z.plan(a, env_in={})["standby"] == [], "only a planned child can be held (no voice module here)"


# ---- 4. on demand: the ladder falls back to a held engine and it is started ------------------------------------------

def test_a_held_engine_is_started_on_demand_said_and_waited_for(pack, capsys):
    _table(pack, {"audio8": {"state": "standby", "why": VD.STANDBY_WHY}})
    asked, ticks = [], {"t": 0.0}
    up = lambda: bool(asked) and ticks["t"] >= 2.0    # noqa: E731  answers 2 s after its start

    def sleep(s):
        ticks["t"] += s
    got = VD.ensure("audio8", pack, up=up, request=lambda: asked.append(1) or {"ok": True}, sleep=sleep,
                    clock=lambda: ticks["t"])
    assert got is True and asked == [1]
    err = capsys.readouterr().err
    assert "audio8 is installed, not running" in err and "started on demand" in err and "answered" in err
    assert "started on demand" in (pack / "logs" / "voice-demand.log").read_text(), "said in the log"
    assert VD.last_use("audio8", pack) > 0, "the line's use is stamped for the idle stop"


def test_an_engine_that_is_not_held_is_not_this_rules(pack):
    _table(pack, {"pocket": {"state": "exited"}, "audio8": {"state": "running"}, "tts": {"state": "waiting"}})
    for eng in ("pocket", "audio8", "kokoro"):
        assert VD.ensure(eng, pack, request=lambda: pytest.fail("not held: no start")) is None
    assert VD.ensure("say", pack) is None and VD.ensure("text", pack) is None


def test_a_held_engine_that_does_not_come_up_lets_the_ladder_go_on(pack):
    _table(pack, {"tts": {"state": "standby"}})
    clock = {"t": 0.0}
    got = VD.ensure("kokoro", pack, up=lambda: False, request=lambda: {"ok": True},
                    sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"])
    assert got is False and clock["t"] >= VD.wait_s("kokoro", pack)
    assert VD.ensure("kokoro", pack, up=lambda: False, request=lambda: {"ok": False, "error": "launcher gone"}) is False


def test_close_agent_holds_a_held_voice_too(pack):
    _table(pack, {"audio8": {"state": "standby"}})
    W.set_closed(W.AGENT, True, by="the menu", home=pack)
    assert VD.ensure("audio8", pack, request=lambda: pytest.fail("Close Agent: no start")) is False


def _seam(monkeypatch, home, rec, up: dict):
    """The seam over a fake launcher: a held engine "answers" once the launcher has its start request."""
    monkeypatch.setattr(T, "_tier_record", lambda h=None: dict(rec))
    monkeypatch.setattr(T, "_installed_here", lambda name: name == "audio8")
    asked = lambda lane: (crissa_procs.req_dir(home) / ("%s.json" % lane)).exists()   # noqa: E731
    monkeypatch.setattr(T, "stream_up", lambda name, env=None, timeout=0.3, url=None: up.get(name, False))
    monkeypatch.setattr(T, "audio8_up", lambda env=None, timeout=0.5, url=None: up.get("audio8") or asked("audio8"))
    monkeypatch.setattr(T, "kokoro_up", lambda env=None, timeout=0.3: up.get("kokoro") or asked("tts"))


def test_the_walk_for_a_line_starts_a_held_backup_and_a_status_read_does_not(pack, monkeypatch):
    _table(pack, {"pocket": {"state": "exited"}, "audio8": {"state": "standby"}, "tts": {"state": "standby"}})
    _seam(monkeypatch, pack, REC_101, up={"pocket": False})
    quiet = T.config({}, home=pack)                         # the menu, the status mark, the panel: no demand
    assert quiet == {} and not (crissa_procs.req_dir(pack) / "audio8.json").exists(), "a status read starts nothing"
    got = T.config({}, home=pack, demand=True)               # house-speak's line
    assert got["engine"] == "audio8" and got["tier_main"] == "pocket"
    req = json.loads((crissa_procs.req_dir(pack) / "audio8.json").read_text())
    assert req["action"] == "start" and req["name"] == "audio8", "asked through the launcher's request queue"
    assert not (crissa_procs.req_dir(pack) / "tts.json").exists(), "the first held voice in the chain, not all of them"


def test_synth_starts_a_held_engine_before_asking_it(pack, monkeypatch):
    _table(pack, {"tts": {"state": "standby"}})
    _seam(monkeypatch, pack, REC_101, up={})
    spoke = []

    def kokoro(text, cfg, speaker, timeout):
        assert (crissa_procs.req_dir(pack) / "tts.json").exists(), "started before it is asked"
        spoke.append(speaker)
        return b"\x01\x00" * 24000, 24000
    monkeypatch.setattr(T, "_kokoro", kokoro)
    monkeypatch.setattr(T, "_note_spoke", lambda *a, **k: None)
    pcm, sr = T.synth("Hello.", "announcer", {"engine": "kokoro"})
    assert spoke == ["announcer"] and sr == T.HOUSE_SR


# ---- 5. the idle stop, and a re-pick ---------------------------------------------------------------------------------

def test_decide_stops_an_unused_engine_only_after_its_idle_time():
    since = {}
    lanes = {"audio8": {"alive": True, "standby": False}, "pocket": {"alive": True, "standby": False}}
    use = {"pocket"}
    assert VD.decide(lanes, use, {}, since, 1000.0, 600) == [], "first seen running unused: the clock starts"
    assert VD.decide(lanes, use, {"audio8": 1500.0}, since, 1599.0, 600) == [], "a line asked it at 1500"
    got = VD.decide(lanes, use, {"audio8": 1500.0}, since, 2100.0, 600)
    assert [(a, l) for a, l, _w in got] == [("stop", "audio8")], "in use (pocket) is never stopped"
    assert "no line asked it for 10 min" in got[0][2]


def test_decide_starts_a_held_engine_a_role_uses_now_and_holds_one_init_installed():
    lanes = {"audio8": {"alive": False, "standby": True}, "tts": {"alive": False, "waiting": True, "installed": True},
             "pocket": {"alive": False, "waiting": True, "installed": False}}
    got = {(a, l) for a, l, _w in VD.decide(lanes, {"audio8"}, {}, {}, 0.0, 600)}
    assert got == {("start", "audio8"), ("hold", "tts")}, "a voice not installed yet keeps waiting for Init"


class _FakeGroup:
    def __init__(self, home, alive: dict):
        self.home, self._alive = home, dict(alive)
        self.specs = {n: (["python3", n], {}, None) for n in alive}
        self.table = _Table()
        self.stopped, self.spawned = [], []

    def alive(self, name):
        return self._alive.get(name, False)

    def going_down(self, fresh=False):
        return False

    def stop_one(self, name, wait=None):
        self.stopped.append(name)
        self._alive[name] = False
        return -15

    def spawn(self, name, argv, env, port=None):
        self.spawned.append(name)
        self._alive[name] = True


def test_the_launchers_pass_stops_an_idle_backup_and_holds_it(pack, monkeypatch):
    z = _load("crissa_launcher_r17_pass", LAUNCHER)
    g = _FakeGroup(pack, {"pocket": True, "audio8": True})
    state = {"tier": REC_101, "named": ""}
    assert z.voice_demand_pass(g, pack, state, now=10_000.0) == []
    assert z.voice_demand_pass(g, pack, state, now=10_002.0) == [], "at most every on_demand.every_s"
    did = z.voice_demand_pass(g, pack, state, now=10_000.0 + VD.idle_stop_s(pack))
    assert did == ["stop audio8"] and g.stopped == ["audio8"]
    assert "audio8" in z.STANDBY and "audio8" in z.LATE_LANES, "held, and startable through the request queue"
    assert g.table.child("audio8")["state"] == "standby"
    assert "stopped" in (pack / "logs" / "voice-demand.log").read_text()


def test_the_launchers_pass_starts_a_held_voice_after_a_re_pick(pack):
    z = _load("crissa_launcher_r17_repick", LAUNCHER)
    z.STANDBY.add("audio8")
    z.LATE_LANES["audio8"] = {"name": "audio8", "argv": ["python3", "audio8"], "env": {}, "port": 8187}
    g = _FakeGroup(pack, {"pocket": True})
    did = z.voice_demand_pass(g, pack, {"tier": REC_ROBUST, "named": ""}, now=1e9)   # the person picked Audio8
    assert "start audio8" in did and g.spawned == ["audio8"] and "audio8" not in z.STANDBY


def test_a_start_request_takes_a_lane_out_of_the_held_set(pack, monkeypatch):
    z = _load("crissa_launcher_r17_req", LAUNCHER)
    monkeypatch.setattr(z, "ready_check", lambda g, name, timeout=None: (True, "%s answering" % name))
    z.STANDBY.add("audio8")
    z.LATE_LANES["audio8"] = {"name": "audio8", "argv": ["python3", "audio8"], "env": {}, "port": None}
    g = _FakeGroup(pack, {})
    g.specs = {}
    threads = []
    z.handle_request(g, pack, {"name": "audio8", "action": "start", "job": "j1"}, threads)
    for t in threads:
        t.join(timeout=10)
    assert g.spawned == ["audio8"] and "audio8" not in z.STANDBY


def test_the_watch_never_restarts_a_held_lane(pack):
    g = _FakeGroup(pack, {})
    g.standby = {"tts"}
    kid = {"argv": ["python3", "tts"], "env": {}, "port": None}
    clock = {"t": 1000.0}
    w = W.Watch({"tts": kid}, g, cfg=dict(W.load(pack), restart=["tts"], health={}), home=pack,
                log=lambda m: None, now=lambda: clock["t"])
    for _ in range(5):
        clock["t"] += 30
        w.tick()
    assert g.spawned == [] and w.st["tts"]["state"] == "standby"
    assert json.loads((pack / W.STATUS).read_text())["lanes"]["tts"]["state"] == "standby"
    g.standby.discard("tts")                    # started on demand: watched afresh, its boot grace from now
    g._alive["tts"] = True
    w.tick()
    assert w.st["tts"]["started_at"] >= clock["t"] - 1


# ---- 6. the panel: "installed, not running", never red ----------------------------------------------------------------

def test_the_launchers_tile_says_installed_not_running(pack):
    _table(pack, {"audio8": {"state": "standby", "why": VD.STANDBY_WHY},
                  "pocket": {"state": "running", "pid": os.getpid()}})
    tiles = {t["proc"]: t for t in crissa_procs.view(pack)["tiles"]}
    t = tiles["audio8"]
    assert t["health"] == "ok" and t["parked"] is True and t["standby"] is True
    assert t["note"].startswith("installed, not running") and t["sub"] == "standby"
    assert crissa_procs.running_count(pack)["pieces"] == ["launcher", "pocket"], "a held voice is not running"


def test_the_voices_section_says_installed_not_running(pack):
    _table(pack, {"audio8": {"state": "standby"}, "pocket": {"state": "standby"}})
    assert VD.held_engines(pack) == {"audio8", "pocket"}
    import crissa_voice_tiers as VT
    t = VP.tile("pocket", "Pocket", REC_ROBUST, up=lambda e: False, installed=lambda e: True,
                VT=VT, T=T, held={"pocket"})
    assert t["state"] == "off" and t["mark"] == "○" and t["note"].startswith("installed, not running")
    a8 = VP.audio8_tile(REC_101, up=lambda e: False, installed=lambda e: True, VT=VT, T=T, held={"audio8"})
    assert a8["state"] == "off" and a8["actions"] == ["log"], "no Restart key that would start it outside the rule"


def test_the_control_panels_lane_row_for_a_held_voice_is_not_red(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(pack))
    panel = _load("crissa_panel_r17", ROOT / "scripts" / "crissa-panel-server.py")
    import crissa_panel_conf as conf
    cfg = conf.load()
    cfg["_state"] = str(pack)
    cfg["os_power"] = dict(cfg.get("os_power") or {}, state_dir=str(pack / "panel"))
    panel.setup(cfg)
    monkeypatch.setattr(panel.crissa_watch, "read_status", lambda home: {"launcher_alive": True, "lanes": {
        "tts": {"state": "standby", "restarts": 0, "last_why": W.STANDBY_WHY}}})
    row = {r["lane"]: r for r in panel.lane_rows()}["tts"]
    assert row["health"] == "ok" and row["parked"] is True and "installed, not running" in row["note"]


def test_the_feeds_panel_keeps_a_held_tile_over_its_stopped_unit():
    # the kit's feed: scripts/voiceonly-home-server.py is prod-guarded on the house, so this change ships as
    # crissa/overlay/scripts/voiceonly-home-server.py (config/crissa-packages.json `edition_overlay`)
    src = kit("scripts/voiceonly-home-server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "unit_truth")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "unit_truth", "exec"), ns)   # noqa: S102
    held = {"proc": "audio8", "health": "ok", "standby": True, "note": "installed, not running"}
    got = ns["unit_truth"](held, status=lambda u: {"active": "inactive", "health": "bad", "sub": "dead"})
    assert got is held, "a held voice's stopped unit is not a fault"
    run = {"proc": "pocket", "health": "ok"}
    assert ns["unit_truth"](run, status=lambda u: {"active": "failed", "health": "bad"})["health"] == "bad"


# ---- 7. Init installs her backup but starts only what a role uses -----------------------------------------------------

def test_init_starts_her_voice_not_her_backup(pack, monkeypatch):
    import crissa_voice_install as VI
    import crissa_voice_tiers as VT
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.setattr(VT, "broken", lambda: {})
    VT._write(dict(REC_ROBUST, schema=VT.SCHEMA), pack)
    assert VI._in_use(pack) == {"audio8"}
    monkeypatch.setenv(VD.SWITCH, "0")
    assert VI._in_use(pack) is None, "the switch off: every installed one starts, as before"
