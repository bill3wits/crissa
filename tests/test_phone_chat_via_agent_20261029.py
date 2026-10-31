"""The phone asks through the kit's own agent client (the User 2026-10-29: "getting http errors on TTS mobile connection
... when i press send" - Claude answered 400 to the phone's own OpenAI call, which sent a temperature and vLLM's
chat_template_kwargs). The install's Connect file, the phone's persona and its 160-token cap."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _agent(seen):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            b = json.dumps({"choices": [{"message": {"content": "through the client"}, "finish_reason": "stop"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _mod(tmp_path, agent_url):
    spec = importlib.util.spec_from_file_location("phone_app_server_via", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    home = tmp_path / "home"
    home.mkdir()
    (home / "agent.json").write_text(json.dumps({"url": agent_url, "model": "m", "key": "sk-test"}))
    m.CFG.clear()
    m.CFG.update({"packs": str(tmp_path), "lines": str(tmp_path), "agent_file": str(home / "agent.json"),
                  "floor_file": str(tmp_path / "phone-floor.json"), "session_file": str(tmp_path / "phone-session.json")})
    return m


def test_the_install_connect_is_asked_through_the_client(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_PHONE_CHAT_VIA_AGENT", raising=False)
    seen = []
    srv = _agent(seen)
    try:
        m = _mod(tmp_path, "http://127.0.0.1:%d/v1" % srv.server_address[1])
        out = m._chat("hi", [{"role": "assistant", "content": "hello"}], say=False)
        assert out == {"ok": True, "reply": "through the client"}
        body = seen[0][1]
        assert "chat_template_kwargs" not in body and body["messages"][0] == {"role": "system", "content": m.PERSONA}
        assert body["messages"][1:] == [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "hi"}]
        assert body.get("max_tokens") == 160 or body.get("max_completion_tokens") == 160
    finally:
        srv.shutdown()


def test_the_switch_puts_back_the_old_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_PHONE_CHAT_VIA_AGENT", "0")
    seen = []
    srv = _agent(seen)
    try:
        m = _mod(tmp_path, "http://127.0.0.1:%d/v1" % srv.server_address[1])
        assert m._chat("hi", [], say=False)["ok"] is True
        assert "chat_template_kwargs" in seen[0][1]
    finally:
        srv.shutdown()
