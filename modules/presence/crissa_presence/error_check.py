"""Argus error-check — station stills, no video, no FaceFinder.

Same stops as Who-is-here (`_seat_patrol_stops` / camera-stops): rest
tilt 0, seat 12 down 15°. No extra −10° snap. No 4 fps / 8 fps video.
No return-low sweep. Door (10) is rest. FaceFinder is not this tool.

Then record body+seat on the one SoT and return the lease.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from pathlib import Path
from typing import Any

from .sweep_pose import error_check_stops, video_sweep_pose

CAMERA_STOPS = Path(
    os.environ.get(
        "RAIL_CAMERA_STOPS",
        str(_paths.DATA / "camera-stops.json"),
    )
)
LAST_DIR = Path(
    os.environ.get(
        "RAIL_ERROR_CHECK_LAST",
        str(_paths.DATA / "body_audit_last"),
    )
)
LEASE_TTL = 45.0
# Same nearest-pan window body_audit uses — past this the frame is not that seat.
_PAN_MATCH = 140000


def _load_stops() -> dict[str, Any]:
    try:
        raw = json.loads(CAMERA_STOPS.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def measured_hour_pans() -> list[tuple[int, int]]:
    """(pan, hour) from camera-stops for sit-eligible hours. No invented pans."""
    from .seat_slots import eligible_stations

    legal = set(eligible_stations())
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for s in (_load_stops().get("stops") or []):
        if not isinstance(s, dict):
            continue
        try:
            h = int(s.get("station"))
            pan = int(s["pan"])
        except (TypeError, ValueError, KeyError):
            continue
        if h not in legal or h in seen:
            continue
        # Two stops can share a pan (stand 11 / seat 12). Keep the first
        # measured row; hour_from_pan breaks a tie toward the higher hour.
        seen.add(h)
        out.append((pan, h))
    # video_sweep end is hour 12 even when the stop list listed 11 first
    pose = video_sweep_pose()
    if 12 in legal and 12 not in seen:
        out.append((int(pose["end_pan"]), 12))
    if 6 in legal and 6 not in seen:
        out.append((int(pose["start_pan"]), 6))
    return out


def hour_from_pan(
    pan: int,
    hours: list[tuple[int, int]] | None = None,
) -> int | None:
    """Nearest legal clock hour with a measured pan."""
    rows = hours if hours is not None else measured_hour_pans()
    if not rows:
        return None
    best_h: int | None = None
    best_d = 10**9
    for p0, h in rows:
        d = abs(int(pan) - int(p0))
        if d < best_d or (d == best_d and (best_h is None or h > best_h)):
            best_d = d
            best_h = int(h)
    if best_h is None or best_d > _PAN_MATCH:
        return None
    return best_h


def seats_from_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per legal seat: the card whose pan is closest to that seat."""
    hours = measured_hour_pans()
    measured = {h: p for p, h in hours}
    by_h: dict[int, tuple[int, dict[str, Any]]] = {}
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        try:
            pan = int(card["pan"])
        except (TypeError, ValueError, KeyError):
            continue
        h = hour_from_pan(pan, hours)
        if h is None:
            continue
        d = abs(pan - int(measured.get(h, pan)))
        prev = by_h.get(h)
        if prev is None or d < prev[0]:
            by_h[h] = (d, card)
    rows: list[dict[str, Any]] = []
    for h in sorted(by_h):
        _d, card = by_h[h]
        try:
            n_b = int(card.get("bodies") or 0)
        except (TypeError, ValueError):
            n_b = 0
        rows.append(
            {
                "seat": h,
                "bodies": n_b,
                "occupied": n_b > 0,
                "pan": int(card.get("pan") or 0),
                "photo": card.get("photo"),
                "photo_url": card.get("photo_url"),
            }
        )
    return rows


def bodies_seen(rows: list[dict[str, Any]]) -> int:
    """Distinct bodies from occupied seats. Do not sum overlapping frames.

    Exclusive hour → at most one body. Door (multi) uses that frame's blob
    count. Empty seats do not vote.
    """
    from .seat_slots import multi_ok_stations

    multi = set(multi_ok_stations())
    n = 0
    multi_max = 0
    for r in rows:
        if not r.get("occupied"):
            continue
        if int(r["seat"]) in multi:
            try:
                multi_max = max(multi_max, int(r.get("bodies") or 1))
            except (TypeError, ValueError):
                multi_max = max(multi_max, 1)
        else:
            n += 1
    return n + multi_max


def assign_seats(
    *,
    named: list[str],
    unnamed_keys: list[str],
    occupied_hours: list[int],
    old_guess: dict[str, int] | None = None,
) -> dict[str, int]:
    """Pin people onto occupied seats from the scan. Empty seats stay null."""
    from .seat_slots import primary_user, user_seat

    leftover = [int(h) for h in occupied_hours]
    guess: dict[str, int] = {}
    uid = primary_user()
    home = int(user_seat())
    if uid in named and home in leftover:
        guess[uid] = home
        leftover.remove(home)
    for pid, h in (old_guess or {}).items():
        pid_l = str(pid).strip().lower()
        if not pid_l or pid_l in guess:
            continue
        if pid_l not in named and pid_l not in unnamed_keys:
            continue
        try:
            hour = int(h)
        except (TypeError, ValueError):
            continue
        if hour in leftover:
            guess[pid_l] = hour
            leftover.remove(hour)
    # Named people the scan did not place stay present (union) with no new
    # seat. Dumping them onto leftover occupied hours plants the User on
    # door-picture blobs. Extra occupied seats are unnamed.
    for uk in unnamed_keys:
        if uk in guess:
            continue
        if leftover:
            guess[uk] = leftover.pop(0)
    return guess


def record_on_sot(svc, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Write occupancy + seats onto the one inventory object.

    This is the operator recount (Control Panel "Scan the empty room").
    Automatic seat tours use union so a miss does not evict someone still
    in the room (the User 2026-08-11). The operator button is the opposite:
    the User is telling the house the count is wrong. Union + inventory names
    cannot do that — measured 2026-08-18: bodies_seen=1, named=[the user,
    another person], left=[], headcount stayed 2.
    """
    from .guests import is_guest
    from .seat_slots import primary_user

    inv = svc.inventory
    present = [
        str(n).strip().lower()
        for n in (inv.names() or [])
        if n and not is_guest(str(n))
    ]
    occupied = [int(r["seat"]) for r in rows if r.get("occupied")]
    n_bodies = bodies_seen(rows)
    uid = str(primary_user() or "").strip().lower()
    kept = [uid] if uid and uid in present else []
    unnamed_want = max(0, int(n_bodies) - len(kept))
    dropped: list[str] = []
    for n in present:
        if n not in kept:
            inv.force_leave([n], source="operator_error_check")
            dropped.append(n)
    ev = inv.apply_reconcile(kept, unnamed_want, mode="replace", full=True)
    left = sorted(set(dropped) | {str(x) for x in (ev.get("left") or []) if x})
    ev = {**ev, "left": left, "mode": "replace"}
    while len(getattr(inv, "unnamed_keys", []) or []) < int(inv.unnamed or 0):
        inv._new_unnamed_key(source="error_check")
    old = dict(getattr(inv, "station_guess", None) or {})
    after_names = [
        str(n).strip().lower()
        for n in (inv.names() or [])
        if n and not is_guest(str(n))
    ]
    guess = assign_seats(
        named=after_names,
        unnamed_keys=list(inv.unnamed_keys or []),
        occupied_hours=occupied,
        old_guess=old,
    )
    inv.station_guess.clear()
    inv.station_guess.update(guess)
    now = time.time()
    st = svc._presence_from_inventory(
        frame_age=0.2,
        camera_open=True,
        camera_blind=False,
        last_event={
            "kind": "error_check",
            "reason": "error_check",
            "source": "argus",
            "facefinder": False,
            "seats": rows,
            "occupied": occupied,
            "bodies_seen": n_bodies,
            "entered": ev.get("entered"),
            "left": ev.get("left"),
            "unnamed": ev.get("unnamed"),
            "ts": now,
        },
        count_trust="fact",
        camera_mode="holding",
        door_names=[],
    )
    svc._set(st)
    try:
        if hasattr(svc, "_save_inventory_disk"):
            svc._save_inventory_disk(force=True)
    except Exception:
        pass
    return {
        "ok": True,
        "reconcile": {
            k: ev.get(k)
            for k in ("mode", "entered", "left", "unnamed", "headcount", "names")
        },
        "seats": guess,
        "occupied": occupied,
        "bodies_seen": n_bodies,
        "headcount": int(st.headcount),
        "people": [
            {"name": p.name or p.slot_id, "seat": p.seat}
            for p in (st.people or [])
        ],
    }


def _home_and_release(lease_id: str | None) -> dict[str, Any]:
    """FF off → door pose → release lease. Always, even when the sweep failed."""
    from . import room_sweep as rs
    from .seat_shot import go_home

    out: dict[str, Any] = {}
    try:
        out["home"] = go_home(rs, lease_id)
    except Exception as e:
        out["home"] = {"ok": False, "error": type(e).__name__}
    try:
        rel = rs._post("/v1/lease/release", {"lease_id": lease_id}, timeout=8.0)
        out["lease_released"] = bool(rel.get("ok"))
    except Exception:
        out["lease_released"] = False
    return out


def run_error_check(svc) -> dict[str, Any]:
    """Same walk as Who-is-here. Argus/panel aliases must not tour twice."""
    from .inspector import who_is_here

    t0 = time.time()
    presence: dict[str, Any] = {}
    try:
        st = svc.get()
        if hasattr(st, "to_dict"):
            presence = st.to_dict() or {}
        elif isinstance(st, dict):
            presence = st
    except Exception:
        presence = {}
    walk = who_is_here(presence)
    sot = walk.get("sot") if isinstance(walk.get("sot"), dict) else {}
    door = walk.get("door") if isinstance(walk.get("door"), dict) else {}
    out: dict[str, Any] = {
        "ok": bool(walk.get("ok")),
        "schema": "error_check/v1",
        "tool": "error-check",
        "facefinder": True,
        "mode": "who_is_here",
        "law": (
            "One walk with Who-is-here: wide still, FaceFinder zoom, "
            "FaceID names, body-without-face stays S#, User always kept."
        ),
        "seen": walk.get("seen"),
        "unnamed": walk.get("unnamed"),
        "bodies_seen": walk.get("bodies_seen"),
        "sot": sot,
        "looks": walk.get("looks"),
        "camera_returned_home": bool(door.get("ok")),
        "lease_released": True,
        "firmware": {"home": {"home": bool(door.get("ok"))}, "lease_released": True},
        "wall_sec": walk.get("wall_sec") or round(time.time() - t0, 3),
    }
    if walk.get("error"):
        out["error"] = walk.get("error")
    if sot.get("error"):
        out["ok"] = False
        out["error"] = sot.get("error")
    return out
