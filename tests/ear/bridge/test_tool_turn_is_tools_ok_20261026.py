"""The bridge's hard tool lock keys on tools_ok, not the raw voice flag (the User 2026-10-26, Security On under Secure:
the ear granted tools_ok but crissa_reply was handed user_voice alone, so tool_choice stayed none)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_dispatch_hands_crissa_reply_the_tool_turn():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
    assert 'user_voice=bool(ev.get("user_voice") or ev.get("tools_ok")),' in src
    assert src.count('user_voice=bool(ev.get("user_voice")),') == 0
