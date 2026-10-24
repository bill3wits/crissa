from __future__ import annotations

from . import settings as _S  # this install's addresses (the shared port table, 2026-10-10)
import argparse, json, sys, subprocess
import numpy as np
from .pipeline import turn_pcm16, play_pcm, stt_pcm16, llm_reply, tts_pcm
from . import config as C

def record_seconds(seconds: float, sr: int = 16000) -> bytes:
    """Capture: Pulse default source (should be Link). Selection owned by rail-stt.

    TTS playback may use echocancel_sink via voice-broker — unrelated to this mic path.
    """
    n = int(seconds * sr)
    cmd = ["parec", "--format=s16le", f"--rate={sr}", "--channels=1", "--latency-msec=20"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert p.stdout
    raw = p.stdout.read(n * 2)
    p.terminate()
    try:
        p.wait(timeout=1)
    except Exception:
        p.kill()
    return raw

def cmd_once(args):
    if getattr(args, "fixture", None):
        # Spine smoke: file STT path via rail-stt (Parakeet dual) without live mic
        import json as _json
        import urllib.request
        from pathlib import Path
        path = str(Path(args.fixture).expanduser().resolve())
        print(f"[rail-voice] fixture STT {path}…", flush=True)
        req = urllib.request.Request(
            C.STT_URL.replace("/stt", "/v1/file"),
            data=_json.dumps({"path": path}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            stt = _json.loads(r.read().decode())
        text = (stt.get("text") or "").strip()
        person_id = stt.get("person_id")
        stamp = stt.get("stamp") or {}
        presence = stamp.get("presence")
        if not text:
            out = {"text": "", "reply": "", "pcm": b"", "sr": 24000, "reason": "empty_stt",
                   "person_id": person_id, "stamp": stamp}
        else:
            reply = llm_reply(text, person_id=person_id, presence=presence)
            voice = getattr(args, "voice", None)
            audio, asr = tts_pcm(reply, voice=voice)
            out = {"text": text, "reply": reply, "pcm": audio, "sr": asr, "reason": "ok",
                   "person_id": person_id, "stamp": stamp}
    else:
        print(f"[rail-voice] recording {args.seconds}s…", flush=True)
        pcm = record_seconds(args.seconds)
        out = turn_pcm16(pcm)
    print(f"YOU: {out['text']}", flush=True)
    print(f"BOT: {out['reply']}", flush=True)
    print(f"person_id: {out.get('person_id')}", flush=True)
    if out["pcm"] and not args.no_play:
        play_pcm(out["pcm"], out["sr"])
    if args.json:
        print(json.dumps({
            k: out[k] for k in ("text", "reply", "reason", "person_id")
        } | {"pcm_bytes": len(out.get("pcm") or b""), "sr": out.get("sr")}))
    return 0 if out["reason"] == "ok" and out.get("reply") else 1

def cmd_text(args):
    reply = llm_reply(args.text)
    print(reply)
    pcm, sr = tts_pcm(reply, voice=getattr(args, "voice", None))
    if not args.no_play:
        play_pcm(pcm, sr)
    if getattr(args, "json", False):
        print(json.dumps({"reply": reply, "pcm_bytes": len(pcm), "sr": sr}))
    return 0 if reply else 1

# THIS MACHINE ONLY (round 46, the local review of kit crissa-063a2813, 2026-10-23): `serve` took a turn's PCM from any
# page in the buyer's browser. The shared loopback guard (guard.py) runs first in every handler; kill
# CRISSA_LOOPBACK_GUARD=0. Its body is one utterance of PCM16: at most BODY_MAX_S seconds (120 = the ear's longest take,
# crissa_ear.audio.record_until_enter) at the highest rate the ear's routes accept (96 kHz); a larger, negative or
# non-numeric Content-Length is refused 400 unread. Dial: CRISSA_EAR_BODY_MAX_S (0 = no size cap).
def _body_cap():
    import os
    try:
        s = float(os.environ.get("CRISSA_EAR_BODY_MAX_S", "120") or 120)
    except ValueError:
        s = 120.0
    return int(s * 96000 * 2) if s > 0 else None


def make_serve_handler():
    """The `serve` command's handler class (a function so a test can serve it on a spare port)."""
    from http.server import BaseHTTPRequestHandler
    from . import guard as _guard
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if not _guard.check(self):
                return
            if self.path == "/health":
                body = json.dumps({"ok": True, "service": "rail-voice",
                                   "stt": C.STT_URL, "llm": C.LLM_URL, "tts": C.TTS_URL}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()
        def do_POST(self):
            if not _guard.check(self):
                return
            if self.path not in ("/v1/turn", "/turn"):
                self.send_response(404); self.end_headers(); return
            n = _guard.body_length(self.headers, _body_cap())
            if n is None:
                self.close_connection = True
                self.send_response(400); self.end_headers(); return
            sr = int(self.headers.get("X-Sample-Rate", "16000"))
            pcm = self.rfile.read(n)
            out = turn_pcm16(pcm, sr)
            # return json meta + optional omit pcm
            body = json.dumps({"text": out["text"], "reply": out["reply"], "reason": out["reason"],
                               "sr": out["sr"], "pcm_bytes": len(out["pcm"])}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    return H


def cmd_serve(args):
    from http.server import ThreadingHTTPServer
    H = make_serve_handler()
    print(f"[rail-voice] http://127.0.0.1:{args.port}/health", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()

def main(argv=None):
    p = argparse.ArgumentParser(prog="rail-voice")
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("once"); o.add_argument("--seconds", type=float, default=5)
    o.add_argument("--fixture", help="wav path via rail-stt /v1/file (spine smoke, no mic)")
    o.add_argument("--voice", default=None, help="Cosy voice id (zoe, vera, …)")
    o.add_argument("--no-play", action="store_true"); o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_once)
    t = sub.add_parser("text"); t.add_argument("text"); t.add_argument("--no-play", action="store_true")
    t.add_argument("--voice", default=None); t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_text)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=_S.port("bridge"))
    s.set_defaults(func=cmd_serve)
    args = p.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
