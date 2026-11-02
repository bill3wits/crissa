"""The room, described to Argus in one paragraph, straight from init.

the User 2026-08-16:

    "Maybe just give it the clock icon and seats and camera position. I believe
     argus has spatial understanding."
    "door is only point of in/egress and camera/Argus sits as center of clock.
     User sits at 6 oclock, Mi 8, Door 10."
    "Door=Sole Regress"

The last one is the load-bearing fact and it settles a whole class of wrong
answers. Argus watched a clean entry — "walks from the hallway through the
doorway into the room and then moves toward the left side of the frame and
leaves the view" — and returned `unresolved` because it could not say where he
ended up. But there is nowhere else to go: the door is the only way out, so
someone who leaves the FRAME without crossing the door plane is still in the
ROOM. Told that, the model can finish the reasoning it had already started.

Everything here is read from house init (`room_geometry.json`, `seat_slots`),
never hardcoded — the next house has different seats, and a literal 6 or 10 in
this file would be wrong there on day one.
"""

from __future__ import annotations

from . import paths as _paths


def _roles() -> dict[str, str]:
    try:
        from .room_heading import heading

        import json
        import os
        from pathlib import Path

        p = Path(
            os.environ.get(
                "ROOM_GEOMETRY",
                str(_paths.DATA / "room_geometry.json"),
            )
        )
        j = json.loads(p.read_text(encoding="utf-8"))
        _ = heading  # keep the init source explicit
        return {str(k): str(v) for k, v in (j.get("station_roles") or {}).items()}
    except Exception:
        return {}


def room_paragraph() -> str:
    """One paragraph of room geometry, or '' when init has not described it."""
    roles = _roles()
    if not roles:
        return ""
    try:
        from .room_heading import door_hour
        from .seat_slots import primary_user, user_seat

        door_h = int(door_hour())
        uid = str(primary_user() or "the User")
        user_h = int(user_seat())
    except Exception:
        return ""

    # Named positions only. "9 o'clock stand" tells Argus nothing about a
    # crossing, and every token here is paid on every call — the User trimmed 18k
    # of exactly this out of the SOUL files.
    generic = {"door", "stand", "seat"}
    seats = [
        f"{role.replace('_', ' ')} {hour}"
        for hour, role in sorted(roles.items(), key=lambda kv: int(kv[0]))
        if role not in generic
    ]
    seat_txt = ", ".join(seats)

    # Camera clock: looking at the door. User home beam is behind the lens
    # (seat 6 this house) — not in this FOV. Monitor-frame door hour is a
    # dial label, not "the camera faces hour 10".
    return (
        f"ROOM: camera = clock centre, looking at the door (only in/out; "
        f"dial door={door_h}). "
        f"{uid} home beam is hour {user_h} BEHIND this camera — that desk is "
        f"not in this FOV. After enter they walk straight to {user_h}. "
        f"{seat_txt}. "
        "Face toward the lens then empty doorway = they sat behind the camera "
        "(enter finished), not a leave. "
        "Leave is only a BACK through the door into the hallway. "
        "Leaving the frame without crossing the door plane means still in the room. "
    )
