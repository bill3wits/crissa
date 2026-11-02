#!/usr/bin/env python3
"""Crissa in one command (2026-10-05): the player, the voice, the ear and the aligner, one process group.

    python3 scripts/crissa.py [--face crissa|lila] [--audio-at host|page] [--port-offset N] [--asr-url URL]
                              [--no-voice] [--no-open] [--plan]
    python3 scripts/crissa.py --service install|status|uninstall [the same flags]     # the launcher as a service
    python3 scripts/crissa.py --uninstall                                             # = --service uninstall

What it starts, each under its own script name, all on 127.0.0.1 with ports from lib/crissa_ports:

    player   scripts/house-conductor.py               always; her picture (the render the Wall docks)
    wall     scripts/house-wall-ascii                 always; the desk. Linux: the desktop's background, under the
                                                      person's windows (dock and top bar stay); --fullscreen /
                                                      CRISSA_FULLSCREEN=1 only when the person chooses it (2026-10-10).
                                                      macOS: --fullscreen, Tk's full-screen window (no desktop layer
                                                      there); --windowed turns it off. A weak machine gets this Wall.
    panel    scripts/crissa-panel-server.py           always; the Control Panel on :8097, the Wall's panel.
                                                      Krew's tiles, or the "plugin not installed" placeholder, are its add-on slot.
    aligner  scripts/crissa-align.py                  when better mouths are on and installed
    tts      modules/voice/scripts/crissa-tts.py      when the voice module carries it and it is installed (Kokoro);
                                                      the voice seam's default once it answers; --tts names another
    turbo    modules/voice/scripts/crissa-turbo.py    when installed AND this machine's voice tier is Turbo (an NVIDIA
                                                      GPU with room; lib/crissa_voice_tiers), or --tts turbo; Kokoro
                                                      then is the GPU voice's announcer
    pocket   modules/voice/scripts/crissa-pocket.py   when installed AND Pocket is in this machine's voice chain (its
                                                      main on a CPU machine or a Mac, the fallback behind a GPU voice),
                                                      or --tts pocket
    asr      modules/voice/scripts/crissa-asr.py      when the voice module is here and its ASR is installed
    stt      modules/voice/scripts/crissa-stt.py      with the ASR: the microphone's turns
    ear      scripts/crissa-ear.py                    with the STT: each turn becomes a question to the agent

WHO RESTARTS A LANE (2026-10-06): the launcher does (lib/crissa_watch, config/crissa-watch.json). A lane that exits,
or whose /health stops answering, is started again with the reason in <crissa home>/logs/lanes.log, and the Control
Panel shows each lane (<crissa home>/lanes.json) and can restart one. krew's keeper only reads Crissa through the
player; it restarts nothing of Crissa's.

WHO RESTARTS THE LAUNCHER (2026-10-06): its service (lib/crissa_service). When the player dies the launcher stops the
group and exits 1; a systemd user unit (Linux: crissa-launcher.service, Restart=on-failure) or a launchd agent (macOS:
com.crissa.launcher, KeepAlive; unverified) starts it again. Init installs it when a first boot finishes; --service
install does it by hand and --uninstall removes it. Each run's end and why is in <crissa home>/launcher.json and
logs/launcher.log, and the Control Panel's launcher tile shows it with the service's state.

The desk is that Wall. It does not open the talking page (/wall). --no-open is kept for older commands.
CRISSA_INIT=1 in the environment turns on the guided first
boot (lib/crissa_init; runs once until <crissa home>/init.json says done; since 2026-10-29 a start with no init.json turns
it on by itself, first_start, and CRISSA_INIT=0 starts without it): Connect, the machine card,
Voice ID, face photos, Your room, Your hours, done. It opens at /init in HER OWN WINDOW (lib/crissa_window, 2026-10-11:
Crissa's own Brave / Chromium / Chrome instance, her profile under the state home, a normal window brought to the front;
never a tab in the person's own browser), and the Wall page sends a first boot there.

HER OWN WINDOW AND HER DOCK ENTRY (the User 2026-10-11): `--window` is the dock entry's press (start Crissa when she is
not running, then her own window on her Wall, or on Setup while Setup is due, brought forward); `--dock install|remove|
status` handles the entry, which a pack writes by itself at its first start on a Linux desktop (CRISSA_DOCK=0: not).
With the desk Wall off (--no-desk, or the Wall closed on purpose) her browser Wall stays open in a normal window; with
the desk Wall on her window is never forced open and never closed (CRISSA_KEEP_WINDOW=0: not kept open).

THE VOICE PICK WAITS FOR INIT (2026-10-10): while Init is still needed this launcher does not write the voice pick
(voice-tier.json); it plans from an unwritten pick, and Init's machine card writes the real one (after Allow or Skip).
The voice Init picks starts without a restart: every streamed voice this start did not start (multilingual, turbo,
pocket, audio8) is planned as a waiting lane (LATE_LANES, "starts when Init has installed it"), and Init asks this
launcher to start the one it chose through the request queue (lib/crissa_voice_install.launcher_start ->
lib/crissa_procs.request -> handle_request), whether it installed it just now or it was already here. A lane that
already runs answers "already running"; nothing is restarted.

REGISTER'S START NEEDS (2026-10-10): every child gets HOUSE_REGISTER_TOKEN=1 and RAIL_REGISTER_TOKEN=1 unless the
caller set them, so Init's Voice ID runs on Register's token path (the coach says register_token: true, presence lends
it the camera) and nobody sets a variable. Ctrl-C, a SIGTERM, or the player exiting stops the whole group, and the
lines and the aligner the player started on its own are stopped with it.

--audio-at page: the Wall page owns the microphone and the speakers (CRISSA_AUDIO_AT=page); `host`
(the default until the page path is proved) keeps this machine's player and input device.
--port-offset N moves every port by N, to run beside another install. A port that already answers is
refused before anything starts: this never binds over another service.
--tts-url names a voice already serving (crissa-tts on another machine; CRISSA_TTS_URL for the player)
and --asr-url an ASR that is already running (the /stt contract; another machine on the LAN) instead
of starting the bundled one. --plan prints what would start (JSON) and exits.

The voice module is looked for beside this file (modules/voice) or at --voice-dir / CRISSA_VOICE_DIR.
Logs: <crissa home>/logs/<name>.log. Exit 0 on a clean stop, 1 when the player died, 2 for a bad
setup, 3 when a port is taken.

THE CONTROL PANEL SEES EVERY PIECE (2026-10-06, the User: every service visible and controllable, failures named): the
launcher keeps <crissa home>/run/procs.json (lib/crissa_procs) - each child's pid, port, log, state and, when it
stops, why (exit code or signal plus the line in its log that says what went wrong) - and takes the panel's Restart,
Stop and Start for one child from <crissa home>/run/requests/. Restart stops that child alone (TERM, then KILL),
starts it again with the same command and checks it answers; the rest keep going. The player is her picture, docked in the Wall. The panel may restart it, never stop it.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
# THE PERSON'S ENVIRONMENT, AS THEY STARTED IT (2026-10-10): taken before anything is imported that changes it (OpenCV
# prepends its own library folder to LD_LIBRARY_PATH and sets Qt's), so the handoff carries what the person gave, and
# the service's own import adds the same once - not twice (the acceptance's service case compares the two modes live).
PERSON_ENV = dict(os.environ)

import crissa_egress  # noqa: E402  the one switch for what leaves (2026-10-05); off = urllib's own call
import crissa_ports  # noqa: E402
import crissa_service  # noqa: E402  who restarts the launcher itself (2026-10-06)
import crissa_watch  # noqa: E402  who restarts a lane that went down (2026-10-06)
import crissa_procs  # noqa: E402  the panel's view of every child + its Restart/Stop/Start (2026-10-06)
import crissa_addon_link  # noqa: E402  is the other plugin live? (the watcher crossover, 2026-10-06)

# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04, as in the ear): kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")

import crissa_avatar  # noqa: E402  which library she wears: the shipped faces + <home>/libraries, the panel's pick (2026-10-26)
FACES = dict(crissa_avatar.SHIPPED)   # --face choices; the pick in <home>/avatar.json may name a library beyond these
BOOT_WAIT_S = 120.0
STOP_WAIT_S = 8.0
# what the player itself starts in a session of its own: stopped by command line, inside this tree only
STRAYS = ("scripts/crissa-align.py", "scripts/house-speak.py")


# THE APP'S OWN PACKAGES ARE CHECKED BEFORE ANYTHING STARTS (the User 2026-10-07 Ubuntu retest: a fresh clone without
# them stopped with a cv2 traceback from the player). The base requirements.txt every pack carries (the export's
# REQUIREMENTS), by import name; a test pins the two lists together.
# OpenCV is installed here, then checked again, before any child starts (the User 2026-10-08). A .venv beside this
# tree that already has the packages is used. If they are missing, they are installed into that .venv. If the
# install fails, or cv2 still does not import, one plain line and exit 2. Nothing starts.
# Kill CRISSA_PACKAGE_CHECK=0 (no check), CRISSA_USE_VENV=0 (never switch to .venv),
# CRISSA_PACKAGE_INSTALL=0 (do not download; print the one command, the 2026-10-07 message).
APP_PACKAGES = (("numpy", "numpy"), ("cv2", "opencv-python-headless"), ("PIL", "Pillow"))   # = every pack's base list
SETUP_CMD = "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
# What the launcher installs beside them but never refuses to start without (the User 2026-10-20, "I would like to
# include the QR code"): segno draws the QR on the menu's Your phone card (lib/crissa_phone_card.py). Running in the
# tree's .venv (where the base install lands and restarts into), a missing extra is installed at start; a failure is
# one line, never a stop. = the player's requirements beyond the base (the export's PLAYER_REQUIREMENTS); a test pins
# the two.
# Kill CRISSA_EXTRA_INSTALL=0 (say the command, install nothing).
EXTRA_PACKAGES = (("segno", "segno==1.6.6"),)


def missing_packages(python: str | None = None) -> list[str]:
    """The pip names of the app packages this Python cannot import (a broken install counts as missing).
    `python` asks another interpreter (a .venv) in a child process; None = this one."""
    if python:
        code = ("import importlib,sys\nfor m in sys.argv[1:]:\n    try: importlib.import_module(m)\n"
                "    except Exception: print(m)")
        try:
            r = subprocess.run([python, "-c", code, *[m for m, _ in APP_PACKAGES]], capture_output=True, text=True,
                               timeout=60, cwd=str(ROOT))
        except (OSError, subprocess.SubprocessError):
            return [pip for _, pip in APP_PACKAGES]
        if r.returncode != 0:
            return [pip for _, pip in APP_PACKAGES]
        bad = set(r.stdout.split())
        return [pip for m, pip in APP_PACKAGES if m in bad]
    import importlib
    out = []
    for mod, pip in APP_PACKAGES:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001  ImportError, or a wheel that will not load (a missing system library)
            out.append(pip)
    return out


def missing_extras() -> list[str]:
    """The pip specs of EXTRA_PACKAGES this Python cannot import."""
    import importlib
    out = []
    for mod, pip in EXTRA_PACKAGES:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001  as missing_packages: a broken install counts as missing
            out.append(pip)
    return out


def extras_ready(env=None, install=None) -> list[str]:
    """After packages_ready: install the extras this Python lacks, only into the tree's own .venv and only when
    running in it (a system Python is never installed into). Never stops Crissa; returns what is still missing."""
    env = os.environ if env is None else env
    if str(env.get("CRISSA_PACKAGE_CHECK", "1")).strip() == "0":
        return []
    miss = missing_extras()
    if not miss:
        return []
    vpy = venv_python()
    if (_in_venv(vpy) and str(env.get("CRISSA_EXTRA_INSTALL", "1")).strip() != "0"
            and str(env.get("CRISSA_PACKAGE_INSTALL", "1")).strip() != "0"):
        print("Installing segno (the QR code on Your phone)…", flush=True)
        ok, note = (install or install_app_packages)(miss)
        if ok:
            import importlib
            importlib.invalidate_caches()
            miss = missing_extras()
            if not miss:
                return []
        else:
            print("Crissa: could not install %s (%s); Your phone will show the address and no QR code."
                  % (", ".join(miss), note), file=sys.stderr, flush=True)
            return miss
    print("Crissa: Your phone will show the address and no QR code until %s is installed:  %s -m pip install %s"
          % (", ".join(miss), sys.executable, " ".join(miss)), file=sys.stderr, flush=True)
    return miss


def venv_python(root: Path = ROOT) -> Path | None:
    """The README's .venv beside this tree, when there is one (Linux/macOS layout, then Windows)."""
    for rel in (".venv/bin/python", ".venv/bin/python3", ".venv/Scripts/python.exe"):
        if (root / rel).is_file():
            return root / rel
    return None


def _in_venv(vpy: Path | None) -> bool:
    """Is this process already that venv? By its prefix: a venv's python is a symlink to the base interpreter."""
    return vpy is not None and Path(sys.prefix).resolve() == vpy.parent.parent.resolve()


def _pip_tail(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "pip failed")[:300]


def install_app_packages(miss: list[str], root: Path = ROOT, run=None) -> tuple[bool, str]:
    """Install the missing app packages into the tree's .venv. (ok, one plain line). Never raises."""
    run = subprocess.run if run is None else run
    try:
        vpy = venv_python(root)
        if vpy is None:
            made = run([sys.executable, "-m", "venv", str(root / ".venv")],
                       capture_output=True, text=True, timeout=180)
            if getattr(made, "returncode", 1) != 0:
                return False, _pip_tail(getattr(made, "stderr", "") or getattr(made, "stdout", ""))
            vpy = venv_python(root)
            if vpy is None:
                return False, "could not create .venv"
        inst = run([str(vpy), "-m", "pip", "install", "--disable-pip-version-check", *miss],
                   capture_output=True, text=True, timeout=600)
        log_path = root / ".venv" / "install.log"
        try:
            log_path.write_text((getattr(inst, "stdout", "") or "") + (getattr(inst, "stderr", "") or ""),
                                encoding="utf-8")
        except OSError:
            pass
        if getattr(inst, "returncode", 1) != 0:
            return False, _pip_tail(getattr(inst, "stderr", "") or getattr(inst, "stdout", ""))
        return True, "installed"
    except (OSError, subprocess.SubprocessError) as e:
        return False, type(e).__name__


def opencv_error(miss: list[str], why: str) -> str:
    """One plain line. OpenCV is named when it is the package that failed."""
    if "opencv-python-headless" in miss:
        return "Crissa could not install OpenCV (cv2): %s. Crissa did not start." % why
    return "Crissa could not install what it needs (%s): %s. Crissa did not start." % (", ".join(miss), why)


def packages_ready(argv: list, env=None, execv=None, install=None) -> int | None:
    """None when this Python carries the app's packages (or the check is off). Otherwise install them into
    .venv and start again in that Python, or switch to a .venv that already has them. A failed install, or a
    check that still cannot import cv2, prints one plain line and returns 2. Never a traceback."""
    env = os.environ if env is None else env
    if str(env.get("CRISSA_PACKAGE_CHECK", "1")).strip() == "0":
        return None
    miss = missing_packages()
    if not miss:
        return None
    if str(env.get("CRISSA_PACKAGE_INSTALLED") or "").strip() == "1":
        print(opencv_error(miss, "it was installed and the check still cannot import it"), file=sys.stderr, flush=True)
        return 2
    vpy = venv_python()
    inside = _in_venv(vpy)
    if (vpy is not None and str(env.get("CRISSA_USE_VENV", "1")).strip() != "0"
            and not inside and not env.get("CRISSA_VENV_SWITCHED")
            and not missing_packages(str(vpy))):
        print("Crissa: using the packages in %s" % vpy.parent.parent, flush=True)
        os.environ["CRISSA_VENV_SWITCHED"] = "1"           # never twice
        (execv or os.execv)(str(vpy), [str(vpy), str(Path(__file__).resolve()), *argv])
        return 2                                          # only a stubbed execv gets here
    if str(env.get("CRISSA_PACKAGE_INSTALL", "1")).strip() == "0":
        print("Crissa needs its packages installed first (missing: %s).\n"
              "Run this once, in %s:\n\n    %s\n\nthen start Crissa again:  python3 scripts/crissa.py"
              % (", ".join(miss), ROOT, SETUP_CMD), file=sys.stderr, flush=True)
        if vpy is not None:
            print("(%s is there but is missing packages too: run the second half, .venv/bin/pip install -r "
                  "requirements.txt)" % vpy.parent.parent, file=sys.stderr, flush=True)
        return 2
    # Install what the venv cannot import. This interpreter can already have Pillow
    # (the system Python did, on the clean room) while a new .venv does not see it.
    # Installing only this interpreter's gaps, then refusing a second try, stopped
    # the desk before the Wall or the voice started.
    if vpy is not None and not inside:
        into = missing_packages(str(vpy))
    elif vpy is None:
        into = [pip for _, pip in APP_PACKAGES]
    else:
        into = miss
    print("Installing OpenCV (cv2)…", flush=True)
    ok, note = (install or install_app_packages)(into)
    if not ok:
        print(opencv_error(into, note), file=sys.stderr, flush=True)
        return 2
    os.environ["CRISSA_PACKAGE_INSTALLED"] = "1"
    vpy = venv_python()
    if vpy is not None and not _in_venv(vpy):
        (execv or os.execv)(str(vpy), [str(vpy), str(Path(__file__).resolve()), *argv])
        return 2
    still = missing_packages()
    if still:
        print(opencv_error(still, "it was installed and the check still cannot import it"), file=sys.stderr, flush=True)
        return 2
    return None


def addon_pass(home, last: dict | None = None, log=None) -> dict:
    """One watcher pass of the add-on link (lib/crissa_addon_link): is each expected add-on installed and live,
    and - only while it is - are its pieces up. Recorded in the launcher's table with the heartbeat, so the panel
    shows it; a change (installed, live, a piece down) is said once in the launcher's log. Never raises."""
    try:
        cfg, mans = crissa_addon_link.expected(home)
        ls = crissa_addon_link.links(cfg, home, mans)
    except Exception as e:  # noqa: BLE001 - a broken add-on never stops Crissa
        ls = [{"id": "?", "live": False, "installed": None, "pieces": [], "down": [],
               "note": "add-on link unreadable (%s) - watching Crissa only" % type(e).__name__}]
    before = {x.get("id"): (x.get("live"), tuple(x.get("down") or ())) for x in (last or {}).get("links") or []}
    for x in ls:
        if before.get(x.get("id")) != (x.get("live"), tuple(x.get("down") or ())) and log:
            log("add-on  " + x.get("note", "") + ("; down: " + ", ".join(x["down"]) if x.get("down") else ""))
    return {"links": ls, "checked": round(time.time(), 1), "note": crissa_addon_link.note(ls),
            "table": crissa_addon_link.watch_table(ls, True)}


# A FEW PLAIN LINES ON THE CONSOLE, EVERY DETAIL IN THE FULL LOG (the User 2026-10-07 Ubuntu retest: "The startup log is
# too technical for a new user"; the User's rule: any cut says it is cut and points to the full log). Every line the
# launcher has always printed goes to <crissa home>/logs/crissa.log; the console shows only a line's plain form, an
# error in plain words, and - wherever something was left out - "more in <full log>". --verbose (or
# CRISSA_VERBOSE=1) prints every line as before, plain lines too.
class Console:
    def __init__(self):
        self.verbose = False
        self.path: Path | None = None
        self.cut = 0                         # lines held back since the console last named the full log

    def write(self, line: str) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
        except OSError:
            pass

    def more(self) -> str:
        return "more in %s" % self.path


CON = Console()


def _log(msg: str, plain: str | None = None, error: bool = False) -> None:
    """One launcher line: always to the full log; to the console as before under --verbose, else its plain form
    (an error's plain form, or the line itself, always shows), else held back and counted."""
    line = "crissa: " + msg
    CON.write(line)
    if CON.verbose:
        print(line, flush=True)
    elif error:
        print("%s (%s)" % (plain or msg, CON.more()) if CON.path else (plain or msg), flush=True)
    elif plain:
        print(plain, flush=True)
    else:
        CON.cut += 1


def _say(plain: str) -> None:
    """A line written for the console alone (its detail is in the lines around it): the full log keeps it too."""
    CON.write("crissa: " + plain)
    if not CON.verbose:
        print(plain, flush=True)


def _cut_note() -> None:
    """Whenever lines were held back, say so and name the full log."""
    if CON.cut and not CON.verbose:
        print("(%d more line%s not shown here: the full log is %s; --verbose prints everything)"
              % (CON.cut, "" if CON.cut == 1 else "s", CON.path or "not written"), flush=True)
        CON.cut = 0


PLAIN_LANES = {"player": "her picture", "wall": "the Wall", "panel": "the Control Panel",
               "presence": "who is in the room",
               "tts": "her voice (Kokoro)", "turbo": "her voice (Turbo)",
               "pocket": "her voice (Pocket)", "multilingual": "her voice (Multilingual)",
               "audio8": "the Audio8 voice",
               "asr": "speech recognition", "stt": "listening", "ear": "the ear", "bridge": "the ear bridge",
               "aligner": "the mouth aligner",
               "warm": "the voice warmer", "phone": "her phone app", "knowledge": "what she knows"}


def open_page(page: str, plat: str | None = None, which=None, popen=None, log=None) -> bool:
    """Open the Wall in the person's browser (2026-10-07). The browser's own output - Chromium and Brave print
    "Failed sending zygote boot message: Broken pipe" and the like - goes to the full log line by line ("browser: ..."),
    and under --verbose to the console as well (_log), so nothing is hidden and nothing lands in the middle of the
    console's plain lines. The opener runs in its own session with SIGPIPE left ignored (Python's own setting, kept
    across exec: restore_signals=False), so a browser still writing after Crissa stops gets an error back, never a
    signal. No opener on this machine: Python's webbrowser, as before. True when something was asked to open it."""
    import shutil
    import threading
    plat = plat or sys.platform
    which = which or shutil.which
    popen = popen or subprocess.Popen
    log = log or (lambda line: _log("browser: " + line))
    if plat.startswith("win"):
        os.startfile(page)  # type: ignore[attr-defined]  # noqa: S606  Windows: the shell opens it, no output
        return True
    opener = which("open") if plat == "darwin" else (which("xdg-open") or which("gio"))
    if not opener:
        return bool(webbrowser.open(page))
    argv = [opener, "open", page] if Path(opener).name == "gio" else [opener, page]
    proc = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 start_new_session=True, restore_signals=False)

    def _drain() -> None:
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    log(line)
        except (OSError, ValueError):
            pass
    threading.Thread(target=_drain, name="browser-output", daemon=True).start()
    return True


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def lane_plain(m: str) -> tuple[str | None, bool]:
    """A watch event ("<lane> <what>") in plain words: (line or None to hold it back, is it an error)."""
    lane, _, what = m.partition(" ")
    who = PLAIN_LANES.get(lane, lane)
    who = _cap(who)
    if what.startswith("went down"):
        return "%s stopped; Crissa is starting it again" % who, True
    if what.startswith("answering again"):
        return "%s is back." % who, False
    if what.startswith("given up"):
        return "%s keeps stopping, so Crissa stopped restarting it; restart it from the Control Panel" % who, True
    return None, False


def face_name(a) -> str:
    """The face as the person reads it: her own name for the `crissa` face (lib/crissa_home.her_name: Crissa in a pack;
    .101 2026-10-11 read "Crissa's voice: Pocket TTS"), else the face's own name (Lila)."""
    face = str(getattr(a, "face", "crissa") or "crissa")
    if face == "crissa":
        try:
            import crissa_home
            return crissa_home.her_name()
        except Exception:  # noqa: BLE001  a missing module only loses the house's own name
            return "Crissa"
    return face.capitalize()


def voice_plain(a, installing: bool = False) -> str:
    """'Crissa's voice: Pocket TTS (installing in the background)', from the voice tier; plain, no engine flags."""
    who = "%s's voice" % face_name(a)
    if getattr(a, "no_voice", False):
        return "%s: not started (--no-voice)" % who
    if not getattr(a, "voice", None):
        return "%s: the voice module is not here, so she answers what is typed" % who
    if getattr(a, "tts_url", None):
        return "%s: the one serving at %s" % (who, a.tts_url)
    eng = (getattr(a, "tts", "") or "").strip().lower() or str((getattr(a, "tier", None) or {}).get("main") or "")
    if eng == "text":
        return "%s: none on this machine, so she answers in text on screen" % who
    name = eng or "the default"
    try:
        import crissa_voice_install as VI
        name = VI.label(eng) if eng else name
    except Exception:  # noqa: BLE001  a missing module only loses the nicer name
        pass
    return "%s: %s%s" % (who, name, " (installing in the background; progress on the Connect page)"
                         if installing else "")


def sound_tools_missing(a, which=None) -> list:
    """The programs her voice plays through on this computer that are not installed (2026-10-07, .101: with them
    missing every line failed as "no speech engine answered" while her voice was serving). Linux with the voice
    playing on the computer (--audio-at host): pactl and paplay (pulseaudio-utils). [] elsewhere, or with no voice."""
    import shutil
    if getattr(a, "no_voice", False) or not getattr(a, "voice", None) or getattr(a, "audio_at", "host") == "page":
        return []
    if not sys.platform.startswith("linux"):
        return []
    have = which or shutil.which
    return [t for t in ("pactl", "paplay") if not have(t)]


def sound_tools_plain(missing: list) -> str:
    return ("Her voice cannot play on this computer: %s %s not installed (they come with pulseaudio-utils), so "
            "she answers in text on screen until %s." % (" and ".join(missing), "is" if len(missing) == 1 else "are",
                                                         "it is" if len(missing) == 1 else "they are"))


def voice_dir(a) -> Path | None:
    for cand in (a.voice_dir, os.environ.get("CRISSA_VOICE_DIR"), str(ROOT / "modules" / "voice")):
        if cand and (Path(cand) / "scripts" / "crissa-stt.py").is_file():
            return Path(cand).resolve()
    return None


def ear_module(a, env_in: dict | None = None) -> Path | None:
    """modules/ear when it is here, installed (its env under the state home) and not opted out; else None."""
    env_in = os.environ if env_in is None else env_in
    if getattr(a, "no_ear_module", False) or str(env_in.get("CRISSA_EAR_MODULE", "1")).strip().lower() in ("0", "off", "false", "no"):
        return None
    edir = ROOT / "modules" / "ear"
    if not (edir / "scripts" / "crissa-stt.py").is_file():
        return None
    try:
        if str(edir) not in sys.path:
            sys.path.insert(0, str(edir))
        from crissa_ear import setup as ear_setup
        if not ear_setup.installed():
            return None
    except Exception:  # noqa: BLE001
        return None
    return edir


def asr_installed(vdir: Path) -> bool:
    sys.path.insert(0, str(vdir))
    try:
        from crissa_voice import asr_setup
        return bool(asr_setup.installed())
    except Exception:  # noqa: BLE001  a broken module is a missing one here; the mark says so
        return False


def tts_installed(vdir: Path) -> bool:
    """The resident voice (modules/voice/scripts/crissa-tts.py, Kokoro) is here and installed."""
    if not (vdir / "scripts" / "crissa-tts.py").is_file():
        return False
    sys.path.insert(0, str(vdir))
    try:
        from crissa_voice import kokoro_setup
        return bool(kokoro_setup.installed())
    except Exception:  # noqa: BLE001
        return False


def module_installed(vdir: Path, engine: str) -> bool:
    """A streamed voice of the voice module (modules/voice/scripts/crissa-<engine>.py) is here and installed."""
    if not (vdir / "scripts" / ("crissa-%s.py" % engine)).is_file():
        return False
    sys.path.insert(0, str(vdir))
    try:
        import importlib
        return bool(importlib.import_module("crissa_voice.%s_setup" % engine).installed())
    except Exception:  # noqa: BLE001
        return False


def voice_tier(write: bool = True) -> dict:
    """THE VOICE TIER (2026-10-06, the User: the voice is chosen by what the machine can handle). The first start probes
    this machine once and keeps the pick (lib/crissa_voice_tiers.ensure); later starts read it. --plan never writes."""
    try:
        import crissa_voice_tiers as VT
    except ImportError:
        return {}
    try:
        if write:
            try:                                     # the announcer follows the card's ladder (2026-10-10, .101)
                import crissa_machine
                crissa_machine.fit_announcer()
            except Exception:  # noqa: BLE001
                pass
            return VT.ensure()
        rec = VT.clean_record(VT.read())             # Audio8 is her ear, never a voice (the User 2026-10-17)
        return rec if rec else VT.pick(VT.probe(), VT.table())
    except Exception as e:  # noqa: BLE001  a broken probe never stops her: the seam's own order still runs
        return {"say": ["voice: the machine probe failed (%s); the default order runs" % type(e).__name__]}


def pick_tier(a, env=None) -> dict:
    """The voice tier this start plans from. While Init is still needed it is NOT written here (THE VOICE PICK WAITS
    FOR INIT, 2026-10-10): Init's machine card writes it after Allow or Skip; this start plans from an unwritten pick
    and every voice it did not start waits as a late lane."""
    if a.no_voice:
        return {}
    return voice_tier(write=not a.plan and not init_needed(env))


def warm_wanted() -> bool:
    try:
        import crissa_voice_warm
        return bool(crissa_voice_warm.target()[0])
    except Exception:  # noqa: BLE001
        return False


def aligner_wanted() -> bool:
    try:
        import crissa_aligner
        return bool(crissa_aligner.applicable() and crissa_aligner.better() and crissa_aligner.installed())
    except Exception:  # noqa: BLE001
        return False


def fullscreen_wanted(a=None, env=None, plat: str | None = None) -> bool:
    """Is the desk Wall full screen? Only when the person chose it (the User 2026-10-10, .101: a full-screen Wall covered
    GNOME's dock and top bar and nothing said how to leave): --fullscreen or CRISSA_FULLSCREEN=1 on Linux. A Mac keeps
    the full-screen window it had (2026-10-08: no desktop layer there; F11 or the green button leaves it), and
    --windowed or CRISSA_FULLSCREEN=0 turns it off there too."""
    plat = sys.platform if plat is None else plat
    e = os.environ if env is None else env
    if a is not None and getattr(a, "fullscreen", None) is not None:
        return bool(a.fullscreen)
    raw = str(e.get("CRISSA_FULLSCREEN") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return plat == "darwin"


def desk_wall(py: str, plat: str | None = None, fullscreen: bool | None = None) -> tuple[list[str], str]:
    """The ASCII Wall as the desk, on every machine, including a weak one. Nothing here reads RAM or the voice tier.

    Linux: the Wall's own default, a desktop-background window below every other window: the dock, the top bar and
    the person's windows stay where they are. --fullscreen only when the person chose it (fullscreen_wanted).
    macOS: Tk's full-screen window (no desktop layer there), unless the person turned it off.
    Either way the Wall's Crissa key (bottom left) opens the menu: Help names the keys (Ctrl+Q closes the Wall, F11
    leaves full screen) and Stop Crissa stops everything.
    """
    plat = sys.platform if plat is None else plat
    fs = (plat == "darwin") if fullscreen is None else bool(fullscreen)
    argv = [py, str(ROOT / "scripts" / "house-wall-ascii")] + (["--fullscreen"] if fs else [])
    if fs and plat == "darwin":
        note = ("On a Mac the desk is a full-screen window. F11 leaves full screen; the Crissa key at the bottom left "
                "has the menu.")
    elif fs:
        note = "The desk is a full-screen Wall: F11 leaves full screen, Ctrl+Q closes the Wall, the Crissa key has the menu."
    else:
        note = ("The desk is the Wall as your desktop background, under your windows. The Crissa key at its bottom left "
                "has the menu.")
    return argv, note


def no_desk(a=None, env=None) -> bool:
    """--no-desk, or CRISSA_NO_DESK=1: start everything but the Tk desk (the Wall page in a browser is the screen;
    a headless host or a sandbox has none). 2026-10-08: the full desk from one command made the launcher need a
    screen, and the page acceptance - a sandbox with no X - could no longer run it."""
    e = os.environ if env is None else env
    if a is not None and getattr(a, "no_desk", False):
        return True
    return str(e.get("CRISSA_NO_DESK") or "").strip().lower() in ("1", "true", "yes", "on")


def screen_problem(plat: str | None = None, env=None, tk_ok: bool | None = None) -> str | None:
    """None when the desk can draw. Else one plain sentence, and the launcher starts nothing."""
    plat = sys.platform if plat is None else plat
    env = os.environ if env is None else env
    if str(plat).startswith("linux") and not (str(env.get("DISPLAY") or "").strip()
                                              or str(env.get("WAYLAND_DISPLAY") or "").strip()):
        return "Crissa needs a screen, and this session has no display, so the desk did not start."
    if tk_ok is None:
        try:
            import tkinter  # noqa: F401  the Wall's window; a missing Tk is a plain error, not a traceback
            tk_ok = True
        except Exception:  # noqa: BLE001
            tk_ok = False
    if not tk_ok:
        return "Crissa needs Tk to draw the Wall, and this Python does not have it, so the desk did not start."
    return None


def mouth_env(face: str, root: Path | None = None) -> dict:
    """The shipped mouth defaults for this face. A missing or broken file is no defaults, never a crash.

    setdefault in plan() so a variable already in the environment wins. The conductor reads these at
    import, so they have to be on the player process, not only on a later house-speak spawn."""
    path = (ROOT if root is None else root) / "config" / "crissa-mouth.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in (data.get("env") or {}).items():
        if isinstance(k, str) and v is not None:
            out[k] = str(v)
    face_env = (data.get("faces") or {}).get(face) if isinstance(data.get("faces"), dict) else None
    if isinstance(face_env, dict):
        for k, v in face_env.items():
            if isinstance(k, str) and v is not None:
                out[k] = str(v)
    return out


def plan(a, env_in: dict | None = None, feed_script: Path | None = None) -> dict:
    """Every child: name, argv, the env it adds, the port it binds (None for the ear). Pure: no process starts."""
    env_in = os.environ if env_in is None else env_in
    off = dict(env_in, CRISSA_PORT_OFFSET=str(a.port_offset)) if a.port_offset else dict(env_in)
    ports = {k: crissa_ports.port(k, off) for k in crissa_ports.DEFAULTS}
    py = sys.executable
    common = dict(crissa_ports.env_for(ports), CRISSA_AUDIO_AT=a.audio_at)
    # EVERY ADDRESS FROM THE PORT TABLE (2026-10-10, the trial beside the reference install): the pieces read their
    # neighbours from variables whose defaults were the house's ports, so each child gets this install's from
    # lib/crissa_ports.ADDRESS_KEYS; a value the caller set wins, and a child's own line below still overrides.
    for key, val in crissa_ports.addresses(ports).items():
        if not str(env_in.get(key) or "").strip():
            common[key] = val
    # NO CAMERA (2026-10-10): --no-camera / CRISSA_NO_CAMERA=1 reaches every child, so nothing talks to a camera - the
    # presence service's calls answer {"camera": "off"}, the oracle opens no device, the feed's camera cell says so.
    if getattr(a, "no_camera", False):
        common["CRISSA_NO_CAMERA"] = "1"
    # REGISTER'S START NEEDS (the User 2026-10-10: "satisfied by the pack itself"): the token path for every child
    for key in ("HOUSE_REGISTER_TOKEN", "RAIL_REGISTER_TOKEN"):
        if not str(env_in.get(key) or "").strip():
            common[key] = "1"
    player_url = "http://127.0.0.1:%d" % ports["player"]
    kids = []
    if a.with_aligner:
        kids.append({"name": "aligner", "port": ports["align"], "argv": None,   # its own env's python: see run()
                     "env": dict(common)})
    # WHICH LIBRARY SHE WEARS (the User 2026-10-26): the Control Panel's pick in <home>/avatar.json wins over --face
    # when the picker is on and that library is still there (a store hub, a hub of theirs under <home>/libraries);
    # else the face named on the command line. The same read serves a player restart (apply_avatar).
    import crissa_home
    face, pose_dir = crissa_avatar.pose_dir(ROOT, crissa_home.HOME, a.face, env_in)
    a.face = face
    player_env = dict(common, CRISSA_AUTHORING="0", HOUSE_CONDUCTOR_OWN_MAILBOX="1",
                      HOUSE_POSE_DIR=pose_dir, CRISSA_WALL_PAGE="1")
    # A LIBRARY OF ANOTHER SIZE PAINTS AT ITS OWN (the User 2026-10-26, the 480p store hub, 848x480): the player gets
    # HOUSE_FRAME_FROM_LIBRARY for a library whose inventory names a size other than the pack's native frames
    # (crissa_avatar.player_env); a variable already in the environment wins, as for the mouth defaults below.
    for key, val in crissa_avatar.player_env(pose_dir, env_in).items():
        if not (key in env_in and env_in.get(key) is not None):
            player_env.setdefault(key, val)
    for key, val in mouth_env(a.face).items():
        # env_in is the process environment (os.environ when the launcher calls plan). A named
        # variable there wins and is copied onto the player; the json fills only what is absent.
        if key in env_in and env_in.get(key) is not None:
            player_env[key] = str(env_in[key])
        else:
            player_env.setdefault(key, val)
    if a.with_aligner:
        # ONE ALIGNER, THE LAUNCHER'S (round 17, 2026-10-13, .101 08:28): the lane owns its port; the player never
        # starts its own beside it (lib/crissa_aligner.lane_owned; kill CRISSA_ALIGNER_ONE_OWNER=0)
        player_env["CRISSA_ALIGNER_LANE"] = "1"
    # A hand start holds the ports (CRISSA_DESK_HELD=1), so Init installs the unit and does not start a
    # second one. A start that already is the service keeps CRISSA_SERVICE, and Init leaves it alone.
    if crissa_service.under(env_in) == "hand":
        player_env.setdefault("CRISSA_DESK_HELD", "1")
    elif env_in.get("CRISSA_SERVICE"):
        player_env.setdefault("CRISSA_SERVICE", str(env_in["CRISSA_SERVICE"]))
    kids.append({"name": "player", "port": ports["player"],
                 "argv": [py, str(ROOT / "scripts" / "house-conductor.py")],
                 "env": player_env})
    vdir = a.voice
    # WHICH VOICE SPEAKS IS DECIDED IN ONE PLACE, lib/crissa_tts, NEVER HERE (the User 10-05, relayed by Thread 1).
    # Its unnamed default since 2026-10-06 (crissatest): Kokoro when it answers, else `say` on a Mac, else text only
    # (kill CRISSA_TTS_DEFAULT_KOKORO=0). So the resident voice is STARTED when installed - installing it is the
    # person's choice - and named for the player only when the person named it here (--tts) or named a remote one
    # (--tts-url). A CRISSA_TTS already in the environment, or agent.json's engine, reaches the seam as is.
    named = (getattr(a, "tts", None) or "").strip().lower()
    tts_url = getattr(a, "tts_url", None) or ""
    if named:
        common["CRISSA_TTS"] = named
    if tts_url:                                      # a voice already serving elsewhere (another machine on the LAN)
        common.update(CRISSA_TTS=named or env_in.get("CRISSA_TTS") or "kokoro", CRISSA_TTS_URL=tts_url)
    elif vdir is not None and getattr(a, "with_tts", False):
        kids.append({"name": "tts", "port": ports["tts"],
                     "argv": [py, str(vdir / "scripts" / "crissa-tts.py"), "--port", str(ports["tts"])],
                     "env": dict(common)})
    for eng in ("multilingual", "turbo", "pocket"):
        if vdir is not None and not tts_url and getattr(a, "with_" + eng, False):
            kids.append({"name": eng, "port": ports[eng],
                         "argv": [py, str(vdir / "scripts" / ("crissa-%s.py" % eng)), "--port", str(ports[eng])],
                         "env": dict(common)})
    if vdir is not None and not tts_url and getattr(a, "with_audio8", False):
        kids.append({"name": "audio8", "port": ports["audio8"],
                     "argv": [py, str(vdir / "scripts" / "crissa-audio8.py"), "--port", str(ports["audio8"])],
                     "env": dict(common)})
    if getattr(a, "with_warm", False):
        kids.append({"name": "warm", "port": None, "argv": [py, str(ROOT / "scripts" / "crissa-voice-warm.py")],
                     "env": dict(common)})
    asr_url = getattr(a, "asr_url", None) or ""
    if vdir is not None and (a.with_asr or asr_url):
        if not asr_url:
            kids.append({"name": "asr", "port": ports["asr"],
                         "argv": [py, str(vdir / "scripts" / "crissa-asr.py"), "--port", str(ports["asr"])],
                         "env": dict(common)})
        # THE EAR MODULE IS THE EAR (2026-10-09, the User: a clean install must be the house): when modules/ear is
        # here and installed, the house's own ear (speaker stamps, wake grammar, hear gate, own-voice veto, the
        # session machine) listens and bridges; the two stand-ins (modules/voice/scripts/crissa-stt.py,
        # scripts/crissa-ear.py) serve only a pack without it. Opt out: --no-ear-module / CRISSA_EAR_MODULE=0.
        edir = ear_module(a, env_in)
        if edir is not None:
            kids.append({"name": "stt", "port": ports["stt"],
                         "argv": [py, str(edir / "scripts" / "crissa-stt.py"), "--port", str(ports["stt"]),
                                  "--asr", asr_url or "http://127.0.0.1:%d" % ports["asr"], "--player", player_url]
                                 + (["--mic", "page"] if a.audio_at == "page" else []),
                         "env": dict(common)})
            kids.append({"name": "bridge", "port": ports["bridge"],
                         "argv": [py, str(edir / "scripts" / "crissa-bridge.py"), "--port", str(ports["bridge"]),
                                  "--stt", "http://127.0.0.1:%d" % ports["stt"], "--player", player_url],
                         "env": dict(common)})
        else:
            kids.append({"name": "stt", "port": ports["stt"],
                         "argv": [py, str(vdir / "scripts" / "crissa-stt.py"), "--port", str(ports["stt"]),
                                  "--asr", asr_url or "http://127.0.0.1:%d" % ports["asr"], "--player", player_url,
                                  "--mic", a.audio_at],
                         "env": dict(common)})
            kids.append({"name": "ear", "port": None,
                         "argv": [py, str(ROOT / "scripts" / "crissa-ear.py"),
                                  "--turns", "http://127.0.0.1:%d/v1/turns" % ports["stt"], "--player", player_url],
                         "env": dict(common)})
    # The demo helper binds its own loopback port. It does not start a lap. Dev mode stays off until the page says on.
    # The demo helper and the Control Panel ship in the panel package: a pack without them (the player pack) starts
    # without them and says so, instead of dying on a missing file (2026-10-08: the panel fell over before its
    # port answered and the launcher stopped everything).
    if (ROOT / "scripts" / "demo-hold.py").is_file():
        kids.append({"name": "demo", "port": None,
                     "argv": [py, str(ROOT / "scripts" / "demo-hold.py")],
                     "env": dict(common)})
    # Who is in the room. scripts/crissa-presence.py runs the presence module's service (modules/presence, the
    # house's rail: door watch, Secure, visitors, registration tokens) when that module is installed, else the
    # stdlib lamp, so a weak machine still starts it. It never binds the house service's port.
    # the recognizer's own threads match presence's two cores (scripts/crissa-presence.py cap_cpus; .101 AA, 2026-10-10)
    kids.append({"name": "presence", "port": ports["presence"],
                 "argv": [py, str(ROOT / "scripts" / "crissa-presence.py"), "--port", str(ports["presence"])],
                 "env": dict(common, RAIL_FACE_ORT_THREADS=str(env_in.get("RAIL_FACE_ORT_THREADS") or "2"))})
    presence_url = "http://127.0.0.1:%d" % ports["presence"]
    # The presence module's other two children (2026-10-09): the camera grabber, the one process that opens the room
    # camera (the service, the oracle's door feed and the registration panel read it), and the registration panel.
    # Each starts only when the module is installed (crissa-presence.py --install) and not opted out
    # (--no-camera / CRISSA_NO_CAMERA=1, --no-enroll / CRISSA_NO_ENROLL=1). A camera already serving elsewhere
    # (CRISSA_CAMERA_URL in the environment) is used instead of starting one.
    pmod = ROOT / "modules" / "presence"
    camera_url = ""
    if presence_module_installed(pmod):
        camera_url = str(env_in.get("CRISSA_CAMERA_URL") or env_in.get("RAIL_CAMERA_URL") or "").strip()
        if not camera_url and not getattr(a, "no_camera", False):
            kids.append({"name": "camera", "port": ports["camera"],
                         "argv": [py, str(pmod / "scripts" / "crissa-camera.py"), "--port", str(ports["camera"])],
                         "env": dict(common)})
            camera_url = "http://127.0.0.1:%d" % ports["camera"]
        if camera_url:
            for k in kids:
                if k["name"] == "presence":
                    k["env"].setdefault("RAIL_CAMERA_URL", camera_url)
                    k["env"].setdefault("CRISSA_CAMERA_URL", camera_url)
            player_env.setdefault("CRISSA_CAMERA_URL", camera_url)
        if not getattr(a, "no_enroll", False):
            enroll_env = dict(common, RAIL_PRESENCE=presence_url + "/v1/presence")
            if camera_url:                       # an empty one made the coach fall back to a fixed port (2026-10-10)
                enroll_env.update(RAIL_CAMERA_URL=camera_url, CRISSA_CAMERA_URL=camera_url)
            kids.append({"name": "enroll", "port": ports["enroll"],
                         "argv": [py, str(pmod / "scripts" / "crissa-enroll.py"), "--port", str(ports["enroll"])],
                         "env": enroll_env})
            player_env.setdefault("CRISSA_ENROLL_URL", "http://127.0.0.1:%d" % ports["enroll"])
    # A value already in the caller's environment is the one the player uses. Otherwise this install's child.
    for key, default in (("RAIL_PRESENCE", presence_url + "/v1/presence"),
                         ("RAIL_VISIT_LAMP", presence_url + "/v1/visit_lamp"),
                         ("CRISSA_PRESENCE_URL", presence_url)):
        supplied = str(env_in.get(key) or "").strip()
        if supplied:
            player_env[key] = supplied
        else:
            player_env.setdefault(key, default)
    lamp = player_env.get("CRISSA_PRESENCE_URL") or presence_url
    for k in kids:
        if k["name"] in ("stt", "ear", "bridge"):
            k["env"].setdefault("CRISSA_PRESENCE_URL", lamp)
            k["env"].setdefault("RAIL_PRESENCE_URL", lamp + "/v1/presence")
            k["env"].setdefault("EAR_BRIDGE_PRESENCE", lamp + "/v1/presence")
    # The oracle: the camera, the face check and the door feed (modules/oracle). Started on every machine since
    # 2026-10-08 (before, nothing started it, so a pack never looked): no camera or no face engine is a mark on the
    # clock, never an error, and the service still answers. It posts each look's numbers to the presence service.
    oracle_script = ROOT / "modules" / "oracle" / "scripts" / "crissa-oracle.py"
    if oracle_script.is_file():
        oracle_env = dict(common, CRISSA_PRESENCE_URL=lamp)
        if camera_url:
            # the grabber owns the camera device: the oracle reads its /snapshot and never opens the device (2026-10-12,
            # .101 round 10: 2927 failed opens in a day, so no look ever reached presence; crissa_oracle.camera)
            oracle_env.setdefault("CRISSA_ORACLE_CAMERA_URL", camera_url)
        kids.append({"name": "oracle", "port": ports["oracle"],
                     "argv": [py, str(oracle_script), "--port", str(ports["oracle"])],
                     "env": oracle_env})
        oracle_url = "http://127.0.0.1:%d" % ports["oracle"]
        if not str(env_in.get("CRISSA_ORACLE") or "").strip():
            player_env.setdefault("CRISSA_ORACLE", oracle_url)
    # THE WALL'S HOME FEED (2026-10-09, the Crissa parity build): scripts/voiceonly-home-server.py on :8099, the
    # server the Tk wall's HOUSE routes read (/v1/house chips, /v1/sound beam clock, /v1/speaker, /frame camera cell,
    # /v1/register, /v1/glass-prefs, /v1/action, the logs roll, the house Control Panel page). It reads the player as
    # its mouth feed, this install's presence service and the oracle's camera; a pack without the script (the player
    # pack) starts without it and the wall keeps today's dark routes.
    feed_url = ""
    feed_script = ROOT / "scripts" / "voiceonly-home-server.py" if feed_script is None else feed_script
    if feed_script.is_file():
        feed_url = "http://127.0.0.1:%d" % ports["feed"]
        oracle_base = "http://127.0.0.1:%d" % ports["oracle"]
        # the camera cell and the firmware (pan) come from the room camera's one owner, the presence module's
        # grabber (/snapshot, /v1/firmware: the house's rail-camera contract), when it runs; the oracle module
        # serves /v1/oracle alone (2026-10-09, the clean-install trial: the oracle 404'd both)
        cam_base = camera_url or oracle_base
        # THE HOUSE STATE (the User 2026-10-10, .101: the feed's /v1/house said "house upstream down" - it read the
        # oracle, which serves no /v1/house): the face shell ships with the feed and serves it from this install's
        # presence, ear and voice (scripts/face-shell-server.py, the port table's face_shell)
        shell_script = ROOT / "scripts" / "face-shell-server.py"
        house_url = oracle_base + "/v1/house"
        if shell_script.is_file():
            house_url = "http://127.0.0.1:%d/v1/house" % ports["face_shell"]
            kids.append({"name": "faceshell", "port": ports["face_shell"], "argv": [py, str(shell_script)],
                         "env": dict(common, FACE_SHELL_PORT=str(ports["face_shell"]),
                                     FACE_SHELL_PRESENCE=lamp, FACE_SHELL_STT="http://127.0.0.1:%d" % ports["stt"])})
        kids.append({"name": "feed", "port": ports["feed"], "argv": [py, str(feed_script)],
                     "env": dict(common, VO_HOME_PORT=str(ports["feed"]), HOUSE_WALL_FEED=player_url,
                                 CRISSA_PRESENCE_URL=lamp, VO_PRESENCE_URL=lamp + "/v1/presence",
                                 VO_FRAME_URL=cam_base + ("/snapshot" if camera_url else "/frame.jpg"),
                                 VO_FIRMWARE_URL=cam_base + "/v1/firmware",
                                 VO_STT_HEALTH_URL="http://127.0.0.1:%d/health" % ports["stt"],
                                 VO_HOUSE_URL=house_url)})
    # THE DESK, on every machine (the User 2026-10-08). Her picture is the player. The ASCII Wall is the screen
    # and docks that picture. The Control Panel is the Wall's panel. A weak machine gets the same Wall.
    # HER PHONE STARTS WITH HER (round 29, the User 2026-10-17: "the app starts with Crissa with a menu item showing the
    # address"): the phone module's server (scripts/phone-app-server.py, the port table's `phone`) is a lane of its own
    # when the module is installed and its packs are unpacked (<state home>/phone/packs). The menu's "Your phone" item
    # shows its address. Kill CRISSA_PHONE_START=0 (round 28: started by hand).
    # THE PHONE FOLLOWS THE WORN AGENT (crissa-6a #6, 1.0.6; lib/crissa_skin_phone): under CRISSA_AGENT_PHONE=1
    # the lane serves the worn library's Mobile packs (<library>/<mobile_library>/packs) and talks on its show.json pack,
    # else the fixed folder; off = the fixed folder and today's argv.
    phone_script = ROOT / "scripts" / "phone-app-server.py"
    phone_follow = None
    try:
        import crissa_home as _ph
        phone_packs = Path(_ph.HOME) / "phone" / "packs"
    except Exception:  # noqa: BLE001
        phone_packs = None
    if phone_packs is not None:
        try:
            import crissa_skin_phone as _sp
            if _sp.enabled(env_in):
                phone_follow = _sp.resolve(_ph.HOME, ROOT, env_in)
                phone_packs = Path(phone_follow["packs"])
        except Exception:  # noqa: BLE001  a pack without the module, or a broken pick: the fixed folder, as before
            phone_follow = None
    if phone_script.is_file() and phone_packs is not None and phone_packs.is_dir() and str(
            env_in.get("CRISSA_PHONE_START", "1")).strip().lower() not in ("0", "off", "false", "no"):
        phone_argv = [py, str(phone_script), "--port", str(ports["phone"])]
        if phone_follow is not None:
            phone_argv = _sp.argv_for(phone_argv, phone_follow)
            try:
                _sp.mark_served(phone_follow, _ph.HOME)
            except OSError:
                pass
        kids.append({"name": "phone", "port": ports["phone"], "argv": phone_argv, "env": dict(common)})
    # WHAT SHE KNOWS (2026-11-01, the User: a local index and memory module for the standalone Wall): the knowledge
    # module's lane, once installed (crissa-knowledge.py --install wrote install.json) and not switched off
    # (CRISSA_KNOWLEDGE=0; dark on the house). The ear bridge's recall tool asks it at CRISSA_KNOWLEDGE_URL.
    kmod = ROOT / "modules" / "knowledge"
    if knowledge_module_on(kmod, env_in):
        kids.append({"name": "knowledge", "port": ports["knowledge"],
                     "argv": [py, str(kmod / "scripts" / "crissa-knowledge.py"), "--port", str(ports["knowledge"])],
                     "env": dict(common)})
        k_url = "http://127.0.0.1:%d" % ports["knowledge"]
        for k in kids:
            if k["name"] in ("bridge", "ear", "player"):
                k["env"].setdefault("CRISSA_KNOWLEDGE_URL", k_url)
        player_env.setdefault("CRISSA_KNOWLEDGE_URL", k_url)
    panel_url = "http://127.0.0.1:%d/" % ports["panel"]
    if (ROOT / "scripts" / "crissa-panel-server.py").is_file():
        kids.append({"name": "panel", "port": ports["panel"],
                     "argv": [py, str(ROOT / "scripts" / "crissa-panel-server.py"), "--port", str(ports["panel"])],
                     "env": dict(common, CRISSA_PRESENCE_URL=lamp)})
    else:
        panel_url = ""                                   # no Control Panel in this pack: the Wall says so
    # THE RENDER MODULE'S RUNNER, opt-in (2026-10-09): --with-render / CRISSA_RENDER=1 keeps modules/render's
    # order runner up (crissa-render.py orders run --watch), taking pending render orders in turn on the user's own
    # ComfyUI. Opt-in because it enters Render (Comfy on the GPU) on its own; everything else here is a service.
    rmod = ROOT / "modules" / "render"
    if getattr(a, "with_render", False) and (rmod / "scripts" / "crissa-render.py").is_file():
        kids.append({"name": "render", "port": None,
                     "argv": [py, str(rmod / "scripts" / "crissa-render.py"), "orders", "run", "--watch",
                              str(getattr(a, "render_watch_s", None) or RENDER_WATCH_S)],
                     "env": dict(common)})
    if not no_desk(a):                               # --no-desk / CRISSA_NO_DESK=1: page-only or headless, no Tk window
        wall_env = dict(common, HOUSE_WALL_FEED=player_url, HOUSE_WALL_PANEL=panel_url, HOUSE_WALL_PRESENCE=lamp)
        # her pictures are kept per library (lib/wall_ascii_disk.library_print, the User 2026-10-30): the Wall is told
        # which one she wears, as the player is; apply_avatar keeps it with the next pick
        wall_env["HOUSE_POSE_DIR"] = str(pose_dir)
        if feed_url:
            wall_env["HOUSE_WALL_HOUSE"] = feed_url     # the wall's HOUSE routes light up from the feed
        # HER THINKING GLOW (parity sweep 2026-10-26, gap 1): the ear bridge writes <home>/crissa-thinking.json by
        # default (its settings' `thinking` is 1), but the desk Wall only pulses her dot with
        # HOUSE_WALL_THINKING_PULSE=1, which defaults 0 for the house (its drop-in arms it) and nothing here set,
        # so the kit's Wall never glowed while she worked. On for the desk Wall; a value already in the environment
        # wins. Kill CRISSA_WALL_THINKING_PULSE=0. The browser Wall's pulse is not this switch.
        if str(env_in.get("CRISSA_WALL_THINKING_PULSE", "1")).strip().lower() not in ("0", "off", "false", "no"):
            wall_env["HOUSE_WALL_THINKING_PULSE"] = str(env_in.get("HOUSE_WALL_THINKING_PULSE") or "1")
        kids.append({"name": "wall", "port": None, "argv": desk_wall(py, fullscreen=fullscreen_wanted(a, env_in))[0],
                     "env": wall_env})
    for k in kids:                                   # the voice choice above reaches every child, the player first
        for key in ("CRISSA_TTS", "CRISSA_TTS_URL"):
            if key in common:
                k["env"][key] = common[key]
    return {"ports": ports, "children": kids, "player": player_url, "audio_at": a.audio_at,
            "face": a.face, "voice": str(vdir) if vdir else None,
            "roster": roster(a, env_in, [k["name"] for k in kids]),
            # planned but held, "installed, not running" (round 17, 2026-10-13: voice_standby)
            "standby": sorted(set(getattr(a, "standby", None) or ()) & {k["name"] for k in kids})}


def dock_back(run=None) -> bool:
    """Ubuntu's dock as the person had it (the desk Wall keeps it in <home>/dock-before.json while it tidies it,
    lib/glass_mode.pack_dock_tidy, and puts it back itself; this is the launcher's belt for a Wall that died). Read
    here without importing the wall package (the player pack has none)."""
    import subprocess
    try:
        import crissa_home
        path = crissa_home.HOME / "dock-before.json"
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    run = run or subprocess.run
    for key, val in (rec.get("keys") or {}).items():
        try:
            run(["gsettings", "set", str(rec.get("schema") or "org.gnome.shell.extensions.dash-to-dock"), str(key),
                 str(val)], capture_output=True, text=True, timeout=8, check=False)
        except Exception:  # noqa: BLE001
            pass
    try:
        path.unlink()
    except OSError:
        pass
    return True


RENDER_WATCH_S = 30
# EVERY PIECE AND ITS SWITCH (2026-10-09, the Crissa parity build): --plan lists each child the launcher knows,
# whether it would start on this machine, and the flag that turns it off (or on, for the two opt-ins). The order
# is the start order. A name here is a name in lib/crissa_procs.LABELS (the Control Panel's tile).
ROSTER = (
    ("aligner", "opt-in", "on when the aligner is installed and better here (scripts/crissa-align.py); off: CRISSA_ALIGNER=0"),
    ("player", "always", "her picture (scripts/house-conductor.py); no switch"),
    ("tts", "when installed", "Kokoro, the resident voice; off: --no-voice, or --tts <engine> / --tts-url names another"),
    ("multilingual", "voice tier", "Chatterbox Multilingual V3; the tier table or --tts multilingual; off: --no-voice"),
    ("turbo", "voice tier", "Chatterbox Turbo; the tier table or --tts turbo; off: --no-voice"),
    ("pocket", "voice tier", "Pocket, the CPU voice; the tier table or --tts pocket; off: --no-voice"),
    ("audio8", "voice tier", "Audio8, the announcer; the tier table's announcer chain; off: --no-voice"),
    ("warm", "with a GPU voice", "keeps a GPU voice warm; off with the voice, or keep_warm.on in the tier table"),
    ("asr", "when installed", "speech to text (modules/voice crissa-asr.py); off: --no-voice; --asr-url names one elsewhere"),
    ("stt", "with the asr", "the microphone's turns (modules/ear, else the voice module's stand-in); off with the asr"),
    ("bridge", "with the ear module", "wake word, who spoke, her reply (modules/ear); off: --no-ear-module / CRISSA_EAR_MODULE=0"),
    ("ear", "without the ear module", "the stand-in ear (scripts/crissa-ear.py) when modules/ear is off or absent"),
    ("demo", "when shipped", "the demo helper (scripts/demo-hold.py, the panel pack); no switch, absent in the player pack"),
    ("presence", "always", "who is in the room (modules/presence when installed, else the lamp); no switch"),
    ("camera", "presence module", "the room camera, one owner; off: --no-camera / CRISSA_NO_CAMERA=1 (then nothing asks a camera: presence's calls answer camera: off, the oracle opens none), or CRISSA_CAMERA_URL names one"),
    ("enroll", "presence module", "Register, the coach (the id module's when installed); off: --no-enroll / CRISSA_NO_ENROLL=1"),
    ("oracle", "when shipped", "the camera's face check and door feed (modules/oracle); no switch (--no-camera keeps it from opening a camera)"),
    ("faceshell", "with the feed", "the house state the feed's /v1/house reads (scripts/face-shell-server.py, :8088): presence, the ear, chat, weather; no switch, absent in the player pack"),
    ("feed", "when shipped", "the Wall's home server, :8099 (the feed pack); no switch, absent in the player pack"),
    ("render", "opt-in", "the render order runner on your ComfyUI (modules/render); on: --with-render / CRISSA_RENDER=1"),
    ("panel", "when shipped", "the Control Panel (the panel pack); no switch, absent in the player pack"),
    ("knowledge", "when installed", "what she knows: the local index over her docs, the camera's, this install, your approved folders (modules/knowledge, crissa-knowledge.py --install); off: CRISSA_KNOWLEDGE=0"),
    ("wall", "the desk", "the Tk Wall; off: --no-desk / CRISSA_NO_DESK=1 (the Wall page in a browser is the screen)"),
)


def roster(a, env_in: dict | None = None, starting: list | None = None) -> list:
    """Every child the launcher knows: name, whether it starts, when it would, its switch. Pure."""
    on = set(starting or [])
    return [{"name": n, "starts": n in on, "when": when, "switch": switch} for n, when, switch in ROSTER]


def knowledge_module_on(kmod: Path, env_in: dict | None = None) -> bool:
    """modules/knowledge is present, installed (crissa-knowledge.py --install wrote <state home>/knowledge/install.json)
    and not switched off: CRISSA_KNOWLEDGE=0 off, =1 on, unset = on in a pack, off on the house."""
    e = os.environ if env_in is None else env_in
    v = str(e.get("CRISSA_KNOWLEDGE") or "").strip().lower()
    if v in ("0", "off", "false", "no"):
        return False
    if not (kmod / "scripts" / "crissa-knowledge.py").is_file():
        return False
    try:
        import crissa_home
        rec = crissa_home.HOME / "knowledge" / "install.json"
        house = crissa_home.is_house()
    except ImportError:
        rec = Path.home() / ".crissa" / "knowledge" / "install.json"
        house = False
    if v not in ("1", "on", "true", "yes") and house:
        return False
    return rec.is_file()


def presence_module_installed(pmod: Path) -> bool:
    """modules/presence is present and its env was built (crissa-presence.py --install wrote install.json)."""
    try:
        import crissa_home
        rec = crissa_home.HOME / "presence" / "install.json"
    except ImportError:
        rec = Path.home() / ".crissa" / "presence" / "install.json"
    return (pmod / "scripts" / "crissa-camera.py").is_file() and rec.is_file()


def init_needed(env=None) -> bool:
    """A first boot that Init (CRISSA_INIT=1) has not finished: <crissa home>/init.json missing or not done.
    Init owns that file; this only reads it."""
    env = os.environ if env is None else env
    if str(env.get("CRISSA_INIT") or "0").strip() != "1":
        return False
    try:
        import crissa_home
        got = json.loads((crissa_home.HOME / "init.json").read_text(encoding="utf-8"))
        return not bool((got or {}).get("done"))
    except (OSError, ValueError, AttributeError):
        return True


def first_start(env=None) -> bool:
    """A TRUE FIRST START RUNS SETUP WITH NO SWITCH (the User 2026-10-29, the first-time walk of 1.0.1: "why would it
    need the flag if it was your first time"): in a pack with the Init switch not set either way and no init.json in the
    state home (missing = first boot; a finished Setup writes it, a cancelled first walk leaves none and so runs again),
    this start turns the switch on. An
    explicit CRISSA_INIT=0 stays off; the house never. Kill CRISSA_INIT_ON_FIRST_START=0."""
    if env is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return False                         # a test's launcher never turns Setup on in the suite's own environment
    env = os.environ if env is None else env
    if str(env.get("CRISSA_INIT_ON_FIRST_START", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    if str(env.get("CRISSA_INIT") or "").strip():
        return False
    try:
        if str(ROOT / "lib") not in sys.path:
            sys.path.insert(0, str(ROOT / "lib"))
        import crissa_home
        if crissa_home.is_house():
            return False
        f = crissa_home.HOME / "init.json"
        if not f.exists():
            return True
        # the 10-29 review of cut 13: a file that does not read, or a walk neither finished nor cancelled (an older
        # Setup's half-written marker), is no Setup record either - an older cancel (done false + cancelled_at) stays
        try:
            got = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return isinstance(got, dict) and not got.get("done") and not got.get("cancelled_at")
    except (ImportError, OSError, AttributeError):
        return False


def has_display(env=None, plat: str | None = None) -> bool:
    """A screen a browser can open on: a Mac or Windows always; Linux with DISPLAY or WAYLAND_DISPLAY."""
    env = os.environ if env is None else env
    plat = plat or sys.platform
    if plat == "darwin" or plat.startswith("win"):
        return True
    return bool(str(env.get("DISPLAY") or "").strip() or str(env.get("WAYLAND_DISPLAY") or "").strip())


def open_setup_once(player_url: str, a=None, env=None, opener=None, window=None) -> str:
    """FIRST BOOT OPENS SETUP, ONCE (the User 2026-10-10), IN HER OWN WINDOW (the User 2026-10-11). While Init leads a
    first boot and has not opened its page before (<crissa home>/init-opened.json, lib/crissa_init.browser_led),
    <player>/init opens in Crissa's own browser instance (lib/crissa_window: Brave, else Chromium, else Google Chrome,
    her profile under the state home) and that window comes to the front; the desk Wall then shows only "Setup is open
    in Crissa's window". It is never a tab in the person's own browser (.101 JJ: a tab in the Brave window already
    there was not raised, and the desk Wall's own Setup plate took the screen). With no browser of that family (Firefox
    only, or none) nothing opens: the line says so and the desk Wall's own plates carry the walk.
    Returns what happened: "opened", "already", "not first boot", "off" (--no-open, CRISSA_OPEN=0), "no screen",
    "no own browser", "window failed". CRISSA_OPENER names another opener (the acceptance's and the trial's recorder);
    CRISSA_OWN_WINDOW=0 is the old way (the person's default browser, open_page). `opener` / `window`: tests."""
    env = os.environ if env is None else env
    if not init_needed(env):
        return "not first boot"
    if (os.environ.get("PYTEST_CURRENT_TEST") or env.get("PYTEST_CURRENT_TEST")) and opener is None \
            and window is None and not str(env.get("CRISSA_OPENER") or "").strip():
        return "test"                                # a test never opens a real browser on anyone's screen
    if (a is not None and getattr(a, "no_open", False)) or str(env.get("CRISSA_OPEN", "1")).strip() == "0":
        _log("setup   open %s/init in a browser (the first-boot open is off: --no-open)" % player_url)
        return "off"
    import crissa_init
    if crissa_init.browser_record():
        return "already"
    url = player_url + "/init"
    if not has_display(env):
        _log("setup   no screen here: open %s in a browser" % url, plain="Setup: open %s in a web browser." % url)
        return "no screen"
    who = str(env.get("CRISSA_OPENER") or "").strip()
    import crissa_window
    if not who and opener is None and (window is not None or crissa_window.enabled(env)):
        got = (window or crissa_window.show)(url, front=True, env=env)
        if got.get("ok") or got.get("launched"):
            # started, even if slow to answer: the desk Wall says so (or that the window is closed) and never draws
            # a second Setup plate meanwhile
            crissa_init.mark_browser_opened(url, by="window")
            _log("setup   %s in her own window (%s)" % (url, ", ".join(got.get("how") or []) or "started"))
            _say("Setup is open in %s's window." % crissa_home_name())
            return "opened"
        if got.get("why") == "no browser":
            _log("setup   no own window: %s" % got.get("message"), plain=str(got.get("message") or ""))
            return "no own browser"
        _log("setup   her window did not open (%s): %s" % (got.get("why"), got.get("message") or ""),
             plain="Setup: %s's window did not open (%s); her desk Wall carries Setup, or open %s yourself."
             % (crissa_home_name(), got.get("message") or got.get("why"), url))
        return "window failed"
    try:
        if who:
            subprocess.Popen([who, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            ok = True
        else:
            ok = (opener or open_page)(url)
    except OSError as e:
        _log("setup   could not open the browser (%s): open %s" % (e, url))
        return "no screen"
    if ok:
        crissa_init.mark_browser_opened(url, by="launcher")
        _say("Setup is open in your web browser: %s" % url)
        return "opened"
    return "no screen"


def crissa_home_name() -> str:
    try:
        import crissa_home
        return crissa_home.her_name()
    except Exception:  # noqa: BLE001
        return "Crissa"


# ── HER OWN WINDOW, FROM THE LAUNCHER (the User 2026-10-11) ────────────────────────────────────────────────────────────
# The dock entry (lib/crissa_window.install_dock) runs `crissa.py --window`: Crissa started when she is not running,
# then her own window on her Wall, or on Setup while Setup is due. With the desk Wall off (--no-desk, or the Wall
# closed on purpose), her browser Wall stays open somewhere: the launcher's loop checks every WINDOW_KEEP_S and, after
# two passes in a row with no desk Wall, starts her window or opens her Wall in it - not raised, nothing closed. With the
# desk Wall on, her window is never forced open and never closed. Kill: CRISSA_KEEP_WINDOW=0.
WINDOW_KEEP_S = 10.0
WINDOW_BOOT_WAIT_S = 120.0


def keep_window_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_KEEP_WINDOW", "1")).strip().lower() not in ("0", "off", "false", "no")


def desk_is_off(g, home=None) -> bool:
    """No desk Wall now: --no-desk (no wall lane), the Wall closed on purpose, or its lane down."""
    if "wall" not in getattr(g, "procs", {}):
        return True
    try:
        if crissa_watch.closed(home, "wall"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return not g.alive("wall")


STOP_CLEAN_SWITCH = "CRISSA_STOP_ALL_CLEAN"


def stop_of_everything(player_proc, going_down=None, env=None) -> bool:
    """The player's unit was stopped on purpose (systemd's Result=success) while the launcher's own unit is being
    stopped: a stop of everything, not a crash. Process mode (no unit) is never this. Kill: CRISSA_STOP_ALL_CLEAN=0."""
    e = os.environ if env is None else env
    if str(e.get(STOP_CLEAN_SWITCH, "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    if not isinstance(player_proc, crissa_service.UnitProc):
        return False
    if not getattr(player_proc, "stopped_clean", False):
        return False
    try:
        return bool((going_down or crissa_service.going_down)())
    except Exception:  # noqa: BLE001  unknown: the old reading (the player stopped)
        return False


# AFTER SETUP, THE DESKTOP WALL (round 15, .101's fresh install: the User left round 14's trap with Ctrl+Q, the Wall's
# close was recorded and held, and after Setup the Wall opened in browser mode - only --open-wall brought the wallpaper
# back; "it should open in wallpaper mode"). A close of the desk Wall while first-run Setup is due is Setup's own, never a
# lasting "closed by you": at Setup's end it is cleared and the desk Wall starts in the same pass, before the watcher
# could open her browser Wall in its place. Kill: CRISSA_SETUP_WALL_BACK=0.
def setup_end_pass(g, home, state: dict, env=None, due=None) -> str:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_SETUP_WALL_BACK", "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    now_due = bool(init_needed(e) if due is None else due)
    rec = crissa_watch.closed(home, "wall")
    if now_due:
        state["was_due"] = True
        if rec:
            state["closed_in_setup"] = True
        return ""
    if not state.pop("was_due", False):
        return ""
    if not (state.pop("closed_in_setup", False) and rec):
        return ""
    crissa_watch.set_closed("wall", False, home=home)
    spec = g.specs.get("wall") or ((LATE_LANES["wall"]["argv"], LATE_LANES["wall"]["env"], LATE_LANES["wall"]["port"])
                                    if "wall" in LATE_LANES else None)
    if spec is None or g.alive("wall"):
        _log("wall    Setup is done: the Wall's close during Setup is cleared")
        return "cleared"
    try:
        g.specs["wall"] = spec
        g.spawn("wall", *spec)
    except Exception as ex:  # noqa: BLE001  a Wall that will not start is named, never a launcher crash
        _log("wall    Setup is done, but the desktop Wall did not start: %s: %s" % (type(ex).__name__, ex), error=True)
        return "did not start"
    _log("wall    Setup is done: the desktop Wall is back (its close during Setup was Setup's own, not a lasting one)",
         plain="Setup is done: her Wall is your desktop again.")
    return "started"


AGENT_HELD: set = set()          # the lanes Close Agent holds down in this run (started again by Open Agent)
WINDOW_TRIES = 5                 # round 26: passes Close Agent spends closing her pages before it gives up and says so


def close_agent_window(close=None) -> str:
    """Close Agent's window step: her own instance's pages on the player's origin close (lib/crissa_window
    close_agent_pages); never the person's browser, never the Control Panel she may have pressed it on."""
    if os.environ.get("PYTEST_CURRENT_TEST") and close is None:
        return "test"
    try:
        import crissa_window
        got = (close or crissa_window.close_agent_pages)(crissa_ports.url("player"))
        return ", ".join(got.get("how") or []) or "nothing"   # "still open" in it: agent_pass tries again
    except Exception as e:  # noqa: BLE001  Close Agent never fails on her window
        return "could not close it (%s)" % type(e).__name__


def agent_pass(g, home, state: dict, close_window=None, watch=None) -> str:
    """CLOSE AGENT / OPEN AGENT (the User 2026-10-12: "Close Agent vs OS off ... make them match wherever possible").
    One pass of the launcher's loop. While Close Agent is recorded (lib/crissa_watch.agent_closed: the menus, the
    Control Panel), every lane she listens or speaks with that runs is stopped and held (the watch holds them too) and,
    on the change, her own window's agent pages close (close_agent_window: her Wall, Setup, Connect; a Control Panel or
    store page there stays): the desk Wall, the player, the feed and the Control Panel stay - the desktop, the OS. When the record is cleared (Open Agent: the menus, the Control Panel, her dock icon), the
    lanes it held start again. Returns what it did."""
    rec = crissa_watch.agent_closed(home)
    did = []
    if rec:
        for name in crissa_watch.AGENT_LANES:
            if name in getattr(g, "procs", {}) and g.alive(name):
                rc = g.stop_one(name)
                g.table.exited(name, rc, expected=True, why=crissa_watch.AGENT_WHY % (rec.get("by") or "closed"))
                AGENT_HELD.add(name)
                did.append("stopped " + name)
        if not state.get("closed"):
            state["closed"] = True
            state["window_tries"] = 0
            state["window_done"] = False
            first = True
        else:
            first = False
        if not state.get("window_done") and int(state.get("window_tries") or 0) < WINDOW_TRIES:
            # round 26: her pages are closed until they ARE closed (a busy browser left them open: see
            # crissa_window.close_agent_pages), at most WINDOW_TRIES passes
            state["window_tries"] = int(state.get("window_tries") or 0) + 1
            said = (close_window or close_agent_window)()
            state["window_done"] = not ("still open" in said or said.startswith("could not"))
            did.append("her window: " + said)
            if not first:
                _log("agent   Close Agent: her window again (%d): %s" % (state["window_tries"], said))
        if first:
            _log("agent   Close Agent (%s): %s; the Wall stays as your desktop" % (rec.get("by") or "closed",
                                                                                   ", ".join(did) or "nothing ran"),
                 plain="Crissa's agent is closed: she does not listen or speak until Open Agent. The Wall stays.")
        return ", ".join(did) or "closed"
    if state.get("closed"):
        state["closed"] = False
        back = []
        for name in sorted(AGENT_HELD):
            spec = g.specs.get(name) or ((LATE_LANES[name]["argv"], LATE_LANES[name]["env"], LATE_LANES[name]["port"])
                                         if name in LATE_LANES else None)
            if spec is None:
                continue
            if g.alive(name):
                back.append(name)              # already back (its unit started by something else): said running below
                continue
            served = getattr(watch, "served", None)
            said = served(name) if callable(served) else ""
            if said:
                # ANOTHER COPY SERVES ITS PORT (round 17, 2026-10-13, .101 08:28: the player's own aligner held :8195
                # and this start failed every 2 s): not started twice; the watch holds it as "served" and starts this
                # lane's own when that copy stops (kill CRISSA_LANE_PORT_SERVED=0)
                g.specs[name] = spec
                g.table.mark(name, state="served", why=crissa_watch.SERVED_WHY % (spec[2], said))
                did.append("%s already served on :%s" % (name, spec[2]))
                continue
            try:
                g.specs[name] = spec
                g.spawn(name, *spec)
                did.append("started " + name)
                back.append(name)
            except Exception as e:  # noqa: BLE001  a lane that will not start is named, never a launcher crash
                g.table.exited(name, None, why="could not start: %s: %s" % (type(e).__name__, e))
                did.append("%s did not start" % name)
        try:
            # STARTED CLEAN, AND SAID SO (round 15; round 16, .101 00:52: tiles kept "stopped ... Start brings it back"
            # and "restarted x1 - last: Close Agent" after Open Agent): every lane Open Agent has back reads running,
            # no restarts and no close reason, on the launcher's table and on the watch's lanes
            for name in back:
                c = g.table.child(name)
                c["restarts"] = 0
                if c.get("state") != "running" or str(c.get("why") or "").startswith("Close Agent"):
                    p = g.procs.get(name)
                    c.update(state="running", why="", exit_code=None, stopped=None,
                             pid=getattr(p, "pid", None) or c.get("pid"))
            g.table.write()
        except Exception:  # noqa: BLE001  a count never stops Open Agent
            pass
        if watch is not None:
            watch.reset_lanes(back, crissa_watch.OPENED_WHY)
        AGENT_HELD.clear()
        _log("agent   Open Agent: %s" % (", ".join(did) or "nothing to start"),
             plain="Crissa's agent is open again: she listens and speaks.")
        return ", ".join(did) or "open"
    return ""


# NOT WHILE HER FACE WINDOW IS UP (round 43, .101 round 41 R41-7: the browser Wall opened by itself 11 s after the User
# reopened the desk Wall from her face window; the User: "leave that for when Wall Off and no render window. With this
# we are one button from back to Wall, so not here"). The browser Wall opens only when the desk Wall is off AND her face
# window is not up (the player's /v1/health `face_window`: its page polled within lib/crissa_face STALE_S). Either one
# resets the count, and so does the desk Wall coming back (its lane's process changed since the last pass: a reopen,
# even while it still reads off for a moment). Kill CRISSA_KEEP_WINDOW_FACE=0 (round 42: two desk-off passes).
def keep_window_face_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CRISSA_KEEP_WINDOW_FACE", "1")).strip().lower() not in ("0", "off", "false", "no")


def face_window_up(player_url: str, timeout: float = 1.0) -> bool:
    """Her face window is up now (GET /v1/health `face_window`; a player that cannot say: no)."""
    try:
        with crissa_egress.urlopen(player_url + "/v1/health", timeout, "her window: the face window") as r:
            d = json.loads(r.read(20000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:              # 503 while her picture is not moving yet: the body still says
        try:
            d = json.loads(e.read(20000).decode("utf-8", "replace") or "{}")
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False
    return bool(isinstance(d, dict) and d.get("face_window"))


def keep_window(player_url: str, g, state: dict, home=None, env=None, ensure=None, now: float | None = None,
                face=None) -> str:
    """One pass of the desk-off rule. `state` carries the pass count between calls. Returns what it did."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if now - float(state.get("at") or 0.0) < WINDOW_KEEP_S:
        return "wait"
    state["at"] = now
    if not keep_window_on(env) or not has_display(env):
        return "off"
    if crissa_watch.agent_closed(home):
        return "agent closed"                    # Close Agent: the watcher stands down until Open Agent
    if not desk_is_off(g, home):
        state["off"] = 0
        return "desk wall up"
    if keep_window_face_on(env):
        wall = getattr(getattr(g, "procs", {}).get("wall"), "pid", None)
        was, state["wall_pid"] = state.get("wall_pid"), wall
        if wall is not None and was is not None and wall != was:
            state["off"] = 0
            return "desk wall reopened"          # round 43: a reopen resets the count
        if (face or face_window_up)(player_url):
            state["off"] = 0
            return "face window up"              # round 43: one button from back to the Wall, so not here
    state["off"] = int(state.get("off") or 0) + 1
    if state["off"] < 2:                         # a Wall the watch is starting again is not off
        return "desk wall off (once)"
    if init_needed(env):
        return "setup due"                       # open_setup_once's own window carries Setup
    import crissa_window
    did = (ensure or crissa_window.ensure_wall)(player_url + "/wall", env=env)
    if did not in ("present", "test", "off"):
        _log("window  the desk Wall is off: %s's Wall in her own window: %s" % (crissa_home_name(), did))
    return did


def close_her_window(env=None, close=None) -> str:
    """STOP CRISSA CLOSES HER WINDOW (.101 round 11 HH, 2026-10-12: after `systemctl --user stop 'crissa-*'` her
    Brave stayed up in its own scope showing a frozen loop; the User: "loop frozen, needs to be closed on exit"). The
    launcher's own stop - the unit's stop, Stop Crissa in the menu, Ctrl-C in process mode - closes her own instance
    (Browser.close, then her scope, lib/crissa_window.close_own) before the lanes stop, and only hers: the browser on
    her profile, never the person's own. Kill: CRISSA_WINDOW_CLOSE_ON_STOP=0. Returns what was done."""
    env = os.environ if env is None else env
    if (os.environ.get("PYTEST_CURRENT_TEST") or env.get("PYTEST_CURRENT_TEST")) and close is None:
        return "test"
    try:
        import crissa_window
        got = (close or crissa_window.close_own)(env=env)
    except Exception as e:  # noqa: BLE001  (a stop never fails on her window)
        _log("window  could not close %s's window: %s: %s" % (crissa_home_name(), type(e).__name__, e), error=True)
        return "failed"
    how = ", ".join(got.get("how") or [])
    if got.get("left"):
        _log("window  %s's window is still up after %s (pids %s)" % (crissa_home_name(), how, got["left"]), error=True)
        return "left"
    if how not in ("off", "not running") and not how.startswith("kept"):
        _log("window  %s's window closed (%s)" % (crissa_home_name(), how))
    return how


def dock_on_start(env=None, install=None, current=None) -> str:
    """HER DOCK ENTRY (the User 2026-10-11: "a Crissa icon in the dock"): written at a pack's start on a Linux desktop
    when it is missing or names another install (a moved tree), pinned in GNOME's dock once (lib/crissa_window). The
    kit's own step, so a kit installed by its README gets it at the first start; `--dock install` does it by hand.
    Kill: CRISSA_DOCK=0."""
    env = os.environ if env is None else env
    if not sys.platform.startswith("linux"):
        return "not linux"
    if (os.environ.get("PYTEST_CURRENT_TEST") or env.get("PYTEST_CURRENT_TEST")) and install is None:
        return "test"
    if str(env.get("CRISSA_DOCK", "1")).strip().lower() in ("0", "off", "false", "no"):
        return "off"
    if not has_display(env):
        return "no screen"
    import crissa_window
    if not crissa_window.enabled(env):
        return "off"
    off = crissa_ports.offset(env)
    launcher = ROOT / "scripts" / "crissa.py"
    if (current or crissa_window.dock_current)(sys.executable, launcher, off, env=env):
        return "present"
    got = (install or crissa_window.install_dock)(sys.executable, launcher, env=env, port_offset=off)
    _log("dock    %s's entry: %s%s" % (crissa_home_name(), got.get("file") or got.get("why"),
                                       " (pinned in the dock)" if got.get("pinned") else ""))
    return "installed" if got.get("ok") else "failed"


def _player_up(player_url: str, timeout: float = 1.0) -> bool:
    try:
        with crissa_egress.urlopen(player_url + "/v1/menu", timeout, "her window: the player") as r:
            return 200 <= int(getattr(r, "status", 200)) < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:  # noqa: BLE001
        return False


def setup_due(player_url: str, timeout: float = 2.0) -> bool:
    """Init is due or running on this player (GET /v1/init; 404 = Init is off here)."""
    try:
        with crissa_egress.urlopen(player_url + "/v1/init", timeout, "her window: Setup") as r:
            d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
        return bool(d.get("needed")) or d.get("status") == "running"
    except Exception:  # noqa: BLE001
        return False


def _notify(text: str) -> None:
    """A line the person sees when the dock started this (no terminal): the desktop's notification, if it has one."""
    import shutil
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", crissa_home_name(), text], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass


def restore_whole_off(popen=None, power=None) -> str:
    """THE WAY BACK FROM A WHOLE ALL OS OFF (the User 2026-11-01, 1.0.7; lib/crissa_os_power): Off stopped the Control
    Panel and the add-ons too, so nothing is left to press Turn On All Services on. Her dock icon or `crissa.py` runs On from the
    snapshot (everything that was on, the panel and the add-ons included), detached, its words in logs/os-power-on.log. The
    snapshot is marked as being restored first, so a second caller (the launcher this On starts) never runs it twice and
    the panel never retires it half way. '' = nothing to restore (no Off in force, a restore already running, the switch
    off, or no panel package here); else what it started."""
    try:
        if str(ROOT / "lib") not in sys.path:
            sys.path.insert(0, str(ROOT / "lib"))
        import crissa_os_power
        if power is None and popen is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return ""                        # a test never starts a real On (crissa_os_power.test_refusal)
        pw = power or crissa_os_power.Power()
        if not pw.whole or not pw.begin_restore():
            return ""
    except Exception:  # noqa: BLE001 - a pack without the panel package, or no settings: the plain start goes on
        return ""
    import crissa_home
    logs = crissa_home.HOME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    tool = ROOT / "scripts" / "crissa-os-power"
    with open(logs / "os-power-on.log", "ab") as out:
        (popen or subprocess.Popen)([sys.executable, str(tool), "on"], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True, cwd=str(ROOT))
    return "restoring what All OS Off stopped (log: %s)" % (logs / "os-power-on.log")


def start_for_window(a, run=None, popen=None) -> str:
    """Crissa is not running and the dock was pressed: her service when it is installed, else this launcher by hand
    (which hands itself to the service where it can). Returns how. After a whole All OS Off, On runs from its snapshot
    first (restore_whole_off), which brings back the Control Panel and the add-ons too."""
    run = run or (lambda argv, t=30.0: subprocess.run(argv, capture_output=True, text=True, timeout=t))
    back = restore_whole_off(popen=popen)
    if back:
        _log("window  %s" % back)
    f = crissa_service.service_file()
    m = crissa_service.manager()
    if f is not None and f.exists() and m == "systemd":
        run(["systemctl", "--user", "start", crissa_service.UNIT])
        return "service"
    if f is not None and f.exists() and m == "launchd":
        run(["launchctl", "kickstart", "gui/%d/%s" % (os.getuid(), crissa_service.LABEL)])
        return "service"
    import crissa_home
    logs = crissa_home.HOME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(ROOT / "scripts" / "crissa.py")] + (
        ["--port-offset", str(a.port_offset)] if getattr(a, "port_offset", 0) else [])
    with open(logs / "window-start.log", "ab") as out:
        (popen or subprocess.Popen)(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True, cwd=str(ROOT))
    return "by hand"


DOCK_PANEL_SWITCH = "CRISSA_DOCK_PANEL"
DESK_WALL_STATES = ("running", "restarting", "restarted", "up")


def dock_panel_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(DOCK_PANEL_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def desk_wall_on(home=None, now: float | None = None, pid_alive=None) -> bool:
    """The desk Wall is up in this install now (or coming back): the launcher's heartbeat is fresh, its table has the
    wall lane running with a live process (or being restarted), and the person has not closed it. A --no-desk start, a
    Wall closed on purpose, a lane that is down, or no launcher at all: False."""
    try:
        import crissa_home
        home = crissa_home.HOME if home is None else home
        tab = crissa_procs.load(home)
        if not tab or not crissa_procs.launcher_alive(tab, now)[0]:
            return False
        if crissa_watch.closed(home, "wall"):
            return False
        c = (tab.get("children") or {}).get("wall") or {}
        state = str(c.get("state") or "")
        if state not in DESK_WALL_STATES:
            return False
        if state == "restarting":
            return True
        return bool(c.get("pid")) and (pid_alive or crissa_procs._pid_alive)(c["pid"])
    except Exception:  # noqa: BLE001  (unreadable: as if there were no desk Wall, today's behaviour)
        return False


def _panel_up(panel_url: str, timeout: float = 1.0) -> bool:
    try:
        with crissa_egress.urlopen(panel_url.rstrip("/") + "/health", timeout, "her window: the Control Panel") as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:  # noqa: BLE001
        return False


def window_cmd(a, env=None, show=None, run=None, popen=None, sleep=time.sleep, wait_s: float | None = None,
               show_panel=None) -> int:
    """`--window`, the dock entry's press: start Crissa when she is not running, then show her own window - on Setup
    while Setup is due (and the desk Wall then shows only "Setup is open in Crissa's window"), else on her Wall - and
    bring it forward. Never the person's own browser.
    WHILE THE DESK WALL IS UP, HER WINDOW SHOWS THE CONTROL PANEL (the User 2026-10-12: "agree on control panel. Only
    if they want the wall it should be available. Make it auto open CP if that is better"; on .101 the dock opened the
    browser Wall beside the desk Wall and two glasses painted her, her Brave at a whole core): the dock press brings
    her window forward on the Control Panel (a Control Panel page of hers, else the page she shows moved there), never
    a second Wall; the Control Panel's "Open the Wall in this window" opens the browser Wall on request. No desk Wall
    (--no-desk, closed, down, a Mac): her Wall as before. Setup due: Setup, as before. Kill: CRISSA_DOCK_PANEL=0."""
    env = os.environ if env is None else env
    try:
        import crissa_home
        if crissa_home.is_house():
            _log("this tree is the house's own install; its Wall window is the house's (crissa-display)", error=True)
            return 2
    except ImportError:
        pass
    import crissa_window
    off = dict(env, CRISSA_PORT_OFFSET=str(a.port_offset)) if getattr(a, "port_offset", 0) else dict(env)
    player = "http://127.0.0.1:%d" % crissa_ports.port("player", off)
    if not _player_up(player):
        how = start_for_window(a, run=run, popen=popen)
        _log("window  %s was not running: started (%s)" % (crissa_home_name(), how))
        end = time.time() + (WINDOW_BOOT_WAIT_S if wait_s is None else wait_s)
        while not _player_up(player) and time.time() < end:
            sleep(0.5)
        if not _player_up(player):
            msg = "%s did not start in time. Her log: %s" % (crissa_home_name(), CON.path)
            _log(msg, error=True)
            _notify(msg)
            return 1
    try:                                     # her icon brings the agent back after Close Agent (2026-10-12)
        import crissa_home as _zh
        if crissa_watch.agent_closed(_zh.HOME):
            import crissa_menu
            crissa_menu.agent_open(by="her dock icon")
            _log("window  Open Agent (her dock icon): she listens and speaks again")
    except Exception as e:  # noqa: BLE001  the window still opens
        _log("window  Open Agent: %s: %s" % (type(e).__name__, e), error=True)
    due = setup_due(player)
    url = player + ("/init" if due else "/wall")
    panel = "http://127.0.0.1:%d/" % crissa_ports.port("panel", off)
    to_panel = not due and dock_panel_on(env) and desk_wall_on()
    if to_panel and not _panel_up(panel):
        # the desk Wall is up and there is no Control Panel to show: never a second Wall
        msg = "The Wall is on your desktop; the Control Panel did not answer at %s" % panel
        _log("window  " + msg, error=True)
        _notify(msg)
        return 1
    if to_panel:
        url = panel
        got = (show_panel or crissa_window.show_panel)(panel, player, env=off)
    else:
        got = (show or crissa_window.show)(url, front=True, env=off)
    if got.get("ok"):
        if due:
            import crissa_init
            crissa_init.mark_browser_opened(url, by="dock")
        _log("window  %s (%s)%s" % (url, ", ".join(got.get("how") or []) or "started",
                                    " - the desk Wall is up, so her window shows the Control Panel" if to_panel else ""))
        return 0
    msg = str(got.get("message") or got.get("why") or "her window did not open")
    _log("window  " + msg, error=True)
    _notify(msg)
    return 1


def dock_cmd(what: str, a=None, env=None) -> int:
    """`--dock install|remove|status`: her dock entry by hand (a pack, a Linux desktop)."""
    env = os.environ if env is None else env
    import crissa_window
    if what == "install":
        off = getattr(a, "port_offset", 0) or 0
        out = crissa_window.install_dock(sys.executable, ROOT / "scripts" / "crissa.py", env=env, port_offset=off)
    elif what == "remove":
        out = crissa_window.remove_dock(env=env)
    else:
        out = {"ok": True, "installed": crissa_window.dock_installed(env),
               "file": str(crissa_window.apps_dir(env) / crissa_window.DESKTOP_ID)}
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", True) else 1


def first_page(player_url: str, env=None) -> str:
    """Init's page (/init) while Init leads a first boot; else /connect on a first run (no agent connected yet),
    else the Wall page."""
    if init_needed(env):
        return player_url + "/init"
    try:
        import crissa_agent
        if not crissa_agent.configured(crissa_agent.load()):
            return player_url + "/connect"
    except Exception:  # noqa: BLE001
        pass
    return player_url + "/wall"


class Group:
    """Every child in ONE process group of its own, so one signal reaches them all; the launcher stays
    outside it (it is the one sending the signal). Each child's command is kept, so the panel can restart one."""

    def __init__(self, logs: Path, table: "crissa_procs.Table | None" = None, *,
                 use_units: bool = False, unit_run=None):
        self.pgid: int | None = None
        self.procs: dict = {}
        self.specs: dict[str, tuple] = {}            # name -> (argv, env, port)
        self.extra_pgids: set[int] = set()           # a child restarted after its group emptied leads a new one
        self.expected: set[str] = set()              # stopped on purpose: not a crash
        self.logs = logs
        self.table = table
        self.use_units = bool(use_units)
        self.unit_run = unit_run or crissa_service.default_run
        self._units_reloaded = False
        self._down = (-1e9, False)                   # (asked at, going down): lib/crissa_service.going_down
        self.manager_gone = False                    # the user manager cannot be reached: children run by hand

    def spawn(self, name: str, argv: list[str], env: dict, port: int | None = None):
        self.logs.mkdir(parents=True, exist_ok=True)
        log = self.logs / ("%s.log" % name)
        self.specs[name] = (list(argv), dict(env), port)
        self.expected.discard(name)
        if self.use_units:
            unit_proc = self._spawn_unit(name, argv, env, port, log)
            if unit_proc is not None:
                return unit_proc
            if not self.manager_gone:
                # ONE UNIT OWNS EACH PORT (2026-10-10, .101 finding I): a unit whose start did not take is left to its
                # manager and to the watch's next try - never a by-hand copy beside it, which would hold its port
                p = crissa_service.UnitProc(crissa_service.desk_unit_name(name), self.unit_run, 0)
                self.procs[name] = p
                if self.table is not None:
                    self.table.started(name, 0, port, log, argv)
                return p
        return self._spawn_popen(name, argv, env, port, log)

    def _launch(self, argv: list[str], env: dict) -> dict:
        """This child's launch (crissa_service.child_launch) with the LIVE session's display keys, read now through
        the group's own runner (2026-10-24: a unit is a snapshot of one login; the Wall must open on the one that is up)."""
        session = crissa_service.live_session_env(run=self.unit_run)
        return crissa_service.child_launch(argv, env, session=session)

    def going_down(self, fresh: bool = False) -> bool:
        """Service mode: the launcher's own unit is being stopped (a job on it, or deactivating), so no child may start
        again (lib/crissa_service.going_down; the User 2026-10-10, .101 round 5 HH). Never in process mode."""
        if not self.use_units or self.manager_gone:
            return False
        now = time.time()
        if fresh or now - self._down[0] >= 1.0:   # at most once a second (a boot starts its units in a burst)
            self._down = (now, crissa_service.going_down(self.unit_run))
        return self._down[1]

    def _spawn_unit(self, name: str, argv: list[str], env: dict, port: int | None, log: Path):
        """Write crissa-<name>.service from the child's launch (the same one process mode starts) and start it. None
        means start it by hand instead."""
        unit = crissa_service.desk_unit_name(name)
        if self._units_reloaded and self.going_down():
            # a start now would wait on the launcher's own stop job (each child is After= it) and run once the launcher
            # is gone: the unit would come back with nothing to stop it (HH). Left to the watch, which holds it.
            _log("desk unit %s not started: Crissa is stopping (its service is being stopped)" % unit)
            return None
        crissa_service.write_desk_unit(name, self._launch(argv, env), home=self.logs.parent)
        if not self._units_reloaded:
            self.unit_run(["systemctl", "--user", "daemon-reload"], 30.0)
            self._units_reloaded = True
        rc, so, se = self.unit_run(["systemctl", "--user", "start", unit], 30.0)
        if rc != 0 and not crissa_service.manager_unreachable(se or so):
            time.sleep(1.5)                            # a job already running for it (a restart in flight): once more
            rc, so, se = self.unit_run(["systemctl", "--user", "start", unit], 30.0)
        if rc != 0:
            err = ((se or so).strip() or "exit %s" % rc)[:200]
            self.manager_gone = crissa_service.manager_unreachable(se or so)
            line = "desk unit %s did not start (%s); %s" % (
                unit, err, "starting it by hand (the user manager cannot be reached)" if self.manager_gone
                else "left to systemd and the watch's next try (one unit owns its port)")
            try:
                with log.open("ab") as fh:
                    fh.write(("--- %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line)).encode())
            except OSError:
                pass
            _log(line)
            return None
        pid = crissa_service.main_pid(unit, run=self.unit_run)
        p = crissa_service.UnitProc(unit, self.unit_run, pid)
        self.procs[name] = p
        if self.table is not None:
            self.table.started(name, p.pid or 0, port, log, argv)
        try:
            with log.open("ab") as fh:
                fh.write(("--- %s start %s pid %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), unit, p.pid)).encode())
        except OSError:
            pass
        return p

    def _spawn_popen(self, name: str, argv: list[str], env: dict, port: int | None, log: Path) -> subprocess.Popen:
        out = open(log, "ab")
        out.write(("--- %s start\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
        out.flush()

        launch = self._launch(argv, env)                     # the same launch a unit carries (service mode)

        pri = crissa_service.lane_priority(name, launch["env"])
        nice = pri[0] if pri else 0                          # the lane's share, as its unit carries it (round 13)
        try:                                                 # every lane on the rest of the machine (lib/paint_cores)
            import paint_cores
            rest = paint_cores.preexec_rest(launch["env"])
        except Exception:  # noqa: BLE001
            rest = None

        def popen(pg: int) -> subprocess.Popen:
            if sys.version_info >= (3, 11):
                kw = {"process_group": pg}
                if nice > 0 or rest is not None:
                    kw["preexec_fn"] = lambda: ((os.nice(nice) if nice > 0 else None), (rest() if rest else None))
            else:
                kw = {"preexec_fn": lambda: (os.setpgid(0, pg), (os.nice(nice) if nice > 0 else None),
                                             (rest() if rest else None))}
            return subprocess.Popen(launch["argv"], cwd=launch["cwd"], env=launch["env"], stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, **kw)
        try:
            pg = 0 if self.pgid is None else self.pgid
            try:
                p = popen(pg)
            except (OSError, subprocess.SubprocessError):
                if pg == 0:
                    raise
                p = popen(0)                          # the old group has no member left: lead a new one
                self.extra_pgids.add(p.pid)
        finally:
            out.close()
        if self.pgid is None:
            self.pgid = p.pid
        self.procs[name] = p
        if self.table is not None:
            self.table.started(name, p.pid, port, log, argv)
        return p

    def alive(self, name: str) -> bool:
        p = self.procs.get(name)
        return p is not None and p.poll() is None

    def stop_one(self, name: str, wait: float | None = None):
        """Stop ONE child (TERM, then KILL); the rest keep going. Returns its exit code."""
        wait = STOP_WAIT_S if wait is None else wait
        p = self.procs.get(name)
        if p is None:
            return None
        self.expected.add(name)
        if hasattr(p, "stop_service"):
            p.stop_service()
            return p.poll()
        if p.poll() is None:
            lead = False
            try:
                lead = os.getpgid(p.pid) == p.pid and p.pid != self.pgid   # a child leading its own group (its own kids)
            except OSError:
                pass
            for sig, w in ((signal.SIGTERM, wait), (signal.SIGKILL, 2.0)):
                try:
                    os.killpg(p.pid, sig) if lead else os.kill(p.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    p.wait(w)
                    break
                except subprocess.TimeoutExpired:
                    continue
        return p.poll()

    def restart(self, name: str, planned: bool = True) -> subprocess.Popen:
        """Stop one child and start it again. `planned` (a Restart from the Control Panel, Init's microphone pick) is
        kept apart from a restart because it fell over (lib/crissa_watch): the tiles count only the unplanned ones (the
        User 2026-10-10, .101 round 4: "restarted x17" on stt counted on purpose and by accident alike)."""
        argv, env, port = self.specs[name]
        rc = self.stop_one(name)
        if self.table is not None:
            c = self.table.child(name)
            key = "planned_restarts" if planned else "restarts"
            c[key] = int(c.get(key) or 0) + 1
            c["last_exit"] = rc
        return self.spawn(name, argv, env, port)

    def stop(self) -> None:
        had_units = False
        for p in list(self.procs.values()):
            if hasattr(p, "stop_service"):
                had_units = True
                try:
                    p.stop_service()
                except Exception:  # noqa: BLE001  a unit that will not stop is still named by its poll
                    pass
        if self.pgid is None:
            if had_units:
                stop_strays()
            return
        for sig, wait in ((signal.SIGTERM, STOP_WAIT_S), (signal.SIGKILL, 2.0)):
            for pg in [self.pgid] + sorted(self.extra_pgids):
                try:
                    os.killpg(pg, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            for p in self.procs.values():              # a child that left the group still stops
                if hasattr(p, "stop_service") or p.poll() is not None:
                    continue
                try:
                    os.kill(p.pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            groups = [self.pgid] + sorted(self.extra_pgids) if stop_waits_group_on() else []
            end = time.time() + wait
            while time.time() < end and (any(p.poll() is None for p in self.procs.values())
                                         or any(_group_alive(pg) for pg in groups)):
                time.sleep(0.1)
            if all(p.poll() is not None for p in self.procs.values()) and not any(_group_alive(pg) for pg in groups):
                break
        stop_strays()


# A STOP WAITS FOR THE WHOLE GROUP (round 37, 2026-10-20): the stop signalled the children's process groups but waited
# only for its own children, so a grandchild still dying (the desk Wall's prefill decoder, house-conductor.py
# --clip-frames, which leaves only when it has loaded the library and read its stdin's end) outlived "Crissa stopped"
# under load (round 36's cut 9 and rehearsal (B), the acceptance's "children left"). Now the TERM wait lasts until every
# group is empty, and what is left gets the KILL. Kill CRISSA_STOP_WAITS_GROUP=0.
def stop_waits_group_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_STOP_WAITS_GROUP", "1")).strip().lower() not in ("0", "off", "false", "no")


def _group_alive(pg: int | None) -> bool:
    """Any process left in group pg (signal 0 sends nothing)."""
    if not pg:
        return False
    try:
        os.killpg(pg, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def ready_check(g: Group, name: str, timeout: float | None = None) -> "tuple[bool, str]":
    """After a restart or start: does the child stay up and answer its port? (ok, a note that names what happened)."""
    p = g.procs[name]
    port = g.specs[name][2]
    timeout = crissa_procs.READY_S.get(name, crissa_procs.READY_DEFAULT_S) if timeout is None else timeout
    end = time.time() + timeout
    log = g.logs / ("%s.log" % name)
    if not port:
        time.sleep(min(3.0, timeout))
        if p.poll() is not None:
            return False, "%s fell over: %s" % (name, crissa_procs.exit_reason(p.returncode, log))
        return True, "%s restarted (pid %d)" % (name, p.pid)
    while time.time() < end:
        if p.poll() is not None:
            return False, "%s fell over before :%d answered: %s" % (name, port, crissa_procs.exit_reason(p.returncode, log))
        if crissa_ports.in_use(port):
            return True, "%s restarted · :%d answering (pid %d)" % (name, port, p.pid)
        time.sleep(0.5)
    return False, "%s is running but :%d did not answer in %ds%s" % (
        name, port, int(timeout), (" · " + crissa_procs.last_words(log)) if crissa_procs.last_words(log) else "")


# A voice Init installs while the Wall runs (lib/crissa_voice_install, 2026-10-07): its lane is planned at start,
# registered as waiting, and started by a "start" request once installed, so she speaks without a restart.
LATE_LANES: dict = {}
# A VOICE RUNS ONLY WHILE A ROLE USES IT (round 17, 2026-10-13, the User: "agree on stoping not used software"; .101
# 2026-10-11: Pocket in use at 0.9 GB RSS, Kokoro 1.1 GB and Audio8 1.8 GB resident and idle). The voice lanes whose
# engine no role uses (her replies, Lila and Setup, the announcer: lib/crissa_voice_demand.roles) are planned but HELD
# here: not started, "installed, not running" on the Control Panel, never restarted by the watch, and started through
# the request queue when a line's ladder falls back to one (lib/crissa_voice_demand.ensure). voice_demand_pass stops a
# started one again after on_demand.idle_stop_s unasked, and starts a held one a role uses after a re-pick. Linux (desk
# units) and a Mac (one process group) alike. Kill CRISSA_VOICE_ENGINES_ON_DEMAND=0 (every installed engine of a chain
# starts and stays up, as before).
STANDBY: set = set()
# audio8: the announcer's first voice on a robust CPU machine; multilingual: Init's pick for another language
LATE_ENGINES = ("multilingual", "turbo", "pocket", "audio8")


def named_voice(a, env=None) -> str:
    """The engine the person named for her: --tts, else CRISSA_TTS, else agent.json's tts engine ('' = Init's pick)."""
    try:
        import crissa_voice_demand as VD
    except ImportError:
        return (getattr(a, "tts", "") or "").strip().lower()
    return (getattr(a, "tts", "") or "").strip().lower() or VD.named_engine(env)


def voice_standby(a, env=None) -> set:
    """The voice lanes this start plans and holds (installed, not running): each whose engine no role uses."""
    try:
        import crissa_voice_demand as VD
    except ImportError:
        return set()
    planned = [lane for lane in VD.LANE_ENGINE if getattr(a, "with_" + lane, False)]
    return VD.standby_at_start(planned, getattr(a, "tier", None) or {}, named_voice(a, env), env)


def voice_demand_pass(g, home, state: dict, now: float | None = None) -> list:
    """One pass of the rule (at most every on_demand.every_s): a held lane a role uses now starts and stays; a running
    lane no role uses, unasked for on_demand.idle_stop_s, stops and is held; a lane Init installed that no role uses is
    held. Nothing while Close Agent holds her voices or everything is stopping. Returns what it did."""
    try:
        import crissa_voice_demand as VD
    except ImportError:
        return []
    if not VD.on():
        return []
    now = time.time() if now is None else now
    if now - float(state.get("at") or 0) < VD.every_s(home):
        return []
    state["at"] = now
    if crissa_watch.agent_closed(home) or g.going_down():
        return []
    try:
        import crissa_voice_tiers as VT
        rec = VT.read(home) or state.get("tier") or {}
        installed = VT.installed
    except Exception:  # noqa: BLE001  no tier code: the pick the launcher started from
        rec, installed = state.get("tier") or {}, (lambda e: False)
    named = str(state.get("named") or "")
    use = VD.in_use(rec, named)
    if use is None:
        return []
    lanes, specs = {}, {}
    for lane, eng in VD.LANE_ENGINE.items():
        k = LATE_LANES.get(lane)
        spec = g.specs.get(lane) or ((k["argv"], k["env"], k["port"]) if k else None)
        if spec is None:
            continue                                  # not planned on this machine (not installed, or --tts-url)
        specs[lane] = spec
        waiting = g.table.child(lane).get("state") == "waiting"
        lanes[lane] = {"alive": g.alive(lane), "standby": lane in STANDBY, "waiting": waiting,
                       "installed": bool(waiting and installed(eng))}
    last = {eng: VD.last_use(eng, home) for eng in VD.ENGINE_LANE}
    did = []
    for act, lane, why in VD.decide(lanes, use, last, state.setdefault("since", {}), now, VD.idle_stop_s(home)):
        eng = VD.LANE_ENGINE[lane]
        if act == "start":
            STANDBY.discard(lane)
            g.specs[lane] = specs[lane]
            try:
                g.spawn(lane, *specs[lane])
            except Exception as e:  # noqa: BLE001  a lane that will not start is named, never a launcher crash
                g.table.exited(lane, None, why="could not start: %s: %s" % (type(e).__name__, e))
                continue
            line = "voice   %s: %s uses it now: started, and it stays up" % (eng, VD.who_uses(rec, named, eng) or why)
        else:
            STANDBY.add(lane)                         # held first, so the watch never starts it again
            if act == "stop":
                rc = g.stop_one(lane)
                g.table.exited(lane, rc, expected=True, why=why)
                line = "voice   %s: %s: stopped (installed, not running; a line that needs it starts it)" % (eng, why)
            else:
                line = "voice   %s: installed, no role uses it: held (not started; a line that needs it starts it)" % eng
            argv, env, port = specs[lane]
            LATE_LANES[lane] = {"name": lane, "argv": argv, "env": env, "port": port}
            g.table.mark(lane, state=VD.STANDBY, why=VD.STANDBY_WHY)
        _log(line)
        VD.note(line.replace("voice   ", "launcher: "), home)
        did.append("%s %s" % (act, lane))
    return did


def late_voice_lanes(a, p: dict) -> dict:
    """{engine: child} for each streamed voice in Init's order that this start did not start (not installed yet)."""
    if not p.get("voice") or getattr(a, "tts_url", None) or (getattr(a, "tts", "") or "").strip():
        return {}
    if os.environ.get("CRISSA_INIT_VOICE_INSTALL", "1").strip() == "0":
        return {}
    have = {k["name"] for k in p["children"]}
    import copy
    b = copy.copy(a)
    for eng in LATE_ENGINES:
        setattr(b, "with_" + eng, eng not in have)
    return {k["name"]: k for k in plan(b)["children"]
            if k["name"] in LATE_ENGINES and k["name"] not in have}


def install_voice_on_start(a, home: Path, env=None) -> bool:
    """A pack whose voice is not installed and whose Init walk will not run (CRISSA_INIT off, or already done):
    install it now, in the background, the way Init does (lib/crissa_voice_install), and start its lane when it is
    there. Every start tries again while it is missing, so "restart Crissa" is a retry too. The person sees the
    progress on the Connect page; the launcher's log keeps every line. True when an install was started."""
    env = os.environ if env is None else env
    if a.no_voice or not a.voice or getattr(a, "tts_url", None) or (getattr(a, "tts", "") or "").strip():
        return False
    if init_needed(env):
        return False                                   # Init's Machine step installs it, with its plate
    want = [e for e in (a.tier.get("chain") or [])[:1] if e in LATE_LANES]
    want += [e for e in (a.tier.get("announcer_chain") or [])[:1] if e == "audio8" and e in LATE_LANES]   # the announcer
    if not want:
        return False                                   # her voice is installed, needs no install, or is text
    try:
        import crissa_voice_install as VI
    except ImportError:
        return False
    if not VI.enabled(env) or VI.running(home):
        return False
    import threading
    _log("voice   installing %s (progress on the Connect page)" % VI.label(want[0]))

    def _install():
        res = VI.run(home, progress=lambda line: _log("voice   " + str(line)[:300]),
                     start=lambda eng: crissa_procs.request(home, eng, "start"))
        done = str((res or {}).get("line") or "")       # the install's own plain sentence, for the console too
        if done:
            _log("voice   " + done, plain=done, error=not (res or {}).get("ok"))
    threading.Thread(target=_install, name="voice-install", daemon=True).start()
    return True


def stop_off_loop_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_STOP_OFF_LOOP", "1")).strip().lower() not in ("0", "off", "false", "no")


def apply_avatar(g, home: Path, env: dict | None = None) -> str:
    """The player's spec wears the picked library on its next start: HOUSE_POSE_DIR, the library's own frame switch
    and the face's shipped mouth defaults are rewritten in g.specs["player"] from <home>/avatar.json
    (lib/crissa_avatar). Returns the face key."""
    argv, penv, port = g.specs["player"]
    face, pose_dir = crissa_avatar.pose_dir(ROOT, home, None, env)
    penv = dict(penv, HOUSE_POSE_DIR=pose_dir)
    e = os.environ if env is None else env
    for key in [k for k in penv if crissa_avatar.is_library_key(k)]:   # the last pick's own keys go with the last pick
        if key not in e:
            penv.pop(key, None)
    # the shipped mouth defaults, then the library's own keys over them, as plan() orders them (cut 10: a skin switch
    # put the shipped profile-45 back over the library's own turn libraries); a variable in the environment wins
    for key, val in mouth_env(face).items():
        if key not in e:
            penv[key] = val
    for key, val in crissa_avatar.player_env(pose_dir, e).items():
        if key not in e:
            penv[key] = val
    g.specs["player"] = (argv, penv, port)
    if "wall" in g.specs:                            # the Wall's pictures follow the pick on its next start (2026-10-30)
        wargv, wenv, wport = g.specs["wall"]
        g.specs["wall"] = (wargv, dict(wenv, HOUSE_POSE_DIR=pose_dir), wport)
    apply_phone(g, home, env)                        # the phone lane's spec follows too; it restarts only on its own
    try:
        g.table.data["launcher"]["face"] = face
        g.table.write()
    except (AttributeError, KeyError, TypeError):
        pass
    return face


def apply_phone(g, home: Path, env: dict | None = None, mark: bool = False) -> dict | None:
    """CRISSA_AGENT_PHONE: the phone lane's spec serves the worn library's Mobile packs (lib/crissa_skin_phone). A
    player restart updates the spec only (a phone session is never cut); a phone restart marks what it starts with."""
    if "phone" not in g.specs:
        return None
    try:
        import crissa_skin_phone
        if not crissa_skin_phone.enabled(env):
            return None
        r = crissa_skin_phone.resolve(home, ROOT, env)
        if not Path(r["packs"]).is_dir():
            return None
        argv, penv, port = g.specs["phone"]
        g.specs["phone"] = (crissa_skin_phone.argv_for(list(argv), r), penv, port)
        if mark:
            crissa_skin_phone.mark_served(r, home)
        return r
    except Exception:  # noqa: BLE001  the phone keeps its last folder rather than fail a restart
        return None


def handle_request(g: Group, home: Path, req: dict, threads: list) -> None:
    """One Restart / Stop / Start from the panel. The answer lands in <home>/run/requests/<name>.done.json."""
    import threading
    name, act, job = req["name"], req["action"], req["job"]
    if name not in g.specs and name in LATE_LANES and act == "start":
        k = LATE_LANES[name]                         # a voice Init just installed: start its planned lane
        g.specs[name] = (k["argv"], k["env"], k["port"])
        act = "start"
    if name not in g.specs:
        crissa_procs.finish(home, name, job, False, "%s is not one of the pieces this launcher started" % name)
        return
    if act == "stop":
        if name == "player":
            crissa_procs.finish(home, name, job, False, "the player is the Wall itself - stop Crissa from its terminal "
                                "(Restart is fine)")
            return

        def _stop():
            rc = g.stop_one(name)
            g.table.exited(name, rc, expected=True, why="stopped from the Control Panel")
            crissa_procs.finish(home, name, job, True, "%s stopped" % name)
        if stop_off_loop_on():
            # THE LAUNCHER BEATS THROUGH A WALL CLOSE (the User 2026-10-24, .101: `systemctl --user stop crissa-wall`
            # waits on the Wall's exit - up to its TimeoutStopSec - on the loop that writes the heartbeat, so the beat
            # went stale and --open-wall was refused "the launcher is gone"). The stop runs beside the loop, as a
            # start's ready check does. Kill: CRISSA_STOP_OFF_LOOP=0.
            t = threading.Thread(target=_stop, name="stop-" + name, daemon=True)
            t.start()
            threads.append(t)
        else:
            _stop()
        return
    if act == "start" and g.alive(name):
        crissa_procs.finish(home, name, job, True, "%s is already running" % name)
        return
    STANDBY.discard(name)                            # a held voice asked for (a line, the panel): watched from its start
    try:
        g.table.mark(name, state="restarting")
        if act == "restart":
            if name == "player":
                apply_avatar(g, home)                # the panel's pick lands on this restart (2026-10-26)
            elif name == "phone":
                apply_phone(g, home, mark=True)      # the worn agent's Mobile packs, once no session is open
            g.restart(name)
        else:
            argv, env, port = g.specs[name]
            g.spawn(name, argv, env, port)
    except Exception as e:  # noqa: BLE001  a child that will not start is named, never a launcher crash
        g.table.exited(name, None, why="could not start: %s: %s" % (type(e).__name__, e))
        crissa_procs.finish(home, name, job, False, "%s could not start: %s: %s" % (name, type(e).__name__, e))
        return

    def _check():
        ok, note = ready_check(g, name)
        crissa_procs.finish(home, name, job, ok, note)
    t = threading.Thread(target=_check, name="ready-" + name, daemon=True)
    t.start()
    threads.append(t)


# A STRAY IS ONE OF HER PROGRAMS, NOT A FILE SOMEONE HAS OPEN (round 46, the local review): the match was a substring of
# the whole command line, so an editor, a pager or a grep holding one of these scripts' paths was SIGTERMed with them.
# Now the script must be the program: argv[0] itself, or the first non-option argument of a python interpreter. Kill
# CRISSA_STRAYS_BY_ARGV=0 (the substring match).
def strays_by_argv_on() -> bool:
    return os.environ.get("CRISSA_STRAYS_BY_ARGV", "1").strip().lower() not in ("0", "off", "false", "no")


def _stray_argv(cmd: str, mine: list) -> bool:
    """`cmd` (ps's command column) runs one of `mine` as its program."""
    argv = cmd.split()
    if not argv:
        return False
    if argv[0] in mine:
        return True
    if "python" not in os.path.basename(argv[0]):
        return False
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        return a in mine
    return False


def stop_strays() -> int:
    """The aligner and any line the player started in a session of its own, from THIS tree only."""
    mine = [str(ROOT / s) for s in STRAYS]
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    n = 0
    for line in out.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if not pid_s.isdigit() or int(pid_s) == os.getpid():
            continue
        if (_stray_argv(cmd, mine) if strays_by_argv_on() else any(m in cmd for m in mine)):
            pid = int(pid_s)
            try:
                if os.getpgid(pid) == pid:      # the aligner leads its own group (its MFA child is in it)
                    os.killpg(pid, signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
                n += 1
            except (ProcessLookupError, PermissionError):
                pass
    return n


def wait_player(url: str, proc: subprocess.Popen, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            with crissa_egress.urlopen(url + "/v1/state", 2, "the player") as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def log_tail(path: Path, n: int = 600) -> str:
    try:
        return path.read_bytes()[-n:].decode("utf-8", "replace").replace("\n", " | ")
    except OSError:
        return ""


def why_tail(path: Path, n: int = 400) -> str:
    """The end of a log for a reason line: whole lines only (a cut first line is dropped)."""
    t = log_tail(path, n)
    return t.split(" | ", 1)[1] if " | " in t and len(t) >= n - 10 else t


def run(a) -> int:
    """One run, recorded: launcher.json and logs/launcher.log say it started, how it ended and why, so the service
    that starts it again (lib/crissa_service) and the Control Panel can say why."""
    try:
        import crissa_home
    except ImportError:
        _log("lib/crissa_home.py is missing: run this from a Crissa tree", error=True)
        return 2
    if crissa_home.is_house() and not a.allow_house:
        _log("this tree is the house's own install, which runs as its own services; the launcher is for a pack",
             error=True)
        return 2
    CON.path = crissa_home.HOME / "logs" / "crissa.log"
    CON.write("---- start (pid %d): %s" % (os.getpid(), " ".join(sys.argv)))
    why = {"text": ""}
    rec = crissa_service.launcher_started(crissa_home.HOME)
    try:
        ended = crissa_watch.agent_at_start(crissa_home.HOME, rec)   # round 17: Close Agent is a pause of one session
    except Exception:  # noqa: BLE001  never a start's failure
        ended = ""
    if ended:
        _log("agent   Close Agent ended with the last session (%s): she starts open" % ended,
             plain="Crissa starts with her agent open (Close Agent lasts until Crissa stops).")
    rc = 1
    try:
        rc = _run(a, crissa_home, why)
    except BaseException as e:  # noqa: BLE001  recorded, then raised as before
        why["text"] = why["text"] or "the launcher itself failed: %s: %s" % (type(e).__name__, e)
        raise
    finally:
        shadow = rec if (rec or {}).get("shadow") else None
        if why.get("shadow_end") and not (shadow or {}).get("shadow"):
            shadow = {"shadow": True, "pid": os.getpid(), "under": (rec or {}).get("under") or "hand"}
        crissa_service.launcher_ended(rc, why["text"], crissa_home.HOME, shadow=shadow)
    return crissa_service.service_rc(rc)


LEFTOVER_WAIT_S = 15.0


def take_back_leftovers(taken: list, *, wait_s: float = LEFTOVER_WAIT_S, sleep=time.sleep) -> list:
    """A FIRST BOOT TAKES BACK ITS OWN LEFTOVER (the User 2026-10-10, .101 round 5 HH): the lanes in `taken` whose port
    is held by this install's own crissa-<lane>.service, left running with no launcher, are stopped and their ports
    waited for (lib/crissa_service.own_leftovers says which are ours). Returns what is still taken: a port held by
    anything else stays refused."""
    own = crissa_service.own_leftovers([n for n, _p in taken])
    if not own:
        return taken
    _log("found this install's own %s still running from an earlier run, with no launcher: stopping %s first"
         % (", ".join(crissa_service.desk_unit_name(n) for n in own), "it" if len(own) == 1 else "them"),
         plain="An earlier run of Crissa left %s running; Crissa is stopping %s and starting fresh."
         % (", ".join(_cap(PLAIN_LANES.get(n, n)) for n in own), "it" if len(own) == 1 else "them"))
    crissa_service.stop_desk_units(own)
    end = time.time() + wait_s
    left = [(n, port) for n, port in taken if crissa_ports.in_use(port)]
    while left and time.time() < end:
        sleep(0.25)
        left = [(n, port) for n, port in left if crissa_ports.in_use(port)]
    return left


def crissa_running_words(home=None, player_url: str = "") -> str:
    """'' unless Crissa herself already runs here; then the whole refusal in plain words: she is running, how to open
    her and how to stop her (the User 2026-10-11, .101 round 6 II: a second start while she ran listed fourteen taken
    ports). Another live launcher's record counts (this run has already written its own, so its own pid never does),
    and so does the launcher's unit running."""
    L = crissa_service.read_launcher(home)
    if L.get("alive") and L.get("pid") != os.getpid():
        svc = L.get("under") == "systemd"
        how = "pid %s, %s" % (L.get("pid"), "under the service" if svc else "started by hand")
    elif crissa_service.manager() == "systemd" and crissa_service.handoff_allowed() and \
            crissa_service.launcher_unit_running():
        svc, how = True, "as crissa-launcher.service"
    else:
        return ""
    wall = ("%s/wall" % player_url.rstrip("/")) if player_url else "the Wall"
    return ("Crissa is already running here (%s), so nothing was started. Open her: %s. Or stop her first with Stop "
            "Crissa in the Crissa key's menu%s, then start again." % (
                how, wall, " or: systemctl --user stop crissa-launcher" if svc else ", or Ctrl-C where she was started"))


def open_wall_command(python: str | None = None) -> str:
    """The one line that opens a closed desk Wall from a terminal: --open-wall asks the launcher that runs to start it
    (and, when she is not running, starts her with it)."""
    import shlex
    return "%s %s --open-wall" % (shlex.quote(python or sys.executable), shlex.quote(str(ROOT / "scripts" / "crissa.py")))


def wall_closed_words(home=None, player_url: str = "", python: str | None = None) -> str:
    """'' unless the desk Wall was closed on purpose (lib/crissa_watch CLOSED, which holds across starts); then the line
    a start says where the person typed it (the User 2026-10-11, .101 round 9: a Close the Wall before the upgrade kept
    the Wall closed, the launcher's log alone said "wall closed by you: not started", and he took Crissa for not
    running). CRISSA_WALL_CLOSED_SAY=0 keeps the console as it was."""
    if str(os.environ.get("CRISSA_WALL_CLOSED_SAY", "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    try:
        rec = crissa_watch.closed(Path(home) if home else None, "wall")
    except Exception:  # noqa: BLE001  an unreadable record is an open Wall, as the watch reads it
        rec = {}
    if not rec:
        return ""
    where = (" (her browser Wall: %s/wall, or the Control Panel)" % player_url.rstrip("/")) if player_url else ""
    return "Crissa is running; the Wall is closed \u2014 open it from the Crissa key%s or: %s" % (
        where, open_wall_command(python))


def _has_wall(p: dict) -> bool:
    """This start has a desk Wall (not --no-desk): only then is a closed Wall worth a word."""
    return any(k.get("name") == "wall" for k in (p or {}).get("children") or [])


def open_wall_now(home, why: dict) -> int:
    """--open-wall while she runs: the running launcher starts the Wall, as Open the Wall in the menu does (the close is
    cleared first, so it also opens at her next start). Says what happened; 0 when the launcher took it."""
    import crissa_menu
    got = crissa_menu.wall_open(home)
    why["text"] = "--open-wall: %s" % ("asked the running launcher" if got.get("ok") else "not taken")
    if got.get("ok"):
        _say("Crissa is running; opening the Wall now.")
        return 0
    _log(str(got.get("error") or "the Wall did not open"), error=True)
    return 3


def port_holder_words(home=None, base_ports=None) -> str:
    """Who holds a taken port, as far as the launcher can tell without guessing (the refusal's second sentence).
    `base_ports`: every port this run would bind, WITHOUT its offset - the hint then names an offset at which every one
    of them is free (the User 2026-10-26, the crissatest walk: "try --port-offset 100" was said again after +100 had
    been tried, and +100 put the feed on a port another program held; he had to guess his way to +110)."""
    mine = crissa_running_words(home)
    if mine:
        return mine
    off = crissa_ports.free_offset(base_ports) if base_ports else None
    if off is None:
        return "Another install or program is using it here; stop it, or try --port-offset 100."
    return "Another install or program is using it here; stop it, or try --port-offset %d (every port is free there)." % off


def _handoff_now(a, crissa_home, why: dict) -> bool:
    """After the screen check, before any child binds: start crissa-launcher.service and leave.

    The unit's own process has CRISSA_SERVICE=systemd, so it does not hand off again. A failed start
    falls through and this process starts the desk by hand. CRISSA_SERVICE_HANDOFF=0 keeps that path.
    """
    if not crissa_service.handoff_allowed():
        return False
    got = crissa_service.handoff_to_service(list(getattr(a, "args", None) or []), crissa_home.HOME, env=PERSON_ENV)
    if got.get("handed"):
        why["shadow_end"] = True
        why["text"] = "handed to crissa-launcher.service"
        _say("Crissa is starting as crissa-launcher.service")
        # how to stop and start it, said where the person typed the command (the User 2026-10-10, .101: Ctrl-C does
        # not reach a service, and nothing said what does)
        _say("Stop her with All OS Off in the Crissa key's menu (bottom left of the Wall), or: "
             "systemctl --user stop crissa-launcher. Start her again: systemctl --user start crissa-launcher")
        return True
    _log("desk service did not start (%s); starting it by hand" % (got.get("note") or "not started"))
    return False


def _run(a, crissa_home, why: dict) -> int:
    who = face_name(a)
    _say("Starting Crissa…" if who == "Crissa" else "Starting Crissa (%s)…" % who)
    # THE LIVE SESSION'S DISPLAY KEYS, at this start (the User 2026-10-24, .101: a reboot + `systemctl --user start`
    # ran the Wall on the install session's XAUTHORITY and it crash-looped on "couldn't connect to display"). The
    # launcher's own environment first; each child reads them again at its own start (crissa_service.child_launch).
    # Kill: CRISSA_LIVE_SESSION_ENV=0.
    for k, (was, now) in sorted(crissa_service.adopt_live_session().items()):
        _log("session %s: %s (the unit carried %s)" % (k, now, was if was is not None else "nothing"))
    for line in (getattr(a, "tier", None) or {}).get("say") or []:
        _log(line)
    p = plan(a)
    if crissa_service.desk_units_on():
        crissa_service.stop_desk_units(k["name"] for k in p["children"])
    taken = [(k["name"], k["port"]) for k in p["children"] if k["port"] and crissa_ports.in_use(k["port"])]
    if taken and not crissa_service.desk_units_on():
        taken = take_back_leftovers(taken)
    if taken:
        mine = crissa_running_words(crissa_home.HOME, p.get("player") or "")
        if mine and getattr(a, "open_wall", False):
            return open_wall_now(crissa_home.HOME, why)     # --open-wall while she runs: the Wall, nothing else
        if mine:                                   # II: she is running; the ports are hers, and no list is said
            why["text"] = "Crissa is already running here: nothing started"
            _log(mine, plain=mine, error=True)
            closed = _has_wall(p) and wall_closed_words(crissa_home.HOME, p.get("player") or "")
            if closed:                             # and the Wall she runs without is closed on purpose (.101 round 9)
                _say(closed)
            return 3
        why["text"] = "port taken, nothing started: %s" % ", ".join("%s %d" % t for t in taken)
        off_now = int(getattr(a, "port_offset", 0) or 0)
        base = [int(k["port"]) - off_now for k in p["children"] if k.get("port")]
        _log("port taken, nothing started: %s. %s" % (", ".join("%s %d" % t for t in taken),
                                                       port_holder_words(crissa_home.HOME, base)), error=True)
        return 3
    problem = None if no_desk(a) else screen_problem()
    if problem:
        why["text"] = problem
        _log(problem, plain=problem, error=True)
        return 2
    if getattr(a, "open_wall", False):
        # --open-wall clears the close here, before a hand-off, so the service it starts opens the Wall too (the flag
        # itself is not carried into the service's own start: main() leaves it out of a.args)
        crissa_watch.set_closed("wall", False, home=crissa_home.HOME)
    if _handoff_now(a, crissa_home, why):
        closed = _has_wall(p) and wall_closed_words(crissa_home.HOME, p.get("player") or "")
        if closed:                                 # the service starts her without the Wall: said here (.101 round 9)
            _say(closed)
        return 0
    logs = crissa_home.HOME / "logs"
    table = crissa_procs.Table(crissa_home.HOME, os.getpid(), sys.argv)
    table.data["launcher"]["face"] = p["face"]       # which library she wears this run (the panel's Avatar tile reads it)
    table.write()
    g = Group(logs, table, use_units=crissa_service.desk_units_on())
    g.standby = STANDBY                           # the watch holds these (lib/crissa_watch, round 17)
    checks: list = []
    stopping = {"why": None}

    def on_signal(signum, _frame):
        stopping["why"] = "signal %d" % signum
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, on_signal)
    os.environ.update(crissa_ports.env_for(p["ports"]))     # this process's own reads (the aligner's port)
    rc = 0
    watch = None
    try:
        LATE_LANES.clear()
        STANDBY.clear()
        LATE_LANES.update(late_voice_lanes(a, p))
        for name in LATE_LANES:                       # known to the request queue; started once Init installs it
            table.mark(name, state="waiting", why="starts when Init has installed it")
        installing = install_voice_on_start(a, crissa_home.HOME)
        _say(voice_plain(a, installing))
        missing = sound_tools_missing(a)
        if missing:                                   # said at start, not first found by a failed line
            _log("voice   cannot play sound here: %s missing" % ", ".join(missing),
                 plain=sound_tools_plain(missing))
        if getattr(a, "open_wall", False):           # --open-wall: the person opens a Wall they closed (2026-10-10)
            crissa_watch.set_closed("wall", False, home=crissa_home.HOME)
        for kid in p["children"]:
            argv = kid["argv"]
            if kid["name"] == "wall" and crissa_watch.closed(crissa_home.HOME, "wall"):
                # A WALL THE PERSON CLOSED STAYS CLOSED (2026-10-10): not started; Open the Wall (the menu, the
                # Control Panel) starts it through the request queue, as a voice Init installed is started
                LATE_LANES["wall"] = kid
                table.mark("wall", state="closed", why="closed by you (%s): Open the Wall in the Crissa menu or the "
                           "Control Panel, or start with --open-wall"
                           % (crissa_watch.closed(crissa_home.HOME, "wall").get("by") or "closed"))
                _log("wall    closed by you: not started (Open the Wall in the menu or the Control Panel)")
                continue
            if kid["name"] == "aligner":
                import crissa_aligner
                argv = [str(crissa_aligner.ENV / "bin" / "python"), str(ROOT / "scripts" / "crissa-align.py"),
                        "--port", str(kid["port"])]
                kid["env"] = dict(kid["env"], HOUSE_ALIGN_MFA_BUNDLE=str(crissa_aligner.MODELS), CUDA_VISIBLE_DEVICES="")
            kid["argv"] = argv
            if kid["name"] == "player":     # Init installs the launcher's service with the flags it was given
                # ...and with the person's environment, by name (lib/crissa_service.carried_env, 2026-10-10)
                kid["env"] = dict(kid["env"], CRISSA_LAUNCHER_ARGS=json.dumps(list(getattr(a, "args", None) or [])),
                                  CRISSA_LAUNCHER_ENV_KEYS=json.dumps(sorted(crissa_service.carried_env(os.environ))))
            if kid["name"] in (getattr(a, "standby", None) or ()):
                # NO ROLE USES THIS VOICE (round 17, 2026-10-13): planned, held, started when a line needs it
                LATE_LANES[kid["name"]] = kid
                STANDBY.add(kid["name"])
                import crissa_voice_demand
                table.mark(kid["name"], state=crissa_voice_demand.STANDBY, why=crissa_voice_demand.STANDBY_WHY)
                _log("%-7s installed, not running: no role uses it (a line that needs it starts it)" % kid["name"])
                continue
            if kid["name"] in crissa_watch.AGENT_LANES and crissa_watch.agent_closed(crissa_home.HOME):
                # CLOSE AGENT HOLDS ACROSS STARTS (2026-10-12): her listening and speaking lanes wait for Open Agent
                LATE_LANES[kid["name"]] = kid
                AGENT_HELD.add(kid["name"])
                table.mark(kid["name"], state="closed", why=crissa_watch.AGENT_WHY % (
                    crissa_watch.agent_closed(crissa_home.HOME).get("by") or "closed"))
                _log("%-7s Close Agent: not started (Open Agent in the Crissa menu, the Control Panel or her dock icon)"
                     % kid["name"])
                continue
            g.spawn(kid["name"], argv, kid["env"], kid["port"])
            _log("%-7s %s" % (kid["name"], ("127.0.0.1:%d" % kid["port"]) if kid["port"] else "(no port)"))
        if not p["voice"]:
            _log("voice   not here (modules/voice): she answers what is typed; --voice-dir names it")
        elif not a.with_asr and not a.asr_url:
            _say("Listening: not installed on this machine, so she answers what is typed "
                 "(the voice module's README says how to add it)")
            # plain words on the console (the User 2026-10-07); how to add it is in modules/voice/README.md
            _log("voice   listening is not installed on this machine: she answers what is typed "
                 "(the voice module's README says how to add it)")
        if not wait_player(p["player"], g.procs["player"], BOOT_WAIT_S):
            if stopping["why"]:
                why["text"] = "stopped (%s) before the player came up" % stopping["why"]
                return 0
            why["text"] = "the player did not come up (exit %s): its log ends: %s" % (
                g.procs["player"].poll(), why_tail(logs / "player.log"))
            _log("the player did not come up: " + log_tail(logs / "player.log"),
                 plain="Crissa could not start: her player stopped before it was ready. Its own log: %s"
                 % (logs / "player.log"), error=True)
            stopping["why"] = "the player did not come up: " + (crissa_procs.exit_reason(
                g.procs["player"].poll(), logs / "player.log") or crissa_procs.last_words(logs / "player.log"))
            return 1
        for name in [n for n in ("panel", "wall") if n in g.procs]:
            ok, said = ready_check(g, name)
            _log("%s  %s" % (name, said))
            if not ok:
                why["text"] = said
                _log(said, plain="Crissa could not start: %s. Crissa did not stay up." % said, error=True)
                stopping["why"] = said
                return 1
        panel_url = ("http://127.0.0.1:%d/" % p["ports"]["panel"]) if "panel" in g.procs else "not in this pack"
        note = desk_wall(sys.executable, fullscreen=fullscreen_wanted(a))[1]
        _log("ready: the Wall is the screen; the Control Panel is %s (voice plays on the %s)"
             % (panel_url, "page" if a.audio_at == "page" else "computer"), plain=note)
        closed = _has_wall(p) and "wall" not in g.procs and wall_closed_words(crissa_home.HOME, p["player"])
        if closed:                                # the Wall stayed closed on purpose: never "the Wall is the screen"
            _say(closed + ". The Control Panel is %s" % panel_url)
            try:                                  # THE WALL OFF (2026-10-16): her face on top instead (lib/crissa_face)
                import crissa_face
                face = crissa_face.wall_off(p["player"], crissa_home.HOME)
                if face:
                    _say(face)
            except Exception as e:  # noqa: BLE001  a start never fails on her window
                _log("the Wall off: no face window (%s)" % type(e).__name__)
        else:
            _say("Crissa is ready. The Wall is the screen. The Control Panel is %s" % panel_url)
        _say("Stop it with Ctrl-C here, or All OS Off in the Crissa key's menu. Full log: %s" % CON.path)
        f = crissa_service.service_file()
        if crissa_service.under() == "hand" and f is not None and not f.exists():
            _log("not installed as a service: if the player dies, nothing brings Crissa back. "
                 "python3 scripts/crissa.py --service install")
        # The desk is the Wall: it opens no browser, with ONE exception - a first boot opens Setup (/init) in the
        # person's browser, once (the User 2026-10-10, .101: the desk Wall's Tk Connect plate came first and nothing
        # said where Setup was). --no-open / CRISSA_OPEN=0 keeps it closed.
        try:
            import crissa_window
            crissa_window.keep_her_sound(_log)     # .101 round 30: her page's sound on the echo canceller
        except Exception as e:  # noqa: BLE001
            _log("her window's sound: not kept (%s)" % type(e).__name__)
        open_setup_once(p["player"], a)
        dock_on_start()                           # her dock entry, written once on a Linux desktop (2026-10-11)
        demand_state: dict = {"tier": dict(getattr(a, "tier", None) or {}), "named": named_voice(a)}   # round 17
        window_keep: dict = {"at": time.time()}   # the desk-off rule's passes (keep_window)
        agent_state: dict = {"closed": bool(crissa_watch.agent_closed(crissa_home.HOME))}
        setup_state: dict = {}
        # THE PLAYER'S ADDRESS, HELD BEFORE THE LOOP (round 10, 2026-10-11): the loop below rebinds `p` to each child
        # process (`for name, p in ...`), so `p["player"]` read inside it subscripted a UnitProc / Popen on the second
        # pass and the launcher died a moment after "ready" ("TypeError: 'UnitProc' object is not subscriptable").
        player_url = p["player"]
        watch = crissa_watch.Watch({k["name"]: k for k in p["children"] if k["name"] != "player"}, g,
                                   home=crissa_home.HOME, log=lambda m: _log("lane " + m, *lane_plain(m)),
                                   tail=lambda n: log_tail(logs / ("%s.log" % n), 300))
        warned: set[int] = set()                  # by pid: a restarted child is watched afresh
        beat = 0.0
        linked = 0.0                              # the add-on link: re-checked every pass (LINK_EVERY_S)
        _cut_note()                               # the startup's held-back lines: said, and where they are
        while not stopping["why"]:
            for req in crissa_procs.take_requests(crissa_home.HOME):
                if req["action"] == crissa_procs.QUIT:
                    # STOP CRISSA (the menu, 2026-10-10): everything stops as on Ctrl-C, and the exit is 0, so the
                    # launcher's service does not start it again until the next login
                    crissa_procs.finish(crissa_home.HOME, crissa_procs.QUIT_NAME, req["job"], True, "Crissa is stopping")
                    stopping["why"] = "the Crissa menu"
                    break
                handle_request(g, crissa_home.HOME, req, checks)
            if stopping["why"]:
                break
            if not g.alive("player") and stop_of_everything(g.procs.get("player")):
                # A STOP OF EVERYTHING IS A CLEAN STOP (round 15, .101: `systemctl --user stop` left crissa-launcher
                # `failed`, status 1): systemd stops the player's unit first (it is PartOf and After the launcher), so
                # the launcher saw "the player stopped" and exited 1 before its own SIGTERM came. A player whose unit
                # was stopped on purpose while the launcher's own stop is queued is the same stop: exit 0, said plainly
                stopping["why"] = "stopped with everything (systemctl stop)"
                table.exited("player", g.procs["player"].returncode, expected=True, why="stopped with Crissa")
                break
            if not g.alive("player"):
                why["text"] = "the player stopped (exit %s): its log ends: %s" % (
                    g.procs["player"].returncode, why_tail(logs / "player.log"))
                _log("the player stopped: " + log_tail(logs / "player.log"),
                     plain="Her player stopped, so Crissa is stopping. Its own log: %s" % (logs / "player.log"),
                     error=True)
                table.exited("player", g.procs["player"].returncode,
                             why=crissa_procs.exit_reason(g.procs["player"].returncode, logs / "player.log") or why["text"])
                stopping["why"] = why["text"]
                rc = 1
                break
            # Close Agent / Open Agent (2026-10-12) BEFORE the watch (round 16, .101 00:52: the watch saw her held lanes
            # down the moment the close was cleared and brought them back itself, "restarted x1 - last: Close Agent",
            # before Open Agent could start them clean)
            agent_pass(g, crissa_home.HOME, agent_state, watch=watch)
            voice_demand_pass(g, crissa_home.HOME, demand_state)    # a voice runs only while a role uses it (round 17)
            watch.tick()                     # brings back a lane that went down, with the reason (lib/crissa_watch)
            setup_end_pass(g, crissa_home.HOME, setup_state)        # Setup's end: the desktop Wall, never the browser one
            keep_window(player_url, g, window_keep, crissa_home.HOME)   # desk Wall off: her browser Wall stays open
            for name in list(g.procs):
                p = g.procs[name]
                if name != "player" and p.poll() is not None and p.pid not in warned:
                    warned.add(p.pid)
                    if name in getattr(g, "expected", set()):
                        continue
                    if name not in watch.kids:
                        table.exited(name, p.returncode)
                        _log("%s stopped (%s); the rest keep going and nothing restarts it (not in the watch "
                             "settings' restart list). Its log: %s"
                             % (name, table.child(name).get("why") or "exit %s" % p.returncode,
                                logs / ("%s.log" % name)),
                             plain="%s stopped; the rest keep going. Its own log: %s" % (
                                 _cap(PLAIN_LANES.get(name, name)), logs / ("%s.log" % name)), error=True)
            for name, p in list(g.procs.items()):
                # ONE UNIT OWNS EACH PORT (2026-10-10, .101 finding I): a unit systemd restarted by itself (`systemctl
                # --user restart crissa-stt`) runs as a new process; the table follows it, or the panels read the old
                # pid as dead
                if isinstance(p, crissa_service.UnitProc) and p.pid and p.returncode is None \
                        and table.child(name).get("pid") != p.pid:
                    table.mark(name, pid=p.pid, state="running", by="systemd", why="", exit_code=None)
            if time.time() - linked >= crissa_addon_link.LINK_EVERY_S:
                table.data["addons"] = addon_pass(crissa_home.HOME, table.data.get("addons"), _log)
                linked = time.time()
                beat = 0.0                           # written with this beat
            if time.time() - beat >= 2.0:
                table.write()                        # the heartbeat: the panel knows the launcher is alive
                beat = time.time()
            time.sleep(0.5)
    finally:
        if stopping["why"] and not why["text"]:
            why["text"] = "stopped (%s)" % stopping["why"]
        _log("stopping" + (" (%s)" % stopping["why"] if stopping["why"] else ""), plain="Stopping Crissa…")
        close_her_window()                   # her own window goes first, cleanly (.101 round 11 HH, 2026-10-12)
        g.stop()
        if watch is not None:
            watch.stopped(stopping["why"] or ("the player stopped" if rc else "stopped"))
        for name, p in g.procs.items():
            if table.child(name).get("state") in ("running", "restarting"):
                table.exited(name, p.poll(), expected=True, why="stopped with Crissa")
        table.stop(stopping["why"] or "")
        dock_back()                          # the person's own dock again, if a Wall that died left it tidied
        _say("Crissa stopped.")
        _cut_note()
    return rc


def service_cmd(what: str, args: list) -> int:
    try:
        import crissa_home
        if crissa_home.is_house() and what == "install":
            _log("this tree is the house's own install, which runs as its own services; the launcher is for a pack")
            return 2
        home = crissa_home.HOME
    except ImportError:
        home = None
    if what == "install":
        out = crissa_service.install(args, home=home)
    elif what == "uninstall":
        out = crissa_service.uninstall()
        try:                                     # her dock entry goes with the service (2026-10-11)
            import crissa_window
            if not crissa_home.is_house():
                out["dock"] = crissa_window.remove_dock()
        except Exception as e:  # noqa: BLE001
            out["dock"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    else:
        out = crissa_service.status(home=home)
        out["health"], out["note"] = crissa_service.describe(out)
    print(json.dumps(out, indent=2))
    if what != "status":                # stdout stays one JSON document; the sentence goes to stderr
        print("crissa: " + (out.get("note") or ""), file=sys.stderr, flush=True)
    return 0 if out.get("ok", True) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # no --face and no CRISSA_FACE = the pack's default skin (crissa_avatar.default_face, 2026-10-27), else crissa
    ap.add_argument("--face", choices=sorted(FACES), default=os.environ.get("CRISSA_FACE") or None)
    ap.add_argument("--audio-at", choices=("host", "page"),
                    default=(os.environ.get("CRISSA_AUDIO_AT") or "host").strip().lower())
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("CRISSA_PORT_OFFSET") or 0))
    ap.add_argument("--voice-dir", default=None)
    ap.add_argument("--tts", default="", help="name her voice engine (multilingual, turbo, pocket, kokoro, piper, say, openai); unset = this "
                    "machine's voice tier (scripts/crissa-voice-tier.py shows and changes it)")
    ap.add_argument("--tts-url", default="",
                    help="a voice already serving (crissa-tts on another machine): CRISSA_TTS_URL for the player")
    ap.add_argument("--no-ear-module", action="store_true",
                    help="listen with the voice module's stand-ins instead of modules/ear (CRISSA_EAR_MODULE=0)")
    ap.add_argument("--asr-url", default=os.environ.get("CRISSA_ASR_URL", ""),
                    help="an ASR already serving the /stt contract (another machine on the LAN, or a test); "
                         "the bundled one is then not started")
    ap.add_argument("--no-voice", action="store_true", help="start without the voice module")
    ap.add_argument("--no-camera", action="store_true", default=os.environ.get("CRISSA_NO_CAMERA", "0").strip() == "1",
                    help="no camera at all (CRISSA_NO_CAMERA=1): no grabber, the oracle opens no device, every presence camera "
                         "call is a no-op that answers camera: off; CRISSA_CAMERA_URL names a camera elsewhere")
    ap.add_argument("--no-enroll", action="store_true", default=os.environ.get("CRISSA_NO_ENROLL", "0").strip() == "1",
                    help="start without the registration panel (CRISSA_NO_ENROLL=1)")
    ap.add_argument("--no-open", action="store_true",
                    help="on a first boot, do not open Setup (/init) in your web browser (CRISSA_OPEN=0); the desk "
                         "never opens a browser otherwise")
    ap.add_argument("--no-desk", action="store_true", default=no_desk(),
                    help="start without the Tk desk (CRISSA_NO_DESK=1): the Wall page in a browser is the screen, "
                         "for a headless host or a page-only setup")
    ap.add_argument("--fullscreen", dest="fullscreen", action="store_true", default=None,
                    help="the desk Wall full screen (CRISSA_FULLSCREEN=1); on Linux it is otherwise your desktop's "
                         "background, under your windows; F11 leaves full screen, Ctrl+Q closes the Wall")
    ap.add_argument("--windowed", dest="fullscreen", action="store_false",
                    help="never full screen, also on a Mac (CRISSA_FULLSCREEN=0)")
    ap.add_argument("--open-wall", action="store_true", default=os.environ.get("CRISSA_OPEN_WALL", "0").strip() == "1",
                    help="open the desk Wall again after you closed it (Ctrl+Q or Close the Wall keep it closed, also "
                         "across starts, until you open it: here, or Open the Wall in the Crissa menu or the panel)")
    ap.add_argument("--with-render", action="store_true", default=os.environ.get("CRISSA_RENDER", "0").strip() == "1",
                    help="keep the render module's order runner up (CRISSA_RENDER=1): pending orders on your ComfyUI")
    ap.add_argument("--first-boot", action="store_true",
                    help="start as a first boot: Setup's records move to <state home>/first-boot-archive/<time> "
                         "(nothing deleted; the installs stay), then Setup runs (round 38)")
    ap.add_argument("--plan", action="store_true", help="print what would start (and every piece's switch), then exit")
    ap.add_argument("--verbose", action="store_true",
                    default=os.environ.get("CRISSA_VERBOSE", "0").strip() == "1",
                    help="print every launcher line on the console (lanes, ports, the voice probe); without it the "
                         "console shows a few plain lines and the rest is in <crissa home>/logs/crissa.log")
    ap.add_argument("--allow-house", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--service", choices=("install", "status", "uninstall"), default=None,
                    help="the launcher as a service of your login (Linux: a systemd user unit, Restart=on-failure; "
                         "macOS: a launchd agent): install it with the other flags given here, show it, remove it")
    ap.add_argument("--uninstall", action="store_true", help="remove the launcher's service (= --service uninstall) "
                    "and her dock entry")
    ap.add_argument("--window", action="store_true",
                    help="her own window (the dock entry's press): start Crissa when she is not running, then show "
                         "her Wall - or Setup while Setup is due - in her own browser window and bring it forward")
    ap.add_argument("--dock", choices=("install", "remove", "status"), default=None,
                    help="her dock entry on a Linux desktop (written by itself at the first start): install it, take "
                         "it away, or show it")
    raw = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)
    CON.verbose = bool(a.verbose)
    # --no-open is the service's own (launch_argv adds it): left out here, so a start by hand and the service it hands
    # off to carry the same flags (CRISSA_LAUNCHER_ARGS, 2026-10-10)
    a.args = [x for i, x in enumerate(raw) if x not in ("--uninstall", "--open-wall", "--first-boot")
              and not x.startswith("--service")
              and not (i and raw[i - 1] == "--service")]      # --open-wall is a one-time act, never a service's flag
    if a.window or a.dock:
        # her own window and her dock entry need none of the app's packages: before the package check (2026-10-11)
        return window_cmd(a) if a.window else dock_cmd(a.dock, a)
    if not a.plan and not a.uninstall and a.service in (None, "install"):
        if not a.allow_house:
            try:
                import crissa_home
                house = crissa_home.is_house()
            except ImportError:
                house = False
            if house:
                # before any download: this tree is the running house, and a pack install must not land in it
                _log("this tree is the house's own install, which runs as its own services; the launcher is for a pack",
                     error=True)
                return 2
        rc = packages_ready(raw)    # before any probe, write, service file or child: plain words, never a traceback
        if rc is not None:
            return rc
        extras_ready()              # the Your phone QR code (segno): installed into our .venv, never a stop
        try:                        # round 40: espeak-ng for her mouth, through this machine's package manager, never a stop
            import threading as _th
            if str(ROOT / "lib") not in sys.path:
                sys.path.insert(0, str(ROOT / "lib"))
            import crissa_espeak
            # its words go to stderr (2026-10-24): stdout carries --service's one JSON document, which a line in front of
            # it broke on a machine without espeak-ng (GitHub's runner)
            _th.Thread(target=crissa_espeak.ensure, daemon=True, name="crissa-espeak",
                       kwargs={"log": lambda m: print(m, file=sys.stderr, flush=True)}).start()
        except Exception as _e:  # noqa: BLE001
            print("Crissa: espeak-ng check skipped (%s)" % type(_e).__name__, file=sys.stderr, flush=True)
        if a.first_boot:            # round 38: a true first boot without a reinstall (crissa_init.fresh_first_boot)
            try:
                sys.path.insert(0, str(ROOT / "lib"))
                import crissa_init
                got = crissa_init.fresh_first_boot()
            except Exception as e:  # noqa: BLE001
                got = {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
            if got.get("ok"):
                # round 40 (.101 round 38 N18, the User: "no init"): the switch is a first boot, so this start runs
                # Setup - its walk is served only with the Init switch on, which --first-boot alone left off
                os.environ["CRISSA_INIT"] = "1"
                # round 41 (.101 round 40 N18, FAIL again under systemd): the handoff to crissa-launcher.service carries
                # PERSON_ENV - the environment as the person started it, taken before this line - so the unit never had
                # the switch and /v1/init answered "no such path". The unit carries it now (its ExecStart has no
                # --first-boot: the records are not moved twice). Kill CRISSA_FIRST_BOOT_CARRY_INIT=0 (round 40).
                if os.environ.get("CRISSA_FIRST_BOOT_CARRY_INIT", "1").strip().lower() not in ("0", "off", "false", "no"):
                    PERSON_ENV["CRISSA_INIT"] = "1"
                _log("first boot from scratch: %d of Setup's records moved to %s (nothing deleted); Setup runs now"
                     % (got.get("moved") or 0, got.get("archive")))
            else:
                _log("first boot from scratch: not done (%s)" % got.get("why"), error=True)
                return 2
    if not a.uninstall and (not a.service or a.service == "install") and first_start():   # a status or stop asks nothing
        os.environ["CRISSA_INIT"] = "1"
        PERSON_ENV["CRISSA_INIT"] = "1"     # the handoff to the launcher unit carries it too (round 41's lesson)
        _log("first start: no Setup record yet, so Setup runs now (CRISSA_INIT=0 starts without it)")
    if a.uninstall or a.service:
        return service_cmd("uninstall" if a.uninstall else a.service, a.args)
    if not a.plan:                  # `crissa.py` after a whole All OS Off: On from its snapshot, the panel and add-ons too
        back = restore_whole_off()
        if back:
            _log(back)
    if a.audio_at not in ("host", "page"):
        a.audio_at = "host"
    a.no_open = a.no_open or os.environ.get("CRISSA_OPEN", "1").strip() == "0"
    a.voice = None if a.no_voice else voice_dir(a)
    a.with_asr = bool(a.voice) and asr_installed(a.voice)
    a.with_tts = bool(a.voice) and not a.tts_url and tts_installed(a.voice)
    a.tier = pick_tier(a)
    named = (a.tts or os.environ.get("CRISSA_TTS") or "").strip().lower()
    for eng in ("multilingual", "turbo", "pocket"):
        # every streamed voice in Init's chain starts when installed: the main, and the stand-ins behind it (Turbo
        # behind Multilingual only when Init found GPU room for both; Pocket behind a GPU voice). Init's pick decides.
        wanted = named == eng or (not named and (a.tier.get("main") == eng or eng in (a.tier.get("chain") or [])))
        setattr(a, "with_" + eng, bool(a.voice) and not a.tts_url and wanted and module_installed(a.voice, eng))
    # Audio8 starts when installed and in the announcer's chain or Crissa's own (her voice on a robust CPU machine)
    a.with_audio8 = bool(a.voice) and not a.tts_url and module_installed(a.voice, "audio8") and (
        "audio8" in (a.tier.get("announcer_chain") or []) or "audio8" in (a.tier.get("chain") or []))
    # the keep-warm runs beside an active GPU voice (lib/crissa_voice_warm.py; keep_warm.on in the tier table)
    a.with_warm = bool(getattr(a, "with_turbo", False) or getattr(a, "with_multilingual", False)) and warm_wanted()
    a.with_aligner = aligner_wanted()
    a.with_render = bool(getattr(a, "with_render", False))
    a.standby = voice_standby(a)            # planned but held: no role uses them (round 17, 2026-10-13)
    if a.plan:
        print(json.dumps(plan(a), indent=2))
        return 0
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
