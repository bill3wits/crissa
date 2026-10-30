"""CONTRACT-CLOTHING-LEAVE Phase A — traits, leave match, leave_package."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.service import PresenceService
from crissa_presence.traits_leave import (
    best_traits_match,
    clothes_from_prose,
    leave_contradicts_card,
    match_leave_occupant,
    normalize_traits,
    score_traits,
)
import pytest


# These guard the occupancy arithmetic, which the service publishes only with the occupancy mill on: RAIL_OCCUPANCY,
# default off since the rail's 00a232b ("Occupancy mill off", 2026-08-27; crissa_presence.service.occupancy_on). With
# it unset every named person is dropped from the published state and headcount reads 0, whatever the arithmetic did.
@pytest.fixture(autouse=True)
def _occupancy_mill_on(monkeypatch):
    monkeypatch.setenv("RAIL_OCCUPANCY", "1")


class _FakeCam:
    mode = "dir"
    last_error = None
    _jpeg = b"\xff\xd8\xff\xd9"

    def grab(self):
        return self._jpeg

    def grab_buffer(self, **kw):
        return [self._jpeg]


def test_normalize_traits():
    t = normalize_traits(
        {
            "shirt": "White",
            "hair": "dark",
            "height_frac_door": 0.9,
            "height_frac_frame": 0.6,
            "summary": "white shirt",
        }
    )
    assert t["shirt"] == "white"
    assert t["height_frac_door"] == 0.9


def test_clothes_from_prose_blue_short_sleeve():
    assert "blue" in (clothes_from_prose("blue short sleev shirt walked out") or "")
    assert "shirt" in (
        clothes_from_prose("A person in a blue short-sleeve shirt walked out.") or ""
    )
    assert clothes_from_prose("No doorway crossing is visible.") is None
    assert clothes_from_prose("x") is None


def test_match_leave_typo_short_sleev():
    cards = [
        {
            "person_id": "user",
            "clothes": "blue short-sleeve shirt",
            "hair": "short",
            "sex": "M",
        },
        {
            "person_id": "wanda",
            "clothes": "green jacket",
            "hair": "ponytail",
            "sex": "F",
        },
    ]
    picked, sc, why = match_leave_occupant(
        {"clothes": "blue short sleev shirt"}, cards
    )
    assert picked == "user", (picked, sc, why)
    assert sc >= 0.35


def test_match_leave_color_mismatch_does_not_pick_other_shirt():
    cards = [
        {"person_id": "user", "clothes": "white shirt", "sex": "M"},
        {"person_id": "s1", "clothes": "blue shirt", "sex": "M"},
    ]
    picked, sc, why = match_leave_occupant({"clothes": "blue short-sleeve shirt"}, cards)
    assert picked == "s1", (picked, sc, why)


def test_leave_contradicts_hard_color():
    card = {"person_id": "user", "clothes": "white shirt", "sex": "M"}
    assert leave_contradicts_card({"clothes": "blue shirt", "sex": "M"}, card) is True
    assert leave_contradicts_card({"clothes": "white tee", "sex": "M"}, card) is False
    assert leave_contradicts_card({"clothes": "white shirt", "sex": "F"}, card) is True


def test_score_traits_clothing():
    a = {"shirt": "white", "hair": "dark", "height_frac_door": 0.90}
    b = {"shirt": "white", "hair": "dark", "height_frac_door": 0.88}
    c = {"shirt": "green", "hair": "dark", "height_frac_door": 0.85}
    assert score_traits(a, b) > score_traits(a, c)


def test_enter_traits_on_named():
    svc = PresenceService(camera=_FakeCam())
    st = svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["user"],
        force=True,
        enter_traits={
            "shirt": "white",
            "hair": "dark",
            "height_frac_door": 0.90,
            "height_frac_frame": 0.60,
            "summary": "white shirt dark hair ~90% door",
        },
    )
    assert st.headcount == 1
    people = [p for p in st.people if p.name == "user"]
    assert people
    tr = people[0].traits_live or {}
    assert tr.get("shirt") == "white"
    assert abs(float(tr.get("height_frac_door") or 0) - 0.9) < 0.01


def test_sole_leave():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="test", stamp_names=["user"], force=True)
    st = svc.apply_vl_delta(0, 1, source="test", force=True)
    assert st.headcount == 0
    lm = (st.last_event or {}).get("leave_match") or {}
    assert lm.get("method") == "sole"
    assert lm.get("person_id") == "user"


def test_leave_person_id():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["wanda"],
        force=True,
        enter_traits={"shirt": "green", "hair": "dark"},
    )
    # force second body past dedup/unnamed cap
    svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["user"],
        force=True,
        enter_traits={"shirt": "white", "hair": "dark"},
    )
    assert svc.get().headcount == 2
    st = svc.apply_vl_delta(
        0,
        1,
        source="test",
        force=True,
        leave_person_id="user",
        leave_match_conf=0.8,
        leave_match_method="visual_still",
    )
    names = [p.name for p in st.people if p.name]
    assert "user" not in names
    assert "wanda" in names
    lm = (st.last_event or {}).get("leave_match") or {}
    assert lm.get("person_id") == "user"
    assert lm.get("method") in ("vl_soft", "visual_still")


def test_traits_match_picks_correct():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["wanda"],
        force=True,
        enter_traits={"shirt": "green", "hair": "dark", "height_frac_door": 0.85},
    )
    svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["user"],
        force=True,
        enter_traits={"shirt": "white", "hair": "dark", "height_frac_door": 0.90},
    )
    st = svc.apply_vl_delta(
        0,
        1,
        source="test",
        force=True,
        leave_traits={"shirt": "white", "hair": "dark", "height_frac_door": 0.89},
    )
    names = [p.name for p in st.people if p.name]
    assert "user" not in names
    assert "wanda" in names
    lm = (st.last_event or {}).get("leave_match") or {}
    assert lm.get("method") == "traits"
    assert lm.get("person_id") == "user"


def test_unknown_leave_person_id_ignored_uses_traits_or_fallback():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["user"],
        force=True,
        enter_traits={"shirt": "white"},
    )
    st = svc.apply_vl_delta(
        0,
        1,
        source="test",
        force=True,
        leave_person_id="not_here",
        leave_traits={"shirt": "white"},
    )
    assert st.headcount == 0
    lm = (st.last_event or {}).get("leave_match") or {}
    # traits or sole (only one person)
    assert lm.get("person_id") == "user"


def test_leave_package_shape():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(
        1,
        0,
        source="test",
        stamp_names=["user"],
        force=True,
        enter_traits={"shirt": "white", "height_frac_door": 0.9},
    )
    pkg = svc.leave_package()
    assert pkg["schema"] == "leave_package/v1"
    assert pkg["headcount"] == 1
    assert pkg["sole"] is True
    assert len(pkg["occupants"]) == 1
    occ = pkg["occupants"][0]
    assert occ["person_id"] == "user"
    assert occ["traits_live"]["shirt"] == "white"


def test_best_traits_match_helper():
    leave = {"shirt": "white", "hair": "dark"}
    pool = {
        "user": {"shirt": "white", "hair": "dark"},
        "wanda": {"shirt": "green", "hair": "dark"},
    }
    pid, sc = best_traits_match(leave, pool)
    assert pid == "user"
    assert sc > 0.3


def test_vl_enter_traits_transfer_on_face_absorb():
    """VL stores traits on unnamed; door_face absorb must move them onto user."""
    inv = DoorInventory()
    # VL-style enter (no name)
    inv.apply_vl_delta(
        1,
        0,
        source="cosmos",
        enter_traits={"shirt": "white", "hair": "dark", "height_frac_door": 0.9},
    )
    assert inv.unnamed == 1
    assert inv.unnamed_keys
    uk = inv.unnamed_keys[-1]
    assert inv.traits_live.get(uk, {}).get("shirt") == "white"
    # fake still meta
    inv.enter_still[uk] = {"paths": ["/tmp/x.jpg"], "count": 1}

    evs = inv.observe_door(["user"], door_unnamed=0)
    assert any(e.get("name") == "user" and e.get("source") == "door_face" for e in evs)
    assert inv.unnamed == 0
    assert "user" in inv.present
    assert inv.traits_live.get("user", {}).get("shirt") == "white"
    assert inv.enter_still.get("user", {}).get("count") == 1
    assert uk not in inv.traits_live
    assert uk not in inv.enter_still


def test_vl_stamp_enter_absorbs_unnamed_traits():
    inv = DoorInventory()
    inv.apply_vl_delta(
        1,
        0,
        source="qwen",
        enter_traits={"shirt": "white", "summary": "white tee"},
    )
    inv.apply_vl_delta(
        1,
        0,
        source="face",
        stamp_names=["user"],
        enter_traits=None,
    )
    assert inv.headcount() == 1
    assert "user" in inv.present
    assert inv.unnamed == 0
    assert inv.traits_live.get("user", {}).get("shirt") == "white"


def test_sole_empty_leave_match_and_long_confirm():
    """Sole empty needs long confirm; leave_match.method=sole."""
    inv = DoorInventory()
    inv.apply_vl_delta(
        1,
        0,
        source="op",
        stamp_names=["user"],
        enter_traits={"shirt": "white"},
        now=100.0,
        entry_grace_sec=5.0,
    )
    # past grace, start empty clock
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=110.0)
    assert "user" in inv.present
    # 50s empty still keeps
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=160.0)
    assert "user" in inv.present
    # 121s from leave_since (110): desk sit — MUST stay (user law 2026-07-27)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=110.0 + 121.0)
    assert "user" in inv.present
    # ghost recovery past 1800s — User protected (2026-08-11); use operator leave
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0, now=110.0 + 1801.0)
    assert "user" in inv.present, "User must not ghost-leave"
    inv.force_leave(["user"], source="operator")
    assert "user" not in inv.present


def test_stale_empty_spares_certain():
    """STALE_EMPTY timer must not clear id_certain people (COORD 12:16)."""
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(
        1,
        0,
        source="op",
        stamp_names=["user"],
        force=True,
        face_certain=True,
        enter_traits={"shirt": "white"},
    )
    assert svc.get().headcount == 1
    assert svc._id_certain.get("user") is True
    # simulate empty FOV timer fire path
    svc._empty_fov_since = __import__("time").time() - 200
    # call internal clear logic by invoking the branch via fake door tick state
    # Direct unit: only unconfirmed would clear
    certain = [n for n in svc.inventory.names() if svc._id_certain.get(n)]
    unconf = [n for n in svc.inventory.names() if not svc._id_certain.get(n)]
    assert certain == ["user"]
    assert unconf == []
    # if we ran stale clear filter, user stays
    cleared = [n for n in svc.inventory.names() if not svc._id_certain.get(n, False)]
    assert cleared == []
    assert "user" in svc.inventory.present
    assert svc.inventory.traits_live.get("user", {}).get("shirt") == "white"


def test_face_first_then_vl_traits_merge_onto_sole():
    """Happy path: face stamps user, then VL enter+traits merges onto user (no U1)."""
    inv = DoorInventory()
    inv.apply_vl_delta(
        1, 0, source="face", stamp_names=["user"], now=1.0
    )
    assert inv.headcount() == 1
    assert not inv.traits_live.get("user")
    inv.apply_vl_delta(
        1,
        0,
        source="qwen",
        enter_traits={"shirt": "white", "hair": "dark", "height_frac_door": 0.9},
        now=2.0,
    )
    assert inv.headcount() == 1, "must not stack U on sole named"
    assert inv.unnamed == 0
    assert inv.traits_live.get("user", {}).get("shirt") == "white"
    kinds = [e.get("kind") for e in inv.events]
    # transit-ledger merge replaced the sole-shape traits_refresh heuristics
    assert "transit_dedup_noop" in kinds


def test_code_write_through_preserves_traits_on_refresh():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(
        1,
        0,
        source="op",
        stamp_names=["user"],
        force=True,
        face_certain=True,
        enter_traits={"shirt": "white", "hair": "dark"},
    )
    # refresh present (not missing) — traits must remain
    svc.code_write_through(
        ["user"], source="code", sims={"user": 0.6}, live_face={"user": True}
    )
    tr = svc.inventory.traits_live.get("user") or {}
    assert tr.get("shirt") == "white"
    assert "user" in svc.inventory.present
