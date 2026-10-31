"""Speaker stack for CACrissa — always-on values about the speaker.

the User 2026-08-30: beam is a clock reading (hour + tenths from HID az), not a
percent. Same Speaker payload as embeds; does not wait on the voice stamp
and does not vote stack. Law: docs/BEAM-IS-FF-CLUE.md
"""

from __future__ import annotations

from typing import Any


def clock_from_az(az_deg: float | None) -> float | None:
    """Mic-frame az → clock hours with tenths. 0° = 12.0, 180° = 6.0, 191° = 6.4."""
    if az_deg is None:
        return None
    try:
        cam = float(az_deg) % 360.0
    except (TypeError, ValueError):
        return None
    raw = cam / 30.0
    tenth = round(raw + 1e-9, 1)
    if tenth <= 0.0 or tenth >= 12.0:
        return 12.0
    if tenth < 1.0:
        return round(12.0 + tenth, 1)
    return tenth


def stack_speaker(
    *,
    beam_hour: int | None = None,
    az_deg: float | None = None,
    user_seat: int = 6,
    voice_sim: float | None = None,
    face_sim: float | None = None,
    sex_live: str | None = None,
    sex_user: str | None = None,
    camera: float | None = None,
    voice_thr: float = 0.35,
    face_thr: float = 0.60,
) -> dict[str, Any]:
    """Return beam as clock tenths; camera/embeds/sex/stack 0..1."""
    try:
        seat = int(user_seat)
    except (TypeError, ValueError):
        seat = 6
    clock = clock_from_az(az_deg)
    hour: int | None
    try:
        hour = int(beam_hour) if beam_hour is not None else None
    except (TypeError, ValueError):
        hour = None
    if hour is None and clock is not None:
        hour = 12 if clock >= 11.95 or clock < 0.5 else int(round(clock))
        if hour == 0:
            hour = 12
    # Clock tenths, not a 0..1 identity score (that read as 50%).
    if clock is not None:
        beam = clock
    elif hour is not None:
        beam = float(hour)
    else:
        beam = 0.0

    def _f(x: float | None) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    vs, fs = _f(voice_sim), _f(face_sim)
    voice_ok = vs is not None and vs >= float(voice_thr)
    face_ok = fs is not None and fs >= float(face_thr)
    # B is this-turn voice. Face is its own cell. Never max() them into
    # "embeds" — that published occupancy sticky as a live voice match.
    embeds_ok = bool(voice_ok)
    embeds = min(1.0, vs) if vs is not None else None

    sl = (sex_live or "").strip().upper()[:1] or None
    su = (sex_user or "").strip().upper()[:1] or None
    if sl not in ("M", "F"):
        sl = None
    if su not in ("M", "F"):
        su = None
    sex: float | None
    sex_ok: bool | None
    if sl and su:
        sex_ok = sl == su
        sex = 1.0 if sex_ok else 0.0
    else:
        sex_ok = None
        sex = None

    try:
        cam = max(0.0, min(1.0, float(camera))) if camera is not None else 0.0
    except (TypeError, ValueError):
        cam = 0.0

    # Beam is display + FF clue only. It must not vote stack (elsewhere
    # must not look like a failed identity; desk must not be 50% User).
    if embeds_ok and sex_ok is True:
        stack = 1.0
    elif cam >= 1.0 and sex_ok is True:
        stack = 1.0
    else:
        stack = cam * 0.5

    return {
        "beam": beam,
        "camera": round(cam, 3),
        "embeds": None if embeds is None else round(embeds, 3),
        "voice": None if vs is None else round(min(1.0, vs), 3),
        "face": None if fs is None else round(min(1.0, fs), 3),
        "sex": sex,
        "stack": round(float(stack), 3),
        "beam_hour": hour,
        "user_seat": seat,
        "embeds_ok": embeds_ok,
        "voice_ok": voice_ok,
        "face_ok": face_ok,
        "sex_ok": sex_ok,
        "sex_live": sl,
        "sex_user": su,
    }


def voice_bar(person_id: str | None, env: dict | None = None) -> float:
    """The voice bar the EAR uses for this person (rail-stt voice_bank.match_sim_for): RAIL_VOICE_MATCH_SIM_<ID>, else
    RAIL_VOICE_MATCH_SIM, else 0.50. The speaker row graded every voice on the base 0.50 while the ear accepted the
    User at his own 0.40, so a 0.40-0.50 match the ear took read gold (the User 2026-10-03). Kill:
    RAIL_PRESENCE_PERSON_BAR=0 (base bar for everyone, as before)."""
    import os
    e = os.environ if env is None else env
    def _f(v, d):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d
    base = _f(e.get("RAIL_VOICE_MATCH_SIM", "0.50"), 0.50)
    if str(e.get("RAIL_PRESENCE_PERSON_BAR", "1")).strip().lower() in ("0", "false", "no", "off"):
        return base
    pid = (person_id or "").strip().upper()
    own = e.get(f"RAIL_VOICE_MATCH_SIM_{pid}") if pid else None
    return _f(own, base) if own not in (None, "") else base
