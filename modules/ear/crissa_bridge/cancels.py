"""Her line, cancelled: where she stopped, and a summary the next turn carries (round 15 (g), the User 2026-10-12: "in
my other setup she gets cancelled in log with summary of recent context").

THE HOUSE: rail-voice's ear_bridge.abort_mouth writes the gate notice "interrupted" (the house page's toast:
"Interrupted · message cancelled") and the next turn's system text says "The previous spoken reply was interrupted and
cancelled. Do not continue it."; the house's agent carries the rest into that turn
(agent/conversation_loop._apply_active_turn_redirect: "[Context from the interrupted assistant response] ... Visible
response before the interruption: ..."). A pack's Connect endpoint keeps no turn of its own, so the bridge keeps it:

    <home>/cancel.json (the latest; the reading code reads it for "go on"), one row per cancel in
    <home>/logs/cancels.jsonl:
    {"ts", "why", "request", "title", "said", "spoken_chars", "line_chars", "last_sentence", "next_sentence",
     "summary", "noted"}
      request        what he asked that she was answering
      title          what she was reading or answering (note_title() sets it; else the request, shortened)
      said           the last SAID_TAIL characters she had said when she stopped (her pieces, the current one cut at
                     the stop by its own clock: CHARS_PER_S)
      spoken_chars   how far into her answer she was (characters)
      last_sentence  the last whole sentence she said
      next_sentence  the sentence she was in, or would have said next
      summary        one line for the glass's log and the next turn

The next turn carries context_note() once (the house agent's shape). Kill: EAR_BRIDGE_CANCEL_RECORD=0.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .home import HOME

CHARS_PER_S = float(os.environ.get("EAR_BRIDGE_SPOKEN_CPS", "15"))   # .101 2026-10-12: 799 characters in ~53 s
SAID_TAIL = 600
_LOCK = threading.Lock()
_REQ: dict[str, Any] = {"text": "", "title": "", "at": 0.0}
_NOTE: dict[str, Any] = {"pending": False}


def on() -> bool:
    return os.environ.get("EAR_BRIDGE_CANCEL_RECORD", "1").strip().lower() not in ("0", "false", "no", "off")


def path() -> Path:
    return HOME / "cancel.json"


def log_path() -> Path:
    return HOME / "logs" / "cancels.jsonl"


def note_request(text: str, now: float | None = None) -> None:
    """The ask she is about to answer (the dispatch calls this before her reply is spoken)."""
    with _LOCK:
        _REQ.update(text=(text or "").strip()[:400], title="", at=float(now or time.time()))


def note_title(title: str) -> None:
    """What she is reading or answering, in a few words (the reading code may name the book and the place)."""
    with _LOCK:
        _REQ["title"] = (title or "").strip()[:120]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text or "") if s.strip()]


def position(rows: list[dict], now: float | None = None, since: float | None = None) -> dict[str, Any]:
    """Where she is in her answer: {"said", "spoken_chars", "line_chars", "last_sentence", "next_sentence", "current"}
    from the say log's rows (start, end, text) since `since` (the ask); the piece on the air is cut by CHARS_PER_S."""
    now = float(now or time.time())
    since = float(_REQ["at"] if since is None else since) - 1.0
    said_parts, line_parts, cur, cut_in_cur = [], [], "", 0
    for r in rows:
        if float(r.get("start") or 0.0) < since:
            continue
        text = str(r.get("text") or "")
        line_parts.append(text)
        end = r.get("end")
        if end is not None and float(end) <= now:
            said_parts.append(text)
            continue
        cut = max(0, min(len(text), int((now - float(r.get("start") or now)) * CHARS_PER_S)))
        said_parts.append(text[:cut])
        cur, cut_in_cur = text, cut
        break
    said = " ".join(p for p in said_parts if p).strip()
    whole = _sentences(said)
    ends_whole = bool(re.search(r"[.!?…]\s*$", said))
    last = whole[-1] if whole and ends_whole else (whole[-2] if len(whole) >= 2 else "")
    nxt = ""
    if cur:
        rest = _sentences(cur[cut_in_cur:]) if ends_whole else _sentences(cur)
        if not ends_whole:
            # the sentence the cut falls in
            acc = 0
            for snt in _sentences(cur):
                acc += len(snt) + 1
                if acc > cut_in_cur:
                    nxt = snt
                    break
        else:
            nxt = rest[0] if rest else ""
    return {"said": said[-SAID_TAIL:], "spoken_chars": len(said), "line_chars": len(" ".join(line_parts)),
            "last_sentence": last[:300], "next_sentence": nxt[:300], "current": cur[:800]}


def record(why: str, rows: list[dict], now: float | None = None) -> dict[str, Any] | None:
    """She was stopped: write the record (cancel.json + logs/cancels.jsonl) and arm the next turn's note."""
    if not on():
        return None
    now = float(now or time.time())
    with _LOCK:
        req = dict(_REQ)
    pos = position(rows, now, since=req["at"] or None)
    if not pos["spoken_chars"] and not req["text"]:
        return None
    title = req["title"] or req["text"][:80]
    where = ('after: "%s"' % pos["last_sentence"]) if pos["last_sentence"] else "before her first sentence ended"
    summary = ("Cancelled %d characters into %s, %s" % (
        pos["spoken_chars"], ('"%s"' % title) if title else "her answer", where))[:400]
    rec = {"ts": round(now, 3), "why": str(why or "")[:60], "request": req["text"], "title": title,
           "said": pos["said"], "spoken_chars": pos["spoken_chars"], "line_chars": pos["line_chars"],
           "last_sentence": pos["last_sentence"], "next_sentence": pos["next_sentence"], "summary": summary,
           "noted": False}
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        lp = log_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    with _LOCK:
        _NOTE.update(pending=True, rec=rec)
    return rec


def latest() -> dict[str, Any]:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def context_note(consume: bool = True) -> str:
    """The next turn's system text after a cancel (once): what she was answering, what she had said, where she stopped
    - the house agent's shape, "[Context from the interrupted assistant response] ... Visible response before the
    interruption: ...". '' when there is none."""
    with _LOCK:
        if not _NOTE.get("pending"):
            return ""
        rec = dict(_NOTE.get("rec") or {})
        if consume:
            _NOTE["pending"] = False
    if not rec:
        return ""
    parts = [" [Context from your interrupted spoken reply]"]
    if rec.get("request"):
        parts.append(' You were answering: "%s".' % rec["request"][:200])
    if rec.get("said"):
        parts.append(' What you had said before the interruption (its end): "%s".' % rec["said"][-300:])
    if rec.get("last_sentence"):
        parts.append(' You stopped after: "%s".' % rec["last_sentence"])
    parts.append(" If he asks you to go on, continue from there; otherwise answer only this turn.")
    return "".join(parts)
