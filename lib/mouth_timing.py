"""
mouth_timing.py — universal viseme-to-hop scheduler for avatar loops.

Drop-in helper for house-speak.py / mouth_clock.py.  It fixes two problems
at once:

  1. Timing: visemes are anchored to the VOICED part of the audio (found by a
     simple energy detector), not to the start/end of the audio buffer.  This
     removes the ~0.5 s early open (TTS head padding) and the late close
     (tail padding) on both the warm word-span path and the even-spread
     fallback path.

  2. Flicker: within each word, visemes are given realistic relative
     durations (vowels long, stops short), every viseme is held for a minimum
     number of hops, the mouth pre-opens one hop before sound, and the final
     viseme fades to REST instead of snapping.

No third-party packages: standard library + numpy (already used for audio).

Public API
----------
    schedule(word_visemes, audio, sr, hop_ms=20, word_spans=None) -> list[str]

    word_visemes : list of lists, one inner list per word, e.g.
                   [["FV","AH","R","EE","AH","MBP","L","EH","SZ"]]
                   Use the same class names text_to_visemes() already emits.
    audio        : 1-D numpy array of samples (float or int16), mono.
    sr           : sample rate in Hz.
    hop_ms       : hop length; house-speak.py uses 20.
    word_spans   : optional list of (start_sec, end_sec) per word from the
                   :8195 aligner.  If None, words are spread across the voiced
                   region proportionally to their viseme weight.

    Returns one class name per hop for the whole audio, "REST" for silence.

House wire names (mouth_viseme.CLASSES) are accepted in WEIGHTS. schedule()
still emits REST/SZ spellings; house_name() maps them onto rest / S/Z /
TDN before class_index. Voice PCM is pre-atempo: pass hop_ms = 20 *
HOUSE_SPEAK_TEMPO so one hop is 20 ms of heard time.
"""

from __future__ import annotations

import os
import numpy as np

REST = "REST"

# Relative duration weight per viseme class.  Vowels are held 2-3x longer than
# lip-closures and fricatives.  Unknown classes get 1.0.  Adjust names to match
# text_to_visemes() if yours differ (e.g. "S/Z" vs "SZ").
WEIGHTS = {
    "AH": 3.0, "AA": 3.0, "OH": 3.0, "AW": 3.0, "UH": 2.5,
    "EE": 2.5, "IY": 2.5, "EH": 2.5, "AE": 2.5, "OO": 2.5, "W": 2.0,
    "R": 1.5, "L": 1.2, "TH": 1.2,
    "MBP": 1.0, "FV": 1.0, "SZ": 1.0, "S/Z": 1.0, "TD": 0.8, "KG": 0.8, "CH": 1.0,
    # house mouth_viseme.CLASSES (text_to_visemes emits these)
    "TDN": 0.8, "SH/CH": 1.0, "SH": 1.0, "ER": 2.5, "WOO": 2.0,
    "Y": 1.2, "H": 0.8, "rest": 1.0, "REST": 1.0, "rest-open": 1.0,
}
VOWELS = {
    "AH", "AA", "OH", "AW", "UH", "EE", "IY", "EH", "AE", "OO",
    "ER", "WOO",
}

# A SHEET SLOT IS HALF A FILM FRAME — THE ONE DEFINITION (the User 2026-09-22:
# "we want an oroborous in frames and no real time … a 2:1 ratio mouth tiles
# to clip frames"). Slot n belongs to film frame n >> 1, at 1x and at 1/4
# alike, so a dwell in hops is a dwell in semiframes and the conversion is a
# halving — no hop_ms, no frame rate, no playback rate in it at all.
#
# It lived only in display/clip-doctor.html (SLOTS_PER_FRAME), which is why
# CLAUDE.md has to carry a warning that the page's HOP_CEIL "must equal" the
# server's clamp: a number kept in two places drifts. The page reads this one
# off /v1/state now and keeps its literal only as a fallback.
#
# Why the ratio and not a frame rate: measured 2026-09-22, the films carry
# duplicate frames and the distinct-frame count is content-dependent
# (talk-back 205/241, crissa-close 219/241), so frames and seconds are NOT
# linearly related inside a take. No single fps is correct, which makes every
# ms<->frame conversion wrong by a varying amount. The ratio is exact.
SLOTS_PER_FRAME = 2


# Tunables (in hops; 1 hop = 20 ms by default)
# MIN_HOLD / CONS_HOLD read the environment once per process: house-speak
# is spawned per line, so the conductor's `min_hold` / `cons_hold` dials
# (POST /v1/mouth-tune, 2026-09-12) reach the sheet through
# HOUSE_MOUTH_MIN_HOLD / HOUSE_MOUTH_CONS_HOLD on the next Say. Absent env
# is the measured default (3 / 5); a bad value is the default, never a crash.
# hi was 12 hops (240 ms = 5.8 frames), which put half a second out of reach.
# 36 = 720 ms = 17.3 frames at 24 fps (the User 2026-09-20: "raise that cap so
# I can set when i return"). The page reds anything over half a second.
def _env_hops(name: str, default: int, lo: int = 0, hi: int = 36) -> int:   # 0 hops is a value (2026-10-25)
    try:
        return max(lo, min(hi, int(float(os.environ.get(name, "") or default))))
    except (TypeError, ValueError):
        return default


MIN_HOLD = _env_hops("HOUSE_MOUTH_MIN_HOLD", 3)   # never show a viseme for fewer than this many hops (60 ms)
# A CONSONANT HIT IS AT LEAST TWO CLIP FRAMES (the User 2026-09-11): the wall
# shows 24 distinct frames a second, so a hit shorter than 83 ms can fall
# between two of them. Five hops = 100 ms. Applies when the aligner gave the
# consonant its own onset (char spans); the fill_word path keeps MIN_HOLD.
CONS_HOLD = _env_hops("HOUSE_MOUTH_CONS_HOLD", 5)
VOWEL_LETTERS = set("aeiou")
PRE_OPEN = 1          # lips form the shape this many hops before sound
# EDGE PAD: A WORD'S FIRST SHAPE MAY START EARLY, AND ONLY IT
# (the User 2026-09-22: "could we intruduce a modifier that could be tuned for
# start or end of word dwell or lead padding? ... intra word hits could be
# padded by less or zero and space between sentences and words would hit more
# perfectly with longer dwell and middle hits stay sharp with no modifier so
# she does not fall behind in longer multi-hit words").
#
# HER OWN NUMBERS SAY THE SAME THING AND SAY WHICH WAY IT GROWS. Measured over
# her filmed closures (2026-09-21): dwell is 3.23 f interior and 5.50 f
# sentence-initial, while the RELEASE sits 1.56 f before the burst in both. So
# the extra dwell is on the FRONT — she closes earlier and lets go at the same
# moment. The pad therefore grows BACKWARDS, never forwards: forwards would
# carry the release late, which is the error the drill already measures.
#
# Two rules keep it from cascading, which is the "does not fall behind" half:
#   - it applies to the FIRST shape of a word only, never inside one
#   - it eats REST and nothing else, and stops at the previous word's last
#     painted hop, so it can never shorten or shift a shape that is already there
# A word with no silence in front of it is therefore padded by nothing at all,
# whatever the dial says.
#
# Both default to 0: nothing changes until they are turned. In hops, like the
# holds; the Composer shows them in frames (2 hops = 1 film frame).
SENT_PAD = _env_hops("HOUSE_MOUTH_SENT_PAD", 0, lo=0)   # after a real pause
WORD_PAD = _env_hops("HOUSE_MOUTH_WORD_PAD", 0, lo=0)   # after a word gap
# What counts as "a sentence, not just a word": hops of silence in front of it.
# GAP_TO_REST (8 hops = 160 ms) is already the house's "this gap is real" line.
SENT_GAP = _env_hops("HOUSE_MOUTH_SENT_GAP", 8, lo=1)
# THE PER-CLASS PREP TABLE (the User 2026-09-25: "build the per-class prep table into the sheet and
# then compare to the STANDARDIZED padding"). Measured on 136 word-first shapes across nine
# hand-checked keys (six Crissa pangram takes, three Lila takes): a word's first shape forms about
# ONE FRAME before its sound whatever the position (median -1 f sentence-initial, interior and after
# a pause alike); TH MBP FV H KG 1 f and SH/CH S/Z 2 f, tight; TDN WOO L Y EE and the vowels on the
# sound, wide. After a pause the tail lengthens (p25 -4 f vs -2 f interior), so a pause adds a bonus.
# A pad is one number for the whole take; this is one number per class — the rule the live sheet can
# carry, where the pads were its two-number stand-in. Frames -> hops x SLOTS_PER_FRAME (2).
# Same law as edge_pad: it eats REST only, backwards from the word's first shape, never past the
# previous word, never over a scheduled hop. DEFAULT OFF: house-speak is exec'd from disk per line, so
# an ON default goes live on the wall at commit. HOUSE_MOUTH_CLASS_PREP=1 arms it (the bench first);
# HOUSE_MOUTH_CLASS_PREP_TABLE="MBP:1,SH/CH:2,..." (frames) overrides the table, HOUSE_MOUTH_CLASS_PREP_PAUSE_F
# the pause bonus (frames, default 1).
CLASS_PREP_ON = os.environ.get("HOUSE_MOUTH_CLASS_PREP", "0").strip().lower() in ("1", "true", "on", "yes")
CLASS_PREP_F = {"TH": 1, "MBP": 1, "FV": 1, "H": 1, "KG": 1,
                "SH/CH": 2, "SH": 2, "CH": 2, "S/Z": 2, "SZ": 2}


def _class_prep_table() -> dict:
    """{class: hops} from the frame table, with the env override on top."""
    t = dict(CLASS_PREP_F)
    raw = os.environ.get("HOUSE_MOUTH_CLASS_PREP_TABLE", "").strip()
    for item in raw.split(","):
        if ":" in item:
            k, v = item.rsplit(":", 1)
            try:
                t[k.strip()] = float(v)
            except ValueError:
                pass
    return {k: max(0, int(round(float(v) * SLOTS_PER_FRAME))) for k, v in t.items()}


def _class_prep_pause_hops() -> int:
    try:
        return max(0, int(round(float(os.environ.get("HOUSE_MOUTH_CLASS_PREP_PAUSE_F", "1")) * SLOTS_PER_FRAME)))
    except ValueError:
        return SLOTS_PER_FRAME
TAIL_FADE = 6         # hops to ease into REST after last voiced hop
# NO FILLER INTO A COMMA OR PERIOD (the User 2026-09-24: "fill vs comma period or
# between words, no filler to comma"). The tail fade re-paints the word's last
# vowel before the pause: "all," read oh-l-oh, "including," ...TDN EE. Off = the
# last shape goes straight to the pause; between words the fill is unchanged.
# "none" = no tail fade anywhere: the word gaps are the pads' job (WORD_PAD /
# SENT_PAD grow the NEXT word's first shape back into the silence — the User:
# "we have word pad and sentence pad to help there").
# Default ON (the old behaviour) because house-speak is exec'd per line.
_TAIL = os.environ.get("HOUSE_MOUTH_TAIL_FADE", "1").strip().lower()
TAIL_FADE_VOWEL = _TAIL not in ("0", "false", "off", "no", "none")
TAIL_FADE_NONE = _TAIL == "none"
GAP_TO_REST = 8       # a silent gap longer than this (160 ms) returns to REST
# THE CAP IS THE FRAME BUDGET, NOT A TASTE SETTING (the User 2026-09-20:
# "24 max changes per second. This way my dials do something everytime i
# change them").
#
# It was 10 from 2026-09-07 ("10 is the sweet spot, not 8"), justified as
# "every viseme seen twice on a 24 fps glass" — 10 x tempo 1.2 = 12/s = 24/2.
# That was a legibility judgement, and it silently outranked min_hold and
# cons_hold: the budget is max_per_sec x voiced seconds, so no hold setting
# could ever produce more changes than the cap allowed, and lowering the holds
# to 1 did nothing. A dial that cannot act is the fault we spent 2026-09-20
# pulling out of this tree.
#
# 24 = ONE TILE PER CLIP FRAME (the User 2026-09-20: "max 24/s", "we can start
# with 1 tile per frame"). The film changes 24 times a second, so more mouth
# changes than that move the lips while the face under them is still. His
# reason for wanting the headroom, from picking tiles by hand: "natural can be
# as little as one frame for the hit ... one frame per phoneme. I mean from oo
# to ah" — a real transition can occupy a single frame, and at the old cap of
# 10 (12/s at tempo 1.2) half of those could not exist.
#
# Measured when it was raised: on crissa-close-harvest the sheet produced 6.5
# changes/s at min_hold 3 and 6.8 at min_hold 1, so neither the old cap nor the
# holds were binding on that film — the limiter there is upstream, in how many
# DISTINCT visemes the line has after adjacent identical ones collapse. This
# change buys headroom for a fast line, not shapes on a slow one.
#
# The User's point on the eye, which is why the old "seen twice" floor went: at
# 41.7 ms a shape is nowhere near a detection threshold, so the second frame was
# never needed for it to be SEEN. It has to be rendered and not culled — a
# pipeline limit, not a perceptual one.
#
# OFF SINCE 2026-09-20 (the User: "go ahead and remove the cap", "no cap i say
# just go read after 1/2s or like that i frames").
#
# The holds already bound the rate, in FRAMES, on a dial he can see and turn:
# nothing is shorter than a hop, so min_hold 1 permits ~50 changes/s and
# min_hold 2 permits ~25. The cap was a SECOND limiter doing the same job
# invisibly, and being invisible is what let it eat his consonants — it
# outranked the holds, so lowering them did nothing, and the merge takes
# consonants FIRST (0.26 -> 0.78 consonants seen on harvest-talk-back when it
# went 10 -> 24).
#
# It was also a frame rate wearing a seconds label: "24" meant one tile per clip
# frame and would have silently meant one per 2.5 frames on a 60 Hz source.
# Off, that question does not arise — the holds are in frames already.
#
# _cap_changes returns the sequence untouched at <= 0, so 0 is off with no other
# change. HOUSE_MOUTH_MAX_CHANGES_PER_SEC puts a ceiling back; house-speak is
# spawned per line, so it reaches the sheet on the next Say.
try:
    MAX_CHANGES_PER_SEC = max(0, min(120, int(float(
        os.environ.get("HOUSE_MOUTH_MAX_CHANGES_PER_SEC", "") or 0))))
except (TypeError, ValueError):
    MAX_CHANGES_PER_SEC = 0

# schedule() spellings → mouth_viseme.CLASSES. Unknown names stay as-is
# (class_index then falls through to rest).
HOUSE_ALIASES = {
    "REST": "rest",
    "SZ": "S/Z",
    "S": "S/Z",
    "Z": "S/Z",
    "TD": "TDN",
    "CH": "SH/CH",
    "SH": "SH/CH",
    "W": "WOO",
    "IY": "EE",
    "AA": "AH",
    "AE": "AH",
    "OO": "WOO",
    "UW": "WOO",
    "UH": "OH",
    "AW": "AH",
}


def house_name(c) -> str:
    """Map a schedule() class onto mouth_viseme.CLASSES."""
    s = str(c or REST).strip() or REST
    if s in HOUSE_ALIASES:
        return HOUSE_ALIASES[s]
    return HOUSE_ALIASES.get(s.upper(), s)


def pcm16_to_audio(pcm: bytes):
    """s16le mono bytes → float array for schedule()."""
    n = len(pcm) - (len(pcm) % 2)
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    return np.frombuffer(memoryview(pcm)[:n], dtype="<i2").astype(np.float64)


# ----------------------------------------------------------------------------
# 1. Voice activity: find which hops actually contain speech
# ----------------------------------------------------------------------------
def voiced_hops(audio: np.ndarray, sr: int, hop_ms: float = 20) -> np.ndarray:
    """Boolean array, one entry per hop, True where there is speech energy."""
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.size and np.abs(x).max() > 1.5:          # int16-style input
        x = x / 32768.0
    hop = max(1, int(sr * hop_ms / 1000))
    n = int(np.ceil(x.size / hop))
    if n == 0:
        return np.zeros(0, dtype=bool)
    pad = np.zeros(n * hop)
    pad[: x.size] = x
    rms = np.sqrt((pad.reshape(n, hop) ** 2).mean(axis=1))
    # Threshold sits between the noise floor and the loud parts.
    floor = np.percentile(rms, 10)
    peak = np.percentile(rms, 95)
    thr = floor + 0.12 * (peak - floor)
    v = rms > max(thr, 1e-4)
    # Fill tiny dropouts inside words (unvoiced stops) so a word stays contiguous.
    return _close_gaps(v, max_gap=GAP_TO_REST // 2)


def _close_gaps(v: np.ndarray, max_gap: int) -> np.ndarray:
    v = v.copy()
    i = 0
    n = v.size
    while i < n:
        if not v[i]:
            j = i
            while j < n and not v[j]:
                j += 1
            if 0 < i and j < n and (j - i) <= max_gap:
                v[i:j] = True
            i = j
        else:
            i += 1
    return v


def voiced_regions(v: np.ndarray):
    """List of (start_hop, end_hop_exclusive) contiguous voiced runs."""
    regions, start = [], None
    for i, on in enumerate(v):
        if on and start is None:
            start = i
        elif not on and start is not None:
            regions.append((start, i)); start = None
    if start is not None:
        regions.append((start, v.size))
    return regions


# ----------------------------------------------------------------------------
# 2. Distribute a word's visemes across a span with realistic proportions
# ----------------------------------------------------------------------------
def fill_word(visemes, n_hops: int) -> list:
    """Return n_hops class names for one word."""
    if n_hops <= 0 or not visemes:
        return []
    w = np.array([WEIGHTS.get(c, 1.0) for c in visemes], dtype=float)
    # Guarantee MIN_HOLD per viseme if the span allows it; otherwise drop the
    # least-important consonants until it fits (a fast word can't show them).
    vis = list(visemes)
    while len(vis) * MIN_HOLD > n_hops and len(vis) > 1:
        # drop the lowest-weight NON-first, NON-last consonant
        cands = [(w[i], i) for i in range(1, len(vis) - 1) if vis[i] not in VOWELS]
        if not cands:
            cands = [(w[i], i) for i in range(1, len(vis) - 1)] or [(w[-1], len(vis) - 1)]
        _, drop = min(cands)
        del vis[drop]
        w = np.delete(w, drop)
    counts = np.maximum(MIN_HOLD if len(vis) * MIN_HOLD <= n_hops else 1,
                        np.round(w / w.sum() * n_hops)).astype(int)
    # Fix rounding so the total equals n_hops exactly (adjust longest vowel).
    diff = n_hops - counts.sum()
    if diff != 0:
        k = int(np.argmax(w))
        counts[k] = max(1, counts[k] + diff)
    out = []
    for c, k in zip(vis, counts):
        out.extend([c] * int(k))
    return out[:n_hops] + [out[-1]] * max(0, n_hops - len(out))


def _letter_segments(chars) -> list:
    """[(kind, a, b)] runs of vowel / consonant letters from aligner char
    spans [(c, a, b)]. y is a vowel unless it opens the word (yes / by)."""
    segs = []
    for j, (c, a, b) in enumerate(chars):
        c = str(c).lower()
        kind = "v" if (c in VOWEL_LETTERS or (c == "y" and j > 0)) else "c"
        if c == "'":
            continue
        if segs and segs[-1][0] == kind:
            segs[-1] = (kind, segs[-1][1], float(b))
        else:
            segs.append((kind, float(a), float(b)))
    return segs


# SHAPES THAT SHARE ONE LETTER SHARE THE TIME TO THE NEXT LETTER (2026-09-24). "hue"
# is h_j_u: H and Y both key off the one letter "h", whose aligner span is a 20 ms
# slice, so H got under a hop and vanished (painted Y WOO; the User reads h-y-oo).
# On: several shapes on one letter split [its onset, the next letter's onset).
# Default OFF — the wall's house-speak runs this per line; the bench turns it on.
SPLIT_TO_NEXT = os.environ.get("HOUSE_MOUTH_SPLIT_TO_NEXT", "0").strip().lower() in ("1", "true", "on", "yes")


def viseme_onsets(visemes, chars) -> list | None:
    """Seconds at which each viseme of a word starts, from the aligner's
    letter spans. the User 2026-09-11: "key off consonants ... and starts of
    words, the vowels label themselves by the gaps between consonants".

    Letters are grouped into vowel / consonant runs; each viseme takes the
    next run of its own kind (never going back), several visemes in one run
    split it evenly. A viseme with no run of its kind left rides the last
    one. None when there is nothing to key off (no chars, no runs).
    """
    if not visemes or not chars:
        return None
    segs = _letter_segments(chars)
    if not segs:
        return None
    kinds = ["v" if v in VOWELS else "c" for v in visemes]
    # assign each viseme a segment index, monotone
    idx = []
    cur = 0
    for k in kinds:
        j = cur
        while j < len(segs) and segs[j][0] != k:
            j += 1
        if j >= len(segs):
            # nothing of this kind ahead: the last segment of this kind seen, else the current one
            back = [i for i in range(len(segs)) if segs[i][0] == k and i < cur]
            j = back[-1] if back else min(cur, len(segs) - 1)
        idx.append(j)
        cur = max(cur, j)
    onsets = [0.0] * len(visemes)
    for j in sorted(set(idx)):
        members = [i for i, x in enumerate(idx) if x == j]
        _, a, b = segs[j]
        if SPLIT_TO_NEXT and len(members) > 1 and j + 1 < len(segs):
            b = max(b, segs[j + 1][1])      # share the time up to the next letter
        for m, i in enumerate(members):
            onsets[i] = a + (b - a) * m / len(members)
    # monotone, never before the word's first letter
    lo = segs[0][1]
    for i in range(len(onsets)):
        onsets[i] = max(onsets[i], lo if i == 0 else onsets[i - 1])
    return onsets


def fill_word_timed(visemes, n_hops: int, onset_hops) -> list:
    """n_hops class names for one word whose visemes have measured onsets
    (hop offsets from the word start). Each viseme runs to the next onset;
    a consonant shorter than CONS_HOLD borrows from the vowel after it."""
    if n_hops <= 0 or not visemes:
        return []
    starts = [max(0, min(n_hops, int(round(h)))) for h in onset_hops]
    starts[0] = 0
    ends = starts[1:] + [n_hops]
    spans = [[max(s, 0), max(e, s)] for s, e in zip(starts, ends)]
    for i, v in enumerate(visemes):
        if v in VOWELS:
            continue
        ln = spans[i][1] - spans[i][0]
        # A borrow never deletes the vowel it borrows from: the neighbour keeps at least one hop (the grid's one
        # slot) whatever MIN_HOLD says - the holds may be 0 since 2026-10-25 (the User: a hit between two others can be
        # one slot, and any floor loses a different quick hit), and a vowel at 0 hops would be that lost hit.
        if ln < CONS_HOLD and i + 1 < len(visemes) and visemes[i + 1] in VOWELS:
            take = min(CONS_HOLD - ln, max(0, (spans[i + 1][1] - spans[i + 1][0]) - max(MIN_HOLD, 1)))
            spans[i][1] += take
            spans[i + 1][0] += take
        # … and from the vowel BEFORE it (a word-final consonant has no vowel
        # after; the closing b of "Bob" was 1–2 hops and the min-hold merge
        # gave it to the AH — the User 2026-09-13 03:3x "failed to articulate").
        ln = spans[i][1] - spans[i][0]
        if ln < CONS_HOLD and i > 0 and visemes[i - 1] in VOWELS:
            take = min(CONS_HOLD - ln, max(0, (spans[i - 1][1] - spans[i - 1][0]) - max(MIN_HOLD, 1)))
            spans[i - 1][1] -= take
            spans[i][0] -= take
    out = []
    for v, (a, b) in zip(visemes, spans):
        out.extend([v] * max(0, b - a))
    if not out:
        return fill_word(visemes, n_hops)
    return out[:n_hops] + [out[-1]] * max(0, n_hops - len(out))


# ----------------------------------------------------------------------------
# 3. Smoothing: pre-open, tail fade, min-hold, global busy-ness cap
# ----------------------------------------------------------------------------
def smooth(seq: list, hop_ms: float = 20) -> list:
    seq = list(seq)
    n = len(seq)
    # Pre-open: shift every REST->viseme transition earlier by PRE_OPEN hops.
    for i in range(1, n):
        if seq[i] != REST and seq[i - 1] == REST:
            for k in range(1, PRE_OPEN + 1):
                if i - k >= 0:
                    seq[i - k] = seq[i]
    # Tail fade: after a viseme->REST transition, hold a half-open shape briefly
    # so the mouth eases shut instead of snapping.  We reuse the last vowel
    # seen in the word as the "easing" shape for TAIL_FADE//2 hops.
    i = 1
    while i < n:
        if TAIL_FADE_NONE:
            break
        if seq[i] == REST and seq[i - 1] != REST and not TAIL_FADE_VOWEL:
            j = i
            while j < n and seq[j] == REST:
                j += 1
            if j - i >= SENT_GAP or j >= n:
                i = j               # a comma / period / the end: no filler into it
                continue
        if seq[i] == REST and seq[i - 1] != REST:
            last_vowel = next((seq[j] for j in range(i - 1, max(-1, i - 20), -1)
                               if seq[j] in VOWELS), None)
            if last_vowel:
                for k in range(TAIL_FADE // 2):
                    if i + k < n and seq[i + k] == REST:
                        seq[i + k] = last_vowel
            i += TAIL_FADE
        else:
            i += 1
    # Calm first (off unless asked): what gives way is chosen, not whatever is shortest.
    ch = calm_hops()
    if ch > 0:
        seq = calm(seq, ch)
    # Min-hold: merge any run shorter than MIN_HOLD into its neighbour.
    seq = _enforce_min_hold(seq, MIN_HOLD)
    # Busy-ness cap. Declared with the other tunables; the eye reads ~8–10
    # mouth changes/s as speech, one class per hop as buzzing.
    seq = _cap_changes(seq, hop_ms)
    return seq


def edge_pad(seq: list, bounds: list, sent_pad: int, word_pad: int, sent_gap: int) -> list:
    """Start each word's first shape early, into the silence in front of it.

    `bounds` is the per-word (start, end) hop range `schedule` already built.
    A word whose gap is at least `sent_gap` hops is sentence-initial and takes
    `sent_pad`; any other word with a gap takes `word_pad`; a word that runs
    straight on from the one before it takes nothing, because there is no
    silence to take.

    It never writes over a non-REST hop, so the pad cannot shorten, move or
    delete a shape that is already scheduled — a mid-word hit is untouched and
    nothing downstream of it shifts.
    """
    if not bounds or (sent_pad <= 0 and word_pad <= 0):
        return seq
    seq = list(seq)
    n = len(seq)
    prev_end = None
    for wi, (s, e) in enumerate(bounds):
        s, e = max(0, int(s)), min(n, int(e))
        if s >= e or s >= n:
            prev_end = e if prev_end is None else max(prev_end, e)
            continue
        cls = seq[s]
        if cls == REST:
            prev_end = e
            continue
        # how much silence is really in front of this word
        floor = 0 if prev_end is None else max(0, int(prev_end))
        j = s
        while j - 1 >= floor and seq[j - 1] == REST:
            j -= 1
        gap = s - j
        if gap <= 0:
            prev_end = e
            continue
        pad = sent_pad if (prev_end is None or gap >= sent_gap) else word_pad
        pad = min(int(pad), gap)
        for k in range(1, pad + 1):
            if s - k >= 0 and seq[s - k] == REST:
                seq[s - k] = cls
        prev_end = e
    return seq


def class_prep(seq: list, bounds: list, table: dict, pause_hops: int, sent_gap: int) -> list:
    """Start each word's first shape early by ITS CLASS's prep (hops), plus `pause_hops` when the
    silence in front of the word is at least `sent_gap`. Eats REST only, never past the previous
    word, never over a scheduled hop — the edge_pad law, one number per class instead of per take."""
    if not bounds or not table and pause_hops <= 0:
        return seq
    seq = list(seq)
    n = len(seq)
    prev_end = None
    for s, e in bounds:
        s, e = max(0, int(s)), min(n, int(e))
        if s >= e or s >= n:
            prev_end = e if prev_end is None else max(prev_end, e)
            continue
        cls = seq[s]
        if cls == REST:
            prev_end = e
            continue
        floor = 0 if prev_end is None else max(0, int(prev_end))
        j = s
        while j - 1 >= floor and seq[j - 1] == REST:
            j -= 1
        gap = s - j
        if gap <= 0:
            prev_end = e
            continue
        pad = int(table.get(cls, 0))
        if prev_end is None or gap >= sent_gap:
            pad += int(pause_hops)
        pad = min(pad, gap)
        for k in range(1, pad + 1):
            if s - k >= 0 and seq[s - k] == REST:
                seq[s - k] = cls
        prev_end = e
    return seq


def _runs(seq):
    runs, start = [], 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[start]:
            runs.append([seq[start], i - start]); start = i
    return runs


# FEWER, LONGER SHAPES - AND WHICH ONES GO IS A CHOICE (the User 2026-09-27: "smooth out live").
# Measured on the pangram in the streamed voice: a word gets 9 film frames where the film take gives
# it 19, the sheet asks 93 shapes of 26 words, and 46 of the 119 painted last a frame or less -
# the mouth flickers. min_hold is the only lever there was, and it deletes whatever is shortest,
# which is the consonants: at 1.5 f the flicker falls to 8 and six consonants and one of four lip
# closures go with it.
#
# calm() takes a run shorter than `target` hops and decides by what it IS:
#   a lip closure (MBP, FV)     never dropped. It borrows from the vowel beside it.
#   a shape you can see          borrows from a vowel; with none to borrow from it swallows a
#   (TH L SH/CH R Y)             weak neighbour; only then does it give way.
#   a weak consonant             gives way first, into the vowel beside it. TDN KG S/Z H happen
#   (TDN KG S/Z H)               behind the teeth; at paint scale they read as the vowel anyway.
#   a vowel                      joins a vowel beside it, or swallows a weak consonant.
# Rests are walls: nothing crosses one, so the pads and the pauses are untouched.
# HOUSE_MOUTH_CALM_HOPS, 0 = off (the default). 3 = 1.5 f, 4 = 2 f.
CALM_KEEP = frozenset({"MBP", "FV"})
CALM_SEEN = frozenset({"TH", "L", "SH/CH", "SH", "CH", "R", "Y"})
CALM_WEAK = frozenset({"TDN", "TD", "KG", "S/Z", "SZ", "H"})


def calm_hops() -> int:
    try:
        return max(0, min(12, int(float(os.environ.get("HOUSE_MOUTH_CALM_HOPS", "0") or 0))))
    except ValueError:
        return 0


def _is_rest(c) -> bool:
    return str(c).lower().startswith("rest")


def calm(seq: list, target: int) -> list:
    if target <= 1 or not seq:
        return list(seq)
    runs = _runs(seq)
    settled: set = set()

    def nb(i, step):
        j = i + step
        return j if 0 <= j < len(runs) and not _is_rest(runs[j][0]) else None

    for _guard in range(4 * len(seq) + 8):
        short = [(ln, 0 if c in CALM_WEAK else 1 if c in VOWELS else 2, i)
                 for i, (c, ln) in enumerate(runs)
                 if ln < target and not _is_rest(c) and id(runs[i]) not in settled]
        if not short:
            break
        _, _, i = min(short)
        c, ln = runs[i]
        left, right = nb(i, -1), nb(i, 1)

        def fold(into):
            runs[into][1] += runs[i][1]
            del runs[i]

        def swallow(j):
            runs[i][1] += runs[j][1]
            del runs[j]

        def borrow():
            # a lip closure may take a neighbour down to one film frame, and from any shape
            # that is not itself a closure; a shape you can see takes only a vowel's spare
            floor = SLOTS_PER_FRAME if c in CALM_KEEP else target
            got = 0
            order = sorted([j for j in (right, left) if j is not None],
                           key=lambda j: (runs[j][0] not in VOWELS, -runs[j][1]))
            for j in order:
                if runs[j][0] in CALM_KEEP or (c not in CALM_KEEP and runs[j][0] not in VOWELS):
                    continue
                take = min(target - runs[i][1], max(0, runs[j][1] - floor))
                if take > 0:
                    runs[j][1] -= take
                    runs[i][1] += take
                    got += take
            return got

        vowels = [j for j in (right, left) if j is not None and runs[j][0] in VOWELS]
        weak = [j for j in (right, left) if j is not None and runs[j][0] in CALM_WEAK]
        if c in CALM_WEAK:
            if vowels:
                fold(vowels[0])
            elif left is not None or right is not None:
                fold(max([j for j in (left, right) if j is not None], key=lambda j: runs[j][1]))
            else:
                settled.add(id(runs[i]))
        elif c in VOWELS:
            if vowels:
                j = max(vowels, key=lambda j: runs[j][1])
                if runs[j][1] >= ln:
                    fold(j)
                else:
                    swallow(j)
            elif weak:
                swallow(weak[0])
            else:
                settled.add(id(runs[i]))
        else:
            if borrow():
                if runs[i][1] >= target:
                    continue
            if runs[i][1] >= target:
                continue
            if c in CALM_KEEP:
                if weak:
                    swallow(weak[0])
                else:
                    settled.add(id(runs[i]))
            elif weak:
                swallow(weak[0])
            elif vowels:
                fold(vowels[0])
            else:
                settled.add(id(runs[i]))
        # neighbours that became the same shape are one run
        k = 1
        while k < len(runs):
            if runs[k][0] == runs[k - 1][0]:
                runs[k - 1][1] += runs[k][1]
                del runs[k]
            else:
                k += 1
    out = []
    for c, ln in runs:
        out.extend([c] * ln)
    return out


def _enforce_min_hold(seq, min_hold):
    runs = _runs(seq)
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, (c, ln) in enumerate(runs):
            if ln < min_hold and c != REST:
                # give the hops to the longer neighbour (prefer a vowel neighbour)
                left = runs[i - 1] if i > 0 else None
                right = runs[i + 1] if i + 1 < len(runs) else None
                target = None
                for nb in (left, right):
                    if nb and nb[0] in VOWELS:
                        target = nb; break
                if target is None:
                    target = max([r for r in (left, right) if r], key=lambda r: r[1])
                target[1] += ln
                del runs[i]
                changed = True
                break
    out = []
    for c, ln in runs:
        out.extend([c] * ln)
    return out


def _cap_changes(seq, hop_ms=20, max_per_sec=MAX_CHANGES_PER_SEC):
    """Merge extra speech runs until the rate is at most max_per_sec.

    Consonants go first (the flash). First and last shapes of the take stay.
    """
    if max_per_sec <= 0 or not seq:
        return seq
    runs = _runs(seq)
    voiced = sum(ln for c, ln in runs if c != REST)
    if voiced <= 0:
        return seq
    budget = max(1, int(round(max_per_sec * voiced * (float(hop_ms) / 1000.0))))

    def speech_idx(rs):
        return [i for i, (c, _) in enumerate(rs) if c != REST]

    while len(speech_idx(runs)) > budget and len(runs) > 1:
        inner = speech_idx(runs)
        if len(inner) <= 2:
            break
        cands = [
            (runs[i][0] in VOWELS, runs[i][1], i)
            for i in inner[1:-1]
        ]
        if not cands:
            break
        _, _, i = min(cands)
        left = runs[i - 1] if i > 0 else None
        right = runs[i + 1] if i + 1 < len(runs) else None
        target = None
        for nb in (left, right):
            if nb and nb[0] in VOWELS:
                target = nb
                break
        if target is None:
            nbs = [r for r in (left, right) if r]
            if not nbs:
                break
            target = max(nbs, key=lambda r: r[1])
        target[1] += runs[i][1]
        del runs[i]
        if i > 0 and i < len(runs) and runs[i - 1][0] == runs[i][0]:
            runs[i - 1][1] += runs[i][1]
            del runs[i]
    out = []
    for c, ln in runs:
        out.extend([c] * ln)
    return out


# ----------------------------------------------------------------------------
# 4. Top-level scheduler
# ----------------------------------------------------------------------------
# THE WORDS SIT WHERE THE ALIGNER HEARD THEM (the User 2026-09-27: "she seems to fall behind at the
# end but hits most up front ... it is a forced padding somewhere we should remove").
#
# The warm path below STRETCHES the aligned words over the whole voiced region: first word start ->
# first voiced hop, last word end -> last voiced hop, everything between scaled to fit. That was
# built for an aligner that ate the pad into its first and last token, and it is right only when
# the voiced region IS the sentence. The voice leaks a syllable in front of the line ("werk", measured
# on Lila's pangram: voiced from 0.00 s, first real word at 0.64 s), so the region was 361 hops
# for 333.5 hops of words - every span scaled x1.082, the head painted ~14 f early and the error
# closing to 0 at the last word. No lead fixes a slope; the tuner's -5 f only centred it.
#
# With the switch on and the caller saying how long one hop is on the spans' own clock
# (`span_hop_ms`: house-speak's spans are HEARD seconds, its hops are heard semiframes), a span is
# placed at its own time and only CLIPPED into the voiced region - which still keeps a pad-eating
# aligner off the silence. It was OFF by default for one morning, on the benches only, while the
# lead was retuned there. THE DEFAULT IS ON since 2026-09-27 (the User, both libraries tuned with
# it: "lets fix delay to match tunes") - the wall wears those tunes, so a line that still
# stretched would be early at its head under a lead set without the stretch.
# Off, at once and with no restart (house-speak is exec'd per line): HOUSE_MOUTH_SPAN_ABS=0 in the
# spawner's env, or the file <state home>/mouth-span-abs.off.
def span_abs_on() -> bool:
    v = os.environ.get("HOUSE_MOUTH_SPAN_ABS", "").strip().lower()
    if v:
        return v in ("1", "true", "on", "yes")
    try:
        from crissa_home import HOME as _home
        if (_home / "mouth-span-abs.off").is_file():
            return False
    except Exception:  # noqa: BLE001 - no state home is no off switch
        pass
    return True


def schedule(word_visemes, audio, sr, hop_ms=20, word_spans=None, char_spans=None,
             out_bounds: list | None = None, span_hop_ms: float | None = None) -> list:
    """`char_spans`: per word, [(letter, a, b)] from the aligner (or None for
    that word) — consonants take their measured onsets, vowels fill between.
    Same length as word_visemes or ignored. `out_bounds`, when given, is filled
    with each word's (start_hop, end_hop), so a caller can tell a word gap from
    the pause after a comma (mouth_clock._midline_open)."""
    if isinstance(audio, (bytes, bytearray, memoryview)):
        audio = pcm16_to_audio(bytes(audio))
    v = voiced_hops(audio, sr, hop_ms)
    n = v.size
    seq = [REST] * n
    if n == 0 or not word_visemes:
        return seq
    regions = voiced_regions(v)
    if not regions:
        return seq
    first, last = regions[0][0], regions[-1][1]

    if word_spans is not None and len(word_spans) == len(word_visemes):
        # Warm path: aligner gave us word times.  Trust their ORDER and relative
        # positions, but re-anchor the whole set to the voiced region so TTS
        # head/tail padding can't push the mouth onto silence.
        starts = np.array([s for s, _ in word_spans], float)
        ends = np.array([e for _, e in word_spans], float)
        a0, a1 = starts.min(), ends.max()
        span_len = max(a1 - a0, 1e-6)
        if span_hop_ms and span_abs_on():
            per_s = 1000.0 / float(span_hop_ms)
            h0 = lambda t: min(max(t * per_s, first), last)
        else:
            h0 = lambda t: first + (t - a0) / span_len * (last - first)
        bounds = [(int(round(h0(s))), int(round(h0(e)))) for s, e in zip(starts, ends)]
    else:
        # Fallback: spread words across the voiced region by total viseme weight.
        wsum = np.array([sum(WEIGHTS.get(c, 1.0) for c in w) or 1.0 for w in word_visemes])
        edges = first + np.round(np.concatenate([[0], np.cumsum(wsum)]) / wsum.sum()
                                 * (last - first)).astype(int)
        bounds = list(zip(edges[:-1], edges[1:]))

    if out_bounds is not None:
        out_bounds[:] = [(int(a), int(b)) for a, b in bounds]
    if char_spans is not None and len(char_spans) != len(word_visemes):
        char_spans = None
    for wi, ((s, e), vis) in enumerate(zip(bounds, word_visemes)):
        s, e = max(0, s), min(n, e)
        filled = None
        chars = char_spans[wi] if char_spans is not None else None
        if chars and word_spans is not None:
            ons = viseme_onsets(vis, chars)
            if ons:
                # letters are heard seconds like the word spans: same re-anchor
                filled = fill_word_timed(vis, e - s, [h0(t) - s for t in ons])
        if filled is None:
            filled = fill_word(vis, e - s)
        # Only paint hops that are actually voiced inside the word bounds.
        for c_idx, cls in enumerate(filled):
            h = s + c_idx
            if h < n and (v[h] or True):   # word bounds already sit in voiced region
                seq[h] = cls
    # Anything painted onto a long silent gap between words goes back to REST.
    for i in range(n):
        if not v[i] and seq[i] != REST:
            # keep only if within GAP_TO_REST/2 of a voiced hop (short gaps ok)
            near = v[max(0, i - GAP_TO_REST // 2): i + GAP_TO_REST // 2 + 1].any()
            if not near:
                seq[i] = REST
    # AFTER that sweep, never before: it returns painted silence to REST by how
    # near a voiced hop is, which is exactly the silence the pad is there to use.
    seq = edge_pad(seq, bounds, SENT_PAD, WORD_PAD, SENT_GAP)
    # the per-class prep, on top of the pads (armed on the bench only until measured)
    if CLASS_PREP_ON:
        seq = class_prep(seq, bounds, _class_prep_table(), _class_prep_pause_hops(), SENT_GAP)
    return smooth(seq, hop_ms)


# ----------------------------------------------------------------------------
# Self-test with synthetic audio: 0.5 s silence, speech, 0.4 s silence
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    sr = 16000
    rng = np.random.default_rng(0)
    pad_head = np.zeros(int(0.5 * sr)) + rng.normal(0, 0.002, int(0.5 * sr))
    speech = np.sin(2 * np.pi * 180 * np.arange(int(0.9 * sr)) / sr) * 0.4
    speech *= (0.6 + 0.4 * np.sin(2 * np.pi * 4 * np.arange(speech.size) / sr))
    pad_tail = rng.normal(0, 0.002, int(0.4 * sr))
    audio = np.concatenate([pad_head, speech, pad_tail])

    words = [["FV", "AH", "R", "EE", "AH", "MBP", "L", "EH", "SZ"]]
    hops = schedule(words, audio, sr)
    runs = _runs(hops)
    t = 0.0
    print(f"{'start':>6} {'dur':>5}  class")
    for c, ln in runs:
        print(f"{t:6.2f} {ln*0.02:5.2f}  {c}")
        t += ln * 0.02
    nonrest = [i for i, c in enumerate(hops) if c != REST]
    print(f"\nfirst open at {nonrest[0]*0.02:.2f}s (speech starts 0.50s), "
          f"last open at {nonrest[-1]*0.02:.2f}s (speech ends 1.40s)")
    print(f"mouth changes: {len([r for r in runs if r[0] != REST])} in "
          f"{len(hops)*0.02:.2f}s")
