"""Register takes only its own keys from a page (the User 2026-10-04, Init v2)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import enroll_payload as E  # noqa: E402

DEFAULT = {"mode": "voice-register", "station": "desk"}


def test_every_real_caller_passes_unchanged():
    wall = {"mode": "voice-register", "display": "Alex", "person_id": "alex", "station": "desk", "create": True,
            "first_boot": True}
    assert E.clean(wall, DEFAULT) == (wall, "")
    panel = {"mode": "voice-register", "display": "Alex", "person_id": "alex", "station": "desk", "create": True}
    assert E.clean(panel, DEFAULT) == (panel, "")


def test_no_payload_is_the_servers_default():
    assert E.clean(None, DEFAULT) == (DEFAULT, "")


def test_another_mode_is_refused():
    for mode in ("face", "voice", "full"):
        out, why = E.clean({"mode": mode, "display": "Eve"}, DEFAULT)
        assert out is None and "voice-register" in why


def test_extra_keys_are_dropped():
    out, _ = E.clean({"mode": "voice-register", "display": "Eve", "replace": True, "tier": "identity"}, DEFAULT)
    assert "replace" not in out and "tier" not in out and out["display"] == "Eve"


def test_kill_switch_forwards_as_sent(monkeypatch):
    monkeypatch.setenv("HOUSE_ENROLL_PAYLOAD_WHITELIST", "0")
    raw = {"mode": "full", "replace": True}
    assert E.clean(raw, DEFAULT) == (raw, "")


def test_the_server_uses_it_before_the_coach_is_called():
    src = (ROOT / "scripts" / "voiceonly-home-server.py").read_text()
    i = src.index("enroll_payload.clean(body.get(\"payload\"), target[1])")
    assert i < src.index("url, data=json.dumps(payload).encode()", i)
