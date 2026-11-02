"""HTTP client for rail-presence — ears read RoomPresence, never re-count bodies.

Law: rails talk HTTP only. No Crissa. No import of rail_presence.
"""

from __future__ import annotations

from .settings import url as _url  # this install's addresses (the shared port table, 2026-10-10)

import json
import os
import urllib.request
from typing import Any

PRESENCE_URL = os.environ.get(
    "RAIL_PRESENCE_URL", _url("presence", path="/v1/presence")
)
PRESENCE_TIMEOUT = float(os.environ.get("RAIL_PRESENCE_TIMEOUT", "1.5"))
SURPRISE_URL = os.environ.get(
    "RAIL_PRESENCE_SURPRISE_URL", _url("presence", path="/v1/surprise")
)
# Fire surprise when speech + empty room (0 disables)
SURPRISE_ON_EMPTY = os.environ.get("RAIL_STT_SURPRISE_ON_EMPTY", "1") not in (
    "0",
    "false",
    "no",
)
INSTRUCT_URL = os.environ.get(
    "RAIL_PRESENCE_INSTRUCT_URL",
    PRESENCE_URL.replace("/v1/presence", "/v1/instruct"),
)
INSTRUCT_TIMEOUT = float(os.environ.get("RAIL_PRESENCE_INSTRUCT_TIMEOUT", "1.0"))
# Per-utterance voice stamp → presence user_auth (0 disables)
POST_VOICE_STAMP = os.environ.get("RAIL_STT_POST_VOICE_STAMP", "1") not in (
    "0",
    "false",
    "no",
)


def fetch_presence(url: str | None = None, timeout: float | None = None) -> dict[str, Any]:
    """GET RoomPresence. On failure return a soft empty snapshot (fail open for STT)."""
    u = url or PRESENCE_URL
    t = PRESENCE_TIMEOUT if timeout is None else timeout
    try:
        with urllib.request.urlopen(u, timeout=t) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:200],
            "headcount": None,
            "people": [],
            "camera_mode": None,
            "count_trust": None,
        }


F2F_URL = os.environ.get(
    "RAIL_PRESENCE_F2F_URL", _url("presence", path="/v1/f2f")
)


def request_f2f(
    *,
    person_id: str | None = None,
    source: str = "stt_voice",
    timeout: float = 1.5,
    look: bool = True,
    hour: int | None = None,
) -> dict[str, Any]:
    """POST presence look. Impulse/stt_voice is behind ear VAD, not puck VAD."""
    body: dict[str, Any] = {
        "on": True,
        "look": bool(look),
        "reason": source,
        "person_id": person_id,
    }
    if hour is not None:
        try:
            body["hour"] = int(hour)
        except (TypeError, ValueError):
            pass
    body_bytes = json.dumps(body).encode()
    try:
        req = urllib.request.Request(
            F2F_URL,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def request_surprise(
    *,
    person_id: str | None = None,
    source: str = "voice_while_empty",
    timeout: float = 2.0,
    mode: str = "body_count",
    force: bool = False,
    media: bool = False,
) -> dict[str, Any]:
    """POST presence surprise. Modes: body_count | user_auth (async on presence)."""
    if not SURPRISE_ON_EMPTY:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if media:
        return {"ok": True, "skipped": "media"}
    body = json.dumps(
        {
            "person_id": person_id,
            "source": source,
            "force": bool(force),
            "mode": mode or "body_count",
            "media": False,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            SURPRISE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Short timeout: we nudge; full sweep can finish async on presence side.
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def post_speaker_voice(
    person_id: str | None,
    sim: float,
    *,
    match: bool = False,
    source: str = "voice_id",
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST ranked voice ID to the Speaker stack. Does not wait for the face check."""
    body = json.dumps(
        {
            "op": "speaker.voice",
            "person_id": person_id,
            "voice_emb_sim": sim,
            "match": bool(match),
            "source": source,
        }
    ).encode()
    t = INSTRUCT_TIMEOUT if timeout is None else timeout
    try:
        req = urllib.request.Request(
            INSTRUCT_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def post_user_voice(
    person_id: str,
    sim: float,
    *,
    source: str = "crissa_ear",
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST {op: user.voice} so presence can close user_auth on voice.

    Presence derives match (person_id == user AND sim >= HOUSE_USER_VOICE_MIN_SIM)
    and ages the stamp out after 30 s — hence per-utterance, fail open."""
    if not POST_VOICE_STAMP:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    body = json.dumps(
        {
            "op": "user.voice",
            "person_id": person_id,
            "voice_emb_sim": sim,
            "source": source,
        }
    ).encode()
    t = INSTRUCT_TIMEOUT if timeout is None else timeout
    try:
        req = urllib.request.Request(
            INSTRUCT_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def post_sound_heard(
    az_deg: float | None = None,
    *,
    conf: float | None = None,
    person_id: str | None = None,
    speech: bool | None = None,
    source: str = "xvf3800",
    id_source: str | None = None,
    certain: bool | None = None,
    voice_emb_sim: float | None = None,
    ts: float | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST {op: sound.heard} — mic-frame only. Fail open. Never last_event."""
    if os.environ.get("RAIL_STT_POST_SOUND", "1") in ("0", "false", "no", "off"):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    body: dict[str, Any] = {
        "op": "sound.heard",
        "source": source,
        "frame": "mic",
    }
    if az_deg is not None:
        body["az_deg"] = az_deg
    if conf is not None:
        body["conf"] = conf
        body["beam_conf"] = conf
    if person_id:
        body["person_id"] = person_id
    if id_source:
        body["id_source"] = id_source
    if certain is not None:
        body["certain"] = bool(certain)
    if voice_emb_sim is not None:
        body["voice_emb_sim"] = voice_emb_sim
    if speech is not None:
        body["speech"] = bool(speech)
    if ts is not None:
        body["ts"] = ts
    t = INSTRUCT_TIMEOUT if timeout is None else timeout
    try:
        req = urllib.request.Request(
            INSTRUCT_URL,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=t) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def roster_names(presence: dict[str, Any]) -> list[str]:
    """Named people currently on the inventory (lowercase ids)."""
    out: list[str] = []
    for p in presence.get("people") or []:
        n = p.get("name") if isinstance(p, dict) else None
        if n:
            out.append(str(n).lower().strip())
    return out


def snapshot(presence: dict[str, Any]) -> dict[str, Any]:
    """Minimal facts for LLM / stamp — not the full event dump."""
    people = []
    for p in presence.get("people") or []:
        if not isinstance(p, dict):
            continue
        people.append(
            {
                "slot_id": p.get("slot_id"),
                "name": p.get("name"),
                "certain": p.get("certain"),
                "gender": p.get("gender"),  # soft M/F for closed-room join
            }
        )
    return {
        "headcount": presence.get("headcount"),
        "count_certain": presence.get("count_certain"),
        "count_trust": presence.get("count_trust"),
        "camera_mode": presence.get("camera_mode"),
        "people": people,
        "door_names": presence.get("door_names") or [],
        "visitor_today": presence.get("visitor_today") or [],
        "ts": presence.get("ts"),
        "error": presence.get("error"),
    }
