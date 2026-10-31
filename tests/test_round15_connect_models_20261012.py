"""Round 15, Connect's model list (.101's fresh install, ASK-20261012-1425-from-101 item 2; 2026-10-12).

It took the User a while to find the exact model name; Connect only sent a test line. The User: "was it loading that
whole API lookup? It should be." Now the player asks the provider's own list (GET <base>/models with the key) once a
key is entered or the Kind changes, and the form offers the chat models, the best for a voice avatar first; the typed
field stays; the key is never logged or shown; a failed list says so plainly.
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

import crissa_agent as A  # noqa: E402

KEY = "xai-SECRETKEY-1234567890"
XAI = {"object": "list", "data": [
    {"id": "grok-4.7", "object": "model", "owned_by": "xai"},
    {"id": "grok-4.20-0309-reasoning", "object": "model"},
    {"id": "grok-imagine-image", "object": "model"},
    {"id": "grok-4.20-0309-non-reasoning", "object": "model"},
    {"id": "grok-4.6", "object": "model"},
    {"id": "grok-imagine-video", "object": "model"}]}
ANTHROPIC = {"data": [
    {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"},
    {"type": "model", "id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5"},
    {"type": "model", "id": "claude-fable-5-1", "display_name": "Claude Fable 5.1"},
    {"type": "model", "id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5"}], "has_more": False}
LOCAL = {"object": "list", "data": [{"id": "ops", "object": "model"}, {"id": "nomic-embed-text", "object": "model"}]}


def _serve(routes: dict):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
            code, body = routes.get(self.path, (404, {"error": "no"}))
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1], seen


def test_xai_lists_its_chat_models_with_the_non_reasoning_preset_first():
    srv, base, seen = _serve({"/v1/models": (200, XAI)})
    try:
        got = A.list_models({"url": base + "/v1", "key": KEY, "preset": "xai"})
    finally:
        srv.shutdown()
    assert got["ok"] and got["models"][0] == "grok-4.20-0309-non-reasoning" == A.KINDS["xai"]["model"]
    assert "grok-imagine-image" not in got["models"] and "grok-imagine-video" not in got["models"]
    assert got["models"][-1] == "grok-4.20-0309-reasoning" and got["count"] == 4
    assert seen[0]["path"] == "/v1/models" and seen[0]["headers"]["authorization"] == "Bearer " + KEY
    assert KEY not in json.dumps(got)


def test_anthropic_shape_and_headers_and_its_preset_first():
    srv, base, seen = _serve({"/v1/models": (200, ANTHROPIC)})
    try:
        got = A.list_models({"url": base + "/v1", "key": KEY, "preset": "claude"})
    finally:
        srv.shutdown()
    # round 17 (the Claude preset): Haiku 5.5 is the kind's model, so it leads; Anthropic's native headers, no Bearer
    assert got["ok"] and got["models"][0] == A.KINDS["claude"]["model"] == "claude-haiku-5-5"
    assert seen[0]["headers"]["x-api-key"] == KEY and "authorization" not in seen[0]["headers"]
    h = A._models_headers({"url": "https://api.anthropic.com/v1", "key": KEY})          # round 15's one ask (the kill)
    assert h["x-api-key"] == KEY and h["Authorization"] == "Bearer " + KEY and h["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in A._models_headers({"url": "https://api.x.ai/v1", "key": KEY})


def test_a_local_engine_needs_no_key_and_drops_embedding_models():
    srv, base, seen = _serve({"/v1/models": (200, LOCAL)})
    try:
        got = A.list_models({"url": base + "/v1", "preset": "local"})
    finally:
        srv.shutdown()
    assert got == {"ok": True, "models": ["ops"], "count": 1, "host": base.split("//")[1]}
    assert "authorization" not in seen[0]["headers"]


def test_a_bare_host_and_a_full_chat_address_both_find_the_list():
    assert A.models_url({"url": "http://127.0.0.1:8000"}) == "http://127.0.0.1:8000/v1/models"
    assert A.models_url({"url": "https://api.x.ai/v1/chat/completions"}) == "https://api.x.ai/v1/models"
    assert A.models_url({"url": "https://gw.example/p/crissa/v1"}) == "https://gw.example/p/crissa/v1/models"


def test_failures_say_so_plainly_and_never_carry_the_key():
    srv, base, _ = _serve({"/v1/models": (401, {"error": "Incorrect API key provided: " + KEY})})
    try:
        bad = A.list_models({"url": base + "/v1", "key": KEY, "preset": "xai"})
        missing = A.list_models({"url": base + "/nolist", "key": KEY})
    finally:
        srv.shutdown()
    assert not bad["ok"] and bad["models"] == [] and "key was refused" in bad["error"] and KEY not in json.dumps(bad)
    assert not missing["ok"] and "does not list its models" in missing["error"]
    gone = A.list_models({"url": "http://127.0.0.1:9/v1", "key": KEY}, timeout=1.0)
    assert not gone["ok"] and "Type the model's name" in gone["error"] and KEY not in json.dumps(gone)


def test_model_ids_reads_every_shape():
    assert A.model_ids(XAI)[:2] == ["grok-4.7", "grok-4.20-0309-reasoning"]
    assert A.model_ids({"models": [{"name": "llama3"}, {"name": "llama3"}]}) == ["llama3"]
    assert A.model_ids(["a", "b"]) == ["a", "b"] and A.model_ids(None) == [] and A.model_ids({"data": "x"}) == []


def test_the_player_route_sends_a_stored_key_only_to_its_own_host():
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    assert 'if head in ("/v1/agent", "/v1/agent/test", "/v1/agent/models", "/v1/ask")' in src   # the loopback guard
    assert src.count('if head == "/v1/agent/models":') == 1
    block = src.split('if head == "/v1/agent/models":')[1].split('if head == "/v1/agent/test":')[0]
    assert "crissa_agent.list_models(cfg)" in block and "if same_host else" in block
    assert "_log" not in block and "action(" not in block                     # never logged


def test_the_page_offers_the_list_and_keeps_the_typed_field():
    page = (ROOT / "display" / "crissa-connect.html").read_text(encoding="utf-8")
    assert '<input id="model" type="text"' in page and 'list="model-list"' in page and '<datalist id="model-list">' in page
    assert 'post("/v1/agent/models", body)' in page
    assert '$("key").addEventListener("change", lookupModels)' in page
    pre = page.split('$("preset").addEventListener("change", () => {')[1].split("});")[0]
    assert "lookupModels();" in pre
    assert "Type the model's name" in page
