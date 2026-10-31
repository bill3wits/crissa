"""Configuration — env + sane defaults. No OS integration."""

from __future__ import annotations

from .settings import url as _url  # this install's addresses (the shared port table, 2026-10-10)

import os
from dataclasses import dataclass, field
from pathlib import Path

from .home import EAR, HOME

# The interpreter is the module's own env (crissa_ear.setup); this is only its default path.
DEFAULT_PYTHON = str(EAR / "env" / "bin" / "python")


# Role name, like ops/crew. Aliases are one-release / CLI compat.
LIVE_EAR_ENGINE_NAMES = frozenset({"live-ear", "parakeet", "audio8", "audio8-ark"})
DEFAULT_LIVE_EAR_URL = _url("asr")


def live_asr_name() -> str:
    """Name that belongs on published turns. Today's worker is Audio8, not the slot."""
    return (os.environ.get("RAIL_STT_ASR_ENGINE") or "audio8-ark").strip() or "audio8-ark"


def normalize_engine_name(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def is_live_ear_engine(name: str) -> bool:
    return normalize_engine_name(name) in LIVE_EAR_ENGINE_NAMES


def live_ear_url() -> str:
    """HTTP URL of the live-ear ASR slot.

    LIVE_EAR_URL is the role env. PARAKEET_URL is a one-release alias so a
    missed drop-in rename does not deafen the house. Code default is :8096
    (Audio8 today), not the retired Parakeet :8092.
    """
    for key in ("LIVE_EAR_URL", "RAIL_ASR_URL", "PARAKEET_URL"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return DEFAULT_LIVE_EAR_URL

# Model cache (whisper): under the state home unless a unit names one.
os.environ.setdefault("HF_HOME", str(HOME / "hf"))


@dataclass
class Config:
    engine: str = field(
        default_factory=lambda: os.environ.get("RAIL_STT_ENGINE", "live-ear")
    )
    live_ear_url: str = field(default_factory=live_ear_url)

    @property
    def parakeet_url(self) -> str:
        return self.live_ear_url

    whisper_model: str = field(
        default_factory=lambda: os.environ.get("RAIL_STT_WHISPER", "large-v3")
    )
    whisper_device: str = field(
        default_factory=lambda: os.environ.get("RAIL_STT_DEVICE", "cuda")
    )
    whisper_compute: str = field(
        default_factory=lambda: os.environ.get("RAIL_STT_COMPUTE", "int8_float16")
    )
    # "auto" → sole watcher in audio.resolve_mic → Insta360 Link 2 only (no echocancel)
    mic: str = field(default_factory=lambda: os.environ.get("RAIL_STT_MIC", "auto"))
    sample_rate: int = 16000
    # Quality gates (whisper segment)
    max_no_speech_prob: float = 0.55
    min_avg_logprob: float = -1.05
    # Live-ear HTTP score floor (model-dependent; None skips). Dual/lab only.
    min_parakeet_score: float | None = None
    # Consensus: if dual texts disagree and both weak, return empty rather than invent
    require_agreement_if_weak: bool = True
    language: str = "en"
    # Optional household bias for whisper only (empty = none)
    initial_prompt: str = field(
        default_factory=lambda: os.environ.get(
            "RAIL_STT_PROMPT",
            "Crissa. Clear American English conversation.",
        )
    )
    # Where to put temp wavs
    work_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("RAIL_STT_WORK", str(EAR / "work"))
        )
    )

    def ensure_work(self) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        return self.work_dir
