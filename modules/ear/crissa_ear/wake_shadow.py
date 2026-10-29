"""Wake-word shadow scorer — log-only, never gates anything.

the User assent 2026-08-05 (card t_502f07eb step 3): score every stamped
utterance with the openwakeword ORT features + the trained 'Crissa' verifier
head, alongside (not instead of) the regex wake path in ear-bridge. Output
goes to the stamp dict + logs/wake_shadow.jsonl so the head's live hit-rate
is measurable before any promote.

Enable: RAIL_WAKE_SHADOW=1 (drop-in). Head/model paths overridable via env.
Featurization mirrors train/wake-crissa/train_head.py exactly: melspec →
76-frame windows stride 8 → 96-d embeddings → mean+max pool → normalized
MLP. A mismatch here silently ruins scores — change both together.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .home import EAR, HOME

# No head ships: the house trained its own on the owner's voice. A head placed here is scored, log only.
MODELS = Path(os.environ.get("RAIL_WAKE_MODELS", str(EAR / "wake")))
HEAD = Path(os.environ.get("RAIL_WAKE_HEAD", str(MODELS / "head.npz")))
THR = float(os.environ.get("RAIL_WAKE_SHADOW_THR", "0.5"))
LOG = HOME / "logs" / "wake_shadow.jsonl"
WIN, STRIDE, RATE = 76, 8, 16000

_state: dict[str, Any] = {}


def enabled() -> bool:
    return os.environ.get("RAIL_WAKE_SHADOW", "0") in ("1", "true", "yes")


def _load() -> bool:
    if _state.get("ok") is not None:
        return _state["ok"]
    try:
        import numpy as np
        import onnxruntime as ort

        _state["mel"] = ort.InferenceSession(
            str(MODELS / "melspectrogram.onnx"), providers=["CPUExecutionProvider"])
        _state["emb"] = ort.InferenceSession(
            str(MODELS / "embedding_model.onnx"), providers=["CPUExecutionProvider"])
        w = np.load(HEAD)
        _state["w"] = {k: w[k] for k in ("mu", "sd", "w1", "b1", "w2", "b2")}
        _state["np"] = np
        _state["ok"] = True
    except Exception as e:
        _state["ok"] = False
        _state["err"] = str(e)[:160]
    return _state["ok"]


def score_pcm(pcm: bytes, sr: int = RATE) -> dict[str, Any] | None:
    """Score int16 mono PCM; returns shadow dict or None when off/unloadable."""
    if not enabled() or not pcm or not _load():
        return None
    np = _state["np"]
    t0 = time.time()
    try:
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if sr != RATE:
            n_out = int(len(x) * RATE / sr)
            x = np.interp(np.linspace(0, 1, n_out, endpoint=False),
                          np.linspace(0, 1, len(x), endpoint=False), x).astype(np.float32)
        if len(x) < RATE // 2:
            return None
        m = _state["mel"].run(None, {"input": x[None, :]})[0].reshape(-1, 32)
        if m.shape[0] < WIN:
            m = np.concatenate([m, np.zeros((WIN - m.shape[0], 32), np.float32)])
        wins = np.stack([m[i:i + WIN] for i in range(0, m.shape[0] - WIN + 1, STRIDE)])
        e = _state["emb"].run(None, {"input_1": wins[..., None].astype(np.float32)})[0]
        e = e.reshape(e.shape[0], 96)
        f = np.concatenate([e.mean(0), e.max(0)])
        w = _state["w"]
        z = (f - w["mu"]) / w["sd"]
        h = np.maximum(w["w1"] @ z + w["b1"], 0.0)
        logit = float((w["w2"] @ h + w["b2"]).reshape(-1)[0])
        score = 1.0 / (1.0 + np.exp(-logit))
        out = {"score": round(float(score), 4), "would_wake": bool(score >= THR),
               "thr": THR, "head": HEAD.name, "ms": int((time.time() - t0) * 1000)}
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a") as fh:
                fh.write(json.dumps({"ts": time.time(), **out}) + "\n")
        except Exception:
            pass
        return out
    except Exception as e:
        return {"error": str(e)[:120]}
