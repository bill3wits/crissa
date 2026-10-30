"""Her reply on the Wall's log as the model streams it, before her voice says it (round 18, 2026-10-13).

The User 2026-10-13 (.101 round 17, 15:08): "her long ones are not streaming to logs. I want her words to print live
too", then "we need it to stream to the logs before synth if possible", and for a cut: the part she printed but never
said is marked, so the log does not claim she said it. And for round 18: "her words streamed live, before synthesis if
possible, with any unspoken rest marked when she is cut".

Until now the log showed her words only as the mouth's line (house-speak's tts gate: the piece on the air), one piece
at a time and only when a 2 s poll caught it, so a reading never showed and a long answer showed in holes (.101 15:08:
of 14 rows none was her reply; she appeared only as "Cancelled 544 characters into ..."). Now the dispatch writes each
reply as ONE row while it streams, and the feed draws it as one line that both Walls grow in place:

  begin()             a reply starts (each reply of a turn; a revise turn's reply takes the same row: its draft was
                      never said - the dispatch revises only before her first clause - so it is replaced, not kept)
  grow(buf)           the stream so far: the row's text is its complete clauses (think blocks, planning and markdown
                      out, the bridge's own cleaner), written when it grows by a clause (at most every WRITE_S)
  on_air(piece) / off_air()   a piece handed to her voice and done (speak_blocking): where she is in the row
  cut(rec)            a stop (abort_mouth): the cut point the bridge already records (cancels.record, the "Cancelled N
                      characters into ..." row: what she had said by the say log's clock); the rest is what she never
                      said. The text stops growing there.
  done(reply, said, stopped)  the dispatch's end: the finished reply and what she said of it (the history's `said`)
  close()             _finish_dispatch: a row still open that nothing was said of (cancelled before her first word)
                      is marked so; an empty one goes

    <home>/her-words.json: {"v": 1, "rows": [{"rid", "ts", "at", "text", "state": "open" | "done" | "cut",
                            "said_n", "pieces": [[o0, o1, t0, t1 | null], ...], "why"}]}

said_n: how many characters of `text` she said (null while open, and when she said all of it). The feed
(crissa/overlay/scripts/voiceonly-home-server.py her_words_rows) shows a window of the row around where her voice is,
dims what she has not reached yet and strikes what she never said. Kill: CRISSA_LOG_LIVE_WORDS=0 (nothing written;
the log shows the mouth's line as in round 17).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .home import HOME

KEEP = 8                       # rows kept in the file (the feed shows the last HEARD_ROLL_S of them)
KEEP_S = 900.0
MAX_CHARS = 20000              # a reading is capped at SPEAK_CHARS (16000) by the bridge
WRITE_S = 0.2                  # a growing row is written at most this often (the Walls read every 2 s)
ANCHOR = 8                     # words of the cancel record's `said` used to find the cut in the row
CHARS_PER_S = float(os.environ.get("EAR_BRIDGE_SPOKEN_CPS", "15"))   # cancels.CHARS_PER_S: the fallback cut clock
_LOCK = threading.RLock()
_ROWS: list[dict[str, Any]] = []
_OPEN: dict[str, Any] = {"row": None}
_CLEAN: dict[str, Callable[[str], str] | None] = {"f": None}
_CLAUSE_END = re.compile(r"[.!?…,;:—][\"'”’)\]]*(?=\s)")
_WORD = re.compile(r"\S+")


def on() -> bool:
    return os.environ.get("CRISSA_LOG_LIVE_WORDS", "1").strip().lower() not in ("0", "false", "no", "off")


def path() -> Path:
    return HOME / "her-words.json"


def set_cleaner(f: Callable[[str], str] | None) -> None:
    """The bridge's own text cleaner (markdown, links, ids: _piece_clean), so the log reads as her voice is given it."""
    _CLEAN["f"] = f


def _norm(w: str) -> str:
    return re.sub(r"[^\w']+", "", (w or "").lower())


def _spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, normalised word) for every word of `text` with letters or digits in it."""
    out = []
    for m in _WORD.finditer(text or ""):
        n = _norm(m.group(0))
        if n:
            out.append((m.start(), m.end(), n))
    return out


def _words(text: str) -> list[str]:
    return [n for _a, _b, n in _spans(text)]


def _strip_think(buf: str) -> str:
    t = re.sub(r"<think>[\s\S]*?</think>", " ", buf or "", flags=re.I)
    k = t.lower().find("<think>")
    return t[:k] if k >= 0 else t


def _lead_out(t: str) -> str:
    try:
        from .ttfs import spoken_from_reply      # leading third-person planning out, as the reply is
        return spoken_from_reply(t)
    except Exception:  # noqa: BLE001
        return t


def _clean_seg(raw: str) -> str:
    """Markdown, links and ids out of a piece of her text (the bridge's cleaner), whitespace made single."""
    t = _strip_think(raw)
    f = _CLEAN.get("f")
    if f is not None:
        try:
            t = f(t)
        except Exception:  # noqa: BLE001 - the log is a courtesy; an uncleaned clause is still her words
            pass
    return " ".join(str(t or "").split())


def _clean(raw: str) -> str:
    return " ".join(_lead_out(_clean_seg(raw)).split())


def _complete(buf: str) -> str:
    """The stream's text up to its last clause end that has whitespace after it (a dot at the buffer's very end may be
    the "4." of "4.20" still arriving: ttfs's own rule)."""
    t = _strip_think(buf)
    ends = [m.end() for m in _CLAUSE_END.finditer(t)]
    return t[: ends[-1]] if ends else ""


def _save(now: float | None = None) -> None:
    """Write the file (atomically)."""
    now = time.time() if now is None else float(now)
    _OPEN["t_save"] = now
    keep = [r for r in _ROWS if now - float(r.get("at") or r.get("ts") or now) < KEEP_S][-KEEP:]
    _ROWS[:] = keep
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"v": 1, "rows": keep}), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def begin(now: float | None = None) -> str | None:
    """A reply starts: its row (a revise turn's reply takes the open row nothing was said of). The row's id."""
    if not on():
        return None
    now = time.time() if now is None else float(now)
    with _LOCK:
        row = _OPEN.get("row")
        if row is not None and row.get("state") == "open" and not row.get("pieces"):
            row.update(text="", at=now)          # the draft before his last words: never said, replaced
            _OPEN.update(raw_n=0, said=None, acc="", raw="")
            _save(now)
            return str(row["rid"])
        if row is not None:
            _close_locked(now)
        row = {"rid": "h%d" % int(now * 1000), "ts": round(now, 3), "at": round(now, 3), "text": "",
               "state": "open", "said_n": None, "pieces": [], "why": ""}
        _ROWS.append(row)
        _OPEN.update(row=row, raw_n=0, said=None, acc="", raw="")
        _save(now)
        return str(row["rid"])


def _flush_locked(now: float) -> bool:
    """The clauses received since the last write onto the open row (only the new part is cleaned). True if it grew."""
    row = _OPEN.get("row")
    raw = str(_OPEN.get("raw") or "")
    n0 = int(_OPEN.get("raw_n") or 0)
    if row is None or row.get("state") != "open" or len(raw) <= n0:
        return False
    _OPEN["raw_n"] = len(raw)
    add = _clean_seg(raw[n0:])
    if not add:
        return False
    acc = (str(_OPEN.get("acc") or "") + " " + add).strip()
    _OPEN["acc"] = acc
    txt = _lead_out(acc)[:MAX_CHARS]
    if not txt or txt == row.get("text"):
        return False
    row["text"], row["at"] = txt, round(now, 3)
    return True


def grow(buf: str, now: float | None = None) -> None:
    """The stream so far (the reply's raw text, on the stream's own thread): the row grows by whole clauses, written
    at most every WRITE_S (on_air, cut and done write what is waiting). Never raises."""
    if not on():
        return
    try:
        now = time.time() if now is None else float(now)
        with _LOCK:
            row = _OPEN.get("row")
            if row is None or row.get("state") != "open":
                return
            raw = _complete(buf or "")
            if len(raw) > len(str(_OPEN.get("raw") or "")):
                _OPEN["raw"] = raw
            if len(str(_OPEN.get("raw") or "")) <= int(_OPEN.get("raw_n") or 0):
                return                           # nothing new since the last write
            if now - float(_OPEN.get("t_save") or 0.0) < WRITE_S:
                return                           # the stream's next piece, a piece on the air or the end writes it
            if _flush_locked(now):
                _save(now)
    except Exception:  # noqa: BLE001 - the log never stops her stream
        pass


def on_air(piece: str, now: float | None = None) -> None:
    """A piece handed to her voice: where it sits in the open row (its words after the last piece's), or nothing when
    it is not the row's (a callout, an ack)."""
    if not on() or not (piece or "").strip():
        return
    try:
        now = time.time() if now is None else float(now)
        with _LOCK:
            row = _OPEN.get("row")
            if row is None or row.get("state") != "open":
                return
            _flush_locked(now)
            sp = _spans(row.get("text") or "")
            pw = _words(piece)
            if not sp or not pw:
                return
            after = max([int(p[1]) for p in row["pieces"]] or [0])
            first = next((i for i, s in enumerate(sp) if s[0] >= after), None)
            if first is None:
                return
            k = min(len(pw), 6)
            at = None
            for i in range(first, len(sp) - k + 1):
                if [s[2] for s in sp[i:i + k]] == pw[:k]:
                    at = i
                    break
            if at is None:
                return
            j = min(len(sp) - 1, at + len(pw) - 1)
            row["pieces"].append([sp[at][0], sp[j][1], round(now, 3), None])
            row["at"] = round(now, 3)
            _save(now)
    except Exception:  # noqa: BLE001
        pass


def off_air(now: float | None = None) -> None:
    """The piece on the air ended (said whole, or stopped: a stop's cut() came first)."""
    if not on():
        return
    now = time.time() if now is None else float(now)
    with _LOCK:
        row = _OPEN.get("row")
        if row is None or not row.get("pieces") or row["pieces"][-1][3] is not None:
            return
        row["pieces"][-1][3] = round(now, 3)
        if row.get("state") == "open":
            _save(now)


def _anchor_end(text: str, said: str, before: int | None = None) -> int | None:
    """The end of the last occurrence in `text` (ending at or before `before`) of the last ANCHOR words of `said`; the
    record cuts the piece on the air by characters, so its last word may be half a word: then the words before it."""
    words = _words(said)
    sp = _spans(text)
    for sw in (words[-ANCHOR:], words[-ANCHOR - 1:-1]):
        if not sw or not sp:
            continue
        k = len(sw)
        best = None
        for i in range(0, len(sp) - k + 1):
            if [s[2] for s in sp[i:i + k]] == sw:
                end = sp[i + k - 1][1]
                if before is None or end <= before:
                    best = end
        if best is not None:
            return best
    return None


def _word_end_before(text: str, n: int) -> int:
    """`n` moved back to the end of the word it falls in (0 when it is inside the first word)."""
    n = max(0, min(len(text), int(n)))
    if n >= len(text) or text[n:n + 1].isspace():
        return n
    sp = text.rfind(" ", 0, n)
    return max(0, sp)


def cut(rec: dict | None, why: str = "", now: float | None = None) -> None:
    """She was stopped: the cancel record's own cut point (its `said` - what she had said by the say log's clock - is
    found in the row), else the piece on the air cut by CHARS_PER_S. The row stops growing."""
    if not on():
        return
    try:
        now = time.time() if now is None else float(now)
        with _LOCK:
            row = _OPEN.get("row")
            if row is None or row.get("state") != "open":
                return
            _flush_locked(now)
            text = row.get("text") or ""
            pieces = row.get("pieces") or []
            on_p = pieces[-1] if pieces and pieces[-1][3] is None else None
            limit = int(on_p[1]) if on_p else (max([int(p[1]) for p in pieces]) if pieces else None)
            n = None
            said = str((rec or {}).get("said") or "")
            if said and pieces:
                n = _anchor_end(text, said, before=limit)
            if n is None:
                if on_p:
                    n = _word_end_before(text, int(on_p[0]) + int(max(0.0, now - float(on_p[2])) * CHARS_PER_S))
                    n = min(n, int(on_p[1]))
                else:
                    n = max([int(p[1]) for p in pieces if p[3] is not None] or [0])
            row.update(state="cut", said_n=int(n), why=str(why or (rec or {}).get("why") or "")[:40],
                       at=round(now, 3))
            if on_p:
                on_p[3] = round(now, 3)
            _save(now)
    except Exception:  # noqa: BLE001
        pass


def _said_n(text: str, said: str) -> tuple[bool, int | None]:
    """(said is a word-prefix of text, the character end of it in text; None when it is all of text)."""
    tw, sw = _spans(text), _words(said)
    if not sw or len(sw) > len(tw) or [s[2] for s in tw[:len(sw)]] != sw:
        return False, None
    return True, (None if len(sw) == len(tw) else tw[len(sw) - 1][1])


def done(reply: str | None, said: str | None, stopped: bool = False, now: float | None = None) -> None:
    """The dispatch's end: the reply as finished and what she said of it. A cut row keeps its cut."""
    if not on():
        return
    try:
        now = time.time() if now is None else float(now)
        with _LOCK:
            row = _OPEN.get("row")
            if row is None:
                return
            if row.get("state") == "open":
                _flush_locked(now)
                said = " ".join(str(said or "").split())
                streamed = row.get("text") or ""
                final = _clean(str(reply or ""))[:MAX_CHARS]
                if not said:
                    row["text"] = streamed or final
                    row["state"] = "cut" if stopped else "done"
                    row["said_n"] = 0 if (stopped and row["text"]) else None
                else:
                    for cand in (streamed, final):
                        ok, n = _said_n(cand, said)
                        if ok:
                            row["text"], row["said_n"] = cand, n
                            break
                    else:
                        row["text"], row["said_n"] = said[:MAX_CHARS], None   # she said something else: that
                    row["state"] = "cut" if stopped else "done"
                row["at"] = round(now, 3)
            _OPEN["row"] = None
            if not row.get("text"):
                _ROWS[:] = [r for r in _ROWS if r is not row]
            _save(now)
    except Exception:  # noqa: BLE001
        pass


def _close_locked(now: float) -> None:
    row = _OPEN.get("row")
    _OPEN["row"] = None
    if row is None:
        return
    if row.get("state") == "open":
        said_end = max([int(p[1]) for p in row.get("pieces") or [] if p[3] is not None] or [0])
        row["state"] = "cut"
        row["said_n"] = said_end if said_end < len(row.get("text") or "") else None
        row["at"] = round(now, 3)
    if not row.get("text"):
        _ROWS[:] = [r for r in _ROWS if r is not row]
    _save(now)


def close(now: float | None = None) -> None:
    """The turn is over (_finish_dispatch): a row still open is closed - what was said of it stands, the rest is not
    said (a reply cancelled before her first word is all "not said"); an empty row goes."""
    if not on():
        return
    try:
        with _LOCK:
            if _OPEN.get("row") is not None:
                _close_locked(time.time() if now is None else float(now))
    except Exception:  # noqa: BLE001
        pass


def reset() -> None:
    """Tests: forget the rows in memory."""
    with _LOCK:
        _ROWS.clear()
        _OPEN.clear()
        _OPEN["row"] = None
