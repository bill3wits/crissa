"""User-only / user_auth_only mode — BAU vs guest vs silent.

the User 2026-08-03: polite guest chat (weather etc.) without names/ID files;
BAU (house commands + full free-air) only when Oracle says User is face-authed
AND the current stamp is the User's voice (the user).

Mode flag: <crissa home>/house-mode.json
  mode: user_auth_only | house
  auth.user_id: the user
  guest: polite | silent   (default polite for the User's weather/chat path)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .home import HOME

HOUSE_MODE_PATH = Path(
    os.environ.get("HOUSE_MODE_PATH")
    or (HOME / "house-mode.json")
)

# ECAPA floor for "this utterance is the User" (desk bank is thin; soft-fill was 0.55)
USER_VOICE_MIN_SIM = float(os.environ.get("HOUSE_USER_VOICE_MIN_SIM", "0.48"))
# Below this emb-vs-User score we treat the turn as *not* the User (no need to
# name who). Measured 2026-08-12: guest board cmds rode session_held with
# is_user_block sim 0.05–0.36 while STT correctly refused person_id=the user.
NOT_USER_VOICE_SIM = float(os.environ.get("HOUSE_NOT_USER_VOICE_SIM", "0.40"))
# Tier-1 (house commands) accepts a strong match this recent within a held
# session; Tier-2 (security and identity) always demands this-turn voice.
TIER1_MAX_AGE_S = float(os.environ.get("HOUSE_TIER1_MAX_VOICE_AGE_S", "180"))
# THE ORACLE RULE (the User 2026-10-01): "when the face check says it's me, a low voice score never drops me to
# guest. Voice only decides when the camera can't." Measured 10-01 11:54:53: face 0.615 (live), voice 0.401 - her
# reply's tail in his turn - and the AUTH-VOICE law made it guest. Under this switch a FRESH live the face check match of
# the User at or above ORACLE_FACE_MIN (the camera's own 0.60 bar) decides the turn: BAU, tools, house commands.
# NOT a look in flight, an off-camera hold, Secure or the 5 s empty-FOV tail - there the camera cannot see him, so
# voice still decides. Tier 2 (security / identity) still demands this-turn voice. The cost: an unenrolled speaker
# talking while he is in frame gets his level (replay 10-01: 1 of 18 turns changes, the 11:54:53 one).
# OFF by default: HOUSE_ORACLE_FACE_WINS=1 arms it.
ORACLE_FACE_WINS = os.environ.get("HOUSE_ORACLE_FACE_WINS", "0").strip().lower() in ("1", "true", "yes", "on")
# A fixed camera's look decides by its age, never "in flight" (round 13, 2026-10-12). 0 = the old reading.
FIXED_LOOK_NOT_PENDING = os.environ.get("CRISSA_FIXED_LOOK_NOT_PENDING", "1").strip().lower() not in ("0", "false", "no", "off")
ORACLE_FACE_MIN = float(os.environ.get("HOUSE_ORACLE_FACE_MIN", "0.60"))

_cache: dict[str, Any] = {"ts": 0.0, "mode": None}


def load_house_mode(*, force: bool = False) -> dict[str, Any]:
    """Cheap per-turn read with 1s cache so the User can flip the file without restart."""
    now = time.time()
    if not force and _cache.get("mode") is not None and (now - float(_cache["ts"])) < 1.0:
        return dict(_cache["mode"] or {})
    raw: dict[str, Any] = {}
    try:
        if HOUSE_MODE_PATH.is_file():
            raw = json.loads(HOUSE_MODE_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
    except Exception:
        raw = {}
    _cache["ts"] = now
    _cache["mode"] = raw
    return dict(raw)


_STORE_USER: dict[str, Any] = {}


def identity_user_id(ttl: float = 5.0) -> str:
    """The person Init's Voice ID made the User: person.json role "user" in the identity store (CRISSA_IDENTITY, else
    HOUSE_ID_ROOT, else <home>/identity - the store the coach writes and the ear matches against), else "". Read
    again at most every `ttl` seconds, so a User enrolled while the bridge runs is the User on the next turn
    (2026-10-10: the bridge's User was always "user" and Init names the person by their first name, so a matched
    print could never be the User's)."""
    now = time.monotonic()
    if _STORE_USER.get("at") is not None and now - _STORE_USER["at"] < ttl:
        return str(_STORE_USER.get("id") or "")
    uid = ""
    root = Path(os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or str(HOME / "identity"))
    try:
        for pj in sorted((root / "people").glob("*/person.json")):
            rec = json.loads(pj.read_text(encoding="utf-8")) or {}
            if rec.get("active") is False:
                continue
            if str(rec.get("role") or "").strip().lower() == "user":
                uid = str(rec.get("id") or pj.parent.name).strip().lower()
                break
    except Exception:  # noqa: BLE001 - an unreadable store names nobody
        uid = ""
    _STORE_USER.update(at=now, id=uid)
    return uid


_PRINTS: dict[str, Any] = {"at": None, "uid": "", "n": None}


def user_voice_prints(uid: str, ttl: float = 10.0) -> int | None:
    """How many voice prints (records with numbers) the identity store holds for the User; None when the store names
    no such person (no Init user, or a house that keeps its store elsewhere). Read again every `ttl` seconds, so a print
    made by Voice ID again counts on the next turn (the User 2026-10-10, .101 Z)."""
    now = time.monotonic()
    u = str(uid or "").strip().lower()
    if _PRINTS["at"] is not None and now - _PRINTS["at"] < ttl and _PRINTS["uid"] == u:
        return _PRINTS["n"]
    root = Path(os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or str(HOME / "identity"))
    person = root / "people" / u
    n: int | None = None
    if u and (person / "person.json").is_file():
        n = 0
        for f in sorted((person / "voice").glob("*.json")):
            try:
                enc = (json.loads(f.read_text(encoding="utf-8")) or {}).get("encoding")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(enc, list) and len(enc) >= 8:
                n += 1
    _PRINTS.update(at=now, uid=u, n=n)
    return n


def user_id_from_mode(mode: dict[str, Any] | None = None) -> str:
    """house-mode.json's auth.user_id, else its user_id, else HOUSE_USER_ID, else the identity store's User (Init's
    Voice ID), else the generic "user"."""
    m = mode if mode is not None else load_house_mode()
    auth = m.get("auth") if isinstance(m.get("auth"), dict) else {}
    uid = (
        auth.get("user_id")
        or m.get("user_id")
        or os.environ.get("HOUSE_USER_ID")
        or identity_user_id()
        or "user"
    )
    return str(uid).strip().lower() or "user"


# The mode when the file names none. The house ran user_auth_only (only the owner's voice gets BAU; everyone
# else is a polite guest); a pack starts there too, so a fresh install is the safe one. HOUSE_MODE_DEFAULT=house
# is the open house.
MODE_DEFAULT = os.environ.get("HOUSE_MODE_DEFAULT", "user_auth_only").strip().lower() or "user_auth_only"


def is_user_auth_only(mode: dict[str, Any] | None = None) -> bool:
    m = mode if mode is not None else load_house_mode()
    return str(m.get("mode") or MODE_DEFAULT).strip().lower() in (
        "user_auth_only",
        "user-only",
        "user_only",
        "locktight",
    )


def guest_policy(mode: dict[str, Any] | None = None) -> str:
    """polite | silent — the User asked for weather/chat; Fable default was silent."""
    m = mode if mode is not None else load_house_mode()
    g = str(m.get("guest") or os.environ.get("HOUSE_GUEST_POLICY") or "polite").lower()
    if g in ("silent", "silence", "mute", "none"):
        return "silent"
    # speak_to_others false used to mean silence; guest:polite overrides for the User
    if m.get("guest") is None and isinstance(m.get("policy"), dict):
        if m["policy"].get("speak_to_others") is False and os.environ.get(
            "HOUSE_GUEST_POLICY"
        ) is None:
            # the User 2026-08-03 free-air: still allow polite unless explicitly silent
            return "polite"
    return "polite"


# Live the face check visor: sim floor matches presence MATCH_SIM (0.60).
# Visor ticks 1.2 s; server scan cooldown 0.8 s — 2.5 s is one missed tick.
LIVE_FACE_MIN_SIM = float(os.environ.get("HOUSE_LIVE_FACE_MIN_SIM", "0.60"))
LIVE_FACE_AGE_S = float(os.environ.get("HOUSE_LIVE_FACE_AGE_S", "2.5"))
# A FIXED CAMERA'S FACE (2026-10-11): presence publishes the oracle's look there (face block `fixed`: true), one look a
# second while a face is in view, built into the snapshot at presence's own tick (1.2-2 s); the visor's 2.5 s would
# drop a seated User between two ticks. The camera's one view is the seat, so a face seen there this recently is live.
FIXED_FACE_AGE_S = float(os.environ.get("HOUSE_FIXED_FACE_AGE_S", "6"))
# Side/profile vs enrolled profile prints. Front lock stays LIVE_FACE_MIN_SIM.
LIVE_PROFILE_MIN_SIM = float(os.environ.get("HOUSE_LIVE_PROFILE_MIN_SIM", "0.40"))
# Empty FOV after a look ends: keep A this long, then Guest.
# During a look at the station, AUTH persists (profile / back of head).
AUTH_OFF_CAMERA_S = float(os.environ.get("HOUSE_AUTH_OFF_CAMERA_S", "5"))


def _face_sim_of(user: dict, presence: dict) -> float | None:
    try:
        if user.get("user_face_sim") is not None:
            return float(user["user_face_sim"])
        if user.get("face_sim") is not None:
            return float(user["face_sim"])
        if presence.get("user_face_sim") is not None:
            return float(presence["user_face_sim"])
    except (TypeError, ValueError):
        return None
    return None


def _live_face_user(
    presence: dict, uid: str
) -> tuple[bool, float | None, bool]:
    """Live the face check visor face. Returns (user_in_fov, sim, fresh).

    Fresh = visor/scan age within LIVE_FACE_AGE_S, or the camera is in a
    session look (f2f). Stale HUD is not a live miss.
    """
    uid = (uid or "").strip().lower()
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    if not uid or not buf:
        return False, None, False
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    rec = None
    for f in faces:
        if not isinstance(f, dict):
            continue
        if str(f.get("name") or "").strip().lower() == uid:
            rec = f
            break
    locked = str(buf.get("locked") or "").strip().lower()
    sim = None
    if rec is not None and rec.get("sim") is not None:
        try:
            sim = float(rec["sim"])
        except (TypeError, ValueError):
            sim = None
    looking = bool(presence.get("f2f") or buf.get("f2f"))
    pending = bool(buf.get("pending_look") or presence.get("pending_look"))
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    src = str(buf.get("source") or "").strip().lower()
    has_scan = bool(
        rec
        or locked
        or faces
        or buf.get("n_faces")
        or buf.get("ts")
        or (src and src not in ("idle", "none"))
    )
    # Session look in flight: not a live miss (door frame) and not a sticky.
    # Do not require f2f already true — the gimbal is still on the door when
    # the wake turn is classified (the User 2026-08-28: answer on wake+ID, do
    # not wait on camera). A holds so User voice (B) → BAU.
    # A FIXED CAMERA'S LOOK IS NEVER IN FLIGHT (round 13, .101 2026-10-12): there is no gimbal to aim, so a session
    # look's scan never completes and `pending_look` stayed true for the whole conversation - every turn read
    # "a look is in flight", fresh False, and the face never decided (3 turns held await_voice at face 0.68-0.80).
    # A block the oracle's fixed look wrote (`fixed`) is judged by its age, like any live face. Kill:
    # CRISSA_FIXED_LOOK_NOT_PENDING=0.
    if pending and not (buf.get("fixed") and FIXED_LOOK_NOT_PENDING):
        return True, sim, False
    if not has_scan:
        return False, None, False
    # Fresh is age only. f2f-as-fresh is occupancy sticky renamed.
    fresh = bool(age is not None and age <= (FIXED_FACE_AGE_S if buf.get("fixed") else LIVE_FACE_AGE_S))
    hit = rec is not None or locked == uid
    floor = LIVE_FACE_MIN_SIM
    if looking:
        floor = min(floor, LIVE_PROFILE_MIN_SIM)
    if hit and (sim is None or sim >= floor) and fresh:
        return True, sim, True
    return False, sim, fresh


def _face_empty_fov(buf: dict) -> bool:
    try:
        n = int(buf.get("n_faces") or 0)
    except (TypeError, ValueError):
        n = 0
    locked = str(buf.get("locked") or "").strip().lower()
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    named = [
        f
        for f in faces
        if isinstance(f, dict) and str(f.get("name") or "").strip()
    ]
    return n <= 0 and not locked and not named


def _other_face_in_fov(buf: dict, uid: str) -> bool:
    uid = (uid or "").strip().lower()
    locked = str(buf.get("locked") or "").strip().lower()
    if locked and locked != uid:
        return True
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    named_other = False
    named_user = locked == uid
    for rec in faces:
        if not isinstance(rec, dict):
            continue
        nm = str(rec.get("name") or "").strip().lower()
        if not nm:
            continue
        if nm == uid:
            named_user = True
        else:
            named_other = True
    return named_other


def _off_camera_auth_hold(buf: dict, uid: str) -> tuple[bool, float | None]:
    """True when empty FOV is still inside the 5s User-lock hold."""
    if not isinstance(buf, dict) or not buf:
        return False, None
    if _other_face_in_fov(buf, uid):
        return False, None
    sim = None
    try:
        if buf.get("last_user_lock_sim") is not None:
            sim = float(buf["last_user_lock_sim"])
    except (TypeError, ValueError):
        sim = None
    if buf.get("auth_hold") is True:
        return True, sim
    if buf.get("auth_hold") is False:
        return False, sim
    looking = bool(buf.get("f2f"))
    try:
        age = (
            float(buf["last_user_lock_age_s"])
            if buf.get("last_user_lock_age_s") is not None
            else None
        )
    except (TypeError, ValueError):
        age = None
    if looking and age is not None and 0 <= age <= AUTH_OFF_CAMERA_S:
        return True, sim
    try:
        rem = float(buf["auth_hold_s"]) if buf.get("auth_hold_s") is not None else None
    except (TypeError, ValueError):
        rem = None
    if looking and rem is not None and rem > 0:
        return True, sim
    return False, sim


def _face_authed_user(presence: dict, uid: str) -> tuple[bool, float | None]:
    """User face AUTH = live lock, profile at station, or hold after the look.

    Occupancy sticky / people[] roster grants are not A. Unlock is not A.
    Other named face in FOV clears immediately. Back of head / unlabeled at
    the look station is not a miss. Master flag is this face fused with
    this-turn voice in evaluate_turn.
    """
    uid = (uid or "").strip().lower()
    user = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    live, live_sim, _fresh = _live_face_user(presence, uid)
    if live:
        return True, live_sim if live_sim is not None else _face_sim_of(user, presence)
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    if presence.get("f2f") and isinstance(buf, dict) and "f2f" not in buf:
        buf = dict(buf)
        buf["f2f"] = True
    hold, hold_sim = _off_camera_auth_hold(buf, uid)
    if hold:
        if live_sim is not None:
            return True, live_sim
        if hold_sim is not None:
            return True, hold_sim
        return True, _face_sim_of(user, presence)
    # Secure pauses AUTH's time expiry and FF tracking. Sage without
    # user_auth is not a grant. Occupancy user_auth without Secure is
    # not conversation A.
    secure = bool(user.get("secure")) or str(
        presence.get("visit_lamp") or ""
    ).strip().lower() == "sage"
    if secure and bool(presence.get("user_auth") or user.get("auth")):
        return True, live_sim if live_sim is not None else _face_sim_of(
            user, presence
        )
    return False, live_sim


def _voice_sim(stamp: dict) -> float | None:
    try:
        sim = stamp.get("voice_emb_sim")
        if sim is None:
            sim = stamp.get("self_sim")
        return float(sim) if sim is not None else None
    except (TypeError, ValueError):
        return None


def _not_user_voice_evidence(
    stamp: dict, uid: str
) -> tuple[bool, float | None, str]:
    """True when stamp evidence says this utterance is *not* the sudo User.

    Does not require naming who spoke — only "not the user". STT id_matrix already
    emits is_user_block / is_user_uncertain_sole for this (the User 2026-08-12 guest
    board work while session_held).
    """
    stamp = stamp if isinstance(stamp, dict) else {}
    uid = (uid or "user").strip().lower()
    src = str(stamp.get("source") or "").lower()
    sim_f = _voice_sim(stamp)

    if stamp.get("is_user") is False:
        return True, sim_f, "is_user_false"
    if src in ("is_user_block",) or src.startswith("is_user_block"):
        return True, sim_f, "is_user_block"
    if src in ("is_user_uncertain_sole",) or "is_user_uncertain" in src:
        return True, sim_f, "is_user_uncertain_sole"

    open_id = str(stamp.get("open_top_id") or stamp.get("top_id") or "").strip().lower()
    try:
        open_sim = (
            float(stamp["open_top_sim"])
            if stamp.get("open_top_sim") is not None
            else (
                float(stamp["top_sim"]) if stamp.get("top_sim") is not None else None
            )
        )
    except (TypeError, ValueError):
        open_sim = None
    if (
        open_id
        and open_id != uid
        and open_sim is not None
        and open_sim >= 0.45
        and (sim_f is None or open_sim >= float(sim_f) + 0.08)
    ):
        return True, sim_f, "open_not_user"

    # Emb vs User bank well below floor *and* no person_id=User claim:
    # not me. If person_id already is User with weak sim, that is weak-User
    # (session_held may still BAU chat) — not a guest rider.
    pid = str(stamp.get("person_id") or "").strip().lower()
    if (
        sim_f is not None
        and sim_f < NOT_USER_VOICE_SIM
        and src
        and pid != uid
    ):
        if any(k in src for k in ("voice", "is_user", "emb", "sole", "block")):
            return True, sim_f, "low_user_sim"
    return False, sim_f, ""


def is_occupancy_slot(pid: str | None) -> bool:
    """s1/s2 and U1… are inventory slots, not named other-speakers."""
    p = (pid or "").strip().lower()
    if len(p) >= 2 and p[0] in ("u", "s") and p[1:].isdigit():
        return True
    return False


def resolve_stamp_pid(
    pid: str | None, presence: dict | None, uid: str
) -> str:
    """Map occupancy slots through SoT names (s1 sitting as the user → the user)."""
    p = (pid or "").strip().lower()
    uid = (uid or "user").strip().lower()
    if not p or p == uid:
        return p
    if not is_occupancy_slot(p):
        return p
    presence = presence if isinstance(presence, dict) else {}
    for person in presence.get("people") or []:
        if not isinstance(person, dict):
            continue
        slot = str(
            person.get("slot_id") or person.get("id") or ""
        ).strip().lower()
        name = str(person.get("name") or "").strip().lower()
        if slot == p and name == uid:
            return uid
        if p == name:
            return uid
    return p


def is_house_task(text: str) -> bool:
    """True when the utterance asks Crissa to *act* (board/wall/files), not chat.

    Face check runs on task + wrong_voice — not on small talk (not before).
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    needles = (
        "board",
        "the board",
        "on the board",
        "completed task",
        "put it on the screen",
        "put it on the wall",
        "on the wall",
        "on the screen",
        "team_display",
        "write a document",
        "write a file",
        "summarize all the completed",
        "summarize the completed",
        "show me the board",
        "display the board",
        "house command",
        "system-off",
        "system off",
        "reboot",
    )
    if any(n in t for n in needles):
        return True
    if "summarize" in t and ("task" in t or "board" in t):
        return True
    if ("put" in t or "show" in t or "display" in t) and (
        "screen" in t or "wall" in t or "document" in t
    ):
        return True
    return False


def _voice_is_user(stamp: dict, pid: str | None, uid: str) -> tuple[bool, float | None]:
    """Current utterance is User voice (stamp person_id + emb strength)."""
    stamp = stamp if isinstance(stamp, dict) else {}
    pid = (pid or stamp.get("person_id") or "").strip().lower()
    # Explicit not-User evidence wins even if person_id lagged as the user
    not_me, sim_nm, _why = _not_user_voice_evidence(stamp, uid)
    if not_me:
        return False, sim_nm
    if pid != uid:
        return False, None
    sim_f = _voice_sim(stamp)
    src = str(stamp.get("source") or "").lower()
    # Strong paths: voice emb match; sole_present only if sim ok or certain
    if src in (
        "voice_emb",
        "voice_emb_user_auth",
        "voice_emb_vs_sole",
        "voice_emb_weak_open",
    ):
        if sim_f is not None and sim_f < USER_VOICE_MIN_SIM:
            return False, sim_f
        if src == "voice_emb_weak_open":
            return False, sim_f
        return True, sim_f
    if src == "sole_present" and stamp.get("certain"):
        # face-sole without voice emb is not enough for BAU in user_auth_only
        if sim_f is not None and sim_f >= USER_VOICE_MIN_SIM:
            return True, sim_f
        return False, sim_f
    # person_id=the user from emb with sim
    if sim_f is not None and sim_f >= USER_VOICE_MIN_SIM and pid == uid:
        return True, sim_f
    return False, sim_f


def face_why(presence: dict | None, uid: str, *, oracle_face: bool | None = None) -> dict[str, Any]:
    """{"face_why", "face_age_s", "face_source", "face_fixed"}: why the live face did or did not decide this turn, in
    the order _live_face_user and the Oracle rule test it (see evaluate_turn's dispatch line)."""
    presence = presence if isinstance(presence, dict) else {}
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    fixed = bool(buf.get("fixed"))
    out: dict[str, Any] = {"face_age_s": None if age is None else round(age, 2),
                           "face_source": str(buf.get("source") or "") or None, "face_fixed": fixed}
    u = (uid or "").strip().lower()
    limit = FIXED_FACE_AGE_S if fixed else LIVE_FACE_AGE_S
    names = {str((f or {}).get("name") or "").strip().lower(): (f or {}).get("sim")
             for f in (buf.get("faces") or []) if isinstance(f, dict)}
    sim = names.get(u) if u else None
    if oracle_face:
        why = "decided: face %s, %.1f s old (%s)" % (sim, age or 0.0, out["face_source"] or "?")
    elif not ORACLE_FACE_WINS:
        why = "off: HOUSE_ORACLE_FACE_WINS is not 1"
    elif not buf:
        why = "no face block from presence"
    elif bool(buf.get("pending_look") or presence.get("pending_look")) and not (fixed and FIXED_LOOK_NOT_PENDING):
        why = "a look is in flight (not a live face yet)"
    elif u not in names and str(buf.get("locked") or "").strip().lower() != u:
        why = "no face named %s in the block (%d face%s, %s)" % (
            u or "?", len(names), "" if len(names) == 1 else "s", out["face_source"] or "?")
    elif age is None:
        why = "no age on the face block (%s)" % (out["face_source"] or "?")
    elif age > limit:
        why = "stale: %.1f s old, the limit is %.1f s (%s, %s camera)" % (
            age, limit, out["face_source"] or "?", "fixed" if fixed else "moving or unknown")
    elif sim is None:
        why = "the face block names %s with no similarity" % u
    else:
        try:
            low = float(sim) < ORACLE_FACE_MIN
        except (TypeError, ValueError):
            low = True
        why = ("sim %.3f under %.2f" % (float(sim), ORACLE_FACE_MIN)) if low else "not decided (%s)" % sim
    out["face_why"] = why
    return out


def evaluate_turn(
    presence: dict | None,
    stamp: dict | None,
    *,
    person_id: str | None = None,
    look_pending: bool = False,
) -> dict[str, Any]:
    """Free-air level from AUTH-VOICE law (docs/AUTH-VOICE-LAW-20260812.md).

    Two questions — never mix:
      A) Live the face check User lock, session look pending, or empty FOV ≤5s
         after that lock. Occupancy sticky is not A. Other face clears A.
         Unlock is a Face-grab trigger, not A.
      B) This utterance User voice? If no → guest tools-off; AUTH (A) stays.
    Master flag = A fused with B, reevaluated every turn.

    If A and B → BAU + tools. If A and not B → guest/chat tools-off. If not A
    → guest/silent. Tools and house commands never ride session_held alone.
    Host lock does not get a second guest dispatcher.
    """
    from .host_session import host_unlocked
    from .id_gate import owner_verdict          # round 17: Security ID-only / Off decide no turn by identity

    opened = owner_verdict(presence, stamp, person_id=person_id)
    if opened is not None:
        return opened
    presence = presence if isinstance(presence, dict) else {}
    stamp = stamp if isinstance(stamp, dict) else {}
    mode = load_house_mode()
    uid = user_id_from_mode(mode)
    lights_out = bool(
        presence.get("lights_out")
        or (presence.get("lights") or {}).get("lights_out")
    )

    # A — face sticky / operator only. Unlock is not A (Phase I).
    face_ok, face_sim = _face_authed_user(presence, uid)
    if not look_pending:
        look_pending = bool(
            presence.get("pending_look")
            or (
                isinstance(presence.get("face"), dict)
                and presence["face"].get("pending_look")
            )
        )
    # Vocative look is in flight on the ear even before presence.f2f flips.
    if look_pending and not face_ok:
        face_ok = True
    unlocked = bool(host_unlocked(presence))
    present_a = bool(face_ok)

    stamp_pid_raw = (person_id or stamp.get("person_id") or "")
    stamp_pid = resolve_stamp_pid(stamp_pid_raw, presence, uid)

    # B — is this turn my voice?
    voice_ok, voice_sim = _voice_is_user(stamp, stamp_pid or None, uid)

    # The Oracle (OFF by default): a fresh live face match decides; voice only when the camera can't.
    live_now, live_now_sim, live_fresh = _live_face_user(presence, uid)
    oracle_face = bool(
        ORACLE_FACE_WINS
        and live_now and live_fresh
        and live_now_sim is not None and float(live_now_sim) >= ORACLE_FACE_MIN
    )
    face_diag = face_why(presence, uid, oracle_face=oracle_face)

    ublk = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    # Explicit session only — do NOT promote face sticky alone to session_held
    # (that made weak non-session turns look like full BAU).
    session_held = bool(ublk.get("session_held"))
    strong_age = ublk.get("strong_voice_age_s")
    try:
        strong_age = float(strong_age) if strong_age is not None else None
    except (TypeError, ValueError):
        strong_age = None

    force_reprove = False
    other_speaker = bool(
        stamp_pid
        and stamp_pid != uid
        and not is_occupancy_slot(stamp_pid)
        and stamp_pid not in ("", "unknown", "none")
    )
    not_me, not_me_sim, not_me_why = _not_user_voice_evidence(stamp, uid)

    if lights_out:
        reason = "lights_out"
        level = "silent"
        auth = False
        session_held = False
        present_a = False
    elif not is_user_auth_only(mode):
        reason = "house_mode"
        auth = present_a and voice_ok
        level = "legacy"
    elif not present_a:
        # A = no. Lock guest is still this branch (one guest wire). Reason
        # host_locked tells the camera not to hunt until unlock.
        if (
            ublk.get("auth_reason") == "host_locked"
            or presence.get("user_auth_reason") == "host_locked"
        ):
            reason = "host_locked"
        else:
            reason = "user_absent"
        auth = False
        level = "silent" if guest_policy(mode) == "silent" else "guest"
    elif voice_ok:
        # A yes + B yes — full BAU + tools. Sticky/unlock is enough for A.
        reason = "ok"
        auth = True
        level = "bau"
    elif oracle_face:
        # The camera sees and matches him now: the voice score does not decide (the User 2026-10-01).
        reason = "oracle_face"
        auth = True
        level = "bau"
    elif other_speaker or not_me:
        # A yes + B no — still here; this utterance is not me.
        # Guest tools-off. Do NOT clear sticky AUTH on presence.
        reason = (
            "wrong_voice"
            if other_speaker
            else f"not_user_voice:{not_me_why or 'emb'}"
        )
        auth = True  # SoT: still AUTHed present; ear level is guest
        level = "silent" if guest_policy(mode) == "silent" else "guest"
        force_reprove = True  # house commands must re-prove speaker
        if not_me_sim is not None and voice_sim is None:
            voice_sim = not_me_sim
    elif session_held:
        # A yes + held session + no usable voice this turn — chat OK; tools need B
        reason = "session_held"
        auth = True
        level = "bau"
    else:
        # A yes (face/unlock) but no session_held and no usable User voice:
        # await a real voice turn — silent (not tools, not free-air BAU).
        reason = "await_voice"
        auth = True  # A still holds; ear stays quiet until B
        level = "silent"

    # Tools / injection: only A + this-turn User voice (never session_held alone) - or the Oracle's live face.
    tools_ok = bool(auth and (voice_ok or oracle_face) and not force_reprove)

    # Risk tiers for the dispatcher (ad-hoc re-auth, not a timer):
    #   tier1_ok — house commands: this-turn User voice OR a strong match
    #              recently in the held session (never while contested).
    #   tier2_ok — security and identity actions: this-turn User voice, full stop.
    tier1_ok = bool(
        auth
        and not force_reprove
        and (
            voice_ok
            or oracle_face
            or (
                session_held
                and strong_age is not None
                and strong_age < TIER1_MAX_AGE_S
            )
        )
    )
    tier2_ok = bool(auth and voice_ok and not force_reprove)

    # auth_tier watch string from presence if published; else derive lightly
    auth_tier = (
        ublk.get("auth_tier")
        or presence.get("user_auth_tier")
        or ("contested" if force_reprove else None)
    )
    auth_age_s = ublk.get("auth_age_s")
    if auth_age_s is None:
        auth_age_s = presence.get("user_auth_age_s")

    return {
        "mode": str(mode.get("mode") or MODE_DEFAULT),
        "user_id": uid,
        "user_present": present_a,
        "user_face_ok": face_ok,
        "host_unlocked": unlocked,
        "user_face_sim": face_sim,
        "user_voice": voice_ok,
        "user_voice_sim": voice_sim,
        "auth": auth,
        "auth_reason": reason,
        "level": level,
        "guest_policy": guest_policy(mode),
        "bau": level == "bau",
        "tools_ok": tools_ok,
        "lights_out": lights_out,
        "session_held": session_held and not force_reprove,
        "strong_voice_age_s": strong_age,
        "tier1_ok": tier1_ok,
        "tier2_ok": tier2_ok,
        "force_reprove": force_reprove,
        "auth_tier": auth_tier,
        "auth_age_s": auth_age_s,
        "oracle_face": oracle_face,
        # WHY THE FACE DID OR DID NOT DECIDE (the User 2026-10-12, .101 round 11: oracle_face false on all 8 turns with
        # his face at 0.62-0.86 and nothing said why): one reason, the face block's age, source and kind, and the
        # stamp's own source and top similarity, on every dispatch line. Diagnostic only: nothing reads them.
        **face_diag,
        "stamp_source": stamp.get("source"),
        "stamp_certain": stamp.get("certain"),
        "top_sim": stamp.get("top_sim"),
    }


# THE HOUSE'S "THIS TURN IS THE USER'S" (round 15 auth, the User 2026-10-12: "smalltalk whatnot is fine but need Auth
# just like in main ... to give real info"): the levels evaluate_turn gives - rail-voice user_mode.evaluate_turn, the
# AUTH-VOICE law, ported unchanged here (A = the User's live face lock, a look in flight or his hold; B = this turn's
# voice) - name one owner: auth held and a level that answers him (bau: A and B, his face deciding, or a held session's
# chat; legacy with auth). Anything else is a guest's small talk (GUEST_SYSTEM) or silence. Every path that gives his
# words or her answers to the model as HIS asks this one question (the over-line ask included). Kill: none - it is the
# levels themselves.
AUTHED_LEVELS = ("bau", "allow", "legacy", "soft")


def authed_owner(ev: dict[str, Any] | None) -> bool:
    ev = ev if isinstance(ev, dict) else {}
    return bool(ev.get("auth")) and str(ev.get("level") or "") in AUTHED_LEVELS


GUEST_SYSTEM = (
    "You are a polite house assistant in guest mode. "
    "Verbal banter only — weather, time, small talk, general questions. "
    "The wall stays on the homescreen. Do not offer to show, put up, pull up, "
    "or display the board, a document, or anything on the screen. "
    "If they ask to see the board, answer in speech if you can, and do not offer "
    "to put it up. Never mention tools. "
    "Do not use or invent anyone's name. Do not mention identity files, enrollments, "
    "or who is in the room. Do not run house controls or system commands. "
    "Do not talk about the house being up or down — it is not a mode you can grant. "
    "Answer in 1-2 short spoken sentences. No markdown."
)
