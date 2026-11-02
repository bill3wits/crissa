"""VoiceOnly live beam — glyph + placement around the center icon.

Shipped mapping used by :8099 /v1/sound (JSON + SSE). The 2s /v1/house poll
still carries headlines/vitals; the lobe must not wait on it.

Not a roster. Occupancy sitters never enter this module.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any

N_LAMPS = int(os.environ.get("VO_SOUND_LAMPS", "36"))
if N_LAMPS < 16:
    N_LAMPS = 16
if N_LAMPS > 72:
    N_LAMPS = 72

CX = 130.0
CY = 130.0
# Inner ring of the 260² dial — around the center icon, not hour marks (r≈110).
LETTER_R = 66.0
LETTER_BASELINE = 5.0
LAMP_R = 96.0

# The house's own voices on the beam (never a visitor): hers, the announcer's, the crew's, and this machine's
# named announcer voices (config/house-machine.json announcer_voices; a pack names none).
def _own_voices() -> frozenset:
    base = {"crissa", "crissa_her_tts", "announcer", "crew_tts"}
    try:
        import crissa_home
        exact = crissa_home.house_value("self_voices")      # the house's own set, as before the Crissa merge
        if exact:
            return frozenset(v.strip().lower() for v in exact.split(",") if v.strip())
        base |= {v.strip().lower() for v in (crissa_home.house_value("announcer_voices") or "").split(",") if v.strip()}
    except Exception:  # noqa: BLE001
        pass
    return frozenset(base)


SELF_VOICE = _own_voices()
_SCRATCH = re.compile(r"^s\d+$")

# --- two clocks (do not smash) --------------------------------------------
# heading.door_hour = dial DOOR tick (this office 10). 12 = USB = top of icon.
# stations.door_beam = camera-frame door sitter (this office 12).
# vo_sound reads heading for the tick ON PURPOSE. Never copy door_beam onto
# the tick — those fields will drift again if they share a number.
# Bearings are not remapped. Offset stays 0 unless the User agrees a calibration.
def _site_json_default() -> str:
    """The house's own site.json; in a pack (the User 2026-10-11) the one Init writes, under the state home's identity
    store (CRISSA_IDENTITY / HOUSE_ID_ROOT name another), so the dial reads what Your room saved."""
    try:
        import crissa_home
        if not crissa_home.is_house():
            root = (os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or "").strip()
            return os.path.join(os.path.expanduser(root) if root else str(crissa_home.HOME / "identity"), "site.json")
    except Exception:  # noqa: BLE001
        pass
    return os.path.expanduser("~/projects/house-id/site.json")


_SITE_JSON = os.environ.get("HOUSE_SITE_JSON") or _site_json_default()
_FRAME_TTL = 30.0
_frame_cache: dict[str, Any] = {"ts": 0.0, "val": None}


def room_frame() -> dict[str, Any]:
    """Dial + camera fields from house-id/site.json. Cached ~30 s.

    door_hour  — heading.door_hour (dial tick). Never stations.door_beam.
    door_beam  — stations.door_beam (camera sitter). Unused for the tick.
    Missing site.json is offset 0, not an error.
    """
    import time as _t

    now = _t.time()
    if _frame_cache["val"] is not None and (now - float(_frame_cache["ts"])) < _FRAME_TTL:
        return _frame_cache["val"]  # type: ignore[return-value]
    out = {
        "offset_deg": 0.0,
        "door_hour": None,
        "door_beam": None,
        "user_hour": 6,
        "user_beam": None,
        "frame": None,
    }
    try:
        with open(_SITE_JSON, encoding="utf-8") as fh:
            st = (json.load(fh) or {}).get("stations") or {}
        mp = st.get("mic_placement") or {}
        deg = mp.get("mic_zero_offset_deg")
        if deg is None and mp.get("mic_zero_offset_hours") is not None:
            deg = float(mp["mic_zero_offset_hours"]) * 30.0
        out["offset_deg"] = float(deg or 0.0)
        heading = st.get("heading") if isinstance(st.get("heading"), dict) else {}
        if heading.get("door_hour") is not None:
            out["door_hour"] = int(heading["door_hour"])
        if st.get("door_beam") is not None:
            out["door_beam"] = int(st["door_beam"])
        if heading.get("user_hour") is not None:
            out["user_hour"] = int(heading["user_hour"])
        if st.get("user_beam") is not None:
            out["user_beam"] = int(st["user_beam"])
        out["frame"] = st.get("frame")
    except (OSError, ValueError, TypeError, KeyError):
        pass
    _frame_cache["ts"] = now
    _frame_cache["val"] = out
    return out


_SEATS_CACHE: dict[str, Any] = {"ts": 0.0, "val": None}
_SEATS_TTL = 120.0
_PRESENCE = os.environ.get("HOUSE_PRESENCE_URL", "http://127.0.0.1:8191")


def user_mark(name: Any) -> str:
    """The two-letter desk mark for a person id: "the user" → "Bi". Data-driven
    from the registered id, never a spoken string (product voice: the User)."""
    n = str(name or "").strip()
    if not n:
        return "U"
    return n[0].upper() + n[1:2].lower()


def seats_fixed() -> dict[str, Any]:
    """The FIXED desks on the dial (the User 2026-09-07: "just the spots they
    register in as regulars. User gets 6, Crissa the dot. Those are fixed."):
    the User's seat hour from site.json heading.user_hour (room_frame) and
    the registered primary user id from presence's public geometry, cached
    ~2 min. Crissa is the centre. Presence down = last value or the defaults."""
    import time as _t
    import urllib.request

    now = _t.time()
    if _SEATS_CACHE["val"] is not None and (now - float(_SEATS_CACHE["ts"])) < _SEATS_TTL:
        return _SEATS_CACHE["val"]  # type: ignore[return-value]
    rf = room_frame()
    out: dict[str, Any] = {"user_seat": int(rf.get("user_hour") or 6), "user": "", "crissa": "center"}
    try:
        with urllib.request.urlopen(f"{_PRESENCE}/v1/seats/eligible", timeout=1.5) as r:
            g = json.loads(r.read().decode("utf-8", "replace")) or {}
        if g.get("primary_user"):
            out["user"] = str(g["primary_user"])
        if g.get("user_seat") is not None:
            out["user_seat"] = int(g["user_seat"])
        if g.get("media_station") is not None:
            out["media"] = int(g["media_station"])   # the MEDIA hour on the dial
        regs = g.get("regulars") if isinstance(g.get("regulars"), dict) else {}
        # the registered regulars by hour ({"6": "<the user's id>", "8": "<a regular's id>"}, init
        # station_roles): their marks replace the numerals on the dial
        out["regulars"] = {str(h): str(pid) for h, pid in regs.items() if pid}
        out["marks"] = {h: user_mark(pid) for h, pid in out["regulars"].items()}
    except Exception:  # noqa: BLE001 — presence down: keep the geometry defaults
        if _SEATS_CACHE["val"] is not None:
            out["user"] = _SEATS_CACHE["val"].get("user", "")
    if "media" not in out:
        mh = _pack_media_hour()
        if mh is not None:
            out["media"] = mh                         # a pack's Your room: the speakers' hour (2026-10-11)
    out["user_mark"] = user_mark(out["user"])
    _SEATS_CACHE["ts"] = now
    _SEATS_CACHE["val"] = out
    return out


def _pack_media_hour() -> int | None:
    """A pack's speakers hour, as Init's Your room saved it (site.json media_mode external + media_station); None on
    the house (its presence names the media station) or when the sound is this computer's."""
    try:
        import crissa_home
        if crissa_home.is_house():
            return None
        with open(_SITE_JSON, encoding="utf-8") as fh:
            site = json.load(fh) or {}
        if site.get("media_mode") != "external":
            return None
        h = int(site.get("media_station"))
        return h if 1 <= h <= 12 else None
    except Exception:  # noqa: BLE001
        return None


def beam_clock(az_deg: Any) -> float | None:
    """Mic-frame az → clock hours with tenths: 0° = 12.0, 180° = 6.0, 191° = 6.4.
    The rail-presence speaker_stack rule (BEAM-IS-FF-CLUE), published on the
    sound object so neither glass computes it (Connect Walls step 0,
    2026-09-07): the wallpaper's beam cell and Brave's Speaker row print
    THIS number at the beam's own rate."""
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


def az_norm(az_deg: Any) -> float | None:
    if az_deg is None:
        return None
    try:
        az = float(az_deg)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(az):
        return None
    return (az % 360.0 + 360.0) % 360.0


def initials_for(display: str, *, kind: str = "regular") -> str | None:
    """Two-letter code for a person on the glass (the User 2026-08-14).

    The CASE carries the meaning, so a stranger's mark can never be mistaken
    for someone who lives here:

      regular / user  →  ``Bi``   first letter capital, SECOND LETTER OF THE
                                  FIRST NAME lowercase
      visitor / guest →  ``BT``   first and last initial, BOTH CAPITAL

    So "any lowercase second letter means a regular; all caps means a visitor."
    A visitor with no surname falls back to one capital rather than inventing a
    letter — we never guess at a name we were not given.
    """
    name = " ".join(str(display or "").split())
    if not name:
        return None
    if _SCRATCH.fullmatch(name.lower()):
        return name.upper()  # scratch ids stay whole: S12, never S
    parts = [p for p in re.split(r"[\s\-_]+", name) if p]
    if not parts:
        return None
    first = parts[0]
    if str(kind).strip().lower() in ("visitor", "guest"):
        if len(parts) > 1:
            return (first[0] + parts[-1][0]).upper()
        return first[0].upper()
    if len(first) > 1:
        return first[0].upper() + first[1].lower()
    return first[0].upper()


def glyph_for_sound(sound: dict[str, Any] | None) -> str | None:
    """Mark around the icon, or None (glow only).

    The glyph is Oracle ``short_id`` on the hearing field — never a
    first-letter invented here. Scratch tokens (s12) are the id itself.
    missing / weak / self-voice / speech:false → no letter.
    """
    if not isinstance(sound, dict):
        return None
    if sound.get("speech") is False:
        return None
    lvl = str(sound.get("id_level") or "none").strip().lower()
    if lvl != "strong":
        return None
    raw = str(sound.get("person_id") or "").strip().lower()
    if not raw or raw in SELF_VOICE:
        return None
    sid = str(sound.get("short_id") or "").strip()
    if sid:
        return sid
    if _SCRATCH.fullmatch(raw):
        return raw.upper()
    return None


def letter_xy(
    az_deg: float,
    *,
    cx: float = CX,
    cy: float = CY,
    radius: float = LETTER_R,
    baseline: float = LETTER_BASELINE,
) -> tuple[float, float]:
    """Mic-frame az 0° = 12 o'clock on the dial (same as the JS lobe)."""
    a = math.radians(float(az_deg) - 90.0)
    return (cx + math.cos(a) * radius, cy + math.sin(a) * radius + baseline)


def lamp_deg(i: int, n: int = N_LAMPS) -> float:
    return (i * (360.0 / max(1, n))) % 360.0


def lamp_xy(
    i: int,
    n: int = N_LAMPS,
    *,
    cx: float = CX,
    cy: float = CY,
    radius: float = LAMP_R,
) -> tuple[float, float]:
    a = math.radians(lamp_deg(i, n) - 90.0)
    return (cx + math.cos(a) * radius, cy + math.sin(a) * radius)


def hour_mark_xy(hour: int, *, radius: float = 110.0) -> tuple[float, float]:
    """Occupancy-style clock hour (12 up). Not used for the speaker letter."""
    h = int(hour) % 12
    a = math.radians((h * 30) - 90.0)
    return (CX + math.cos(a) * radius, CY + math.sin(a) * radius)


def enrich_sound(
    sound: dict[str, Any] | None, *, n_lamps: int = N_LAMPS
) -> dict[str, Any] | None:
    """Attach glyph + letter xy. Returns None when there is no bearing."""
    if not isinstance(sound, dict) or sound.get("az_deg") is None:
        return None
    az = az_norm(sound.get("az_deg"))
    if az is None:
        return None
    fr = room_frame()
    # the User 2026-08-14: "do not remap the circle data coming in." The bearing is
    # passed through untouched unless a rotation has been deliberately set, and
    # offset is 0. We never relabel `frame`. heading.door_hour is the dial
    # LABEL (tick). stations.door_beam is the camera sitter — not copied here.
    off = float(fr["offset_deg"])
    if off:
        az = (az + off) % 360.0
    out = dict(sound)
    out["az_deg"] = round(az, 1)
    out["beam"] = beam_clock(az)          # the one clock reading, for both glasses
    if fr.get("door_hour") is not None:
        out["door_hour"] = int(fr["door_hour"])
    glyph = glyph_for_sound(out)
    out["glyph"] = glyph
    out["n_lamps"] = int(n_lamps)
    x, y = letter_xy(az)
    out["letter"] = {"x": round(x, 1), "y": round(y, 1)}
    return out


def build_sound_payload(
    sound_raw: dict[str, Any] | None, *, n_lamps: int = N_LAMPS
) -> dict[str, Any]:
    """JSON / SSE body. Empty sound is correct when nobody is talking."""
    enriched = enrich_sound(sound_raw, n_lamps=n_lamps)
    return {
        "ok": True,
        "schema": "vo_sound/v1",
        "sound": enriched,
        "glyph": None if not enriched else enriched.get("glyph"),
        "az_deg": None if not enriched else enriched.get("az_deg"),
    }
