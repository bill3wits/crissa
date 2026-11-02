"""The ear's voice encoder: WeSpeaker ResNet221 (English) through ONNX Runtime, else ECAPA (2026-10-09; one engine 2026-10-10).

The house embedded voices through a router in its identity tools: WeSpeaker live (an ONNX export of the
wespeaker hub model `english`, run on the CPU, bit-exact with the torch path and 1.75-3x faster), ECAPA
kept as the revert path. This is that router, carried into the ear package. Its WeSpeaker path is the pack's ONE
voice-ID engine, wespeaker_onnx.py beside this file (the same file the Register coach writes prints with, in the id and
presence modules): WeSpeaker's published ONNX of the same checkpoint, pinned by revision and sha256, with the kaldi
fbank computed in numpy - no torch, no `wespeaker` package. Nothing of anyone's voice is here: the model is a speaker
encoder, the prints live in the identity store (<crissa home>/identity/people/<id>/voice/*.json).

    HOUSE_VOICE_ENCODER     wespeaker (default) | ecapa (speechbrain, when installed by hand: the house's revert path)
    CRISSA_VOICE_ID_MODEL   another ONNX file (the house's HOUSE_VOICE_ORT_MODEL too); default
                            <crissa home>/models/voice-id/voxceleb_resnet221_LM.onnx, which the ear's --install fetches
    HOUSE_VOICE_ORT_THREADS 2

Without the model file `available()` is False and the ear runs without voice ID: every turn is stamped by presence
alone, and /health says `voice_encoder_ok: false`.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from . import wespeaker_onnx as W
from .home import EAR, HOME

SR = W.SR
DIM = W.DIM
ORT_ENABLED = os.environ.get("HOUSE_VOICE_ORT", "1") not in ("0", "false", "no")
_ecapa = None
_ecapa_err: Optional[str] = None


def encoder_name() -> str:
    raw = (os.environ.get("HOUSE_VOICE_ENCODER") or os.environ.get("RAIL_VOICE_ENCODER") or "wespeaker").strip().lower()
    return "ecapa" if raw in ("ecapa", "speechbrain", "sb", "spkrec") else "wespeaker"


def model_id() -> str:
    return "speechbrain/spkrec-ecapa-voxceleb" if encoder_name() == "ecapa" else W.MODEL_ID


def dim() -> int:
    return 192 if encoder_name() == "ecapa" else DIM


def ort_model() -> Path:
    """The engine's file: CRISSA_VOICE_ID_MODEL (or HOUSE_VOICE_ORT_MODEL), else the state home's pinned copy."""
    return W.model_path(HOME)


_ORT_LOCK = threading.Lock()


def _get_ort():
    # one session per process: the warm-up, /health and the first turn's print may all ask at once (round 15)
    if not ORT_ENABLED:
        return None
    with _ORT_LOCK:
        return W.session(ort_model())


def _get_ecapa():
    global _ecapa, _ecapa_err
    if _ecapa is not None or _ecapa_err:
        return _ecapa
    try:
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore

        _ecapa = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                                savedir=str(EAR / "ecapa"), run_opts={"device": "cpu"})
    except Exception as e:  # noqa: BLE001
        _ecapa_err = str(e)[:200]
    return _ecapa


def available() -> bool:
    return (_get_ecapa() if encoder_name() == "ecapa" else _get_ort()) is not None


def last_error() -> Optional[str]:
    return _ecapa_err if encoder_name() == "ecapa" else W.last_error(ort_model())


def _emb_ort(w: np.ndarray) -> Optional[np.ndarray]:
    if not ORT_ENABLED or _get_ort() is None:
        return None
    return W.embed_wav16k(w, ort_model())


def _emb_ecapa(w: np.ndarray) -> Optional[np.ndarray]:
    m = _get_ecapa()
    if m is None:
        return None
    try:
        import torch

        emb = m.encode_batch(torch.from_numpy(w).float().unsqueeze(0))
        v = np.asarray(emb.detach().cpu().numpy(), dtype=np.float32).reshape(-1)
        n = float(np.linalg.norm(v))
        return None if n < 1e-8 else v / n
    except Exception as e:  # noqa: BLE001
        global _ecapa_err
        _ecapa_err = str(e)[:200]
        return None


def embed_wav16k(wav: np.ndarray) -> Optional[np.ndarray]:
    w = np.asarray(wav, dtype=np.float32).reshape(-1)
    if w.size < SR // 2:
        return None
    return _emb_ecapa(w) if encoder_name() == "ecapa" else _emb_ort(w)


def embed_pcm16(pcm: bytes, sr: int = 16000) -> Optional[np.ndarray]:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return embed_wav16k(W.resample16k(audio, sr))


def status() -> dict[str, Any]:
    return {"encoder": encoder_name(), "model": model_id(), "dim": dim(), "available": available(),
            "ort_model": str(ort_model()), "pinned": W.status(HOME)["pinned"], "error": last_error()}


# THE PRINT IS READY BEFORE THE EAR OPENS (round 15, .101 2026-10-12 14:24:34, the first "Hey, Chrisa." after a fresh
# start heard with no voice print). The encoder was built on the first turn that needed it - a 95 MB ResNet221 graph,
# optimised on the CPU while Setup's ASCII build ran - and the turn waits 4 s for its print, so the first words of a
# fresh ear could be stamped without one. The ear now builds it, and runs one print through it, as it starts; opening
# the ear waits for that (WARM_WAIT_S at most). Kill: CRISSA_EAR_VOICE_WARM=0 (built on first use, as before).
WARM_ON = os.environ.get("CRISSA_EAR_VOICE_WARM", "1").strip().lower() not in ("0", "false", "no", "off")
WARM_WAIT_S = float(os.environ.get("CRISSA_EAR_VOICE_WARM_WAIT_S", "30"))
_warm: dict[str, Any] = {"state": "cold", "ms": None, "error": None}
_warm_done = threading.Event()
_warm_lock = threading.Lock()


def warm() -> dict[str, Any]:
    """Build the encoder and print one second of quiet noise through it. {"state": "warm" | "none" (no encoder on
    this machine: the ear runs on presence alone) | "error", "ms"}."""
    t0 = time.monotonic()
    try:
        if not available():
            _warm.update(state="none", ms=round((time.monotonic() - t0) * 1000), error=last_error())
        else:
            noise = (np.random.default_rng(0).standard_normal(SR) * 0.003).astype(np.float32)
            got = embed_wav16k(noise)
            _warm.update(state="warm" if got is not None else "error",
                         ms=round((time.monotonic() - t0) * 1000), error=None if got is not None else last_error())
    except Exception as e:  # noqa: BLE001 - a warm-up never stops the ear
        _warm.update(state="error", ms=round((time.monotonic() - t0) * 1000), error="%s: %s" % (type(e).__name__, e))
    finally:
        _warm_done.set()
    return dict(_warm)


def start_warm() -> bool:
    """warm() on a thread of its own, once. False when switched off."""
    if not WARM_ON:
        _warm.update(state="off")
        _warm_done.set()
        return False
    with _warm_lock:
        if _warm["state"] != "cold":
            return True
        _warm["state"] = "warming"
    threading.Thread(target=warm, name="voice-warm", daemon=True).start()
    return True


def wait_warm(timeout: float | None = None) -> dict[str, Any]:
    """Wait for the warm-up (never longer than `timeout`, WARM_WAIT_S by default); a warm-up never started does not
    wait. The state after the wait, with `waited_ms`."""
    t0 = time.monotonic()
    if _warm["state"] not in ("cold", "off"):
        _warm_done.wait(WARM_WAIT_S if timeout is None else max(0.0, float(timeout)))
    return dict(_warm, waited_ms=round((time.monotonic() - t0) * 1000))


def warm_status() -> dict[str, Any]:
    return dict(_warm)
