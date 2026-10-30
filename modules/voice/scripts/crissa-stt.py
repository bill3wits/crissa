#!/usr/bin/env python3
"""The voice module's speech-to-text service (2026-10-01): the microphone in, turns out.

    GET  /health                 {"ok", "asr", "listening", "mic", "stats"}
    GET  /v1/turns?n=30          {"turns": [newest first], "count"}  - what scripts/crissa-ear.py reads
    POST /stt                    raw int16 PCM (X-Sample-Rate) -> the ASR's answer, passed through
    POST /v1/mic                 raw int16 PCM (X-Sample-Rate) from the Wall page   (--mic page only)

    python3 modules/voice/scripts/crissa-stt.py [--port 8190] [--asr http://127.0.0.1:8096]
                                                [--player http://127.0.0.1:8100] [--device NAME|INDEX]
                                                [--mic host|page]

The defaults follow the shared port table (CRISSA_PORT_STT / _ASR / _PLAYER, CRISSA_PORT_OFFSET); a port
already taken is one line on stderr and exit 2.

--mic page (2026-10-05): the microphone is the Wall page's (getUserMedia, the browser's echo
canceller on); its frames arrive through the player's /v1/mic. `host` (the default) is this
machine's input device through sounddevice, as before. CRISSA_AUDIO_AT sets the default.

Then: python3 scripts/crissa-ear.py --turns http://127.0.0.1:8190/v1/turns
The ASR is any service with the /stt contract; modules/voice/scripts/crissa-asr.py is the bundled one.
Loopback only. Turns are kept in memory (the last 200), never written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# A MAC'S SYSTEM PROXY NEVER CARRIES A LOOPBACK CALL (2026-10-04): urllib applies macOS's proxy settings to
# 127.0.0.1. Any *_proxy in the environment replaces the system settings outright on macOS (urllib reads the
# environment first), so a Mac that needs a proxy to go out sets HTTPS_PROXY beside this. Kill CRISSA_NO_PROXY=0.
if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":
    for _k in ("NO_PROXY", "no_proxy"):
        os.environ.setdefault(_k, "127.0.0.1,localhost")


# THE SHARED PORT TABLE, READ INLINE (lib/crissa_ports, 2026-10-06 crissatest: this ignored --port-offset). This module
# installs on its own, so the three rules are repeated here: CRISSA_PORT_<NAME>, the older variable, else the
# default + CRISSA_PORT_OFFSET.
_DEFAULT_PORTS = {"player": 8100, "asr": 8096, "stt": 8190, "oracle": 8197}
_LEGACY_PORTS = {"player": "HOUSE_CONDUCTOR_PORT", "oracle": "CRISSA_ORACLE_PORT"}


def _port(name: str, env=None) -> int:
    e = os.environ if env is None else env
    for k in ("CRISSA_PORT_" + name.upper(), _LEGACY_PORTS.get(name)):
        v = str(e.get(k) or "").strip() if k else ""
        if v.isdigit() and 0 < int(v) < 65536:
            return int(v)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    return _DEFAULT_PORTS[name] + off


def _bind(name: str, port: int, handler, how: str = "--port N"):
    """The server on 127.0.0.1:port, or None after one clear line when the port is taken (never a traceback)."""
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        # the developer's ways round it first (kept in the log), the plain line last: it is the one a status shows
        print("%s: (for developers) another port: %s, CRISSA_PORT_%s=N or CRISSA_PORT_OFFSET=N" % (
              name, how, name.split("-")[-1].upper()), file=sys.stderr, flush=True)
        print("%s: port %d is already in use (%s): another copy may be running. Stop it, then start Crissa again."
              % (name, port, e.strerror or e), file=sys.stderr, flush=True)
        return None


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crissa_voice import egress  # noqa: E402
from crissa_voice.listen import Listener, PushedFrames, asr_client, mic_frames, player_speaking  # noqa: E402
from crissa_voice import guard as _guard  # noqa: E402

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): any web page open in the buyer's browser could read the ear's turns (/v1/turns, what was said in the room)
# and post audio into her ear (/v1/mic, /stt). The module's
# copy of the one loopback rule (crissa_voice.guard, byte-equal to lib/crissa_guard.py) refuses, first in every do_*, a Host
# that is not this machine, a foreign Origin, and a cross-site mark with no loopback Referer; the player, the ear and
# curl send none of those and pass. A negative Content-Length (rfile.read(-1) reads until the peer closes) is refused
# too. On by default (a module is the kit's); kills CRISSA_LOOPBACK_GUARD=0 and CRISSA_BODY_LENGTH=0 (the old read).
# THE BODY'S SIZE: /stt goes on to the ASR, which refuses past 64 MiB (crissa-asr.py ASR_BODY_MAX), so nothing bigger is
# worth carrying to it; /v1/mic is one page chunk, far under. A dial: CRISSA_STT_BODY_MAX (bytes).
BODY_MAX = int(os.environ.get("CRISSA_STT_BODY_MAX") or 64 * 1024 * 1024)

def body_len(headers, cap):
    """Round 46: the Content-Length to read, else None (negative, not a number, over `cap`); the kill reads as before."""
    n = _guard.body_length(headers, cap)
    if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
        n = int(headers.get("Content-Length") or 0)
    return n

TURNS: deque = deque(maxlen=200)
STATE: dict = {"listener": None, "asr": None, "listening": False, "error": None, "mic": "host", "pushed": None}


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not _guard.check(self):
                return
            u = urlparse(self.path)
            if u.path == "/health":
                ls = STATE["listener"]
                pushed = STATE["pushed"]
                return self._json(200, {"ok": True, "asr": STATE["asr"], "listening": STATE["listening"],
                                        "error": STATE["error"], "mic": STATE["mic"],
                                        "stats": ls.stats if ls else {},
                                        "page_mic": dict(pushed.stats) if pushed else None})
            if u.path == "/v1/turns":
                n = max(1, min(200, int((parse_qs(u.query).get("n") or ["30"])[0])))
                rows = list(TURNS)[-n:][::-1]
                return self._json(200, {"turns": rows, "count": len(rows)})
            self._json(404, {"ok": False})

        def do_POST(self):
            if not _guard.check(self):
                return
            path = urlparse(self.path).path
            n = body_len(self.headers, BODY_MAX)
            if n is None:
                return self._json(413 if str(self.headers.get("Content-Length") or "").strip().isdigit() else 400,
                                  {"ok": False, "error": "bad Content-Length"})
            if path == "/v1/mic":
                body = self.rfile.read(n)
                pushed = STATE["pushed"]
                if pushed is None:
                    return self._json(404, {"ok": False, "error": "this service listens on the host (--mic host)"})
                try:
                    sr = int(self.headers.get("X-Sample-Rate") or 16000)
                except ValueError:
                    return self._json(400, {"ok": False, "error": "X-Sample-Rate is a number"})
                if not 8000 <= sr <= 96000:
                    return self._json(400, {"ok": False, "error": "X-Sample-Rate 8000-96000"})
                return self._json(200, {"ok": True, "frames": pushed.push(body, sr)})
            if path != "/stt":
                return self._json(404, {"ok": False})
            body = self.rfile.read(n)
            req = urllib.request.Request(STATE["asr"].rstrip("/") + "/stt", data=body, method="POST",
                                         headers={"X-Sample-Rate": self.headers.get("X-Sample-Rate") or "16000"})
            try:
                with egress.urlopen(req, 60, "the ASR") as r:
                    return self._json(200, json.loads(r.read() or b"{}"))
            except egress.Refused as e:
                return self._json(403, {"ok": False, "error": str(e)})
            except OSError as e:
                return self._json(502, {"ok": False, "error": "the ASR is not answering: %s" % e})
    return H


def start_listening(asr: str, player: str, device, frames=None) -> None:
    ls = Listener(asr_client(asr), TURNS.append, speaking=player_speaking(player))
    STATE["listener"] = ls

    def go():
        try:
            STATE["listening"] = True
            ls.run(frames if frames is not None else mic_frames(device))
        except Exception as e:  # noqa: BLE001  no microphone: the service still answers and says why
            STATE["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        finally:
            STATE["listening"] = False
    threading.Thread(target=go, name="crissa-listen", daemon=True).start()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the voice module's speech-to-text service")
    ap.add_argument("--port", type=int, default=_port("stt"))
    ap.add_argument("--asr", default="http://127.0.0.1:%d" % _port("asr"))
    ap.add_argument("--player", default="http://127.0.0.1:%d" % _port("player"))
    ap.add_argument("--device", default=None, help="input device name or index (default: the system's)")
    ap.add_argument("--no-mic", action="store_true", help="serve /stt and /v1/turns without listening")
    ap.add_argument("--mic", choices=("host", "page"),
                    default="page" if os.environ.get("CRISSA_AUDIO_AT", "").strip().lower() == "page" else "host",
                    help="host: this machine's input device; page: the Wall page posts it (/v1/mic)")
    a = ap.parse_args(argv)
    srv = _bind("crissa-stt", a.port, make_handler())     # before the microphone opens: a taken port says so, once
    if srv is None:
        return 2
    STATE["asr"] = a.asr
    STATE["mic"] = "off" if a.no_mic else a.mic
    if a.no_mic:
        pass
    elif a.mic == "page":
        STATE["pushed"] = PushedFrames()
        start_listening(a.asr, a.player, None, frames=STATE["pushed"])
    else:
        dev = int(a.device) if a.device and a.device.isdigit() else a.device
        start_listening(a.asr, a.player, dev)
    print("crissa-stt: http://127.0.0.1:%d  (turns at /v1/turns, ASR %s)" % (a.port, a.asr), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
