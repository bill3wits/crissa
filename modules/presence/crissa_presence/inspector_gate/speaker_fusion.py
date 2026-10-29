"""Who is speaking — beam × voice × Argus face, over a recency-weighted set.

the User's goal, 2026-08-14:

    wire Argus pics at entry with description to populate the Oracle presence
    for User AUTH. Use microphone beam data, faceid L embeddings and voice
    embeddings along with camera for positive faceID to know who is speaking.

Every channel here is individually too weak to name someone, which is why they
have to be joined rather than chosen between. Measured on this house:

  voice   the bank SEPARATES cleanly — zero rank inversions in 855 trials — but
          at the 0.50 floor it rejects the User on 13% of his own turns. Good at
          telling people apart, mediocre at recognising them.
  beam    honest as a bearing, but the mic-frame residual is unmeasurable here
          (two clap anchors disagreed by 126 deg), so it can say "over there",
          never "at hour 8". A where-prior, not identity.
  face    the strongest single signal, and the rarest — it exists only when
          someone walked through the door and Argus caught a >=60px crop.

The join is what makes them useful: **face says who came in, recency says who is
still likely here, and voice picks among what is left.** Beam is a FaceFinder
aim clue (the User 2026-08-30) — it does not vote this score. Shrinking the
candidate set is worth more than any threshold change, because a 0.50 match
against three plausible people is a very different claim from 0.50 against the
whole bank.

## Laws this obeys

- **Voice never auths.** This SUGGESTS a speaker. Face-grade AUTH is unchanged.
- **Never invent a name.** A candidate must have entered, be on the roster, or be
  the User. An empty candidate set returns `None`, not a guess.
- **No second ledger.** Pure function over evidence handed in; writes nothing.
- **Guests are not occupancy.** Someone seen at the door is a candidate to have
  spoken, never a headcount.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

# How long after someone is seen at the door they stay a plausible speaker.
# A half-life, not a cliff: presence decays, it does not expire on a timer.
ENTRY_HALF_LIFE_SEC = 45 * 60.0
ENTRY_MAX_AGE_SEC = 6 * 3600.0

# A voice match this far above the runner-up is decisive on its own.
DECISIVE_MARGIN = 0.08
# Below this, voice contributes nothing whoever it names.
VOICE_FLOOR = 0.50

# What each channel is worth. Face dominates because it is the only one that
# ever measured a person rather than inferred them.
# W_BEAM stays 0: bearing is FF look, not a Bayes vote (BEAM-IS-FF-CLUE.md).
W_VOICE = 1.0
W_FACE = 1.4
W_BEAM = 0.0
W_RECENCY = 0.5


@dataclass
class Candidate:
    """One person who could be speaking, and why we think so."""

    person_id: str
    voice_sim: float = -1.0
    face_sim: float = -1.0
    seen_ts: float | None = None          # last door appearance
    home_hour: int | None = None          # usual seat, if the site declared one
    description: str | None = None        # Argus's words, evidence only
    score: float = 0.0
    why: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "voice_sim": round(self.voice_sim, 3) if self.voice_sim >= 0 else None,
            "face_sim": round(self.face_sim, 3) if self.face_sim >= 0 else None,
            "seen_ts": self.seen_ts,
            "home_hour": self.home_hour,
            "description": self.description,
            "score": round(self.score, 3),
            "why": list(self.why),
        }


@dataclass
class SpeakerVerdict:
    person_id: str | None
    level: str                              # strong | weak | none
    score: float = 0.0
    margin: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "speaker_fusion/v1",
            "person_id": self.person_id,
            "level": self.level,
            "score": round(self.score, 3),
            "margin": round(self.margin, 3),
            "reasons": list(self.reasons),
            "candidates": [c.as_dict() for c in self.candidates],
        }


def recency_weight(seen_ts: float | None, *, now: float | None = None) -> float:
    """1.0 at the door, decaying by half every ENTRY_HALF_LIFE_SEC.

    Someone who walked in a minute ago is far more likely to be talking than
    someone last seen this morning — but neither is impossible, so this decays
    rather than cutting off.
    """
    if not seen_ts:
        return 0.0
    now = time.time() if now is None else now
    age = max(0.0, float(now) - float(seen_ts))
    if age > ENTRY_MAX_AGE_SEC:
        return 0.0
    return math.pow(0.5, age / ENTRY_HALF_LIFE_SEC)


def beam_agreement(az_deg: float | None, home_hour: int | None,
                   *, residual_deg: float | None = None) -> float:
    """How well a bearing matches where this person usually sits. 0 when unmapped.

    Returns 0.0 — not a penalty — when the mic frame is uncalibrated. An
    unmeasurable channel must abstain, never vote. This house has
    HOUSE_MIC_AZ_RESIDUAL_DEG unset for exactly that reason.
    """
    if az_deg is None or home_hour is None or residual_deg is None:
        return 0.0
    try:
        cam = (float(az_deg) - float(residual_deg)) % 360.0
        want = (int(home_hour) % 12) * 30.0
    except (TypeError, ValueError):
        return 0.0
    d = abs((cam - want + 180.0) % 360.0 - 180.0)
    # full credit within half an hour-slot, nothing beyond a slot and a half
    if d <= 15.0:
        return 1.0
    if d >= 45.0:
        return 0.0
    return 1.0 - (d - 15.0) / 30.0


def read_entry_evidence(
    events_dir: str | Any,
    *,
    max_age_sec: float = ENTRY_MAX_AGE_SEC,
    now: float | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Argus's door observations, as `fuse_speaker` wants them.

    Reads `entry_best.json` — what Argus chose as the best FaceID-grade face on
    an entry — from recent packs. `name: null` is kept deliberately: it means
    somebody came in that we could not name, which the verdict should know.

    Counts are not derived here and never should be. The question is who, not
    how many (the User 2026-08-14).
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    now = time.time() if now is None else now
    root = _Path(str(events_dir))
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        packs = sorted(
            (d for d in root.glob("ev_*") if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[:limit]
    except OSError:
        return []
    for d in packs:
        best = d / "entry_best.json"
        if not best.is_file():
            continue
        try:
            if now - best.stat().st_mtime > max_age_sec:
                continue
            v = _json.loads(best.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = best.stat().st_mtime
        for cand in d.glob("ev_*.json"):
            try:
                ev = _json.loads(cand.read_text(encoding="utf-8"))
                if ev.get("trigger_ts"):
                    ts = float(ev["trigger_ts"])
                break
            except (OSError, ValueError, TypeError):
                break
        try:
            sim = float(v.get("sim", -1.0))
        except (TypeError, ValueError):
            sim = -1.0
        out.append({
            "person_id": v.get("name"),
            "face_sim": sim,
            "ts": ts,
            "pack": d.name,
            "face_px": v.get("face_px"),
            "soft_gender": v.get("soft_gender"),
            "face_file": v.get("face_file"),
        })
    return out


def beam_is_media(az_deg: float | None, media_hour: int | None,
                  *, residual_deg: float | None = None) -> bool:
    """Is this bearing pointing at the loudspeakers rather than at a person?

    the User 2026-08-16: "Media is 7 oclock here and should be labeled as media
    sound source." The hour has been in room_geometry all along with nothing
    consulting it.

    A loudspeaker is a voice the room can hear and a body it cannot see, and
    that mismatch has already cost: the TV authenticated as the User on 22.59%
    of turns at the old 0.35 voice threshold. Raising the threshold made the
    User harder to recognise without making the TV any less of a person — a
    bearing is what separates them.

    Abstains (False) while the mic frame is uncalibrated, exactly as
    beam_agreement does. An unmeasurable channel must not veto either.
    """
    if az_deg is None or media_hour is None or residual_deg is None:
        return False
    return beam_agreement(az_deg, int(media_hour), residual_deg=residual_deg) >= 0.5


def fuse_speaker(
    *,
    voice_sims: dict[str, float] | None = None,
    entries: Sequence[dict[str, Any]] | None = None,
    roster: Sequence[str] | None = None,
    user_id: str | None = None,
    az_deg: float | None = None,
    residual_deg: float | None = None,
    media_hour: int | None = None,
    home_hours: dict[str, int] | None = None,
    now: float | None = None,
    voice_floor: float = VOICE_FLOOR,
) -> SpeakerVerdict:
    """Name the speaker, or honestly decline to.

    `entries` are Argus door observations, newest first or any order::

        {"person_id": "user", "face_sim": 0.71, "ts": 1786735000.0,
         "description": "grey shirt, short dark hair"}

    An entry with no `person_id` still counts — it establishes that *somebody*
    came in, which is a reason to be less sure, not more.
    """
    now = time.time() if now is None else now
    voice_sims = {str(k).lower(): float(v) for k, v in (voice_sims or {}).items()}
    home_hours = {str(k).lower(): int(v) for k, v in (home_hours or {}).items()}
    reasons: list[str] = []

    # --- build the candidate set. This is the whole trick. ------------------
    cands: dict[str, Candidate] = {}

    def _c(pid: str) -> Candidate:
        pid = str(pid).strip().lower()
        if pid not in cands:
            cands[pid] = Candidate(person_id=pid, home_hour=home_hours.get(pid))
        return cands[pid]

    unnamed_entry = False
    for e in entries or []:
        pid = str((e or {}).get("person_id") or "").strip().lower()
        if not pid:
            unnamed_entry = True
            continue
        c = _c(pid)
        ts = (e or {}).get("ts")
        if ts and (c.seen_ts is None or float(ts) > c.seen_ts):
            c.seen_ts = float(ts)
            c.description = (e or {}).get("description") or c.description
        try:
            fs = float((e or {}).get("face_sim", -1.0))
        except (TypeError, ValueError):
            fs = -1.0
        if fs > c.face_sim:
            c.face_sim = fs

    for pid in roster or []:
        _c(pid)
    if user_id:
        _c(user_id)

    if not cands:
        reasons.append("no_candidates")
        return SpeakerVerdict(person_id=None, level="none", reasons=reasons)

    # Voice only scores people already in the set — never opens the bank.
    for pid, c in cands.items():
        if pid in voice_sims:
            c.voice_sim = voice_sims[pid]

    if unnamed_entry:
        reasons.append("unnamed_entry_today")

    # --- score ------------------------------------------------------------
    for c in cands.values():
        score = 0.0
        if c.voice_sim >= voice_floor:
            score += W_VOICE * c.voice_sim
            c.why.append(f"voice {c.voice_sim:.2f}")
        elif c.voice_sim >= 0:
            c.why.append(f"voice {c.voice_sim:.2f} below floor")
        if c.face_sim >= 0:
            score += W_FACE * c.face_sim
            c.why.append(f"face {c.face_sim:.2f}")
        rw = recency_weight(c.seen_ts, now=now)
        if rw > 0:
            score += W_RECENCY * rw
            c.why.append(f"seen {rw:.2f}")
        ba = beam_agreement(az_deg, c.home_hour, residual_deg=residual_deg)
        if ba > 0 and W_BEAM > 0:
            score += W_BEAM * ba
            c.why.append(f"beam {ba:.2f}")
        c.score = score

    ranked = sorted(cands.values(), key=lambda c: -c.score)
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    margin = top.score - (second.score if second else 0.0)

    if az_deg is not None and residual_deg is None:
        reasons.append("beam_unmapped_abstained")
    if beam_is_media(az_deg, media_hour, residual_deg=residual_deg):
        # The room heard something; nobody said it.
        reasons.append("beam_points_at_media")
        return SpeakerVerdict(None, "none", 0.0, 0.0, ranked, reasons)

    # --- decide ------------------------------------------------------------
    if top.score <= 0:
        reasons.append("no_channel_fired")
        return SpeakerVerdict(None, "none", 0.0, 0.0, ranked, reasons)

    # A face seen at the door plus any voice agreement is the strong case.
    strong = (top.face_sim >= 0 and top.voice_sim >= voice_floor) or (
        top.voice_sim >= voice_floor and margin >= DECISIVE_MARGIN
    )
    # One candidate and one channel is suggestive, not decisive.
    if strong:
        reasons.append("face_and_voice_agree" if top.face_sim >= 0 else "voice_clear")
        level = "strong"
    else:
        reasons.append("single_channel" if margin < DECISIVE_MARGIN else "weak_margin")
        level = "weak"
    return SpeakerVerdict(top.person_id, level, top.score, margin, ranked, reasons)
