"""Which 27B door VLM / crossing describe may call.

Crissa is ops :8000. The crew is :8005. Occupancy parks engines; it does
not migrate pins. Render and Beast park ops, so this rail rides crew.
Read the mode file at call time — the unit lives across switches.

Override: RAIL_PRESENCE_VLM_URL / RAIL_OPS_URL still win in Normal.
In Render/Beast occupancy wins so a unit env of ops does not 404.

A PACK (2026-10-10): those two ports were the house's own engines, so a pack beside the house asked the house's
brains. A pack's engine is ports.brain(): the variables above, else the Connect endpoint when it is on this machine,
else none (the describers skip). Render / Beast ride RAIL_PRESENCE_CREW_URL when an install names a second engine
(krew's switch does), else the same one.
"""
from __future__ import annotations

from . import paths as _paths
from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import os
from pathlib import Path

MODE_FILE = Path(
    os.environ.get("HOUSE_GPU_MODE_FILE", str(_paths.GPU_MODE_FILE))
)


def gpu_mode() -> str:
    try:
        first = MODE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return "normal"
    return first if first in {"render", "normal", "beast", "off"} else "normal"


def chat() -> tuple[str, str]:
    """Return (completions_url, served_name) for the live 27B ('' = no engine here: the describers skip)."""
    if gpu_mode() in {"render", "beast"}:
        crew = os.environ.get("RAIL_PRESENCE_CREW_URL", "").strip()
        if crew:
            return crew, os.environ.get("RAIL_PRESENCE_CREW_MODEL", "crew")
    url, model = _ports.brain()
    return url, model or "ops"
