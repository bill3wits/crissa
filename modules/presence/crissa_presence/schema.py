"""room_presence/v1 — pure data contract.

STONE: camera_mode + count_trust so consumers never treat a stale count as fact.
count_trust is CAMERA CONTINUITY only (the User 2026-08-11): fact while the always-on
door feed has no outage gap; stale only after away/outage / sequential-frame gap.
Empty door FOV and voice mismatch do NOT demote count_trust.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import time

SCHEMA_ID = "room_presence/v1"

CameraMode = Literal["holding", "away", "reconciling"]
CountTrust = Literal["fact", "stale", "reconciling", "unknown"]

# Epoch float keys we promote to human-readable ISO (local + UTC) on export.
_TS_KEYS = frozenset(
    {
        "ts",
        "capture_ts",
        "last_seen",
        "last_seen_ts",
        "seen_at",
        "enter_ts",
        "exit_ts",
        "started_at",
        "last_sweep_ts",
        "t0",
        "t1",
    }
)


def _normalize_epoch(ts: float | int | None) -> float | None:
    if ts is None:
        return None
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if t > 1e12:  # ms epoch
        t = t / 1000.0
    if t <= 0:
        return None
    return t


def epoch_to_iso(ts: float | int | None) -> str | None:
    """Unix epoch → local wall time ``YYYY-MM-DD HH:MM:SS`` (house local)."""
    t = _normalize_epoch(ts)
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def epoch_to_iso_utc(ts: float | int | None) -> str | None:
    """Unix epoch → UTC ``YYYY-MM-DDTHH:MM:SSZ``."""
    t = _normalize_epoch(ts)
    if t is None:
        return None
    try:
        return (
            datetime.fromtimestamp(t, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except (OverflowError, OSError, ValueError):
        return None


def epoch_to_ago(ts: float | int | None, *, now: float | None = None) -> str | None:
    """Human relative age from epoch, e.g. ``just now``, ``12s ago``, ``3m ago``."""
    t = _normalize_epoch(ts)
    if t is None:
        return None
    age = (now if now is not None else time.time()) - t
    if age < 0:
        age = 0.0
    if age < 1.5:
        return "just now"
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        h = int(age // 3600)
        m = int((age % 3600) // 60)
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    d = int(age // 86400)
    return f"{d}d ago"


def format_last_seen(ts: float | int | None, *, now: float | None = None) -> dict[str, Any]:
    """Canonical last_seen fields for people[].sources and events."""
    t = _normalize_epoch(ts)
    if t is None:
        return {}
    return {
        "last_seen": t,
        "last_seen_local": epoch_to_iso(t),
        "last_seen_utc": epoch_to_iso_utc(t),
        "last_seen_ago": epoch_to_ago(t, now=now),
    }


def enrich_timestamps(obj: Any, *, depth: int = 0) -> Any:
    """Copy dict/list; for known epoch keys add `<key>_local` and `<key>_utc`."""
    if depth > 8:
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out[k] = enrich_timestamps(v, depth=depth + 1)
            if k in _TS_KEYS and isinstance(v, (int, float)):
                loc = epoch_to_iso(v)
                utc = epoch_to_iso_utc(v)
                if loc is not None:
                    out[f"{k}_local"] = loc
                if utc is not None:
                    out[f"{k}_utc"] = utc
        return out
    if isinstance(obj, list):
        return [enrich_timestamps(x, depth=depth + 1) for x in obj]
    return obj


# Auth levels on a slot — downstream rails (STT, voice, security) read only these.
# voice alone never grants face/operator.
AUTH_NONE = "none"
AUTH_SOFT = "soft"  # voice/suggest — not security auth
AUTH_FACE = "face"  # faceid door face proven
AUTH_OPERATOR = "operator"  # the User / assert force

# Rank for merge (higher wins)
_AUTH_RANK = {
    AUTH_NONE: 0,
    AUTH_SOFT: 1,
    AUTH_FACE: 2,
    AUTH_OPERATOR: 3,
}


def auth_rank(level: str | None) -> int:
    return _AUTH_RANK.get(str(level or AUTH_NONE).lower(), 0)


@dataclass
class PersonSlot:
    slot_id: str
    name: str | None = None
    certain: bool = False
    # Soft ID (the User 2026-07-27): provisional → log "name?"; confirmed drops ?
    id_status: str = "unknown"  # provisional | confirmed | unknown
    display: str | None = None  # "another person?" or "another person"
    gender: str | None = None  # "M" | "F" | None
    seat: int | None = None  # clock hour 1-12 from station_guess; null until written
    sources: dict[str, Any] = field(default_factory=dict)
    # Auth for downstream systems — single place, never a side ledger
    auth: dict[str, Any] = field(default_factory=dict)
    # Live clothing/height + enter stills (CONTRACT-CLOTHING-LEAVE)
    traits_live: dict[str, Any] | None = None
    enter_still: dict[str, Any] | None = None
    # Oracle visit row (ARGUS-ORACLE-LOOP) — same object, not a second ledger
    short_id: str | None = None
    enter_ts: float | None = None
    exit_ts: float | None = None
    event: str | None = None  # enter | exit | sighting
    visit: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # derive display if caller left it empty
        if not d.get("display") and d.get("name"):
            if d.get("certain") and d.get("id_status") != "provisional":
                d["display"] = d["name"]
            elif d.get("name") and not str(d["name"]).startswith(
                ("U", "F", "M", "crowd", "unknown")
            ):
                d["display"] = d["name"] if d.get("certain") else f"{d['name']}?"
            else:
                d["display"] = d["name"]
        if not d.get("auth"):
            d["auth"] = {
                "level": AUTH_NONE,
                "authed": False,
                "sources": {},
            }
        # omit empty optional leave-ID fields for quieter JSON
        if not d.get("traits_live"):
            d.pop("traits_live", None)
        if not d.get("enter_still"):
            d.pop("enter_still", None)
        if not d.get("visit"):
            d.pop("visit", None)
        if not d.get("short_id"):
            d.pop("short_id", None)
        if d.get("event") is None:
            d.pop("event", None)
        if d.get("enter_ts") is None:
            d.pop("enter_ts", None)
        if d.get("exit_ts") is None:
            d.pop("exit_ts", None)
        return enrich_timestamps(d)


@dataclass
class RoomPresence:
    camera_open: bool = False
    camera_blind: bool = True
    frame_age_sec: float = 999.0
    headcount: int = 0
    count_certain: bool = False
    people: list[PersonSlot] = field(default_factory=list)
    unidentified: int = 0
    last_event: dict[str, Any] = field(default_factory=dict)
    # Peak occupancy on the SoT object (code ratchets from apply + door FOV)
    peaks: dict[str, Any] = field(default_factory=dict)
    # Auth summary for STT / voice / security consumers (read this, not invent)
    auth: dict[str, Any] = field(default_factory=dict)
    # Metrics snapshot (tidy clicker state)
    metrics: dict[str, Any] = field(default_factory=dict)
    # Per-VL-writer mode + evidence stats (presence-perfect §3.7) — additive;
    # downstream consumers must tolerate this field (same class as doubt/state)
    writers: dict[str, Any] = field(default_factory=dict)
    # non-counting pet display (the User 2026-07-28): listed, NEVER counted —
    # same class as crowd_noise; headcount is humans only
    pets: dict[str, Any] = field(default_factory=dict)
    # STONE spine fields
    camera_mode: str = "holding"  # holding | away | reconciling | f2f
    # Face-to-face: EA is looking at the speaker. Marker only — not occupancy.
    f2f: bool = False
    f2f_since: float | None = None
    f2f_reason: str = ""
    # Last FaceID visor reading (live face ID). Not a roster / not count.
    faceid: dict = field(default_factory=dict)
    # CACrissa speaker stack: beam, camera, embeds, sex, stack (0..1).
    speaker: dict = field(default_factory=dict)
    count_trust: str = "stale"  # fact | stale | reconciling | unknown
    door_names: list[str] = field(default_factory=list)
    guards: dict[str, bool] = field(
        default_factory=lambda: {
            "screen_body_veto": False,
            "vlm_no_think": False,
            "door_first": True,
        }
    )
    # Lights-out (the User/Fable 2026-08-03): grabber luminance vs CLIP_DARK_LUM.
    # Top-level bool for fast gates; lights{} carries measure + refusal copy.
    lights_out: bool = False
    lights: dict[str, Any] = field(default_factory=dict)
    # User-only mode (Fable MODE-USER-AUTH-ONLY): one enrolled User (the user).
    # user_voice is filled when ears report a stamp; at rest often false/unknown.
    user_id: str = "user"
    user_present: bool = False
    user_face_sim: float | None = None
    # True when face was captured at door/entry and held while still on roster
    # (desk seat is outside door FOV — see MODE sticky-on-entry).
    user_face_sticky: bool = False
    # How the hold was MINTED (FaceFinder source, Law 1). None when Guest.
    user_face_auth_source: str | None = None
    user_voice: bool = False
    user_voice_sim: float | None = None
    user_auth: bool = False
    user_auth_reason: str = "user_absent"
    # Entry-anchored session (2026-08-04): held by presence continuity, no
    # timer; the ear's risk tiers consult the voice ages for ad-hoc re-auth.
    user_session_held: bool = False
    user_session_opened: float | None = None
    user_voice_age_s: float | None = None
    user_strong_voice_age_s: float | None = None
    # AUTH-first authority tier (COMMS 2026-08-12): fresh|held|stale|contested|absent
    # Publish always; enforcement is opt-in (RAIL_AUTH_TIER_ENFORCE). Does not drop sticky.
    user_auth_tier: str = "absent"
    user_auth_age_s: float | None = None
    # Host desktop unlocked is a Face-grab trigger, not AUTH (Phase I 2026-08-24).
    host_unlocked: bool = False
    # Door storm leftover. Linger/Busy are not the product (2026-08-26).
    # Speech look does not read this flag. Occupancy is not wiped.
    room_busy: bool = False
    # the camera token: who holds the one lease and why (camera_token.py)
    camera_token: dict | None = None
    # 1 s note when sage drops because door-watch can't run (joystick off door)
    # ({text, ttl_s, remaining_s} or None). Keyboard hold never sets this.
    camera_note: dict | None = None
    busy_reason: str = ""
    busy_since: float | None = None
    # Monotonic SoT write counter. Bumps only on material occupancy/AUTH/Busy
    # change — not mill republish. writers.last is the same object (the hook
    # that used to be {}). Glass :8099 has a different rev; do not mix them.
    rev: int = 0
    writer: dict[str, Any] | None = None
    # Product provenance (keep user_auth bool for back-compat; new code reads these).
    user_granted_by: str | None = None
    user_product_tier: str = "none"
    user_face_age_s: float | None = None
    # Mic-frame hearing (XVF3800 DOA). Not a roster, not last_event.
    # {az_deg, conf, ts, source, person_id?, speech?}
    sound: dict[str, Any] | None = None
    # Known guest faces seen today — not occupancy, not headcount.
    # [{person_id, ts, source, day}]
    visitor_today: list[dict[str, Any]] = field(default_factory=list)
    # Distinct people since local midnight (User counts as 1). Not occupancy.
    seen_today: dict[str, Any] = field(default_factory=dict)
    # Security X# strangers (photos on disk). Not occupancy.
    strangers: list[dict[str, Any]] = field(default_factory=list)
    # Door events opened on motion (event_id = trigger ts). Survives _drop().
    visits: list[dict[str, Any]] = field(default_factory=list)
    # Visit lamp. Derived every publish. Default amber = fail-safe (never sage).
    visit_lamp: str = "amber"
    visit_lamp_info: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    schema: str = SCHEMA_ID

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        people = []
        for p in self.people:
            people.append(p.to_dict() if hasattr(p, "to_dict") else p)
        d["people"] = people
        d["schema"] = SCHEMA_ID
        d = enrich_timestamps(d)
        # top-level always readable
        d["ts_local"] = epoch_to_iso(self.ts)
        d["ts_utc"] = epoch_to_iso_utc(self.ts)
        # Convenience aliases Fable/ops can poll without nested dig
        d["luminance"] = (self.lights or {}).get("luminance")
        # Panel alias: auth as bool (user_auth) without clobbering auth{} package
        d["user"] = {
            "user_id": self.user_id,
            "user_present": self.user_present,
            "user_face_sim": self.user_face_sim,
            "user_face_sticky": self.user_face_sticky,
            "face_auth_source": self.user_face_auth_source,
            "user_voice": self.user_voice,
            "user_voice_sim": self.user_voice_sim,
            "auth": self.user_auth,
            "secure": bool(
                (self.visit_lamp_info or {}).get("secure")
                if isinstance(self.visit_lamp_info, dict)
                else False
            )
            or str(self.visit_lamp or "").strip().lower() == "sage",
            "auth_reason": self.user_auth_reason,
            "auth_tier": self.user_auth_tier,
            "auth_age_s": self.user_auth_age_s,
            "session_held": self.user_session_held,
            "session_opened": self.user_session_opened,
            "voice_age_s": self.user_voice_age_s,
            "strong_voice_age_s": self.user_strong_voice_age_s,
            "host_unlocked": bool(self.host_unlocked),
            "session_unlocked": bool(self.host_unlocked),
            "auth_plane": "face_sticky",
            "granted_by": self.user_granted_by,
            "tier": self.user_product_tier,
            "face_age_s": self.user_face_age_s,
            "auth_provenance": {
                "granted_by": self.user_granted_by,
                "face_age_s": self.user_face_age_s,
                "voice_age_s": self.user_voice_age_s,
                "tier": self.user_product_tier,
            },
        }
        d["host_unlocked"] = bool(self.host_unlocked)
        d["room_busy"] = bool(self.room_busy)
        d["camera_token"] = dict(self.camera_token) if isinstance(self.camera_token, dict) else None
        d["busy_reason"] = self.busy_reason or ""
        d["busy_since"] = self.busy_since
        d["rev"] = int(self.rev or 0)
        d["writer"] = self.writer
        writers = dict(self.writers or {})
        if self.writer:
            writers["last"] = self.writer
        d["writers"] = writers
        d["f2f"] = bool(self.f2f)
        d["f2f_since"] = self.f2f_since
        d["f2f_reason"] = self.f2f_reason or ""
        d["user"]["f2f"] = bool(self.f2f)
        d["speaker"] = dict(self.speaker or {})
        d["user"]["speaker"] = dict(self.speaker or {})
        # THE EAR READS "face" (2026-10-11): the ear and the ear-bridge read the live face block as presence["face"]
        # (the house's own key, renamed one way in the ear's port) while this module publishes it as "faceid"; nothing mapped
        # one to the other, so "the face decides" and the stamp's live names never saw a face. Same block, both keys.
        if "faceid" in d and "face" not in d:
            d["face"] = dict(d.get("faceid") or {})
        if self.f2f:
            d["wall_count"] = "F2F"
        elif str(self.count_trust or "") == "unknown":
            d["wall_count"] = "unknown"
        if not d.get("sound"):
            d.pop("sound", None)
        if not d.get("visitor_today"):
            d.pop("visitor_today", None)
        if not d.get("strangers"):
            d.pop("strangers", None)
        if not d.get("visits"):
            d.pop("visits", None)
        return d


def empty_room(*, camera_open: bool = False) -> RoomPresence:
    return RoomPresence(
        camera_open=camera_open,
        camera_blind=not camera_open,
        frame_age_sec=0.0 if camera_open else 999.0,
        headcount=0,
        count_certain=True,
        people=[],
        unidentified=0,
        camera_mode="holding",
        count_trust="stale",
    )
