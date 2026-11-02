"""thinking: tell the wall she is working on his turn (K, the User 2026-10-01).

He could not see that she was on it until her log line appeared - after the model, the tools, Cosy's first
hop and the wall's 2 s poll. With `EAR_BRIDGE_THINKING=1` the bridge writes one small file the live wall
reads at 5 Hz (`HOUSE_THINKING_FILE`, default <crissa home>/crissa-thinking.json):

    {"on": true,  "since": <when his turn was sent>, "seq": n}
    {"on": false, "since": ..., "end": <turn over>, "seq": n}

`on` from the moment dispatch() takes his turn; re-armed with a new `since` when her first clause has been
said and the reply is still coming (a tool run); off when the turn ends (spoken, cancelled, silent). The wall
(HOUSE_WALL_THINKING_PULSE=1) pulses Crissa's dot while `on` and none of her lines has entered its log since
`since` - so when the pulse stops, her line is already on screen. Default OFF: no file is written.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .home import HOME

_lock = threading.Lock()
_seq = 0


def armed() -> bool:
    return os.environ.get("EAR_BRIDGE_THINKING", "0").strip().lower() in ("1", "true", "yes", "on")


def path() -> Path:
    return Path(os.environ.get("HOUSE_THINKING_FILE") or (HOME / "crissa-thinking.json"))


def _write(rec: dict) -> None:
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, p)  # the wall never reads half a file
    except OSError:
        pass


_last: dict = {}


def start(now: float | None = None) -> dict | None:
    """His turn was sent (or she is waiting on the model again): the pulse runs from `now`."""
    global _seq, _last
    if not armed():
        return None
    with _lock:
        _seq += 1
        _last = {"on": True, "since": time.time() if now is None else now, "seq": _seq}
        _write(_last)
        return dict(_last)


def stop(why: str = "done", now: float | None = None) -> dict | None:
    global _last
    if not armed():
        return None
    with _lock:
        if not _last.get("on"):
            return None
        _last = dict(_last, on=False, end=time.time() if now is None else now, why=why)
        _write(_last)
        return dict(_last)
