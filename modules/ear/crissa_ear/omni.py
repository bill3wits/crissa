"""Omni feed for Crissa display — STT turns + presence + inspector IDs.

HTTP-only aggregation for the Brave omni-log rail (file:// or :8190/omni).
Never invents headcount; presence SoT + STT stamps + inspector shadow verdict.
"""

from __future__ import annotations

from . import settings as _S  # this install's addresses (the shared port table, 2026-10-10)

import json
import os
import urllib.request
from typing import Any

from .presence import fetch_presence
from .turn_log import get_turn_log

INSPECTOR_URL = os.environ.get(
    "RAIL_INSPECTOR_URL", _S.url("presence", path="/v1/inspector/status")
)
INSPECTOR_TIMEOUT = float(os.environ.get("RAIL_INSPECTOR_TIMEOUT", "1.5"))


def fetch_inspector(timeout: float | None = None) -> dict[str, Any]:
    t = INSPECTOR_TIMEOUT if timeout is None else timeout
    try:
        with urllib.request.urlopen(INSPECTOR_URL, timeout=t) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160], "service": "inspector"}


def _thin_stamp(st: dict[str, Any] | None) -> dict[str, Any] | None:
    if not st:
        return None
    return {
        "person_id": st.get("person_id"),
        "source": st.get("source"),
        "confidence": st.get("confidence"),
        "certain": st.get("certain"),
        "id_status": st.get("id_status"),
        "display_name": st.get("display_name"),
        "roster": st.get("roster") or [],
        "voice_emb_sim": st.get("voice_emb_sim"),
    }


def _thin_turn(u: dict[str, Any]) -> dict[str, Any]:
    st = u.get("stamp") if isinstance(u.get("stamp"), dict) else {}
    pid = u.get("person_id") or st.get("person_id")
    return {
        "ts": u.get("ts"),
        "ts_local": u.get("ts_local"),
        "via": u.get("via"),
        "text": (u.get("text") or "")[:240],
        "error": u.get("error"),
        "person_id": pid,
        "stamp": _thin_stamp(st if isinstance(st, dict) else None),
        "engine": u.get("engine"),
        "decode_ms": u.get("decode_ms"),
    }


def _thin_inspector(ins: dict[str, Any]) -> dict[str, Any]:
    lv = ins.get("last_verdict") if isinstance(ins.get("last_verdict"), dict) else {}
    verd = lv.get("verdict") if isinstance(lv.get("verdict"), dict) else {}
    details = verd.get("details") if isinstance(verd.get("details"), dict) else {}
    last = ins.get("last") if isinstance(ins.get("last"), dict) else {}
    live = ins.get("last_live_snap") if isinstance(ins.get("last_live_snap"), dict) else {}
    return {
        "ok": ins.get("ok"),
        "shadow": ins.get("shadow"),
        "write_emb": ins.get("write_emb"),
        "person_id": last.get("person_id") or verd.get("person_id"),
        "stamp_level": last.get("stamp_level") or verd.get("stamp_level"),
        "stamp_confidence": verd.get("stamp_confidence"),
        "best_sim": last.get("best_sim") or verd.get("best_sim"),
        "margin": verd.get("margin"),
        "reason_codes": (last.get("reason_codes") or verd.get("reason_codes") or [])[:12],
        "roster": lv.get("roster") or [],
        "media_flag": lv.get("media_flag"),
        "store_allowed": last.get("store_allowed")
        if last.get("store_allowed") is not None
        else verd.get("store_allowed"),
        "voice_suggest": last.get("voice_suggest") or details.get("voice_suggest"),
        "voice_sim": last.get("voice_sim") or details.get("voice_sim"),
        "source": last.get("source") or live.get("source") or lv.get("source"),
        "n_faces": last.get("n_faces") or live.get("n_faces"),
        "ts_local": last.get("ts_local") or lv.get("ts_local") or live.get("ts_local"),
        "counts": ins.get("counts"),
        "error": ins.get("error") or live.get("error"),
        "ops_hint": ins.get("ops_hint"),
    }


def _thin_presence(p: dict[str, Any]) -> dict[str, Any]:
    people = []
    for x in p.get("people") or []:
        if not isinstance(x, dict):
            continue
        people.append(
            {
                "name": x.get("name"),
                "slot_id": x.get("slot_id"),
                "certain": x.get("certain"),
                "gender": x.get("gender") or (x.get("sources") or {}).get("gender"),
                "face_sim": (x.get("sources") or {}).get("face_sim"),
                "state": (x.get("sources") or {}).get("state"),
            }
        )
    le = p.get("last_event") if isinstance(p.get("last_event"), dict) else {}
    return {
        "headcount": p.get("headcount"),
        "count_trust": p.get("count_trust"),
        "count_certain": p.get("count_certain"),
        "camera_mode": p.get("camera_mode"),
        "camera_blind": p.get("camera_blind"),
        "people": people,
        "last_event_kind": le.get("kind"),
        "error": p.get("error"),
    }


def _http_ok(url: str, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= int(getattr(r, "status", 200) or 200) < 500
    except Exception:
        return False


# Idle door holds are not "oracle signaling" — only active door/oracle cues.
_ORACLE_IDLE_KINDS = frozenset(
    {
        "",
        "none",
        "door_hold",
        "hold",
        "idle",
        "null",
    }
)


def _oracle_signaling(presence: dict[str, Any]) -> tuple[bool, str | None]:
    """True when door/oracle is actively signaling (not quiet hold)."""
    le = presence.get("last_event") if isinstance(presence.get("last_event"), dict) else {}
    kind = str(le.get("kind") or "").strip().lower()
    mode = str(presence.get("camera_mode") or "").strip().lower()
    names = le.get("door_names") or presence.get("door_names") or []
    best = le.get("best_sim") if isinstance(le.get("best_sim"), dict) else {}
    new_ev = le.get("new_events") or []
    if names:
        return True, kind or "door_names"
    if best:
        return True, kind or "face_sim"
    if new_ev:
        return True, kind or "new_events"
    if mode in ("door", "sweep", "sweeping", "surprise", "peek"):
        return True, mode
    if kind and kind not in _ORACLE_IDLE_KINDS:
        return True, kind
    return False, kind or None


def build_flags(
    *,
    ear: dict[str, Any],
    presence: dict[str, Any],
    inspector: dict[str, Any],
) -> dict[str, Any]:
    """Binary on/off house flags for omni strip + log.

    Services (ear/presence/door/brain/mouth/insp): on = healthy / available.
    Signals (oracle): on = actively signaling; off when quiet hold — strip stays clean.
    """
    ear_on = bool(ear.get("open")) and not ear.get("error")
    presence_on = (
        presence.get("error") is None
        and presence.get("headcount") is not None
        and not presence.get("camera_blind")
    )
    # this install's camera, engine and mouth (2026-10-10; the house's were 8192, 8000 and 7789); no camera = off
    door_on = False if _S.camera_off() else _http_ok(
        os.environ.get("RAIL_CAMERA_HEALTH") or _S.camera("/health")
    )
    brain_on = _http_ok(
        os.environ.get("RAIL_BRAIN_HEALTH") or ((_S.brain_base() + "/models") if _S.brain_base() else "")
    )
    mouth_on = _http_ok(
        os.environ.get("RAIL_MOUTH_HEALTH") or _S.url("player", path="/v1/health")
    )
    # Inspector: down only when fetch failed (ok=false + error)
    insp_on = not (
        inspector.get("ok") is False and inspector.get("error")
    )
    verd = {}
    lv = inspector.get("last_verdict") if isinstance(inspector.get("last_verdict"), dict) else {}
    if isinstance(lv.get("verdict"), dict):
        verd = lv["verdict"]

    oracle_on, oracle_detail = _oracle_signaling(presence)

    voice_on = False
    try:
        from .house_voice import is_voice_on

        voice_on = bool(is_voice_on())
    except Exception:
        voice_on = False

    elements = {
        "ear": {
            "on": ear_on,
            "kind": "service",
            "detail": None
            if ear_on
            else (ear.get("closed_reason") or ear.get("error") or "closed"),
        },
        "presence": {
            "on": presence_on,
            "kind": "service",
            "detail": presence.get("count_trust") or presence.get("camera_mode"),
        },
        "door": {"on": door_on, "kind": "service", "detail": None},
        "brain": {"on": brain_on, "kind": "service", "detail": None},
        "mouth": {"on": mouth_on, "kind": "service", "detail": None},
        "insp": {
            "on": insp_on,
            "kind": "service",
            "detail": verd.get("stamp_level"),
        },
        "oracle": {
            "on": oracle_on,
            "kind": "signal",
            "detail": oracle_detail,
        },
        # Master free-air arm — signal when ON so strip shows VOICE LIVE
        "voice": {
            "on": voice_on,
            "kind": "signal",
            "detail": "LIVE" if voice_on else "locked",
        },
    }
    # Services that are OFF (bad) — strip shows these
    off = [k for k, v in elements.items() if v["kind"] == "service" and not v["on"]]
    # Signals that are ON (interesting) — strip shows these only when active
    signals_on = [k for k, v in elements.items() if v["kind"] == "signal" and v["on"]]
    return {
        "elements": elements,
        "off": off,
        "signals_on": signals_on,
        # true when strip should stay quiet (nothing off, no active signals)
        "quiet": not off and not signals_on,
    }


def build_omni(*, n_turns: int = 30) -> dict[str, Any]:
    """Single payload for omni-log UI."""
    turns_raw = get_turn_log().list(n=max(1, min(100, n_turns)))
    presence = fetch_presence()
    inspector = fetch_inspector()
    try:
        from .ear_loop import get_ear_loop

        ear = get_ear_loop().status()
    except Exception as e:
        ear = {"open": False, "error": str(e)[:80]}
    flags = build_flags(ear=ear, presence=presence, inspector=inspector)
    return {
        "ok": True,
        "schema": "house_omni/v1",
        "presence": _thin_presence(presence),
        "inspector": _thin_inspector(inspector),
        "ear": {
            "open": bool(ear.get("open")),
            "speech_chunks": ear.get("speech_chunks"),
            "last_text": (ear.get("last_text") or "")[:120] or None,
            "last_person_id": ear.get("last_person_id"),
            "closed_reason": ear.get("closed_reason"),
            "error": ear.get("error") or ear.get("last_error"),
        },
        "flags": flags,
        "turns": [_thin_turn(u) for u in turns_raw],
        "counts": {
            "turns": len(turns_raw),
            "voice_ids": sorted(
                {
                    t.get("person_id")
                    for t in turns_raw
                    if t.get("person_id")
                }
            ),
            "ring_len": len(get_turn_log()),
        },
    }
