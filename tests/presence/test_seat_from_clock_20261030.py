"""The User's hour comes from the Oracle clock when no stop was measured (the User 2026-10-30: "it knows User is always
at 6 ... that is embedded in our Oracle clock during Init"). A pack has no camera-stops.json, so every session look
answered no_station and the camera never stayed on him while he talked."""
from __future__ import annotations

import json

from crissa_presence import seat_shot


def test_user_hour_aims_the_seat_without_a_measured_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(seat_shot, "STOPS", tmp_path / "camera-stops.json")      # a pack: no stops file
    monkeypatch.setattr("crissa_presence.seat_slots.user_seat", lambda: 6)
    p = seat_shot.pose_for_hour(6)
    assert p == {"id": "desk", "pan": 0, "tilt": 0, "station": 6, "home": False}
    assert seat_shot.pose_for_hour(9) is None                                     # other hours stay measured-only
    monkeypatch.setenv("RAIL_SEAT_FROM_CLOCK", "0")
    assert seat_shot.pose_for_hour(6) is None


def test_a_measured_stop_still_wins(tmp_path, monkeypatch):
    f = tmp_path / "camera-stops.json"
    f.write_text(json.dumps({"stops": [{"id": "desk", "station": 6, "pan": 7200, "tilt": -3600}]}))
    monkeypatch.setattr(seat_shot, "STOPS", f)
    monkeypatch.setattr("crissa_presence.seat_slots.user_seat", lambda: 6)
    assert seat_shot.pose_for_hour(6) == {"id": "desk", "pan": 7200, "tilt": -3600, "station": 6, "home": False}
