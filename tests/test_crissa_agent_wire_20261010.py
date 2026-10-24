"""Connect's test against the endpoints the .101 demo met (the User 2026-10-10), each against a loopback stub:

  - OpenAI's reasoning families (o-series, gpt-5) are asked with no temperature and their cap in max_completion_tokens;
  - any other model that answers 400 naming temperature / max_tokens is asked once more without it, and remembered;
  - a thinking model passes the test on a real reply (the test's budget is big enough), and a reply that is all
    thinking is "still thinking", never "no text";
  - a base address with a path of its own is used as given (no /v1 pushed in front of chat/completions), and both
    documented shapes still work;
  - a Claude model is still asked with no temperature;
  - a pass is kept (agent-test.json, no key) so Init's Continue can count a pass made on the Connect page.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_agent as za  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    """An OpenAI-compatible stub. `behave(body, path) -> (status, kind, payload)`; kind sse|json|error."""
    seen: list = []
    behave = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).seen.append({"path": self.path, "body": body})
        status, kind, payload = type(self).behave(body, self.path)
        if kind == "error":
            raw = json.dumps({"error": {"message": payload, "type": "invalid_request_error"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if kind == "json":
            raw = json.dumps({"choices": [{"message": payload}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta in payload:
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture()
def stub(monkeypatch):
    Stub.seen = []
    za._REFUSED.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def _hello(*_a):
    return 200, "sse", [{"content": "Hello"}, {"content": " there."}]


# ---- reasoning models ---------------------------------------------------------------------------------------------
def _openai_reasoning_rules(body, path):
    """How OpenAI answers an o-series / gpt-5 model: 400 on max_tokens and on a non-default temperature."""
    if "max_tokens" in body:
        return 400, "error", ("Unsupported parameter: 'max_tokens' is not supported with this model. "
                              "Use 'max_completion_tokens' instead.")
    if "temperature" in body and body["temperature"] != 1:
        return 400, "error", "Unsupported value: 'temperature' does not support 0.4 with this model. Only the default (1) value is supported."
    return _hello()


@pytest.mark.parametrize("model", ["o3-mini", "o1", "o4-mini", "gpt-5", "gpt-5-mini", "openai/o3"])
def test_a_reasoning_model_is_asked_without_temperature_and_with_its_own_cap(stub, model):
    Stub.behave = staticmethod(_openai_reasoning_rules)
    r = za.test({"url": stub + "/v1", "model": model, "key": "k"}, timeout=10)
    assert r["ok"] is True and r["reply"] == "Hello there.", r
    first = Stub.seen[0]["body"]
    assert "temperature" not in first and "max_tokens" not in first, first
    assert first["max_completion_tokens"] == za.TEST_MAX_TOKENS, "the test's budget goes in the cap the model takes"
    assert len(Stub.seen) == 1, "sent right the first time: no refusal to retry"


def test_another_model_that_refuses_temperature_is_asked_once_more_without_it_and_remembered(stub):
    Stub.behave = staticmethod(lambda body, path: (400, "error", "temperature: unsupported for this model")
                               if "temperature" in body else _hello())
    cfg = {"url": stub + "/v1", "model": "house-reasoner-7b", "key": "k"}
    assert za.ask("hi", cfg) == "Hello there."
    assert ["temperature" in s["body"] for s in Stub.seen] == [True, False], "one retry, without it"
    za.ask("again", cfg)
    assert "temperature" not in Stub.seen[-1]["body"] and len(Stub.seen) == 3, "remembered: no second refusal"


def test_a_400_that_asks_for_max_completion_tokens_moves_the_cap_there(stub):
    Stub.behave = staticmethod(lambda body, path: (400, "error", "Unsupported parameter: 'max_tokens' is not supported "
                                                   "with this model. Use 'max_completion_tokens' instead.")
                               if "max_tokens" in body else _hello())
    r = za.test({"url": stub + "/v1", "model": "some-new-model", "key": "k"}, timeout=10)
    assert r["ok"] is True, r
    assert "max_tokens" in Stub.seen[0]["body"] and "max_tokens" not in Stub.seen[1]["body"]
    assert Stub.seen[1]["body"]["max_completion_tokens"] == za.TEST_MAX_TOKENS


def test_a_400_about_something_else_is_a_plain_error_and_not_retried(stub):
    Stub.behave = staticmethod(lambda body, path: (400, "error", "messages: the first message must be from the user"))
    r = za.test({"url": stub + "/v1", "model": "m"}, timeout=10)
    assert r["ok"] is False and "400" in r["detail"] and len(Stub.seen) == 1


# ---- thinking models ----------------------------------------------------------------------------------------------
def _thinker(body, path):
    """A thinking model: it thinks ~600 tokens before it answers; a cap below that cuts it inside its thinking."""
    cap = body.get("max_tokens") or body.get("max_completion_tokens")
    if cap is not None and cap < 600:
        return 200, "sse", [{"content": "<think>Let me consider how to greet"}]
    return 200, "sse", [{"content": "<think>Let me consider how to greet.</think>"}, {"content": "Hello there."}]


def test_a_thinking_model_passes_on_its_real_reply(stub):
    Stub.behave = staticmethod(_thinker)
    r = za.test({"url": stub + "/v1", "model": "qwen3-think"}, timeout=10)
    assert r["ok"] is True and r["reply"] == "Hello there." and len(Stub.seen) == 1, r
    assert Stub.seen[0]["body"]["max_tokens"] >= 600, "the test's budget leaves room to think and then answer"


def test_a_reply_that_is_all_thinking_is_still_thinking_not_no_text(stub):
    Stub.behave = staticmethod(lambda body, path: (200, "sse", [{"content": "<think>hmm, the user wants"}]))
    r = za.test({"url": stub + "/v1", "model": "deep-thinker"}, timeout=10)
    assert r["ok"] is False and r.get("thinking") is True and r["error"] == za.STILL_THINKING
    assert "no text" not in r["error"]
    assert len(Stub.seen) == 2 and "max_tokens" not in Stub.seen[1]["body"], "asked once more with no cap of ours"


def test_reasoning_frames_with_no_answer_are_thinking_too(stub):
    Stub.behave = staticmethod(lambda body, path: (200, "sse", [{"reasoning_content": "Thinking about it"}]))
    r = za.test({"url": stub + "/v1", "model": "r1"}, timeout=10)
    assert r["ok"] is False and r.get("thinking") is True


def test_reasoning_then_an_answer_passes(stub):
    Stub.behave = staticmethod(lambda body, path: (200, "sse", [{"reasoning_content": "Thinking"}, {"content": "Hi there."}]))
    assert za.test({"url": stub + "/v1", "model": "r1"}, timeout=10)["ok"] is True


def test_a_json_reply_with_only_reasoning_is_thinking(stub):
    Stub.behave = staticmethod(lambda body, path: (200, "json", {"content": "", "reasoning_content": "..."}))
    r = za.test({"url": stub + "/v1", "model": "r1"}, timeout=10)
    assert r["ok"] is False and r.get("thinking") is True


def test_a_template_that_opened_the_think_block_in_the_prompt():
    assert za.speakable("weighing the greeting...</think>Hello there.") == "Hello there."


# ---- the address --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("base,path", [
    ("/v1beta/openai", "/v1beta/openai/chat/completions"),
    ("/api", "/api/chat/completions"),
    ("/p/agent/v1", "/p/agent/v1/chat/completions"),
    ("/v1", "/v1/chat/completions"),
    ("/v1/chat/completions", "/v1/chat/completions"),
    ("", "/v1/chat/completions"),
])
def test_the_address_is_used_as_given(stub, base, path):
    Stub.behave = staticmethod(_hello)
    assert za.ask("hi", {"url": stub + base, "model": "m"}) == "Hello there."
    assert Stub.seen[-1]["path"] == path


# ---- Claude stays as it was ---------------------------------------------------------------------------------------
def test_a_claude_model_is_asked_without_a_temperature(stub):
    Stub.behave = staticmethod(lambda body, path: (400, "error", "temperature: must be at least 1")
                               if body.get("temperature", 1) < 1 else _hello())
    r = za.test({"url": stub + "/v1", "model": "claude-fable-5-1", "key": "k"}, timeout=10)
    assert r["ok"] is True and "temperature" not in Stub.seen[0]["body"] and len(Stub.seen) == 1


# ---- the record Init reads ----------------------------------------------------------------------------------------
def test_every_test_is_kept_without_the_key(stub, tmp_path, monkeypatch):
    Stub.behave = staticmethod(_hello)
    monkeypatch.setattr(za, "HOME", tmp_path)
    monkeypatch.setattr(za, "_records_here", lambda: True)
    cfg = {"url": stub + "/v1", "model": "m", "key": "sk-secret-1"}
    assert za.test(cfg, timeout=10)["ok"]
    rec = za.last_test()
    assert rec["ok"] is True and rec["fp"] == za.fingerprint(cfg) and rec["n"] == 1
    assert "sk-secret-1" not in (tmp_path / "agent-test.json").read_text()
    assert za.last_pass_cfg()["key"] == "sk-secret-1", "the passing config stays in memory for Init's TEST"
    Stub.behave = staticmethod(lambda body, path: (401, "error", "no"))
    assert za.test(cfg, timeout=10)["ok"] is False
    assert za.last_test()["ok"] is False and za.last_test()["n"] == 2


def test_the_house_state_directory_is_never_written(monkeypatch, tmp_path):
    monkeypatch.setattr(za, "HOME", tmp_path)
    monkeypatch.setattr(za, "_records_here", lambda: False)
    assert za.record_test({"url": "http://x/v1"}, {"ok": True}) == {}
    assert not (tmp_path / "agent-test.json").exists()


def test_the_rule_is_written_down():
    src = (ROOT / "lib" / "crissa_agent.py").read_text()
    assert "REASONING MODELS (2026-10-10" in src and "ONCE more without what it named" in src
    assert re.search(r"TEST_MAX_TOKENS = \d{4}", src)
