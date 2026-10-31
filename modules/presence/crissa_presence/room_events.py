"""room_event/v1 — door physics + seat-beam soft slots for ops/Inspector.

G1 surface (the User 2026-08-02). CODE emits; ops 35B may later label
add|sub|null + seat beams. Does not invent headcount.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

# Seat eligibility from room_geometry (init) via seat_slots — not freelanced 1–12.
from . import seat_slots as _seats

def _soft_seat_prior() -> dict[str, int]:
    """Init regulars + User desk. Not values to write as occupancy."""
    return _seats.soft_seat_priors()


def _seat_clock_brief() -> dict[str, Any]:
    elig = _seats.eligible_stations()
    wall = _seats.wall_stations()
    blank = _seats.blanked_sit_stations()
    from .room_heading import glass_clock

    glass = glass_clock()
    return {
        "center": "camera",
        "twelve": "door_center",
        # Monitor-12 (mic USB). VO dial DOOR mark. Not stations.door_beam.
        "door_hour": glass["door_hour"],
        "door_az_deg": glass["door_az_deg"],
        "monitor_frame": glass,
        # the User 2026-08-07: FOV is full 12 + edges of 11 and 1 (not whole 10→2)
        "fov_span_hours": [11, 12, 1],
        "fov_note": (
            "Door rest FOV = all of 12 plus some of 11 and 1; often only the "
            "edge of a person is visible at 11/1. Seats 8/10 and station 2 are off-screen."
        ),
        "legal_sit": elig,
        "eligible_stations": elig,
        "wall": wall,
        "blanked_sit": blank,
        "media_blank": blank,
        "multi_ok": _seats.multi_ok_stations(),
        "ask": (
            "On +1, optional station from FOV edges — not habit. "
            "FOV = full 12 + edges of 11 and 1. After enter (true-handed): "
            "edge left→11/10; edge right→1; fully off-screen right into room→2; "
            "else soft 8 or 10; still in door→12. Count is only add/sub/null on plane. "
            "Omit station_guess if unsure. Only eligible_stations (init). Never wall/media blank."
        ),
        "motion_hints": {
            "edge_image_left_after_hflip": "11 then 10 (or 8 deep left/behind)",
            "edge_image_right_after_hflip": "1",
            "fully_offscreen_right_into_room": 2,
            "no_edge_soft_sits": [8, 10],
            "linger_in_door": 12,
            "exit_image_left_after_hflip": "toward 10–11 then 8 deep",
            "exit_image_right_after_hflip": "toward 1–3",
        },
    }


# Hold events spam the door; only record meaningful transitions + rate-limited holds
_HOLD_MIN_INTERVAL_SEC = float(os.environ.get("RAIL_ROOM_EVENT_HOLD_SEC", "30"))
_MAX = int(os.environ.get("RAIL_ROOM_EVENT_MAX", "200"))

_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=_MAX)
_last_hold_ts = 0.0
_last_sig: str | None = None


def legal_stations() -> list[int]:
    """Eligible sit hours from init geometry (excludes wall + media blank)."""
    return _seats.eligible_stations()


def soft_priors() -> dict[str, int]:
    return _soft_seat_prior()


def _sig(ev: dict[str, Any]) -> str:
    return "|".join(
        [
            str(ev.get("kind")),
            str(ev.get("source")),
            ",".join(str(x) for x in (ev.get("door_names") or [])),
            str(ev.get("frame_seq") or ev.get("buffer_seq") or ""),
            str(sorted((ev.get("seat_beams") or {}).items())),
        ]
    )


def _enter_still_urls_for(
    person_id: str,
    enter_still: dict[str, Any] | None,
) -> list[str]:
    """HTTP paths for an occupant's entry stills (same as presence slots)."""
    if not enter_still or not person_id:
        return []
    pid = str(person_id).strip().lower()
    meta = enter_still.get(pid) or enter_still.get(f"__{pid}") or {}
    if not isinstance(meta, dict):
        return []
    n = int(meta.get("count") or len(meta.get("paths") or []) or 0)
    if n <= 0:
        return []
    return [f"/v1/enter_still/{pid}/{i}.jpg" for i in range(n)]


def _occupants_block(
    people: list[dict[str, Any]] | None,
    *,
    enter_still: dict[str, Any] | None,
    station_guess: dict[str, int] | None,
) -> list[dict[str, Any]]:
    """Closed roster for leave match: who is in room + their entry stills + seat.

    Fable P1 (2026-08-03): turns "who left?" into comparison against each
    occupant's own entry stills — no new capture, no open-set gallery.
    """
    out: list[dict[str, Any]] = []
    beams = station_guess or {}
    seen: set[str] = set()
    for p in people or []:
        if not isinstance(p, dict):
            continue
        nm = str(p.get("name") or p.get("person_id") or "").strip().lower()
        if not nm or nm in seen:
            continue
        # crowd tokens are aggregates, not bodies. unknown-* STAY: an opaque id
        # asserts no name, and the closed leave-roster is worth most exactly
        # when the room is guests-only (DESIGN-END-STATE-IDENTITY).
        if nm.startswith("crowd"):
            continue
        seen.add(nm)
        seat = beams.get(nm)
        if seat is None:
            try:
                s = p.get("seat") or (p.get("sources") or {}).get("station_guess")
                seat = int(s) if s is not None else None
            except (TypeError, ValueError):
                seat = None
        urls = _enter_still_urls_for(nm, enter_still)
        # also try presence-published enter_still on the person slot
        if not urls and isinstance(p.get("enter_still"), dict):
            urls = list(p["enter_still"].get("urls") or [])
        out.append(
            {
                "person_id": nm,
                "display": p.get("display") or nm,
                "seat": seat,
                "station": seat,
                "enter_still_urls": urls,
                "enter_still_count": len(urls),
                "certain": bool(p.get("certain")),
                "auth_level": (p.get("auth") or {}).get("level")
                if isinstance(p.get("auth"), dict)
                else None,
            }
        )
    # inventory-only names (station_guess keys not yet in people list)
    for nm, seat in beams.items():
        n = str(nm).lower()
        if n in seen:
            continue
        seen.add(n)
        urls = _enter_still_urls_for(n, enter_still)
        out.append(
            {
                "person_id": n,
                "display": n,
                "seat": int(seat) if seat is not None else None,
                "station": int(seat) if seat is not None else None,
                "enter_still_urls": urls,
                "enter_still_count": len(urls),
                "certain": False,
                "auth_level": None,
            }
        )
    return out


def build_room_event(
    last_event: dict[str, Any] | None,
    *,
    station_guess: dict[str, int] | None = None,
    headcount: int | None = None,
    people: list[dict[str, Any]] | None = None,
    count_trust: str | None = None,
    enter_still: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize presence last_event (+ inventory seats) → room_event/v1."""
    le = dict(last_event or {})
    if not le and not station_guess:
        return None
    kind = le.get("kind") or "unknown"
    beams: dict[str, int] = {}
    if station_guess:
        for k, v in station_guess.items():
            try:
                beams[str(k).lower()] = int(v)
            except (TypeError, ValueError):
                continue
    # fill soft priors only as annotations when no live guess
    priors_used: dict[str, int] = {}
    names = [str(p.get("name") or "").lower() for p in (people or []) if p.get("name")]
    prior = _soft_seat_prior()
    for n in names:
        if n in beams:
            continue
        if n in prior:
            priors_used[n] = prior[n]

    # VL-style action hint from CODE event kind (ops may re-judge)
    action = "null"
    k = str(kind).lower()
    if k in ("enter", "door_enter", "add", "+1") or "enter" in k:
        action = "add"
    elif k in ("leave", "door_leave", "sub", "-1", "exit", "force_leave") or "leave" in k:
        action = "sub"
    elif k in ("door_hold", "hold", "away", "reconcile", "tool_hold"):
        action = "null"

    multi_ok = sorted({int(v) for v in beams.values() if int(v) not in set(_seats.wall_stations())})

    occupants = _occupants_block(
        people, enter_still=enter_still, station_guess=beams
    )
    # person_id → enter_still_urls (flat map for quick leave compare)
    enter_stills = {
        o["person_id"]: o["enter_still_urls"]
        for o in occupants
        if o.get("enter_still_urls")
    }
    # person_id → station at event end (cheap CODE half of end-state reframe)
    body_positions = {
        o["person_id"]: o["seat"]
        for o in occupants
        if o.get("seat") is not None
    }

    return {
        "schema": "room_event/v1",
        "ts": float(le.get("ts") or le.get("capture_ts") or time.time()),
        "ts_local": le.get("capture_ts_local") or le.get("ts_local"),
        "kind": kind,
        "source": le.get("source") or "code",
        # CODE physics hint — ops 35B may override via tools later
        "action": action,  # add | sub | null
        "action_source": "code_kind_map",
        "headcount": headcount,
        "count_trust": count_trust,
        "door_names": list(le.get("door_names") or []),
        "best_sim": le.get("best_sim") or {},
        "genders": le.get("genders") or {},
        "door_unnamed": le.get("door_unnamed"),
        "max_bodies_same_frame": le.get("max_bodies_same_frame"),
        "count_rule": le.get("count_rule"),
        "id_rule": le.get("id_rule"),
        "frame_seq": le.get("frame_seq") or le.get("buffer_seq"),
        # Seat / beam slots (clock hours). Multiples OK in door FOV.
        "seat_beams": beams,  # person_id → station hour (live guesses only)
        "seat_priors": priors_used,  # habit expectations — NOT auto-written
        "seat_multi_fov": multi_ok,  # stations currently claimed (unique)
        "legal_stations": legal_stations(),
        "eligible_stations": legal_stations(),
        "wall_stations": _seats.wall_stations(),
        "blanked_sit": _seats.blanked_sit_stations(),
        # Ops: guess station from motion/exit; camera=center, 12=door
        "seat_clock": _seat_clock_brief(),
        # Fable P1: closed-roster leave compare (entry stills already on disk)
        "occupants": occupants,
        "enter_stills": enter_stills,
        # Fable P2: body position at event end (station hour if CODE knows)
        "body_positions": body_positions,
        "detail_tail": (le.get("detail") or [])[-5:]
        if isinstance(le.get("detail"), list)
        else None,
    }


def maybe_record(event: dict[str, Any] | None) -> bool:
    """Push event if new / not spammy hold. Returns True if recorded."""
    global _last_hold_ts, _last_sig
    if not event:
        return False
    kind = str(event.get("kind") or "")
    now = time.time()
    sig = _sig(event)
    with _lock:
        if kind in ("door_hold", "hold") or event.get("action") == "null" and "hold" in kind:
            if now - _last_hold_ts < _HOLD_MIN_INTERVAL_SEC and sig == _last_sig:
                return False
            _last_hold_ts = now
        if sig == _last_sig and kind not in ("enter", "leave", "door_enter", "door_leave"):
            # skip identical spam
            if kind in ("door_hold", "hold"):
                return False
        _last_sig = sig
        event = dict(event)
        event["recorded_ts"] = now
        _events.appendleft(event)
        return True


def record_from_presence(
    last_event: dict[str, Any] | None,
    *,
    station_guess: dict[str, int] | None = None,
    headcount: int | None = None,
    people: list[dict[str, Any]] | None = None,
    count_trust: str | None = None,
    enter_still: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    ev = build_room_event(
        last_event,
        station_guess=station_guess,
        headcount=headcount,
        people=people,
        count_trust=count_trust,
        enter_still=enter_still,
    )
    if ev and maybe_record(ev):
        return ev
    return ev  # still return built for feed even if not newly recorded


def last(n: int = 1) -> list[dict[str, Any]]:
    with _lock:
        items = list(_events)
    return items[: max(1, min(n, _MAX))]


def tail(n: int = 20) -> list[dict[str, Any]]:
    with _lock:
        items = list(_events)
    return items[: max(1, min(n, _MAX))]


def snapshot() -> dict[str, Any]:
    with _lock:
        items = list(_events)
    return {
        "schema": "room_events_feed/v1",
        "ok": True,
        "n": len(items),
        "last": items[0] if items else None,
        "events": items[:20],
        "legal_stations": legal_stations(),
        "eligible_stations": legal_stations(),
        "wall_stations": _seats.wall_stations(),
        "blanked_sit": _seats.blanked_sit_stations(),
        "soft_seat_prior": _soft_seat_prior(),
        "seat_clock": _seat_clock_brief(),
        "note": (
            "CODE physics + seat beams + enter_stills on each event. "
            "Leave match = closed-roster compare to occupants[].enter_still_urls "
            "(not open-set gallery). body_positions = station at event end when known. "
            "ops 35B: on +1 guess station from door FOV motion/exit edge — camera is "
            "clock center, 12 is door center. soft_seat_prior is scoring only. "
            "Never seat wall or media blank (init geometry). docs/SEAT-GUESS-FROM-MOTION.md · "
            "docs/FABLE-P0-DOOR-EVENTS-20260803.md · MANIFEST-ULTRA-20260803.md"
        ),
    }
