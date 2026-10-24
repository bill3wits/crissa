"""Connect (2026-09-28): the player answers through any OpenAI-compatible agent.

The client is proved against a real HTTP server on the loopback (stdlib), in
both shapes a server may answer with: SSE when it streams, one JSON body when
it ignores `stream: true`. The conductor's half is proved on its source — the
routes are public (never in the authoring gate), their anchors are unique for
the source-slicing tests, and a foreign web page cannot change who she talks to.
"""
from __future__ import annotations

import ast
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_agent as za  # noqa: E402

SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


class _Agent(BaseHTTPRequestHandler):
    mode = "sse"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if type(self).mode == "401":
            self.send_response(401)
            self.end_headers()
            return
        if type(self).mode == "json":
            raw = json.dumps({"choices": [{"message": {"content": "Hello <think>x</think>there."}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ("Hi", " there", ", friend."):
            ev = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
        self.wfile.write(b": keepalive\n\ndata: [DONE]\n\n")


@pytest.fixture()
def agent():
    _Agent.mode, _Agent.seen = "sse", []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Agent)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def test_streams_a_reply(agent):
    out = za.ask("hello", {"url": agent + "/v1", "model": "m", "key": "sk-1"})
    assert out == "Hi there, friend."
    got = _Agent.seen[-1]
    assert got["path"] == "/v1/chat/completions"
    assert got["headers"]["Authorization"] == "Bearer sk-1"
    assert got["body"]["model"] == "m" and got["body"]["stream"] is True
    assert got["body"]["messages"][0]["role"] == "system"
    assert not any(h.lower().endswith("session-id") for h in got["headers"]), "no session header unless named"


def test_a_server_that_ignores_stream_still_answers(agent):
    _Agent.mode = "json"
    out = za.ask("hello", {"url": agent + "/v1/chat/completions"})
    assert za.speakable(out) == "Hello there."
    assert _Agent.seen[-1]["path"] == "/v1/chat/completions"


def test_a_refusal_names_the_status_not_the_key(agent):
    _Agent.mode = "401"
    with pytest.raises(za.AgentError) as e:
        za.ask("hello", {"url": agent, "key": "sk-secret"})
    assert "API key" in str(e.value) and "401" in e.value.detail
    assert "sk-secret" not in str(e.value) and "sk-secret" not in e.value.detail


def test_unreachable_is_an_agent_error():
    r = za.test({"url": "http://127.0.0.1:9/v1"}, timeout=2)
    assert r["ok"] is False and "reach" in r["error"]


def test_not_connected_is_said_plainly():
    with pytest.raises(za.AgentError, match="The Address is empty"):
        za.ask("hi", {})
    with pytest.raises(za.AgentError, match="not connected"):
        za.ask("hi", {"url": "http://x/v1", "preset": "none"})
    assert not za.configured({"url": "http://x/v1", "preset": "none"})


def test_a_gateway_is_a_url_and_a_named_session_header(agent):
    """A gateway serving several agents is just its per-agent url; one that keeps
    memory per session gets the id under the header the User names."""
    cfg = {"url": agent + "/p/crissa/v1", "key": "k", "session_header": "X-Agent-Session-Id"}
    assert za.chat_url(cfg) == agent + "/p/crissa/v1/chat/completions"
    za.ask("hi", cfg, session_id="s-1")
    assert _Agent.seen[-1]["headers"]["X-Agent-Session-Id"] == "s-1"
    assert _Agent.seen[-1]["path"] == "/p/crissa/v1/chat/completions"


@pytest.mark.parametrize("url,want", [
    ("http://h:1/v1", "http://h:1/v1/chat/completions"),
    ("http://h:1/v1/", "http://h:1/v1/chat/completions"),
    ("http://h:1", "http://h:1/v1/chat/completions"),
    ("https://api.x.ai/v1/chat/completions", "https://api.x.ai/v1/chat/completions"),
])
def test_chat_url(url, want):
    assert za.chat_url({"url": url}) == want


def test_save_is_private_and_keeps_the_key_on_the_same_host(tmp_path):
    za.save({"url": "http://127.0.0.1:8000/v1", "key": "sk-a", "model": "m"}, home=tmp_path)
    p = za.path(tmp_path)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    za.save({"url": "http://127.0.0.1:8000/v1", "model": "m2"}, home=tmp_path)
    cfg = za.load(home=tmp_path, env={})
    assert cfg["key"] == "sk-a" and cfg["model"] == "m2"


def test_a_key_never_follows_a_new_host(tmp_path):
    za.save({"url": "http://127.0.0.1:8000/v1", "key": "sk-a"}, home=tmp_path)
    za.save({"url": "https://elsewhere.example/v1"}, home=tmp_path)
    assert "key" not in za.load(home=tmp_path, env={})
    za.save({"url": "https://elsewhere.example/v1", "key": "sk-b"}, home=tmp_path)
    za.save({"clear_key": True}, home=tmp_path)
    assert "key" not in za.load(home=tmp_path, env={})


def test_redacted_never_carries_the_key(tmp_path):
    za.save({"url": "http://127.0.0.1:8000/v1", "key": "sk-a"}, home=tmp_path)
    out = za.redacted(za.load(home=tmp_path, env={}))
    assert "key" not in out and out["has_key"] is True and out["configured"] is True
    assert "sk-a" not in json.dumps(out)


def test_env_beats_the_file(tmp_path):
    za.save({"url": "http://127.0.0.1:8000/v1"}, home=tmp_path)
    cfg = za.load(home=tmp_path, env={"CRISSA_AGENT_URL": "http://127.0.0.1:9000/v1"})
    assert cfg["url"] == "http://127.0.0.1:9000/v1"


@pytest.mark.parametrize("bad", [
    {"url": "file:///etc/passwd"}, {"url": "ftp://h/"}, {"preset": "grok"}, {"model": "a\nb"},
    {"session_header": "Authorization"}, {"session_header": "X-A: b"},
])
def test_validate_refuses(tmp_path, bad):
    with pytest.raises(ValueError):
        za.save(bad, home=tmp_path)


def test_speakable_cuts_at_a_sentence_and_drops_markup():
    long = "First sentence here. " * 60
    out = za.speakable(long, 100)
    assert len(out) <= 100 and out.endswith(".")
    assert za.speakable("**Bold** and `code` <think>plan</think> done") == "Bold and code done"


def test_conversation_carries_context(agent):
    conv = za.Conversation(turns=2)
    cfg = {"url": agent + "/v1"}
    conv.ask("one", cfg)
    conv.ask("two", cfg)
    msgs = _Agent.seen[-1]["body"]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "one" and msgs[-1]["content"] == "two"


# ---- the conductor's half ------------------------------------------------------------

def _fn(name: str):
    tree = ast.parse(SRC)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns[name]


def test_connect_routes_are_the_players_not_authoring():
    gate = SRC.split("AUTHORING_POST = frozenset({", 1)[1].split("})", 1)[0]
    for r in ("/v1/ask", "/v1/agent"):
        assert '"%s"' % r not in gate, "a pack with no agent cannot answer anyone"


def test_route_anchors_are_unique():
    """Eight tests slice this file on route anchors; a second copy hands them the wrong block."""
    assert SRC.count('if head == "/v1/agent":') == 1
    assert SRC.count('if head == "/v1/agent/test":') == 1
    # round 15: the guard names the model list's route too
    assert SRC.count('if head in ("/v1/agent", "/v1/agent/test", "/v1/agent/models", "/v1/ask")') == 1
    assert SRC.count('if head == "/v1/agent/models":') == 1


@pytest.mark.parametrize("origin,ok", [
    (None, True), ("http://127.0.0.1:8100", True), ("http://localhost:8100", True),
    ("http://[::1]:8100", True), ("https://evil.example", False), ("null", False),
    ("http://127.0.0.1.evil.example", False),
])
def test_only_this_machines_pages_may_connect(origin, ok):
    check = _fn("connect_origin_ok")
    assert check({} if origin is None else {"Origin": origin}) is ok


def test_the_page_exists_and_never_asks_for_the_key_back():
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert "/v1/agent/test" in page and "/v1/ask" in page
    assert "has_key" in page, "a stored key shows as stored, never as its value"
    assert "Created with Grok. Credits and licences: CREDITS.md and MEDIA-LICENSE.md in this pack." in page


def test_the_manifest_has_no_duplicate_keys():
    """JSON's last duplicate wins silently: a second `files` list in crissa-avatar once
    dropped the Connect page from a built pack with every test green."""
    def hook(pairs):
        keys = [k for k, _ in pairs]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        assert not dup, "duplicate keys %s" % dup
        return dict(pairs)
    json.loads((ROOT / "config" / "crissa-packages.json").read_text(), object_pairs_hook=hook)
    pkg = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]["crissa-avatar"]
    assert "display/crissa-connect.html" in pkg["files"] and "crissa_agent.py" in pkg["lib"]


def test_save_keeps_keys_it_does_not_know(tmp_path):
    """A Save on /connect kept only FIELDS and so deleted the voice's `tts` block from the same agent.json (10-04)."""
    p = za.path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tts = {"engine": "say", "key": "sk-tts"}
    p.write_text(json.dumps({"url": "http://127.0.0.1:9/v1", "tts": tts}))
    za.save({"model": "m"}, home=tmp_path)
    stored = json.loads(p.read_text())
    assert stored["tts"] == tts and stored["model"] == "m"
    assert "tts" not in za.load(home=tmp_path, env={})                 # never reaches the agent call
    assert "sk-tts" not in json.dumps(za.redacted(stored))             # never reaches the page
    za.save({"tts": {"engine": "openai"}}, home=tmp_path)              # a page cannot write it
    assert json.loads(p.read_text())["tts"] == tts


def test_the_connect_page_says_when_she_was_not_heard():
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert "not heard" in page
