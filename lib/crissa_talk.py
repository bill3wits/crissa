"""How she talks, waits and listens: one settings reader (2026-10-06).

Three layers, later wins: the shipped `config/crissa-talk.json`, then `<crissa home>/talk.json` (the Init's
`talk` action writes it), then a few environment variables for a service unit. Every value is plain data:
the machine's and the user's values live in the home file, never in code.

NO HIDDEN LIMITS. `reply.max_chars`, `speak.max_chars` and `reply.timeout_s` are 0 by default, which means
none. When a limit is set and something is cut, `keep()` writes the whole text to `<home>/cut-text/` and
returns a note saying how much was kept and where the rest is; the player shows that note on the glass.

A LONG LINE IS READ IN PIECES (round 14, 2026-10-12, .101: a reading was "Cut at 799 of 6667 characters"). The
speaker's streamed voice takes at most 800 characters a line (scripts/house-speak.py VOICE_TEXT_CAP), so in a pack a
longer line is read as consecutive pieces of at most `speak.piece_chars` characters (default 760), each ending on a
sentence (else a word), each its own line with its own mouth sheet; nothing is dropped. `split_pieces()` cuts them.
0 turns pieces off (one line, cut at the cap with a note, as before). The house keeps one line.

WHEN HIS TURN ENDS AND HER MOUTH OPENS (round 17, 2026-10-13, the User: "yes cut the waits"). `turn.end_s` (0.6 s) is the
silence that closes his utterance in a conversation (the ear's hangover, 2.0 s before); with `turn.speculate` her turn
starts as his lands and is cancelled silently, then asked again with the whole utterance, if he goes on before her mouth
opens; her mouth opens only after `turn.final_s` (2.0 s) without speech and `turn.gap_s` (0.3 s, 2.0 before) after his
turn reached the bridge. CRISSA_EAR_END_S, CRISSA_TURN_FINAL_S, CRISSA_SPEAK_GAP_S override; CRISSA_EAR_SPECULATE=0 (read
by the ear and the bridge) turns speculation off and puts the 2.0 s hangover back.

WHAT THE CHAT IS ASKED FOR (round 15, 2026-10-12, .101: an 18,123-character reading hit the 4,096-token reply cap
and took 54 s; the User: "We need to tell grok to limit prompt to 10k tok or so."). `reply.max_tokens` (4096) is the
reply budget of a turn; `reply.reading_tokens` (1000) is a READING's budget per request, "go on" fetching the next;
`reply.context_tokens` (10000) caps what is sent (system prompt + history + the turn, estimated at 4 characters a
token): the oldest history goes first, never the system prompt or the turn. EAR_BRIDGE_MAX_TOKENS,
EAR_BRIDGE_READING_TOKENS and EAR_BRIDGE_CONTEXT_TOKENS override them. 0 = no cap of ours.

WHAT MAY CUT HER LINE (round 18, 2026-10-13, the User: "Interrupt words only" and "Id like to talk with someone else
while she speaks without interrupting her"). `barge.words` is the list of interrupt words he approved (with her name,
`barge.name`, and more spellings of it, `barge.names`); `barge.words_only` false is round 17's any-word cut;
`barge.side_talk` keeps anything else said over her answer from her (the Wall's log shows it as room speech). Read by the
ear's barge.py and the bridge. CRISSA_BARGE_WORDS_ONLY and CRISSA_SIDE_TALK override.

`speak_tuning` holds the speaker's dials (tempo, follow-line, buffering, read size, first-chunk and tail
waits, align lead). null leaves the speaker's own default; `speak_env()` turns the set ones into the
variables the speaker reads. A variable already in the player's environment wins (a unit that pins one).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path

try:
    from crissa_home import HOME
except ImportError:  # imported by path from a test or a tool
    HOME = Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()

SHIPPED = Path(__file__).resolve().parent.parent / "config" / "crissa-talk.json"
FALLBACK = {
    "reply": {"max_chars": 0, "timeout_s": 0, "stream_sentences": True, "min_sentence_chars": 12,
              "working_filler": ["One moment."], "working_every_s": 20,
              "max_tokens": 4096, "reading_tokens": 1000, "context_tokens": 10000},
    "speak": {"max_chars": 0, "piece_chars": 760},
    "announce": {"queue_max": 20, "roundup_over": 3, "dedup_s": 45, "speech_pause_s": 12, "max_wait_s": 0,
                 "quiet_hours": None, "voice": ""},
    "ear": {"wake": [], "variants": [], "gather_s": 1.2, "tail_s": 1.5, "follow_s": 20, "barge": True,
            "body_cues": True, "poll_s": 0.3},
    "turn": {"speculate": True, "end_s": 0.6, "final_s": 2.0, "gap_s": 0.3},
    "barge": {"words_only": True, "words": ["stop", "wait", "hold on", "hang on", "one second", "one sec", "pause",
                                            "quiet", "enough", "cancel", "never mind", "excuse me"],
              "name": True, "names": [], "side_talk": True},
    "speak_tuning": {"tempo": None, "follow_line": None, "buffer": None, "read_bytes": None,
                     "first_chunk_ms": None, "tail_starve_ms": None, "align_lead_ms": None},
}
# speak_tuning key -> (variable the speaker reads, kind)
SPEAK_VARS = {
    "tempo": ("HOUSE_SPEAK_TEMPO", float),
    "follow_line": ("HOUSE_SPEAK_FOLLOW_LINE", bool),
    "buffer": ("HOUSE_SPEAK_BUFFER", bool),
    "read_bytes": ("HOUSE_SPEAK_READ_BYTES", int),
    "first_chunk_ms": ("HOUSE_SPEAK_FIRST_CHUNK_MS", int),
    "tail_starve_ms": ("HOUSE_SPEAK_TAIL_STARVE_MS", int),
    "align_lead_ms": ("HOUSE_ALIGN_LEAD_MS", int),
}
ENV = {("reply", "max_chars"): "CRISSA_REPLY_MAX_CHARS", ("reply", "timeout_s"): "CRISSA_REPLY_TIMEOUT_S",
       ("speak", "max_chars"): "CRISSA_SPEAK_MAX_CHARS", ("speak", "piece_chars"): "CRISSA_SPEAK_PIECE_CHARS",
       # the ear bridge's own names, kept as overrides (round 15, 2026-10-12)
       ("reply", "max_tokens"): "EAR_BRIDGE_MAX_TOKENS", ("reply", "reading_tokens"): "EAR_BRIDGE_READING_TOKENS",
       ("reply", "context_tokens"): "EAR_BRIDGE_CONTEXT_TOKENS",
       # when his turn ends and her mouth opens (round 17, 2026-10-13: the ear's turn_end.py, the bridge's speculate.py)
       ("turn", "end_s"): "CRISSA_EAR_END_S", ("turn", "final_s"): "CRISSA_TURN_FINAL_S",
       ("turn", "gap_s"): "CRISSA_SPEAK_GAP_S"}
_START_ENV = frozenset(os.environ)      # what the unit pinned before this module was imported


def path(home: Path | None = None) -> Path:
    return (home or HOME) / "talk.json"


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge(base: dict, top: dict) -> dict:
    out = copy.deepcopy(base)
    for sec, vals in (top or {}).items():
        if sec.startswith("_") or not isinstance(vals, dict) or not isinstance(out.get(sec), dict):
            continue
        for k, v in vals.items():
            if k in out[sec]:
                out[sec][k] = v
    return out


def load(home: Path | None = None, env: dict | None = None) -> dict:
    e = os.environ if env is None else env
    cfg = _merge(_merge(FALLBACK, _read(SHIPPED)), _read(path(home)))
    for (sec, k), var in ENV.items():
        v = str(e.get(var) or "").strip()
        if v:
            try:
                cfg[sec][k] = float(v)
            except ValueError:
                pass
    return cfg


def _num(v, lo: float, hi: float, what: str):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % what) from None
    if not lo <= f <= hi:
        raise ValueError("%s must be between %s and %s" % (what, lo, hi))
    return f


def validate(update: dict) -> dict:
    """The update cleaned to known keys and sane values. Raises ValueError with the reason."""
    out: dict = {}
    for sec, vals in (update or {}).items():
        if sec not in FALLBACK or not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            if k not in FALLBACK[sec]:
                raise ValueError("%s.%s is not a setting" % (sec, k))
            what = "%s.%s" % (sec, k)
            if sec == "speak_tuning":
                if v is None:
                    pass
                elif SPEAK_VARS[k][1] is bool:
                    v = bool(v)
                elif k == "tempo":
                    v = _num(v, 0.6, 1.6, what)
                else:
                    v = int(_num(v, 0, 10 ** 7, what))
            elif k == "quiet_hours":
                if v is not None:
                    if not isinstance(v, dict):
                        raise ValueError("announce.quiet_hours is {\"from\": H, \"to\": H} or null")
                    v = {"from": int(_num(v.get("from"), 0, 23, "quiet_hours.from")),
                         "to": int(_num(v.get("to"), 0, 23, "quiet_hours.to"))}
            elif k in ("wake", "variants", "working_filler", "words", "names"):
                if not isinstance(v, list) or any(not isinstance(x, str) or len(x) > 80 for x in v):
                    raise ValueError("%s must be a list of short words" % what)
                v = [x.strip() for x in v if x.strip()]
            elif k == "voice":
                v = str(v or "").strip()[:80]
            elif isinstance(FALLBACK[sec][k], bool):
                v = bool(v)
            else:
                v = _num(v, 0, 10 ** 7, what)
            out.setdefault(sec, {})[k] = v
    return out


def save(update: dict, home: Path | None = None) -> dict:
    """Merge a validated update into `<home>/talk.json`; returns the effective settings."""
    clean = validate(update)
    p = path(home)
    cur = _read(p)
    for sec, vals in clean.items():
        cur.setdefault(sec, {}).update(vals)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return load(home)


def speak_env(cfg: dict | None = None, env: dict | None = None) -> dict:
    """The speaker variables for the set dials, minus any the player's own unit pinned."""
    cfg = load() if cfg is None else cfg
    pinned = _START_ENV if env is None else frozenset(env)
    out = {}
    for k, (var, kind) in SPEAK_VARS.items():
        v = (cfg.get("speak_tuning") or {}).get(k)
        if v is None or var in pinned:
            continue
        out[var] = ("1" if v else "0") if kind is bool else ("%.3f" % float(v) if kind is float else str(int(v)))
    return out


def quiet(cfg: dict, now: float | None = None) -> bool:
    """Inside the quiet hours? {"from": 22, "to": 8} wraps midnight; null = never quiet."""
    q = (cfg.get("announce") or {}).get("quiet_hours")
    if not isinstance(q, dict):
        return False
    h = time.localtime(time.time() if now is None else now).tm_hour
    a, b = int(q.get("from", 0)), int(q.get("to", 0))
    if a == b:
        return False
    return (a <= h < b) if a < b else (h >= a or h < b)


def keep(text: str, limit, label: str, home: Path | None = None) -> tuple[str, dict | None]:
    """(text, None) when there is no limit or it fits; else (the kept part, a note).

    The kept part ends at the last sentence end inside the limit when there is one. The note says how much
    was kept and where the whole text is: `<home>/cut-text/<label>-<sha12>.txt`.
    """
    t = text or ""
    try:
        n = int(float(limit or 0))
    except (TypeError, ValueError):
        n = 0
    if n <= 0 or len(t) <= n:
        return t, None
    head = t[:n]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    kept = (head[:end + 1] if end > n // 3 else head).strip()
    full = (home or HOME) / "cut-text" / ("%s-%s.txt" % (label, hashlib.sha256(t.encode()).hexdigest()[:12]))
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(t, encoding="utf-8")
        where = str(full)
    except OSError:
        where = ""
    note = {"kept": len(kept), "total": len(t), "full_text": where, "at": time.time(),
            "note": "Cut at %d of %d characters%s." % (len(kept), len(t),
                                                         ("; the whole text is " + where) if where else "")}
    return kept, note


PIECE_MAX = 780          # the speaker's streamed voice takes 800 characters a line; a little room for its own rewording
PIECE_MIN = 200


def piece_size(cfg: dict | None = None) -> int:
    """speak.piece_chars as an int the speaker can use: 0 = no pieces, else clamped to [PIECE_MIN, PIECE_MAX]."""
    cfg = load() if cfg is None else cfg
    try:
        n = int(float((cfg.get("speak") or {}).get("piece_chars") or 0))
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 0
    return max(PIECE_MIN, min(PIECE_MAX, n))


_PIECE_END = __import__("re").compile(r"[.!?\u2026][\"'\u201d\u2019)\]]*(?=\s|$)")


def split_pieces(text: str, size: int) -> list[str]:
    """`text` as consecutive pieces of at most `size` characters, each ending at the last sentence end inside it (else
    at a word; a run with no space at `size`). Joined with spaces they are the text, whitespace aside: nothing is
    dropped. size <= 0 or a text that fits: one piece."""
    t = " ".join((text or "").split())
    if size <= 0 or len(t) <= size:
        return [t] if t else []
    out: list[str] = []
    while t:
        if len(t) <= size:
            out.append(t)
            break
        window = t[:size + 1]
        ends = [m.end() for m in _PIECE_END.finditer(window) if m.end() <= size]
        if ends and ends[-1] >= size // 3:
            e = ends[-1]
        else:
            sp = window.rfind(" ", 0, size + 1)
            e = sp if sp > size // 3 else size
        out.append(t[:e].strip())
        t = t[e:].strip()
    return [p for p in out if p]
