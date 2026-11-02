"""Round 26 (.101 10-16): her question waits for the answer (the session closed 20 s after "does that sound right?"
with his "yes" unheard), and a long sentence is not cut at 12 s ("...can you tell me the ")."""
from __future__ import annotations

from pathlib import Path

from crissa_bridge import ear_bridge as B

ROOT = Path(__file__).resolve().parents[2]


def test_a_question_keeps_the_session_longer(monkeypatch):
    monkeypatch.setattr(B, "SESSION_SEC", 20.0)
    monkeypatch.setattr(B, "QUESTION_SEC", 45.0)
    assert B.after_reply_sec("Looks like you're near Charlotte, North Carolina, does that sound right?", False) == 45.0
    assert B.after_reply_sec('She said "is it?"', False) == 45.0
    assert B.after_reply_sec("It's 70 and sunny.", False) == 20.0
    assert B.after_reply_sec("Anything else?", True) == B.COMMS_HOLD_SEC          # a hold is longer still
    monkeypatch.setattr(B, "QUESTION_SEC", 0.0)
    assert B.after_reply_sec("Right?", False) == 20.0


def test_both_reply_ends_use_it():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert src.count('time.time() + after_reply_sec(said or reply,') == 2


def test_an_open_with_no_length_takes_the_ears_own_cap():
    src = (ROOT / "modules" / "ear" / "crissa_ear" / "server.py").read_text()
    blk = src.split("def _post_ear_open", 1)[1].split("def _post_omni_note", 1)[0]
    assert 'obj.get("chunk_sec", obj.get("seconds", 12))' not in blk
    assert 'raw_chunk = obj.get("chunk_sec", obj.get("seconds"))' in blk and "CRISSA_EAR_OPEN_DEFAULT_12" in blk


def test_the_loop_reads_none_as_the_setting(monkeypatch):
    import inspect
    from crissa_ear import ear_loop
    src = inspect.getsource(ear_loop.EarLoop.open) if hasattr(ear_loop, "EarLoop") else \
        (ROOT / "modules" / "ear" / "crissa_ear" / "ear_loop.py").read_text()
    assert "if chunk_sec else default_mu" in src


def test_an_unanswered_question_gets_one_line_at_the_sessions_end(monkeypatch):
    monkeypatch.setattr(B, "NO_REPLY_ON", True)
    said = []
    B.after_reply_sec("What can we do today?", False)
    line = B.say_no_reply("Crissa", speak=said.append)
    import time
    time.sleep(0.05)
    assert line and said == [line] and "Crissa" not in line             # never her own name aloud
    assert B.say_no_reply("Crissa", speak=said.append) == ""              # once
    B.after_reply_sec("What can we do today?", False)
    B.note_turn_answered()
    assert B.say_no_reply("Crissa", speak=said.append) == ""              # answered
    B.after_reply_sec("It's sunny.", False)
    assert B.say_no_reply("Crissa", speak=said.append) == ""              # not a question
    monkeypatch.setattr(B, "NO_REPLY_ON", False)
    B.after_reply_sec("Right?", False)
    assert B.say_no_reply("Crissa", speak=said.append) == ""


def test_the_wiring():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert "note_turn_answered()                          # round 26" in src
    assert 'if why == "timeout":\n        said = say_no_reply(name)' in src
