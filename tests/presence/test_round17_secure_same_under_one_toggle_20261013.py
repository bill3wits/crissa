"""Round 17 follow-ups (the User 2026-10-13: "Today's Secure behaviour must be byte-for-byte the same once selected"):
the visit lamp's own tests (tests/presence/test_visit_lamp.py, the Secure flag, the click, the tripwire, the password
hold, the release, AUTH held), run again as a PACK on a camera that turns with Security On - once with the one toggle's
kill (CRISSA_SECURITY_ONE_TOGGLE=0, round 17 before it) and once with the toggle on. Each must give the same answer: the
toggle only writes the flag's edges back to <state home>/security.json and never moves the flag itself. (On the house
the same tests run as they always did, in test_visit_lamp.py; this file is the pack's half.)"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "lib"))   # after the package: lib has a crissa_presence.py

from test_visit_lamp import *  # noqa: E402,F401,F403  the visit lamp's own tests, collected here under the fixture below
from crissa_presence import fixed_cam  # noqa: E402
import crissa_home as H  # noqa: E402

TOGGLE_SEEN: dict = {}


@pytest.fixture(autouse=True, params=["round17", "one_toggle"])
def as_a_pack_with_security_on(request, tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("sec-home")
    monkeypatch.setattr(H, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(H, "HOME", home)
    monkeypatch.setattr(fixed_cam, "kind_path", lambda h=None: home / "camera-kind.json")
    (home / "camera-kind.json").write_text(json.dumps({"kind": "motor", "fixed": False}))
    (home / "security.json").write_text(json.dumps({"mode": "on"}))
    for k in ("RAIL_CAMERA_FIXED", "RAIL_FIXED_CAM_SECURE", "CRISSA_NO_CAMERA"):
        monkeypatch.delenv(k, raising=False)
    if request.param == "round17":
        monkeypatch.setenv("CRISSA_SECURITY_ONE_TOGGLE", "0")
    else:
        monkeypatch.delenv("CRISSA_SECURITY_ONE_TOGGLE", raising=False)
    H._SEC_CACHE.clear()
    yield request.param
    H._SEC_CACHE.clear()
    if request.param == "one_toggle":
        TOGGLE_SEEN[request.node.name] = H.security_record(home).get("mode")


def test_zz_the_toggle_followed_the_flag_somewhere():
    """The toggle moved with presence's flag in the runs above (a Secure test that raised it wrote secure or
    double_secure; a drop wrote on) - it was live, not dormant."""
    seen = {v for v in TOGGLE_SEEN.values() if v}
    assert seen & {"secure", "double_secure"}, seen
