"""Speech activity detection — WebRTC VAD + energy floor. Standalone."""

from __future__ import annotations

from dataclasses import dataclass

import os
import sys

import numpy as np

try:
    import webrtcvad

    _HAS_WEBRTC = True
except Exception:
    _HAS_WEBRTC = False


@dataclass
class SpeechSpan:
    start: float  # seconds
    end: float


def _frame_ms_bytes(pcm16: np.ndarray, sr: int, frame_ms: int) -> list[bytes]:
    n = int(sr * frame_ms / 1000)
    raw = np.ascontiguousarray(pcm16, dtype=np.int16).tobytes()
    step = n * 2
    frames = []
    for i in range(0, len(raw) - step + 1, step):
        frames.append(raw[i : i + step])
    return frames


# ---------------------------------------------------------------- silero ---
# the User 2026-08-03: WebRTC VAD is a 2011 GMM — it needs ~100ms before it commits
# (clipped first syllables) and misses quiet/whispered speech under the energy
# floor. Silero is a ~1MB neural VAD, still CPU, far better on both. Same
# signature; RAIL_STT_VAD=webrtc forces the old path.

# the User 2026-08-04: ONNX Runtime backend by default — measured vs JIT on the
# fixtures: spans identical, streaming prob drift max 1e-5 (zero 0.5-threshold
# flips), 3.3x faster per chunk (0.068 vs 0.224 ms). Model ships in the pip
# package, no download. RAIL_STT_VAD_ONNX=0 reverts to torch JIT; JIT is also
# the automatic fallback if onnxruntime is unavailable.

_SILERO = None
_TEN_SAID = False


def vad_engine() -> str:
    """Which VAD detect_speech_spans will try first.

    Unset / silero → Silero. ten → Silero (TEN VAD left the kit in round 41).
    webrtc → 2011 GMM. One source of truth — do not re-parse RAIL_STT_VAD
    at call sites.
    """
    raw = (os.environ.get("RAIL_STT_VAD") or "silero").strip().lower()
    if raw in ("ten", "ten-vad", "ten_vad"):
        # round 41 (the User 2026-10-22: "drop TEN VAD"): the kit no longer installs or carries TEN VAD; an old
        # setting asking for it gets Silero, said once.
        global _TEN_SAID
        if not _TEN_SAID:
            _TEN_SAID = True
            print("[vad] %s=%s: TEN VAD is no longer part of Crissa; using Silero" % ("RAIL_STT_VAD", raw),
                  file=sys.stderr, flush=True)
        return "silero"
    if raw == "webrtc":
        return "webrtc"
    return "silero"


def silero_threshold() -> float:
    """Speech probability to count a Silero frame as speech.

    Default 0.5 is the library lazy value. Live house (AEC + XVF NS) can
    go lower — energy_floor_db is unused on this path.
    """
    raw = (os.environ.get("RAIL_STT_SILERO_THR") or "0.5").strip()
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.5
    return max(0.05, min(0.95, v))


def _silero_model():
    global _SILERO
    if _SILERO is None:
        from silero_vad import load_silero_vad
        want_onnx = os.environ.get("RAIL_STT_VAD_ONNX", "1") not in ("0", "false", "no")
        if want_onnx:
            try:
                _SILERO = load_silero_vad(onnx=True)
            except Exception:
                _SILERO = load_silero_vad(onnx=False)
        else:
            _SILERO = load_silero_vad(onnx=False)
    return _SILERO


def _silero_spans(audio, sr, min_speech_s, pad_s):
    import torch
    from silero_vad import get_speech_timestamps
    x = audio.astype("float32")
    if x.max() > 1.5:                      # int16-scaled input
        x = x / 32768.0
    ts = get_speech_timestamps(
        torch.from_numpy(x), _silero_model(),
        sampling_rate=(16000 if sr not in (8000, 16000) else sr),
        threshold=silero_threshold(),
        min_speech_duration_ms=int(min_speech_s * 1000),
        speech_pad_ms=int(pad_s * 1000),
        return_seconds=True,
    )
    return [SpeechSpan(float(t["start"]), float(t["end"])) for t in ts]


def detect_speech_spans(
    audio: np.ndarray,
    sr: int = 16000,
    aggressiveness: int = 2,
    frame_ms: int = 30,
    min_speech_s: float = 0.25,
    pad_s: float = 0.25,
    energy_floor_db: float = -45.0,
) -> list[SpeechSpan]:
    """
    Return speech spans in seconds. Pads each span; merges near neighbors.
    Falls back to energy-only if webrtcvad is missing.
    """
    if len(audio) == 0:
        return []

    engine = vad_engine()
    if engine != "webrtc":
        try:
            return _silero_spans(audio, sr, min_speech_s, pad_s)
        except Exception:
            pass          # any trouble at all: fall through to webrtc/energy

    # Normalize peak lightly so quiet talk still trips VAD (cap gain)
    peak = float(np.max(np.abs(audio)) + 1e-9)
    gain = min(8.0, 0.5 / peak) if peak < 0.3 else 1.0
    x = np.clip(audio * gain, -1.0, 1.0)
    pcm = (x * 32767.0).astype(np.int16)

    frame_len = int(sr * frame_ms / 1000)
    n_frames = len(pcm) // frame_len
    if n_frames == 0:
        return []

    speech_flags = np.zeros(n_frames, dtype=bool)

    if _HAS_WEBRTC and sr in (8000, 16000, 32000, 48000):
        vad = webrtcvad.Vad(int(aggressiveness))
        for i in range(n_frames):
            frame = pcm[i * frame_len : (i + 1) * frame_len]
            try:
                speech_flags[i] = vad.is_speech(frame.tobytes(), sr)
            except Exception:
                speech_flags[i] = False
    else:
        # Energy-only fallback
        for i in range(n_frames):
            frame = pcm[i * frame_len : (i + 1) * frame_len].astype(np.float32) / 32768.0
            r = float(np.sqrt(np.mean(frame * frame) + 1e-12))
            db = 20.0 * np.log10(r + 1e-12)
            speech_flags[i] = db > energy_floor_db

    # Also require energy floor on webrtc hits (kill fan hum false positives)
    for i in range(n_frames):
        if not speech_flags[i]:
            continue
        frame = pcm[i * frame_len : (i + 1) * frame_len].astype(np.float32) / 32768.0
        r = float(np.sqrt(np.mean(frame * frame) + 1e-12))
        db = 20.0 * np.log10(r + 1e-12)
        if db < energy_floor_db - 6:
            speech_flags[i] = False

    spans: list[SpeechSpan] = []
    i = 0
    while i < n_frames:
        if not speech_flags[i]:
            i += 1
            continue
        j = i
        while j < n_frames and speech_flags[j]:
            j += 1
        start = i * frame_ms / 1000.0
        end = j * frame_ms / 1000.0
        if end - start >= min_speech_s:
            spans.append(SpeechSpan(start=start, end=end))
        i = j

    if not spans:
        return []

    # Pad + merge near neighbors
    pad = pad_s
    dur = len(audio) / sr
    clean: list[SpeechSpan] = []
    for sp in spans:
        a = max(0.0, sp.start - pad)
        b = min(dur, sp.end + pad)
        if clean and a <= clean[-1].end + 0.15:
            clean[-1] = SpeechSpan(start=clean[-1].start, end=max(clean[-1].end, b))
        else:
            clean.append(SpeechSpan(start=a, end=b))
    return clean


def extract_spans(audio: np.ndarray, sr: int, spans: list[SpeechSpan]) -> np.ndarray:
    """Concatenate speech spans (or full audio if none)."""
    if not spans:
        return audio
    parts = []
    for sp in spans:
        i0 = int(sp.start * sr)
        i1 = int(sp.end * sr)
        parts.append(audio[i0:i1])
    if not parts:
        return audio
    return np.concatenate(parts)
