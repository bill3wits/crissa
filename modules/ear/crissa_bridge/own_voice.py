"""own_voice: Crissa must not answer herself (the User 2026-09-30).

09:15-09:16 she heard three of her own replies as a guest ("There we go, love." / "You welcome love." /
"I don't know that name, love, as."), answered each, and heard the answers. rail-stt's fingerprint veto
(crissa_her_tts bank) names her voice when it gets an embedding - and a short line gets none (`no_emb`), so the
turn arrives with `tts_veto: null`, `person_id: null`, and the bridge treats it as a guest in the room.

Three rules, in order. The first that fires names the verdict:
  1. said_it_herself   the utterance was heard inside her speaking window (+ TAIL_S) and its text matches
                       what she said in that window - whatever its length.
  2. voice_is_crissa     rail-stt stamped the house voice (tts veto, or a TTS person id). Always rejected,
                       mouth idle or not.
  3. guest_unsure      nobody is identified (a guest) and the voice is not CONFIDENTLY not Crissa: either
                       there is no embedding at all, or the raw fingerprint similarity (`tts_sim`, when
                       rail-stt stamps it) is not under NOT_CRISSA_SIM.

`EAR_BRIDGE_OWN_VOICE` = report (default: log `own_voice` with what WOULD be dropped, drop nothing) |
drop (armed) | off. Armed is PER RULE: `EAR_BRIDGE_OWN_VOICE_RULES` names the rules that drop (default
`said_it_herself,voice_is_crissa` - the User 2026-09-30: "Arm rules 1 and 2, and keep rule 3 report-only until
rail-stt stamps raw similarity"); every other rule keeps reporting.

One exception survives rule 2, because it was measured: a WAKE VOCATIVE while her mouth is idle is him, not
her (2026-09-09 06:24, "Hey Zoe, can you hear me?" scored 0.51 on her fingerprint with nothing on air). A
veto-stamped turn that is a wake phrase, heard with her mouth idle, passes; any other veto-stamped turn is
rejected whatever her mouth is doing.

ROUND 13 (the User 2026-10-12, .101: "Now two voices are talking to one another"). In a pack this module ran in
`report` mode, so nothing was ever dropped, and its say log held only the bridge's own replies: her test Say, her
error line and any other house-speak line were unknown to it. Now:
  - the default is `drop` (this module runs only in a pack; the house's bridge is its own program);
  - every line she says is learned from the gate files (house-speak's `house-tts-gate.json` with its air window, and
    the bridge's `tts-gate.json`), not only the bridge's replies;
  4. on_air_not_user  the utterance was spoken (the ear's speech window, `speech_start_ts`/`speech_end_ts`) at least
                      half inside one of her lines on the air (to ON_AIR_TAIL_S after it), it is not a wake phrase
                      (a barge-in, "Hey Crissa, stop", always passes) and the voice is not the User's this turn.
                      Armed by default in a pack. A session (`session_held`) never skips this module: it runs before
                      the turn touches any session state.
Kill the new rule alone: EAR_BRIDGE_OWN_VOICE_RULES without on_air_not_user; the old report mode:
EAR_BRIDGE_OWN_VOICE=report.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from difflib import SequenceMatcher

MODE = os.environ.get("EAR_BRIDGE_OWN_VOICE", "drop").strip().lower()
TAIL_S = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_TAIL_S", "1.5"))
MATCH = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_MATCH", "0.75"))
NOT_CRISSA_SIM = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_NOT_CRISSA_SIM", "0.35"))
RUN_GAP_S = 2.5          # clauses closer than this are one speaking run (first clause, then the remainder)
DECODE_SLACK_S = 1.0     # a turn's `ts` is when the decode finished, a little after the audio ended
TTS_IDS = {"crissa", "crissa_her_tts", "crissa-her-tts", "tts_echo", "crew_tts", "announcer"}
RULES = ("said_it_herself", "voice_is_crissa", "guest_unsure", "on_air_not_user")
ON_AIR_TAIL_S = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_ON_AIR_TAIL_S", "0.6"))
ON_AIR_SHARE = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_ON_AIR_SHARE", "0.5"))
ON_AIR_LIKE = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_ON_AIR_LIKE", "0.6"))   # round 15: her words, not her air
ARMED = {r.strip() for r in os.environ.get("EAR_BRIDGE_OWN_VOICE_RULES",
                                          "said_it_herself,voice_is_crissa,on_air_not_user").split(",")
         if r.strip() in RULES}

# ONLY WHAT SHE HAD ON THE AIR IN THE LAST FEW SECONDS (round 16, .101 2026-10-13 00:40-00:43: "Stop." dropped by rule 4
# at air_share 1.0 / like_her 0.667, and the User's "Hang on." / "Thank you." taken for hers against a reading long
# stopped). A row learned from a gate file closed only on a write that still carried its words; a stop or a cancel
# writes none, so it stayed on the air for good, and the run rule pulled a whole reading into every comparison. Now a
# new line, a stop and a cancel close what was open; an open row never runs longer than its words at OPEN_CPS; a long
# line is compared only where she was in it; the run reaches RUN_REACH_S at most; three words or fewer need
# MATCH_SHORT; a stop word she was not saying is never hers. Kill: EAR_BRIDGE_OWN_VOICE_FRESH=0 (round 15's reference).
FRESH = os.environ.get("EAR_BRIDGE_OWN_VOICE_FRESH", "1").strip().lower() not in ("0", "false", "no", "off")
OPEN_CPS = 11.0
OPEN_PAD_S = 4.0
SAY_CPS = 15.0
SLICE_PAD_S = 2.5
WHOLE_CHARS = 160
# faster than any voice of hers: a line's "own pace" above this is a line that was cut short (round 17; 0 = off; off too
# with CRISSA_BARGE_FROM_ASR=0, so that switch is the way back to round 17 before the barge work)
MAX_CPS = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_MAX_CPS", "25") or 0)
RUN_REACH_S = 8.0
MATCH_SHORT = float(os.environ.get("EAR_BRIDGE_OWN_VOICE_MATCH_SHORT", "0.85"))
SHORT_WORDS = 3
STOP_WORDS = ("stop", "wait", "hang on", "hold on", "hold up", "pause", "enough", "one second", "one sec", "a second",
              "one moment", "a moment", "quiet", "hush", "shush", "cancel", "never mind", "wansan", "wonsan")
_CLOSE_NOTICES = ("interrupted", "cancelled", "canceled", "stopped", "stop")


def _end_of(r: dict, now: float) -> float:
    if r["end"] is not None:
        return float(r["end"])
    if not FRESH:
        return now
    return min(now, r["start"] + len(r["text"] or "") / OPEN_CPS + OPEN_PAD_S)


def _stretch(r: dict, t0: float, t1: float, end: float) -> str:
    """The part of a row she was saying between t0 and t1 (whole words, SLICE_PAD_S either side); `end` is the row's
    end, known when r["end"] is set (her pace in that line), else SAY_CPS."""
    text = r["text"] or ""
    if not FRESH or len(text) <= WHOLE_CHARS:
        return text
    dur = end - r["start"]
    cps = len(text) / dur if (r["end"] is not None and dur > 1.0) else SAY_CPS
    if MAX_CPS > 0 and cps > MAX_CPS and os.environ.get("CRISSA_BARGE_FROM_ASR", "1").strip().lower() not in (
            "0", "false", "no", "off"):
        # a line stopped part-way (a cut, a stop) ends early: its length over the time she spoke is no pace of hers
        # (round 17, 2026-10-13: a reading cut 10 s in read as 70 characters a second, and the stretch compared with
        # the person's turn was a part of her line she never reached, so her own words stayed in it)
        cps = SAY_CPS
    pad = SLICE_PAD_S + 0.15 * max(0.0, t1 - r["start"])
    a = int(max(0.0, (t0 - r["start"] - pad) * cps))
    b = int(min(len(text), (t1 - r["start"] + pad) * cps))
    if b <= a:
        return ""
    a = text.rfind(" ", 0, a) + 1 if a > 0 else 0
    nb = text.find(" ", b)
    return text[a:(len(text) if nb < 0 else nb)].strip()


def stops_her(text: str, spoken: str) -> bool:
    """A stop or hold word in `text` that her own stretch does not hold (round 16): a person stopping her."""
    if not FRESH:
        return False
    padded, her = " %s " % _norm(text), " %s " % _norm(spoken)
    return any(" %s " % w in padded and " %s " % w not in her for w in STOP_WORDS)


def _short_bar(text: str, bar: float) -> float:
    return max(bar, MATCH_SHORT) if FRESH and len(_norm(text).split()) <= SHORT_WORDS else bar


def mode() -> str:
    return MODE if MODE in ("report", "drop", "off") else "report"


def drops(rule: str | None) -> bool:
    """Does this verdict DROP the turn (armed mode and an armed rule), or only report it?"""
    return bool(rule) and mode() == "drop" and rule in ARMED


def _norm(s: str) -> str:
    s = (s or "").lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


class SayLog:
    """What she said and when: one row per spoken clause, newest last."""

    def __init__(self, keep: int = 24):
        self._rows: deque = deque(maxlen=keep)
        self._lock = threading.Lock()

    def begin(self, text: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            if FRESH:
                self._close_open(now, "say")
            self._rows.append({"start": now, "end": None, "text": text or "", "src": "say"})

    def _close_open(self, at: float, src: str) -> None:
        """Close the open rows of one kind: a new bridge line ends the bridge's last, a new gate line the gates' last
        (a gate line may be a piece of a bridge reply still being said, so it never closes the bridge's own row)."""
        for r in self._rows:
            if r["end"] is None and r.get("src", "say") == src:
                r["end"] = max(r["start"], float(at))

    def close_all(self, at: float | None = None) -> None:
        """A stop or a cancel: nothing of hers is on the air after `at` (round 16)."""
        at = time.time() if at is None else float(at)
        with self._lock:
            for r in self._rows:
                if r["start"] <= at and (r["end"] is None or r["end"] > at):
                    r["end"] = max(r["start"], at)

    def end(self, now: float | None = None) -> None:
        with self._lock:
            for r in reversed(self._rows):
                if r["end"] is None:
                    r["end"] = now or time.time()
                    break

    def add(self, start: float, end: float, text: str) -> None:
        with self._lock:
            self._rows.append({"start": start, "end": end, "text": text or ""})

    def rows(self) -> list[dict]:
        """Copies of the rows, oldest first (the cancel record and the over-line ask read where she is)."""
        with self._lock:
            return [dict(r) for r in self._rows]

    def note(self, start: float, end: float | None, text: str) -> None:
        """A line learned from a gate file: added once (a line already here, same words and start, is widened)."""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            for r in self._rows:
                if (r["text"] == text or text.startswith(r["text"]) or r["text"].startswith(text)) \
                        and (abs(r["start"] - start) < 3.0 or (FRESH and r["end"] is None and r["text"] == text)):
                    r["start"] = min(r["start"], start)
                    if len(text) > len(r["text"]):
                        r["text"] = text
                    if end is not None and (r["end"] is None or end > r["end"]):
                        r["end"] = end
                    return
            if FRESH:
                self._close_open(start, "gate")    # a new gate line: the gates' open line has ended
            self._rows.append({"start": float(start), "end": end, "text": text, "src": "gate"})

    def sync_gates(self, paths, now: float | None = None) -> None:
        """Learn every line on the air from the gate files: house-speak's (air_at / air_until) and the bridge's
        (speaking, updated_ts). Unreadable files are skipped."""
        import json as _json
        now = now or time.time()
        for p in paths or ():
            try:
                g = _json.loads(open(p, encoding="utf-8").read())
            except (OSError, ValueError):
                continue
            if not isinstance(g, dict):
                continue
            text = str(g.get("last_reply") or "").strip()
            try:
                updated = float(g.get("updated_ts") or 0.0)
                air_at = float(g["air_at"]) if g.get("air_at") else None
                air_until = float(g["air_until"]) if g.get("air_until") else None
            except (TypeError, ValueError):
                continue
            speaking = bool(g.get("speaking"))
            if FRESH and not speaking and (str(g.get("notice") or "").strip().lower() in _CLOSE_NOTICES or not text) \
                    and (air_until or updated):
                self.close_all(air_until or updated)          # stopped / cancelled: off the air
            if not text:
                continue
            if air_at:
                start, end = air_at, (air_until if air_until else (None if speaking else updated or None))
            else:
                end = None if speaking else updated
                start = updated if speaking else updated - max(2.0, len(text) / 13.0)
            if end is not None and now - end > 120:
                continue
            self.note(start, end, text)

    def air_share(self, t0: float, t1: float, *, tail_s: float = ON_AIR_TAIL_S, now: float | None = None) -> float:
        """The share of [t0, t1] that lies inside one of her lines on the air (to tail_s after it): 0..1."""
        now = now or time.time()
        if t1 <= t0:
            return 0.0
        with self._lock:
            rows = [dict(r) for r in self._rows]
        best = 0.0
        for r in rows:
            a0 = r["start"] - 0.3
            a1 = _end_of(r, now) + tail_s
            ov = min(t1, a1) - max(t0, a0)
            if ov > 0:
                best = max(best, ov / (t1 - t0))
        return round(min(1.0, best), 3)

    def spoken_during(self, t0: float, t1: float, *, tail_s: float = TAIL_S, now: float | None = None) -> str:
        """The text of every clause in a speaking run that overlaps [t0, t1] (each clause counted to its
        end + tail_s). A run is clauses less than RUN_GAP_S apart: she says the first clause, then the rest,
        and the ear hands the first clause back while the rest is on air."""
        now = now or time.time()
        with self._lock:
            rows = [dict(r) for r in self._rows]
        ends = [_end_of(r, now) for r in rows]
        hit = [i for i, r in enumerate(rows) if r["start"] <= t1 and ends[i] + tail_s >= t0]
        if not hit:
            return ""
        lo, hi = min(hit), max(hit)
        while lo > 0 and rows[lo]["start"] - ends[lo - 1] < RUN_GAP_S \
                and (not FRESH or t0 - ends[lo - 1] < RUN_REACH_S):
            lo -= 1
        while hi < len(rows) - 1 and rows[hi + 1]["start"] - ends[hi] < RUN_GAP_S \
                and (not FRESH or rows[hi + 1]["start"] - t1 < RUN_REACH_S):
            hi += 1
        out = [_stretch(rows[i], t0, t1, ends[i]) for i in range(lo, hi + 1)]
        return " ".join(t for t in out if t)


def text_match(heard: str, spoken: str) -> float:
    """How well `heard` matches some stretch of `spoken` (0..1): the best ratio of the heard words against
    every window of about as many spoken words. An ASR of her own voice drops and bends words
    ("You welcome love." for "You're welcome, love."), so this is fuzzy, and it is positional - a window,
    not a bag of words - so two common words scattered through a long reply do not match."""
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


# HER NAME IN A BARGE-IN (round 14, .101: "hey Chris, stop!" over her reading): the names the ear writes for her, "Chris"
# included over her own line; never taken out of a heard line with her words.
BARGE_NAMES = ("crissa", "chrissa", "chrisa", "krisa", "prisa", "chrysa", "chrissar", "prissa", "kressa", "grissa",
               "crisa", "krissa", "christa", "clarissa", "chris", "kris", "crissa", "zoe", "zoie")
_WAKEISH = frozenset(BARGE_NAMES) | {"hey", "hi", "ok", "okay", "yo"}


def strip_her_words(heard: str, spoken: str) -> str:
    """`heard` without her words: every run of two or more heard words that appears, in that order, anywhere in her
    line, longest first (the ASR repeats and reorders her clauses). Her name and the vocative words always stay. Round
    14: the same rule as the ear's (crissa_ear/self_echo.residue), so a chunk is never dropped for containing her words;
    the rest is the person's."""
    toks = re.findall(r"\S+", heard or "")
    pairs = [(i, w) for i, t in enumerate(toks) for w in _norm(t).split()]
    words = [w for _, w in pairs]
    sw = _norm(spoken).split()
    grams = {n: {tuple(sw[k:k + n]) for k in range(0, max(0, len(sw) - n + 1))} for n in range(2, 9)}
    gone: set = set()
    i = 0
    while i < len(words):
        hit = 0
        for n in range(min(8, len(words) - i), 1, -1):
            if tuple(words[i:i + n]) in grams.get(n, ()):
                hit = n
                break
        if hit:
            gone.update(range(i, i + hit))
            i += hit
        else:
            i += 1
    kept = sorted({pairs[k][0] for k in range(len(pairs)) if k not in gone or words[k] in _WAKEISH})
    return " ".join(toks[t] for t in kept).strip()


def _own_words(text: str, spoken: str) -> list:
    """The words of `text` that are not her name, a vocative word, or (one word alone) a word of her line."""
    own = [w for w in _norm(text).split() if w not in _WAKEISH]
    if len(own) == 1 and own[0] in set(_norm(spoken).split()):
        return []
    return own


def verdict(turn: dict, say: SayLog, *, guest: bool, now: float | None = None, idle_wake: bool = False,
            wake: bool = False, user_voice: bool = False) -> dict:
    """{"rule": None} = let it through. Otherwise the rule that would drop it, with its evidence.
    `idle_wake`: the turn is a wake vocative and her mouth is idle - rule 2's one measured exception.
    `wake`: the turn is a wake vocative at all (a barge-in passes rule 4); `user_voice`: this turn's voice is the
    User's (rule 4 never drops him)."""
    now = now or time.time()
    if guest:
        try:                                     # round 17: outside Security On nobody is a guest (id_gate), so rule 3
            from .id_gate import gating          # ("nobody is identified") stands down; the text rules stay
            guest = gating()
        except Exception:  # noqa: BLE001
            pass
    text = (turn.get("text") or "").strip()
    st = turn.get("stamp") if isinstance(turn.get("stamp"), dict) else {}
    t1 = float(turn.get("ts") or now)
    dur = float(turn.get("duration_s") or st.get("speech_duration_s") or 0.0)
    w0, w1 = t1 - dur - DECODE_SLACK_S, t1
    try:   # the ear's own speech window when it sent one (round 13): when it was SAID, not when it was decoded
        if turn.get("speech_start_ts") is not None and turn.get("speech_end_ts") is not None:
            w0, w1 = float(turn["speech_start_ts"]), float(turn["speech_end_ts"])
    except (TypeError, ValueError):
        pass
    spoken = say.spoken_during(w0, w1, now=now)
    if spoken:
        ratio = text_match(text, spoken)
        if ratio >= _short_bar(text, MATCH) and not stops_her(text, spoken):
            # round 14: never the whole chunk for containing her words - take hers out; what is left is the person's
            rest = strip_her_words(text, spoken)
            own = _own_words(rest, spoken)
            # what is left is the person's when it names her, his voice spoke it, or it reads unlike her line (the ear's
            # rule, crissa_ear/self_echo.subtract): her own words bent by the ear are still hers
            if wake or (own and user_voice) or (len(own) >= 4 and text_match(rest, spoken) < 0.5):
                return {"rule": None, "strip": rest, "match": ratio}
            return {"rule": "said_it_herself", "match": ratio, "spoken": spoken[:120]}
    pid = str(turn.get("person_id") or st.get("person_id") or "").strip().lower()
    if (pid in TTS_IDS or st.get("source") == "tts_echo_veto" or st.get("tts_veto")) and not idle_wake:
        sim = (st.get("tts_veto") or {}).get("tts_fp_sim") if isinstance(st.get("tts_veto"), dict) else None
        return {"rule": "voice_is_crissa", "pid": pid or None, "tts_sim": sim}
    try:
        s0 = float(turn.get("speech_start_ts")) if turn.get("speech_start_ts") is not None else None
        s1 = float(turn.get("speech_end_ts")) if turn.get("speech_end_ts") is not None else None
    except (TypeError, ValueError):
        s0 = s1 = None
    mixed = bool((turn.get("self_echo") or {}).get("kept") if isinstance(turn.get("self_echo"), dict) else False) \
        or bool(st.get("mixed_with_her"))
    # rule 4 never second-guesses a chunk the ear already took her words out of: what is left is the person's (round 14)
    if s0 is not None and s1 is not None and s1 > s0 and not wake and not user_voice and not mixed:
        share = say.air_share(s0, s1, now=now)
        # round 15 (.101 13:14:31: "Kreza Wansan." - his "Crissa, one second" - dropped at air_share 1.0 with the
        # canceller on): the share only says he spoke while she did; a chunk is taken for her voice only when its words
        # read like the line she was saying (ON_AIR_LIKE), never for being said over her
        her = say.spoken_during(s0, s1, now=now) if share >= ON_AIR_SHARE else ""
        like = text_match(text, her) if her else 0.0
        if share >= ON_AIR_SHARE and like >= _short_bar(text, ON_AIR_LIKE) and not stops_her(text, her):
            return {"rule": "on_air_not_user", "air_share": share, "like_her": like, "pid": pid or None}
    if guest:
        sim = st.get("tts_sim")
        if sim is not None:
            if float(sim) >= NOT_CRISSA_SIM:
                return {"rule": "guest_unsure", "why": "tts_sim", "tts_sim": float(sim)}
        elif st.get("is_user_reason") == "no_emb" or st.get("voice_emb_sim") is None:
            return {"rule": "guest_unsure", "why": "no_emb", "in_her_window": bool(spoken)}
    return {"rule": None}
