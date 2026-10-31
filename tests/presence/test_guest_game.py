"""Threshold Game guest mechanics (user 2026-07-28): guests are numbers by
arrival order; the OBJECT owns guestN -> slot; voters never send identity."""

from crissa_presence.door_watch import DoorInventory
from crissa_presence.service import PresenceService
from crissa_presence import seat_slots as seats
import pytest


# These guard the occupancy arithmetic, which the service publishes only with the occupancy mill on: RAIL_OCCUPANCY,
# default off since the rail's 00a232b ("Occupancy mill off", 2026-08-27; crissa_presence.service.occupancy_on). With
# it unset every named person is dropped from the published state and headcount reads 0, whatever the arithmetic did.
@pytest.fixture(autouse=True)
def _occupancy_mill_on(monkeypatch):
    monkeypatch.setenv("RAIL_OCCUPANCY", "1")


class _FakeCam:
    mode = "fake"

    def open(self):
        return False


def test_guest_roster_is_arrival_ordered():
    inv = DoorInventory()
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=10.0)
    inv.observe_door(["wanda"], 0, leave_confirm_sec=2.0,
                     entry_grace_sec=5.0, now=20.0)
    inv.apply_vl_delta(1, 0, source="qwen", now=40.0)   # unnamed third arrival
    roster = inv.guest_roster()
    assert roster[0] == "user"
    assert roster[1] == "wanda"
    assert roster[2].startswith("__u")


def test_leave_guest_resolves_on_object_side():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    assert svc.get().headcount == 2
    # voter says "guest 2 left" — object resolves to wanda (second arrival)
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_guest=2, force=True)
    names = [p.name for p in st.people]
    assert "user" in names and "wanda" not in names


def test_invalid_guest_falls_back_not_crashes():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    st = svc.apply_vl_delta(0, 1, source="qwen", leave_guest=7, force=True)
    # sole-occupant ladder still resolves; no exception, count correct
    assert st.headcount == 0


def test_leave_package_carries_guest_indices():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["user"], force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"], force=True)
    pkg = svc.leave_package()
    guests = [(o["guest"], o["person_id"]) for o in pkg["occupants"]]
    assert guests[0] == (1, "user")
    assert guests[1] == (2, "wanda")


def test_face_absorb_keeps_arrival_slot():
    """VL body arrives (guest1) then face names it — same body keeps guest1."""
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="qwen", now=10.0)
    inv.observe_door(["user"], 0, leave_confirm_sec=2.0, entry_grace_sec=45.0, now=12.0)
    assert inv.guest_roster() == ["user"]
    assert inv.arrival_seq["user"] == 1


def test_station_guess_lands_on_slot_and_publishes():
    """Resting-position guess rides the add and publishes per person —
    the Inspector aims the beam mic at sources.station_guess."""
    svc = PresenceService(camera=_FakeCam())
    desk = seats.user_seat()
    st = svc.apply_vl_delta(1, 0, source="qwen", stamp_names=["user"],
                            station_guess=seats.seat_named("wanda"),
                            force=True)
    assert svc.inventory.station_guess.get("user") == desk
    slot = next(p for p in st.people if p.name == "user")
    assert (slot.sources or {}).get("station_guess") == desk


def test_named_seats_come_from_init_and_guests_get_none():
    """Seats are a LABEL from init, never a guess (user 2026-08-16: "if we do
    not need a seat detector or guess then delete that part. We have a seat
    detector, the mic.").

    Nothing in the rail gates on seat — identity, AUTH, headcount and matching
    all ignore it — so asking a vision model where an UNNAMED body probably sat
    produced a number no one read. A named regular still gets an hour, because
    that is a lookup in room_geometry, not an inference.
    """
    desk = seats.user_seat()
    mich = seats.seat_named("wanda")
    assert desk and mich

    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["wanda"],
                       station_guess=mich, force=True)
    svc.apply_vl_delta(1, 0, source="op", stamp_names=["guest"],
                       station_guess=mich, force=True)

    # named regular -> init lookup
    assert svc.inventory.station_guess.get("wanda") == mich
    # unnamed guest -> no seat is invented for them any more
    assert "guest" not in svc.inventory.station_guess
    # and a station_guess handed in by the VL never lands
    assert not any(v in seats.forbidden_sit()
                   for v in svc.inventory.station_guess.values())
