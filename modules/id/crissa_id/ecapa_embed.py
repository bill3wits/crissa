#!/usr/bin/env python3
"""ECAPA embed helper — same model as museum (speechbrain/spkrec-ecapa-voxceleb).

Prefer env with speechbrain (e.g. <the id env's python>).
"""
from __future__ import annotations

try:
    from . import paths as _paths
except ImportError:  # run as a file, not a module
    import paths as _paths

import os
from pathlib import Path
from typing import Optional

import numpy as np

_encoder = None
_err: Optional[str] = None
DIM = 192
SR = 16000


def available() -> bool:
    return get_encoder() is not None


def get_encoder():
    global _encoder, _err
    if _encoder is not None:
        return _encoder
    if _err:
        return None
    try:
        from speechbrain.inference.speaker import EncoderClassifier

        savedir = os.path.expanduser(
            os.environ.get("ECAPA_SAVEDIR", str(_paths.STATE / "id" / "models" / "ecapa-voxceleb"))
        )
        Path(savedir).mkdir(parents=True, exist_ok=True)
        _encoder = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=savedir,
            run_opts={"device": "cpu"},
        )
        return _encoder
    except Exception as e:
        _err = str(e)[:200]
        return None


def embed_wav16k(wav: np.ndarray) -> Optional[np.ndarray]:
    enc = get_encoder()
    if enc is None:
        return None
    w = np.asarray(wav, dtype=np.float32).reshape(-1)
    if w.size < SR // 2:
        return None
    import torch

    t = torch.from_numpy(w).float().unsqueeze(0)
    with torch.no_grad():
        emb = enc.encode_batch(t)
    v = emb.squeeze().cpu().numpy().astype(np.float32)
    n = float(np.linalg.norm(v))
    return None if n < 1e-8 else v / n


def embed_pcm16(pcm: bytes, sr: int = 16000) -> Optional[np.ndarray]:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000 and len(audio) > 1:
        # linear resample
        dur = len(audio) / float(sr)
        n = int(dur * 16000)
        x = np.linspace(0, len(audio) - 1, n)
        audio = np.interp(x, np.arange(len(audio)), audio).astype(np.float32)
    return embed_wav16k(audio)
