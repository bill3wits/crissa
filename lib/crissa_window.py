"""HER OWN WINDOW (the User 2026-10-11): Crissa's own browser instance, never a tab in the person's browser.

The rule, as the User put it and confirmed:
  * A Crissa icon in the dock starts HER OWN browser instance: her own profile under the state home, a normal window
    (never maximised, never full screen), showing her Wall page (/wall).
  * Her Setup (/init) opens in that window and comes to the front. It is never a tab in the person's own browser:
    on .101 the kit opened /init as a tab in the Brave window already there, GNOME did not raise it, and the person
    met the desk Wall's own Setup plate instead (JJ).
  * With the desk Wall on, her window is optional: never forced open, never closed by Crissa. With the desk Wall off,
    her browser Wall stays open somewhere, in a normal window (ensure_wall).

WHICH BROWSER: Brave if installed, else Chromium, else Google Chrome (CRISSA_BROWSER names another binary of the
Chromium family). Each runs as its own instance with --user-data-dir = her profile, so it never shares a window, a
tab or a sign-in with the person's own browser of the same make. On Linux the window is named --class=Crissa (X11;
the dock entry's StartupWMClass matches it, so GNOME files the window under the Crissa icon) and the browser runs on
X11 (Xwayland in a Wayland session), where the house measured that Page.bringToFront raises a window (2026-09-30);
a launcher whose unit has no DISPLAY takes the session's own, so the AR key can find her window (session_x11,
2026-10-12). A
snap browser keeps its profile under ~/snap/<name>/common (a snap cannot write a hidden folder in the home). With
Firefox only, nothing is opened: no_browser_message() says so plainly, and the desk Wall's own Setup plates carry
the walk (Firefox has no debugging port Crissa can raise a window with, and the person's Firefox is theirs).

HOW IT IS DRIVEN: the browser's DevTools port on loopback (the port table's `browser`, 8183, moved by the offset),
read over HTTP (/json/version, /json/list) and a minimal WebSocket client below (no package): Page.navigate moves her
Wall page to Setup, Browser.setWindowBounds {windowState: normal} restores a minimised window (only then), and
Page.bringToFront raises it. Nothing here resizes a window the person sized, maximises one or makes one full screen;
after OUR launch a window the window manager maximised is set back to normal once. Her pages only: a page is hers when
it has the player's origin.

A started browser leaves this process's group and, under a systemd unit, its cgroup too (systemd-run --user --scope),
so stopping Crissa, the Wall or the player never closes her window. Her browser's output: <state home>/logs/window.log.

Switches: CRISSA_OWN_WINDOW=0 = the old behaviour (Setup in the person's default browser, the launcher's open_page);
CRISSA_WINDOW_QUIET=0 drops the quiet flags (Brave's own background traffic off, the set lib/brave_quiet measured).
Never on the house (crissa_home.is_house): the house has its own ZOS Brave (lib/zos_brave). A test never starts a
real browser: show() and ensure_wall() refuse under pytest unless a test hands its own popen.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_egress  # noqa: E402  every open goes through the one switch (loopback here, still routed)
import crissa_home  # noqa: E402
import crissa_ports  # noqa: E402

SWITCH = "CRISSA_OWN_WINDOW"
QUIET_SWITCH = "CRISSA_WINDOW_QUIET"
WM_CLASS = "Crissa"                      # X11 WM_CLASS of her window; the dock entry's StartupWMClass
PROFILE_DIR = "browser"                  # <state home>/browser
SNAP_PROFILE = "crissa-browser"          # ~/snap/<name>/common/crissa-browser for a snap browser
LOG = "window.log"
LAUNCH_WAIT_S = 25.0                     # how long a launch waits for the debugging port
PAGE_WAIT_S = 8.0                        # ... and then for her page to exist
STATE_CACHE_S = 2.0
DEFAULT_SIZE = (1280, 800)
SIZE_OF_SCREEN = 0.72                    # her window's share of the screen: under GNOME's auto-maximise size

# Chromium-family browsers, in the order the User named them (Brave, else Chromium, else Google Chrome).
LINUX = (("brave", ("brave-browser", "brave-browser-stable", "brave")),
         ("chromium", ("chromium", "chromium-browser")),
         ("chrome", ("google-chrome", "google-chrome-stable")))
MAC = (("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
       ("chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
       ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
NAMES = {"brave": "Brave", "chromium": "Chromium", "chrome": "Google Chrome", "other": "the named browser"}
# Brave's own background traffic off: the set lib/brave_quiet measured on 2026-10-02 (59 requests in 5 minutes to
# Brave's own services with the chip flags, none with these). Copied, not imported: brave_quiet ships with the Wall,
# and this module is the core's.
QUIET_FLAGS = ("--disable-background-networking", "--disable-component-update", "--disable-domain-reliability",
               "--no-pings", "--disable-sync", "--disable-breakpad", "--metrics-recording-only")
QUIET_PREFS = {"browser": {"enable_spellchecking": False},
               "spellcheck": {"dictionaries": [], "use_spelling_service": False},
               "safebrowsing": {"enabled": False, "enhanced": False},
               "brave": {"stats": {"reporting_enabled": False}}}
QUIET_LOCAL_STATE = {"brave": {"p3a": {"enabled": False}, "stats": {"reporting_enabled": False}},
                     "user_experience_metrics": {"reporting_enabled": False}}

_CACHE: dict = {"at": 0.0, "key": None, "val": None}


def _off(v) -> bool:
    return str(v or "").strip().lower() in ("0", "off", "false", "no")


def enabled(env: dict | None = None) -> bool:
    """Her own window is how a pack opens her pages: on unless CRISSA_OWN_WINDOW=0; never on the house."""
    e = os.environ if env is None else env
    if crissa_home.is_house():
        return False
    return not _off(e.get(SWITCH, "1"))


def _in_test(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or e.get("PYTEST_CURRENT_TEST"))


def port(env: dict | None = None) -> int:
    return crissa_ports.port("browser", env)


def _home(home: Path | None = None) -> Path:
    return Path(home or crissa_home.HOME)


# ── which browser ─────────────────────────────────────────────────────────────────────────────────────────────────────
def find(env: dict | None = None, which=None, plat: str | None = None, isfile=None) -> dict | None:
    """{"kind", "path", "snap"} for the first Chromium-family browser here (Brave, Chromium, Google Chrome), or None.
    CRISSA_BROWSER names a binary (a path or a command) and wins."""
    e = os.environ if env is None else env
    which = which or shutil.which
    isfile = isfile or os.path.isfile
    plat = plat or sys.platform
    named = str(e.get("CRISSA_BROWSER") or "").strip()
    if named:
        path = named if isfile(named) else which(named)
        if path:
            return {"kind": _kind_of(path), "path": path, "snap": _is_snap(path)}
        return None
    if plat == "darwin":
        for kind, path in MAC:
            for p in (path, str(Path.home()) + path):
                if isfile(p):
                    return {"kind": kind, "path": p, "snap": False}
        return None
    for kind, cmds in LINUX:
        for c in cmds:
            path = which(c)
            if path:
                return {"kind": kind, "path": path, "snap": _is_snap(path)}
    return None


def _kind_of(path: str) -> str:
    low = Path(path).name.lower()
    for kind in ("brave", "chromium", "chrome"):
        if kind in low:
            return kind
    return "other"


def _is_snap(path: str) -> bool:
    try:
        real = os.path.realpath(path)
    except OSError:
        real = path
    return str(path).startswith("/snap/") or real.startswith("/snap/") or real.endswith("/usr/bin/snap")


def firefox_here(which=None, plat: str | None = None, isfile=None) -> bool:
    which = which or shutil.which
    isfile = isfile or os.path.isfile
    if (plat or sys.platform) == "darwin":
        return isfile("/Applications/Firefox.app/Contents/MacOS/firefox")
    return bool(which("firefox") or which("firefox-esr"))


def no_browser_message(url: str, which=None, plat: str | None = None, isfile=None) -> str:
    """What the person is told when no Chromium-family browser is here: plainly, with what to do."""
    if firefox_here(which, plat, isfile):
        return ("Crissa opens her own window in Brave, Chromium or Google Chrome, and this computer has only Firefox. "
                "She never opens Setup as a tab in your own browser, so Setup runs on her desk Wall here. To give her "
                "a window of her own, install one of those three; or open %s in Firefox yourself." % url)
    return ("Crissa opens her own window in Brave, Chromium or Google Chrome, and none of them is on this computer. "
            "Install one of them to give her a window of her own; until then her desk Wall carries Setup, or open %s "
            "in any web browser yourself." % url)


def profile_dir(b: dict | None = None, home: Path | None = None) -> Path:
    """Her profile: <state home>/browser; a snap browser's under ~/snap/<name>/common (a snap cannot write a hidden
    folder in the home)."""
    if b and b.get("snap"):
        name = Path(str(b.get("path") or "")).name or "chromium"
        name = {"brave-browser": "brave", "chromium-browser": "chromium"}.get(name, name)
        return Path.home() / "snap" / name / "common" / SNAP_PROFILE
    return _home(home) / PROFILE_DIR


def has_screen(env: dict | None = None, plat: str | None = None) -> bool:
    e = os.environ if env is None else env
    plat = plat or sys.platform
    if plat == "darwin" or plat.startswith("win"):
        return True
    return bool(str(e.get("DISPLAY") or "").strip() or str(e.get("WAYLAND_DISPLAY") or "").strip())


def screen_size(run=None, env: dict | None = None) -> tuple[int, int] | None:
    """The screen's size from xrandr (X11 / Xwayland), or None. `env`: the session's display (session_x11)."""
    run = run or subprocess.run
    try:
        r = run(["xrandr", "--current"], capture_output=True, text=True, timeout=2, **({"env": dict(env)} if env else {}))
    except (OSError, subprocess.SubprocessError):
        return None
    import re
    m = re.search(r"current (\d+) x (\d+)", str(getattr(r, "stdout", "") or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def window_size(screen: tuple[int, int] | None = None) -> tuple[int, int]:
    """A normal window, under the size GNOME maximises on its own: 72 % of the screen, at most 1280 x 800."""
    if not screen:
        return DEFAULT_SIZE
    w, h = screen
    return (max(640, min(DEFAULT_SIZE[0], int(w * SIZE_OF_SCREEN))), max(480, min(DEFAULT_SIZE[1], int(h * SIZE_OF_SCREEN))))


def quiet_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return not _off(e.get(QUIET_SWITCH, "1"))


XWAYLAND_SWITCH = "CRISSA_WINDOW_XWAYLAND"
X11_SOCKETS = "/tmp/.X11-unix"


def _session_env_text() -> str:
    """The login session's environment as the user's systemd manager holds it now (GNOME puts DISPLAY, XAUTHORITY and
    WAYLAND_DISPLAY there once its Xwayland is ready), or "" when it cannot be read."""
    try:
        r = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return str(r.stdout or "") if r.returncode == 0 else ""


def session_x11(env: dict | None = None, plat: str | None = None, read=None, exists=None) -> "tuple[dict, str]":
    """HER WINDOW RUNS ON XWAYLAND IN A WAYLAND SESSION, EVEN FROM A UNIT THAT HAS NO DISPLAY (.101, 2026-10-12: "AR on
    the wall page not working here"). argv() asks for X11 only when DISPLAY is in the launching environment; a launcher
    whose unit started before the desktop exported DISPLAY started her as a native Wayland window, which no X11 read
    can see, so the feed's kiosk-bounds found no window and the AR key stayed grey. Returns (what to add to the
    environment, why): the session's own DISPLAY (":N" with its socket present) and XAUTHORITY (a file that exists),
    taken only from the user manager's environment, never guessed; {} when this process has DISPLAY already, the
    session is not Wayland, or the session names no X display (then she stays a Wayland window and the reason is
    logged). Kill: CRISSA_WINDOW_XWAYLAND=0."""
    e = os.environ if env is None else env
    plat = plat or sys.platform
    if not plat.startswith("linux"):
        return {}, "not Linux"
    if _off(e.get(XWAYLAND_SWITCH, "1")):
        return {}, "off (%s=0)" % XWAYLAND_SWITCH
    if str(e.get("DISPLAY") or "").strip():
        return {}, "DISPLAY %s" % str(e.get("DISPLAY")).strip()
    if read is None and _in_test(e):
        return {}, "test"
    exists = exists or os.path.exists
    got: dict = {}
    for line in str((read or _session_env_text)() or "").splitlines():
        k, sep, v = line.partition("=")
        if sep and k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
            got[k] = v.strip()
    wayland = (str(e.get("WAYLAND_DISPLAY") or "").strip() or got.get("WAYLAND_DISPLAY")
               or str(e.get("XDG_SESSION_TYPE") or got.get("XDG_SESSION_TYPE") or "").strip().lower() == "wayland")
    if not wayland:
        return {}, "no DISPLAY and not a Wayland session"
    import re
    d = got.get("DISPLAY", "")
    m = re.fullmatch(r":(\d+)(?:\.\d+)?", d)
    if not m:
        return {}, "the Wayland session names no X display (no Xwayland): her window stays a Wayland window, which the AR key cannot follow"
    if not exists("%s/X%s" % (X11_SOCKETS, m.group(1))):
        return {}, "the session's X display %s has no socket: her window stays a Wayland window" % d
    add = {"DISPLAY": d}
    xa = got.get("XAUTHORITY", "")
    if xa and not str(e.get("XAUTHORITY") or "").strip() and exists(xa):
        add["XAUTHORITY"] = xa
    return add, "on Xwayland %s, from the session" % d


def _note(home: Path | None, text: str) -> None:
    try:
        logs = _home(home) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / LOG, "a", encoding="utf-8") as f:
            f.write("%s her window: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except OSError:
        pass


def argv(b: dict, url: str, *, port_: int, profile: Path, plat: str | None = None, env: dict | None = None,
         size: tuple[int, int] | None = None) -> list[str]:
    """The command that starts her own instance on `url`: her profile, the debugging port on loopback, a normal
    window of `size` named Crissa, an app window (no tabs, no address bar)."""
    e = os.environ if env is None else env
    plat = plat or sys.platform
    w, h = size or DEFAULT_SIZE
    out = [str(b["path"]), "--user-data-dir=%s" % profile, "--remote-debugging-port=%d" % int(port_),
           "--no-first-run", "--no-default-browser-check", "--password-store=basic",
           "--autoplay-policy=no-user-gesture-required",
           # her Wall is a live picture: never frozen as a covered or background window (the house's 2026-09-01 law)
           "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
           "--disable-background-timer-throttling"]
    if plat.startswith("linux"):
        out += ["--class=%s" % WM_CLASS, "--name=%s" % WM_CLASS]
        if str(e.get("DISPLAY") or "").strip():
            out.append("--ozone-platform=x11")
    if quiet_on(e):
        out += list(QUIET_FLAGS)
    out += ["--window-size=%d,%d" % (int(w), int(h)), "--app=%s" % url]
    return out


def _merge(base: dict, add: dict) -> dict:
    for k, v in add.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def prepare_profile(profile: Path, env: dict | None = None) -> bool:
    """A new profile starts quiet (its preferences written before its first start; a running browser writes its own
    back). An existing profile is left as it is. True when it was made now."""
    if profile.exists():
        return False
    profile.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile, 0o700)
    except OSError:
        pass
    if quiet_on(env):
        (profile / "Default").mkdir(parents=True, exist_ok=True)
        (profile / "Default" / "Preferences").write_text(json.dumps(_merge({}, QUIET_PREFS)), encoding="utf-8")
        (profile / "Local State").write_text(json.dumps(_merge({}, QUIET_LOCAL_STATE)), encoding="utf-8")
    return True


def child_env(env: dict | None = None) -> dict:
    """The browser's environment: the person's, less what the player's own packages put in it (OpenCV prepends its
    library folder and sets Qt's), which a browser must not load."""
    e = dict(os.environ if env is None else env)
    for k in [k for k in e if k.startswith("QT_") and ("cv2" in str(e[k]) or "site-packages" in str(e[k]))]:
        e.pop(k, None)
    ld = [p for p in str(e.get("LD_LIBRARY_PATH") or "").split(os.pathsep)
          if p and "cv2" not in p and "site-packages" not in p]
    if ld:
        e["LD_LIBRARY_PATH"] = os.pathsep.join(ld)
    else:
        e.pop("LD_LIBRARY_PATH", None)
    for k in ("PYTEST_CURRENT_TEST", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        e.pop(k, None)
    try:                                     # .101 round 30: her page's sound is the echo canceller's reference
        import audio_sink
        sink = audio_sink.her_window_sink()
        if sink:
            e["PULSE_SINK"] = sink
    except Exception:  # noqa: BLE001  no canceller: the default output, as before
        pass
    return e


def keep_her_sound(log=print) -> bool:
    """HER WINDOW'S SOUND ON THE ECHO CANCELLER (.101 round 30, the User 2026-10-18: "sound bar moves for lila voice
    ... Should hers be ech cancelled?"). Her page plays Lila's films and lines; on the raw speaker the canceller never
    had them as its reference, so the ear and Setup's meter heard Lila as the person. PULSE_SINK (child_env) places a
    window opened after the canceller loaded; this thread moves any stream of her instance's (her_procs: her profile
    only, never the person's own browser) onto the canceller's sink as the sound server announces it, and again
    whenever the canceller reloads. Kill CRISSA_WINDOW_SOUND_AEC=0."""
    import shutil
    import subprocess
    import audio_sink
    if not sys.platform.startswith("linux") or not shutil.which("pactl") \
            or os.environ.get("CRISSA_WINDOW_SOUND_AEC", "1").strip().lower() in ("0", "off", "false", "no"):
        return False

    def once() -> None:
        sink = audio_sink.her_window_sink()
        if not sink:
            return
        pids = {int(r["pid"]) for r in her_procs() if r.get("pid")}
        moved = audio_sink.move_inputs_of(pids, sink)
        if moved:
            log("her window's sound: %d stream(s) moved onto %s (the echo canceller's reference)" % (len(moved), sink))

    def loop() -> None:
        while True:
            try:
                once()
                proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True)
                for ln in proc.stdout:            # "Event 'new' on sink-input #N", "... on sink #N" (a reload)
                    if "'new'" in ln and (" sink-input " in ln or " sink " in ln):
                        once()
            except Exception as e:  # noqa: BLE001  the sound server restarted: subscribe again
                log("her window's sound: %s" % str(e)[:120])
            time.sleep(2.0)
    threading.Thread(target=loop, name="her-window-sound", daemon=True).start()
    return True


def under_unit(env: dict | None = None) -> bool:
    """This process was started by a systemd unit (its cgroup is the unit's: stopping the unit kills its children)."""
    e = os.environ if env is None else env
    return bool(str(e.get("INVOCATION_ID") or "").strip()) or str(e.get("CRISSA_SERVICE") or "").strip() == "systemd"


SCOPE_PREFIX = "crissa-window-"
CLOSE_SWITCH = "CRISSA_WINDOW_CLOSE_ON_STOP"
CLOSE_CDP_WAIT_S = 4.0                   # Browser.close: how long her instance gets to close itself
CLOSE_TERM_WAIT_S = 3.0                  # then her scope's stop (or SIGTERM): how long before a SIGKILL


def close_on_stop(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return not _off(e.get(CLOSE_SWITCH, "1"))


def _sd_run(argv: list, timeout: float = 5.0, env: dict | None = None) -> "tuple[int, str]":
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           **({"env": dict(env)} if env is not None else {}))
        return r.returncode, str(r.stdout or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "%s" % type(e).__name__


def scope_props(env: dict | None = None, run=None) -> list[str]:
    """HER WINDOW STOPS WITH CRISSA (.101 round 11 HH, 2026-10-12: "loop frozen, needs to be closed on exit"): her scope
    is PartOf the launcher's unit, so `systemctl --user stop 'crissa-*'` takes it too, and Before= it, so on that stop
    the launcher goes first (its own exit closes her window cleanly with Browser.close, close_own) and systemd then
    stops whatever is left. Only while the launcher's unit is active (a scope bound to a unit that is not running
    stays unbound). Kill: CRISSA_WINDOW_CLOSE_ON_STOP=0."""
    e = os.environ if env is None else env
    if not close_on_stop(e) or (run is None and _in_test(e)):
        return []
    import crissa_service
    rc, out = (run or (lambda a: _sd_run(a, env=e)))(["systemctl", "--user", "is-active", crissa_service.UNIT])
    if out.strip() != "active":
        return []
    return ["-p", "PartOf=%s" % crissa_service.UNIT, "-p", "Before=%s" % crissa_service.UNIT]


def spawn(cmd: list[str], env: dict | None = None, popen=None, home: Path | None = None, which=None,
          run=None) -> dict:
    """Start the browser detached: its own session, and under a systemd unit its own scope (systemd-run --user
    --scope), so a stop of the Wall or the player never takes her window with it; a stop of Crissa does (scope_props,
    close_own)."""
    which = which or shutil.which
    popen = popen or subprocess.Popen
    pre: list[str] = []
    if under_unit(env) and which("systemd-run"):
        pre = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
               "--unit=%s%d-%d" % (SCOPE_PREFIX, os.getpid(), int(time.time() * 1000) % 10 ** 9)]
        pre += scope_props(env, run)
    logs = _home(home) / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        out = open(logs / LOG, "ab")
    except OSError:
        out = subprocess.DEVNULL
    try:
        p = popen(pre + list(cmd), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                  start_new_session=True, env=child_env(env))
    except OSError as ex:
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex), "scope": bool(pre)}
    finally:
        if out is not subprocess.DEVNULL:
            try:
                out.close()
            except OSError:
                pass
    return {"ok": True, "pid": getattr(p, "pid", None), "scope": bool(pre)}


# ── the debugging port: HTTP reads and a minimal WebSocket client (loopback only, no package) ────────────────────────
def _http(path: str, port_: int, timeout: float = 1.0, method: str = "GET"):
    req = urllib.request.Request("http://%s:%d%s" % (crissa_ports.HOST, int(port_), path), method=method)
    with crissa_egress.urlopen(req, timeout, "her window") as r:
        return json.loads(r.read(400000).decode("utf-8", "replace") or "null")


def up(port_: int | None = None, timeout: float = 0.6) -> bool:
    try:
        return bool(_http("/json/version", port(None) if port_ is None else port_, timeout))
    except Exception:  # noqa: BLE001
        return False


def pages(port_: int | None = None, timeout: float = 1.0) -> list[dict]:
    try:
        got = _http("/json/list", port(None) if port_ is None else port_, timeout) or []
    except Exception:  # noqa: BLE001
        return []
    return [t for t in got if isinstance(t, dict) and t.get("type") == "page"]


class _WS:
    """One WebSocket to a DevTools endpoint on loopback: text frames out (masked, as a client must), text frames in."""

    def __init__(self, url: str, timeout: float = 4.0):
        u = urllib.parse.urlsplit(url)
        if u.hostname not in (crissa_ports.HOST, "localhost") or u.scheme != "ws":
            raise OSError("not a loopback DevTools address: %s" % url)
        crissa_egress.ws_opts(url, "her window")        # the one switch's check (loopback: always allowed)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                           % (path, u.hostname, u.port or 80, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("the browser closed the connection")
            head += chunk
        line = head.split(b"\r\n", 1)[0]
        if b" 101" not in line:
            raise OSError("the browser refused the connection: %s" % line[:80].decode("latin-1"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.n = 0

    def _exact(self, k: int) -> bytes:
        while len(self.buf) < k:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("the browser closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def _frame(self, op: int, data: bytes) -> None:
        n = len(data)
        head = bytearray([0x80 | op])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            b0, b1 = self._exact(2)
            fin, op, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mask = self._exact(4) if b1 & 0x80 else b""
            data = self._exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                raise OSError("the browser closed the connection")
            if op == 0x9:
                self._frame(0xA, data)
                continue
            if op in (0x1, 0x0):
                parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def call(self, method: str, params: dict | None = None, timeout: float = 4.0) -> dict:
        self.n += 1
        mid = self.n
        self._frame(0x1, json.dumps({"id": mid, "method": method, "params": params or {}}).encode())
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.recv() or "{}")
            if msg.get("id") == mid:
                if msg.get("error"):
                    raise OSError("%s: %s" % (method, (msg["error"] or {}).get("message")))
                return msg.get("result") or {}
        raise OSError("%s: no answer" % method)

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def cdp(ws_url: str, calls: list, timeout: float = 4.0) -> list[dict]:
    """[(method, params), ...] on one connection; a call that fails is {"error": why} and the rest still run."""
    ws = _WS(ws_url, timeout)
    out = []
    try:
        for method, params in calls:
            try:
                out.append(ws.call(method, params, timeout))
            except OSError as e:
                out.append({"error": str(e)[:200]})
    finally:
        ws.close()
    return out


def _browser_ws(port_: int) -> str:
    return str((_http("/json/version", port_, 1.0) or {}).get("webSocketDebuggerUrl") or "")


# ── her instance's processes, and closing it when Crissa stops ─────────────────────────────────────────────────────
def her_profiles(home: Path | None = None) -> list[str]:
    """Every profile her instance can run on: <state home>/browser, and a snap browser's ~/snap/<name>/common copy."""
    out = [str(_home(home) / PROFILE_DIR)]
    try:
        out += sorted(str(p) for p in (Path.home() / "snap").glob("*/common/" + SNAP_PROFILE))
    except OSError:
        pass
    return out


def her_procs(home: Path | None = None, proc: str = "/proc") -> list[dict]:
    """Her own instance's processes on Linux: every process whose command line carries --user-data-dir=<her profile>
    exactly - nobody else's browser runs on her profile. [{"pid", "main", "port", "scope"}]; `main` is the browser
    process (no --type=), `port` its --remote-debugging-port, `scope` its systemd scope when it is one of hers."""
    want = {"--user-data-dir=" + p for p in her_profiles(home)}
    out = []
    try:
        names = os.listdir(proc)
    except OSError:
        return []
    for n in names:
        if not n.isdigit() or int(n) == os.getpid():
            continue
        try:
            args = [a.decode("utf-8", "replace") for a in Path(proc, n, "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if not want.intersection(args):
            continue
        port_ = next((a.split("=", 1)[1] for a in args if a.startswith("--remote-debugging-port=")), "")
        scope = ""
        try:
            for line in Path(proc, n, "cgroup").read_text().splitlines():
                leaf = line.rsplit("/", 1)[-1]
                if leaf.startswith(SCOPE_PREFIX) and leaf.endswith(".scope"):
                    scope = leaf
        except OSError:
            pass
        out.append({"pid": int(n), "main": not any(a.startswith("--type=") for a in args),
                    "port": int(port_) if port_.isdigit() else 0, "scope": scope})
    return out


def _alive(pid: int, proc: str = "/proc") -> bool:
    try:
        return Path(proc, str(pid), "stat").read_text().rsplit(")", 1)[-1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False


def _kill_her(pid: int, sig: int) -> None:
    """spawn() starts her in her own session, so her process group is hers alone; else the one process."""
    if os.getpgid(pid) == pid:
        os.killpg(pid, sig)
    else:
        os.kill(pid, sig)


def close_own(env: dict | None = None, home: Path | None = None, *, procs=None, run=None, cdp_close=None,
              kill=None, sleep=time.sleep, plat: str | None = None) -> dict:
    """STOP CRISSA CLOSES HER WINDOW TOO, and only hers (.101 round 11 HH, 2026-10-12: after `systemctl --user stop
    'crissa-*'` her Brave stayed up a minute in its own scope, showing a frozen loop; the User: "loop frozen, needs to
    be closed on exit"). Called by the launcher's own stop (the unit's stop, the menu's All OS Off, Ctrl-C in process
    mode). Her instance is the browser running on HER profile (her_procs); the person's own browser never is, so it is
    never touched. First Browser.close on her DevTools port (a clean close), then, if she is still up, her scope is
    stopped (or, with no scope, her process group gets SIGTERM), then SIGKILL. {"ok", "how", "left"}.
    Kill: CRISSA_WINDOW_CLOSE_ON_STOP=0."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": True, "how": ["off"], "left": []}
    if not close_on_stop(e):
        return {"ok": True, "how": ["kept (%s=0)" % CLOSE_SWITCH], "left": []}
    plat = plat or sys.platform
    if not plat.startswith("linux"):              # no /proc: her port is hers (she was started on it)
        try:
            ws = _browser_ws(port(e)) if up(port(e)) else ""
            if ws:
                (cdp_close or (lambda u: cdp(u, [("Browser.close", {})], 2.0)))(ws)
                return {"ok": True, "how": ["Browser.close"], "left": []}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "how": ["Browser.close failed: %s" % type(ex).__name__], "left": []}
        return {"ok": True, "how": ["not running"], "left": []}
    procs = procs or (lambda: her_procs(home))
    mains = [p for p in procs() if p["main"]]
    if not mains:
        return {"ok": True, "how": ["not running"], "left": []}
    how: list[str] = []
    p_ = port(e)
    if any(m["port"] == p_ for m in mains):
        try:
            ws = _browser_ws(p_) if up(p_) else ""
            if ws:
                (cdp_close or (lambda u: cdp(u, [("Browser.close", {})], 2.0)))(ws)
                how.append("Browser.close")
        except Exception as ex:  # noqa: BLE001  (a frozen browser may not answer: the scope comes next)
            how.append("Browser.close failed: %s" % type(ex).__name__)

    def left(wait_s: float) -> list[dict]:
        end = time.time() + wait_s
        while True:
            rest = [m for m in mains if _alive(m["pid"])]
            if not rest or time.time() >= end:
                return rest
            sleep(0.2)
    rest = left(CLOSE_CDP_WAIT_S if how and how[-1] == "Browser.close" else 0.0)
    if rest:
        kill = kill or _kill_her
        sd = run or _sd_run
        for sc in sorted({m["scope"] for m in rest if m["scope"]}):
            sd(["systemctl", "--user", "stop", "--no-block", sc])
            how.append("stopped %s" % sc)
        for m in rest:
            if not m["scope"]:
                try:
                    kill(m["pid"], 15)
                    how.append("SIGTERM %d" % m["pid"])
                except OSError:
                    pass
        rest = left(CLOSE_TERM_WAIT_S)
        for m in rest:
            try:
                if m["scope"]:
                    sd(["systemctl", "--user", "kill", "--signal=SIGKILL", m["scope"]])
                else:
                    kill(m["pid"], 9)
                how.append("SIGKILL %d" % m["pid"])
            except OSError:
                pass
        rest = left(1.0)
    return {"ok": not rest, "how": how or ["closed"], "left": [m["pid"] for m in rest]}


# ── her pages ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _origin(url: str) -> tuple:
    u = urllib.parse.urlsplit(str(url or ""))
    host = {"localhost": "127.0.0.1"}.get(u.hostname or "", u.hostname or "")
    return (u.scheme, host, u.port)


def _path(url: str) -> str:
    return urllib.parse.urlsplit(str(url or "")).path or "/"


def hers(page: dict, url: str) -> bool:
    """A page of hers: the same origin as `url` (the player)."""
    return _origin(page.get("url") or "") == _origin(url)


def pick(ps: list[dict], url: str) -> dict | None:
    """The page to show `url` in: one already on its path, else her Wall or Setup page, else any page of hers."""
    mine = [p for p in ps if hers(p, url)]
    want = _path(url)
    for rule in (lambda p: _path(p.get("url")) == want,
                 lambda p: _path(p.get("url")) in ("/wall", "/init"),
                 lambda p: True):
        got = next((p for p in mine if rule(p)), None)
        if got is not None:
            return got
    return None


def _window_state(port_: int, target: str) -> tuple[int | None, str]:
    ws = _browser_ws(port_)
    got = cdp(ws, [("Browser.getWindowForTarget", {"targetId": target})])[0] if ws else {}
    return got.get("windowId"), str((got.get("bounds") or {}).get("windowState") or "")


def _set_normal(port_: int, window_id: int) -> bool:
    ws = _browser_ws(port_)
    if not ws:
        return False
    got = cdp(ws, [("Browser.setWindowBounds", {"windowId": int(window_id), "bounds": {"windowState": "normal"}})])[0]
    return not got.get("error")


# HER WINDOW COMES FORWARD OVER HIS (round 17, 2026-10-13; .101 06:05: "one early popup had to be found behind the
# terminal"). Page.bringToFront is the browser's own activation, stamped with the browser's own last input; a window the
# launcher opened while he typed in his terminal has nothing newer than the terminal's, so GNOME's window manager
# (focus-stealing prevention) leaves it under the terminal and only marks it as wanting attention. Where her window is
# to come forward (front: Setup at the first start, the menu's pages, the dock), it is also activated the way a pager
# does it - _NET_ACTIVE_WINDOW, source 2, as the desk Wall's pop-ups ask since round 16 - her top-most window by its
# class (--class=Crissa). Linux X11 only (her browser runs on X11/Xwayland); no xprop, no libX11, no window of hers: a
# no-op. Never a window of the person's. Kill: CRISSA_WINDOW_PAGER_RAISE=0 (round 16: Page.bringToFront alone).
PAGER_SWITCH = "CRISSA_WINDOW_PAGER_RAISE"


def _hex_ids(text: str) -> list[int]:
    import re
    return [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", str(text or "").split("=", 1)[-1])]


def her_xids(env: dict | None = None, run=None) -> list[int]:
    """Her browser's top-level X windows (WM_CLASS class Crissa), bottom to top as the window manager stacks them."""
    import re
    e = dict(os.environ if env is None else env)
    run = run or (lambda a: subprocess.run(a, capture_output=True, text=True, timeout=2, env=e).stdout or "")
    out = []
    for xid in _hex_ids(run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])):
        names = re.findall(r'"([^"]*)"', run(["xprop", "-id", hex(xid), "WM_CLASS"]))
        if len(names) >= 2 and names[1] == WM_CLASS:
            out.append(xid)
    return out


def x11_activate(xid: int, display: str | None = None) -> bool:
    """_NET_ACTIVE_WINDOW to the root window, source 2 (a pager), as lib/house_wall_ascii InputShape.activate."""
    import ctypes
    from ctypes import Structure, Union, byref, c_char_p, c_int, c_long, c_ulong, c_void_p
    try:
        x = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    dpy = x.XOpenDisplay(display.encode() if display else None)
    if not dpy:
        return False

    class XClientMessageEvent(Structure):
        _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                    ("window", c_ulong), ("message_type", c_ulong), ("format", c_int), ("data", c_long * 5)]

    class XEvent(Union):
        _fields_ = [("xclient", XClientMessageEvent), ("pad", c_long * 24)]
    try:
        x.XInternAtom.restype = c_ulong
        x.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x.XDefaultRootWindow.restype = c_ulong
        x.XDefaultRootWindow.argtypes = [c_void_p]
        x.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
        x.XFlush.argtypes = [c_void_p]
        x.XCloseDisplay.argtypes = [c_void_p]
        ev = XEvent()
        ev.xclient.type = 33                 # ClientMessage
        ev.xclient.window = int(xid)
        ev.xclient.message_type = x.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
        ev.xclient.format = 32
        ev.xclient.data[0] = 2               # source: a pager (the person's own act)
        root = x.XDefaultRootWindow(dpy)
        x.XSendEvent(dpy, root, 0, (1 << 20) | (1 << 19), byref(ev))
        x.XFlush(dpy)
        return True
    finally:
        x.XCloseDisplay(dpy)


# HER ENTRANCE ON A CLEAR SCREEN (round 36, the User on .101 2026-10-20: "Can we minimize all windows when Lila begins to
# talk? So they see the entry."): at Setup's handover, first boot only, every normal window on her screen is minimized -
# never the Wall (a desktop-type window), a dock or a panel, and never her own (her Setup window closes by itself). Each
# window is asked as a pager would (WM_CHANGE_STATE, IconicState: what XIconifyWindow sends), not "show desktop", which
# can hide the Wall too. No restore: the Wall is the desktop now, and his windows are a click away on the dock. Linux
# X11 only (xprop + libX11); anything missing is a no-op that says why. Kill CRISSA_HANDOVER_MINIMIZE=0.
SKIP_TYPES = ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK", "_NET_WM_WINDOW_TYPE_SPLASH",
              "_NET_WM_WINDOW_TYPE_NOTIFICATION", "_NET_WM_WINDOW_TYPE_TOOLBAR", "_NET_WM_WINDOW_TYPE_MENU")


def x11_iconify(xid: int, display: str | None = None) -> bool:
    """WM_CHANGE_STATE IconicState to the root window for `xid` (ICCCM 4.1.4; XIconifyWindow)."""
    import ctypes
    from ctypes import Structure, Union, byref, c_char_p, c_int, c_long, c_ulong, c_void_p
    try:
        x = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    dpy = x.XOpenDisplay(display.encode() if display else None)
    if not dpy:
        return False

    class XClientMessageEvent(Structure):
        _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                    ("window", c_ulong), ("message_type", c_ulong), ("format", c_int), ("data", c_long * 5)]

    class XEvent(Union):
        _fields_ = [("xclient", XClientMessageEvent), ("pad", c_long * 24)]
    try:
        x.XInternAtom.restype = c_ulong
        x.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x.XDefaultRootWindow.restype = c_ulong
        x.XDefaultRootWindow.argtypes = [c_void_p]
        x.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
        x.XFlush.argtypes = [c_void_p]
        x.XCloseDisplay.argtypes = [c_void_p]
        ev = XEvent()
        ev.xclient.type = 33                 # ClientMessage
        ev.xclient.window = int(xid)
        ev.xclient.message_type = x.XInternAtom(dpy, b"WM_CHANGE_STATE", 0)
        ev.xclient.format = 32
        ev.xclient.data[0] = 3               # IconicState
        root = x.XDefaultRootWindow(dpy)
        x.XSendEvent(dpy, root, 0, (1 << 20) | (1 << 19), byref(ev))
        x.XFlush(dpy)
        return True
    finally:
        x.XCloseDisplay(dpy)


def minimize_for_entrance(env: dict | None = None, run=None, send=None, plat: str | None = None) -> tuple[int, str]:
    """(windows minimized, why when none). Never the Wall, a dock or a panel, never hers."""
    import re
    e = dict(os.environ if env is None else env)
    if _off(e.get("CRISSA_HANDOVER_MINIMIZE", "1")):
        return 0, "off"
    if not (plat or sys.platform).startswith("linux"):
        return 0, "not Linux"
    if _in_test(e) and (run is None or send is None):
        return 0, "test"
    if not str(e.get("DISPLAY") or "").strip():
        add, _why = session_x11(e, plat)
        e.update(add)
    if not str(e.get("DISPLAY") or "").strip():
        return 0, "no X display"
    if run is None and not shutil.which("xprop"):
        return 0, "no xprop"
    run = run or (lambda a: subprocess.run(a, capture_output=True, text=True, timeout=2, env=e).stdout or "")
    n = 0
    for xid in _hex_ids(run(["xprop", "-root", "_NET_CLIENT_LIST"])):
        kinds = run(["xprop", "-id", hex(xid), "_NET_WM_WINDOW_TYPE"])
        if any(t in kinds for t in SKIP_TYPES):
            continue
        names = re.findall(r'"([^"]*)"', run(["xprop", "-id", hex(xid), "WM_CLASS"]))
        if len(names) >= 2 and names[1] == WM_CLASS:
            continue                          # hers: her Setup window closes by itself
        if "_NET_WM_STATE_HIDDEN" in run(["xprop", "-id", hex(xid), "_NET_WM_STATE"]):
            continue
        try:
            if (send or x11_iconify)(xid, str(e.get("DISPLAY") or "")):
                n += 1
        except Exception:  # noqa: BLE001  one window that will not go never stops the rest
            continue
    return n, ("" if n else "no window to minimize")


def pager_raise(env: dict | None = None, run=None, send=None, plat: str | None = None) -> str:
    """Activate her top-most window as a pager would. Returns what it did ("activated 0x..", or why not)."""
    e = os.environ if env is None else env
    if _off(e.get(PAGER_SWITCH, "1")):
        return "off"
    if not (plat or sys.platform).startswith("linux"):
        return "not Linux"
    if _in_test(e) and (run is None or send is None):
        return "test"
    if not str(e.get("DISPLAY") or "").strip():
        add, _why = session_x11(e, plat)
        e = dict(e, **add)
    if not str(e.get("DISPLAY") or "").strip():
        return "no X display"
    if run is None and not shutil.which("xprop"):
        return "no xprop"
    try:
        ids = her_xids(e, run)
    except Exception as ex:  # noqa: BLE001  a raise is a nicety; bringToFront already ran
        return "could not list windows (%s)" % type(ex).__name__
    if not ids:
        return "no window of hers"
    try:
        ok = (send or x11_activate)(ids[-1], str(e.get("DISPLAY") or ""))
    except Exception as ex:  # noqa: BLE001
        return "activate failed (%s)" % type(ex).__name__
    return ("activated 0x%x" % ids[-1]) if ok else "activate failed"


# HER FIRST WINDOW COMES FORWARD TOO (round 19, .101 2026-10-14 02:35: "i had to front the window with the button";
# crissa.log said "front, activated"). Right after her own launch the raise lands BEFORE the window manager shows the
# window for the first time: the page answers on the debugging port and the window is already in the client list, but
# GNOME places a new window when it first SHOWS it, and one that opens while the person types in a terminal goes under
# the terminal - which undoes the raise sent a moment earlier. The same raise on a window already shown works (the
# button, the menu's pages). So after a launch it is sent again until her window is seen active, at RETRY_AT_S after
# the launch, and stops for good the first time it is: a window he clicks away from afterwards is his call. Linux X11
# only, as pager_raise. Kill CRISSA_WINDOW_RAISE_RETRY=0 (one raise, as round 18).
RETRY_SWITCH = "CRISSA_WINDOW_RAISE_RETRY"
RETRY_AT_S = (0.5, 1.2, 2.5, 4.0, 6.0, 9.0)


def active_xid(env: dict | None = None, run=None) -> int | None:
    """The X window the window manager says is active (_NET_ACTIVE_WINDOW on the root); 0 or None = none of X's."""
    e = dict(os.environ if env is None else env)
    run = run or (lambda a: subprocess.run(a, capture_output=True, text=True, timeout=2, env=e).stdout or "")
    ids = _hex_ids(run(["xprop", "-root", "_NET_ACTIVE_WINDOW"]))
    return ids[0] if ids else None


def raise_until_active(env: dict | None = None, run=None, send=None, plat: str | None = None, sleep=time.sleep,
                       at: tuple = RETRY_AT_S) -> list[str]:
    """After her launch: send the pager raise again until her window is the active one, or `at` runs out. Returns one
    word per look ("active" ends it)."""
    e = dict(os.environ if env is None else env)
    if _off(e.get(RETRY_SWITCH, "1")) or _off(e.get(PAGER_SWITCH, "1")):
        return ["off"]
    if not (plat or sys.platform).startswith("linux"):
        return ["not Linux"]
    if _in_test(e) and (run is None or send is None):
        return ["test"]
    if not str(e.get("DISPLAY") or "").strip():
        add, _why = session_x11(e, plat)
        e = dict(e, **add)
    if not str(e.get("DISPLAY") or "").strip():
        return ["no X display"]
    if run is None and not shutil.which("xprop"):
        return ["no xprop"]
    done: list[str] = []
    last = 0.0
    for t in at:
        sleep(max(0.0, float(t) - last))
        last = float(t)
        try:
            mine = her_xids(e, run)
            act = active_xid(e, run)
        except Exception as ex:  # noqa: BLE001  a raise is a nicety
            done.append("could not look (%s)" % type(ex).__name__)
            continue
        if act and act in mine:
            done.append("active")
            return done
        done.append(pager_raise(e, run=run, send=send, plat=plat))
    done.append("gave up")
    return done


def _raise_after_launch(home: Path | None = None) -> None:
    got = raise_until_active()
    _note(home, "raise after launch: %s" % ", ".join(got))


def _bring(port_: int, page: dict, url: str, front: bool, after_launch: bool = False) -> dict:
    """Show `url` in `page`: navigate when it is elsewhere, restore it when minimised (and, right after our own launch,
    when the window manager maximised it), bring it to the front."""
    how = []
    ws = str(page.get("webSocketDebuggerUrl") or "")
    calls = []
    if str(page.get("url") or "").rstrip("/") != str(url).rstrip("/"):
        calls.append(("Page.navigate", {"url": url}))
        how.append("navigated")
    if front or after_launch:
        try:
            wid, state = _window_state(port_, str(page.get("id") or ""))
        except OSError:
            wid, state = None, ""
        if wid is not None and ((front and state == "minimized")
                                or (after_launch and state in ("maximized", "fullscreen"))):
            if _set_normal(port_, wid):
                how.append("restored" if state == "minimized" else "normal (was %s)" % state)
    if front:
        calls.append(("Page.bringToFront", {}))
        how.append("front")
    if calls and ws:
        res = cdp(ws, calls)
        errs = [r["error"] for r in res if r.get("error")]
        if errs:
            return {"ok": False, "how": how, "error": errs[0], "id": str(page.get("id") or "")[:8]}
    if front:
        raised = pager_raise()                 # round 17: over the window that has the keyboard (see pager_raise)
        if raised.startswith("activated"):
            how.append("activated")
        if after_launch and not _off(os.environ.get(RETRY_SWITCH, "1")) and not _in_test():
            # round 19: again once GNOME has shown the new window (see RETRY_AT_S); the launcher keeps running
            threading.Thread(target=_raise_after_launch, name="her-window-raise", daemon=True).start()
            how.append("raise again until active")
    return {"ok": True, "how": how, "id": str(page.get("id") or "")[:8]}


def _open_target(port_: int, url: str, background: bool = False) -> str:
    ws = _browser_ws(port_)
    if not ws:
        return ""
    got = cdp(ws, [("Target.createTarget", {"url": url, "background": bool(background)})])[0]
    return str(got.get("targetId") or "")


def _wait_page(port_: int, url: str, wait_s: float, sleep=time.sleep) -> dict | None:
    end = time.time() + wait_s
    while True:
        got = pick(pages(port_), url)
        if got is not None or time.time() >= end:
            return got
        sleep(0.25)


def show(url: str, *, front: bool = True, launch: bool = True, env: dict | None = None, popen=None, which=None,
         plat: str | None = None, home: Path | None = None, wait_s: float = LAUNCH_WAIT_S, sleep=time.sleep,
         run=None, session=None) -> dict:
    """Show `url` in her own window and (front=True) bring it forward: her running instance's page moved there, or a
    new instance started on it. {"ok", "how", "launched", "why", "message"}. Never the person's own browser."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": False, "why": "off", "message": "her own window is off here (CRISSA_OWN_WINDOW=0, or the house)"}
    if _in_test(e) and popen is None:
        return {"ok": False, "why": "test", "message": "a test never opens a real browser"}
    p_ = port(e)
    if up(p_):
        page = pick(pages(p_), url)
        if page is None:
            tid = _open_target(p_, url)
            page = _wait_page(p_, url, PAGE_WAIT_S, sleep) if tid else None
            if page is None:
                return {"ok": False, "why": "no page", "message": "her window did not open a page for %s" % url}
            out = _bring(p_, page, url, front)
            out["how"] = ["opened"] + out["how"]
            return dict(out, launched=False)
        return dict(_bring(p_, page, url, front), launched=False)
    if not launch:
        return {"ok": False, "why": "not running", "launched": False}
    x11, x11_why = session_x11(e, plat, read=session)
    if x11:
        e = dict(e, **x11)
    if not has_screen(e, plat):
        return {"ok": False, "why": "no screen", "message": "no screen here: open %s in a browser" % url}
    b = find(e, which, plat)
    if b is None:
        return {"ok": False, "why": "no browser", "message": no_browser_message(url, which, plat),
                "firefox": firefox_here(which, plat)}
    prof = profile_dir(b, home)
    prepare_profile(prof, e)
    size = window_size(screen_size(run, env=e if x11 else None) if (plat or sys.platform).startswith("linux") else None)
    if (plat or sys.platform).startswith("linux"):
        _note(home, x11_why)
    got = spawn(argv(b, url, port_=p_, profile=prof, plat=plat, env=e, size=size), e, popen, home, which)
    if not got.get("ok"):
        return {"ok": False, "why": "start failed", "message": "%s did not start: %s" % (NAMES.get(b["kind"], "the browser"),
                                                                                         got.get("error")), "launched": False}
    end = time.time() + wait_s
    while not up(p_) and time.time() < end:
        sleep(0.25)
    if not up(p_):
        return {"ok": False, "why": "slow", "launched": True, "browser": b["kind"],
                "message": "%s started but did not answer in %.0f s" % (NAMES.get(b["kind"], "the browser"), wait_s)}
    page = _wait_page(p_, url, PAGE_WAIT_S, sleep)
    out = _bring(p_, page, url, front, after_launch=True) if page is not None else {"ok": True, "how": []}
    return dict(out, launched=True, browser=b["kind"], profile=str(prof))


# HER FACE ALONE, AS THE TILE ITSELF (round 26, .101 2026-10-16, the User: "let's trim the edges and match the AR of
# current state. Give it the same buttons ... keep the same object with buttons on it as only difference"; "face only
# did NOT reopen where I dragged it"; "gets bigger every time I reopen it"; at a start it "just opened in fullscreen").
# - It opens as an APP window (no tabs, no address bar) even when her browser is already running: the same command
#   that starts her browser, which a running instance takes as "open this app window" (it was Target.createTarget, a
#   plain tabbed window).
# - Its place and size are the BROWSER's window bounds, read and set through the same calls (face_bounds, _place). The
#   page's window.screenX / outerWidth disagreed with them by the frame (each reopen grew by it) and under Xwayland its
#   place read wrong. The bounds are set from normal, checked, and set again (a window opened fullscreen or maximised
#   takes a size only once it is normal).
# - No frame: the window manager is asked for none (_MOTIF_WM_HINTS) on X11 / Xwayland; it is found by its title.
# Kill: CRISSA_FACE_FRAMELESS=0 (the frame stays), CRISSA_FACE_APP_WINDOW=0 (round 25's tabbed window).
FACE_TITLE = "Crissa face"


def face_page(url: str, port_: int) -> dict | None:
    return next((p for p in pages(port_) if hers(p, url) and _path(p.get("url")) == _path(url)), None)


def face_bounds(url: str, env: dict | None = None) -> dict | None:
    """Her face window's bounds {x, y, w, h} as the browser has them, or None."""
    e = os.environ if env is None else env
    if not enabled(e) or _in_test(e):
        return None
    p_ = port(e)
    if not up(p_):
        return None
    page = face_page(url, p_)
    ws = _browser_ws(p_) if page else ""
    got = cdp(ws, [("Browser.getWindowForTarget", {"targetId": str(page.get("id") or "")})])[0] if ws else {}
    b = got.get("bounds") or {}
    try:
        return {"x": int(b["left"]), "y": int(b["top"]), "w": int(b["width"]), "h": int(b["height"])}
    except (KeyError, TypeError, ValueError):
        return None


def _place(port_: int, page: dict, bounds: dict, tries: int = 3, sleep=time.sleep) -> bool:
    """Normal first, then the bounds; checked and set again while the browser has not taken them."""
    target = str(page.get("id") or "")
    want = {"left": int(bounds["x"]), "top": int(bounds["y"]), "width": int(bounds["w"]), "height": int(bounds["h"])}
    for _ in range(max(1, tries)):
        ws = _browser_ws(port_)
        got = cdp(ws, [("Browser.getWindowForTarget", {"targetId": target})])[0] if ws else {}
        wid = got.get("windowId")
        if wid is None:
            return False
        if str((got.get("bounds") or {}).get("windowState") or "normal") != "normal":
            cdp(ws, [("Browser.setWindowBounds", {"windowId": int(wid), "bounds": {"windowState": "normal"}})])
            sleep(0.35)
        cdp(ws, [("Browser.setWindowBounds", {"windowId": int(wid), "bounds": want})])
        sleep(0.2)
        now = (cdp(ws, [("Browser.getWindowForTarget", {"targetId": target})])[0].get("bounds") or {})
        if all(abs(int(now.get(k) or 0) - v) <= 4 for k, v in want.items()) and now.get("windowState", "normal") == "normal":
            return True
    return False


def face_xid(env: dict | None = None, run=None) -> int | None:
    """Her face window's X id: her browser's window titled FACE_TITLE."""
    e = dict(os.environ if env is None else env)
    run = run or (lambda a: subprocess.run(a, capture_output=True, text=True, timeout=2, env=e).stdout or "")
    for xid in reversed(her_xids(e, run)):
        if FACE_TITLE in run(["xprop", "-id", hex(xid), "_NET_WM_NAME"]):
            return xid
    return None


def _frameless(env: dict | None = None, run=None, send=None, sleep=time.sleep, plat: str | None = None) -> str:
    e = dict(os.environ if env is None else env)
    if _off(e.get("CRISSA_FACE_FRAMELESS", "1")):
        return "off"
    if not (plat or sys.platform).startswith("linux"):
        return "not Linux"
    if _in_test(e) and (run is None or send is None):
        return "test"
    if not str(e.get("DISPLAY") or "").strip():
        add, _why = session_x11(e, plat)
        e = dict(e, **add)
    if not str(e.get("DISPLAY") or "").strip():
        return "no X display"
    if run is None and not shutil.which("xprop"):
        return "no xprop"
    send = send or x11_undecorate
    for _ in range(10):
        xid = face_xid(e, run)
        if xid:
            return "frameless 0x%x" % xid if send(xid, e.get("DISPLAY")) else "could not ask"
        sleep(0.3)
    return "not found"


def _open_app(url: str, e: dict, p_: int, kill: str = "CRISSA_FACE_APP_WINDOW", **kw) -> bool:
    """Ask her running browser for an app window on `url` (the launch command, which it forwards)."""
    if _off(e.get(kill, "1")):
        return False
    b = find(e, kw.get("which"), kw.get("plat"))
    if b is None:
        return False
    x11, _why = session_x11(e, kw.get("plat"))
    ee = dict(e, **x11) if x11 else e
    got = spawn(argv(b, url, port_=p_, profile=profile_dir(b, kw.get("home")), plat=kw.get("plat"), env=ee),
                ee, kw.get("popen"), kw.get("home"), kw.get("which"))
    return bool(got.get("ok"))


def show_face(url: str, bounds: dict | None, *, env: dict | None = None, above: bool = False, **kw) -> dict:
    """FACE ONLY (2026-10-16, lib/crissa_face): her face page in a window of its own in her browser, at the place and
    size the person last left it (bounds {x, y, w, h}; None = the browser's own). A face window already open comes
    forward as it is. Never the person's browser; the bounds are only ever the ones this window had."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": False, "why": "off", "message": "her own window is off here"}
    if _in_test(e) and kw.get("popen") is None:
        return {"ok": False, "why": "test", "message": "a test never opens a real browser"}
    p_ = port(e)
    fresh = False
    if up(p_):
        page = pick(pages(p_), url)
        if page is not None:
            placed = False
            if bounds:                                   # round 26: a face window left fullscreen or maximised
                try:
                    _wid, st = _window_state(p_, str(page.get("id") or ""))
                    if st in ("fullscreen", "maximized"):
                        placed = _place(p_, page, bounds)
                except Exception:  # noqa: BLE001 - the browser did not say: it comes forward as it is
                    placed = False
            out = dict(_bring(p_, page, url, True), launched=False, opened=False, placed=placed)
            if above:
                out["above"] = _keep_above(e)
            return out
        if _open_app(url, e, p_, **kw):                  # round 26: an app window, no tabs or address bar
            page = _wait_page(p_, url, PAGE_WAIT_S)
        else:
            ws = _browser_ws(p_)
            got = cdp(ws, [("Target.createTarget", {"url": url, "newWindow": True})])[0] if ws else {}
            if not got.get("targetId"):
                return {"ok": False, "why": "no window", "message": "her browser did not open the face window"}
            page = _wait_page(p_, url, PAGE_WAIT_S)
        fresh = True
    else:
        out = show(url, front=True, env=e, **kw)
        if not out.get("ok"):
            return out
        page = pick(pages(p_), url)
        fresh = True
    if page is None:
        return {"ok": False, "why": "no page", "message": "the face window did not open"}
    placed = False
    if fresh and bounds:
        placed = _place(p_, page, bounds)                # round 26: from normal, checked (see FACE_TITLE)
    out = _bring(p_, page, url, True)
    if fresh:
        out["frame"] = _frameless(e)
    if above:                       # THE WALL OFF (2026-10-16): her face kept over every window (lib/crissa_face)
        out["above"] = _keep_above(e)
    return dict(out, opened=True, placed=placed)


def place_face(url: str, bounds: dict, env: dict | None = None) -> bool:
    """Set her face window's place and size (the tile's bar: shape, - / +). True when the browser took it."""
    e = os.environ if env is None else env
    if not enabled(e) or _in_test(e):
        return False
    p_ = port(e)
    if not up(p_):
        return False
    page = face_page(url, p_)
    if page is None:
        return False
    return _place(p_, page, bounds)


_MOVE: dict = {}                     # the face window being dragged: {"target", "ws", "wid", "at"} (move_face)


def close_face(url: str, env: dict | None = None) -> bool:
    """Close her face window (round 45: the tile's x). True when the browser closed it."""
    e = os.environ if env is None else env
    if not enabled(e) or _in_test(e):
        return False
    p_ = port(e)
    if not up(p_):
        return False
    page = face_page(url, p_)
    ws = _browser_ws(p_) if page else ""
    res = cdp(ws, [("Target.closeTarget", {"targetId": str(page.get("id") or "")})], 2.0) if ws else []
    return bool(res) and not res[0].get("error")


def move_face(url: str, x: int, y: int, env: dict | None = None) -> bool:
    """DRAG HER FACE WINDOW (round 27, .101 2026-10-16: "could not drag it" - the window is frameless, so it has no
    title bar to take hold of): the page follows the pointer and asks for each new place; only left/top change. The
    window's id is kept for a drag (2 s) so a move is one call to the browser."""
    e = os.environ if env is None else env
    if not enabled(e) or _in_test(e):
        return False
    p_ = port(e)
    now = time.time()
    # .101 2026-10-17: a COPY - `m = _MOVE` was the same dict, so _MOVE.clear() below emptied it and every next move
    # within 2 s raised KeyError: 'ws' (2290 times in one drag session)
    m = dict(_MOVE) if (_MOVE.get("ws") and now - float(_MOVE.get("at") or 0) < 2.0) else {}
    if not m:
        if not up(p_):
            return False
        page = face_page(url, p_)
        ws = _browser_ws(p_) if page else ""
        got = cdp(ws, [("Browser.getWindowForTarget", {"targetId": str(page.get("id") or "")})])[0] if ws else {}
        if got.get("windowId") is None:
            _MOVE.clear()
            return False
        m = {"ws": ws, "wid": int(got["windowId"])}
    res = cdp(m["ws"], [("Browser.setWindowBounds", {"windowId": m["wid"],
                                                     "bounds": {"left": int(x), "top": int(y)}})])
    ok = bool(res) and not any(isinstance(r, dict) and r.get("error") for r in res)
    _MOVE.clear()
    if ok:
        _MOVE.update(m, at=now)
    return ok


def face_bounds(url: str, env: dict | None = None) -> dict | None:
    """Where her face window is, from the browser itself: {"left", "top", "width", "height"}, None when unknown.
    .101 round 28 (the User 2026-10-17: "When I click it it jumps to left of screen then drags from there"): under
    Xwayland the page's window.screenX/Y is not where the window is, so a drag started from it jumped; the drag now
    starts from these bounds."""
    e = os.environ if env is None else env
    if not enabled(e) or _in_test(e):
        return None
    p_ = port(e)
    if not up(p_):
        return None
    page = face_page(url, p_)
    ws = _browser_ws(p_) if page else ""
    got = cdp(ws, [("Browser.getWindowForTarget", {"targetId": str(page.get("id") or "")})])[0] if ws else {}
    if got.get("windowId") is None:
        return None
    b = cdp(ws, [("Browser.getWindowBounds", {"windowId": int(got["windowId"])})])[0] or {}
    b = b.get("bounds") if isinstance(b.get("bounds"), dict) else (got.get("bounds") or {})
    if b.get("left") is None or b.get("top") is None:
        return None
    _MOVE.clear()
    _MOVE.update(ws=ws, wid=int(got["windowId"]), at=time.time())   # the drag's moves reuse this window id
    return {k: int(b[k]) for k in ("left", "top", "width", "height") if b.get(k) is not None}


def close_face(url: str, env: dict | None = None) -> bool:
    """Close her face window(s) (the page at `url`'s path on the player's origin). True when one closed."""
    e = os.environ if env is None else env
    if not enabled(e) or _in_test(e):
        return False
    p_ = port(e)
    if not up(p_):
        return False
    mine = [p for p in pages(p_) if hers(p, url) and _path(p.get("url")) == _path(url)]
    ws = _browser_ws(p_) if mine else ""
    res = cdp(ws, [("Target.closeTarget", {"targetId": str(p.get("id") or "")}) for p in mine], 2.0) if ws else []
    return bool(res) and not any(r.get("error") for r in res)


def show_panel(url: str, player_url: str, *, env: dict | None = None, **kw) -> dict:
    """HER WINDOW ON THE CONTROL PANEL (the User 2026-10-12, the dock press while the desk Wall is up): `url` is the
    Control Panel, `player_url` the player whose pages are hers. A Control Panel page in her window is brought forward
    as it is; else the page she shows (her Wall, Setup, any page of the player's) is moved there (Page.navigate on her
    own instance) and brought forward, so no second Wall paints beside the desk Wall; else her instance opens it, or
    starts on it (show). Never the person's own browser. Same answer as show()."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": False, "why": "off", "message": "her own window is off here (CRISSA_OWN_WINDOW=0, or the house)"}
    if _in_test(e) and kw.get("popen") is None:
        return {"ok": False, "why": "test", "message": "a test never opens a real browser"}
    p_ = port(e)
    if up(p_):
        ps = pages(p_)
        cp = next((p for p in ps if _origin(p.get("url") or "") == _origin(url)), None)
        if cp is not None:
            return dict(_bring(p_, cp, str(cp.get("url") or url), True), launched=False)
        mine = pick(ps, player_url)
        if mine is not None:
            out = _bring(p_, mine, url, True)
            out["how"] = ["moved from %s" % _path(mine.get("url"))] + list(out.get("how") or [])
            return dict(out, launched=False)
    return show(url, front=True, env=e, **kw)


# ── every Crissa page in her own instance (the User 2026-10-12) ──────────────────────────────────────────────────────
def is_her_page(url: str, env: dict | None = None) -> bool:
    """A Crissa page: one of this install's own local pages (loopback, a port of its port table: the player, the
    Control Panel, the feed, ...) or the Crissa store. Anything else is the person's to open where they like."""
    try:
        u = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"):
        try:
            ports = {crissa_ports.port(n, env) for n in crissa_ports.DEFAULTS if n != "browser"}
        except Exception:  # noqa: BLE001
            return False
        return (u.port or 80) in ports
    if is_phone_page(url, env):
        return True
    # THE STORE OPENS IN THE PERSON'S OWN BROWSER (the User 2026-10-29: "yes open the Store in my browser and mine meaning
    # Users Default so easy to store bookmarks"): it is a website, so it outlives Stop and needs nothing of hers, and
    # their own browser keeps their bookmarks, logins and payment details. Her own pages (Setup, Connect, the Control
    # Panel, the Wall) stay in her window. CRISSA_STORE_IN_HER_WINDOW=1 = the 10-12 way (the Store in hers).
    e = os.environ if env is None else env
    if str(e.get("CRISSA_STORE_IN_HER_WINDOW", "0")).strip().lower() not in ("1", "on", "true", "yes"):
        return False
    try:
        return bool(crissa_home.is_store(str(url)))
    except Exception:  # noqa: BLE001
        return False


def _hers_on_port(p_: int, home: Path | None = None, plat: str | None = None) -> bool:
    """On Linux: the browser on her DevTools port is her own instance (its main process runs on her profile). Off
    Linux there is no /proc to ask, and her port is hers (she was started on it)."""
    if not (plat or sys.platform).startswith("linux"):
        return True
    return any(m["main"] and m["port"] == int(p_) for m in her_procs(home))


def open_page(url: str, *, env: dict | None = None, front: bool = True, home: Path | None = None, **kw) -> dict:
    """EVERY CRISSA PAGE IN HER OWN INSTANCE (the User 2026-10-12, .101: the Store key, Connect and the Control Panel
    opened in his own Brave as "default browser", and after Stop a Crissa page stayed there frozen - "need to close her
    Brave instance on shutdown. Left open frozen"). The same page already open in her instance is brought forward; else
    a new window of her instance opens it (Target.createTarget); else her instance starts on it (show). Stop and All OS
    Off close her instance (close_own), so every page of hers goes with it. Same answer as show(); "why": "no browser"
    when this computer has no browser of her family, and the caller opens the page in the default browser and says so."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": False, "why": "off", "message": "her own window is off here (CRISSA_OWN_WINDOW=0, or the house)"}
    if _in_test(e) and kw.get("popen") is None:
        return {"ok": False, "why": "test", "message": "a test never opens a real browser"}
    p_ = port(e)
    if up(p_):
        ps = pages(p_)
        same = next((p for p in ps if _origin(p.get("url") or "") == _origin(url)
                     and _path(p.get("url") or "") == _path(url)), None)
        if same is not None:
            return dict(_bring(p_, same, url, front), launched=False)
        tid = _open_target(p_, url, background=not front)
        end = time.time() + PAGE_WAIT_S
        page = None
        while tid and page is None and time.time() < end:
            page = next((p for p in pages(p_) if str(p.get("id") or "") == tid), None)
            if page is None:
                kw.get("sleep", time.sleep)(0.2)
        if page is None:
            return {"ok": False, "why": "no page", "message": "her window did not open a page for %s" % url,
                    "launched": False}
        out = _bring(p_, page, str(page.get("url") or url), front)
        out["how"] = ["opened"] + list(out.get("how") or [])
        return dict(out, launched=False)
    return show(url, front=front, env=e, home=home, **kw)


# THE PHONE PAGE IN A PHONE-SHAPED WINDOW OF HERS (.101 10-19, the User: "messy browser phone window", "not a clean
# rectangle box"): the Wall key's "Your phone: http://<this computer>:8226" opened in the person's default browser
# (wall.log "open http://<its LAN address>:8226 (default browser)": tabs, address bar, bookmarks, side bar), because only
# loopback addresses counted as her pages. This install's phone port on one of this computer's own addresses is hers
# now: an app window of her instance (no tabs, no bar), phone-shaped (PHONE_ASPECT, PHONE_OF_SCREEN of the screen's
# height), centred; Stop closes it with her instance. Kill CRISSA_PHONE_OWN_WINDOW=0 (the default browser, as before).
PHONE_ASPECT = 9.0 / 19.5
PHONE_OF_SCREEN = 0.85


def _own_address(host: str) -> bool:
    """`host` is an address of this computer (a socket binds to it)."""
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


def is_phone_page(url: str, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if _off(e.get("CRISSA_PHONE_OWN_WINDOW", "1")):
        return False
    try:
        u = urllib.parse.urlsplit(str(url or ""))
        return (u.scheme == "http" and bool(u.hostname) and (u.port or 80) == crissa_ports.port("phone", env)
                and _own_address(str(u.hostname)))
    except Exception:  # noqa: BLE001
        return False


def phone_bounds(env: dict | None = None) -> dict | None:
    """{x, y, w, h}: a phone's shape, PHONE_OF_SCREEN of the screen's height, centred; None with no screen size."""
    e = os.environ if env is None else env
    x11, _why = session_x11(e)
    scr = screen_size(env=dict(e, **x11) if x11 else None)
    if not scr:
        return None
    sw, sh = scr
    h = int(sh * PHONE_OF_SCREEN)
    w = int(h * PHONE_ASPECT)
    return {"x": (sw - w) // 2, "y": (sh - h) // 2, "w": w, "h": h}


def open_phone(url: str, *, env: dict | None = None, **kw) -> dict:
    """The phone page in an app window of her instance, phone-shaped; one already open comes forward as it is."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": False, "why": "off", "message": "her own window is off here (CRISSA_OWN_WINDOW=0, or the house)"}
    if _in_test(e) and kw.get("popen") is None:
        return {"ok": False, "why": "test", "message": "a test never opens a real browser"}
    p_ = port(e)
    if up(p_):
        same = next((p for p in pages(p_) if hers(p, url)), None)
        if same is not None:
            return dict(_bring(p_, same, url, True), launched=False, phone=True)
        page = _wait_page(p_, url, PAGE_WAIT_S) if _open_app(url, e, p_, **kw) else None
        if page is None:
            return open_page(url, env=e, **kw)            # a window of hers with its tab strip, as before
        launched = False
    else:
        out = show(url, front=True, env=e, **kw)          # her instance starts on it, an app window (--app)
        if not out.get("ok"):
            return out
        # round 31 (the User: "phone opens in normal tab default ... proper AR"): a browser just started may not list
        # its page yet; one look missed it and the window kept the launch size. Wait for it, as the other paths do.
        page = _wait_page(p_, url, PAGE_WAIT_S)
        launched = True
        if page is None:
            return dict(out, phone=True)
    b = phone_bounds(e)
    placed = _place(p_, page, b) if b else False
    return dict(_bring(p_, page, url, True), launched=launched, placed=placed, phone=True)


def close_agent_pages(player_url: str, env: dict | None = None, home: Path | None = None,
                      plat: str | None = None, tries: int = 3, sleep=time.sleep) -> dict:
    """CLOSE AGENT closes the agent's pages, never the OS's (the User 2026-10-12): every page of her instance on the
    player's origin (her Wall, Setup, Connect) is closed with Target.closeTarget; a Control Panel or store page she
    shows stays (the person may have pressed Close Agent there). Her instance only - the browser on her DevTools port
    running on her profile - never the person's browser. {"ok", "how"}; "retry": True when it could not finish.

    PATIENT, AND CHECKED (round 26, the round 25 and 26 acceptances under a parallel test run: "Close Agent did not close
    the agent alone", her Wall and Connect pages still open 36 s later): a busy browser answered after the 0.6 s / 1 s / 2 s
    waits, so the one try did nothing and was never repeated. Each step now waits up to 3 s (the close 6 s), the pages are
    looked at again after the close, and up to `tries` rounds run; what is still open says "retry" so the launcher's next
    pass tries again (scripts/crissa.py agent_pass)."""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": True, "how": ["off"]}
    if not close_on_stop(e):
        return {"ok": True, "how": ["kept (%s=0)" % CLOSE_SWITCH]}
    p_ = port(e)
    closed: list = []
    for attempt in range(max(1, int(tries))):
        if attempt:
            sleep(1.0)
        if not up(p_, timeout=3.0):
            if attempt < tries - 1:
                continue
            return {"ok": True, "how": closed or ["not running"]}
        if not _hers_on_port(p_, home, plat):
            return {"ok": True, "how": ["not hers: the browser on her port runs on another profile"]}
        mine = [p for p in pages(p_, timeout=3.0) if hers(p, player_url)]
        if not mine:
            return {"ok": True, "how": closed or ["no page of the agent's"]}
        ws = ""
        for _ in range(2):
            ws = _browser_ws(p_)
            if ws:
                break
        if not ws:
            continue
        cdp(ws, [("Target.closeTarget", {"targetId": str(p.get("id") or "")}) for p in mine], 6.0)
        sleep(0.5)
        left = [p for p in pages(p_, timeout=3.0) if hers(p, player_url)]
        gone = [p for p in mine if p.get("id") not in {x.get("id") for x in left}]
        closed += ["closed %s" % _path(p.get("url")) for p in gone]
        if not left:
            return {"ok": True, "how": closed}
    return {"ok": False, "retry": True, "how": closed + ["still open after %d tries" % tries],
            "error": "her browser did not close every page of the agent's"}


# ── Setup in front of the desk Wall (round 15, 2026-10-12) ───────────────────────────────────────────────────────────
# .101, a fresh install's Setup: "i am trapped in init screen on .101. nothing will front the Wallpaper so no init screen
# for setup. I did see the brave tile flash at the top but it was hidden", and "they all show on the menu but wont front
# the wallpaper when clicked". The desk Wall set itself -topmost (and lifted itself) every half second while Init ran -
# the house's rule from 2026-09-12, when the Wall drew the whole walk itself and had to sit over the desktop's folders and
# dock. Since 2026-10-11 a pack's Setup runs in HER window (show(front=True) above), so a topmost Wall covered it and
# no window could come above it. WALL_TOP_SWITCH=1 is the old rule in a pack.
WALL_TOP_SWITCH = "CRISSA_WALL_WIZARD_TOP"
_SETUP_IN_WINDOW: dict = {}


def setup_in_own_window(env: dict | None = None, which=None, plat: str | None = None) -> bool:
    """A pack whose Setup runs in her own window: her window module is on here and this computer has a browser of her
    family to run it in (Firefox only: the desk Wall's own plates carry Setup, as before). Read once a process."""
    key = (id(env), plat)
    if key in _SETUP_IN_WINDOW and which is None:
        return _SETUP_IN_WINDOW[key]
    e = os.environ if env is None else env
    try:
        got = bool(enabled(e) and find(e, which, plat) is not None)
    except Exception:  # noqa: BLE001  unknown: the Wall keeps its old rule
        got = False
    if which is None:
        _SETUP_IN_WINDOW[key] = got
    return got


def wall_on_top_for_setup(status: str, *, house: bool, in_window: bool, env: dict | None = None) -> bool:
    """Does the desk Wall go above the other windows while Init runs? Only where it draws Setup itself: the house's Init,
    or a pack with no window of hers to run it in. A pack whose Setup is in her window: never - the Wall stays at the
    desktop layer under every window, so her Setup window is in front and the dock, the overview and the Wall's own
    SHOW CRISSA'S WINDOW key can bring it back. Pure: tests read it."""
    if str(status or "") != "running":
        return False
    if house:
        return True
    e = os.environ if env is None else env
    if str(e.get(WALL_TOP_SWITCH, "")).strip().lower() in ("1", "on", "true", "yes"):
        return True
    return not in_window


# IS SETUP IN FRONT OF HIM (round 38, the User via .101 round 37: her window was MINIMIZED and nothing on the Wall said
# Setup was mid-process): the Setup page's own document.visibilityState over CDP. A minimized window reads hidden; a
# window merely behind another usually still reads visible on Linux (no occlusion tracking there). Unread = visible.
# Kill CRISSA_WINDOW_VISIBLE_CHECK=0.
def visible_check_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_WINDOW_VISIBLE_CHECK", "1")).strip().lower() not in ("0", "off", "false", "no")


def _setup_visible(ps: list) -> bool:
    for p in ps:
        ws = p.get("webSocketDebuggerUrl")
        if not ws:
            continue
        try:
            got = cdp(ws, [("Runtime.evaluate", {"expression": "document.visibilityState", "returnByValue": True})],
                      timeout=1.0)
            v = (((got or [{}])[0].get("result") or {}).get("value"))
        except Exception:  # noqa: BLE001
            return True
        if v == "visible":
            return True
        if v is None:
            return True
    return False


def state(url: str, env: dict | None = None, now: float | None = None) -> dict:
    """{"up", "setup", "wall"}: her window is running, and shows Setup / her Wall now. Cached STATE_CACHE_S."""
    e = os.environ if env is None else env
    t = time.time() if now is None else now
    key = (port(e), _origin(url))
    if _CACHE["key"] == key and _CACHE["val"] is not None and t - _CACHE["at"] < STATE_CACHE_S:
        return dict(_CACHE["val"])
    if not enabled(e):
        val = {"up": False, "setup": False, "wall": False, "on": False}
    else:
        ps = [p for p in pages(key[0], 0.5) if hers(p, url)] if up(key[0], 0.4) else None
        val = {"up": ps is not None, "on": True,
               "setup": bool(ps) and any(_path(p.get("url")) == "/init" for p in ps),
               "wall": bool(ps) and any(_path(p.get("url")) == "/wall" for p in ps)}
        if val["setup"] and visible_check_on(e):
            val["visible"] = _setup_visible([p for p in ps if _path(p.get("url")) == "/init"])
    _CACHE.update(at=t, key=key, val=dict(val))
    return val


def ensure_wall(url: str, *, env: dict | None = None, popen=None, which=None, plat: str | None = None,
                home: Path | None = None, sleep=time.sleep, run=None) -> str:
    """THE DESK WALL IS OFF, SO HER BROWSER WALL STAYS OPEN SOMEWHERE (the User 2026-10-11): her window running with a
    page of hers, else her Wall opened - in a normal window, not brought forward, nothing closed. Returns what it did."""
    e = os.environ if env is None else env
    if not enabled(e):
        return "off"
    if _in_test(e) and popen is None:
        return "test"
    p_ = port(e)
    if up(p_):
        if any(hers(p, url) for p in pages(p_)):
            return "present"
        return "opened" if _open_target(p_, url, background=True) else "could not open"
    got = show(url, front=False, env=e, popen=popen, which=which, plat=plat, home=home, sleep=sleep, run=run)
    return "started" if got.get("ok") else "not started: %s" % (got.get("why") or "?")


# ── the dock entry (Linux desktops) ───────────────────────────────────────────────────────────────────────────────────
DESKTOP_ID = "crissa.desktop"
ICON_NAME = "crissa-icon.png"
DOCK_RECORD = "dock-entry.json"
PIN_SWITCH = "CRISSA_DOCK_PIN"


def apps_dir(env: dict | None = None) -> Path:
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_APPS_DIR") or "").strip()
    if got:
        return Path(got).expanduser()
    data = str(e.get("XDG_DATA_HOME") or "").strip()
    return (Path(data).expanduser() if data else Path.home() / ".local" / "share") / "applications"


def desktop_text(python: str, launcher: Path, icon: Path, port_offset: int = 0) -> str:
    """The dock entry: her name and icon; a press runs the launcher's --window (start Crissa if she is not running,
    then her own window on her Wall, or on Setup while Setup is due)."""
    def q(s: str) -> str:
        return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    exe = "%s %s --window" % (q(python), q(launcher))
    if port_offset:
        exe += " --port-offset %d" % int(port_offset)
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Crissa",
        "Comment=Crissa's Wall, in her own window",
        "Exec=%s" % exe,
        "Icon=%s" % icon,
        "Terminal=false",
        "StartupNotify=true",
        "StartupWMClass=%s" % WM_CLASS,
        "Categories=Utility;",
        "",
    ])


def make_icon(path: Path, size: int = 256) -> bool:
    """Her dock icon: the Wall's dark square with her name, the C in ember (as the house's own icon carried its Z).
    Pillow is the pack's base requirement; without it no icon is written (the entry then uses a generic one)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    inset = size // 22
    d.rounded_rectangle((inset, inset, size - inset - 1, size - inset - 1), radius=size // 6, fill=(0x10, 0x13, 0x1A, 255))
    first, rest = "C", "rissa"
    face = next((c for c in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                             "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                             "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf")
                 if os.path.isfile(c)), "")

    def font_of(px: int):
        if face:
            try:
                return ImageFont.truetype(face, px)
            except OSError:
                pass
        try:
            return ImageFont.load_default(size=px)
        except TypeError:                         # an older Pillow: its one bitmap size
            return ImageFont.load_default()
    px = int(size * 0.34)
    font = font_of(px)
    while px > 8 and d.textlength(first + rest, font=font) > size * 0.80:     # her whole name fits the square
        px -= 2
        font = font_of(px)
    w1 = d.textlength(first, font=font)
    w2 = d.textlength(rest, font=font)
    l, t, r, b = d.textbbox((0, 0), first + rest, font=font)
    x = (size - (w1 + w2)) / 2
    y = (size - (b - t)) / 2 - t
    d.text((x, y), first, font=font, fill=(0xD9, 0xA0, 0x5B, 255))
    d.text((x + w1, y), rest, font=font, fill=(0x8F, 0xB0, 0x96, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return True


def _favourites(run) -> list[str] | None:
    try:
        r = run(["gsettings", "get", "org.gnome.shell", "favorite-apps"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip()
    if getattr(r, "returncode", 1) != 0 or not out:
        return None
    if out.startswith("@as"):
        out = out[3:].strip()
    try:
        import ast
        got = ast.literal_eval(out)
    except (ValueError, SyntaxError):
        return None
    return [str(x) for x in got] if isinstance(got, (list, tuple)) else None


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def install_dock(python: str, launcher: Path, *, home: Path | None = None, env: dict | None = None, run=None,
                 port_offset: int = 0, pin: bool | None = None) -> dict:
    """Write her dock entry (~/.local/share/applications/crissa.desktop) and her icon, and pin her in GNOME's dock once:
    at the end of the person's favourites, never reordering them, and never again after the person unpins her
    (<state home>/dock-entry.json remembers). CRISSA_DOCK_PIN=0 writes the entry without pinning. Never on the house."""
    e = os.environ if env is None else env
    run = run or subprocess.run
    if crissa_home.is_house():
        return {"ok": False, "why": "the house has its own Wall entry"}
    h = _home(home)
    icon = h / ICON_NAME
    made_icon = make_icon(icon)
    f = apps_dir(e) / DESKTOP_ID
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(".%s.%d.tmp" % (f.name, os.getpid()))
    tmp.write_text(desktop_text(python, launcher, icon if made_icon else Path("utilities-terminal"), port_offset),
                   encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, f)
    try:
        run(["update-desktop-database", str(f.parent)], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    rec = _read(h / DOCK_RECORD)
    out = {"ok": True, "file": str(f), "icon": str(icon) if made_icon else "", "pinned": False}
    want_pin = (not _off(e.get(PIN_SWITCH, "1"))) if pin is None else bool(pin)
    if want_pin and not rec.get("pinned_once"):
        favs = _favourites(run)
        if favs is not None:
            if DESKTOP_ID not in favs:
                try:
                    r = run(["gsettings", "set", "org.gnome.shell", "favorite-apps", repr(favs + [DESKTOP_ID])],
                            capture_output=True, text=True, timeout=5)
                    out["pinned"] = getattr(r, "returncode", 1) == 0
                except (OSError, subprocess.SubprocessError):
                    out["pinned"] = False
            rec["pinned_once"] = True
            rec["pinned_by_crissa"] = bool(out["pinned"]) or bool(rec.get("pinned_by_crissa"))
    rec.update(file=str(f), at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    h.mkdir(parents=True, exist_ok=True)
    (h / DOCK_RECORD).write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return out


def remove_dock(*, home: Path | None = None, env: dict | None = None, run=None) -> dict:
    """Take her dock entry away: unpinned only when Crissa pinned it, the entry and the icon removed."""
    e = os.environ if env is None else env
    run = run or subprocess.run
    h = _home(home)
    rec = _read(h / DOCK_RECORD)
    out = {"ok": True, "removed": False, "unpinned": False}
    if rec.get("pinned_by_crissa"):
        favs = _favourites(run)
        if favs is not None and DESKTOP_ID in favs:
            try:
                r = run(["gsettings", "set", "org.gnome.shell", "favorite-apps",
                         repr([x for x in favs if x != DESKTOP_ID])], capture_output=True, text=True, timeout=5)
                out["unpinned"] = getattr(r, "returncode", 1) == 0
            except (OSError, subprocess.SubprocessError):
                pass
    for p in (apps_dir(e) / DESKTOP_ID, h / ICON_NAME, h / DOCK_RECORD):
        try:
            p.unlink()
            out["removed"] = out["removed"] or p.name == DESKTOP_ID
        except OSError:
            pass
    return out


def dock_installed(env: dict | None = None) -> bool:
    return (apps_dir(env) / DESKTOP_ID).is_file()


def dock_current(python: str, launcher: Path, port_offset: int = 0, *, env: dict | None = None,
                 home: Path | None = None) -> bool:
    """Her entry is there, names this install's launcher and Python, and its icon exists (a moved tree rewrites it)."""
    f = apps_dir(env) / DESKTOP_ID
    icon = _home(home) / ICON_NAME
    try:
        return icon.is_file() and f.read_text(encoding="utf-8") == desktop_text(python, launcher, icon, port_offset)
    except OSError:
        return False


# ── THE SHEET (2026-10-16, the User: "a display tool so she can show a document or lookup page in browser in 8x11 size
# on right side to match house tool") ─────────────────────────────────────────────────────────────────────────────────
# One window of her own browser, the house's letter: 8.5 x 11, on the right of the screen (letter_rect, the same formula
# as the house's doc_render_shift.letter_rect). It opens on the player's /letter (a document, as a letter) and is moved
# to a web address when she shows one. A new show reuses the window; a clear closes it. Kept ABOVE other windows
# (_NET_WM_STATE_ABOVE, X11/Xwayland, as the house's chips) unless CRISSA_SHEET_ABOVE=0.
SHEET = {"target": None}
ABOVE_SWITCH = "CRISSA_SHEET_ABOVE"


def letter_rect(screen: tuple | None) -> dict:
    """The 8.5 x 11 letter on the right: width min(40 % of the width, 93.6 % of the height x 8.5/11), 2 % in from the
    right, centred top to bottom but never above 6 % (the house's letter_rect)."""
    sw, sh = (screen or (1920, 1080))
    sw, sh = int(sw), int(sh)
    w = min(0.40 * sw, 0.936 * sh * 8.5 / 11.0)
    h = w * 11.0 / 8.5
    x = sw - 0.02 * sw - w
    y = max(0.06 * sh, (sh - h) / 2.0)
    return {"x": int(x), "y": int(y), "w": int(round(w)), "h": int(round(h))}


def x11_above(xid: int, display: str | None = None) -> bool:
    """Ask the window manager to keep `xid` above other windows (_NET_WM_STATE add _NET_WM_STATE_ABOVE, source 2)."""
    import ctypes
    from ctypes import Structure, Union, byref, c_char_p, c_int, c_long, c_ulong, c_void_p
    try:
        x = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    dpy = x.XOpenDisplay(display.encode() if display else None)
    if not dpy:
        return False

    class XClientMessageEvent(Structure):
        _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                    ("window", c_ulong), ("message_type", c_ulong), ("format", c_int), ("data", c_long * 5)]

    class XEvent(Union):
        _fields_ = [("xclient", XClientMessageEvent), ("pad", c_long * 24)]
    try:
        x.XInternAtom.restype = c_ulong
        x.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x.XDefaultRootWindow.restype = c_ulong
        x.XDefaultRootWindow.argtypes = [c_void_p]
        x.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
        x.XFlush.argtypes = [c_void_p]
        x.XCloseDisplay.argtypes = [c_void_p]
        ev = XEvent()
        ev.xclient.type = 33                 # ClientMessage
        ev.xclient.window = int(xid)
        ev.xclient.message_type = x.XInternAtom(dpy, b"_NET_WM_STATE", 0)
        ev.xclient.format = 32
        ev.xclient.data[0] = 1               # _NET_WM_STATE_ADD
        ev.xclient.data[1] = x.XInternAtom(dpy, b"_NET_WM_STATE_ABOVE", 0)
        ev.xclient.data[3] = 2               # source: a pager
        root = x.XDefaultRootWindow(dpy)
        x.XSendEvent(dpy, root, 0, (1 << 20) | (1 << 19), byref(ev))
        x.XFlush(dpy)
        return True
    finally:
        x.XCloseDisplay(dpy)


def x11_undecorate(xid: int, display: str | None = None) -> bool:
    """Ask the window manager to draw no frame on `xid` (_MOTIF_WM_HINTS: decorations 0)."""
    import ctypes
    from ctypes import c_char_p, c_int, c_long, c_ulong, c_void_p
    try:
        x = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False
    x.XOpenDisplay.restype = c_void_p
    x.XOpenDisplay.argtypes = [c_char_p]
    dpy = x.XOpenDisplay(display.encode() if display else None)
    if not dpy:
        return False
    try:
        x.XInternAtom.restype = c_ulong
        x.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x.XChangeProperty.argtypes = [c_void_p, c_ulong, c_ulong, c_ulong, c_int, c_int, c_void_p, c_int]
        x.XFlush.argtypes = [c_void_p]
        x.XCloseDisplay.argtypes = [c_void_p]
        atom = x.XInternAtom(dpy, b"_MOTIF_WM_HINTS", 0)
        hints = (c_long * 5)(2, 0, 0, 0, 0)          # flags: decorations; decorations: none
        x.XChangeProperty(dpy, int(xid), atom, atom, 32, 0, ctypes.cast(hints, c_void_p), 5)   # PropModeReplace
        x.XFlush(dpy)
        return True
    finally:
        x.XCloseDisplay(dpy)


def above_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return not _off(e.get(ABOVE_SWITCH, "1"))


def _keep_above(env: dict | None = None, run=None, send=None, plat: str | None = None, sleep=time.sleep) -> str:
    """Her active window (the sheet, just brought forward) kept above the rest. Returns what it did."""
    e = dict(os.environ if env is None else env)
    if not above_on(e):
        return "off"
    if not (plat or sys.platform).startswith("linux"):
        return "not Linux"
    if _in_test(e) and (run is None or send is None):
        return "test"
    if not str(e.get("DISPLAY") or "").strip():
        add, _why = session_x11(e, plat)
        e = dict(e, **add)
    if not str(e.get("DISPLAY") or "").strip():
        return "no X display"
    if run is None and not shutil.which("xprop"):
        return "no xprop"
    send = send or x11_above
    for _ in range(6):
        act = active_xid(e, run)
        if act and act in her_xids(e, run):
            return "above 0x%x" % act if send(act, e.get("DISPLAY")) else "could not ask"
        sleep(0.4)
    return "not active"


def show_sheet(player_url: str, url: str | None, bounds: dict | None, *, env: dict | None = None, **kw) -> dict:
    """The sheet window: the player's /letter in a window of her own (reused while it is open), moved to `url` when she
    shows a web address, placed at `bounds` when it is new, brought forward and kept above. {"ok", "how", ...}"""
    e = os.environ if env is None else env
    if not enabled(e):
        return {"ok": False, "why": "off", "message": "her own window is off here"}
    if _in_test(e) and kw.get("popen") is None:
        return {"ok": False, "why": "test", "message": "a test never opens a real browser"}
    sheet = player_url.rstrip("/") + "/letter"
    want = url or sheet
    p_ = port(e)
    page = None
    fresh = False
    if up(p_) and SHEET["target"]:
        page = next((p for p in pages(p_) if str(p.get("id") or "") == SHEET["target"]), None)
    if page is None:
        known = {str(p.get("id") or "") for p in pages(p_)} if up(p_) else set()
        if known and _open_app(sheet, e, p_, kill="CRISSA_SHEET_APP_WINDOW", **kw):
            # round 45 (.101 round 43 R43-2: her letter on the browser Wall opened as a full Brave window - tab strip,
            # address bar, bookmark star, sidebar): with her browser already running, Target.createTarget's new window
            # is a plain tabbed one. The sheet asks for an app window as her face window does (round 26): the launch
            # command, which the running browser takes as "open this app window". Kill CRISSA_SHEET_APP_WINDOW=0.
            end = time.time() + PAGE_WAIT_S
            while page is None and time.time() < end:
                page = next((p for p in pages(p_) if str(p.get("id") or "") not in known
                             and _path(p.get("url")) == "/letter" and hers(p, sheet)), None)
                if page is None:
                    time.sleep(0.25)
            if page is None:
                print("[window] the sheet's app window did not open a page: a plain window instead", flush=True)
        if page is None and up(p_):
            ws = _browser_ws(p_)
            got = cdp(ws, [("Target.createTarget", {"url": sheet, "newWindow": True})])[0] if ws else {}
            tid = str(got.get("targetId") or "")
            end = time.time() + PAGE_WAIT_S
            while tid and page is None and time.time() < end:
                page = next((p for p in pages(p_) if str(p.get("id") or "") == tid), None)
                if page is None:
                    time.sleep(0.25)
        elif page is None:
            out = show(sheet, front=True, env=e, **kw)
            if not out.get("ok"):
                return out
            page = next((p for p in pages(p_) if _path(p.get("url")) == "/letter" and hers(p, sheet)), None)
        fresh = True
    if page is None:
        return {"ok": False, "why": "no page", "message": "the sheet window did not open"}
    SHEET["target"] = str(page.get("id") or "")
    placed = False
    if fresh and bounds:
        wid, _st = _window_state(p_, SHEET["target"])
        ws = _browser_ws(p_)
        if wid is not None and ws:
            got = cdp(ws, [("Browser.setWindowBounds", {"windowId": int(wid), "bounds": {"windowState": "normal"}}),
                           ("Browser.setWindowBounds", {"windowId": int(wid), "bounds": {
                               "left": int(bounds["x"]), "top": int(bounds["y"]),
                               "width": int(bounds["w"]), "height": int(bounds["h"])}})])
            placed = not got[-1].get("error")
    out = _bring(p_, page, want, True)
    out["above"] = _keep_above(e)
    return dict(out, opened=fresh, placed=placed)


# A SETUP RUN AGAIN OPENS HER WINDOW (round 33, .101 10-19: "a forced Setup re-run did not open her window itself
# (opened by hand, as before)"). Only the launcher's start opened Setup in her window (open_setup_once), and only once
# per first boot (init-opened.json). A whole walk started again on the player keeps the window closed. That walk is
# "Set up again" / "Run Setup again", the desk Wall's restart key, or the menu's. Now the player brings her window
# forward on /init as such a walk starts, as the launcher does at a first boot, and marks it hers. Voice ID again
# (`again`) is not a whole walk. A pack only. Kill CRISSA_SETUP_AGAIN_WINDOW=0.
def setup_again_window_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return enabled(e) and not _off(e.get("CRISSA_SETUP_AGAIN_WINDOW", "1"))


def setup_again(player_url: str, act: str, body: dict | None, got: dict | None, *, env: dict | None = None,
                show_=None, mark=None) -> str:
    """After POST /v1/init: a forced whole walk now running brings her window forward on /init. Returns what it did."""
    if not setup_again_window_on(env):
        return "off"
    b = body if isinstance(body, dict) else {}
    a = str(act or "").strip().lower()
    if not (a == "restart" or (a == "start" and b.get("force"))):
        return ""
    g = got if isinstance(got, dict) else {}
    if g.get("status") != "running" or g.get("again"):
        return ""
    url = player_url.rstrip("/") + "/init"
    out = (show_ or show)(url, front=True, env=env)
    if out.get("ok") or out.get("launched"):
        if mark is None:
            import crissa_init
            mark = crissa_init.mark_browser_opened
        mark(url, by="window")
        return "opened"
    return "not opened: %s" % (out.get("message") or out.get("why") or "no window")


def close_sheet(env: dict | None = None) -> bool:
    """Close the sheet window, when one is open. True when one was closed."""
    e = os.environ if env is None else env
    tid = SHEET.get("target")
    SHEET["target"] = None
    if not tid or not enabled(e) or _in_test(e):
        return False
    p_ = port(e)
    if not up(p_):
        return False
    ws = _browser_ws(p_)
    res = cdp(ws, [("Target.closeTarget", {"targetId": tid})], 2.0) if ws else []
    return bool(res) and not res[0].get("error")
