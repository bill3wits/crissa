"""The presence module's voice-ID router: the Register coach's encoder when modules/id is not installed (2026-10-10).

The coach in crissa_presence.enroll_panel writes the User's voice prints with this, and the ear matches them with the
same engine: wespeaker_onnx.py beside this file is byte for byte the ear's and the id module's (WeSpeaker's published
ONNX of the house's ResNet221 checkpoint on onnxruntime, numpy fbank; tests/test_crissa_voice_id.py holds the copies
equal). Before this file the presence copy did `import voice_embed` from a folder that never had one, so an install
without modules/id could not make a voice print at all.

    HOUSE_VOICE_ENCODER     wespeaker (the only engine here; "ecapa" names none: speechbrain is not in this env)
    CRISSA_VOICE_ID_MODEL   another ONNX file (HOUSE_VOICE_ORT_MODEL too); default <state home>/models/voice-id/...
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import numpy as np

from . import paths as _paths
from . import wespeaker_onnx as W

FAMILY_MODEL = "wespeaker/english"          # the model string the house's coach writes on a print


def encoder_name() -> str:
    raw = (os.environ.get("HOUSE_VOICE_ENCODER") or os.environ.get("RAIL_VOICE_ENCODER") or "wespeaker").strip().lower()
    return "ecapa" if raw in ("ecapa", "speechbrain", "sb", "spkrec") else "wespeaker"


def ort_model() -> Path:
    return W.model_path(_paths.HOME)


def model_id() -> str:
    return "speechbrain/spkrec-ecapa-voxceleb" if encoder_name() == "ecapa" else FAMILY_MODEL


def dim() -> int:
    return 192 if encoder_name() == "ecapa" else W.DIM


def available() -> bool:
    return encoder_name() == "wespeaker" and W.session(ort_model()) is not None


def last_error() -> Optional[str]:
    if encoder_name() != "wespeaker":
        return "ECAPA is not installed in the presence module (speechbrain); the pack's engine is WeSpeaker"
    return W.last_error(ort_model())


def embed_pcm16(pcm: bytes, sr: int = 16000) -> Optional[np.ndarray]:
    if encoder_name() != "wespeaker":
        return None
    return W.embed_pcm16(pcm, ort_model(), sr=sr)


def status() -> dict[str, Any]:
    ok = available()
    return {"encoder": encoder_name(), "model": model_id(), "dim": dim() if ok else None, "available": ok,
            "file": str(ort_model()), "error": None if ok else last_error()}
