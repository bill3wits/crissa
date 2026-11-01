"""Dev mode and the tuning demo (the User 2026-10-08).

Dev mode is the Control Panel gate. Off, the demo controls stay hidden
and the scripted lap stays off. On, the panel shows the demo, the hold,
the extra beats, and Record your lines.

The demo plays the wavs in the Crissa data folder. A question with no
file is skipped. The off file stops the lap at the next beat. The hold
file stops the next beat so the user can speak. Neither one pauses
Listening or homes the wall. Extra beats run only while their file exists.
A fail stays in the ledger.

Kill the wait with HOUSE_DEMO_HOLD=0. Kill extra beats with HOUSE_DEMO_DEV=0.

The crew drill (the User 2026-10-09) is its own key on the same drawer and lives
in lib/demo_krew_drill (house-only): this module reaches it through _crew_drill()
and an install without it has no such key. It needs Dev mode, and it is
independent of the voice lap.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import crissa_home
import crissa_ports

HOME = crissa_home.HOME
HOLD_FILE = HOME / "demo-hold"
DEV_FILE = HOME / "demo-dev.on"
MODE_FILE = HOME / "demo-mode.on"
OFF_FILE = HOME / "xspaces-live.off"
PID_FILE = HOME / "xspaces-live.pid"
LOG_FILE = HOME / "xspaces-live.log"
ROUNDS_LOG = HOME / "demo-rounds.jsonl"
DRILL_DIR = HOME / "xspaces-drill"
PORT = crissa_ports.port("demo")      # 8124 + this install's offset (the shared table, 2026-10-10)
ROOT = Path(__file__).resolve().parent.parent

_OFF = ("0", "off", "false", "no")


def _flag(env: dict | None, name: str, default: str) -> str:
    e = os.environ if env is None else env
    return str(e.get(name, default)).strip().lower()


def hold_armed(env: dict | None = None) -> bool:
    return _flag(env, "HOUSE_DEMO_HOLD", "1") not in _OFF


def dev_armed(env: dict | None = None) -> bool:
    return _flag(env, "HOUSE_DEMO_DEV", "1") not in _OFF


def script_held(env: dict | None = None) -> bool:
    """True only when the wait is armed and the file is present."""
    return hold_armed(env) and HOLD_FILE.exists()


def dev_on(env: dict | None = None) -> bool:
    """True only when dev beats are armed and the file is present."""
    return dev_armed(env) and DEV_FILE.exists()


def _write(path: Path, on: bool) -> None:
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"at": time.time()}) + "\n", encoding="utf-8")
        tmp.replace(path)
    else:
        path.unlink(missing_ok=True)


def set_hold(on: bool) -> None:
    _write(HOLD_FILE, on)


def set_dev(on: bool) -> None:
    _write(DEV_FILE, on)


def mode_on() -> bool:
    return MODE_FILE.exists()


def _read_pid() -> int:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lap_alive() -> bool:
    return _pid_alive(_read_pid())


def _stop_lap() -> None:
    """The lap notices this file at the next beat and exits on its own."""
    OFF_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not OFF_FILE.exists():
        OFF_FILE.write_text("", encoding="utf-8")


def start_demo() -> bool:
    """Start one lap process. A live pid is left alone."""
    if lap_alive():
        return True
    script = ROOT / "scripts" / "xspaces-live.py"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "ab", buffering=0)
    try:
        env = os.environ.copy()
        env["CRISSA_DEMO"] = "1"
        env["CRISSA_DEMO_BROADCAST"] = "1"
        env["HOUSE_XSPACES_LIVE"] = "1"
        env["HOUSE_XSPACES_BROADCAST"] = "1"
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        return False
    finally:
        log.close()
    return True


def set_mode(on: bool) -> None:
    """Dev mode. Turning it off stops the lap. Turning it on does not start one."""
    _write(MODE_FILE, on)
    if not on:
        _stop_lap()


def set_demo(on: bool) -> bool:
    """The tuning lap. Refuses to start while Dev mode is off."""
    if not on:
        _stop_lap()
        return True
    if not mode_on():
        return False
    OFF_FILE.unlink(missing_ok=True)
    if start_demo():
        return True
    _stop_lap()
    return False


def _crew_drill():
    """lib/demo_krew_drill when this install carries it (the house); None in a pack."""
    try:
        import demo_krew_drill
    except ImportError as exc:
        if exc.name != "demo_krew_drill":
            raise
        return None
    return demo_krew_drill


def _fresh_note() -> str:
    """The lap's last line, only just after a start that did not stay up."""
    if lap_alive() or OFF_FILE.exists():
        return ""
    try:
        if time.time() - LOG_FILE.stat().st_mtime > 20:
            return ""
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return (lines[-1] if lines else "")[:160]


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def apply_body(body: dict) -> dict:
    if "mode" in body:
        set_mode(_as_bool(body.get("mode")))
    if "hold" in body:
        set_hold(_as_bool(body.get("hold")))
    if "dev" in body:
        set_dev(_as_bool(body.get("dev")))
    if "demo" in body:
        set_demo(_as_bool(body.get("demo")))
    drill = _crew_drill()
    if drill is not None:
        drill.apply_body(body)
    return status()


def _tail_rows(path: Path, n: int = 200) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _is_fail(row: dict) -> bool:
    if row.get("held") or row.get("skipped"):
        return False
    if str(row.get("result") or "").lower() == "fail":
        return True
    return row.get("pass") is False


def recent_fails(limit: int = 6) -> list[dict]:
    """Newest fail rows from the lap ledger and the demo-round log. Skips stay out."""
    found: list[tuple[float, dict]] = []

    def take(path: Path, mtime: float) -> None:
        rows = _tail_rows(path)
        for i, row in enumerate(rows):
            if not _is_fail(row):
                continue
            why = str(row.get("why") or row.get("reason") or "fail")
            found.append((mtime + i / 100000.0, {
                "beat": str(row.get("beat") or row.get("slice") or ""),
                "why": why[:160],
                "dev": bool(row.get("dev")),
            }))

    if DRILL_DIR.is_dir():
        files = sorted(DRILL_DIR.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        for path in files:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            take(path, mtime)
    if ROUNDS_LOG.is_file():
        try:
            take(ROUNDS_LOG, ROUNDS_LOG.stat().st_mtime)
        except OSError:
            pass
    found.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in found[:limit]]


def status(env: dict | None = None) -> dict:
    alive = lap_alive()
    stopped = OFF_FILE.exists()
    return {
        "ok": True,
        "mode": mode_on(),
        "demo": alive and not stopped,
        "stopping": alive and stopped,
        "note": _fresh_note(),
        "hold": HOLD_FILE.exists(),
        "honored": script_held(env),
        "dev": DEV_FILE.exists(),
        "dev_honored": dev_on(env),
        "fails": recent_fails(),
        **(_crew_drill().status() if _crew_drill() is not None else {}),
    }
