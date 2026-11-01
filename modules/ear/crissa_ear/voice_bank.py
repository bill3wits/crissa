"""Load / match ECAPA voice prints from house-id people/<id>/voice/*.json.

Optional live promote (the User 2026-08-02): high-confidence free-air stamps can
append a new print so the next exchange matches better. Off unless
RAIL_VOICE_BANK_PROMOTE=1. Never invents person_id; never writes face emb.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .home import EAR
from typing import Any

import numpy as np

from .house_id import people_dir

# Cosine threshold — museum ecapa family ties often ~0.25–0.45; start conservative
# Gate 1 final 2026-08-06: live default 0.35 (WeSpeaker far-field). Per-person:
# RAIL_VOICE_MATCH_SIM_SECOND=0.30 etc. (see match_sim_for).
VOICE_MATCH_SIM = float(os.environ.get("RAIL_VOICE_MATCH_SIM", "0.35"))


# THE OWNER'S FLOOR NAMES THE OWNER (round 19, .101 2026-10-14: no line of his named by his voice, 4 prints from Setup).
# RAIL_VOICE_MATCH_SIM_USER (the owner's own floor, 0.40 in a pack's settings, the house's measured value) was keyed to
# the id "user", but a pack's User is the name typed at Voice ID ("sam"), so the owner was held to everyone's 0.50 - the
# bar round 9 measured his laptop voice under (0.27-0.63). The User's own id now falls back to the USER floor when it has
# no floor of its own. Nothing decides on it alone: the bridge's own bar for "the User's voice" stays 0.48.
# OFF BY DEFAULT since the User's ruling (2026-10-14, "back to 0.50"): at 0.40 a house test named a different synthetic
# voice (sim 0.452) as the User, and the house's own trials on this engine let 22.6 % of other voices through at 0.35
# against 0.14 % at 0.50. The live-print half of round 19's fix (id_trail) stays on and lifts his own sims instead.
# CRISSA_VOICE_USER_FLOOR=1 gives his own id the owner's floor (0.40) again.
def user_floor_on() -> bool:
    return os.environ.get("CRISSA_VOICE_USER_FLOOR", "0").strip().lower() in ("1", "true", "yes", "on")


def match_sim_for(person_id: str | None = None) -> float:
    """Per-person auth floor, falling back to RAIL_VOICE_MATCH_SIM.

    Env: RAIL_VOICE_MATCH_SIM_<ID> e.g. RAIL_VOICE_MATCH_SIM_USER=0.35
    Second placeholder from Gate 1: set RAIL_VOICE_MATCH_SIM_SECOND=0.30 after
    re-enroll; until then keep global thr and soft sole path.
    """
    base = float(os.environ.get("RAIL_VOICE_MATCH_SIM", str(VOICE_MATCH_SIM)))
    if not person_id:
        return base
    key = re.sub(r"[^A-Za-z0-9]+", "_", str(person_id).strip().upper())
    raw = os.environ.get(f"RAIL_VOICE_MATCH_SIM_{key}", "").strip()
    if not raw and key != "USER" and user_floor_on() and str(person_id).strip().lower() == _canonical_user_id():
        raw = os.environ.get("RAIL_VOICE_MATCH_SIM_USER", "").strip()
    if not raw:
        return base
    try:
        return float(raw)
    except ValueError:
        return base

# Live promote gates (false id into bank is worse than a miss)
# Default OFF until bank quality is trusted again (the User 2026-08-03: wiped
# live_promote; free-air was storing non-speech / media as household ids).
# Re-enable: RAIL_VOICE_BANK_PROMOTE=1 after confident desk enrolls.
# A pack had it on 2026-10-11..16 (.101 round 9: two night-time prints left his voice at 0.27-0.63 against the 0.50
# line); OFF again since 2026-10-16 (the User: collecting live prints "nearly immediately corrupt[s] the embeddings").
# The fix for a weak score is a good first set of prints, not adding live ones. RAIL_VOICE_BANK_PROMOTE=1 turns it on.
PROMOTE = os.environ.get("RAIL_VOICE_BANK_PROMOTE", "0") in ("1", "true", "yes")
# Multi-person emb lock path
PROMOTE_MIN_SIM = float(os.environ.get("RAIL_VOICE_PROMOTE_MIN_SIM", "0.48"))
PROMOTE_MIN_CONF = float(os.environ.get("RAIL_VOICE_PROMOTE_MIN_CONF", "0.72"))
PROMOTE_MIN_MARGIN = float(os.environ.get("RAIL_VOICE_PROMOTE_MIN_MARGIN", "0.08"))
# Sole-certain: still require a floor vs own bank (2026-08-03: media/TV was
# landing under the user with sim~0.46 via sole path with no floor). Cross-person
# conflict remains hard-block; self-sim is a soft quality floor, not identity.
PROMOTE_SOLE_MIN_SELF_SIM = float(
    os.environ.get("RAIL_VOICE_PROMOTE_SOLE_MIN_SELF", "0.55")
)
PROMOTE_SOLE_CONFLICT_SIM = float(
    os.environ.get("RAIL_VOICE_PROMOTE_SOLE_CONFLICT_SIM", "0.35")
)
# Media gate must say clear_human (20s char window) before any write
PROMOTE_REQUIRE_HUMAN = os.environ.get("RAIL_VOICE_PROMOTE_REQUIRE_HUMAN", "1") in (
    "1",
    "true",
    "yes",
)
PROMOTE_MIN_DUR = float(os.environ.get("RAIL_VOICE_PROMOTE_MIN_DUR", "1.5"))
# Cap counts *all* voice/*.json (not only 00*.json — that bug let the user hit 119)
PROMOTE_MAX_PER_PERSON = int(os.environ.get("RAIL_VOICE_PROMOTE_MAX", "40"))
PROMOTE_DEDUP = float(os.environ.get("RAIL_VOICE_PROMOTE_DEDUP", "0.95"))
# Emb-lock sources (multi-person). sole_present handled separately when certain.
PROMOTE_EMB_SOURCES = frozenset(
    s.strip()
    for s in os.environ.get(
        "RAIL_VOICE_PROMOTE_SOURCES", "voice_emb,voice_emb_vs_sole"
    ).split(",")
    if s.strip()
)

# Phase I seal: live writers = User only. Everyone else = registration.
_REGISTER_SOURCES = frozenset(
    {
        "register",
        "enroll",
        "voice-register",
        "registration",
        "desk",
        "manual",
    }
)


def _canonical_user_id() -> str:
    """HOUSE_USER_ID / RAIL_PRIMARY_USER, else the identity store's User (the person Init's Voice ID made the User,
    house_id.user_id), else the generic "user". Until 2026-10-11 a pack never named one: the User Init enrols is a
    first name ("sam"), so every live print for him was refused as "sealed" (the .101 round 9 finding)."""
    env = (
        os.environ.get("HOUSE_USER_ID")
        or os.environ.get("RAIL_PRIMARY_USER")
        or ""
    ).strip().lower()
    if env:
        return env
    try:
        from .house_id import user_id as _store_user

        return (_store_user() or "user").strip().lower() or "user"
    except Exception:  # noqa: BLE001 - an unreadable store is the generic User
        return "user"


def bank_write_allowed(person_id: str, source: str = "") -> bool:
    """Live sources may write the User bank. Everyone else: registration."""
    pid = (person_id or "").strip().lower()
    src = (source or "").strip().lower()
    if not pid:
        return False
    if pid in ("s1", "s2") or (pid.startswith("s") and pid[1:].isdigit()):
        return False
    if src in _REGISTER_SOURCES:
        return True
    return pid == _canonical_user_id()


def live_print_model() -> str:
    """model string written on a new print for the live encoder family."""
    if _encoder_filter() == "wespeaker":
        return "wespeaker/english_resnet221 (onnxruntime fp32)"
    return "speechbrain/spkrec-ecapa-voxceleb"


def live_print_dim() -> int:
    return 256 if _encoder_filter() == "wespeaker" else 192


def _encoder_filter() -> str | None:
    """Only load bank prints matching live encoder family (ecapa | wespeaker).

    Mixing ECAPA and WeSpeaker vectors in one cosine match is meaningless.
    Env HOUSE_VOICE_ENCODER / RAIL_VOICE_ENCODER selects; unset = **wespeaker**.

    The default was ecapa, which stopped being true on 2026-08-04 when WeSpeaker
    went live, and became dangerous on 2026-08-15 when the ECAPA prints were
    quarantined: losing the env var would have loaded ZERO prints for the User
    and stopped voice ID with no error at all. A default that points at an empty
    bank is worse than a wrong one, because nothing complains.
    """
    raw = (
        os.environ.get("HOUSE_VOICE_ENCODER")
        or os.environ.get("RAIL_VOICE_ENCODER")
        or "wespeaker"
    ).strip().lower()
    if raw in ("wespeaker", "ws", "we"):
        return "wespeaker"
    return "ecapa"


def _rec_encoder_family(rec: dict) -> str:
    model = str(rec.get("model") or rec.get("encoder") or rec.get("family") or "").lower()
    if "wespeaker" in model or model.startswith("ws/") or "campplus" in model:
        return "wespeaker"
    if "ecapa" in model or "speechbrain" in model or "spkrec" in model:
        return "ecapa"
    # legacy prints without model tag = ECAPA museum/migrate path
    if rec.get("embedding_ecapa") is not None:
        return "ecapa"
    # bare encoding: assume live default family
    return _encoder_filter() or "ecapa"


def load_voice_bank(root: Path | None = None) -> dict[str, list[np.ndarray]]:
    d = root or people_dir()
    out: dict[str, list[np.ndarray]] = {}
    want = _encoder_filter()
    if not d.is_dir():
        return out
    for person in sorted(p for p in d.iterdir() if p.is_dir()):
        if person.name.startswith((".", "_")):
            continue
        # Skip system / TTS-filter voices — stamp TTS veto only, never who-spoke rank.
        if person.name.lower() in (
            "crissa_her_tts",
            "crissa-her-tts",
            "tts_echo",
            "announcer",
            "crew_tts",
            "s1",
            "s2",
        ):
            continue
        pj = person / "person.json"
        if pj.is_file():
            try:
                role = json.loads(pj.read_text(encoding="utf-8")).get("role")
                if role in ("system_voice", "tts_filter", "scratch_voice"):
                    continue
            except Exception:
                pass
        embs = []
        for f in sorted((person / "voice").glob("*.json")):
            try:
                rec = json.loads(f.read_text())
            except Exception:
                continue
            # Per-print TTS filter (role on json) — veto bank only
            if rec.get("role") == "tts_filter":
                continue
            if want and _rec_encoder_family(rec) != want:
                continue
            enc = rec.get("encoding") or rec.get("embedding_ecapa") or rec.get("embedding")
            if not enc:
                continue
            e = np.asarray(enc, dtype=np.float32)
            n = float(np.linalg.norm(e))
            if n < 1e-8:
                continue
            embs.append(e / n)
        if embs:
            out[person.name.lower()] = embs
    return out


def rank_voice(
    emb: np.ndarray,
    bank: dict[str, list[np.ndarray]] | None = None,
    *,
    roster: list[str] | None = None,
    top_k: int = 3,
) -> list[tuple[str, float]]:
    """Per-person best cosine, highest first. Optional closed roster filter (names lowercased)."""
    bank = bank if bank is not None else load_voice_bank()
    e = np.asarray(emb, dtype=np.float32)
    n = float(np.linalg.norm(e))
    if n < 1e-8 or not bank:
        return []
    e = e / n
    allow = None
    if roster is not None:
        allow = {str(x).lower() for x in roster if x}
    scores: list[tuple[str, float]] = []
    for name, embs in bank.items():
        if allow is not None and name not in allow:
            continue
        best = -1.0
        for k in embs:
            # A print from a different encoder cannot be compared to this probe.
            # np.dot RAISES on a shape mismatch, which would take down the whole
            # stamp path for one stray vector; skip it and keep matching.
            if getattr(k, "shape", None) and k.shape[-1] != e.shape[-1]:
                continue
            s = float(np.dot(e, k))
            if s > best:
                best = s
        if best > -1.0:
            scores.append((name, best))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[: max(1, top_k)]


def match_voice(
    emb: np.ndarray,
    bank: dict[str, list[np.ndarray]] | None = None,
    threshold: float | None = None,
) -> tuple[str | None, float]:
    """Best person_id and sim; None if below threshold."""
    thr = VOICE_MATCH_SIM if threshold is None else threshold
    ranked = rank_voice(emb, bank=bank, top_k=1)
    if not ranked:
        return None, -1.0
    best_name, best_sim = ranked[0]
    if best_sim < thr:
        return None, best_sim
    return best_name, best_sim


_EMPTY_WARNED = False


def bank_counts() -> dict[str, int]:
    return {k: len(v) for k, v in load_voice_bank().items()}


def _is_house() -> bool:
    try:
        from .home import is_house
        return bool(is_house())
    except Exception:  # noqa: BLE001
        return False


def bank_health() -> dict:
    """Is the live bank actually usable? Say so loudly when it is not.

    An empty bank is indistinguishable from "nobody is speaking" at every layer
    above this one: no match, no stamp, no letter, no AUTH suggestion — and no
    error. This makes the silence audible once.
    """
    global _EMPTY_WARNED
    enc = _encoder_filter()
    counts = bank_counts()
    total = sum(counts.values())
    human = {k: v for k, v in counts.items()
             if not k.endswith("_tts") and k not in ("announcer",)}
    ok = sum(human.values()) > 0
    if not ok and not _EMPTY_WARNED:
        _EMPTY_WARNED = True
        if _is_house():
            print(
                f"[voice_bank] EMPTY BANK for encoder={enc!r} — no human voice "
                f"prints loaded. Voice ID cannot match anyone. Check "
                f"HOUSE_VOICE_ENCODER and people/*/voice/.",
                flush=True,
            )
        else:
            # A PACK BEFORE ITS FIRST VOICE ID IS NOT A FAULT (the User 2026-10-10, .101: the panel's tile went red on
            # "EMPTY BANK ... cannot match anyone"): said plainly, with no word the tiles read as an error
            print(f"[voice_bank] no Voice ID yet (encoder {enc}): nobody's voice is known until Voice ID runs "
                  f"(Setup, or Voice ID again in the Crissa menu)", flush=True)
    return {"ok": ok, "encoder": enc, "total": total, "counts": counts}


# ── Adaptive s-norm helpers ─────────────────────────────────────
# Adaptive s-norm (sphere-normalized) calibration against a cohort
# of non-target reference vectors.  The cohort is built from:
#   1. All enrolled voice prints for OTHER people (excluding the target)
#   2. Logged media embeddings (from crissa media emb log)
# Calibrated score: z = (sim_target - mu_cohort) / sigma_cohort
# Output: sigmoid(z) ∈ [0, 1] as a calibrated confidence-like score.
# Shadow-only: never affects live gating or person_id assignment.


import math as _math


def _media_emb_dir() -> Path:
    """Directory for logged media embeddings used as cohort references."""
    raw = os.environ.get("RAIL_MEDIA_EMB_DIR")
    if raw:
        return Path(raw)
    return EAR / "media_embs"


def load_media_embs(path: Path | None = None) -> list[np.ndarray]:
    """Load logged media embeddings from JSONL/JSON files."""
    d = path or _media_emb_dir()
    embs: list[np.ndarray] = []
    if not d.is_dir():
        return embs
    for f in sorted(d.glob("*.jsonl")) + sorted(d.glob("media_embs*.json")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                enc = (
                    rec.get("encoding")
                    or rec.get("embedding")
                    or rec.get("emb")
                )
                if enc:
                    e = np.asarray(enc, dtype=np.float32)
                    n = float(np.linalg.norm(e))
                    if n > 1e-8:
                        embs.append(e / n)
        except Exception:
            pass
    return embs


def _asnorm_cohort(
    emb: np.ndarray,
    bank: dict[str, list[np.ndarray]] | None = None,
    exclude_person: str | None = None,
    media_embs: list[np.ndarray] | None = None,
) -> list[float]:
    """Build cohort similarity scores.

    Returns a list of cosine scores between *emb* and every reference vector
    in the bank EXCEPT those belonging to *exclude_person*, plus any media
    embeddings.  Empty list if cohort is empty.
    """
    bank = bank if bank is not None else load_voice_bank()
    e = np.asarray(emb, dtype=np.float32)
    n = float(np.linalg.norm(e))
    if n < 1e-8 or not bank:
        return []
    e = e / n
    exclude_lower = str(exclude_person).lower() if exclude_person else None
    scores: list[float] = []
    for name, ref_embs in bank.items():
        if exclude_lower and name.lower() == exclude_lower:
            continue
        for ref in ref_embs:
            s = float(np.dot(e, ref))
            scores.append(s)
    if media_embs:
        for ref in media_embs:
            s = float(np.dot(e, ref))
            scores.append(s)
    return scores


def asnorm_calibrated_score(
    emb: np.ndarray,
    bank: dict[str, list[np.ndarray]] | None = None,
    *,
    target_person: str | None = None,
    media_embs: list[np.ndarray] | None = None,
) -> dict[str, Any]:
    """Compute adaptive s-norm calibrated similarity.

    Steps:
      1. Get the best cosine sim for *target_person* (or overall best if no target).
      2. Build cohort of all OTHER enrolled prints + media embs.
      3. Compute mu, sigma of cohort scores.
      4. z = (sim_target - mu) / sigma  (or z = 0 if sigma ≈ 0).
      5. calibrated = sigmoid(z) ∈ [0, 1].

    Returns dict with top_sim, cohort_mu, cohort_sigma, z_score, calibrated_score.
    """
    bank = bank if bank is not None else load_voice_bank()
    e = np.asarray(emb, dtype=np.float32)
    n = float(np.linalg.norm(e))
    if n < 1e-8 or not bank:
        return {"top_sim": None, "cohort_mu": None, "cohort_sigma": None,
                "z_score": None, "calibrated_score": None}

    e = e / n

    # Best overall sim from bank
    all_scores: list[tuple[str, float]] = []
    for name, refs in bank.items():
        best = -1.0
        for ref in refs:
            s = float(np.dot(e, ref))
            if s > best:
                best = s
        all_scores.append((name, best))
    all_scores.sort(key=lambda x: x[1], reverse=True)

    if not all_scores:
        return {"top_sim": None, "cohort_mu": None, "cohort_sigma": None,
                "z_score": None, "calibrated_score": None}

    top_name, top_sim = all_scores[0]

    # If a target was given, use its score; otherwise use overall best
    if target_person:
        target_lower = target_person.lower()
        target_sim = next(
            (s for nm, s in all_scores if nm.lower() == target_lower), None
        )
        if target_sim is not None:
            top_name = target_person
            top_sim = target_sim

    # Cohort: everything EXCEPT the top match person
    cohort_scores = _asnorm_cohort(
        emb, bank, exclude_person=top_name, media_embs=media_embs
    )

    if not cohort_scores:
        return {
            "top_sim": round(top_sim, 4),
            "top_name": top_name,
            "cohort_size": 0,
            "cohort_mu": None,
            "cohort_sigma": None,
            "z_score": None,
            "calibrated_score": None,
        }

    mu = float(np.mean(cohort_scores))
    sigma = float(np.std(cohort_scores))
    if sigma < 1e-6:
        z = 0.0
    else:
        z = (top_sim - mu) / sigma

    # Sigmoid for calibrated [0,1] score
    try:
        calibrated = 1.0 / (1.0 + _math.exp(-z))
    except OverflowError:
        calibrated = 1.0 if z > 0 else 0.0

    return {
        "top_sim": round(top_sim, 4),
        "top_name": top_name,
        "cohort_size": len(cohort_scores),
        "cohort_mu": round(mu, 4),
        "cohort_sigma": round(sigma, 4),
        "z_score": round(z, 4),
        "calibrated_score": round(calibrated, 4),
    }


def _next_slot(voice_dir: Path) -> int:
    n = 1
    while (voice_dir / f"{n:03d}.json").is_file():
        n += 1
    return n


def _presence_sole_certain(st: dict[str, Any], pid: str) -> dict[str, Any]:
    """True when presence says one named person and they are face-certain."""
    pres = st.get("presence") if isinstance(st.get("presence"), dict) else {}
    people = [p for p in (pres.get("people") or []) if isinstance(p, dict)]
    named = [
        str(p.get("name") or "").lower()
        for p in people
        if p.get("name") and not str(p.get("name")).startswith("U")
    ]
    named = [n for n in named if n]
    hc = pres.get("headcount")
    try:
        hc_i = int(hc) if hc is not None else len(named)
    except (TypeError, ValueError):
        hc_i = len(named)
    sole = hc_i == 1 and len(set(named)) == 1 and named[0] == pid
    certain = False
    for p in people:
        if str(p.get("name") or "").lower() == pid:
            certain = bool(p.get("certain") or p.get("id_status") == "confirmed")
            break
    # stamp-level certain also counts (sole_present confirmed)
    if st.get("certain") and st.get("person_id") == pid:
        certain = True
    if st.get("id_status") == "confirmed" and st.get("person_id") == pid:
        certain = True
    return {
        "sole": sole,
        "certain": certain,
        "headcount": hc_i,
        "named": named,
        "ok": bool(sole and certain),
    }


def _decide_promote_trust(
    *,
    emb: np.ndarray,
    pid: str,
    st: dict[str, Any],
    sim: float,
    conf: float,
    dur: float,
    media: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Corruption-aware allow/deny for writing emb into bank.

    Media gate (20s char, conversational pauses) replaces self-sim as the
    sole-room quality signal. self-sim is logged only, never a hard sole gate.

    Paths:
      A) Multi-person emb lock (voice_emb*): high sim+conf+margin + clear_human
      B) Sole certain: presence id trusted; emb must not prefer someone else;
         + clear_human (no media)
    """
    src = str(st.get("source") or "")
    sole_info = _presence_sole_certain(st, pid)
    open_rank = rank_voice(emb, roster=None, top_k=3)
    self_rank = rank_voice(emb, roster=[pid], top_k=1)
    self_sim = float(self_rank[0][1]) if self_rank else -1.0  # diagnostic only
    top_id = open_rank[0][0] if open_rank else None
    top_sim = float(open_rank[0][1]) if open_rank else -1.0
    second_sim = float(open_rank[1][1]) if len(open_rank) > 1 else -1.0
    margin = (top_sim - second_sim) if second_sim > -1 else top_sim
    media = media or {}

    base = {
        "src": src,
        "self_sim": round(self_sim, 4),  # diagnostic — not a sole gate
        "top_id": top_id,
        "top_sim": round(top_sim, 4),
        "margin": round(margin, 4) if margin == margin else None,
        "sole": sole_info,
        "media": {
            "phase": media.get("phase"),
            "verdict": media.get("verdict"),
            "reason": media.get("reason"),
            "promote_ok": media.get("promote_ok"),
        },
    }

    if dur < PROMOTE_MIN_DUR:
        return {**base, "allow": False, "reason": "dur", "dur": dur}

    # Media / dead-air / characterize — pause writes until clear human
    if PROMOTE_REQUIRE_HUMAN and not media.get("promote_ok"):
        return {
            **base,
            "allow": False,
            "reason": "await_clear_human",
            "media_reason": media.get("reason") or media.get("verdict") or "no_media_obs",
            "path": "media_gate",
        }

    # --- Path B: sole + certain (preferred when only one trusted body) ---
    if sole_info["ok"] or (
        src == "sole_present" and sole_info["certain"] and sole_info["sole"]
    ):
        # Corruption: open bank clearly prefers a *different* enrolled person
        if (
            top_id
            and top_id != pid
            and top_sim >= PROMOTE_SOLE_CONFLICT_SIM
            and (margin is None or margin >= 0.05)
        ):
            return {
                **base,
                "allow": False,
                "reason": "sole_voice_conflict",
                "path": "sole_certain",
            }
        # Floor vs own bank — blocks media/TV crumbs attributed via sole_certain
        if self_sim < PROMOTE_SOLE_MIN_SELF_SIM and sim < PROMOTE_SOLE_MIN_SELF_SIM:
            return {
                **base,
                "allow": False,
                "reason": "sole_self_sim_floor",
                "path": "sole_certain",
                "min_self_sim": PROMOTE_SOLE_MIN_SELF_SIM,
                "self_sim": round(self_sim, 4),
                "sim": round(sim, 4),
            }
        return {
            **base,
            "allow": True,
            "reason": "sole_certain_clear_human",
            "path": "sole_certain",
        }

    # --- Path A: multi-person emb discrimination ---
    if src not in PROMOTE_EMB_SOURCES:
        return {**base, "allow": False, "reason": "source", "path": "emb"}

    certain = bool(st.get("certain") or st.get("id_status") == "confirmed")
    min_sim = PROMOTE_MIN_SIM * (0.92 if certain else 1.0)
    min_conf = PROMOTE_MIN_CONF * (0.95 if certain else 1.0)
    # Use stamp sim only (not self_sim) for multi path
    if sim < min_sim:
        return {
            **base,
            "allow": False,
            "reason": "sim",
            "path": "emb",
            "min_sim": min_sim,
            "sim": sim,
        }
    if conf < min_conf and not certain:
        return {
            **base,
            "allow": False,
            "reason": "conf",
            "path": "emb",
            "min_conf": min_conf,
            "conf": conf,
        }
    if second_sim > 0 and margin < PROMOTE_MIN_MARGIN and top_id != pid:
        return {
            **base,
            "allow": False,
            "reason": "ambiguous_margin",
            "path": "emb",
            "min_margin": PROMOTE_MIN_MARGIN,
        }
    if top_id and top_id != pid and top_sim >= sim:
        return {
            **base,
            "allow": False,
            "reason": "open_prefers_other",
            "path": "emb",
        }
    return {
        **base,
        "allow": True,
        "reason": "emb_lock_clear_human" + ("_certain" if certain else ""),
        "path": "emb",
    }


class _AlreadySeen(Exception):
    """The turn's media verdict is already on its stamp."""


def maybe_promote_live_print(
    *,
    person_id: str,
    emb: Any,
    stamp: dict[str, Any] | None,
    text: str = "",
    speech_duration_s: float | None = None,
    pcm: bytes | None = None,
    sr: int = 16000,
) -> dict[str, Any] | None:
    """Append a gated live ECAPA print to people/<id>/voice/NNN.json.

    Corruption-first: only write when identity is trusted
    (emb lock with margin, or sole person face-certain in room).

    Returns promote result dict or None if skipped / disabled.
    """
    if not PROMOTE:
        return None
    st = stamp or {}
    src = str(st.get("source") or "")
    pid = (person_id or st.get("person_id") or "").strip().lower()
    if not pid or (pid.startswith("u") and pid[1:].isdigit()):      # u1/u2 are unknown slots, never a person
        return {"ok": False, "skipped": "no_person"}
    if not bank_write_allowed(pid, "live_promote"):
        return {"ok": False, "skipped": "sealed", "person_id": pid}
    try:
        sim = float(st.get("voice_emb_sim") if st.get("voice_emb_sim") is not None else -1)
    except (TypeError, ValueError):
        sim = -1.0
    try:
        conf = float(st.get("confidence") if st.get("confidence") is not None else -1)
    except (TypeError, ValueError):
        conf = -1.0
    dur = float(speech_duration_s or st.get("speech_duration_s") or 0.0)
    if emb is None:
        return {"ok": False, "skipped": "no_emb"}

    e = np.asarray(emb, dtype=np.float32).reshape(-1)
    nrm = float(np.linalg.norm(e))
    if nrm < 1e-8:
        return {"ok": False, "skipped": "zero_emb"}
    e = e / nrm
    want_dim = live_print_dim()
    if int(e.shape[0]) != want_dim:
        return {
            "ok": False,
            "skipped": "encoder_dim_mismatch",
            "dim": int(e.shape[0]),
            "want": want_dim,
            "encoder": _encoder_filter(),
        }

    # Media vs person (20s char after dead air; pauses → human). The turn's own verdict when the server already fed it
    # (st["media_gate"], every ear turn): feeding the same turn twice puts a zero-gap copy into the window, which reads
    # as dense speech - only ever exercised in a pack, where promotion is on (2026-10-11).
    media_obs: dict[str, Any] = {}
    seen = st.get("media_gate") if isinstance(st.get("media_gate"), dict) else None
    try:
        if seen is not None and "promote_ok" in seen:
            raise _AlreadySeen
        from .media_gate import observe_turn

        media_obs = observe_turn(
            duration_s=dur,
            rms_db=st.get("rms_db"),  # optional if stamp carries it
            text=text or "",
            person_id=pid,
        )
    except _AlreadySeen:
        media_obs = dict(seen or {})
    except Exception as e:
        media_obs = {
            "promote_ok": False,
            "reason": f"media_gate_err:{e}",
            "verdict": "unsure",
        }

    decision = _decide_promote_trust(
        emb=e, pid=pid, st=st, sim=sim, conf=conf, dur=dur, media=media_obs
    )
    if not decision.get("allow"):
        return {
            "ok": False,
            "skipped": decision.get("reason"),
            "decision": decision,
            "source": src,
        }

    root = people_dir()
    voice_dir = root / pid / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    # Cap live_promote growth only (desk/manual enrolls unlimited).
    # Count all *.json except quarantine; prefer counting source=live_promote.
    all_slots = sorted(
        f
        for f in voice_dir.glob("*.json")
        if f.is_file() and not f.name.startswith("_")
    )
    live_n = 0
    for f in all_slots:
        try:
            rec = json.loads(f.read_text())
            if rec.get("source") == "live_promote":
                live_n += 1
        except Exception:
            live_n += 1  # treat unreadable as live (conservative)
    if live_n >= PROMOTE_MAX_PER_PERSON:
        return {
            "ok": False,
            "skipped": "max_per_person",
            "n": live_n,
            "max": PROMOTE_MAX_PER_PERSON,
        }

    # Near-dup of an existing print for this person → skip
    for f in all_slots:
        try:
            rec = json.loads(f.read_text())
            enc = rec.get("encoding") or rec.get("embedding")
            if not enc:
                continue
            k = np.asarray(enc, dtype=np.float32)
            kn = float(np.linalg.norm(k))
            if kn < 1e-8:
                continue
            if float(np.dot(e, k / kn)) >= PROMOTE_DEDUP:
                return {
                    "ok": False,
                    "skipped": "near_dup",
                    "dup_of": f.name,
                    "cos": float(np.dot(e, k / kn)),
                }
        except Exception:
            continue

    slot = _next_slot(voice_dir)
    wav_path = None
    if pcm and len(pcm) > 2000:
        import wave

        wav_path = voice_dir / f"live_{int(time.time())}_{slot}.wav"
        try:
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(sr))
                w.writeframes(pcm)
        except Exception:
            wav_path = None

    rec = {
        "person_id": pid,
        "modality": "voice",
        "model": live_print_model(),
        "dim": int(e.shape[0]),
        "encoding": [float(x) for x in e.tolist()],
        "source": "live_promote",
        "stamp_source": src,
        "promote_path": decision.get("path"),
        "promote_reason": decision.get("reason"),
        "voice_emb_sim": sim,
        "self_sim": decision.get("self_sim"),
        "confidence": conf,
        "certain": bool(st.get("certain")),
        "id_status": st.get("id_status"),
        "speech_duration_s": dur,
        "cue": (text or "")[:200],
        "wav": str(wav_path) if wav_path else None,
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": (
            "auto-promoted free-air emb when identity trusted "
            f"({decision.get('path')}/{decision.get('reason')})"
        ),
    }
    jp = voice_dir / f"{slot:03d}.json"
    jp.write_text(json.dumps(rec, indent=2) + "\n")
    return {
        "ok": True,
        "person_id": pid,
        "slot": slot,
        "path": str(jp),
        "sim": sim,
        "conf": conf,
        "decision": decision,
        "wav": str(wav_path) if wav_path else None,
    }
