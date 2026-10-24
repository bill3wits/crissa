"""Text → viseme *classes* (the strip's instruments). Timing is the caller's.

The house's speech engine does not emit phoneme timestamps. The *shapes* come from the
sentence (text exists before the first PCM byte). The *clock* is the 20 ms
PCM envelope house-speak already posts (pts = sink time).

English ~44 phones collapse to ~13 visual classes (Preston-Blair). The
classes are universal; which filmed tile holds which class is the avatar.
The Conductor indexes the strip by class, then loudness among that class.
Unlabelled hubs, missing classes, and Composer level-holds fall back to
loudness of the whole strip.

Authority: docs/MOUTH-INSTRUMENTS.md. Do not pip-install g2p.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Short names on the wire and in the picker. Order is stable: rest=0 matches
# the retired 4-still bank's rest, so a leftover int 0 still means closed.
CLASSES = (
    "rest",
    "MBP",
    "FV",
    "TH",
    "TDN",
    "L",
    "R",
    "KG",
    "S/Z",
    "SH/CH",
    "EE",
    "EH",
    "ER",
    "AH",
    "OH",
    "WOO",
    "Y",
    "H",
    # Appended, never inserted: rest=0 stays closed. Mid-sentence pause is
    # rest-open (the User 2026-09-03: open mid-sentence, period/end closed).
    "rest-open",
)
# HIS DECIDED CLASSES (round 41, the User 2026-10-21 on his phoneme map; config/phoneme-families.json `family_v2`):
# a true open Ah (father, lot, the start of five, the laugh) and Aa (cat, the start of now) are NEW, appended so no wire
# id moves; today's AH tile is his Uh; TDN with S/Z and H with Y merge (one tile each: TDN's and H's). A class with no
# tile yet paints CLASS_FALLBACK's (never blank). ON by default since round 41 (crissa-b3 2026-10-22: the User
# confirmed the sheets, "perfect mouths", and they were made with V2 on; shipping it off would ship mouths he never
# saw). A library with no Ah/Aa tile (Lila's, the phone's baked packs) paints AH there, as before. Kill:
# HOUSE_MOUTH_CLASSES_V2=0 (today's nineteen). Its own switch, apart from the speller's (HOUSE_MOUTH_PHONES).
CLASSES_V2 = os.environ.get("HOUSE_MOUTH_CLASSES_V2", "1").strip().lower() in ("1", "true", "on", "yes")
if CLASSES_V2:
    CLASSES = CLASSES + ("Ah", "Aa")
CLASS_FALLBACK = {"Ah": "AH", "Aa": "AH"}
CLASSES_SET = frozenset(CLASSES)
CLASS_INDEX = {c: i for i, c in enumerate(CLASSES)}

# Retired 4-still names, now aliases of the 13-class shorts. Tests and any
# leftover hop that still says "aa" land on the open-jaw class, not a still.
REST, REST_OPEN, AA, EE, OH = "rest", "rest-open", "AH", "EE", "OH"

# Mark tool flags label≠geom when a "closed" class sits above this openness.
# Same cutoff here: MBP/rest pick among tiles that actually look shut.
CLOSED = frozenset({"MBP", "rest"})
CLOSED_OPEN = 0.40

# Human labels in mouth-strip.labels.json (mark UI / score). Short is the
# instrument; the parenthetical is for the eye.
CLASS_LONG = {
    "MBP": "MBP (lips shut)",
    "FV": "FV (lip to teeth)",
    "TH": "TH (tongue to teeth)",
    "TDN": "TDN (tongue tip)",
    "L": "L (tongue up)",
    "R": "R",
    "KG": "KG (back)",
    "S/Z": "S/Z (teeth close)",
    "SH/CH": "SH/CH (pursed)",
    "EE": "EE (wide)",
    "EH": "EH (mid)",
    "ER": "ER (mid round)",
    "AH": "AH (open jaw)",
    "OH": "OH (rounded open)",
    "WOO": "WOO (rounded)",
    "Y": "Y (ya)",
    "H": "H (smaller, constricted)",
    "rest": "rest",
    "rest-open": "rest (open, mid-sentence)",
    "Ah": "Ah (true open: father)",
    "Aa": "Aa (cat, now)",
}
if CLASSES_V2:
    CLASS_LONG["AH"] = "Uh (cut, about)"

# Sentence-final punct. Comma is mid-sentence (open rest), not this.
_SENTENCE_END = re.compile(r'[.!?]+["\'\)]*\s*$')
# Keep trailing punct on the token so ends_sentence can see it.
_WORD_TOKEN = re.compile(
    r"[A-Za-z0-9']+(?:['’][A-Za-z0-9]+)*[^\sA-Za-z0-9']*"
)


def ends_sentence(token: str) -> bool:
    """True after . ! ? — closed rest. False for comma / no punct — open rest."""
    return bool(_SENTENCE_END.search(token or ""))


def word_tokens(text: str) -> list[str]:
    """Words with trailing punct still attached. Aligners usually strip it."""
    return _WORD_TOKEN.findall(text or "")


# PHONEME FAMILIES ARE DATA (the User 2026-10-21: "let's map every phoneme to its phonetic family of sounds"): every
# phoneme of the three alphabets the house touches - CMUdict ARPAbet, espeak-ng -x, misaki/IPA - and the letter rules'
# own phones, each with its family (= the mouth class) today and under his v2 set, in config/phoneme-families.json.
# PHONE_TO_CLASS, DIPHTHONG and ESPEAK_PHONE are read from it, never a second copy here.
FAMILIES_PATH = Path(os.environ.get("HOUSE_PHONEME_FAMILIES") or
                     Path(__file__).resolve().parents[1] / "config" / "phoneme-families.json")
FAMILIES = json.loads(FAMILIES_PATH.read_text(encoding="utf-8"))
_SHEET_ROWS = {**FAMILIES["arpabet"], **FAMILIES["house"]}

# a phone -> its class: a diphthong's row names its onset's family (AY -> AH); DIPHTHONG splits it before this is read
PHONE_TO_CLASS = {ph: row["family"][0] for ph, row in _SHEET_ROWS.items() if row["family"]}

# Two-phone travelling vowels. Flattening them to one class holds her
# mouth still through the glide — the stuck-shape look. the User, 2026-09-01:
# "hi" is H then aye, and aye itself is AEI then EE.
DIPHTHONG = {ph: tuple(row["phones"]) for ph, row in _SHEET_ROWS.items() if len(row["phones"]) == 2}

# Under HOUSE_MOUTH_CLASSES_V2 the same machinery reads his decided column: a family's name becomes the class that
# paints it (Uh = today's AH tile, TDN+S/Z = TDN's, H+Y = H's), and each family has one stand-in phone (the first
# ARPAbet phoneme that is exactly that family), so a glide splits into the phones of ITS picks (five = Ah -> EE, never
# cat's Aa) and every other path (the letter rules, espeak) stays phones -> class.
V2_NAME = {"Uh": "AH", "TDN+S/Z": "TDN", "H+Y": "H"}


def _v2(fam: str) -> str:
    return V2_NAME.get(fam, fam)


V2_PHONE = {}
for _ph, _row in FAMILIES["arpabet"].items():
    if len(_row.get("family_v2") or []) == 1:
        V2_PHONE.setdefault(_row["family_v2"][0], _ph)
# A glide written the old way, as its two phones (his hand calls and the letter rules: five = AE IY), is the glide:
# under v2 it becomes the stand-ins of the glide's own pick (five = Ah EE), never cat's Aa then EE.
_OLD_GLIDE = {tuple(row["phones"]): ph for ph, row in FAMILIES["arpabet"].items() if len(row["phones"]) == 2}


def v2_glides(phones: list[str]) -> list[str]:
    """Under HOUSE_MOUTH_CLASSES_V2: each old-style glide pair in `phones` -> its glide's v2 stand-ins."""
    if not CLASSES_V2:
        return list(phones)
    out, i = [], 0
    while i < len(phones):
        pair = tuple(phones[i:i + 2])
        if len(pair) == 2 and pair in _OLD_GLIDE and _OLD_GLIDE[pair] in DIPHTHONG:
            out.extend(DIPHTHONG[_OLD_GLIDE[pair]])
            i += 2
        else:
            out.append(phones[i])
            i += 1
    return out


if CLASSES_V2:
    PHONE_TO_CLASS = {ph: _v2(row["family_v2"][0]) for ph, row in _SHEET_ROWS.items() if row.get("family_v2")}
    DIPHTHONG = {ph: tuple(V2_PHONE[f] for f in row["family_v2"]) for ph, row in _SHEET_ROWS.items()
                 if len(row.get("family_v2") or []) == 2 and all(f in V2_PHONE for f in row["family_v2"])}
DIGRAPH = [
    ("TH", "TH"), ("SH", "SH"), ("CH", "CH"), ("PH", "F"), ("CK", "K"),
    ("NG", "NG"), ("OO", "OO"), ("EE", "EE"), ("EA", "EE"), ("OA", "OW"),
    ("IE", "EE"),
    # the User, 2026-09-01: *"need a 'kw' in Quick, 'k' far different"*. QU is not
    # a K — the lips ROUND for the /w/, and a back-K with spread lips is a
    # visibly different mouth. Two phones, so the glide gets its own note.
    ("QU", ("K", "W")),
]
SINGLE = {
    "A": "AE", "E": "EH", "I": "IH", "O": "AA", "U": "AH", "Y": "IY",
    # X is /ks/ — spelling it as a bare K silently ate the /s/ in "fox",
    # which is a whole note of teeth-close missing from the sheet.
    "C": "K", "X": ("K", "S"), "J": "JH", "Q": ("K", "W"),
}

# Whole-word overrides. The letter machine cannot say /ju/ for YOU (OU is
# the /aʊ/ diphthong) or /mi/ for ME (E is EH; silent-e wants len>2).
# FIVE is i_e → AY = AE IY; AE already lands in class AH.
# SHE/HE are the same open-final-e set as ME (≤3 letters).
WORD_G2P = {
    # A HUM IS A CLOSED MOUTH (the User 2026-09-26 on talk-profile-90-b: "tune tosses random ah in there
    # for MM"): espeak reads "hmm" as a spoken syllable, H AH M, and spells "hm" out as letters; her lips
    # are shut for the whole hum (measured: frames 1-17 shut on that take). The table wins over espeak.
    "HMM": ["M"], "HM": ["M"], "MM": ["M"], "MMM": ["M"], "MHM": ["M"], "HMMM": ["M"],
    "YOU": ["Y", "UW"],
    "ME": ["M", "IY"],
    "BE": ["B", "IY"],
    "WE": ["W", "IY"],
    "SHE": ["SH", "IY"],
    "HE": ["HH", "IY"],
    "YE": ["Y", "IY"],
    "FIVE": ["F", "AE", "IY", "V"],
    # /oʊkeɪ/ is two syllables: OW (AO UW) then K EY (EH IY). The letter
    # machine spells O-K-A-Y as AH KG AH EE and the first land is late
    # (the User 2026-09-07: first Okay 1–2 frames late, "a two step").
    "OKAY": ["AO", "UW", "K", "EH", "IY"],
    "OK": ["AO", "UW", "K", "EH", "IY"],
    # Letter A→AE→AH opens the jaw on "var" and "a". Live mouth is
    # V eh R EE eh b l s (the User 2026-09-07). No courtesy vowel before /s/.
    "VARIABLES": ["V", "EH", "R", "IY", "EH", "B", "L", "S"],
    "VARIABLE": ["V", "EH", "R", "IY", "EH", "B", "L"],
    # "all" is oh-l on her face (the User 2026-09-24, after trying ah-l). espeak gives it
    # the same O: as "waters", which he reads w-ah — so this is the word, not the vowel.
    "ALL": ["AO", "L"],
    # "hue" is h-oh-woo on her face (the User 2026-09-24): espeak's Y glide reads as the
    # rounding starting — OH, then WOO.
    "HUE": ["HH", "AO", "UW"],
}


# ESPEAK-NG, THE PHONEMES AND NOT THE SPELLING (2026-09-23). The letter rules
# below read spelling: "beige" came out MBP EH EE KG (the -ige is ZH), "hue"
# H AH, "symphony" took an MBP and an FV for its "ph". The pangram harvest
# only covers every class if its phonemes are read, so WHICH comes from
# espeak-ng when HOUSE_MOUTH_G2P=espeak. It is piper's own copy
# (its folder is this machine's config/house-machine.json `espeak_dir`), not a pip install; the file
# is not executable there, so it runs through the dynamic loader. WORD_G2P (the User's hand calls: Okay,
# variables) still wins, and any failure falls back to the letter rules.
#
# DEFAULT OFF: house-speak is exec'd from disk for every line, so an ON default
# would go live on the wall at commit (CLAUDE.md, 09-22).
G2P_ENGINE = os.environ.get("HOUSE_MOUTH_G2P", "rules").strip().lower()
# THE MOUTH SPELLED BY PHONEMES (round 40, the User 2026-10-21: "espeak id say ... At those timings could handle one
# line a frame. Practically free"). HOUSE_MOUTH_PHONES=1 spells each line with ONE espeak-ng call (so the weak forms
# survive: "a" is a#, never the letter's name), keeps apostrophes ("I'll" is not "ill"), pairs espeak's words back to
# the line's by their sounds (espeak joins "in the" into one word and says "9:30" as two), and maps every phone through
# config/phoneme-families.json; house-speak then paints them with the phone sheet (mouth_clock.hops_phoned). Measured
# on 30 lines: WHICH 0.70 -> 0.91, closures missed 11/49 -> 3, ~4 ms a line. espeak-ng is GPL-3.0: it is called as a
# separate program, never bundled. ON by default since round 41 (the User 2026-10-22 on .101's walk, S4: "Lips are
# really good now. Shapes too." ... "ship it on. No doubt"). house-speak is exec'd from disk for every line, so the
# house wall takes it at the ff with no restart. Kill: HOUSE_MOUTH_PHONES=0 (the letter rules); a missing espeak-ng
# still falls back to the letter rules line by line.
PHONES_DEFAULT = "1"
PHONES_ON = os.environ.get("HOUSE_MOUTH_PHONES", PHONES_DEFAULT).strip().lower() in ("1", "true", "on", "yes")
if PHONES_ON:
    G2P_ENGINE = "espeak-line"


def _espeak_dir() -> str:
    """The house's private espeak-ng copy: HOUSE_ESPEAK_DIR, else this machine's config/house-machine.json
    `espeak_dir` (piper's copy). '' = none (a pack: lib/crissa_espeak finds the installed one on PATH)."""
    got = (os.environ.get("HOUSE_ESPEAK_DIR") or "").strip()
    if not got:
        try:
            import crissa_home
            got = crissa_home.house_value("espeak_dir")
        except ImportError:
            got = ""
    return str(Path(got).expanduser()) if got else ""


ESPEAK_DIR = Path(_espeak_dir()) if _espeak_dir() else None
ESPEAK_LOADER = "/lib64/ld-linux-x86-64.so.2"

# espeak-ng `-x` mnemonics (en-us) → the phones PHONE_TO_CLASS already knows.
# Diphthongs take two phones, like DIPHTHONG above; r-coloured vowels keep
# their R, because the lips make it.
ESPEAK_PHONE = {tok: list(row["phones"]) for tok, row in FAMILIES["espeak"].items()}
if CLASSES_V2:                       # each token through his column, as the stand-in phones of its families
    ESPEAK_PHONE = {tok: [V2_PHONE[f] for f in row["family_v2"] if f in V2_PHONE]
                    for tok, row in FAMILIES["espeak"].items()}
_ESPEAK_CACHE: dict[str, list[str] | None] = {}
_ESPEAK_DEAD = False


def espeak_token(tok: str) -> list[str] | None:
    """One `-x` token → phones. Stress marks go, and so do espeak's variant
    suffixes (`@2`, `I#`, `t#`, `O2`, `r-`), except where the digit is the
    phoneme itself (`0`, the lot vowel)."""
    t = tok.replace("'", "").replace(",", "")
    if not t:
        return []
    if CLASSES_V2 and t == "a#":                       # the weak a ("a cat"): a reduced vowel, his Uh
        return [V2_PHONE["Uh"]]
    if t in ESPEAK_PHONE:
        return list(ESPEAK_PHONE[t])
    base = t.rstrip("#-") if t != "0" else t
    if len(base) > 1 and base[-1].isdigit() and base[:-1]:
        base = base[:-1]
    if base in ESPEAK_PHONE:
        return list(ESPEAK_PHONE[base])
    return None


def espeak_parse(line: str) -> list[str] | None:
    """`b_'eI_Z` → ["B", "EH", "IY", "SH"]. None if any token is unknown."""
    out: list[str] = []
    for tok in (line or "").strip().split("_"):
        ph = espeak_token(tok)
        if ph is None:
            return None
        out.extend(ph)
    return out or None


def _espeak_cmd() -> tuple[list[str], dict] | None:
    """How to run espeak-ng here, from lib/crissa_espeak.found() (the one lookup: the house's copy, else the one the
    launcher installed on PATH). None = no espeak-ng (the letter rules)."""
    try:
        import crissa_espeak
    except ImportError:
        return None
    env = dict(os.environ)
    if ESPEAK_DIR is not None:
        env["HOUSE_ESPEAK_DIR"] = str(ESPEAK_DIR)
    found = crissa_espeak.found(env)
    if not found:
        return None
    args = ["-q", "-x", "--sep=_", "-v", "en-us"]
    if ESPEAK_DIR is not None and Path(found).parent == ESPEAK_DIR:    # piper's copy: its own libs and data
        cmd = [found] if os.access(found, os.X_OK) else [ESPEAK_LOADER, found]
        return cmd + args, dict(os.environ, LD_LIBRARY_PATH=str(ESPEAK_DIR),
                                ESPEAK_DATA_PATH=str(ESPEAK_DIR / "espeak-ng-data"))
    return [found] + args, dict(os.environ)


def espeak_words(words) -> dict[str, list[str] | None]:
    """Phones for many words in ONE espeak call (one word per line in, one
    line per word out). Cached for the process; None = use the letter rules."""
    global _ESPEAK_DEAD
    want = [w for w in dict.fromkeys(words) if w and w not in _ESPEAK_CACHE]
    run = _espeak_cmd() if want and not _ESPEAK_DEAD else None
    if want and not run:
        _ESPEAK_DEAD = True
    if want and run:
        import subprocess
        cmd, env = run
        try:
            r = subprocess.run(cmd, input="\n".join(want) + "\n", capture_output=True,
                               text=True, timeout=5, env=env)
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
            if r.returncode != 0 or len(lines) != len(want):
                raise RuntimeError("espeak-ng: rc %s, %d lines for %d words" % (
                    r.returncode, len(lines), len(want)))
            for w, ln in zip(want, lines):
                _ESPEAK_CACHE[w] = espeak_parse(ln)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            _ESPEAK_DEAD = True      # once per process; the rules take over
    return {w: _ESPEAK_CACHE.get(w) for w in words}


def g2p(word: str) -> list[str]:
    """English word → phones: the User's WORD_G2P first, then espeak-ng when
    HOUSE_MOUTH_G2P=espeak, else the letter rules below.
    """
    if G2P_ENGINE == "espeak":
        w = re.sub(r"[^A-Z']", "", (word or "").upper()).replace("'", "")
        if w and w not in WORD_G2P:
            ph = espeak_words([w.lower()]).get(w.lower())
            if ph:
                return ph
    if G2P_ENGINE == "espeak-line":                # the apostrophe stays: "I'll" is aI_l, never "ill"
        w = _speller_word(word)
        if w and w.replace("'", "").upper() not in WORD_G2P:
            ph = espeak_words([w]).get(w)
            if ph:
                return ph
    return g2p_rules(word)


def _speller_word(word: str) -> str:
    """A word as espeak should hear it: letters, digits and the apostrophe ("we're", "9:30" keeps its digits)."""
    return re.sub(r"[^a-z0-9':]", "", (word or "").lower().replace("\u2019", "'")).strip("':")


def _phone_cost(a: list[str], b: list[str]) -> float:
    """Edit distance between two phone lists, over the longer one's length (0 = the same sounds)."""
    n, m = len(a), len(b)
    if not n or not m:
        return float(bool(n or m))
    row = list(range(m + 1))
    for i in range(1, n + 1):
        prev, row[0] = row[0], i
        for j in range(1, m + 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
    return row[m] / max(n, m)


def _split_by(phones: list[str], sizes: list[int]) -> list[list[str]]:
    """One espeak word that covers several of ours ("in the" = I_n_D_@2): its phones cut in proportion to each
    word's own spelling."""
    total = max(1, sum(sizes))
    out, at = [], 0
    for k, size in enumerate(sizes):
        end = len(phones) if k == len(sizes) - 1 else min(len(phones), at + max(1, round(size * len(phones) / total)))
        out.append(phones[at:end])
        at = end
    return out


_LINE_CACHE: dict[str, list[list[str]] | None] = {}

# A LONG LINE IS PAIRED IN SECONDS, NOT MINUTES (2026-10-23, the demo lap): the pairing below lets one of our words take
# any run of espeak's words, so its cost grew as words^3 x phones^2 - a 918-character case brief held house-speak on its
# main thread for 163 s after her voice ended (py-spy: espeak_line -> _phone_cost), the player never got its end, and the
# lap's timeout killed the line into ~100 s of dead air. A run cannot be capped: measured on 402 lines, a date takes 7-8
# of espeak's words and an address 10. So the search is PRUNED, exactly: a first pass with runs of at most two
# (_pair_bound) gives a cost the answer cannot exceed, and the full pass skips any step whose cost so far plus its run
# penalty already exceeds it (every cost is >= 0, so such a step is on no path that could win). Same pairing, word for
# word (pinned). Kill: HOUSE_MOUTH_PHONES_PRUNE=0 (the full search).
LINE_PRUNE = os.environ.get("HOUSE_MOUTH_PHONES_PRUNE", "1").strip().lower() not in ("0", "false", "no", "off")


def _pair_dp(alone: list[list[str]], toks: list[list[str]], bound: float, wide: int = 0):
    """(best, back) of the pairing: our word i takes espeak's j..j+k, or espeak's word j covers ours i..i+g; a run of
    k (or g) costs k-1 more. A step is skipped when its cost so far, plus what is left (each run of k shifts the two
    word counts by k-1 and costs at least that, so |words left on one side - the other| more), exceeds `bound`. A run's
    edit distance grows a column per phone instead of being redone per run. `wide` > 0 limits runs (the bound pass)."""
    n, m = len(alone), len(toks)
    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    bound = bound + 1e-9                                  # float sums: the bound's own path must survive
    for i in range(n + 1):
        for j in range(m + 1):
            b0 = best[i][j]
            if b0 == inf or b0 + abs((m - j) - (n - i)) > bound:
                continue
            if i < n:
                a = alone[i]
                col = list(range(len(a) + 1))            # edit distance of a against espeak's j..j+k, a column a phone
                got = 0
                kmax = m - j if not wide else min(m - j, wide)
                for k in range(1, kmax + 1):               # our word i takes espeak's j..j+k
                    if b0 + (k - 1) + abs((m - j - k) - (n - i - 1)) > bound:
                        if (m - j - k) - (n - i - 1) <= 0:  # past the diagonal: every longer run is worse still
                            break
                        col, got = _grow(col, a, toks[j + k - 1]), got + len(toks[j + k - 1])
                        continue
                    col, got = _grow(col, a, toks[j + k - 1]), got + len(toks[j + k - 1])
                    cost = (col[-1] / max(len(a), got)) if (a and got) else float(bool(a or got))
                    c = b0 + cost + (k - 1)
                    if c < best[i + 1][j + k]:
                        best[i + 1][j + k], back[i + 1][j + k] = c, (i, j, 1, k)
            gmax = n - i if not wide else min(n - i, wide)
            for g in range(2, gmax + 1):                   # espeak's word j covers our words i..i+g
                if j >= m or b0 + (g - 1) + abs((m - j - 1) - (n - i - g)) > bound:
                    if j >= m or (m - j - 1) - (n - i - g) >= 0:
                        break
                    continue
                want = [p for w in alone[i:i + g] for p in w]
                c = b0 + _phone_cost(want, toks[j]) + (g - 1)
                if c < best[i + g][j + 1]:
                    best[i + g][j + 1], back[i + g][j + 1] = c, (i, j, g, 1)
    return best, back


def _grow(col: list[int], a: list[str], phones: list[str]) -> list[int]:
    """The edit-distance column of `a` against a sequence, extended by `phones` (one column a phone)."""
    for ph in phones:
        nxt = [col[0] + 1]
        for r in range(1, len(col)):
            nxt.append(min(col[r] + 1, nxt[r - 1] + 1, col[r - 1] + (a[r - 1] != ph)))
        col = nxt
    return col


def _pair_bound(alone: list[list[str]], toks: list[list[str]]) -> float:
    """A cost the best pairing cannot exceed: the best with runs of at most two (inf when that cannot pair the line)."""
    best, _ = _pair_dp(alone, toks, float("inf"), wide=2)
    return best[len(alone)][len(toks)]


def espeak_line(words: list[str]) -> list[list[str]] | None:
    """Phones per word for a whole line from ONE espeak-ng call, in context, paired back to `words` by sound: a
    dynamic program over (our words, espeak's words) where one of ours may take several of espeak's ("9:30" = nine
    thirty) or one of espeak's may cover several of ours ("for the"), costed against each word's own spelling. None when
    espeak-ng is missing or a word is unknown to it (the caller spells word by word)."""
    key = " ".join(words)
    if key in _LINE_CACHE:
        return _LINE_CACHE[key]
    run = _espeak_cmd() if not _ESPEAK_DEAD else None
    if not run or not words:
        return None
    import subprocess
    cmd, env = run
    try:
        r = subprocess.run(cmd, input=key + "\n", capture_output=True, text=True, timeout=5, env=env)
        toks = [espeak_parse(t) for t in r.stdout.split()]
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not toks or any(t is None for t in toks):
        _LINE_CACHE[key] = None
        return None
    cite = espeak_words(words)
    alone = [cite.get(w) or [] for w in words]
    n, m = len(words), len(toks)
    best, back = _pair_dp(alone, toks, _pair_bound(alone, toks) if LINE_PRUNE else float("inf"))
    if best[n][m] == float("inf"):
        _LINE_CACHE[key] = None
        return None
    out: list[list[str]] = [[] for _ in range(n)]
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, g, k = back[i][j]
        if g == 1:
            out[pi] = [p for t in toks[pj:pj + k] for p in t]
        else:
            for x, part in enumerate(_split_by(toks[pj], [len(w) for w in alone[pi:pi + g]])):
                out[pi + x] = part
        i, j = pi, pj
    _LINE_CACHE[key] = out
    return out


def g2p_rules(word: str) -> list[str]:
    """Rule-based English letters → phones. Crude but honest; no g2p_en /
    phonemizer / nltk on this box, and we do not pip-install them.

    Diphthongs emit TWO phones (aye = AE then IY). Final I/Y as the word's
    only vowel is /aɪ/ ("hi", "I", "my") — not a short IH that would skip
    the open onset.
    """
    w = re.sub(r"[^A-Z]", "", (word or "").upper())
    if w in WORD_G2P:
        return list(WORD_G2P[w])
    if w == "I":
        return ["AE", "IY"]
    vowels = "AEIOU"
    if w.endswith("I") and sum(c in vowels for c in w) == 1:
        stem = g2p(w[:-1]) if w[:-1] else []
        return (stem if stem != ["-"] else []) + ["AE", "IY"]
    if w.endswith("Y") and sum(c in vowels for c in w[:-1]) == 0 and len(w) > 1:
        stem = g2p(w[:-1]) if w[:-1] else []
        return (stem if stem != ["-"] else []) + ["AE", "IY"]
    out: list[str] = []
    i = 0
    while i < len(w):
        hit = False
        for a, pair in DIPHTHONG.items():
            if w.startswith(a, i):
                out.extend(pair)
                i += len(a)
                hit = True
                break
        if hit:
            continue
        for a, b in DIGRAPH:
            if w.startswith(a, i):
                out.extend(b) if isinstance(b, tuple) else out.append(b)
                i += len(a)
                hit = True
                break
        if hit:
            continue
        ch = w[i]
        if ch == "E" and i == len(w) - 1 and len(w) > 2:
            pass  # silent final e
        elif ch == "Y" and i == 0 and i + 1 < len(w) and w[i + 1] in vowels:
            out.append("Y")
        else:
            sub = SINGLE.get(ch, ch)
            out.extend(sub) if isinstance(sub, tuple) else out.append(sub)
        i += 1
    return ["HH" if p == "H" else p for p in (out or ["-"])]


def class_of_phone(phone: str) -> str:
    p = (phone or "-").upper()
    return PHONE_TO_CLASS.get(p, "rest")


def viseme_label(phone: str) -> str:
    """Long name for the mark UI / labels.json. Same string the labeler wrote."""
    return CLASS_LONG.get(class_of_phone(phone), class_of_phone(phone))


def viseme_short(label: str | None) -> str:
    """'AH (open jaw)' → 'AH'; 'S/Z (teeth close)' → 'S/Z'; 'rest' → 'rest'."""
    s = str(label or "rest").strip()
    if not s:
        return "rest"
    if s.startswith("S/Z"):
        return "S/Z"
    if s.startswith("SH/CH"):
        return "SH/CH"
    return s.split()[0]


def viseme_of(v) -> str | None:
    """Canonical short class from a hop: string, labels.json long name, or int.

    Ints 0..len(CLASSES)-1 are CLASSES indices (rest=0). The retired 4-still
    bank also used 0..3 as rest/aa/ee/oh — 0 still means rest; 1..3 would
    collide with MBP/FV/TH, so only leftover hops from *before* the 13-class
    speak path can land wrong, and those hops retire in 0.5 s.
    """
    if v is None or v is False:
        return None
    if isinstance(v, str):
        if not v.strip() or v.strip() == "-":
            return None
        s = viseme_short(v)
        return s if s in CLASSES_SET else None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(CLASSES):
        return CLASSES[i]
    return None


def class_index(v) -> int:
    """Wire id for `/v1/mouth`. The wall ingest is chattr +i and only stores int."""
    name = viseme_of(v)
    return CLASS_INDEX.get(name or "rest", 0)


def expected_hops(text: str, hop_ms: float = 20.0) -> int:
    """ASSUMED ~12.5 letters/s (Yeah, the User? is ~0.9 s / 10 letters)."""
    letters = len(re.sub(r"\s+", "", text or ""))
    sec = max(0.4, letters * 0.08)
    return max(8, int(sec * 1000.0 / max(1.0, hop_ms)))


def text_to_visemes(text: str) -> list[str]:
    """Shape sequence for this sentence.

    Closed mouth at rest and after .!?. The one open-rest is REST_OPEN,
    and only between words (mid-sentence gap). Empty / no vowels is
    closed REST — never a courtesy AH. The line always finishes closed.
    """
    tokens = word_tokens((text or "").lower())
    _prefetch(tokens)
    out: list[str] = []
    for i, tok in enumerate(tokens):
        w = re.sub(r"[^a-z']+", "", tok)
        vs = _word_visemes(w)
        if not vs:
            continue
        out.extend(vs)
        if i < len(tokens) - 1:
            out.append(REST if ends_sentence(tok) else REST_OPEN)
    if not out:
        return [REST]
    if out[-1] == REST_OPEN:
        out[-1] = REST
    elif out[-1] != REST:
        out.append(REST)
    return _collapse(out)


def word_viseme_lists(text: str) -> list[list[str]]:
    """text_to_visemes grouped per word. No rest / rest-open glue.

    This is the `word_visemes` argument mouth_timing.schedule() wants.
    """
    out: list[list[str]] = []
    tokens = word_tokens((text or "").lower())
    if G2P_ENGINE == "espeak-line":
        line = _line_visemes(tokens)
        if line is not None:
            return line
    _prefetch(tokens)
    for tok in tokens:
        w = re.sub(r"[^a-z']+", "", tok)
        vs = _word_visemes(w)
        if vs:
            out.append(vs)
    return out


def viseme_for_hop(visemes: list, hop_i: int, n_expect: int):
    vs = visemes or [REST]
    n = len(vs)
    if n_expect <= 1:
        return vs[0]
    idx = min(n - 1, int(max(0, hop_i) * n / n_expect))
    return vs[idx]


# Voice pad / atempo quiet sits ~0.00–0.02; first voice ~0.12 (measured
# 2026-09-07 on the recut line). Below this the letter clock must not walk.
VOICE_RMS = 0.08


def stamp_voiced(
    visemes: list,
    rms: list[float],
    floor: float = VOICE_RMS,
) -> list:
    """Closed rest on quiet before the first voice and after the last.

    Voice PCM carries ~1 s of pad. Spreading the viseme sheet across those
    hops ate 'Okay' on silence and pushed 'variables' off the end of the
    take. Mid-line quiet (comma) is REST_OPEN. Voiced hops get `visemes`
    spread evenly across the voiced count, not the pad.
    """
    vs = visemes or [REST]
    n = len(rms)
    if n <= 0:
        return []
    voiced = [i for i, x in enumerate(rms) if float(x) >= floor]
    if not voiced:
        return [REST] * n
    lo, hi = voiced[0], voiced[-1]
    nv = len(voiced)
    nvs = len(vs)
    out: list = []
    k = 0
    for i, x in enumerate(rms):
        if i < lo or i > hi:
            out.append(REST)
        elif float(x) < floor:
            out.append(REST_OPEN)
        else:
            out.append(vs[min(nvs - 1, int(k * nvs / nv))])
            k += 1
    return out


def stamp_hops(
    visemes: list,
    n_hops: int,
    rms: list[float] | None = None,
) -> list:
    """One viseme per envelope hop. Energy-weighted when rms is given."""
    if n_hops <= 0:
        return []
    vs = visemes or [REST]
    n = len(vs)
    if rms and len(rms) >= n_hops:
        acc = 0.0
        totals: list[float] = []
        for x in rms[:n_hops]:
            acc += max(0.0, float(x))
            totals.append(acc)
        total = totals[-1] if totals else 0.0
        if total > 1e-9:
            return [vs[min(n - 1, int((a / total) * n))] for a in totals]
    return [viseme_for_hop(vs, i, n_hops) for i in range(n_hops)]


def load_class_index(src) -> dict[str, list[int]]:
    """tile indices per short class, sorted (strip is closed → open)."""
    if isinstance(src, (str, Path)):
        p = Path(src)
        if not p.is_file():
            return {}
        src = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(src, list):
        return {}
    out: dict[str, list[int]] = {}
    for row in src:
        if not isinstance(row, dict):
            continue
        try:
            ti = int(row.get("tile"))
        except (TypeError, ValueError):
            continue
        klass = viseme_of(row.get("viseme") or "rest")
        if not klass:
            continue
        out.setdefault(klass, []).append(ti)
    for k, v in list(out.items()):
        out[k] = sorted(set(v))
    return out


def pick_tile(
    n: int,
    level: float,
    viseme: str | int | None = None,
    by_class: dict[str, list[int]] | None = None,
    opens: list[float] | None = None,
) -> int:
    """Strip index. Class first; loudness among that class; else loudness.

    Missing class in this bank (Crissa's first strip had no ER) is loudness of
    the whole strip, not a neighbour class — do not invent a /r/ from /ah/.
    """
    n = int(n)
    if n <= 1:
        return 0
    lvl = max(0.0, min(1.0, float(level)))
    i_loud = int(round(lvl * (n - 1)))
    klass = viseme_of(viseme) if viseme is not None else None
    if not klass or not by_class:
        return i_loud
    cand = [int(i) for i in by_class.get(klass, []) if 0 <= int(i) < n]
    if not cand and klass in CLASS_FALLBACK:          # a new class with no tile yet: its fallback's, never blank
        klass = CLASS_FALLBACK[klass]
        cand = [int(i) for i in by_class.get(klass, []) if 0 <= int(i) < n]
    if not cand:
        return i_loud
    if klass in CLOSED and opens:
        closed = [
            i for i in cand
            if i < len(opens) and float(opens[i]) <= CLOSED_OPEN
        ]
        if closed:
            cand = closed
        elif klass == "MBP":
            # Live back bank: all 7 MBP labels sit at open 0.74–1.15
            # (word-alignment guessed /m/ on open frames). Do not paint those
            # as /m/ — rest's actually-shut tiles stand in until the shoot
            # fills a real closure.
            rest = [
                int(i) for i in by_class.get("rest", []) if 0 <= int(i) < n
            ]
            rest_closed = [
                i for i in rest
                if i < len(opens) and float(opens[i]) <= CLOSED_OPEN
            ]
            if rest_closed:
                cand = rest_closed
    cand = sorted(set(cand))
    j = int(round(lvl * (len(cand) - 1)))
    return cand[j]


def _prefetch(tokens) -> None:
    """One espeak call for the whole line instead of one per word."""
    if G2P_ENGINE == "espeak":
        espeak_words([re.sub(r"[^a-z]", "", t.lower()) for t in tokens])
    elif G2P_ENGINE == "espeak-line":
        espeak_words([w for w in (_speller_word(t) for t in tokens) if w])


def _line_visemes(tokens: list[str]) -> list[list[str]] | None:
    """word_viseme_lists under HOUSE_MOUTH_PHONES: the line's words in context (espeak_line), the User's WORD_G2P hand
    calls and a lone letter's name still first. Same words kept as the word-by-word path keeps, so the aligner's spans
    pair the same way. None = espeak-ng could not spell the line (word by word then)."""
    keep = [(tok, _speller_word(tok)) for tok in tokens]
    keep = [(tok, w) for tok, w in keep if w and _word_visemes(re.sub(r"[^a-z']+", "", tok))]
    ph = espeak_line([w for _, w in keep])
    if ph is None:
        return None
    out = []
    for (tok, w), phones in zip(keep, ph):
        u = re.sub(r"[^a-z]", "", w)
        if u.upper() in WORD_G2P or (len(u) == 1 and u in LETTER_NAMES and letter_names_on()):
            vs = _word_visemes(re.sub(r"[^a-z']+", "", tok))
        else:
            vs = _collapse([class_of_phone(p) for p in phones]) or _word_visemes(re.sub(r"[^a-z']+", "", tok))
        out.append(vs)
    return out


def phones_status() -> str:
    """For the log: what HOUSE_MOUTH_PHONES is spelling with right now."""
    if not PHONES_ON:
        return "phones: off"
    run = _espeak_cmd()
    return "phones: espeak-ng (%s)" % run[0][0] if run else "phones: no espeak-ng, letter rules"


# A LONE LETTER IS SAID BY ITS NAME (round 13, .101 2026-10-12: on the lip-sync test line the User "missed the last"
# words). A pack has no espeak-ng (the house's is piper's private copy), so its words go through the letter rules, which
# read "p", "b" and "m" as their bare consonants: the test line's "every p, b and m." held her lips shut for 0.79 s
# through "pee, bee" and "em" had no open at all. A one-letter word other than "a" and "I" is spoken as its name, so
# it takes the name's phones (the module's own diphthong spellings: EY = EH IY, AY = AE IY, OW = AO UW). WORD_G2P
# still wins. On in a pack, off on the house (its espeak reads letter names already, and its sheets do not change);
# HOUSE_MOUTH_LETTER_NAMES=1|0 forces it.
LETTER_NAMES = {
    "b": ["B", "IY"], "c": ["S", "IY"], "d": ["D", "IY"], "e": ["IY"], "f": ["EH", "F"], "g": ["JH", "IY"],
    "h": ["EH", "IY", "CH"], "j": ["JH", "EH", "IY"], "k": ["K", "EH", "IY"], "l": ["EH", "L"], "m": ["EH", "M"],
    "n": ["EH", "N"], "o": ["AO", "UW"], "p": ["P", "IY"], "q": ["K", "Y", "UW"], "r": ["AA", "R"], "s": ["EH", "S"],
    "t": ["T", "IY"], "u": ["Y", "UW"], "v": ["V", "IY"], "w": ["D", "AH", "B", "AH", "L", "Y", "UW"],
    "x": ["EH", "K", "S"], "y": ["W", "AE", "IY"], "z": ["Z", "IY"],
}
_LETTER_NAMES: dict = {}


def letter_names_on() -> bool:
    raw = os.environ.get("HOUSE_MOUTH_LETTER_NAMES", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    if "on" not in _LETTER_NAMES:
        try:
            import crissa_home
            _LETTER_NAMES["on"] = not crissa_home.is_house()
        except Exception:  # noqa: BLE001 - no state home to ask: the rules as they were
            _LETTER_NAMES["on"] = False
    return bool(_LETTER_NAMES["on"])


def _word_visemes(w: str) -> list[str]:
    u = re.sub(r"[^a-z]", "", (w or "").lower())
    if len(u) == 1 and u in LETTER_NAMES and u.upper() not in WORD_G2P and letter_names_on():
        return _collapse([class_of_phone(p) for p in v2_glides(LETTER_NAMES[u])])
    seq = [class_of_phone(p) for p in v2_glides(g2p(w))]
    return _collapse(seq)


def _collapse(seq: list[str]) -> list[str]:
    out: list[str] = []
    for v in seq:
        if not out or out[-1] != v:
            out.append(v)
    return out
