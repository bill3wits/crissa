"""Her own calls pass the same gates (cut 15, 2026-10-29): on a plain chat endpoint her tool calls come as `tool_calls`
deltas, never the gateway's tool event, so ID-only's face check (FaceFinder at his seat) never ran for them. Now it runs
before a call is done: no face, nothing runs and she says the face line; his face found, the call runs."""
from __future__ import annotations

import json

from crissa_bridge import display_call, id_gate
from crissa_bridge import ear_bridge as B

TOOL = {"type": "function", "function": {"name": "display", "parameters": {"type": "object"}}}


def _agent(tmp_path, monkeypatch):
    from crissa_bridge import connect
    (tmp_path / "agent.json").write_text(json.dumps({"url": "https://api.x.ai/v1", "model": "grok-x", "key": "k"}))
    monkeypatch.setattr(connect.A, "path", lambda home=None: tmp_path / "agent.json")
    monkeypatch.delenv("RAIL_VOICE_LLM", raising=False)
    monkeypatch.delenv("EAR_BRIDGE_OPS", raising=False)
    monkeypatch.setattr(B, "_crissa_soul", lambda: "You are her.")
    monkeypatch.setattr(display_call, "tools", lambda url="": [TOOL])
    monkeypatch.setattr(B, "_get", lambda *a, **k: {})
    monkeypatch.setattr(id_gate, "mode", lambda: "id_only")
    display_call._REFUSED.clear()


def _stream(posted):
    def fake(url, payload, **kw):
        posted.append(payload)
        calls = kw.get("calls")
        if len(posted) == 1 and calls is not None:
            calls.fold([{"index": 0, "id": "c1", "function": {"name": "display",
                                                              "arguments": '{"what": "document", "text": "x"}'}}])
            return "Putting it up."
        return "It's on the right."
    return fake


def _turn(tmp_path, monkeypatch, face_ok):
    _agent(tmp_path, monkeypatch)
    looked, ran, posted = [], [], []
    monkeypatch.setattr(id_gate, "act_check", lambda p, fetch, wait_s=None, look=None:
                        (looked.append(look) or face_ok, "face" if face_ok else "no_face", {}))
    monkeypatch.setattr(B, "_stream_chat_text", _stream(posted))
    monkeypatch.setattr(display_call, "run", lambda calls, url, **kw: [
        dict(c, result="shown: x") for c in calls.done() if not ran.append(c)])
    out = B.crissa_reply("put it on the screen", person_id="u1", presence={"headcount": 1}, level="bau",
                       user_voice=True)                                  # tools are offered on his voice only
    return out, looked, ran


def test_no_face_nothing_runs_and_she_says_the_face_line(tmp_path, monkeypatch):
    out, looked, ran = _turn(tmp_path, monkeypatch, False)
    assert looked and ran == []
    assert out == id_gate.act_line("no_face")


def test_his_face_found_the_call_runs(tmp_path, monkeypatch):
    out, looked, ran = _turn(tmp_path, monkeypatch, True)
    assert looked and len(ran) == 1 and "It's on the right." in out


def test_the_kill_skips_the_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "OWN_TOOL_GUARD", False)
    out, looked, ran = _turn(tmp_path, monkeypatch, False)
    assert looked == [] and len(ran) == 1
