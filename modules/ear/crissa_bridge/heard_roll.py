"""What the ear heard that was not sent to her, on the glass's log (round 15 (d), the User 2026-10-12, .101: "hard for it
to print while she is speaking ... would not print what I said no matter how hard I tried until the final one").

The glass's log shows turns the ear wrote; a turn the bridge then held back (taken for her own voice, said while she was
not listening, queued behind her line) left no trace there. Each one is written here and the feed
(scripts/voiceonly-home-server.py, recent_heard_roll) shows it in the roll, after the words, marked "not sent" with why.
The session's end is written here too (e): "Listening off. Say Crissa to talk again."

    <home>/heard-roll.jsonl: {"ts", "who", "text", "why", "kind"}  (kind: heard | status | cancel)

SIDE TALK (round 18, the User 2026-10-13: "Id like to talk with someone else while she speaks without interrupting
her"): words said over her answer with no interrupt word are written here with `"lane": "room"`, and the feed shows them
as room speech (log_room, plain words), never as a line sent to her.

Kill: EAR_BRIDGE_HEARD_ROLL=0.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .home import HOME

KEEP = 200
_LOCK = threading.Lock()
# ONE LINE, ONE ROW (round 17, .101 2026-10-13 05:54: one "Keep reading" said over her line was written 6 times, ~0.6 s
# apart, while the bridge re-asked it). The same row (who, words, why, kind) is written once in ONCE_S, whoever calls.
# Kill: EAR_BRIDGE_HEARD_ROLL_ONCE=0.
ONCE_S = 30.0
_LAST: dict = {}


def once_on() -> bool:
    return os.environ.get("EAR_BRIDGE_HEARD_ROLL_ONCE", "1").strip().lower() not in ("0", "false", "no", "off")


def on() -> bool:
    return os.environ.get("EAR_BRIDGE_HEARD_ROLL", "1").strip().lower() not in ("0", "false", "no", "off")


def path() -> Path:
    return HOME / "heard-roll.jsonl"


def note(who: str, text: str, why: str, kind: str = "heard", now: float | None = None, lane: str = "") -> None:
    if not on() or not (text or "").strip():
        return
    row = {"ts": round(float(now or time.time()), 3), "who": (who or "").strip().lower()[:40] or "?",
           "text": (text or "").strip()[:220], "why": (why or "").strip()[:80], "kind": kind}
    if lane:
        row["lane"] = str(lane)[:16]          # round 18: "room" = side talk, shown as room speech
    with _LOCK:
        if once_on():
            key = (row["who"], " ".join(row["text"].lower().split()), row["why"], kind)
            if 0.0 <= row["ts"] - _LAST.get(key, -1e18) < ONCE_S:
                return
            _LAST[key] = row["ts"]
            for k in [k for k, t in _LAST.items() if row["ts"] - t >= ONCE_S]:
                _LAST.pop(k, None)
        try:
            p = path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if p.stat().st_size > 256 * 1024:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-KEEP:]
                tmp = p.with_suffix(".jsonl.tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(tmp, p)
        except OSError:
            pass
