"""Speech look: ear VAD piercing → vocative beam station → FaceID.

Puck VAD / sound.heard is hearing only — not a camera command. Two-frame
ear VAD snaps to the clock hour the vocative rode in on (usually desk 6,
not always). Session holds until session_end, then homes. AUTH HUD is the
live FaceID face. Door motion is a one-still FaceID glance, not occupancy.
"""
from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import json
import os
import urllib.request
from typing import Any

OWNER = "beam_look"
# The camera lease is one token on a ladder (camera_token.LADDER): every
# presence-owned holder yields to a higher asker through the broker
# (PresenceService.cam_take). Inspector and anything else keep the camera.
from .camera_token import LADDER as _LADDER  # noqa: E402

YIELD_OWNERS = frozenset({None, ""} | set(_LADDER))
CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
try:
    DWELL_SEC = float(os.environ.get("RAIL_BEAM_LOOK_DWELL_SEC", "8"))
except (TypeError, ValueError):
    DWELL_SEC = 8.0
try:
    LEASE_TTL = float(os.environ.get("RAIL_BEAM_LOOK_LEASE_TTL", "45"))
except (TypeError, ValueError):
    LEASE_TTL = 45.0


def _pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


# Two 0.3 s ear speech frames. Puck VAD alone is not this.
IMPULSE_SPEECH_FRAMES = 2
# Ear VAD piercing — not puck beam, not ASR-late stt_voice.
VAD_LOOK = frozenset({"vad", "impulse", "two_word"})
IMPULSE_REASONS = VAD_LOOK | frozenset({"stt_voice"})
SESSION_LOOK = frozenset(
    {"wake", "hey_zoe", "hey_crissa", "session", "session_open", "crissa"}
)


_MEDIA_SNAP: list = [0.0, {}]


def fetch_media_status() -> dict:
    """STT panel media_gate. Cached 1 s. Empty on miss — never block the ear."""
    if _pytest():
        return dict(_MEDIA_SNAP[1] or {})
    now = __import__("time").time()
    if now - float(_MEDIA_SNAP[0] or 0) < 1.0 and _MEDIA_SNAP[1]:
        return dict(_MEDIA_SNAP[1])
    try:
        with urllib.request.urlopen(
            _ports.url("stt", "/panel/state"), timeout=0.35
        ) as r:
            body = json.loads(r.read().decode())
        m = body.get("media") if isinstance(body, dict) else {}
        if not isinstance(m, dict):
            m = {}
    except Exception:
        m = {}
    _MEDIA_SNAP[0] = now
    _MEDIA_SNAP[1] = m
    return dict(m)


def _hour_near(a: int | None, b: int | None, n: int = 1) -> bool:
    if a is None or b is None:
        return False
    d = abs(int(a) - int(b)) % 12
    d = min(d, 12 - d)
    return d <= n


def media_blocks_look(hour: int | None, *, media: dict | None = None) -> str | None:
    """A loudspeaker is not a speaker. Skip look; caller homes the lease."""
    from .seat_slots import media_station

    ms = media_station()
    m = media if media is not None else fetch_media_status()
    if m.get("media_on") or m.get("verdict") == "media":
        return "media_on"
    if hour is not None and ms is not None and int(hour) == int(ms):
        return "media_beam"
    phase = str(m.get("phase") or "")
    # Exact media hour only. Adjacent desk 6 must still snap — characterizing
    # the TV must not veto a vocative at the User seat.
    if (
        phase in ("characterize", "media_hold")
        and hour is not None
        and ms is not None
        and int(hour) == int(ms)
    ):
        return "media_locus"
    return None


def ff_at_beam_blocked(
    *,
    user_auth: bool = False,
    visit_lamp: str = "",
    locked: str | None = None,
    user_id: str = "user",
    age_s: float | None = None,
) -> str | None:
    """Skip FaceFinder only while Secure.

    None = the beam vector may run. Law 1: BAU is FF-for-AUTH — tracking
    continues so AUTH can persist, so held AUTH / a fresh visor lock are NOT
    a skip (the User 2026-09-05; the old "auth" returns were sticky-AUTH). Law 2:
    Secure is the pause of that tracking, camera on the door.
    user_auth / locked / age_s are accepted for callers and ignored.
    """
    if str(visit_lamp or "").strip().lower() == "sage":
        return "secure"
    return None


def look_ok(*, already_looking: bool = False, impulse: bool = False, reason: str = "") -> str | None:
    """None = the look may run.

    A look starts on vocative ``session_open``, not paused keyboard VAD.
    Puck ``sound.heard`` and ASR-late ``stt_voice`` do not aim. In-session
    VAD does not chase a new az.
    """
    why = str(reason or "").strip().lower()
    if impulse and not why:
        why = "impulse"
    if why in VAD_LOOK:
        if already_looking:
            return "already_looking"
        return "need_session"
    if why in SESSION_LOOK:
        return None
    return "need_session"


def hour_for(*, hour: int | None = None, az: float | None = None) -> int | None:
    if hour is not None:
        try:
            h = int(hour)
        except (TypeError, ValueError):
            h = None
        else:
            if 1 <= h <= 12:
                return h
    from .seat_shot import hour_from_az

    return hour_from_az(az)


def decide(
    *,
    hour: int | None = None,
    az: float | None = None,
    person_id: str | None = None,
    user_id: str = "user",
    lease_owner: str | None = None,
    current_hour: int | None = None,
    looking: bool = False,
) -> dict[str, Any]:
    """Pure station pick. Busy / linger / headcount are not inputs."""
    owner = str(lease_owner or "").strip() or None
    if owner and owner not in YIELD_OWNERS:
        return {
            "ok": True,
            "skipped": "inspector",
            "owner": owner,
            "hour": None,
            "pose": None,
        }
    from .seat_shot import pose_for_hour
    from .seat_slots import media_station, user_seat

    h = hour_for(hour=hour, az=az)
    uid = str(user_id or "user").strip().lower()
    pid = str(person_id or "").strip().lower() or None
    block = media_blocks_look(h)
    if block:
        return {
            "ok": True,
            "skipped": block,
            "hour": h,
            "pose": None,
        }
    ms = media_station()
    if ms is not None and h is not None and int(h) == int(ms):
        return {
            "ok": True,
            "skipped": "media_beam",
            "hour": h,
            "pose": None,
        }
    pose = pose_for_hour(h) if h is not None else None
    if pose is None and pid == uid:
        try:
            pose = pose_for_hour(int(user_seat()))
        except Exception:
            pose = pose_for_hour(6)
    if pose is None:
        return {
            "ok": True,
            "skipped": "no_station",
            "hour": h,
            "pose": None,
        }
    stop_h = pose.get("station")
    if looking and current_hour is not None and stop_h == current_hour:
        return {
            "ok": True,
            "skipped": "same_station",
            "hour": stop_h,
            "stop": pose.get("id"),
            "pose": pose,
        }
    return {
        "ok": True,
        "skipped": None,
        "hour": stop_h,
        "stop": pose.get("id"),
        "pose": pose,
    }


def lease_owner(health: dict | None) -> str | None:
    lease = (health or {}).get("lease") or {}
    if not isinstance(lease, dict):
        return None
    return str(lease.get("owner") or "").strip() or None


def camera_health() -> dict[str, Any]:
    if _pytest():
        return {"ok": True, "lease": None}
    if _ports.camera_off():                      # no camera: answered here, never asked (2026-10-10)
        return {"ok": False, "lease": None, "camera": "off"}
    try:
        with urllib.request.urlopen(f"{CAMERA}/v1/health", timeout=0.4) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "lease": None, "error": f"{type(e).__name__}"}


def _cam_post(path: str, body: dict, timeout: float = 8.0) -> dict[str, Any]:
    from . import room_sweep as rs

    return rs._post(path, body, timeout=timeout)


def preempt_mill(health: dict | None) -> bool:
    """Steal only a mill lease. Inspector stays. Returns True if mill was stolen."""
    if lease_owner(health) != "door_mill":
        return False
    if _pytest():
        return True
    try:
        _cam_post(
            "/v1/pause",
            {
                "owner": OWNER,
                "ttl_sec": LEASE_TTL,
                "reason": "speech",
                "steal": True,
            },
            timeout=2.0,
        )
        return True
    except Exception:
        return False


def aim_hold(
    pose: dict[str, Any],
    *,
    lease_id: str | None = None,
    engage: float = 2.0,
    n: int = 2,
    owner: str = OWNER,
    ttl: float | None = None,
) -> dict[str, Any]:
    """Aim a station and stay. Caller homes on quiet. Not shot_at (that homes).

    ``lease_id`` is the broker's token (camera_token); ``owner`` names who
    holds it, so the renew here keeps the grabber's owner in step."""
    from . import room_sweep as rs
    from .seat_shot import SETTLE_SEC, sweep_zoom

    out: dict[str, Any] = {"ok": False, "frames": [], "lease_id": lease_id}
    if _pytest():
        out["ok"] = True
        out["skipped"] = "pytest"
        return out
    if pose.get("home"):
        try:
            jpeg = rs._snapshot()
        except Exception:
            jpeg = None
        if jpeg:
            out["frames"] = [jpeg]
            out["ok"] = True
        out["home"] = True
        return out
    health = camera_health()
    holder = lease_owner(health)
    if holder and holder not in YIELD_OWNERS and holder != owner:
        out["error"] = "inspector"
        out["owner"] = holder
        return out
    if not lease_id:
        preempt_mill(health)
    try:
        lease = _cam_post(
            "/v1/lease",
            {"owner": owner, "ttl_sec": float(ttl or LEASE_TTL), "lease_id": lease_id},
            timeout=8.0,
        )
    except Exception as e:
        out["error"] = f"lease:{type(e).__name__}"
        return out
    if not lease.get("ok"):
        out["error"] = "camera_leased"
        out["lease"] = lease
        return out
    lid = lease.get("lease_id")
    out["lease_id"] = lid
    try:
        aim = _cam_post(
            "/v1/aim",
            {
                "pan": int(pose["pan"]),
                "tilt": int(pose.get("tilt") or 0),
                "zoom": sweep_zoom(),
                "lease_id": lid,
            },
            timeout=25.0,
        )
        out["aim_ok"] = bool(aim.get("ok"))
        if not aim.get("ok"):
            out["error"] = "aim_failed"
            return out
        import time

        time.sleep(max(0.4, float(SETTLE_SEC)))
        try:
            _cam_post("/v1/facefinder", {"on": True, "lease_id": lid}, timeout=5.0)
            time.sleep(max(0.8, float(engage)))
        except Exception:
            time.sleep(0.8)
        for _ in range(max(1, int(n))):
            try:
                out["frames"].append(rs._snapshot())
            except Exception:
                break
            time.sleep(0.4)
        out["ok"] = bool(out["frames"])
        out["pan"] = int(pose["pan"])
        out["stop"] = pose.get("id")
        out["hour"] = pose.get("station")
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{e}"[:160]
    return out


def renew(lease_id: str | None, *, owner: str = OWNER, ttl: float | None = None) -> dict[str, Any]:
    if not lease_id or _pytest():
        return {"ok": True, "skipped": "no_lease" if not lease_id else "pytest"}
    try:
        return _cam_post(
            "/v1/lease",
            {"owner": owner, "ttl_sec": float(ttl or LEASE_TTL), "lease_id": lease_id},
            timeout=2.0,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}"}


def rest(lease_id: str | None, verify=None) -> dict[str, Any]:
    """Door rest, FF off, lease back. Watchdogs resume.

    `verify` (Init v2's verified home, the User 2026-10-04): called after the door rest and BEFORE the lease is released
    and the watchdogs resume, so what it reads is where the rest put the camera - not wherever a watchdog has moved it
    since. Its answer is out["verify"]; it never stops the release."""
    from . import room_sweep as rs
    from .seat_shot import go_home

    out: dict[str, Any] = {"ok": True}
    if _pytest():
        out["skipped"] = "pytest"
        return out
    try:
        out["home"] = go_home(rs, lease_id)
    except Exception as e:
        out["home_err"] = f"{type(e).__name__}"
    if verify is not None:
        try:
            out["verify"] = verify()
        except Exception as e:  # noqa: BLE001
            out["verify"] = {"error": type(e).__name__}
    if lease_id:
        try:
            _cam_post("/v1/lease/release", {"lease_id": lease_id}, timeout=8.0)
            out["released"] = True
        except Exception:
            out["released"] = False
    try:
        _cam_post("/v1/resume", {"force": True}, timeout=2.0)
        out["resumed"] = True
    except Exception:
        out["resumed"] = False
    return out
