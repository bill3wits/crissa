"""The door feed (2026-10-01): someone appears in the part of the picture that shows the door.

The door is a box in the room camera's frame, as fractions (x0, y0, x1, y1), set once by the
user (CRISSA_ORACLE_DOOR="0.70,0.10,1.00,0.90"; unset = no door feed). An event is a face seen
inside that box after `quiet_s` without one. SKELETON: faces only; a person walking in with
their back to the camera is not seen yet.
"""
from __future__ import annotations

import os
import time


def box(env: dict | None = None) -> "tuple[float, float, float, float] | None":
    e = os.environ if env is None else env
    try:
        v = tuple(float(x) for x in str(e.get("CRISSA_ORACLE_DOOR") or "").split(","))
    except ValueError:
        return None
    if len(v) != 4 or not (0 <= v[0] < v[2] <= 1 and 0 <= v[1] < v[3] <= 1):
        return None
    return v  # type: ignore[return-value]


class DoorFeed:
    def __init__(self, door_box=None, quiet_s: float = 8.0, keep: int = 20):
        self.box, self.quiet_s, self.keep = door_box, quiet_s, keep
        self.events: list[dict] = []
        self._last_seen = float("-inf")

    def feed(self, face_centres: "list[tuple[float, float]]", now: float | None = None) -> "dict | None":
        """face_centres: each face's centre as fractions of the frame. Returns a new event or None."""
        if self.box is None:
            return None
        now = time.time() if now is None else now
        x0, y0, x1, y1 = self.box
        inside = [c for c in face_centres if x0 <= c[0] <= x1 and y0 <= c[1] <= y1]
        if not inside:
            return None
        fresh = now - self._last_seen > self.quiet_s
        self._last_seen = now
        if not fresh:
            return None
        ev = {"kind": "door", "at": now, "faces": len(inside)}
        self.events = (self.events + [ev])[-self.keep:]
        return ev
