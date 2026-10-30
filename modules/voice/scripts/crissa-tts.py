#!/usr/bin/env python3
"""The voice module's TTS service (2026-10-05): Kokoro-82M on the CPU, the OpenAI speech contract.

    POST /v1/audio/speech  {"input", "voice", "response_format": "pcm"|"wav", "speed"}
                           -> 24 kHz 16-bit mono PCM (audio/pcm) or a WAV
    GET  /v1/voices        the voice ids this install has
    GET  /health

    python3 modules/voice/scripts/crissa-tts.py --install     # first use: env + models (crissa_voice.kokoro_setup)
    python3 modules/voice/scripts/crissa-tts.py [--port 8189]  # re-runs itself in the TTS env

Resident on purpose: a spoken line's player is started per line, and the model and its G2P take seconds to load.
Loopback only. No espeak: see crissa_voice/kokoro_setup.py. The model is Apache-2.0
(onnx-community/Kokoro-82M-v1.0-ONNX).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from crissa_voice import kokoro_setup as S  # noqa: E402
from crissa_voice import guard as _guard  # noqa: E402

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): any web page open in the buyer's browser could make her voice say its words. The module's
# copy of the one loopback rule (crissa_voice.guard, byte-equal to lib/crissa_guard.py) refuses, first in every do_*, a Host
# that is not this machine, a foreign Origin, and a cross-site mark with no loopback Referer; the player, the ear and
# curl send none of those and pass. A negative Content-Length (rfile.read(-1) reads until the peer closes) is refused
# too. On by default (a module is the kit's); kills CRISSA_LOOPBACK_GUARD=0 and CRISSA_BODY_LENGTH=0 (the old read).
# THE BODY'S SIZE: a line's text; 1 MiB is far past any line and was the size here before. A dial:
# CRISSA_TTS_BODY_MAX (bytes).
BODY_MAX = int(os.environ.get("CRISSA_TTS_BODY_MAX") or 1 << 20)

def body_len(headers, cap):
    """Round 46: the Content-Length to read, else None (negative, not a number, over `cap`); the kill reads as before."""
    n = _guard.body_length(headers, cap)
    if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
        n = int(headers.get("Content-Length") or 0)
    return n

RT = {"engine": None, "load_s": None}
DEFAULT_VOICE = "af_heart"


def _port() -> int:
    for k in ("CRISSA_PORT_TTS", "CRISSA_TTS_PORT"):
        v = os.environ.get(k, "").strip()
        if v.isdigit():
            return int(v)
    return 8189 + int(os.environ.get("CRISSA_PORT_OFFSET", "0") or 0)


def _peak_mb() -> float:
    """This server's peak resident memory (ru_maxrss is bytes on macOS, KiB on Linux)."""
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1 << 20) if sys.platform == "darwin" else r / 1024.0, 1)


def _wav(pcm: bytes, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        if not _guard.check(self):
            return
        path = self.path.split("?")[0]
        eng = RT["engine"]
        if path in ("/health", "/"):
            return self._json(200, {"ok": eng is not None, "engine": "kokoro-82m", "model": S.MODEL_REPO,
                                    "rev": S.MODEL_REV, "load_s": RT["load_s"], "device": "cpu", "peak_mb": _peak_mb()})
        if path in ("/v1/voices", "/voices"):
            return self._json(200, {"voices": eng.voices() if eng else []})
        self._json(404, {"ok": False})

    def do_POST(self):
        if not _guard.check(self):
            return
        if self.path.split("?")[0] not in ("/v1/audio/speech", "/audio/speech"):
            return self._json(404, {"ok": False})
        n = body_len(self.headers, BODY_MAX)
        if n is None:
            big = str(self.headers.get("Content-Length") or "").strip().isdigit()
            return self._json(413 if big else 400, {"ok": False, "error": "too large" if big else "bad Content-Length"})
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            text = str(req.get("input") or "").strip()
            voice = str(req.get("voice") or DEFAULT_VOICE).strip()
            speed = min(2.0, max(0.5, float(req.get("speed") or 1.0)))
        except (ValueError, TypeError):
            return self._json(400, {"ok": False, "error": "bad request"})
        if not text:
            return self._json(400, {"ok": False, "error": "no input"})
        t0 = time.monotonic()
        try:
            pcm = RT["engine"].synth(text, voice, speed)
        except KeyError as e:
            return self._json(400, {"ok": False, "error": str(e).strip("'\"")})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": ("%s: %s" % (type(e).__name__, e))[:240]})
        ms = "%.1f" % ((time.monotonic() - t0) * 1000.0)
        if str(req.get("response_format") or "pcm").lower() == "wav":
            return self._send(200, _wav(pcm, 24000), "audio/wav", {"X-Synth-Ms": ms})
        self._send(200, pcm, "audio/pcm", {"X-Synth-Ms": ms, "X-Sample-Rate": "24000"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kokoro-82M on the CPU with the OpenAI speech contract")
    ap.add_argument("--port", type=int, default=_port())
    ap.add_argument("--install", action="store_true", help="build the env and fetch the models, then exit")
    a = ap.parse_args(argv)
    if a.install:
        why = S.unsupported()
        if why:
            print("crissa-tts: " + why, file=sys.stderr)
            return 2
        S.install()
        return 0
    if not S.installed():
        print("crissa-tts: not installed yet; run with --install first (about 1.4 GB)", file=sys.stderr)
        return 2
    if not S.in_env():
        os.execv(str(S.env_python()), [str(S.env_python()), __file__, *sys.argv[1:]])
    os.environ["HF_HUB_OFFLINE"] = "1"             # every model is local; a line never reaches the network
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    t0 = time.monotonic()
    from crissa_voice.kokoro import Kokoro
    eng = Kokoro(S.MODEL, {a_: S.g2p_dir(a_) for a_ in S.G2P})
    eng.synth("Ready.", DEFAULT_VOICE)              # warm the session and the G2P before the first line
    RT.update(engine=eng, load_s=round(time.monotonic() - t0, 2))
    print("crissa-tts: ready in %.1fs on http://127.0.0.1:%d" % (RT["load_s"], a.port), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
