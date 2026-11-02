#!/usr/bin/env python3
"""The Control Panel: every service of this install on one glass, with the All OS Off / Turn On All Services
switch, Park GPU (when the machine has a GPU step), journals, start scripts, and the document sheet.

Lifted 2026-10-06 from the house's panel server with its Control Panel fix applied (the registry decides the tiles,
All OS Off stops everything but this panel and Turn On All Services brings back exactly that, Park GPU is a toggle,
the header never says "All normal" while things are paused). What one machine runs (its units, ports, sections,
the GPU roster and its command) is in that machine's settings (lib/crissa_panel_conf.py), never here. A fresh
install's panel discovers ITS OWN services: units named crissa-*, or whose program lives in this install's folder.

    python3 scripts/crissa-panel-server.py                  # serve on 127.0.0.1:<port> (default 8097)
    python3 scripts/crissa-panel-server.py --port 18097     # another port (or CRISSA_PORT_PANEL)
    python3 scripts/crissa-panel-server.py --unit           # print a systemd user unit for this install

Routes:
  GET  /, /panel        the page (display/crissa-panel.html)
  GET  /v1/services     tiles: curated (settings) + every discovered service, health, the Off overlay, header
  GET  /v1/house        same tiles in the shape krew-panel expects (svc.services / gpu / battery)
  GET  /v1/registry     every unit the rules found, why, and what is new or removed (last few days)
  GET  /v1/os_power     the last Off/On report, whether Off is in force, what Off would stop now
  GET  /v1/journal      ?unit=<unit>|proc:<name>|all [&lines=N|all] [&before=<token>] [&file=<i>]  a page of the
                        log, newest last; when there is more above it, `marker` says so and `earlier` is the token
                        that reads the page before (journal cursor, or a byte offset for a log file). No line is cut
                        and N is not capped.
  GET  /v1/log-file     ?unit=<unit>|proc:<name> [&file=<i>]  the WHOLE log as text (journal, or the file)
  GET  /v1/script       ?unit=<unit>  its unit file and start script (discovered units only)
  GET  /v1/logs         ?lane=camera|voice|security  allowlisted log tails (Crissa lane files for voice;
                        camera/security honest empty notes — face stamps stay house-only)
  GET  /v1/security/summary  portable stub (no incidents store on Crissa; krew-panel can call it)
  GET  /v1/restart-report ?job=<id>  how a Restart / Run now went: a new run that stays up and answers, or the reason
  GET  /v1/caps         what this panel can do on this machine
  GET  /v1/mic          her microphone: the ear's voice master lock {on, ear}; POST /v1/mic {on} presses the Mic key
  GET  /v1/wall         the desk Wall: closed on purpose or not; POST /v1/wall {action: wall_close|wall_open} (2026-10-10)
  GET  /v1/agent        Close Agent / Open Agent (2026-10-12); POST /v1/agent {action: agent_close|agent_open}
  GET  /v1/addons       the bottom section: installed add-ons, placeholders for the missing ones, refused manifests
  POST /v1/action       restart|start|stop {unit}; os_off|os_on; mode_<name> (GPU modes, Park GPU = mode_off)
                        restart {unit: "lane:<name>"}: a lane the launcher runs (scripts/crissa.py) is restarted by it
                        A unit tile's Restart is checked (new run, still up after a settle, its port answering) and a
                        refusal or a fall-over is named. A timer's Restart is "Run now" (its service, once). A
                        launcher piece (proc:<name>, lib/crissa_procs) is restarted by the Crissa launcher alone.

THE LAUNCHER'S LANES (2026-10-06): when Crissa runs from its launcher (one process group, no units), the launcher
restarts a lane that goes down (lib/crissa_watch) and writes <crissa home>/lanes.json. Each lane is a tile here
("Crissa lanes": state, restarts, the last reason); Restart asks the launcher; the journal is the lane's log and its
lines in logs/lanes.log. A launcher that is not running says so on every lane tile. The panel also shows each child
as proc:<name> (lib/crissa_procs) with its log and Restart; wire_voices folds a voice child into the Voices section.

THE LAUNCHER ITSELF (2026-10-06): the first tile of that section is the launcher (unit "launcher"): whether it runs as
its service (lib/crissa_service: a systemd user unit, or a launchd agent), how often the service started it again and
why (<crissa home>/launcher.json), or that it was started by hand and nothing brings it back. Restart/Start/Stop drive
the service; Restart while it runs by hand stops that one and starts the service. Its journal is logs/launcher.log
and the unit's journal. The discovered crissa-launcher unit is shown on this tile, not twice.
  GET  /doc, /v1/doc    the document sheet (a sheet-sized window over the wall that clears itself)
  POST /v1/screen       {doc, doc_title, doc_ttl} or {clear: ["doc"]}
  GET  /health

Loopback only by default. A POST from a page of another origin is refused (a web page in the browser must not
press the panel's keys).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_panel_addons as addons  # noqa: E402
import crissa_panel_conf as conf  # noqa: E402
import crissa_voice_panel as voice_panel  # noqa: E402
import crissa_procs as procs  # noqa: E402  the launcher's pieces: tiles, logs, Restart (2026-10-06)
import crissa_addon_link as addon_link  # noqa: E402  is the other plugin live? (the watcher crossover, 2026-10-06)
import crissa_units as zu  # noqa: E402
import crissa_watch  # noqa: E402  the launcher's lanes: lanes.json, a restart request (2026-10-06)
import crissa_service  # noqa: E402  the launcher's own service: who restarts the launcher (2026-10-06)
try:
    import crissa_capability  # noqa: E402  what this computer can do: one answer for both panels and the menu (2026-10-10)
except ImportError:              # an older core beside this panel: nothing is folded
    crissa_capability = None
from crissa_os_power import Power  # noqa: E402
from glass_prefs import GlassPrefs  # noqa: E402  Connect Walls shared prefs
import crissa_guard  # noqa: E402  round 46: the one loopback rule every server of a pack keeps

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): the panel took a POST
# whose Origin equalled its Host without asking whether that Host was this machine, so a page that rebinds its own
# name to 127.0.0.1 (same-origin, Origin = Host = the attacker's name) pressed the panel's keys, and every GET (the
# house rows, the services, the logs) answered any page. lib/crissa_guard refuses, first in every do_*, a Host that is
# not this machine, a foreign Origin, and a cross-site mark with no loopback Referer. A panel bound by its settings to
# another address (`host`) counts that address as this machine. Kill: CRISSA_LOOPBACK_GUARD=0.
# THE BODY'S SIZE: the panel's biggest body is a glass-prefs record or a screen push, a few KB; 1 MB is far past
# either and was the size here before. A dial: CRISSA_PANEL_BODY_MAX (bytes); CRISSA_BODY_LENGTH=0 is the kill.
PANEL_BODY_MAX = int(os.environ.get("CRISSA_PANEL_BODY_MAX") or 1_000_000)


def guard_names() -> tuple:
    h = str(CFG.get("host") or "").strip().lower()
    return (h,) if h and h not in ("0.0.0.0", "::", "") else ()

PAGE = ROOT / "display" / "crissa-panel.html"
DOC_PAGE = ROOT / "display" / "crissa-doc.html"
OS_POWER_BIN = ROOT / "scripts" / "crissa-os-power"
DOC_WINDOW = ROOT / "scripts" / "crissa-doc-window.sh"

CFG: dict = {}
RULES: zu.Rules
POWER: Power
_lock = threading.Lock()
_svc_cache: dict = {"ts": 0.0, "data": None}
_reg_cache: dict = {"ts": 0.0, "reg": None}
# THE UNIT QUERIES ARE SHARED AND CACHED (round 13, 2026-10-12, .101): /v1/os_power asked systemd for every unit's
# properties (list-unit-files, then `show` on ~150 units, each one parsed again by the user manager) on every call, and
# the page asks every 5 s - the user manager ran at 91 % while the Control Panel was open; in a background tab the
# browser runs all three polls once a minute, so it came as five list-unit-files at :21 of every minute. Now the plan
# (what Off would stop) is made at most every PANEL_PLAN_TTL_S, one maker at a time; the registry check takes the same
# turn; an action drops both, so the page shows the result of a press at once. Kill: CRISSA_PANEL_PLAN_TTL=0 (a plan
# on every call, as before).
_plan_cache: dict = {"ts": 0.0, "plan": None}
_unit_lock = threading.Lock()


def plan_ttl_s(env=None) -> float:
    e = os.environ if env is None else env
    try:
        return max(0.0, float(str(e.get("CRISSA_PANEL_PLAN_TTL", "60")).strip() or "60"))
    except ValueError:
        return 60.0


def cached_plan(now: float | None = None):
    """POWER.plan(), made at most every plan_ttl_s() and by one caller at a time."""
    ttl = plan_ttl_s()
    now = time.time() if now is None else now
    with _unit_lock:
        if ttl > 0 and _plan_cache["plan"] is not None and now - _plan_cache["ts"] < ttl:
            return _plan_cache["plan"]
        plan = POWER.plan(run)
        _plan_cache.update(ts=now, plan=plan)
        return plan


def retire_stale_off(plan: dict | None = None) -> str:
    """A stale All OS Off is cleared before any view reads it (lib/crissa_os_power stale_why, round 15): older than
    the launcher's current start or the last Open Agent, or with everything it stopped running again (from the plan
    the panel already holds - no systemd call here). Returns why it was cleared, or ""."""
    try:
        if not POWER.read_snapshot():
            return ""
        home = Path(CFG["_state"])
        tab = procs.load(home)
        alive, _ = procs.launcher_alive(tab) if tab else (False, "")
        started = str((tab.get("launcher") or {}).get("started") or "") if alive else ""
        p = plan if plan is not None else _plan_cache.get("plan")
        now_units = live_units()                       # the state now, read for the tiles anyway (round 16)
        if now_units:
            rows, rows_at = [{"unit": u, "active": v.get("active")} for u, v in now_units.items()], _live_units["ts"]
        else:
            rows, rows_at = ((p or {}).get("units") if p else None), (_plan_cache.get("ts") if p else None)
        why = POWER.stale_why(rows, started, crissa_watch.agent_opened_at(home), rows_at=rows_at)
        if why and POWER.retire_snapshot(why):
            log("All OS Off cleared: %s" % why)
            return why
    except Exception as e:  # noqa: BLE001  a check never blanks the panel
        log(f"stale Off check: {e!r}"[:160])
    return ""


def drop_unit_caches() -> None:
    """After a press (start, stop, restart, Off, On): the next read asks systemd again."""
    with _unit_lock:
        _plan_cache.update(ts=0.0, plan=None)
        _reg_cache.update(ts=0.0)
_screen: dict = {"doc": None, "doc_title": None, "doc_ttl": None, "rev": 0}
_jobs: dict = {}                                   # restart job id -> its record (the page polls /v1/restart-report)
_glass = GlassPrefs()                              # /v1/house["glass"] + POST /v1/glass-prefs
_reason_cache: dict = {}                           # (unit, scope, run, state) -> the named reason


def log(msg: str) -> None:
    sys.stderr.write(time.strftime("%H:%M:%S ") + msg + "\n")
    sys.stderr.flush()


def setup(cfg: dict | None = None) -> None:
    global CFG, RULES, POWER
    CFG = conf.load() if cfg is None else cfg
    RULES = zu.Rules.from_conf(CFG)
    POWER = Power(CFG, RULES)
    _load_screen()
    try:
        _glass.load()
        log(f"glass prefs rev={_glass.record().get('rev')} from {_glass.path}")
    except Exception as e:  # noqa: BLE001
        log(f"glass prefs load failed: {type(e).__name__}: {e}")


# ---- systemd ---------------------------------------------------------------------------------------------
def run(argv: list, timeout: float = 20.0) -> "tuple[int, str, str]":
    return zu.default_run(argv, timeout)


def registry(force: bool = False) -> dict:
    """The registry, refreshed when the unit-file names change (a cheap check at most every 30 s)."""
    now = time.time()
    if not force and _reg_cache["reg"] is not None and now - _reg_cache["ts"] < 30:
        return _reg_cache["reg"]
    with _unit_lock:                                # one check at a time; a caller that waited reads its result
        if not force and _reg_cache["reg"] is not None and time.time() - _reg_cache["ts"] < 30:
            return _reg_cache["reg"]
        RULES.addon_units = zu._addon_units(CFG)       # an add-on installed or removed while the panel runs
        try:
            zu.cheap_check(rules=RULES)
        except Exception as e:  # noqa: BLE001 - a broken check must not blank the panel
            log(f"registry check failed: {e!r}")
        reg = zu.load(RULES.registry_file)
        _reg_cache.update(ts=time.time(), reg=reg)
        return reg


# ONE LIVE STATE FOR THE TILES AND THE LIST (round 16, .101 00:57: after Open Agent the tiles ASR, Audio8 and Ear bridge
# read "stopped ... Start brings it back" while the unit list said active/running): every unit's state as systemd has it
# NOW comes from one cheap call per scope (`list-units --all`: loaded units only, no `show` per unit), kept LIVE_UNITS_S
# (the services view's own 3 s), and both the unit list (live_registry) and the launcher's tiles (live_proc_tiles) read
# it. Kill: CRISSA_PANEL_LIVE_UNITS=0 (the list reads the plan alone, the tiles the launcher's table, as before).
LIVE_UNITS_S = 3.0
_live_units: dict = {"ts": 0.0, "units": None}


def live_units_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PANEL_LIVE_UNITS", "1")).strip().lower() not in ("0", "off", "false", "no")


def parse_list_units(text: str) -> dict:
    """{unit: {"active", "sub"}} from `systemctl list-units --all --no-legend --plain` (UNIT LOAD ACTIVE SUB DESC).
    Pure: tests read it."""
    out = {}
    for ln in (text or "").splitlines():
        f = ln.split()
        if f and f[0] in ("●", "*", "○"):         # a failed unit's mark, when systemd prints one
            f = f[1:]
        if len(f) >= 4 and "." in f[0]:
            out[f[0]] = {"active": f[2], "sub": f[3], "load": f[1]}
    return out


def live_units(now: float | None = None, runner=None) -> dict:
    """{unit: {"active", "sub", "load"}} now, every scope the rules read; {} when off or systemd cannot be asked."""
    if not live_units_on():
        return {}
    now = time.time() if now is None else now
    if _live_units["units"] is not None and now - _live_units["ts"] < LIVE_UNITS_S:
        return _live_units["units"]
    got: dict = {}
    for scope in RULES.scopes:
        try:
            rc, out, _err = (runner or run)(zu.sc(scope) + ["list-units", "--all", "--no-legend", "--plain",
                                                            "--no-pager"], 10.0)
        except Exception:  # noqa: BLE001  a scope that cannot be asked adds nothing
            continue
        if rc == 0:
            got.update(parse_list_units(out))
    _live_units.update(ts=now, units=got)
    return got


def live_proc_tiles(prow: list, units: dict) -> list:
    """The launcher's tiles read their unit's live state where the piece runs as a unit (service mode): a tile the
    table still calls stopped, exited or closed while systemd runs its crissa-<name>.service reads running, and one the
    table calls running while its unit is not active says what systemd says. Process mode (no such unit) is the table's,
    as before. Pure: tests read it."""
    out = []
    for t in prow or []:
        name = str(t.get("proc") or str(t.get("unit") or "").replace("proc:", ""))
        u = units.get("crissa-%s.service" % name) if name else None
        if not u or u.get("load") == "not-found":
            out.append(t)
            continue
        t = dict(t)
        if u.get("active") == "active" and t.get("active") != "active":
            t.update(active="active", sub=u.get("sub") or "running", health="ok", note="", parked=False)
        elif u.get("active") in ("inactive", "failed") and t.get("active") == "active":
            t.update(active=u["active"], sub=u.get("sub") or "dead", health="bad" if u["active"] == "failed" else "warn",
                     note="its unit is %s/%s (systemd)" % (u["active"], u.get("sub") or ""))
        out.append(t)
    return out


def live_registry(reg: dict) -> dict:
    """THE UNIT LIST READS LIVE STATE (round 15, .101 13:37: "EVERY UNIT THIS PANEL WATCHES ... looked 2026-10-11
    02:04", and crissa-aligner.service "manual - inactive/dead" while it ran): the registry is looked at again only
    when the unit files change, so its states were those of that look. Each row now carries the state of the panel's
    plan (systemd asked at most every plan_ttl_s, one maker at a time, round 13), and `states_at` says when."""
    try:
        p = cached_plan()
    except Exception:  # noqa: BLE001  the list as it was looked at
        return reg
    live = {r.get("unit"): r for r in p.get("units") or []}
    out = dict(reg)
    out["units"] = [dict(r, **{k: live[r.get("unit")][k] for k in ("active", "sub", "file_state")
                               if k in live[r.get("unit")]}) if r.get("unit") in live else r
                    for r in reg.get("units") or []]
    at = _plan_cache.get("ts") or time.time()
    now_units = live_units()                         # the same live read the tiles use (round 16)
    if now_units:
        out["units"] = [dict(r, active=now_units[r["unit"]]["active"], sub=now_units[r["unit"]]["sub"])
                        if r.get("unit") in now_units else r for r in out["units"]]
        at = _live_units.get("ts") or at
    out["states_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at))
    return out


def allowed_units(reg: dict | None = None) -> dict:
    """unit -> scope, for every unit the panel may name: the discovered ones, the curated tiles, itself."""
    reg = registry() if reg is None else reg
    out = {r["unit"]: r["scope"] for r in reg.get("units") or []}
    for t in CFG.get("tiles") or []:
        u = conf.full(t.get("unit") or "")
        out.setdefault(u, t.get("scope") or "")
    out.setdefault(conf.full(CFG.get("self_unit")), "user")
    return out


def resolve_unit(name: str) -> "tuple[str, str] | None":
    """(unit, scope) for a name the page sent, or None when the panel does not know it. Never a path."""
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9@._:-]{1,120}", name):
        return None
    units = allowed_units()
    for cand in (name, conf.full(name)):
        if cand in units:
            return cand, units[cand]
    return None


STEADY_S = float(os.environ.get("CRISSA_PANEL_STEADY_S", "120"))   # a run this old is steady, whatever came before


def unit_status(name: str, scope_hint: str = "") -> dict:
    """State of one unit. amber = up but restarting lately (a unit can be active and still be thrashing)."""
    unit = conf.full(name)
    scopes = [scope_hint] if scope_hint in ("user", "system") else list(RULES.scopes)
    for scope in scopes:
        rc, out, err = run(zu.sc(scope) + ["show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                                           "-p", "NRestarts", "-p", "ActiveEnterTimestampMonotonic", "-p", "Result",
                                           "-p", "ExecMainStatus", "-p", "ExecMainCode", "-p", "InvocationID",
                                           "-p", "InactiveEnterTimestampMonotonic"], 10.0)
        if rc != 0 and not out:
            return {"unit": conf.bare(name), "scope": scope, "active": "unknown", "sub": "", "restarts": 0,
                    "health": "warn", "note": "systemd unreachable: " + ((err or "").strip().splitlines() or ["?"])[-1][:120]}
        kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
        if kv.get("LoadState") in (None, "", "not-found"):
            continue
        active = kv.get("ActiveState", "")
        try:
            nrest = int(kv.get("NRestarts") or 0)
        except ValueError:
            nrest = 0
        try:
            entered = int(kv.get("ActiveEnterTimestampMonotonic") or 0)
            now_us = time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000
            age = (now_us - entered) / 1e6 if entered else 0
        except (ValueError, OSError, AttributeError):
            age = 0
        # BOUNCING IS NOW, NOT THIS ROUND (the User 2026-10-10, .101: "15 restarts lately" on an ear that had run
        # steadily for minutes): systemd's NRestarts counts every automatic restart since the unit was loaded, so a
        # unit is amber only while its current run is younger than STEADY_S
        bouncing = nrest >= 3 and age < STEADY_S
        health = ("warn" if bouncing else "ok") if active == "active" else ("warn" if active == "activating" else "bad")
        note = ""
        if health == "bad":
            note = failure_reason(unit, scope, kv)
        elif bouncing:
            note = (f"restarting: {nrest} restarts, this run up {int(age)} s · "
                    + (failure_reason(unit, scope, kv, last_only=True) or "see its journal"))
        elif active == "activating":
            note = "starting"
        row = {"unit": conf.bare(name), "scope": scope, "active": active, "sub": kv.get("SubState", ""),
               "restarts": nrest, "health": health, "note": note}
        if unit.endswith(".timer"):
            row["run_now"] = True
        if health != "ok" and POWER.parked_by_off(unit):
            row.update(health="warn", note=f"parked in {POWER.switch_name}", parked=True)
        elif health != "ok" and active != "activating":
            why_off = chosen_off(name)
            if why_off:                                  # round 26: off by the person's choice, never red
                row.update(health="warn", note=why_off, parked=True)
        return row
    return {"unit": conf.bare(name), "scope": None, "active": "not-found", "sub": "", "restarts": 0,
            "health": "bad", "note": "not installed"}


# OFF ON PURPOSE IS NOT DOWN (round 26, .101 2026-10-16: the header read "2 units down: Audio8, Wall" in red while the
# Wall was closed by the User and Audio8 was held because no role uses it; "intentional states should not read as a
# failure"). A unit stopped by the person's own close (lib/crissa_watch, wall-closed.json: the Wall, Close Agent's lanes)
# or held in standby by the voice demand (lib/crissa_voice_demand.held_engines) is parked - amber, named, not counted as
# down - as process mode has done since round 17. Kill: CRISSA_PANEL_CHOSEN_OFF=0.
def chosen_off(name: str, home: Path | None = None) -> str:
    """Why `name` is off by choice, or ""."""
    if os.environ.get("CRISSA_PANEL_CHOSEN_OFF", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    bare = conf.bare(name)
    if not bare.startswith("crissa-"):
        return ""
    lane = bare[len("crissa-"):]
    h = home or _lane_home()
    try:
        rec = crissa_watch.closed(h, lane)
        if rec:
            return "closed by you%s - open it again from her window or here" % (
                (" (%s)" % rec.get("by")) if rec.get("by") else "")
        if crissa_watch.agent_closed(h) and lane not in ("wall", "panel", "launcher", "feed", "player"):
            return "held by Close Agent - Open Agent brings it back"
    except Exception:  # noqa: BLE001
        pass
    try:
        import crissa_voice_demand as _vd
        held = _vd.held_engines(h)
        engine = _vd.LANE_ENGINE.get(lane)
        if lane in held or (engine and engine in held):
            return "standby: installed, no role uses it now"
        if engine:
            import crissa_voice_tiers as _vt
            use = _vd.in_use(_vt.read(h) or {}, _vd.named_engine(home=h))
            if use is not None and engine not in use:
                return "standby: installed, no role uses it now"
    except Exception:  # noqa: BLE001
        pass
    return ""


RESULT_WORDS = {
    "exit-code": "exit {status}", "signal": "killed by {signal}", "core-dump": "crashed ({signal}, core dumped)",
    "timeout": "timed out", "watchdog": "stopped answering systemd's watchdog", "start-limit-hit":
    "gave up: restarted too often (start-limit-hit)", "oom-kill": "killed: out of memory", "resources":
    "could not start: a file, folder or user it needs is missing (resources)", "protocol": "broke systemd's start protocol",
    "exec-condition": "its start condition said no", "condition": "its start condition said no",
}


def _signal_name(n: str) -> str:
    try:
        import signal as _sg
        return _sg.Signals(int(n)).name
    except (ValueError, TypeError):
        return f"signal {n}"


def failure_reason(unit: str, scope: str, kv: dict, *, last_only: bool = False) -> str:
    """A down unit's reason in words: its state, systemd's Result (exit code / signal / start limit / out of
    memory ...) and the line in its journal that says what went wrong. Cached per run, so a 3 s poll does not
    re-read the journal."""
    active, sub, result = kv.get("ActiveState", ""), kv.get("SubState", ""), kv.get("Result", "")
    key = (unit, scope, kv.get("InvocationID", ""), kv.get("InactiveEnterTimestampMonotonic", ""), active, result, last_only)
    if key in _reason_cache:
        return _reason_cache[key]
    bits = []
    if not last_only:
        if active == "failed":
            bits.append("failed")
        elif active in ("inactive", "deactivating") and result in ("", "success"):
            bits.append("stopped - nothing failed; Start brings it back" if unit.endswith(".service") else "stopped")
        else:
            bits.append(f"{active}/{sub}" if sub else (active or "down"))
        if result and result != "success":
            code = kv.get("ExecMainStatus", "")
            words = RESULT_WORDS.get(result, result).format(
                status=code or "?", signal=_signal_name(code) if code else "a signal")
            bits.append(words)
    line = ""
    if last_only or (active == "failed" or (result and result != "success")):
        argv = (["journalctl", "--user"] if scope == "user" else ["journalctl"]) + \
            ["-u", unit, "-n", "40", "-o", "cat", "--no-pager"]
        rc, out, _ = run(argv, 8.0)
        if rc == 0:
            line = procs.pick_reason(out.splitlines())
    if line:
        bits.append(line)
    why = " · ".join(b for b in bits if b)
    if len(_reason_cache) > 500:
        _reason_cache.clear()
    _reason_cache[key] = why
    return why


def capability_view() -> dict:
    """What this computer can do (lib/crissa_capability): the panel folds what it cannot into "Not on this computer"."""
    if crissa_capability is None:
        return {"facts": {}, "features": {}, "unavailable": []}
    try:
        return crissa_capability.features()
    except Exception as e:  # noqa: BLE001  never blanks the panel
        return {"facts": {}, "features": {}, "unavailable": [], "error": f"{type(e).__name__}: {e}"[:160]}


# a voice tile and the feature that decides whether this computer can run it
VOICE_FEATURE = {"turbo": "turbo", "multilingual": "multilingual", "warm": "keep_warm"}


def blank_unavailable(data: dict, cap: dict) -> None:
    """A tile this computer cannot run is marked blank with its reason (the page folds it into "Not on this
    computer", never a live key or an error): the GPU voices and the keep-warm, krew's placeholder, the GPU keys."""
    feats = cap.get("features") or {}
    for v in ((data.get("voices") or {}).get("tiles") or []):
        f = feats.get(VOICE_FEATURE.get(str(v.get("id") or ""), ""))
        if f and not f.get("ok"):
            v["blank"] = f.get("why") or "not on this computer"
    for ph in ((data.get("addons") or {}).get("missing") or []):
        f = feats.get(str(ph.get("slot") or ""))
        if f and not f.get("ok"):
            # GREYED, NEVER HIDDEN (the User 2026-10-28: "grey out any krew services if not present"; the 10-29 scan of
            # cut 12: blanking folded the whole krew section into one line under "Not on this computer"): the
            # placeholder stays in its section, greyed, with the reason as its note. CRISSA_PANEL_ADDON_FOLD=1 = the fold.
            if os.environ.get("CRISSA_PANEL_ADDON_FOLD", "0").strip().lower() in ("1", "on", "true", "yes"):
                ph["blank"] = f.get("why") or "not installed"
            else:
                ph["note"] = ph.get("note") or f.get("why") or "not installed"
    g = feats.get("gpu_modes")
    if data.get("gpu") and g and not g.get("ok"):
        data["gpu"] = None                            # no Mode row on a computer without its GPU
    # the launcher's own piece for a GPU voice (proc:multilingual "starts when Init has installed it", .101 round 3):
    # folded into "Not on this computer" with its voice, never a red tile
    gone = {"proc:" + p for p, f in (("turbo", "turbo"), ("multilingual", "multilingual"), ("warm", "keep_warm"))
            if (feats.get(f) or {}).get("ok") is False}
    if gone:
        data["services"] = [r for r in data.get("services") or [] if r.get("unit") not in gone]


def gpu_snapshot() -> dict | None:
    if not POWER.has_gpu():
        return None
    mode = POWER.gpu_mode()
    modes = POWER.gpu.get("modes") or {}
    return {"mode": mode, "label": POWER.mode_label(mode),
            "return_mode": POWER.gpu_return_mode() if mode == "off" else None,
            "modes": [{"id": k, "label": v.get("label") or k} for k, v in modes.items()],
            "off_label": POWER.mode_label("off"), "park_what": POWER.gpu.get("label") or "the GPU tenants",
            "os_off": POWER.summary()}


def addons_view(reg: dict | None = None) -> dict:
    """The bottom section (lib/crissa_panel_addons.py); an add-on that breaks never blanks the panel."""
    reg = registry() if reg is None else reg
    try:
        return addons.view(CFG, reg.get("units") or [])
    except Exception as e:  # noqa: BLE001
        log(f"add-ons: {e!r}")
        return {"section": addons.settings(CFG)["section"], "installed": [], "missing": [], "tiles": [], "claimed": [],
                "errors": [{"file": "", "why": f"{type(e).__name__}: {e}"[:160]}]}


def addon_rows(av: dict, by_unit: dict) -> list:
    """Tiles an installed add-on registered, in its own order, all in the bottom section. A unit tile is a full
    tile (state, journal, script, keys); a link opens its page; a report paints the add-on's own file."""
    rows = []
    for t in av.get("tiles") or []:
        common = {"label": t.get("label"), "port": t.get("port"), "href": t.get("href"), "section": t["section"],
                  "addon": t["addon"], "id": t.get("id") or "", "run": t.get("run") or None,
                  "off": t.get("off") or None, "off_now": bool(t.get("off_now"))}
        if t["kind"] == "link":
            rows.append(dict(common, unit="", scope="", active="", sub="", restarts=0, health="", note="", link=True))
            continue
        if t["kind"] == "status":
            st = addons.read_status(t["status"], t.get("stale_s") or 900)
            row = dict(common, unit=conf.bare(t.get("unit") or ""), scope=t.get("scope") or "", active="", sub="",
                       restarts=0, health=st["health"], note=st["note"], report=True)
            rows.append(_apply_off(row, t))
            continue
        b = conf.bare(t["unit"])
        reg_row = by_unit.get(t["unit"]) or {}
        if reg_row.get("systemd") == "unreachable":
            r = {"unit": b, "scope": reg_row.get("scope") or "user", "active": "unknown", "sub": "", "restarts": 0,
                 "health": "warn", "note": "systemd unreachable - state unknown"}
        else:
            r = unit_status(b, reg_row.get("scope") or t.get("scope") or "")
        r.update(common, why=reg_row.get("why") or f"add-on {t['addon']}")
        if t.get("masked"):
            r.update(health="warn", note="masked - off by design", parked=True)
        rows.append(_apply_off(r, t))
    return rows


def _apply_off(row: dict, tile: dict) -> dict:
    """An off switch that is on parks the tile. It is a choice, so it is not counted as down."""
    off_unit = (tile.get("off") or {}).get("unit")
    if off_unit and not row.get("off_now"):
        rc, out, _err = run(zu.sc(row.get("scope") or "user") + ["show", "-p", "UnitFileState", "--value", off_unit], 8.0)
        if rc == 0 and (out or "").strip() in ("disabled", "masked"):
            row["off_now"] = True
    if row.get("off_now") and not row.get("parked"):
        row.update(parked=True, note=row.get("note") or "off")
        if row.get("health") == "bad":
            row["health"] = "warn"
    return row


def voices_view() -> dict:
    """The Voices section: Chatterbox (Turbo), Multilingual V3 when installed or offered, Pocket, Audio8, Keep-warm
    (lib/crissa_voice_panel.py). A broken voice module never takes the services list down with it."""
    try:
        return voice_panel.view()
    except Exception as e:  # noqa: BLE001
        return {"section": "Voices", "tiles": [], "error": "could not read the voices: %s" % e}


_link_cache: dict = {"ts": 0.0, "data": None}


def link_view(av: dict, pv: dict, now: float | None = None) -> dict:
    """The add-on link as the panel shows it: the launcher's own record when its beat is fresh (it re-checks every
    pass), else this panel's own check (the house runs no launcher), re-run every LINK_EVERY_S. Only while an
    add-on is live are its pieces watched here; a piece down then lights its row in the table, never before."""
    now = time.time() if now is None else now
    la = (procs.load(CFG["_state"]).get("addons") or {}) if (pv.get("launcher") or {}).get("alive") else {}
    if la.get("links") is not None and now - float(la.get("checked") or 0) <= 3 * addon_link.LINK_EVERY_S:
        out = dict(la, source="the launcher")
    else:
        if _link_cache["data"] is None or now - _link_cache["ts"] >= addon_link.LINK_EVERY_S:
            try:
                ms, _err = addons.manifests(CFG)
                ls = addon_link.links(CFG, CFG["_state"], ms)
            except Exception as e:  # noqa: BLE001 - never blanks the panel
                ls = [{"id": "?", "live": False, "installed": None, "pieces": [], "down": [],
                       "note": f"add-on link unreadable ({type(e).__name__}) - watching Crissa only"}]
            _link_cache.update(ts=now, data={"links": ls, "checked": round(now, 1), "note": addon_link.note(ls)})
        out = dict(_link_cache["data"], source="this panel")
    out["table"] = addon_link.watch_table(out.get("links") or [], bool((pv.get("launcher") or {}).get("alive")),
                                          panel=True)
    return out


def services_snapshot() -> dict:
    """Tiles, cached 3 s: curated tiles from the settings, then every discovered service they do not cover."""
    now = time.time()
    if _svc_cache["data"] is not None and now - _svc_cache["ts"] < 3:
        return _svc_cache["data"]
    reg = live_registry(registry())
    by_unit = {r["unit"]: r for r in reg.get("units") or []}
    rows, tiled = [], set()
    self_bare = conf.bare(CFG.get("self_unit"))
    curated = list(CFG.get("tiles") or [])
    if not any(conf.bare(t.get("unit")) == self_bare for t in curated):
        curated.insert(0, {"unit": self_bare, "label": CFG.get("self_label") or "Control Panel",
                           "port": CFG.get("port"), "section": CFG.get("self_section") or "Control Panel"})
    av = addons_view(reg)
    claimed = {conf.bare(u) for u in av.get("claimed") or []}
    curated = [t for t in curated if conf.bare(t.get("unit")) not in claimed]    # an add-on's tile moves to the bottom
    tiled |= claimed
    for t in curated:
        b = conf.bare(t.get("unit"))
        tiled.add(b)
        reg_row = by_unit.get(conf.full(b)) or {}
        r = dict(unit_status(b, reg_row.get("scope") or t.get("scope") or ""), label=t.get("label") or b,
                 port=t.get("port"), href=t.get("href"), section=t.get("section") or CFG.get("default_section"))
        rows.append(r)
    notes = zu.load_notes(RULES)
    for t in zu.panel_tiles(reg, notes, tiled=tiled, default_section=CFG.get("default_section") or "Services",
                            new_section=CFG.get("new_section") or "New · not yet sorted"):
        if t.get("systemd") == "unreachable":
            r = {"unit": t["name"], "scope": t["scope"], "active": "unknown", "sub": "", "restarts": 0,
                 "health": "warn", "note": "systemd unreachable - state unknown"}
        else:
            r = unit_status(t["name"], t["scope"])
        r.update(label=t["label"], port=t["port"], href=None, section=t["section"],
                 registry_new=bool(t.get("new_since")), why=t.get("why"))
        if t.get("new_since"):
            r["note"] = r.get("note") or f"new - found {str(t['new_since']).replace('T', ' ')[:16]}"
        rows.append(r)
    pv = proc_view()
    all_prow = [dict(t) for t in pv.get("tiles") or []]
    all_prow = live_proc_tiles(all_prow, live_units())   # a unit's own live state over the table's (round 16)
    prow = procs.without_live_units(all_prow, rows)  # a live crissa-<name> unit replaces its proc tile
    rows += prow                                      # the launcher's pieces (a pack's player, voices, ear), above the add-ons
    rows += addon_rows(av, by_unit)
    gone = {c["unit"] for c in zu.recent_changes(reg) if c.get("change") == "removed"}
    for r in rows:
        if conf.full(r["unit"]) in gone and conf.full(r["unit"]) not in by_unit and r.get("active") == "not-found":
            r["removed"] = True
            r["note"] = "removed - this unit is no longer on the machine"
    rows = [r for r in rows if conf.bare(r.get("unit")) != conf.bare(crissa_service.UNIT)]
    rows += launcher_rows() + lane_rows() + build_rows()
    retire_stale_off()                        # a stale All OS Off never lights the way back (round 15)
    os_off = POWER.summary()
    if os_off.get("in_force"):
        paused = set(os_off.get("paused") or [])
        for r in rows:
            if conf.full(r["unit"]) in paused and r.get("health") != "ok":
                r.update(health="warn", note=f"parked in {POWER.switch_name}", parked=True)
    friendly_labels(rows)
    watch_decorate(rows, now)                 # who watches each tile (the house wiring's line, the kit's watchers)
    data = {"ok": True, "ts": now, "services": rows, "fleet": {}, "sections": section_folds(rows),
            "os_off": os_off, "os_off_whole": POWER.whole, "gpu": gpu_snapshot(), "systemd": reg.get("systemd") or "ok",
            "title": CFG.get("title") or "Control Panel", "self_unit": self_bare,
            "addons": dict({k: av.get(k) for k in ("section", "sections", "installed", "missing", "errors")},
                           link=link_view(av, pv, now)),
            "launcher": dict(pv.get("launcher") or {}, present=bool(pv.get("present"))),
            "voices": voices_view(), "capability": capability_view()}
    wire_voices(data, all_prow)
    blank_unavailable(data, data["capability"])
    data["fleet"] = dict(fleet_of(data), **running_view())
    _svc_cache.update(ts=now, data=data)
    return data


def running_view() -> dict:
    """The header's "N units running": THE ONE COUNT the feed's own panel (its /panel page) says too, read from the
    launcher's table by lib/crissa_procs.running_count (the User 2026-10-10, .101 round 5: 24 here, 16 there). {} where
    the launcher never ran (the page then counts its tiles, as before)."""
    try:
        if not procs.load(CFG["_state"]):
            return {}
        got = procs.running_count(CFG["_state"])
        return {"running": int(got["running"]), "running_what": got["what"]}
    except Exception as e:  # noqa: BLE001 - a count never blanks the panel
        log(f"running count: {e!r}")
        return {}


def friendly_labels(rows: list) -> None:
    """A Crissa unit (crissa-<piece>.service, service mode) is named as its piece is in process mode, never by its unit
    name alone (the User 2026-10-10, .101's panel: sixteen tiles reading crissa-asr, crissa-stt, ...)."""
    for r in rows:
        bare = conf.bare(r.get("unit") or "")
        if not bare.startswith("crissa-") or (r.get("label") or bare) not in (bare, conf.full(bare)):
            continue
        lab = procs.LABELS.get(bare[len("crissa-"):])
        if lab:
            r["label"] = lab[0]


def fleet_of(data: dict) -> dict:
    """The header's count, over what the page SHOWS (the User 2026-10-10, .101: "2 units down, 1 warn" over tiles that
    were all green - a GPU voice this computer cannot run and a voice the Voices row draws were counted). A tile folded
    into "Not on this computer" is not counted; a voice is counted once, as its Voices tile. Each down or warn tile is
    named, so the header says which."""
    rows = [r for r in data.get("services") or [] if not r.get("link") and not r.get("report") and not r.get("blank")]
    voices = [v for v in ((data.get("voices") or {}).get("tiles") or []) if not v.get("blank") and v.get("unit")]
    down, warn, parked = [], [], 0
    for r in rows:
        name = str(r.get("label") or r.get("unit") or "?")
        if r.get("parked"):
            parked += 1
        elif r.get("health") == "bad":
            down.append(name)
        elif r.get("health") == "warn":
            warn.append(name)
    for v in voices:
        if v.get("state") == "bad":
            down.append(str(v.get("label") or v.get("id")))
        elif v.get("state") == "warn":
            warn.append(str(v.get("label") or v.get("id")))
    short = [n.split(" - ")[0] for n in down], [n.split(" - ")[0] for n in warn]
    return {"total": len(rows) + len(voices), "down": len(down), "warn": len(warn), "parked": parked,
            "down_names": short[0], "warn_names": short[1]}


# ---- krew-panel / house-plugin shape (2026-10-06) -----------------------------------------------------------
# krew-panel still speaks the house Control Panel dialect (/v1/house, /v1/logs, /v1/security/summary). Crissa
# keeps its native /v1/services and /v1/journal; these three routes are the thin bridge so a joined krew can
# read the Wall's panel without pointing at the house's own panel.
LOG_LANES = ("camera", "voice", "security")
LOG_TAIL_LINES = int(os.environ.get("CRISSA_LOG_TAIL", "80"))
LOG_LINE_MAX = int(os.environ.get("CRISSA_LOG_LINE_MAX", "8192"))


def _presence_root() -> str:
    """The pack lamp, when the launcher named one. No address: the panel does not guess a port."""
    return str(os.environ.get("CRISSA_PRESENCE_URL") or "").strip().rstrip("/")


def _presence_call(method: str, path: str, body: dict | None = None, timeout: float = 0.4):
    root = _presence_root()
    if not root:
        return 0, {"ok": False, "error": "no presence address"}
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        root + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode() or "{}")
        except ValueError:
            payload = {"ok": False, "error": "the lamp refused"}
        if not isinstance(payload, dict):
            payload = {"ok": False, "error": "the lamp refused"}
        return exc.code, payload
    except (OSError, ValueError, urllib.error.URLError):
        return 0, {"ok": False, "error": "the lamp did not answer"}
    if not isinstance(payload, dict):
        return 200, {"ok": False, "error": "the lamp did not answer"}
    return 200, payload


def _visit_note(data: dict) -> str:
    if data.get("error") == "secure_not_offered":
        return str(data.get("why") or "Secure is not offered on this machine")    # round 17: the camera's reason
    if data.get("error") == "a password is required":
        return "A password is required"
    if data.get("error") == "refused":
        return "The password was refused"
    if data.get("error") == "wait":
        return "Wait a moment"
    if data.get("held") or (data.get("hold") and data.get("via") == "keyboard_hold"):
        return "Secure held"
    if data.get("via") == "click_off":
        return "Secure off"
    if data.get("secure"):
        return "Secure on"
    if data.get("error"):
        err = str(data.get("error"))
        return err if len(err) < 80 else "the lamp refused"
    return "not Secure"


def _visit_action(act: str, body: dict) -> "tuple[int, dict]":
    """One click toggles Secure. A password holds it. The password is not kept and not logged."""
    if act == "visit_click":
        code, data = _presence_call("POST", "/v1/visit_lamp/click", {}, timeout=8.0)
    else:
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        secret = str(payload.get("password") if isinstance(payload, dict) and "password" in payload
                     else body.get("password") or "")
        code, data = _presence_call("POST", "/v1/visit_lamp", {"op": "hold", "password": secret}, timeout=8.0)
        secret = ""
    ok = bool(data.get("ok")) and code not in (0, 400, 403)
    clean = {k: v for k, v in data.items() if k != "password"}
    return (200 if code else 502), {"ok": ok, "action": act, "result": clean, "note": _visit_note(data)}


def house_payload() -> dict:
    """GET /v1/house: services_snapshot wrapped the way krew-panel's panel_units reads it."""
    snap = services_snapshot()
    out = {"ok": True, "ts": snap.get("ts"), "title": snap.get("title"),
           "svc": {"services": snap.get("services") or [], "summary": [], "fleet": snap.get("fleet"),
                   "announcer_muted": False},
           "gpu": snap.get("gpu"), "battery": None, "os_off": snap.get("os_off"),
           "systemd": snap.get("systemd"), "self_unit": snap.get("self_unit")}
    if _presence_root():
        code, lamp = _presence_call("GET", "/v1/visit_lamp", timeout=0.4)
        if code == 200 and lamp.get("visit_lamp"):
            info = lamp.get("visit_lamp_info") if isinstance(lamp.get("visit_lamp_info"), dict) else {}
            out["visit_lamp"] = lamp.get("visit_lamp")
            out["visit_lamp_info"] = info
            out["presence"] = {"visit_lamp": lamp.get("visit_lamp"), "visit_lamp_info": info,
                               "user_auth": lamp.get("user_auth"), "user_id": lamp.get("user_id")}
            out["auth"] = {"user_auth": lamp.get("user_auth") is True}
    try:
        out["glass"] = _glass.record()
    except Exception as e:  # noqa: BLE001
        log(f"glass prefs read failed: {type(e).__name__}")
    sec = security_view()                            # the Security toggle, its lock and the ask (round 17 follow-ups)
    if sec is not None:
        out["security"] = sec
    return out


def tail_lane(lane: str) -> dict:
    """Allowlisted log tails only — never an arbitrary path (same discipline as the house panel)."""
    key = str(lane or "").strip().lower()
    if key not in LOG_LANES:
        return {"ok": False, "error": f"unknown lane {lane!r}", "lines": []}
    if key == "camera":
        return {"ok": True, "lane": key, "lines": [], "note": "no camera lane on Crissa"}
    if key == "security":
        return {"ok": True, "lane": key, "lines": [],
                "note": "no security lane on Crissa (face stamps stay on the house panel)"}
    # voice: the launcher's ear / stt / asr / tts / lanes logs under <crissa home>/logs
    home = _lane_home() / "logs"
    paths = [home / n for n in ("ear.log", "stt.log", "asr.log", "tts.log", "lanes.log")]
    lines: list[str] = []
    for p in paths:
        try:
            if not p.is_file():
                continue
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-LOG_TAIL_LINES:]
            if tail:
                lines.append(f"── {p.name} ──")
                lines += [t.rstrip("\n")[:LOG_LINE_MAX] for t in tail]
        except OSError:
            continue
    if not lines:
        missing = [str(x) for x in paths if not x.is_file()]
        note = ("no feed — source missing: " + ", ".join(missing)) if missing else "source is present but empty"
        return {"ok": True, "lane": key, "lines": [], "note": note}
    return {"ok": True, "lane": key, "lines": lines[-LOG_TAIL_LINES * 2:]}


def lane_units() -> dict:
    """Which of this install's pieces write each log lane: the page's log keys say what they watch (the User
    2026-10-28, with the house panel's FaceID log key). The voice lane is the launcher's ear / stt / asr / tts logs,
    so its writers are those pieces when they are in the launcher's table; the camera and security lanes have no
    writer in this edition until a module brings one."""
    try:
        names = {str(t.get("unit") or "") for t in (proc_view().get("tiles") or [])}
    except Exception:  # noqa: BLE001 - a broken table never blanks the caps
        names = set()
    voice = [u for u in ("proc:ear", "proc:stt", "proc:asr", "proc:tts", "proc:turbo") if u in names]
    return {"camera": [], "voice": voice, "security": []}


# ---- the house CP wiring, the kit's half (the User 2026-10-28, "Crissa Wall + Krew = house now") -----------------
# What lib/panel_wiring gives the house panel and this server lacked: keys masked in every line shown (lib/panel_logs,
# the one masker, now shipped with this package), who watches each tile and when they last checked, the sections that
# fold. Paging, the Whole log route, named failure reasons and Run now were already here. Switches, read at call time:
# CRISSA_PANEL_LOG_MASK=0 shows lines unmasked; CRISSA_PANEL_WATCHED_BY=0 draws no watched-by line and no /v1/watchers;
# CRISSA_PANEL_FOLD=0 draws every section open.
try:
    import panel_logs as _plog
except ImportError:  # the masker is in this package; a tree without it shows lines as they are (said in caps)
    _plog = None


def _kit_switch(name: str, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(name, "1")).strip().lower() not in ("0", "off", "false", "no")


def mask_on(env: dict | None = None) -> bool:
    return _plog is not None and _kit_switch("CRISSA_PANEL_LOG_MASK", env)


def mask_text(text: str) -> str:
    """API keys, tokens, passwords and bearer headers become [masked] in anything the panel shows (lib/panel_logs.mask)."""
    if not mask_on() or not text:
        return text
    return _plog.mask(text)


def mask_lines(res: dict) -> dict:
    """A journal / tail answer with its `lines` (and `text`, `unit_file`) masked. The whole log is masked where it is sent."""
    if not mask_on() or not isinstance(res, dict):
        return res
    if isinstance(res.get("lines"), list):
        res["lines"] = [mask_text(str(ln)) for ln in res["lines"]]
    for k in ("text", "unit_file"):
        if isinstance(res.get(k), str) and res[k]:
            res[k] = mask_text(res[k])
    return res


def _clock(ts) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts))) if ts else ""
    except (TypeError, ValueError, OverflowError):
        return ""


def watchers_view(now: float | None = None) -> dict:
    """Who watches this install's tiles: the launcher's watch (lib/crissa_watch, each lane's last health check),
    systemd (Restart= on the units it runs), and this panel (every tile's state while it is read)."""
    now = time.time() if now is None else now
    st = crissa_watch.read_status(_lane_home())
    lanes = st.get("lanes") or {}
    last = max([float(l.get("checked_at") or 0) for l in lanes.values()] + [0.0])
    return {"ok": True, "on": _kit_switch("CRISSA_PANEL_WATCHED_BY"), "watchers": [
        {"id": "launcher", "label": "the launcher's watch", "alive": bool(st.get("launcher_alive")), "last": last or None,
         "how": "brings a lane back that exits or stops answering its /health; its reason is on the tile (lib/crissa_watch)"},
        {"id": "systemd", "label": "systemd", "alive": True, "last": None,
         "how": "starts a unit again on its Restart= rule; the panel reads its state and journal"},
        {"id": "panel", "label": "this Control Panel", "alive": True, "last": now,
         "how": "every tile's state and named reason, while the panel is read"}]}


def watch_decorate(rows: list, now: float | None = None) -> list:
    """Each tile's `watched_by` and `watch_line` ("the launcher's watch 07:41 · this panel 07:42"): a lane is the
    launcher's, a launcher piece the launcher's, a unit systemd's; this panel watches them all while it is read.
    A watcher that has not checked in `ttl` is said STALE, as the house's line says it."""
    if not _kit_switch("CRISSA_PANEL_WATCHED_BY"):
        return rows
    now = time.time() if now is None else now
    try:
        st = crissa_watch.read_status(_lane_home())
    except Exception:  # noqa: BLE001
        st = {}
    lanes = st.get("lanes") or {}
    alive = bool(st.get("launcher_alive"))
    for r in rows:
        if not isinstance(r, dict) or r.get("placeholder") or r.get("link"):
            continue
        u = str(r.get("unit") or "")
        wb = []
        if u.startswith("lane:") or u.startswith("proc:") or u == "launcher":
            l = lanes.get(u[5:]) if u.startswith("lane:") else None
            at = float((l or {}).get("checked_at") or 0) or None
            fresh = alive and (at is None or now - at < 900)
            wb.append({"id": "launcher", "label": "the launcher's watch", "last": at, "fresh": fresh,
                       "how": "restarts the lane; checks its /health"})
        elif r.get("scope") in ("user", "system"):
            wb.append({"id": "systemd", "label": "systemd", "last": None, "fresh": True, "how": "its Restart= rule"})
        wb.append({"id": "panel", "label": "this panel", "last": now, "fresh": True, "how": "state and reason while read"})
        r["watched_by"] = wb
        r["watch_line"] = " \u00b7 ".join((w["label"] + (" " + _clock(w["last"]) if w["last"] else "") +
                                            ("" if w["fresh"] else " STALE")) for w in wb)
    return rows


def section_folds(rows: list) -> dict:
    """Which sections start folded (one line, the count, the worst lamp): the not-yet-sorted section and any other
    with more than FOLD_AT tiles; a red tile opens its section by itself (the page). The first section never folds."""
    if not _kit_switch("CRISSA_PANEL_FOLD"):
        return {}
    counts: dict = {}
    order: list = []
    for r in rows:
        g = str((r or {}).get("section") or "")
        if g and g not in counts:
            order.append(g)
        counts[g] = counts.get(g, 0) + 1
    new = str(CFG.get("new_section") or "New \u00b7 not yet sorted")
    out = {}
    for g in order[1:]:
        if g == new or counts.get(g, 0) > FOLD_AT:
            out[g] = {"fold": True}
    return out


FOLD_AT = int(os.environ.get("CRISSA_PANEL_FOLD_AT", "8") or 8)


def security_summary() -> dict:
    """Portable stub: Crissa ships no incident archive. Honest empty so krew-panel panel_security works."""
    return {"ok": True, "incidents": 0, "latest": None, "cache": None, "archive": 0,
            "note": "Crissa has no security incident store; face stamps stay on the house panel"}


# ---- the launcher's lanes ----------------------------------------------------------------------------------
LANE_LABELS = {"asr": "Ear · speech recognizer (asr)", "stt": "Ear · speech to text (stt)", "ear": "Ear · turns to the agent",
               "aligner": "Aligner (better mouths)", "tts": "Voice (tts)", "turbo": "Voice (Turbo)",
               "knowledge": "Knowledge - what she knows (her docs, the camera's, your files)"}


_KN: dict = {"at": 0.0, "line": ""}


def knowledge_line(timeout: float = 0.6, now: float | None = None) -> str:
    """The knowledge lane's headline (GET /v1/status): documents, sources, and that nothing leaves this machine.
    Cached 30 s; '' when the lane does not answer."""
    import time as _t
    now = _t.time() if now is None else now
    if now - _KN["at"] < 30.0:
        return _KN["line"]
    _KN["at"] = now
    try:
        import crissa_ports
        url = crissa_ports.url("knowledge") + "/v1/status"
        with crissa_egress.urlopen(url, timeout, "the Control Panel: the knowledge lane") as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        _KN["line"] = str(d.get("headline") or "")
    except Exception:  # noqa: BLE001
        _KN["line"] = ""
    return _KN["line"]


def _lane_home() -> Path:
    return Path(CFG.get("_state") or crissa_watch.HOME)


def launcher_rows() -> list:
    """The launcher's tile: its service's state and the last reason it was started again. None on the house (its
    units are its own) or when this Crissa has never run from its launcher and has no service."""
    st = crissa_service.status(home=_lane_home(), run=run)
    L = st.get("launcher") or {}
    if not st.get("installed") and not L.get("pid"):
        return []
    health, note = crissa_service.describe(st)
    return [{"unit": "launcher", "scope": "launcher", "active": st.get("active") or ("running" if L.get("alive") else "stopped"),
             "sub": st.get("sub") or L.get("under") or "", "restarts": int(st.get("restarts") or 0), "health": health,
             "note": note, "label": "Crissa launcher" + (" (%s)" % st.get("unit") if st.get("installed") else ""),
             "port": None, "href": None, "section": "Crissa lanes", "service": {k: st.get(k) for k in (
                 "manager", "installed", "enabled", "file", "result", "exit_status")},
             "why": "starts and restarts the lanes; its own service starts it again (lib/crissa_service)"}]


def build_rows() -> list:
    """Her pictures for this screen (round 14, lib/wall_ascii_disk.py): built at idle on this machine; one row with the
    numbers while there is a build to tell about. Nothing to press: the build runs and pauses by itself."""
    try:
        import crissa_build
        b = crissa_build.build_view(_lane_home())
    except Exception:  # noqa: BLE001 - the row is information only
        return []
    if b.get("pct") is None:
        return []
    return [{"unit": "build:screen", "scope": "info", "active": "active" if b.get("running") else "inactive",
             "sub": "", "restarts": 0, "health": "warn" if b.get("stopped") else "ok", "note": b.get("note") or "",
             "label": "Built for this screen", "port": None, "href": None, "section": "Crissa lanes",
             "why": "her pictures for this screen size and look, built at idle on this machine (lib/wall_ascii_disk.py)"}]


def lane_rows() -> list:
    """One tile per lane the launcher watches; none when Crissa does not run from its launcher here."""
    st = crissa_watch.read_status(_lane_home())
    lanes = st.get("lanes") or {}
    rows = []
    for name, l in lanes.items():
        n = int(l.get("restarts") or 0)
        state = str(l.get("state") or "")
        if st.get("stopped_at") and not st.get("launcher_alive"):
            health, note = "warn", "Crissa stopped (%s) - start it: python3 scripts/crissa.py" % (st.get("stopped_why") or "stopped")
        elif not st.get("launcher_alive"):
            health, note = "bad", "the launcher is not running, so nothing restarts this lane - start Crissa: python3 scripts/crissa.py"
        elif state == "up":
            health, note = ("warn", "restarted ×%d - last: %s" % (n, l.get("last_why"))) if n else ("ok", "")
        elif state == "restarted" and not n and l.get("last_why") == crissa_watch.OPENED_WHY:
            health, note = "ok", str(l.get("last_why"))      # Open Agent brought it back (round 16): not a fault
        elif state in ("restarted", "failing"):
            health, note = "warn", (l.get("last_why") if state == "restarted" else "health: %s" % l.get("last_health"))
        elif state == "given_up":
            health, note = "bad", "given up after restarts - press Restart. Last: %s" % l.get("last_why")
        elif state == "closed":                # closed on purpose (Close Agent, a closed Wall): parked, never red
            health, note = "warn", str(l.get("last_why") or "closed on purpose")
        elif state == "served":                # another copy answers on its port (round 17, 2026-10-13): never red
            health, note = "ok", str(l.get("last_why") or "another copy answers on its port")
        elif state == "standby":               # a voice no role uses, held (round 17, 2026-10-13): never red
            health, note = "ok", str(l.get("last_why") or "installed, not running")
        else:
            health, note = "bad", "%s - %s" % (state or "down", l.get("last_why") or "")
        if name == "knowledge" and state == "up":      # what she knows, in one line (2026-11-01): the lane's own status
            kn = knowledge_line()
            if kn:
                note = (note + " · " if note else "") + kn
        rows.append({"unit": "lane:" + name, "scope": "launcher", "active": state or "unknown", "sub": "",
                     "restarts": n, "health": health, "note": note, "label": LANE_LABELS.get(name, name),
                     "parked": state in ("closed", "standby"),
                     "port": l.get("port"), "href": None, "section": "Crissa lanes", "lane": name,
                     "why": "run and restarted by the launcher (scripts/crissa.py, lib/crissa_watch)"})
    return rows


def lane_journal(name: str, lines: int) -> dict:
    home = _lane_home()
    out = []
    for f, keep in ((home / "logs" / "lanes.log", lambda ln: (" %s: " % name) in ln), (home / "logs" / ("%s.log" % name), None)):
        try:
            got = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        out += [ln for ln in got if keep is None or keep(ln)][-lines:]
    return {"ok": True, "unit": "lane:" + name, "lines": out}


def proc_view() -> dict:
    """The Crissa launcher's children (lib/crissa_procs); none where it never ran (the house runs units)."""
    try:
        return procs.view(CFG["_state"])
    except Exception as e:  # noqa: BLE001 - a broken table never blanks the panel
        log(f"launcher table: {e!r}")
        return {"present": True, "launcher": {"alive": False, "why": f"cannot read the launcher's table: {e}"[:160]},
                "tiles": []}


def wire_voices(data: dict, prow: list) -> None:
    """When the Voices section is in the snapshot (lib/crissa_voice_panel.py), its tiles carry their launcher
    child's keys, log and crash reason, and that child is not drawn a second time in the services list."""
    vo = data.get("voices")
    if not isinstance(vo, dict) or not prow:
        return
    used = procs.wire_voices(vo, prow)
    if used:
        data["services"] = [r for r in data["services"] if r.get("proc") not in used]


# ---- journal / script ------------------------------------------------------------------------------------
_EXEC_ARGV_RE = re.compile(r"argv\[\]=([^;]*)")
_EXEC_PATH_RE = re.compile(r"\bpath=([^;]+)")
_SCRIPT_EXTS = {".sh", ".bash", ".py", ".service", ".conf", ".timer", ".path", ".socket", ""}


def parse_exec_start(raw: str) -> list:
    """systemd's ExecStart (`{ path=... ; argv[]=... }` or a bare line) as argv."""
    raw = raw or ""
    m = _EXEC_ARGV_RE.search(raw)
    if m:
        return m.group(1).strip().split()
    m = _EXEC_PATH_RE.search(raw)
    if m:
        return [m.group(1).strip()]
    return raw.strip().lstrip("@-:+!").split()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def script_view(unit: str, scope: str) -> dict:
    rc, cat, err = run(zu.sc(scope or "user") + ["cat", unit], 10.0)
    rc2, out, _ = run(zu.sc(scope or "user") + ["show", unit, "-p", "ExecStart"], 10.0)
    argv = parse_exec_start(out.partition("=")[2] if out else "")
    roots = [Path(p) for p in CFG.get("script_roots") or []]
    limit = int(CFG.get("script_max_bytes") or 200000)
    script, text, why = None, None, ""
    for a in argv:
        p = Path(a)
        if not p.is_absolute() or not p.is_file():
            continue
        if p.suffix not in _SCRIPT_EXTS:
            continue
        if not any(_is_under(p, r) for r in roots):
            why = f"{p.name} is outside the folders the panel reads"
            continue
        try:
            raw = p.read_bytes()[:limit]
        except OSError as e:
            why = str(e)
            continue
        if b"\0" in raw[:4096]:
            continue
        script, text = str(p), raw.decode("utf-8", errors="replace")
    return {"ok": True, "unit": unit, "unit_file": cat if rc == 0 else (err or "").strip()[:300],
            "exec": argv, "script": script, "text": text, "why": why if not script else ""}


def _lines_arg(v) -> "int | None":
    """N lines, or None for every line ("all"). No upper cap: a person who asks for more gets more."""
    v = str(v or "").strip().lower()
    if v == "all":
        return None
    try:
        n = int(v)
    except ValueError:
        n = 0
    return n if n > 0 else int(CFG.get("journal_lines") or 80)


def _fmt_entry(e: dict) -> str:
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(int(e.get("__REALTIME_TIMESTAMP") or 0) / 1e6))
    except (TypeError, ValueError, OverflowError):
        ts = "?"
    msg = e.get("MESSAGE")
    if isinstance(msg, list):                          # journald hands binary messages over as a byte list
        try:
            msg = bytes(msg).decode("utf-8", "replace")
        except (TypeError, ValueError):
            msg = str(msg)
    ident = e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or ""
    pid = e.get("_PID") or e.get("SYSLOG_PID") or ""
    return f"{ts} {e.get('_HOSTNAME') or ''} {ident}{('[' + str(pid) + ']') if pid else ''}: {'' if msg is None else msg}"


def _journal_scope(scope: str, units: list, n: "int | None", cursor: str = "") -> "tuple[list, str, str]":
    """(lines oldest first, cursor of the oldest shown when more is above it, error). Reads newest-first with one
    entry extra to know whether there is more; `cursor` continues above an earlier page."""
    argv = (["journalctl", "--user"] if scope == "user" else ["journalctl"])
    for u in units:
        argv += ["-u", u]
    argv += ["-o", "json", "-r", "--no-pager"]
    if n is not None:
        argv += ["-n", str(n + 1)]
    if cursor:
        argv += [f"--after-cursor={cursor}"]
    rc, out, err = run(argv, 60.0)
    if rc != 0:
        return [], "", ((err or "").strip()[:300] or f"journalctl rc {rc}")
    ents = []
    for ln in out.splitlines():
        try:
            ents.append(json.loads(ln))
        except ValueError:
            continue
    more = n is not None and len(ents) > n
    if more:
        ents = ents[:n]
    ents.reverse()
    return [_fmt_entry(e) for e in ents], (str(ents[0].get("__CURSOR") or "") if more and ents else ""), ""


def _token(d: dict) -> str:
    import base64
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode()


def _untoken(t: str) -> dict:
    import base64
    try:
        d = json.loads(base64.urlsafe_b64decode((t or "").encode() + b"==="))
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def journal_units_for(u: str, scope: str) -> list:
    """A timer's tape is the timer AND the service it starts (a janitor's run lives in its service's journal)."""
    out = [u]
    if u.endswith(".timer"):
        rc, o, _ = run(zu.sc(scope or "user") + ["show", u, "-p", "Triggers", "--value"], 10.0)
        out += [t for t in (o or "").split() if t.endswith(".service")]
    return out


def output_files(u: str, scope: str) -> list:
    """Files a unit writes besides the journal (StandardOutput=/StandardError= append:/file:/truncate:)."""
    rc, out, _ = run(zu.sc(scope or "user") + ["cat", u], 10.0)
    files = []
    for ln in (out or "").splitlines():
        ln = ln.strip()
        for key in ("StandardOutput=", "StandardError="):
            if ln.startswith(key):
                val = ln[len(key):]
                for pre in ("append:", "file:", "truncate:"):
                    if val.startswith(pre) and val[len(pre):] not in files:
                        files.append(val[len(pre):])
    return files


def live_file(files: list) -> "int | None":
    """The index of the file a Crissa unit writes its output to (StandardOutput=append:<home>/logs/<name>.log): that is
    its live log. The unit's journal holds only systemd's own lines (Started, Stopped), so a tape read from the journal
    stood still at the unit's start (the User 2026-10-10, .101). None for any other unit."""
    logs = _lane_home() / "logs"
    for i, f in enumerate(files or []):
        if _is_under(Path(f), logs):
            return i
    return None


def _marker(n_shown: int, what: str) -> str:
    return f"── earlier lines of {what} not shown ({n_shown} newest here) · Earlier reads the page before, Whole log reads it all ──"


def journal(unit: str, lines, before: str = "", file: str = "") -> dict:
    n = _lines_arg(lines)
    if unit.startswith("proc:"):
        name = unit[5:]
        c = (procs.load(CFG["_state"]).get("children") or {}).get(name) if procs.NAME.fullmatch(name) else None
        if not c or not c.get("log"):
            return {"ok": False, "unit": unit, "lines": [], "error": f"{name!r} is not one of the launcher's pieces"}
        return _file_page(unit, c["log"], n, before)
    if unit == "all":
        reg = registry()
        by_scope: dict = {}
        for r in reg.get("units") or []:
            by_scope.setdefault(r["scope"], []).append(r["unit"])
        cur = _untoken(before)
        out_lines, nxt, errs = [], {}, []
        for scope, units in by_scope.items():
            if before and scope not in cur:
                continue                               # that scope's journal was read to its start already
            got, c, err = _journal_scope(scope, units, n, cur.get(scope, ""))
            if err:
                errs.append(f"[{scope}] {err}")
            out_lines += got
            if c:
                nxt[scope] = c
        out_lines.sort(key=lambda ln: ln[:24])
        res = {"ok": True, "unit": "all", "lines": errs + out_lines}
        if nxt:
            res.update(earlier=_token(nxt), marker=_marker(len(out_lines), "every service's journal"))
        return res
    if unit == "launcher":
        return {"ok": True, "unit": "launcher", "lines": crissa_service.journal(_lane_home(), n or 80, run=run)}
    if str(unit).startswith("lane:"):
        name = str(unit)[5:]
        if name not in (crissa_watch.read_status(_lane_home()).get("lanes") or {}):
            return {"ok": False, "error": f"{unit!r} is not one of the launcher's lanes"}
        return lane_journal(name, n or 80)
    got = resolve_unit(unit)
    if not got:
        return {"ok": False, "error": f"{unit!r} is not one of this install's units"}
    u, scope = got
    files = output_files(u, scope)
    live = live_file(files)
    if file in ("", None) and live is not None:
        file = str(live)                               # a Crissa unit writes its output to its own file, not the journal
    if file == "journal":
        file = ""
    if file not in ("", None):
        try:
            page = _file_page(u, files[int(file)], n, before)
        except (ValueError, IndexError):
            return {"ok": False, "unit": u, "lines": [], "error": "no such log file for this unit"}
        page.update(files=files, file_index=int(file))
        return page
    units = journal_units_for(u, scope)
    got_lines, cursor, err = _journal_scope(scope or "user", units, n, _untoken(before).get("c", "") if before else "")
    if err:
        return {"ok": False, "unit": u, "error": err, "lines": [], "files": files}
    res = {"ok": True, "unit": u, "units": units, "lines": got_lines, "files": files}
    if cursor:
        res.update(earlier=_token({"c": cursor}), marker=_marker(len(got_lines), u + "'s journal"))
    if files and not before:
        res["lines"] = res["lines"] + [f"── {u} also writes {f} · Logs → file {i} reads it ──" for i, f in enumerate(files)]
    return res


def _file_page(unit: str, path: str, n: "int | None", before: str) -> dict:
    try:
        b = int(before) if str(before or "").strip() else None
    except ValueError:
        b = None
    if n is None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"ok": False, "unit": unit, "lines": [], "error": f"{path}: {e.strerror or e}", "file": path}
        return {"ok": True, "unit": unit, "file": path, "lines": text.splitlines()}
    got = procs.read_log(path, n, b)
    got["unit"] = unit
    if got.get("earlier") is not None:
        got["earlier"] = str(got["earlier"])
    return got


def whole_log(unit: str, file: str = "") -> "tuple[int, bytes]":
    """The whole log as text: a launcher piece's file, a unit's own file, or its journal entire."""
    if unit.startswith("proc:"):
        name = unit[5:]
        c = (procs.load(CFG["_state"]).get("children") or {}).get(name) if procs.NAME.fullmatch(name) else None
        if not c or not c.get("log"):
            return 404, f"{name!r} is not one of the launcher's pieces\n".encode()
        try:
            return 200, Path(c["log"]).read_bytes()
        except OSError as e:
            return 404, f"{c['log']}: {e.strerror or e}\n".encode()
    got = resolve_unit(unit)
    if not got:
        return 400, f"{unit!r} is not one of this install's units\n".encode()
    u, scope = got
    if file in ("", None):
        live = live_file(output_files(u, scope))
        file = "" if live is None else str(live)
    if file == "journal":
        file = ""
    if file not in ("", None):
        try:
            return 200, Path(output_files(u, scope)[int(file)]).read_bytes()
        except (ValueError, IndexError, OSError) as e:
            return 404, f"no such log file for {u}: {e}\n".encode()
    argv = (["journalctl", "--user"] if scope == "user" else ["journalctl"])
    for x in journal_units_for(u, scope):
        argv += ["-u", x]
    rc, out, err = run(argv + ["-o", "short-iso", "--no-pager"], 300.0)
    if rc != 0:
        return 502, ((err or "").strip() or f"journalctl rc {rc}").encode() + b"\n"
    return 200, out.encode("utf-8", "replace")


# ---- actions ---------------------------------------------------------------------------------------------
def locked() -> bool:
    f = CFG.get("lock_file")
    if not f:
        return False
    try:
        first = Path(f).read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return False
    return first in ("on", "1", "true", "lock", "prod")


# THE PAGE SAYS IT BEFORE IT GOES (round 20, .101 2026-10-14 09:26: All OS Off from this panel stopped the launcher, its
# panel with it (PartOf) and her browser window the panel lives in, so the page never drew "stopped": "nothing greyed
# out but it closed before it had the chance"). The page greys itself the moment the switch is accepted, and the switch
# starts OFF_DRAW_S later, so the stopped state is on the glass before the window goes. Kill CRISSA_PANEL_OFF_DRAW_S=0.
OFF_DRAW_S = float(os.environ.get("CRISSA_PANEL_OFF_DRAW_S", "1.5") or 0)


def _spawn_logged(argv: list, logname: str) -> None:
    try:
        _spawn(argv, logname)
    except Exception as e:  # noqa: BLE001 - on a timer: nobody to answer, so it is said on stderr
        print("[panel] %s did not start: %s" % (" ".join(map(str, argv[-2:])), e), file=sys.stderr, flush=True)


def _spawn(argv: list, logname: str) -> None:
    logf = POWER.state_dir / logname
    logf.parent.mkdir(parents=True, exist_ok=True)
    with logf.open("ab") as fh:
        subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
                         env=dict(os.environ, CRISSA_PANEL_CALLER="1"))


def _props(unit: str, scope: str) -> dict:
    rc, out, _ = run(zu.sc(scope) + ["show", unit, "-p", "InvocationID", "-p", "ActiveState", "-p", "SubState",
                                     "-p", "Result", "-p", "ExecMainStatus", "-p", "Triggers",
                                     "-p", "InactiveEnterTimestampMonotonic"], 10.0)
    return dict(ln.split("=", 1) for ln in (out or "").splitlines() if "=" in ln)


def _port_of(unit: str) -> "int | None":
    b = conf.bare(unit)
    for t in CFG.get("tiles") or []:
        if conf.bare(t.get("unit") or "") == b and t.get("port"):
            return int(t["port"])
    n = (zu.load_notes(RULES).get("notes") or {}).get(conf.full(unit)) or {}
    if n.get("port"):
        return int(n["port"])
    if conf.full(unit) == conf.full(CFG.get("self_unit")):
        return int(CFG.get("port") or 0) or None
    return None


def _settle_s() -> float:
    try:
        return float(CFG.get("restart_settle_s", 3))
    except (TypeError, ValueError):
        return 3.0


def postcheck(unit: str, scope: str, before: str, port: "int | None") -> dict:
    """Did the restart take? A new run, active, still that run after a settle, its port answering. Else why."""
    wait_active = float(CFG.get("restart_active_s") or 30)
    t0 = time.time()
    p: dict = {}
    while time.time() - t0 < wait_active:
        p = _props(unit, scope)
        if p.get("InvocationID") and p.get("InvocationID") != before and p.get("ActiveState") in ("active", "failed"):
            break
        time.sleep(0.4)
    inv = p.get("InvocationID", "")
    if not inv or inv == before:
        return {"ok": False, "state": "no-restart", "note": f"{conf.bare(unit)}: systemd never started a new run "
                f"({p.get('ActiveState') or 'unknown'}) · " + (failure_reason(unit, scope, p, last_only=True) or "no journal line")}
    if p.get("ActiveState") != "active":
        return {"ok": False, "state": p.get("ActiveState") or "down", "note": f"{conf.bare(unit)}: " + failure_reason(unit, scope, p)}
    time.sleep(_settle_s())
    q = _props(unit, scope)
    if q.get("InvocationID") != inv or q.get("ActiveState") != "active":
        return {"ok": False, "state": "crashed", "note": f"{conf.bare(unit)} came up and fell over within "
                f"{_settle_s():.0f}s: " + failure_reason(unit, scope, q)}
    if port:
        wait = float((CFG.get("restart_port_s") or {}).get(conf.bare(unit), 0) or (CFG.get("restart_port_s") or {}).get("*", 0) or 120) \
            if isinstance(CFG.get("restart_port_s"), dict) else float(CFG.get("restart_port_s") or 120)
        t1 = time.time()
        while not procs._port_up(port):
            r = _props(unit, scope)
            if r.get("InvocationID") != inv or r.get("ActiveState") != "active":
                return {"ok": False, "state": "crashed", "note": f"{conf.bare(unit)} fell over before :{port} answered: "
                        + failure_reason(unit, scope, r)}
            if time.time() - t1 > wait:
                return {"ok": False, "state": "no-port", "note": f"{conf.bare(unit)} is running but :{port} did not answer "
                        f"in {wait:.0f}s · " + (failure_reason(unit, scope, r, last_only=True) or "no journal line")}
            time.sleep(1.0)
        return {"ok": True, "state": "up", "note": f"{conf.bare(unit)} restarted · :{port} answering"}
    return {"ok": True, "state": "up", "note": f"{conf.bare(unit)} restarted · running"}


def run_now_check(svc: str, scope: str, before: str) -> dict:
    """A timer's Run now: its service ran once - how did it end?"""
    t0, wait = time.time(), float(CFG.get("run_now_s") or 600)
    p: dict = {}
    while time.time() - t0 < wait:
        p = _props(svc, scope)
        if p.get("InvocationID") and p.get("InvocationID") != before and p.get("ActiveState") in ("inactive", "failed", "active"):
            if p.get("ActiveState") == "active" or p.get("Result") in ("success", ""):
                return {"ok": True, "state": "ran", "note": f"{conf.bare(svc)} ran" + (" and is running" if p.get("ActiveState") == "active" else ": success")}
            return {"ok": False, "state": "failed", "note": f"{conf.bare(svc)}: " + failure_reason(svc, scope, p)}
        time.sleep(0.5)
    return {"ok": False, "state": "slow", "note": f"{conf.bare(svc)} did not finish in {wait:.0f}s ({p.get('ActiveState') or '?'})"}


def _job(rec: dict, fn) -> dict:
    import uuid
    job = uuid.uuid4().hex[:12]
    rec = dict(rec, state="running", job=job)
    with _lock:
        _jobs[job] = rec
        for old in list(_jobs)[:-40]:
            _jobs.pop(old, None)

    def go():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "state": "error", "note": f"{type(e).__name__}: {e}"[:200]}
        with _lock:
            rec.update(res, state="done", ts=time.strftime("%H:%M:%S"))
            _jobs[job] = rec
        _svc_cache["ts"] = 0.0
    threading.Thread(target=go, name="cp-" + job, daemon=True).start()
    return rec


def start_restart(unit: str, scope: str) -> dict:
    """Restart with a post-check (the page polls /v1/restart-report). The panel restarting itself answers first.
    A timer's Restart runs its service once."""
    if unit == conf.full(CFG.get("self_unit")):
        threading.Timer(0.8, lambda: run(zu.sc(scope) + ["restart", unit], 30.0)).start()
        return {"ok": True, "action": "restart", "unit": unit, "self": True,
                "note": "Control Panel restarting - the page reconnects in a few seconds"}
    if unit.endswith(".timer"):
        svcs = [t for t in (_props(unit, scope).get("Triggers") or "").split() if t.endswith(".service")]
        if not svcs:
            return {"ok": False, "action": "restart", "unit": unit, "error": f"{unit} starts no service to run now"}
        svc = svcs[0]
        before = _props(svc, scope).get("InvocationID", "")
        rc, out, err = run(zu.sc(scope) + ["start", "--no-block", svc], 30.0)
        if rc:
            return {"ok": False, "action": "restart", "unit": unit, "error": (err or out).strip()[:300] or f"rc {rc}"}
        rec = _job({"unit": unit, "service": svc}, lambda: run_now_check(svc, scope, before))
        return {"ok": True, "action": "restart", "unit": unit, "job": rec["job"], "run_now": True,
                "note": f"Running {conf.bare(svc)} now…"}
    before = _props(unit, scope).get("InvocationID", "")
    rc, out, err = run(zu.sc(scope) + ["restart", "--no-block", unit], 30.0)
    if rc:
        return {"ok": False, "action": "restart", "unit": unit, "error": (err or out).strip()[:300] or f"systemctl rc {rc}",
                "note": f"{conf.bare(unit)}: " + ((err or out).strip()[:300] or f"systemctl rc {rc}")}
    port = _port_of(unit)
    rec = _job({"unit": unit}, lambda: postcheck(unit, scope, before, port))
    return {"ok": True, "action": "restart", "unit": unit, "job": rec["job"], "note": f"Restarting {conf.bare(unit)}…"}


def restart_report(job: str) -> dict:
    with _lock:
        rec = dict(_jobs.get(job or "") or {})
    if rec.get("proc"):
        got = procs.report(CFG["_state"], rec["proc"], job)
        return dict(got, unit=rec.get("unit"))
    return rec or {"state": "done", "ok": False, "note": "no such restart (the panel restarted since?)"}


def proc_script(name: str) -> dict:
    c = (procs.load(CFG["_state"]).get("children") or {}).get(name) if procs.NAME.fullmatch(name or "") else None
    if not c:
        return {"ok": False, "error": f"{name!r} is not one of the launcher's pieces"}
    roots = [Path(p) for p in CFG.get("script_roots") or []]
    script, text, why = None, None, ""
    for a in c.get("argv") or []:
        p = Path(a)
        if p.suffix not in (".py", ".sh") or not p.is_absolute() or not p.is_file():
            continue
        if not any(_is_under(p, r) for r in roots):
            why = f"{p.name} is outside the folders the panel reads"
            continue
        script, text = str(p), p.read_text(encoding="utf-8", errors="replace")
        break
    return {"ok": True, "unit": "proc:" + name, "unit_file": "started by the Crissa launcher (scripts/crissa.py):\n  "
            + " ".join(c.get("argv") or []) + f"\nlog: {c.get('log') or '?'}", "exec": c.get("argv") or [],
            "script": script, "text": text, "why": why if not script else ""}


def do_action(body: dict) -> "tuple[int, dict]":
    act = str(body.get("action") or "")
    if act in ("house_off", "house_on"):
        act = {"house_off": "os_off", "house_on": "os_on"}[act]
    if act in ("restart", "start", "stop") and body.get("unit") == "launcher":
        if locked():
            return 200, {"ok": False, "action": act, "unit": "launcher", "note": "The lock file is on - the panel is frozen"}
        got = crissa_service.control(act, home=_lane_home(), run=run)
        _svc_cache["ts"] = 0.0
        log(f"launcher {act}: {got.get('note')}")
        return 200, dict(got, action=act, unit="launcher")
    if act in ("restart", "start", "stop") and str(body.get("unit") or "").startswith("lane:"):
        name = str(body.get("unit"))[5:]
        st = crissa_watch.read_status(_lane_home())
        if name not in (st.get("lanes") or {}):
            return 400, {"ok": False, "action": act, "error": "not one of the launcher's lanes"}
        if act != "restart":
            return 200, {"ok": False, "action": act, "unit": body.get("unit"),
                         "note": "a launcher lane is restarted, not started or stopped, from here (it stops with Crissa)"}
        if locked():
            return 200, {"ok": False, "action": act, "unit": body.get("unit"), "note": "The lock file is on - the panel is frozen"}
        if not st.get("launcher_alive"):
            return 200, {"ok": False, "action": act, "unit": body.get("unit"),
                         "note": "the launcher is not running - start Crissa: python3 scripts/crissa.py"}
        crissa_watch.request_restart(name, _lane_home())
        _svc_cache["ts"] = 0.0
        log(f"lane restart asked: {name}")
        return 200, {"ok": True, "action": act, "unit": body.get("unit"),
                     "note": f"asked the launcher to restart {name}; it does on its next pass (under a second)"}
    if act in ("restart", "start", "stop") and str(body.get("unit") or "").startswith("proc:"):
        name = str(body.get("unit"))[5:]
        if locked():
            return 200, {"ok": False, "action": act, "unit": body.get("unit"), "note": "The lock file is on - the panel is frozen"}
        res = procs.request(CFG["_state"], name, act)
        if res.get("ok"):
            with _lock:
                _jobs[res["job"]] = {"state": "running", "job": res["job"], "proc": name, "unit": "proc:" + name}
                for old in list(_jobs)[:-40]:
                    _jobs.pop(old, None)
        _svc_cache["ts"] = 0.0
        code = 200 if res.get("ok") or procs.known(CFG["_state"], name) else 400
        return code, dict(res, action=act, unit="proc:" + name, note=res.get("note") or res.get("error") or "")
    if act in ("restart", "start", "stop"):
        got = resolve_unit(body.get("unit"))
        if not got:
            return 400, {"ok": False, "action": act, "error": "not one of this install's units"}
        unit, scope = got
        if act == "stop" and unit == conf.full(CFG.get("self_unit")):
            return 200, {"ok": False, "action": act, "unit": unit,
                         "note": "This is the Control Panel - stop it from a terminal; it is the switch you turn things back on with"}
        if locked():
            return 200, {"ok": False, "action": act, "unit": unit, "note": "The lock file is on - the panel is frozen"}
        if act == "restart":
            return 200, start_restart(unit, scope or "user")
        rc, out, err = run(zu.sc(scope or "user") + [act, unit], 60.0)
        _svc_cache["ts"] = 0.0
        st = unit_status(unit, scope)
        why = "" if rc else (err or out).strip()[:300]
        if act == "start" and not rc and st.get("health") == "bad":
            why = st.get("note") or ""
        return 200, {"ok": rc == 0 and not (act == "start" and st.get("health") == "bad"), "action": act, "unit": unit,
                     "state": st, "error": why,
                     "note": f"{conf.bare(unit)}: {st.get('active')}/{st.get('sub')}" + (f" · {why}" if why else "")}
    if act in ("os_off", "os_on"):
        why = POWER.refused()
        if why:
            return 200, {"ok": False, "action": act, "note": why}
        if locked():
            return 200, {"ok": False, "action": act, "note": "The lock file is on - the switch is frozen"}
        summ = POWER.summary()
        if act == "os_on" and not summ.get("in_force"):
            return 200, {"ok": True, "action": act, "already": True, "note": "All services are already on"}
        cmd = [sys.executable, str(OS_POWER_BIN), "off" if act == "os_off" else "on"]
        try:
            if act == "os_off" and OFF_DRAW_S > 0:
                # round 20: the page draws "stopped" before anything stops (see THE PAGE SAYS IT BEFORE IT GOES)
                t = threading.Timer(OFF_DRAW_S, _spawn_logged, args=(cmd, "os-power.log"))
                t.daemon = True
                t.start()
            else:
                _spawn(cmd, "os-power.log")
        except Exception as e:  # noqa: BLE001
            return 200, {"ok": False, "action": act, "error": str(e)[:160]}
        _svc_cache["ts"] = 0.0
        note = (f"{POWER.switch_name} - stopping every service except this panel. The list lands here."
                if act == "os_off" else f"{POWER.on_name} - exactly what was on before.")
        return 200, {"ok": True, "action": act, "started": True, "note": note, "os_off": summ}
    if act.startswith("mode_"):
        if not POWER.has_gpu():
            return 200, {"ok": False, "action": act, "note": "This machine has no GPU step in its settings"}
        if POWER.summary().get("in_force"):
            return 200, {"ok": False, "action": act, "note": f"{POWER.switch_name} is in force - {POWER.on_name} first"}
        if locked():
            return 200, {"ok": False, "action": act, "note": "The lock file is on - the GPU switch is frozen"}
        mode = act[5:]
        cur = POWER.gpu_mode()
        if mode == "off" and cur == "off":
            mode = POWER.gpu_return_mode()      # PARK GPU IS A TOGGLE: pressed again it returns to the mode it left
        if mode == "off":
            args = list(POWER.gpu.get("park_args") or ["off"])
            note = (f"{POWER.mode_label('off')} - {POWER.gpu.get('label') or 'the GPU tenants'} off the card. "
                    f"Press it again to return to {POWER.mode_label(cur)}.")
        elif mode in (POWER.gpu.get("modes") or {}):
            recipe = POWER.gpu_state().get("prev_recipe") if cur == "off" else None
            args, _ = POWER.mode_args(mode, recipe)
            note = f"Switching to {POWER.mode_label(mode)}."
        else:
            return 400, {"ok": False, "action": act, "error": f"unknown mode {mode!r}"}
        try:
            _spawn(POWER.gpu_cmd(args), "gpu-mode.log")
        except Exception as e:  # noqa: BLE001
            return 200, {"ok": False, "action": act, "error": str(e)[:160]}
        _svc_cache["ts"] = 0.0
        return 200, {"ok": True, "action": act, "mode": mode, "note": note}
    if act in ("run_now", "mark_off", "mark_on"):
        return _addon_key(act, body)
    if act in ("visit_click", "visit_hold"):
        return _visit_action(act, body)
    return 400, {"ok": False, "error": f"unknown action {act!r}"}


def _addon_tile(tile_id: str) -> dict | None:
    """The installed add-on tile with this id. The page cannot name a unit the manifest did not."""
    if not tile_id:
        return None
    for t in addons_view().get("tiles") or []:
        if t.get("id") == tile_id:
            return t
    return None


def _addon_key(act: str, body: dict) -> "tuple[int, dict]":
    """Run now, or Off / On, for one add-on tile. The unit or file is the one the manifest named."""
    tile = _addon_tile(str(body.get("id") or ""))
    if not tile:
        return 400, {"ok": False, "action": act, "error": "not one of this panel's tiles"}
    if locked():
        return 200, {"ok": False, "action": act, "id": tile.get("id"), "note": "The lock file is on - the panel is frozen"}
    scope = tile.get("scope") or "user"
    if act == "run_now":
        unit = addons.checked_unit((tile.get("run") or {}).get("unit"))
        if not unit:
            return 400, {"ok": False, "action": act, "error": "this tile has no run key"}
        rc, out, err = run(zu.sc(scope) + ["start", unit], 60.0)
        _svc_cache["ts"] = 0.0
        why = "" if rc == 0 else (err or out).strip()[:300]
        return 200, {"ok": rc == 0, "action": act, "id": tile.get("id"), "unit": unit,
                     "note": why or f"started {conf.bare(unit)}"}
    off = tile.get("off") or {}
    if off.get("file"):
        path = addons.off_file_ok(off.get("file"), addons.settings(CFG).get("home_env") or "")
        if not path:
            return 400, {"ok": False, "action": act, "error": "this tile's off file is not under the add-on's home"}
        try:
            if act == "mark_off":
                os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o644))
            elif os.path.islink(path):
                return 400, {"ok": False, "action": act, "error": "the off file is a link"}
            elif os.path.isfile(path):
                os.unlink(path)
        except OSError as exc:
            return 200, {"ok": False, "action": act, "id": tile.get("id"), "note": str(exc)[:200]}
        _svc_cache["ts"] = 0.0
        return 200, {"ok": True, "action": act, "id": tile.get("id"), "off_now": act == "mark_off"}
    unit = addons.checked_unit(off.get("unit"))
    if not unit:
        return 400, {"ok": False, "action": act, "error": "this tile has no off key"}
    verb = ["disable", "--now", unit] if act == "mark_off" else ["enable", "--now", unit]
    rc, out, err = run(zu.sc(scope) + verb, 60.0)
    _svc_cache["ts"] = 0.0
    why = "" if rc == 0 else (err or out).strip()[:300]
    return 200, {"ok": rc == 0, "action": act, "id": tile.get("id"), "unit": unit,
                 "off_now": act == "mark_off", "note": why or conf.bare(unit)}


# ---- the document sheet ----------------------------------------------------------------------------------
def _screen_path() -> Path:
    return Path((CFG.get("os_power") or {}).get("state_dir") or Path(CFG["_state"]) / "panel") / "screen.json"


def _load_screen() -> None:
    try:
        d = json.loads(_screen_path().read_text(encoding="utf-8"))
        if isinstance(d, dict):
            _screen.update({k: d.get(k) for k in ("doc", "doc_title", "doc_ttl", "rev")})
    except (OSError, ValueError):
        pass


def _save_screen() -> None:
    p = _screen_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_screen), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log(f"screen state not saved: {e!r}")


def resolve_ttl(val, default: float, now: float | None = None) -> float:
    """Seconds from now or an epoch; junk -> the default. A document always carries a timer."""
    now = time.time() if now is None else now
    try:
        v = float(val)
    except (TypeError, ValueError):
        return now + default
    if v <= 0:
        return now + default
    return v if v > 1e9 else now + v


def _doc_window(cmd: str) -> None:
    doc = CFG.get("doc") or {}
    if not doc.get("window") or not DOC_WINDOW.is_file():
        return
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    try:
        subprocess.Popen(["bash", str(DOC_WINDOW), cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True,
                         env=dict(os.environ, CRISSA_PANEL_URL=f"http://127.0.0.1:{CFG['port']}", CRISSA_HOME=str(CFG.get("_state") or "")))
    except Exception as e:  # noqa: BLE001
        log(f"doc window {cmd} failed ({e!r})")


def _clear_expired() -> bool:
    ttl = _screen.get("doc_ttl")
    if _screen.get("doc") and ttl and time.time() >= float(ttl):
        _screen.update(doc=None, doc_title=None, doc_ttl=None)
        _doc_window("close")
        return True
    return False


def post_screen(body: dict) -> dict:
    doc_cfg = CFG.get("doc") or {}
    with _lock:
        for k in body.get("clear") or []:
            if k == "doc":
                _screen.update(doc=None, doc_title=None, doc_ttl=None)
                _doc_window("close")
        if "doc" in body:
            d = body.get("doc")
            if d:
                lines = str(d).replace("\r", "").split("\n")[:int(doc_cfg.get("max_lines") or 400)]
                _screen["doc"] = "\n".join(lines)[:int(doc_cfg.get("max_chars") or 40000)]
                _screen["doc_ttl"] = resolve_ttl(body.get("doc_ttl"), float(doc_cfg.get("ttl_s") or 900))
                _doc_window("open")
            else:
                _screen.update(doc=None, doc_title=None, doc_ttl=None)
                _doc_window("close")
        if "doc_title" in body:
            dt = body.get("doc_title")
            _screen["doc_title"] = str(dt)[:80] if dt else None
        _screen["rev"] = int(_screen.get("rev") or 0) + 1
        _save_screen()
        return {"ok": True, "doc": bool(_screen.get("doc")), "doc_ttl": _screen.get("doc_ttl"), "rev": _screen["rev"]}


# ---- HTTP ------------------------------------------------------------------------------------------------

def demo_url(env: dict | None = None) -> str:
    """The demo helper's address, the shared port table's `demo` rule repeated here (the panel installs on its own,
    standard library only): CRISSA_PORT_DEMO, else 8124 + CRISSA_PORT_OFFSET (lib/crissa_ports, 2026-10-10)."""
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_PORT_DEMO") or "").strip()
    if got.isdigit() and 0 < int(got) < 65536:
        return "http://127.0.0.1:%d" % int(got)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    # CRISSA_PORT_DEMO: the table's number
    DEMO_PORT = 8124
    return "http://127.0.0.1:%d" % (DEMO_PORT + off)


def ear_url(env: dict | None = None) -> str:
    """The ear's address (the Mic key's lock lives there), the shared port table's `stt` rule repeated here like
    demo_url: CRISSA_PORT_STT, else 8190 + CRISSA_PORT_OFFSET (lib/crissa_ports)."""
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_PORT_STT") or "").strip()
    if got.isdigit() and 0 < int(got) < 65536:
        return "http://127.0.0.1:%d" % int(got)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    # CRISSA_PORT_STT: the table's number
    STT_PORT = 8190
    return "http://127.0.0.1:%d" % (STT_PORT + off)


def mic_view(env: dict | None = None) -> dict:
    """THE MIC KEY (the User 2026-10-10: the Control Panel gets the same Mic key the feed's panel has). Her
    microphone is the ear's voice master lock (crissa_ear.house_voice: GET /v1/house/voice); a missing lock is off,
    and an ear that does not answer is said (`ear: false`), never read as on."""
    try:
        with urllib.request.urlopen(ear_url(env) + "/v1/house/voice", timeout=1.5) as r:
            st = json.loads(r.read().decode() or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": True, "on": False, "ear": False, "why": "the ear is not answering (%s)" % type(e).__name__}
    return {"ok": True, "on": str(st.get("voice") or "off") == "on", "ear": True, "since": st.get("on_since")}


# ECHO CANCEL (round 14, the User 2026-10-12: "We travelled these roads in the house version"): the Ears and mouth
# setting for the ear's echo canceller (modules/ear/crissa_ear/aec.py, the house's module-echo-cancel webrtc). The
# choice is the ear's own (ear.json "aec": auto | off); the ear takes it at its restart, which the launcher does.
def aec_view() -> dict:
    home = Path(CFG["_state"])
    try:
        ear = (json.loads((home / "ear.json").read_text(encoding="utf-8")).get("ear") or {})
    except (OSError, ValueError, AttributeError):
        ear = {}
    try:
        st = json.loads((home / "aec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    setting = str(ear.get("aec") or "auto").lower()
    return {"ok": True, "setting": setting if setting in ("auto", "on", "off") else "auto",
            "on": bool((st or {}).get("on")), "why": (st or {}).get("why") or "",
            "mic": (st or {}).get("mic") or "", "speaker": (st or {}).get("speaker") or ""}


def aec_set(setting: str) -> tuple[int, dict]:
    setting = str(setting or "").strip().lower()
    if setting not in ("auto", "on", "off"):
        return 400, {"ok": False, "error": "echo cancel is auto, on or off"}
    home = Path(CFG["_state"])
    p = home / "ear.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    ear = d.setdefault("ear", {}) if isinstance(d.get("ear", {}), dict) else {}
    if setting == "auto":
        ear.pop("aec", None)
    else:
        ear["aec"] = setting
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    job = procs.request(CFG["_state"], "stt", "restart")
    return 200, dict(aec_view(), restart=job)


def wall_page() -> str:
    """Her browser Wall's address, for the page's "Open the Wall in this window" (the User 2026-10-12: "Only if they
    want the wall it should be available"): the player's /wall on this install's port table."""
    try:
        import crissa_ports
        return crissa_ports.url("player") + "/wall"
    except Exception:  # noqa: BLE001
        return ""


def wall_view() -> dict:
    """The desk Wall's Close / Open key (2026-10-10): closed on purpose stays closed (lib/crissa_menu, crissa_watch);
    `page`: her browser Wall's address (wall_page)."""
    try:
        import crissa_menu
        return dict(crissa_menu.wall_state(), ok=True, page=wall_page())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "present": False, "page": wall_page(), "error": f"{type(e).__name__}: {e}"[:160]}


def agent_view() -> dict:
    """Close Agent / Open Agent (the User 2026-10-12, "Close Agent vs OS off"): the same key as the Crissa menus'
    (lib/crissa_menu.agent_state)."""
    try:
        import crissa_menu
        return dict(crissa_menu.agent_state(), ok=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "closed": False, "error": f"{type(e).__name__}: {e}"[:160]}


# A STRANGER IS LOCKED OUT OF ADJUSTING (round 17 follow-ups, the User 2026-10-13: "a stranger should then be locked out
# from any further adjusting. All tools or switches requesting auth gets face match sequence"): while Security is On or
# above, every key of this panel that changes something runs the kit's one face-match sequence first
# (lib/crissa_menu.face_gate -> lib/crissa_home.adjust_gate) and does nothing without his face; what only stops or
# quiets her stays open (crissa_home.OPEN_TO_ANYONE: a lane's Stop, All OS Off, Mic off, Close Agent, Close the Wall).
# Secure's own keys are presence's (it checks his face for a step down itself). The page says the ask first
# (/v1/house `security`). Kill: CRISSA_SECURITY_LOCK_ADJUST=0. Never on the house.
PANEL_STOPS = {"stop": "unit_stop", "os_off": "stop", "house_off": "stop", "mark_off": "unit_stop"}


def panel_gate_name(path: str, body: dict) -> str:
    """The lock's name for a POST of this panel; '' = changes no setting (presence's Secure keys, the screen)."""
    b = body if isinstance(body, dict) else {}
    if path == "/v1/screen":
        return ""                                    # what the screen shows: no setting
    if path == "/v1/glass-prefs":
        try:
            import crissa_menu
            return crissa_menu.glass_gate_name(b)    # the glass's own view is open; how it draws her is locked
        except ImportError:
            return "glass_prefs"
    if path == "/v1/security":
        return ""                                    # the Crissa menu's own action runs the gate (crissa_menu.act)
    if path == "/v1/mic":
        return "mic" if b.get("on") else "mic_off"
    if path == "/v1/wall":
        return "wall_close" if str(b.get("action") or "") == "wall_close" else "wall_open"
    if path == "/v1/agent":
        a = str(b.get("action") or "")
        if a == "agent_close":
            return "agent_close"
        try:
            import crissa_menu
            return crissa_menu._menu_gate_name("agent_open", {}, None)   # closed: the way back (the camera is down too)
        except Exception:  # noqa: BLE001
            return "agent_open"
    if path == "/v1/action":
        a = str(b.get("action") or "")
        if a in ("visit_click", "visit_hold"):
            return ""                                # presence's own keys: it checks a step down itself
        return PANEL_STOPS.get(a, "panel_" + a)
    return "panel" + path.replace("/", "_")


def face_refused(path: str, body: dict) -> dict | None:
    """Before a POST that changes something (above): None = go on; else the answer, nothing changed."""
    name = panel_gate_name(path, body)
    if not name:
        return None
    try:
        import crissa_menu
    except ImportError:
        return None
    got = crissa_menu.face_gate(name)
    if got is None:
        return None
    log(f"POST {path}: refused, his face was not seen ({got.get('why')})")
    return dict(got, note=got.get("error"))


def avatar_view() -> dict:
    """GET /v1/avatar: which library she wears (the User 2026-10-26) - every library this install can wear, the pick,
    the face running (lib/crissa_avatar). Pack only: the house's page is the Composer's and gets 404."""
    import crissa_home
    if crissa_home.is_house():
        return {"ok": False, "error": "no avatar picker on the house: the Composer picks her library there", "code": 404}
    import crissa_avatar
    out = crissa_avatar.status(home=_state_home())
    if crissa_avatar.cards_enabled():
        # GROUPED, WITH A PICTURE (the User 2026-10-31, "agent and flavor"): each row says its agent, its flavor, its title
        for l in out["libraries"] + out.get("phone_looks", []):
            d = Path(l["dir"])
            l.update(crissa_avatar.agent_flavor(d, l["key"], l["label"]))
            l["picture"] = ("/v1/avatar/picture?key=" + l["key"]) if crissa_avatar.picture(Path(l.get("mobile") or d)) else ""
        out["cards"] = True
    try:
        import crissa_skin_days, crissa_skin_add
    except ImportError:
        return out                                              # no skin modules: Choose skin as it was
    if crissa_skin_days.enabled():
        out["week"] = crissa_skin_days.view(_state_home())      # EACH DAY (the User 2026-10-31): absent when off
    if crissa_skin_add.enabled():
        out["can_add"] = True                                   # ADD A LIBRARY: the panel draws the button
    if crissa_skin_add.remove_enabled():
        # REMOVE A LOOK (crissa-6a #12): only a look the buyer added, never the worn one
        worn = crissa_skin_add.worn_keys(_state_home())
        ld = crissa_avatar.libraries_dir(_state_home()).resolve()
        out["can_remove"] = [l["key"] for l in out["libraries"] + out.get("phone_looks", [])
                             if l["key"] not in worn and Path(l["dir"]).resolve().parent == ld]
    return out


def avatar_remove(body: dict) -> dict:
    """POST /v1/avatar/remove {key} (CRISSA_SKIN_REMOVE): a look the buyer added, moved aside, never deleted; never the
    worn one (lib/crissa_skin_add.remove)."""
    import crissa_home
    try:
        import crissa_skin_add
    except ImportError:
        return {"ok": False, "error": "not found", "code": 404}
    if crissa_home.is_house() or not crissa_skin_add.remove_enabled():
        return {"ok": False, "error": "not found", "code": 404}
    out = crissa_skin_add.remove(str((body or {}).get("key") or ""), _state_home())
    drop_unit_caches()
    return dict(out, code=200 if out.get("ok") else 409)


def avatar_set(body: dict) -> dict:
    """POST /v1/avatar {key}: save the pick (<state home>/avatar.json), then ask the launcher to restart the player lane
    the way the Restart key does (lib/crissa_procs.request), so she wears it in a few seconds; the Wall draws the
    player's frames and follows. The pick is saved even when the launcher is not up: the next start wears it."""
    import crissa_home
    if crissa_home.is_house():
        return {"ok": False, "error": "no avatar picker on the house", "code": 404}
    if (body or {}).get("phone_follows_desk"):
        return phone_look_wear("", by="the Control Panel")
    return avatar_wear(str((body or {}).get("key") or ""), by="the Control Panel")


def phone_look_wear(key: str, by: str) -> dict:
    """A PHONE-ONLY look picked (CRISSA_SKIN_PHONE_ONLY; Denim Crissa, the User 2026-10-31): the phone pick only - the desk
    pick, the player and the Wall untouched; the phone follows at its next quiet moment. '' = follow the desk look."""
    import crissa_avatar
    home = _state_home()
    res = crissa_avatar.choose_phone(key, home=home, by=by)
    if not res.get("ok"):
        return dict(res, code=409)
    res["phone_follow"] = phone_follow(home)
    owed = (res["phone_follow"] or {}).get("phone") == "owed"
    res["note"] = ("Her phone wears %s" % res.get("label", key) if key else "Her phone follows her desk look again") + (
        " when its conversation ends" if owed else "")
    drop_unit_caches()
    return res


def avatar_wear(key: str, by: str) -> dict:
    """The one pick path: a person's pick (POST /v1/avatar) and the week's switch (lib/crissa_skin_days) both land here;
    the desk pick itself is crissa_avatar.wear, which the Wall MENU's Agent Look shares (2026-10-31)."""
    import crissa_avatar
    home = _state_home()
    if key and key in {l["key"] for l in crissa_avatar.phone_libraries(home)}:
        return phone_look_wear(key, by)
    res = crissa_avatar.wear(key, by, home, procs)
    if not res.get("ok"):
        return dict(res, code=409)
    drop_unit_caches()
    return res


def phone_follow(home: Path, tick: bool = False) -> dict:
    """CRISSA_AGENT_PHONE: the phone lane follows the worn library's Mobile packs (crissa_avatar.phone_follow)."""
    import crissa_avatar
    return crissa_avatar.phone_follow(home, procs, tick)


def _state_home() -> Path:
    import crissa_home
    return Path(CFG.get("_state") or crissa_home.HOME)


def avatar_picture(key: str) -> tuple:
    """GET /v1/avatar/picture?key=K: a listed library's own small picture (crissa_avatar.picture), nothing else."""
    import crissa_avatar, crissa_home
    if crissa_home.is_house() or not crissa_avatar.cards_enabled():
        return 404, b"", "text/plain"
    libs = {l["key"]: l for l in crissa_avatar.libraries(home=_state_home()) + crissa_avatar.phone_libraries(_state_home())}
    lib = libs.get(str(key or "").strip().lower())
    p = crissa_avatar.picture(Path(lib.get("mobile") or lib["dir"])) if lib else None
    if p is None:
        return 404, b"", "text/plain"
    return 200, p.read_bytes(), "image/png" if p.suffix.lower() == ".png" else "image/jpeg"


def avatar_week_set(body: dict) -> dict:
    """POST /v1/avatar/week {days: {mon..sun: key | ""}}: the week Choose skin keeps (CRISSA_SKIN_DAYS)."""
    import crissa_avatar, crissa_home, crissa_skin_days
    if crissa_home.is_house() or not crissa_skin_days.enabled():
        return {"ok": False, "error": "not found", "code": 404}
    rule = crissa_avatar.offer_rule()                      # Crissa looks only in 1.0.5: a week names offered skins
    libs = [l for l in crissa_avatar.libraries(home=_state_home()) if crissa_avatar.offered(l, rule)]
    out = crissa_skin_days.save_week((body or {}).get("days"), libs, _state_home())
    return out if out.get("ok") else dict(out, code=400)


def skin_days_loop(stop: threading.Event | None = None) -> None:
    """EACH DAY: the ticker. One look at today per tick; a switch waits for her to be quiet (lib/crissa_skin_days).
    The same tick pays an owed phone switch once no phone session is open (CRISSA_AGENT_PHONE)."""
    import crissa_skin_days
    stop = stop or threading.Event()
    tick_s = float(crissa_skin_days.config()["tick_s"])
    while not stop.wait(tick_s):
        if crissa_skin_days.enabled():
            try:
                got = crissa_skin_days.tick(_state_home(), wear=lambda k: avatar_wear(k, by=crissa_skin_days.BY))
                if got.get("ok") is not None or got.get("what"):
                    log("each day: %s" % got.get("what"))
            except Exception as e:  # noqa: BLE001 - the week is never a reason the panel stops
                log("each day: %r" % e)
        got = phone_follow(_state_home(), tick=True)
        if got.get("phone") in ("restarting", "no lane", "error"):
            log("phone follows: %s" % (got.get("packs") or got.get("why") or got.get("phone")))


def skin_add_upload(handler) -> dict:
    """POST /v1/avatar/add (CRISSA_SKIN_ADD): a pack zip the buyer picked, streamed (not JSON), checked, converted and
    installed whole; listed in Choose skin at once, never worn (lib/crissa_skin_add)."""
    import crissa_home, crissa_import, crissa_skin_add
    if crissa_home.is_house() or not crissa_skin_add.enabled():
        return {"ok": False, "error": "not found", "code": 404}
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        n = -1
    if n <= 0:
        return {"ok": False, "error": "choose the pack you downloaded (.zip) first", "code": 400}
    if n > crissa_import.MAX_BYTES:
        # a WHOLE AGENT (CRISSA_SKIN_AGENT_PACK, crissa-6a #7) is 1.5-2.1 GB: taken when the zip and its unpacked
        # folder fit this disk's room (config add.agent_keep_free_gb); the zip itself says whether it is one
        if not crissa_skin_add.agent_enabled():
            return {"ok": False, "error": "that file is larger than a pack may be", "code": 413}
        if 2 * n > crissa_skin_add.room(_state_home()):
            return {"ok": False, "error": "this computer does not have the room for that pack", "code": 413}
    import tempfile
    d = crissa_skin_add.staging_root(_state_home())
    d.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(prefix=".upload-", suffix=".zip", dir=str(d))
    try:
        with os.fdopen(fd, "wb") as f:
            left = n
            while left > 0:
                chunk = handler.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            return {"ok": False, "error": "the upload stopped part way; try again", "code": 400}
        out = crissa_skin_add.add_zip(Path(part), _state_home())
        log("add a library: %s (%s), %d clips" % (out["name"], out["key"], out["clips"]))
        return out
    except crissa_skin_add.Refused as e:
        return {"ok": False, "error": str(e), "code": 409}
    finally:
        Path(part).unlink(missing_ok=True)


def security_set(body: dict) -> dict:
    """The Security toggle from this panel (POST /v1/security {mode, password?}): the Crissa menu's own action."""
    try:
        import crissa_menu
    except ImportError:
        return {"ok": False, "error": "this install has no Crissa menu module"}
    b = dict(body or {})
    b.setdefault("by", "the Control Panel")
    out = crissa_menu.act("security", b)
    b.pop("password", None)
    return dict(out, note=out.get("note") or out.get("error") or "")


def security_view() -> dict | None:
    try:
        import crissa_home
        if crissa_home.is_house():
            return None
        import crissa_menu
        return crissa_menu.security_view(presence=crissa_menu._presence())
    except Exception:  # noqa: BLE001
        return None


def agent_set(action: str) -> dict:
    try:
        import crissa_menu
    except ImportError:
        return {"ok": False, "error": "this install has no Crissa menu module"}
    if action == "agent_close":
        return crissa_menu.agent_close(by="the Control Panel")
    if action == "agent_open":
        return crissa_menu.agent_open(by="the Control Panel")
    return {"ok": False, "error": "unknown action %r" % action}


def wall_set(action: str) -> dict:
    try:
        import crissa_menu
    except ImportError:
        return {"ok": False, "error": "this install has no Crissa menu module"}
    if action == "wall_close":
        return crissa_menu.wall_close(by="the Control Panel")
    if action == "wall_open":
        return crissa_menu.wall_open()
    return {"ok": False, "error": "unknown action %r" % action}


def mic_set(on: bool, env: dict | None = None) -> tuple[int, dict]:
    """Press the Mic key: the ear's own route (POST /panel/voice {on, on_by: "user"}), as the feed's panel does."""
    req = urllib.request.Request(ear_url(env) + "/panel/voice", method="POST",
                                 data=json.dumps({"on": bool(on), "on_by": "user"}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=4.0) as r:
            st = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return 502, {"ok": False, "error": "the ear refused the Mic key (HTTP %d)" % e.code}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 503, {"ok": False, "error": "the ear is not answering (%s); start Crissa with her ear" % type(e).__name__}
    got = bool(st.get("voice_on"))
    return (200 if got == bool(on) else 502), {"ok": got == bool(on), "on": got, "ear": True}


def page_bytes(env: dict | None = None) -> bytes:
    """The panel page with this install's demo helper written in (`__VO_DEMO__`)."""
    return PAGE.read_bytes().replace(b"__VO_DEMO__", demo_url(env).encode())

class Handler(BaseHTTPRequestHandler):
    server_version = "CrissaPanel/1"

    def log_message(self, fmt, *args):  # quiet: the journal is for what matters
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin") or ""
        if not origin:
            return True                   # curl, the agent tools, the panel's own fetch on old browsers
        host = self.headers.get("Host") or ""
        o = urllib.parse.urlparse(origin)
        # round 46: Origin == Host is same-origin only when that Host is this machine (DNS rebinding: both the
        # attacker's name); the kill (CRISSA_LOOPBACK_GUARD=0) puts back the old rule
        same = o.netloc == host and (not crissa_guard.guard_on() or crissa_guard.loopback_hostport(host, guard_names()))
        return same or o.hostname in ("127.0.0.1", "localhost") and str(o.port) == str(CFG["port"])

    def _guard(self) -> bool:
        """First in every do_*: True = go on; False = refused 403 (round 46, above)."""
        return crissa_guard.check(self, names=guard_names())

    def do_GET(self) -> None:
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        try:
            if path in ("/", "/panel"):
                if not PAGE.is_file():
                    return self._json(500, {"ok": False, "error": "the panel page is missing"})
                return self._send(200, page_bytes(), "text/html; charset=utf-8")
            if path == "/health":
                return self._json(200, {"ok": True, "port": CFG["port"], "self_unit": CFG.get("self_unit")})
            if path == "/v1/caps":
                return self._json(200, {"ok": True, "title": CFG.get("title"), "gpu": POWER.has_gpu(),
                                        "doc": bool((CFG.get("doc") or {}).get("window")), "os_power": True,
                                        "switch_name": POWER.switch_name, "on_name": POWER.on_name,
                                        "sources": CFG.get("_sources"), "scopes": list(RULES.scopes),
                                        "rules": RULES.describe(),
                                        "house_compat": True,
                                        "log_lanes": list(LOG_LANES), "lane_units": lane_units(),
                                        "log_mask": mask_on(), "watched_by": _kit_switch("CRISSA_PANEL_WATCHED_BY"),
                                        "fold_at": FOLD_AT})
            if path == "/v1/services":
                return self._json(200, services_snapshot())
            if path == "/v1/house":
                return self._json(200, house_payload())
            if path == "/v1/glass-prefs":
                return self._json(200, _glass.record())
            if path == "/v1/logs":
                return self._json(200, mask_lines(tail_lane((q.get("lane") or [""])[0])))
            if path == "/v1/watchers":
                return self._json(200, watchers_view())
            if path == "/v1/security/summary":
                return self._json(200, security_summary())
            if path == "/v1/voices":
                return self._json(200, dict(voice_panel.view(), ok=True))
            if path == "/v1/mic":
                return self._json(200, mic_view())
            if path == "/v1/aec":
                return self._json(200, aec_view())
            if path == "/v1/wall":
                return self._json(200, wall_view())
            if path == "/v1/agent":
                return self._json(200, agent_view())
            if path == "/v1/voices/log":
                out = voice_panel.log(str((q.get("id") or [""])[0]), int((q.get("lines") or ["120"])[0]))
                return self._json(200 if "text" in out else 404, dict(out, ok="text" in out))
            if path == "/v1/addons":
                av = addons_view(registry(force=bool(q.get("refresh"))))
                return self._json(200, dict(av, ok=True, paths=addons.settings(CFG)["paths"]))
            if path == "/v1/registry":
                reg = live_registry(registry(force=bool(q.get("refresh"))))
                notes = zu.load_notes(RULES)
                return self._json(200, {"ok": True, "n": len(reg.get("units") or []), "units": reg.get("units") or [],
                                        "changes": zu.recent_changes(reg), "systemd": reg.get("systemd") or "ok",
                                        "rules": reg.get("rules") or RULES.describe(), "updated": reg.get("updated"),
                                        "states_at": reg.get("states_at"),
                                        "notes_without_units": zu.notes_without_units(reg, notes)})
            if path == "/v1/os_power":
                try:
                    if plan_ttl_s() > 0:
                        retire_stale_off(cached_plan())         # the same cached plan the view reads next
                        return self._json(200, POWER.panel_view(run, plan=cached_plan()))
                    retire_stale_off(POWER.plan(run))
                    return self._json(200, POWER.panel_view())
                except Exception as e:  # noqa: BLE001 - never 500 the panel
                    return self._json(200, {"ok": False, "error": f"os_power view: {e}"[:160]})
            if path == "/v1/journal":
                return self._json(200, mask_lines(journal((q.get("unit") or ["all"])[0], (q.get("lines") or [""])[0],
                                                          (q.get("before") or [""])[0], (q.get("file") or [""])[0])))
            if path == "/v1/log-file":
                code, body = whole_log((q.get("unit") or [""])[0], (q.get("file") or [""])[0])
                if code == 200 and mask_on():      # the whole log is masked line by line, as every page of it is
                    body = "\n".join(mask_text(ln) for ln in body.decode("utf-8", "replace").splitlines()).encode("utf-8") + b"\n"
                return self._send(code, body, "text/plain; charset=utf-8")
            if path == "/v1/restart-report":
                return self._json(200, restart_report((q.get("job") or [""])[0]))
            if path == "/v1/avatar":
                out = avatar_view()
                return self._json(int(out.pop("code", 200)), out)
            if path == "/v1/avatar/picture":
                code, body, ctype = avatar_picture((q.get("key") or [""])[0])
                return self._send(code, body, ctype)
            if path == "/v1/script" and (q.get("unit") or [""])[0].startswith("proc:"):
                return self._json(200, mask_lines(proc_script((q.get("unit") or [""])[0][5:])))
            if path == "/v1/script":
                got = resolve_unit((q.get("unit") or [""])[0])
                if not got:
                    return self._json(400, {"ok": False, "error": "not one of this install's units"})
                return self._json(200, mask_lines(script_view(*got)))
            if path == "/doc":
                if not DOC_PAGE.is_file():
                    return self._json(500, {"ok": False, "error": "the document page is missing"})
                return self._send(200, DOC_PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/v1/doc":
                with _lock:
                    _clear_expired()
                    return self._json(200, {"ok": True, "doc": _screen.get("doc"), "doc_title": _screen.get("doc_title"),
                                            "doc_font": None, "doc_mug": None, "doc_gag": False, "rev": _screen.get("rev")})
        except Exception as e:  # noqa: BLE001 - one bad read must not take the panel down
            log(f"GET {path}: {e!r}")
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        if not self._same_origin():
            return self._json(403, {"ok": False, "error": "a page of another origin may not press the panel's keys"})
        if path == "/v1/avatar/add":
            # a file, not JSON: streamed before the panel's JSON body cap (CRISSA_SKIN_ADD); his face first while Security is On
            refused = face_refused(path, {})
            if refused is not None:
                return self._json(200, refused)
            out = skin_add_upload(self)
            return self._json(int(out.pop("code", 200)), out)
        n = crissa_guard.body_length(self.headers, PANEL_BODY_MAX)   # round 46: -1 was rfile.read(-1), to the close
        if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
            n = min(int(self.headers.get("Content-Length") or 0), PANEL_BODY_MAX)         # the kill: the old read
        if n is None:
            return self._json(413 if str(self.headers.get("Content-Length") or "").strip().isdigit() else 400,
                              {"ok": False, "error": "bad Content-Length"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, OSError) as e:
            return self._json(400, {"ok": False, "error": f"bad body: {e}"[:160]})
        try:
            refused = face_refused(path, body)       # while Security is On: his face first (above)
            if refused is not None:
                return self._json(200, refused)
            if path == "/v1/security":
                return self._json(200, security_set(body))
            if path == "/v1/avatar":
                out = avatar_set(body)
                return self._json(int(out.pop("code", 200)), out)
            if path == "/v1/avatar/week":
                out = avatar_week_set(body)
                return self._json(int(out.pop("code", 200)), out)
            if path == "/v1/avatar/remove":
                out = avatar_remove(body)
                return self._json(int(out.pop("code", 200)), out)
            if path == "/v1/voices/action":
                # the Voices section's two keys: Restart (Audio8) and Run now (keep-warm); nothing else is accepted
                out = voice_panel.action(str(body.get("id") or ""), str(body.get("act") or ""))
                drop_unit_caches()
                return self._json(200 if out.get("ok") else 409, out)
            if path == "/v1/action":
                code, out = do_action(body)
                drop_unit_caches()
                return self._json(code, out)
            if path == "/v1/mic":
                code, out = mic_set(bool(body.get("on")))
                return self._json(code, out)
            if path == "/v1/aec":
                code, out = aec_set(str(body.get("setting") or ""))
                return self._json(code, out)
            if path == "/v1/wall":
                return self._json(200, wall_set(str(body.get("action") or "")))
            if path == "/v1/agent":
                return self._json(200, agent_set(str(body.get("action") or "")))
            if path == "/v1/glass-prefs":
                # partial merge; reply is the whole record with its new rev (Connect Walls)
                return self._json(200, _glass.apply(body))
            if path == "/v1/screen":
                return self._json(200, post_screen(body))
        except Exception as e:  # noqa: BLE001
            log(f"POST {path}: {e!r}")
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
        return self._json(404, {"ok": False, "error": "not found"})


UNIT_TEMPLATE = """[Unit]
Description=Crissa Control Panel (:{port})
After=default.target

[Service]
ExecStart={python} {script}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=None, help="port (default: settings, else 8097)")
    ap.add_argument("--host", default=None, help="address (default 127.0.0.1)")
    ap.add_argument("--unit", action="store_true", help="print a systemd user unit for this install and exit")
    a = ap.parse_args(argv)
    setup()
    if a.port:
        CFG["port"] = a.port
    if a.unit:
        sys.stdout.write(UNIT_TEMPLATE.format(port=CFG["port"], python=sys.executable,
                                              script=Path(__file__).resolve()))
        return 0
    host = a.host or CFG.get("host") or "127.0.0.1"
    srv = ThreadingHTTPServer((host, int(CFG["port"])), Handler)
    srv.daemon_threads = True
    try:
        import crissa_skin_add, crissa_skin_days
    except ImportError as e:      # a panel without the skin modules runs without them; it never fails to start
        log("choose skin: %s" % e)
    else:
        try:
            import crissa_skin_phone
            phone_on = crissa_skin_phone.enabled()
        except ImportError:
            phone_on = False
        if crissa_skin_days.enabled() or phone_on:
            threading.Thread(target=skin_days_loop, name="skin-days", daemon=True).start()
        if crissa_skin_add.enabled() or crissa_skin_add.store_enabled():
            try:
                crissa_skin_add.sweep(_state_home())
            except Exception as e:  # noqa: BLE001
                log("add a library: sweep %r" % e)
    log(f"Control Panel on http://{host}:{CFG['port']}/ (settings: {', '.join(CFG.get('_sources') or [])}; "
        f"rules: {json.dumps(RULES.describe())})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
