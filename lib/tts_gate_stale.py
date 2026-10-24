"""A speaking flag nobody is speaking under (the User 2026-10-03).

the house-tts-gate.json record said speaking=true from 06:14:34 with an empty reply, no air_until and no house-speak
process - a line that died without clearing it. The glass read it as Crissa talking for 40 minutes: the sound ring
hid his beam (the angle is withheld while she talks) and the speaker row froze. A gate is STALE when it says speaking,
has no air_until, is older than the ear's stuck-gate age (180 s), and no Crissa stream is playing; the glass then treats it as not speaking.
The file itself is the writer's - nothing here rewrites it.

Kill: VO_TTS_GATE_STALE_S=0 (always trust the file, the old behaviour).
"""
from __future__ import annotations

import os
import time


def stale_after_s(env: dict | None = None) -> float:
    """The ear's own rule (rail-stt tts_gate.STUCK_GATE_S, since 09-07: 'no Cosy turn runs this long'), so the house
    has ONE definition of a stuck gate: RAIL_TTS_STUCK_GATE_SEC, default 180. VO_TTS_GATE_STALE_S overrides; 0 = off."""
    e = os.environ if env is None else env
    raw = e.get("VO_TTS_GATE_STALE_S") or e.get("RAIL_TTS_STUCK_GATE_SEC") or "180"
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 180.0


def is_stale(gate: dict, *, sink_live: bool, now: float | None = None, env: dict | None = None) -> bool:
    limit = stale_after_s(env)
    if limit <= 0 or not isinstance(gate, dict) or not gate.get("speaking"):
        return False
    if gate.get("air_until") is not None or sink_live:
        return False
    try:
        ts = float(gate.get("updated_ts") or 0.0)
    except (TypeError, ValueError):
        return False
    return ts > 0 and ((now if now is not None else time.time()) - ts) > limit
