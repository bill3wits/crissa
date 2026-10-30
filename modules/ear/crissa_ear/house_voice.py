"""House voice master lock — User-gated free-air arm.

Missing file = OFF. Only User/panel should write ON.
ear-bridge and any free-air Crissa path must call is_voice_on().
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .home import HOME

VOICE_PATH = Path(
    os.environ.get(
        "HOUSE_VOICE_LOCK",
        str(HOME / "house-voice.json"),
    )
).expanduser()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_state() -> dict[str, Any]:
    return {
        "schema": "house_voice_lock/v1",
        "voice": "off",
        "on_since": None,
        "on_by": None,
        "updated_at": None,
        "note": "User master arm — free-air Crissa requires voice=on",
    }


def read_state() -> dict[str, Any]:
    if not VOICE_PATH.is_file():
        return default_state()
    try:
        data = json.loads(VOICE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_state()
        base = default_state()
        base.update(data)
        v = str(base.get("voice") or "off").strip().lower()
        base["voice"] = "on" if v in ("on", "1", "true", "yes") else "off"
        return base
    except Exception:
        return default_state()


def is_voice_on() -> bool:
    return read_state().get("voice") == "on"


def write_state(voice: str, *, on_by: str = "user") -> dict[str, Any]:
    v = str(voice or "off").strip().lower()
    on = v in ("on", "1", "true", "yes")
    prev = read_state()
    state = default_state()
    state["voice"] = "on" if on else "off"
    state["updated_at"] = _now()
    if on:
        state["on_since"] = prev.get("on_since") or _now()
        state["on_by"] = on_by or "user"
    else:
        state["on_since"] = None
        state["on_by"] = None
        state["off_by"] = on_by or "user"   # round 23: who turned it off (the player's start keeps a person's off)
    VOICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state
