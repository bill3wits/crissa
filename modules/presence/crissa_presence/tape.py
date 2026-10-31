"""Read the continuous security tape — the house's time machine.

the User 2026-08-09: "capture full rate videos all the time to cache it for an
hour ... This gives us time travel to grab initial action."

The recording was never skipped. `<records>/raw` holds ~15fps of
every minute, indexed, going back days — 86 GB and 1.27M frames as of today.
What was missing is any reader: door packs are assembled from the 20-second
RAM ring in grabber.py, which is why `clamp_onset` has to cap the walk-back at
MAX_PREROLL_SEC=6 and why a crossing that the classifier minted late is simply
not in the pack. Ghost recovery fires 1800s after the fact against that same
20s ring, which is how 160 leave packs became empty doorways.

With the tape, the start of an event never has to be predicted. Motion ends,
we stop, and *then* we reach backwards for however much lead-in we want. The
only limit is retention.

Layout on disk, one directory per minute:

    <records>/raw/YYYYMMDD/HHMM/
        index.jsonl                       {"seq","ts","file","n"} per frame
        f_<ms>_<seq>.jpg

The index is written in capture order, so a window read is a linear scan of a
handful of small files — no decode, no directory walk per frame.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

TAPE_ROOT = Path(os.environ.get("RAIL_TAPE_ROOT", str(_paths.RECORDS / "raw")))


def _minute_dirs(t0: float, t1: float) -> Iterator[Path]:
    """Every minute directory touching [t0, t1], inclusive, in order."""
    if t1 < t0:
        t0, t1 = t1, t0
    m = int(t0 // 60) * 60
    while m <= t1:
        lt = time.localtime(m)
        yield TAPE_ROOT / time.strftime("%Y%m%d", lt) / time.strftime("%H%M", lt)
        m += 60


def frames_between(t0: float, t1: float, *, stride: int = 1) -> list[dict[str, Any]]:
    """Tape frames with t0 <= ts <= t1, oldest first.

    Each row is {seq, ts, path}. `stride` samples every Nth frame — the tape
    runs ~15fps, so stride=1 is everything and the caller does its own
    dense/thin selection exactly as it would from the ring.
    """
    out: list[dict[str, Any]] = []
    for d in _minute_dirs(t0, t1):
        idx = d / "index.jsonl"
        if not idx.is_file():
            continue
        try:
            raw = idx.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = float(r["ts"])
            except (ValueError, KeyError):
                continue
            if ts < t0 or ts > t1:
                continue
            out.append({"seq": int(r.get("seq") or 0), "ts": ts,
                        "path": str(d / str(r.get("file")))})
    out.sort(key=lambda r: r["ts"])
    return out[::stride] if stride > 1 else out


def frames_around(
    ts: float, *, before_sec: float = 10.0, after_sec: float = 10.0, **kw: Any
) -> list[dict[str, Any]]:
    """Walk back from a trigger without the ring's 6-second ceiling."""
    return frames_between(ts - float(before_sec), ts + float(after_sec), **kw)


def coverage(t0: float, t1: float) -> dict[str, Any]:
    """What the tape can actually offer for a window — check before trusting
    a recovery. A gap means the recorder was down, not that nothing moved."""
    rows = frames_between(t0, t1)
    if not rows:
        return {"ok": False, "frames": 0, "reason": "no_tape_for_window"}
    span = rows[-1]["ts"] - rows[0]["ts"]
    gaps = [
        round(b["ts"] - a["ts"], 2)
        for a, b in zip(rows, rows[1:])
        if (b["ts"] - a["ts"]) > 1.0
    ]
    return {
        "ok": True,
        "frames": len(rows),
        "span_sec": round(span, 2),
        "fps": round(len(rows) / span, 2) if span > 0 else None,
        "first_ts": rows[0]["ts"],
        "last_ts": rows[-1]["ts"],
        "gaps_over_1s": gaps[:10],
        "n_gaps": len(gaps),
    }


def read(path: str | Path) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None
