"""Aggregate Control Panel state (read-only + voice SoT)."""

from __future__ import annotations

from . import settings as _S  # this install's addresses (the shared port table, 2026-10-10)

import json
import os
from typing import Any

from .display_surface import status as display_status
from .ear_loop import get_ear_loop
from .house_id import house_id_root, index_ids, people_dir, voice_bank_counts
from .house_voice import is_voice_on, read_state
from .presence import fetch_presence


def _face_slots(person_id: str) -> int:
    d = people_dir() / person_id / "face"
    if not d.is_dir():
        return 0
    return len(list(d.glob("*.json"))) + len(list((d / "refs").glob("*.jpg") if (d / "refs").is_dir() else []))


def _bank_status() -> dict[str, Any]:
    voices = voice_bank_counts()
    ids = index_ids() or sorted(set(voices.keys()) | {"user"})
    out: dict[str, Any] = {}
    for pid in ids:
        fv = voices.get(pid, 0)
        # face: count enroll json under face/
        fd = people_dir() / pid / "face"
        fj = len(list(fd.glob("enroll_*.json"))) if fd.is_dir() else 0
        if fj == 0 and fd.is_dir():
            fj = len(list(fd.glob("*.json")))
        out[pid] = {
            "face": fj > 0,
            "face_files": fj,
            "voice_slots": fv,
            "reenroll_cmd": f"enroll voice {pid}",
        }
    return out


def _lane_health() -> dict[str, Any]:
    """Best-effort lane dots — never block panel."""
    import urllib.request

    lanes: dict[str, Any] = {}
    probes = {
        # this install's lanes (2026-10-10; the house probed its own engines, voice and announcer by port): the
        # engine is the Connect endpoint on this machine, the mouth is the player, the announcer the voice module's
        # Audio8; a second engine only when an install names one. An empty address reads down.
        "ops": (_S.brain_base() + "/models") if _S.brain_base() else "",
        "crew": os.environ.get("RAIL_CREW_HEALTH", ""),
        "cosy": _S.url("player", path="/v1/health"),
        "announcer": _S.url("audio8", path="/api/health"),
    }
    for name, url in probes.items():
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=0.4) as r:
                lanes[name] = {"ok": r.status < 500}
        except Exception:
            lanes[name] = {"ok": False}
    try:
        ear = get_ear_loop().status()
        lanes["ear"] = {"ok": True, "open": bool(ear.get("open"))}
    except Exception:
        lanes["ear"] = {"ok": False, "open": False}
    return lanes


def _probe_ops_direct() -> dict[str, Any]:
    """Direct :8000 models probe — independent of presence / prod_feed."""
    import urllib.request
    import time

    url = (_S.brain_base() + "/models") if _S.brain_base() else ""   # the Connect engine on this machine (2026-10-10)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as r:
            body = json.loads(r.read().decode())
        data = body.get("data") or []
        root = None
        ids = []
        for m in data:
            ids.append(m.get("id"))
            if m.get("id") in ("ops", "qw3.6-35b") or "35" in str(m.get("id") or ""):
                root = m.get("root") or m.get("id")
                break
        if not root and data:
            root = data[0].get("root") or data[0].get("id")
        return {
            "ok": True,
            "model": "ops",
            "root": str(root or "")[:120],
            "ids": ids[:6],
            "latency_ms": int((time.time() - t0) * 1000),
            "url": url,
            "summary": (
                "Ops 35B online (direct :8000). Judgment owner; CODE→SoT is continuous physics."
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "model": "ops",
            "error": str(e)[:120],
            "summary": "Ops 35B unreachable on :8000",
        }


def _fetch_prod_feed() -> dict[str, Any]:
    """ops 35B + SoT prod feed from rail-presence (no shadow oracles)."""
    import urllib.request

    try:
        req = urllib.request.Request(
            _S.url("presence", path="/v1/prod_feed"), method="GET"
        )
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _media_status() -> dict[str, Any]:
    """Media gate + locus for dial (hour from init; default 9 = DD desk)."""
    try:
        from .media_gate import status as media_status

        return media_status()
    except Exception as e:
        return {
            "media_on": False,
            "media_station": 9,
            "phase": "unknown",
            "verdict": "unsure",
            "error": str(e)[:80],
        }


def _media_locus_panel() -> dict[str, Any]:
    try:
        from .media_locus import get_media_locus

        loc = get_media_locus()
        return {
            "mode": loc.get("mode"),
            "internal_block": loc.get("internal_block"),
            "media_station": loc.get("station"),
            "locus_station": loc.get("station"),
            "locus_label": loc.get("label"),
            "contiguous_with_user_seat": loc.get("contiguous_with_user_seat"),
            "user_seat": loc.get("user_seat"),
            "dial_hint": (loc.get("dial") or {}).get("note"),
            "show_mark": (loc.get("dial") or {}).get("show_mark"),
        }
    except Exception:
        return {}


def build_panel_state() -> dict[str, Any]:
    voice = read_state()
    presence = fetch_presence()
    people = []
    for p in presence.get("people") or []:
        if not isinstance(p, dict):
            continue
        people.append(
            {
                "name": p.get("name") or p.get("person_id"),
                "certain": p.get("certain"),
                "slot_id": p.get("slot_id"),
                "station_guess": p.get("seat")
                or (p.get("sources") or {}).get("seat")
                or p.get("station_guess"),
            }
        )
    ear = {}
    try:
        ear = get_ear_loop().status()
    except Exception as e:
        ear = {"error": str(e)[:120]}

    feed = _fetch_prod_feed()
    ops = feed.get("ops") if isinstance(feed, dict) else {}
    le = feed.get("last_event") if isinstance(feed, dict) else {}
    # Prefer prod_feed ops; if presence was restarting, fall back to direct :8000
    if not isinstance(ops, dict) or not ops.get("ok"):
        direct = _probe_ops_direct()
        if direct.get("ok"):
            ops = direct
            ops["via"] = "direct_8000"
        elif not isinstance(ops, dict) or not ops:
            ops = direct
    else:
        ops = dict(ops)
        ops.setdefault("via", "prod_feed")

    return {
        "schema": "house_panel_state/v1",
        "voice_on": is_voice_on(),
        "voice": voice,
        "ear": {
            "open": ear.get("open"),
            "mode": ear.get("mode"),
            "last_text": ear.get("last_text"),
            "last_person_id": ear.get("last_person_id"),
            "engine": os.environ.get("RAIL_STT_ASR_ENGINE") or "audio8-ark",
        },
        "presence": {
            "headcount": presence.get("headcount"),
            "count_trust": presence.get("count_trust"),
            "camera_mode": presence.get("camera_mode"),
            "people": people,
            "error": presence.get("error"),
        },
        # Prod judgment feed (ops 35B) — not gemma/cosmos shadows
        "feed": {
            "ops_ok": bool(ops.get("ok")) if isinstance(ops, dict) else False,
            "ops_model": (ops or {}).get("model") or "ops",
            "ops_root": (ops or {}).get("root"),
            "ops_summary": (ops or {}).get("summary") or (ops or {}).get("note"),
            "ops_latency_ms": (ops or {}).get("latency_ms"),
            "ops_via": (ops or {}).get("via"),
            "last_event_kind": (le or {}).get("kind") if isinstance(le, dict) else None,
            "last_event_source": (le or {}).get("source") if isinstance(le, dict) else None,
            "shadows": "retired",
            "feed_url": _S.url("presence", path="/oracle"),
            "prod_feed_url": _S.url("presence", path="/v1/prod_feed"),
            "prod_feed_error": feed.get("error") if isinstance(feed, dict) else None,
        },
        "camera_aim": "door",  # rest default; optional enrich later
        "banks": _bank_status(),
        "lanes": _lane_health(),
        "house_id_root": str(house_id_root()),
        "coach_url": _S.url("enroll", path="/"),
        "work_url": os.environ.get("RAIL_STT_WORK_URL", ""),
        "omni_url": _S.url("stt", path="/omni"),
        "display": display_status(),
        "dial": {
            "note": (
                "Room clock: sit seats 8/10/11/12/1/2/3; wall 4–7; "
                "9 = media locus (DD desk speakers — not a sit seat)"
            ),
            "stations": {
                "8": "the user's usual seat",
                "10": "a second seat",
                "12": "door / ingress",
                "9": "media / DD desk sound (blanked sit)",
            },
            "media_station": 9,
            "legal_sit": [8, 10, 11, 12, 1, 2, 3],
            "wall": [4, 5, 6, 7],
            "occupants": [
                {
                    "name": p.get("name"),
                    "station_guess": p.get("station_guess") or p.get("seat"),
                    "certain": p.get("certain"),
                }
                for p in people
            ],
        },
        "media": {**_media_status(), **_media_locus_panel()},
    }
