"""Live VIDEO and empty-ref VIDEO must share one tilt."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.sweep_pose import tilt_units, video_sweep_pose  # noqa: E402


def test_tilt_units_five_down() -> None:
    assert tilt_units(-5) == -18000
    assert tilt_units(-15) == -54000


def test_default_pose_is_minus_five() -> None:
    p = video_sweep_pose()
    assert p["tilt_deg"] == -5
    assert p["tilt"] == -18000
    assert p["start_pan"] == 0
    assert p["end_pan"] == 515000
    # 1x. user 2026-08-16: "fix the tool to scan at 1x zoom on all stations
    # except for 10." The empty-ref video and the live-count video are the same
    # path, so this moved together and the diff stays valid. Station 10 keeps
    # its own resting 2x and is excluded from the sweep entirely.
    assert p["zoom"] == 100
    ids = [s["id"] for s in p["sitters"]]
    assert "seat_12" in ids and "chair" in ids
    by = {s["id"]: s for s in p["sitters"]}
    # -20, not -15: user 2026-08-21 took the door down 5d to level and
    # applied the same -5d to seat 12. camera-stops.json has carried
    # -72000 since; these assertions were never updated and had been
    # failing on the real file.
    assert by["seat_12"]["tilt_deg"] == -20
    assert by["chair"]["tilt_deg"] == -20
    assert by["chair"]["pan"] == 300000


def test_cfg_override(tmp_path: Path) -> None:
    p = video_sweep_pose(
        {
            "video_sweep": {
                "tilt_deg": -8,
                "tilt": -28800,
                "start_pan": 1000,
                "end_pan": 500000,
                "zoom": 120,
            }
        }
    )
    assert p["tilt_deg"] == -8
    assert p["tilt"] == -28800
    assert p["start_pan"] == 1000
    assert p["zoom"] == 120


def test_tilt_deg_fills_units_when_tilt_omitted() -> None:
    p = video_sweep_pose({"video_sweep": {"tilt_deg": -5}})
    assert p["tilt"] == -18000


def test_error_check_stops_match_who_is_here() -> None:
    from crissa_presence.error_peek import _seat_patrol_stops
    from crissa_presence.sweep_pose import error_check_down_12, error_check_stops

    patrol = list(_seat_patrol_stops())
    stops = error_check_stops()
    assert [(s["id"], s["pan"], s["tilt"]) for s in stops] == patrol
    # seat 12 is -20 since 2026-08-21 (door down 5d to level, same -5d on 12)
    assert [s["tilt_deg"] for s in stops] == [0, 0, 0, -20]
    assert stops[-1]["kind"] == "down_12"
    assert stops[-1]["id"] == "seat_12"
    d = error_check_down_12()
    assert d["id"] == "seat_12"
    assert d["tilt_deg"] == -20
    assert d["tilt"] == -54000
    assert d["pan"] != 300000
    assert d["snaps"] == 1


def test_camera_stops_on_disk_matches_default() -> None:
    stops = Path.home() / "projects/rail-presence<data>/camera-stops.json"
    data = json.loads(stops.read_text())
    vs = data["video_sweep"]
    assert vs["tilt_deg"] == -5
    assert vs["tilt"] == -18000
