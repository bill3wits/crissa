"""Host unlock → AUTH A; tools still need User voice (B)."""
from __future__ import annotations

import crissa_bridge.user_mode as um
from crissa_bridge.host_session import host_unlocked, unlock_from_presence


MODE = {
    "mode": "user_auth_only",
    "auth": {"user_id": "user"},
    "guest": "polite",
}


def _ev(monkeypatch, pres, stp):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    return um.evaluate_turn(pres, stp, person_id=stp.get("person_id"))


def test_request_ensure_skips_when_host_locked(monkeypatch):
    from crissa_bridge.ear_bridge import request_ensure_user_auth

    posted = []
    monkeypatch.setattr(
        "crissa_bridge.ear_bridge._post_json",
        lambda *a, **k: posted.append(a) or {"auth": True},
    )
    out = request_ensure_user_auth({"auth_reason": "host_locked", "user_id": "user"})
    assert posted == []
    assert out["auth"] is False and out["reason"] == "host_locked"


def test_unlock_from_presence_fields():
    assert unlock_from_presence({"host_unlocked": True}) is True
    assert unlock_from_presence({"user": {"session_unlocked": False}}) is False
    assert unlock_from_presence({}) is None


def test_unlocked_does_not_grant_a_without_face(monkeypatch):
    """Unlock is a Face-grab trigger, not AUTH A (Phase I)."""
    pres = {
        "lights_out": False,
        "host_unlocked": True,
        "people": [],
        "auth": {"authed_ids": []},
        "user": {"user_id": "user", "auth": False, "user_face_sticky": False},
    }
    st = {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.65}
    ev = _ev(monkeypatch, pres, st)
    assert ev["host_unlocked"] is True
    assert ev["user_present"] is False
    assert ev["auth"] is False
    assert ev["tools_ok"] is False
    assert ev["auth_reason"] == "user_absent"


def test_unlocked_without_sticky_no_tools(monkeypatch):
    """Session_held + unlock without sticky is not a face."""
    pres = {
        "lights_out": False,
        "host_unlocked": True,
        "user": {
            "user_id": "user",
            "auth": True,
            "session_held": True,
            "host_unlocked": True,
            "user_face_sticky": False,
            "strong_voice_age_s": 20.0,
        },
    }
    st = {
        "person_id": "second",
        "source": "voice_emb",
        "voice_emb_sim": 0.9,
    }
    ev = _ev(monkeypatch, pres, st)
    assert ev["user_present"] is False
    assert ev["tools_ok"] is False
    assert ev["auth"] is False


def test_lock_cleared_presence_is_existing_guest(monkeypatch):
    """After presence drops sticky on lock, ear guest is the not-A path.

    Do not add a second guest dispatcher for host lock.
    """
    pres = {
        "lights_out": False,
        "host_unlocked": False,
        "headcount": 2,
        "auth": {"authed_ids": [], "soft_ids": ["user"]},
        "people": [
            {
                "name": "user",
                "certain": True,
                "auth": {
                    "level": "none",
                    "authed": False,
                    "sticky": False,
                    "sources": {"face": False, "face_sticky": False},
                },
                "sources": {"face": False, "face_sim": 0.67},
            }
        ],
        "user": {
            "user_id": "user",
            "auth": False,
            "user_face_sticky": False,
            "session_held": False,
            "host_unlocked": False,
            "granted_by": None,
            "auth_reason": "host_locked",
        },
    }
    st = {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.7}
    ev = _ev(monkeypatch, pres, st)
    assert ev["auth"] is False
    assert ev["tools_ok"] is False
    assert ev["level"] == "guest"
    assert ev["bau"] is False
    assert ev["auth_reason"] == "host_locked"


def test_locked_no_face_not_a(monkeypatch):
    """Locked host + no face sticky → A false even with strong emb claim."""
    pres = {
        "lights_out": False,
        "host_unlocked": False,
        "people": [],
        "auth": {"authed_ids": []},
        "user": {
            "user_id": "user",
            "auth": False,
            "user_face_sticky": False,
            "session_held": False,
            "host_unlocked": False,
        },
    }
    st = {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.7}
    ev = _ev(monkeypatch, pres, st)
    assert ev["user_present"] is False
    assert ev["auth"] is False
    assert ev["tools_ok"] is False
    assert ev["level"] in ("guest", "silent")
    assert ev["auth_reason"] == "user_absent"


def test_env_unlock_override(monkeypatch):
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "1")
    assert host_unlocked({}) is True
    monkeypatch.setenv("HOUSE_HOST_UNLOCKED", "0")
    assert host_unlocked({}) is False
    monkeypatch.delenv("HOUSE_HOST_UNLOCKED", raising=False)


def test_face_sticky_plus_user_voice_tools(monkeypatch):
    pres = {
        "lights_out": False,
        "face": {
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.74}],
            "n_faces": 1,
            "source": "visor",
            "age_s": 0.4,
        },
        "user": {
            "user_id": "user",
            "auth": True,
            "user_face_sticky": True,
            "user_face_sim": 0.7,
            "session_held": True,
        },
        "auth": {"authed_ids": ["user"]},
    }
    st = {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.6}
    ev = _ev(monkeypatch, pres, st)
    assert ev["tools_ok"] is True
    assert ev["bau"] is True


def test_session_held_alone_no_tools(monkeypatch):
    """Weak emb while session held: chat BAU ok, tools_ok false."""
    pres = {
        "lights_out": False,
        "face": {
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.74}],
            "n_faces": 1,
            "source": "visor",
            "age_s": 0.4,
        },
        "user": {
            "user_id": "user",
            "auth": True,
            "user_face_sticky": True,
            "session_held": True,
            "strong_voice_age_s": 30.0,
            "user_face_sim": 0.7,
        },
        "auth": {"authed_ids": ["user"]},
    }
    st = {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.16}
    ev = _ev(monkeypatch, pres, st)
    assert ev["level"] == "bau"
    assert ev["auth_reason"] == "session_held"
    assert ev["tools_ok"] is False
    assert ev["tier1_ok"] is True  # recent strong voice age for house cmd re-prove path
    assert ev["tier2_ok"] is False
