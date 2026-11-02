"""Guests are not occupancy — docs/GUEST-VISITOR-TODAY-20260813.md"""

from crissa_presence.door_watch import DoorInventory
from crissa_presence.guests import is_guest
from crissa_presence.schema import RoomPresence, empty_room


def test_guest_ids():
    assert is_guest("xena")
    assert is_guest("val")
    assert is_guest("yuri")
    assert not is_guest("user")
    assert not is_guest("wanda")


def test_guest_face_does_not_enter():
    inv = DoorInventory()
    inv.observe_door(["user"], 0, now=100.0, door_bodies_max=1)
    evs = inv.observe_door(["xena"], 0, now=110.0, door_bodies_max=1)
    assert inv.headcount() == 1
    assert "user" in inv.names()
    assert "xena" not in inv.names()
    assert "xena" in inv.visitor_today
    assert any(e.get("kind") == "visitor" and e.get("name") == "xena" for e in evs)
    assert not any(e.get("kind") == "enter" and e.get("name") == "xena" for e in evs)


def test_guest_face_empty_room_no_headcount():
    inv = DoorInventory()
    evs = inv.observe_door(["xena"], 0, now=10.0, door_bodies_max=1)
    assert inv.headcount() == 0
    assert inv.names() == []
    assert "xena" in inv.visitor_today
    assert any(e.get("kind") == "visitor" for e in evs)
    assert not any(e.get("kind") == "enter" for e in evs)


def test_guest_never_leave():
    inv = DoorInventory()
    inv.observe_door(["user", "xena"], 0, now=10.0, door_bodies_max=2)
    evs = inv.observe_door(["user"], 0, now=20.0, door_bodies_max=1)
    evs += inv.observe_door(["user"], 0, now=30.0, door_bodies_max=1)
    assert inv.headcount() == 1
    assert not any(e.get("kind") == "leave" and e.get("name") == "xena" for e in evs)


def test_guest_evicted_from_occupancy():
    inv = DoorInventory()
    inv.present["xena"] = 1.0
    inv.baseline_set = True
    evs = inv.observe_door(["user"], 0, now=10.0, door_bodies_max=1)
    assert "xena" not in inv.present
    assert "xena" in inv.visitor_today
    assert any(e.get("kind") == "guest_not_occupancy" for e in evs)
    assert not any(e.get("kind") == "leave" and e.get("name") == "xena" for e in evs)
    assert inv.headcount() == 1


def test_vl_nameless_plus_guest_stamp():
    """Door game +1 stays nameless; xena stamp is visitor_today only."""
    inv = DoorInventory()
    evs = inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["xena"], now=10.0)
    assert inv.unnamed == 1
    assert inv.headcount() == 1
    assert "xena" not in inv.names()
    assert "xena" in inv.visitor_today
    assert any(e.get("kind") == "visitor" for e in evs)
    assert not any(e.get("name") == "xena" and e.get("kind") == "enter" for e in evs)


def test_vl_plus_user_still_named():
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["user"], now=10.0)
    assert inv.names() == ["user"]
    assert inv.headcount() == 1


def test_wanda_still_occupancy():
    inv = DoorInventory()
    inv.observe_door(["wanda"], 0, now=10.0, door_bodies_max=1)
    assert "wanda" in inv.names()
    assert inv.headcount() == 1


def test_baseline_strips_guests():
    inv = DoorInventory()
    inv.set_baseline(["user", "xena"], 0)
    assert inv.names() == ["user"]
    assert "xena" in inv.visitor_today
    assert inv.headcount() == 1


def test_schema_omits_empty_visitor_today():
    d = empty_room().to_dict()
    assert "visitor_today" not in d
    r = RoomPresence(visitor_today=[{"person_id": "xena", "day": "2026-08-13"}])
    assert r.to_dict()["visitor_today"][0]["person_id"] == "xena"
