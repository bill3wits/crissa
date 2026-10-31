"""Multi-voter VL delta dedup on DoorInventory SoT.

The bodies here carry no name, so the count is the inventory's (the SoT the arithmetic writes): the published
headcount is named occupancy only since the rail's 588400a ("Unnamed slots do not count", 2026-08-25) - an unnamed
body is a U-slot on the people list, never a count vote."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.service import PresenceService, VL_DEDUP_SEC


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def test_same_sign_dedup_noop():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="gemma")
    assert svc.inventory.headcount() == 1
    st2 = svc.apply_vl_delta(1, 0, source="qwen")  # same sign, within window
    assert svc.inventory.headcount() == 1
    assert (st2.last_event or {}).get("kind") == "vl_delta_deduped"
    assert (st2.last_event or {}).get("action") == "same_sign_noop"


def test_opposite_sign_no_thrash():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="gemma")
    st = svc.apply_vl_delta(0, 1, source="cosmos")  # opposite within window
    assert svc.inventory.headcount() == 1  # did not apply leave
    assert (st.last_event or {}).get("kind") == "vl_delta_conflict"
    assert st.count_trust == "stale"


def test_force_bypasses_dedup():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="gemma")
    st = svc.apply_vl_delta(1, 0, source="qwen", force=True)
    assert svc.inventory.headcount() == 2
    assert (st.last_event or {}).get("kind") == "vl_delta"


def test_null_delta_no_op():
    svc = PresenceService(camera=_FakeCam())
    svc.apply_vl_delta(1, 0, source="gemma", force=True)
    st = svc.apply_vl_delta(0, 0, source="qwen")
    assert svc.inventory.headcount() == 1
