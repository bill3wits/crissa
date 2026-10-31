"""SSE vs JSON fallback for _stream_chat_text. Local fake server, no GPU."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from crissa_bridge.ear_bridge import _stream_chat_text


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def test_stream_chat_sse_accumulates_and_kicks():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("Got it. ", "Filing the card."):
                body = json.dumps({"choices": [{"delta": {"content": piece}}]})
                self.wfile.write(f"data: {body}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
        def log_message(self, *a):
            return
    httpd = _serve(H)
    seen = []
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat/completions"
        text = _stream_chat_text(url, {"messages": []}, timeout=5.0, on_delta=seen.append)
    finally:
        httpd.shutdown()
    assert text == "Got it. Filing the card."
    assert seen[0] == "Got it. "
    assert seen[-1] == text


def test_stream_chat_json_fallback():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "choices": [{"message": {"content": "Hello the User."}}]
            }).encode())
        def log_message(self, *a):
            return
    httpd = _serve(H)
    seen = []
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat/completions"
        text = _stream_chat_text(url, {"messages": []}, timeout=5.0, on_delta=seen.append)
    finally:
        httpd.shutdown()
    assert text == "Hello the User."
    assert seen == ["Hello the User."]
