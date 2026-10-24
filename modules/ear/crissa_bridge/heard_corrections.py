"""Phrases the ear mishears, corrected before Crissa reads the turn (the User 2026-10-03).

Measured 10-03: "Put the render queue on the wall" came back "Put the render cue on the wall" on both mics. A bare
"cue" is a real house word (the conductor's cues), so only the PHRASE is corrected. Config:
modules/ear/heard-corrections.json (re-read when its mtime changes - no restart for a new row once the code is loaded).
Kill: EAR_BRIDGE_CORRECTIONS=0.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "heard-corrections.json"      # modules/ear/heard-corrections.json
_cache: dict = {"path": None, "mtime": None, "rules": []}


def _path() -> Path:
    return Path(os.environ.get("EAR_BRIDGE_CORRECTIONS_FILE") or DEFAULT)


def _rules() -> list:
    p = _path()
    try:
        m = p.stat().st_mtime
    except OSError:
        return []
    if _cache["path"] == str(p) and _cache["mtime"] == m:
        return _cache["rules"]
    try:
        phrases = json.loads(p.read_text(encoding="utf-8")).get("phrases") or {}
    except (OSError, ValueError):
        phrases = {}
    rules = [(re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)", re.I), v) for k, v in phrases.items() if k and v]
    _cache.update(path=str(p), mtime=m, rules=rules)
    return rules


def apply(text: str) -> str:
    if not text or os.environ.get("EAR_BRIDGE_CORRECTIONS", "1").strip().lower() in ("0", "false", "no", "off"):
        return text
    for rx, rep in _rules():
        text = rx.sub(rep, text)
    return text
