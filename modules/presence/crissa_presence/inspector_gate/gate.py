"""Population-closed assurance stack — pure functions, no IO."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .reasons import ReasonCode
from .validate import ValidationResult, l2_normalize, validate_emb_record


@dataclass
class GateConfig:
    """~dozen named constants — one block, no env forest."""

    sim_certain: float = 0.55
    sim_provisional: float = 0.50
    margin_stamp: float = 0.04
    margin_store: float = 0.08  # 2× stamp margin
    # Measured 2026-07-29 identity-store audit: max cross-person face sim ≈ 0.29
    # (two guests). Floor 0.40 leaves headroom above measured max.
    cross_person_floor: float = 0.40
    det_score_floor: float = 0.50
    stamp_certain_post: float = 0.80
    stamp_provisional_post: float = 0.55
    crowd_same_gender_n: int = 3


@dataclass
class RosterPerson:
    person_id: str
    gender: str | None = None  # M|F from person.json (operator stamp)
    certain: bool = False

    def __post_init__(self) -> None:
        self.person_id = self.person_id.strip().lower()
        if self.gender:
            self.gender = self.gender.strip().upper()[:1]
            if self.gender not in ("M", "F"):
                self.gender = None


@dataclass
class PersonBank:
    person_id: str
    gender: str | None
    embs: list[list[float]]  # L2-normalized

    def __post_init__(self) -> None:
        self.person_id = self.person_id.strip().lower()
        if self.gender:
            self.gender = self.gender.strip().upper()[:1]
            if self.gender not in ("M", "F"):
                self.gender = None
        self.embs = [l2_normalize(e) if e else e for e in self.embs]


@dataclass
class Verdict:
    person_id: str | None
    stamp_confidence: float
    stamp_level: str  # certain | provisional | none
    store_allowed: bool
    reason_codes: list[ReasonCode] = field(default_factory=list)
    best_sim: float = -1.0
    second_sim: float = -1.0
    margin: float = 0.0
    posterior: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "stamp_confidence": self.stamp_confidence,
            "stamp_level": self.stamp_level,
            "store_allowed": self.store_allowed,
            "reason_codes": [c.value for c in self.reason_codes],
            "best_sim": self.best_sim,
            "second_sim": self.second_sim,
            "margin": self.margin,
            "posterior": self.posterior,
            "details": self.details,
        }


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    return float(sum(x * y for x, y in zip(a, b)))


def best_sim_to_bank(emb: Sequence[float], bank: PersonBank) -> float:
    if not bank.embs:
        return -1.0
    return max(cosine(emb, e) for e in bank.embs)


def cross_bank_contamination(
    emb: Sequence[float],
    target_id: str,
    banks: Sequence[PersonBank],
    *,
    floor: float,
) -> tuple[bool, str | None, float]:
    """True if emb sims into another person's bank above floor (self-poison)."""
    tid = target_id.strip().lower()
    worst_other = -1.0
    worst_id = None
    for b in banks:
        if b.person_id == tid:
            continue
        s = best_sim_to_bank(emb, b)
        if s > worst_other:
            worst_other = s
            worst_id = b.person_id
    if worst_other >= floor:
        return True, worst_id, worst_other
    return False, worst_id, worst_other


def _softmax(scores: dict[str, float], temp: float = 0.15) -> dict[str, float]:
    if not scores:
        return {}
    # map sim-like scores to log odds-ish
    mx = max(scores.values())
    exps = {k: math.exp((v - mx) / max(temp, 1e-6)) for k, v in scores.items()}
    z = sum(exps.values()) or 1.0
    return {k: exps[k] / z for k in exps}


def assign_closed_population(
    face_embs: list[list[float]],
    soft_genders: list[str | None],
    banks: Sequence[PersonBank],
    roster: Sequence[RosterPerson],
    cfg: GateConfig | None = None,
) -> list[Verdict]:
    """Greedy unique assignment within closed roster (two-F / multi-face)."""
    cfg = cfg or GateConfig()
    bank_map = {b.person_id: b for b in banks}
    # score matrix face_i x person
    people = [r.person_id for r in roster if r.person_id in bank_map]
    if not people:
        return [
            Verdict(None, 0.0, "none", False, [ReasonCode.POP_EMPTY, ReasonCode.STAMP_NONE])
            for _ in face_embs
        ]

    # same-gender crowd
    gender_counts: dict[str, int] = {}
    for r in roster:
        if r.gender:
            gender_counts[r.gender] = gender_counts.get(r.gender, 0) + 1
    for g, n in gender_counts.items():
        if n >= cfg.crowd_same_gender_n:
            return [
                Verdict(
                    None,
                    0.0,
                    "none",
                    False,
                    [ReasonCode.CROWD_NOISE, ReasonCode.STAMP_NONE],
                    details={"crowd_gender": g, "n": n},
                )
                for _ in face_embs
            ]

    pairs: list[tuple[float, int, str]] = []  # sim, face_idx, person_id
    for i, emb in enumerate(face_embs):
        emb_n = l2_normalize(list(emb))
        soft_g = (soft_genders[i] or "").strip().upper()[:1] or None
        if soft_g not in ("M", "F"):
            soft_g = None
        for pid in people:
            b = bank_map[pid]
            # gender partition when both known
            if soft_g and b.gender and soft_g != b.gender:
                continue
            s = best_sim_to_bank(emb_n, b)
            pairs.append((s, i, pid))
    pairs.sort(reverse=True)

    used_f: set[int] = set()
    used_p: set[str] = set()
    pick: dict[int, tuple[str, float]] = {}
    for s, i, pid in pairs:
        if i in used_f or pid in used_p:
            continue
        used_f.add(i)
        used_p.add(pid)
        pick[i] = (pid, s)

    out: list[Verdict] = []
    for i, emb in enumerate(face_embs):
        emb_n = l2_normalize(list(emb))
        soft_g = (soft_genders[i] or "").strip().upper()[:1] or None
        if soft_g not in ("M", "F"):
            soft_g = None
        if i not in pick:
            out.append(
                evaluate_verdict(
                    emb_n,
                    roster=roster,
                    banks=banks,
                    soft_gender=soft_g,
                    media_flag=False,
                    count_certain=True,
                    cfg=cfg,
                )
            )
            continue
        # re-evaluate full stack with closed pop already implied
        v = evaluate_verdict(
            emb_n,
            roster=roster,
            banks=banks,
            soft_gender=soft_g,
            media_flag=False,
            count_certain=True,
            cfg=cfg,
        )
        out.append(v)
    return out


def evaluate_verdict(
    emb: Sequence[float],
    *,
    roster: Sequence[RosterPerson],
    banks: Sequence[PersonBank],
    soft_gender: str | None = None,
    media_flag: bool = False,
    count_certain: bool = True,
    sole_present: bool = False,
    voice_suggest: str | None = None,
    voice_sim: float | None = None,
    det_score: float | None = None,
    operator_person_id: str | None = None,
    validation: ValidationResult | None = None,
    cfg: GateConfig | None = None,
) -> Verdict:
    """Single-face / single-emb assurance stack → stamp + store gates."""
    cfg = cfg or GateConfig()
    reasons: list[ReasonCode] = []
    details: dict[str, Any] = {}

    emb_n = l2_normalize(list(emb))
    soft_g = (soft_gender or "").strip().upper()[:1] or None
    if soft_g not in ("M", "F"):
        soft_g = None
    if soft_g:
        reasons.append(ReasonCode.CIRCULARITY_OK)  # from pixels/caller, not bank

    if validation is not None and not validation.ok:
        return Verdict(
            None,
            0.0,
            "none",
            False,
            [ReasonCode.VALIDATION_FAIL, ReasonCode.STORE_BLOCKED, ReasonCode.STAMP_NONE],
            details={"errors": validation.errors},
        )
    if validation is not None and validation.ok:
        reasons.append(ReasonCode.VALIDATION_OK)

    if det_score is not None and det_score < cfg.det_score_floor:
        reasons.append(ReasonCode.QUALITY_LOW)
    elif det_score is not None:
        reasons.append(ReasonCode.QUALITY_OK)

    roster_ids = {r.person_id for r in roster}
    if not roster_ids:
        reasons.extend([ReasonCode.POP_EMPTY, ReasonCode.STAMP_NONE, ReasonCode.STORE_BLOCKED])
        return Verdict(None, 0.0, "none", False, reasons)

    reasons.append(ReasonCode.POP_CLOSED)
    if not count_certain:
        reasons.append(ReasonCode.COUNT_UNCERTAIN)

    # crowd same gender
    gcount: dict[str, int] = {}
    for r in roster:
        if r.gender:
            gcount[r.gender] = gcount.get(r.gender, 0) + 1
    for g, n in gcount.items():
        if n >= cfg.crowd_same_gender_n:
            reasons.extend([ReasonCode.CROWD_NOISE, ReasonCode.STAMP_NONE, ReasonCode.STORE_BLOCKED])
            return Verdict(None, 0.0, "none", False, reasons, details={"crowd_gender": g})

    bank_map = {b.person_id: b for b in banks if b.person_id in roster_ids}
    # sims per roster person
    sims: dict[str, float] = {}
    for r in roster:
        b = bank_map.get(r.person_id)
        if not b or not b.embs:
            sims[r.person_id] = -1.0
            continue
        # gender partition when soft_g + bank gender known
        if soft_g and b.gender and soft_g != b.gender:
            sims[r.person_id] = -1.0
            continue
        if soft_g and b.gender and soft_g == b.gender:
            reasons.append(ReasonCode.GENDER_SPLIT)
        sims[r.person_id] = best_sim_to_bank(emb_n, b)

    # operator override
    if operator_person_id:
        op = operator_person_id.strip().lower()
        if op in roster_ids:
            reasons.append(ReasonCode.OPERATOR)
            return Verdict(
                op,
                1.0,
                "certain",
                store_allowed=not media_flag and count_certain,
                reason_codes=reasons
                + (
                    [ReasonCode.STORE_ALLOWED, ReasonCode.STAMP_CERTAIN]
                    if not media_flag and count_certain
                    else [ReasonCode.MEDIA_VETO, ReasonCode.STORE_BLOCKED, ReasonCode.STAMP_CERTAIN]
                    if media_flag
                    else [ReasonCode.STORE_BLOCKED, ReasonCode.STAMP_CERTAIN]
                ),
                best_sim=sims.get(op, -1.0),
                posterior={op: 1.0},
            )

    # gender uniqueness boost
    if soft_g:
        same = [r for r in roster if r.gender == soft_g]
        if len(same) == 1:
            reasons.append(ReasonCode.GENDER_UNIQUE)
            # force pool to that person for ranking if sim competitive
            only = same[0].person_id
            if sims.get(only, -1) < 0 and bank_map.get(only):
                sims[only] = best_sim_to_bank(emb_n, bank_map[only])

    ranked = sorted(sims.items(), key=lambda kv: kv[1], reverse=True)
    best_id, best_sim = ranked[0]
    second_sim = ranked[1][1] if len(ranked) > 1 else -1.0
    margin = best_sim - second_sim if second_sim >= 0 else best_sim + 1.0

    details["sims"] = dict(sims)
    details["soft_gender"] = soft_g

    if best_sim < 0:
        reasons.extend([ReasonCode.UNKNOWN, ReasonCode.STAMP_NONE, ReasonCode.STORE_BLOCKED])
        return Verdict(None, 0.0, "none", False, reasons, best_sim=best_sim, second_sim=second_sim)

    if margin >= cfg.margin_stamp:
        reasons.append(ReasonCode.MARGIN_OK)
    else:
        reasons.append(ReasonCode.MARGIN_LOW)

    if best_sim >= cfg.sim_certain:
        reasons.append(ReasonCode.SIM_BAND_CERTAIN)
    elif best_sim >= cfg.sim_provisional:
        reasons.append(ReasonCode.SIM_BAND_PROVISIONAL)
    else:
        reasons.append(ReasonCode.SIM_BAND_LOW)

    # sole present gift
    if sole_present and len(roster) == 1:
        reasons.append(ReasonCode.SOLE_PRESENT)

    # voice soft
    if voice_suggest and voice_sim is not None and voice_sim >= 0.35:
        reasons.append(ReasonCode.VOICE_SOFT)
        details["voice_suggest"] = voice_suggest
        details["voice_sim"] = voice_sim

    # posterior: blend sim + small gifts
    scores = {pid: max(s, -0.99) for pid, s in sims.items() if s >= 0}
    if not scores:
        scores = {best_id: best_sim}
    # gifts
    if soft_g:
        same = [r.person_id for r in roster if r.gender == soft_g]
        if len(same) == 1 and same[0] in scores:
            scores[same[0]] += 0.15
    if sole_present and len(roster) == 1:
        scores[roster[0].person_id] = scores.get(roster[0].person_id, 0) + 0.2
    if voice_suggest and voice_sim and voice_sim >= 0.35:
        vs = voice_suggest.strip().lower()
        if vs in scores:
            scores[vs] += 0.05 * min(voice_sim, 1.0)

    scores["unknown"] = cfg.sim_provisional - 0.05
    post = _softmax(scores)
    # pick MAP excluding unknown if high enough
    ranked_p = sorted(
        ((k, v) for k, v in post.items() if k != "unknown"),
        key=lambda kv: kv[1],
        reverse=True,
    )
    if not ranked_p:
        person_id = None
        stamp_conf = post.get("unknown", 0.0)
        stamp_level = "none"
        reasons.append(ReasonCode.STAMP_NONE)
    else:
        person_id, stamp_conf = ranked_p[0]
        # margin on sim still required for non-unknown
        if best_sim < cfg.sim_provisional or (
            margin < cfg.margin_stamp and best_sim < cfg.sim_certain
        ):
            person_id = None
            stamp_level = "none"
            stamp_conf = post.get("unknown", 0.0)
            reasons.append(ReasonCode.STAMP_NONE)
        elif stamp_conf >= cfg.stamp_certain_post and best_sim >= cfg.sim_certain and margin >= cfg.margin_stamp:
            stamp_level = "certain"
            reasons.append(ReasonCode.STAMP_CERTAIN)
        elif stamp_conf >= cfg.stamp_provisional_post and best_sim >= cfg.sim_provisional:
            stamp_level = "provisional"
            reasons.append(ReasonCode.STAMP_PROVISIONAL)
        else:
            person_id = None
            stamp_level = "none"
            reasons.append(ReasonCode.STAMP_NONE)

    # store gate — all-of checklist
    store_allowed = False
    if person_id and stamp_level == "certain":
        contam, other_id, other_sim = cross_bank_contamination(
            emb_n, person_id, banks, floor=cfg.cross_person_floor
        )
        details["xbank_other"] = other_id
        details["xbank_sim"] = other_sim
        if contam:
            reasons.append(ReasonCode.XBANK_CONTAM)
        else:
            reasons.append(ReasonCode.XBANK_OK)

        media_ok = not media_flag
        if media_flag:
            reasons.append(ReasonCode.MEDIA_VETO)

        count_ok = count_certain
        margin_store_ok = margin >= cfg.margin_store or (
            ReasonCode.GENDER_UNIQUE in reasons and margin >= cfg.margin_stamp
        )
        if margin_store_ok:
            reasons.append(ReasonCode.MARGIN_STRICT_OK)

        val_ok = validation is None or validation.ok
        quality_ok = det_score is None or det_score >= cfg.det_score_floor

        store_allowed = (
            media_ok
            and count_ok
            and not contam
            and margin_store_ok
            and val_ok
            and quality_ok
            and best_sim >= cfg.sim_certain
        )
        if store_allowed:
            reasons.append(ReasonCode.STORE_ALLOWED)
        else:
            reasons.append(ReasonCode.STORE_BLOCKED)
    else:
        reasons.append(ReasonCode.STORE_BLOCKED)

    # de-dupe reasons preserving order
    seen: set[str] = set()
    uniq: list[ReasonCode] = []
    for c in reasons:
        if c.value not in seen:
            seen.add(c.value)
            uniq.append(c)

    return Verdict(
        person_id=person_id,
        stamp_confidence=float(stamp_conf),
        stamp_level=stamp_level,
        store_allowed=store_allowed,
        reason_codes=uniq,
        best_sim=float(best_sim),
        second_sim=float(second_sim),
        margin=float(margin),
        posterior=post,
        details=details,
    )
