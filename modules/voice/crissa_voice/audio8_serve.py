"""The pack's own Audio8 server: the upstream routes, with the stream pipelined (the User 2026-10-08, "it can stream").

Measured on the reference box (engine 7b6a5a1, int4 AR, fp16 codec, 5 ONNX threads): the AR decode alone keeps real
time (1.56x) and the codec decodes a line at 2.4x, but upstream's /api/tts/stream runs them in ONE thread and
re-decodes 128 frames of context for every 12-frame chunk, so it streams at 0.31x; and its generator holds the
request lock until the AR finishes even after a cancel, so a cancelled stream wedged the next line for minutes.

Here the AR runs in its own thread and feeds a queue; the codec thread decodes whatever frames arrived since its
last pass (the chunk grows by itself when the codec is the slower half), with a short context (`AUDIO8_STREAM_CONTEXT`
frames) and the upstream stable-end rule, so no sample is emitted twice or left out. The two ONNX sessions get their
OWN thread budgets (`ARKTTS_THREADS` for the AR, `AUDIO8_CODEC_THREADS` for the codec): sharing one budget made the
pipeline SLOWER than serial (0.65x). Measured on the reference box, the 82-char announcer line: AR 5 + codec 3 =
1.04x real time, first sound 0.93 s (codec 2: 0.95x; 4: 0.98x; AR 8 + codec 4: 0.87x). A cancel sets the AR's stop
event, the AR thread ends within a chunk, and the lock is released in `finally`, whatever the client did. The routes
are upstream's: /api/health, /api/voices, /api/tts (WAV), /api/tts/stream (NDJSON start / audio_chunk / complete /
cancelled), /api/tts/cancel. Upstream's app is one switch away: CRISSA_AUDIO8_UPSTREAM=1 (audio8_setup.server_argv).
"""

import base64
import io
import json
import os
import queue
import threading
from pathlib import Path

CONTEXT = int(os.environ.get("AUDIO8_STREAM_CONTEXT", "16"))      # codec frames of context before each chunk
FIRST_CHUNK = int(os.environ.get("AUDIO8_STREAM_FIRST", "12"))    # frames before the first sound (12 = 0.56 s)
GUARD = int(os.environ.get("AUDIO8_STREAM_GUARD", "1"))           # frames held back at the window's end
CODEC_THREADS = int(os.environ.get("AUDIO8_CODEC_THREADS", "3"))  # the codec session's own ONNX threads (measured best)


def load_runtime(model_dir, voice_root, precision=None, codec_precision=None, threads=5, codec_threads=CODEC_THREADS):
    """ArkTtsRuntime with the codec decoder rebuilt on its own thread budget, so the pipelined stream's two
    sessions do not fight over one pool."""
    import onnxruntime as ort
    from arktts_runtime.runtime import ArkTtsRuntime
    rt = ArkTtsRuntime(Path(model_dir), Path(voice_root), precision or None, codec_precision or None, int(threads))
    codec_models = rt.manifest.get("codec_models", {"fp16": "codec_decoder_fp16.onnx"})
    path = Path(model_dir) / codec_models[rt.codec_precision]
    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, int(codec_threads))
    so.inter_op_num_threads = 1
    rt.decoder = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
    rt.codec_threads = int(codec_threads)
    return rt


def stream_pipelined(rt, chunk_frames: int = FIRST_CHUNK, context: int = CONTEXT, guard_frames: int = GUARD,
                     stop_event: threading.Event | None = None, **kw):
    """Yield {"type": "audio_chunk", "seq", "audio", "frame_count"} then {"type": "complete", "frames"}: the AR in a
    thread, the codec in this one. `rt` is an ArkTtsRuntime (iter_codes, decode_codes, manifest)."""
    import numpy as np
    hop = int(rt.manifest["codec_hop_length"])
    guard = int(guard_frames) * hop
    stop = stop_event if stop_event is not None else threading.Event()
    q: "queue.Queue" = queue.Queue()

    def produce():
        try:
            for fr in rt.iter_codes(stop_event=stop, **kw):
                q.put(fr)
                if stop.is_set():
                    break
        except Exception as e:  # noqa: BLE001
            q.put(e)
        finally:
            q.put(None)

    threading.Thread(target=produce, name="audio8-ar", daemon=True).start()
    frames: list = []
    emitted = 0
    decoded_upto = 0
    seq = 0
    done = False

    def decode_new():
        nonlocal emitted, decoded_upto, seq
        new = len(frames) - decoded_upto
        if new <= 0:
            return None
        start = max(0, len(frames) - int(context) - new)
        window = np.stack(frames[start:], axis=1)
        audio = rt.decode_codes(window)
        absolute_start = start * hop
        stable_end = absolute_start + max(0, audio.size - (0 if done else guard))
        begin = max(0, emitted - absolute_start)
        end = max(begin, stable_end - absolute_start)
        decoded_upto = len(frames)
        if end > begin:
            chunk = np.ascontiguousarray(audio[begin:end])
            emitted += chunk.size
            out = {"type": "audio_chunk", "seq": seq, "audio": chunk, "frame_count": len(frames)}
            seq += 1
            return out
        return None

    while not done:
        item = q.get()                                  # the first frames: wait
        if item is None:
            done = True
        elif isinstance(item, Exception):
            raise item
        else:
            frames.append(item)
            while True:                                 # everything the AR has made meanwhile, without waiting
                try:
                    more = q.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    done = True
                    break
                if isinstance(more, Exception):
                    raise more
                frames.append(more)
        if stop.is_set():
            return
        if done or len(frames) - decoded_upto >= int(chunk_frames):
            ev = decode_new()
            if ev is not None:
                yield ev
    if not frames:
        raise RuntimeError("model produced no codec frames")
    yield {"type": "complete", "frames": len(frames)}


def build_app(runtime=None):
    """The FastAPI app. `runtime` is built from the ARKTTS_* environment when not given (the same names upstream reads)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import Response, StreamingResponse
    from pydantic import BaseModel, Field

    class TtsRequest(BaseModel):
        text: str = Field(..., min_length=1)
        voice_name: str
        max_new_tokens: int = Field(1024, ge=16, le=2048)
        temperature: float = Field(0.3, gt=0.0, le=2.0)
        top_p: float = Field(0.9, gt=0.0, le=1.0)
        top_k: int = Field(50, ge=1, le=4096)
        seed: int = 42

    app = FastAPI(title="crissa-audio8")
    state = {"rt": runtime, "stop": None}
    lock = threading.Lock()

    def rt():
        if state["rt"] is None:
            state["rt"] = load_runtime(os.environ["ARKTTS_MODEL_DIR"], os.environ["ARKTTS_VOICES_DIR"],
                                       os.environ.get("ARKTTS_PRECISION"), os.environ.get("ARKTTS_CODEC_PRECISION"),
                                       int(os.environ.get("ARKTTS_THREADS", "5")), CODEC_THREADS)
        return state["rt"]

    @app.on_event("startup")
    def _startup():
        try:
            rt()
        except Exception as e:  # noqa: BLE001
            print("audio8: runtime did not load: %r" % (e,), flush=True)

    @app.get("/api/health")
    def health():
        r = rt()
        return {"ok": True, "precision": r.precision, "codec_precision": r.codec_precision, "pipelined": True,
                "stream_context": CONTEXT, "codec_threads": getattr(r, "codec_threads", None)}

    @app.get("/api/voices")
    def voices():
        return {"voices": rt().voices.list()}

    def kwargs(req):
        return dict(text=req.text, voice=req.voice_name, max_new_tokens=req.max_new_tokens, temperature=req.temperature,
                    top_p=req.top_p, top_k=req.top_k, seed=req.seed)

    @app.post("/api/tts")
    def tts(req: TtsRequest):
        import soundfile as sf
        with lock:
            r = rt()
            try:
                audio, _ = r.synthesize(**kwargs(req))
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        buf = io.BytesIO()
        sf.write(buf, audio, int(r.manifest["sample_rate"]), format="WAV")
        return Response(buf.getvalue(), media_type="audio/wav")

    @app.post("/api/tts/stream")
    def stream(req: TtsRequest):
        import numpy as np

        def events():
            stop = threading.Event()
            got = lock.acquire(timeout=120.0)
            if not got:
                yield json.dumps({"event": "busy"}) + "\n"
                return
            state["stop"] = stop
            try:
                r = rt()
                yield json.dumps({"event": "start", "sample_rate": int(r.manifest["sample_rate"]), "channels": 1,
                                  "sample_format": "s16le", "precision": r.precision,
                                  "codec_precision": r.codec_precision, "pipelined": True}) + "\n"
                for ev in stream_pipelined(r, stop_event=stop, **kwargs(req)):
                    if stop.is_set():
                        yield json.dumps({"event": "cancelled"}) + "\n"
                        return
                    if ev["type"] == "audio_chunk":
                        pcm = (np.clip(ev["audio"], -1.0, 1.0) * 32767.0).astype("<i2")
                        yield json.dumps({"event": "audio_chunk", "seq": int(ev["seq"]), "frame_count": int(ev["frame_count"]),
                                          "pcm_b64": base64.b64encode(pcm.tobytes()).decode("ascii")}) + "\n"
                    else:
                        yield json.dumps({"event": "complete", "frame_count": int(ev["frames"])}) + "\n"
                if stop.is_set():
                    yield json.dumps({"event": "cancelled"}) + "\n"
            finally:
                stop.set()                      # a client that went away stops the AR too
                if state["stop"] is stop:
                    state["stop"] = None
                lock.release()

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/tts/cancel")
    def cancel():
        s = state["stop"]
        if s is None:
            return {"ok": True, "cancelled": False}
        s.set()
        return {"ok": True, "cancelled": True}

    return app


def _app():
    return build_app()


app = None
if os.environ.get("CRISSA_AUDIO8_SERVE_LAZY", "0") not in ("1", "true"):
    try:
        app = _app()
    except Exception:  # noqa: BLE001   (no fastapi here: the tests import the pipeline alone)
        app = None
