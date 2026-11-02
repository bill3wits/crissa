"""Guest vs occupancy — docs/GUEST-VISITOR-TODAY-20260813.md

Guests are not occupancy. A known guest face means: they were here, and they
may be speaking now. The door count game stays nameless add/sub/null.
"""

from __future__ import annotations

import os
from datetime import datetime

# Law file roster (the User 2026-08-13). Env RAIL_GUEST_IDS overrides.
DEFAULT_GUEST_IDS = frozenset({"a guest", "a guest", "guest3"})
# Door-plane occupancy when it matters (user + regular).
DEFAULT_OCCUPANCY_IDS = frozenset({"user", "another person"})


def guest_ids() -> frozenset[str]:
    raw = (os.environ.get("RAIL_GUEST_IDS") or "").strip()
    if raw:
        return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())
    return DEFAULT_GUEST_IDS


def is_guest(name: str | None) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return n in guest_ids()


def is_occupancy_person(name: str | None) -> bool:
    """User / regular — may sit on inventory when the door plane matters."""
    n = (name or "").strip().lower()
    if not n or is_guest(n):
        return False
    extra = (os.environ.get("RAIL_OCCUPANCY_IDS") or "").strip()
    if extra:
        return n in {x.strip().lower() for x in extra.split(",") if x.strip()}
    return n in DEFAULT_OCCUPANCY_IDS or not is_guest(n)


def visitor_day(now: float | None = None) -> str:
    """House-local calendar day for visitor_today."""
    if now is None:
        return datetime.now().strftime("%Y-%m-%d")
    return datetime.fromtimestamp(float(now)).strftime("%Y-%m-%d")
