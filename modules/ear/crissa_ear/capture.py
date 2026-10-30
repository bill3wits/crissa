"""Capture backends beside parec (2026-10-09): sounddevice for a machine without PulseAudio, and the frames
the Wall page posts (`mic == "page"`).

The house captured through `parec` from one named array, one 0.3 s read at a time (audio.record_seconds).
A pack runs on a Mac or a box without PulseAudio and may listen through the browser's microphone (the
player forwards the page's PCM to this service's /v1/mic). Both are served here with the same contract
the ear loop has always used: `read_frames(source, n_samples, sr)` returns n_samples of int16 mono.

    PAGE            the source name that means "the page's frames"
    PushedFrames    the queue /v1/mic fills (one per service)
    resolve_device  sounddevice: a name (substring), an index, or "auto" (the default input)
"""
from __future__ import annotations

import collections
import shutil
import threading
import time

import numpy as np

PAGE = "page"
_PUSHED: "PushedFrames | None" = None
_pulse: bool | None = None


def pulse_available() -> bool:
    """Is PulseAudio/PipeWire here (pactl + parec on PATH)? Decided once."""
    global _pulse
    if _pulse is None:
        _pulse = bool(shutil.which("pactl") and shutil.which("parec"))
    return _pulse


class PushedFrames:
    """PCM the page posts, resampled to 16 kHz as it arrives; read_frames() takes it in order."""

    def __init__(self, sr: int = 16000, max_s: float = 30.0):
        self.sr = sr
        self._buf: collections.deque = collections.deque()
        self._n = 0
        self._max = int(max_s * sr)
        self._cv = threading.Condition()
        self.stats = {"posts": 0, "samples": 0, "dropped": 0, "last_ts": 0.0}

    def push(self, raw: bytes, sr: int) -> int:
        pcm = np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype=np.int16)
        if sr != self.sr and len(pcm) > 1:
            n = int(len(pcm) * self.sr / float(sr))
            x = np.linspace(0, len(pcm) - 1, n)
            pcm = np.interp(x, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)
        with self._cv:
            self._buf.append(pcm)
            self._n += len(pcm)
            while self._n > self._max and self._buf:
                old = self._buf.popleft()
                self._n -= len(old)
                self.stats["dropped"] += len(old)
            self.stats["posts"] += 1
            self.stats["samples"] += len(pcm)
            self.stats["last_ts"] = time.time()
            self._cv.notify_all()
        return len(pcm)

    def read(self, n: int, timeout: float) -> np.ndarray:
        """n samples, padded with silence when the page goes quiet for `timeout` seconds."""
        out: list[np.ndarray] = []
        got = 0
        deadline = time.monotonic() + timeout
        with self._cv:
            while got < n:
                if self._buf:
                    head = self._buf[0]
                    take = min(len(head), n - got)
                    out.append(head[:take])
                    got += take
                    if take == len(head):
                        self._buf.popleft()
                    else:
                        self._buf[0] = head[take:]
                    self._n -= take
                    continue
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cv.wait(min(left, 0.2))
        if got < n:
            out.append(np.zeros(n - got, dtype=np.int16))
        return np.concatenate(out) if out else np.zeros(n, dtype=np.int16)


def pushed() -> PushedFrames:
    global _PUSHED
    if _PUSHED is None:
        _PUSHED = PushedFrames()
    return _PUSHED


def resolve_device(pref: str):
    """sounddevice input: an index, a name substring, or the default input ("auto")."""
    p = (pref or "auto").strip()
    if p in ("", "auto", "default", "house"):
        return None
    if p.isdigit():
        return int(p)
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and p.lower() in str(d.get("name", "")).lower():
                return i
    except Exception:
        pass
    return p


def read_frames(source, n_samples: int, sr: int) -> np.ndarray:
    """n_samples of int16 mono from the page's frames or a sounddevice input."""
    if source == PAGE:
        return pushed().read(n_samples, timeout=max(0.5, n_samples / float(sr) * 3))
    import sounddevice as sd
    dev = None if source in (None, "", "auto", "default") else source
    rec = sd.rec(n_samples, samplerate=sr, channels=1, dtype="int16", device=dev)
    sd.wait()
    return np.asarray(rec, dtype=np.int16).reshape(-1)


def record_until_silence(source, seconds_max: float, sr: int, hangover_sec: float, frame_sec: float,
                         min_speech_s: float, detect_speech_spans, to_float) -> np.ndarray:
    """audio.record_until_silence for the non-parec backends: frame by frame until speech has ended."""
    frame = max(1, int(frame_sec * sr))
    parts: list[np.ndarray] = []
    total = 0
    cap = int(seconds_max * sr)
    while total < cap:
        parts.append(read_frames(source, min(frame, cap - total), sr))
        total += len(parts[-1])
        if total < sr:
            continue
        audio = to_float(np.concatenate(parts))
        try:
            spans = detect_speech_spans(audio, sr, min_speech_s=min_speech_s)
        except Exception:
            spans = []
        if spans:
            last_end = max(float(getattr(sp, "end", 0.0) or (sp[1] if isinstance(sp, (tuple, list)) else 0.0))
                           for sp in spans)
            if len(audio) / sr - last_end >= hangover_sec:
                break
    pcm = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
    if len(pcm) < sr:
        pcm = np.concatenate([pcm, np.zeros(sr - len(pcm), dtype=np.int16)])
    return to_float(pcm)
