"""The first word over her line stops her, straight from the ASR (round 17, the User 2026-10-13).

The User: "for barge, can we send signal DIRECTLY from ASR on word penetration? She can always start up again. Also,
be sure barge streams to her. No check on voice signature before barge makes it more natural. Be sure not to interfere
with Auth but do allow the cutoff as close as is possible to the barge words." And: "people do not continue speaking
until they figure out who interrupted you know?"

WHAT IT REPLACES (.101 round 15, RESULTS-20261013-0055 item 7): a person's words over her line waited for the utterance to
close (~1.9 s, round 16 made a short one 0.6 s), its whole decode (~1.2 s), the bridge's poll and gather (0.2 + 1.2 s)
and then the over-line model call (~2 s) before anything stopped her: "5-10 s to get through".

HOW, while one of her conversation lines is on the air (the bridge's /health says `barge`; never Setup's lines, the
announcer's board lines or Lila's, which are not the bridge's):
  - from the first frame the VAD calls speech (before the utterance even opens), the ear takes LOOKS: the newest
    audio of that stretch of speech, from one frame before it (LEAD_FRAMES) and at most WINDOW_S long, decoded by the ASR's
    look lane (X-Crissa-Probe: 1, modules/voice/scripts/crissa-asr.py: never behind a whole decode, 24 tokens at most).
    One look at a time, always the newest audio; a look that is still running when the next frame lands is followed
    by the newest window, never by a queue.
  - the words a look heard are judged against the words she is saying right now (self_echo's fresh reference, round 16,
    8f0861390), never against a voice print (judge_words, below): her echo through the speakers, whatever the echo
    canceller left and however the ASR bent its edges, reads like her line and is hers; anything else is someone's
    word. A word that alone never makes a turn ("mm-hmm", "yeah": the bridge's session_skip_short list) does not cut
    her; the next look does when more words come (BACKCHANNEL).
  - the first STRONG look (a stop or a hold, her name, two words of someone's, one long one), or a WEAK one (one short
    word of someone's) right after another, POSTs the cut to the bridge (/v1/barge, phase "cut"): the bridge stops her
    line at once (abort_mouth: the cancel record keeps where she was) and keeps its session open for the turn.
  - a stretch over her line whose looks covered it all and heard only her is not decoded again whole (skip_echo_on).
  - the utterance that cut her is not a fragment: the ear keeps capturing it as an ordinary turn (no on-air 4 s cap, no
    on-air 0.6 s hangover from the cut on), its whole decode goes to the ring with `barge_id`, and the bridge answers it
    through its normal turn path at once. When it ends with no words (her echo, a noise: the whole decode had nothing
    left after the echo check) the ear says so (phase "end", turn false) and the bridge lets her go on by herself.

INTERRUPT WORDS ONLY (round 18, .101 2026-10-13 15:05-15:07: round 17 cut her falsely on "Chapter One." (him finishing
his own request), "Thank you much." and his KEYBOARD, written "I don't know."; the User: "Interrupt words only: barge-in
straight from the ASR's first words, with no voice check, but only on interrupt words (her name, stop, wait, hold on,
one second, pause...)", and then "Id like to talk with someone else while she speaks without interrupting her"). In a
pack a look cuts her only when its words hold one of the interrupt words (config/crissa-talk.json `barge.words`, the
list he approved) or her name in a vocative form (the ear's measured spellings, round 14-16), on word boundaries, and she
is not saying it herself; anything else said over her answer is SIDE TALK: no cut, never sent to her (the bridge,
crissa_bridge/barge.py Air). The first word of a two-word interrupt ("hold", "hang", "one", "never", "excuse") or a
greeting at a look's end only holds the on-air cap for the next look (strength 1, "prefix"), never a cut by itself.
Kill: CRISSA_BARGE_WORDS_ONLY=0 (round 17's any-word cut, below); the house never (its ear is rail-stt).

Who spoke is worked out after the cut, by the normal path: the cut grants, changes and uses no identity, and the turn's
own voice decides anything identity-gated, exactly as before (the bridge, crissa_bridge/barge.py).

Kill: CRISSA_BARGE_FROM_ASR=0 (round 17 before this: the whole utterance, the over-line ask). Knobs: CRISSA_BARGE_WINDOW_S
(1.2), CRISSA_BARGE_CONFIRM_S (1.0), CRISSA_BARGE_BACKCHANNEL=0 (any word cuts), CRISSA_BARGE_SKIP_ECHO=0 (her echo is
decoded whole as before), and the ASR's CRISSA_ASR_PROBE_LANE=0. Log: <home>/logs/barge.jsonl (each cut, refusal, end).
Round 18: CRISSA_BARGE_WORDS_ONLY=0 (any word cuts again), CRISSA_SIDE_TALK=0 (words over her answer go to the over-line
ask as before round 17), and the talk settings' `barge` section (words_only, words, name, names, side_talk).
"""
from __future__ import annotations

import functools
import json
import os
import re
import threading
import time
import urllib.request
from collections import deque
from difflib import SequenceMatcher
from typing import Any, Callable

import numpy as np

from pathlib import Path

from . import self_echo
from .home import HOME


def on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_BARGE_FROM_ASR", "1")).strip().lower() not in ("0", "false", "no", "off")


def skip_echo_on(env=None) -> bool:
    """SKIP_ECHO: an utterance over her line whose looks covered all of it and heard only her own words is not decoded
    again whole (measured 2026-10-13, ASR on one core: those decodes of her echo kept the core at 100 % while she spoke,
    and a look waiting beside one took 0.3-1.3 s instead of 0.2-0.4 s). Any word of anyone's, a gap between looks, a
    failed look: decoded whole, as before. Kill: CRISSA_BARGE_SKIP_ECHO=0."""
    e = os.environ if env is None else env
    return on(e) and str(e.get("CRISSA_BARGE_SKIP_ECHO", "1")).strip().lower() not in ("0", "false", "no", "off")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# A look's audio: from one frame before the first speech frame (the word's start, which the VAD may have called
# silence), the newest WINDOW_S at most. Measured 2026-10-13, one core (taskset, warm ARK-ASR INT8): 0.6 s 213 ms,
# 0.9 s 250-280 ms, 1.2 s 390 ms; a longer window costs more per look and adds nothing to the first word.
LEAD_FRAMES = 1
WINDOW_S = _f("CRISSA_BARGE_WINDOW_S", 1.2)
LOOK_TIMEOUT_S = 3.0
# THE ON-AIR CAP WAITS FOR A LOOK IN FLIGHT, up to CAP_HOLD_S (ear_loop): measured 2026-10-13, a stretch of her echo hit
# the 4 s cap while the look that heard "hang on" was still decoding, so the person's words were split across two
# utterances and the one that cut her came back with no words of theirs. 1.2 s is two looks on one core.
CAP_HOLD_S = _f("CRISSA_BARGE_CAP_HOLD_S", 1.2)
# ... and while the last look heard a word of someone's waiting for its confirming look, up to SOMEONE_HOLD_S more
# (measured 2026-10-13, ASR on one busy core: looks took 0.6-1.0 s, the 1.2 s hold ran out before the second look
# confirmed, and "Hold on a second" was chopped mid-word and lost). Her echo alone keeps the 4 s cap.
SOMEONE_HOLD_S = _f("CRISSA_BARGE_SOMEONE_HOLD_S", 4.0)
# THE UTTERANCE THAT CUT HER IS DECODED FROM WHERE SOMEONE WAS FIRST HEARD, less TRIM_LEAD_S (ear_loop): measured
# 2026-10-13 over her RAW echo, the whole decode of 5.7 s (3.7 s of her echo, then "Wait, go back a bit") came back as
# her words alone, and the cut became a false one. Kill: CRISSA_BARGE_TRIM=0 (the whole utterance).
TRIM_LEAD_S = 0.3


def trim_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_BARGE_TRIM", "1")).strip().lower() not in ("0", "false", "no", "off")
TELL_TIMEOUT_S = 0.5

# Words that alone never become a turn (the bridge's session_skip_short list, ear_bridge.main, and the fillers ah / oh):
# a listener's "mm-hmm" or "yeah" over her line is not an interruption, and the old path never stopped her for one.
_BACKCHANNEL = frozenset(("yeah", "yes", "yep", "yup", "uh", "um", "uhhuh", "ok", "okay", "mm", "mmm", "hmm", "mmhmm",
                          "mhm", "mhmm", "right", "sure", "k", "kay", "ah", "oh"))


def backchannel_words() -> frozenset:
    if str(os.environ.get("CRISSA_BARGE_BACKCHANNEL", "1")).strip().lower() in ("0", "false", "no", "off"):
        return frozenset()
    return _BACKCHANNEL


def _norm_words(text: str) -> list[str]:
    return self_echo._norm((text or "").replace("-", "")).split()


# ── is it hers? Words only, never a voice print ──────────────────────────────────────────────────────────────────────
# Measured 2026-10-13 on 610 looks at her echo alone (two lines, RAW room echo and what a canceller leaves, ARK-ASR on
# one core) and 24 barges over it: the ASR bends the edges of her words in a short window ("With class." for "with
# clouds", "Whenever it is a day." for "...it is a damp", "Nevi, I." for "November in my"), so round 16's whole-line
# match (self_echo.subtract's rules, with no voice print) took 18 of those 610 looks (3 %) for someone's words, and 10
# of 696 later ones: several cuts a minute of her talking. The rule here cut her on none of them and cut all 24 barges:
#   - a heard word is hers when it is close to a word of the stretch she was saying (equal; the start of her word, three
#     letters or more - a window's edge cut it; a ratio of 0.8, 0.7 from six letters; four letters in a row covering
#     60 %; the same consonant skeleton; or the same first three letters at a ratio of 0.5), or when it sits right after
#     (before) one of her content words or her own runs of two and loosely matches her NEXT (previous) word - the bent
#     word at a window's edge;
#   - STRONG (cut now): her name calling her (a name she was not saying), a stop or a hold word she was not saying (a
#     one-word stop the alignment gives her, "no" from "No-vember", is hers), a word asking for the floor she was not
#     saying ("excuse me", "sorry", "pardon", "hey", "hello"), or two or more of someone's content words outnumbering hers;
#   - WEAK (one word of someone's): cuts only when the look before it in the same stretch of speech, at most CONFIRM_S
#     earlier, was someone's too and this one has more to it (a new word, or more words) - one look later (0.25-0.4 s
#     on one core). Replayed on 2,053 looks of her echo alone (610 tuning, the rest live and held out) and 117 barge
#     sequences: no cut on her echo. A person who reuses her words ("skip the garlic" over her garlic recipe) is not cut
#     by a look; the whole turn cuts her when it is decoded (ear_bridge._barge_turn_cut).
# Function words, backchannel words and words of fewer than three letters never cut on their own.
FUNC = frozenset("""a an the and or but so to of in on at for with from by is it its i im me my we you your he she they
them his her this that these those was were be been are am as if not do does did have has had can could would will just
all there then than up out off into over about very really like well""".split())
STOP_WORDS = tuple(self_echo.STOP_WORDS) + ("no", "abort")
# a person asking for the floor (the held-out set, 2026-10-13: "Excuse me, can I ask something?" was one word to the rule)
ATTENTION = ("excuse", "pardon", "sorry", "hey", "hello")
CONFIRM_S = _f("CRISSA_BARGE_CONFIRM_S", 1.0)
NEWEST_TAIL = 400            # characters of her newest line that stand in when no stretch of hers matches a window


# ── round 18: interrupt words only (the User 2026-10-13) ──────────────────────────────────────────────────────────────
# THE LIST HE APPROVED (2026-10-13): her name and its near-names, stop, wait, hold on, hang on, one second / one sec,
# pause, quiet, enough, cancel, never mind, excuse me; left out: okay, yes, no, sorry (every reply and reading has
# them, and "no"/"okay" are what the ASR writes for a cough or a key). It lives in config/crissa-talk.json `barge.words`
# (an install's <home>/talk.json may change it); this copy stands in when neither file can be read.
DEFAULT_WORDS = ("stop", "wait", "hold on", "hang on", "one second", "one sec", "pause", "quiet", "enough", "cancel",
                 "never mind", "excuse me")
# How the ear has written those words, measured on .101, counted as the word when the word is on the list: "Kreza
# Wansan." for "Crissa, one second" (round 15, 2026-10-12), and "nevermind" (one word) for "never mind".
ASR_SPELLINGS = {"one second": ("wansan", "wonsan"), "never mind": ("nevermind",)}
# her old name is not her name in a pack (the wake dropped it 2026-10-10); a person called Zoe may be in the room
# her old name, spelled so the kit's rename (Crissa -> Crissa) leaves it alone: written plainly, the kit read
# ("crissa", "zoe", "zoie") and her own name was dropped from the barge names (round 35, the build.sh rehearsal's CI)
OLD_NAMES = frozenset(("zo" + "ey", "zoe", "zoie"))
# heard for her name only right after a greeting (wake.HEY_ONLY "Hey, Prison.", wake.BARGE_FORM_ONLY "Hey Christmas"):
# alone they are the words
GREETING_ONLY_NAMES = ("prison", "christmas")
GREETINGS = ("hey", "hi", "ok", "okay", "yo")
BARGE_DEFAULTS = {"words_only": True, "words": list(DEFAULT_WORDS), "name": True, "names": [], "side_talk": True}
PACK_ROOT = Path(__file__).resolve().parents[3]      # modules/ear/crissa_ear -> the pack (or the source tree)
_CFG: dict = {"key": None, "v": None}
_CFG_LOCK = threading.Lock()


def _off(raw) -> bool:
    return str(raw).strip().lower() in ("0", "false", "no", "off")


def _talk_files() -> tuple:
    return (PACK_ROOT / "config" / "crissa-talk.json", HOME / "talk.json")


def _read_barge_section() -> dict:
    """config/crissa-talk.json `barge` under <state home>/talk.json, read the way lib/crissa_talk layers them (that
    module when it is importable, else the two files)."""
    try:
        import sys
        lib = str(PACK_ROOT / "lib")
        if lib not in sys.path:
            sys.path.append(lib)
        import crissa_talk                           # noqa: E402
        b = crissa_talk.load(home=HOME, env={}).get("barge")
        if isinstance(b, dict):
            return dict(b)
    except Exception:  # noqa: BLE001 - the files themselves below
        pass
    out: dict = {}
    for p in _talk_files():
        try:
            sec = json.loads(p.read_text(encoding="utf-8")).get("barge")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(sec, dict):
            out.update({k: v for k, v in sec.items() if k in BARGE_DEFAULTS and v is not None})
    return out


def barge_settings() -> dict:
    """{words_only, words, name, names, side_talk}: the talk settings' `barge` (re-read when either file changes)."""
    key = []
    for p in _talk_files():
        try:
            st = p.stat()
            key.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            key.append((str(p), None, None))
    key = tuple(key)
    with _CFG_LOCK:
        if _CFG["key"] == key and _CFG["v"] is not None:
            return dict(_CFG["v"])
    v = dict(BARGE_DEFAULTS)
    v.update({k: x for k, x in _read_barge_section().items() if k in BARGE_DEFAULTS})
    with _CFG_LOCK:
        _CFG.update(key=key, v=dict(v))
    return v


def reset_settings() -> None:
    with _CFG_LOCK:
        _CFG.update(key=None, v=None)


def _is_house() -> bool:
    try:
        from .turn_end import is_house
        return bool(is_house())
    except Exception:  # noqa: BLE001 - no state-home module: not the house
        return False


def words_only_on(env=None) -> bool:
    """Round 18: only an interrupt word or her name cuts her (a pack, the ASR barge on, `barge.words_only`).
    CRISSA_BARGE_WORDS_ONLY=0 is round 17's any-word cut; =1 turns it on over the setting. Never the house."""
    e = os.environ if env is None else env
    if not on(e) or _is_house():
        return False
    raw = e.get("CRISSA_BARGE_WORDS_ONLY")
    if raw is not None and str(raw).strip():
        return not _off(raw)
    return bool(barge_settings().get("words_only", True))


def side_talk_on(env=None) -> bool:
    """Round 18: what is said over her answer without an interrupt word is side talk (the bridge's Air): a pack with
    words only on and `barge.side_talk`. CRISSA_SIDE_TALK=0 sends it to the over-line ask as before round 17."""
    e = os.environ if env is None else env
    if not words_only_on(e):
        return False
    raw = e.get("CRISSA_SIDE_TALK")
    if raw is not None and str(raw).strip():
        return not _off(raw)
    return bool(barge_settings().get("side_talk", True))


def _phrase(w: str) -> str:
    return " ".join(_norm_words(w))


def interrupt_words(cfg: dict | None = None) -> list[tuple[str, str]]:
    """[(as heard, the listed word)], longest first: the list and the ASR's measured spellings of its words."""
    cfg = barge_settings() if cfg is None else cfg
    listed = [_phrase(w) for w in (cfg.get("words") or ()) if isinstance(w, str) and _phrase(w)]
    out = [(w, w) for w in listed]
    for w in listed:
        out += [(sp, w) for sp in ASR_SPELLINGS.get(w, ())]
    return sorted(dict.fromkeys(out), key=lambda x: -len(x[0]))


def her_names(cfg: dict | None = None) -> tuple[str, ...]:
    """Her name (crissa_home.her_name) and the spellings the ear has written for it: the wake's measured ones
    (wake.CRISSA_HEARD, 352 utterances) and the barge's near-names (self_echo.BARGE_NAMES: "Chris", "Krista", ...
    rounds 14-16 on .101), without her old name; plus the setting's own `names`."""
    cfg = barge_settings() if cfg is None else cfg
    names: list[str] = []
    try:
        from .turn_end import _zh
        names.append(_phrase(_zh().her_name()))
    except Exception:  # noqa: BLE001
        pass
    try:
        from crissa_bridge.wake import CRISSA_HEARD
        names += list(CRISSA_HEARD)
    except Exception:  # noqa: BLE001 - the ear without the bridge beside it
        names.append("crissa")
    names += [n for n in self_echo.BARGE_NAMES if n not in OLD_NAMES]
    names += [_phrase(n) for n in (cfg.get("names") or ()) if isinstance(n, str)]
    return tuple(n for n in dict.fromkeys(names) if n and n not in OLD_NAMES and n not in GREETING_ONLY_NAMES)


def _hers_word(w: str, her: list[str]) -> bool:
    """An interrupt word is hers only when she is saying it, a word it begins (a window's edge cut "waiting" to
    "wait", "holding" to "hold"), or a word that begins the same way and is close to it (the fixture's RAW echo bent her
    "involuntarily pausing" to "voluntarily pause"). Not hers_flags' loose likeness alone: measured 2026-10-13, that gave
    "cancel" to her "chance" (four letters in a row) and kept a real "Cancel that." from cutting."""
    return any(w == h or h.startswith(w) or (w[:3] == h[:3] and _close(w, h)) for h in her)


@functools.lru_cache(maxsize=8)
def _names_re(names: tuple[str, ...]):
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    greet = "|".join(GREETINGS)
    form = "|".join(GREETING_ONLY_NAMES)
    # the vocative forms of round 16 (self_echo._VOCATIVE): a greeting before it, first, or last in what was heard
    return re.compile(r"\b(?:%s)\s+(?:%s|%s)\b|^(?:%s)\b|\b(?:%s)$" % (greet, alt, form, alt, alt))


def judge_interrupt(words: list[str], heard: str, her: list[str], flags: list[bool] | None,
                    out: dict) -> dict[str, Any]:
    """Round 18's verdict on a look: 2 (cut) for an interrupt word or her name she is not saying, 1 ("prefix") for the
    first word of a two-word one or a greeting at the look's end (it holds the on-air cap for the next look, never a
    cut), else 0 - whatever else is in it ("side talk": the bridge keeps it from her)."""
    cfg = barge_settings()
    flags = flags if flags is not None else [False] * len(words)
    hers_padded = " %s " % " ".join(her)
    hset = set(her)
    norm = " ".join(words)
    if cfg.get("name", True):
        names = her_names(cfg)
        if names and _names_re(names).search(norm):
            own = [w for w in words if w in names or w in GREETING_ONLY_NAMES]
            if not any(w in hset for w in own if w not in GREETINGS):
                return {**out, "strength": 2, "why": "named", "own": own}
    padded = " %s " % norm
    for heard_as, listed in interrupt_words(cfg):
        if " %s " % heard_as not in padded or " %s " % heard_as in hers_padded:
            continue
        n = len(heard_as.split())
        for i in range(len(words) - n + 1):
            if " ".join(words[i:i + n]) == heard_as and not all(_hers_word(w, her) for w in words[i:i + n]):
                return {**out, "strength": 2, "why": "stop", "own": [listed]}
    if words and not flags[-1]:
        last = words[-1]
        firsts = {p.split()[0] for p, _l in interrupt_words(cfg) if " " in p}
        if last in firsts or (cfg.get("name", True) and last in GREETINGS):
            return {**out, "strength": 1, "why": "prefix", "own": [last]}
    alien = int(out.get("alien") or 0)
    # .101 round 28 (the User 2026-10-17: "build the three word barge rights ... Pause only to resume when quiet if was
    # not to her"): three or more words of someone's (not hers, not a listener's "mm-hmm") cut her like an interrupt
    # word; the bridge then asks her model whether they were to her, and she goes on by herself on quiet when they were
    # not. Fewer stay side talk. CRISSA_BARGE_WORDS_TO_CUT (3; 0 = round 18's interrupt words only).
    n_cut = int(_f("CRISSA_BARGE_WORDS_TO_CUT", 3))
    if n_cut > 0:
        bc = backchannel_words()
        theirs = [w for w, f in zip(words, flags) if not f and w not in bc]
        if len(theirs) >= n_cut:
            return {**out, "strength": 2, "why": "three_words", "own": theirs}
    return {**out, "strength": 0, "why": "side_talk" if alien else "her_words", "own": []}


def _canon(w: str) -> str:
    w = re.sub(r"^c(?=[eiy])", "s", w)
    w = re.sub(r"^k", "c", w)
    return re.sub(r"^ph", "f", w)


def _skel(w: str) -> str:
    w = re.sub(r"[^a-z]", "", _canon(w))
    if not w:
        return ""
    sub = {"c": "k", "q": "k", "g": "k", "z": "s", "v": "f", "d": "t", "b": "p"}
    out = w[0]
    for ch in w[1:]:
        if ch in "aeiouyhw":
            continue
        ch = sub.get(ch, ch)
        if out[-1:] != ch:
            out += ch
    return out


def _close(a: str, b: str) -> bool:
    if a == b or (len(a) >= 3 and b.startswith(a)):        # equal, or a window's edge cut her word ("Nov" of November)
        return True
    sm = SequenceMatcher(None, a, b)
    if sm.ratio() >= (0.7 if len(a) >= 6 else 0.8):
        return True
    if len(a) >= 4 and sm.find_longest_match(0, len(a), 0, len(b)).size >= max(4, int(0.6 * len(a) + 0.5)):
        return True
    sk = _skel(a)
    if len(sk) >= 3 and sk == _skel(b):
        return True
    return len(a) >= 4 and a[:3] == b[:3] and sm.ratio() >= 0.5      # "Dris" of drizzly


def _loose(a: str, b: str) -> bool:
    return _canon(a)[:1] == _canon(b)[:1] or SequenceMatcher(None, a, b).ratio() >= 0.5


def hers_flags(words: list[str], her: list[str]) -> list[bool]:
    """For each heard word: is it one of hers (close to a word of her stretch, or the bent neighbour of one)? A word
    stands where the words of hers it matched stand, so a bent anchor ("Dreisley" for drizzly) still places the word
    after it ("no" of No-vember)."""
    where = [[j for j, h in enumerate(her) if _close(w, h)] for w in words]
    flags = [bool(m) for m in where]
    pairs = {(her[k], her[k + 1]) for k in range(len(her) - 1)}
    anchor = [f and (w not in FUNC or (i > 0 and (words[i - 1], w) in pairs)
                     or (i + 1 < len(words) and (w, words[i + 1]) in pairs))
              for i, (w, f) in enumerate(zip(words, flags))]
    for _ in range(2):
        for i, w in enumerate(words):
            if flags[i]:
                continue
            if i > 0 and anchor[i - 1]:
                nxt = [j + 1 for j in where[i - 1] if j + 1 < len(her) and _loose(w, her[j + 1])]
                if nxt:
                    flags[i] = anchor[i] = True
                    where[i] = nxt
                    continue
            if i + 1 < len(words) and anchor[i + 1]:
                prv = [j - 1 for j in where[i + 1] if j > 0 and _loose(w, her[j - 1])]
                if prv:
                    flags[i] = anchor[i] = True
                    where[i] = prv
    return flags


def judge_words(heard: str, spoken: str) -> dict[str, Any]:
    """How strongly a look's words are someone's over her stretch `spoken`: {"strength": 0 | 1 | 2, "why", "words",
    "own"} (0 hers or nothing, 1 weak, 2 strong; the rules above)."""
    words = _norm_words(heard)
    if not words:
        return {"strength": 0, "why": "no_words", "words": []}
    if words_only_on():
        # round 18: only an interrupt word or her name cuts; `alien` still counts every word not hers, so a look that
        # heard side talk keeps its whole decode (hers_through) and its words reach the log
        her = _norm_words(spoken) if spoken else []
        flags = hers_flags(words, her) if her else [False] * len(words)
        out = {"words": words, "alien": sum(1 for w, f in zip(words, flags) if not f and len(w) >= 3),
               "rule": "words_only"}
        return judge_interrupt(words, heard, her, flags, out)
    bc = backchannel_words()
    if not spoken:
        own = [w for w in words if w not in bc]
        return {"strength": 2 if own else 0, "why": "words" if own else "backchannel", "words": words, "own": own,
                "alien": sum(1 for w in words if len(w) >= 3)}
    her = _norm_words(spoken)
    hset = set(her)
    flags = hers_flags(words, her)
    # words of three letters or more that are not hers, whatever they are (function, listener's): her echo has none
    out: dict[str, Any] = {"words": words, "alien": sum(1 for w, f in zip(words, flags) if not f and len(w) >= 3)}
    if self_echo._calls_her(heard) and not any(w in hset for w in words if w in self_echo.BARGE_NAMES):
        return {**out, "strength": 2, "why": "named", "own": [w for w in words if w in self_echo.BARGE_NAMES]}
    padded, hers_padded = " %s " % " ".join(words), " %s " % " ".join(her)
    for st in STOP_WORDS:
        if " %s " % st in padded and " %s " % st not in hers_padded:
            if " " in st or not flags[words.index(st)]:
                return {**out, "strength": 2, "why": "stop", "own": [st]}
    for i, w in enumerate(words):
        if w in ATTENTION and w not in hset and not flags[i]:
            return {**out, "strength": 2, "why": "attention", "own": [w]}
    content = [(w, f) for w, f in zip(words, flags) if len(w) >= 3 and w not in FUNC and w not in bc]
    own = [w for w, f in content if not f]
    if not own or len(own) <= len(content) - len(own):
        return {**out, "strength": 0, "why": "her_words" if content else "backchannel", "own": own}
    if len(own) >= 2:
        return {**out, "strength": 2, "why": "words", "own": own}
    return {**out, "strength": 1, "why": "one_word", "own": own}


def words_over_her(text: str, t0: float, t1: float, *, lines: "self_echo.Lines | None" = None,
                   now: float | None = None) -> dict[str, Any]:
    """What a look heard, judged against what she was saying between t0 and t1 (self_echo.Lines: both gate files, the
    fresh reference of round 16): judge_words' verdict and her stretch; "cut" = strong. No voice print is read."""
    try:
        from .quality import is_hallucination, strip_foreign
        heard = strip_foreign(text or "")
        if is_hallucination(heard):
            return {"cut": False, "strength": 0, "why": "hallucination", "words": []}
    except Exception:  # noqa: BLE001 - the filters are a help
        heard = text or ""
    L = lines or self_echo.LINES
    now = time.time() if now is None else float(now)
    if lines is None:
        try:
            L.refresh(now)
        except Exception:  # noqa: BLE001 - no reference: every word is someone's
            pass
    spoken = L.during(t0, t1, now=now)
    if not spoken:
        # she is on the air (the bridge said so) but no stretch of hers matches the window: her pace slower than the open
        # row's cap (11 characters a second), or an ear running late. Measured 2026-10-13: with no reference every word
        # is someone's, and her own last sentence cut her. Her newest line's end stands in.
        spoken = L.newest(now)[-NEWEST_TAIL:]
    v = judge_words(heard, spoken)
    return {**v, "cut": v["strength"] >= 2, "her": spoken[:160]}


def asr_look(url: str, pcm: bytes, sr: int, timeout: float = LOOK_TIMEOUT_S) -> dict[str, Any]:
    """One look through the ASR's look lane: {"text", "ms", "error"?}."""
    t0 = time.monotonic()
    req = urllib.request.Request(url.rstrip("/") + "/stt", data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(int(sr)),
                                          "X-Crissa-Probe": "1", "Content-Length": str(len(pcm))})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001 - a look that fails is no cut; the whole decode still comes
        return {"text": "", "ms": round((time.monotonic() - t0) * 1000.0, 1), "error": str(e)[:160]}
    return {"text": str(body.get("text") or "").strip(), "ms": round((time.monotonic() - t0) * 1000.0, 1),
            "asr_ms": body.get("decode_ms"), "lane": bool(body.get("probe"))}


def tell_bridge(body: dict, timeout: float = TELL_TIMEOUT_S) -> dict | None:
    """POST the bridge's /v1/barge; its answer, or None when it did not answer."""
    from .settings import url as _url
    target = os.environ.get("CRISSA_BARGE_URL") or _url("bridge", path="/v1/barge")
    req = urllib.request.Request(target, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read().decode() or "{}")
            return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001 - an old bridge (404) or none: no cut, the old path stands
        return None


def log_path():
    return HOME / "logs" / "barge.jsonl"


def record(row: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **row}) + "\n")
    except OSError:
        pass


class Probe:
    """The looks of one stretch of speech over her line (an EPISODE: from its first speech frame until the utterance it
    opened is flushed, or the VAD goes idle again). One look at a time on its own thread, always the newest window.

    look(pcm, sr) -> {"text", "ms"}; judge(text, t0, t1) -> words_over_her's verdict; tell(body) -> the bridge's answer
    (all three are the module's functions unless a test stands them in).

    The end of a cut episode is told to the bridge exactly once: by the decode of the utterance it opened (the ear's
    decode loop, after settle(): turn or no turn), or here when the episode never opened an utterance."""

    def __init__(self, asr_url: str = "", *, look: Callable | None = None, judge: Callable | None = None,
                 tell: Callable | None = None):
        self._asr = asr_url
        self._look = look or (lambda pcm, sr: asr_look(self._asr, pcm, sr))
        self._judge = judge or words_over_her
        self._tell = tell or tell_bridge
        self._cv = threading.Condition()
        self._slot: tuple | None = None
        self._eid: str | None = None              # the episode being heard now
        self._fired: deque = deque(maxlen=16)     # episodes that cut her
        self._refused: deque = deque(maxlen=16)   # episodes the bridge would not cut for (out of scope)
        self._ended: dict[str, str] = {}          # episode -> "decode" | "idle"
        self._told: deque = deque(maxlen=16)      # episodes whose end the bridge has heard
        self._busy: str | None = None
        self._cut_why: dict[str, str] = {}        # episode -> what the cutting look heard (stop, named, words, ...)
        self._prev: dict[str, tuple] = {}         # the last look's strength and window end, for its episode
        self._cover: dict[str, dict] = {}         # episode -> what its looks covered and found (hers_through)
        self._n = 0
        self.stats: dict[str, Any] = {"looks": 0, "cuts": 0, "refused": 0, "look_ms_last": None, "last": None}
        self._thread = threading.Thread(target=self._run, name="ear-barge-probe", daemon=True)
        self._thread.start()

    def begin(self, now: float | None = None) -> str:
        """A new episode: its id."""
        with self._cv:
            self._n += 1
            self._eid = "b%d-%d" % (int((time.time() if now is None else now) * 1000), self._n)
            return self._eid

    def end(self, eid: str | None, how: str = "decode") -> None:
        """The episode is over: "decode" (its utterance was flushed; the decode tells the bridge) or "idle" (no
        utterance: when it cut her, the bridge hears now that no turn will come)."""
        if eid is None:
            return
        with self._cv:
            if self._eid == eid:
                self._eid = None
            self._ended[eid] = how
            while len(self._ended) > 32:
                self._ended.pop(next(iter(self._ended)))
            tell = how == "idle" and eid in self._fired and eid not in self._told
            if tell:
                self._told.append(eid)
        if tell:
            self.tell_end(eid, {}, why="no_utterance")

    def fired(self, eid: str | None) -> bool:
        return eid is not None and eid in self._fired

    def wants(self, eid: str | None) -> bool:
        """Still looking in this episode (the one being heard, not cut, not refused by the bridge)."""
        return eid is not None and eid == self._eid and eid not in self._fired and eid not in self._refused

    def offer(self, eid: str, wav: np.ndarray, sr: int, t0: float, t1: float, onset_ts: float | None = None) -> bool:
        """The newest window of the episode; replaces one not yet looked at."""
        with self._cv:
            if not self.wants(eid):
                return False
            self._slot = (eid, wav, int(sr), float(t0), float(t1), onset_ts, time.time())
            self._cv.notify_all()
            return True

    def hers_through(self, eid: str | None, speech_end: float | None) -> bool:
        """Did this episode's looks cover all its speech, without a gap, up to `speech_end`, and find only her words
        (no word of anyone's, not even a weak one, no failed look, no cut, no refusal)? Then the whole utterance is her
        own line heard back and need not be decoded (ear_loop: SKIP_ECHO)."""
        if eid is None or eid in self._fired or eid in self._refused:
            return False
        c = self._cover.get(eid)
        if not c or not c["looks"] or c["gap"] or c["someone"]:
            return False
        return speech_end is None or c["until"] >= float(speech_end) - 0.05

    def heard_from(self, eid: str | None) -> float | None:
        """The start of the first window of this episode in which a look heard a word that was not hers."""
        c = self._cover.get(eid) if eid else None
        return c.get("heard_from") if c else None

    def holds_cap(self, eid: str | None, utter_s: float, on_air_max_s: float) -> bool:
        """Should the on-air cap wait for this stretch (utter_s long)? A look still deciding (CAP_HOLD_S), or the last
        look heard a word of someone's that is waiting for its confirming look (SOMEONE_HOLD_S). When the looks are back
        to her words alone the cap applies again, so a turn the looks never cut reaches the whole decode (and the
        turn-level cut) as soon as before (measured: held to 8 s, "Hang on, is that right?" reached the bridge 11.6 s
        after its first word)."""
        if eid is None or eid in self._fired or eid in self._refused:
            return False
        last = self._prev.get(eid)
        if last is not None and last[0] >= 1 and utter_s < on_air_max_s + SOMEONE_HOLD_S:
            return True
        return utter_s < on_air_max_s + CAP_HOLD_S and self.pending(eid)

    def pending(self, eid: str | None) -> bool:
        """A look of this episode is running or waiting (its answer may still cut her)."""
        if eid is None or eid in self._fired or eid in self._refused:
            return False
        with self._cv:
            return self._busy == eid or (self._slot is not None and self._slot[0] == eid)

    def settle(self, eid: str | None, timeout: float = LOOK_TIMEOUT_S + TELL_TIMEOUT_S) -> None:
        """Wait until no look of this episode is waiting or running (the decode reads fired() after this)."""
        if eid is None:
            return
        end = time.time() + timeout
        with self._cv:
            while (self._busy == eid or (self._slot is not None and self._slot[0] == eid)) and time.time() < end:
                self._cv.wait(0.02)

    def idle(self, timeout: float = 5.0) -> bool:
        """For tests and the measurement: True once no look is running or waiting."""
        end = time.time() + timeout
        with self._cv:
            while (self._slot is not None or self._busy) and time.time() < end:
                self._cv.wait(0.02)
            return self._slot is None and not self._busy

    def tell_end(self, eid: str, turn: dict, *, why: str = "") -> None:
        """The bridge hears how the utterance that cut her ended: with words (its turn is on the ring with barge_id), or
        with none (her echo, a noise: she goes on by herself)."""
        text = str((turn or {}).get("text") or "").strip()
        body = {"id": eid, "phase": "end", "turn": bool(text), "why": why or str((turn or {}).get("reason") or ""),
                "cut_why": self._cut_why.get(eid, "")}
        record({"event": "end", **body, "text": text[:120]})
        threading.Thread(target=self._tell, args=(body,), name="ear-barge-end", daemon=True).start()

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._slot is None:
                    self._cv.wait()
                eid, wav, sr, t0, t1, onset_ts, offered = self._slot
                self._slot = None
                self._busy = eid
            try:
                self._one(eid, wav, sr, t0, t1, onset_ts, offered)
            except Exception as e:  # noqa: BLE001 - a look never takes the ear down
                record({"event": "look_error", "id": eid, "error": "%s: %s" % (type(e).__name__, str(e)[:160])})
            finally:
                with self._cv:
                    self._busy = None
                    self._cv.notify_all()

    def _one(self, eid, wav, sr, t0, t1, onset_ts, offered) -> None:
        if eid in self._fired or eid in self._refused:
            return
        pcm = (np.clip(np.asarray(wav, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        got = self._look(pcm, sr) or {}
        self.stats["looks"] += 1
        self.stats["look_ms_last"] = got.get("ms")
        text = str(got.get("text") or "")
        v = self._judge(text, t0, t1)
        strength = int(v.get("strength", 2 if v.get("cut") else 0))
        own = frozenset(v.get("own") or ())
        n_words = len(v.get("words") or ())
        prev = self._prev.get(eid)
        self._prev = {eid: (strength, t1, own, n_words)}
        with self._cv:
            c = self._cover.setdefault(eid, {"from": t0, "until": t0, "gap": False, "someone": False, "looks": 0,
                                             "heard_from": None})
            c["gap"] = c["gap"] or t0 > c["until"] + 0.05
            c["until"] = max(c["until"], t1)
            heard = strength >= 1 or int(v.get("alien") or 0) > 0
            c["someone"] = c["someone"] or heard or bool(got.get("error"))
            if heard and c["heard_from"] is None:
                c["heard_from"] = t0
            c["looks"] += 1
            while len(self._cover) > 16:
                self._cover.pop(next(iter(self._cover)))
        # one word of someone's cuts only when the look before it (CONFIRM_S) was someone's too and this one has more to
        # it (a word that one did not have, or more words): a person goes on talking, a bend of her echo decoded twice
        # says the same thing
        # round 18: a "prefix" (the first word of "hold on", a greeting) only holds the on-air cap; never a cut by itself
        cut = strength >= 2 or (strength == 1 and v.get("why") != "prefix" and prev is not None and prev[0] >= 1
                                and t1 - prev[1] <= CONFIRM_S + 1e-6 and (bool(own - prev[2]) or n_words > prev[3]))
        self.stats["last"] = {"text": text[:80], "strength": strength, "cut": cut, "why": v.get("why")}
        if not cut or eid in self._fired or eid in self._refused:
            return
        heard_at = time.time()
        ans = self._tell({"id": eid, "phase": "cut", "text": text[:200], "words": list(v.get("own") or [])[:12],
                          "why": v.get("why") if strength >= 2 else "one_word_twice", "t0": round(t0, 3), "t1": round(t1, 3),
                          "onset_ts": round(onset_ts, 3) if onset_ts else None, "look_ms": got.get("ms"),
                          "offered_at": round(offered, 3), "heard_at": round(heard_at, 3)})
        cut = bool(ans and ans.get("cut"))
        tell = False
        with self._cv:
            if cut:
                self._fired.append(eid)
                self._cut_why[eid] = str(v.get("why") or "")
                while len(self._cut_why) > 16:
                    self._cut_why.pop(next(iter(self._cut_why)))
                self.stats["cuts"] += 1
                tell = self._ended.get(eid) == "idle" and eid not in self._told
                if tell:
                    self._told.append(eid)
            else:
                self._refused.append(eid)          # out of the bridge's scope, or no bridge: no more looks this time
                self.stats["refused"] += 1
        record({"event": "cut" if cut else "not_cut", "id": eid, "text": text[:120], "why": v.get("why"),
                "bridge": (ans or {}).get("why") if ans is not None else "no answer", "look_ms": got.get("ms"),
                "window_s": round(t1 - t0, 2), "since_onset_s": round(heard_at - onset_ts, 3) if onset_ts else None})
        if tell:
            self.tell_end(eid, {}, why="no_utterance")


def window(frames, start_seq: int, frame_s: float) -> tuple[np.ndarray, float, float]:
    """The newest audio of an episode from `frames` [(seq, end_ts, wav), ...]: the frames from `start_seq` on, the newest
    WINDOW_S of them; (wav, t0, t1). By frame number, not by clock, so a late frame never pulls older ones in."""
    keep = [(e, w) for q, e, w in frames if q >= start_seq]
    if not keep:
        return np.zeros(0, dtype=np.float32), 0.0, 0.0
    n = max(1, int(round(WINDOW_S / max(frame_s, 0.05))))
    keep = keep[-n:]
    wav = np.concatenate([np.asarray(w, dtype=np.float32) for _, w in keep])
    return wav, keep[0][0] - frame_s, keep[-1][0]
