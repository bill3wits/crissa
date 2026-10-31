"""AUTH-first helpers — sudo User face AUTH is the control plane.

See docs/AUTH-FIRST-PLAN-20260812.md.
No Oracle. Inventory headcount is not required to hold AUTH.
"""
from __future__ import annotations

from . import paths as _paths

import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

GOOD_MORNING_TEXT = os.environ.get(
    "HOUSE_GOOD_MORNING_TEXT", "Good Morning."
).strip() or "Good Morning."


# THE GREETING FITS THE HOUR, AND SETUP IS NOT A NEW DAY (round 29; .101 2026-10-17: "Good Morning." spoke during Setup,
# at 2:42 pm, right after Setup's first face grant). The first grant of the day greets by the clock (morning before
# noon, afternoon before 6 pm, evening after); HOUSE_GOOD_MORNING_TEXT, when set, is said as it is. A grant while Setup
# runs (the state home has no done init.json) marks the day and says nothing. Kills CRISSA_GREET_BY_HOUR=0,
# CRISSA_GREET_IN_SETUP=1.
def greeting_text(hour: int | None = None) -> str:
    if os.environ.get("HOUSE_GOOD_MORNING_TEXT", "").strip():
        return GOOD_MORNING_TEXT
    if os.environ.get("CRISSA_GREET_BY_HOUR", "1").strip().lower() in ("0", "off", "false", "no"):
        return GOOD_MORNING_TEXT
    try:
        if _paths.is_house():                     # the house keeps its own words
            return GOOD_MORNING_TEXT
    except Exception:  # noqa: BLE001
        pass
    h = time.localtime().tm_hour if hour is None else int(hour)
    return "Good morning." if 4 <= h < 12 else ("Good afternoon." if 12 <= h < 18 else "Good evening.")


def setup_running(home: Path | None = None) -> bool:
    """True while this install's Setup has not finished (a pack's init.json missing or not done). The house never."""
    if os.environ.get("CRISSA_GREET_IN_SETUP", "0").strip() == "1":
        return False
    try:
        if _paths.is_house():
            return False
    except Exception:  # noqa: BLE001
        return False
    import json
    try:
        d = json.loads(((home or _paths.HOME) / "init.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not (isinstance(d, dict) and d.get("done")):
        return True
    return opener_pending(home)


# NO GREETING BEFORE HER OPENER (round 35, .101 round 34: "Good afternoon came in there when she sat down and her lips did
# not move"): Setup writes init.json done before her walk-in and her opener, and the camera's first grant after Setup
# handed it back greeted in that gap, on the walk-in's frames. Setup's end is her opener's landing record
# (init-run.json `landing.done_at`, written after her opener): until it is there - at most OPENER_HOLD_S after the walk
# finished - Setup counts as running and her opener is the greeting. Kill CRISSA_GREET_AFTER_OPENER=0.
OPENER_HOLD_S = 180.0


def opener_pending(home: Path | None = None, now: float | None = None) -> bool:
    if os.environ.get("CRISSA_GREET_AFTER_OPENER", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    import json
    import time as _t
    try:
        r = json.loads(((home or _paths.HOME) / "init-run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(r, dict) or r.get("status") != "done" or (r.get("landing") or {}).get("done_at"):
        return False
    try:
        ended = float(r.get("ended_t") or 0.0)
    except (TypeError, ValueError):
        ended = 0.0
    return bool(ended) and ((_t.time() if now is None else now) - ended) < OPENER_HOLD_S


def default_day_flag_path(uid: str = "user") -> Path:
    root = Path(
        os.environ.get(
            "HOUSE_AUTH_DAY_DIR",
            str(_paths.AUTH_DAY),
        )
    ).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{(uid or 'user').strip().lower()}.day"


def local_today() -> str:
    return date.today().isoformat()


def should_emit_good_morning(
    uid: str,
    *,
    day_path: Path | None = None,
    today: str | None = None,
) -> bool:
    """True if this is the first User AUTH of the local calendar day.

    Midnight roll is intentional for this house (the User's day starts ~02:00).
    Claude 2026-08-12 02:10 retracted a wake-hour ask — do not reintroduce
    HOUSE_WAKE_HOUR here without the User. house-keeper defers to any same-day claim.
    """
    path = day_path or default_day_flag_path(uid)
    day = today or local_today()
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == day:
            return False
    except Exception:
        pass
    return True


def record_good_morning_day(
    uid: str,
    *,
    day_path: Path | None = None,
    today: str | None = None,
) -> str:
    path = day_path or default_day_flag_path(uid)
    day = today or local_today()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(day + "\n", encoding="utf-8")
    return day


# NO SCRIPTED GREETING IN A PACK (the User 2026-10-26, his walk of 1.0.1: "could we please remove the scripted good
# morning. it fires all the time while developing restarts ... It is mindless anyway. We can coach to that in SOUL"):
# the first-grant-of-the-day line is the house's; a pack says nothing by script and her SOUL greets in conversation.
# CRISSA_GREETING=1 brings the line back in a pack.
def greeting_off(env=None) -> bool:
    e = os.environ if env is None else env
    try:
        if _paths.is_house():
            return False
    except Exception:  # noqa: BLE001
        return False
    return str(e.get("CRISSA_GREETING", "0")).strip().lower() not in ("1", "true", "on", "yes")


def maybe_good_morning(
    uid: str,
    *,
    day_path: Path | None = None,
    today: str | None = None,
    speak: Callable[[str], Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Emit Good Morning at most once per local calendar day. Returns emit result."""
    uid = (uid or "user").strip().lower()
    if not should_emit_good_morning(uid, day_path=day_path, today=today):
        return {
            "emitted": False,
            "reason": "already_today",
            "text": GOOD_MORNING_TEXT,
            "uid": uid,
        }
    day = record_good_morning_day(uid, day_path=day_path, today=today)
    if setup_running():
        return {"emitted": False, "reason": "setup_running", "text": "", "uid": uid, "day": day}
    if greeting_off():
        return {"emitted": False, "reason": "off: a pack greets through her SOUL", "text": "", "uid": uid, "day": day}
    text = greeting_text()
    out: dict[str, Any] = {
        "emitted": True,
        "reason": "first_auth_of_day",
        "text": text,
        "uid": uid,
        "day": day,
        "dry_run": bool(dry_run),
    }
    if dry_run or speak is None:
        return out
    try:
        speak(text)
        out["spoke"] = True
    except Exception as e:
        out["spoke"] = False
        out["speak_error"] = f"{type(e).__name__}: {e}"[:160]
    return out


def default_speak_good_morning(text: str) -> None:
    """Best-effort house speak (non-PA Cosy path preferred for greeting)."""
    import subprocess
    import sys

    script = _paths.PACK / "scripts" / "house-speak.py"
    if script.is_file():
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--voice",
                os.environ.get("HOUSE_GREET_VOICE", "crissa"),
                "--text",
                text,
            ],
            timeout=60,
            check=False,
            capture_output=True,
        )
        return
    # Fallback PA (may be muted outside hours)
    try:
        import importlib.util

        ann = _paths.announce_path()
        if ann.is_file():
            spec = importlib.util.spec_from_file_location("house_announce", ann)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "announce"):
                    mod.announce(text, category="protocol")
    except Exception:
        pass


def compute_auth_tier(
    *,
    user_auth: bool,
    face_sticky: bool = False,
    hold_ts: float | None = None,
    last_voice_match: bool = False,
    last_voice_ts: float | None = None,
    reason: str = "",
    now: float | None = None,
    fresh_sec: float | None = None,
    stale_sec: float | None = None,
) -> dict[str, Any]:
    """Authority tier for sudo User (COMMS §3) — decay authority, not identity.

    Tiers (published always; enforcement is opt-in via RAIL_AUTH_TIER_ENFORCE):
      fresh     — face or User voice within fresh_sec
      held      — sticky AUTH, quiet room, no contrary evidence
      stale     — no face and no User voice for stale_sec (still AUTH)
      contested — wrong_voice while sticky held
      absent    — not authed

    Does **not** clear sticky. Thresholds default from env (proposals).
    """
    now = time.time() if now is None else float(now)
    try:
        fresh_sec = float(
            fresh_sec
            if fresh_sec is not None
            else os.environ.get("RAIL_AUTH_FRESH_SEC", "300")
        )
    except (TypeError, ValueError):
        fresh_sec = 300.0
    try:
        stale_sec = float(
            stale_sec
            if stale_sec is not None
            else os.environ.get("RAIL_AUTH_STALE_SEC", "1800")
        )
    except (TypeError, ValueError):
        stale_sec = 1800.0

    reason_l = (reason or "").strip().lower()
    if not user_auth:
        return {
            "auth_tier": "absent",
            "auth_age_s": None,
            "face_age_s": None,
            "voice_age_s": None,
            "fresh_sec": fresh_sec,
            "stale_sec": stale_sec,
            "enforce": os.environ.get("RAIL_AUTH_TIER_ENFORCE", "0")
            in ("1", "true", "yes"),
        }

    face_age = None
    if hold_ts:
        try:
            face_age = max(0.0, now - float(hold_ts))
        except (TypeError, ValueError):
            face_age = None
    voice_age = None
    if last_voice_ts and last_voice_match:
        try:
            voice_age = max(0.0, now - float(last_voice_ts))
        except (TypeError, ValueError):
            voice_age = None

    ages = [a for a in (face_age, voice_age) if a is not None]
    auth_age = min(ages) if ages else (face_age if face_age is not None else voice_age)

    if reason_l == "wrong_voice" or (
        reason_l.startswith("wrong") and face_sticky
    ):
        tier = "contested"
    elif auth_age is not None and auth_age <= fresh_sec:
        tier = "fresh"
    elif auth_age is None or auth_age >= stale_sec:
        # no recent evidence, or evidence older than stale window
        if face_age is not None and face_age >= stale_sec and (
            voice_age is None or voice_age >= stale_sec
        ):
            tier = "stale"
        elif auth_age is None and face_sticky:
            # sticky with no usable timestamps → held (not stale guess)
            tier = "held"
        elif auth_age is not None and auth_age >= stale_sec:
            tier = "stale"
        else:
            tier = "held"
    else:
        tier = "held"

    return {
        "auth_tier": tier,
        "auth_age_s": round(auth_age, 1) if auth_age is not None else None,
        "face_age_s": round(face_age, 1) if face_age is not None else None,
        "voice_age_s": round(voice_age, 1) if voice_age is not None else None,
        "fresh_sec": fresh_sec,
        "stale_sec": stale_sec,
        "enforce": os.environ.get("RAIL_AUTH_TIER_ENFORCE", "0")
        in ("1", "true", "yes"),
    }


def compute_auth_provenance(
    *,
    face_sticky: bool,
    granted_by: str | None,
    hold_ts: float | None = None,
    last_voice_match: bool = False,
    last_voice_ts: float | None = None,
    this_turn_user_voice: bool = False,
    now: float | None = None,
    fresh_sec: float | None = None,
) -> dict[str, Any]:
    """Product AUTH provenance (Phase I). Unlock is never granted_by.

    Tiers (nothing new may read the user_auth bool):
      none     — no face sticky / operator
      chat     — sticky or operator; tools not allowed
      tools    — sticky + this-turn User voice
      identity — FRESH face sticky (face_age ≤ fresh_sec) + this-turn User voice
    """
    now = time.time() if now is None else float(now)
    try:
        fresh_sec = float(
            fresh_sec
            if fresh_sec is not None
            else os.environ.get("RAIL_AUTH_FRESH_SEC", "300")
        )
    except (TypeError, ValueError):
        fresh_sec = 300.0

    gb = (granted_by or "").strip().lower() or None
    if gb not in ("face", "operator"):
        gb = "face" if face_sticky else None

    face_age = None
    if hold_ts:
        try:
            face_age = max(0.0, now - float(hold_ts))
        except (TypeError, ValueError):
            face_age = None
    voice_age = None
    if last_voice_ts and last_voice_match:
        try:
            voice_age = max(0.0, now - float(last_voice_ts))
        except (TypeError, ValueError):
            voice_age = None

    sticky = bool(face_sticky or gb == "operator")
    voice = bool(this_turn_user_voice)
    face_fresh = bool(face_age is not None and face_age <= fresh_sec)

    if not sticky:
        tier = "none"
        gb = None
    elif voice and face_fresh:
        tier = "identity"
    elif voice:
        tier = "tools"
    else:
        tier = "chat"

    return {
        "granted_by": gb,
        "face_age_s": round(face_age, 1) if face_age is not None else None,
        "voice_age_s": round(voice_age, 1) if voice_age is not None else None,
        "tier": tier,
        "fresh_sec": fresh_sec,
    }


def product_tier_from_presence(presence: dict | None) -> str:
    """Read published product tier; fail closed to none."""
    p = presence if isinstance(presence, dict) else {}
    user = p.get("user") if isinstance(p.get("user"), dict) else {}
    prov = user.get("auth_provenance") if isinstance(user.get("auth_provenance"), dict) else {}
    t = str(prov.get("tier") or user.get("tier") or "").strip().lower()
    if t in ("none", "chat", "tools", "identity"):
        return t
    return "none"


def identity_ok_from_presence(presence: dict | None) -> tuple[bool, str]:
    """store_face / registration: identity tier only."""
    t = product_tier_from_presence(presence)
    if t == "identity":
        return True, "identity"
    return False, f"need_identity_tier:{t or 'none'}"


def is_sudo_user(person_id: str, user_id: str) -> bool:
    """Only the configured house User gets AUTH product path."""
    return (person_id or "").strip().lower() == (user_id or "").strip().lower()


def host_lock_state(*, probe: bool | None = None) -> bool | None:
    """True = unlocked, False = locked, None = unknown.

    Order: HOUSE_HOST_UNLOCKED / HOUSE_SESSION_UNLOCKED env, then optional
    loginctl LockedHint when HOUSE_HOST_UNLOCK_PROBE=1. systemd --user has
    no XDG_SESSION_ID; Display= is the seat session.

    Unknown (probe off, loginctl fail) must **not** de-AUTH. Known locked
    (the User 2026-08-24) does: lock drops User AUTH; unlock is a Face grab.
    """
    for key in ("HOUSE_HOST_UNLOCKED", "HOUSE_SESSION_UNLOCKED"):
        if key in os.environ:
            s = str(os.environ.get(key) or "").strip().lower()
            if s in ("1", "true", "yes", "on", "unlocked"):
                return True
            if s in ("0", "false", "no", "off", "locked"):
                return False
            return None
    do_probe = (
        probe
        if probe is not None
        else os.environ.get("HOUSE_HOST_UNLOCK_PROBE", "0").strip().lower()
        in ("1", "true", "yes", "on")
    )
    if not do_probe:
        return None
    try:
        import subprocess

        sid = (os.environ.get("XDG_SESSION_ID") or "").strip()
        if not sid:
            uid = (
                os.environ.get("USER")
                or os.environ.get("LOGNAME")
                or "agent"
            )
            sid = subprocess.check_output(
                ["loginctl", "show-user", uid, "-p", "Display", "--value"],
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                text=True,
            ).strip()
        if not sid:
            return None
        out = subprocess.check_output(
            ["loginctl", "show-session", sid, "-p", "LockedHint", "--value"],
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            text=True,
        ).strip().lower()
    except Exception:
        return None
    if out in ("yes", "true", "1"):
        return False
    if out in ("no", "false", "0"):
        return True
    return None


def host_unlocked_signal(*, probe: bool | None = None) -> bool:
    """True only when the host is *known* unlocked. Unknown → False.

    Do not use this bool to de-AUTH — unknown would look locked. Use
    host_lock_state() (False = known locked) for the lock drop.
    """
    return host_lock_state(probe=probe) is True


# Law 1 (docs/VISIT-LAMP.md, the User 2026-09-05/06): all AUTH requires FaceFinder
# + FaceID. These ``_note_face_auth`` sources are FaceFinder looks — a
# leased aim at the seat (or the beam station) with the engage, then FaceID
# on the stills. Everything else (door stills, ring / FOV snapshots, soft
# IDs, Oracle seat guesses) may refresh a hold but never mint one.
FF_GRANT_SOURCES: tuple[str, ...] = (
    "visit_lamp_click",  # Secure click On → seat_shot → FaceID
    "secure_token",  # restart / occupancy token → same seat look, then sage
    "restart_token",  # alias: token FaceFinder hit
    "beam_look",  # FF-at-beam session look
    "desk_ff",  # _desk_look_once stills
    "desk",
    "facefind",
    "door_chase_ff",  # chase's one door-pan FF engage
    "door_ff",  # the same engage, as ensure_user_auth reports it
    "reauth_ladder_ff",  # voice-here-face-not ladder → seat look
    "unlock_ff",  # host unlock → seat look
    "busy_auth_scan",  # Busy-off forced parade
    "busy_off_recheck",  # Busy-off seat_shot
    "operator",
)


def is_ff_source(source: str | None) -> bool:
    s = str(source or "").strip().lower()
    if not s:
        return False
    return any(s == k or s.startswith(k + ":") or s.startswith(k + "_") for k in FF_GRANT_SOURCES)
