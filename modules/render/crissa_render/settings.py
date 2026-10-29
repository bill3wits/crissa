"""Where the user's ComfyUI is, and where this module keeps its own files (2026-10-09).

One file, <state home>/render.json, written once by the person (or `crissa-render.py --configure`):

    {
      "comfy_dir":    "/path/to/ComfyUI",          # the folder holding main.py
      "comfy_python": "/path/to/ComfyUI/venv/bin/python",   # the interpreter that runs it ("" = the running one)
      "comfy_url":    "<the port table's comfy>",  # where it listens (loopback; CRISSA_PORT_COMFY / _OFFSET move it)
      "workflows":    "<comfy_dir>/workflows",     # saved API graphs the recipes name
      "models":       "<comfy_dir>/models",
      "input":        "<comfy_dir>/input",
      "output":       "<comfy_dir>/output",
      "recipes":      "<state>/render/recipes.json",
      "orders":       "<state>/render/orders",
      "highvram":     false
    }

Every path is the user's, read from this file; the code names none. A key left out takes the default shown.
The GPU mode flag, the Comfy pid file and the work-in-progress files live under <state>/render/.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

try:
    from . import crissa_home as H              # the copy a pack puts beside this file
except ImportError:                             # the source tree: lib/ on sys.path
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    import crissa_home as H                     # noqa: E402
try:
    from . import crissa_ports as P             # a copy beside this file, when a pack puts one there
except ImportError:                             # the player's lib/ (this module ships with it, and the source tree)
    import sys as _sys
    _lib = str(Path(__file__).resolve().parents[3] / "lib")
    if _lib not in _sys.path:
        _sys.path.insert(0, _lib)
    import crissa_ports as P                    # noqa: E402

HOME = Path(os.environ.get("CRISSA_HOME") or H.HOME)
STATE = HOME / "render"
FILE = Path(os.environ.get("CRISSA_RENDER_CONFIG") or (HOME / "render.json"))
MODES = ("render", "normal", "off")


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def load(path: Path | None = None, env=None) -> dict:
    """The merged settings with every path absolute; `comfy_dir` "" means no Comfy is configured."""
    e = os.environ if env is None else env
    raw = _read(path or FILE)
    comfy_dir = str(e.get("CRISSA_COMFY_DIR") or raw.get("comfy_dir") or "").strip()
    cd = Path(comfy_dir).expanduser() if comfy_dir else None

    def under(key: str, sub: str) -> str:
        got = str(raw.get(key) or "").strip()
        if got:
            return str(Path(got).expanduser())
        return str(cd / sub) if cd else ""

    cfg = {
        "comfy_dir": str(cd) if cd else "",
        "comfy_python": str(e.get("CRISSA_COMFY_PYTHON") or raw.get("comfy_python") or "").strip(),
        # this install's port for it (lib/crissa_ports "comfy", 2026-10-10): beside another install a ComfyUI on
        # ComfyUI's own 8188 is that install's, and "already up" there handed it this install's renders
        "comfy_url": str(e.get("CRISSA_COMFY_URL") or raw.get("comfy_url") or P.url("comfy", e)).rstrip("/"),
        "workflows": under("workflows", "workflows"),
        "models": under("models", "models"),
        "input": under("input", "input"),
        "output": under("output", "output"),
        "recipes": str(Path(str(raw.get("recipes") or (STATE / "recipes.json"))).expanduser()),
        "orders": str(Path(str(raw.get("orders") or (STATE / "orders"))).expanduser()),
        "highvram": bool(raw.get("highvram", False)),
        "state": str(STATE),
        "file": str(path or FILE),
    }
    return cfg


def write(values: dict, path: Path | None = None) -> Path:
    p = path or FILE
    cur = _read(p)
    cur.update({k: v for k, v in values.items() if v is not None})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cur, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def has_gpu(which=shutil.which, run=subprocess.run) -> bool:
    """An NVIDIA card nvidia-smi can see. Anything else is 'no GPU' for this module."""
    if not which("nvidia-smi"):
        return False
    try:
        r = run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "GPU" in (r.stdout or "")


def mark(cfg: dict | None = None, *, gpu: bool | None = None, up: bool | None = None) -> dict:
    """How the module stands, as the Wall's marks (lib/crissa_status): ok / fallback / off + a few plain words."""
    cfg = cfg or load()
    g = has_gpu() if gpu is None else gpu
    if not g:
        return {"state": "off", "mark": "○", "note": "no GPU"}
    if not cfg.get("comfy_dir"):
        return {"state": "off", "mark": "○", "note": "no Comfy configured (render.json)"}
    if up is None:
        from . import comfy
        up = comfy.comfy_up(cfg)
    if not up:
        return {"state": "fallback", "mark": "◐", "note": "Comfy not running"}
    return {"state": "ok", "mark": "●", "note": "Comfy up"}
