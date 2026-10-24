"""Text quality: normalize, hallucination reject, dual-engine consensus."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_PUNCT_RE = re.compile(r"[^\w\s']+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_REPEAT_TOKEN_RE = re.compile(r"\b(\w+)(?:\s+\1){3,}\b", re.IGNORECASE)
_REPEAT_PHRASE_RE = re.compile(r"(.{8,40}?)(?:\s*\1){2,}", re.IGNORECASE | re.DOTALL)

# --- out-of-language output -------------------------------------------------
# Measured 2026-08-14: 97 of 9,975 transcribed turns (1.0%) came back as CJK in
# an English-speaking house — 你猜猜。 / 喵喵喵。 / 嗯。 — and two were stamped
# `second, certain=True`, i.e. the wall put Chinese words in a real person's
# mouth. The ghost list already blocked the single string 字幕, which shows
# someone met this before and patched one instance instead of the class.
#
# So: screen by SCRIPT, not by another blocklist entry. Anything whose letters
# are mostly outside the house language is decoder garbage, whatever it says.
_CJK_RE = re.compile(
    "[\u3040-\u30ff"      # hiragana / katakana
    "\u3400-\u4dbf"       # CJK ext A
    "\u4e00-\u9fff"       # CJK unified
    "\uf900-\ufaff"       # compatibility ideographs
    "\uac00-\ud7af]"      # hangul
)
# Stripping also takes CJK punctuation and fullwidth forms. Kept separate from
# _CJK_RE because foreign_script_fraction counts LETTERS -- adding punctuation
# there would not change the fraction, but it would muddy what the name means.
_CJK_STRIP_RE = re.compile(
    "[\u3000-\u303f"      # CJK symbols and punctuation, incl. the full stop 。
    "\u3040-\u30ff"
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\uf900-\ufaff"
    "\uff00-\uffef"       # fullwidth forms
    "\uac00-\ud7af]"
)
# Fraction of letters that may sit outside the expected script before the whole
# turn is called garbage. A borrowed word or a name should survive; a sentence
# should not. Override per site with RAIL_STT_FOREIGN_MAX.
_FOREIGN_MAX = 0.30


def foreign_script_fraction(text: str) -> float:
    """Share of LETTERS that are CJK/kana/hangul. Punctuation and digits ignored."""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _CJK_RE.match(c)) / len(letters)


def normalize_for_compare(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.lower().strip()
    t = _PUNCT_RE.sub(" ", t)
    t = _SPACE_RE.sub(" ", t).strip()
    return t


def strip_foreign(text: str) -> str:
    """Drop stray out-of-script characters from otherwise-English text.

    The decoder sometimes injects CJK mid-utterance -- measured examples:
    "in your\u5f1f\u7684house", "and\u4e0d\u4e0a\u3002". The sentence is mostly right, so
    rejecting the whole turn throws away a good transcript; but leaving the
    characters in puts words on the wall that nobody said. Strip and keep.

    Majority-foreign text is NOT repaired here -- `is_hallucination` rejects it
    outright, because there is no English underneath to save.
    """
    t = text or ""
    if not t or foreign_script_fraction(t) > _FOREIGN_MAX:
        return t
    out = _CJK_STRIP_RE.sub("", t)
    # collapse the spacing the removal leaves behind
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return out.strip()


# A REAL LINE MAY SAY "SUBSCRIBE" OR A WEB ADDRESS (round 46, the local review of kit crissa-063a2813, 2026-10-23): any
# line under 12 words that contained "subscribe" or "www." was dropped as a decoder ghost, so "Crissa, subscribe me to
# the newsletter" and "open www dot ..." never reached her. Those two ghosts now match only their ghost shapes - the
# channel sign-off ("please subscribe", "like and subscribe", "subscribe to my channel") and a credit line or a bare web
# address ("Subtitles by www.example.org", "www.example.org") - still under 12 words; the other ghosts are unchanged.
# Kill: CRISSA_EAR_GHOST_NARROW=0 (the substring rule).
_GHOSTS_PLAIN = ("thank you for watching", "thanks for watching", "bankrupt.com", "♪", "字幕")
_SUBSCRIBE_GHOST = re.compile(
    r"\b(?:please|and)\s+subscribe\b|\bsubscribe\s+(?:to|for)\s+(?:my|our|the|this)\s+channel\b"
    r"|\bdon'?t\s+forget\s+to\s+subscribe\b", re.I)
_WWW_GHOST = re.compile(
    r"^\W*www\.\S+\W*$|\b(?:subtitles?|captions?|transcri\w*|translat\w*|amara)\b.*\bwww\.", re.I)


def _NARROW_GHOSTS_ON() -> bool:
    import os as _os

    return _os.environ.get("CRISSA_EAR_GHOST_NARROW", "1").strip().lower() not in ("0", "false", "no", "off")


def _ghost_line(low: str) -> bool:
    """A short line that is a decoder ghost (the narrow rule)."""
    if len(low.split()) >= 12:
        return False
    if any(g in low for g in _GHOSTS_PLAIN):
        return True
    return bool(_SUBSCRIBE_GHOST.search(low) or _WWW_GHOST.search(low))


def is_hallucination(text: str) -> bool:
    """Catch classic ASR garbage: token loops, bankrupt.com spam, etc."""
    if not text or not text.strip():
        return False  # empty is abstain, not hallucinate
    t = text.strip()
    # Extreme length for short audio is handled elsewhere; here: loops
    if _REPEAT_TOKEN_RE.search(t):
        return True
    if _REPEAT_PHRASE_RE.search(t):
        return True
    # Known whisper silence ghosts
    low = t.lower()
    ghosts = (
        "thank you for watching",
        "thanks for watching",
        "subscribe",
        "bankrupt.com",
        "www.",
        "♪",
        "字幕",
    )
    if _NARROW_GHOSTS_ON():
        if _ghost_line(low):
            return True
    elif any(g in low for g in ghosts) and len(t.split()) < 12:
        return True
    # Out-of-language output. An English house does not produce sentences of
    # kanji; the decoder does, on noise. See _CJK_RE above.
    import os as _os

    try:
        cap = float(_os.environ.get("RAIL_STT_FOREIGN_MAX", _FOREIGN_MAX))
    except ValueError:
        cap = _FOREIGN_MAX
    if foreign_script_fraction(t) > cap:
        return True
    # Single token repeated via spacing variants
    words = normalize_for_compare(t).split()
    if len(words) >= 6:
        # majority single unique word
        from collections import Counter

        c = Counter(words)
        top, n = c.most_common(1)[0]
        if n / len(words) >= 0.7 and len(top) >= 2:
            return True
    return False


def is_prompt_echo(text: str, prompt: str | None) -> bool:
    """Whisper on near-silence sometimes emits (part of) its own initial_prompt."""
    if not text or not prompt:
        return False
    t = normalize_for_compare(text)
    if not t or len(t) < 8:
        return False
    return t in normalize_for_compare(prompt)


def token_f1(a: str, b: str) -> float:
    ta = set(normalize_for_compare(a).split())
    tb = set(normalize_for_compare(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == 0:
        return 0.0
    p = inter / len(tb)
    r = inter / len(ta)
    return 2 * p * r / (p + r)


@dataclass
class ConsensusResult:
    text: str
    confidence: float  # 0..1
    agreed: bool
    reason: str
    candidates: dict


def consensus(
    parakeet_text: str | None,
    parakeet_score: float | None,
    whisper_text: str | None,
    whisper_avg_logprob: float | None,
    whisper_no_speech: float | None,
    *,
    agree_f1: float = 0.72,
) -> ConsensusResult:
    """
    Merge dual engines into one answer.
    Prefer agreement; otherwise prefer non-empty non-hallucination with better score.
    """
    p = (parakeet_text or "").strip()
    w = (whisper_text or "").strip()
    cands = {
        "live-ear": {"text": p, "score": parakeet_score},
        "parakeet": {"text": p, "score": parakeet_score},
        "whisper": {
            "text": w,
            "avg_logprob": whisper_avg_logprob,
            "no_speech_prob": whisper_no_speech,
        },
    }

    if is_hallucination(p):
        p = ""
    if is_hallucination(w):
        w = ""

    # Whisper hard gate (mirrors known-good thresholds without importing legacy STT stacks)
    if w:
        if whisper_no_speech is not None and whisper_no_speech >= 0.55:
            w = ""
        if whisper_avg_logprob is not None and whisper_avg_logprob <= -1.05:
            w = ""

    if not p and not w:
        return ConsensusResult(
            text="",
            confidence=0.0,
            agreed=True,
            reason="both_empty_or_rejected",
            candidates=cands,
        )

    if p and not w:
        conf = 0.75
        if parakeet_score is not None:
            # parakeet scores are often negative log-likes; map softly
            conf = min(0.92, max(0.45, 0.55 + (parakeet_score + 10) * 0.02))
        return ConsensusResult(text=p, confidence=conf, agreed=False, reason="live_ear_only", candidates=cands)

    if w and not p:
        conf = 0.7
        if whisper_avg_logprob is not None:
            conf = min(0.9, max(0.4, (whisper_avg_logprob + 1.0) * 0.5 + 0.5))
        return ConsensusResult(text=w, confidence=conf, agreed=False, reason="whisper_only", candidates=cands)

    f1 = token_f1(p, w)
    if f1 >= agree_f1 or normalize_for_compare(p) == normalize_for_compare(w):
        # Prefer the longer of near-equal (usually more complete)
        text = p if len(p) >= len(w) * 0.9 else w
        # Prefer whisper punctuation if lengths similar and whisper has caps/.
        if abs(len(p) - len(w)) < 8 and any(ch in w for ch in ".?"):
            text = w
        return ConsensusResult(
            text=text,
            confidence=min(0.98, 0.85 + 0.15 * f1),
            agreed=True,
            reason=f"agree_f1={f1:.2f}",
            candidates=cands,
        )

    # Disagree: pick higher-trust
    # Prefer live-ear HTTP unless whisper much more confident
    pick = p
    reason = "disagree_prefer_live_ear"
    conf = 0.55
    if whisper_avg_logprob is not None and whisper_avg_logprob > -0.4:
        if parakeet_score is None or parakeet_score < 0:
            pick = w
            reason = "disagree_prefer_whisper_logprob"
            conf = 0.6
    return ConsensusResult(text=pick, confidence=conf, agreed=False, reason=reason, candidates=cands)
