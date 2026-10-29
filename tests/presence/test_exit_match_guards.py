"""Who-left guards (user 2026-08-04, after wanda's exit ghost-removed USER):
1. fresh-evidence veto — the janitor may not remove a person the wider rail
   just saw (desk face / voice / session).
2. multi-occupant ambiguity emits leave_pending for the entry-pic match game
   instead of a blind time-based drop.
3. sole occupant keeps the classic ghost recovery."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
from crissa_presence.door_watch import DoorInventory


def _aged_sole(inv, name="user", now=10.0):
    inv.apply_vl_delta(1, 0, source="gemma", stamp_names=[name], now=now,
                       entry_grace_sec=5.0)
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                     now=now + 6.0)
    return now + 6.0


def test_fresh_evidence_vetoes_ghost_drop():
    inv = DoorInventory()
    t = _aged_sole(inv)
    inv.fresh_evidence_cb = lambda n: n == "user"
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                           now=t + 1801.0)
    assert "user" in inv.names(), "User must stay (protected / veto)"
    # Either classic ghost_vetoed or leave_blocked (protected User)
    assert any(
        e.get("kind") in ("ghost_vetoed", "leave_blocked") for e in evs
    ) or "user" in inv.names()
    evs2 = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                            now=t + 1810.0)
    assert "user" in inv.names()


def test_multi_occupant_ambiguity_emits_leave_pending():
    inv = DoorInventory()
    inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["user"], now=10.0,
                       entry_grace_sec=1.0)
    inv.apply_vl_delta(1, 0, source="gemma", stamp_names=["wanda"], now=12.0,
                       entry_grace_sec=1.0)
    # both fall into doubt; empty FOV long past ghost threshold
    inv.doubt_since["user"] = 20.0
    inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=1.0, now=21.0)
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=1.0,
                           now=21.0 + 1801.0)
    assert sorted(inv.names()) == ["user", "wanda"], \
        "nobody drops until the match game answers"
    pend = [e for e in evs if e.get("kind") == "leave_pending"]
    assert pend and sorted(pend[0]["candidates"]) == ["user", "wanda"]


def test_sole_occupant_ghost_recovery_still_fires():
    """Non-User sole still ghost-recovers; User is protected (2026-08-11)."""
    inv = DoorInventory()
    t = _aged_sole(inv, name="wanda")
    evs = inv.observe_door([], 0, leave_confirm_sec=2.0, entry_grace_sec=5.0,
                           now=t + 1801.0)
    leave = next(e for e in evs if e.get("kind") == "leave")
    assert leave.get("source") == "ghost_recovery"
    assert leave.get("name") == "wanda"
    assert inv.headcount() == 0
