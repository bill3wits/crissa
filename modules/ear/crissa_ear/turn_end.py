"""turn_end: the end of his turn, sooner (round 17, 2026-10-13).

The User 2026-10-13: "yes cut the waits". Measured on one core with the kit's own engines (checkpoint
round17-id-off-20261013): end of his speech -> her first sound p50 5.06 s (ID-only), and 2.0 s of it was THIS
hangover - the silence that closed his utterance in a conversation (RAIL_STT_HANGOVER_SEC, the ear's settings table:
2.0, "thinking pauses stay in one utterance"). The other 2.0 s is the bridge's quiet before her mouth opens
(crissa_bridge/speculate.py).

In a pack with speculation on (config/crissa-talk.json `turn.speculate`, kill CRISSA_EAR_SPECULATE=0):
  - the conversation's hangover is `turn.end_s` (0.6 s: two of the ear's 0.3 s frames) instead of 2.0. The chunk
    closes and is decoded that much sooner. A pause longer than that inside his sentence splits it in two; the bridge
    puts it back together (her turn is started on the first part, cancelled silently when he goes on, and started
    again on the whole utterance - checkpoint round17-waits-20261013 measures how often, per setting);
  - the ear says when it last heard speech: `speech_at` on /v1/ear/status (and /health's ear block), the end of the
    last 0.3 s frame the VAD called speech - a lone frame that opens no utterance counts, so he is seen again within
    one frame - and never while one of her lines is on the air (her own voice coming back is not his). The bridge's
    rule for her mouth reads it: the turn is final only after `turn.final_s` without it.
The wake hangover (paused, RAIL_STT_WAKE_HANGOVER_SEC) and the on-air hangover (ON_AIR_HANGOVER_S) are untouched: this is
only the engaged (conversation) one. The house never (is_house: its ear is rail-stt).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .home import HOME

PACK_ROOT = Path(__file__).resolve().parents[3]      # modules/ear/crissa_ear -> the pack (or the source tree)
# what config/crissa-talk.json `turn` says when nothing can be read (lib/crissa_talk.FALLBACK holds the same)
DEFAULTS = {"speculate": True, "end_s": 0.6, "final_s": 2.0, "gap_s": 0.3}
_CACHE: dict = {"key": None, "v": None}
_LAST = {"speech_at": 0.0}
_LOCK = threading.Lock()


def _zh():
    try:
        from . import crissa_home as H              # the copy a pack puts beside this package
        return H
    except ImportError:
        import crissa_home as H                     # the source tree's lib (home.py put it on the path)
        return H


def is_house() -> bool:
    try:
        return bool(_zh().is_house())
    except Exception:  # noqa: BLE001 - no state-home module: not the house
        return False


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _talk_turn() -> dict:
    """config/crissa-talk.json `turn` under <state home>/talk.json, read the way lib/crissa_talk layers them (that
    module when it is importable, else the two files)."""
    try:
        import sys
        lib = str(PACK_ROOT / "lib")
        if lib not in sys.path:
            sys.path.append(lib)
        import crissa_talk                           # noqa: E402
        t = crissa_talk.load(home=HOME, env={}).get("turn")   # the environment: settings(), per call
        if isinstance(t, dict):
            return dict(t)
    except Exception:  # noqa: BLE001 - the files themselves below
        pass
    out = dict(DEFAULTS)
    for p in (PACK_ROOT / "config" / "crissa-talk.json", HOME / "talk.json"):
        sec = _read_json(p).get("turn")
        if isinstance(sec, dict):
            out.update({k: v for k, v in sec.items() if k in DEFAULTS and v is not None})
    return out


def _file_key() -> tuple:
    out = []
    for p in (PACK_ROOT / "config" / "crissa-talk.json", HOME / "talk.json"):
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)


def settings(now: float | None = None) -> dict:
    """{speculate, end_s, final_s, gap_s, house}: the talk settings' `turn` (re-read when config/crissa-talk.json or
    <state home>/talk.json changes), the environment on top (CRISSA_EAR_SPECULATE, CRISSA_EAR_END_S,
    CRISSA_TURN_FINAL_S) and whether this is the house, read on every call."""
    key = _file_key()
    with _LOCK:
        cached = _CACHE["v"] if _CACHE.get("key") == key else None
    if cached is None:
        cached = dict(DEFAULTS)
        cached.update({k: x for k, x in _talk_turn().items() if k in DEFAULTS})
        with _LOCK:
            _CACHE.update(key=key, v=dict(cached))
    v = dict(cached)
    v["house"] = is_house()
    raw = os.environ.get("CRISSA_EAR_SPECULATE")
    if raw is not None and raw.strip():
        v["speculate"] = raw.strip().lower() not in ("0", "false", "no", "off")
    for key, var in (("end_s", "CRISSA_EAR_END_S"), ("final_s", "CRISSA_TURN_FINAL_S")):
        raw = os.environ.get(var)
        if raw is not None and raw.strip():
            try:
                v[key] = float(raw)
            except ValueError:
                pass
    return v


def reset() -> None:
    """Read the talk files again at the next call."""
    with _LOCK:
        _CACHE.update(key=None, v=None)


def on() -> bool:
    """Speculation on here: a pack, and turn.speculate (CRISSA_EAR_SPECULATE=0 turns it off)."""
    try:
        v = settings()
        return bool(v.get("speculate")) and not v.get("house")
    except Exception:  # noqa: BLE001
        return False


def engaged_hangover(hang: float) -> float:
    """The silence that closes his utterance in a conversation: turn.end_s when speculation is on (clamped to the ear's
    own 0.3-4 s), else `hang` (RAIL_STT_HANGOVER_SEC, 2.0 in a pack)."""
    if not on():
        return hang
    try:
        return max(0.3, min(4.0, float(settings().get("end_s") or hang)))
    except (TypeError, ValueError):
        return hang


def note_frame(has_speech: bool, now: float | None = None) -> None:
    """One 0.3 s frame through the VAD: a speech frame stamps `speech_at` (not while her line is on the air)."""
    if not has_speech:
        return
    now = time.time() if now is None else float(now)
    try:
        from .ear_loop import her_air_now
        if her_air_now(now):
            return
    except Exception:  # noqa: BLE001 - no gate: count it (a held mouth is the safe side)
        pass
    _LAST["speech_at"] = now


def state() -> dict:
    """For the ear's status: {"speech_at", "speculate"} in a pack with speculation on, else {}."""
    if not on():
        return {}
    return {"speech_at": round(float(_LAST["speech_at"]), 3), "speculate": True}
