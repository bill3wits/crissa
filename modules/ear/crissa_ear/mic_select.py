"""Near-field desk microphone vs the room microphone — liveness before VAD.

No toggle. The desk capsule wins only when it is hot enough to be close-talk; the room microphone (or an
echo-cancel source) stays the busy-room ear. The beam's direction of arrival is independent (doa_loop).

Measured on the house's own pair 2026-08-27 05:41 (quiet room):
  desk broadcast microphone settle  -48.8 dBFS (wake pop -36)
  room array raw                    -49 to -51 dBFS
Close-talk on a broadcast mic is well above -28. Thresholds are those floors plus margin, not speech samples.

RAIL_STT_MIC_CLOSE names the desk microphone (a source name or any part of it); unset or "" = no desk
microphone (the house named its own here; a pack ships no device name).
"""
from __future__ import annotations

import os
from typing import Any

from . import audio as audio_mod

# Below this, Shure is room bleed / self-noise, not someone in the capsule.
NEAR_MIN_DB = float(os.environ.get("RAIL_STT_NEAR_MIN_DB", "-28"))
# Shure must also beat the room mic. Desk speech on the puck must not flip.
NEAR_MARGIN_DB = float(os.environ.get("RAIL_STT_NEAR_MARGIN_DB", "8"))

ROOM = "room"
NEAR = "near"


def enabled() -> bool:
    raw = os.environ.get("RAIL_STT_MIC_CLOSE", "").strip().lower()
    return raw not in ("", "0", "off", "false", "no", "none", "auto")


def close_device() -> str | None:
    """The desk capsule's source. None when none is named, or it is not plugged in."""
    if not enabled():
        return None
    needle = os.environ.get("RAIL_STT_MIC_CLOSE", "").strip()
    sources = audio_mod.list_pulse_sources()
    if needle in sources:
        return needle
    for s in sources:
        if s.endswith(".monitor"):
            continue
        if needle.lower() in s.lower() and s.startswith("alsa_input."):
            return s
    return None


def rms_db(wav: Any) -> float:
    import numpy as np

    if wav is None:
        return -120.0
    arr = np.asarray(wav)
    if arr.size == 0:
        return -120.0
    return float(audio_mod.rms_db(arr))


def decide(
    *,
    near_db: float,
    room_db: float,
    held: str | None = None,
    near_min_db: float | None = None,
    near_margin_db: float | None = None,
) -> dict[str, Any]:
    """Pick ASR PCM. ``held`` sticks for one utterance so we never splice.

    Liveness only — not VAD, not embeddings.
    """
    lo = NEAR_MIN_DB if near_min_db is None else float(near_min_db)
    margin = NEAR_MARGIN_DB if near_margin_db is None else float(near_margin_db)
    near_hot = float(near_db) >= lo and float(near_db) >= float(room_db) + margin
    if held in (NEAR, ROOM):
        # One utterance, one capsule. Hangover clears held.
        pick = held
        why = "held"
    elif near_hot:
        pick = NEAR
        why = "near_field"
    else:
        pick = ROOM
        why = "room"
    return {
        "mic": pick,
        "why": why,
        "near_db": round(float(near_db), 1),
        "room_db": round(float(room_db), 1),
        "near_hot": bool(near_hot),
        "near_min_db": lo,
        "near_margin_db": margin,
    }
