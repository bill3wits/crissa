"""Monitor-12 heading + door-home pose from room_geometry."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.room_heading import (
    door_az_deg,
    door_home_pose,
    door_hour,
    glass_clock,
    pose_hflip,
    pose_hflip_from_az,
)
from crissa_presence.seat_slots import camera_door_station, load_room_geometry


def test_this_office_door_is_hour_10_monitor_frame():
    g = load_room_geometry()
    h = g.get("heading") if isinstance(g.get("heading"), dict) else {}
    expect = int(h.get("door_hour") or camera_door_station())
    assert door_hour() == expect
    if h.get("door_az_deg") is not None:
        assert door_az_deg() == float(h["door_az_deg"])
    pose = door_home_pose()
    if h.get("door_home_pan") is not None:
        assert pose["pan"] == int(h["door_home_pan"])
    if h.get("door_home_tilt") is not None:
        assert pose["tilt"] == int(h["door_home_tilt"])
    if h.get("door_home_zoom") is not None:
        assert pose["zoom"] == int(h["door_home_zoom"])


def test_glass_clock_for_vo_dial():
    g = glass_clock()
    init = load_room_geometry()
    h = init.get("heading") if isinstance(init.get("heading"), dict) else {}
    assert g["twelve"] == (h.get("twelve") or "monitor")
    assert g["door_hour"] == int(h.get("door_hour") or camera_door_station())
    if h.get("door_home_pan") is not None:
        assert g["door_home_pan"] == int(h["door_home_pan"])


def test_pose_hflip_az_sign_about_6_12():
    assert pose_hflip_from_az(300.0) is False  # door 10, user-left
    assert pose_hflip_from_az(90.0) is True  # hour 3, user-right
    assert pose_hflip_from_az(0.0) is False  # 12 on-axis
    assert pose_hflip_from_az(180.0) is False  # 6 on-axis
    assert pose_hflip_from_az(None, 10) is False
    assert pose_hflip_from_az(None, 3) is True
    assert pose_hflip_from_az(None, None) is False
    src = Path(__file__).resolve().parents[2] / "modules" / "presence" / "crissa_presence" / "room_heading.py"
    text = src.read_text(encoding="utf-8")
    fn = text.split("def pose_hflip_from_az")[1].split("def pose_hflip(")[0]
    body = fn.split('"""', 2)[-1]
    assert "door_beam" not in body
    assert "camera_door_beam" not in body
    live = pose_hflip()
    g = glass_clock()
    assert g["pose_hflip"] is live
    assert live is pose_hflip_from_az(g["door_az_deg"], g["door_hour"])


def test_ptz_imports_heading_pose():
    from crissa_presence.ptz import DOOR_HOME_PAN, DOOR_HOME_TILT

    # Assert the SOURCE, not the numbers. These are per-house init values
    # (room_geometry.json heading) and a literal here is the same portability
    # bug the module exists to prevent — this test failed the moment the tilt
    # was measured properly at +5 deg on 2026-08-16.
    from crissa_presence.room_heading import door_home_pose

    pose = door_home_pose()
    assert DOOR_HOME_PAN == pose["pan"]
    assert DOOR_HOME_TILT == pose["tilt"]
