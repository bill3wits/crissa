from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineResult:
    text: str
    engine: str
    duration_s: float = 0.0
    decode_ms: float = 0.0
    score: float | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    words: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
