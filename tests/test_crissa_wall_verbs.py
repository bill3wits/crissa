"""The Wall's verbs for agents (2026-10-01): one definition (lib/crissa_wall_verbs), served over MCP
(scripts/crissa-mcp.py), the one agent endpoint. A stand-in player on loopback; no network."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_wall_verbs as V  # noqa: E402

NAMES = ["wall_state", "wall_keys", "wall_cue", "wall_home", "wall_comms", "wall_speak", "wall_ask", "wall_stop",
         "wall_announce"]


@pytest.fixture()
def player():
    seen: list = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(("GET", self.path, None, self.headers.get("Origin")))
            if self.path == "/v1/state":
                return self._send(200, {"clip": "work", "on_air": False, "comms": False, "secret": 1})
            self._send(200, {"ok": True, "keys": ["wake", "rest"]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen.append(("POST", self.path, body, self.headers.get("Origin")))
            if self.path == "/v1/cue" and body.get("key") == "nope":
                return self._send(409, {"ok": False, "error": "not a key here"})
            if self.path == "/v1/ask" and body.get("text") == "silent":      # answered, voice failed (2026-10-04)
                return self._send(502, {"ok": False, "reply": "Hello there.", "spoke": False,
                                        "error": "she could not speak (exit 1): cosy refused"})
            if self.path == "/v1/ask" and body.get("text") == "dead":        # the agent itself failed
                return self._send(502, {"ok": False, "reply": None, "error": "the agent is not answering"})
            self._send(200, {"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], seen
    srv.shutdown()


def test_every_verb_has_a_description_and_an_object_schema():
    assert [n for n, *_ in V.VERBS] == NAMES
    for name, desc, schema, fn in V.VERBS:
        assert desc.strip() and schema["type"] == "object" and callable(fn), name


def test_each_verb_is_one_call_to_the_players_public_route(player):
    base, seen = player
    assert V.call("wall_state", {}, base=base)["result"] == {"clip": "work", "on_air": False, "comms": False}
    V.call("wall_keys", {}, base=base)
    V.call("wall_cue", {"key": "wake", "ttl": 5}, base=base)
    V.call("wall_home", {}, base=base)
    V.call("wall_comms", {"on": True}, base=base)
    V.call("wall_speak", {"text": "Hello there."}, base=base)
    V.call("wall_ask", {"text": "What time is it?"}, base=base)
    V.call("wall_stop", {}, base=base)
    V.call("wall_announce", {"text": "The build is done.", "kind": "done", "dedup_key": "t1"}, base=base)
    got = [(m, p, b) for m, p, b, _ in seen]
    assert got == [
        ("GET", "/v1/state", None), ("GET", "/v1/kit", None),
        ("POST", "/v1/cue", {"key": "wake", "by": "agent", "ttl": 5.0}),
        ("POST", "/v1/home", {"by": "agent"}),
        ("POST", "/v1/comms", {"on": True, "why": "agent"}),
        ("POST", "/v1/speak", {"text": "Hello there.", "by": "agent"}),
        ("POST", "/v1/ask", {"text": "What time is it?", "wait": True, "by": "agent"}),
        ("POST", "/v1/stop", {"by": "agent"}),
        ("POST", "/v1/announce", {"text": "The build is done.", "by": "agent", "kind": "done", "dedup_key": "t1"}),
    ]
    assert all(o is None for *_, o in seen), "no Origin: the player treats the agent as this machine"


def test_a_verb_never_raises(player):
    base, _ = player
    assert V.call("wall_cue", {"key": "nope"}, base=base) == {"ok": False, "error": "not a key here"}
    assert V.call("wall_cue", {}, base=base)["ok"] is False
    assert V.call("wall_comms", {"on": "yes"}, base=base)["ok"] is False
    assert V.call("wall_speak", {"text": " "}, base=base)["ok"] is False
    assert "not answering" in V.call("wall_state", {}, base="http://127.0.0.1:9")["error"]
    assert V.call("wall_fly", {})["error"] == "no such verb: wall_fly"


def test_the_mcp_server_speaks_stdio_json_rpc(player):
    base, seen = player
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "wall_speak", "arguments": {"text": "Hi."}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wall_cue", "arguments": {"key": "nope"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"}]
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "crissa-mcp.py")],
                       input="\n".join(json.dumps(m) for m in msgs) + "\nnot json\n",
                       capture_output=True, text=True, timeout=60, env={"CRISSA_PLAYER": base, "PATH": "/usr/bin"})
    out = [json.loads(ln) for ln in r.stdout.splitlines()]
    assert [o.get("id") for o in out] == [1, 2, 3, 4, 5, None], "a notification gets no reply"
    assert out[0]["result"]["protocolVersion"] == "2025-06-18" and "tools" in out[0]["result"]["capabilities"]
    assert [t["name"] for t in out[1]["result"]["tools"]] == NAMES
    assert all("inputSchema" in t and t["description"] for t in out[1]["result"]["tools"])
    assert out[2]["result"]["isError"] is False and ("POST", "/v1/speak", {"text": "Hi.", "by": "agent"}, None) in seen
    assert out[3]["result"]["isError"] is True and "not a key here" in out[3]["result"]["content"][0]["text"]
    assert out[4]["error"]["code"] == -32601 and out[5]["error"]["code"] == -32700


def test_the_conductor_has_a_public_speak_route_with_the_loopback_rule():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/speak"') == 1
    block = src.split('if head == "/v1/speak"', 1)[1].split("if head", 1)[0]
    # the line goes through the speech lane since 2026-10-06 (LANE.say spawns it, or queues it with "queue")
    assert "connect_origin_ok" in block and "CRISSA_SPEAK_ROUTE" in block and "LANE.say(" in block
    assert "/v1/speak" not in src.split("AUTHORING_POST = frozenset(", 1)[1].split(")", 1)[0]


def test_ask_keeps_the_reply_when_she_was_not_heard(player):
    """The player answers 502 with the reply when her voice fails (Mac fix 7); the verb returns the reply with
    spoke false instead of raising, so the agent still knows what she said (2026-10-04)."""
    base, _ = player
    out = V.ask({"text": "silent"}, base=base)
    assert out["reply"] == "Hello there." and out["spoke"] is False and out["ok"] is False
    assert "could not speak" in out["error"]
    with pytest.raises(V.WallError, match="not answering"):
        V.ask({"text": "dead"}, base=base)                       # no reply: still an error
    with pytest.raises(V.WallError, match="not a key"):
        V.cue({"key": "nope"}, base=base)                        # other verbs unchanged


def test_crissa_ships_no_agent_framework_plugin():
    """The owner's boundary (2026-10-06): the Wall touches krew only through the agent endpoint (the MCP server and
    the player's HTTP routes) and the Control Panel tiles; Crissa ships no framework plugin. krew carries its own
    (the Wall's tools and the kill-search hook), so the plugin source, its package, its scrub exception and the
    agents'-side hook code in lib/crissa_egress.py are gone; the verbs file still ships for the MCP server."""
    assert not (ROOT / "integrations" / "krew").exists()
    pk = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]
    assert "crissa-krew" not in pk
    assert any("crissa_wall_verbs.py" in (p.get("lib") or []) for p in pk.values()), "the MCP server's verbs still ship"
    ex = json.loads((ROOT / "config" / "crissa-scrub-exceptions.json").read_text())["packages"]
    assert "crissa-krew" not in ex
    assert "integrations/krew" not in (ROOT / "scripts" / "crissa-export.py").read_text()
    import crissa_egress as E
    for gone in ("agent_tool_verdict", "relay_answer", "relay_search", "relay_extract", "AGENT_TOOL_PREFIXES"):
        assert not hasattr(E, gone), gone
    assert callable(E.search_route) and callable(E.brave_flags) and callable(E.refuse_install)


def test_stop_falls_back_to_say_stop_on_an_older_player(monkeypatch):
    seen = []

    def fake(method, path, body=None, timeout=15.0, base=None, soft=()):
        seen.append(path)
        if path == "/v1/stop":
            raise V.WallError("the player said 404 on /v1/stop")
        return {"ok": True}
    monkeypatch.setattr(V, "_call", fake)
    assert V.call("wall_stop", {})["ok"] is True and seen == ["/v1/stop", "/v1/say"]
    assert V.call("wall_speak", {"text": "Later.", "queue": True})["ok"] is True


def test_wall_announce_schema_carries_voice_and_pause():
    desc, schema, fn = V.BY_NAME["wall_announce"]
    props = schema["properties"]
    assert "voice" in props and "speech_pause_s" in props and "max_wait_s" in props
