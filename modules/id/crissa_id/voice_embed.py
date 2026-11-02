#!/usr/bin/env python3
"""Unified voice embed router — WeSpeaker (live) or ECAPA (museum).

Env ``HOUSE_VOICE_ENCODER``:
  wespeaker | ws        → crissa_id/wespeaker_embed.py  (default; the ear's live match: the pack's one engine,
                          crissa_id/wespeaker_onnx.py, the same file the ear matches with)
  ecapa | speechbrain   → crissa_id/ecapa_embed.py      (museum numbered 001.json; speechbrain by hand)

Unset matches rail-stt: WeSpeaker. Numbered ECAPA prints are a different
space — load_voice_bank filters them out. Cosine across encoders is
meaningless — never mix banks at match time.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np

_TOOLS = Path(__file__).resolve().parent
_LOAD_ERR: dict[str, str] = {}


def encoder_name() -> str:
    raw = (
        os.environ.get("HOUSE_VOICE_ENCODER")
        or os.environ.get("RAIL_VOICE_ENCODER")
        or "wespeaker"
    ).strip().lower()
    if raw in ("ecapa", "speechbrain", "sb", "spkrec"):
        return "ecapa"
    return "wespeaker"


def _load(name: str):
    """The backend as a module OF THIS PACKAGE (crissa_id.<name>_embed). The house loaded the file by its path as a
    top-level module, which works for its flat tools folder; in a package that file's own `from . import paths` then
    fails ("attempted relative import with no known parent package"), so a fresh install never had an encoder
    (2026-10-10). A backend that cannot import is None, and status() says why."""
    if not (_TOOLS / f"{name}_embed.py").is_file():
        return None
    try:
        return importlib.import_module("%s.%s_embed" % (__package__ or "crissa_id", name))
    except Exception as e:  # noqa: BLE001
        _LOAD_ERR[name] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return None


def get_backend():
    """Return embed module for current encoder."""
    if encoder_name() == "wespeaker":
        return _load("wespeaker")
    return _load("ecapa")


def available() -> bool:
    mod = get_backend()
    return bool(mod and mod.available())


def model_id() -> str:
    mod = get_backend()
    if mod is None:
        return encoder_name()
    if encoder_name() == "wespeaker":
        return getattr(mod, "FAMILY", "wespeaker/english")
    return "speechbrain/spkrec-ecapa-voxceleb"


def dim() -> int:
    # Family owns the dim even when the backend failed to import. A wespeaker
    # bank is 256; reporting 192 here is how enroll wrote ECAPA into it.
    if encoder_name() == "wespeaker":
        mod = get_backend()
        return int(getattr(mod, "DIM", 256)) if mod is not None else 256
    mod = get_backend()
    if mod is None:
        return 192
    return int(getattr(mod, "DIM", 192))


def embed_pcm16(pcm: bytes, sr: int = 16000) -> Optional[np.ndarray]:
    mod = get_backend()
    if mod is None or not mod.available():
        return None
    return mod.embed_pcm16(pcm, sr=sr)


def status() -> dict[str, Any]:
    name = encoder_name()
    mod = get_backend()
    ok = bool(mod and mod.available())
    err = _LOAD_ERR.get(name)
    if mod is not None and hasattr(mod, "last_error"):
        err = mod.last_error()
    elif mod is not None and getattr(mod, "_err", None):
        err = mod._err
    return {
        "encoder": name,
        "model": model_id(),
        "dim": dim() if ok else None,
        "available": ok,
        "error": err,
        "note": (
            "live WeSpeaker bank (rail-stt HOUSE_VOICE_ENCODER=wespeaker)"
            if name == "wespeaker"
            else "ECAPA museum — live match will ignore these prints"
        ),
    }
