"""Oracle visit instruction — one object, no AUTH grant."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.oracle_visit import apply_visit, short_id


@pytest.fixture(autouse=True)
def _guests(monkeypatch):
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setenv("RAIL_GUEST_IDS", "xena,val,will")


def test_short_id_glyphs():
    # the household glyph map does not ship: a generic id reads as its own initials (first two letters)
    assert short_id("user") == "Us"
    assert short_id("wanda") == "Wa"
    assert short_id("s1") == "S1"
    assert short_id("xena") == "Xe"


def test_short_id_guest_uses_stored_fl_initials(tmp_path, monkeypatch):
    people = tmp_path / "people" / "user_t"
    people.mkdir(parents=True)
    (people / "person.json").write_text(
        json.dumps(
            {
                "id": "user_t",
                "display": "user Turner",
                "role": "guest",
                "initials": "BT",
            }
        )
    )
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    assert short_id("user_t") == "BT"


def test_enter_adds_one_row():
    inv = DoorInventory()
    visits: dict = {}
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=100.0,
        clothes="dark shirt",
        hair="short",
        sex="M",
        faceid={"sim": 0.71, "face_px": [125, 199]},
        pack_id="ev_1",
    )
    assert out["applied"] is True
    assert out["event"] == "enter"
    assert out["headcount_after"] == 1
    assert "user" in inv.present
    assert visits["user"]["short_id"] == "Us"   # generic initials: the household glyph map does not ship
    assert visits["user"]["exit_ts"] is None
    assert visits["user"]["clothes"] == "dark shirt"
    assert inv.traits_live["user"]["hair"] == "short"


def test_enter_clothes_frozen_on_later_sighting():
    """WP-3: one robe on the enter card; sighting must not replace it."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=100.0,
        clothes="grey long-sleeve shirt, dark shorts",
        hair="short",
        sex="M",
        pack_id="ev_enter",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="sighting",
        person_id="user",
        now=110.0,
        clothes="green t-shirt, grey shorts",
        hair="short",
        sex="M",
        pack_id="ev_see",
    )
    assert visits["user"]["clothes"] == "grey long-sleeve shirt, dark shorts"
    tl = inv.traits_live["user"]
    assert tl["clothes"] == "grey long-sleeve shirt, dark shorts"
    assert tl["summary"] == tl["clothes"]


def test_sighting_does_not_repaint_hair_or_sex():
    """2026-08-25 05:48: already_present sighting painted F robe onto user."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=100.0,
        clothes="white t-shirt, dark shorts",
        hair="short dark",
        sex="M",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="sighting",
        person_id="user",
        now=110.0,
        clothes="light robe over white top and dark pants",
        hair="dark, pulled back",
        sex="F",
    )
    assert visits["user"]["hair"] == "short dark"
    assert visits["user"]["sex"] == "M"
    assert visits["user"]["clothes"] == "white t-shirt, dark shorts"
    tl = inv.traits_live["user"]
    assert tl["hair"] == "short dark"
    assert tl["sex"] == "M"
    assert tl["clothes"] == "white t-shirt, dark shorts"


def test_exit_who_wanda_user_clothes_decrements_user():
    """2026-08-25 06:32:14: Argus exit who=wanda, clothes=User, present
    {user,s1}. Must −1 user, never apply exit wanda with hc 2→2."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="white t-shirt, dark shorts",
        hair="short, dark",
        sex="M",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=11.0,
        clothes="light robe over white top and dark pants",
        hair="dark, pulled back",
        sex="F",
    )
    visits["wanda"] = {
        "person_id": "wanda",
        "event": "sighting",
        "clothes": "white t-shirt, dark shorts",
        "hair": "short, dark",
        "sex": "M",
        "enter_ts": 5.0,
        "exit_ts": None,
    }
    assert "wanda" not in inv.present
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="wanda",
        now=20.0,
        clothes="white t-shirt, dark shorts",
        hair="short, dark",
        sex="M",
    )
    assert out["applied"] is True, out
    assert out["person_id"] == "user", out
    assert out["reason"] == "user_exit_needs_confirm", out
    assert "user" in inv.present
    assert "s1" in inv.present


def test_exit_of_absent_person_does_not_no_op_count():
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="white t-shirt, dark shorts",
        hair="short",
        sex="M",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="wanda",
        now=20.0,
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    assert "user" in inv.present
    assert out["headcount_after"] == 1
    assert out.get("reason") in (
        "not_present",
        "appearance_contradicts",
        "need_person_id",
    )


def test_sighting_never_present_does_not_count():
    inv = DoorInventory()
    visits: dict = {}
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="sighting",
        person_id="s1",
        now=10.0,
    )
    assert out["applied"] is True
    assert "s1" not in inv.present
    assert out["headcount_after"] == 0


def test_already_present_enter_becomes_sighting():
    inv = DoorInventory()
    inv.present["user"] = 50.0
    visits: dict = {}
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=200.0,
        clothes="same shirt",
    )
    assert out["applied"] is True
    assert out["event"] == "sighting"
    assert out["reason"] == "already_present"
    assert out["headcount_after"] == 1
    assert out["headcount_before"] == 1


def test_guest_never_plus_one():
    inv = DoorInventory()
    visits: dict = {}
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="xena",
        now=300.0,
        clothes="blue dress",
    )
    assert out["applied"] is True
    assert out["event"] == "sighting"
    assert out["reason"] == "guest_visitor"
    assert "xena" not in inv.present
    assert out["headcount_after"] == 0


def test_user_exit_does_not_drop_until_confirm():
    """Two named bodies, no robe match: User still needs seat confirm."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(inventory=inv, visits=visits, event="enter", person_id="user", now=10.0)
    apply_visit(
        inventory=inv, visits=visits, event="enter", person_id="wanda", now=11.0
    )
    out = apply_visit(
        inventory=inv, visits=visits, event="exit", person_id="user", now=20.0
    )
    assert out["reason"] == "user_exit_needs_confirm"
    assert "user" in inv.present
    assert visits["user"].get("pending_confirm") is True
    assert visits["user"].get("exit_ts") is None
    assert out["headcount_after"] == 2


def test_sole_user_argus_exit_counts():
    """Clothes match still does not skip the User seat confirm (AUTH hole)."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="black shirt",
        hair="short",
        sex="M",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="",
        now=20.0,
        clothes="black shirt",
        hair="short",
        sex="M",
    )
    assert out["applied"] is True, out
    assert out["reason"] == "user_exit_needs_confirm", out
    assert "user" in inv.present
    assert visits["user"].get("pending_confirm") is True
    assert visits["user"].get("exit_ts") is None
    assert (out.get("leave_match") or {}).get("method") == "clothes_hair"


def test_unnamed_ghosts_do_not_block_sole_user_exit():
    """Live SoT often has U1… ghosts. Only user is named — his exit still counts."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(inventory=inv, visits=visits, event="enter", person_id="user", now=10.0)
    inv.present["U1"] = 10.0
    inv.present["U2"] = 10.0
    out = apply_visit(
        inventory=inv, visits=visits, event="exit", person_id="", now=20.0
    )
    assert out["applied"] is True, out
    assert out["person_id"] == "user"
    assert out["reason"] == "user_exit_needs_confirm", out
    assert "user" in inv.present
    assert (out.get("leave_match") or {}).get("method") == "sole_named"


def test_exit_ignores_face_id():
    """FaceID named wanda on a back. Robe matches user."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="black shirt gray shorts",
        hair="short",
        sex="M",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="wanda",
        now=11.0,
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="wanda",
        now=20.0,
        clothes="black shirt gray shorts",
        hair="short",
        sex="M",
        faceid={"name": "wanda", "sim": 0.62},
    )
    assert out["person_id"] == "user", out
    assert out["reason"] == "user_exit_needs_confirm", out
    assert "user" in inv.present
    assert "wanda" in inv.present


def test_exit_closes_row_and_drops_slot():
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv, visits=visits, event="enter", person_id="wanda", now=10.0
    )
    assert inv.headcount() == 1
    out = apply_visit(
        inventory=inv, visits=visits, event="exit", person_id="wanda", now=20.0
    )
    assert out["applied"] is True
    assert out["event"] == "exit"
    assert "wanda" not in inv.present
    assert visits["wanda"]["exit_ts"] == 20.0
    assert out["headcount_after"] == 0


def test_does_not_grant_auth():
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=1.0,
        faceid={"sim": 0.9},
    )
    assert not hasattr(inv, "_face_auth_hold")
    assert "auth" not in (visits["user"] or {})


def test_sighting_no_count_change():
    inv = DoorInventory()
    inv.present["user"] = 1.0
    visits: dict = {}
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="sighting",
        person_id="user",
        now=2.0,
        clothes="mug in hand",
    )
    assert out["event"] == "sighting"
    assert out["headcount_after"] == 1
    assert visits["user"]["clothes"] == "mug in hand"


def test_bad_event_rejected():
    inv = DoorInventory()
    out = apply_visit(
        inventory=inv, visits={}, event="maybe", person_id="user", now=1.0
    )
    assert out["applied"] is False
    assert out["reason"] == "bad_event"
    assert inv.headcount() == 0


def test_exit_keeps_enter_robe_and_closes_the_card():
    """Who left is written on the visit; the enter card is no longer open."""
    from crissa_presence import oracle_event as oe
    from crissa_presence.oracle_event import get_event, open_event, open_visits

    oe._EVENTS.clear()
    oe._ALIASES.clear()
    inv = DoorInventory()
    visits: dict = {}
    open_event(10.0, pack_id="ev_in")
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=10.0,
        clothes="light gray cardigan",
        hair="long wavy brown",
        sex="F",
        event_id="ev_in",
    )
    open_event(20.0, pack_id="ev_out")
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="s1",
        now=20.0,
        clothes="light gray cardigan",
        hair="long wavy brown",
        sex="F",
        event_id="ev_out",
    )
    assert out["applied"] is True
    assert visits["s1"]["clothes"] == "light gray cardigan"
    assert visits["s1"]["leave_clothes"] == "light gray cardigan"
    assert visits["s1"]["leave_match"]["method"] == "clothes_hair"
    assert out["leave_match"]["person_id"] == "s1"
    assert get_event("ev_in")["exit_ts"] == 20.0
    assert get_event("ev_in")["clothes"] == "light gray cardigan"
    assert get_event("ev_out")["person_id"] == "s1"
    assert get_event("ev_out")["leave_match"]["person_id"] == "s1"
    assert "s1" not in inv.present
    leave_evs = [e for e in inv.events if e.get("kind") == "leave"]
    assert leave_evs and leave_evs[-1].get("leave_match", {}).get("person_id") == "s1"
    assert not any(r.get("person_id") == "s1" for r in open_visits())


def test_exit_subtracts_occupant_matching_leave_appearance():
    """Two open occupants. Gray-shirt/short-graying leave subtracts B, not A."""
    from crissa_presence import oracle_event as oe

    oe._EVENTS.clear()
    oe._ALIASES.clear()
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=10.0,
        clothes="light gray cardigan over gray top",
        hair="long wavy brown",
        sex="F",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="wanda",
        now=15.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    assert set(inv.present) == {"s1", "wanda"}
    assert inv.headcount() == 2
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="",
        now=20.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    assert out["applied"] is True, out
    assert out["person_id"] == "wanda", out
    assert out["headcount_before"] == 2
    assert out["headcount_after"] == 1
    assert "wanda" not in inv.present
    assert "s1" in inv.present
    assert visits["s1"].get("exit_ts") is None
    assert visits["wanda"]["exit_ts"] == 20.0
    assert (out.get("leave_match") or {}).get("person_id") == "wanda"
    assert (out.get("leave_match") or {}).get("method") == "clothes_hair"


def test_exit_wrong_person_id_still_subtracts_appearance_match():
    """Sole-visit used to send s1 for a man's leave. Appearance wins."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=10.0,
        clothes="light gray cardigan over gray top",
        hair="long wavy brown",
        sex="F",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="wanda",
        now=15.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="s1",
        now=20.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    assert out["person_id"] == "wanda", out
    assert "wanda" not in inv.present
    assert "s1" in inv.present
    assert inv.headcount() == 1


def test_exit_prose_blue_shirt_drops_that_id():
    """Argus says the shirt walked out. JSON clothes may be empty. hc −1."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="blue short-sleeve shirt",
        hair="short",
        sex="M",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="wanda",
        now=11.0,
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    assert inv.headcount() == 2
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="",
        now=20.0,
        clothes="",
        note="A person in a blue short sleev shirt walked out.",
    )
    assert out["applied"] is True, out
    assert out["person_id"] == "user", out
    assert out["reason"] == "user_exit_needs_confirm", out
    assert "user" in inv.present
    assert "wanda" in inv.present
    assert (out.get("leave_match") or {}).get("method") == "clothes_hair"


def test_exit_sex_mismatch_does_not_subtract():
    """A man leaving must not drop the only open card when that card is F."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=10.0,
        clothes="light gray cardigan",
        hair="long wavy brown",
        sex="F",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="s1",
        now=20.0,
        clothes="gray long-sleeve shirt",
        hair="short graying",
        sex="M",
    )
    assert out["applied"] is False, out
    assert out["reason"] == "appearance_contradicts", out
    assert "s1" in inv.present
    assert inv.headcount() == 1
    assert visits["s1"].get("exit_ts") is None


def test_exit_unique_clothes_drops_user():
    """WP-4: unique clothes win on User → drop User."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="blue short-sleeve shirt",
        hair="short",
        sex="M",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="wanda",
        now=11.0,
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="",
        now=20.0,
        clothes="blue short-sleeve shirt",
        hair="short",
        sex="M",
    )
    assert out["applied"] is True, out
    assert out["person_id"] == "user", out
    assert out["reason"] == "user_exit_needs_confirm", out
    assert "user" in inv.present
    assert "wanda" in inv.present
    assert (out.get("leave_match") or {}).get("method") == "clothes_hair"


def test_exit_color_mismatch_never_drops_user():
    """WP-4: contradict User card (hard color) → never drop User AUTH."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="white shirt",
        hair="short",
        sex="M",
    )
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="s1",
        now=11.0,
        clothes="blue shirt",
        hair="short",
        sex="M",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="user",
        now=20.0,
        clothes="blue shirt",
        hair="short",
        sex="M",
    )
    assert out["person_id"] == "s1", out
    assert "s1" not in inv.present
    assert "user" in inv.present


def test_exit_sole_user_stale_robe_goes_to_confirm():
    """08:20 unnamed_exit: sole User, enter card still a robe, leave is a shirt.

    Color mismatch is a stale card, not a second body. AUTH waits on the
    seat snap — do not no-op the exit.
    """
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="brown robe",
        hair="short",
        sex="M",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="",
        now=20.0,
        clothes="grey shirt",
        hair="short",
        sex="M",
    )
    assert out["reason"] == "user_exit_needs_confirm", out
    assert out["person_id"] == "user", out
    assert "user" in inv.present
    assert visits["user"].get("pending_confirm") is True
    assert visits["user"].get("exit_ts") is None
    assert (out.get("leave_match") or {}).get("method") == "sole_stale_card"


def test_exit_sole_user_sex_mismatch_refuses():
    """Woman leaving while User is the only named body — do not de-AUTH."""
    inv = DoorInventory()
    visits: dict = {}
    apply_visit(
        inventory=inv,
        visits=visits,
        event="enter",
        person_id="user",
        now=10.0,
        clothes="white shirt",
        hair="short",
        sex="M",
    )
    out = apply_visit(
        inventory=inv,
        visits=visits,
        event="exit",
        person_id="",
        now=20.0,
        clothes="green jacket",
        hair="ponytail",
        sex="F",
    )
    assert out["applied"] is False, out
    assert out["reason"] == "appearance_contradicts", out
    assert "user" in inv.present
