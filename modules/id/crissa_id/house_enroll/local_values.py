"""This household's fallback values, read from config - never written in code.

The User's rule (2026-10-02): no household name stays in any repo. The few
places that fell back to a named person (the coach's people list, the room
frame's stop label, the re-enrol inbox, the voice migration) ask here instead.

- Switch OFF (default, ``HOUSE_ID_GENERIC_FALLBACKS`` unset or 0): the values
  come from ``house-local.json`` (gitignored; ``house-local.example.json`` shows
  the keys), so a machine that has the file behaves exactly as before.
- Switch ON (``HOUSE_ID_GENERIC_FALLBACKS=1``): the generic value the caller
  passes, which names nobody.

A missing or unreadable file also gives the generic value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import _LEGACY_ROOT, house_id_root

SWITCH = "HOUSE_ID_GENERIC_FALLBACKS"
FILE_NAME = "house-local.json"


def generic_on() -> bool:
    return os.environ.get(SWITCH, "").strip().lower() in ("1", "on", "true", "yes")


def local_path() -> Path | None:
    """HOUSE_ID_LOCAL, else the identity root's copy, else this tree's."""
    env = os.environ.get("HOUSE_ID_LOCAL", "").strip()
    if env:
        return Path(env).expanduser()
    for root in (house_id_root(), _LEGACY_ROOT):
        p = Path(root) / FILE_NAME
        if p.is_file():
            return p
    return None


def _load() -> dict[str, Any]:
    p = local_path()
    if p is None:
        return {}
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def local(key: str, generic: Any) -> Any:
    """The house's value for ``key``, or ``generic`` (switch on / no value)."""
    if generic_on():
        return generic
    v = _load().get(key)
    return generic if v is None else v
