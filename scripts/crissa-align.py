#!/usr/bin/env python3
"""The pack's word aligner (better mouths, 2026-10-01): MFA on loopback, the same contract as the house's
rail-align, so house-speak needs no change.

    GET  /health                      {"ok": true, "engine": "mfa", "warm": bool}
    POST /align  {"text", "sr", "pcm16_b64" | "wav_b64"}
                                      {"ok", "words": [{"w", "a", "b", "chars", ...}], "audio_s", "ms", "engine"}

Runs with the aligner env's own python (lib/crissa_aligner installs it on first use); the model bundle
comes from HOUSE_ALIGN_MFA_BUNDLE. One alignment at a time (the engine is not re-entrant). Loopback only.

A TAKEN PORT ENDS IT AT ONCE, WITH 3 (round 17, 2026-10-13, .101 08:28: crissa-aligner.service failed "Address already
in use" every 2 s, 3,453 restarts, while the player's own copy held :8195). It binds first and warms MFA only once the
port is its own (it warmed, then failed to bind, on every one of those starts), and a port already in use exits 3, the
launcher's "port taken" code, which its unit counts as a clean end (lib/crissa_service SuccessExitStatus) and never
retries. Kill CRISSA_ALIGN_PORT_TAKEN_EXIT=0 (round 16: warm, then a traceback and exit 1, retried every 2 s).
"""
from __future__ import annotations

import argparse
import base64
import errno
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

_M: dict = {"engine": None, "warm": False, "err": None}
_LOCK = threading.Lock()
SR = 16000
PORT_TAKEN = 3                  # the launcher's code for a taken port (scripts/crissa.py exit 3)


def norm_words(text: str) -> tuple[list[str], list[int]]:
    """Lowercase a-z and apostrophe, with each kept word's index in the caller's list (rail-align's rule)."""
    out, keep = [], []
    for i, w in enumerate((text or "").split()):
        n = re.sub(r"[^a-z']", "", w.lower()).strip("'")
        if n:
            out.append(n)
            keep.append(i)
    return out, keep


def _pcm16_mono_16k(body: dict) -> bytes:
    import numpy as np
    if body.get("wav_b64"):
        with wave.open(io.BytesIO(base64.b64decode(body["wav_b64"]))) as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            raise ValueError("wav must be 16-bit")
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
    elif body.get("pcm16_b64"):
        sr = int(body.get("sr") or SR)
        x = np.frombuffer(base64.b64decode(body["pcm16_b64"]), dtype=np.int16).astype(np.float32)
    else:
        raise ValueError("need wav_b64 or pcm16_b64")
    if sr != SR and len(x):
        n = int(round(len(x) * SR / float(sr)))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


def _warm() -> None:
    try:
        import align_mfa
        eng = align_mfa.Engine()
        eng.start()
        _M.update(engine=eng, warm=True)
        print("[crissa-align] warm in %.2fs" % eng.load_s, flush=True)
    except Exception as e:  # noqa: BLE001
        _M["err"] = "%s: %s" % (type(e).__name__, e)
        print("[crissa-align] could not warm: %s" % _M["err"], file=sys.stderr, flush=True)


def align(body: dict) -> dict:
    words, keep = norm_words(str(body.get("text") or ""))
    if not words:
        return {"ok": False, "error": "no tokenizable words in text", "engine": "mfa"}
    if not _M["warm"]:
        return {"ok": False, "error": _M["err"] or "warming", "engine": "mfa"}
    pcm = _pcm16_mono_16k(body)
    with tempfile.TemporaryDirectory(prefix="crissa-align-") as d:
        path = os.path.join(d, "line.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
        t0 = time.time()
        with _LOCK:
            got = _M["engine"].align(words, keep, path)
    el = time.time() - t0
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error"), "engine": "mfa"}
    secs = len(pcm) / 2.0 / SR
    return {"ok": True, "words": got["words"], "audio_s": round(secs, 3), "ms": round(el * 1000.0, 1),
            "rt": round(secs / max(1e-6, el), 1), "engine": "mfa"}


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
        if self.path.split("?")[0] == "/health":
            return self._json(200, {"ok": True, "engine": "mfa", "warm": bool(_M["warm"]), "error": _M["err"]})
        self._json(404, {"ok": False})

    def do_POST(self):
        if self.path.split("?")[0] != "/align":
            return self._json(404, {"ok": False})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024 * 1024:
                return self._json(413, {"ok": False, "error": "too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            return self._json(200, align(body))
        except Exception as e:  # noqa: BLE001
            return self._json(200, {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]), "engine": "mfa"})


def _leave_with(parent: int, every_s: float = 1.0) -> None:
    """THE ALIGNER LEAVES WITH ITS PARENT (2026-10-05, egress audit): the player starts this in a
    session of its own, so a player that was killed left it listening on its port, with its MFA child, for good.
    When `parent` is gone: the warm MFA child (a spawn-context daemon process) first, then the whole group when this
    process leads one (it does when the player started it), then this process."""
    import multiprocessing as mp
    import signal
    while True:
        time.sleep(every_s)
        try:
            os.kill(parent, 0)
            if os.getppid() == parent:
                continue
        except ProcessLookupError:
            pass
        except PermissionError:
            continue
        print("[crissa-align] parent %d is gone: leaving" % parent, file=sys.stderr, flush=True)
        for c in mp.active_children():
            c.terminate()
        if os.getpgrp() == os.getpid():
            os.killpg(os.getpid(), signal.SIGTERM)     # this server sets no SIGTERM handler: the default ends it too
        os._exit(0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the pack's word aligner (MFA) on loopback")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CRISSA_ALIGN_PORT", "8195")))
    ap.add_argument("--parent", type=int, default=0, help="leave when this pid is gone (the player that started it)")
    a = ap.parse_args(argv)
    if a.parent > 0:
        threading.Thread(target=_leave_with, args=(a.parent,), daemon=True, name="leave-with-parent").start()
    if os.environ.get("CRISSA_ALIGN_PORT_TAKEN_EXIT", "1").strip().lower() in ("0", "off", "false", "no"):
        threading.Thread(target=_warm, daemon=True).start()
        ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
        return 0
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print("[crissa-align] port %d is already in use (another aligner, or another program): not starting a second "
              "one (exit %d)" % (a.port, PORT_TAKEN), file=sys.stderr, flush=True)
        return PORT_TAKEN
    threading.Thread(target=_warm, daemon=True).start()
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
