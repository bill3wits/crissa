"""One conversation (the User 2026-10-18, the iPhone app's M5): with --one-conversation the phone's Chat asks the
Wall's own Conversation (POST /v1/ask, speak false), so the phone and the Wall share history and session; a Wall with
no agent says so; a player without the route leaves the phone asking the agent itself, as before."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fake(code, body, seen):
    class F(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
            b = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), F)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _mod(tmp_path, **cfg):
    spec = importlib.util.spec_from_file_location("phone_app_server_oc", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CFG.clear()
    m.CFG.update({"agent_file": str(tmp_path / "none.json"), "one_conversation": True,
                  "floor_file": str(tmp_path / "phone-floor.json"), "session_file": str(tmp_path / "phone-session.json")}, **cfg)
    return m


def test_the_phone_asks_through_the_walls_conversation_without_speaking_there(tmp_path):
    seen = []
    wall = _fake(200, {"reply": "We were talking about the loch.", "error": None}, seen)
    try:
        m = _mod(tmp_path, wall="http://127.0.0.1:%d" % wall.server_address[1])
        out = m._chat("what were we talking about?", [], say=False)
        assert out == {"ok": True, "reply": "We were talking about the loch."}
        assert seen == [("/v1/ask", {"text": "what were we talking about?", "speak": False, "by": "phone"})]
    finally:
        wall.shutdown()


def test_a_wall_with_no_agent_says_connect(tmp_path):
    wall = _fake(409, {"ok": False, "connect": True}, [])
    try:
        m = _mod(tmp_path, wall="http://127.0.0.1:%d" % wall.server_address[1])
        out = m._chat("hi", [], say=False)
        assert out["ok"] is False and out["error"] == m.NOT_CONNECTED
    finally:
        wall.shutdown()


def test_a_player_without_the_route_leaves_the_phone_asking_the_agent_itself(tmp_path):
    seen = []
    wall = _fake(404, {"ok": False, "error": "not found"}, [])
    agent = _fake(200, {"choices": [{"message": {"content": "direct answer"}}]}, seen)
    try:
        m = _mod(tmp_path, wall="http://127.0.0.1:%d" % wall.server_address[1],
                 agent="http://127.0.0.1:%d/v1" % agent.server_address[1], model="m")
        out = m._chat("hi", [], say=False)
        assert out == {"ok": True, "reply": "direct answer"} and seen[0][0] == "/v1/chat/completions"
    finally:
        wall.shutdown(); agent.shutdown()


def test_off_by_default_and_the_wall_says_nothing_for_the_phone():
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    assert 'os.environ.get("CRISSA_PHONE_ONE_CONVERSATION", "0")' in src
    con = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert "def connect_ask(text: str, who: str, by: str, check_s: float = 0.0, speak: bool = True) -> dict:" in con
    body = con.split("def connect_ask(", 1)[1].split("\ndef ", 1)[0]
    assert 'if not speak or not line or turn["capped"]' in body                  # nothing said on the computer
    assert "    if speak:\n        LANE.begin_turn()" in body and "        if speak:\n            LANE.end_turn()" in body
    assert 'reply = CONNECT["conv"].ask(text, cfg' in body                         # the same Conversation: history + session
    assert 'if body.get("speak") is False:' in con and "rec = connect_ask(text, who, by, speak=False)" in con
