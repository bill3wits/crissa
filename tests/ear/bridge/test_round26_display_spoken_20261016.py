"""Round 26: her display tool on a spoken turn (.101 10-16 check 4, three tries: "no display although she says she
did"). The tool was only on the conductor's own ask path; a spoken turn goes through the bridge, which never offered
it. Now: offered when the turn's tools are allowed, the call run at the conductor, her short line after the result;
not offered = she is told she cannot show anything this turn."""
from __future__ import annotations

import json

from crissa_bridge import display_call
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
    display_call._REFUSED.clear()


def _stream(posted, first="Putting it up.", call=True):
    def fake(url, payload, **kw):
        posted.append(json.loads(json.dumps(payload)))
        calls = kw.get("calls")
        if len(posted) == 1 and call and calls is not None:
            calls.fold([{"index": 0, "id": "c1", "function": {"name": "display", "arguments": ""}}])
            calls.fold([{"index": 0, "function": {"arguments": '{"what": "document", "title": "Pancakes",'}}])
            calls.fold([{"index": 0, "function": {"arguments": ' "text": "1 cup flour"}'}}])
            return first
        if kw.get("on_delta"):
            kw["on_delta"]("It's on the right.")
        return "It's on the right." if len(posted) > 1 else first
    return fake


def test_an_allowed_turn_offers_the_tool_runs_it_and_says_one_line(tmp_path, monkeypatch):
    _agent(tmp_path, monkeypatch)
    posted, ran = [], []
    monkeypatch.setattr(B, "_stream_chat_text", _stream(posted))
    monkeypatch.setattr(display_call, "run", lambda calls, url, **kw: [
        dict(c, result="shown: Pancakes") for c in calls.done() if not ran.append((url, c))])
    out = B.crissa_reply("Crissa, put a pancake recipe on the screen", person_id="u1", presence={"headcount": 1},
                       level="bau", user_voice=True)
    assert posted[0]["tools"] == [TOOL] and posted[0].get("tool_choice") is None
    assert ran and ran[0][0].endswith("/v1/display-tool")
    assert json.loads(ran[0][1]["args"]) == {"what": "document", "title": "Pancakes", "text": "1 cup flour"}
    second = posted[1]
    assert second["tool_choice"] == "none"
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "display"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "shown: Pancakes"}
    assert "Putting it up." in out and "It's on the right." in out


def test_a_turn_without_tools_is_told_it_cannot_show_anything(tmp_path, monkeypatch):
    _agent(tmp_path, monkeypatch)
    posted = []
    monkeypatch.setattr(B, "_stream_chat_text", _stream(posted, first="I can't show it yet.", call=False))
    B.crissa_reply("put it on the screen", person_id=None, presence={"headcount": 1}, level="bau", user_voice=False)
    assert "tools" not in posted[0] and len(posted) == 1
    assert display_call.NO_SCREEN_LINE.strip() in posted[0]["messages"][0]["content"]


def test_the_kill_offers_nothing_and_adds_nothing(tmp_path, monkeypatch):
    _agent(tmp_path, monkeypatch)
    monkeypatch.setattr(display_call, "tools", lambda url="": [])
    posted = []
    monkeypatch.setattr(B, "_stream_chat_text", _stream(posted, call=False))
    B.crissa_reply("hi", person_id="u1", presence={"headcount": 1}, level="bau", user_voice=True)
    assert "tools" not in posted[0]
    assert display_call.NO_SCREEN_LINE.strip() not in posted[0]["messages"][0]["content"]


def test_the_switch_and_the_house(monkeypatch):
    monkeypatch.setenv("CRISSA_BRIDGE_DISPLAY", "0")
    assert display_call.tools() == []
    monkeypatch.delenv("CRISSA_BRIDGE_DISPLAY")
    monkeypatch.setenv("CRISSA_DISPLAY_TOOL", "0")
    assert display_call.tools() == []


def test_calls_fold_from_stream_lines_and_whole_messages():
    c = display_call.Calls()
    c.line(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"display","arguments":"{\\"what\\""}}]}}]}')
    c.line(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":": \\"clear\\"}"}}]}}]}')
    c.line(b'data: {"choices":[{"delta":{"content":"hi"}}]}')
    assert [(x["name"], json.loads(x["args"])) for x in c.done()] == [("display", {"what": "clear"})]
    m = display_call.Calls()
    m.message({"choices": [{"message": {"tool_calls": [{"id": "b", "function": {"name": "display",
                                                                               "arguments": {"what": "clear"}}}]}}]})
    assert m.done()[0]["id"] == "b" and json.loads(m.done()[0]["args"]) == {"what": "clear"}
    assert not display_call.Calls()


def test_run_posts_each_call_and_a_failure_is_not_done():
    c = display_call.Calls()
    c.fold([{"index": 0, "id": "a", "function": {"name": "display", "arguments": '{"what": "clear"}'}}])
    seen = []
    out = display_call.run(c, "http://x/v1/display-tool", post=lambda u, b: seen.append(b) or {"result": "cleared"})
    assert seen == [{"name": "display", "arguments": {"what": "clear"}}] and out[0]["result"] == "cleared"

    def boom(u, b):
        raise OSError("down")
    assert display_call.run(c, "u", post=boom)[0]["result"] == "NOT DONE: OSError"


def test_the_conductor_runs_a_call_on_its_own_route():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[3] / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/display-tool":') == 1
    blk = src.split('if head == "/v1/display-tool":', 1)[1].split("if head ==", 1)[0]
    assert "connect_origin_ok" in blk and "crissa_display.on()" in blk and "display_tool(" in blk
