"""The agent this install talks to: the Connect endpoint (2026-10-09).

The house's door watchman asked a named agent profile on its own port, with a key read from that runtime's files.
A pack has one agent: the one the person connected on the Connect page (lib/crissa_agent, `<state home>/agent.json`:
url, key or key_ref, model). The describers here read that file and nothing else; no agent configured = no describer.
"""
from __future__ import annotations

import json
import os
import re

from . import paths as _paths


def load() -> dict:
    try:
        d = json.loads((_paths.HOME / "agent.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def chat_url(cfg: dict | None = None) -> str:
    cfg = load() if cfg is None else cfg
    url = str(cfg.get("url") or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    if not re.search(r"/v\d+$", url):
        url += "/v1"
    return url + "/chat/completions"


def key(cfg: dict | None = None) -> str:
    cfg = load() if cfg is None else cfg
    k = str(cfg.get("key") or "").strip()
    if k:
        return k
    ref = cfg.get("key_ref")
    name = ref.get("env") if isinstance(ref, dict) else (ref if isinstance(ref, str) else "")
    return os.environ.get(str(name or ""), "").strip() if name else ""


def model(cfg: dict | None = None, default: str = "") -> str:
    cfg = load() if cfg is None else cfg
    return str(cfg.get("model") or default or "").strip()
