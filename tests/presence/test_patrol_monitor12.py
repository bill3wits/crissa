"""Patrol is the four named stations, clock order, no 20-stop wander."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.error_peek import _seat_patrol_stops
from crissa_presence.look_around import _stops_for_range, load_geometry
from crissa_presence.seat_slots import (
    camera_door_station,
    eligible_stations,
    forbidden_sit,
    user_seat,
)


def test_eligible_are_only_live_stations():
    elig = set(eligible_stations())
    assert elig.isdisjoint(forbidden_sit())
    assert user_seat() in elig
    assert camera_door_station() in elig


def test_seat_patrol_is_short_and_ordered():
    ids = [n for n, _, _ in _seat_patrol_stops()]
    assert ids == ["desk", "wanda", "stand_11", "seat_12"]
    assert "chair" not in ids
    assert "seats_1_2" not in ids
    from crissa_presence.seat_slots import load_room_geometry

    from crissa_presence.seat_slots import load_room_geometry, seat_named

    g = load_room_geometry()
    wall = set(g.get("wall_hours") or [])
    desk = user_seat()
    mich = seat_named("wanda")
    assert desk not in wall
    if mich is not None:
        assert mich not in wall
    assert g.get("user_seat") == desk
    media = g.get("media_station")
    if media is not None:
        assert int(media) in set(g.get("blanked_sit") or []) | set(
            g.get("blocked_stations") or []
        )


def test_look_around_ends_door_and_stays_tiny():
    stops = _stops_for_range("limited", load_geometry())
    ids = [n for n, _, _ in stops]
    assert ids[0] == "desk"
    assert ids[-1] == "door_home"
    assert len(ids) <= 5
    assert "seats_1_2" not in ids
