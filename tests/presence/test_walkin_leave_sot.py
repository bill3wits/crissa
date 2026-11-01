"""Walk-in + leave on the single DoorInventory SoT (goal: enter/leave detect).

Drives shipped door_watch.DoorInventory — not a reimplementation.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory


@pytest.fixture(autouse=True)
def _user_user(monkeypatch):
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setenv("RAIL_PRESENCE_MATCH_SIM", "0.60")


def test_empty_room_door_unnamed_walkin_raises_headcount():
    """Empty SoT + door FOV body → +1 (no uncorroborated cancel)."""
    inv = DoorInventory()
    inv.set_baseline([], 0)
    assert inv.headcount() == 0
    evs = inv.observe_door(
        [],
        door_unnamed=1,
        door_bodies_max=1,
        now=100.0,
        entry_grace_sec=0.0,
        leave_confirm_sec=1.0,
    )
    kinds = [e.get("kind") for e in evs]
    assert "enter" in kinds, f"walk-in must mint enter, got {evs}"
    assert "crossing_cancelled" not in kinds
    assert inv.headcount() == 1
    assert inv.unnamed >= 1 or inv.present


def test_user_face_enter_then_operator_leave_clears_roster():
    """Named door enter → user present; strong leave (operator) → hc=0.

    AUTH-first: arm+empty alone does not leave sudo User (approach-and-return).
    """
    inv = DoorInventory()
    inv.set_baseline([], 0)
    enter = inv.observe_door(
        ["user"],
        door_unnamed=0,
        door_bodies_max=1,
        now=200.0,
        entry_grace_sec=0.0,
        leave_confirm_sec=1.0,
    )
    assert any(e.get("kind") == "enter" and e.get("name") == "user" for e in enter)
    assert "user" in inv.present
    assert inv.headcount() == 1

    inv.observe_door(
        ["user"], 0, now=210.0, entry_grace_sec=0.0, leave_confirm_sec=1.0
    )
    assert "user" in inv.exit_armed
    inv.observe_door([], 0, now=211.0, entry_grace_sec=0.0, leave_confirm_sec=1.0)
    leave_blocked = inv.observe_door(
        [], 0, now=213.0, entry_grace_sec=0.0, leave_confirm_sec=1.0
    )
    assert "user" in inv.present  # still here after arm+empty
    inv.force_leave(["user"], source="operator")
    assert "user" not in inv.present
    assert inv.headcount() == 0
    assert leave_blocked is not None


def test_null_door_no_transit_leaves_headcount_unchanged():
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    before = inv.headcount()
    # empty FOV but not exit-armed → no leave (null transit)
    inv.observe_door([], 0, now=50.0, entry_grace_sec=0.0, leave_confirm_sec=2.0)
    inv.observe_door([], 0, now=53.0, entry_grace_sec=0.0, leave_confirm_sec=2.0)
    assert inv.headcount() == before
    assert "user" in inv.present


def test_vl_plus_one_minus_one_distinct():
    """VL path: enter never decrements, leave never increments."""
    inv = DoorInventory()
    inv.set_baseline([], 0)
    inv.apply_vl_delta(1, 0, source="test", stamp_names=["user"], now=10.0, entry_grace_sec=0.0)
    assert inv.headcount() == 1 and "user" in inv.present
    inv.apply_vl_delta(0, 0, source="test", now=11.0)  # null
    assert inv.headcount() == 1
    inv.apply_vl_delta(0, 1, source="test", leave_person_id="user", now=12.0)
    assert inv.headcount() == 0 and "user" not in inv.present
    # enter does not leave
    inv.apply_vl_delta(1, 0, source="test", stamp_names=["user"], now=13.0, entry_grace_sec=0.0)
    assert inv.headcount() == 1
    # leave does not add
    inv.apply_vl_delta(0, 1, source="test", leave_person_id="user", now=14.0)
    assert inv.headcount() == 0
