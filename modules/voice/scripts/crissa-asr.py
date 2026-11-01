#!/usr/bin/env python3
"""The voice module's ASR service (2026-10-01): ARK-ASR 0.6B INT8 ONNX on the CPU, the /stt contract.

    POST /stt   raw int16 mono PCM (X-Sample-Rate, default 16000) -> {"text", "duration_s", "decode_ms", "engine"}
    GET  /health

    python3 modules/voice/scripts/crissa-asr.py --install     # first use: env + model (crissa_voice.asr_setup)
    python3 modules/voice/scripts/crissa-asr.py [--port 8096]  # re-runs itself in the ASR env

The port follows the shared table (CRISSA_PORT_ASR, CRISSA_PORT_OFFSET); a taken one is one line and exit 2.

Loopback only. The model is Apache-2.0 (Edge0/ark-asr-0.6b-int8-onnx); see crissa_voice/asr_setup.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from crissa_voice import asr_setup as S  # noqa: E402
from crissa_voice import guard as _guard  # noqa: E402

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): any web page open in the buyer's browser could post audio here and read back the words. The module's
# copy of the one loopback rule (crissa_voice.guard, byte-equal to lib/crissa_guard.py) refuses, first in every do_*, a Host
# that is not this machine, a foreign Origin, and a cross-site mark with no loopback Referer; the player, the ear and
# curl send none of those and pass. A negative Content-Length (rfile.read(-1) reads until the peer closes) is refused
# too. On by default (a module is the kit's); kills CRISSA_LOOPBACK_GUARD=0 and CRISSA_BODY_LENGTH=0 (the old read).
# THE BODY'S SIZE: 64 MiB of 16-bit mono is over half an hour at 16 kHz, past any turn; it was the size here before. A
# dial: CRISSA_ASR_BODY_MAX (bytes).
ASR_BODY_MAX = int(os.environ.get("CRISSA_ASR_BODY_MAX") or 64 * 1024 * 1024)

def body_len(headers, cap):
    """Round 46: the Content-Length to read, else None (negative, not a number, over `cap`); the kill reads as before."""
    n = _guard.body_length(headers, cap)
    if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
        n = int(headers.get("Content-Length") or 0)
    return n

RT = {"rt": None, "load_s": None}
# ONE DECODE AT A TIME, ON A FEW THREADS (round 13, 2026-10-12, .101): ARK-ASR's runtime opens its ONNX sessions with
# default options - every core, and ORT's spinning threads busy-wait between ops - and this server is threaded, so the
# ear's turns decoded side by side. On .101 (16 threads) the ASR ran at 677 % while she read Moby-Dick and the player's
# mouth lost half its rows. Now: CRISSA_ASR_THREADS intra-op threads (default: a quarter of the CPUs, 2 to 4), one
# inter-op thread, no spinning, torch on the same count, and one decode at a time (the ear's turns queue).
# Kill: CRISSA_ASR_THREADS=0 (the runtime's own defaults, and decodes side by side as before).
import threading  # noqa: E402

DECODE_LOCK = threading.Lock()

# A LOOK FOR THE FIRST WORD OVER HER LINE (round 17, the User 2026-10-13: "for barge, can we send signal DIRECTLY from
# ASR on word penetration?"). The ear's barge probe (modules/ear/crissa_ear/barge.py) sends the newest 0.6-1.5 s of an
# utterance said over her line, with the header X-Crissa-Probe: 1. A look never waits behind a whole utterance's decode
# (one 4 s piece of her echo is ~1.1 s on one core): it takes its own lock (one look at a time), runs beside the decode in
# flight on the same sessions (their thread pools are shared, so the CPU stays within two sessions' threads for the
# look's 0.2-0.5 s), and decodes at most PROBE_TOKENS tokens. Measured 2026-10-13, one core (taskset), warm: 0.6 s of
# speech 213 ms, 0.9 s 250-280 ms, 1.2 s 390 ms, 4 s 1.07 s (rtf 0.25-0.35; four threads were no faster: 215/236/340 ms;
# .101's warm rtf 0.4 is ~1.3x these). Kill: CRISSA_ASR_PROBE_LANE=0 (a look queues behind the decodes like any request).
PROBE_LOCK = threading.Lock()
PROBE_TOKENS = int(os.environ.get("CRISSA_ASR_PROBE_TOKENS", "24") or 24)


def probe_lane_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_ASR_PROBE_LANE", "1")).strip().lower() not in ("0", "false", "no", "off")


def asr_threads(env=None, cpus: int | None = None) -> int:
    """The intra-op thread count, or 0 for the runtime's own defaults (the kill)."""
    e = os.environ if env is None else env
    raw = str(e.get("CRISSA_ASR_THREADS", "")).strip()
    if raw.isdigit():
        return int(raw)
    n = int(cpus if cpus is not None else (os.cpu_count() or 4))
    return max(2, min(4, n // 4))


def capped_session_options(ort, threads: int):
    """SessionOptions for one ARK-ASR session on `threads` threads: no spinning, one inter-op thread."""
    o = ort.SessionOptions()
    o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    o.intra_op_num_threads = int(threads)
    o.inter_op_num_threads = 1
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    for k in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
        try:
            o.add_session_config_entry(k, "0")
        except Exception:  # noqa: BLE001 - an older runtime without the key keeps spinning
            pass
    return o


def cap_runtime(mod, threads: int) -> bool:
    """Replace the runtime module's load_session with one on capped options (the module's own code is the model's,
    fetched by sha, so it is not edited). False when the module has no load_session to replace."""
    if threads <= 0 or not hasattr(mod, "load_session"):
        return False
    ort = mod.ort

    def load_session(model_path):
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ort.InferenceSession(str(model_path), sess_options=capped_session_options(ort, threads),
                                    providers=providers)

    mod.load_session = load_session
    try:
        import torch
        torch.set_num_threads(int(threads))
        torch.set_num_interop_threads(1)
    except Exception:  # noqa: BLE001 - torch already started its pools, or is absent
        pass
    return True


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


def transcribe(pcm: bytes, sr: int, probe: bool = False) -> dict:
    t0 = time.monotonic()
    n = len(pcm) // 2
    dur = n / float(max(sr, 1))
    out = {"text": "", "duration_s": round(dur, 2), "decode_ms": 0.0, "engine": "ark-asr-0.6b-int8"}
    probe = bool(probe) and probe_lane_on()
    if probe:
        out["probe"] = True
    if n < int(0.05 * sr):
        return out
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="crissa-asr-")
    os.close(fd)
    try:
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(sr)); w.writeframes(pcm)
        lock = PROBE_LOCK if probe else (DECODE_LOCK if asr_threads() > 0 else None)
        if lock is not None:
            lock.acquire()
        try:
            text = RT["rt"].transcribe(audio_path=path, max_new_tokens=PROBE_TOKENS if probe else 128,
                                       max_audio_seconds=min(30.0, max(1.0, dur + 0.5)), precision="int8",
                                       asr_block_token_id_from=151670)
        finally:
            if lock is not None:
                lock.release()
        out["text"] = (text or "").strip()
    except Exception as e:  # noqa: BLE001
        out["error"] = ("%s: %s" % (type(e).__name__, e))[:240]
    finally:
        os.unlink(path)
    out["decode_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            # .101 round 30: a caller that gave up (a health check while she warms) is not a traceback in her log
            print("crissa-asr: %s hung up before the answer" % (self.path or "?"), flush=True)

    def do_GET(self):
        if not _guard.check(self):
            return
        if self.path.split("?")[0] in ("/health", "/"):
            return self._json(200, {"ok": RT["rt"] is not None, "engine": "ark-asr-0.6b-int8", "model": S.MODEL_REPO,
                                    "rev": S.MODEL_REV, "load_s": RT["load_s"], "device": "cpu"})
        self._json(404, {"ok": False})

    def do_POST(self):
        if not _guard.check(self):
            return
        if self.path.split("?")[0] not in ("/stt", "/transcribe"):
            return self._json(404, {"ok": False})
        n = body_len(self.headers, ASR_BODY_MAX)
        if n is None:
            big = str(self.headers.get("Content-Length") or "").strip().isdigit()
            return self._json(413 if big else 400, {"ok": False, "error": "too large" if big else "bad Content-Length"})
        self._json(200, transcribe(self.rfile.read(n), int(self.headers.get("X-Sample-Rate") or 16000),
                                   probe=str(self.headers.get("X-Crissa-Probe") or "").strip() == "1"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ARK-ASR on the CPU with the /stt contract")
    ap.add_argument("--port", type=int, default=_port("asr"))
    ap.add_argument("--install", action="store_true", help="build the env and fetch the model, then exit")
    a = ap.parse_args(argv)
    if a.install:
        why = S.unsupported()
        if why:
            print("crissa-asr: " + why, file=sys.stderr)
            return 2
        S.install()
        return 0
    if not S.installed():
        print("crissa-asr: not installed yet; run with --install first (about 2.7 GB)", file=sys.stderr)
        return 2
    if not S.in_env():
        os.execv(str(S.env_python()), [str(S.env_python()), __file__, *sys.argv[1:]])
    srv = _bind("crissa-asr", a.port, H)                 # before the model loads: a taken port says so at once
    if srv is None:
        return 2
    t0 = time.monotonic()
    sys.path.insert(0, str(S.MODEL))
    os.environ.setdefault("HF_HOME", str(S.ROOT / "hf"))
    import infer_ark_audio_onnx
    n = asr_threads()
    capped = cap_runtime(infer_ark_audio_onnx, n)
    RT.update(rt=infer_ark_audio_onnx.ArkAsrOnnxRuntime(S.ROOT), load_s=round(time.monotonic() - t0, 2))
    print("crissa-asr: ready in %.1fs on http://127.0.0.1:%d (%s)" % (
        RT["load_s"], a.port, ("%d threads, no spinning, one decode at a time" % n) if capped
        else "the runtime's own thread defaults"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
