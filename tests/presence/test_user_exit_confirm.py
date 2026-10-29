"""User-exit confirm: seat face keeps AUTH; empty seat may drop."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.user_exit_confirm import decide_user_exit, user_seat_pose


def test_seat_pose_is_desk() -> None:
    p = user_seat_pose()
    assert p["pan"] == 0
    assert p["tilt"] == 0


def test_user_in_seat_not_confirmed() -> None:
    d = decide_user_exit(seat={"found": True, "sim": 0.72, "name": "user"})
    assert d["confirmed"] is False
    assert d["reason"] == "user_in_seat"


def test_user_in_sweep_not_confirmed() -> None:
    d = decide_user_exit(
        seat={"found": False, "n_faces": 0},
        sweep_hits=[{"found": True, "file": "v090.jpg", "name": "user"}],
    )
    assert d["confirmed"] is False
    assert d["reason"] == "user_in_room"


def test_empty_seat_no_face_confirms() -> None:
    d = decide_user_exit(seat={"found": False, "n_faces": 0, "sim": None})
    assert d["confirmed"] is True
    assert d["reason"] == "seat_empty_and_no_user_face"


def test_face_error_fail_closed() -> None:
    d = decide_user_exit(seat={"found": False, "error": "no_frame"})
    assert d["confirmed"] is False
    assert d["reason"] == "confirm_failed"


def test_force_leave_oracle_visit_does_not_drop_user() -> None:
    inv = DoorInventory()
    inv.present["user"] = 1.0
    evs = inv.force_leave(["user"], source="oracle_visit")
    assert "user" in inv.present
    assert evs and evs[0].get("kind") == "leave_blocked"


def test_force_leave_confirmed_drops_user() -> None:
    inv = DoorInventory()
    inv.present["user"] = 1.0
    evs = inv.force_leave(["user"], source="argus_exit_confirmed")
    assert "user" not in inv.present
    assert evs and evs[0].get("kind") == "leave"


def test_confirm_user_exit_does_not_smooth_scan() -> None:
    import inspect

    from crissa_presence.service import PresenceService

    src = inspect.getsource(PresenceService.confirm_user_exit)
    assert "smooth_scan" not in src
    assert "shot_at" in src
    assert "camera_leased" in src
    assert "steal" not in src


def test_null_clip_plus_empty_seat_keeps_auth() -> None:
    d = decide_user_exit(
        seat={"found": False, "n_faces": 0},
        user_exit=None,
        user_exit_set=True,
    )
    assert d["confirmed"] is False
    assert d["reason"] == "confirm_failed"


def test_clip_said_stay_keeps_auth_without_face() -> None:
    d = decide_user_exit(
        seat={"found": False, "n_faces": 0},
        user_exit=False,
        user_exit_set=True,
    )
    assert d["confirmed"] is False
    assert d["reason"] == "clip_said_stay"


def test_clip_leave_plus_empty_seat_confirms() -> None:
    d = decide_user_exit(
        seat={"found": False, "n_faces": 0},
        user_exit=True,
        user_exit_set=True,
    )
    assert d["confirmed"] is True


def test_seat_face_beats_clip_leave() -> None:
    d = decide_user_exit(
        seat={"found": True, "sim": 0.72, "name": "user"},
        user_exit=True,
        user_exit_set=True,
    )
    assert d["confirmed"] is False
    assert d["reason"] == "user_in_seat"


def test_force_leave_clothes_exit_drops_user() -> None:
    inv = DoorInventory()
    inv.present["user"] = 1.0
    evs = inv.force_leave(["user"], source="argus_clothes_exit")
    assert "user" not in inv.present
    assert evs and evs[0].get("kind") == "leave"


def test_force_leave_argus_source_blocked_for_user() -> None:
    inv = DoorInventory()
    inv.present["user"] = 1.0
    evs = inv.force_leave(["user"], source="argus")
    assert "user" in inv.present
    assert evs and evs[0].get("kind") == "leave_blocked"
