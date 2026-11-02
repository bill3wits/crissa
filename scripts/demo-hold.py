#!/usr/bin/env python3
"""Loopback switch for Dev mode, the tuning demo, and Record your lines.

Bind is 127.0.0.1 only. POST {"mode": true|false} opens or closes the
gate. {"demo": true|false} starts or stops the scripted lap, and only
while Dev mode is on. {"hold": true|false} and {"dev": true|false} are
the pause and the extra beats. GET /v1/demo-hold returns the status the
panel paints. GET /v1/demo-lines lists each question and whether a wav
is saved. POST a mono 16-bit wav with the header X-Demo-Line to store
one question. GET /v1/demo-lines/<id> returns that wav for playback.
Stopping writes the lap's off file. This process does not pause
Listening and does not home the wall.
"""
from __future__ import annotations

import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import demo_hold  # noqa: E402
import demo_lines  # noqa: E402

from urllib.parse import urlparse

# A machine may also reach the panel by its own LAN names: config/house-machine.json `panel_origins`
# (comma-separated origins; the house's file names its two). A pack carries no such file: loopback only.
try:
    import crissa_home as _zh
    _ORIGINS = {o.strip() for o in _zh.house_value("panel_origins").split(",") if o.strip()}
except Exception:  # noqa: BLE001
    _ORIGINS = set()


def _origin_ok(origin: str) -> bool:
    """Loopback pages only (the panel and the wall are on this machine), or one of the house's named hosts."""
    if origin in _ORIGINS:
        return True
    try:
        host = (urlparse(origin).hostname or "").lower()
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _cors(self) -> None:
        origin = self.headers.get("Origin") or ""
        if _origin_ok(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Demo-Line")

    def _json(self, code: int, body: dict) -> None:
        raw = (json.dumps(body) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def _read_body(self, limit: int) -> bytes | None:
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._json(400, {"ok": False, "error": "bad length"})
            return None
        if n < 0 or n > limit:
            self._json(413, {"ok": False, "error": "too large"})
            return None
        return self.rfile.read(n)

    def _line_id(self) -> str:
        raw = self.headers.get("X-Demo-Line") or ""
        if not raw and "?" in self.path:
            query = self.path.split("?", 1)[1]
            for part in query.split("&"):
                if part.startswith("id="):
                    raw = part[3:]
                    break
        return raw.strip()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/v1/demo-hold":
            return self._json(200, demo_hold.status())
        if path == "/v1/demo-lines":
            return self._json(200, demo_lines.list_lines())
        prefix = "/v1/demo-lines/"
        if path.startswith(prefix):
            blob = demo_lines.wav_bytes(path[len(prefix):])
            if blob is None:
                return self._json(404, {"ok": False, "error": "no recording"})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(blob)
            return
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/v1/demo-hold":
            raw = self._read_body(4096)
            if raw is None:
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json(400, {"ok": False, "error": "bad json"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "want an object"})
            return self._json(200, demo_hold.apply_body(body))
        if path == "/v1/demo-lines":
            beat_id = self._line_id()
            blob = self._read_body(3_000_000)
            if blob is None:
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "application/json":
                try:
                    body = json.loads(blob or b"{}")
                except ValueError:
                    return self._json(400, {"ok": False, "error": "bad json"})
                if not isinstance(body, dict):
                    return self._json(400, {"ok": False, "error": "want an object"})
                beat_id = str(body.get("id") or beat_id)
                try:
                    blob = base64.b64decode(body.get("wav") or "", validate=True)
                except ValueError:
                    return self._json(400, {"ok": False, "error": "bad wav"})
            saved = demo_lines.save_upload(beat_id, blob)
            return self._json(200 if saved.get("ok") else 422, saved)
        return self._json(404, {"ok": False, "error": "not found"})


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", demo_hold.PORT), Handler)
    print(f"demo-hold on 127.0.0.1:{demo_hold.PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
