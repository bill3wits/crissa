"""The clock: real word boundaries -> a class per hop.

`mouth_viseme` owns WHAT shape a sound is (g2p, the 18 wire classes).
This module owns WHEN. They were one thing, and the timing half was a guess:

    expected_hops(text) = letters x 0.08 s, spread evenly

Measured 2026-09-01 on clip-speak-phoneme against the User's hand-dragged word
spans (34 words, 9.33 s):

    modelled total 9.44 s vs true 9.33 s   -> 1.01x.  The RATE is fine.
    mean |drift| 0.27 s, worst 0.73 s      -> the DISTRIBUTION is not.

That is the whole diagnosis. "a" is fast and "noon" is slow, so the error piles
up mid-phrase and resets at a pause — the User's "starts together but lips fall out
of sync quick". No better letters-per-second constant fixes it, because the
constant was never the problem. Only real boundaries are.

WHAT THIS DOES AND DOES NOT KNOW. Forced alignment gives WORD spans. Inside a
word the shapes are still spread evenly, which is still an approximation — it
is just a much smaller one, bounded by one word instead of one sentence. Do not
reach for phoneme-level alignment before measuring whether the word-level fix
was enough (docs/HANDOFF-MOUTH-20260901-PM.md).

When the voice PCM is in hand, hops_scheduled() (mouth_timing.schedule)
pins that sheet to the voiced region so TTS head/tail pad cannot steal
shapes. hops_from_spans stays the no-audio path.

Silence is not a held shape. A held final vowel across a pause is the
"stuck-shape look" the staff doc names. Which rest:

    REST       closed — head of the line, after .!?, tail, idle (class_at default)
    REST_OPEN  mid-sentence, the whole gap while she is still on the line

the User 2026-09-04: rest-open during speech, rest closed after periods and at rest.
A leftover closed rest in the same gap was the open/closed pump.
"""
from __future__ import annotations

import os
import re

from mouth_viseme import (
    REST,
    REST_OPEN,
    class_index,
    ends_sentence,
    word_tokens,
    word_viseme_lists,
    _collapse,
    _word_visemes,
)

__all__ = [
    "spans_from_words",
    "class_at",
    "hops_from_spans",
    "hops_scheduled",
    "word_spans_for_timing",
    "SPAN_MIN_S",
    "stamp_word_punct",
]

# A word span shorter than this cannot hold a shape worth painting; it gets one
# class rather than a slice per phone. 40 ms is two hops at the 20 ms envelope.
SPAN_MIN_S = 0.04


def stamp_word_punct(words, text: str | None):
    """Copy trailing .!? from `text` onto aligner tokens (which usually strip it)."""
    out = []
    tokens = word_tokens(text) if text else []
    ti = 0
    for w in words or []:
        if not isinstance(w, dict):
            continue
        d = dict(w)
        if tokens:
            core = _core(str(d.get("w") or ""))
            while ti < len(tokens):
                tok = tokens[ti]
                ti += 1
                if _core(tok).lower() == core.lower():
                    d["w"] = tok
                    break
        out.append(d)
    return out


def _core(token: str) -> str:
    return "".join(ch for ch in token if ch.isalnum() or ch in "'’")


def _emit_gap(out: list, t0: float, t1: float, kind: str) -> None:
    if t1 <= t0 + 1e-6:
        return
    out.append((t0, t1, kind))


def spans_from_words(words, gap_rest: bool = True,
                     text: str | None = None) -> list[tuple]:
    """[{w, a, b}] in seconds -> [(t0, t1, class)], gaps filled with rest.

    `words` is rail-align's `words` array, or anything with the same three
    keys. Order is taken as given; a caller that drops a token must not
    renumber (rail-align returns `i` for exactly this reason).

    Pass `text` (the voice line) so trailing .!? survive the aligner. Head of
    the take and anything after .!? is closed REST. Mid-sentence is REST_OPEN
    for the whole gap. After the last word, class_at returns REST (closed) —
    end of the message. Idle is the same closed REST.
    """
    out: list[tuple] = []
    prev_end = 0.0
    prev_word = ""
    for w in stamp_word_punct(words, text):
        try:
            a, b = float(w["a"]), float(w["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        if gap_rest and a > prev_end + 1e-6:
            # No previous token = head of the line (closed). Period = closed.
            kind = REST if (not prev_word or ends_sentence(prev_word)) else REST_OPEN
            _emit_gap(out, prev_end, a, kind)
        vs = _collapse(_word_visemes(str(w.get("w") or "").lower()))
        prev_word = str(w.get("w") or "")
        if not vs:
            out.append((a, b, REST))
            prev_end = b
            continue
        if (b - a) < SPAN_MIN_S or len(vs) == 1:
            out.append((a, b, vs[0]))
            prev_end = b
            continue
        # Even INSIDE the word — the honest remaining approximation.
        step = (b - a) / len(vs)
        for k, v in enumerate(vs):
            out.append((a + k * step, a + (k + 1) * step, v))
        prev_end = b
    return out


def class_at(spans: list[tuple], t: float) -> str:
    """The class covering time `t`. Before the first span or after the last is
    REST — she is not talking yet, or has stopped."""
    if not spans:
        return REST
    lo, hi = 0, len(spans) - 1
    if t < spans[0][0] or t >= spans[-1][1]:
        return REST
    while lo <= hi:                       # spans are sorted and disjoint
        mid = (lo + hi) // 2
        t0, t1, v = spans[mid]
        if t < t0:
            hi = mid - 1
        elif t >= t1:
            lo = mid + 1
        else:
            return v
    return REST


def hops_from_spans(spans: list[tuple], n: int, hop_s: float,
                    t0: float = 0.0, lead_s: float = 0.0) -> list[int]:
    """Class ids for `n` hops starting at `t0`.

    `lead_s` shifts the stamp EARLIER in her mouth: the class at hop i is the
    class of the sound `lead_s` later, so the jaw prepares the way a real one
    does. This is the same dial as HOUSE_VISEME_LEAD_MS and it is deliberately
    not applied here by default — the caller owns the lead.
    """
    return [class_index(class_at(spans, t0 + i * hop_s + lead_s))
            for i in range(max(0, int(n)))]


def word_spans_for_timing(words, text: str | None = None) -> list[tuple] | None:
    """Aligner [{w,a,b}] → [(a, b)] matching word_viseme_lists(text).

    Length must equal the viseme-word list or schedule() falls back to
    weight-spread. None if they cannot be paired.
    """
    wvs = word_viseme_lists(text or "")
    if not wvs:
        return None
    ab: list[tuple] = []
    for w in stamp_word_punct(words, text):
        try:
            a, b = float(w["a"]), float(w["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        vs = _collapse(_word_visemes(str(w.get("w") or "").lower()))
        if vs:
            ab.append((a, b))
    if len(ab) != len(wvs):
        return None
    return ab


def char_spans_for_timing(words, text: str | None = None) -> list | None:
    """Aligner [{w,a,b,chars}] → per viseme-word [(c, a, b)] or None, paired
    like word_spans_for_timing. None when the words cannot be paired."""
    wvs = word_viseme_lists(text or "")
    if not wvs:
        return None
    out: list = []
    for w in stamp_word_punct(words, text):
        try:
            a, b = float(w["a"]), float(w["b"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        vs = _collapse(_word_visemes(str(w.get("w") or "").lower()))
        if not vs:
            continue
        ch = w.get("chars")
        try:
            out.append([(str(c["c"]), float(c["a"]), float(c["b"])) for c in ch] if ch else None)
        except (KeyError, TypeError, ValueError):
            out.append(None)
    if len(out) != len(wvs):
        return None
    return out


# CLOSED REST AFTER A COMMA (the User 2026-09-24: "open rests between words.
# Closed rests between sentences or comma and next word"). _midline_open made
# every interior silence rest-open, so the pause after "all," painted an open
# mouth and only .!? ever closed. Length cannot stand in for the comma: on
# close-pangram-a the word gap impressed→all is 5 f, longer than SENT_GAP.
# Default OFF because house-speak is exec'd per line; the bench turns it on.
COMMA_CLOSED = os.environ.get("HOUSE_MOUTH_COMMA_CLOSED", "0").strip().lower() in ("1", "true", "on", "yes")
# The User, same day, on seeing it: "is open rest not closed after the commas. my bad".
# So only a sentence end closes the gap; a comma keeps the open rest, like a word
# gap. The switch keeps its name (the bench unit sets it); what it closes is .!?.
_CLAUSE_END = re.compile(r'[.!?]+["\'\)]*\s*$')


# A LONG GAP IS A SENTENCE, MEASURED IN HER OWN WORDS — THE ELASTIC PAUSE (the User
# 2026-09-24: "no difference in punctuation the length of time is the key", "frames will
# need adjustments for speech rate", "make that one part of the adjustable elastic
# factored by speech rate"). Measured over 28 takes (word end -> next word start):
# normal-pace takes have a median word of 7 f and a gap p90 of 9 f; the slow pangrams run
# 8-13 f words. The User set 12 f at normal pace = 1.7 words. So a gap of at least
# PAUSE_WORDS x this line's median word takes the closed rest; a shorter one the open rest,
# whatever the punctuation. It is a dial (pause_words), the env is its start value.
# 0 = off (default; house-speak is exec'd per line).
def _env_float(name: str, default: str) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except ValueError:
        return 0.0


PAUSE_WORDS = _env_float("HOUSE_MOUTH_PAUSE_WORDS", "0")
# NO BLIP IN A HAIRLINE GAP: half of all word gaps are <= 1 f and each could flash the
# open rest for a hop ("The beige": TH, rest-open, MBP). A gap under BLIP_WORDS x the
# median word holds the shape before it (0.3 = 2 f at normal pace). 0 = off.
BLIP_WORDS = _env_float("HOUSE_MOUTH_BLIP_WORDS", "0")
# THE WORD'S TAIL (the User 2026-09-24: "vowels fill except for word ending … or prescribe
# that dwell time", ".4 wordframes"). Only consonants are timed from the sound; a vowel
# between two of them fills the gap, but a word-final vowel has nothing after it to end
# it. It holds TAIL_WORDS x the line's median word after the word's last consonant, then
# the gap takes over (open or closed by PAUSE_WORDS). Measured over 25 word-final vowels
# on 5 takes (last consonant burst -> word end): median 0.33 words, middle half
# 0.16-0.57. 0 = off (default: the vowel runs to the aligner's word end).
TAIL_WORDS = _env_float("HOUSE_MOUTH_TAIL_WORDS", "0")


def closed_after_words(text: str | None) -> list[bool]:
    """Per word of word_viseme_lists(text): does a closed rest follow it (. ! ?)."""
    out = []
    for tok in word_tokens(text or ""):
        if _word_visemes(re.sub(r"[^a-z']+", "", tok.lower())):
            out.append(bool(_CLAUSE_END.search(tok)))
    return out


def _word_tails(names: list, bounds: list, tail: int) -> None:
    """In place: each word ending on a vowel holds that vowel `tail` hops after its last
    consonant — cut to rest inside the word, or carried over rest into the gap, never
    over the next word."""
    from mouth_timing import REST as TIMED_REST, VOWELS

    rests = (REST, TIMED_REST, REST_OPEN)
    n = len(names)
    for wi, (a, b) in enumerate(bounds):
        a, b = max(0, int(a)), min(n, int(b))
        shapes = [i for i in range(a, b) if names[i] not in rests]
        if not shapes or names[shapes[-1]] not in VOWELS:
            continue
        cons = [i for i in shapes if names[i] not in VOWELS]
        if not cons:
            continue                                  # a vowel-only word ("a", "I"): leave it
        v = names[shapes[-1]]
        start = cons[-1] + 1
        stop = min(n, int(bounds[wi + 1][0]) if wi + 1 < len(bounds) else n)
        for h in range(start, stop):
            if h < start + tail:
                if names[h] in rests or names[h] == v:
                    names[h] = v
                else:
                    break
            elif h < b and names[h] == v:
                names[h] = TIMED_REST                 # the rest of the word goes to the gap


def _midline_open(seq: list[str], bounds: list | None = None,
                  closed_after: list | None = None) -> list[str]:
    """Pad stays closed rest. Interior silence is rest-open, except a gap of at least
    PAUSE_WORDS median words (and, with COMMA_CLOSED, the gap after . ! ?), which stays
    closed. A gap under BLIP_WORDS median words holds the shape before it."""
    from mouth_timing import REST as TIMED_REST, house_name

    names = [house_name(c) for c in seq]
    spoken = [i for i, c in enumerate(names) if c not in (REST, TIMED_REST)]
    if len(spoken) < 2:
        return names
    keep = set()
    word = 0.0
    if bounds:
        lens = sorted(max(0, int(b) - int(a)) for a, b in bounds)
        word = float(lens[len(lens) // 2])            # this line's median word, in hops
        if TAIL_WORDS > 0:
            _word_tails(names, bounds, int(round(TAIL_WORDS * word)))
        for wi in range(len(bounds) - 1):
            a, b = int(bounds[wi][1]), int(bounds[wi + 1][0])
            punct = (COMMA_CLOSED and closed_after and len(bounds) == len(closed_after)
                     and closed_after[wi])
            if punct or (PAUSE_WORDS > 0 and b - a >= PAUSE_WORDS * word):
                keep.update(range(a, b))
    lo, hi = spoken[0], spoken[-1]
    blip = BLIP_WORDS * word
    i = lo
    while i < hi:
        if names[i] not in (REST, TIMED_REST):
            i += 1
            continue
        j = i
        while j < hi and names[j] in (REST, TIMED_REST):
            j += 1
        if i not in keep and blip > 0 and j - i < blip and i > 0:
            for h in range(i, j):
                names[h] = names[i - 1]         # hold the shape before, no flash
        else:
            for h in range(i, j):
                names[h] = REST if h in keep else REST_OPEN
        i = j
    return names


def hops_scheduled(word_visemes, audio, sr, hop_ms: float = 20,
                   word_spans=None, char_spans=None, text: str | None = None,
                   span_hop_ms: float | None = None) -> list[int]:
    """One wire id per hop, pinned to voiced energy.

    `audio` is a float/int16 array or s16le bytes. `word_spans` is
    [(start_sec, end_sec), ...] from the aligner, or None for the
    weight-spread fallback. Pre-open / min-hold / vowel duration live
    in mouth_timing.schedule; do not stack HOUSE_VISEME_LEAD_MS on top.
    """
    from mouth_timing import schedule

    bounds: list = []
    names = _midline_open(schedule(
        word_visemes, audio, sr, hop_ms=hop_ms, word_spans=word_spans, char_spans=char_spans,
        out_bounds=bounds, span_hop_ms=span_hop_ms), bounds, closed_after_words(text) if text else None)
    return [class_index(c) for c in names]


def hops_phoned(word_visemes, audio, sr, hop_ms: float = 20, word_spans=None, text: str | None = None,
                span_hop_ms: float | None = None) -> list[int] | None:
    """THE PHONE SHEET (round 40, HOUSE_MOUTH_PHONES=1): each word's phones (mouth_viseme's whole-line espeak
    spelling) share the aligner's span for that word evenly, one shape per phone, and no hold merges or deletes any of
    them (measured: every hold above one hop deleted real phonemes, at 3 hops half of them and 59 % of the closures).
    Word spans land on the voiced audio exactly as schedule() lands them; his locked pads (edge_pad) still start a
    word's first shape early; gaps are rest-open mid-line and rest after . ! ? (as hops_scheduled). None when the
    aligner's spans are not here or do not pair (the caller keeps hops_scheduled)."""
    import numpy as np

    from mouth_timing import (REST as TIMED_REST, SENT_GAP, SENT_PAD, WORD_PAD, edge_pad, pcm16_to_audio,
                              span_abs_on, voiced_hops, voiced_regions)

    if not word_visemes or word_spans is None or len(word_spans) != len(word_visemes):
        return None
    if isinstance(audio, (bytes, bytearray, memoryview)):
        audio = pcm16_to_audio(bytes(audio))
    v = voiced_hops(audio, sr, hop_ms)
    n = v.size
    regions = voiced_regions(v) if n else []
    if not regions:
        return None
    first, last = regions[0][0], regions[-1][1]
    starts = np.array([s for s, _ in word_spans], float)
    ends = np.array([e for _, e in word_spans], float)
    a0, span_len = starts.min(), max(ends.max() - starts.min(), 1e-6)
    if span_hop_ms and span_abs_on():
        per_s = 1000.0 / float(span_hop_ms)
        h0 = lambda t: min(max(t * per_s, first), last)          # noqa: E731
    else:
        h0 = lambda t: first + (t - a0) / span_len * (last - first)   # noqa: E731
    bounds = [(int(round(h0(s))), int(round(h0(e)))) for s, e in zip(starts, ends)]
    seq = [TIMED_REST] * n
    for (s, e), vis in zip(bounds, word_visemes):
        s, e = max(0, s), min(n, max(e, s + 1))
        k = len(vis)
        for idx in range(e - s):
            seq[s + idx] = vis[min(k - 1, idx * k // (e - s))]
    seq = edge_pad(seq, bounds, SENT_PAD, WORD_PAD, SENT_GAP)
    names = _midline_open(seq, bounds, closed_after_words(text) if text else None)
    return [class_index(c) for c in names]
