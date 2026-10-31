"""Who restarts Crissa's own lanes (2026-10-06).

krew's keeper no longer restarts Crissa's ear: it only reads it through the player. So Crissa restarts its own. The
launcher (scripts/crissa.py) runs every lane in one process group and, through this watch, brings back a lane that
went down, says why, and shows it on the Control Panel. Nothing Crissa starts is left with no one to restart it:
  - a lane that EXITS is started again (its exit code and the end of its log are the reason) - except a closable lane
    (the Wall) the person closed on purpose, which stays closed (CLOSED below, 2026-10-10);
  - a lane that is up but whose /health stops answering `health_fails` times in a row is stopped and started again
    (the health error is the reason). Before a lane first answers, `boot_grace_s` gives it time to load;
  - the Control Panel's Restart on a lane drops a request (<crissa home>/lanes-restart/<lane>) that the next tick
    serves (the reason says it was asked from the panel);
  - the player itself is the launcher's own: when it stops the launcher stops everything (a unit's Restart= or the
    person starts it again), as before.
Restarts back off along `backoff_s` (the last step repeats). `give_up_after` 0 = never give up; set it and a lane
that hits it is marked given up on the panel, which can still restart it by hand.

Settings: config/crissa-watch.json under <crissa home>/watch.json. Every event goes to <crissa home>/logs/lanes.log
whole; <crissa home>/lanes.json is the panel's view (each lane's state, restarts and last reason). No timer of its
own: the launcher's loop calls tick().
"""
from __future__ import annotations

import copy
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from crissa_home import HOME
except ImportError:  # imported by path from a test or a tool
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

SHIPPED = Path(__file__).resolve().parent.parent / "config" / "crissa-watch.json"
FALLBACK = {"restart": ["asr", "stt", "ear", "aligner", "tts", "turbo", "presence", "feed", "panel", "wall"],
            "health": {"asr": "/health", "stt": "/health", "aligner": "/health", "tts": "/health", "turbo": "/health",
                       "presence": "/health", "panel": "/health"},
            "health_every_s": 10, "health_fails": 3, "health_timeout_s": 2,
            "boot_grace_s": {"default": 120, "aligner": 300, "turbo": 600},
            "backoff_s": [2, 5, 15, 30, 60], "steady_s": 120, "give_up_after": 0}
STATUS = "lanes.json"
REQUESTS = "lanes-restart"

# A WALL THE PERSON CLOSES STAYS CLOSED (the User 2026-10-10, .101: `systemctl --user stop crissa-wall.service` and
# the watch started the Wall again within a second, full screen, and only stopping every unit gave the desktop back).
# A closable lane (`closable` in the settings: the Wall) is restarted only when it CRASHED. Closed on purpose - Ctrl+Q,
# Close the Wall in the Crissa menu or the Control Panel, a clean exit, a SIGTERM, or its unit stopped (systemd's
# Result=success) - it is recorded in <crissa home>/wall-closed.json and holds, across launcher starts too, until the
# person opens the Wall again: Open the Wall in the menu or the panel, or the launcher's --open-wall.
CLOSED = "wall-closed.json"
CLOSABLE_DEFAULT = ("wall",)
# CLOSE AGENT (the User 2026-10-12, .101 09:2x: "We should turn that side into Close Agent vs OS off ... make them
# match wherever possible"): Crissa the agent goes - she stops listening and speaking and her window closes - while
# the desk Wall stays as the desktop, the OS. Recorded like a closed Wall (the pseudo-lane "agent" in CLOSED), it holds
# every lane she listens or speaks with down, across launcher starts too, until Open Agent (the menus, the Control
# Panel, her dock icon). All OS Off is the other one: everything of Crissa off (the menus' All OS Off, the units' stop).
AGENT = "agent"
# HER EYES GO WITH HER (round 15, .101 13:37: presence held ~2 cores while she was closed): the camera's grabber, presence
# and the oracle stop with Close Agent and come back with Open Agent; the film, the feed, the player and the Wall stay.
AGENT_EYES = ("camera", "presence", "oracle")
AGENT_LANES = ("asr", "stt", "ear", "bridge", "aligner", "tts", "multilingual", "turbo", "pocket", "audio8", "warm",
               "enroll") + AGENT_EYES
AGENT_OPENED = "agent_opened_at"
OPENED_WHY = "opened again (Open Agent)"            # in CLOSED: when Open Agent last ran (the panel clears a stale Off by it)
AGENT_WHY = "Close Agent (%s): Open Agent brings her back"
DELIBERATE_CODES = (0, -15)                 # a clean exit, or a SIGTERM (someone stopped it); anything else crashed

# A STOP OF EVERYTHING STAYS STOPPED (the User 2026-10-10, .101 round 5 HH: after `systemctl --user stop 'crissa-*'`
# this watch started crissa-tts again; it came back 20 s later with no launcher). In service mode the group says when
# the launcher's own unit is being stopped (lib/crissa_service.going_down): then no lane is started again, whatever
# the reason. A unit stopped on purpose (systemd's Result=success) is also given CLEAN_STOP_GRACE_S before it is
# started again, so the stop of the launcher that follows a stop of its children is seen first; a lone
# `systemctl --user stop crissa-<lane>` is still brought back after it. A crash is unchanged (systemd's Restart= and
# this watch, at once).
CLEAN_STOP_GRACE_S = 10.0
GOING_DOWN_EVERY_S = 1.0
STOPPING_WHY = "Crissa is stopping (its service is being stopped): not started again"
# A VOICE NO ROLE USES IS HELD (round 17, 2026-10-13, lib/crissa_voice_demand): the launcher's held lanes
# (group.standby, "installed, not running") are never started again here; a line that needs one starts it through the
# request queue, and the watch then watches it afresh from that start (its boot grace counts from there). A Restart
# from the Control Panel takes a lane out of the held set. Kill CRISSA_VOICE_ENGINES_ON_DEMAND=0 (nothing is held).
STANDBY_WHY = "installed, not running: no role uses this voice now; a line that needs it starts it"

# A PORT ANOTHER COPY SERVES IS NOT STARTED TWICE (round 17, 2026-10-13, .101 08:28: after Open Agent the player's own
# aligner held :8195, crissa-aligner.service failed "Address already in use" every 2 s - 3,453 restarts by 08:28 - and
# this watch read the lane up, its /health answered by the other copy). A lane that is down while its port is taken and
# answers the lane's /health is held as "served", said once in lanes.log and on the panel, and looked at again every
# health_every_s; when that copy stops answering the lane's own is started. Open Agent asks the same before it starts a
# lane it held. Kill CRISSA_LANE_PORT_SERVED=0 (round 16: started again into the taken port, on the back-off).
SERVED_SWITCH = "CRISSA_LANE_PORT_SERVED"
SERVED_WHY = ("another copy already answers on its port :%s (%s): not started a second time; this lane's own starts "
              "when that one stops")
SERVED_GONE_WHY = "the copy that served its port :%s stopped answering"


def served_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(SERVED_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def port_taken(port: int, timeout: float = 0.3) -> bool:
    """Something listens on 127.0.0.1:port (a connect is answered)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except (OSError, ValueError):
        return False


def closed_path(home: Path | None = None) -> Path:
    return Path(home or HOME) / CLOSED


def closed(home: Path | None = None, lane: str = "wall") -> dict:
    """The person's close of `lane` ({"closed": true, "by", "at"}), or {} when it is open."""
    d = _read(closed_path(home))
    lanes = d.get("lanes") if isinstance(d.get("lanes"), dict) else {}
    rec = lanes.get(lane) or {}
    return rec if rec.get("closed") else {}


def agent_opened_at(home: Path | None = None) -> str:
    """When Open Agent last ran here ("%Y-%m-%dT%H:%M:%S", local), or ""."""
    return str(_read(closed_path(home)).get(AGENT_OPENED) or "")


def agent_closed(home: Path | None = None) -> dict:
    """The person's Close Agent ({"closed": true, "by", "at"}), or {} while the agent is open."""
    return closed(home, AGENT)


def set_closed(lane: str, on: bool, by: str = "", home: Path | None = None) -> dict:
    """Record (on) or clear (off) the person's close of `lane`. Atomic; never raises."""
    p = closed_path(home)
    d = _read(p)
    lanes = d.get("lanes") if isinstance(d.get("lanes"), dict) else {}
    if on:
        lanes[lane] = {"closed": True, "by": str(by or "")[:40], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    else:
        lanes.pop(lane, None)
        if lane == AGENT:
            d[AGENT_OPENED] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        out = {"schema": "crissa-closed/1", "lanes": lanes}
        if d.get(AGENT_OPENED):
            out[AGENT_OPENED] = d[AGENT_OPENED]
        tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass
    return lanes.get(lane) or {}


# CLOSE AGENT IS A PAUSE OF ONE SESSION (round 17, 2026-10-13; .101 06:21: "Close Agent survives All OS Off and a
# start", 7 units at the next start instead of 18). A session runs from a launcher's start to its end. At the next
# start the close is ended - she starts open - when the run before ended on purpose (All OS Off, a stop of everything,
# Ctrl-C, a logout: lib/crissa_service's record of that run, exit 0, or the service manager's own stop on purpose), or
# the computer started since the close (power lost, no record). A run that crashed or was killed in this boot, and that
# its service starts again, is the same session: the close holds. No setting of the person's keeps her closed across a
# start (none exists: the Mic switch and Close the Wall are their own, and they stand). Kill:
# CRISSA_AGENT_CLOSE_SESSION=0 (round 16: the close holds across every start until Open Agent).
SESSION_SWITCH = "CRISSA_AGENT_CLOSE_SESSION"
DELIBERATE_STOP = ("TERM", "INT", "HUP", "0", "15", "2", "1")     # lib/crissa_service.DELIBERATE


def boot_time() -> float | None:
    """When this computer started (epoch s), or None when it cannot be read."""
    try:
        for ln in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if ln.startswith("btime "):
                return float(ln.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        import re
        import subprocess
        import sys
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=2).stdout
            m = re.search(r"sec\s*=\s*(\d+)", out or "")
            return float(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _stamp_epoch(at: str) -> float | None:
    """A close record's "at" ("%Y-%m-%dT%H:%M:%S%z", or without the zone) as epoch seconds, or None."""
    import datetime
    t = str(at or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.datetime.strptime(t if "%z" in fmt else t[:19], fmt)
            return d.timestamp()
        except ValueError:
            continue
    return None


def run_before_on_purpose(prev: dict | None) -> str:
    """How the launcher's run before this one ended, when it ended on purpose; "" for a crash, a kill, no record."""
    p = prev if isinstance(prev, dict) else {}
    if p.get("exit_code") == 0:
        return str(p.get("why") or "stopped")
    stop = p.get("stop") if isinstance(p.get("stop"), dict) else None
    if p.get("exit_code") is None and stop and (stop.get("result") == "success" or (
            stop.get("code") in ("killed", "exited") and str(stop.get("status") or "") in DELIBERATE_STOP)):
        return "stopped by its service manager (%s)" % (stop.get("status") or stop.get("result") or "?")
    return ""


def agent_at_start(home: Path | None = None, rec: dict | None = None, *, env: dict | None = None,
                   booted: float | None = None) -> str:
    """At a launcher's start (`rec`: lib/crissa_service.launcher_started's record of this run, carrying the run before
    in "previous"): end a Close Agent left from an earlier session. Returns why it ended it ("" = nothing ended)."""
    e = os.environ if env is None else env
    if str(e.get(SESSION_SWITCH, "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    close = agent_closed(home)
    if not close:
        return ""
    r = rec if isinstance(rec, dict) else {}
    if r.get("shadow"):
        return ""                         # another launcher runs: its session, and its pause, stand
    prev = r.get("previous")
    why = ""
    if not prev:
        why = "no run of Crissa before this one"
    else:
        on_purpose = run_before_on_purpose(prev)
        if on_purpose:
            why = "the run before ended on purpose (%s)" % on_purpose
    if not why:
        bt = boot_time() if booted is None else booted
        at = _stamp_epoch(close.get("at"))
        if bt and at and at < bt:
            why = "the computer started since the close"
    if not why:
        return ""
    set_closed(AGENT, False, by="a new session", home=home)
    return why


def deliberate_end(proc) -> str:
    """'' when the lane's end looks like a crash; else how it was closed on purpose."""
    if getattr(proc, "stopped_clean", False):
        return "its unit was stopped (systemctl stop)"
    rc = getattr(proc, "returncode", None)
    if rc == 0:
        return "it exited cleanly (closed)"
    if rc == -15:
        return "it was stopped (SIGTERM)"
    return ""


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load(home: Path | None = None, shipped: Path | None = None) -> dict:
    out = copy.deepcopy(FALLBACK)
    for d in (_read(shipped or SHIPPED), _read(Path(home or HOME) / "watch.json")):
        out.update({k: v for k, v in d.items() if k in FALLBACK})
    return out


def health(url: str, timeout: float) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return (200 <= r.status < 300), "answered %d" % r.status
    except urllib.error.HTTPError as e:
        return False, "answered %d" % e.code
    except (OSError, ValueError) as e:
        return False, "%s: %s" % (type(e).__name__, e)


def request_restart(lane: str, home: Path | None = None) -> Path:
    """The Control Panel's Restart: a request file the launcher's next tick serves."""
    d = Path(home or HOME) / REQUESTS
    d.mkdir(parents=True, exist_ok=True)
    p = d / lane
    p.write_text(str(time.time()), encoding="utf-8")
    return p


def read_status(home: Path | None = None) -> dict:
    """lanes.json plus whether the launcher that wrote it is still running."""
    d = _read(Path(home or HOME) / STATUS)
    pid = d.get("launcher_pid")
    alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    d["launcher_alive"] = alive
    return d


class Watch:
    """kids: {lane: {"argv", "env", "port"}}; group: spawn(name, argv, env, port=None), alive(name), stop_one(name), procs."""

    def __init__(self, kids: dict, group, *, cfg: dict | None = None, home: Path | None = None, log=print,
                 now=time.time, check=health, tail=lambda name: "", taken=port_taken):
        self.cfg = cfg if cfg is not None else load(home)
        self.taken = taken
        self.home = Path(home or HOME)
        self.kids = {k: v for k, v in kids.items() if k in (self.cfg.get("restart") or [])}
        self.closable = set(self.cfg.get("closable") or CLOSABLE_DEFAULT)
        self.group, self.log, self.now, self.check, self.tail = group, log, now, check, tail
        t = self.now()
        self.st = {k: {"state": "up", "started_at": t, "answered": False, "fails": 0, "checked_at": 0.0,
                       "restarts": 0, "streak": 0, "next_at": 0.0, "down_since": None, "last_why": "",
                       "last_at": None, "last_health": ""} for k in self.kids}
        self._wrote = 0.0
        self._down_at, self._down = -1e9, False
        reqs = self.home / REQUESTS
        if reqs.is_dir():
            for p in reqs.iterdir():          # a request left from an earlier run is not this run's
                p.unlink(missing_ok=True)
        self.write()

    # ---- the log and the panel's view -----------------------------------------------------------------------
    def event(self, lane: str, what: str) -> None:
        line = "%s %s: %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), lane, what)
        self.log("%s %s" % (lane, what))
        try:
            (self.home / "logs").mkdir(parents=True, exist_ok=True)
            with open(self.home / "logs" / "lanes.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def write(self) -> None:
        out = {"launcher_pid": os.getpid(), "updated_at": self.now(), "watch": "lib/crissa_watch.py",
               "lanes": {}}
        for k, s in self.st.items():
            p = (self.group.procs.get(k) if hasattr(self.group, "procs") else None)
            out["lanes"][k] = {"state": s["state"], "port": self.kids[k].get("port"),
                               "pid": getattr(p, "pid", None), "restarts": s["restarts"],
                               "last_why": s["last_why"], "last_at": s["last_at"], "last_health": s["last_health"]}
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.home / (STATUS + ".tmp")
            tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
            tmp.replace(self.home / STATUS)
        except OSError:
            pass
        self._wrote = self.now()

    def stopped(self, why: str) -> None:
        """The launcher stopped everything on purpose: the panel says so instead of "not running"."""
        for s in self.st.values():
            s["state"] = "stopped"
        self.write()
        try:
            d = _read(self.home / STATUS)
            d.update(stopped_at=self.now(), stopped_why=why)
            (self.home / STATUS).write_text(json.dumps(d, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---- one pass -------------------------------------------------------------------------------------------
    def _grace(self, lane: str) -> float:
        g = self.cfg.get("boot_grace_s") or {}
        return float(g.get(lane, g.get("default", 120)) if isinstance(g, dict) else g)

    def going_down(self, fresh: bool = False) -> bool:
        """The group's word (service mode) that the launcher's own unit is being stopped; asked only when a lane would
        be started again, at most once a second while lanes wait, and afresh before a restart. A group with no such word
        (process mode) is never going down."""
        ask = getattr(self.group, "going_down", None)
        if ask is None:
            return False
        t = self.now()
        if fresh or t - self._down_at >= GOING_DOWN_EVERY_S:
            try:
                self._down = bool(ask(fresh=True))         # the group's own once-a-second answer is for its starts
            except TypeError:
                self._down = bool(ask())
            except Exception:  # noqa: BLE001  a question that fails is not a stop
                self._down = False
            self._down_at = t
        return self._down

    def _hold(self, lane: str) -> None:
        s = self.st[lane]
        if s["state"] != "stopping":
            s.update(state="stopping", last_why=STOPPING_WHY, last_at=self.now())
            self.event(lane, STOPPING_WHY)
            self.write()

    def restart(self, lane: str, why: str) -> None:
        s, kid = self.st[lane], self.kids[lane]
        if self.going_down(fresh=True):
            self._hold(lane)
            return
        if self.group.alive(lane):
            self.group.stop_one(lane)
        self.group.spawn(lane, kid["argv"], kid["env"], kid.get("port"))
        table = getattr(self.group, "table", None)
        if table is not None and why != "asked from the Control Panel":
            try:                                   # the piece's tile counts a restart it did not ask for (2026-10-10)
                c = table.child(lane)
                c["restarts"] = int(c.get("restarts") or 0) + 1
                table.write()
            except Exception:  # noqa: BLE001  a count never stops a restart
                pass
        t = self.now()
        steps = [float(x) for x in (self.cfg.get("backoff_s") or [0])]
        s.update(state="restarted", started_at=t, answered=False, fails=0, checked_at=0.0, down_since=None,
                 restarts=s["restarts"] + 1, streak=s["streak"] + 1, last_why=why, last_at=t)
        s["next_at"] = t + steps[min(s["streak"] - 1, len(steps) - 1)]
        self.event(lane, "restarted (restart %d): %s" % (s["restarts"], why))
        self.write()

    def served(self, lane: str) -> str:
        """What answers on `lane`'s port while the lane itself is down ('' = nothing: it may be started). Only a lane
        with a port and a /health route; a port taken by something that does not answer it is not "served"."""
        kid = self.kids.get(lane)
        if kid is None or not served_on():
            return ""
        path, port = (self.cfg.get("health") or {}).get(lane), kid.get("port")
        if not (path and port) or not self.taken(int(port)):
            return ""
        ok, said = self.check("http://127.0.0.1:%d%s" % (int(port), path), float(self.cfg.get("health_timeout_s") or 2))
        return said if ok else ""

    def _hold_served(self, lane: str, said: "str | None" = None, fresh: bool = False) -> bool:
        """Hold a down lane whose port another copy serves (round 17). True when it is held (the tick moves on). Its
        port is looked at once per health_every_s, never every pass: a holder that does not answer costs the loop its
        health timeout each time."""
        s, t = self.st[lane], self.now()
        if not fresh and t - float(s.get("served_at") or 0.0) < float(self.cfg.get("health_every_s") or 10):
            return s["state"] == "served"        # looked at less than health_every_s ago
        said = said if said is not None else self.served(lane)
        s["served_at"] = t
        if said:
            if s["state"] != "served":
                why = SERVED_WHY % (self.kids[lane].get("port"), said)
                s.update(state="served", down_since=None, streak=0, last_why=why, last_at=t, last_health=said)
                self.event(lane, why)
                table = getattr(self.group, "table", None)
                if table is not None:
                    try:
                        table.mark(lane, state="served", why=why)
                    except Exception:  # noqa: BLE001  the panel's view never stops the watch
                        pass
                self.write()
            return True
        if s["state"] == "served":               # that copy is gone: this lane's own now, at once
            s.update(state="down", down_since=t, next_at=t, streak=0)
            self.restart(lane, SERVED_GONE_WHY % self.kids[lane].get("port"))
            return True
        return False

    def reset_lanes(self, lanes, why: str) -> None:
        """OPEN AGENT STARTS THEM CLEAN (round 15, .101 13:37: Voice, Ear (asr), Ear (stt) stayed orange "restarted x2
        - last: Close Agent" after Open Agent brought them back): the lanes Open Agent started have no restarts to show,
        and say why they are up again; the health check takes over as for any start."""
        t = self.now()
        for lane in lanes:
            s = self.st.get(lane)
            if s is None:
                continue
            s.update(state="restarted", restarts=0, streak=0, started_at=t, answered=False, fails=0, checked_at=0.0,
                     down_since=None, last_why=why, last_at=t, next_at=t)
            self.event(lane, why)
        self.write()

    def tick(self) -> None:
        t, changed = self.now(), False
        every = float(self.cfg.get("health_every_s") or 10)
        give_up = int(self.cfg.get("give_up_after") or 0)
        for lane, kid in self.kids.items():
            s = self.st[lane]
            req = self.home / REQUESTS / lane
            if req.exists():
                req.unlink(missing_ok=True)
                s["streak"] = 0
                held = getattr(self.group, "standby", None)
                if held is not None:
                    held.discard(lane)               # a Restart asked for it: no longer held (round 17)
                self.restart(lane, "asked from the Control Panel")
                continue
            if lane in (getattr(self.group, "standby", None) or ()):
                if s["state"] != "standby":
                    s.update(state="standby", down_since=None, last_at=t, last_why=STANDBY_WHY)
                    self.event(lane, "held: " + STANDBY_WHY)
                    changed = True
                s.update(started_at=t, answered=False, fails=0, streak=0, next_at=t)    # watched afresh from a start
                continue
            if lane in AGENT_LANES and agent_closed(self.home):
                rec = agent_closed(self.home)       # Close Agent: held down, never started again until Open Agent
                if s["state"] != "closed":
                    s.update(state="closed", down_since=t, last_at=t, last_why=AGENT_WHY % (rec.get("by") or "closed"))
                    self.event(lane, s["last_why"])
                    changed = True
                continue
            if (lane in AGENT_LANES and s["state"] == "closed" and str(s.get("last_why") or "").startswith("Close Agent")
                    and not self.going_down()):
                # OPEN AGENT'S, NEVER A RESTART (round 16, .101 00:52: tts, asr, stt and presence read "restarted x1 -
                # last: Close Agent (the desk Wall): Open Agent brings her back" after Open Agent): a lane Close Agent
                # held comes back clean - no restart counted, no close reason kept; started here only if the
                # launcher's Open Agent pass has not (it runs first)
                if not self.group.alive(lane):
                    said = self.served(lane)
                    if said:                        # another copy serves its port (round 17): held, never twice
                        self._hold_served(lane, said, fresh=True)
                        changed = True
                        continue
                    self.group.spawn(lane, kid["argv"], kid["env"], kid.get("port"))
                s.update(state="restarted", restarts=0, streak=0, started_at=t, answered=False, fails=0, checked_at=0.0,
                         down_since=None, last_why=OPENED_WHY, last_at=t, next_at=t)
                self.event(lane, OPENED_WHY)
                changed = True
                continue
            if not self.group.alive(lane):
                clean = bool(getattr(self.group.procs.get(lane), "stopped_clean", False))
                if clean and self.going_down():
                    self._hold(lane)               # stopped with everything (HH): stays stopped, and a Wall stopped
                    continue                       # this way is not recorded as closed by the person
                if lane in self.closable:
                    rec = closed(self.home, lane)
                    if not rec:
                        how = deliberate_end(self.group.procs.get(lane))
                        if how:
                            rec = set_closed(lane, True, by=how, home=self.home)
                    if rec:
                        if s["state"] != "closed":
                            s.update(state="closed", down_since=t, last_at=t,
                                     last_why="closed by you (%s): open it again from the Crissa menu or the Control "
                                              "Panel" % (rec.get("by") or "closed"))
                            self.event(lane, s["last_why"])
                            changed = True
                        continue
                if served_on() and self._hold_served(lane):
                    continue                       # another copy serves its port (round 17): held, said once
                if s["down_since"] is None:
                    p = self.group.procs.get(lane)
                    s.update(down_since=t, state="down",
                             last_why="exited (code %s); its log ends: %s" % (getattr(p, "returncode", "?"),
                                                                             self.tail(lane) or "(empty)"))
                    self.event(lane, "went down: " + s["last_why"])
                    changed = True
                if give_up and s["streak"] >= give_up:
                    if s["state"] != "given_up":
                        s["state"] = "given_up"
                        self.event(lane, "given up after %d restarts in a row (give_up_after in the watch settings); "
                                         "restart it from the Control Panel" % s["streak"])
                        changed = True
                    continue
                if clean and t - s["down_since"] < CLEAN_STOP_GRACE_S:
                    continue                       # a stop on purpose: the launcher's own stop may be next
                if t >= s["next_at"]:
                    self.restart(lane, s["last_why"])
                continue
            if s["state"] == "closed":                  # opened again (Open the Wall): watched afresh
                s.update(state="up", down_since=None, started_at=t, streak=0, last_why="opened again", last_at=t)
                self.event(lane, "opened again")
                changed = True
            path, port = (self.cfg.get("health") or {}).get(lane), kid.get("port")
            if path and port and t - s["checked_at"] >= every:
                s["checked_at"] = t
                ok, said = self.check("http://127.0.0.1:%d%s" % (port, path),
                                      float(self.cfg.get("health_timeout_s") or 2))
                if ok:
                    if s["fails"] or s["state"] != "up":
                        if s["fails"]:
                            self.event(lane, "answering again")
                        s.update(state="up", fails=0, last_health=said)
                        changed = True
                    s["answered"] = True
                elif s["answered"] or t - s["started_at"] >= self._grace(lane):
                    s["fails"] += 1
                    s["last_health"] = said
                    s["state"] = "failing"
                    changed = True
                    if s["fails"] >= int(self.cfg.get("health_fails") or 3):
                        why = ("its /health did not answer %d times in a row (%s)" % (s["fails"], said)
                               if s["answered"] else
                               "it never answered /health in its %g s to load (%s)" % (self._grace(lane), said))
                        if give_up and s["streak"] >= give_up:
                            s["state"] = "given_up"
                            self.event(lane, "given up (still failing: %s); restart it from the Control Panel" % why)
                        else:
                            self.restart(lane, why)
                        continue
            elif not path and s["state"] != "up" and t - s["started_at"] >= 5:
                s["state"] = "up"                 # no health route (the ear): running is all there is to see
                changed = True
            if s["streak"] and s["state"] == "up" and s["last_at"] and t - s["last_at"] >= float(self.cfg.get("steady_s") or 120):
                s["streak"] = 0                   # it stayed up: the next fall starts the back-off again
        if changed or t - self._wrote >= every:
            self.write()
