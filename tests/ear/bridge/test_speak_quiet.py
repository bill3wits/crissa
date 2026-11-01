"""2s mouth-start quiet; LLM still predicts. No live engines."""
from pathlib import Path

from crissa_bridge.ear_bridge import (
    SPEAK_QUIET_SEC,
    coalesce_followup,
    remainder_revise_user,
)


def test_speak_quiet_default_is_two_seconds():
    assert SPEAK_QUIET_SEC == 2.0


def test_speak_passes_full_report_via_text_file():
    """512 tok + text[:500] chopped spoken reports. 4096 + --text-file is the mouth."""
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text()
    speak = src.split("def speak_blocking(")[1].split("\ndef wake_ack_name")[0]
    assert 'text[:500]' not in speak
    assert "--text-file" in speak
    assert "SPEAK_CHARS" in speak
    # round 15: the 4096 reply budget is the talk dial reply.max_tokens; EAR_BRIDGE_MAX_TOKENS still overrides it
    from crissa_bridge import ear_bridge as eb
    assert eb.reply_budget(cfg={}) == 4096 and eb.reply_budget(cfg={"reply": {"max_tokens": 4096}}) == 4096
    assert 'os.environ.get("EAR_BRIDGE_MAX_TOKENS", "")' in src


def test_mouth_wait_is_speak_quiet_not_flush_fast():
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text()
    wait = src.split("def _wait_speak_quiet")[1].split("\ndef speak_blocking")[0]
    assert "SPEAK_QUIET_SEC" in wait
    speak = src.split("def speak_blocking(")[1].split("\ndef wake_ack_name")[0]
    assert "_wait_speak_quiet(token)" in speak
    assert "quiet_need = FLUSH_FAST" not in speak
    kick = src.split("def _kick_clause")[1].split("def _one_reply")[0]
    assert "_wait_speak_quiet(token)" in kick
    assert "ttfs_held" in kick
    assert "skip_quiet=True" in kick


def test_coalesce_appends_continuation_and_vocative_replaces():
    old = ("that's not working now, so", {"kind": "session"})
    joined, meta = coalesce_followup(
        old, "see the feed", {"kind": "session", "person_id": "user"}, vocative=False
    )
    assert joined == "that's not working now, so see the feed"
    assert meta["person_id"] == "user"
    fresh, _ = coalesce_followup(old, "Hey Crissa, stop", {}, vocative=True)
    assert fresh == "Hey Crissa, stop"
    first, _ = coalesce_followup(None, "hello", {}, vocative=False)
    assert first == "hello"


def test_remainder_revise_user_names_spoken_and_continuation():
    blob = remainder_revise_user(
        "You want strategy on the feed.",
        "I was talking about an intelligent switch.",
        "Which feed should I look at?",
    )
    assert "Already said aloud" in blob
    assert "You want strategy on the feed." in blob
    assert "intelligent switch" in blob
    assert "Which feed" in blob
    assert "corrected remainder" in blob.lower()


def test_dispatch_revises_before_second_clause():
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text()
    body = src.split("def dispatch(")[1].split("\ndef _drop_pending")[0]
    assert "predict_revise" in body
    assert "remainder_revise" in body
    assert "crissa_revise_remainder" in body
    assert "keep TEN VAD" in src or "SPEAK_QUIET_SEC" in src
    coalesce = src.split('event": "coalesce"')[1][:280]
    assert "append" in coalesce
