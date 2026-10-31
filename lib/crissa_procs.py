"""The launcher's pieces on the Control Panel (2026-10-06, the User: "every service must be visible and controllable in
the Control Panel and failures must be named").

A pack runs Crissa from scripts/crissa.py: the player, the voices (Kokoro, Chatterbox Turbo, Chatterbox Multilingual
V3, Pocket), the ASR, the STT, the ear and the aligner are CHILD PROCESSES of that launcher, not systemd units, so the
panel's unit discovery never saw them: no tile, no log, no Restart, and a crash was one line on the launcher's own
terminal. This module is the seam between the two:

  launcher side   Table(home) writes <home>/run/procs.json: one row per child (pid, port, log file, state, exit
                  code, the reason it stopped, restarts) plus a heartbeat. take_requests() hands it the panel's
                  Restart / Stop / Start requests (<home>/run/requests/<name>.json); finish() answers them.
  panel side      view(home) -> tiles "proc:<name>" (state, a NAMED reason when one is down, port, log file);
                  request(home, name, action) -> a job the page polls with report(home, name, job).
  both            read_log(path, lines, before) pages a log file backwards with an explicit marker for what is not
                  shown and the byte offset that reads it; the panel's "Whole log" serves the file entire.

Nothing here caps a log line or the number of lines a person asks for. Names are a fixed shape ([a-z0-9-]); a
request can only name a child the launcher itself started. A child name the panel does not know (a voice added
later) still gets a tile, with its name as the label, so new pieces plug in without a code change here.
"""
from __future__ import annotations

import json
import os
import re
import signal
import time
import uuid
from pathlib import Path

SCHEMA = "crissa-procs/1"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
ACTIONS = ("restart", "stop", "start")
BEAT_STALE_S = 15.0          # the launcher beats every loop (0.5 s); this long silent = it is gone
READY_S = {"player": 120.0, "turbo": 240.0, "multilingual": 240.0, "aligner": 120.0, "presence": 120.0, "stt": 120.0}   # model loads take a while
READY_DEFAULT_S = 60.0

# label "Name - what it is" and section, by child name. Unknown names get their own name and the default section.
LABELS = {
    "player": ("Crissa - her picture, docked in the Wall", "Wall services"),
    "wall": ("Wall - the desk", "Wall services"),
    "panel": ("Control Panel - the Wall's panel", "Wall services"),
    "feed": ("Feed - the Wall's home server: chips, sound, the roll, Register", "Wall services"),
    "faceshell": ("House state - who is here, the ear, chat, weather (the feed reads it)", "Wall services"),
    "presence": ("Presence - who is in the room, Secure and Guest", "Wall services"),
    "camera": ("Camera - the room camera, one owner", "Wall services"),
    "enroll": ("Register - the enrollment panel", "Wall services"),
    "aligner": ("Aligner - mouths follow the words", "Wall services"),
    "tts": ("Kokoro - the resident voice", "Voices"),
    "turbo": ("Chatterbox Turbo - the GPU voice", "Voices"),
    "multilingual": ("Chatterbox Multilingual V3 - the GPU voice for your language", "Voices"),
    "pocket": ("Pocket - the CPU voice", "Voices"),
    "asr": ("ASR - speech to text", "Ear"),
    "stt": ("STT - the microphone's turns", "Ear"),
    "ear": ("Ear - each turn becomes a question to the agent", "Ear"),
    "bridge": ("Ear bridge - wake word, who spoke, her reply", "Ear"),
    "render": ("Render - the order runner on your ComfyUI", "Render"),
    "oracle": ("Oracle - the camera's face check and door feed", "Wall services"),
    "demo": ("Demo - the demo helper (Dev mode)", "Wall services"),
    "audio8": ("Audio8 - the announcer's voice", "Voices"),
    "warm": ("Keep-warm - holds a GPU voice ready", "Voices"),
}
DEFAULT_SECTION = "Wall services"
# the Voices section's ids (lib/crissa_voice_panel.py) -> the launcher child that speaks for it
VOICE_PROC = {"turbo": "turbo", "multilingual": "multilingual", "pocket": "pocket", "kokoro": "tts", "tts": "tts"}

_ERRLINE = re.compile(r"(Error|Exception|Traceback|FATAL|Fatal|fatal|failed|Failed|refus|denied|Killed|"
                      r"No such file|not found|cannot|can't|unable|Unable)")


def run_dir(home) -> Path:
    return Path(home) / "run"


def table_path(home) -> Path:
    return run_dir(home) / "procs.json"


def req_dir(home) -> Path:
    return run_dir(home) / "requests"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---- why a child stopped ------------------------------------------------------------------------------------------
def _signame(n: int) -> str:
    try:
        return signal.Signals(n).name
    except (ValueError, AttributeError):
        return "signal %d" % n


def last_words(path, window: int = 65536) -> str:
    """The line in a log's tail that says what went wrong: the last error-looking line (a traceback's final line),
    else the last non-empty line. Reads only the end of the file to FIND the line; the log itself is never cut."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - window))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return pick_reason(data.splitlines())


def pick_reason(lines) -> str:
    """Of some log lines, the one that names the trouble: the last error-looking line (a traceback's final line,
    not its frames), else the last non-empty line."""
    lines = [ln.strip() for ln in lines if ln and ln.strip() and not ln.startswith("--- ")]
    for ln in reversed(lines):
        if _ERRLINE.search(ln) and not ln.startswith(("File \"", "Traceback")):
            return ln
    return lines[-1] if lines else ""


def exit_reason(returncode, log=None) -> str:
    """'exit 1: ModuleNotFoundError: No module named x' / 'killed by SIGKILL: ...' / 'exited cleanly'."""
    if returncode is None:
        return ""
    if returncode < 0:
        head = "killed by %s" % _signame(-returncode)
    elif returncode == 0:
        head = "exited cleanly (exit 0)"
    else:
        head = "exit %d" % returncode
    words = last_words(log) if log else ""
    return head + (": " + words if words else "")


# ---- the launcher's side --------------------------------------------------------------------------------------------
class Table:
    """The launcher's live record. One writer (the launcher); the panel only reads it."""

    def __init__(self, home, launcher_pid: int | None = None, argv: list | None = None):
        self.home = Path(home)
        self.path = table_path(home)
        self.data = {"schema": SCHEMA, "launcher": {"pid": launcher_pid or os.getpid(), "started": _now(),
                                                    "argv": list(argv or []), "state": "running", "beat": time.time()},
                     "children": {}}
        req_dir(home).mkdir(parents=True, exist_ok=True)
        for f in req_dir(home).glob("*.json"):       # requests left from a launcher that is gone are void
            try:
                f.unlink()
            except OSError:
                pass
        self.write()

    def write(self) -> None:
        self.data["launcher"]["beat"] = time.time()
        _write(self.path, self.data)

    def child(self, name: str) -> dict:
        return self.data["children"].setdefault(name, {"name": name, "restarts": 0})

    def started(self, name: str, pid: int, port, log, argv: list, by: str = "launcher") -> None:
        c = self.child(name)
        c.update(pid=pid, port=port, log=str(log), argv=[str(a) for a in argv], state="running", started=_now(),
                 exit_code=None, why="", stopped=None, by=by)
        self.write()

    def exited(self, name: str, returncode, *, expected: bool = False, why: str = "") -> None:
        c = self.child(name)
        c.update(state="stopped" if expected else "exited", exit_code=returncode, stopped=_now(),
                 why=why or exit_reason(returncode, c.get("log")))
        self.write()

    def mark(self, name: str, **kw) -> None:
        self.child(name).update(kw)
        self.write()

    def stop(self, why: str = "") -> None:
        self.data["launcher"].update(state="stopped", stopped=_now(), why=why)
        self.write()


def take_requests(home) -> list:
    """[{name, action, job}] waiting for the launcher, oldest first; each is taken once."""
    out = []
    d = req_dir(home)
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if f.name.endswith(".done.json"):
            continue
        r = _read(f)
        try:
            f.unlink()
        except OSError:
            continue
        name, act, job = str(r.get("name") or ""), str(r.get("action") or ""), str(r.get("job") or "")
        if NAME.fullmatch(name) and job and (act in ACTIONS or (act == QUIT and name == QUIT_NAME)):
            out.append({"name": name, "action": act, "job": job, "by": str(r.get("by") or "")})
    return out


# STOP CRISSA (the User 2026-10-10, the Crissa menu): one request stops everything the launcher runs, in process mode
# and in service mode alike - it stops its children (or its units) the way Ctrl-C does and exits 0, so its service
# (Restart=on-failure) does not start it again until the next login. The answer lands in crissa.done.json.
QUIT, QUIT_NAME = "quit", "crissa"


def request_quit(home, by: str = "menu") -> dict:
    alive, why = launcher_alive(load(home))
    if not alive:
        return {"ok": False, "error": why}
    job = uuid.uuid4().hex[:12]
    try:
        (req_dir(home) / ("%s.done.json" % QUIT_NAME)).unlink()
    except OSError:
        pass
    _write(req_dir(home) / ("%s.json" % QUIT_NAME), {"name": QUIT_NAME, "action": QUIT, "job": job, "by": str(by)[:20],
                                                     "ts": _now()})
    return {"ok": True, "job": job, "note": "Stopping Crissa…"}


def finish(home, name: str, job: str, ok: bool, note: str, state: str = "done") -> None:
    _write(req_dir(home) / ("%s.done.json" % name), {"job": job, "name": name, "ok": bool(ok), "note": note,
                                                       "state": state, "ts": _now()})


# ---- the panel's side ----------------------------------------------------------------------------------------------
def load(home) -> dict:
    return _read(table_path(home))


def launcher_alive(tab: dict, now: float | None = None) -> "tuple[bool, str]":
    """(alive, why not). The heartbeat decides (portable; a reused pid cannot fake a beat)."""
    now = time.time() if now is None else now
    la = tab.get("launcher") or {}
    if not la:
        return False, "Crissa has not been started on this machine yet (python3 scripts/crissa.py)"
    if la.get("state") == "stopped":
        return False, "Crissa was stopped %s%s - start it: python3 scripts/crissa.py" % (
            str(la.get("stopped") or "").replace("T", " ")[:16], (" (%s)" % la["why"]) if la.get("why") else "")
    try:
        age = now - float(la.get("beat") or 0)
    except (TypeError, ValueError):
        age = 1e9
    if age > BEAT_STALE_S:
        return False, "the Crissa launcher stopped answering %s ago (pid %s) - start it: python3 scripts/crissa.py" % (
            _ago(age), la.get("pid"))
    return True, ""


def _ago(s: float) -> str:
    s = int(s)
    return "%ds" % s if s < 120 else ("%d min" % (s // 60) if s < 7200 else "%d h" % (s // 3600))


def _port_up(port, timeout: float = 0.4) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def voice_chosen(name: str, home: Path | None = None) -> bool:
    """Does this install's voice choice (<state home>/voice-tier.json, Init's: main, tier, chain, announcer chain) name
    the lane `name`? True when the record is missing or unreadable: an unknown choice never hides a lane."""
    try:
        if home is None:
            import crissa_home
            home = Path(crissa_home.HOME)
        rec = json.loads((Path(home) / "voice-tier.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return True
    if not isinstance(rec, dict) or not (rec.get("main") or rec.get("tier")):
        return True
    named = {str(rec.get("main") or ""), str(rec.get("tier") or ""), str(rec.get("announcer") or "")}
    for k in ("chain", "announcer_chain"):
        named.update(str(x) for x in (rec.get(k) or []))
    return name in named


def tile(name: str, c: dict, alive: bool, why_launcher: str, port_up=_port_up, pid_alive=_pid_alive) -> dict:
    label, section = LABELS.get(name, (name, DEFAULT_SECTION))
    r = {"unit": "proc:" + name, "proc": name, "label": label, "section": section, "port": c.get("port"),
         "scope": "launcher", "log": c.get("log") or "", "pid": c.get("pid"), "restarts": int(c.get("restarts") or 0),
         "planned_restarts": int(c.get("planned_restarts") or 0),
         "active": "", "sub": "", "health": "ok", "note": ""}
    st = c.get("state") or "unknown"
    if not alive:
        r.update(active="inactive", sub="launcher gone", health="bad",
                 note=why_launcher + ((" · last: " + c["why"]) if c.get("why") else ""))
        return r
    if st == "running":
        if c.get("pid") and not pid_alive(c["pid"]):
            r.update(active="failed", sub="gone", health="bad",
                     note="its process is gone and the launcher has not noticed yet: " + (last_words(c.get("log")) or "no log line"))
        elif c.get("port") and not port_up(c["port"]):
            r.update(active="activating", sub="no port", health="warn",
                     note="running, :%s not answering yet%s" % (c["port"], (" · " + last_words(c.get("log"))) if c.get("log") else ""))
        else:
            r.update(active="active", sub="running")
    elif st == "restarting":
        r.update(active="activating", sub="restarting", health="warn", note="restarting (asked from the panel)")
    elif st == "stopped":
        why = str(c.get("why") or "")
        r.update(active="inactive", sub="stopped", health="warn", parked=True,
                 note=(why if why.startswith("Close Agent") else      # held by Close Agent (round 15): says so
                       "stopped from the panel %s - Start brings it back" % str(c.get("stopped") or "").replace("T", " ")[11:16]))
    elif st == "closed":
        r.update(active="inactive", sub="closed", health="warn", parked=True, note=str(c.get("why") or "closed on purpose"))
    elif st == "served":
        # ANOTHER COPY SERVES ITS PORT (round 17, 2026-10-13, lib/crissa_watch): held, not started twice - never red
        r.update(active="active", sub="served", health="ok", note=str(c.get("why") or "another copy answers on its port"))
    elif st == "standby":
        # INSTALLED, NOT RUNNING (round 17, 2026-10-13, lib/crissa_voice_demand): a voice no role uses now, held until a
        # line needs it - off by design, never red and never counted as down
        r.update(active="inactive", sub="standby", health="ok", parked=True, standby=True,
                 note=str(c.get("why") or "installed, not running"))
    elif st == "waiting" and not voice_chosen(name):
        # NOT INSTALLED, NOT CHOSEN (.101 round 30, the User 2026-10-18: the Wall's CRISSA chip "still red when all
        # seems green now"): a voice lane this install's voice choice does not use (Audio8, when Pocket is her voice and
        # her announcer) fell to the last branch below and read "exited", red, with every piece of hers running. Like
        # standby it is off by design, never red and never counted as down. A lane the choice DOES name (the full
        # build's Audio8 that announces the board: "be sure you turn that off only for this pocket mode") still reads
        # red while it waits, as before.
        r.update(active="inactive", sub="not installed", health="ok", parked=True, standby=True,
                 note=str(c.get("why") or "not installed"))
    else:
        r.update(active="failed", sub="exited", health="bad", note=c.get("why") or "exited (no reason recorded)")
    return r


def without_live_units(prow: list, rows: list) -> list:
    """Drop a proc tile only when its crissa-<name> row is actually installed.

    The curated "not installed" tile is a phantom. Hiding the green proc tile behind it would leave
    the panel showing only the red one. A missing, unknown, or blank active state keeps the proc tile.
    """
    live = set()
    for r in rows or []:
        active = str(r.get("active") or "")
        if active in ("not-found", "unknown", ""):
            continue
        unit = str(r.get("unit") or "")
        if unit.endswith(".service"):
            unit = unit[:-8]
        if unit.startswith("crissa-"):
            live.add(unit[len("crissa-"):])
    out = []
    for t in prow or []:
        name = t.get("proc") or str(t.get("unit") or "")
        if str(name).startswith("proc:"):
            name = str(name)[5:]
        if name in live:
            continue
        out.append(t)
    return out


def view(home, port_up=_port_up, now: float | None = None) -> dict:
    """{"present": bool, "launcher": {alive, why, pid}, "tiles": [...]}; present False when Crissa's launcher never
    ran with this state home (the house, which runs as its own services, never has one)."""
    tab = load(home)
    if not tab:
        return {"present": False, "launcher": {"alive": False, "why": ""}, "tiles": []}
    alive, why = launcher_alive(tab, now)
    kids = tab.get("children") or {}
    order = list(LABELS)
    names = sorted(kids, key=lambda n: (order.index(n) if n in order else len(order), n))
    return {"present": True, "launcher": {"alive": alive, "why": why, "pid": (tab.get("launcher") or {}).get("pid")},
            "tiles": [tile(n, kids[n], alive, why, port_up) for n in names if NAME.fullmatch(n)]}


# ONE COUNT ON BOTH PANELS (the User 2026-10-10, .101 round 5: the Control Panel at :8097 said "All normal, 24 units
# running" while the feed's panel at :8099/panel said "ALL NORMAL, 16 units running" - one counted its tiles, voices and
# lanes included, the other the launcher's children). Both headers now say the number this one function reads from the
# launcher's own table (<home>/run/procs.json): Crissa's launcher, while its heartbeat is fresh, plus each piece it
# started that is running now (its state is running and its process is alive). In service mode each of them is one
# crissa-*.service unit, so the number equals the running units `systemctl --user list-units 'crissa-*'` lists; in
# process mode it is the same pieces as processes. Not counted: a piece waiting for Init to install it, a Wall the
# person closed, a piece stopped from the panel, one that is down, and the add-on tiles (krew) - those are tiles, not
# Crissa's own units. The tiles and their down/warn names are unchanged.
RUNNING_WHAT = ("Crissa's launcher and each piece it started that is running now (in service mode, its "
                "crissa-*.service units)")


def running_count(home, now: float | None = None, pid_alive=_pid_alive) -> dict:
    """{"running": n, "pieces": [names], "what": RUNNING_WHAT}; running 0 when the launcher is not running here."""
    tab = load(home)
    alive, _why = launcher_alive(tab, now) if tab else (False, "")
    if not alive:
        return {"running": 0, "pieces": [], "what": RUNNING_WHAT}
    names = sorted(n for n, c in (tab.get("children") or {}).items()
                   if NAME.fullmatch(str(n)) and (c or {}).get("state") == "running" and (c or {}).get("pid")
                   and pid_alive(c["pid"]))
    return {"running": 1 + len(names), "pieces": ["launcher"] + names, "what": RUNNING_WHAT}


def known(home, name: str) -> bool:
    return bool(NAME.fullmatch(name or "")) and name in (load(home).get("children") or {})


def request(home, name: str, action: str) -> dict:
    """Queue a Restart / Stop / Start for the launcher. A refusal says why in plain words."""
    if action not in ACTIONS:
        return {"ok": False, "error": "unknown action %r" % action}
    if not known(home, name):
        return {"ok": False, "error": "%r is not one of the pieces the Crissa launcher started" % name}
    alive, why = launcher_alive(load(home))
    if not alive:
        return {"ok": False, "error": why}
    job = uuid.uuid4().hex[:12]
    done = req_dir(home) / ("%s.done.json" % name)
    try:
        done.unlink()
    except OSError:
        pass
    _write(req_dir(home) / ("%s.json" % name), {"name": name, "action": action, "job": job, "ts": _now()})
    return {"ok": True, "job": job, "proc": name, "note": "%s %s…" % (
        {"restart": "Restarting", "stop": "Stopping", "start": "Starting"}[action], name)}


def report(home, name: str, job: str) -> dict:
    d = _read(req_dir(home) / ("%s.done.json" % name)) if NAME.fullmatch(name or "") else {}
    if d.get("job") == job:
        return d
    if (req_dir(home) / ("%s.json" % name)).exists():
        return {"state": "queued", "job": job, "note": "waiting for the launcher to take it"}
    alive, why = launcher_alive(load(home))
    if not alive:
        return {"state": "done", "ok": False, "job": job, "note": why}
    return {"state": "running", "job": job, "note": "the launcher is on it"}


def wire_voices(voices: dict, proc_tiles: list) -> set:
    """The Voices section's tiles (lib/crissa_voice_panel.py) get their launcher child's keys and log: `unit`
    "proc:<child>", `log`, and when that child is down its named reason (a crash outranks "off"). Returns the
    child names the Voices section now speaks for, so the services list does not draw them twice."""
    by = {t.get("proc"): t for t in proc_tiles or []}
    used = set()
    for v in (voices or {}).get("tiles") or []:
        child = VOICE_PROC.get(str(v.get("id") or ""))
        t = by.get(child)
        if not t:
            continue
        used.add(child)
        v.update(unit=t["unit"], log=t.get("log") or "", proc=child, keys=True)
        if t.get("health") == "bad":
            v.update(state="bad", mark="◐", note=t.get("note") or "down")
        elif t.get("health") == "warn" and v.get("state") == "ok":
            v.update(state="warn", note=t.get("note") or v.get("note"))
    return used


# ---- reading a log, whole or a page at a time -----------------------------------------------------------------------
def read_log(path, lines: int = 200, before: int | None = None, chunk: int = 65536) -> dict:
    """The last `lines` lines of a file ending at byte `before` (EOF when None), read backwards. Returns the lines,
    the byte range shown, and when there is more above them `earlier` (the offset that reads the page before) and a
    `marker` that says so. Lines are never cut; `lines` is never capped."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "error": "%s: %s" % (p, e.strerror or type(e).__name__), "lines": [], "file": str(p)}
    end = size if before is None else max(0, min(int(before), size))
    lines = max(1, int(lines))
    pos, data = end, b""
    try:
        with p.open("rb") as fh:
            while pos > 0 and data.count(b"\n") <= lines:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
    except OSError as e:
        return {"ok": False, "error": "%s: %s" % (p, e.strerror or type(e).__name__), "lines": [], "file": str(p)}
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()                                      # the newline that ends the last line shown
    start = pos
    if pos > 0:                                          # the first piece may be the end of a line above: give it back
        start += len(parts[0]) + 1
        parts = parts[1:]
    if len(parts) > lines:
        drop = parts[:len(parts) - lines]
        start += sum(len(x) + 1 for x in drop)
        parts = parts[len(parts) - lines:]
    out = {"ok": True, "file": str(p), "size": size, "start": start, "end": end,
           "lines": [x.decode("utf-8", "replace").rstrip("\r") for x in parts]}
    if start > 0:
        out["earlier"] = start
        out["marker"] = "── %s earlier in %s not shown · Earlier reads them, Whole log opens the file ──" % (
            _bytes(start), p.name)
    return out


def _bytes(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)
