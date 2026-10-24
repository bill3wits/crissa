"""Her own voice heard back is not a turn (round 13, the User 2026-10-12, .101).

On .101 her lines came back through the room into the Shure: the ear wrote "My mum made fung jam. Wash my lipstick on
every P B and Z." (her own lip-sync test line) to the turn ring, the Wall logged it as STRANGER, and with a session
open the bridge sent her echoes to the chat as the User's words ("Yeah, the User. Yeah, the User. Got it." -> HTTP 400 ->
"I couldn't reach my chat service. Try once more." -> heard again -> ...), three rounds. The fingerprint veto never
fired: a pack ships no prints of its own voice, and a pack's speaker plays what house-speak writes to
`house-tts-gate.json`, a file the ear never read (it reads the bridge's `tts-gate.json`).

The rule, evidence first: an utterance heard while one of her lines was on the air (from just before its first sound to
TAIL_S after its last) whose words match that line (fuzzy and positional, MATCH) is her echo. It is not decoded into a
turn: the ear returns no text, writes it to logs/self-echo.jsonl, and nothing downstream (the ring, the Wall's log, the
bridge) sees it. A person clearly speaking (a voice print at PERSON_SIM or more) is never her echo, and a barge-in
("Hey Crissa, stop") calls her by name, so it passes even when it lands on top of her words.

Her lines come from both gate files (house-speak's, with the air window, and the bridge's) and are kept here for
KEEP_S, so a run of lines (an error line right after an answer) is still known when its echo is decoded.

Kill: CRISSA_SELF_ECHO=0. Knobs: CRISSA_SELF_ECHO_TAIL_S (2.0), CRISSA_SELF_ECHO_MATCH (0.6).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .home import HOME

TAIL_S = float(os.environ.get("CRISSA_SELF_ECHO_TAIL_S", "2.0"))
LEAD_S = 0.3
MATCH = float(os.environ.get("CRISSA_SELF_ECHO_MATCH", "0.6"))
PERSON_SIM = float(os.environ.get("CRISSA_SELF_ECHO_PERSON_SIM", "0.60"))
# round 14: what is left after her words are taken out
PERSON_OVER_HER = float(os.environ.get("CRISSA_SELF_ECHO_PERSON_OVER_HER", "0.30"))   # a person's voice in the chunk
RESIDUE_ECHO = float(os.environ.get("CRISSA_SELF_ECHO_RESIDUE_ECHO", "0.50"))         # the rest still reads like her
RESIDUE_MIN_WORDS = 4
KEEP_S = 90.0

# ONLY WHAT SHE HAD ON THE AIR IN THE LAST FEW SECONDS (round 16, .101 2026-10-13 00:43: five of the User's lines -
# "Hang on.", "Right. Hang on.", "Thank you.", "What book was that?", "Okay, that's good. Stop there, Krista." - were
# dropped as her echo at 0.60-0.73, every one against the SAME text: the reading's opening, long after it was stopped
# and cancelled). A line's row was closed only by a gate write that still carried its words; a stop or a cancel writes
# none, and the next line replaced it unseen, so the row stayed "on the air" for good. Now a new line, a stop and a
# cancel close what was open; a row never runs longer than its words at OPEN_CPS; a long line is compared only where
# she was in it (her pace SAY_CPS, or the line's own when its end is known) around the chunk; a chunk of three words or
# fewer needs MATCH_SHORT; and a stop word she was not saying, or her name, is never her echo. Kill:
# CRISSA_SELF_ECHO_FRESH=0 (round 15's reference).
FRESH = os.environ.get("CRISSA_SELF_ECHO_FRESH", "1").strip().lower() not in ("0", "false", "no", "off")
OPEN_CPS = 11.0          # an open line has run out by len/OPEN_CPS + OPEN_PAD_S (a slow pace, so never cut short)
OPEN_PAD_S = 4.0
SAY_CPS = float(os.environ.get("CRISSA_SELF_ECHO_CPS", "15"))
SLICE_PAD_S = 2.5        # around the chunk, plus 15 % of how far into the line she is (the pace is a guess)
WHOLE_CHARS = 160        # a line this short is compared whole
# faster than any voice of hers: a line's "own pace" above this is a line that was cut short (round 17; 0 = off; off too
# with CRISSA_BARGE_FROM_ASR=0, so that switch is the way back to round 17 before the barge work)
MAX_CPS = float(os.environ.get("CRISSA_SELF_ECHO_MAX_CPS", "25") or 0)
MATCH_SHORT = float(os.environ.get("CRISSA_SELF_ECHO_MATCH_SHORT", "0.85"))
SHORT_WORDS = 3
_CLOSE_NOTICES = ("interrupted", "cancelled", "canceled", "stopped", "stop")


def enabled() -> bool:
    return os.environ.get("CRISSA_SELF_ECHO", "1").strip().lower() not in ("0", "false", "no", "off")


def gate_paths() -> list[Path]:
    """The bridge's gate (tts-gate.json), house-speak's (house-tts-gate.json; lib/audio_sink's default name) and the
    file HOUSE_TTS_GATE names, when it names another."""
    env = (os.environ.get("HOUSE_TTS_GATE") or "").strip()
    out = [HOME / "tts-gate.json", HOME / "house-tts-gate.json"]
    if env and Path(env).expanduser() not in out:
        # the ear's own settings seed HOUSE_TTS_GATE with the bridge's file; house-speak's is read as well (round 14:
        # with the variable set, round 13 read only the bridge's gate and missed every line house-speak played alone)
        out.insert(0, Path(env).expanduser())
    return out


def log_path() -> Path:
    return HOME / "logs" / "self-echo.jsonl"


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class Lines:
    """Her recent lines: {"start", "end", "text"}, newest last, from the gate files."""

    def __init__(self):
        self._rows: deque = deque(maxlen=32)
        self._lock = threading.Lock()

    def note(self, start: float, end: float | None, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            for r in self._rows:
                if r["text"] == text and (abs(r["start"] - start) < 3.0 or (FRESH and r["end"] is None)):
                    r["start"] = min(r["start"], start)
                    if end is not None:
                        r["end"] = max(r["end"] or end, end)
                    return
            if FRESH:
                # a new line: whatever was still open has ended (its own closing write may never have been seen)
                for r in self._rows:
                    if r["end"] is None:
                        r["end"] = max(r["start"], float(start))
            self._rows.append({"start": float(start), "end": end, "text": text})

    def close_all(self, at: float) -> None:
        """A stop or a cancel: nothing of hers is on the air after `at`."""
        with self._lock:
            for r in self._rows:
                if r["start"] <= at and (r["end"] is None or r["end"] > at):
                    r["end"] = max(r["start"], float(at))

    def refresh(self, now: float | None = None, paths: list[Path] | None = None) -> None:
        now = time.time() if now is None else float(now)
        for p in (paths or gate_paths()):
            try:
                g = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(g, dict):
                continue
            text = str(g.get("last_reply") or "").strip()
            updated = _f(g.get("updated_ts")) or 0.0
            air_at, air_until = _f(g.get("air_at")), _f(g.get("air_until"))
            speaking = bool(g.get("speaking"))
            if FRESH and not speaking:
                notice = str(g.get("notice") or "").strip().lower()
                if (notice in _CLOSE_NOTICES or not text) and (air_until or updated):
                    self.close_all(float(air_until or updated))       # stopped / cancelled: off the air
            if not text:
                continue
            if air_at:
                start = air_at
                end = air_until if air_until else (None if speaking else updated or None)
            else:
                # the bridge's gate has no air clock: written at the start (speaking) and at the end (not)
                end = None if speaking else updated
                start = updated if speaking else (updated - max(2.0, len(text) / 13.0))
            if now - (end or now) > KEEP_S:
                continue
            self.note(start, end, text)
        with self._lock:
            while self._rows and self._rows[0]["end"] is not None and now - self._rows[0]["end"] > KEEP_S:
                self._rows.popleft()

    def newest(self, now: float | None = None, keep_s: float = KEEP_S) -> str:
        """The newest line's text that started at most keep_s ago, or '' (round 17: barge.words_over_her's fallback when
        no stretch matches the window - her pace slower than the open row's cap, or a late ear)."""
        now = time.time() if now is None else float(now)
        with self._lock:
            rows = [dict(r) for r in self._rows if r["start"] <= now and now - r["start"] <= keep_s]
        return rows[-1]["text"] if rows else ""

    def during(self, t0: float, t1: float, *, now: float | None = None, tail_s: float = TAIL_S) -> str:
        """Every line whose air (LEAD_S before its start to tail_s after its end) overlaps [t0, t1] - of a long line,
        only the stretch she was saying then (FRESH)."""
        now = time.time() if now is None else float(now)
        with self._lock:
            rows = [dict(r) for r in self._rows]
        hit = []
        for r in rows:
            end = _end_of(r, now)
            if r["start"] - LEAD_S <= t1 and end + tail_s >= t0:
                hit.append(_stretch(r, t0, t1, end) if FRESH else r["text"])
        return " ".join(h for h in hit if h)


def _end_of(r: dict, now: float) -> float:
    if r["end"] is not None:
        return float(r["end"])
    if not FRESH:
        return now
    return min(now, r["start"] + len(r["text"]) / OPEN_CPS + OPEN_PAD_S)


def _stretch(r: dict, t0: float, t1: float, end: float) -> str:
    """The part of a line she was saying between t0 and t1 (whole words, SLICE_PAD_S either side)."""
    text = r["text"]
    if len(text) <= WHOLE_CHARS:
        return text
    dur = end - r["start"]
    cps = len(text) / dur if (r["end"] is not None and dur > 1.0) else SAY_CPS
    if MAX_CPS > 0 and cps > MAX_CPS and os.environ.get("CRISSA_BARGE_FROM_ASR", "1").strip().lower() not in (
            "0", "false", "no", "off"):
        # a line stopped part-way (a cut, a stop) ends early: its length over the time she spoke is no pace of hers
        # (round 17, 2026-10-13: a reading cut 10 s in read as 70 characters a second, and the stretch compared with
        # the person's turn was a part of her line she never reached, so her own words stayed in it)
        cps = SAY_CPS
    into = max(0.0, t1 - r["start"])
    pad = SLICE_PAD_S + 0.15 * into
    a = int(max(0.0, (t0 - r["start"] - pad) * cps))
    b = int(min(len(text), (t1 - r["start"] + pad) * cps))
    if b <= a:
        return ""
    a = text.rfind(" ", 0, a) + 1 if a > 0 else 0
    nb = text.find(" ", b)
    b = len(text) if nb < 0 else nb
    return text[a:b].strip()


LINES = Lines()


def _norm(s: str) -> str:
    s = (s or "").lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def text_match(heard: str, spoken: str) -> float:
    """How well `heard` matches some stretch of `spoken` (0..1): the best ratio against every window of about as many
    spoken words (the bridge's own_voice.text_match, kept here so the ear needs no bridge import)."""
    h, s = _norm(heard), _norm(spoken)
    if not h or not s:
        return 0.0
    if h in s:
        return 1.0
    hw, sw = h.split(), s.split()
    best = 0.0
    for n in {max(1, len(hw) - 1), len(hw), len(hw) + 1}:
        for i in range(0, max(1, len(sw) - n + 1)):
            best = max(best, SequenceMatcher(None, h, " ".join(sw[i:i + n])).ratio())
    return round(best, 3)


# HER NAME IN A BARGE-IN, as the ear writes it (round 14, .101 2026-10-12: "hey Chris, stop!" over her reading). The
# bridge's wake matcher (wake.py CRISSA_HEARD, measured) leaves "Chris" out on purpose - ordinary speech must not wake
# her - but over her own line a vocative "Chris" is her name: the ear wrote her name that way when the User stopped her.
BARGE_NAMES = ("crissa", "chrissa", "chrisa", "krisa", "prisa", "chrysa", "chrissar", "prissa", "kressa", "grissa",
               "crisa", "krissa", "christa", "clarissa", "chris", "kris", "crissa", "zoe", "zoie",
               # round 16 (.101 2026-10-13: "Stop there, Krista." dropped as her echo) and round 15's over-line spellings
               "krista", "kreza", "tressa")
# a person stopping or holding her (the bridge's wake.stop_only words): never her echo unless she is saying them
STOP_WORDS = ("stop", "wait", "hang on", "hold on", "hold up", "pause", "enough", "one second", "one sec", "a second",
              "one moment", "a moment", "quiet", "hush", "shush", "cancel", "never mind", "wansan", "wonsan")
_VOC = ("hey", "hi", "ok", "okay", "yo")
_HER = r"(?:" + "|".join(BARGE_NAMES) + r")"
_VOCATIVE = re.compile(r"\b(?:" + "|".join(_VOC) + r")\s+" + _HER + r"\b|^" + _HER + r"\b|\b" + _HER + r"$")
_KEEP = frozenset(BARGE_NAMES) | frozenset(_VOC)


def _calls_her(text: str) -> bool:
    return bool(_VOCATIVE.search(_norm(text)))


def _stops_her(text: str, spoken: str) -> bool:
    """A stop or hold word in `text` that her own stretch does not hold (round 16)."""
    if not FRESH:
        return False
    padded, her = " %s " % _norm(text), " %s " % _norm(spoken)
    return any(" %s " % w in padded and " %s " % w not in her for w in STOP_WORDS)


def residue(heard: str, spoken: str) -> tuple[str, int]:
    """(`heard` without her words, how many words were taken out). Her words are any run of two or more of the heard
    words that appears, in that order, anywhere in her line (the ASR repeats and reorders her clauses: "Yeah, the User.
    Yeah, the User. Got it."), longest run first. Her name and the vocative words are never taken out."""
    toks = re.findall(r"\S+", heard or "")
    pairs = [(i, w) for i, t in enumerate(toks) for w in _norm(t).split()]
    words = [w for _, w in pairs]
    sw = _norm(spoken).split()
    grams: dict[int, set] = {}
    for n in range(2, 9):
        grams[n] = {tuple(sw[k:k + n]) for k in range(0, max(0, len(sw) - n + 1))}
    gone: set[int] = set()
    hers: set[int] = set()     # inside a run of three or more of her words: her own name in her own sentence (round 16)
    i = 0
    while i < len(words):
        hit = 0
        for n in range(min(8, len(words) - i), 1, -1):
            if tuple(words[i:i + n]) in grams.get(n, ()):
                hit = n
                break
        if hit:
            gone.update(range(i, i + hit))
            if FRESH and hit >= 3:
                hers.update(range(i, i + hit))
            i += hit
        else:
            i += 1
    kept_tok = sorted({pairs[k][0] for k in range(len(pairs))
                       if k not in gone or (words[k] in _KEEP and k not in hers)})
    return " ".join(toks[t] for t in kept_tok).strip(), len(gone)


def _person_speaking(stamp: dict | None) -> bool:
    """A named voice at PERSON_SIM or more: a person, not her echo."""
    st = stamp if isinstance(stamp, dict) else {}
    if not str(st.get("person_id") or "").strip():
        return False
    if not str(st.get("source") or "").startswith("voice_emb"):
        return False
    for k in ("voice_emb_sim", "sim", "top_sim"):
        v = _f(st.get(k))
        if v is not None:
            return v >= PERSON_SIM
    return False


def _best_person_score(stamp: dict | None) -> float | None:
    """The best voice score the ear gave any enrolled person for this chunk, or None when it gave none."""
    st = stamp if isinstance(stamp, dict) else {}
    got = [v for v in (_f(st.get(k)) for k in ("voice_emb_sim", "sim", "user_sim", "best_other_sim", "top_sim"))
           if v is not None]
    return max(got) if got else None


def subtract(text: str, start_ts: float, end_ts: float, *, stamp: dict | None = None, now: float | None = None,
             lines: Lines | None = None, barge: bool = False) -> dict[str, Any]:
    """What to do with an utterance heard while one of her lines was on the air (round 14: never a whole chunk because
    it contains her words). {"action": "keep"} - nothing of hers in it, or she was not on the air; {"action": "drop",
    "rule": "said_it_herself", ...} - her words and nothing else; {"action": "rewrite", "text": <his words>, ...} - her
    words taken out, the rest is the person's turn."""
    if not enabled():
        return {"action": "keep"}
    words = _norm(text).split()
    if not words:
        return {"action": "keep"}
    L = lines or LINES
    now = time.time() if now is None else float(now)
    if lines is None:
        L.refresh(now)
    spoken = L.during(start_ts, end_ts, now=now)
    if not spoken:
        return {"action": "keep"}
    ratio = text_match(text, spoken)
    if barge:
        # THE UTTERANCE THAT CUT HER (round 17, barge.py): a look already heard someone's words in it, so only her own
        # runs of words are taken out here; what is left is theirs, however much it reads like her line (measured
        # 2026-10-13: "Can you say that again?" over her reading was dropped whole here as "words like her line", and the
        # cut became a false one). Nothing left is no turn: her echo or a noise (the bridge goes on).
        kept, n_gone = residue(text, spoken)
        if not _norm(kept).split():
            return {"action": "drop", "rule": "said_it_herself", "match": ratio, "removed": n_gone, "barge": True,
                    "spoken": spoken[:160]}
        if n_gone:
            return {"action": "rewrite", "text": kept, "removed": n_gone, "match": ratio, "named": False, "why": "barge"}
        return {"action": "keep"}
    kept, n_gone = residue(text, spoken)
    rest = [w for w in _norm(kept).split()]
    named = _calls_her(kept)
    stops = _stops_her(kept, spoken)
    if not n_gone:
        # nothing of hers: one word said just after her line is a person (round 13); a short echo the ASR bent beyond
        # any two-word run of her line is judged on the whole (round 13's rule) unless it names her or is a person's;
        # round 16: three words or fewer need MATCH_SHORT, and a stop word she was not saying is a person's
        need = MATCH_SHORT if (FRESH and len(words) <= SHORT_WORDS) else MATCH
        if (ratio >= need and len(words) >= 2 and not named and not stops and not _person_speaking(stamp)
                and L.during(start_ts, start_ts, now=now, tail_s=0.0)):
            return {"action": "drop", "rule": "said_it_herself", "match": ratio, "spoken": spoken[:160]}
        return {"action": "keep"}
    own = [w for w in rest if w not in _KEEP]
    if named or stops:                          # her name, or a stop she was not saying: the person's (a barge-in)
        return {"action": "rewrite", "text": kept, "removed": n_gone, "match": ratio, "named": bool(named),
                **({"stops": True} if stops else {})}
    # What is left is either the person's words or her own words the ear bent ("fung jam. Wash my lipstick" for "plum
    # jam. Watch my lips"). A person's voice in the chunk (the best enrolled score: .101's overlapping turns of the User
    # 0.43-0.68, her echo's median 0.16 - round 13 CPU) keeps it; without one, words that read like her line are hers.
    bent = text_match(kept, spoken)
    voice = _best_person_score(stamp)
    if own and voice is not None and voice >= PERSON_OVER_HER:
        why = "a person's voice"
    elif len(own) >= RESIDUE_MIN_WORDS and bent < RESIDUE_ECHO:
        why = "words unlike her line"
    else:
        return {"action": "drop", "rule": "said_it_herself", "match": ratio, "removed": n_gone, "left": len(own),
                "left_like_her": bent, "voice": voice, "spoken": spoken[:160]}
    return {"action": "rewrite", "text": kept, "removed": n_gone, "match": ratio, "named": False, "why": why,
            "left_like_her": bent, "voice": voice}


def check(text: str, start_ts: float, end_ts: float, *, stamp: dict | None = None, now: float | None = None,
          lines: Lines | None = None) -> dict[str, Any] | None:
    """Round 13's question, kept for its callers: {"rule": "said_it_herself", ...} when the WHOLE utterance is her own
    line heard back (subtract says drop), else None."""
    got = subtract(text, start_ts, end_ts, stamp=stamp, now=now, lines=lines)
    if got.get("action") != "drop":
        return None
    return {k: v for k, v in got.items() if k != "action"}


def record(row: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **row}) + "\n")
    except OSError:
        pass


def shown_text(text: str, *, on_air: bool, user_voice: bool = False) -> str:
    """Her name, where it addresses her, written "Crissa" in what the ear logs and shows (round 15 (f); the rule and
    the spellings are the bridge's: crissa_bridge.wake.address_name). Unchanged when the bridge is not beside the ear."""
    try:
        from crissa_bridge.wake import address_name
    except Exception:  # noqa: BLE001
        return text
    try:
        return address_name(text, on_air=on_air, user_voice=user_voice)
    except TypeError:     # a bridge from before round 16
        return address_name(text, on_air=on_air)
    except Exception:  # noqa: BLE001
        return text
