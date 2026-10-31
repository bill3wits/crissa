"""CODE write-through must not resurrect recent leavers without live face."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.service import PresenceService


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def test_leave_then_memory_cannot_readd():
    svc = PresenceService(camera=_FakeCam())
    svc.assert_present(["user", "wanda"], source="test")
    assert svc.inventory.headcount() == 2
    svc.assert_leave(["wanda"], source="door_empty_after")
    assert "wanda" not in svc.inventory.names()
    # pure memory write-through (no live face) — must not resurrect
    out = svc.code_write_through(
        ["user", "wanda"],
        source="code",
        sims={"user": 0.65, "wanda": 0.61},
        live_face={"user": False, "wanda": False},
    )
    # may return None or blocked event; wanda must stay gone
    assert "wanda" not in svc.inventory.names()
    assert "user" in svc.inventory.names()


def test_live_face_can_reenter_after_leave():
    svc = PresenceService(camera=_FakeCam())
    svc.assert_present(["wanda"], source="test")
    svc.assert_leave(["wanda"], source="door")
    assert "wanda" not in svc.inventory.names()
    svc.code_write_through(
        ["wanda"],
        source="code",
        sims={"wanda": 0.62},
        live_face={"wanda": True},
    )
    assert "wanda" in svc.inventory.names()


def test_weak_live_face_does_not_new_add():
    """Poster / residual emb < 0.50 must not invent a body on empty SoT."""
    svc = PresenceService(camera=_FakeCam())
    out = svc.code_write_through(
        ["user"],
        source="code",
        sims={"user": 0.47},
        live_face={"user": True},
    )
    assert "user" not in svc.inventory.names()
    assert svc.inventory.headcount() == 0


def test_tombstone_expires():
    svc = PresenceService(camera=_FakeCam())
    svc.assert_present(["wanda"], source="test")
    svc.assert_leave(["wanda"], source="door")
    # force-expire tombstone
    svc._left_block_until["wanda"] = time.time() - 1
    # still needs live face for new add
    svc.code_write_through(
        ["wanda"],
        sims={"wanda": 0.5},
        live_face={},
    )
    assert "wanda" not in svc.inventory.names()


def test_join_while_named_allows_one_unnamed_slot():
    """wanda home + VL +1 (user joining) must increment clicker — not suppress."""
    svc = PresenceService(camera=_FakeCam())
    svc.assert_present(["wanda"], source="test")
    assert svc.inventory.headcount() == 1
    st = svc.apply_vl_delta(1, 0, source="qwen")
    assert svc.inventory.names() == ["wanda"]
    assert svc.inventory.unnamed == 1
    assert svc.inventory.headcount() == 2
    assert (st.last_event or {}).get("kind") == "vl_delta"
    # second +1 while U already open → cap (double-click noise)
    svc._vl_recent = []  # outside multi-voter window so cap (not same-sign dedup) applies
    st2 = svc.apply_vl_delta(1, 0, source="gemma")
    assert svc.inventory.unnamed == 1
    assert svc.inventory.headcount() == 2
    assert (st2.last_event or {}).get("reason") == "unnamed_slot_already"
    # face stamp absorbs U → two named
    svc.code_write_through(
        ["user"],
        source="code",
        sims={"user": 0.62},
        live_face={"user": True},
    )
    assert set(svc.inventory.names()) == {"user", "wanda"}
    assert svc.inventory.unnamed == 0
    assert svc.inventory.headcount() == 2


def test_solo_named_then_vl_adds_u_face_absorbs():
    """user alone first: VL +1 opens U; face absorb collapses (no permanent double)."""
    svc = PresenceService(camera=_FakeCam())
    svc.assert_present(["user"], source="test")
    svc.apply_vl_delta(1, 0, source="gemma")
    assert svc.inventory.headcount() == 2
    assert svc.inventory.unnamed == 1
    svc.code_write_through(
        ["user"],
        source="code",
        sims={"user": 0.7},
        live_face={"user": True},
    )
    assert svc.inventory.names() == ["user"]
    assert svc.inventory.unnamed == 0
    assert svc.inventory.headcount() == 1


def test_absorb_unnamed_when_face_already_certain():
    """VL +1 unnamed first, then CODE user certain — U must collapse (no double)."""
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="gemma")
    assert svc.inventory.unnamed == 1
    # first face add absorbs
    svc.code_write_through(
        ["user"],
        source="code",
        sims={"user": 0.7},
        live_face={"user": True},
    )
    assert "user" in svc.inventory.names()
    assert svc.inventory.unnamed == 0
    assert svc.inventory.headcount() == 1
    # phantom U reappears (keyed, in-grace — presence-perfect §2.2: only a
    # provenanced same-transit U is absorbable; a bare keyless `unnamed = 1`
    # is legacy shape with NO meta and must NOT be eaten until Phase 6.2
    # keys every mint); certain user refresh still absorbs the keyed one
    svc.inventory.apply_vl_delta(1, 0, source="gemma")
    assert svc.inventory.unnamed == 1
    svc.code_write_through(
        ["user"],
        source="code",
        sims={"user": 0.7},
        live_face={"user": True},
    )
    assert svc.inventory.unnamed == 0
    assert svc.inventory.headcount() == 1
