"""The render module's own env: <state>/render/env, from the hash lock in modules/render/locks (2026-10-09).

The module itself is standard library; the one third-party package is websocket-client, which the work-in-progress
watcher (lib/render_wip, copied beside this file in a pack) needs to hold Comfy's socket. Comfy is the user's own
install and is never installed from here: render.json says where it is.

    install(log)   python -m venv <state>/render/env; pip install --require-hashes -r <lock>; returns the env's python
    python()       that interpreter when the env is there, else None
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import settings

LOCKS = Path(__file__).resolve().parent.parent / "locks"
LOCK = LOCKS / "render-any-py3.txt"


def env_dir(cfg: dict | None = None) -> Path:
    return Path((cfg or settings.load())["state"]) / "env"


def python(cfg: dict | None = None) -> Path | None:
    d = env_dir(cfg)
    for cand in (d / "bin" / "python", d / "Scripts" / "python.exe"):
        if cand.is_file():
            return cand
    return None


def installed(cfg: dict | None = None, run=subprocess.run) -> bool:
    py = python(cfg)
    if not py:
        return False
    try:
        r = run([str(py), "-c", "import websocket"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def install(cfg: dict | None = None, log=print, run=subprocess.run, lock: Path = LOCK) -> Path:
    """Build the env from the lock; idempotent. Returns the env's python."""
    cfg = cfg or settings.load()
    d = env_dir(cfg)
    if not python(cfg):
        d.parent.mkdir(parents=True, exist_ok=True)
        log(f"render: making the env at {d}")
        r = run([sys.executable, "-m", "venv", str(d)], capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError("venv failed: " + (r.stderr or r.stdout).strip()[-800:])
    py = python(cfg)
    if not py:
        raise RuntimeError(f"no python in {d} after venv")
    log(f"render: installing {lock.name} (hash-pinned)")
    r = run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "--require-hashes",
             "-r", str(lock)], capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise RuntimeError("pip failed: " + (r.stderr or r.stdout).strip()[-800:])
    return py
