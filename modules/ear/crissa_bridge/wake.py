"""Free-air vocative wake: Crissa, plus the named crew (the User 2026-10-10: she answers to "Crissa" and "Hey Crissa").

Everyone is a *strict vocative*, so a sentence that merely mentions a name ("I told Crissa about it") does not
dispatch: hey/hi/ok/okay/yo + name, a leading "Name," / "Name.", the name alone, or her name as word 1 or 2 (the
speech-to-text service garbles the greeting or drops the comma).

HER NAME AS THE EAR HEARS IT (measured 2026-10-10, never guessed). The pack's own speech-to-text (ARK-ASR 0.6B INT8,
modules/voice) never spelled her "Crissa" (0 of 352 utterances): over the pack's shipped voices (Pocket's 18 stock
voices, Kokoro af_heart, af_bella, bf_emma, am_michael; clean and with room noise) it wrote Chrissa, Chrisa, Krisa,
Prisa, Chrysa, ... CRISSA_HEARD is every spelling it produced for her name, each counted as her name; the
transcripts are the fixture tests/ear/bridge/fixtures/crissa-wake-asr.json. A spelling that is also an ordinary word
or a common name (Chris, Krista, Person) is NOT in the set even where the ear produced it, so ordinary speech never
wakes her (NOT_HER, held by a test). Her old name no longer wakes her.

route:
  direct  - the bridge asks the Connect endpoint as that profile (her: profile id `crissa`, a code name until v2)
  summon  - still her endpoint; she files a board card for <profile>

The `kind` values (hey_zoe, zoe_solo, crissa, zoe_weak) are wire keys the bridge and its logs read; they keep their
names until the v2 code-name pass.
"""
from __future__ import annotations

import os
import re

# Her name and every spelling the pack's speech-to-text produced for it, with how often (2026-10-10: 22 voices x 8
# phrases x 2 rooms = 352 utterances of her name). Measured, never guessed; ordered by count.
CRISSA_HEARD: tuple[str, ...] = (
    "crissa",                                   # her name (the ear never wrote it this way: 0 of 352)
    "chrissa",      # 150
    "chrisa",       # 131
    "krisa",        # 16
    "prisa",        # 12
    "chrysa",       # 9
    "chrissar",     # 4
    "prissa",       # 3
    "kressa",       # 2
    "grissa",       # 2
    "crisa",        # 1
    "krisen",       # 1
    "chriso",       # 1
    "chrisso",      # 1
    "chrissel",     # 1
    "quissa",       # 1
    "kissa",        # 1
    "vikissa",      # 1  ("Hi Crissa" run together)
    "kokrissa",     # 1  ("Okay Crissa" run together)
)
# Never her, wherever they stand: ordinary words and common names near her name. The ear also wrote "Chris" (6),
# "Krista" (4) and "Person" (1) for her name; those stay out (someone in the room may be a Chris), so those 11 of 352
# do not wake her. The rest are what the ear wrote for the ordinary sentences of the measurement.
NOT_HER: tuple[str, ...] = ("chris", "christa", "krista", "kristy", "kristen", "kristin", "crisp", "crisper",
                            "crispr", "crisis", "crystal", "cristal", "christmas", "clarissa", "carissa", "corissa",
                            "melissa", "alissa", "alisa", "lisa", "prista", "priss", "person")

_GREET = r"(?:hey|hi|ok|okay|yo)"

# Heard for her name ONLY right after "hey", as the utterance's first two words (the User 2026-10-10: on .101 the one
# missed wake was heard "Hey, Prison."). "Prison" is an ordinary word, so alone, as word 1 or 2 without the "hey", after
# hi/ok/yo, or anywhere later in a sentence it is never her. Kept because the measured ordinary sentences
# (tests/ear/bridge/fixtures/crissa-wake-asr.json, every `nowake` row) still wake her 0 times with it in; a test holds
# that, and the line comes out the day one does.
HEY_ONLY: tuple[str, ...] = ("prison",)
_HEY_ONLY = re.compile(r"(?i)^\s*hey[,\s]+(?:%s)\b" % "|".join(re.escape(a) for a in HEY_ONLY))
# .101 round 28 (the User 2026-10-17: "will you ask to add krista and chris to wake words?"; that afternoon the ear wrote
# his "Crissa" as "Hey, Krista. Could you read..." 14:4x, "Chris, can you put that back on the screen" 15:20:53 and
# "Chris, please put that back on the screen for me." 15:21:16 - each one skipped, she said nothing). These stay NOT_HER
# anywhere else; they wake her only as an ADDRESS at the very start: after a greeting ("Hey Chris, ..."), or as the first
# word with its own comma/colon/!/? ("Chris, please ..."). "I told Chris", "Chris is coming", "Krista" alone never do.
# Kill EAR_BRIDGE_WAKE_ADDRESS_ONLY=0.
ADDRESS_ONLY: tuple[str, ...] = ("chris", "christa", "krista")
_ADDRESS_ONLY = re.compile(r"(?i)^\s*(?:%s[,\s]+)?(?:%s)\s*[,:!?]+\s*|^\s*%s[,\s]+(?:%s)\b[\s,.!?]*" % (
    "(?:hey|hi|ok|okay|yo)", "|".join(re.escape(a) for a in ADDRESS_ONLY),
    "(?:hey|hi|ok|okay|yo)", "|".join(re.escape(a) for a in ADDRESS_ONLY)))


def _address_only_on() -> bool:
    return os.environ.get("EAR_BRIDGE_WAKE_ADDRESS_ONLY", "1").strip().lower() not in ("0", "false", "off", "no")


def _alias_re(aliases: tuple[str, ...]) -> str:
    return "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))


_HER = _alias_re(CRISSA_HEARD)
_NAME = re.compile(rf"(?i)\b(?:{_HER})\b")
_HEY_NAME = re.compile(rf"(?i)\b{_GREET}[,\s]+(?:{_HER})\b")
_NAME_ALONE = re.compile(rf"(?i)^(?:{_HER})[\s,.!?…]*$")

# Ada keeps her own route. Argus is gone: his vocative reaches her.
DIRECT_PROFILES = frozenset({"crissa", "ada"})
SUMMON_PROFILES = frozenset({"lila", "vera", "steward", "bash", "argus"})

# canonical -> spoken aliases (ASR included)
_CREW_ALIASES: dict[str, tuple[str, ...]] = {
    "lila": ("lila", "leila", "leela", "leya"),
    "vera": ("vera",),
    "steward": ("steward",),
    "bash": ("bash",),
    "ada": ("ada",),
    "argus": ("argus",),
}


def _strict_vocative(text: str, aliases: tuple[str, ...]) -> str | None:
    """Return residual if `text` is a vocative for these aliases, else None."""
    raw = (text or "").strip()
    if not raw:
        return None
    names = _alias_re(aliases)
    hey = re.compile(rf"(?i)\b{_GREET}[,\s]+(?:{names})\b")
    solo = re.compile(rf"(?i)^(?:{names})[\s,.!?…]*$")
    lead_punct = re.compile(rf"(?i)^(?:{names})[\s]*[,:!?]+\s*")
    if hey.search(raw):
        residual = hey.sub(" ", raw)
        residual = re.sub(rf"(?i)\b(?:{names})\b", " ", residual)
        return re.sub(r"\s+", " ", residual).strip(" ,.-")
    if solo.match(raw):
        return ""
    m = lead_punct.match(raw)
    if m:
        return raw[m.end() :].strip(" ,.-")
    return None


_NAME_TOKEN = re.compile(r"[A-Za-z']+")


def _name_in_first_two(raw: str) -> tuple[int, str] | None:
    """If her name is word 1 or 2, return (index, residual after the name).

    The speech-to-text service often garbles the greeting ("Hay Chrissa", "Uh Krisa") or drops the comma after a
    leading name. A mention stays word 3+ and misses.
    """
    spans = list(_NAME_TOKEN.finditer(raw))
    aliases = set(CRISSA_HEARD)
    for i, m in enumerate(spans[:2]):
        if m.group(0).lower() in aliases:
            return i, raw[m.end() :].strip(" ,.-!?…")
    return None


def _her_match(raw: str) -> dict | None:
    m = _HEY_ONLY.match(raw) or (_ADDRESS_ONLY.match(raw) if _address_only_on() else None)
    if m:
        return {"hit": True, "kind": "hey_zoe", "residual": raw[m.end():].strip(" ,.-"), "profile": "crissa",
                "route": "direct"}
    slot = _name_in_first_two(raw)
    residual = _strict_vocative(raw, CRISSA_HEARD)
    if residual is None and slot is not None:
        residual = slot[1]
    if residual is None:
        if _NAME.search(raw):
            return {"hit": False, "kind": "zoe_weak", "residual": raw, "profile": None, "route": None}
        return None
    if _HEY_NAME.search(raw) or (slot is not None and slot[0] == 1):
        kind = "hey_zoe"
    elif _NAME_ALONE.match(raw) or residual == "":
        kind = "zoe_solo"
    else:
        kind = "crissa"
    return {"hit": True, "kind": kind, "residual": residual, "profile": "crissa", "route": "direct"}


def wake_match(text: str) -> dict:
    """Return {hit, kind, residual, profile, route} for the bridge."""
    raw = (text or "").strip()
    empty = {"hit": False, "kind": None, "residual": "", "profile": None, "route": None}
    if not raw:
        return empty

    her = _her_match(raw)
    if her is not None:
        return her

    # Earliest vocative wins if several names appear.
    best: tuple[int, str, str] | None = None  # (index, profile, residual)
    for profile, aliases in _CREW_ALIASES.items():
        residual = _strict_vocative(raw, aliases)
        if residual is None:
            continue
        names = _alias_re(aliases)
        m = re.search(rf"(?i)\b(?:{names})\b", raw)
        idx = m.start() if m else 10_000
        if best is None or idx < best[0]:
            best = (idx, profile, residual)

    if not best:
        return {**empty, "residual": raw}

    _, profile, residual = best
    route = "direct" if profile in DIRECT_PROFILES else "summon"
    return {"hit": True, "kind": profile, "residual": residual, "profile": profile, "route": route}


# ── BARGE-IN (round 14, the User 2026-10-12, .101: "stop does not work" - "hey Chris, stop!" over her reading). Over her
# own line her name is wider than a wake: the ear wrote her "Chris" when he stopped her (NOT_HER keeps it from WAKING
# her, because someone in the room may be a Chris; while she is talking, a vocative "Chris" is her). Only the bridge's
# name-interrupt reads this, and only while she speaks or answers. Kill: RAIL_BARGE_WIDE=0 (wake_match alone, as the
# house does).
BARGE_EXTRA: tuple[str, ...] = ("chris", "christa", "krista", "kris",
                                 # round 15 (.101 2026-10-12): written for "Crissa" over her reading
                                 "kreza", "tressa")
# Only in a vocative FORM ("Hey Christmas", "Christmas, stop") over her line: the User 2026-10-12 heard his "Crissa"
# come back as "Christmas"; alone or in a sentence ("Christmas dinner") it is the word, never her.
BARGE_FORM_ONLY: tuple[str, ...] = ("christmas",)
_BARGE_ALL = CRISSA_HEARD + BARGE_EXTRA + BARGE_FORM_ONLY
_BARGE_TAIL = re.compile(r"(?i)[,;]\s*(?:%s)[\s,.!?…]*$" % "|".join(re.escape(a) for a in _BARGE_ALL))


_BARGE_HEY = re.compile(r"(?i)\b%s[,\s]+(?:%s)\b[\s,.!?…]*" % (_GREET, "|".join(
    re.escape(a) for a in _BARGE_ALL)))
# her name FIRST, then a stop or a hold, no comma between (round 15, .101: "Kreza Wansan." for "Crissa, one second")
_BARGE_LEAD_STOP = re.compile(r"(?i)^(?:%s)\b[\s,.!?…]*(?=\S)" % "|".join(re.escape(a) for a in
                                                                        CRISSA_HEARD + BARGE_EXTRA))


def _ask_after(raw: str) -> str | None:
    """A barge-in's ask: the words AFTER "Hey <name>" (what stands before it in a chunk heard over her line is her own
    words the ear bent, .101 harness: "I was. Hey, Chris, stop."), or, when her name closes the sentence ("Now, one
    second, Chris,"), the words before it. None when neither form is there."""
    m = _BARGE_HEY.search(raw)
    if m:
        return raw[m.end():].strip(" ,.-")
    m = _BARGE_TAIL.search(raw)
    if m:
        return raw[:m.start()].strip(" ,.-")
    m = _BARGE_LEAD_STOP.match(raw)
    if m and stop_only(raw[m.end():]):
        return raw[m.end():].strip(" ,.-")
    return None


def barge_match(text: str) -> dict:
    """{hit, kind, residual, profile, route, via} for a barge-in: wake_match first; then, over her line, the strict
    vocative with "Chris" and its kin, or her name closing the sentence ("Now, one second, Chris,"). The residual is the
    ask that goes with the name (_ask_after), never words that stood before "Hey <name>"."""
    raw = (text or "").strip()
    wide = os.environ.get("RAIL_BARGE_WIDE", "1").strip().lower() not in ("0", "false", "no", "off")
    wm = wake_match(text)
    if wm.get("hit") and str(wm.get("profile") or "") == "crissa":
        ask = _ask_after(raw) if wide else None
        return {**wm, "residual": wm.get("residual") if ask is None else ask, "via": "wake"}
    miss = {"hit": False, "kind": None, "residual": raw, "profile": None, "route": None, "via": None}
    if not raw or not wide:
        return miss
    residual = _ask_after(raw)
    if residual is None:
        residual = _strict_vocative(raw, CRISSA_HEARD + BARGE_EXTRA)
    if residual is None:
        # "Christmas" only with punctuation or a greeting, never alone or inside a sentence
        lead = re.match(r"(?i)^(?:%s)[\s]*[,:!?]+\s*" % "|".join(BARGE_FORM_ONLY), raw)
        if lead:
            residual = raw[lead.end():].strip(" ,.-")
    if residual is None:
        return miss
    return {"hit": True, "kind": "barge", "residual": residual, "profile": "crissa", "route": "direct", "via": "barge"}


# What stops her and asks nothing (round 14): the house's cancel verbs (house_commands._CANCEL: cancel, abort, stop,
# never mind, no) and the holds (wait, quiet, hold on, one second, ...). A residual made only of these and filler is a
# stop: she stops at once and nothing is sent to the chat (sent, "stop" came back as a spoken "Okay, stopping.").
_STOPS = re.compile(r"\b(?:cancel|abort|stop(?:\s+talking)?|nevermind|never\s+mind|no|wait|quiet|be\s+quiet|hush|shh+|"
                    r"shush|hold\s+on|hang\s+on|hold\s+up|pause|enough|that'?s\s+enough|one\s+(?:second|sec|moment|minute)|"
                    r"(?:just\s+)?a\s+(?:second|sec|moment|minute)|"
                    # the ear's spellings of "one second" over her line (round 15, .101: "Kreza Wansan.")
                    r"wansan|wonsan|one\s*sec)\b", re.I)
_FILLER = re.compile(r"\b(?:now|please|okay|ok|just|so|uh|um|hey|alright|all\s+right|right|for|a|it|there|then)\b", re.I)


def stop_only(residual: str) -> bool:
    """True when the words after her name only tell her to stop or hold ("stop!", "Now, one second", "wait please")."""
    t = (residual or "").strip()
    if not t or not _STOPS.search(t):
        return False
    rest = _FILLER.sub(" ", _STOPS.sub(" ", t))
    return not re.sub(r"[^A-Za-z0-9']+", "", rest)


# ── HER NAME AS SHOWN (round 15 (f), the User 2026-10-12: "correct STT to Crissa in logs. prefer that spelling and over
# some of the mistranslations including christmas"). Where the name ADDRESSES her - a greeting before it, first in the
# utterance with punctuation after it, first and followed by a stop or a hold, or closing the sentence after a comma -
# the ear's spelling becomes "Crissa" in what is shown and logged. Her measured spellings (CRISSA_HEARD, which wake her
# already) always; the over-her-line ones (BARGE_EXTRA, BARGE_FORM_ONLY) only while she is on the air, so "Christmas
# dinner", "the crisper drawer", "Christa called" and "it's a crisis" stay as said and never wake her. Kill:
# RAIL_NAME_SHOWN=0.
SHOWN_NAME = "Crissa"


# "HEY" AND A NEAR-NAME FROM THE USER WAKES HER (round 16 (d), .101 2026-10-13 00:48:00 and 00:48:07: his "Hey Crissa"
# was written "Hey, Krista." twice, with his own voice recognised, and skipped `paused` - krista is in NOT_HER because
# someone in the room may be a Krista). When the recognised User opens an utterance with "Hey" and one of the names the
# ear writes for her over her line (BARGE_EXTRA, BARGE_FORM_ONLY: Chris, Christa, Krista, Kris, ...), as a bare call -
# nothing after the name - the name is hers: the line is read as "Hey, Crissa.". With words after it ("Hey Chris, what
# time is it?", one of the measured no-wake sentences, round 1's test 10) it is hers only while a pause of hers is fresh
# or she is on the air (`ask_ok`, the bridge's call): otherwise he may be talking to a Chris. Anyone else, any other
# greeting, and the name later in a sentence stay as they are. Kill: RAIL_USER_NEAR_WAKE=0.
def user_near_wake(text: str, *, ask_ok: bool = False) -> str | None:
    """`text` with the near-name after an opening "Hey" written SHOWN_NAME, or None. A bare call always; words after the
    name only with `ask_ok`. The caller decides that the voice is the recognised User's."""
    if os.environ.get("RAIL_USER_NEAR_WAKE", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    raw = text or ""
    alt = "|".join(re.escape(n) for n in sorted(BARGE_EXTRA + BARGE_FORM_ONLY, key=len, reverse=True))
    m = re.match(r"(?i)^(\s*hey[,\s]+)(?:%s)\b" % alt, raw)
    if not m:
        return None
    if re.sub(r"[\s,.!?…]+", "", raw[m.end():]) and not ask_ok:
        return None
    return m.group(1) + SHOWN_NAME + raw[m.end():]


def address_name(text: str, *, on_air: bool = False, user_voice: bool = False) -> str:
    """`text` with her name, where it addresses her, written SHOWN_NAME (and, round 16, an opening "Hey <near-name>"
    when the voice is the recognised User's)."""
    raw = text or ""
    if not raw.strip() or os.environ.get("RAIL_NAME_SHOWN", "1").strip().lower() in ("0", "false", "no", "off"):
        return raw
    if user_voice:
        raw = user_near_wake(raw) or raw
    names = list(CRISSA_HEARD) + (list(BARGE_EXTRA + BARGE_FORM_ONLY) if on_air else [])
    names = [n for n in names if n != "crissa"]
    if not names:
        return raw
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    out = re.sub(r"(?i)(\b%s[,\s]+)(?:%s)\b" % (_GREET, alt), lambda m: m.group(1) + SHOWN_NAME, raw)
    out = re.sub(r"(?i)^(\s*)(?:%s)(?=[\s]*(?:[,:!?.…]|$))" % alt, lambda m: m.group(1) + SHOWN_NAME, out)
    out = re.sub(r"(?i)([,;]\s*)(?:%s)(?=[\s,.!?…]*$)" % alt, lambda m: m.group(1) + SHOWN_NAME, out)
    if on_air:
        m = re.match(r"(?i)^(\s*)(?:%s)\b" % alt, out)
        if m and stop_only(out[m.end():]):
            out = m.group(1) + SHOWN_NAME + out[m.end():]
    return out
