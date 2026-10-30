#!/usr/bin/env python3
"""face-shell — Crissa's presentation surface + house state provider.

face-shell :8088 is NOT retired: :8099 PROXIES it for /v1/house (and the
screen surface depends on this process for presence, voice, chat, sitters,
weather, and other house state). Keep this service running headless even
when the PAGE is dormant. Safe archive = display/face-shell.html (page)
only — never stop face-shell-server.py as “retired UI.”

Serves display/face-shell.html (optional/dormant page), an SSE widget bus,
and thin same-origin proxies to the house rails (avoids CORS without
touching any agent's servers).

IN A PACK (the User 2026-10-10, .101: "why am I missing so many features in the Control Panel and the Wall?" - the
feed's /v1/house answered "house upstream down" because nothing served it): this file ships with the feed and the
launcher starts it on the port table's face_shell port (8088, the house's). Every source is this install's own piece
from lib/crissa_ports (presence, the ear, the voice); every path is the state home's (the identity store, the ear's turn
log, presence's occupancy history). A source a pack cannot have answers a mark (`marks`), never an error: the house's
engine lanes, a weather location nobody set. The house keeps its own values (config/house-machine.json and its env).

  GET  /                     shell page (may be dormant on wall — service stays)
  GET  /events               SSE — expression + echo stream
  POST /v1/express           expression/v1 widget (team_express tool)
  POST /v1/chalk             chalkboard/resting board (team_display chalk|resting|clear)
  GET  /v1/chalk             current board state (debug)
  POST /v1/echo              {target_id, verb} — touch-ring an existing control
  GET  /v1/house             merged snapshot: presence + room_event + voice state
                             (**required** by screen :8099 proxy)

Port: the port table's face_shell (CRISSA_PORT_FACE_SHELL, or FACE_SHELL_PORT; 8088), localhost only.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(ROOT, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
from audio_sink import live_mouth_line  # noqa: E402
import crissa_home  # noqa: E402
import crissa_ports  # noqa: E402

PORT = crissa_ports.port("face_shell")
PAGE = os.path.join(ROOT, "display", "face-shell.html")
HYBRID_PAGE = os.path.join(ROOT, "display", "hybrid-shell.html")
IS_HOUSE = crissa_home.is_house()
import crissa_guard  # noqa: E402  round 46: the one loopback rule every server of a pack keeps

# EVERY ROUTE IS THIS MACHINE'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): /v1/stage, /v1/mode
# and /v1/chalk took any caller, so a web page open in the buyer's browser could put its own page on her stage, write
# her board or switch the mode, and /v1/house read the room to it. lib/crissa_guard refuses, first in every do_*, a
# Host that is not this machine, a foreign Origin, and a cross-site mark with no loopback Referer. The feed, the Wall
# and team_display call from this machine with none of those and pass. On in a pack; the house keeps today's server
# until it is flipped there (CRISSA_LOOPBACK_GUARD=1); =0 is the kill.
# THE BODY'S SIZE: the biggest body here is a chalk board, whose fields are cut at 32 KB of html and 8 KB of text or
# art (POST /v1/chalk below); 1 MB is far past any board and was never a picture. A dial: CRISSA_FACE_SHELL_BODY_MAX;
# CRISSA_BODY_LENGTH=0 is the kill (the old read).
GUARD_DEFAULT = "0" if IS_HOUSE else "1"
BODY_MAX = int(os.environ.get("CRISSA_FACE_SHELL_BODY_MAX") or 1_000_000)

# The sources: the house names its presence rail in config/house-machine.json (presence_url); a pack asks its own
# presence service. The ear (stt) and the voice from the port table on both.
PRESENCE_BASE = (os.environ.get("FACE_SHELL_PRESENCE") or crissa_home.house_value("presence_url")
                 or crissa_ports.url("presence")).rstrip("/")
STT_BASE = (os.environ.get("FACE_SHELL_STT") or crissa_ports.url("stt")).rstrip("/")
PRESENCE = PRESENCE_BASE + "/v1/presence"
ROOM_EVENT = PRESENCE_BASE + "/v1/room_events/last"
VOICE_STATE = STT_BASE + "/panel/state"
EAR_HEALTH = STT_BASE + "/health"
MARKS: dict = {}            # source -> why it is not here (a pack's mark, never an error)


def identity_root() -> str:
    """The identity store: CRISSA_IDENTITY, else HOUSE_ID_ROOT, else the house's site root (config/house-machine.json
    `site_root`, where this file always read site.json and people/ - the Crissa merge, 2026-10-11), else
    <state home>/identity (lib/crissa_init's rule)."""
    return (os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or crissa_home.house_value("site_root")
            or str(crissa_home.HOME / "identity"))


def _presence_house():
    """lib/presence_house: the house's own presence payload on the neutral keys (house only, absent in a pack)."""
    try:
        import presence_house
    except ImportError as e:
        if e.name != "presence_house":
            raise
        return None
    return presence_house


def user_id() -> str:
    """The User's id: user.json (Init) in a pack, the house's config on the house, else "user"."""
    try:
        u = json.load(open(os.path.join(str(crissa_home.HOME), "user.json")))
        if u.get("person_id"):
            return str(u["person_id"]).strip().lower()
    except Exception:  # noqa: BLE001
        pass
    ph = _presence_house()          # the house's User, as this file always named him (the Crissa merge, 2026-10-11)
    if ph is not None:
        return str(ph.HOUSE_USER).strip().lower()
    return str(crissa_home.house_value("user_id") or "user").strip().lower()


def guest_names() -> set:
    """Guests who are not occupancy (no came-in / left line): the house's list in its config; a pack has none."""
    try:
        d = json.load(open(str(crissa_home.HOUSE_MACHINE)))
        return {str(x).strip().lower() for x in (d.get("door_guest_names") or [])}
    except Exception:  # noqa: BLE001
        return set()

ALLOWED_KINDS = {"card", "choices", "art", "state", "scene", "clear"}


def page_ver() -> int:
    try:
        return int(os.path.getmtime(PAGE))
    except Exception:
        return 0


# Cached outdoor glance (wttr.in) — optional UI fact strip; fail soft.
_weather_cache: dict = {"ts": 0.0, "text": "", "ok": False}
_WEATHER_TTL_S = 15 * 60


def fetch_text(url: str, timeout: float = 5.0) -> str | None:
    """An outbound read (the weather), through the one egress switch (lib/crissa_egress: kill-search)."""
    try:
        import crissa_egress
        req = urllib.request.Request(
            url, headers={"User-Agent": ("house" if IS_HOUSE else "crissa") + "-face-shell/1.0"})
        with crissa_egress.urlopen(req, timeout, "the weather glance") as r:
            return r.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return None


def weather_location() -> str | None:
    """Where the weather is for. A pack: the location Init or the site config names (identity/site.json "location",
    else <state home>/weather.json {"location"}), else None - and no glance is fetched (wttr.in would place the machine
    by its address, and nobody asked). The house: its config's weather_location, else "" (wttr.in's own guess, as
    before)."""
    if IS_HOUSE:                    # the house's config only, as before (the Crissa merge, 2026-10-11)
        return str(crissa_home.house_value("weather_location") or "")
    for path in (os.path.join(identity_root(), "site.json"), os.path.join(str(crissa_home.HOME), "weather.json")):
        try:
            loc = str((json.load(open(path)) or {}).get("location") or "").strip()
        except Exception:  # noqa: BLE001
            loc = ""
        if loc:
            return loc
    if IS_HOUSE:
        return str(crissa_home.house_value("weather_location") or "")
    return None


def weather_glance() -> dict:
    """Muted outdoor line for the surface facts strip. Never blocks the shell."""
    loc = weather_location()
    if loc is None:
        MARKS["weather"] = "no location set (Setup or <state home>/weather.json)"
        return {"ok": False, "text": "", "age_s": 0, "mark": MARKS["weather"]}
    MARKS.pop("weather", None)
    now = time.time()
    if _weather_cache["ts"] and (now - _weather_cache["ts"]) < _WEATHER_TTL_S:
        return {
            "ok": _weather_cache["ok"],
            "text": _weather_cache["text"],
            "age_s": int(now - _weather_cache["ts"]),
        }
    from urllib.parse import quote
    raw = fetch_text("https://wttr.in/%s?format=%%C+%%t" % quote(loc), timeout=4.0) or ""
    # collapse whitespace; drop leading + on temps
    text = " ".join(raw.split()).replace("+", "") if raw else ""
    _weather_cache["ts"] = now
    _weather_cache["text"] = text[:120]
    _weather_cache["ok"] = bool(text)
    return {
        "ok": _weather_cache["ok"],
        "text": _weather_cache["text"],
        "age_s": 0,
    }


def face_block(pres: dict) -> dict:
    """The presence payload's face-ID block (locked, faces, pending_look, age_s) on its engine-neutral name. The pack's
    presence serves it as `faceid`; `face_id` is the neutral key the conductor reads (lib/conductor.py). The house's
    own presence names the block after its engine, and only the house's adapter (lib/presence_house, never in a pack)
    knows that name: a file that ships never spells it (the export scrub refuses it, .101 round 3 cut 2026-10-10)."""
    ph = _presence_house()          # the house: its presence names the block after its engine (the Crissa merge)
    if ph is not None and isinstance(pres, dict):
        pres = ph.neutral(pres)
    for key in ("faceid", "face_id"):
        b = pres.get(key) if isinstance(pres, dict) else None
        if isinstance(b, dict):
            return b
    return {}


def live_auth(pres: dict) -> dict:
    """Wall / CP AUTH is presence's user_auth — one object. Sage holds that
    bool on the presence service; this echo must not mint User from the lamp colour while
    presence says Guest (the User 2026-09-09: Wall User / camera F)."""
    uid = str(pres.get("user_id") or user_id()).strip().lower()
    buf = face_block(pres)
    locked = str(buf.get("locked") or "").strip().lower()
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    hit = locked == uid or any(
        str((f or {}).get("name") or "").strip().lower() == uid for f in faces
    )
    pending = bool(buf.get("pending_look"))
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    u = pres.get("user") if isinstance(pres.get("user"), dict) else {}
    on = bool(pres.get("user_auth") is True or u.get("auth") is True)
    reason = str(
        pres.get("user_auth_reason") or u.get("auth_reason") or ("ok" if on else "no_lock")
    )
    tier = str(
        pres.get("user_auth_tier") or u.get("auth_tier") or ("held" if on else "absent")
    )
    age_s = pres.get("user_auth_age_s")
    if age_s is None:
        age_s = u.get("auth_age_s")
    if age_s is None:
        age_s = age
    return {
        "user_auth": on,
        "reason": reason,
        "present": bool(on or hit or pending),
        "voice": pres.get("user_voice") if pres.get("user_voice") is not None else u.get("user_voice"),
        "lights_out": pres.get("lights_out"),
        "session_held": bool(pres.get("f2f") or u.get("session_held") or pending or on),
        "strong_voice_age_s": pres.get("user_strong_voice_age_s"),
        "user_auth_tier": tier,
        "user_auth_age_s": age_s,
        "speaker": locked or None,
    }


def house_facts(pres: dict, ear: dict, voice: dict | None, revent: dict | None) -> dict:
    """Wall chips. Occupancy home/count are gone — Speaker row is identity."""
    ear_open = bool(ear.get("open")) if ear else None
    v = voice or {}
    voice_on = bool(
        v.get("voice_on") is True
        or (v.get("voice") or {}).get("voice") == "on"
        or v.get("on") is True
    )
    lines = []
    if ear_open is not None:
        lines.append("ear open" if ear_open else "ear quiet")
    lines.append("voice on" if voice_on else "voice off")
    return {
        "named": [],
        "headcount": None,
        "count_trust": None,
        "ear_open": ear_open,
        "voice_on": voice_on,
        "room_kind": None,
        "line": " · ".join(lines),
    }


def _lane_ok(url: str, timeout: float = 0.4) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= int(r.status) < 500
    except Exception:
        return False


def patch_voice_lanes(voice: dict | None) -> dict | None:
    """Wall voice/announcer dots: each lane's own health, never the ear's /panel/state copy (it carried stale
    addresses). Do not restart the ear to fix the glass."""
    if not isinstance(voice, dict):
        return voice
    old = voice.get("lanes") if isinstance(voice.get("lanes"), dict) else {}
    lanes = {}
    # the house's engine lanes come from its config (panel.engine_urls / voice_health_url); a pack's are its voice
    # pieces from the port table, and the house's engines are a mark there
    for name, url in lane_urls().items():
        lanes[name] = {"ok": _lane_ok(url)}
    lanes["ear"] = old.get("ear") or {"ok": False, "open": False}
    out = dict(voice)
    out["lanes"] = lanes
    return out


def lane_urls() -> dict:
    """{lane: health url}. The house: its engines and voices from config/house-machine.json (panel.lane_urls). A pack:
    the voice it runs (the port table's tts, pocket, audio8 - whichever answers is the one in use)."""
    try:
        d = json.load(open(str(crissa_home.HOUSE_MACHINE)))
        lanes = ((d.get("panel") or {}).get("lane_urls") or {}) if isinstance(d, dict) else {}
        if lanes:
            return {str(k): str(v) for k, v in lanes.items()}
    except Exception:  # noqa: BLE001
        pass
    MARKS["engines"] = "the house's engine lanes are not a Crissa piece"
    return {"voice": crissa_ports.url("tts") + "/health", "pocket": crissa_ports.url("pocket") + "/health",
            "audio8": crissa_ports.url("audio8") + "/api/health"}


def build_house() -> dict:
    """The merged snapshot. On the house, exactly the payload it served before the Crissa merge (2026-10-11): the
    face-ID block under the house presence's own name (lib/presence_house), no pack marks."""
    d = _build_house()
    ph = _presence_house() if IS_HOUSE else None
    if ph is not None:
        p = d.get("presence") if isinstance(d.get("presence"), dict) else {}
        d["presence"] = {(ph.FACE_NAMERS[0] if k == "face_id" else k): v for k, v in p.items()}   # same place
        d.pop("marks", None)
        d.pop("sources", None)
    return d


def _build_house() -> dict:
    pres = fetch(PRESENCE) or {}
    ear = (fetch(EAR_HEALTH) or {}).get("ear") or {}
    locus = fetch(STT_BASE + "/v1/media_locus") or {}
    gate = fetch(STT_BASE + "/v1/media_gate") or {}
    voice = patch_voice_lanes(fetch(VOICE_STATE))
    revent = (fetch(ROOM_EVENT) or {}).get("last")
    return {
        "ok": True,
        "presence": {
            "headcount": None,
            "count_trust": None,
            "people": [],
            "f2f": pres.get("f2f"),
            "face_id": face_block(pres),
            "seen_today": pres.get("seen_today")
            or {"n": 0, "user_seen": False, "ids": []},
            "visit_lamp": pres.get("visit_lamp") or "amber",
            "visit_lamp_info": pres.get("visit_lamp_info")
            if isinstance(pres.get("visit_lamp_info"), dict) else {},
        },
        "visit_lamp": pres.get("visit_lamp") or "amber",
        "visit_lamp_info": pres.get("visit_lamp_info")
        if isinstance(pres.get("visit_lamp_info"), dict) else {},
        "room_event": revent,
        "voice": voice,
        "ear_open": ear.get("open"),
        "weather": weather_glance(),
        "facts": house_facts(pres, ear, voice, revent),
        # user-auth-only mode: what the wall shows about who the house serves
        "chat": recent_chat(),
        # usual sitters from init (site.json stations) — dial fallback when
        # the SoT has no live seat; never paints anyone into the wall.
        "sitters": usual_sitters(),
        "occupants": [],
        "auth": live_auth(pres),
        # Mic-frame hearing (XVF3800). Glass lobe only — not a roster.
        "sound": pres.get("sound"),
        # Known guest faces today — not occupancy, not enter/leave.
        "visitor_today": pres.get("visitor_today") or [],
        "seen_today": pres.get("seen_today")
        or {"n": 0, "user_seen": False, "ids": []},
        "media": {
            "mode": locus.get("mode", "external"),
            "station": (gate.get("media_station")
                        if gate.get("media_station") is not None
                        else locus.get("station", 9)),
            "media_on": bool(gate.get("media_on")),
        },
        "page_ver": page_ver(),
        "marks": dict(MARKS),
        "sources": {"presence": bool(pres), "ear": bool(ear)},
    }


def usual_sitters() -> dict:
    """{name: hour} from site.json stations.sitters (init data)."""
    try:
        site = json.load(open(os.path.join(identity_root(), "site.json")))
        out = {}
        for hour, meta in ((site.get("stations") or {}).get("sitters") or {}).items():
            who = (meta or {}).get("usual")
            if who:
                out[str(who).lower()] = int(hour)
        return out
    except Exception:
        return {}


def _enrolled_people() -> set[str]:
    people_dir = os.path.join(identity_root(), "people")
    try:
        return {
            d.lower()
            for d in os.listdir(people_dir)
            if os.path.isdir(os.path.join(people_dir, d)) and not d.startswith(".")
        }
    except Exception:
        return set()


def _is_scratch_who(who: str) -> bool:
    w = str(who or "").strip().lower()
    return len(w) >= 2 and w[0] == "s" and w[1:].isdigit()


def _is_user_who(who: str) -> bool:
    return str(who or "").strip().lower() in (user_id(), "user", "you")


def _is_crissa_who(who: str) -> bool:
    return str(who or "").strip().lower() in ("crissa", "zoe", "crissa_her_tts")


def _log_lanes(t: dict, who: str, *, media_row: bool) -> tuple[bool, bool]:
    """User vs room print lanes — same split hear_gate stamps.

    ear-bridge writes ``log_user`` / ``log_room`` onto its poll copy; the
    STT ring never keeps them (measured 2026-08-30 omni: both null). Infer
    the same split so the wall UserOnly chip can hide ``?`` / scratch / TV.
    """
    if "log_user" in t or "log_room" in t:
        return bool(t.get("log_user")), bool(t.get("log_room"))
    w = str(who or "").strip().lower()
    if _is_crissa_who(w) or w == "door":
        return False, False
    if _is_user_who(w) and not media_row:
        return True, False
    return False, True


def _turn_to_chat_line(t: dict, enrolled: set[str]) -> dict | None:
    """One STT turn → wisp line, or None if noise/media/empty.

    Carries ``stamped`` so Voice-Only idle art only clears on a *real* User
    voice stamp (Fable 2026-08-06: sole-present relabel must not wake coffee).
    """
    txt = (t.get("text") or "").strip()
    if not txt:
        return None
    # Failed STT (CUDA/empty decode) has error + empty text — already dropped.
    stamp = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
    mg = stamp.get("media_gate") if isinstance(stamp.get("media_gate"), dict) else {}
    src0 = str(stamp.get("source") or "")
    # TV captions stay off the roll. A long hoarse desk turn (laryngitis) is
    # stamped media_gate / long_chunk_weak_emb — that is still speech when a
    # body is in the room. Measured 2026-08-24: 18.6s paragraph, emb 0.40.
    metrics = mg.get("metrics") if isinstance(mg.get("metrics"), dict) else {}
    try:
        named_hc = int(metrics.get("named_hc") or 0)
    except (TypeError, ValueError):
        named_hc = 0
    try:
        room_hc = int(metrics.get("room_hc") or 0)
    except (TypeError, ValueError):
        room_hc = 0
    tv_ghost = str(mg.get("reason") or "") == "asr_media_ghosts"
    # Omni thins stamp.media_gate away — no metrics. Fail open: a media_gate
    # source with no metrics is desk speech until proven empty-room TV.
    empty_room = bool(metrics) and named_hc == 0 and room_hc == 0
    media_row = (
        bool(stamp.get("media_suppressed_id"))
        or mg.get("verdict") == "media"
        or mg.get("media_on") is True
        or src0.startswith("media_hold")
        or src0.startswith("media_gate")
    )
    if t.get("crissa_hears") is False:
        return None
    if media_row and (tv_ghost or empty_room):
        return None
    # Argus stills are not speech — they were replaying "a person crosses…"
    # on the VoiceOnly log as if someone had said it.
    pid0 = str(t.get("person_id") or stamp.get("person_id") or "").strip().lower()
    if src0.startswith("argus") or pid0 == "argus":
        return None
    raw_who = (
        (t.get("person_id") or "")
        or (stamp.get("person_id") or "")
        or (stamp.get("display_name") or "")
    )
    who = str(raw_who).strip().lower()
    certain = bool(stamp.get("certain")) or bool(t.get("person_id"))
    src = str(stamp.get("source") or "")
    # Genuine voice/id stamp (Fable 2026-08-06): not media_hold / not display-only guess
    _genuine = src in (
        "sole_present",
        "voice_emb",
        "voice_emb_vs_sole",
        "voice_emb_weak_open",
        "vocative_in_room",
    )
    stamped = bool(
        who
        and who in enrolled
        and (certain or _genuine)
        and not src.startswith("media_hold")
        and not src.startswith("media_gate")
        and src != "tts_echo_veto"
    )
    if _is_scratch_who(who):
        # Scratch unknown-voice slots — must match the ring letter (not "stranger").
        stamped = False
    elif who not in enrolled:
        # Never paint the User (or anyone) from roster-of-one / sole-present.
        # Voice emb on the stamp is the speaker id. Occupancy roster is who
        # is in the room, not who spoke.
        if str(who).startswith("u") and len(who) > 1:
            stamped = False
        else:
            who = "stranger"
            stamped = False
    # Prefer turn ts; never invent a new now() per poll (that re-keyed the wisp
    # and re-spawned the same "stay salty…" line every SSE tick).
    try:
        ts = float(t.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts <= 0:
        ts = 0.0  # stable "unknown"; client dedupes by who|text
    log_user, log_room = _log_lanes(t, who, media_row=media_row)
    return {"who": who, "text": txt[:800], "ts": ts, "stamped": stamped,
            "source": src or None, "certain": certain,
            "log_user": log_user, "log_room": log_room}


def _voice_on() -> bool:
    d = fetch(STT_BASE + "/v1/house/voice", timeout=0.3) or {}
    return str(d.get("voice") or "").lower() == "on"


def recent_chat(n: int = 24) -> list[dict]:
    """Spoken words for the surface wisp (the User, a guest, Crissa, …).

    Prefer live turn ring via the ear's /v1/omni (same source as omni-log), then
    fall back to turns.jsonl. Skip media TV. Glance window ~last few minutes
    for human STT only. Crissa/crew speech is mouth-air (paplay) or not at all —
    never a 15-minute journal of LLM `reply` events. Dedup by who+text.
    """
    lines: list[dict] = []
    enrolled = _enrolled_people()
    now = time.time()
    cutoff = now - 10 * 60
    voice_on = _voice_on()

    # 1) Live ring — freshest, survives jsonl tail races
    try:
        omni = fetch(STT_BASE + "/v1/omni?n=40", timeout=2.0) or {}
        for t in reversed(omni.get("turns") or []):
            if not isinstance(t, dict):
                continue
            row = _turn_to_chat_line(t, enrolled)
            if not row:
                continue
            if row["ts"] and 1_000_000_000 < row["ts"] < cutoff:
                continue
            if not voice_on:
                continue
            # Pause is Crissa only — User roll is not dropped on pause.
            lines.append(row)
        # Soft live line: ear last_text when ring is empty/errored but she heard words
        ear = omni.get("ear") or {}
        last = (ear.get("last_text") or "").strip()
        if last and voice_on and not any(r["text"] == last for r in lines):
            pid = (ear.get("last_person_id") or "").strip().lower()
            if pid in enrolled:
                who, stamped = pid, True
            elif _is_scratch_who(pid):
                who, stamped = pid, False
            else:
                who, stamped = "stranger", False
            # Do not use time.time() — a missing ts would re-key the same
            # last_text on every poll and dump it across a reload.
            try:
                ets = float(ear.get("last_ts") or ear.get("started_ts") or 0)
            except (TypeError, ValueError):
                ets = 0.0
            if ets > 1_000_000_000:
                fake = {"person_id": pid, "stamp": {"source": "ear_last"}}
                lu, lr = _log_lanes(fake, who, media_row=False)
                lines.append({"who": who, "text": last[:800], "ts": ets,
                              "stamped": stamped, "source": "ear_last",
                              "certain": bool(pid),
                              "log_user": lu, "log_room": lr})
    except Exception:
        pass

    # 2) Disk tail — history if ring was empty after restart
    if len(lines) < 4:
        try:
            turns_path = os.path.expanduser(
                os.environ.get("RAIL_TURNS_LOG") or crissa_home.house_value("turns_log")
            or str(crissa_home.HOME / "ear" / "turns.jsonl"))
            with open(turns_path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 196608))
                tail = f.read().decode(errors="replace").splitlines()
            for raw in tail[-200:]:
                try:
                    t = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(t, dict):
                    continue
                row = _turn_to_chat_line(t, enrolled)
                if not row:
                    continue
                if row["ts"] and 1_000_000_000 < row["ts"] < cutoff:
                    continue
                if not voice_on:
                    continue
                lines.append(row)
        except Exception:
            pass

    # 2b) Door enter/leave — people coming in must show on the VoiceOnly roll
    try:
        occ = os.path.expanduser(
            os.environ.get("RAIL_OCCUPANCY_HISTORY") or crissa_home.house_value("occupancy_history")
            or str(crissa_home.HOME / "presence" / "data" / "occupancy_history.jsonl"))
        with open(occ, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            tail = f.read().decode(errors="replace").splitlines()
        for raw in tail[-400:]:
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            kind = str(ev.get("kind") or "")
            if kind not in ("enter", "leave"):
                continue
            try:
                ts = float(ev.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            if ts and 1_000_000_000 < ts < cutoff:
                continue
            who = str(ev.get("name") or ev.get("unnamed_key") or "").strip().lower()
            if who in guest_names():
                # Guests are not occupancy — no came-in / left line.
                continue
            if not who or who.startswith("__u") or who.startswith("u") and who[1:].isdigit():
                who = "someone"
            verb = "came in" if kind == "enter" else "left"
            lines.append(
                {
                    "who": "door",
                    "text": f"{who} {verb}",
                    "ts": ts,
                    "stamped": False,
                    "source": f"door_{kind}",
                    "certain": bool(ev.get("name")),
                }
            )
    except Exception:
        pass

    # Crissa/crew: only what Cosy is paplaying right now. Journal `reply` is a
    # log, not a queue — unspoken / skip-play lines stay off the roll.
    mouth = live_mouth_line()
    if mouth:
        lines.append(mouth)

    lines.sort(key=lambda r: r.get("ts") or 0)
    # Global who|text dedup (keep earliest ts) — adjacent-only left clones when
    # ring + disk + ear_last interleaved the same utterance.
    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for r in lines:
        who = str(r.get("who") or "").lower()
        text = (r.get("text") or "").strip()
        if not text:
            continue
        k = (who, text)
        if k not in best:
            best[k] = r
            order.append(k)
        else:
            # keep earliest real ts
            ot, nt = float(best[k].get("ts") or 0), float(r.get("ts") or 0)
            if nt and (not ot or nt < ot):
                best[k] = r
    dedup = [best[k] for k in order]
    return dedup[-n:]


def build_hq() -> dict:
    """HQ cards — the presence rails merged same-origin (F4/C3)."""
    feed = fetch(PRESENCE_BASE + "/v1/prod_feed") or {}
    insp = fetch(PRESENCE_BASE + "/v1/inspector/status") or {}
    events = (fetch(PRESENCE_BASE + "/v1/room_events?n=6") or {}).get("events") or []
    banks = {}
    people_dir = os.path.join(identity_root(), "people")
    try:
        for d in sorted(os.listdir(people_dir)):
            fdir = os.path.join(people_dir, d, "face")
            vdir = os.path.join(people_dir, d, "voice")
            if not (os.path.isdir(fdir) or os.path.isdir(vdir)):
                continue
            banks[d] = {
                "face": len([f for f in os.listdir(fdir) if f.endswith(".json")]) if os.path.isdir(fdir) else 0,
                "voice": len([f for f in os.listdir(vdir) if f.endswith(".json")]) if os.path.isdir(vdir) else 0,
            }
    except Exception:
        pass
    ops = feed.get("ops") or {}
    return {
        "ops": {"line": ops.get("line") or ops.get("status") or "up",
                "model": ops.get("model")},
        "room_event": feed.get("last_room_event") or (events[-1] if events else None),
        "events_tail": [
            {"kind": e.get("kind"), "action": e.get("action"),
             "ts": e.get("ts"), "names": e.get("door_names")}
            for e in events[-5:]
        ],
        "inspector": {
            "shadow": insp.get("shadow"), "write_emb": insp.get("write_emb"),
            "last_verdict": insp.get("last_verdict"),
            "last_stamp": insp.get("last_stamp"),
            "soft_label": (insp.get("last_verdict") or {}).get("soft_label")
                          if isinstance(insp.get("last_verdict"), dict) else None,
        },
        "banks": banks,
    }


ACTIONS = {
    # name -> (method, url, body) — proxied so Hybrid buttons + Crissa verbs share one path
    "peek_mid": ("POST", PRESENCE_BASE + "/v1/peek", {"mode": "mid_arc"}),
    "peek_pulse": ("POST", PRESENCE_BASE + "/v1/peek", {"mode": "three_pulse"}),
    # the camera home to the door: presence's joystick home (the token goes back and the camera rests on the door).
    # It named /v1/door_home, a route no presence serves - the house's neither (2026-10-10, a round-3 leftover)
    "door_home": ("POST", PRESENCE_BASE + "/v1/camera/aim", {"op": "home"}),
}


def post_upstream(url: str, body: dict, timeout: float = 20.0):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _house_broadcaster() -> None:
    """Push the house object over SSE whenever it changes (~2s cadence) - only while a page listens (the User
    2026-10-10, .101 idle load: with no /events listener it built the house and the HQ cards every 2 s for nobody)."""
    last = None
    while True:
        with _lock:
            listening = bool(_subs)
        if not listening:
            last = None
            time.sleep(2.0)
            continue
        try:
            h = build_house()
            key = json.dumps(h, sort_keys=True)
            if key != last:
                last = key
                publish({"type": "house", **h})
            hq = build_hq()
            kq = json.dumps(hq, sort_keys=True, default=str)
            if kq != getattr(_house_broadcaster, "_last_hq", None):
                _house_broadcaster._last_hq = kq
                publish({"type": "hq", **hq})
        except Exception:
            pass
        time.sleep(2.0)

_subs: list[queue.Queue] = []
_lock = threading.Lock()
_history: list[dict] = []
# What the right zone / resting layer currently holds. Replayed to every new
# SSE subscriber so a page reload never loses the board.
_board: dict = {"stage": None, "chalk": None, "resting": None}


def publish(event: dict) -> None:
    event.setdefault("ts", time.time())
    raw = json.dumps(event, ensure_ascii=False)
    with _lock:
        _history.append(event)
        del _history[:-40]
        subs = list(_subs)
    for q in subs:
        try:
            q.put_nowait(raw)
        except queue.Full:
            pass


def fetch(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):  # quiet
        return

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Wall / :8099 poller hung up mid-write (measured 2026-08-25
            # 02:45:46 on GET /v1/house). Not a house fault — same swallow
            # voiceonly-home-server already uses.
            return

    def _guard(self) -> bool:
        """First in every do_*: True = go on; False = refused 403 (round 46, above)."""
        return crissa_guard.check(self, default=GUARD_DEFAULT)

    def _body(self):
        """The body's bytes, or None after a 400/413 (round 46: -1 was rfile.read(-1), which reads to the close)."""
        if os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):   # the kill
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""
        n = crissa_guard.body_length(self.headers, BODY_MAX)
        if n is None:
            big = str(self.headers.get("Content-Length") or "").strip().isdigit()
            self._json(413 if big else 400, {"ok": False, "error": "too big" if big else "bad Content-Length"})
            return None
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        # User 2026-08-09: one surface only — face-shell at /. /hybrid was a
        # second "User Brave desk" that split Hybrid ↔ VoiceOS across windows.
        if path in ("/hybrid", "/hybrid.html", "/hybrid/"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            try:
                body = open(PAGE, "rb").read()
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:200]})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/health":
            return self._json(200, {"ok": True, "service": "face-shell", **({} if IS_HOUSE else {"port": PORT})})
        if path == "/v1/house":
            return self._json(200, build_house())
        if path == "/v1/hq":
            return self._json(200, build_hq())
        if path == "/v1/chalk":
            with _lock:
                return self._json(200, {"ok": True, **_board})
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q: queue.Queue = queue.Queue(maxsize=128)
            with _lock:
                _subs.append(q)
                hist = list(_history[-8:])
                # board replay after history: latest stage/chalk owner wins
                # client-side, resting always last (it's an overlay)
                owners = [e for e in (_board["stage"], _board["chalk"]) if e]
                owners.sort(key=lambda e: e.get("ts") or 0)
                hist += owners
                if _board["resting"]:
                    hist.append(_board["resting"])
            try:
                for ev in hist:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15.0)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(f"data: {msg}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _lock:
                    if q in _subs:
                        _subs.remove(q)
            return
        self.send_error(404)

    def do_POST(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        raw = self._body()
        if raw is None:
            return
        if path in ("/v1/mode", "/mode"):
            try:
                obj = json.loads(raw.decode() or "{}")
            except Exception:
                obj = {}
            mode = str(obj.get("mode") or "hybrid")
            try:
                import sys
                lib = os.path.join(ROOT, "lib")
                if lib not in sys.path:
                    sys.path.insert(0, lib)
                from mode_orchestrate import set_mode
                return self._json(200, set_mode(mode))
            except ImportError:
                return self._json(200, {"ok": False, "mark": "no mode switch in this edition"})
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:240]})

        try:
            obj = json.loads(raw.decode() or "{}") if raw else {}
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        if path == "/v1/express":
            kind = obj.get("kind")
            if kind not in ALLOWED_KINDS:
                return self._json(400, {"ok": False, "error": f"kind must be one of {sorted(ALLOWED_KINDS)}"})
            # cap art size so a runaway never wrecks the stage
            if isinstance(obj.get("text"), str):
                obj["text"] = obj["text"][:4000]
            obj["type"] = "express"
            publish(obj)
            return self._json(200, {"ok": True, "kind": kind})
        if path == "/v1/stage":
            # Crissa's pure display stage (right zone, Voice-Only layout).
            # {url: "..."} shows it; {url: ""} clears back to idle.
            url = str(obj.get("url") or "").strip()
            if url and not (url.startswith("http://127.0.0.1")
                            or url.startswith("http://localhost")
                            or url.startswith("https://")):
                return self._json(400, {"ok": False,
                                        "error": "url must be local or https"})
            # Never iframe the shell into itself (nests clock/presence/dial).
            # Common agent mistake: staging this shell's own address.
            if url:
                low = url.lower().rstrip("/")
                self_hosts = tuple("%s://%s:%d" % (sch, h, PORT) for sch in ("http", "https")
                                   for h in ("127.0.0.1", "localhost"))
                if low in self_hosts or low.startswith(tuple(h + "/" for h in self_hosts)):
                    # Reject root shell only; allow other paths on this port later if needed
                    path_part = low.split(":%d" % PORT, 1)[-1] if (":%d" % PORT) in low else ""
                    if path_part in ("", "/"):
                        return self._json(400, {
                            "ok": False,
                            "error": "refuse to stage face-shell into itself "
                                     "(would nest the UI); use a real page URL",
                        })
            ev = {"type": "stage", "url": url,
                  "title": str(obj.get("title") or "")[:120]}
            publish(ev)
            with _lock:
                _board["stage"] = ev if url else None
            return self._json(200, {"ok": True, "url": url})
        if path == "/v1/chalk":
            # Crissa's chalkboard (right zone) + resting art (silence overlay).
            # {kind:"chalk", text|art|html, title?} writes the board
            # {kind:"resting", text|art|html|moment, title?} sets the quiet scene
            # {kind:"clear", target:"chalk"|"resting"|"stage"|"all"}
            kind = str(obj.get("kind") or "")
            if kind not in ("chalk", "resting", "clear"):
                return self._json(400, {"ok": False,
                                        "error": "kind must be chalk|resting|clear"})
            if kind == "clear":
                target = str(obj.get("target") or "chalk")
                if target not in ("chalk", "resting", "stage", "all"):
                    return self._json(400, {"ok": False,
                                            "error": "target must be chalk|resting|stage|all"})
                with _lock:
                    for k in (("chalk", "resting", "stage")
                              if target == "all" else (target,)):
                        _board[k] = None
                publish({"type": "chalk", "kind": "clear", "target": target})
                if target in ("stage", "all"):
                    publish({"type": "stage", "url": "", "title": ""})
                return self._json(200, {"ok": True, "cleared": target})
            ev = {"type": "chalk", "kind": kind}
            for k, cap in (("title", 120), ("text", 8000), ("art", 8000),
                           ("html", 32768), ("moment", 40), ("role", 40),
                           # Resting gallery extras (Voice-Only delight 2026-08-07)
                           ("kicker", 80), ("tone", 24), ("line", 240),
                           ("float", 24),
                           # Lila scene float: image URL for fun non-ASCII renders
                           ("image", 512), ("url", 512)):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    ev[k] = v[:cap]
            if obj.get("emote") is True:
                ev["emote"] = True
            # Free-air typed reply (TTS busy/fail) — face-shell 20pt mode
            if obj.get("typed") is True or str(obj.get("role") or "").lower() == "reply":
                ev["typed"] = True
                ev["role"] = ev.get("role") or "reply"
            # image/url alone ok for resting scene float (Lila tumble window)
            need = ("text", "art", "html", "moment", "image", "url")
            if not any(k in ev for k in need):
                return self._json(400, {"ok": False,
                                        "error": f"{kind} needs text, art, html"
                                                 + (", moment, image, or url"
                                                    if kind == "resting"
                                                    else "")})
            # Local/loopback images only for security (same as stage)
            for ik in ("image", "url"):
                u = ev.get(ik)
                if not u or kind != "resting":
                    continue
                low = u.lower().strip()
                if not (low.startswith("http://127.0.0.1")
                        or low.startswith("http://localhost")
                        or low.startswith("https://127.0.0.1")
                        or low.startswith("https://localhost")
                        or low.startswith("data:image/")):
                    return self._json(400, {"ok": False,
                                            "error": f"{ik} must be loopback http(s) or data:image"})
            publish(ev)
            with _lock:
                _board[kind] = ev
            return self._json(200, {"ok": True, "kind": kind})
        if path == "/v1/action":
            name = str(obj.get("name") or "")
            spec = ACTIONS.get(name)
            if not spec:
                return self._json(400, {"ok": False, "error": f"unknown action; have {sorted(ACTIONS)}"})
            try:
                res = post_upstream(spec[1], {**spec[2], **(obj.get("args") or {})})
                publish({"type": "echo", "target_id": f"hq-{name}",
                         "verb": obj.get("verb") or name.replace("_", " ")})
                return self._json(200, {"ok": True, "result": res})
            except Exception as e:
                return self._json(502, {"ok": False, "error": str(e)[:200]})
        if path == "/v1/echo":
            publish({"type": "echo", "target_id": str(obj.get("target_id", ""))[:80],
                     "verb": str(obj.get("verb", ""))[:120]})
            return self._json(200, {"ok": True})
        self.send_error(404)


def main() -> None:
    threading.Thread(target=_house_broadcaster, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[face-shell] http://127.0.0.1:{PORT}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
