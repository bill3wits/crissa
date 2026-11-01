"""Clothing / height traits + leave match scoring (object-side).

CONTRACT-CLOTHING-LEAVE.md — VL describes; object ranks and decrements.
"""
from __future__ import annotations

import re
from typing import Any

# Height Δ below this (door or frame frac) boosts match
HEIGHT_TIE_EPS = 0.08
TRAITS_MATCH_MIN = 0.35


def normalize_traits(raw: Any) -> dict[str, Any] | None:
    """Sanitize enter_traits / leave_traits from VL body."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("shirt", "hair", "bottom", "summary"):
        v = raw.get(key)
        if v is None:
            continue
        s = str(v).strip().lower()
        if s:
            out[key] = s[:80]
    for key in ("height_frac_door", "height_frac_frame"):
        v = raw.get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 <= f <= 1.5:
            out[key] = round(min(1.2, max(0.0, f)), 3)
    if not out:
        return None
    return out


# Compound garments so "short sleev" and "short-sleeve" are one token.
_COMPOUND = (
    (re.compile(r"t[\s\-]?shirts?\b"), "tshirt"),
    (re.compile(r"short[\s\-]?sleev(?:e|ed|s)?\b"), "shortsleeve"),
    (re.compile(r"long[\s\-]?sleev(?:e|ed|s)?\b"), "longsleeve"),
    (re.compile(r"\bsleev(?:e|ed|s)?\b"), "sleeve"),
)
_SYNONYM = {
    "tshirt": frozenset({"tshirt", "shirt", "tee"}),
    "tee": frozenset({"tshirt", "shirt", "tee"}),
    "shirt": frozenset({"tshirt", "shirt", "tee"}),
    "grey": frozenset({"gray", "grey"}),
    "gray": frozenset({"gray", "grey"}),
    "navy": frozenset({"navy", "blue"}),
    "blue": frozenset({"navy", "blue"}),
}
_COLORS = frozenset(
    {
        "black",
        "white",
        "gray",
        "grey",
        "blue",
        "navy",
        "teal",
        "green",
        "olive",
        "red",
        "maroon",
        "pink",
        "purple",
        "yellow",
        "orange",
        "brown",
        "tan",
        "beige",
        "khaki",
        "burgundy",
    }
)
_GARMENT_RE = (
    r"(?:short[\s\-]?sleev(?:e|ed)?|long[\s\-]?sleev(?:e|ed)?|"
    r"t[\s\-]?shirt|tee|shirt|top|blouse|sweater|cardigan|"
    r"hoodie|jacket|coat|robe|dress|shorts|pants|jeans|skirt|polo)"
)
_CLOTHES_STOP = re.compile(
    r"\b(?:walked|walks|walking|exits?|exited|left|leaving|into|from|"
    r"through|hallway|doorway|door|camera|person|someone|body)\b",
    re.I,
)


def _tokens(s: str) -> set[str]:
    raw = str(s or "").lower()
    for pat, repl in _COMPOUND:
        raw = pat.sub(repl, raw)
    out: set[str] = set()
    for t in re.split(r"[^a-z0-9]+", raw):
        if len(t) < 2:
            continue
        out.add(t)
        out.update(_SYNONYM.get(t, ()))
    return out


def _color_set(tr: dict[str, Any] | None) -> set[str]:
    toks = set()
    for key in ("shirt", "summary", "clothes"):
        v = (tr or {}).get(key)
        if v:
            toks |= _tokens(str(v))
    colors = set()
    for t in toks:
        if t in _COLORS:
            colors.update(_SYNONYM.get(t, {t}))
    return colors


def clothes_from_prose(text: str | None) -> str | None:
    """Pull a robe phrase out of Argus's 1–2 sentences when JSON clothes is empty."""
    t = " ".join(str(text or "").strip().split())
    if not t:
        return None
    brace = t.rfind("{")
    if brace > 0:
        t = t[:brace].strip()
    if not t:
        return None
    pats = (
        rf"(?:wearing|wore|in|with)\s+(?:a|an|the)\s+"
        rf"((?:[a-z0-9]+[\s,\-]*){{0,6}}{_GARMENT_RE}(?:\s+(?:shirt|top|tee))?)",
        rf"(?:wearing|wore|in|with)\s+"
        rf"((?:[a-z0-9]+[\s,\-]*){{0,6}}{_GARMENT_RE}(?:\s+(?:shirt|top|tee))?)",
        rf"\b((?:(?:dark|light|navy|olive|gray|grey|black|white|blue|green|"
        rf"red|pink|brown|tan|khaki|maroon)\s+)?"
        rf"(?:[a-z0-9]+[\s\-]*){{0,3}}{_GARMENT_RE}(?:\s+shirt)?)\b",
    )
    for p in pats:
        m = re.search(p, t, re.I)
        if not m:
            continue
        phrase = _CLOTHES_STOP.split(m.group(1), 1)[0]
        phrase = re.sub(r"\s+", " ", phrase).strip(" ,.-")
        if re.search(_GARMENT_RE, phrase, re.I) and len(phrase) >= 4:
            return phrase[:240]
    return None


def score_traits(leave: dict[str, Any] | None, stored: dict[str, Any] | None) -> float:
    """0–1 similarity: clothing primary, height tie-break."""
    if not leave or not stored:
        return 0.0
    score = 0.0
    weight = 0.0
    for key, w in (("shirt", 0.45), ("hair", 0.25), ("bottom", 0.15)):
        a, b = leave.get(key), stored.get(key)
        if not a or not b:
            continue
        weight += w
        ta, tb = _tokens(str(a)), _tokens(str(b))
        if not ta or not tb:
            continue
        inter = len(ta & tb)
        union = len(ta | tb) or 1
        score += w * (inter / union)
    # summary soft overlap
    sa, sb = leave.get("summary"), stored.get("summary")
    if sa and sb:
        weight += 0.1
        ta, tb = _tokens(str(sa)), _tokens(str(sb))
        inter = len(ta & tb)
        union = len(ta | tb) or 1
        score += 0.1 * (inter / union)
    # height: small delta boosts (tie-break, not sole ID)
    for key, w in (("height_frac_door", 0.08), ("height_frac_frame", 0.05)):
        if key not in leave or key not in stored:
            continue
        try:
            da = abs(float(leave[key]) - float(stored[key]))
        except (TypeError, ValueError):
            continue
        weight += w
        if da <= HEIGHT_TIE_EPS:
            score += w * (1.0 - da / max(HEIGHT_TIE_EPS, 1e-6))
        elif da < 0.2:
            score += w * 0.25
    if weight <= 0:
        return 0.0
    # normalize by available weights so partial traits still score
    return max(0.0, min(1.0, score / min(weight, 1.0)))


def best_traits_match(
    leave_traits: dict[str, Any] | None,
    candidates: dict[str, dict[str, Any]],
    *,
    min_score: float = TRAITS_MATCH_MIN,
) -> tuple[str | None, float]:
    """candidates: person_id -> traits_live. Returns (id, score) or (None, 0)."""
    if not leave_traits or not candidates:
        return None, 0.0
    best_id: str | None = None
    best_sc = 0.0
    for pid, tr in candidates.items():
        sc = score_traits(leave_traits, tr)
        if sc > best_sc:
            best_sc = sc
            best_id = pid
    if best_id is None or best_sc < min_score:
        return None, best_sc
    return best_id, best_sc


TIE_EPS = 0.08


def sex_mismatch(
    leave: dict[str, Any] | None, card: dict[str, Any] | None
) -> bool:
    """True when both sides name M/F and they disagree."""
    a = str((leave or {}).get("sex") or "").upper()[:1]
    b = str((card or {}).get("sex") or "").upper()[:1]
    return a in ("M", "F") and b in ("M", "F") and a != b


def leave_contradicts_card(
    leave: dict[str, Any] | None, card: dict[str, Any] | None
) -> bool:
    """True when the leave clip cannot be this enter card (sex / hard color)."""
    leave = leave or {}
    card = card or {}
    if sex_mismatch(leave, card):
        return True
    leave_tr = {
        "shirt": leave.get("clothes") or leave.get("shirt"),
        "summary": leave.get("clothes") or leave.get("shirt") or leave.get("summary"),
        "clothes": leave.get("clothes") or leave.get("shirt"),
    }
    stored = {
        "shirt": card.get("clothes") or card.get("shirt"),
        "summary": card.get("clothes") or card.get("shirt") or card.get("summary"),
        "clothes": card.get("clothes") or card.get("shirt"),
    }
    lc, cc = _color_set(leave_tr), _color_set(stored)
    return bool(lc and cc and not (lc & cc))


def match_leave_occupant(
    leave: dict[str, Any] | None,
    cards: list[dict[str, Any]],
    *,
    min_score: float = TRAITS_MATCH_MIN,
    tie_eps: float = TIE_EPS,
) -> tuple[str | None, float, dict[str, Any]]:
    """Pick the enter card whose clothes/hair/sex match the leave clip.

    Pure ranking. Does not write inventory. The visit apply subtracts
    whoever this returns.
    """
    leave = leave or {}
    clothes = leave.get("clothes") or leave.get("shirt")
    hair = leave.get("hair")
    sex = str(leave.get("sex") or "").upper()[:1]
    if not (clothes or hair or sex):
        return None, 0.0, {}
    leave_tr = {"shirt": clothes, "summary": clothes, "hair": hair}
    ranked: list[tuple[str, float]] = []
    for c in cards or []:
        pid = str(c.get("person_id") or "").strip().lower()
        if not pid or pid == "unknown":
            continue
        stored = {
            "shirt": c.get("clothes") or c.get("shirt"),
            "summary": c.get("clothes") or c.get("shirt") or c.get("summary"),
            "hair": c.get("hair"),
        }
        sc = score_traits(leave_tr, stored)
        lc, cc = _color_set(leave_tr), _color_set(stored)
        if lc and cc and not (lc & cc):
            sc *= 0.1
        elif lc and cc and (lc & cc):
            sc = min(1.0, sc + 0.15)
        c_sex = str(c.get("sex") or "").upper()[:1]
        if sex in ("M", "F") and c_sex in ("M", "F"):
            if sex == c_sex:
                sc = min(1.0, sc + 0.25)
            else:
                sc *= 0.1
        ranked.append((pid, sc))
    ranked.sort(key=lambda kv: kv[1], reverse=True)
    why = {
        "scores": {p: round(s, 3) for p, s in ranked},
        "clothes": clothes,
        "hair": hair,
        "sex": sex or None,
    }
    if not ranked:
        return None, 0.0, why
    best, sc = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if sc < min_score:
        return None, sc, why
    if len(ranked) > 1 and (sc - second) < tie_eps:
        return None, sc, {**why, "tie": True}
    return best, sc, why
