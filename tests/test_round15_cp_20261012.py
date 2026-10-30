"""Round 15 (2026-10-12, .101 addendum 13:37): Close Agent shows and takes her eyes, the wake after Open Agent, and the
Control Panel's stale state and layout.

  1. Close Agent was invisible ("just closed agent, nothing happened"): the desk Wall rests on a dimmed still with a
     muted "Agent closed · Open Agent" plate, the browser Wall dims under the same plate; her eyes (camera, presence,
     oracle) stop with her and come back with Open Agent.
  2. The first "Hey, Chrisa." after Open Agent was skipped with `media: true`: the identity hold of a fresh ear
     (`media_hold_sole`) no longer counts as media against the wake.
  3. The Control Panel: a stale All OS Off is retired (older than the launcher's start or the last Open Agent, or
     everything it stopped runs again); the unit list reads live states; her lanes start clean after Open Agent and a
     lane Close Agent holds is parked, not red; the log wraps, the scrollbars wear the page's colours, the unit list sits
     under the tiles.
Stand-ins only (fake lane groups, tmp homes, a fake plan); test_crissa_menu, the panel layout and house_wall_ascii are
left out on purpose (a leak is being traced there): the Wall's own lines are read from its source.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))

import crissa_home  # noqa: E402
import crissa_procs as P  # noqa: E402
import crissa_watch as WT  # noqa: E402
import crissa_wall_key as K  # noqa: E402
from crissa_bridge.hear_gate import classify_hear, wake_media  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def pack(monkeypatch, tmp_path):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    return tmp_path


# ── 1. Close Agent takes her eyes, and Open Agent starts her lanes clean ────────────────────────────────────────────
class _Proc:
    def __init__(self, alive=True):
        self.returncode = None if alive else 1

    def poll(self):
        return self.returncode


class _Table:
    def __init__(self):
        self.children, self.writes = {}, 0

    def exited(self, *a, **k):
        pass

    def child(self, name):
        return self.children.setdefault(name, {"name": name, "restarts": 2})

    def write(self):
        self.writes += 1


class FakeGroup:
    def __init__(self, names):
        self.procs = {n: _Proc(True) for n in names}
        self.spawned, self.stopped = [], []
        self.specs = {n: (["x", n], {}, None) for n in names}
        self.table = _Table()

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


@pytest.fixture
def L(monkeypatch, pack):
    mod = _load("crissa_launcher_r15", ROOT / "scripts" / "crissa.py")
    monkeypatch.setattr(mod, "_log", lambda *a, **k: None)
    mod.AGENT_HELD.clear()
    mod.LATE_LANES.clear()
    return mod


def test_close_agent_stops_her_eyes_too_and_open_agent_starts_them_clean(L, pack):
    assert set(WT.AGENT_EYES) == {"camera", "presence", "oracle"} and set(WT.AGENT_EYES) <= set(WT.AGENT_LANES)
    g = FakeGroup(["player", "wall", "feed", "panel", "faceshell", "stt", "ear", "camera", "presence", "oracle"])
    st = {"closed": False}
    WT.set_closed(WT.AGENT, True, by="the desk Wall", home=pack)
    L.agent_pass(g, pack, st, close_window=lambda: "closed /wall")
    assert sorted(g.stopped) == ["camera", "ear", "oracle", "presence", "stt"], "the film, feed, player, Wall stay"
    resets = []
    watch = types.SimpleNamespace(reset_lanes=lambda lanes, why: resets.append((sorted(lanes), why)))
    WT.set_closed(WT.AGENT, False, home=pack)
    L.agent_pass(g, pack, st, watch=watch)
    assert sorted(g.spawned) == ["camera", "ear", "oracle", "presence", "stt"]
    assert resets == [(["camera", "ear", "oracle", "presence", "stt"], "opened again (Open Agent)")]
    assert all(g.table.children[n]["restarts"] == 0 for n in g.spawned), "no restarts carried onto her tiles"
    assert WT.agent_opened_at(pack), "Open Agent leaves its time for the panel"


def test_the_watch_starts_open_agent_lanes_clean_and_calls_them_up(pack):
    cfg = dict(WT.FALLBACK, restart=["ear"], closable=["wall"], backoff_s=[0], health={})
    g = FakeGroup(["ear"])
    w = WT.Watch({"ear": {"argv": ["x"], "env": {}, "port": None}}, g, cfg=cfg, home=pack, log=lambda m: None)
    w.st["ear"].update(restarts=2, state="closed", last_why="Close Agent (x)")
    w.reset_lanes(["ear"], "opened again (Open Agent)")
    assert w.st["ear"]["restarts"] == 0 and w.st["ear"]["state"] == "restarted"
    assert w.st["ear"]["last_why"] == "opened again (Open Agent)"
    lanes = json.loads((pack / WT.STATUS).read_text())["lanes"]
    assert lanes["ear"]["restarts"] == 0


# ── 1. Close Agent shows on both glasses ────────────────────────────────────────────────────────────────────────────
def test_the_desk_walls_plate_reads_the_menu_and_sits_centred_near_the_foot():
    assert K.agent_closed({"agent": {"closed": True}}) is True and K.agent_closed({"agent": {}}) is False
    assert K.agent_closed({}) is False and K.PLATE_TEXT == "Agent closed  ·  Open Agent"
    assert K.plate_place((70, 32, 3370, 1408), 300, 30) == (70 + (3370 - 300) // 2, 32 + 1408 - 30 - 56)
    src = (ROOT / "lib" / "crissa_wall_key.py").read_text()
    tick = src.split("def plate_tick():")[1].split("root.after(PLATE_TICK_MS, plate_tick)")[0]
    assert "plate.place(x=x, y=y)" in tick and "plate.place_forget()" in tick and "on_box()" in tick
    assert '"action": "agent_open", "by": "the desk Wall"' in src


def test_the_desk_wall_rests_on_a_dimmed_still_and_takes_the_plates_clicks():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    look = src.split("def agent_closed_look() -> bool:")[1].split("\ndef ")[0]
    assert "crissa_home.is_house()" in look and "CRISSA_AGENT_CLOSED_LOOK" in look
    rest = src.split("def t_rest(st: State):")[1].split("\ndef ")[0]
    assert "if agent_closed_look():" in rest and "her agent is closed" in rest
    frames = src.split("def t_frames(st: State, fps: float):")[1].split("\ndef ")[0]
    assert "(1.0 / AGENT_STILL_FPS if agent_closed_look() else 1.0 / REST_FPS)" in frames
    assert src.count('brightness=pref["brightness"] * (agent_dim() if own else 1.0)') == 2, "film and fallback both dim"
    assert 'boxes.append(crissa_key["plate"]())' in src and 'crissa_key["plate"] = crissa_wall_key.plate_box' in src
    assert "AGENT_STILL_FPS = _env_f(\"HOUSE_WALL_AGENT_FPS\", 0.2" in src and "AGENT_DIM = _env_f(\"HOUSE_WALL_AGENT_DIM\", 0.4" in src


def test_the_browser_wall_dims_under_the_same_plate():
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert 'body.agent-closed #glass' in page and "filter: brightness(.4)" in page
    assert '<div id="agentPlate" role="status"><span>Agent closed</span><button type="button" id="agentOpen">Open Agent' \
           '</button></div>' in page
    assert 'document.body.classList.toggle("agent-closed"' in page and "setInterval(agentWatch, 5000)" in page
    assert 'menuPost({action: "agent_open", by: "her window"})' in page


# ── 2. the wake after Open Agent ────────────────────────────────────────────────────────────────────────────────────
def _sole_hold(text):
    st = {"person_id": None, "source": "media_hold_sole", "media_suppressed_id": "user",
          "media_suppressed_source": "sole_present", "media_gate": {"phase": "characterize", "verdict": "unsure"}}
    return {"text": text, "person_id": None, "stamp": st}


def test_a_wake_held_only_for_identity_unpauses_and_stays_off_the_users_lane(monkeypatch):
    monkeypatch.delenv("EAR_WAKE_SOLE_HOLD_BLOCKS", raising=False)
    g = classify_hear(_sole_hold("Hey, Chrisa."), session_open=False, uid="user")
    assert g["media"] is True and g["unpause"] is True and g["paused"] is False and g["crissa_hears"] is True
    assert g["log_user"] is False and g["to_her"] is False, "the hold still keeps it off the User's lane"
    tv = _sole_hold("Hey, Chrisa.")
    tv["stamp"].update(source="media_gate", media_gate={"verdict": "media", "media_on": True})
    assert classify_hear(tv, session_open=False, uid="user")["unpause"] is False, "real media still holds the wake"
    assert wake_media({"source": "media_hold_sole", "media_suppressed_source": "sole_present"}) is False
    assert wake_media({"source": "media_gate"}) is True
    monkeypatch.setenv("EAR_WAKE_SOLE_HOLD_BLOCKS", "1")
    assert classify_hear(_sole_hold("Hey, Chrisa."), session_open=False, uid="user")["unpause"] is False


# ── 3. the Control Panel ────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def power(pack):
    import crissa_os_power as OP
    import crissa_panel_conf as conf
    cfg = conf.load()
    cfg["_state"] = str(pack)
    cfg["os_power"] = dict(cfg.get("os_power") or {}, state_dir=str(pack / "panel"))
    pw = OP.Power(cfg)
    pw.state_dir.mkdir(parents=True, exist_ok=True)
    return pw


def _snap(pw, ts, units=("crissa-stt.service", "crissa-ear.service")):
    pw._write_json(pw.snapshot_path, {"schema": "crissa_os_off/v1", "ts": ts, "updated": ts,
                                      "units": [{"unit": u, "scope": "user"} for u in units]})


def test_a_stale_all_os_off_is_retired_and_a_live_one_stands(power, monkeypatch):
    monkeypatch.delenv("CRISSA_OS_OFF_STALE", raising=False)
    _snap(power, "2026-10-11T09:37:02")
    assert power.summary()["in_force"] is True
    assert power.stale_why(None, "2026-10-12T13:00:00", "") .startswith("Crissa started again since")
    assert power.stale_why(None, "", "2026-10-12T13:33:34").startswith("Open Agent since")
    rows_on = [{"unit": "crissa-stt.service", "active": "active"}, {"unit": "crissa-ear.service", "active": "active"}]
    rows_half = [{"unit": "crissa-stt.service", "active": "active"}, {"unit": "crissa-ear.service", "active": "inactive"}]
    # round 16: "runs again" reads only a state taken after the stop settled (tests/test_round16_cp_20261013.py)
    late = power._epoch("2026-10-11T09:37:02") + power.OFF_SETTLE_S + 1
    assert power.stale_why(rows_on, rows_at=late) == "everything it stopped runs again"
    assert power.stale_why(rows_half, "2026-10-11T08:00:00", "2026-10-11T08:00:00", rows_at=late) == "", "Off holds"
    assert power.retire_snapshot("Crissa started again since (2026-10-12 13:00:00)")
    assert power.summary() == {"in_force": False}
    assert list(power.state_dir.glob(power.snapshot_path.name + ".cleared-*"))
    assert json.loads(power.report_path.read_text())["action"] == "cleared"
    monkeypatch.setenv("CRISSA_OS_OFF_STALE", "0")
    _snap(power, "2026-10-11T09:37:02")
    assert power.stale_why(rows_on, "2026-10-12T13:00:00", rows_at=late) == ""


@pytest.fixture
def panel(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(pack))
    mod = _load("crissa_panel_r15", ROOT / "scripts" / "crissa-panel-server.py")
    import crissa_panel_conf as conf
    cfg = conf.load()
    cfg["_state"] = str(pack)
    cfg["os_power"] = dict(cfg.get("os_power") or {}, state_dir=str(pack / "panel"))
    mod.setup(cfg)
    return mod


def test_the_panel_retires_a_stale_off_before_it_reads_it(panel, pack):
    pw = panel.POWER
    pw.state_dir.mkdir(parents=True, exist_ok=True)
    _snap(pw, "2026-10-11T09:37:02")
    P._write(P.table_path(Path(panel.CFG["_state"])), {"schema": P.SCHEMA, "launcher": {
        "pid": os.getpid(), "beat": time.time(), "state": "running", "started": "2026-10-12T13:00:00"}, "children": {}})
    assert panel.retire_stale_off().startswith("Crissa started again since")
    assert pw.summary() == {"in_force": False}


def test_the_unit_list_carries_live_states(panel, monkeypatch):
    monkeypatch.setattr(panel, "cached_plan", lambda now=None: {"units": [
        {"unit": "crissa-aligner.service", "active": "active", "sub": "running", "file_state": "static"}]})
    reg = {"updated": "2026-10-11T02:04:00", "units": [
        {"unit": "crissa-aligner.service", "role": "manual", "active": "inactive", "sub": "dead"},
        {"unit": "crissa-gone.service", "role": "service", "active": "inactive", "sub": "dead"}]}
    got = panel.live_registry(reg)
    assert got["units"][0]["active"] == "active" and got["units"][0]["sub"] == "running"
    assert got["units"][1]["active"] == "inactive" and got["states_at"] and got["updated"] == reg["updated"]
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert "reg = live_registry(registry())" in src and "reg = live_registry(registry(force=" in src


def test_a_lane_close_agent_holds_is_parked_and_says_why_not_red(panel, monkeypatch):
    monkeypatch.setattr(panel.crissa_watch, "read_status", lambda home: {"launcher_alive": True, "lanes": {
        "stt": {"state": "closed", "restarts": 2, "last_why": "Close Agent (the desk Wall): Open Agent brings her back"},
        "ear": {"state": "up", "restarts": 0, "last_why": "opened again (Open Agent)"}}})
    rows = {r["lane"]: r for r in panel.lane_rows()}
    assert rows["stt"]["health"] == "warn" and rows["stt"]["parked"] is True and "Close Agent" in rows["stt"]["note"]
    assert rows["ear"]["health"] == "ok" and rows["ear"]["note"] == ""
    t = P.tile("stt", {"state": "stopped", "why": "Close Agent (x): Open Agent brings her back", "pid": 1}, True, "")
    assert t["parked"] and t["note"].startswith("Close Agent")
    t2 = P.tile("stt", {"state": "stopped", "why": "stopped from the Control Panel", "stopped": "2026-10-12T13:00:00"},
                True, "")
    assert t2["note"].startswith("stopped from the panel")


def test_the_panel_lays_out_to_fill_wraps_its_log_and_styles_its_scrollbars():
    html = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    body = html.split('<div class="work">')[1]
    left = body.split('<div id="leftCol">')[1].split('<div class="pane" id="logPane">')[0]
    for part in ('<div id="svcRow"></div>', 'id="regPane"', 'id="powerPane"'):
        assert part in left, part
    assert ".work #logPane pre{white-space:pre-wrap;overflow-wrap:anywhere;overflow-x:hidden}" in html
    assert "scrollbar-color:#3a3e46 var(--well)" in html and "::-webkit-scrollbar-thumb{" in html
    assert "' · states read '" in html
