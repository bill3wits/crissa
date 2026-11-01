#!/usr/bin/env python3
"""The voice module's Chatterbox Multilingual V3 service (2026-10-06): an optional GPU voice for languages other than
English, on loopback, the same /stream contract and the same server code as Turbo (scripts/crissa-turbo.py).

    python3 modules/voice/scripts/crissa-multilingual.py --install     # opt-in; runs in Turbo's env
    python3 modules/voice/scripts/crissa-multilingual.py [--port 8185]
    python3 modules/voice/scripts/crissa-multilingual.py --self-test

POST /stream takes {"text", "voice"[, "language"]}: the language is the request's, else
multilingual.settings.language in the tier table ("auto" = the language Init detected, voice-tier.json
facts.language), else English. Voices: <crissa home>/multilingual/voices/<face>.wav only, cloned here on their own
(its speaker embeddings are not Turbo's). The Perth watermark is on, as upstream ships it; the same key turns it off
(multilingual.settings.watermark, or CRISSA_MULTILINGUAL_WATERMARK=0) and /health always says which.
Model: Multilingual V3 (t3_mtl23ls_v3) loaded through chatterbox-tts 0.1.7's own loader, which names the v2 file;
the load is pointed at the pinned v3 file (upstream's later loader takes t3_model="v3" the same way).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from crissa_voice import multilingual_setup as M  # noqa: E402

_spec = importlib.util.spec_from_file_location("crissa_turbo_server", HERE / "crissa-turbo.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

DEFAULTS = {"watermark": True, "chunk_first_min": 0, "chunk_min": 0, "chunk_max": 80, "temperature": 0.8,
            "seed": 0, "vram_cap_gb": 4.0, "headroom_gb": 2.0, "language": "auto", "exaggeration": 0.5,
            "cfg_weight": 0.5}


def detected_language(home: Path | None = None) -> str:
    try:
        rec = json.loads(((home or M._home()) / "voice-tier.json").read_text(encoding="utf-8"))
        return str(((rec.get("facts") or {}).get("language") or {}).get("code") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def language_for(req_lang: str = "", st: dict | None = None, home: Path | None = None) -> tuple[str, str]:
    """(code, where it came from). Unknown codes are said and English speaks."""
    st = st or B.RT["settings"]
    for code, where in ((req_lang, "the request"), ("" if str(st.get("language") or "auto") == "auto"
                                                    else str(st["language"]), "multilingual.settings.language"),
                        (detected_language(home), "Init's language detection")):
        code = (code or "").strip().lower()
        if code in M.LANGUAGES:
            return code, where
        if code:
            B._log(event="language", note="%r from %s is not one of its languages; English speaks" % (code, where))
            return "en", "fallback"
    return "en", "default"


def load(st: dict) -> None:
    import torch  # noqa: PLC0415
    free, total = torch.cuda.mem_get_info(0)
    ok, why = B.room(free, B.own_gpu_bytes(), st)
    B._log(event="gpu_room", note=why.replace("Turbo", "Multilingual").replace("turbo.settings", "multilingual.settings"))
    if not ok:
        raise RuntimeError("only " + why.replace("Turbo", "Multilingual") + ". The next voice in the chain speaks.")
    torch.cuda.set_per_process_memory_fraction(min(1.0, float(st["vram_cap_gb"]) * B.GiB / total), 0)
    import perth  # noqa: PLC0415
    if st["watermark"] and getattr(perth, "PerthImplicitWatermarker", None) is None:
        raise RuntimeError("the Perth watermarker cannot load on this machine and multilingual.settings.watermark is "
                           "on; it will not speak unmarked unless you set it off")
    if not st["watermark"] and getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
    import chatterbox.models.t3.t3 as t3mod  # noqa: PLC0415
    import chatterbox.mtl_tts as mt  # noqa: PLC0415
    t3mod.tqdm = B._abortable_range
    real = mt.load_safetensors
    mt.load_safetensors = lambda p, *a, **k: real(str(p).replace("t3_mtl23ls_v2.safetensors", M.T3), *a, **k)
    try:
        m = mt.ChatterboxMultilingualTTS.from_local(str(M.MODEL), "cuda")
    finally:
        mt.load_safetensors = real
    if not st["watermark"]:
        m.watermarker.apply_watermark = lambda wav, sample_rate=B.SR: wav
    B.RT["model"], B.RT["builtin"] = m, m.conds


def render(chunk: str, face: str, seed: int) -> bytes:
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415
    m = B.RT["model"]
    m.conds = B.conds_for(face)
    lang, _ = language_for(B.RT.get("language") or "")
    st = B.RT["settings"]
    torch.manual_seed(seed)
    with torch.inference_mode():
        wav = m.generate(chunk, language_id=lang, temperature=float(st["temperature"]),
                         exaggeration=float(st["exaggeration"]), cfg_weight=float(st["cfg_weight"])).squeeze(0).numpy()
    return (np.clip(wav, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def health_extra() -> dict:
    lang, where = language_for("") if B.RT.get("settings") else ("en", "default")
    return {"language": lang, "language_from": where, "languages": sorted(M.LANGUAGES),
            "reduced": {k: v for k, v in M.REDUCED.items()}, "model_t3": M.T3}


def use() -> None:
    B.PROFILE.update(tier="multilingual", svc="crissa-multilingual", engine="chatterbox-multilingual-v3",
                     key="multilingual", port_env="CRISSA_PORT_MULTILINGUAL", port=8185,
                     watermark_env="CRISSA_MULTILINGUAL_WATERMARK", script=str(Path(__file__).resolve()),
                     label="Chatterbox Multilingual")
    B.S, B.DEFAULTS, B.load, B.render, B.health_extra = M, dict(DEFAULTS), load, render, health_extra
    B.RT["settings"] = dict(DEFAULTS)


if __name__ == "__main__":
    use()
    raise SystemExit(B.main())
