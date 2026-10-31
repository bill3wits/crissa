"""The oracle is the pack's camera (2026-10-08): one enrollment serves presence and oracle, every look with numbers
reaches the presence service as source `oracle`, and the presence service grants on it."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "oracle"))
sys.path.insert(0, str(ROOT / "lib"))
from crissa_oracle import enroll, service  # noqa: E402


def test_the_oracle_reads_inits_enrollment_beside_its_own(tmp_path):
    own = tmp_path / "oracle" / "user-face.json"
    own.parent.mkdir()
    own.write_text(json.dumps({"engine": "auraface-v1", "vectors": [[1.0, 0.0]]}))
    init = tmp_path / "enroll.json"
    init.write_text(json.dumps({"owner": "someone", "faces": [[0.0, 1.0], "not a row"]}))
    assert enroll.load(own, init) == [[1.0, 0.0], [0.0, 1.0]]
    assert enroll.load(tmp_path / "none.json", init) == [[0.0, 1.0]]
    assert enroll.load(tmp_path / "none.json", tmp_path / "none2.json") == []


def test_a_look_with_numbers_is_posted_to_presence_and_counted():
    posted = []
    cam = type("Cam", (), {"read": lambda self: "frame", "close": lambda self: None})()
    o = service.Oracle(cam=cam, look=lambda frame, enrolled: {"seen": True, "sim": 0.9, "embedding": [0.1, 0.2], "centres": []},
                       post=lambda e: posted.append(e) or True)
    st = o.tick(now=10.0)
    assert posted == [[0.1, 0.2]] and st["posted"] == 1 and st["user"] is True
    quiet = service.Oracle(cam=cam, look=lambda frame, enrolled: {"seen": False, "sim": None, "embedding": None, "centres": []},
                           post=lambda e: posted.append(e) or True)
    assert quiet.tick(now=11.0)["posted"] == 0 and posted == [[0.1, 0.2]]


def test_post_look_hits_v1_see_as_source_oracle_and_presence_grants_on_it(monkeypatch, tmp_path):
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            hits.append((self.path, body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:%d" % srv.server_port
        assert service.post_look([0.5, 0.5], url) is True
        assert hits == [("/v1/see", {"embedding": [0.5, 0.5], "source": "oracle"})]
        assert service.post_look([0.5, 0.5], "") is False and service.post_look("no", url) is False
        monkeypatch.setenv("CRISSA_PRESENCE_URL", url + "/")
        assert service.presence_url() == url and service.post_look([1.0], None) is True
    finally:
        srv.shutdown()
    import crissa_presence
    assert crissa_presence.is_ff_source("oracle") and crissa_presence.is_ff_source("oracle:desk")
