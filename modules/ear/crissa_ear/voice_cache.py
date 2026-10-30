"""Rolling audio cache — a few minutes of the room, for security grabs.

the User 2026-08-14:

    you can use that path to record the intrusion voice in a cached 3mins or
    however much we need to have available for the operation to grab the clip
    and pic. Then stash in what we create under the Inspector filetree.

The problem this solves: an intrusion attempt is recognised *after* the words
are decoded, by which point the audio is gone. Nothing retained utterance
audio, so `rail-voice`'s incident reporter could only ever file
``voice_clip: null``. This keeps the last few minutes so the clip can be cut
retroactively.

Deliberately a **ring in RAM**, not a disk recorder:

- It is evidence-on-demand, not surveillance. Nothing touches disk until an
  incident asks for a clip.
- A crash loses it, which is the correct trade for a house that does not want a
  rolling transcript of itself on a drive.
- int16 at 16 kHz mono ≈ **5.8 MB for 3 minutes**. Cheap enough to always run.

Fail-open in every direction: a failure here must never cost the house its ear,
so `offer()` swallows everything and the ring simply goes stale.
"""

from __future__ import annotations

import json
import os
import threading
import time
import wave
from collections import deque
from pathlib import Path

from .home import HOME
from typing import Any

import numpy as np

# 3 minutes by default, per the User. Raise only with the memory cost in mind.
CACHE_SEC = float(os.environ.get("RAIL_VOICE_CACHE_SEC", "180"))
ENABLED = os.environ.get("RAIL_VOICE_CACHE", "1") not in ("0", "false", "no")

_lock = threading.Lock()
# (ts_end, sample_rate, int16 samples) — newest last
_ring: deque[tuple[float, int, np.ndarray]] = deque()
_total_sec = 0.0
# Fill only while an exchange is open (VAD speech → release after the turn).
# Idle room must not sit at ~180s "held" on the Control Panel.
_accepting = False


def _exchanges_path() -> Path | None:
    if os.environ.get("RAIL_VOICE_CACHE_LOG", "1").lower() in ("0", "false", "no"):
        return None
    override = os.environ.get("RAIL_VOICE_CACHE_LOG_PATH")
    if override:
        return Path(override)
    return HOME / "logs" / "exchanges.jsonl"


def _to_int16(wav: np.ndarray) -> np.ndarray:
    """float32 [-1,1] or int16 in → int16 out. Clipped, never wrapped."""
    a = np.asarray(wav)
    if a.dtype == np.int16:
        return a
    a = np.nan_to_num(a.astype(np.float32, copy=False), nan=0.0)
    if np.issubdtype(a.dtype, np.floating):
        peak = float(np.max(np.abs(a))) if a.size else 0.0
        # Some capture paths already hand back int-scaled floats.
        if peak > 1.5:
            a = a / 32768.0
        a = np.clip(a, -1.0, 1.0) * 32767.0
    return a.astype(np.int16, copy=False)


def arm() -> None:
    """Open the ring for this exchange. Called when VAD confirms speech."""
    if not ENABLED:
        return
    global _accepting
    with _lock:
        _accepting = True


def offer(wav: Any, sample_rate: int, *, ts_end: float | None = None) -> None:
    """Add a captured frame. Never raises — the ear outranks the cache."""
    if not ENABLED:
        return
    try:
        a = _to_int16(wav)
        if a.ndim > 1:
            a = a.reshape(-1)
        if a.size == 0:
            return
        sr = int(sample_rate or 16000)
        dur = a.size / float(max(1, sr))
        end = float(ts_end if ts_end is not None else time.time())
        global _total_sec
        with _lock:
            if not _accepting:
                return
            _ring.append((end, sr, a))
            _total_sec += dur
            while _ring and _total_sec > CACHE_SEC:
                _old_end, old_sr, old = _ring.popleft()
                _total_sec -= old.size / float(max(1, old_sr))
    except Exception:
        return


def status() -> dict[str, Any]:
    with _lock:
        n = len(_ring)
        secs = _total_sec
        newest = _ring[-1][0] if _ring else None
        sr = _ring[-1][1] if _ring else None
        open_ex = _accepting
    return {
        "enabled": ENABLED,
        "open": open_ex,
        "frames": n,
        "seconds": round(secs, 2),
        "capacity_sec": CACHE_SEC,
        "newest_ts": newest,
        "sample_rate": sr,
        "age_sec": round(time.time() - newest, 2) if newest else None,
    }


def clear() -> dict[str, Any]:
    """Drop PCM. Does not write a log line (use release() after an exchange)."""
    global _total_sec, _accepting
    with _lock:
        held = _total_sec
        n = len(_ring)
        _ring.clear()
        _total_sec = 0.0
        _accepting = False
    return {"ok": True, "cleared_sec": round(held, 2), "cleared_frames": n}


def release(note: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize this exchange to jsonl, then drop the ring.

    Intrusion clips must be cut *before* this (rail-voice files during the
    turn). After Crissa finishes speaking — or a short idle if she does not —
    the house should not sit on ~3 min of room audio.
    """
    st = status()
    out = clear()
    rec = {
        "schema": "voice_exchange/v1",
        "ts": time.time(),
        "audio_sec": out.get("cleared_sec") or 0,
        "frames": out.get("cleared_frames") or 0,
    }
    if isinstance(note, dict):
        for k in ("who", "heard", "reply", "person_id"):
            v = note.get(k)
            if v:
                rec[k] = str(v)[:400]
    path = _exchanges_path()
    if path is not None and (rec.get("audio_sec") or rec.get("heard") or rec.get("reply")):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rec["log"] = str(path)
        except OSError:
            rec["log"] = None
    rec.update(out)
    rec["ok"] = True
    rec["was_open"] = bool(st.get("open") or st.get("seconds"))
    return rec


def write_clip(
    dest: str | Path,
    *,
    seconds: float | None = None,
    t_end: float | None = None,
) -> dict[str, Any]:
    """Cut the tail of the ring to a wav. Returns what was actually written.

    `seconds` defaults to everything held. The result reports the real span, so
    a caller can tell a full 3-minute clip from the 4 seconds that happened to
    be in the ring at boot.
    """
    if not ENABLED:
        return {"ok": False, "error": "cache disabled"}
    end = float(t_end if t_end is not None else time.time())
    want = float(seconds) if seconds else CACHE_SEC
    start = end - want
    with _lock:
        frames = [f for f in _ring if f[0] > start]
    if not frames:
        return {"ok": False, "error": "cache empty"}
    sr = frames[-1][1]
    # Mixed rates would corrupt the timeline; keep only the newest rate.
    parts = [a for (_, s, a) in frames if s == sr]
    if not parts:
        return {"ok": False, "error": "no frames at current rate"}
    data = np.concatenate(parts)
    p = Path(dest)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(data.tobytes())
    except OSError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
    return {
        "ok": True,
        "path": str(p),
        "seconds": round(data.size / float(max(1, sr)), 2),
        "sample_rate": sr,
        "requested_sec": want,
        "bytes": p.stat().st_size if p.is_file() else 0,
    }
