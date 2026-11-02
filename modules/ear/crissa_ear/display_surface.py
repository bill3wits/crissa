"""Her display browser (profile <crissa home>/display, CDP :9222) - never the user's own browser.

Panel toggle: start via the pack's display script when it ships one (scripts/crissa-display); stop kills
only that profile. Without the script, status still reads the CDP port.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

from .home import HOME

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"      # modules/ear/crissa_ear -> the pack's scripts
PROFILE = Path(
    os.environ.get("DISPLAY_PROFILE", str(HOME / "display"))
).expanduser()
# Her display browser's DevTools port, when an install runs one (DISPLAY_CDP_PORT). The house's 9222 was her browser
# there; on another machine 9222 can be the person's own browser, so a pack names none and looks at none (2026-10-10).
PORT = int(os.environ.get("DISPLAY_CDP_PORT") or 0)
CRISSA_DISPLAY = Path(os.environ.get("CRISSA_DISPLAY_SCRIPT", str(_SCRIPTS / "crissa-display")))
DISPLAY_SURFACE = Path(os.environ.get("DISPLAY_SURFACE_SCRIPT", str(_SCRIPTS / "display-surface.sh")))


def cdp_up() -> bool:
    if not PORT:
        return False
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/json/version", timeout=1.5
        ) as r:
            return r.status < 400
    except Exception:
        return False


def status() -> dict[str, Any]:
    up = cdp_up()
    tabs: list[dict[str, str]] = []
    if up:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json/list", timeout=2.0
            ) as r:
                import json

                for t in json.loads(r.read().decode()):
                    if t.get("type") != "page":
                        continue
                    tabs.append(
                        {
                            "title": str(t.get("title") or "")[:60],
                            "url": str(t.get("url") or "")[:120],
                        }
                    )
        except Exception:
            pass
    return {
        "on": up,
        "cdp_port": PORT,
        "profile": str(PROFILE),
        "tabs": tabs,
        "note": "her display browser only - never the user's personal browser profile",
    }


def _pids_for_display_profile() -> list[int]:
    """PIDs whose cmdline includes our display user-data-dir (not personal Brave)."""
    needle = str(PROFILE)
    pids: list[int] = []
    try:
        out = subprocess.check_output(["ps", "ax", "-o", "pid=,args="], text=True)
    except Exception:
        return pids
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_s, args = line.split(None, 1)
        except ValueError:
            continue
        if "brave" not in args.lower() and "chrome" not in args.lower():
            continue
        if needle not in args and f"--user-data-dir={needle}" not in args:
            # also match expanded path variants
            if str(PROFILE.resolve()) not in args:
                continue
        try:
            pids.append(int(pid_s))
        except ValueError:
            pass
    return pids


def ensure_on() -> dict[str, Any]:
    if cdp_up():
        return {"ok": True, "on": True, "action": "already_up", **status()}
    script = CRISSA_DISPLAY if CRISSA_DISPLAY.is_file() else DISPLAY_SURFACE
    if not script.is_file():
        return {"ok": False, "error": f"missing {script}", "on": False}
    try:
        # ensure = start/pin shell with omni rail
        subprocess.Popen(
            ["bash", str(script)] + (["ensure"] if script.name == "crissa-display" else []),
            stdout=open("/tmp/panel-display-on.log", "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "on": False}
    for _ in range(25):
        time.sleep(0.4)
        if cdp_up():
            return {"ok": True, "on": True, "action": "started", **status()}
    return {
        "ok": False,
        "on": False,
        "error": "CDP not up after start — see /tmp/panel-display-on.log",
        **status(),
    }


def ensure_off() -> dict[str, Any]:
    pids = _pids_for_display_profile()
    killed: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
        except Exception:
            pass
    # wait, then SIGKILL stragglers
    time.sleep(0.8)
    for pid in _pids_for_display_profile():
        try:
            os.kill(pid, signal.SIGKILL)
            if pid not in killed:
                killed.append(pid)
        except Exception:
            pass
    # clear stale locks so next ensure can start
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (PROFILE / name).unlink(missing_ok=True)  # type: ignore[arg-type]
        except TypeError:
            p = PROFILE / name
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        except OSError:
            pass
    return {
        "ok": True,
        "on": cdp_up(),
        "action": "stopped",
        "killed": killed,
        **status(),
    }


def set_on(on: bool) -> dict[str, Any]:
    return ensure_on() if on else ensure_off()
