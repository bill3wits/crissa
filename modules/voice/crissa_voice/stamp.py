"""Stamp a turn with the presence id. No presence address: the turn keeps its old shape.

The ear does not invent a second headcount. It reads the presence feed after the words are in.
"""
from __future__ import annotations

import json
import os
import urllib.request


def stamp(env=None, timeout: float = 0.4) -> dict | None:
    """None when no presence address is configured.

    Owner: speaker is the presence id, why presence.
    Not the owner: speaker is empty, why guest.
    Configured and not answering: speaker is empty, why unreachable.
    """
    env = os.environ if env is None else env
    root = str(env.get("CRISSA_PRESENCE_URL") or "").strip().rstrip("/")
    if not root:
        return None
    try:
        with urllib.request.urlopen(root + "/v1/presence", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
    except (OSError, ValueError, TimeoutError):
        return {"speaker": "", "why": "unreachable"}
    if not isinstance(data, dict):
        return {"speaker": "", "why": "unreachable"}
    if data.get("user_auth") is True:
        return {"speaker": str(data.get("user_id") or ""), "why": "presence"}
    return {"speaker": "", "why": "guest"}
