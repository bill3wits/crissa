"""The camera lease is one token on a ladder (camera_token.py) — pure rules.

user 2026-09-07: "Camera lease won't yield … make the lease a token they pass
around" (the Oracle joystick lost to a mid-dwell look)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import camera_token as ct  # noqa: E402


def test_the_ladder_puts_the_user_first_then_the_secure_watch_then_the_chase():
    assert ct.LADDER[0] == "user_joystick"
    assert ct.LADDER.index("secure_glance") < ct.LADDER.index("ff_mode") < ct.LADDER.index("beam_look")
    assert ct.outranks("user_joystick", "beam_look")
    assert ct.outranks("user_joystick", "ff_mode")
    assert ct.outranks("secure_glance", "ff_mode"), "Law 2: under Secure the door watch beats the chase"
    assert ct.outranks("ff_mode", "beam_look")
    assert ct.outranks("ff_mode", "f2f")
    assert not ct.outranks("beam_look", "ff_mode")
    assert not ct.outranks("beam_look", "beam_look")
    # external holders (inspector, a tool pause) are not ours: nobody outranks them
    assert not ct.outranks("user_joystick", "inspector")
    assert not ct.is_ours("inspector") and ct.is_ours("door_mill")


def test_grant_on_empty_or_expired_renew_for_the_holder():
    now = 1000.0
    assert ct.decide("beam_look", None, now)["act"] == "grant"
    old = {"owner": "beam_look", "lease_id": "L1", "until": now - 1}
    assert ct.expired(old, now) and ct.decide("ff_mode", old, now)["act"] == "grant"
    live = {"owner": "beam_look", "lease_id": "L1", "until": now + 30}
    d = ct.decide("beam_look", live, now)
    assert d["act"] == "renew" and d["holder"] == "beam_look"


def test_a_higher_asker_takes_the_token_a_lower_or_equal_one_is_refused_by_name():
    now = 1000.0
    live = {"owner": "beam_look", "lease_id": "L1", "until": now + 30}
    assert ct.decide("user_joystick", live, now)["act"] == "handoff"
    assert ct.decide("ff_mode", live, now)["act"] == "handoff"
    assert ct.decide("secure_glance", {"owner": "ff_mode", "lease_id": "L2", "until": now + 30}, now)["act"] == "handoff"
    d = ct.decide("f2f", live, now)
    assert d["act"] == "refuse" and d["holder"] == "beam_look"
    joy = {"owner": "user_joystick", "lease_id": "L3", "until": now + 300}
    for asker in ("ff_mode", "beam_look", "secure_glance", "door_mill"):
        d = ct.decide(asker, joy, now)
        assert d["act"] == "refuse" and d["holder"] == "user_joystick", asker


def test_an_external_holder_is_refused_unless_the_joystick_steals():
    now = 1000.0
    ext = {"owner": "inspector", "lease_id": "L9", "until": now + 30}
    assert ct.decide("ff_mode", ext, now)["act"] == "refuse"
    assert ct.decide("user_joystick", ext, now)["act"] == "refuse"
    assert ct.decide("user_joystick", ext, now, steal=True)["act"] == "steal"
    assert ct.decide("ff_mode", ext, now, steal=True)["act"] == "refuse", "only the User is the arbiter"
