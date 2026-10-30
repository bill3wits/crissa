"""Init v2 in the id module (the User 2026-10-04) - retired in a pack on 2026-10-10.

A pack has ONE first-boot walk, the player's lib/crissa_init.py: Connect, the machine card, Voice ID (the Register
coach's routine), face photos, done - landing on her listening loop (the User 2026-10-10, after the .101 demo). Init
v2's other steps - the opener film, the portrait and its walk-off, the room shot, the overlay, the walk-in, the
camera's return - are no longer a pack's: the films of the character were left behind with them.

This module stays so an old import, or HOUSE_INIT_V2=1, lands on that same walk: every entry hands over to it through
init_wizard._walk(). The house keeps its own Init v2 (the house's lib/init_v2.py, its home server); nothing here is
the house's.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from . import init_wizard as w

SWITCH = "HOUSE_INIT_V2"
SCHEMA = "house_init_wizard/v2"


def enabled() -> bool:
    return os.environ.get(SWITCH, "").strip().lower() in ("1", "on", "true", "yes")


def _walk():
    mod = w._walk()
    if mod is None:
        raise RuntimeError("the pack's walk (lib/crissa_init.py) is not beside this module")
    return mod


def apply(action: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return _walk().apply(action, body or {})


def snapshot(st: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return _walk().snapshot(st)


def needed(st: Optional[dict[str, Any]] = None) -> bool:
    return bool(_walk().needed(st))


def attend() -> dict[str, Any]:
    return _walk().apply("attend")


def steps() -> list[dict[str, Any]]:
    """The walk's rows; the module alone (no player beside it) names the same four from its own sequence file."""
    mod = w._walk()
    return mod.steps() if mod is not None else w.steps()
