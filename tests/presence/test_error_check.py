"""error-check: 8 fps 6→12 mapping, SoT write, lease home on failure."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.error_check import (
    assign_seats,
    bodies_seen,
    hour_from_pan,
    record_on_sot,
    run_error_check,
    seats_from_cards,
)
from crissa_presence.schema import PersonSlot, RoomPresence
from crissa_presence.sweep_pose import error_check_stops


def test_error_check_is_station_snaps_not_video() -> None:
    from crissa_presence.error_peek import _seat_patrol_stops

    stops = error_check_stops()
    patrol = list(_seat_patrol_stops())
    assert [(s["id"], s["pan"], s["tilt"]) for s in stops] == patrol
    kinds = [s["kind"] for s in stops]
    assert kinds[-1] == "down_12"
    assert all(k == "level" for k in kinds[:-1])
    assert all(s["snaps"] == 1 for s in stops)
    ids = [s["id"] for s in stops]
    assert ids == ["desk", "wanda", "stand_11", "seat_12"]
    by = {s["id"]: s for s in stops}
    assert by["desk"]["tilt"] == 0
    assert by["wanda"]["tilt"] == 0
    assert by["stand_11"]["tilt"] == 0
    assert by["stand_11"]["pan"] == by["seat_12"]["pan"]
    # -20, not -15: user 2026-08-21 took the door down 5d to level and
    # applied the same -5d to seat 12. camera-stops.json has carried
    # -72000 since; these assertions were never updated and had been
    # failing on the real file.
    assert by["seat_12"]["tilt"] == -72000
    assert by["seat_12"]["tilt_deg"] == -20
    assert by["seat_12"]["pan"] != 300000


def test_hour_from_pan_uses_measured_seats() -> None:
    hours = [(0, 6), (225000, 8), (432000, 10), (515000, 11), (515000, 12)]
    assert hour_from_pan(0, hours) == 6
    assert hour_from_pan(220000, hours) == 8
    assert hour_from_pan(440000, hours) == 10
    # same pan: prefer hour 12 over 11
    assert hour_from_pan(515000, hours) == 12
    assert hour_from_pan(9_000_000, hours) is None


def test_seats_from_cards_one_row_per_hour() -> None:
    cards = [
        {"pan": 0, "bodies": 1, "photo": "a.jpg"},
        {"pan": 70000, "bodies": 1, "photo": "b.jpg"},
        {"pan": 225000, "bodies": 0, "photo": "c.jpg"},
        {"pan": 515000, "bodies": 1, "photo": "d.jpg"},
    ]
    rows = seats_from_cards(cards)
    by = {r["seat"]: r for r in rows}
    assert 6 in by and by[6]["occupied"]
    assert 8 in by and not by[8]["occupied"]
    assert 12 in by and by[12]["occupied"]


def test_bodies_seen_does_not_sum_exclusive_hours() -> None:
    rows = [
        {"seat": 6, "bodies": 1, "occupied": True},
        {"seat": 8, "bodies": 1, "occupied": True},
        {"seat": 10, "bodies": 2, "occupied": True},
    ]
    # 2 exclusive + door frame's 2
    assert bodies_seen(rows) == 4


def test_assign_seats_user_desk_first() -> None:
    guess = assign_seats(
        named=["user", "wanda"],
        unnamed_keys=["__u0"],
        occupied_hours=[8, 6],
        old_guess={"wanda": 8},
    )
    assert guess["user"] == 6
    assert guess["wanda"] == 8
    assert "__u0" not in guess


def test_assign_seats_does_not_plant_user_on_door_blobs() -> None:
    guess = assign_seats(
        named=["user"],
        unnamed_keys=["__u0", "__u1"],
        occupied_hours=[10, 12],
        old_guess={},
    )
    assert "user" not in guess
    assert guess["__u0"] == 10
    assert guess["__u1"] == 12


def test_record_on_sot_operator_drops_ghost_name() -> None:
    """Control Panel Scan is a recount: a named ghost must actually leave.

    2026-08-18: error-check saw 1 body, union kept user+wanda, hc stayed 2.
    """
    inv = DoorInventory()
    inv.set_baseline(["user", "wanda"], 0)
    assert inv.headcount() == 2

    class _Svc:
        def __init__(self, inventory):
            self.inventory = inventory
            self.st = None

        def _presence_from_inventory(self, **kw):
            people = [
                PersonSlot(
                    slot_id=n,
                    name=n,
                    seat=self.inventory.station_guess.get(n),
                )
                for n in self.inventory.names()
            ]
            for i, uk in enumerate(self.inventory.unnamed_keys[: self.inventory.unnamed]):
                people.append(
                    PersonSlot(
                        slot_id=f"U{i + 1}",
                        name=f"U{i + 1}",
                        seat=self.inventory.station_guess.get(uk),
                    )
                )
            return RoomPresence(
                headcount=self.inventory.headcount(),
                people=people,
                last_event=kw.get("last_event") or {},
            )

        def _set(self, st):
            self.st = st

        def _save_inventory_disk(self, force=False):
            return None

    svc = _Svc(inv)
    out = record_on_sot(
        svc,
        [{"seat": 6, "bodies": 1, "occupied": True, "pan": 0}],
    )
    assert out["ok"]
    assert out["bodies_seen"] == 1
    assert "wanda" not in inv.names()
    assert "user" in inv.names()
    assert inv.headcount() == 1
    assert "wanda" in (out["reconcile"].get("left") or [])
    assert svc.st.headcount == 1


def test_record_on_sot_shrinks_unnamed_on_one_body() -> None:
    inv = DoorInventory()
    inv.set_baseline(["user"], 0)
    for _ in range(5):
        inv._new_unnamed_key(source="test")
        inv.unnamed += 1
    assert inv.headcount() == 6

    class _Svc:
        def __init__(self, inventory):
            self.inventory = inventory
            self.st = None

        def _presence_from_inventory(self, **kw):
            people = [
                PersonSlot(
                    slot_id=n,
                    name=n,
                    seat=self.inventory.station_guess.get(n),
                )
                for n in self.inventory.names()
            ]
            for i, uk in enumerate(self.inventory.unnamed_keys[: self.inventory.unnamed]):
                people.append(
                    PersonSlot(
                        slot_id=f"U{i + 1}",
                        name=f"U{i + 1}",
                        seat=self.inventory.station_guess.get(uk),
                    )
                )
            return RoomPresence(
                headcount=self.inventory.headcount(),
                people=people,
                last_event=kw.get("last_event") or {},
            )

        def _set(self, st):
            self.st = st

        def _save_inventory_disk(self, force=False):
            return None

    svc = _Svc(inv)
    out = record_on_sot(
        svc,
        [{"seat": 6, "bodies": 1, "occupied": True, "pan": 0}],
    )
    assert out["ok"]
    assert out["bodies_seen"] == 1
    assert inv.unnamed == 0
    assert inv.headcount() == 1
    assert inv.station_guess.get("user") == 6
    assert svc.st is not None
    assert svc.st.last_event.get("kind") == "error_check"
    assert svc.st.last_event.get("facefinder") is False


def test_run_error_check_is_the_same_walk(monkeypatch) -> None:
    import crissa_presence.error_check as ec

    def _who(_presence):
        return {
            "ok": True,
            "seen": ["user"],
            "unnamed": 0,
            "bodies_seen": 1,
            "sot": {"headcount": 1, "people": [{"name": "user", "seat": 6}]},
            "door": {"ok": True},
            "looks": [],
            "wall_sec": 0.1,
        }

    monkeypatch.setattr("crissa_presence.inspector.who_is_here", _who)

    class _Svc:
        inventory = DoorInventory()

        def get(self):
            return {}

    out = run_error_check(_Svc())
    assert out["ok"] is True
    assert out["facefinder"] is True
    assert out["mode"] == "who_is_here"
    assert out["sot"]["headcount"] == 1
    assert out["camera_returned_home"] is True
