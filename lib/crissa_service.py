"""Who restarts the launcher itself (2026-10-06): Crissa's launcher as a service of the person's own login.

The launcher (scripts/crissa.py) restarts its lanes (lib/crissa_watch). When its PLAYER dies it stops the whole
group and exits 1; this service is what starts it again, so nothing Crissa runs is left with no one to restart it:

    Linux  a systemd USER unit, ~/.config/systemd/user/crissa-launcher.service, enabled for the login
           (default.target), Restart=on-failure, RestartSec=5, at most 5 starts in 10 minutes. A bad setup (exit 2)
           or a taken port (exit 3) is not retried (RestartPreventExitStatus): retrying cannot fix either.
    macOS  a launchd agent, ~/Library/LaunchAgents/com.crissa.launcher.plist, RunAtLoad and KeepAlive
           {SuccessfulExit: false} (started again after a failing exit), ThrottleInterval 10. launchd has no
           "do not retry this code", so under launchd the launcher exits 0 for a bad setup or a taken port (the
           reason is still written). UNVERIFIED: written and unit-tested, never run on a Mac here.

Init installs it when a first boot finishes (lib/crissa_init), and by hand:
    python3 scripts/crissa.py --service install     # write it and enable it for the login (started now only when
                                                    # no launcher is running: it would find its own ports taken)
    python3 scripts/crissa.py --service status      # JSON: installed, enabled, state, restarts, the last reason
    python3 scripts/crissa.py --uninstall           # stop it, disable it and remove its file (= --service uninstall)

WHY IT RESTARTED: the launcher writes <crissa home>/launcher.json (pid, how it runs, how many runs, and the last
run's end: exit code and why, e.g. "the player stopped (exit -9): its log ends ...") and appends each start and end
to <crissa home>/logs/launcher.log. A run that ended without a word (SIGKILL, power) is named as such by the next.
The Control Panel's launcher tile reads that file and the service manager's own state (lib/crissa_service.status).
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

try:
    from crissa_home import HOME
except ImportError:  # imported by path from a test or a tool
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

ROOT = Path(__file__).resolve().parent.parent
UNIT = "crissa-launcher.service"
LABEL = "com.crissa.launcher"
STATE = "launcher.json"
RUNS_LOG = "launcher.log"
NO_RETRY = (2, 3)                 # a bad setup, a taken port: a retry cannot fix them
Runner = Callable[[list, float], "tuple[int, str, str]"]


def default_run(argv: list, timeout: float = 30.0) -> "tuple[int, str, str]":
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %.0fs" % timeout
    except OSError as e:
        return 127, "", "%s: %s" % (type(e).__name__, e)


def manager(platform: str | None = None) -> str | None:
    p = platform or sys.platform
    return "systemd" if p.startswith("linux") else ("launchd" if p == "darwin" else None)


def unit_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("CRISSA_SYSTEMD_USER_DIR") or (Path.home() / ".config" / "systemd" / "user")).expanduser()


def agent_dir(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("CRISSA_LAUNCHAGENTS_DIR") or (Path.home() / "Library" / "LaunchAgents")).expanduser()


def service_file(platform: str | None = None, env=None) -> Path | None:
    m = manager(platform)
    if m == "systemd":
        return unit_dir(env) / UNIT
    if m == "launchd":
        return agent_dir(env) / (LABEL + ".plist")
    return None


def under(env=None) -> str:
    """How this launcher runs: systemd, launchd or hand (the unit and the agent set CRISSA_SERVICE)."""
    env = os.environ if env is None else env
    s = str(env.get("CRISSA_SERVICE") or "").strip().lower()
    return s if s in ("systemd", "launchd") else "hand"


def launch_argv(args: list, python: str | None = None) -> list:
    """The command the service runs: this python, this launcher, the person's own flags, never --plan. --no-open only
    when the person gave it (2026-10-10, .101 finding T: the service always added it, so a first boot under the service
    never opened Setup; the launcher opens /init once, in the person's graphical session the unit carries)."""
    keep = [a for a in args if a != "--plan"]
    return [python or sys.executable, str(ROOT / "scripts" / "crissa.py"), *keep]


def _sd_quote(s: str, exec_: bool = True) -> str:
    """One quoted word for a unit file: C escapes for \\ " and newline, %% for a specifier, and on an ExecStart= line
    $$ for a dollar (systemd expands $VAR there; in Environment= a dollar means nothing and stays as it is)."""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("%", "%%")
    if exec_:
        s = s.replace("$", "$$")
    return '"%s"' % s


# ---- ONE LAUNCH FOR EVERY CHILD, IN BOTH MODES (the User 2026-10-10, the first real-hardware test on .101) -----------
# On a desktop the launcher hands off to systemd user units (each child its own crissa-<name>.service), and every
# acceptance and trial before had run in process mode (the sandbox has no user bus). The units carried six prefixes
# only and dropped any name with TOKEN in it, so Register's switches (HOUSE_REGISTER_TOKEN, RAIL_REGISTER_TOKEN), every
# RAIL_*, VO_* and EAR_BRIDGE_* address the plan gives and the person's own settings never reached a child: Voice ID
# refused ("RAIL_REGISTER_TOKEN is off") and said the camera was busy. Now both modes start a child from child_launch():
# the same argv, the same working directory and the same environment, key by key. Two things a unit file never holds:
#   SYSTEMD_OWN   what systemd sets for each run of a unit itself (its invocation id, journal stream, main pid);
#   a secret-named variable (KEY, TOKEN, SECRET or PASSWORD in its name): it goes to a 0600 file under the state home
#                 (<home>/run/units/<unit>.env) that the unit reads with EnvironmentFile=, so it still arrives.
# The handoff carries the person's environment to the launcher's own unit the same way (carried_env), less what only
# a terminal has (SESSION_ONLY). tests/test_crissa_service_parity.py renders every unit and compares them with the
# process-mode plan; scripts/public-loops-accept.py does it again on each exported pack.
SECRET_MARKS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
SYSTEMD_OWN = frozenset((
    "INVOCATION_ID", "JOURNAL_STREAM", "MANAGERPID", "SYSTEMD_EXEC_PID", "NOTIFY_SOCKET", "LISTEN_PID",
    "LISTEN_FDS", "LISTEN_FDNAMES", "WATCHDOG_PID", "WATCHDOG_USEC", "MAINPID", "SERVICE_RESULT", "EXIT_CODE",
    "EXIT_STATUS", "MONITOR_SERVICE_RESULT", "MONITOR_EXIT_CODE", "MONITOR_EXIT_STATUS", "MONITOR_INVOCATION_ID",
    "MONITOR_UNIT", "TRIGGER_UNIT", "TRIGGER_PATH", "TRIGGER_TIMER_REALTIME_USEC", "TRIGGER_TIMER_MONOTONIC_USEC",
    "RUNTIME_DIRECTORY", "STATE_DIRECTORY", "CACHE_DIRECTORY", "LOGS_DIRECTORY", "CONFIGURATION_DIRECTORY",
    "CREDENTIALS_DIRECTORY", "MEMORY_PRESSURE_WATCH", "MEMORY_PRESSURE_WRITE", "SYSTEMD_NSS_DYNAMIC_BYPASS"))
# what only a terminal session has: not carried into a login service (it has no terminal)
SESSION_ONLY = frozenset((
    "TERM", "COLORTERM", "SHELL", "PWD", "OLDPWD", "SHLVL", "_", "LS_COLORS", "LESSOPEN", "LESSCLOSE", "COLUMNS",
    "LINES", "PS1", "PS2", "PS4", "PROMPT_COMMAND", "HISTFILE", "HISTSIZE", "HISTFILESIZE", "HISTCONTROL", "MAIL",
    "SSH_CLIENT", "SSH_CONNECTION", "SSH_TTY", "TMUX", "TMUX_PANE", "STY", "WINDOW", "WINDOWID", "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION", "VTE_VERSION", "GNOME_TERMINAL_SCREEN", "GNOME_TERMINAL_SERVICE", "KONSOLE_DBUS_SERVICE",
    "KONSOLE_DBUS_SESSION", "KONSOLE_DBUS_WINDOW", "KONSOLE_VERSION", "PYTEST_CURRENT_TEST"))
# the launcher's service sets these itself (unit_text); the hand values do not cross
LAUNCHER_SETS = ("CRISSA_SERVICE", "PYTHONUNBUFFERED")   # CRISSA_OPEN is the person's, and crosses (finding T)
LAUNCHER_ENV_KEYS = "CRISSA_LAUNCHER_ENV_KEYS"     # the person's names, handed to the player for Init's install

# THE LIVE SESSION'S DISPLAY KEYS (the User 2026-10-24, .101 on 1.0.0: after a reboot `systemctl --user start
# crissa-launcher` left the Wall in a crash loop - `couldn't connect to display ":0"` - because every child unit still
# carried the INSTALL session's XAUTHORITY (/run/user/1000/.mutter-Xwaylandauth.6CBGX3) while the new login's was
# ...QIDSX3; a hand start rewrote the units with the live key and the Wall came up; round 36 saw the same loop after a
# GNOME Shell crash). A unit file is a snapshot of one session; these keys belong to the session that is up NOW. So
# every start path reads them live - the user manager's environment (`systemctl --user show-environment`, where
# gnome-session / mutter import DISPLAY, WAYLAND_DISPLAY and the Xwayland XAUTHORITY at each login), gaps filled from
# the session's own gnome-shell (/proc/<pid>/environ, this uid) - and lays them over the launcher's environment at its
# start (adopt_live_session) and over each child's at its start (child_launch). Kill: CRISSA_LIVE_SESSION_ENV=0 (the
# unit's snapshot, as 1.0.0).
SESSION_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                "XDG_SESSION_TYPE")
SESSION_SHELLS = ("gnome-shell",)       # the process whose environment IS the live session (its /proc/<pid>/environ)
LIVE_SESSION_SWITCH = "CRISSA_LIVE_SESSION_ENV"

# WHAT A HAND START CARRIES INTO THE SERVICE (the User 2026-10-24, .101: CRISSA_LAUNCHER_ENV_KEYS listed CLAUDE_CODE_*
# because he started her from a Claude shell - the units carried that shell's WHOLE environment). The handoff carries
# the names she reads, not everything: her own switches by prefix, the session and locale keys by name, the toolchain
# and proxy names, and whatever the person adds in CRISSA_CARRY_ENV_EXTRA (names or prefixes ending in _, comma-
# separated). Kill: CRISSA_CARRY_ENV=all carries every name as 1.0.0 did (less SESSION_ONLY / SYSTEMD_OWN).
CARRY_PREFIXES = ("CRISSA_", "CRISSA_", "HOUSE_", "RAIL_", "VO_", "EAR_BRIDGE_", "KREW_", "XDG_", "LC_",
                  "GTK_", "GDK_", "QT_", "PYTHON", "CUDA_", "NVIDIA_", "HF_", "OMP_", "MKL_", "SSL_", "REQUESTS_",
                  "PULSE_", "PIPEWIRE_", "SDL_", "TORCH_", "XAI_", "OPENAI_", "ANTHROPIC_", "STRIPE_")
CARRY_NAMES = frozenset(("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "VIRTUAL_ENV",
                         "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                         "XDG_SESSION_TYPE", "http_proxy", "https_proxy", "no_proxy", "all_proxy", "HTTP_PROXY",
                         "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "CURL_CA_BUNDLE", "LD_LIBRARY_PATH", "LIBGL_ALWAYS_SOFTWARE"))
CARRY_SWITCH = "CRISSA_CARRY_ENV"
CARRY_EXTRA = "CRISSA_CARRY_ENV_EXTRA"


def live_session_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(LIVE_SESSION_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def _parse_env_lines(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            k = k.strip()
            if k:
                out[k] = v
    return out


def _shell_proc_env(proc: Path | None = None, uid: int | None = None) -> dict:
    """The live session's gnome-shell environment, from /proc (this uid). {} where there is none to read."""
    proc = Path("/proc") if proc is None else proc
    uid = os.getuid() if uid is None else uid
    try:
        pids = [d for d in proc.iterdir() if d.name.isdigit()]
    except OSError:
        return {}
    for d in pids:
        try:
            if d.stat().st_uid != uid:
                continue
            if (d / "comm").read_text().strip() not in SESSION_SHELLS:
                continue
            raw = (d / "environ").read_bytes()
        except OSError:
            continue
        out = {}
        for item in raw.split(b"\0"):
            if b"=" in item:
                k, _, v = item.partition(b"=")
                out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
        return out
    return {}


def live_session_env(run: Runner = default_run, proc_env=None, env=None) -> dict:
    """The display keys of the session that is up now: the user manager's environment first (what the login imported),
    the live gnome-shell's /proc environment for a key it lacks. Only SESSION_KEYS, only those found. {} when off or
    nothing answers (the caller keeps what it has)."""
    if not live_session_on(env):
        return {}
    found = {}
    try:
        rc, so, _se = run(["systemctl", "--user", "show-environment"], 5.0)
        if rc == 0:
            found = _parse_env_lines(so)
    except Exception:  # noqa: BLE001  no user manager here: the shell's own environment alone
        found = {}
    out = {k: found[k] for k in SESSION_KEYS if found.get(k)}
    if len(out) < len(SESSION_KEYS):
        shell = _shell_proc_env() if proc_env is None else proc_env
        for k in SESSION_KEYS:
            if k not in out and shell.get(k):
                out[k] = shell[k]
    return out


def adopt_live_session(env=None, session: dict | None = None, run: Runner = default_run) -> dict:
    """At the launcher's start: the live session's display keys laid over its own environment (in place; os.environ by
    default). Returns {key: (old, new)} for what changed, so the launcher can say it."""
    e = os.environ if env is None else env
    live = live_session_env(run) if session is None else dict(session)
    changed = {}
    for k, v in live.items():
        if e.get(k) != v:
            changed[k] = (e.get(k), v)
            e[k] = v
    return changed


def secret_name(name: str) -> bool:
    up = str(name).upper()
    return any(m in up for m in SECRET_MARKS)


def child_launch(argv: list, kid_env: dict | None, base: dict | None = None, session: dict | None = None) -> dict:
    """THE one record of how a child starts, in process mode (Popen) and in service mode (its unit): argv, the working
    directory and the whole environment - the launcher's own, then the LIVE session's display keys (live_session_env;
    read now, at this child's start, never the launcher's snapshot: 2026-10-24), then what the plan gives this child.
    `session` names those keys outright (a test); None reads them when `base` is the launcher's own environment."""
    if session is None and base is None:
        session = live_session_env()
    base = os.environ if base is None else base
    merged = dict(base)
    merged.update(session or {})
    merged.update(kid_env or {})
    env = {str(k): str(v) for k, v in merged.items() if v is not None}
    return {"argv": [str(a) for a in argv], "cwd": str(ROOT), "env": env}


def unit_env_split(env: dict) -> "tuple[dict, dict]":
    """(inline, secret): what a unit file says with Environment= and what goes to its 0600 EnvironmentFile. The keys
    systemd sets for each run are in neither."""
    inline, secret = {}, {}
    for k, v in (env or {}).items():
        if v is None or k in SYSTEMD_OWN:
            continue
        (secret if secret_name(k) else inline)[str(k)] = str(v)
    return inline, secret


def carried_env(env: dict | None = None) -> dict:
    """What the hand start hands to the launcher's service: the person's environment, less what only a terminal has
    and what systemd sets itself. Init's install from the player (which is a child, with the plan's keys on top) carries
    only the names the launcher passed it (CRISSA_LAUNCHER_ENV_KEYS)."""
    env = os.environ if env is None else env
    names = None
    raw = env.get(LAUNCHER_ENV_KEYS)
    if raw:
        try:
            names = set(json.loads(raw))
        except ValueError:
            names = None
    out = {}
    for k, v in env.items():
        if v is None or k in SESSION_ONLY or k in SYSTEMD_OWN or k in LAUNCHER_SETS or k == LAUNCHER_ENV_KEYS:
            continue
        if names is not None and k not in names:
            continue
        if names is None and not carried_name(k, env):
            continue
        out[str(k)] = str(v)
    return out


def _carry_extra(env: dict) -> "tuple[set, tuple]":
    names, prefixes = set(), []
    for w in str(env.get(CARRY_EXTRA, "")).replace(";", ",").split(","):
        w = w.strip()
        if not w:
            continue
        (prefixes.append if w.endswith("_") else names.add)(w)
    return names, tuple(prefixes)


def carried_name(name: str, env: dict | None = None) -> bool:
    """Is this one of the names a hand start carries into the service (CARRY_PREFIXES / CARRY_NAMES / the person's
    CRISSA_CARRY_ENV_EXTRA)? Everything, when CRISSA_CARRY_ENV=all (1.0.0's way)."""
    e = os.environ if env is None else env
    if str(e.get(CARRY_SWITCH, "")).strip().lower() in ("all", "1", "everything"):
        return True
    if name in CARRY_NAMES or name.startswith(CARRY_PREFIXES):
        return True
    extra_names, extra_prefixes = _carry_extra(e)
    return name in extra_names or (bool(extra_prefixes) and name.startswith(extra_prefixes))


def env_file_path(home: Path | None, unit: str) -> Path:
    return Path(home or HOME) / "run" / "units" / (unit + ".env")


def _env_file_quote(v: str) -> str:
    # systemd's EnvironmentFile: inside double quotes a backslash escapes " \ ` $; a raw newline stays in the value
    out = []
    for ch in str(v):
        out.append("\\" + ch if ch in '"\\`$' else ch)
    return '"%s"' % "".join(out)


def env_file_text(pairs: dict) -> str:
    return "".join("%s=%s\n" % (k, _env_file_quote(v)) for k, v in sorted(pairs.items()))


def write_env_file(path: Path, pairs: dict) -> Path:
    """Write the 0600 file a unit reads (EnvironmentFile=), its folder 0700, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(env_file_text(pairs))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _launcher_pairs(home: Path | None, env=None) -> dict:
    pairs = carried_env(env)
    pairs.update(CRISSA_SERVICE="", PYTHONUNBUFFERED="1")
    if (env or {}).get("CRISSA_HOME") or home:
        pairs["CRISSA_HOME"] = str(home or (env or {}).get("CRISSA_HOME"))
    return pairs


def _env_pairs(home: Path | None, env=None) -> dict:
    """The launcher service's environment (both managers): the person's, carried (carried_env)."""
    return _launcher_pairs(home, os.environ if env is None else env)


STOPS_LOG = "launcher-stops.log"          # <home>/run/: one line per end of the service's main process (ExecStopPost)


def stops_log(home: Path | None = None) -> Path:
    return Path(home or HOME) / "run" / STOPS_LOG


def unit_text(argv: list, home: Path | None = None, env=None, env_file: Path | None = None) -> str:
    """The launcher's own unit. A secret-named variable goes to `env_file` (written by install), never in here.

    WHY IT STOPPED, IN SYSTEMD'S OWN WORDS (the User 2026-10-10, .101: a stop on purpose read as "killed (SIGKILL, out
    of memory) or the machine stopped"): ExecStopPost appends `<epoch> $SERVICE_RESULT $EXIT_CODE $EXIT_STATUS` to
    <home>/run/launcher-stops.log after every end of the main process - a stop, a crash, an out-of-memory kill, a stop
    that timed out - and the next start reads it (launcher_started)."""
    pairs = dict(_env_pairs(home, env), CRISSA_SERVICE="systemd")
    inline, secret = unit_env_split(pairs)
    envs = "".join("Environment=%s\n" % _sd_quote("%s=%s" % kv, exec_=False) for kv in inline.items())
    if secret and env_file is not None:
        envs += "EnvironmentFile=%s\n" % str(env_file).replace("%", "%%")
    return ("[Unit]\n"
            "Description=Crissa launcher: the player, the voice, the ear and the aligner (lib/crissa_service)\n"
            "After=pipewire-pulse.service\n"
            "StartLimitIntervalSec=600\n"
            "StartLimitBurst=5\n\n"
            "[Service]\n"
            "Type=simple\n"
            "WorkingDirectory=%s\n%s"            # a bare path: systemd takes no quotes here
            "ExecStart=%s\n"
            "Restart=on-failure\n"
            "RestartSec=5\n"
            "RestartPreventExitStatus=%s\n"
            "KillMode=mixed\n"
            "TimeoutStopSec=20\n"
            "ExecStopPost=/bin/sh -c %s\n\n"
            "[Install]\n"
            "WantedBy=default.target\n") % (str(ROOT).replace("%", "%%"), envs, " ".join(_sd_quote(a) for a in argv),
                                            " ".join(str(c) for c in NO_RETRY), _sd_quote(stop_record_cmd(home)))


def stop_record_cmd(home: Path | None = None) -> str:
    """The shell line ExecStopPost runs (systemd's quoting is added by _sd_quote): one line per end, never an error."""
    import shlex
    log = stops_log(home)
    return ('mkdir -p %s && echo "$(date +%%s) $SERVICE_RESULT $EXIT_CODE $EXIT_STATUS" >> %s || true'
            % (shlex.quote(str(log.parent)), shlex.quote(str(log))))


# START CRISSA WHEN I LOG IN is the person's choice (the User 2026-10-10, .101 finding L), asked on Init's last step and
# kept in <crissa home>/login.json {"on"}. The default is OFF: on .101 the installed service came back at every login -
# on that first run with the full-screen desk Wall - and on a computer someone uses every day an app that starts itself
# unasked is a surprise. Off, the service is still installed (it brings Crissa back after a crash, and a start by hand
# hands to it); it is only not started by the login. CRISSA_LOGIN=1|0 overrides the file.
LOGIN_FILE = "login.json"
LOGIN_DEFAULT = False
LOGIN_WHY = ("off unless you choose it: Crissa starts when you start her (python3 scripts/crissa.py, or the launcher "
             "icon); on, she also starts at each login")


def login_wanted(home: Path | None = None, env=None) -> bool:
    e = os.environ if env is None else env
    raw = str(e.get("CRISSA_LOGIN") or "").strip().lower()
    if raw in ("1", "on", "true", "yes"):
        return True
    if raw in ("0", "off", "false", "no"):
        return False
    got = _read(Path(home or HOME) / LOGIN_FILE)
    return bool(got.get("on")) if "on" in got else LOGIN_DEFAULT


def set_login(on: bool, *, home: Path | None = None, run: Runner = default_run, platform: str | None = None,
              env=None, by: str = "") -> dict:
    """Record the choice and apply it to the installed service now (systemd: enable / disable; launchd: RunAtLoad)."""
    home = Path(home or HOME)
    _write(home / LOGIN_FILE, {"on": bool(on), "by": str(by)[:20], "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    m = manager(platform)
    f = service_file(platform, env)
    out = {"ok": True, "on": bool(on), "manager": m, "installed": bool(f and f.exists())}
    if not out["installed"]:
        out["note"] = "kept; it applies when the service is installed (Init's last step installs it)"
        return out
    if m == "systemd":
        rc, so, se = run(_sd("enable" if on else "disable", UNIT), 30.0)
        if rc != 0:
            out.update(ok=False, error=(se or so).strip()[:200])
    elif m == "launchd":
        try:
            d = plistlib.loads(f.read_bytes())
            d["RunAtLoad"] = bool(on)
            f.write_bytes(plistlib.dumps(d))
        except (OSError, ValueError) as e:
            out.update(ok=False, error="%s: %s" % (type(e).__name__, e))
    out["note"] = ("Crissa starts at each login." if on else "Crissa does not start at login; start her when you want her.") \
        if out["ok"] else "The choice is kept, but the service did not take it: %s" % out.get("error")
    return out


def plist_bytes(argv: list, home: Path | None = None, env=None) -> bytes:
    pairs = dict(_env_pairs(home, env), CRISSA_SERVICE="launchd")
    logs = Path(home or HOME) / "logs"
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": [str(a) for a in argv],
                           "WorkingDirectory": str(ROOT), "EnvironmentVariables": pairs,
                           "RunAtLoad": login_wanted(home, env), "KeepAlive": {"SuccessfulExit": False},
                           "ThrottleInterval": 10,
                           "ProcessType": "Interactive",
                           "StandardOutPath": str(logs / "launcher-service.log"),
                           "StandardErrorPath": str(logs / "launcher-service.log")})


# ---- the launcher's own record ------------------------------------------------------------------------------
def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _runs_log(home: Path, line: str) -> None:
    f = Path(home) / "logs" / RUNS_LOG
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write("%s %s\n" % (_stamp(), line))


def launcher_started(home: Path | None = None, env=None, pid: int | None = None) -> dict:
    """At the launcher's start: count the run, carry the last run's end (or say it ended without a word)."""
    home = Path(home or HOME)
    old = _read(home / STATE)
    me = pid or os.getpid()
    if old.get("pid") and old.get("pid") != me and not old.get("ended_at") and _alive(old.get("pid")):
        # ANOTHER LAUNCHER IS RUNNING: its record stays; this one only notes that it tried (it will find the ports
        # taken and end 3), so the panel can say e.g. "the service tried to start while one ran by hand"
        _runs_log(home, "start (pid %d, %s) while pid %s (%s) runs: its record is kept"
                  % (me, under(env), old.get("pid"), old.get("under")))
        return {"shadow": True, "pid": me, "under": under(env), "held_by": old.get("pid")}
    prev = old.get("last") if old.get("ended_at") else None
    if old.get("pid") == me and not old.get("ended_at"):
        # the handoff already wrote this run's pid (claim_service_pid): it is this run, not one that ended without a
        # word (.101, 2026-10-10: every service start said so about itself)
        prev = old.get("previous")
    elif old.get("pid") and not old.get("ended_at"):
        stop = last_stop(home, float(old.get("started_t") or 0.0))
        prev = {"exit_code": None, "at": old.get("started_at"),
                "why": "the run before (pid %s, %s) %s" % (old.get("pid"), old.get("under") or "?", stop_words(stop)),
                "stop": stop}
    how = under(env)
    d = {"schema": "crissa-launcher/1", "pid": me, "under": how, "started_at": _stamp(), "started_t": time.time(),
         "runs": int(old.get("runs") or 0) + 1, "previous": prev, "invocation": (env or os.environ).get("INVOCATION_ID")}
    _write(home / STATE, d)
    _runs_log(home, "start (pid %d, %s, run %d)%s" % (d["pid"], how, d["runs"],
                                                       ("; the run before: " + prev["why"]) if prev else ""))
    return d


def last_stop(home: Path | None, since: float) -> dict | None:
    """The service manager's record of the latest end at or after `since` (epoch s): {"t", "result", "code",
    "status"}, or None (no record: not under systemd, or before this unit carried ExecStopPost)."""
    try:
        lines = stops_log(home).read_text(encoding="utf-8").splitlines()[-50:]
    except OSError:
        return None
    for ln in reversed(lines):
        parts = ln.split()
        try:
            t = float(parts[0])
        except (IndexError, ValueError):
            continue
        if since and t < since:
            return None
        return {"t": t, "result": parts[1] if len(parts) > 1 else "", "code": parts[2] if len(parts) > 2 else "",
                "status": parts[3] if len(parts) > 3 else ""}
    return None


DELIBERATE = ("TERM", "INT", "HUP", "0", "15", "2", "1")


def stop_words(stop: dict | None) -> str:
    """How the run before ended, in words: a stop on purpose is never worded as a crash."""
    if not stop:
        return ("ended without a word: killed (SIGKILL, out of memory) or the machine stopped (no service record of "
                "its end)")
    res, code, status = stop.get("result", ""), stop.get("code", ""), stop.get("status", "")
    if res == "oom-kill":
        return "was killed: out of memory"
    if res == "timeout":
        return "did not stop in time and was killed (SIGKILL)"
    if res == "success" or (code in ("killed", "exited") and status in DELIBERATE):
        return "was stopped on purpose (systemctl stop, or All OS Off)"
    if code == "killed" or res == "signal":
        return "was killed by a signal (%s)" % (status or "?")
    if code == "exited":
        return "exited with code %s" % (status or "?")
    return "ended (%s)" % " ".join(x for x in (res, code, status) if x)


def launcher_ended(code: int, why: str, home: Path | None = None, shadow: dict | None = None) -> dict:
    home = Path(home or HOME)
    d = _read(home / STATE)
    if shadow and shadow.get("shadow"):
        d["refused"] = {"pid": shadow.get("pid"), "under": shadow.get("under"), "exit_code": code, "at": _stamp(),
                        "why": why or "?"}
        _write(home / STATE, d)
        _runs_log(home, "end (pid %s, %s, exit %s) beside the running one: %s"
                  % (shadow.get("pid"), shadow.get("under"), code, why or "?"))
        return d
    d.update(ended_at=_stamp(), last={"exit_code": code, "why": why or ("stopped" if code == 0 else "?"),
                                      "at": _stamp()})
    _write(home / STATE, d)
    _runs_log(home, "end (pid %s, exit %s): %s" % (d.get("pid"), code, d["last"]["why"]))
    return d


def claim_service_pid(home: Path | None, pid: int, under_name: str = "systemd") -> dict:
    """The service's MainPID owns launcher.json. The hand process's later end must not stamp ended_at."""
    home = Path(home or HOME)
    d = _read(home / STATE)
    d["schema"] = "crissa-launcher/1"
    d["pid"] = int(pid)
    d["under"] = under_name
    d.pop("ended_at", None)
    d.setdefault("started_at", _stamp())
    d.setdefault("runs", int(d.get("runs") or 0) or 1)
    _write(home / STATE, d)
    return d


def main_pid(unit: str = UNIT, *, run: Runner = default_run) -> int:
    rc, so, _se = run(_sd("show", unit, "-p", "MainPID", "--value"), 10.0)
    if rc != 0 and not (so or "").strip():
        return 0
    try:
        return int((so or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def handoff_allowed(env=None, pytest_current=None) -> bool:
    """A hand start on Linux may hand the desk to crissa-launcher.service before any child binds.

    CRISSA_SERVICE_HANDOFF=0 keeps the hand start. A test never starts a real unit unless the flag is
    "test". The service process itself (CRISSA_SERVICE=systemd) does not hand off again.
    """
    env = os.environ if env is None else env
    if pytest_current is None:
        pytest_current = os.environ.get("PYTEST_CURRENT_TEST") or ""
    flag = str(env.get("CRISSA_SERVICE_HANDOFF") or "").strip()
    if flag == "0":
        return False
    if pytest_current and flag != "test":
        return False
    if str(env.get("CRISSA_SERVICE_INSTALL") or "1").strip() == "0":
        return False
    if under(env) != "hand":
        return False
    return manager() == "systemd"


def handoff_to_service(args, home: Path | None = None, env=None, *, run: Runner = default_run,
                       platform: str | None = None) -> dict:
    """Install and start the launcher unit. start=True: the hand process is already the live record,
    and start=None would see that and skip."""
    env = os.environ if env is None else env
    home = Path(home or HOME)
    out = install(list(args or []), home=home, start=True, run=run, platform=platform or "linux", env=env)
    pid = main_pid(run=run) if out.get("started") else 0
    if pid > 0:
        claim_service_pid(home, pid)
    return {"handed": bool(out.get("started")), "note": out.get("note") or "", "pid": pid, "ok": bool(out.get("ok"))}


def desk_units_on(env=None, pytest_current=None, platform: str | None = None) -> bool:
    """Each child is a user unit when this process is the service. CRISSA_DESK_UNITS=0 keeps process children.
    A test stays on processes unless the flag is "test". "1" follows the service manager."""
    env = os.environ if env is None else env
    if pytest_current is None:
        pytest_current = os.environ.get("PYTEST_CURRENT_TEST") or ""
    raw = env.get("CRISSA_DESK_UNITS")
    flag = "" if raw is None else str(raw).strip()
    if flag == "0":
        return False
    if flag == "test":
        return True
    if pytest_current:
        return False
    if flag == "1":
        return manager(platform) == "systemd"
    return under(env) == "systemd" and manager(platform) == "systemd"


def desk_unit_name(name: str) -> str:
    return "crissa-%s.service" % name


def stop_desk_units(names, *, run: Runner = default_run) -> list:
    """Stop leftover child units before a service start binds their ports.

    A launcher restart propagates to PartOf children, and a child still holding its port makes this
    start exit 3, which is not retried. Stopping a unit that is not installed is ignored.
    """
    calls = []
    for name in names:
        argv = ["systemctl", "--user", "stop", desk_unit_name(str(name))]
        try:
            run(argv, 20.0)
        except Exception:  # noqa: BLE001  a missing unit must not stop the desk from starting
            pass
        calls.append(argv)
    return calls


# A STOP OF EVERYTHING STAYS STOPPED (the User 2026-10-10, .101 round 5 HH: `systemctl --user stop 'crissa-*'` stopped
# tts at 22:17:00.07; the launcher's watch saw it down at .13 and ran `systemctl --user start crissa-tts`. Each child is
# After= the launcher, so that start job waited for the launcher's own stop job, the launcher sat inside it and was
# killed at its 20 s TimeoutStopSec, and the start then ran: tts came back at 22:17:20 with no launcher, and the next
# first boot refused its port). A stop of the launcher's unit - the glob, `systemctl --user stop crissa-launcher`, a
# restart of it - queues a job on that unit before any child stops (PartOf/After order the children's stops first),
# so while the launcher runs, a job on its own unit or its unit deactivating means everything is going down: nothing
# may be started again. Kill switch: CRISSA_STOP_ALL_HOLDS=0 (the watch restarts as before).
def going_down(run: Runner = default_run, unit: str = UNIT, env=None) -> bool:
    """True while the launcher's own unit has a job queued (a stop or a restart) or is deactivating."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_STOP_ALL_HOLDS") or "1").strip() == "0":
        return False
    try:
        rc, so, _se = run(_sd("show", unit, "-p", "Job", "-p", "ActiveState"), 10.0)
    except Exception:  # noqa: BLE001  a manager that cannot be asked is not a stop
        return False
    kv = {}
    for ln in (so or "").splitlines():
        k, sep, v = ln.partition("=")
        if sep:
            kv[k.strip()] = v.strip()
    job = kv.get("Job", "")
    return bool(job and not job.startswith("0")) or kv.get("ActiveState", "") == "deactivating"


# A FIRST BOOT TAKES BACK ITS OWN LEFTOVER (the User 2026-10-10, .101 round 5 HH: the next first boot found tts's port
# held by this install's own crissa-tts.service, left running with no launcher, and refused: "port taken, nothing
# started: tts 8189. Another install is running here"). A unit is this install's own when its file is the launcher's
# name for the lane (crissa-<lane>.service) in the person's unit folder and its WorkingDirectory is this tree; with no
# launcher of this install running, such a unit is a leftover, and the start stops it before it checks the ports. A
# port held by anything else is still refused, with what holds it. A test never asks the real manager unless
# CRISSA_DESK_UNITS=test.
LIVE_STATES = ("active", "activating", "reloading", "deactivating")


def unit_state(unit: str, run: Runner = default_run) -> str:
    try:
        _rc, so, _se = run(_sd("is-active", unit), 10.0)
    except Exception:  # noqa: BLE001
        return ""
    lines = (so or "").strip().splitlines()
    return lines[-1].strip() if lines else ""


def launcher_unit_running(run: Runner = default_run) -> bool:
    return unit_state(UNIT, run) in LIVE_STATES


def own_leftovers(names, *, run: Runner = default_run, env=None, root: Path | None = None,
                  platform: str | None = None, pytest_current=None) -> list:
    """The lanes among `names` whose crissa-<lane>.service is this tree's own unit and is running while no launcher
    unit runs. [] off systemd, under a test (unless CRISSA_DESK_UNITS=test), or while the launcher's unit runs."""
    e = os.environ if env is None else env
    if pytest_current is None:
        pytest_current = os.environ.get("PYTEST_CURRENT_TEST") or ""
    if manager(platform) != "systemd" or str(e.get("CRISSA_LEFTOVER_STOP") or "1").strip() == "0":
        return []
    if pytest_current and str(e.get("CRISSA_DESK_UNITS") or "").strip() != "test":
        return []
    here = Path(root or ROOT).resolve()
    mine = []
    for name in names:
        f = unit_dir(e) / desk_unit_name(str(name))
        try:
            cwd = read_unit(f.read_text(encoding="utf-8")).get("cwd") or ""
        except OSError:
            continue
        try:
            same = Path(cwd).resolve() == here
        except (OSError, ValueError):
            same = False
        if same:
            mine.append(str(name))
    if not mine or launcher_unit_running(run):
        return []
    return [n for n in mine if unit_state(desk_unit_name(n), run) in LIVE_STATES]


# THE PICTURE AND THE VOICE FIRST (round 13, 2026-10-12, .101): while she spoke, the speech recognition (asr 677 %,
# stt 200 %) and presence (192 %) took most of the 16 threads, the player's mouth composite went 14 -> 33-38 ms and lost
# 40-55 % of its rows, and the Wall fell to 20/s; paused, the Wall was a steady 24/s. Each lane now has a share: the
# player and the Wall paint her, her voice must keep real time, and the listeners and watchers yield when the CPU is
# full (CPUWeight= in service mode; a positive nice in both modes - a user's lanes cannot go below 0). Unlisted lanes
# keep systemd's 100 and nice 0. Kill: CRISSA_LANE_PRIORITY=0.
LANE_PRIORITY = {                       # lane: (nice, CPUWeight)
    "player": (0, 400), "wall": (0, 400),
    "pocket": (0, 300), "turbo": (0, 300), "multilingual": (0, 300), "audio8": (0, 300), "tts": (0, 300),
    "aligner": (0, 200),
    "asr": (10, 50), "presence": (10, 50), "oracle": (10, 50), "faceshell": (10, 50), "render": (10, 30),
    "enroll": (5, 50), "panel": (5, 50), "demo": (5, 50), "warm": (15, 20),
}


def _paint_cores_line(name: str, env=None) -> str:
    """Every lane on the rest of the machine; her paint threads move onto their reserved cores (lib/paint_cores)."""
    try:
        import paint_cores
        return paint_cores.unit_line(name, env)
    except Exception:  # noqa: BLE001 - no reservation, the unit as before
        return ""


def lane_priority_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LANE_PRIORITY", "1")).strip().lower() not in ("0", "off", "false", "no")


def lane_priority(name: str, env=None) -> "tuple[int, int] | None":
    """(nice, CPUWeight) for a lane, or None when it keeps the defaults (unlisted, or the switch is off)."""
    if not lane_priority_on(env):
        return None
    return LANE_PRIORITY.get(str(name))


# A TAKEN PORT IS NOT RETRIED EVERY 2 S (round 17, 2026-10-13, .101 08:28): crissa-aligner.service failed "Address
# already in use" every 2 s while the player's own aligner held :8195, 3,453 restarts in about 2 h. RestartSec=2 sits on
# systemd's default start limit (5 starts in 10 s, a fixed window from the first): any cycle longer than 2.0 s - the
# delay plus the time to fail, ~2.2 s there - never trips it, and the watch saw nothing (a unit waiting to restart reads
# "activating", alive, and /health answered from the other copy). The aligner now ends a taken port with 3
# (scripts/crissa-align.py) and its unit counts 3 as a clean end, so systemd does not start it again; the watch holds the
# lane while another copy serves its port (lib/crissa_watch "served"). Kill CRISSA_ALIGN_PORT_TAKEN_EXIT=0.
PORT_TAKEN_EXIT = 3
PORT_TAKEN_LANES = ("aligner",)


def port_taken_clean(name: str, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_ALIGN_PORT_TAKEN_EXIT", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    return str(name) in PORT_TAKEN_LANES


def desk_unit_text(name: str, argv: list, env: dict | None = None, home: Path | None = None,
                   env_file: Path | None = None, cwd: str | None = None) -> str:
    """One child, from its launch (child_launch): the same argv, working directory and environment as a process-mode
    child. PartOf the launcher so stopping that unit stops the desk. No install section: these are started by the
    launcher, not by the login. A secret-named variable is read from `env_file` (EnvironmentFile=), never written
    here; with secret-named variables and no file the unit is refused rather than started without them."""
    inline, secret = unit_env_split(env or {})
    if secret and env_file is None:
        raise ValueError("%s: %d secret-named variable(s) and no environment file for them" % (name, len(secret)))
    envs = "".join("Environment=%s\n" % _sd_quote("%s=%s" % kv, exec_=False) for kv in inline.items())
    if secret:
        envs += "EnvironmentFile=%s\n" % str(env_file).replace("%", "%%")
    log = str(Path(home or HOME) / "logs" / ("%s.log" % name)).replace("%", "%%")
    pri = lane_priority(name, env)
    prio = ("Nice=%d\nCPUWeight=%d\n" % pri) if pri else ""
    prio += _paint_cores_line(name, env)
    if port_taken_clean(name, env):
        prio += "SuccessExitStatus=%d\n" % PORT_TAKEN_EXIT
    return ("[Unit]\n"
            "Description=Crissa %s\n"
            "PartOf=%s\n"
            "After=%s\n\n"
            "[Service]\n"
            "Type=simple\n"
            "WorkingDirectory=%s\n"
            "%s"
            "ExecStart=%s\n"
            "Restart=on-failure\n"
            "RestartSec=2\n"
            "TimeoutStopSec=10\n"
            "%s"
            "StandardOutput=append:%s\n"
            "StandardError=append:%s\n"
            % (name, UNIT, UNIT, str(cwd or ROOT).replace("%", "%%"), envs,
               " ".join(_sd_quote(str(a)) for a in argv), prio, log, log))


def write_desk_unit(name: str, launch: dict, home: Path | None = None, units: Path | None = None) -> dict:
    """Write crissa-<name>.service (and its 0600 environment file when it needs one) from the child's launch.
    Returns {"unit": path, "env_file": path or None}."""
    unit = desk_unit_name(name)
    _inline, secret = unit_env_split(launch["env"])
    ef = None
    if secret:
        ef = write_env_file(env_file_path(home, unit), secret)
    else:
        try:
            env_file_path(home, unit).unlink()
        except OSError:
            pass
    path = Path(units or unit_dir()) / unit
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desk_unit_text(name, launch["argv"], launch["env"], home=home, env_file=ef, cwd=launch.get("cwd")),
                    encoding="utf-8")
    return {"unit": path, "env_file": ef}


# ---- reading a unit back (the parity test and the acceptance compare it with the process-mode launch) --------------
def _sd_words(text: str, exec_: bool = True) -> list:
    """The words of an ExecStart= (exec_) or Environment= line as systemd reads the quoting this module writes."""
    out, i, n = [], 0, len(text)
    while i < n:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        word, quoted = [], text[i] == '"'
        if quoted:
            i += 1
        while i < n:
            ch = text[i]
            if quoted and ch == '"':
                i += 1
                break
            if not quoted and ch in " \t":
                break
            if ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                word.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                i += 2
                continue
            if ch in ("%$" if exec_ else "%") and i + 1 < n and text[i + 1] == ch:
                word.append(ch)
                i += 2
                continue
            word.append(ch)
            i += 1
        out.append("".join(word))
    return out


def _env_file_read(text: str) -> dict:
    """systemd's EnvironmentFile rules for what env_file_text writes: KEY="value", a backslash escapes a double quote,
    a backslash, a backtick or a dollar."""
    out, i, n = {}, 0, len(text)
    while i < n:
        j = text.find("=", i)
        if j < 0:
            break
        key = text[i:j].strip()
        i = j + 1
        val = []
        if i < n and text[i] == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\`$':
                    val.append(text[i + 1])
                    i += 2
                    continue
                val.append(text[i])
                i += 1
            i += 1
        else:
            k = text.find("\n", i)
            k = n if k < 0 else k
            val.append(text[i:k])
            i = k
        while i < n and text[i] == "\n":
            i += 1
        out[key] = "".join(val)
    return out


def read_unit(text: str, env_file_text_: str | None = None) -> dict:
    """{"argv", "cwd", "env", "env_file"} as systemd would start this unit (its Environment= lines in order, then the
    EnvironmentFile's)."""
    argv, cwd, env, ef = [], None, {}, None
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            argv = _sd_words(line[len("ExecStart="):])
        elif line.startswith("WorkingDirectory="):
            cwd = line[len("WorkingDirectory="):].replace("%%", "%")
        elif line.startswith("Environment="):
            for w in _sd_words(line[len("Environment="):], exec_=False):
                k, _, v = w.partition("=")
                env[k] = v
        elif line.startswith("EnvironmentFile="):
            ef = line[len("EnvironmentFile="):].lstrip("-").replace("%%", "%")
    if ef is not None:
        if env_file_text_ is None:
            try:
                env_file_text_ = Path(ef).read_text(encoding="utf-8")
            except OSError:
                env_file_text_ = ""
        env.update(_env_file_read(env_file_text_))
    return {"argv": argv, "cwd": cwd, "env": env, "env_file": ef}


def launch_differences(process: dict, unit: dict) -> list:
    """Every way a unit would start a child differently from process mode, key by key. [] = the same launch. The keys
    systemd sets for each run are not compared (a unit cannot carry them)."""
    out = []
    if list(process.get("argv") or []) != list(unit.get("argv") or []):
        out.append("argv: process %r, unit %r" % (process.get("argv"), unit.get("argv")))
    if str(process.get("cwd") or "") != str(unit.get("cwd") or ""):
        out.append("cwd: process %r, unit %r" % (process.get("cwd"), unit.get("cwd")))
    pe = {k: v for k, v in (process.get("env") or {}).items() if k not in SYSTEMD_OWN}
    ue = {k: v for k, v in (unit.get("env") or {}).items() if k not in SYSTEMD_OWN}
    for k in sorted(set(pe) | set(ue)):
        if k not in ue:
            out.append("env %s: in process mode only" % k)
        elif k not in pe:
            out.append("env %s: in the unit only" % k)
        elif pe[k] != ue[k]:
            out.append("env %s: process %r, unit %r" % (k, pe[k], ue[k]))
    return out


def manager_unreachable(text: str) -> bool:
    """systemctl's words when there is no user manager to ask (no session bus): only then may a unit's child run by
    hand instead."""
    low = str(text or "").lower()
    return any(m in low for m in ("failed to connect to bus", "no medium found", "failed to connect to user scope bus",
                                  "system has not been booted with systemd", "connection refused"))


class UnitProc:
    """A child that is a user unit. poll() follows systemctl, so a restart by systemd stays one child."""

    def __init__(self, unit: str, run: Runner, pid: int = 0):
        self.unit = unit
        self._run = run
        self.pid = int(pid or 0)
        self.returncode = None
        self.stopped_clean = False

    def poll(self):
        rc, so, _se = self._run(["systemctl", "--user", "is-active", self.unit], 10.0)
        state = (so or "").strip().splitlines()[-1] if (so or "").strip() else ""
        # ONE UNIT OWNS EACH PORT (the User 2026-10-10, .101 finding I): `systemctl --user restart crissa-stt` passes
        # through "deactivating" and "activating"; read as a crash, the launcher stopped and started the unit over
        # systemd's own restart, its start lost the race, and a by-hand copy took the port - the unit then looped on
        # "port 8190 is already in use". A unit on its way down or up is the manager's business, not a crash.
        if state in ("active", "activating", "reloading", "deactivating") or (not state and rc == 0):
            self.pid = main_pid(self.unit, run=self._run) or self.pid
            self.returncode = None
            self.stopped_clean = False
            return None
        # A UNIT STOPPED ON PURPOSE IS NOT A CRASH (2026-10-10, .101): `is-active` answers 3 for any inactive unit, so
        # a `systemctl stop` read as exit 1 and the watch started the Wall again. systemd's own Result says which.
        res = ""
        if state == "inactive":
            rc2, so2, _se2 = self._run(["systemctl", "--user", "show", self.unit, "-p", "Result", "--value"], 10.0)
            res = (so2 or "").strip().splitlines()[-1] if (so2 or "").strip() else ""
        self.stopped_clean = state == "inactive" and res == "success"
        self.returncode = 0 if self.stopped_clean else (1 if state == "failed" or rc not in (0, None) else 0)
        return self.returncode

    def wait(self, timeout=None):
        end = time.time() + (30.0 if timeout is None else float(timeout))
        while time.time() < end:
            if self.poll() is not None:
                return self.returncode
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(self.unit, timeout)

    def stop_service(self) -> None:
        self._run(["systemctl", "--user", "stop", self.unit], 30.0)
        self.poll()


def service_rc(code: int, env=None) -> int:
    """launchd cannot be told "do not retry this code": a bad setup or a taken port ends 0 under it."""
    return 0 if under(env) == "launchd" and code in NO_RETRY else code


def read_launcher(home: Path | None = None) -> dict:
    d = _read(Path(home or HOME) / STATE)
    d["alive"] = bool(d.get("pid")) and not d.get("ended_at") and _alive(d.get("pid"))
    return d


# ---- install / uninstall / status ---------------------------------------------------------------------------
def _sd(*args: str) -> list:
    return ["systemctl", "--user", *args]


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


def install(args: list | None = None, *, home: Path | None = None, start: bool | None = None,
            run: Runner = default_run, platform: str | None = None, env=None, python: str | None = None) -> dict:
    """Write the unit (or the agent) and enable it for the login. start=None: start it now only when no launcher
    runs here (a running one holds the ports: the service would exit 3 and not be retried)."""
    m = manager(platform)
    f = service_file(platform, env)
    if m is None or f is None:
        return {"ok": False, "manager": None, "note": "no service manager for %s: start Crissa by hand "
                                                       "(python3 scripts/crissa.py)" % (platform or sys.platform)}
    home = Path(home or HOME)
    argv = launch_argv(list(args or []), python)
    if start is None:
        start = not read_launcher(home).get("alive")
    f.parent.mkdir(parents=True, exist_ok=True)
    out = {"ok": True, "manager": m, "file": str(f), "argv": argv, "started": False, "steps": []}
    if m == "systemd":
        # the person's environment crosses the handoff (carried_env); a secret-named one in a 0600 file (2026-10-10)
        _inline, secret = unit_env_split(dict(_env_pairs(home, env), CRISSA_SERVICE="systemd"))
        ef = write_env_file(env_file_path(home, UNIT), secret) if secret else None
        if ef is None:
            try:
                env_file_path(home, UNIT).unlink()
            except OSError:
                pass
        f.write_text(unit_text(argv, home, env, env_file=ef), encoding="utf-8")
        login = login_wanted(home, env)
        steps = ([_sd("daemon-reload"), _sd("enable" if login else "disable", UNIT)]
                 + ([_sd("start", UNIT)] if start else []))
    else:
        (home / "logs").mkdir(parents=True, exist_ok=True)
        f.write_bytes(plist_bytes(argv, home, env))
        try:
            os.chmod(f, 0o600)              # it may hold a secret-named variable: the person's eyes only
        except OSError:
            pass
        steps = ([["launchctl", "bootstrap", "gui/%d" % _uid(), str(f)]] if start else [])
        if start and not login_wanted(home, env):     # RunAtLoad off: the bootstrap loads it, kickstart runs it now
            steps.append(["launchctl", "kickstart", "gui/%d/%s" % (_uid(), LABEL)])
    for argv_ in steps:
        rc, so, se = run(argv_, 30.0)
        out["steps"].append({"run": " ".join(argv_), "rc": rc, "error": (se or so).strip()[:200] if rc else ""})
        if rc != 0:
            out["ok"] = False
            break
    out["started"] = bool(start) and out["ok"]
    if not out["ok"]:
        out["note"] = "written to %s, but %s failed: %s" % (f, out["steps"][-1]["run"], out["steps"][-1]["error"])
    elif out["started"]:
        out["note"] = "installed and started (%s); it comes back after a failure%s" % (
            m, " and at each login" if login_wanted(home, env) else " (not at login: your choice on Init's last step)")
    else:
        out["note"] = ("installed (%s), %s; this launcher was started by hand, so the service takes over at its next "
                       "start, or now with Restart on the launcher tile in the Control Panel"
                       % (m, "started at each login" if login_wanted(home, env) else "not started at login"))
    out["login"] = login_wanted(home, env)
    return out


def uninstall(*, run: Runner = default_run, platform: str | None = None, env=None, home: Path | None = None) -> dict:
    """Stop it, disable it, remove its file. Crissa's home (her settings, logs, memory) is left where it is."""
    m = manager(platform)
    f = service_file(platform, env)
    if m is None or f is None:
        return {"ok": True, "manager": None, "removed": False, "note": "no service manager here: nothing to remove"}
    out = {"ok": True, "manager": m, "file": str(f), "removed": False, "steps": []}
    if m == "systemd":
        steps = [_sd("disable", "--now", UNIT)] if f.exists() else []
    else:
        steps = [["launchctl", "bootout", "gui/%d/%s" % (_uid(), LABEL)]] if f.exists() else []
    for argv_ in steps:
        rc, so, se = run(argv_, 40.0)
        out["steps"].append({"run": " ".join(argv_), "rc": rc, "error": (se or so).strip()[:200] if rc else ""})
    if f.exists():
        f.unlink()
        out["removed"] = True
    if m == "systemd":
        out["pieces"] = _remove_own_desk_units(run, env)
    out["dock"] = _remove_dock(env, home)
    if m == "systemd":
        rc, so, se = run(_sd("daemon-reload"), 30.0)
        out["steps"].append({"run": "systemctl --user daemon-reload", "rc": rc, "error": (se or so).strip()[:200] if rc else ""})
    out["note"] = ("removed %s and %d piece service(s)%s; Crissa's home %s is kept"
                   % (f, len(out.get("pieces") or []), ", the dock entry" if (out.get("dock") or {}).get("removed") else "",
                      HOME)) if out["removed"] else "no launcher service was installed (%s)" % f
    return out


# UNINSTALL TAKES ALL OF IT (the User 2026-10-25, the 1.0.0 stranger install: one start wrote eighteen units, a launcher
# entry and a dock favourite outside the install folder and ~/.crissa, and --uninstall removed one of them). Every
# crissa-<piece>.service whose WorkingDirectory is THIS install (read_unit's cwd, the rule own_leftovers uses) is stopped
# and removed, with its 0600 environment file; another install's units are never touched. The dock entry, its icon and
# the favourite go through crissa_window.remove_dock, which unpins only what Crissa pinned. Her home stays.
def _remove_own_desk_units(run: Runner, env=None) -> list:
    e = os.environ if env is None else env
    here = Path(ROOT).resolve()
    gone = []
    d = unit_dir(e)
    for f in sorted(d.glob(desk_unit_name("*"))) if d.is_dir() else []:
        try:
            cwd = read_unit(f.read_text(encoding="utf-8")).get("cwd") or ""
            if Path(cwd).resolve() != here:
                continue
        except (OSError, ValueError):
            continue
        run(_sd("disable", "--now", f.name), 40.0)
        try:
            env_file_path(None, f.name).unlink()
        except OSError:
            pass
        try:
            f.unlink()
            gone.append(f.name)
        except OSError:
            pass
    return gone


def _remove_dock(env=None, home: Path | None = None) -> dict:
    try:
        import crissa_window
        return crissa_window.remove_dock(home=home, env=env)
    except Exception as ex:  # noqa: BLE001  no desktop here, or no dock: nothing to remove
        return {"ok": False, "error": str(ex)[:120]}


def status(*, home: Path | None = None, run: Runner = default_run, platform: str | None = None, env=None) -> dict:
    """installed, enabled, state, restarts the manager counted, its own result, and the launcher's record."""
    m = manager(platform)
    f = service_file(platform, env)
    out = {"manager": m, "file": str(f) if f else None, "installed": bool(f and f.exists()), "enabled": None,
           "active": "", "sub": "", "restarts": 0, "result": "", "exit_status": None, "reachable": True,
           "launcher": read_launcher(home), "unit": UNIT if m == "systemd" else (LABEL if m == "launchd" else None)}
    if not out["installed"]:
        return out
    if m == "systemd":
        rc, so, se = run(_sd("show", UNIT, "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts", "-p", "Result",
                             "-p", "ExecMainStatus", "-p", "UnitFileState"), 10.0)
        if rc != 0 and not so:
            out.update(reachable=False, error=((se or "").strip().splitlines() or ["?"])[-1][:160])
            return out
        kv = dict(ln.split("=", 1) for ln in so.splitlines() if "=" in ln)
        try:
            n = int(kv.get("NRestarts") or 0)
        except ValueError:
            n = 0
        try:
            ex = int(kv.get("ExecMainStatus") or 0)
        except ValueError:
            ex = None
        out.update(active=kv.get("ActiveState", ""), sub=kv.get("SubState", ""), restarts=n,
                   result=kv.get("Result", ""), exit_status=ex,
                   enabled=kv.get("UnitFileState", "") in ("enabled", "enabled-runtime", "linked"))
    else:
        rc, so, se = run(["launchctl", "print", "gui/%d/%s" % (_uid(), LABEL)], 10.0)
        if rc != 0:
            out.update(active="inactive", sub="not loaded", enabled=True)
            return out
        kv = {}
        for ln in so.splitlines():
            if " = " in ln:
                k, v = ln.strip().split(" = ", 1)
                kv.setdefault(k.strip(), v.strip())
        state = kv.get("state", "")
        try:
            runs = int(kv.get("runs") or 0)
        except ValueError:
            runs = 0
        try:
            ex = int(kv.get("last exit code") or 0)
        except ValueError:
            ex = None
        out.update(active="active" if state == "running" else "inactive", sub=state, restarts=max(0, runs - 1),
                   result="" if not ex else "exit-code", exit_status=ex, enabled=True)
    return out


def describe(st: dict) -> "tuple[str, str]":
    """(health, note) for the Control Panel's launcher tile."""
    L = st.get("launcher") or {}
    prev = (L.get("previous") or {}) if L.get("alive") else (L.get("last") or {})
    last = ""
    if prev.get("why"):
        last = "%s (exit %s, %s)" % (prev.get("why"), prev.get("exit_code"), prev.get("at") or "?")
    m = st.get("manager")
    if not m:
        return ("warn" if L.get("alive") else "bad",
                "no service manager on this machine: if the launcher stops, start it again by hand (python3 scripts/crissa.py)")
    if not st.get("installed"):
        lead = ("running (started by hand, pid %s), " % L.get("pid")) if L.get("alive") else "not running, "
        return ("warn" if L.get("alive") else "bad",
                lead + "and not installed as a service: if its player dies, nothing brings Crissa back - "
                "install it: python3 scripts/crissa.py --service install" + ("; last: " + last if last else ""))
    if not st.get("reachable", True):
        return "warn", "%s unreachable (%s); the launcher's own record: %s" % (m, st.get("error") or "?", last or "none")
    active = st.get("active")
    if active == "active" and L.get("under") == m:
        n = int(st.get("restarts") or 0)
        if n or (L.get("previous") or {}).get("exit_code") not in (None, 0) or (L.get("previous") or {}).get("why", "").startswith("the run before"):
            return "warn", "running under %s; restarted by it ×%d - last: %s" % (m, n, last or "?")
        return "ok", "running under %s (comes back after a failure and at each login)" % m
    if active == "activating":
        return "warn", "%s is starting it again (restart %s) - last: %s" % (m, st.get("restarts"), last or "?")
    if L.get("alive") and L.get("under") == "hand":
        ref = L.get("refused") or {}
        tried = ("; the service tried to start at %s and found: %s" % (ref.get("at"), ref.get("why"))) if ref else ""
        return "warn", ("this launcher was started by hand (pid %s), so %s does not bring it back - press Restart to "
                        "run it under the service%s" % (L.get("pid"), m, tried))
    if active == "failed":
        return "bad", "failed (%s, exit %s): %s stopped retrying - last: %s - press Start" % (
            st.get("result") or "?", st.get("exit_status"), m, last or "?")
    if (L.get("last") or {}).get("exit_code") in NO_RETRY or str((L.get("last") or {}).get("why", "")).startswith("port taken"):
        return "bad", "not running and not retried (a retry cannot fix it): %s - press Start once it is fixed" % (last or "?")
    return ("bad" if st.get("enabled") else "warn",
            "installed, not running%s - press Start; last: %s" % ("" if st.get("enabled") else " and not enabled", last or "none"))


# ---- the Control Panel's keys ----------------------------------------------------------------------------------
def _cmdline(pid: int) -> str:
    try:
        return Path("/proc/%d/cmdline" % pid).read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        rc, so, _ = default_run(["ps", "-o", "command=", "-p", str(pid)], 5.0)
        return so if rc == 0 else ""


def stop_hand(home: Path | None = None, wait: float = 20.0, *, kill=os.kill, cmdline=_cmdline,
              sleep=time.sleep) -> dict:
    """Stop a launcher started by hand (SIGTERM: it stops its lanes and records why), so the service can take over.
    Only a pid whose command line is this launcher."""
    L = read_launcher(home)
    pid = L.get("pid")
    if not L.get("alive") or L.get("under") != "hand":
        return {"ok": True, "stopped": False, "note": "no launcher started by hand is running"}
    if "crissa.py" not in cmdline(pid):
        return {"ok": False, "stopped": False, "note": "pid %s is not the launcher any more; nothing stopped" % pid}
    import signal
    kill(pid, signal.SIGTERM)
    end = time.time() + wait
    while time.time() < end:
        if not _alive(pid):
            return {"ok": True, "stopped": True, "pid": pid}
        sleep(0.25)
    return {"ok": False, "stopped": False, "pid": pid, "note": "the launcher (pid %s) did not stop in %.0f s" % (pid, wait)}


def control(action: str, *, home: Path | None = None, run: Runner = default_run, platform: str | None = None,
            env=None, stop_hand_fn=None) -> dict:
    """restart | start | stop the launcher from the Control Panel. Restart while a launcher runs by hand stops that
    one and starts the service (it takes over); without a service installed, start and restart say how to install."""
    m = manager(platform)
    st = status(home=home, run=run, platform=platform, env=env)
    L = st.get("launcher") or {}
    if action not in ("restart", "start", "stop"):
        return {"ok": False, "note": "unknown action %r" % action}
    if action == "stop" and L.get("alive") and L.get("under") == "hand":
        got = (stop_hand_fn or stop_hand)(home)
        return dict(got, note=got.get("note") or "stopped the launcher started by hand (pid %s)" % L.get("pid"))
    if not st.get("installed"):
        return {"ok": False, "note": "the launcher is not installed as a service: python3 scripts/crissa.py --service install"}
    steps = []
    if action in ("restart", "start") and L.get("alive") and L.get("under") == "hand":
        got = (stop_hand_fn or stop_hand)(home)
        steps.append({"run": "stop the launcher started by hand (pid %s)" % L.get("pid"), "rc": 0 if got.get("ok") else 1,
                      "error": got.get("note") or ""})
        if not got.get("ok"):
            return {"ok": False, "steps": steps, "note": got.get("note")}
        action = "start"
    if m == "systemd":
        argv = _sd(action, UNIT)
    else:
        target = "gui/%d/%s" % (_uid(), LABEL)
        if action == "stop":
            argv = ["launchctl", "kill", "SIGTERM", target]
        elif st.get("sub") == "not loaded":
            argv = ["launchctl", "bootstrap", "gui/%d" % _uid(), st["file"]]
        else:
            argv = ["launchctl", "kickstart"] + (["-k"] if action == "restart" else []) + [target]
    rc, so, se = run(argv, 60.0)
    steps.append({"run": " ".join(argv), "rc": rc, "error": (se or so).strip()[:200] if rc else ""})
    return {"ok": rc == 0, "steps": steps, "note": ("%s: %s" % (" ".join(argv), "done" if rc == 0 else steps[-1]["error"]))}


def journal(home: Path | None = None, lines: int = 80, *, run: Runner = default_run, platform: str | None = None,
            env=None) -> list:
    """logs/launcher.log (each run's start and end with why), then the service manager's own lines."""
    out = []
    try:
        out = (Path(home or HOME) / "logs" / RUNS_LOG).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        pass
    f = service_file(platform, env)
    if manager(platform) == "systemd" and f is not None and f.exists():
        rc, so, se = run(["journalctl", "--user", "-u", UNIT, "-n", str(lines), "--no-pager", "-o", "short-iso"], 15.0)
        out += ["--- journalctl --user -u %s" % UNIT] + (so.splitlines() if rc == 0 else [(se or "").strip()[:200]])
    return out
