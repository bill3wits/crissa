"""callouts: she says what she is doing when she reaches for a tool (L, the User 2026-10-01).

The house's own tool hook speaks for her direct tools, but in a
voice turn she calls only `hands` (16 of 22 calls on 10-01, the rest skill_view) and the hook SKIPs hands, so
nothing was said through a 63 s tool run. The gateway's stream already carries each call
(an `event:` line naming a tool call, crissa_bridge.connect.tool_event; data {tool, label = the call's preview, status}); with
`EAR_BRIDGE_TOOL_CALLOUTS=1` the bridge maps a running call to one short plain phrase and says it at once,
through her voice, without waiting for the model.

  - Tools: EAR_BRIDGE_CALLOUT_TOOLS (default `hands` - the hook keeps the rest, so nothing is said twice).
  - Once per turn per phrase: hands asked to search three ways is one "searching the files".
  - Never over him: if rail-stt's user_speaking flag (I) is up the callout is dropped, not queued (until that
    flag lands the check reads False); a name interrupt cuts it like any line. Never stale: if her mouth is still busy after
    EAR_BRIDGE_CALLOUT_WAIT_S (2.0) it is dropped. Never a repeat of her own clause ("let me check the board"
    then "checking the board").
Default OFF.
"""
from __future__ import annotations

import os
import re

from .own_voice import text_match

_ON = ("1", "true", "yes", "on")

# first match wins: the ask text of a hands call (or a direct tool's preview) -> the phrase
RULES: tuple[tuple[str, str], ...] = (
    (r"\b(file|submit|create|open|add)\b.{0,30}\b(card|ticket|task)\b|\bto (the )?board\b", "filing a card"),
    (r"\b(board|cards?)\b", "checking the board"),
    (r"\b(browser|web ?page|website|url|https?://)\b", "opening the browser"),
    (r"\b(search|look up|google)\b.{0,20}\b(web|online|internet)\b|\bweb search\b", "searching the web"),
    (r"\b(prompt size|tokens?|context)\b", "checking the numbers"),
    (r"\b(memory|remember|hindsight)\b", "checking memory"),
    (r"\b(camera|room|who is (here|there))\b", "looking at the room"),
    (r"\b(read|open)\b.{0,30}\b(files?|report|txt|html|md|contents)\b|\b(files?|txt|html|md)\b.{0,60}\bread\b",
     "reading the file"),
    (r"\b(find|search|look for|list|check)\b.{0,40}\b(files?|folders?|deliverables|home|workspace|notes|desktop)\b",
     "searching the files"),
    (r"\b(show|put|pull up|display)\b.{0,40}\b(wall|glass|screen)\b", "putting it up"),
)
FALLBACK = "on it"


def armed() -> bool:
    return os.environ.get("EAR_BRIDGE_TOOL_CALLOUTS", "0").strip().lower() in _ON


def tools() -> set[str]:
    return {t.strip() for t in os.environ.get("EAR_BRIDGE_CALLOUT_TOOLS", "hands").split(",") if t.strip()}


def grace_s() -> float:
    """Seconds a callout waits for her own first clause before it speaks (0 = at once, the User's ask)."""
    try:
        return max(0.0, float(os.environ.get("EAR_BRIDGE_CALLOUT_GRACE_S", "0")))
    except ValueError:
        return 0.0


def wait_s() -> float:
    try:
        return float(os.environ.get("EAR_BRIDGE_CALLOUT_WAIT_S", "2.0"))
    except ValueError:
        return 2.0


def phrase_for(tool: str, label: str) -> str | None:
    """The phrase for one running tool call, or None when this tool is not ours to call out."""
    if (tool or "") not in tools():
        return None
    low = (label or "").lower()
    for pat, phrase in RULES:
        if re.search(pat, low):
            return phrase
    return FALLBACK


class Turn:
    """One dispatch: which phrases were said, and what she has said herself this turn."""

    def __init__(self) -> None:
        self.said: set[str] = set()
        self.clause = ""

    def take(self, tool: str, label: str, status: str = "running") -> str | None:
        if status != "running":
            return None
        p = phrase_for(tool, label)
        if not p or p in self.said:
            return None
        self.said.add(p)
        if self.repeats_clause(p):
            return None  # her clause already said it
        return p

    def repeats_clause(self, phrase: str) -> bool:
        """Her first clause says the same thing ("let me check the board" / "checking the board")."""
        return bool(self.clause) and text_match(phrase, self.clause) >= 0.75


def all_phrases() -> list[str]:
    return [p for _, p in RULES] + [FALLBACK]
