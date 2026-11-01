"""ID matrix VIEW — isUser gate + soft who-of-N scores (shadow-first).

Rebuildable each turn from presence + house-id + emb. Never authoritative auth.
Plan rev2 + Fable REVIEW-ORACLE-ID-MATRIX-20260806.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import id_matrix_config as cfg


# THE USER IS THE ID INIT GAVE (round 46, the local review of kit crissa-063a2813, 2026-10-23): every "is this the
# User?" test here compared against the literal "user" - the house's id for its owner. A pack's User is the person
# Init's Voice ID enrolled (person.json role "user", named by their first name), so the guard below never fired and a
# sole-present stamp named any voice as the User with no voice check. The User is house_id.user_id() (HOUSE_USER_ID,
# else RAIL_PRIMARY_USER, else role "user" in the identity store, else "user"), and the literal still counts, so the
# house reads exactly as before. Kill: CRISSA_EAR_CANON_USER=0 (the literal alone).
GENERIC_USER = "user"


def canon_user_on() -> bool:
    import os

    return os.environ.get("CRISSA_EAR_CANON_USER", "1").strip().lower() not in ("0", "false", "no", "off")


def user_ids() -> set[str]:
    """Every id that names the User: the literal, and the canonical User's id (house_id.user_id)."""
    ids = {GENERIC_USER}
    if canon_user_on():
        try:
            from .house_id import user_id

            u = str(user_id() or "").strip().lower()
            if u:
                ids.add(u)
        except Exception:  # noqa: BLE001 - an unreadable store: the literal alone, as before
            pass
    return ids


def is_user_id(pid: str | None) -> bool:
    return bool(pid) and str(pid).strip().lower() in user_ids()


def user_key(names) -> str:
    """Which of `names` (voice-bank columns) is the User's: the canonical id when it is among them, else the literal."""
    have = {str(n).strip().lower() for n in (names or ())}
    for u in sorted(user_ids() - {GENERIC_USER}):
        if u in have:
            return u
    return GENERIC_USER


@dataclass
class IsUserResult:
    is_user: bool | None  # True / False / None=uncertain
    score: float
    reason: str
    user_sim: float
    best_other_id: str | None
    best_other_sim: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_user": self.is_user,
            "is_user_score": round(self.score, 4),
            "is_user_reason": self.reason,
            "user_sim": round(self.user_sim, 4),
            "best_other_id": self.best_other_id,
            "best_other_sim": round(self.best_other_sim, 4),
        }


@dataclass
class MatrixResult:
    scores: dict[str, float] = field(default_factory=dict)
    votes: dict[str, dict[str, float]] = field(default_factory=dict)
    winner: str | None = None
    margin: float = 0.0
    source: str = "matrix_spoke"
    is_user: bool | None = None
    eligible: list[str] = field(default_factory=list)
    crossing_index: int | None = None  # reserved F3/P9

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scores"] = {k: round(v, 4) for k, v in self.scores.items()}
        d["margin"] = round(self.margin, 4)
        return d


def build_columns(
    known: list[str],
    regulars: list[str],
    *,
    u_slots: list[str] | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    """Closed N: known ∪ regulars ∪ U ∪ extra; TTS filters excluded."""
    skip = {"crissa_her_tts", "crissa-her-tts", "tts_echo", "announcer", "crew_tts"}
    out: list[str] = []
    for n in list(known) + list(regulars) + list(u_slots or []) + list(extra or []):
        if not n:
            continue
        key = str(n).strip().lower()
        if key in skip or key in out:
            continue
        out.append(key)
    return out[:8]  # soft cap ~6–8


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def is_user_gate(
    *,
    user_sim: float,
    best_other_id: str | None,
    best_other_sim: float,
    media_on: bool = False,
    tts_veto: bool = False,
    voice_usable: bool = True,
) -> IsUserResult:
    """Phase A — cheap isUser? (never true on media/TTS; impostor-safe floor)."""
    user_sim = float(user_sim) if user_sim is not None else -1.0
    best_other_sim = float(best_other_sim) if best_other_sim is not None else -1.0
    other = (best_other_id or "").strip().lower() or None
    if is_user_id(other):
        other = None
        best_other_sim = -1.0

    if tts_veto:
        return IsUserResult(
            is_user=False,
            score=0.0,
            reason="tts_veto",
            user_sim=max(0.0, user_sim),
            best_other_id=other,
            best_other_sim=max(0.0, best_other_sim),
        )
    if media_on:
        return IsUserResult(
            is_user=False,
            score=0.0,
            reason="media",
            user_sim=max(0.0, user_sim),
            best_other_id=other,
            best_other_sim=max(0.0, best_other_sim),
        )
    if not voice_usable or user_sim < 0:
        return IsUserResult(
            is_user=None,
            score=0.0,
            reason="no_emb",
            user_sim=max(0.0, user_sim),
            best_other_id=other,
            best_other_sim=max(0.0, best_other_sim),
        )

    # is_user = false when another person clearly leads (abstain-safe)
    if (
        other
        and best_other_sim >= cfg.NOT_USER_SIM
        and best_other_sim >= user_sim + cfg.NOT_USER_MARGIN
    ):
        return IsUserResult(
            is_user=False,
            score=_clamp01(best_other_sim),
            reason="voice_other_leads",
            user_sim=max(0.0, user_sim),
            best_other_id=other,
            best_other_sim=max(0.0, best_other_sim),
        )

    # is_user = true only above impostor floor AND margin over best other
    if user_sim >= cfg.IS_USER_SIM and user_sim >= best_other_sim + cfg.IS_USER_MARGIN:
        return IsUserResult(
            is_user=True,
            score=_clamp01(user_sim),
            reason="voice_margin",
            user_sim=max(0.0, user_sim),
            best_other_id=other,
            best_other_sim=max(0.0, best_other_sim),
        )

    return IsUserResult(
        is_user=None,
        score=_clamp01(max(user_sim, best_other_sim, 0.0)),
        reason="uncertain",
        user_sim=max(0.0, user_sim),
        best_other_id=other,
        best_other_sim=max(0.0, best_other_sim),
    )


def _person_sex(person_id: str) -> str | None:
    try:
        from pathlib import Path
        import json
        import os

        from .house_id import house_id_root      # was never imported: a NameError the except hid (2026-10-10)

        root = Path(
            os.environ.get(
                "HOUSE_ID_ROOT",
                str(house_id_root()),
            )
        )
        pj = root / "people" / person_id / "person.json"
        if not pj.is_file():
            return None
        data = json.loads(pj.read_text(encoding="utf-8"))
        g = data.get("sex") or data.get("gender")
        if not g:
            return None
        g = str(g).strip().upper()[:1]
        return g if g in ("M", "F") else None
    except Exception:
        return None


def _bank_n(person_id: str) -> int:
    try:
        from .voice_bank import load_voice_bank

        bank = load_voice_bank()
        return len(bank.get(person_id, []) or [])
    except Exception:
        return 0


def score_spoke(
    columns: list[str],
    *,
    voice_sims: dict[str, float] | None = None,
    face_sims: dict[str, float] | None = None,
    traits_scores: dict[str, float] | None = None,
    live_sex: str | None = None,
    home_beams: dict[str, int] | None = None,
    seat_guess: int | None = None,
    on_roster: set[str] | None = None,
    sole_name: str | None = None,
    is_user: bool | None = None,
) -> MatrixResult:
    """Soft who-spoke scores; the user excluded from winner if is_user is False."""
    voice_sims = voice_sims or {}
    face_sims = face_sims or {}
    traits_scores = traits_scores or {}
    home_beams = home_beams or {}
    on_roster = on_roster or set()
    weights = cfg.spoke_weights()
    scores: dict[str, float] = {}
    votes: dict[str, dict[str, float]] = {}

    for col in columns:
        c = col.lower()
        v_voice = _clamp01(float(voice_sims.get(c, 0.0)))
        # thin bank softens voice contribution (Visitor n=2)
        if _bank_n(c) < cfg.THIN_BANK_N:
            v_voice *= cfg.THIN_BANK_VOICE_SCALE
        v_face = _clamp01(float(face_sims.get(c, 0.0)))
        v_appear = _clamp01(float(traits_scores.get(c, 0.0)))
        # sex soft: boost only, never penalty
        v_sex = 0.0
        if live_sex in ("M", "F"):
            px = _person_sex(c)
            if px == live_sex:
                v_sex = 1.0
        # Beam recorded for logs; weight default 0 (FF clue, not Bayes).
        v_beam = 0.0
        if seat_guess is not None and c in home_beams:
            if int(home_beams[c]) == int(seat_guess):
                v_beam = 0.7
        v_occ = 0.0
        if c in on_roster:
            v_occ = 0.5
        if sole_name and c == sole_name.lower():
            v_occ = max(v_occ, 0.35)  # weak last

        fam = {
            "voice": v_voice,
            "face": v_face,
            "appear": v_appear,
            "sex": v_sex,
            "beam": v_beam,
            "occ": v_occ,
        }
        total = sum(weights.get(k, 0.0) * fam.get(k, 0.0) for k in weights)
        scores[c] = _clamp01(total)
        votes[c] = fam

    eligible = list(columns)
    if is_user is False:
        eligible = [c for c in eligible if not is_user_id(c)]

    winner = None
    margin = 0.0
    ranked = sorted(
        ((c, scores[c]) for c in eligible if c in scores),
        key=lambda x: x[1],
        reverse=True,
    )
    if ranked:
        winner = ranked[0][0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = ranked[0][1] - second
        if margin < cfg.MATRIX_MARGIN or ranked[0][1] < 0.15:
            winner = None  # abstain

    return MatrixResult(
        scores=scores,
        votes=votes,
        winner=winner,
        margin=margin,
        source="matrix_spoke",
        is_user=is_user,
        eligible=eligible,
    )


def score_leave(
    columns: list[str],
    *,
    traits_scores: dict[str, float] | None = None,
    face_sims: dict[str, float] | None = None,
    door_beam_boost: dict[str, float] | None = None,
    live_sex: str | None = None,
    on_roster: set[str] | None = None,
    crossing_index: int | None = None,
) -> MatrixResult:
    """Clothing-primary leave scores (shadow / later apply)."""
    traits_scores = traits_scores or {}
    face_sims = face_sims or {}
    door_beam_boost = door_beam_boost or {}
    on_roster = on_roster or set()
    weights = cfg.leave_weights()
    scores: dict[str, float] = {}
    votes: dict[str, dict[str, float]] = {}

    for col in columns:
        c = col.lower()
        v_appear = _clamp01(float(traits_scores.get(c, 0.0)))
        v_face = _clamp01(float(face_sims.get(c, 0.0)))
        v_beam = _clamp01(float(door_beam_boost.get(c, 0.0)))
        v_sex = 0.0
        if live_sex in ("M", "F") and _person_sex(c) == live_sex:
            v_sex = 1.0
        v_occ = 0.5 if c in on_roster else 0.0
        fam = {
            "appear": v_appear,
            "face": v_face,
            "beam": v_beam,
            "sex": v_sex,
            "occ": v_occ,
        }
        total = sum(weights.get(k, 0.0) * fam.get(k, 0.0) for k in weights)
        scores[c] = _clamp01(total)
        votes[c] = fam

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    winner = None
    margin = 0.0
    if ranked:
        winner = ranked[0][0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = ranked[0][1] - second
        if margin < cfg.MATRIX_MARGIN or ranked[0][1] < 0.2:
            winner = None

    return MatrixResult(
        scores=scores,
        votes=votes,
        winner=winner,
        margin=margin,
        source="matrix_leave",
        eligible=list(columns),
        crossing_index=crossing_index,
    )


def desk_silence_allows_user(
    *,
    voice_usable: bool,
    sole_name: str | None,
    sole_certain: bool,
    sole_stale: bool,
    face_auth_age_s: float | None,
    seat_beam: int | None,
    open_u: bool,
) -> bool:
    """Fold-in 1: provisional the user without voice only if tight desk silence.

    face auth age ≤ DESK_SILENCE_FACE_SEC at the user home beam AND no open U.
    """
    if voice_usable:
        return False
    if not is_user_id(sole_name):
        return False
    if not sole_certain or sole_stale:
        return False
    if open_u:
        return False
    if face_auth_age_s is None or face_auth_age_s > cfg.DESK_SILENCE_FACE_SEC:
        return False
    if seat_beam is not None and int(seat_beam) != int(cfg.USER_HOME_BEAM):
        # unknown seat: allow if age+certain ok? Plan: at the user home beam.
        return False
    if seat_beam is None:
        # no seat metadata — require age+certain only (conservative beam skip)
        pass
    return True


def apply_is_user_paint_guard(
    person_id: str | None,
    source: str | None,
    is_user: bool | None,
    *,
    voice_usable: bool,
    sole_stale: bool = False,
    desk_silence_ok: bool = False,
) -> tuple[str | None, str | None, str | None, bool]:
    """Enforce P0 on legacy stamp output. Restrictive-only (D8).

    Returns (person_id, source, guard_note, suppressed).
    May map the user→null only; never invents the user from null/other.
    """
    if not cfg.IS_USER_GUARD:
        return person_id, source, None, False

    if person_id is None:
        return None, source, None, False
    pid = str(person_id).strip().lower()
    # D8: never promote non-the user → the user here
    if not is_user_id(pid):
        return person_id, source, None, False

    # the face check locked this face while looking at the speaker. Voice follows face.
    if source == "face_speaker":
        return person_id, source, "face_speaker", False

    sole_sources = {
        "sole_present",
        "sole_present_stale",
        "sole_stale_unlabeled",
        "media_hold_sole",
    }

    if is_user is False:
        return None, "is_user_block", "is_user_false", True

    # Desk silence exception: no voice + face-certain fresh @ home beam, no U
    if desk_silence_ok and not voice_usable and source in sole_sources:
        return person_id, source, "desk_silence", False

    # uncertain / not true: strip sole the user when voice present (P2)
    if is_user is not True and voice_usable and source in sole_sources:
        return None, "is_user_uncertain_sole", "uncertain_no_sole_user", True

    if is_user is not True and voice_usable and sole_stale and is_user_id(pid):
        return None, "is_user_uncertain_sole", "stale_voice_no_user", True

    # voice_emb path claimed the user without is_user true → strip
    if is_user is not True and voice_usable and source in (
        "voice_emb",
        "voice_emb_vs_sole",
        "voice_emb_weak_open",
    ):
        return None, "is_user_block", "voice_user_without_gate", True

    return person_id, source, None, False
