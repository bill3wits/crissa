"""A camera with no pan control is on its door view (2026-10-10, found by the round-3 sandbox trial: a film standing in
for a webcam read "off the door" forever, so presence's door watch never scanned and the door glance always skipped)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))


def test_a_camera_that_never_reported_a_pan_is_at_the_door(monkeypatch):
    from crissa_presence import grabber as g
    monkeypatch.setattr(g, "_PAN_SEEN", False)
    monkeypatch.delenv("RAIL_CAMERA_FIXED_AT_DOOR", raising=False)
    assert g.door_view(None, 24000) is True                       # no pan control: one view, the door's
    home = int(g.DOOR_HOME_PAN)
    assert g.door_view(home + 3600, 24000) is True                # a PTZ head near door home
    assert g.door_view(home + 100000, 24000) is False             # a PTZ head turned away
    assert g.door_view(None, 24000) is False                      # it reported a pan before: unreadable now = off
    monkeypatch.setattr(g, "_PAN_SEEN", False)
    monkeypatch.setenv("RAIL_CAMERA_FIXED_AT_DOOR", "0")
    assert g.door_view(None, 24000) is False                      # the old rule


def test_health_reads_the_door_view_through_the_rule():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence", "crissa_presence",
                            "grabber.py"), encoding="utf-8").read()
    assert "at_door = door_view(pan_i, door_tol)" in src
