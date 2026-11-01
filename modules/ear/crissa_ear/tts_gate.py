"""House TTS gate — skip logging Crissa's mouth as STT turns.

the ear bridge writes <crissa home>/tts-gate.json while the voice is speaking
(and briefly after). Continuous ear still captures room audio; we drop
near-echo of the last spoken reply so omni/log stay clean.
"""
from __future__ import annotations

import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .home import HOME

GATE_PATH = Path(
    os.environ.get(
        "HOUSE_TTS_GATE",
        str(HOME / "tts-gate.json"),
    )
).expanduser()

ECHO_RATIO = float(os.environ.get("RAIL_TTS_ECHO_RATIO", "0.55"))
# How long after TTS finishes to still suppress all ear audio.
# 8.0s covers STT processing latency + room reverb tail for long TTS turns.
DEAF_COOLDOWN = float(os.environ.get("RAIL_TTS_DEAF_COOLDOWN", "8.0"))
# speaking=true older than this with no air_until ahead is a crashed run, not a mouth
STUCK_GATE_S = float(os.environ.get("RAIL_TTS_STUCK_GATE_SEC", "180"))


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def read_gate() -> dict[str, Any]:
    try:
        if not GATE_PATH.is_file():
            return {}
        data = json.loads(GATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_echo_of(text: str, last_reply: str, *, ratio: float = ECHO_RATIO) -> bool:
    a, b = _norm(text), _norm(last_reply)
    if not a or not b:
        return False
    if len(a) < 8 and a in b:
        return True
    if a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    if ta and len(ta) >= 3:
        overlap = len(ta & tb) / max(1, len(ta))
        if overlap >= 0.7:
            return True
    return SequenceMatcher(None, a, b).ratio() >= ratio


def mouth_open(*, grace_s: float = 0.3, now: float | None = None) -> bool:
    """True while Cosy is speaking, plus a short tail after the gate drops.

    Used to keep the sound lobe dark (and skip sound.heard) — not to mute STT.
    """
    g = read_gate()
    if not g:
        return False
    t = time.time() if now is None else float(now)
    if g.get("speaking"):
        # A STUCK GATE IS NOT A MOUTH. 2026-09-06 21:14 a speak run died with
        # speaking=true, air_firm=true and no air_until; every DoA tick then
        # skipped as mouth_open for 12 hours and the oracle lobe went dark
        # (the User 2026-09-07: "Nothing now"). No Cosy turn runs this long:
        # past STUCK_GATE_S with no air_until in the future, the gate is stale.
        try:
            updated = float(g.get("updated_ts") or 0.0)
            until = float(g.get("air_until") or 0.0)
        except (TypeError, ValueError):
            updated, until = 0.0, 0.0
        if until and until > t:
            return True
        if updated and (t - updated) > STUCK_GATE_S:
            return False
        return True
    try:
        updated = float(g.get("updated_ts") or 0.0)
    except (TypeError, ValueError):
        updated = 0.0
    return bool(updated and (t - updated) <= float(grace_s))


def should_drop_stt_text(text: str) -> bool:
    """Deprecated no-op. Mouth filter is stamp tts_echo_veto (embeddings).

    Hard mute / post-speak timers ate real follow-ups (the User 2026-08-09 and
    2026-08-13). rail-stt listen path no longer calls this. Kept so leftover
    callers fail open.
    """
    return False
