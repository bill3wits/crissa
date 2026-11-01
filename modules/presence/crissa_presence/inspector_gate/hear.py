"""Closed-set hear: roster × voice rank × mic beam (optional camera peek).

Voice never auths. Beam is a where-prior, not identity. Residual (mic − camera
degrees at the same bearing) is an argument — unmapped until clap-at-12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .reasons import ReasonCode

LEGAL_BEAMS: tuple[int, ...] = (1, 2, 3, 8, 10, 11, 12)
ILLEGAL_BEAMS: frozenset[int] = frozenset({4, 5, 6, 7, 9})
# Door FOV wedge — Inspector can snap without leaving rest.
DOOR_FOV_BEAMS: frozenset[int] = frozenset({11, 12, 1})
# Regular home beams (this office). Guests have none.
DEFAULT_REGULARS: dict[int, str] = {8: "user", 10: "another person"}
VOICE_MARGIN = 0.08  # fox WeSpeaker a guest-OFF vs another person measured +0.115


def clock_deg(hour: int) -> float:
    """Physical camera bearing: 12 = 0°, 3 = 90°, clockwise."""
    h = int(hour)
    if h == 12:
        return 0.0
    return float((h % 12) * 30)


def az_to_clock(az_deg: float, residual_deg: float | None) -> int | None:
    """Mic-frame degrees → clock hour. None until residual is measured."""
    if residual_deg is None:
        return None
    try:
        cam = (float(az_deg) - float(residual_deg)) % 360.0
    except (TypeError, ValueError):
        return None
    hour = int(round(cam / 30.0)) % 12
    return 12 if hour == 0 else hour


def residual_from_pair(az_deg: float, hour: int) -> float:
    """mic_az − camera_az at the same physical bearing (clap at that hour)."""
    return (float(az_deg) - clock_deg(hour)) % 360.0


def residual_from_door(theta_door_deg: float) -> float:
    """Clap at the door (hour 12 / camera 0°) → residual := θ_door.

    Do not fit residual to the desk. Desk is the check, not the set.
    """
    return float(theta_door_deg) % 360.0


def desk_check_ok(
    residual_deg: float,
    desk_az_deg: float = 39.0,
    lo_deg: float = 225.0,
    hi_deg: float = 255.0,
) -> bool:
    """True if (desk_az − residual) mod 360 lands in hour-8 band [225, 255)."""
    cam = (float(desk_az_deg) - float(residual_deg)) % 360.0
    return lo_deg <= cam < hi_deg


def snap_legal_beam(hour: int | None, legal: Sequence[int] = LEGAL_BEAMS) -> int | None:
    if hour is None:
        return None
    legal_set = {int(x) for x in legal}
    if int(hour) in legal_set:
        return int(hour)
    # nearest legal hour on the circle (12 next to 1)
    def circ(a: int, b: int) -> int:
        d = abs(a - b) % 12
        return min(d, 12 - d)

    h = 12 if int(hour) == 0 else int(hour)
    # Stable: nearest, then prefer regulars 8/10, then smaller hour.
    pref = {8: 0, 10: 1}
    return min(
        legal_set,
        key=lambda x: (circ(h, x), pref.get(x, 2), x if x != 12 else 0),
    )


@dataclass
class HearVerdict:
    person_id: str | None
    suggest_level: str  # strong | weak | none
    reason_codes: list[ReasonCode] = field(default_factory=list)
    beam_hour: int | None = None
    beam_legal: int | None = None
    beam_prior: str | None = None
    voice_best: str | None = None
    voice_sim: float = -1.0
    voice_second: str | None = None
    voice_second_sim: float = -1.0
    voice_margin: float = 0.0
    peek_recommended: bool = False
    peek_beam: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "suggest_level": self.suggest_level,
            "reason_codes": [c.value for c in self.reason_codes],
            "beam_hour": self.beam_hour,
            "beam_legal": self.beam_legal,
            "beam_prior": self.beam_prior,
            "voice_best": self.voice_best,
            "voice_sim": self.voice_sim,
            "voice_second": self.voice_second,
            "voice_second_sim": self.voice_second_sim,
            "voice_margin": self.voice_margin,
            "peek_recommended": self.peek_recommended,
            "peek_beam": self.peek_beam,
            "details": self.details,
        }


def join_hear(
    *,
    roster: Sequence[str],
    voice_ranks: Sequence[tuple[str, float]] | None = None,
    az_deg: float | None = None,
    residual_deg: float | None = None,
    beam_hour: int | None = None,
    regulars: dict[int, str] | None = None,
    legal: Sequence[int] = LEGAL_BEAMS,
    voice_margin: float = VOICE_MARGIN,
) -> HearVerdict:
    """Join closed roster × voice rank × beam prior. Suggest only — never auth."""
    roster_ids = [str(x).strip().lower() for x in roster if str(x).strip()]
    roster_set = set(roster_ids)
    regs = {int(k): str(v).strip().lower() for k, v in (regulars or DEFAULT_REGULARS).items()}
    reasons: list[ReasonCode] = []

    hour = beam_hour
    if hour is None and az_deg is not None:
        hour = az_to_clock(az_deg, residual_deg)
    legal_h = snap_legal_beam(hour, legal)
    if hour is None:
        reasons.append(ReasonCode.BEAM_UNMAPPED)
    else:
        reasons.append(ReasonCode.BEAM_MAPPED)
        if legal_h is not None and legal_h != hour:
            reasons.append(ReasonCode.BEAM_ILLEGAL)

    beam_prior = None
    if legal_h is not None:
        cand = regs.get(int(legal_h))
        if cand and cand in roster_set:
            beam_prior = cand
            reasons.append(ReasonCode.BEAM_PRIOR)

    # Unfiltered ranks own the margin. Roster filter only decides who we may name.
    # A one-element ranking cannot produce a margin — never "strong" (R4 G1).
    raw_ranks = [
        (str(pid).strip().lower(), float(sim))
        for pid, sim in (voice_ranks or [])
        if str(pid).strip()
    ]
    raw_ranks.sort(key=lambda kv: -kv[1])
    ranks = [(pid, sim) for pid, sim in raw_ranks if pid in roster_set]
    v_best = ranks[0][0] if ranks else None
    v_sim = ranks[0][1] if ranks else -1.0
    v_second = ranks[1][0] if len(ranks) > 1 else None
    v_2sim = ranks[1][1] if len(ranks) > 1 else -1.0
    raw_best = raw_ranks[0][0] if raw_ranks else None
    if len(raw_ranks) >= 2:
        v_margin = float(raw_ranks[0][1]) - float(raw_ranks[1][1])
    else:
        v_margin = 0.0
    if v_best:
        reasons.append(ReasonCode.VOICE_SOFT)

    if not roster_ids:
        reasons.append(ReasonCode.POP_EMPTY)
        reasons.append(ReasonCode.STAMP_NONE)
        return HearVerdict(
            None,
            "none",
            reasons,
            beam_hour=hour,
            beam_legal=legal_h,
            beam_prior=beam_prior,
            peek_recommended=False,
            details={"roster": []},
        )

    reasons.append(ReasonCode.POP_CLOSED)
    sole = len(roster_ids) == 1
    if sole:
        reasons.append(ReasonCode.SOLE_PRESENT)

    pick: str | None = None
    level = "none"
    peek = False

    if v_best and beam_prior:
        if v_best == beam_prior:
            pick, level = v_best, "strong"
            reasons.append(ReasonCode.BEAM_AGREE)
        else:
            reasons.append(ReasonCode.BEAM_CONFLICT)
            peek = True
            pick, level = None, "none"
    elif sole:
        pick = roster_ids[0]
        # Strong only with a real two-candidate margin AND unfiltered best
        # is the sole person. One bank score of 0.09 is not a margin.
        real_margin = len(raw_ranks) >= 2 and v_margin >= voice_margin
        if raw_best == pick and real_margin:
            level = "strong"
        else:
            level = "weak"
    elif v_best and v_margin >= voice_margin and len(raw_ranks) >= 2:
        pick, level = v_best, "strong" if v_margin >= 0.15 else "weak"
        if not beam_prior:
            peek = len(roster_ids) >= 2
    elif beam_prior:
        pick, level = beam_prior, "weak"
        peek = True
    else:
        peek = len(roster_ids) >= 2
        if v_best:
            pick, level = v_best, "weak"

    if peek:
        reasons.append(ReasonCode.PEEK_RECOMMENDED)
    if pick:
        reasons.append(
            ReasonCode.STAMP_PROVISIONAL if level != "strong" else ReasonCode.HEAR_OK
        )
    else:
        reasons.append(ReasonCode.STAMP_NONE)

    peek_beam = legal_h
    if peek and peek_beam is None and beam_prior:
        for h, pid in regs.items():
            if pid == beam_prior:
                peek_beam = h
                break

    return HearVerdict(
        pick,
        level,
        reasons,
        beam_hour=hour,
        beam_legal=legal_h,
        beam_prior=beam_prior,
        voice_best=v_best,
        voice_sim=v_sim,
        voice_second=v_second,
        voice_second_sim=v_2sim,
        voice_margin=v_margin,
        peek_recommended=peek,
        peek_beam=peek_beam,
        details={
            "roster": roster_ids,
            "regulars": {str(k): v for k, v in regs.items()},
            "residual_set": residual_deg is not None,
            "raw_best": raw_best,
            "raw_n": len(raw_ranks),
        },
    )


def fox_only_files(rec: dict[str, Any]) -> bool:
    """True for the prompted fox pair (cue or matching live-room note)."""
    cue = str(rec.get("cue") or rec.get("note") or "")
    low = cue.lower()
    return "quick brown fox" in low or "prompted fox" in low
