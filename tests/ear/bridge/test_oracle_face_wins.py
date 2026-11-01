"""The Oracle rule (the User 2026-10-01): when the camera sees and matches the User NOW, a low voice score never
drops him to guest; voice decides only when the camera can't. Behind HOUSE_ORACLE_FACE_WINS, OFF by default.
The case is 10-01 11:54:53: live face 0.615, voice 0.401 (her reply's tail in his turn) -> guest under the law."""
from __future__ import annotations

import pytest

import crissa_bridge.user_mode as um

MODE = {"mode": "user_auth_only", "auth": {"user_id": "user"}, "guest": "polite"}
STAMP_1154 = {"source": "is_user_block", "voice_emb_sim": 0.401, "person_id": None}


def _presence(sim=0.615, age=1.0, pending=False, faces=True):
    buf = {"locked": "user" if faces else "", "age_s": age, "source": "visor", "n_faces": 1 if faces else 0,
           "faces": [{"name": "user", "sim": sim}] if faces else []}
    if pending:
        buf["pending_look"] = True
    return {"face": buf, "user": {"user_id": "user"}}


@pytest.fixture(autouse=True)
def _mode(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda *a, **k: MODE)


def test_off_by_default_the_1154_turn_is_guest(monkeypatch):
    assert um.ORACLE_FACE_WINS is False
    r = um.evaluate_turn(_presence(), STAMP_1154)
    assert r["level"] == "guest" and r["auth_reason"].startswith("not_user_voice") and not r["tools_ok"]
    assert r["oracle_face"] is False


def test_on_a_live_face_match_decides(monkeypatch):
    monkeypatch.setattr(um, "ORACLE_FACE_WINS", True)
    r = um.evaluate_turn(_presence(), STAMP_1154)
    assert r["level"] == "bau" and r["auth_reason"] == "oracle_face" and r["oracle_face"]
    assert r["tools_ok"] and r["tier1_ok"], "house commands ride the live face"
    assert not r["tier2_ok"], "security / identity actions still demand this-turn voice"


@pytest.mark.parametrize("why,kw", [
    ("below the camera's 0.60 bar", {"sim": 0.55}),
    ("stale scan - the camera cannot see him now", {"age": 999.0}),
    ("a look in flight is not a match", {"pending": True, "faces": False}),
])
def test_on_voice_still_decides_when_the_camera_cannot(monkeypatch, why, kw):
    monkeypatch.setattr(um, "ORACLE_FACE_WINS", True)
    r = um.evaluate_turn(_presence(**kw), STAMP_1154)
    assert not r["oracle_face"], why
    assert r["level"] != "bau" or r["auth_reason"] != "oracle_face", why


def test_on_his_own_voice_is_still_ok(monkeypatch):
    monkeypatch.setattr(um, "ORACLE_FACE_WINS", True)
    r = um.evaluate_turn(_presence(), {"source": "voice_emb", "voice_emb_sim": 0.55, "person_id": "user"})
    assert r["level"] == "bau" and r["auth_reason"] == "ok"
