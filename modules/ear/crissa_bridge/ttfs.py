"""First-clause split for 1–2 s time-to-first-speech.

Cosy /stream needs a finished text blob. The LLM can still stream: speak the
first clause as soon as it is a sentence, then the rest when the stream ends.
"""
from __future__ import annotations

import json
import os
import re

_SENT = re.compile(r"^(.*?[.!?])(\s+|$)")
# A NUMBER STILL ARRIVING IS NOT A SENTENCE END (round 17, .101 2026-10-13 05:57: "What model are you?" -> "grok-4.",
# then `rest_empty`): the stream's buffer can stop right after "grok-4." with "20" still to come, and the clause test
# took the buffer's end for the end of a sentence. A clause that ends in "<digit>." at the very end of the buffer waits
# for the next piece (a dot between digits never ends a clause); the finished reply is unaffected.
# Kill: EAR_BRIDGE_CLAUSE_NUMBER=0.
_EDGE_NUMBER = re.compile(r"\d\.$")


def _clause_number_on() -> bool:
    return os.environ.get("EAR_BRIDGE_CLAUSE_NUMBER", "1").strip().lower() not in ("0", "false", "no", "off")
# 2026-09-08: TTFS spoke "The user is asking me to check the can board…"
# before any tool. Planning is not a spoken clause.
_PLAN = re.compile(
    r"^(the user is asking|the user wants|the user requested|"
    r"i need to|let me |i'll |i will |this is (a bit )?ambiguous|"
    r"okay,?\s+i('ll| will))",
    re.I,
)


def is_planning_clause(text: str) -> bool:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return bool(t and _PLAN.match(t))


# A SENTENCE KEEPS ITS LAST WORDS (round 28, .101 2026-10-17 05:32: "Understood, what are you in the mood for tonight or
# what ingredients do" was said, and "you have on hand?" 6 s later on its own). A first sentence with no end by 72
# characters was cut at the last space there. It now waits up to CLAUSE_CUT_CHARS (120) for the sentence's own end -
# at her stream's speed well under a second more - and a cut, when one is still needed, prefers the last comma,
# semicolon or dash past 24 characters to a bare space. Kill: EAR_BRIDGE_CLAUSE_CUT_CHARS=72 (round 27's cut).
def clause_cut_chars() -> int:
    try:
        return max(40, int(os.environ.get("EAR_BRIDGE_CLAUSE_CUT_CHARS", "120")))
    except ValueError:
        return 120


_SOFT_STOP = re.compile(r"[,;:\u2014\u2013]\s")


def first_speakable_clause(buf: str) -> str | None:
    """Return the first spoken clause, or None if the buffer is still a stub."""
    t = re.sub(r"<think>[\s\S]*?</think>", " ", buf or "", flags=re.I)
    t = re.sub(r"</?think>", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if is_planning_clause(t):
        return None
    m = _SENT.match(t)
    if m:
        s = m.group(1).strip()
        growing = not m.group(2) and _clause_number_on() and bool(_EDGE_NUMBER.search(s))   # "grok-4." + "20"
        if len(s) >= 3 and not is_planning_clause(s) and not growing:
            return s
    lim = clause_cut_chars()
    if len(t) >= lim:
        soft = [m.end() for m in _SOFT_STOP.finditer(t, 24, lim)] if lim > 72 else []
        cut = soft[-1] - 1 if soft else t.rfind(" ", 24, lim)
        if cut >= 24:
            s = t[:cut].strip()
            if not is_planning_clause(s):
                return s
    return None


def spoken_from_reply(full: str) -> str:
    """Drop leading third-person planning. Empty if that was the whole turn."""
    t = re.sub(r"\s+", " ", (full or "").strip())
    while t:
        if is_planning_clause(t):
            m = _SENT.match(t)
            if not m:
                return ""
            t = t[m.end() :].strip()
            continue
        m = _SENT.match(t)
        if m and is_planning_clause(m.group(1)):
            t = t[m.end() :].strip()
            continue
        break
    return t


def rest_after_clause(full: str, clause: str | None) -> str:
    """Remainder after a first clause. Empty if we would double-speak.

    PUNCTUATION-BLIND (the User 2026-09-28, "Crissa speaks full replies twice"):
    the streamed first clause and the finished reply are two renderings of one
    text, and they disagree on punctuation — a dash in the clause where the
    final reply has a comma, a colon for a dash. An exact prefix test failed on
    that and the caller then spoke the WHOLE reply again. So the clause is
    matched on its letters and digits alone (case-folded; dash, colon, comma,
    quotes and spacing all ignored) and the reply is cut right after the last
    matched character, skipping the punctuation and space that follow it.
    A clause that does not match — or that would end mid-word in the reply —
    gives "", never the reply: silence beats saying it twice.
    """
    f = re.sub(r"\s+", " ", (full or "").strip())
    c = re.sub(r"\s+", " ", (clause or "").strip())
    if not f:
        return ""
    if not c:
        return f
    want = [ch for ch in c.casefold() if ch.isalnum()]
    if not want:
        return ""
    k = 0
    cut = -1
    for i, ch in enumerate(f):
        if not ch.isalnum():
            continue
        if ch.casefold() != want[k]:
            return ""
        k += 1
        if k == len(want):
            cut = i + 1
            break
    if cut < 0:
        return ""                       # the reply is shorter than the clause
    if cut < len(f) and f[cut].isalnum():
        return ""                       # the clause ends inside a word of the reply
    rest = f[cut:]
    while rest and not rest[0].isalnum() and rest[0] not in "\"'(“‘[":
        rest = rest[1:]
    return rest.strip()


def sse_delta_text(line: str) -> str | None:
    """Parse one SSE line. None = skip. '' = [DONE]."""
    s = (line or "").strip()
    if not s or s.startswith(":"):
        return None
    if not s.startswith("data:"):
        return None
    data = s[5:].strip()
    if data == "[DONE]":
        return ""
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    ch = (obj.get("choices") or [{}])[0]
    delta = ch.get("delta") or {}
    piece = delta.get("content")
    if isinstance(piece, str) and piece:
        return piece
    msg = ch.get("message") or {}
    piece = msg.get("content")
    if isinstance(piece, str) and piece:
        return piece
    return None
