#!/usr/bin/env python3
"""The voice module's Turbo service (2026-10-06): Chatterbox-Turbo on an NVIDIA GPU, on loopback, the house's /stream contract.

    POST /stream           {"text", "voice"[, "seed", "chunk_first_min", "chunk_min", "chunk_max"]} -> chunked 16-bit
                           mono PCM at 24 kHz (X-Sample-Rate): the contract house-speak's streamed path speaks, so a pack
                           whose voice tier is Turbo streams through the same code as Pocket
    POST /abort | /clear   barge-in: stop within one token and drop every queued clip
    POST /warm             {"voice"}: a silent render
    POST /reload_voices    re-read the voice clips (alias /register)
    GET  /health

    python3 modules/voice/scripts/crissa-turbo.py --install
    python3 modules/voice/scripts/crissa-turbo.py [--port 8186]     # re-runs itself in the Turbo env
    python3 modules/voice/scripts/crissa-turbo.py --self-test       # the install's self-test again

SENTENCE-CHUNKED, NOT STREAMED: a line is cut into ~80-character sentence chunks (crissa_voice.chunker; 80 was the
smallest size that still sounded natural, 40 sounded choppy), each chunk renders as a whole clip, and clips go out in
order over one response while the next renders. Measured on the reference house: first clip 0.3-0.5 s, each next clip
ready in 0.55 s or less.

VOICES: a face's own clip from <crissa home>/turbo/voices when the person put one there;
none ship. A face with no clip speaks with Turbo's built-in voice and the log says so once per face.

WATERMARK (turbo.settings.watermark, default true; CRISSA_TURBO_WATERMARK=0|1 wins): Resemble's Perth watermark on
every clip, as upstream. /health and the start-up log say which. Logs carry numbers only, never the spoken text.

GPU room (turbo.settings): vram_cap_gb caps this process's share of the card; it refuses to start with less than
vram_cap_gb + headroom_gb free and says how much it saw. Port: the shared table's `turbo` (CRISSA_PORT_TURBO, else
8186 + CRISSA_PORT_OFFSET). Loopback only; every model file is local and checked.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from crissa_voice import chunker, turbo_setup as S  # noqa: E402
from crissa_voice import guard as _guard  # noqa: E402

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): any web page open in the buyer's browser could make her speak or stop her (the
# multilingual tier serves this same handler). The module's
# copy of the one loopback rule (crissa_voice.guard, byte-equal to lib/crissa_guard.py) refuses, first in every do_*, a Host
# that is not this machine, a foreign Origin, and a cross-site mark with no loopback Referer; the player, the ear and
# curl send none of those and pass. A negative Content-Length (rfile.read(-1) reads until the peer closes) is refused
# too. On by default (a module is the kit's); kills CRISSA_LOOPBACK_GUARD=0 and CRISSA_BODY_LENGTH=0 (the old read).
# THE BODY'S SIZE: a line's text and its knobs; 1 MiB is far past any line and was the size here before. A dial:
# CRISSA_TURBO_BODY_MAX (bytes).
BODY_MAX = int(os.environ.get("CRISSA_TURBO_BODY_MAX") or 1 << 20)

def body_len(headers, cap):
    """Round 46: the Content-Length to read, else None (negative, not a number, over `cap`); the kill reads as before."""
    n = _guard.body_length(headers, cap)
    if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
        n = int(headers.get("Content-Length") or 0)
    return n

SR = 24000
WRITE_BYTES = 9600   # 0.2 s per HTTP chunk
GiB = 1024 ** 3
DEFAULTS = {"watermark": True, "chunk_first_min": 0, "chunk_min": 0, "chunk_max": 80, "temperature": 0.8,
            "seed": 0, "vram_cap_gb": 4.5, "headroom_gb": 1.5}
# Which voice this process serves. crissa-multilingual.py swaps PROFILE, S, DEFAULTS, load and render and calls
# main(): one server, two models (the HTTP contract, chunking, barge-in and the job log are the same code).
PROFILE = {"tier": "turbo", "svc": "crissa-turbo", "engine": "chatterbox-turbo", "key": "turbo",
           "port_env": "CRISSA_PORT_TURBO", "port": 8186, "watermark_env": "CRISSA_TURBO_WATERMARK",
           "script": __file__, "label": "Turbo"}
RT = {"model": None, "load_s": None, "settings": dict(DEFAULTS), "last_job": None, "builtin": None, "said": set(), "face_why": {}}
_ABORT = threading.Event()
_LOCK = threading.Lock()
_CONDS: dict = {}   # face -> (clip signature, Conditionals)


def _port() -> int:
    v = os.environ.get(PROFILE["port_env"], "").strip()
    if v.isdigit():
        return int(v)
    return int(PROFILE["port"]) + int(os.environ.get("CRISSA_PORT_OFFSET", "0") or 0)


def _log(**kv):
    print(json.dumps({"t": round(time.time(), 3), "svc": PROFILE["svc"], **kv}), flush=True)


def settings(repo_root: Path | None = None, home: Path | None = None, env=None) -> dict:
    """DEFAULTS, then the tier table's turbo.settings (the install's config, then the state-home copy), then
    CRISSA_TURBO_WATERMARK from the environment."""
    e = os.environ if env is None else env
    out = dict(DEFAULTS)
    root = repo_root if repo_root is not None else HERE.parent.parent
    for p in (root / "config" / "crissa-voice-tiers.json", (home or S._home()) / "crissa-voice-tiers.json"):
        try:
            got = ((json.loads(p.read_text(encoding="utf-8")).get("tiers") or {}).get(PROFILE["tier"]) or {}).get("settings")
        except (OSError, ValueError, AttributeError):
            continue
        out.update(got or {})
    w = str(e.get(PROFILE["watermark_env"], "")).strip().lower()
    if w in ("0", "1", "true", "false", "on", "off", "yes", "no"):
        out["watermark"] = w in ("1", "true", "on", "yes")
    return out


def _speakable():
    """lib/speakable.py when the module sits in a Crissa install: numbers, times and money read as words (Turbo has
    no text front end of its own)."""
    p = HERE.parent.parent / "lib" / "speakable.py"
    if not p.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("crissa_speakable", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001  plain text beats no voice
        return None


SP = None


def prepare_text(text: str) -> str:
    t = text or ""
    if SP is not None and hasattr(SP, "restore_name_spellings"):
        t = SP.restore_name_spellings(t)     # Turbo drops mouth respellings: speak the plain name
    if SP is not None and hasattr(SP, "numbers_to_words"):
        t = SP.numbers_to_words(t)
    return re.sub(r"\s+", " ", t).strip()


def chunks_for(text: str, knobs: dict | None = None, st: dict | None = None) -> list[str]:
    s, k = st or RT["settings"], knobs or {}
    return chunker.chunk_text(prepare_text(text), int(k.get("first_min", s["chunk_first_min"])),
                              int(k.get("min", s["chunk_min"])), int(k.get("max", s["chunk_max"])), 0)


class Aborted(Exception):
    pass


def _abortable_range(it, *a, **k):
    """Stands in for tqdm in the T3 token loop: a barge-in stops the clip within one token."""
    for x in it:
        if _ABORT.is_set():
            raise Aborted()
        yield x


def conds_for(face: str):
    clips = S.voice_clips()
    p = clips.get((face or "").strip().lower())
    if p is None:
        if face not in RT["said"]:
            RT["said"].add(face)
            _log(event="voice", note="no clip for %r; %s's built-in voice speaks" % (face, PROFILE["label"]))
        return RT["builtin"]
    st = p.stat()
    sig = (str(p.resolve()), st.st_mtime, st.st_size)
    hit = _CONDS.get(face)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        RT["model"].prepare_conditionals(str(p))
    except Exception as e:  # noqa: BLE001  a bad clip never silences a face: the built-in voice speaks, said once
        why = "%s's clip %s could not be cloned (%s: %s)" % (face, p.name, type(e).__name__, str(e)[:160])
        if RT["face_why"].get(face) != why:
            RT["face_why"][face] = why
            _log(event="voice", note=why + "; %s's built-in voice speaks" % PROFILE["label"])
        return RT["builtin"]
    RT["face_why"].pop(face, None)
    _CONDS[face] = (sig, RT["model"].conds)
    return RT["model"].conds


def faces_now() -> dict:
    """{face: what it speaks with, and why when it fell back}, for /health and Init's face check."""
    out = {}
    plan = S.face_plan() if hasattr(S, "face_plan") else {}
    for f, p in plan.items():
        why = RT["face_why"].get(f) or p.get("fallback") or ""
        out[f] = {"voice": ("%s's built-in voice" % PROFILE["label"]) if RT["face_why"].get(f) else p["voice"],
                  "fallback": why}
    return out


def render(chunk: str, face: str, seed: int) -> bytes:
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415
    m = RT["model"]
    m.conds = conds_for(face)
    torch.manual_seed(seed)
    with torch.inference_mode():
        wav = m.generate(chunk, temperature=float(RT["settings"]["temperature"])).squeeze(0).numpy()
    return (np.clip(wav, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def _seed_for(face: str, seed) -> int:
    if isinstance(seed, int):
        return seed & 0x7FFFFFFF
    return (int(RT["settings"]["seed"]) + sum(map(ord, face))) & 0x7FFFFFFF


def run_job(text: str, face: str, seed, sink, knobs: dict | None = None) -> dict:
    """Render chunk by chunk on a producer thread; `sink(pcm)` writes. Returns numbers only."""
    chunks = chunks_for(text, knobs)
    q: queue.Queue = queue.Queue()
    stats = {"chunks": len(chunks), "clip_ms": [], "first_audio_ms": None, "aborted": False, "audio_s": 0.0}
    t0 = time.perf_counter()
    s = _seed_for(face, seed)

    def produce():
        try:
            for c in chunks:
                if _ABORT.is_set():
                    break
                tc = time.perf_counter()
                pcm = render(c, face, s)
                stats["clip_ms"].append(round((time.perf_counter() - tc) * 1000))
                q.put(pcm)
        except Aborted:
            pass
        except Exception as e:  # noqa: BLE001  surfaced to the writer
            q.put(e)
        finally:
            q.put(None)

    th = threading.Thread(target=produce, daemon=True)
    th.start()
    while True:
        item = q.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        if _ABORT.is_set():
            break
        if stats["first_audio_ms"] is None:
            stats["first_audio_ms"] = round((time.perf_counter() - t0) * 1000)
        for i in range(0, len(item), WRITE_BYTES):
            if _ABORT.is_set():
                break
            sink(item[i:i + WRITE_BYTES])
            stats["audio_s"] += len(item[i:i + WRITE_BYTES]) / 2 / SR
    if _ABORT.is_set():
        stats["aborted"] = True
        while True:            # nothing queued survives a barge-in
            try:
                q.get_nowait()
            except queue.Empty:
                break
    th.join(timeout=30)
    stats["total_ms"] = round((time.perf_counter() - t0) * 1000)
    stats["audio_s"] = round(stats["audio_s"], 2)
    return stats


def health_extra() -> dict:
    """What /health and the ready line add for this voice (Turbo: nothing)."""
    return {}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not _guard.check(self):
            return
        if self.path.split("?")[0] not in ("/health", "/"):
            return self._json(404, {"ok": False})
        s = RT["settings"]
        self._json(200, {**health_extra(), "ok": RT["model"] is not None, "engine": PROFILE["engine"], "device": "cuda",
                         "sample_rate": SR, "voices": sorted(S.voice_clips()), "builtin_voice": True,
                         "watermark": bool(s["watermark"]), "chunking": {"first_min": s["chunk_first_min"],
                         "min": s["chunk_min"], "max": s["chunk_max"], "mode": "sentence-chunked"},
                         "load_s": RT["load_s"], "last_job": RT["last_job"], "credit": S.CREDIT,
                         "faces": faces_now()})

    def do_POST(self):
        if not _guard.check(self):
            return
        path = self.path.split("?")[0]
        try:
            n = body_len(self.headers, BODY_MAX)
            if n is None:
                return self._json(413 if str(self.headers.get("Content-Length") or "").strip().isdigit() else 400,
                                  {"ok": False, "error": "request over %d bytes or a bad Content-Length" % BODY_MAX})
            req = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except (ValueError, TypeError):
            return self._json(400, {"ok": False, "error": "bad request"})
        if path in ("/abort", "/clear"):
            _ABORT.set()
            _log(event="abort")
            return self._json(200, {"ok": True, "queue_cleared": True})
        if path in ("/reload_voices", "/register"):
            with _LOCK:
                _CONDS.clear()
                RT["said"].clear()
            return self._json(200, {"ok": True, "voices": sorted(S.voice_clips())})
        if path == "/warm":
            face = str(req.get("voice") or "default")
            with _LOCK:
                _ABORT.clear()
                n = len(render("Mm, okay.", face, _seed_for(face, None)))
            return self._json(200, {"ok": True, "bytes": n})
        if path != "/stream":
            return self._json(404, {"ok": False})
        text = str(req.get("text") or "").strip()
        face = str(req.get("voice") or "default").strip()
        if not text:
            return self._json(400, {"ok": False, "error": "text is needed"})
        knobs = {k: max(0, min(int(req["chunk_" + k]), 800)) for k in ("first_min", "min", "max")
                 if isinstance(req.get("chunk_" + k), int)}
        with _LOCK:
            _ABORT.clear()
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16")
            self.send_header("X-Sample-Rate", str(SR))
            self.send_header("X-Channels", "1")
            self.send_header("X-Voice-Engine", PROFILE["key"])
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def sink(b: bytes):
                self.wfile.write(b"%X\r\n" % len(b) + b + b"\r\n")
                self.wfile.flush()
            try:
                RT["language"] = str(req.get("language") or "").strip().lower()
                stats = run_job(text, face, req.get("seed"), sink, knobs)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                _ABORT.set()           # the listener left (barge-in): stop this line
                stats = {"aborted": True, "why": "client_gone"}
            except Exception as e:  # noqa: BLE001
                stats = {"error": type(e).__name__}
            RT["last_job"] = stats
            _log(event="job", **stats)
        self.close_connection = True


def own_gpu_bytes(pid: int | None = None, run=subprocess.run) -> int:
    """What this process holds on the card as nvidia-smi counts it (its CUDA context once torch has touched the GPU).
    0 when nvidia-smi is missing or cannot see the process (a container's own pid namespace)."""
    pid = os.getpid() if pid is None else pid
    try:
        out = run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in out.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) == 2 and f[0] == str(pid) and f[1].isdigit():
            return int(f[1]) * 1024 * 1024
    return 0


def room(free_now: int, own: int, st: dict) -> tuple[bool, str]:
    """The start gate in Init's terms. Init reads nvidia-smi's free memory before Turbo starts; torch can only ask
    after its own CUDA context is on the card (about 0.5 GiB on the reference house), so that context is added back.
    Without it a card Init picked Turbo for (6 GiB free) was refused at start (GPU run 2026-10-06: 6.03 free, 5.5
    after the context). The context itself is part of what headroom_gb leaves for the card's other programs."""
    need = float(st["vram_cap_gb"]) + float(st["headroom_gb"])
    free = (free_now + own) / GiB
    why = ("%.2f GiB of GPU memory free as Init counts it (%.2f after this process's CUDA context of %.2f); Turbo "
           "needs %.1f (turbo.settings vram_cap_gb %s + headroom_gb %s)"
           % (free, free_now / GiB, own / GiB, need, st["vram_cap_gb"], st["headroom_gb"]))
    return free >= need, why


def load(st: dict) -> None:
    import torch  # noqa: PLC0415
    free, total = torch.cuda.mem_get_info(0)
    ok, why = room(free, own_gpu_bytes(), st)
    _log(event="gpu_room", note=why)
    if not ok:
        raise RuntimeError("only " + why + ". The next voice in the chain speaks.")
    torch.cuda.set_per_process_memory_fraction(min(1.0, float(st["vram_cap_gb"]) * GiB / total), 0)
    import perth  # noqa: PLC0415
    if st["watermark"] and getattr(perth, "PerthImplicitWatermarker", None) is None:
        raise RuntimeError("the Perth watermarker cannot load on this machine and turbo.settings.watermark is on; "
                           "Turbo will not speak unmarked unless you set it off")
    if not st["watermark"] and getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
    import chatterbox.models.t3.t3 as t3mod  # noqa: PLC0415
    from chatterbox.tts_turbo import ChatterboxTurboTTS  # noqa: PLC0415
    t3mod.tqdm = _abortable_range
    m = ChatterboxTurboTTS.from_local(str(S.MODEL), "cuda")
    if not st["watermark"]:
        m.watermarker.apply_watermark = lambda wav, sample_rate=SR: wav
    RT["model"], RT["builtin"] = m, m.conds


def main(argv=None) -> int:
    global SP
    ap = argparse.ArgumentParser(description="%s on loopback" % PROFILE["engine"])
    ap.add_argument("--port", type=int, default=_port())
    ap.add_argument("--install", action="store_true", help="build the env and fetch the model, then exit")
    ap.add_argument("--self-test", action="store_true", help="re-run the install's self-test and keep the result "
                    "(Init passes Turbo over while it fails)")
    a = ap.parse_args(argv)
    if a.self_test:
        if not S.installed():
            print("%s: not installed yet; run with --install first" % PROFILE["svc"], file=sys.stderr)
            return 2
        st = S.self_test(watermark=bool(settings()["watermark"]))
        S.save_self_test(st)
        print("%s: self-test %s" % (PROFILE["svc"], "passed: %s (sm_%s, torch %s)" % (
            st.get("gpu"), str(st.get("cap", "")).replace(".", ""), st.get("torch")) if st.get("ok")
            else "FAILED: %s. Init passes %s over and the next voice speaks." % (st.get("why"), PROFILE["label"])))
        return 0 if st.get("ok") else 1
    if a.install:
        try:
            S.install()
        except (RuntimeError, PermissionError) as e:   # PermissionError: kill-search refused the download
            print("%s: %s" % (PROFILE["svc"], e), file=sys.stderr)
            return 2
        return 0
    if not S.installed():
        print("%s: not installed yet; run with --install first (about 3 GB of model)" % PROFILE["svc"], file=sys.stderr)
        return 2
    if not S.in_env():
        os.execv(str(S.env_python()), [str(S.env_python()), PROFILE["script"], *sys.argv[1:]])
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        os.environ.pop(k, None)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    RT["settings"] = settings()
    SP = _speakable()
    t0 = time.monotonic()
    try:
        load(RT["settings"])
        render("Ready.", "default", 0)
    except Exception as e:  # noqa: BLE001
        print("%s: could not load: %s: %s" % (PROFILE["svc"], type(e).__name__, e), file=sys.stderr)
        return 1
    RT["load_s"] = round(time.monotonic() - t0, 2)
    _log(event="ready", port=a.port, watermark=bool(RT["settings"]["watermark"]), load_s=RT["load_s"],
         voices=sorted(S.voice_clips()), credit=S.CREDIT, **health_extra())
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
