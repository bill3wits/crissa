#!/usr/bin/env python3
"""WeSpeaker embed helper: the id module's side of the pack's ONE voice-ID engine (2026-10-10).

The house's helper ran WeSpeaker's hub `english` model (VoxCeleb ResNet221-LM, 256-d) through an ONNX export on
onnxruntime, with the `wespeaker` package's torch path as a fallback. The pack runs only the ONNX path, through
wespeaker_onnx.py beside this file - byte for byte the file the ear matches with - on WeSpeaker's published ONNX of
the same checkpoint (pinned by revision and sha256; the id module's --install fetches it into
<state home>/models/voice-id/, one file for every module), with the kaldi fbank in numpy. The `wespeaker` package
and its torch hub path are gone: the package is git-only (not on PyPI) and calls torchaudio.set_audio_backend, which
the locked torchaudio removed, so a fresh install could never load it.

Env:
  HOUSE_VOICE_ENCODER=wespeaker      # selected via voice_embed.py (the default)
  CRISSA_VOICE_ID_MODEL=<file>       # another ONNX file (the house's HOUSE_VOICE_ORT_MODEL too)
  HOUSE_VOICE_ORT=0                  # no voice ID (the house's switch)
  HOUSE_VOICE_ORT_THREADS=2

Upstream: https://github.com/wenet-e2e/wespeaker (Apache-2.0); the model: wespeaker_onnx.py's MODEL_* and LICENCE.
"""
from __future__ import annotations

from . import paths as _paths
from . import wespeaker_onnx as W

import os
import wave
from pathlib import Path
from typing import Optional

import numpy as np

SR = W.SR
DIM = W.DIM
MODEL_ID = "english"                         # the hub key the house named; the family below is what prints carry
FAMILY = "wespeaker/english"
ORT_ENABLED = os.environ.get("HOUSE_VOICE_ORT", "1") not in ("0", "false", "no")
_err: Optional[str] = None


def ort_model() -> Path:
    """The engine's file: CRISSA_VOICE_ID_MODEL (or HOUSE_VOICE_ORT_MODEL), else <state home>/models/voice-id/."""
    return W.model_path(_paths.STATE)


def available() -> bool:
    return ORT_ENABLED and W.session(ort_model()) is not None


def last_error() -> Optional[str]:
    if not ORT_ENABLED:
        return "HOUSE_VOICE_ORT=0: voice ID is off"
    return _err or W.last_error(ort_model())


def embed_wav16k(wav: np.ndarray) -> Optional[np.ndarray]:
    if not ORT_ENABLED:
        return None
    return W.embed_wav16k(wav, ort_model())


def embed_pcm16(pcm: bytes, sr: int = 16000) -> Optional[np.ndarray]:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return embed_wav16k(W.resample16k(audio, sr))


def embed_wav_path(path: str | Path) -> Optional[np.ndarray]:
    """A 16-bit WAV (any rate, mono or not) through the engine; stdlib wave only."""
    global _err
    try:
        with wave.open(str(path), "rb") as wf:
            sr, nch, sw = int(wf.getframerate()), int(wf.getnchannels()), int(wf.getsampwidth())
            raw = wf.readframes(wf.getnframes())
    except Exception as e:  # noqa: BLE001
        _err = "wav load: %s" % str(e)[:180]
        return None
    if sw != 2:
        _err = "wav load: %d-byte samples (16-bit PCM only)" % sw
        return None
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        audio = audio.reshape(-1, nch).mean(axis=1)
    return embed_wav16k(W.resample16k(audio, sr))
