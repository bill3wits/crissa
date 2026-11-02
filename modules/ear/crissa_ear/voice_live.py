"""Voice ID in parallel with ASR — wall/HUD as soon as the embed ranks.

Does not wait for the camera. TTS-mouth veto still applies. Gold name is a
ranked candidate; green is sim at/above the live match floor.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-id")


def kick(pcm: bytes, sr: int) -> Future:
    return _POOL.submit(embed_rank_publish, pcm, sr)


def embed_rank_publish(pcm: bytes, sr: int = 16000) -> dict[str, Any]:
    out: dict[str, Any] = {
        "emb": None,
        "pid": None,
        "sim": None,
        "match": False,
        "veto": False,
    }
    # One 0.3 s 16 kHz s16 frame is 9600 bytes. Vocatives must rank, not wait.
    if not pcm or len(pcm) < 8000:
        return out
    try:
        from .server import _try_ecapa

        emb = _try_ecapa(pcm, sr)
    except Exception:
        return out
    if emb is None:
        return out
    import numpy as np

    e = np.asarray(emb, dtype=np.float32).reshape(-1)
    out["emb"] = e
    try:
        from .voice_bank import match_sim_for, rank_voice

        ranked = rank_voice(e, top_k=2)
    except Exception:
        ranked = []
    top_sim = float(ranked[0][1]) if ranked else None
    try:
        from .stamp import tts_veto_check

        veto = tts_veto_check([float(x) for x in e.tolist()], top_sim=top_sim)
    except Exception:
        veto = None
    if veto:
        out["veto"] = True
        return out
    if not ranked:
        return out
    pid, sim = ranked[0]
    try:
        thr = float(match_sim_for(pid))
    except Exception:
        thr = 0.50
    match = float(sim) >= thr
    out["pid"] = str(pid).strip().lower()
    out["sim"] = float(sim)
    out["match"] = match
    out["thr"] = thr
    _publish(out)
    return out


def _publish(info: dict[str, Any]) -> None:
    pid = info.get("pid")
    sim = info.get("sim")
    match = bool(info.get("match"))
    if sim is None:
        return
    try:
        from .presence import post_sound_heard, post_speaker_voice, post_user_voice

        post_speaker_voice(
            person_id=str(pid) if pid else None,
            sim=float(sim),
            match=match,
        )
        if pid:
            # Initial guess names the lobe before the camera. certain=match
            # is green; a ranked pid still goes on speaker.voice (gold).
            post_sound_heard(
                person_id=str(pid),
                speech=True,
                source="voice_id",
                id_source="voice_emb",
                certain=bool(match),
                voice_emb_sim=float(sim),
            )
        if match and pid:
            threading.Thread(
                target=post_user_voice,
                args=(str(pid), float(sim)),
                kwargs={"source": "voice_id"},
                daemon=True,
            ).start()
    except Exception:
        pass
