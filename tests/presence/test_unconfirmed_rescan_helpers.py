"""Unit-level checks for unconfirmed tracking (ghost rescan gate)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.door_watch import DoorInventory
from crissa_presence.service import PresenceService


def test_unconfirmed_sets_without_full_or_gender():
    svc = PresenceService.__new__(PresenceService)
    svc.inventory = DoorInventory()
    svc._unconfirmed_since = {}
    svc.inventory.set_baseline(["wanda"], 0)
    now = time.time()
    # age past entry grace
    svc.inventory.entry_grace_until.clear()
    stamps = {"wanda": {"gender": "F"}, "user": {"gender": "M"}}
    svc._update_unconfirmed(
        now=now,
        full_matches=set(),
        live_genders=set(),
        stamps=stamps,
    )
    assert "wanda" in svc._unconfirmed_since


def test_full_match_clears_unconfirmed():
    svc = PresenceService.__new__(PresenceService)
    svc.inventory = DoorInventory()
    svc._unconfirmed_since = {"wanda": time.time() - 500}
    svc.inventory.set_baseline(["wanda"], 0)
    svc.inventory.entry_grace_until.clear()
    stamps = {"wanda": {"gender": "F"}}
    svc._update_unconfirmed(
        now=time.time(),
        full_matches={"wanda"},
        live_genders=set(),
        stamps=stamps,
    )
    assert "wanda" not in svc._unconfirmed_since


def test_gender_live_clears_unconfirmed():
    svc = PresenceService.__new__(PresenceService)
    svc.inventory = DoorInventory()
    svc._unconfirmed_since = {"wanda": time.time() - 500}
    svc.inventory.set_baseline(["wanda"], 0)
    svc.inventory.entry_grace_until.clear()
    stamps = {"wanda": {"gender": "F"}}
    svc._update_unconfirmed(
        now=time.time(),
        full_matches=set(),
        live_genders={"F"},
        stamps=stamps,
    )
    assert "wanda" not in svc._unconfirmed_since


def test_should_rescan_after_threshold():
    svc = PresenceService.__new__(PresenceService)
    svc._unconfirmed_since = {"wanda": time.time() - 200}
    svc._last_rescan_at = 0.0
    assert svc._should_ghost_rescan(time.time()) is True
