"""ID matrix + isUser thresholds — single config surface (PROVISIONAL).

Plan rev4 / Fable review F2+P6 + Part 3 fold-ins.
Tune via env; values marked provisional until matrix shadow soak green
(≥200 voiced turns minimum before RAIL_MATRIX_SPOKE=1).

CRITICAL (fold-in 2): Phase A isUser floors compare **RAW cosine** sims.
Thin-bank ×0.5 scaling applies ONLY inside Phase B weighted sum — never
to RAIL_IS_USER_SIM / RAIL_NOT_USER_SIM.
"""
from __future__ import annotations

import os
from typing import Any


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# --- isUser Phase A (RAW cosine; true floor above measured impostor ~0.433) --
IS_USER_SIM = _f("RAIL_IS_USER_SIM", 0.45)
IS_USER_MARGIN = _f("RAIL_IS_USER_MARGIN", 0.05)
NOT_USER_SIM = _f("RAIL_NOT_USER_SIM", 0.28)
NOT_USER_MARGIN = _f("RAIL_NOT_USER_MARGIN", 0.05)
IS_USER_GUARD = _b("RAIL_IS_USER_GUARD", True)
# Desk silence (fold-in 1): face auth age ≤ this AND home beam + no U
DESK_SILENCE_FACE_SEC = _f("RAIL_DESK_SILENCE_FACE_SEC", 120.0)
USER_HOME_BEAM = int(os.environ.get("RAIL_USER_HOME_BEAM", "8") or "8")

# --- matrix combination (normalized weighted sum ∈ [0,1]) --------------------
MATRIX_MARGIN = _f("RAIL_MATRIX_MARGIN", 0.05)
THIN_BANK_N = int(os.environ.get("RAIL_THIN_BANK_N", "5") or "5")
THIN_BANK_VOICE_SCALE = _f("RAIL_THIN_BANK_VOICE_SCALE", 0.5)

# Spoke weights (must sum ≈ 1.0). Beam default 0: FF clue, not Bayes
# (crissa/docs/BEAM-IS-FF-CLUE.md). Votes still record v_beam.
SPOKE_WEIGHTS: dict[str, float] = {
    "voice": _f("RAIL_MX_W_VOICE", 0.40),
    "face": _f("RAIL_MX_W_FACE", 0.25),
    "appear": _f("RAIL_MX_W_APPEAR", 0.10),
    "sex": _f("RAIL_MX_W_SEX", 0.05),
    "beam": _f("RAIL_MX_W_BEAM", 0.0),
    "occ": _f("RAIL_MX_W_OCC", 0.10),
}

# Leave weights
LEAVE_WEIGHTS: dict[str, float] = {
    "appear": _f("RAIL_MX_W_LEAVE_APPEAR", 0.45),
    "face": _f("RAIL_MX_W_LEAVE_FACE", 0.25),
    "beam": _f("RAIL_MX_W_LEAVE_BEAM", 0.0),
    "sex": _f("RAIL_MX_W_LEAVE_SEX", 0.05),
    "occ": _f("RAIL_MX_W_LEAVE_OCC", 0.10),
}

# Ship: shadow-first; matrix does not drive person_id until RAIL_MATRIX_SPOKE=1
MATRIX_SPOKE_LIVE = _b("RAIL_MATRIX_SPOKE", False)
MATRIX_SHADOW = _b("RAIL_MATRIX_SHADOW", True)  # log always unless explicit off


def normalize_weights(w: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, float(v)) for v in w.values()) or 1.0
    return {k: max(0.0, float(v)) / s for k, v in w.items()}


def spoke_weights() -> dict[str, float]:
    return normalize_weights(dict(SPOKE_WEIGHTS))


def leave_weights() -> dict[str, float]:
    return normalize_weights(dict(LEAVE_WEIGHTS))


def as_public_dict() -> dict[str, Any]:
    """For stamp / debug — shows provisional knobs in use."""
    return {
        "provisional": True,
        "is_user_sim": IS_USER_SIM,
        "is_user_margin": IS_USER_MARGIN,
        "not_user_sim": NOT_USER_SIM,
        "not_user_margin": NOT_USER_MARGIN,
        "matrix_margin": MATRIX_MARGIN,
        "matrix_spoke_live": MATRIX_SPOKE_LIVE,
        "matrix_shadow": MATRIX_SHADOW,
        "spoke_weights": spoke_weights(),
        "leave_weights": leave_weights(),
        "thin_bank_n": THIN_BANK_N,
    }
