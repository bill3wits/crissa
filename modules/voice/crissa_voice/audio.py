"""Audio helpers for the voice module (cut from the house's rail-stt audio.py at 2496162, 2026-10-01):
resampling, PCM conversion, a wav writer and RMS. Capture lives in listen.py."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def resample_linear(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 1:
        return np.zeros(0, dtype=np.float32)
    xp = np.linspace(0, len(x) - 1, num=len(x), dtype=np.float64)
    xout = np.linspace(0, len(x) - 1, num=n_out, dtype=np.float64)
    return np.interp(xout, xp, x.astype(np.float64)).astype(np.float32)


def float_to_pcm16(x: np.ndarray) -> np.ndarray:
    x = np.clip(x.astype(np.float32), -1.0, 1.0)
    return (x * 32767.0).astype(np.int16)


def pcm16_to_float(pcm: np.ndarray) -> np.ndarray:
    return pcm.astype(np.float32) / 32768.0


def write_wav_pcm16(path: str | Path, pcm: np.ndarray, sr: int = 16000) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.ascontiguousarray(pcm, dtype=np.int16).tobytes())


def rms_db(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64))) + 1e-12))
    return float(20.0 * np.log10(r + 1e-12))
