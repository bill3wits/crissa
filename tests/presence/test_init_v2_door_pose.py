"""ONE door pose (the User 2026-10-04, Init v2).

seat_shot.door_pose() read camera-stops.json door{} with `or` fallbacks, so a
configured tilt of 0 became +5 deg (18000) and go_home aimed there, while the
door watch rested at room_heading.door_home_pose(). Now door_pose() IS the door
watch's pose; RAIL_SEAT_SHOT_OWN_DOOR=1 restores the old reader.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

import pytest  # noqa: E402

from crissa_presence import room_heading, seat_shot  # noqa: E402


@pytest.fixture
def files(tmp_path, monkeypatch):
    geom = tmp_path / "room_geometry.json"
    geom.write_text(json.dumps({"heading": {
        "door_home_pan": 432000, "door_home_tilt": 0, "door_home_zoom": 100}}))
    stops = tmp_path / "camera-stops.json"
    stops.write_text(json.dumps({"door": {"pan": 432000, "tilt": 0, "zoom": 100}}))
    monkeypatch.setattr(room_heading, "_GEOM", geom)
    monkeypatch.setattr(seat_shot, "STOPS", stops)
    monkeypatch.delenv("RAIL_SEAT_SHOT_OWN_DOOR", raising=False)
    return geom, stops


def test_a_configured_tilt_of_zero_stays_zero(files):
    assert seat_shot.door_pose() == {"pan": 432000, "tilt": 0, "zoom": 100}


def test_door_pose_is_the_door_watchs_own_pose(files):
    geom, _ = files
    geom.write_text(json.dumps({"heading": {
        "door_home_pan": 400000, "door_home_tilt": -7200, "door_home_zoom": 120}}))
    assert seat_shot.door_pose() == room_heading.door_home_pose() == {
        "pan": 400000, "tilt": -7200, "zoom": 120}


def test_go_home_aims_at_the_door_watchs_pose(files):
    posted: list = []

    class RS:
        @staticmethod
        def _post(path, body, timeout=8.0):
            posted.append((path, dict(body)))
            return {"ok": True, "pan_actual": body.get("pan")}

    out = seat_shot.go_home(RS, "L1")
    aim = [b for p, b in posted if p == "/v1/aim"][0]
    assert (aim["pan"], aim["tilt"], aim["zoom"]) == (432000, 0, 100)
    assert out["home"] is True


def test_kill_switch_restores_the_old_reader(files, monkeypatch):
    monkeypatch.setenv("RAIL_SEAT_SHOT_OWN_DOOR", "1")
    # the old reader's `or` fallbacks: tilt 0 -> 18000 (that is the bug it brings back)
    assert seat_shot.door_pose() == {"pan": 432000, "tilt": 18000, "zoom": 100}
    _, stops = files
    stops.write_text("{}")
    assert seat_shot.door_pose() == {"pan": 450000, "tilt": 18000, "zoom": 150}
