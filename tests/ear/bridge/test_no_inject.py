"""Non-User cannot inject house commands / tools on session_held alone."""
from __future__ import annotations

import json

import crissa_bridge.user_mode as um
from crissa_bridge.house_commands import (
    _is_authed,
    command_may_execute,
    handle_control,
    match_control,
    try_handle_utterance,
)

MODE = {
    "mode": "user_auth_only",
    "auth": {"user_id": "user"},
    "guest": "polite",
}


def sticky_user():
    return {
        "lights_out": False,
        "auth": {"authed_ids": ["user"], "soft_ids": []},
        "face": {
            "locked": "user",
            "faces": [{"name": "user", "sim": 0.74}],
            "n_faces": 1,
            "source": "visor",
            "age_s": 0.4,
        },
        "people": [
            {
                "name": "user",
                "certain": True,
                "auth": {"level": "face", "authed": True},
                "sources": {"face_sim": 0.7},
            }
        ],
        "user_id": "user",
        "user_auth": True,
        "user_face_sticky": True,
        "user": {
            "user_id": "user",
            "auth": True,
            "user_face_sticky": True,
            "session_held": True,
            "user_face_sim": 0.71,
            "strong_voice_age_s": 40.0,
            "host_unlocked": True,
        },
        "host_unlocked": True,
    }


def _second_meta():
    return {
        "person_id": "second",
        "stamp": {
            "person_id": "second",
            "source": "voice_emb",
            "voice_emb_sim": 0.9,
            "certain": True,
        },
    }


def _user_meta():
    return {
        "person_id": "user",
        "stamp": {
            "person_id": "user",
            "source": "voice_emb",
            "voice_emb_sim": 0.65,
            "certain": True,
        },
    }


def test_second_voice_not_authed_for_system(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    meta = {
        "person_id": "second",
        "stamp": {
            "person_id": "second",
            "source": "voice_emb",
            "voice_emb_sim": 0.88,
        },
    }
    assert _is_authed(meta, sticky_user()) is False


def test_is_user_block_not_authed(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    meta = {
        "person_id": None,
        "stamp": {
            "person_id": None,
            "source": "is_user_block",
            "voice_emb_sim": 0.05,
            "is_user": False,
        },
    }
    assert _is_authed(meta, sticky_user()) is False


def test_session_held_null_pid_not_authed(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    meta = {
        "person_id": None,
        "stamp": {"person_id": None, "source": "voice_emb", "voice_emb_sim": 0.2},
    }
    assert _is_authed(meta, sticky_user()) is False


def test_user_voice_is_authed(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    meta = {
        "person_id": "user",
        "stamp": {
            "person_id": "user",
            "source": "voice_emb",
            "voice_emb_sim": 0.62,
        },
    }
    assert _is_authed(meta, sticky_user()) is True


def test_handle_system_denied_for_guest_voice(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    spoken = []
    r = handle_control(
        "system-off",
        meta={
            "person_id": "second",
            "stamp": {
                "person_id": "second",
                "source": "voice_emb",
                "voice_emb_sim": 0.9,
            },
        },
        presence=sticky_user(),
        speak=spoken.append,
    )
    assert r["handled"] is True
    assert r.get("denied") == "auth"
    assert match_control("system-off")  # phrase still matches — gate is auth


def test_evaluate_matrix_export(monkeypatch, tmp_path):
    """Drive real evaluate_turn matrix; write results for verifier."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    cases = []
    p = sticky_user()

    def run(name, stamp, pid=None):
        ev = um.evaluate_turn(p, stamp, person_id=pid if pid is not None else stamp.get("person_id"))
        row = {
            "case": name,
            "level": ev["level"],
            "auth": ev["auth"],
            "tools_ok": ev["tools_ok"],
            "bau": ev["bau"],
            "force_reprove": ev["force_reprove"],
            "auth_reason": ev["auth_reason"],
            "user_voice": ev["user_voice"],
        }
        cases.append(row)
        return row

    r1 = run(
        "face+user_voice",
        {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.65},
    )
    assert r1["tools_ok"] and r1["bau"]

    r2 = run(
        "face+not_user",
        {"person_id": "second", "source": "voice_emb", "voice_emb_sim": 0.9},
        pid="second",
    )
    assert r2["auth"] and not r2["tools_ok"] and r2["force_reprove"]

    r3 = run(
        "face+weak_session",
        {"person_id": "user", "source": "voice_emb", "voice_emb_sim": 0.15},
    )
    assert r3["level"] == "bau" and not r3["tools_ok"]

    r4 = run(
        "is_user_block",
        {
            "person_id": None,
            "source": "is_user_block",
            "voice_emb_sim": 0.04,
            "is_user": False,
        },
        pid=None,
    )
    assert not r4["tools_ok"] and r4["auth"]

    out = tmp_path / "no_inject_matrix.json"
    out.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    assert out.is_file()


def test_second_denied_hybrid_voice_surface_cmds(monkeypatch):
    """Skeptic: hybrid/voice-on/voice-off must not execute for non-User sticky."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    pres = sticky_user()
    meta = _second_meta()
    for phrase, cmd in (
        ("hybrid mode", "hybrid-mode"),
        ("voice on", "voice-on"),
        ("voice off", "voice-off"),
        ("voice only", "voice-only"),
    ):
        spoken = []
        r = handle_control(phrase, meta=meta, presence=pres, speak=spoken.append)
        assert r["handled"] is True, phrase
        assert r.get("cmd") == cmd
        assert r.get("denied") == "auth", phrase
        assert spoken and "can't help" in spoken[0].lower()
        # Must not speak success copy
        joined = " ".join(spoken).lower()
        assert "hybrid mode." not in joined
        assert "voice on." not in joined
        assert "voice off." not in joined


def test_user_allowed_hybrid(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    spoken = []
    # Avoid real HTTP: only check auth gate not denied
    r = handle_control(
        "hybrid mode",
        meta=_user_meta(),
        presence=sticky_user(),
        speak=spoken.append,
    )
    assert r.get("denied") != "auth"
    assert r["handled"] is True
    assert r.get("cmd") == "hybrid-mode"


def test_second_certain_not_authed_fail_closed(monkeypatch):
    """evaluate_turn path: certain=True + second must not legacy-grant."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    meta = _second_meta()
    assert meta["stamp"]["certain"] is True
    assert _is_authed(meta, sticky_user()) is False


def test_is_authed_eval_exception_fail_closed(monkeypatch):
    """If evaluate_turn raises under user_auth_only, deny (never legacy)."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)

    def _boom(*_a, **_k):
        raise RuntimeError("eval exploded")

    meta = {
        "person_id": "second",
        "stamp": {"person_id": "second", "certain": True, "voice_emb_sim": 0.99},
    }
    import crissa_bridge.house_commands as hc
    import crissa_bridge.user_mode as umod

    # _is_authed does `from .user_mode import evaluate_turn` each call — patch source
    monkeypatch.setattr(umod, "evaluate_turn", _boom)
    assert hc._is_authed(meta, sticky_user()) is False


def test_command_may_execute_blocks_wrong_voice_after_ensure():
    """Post-ensure re-check: face sticky + wrong_voice → no inject."""
    wrong = {
        "auth": True,
        "user_present": True,
        "user_voice": False,
        "tools_ok": False,
        "force_reprove": True,
        "auth_reason": "wrong_voice",
        "bau": False,
    }
    assert command_may_execute(wrong) is False
    ok = {
        "auth": True,
        "user_voice": True,
        "tools_ok": True,
        "force_reprove": False,
    }
    assert command_may_execute(ok) is True
    session_only = {
        "auth": True,
        "user_voice": False,
        "tools_ok": False,
        "force_reprove": False,
        "bau": True,
        "auth_reason": "session_held",
    }
    assert command_may_execute(session_only) is False


def test_try_handle_second_hybrid_no_success_speech(monkeypatch):
    """Real try_handle_utterance path — second cannot inject hybrid."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    spoken = []
    handled = try_handle_utterance(
        "hybrid mode",
        meta=_second_meta(),
        presence=sticky_user(),
        speak=spoken.append,
    )
    assert handled is True  # handled as denied, not passed to LLM
    assert any("can't help" in s.lower() for s in spoken)
    assert not any(s.strip().lower() == "hybrid mode." for s in spoken)


def test_try_handle_second_user_leave_denied(monkeypatch):
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    spoken = []
    r = handle_control(
        "I'm leaving",
        meta=_second_meta(),
        presence=sticky_user(),
        speak=spoken.append,
    )
    # Phrase may or may not match leave depending on patterns — if matched, denied
    if r.get("cmd") == "user-leave":
        assert r.get("denied") == "auth"


def test_second_cannot_confirm_armed_system(monkeypatch):
    """Skeptic: after User arms system-off, non-User confirm must not countdown."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    from crissa_bridge.house_commands import _STATE

    with _STATE.lock:
        _STATE.phase = "idle"
        _STATE.action = None
        if _STATE.timer:
            _STATE.timer.cancel()
            _STATE.timer = None

    pres = sticky_user()
    spoken = []
    # the User arms
    r1 = handle_control(
        "system-off",
        meta=_user_meta(),
        presence=pres,
        speak=spoken.append,
    )
    assert r1.get("phase") == "await_confirm"
    assert r1.get("denied") != "auth"
    assert _STATE.phase == "await_confirm"
    assert _STATE.action == "system-off"

    # Second tries confirm — must stay await_confirm, not countdown
    spoken2 = []
    r2 = handle_control(
        "confirm",
        meta=_second_meta(),
        presence=pres,
        speak=spoken2.append,
    )
    assert r2.get("cmd") == "confirm"
    assert r2.get("denied") == "auth"
    assert r2.get("phase") == "await_confirm"
    assert _STATE.phase == "await_confirm"
    assert _STATE.action == "system-off"
    assert not any("seconds" in s.lower() for s in spoken2)
    assert any("can't help" in s.lower() for s in spoken2)

    # the User can still confirm
    spoken3 = []
    r3 = handle_control(
        "confirm",
        meta=_user_meta(),
        presence=pres,
        speak=spoken3.append,
    )
    assert r3.get("denied") != "auth"
    assert r3.get("phase") == "countdown"
    assert _STATE.phase == "countdown"

    # cleanup
    handle_control("cancel", meta=_user_meta(), presence=pres, speak=lambda t: None)
    assert _STATE.phase == "idle"


def test_command_may_execute_blocks_confirm_after_wrong_voice_eval(monkeypatch):
    """Free-air path gate: evaluate_turn wrong_voice ⇒ no command_may_execute."""
    monkeypatch.setattr(um, "load_house_mode", lambda: MODE)
    ev = um.evaluate_turn(
        sticky_user(),
        _second_meta()["stamp"],
        person_id="second",
    )
    assert ev["force_reprove"] is True
    assert command_may_execute(ev) is False
