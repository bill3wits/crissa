"""Attach person_id to a transcript using presence + text — minimal flags only.

Priority (MINIMAL-FLAGS-CERTAINTY + the User soft-ID 2026-07-27):
  1. speaker emb vs voice bank (when emb present)
  2. sole named body on roster
  3. vocative / alias (a person.json alias) against roster or identity
  4. confirm_mention: purposeful name of a provisional roster person → drop ?
  5. the face check face of the speaker (F2F lock or Oracle ev_speaker) wins over 1–4
  6. else unknown — ask / wait (coached discretion later)

Face of the speaker IDs the voice (the User 2026-08-26). Occupancy chalkboard is not this path.

Soft ID: voice/vocative → id_status=provisional, display name?
Confirm: second cue or confirm_mention → confirmed, certain, no ?

Never invents a headcount. Presence is SoT for who is in the room.
Facefinder is not this module — presence owns camera last-resort.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .presence import roster_names, snapshot
from .scratch_speakers import _user_id

# Soft name cues — not a Do-Not list; just extract candidates for match
_NAME_TOKEN = re.compile(r"\b([A-Za-z][a-z]{1,20})\b")

# Site regulars (the User 2026-08-06 matrix N) — closed voice rank includes these
# even when presence still only names sole the User. Soft prior only; no tripwire.
_REGULARS_CACHE: list[str] | None = None


def station_hour(az_deg: float | None) -> int | None:
    """Mic az → clock hour for a camera-stop look. Residual 0 if unset.

    Does not publish stamp.beam_hour (desk-check still required for that).
    """
    if az_deg is None:
        return None
    raw = (os.environ.get("HOUSE_MIC_AZ_RESIDUAL_DEG") or "").strip()
    try:
        residual = float(raw) if raw else 0.0
        cam = (float(az_deg) - residual) % 360.0
    except (TypeError, ValueError):
        return None
    hour = int(round(cam / 30.0)) % 12
    return 12 if hour == 0 else hour


def _beam_hour(az_deg: float | None) -> int | None:
    """Clock hour from mic az. Residual from the door; desk must check.

    HOUSE_MIC_AZ_RESIDUAL_DEG is θ_door: clap at the door, whose hour is
    stations.door_beam in house-id/site.json (varies by office, never assume 12). Then
    (desk_az − residual) must land in [225, 255) or we stay mic-frame.
    Do not fit residual to the desk (R4 G5).
    """
    if az_deg is None:
        return None
    raw = (os.environ.get("HOUSE_MIC_AZ_RESIDUAL_DEG") or "").strip()
    if not raw:
        return None
    try:
        residual = float(raw)
        desk_az = float(os.environ.get("HOUSE_MIC_AZ_DESK_DEG", "39"))
    except (TypeError, ValueError):
        return None
    desk_cam = (desk_az - residual) % 360.0
    if not (225.0 <= desk_cam < 255.0):
        return None
    cam = (float(az_deg) - residual) % 360.0
    hour = int(round(cam / 30.0)) % 12
    return 12 if hour == 0 else hour


def _site_regulars() -> list[str]:
    """person_ids from house-id site.json stations.regulars[]."""
    global _REGULARS_CACHE
    if _REGULARS_CACHE is not None:
        return list(_REGULARS_CACHE)
    out: list[str] = []
    try:
        site = Path(
            os.environ.get(
                "HOUSE_SITE_JSON",
                str(_house_id_root() / "site.json"),
            )
        )
        if site.is_file():
            data = json.loads(site.read_text(encoding="utf-8"))
            regs = (data.get("stations") or {}).get("regulars") or []
            for r in regs:
                if isinstance(r, dict) and r.get("person_id"):
                    out.append(str(r["person_id"]).strip().lower())
                elif isinstance(r, str) and r.strip():
                    out.append(r.strip().lower())
    except Exception:
        out = []
    if not out:
        out = ["user"]  # one owner by default; site.json stations.regulars adds the rest
    _REGULARS_CACHE = out
    return list(out)

# --- TTS-fingerprint veto --------------------------------------------------------
# Prevent Crissa's own TTS output (captured via echo path) from being stamped as
# a real person (e.g. second).  We maintain one or more ECAPA embeddings of
# Zoe's TTS voice in <ear>/mouths/crissa_her_tts/*.json (or people/crissa_her_tts/voice/) and score every
# incoming speaker embedding against them.  If the utterance matches Crissa TTS
# more closely than any real-person candidate, we drop the stamp entirely.
#
# Threshold: configurable via RAIL_VOICE_TTS_VETO_SIM (default 0.50).  The
# Crissa TTS echo should score >0.75 against its own fingerprint but <0.55 against
# any real-person bank entry, so 0.50 gives comfortable margin.
#
# CRITICAL: fingerprints must match the live encoder family. Cosine similarity
# between ECAPA (192-d) and WeSpeaker (256-d) embeddings is meaningless.
# The loader filters by HOUSE_VOICE_ENCODER / RAIL_VOICE_ENCODER.
_TTS_VETO_SIM = float(os.environ.get("RAIL_VOICE_TTS_VETO_SIM", "0.50"))
# Human must beat the TTS match by this much to count as barge-in (not echo).
# Cosy leaking as second ~0.60 must still veto when crissa_her_tts is close.
_TTS_VETO_HUMAN_LEAD = float(os.environ.get("RAIL_VOICE_TTS_VETO_HUMAN_LEAD", "0.08"))
# Comma list: crissa mouth + announcer PA + rest of crew TTS (the User 2026-08-06 gym)
_TTS_VETO_IDS = [
    x.strip()
    for x in os.environ.get(
        "RAIL_VOICE_TTS_VETO_IDS", "crissa_her_tts,announcer,crew_tts"
    ).split(",")
    if x.strip()
]
_TTS_VETO_ID = _TTS_VETO_IDS[0] if _TTS_VETO_IDS else "crissa_her_tts"
_tts_fp_cache: list[dict[str, Any]] | None = None
_tts_fp_cache_encoder: str | None = None  # encoder family the cache was built for


def _live_encoder_family() -> str:
    """Return the live encoder family (ecapa | wespeaker), matching voice_bank filter."""
    raw = (
        os.environ.get("HOUSE_VOICE_ENCODER")
        or os.environ.get("RAIL_VOICE_ENCODER")
        or "wespeaker"
    ).strip().lower()
    if raw in ("wespeaker", "ws", "we"):
        return "wespeaker"
    return "ecapa"


def _fp_encoder_family(rec: dict) -> str:
    """Determine encoder family from a fingerprint record's model string."""
    model = str(rec.get("model") or rec.get("encoder") or rec.get("family") or "").lower()
    if "wespeaker" in model or model.startswith("ws/") or "campplus" in model:
        return "wespeaker"
    if "ecapa" in model or "speechbrain" in model or "spkrec" in model:
        return "ecapa"
    # Legacy prints without model tag: infer from dimension
    dim = rec.get("dim")
    if isinstance(dim, int):
        if dim == 256:
            return "wespeaker"  # ResNet221 typical dim
        if dim == 192:
            return "ecapa"  # ECAPA-TDNN typical dim
    # Bare encoding: assume live default family
    return _live_encoder_family()


def _load_tts_fingerprints() -> list[dict[str, Any]]:
    """Load TTS-mouth fingerprints from house-id bank, filtered by live encoder.

    Only fingerprints matching the current encoder family (ECAPA vs WeSpeaker)
    are loaded. Cosine similarity across encoder families is meaningless.
    Cache is invalidated when the encoder family changes.
    """
    global _tts_fp_cache, _tts_fp_cache_encoder
    want = _live_encoder_family()
    search = []
    env_m = (os.environ.get("HOUSE_MOUTHS_ROOT") or "").strip()
    hid = _house_id_root()
    if env_m:
        search.append(Path(env_m).expanduser())
    else:
        from .house_id import mouths_root

        search.append(mouths_root())
    search.append(hid / "people")
    # round 16 (e): prints recorded while the ear runs (Setup) are read on the next turn, and a print made with another
    # voice tier or speaker is stale (her_prints.py) - the cache follows the folders and that context
    ctx = None
    sig: Any = want
    if _HER_PRINTS_FOLLOW:
        try:
            from . import her_prints as _hp

            ctx = _hp.context_now()
        except Exception:  # noqa: BLE001
            ctx = None
        sig = (want, tuple(sorted((ctx or {}).items())), _hp.stale_check_on() if ctx is not None else None,
               tuple(_dir_stamp(b / pid / "voice") for b in search for pid in _TTS_VETO_IDS))
    if _tts_fp_cache is not None and _tts_fp_cache_encoder == sig:
        return _tts_fp_cache
    out: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    allowed = set(_TTS_VETO_IDS)
    for pid in _TTS_VETO_IDS:
        voice_dir = None
        for base in search:
            cand = base / pid / "voice"
            if cand.is_dir():
                voice_dir = cand
                break
        if voice_dir is None:
            continue
        if not voice_dir.is_dir():
            continue
        for f in sorted(voice_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                # accept matching person_id or explicit tts_filter role
                if data.get("person_id") in allowed or data.get("role") == "tts_filter":
                    # Only load fingerprints matching the live encoder family
                    if _fp_encoder_family(data) == want:
                        if _HER_PRINTS_FOLLOW and ctx is not None:
                            data = dict(data, _file=f.name)
                            found.append(data)
                            why = _hp.stale_why(data, ctx) if _hp.stale_check_on() else ""
                            if why:
                                stale.append(dict(data, _stale=why))
                                continue
                        out.append(data)
            except Exception:
                pass
    if _HER_PRINTS_FOLLOW and ctx is not None:
        try:
            _hp.write_report(found, out, stale, ctx)
        except Exception:  # noqa: BLE001
            pass
    _tts_fp_cache = out
    _tts_fp_cache_encoder = sig
    return out


# Kill: CRISSA_HER_PRINTS_FOLLOW=0 (prints loaded once per process, every print used, as before round 16)
_HER_PRINTS_FOLLOW = os.environ.get("CRISSA_HER_PRINTS_FOLLOW", "1").strip().lower() not in ("0", "false", "no", "off")


def _dir_stamp(d: Path) -> tuple:
    try:
        st = d.stat()
        return (str(d), st.st_mtime_ns, len(list(d.glob("*.json"))))
    except OSError:
        return (str(d), 0, 0)


def _reset_tts_fp_cache() -> None:
    """Clear the TTS fingerprint cache (for testing / hot-reload)."""
    global _tts_fp_cache, _tts_fp_cache_encoder
    _tts_fp_cache = None
    _tts_fp_cache_encoder = None


def _cosine(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity without numpy."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def tts_veto_check(emb_list: list[float], *, top_sim: float | None = None) -> dict[str, Any] | None:
    """Check if an embedding matches Crissa TTS fingerprint.

    Returns a dict with veto info if the echo veto should fire, else None.
    If top_sim is provided (the best real-person match), the veto fires when
    the TTS match exceeds both _TTS_VETO_SIM and top_sim.

    Fingerprints are loaded filtered by the live encoder family (ECAPA vs
    WeSpeaker). If no matching fingerprint exists for the current encoder,
    the check returns None (no veto).
    """
    fps = _load_tts_fingerprints()
    if not fps:
        return None
    best_tts_sim = 0.0
    matched_count = 0
    for fp in fps:
        enc = fp.get("encoding")
        if isinstance(enc, list) and len(enc) == len(emb_list):
            s = _cosine(emb_list, enc)
            matched_count += 1
            if s > best_tts_sim:
                best_tts_sim = s
    if best_tts_sim < _TTS_VETO_SIM:
        return None
    # Barge-in: a real person must clearly lead the TTS bank. A Cosy leak
    # that also scores ~0.60 as second must still veto (the User 2026-08-13).
    if top_sim is not None and top_sim >= best_tts_sim + _TTS_VETO_HUMAN_LEAD:
        return None
    return {
        "veto": True,
        "tts_fp_sim": round(best_tts_sim, 4),
        "tts_fp_count": matched_count,
        "threshold": _TTS_VETO_SIM,
        "human_lead": _TTS_VETO_HUMAN_LEAD,
    }

# Aliases come from each person's person.json (`aliases`); nothing is built in.
_DEFAULT_ALIASES: dict[str, str] = {}

_alias_cache: dict[str, str] | None = None


def _house_id_root() -> Path:
    from .house_id import house_id_root

    return house_id_root()


def load_name_aliases(*, force: bool = False) -> dict[str, str]:
    """Map alias/display lower → canonical person id (from house-id person.json)."""
    global _alias_cache
    if _alias_cache is not None and not force:
        return _alias_cache
    out = dict(_DEFAULT_ALIASES)
    root = _house_id_root() / "people"
    if root.is_dir():
        for person_dir in root.iterdir():
            if not person_dir.is_dir():
                continue
            pid = person_dir.name.lower()
            out[pid] = pid
            pj = person_dir / "person.json"
            if not pj.is_file():
                continue
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("active") is False:
                continue
            can = str(data.get("id") or pid).lower()
            out[can] = can
            disp = data.get("display")
            if isinstance(disp, str) and disp.strip():
                out[disp.strip().lower()] = can
            for a in data.get("aliases") or []:
                if isinstance(a, str) and a.strip():
                    out[a.strip().lower()] = can
    _alias_cache = out
    return out


def canonical_name(token: str, aliases: dict[str, str] | None = None) -> str:
    t = (token or "").lower().strip()
    if not t:
        return t
    amap = aliases if aliases is not None else load_name_aliases()
    return amap.get(t, t)


def expand_candidates(candidates: list[str], aliases: dict[str, str] | None = None) -> list[str]:
    """Include canonical ids plus every alias that maps into this set."""
    amap = aliases if aliases is not None else load_name_aliases()
    want = {canonical_name(c, amap) for c in candidates if c}
    # surface forms to search in text: each alias whose canonical is in want
    forms: list[str] = []
    for form, can in amap.items():
        if can in want and form not in forms:
            forms.append(form)
    for c in want:
        if c not in forms:
            forms.append(c)
    return forms


# Names that double as common English words ("will" = modal verb) hit on
# every other media sentence; demand an address/self-ID cue for these.
# Gate 1 final 2026-08-06: will alone caused 13 wrong WeSpeaker stamps — expand
# the default set (override via RAIL_VOCATIVE_COMMON_WORDS=comma,list).
_COMMON_WORD_DEFAULT = (
    "will,may,mark,hope,grace,rose,pat,joe,ann,sue,max,ray,lee,"
    "art,bob,ted,dan,sam,kim,ben,tim,amy,jay,roy,guy,don,ron"
)
_COMMON_WORD_NAMES = frozenset(
    s.strip().lower()
    for s in os.environ.get(
        "RAIL_VOCATIVE_COMMON_WORDS", _COMMON_WORD_DEFAULT
    ).split(",")
    if s.strip()
)


def _addressed(form: str, lower: str) -> bool:
    """Address cue for a common-word name: greeting before it or self-ID."""
    f = re.escape(form)
    return bool(
        re.search(rf"\b(?:hey|hi|hello|yo|okay|ok)[, ]+{f}\b", lower)
        or re.search(rf"\b(?:this is|i am|i'm|it's)\s+{f}\b", lower)
    )


def vocative_hits(text: str, candidates: list[str]) -> list[str]:
    """Canonical ids that appear as whole-word name or alias in text."""
    if not text or not candidates:
        return []
    amap = load_name_aliases()
    forms = expand_candidates(candidates, amap)
    lower = text.lower()
    hits_can: list[str] = []
    for form in forms:
        if not form:
            continue
        if form in _COMMON_WORD_NAMES and not _addressed(form, lower):
            continue
        if re.search(rf"\b{re.escape(form)}\b", lower):
            can = canonical_name(form, amap)
            if can not in hits_can:
                hits_can.append(can)
    return hits_can


def _roster_slots(presence: dict[str, Any]) -> list[dict[str, Any]]:
    people = presence.get("people") or []
    out = []
    for p in people:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").lower().strip() or None
        srcs = p.get("sources") if isinstance(p.get("sources"), dict) else {}
        last_seen = srcs.get("last_seen") or p.get("last_seen")
        out.append(
            {
                "name": name,
                "certain": bool(p.get("certain")),
                "id_status": p.get("id_status")
                or ("confirmed" if p.get("certain") else ("provisional" if name else "unknown")),
                "last_seen": last_seen,
                "sources": srcs,
            }
        )
    return out


_UNKNOWN_NAME = re.compile(r"^u\d+$", re.I)


def is_named_person(name: str | None) -> bool:
    """True for real house ids — not U1/U2 placeholders or bare 'unknown'."""
    if not name:
        return False
    n = name.lower().strip()
    if not n or n in ("unknown", "unnamed", "none", "?"):
        return False
    if _UNKNOWN_NAME.match(n):
        return False
    return True


def named_roster(presence: dict[str, Any], aliases: dict[str, str] | None = None) -> list[str]:
    """Roster names that count as face/inventory IDs (excludes U1/U2)."""
    amap = aliases if aliases is not None else load_name_aliases()
    out: list[str] = []
    for n in roster_names(presence):
        can = canonical_name(n, amap)
        if is_named_person(can) and can not in out:
            out.append(can)
    return out


def visitor_today_ids(presence: dict[str, Any]) -> list[str]:
    """Guest faces seen today — voice-label candidates, not occupancy."""
    rows = presence.get("visitor_today") or []
    out: list[str] = []
    if isinstance(rows, dict):
        rows = [
            {"person_id": k, **(v if isinstance(v, dict) else {})}
            for k, v in rows.items()
        ]
    for row in rows:
        if isinstance(row, str):
            pid = row.strip().lower()
        elif isinstance(row, dict):
            pid = str(row.get("person_id") or row.get("name") or "").strip().lower()
        else:
            continue
        if pid and pid not in out:
            out.append(pid)
    return out


def _voice_too_short(text: str, speech_duration_s: float | None) -> bool:
    """Fable P0 2026-08-01: skip emb scoring on short clips (noise sims).

    Gate fires if duration known and below RAIL_VOICE_MIN_SEC (default 1.5),
    OR duration unknown and text_len below RAIL_VOICE_MIN_TEXT (default 15).
    """
    import os

    min_sec = float(os.environ.get("RAIL_VOICE_MIN_SEC", "1.5"))
    min_text = int(os.environ.get("RAIL_VOICE_MIN_TEXT", "15"))
    if speech_duration_s is not None:
        try:
            if float(speech_duration_s) + 1e-9 < min_sec:
                return True
        except (TypeError, ValueError):
            pass
        return False  # duration known and long enough
    return len((text or "").strip()) < min_text


def _presence_user_id(presence: dict[str, Any]) -> str:
    """User id from presence, else env / house-id role=user. Never a hardcoded name."""
    user_blk = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    from_pres = str(
        user_blk.get("user_id") or presence.get("user_id") or ""
    ).strip().lower()
    if from_pres:
        return from_pres
    return _user_id()


_FACE_SPEAKER_SIM = 0.60
_FACE_SPEAKER_AGE_S = 30.0


def _locked_face_face(buf: dict[str, Any]) -> tuple[str | None, float | None]:
    locked = str(buf.get("locked") or "").strip().lower() or None
    if not locked:
        return None, None
    sim: float | None = None
    for rec in buf.get("faces") or []:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("name") or "").strip().lower() != locked:
            continue
        try:
            sim = float(rec.get("sim"))
        except (TypeError, ValueError):
            sim = None
        break
    if sim is None:
        names = buf.get("names") if isinstance(buf.get("names"), dict) else {}
        try:
            if locked in names:
                sim = float(names.get(locked))
        except (TypeError, ValueError):
            sim = None
    return locked, sim


def _speaker_ring_row(presence: dict[str, Any]) -> dict[str, Any] | None:
    for r in presence.get("visits") or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("event_id") or "") == "ev_speaker" or str(
            r.get("event") or ""
        ).lower() == "speaker":
            return r
    sp = presence.get("speaker") if isinstance(presence.get("speaker"), dict) else {}
    pid = str(sp.get("person_id") or "").strip().lower() or None
    if pid:
        return {"person_id": pid, "face": {"sim": sp.get("camera")}, "trigger_ts": None}
    return None


def face_speaker_id(
    presence: dict[str, Any],
    *,
    beam_hour: int | None = None,
) -> tuple[str | None, float | None]:
    """the face check face of the speaker → person_id. Not occupancy.

    Live F2F lock, or the Oracle ev_speaker init while the beam is the User seat.
    Weak / scenery / media locks do not name the voice.
    """
    if str(presence.get("camera_mode") or "").lower() == "media":
        return None, None
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    f2f = bool(presence.get("f2f") or (presence.get("user") or {}).get("f2f"))
    if f2f and buf.get("f2f"):
        try:
            age = float(buf["age_s"]) if buf.get("age_s") is not None else 0.0
        except (TypeError, ValueError):
            age = 0.0
        if age <= 5.0:
            pid, sim = _locked_face_face(buf)
            if pid and sim is not None and sim >= _FACE_SPEAKER_SIM:
                return pid, sim
    row = _speaker_ring_row(presence)
    if not row:
        return None, None
    pid = str(row.get("person_id") or "").strip().lower() or None
    b = row.get("face") if isinstance(row.get("face"), dict) else {}
    sim: float | None = None
    try:
        if b.get("sim") is not None:
            sim = float(b.get("sim"))
    except (TypeError, ValueError):
        sim = None
    if sim is None:
        try:
            sim = float(row.get("sim"))
        except (TypeError, ValueError):
            sim = None
    if not pid or sim is None or sim < _FACE_SPEAKER_SIM:
        return None, None
    ts = row.get("trigger_ts")
    if ts is not None:
        try:
            if time.time() - float(ts) > _FACE_SPEAKER_AGE_S:
                return None, None
        except (TypeError, ValueError):
            pass
    try:
        seat = int(os.environ.get("HOUSE_USER_SEAT", "6"))
    except (TypeError, ValueError):
        seat = 6
    if beam_hour is not None:
        try:
            if int(beam_hour) == seat:
                return pid, sim
        except (TypeError, ValueError):
            pass
    return None, None


def stamp_turn(
    text: str,
    presence: dict[str, Any],
    *,
    extra_names: list[str] | None = None,
    voice_emb: Any = None,
    speech_duration_s: float | None = None,
    beam_angle: float | None = None,
    beam_conf: float | None = None,
) -> dict[str, Any]:
    """Return stamp dict to merge into STT / voice turn JSON.

    Soft-ID fields (the User 2026-07-27):
      id_status: provisional | confirmed | null
      certain: bool (False → log as name?)
      display_name: \"second?\" or \"second\"
      confirm: true when this turn promotes provisional → confirmed
    """
    amap = load_name_aliases()
    # Occupancy roster is not a product. Do not stamp from people[].
    roster: list[str] = []
    known: list[str] = []
    # ...with ONE exception (2026-10-11, .101 round 9): presence's fixed-camera slot - the User, alone, the camera's one
    # face, matched at the live-face bar (crissa_presence.fixed_cam; flagged sources.fixed_cam_sole, certain). It is
    # the camera's own reading of the seat, not the occupancy mill, so the sole_present rule below may use it.
    # RAIL_FIXED_CAM_SOLE_STAMP=0 ignores it.
    if os.environ.get("RAIL_FIXED_CAM_SOLE_STAMP", "1").strip().lower() not in ("0", "off", "false", "no"):
        for _s in _roster_slots(presence):
            if _s.get("name") and _s.get("certain") and (_s.get("sources") or {}).get("fixed_cam_sole"):
                known.append(canonical_name(str(_s["name"]), amap))
        known = list(dict.fromkeys(known))[:1] if len(set(known)) == 1 else []
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    live_names: list[str] = []
    locked = str(buf.get("locked") or "").strip().lower()
    if locked and is_named_person(locked):
        live_names.append(canonical_name(locked, amap))
    for f in buf.get("faces") or []:
        if not isinstance(f, dict):
            continue
        n = canonical_name(str(f.get("name") or "").strip().lower(), amap)
        if n and is_named_person(n):
            live_names.append(n)
    try:
        from .house_id import index_ids

        bank_ids = [canonical_name(n, amap) for n in index_ids() if n]
    except Exception:
        bank_ids = []
    regulars = [canonical_name(n, amap) for n in _site_regulars()]
    visitors = [
        canonical_name(n, amap)
        for n in visitor_today_ids(presence)
        if n and is_named_person(n)
    ]
    voice_cands = list(
        dict.fromkeys(
            [n for n in bank_ids if n and is_named_person(n)]
            + live_names
            + visitors
        )
    )
    user_blk = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    sudo_uid = _presence_user_id(presence)
    sudo_authed = False
    pool = list(
        dict.fromkeys(
            known
            + regulars
            + [canonical_name(n, amap) for n in (extra_names or []) if n]
            + ([sudo_uid] if sudo_authed and sudo_uid else [])
        )
    )
    snaps = snapshot(presence)
    slots = _roster_slots(presence)
    provisional = {
        s["name"]
        for s in slots
        if s.get("name") and is_named_person(s.get("name")) and not s.get("certain")
    }
    hc_n = 0
    # Unidentified capacity: unknowns on roster, or headcount above named count
    unidentified_slots = max(
        0,
        sum(1 for n in roster if not is_named_person(n)),
        hc_n - len(known),
    )

    hits_all = vocative_hits(text or "", pool)
    hits_in_room = [h for h in hits_all if h in known]
    person_id: str | None = None
    source: str | None = None
    conf = 0.0
    voice_sim: float | None = None
    id_status: str | None = None
    certain = False
    confirm = False
    voice_skip: str | None = None
    voice_usable = voice_emb is not None and not _voice_too_short(text or "", speech_duration_s)
    if voice_emb is not None and not voice_usable:
        voice_skip = "short_clip"

    # 1) Voice emb — strongest speaker signal when bank has prints.
    #    Presence owns headcount; ears stamp speaker id among WHO IS HERE.
    #    Law (the User 2026-08-11): question is "who here spoke", never "who in the
    #    world bank spoke." Named present → closed rank only; no open-bank
    #    fallback. Empty room → no invent stamp (surprise/camera owns bodies).
    #    U-slots only → soft-fill among site regulars (bodies here, need names).
    #    Short clips: still logged in P0, but do not stamp or override sole.
    if voice_usable:
        try:
            from .voice_bank import match_sim_for, rank_voice

            thr_global = match_sim_for(None)
            thr_soft = float(
                __import__("os").environ.get("RAIL_VOICE_SOFT_FILL_SIM", "0.55")
            )
            vid: str | None = None
            vsim: float | None = None
            if voice_cands:
                # Closed set: only people currently on the presence roster.
                ranked = rank_voice(voice_emb, roster=voice_cands, top_k=2)
                if ranked:
                    thr = match_sim_for(ranked[0][0])
                    vsim = float(ranked[0][1])
                    voice_sim = round(vsim, 3)
                    if vsim >= thr:
                        vid = ranked[0][0]
                    # else: keep voice_sim for logs; do NOT open-bank invent
            elif unidentified_slots > 0:
                # Bodies here without names — soft-fill among house regulars only
                # (not full bank: no off-site Visitor invent while U1 walks in).
                fill_roster = [
                    n for n in regulars if n and is_named_person(n)
                ]
                ranked = (
                    rank_voice(voice_emb, roster=fill_roster, top_k=2)
                    if fill_roster
                    else []
                )
                if ranked:
                    vsim = float(ranked[0][1])
                    voice_sim = round(vsim, 3)
                    if vsim >= thr_soft:
                        vid = ranked[0][0]
                    else:
                        source = "voice_emb_weak_open"
                        conf = min(0.5, 0.3 + 0.3 * max(0.0, vsim))
                        person_id = None
            else:
                # Empty room (hc 0 / no named / no U): log best-of-regulars for
                # diagnostics only — never stamp a world identity into SoT.
                diag = rank_voice(
                    voice_emb,
                    roster=[n for n in regulars if n] or None,
                    top_k=1,
                )
                if diag:
                    voice_sim = round(float(diag[0][1]), 3)
                source = "voice_emb_empty_room"
                conf = 0.0
                person_id = None

            if vid is not None and vsim is not None:
                thr = match_sim_for(vid) if vid else thr_global
                if float(vsim) >= thr or (
                    not voice_cands
                    and unidentified_slots > 0
                    and float(vsim) >= thr_soft
                ):
                    vid = canonical_name(vid, amap)
                    on_bank = vid in voice_cands
                    on_visitor = vid in visitors
                    on_sudo = bool(sudo_uid and vid == sudo_uid)
                    # Master flag: enrolled bank + visor + visitors. Occupancy
                    # people[] is not a product roster.
                    if on_bank or on_sudo or on_visitor:
                        person_id = vid
                        source = (
                            "voice_emb_user_auth"
                            if on_sudo and not on_bank
                            else (
                                "voice_emb_visitor"
                                if on_visitor and not on_bank
                                else "voice_emb"
                            )
                        )
                        conf = min(0.95, 0.55 + 0.4 * max(0.0, float(vsim)))
                        if on_sudo and not on_bank:
                            id_status = "provisional"
                            certain = False
                        elif on_visitor and not on_bank:
                            # Guest face today → name the turn, not occupancy
                            id_status = "provisional"
                            certain = False
                        elif vid in provisional or conf < 0.85:
                            id_status = "provisional"
                            certain = False
                        else:
                            id_status = (
                                "provisional" if conf < 0.9 else "confirmed"
                            )
                            certain = id_status == "confirmed"
                    elif unidentified_slots > 0 and float(vsim) >= thr_soft:
                        # Soft name into open U-slot — provisional only
                        person_id = vid
                        source = "voice_emb"
                        conf = min(0.95, 0.55 + 0.4 * max(0.0, float(vsim)))
                        id_status = "provisional"
                        certain = False
                    else:
                        # Named room does not include this id — do not steal
                        source = "voice_emb_off_roster"
                        conf = 0.4
                        person_id = None
                        if voice_sim is None:
                            voice_sim = round(float(vsim), 3)
        except Exception:
            pass

    # 2) Sole body / vocative only if emb did not lock person_id.
    #    Do NOT hard-block on voice_emb_off_roster — sole present still paints omni
    #    UNLESS open-set voice clearly prefers someone else (the User/Second 2026-08-01):
    #    face-sole the user + second speaking was stamp_source=sole_present with weak
    #    the user sim while open_top_id=second — that is wrong soft ID.
    if person_id is None:
        # Sole body: one *named* roster id (U1 alone is not a sole name stamp).
        # Always stamp that id (soft-ID). "certain" only when face-certain AND
        # trust is fact (or unset). Stale sole → provisional "name?" for omni/log
        # so continuous ear still paints person_id (measured 2026-07-31 gym pass).
        sole_trust = snaps.get("count_trust")
        sole_ok = snaps.get("headcount") == 1 and len(known) == 1
        sole_slot = (
            next((s for s in slots if s.get("name") == known[0]), None)
            if sole_ok
            else None
        )
        sole_certain = bool(sole_slot and sole_slot.get("certain"))
        if sole_ok:
            sole_name = known[0]
            # Stale sole: presence still says "only the User" hours after last face
            # (measured 2026-08-06: hc=1 the user certain, last_seen 10h, count_trust
            # stale → every room voice became sole_present the user). Soft demote:
            # voice among regulars may win without requiring sole_sim < 0.20.
            sole_stale = False
            try:
                last_seen = None
                if sole_slot:
                    srcs = sole_slot.get("sources") or {}
                    last_seen = srcs.get("last_seen") or sole_slot.get("last_seen")
                if last_seen is not None:
                    age = time.time() - float(last_seen)
                    thr_age = float(
                        os.environ.get("RAIL_SOLE_STALE_SEC", "600")
                    )  # 10 min default
                    sole_stale = age >= thr_age
                if str(sole_trust or "").lower() == "stale" and not snaps.get(
                    "count_certain"
                ):
                    sole_stale = True
            except Exception:
                sole_stale = str(sole_trust or "").lower() == "stale"

            # Voice conflict among WHO IS HERE only (multi-named roster).
            # Never open-bank invent a second person off a sole slot (that was
            # the Visitor/world path). If Second is speaking she must be on
            # presence first — ears do not grow the inventory.
            voice_overrides_sole = False
            if voice_usable and len(known) >= 2:
                try:
                    from .voice_bank import match_sim_for, rank_voice

                    thr_match = match_sim_for(None)
                    thr_vs_sole = float(
                        __import__("os").environ.get(
                            "RAIL_VOICE_VS_SOLE_SIM",
                            str(min(0.25, thr_match)),
                        )
                    )
                    thr_sole_max = float(
                        __import__("os").environ.get(
                            "RAIL_VOICE_VS_SOLE_MAX_SOLE_SIM", "0.20"
                        )
                    )
                    thr_margin = float(
                        __import__("os").environ.get(
                            "RAIL_VOICE_VS_SOLE_MARGIN", "0.05"
                        )
                    )
                    here_r = rank_voice(voice_emb, roster=list(known), top_k=3)
                    if here_r:
                        open_id, open_sim = here_r[0][0], float(here_r[0][1])
                        open_id = canonical_name(open_id, amap)
                        sole_sim = next(
                            (
                                float(s)
                                for n, s in here_r
                                if canonical_name(n, amap) == sole_name
                            ),
                            -1.0,
                        )
                        sole_weak = sole_sim < thr_sole_max
                        if sole_stale:
                            sole_weak = True
                        if (
                            open_id
                            and open_id != sole_name
                            and open_id in known
                            and is_named_person(open_id)
                            and open_id not in ("crissa_her_tts", "crissa-her-tts", "tts_echo")
                            and sole_weak
                            and open_sim >= thr_vs_sole
                            and open_sim >= sole_sim + thr_margin
                        ):
                            person_id = open_id
                            source = "voice_emb_vs_sole"
                            conf = min(0.9, 0.5 + 0.4 * open_sim)
                            id_status = "provisional"
                            certain = False
                            voice_sim = round(open_sim, 3)
                            voice_overrides_sole = True
                except Exception:
                    pass
            if not voice_overrides_sole:
                # Stale sole + no clear voice winner: leave unlabeled rather than
                # paint every speaker as the ghost face (the User 2026-08-06 multi-person).
                if sole_stale and voice_usable:
                    person_id = None
                    source = "sole_stale_unlabeled"
                    conf = 0.0
                    id_status = None
                    certain = False
                else:
                    person_id = sole_name
                    source = "sole_present"
                    # Face-certain sole (even on stale desk hold) → confirmed.
                    # Uncertain sole on stale/empty trust → provisional (still stamp id).
                    if (
                        sole_certain
                        and not sole_stale
                        and sole_trust in ("fact", None, "certain")
                    ):
                        certain = True
                        conf = 0.7 if snaps.get("count_certain") else 0.65
                        id_status = "confirmed"
                    elif sole_certain and sole_stale:
                        certain = False
                        conf = 0.45
                        id_status = "provisional"
                        person_id = sole_name
                        source = "sole_present_stale"
                    else:
                        certain = False
                        conf = 0.5
                        id_status = "provisional"
        elif len(hits_in_room) == 1:
            person_id = hits_in_room[0]
            source = "vocative_in_room"
            conf = 0.5
            id_status = "provisional"
            certain = False
        elif len(hits_in_room) > 1:
            person_id = None
            source = "vocative_ambiguous"
            conf = 0.0
        elif (
            person_id is None
            and len(hits_all) == 1
            and unidentified_slots > 0
            and is_named_person(hits_all[0])
        ):
            # Self-ID / name while only U-slots present ("This is the User") — soft stamp
            person_id = hits_all[0]
            source = "vocative_unknown_slot"
            conf = 0.45
            id_status = "provisional"
            certain = False

    # 3) Non-overt confirm: text names a provisional roster person (the User says
    #    "Second" / "Shell" on purpose while log shows second?)
    confirm_hits = [h for h in hits_all if h in provisional]
    if confirm_hits:
        # Prefer unique provisional hit
        if len(confirm_hits) == 1 or (
            person_id and person_id in confirm_hits
        ):
            target = person_id if person_id in confirm_hits else confirm_hits[0]
            # Promote without ceremony
            if person_id is None or person_id == target:
                person_id = target
                if source in (None, "sole_present", "vocative_in_room", "voice_emb", "vocative_unknown_slot"):
                    if source != "voice_emb":
                        source = "confirm_mention"
                    confirm = True
                    certain = True
                    id_status = "confirmed"
                    conf = max(conf, 0.8)

    # 4) Alias-only mention off empty room: still report mentioned for logs
    #    (does not invent headcount)

    # 5) the face check face of the speaker IDs the voice. Occupancy chalkboard is not
    #    this path. TTS echo still vetoes below.
    face_pid, face_sim = face_speaker_id(
        presence, beam_hour=_beam_hour(beam_angle)
    )
    if face_pid:
        person_id = face_pid
        source = "face_speaker"
        conf = min(0.95, 0.55 + 0.4 * float(face_sim or 0))
        certain = True
        id_status = "confirmed"
        confirm = False

    # --- TTS-fingerprint veto ---------------------------------------------------
    # If the voice embedding matches Crissa TTS more than any real-person candidate,
    # this is an echo of our own TTS output.  Null out the stamp so it doesn't
    # pollute presence / omni with "second?" etc.
    veto_info: dict[str, Any] | None = None
    # Accept list or array-like emb for veto (tests + live encoders)
    _emb_for_veto = voice_emb
    if _emb_for_veto is not None and not isinstance(_emb_for_veto, list):
        try:
            _emb_for_veto = [float(x) for x in list(_emb_for_veto)]
        except Exception:
            _emb_for_veto = None
    if _emb_for_veto is not None:
        # Always veto if we somehow stamped the TTS id itself
        if person_id is not None and str(person_id).lower() in (
            "crissa_her_tts",
            "crissa-her-tts",
            "tts_echo",
        ):
            veto = {
                "veto": True,
                "tts_fp_sim": float(voice_sim or 1.0),
                "tts_fp_count": 0,
                "threshold": _TTS_VETO_SIM,
            }
        else:
            veto = tts_veto_check(_emb_for_veto, top_sim=voice_sim)
        if veto:
            person_id = None
            source = "tts_echo_veto"
            conf = 0.0
            id_status = None
            certain = False
            confirm = False
            voice_sim = None
            # Log what the match would have been (for P0 / diagnostics)
            veto_info = {
                "tts_fp_sim": veto.get("tts_fp_sim"),
                "tts_fp_count": veto.get("tts_fp_count"),
                "threshold": veto.get("threshold"),
            }

    # --- Phase A isUser + Phase B matrix shadow (plan rev4) -------------------
    # isUser LIVE paint guard; matrix scores SHADOW until RAIL_MATRIX_SPOKE=1.
    is_user_info: dict[str, Any] | None = None
    matrix_shadow: dict[str, Any] | None = None
    is_user_guard_suppressed = False
    try:
        from .id_matrix import (
            apply_is_user_paint_guard,
            build_columns,
            desk_silence_allows_user,
            is_user_gate,
            is_user_id,
            score_spoke,
            user_key,
        )
        from .id_matrix_config import MATRIX_SHADOW, MATRIX_SPOKE_LIVE
        from .voice_bank import rank_voice

        user_sim_a = -1.0
        best_other_id: str | None = None
        best_other_sim = -1.0
        voice_sims: dict[str, float] = {}
        # Stage-1 rank already uses bank ids + live faces + visitors
        # (voice_cands). is_user must see the same set so a visitor lead is
        # best_other, not second-from-regulars-only (2026-09-07: the user 0.37
        # / visitor 0.585 still is_user=None because columns were the user+second).
        cols = build_columns(known, regulars, extra=voice_cands)
        if voice_usable and voice_emb is not None:
            ranked = rank_voice(voice_emb, roster=cols or None, top_k=max(3, len(cols) or 3))
            for name, sim in ranked:
                nm = canonical_name(name, amap)
                voice_sims[nm] = float(sim)
            # round 46: the User's column is the id Init gave (id_matrix.user_key), not only the literal "user"
            user_sim_a = float(voice_sims.get(user_key(voice_sims), -1.0))
            for nm, sim in sorted(voice_sims.items(), key=lambda x: -x[1]):
                if not is_user_id(nm):
                    best_other_id, best_other_sim = nm, float(sim)
                    break

        tts_hit = source == "tts_echo_veto" or bool(veto_info)
        media_on = False
        try:
            # soft: presence may carry media flag
            media_on = bool(
                snaps.get("media_on")
                or (snaps.get("camera_mode") or "").lower() == "media"
            )
        except Exception:
            media_on = False

        ibr = is_user_gate(
            user_sim=user_sim_a,
            best_other_id=best_other_id,
            best_other_sim=best_other_sim,
            media_on=media_on,
            tts_veto=tts_hit,
            voice_usable=voice_usable,
        )
        is_user_info = ibr.as_dict()

        # Desk silence metadata for fold-in 1
        sole_name_g = known[0] if snaps.get("headcount") == 1 and len(known) == 1 else None
        sole_slot_g = next(
            (s for s in slots if s.get("name") == sole_name_g), None
        ) if sole_name_g else None
        face_age = None
        seat_beam = None
        try:
            if sole_slot_g:
                ls = (sole_slot_g.get("sources") or {}).get("last_seen") or sole_slot_g.get(
                    "last_seen"
                )
                if ls is not None:
                    face_age = time.time() - float(ls)
                seat_beam = sole_slot_g.get("beam") or sole_slot_g.get("seat") or (
                    (sole_slot_g.get("sources") or {}).get("beam")
                )
        except Exception:
            pass
        open_u = unidentified_slots > 0
        desk_ok = desk_silence_allows_user(
            voice_usable=voice_usable,
            sole_name=sole_name_g,
            sole_certain=bool(sole_slot_g and sole_slot_g.get("certain")),
            sole_stale=bool(
                sole_name_g
                and str(snaps.get("count_trust") or "").lower() == "stale"
            ),
            face_auth_age_s=face_age,
            seat_beam=int(seat_beam) if seat_beam is not None else None,
            open_u=open_u,
        )

        pre_pid = person_id
        person_id, source, _gnote, suppressed = apply_is_user_paint_guard(
            person_id,
            source,
            ibr.is_user,
            voice_usable=voice_usable,
            sole_stale=bool(
                sole_name_g
                and str(snaps.get("count_trust") or "").lower() == "stale"
            ),
            desk_silence_ok=desk_ok,
        )
        is_user_guard_suppressed = bool(suppressed)
        if suppressed and pre_pid:
            conf = 0.0
            certain = False
            id_status = None
            confirm = False

        # Phase B shadow (always log when enabled)
        if MATRIX_SHADOW or MATRIX_SPOKE_LIVE:
            on_set = set(known)
            live_sex = None
            for s in slots:
                g = s.get("gender")
                if g in ("M", "F"):
                    live_sex = g
                    break
            mx = score_spoke(
                cols,
                voice_sims=voice_sims,
                on_roster=on_set,
                sole_name=sole_name_g,
                live_sex=live_sex,
                is_user=ibr.is_user,
            )
            matrix_shadow = mx.as_dict()
            # Live matrix drive only when explicitly green
            if (
                MATRIX_SPOKE_LIVE
                and mx.winner
                and ibr.is_user is not False
                and source != "face_speaker"
            ):
                if not (is_user_id(mx.winner) and ibr.is_user is not True):
                    person_id = mx.winner
                    source = "matrix_spoke"
                    conf = min(0.9, 0.5 + 0.4 * mx.scores.get(mx.winner, 0.0))
                    id_status = "provisional"
                    certain = False
    except Exception:
        pass

    display_name: str | None = None
    if person_id:
        if id_status is None:
            id_status = "confirmed" if certain else "provisional"
        display_name = person_id if certain else f"{person_id}?"

    try:
        beam_angle = float(beam_angle) % 360.0 if beam_angle is not None else None
    except (TypeError, ValueError):
        beam_angle = None
    try:
        beam_conf = float(beam_conf) if beam_conf is not None else None
    except (TypeError, ValueError):
        beam_conf = None

    out = {
        "person_id": person_id,
        "source": source,
        "confidence": conf,
        "certain": certain,
        "id_status": id_status,
        "display_name": display_name,
        "confirm": confirm,
        "vocative_hits": hits_all,
        "mentioned": hits_all,
        "roster": roster,
        "presence": snaps,
        "beam_angle": beam_angle,
        "beam_conf": beam_conf,
        "beam_hour": _beam_hour(beam_angle),
        "voice_emb_sim": voice_sim,
        "voice_skip": voice_skip,
        "speech_duration_s": (
            round(float(speech_duration_s), 3)
            if speech_duration_s is not None
            else None
        ),
        "tts_veto": veto_info if source == "tts_echo_veto" else None,
        "is_user": (is_user_info or {}).get("is_user") if is_user_info else None,
        "is_user_score": (is_user_info or {}).get("is_user_score") if is_user_info else None,
        "is_user_reason": (is_user_info or {}).get("is_user_reason") if is_user_info else None,
        "user_sim": (is_user_info or {}).get("user_sim") if is_user_info else None,
        "best_other_id": (is_user_info or {}).get("best_other_id") if is_user_info else None,
        "best_other_sim": (is_user_info or {}).get("best_other_sim") if is_user_info else None,
        "is_user_guard_suppressed": is_user_guard_suppressed,
        "matrix_shadow": matrix_shadow,
    }
    if os.environ.get("RAIL_STT_PUBLISH_MATRIX", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        out["matrix_shadow"] = None
    # Treaty P0: measure soft-ID quality (never count)
    try:
        from .voice_bank import _encoder_filter
        from .voice_p0 import append_p0, build_p0_row

        append_p0(
            build_p0_row(
                presence=presence,
                text=text or "",
                voice_emb=voice_emb,
                stamp=out,
                model=_encoder_filter() or "ecapa",
            )
        )
    except Exception:
        pass

    # Adaptive s-norm shadow fields (P0 only — never affects live gating).
    # Computed against full enrolled bank + logged media embs for calibrated
    # cross-cohort similarity.  Defaults OFF; enable with RAIL_VOICE_ASNORM=1.
    try:
        _asnorm_on = os.environ.get("RAIL_VOICE_ASNORM", "").strip().lower()
        if _asnorm_on in ("1", "true", "yes", "on") and voice_emb is not None:
            from .voice_bank import asnorm_calibrated_score
            from .voice_p0 import would_stamp

            asnorm = asnorm_calibrated_score(
                voice_emb,
                target_person=person_id,
            )
            out["top_sim_asnorm"] = asnorm.get("top_sim")
            out["top_name_asnorm"] = asnorm.get("top_name")
            out["asnorm_calibrated_score"] = asnorm.get("calibrated_score")
            out["asnorm_cohort_mu"] = asnorm.get("cohort_mu")
            out["asnorm_cohort_sigma"] = asnorm.get("cohort_sigma")
            out["asnorm_z_score"] = asnorm.get("z_score")
            out["asnorm_cohort_size"] = asnorm.get("cohort_size")
            # Would an asnorm-based stamp have fired?
            if asnorm.get("calibrated_score") is not None:
                thr_asnorm = float(os.environ.get("RAIL_VOICE_ASNORM_THR", "0.5"))
                n_known = len(known)
                out["would_stamp_asnorm"] = would_stamp(
                    asnorm["calibrated_score"],
                    None,  # margin not used for calibrated score
                    n_roster=n_known,
                    thr_sim=thr_asnorm,
                    thr_margin=0.0,
                )
            else:
                out["would_stamp_asnorm"] = None
    except Exception:
        # Shadow fields must never break the live path
        pass

    return out
