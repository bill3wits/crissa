"""Seat slot allocation — clock hours as exclusive seats + multi door.

the User 2026-08-02 product:
  * Eligible sit hours come from **room_geometry/v1** (init / rescan interview) —
    never hardcode 1–12 in product UI or train games.
  * Wall + media blank hours are **not** eligible.
  * 12 (door) — multiples OK (stack in door FOV)
  * Rescan room → null all allocations; re-pin sole User as desk reference
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from pathlib import Path
from typing import Any

# Fallbacks only when room_geometry.json is missing (pre-init).
_FALLBACK_LEGAL: tuple[int, ...] = tuple(
    int(x)
    for x in os.environ.get(
        "RAIL_PRESENCE_STATIONS", "6,8,9,10,11,12"
    ).split(",")
    if x.strip().isdigit()
)
# Fallback only. Live list is room_geometry.blocked_stations.
# Final: B6 media7 M8 stand9 door10 stand11 seat12. Wall 1–5.
WALL_STATIONS: frozenset[int] = frozenset({1, 2, 3, 4, 5})
_FALLBACK_MULTI: frozenset[int] = frozenset(
    int(x)
    for x in os.environ.get("RAIL_SEAT_MULTI", "10").split(",")
    if x.strip().isdigit()
)
PRIMARY_USER: str = (
    os.environ.get("RAIL_PRIMARY_USER")
    or os.environ.get("HOUSE_USER_ID")
    or "user"
).strip().lower()
_FALLBACK_USER_SEAT: int = int(os.environ.get("RAIL_USER_SEAT", "6"))

GEOM_PATH = Path(
    os.environ.get(
        "RAIL_ROOM_GEOMETRY",
        str(_paths.DATA / "room_geometry.json"),
    )
)


def load_room_geometry() -> dict[str, Any]:
    """Init/rescan SoT for legal / blocked / media blank seats."""
    default: dict[str, Any] = {
        "schema": "room_geometry/v1",
        "blocked_stations": sorted(WALL_STATIONS),
        "legal_stations": list(_FALLBACK_LEGAL),
        "multi_ok": sorted(_FALLBACK_MULTI),
        "blanked_sit": [],
        "media_station": None,
        "user_seat": _FALLBACK_USER_SEAT,
        "source": "fallback_env",
    }
    if not GEOM_PATH.is_file():
        return default
    try:
        raw = json.loads(GEOM_PATH.read_text())
        if not isinstance(raw, dict):
            return default
        out = {**default, **raw}
        out["schema"] = "room_geometry/v1"
        return out
    except Exception:
        return default


def wall_stations() -> list[int]:
    """Hours a person cannot occupy because of a wall.

    Prefer init `wall_hours`. Do not treat media/dresser/down as wall.
    """
    g = load_room_geometry()
    raw = g.get("wall_hours")
    if raw:
        out: list[int] = []
        for x in raw:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                pass
        if out:
            return sorted(set(out))
    return sorted(WALL_STATIONS)


def blanked_sit_stations() -> list[int]:
    """Media / blank sit hours from init interview (e.g. speakers @ 9)."""
    g = load_room_geometry()
    out: set[int] = set()
    for x in g.get("blanked_sit") or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            pass
    ms = g.get("media_station")
    if ms is not None:
        try:
            out.add(int(ms))
        except (TypeError, ValueError):
            pass
    return sorted(out)


def media_station() -> int | None:
    """Clock hour the room's media sound comes from. None if init never asked.

    the User 2026-08-16: "Media is 7 oclock here and should be labeled as media
    sound source." It was already in room_geometry as media_station, and until
    now nothing consulted it — recorded and never read.

    It matters because a loudspeaker is a voice the room can hear and a body the
    room cannot see. That mismatch has already cost: the TV authenticated as the
    User on 22.59% of turns at the old 0.35 voice threshold. A bearing that
    points at this hour is a sound source, never a speaker.
    """
    g = load_room_geometry()
    ms = g.get("media_station")
    try:
        return int(ms) if ms is not None else None
    except (TypeError, ValueError):
        return None


def camera_door_station() -> int:
    """Door hour from init (`camera_door` / `multi_ok`)."""
    g = load_room_geometry()
    for raw in (g.get("camera_door"), (g.get("multi_ok") or [None])[0]):
        try:
            h = int(raw)
            if 1 <= h <= 12:
                return h
        except (TypeError, ValueError):
            pass
    return 12


def multi_ok_stations() -> list[int]:
    g = load_room_geometry()
    raw = g.get("multi_ok")
    if raw:
        out = []
        for x in raw:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                pass
        if out:
            return out
    return sorted(_FALLBACK_MULTI)


def legal_stations() -> list[int]:
    """Stations listed legal by geometry (may still include media if mis-init)."""
    g = load_room_geometry()
    raw = g.get("legal_stations") or _FALLBACK_LEGAL
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out or list(_FALLBACK_LEGAL)


def eligible_stations() -> list[int]:
    """Sit-eligible for UI + LLM seat game: legal − wall − media blank.

    This is the only list product surfaces should offer. Never hardcode 1–12.
    """
    wall = set(wall_stations())
    blank = set(blanked_sit_stations())
    multi = set(multi_ok_stations())
    out: list[int] = []
    for h in legal_stations():
        if h in wall or h in blank:
            continue
        out.append(h)
    # door multi-ok always eligible if not wall
    for h in multi:
        if h not in wall and h not in blank and h not in out:
            out.append(h)
    # stable clock order: 12,1,2,…11 then keep relative
    def _ord(h: int) -> int:
        return (h - 12) % 12

    return sorted(set(out), key=_ord)


def primary_user() -> str:
    return PRIMARY_USER


def user_seat() -> int:
    g = load_room_geometry()
    try:
        us = int(g.get("user_seat") or _FALLBACK_USER_SEAT)
    except (TypeError, ValueError):
        us = _FALLBACK_USER_SEAT
    if us in set(wall_stations()) or us in set(blanked_sit_stations()):
        # prefer first exclusive eligible sit that is not door multi
        for h in eligible_stations():
            if h not in multi_ok_stations():
                return h
        return eligible_stations()[0] if eligible_stations() else 6
    return us


def station_roles() -> dict[int, str]:
    """Init `station_roles` hour → role (user_desk, another person, door, …)."""
    g = load_room_geometry()
    raw = g.get("station_roles") if isinstance(g.get("station_roles"), dict) else {}
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            h = int(k)
        except (TypeError, ValueError):
            continue
        role = str(v or "").strip().lower()
        if 1 <= h <= 12 and role:
            out[h] = role
    return out


def seat_named(name: str) -> int | None:
    """Hour for a named regular from init. User is one of them (`user_seat`)."""
    want = str(name or "").strip().lower()
    if not want:
        return None
    if want in {primary_user(), "user", "user_desk"}:
        return user_seat()
    if want in {"door", "camera_door"}:
        return camera_door_station()
    for h, role in station_roles().items():
        if role == want:
            return h
    return None


def forbidden_sit() -> set[int]:
    return set(wall_stations()) | set(blanked_sit_stations())


def soft_seat_priors() -> dict[str, int]:
    """Scoring priors from init roles. Not values to write as occupancy."""
    out: dict[str, int] = {primary_user(): user_seat()}
    stand: int | None = None
    for h, role in station_roles().items():
        if role in {"user_desk", "media", "door", "stand", "seat"}:
            if role == "stand" and stand is None:
                stand = h
            continue
        out[role] = h
    if stand is not None:
        for pid in ("a guest", "a guest", "guest3"):
            out.setdefault(pid, stand)
    return out


def regulars_by_hour() -> dict[int, str]:
    """Hour → person_id for named regulars + User desk. From init only."""
    out: dict[int, str] = {user_seat(): primary_user()}
    for h, role in station_roles().items():
        if role in {"user_desk", "media", "door", "stand", "seat"}:
            continue
        out[h] = role
    return out


def geometry_public() -> dict[str, Any]:
    """API blob for stamp UI + seat games (no freelanced hours)."""
    g = load_room_geometry()
    elig = eligible_stations()
    return {
        "ok": True,
        "schema": "room_geometry/v1",
        "source": g.get("source") or "unknown",
        "path": str(GEOM_PATH),
        "eligible_stations": elig,
        "legal_stations": legal_stations(),
        "wall_stations": wall_stations(),
        "blanked_sit": blanked_sit_stations(),
        "media_station": g.get("media_station"),
        "media_label": g.get("media_label"),
        "multi_ok": multi_ok_stations(),
        "user_seat": user_seat(),
        "primary_user": primary_user(),
        "door": camera_door_station(),
        "station_roles": {str(h): r for h, r in sorted(station_roles().items())},
        "regulars": {str(h): p for h, p in sorted(regulars_by_hour().items())},
        "note": (
            "eligible_stations = init geometry legal − wall − media blank. "
            "User seat, door, and regulars come from the same init. "
            "Stamp UI and LLM seat-guess MUST use this list only."
        ),
    }


def empty_slots() -> dict[str, Any]:
    """Canonical empty seat table (exclusive hours → null; multi list empty)."""
    elig = eligible_stations()
    multi_ok = set(multi_ok_stations())
    wall = wall_stations()
    exclusive = {
        str(h): None
        for h in elig
        if h not in multi_ok
    }
    multi = {str(h): [] for h in multi_ok if h in elig or h not in set(wall)}
    return {
        "schema": "seat_slots/v1",
        "ts": time.time(),
        "exclusive": exclusive,  # hour → person_id | null
        "multi": multi,  # hour → [person_id, …]
        "wall": wall,
        "blanked_sit": blanked_sit_stations(),
        "legal": elig,
        "eligible": elig,
        "primary_user": PRIMARY_USER,
        "user_seat": user_seat(),
        "source": "null",
        "geometry_source": load_room_geometry().get("source"),
    }


def from_station_guess(station_guess: dict[str, int] | None) -> dict[str, Any]:
    """Build seat_slots/v1 from inventory station_guess (person → hour)."""
    slots = empty_slots()
    slots["source"] = "station_guess"
    for pid, hour in (station_guess or {}).items():
        try:
            h = int(hour)
        except (TypeError, ValueError):
            continue
        if h in set(wall_stations()) or h in set(blanked_sit_stations()):
            continue
        if h not in set(eligible_stations()):
            continue
        name = str(pid).strip().lower()
        if not name:
            continue
        if h in set(multi_ok_stations()):
            slots["multi"].setdefault(str(h), []).append(name)
        else:
            # exclusive: first writer wins unless overwriting null
            key = str(h)
            if slots["exclusive"].get(key) in (None, name):
                slots["exclusive"][key] = name
            # conflict: leave existing; caller can resolve via allocate()
    return slots


def to_station_guess(slots: dict[str, Any]) -> dict[str, int]:
    """Flatten seat_slots → person_id → hour for inventory."""
    out: dict[str, int] = {}
    for h, pid in (slots.get("exclusive") or {}).items():
        if pid:
            out[str(pid).lower()] = int(h)
    for h, pids in (slots.get("multi") or {}).items():
        for pid in pids or []:
            if pid:
                out[str(pid).lower()] = int(h)
    return out


def null_all(slots: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rescan room: clear every allocation."""
    s = empty_slots()
    s["source"] = "rescan_null"
    s["ts"] = time.time()
    if slots and slots.get("updated_ts"):
        s["prev_updated_ts"] = slots.get("updated_ts")
    return s


def pin_user(
    slots: dict[str, Any],
    *,
    user_id: str | None = None,
    seat: int | None = None,
) -> dict[str, Any]:
    """After null: re-pin primary User as full-frame desk/seat reference."""
    uid = (user_id or PRIMARY_USER).strip().lower()
    h = int(seat if seat is not None else user_seat())
    if h in set(wall_stations()) or h in set(blanked_sit_stations()):
        h = user_seat()
    s = dict(slots)
    s["exclusive"] = dict(s.get("exclusive") or {})
    s["multi"] = {k: list(v) for k, v in (s.get("multi") or {}).items()}
    # clear user from any other slot first
    for k, v in list(s["exclusive"].items()):
        if v == uid:
            s["exclusive"][k] = None
    for k, lst in list(s["multi"].items()):
        s["multi"][k] = [x for x in lst if x != uid]
    if h in set(multi_ok_stations()):
        s["multi"].setdefault(str(h), []).append(uid)
    else:
        s["exclusive"][str(h)] = uid
    s["source"] = "user_pin"
    s["ts"] = time.time()
    s["pinned_user"] = uid
    s["pinned_seat"] = h
    return s


def allocate(
    slots: dict[str, Any],
    person_id: str,
    hour: int,
    *,
    exclusive_force: bool = False,
) -> dict[str, Any]:
    """Place person at hour respecting multi/exclusive rules."""
    pid = person_id.strip().lower()
    h = int(hour)
    s = dict(slots)
    s["exclusive"] = dict(s.get("exclusive") or {})
    s["multi"] = {k: list(v) for k, v in (s.get("multi") or {}).items()}
    if h in set(wall_stations()) or h in set(blanked_sit_stations()):
        s["error"] = f"wall_or_media_illegal:{h}"
        return s
    if h not in set(eligible_stations()):
        s["error"] = f"not_eligible:{h}"
        return s
    # remove prior placement
    for k, v in list(s["exclusive"].items()):
        if v == pid:
            s["exclusive"][k] = None
    for k, lst in list(s["multi"].items()):
        s["multi"][k] = [x for x in lst if x != pid]
    if h in set(multi_ok_stations()):
        s["multi"].setdefault(str(h), [])
        if pid not in s["multi"][str(h)]:
            s["multi"][str(h)].append(pid)
    else:
        cur = s["exclusive"].get(str(h))
        if cur and cur != pid and not exclusive_force:
            s["error"] = f"seat_taken:{h}:{cur}"
            return s
        s["exclusive"][str(h)] = pid
    s["ts"] = time.time()
    s["source"] = "allocate"
    s.pop("error", None)
    return s


def resolve_exclusive(
    hour: int,
    *,
    taken: set[int],
    prefer: int | None = None,
) -> int:
    """Snap to a free exclusive seat (or multi door). Used by service."""
    stations = eligible_stations()
    multi = set(multi_ok_stations())
    sg = int(prefer if prefer is not None else hour)

    def _cd(a: int, b: int) -> int:
        d = abs(a - b) % 12
        return min(d, 12 - d)

    if not stations:
        return 12
    if sg not in stations:
        sg = min(stations, key=lambda c: _cd(c, sg))
    if sg in multi:
        return sg  # door stack always OK
    if sg not in taken:
        return sg
    order = sorted(
        (c for c in stations if c != sg),
        key=lambda c: (_cd(c, sg), _cd(c, 12)),
    )
    for c in order:
        if c in multi:
            return c
        if c not in taken:
            return c
    return multi_ok_stations()[0] if multi_ok_stations() else 12


def occupancy_summary(presence: dict[str, Any] | None) -> dict[str, Any]:
    """Classify room for auto-rescan allow list."""
    p = presence or {}
    people = [x for x in (p.get("people") or []) if isinstance(x, dict)]
    named = []
    for x in people:
        n = str(x.get("name") or "").strip().lower()
        if not n or n.startswith("u"):
            continue
        certain = bool(x.get("certain") or (x.get("id_status") == "confirmed"))
        named.append({"id": n, "certain": certain})
    hc = int(p.get("headcount") or len(named) or 0)
    uid = PRIMARY_USER
    sole = named[0]["id"] if len(named) == 1 else None
    sole_user = bool(sole == uid and (named[0].get("certain") if named else False))
    empty = hc == 0 and len(named) == 0
    allow_rescan = empty or sole_user
    return {
        "headcount": hc,
        "named": named,
        "empty": empty,
        "sole_user": sole_user,
        "sole_id": sole,
        "primary_user": uid,
        "allow_auto_rescan": allow_rescan,
        "allow_reason": (
            "empty"
            if empty
            else ("sole_user" if sole_user else "occupied_guests")
        ),
        "empty_refs_ok": empty,  # pure empty refs only when truly empty
        "user_desk_ref_ok": sole_user,
    }
