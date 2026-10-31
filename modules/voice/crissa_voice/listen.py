"""The voice module's ear: microphone frames -> utterances -> text -> turns (2026-10-01).

A slim public counterpart of the house's listening loop. Frames (16 kHz mono int16, 30 ms) come from the
microphone through `sounddevice` (Linux and macOS), or from any iterator a test hands in. Each frame is
called speech or not (WebRTC VAD when installed, else an energy floor); an utterance opens after
OPEN_FRAMES speech frames (with PREROLL_S of audio before them), closes after HANGOVER_S of silence or at
MAX_UTTER_S, must hold MIN_SPEECH_S of speech by vad.detect_speech_spans, and is decoded by an ASR service
speaking the /stt contract (raw int16 PCM + X-Sample-Rate -> {"text", ...}). The quality filters drop decoder
noise. A turn is {"ts", "text", "duration_s", "via": "ear", "engine", "her_voice"} - the shape
scripts/crissa-ear.py reads. `her_voice` marks a turn heard while the player was speaking (her own voice
through the speakers); the ear drops those.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
from collections import deque
from typing import Callable, Iterable, Iterator

import numpy as np

from . import egress, quality
from .audio import rms_db
from .vad import detect_speech_spans

SR = 16000
FRAME_MS = 30
FRAME = SR * FRAME_MS // 1000            # 480 samples
OPEN_FRAMES = int(os.environ.get("CRISSA_STT_OPEN_FRAMES", "2"))
PREROLL_S = float(os.environ.get("CRISSA_STT_PREROLL_S", "0.3"))
HANGOVER_S = float(os.environ.get("CRISSA_STT_HANGOVER_S", "0.9"))
MAX_UTTER_S = float(os.environ.get("CRISSA_STT_MAX_UTTER_S", "20"))
MIN_SPEECH_S = float(os.environ.get("CRISSA_STT_MIN_SPEECH_S", "0.35"))
ENERGY_DB = float(os.environ.get("CRISSA_STT_ENERGY_DB", "-45"))


def frame_judge() -> Callable[[np.ndarray], bool]:
    """Speech or not, per 30 ms frame: WebRTC VAD (mode 2) when installed, else RMS over ENERGY_DB."""
    try:
        import webrtcvad
        v = webrtcvad.Vad(int(os.environ.get("CRISSA_STT_WEBRTC_MODE", "2")))
        return lambda f: bool(v.is_speech(f.astype(np.int16).tobytes(), SR)) and rms_db(f.astype(np.float32) / 32768.0) > ENERGY_DB - 15
    except ImportError:
        return lambda f: rms_db(f.astype(np.float32) / 32768.0) > ENERGY_DB


def asr_client(url: str, timeout: float = 30.0) -> Callable[[np.ndarray], dict]:
    """POST raw int16 PCM to an ASR service with the /stt contract."""
    def run(pcm: np.ndarray) -> dict:
        req = urllib.request.Request(url.rstrip("/") + "/stt", data=pcm.astype(np.int16).tobytes(), method="POST",
                                     headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(SR)})
        with egress.urlopen(req, timeout, "the speech-in service") as r:
            return json.loads(r.read() or b"{}")
    return run


def player_speaking(player: str) -> Callable[[], bool]:
    """Is the avatar on the air? Unreadable counts as no (the ear has its own guard)."""
    def run() -> bool:
        try:
            with egress.urlopen(player.rstrip("/") + "/v1/state", 0.3, "the player") as r:
                return bool(json.loads(r.read() or b"{}").get("on_air"))
        except (OSError, ValueError):
            return False
    return run


def mic_frames(device: str | int | None = None) -> Iterator[np.ndarray]:
    """30 ms int16 frames from the default (or named) input device, forever."""
    import sounddevice as sd
    q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=400)

    def cb(indata, frames, t, status):
        try:
            q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass
    with sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=FRAME, device=device, callback=cb):
        while True:
            yield q.get()


class PushedFrames:
    """Microphone frames that arrive by POST from a browser page (2026-10-05, CRISSA_AUDIO_AT=page).

    The Wall page captures with getUserMedia (the browser's echo canceller and noise suppression on),
    and its posts land here through the player's /v1/mic. Iterating yields 30 ms int16 frames like
    mic_frames(). While nothing arrives it yields silence at the frame rate, so an utterance the page
    stopped sending in the middle of (Talk off) still closes on the hangover instead of hanging open.
    """

    def __init__(self, maxsize: int = 400):
        self.q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=maxsize)
        self.rest = np.zeros(0, dtype=np.int16)
        self.lock = threading.Lock()
        self.stats = {"posts": 0, "frames": 0, "dropped": 0, "last": 0.0}

    def push(self, raw: bytes, sr: int = SR) -> int:
        """Add 16-bit mono PCM at `sr`; returns the frames queued."""
        pcm = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16)
        if sr != SR and len(pcm):
            from .audio import float_to_pcm16, resample_linear
            pcm = float_to_pcm16(resample_linear(pcm.astype(np.float32) / 32768.0, sr, SR))
        n = 0
        with self.lock:
            buf = np.concatenate([self.rest, pcm]) if len(self.rest) else pcm
            whole = len(buf) // FRAME * FRAME
            for i in range(0, whole, FRAME):
                try:
                    self.q.put_nowait(buf[i:i + FRAME].copy())
                    n += 1
                except queue.Full:
                    self.stats["dropped"] += 1
            self.rest = buf[whole:].copy()
            self.stats["posts"] += 1
            self.stats["frames"] += n
            self.stats["last"] = time.time()
        return n

    # The page posts every ~100 ms, so a wait shorter than a post's gap is not the page going quiet: filling
    # it with silence would land BETWEEN real frames and chop speech into runs too short to count (the first
    # draft did, 2026-10-05). Silence starts only after STALL_S with nothing, then at the frame rate.
    STALL_S = 0.35

    def __iter__(self) -> Iterator[np.ndarray]:
        silence = np.zeros(FRAME, dtype=np.int16)
        stalled = False
        while True:
            try:
                f = self.q.get(timeout=FRAME_MS / 1000.0 if stalled else self.STALL_S)
                stalled = False
                yield f
            except queue.Empty:
                stalled = True
                yield silence


class Listener:
    def __init__(self, asr: Callable[[np.ndarray], dict], on_turn: Callable[[dict], None], *,
                 judge: Callable[[np.ndarray], bool] | None = None, speaking: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time):
        self.asr, self.on_turn = asr, on_turn
        self.judge = judge or frame_judge()
        self.speaking = speaking or (lambda: False)
        self.clock = clock
        self.stats = {"utterances": 0, "turns": 0, "dropped_short": 0, "dropped_quality": 0, "asr_errors": 0}
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self, frames: Iterable[np.ndarray]) -> None:
        pre = deque(maxlen=max(1, int(PREROLL_S * 1000 / FRAME_MS)))
        buf: list[np.ndarray] = []
        run_speech = silence = 0
        on_air_seen = False
        hang = max(1, int(HANGOVER_S * 1000 / FRAME_MS))
        cap = max(1, int(MAX_UTTER_S * 1000 / FRAME_MS))
        for f in frames:
            if self._stop.is_set():
                break
            speech = self.judge(f)
            if not buf:
                pre.append(f)
                run_speech = run_speech + 1 if speech else 0
                if run_speech >= OPEN_FRAMES:
                    buf = list(pre)
                    silence = 0
                    on_air_seen = self.speaking()
                continue
            buf.append(f)
            silence = 0 if speech else silence + 1
            if silence >= hang or len(buf) >= cap:
                self._flush(np.concatenate(buf), on_air_seen or self.speaking())
                buf, run_speech, silence = [], 0, 0
                pre.clear()
        if buf:
            self._flush(np.concatenate(buf), on_air_seen)

    def _flush(self, pcm: np.ndarray, her_voice: bool) -> None:
        self.stats["utterances"] += 1
        audio = pcm.astype(np.float32) / 32768.0
        spans = detect_speech_spans(audio, SR)
        if sum(s.end - s.start for s in spans) < MIN_SPEECH_S:
            self.stats["dropped_short"] += 1
            return
        try:
            out = self.asr(pcm)
        except Exception as e:  # noqa: BLE001  a dead ASR loses this turn, never the ear
            self.stats["asr_errors"] += 1
            self.stats["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
            return
        text = quality.strip_foreign(str(out.get("text") or "")).strip()
        if not text or quality.is_hallucination(text):
            self.stats["dropped_quality"] += 1
            return
        self.stats["turns"] += 1
        turn = {"ts": round(self.clock(), 3), "text": text, "duration_s": round(len(pcm) / SR, 2),
                "via": "ear", "engine": out.get("engine") or "asr", "her_voice": bool(her_voice)}
        try:
            from . import stamp as _stamp
            stamped = _stamp.stamp()
        except Exception:  # noqa: BLE001  a missing stamp leaves the turn as it was
            stamped = None
        if stamped:
            turn["speaker"] = stamped.get("speaker", "")
            turn["speaker_why"] = stamped.get("why", "")
        self.on_turn(turn)
