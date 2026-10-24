"""2c and 2d on the ear's side (the User 2026-10-11, .101 round 9): presence's fixed-camera slot gives the stamp
sole_present, and the fixed camera's live face block lets "the face decides" (HOUSE_ORACLE_FACE_WINS, on in a pack)
make the turn the User's; identity and security still demand this-turn voice. Checkpoint
docs/checkpoints/voice-id-101-20261011/README.md."""
from __future__ import annotations

import time

import pytest

import crissa_bridge.user_mode as um
from crissa_bridge import settings as bridge_settings
from crissa_ear.stamp import stamp_turn

MODE = {"mode": "user_auth_only", "auth": {"user_id": "user"}, "guest": "polite"}
WEAK_VOICE = {"source": None, "voice_emb_sim": 0.42, "person_id": None}        # .101: under the 0.50 line


def _fixed_presence(sim=0.71, age=3.5, n=1, name="user"):
    """What presence publishes on a fixed camera (crissa_presence.fixed_cam): the oracle's look as the face block."""
    face = {"fixed": True, "source": "fixed_look", "age_s": age, "n_faces": n, "locked": name if n == 1 else None,
            "faces": [{"name": name, "sim": sim}]}
    return {"face": face, "user": {"user_id": "user"}}


@pytest.fixture(autouse=True)
def _mode(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda *a, **k: MODE)
    monkeypatch.setattr(um, "ORACLE_FACE_WINS", True)


def test_2d_the_rule_is_on_in_a_pack():
    env = {}
    bridge_settings.apply(env)
    assert env["HOUSE_ORACLE_FACE_WINS"] == "1"


def test_2d_a_fixed_cameras_live_user_face_decides_the_turn():
    r = um.evaluate_turn(_fixed_presence(), WEAK_VOICE)
    assert r["level"] == "bau" and r["oracle_face"] is True and r["tools_ok"] and r["tier1_ok"]
    assert not r["tier2_ok"], "identity and security still demand this-turn voice"


def test_2d_the_fixed_window_is_the_seats_not_forever():
    assert um.FIXED_FACE_AGE_S > um.LIVE_FACE_AGE_S
    stale = um.evaluate_turn(_fixed_presence(age=um.FIXED_FACE_AGE_S + 0.5), WEAK_VOICE)
    assert stale["oracle_face"] is False and stale["level"] != "bau"
    moving = _fixed_presence(age=3.5)
    moving["face"].pop("fixed")                         # the same age from a camera that moves: the visor's 2.5 s
    assert um.evaluate_turn(moving, WEAK_VOICE)["oracle_face"] is False


def test_2d_a_weak_face_or_a_stranger_does_not_decide():
    assert um.evaluate_turn(_fixed_presence(sim=0.55), WEAK_VOICE)["oracle_face"] is False
    r = um.evaluate_turn(_fixed_presence(name="xena"), WEAK_VOICE)
    assert r["oracle_face"] is False and r["level"] != "bau"


def _slot_presence(**k):
    now = time.time()
    slot = {"slot_id": "s0", "name": "user", "certain": True, "id_status": "confirmed",
            "sources": {"fixed_cam_sole": True, "face_sim": 0.71, "last_seen": now}}
    slot.update(k)
    return {"headcount": 1, "count_certain": False, "count_trust": "fact", "people": [slot]}


def test_2c_the_fixed_slot_is_sole_present_for_the_stamp():
    st = stamp_turn("what's the weather", _slot_presence())
    assert st["person_id"] == "user" and st["source"] == "sole_present" and st["certain"] is True


def test_2c_only_the_flagged_slot_counts(monkeypatch):
    plain = _slot_presence(sources={"face_sim": 0.71, "last_seen": time.time()})      # the occupancy mill's slot
    assert stamp_turn("what's the weather", plain)["source"] != "sole_present"
    monkeypatch.setenv("RAIL_FIXED_CAM_SOLE_STAMP", "0")
    assert stamp_turn("what's the weather", _slot_presence())["source"] != "sole_present"
