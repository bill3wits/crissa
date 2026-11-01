#!/usr/bin/env python3
"""The voice module's Pocket service (2026-10-06): Kyutai Pocket TTS on the CPU, on loopback, the house's /stream contract.

    POST /stream           {"text", "voice"[, "chunk_first_max", "chunk_max", "emit", "marks"]} -> chunked 16-bit mono PCM at
                           24 kHz (X-Sample-Rate): the contract house-speak's streamed path speaks, so a pack whose voice
                           tier is Pocket streams through the same code as Turbo
    POST /v1/audio/speech  {"input", "voice", "response_format": "pcm"|"wav"} -> the whole line (OpenAI contract)
    POST /abort | /clear   barge-in: stop at the next 80 ms frame and drop every queued chunk
    POST /warm             {"voice"}: a silent render
    GET  /v1/voices        the stock voices this install may speak with
    GET  /health

    python3 modules/voice/scripts/crissa-pocket.py --install
    python3 modules/voice/scripts/crissa-pocket.py [--port 8188]     # re-runs itself in the Pocket env

CHUNKED: a line is cut into ~80-character sentence chunks (crissa_voice.chunker; the first capped at 40 so she starts
sooner), one chunk renders at a time with a queue behind it, and each chunk's 80 ms frames are sent as Pocket makes
them. emit = "chunk" (settings, or POCKET_EMIT=chunk) renders each chunk whole first; measured on 3 cores that leaves
1.0-1.3 s gaps between clips, so frames are the default.

STOCK VOICES ONLY (crissa_voice.pocket_setup.VOICES): no reference clip is ever loaded and nothing is cloned. A face
name (crissa, lila, announcer) maps to a stock voice by the tier table's pocket.settings.voices; an unknown name speaks
with the default voice and the log says so. Logs carry numbers only, never the spoken text.

Port: the shared table's `pocket` (CRISSA_PORT_POCKET, else 8188 + CRISSA_PORT_OFFSET). Loopback only. Every model file
is local and checked; the service never reaches the network.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from crissa_voice import chunker, pocket_setup as S  # noqa: E402
from crissa_voice import guard as _guard  # noqa: E402

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): any web page open in the buyer's browser could make her speak (/stream, /warm) or stop
# her (/abort). The module's
# copy of the one loopback rule (crissa_voice.guard, byte-equal to lib/crissa_guard.py) refuses, first in every do_*, a Host
# that is not this machine, a foreign Origin, and a cross-site mark with no loopback Referer; the player, the ear and
# curl send none of those and pass. A negative Content-Length (rfile.read(-1) reads until the peer closes) is refused
# too. On by default (a module is the kit's); kills CRISSA_LOOPBACK_GUARD=0 and CRISSA_BODY_LENGTH=0 (the old read).
# THE BODY'S SIZE: a line's text and its knobs; 1 MiB is far past any line and was the size here before. A dial:
# CRISSA_POCKET_BODY_MAX (bytes).
BODY_MAX = int(os.environ.get("CRISSA_POCKET_BODY_MAX") or 1 << 20)

def body_len(headers, cap):
    """Round 46: the Content-Length to read, else None (negative, not a number, over `cap`); the kill reads as before."""
    n = _guard.body_length(headers, cap)
    if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
        n = int(headers.get("Content-Length") or 0)
    return n

SR = 24000
# the piece marks on /stream, when a client asks (see do_POST); POCKET_MARKS=0 never sends them
MARKS_ON = os.environ.get("POCKET_MARKS", "1").strip().lower() not in ("0", "false", "no", "off")
# A PIECE'S MARK GOES OUT WITH ITS LAST AUDIO (round 17, 2026-10-13, .101: "her lips are saying something not in the
# reading", piece 1 of every line spoken before its stamp). A mark rode on the NEXT chunk-size line, which is written only
# once the next piece's first frame is made: measured on this machine with the kit's Pocket, each mark but the last
# reached house-speak 103-159 ms after its piece's last audio on a free CPU and 296-444 ms on a busy one, and every
# piece's stamp waited that much longer. Now the stream holds back one sample (2 bytes) of what it writes, and a mark
# that is not the line's last goes out at once on a chunk of its own carrying that sample: the reader names the piece's
# end one sample (41.7 us) early, which no alignment can see, and the PCM is byte for byte the same. The last piece's
# mark stays on the closing chunk, where its end must be the whole line. Kill: POCKET_MARKS_NOW=0.
MARKS_NOW = os.environ.get("POCKET_MARKS_NOW", "1").strip().lower() not in ("0", "false", "no", "off")
WRITE_BYTES = 9600   # 0.2 s per HTTP chunk
DEFAULTS = {"threads": 3, "emit": "frames", "chunk_first_max": 40, "chunk_max": 80, "max_tokens": 400,
            "voices": {"crissa": "crissa", "lila": "eve", "announcer": "anna", "default": "azelma"}}
RT = {"model": None, "load_s": None, "settings": dict(DEFAULTS), "last_job": None, "voices": {}}
# SEAMS AS SHORT AS SPEECH'S (round 19, the User 2026-10-14, .101: "seems halting when they speak with pauses between
# sentences"). Every chunk is its own generation, and Pocket opens one with 0.5-0.9 s of quiet and closes it with
# 0.2-0.5 s, so a seam carried 1.0-1.4 s of silence at a sentence end and 0.4-1.0 s where a long sentence was cut at a
# comma or a space (Pocket's own comma inside one generation: 0.12 s). Measured on this machine's Pocket, five lines
# (docs/checkpoints/r19-voices-20261014). From the second chunk of a line on, the lead-in quiet before its first voiced
# frame is cut to SEAM_LEAD_S, and every chunk but the line's last keeps at most SEAM_SENTENCE_S of its closing quiet
# when it ends a sentence (. ! ? ; :) and SEAM_CLAUSE_S when the chunker cut it mid-sentence. The first chunk's lead-in
# (the window its stamp is made in, round 17) and the line's closing quiet are as Pocket made them; the voice itself is
# never touched. A frame is quiet under SEAM_QUIET_RMS (house-speak's voice-onset level).
# OFF BY DEFAULT, POCKET_SEAM_TRIM=1 TURNS IT ON (r19 bench, docs/checkpoints/r19-voices-20261014): at .101's pace the
# long seams were the aligner's time to stamp the next piece before her voice reached it, and with them cut 93 open-mouth
# rows showed on the letter guess against round 18's 21 (4 pairs, Pocket 1.8-2.1x). Paired with round 17's stamp hold
# (HOUSE_SPEAK_STAMP_HOLD=1), which writes silence only when a piece is not stamped yet, is the measurement owed.
SEAM_TRIM = os.environ.get("POCKET_SEAM_TRIM", "0").strip().lower() in ("1", "true", "yes", "on")
SEAM_LEAD_S = 0.08
SEAM_SENTENCE_S = 0.32
SEAM_CLAUSE_S = 0.12
SEAM_QUIET_RMS = 0.01
_SENTENCE_END = re.compile(r"[.!?;:][\"')\]]*$")
_ABORT = threading.Event()
_LOCK = threading.Lock()


def _port() -> int:
    v = os.environ.get("CRISSA_PORT_POCKET", "").strip()
    if v.isdigit():
        return int(v)
    return 8188 + int(os.environ.get("CRISSA_PORT_OFFSET", "0") or 0)


def _log(**kv):
    print(json.dumps({"t": round(time.time(), 3), "svc": "crissa-pocket", **kv}), flush=True)


def settings(repo_root: Path | None = None, home: Path | None = None, env=None) -> dict:
    """DEFAULTS, then the tier table's pocket.settings (the install's config, then the state-home copy), then
    POCKET_EMIT / POCKET_THREADS / POCKET_FIRST_MAX from the environment."""
    e = os.environ if env is None else env
    out = json.loads(json.dumps(DEFAULTS))
    root = repo_root if repo_root is not None else HERE.parent.parent
    for p in (root / "config" / "crissa-voice-tiers.json", (home or S._home()) / "crissa-voice-tiers.json"):
        try:
            got = ((json.loads(p.read_text(encoding="utf-8")).get("tiers") or {}).get("pocket") or {}).get("settings")
        except (OSError, ValueError, AttributeError):
            continue
        for k, v in (got or {}).items():
            out[k] = dict(out.get(k) or {}, **v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    if str(e.get("POCKET_EMIT", "")).strip().lower() in ("chunk", "frames"):
        out["emit"] = e["POCKET_EMIT"].strip().lower()
    if str(e.get("POCKET_THREADS", "")).strip().isdigit():
        out["threads"] = int(e["POCKET_THREADS"])
    # THE FIRST PIECE'S SIZE, FOR AN A/B (round 32, .101 10-19: a line's first piece spoke 0.60-1.35 s before its stamp;
    # it is stamped only once Pocket has made all of it, so a shorter first chunk is stamped sooner, at a possible seam
    # after its first words). The kit stays at 40 until the User rules on his ear; .101 tries 24 with one line.
    if str(e.get("POCKET_FIRST_MAX", "")).strip().isdigit():
        out["chunk_first_max"] = int(e["POCKET_FIRST_MAX"])
    return out


def _speakable():
    """lib/speakable.py when the module sits in a Crissa install: numbers, times and money read as words."""
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
    if SP is not None and hasattr(SP, "numbers_to_words"):
        t = SP.numbers_to_words(t)
    return re.sub(r"\s+", " ", t).strip()


def chunks_for(text: str, first_max=None, max_chars=None, st: dict | None = None) -> list[str]:
    s = st or RT["settings"]
    return chunker.chunk_text(prepare_text(text), 0, 0, int(max_chars or s["chunk_max"]),
                              int(s["chunk_first_max"] if first_max is None else first_max))


class Aborted(Exception):
    pass


def _language() -> tuple[str, str]:
    """(the language Pocket speaks here, why it is English when Init's language is another one). The language's
    files come with the install; a language installed later needs a reinstall, and until then Pocket says so."""
    want = S.language_code()
    if want == "en":
        return "en", ""
    try:
        have = json.loads(S.DONE.read_text()).get("languages") or []
    except (OSError, ValueError):
        have = []
    if want not in have:
        return "en", ("Init's language is %s but its Pocket voice is not installed; Pocket speaks English until "
                      "`crissa-pocket.py --install` runs again" % S.lang_dir(want))
    return want, ""


def _her_style_map(name: str, mapping: dict | None) -> dict | None:
    """Round 28: her face speaks with the voice her-voice.json chooses ("her" = crissa, "english_accent" = azelma),
    read on every line, so Setup's choice needs no restart. Anything unreadable leaves the table's map."""
    try:
        lib = str(HERE.parent.parent / "lib")
        if lib not in sys.path:
            sys.path.append(lib)
        import crissa_voice_tiers as VT  # noqa: PLC0415
        pick = VT.pocket_face_voice(name, mapping)
    except Exception:  # noqa: BLE001
        return mapping
    return dict(mapping or {}, **{str(name).lower(): pick}) if pick else mapping


def state_for(name: str):
    voice, note = S.voice_for(name, _her_style_map(name, RT["settings"].get("voices")), RT.get("lang", "en"))
    if note:
        _log(event="voice", note=note)
    hit = RT["voices"].get(voice)
    if hit is None:
        hit = RT["model"].get_state_for_audio_prompt(S.voice_path(voice, RT.get("lang", "en")))  # local, checked
        RT["voices"][voice] = hit
    return voice, hit


def frames(chunk: str, name: str):
    _, st = state_for(name)
    for fr in RT["model"].generate_audio_stream(st, chunk, max_tokens=int(RT["settings"]["max_tokens"]), stop=_ABORT):
        if _ABORT.is_set():
            raise Aborted()
        import numpy as np  # noqa: PLC0415
        wav = fr.detach().float().reshape(-1).numpy()
        yield (np.clip(wav, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    if _ABORT.is_set():
        raise Aborted()


def _quiet(frame: bytes) -> bool:
    n = len(frame) // 2
    if n == 0:
        return True
    import array  # noqa: PLC0415
    a = array.array("h")
    a.frombytes(frame[: n * 2])
    return (sum(x * x for x in a) / n) ** 0.5 / 32768.0 < SEAM_QUIET_RMS


def seamed(frs, first: bool, last: bool, text: str):
    """One chunk's frames with its seams cut to speech's (SEAMS AS SHORT AS SPEECH'S): the lead-in quiet of every chunk
    but the first down to SEAM_LEAD_S; in every chunk but the last, a quiet stretch after her voice streams as it is made
    up to SEAM_SENTENCE_S (the chunk ends a sentence) or SEAM_CLAUSE_S, and what runs past that is held: written if her
    voice comes back in the chunk (quiet inside a chunk is kept whole), dropped if the chunk ends there. Nothing is held
    that a seam would keep, so the stream is never later than Pocket for any sound the room hears."""
    if not SEAM_TRIM or (first and last):
        yield from frs
        return
    sentence = bool(_SENTENCE_END.search((text or "").strip()))
    budget = 0 if last else int(2 * SR * (SEAM_SENTENCE_S if sentence else SEAM_CLAUSE_S))
    lead: list = []                  # a later chunk's quiet before her first voiced frame
    held: list = []                  # quiet past the seam's budget since her last voiced frame
    seen = False
    run = 0                          # bytes of quiet since her last voiced frame
    for fr in frs:
        quiet = _quiet(fr)
        if not seen:
            if not quiet:
                seen = True
                yield from lead
                lead = []
                yield fr
            elif first:
                yield fr             # the first chunk's lead-in: as Pocket makes it
            else:
                lead.append(fr)
                while lead and sum(len(f) for f in lead[1:]) >= 2 * SR * SEAM_LEAD_S:
                    lead.pop(0)
            continue
        if not quiet:
            yield from held
            held = []
            run = 0
            yield fr
            continue
        room = budget - run
        run += len(fr)
        if last or room >= len(fr):
            yield fr
        elif room > 0:
            room -= room % 2
            yield fr[:room]
            held.append(fr[room:])
        else:
            held.append(fr)
    if not seen:                     # a later chunk with no voice at all: its last SEAM_LEAD_S of quiet
        yield from lead


def run_job(text: str, name: str, sink, knobs: dict | None = None, mark=None) -> dict:
    """Render chunk by chunk on a producer thread; `sink(pcm)` writes. Returns numbers only. `mark(index, text)`, when
    given, is called once a chunk's last audio has gone to `sink`, before the next chunk's first (the piece marks)."""
    k = knobs or {}
    emit = k.get("emit") or RT["settings"]["emit"]
    chunks = chunks_for(text, k.get("first_max"), k.get("max"))
    q: queue.Queue = queue.Queue()
    stats = {"chunks": len(chunks), "emit": emit, "first_audio_ms": None, "aborted": False, "audio_s": 0.0}
    RT["cut_bytes"] = 0              # the quiet the seams dropped this line (named on each piece mark: `cut`)
    t0 = time.perf_counter()

    def produce():
        try:
            for i, c in enumerate(chunks):
                if _ABORT.is_set():
                    break
                made = [0]

                def counted(it):
                    for b in it:
                        made[0] += len(b)
                        yield b
                fr = seamed(counted(frames(c, name)), i == 0, i == len(chunks) - 1, c)
                sent = 0
                if emit == "frames":
                    for b in fr:
                        sent += len(b)
                        q.put(b)
                else:
                    whole = b"".join(fr)
                    sent = len(whole)
                    q.put(whole)
                RT["cut_bytes"] = RT.get("cut_bytes", 0) + made[0] - sent
                q.put(("mark", i, c))
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
        if isinstance(item, tuple):
            if mark is not None:
                mark(item[1], item[2])
            continue
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


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        if path in ("/health", "/"):
            s = RT["settings"]
            return self._json(200, {"ok": RT["model"] is not None, "engine": "pocket-tts", "device": "cpu",
                                    "sample_rate": SR, "voices": S.voices(RT.get("lang", "en")), "watermark": False,
                                    "language": RT.get("lang", "en"), "language_why": RT.get("lang_why", ""),
                                    "threads": s["threads"], "chunking": {"first_max": s["chunk_first_max"],
                                                                          "max": s["chunk_max"], "emit": s["emit"]},
                                    "load_s": RT["load_s"], "last_job": RT["last_job"], "credit": S.CREDIT,
                                    # round 29: a line is being made now (the machine check waits for this to clear
                                    # before it times her, so another line's queue is never counted as her speed)
                                    "busy": _LOCK.locked()})
        if path in ("/v1/voices", "/voices"):
            return self._json(200, {"voices": S.voices(RT.get("lang", "en")), "faces": RT["settings"].get("voices"),
                                    "language": RT.get("lang", "en")})
        self._json(404, {"ok": False})

    def _body(self) -> dict:
        n = body_len(self.headers, BODY_MAX)
        if n is None:
            raise ValueError("bad Content-Length")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_POST(self):
        if not _guard.check(self):
            return
        path = self.path.split("?")[0]
        try:
            req = self._body()
        except (ValueError, TypeError):
            return self._json(400, {"ok": False, "error": "bad request"})
        if path in ("/abort", "/clear"):
            _ABORT.set()
            _log(event="abort")
            return self._json(200, {"ok": True, "queue_cleared": True})
        if path == "/warm":
            with _LOCK:
                _ABORT.clear()
                n = sum(len(b) for b in frames("Mm, okay.", str(req.get("voice") or "default")))
            return self._json(200, {"ok": True, "bytes": n})
        if path == "/stream":
            text = str(req.get("text") or "").strip()
            voice = str(req.get("voice") or "default").strip()
            if not text:
                return self._json(400, {"ok": False, "error": "text is needed"})
            knobs = {}
            for k in ("first_max", "max"):
                v = req.get("chunk_" + k)
                if isinstance(v, int):
                    knobs[k] = max(0, min(v, 800))
            if req.get("emit") in ("chunk", "frames"):
                knobs["emit"] = req["emit"]
            with _LOCK:
                _ABORT.clear()
                # the stock voice this line speaks with, named in the reply and the log (2026-10-07: .101 heard a
                # man's voice for Crissa and nothing said which voice spoke)
                # round 33: the voice state_for loads (her-voice.json's style on her face), not the table's map alone;
                # kill CRISSA_OWN_VOICE_REPORT=0
                own = os.environ.get("CRISSA_OWN_VOICE_REPORT", "1").strip().lower() not in ("0", "off", "false", "no")
                vmap = _her_style_map(voice, RT["settings"].get("voices")) if own else RT["settings"].get("voices")
                stock = S.voice_for(voice, vmap, RT.get("lang", "en"))[0]
                self.send_response(200)
                self.send_header("Content-Type", "audio/L16")
                self.send_header("X-Sample-Rate", str(SR))
                self.send_header("X-Channels", "1")
                self.send_header("X-Voice-Engine", "pocket")
                self.send_header("X-Voice", stock)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                # PIECE MARKS (round 11, 2026-10-12: .101's long answers spoke on the letter clock until the whole
                # line was made, 13.75 s into a 25.7 s answer): asked for with "marks": true, the end of each chunk is
                # named on the chunk-size line of the next HTTP chunk (or the last one) as a chunk extension,
                # `;piece-end=<i>;text=<the chunk's text, percent-encoded>`, so house-speak can align each chunk
                # while the next is still being made. HTTP readers ignore chunk extensions; the PCM is unchanged.
                pending = []
                marks_on = bool(req.get("marks")) and MARKS_ON
                now_marks = marks_on and MARKS_NOW          # round 17: a mark goes out with its piece's last audio
                n_chunks = len(chunks_for(text, knobs.get("first_max"), knobs.get("max"))) if now_marks else 0
                held = bytearray()                          # the last sample written, while marks go out at once

                def ext() -> bytes:
                    # `cut` (round 19): the bytes of quiet the seams dropped so far, so a reader's pace counts what
                    # Pocket made (readers skip a key they do not know)
                    out = b"".join(b";piece-end=%d;text=%s;cut=%d" % (i, urllib.parse.quote(t, safe="").encode("ascii"),
                                                                      int(RT.get("cut_bytes", 0)))
                                   for i, t in pending)
                    pending.clear()
                    return out

                def put(b: bytes, with_marks: bool = True):
                    self.wfile.write(b"%X" % len(b) + (ext() if (pending and with_marks) else b"") + b"\r\n" + b
                                     + b"\r\n")
                    self.wfile.flush()

                def sink(b: bytes):
                    if now_marks:
                        b = bytes(held) + bytes(b)
                        held[:] = b[-2:]
                        b = b[:-2]
                        if not b:
                            return
                    put(b)

                def mark(i: int, t: str):
                    pending.append((int(i), str(t)))
                    if now_marks and held and int(i) < n_chunks - 1:
                        put(bytes(held))                    # the piece's last sample, carrying its mark, now
                        held.clear()
                try:
                    stats = run_job(text, voice, sink, knobs, mark=mark if marks_on else None)
                    if held:
                        put(bytes(held), with_marks=False)   # the line's last sample; its mark closes the stream
                        held.clear()
                    self.wfile.write(b"0" + (ext() if pending else b"") + b"\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    _ABORT.set()           # the listener left (barge-in): stop this line
                    stats = {"aborted": True, "why": "client_gone"}
                except Exception as e:  # noqa: BLE001
                    stats = {"error": type(e).__name__}
                RT["last_job"] = dict(stats, face=voice[:40], voice=stock)
                _log(event="job", face=voice[:40], voice=stock, **stats)
            self.close_connection = True
            return
        if path in ("/v1/audio/speech", "/audio/speech"):
            text = str(req.get("input") or "").strip()
            if not text:
                return self._json(400, {"ok": False, "error": "input is needed"})
            buf = bytearray()
            t0 = time.monotonic()
            with _LOCK:
                _ABORT.clear()
                try:
                    run_job(text, str(req.get("voice") or "default"), buf.extend, {"emit": "chunk"})
                except Exception as e:  # noqa: BLE001
                    return self._json(500, {"ok": False, "error": type(e).__name__})
            ms = "%.1f" % ((time.monotonic() - t0) * 1000.0)
            if str(req.get("response_format") or "pcm").lower() == "wav":
                return self._send(200, _wav(bytes(buf)), "audio/wav", {"X-Synth-Ms": ms})
            return self._send(200, bytes(buf), "audio/pcm", {"X-Synth-Ms": ms, "X-Sample-Rate": str(SR)})
        self._json(404, {"ok": False})


def main(argv=None) -> int:
    global SP
    ap = argparse.ArgumentParser(description="Pocket TTS (stock voices) on loopback")
    ap.add_argument("--port", type=int, default=_port())
    ap.add_argument("--install", action="store_true", help="build the env and fetch the model, then exit")
    a = ap.parse_args(argv)
    if a.install:
        try:
            S.install()
        except (RuntimeError, PermissionError) as e:   # PermissionError: kill-search refused the download
            print("crissa-pocket: " + str(e), file=sys.stderr)
            return 2
        return 0
    if not S.installed():
        print("crissa-pocket: not installed yet; run with --install first (about 0.33 GB of model)", file=sys.stderr)
        return 2
    if not S.in_env():
        os.execv(str(S.env_python()), [str(S.env_python()), __file__, *sys.argv[1:]])
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        os.environ.pop(k, None)                  # never the person's login: stock voices need none
    os.environ["HF_HUB_OFFLINE"] = "1"          # every file is local and checked; a line never reaches the network
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    RT["settings"] = settings()
    SP = _speakable()
    t0 = time.monotonic()
    try:
        import torch  # noqa: PLC0415
        torch.set_num_threads(int(RT["settings"]["threads"]))
        from pocket_tts import TTSModel  # noqa: PLC0415
        RT["lang"], RT["lang_why"] = _language()
        if RT["lang_why"]:
            print("crissa-pocket: " + RT["lang_why"], file=sys.stderr, flush=True)
        RT["model"] = TTSModel.load_model(config=str(S.local_config(code=RT["lang"])))
        for _ in frames("Ready.", "default"):     # warm before the first line
            pass
    except Exception as e:  # noqa: BLE001
        print("crissa-pocket: could not load: %s" % (("%s: %s" % (type(e).__name__, e))[:300]), file=sys.stderr)
        return 1
    RT["load_s"] = round(time.monotonic() - t0, 2)
    _log(event="ready", port=a.port, threads=RT["settings"]["threads"], emit=RT["settings"]["emit"],
         load_s=RT["load_s"], credit=S.CREDIT)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
