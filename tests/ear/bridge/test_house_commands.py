"""House control phrase matching + confirm flow (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "ear"))

from crissa_bridge.house_commands import (  # noqa: E402
    _STATE,
    handle_control,
    match_control,
)


def test_match_hybrid():
    m = match_control("hybrid mode")
    assert m and m["cmd"] == "hybrid-mode"


def test_match_voice_only():
    assert match_control("voice-only")["cmd"] == "voice-only"
    assert match_control("voice only please")["cmd"] == "voice-only"


def test_match_voice_on_off_and_hey_crissa_residual():
    assert match_control("voice on")["cmd"] == "voice-on"
    assert match_control("hey crissa voice on")["cmd"] == "voice-on"
    assert match_control("hey crissa, voice off")["cmd"] == "voice-off"
    from crissa_bridge.wake import wake_match

    residual = wake_match("Hey Crissa, voice on")["residual"]
    assert match_control(residual)["cmd"] == "voice-on"


def test_match_system_off():
    assert match_control("system off")["cmd"] == "system-off"
    assert match_control("shut down")["cmd"] == "system-off"


def test_match_confirm_cancel():
    assert match_control("confirm")["cmd"] == "confirm"
    assert match_control("cancel")["cmd"] == "cancel"


def test_cancel_idle_not_handled():
    spoken = []
    with _STATE.lock:
        _STATE.phase = "idle"
        _STATE.action = None
        if _STATE.timer:
            _STATE.timer.cancel()
            _STATE.timer = None
    r = handle_control("cancel", meta={}, presence={}, speak=spoken.append)
    assert r["handled"] is False


def test_system_needs_auth():
    spoken = []
    r = handle_control(
        "system-off",
        meta={"person_id": "u1"},
        presence={"people": [], "auth": {"authed_ids": [], "soft_ids": []}},
        speak=spoken.append,
    )
    assert r["handled"] is True
    assert r.get("denied") == "auth"
    assert spoken and "can't help" in spoken[0].lower()


def test_system_confirm_countdown_cancel():
    spoken = []
    meta = {
        "person_id": "user",
        "stamp": {
            "certain": True,
            "person_id": "user",
            "source": "voice_emb",
            "voice_emb_sim": 0.7,
        },
    }
    presence = {
        "people": [
            {
                "name": "user",
                "certain": True,
                "auth": {"level": "face", "authed": True},
                "sources": {"face_sim": 0.7},
            }
        ],
        "auth": {"authed_ids": ["user"], "soft_ids": []},
        "user_id": "user",
        "user_auth": True,
        "user_face_sticky": True,
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
            "user_face_sim": 0.7,
        },
    }
    # reset state
    with _STATE.lock:
        _STATE.phase = "idle"
        _STATE.action = None
        if _STATE.timer:
            _STATE.timer.cancel()
            _STATE.timer = None

    r1 = handle_control("system-off", meta=meta, presence=presence, speak=spoken.append)
    assert r1.get("phase") == "await_confirm"
    assert any("confirm" in s.lower() for s in spoken)

    r2 = handle_control("confirm", meta=meta, presence=presence, speak=spoken.append)
    assert r2.get("phase") == "countdown"
    assert any("seconds" in s.lower() or "cancel" in s.lower() for s in spoken)

    r3 = handle_control("cancel", meta=meta, presence=presence, speak=spoken.append)
    assert r3.get("cmd") == "cancel"
    assert _STATE.phase == "idle"
