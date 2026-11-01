"""Inspector — ID stamp + store gate on presence SoT (shadow-first).

Design: docs/designs/design-inspector-emb-safety.md
Pure stack: lib/inspector_gate (zero Crissa).

HTTP (via server.py):
  GET  /v1/inspector/status
  POST /v1/inspector  {op: verdict|stamp|store|status, ...}

Never invents headcount. Default: no production emb writes (shadow log only).
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Pure gate lives in the Wall repo (not a pip package).
_GATE_LIB = _paths.PACK / "lib"
if _GATE_LIB.is_dir() and str(_GATE_LIB) not in sys.path:
    sys.path.insert(0, str(_GATE_LIB))

from .inspector_gate import (  # noqa: E402  the package's own copy (a pack ships no lib/inspector_gate; the User 2026-10-10, .101)
    GateConfig,
    PersonBank,
    RosterPerson,
    Verdict,
    assign_closed_population,
    evaluate_verdict,
    join_hear,
    validate_emb_record,
)
from .inspector_gate.hear import DOOR_FOV_BEAMS  # noqa: E402

from .enrollments import house_id_people, load_enrolled, load_person_stamps

# --- knobs (one block) ---
THROTTLE_SEC = float(os.environ.get("RAIL_INSPECTOR_THROTTLE_SEC", "60"))
SHADOW = os.environ.get("RAIL_INSPECTOR_SHADOW", "1") not in ("0", "false", "no")
LOG_DIR = Path(os.environ.get("RAIL_INSPECTOR_LOG", str(_paths.DATA / "inspector")))
WRITE_EMB = os.environ.get("RAIL_INSPECTOR_WRITE_EMB", "0") in ("1", "true", "yes")
CAMERA_SNAPSHOT = os.environ.get(
    "RAIL_PRESENCE_SNAPSHOT_URL", _ports.camera("/snapshot")
)
OMNI_URL = os.environ.get("RAIL_OMNI_URL", _ports.url("stt", "/v1/omni"))
LIVE_SNAP_THROTTLE = float(os.environ.get("RAIL_INSPECTOR_LIVE_THROTTLE_SEC", "8"))
# G2: door enter / re_id → auto live_snap (shadow). Off with RAIL_INSPECTOR_AUTO_DOOR=0
AUTO_DOOR = os.environ.get("RAIL_INSPECTOR_AUTO_DOOR", "1") not in ("0", "false", "no")
AUTO_DOOR_THROTTLE = float(os.environ.get("RAIL_INSPECTOR_AUTO_THROTTLE_SEC", "45"))
AUTO_CPU_LLM = os.environ.get("RAIL_INSPECTOR_AUTO_CPU_LLM", "0") in ("1", "true", "yes")
# Camera glance at the heard beam. Default OFF — door rest is law.
BEAM_PEEK = os.environ.get("RAIL_INSPECTOR_BEAM_PEEK", "0") in ("1", "true", "yes")
CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
CAMERA_STOPS = Path(
    os.environ.get(
        "RAIL_CAMERA_STOPS",
        str(_paths.DATA / "camera-stops.json"),
    )
)
SITE_JSON = Path(
    os.environ.get("HOUSE_ID_ROOT", str(_paths.IDENTITY))
).expanduser() / "site.json"


@dataclass
class InspectorState:
    last_stamp_ts: float = 0.0
    last_verdict: dict[str, Any] | None = None
    last_stamp: dict[str, Any] | None = None
    last_live_snap: dict[str, Any] | None = None
    last_live_ts: float = 0.0
    last_auto_ts: float = 0.0
    last_auto: dict[str, Any] | None = None
    n_verdict: int = 0
    n_stamp: int = 0
    n_live_snap: int = 0
    n_auto: int = 0
    n_auto_throttled: int = 0
    n_store_blocked: int = 0
    n_throttled: int = 0
    n_hear: int = 0
    last_hear: dict[str, Any] | None = None
    shadow: bool = SHADOW


_STATE = InspectorState()
_auto_lock = None  # set lazily (threading)


def _ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _append_jsonl(name: str, row: dict) -> str | None:
    try:
        d = _ensure_log_dir()
        p = d / name
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return str(p)
    except OSError:
        return None


def _banks_and_stamps() -> tuple[list[PersonBank], dict[str, dict]]:
    stamps = load_person_stamps()
    enrolled = load_enrolled()
    banks: list[PersonBank] = []
    for pid, embs in enrolled.items():
        g = (stamps.get(pid) or {}).get("gender")
        vecs: list[list[float]] = []
        for e in embs:
            try:
                vecs.append([float(x) for x in e.tolist()])
            except Exception:
                try:
                    vecs.append([float(x) for x in e])
                except Exception:
                    continue
        if vecs:
            banks.append(PersonBank(pid, g if g in ("M", "F") else None, vecs))
    return banks, stamps


def roster_from_presence(presence: dict) -> list[RosterPerson]:
    """Closed population = who is on the SoT right now (named slots only)."""
    people = presence.get("people") or []
    out: list[RosterPerson] = []
    for p in people:
        if not isinstance(p, dict):
            continue
        pid = (p.get("name") or p.get("person_id") or p.get("display") or "").strip().lower()
        if not pid or pid.startswith("unknown") or pid.startswith("stranger"):
            continue
        g = p.get("gender")
        if g not in ("M", "F"):
            g = None
        out.append(RosterPerson(pid, g, certain=bool(p.get("certain"))))
    return out


def status(presence: dict | None = None) -> dict[str, Any]:
    banks, stamps = _banks_and_stamps()
    now = time.time()
    cool = max(0.0, THROTTLE_SEC - (now - _STATE.last_stamp_ts))
    roster = roster_from_presence(presence or {})
    staged = 0
    people = house_id_people()
    if people.is_dir():
        for p in people.iterdir():
            if p.is_dir() and (p / "face" / "staged").is_dir():
                staged += len(list((p / "face" / "staged").glob("*.json")))
    lv = _STATE.last_verdict if isinstance(_STATE.last_verdict, dict) else {}
    verd = lv.get("verdict") if isinstance(lv.get("verdict"), dict) else {}
    live = _STATE.last_live_snap if isinstance(_STATE.last_live_snap, dict) else {}
    return {
        "ok": True,
        "service": "inspector",
        "shadow": _STATE.shadow or not WRITE_EMB,
        "write_emb": bool(WRITE_EMB and not _STATE.shadow),
        "throttle_sec": THROTTLE_SEC,
        "throttle_remaining_sec": round(cool, 2),
        "last_stamp_ts": _STATE.last_stamp_ts or None,
        "last_verdict": _STATE.last_verdict,
        "last_stamp": _STATE.last_stamp,
        "last_hear": _STATE.last_hear,
        "last_live_snap": _STATE.last_live_snap,
        # thin surface for omni / ops one-liner
        "last": {
            "person_id": verd.get("person_id"),
            "stamp_level": verd.get("stamp_level"),
            "best_sim": verd.get("best_sim"),
            "store_allowed": verd.get("store_allowed"),
            "voice_suggest": (verd.get("details") or {}).get("voice_suggest")
            if isinstance(verd.get("details"), dict)
            else None,
            "voice_sim": (verd.get("details") or {}).get("voice_sim")
            if isinstance(verd.get("details"), dict)
            else None,
            "source": live.get("source") or lv.get("source"),
            "trigger": live.get("trigger")
            or (_STATE.last_auto or {}).get("trigger"),
            "room_event_kind": (live.get("room_event") or {}).get("kind")
            if isinstance(live.get("room_event"), dict)
            else None,
            "n_faces": live.get("n_faces"),
            "ts_local": lv.get("ts_local") or live.get("ts_local"),
            "reason_codes": (verd.get("reason_codes") or [])[:8],
        },
        "counts": {
            "verdict": _STATE.n_verdict,
            "stamp": _STATE.n_stamp,
            "live_snap": _STATE.n_live_snap,
            "auto": _STATE.n_auto,
            "auto_throttled": _STATE.n_auto_throttled,
            "store_blocked": _STATE.n_store_blocked,
            "throttled": _STATE.n_throttled,
        },
        "auto_door": {
            "enabled": AUTO_DOOR,
            "throttle_sec": AUTO_DOOR_THROTTLE,
            "throttle_remaining_sec": round(
                max(0.0, AUTO_DOOR_THROTTLE - (now - _STATE.last_auto_ts)), 2
            ),
            "last": _STATE.last_auto,
            "cpu_llm": AUTO_CPU_LLM,
        },
        "bank_people": len(banks),
        "bank_ids": sorted(b.person_id for b in banks),
        "roster": [r.person_id for r in roster],
        "staged_files": staged,
        "house_id_people": str(people),
        "log_dir": str(LOG_DIR),
        "gate": GateConfig().__dict__,
        "ops_hint": (
            "Auto on enter/re_id (shadow). Manual: POST {\"op\":\"live_snap\"}. "
            "Hear join: POST {\"op\":\"hear\"} (+ peek:true to glance the beam, then door). "
            "write_emb off. GET /v1/room_events/last links door physics."
        ),
    }


def _as_emb(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("encoding") or raw.get("emb") or raw.get("vector")
    try:
        v = [float(x) for x in raw]
    except Exception:
        return None
    if len(v) < 8:
        return None
    return v


def verdict(
    *,
    emb: Any,
    presence: dict,
    soft_gender: str | None = None,
    media_flag: bool = False,
    det_score: float | None = None,
    voice_suggest: str | None = None,
    voice_sim: float | None = None,
    operator_person_id: str | None = None,
    person_id_hint: str | None = None,
) -> dict[str, Any]:
    """Dry-run assurance stack — never writes SoT or identity-store."""
    banks, _stamps = _banks_and_stamps()
    roster = roster_from_presence(presence)
    # If room empty of names, allow full bank as enroll-session-style closed set
    # only when operator explicitly sets person_id_hint (enroll path).
    if not roster and person_id_hint:
        pid = person_id_hint.strip().lower()
        g = next((b.gender for b in banks if b.person_id == pid), None)
        roster = [RosterPerson(pid, g, certain=False)]

    emb_v = _as_emb(emb)
    if emb_v is None:
        return {"ok": False, "error": "emb required (list[float] or {encoding:[...]})"}

    count_certain = bool(
        presence.get("count_certain")
        if "count_certain" in presence
        else presence.get("count_trust") == "fact"
    )
    sole = len(roster) == 1 and int(presence.get("headcount") or 0) == 1

    val = validate_emb_record(
        {
            "person_id": person_id_hint or "probe",
            "modality": "face",
            "model": "faceid",
            "dim": len(emb_v),
            "encoding": emb_v,
            "source": "inspector_verdict",
            "det_score": det_score,
            "ts": time.time(),
        },
        accept_legacy=True,
    )

    v = evaluate_verdict(
        emb_v,
        roster=roster,
        banks=banks,
        soft_gender=soft_gender,
        media_flag=bool(media_flag),
        count_certain=count_certain,
        sole_present=sole,
        voice_suggest=voice_suggest,
        voice_sim=voice_sim,
        det_score=det_score,
        operator_person_id=operator_person_id,
        validation=val,
    )
    out = {
        "ok": True,
        "op": "verdict",
        "shadow": True,
        "roster": [r.person_id for r in roster],
        "count_certain": count_certain,
        "media_flag": bool(media_flag),
        "verdict": v.as_dict(),
        "validation_ok": val.ok,
        "validation_errors": val.errors if not val.ok else [],
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _STATE.n_verdict += 1
    _STATE.last_verdict = out
    _append_jsonl("verdicts.jsonl", out)
    return out


def store(
    *,
    person_id: str,
    emb: Any,
    presence: dict,
    meta: dict | None = None,
    soft_gender: str | None = None,
    media_flag: bool = False,
    det_score: float | None = None,
) -> dict[str, Any]:
    """Write-gate path. Shadow by default — logs only, never touches identity-store active."""
    meta = meta or {}
    emb_v = _as_emb(emb)
    if emb_v is None:
        return {"ok": False, "error": "emb required"}
    pid = (person_id or "").strip().lower()
    if not pid:
        return {"ok": False, "error": "person_id required"}

    v_out = verdict(
        emb=emb_v,
        presence=presence,
        soft_gender=soft_gender,
        media_flag=media_flag,
        det_score=det_score,
        person_id_hint=pid,
    )
    if not v_out.get("ok"):
        return v_out
    vd = v_out["verdict"]
    allowed = bool(vd.get("store_allowed")) and vd.get("person_id") == pid

    row = {
        "ok": True,
        "op": "store",
        "person_id": pid,
        "store_allowed": allowed,
        "shadow": _STATE.shadow or not WRITE_EMB,
        "wrote": False,
        "verdict": vd,
        "meta": meta,
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "shadow — no identity-store write" if (_STATE.shadow or not WRITE_EMB) else None,
    }
    if not allowed:
        _STATE.n_store_blocked += 1
        row["ok"] = True
        row["error"] = "store_blocked_by_gate"
    elif WRITE_EMB and not _STATE.shadow:
        # Production path reserved — not enabled for first wire.
        row["error"] = "write_path_not_enabled_yet"
        row["wrote"] = False
        _STATE.n_store_blocked += 1
    _append_jsonl("store_shadow.jsonl", row)
    return row


def stamp(
    *,
    presence: dict,
    embs: list[Any] | None = None,
    soft_genders: list[str | None] | None = None,
    media_flag: bool = False,
    force: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """Snap-style stamp using provided face embs (test path) or refuse if none.

    Full camera lease + facefinder snap lands next; this wire is testable without
    thrashing PTZ. apply=True would write name/certain on slots — disabled in shadow.
    """
    now = time.time()
    if not force and (now - _STATE.last_stamp_ts) < THROTTLE_SEC:
        _STATE.n_throttled += 1
        return {
            "ok": True,
            "op": "stamp",
            "throttled": True,
            "throttle_remaining_sec": round(THROTTLE_SEC - (now - _STATE.last_stamp_ts), 2),
            "last_stamp": _STATE.last_stamp,
        }

    banks, stamps = _banks_and_stamps()
    roster = roster_from_presence(presence)
    if not embs:
        return {
            "ok": False,
            "op": "stamp",
            "error": "embs required until facefinder lease snap is wired "
            "(pass embs:[[...],...] for test; or op=verdict with one emb)",
            "roster": [r.person_id for r in roster],
            "hint": "POST {op:verdict, emb:[512 floats], soft_gender:M|F}",
        }

    face_embs: list[list[float]] = []
    for e in embs:
        v = _as_emb(e)
        if v is not None:
            face_embs.append(v)
    if not face_embs:
        return {"ok": False, "error": "no valid embs"}

    sgs = list(soft_genders or [])
    while len(sgs) < len(face_embs):
        sgs.append(None)

    count_certain = bool(
        presence.get("count_certain")
        if "count_certain" in presence
        else presence.get("count_trust") == "fact"
    )
    # crowd / multi-face via closed assign
    if len(face_embs) > 1:
        verdicts = assign_closed_population(face_embs, sgs, banks, roster)
    else:
        verdicts = [
            evaluate_verdict(
                face_embs[0],
                roster=roster,
                banks=banks,
                soft_gender=sgs[0],
                media_flag=bool(media_flag),
                count_certain=count_certain,
                sole_present=len(roster) == 1,
            )
        ]

    applied: list[dict] = []
    shadow_only = _STATE.shadow or not apply
    out = {
        "ok": True,
        "op": "stamp",
        "throttled": False,
        "shadow": shadow_only,
        "applied_to_sot": False,
        "media_flag": bool(media_flag),
        "count_certain": count_certain,
        "roster": [r.person_id for r in roster],
        "verdicts": [v.as_dict() for v in verdicts],
        "applied": applied,
        "ts": now,
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _STATE.last_stamp_ts = now
    _STATE.n_stamp += 1
    _STATE.last_stamp = out
    _append_jsonl("stamps.jsonl", out)
    return out


def _read_residual_deg() -> float | None:
    raw = (os.environ.get("HOUSE_MIC_AZ_RESIDUAL_DEG") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    if SITE_JSON.is_file():
        try:
            d = json.loads(SITE_JSON.read_text(encoding="utf-8"))
            st = d.get("stations") if isinstance(d.get("stations"), dict) else {}
            v = st.get("mic_az_residual_deg")
            if v is None:
                v = (d.get("mic") or {}).get("az_residual_deg") if isinstance(d.get("mic"), dict) else None
            if v is not None and v != "":
                return float(v)
        except (OSError, TypeError, ValueError):
            return None
    return None


def _load_regulars() -> dict[int, str]:
    from . import seat_slots as _seats

    out: dict[int, str] = dict(_seats.regulars_by_hour())
    if not SITE_JSON.is_file():
        return out
    try:
        d = json.loads(SITE_JSON.read_text(encoding="utf-8"))
        st = d.get("stations") if isinstance(d.get("stations"), dict) else {}
        for row in st.get("regulars") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("person_id") or "").strip().lower()
            try:
                hb = int(row.get("home_beam"))
            except (TypeError, ValueError):
                continue
            if pid:
                out[hb] = pid
    except (OSError, TypeError, ValueError):
        return out
    return out


def _voice_ranks_from_omni() -> list[tuple[str, float]]:
    """Last voice-ranked turn — omni does not keep the raw emb."""
    try:
        import urllib.request

        url = OMNI_URL if "?" in OMNI_URL else f"{OMNI_URL}?n=12"
        with urllib.request.urlopen(url, timeout=2.0) as r:
            d = json.loads(r.read().decode())
        ranks: list[tuple[str, float]] = []
        for t in d.get("turns") or []:
            if not isinstance(t, dict):
                continue
            st = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
            pid = st.get("person_id") or t.get("person_id")
            if not pid:
                continue
            try:
                sim = float(st.get("voice_emb_sim"))
            except (TypeError, ValueError):
                continue
            src = str(st.get("source") or "")
            if not (src.startswith("voice") or sim >= 0.25):
                continue
            ranks.append((str(pid).strip().lower(), sim))
            break
        return ranks
    except Exception:
        return []


def _stop_for_beam(hour: int) -> dict[str, Any] | None:
    """Map clock hour → camera-stops.json row. Door FOV hours stay at door."""
    if int(hour) in DOOR_FOV_BEAMS:
        # From camera-stops.json, not a literal — this returned the pre-2026-08-16
        # door and would have aimed a beam peek five degrees off the opening.
        from .seat_shot import door_pose

        _dp = door_pose()
        return {"id": "door", "pan": _dp["pan"], "tilt": _dp["tilt"],
                "station": 12, "door_fov": True}
    if not CAMERA_STOPS.is_file():
        return None
    try:
        data = json.loads(CAMERA_STOPS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for s in data.get("stops") or []:
        if not isinstance(s, dict):
            continue
        try:
            st = int(s["station"]) if s.get("station") is not None else None
        except (TypeError, ValueError):
            st = None
        if st == int(hour):
            return {
                "id": s.get("id"),
                "pan": int(s["pan"]),
                "tilt": int(s.get("tilt") or 0),
                "station": st,
                "door_fov": False,
            }
    return None


def _post_cam(path: str, body: dict, timeout: float = 20.0) -> dict[str, Any]:
    from .camera_call import post as camera_post

    return camera_post(path, body, owner="inspector", timeout=timeout)


def _peek_beam(hour: int) -> dict[str, Any]:
    """One glance at a beam, then door home. Never AUTH. Never last_event."""
    stop = _stop_for_beam(hour)
    if not stop:
        return {"ok": False, "error": f"no_camera_stop_for_beam_{hour}"}
    if stop.get("door_fov"):
        snap = live_snap(force=True, attach_voice=False, trigger="hear_door_fov")
        snap["peek"] = {"mode": "door_fov", "beam": hour, "panned": False}
        return snap
    aimed = False
    try:
        aim = _post_cam(
            "/v1/aim",
            {"pan": stop["pan"], "tilt": stop["tilt"], "hold_sec": 3.5},
            timeout=20.0,
        )
        if not aim.get("ok"):
            return {"ok": False, "error": "aim_failed", "aim": aim, "stop": stop}
        aimed = True
        time.sleep(1.1)
        snap = live_snap(force=True, attach_voice=False, trigger=f"hear_peek_{hour}")
        snap["peek"] = {
            "mode": "aim",
            "beam": hour,
            "panned": True,
            "stop": stop.get("id"),
            "pan": stop["pan"],
            "tilt": stop["tilt"],
        }
        return snap
    except Exception as e:
        return {"ok": False, "error": f"peek_fail:{type(e).__name__}: {e}"[:200], "stop": stop}
    finally:
        if aimed:
            # return_door alone leaves the zoom tight and the facefinder live.
            # Same shared contract as every other camera path: facefinder off,
            # de-zoom, door home. (This path takes no lease, so None.)
            try:
                from . import room_sweep as _rs
                from .seat_shot import go_home

                go_home(_rs, None)
            except Exception:
                pass
            try:
                _post_cam("/v1/return_door", {"force": True, "settle_sec": 1.2}, timeout=15.0)
            except Exception:
                pass


_ENTRY_EVENTS_DIR = Path(
    os.environ.get(
        "RAIL_EVENTS_DIR",
        str(_paths.DATA / "work/oracle_live/gemma_events"),
    )
)


def _user_id_from_presence(presence: dict | None) -> str | None:
    p = presence if isinstance(presence, dict) else {}
    uid = str(p.get("user_id") or "").strip().lower()
    return uid or None


def hear(
    presence: dict,
    *,
    peek: bool | None = None,
    az_deg: float | None = None,
    voice_ranks: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """Join roster × last voice rank × mic beam. Optional camera glance.

    Suggest only. Never writes identity-store, never last_event, never AUTH.
    Peek defaults off (door-hold). Residual unset → beam hour stays unmapped.
    """
    sound = presence.get("sound") if isinstance(presence.get("sound"), dict) else {}
    try:
        az = float(az_deg) if az_deg is not None else float(sound.get("az_deg"))
    except (TypeError, ValueError):
        az = None
    residual = _read_residual_deg()
    roster = roster_from_presence(presence)
    ranks = voice_ranks if voice_ranks is not None else _voice_ranks_from_omni()
    v = join_hear(
        roster=[r.person_id for r in roster],
        voice_ranks=ranks,
        az_deg=az,
        residual_deg=residual,
        regulars=_load_regulars(),
    )
    # Who is this? -- beam x voice x Argus's door face, over a recency-weighted
    # candidate set (the User 2026-08-14: "counts not a thing anymore. whoThis? is
    # it"). Advisory: join_hear above still owns the existing verdict, and voice
    # never auths. This adds the face-and-recency evidence that join_hear has no
    # access to, and says why it landed where it did.
    fusion: dict[str, Any] | None = None
    try:
        from .inspector_gate.speaker_fusion import fuse_speaker, read_entry_evidence

        _regs = _load_regulars()
        fusion = fuse_speaker(
            voice_sims={str(n).lower(): float(sm) for n, sm in (ranks or [])},
            entries=read_entry_evidence(_ENTRY_EVENTS_DIR),
            roster=[r.person_id for r in roster],
            user_id=_user_id_from_presence(presence),
            az_deg=az,
            residual_deg=residual,
            home_hours={pid: h for h, pid in _regs.items()},
        ).as_dict()
    except Exception as e:  # advisory only -- never break the hear verdict
        fusion = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
    target_beam = v.peek_beam
    if target_beam is None and v.person_id:
        regs = _load_regulars()
        for h, pid in regs.items():
            if pid == v.person_id:
                target_beam = h
                break
    if target_beam is None and (
        (v.person_id or "") == "user"
        or (len(roster) == 1 and roster[0].person_id == "user")
    ):
        try:
            from .seat_slots import user_seat

            target_beam = int(user_seat())
        except Exception:
            target_beam = 6
    looking = bool((presence or {}).get("f2f"))
    if peek is False or looking:
        # Session look owns the gimbal. Hear must not peek hour-8 and yank home.
        do_peek = False
    elif peek is True:
        do_peek = target_beam is not None
    else:
        do_peek = BEAM_PEEK and target_beam is not None
    peeked: dict[str, Any] | None = None
    if do_peek and target_beam is not None:
        peeked = _peek_beam(int(target_beam))
    out = {
        "ok": True,
        "op": "hear",
        "shadow": True,
        "suggest": v.as_dict(),
        # "who is this" -- the joined answer, with every channel's contribution
        # shown. Advisory beside `suggest`; voice still never auths.
        "who": fusion,
        "az_deg": az,
        "residual_deg": residual,
        "sound_age_s": (
            round(time.time() - float(sound["ts"]), 2)
            if sound.get("ts")
            else None
        ),
        "peeked": peeked,
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": (
            "beam_hour unmapped until HOUSE_MIC_AZ_RESIDUAL_DEG or "
            "site.json stations.mic_az_residual_deg (clap at 12)"
            if residual is None
            else None
        ),
    }
    _STATE.n_hear += 1
    _STATE.last_hear = {k: out[k] for k in out if k != "peeked"}
    _append_jsonl("hear.jsonl", {k: out[k] for k in out if k != "peeked"})
    try:
        face_id = None
        if isinstance((peeked or {}).get("verdict"), dict):
            face_id = (peeked or {}).get("verdict", {}).get("person_id")
        face_id = face_id or (peeked or {}).get("person_id") or v.person_id
        guests = {"a guest", "a guest", "guest3"}
        kind = "visitor" if (face_id or "").lower() in guests else "hear"
        text = (
            f"visitor today {face_id} (voice label; do not call leave)"
            if kind == "visitor"
            else (
                f"hear suggest={v.person_id} level={v.suggest_level} "
                f"beam={target_beam} peeked={bool(peeked and peeked.get('ok'))}"
            )
        )
        _memory_learn(kind, text, {"person_id": face_id, "beam": target_beam})
    except Exception:
        pass
    return out


def _memory_learn(kind: str, text: str, meta: dict | None = None) -> None:
    """Best-effort day log so Dream/Hindsight can pick room facts up tonight."""
    script = _paths.PACK / "scripts" / "house_memory.py"
    if not script.is_file():
        return
    import subprocess

    cmd = [
        "python3",
        str(script),
        "append",
        "--kind",
        kind,
        "--text",
        (text or "")[:400],
        "--source",
        "inspector",
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)


def room_check(presence: dict, *, peek_regulars: bool = True) -> dict[str, Any]:
    """Compare SoT headcount to bodies seen at door + regular beams.

    Does not write inventory. Drift is a fact for Crissa/Argus, not a second ledger.
    """
    sot_hc = int(presence.get("headcount") or 0)
    roster = [r.person_id for r in roster_from_presence(presence)]
    looks: list[dict[str, Any]] = []
    door = live_snap(presence=presence, force=True, attach_voice=False, trigger="room_check_door")
    looks.append(
        {
            "beam": 12,
            "stop": "door",
            "n_faces": door.get("n_faces"),
            "person_id": (door.get("verdict") or {}).get("person_id")
            if isinstance(door.get("verdict"), dict)
            else door.get("person_id"),
            "ok": door.get("ok"),
        }
    )
    if peek_regulars:
        for hour in (8, 10):
            snap = _peek_beam(hour)
            looks.append(
                {
                    "beam": hour,
                    "stop": (snap.get("peek") or {}).get("stop") if isinstance(snap.get("peek"), dict) else hour,
                    "n_faces": snap.get("n_faces"),
                    "person_id": (snap.get("verdict") or {}).get("person_id")
                    if isinstance(snap.get("verdict"), dict)
                    else snap.get("person_id"),
                    "ok": snap.get("ok"),
                    "error": snap.get("error"),
                }
            )
    seen = sum(int(x.get("n_faces") or 0) for x in looks if x.get("ok"))
    drift = seen != sot_hc
    out = {
        "ok": True,
        "op": "room_check",
        "shadow": True,
        "sot_headcount": sot_hc,
        "sot_roster": roster,
        "faces_seen_sum": seen,
        "drift": drift,
        "looks": looks,
        "note": (
            "faces_seen_sum can double-count if the same body is in two FOVs; "
            "use per-beam n_faces. Does not apply VL / change SoT."
        ),
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _append_jsonl("room_check.jsonl", out)
    try:
        _memory_learn(
            "room_check",
            f"room_check sot={sot_hc} seen_sum={seen} drift={drift} roster={roster}",
            {"sot": sot_hc, "seen": seen, "drift": drift},
        )
    except Exception:
        pass
    return out


def tally_operator_looks(
    looks: list[dict[str, Any]], *, user_hour: int
) -> tuple[list[str], int]:
    """FaceID names + leftover bodies. User desk is never an S#."""
    named: list[str] = []
    unnamed = 0
    for row in looks or []:
        if not isinstance(row, dict):
            continue
        names = [
            str(n).strip().lower()
            for n in (row.get("names") or [])
            if str(n).strip() and str(n).strip().lower() not in ("unknown", "none", "null")
        ]
        try:
            n_face = int(row.get("n_faces") or 0)
        except (TypeError, ValueError):
            n_face = 0
        try:
            hour = int(row["hour"]) if row.get("hour") is not None else None
        except (TypeError, ValueError):
            hour = None
        extra = max(0, n_face - len(names))
        for n in names:
            if n not in named:
                named.append(n)
        if names:
            unnamed += extra
            continue
        if hour == int(user_hour):
            continue
        # Live faces only. Subtract-blob / poster detections are not S#.
        # Measured 2026-08-18: stand_11 n_faces=3 was The Smiths on the door.
        if n_face > 0:
            unnamed += n_face
    return named, unnamed


def apply_operator_scan(
    inv,
    *,
    named: list[str],
    unnamed_want: int,
    uid: str = "user",
) -> dict[str, Any]:
    """Operator recount: User always stays; FaceID names stay; extras are S#."""
    from .guests import is_guest

    uid = str(uid or "user").strip().lower()
    kept: list[str] = [uid] if uid else []
    for n in named or []:
        n = str(n).strip().lower()
        if not n or n in kept or n in ("unknown", "none", "null"):
            continue
        if is_guest(n):
            continue
        kept.append(n)
    present = [
        str(n).strip().lower()
        for n in (inv.names() or [])
        if n and not is_guest(str(n))
    ]
    left: list[str] = []
    for n in present:
        if n not in kept:
            inv.force_leave([n], source="operator_rescan")
            left.append(n)
    want_u = max(0, int(unnamed_want))
    ev = inv.apply_reconcile(kept, want_u, mode="replace", full=True)
    left = sorted(set(left) | {str(x) for x in (ev.get("left") or []) if x})
    while len(getattr(inv, "unnamed_keys", []) or []) < int(inv.unnamed or 0):
        inv._new_unnamed_key(source="operator_rescan")
    return {
        "ok": True,
        "seen": kept,
        "left": left,
        "unnamed": int(inv.unnamed or 0),
        "headcount": int(inv.headcount()),
        "people": list(inv.names() or []),
        "mode": "replace",
    }


def apply_who_facts(
    inv,
    seen: list[str],
    *,
    uid: str = "user",
    confirm_user=None,
) -> dict[str, Any]:
    """Write Inspector names onto the one inventory object.

    the User 2026-08-18: look-only is a foolish law. Faces we named are facts
    and must correct Oracle. Occupants we did not see leave — except the
    User, who goes through seat confirm (a miss at the glass is not a leave).
    """
    from .guests import is_guest

    uid = str(uid or "user").strip().lower()
    seen_n = [str(s).strip().lower() for s in (seen or []) if str(s).strip()]
    seen_n = [s for s in seen_n if s and s not in ("unknown", "none", "null")]
    if seen_n:
        inv.apply_reconcile(seen_n, 0, mode="union", full=False)
    present = [
        str(n).strip().lower()
        for n in (inv.names() or [])
        if n and not is_guest(str(n))
    ]
    left: list[str] = []
    user_confirm: dict[str, Any] | None = None
    for n in present:
        if n in seen_n:
            continue
        if n == uid:
            if callable(confirm_user):
                try:
                    user_confirm = confirm_user(n)
                except Exception as e:
                    user_confirm = {"confirmed": False, "reason": str(e)[:160]}
            else:
                user_confirm = {"confirmed": False, "reason": "no_confirm"}
            if user_confirm.get("confirmed"):
                inv.force_leave([n], source="operator_who_is_here")
                left.append(n)
            continue
        inv.force_leave([n], source="operator_who_is_here")
        left.append(n)
    return {
        "ok": True,
        "seen": seen_n,
        "left": left,
        "headcount": int(inv.headcount()),
        "people": list(inv.names() or []),
        "user_confirm": user_confirm,
    }


def record_who_on_sot(svc, seen: list[str]) -> dict[str, Any]:
    """apply_who_facts + publish the one presence object."""
    from .seat_slots import primary_user

    inv = svc.inventory
    ev = apply_who_facts(
        inv,
        seen,
        uid=primary_user(),
        confirm_user=getattr(svc, "confirm_user_exit", None),
    )
    now = time.time()
    try:
        cur = svc.get()
        st = svc._presence_from_inventory(
            frame_age=getattr(cur, "frame_age_sec", 0.2) or 0.2,
            camera_open=getattr(cur, "camera_open", True),
            camera_blind=getattr(cur, "camera_blind", False),
            last_event={
                "kind": "who_is_here",
                "source": "inspector",
                "seen": ev.get("seen"),
                "left": ev.get("left"),
                "user_confirm": ev.get("user_confirm"),
                "ts": now,
            },
            count_trust="fact",
            camera_mode="holding",
            door_names=[],
        )
        svc._set(st)
        if hasattr(svc, "_save_inventory_disk"):
            svc._save_inventory_disk(force=True)
        ev["headcount"] = int(st.headcount)
        ev["people"] = [
            {"name": p.name or p.slot_id, "seat": p.seat}
            for p in (st.people or [])
        ]
    except Exception as e:
        ev["publish_error"] = f"{type(e).__name__}: {e}"[:160]
    return ev


def who_is_here(presence: dict) -> dict[str, Any]:
    """One operator walk: tell-the-count-is-wrong stills + names.

    Lease the camera for the whole tour so REST OFF DOOR cannot yank home.
    Each stop: FF off, aim, read pan back, wide snap, FF reframe, face snap,
    FF off. Finally door home, then **return the lease**.
    """
    from . import room_sweep as rs
    from .seat_slots import primary_user, user_seat

    t0 = time.time()
    looks: list[dict[str, Any]] = []
    uid = primary_user()
    user_h = int(user_seat())
    if not _WALK_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "op": "who_is_here",
            "error": "already_running",
            "wall_sec": round(time.time() - t0, 3),
            "ts": time.time(),
            "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    try:
        return _who_is_here_walk(
            presence, t0=t0, looks=looks, uid=uid, user_h=user_h, rs=rs
        )
    finally:
        _WALK_LOCK.release()


_WALK_LOCK = threading.Lock()


def _who_is_here_walk(
    presence: dict,
    *,
    t0: float,
    looks: list[dict[str, Any]],
    uid: str,
    user_h: int,
    rs: Any,
) -> dict[str, Any]:
    from .error_peek import _aim_snap
    from .room_heading import door_home_pose
    from .seat_shot import go_home
    from .sweep_pose import error_check_stops

    lease_ttl = float(os.environ.get("RAIL_WHO_LEASE_TTL_SEC", "90"))
    lease = rs._post(
        "/v1/lease",
        {"owner": "who_is_here", "ttl_sec": lease_ttl, "reason": "who_is_here"},
        timeout=8.0,
    )
    if not lease.get("ok"):
        home = go_home(rs, lease.get("lease_id"))
        return {
            "ok": False,
            "op": "who_is_here",
            "error": "camera_leased",
            "lease": {
                k: lease.get(k) for k in ("owner", "remaining_sec", "error") if k in lease
            },
            "door": {"ok": bool(home.get("home")), "home": home},
            "lease_released": False,
            "wall_sec": round(time.time() - t0, 3),
            "ts": time.time(),
            "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    lid = lease.get("lease_id")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    last_dir = Path(
        os.environ.get(
            "RAIL_ERROR_CHECK_LAST",
            str(_paths.DATA / "body_audit_last"),
        )
    )
    scan_dir = last_dir / f"rescan_{stamp}"
    try:
        scan_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        scan_dir = last_dir
    home: dict[str, Any] = {}
    lease_released = False
    try:
        for i, st in enumerate(error_check_stops()):
            name = str(st["id"])
            pan = int(st["pan"])
            tilt = int(st["tilt"])
            row: dict[str, Any] = {
                "stop": name,
                "pan": pan,
                "tilt": tilt,
                "hour": st.get("hour"),
            }
            try:
                snap = _aim_snap(
                    name,
                    pan,
                    settle=1.0,
                    tilt=tilt,
                    zoom=100,
                    face_zoom=True,
                    lease_id=lid,
                )
                row["aim_ok"] = bool(snap.get("aim_ok"))
                row["pan_actual"] = snap.get("pan_actual")
                row["arrived"] = snap.get("arrived")
                if snap.get("error"):
                    row["error"] = snap.get("error")
                    looks.append(row)
                    continue
                wide_jpg = snap.get("jpeg_wide") or b""
                face_jpg = snap.get("jpeg_face") or b""
                try:
                    if wide_jpg:
                        (scan_dir / f"v{i:03d}_{name}_pan{pan}_t{tilt}_wide.jpg").write_bytes(
                            wide_jpg
                        )
                    if face_jpg:
                        (scan_dir / f"v{i:03d}_{name}_pan{pan}_t{tilt}_face.jpg").write_bytes(
                            face_jpg
                        )
                except Exception:
                    pass
                wide = _name_from_jpeg(wide_jpg, presence, stop=name)
                face = _name_from_jpeg(face_jpg, presence, stop=name)
                names: list[str] = []
                for n in list(wide.get("names") or []) + list(face.get("names") or []):
                    n = str(n).strip().lower()
                    if n and n not in names:
                        names.append(n)
                # Wide counts. FaceFinder names. Zoomed poster faces are not
                # a headcount (measured 2026-08-18: stand_11 zoom n=3 Smiths).
                n_faces = int(wide.get("n_faces") or 0)
                body = _body_blob_same_tilt(wide_jpg, pan=pan, tilt=tilt)
                row.update(
                    {
                        "ok": True,
                        "n_faces": n_faces,
                        "n_faces_wide": wide.get("n_faces"),
                        "n_faces_zoom": face.get("n_faces"),
                        "person_id": face.get("person_id") or wide.get("person_id"),
                        "names": names,
                        "body": bool(body),
                        "facefinder": bool(snap.get("facefinder")),
                    }
                )
            except Exception as e:
                row["ok"] = False
                row["error"] = f"{type(e).__name__}: {e}"[:160]
            looks.append(row)
    finally:
        # Home while we still own the lease, then give it back.
        try:
            home = go_home(rs, lid)
        except Exception as e:
            home = {"ok": False, "error": str(e)[:120]}
        try:
            rel = rs._post("/v1/lease/release", {"lease_id": lid}, timeout=8.0)
            lease_released = bool(rel.get("ok"))
        except Exception:
            lease_released = False

    seen, unnamed_want = tally_operator_looks(looks, user_hour=user_h)
    sot: dict[str, Any] | None = None
    try:
        from .server import get_svc

        svc = get_svc()
        ev = apply_operator_scan(
            svc.inventory, named=seen, unnamed_want=unnamed_want, uid=uid
        )
        now = time.time()
        cur = svc.get()
        st = svc._presence_from_inventory(
            frame_age=getattr(cur, "frame_age_sec", 0.2) or 0.2,
            camera_open=getattr(cur, "camera_open", True),
            camera_blind=getattr(cur, "camera_blind", False),
            last_event={
                "kind": "operator_rescan",
                "source": "inspector",
                "seen": ev.get("seen"),
                "left": ev.get("left"),
                "unnamed": ev.get("unnamed"),
                "facefinder": True,
                "ts": now,
            },
            count_trust="fact",
            camera_mode="holding",
            door_names=[],
        )
        svc._set(st)
        if hasattr(svc, "_save_inventory_disk"):
            svc._save_inventory_disk(force=True)
        sot = {
            "ok": True,
            "headcount": int(st.headcount),
            "people": [
                {"name": p.name or p.slot_id, "seat": p.seat}
                for p in (st.people or [])
            ],
            "seen": ev.get("seen"),
            "left": ev.get("left"),
            "unnamed": ev.get("unnamed"),
        }
    except Exception as e:
        sot = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}

    bodies_seen = len(seen) + int(unnamed_want)
    if uid and uid not in seen:
        bodies_seen += 1
    out = {
        "ok": True,
        "op": "who_is_here",
        "shadow": False,
        "seen": (sot or {}).get("seen") or ([uid] + seen if uid else seen),
        "unnamed": unnamed_want,
        "bodies_seen": bodies_seen,
        "looks": [{k: v for k, v in r.items() if not str(k).startswith("jpeg")} for r in looks],
        "sot": sot,
        "facefinder": True,
        "scan_dir": str(scan_dir),
        "lease_released": bool(lease_released),
        "door": {
            "ok": bool(home.get("home") or home.get("ok")),
            "home": home,
            "already_home": home.get("already_home"),
            "entry_ff": home.get("entry_ff") or home.get("facelock"),
            "pan": (door_home_pose() or {}).get("pan"),
        },
        "sot_roster": [r.person_id for r in roster_from_presence(presence)],
        "note": (
            "lease → FF off → aim → pan readback → wide → FF zoom → "
            "FF off. FaceID names. S# if body no name. User kept. "
            "Then door home and lease returned."
        ),
        "wall_sec": round(time.time() - t0, 3),
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    slim = {k: out[k] for k in out if k != "looks"}
    slim["looks"] = [
        {
            k: r.get(k)
            for k in (
                "stop",
                "hour",
                "pan",
                "pan_actual",
                "arrived",
                "tilt",
                "names",
                "n_faces",
                "body",
                "error",
            )
        }
        for r in looks
    ]
    _append_jsonl("who_is_here.jsonl", slim)
    try:
        _memory_learn(
            "who_is_here",
            f"who_is_here seen={out.get('seen')} unnamed={unnamed_want}",
            {"seen": out.get("seen"), "unnamed": unnamed_want},
        )
    except Exception:
        pass
    return out


def scan_room(presence: dict) -> dict[str, Any]:
    """Same walk as Who-is-here. One operator recount."""
    out = who_is_here(presence)
    out["op"] = "scan_room"
    return out


def _fetch_voice_soft() -> tuple[str | None, float | None]:
    """Soft voice suggest from recent ear turns (omni) — sim only, no raw emb."""
    try:
        import urllib.request

        url = OMNI_URL if "?" in OMNI_URL else f"{OMNI_URL}?n=12"
        with urllib.request.urlopen(url, timeout=2.0) as r:
            d = json.loads(r.read().decode())
        for t in d.get("turns") or []:
            if not isinstance(t, dict):
                continue
            st = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
            vsim = st.get("voice_emb_sim")
            src = (st.get("source") or "") or ""
            pid = st.get("person_id") or t.get("person_id")
            if vsim is None and not src.startswith("voice"):
                continue
            try:
                sim_f = float(vsim) if vsim is not None else None
            except (TypeError, ValueError):
                sim_f = None
            if pid or sim_f is not None:
                return (
                    str(pid).strip().lower() if pid else None,
                    sim_f,
                )
    except Exception:
        pass
    return None, None


def _faceidargest_from_jpeg(jpeg: bytes) -> dict[str, Any]:
    """Detect faces on a JPEG; return largest emb + meta (or n_faces=0)."""
    import cv2
    import numpy as np

    from .face_engine import get_face_engine

    arr = np.frombuffer(jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"ok": False, "error": "jpeg_decode_failed", "n_faces": 0}
    fe = get_face_engine()
    faces = fe.detect(img) or []
    if not faces:
        return {
            "ok": True,
            "n_faces": 0,
            "emb": None,
            "frame_shape": list(img.shape),
        }

    def _area(f: Any) -> float:
        b = fe.bbox(f) or [0, 0, 0, 0]
        return abs(float(b[2] - b[0]) * float(b[3] - b[1]))

    face = max(faces, key=_area)
    emb = fe.embed(face)
    if emb is None:
        return {"ok": False, "error": "embed_failed", "n_faces": len(faces)}
    g, _gc = fe.gender(face)
    return {
        "ok": True,
        "n_faces": len(faces),
        "emb": [float(x) for x in emb.tolist()],
        "det_score": fe.det_score(face),
        "soft_gender": g,
        "bbox": fe.bbox(face),
        "frame_shape": list(img.shape),
        "model": "faceid",
    }


def _live_faces_from_jpeg(
    jpeg: bytes, *, stop: str | None = None
) -> list[dict[str, Any]]:
    """Detect faces, drop posters/portraits/tiny hallway pics. Not a camera move."""
    if not jpeg or len(jpeg) < 500:
        return []
    import cv2
    import numpy as np

    from .face_engine import get_face_engine
    from .static_faces import blanked_by_reference, in_masked_zone, too_small_to_name

    arr = np.frombuffer(jpeg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    fe = get_face_engine()
    out: list[dict[str, Any]] = []
    for fobj in fe.detect(img) or []:
        bbox = fe.bbox(fobj)
        if too_small_to_name(bbox):
            continue
        if in_masked_zone(stop, bbox, img.shape):
            continue
        emb = fe.embed(fobj)
        if emb is None:
            continue
        try:
            if blanked_by_reference(emb, bbox, img.shape, stop=stop)[0]:
                continue
        except Exception:
            pass
        g, _gc = fe.gender(fobj)
        out.append(
            {
                "emb": [float(x) for x in emb.tolist()],
                "bbox": bbox,
                "det_score": fe.det_score(fobj),
                "soft_gender": g,
                "frame_shape": list(img.shape),
            }
        )
    return out


def _name_from_jpeg(
    jpeg: bytes, presence: dict, *, stop: str | None = None
) -> dict[str, Any]:
    """FaceID on an already-captured still. Posters are not people."""
    live = _live_faces_from_jpeg(jpeg, stop=stop)
    if not live:
        return {"n_faces": 0, "names": [], "person_id": None}
    face_meta = max(
        live,
        key=lambda r: abs(
            float((r.get("bbox") or [0, 0, 0, 0])[2])
            - float((r.get("bbox") or [0, 0, 0, 0])[0])
        ),
    )
    n_faces = len(live)
    # Operator walk must name people who are not already on SoT.
    # Closed roster-only match left another person unnamed (2026-08-18 11:54).
    banks, _ = _banks_and_stamps()
    pres_for_gate = dict(presence or {})
    if banks:
        pres_for_gate["people"] = [
            {
                "name": b.person_id,
                "person_id": b.person_id,
                "certain": False,
                "gender": b.gender,
            }
            for b in banks
        ]
        pres_for_gate["count_certain"] = False
    v_out = verdict(
        emb=face_meta["emb"],
        presence=pres_for_gate,
        soft_gender=face_meta.get("soft_gender"),
        media_flag=False,
        det_score=face_meta.get("det_score"),
    )
    verd = v_out.get("verdict") if isinstance(v_out.get("verdict"), dict) else {}
    names = [
        str(n).strip().lower()
        for n in (verd.get("names") or [])
        if str(n).strip()
    ]
    pid = verd.get("person_id")
    if pid:
        p = str(pid).strip().lower()
        if p and p not in names:
            names = [p] + names
    return {"n_faces": n_faces, "names": names, "person_id": pid}


def _body_blob_same_tilt(jpeg: bytes, *, pan: int, tilt: int) -> bool:
    """True only when an empty ref at the SAME pitch shows a blob."""
    if not jpeg or len(jpeg) < 500:
        return False
    try:
        from .body_audit import LAST_DIR, nearest_ref_for_pan, write_ref_diff
    except Exception:
        return False
    tilt_deg = int(round(int(tilt) / 3600.0))
    _ref, _st, d_pan = nearest_ref_for_pan(int(pan), tilt_deg=tilt_deg)
    if _ref is None or d_pan is None or int(d_pan) >= 400000:
        return False
    try:
        LAST_DIR.mkdir(parents=True, exist_ok=True)
        live = LAST_DIR / f"rescan_pan{int(pan)}_t{int(tilt)}.jpg"
        live.write_bytes(jpeg)
        diff = write_ref_diff(
            live, LAST_DIR / f"rescan_diff_pan{int(pan)}.jpg",
            pan=int(pan), tilt_deg=tilt_deg,
        )
    except Exception:
        return False
    if not diff.get("ok"):
        return False
    try:
        return float(diff.get("frac") or 0) >= 0.15
    except (TypeError, ValueError):
        return False


def live_snap(
    *,
    presence: dict,
    media_flag: bool = False,
    force: bool = False,
    snapshot_url: str | None = None,
    attach_voice: bool = True,
    person_id_hint: str | None = None,
    run_cpu_llm: bool = False,
    trigger: str | None = None,
    room_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Camera snapshot → FaceID emb → gate verdict (+ optional voice soft).

    Shadow-only. source=live_snap in log. Does not thrash PTZ (uses current FOV).
    """
    import urllib.request

    now = time.time()
    if not force and (now - _STATE.last_live_ts) < LIVE_SNAP_THROTTLE:
        return {
            "ok": True,
            "op": "live_snap",
            "throttled": True,
            "throttle_remaining_sec": round(
                LIVE_SNAP_THROTTLE - (now - _STATE.last_live_ts), 2
            ),
            "last_live_snap": _STATE.last_live_snap,
            "last_verdict": _STATE.last_verdict,
            "trigger": trigger,
        }

    url = (snapshot_url or CAMERA_SNAPSHOT).strip()
    try:
        with urllib.request.urlopen(url, timeout=8.0) as r:
            jpeg = r.read()
    except Exception as e:
        return {
            "ok": False,
            "op": "live_snap",
            "error": f"snapshot_failed: {type(e).__name__}: {e}"[:200],
            "snapshot_url": url,
        }
    if not jpeg or len(jpeg) < 500:
        return {"ok": False, "op": "live_snap", "error": "empty_snapshot", "bytes": len(jpeg or b"")}

    face_meta = _faceidargest_from_jpeg(jpeg)
    re_link = None
    if isinstance(room_event, dict):
        re_link = {
            "kind": room_event.get("kind"),
            "action": room_event.get("action"),
            "source": room_event.get("source"),
            "frame_seq": room_event.get("frame_seq"),
            "door_names": room_event.get("door_names"),
            "seat_beams": room_event.get("seat_beams"),
            "recorded_ts": room_event.get("recorded_ts") or room_event.get("ts"),
        }
    base = {
        "ok": True,
        "op": "live_snap",
        "source": "live_snap" if not trigger else f"live_snap:{trigger}",
        "trigger": trigger or "manual",
        "room_event": re_link,
        "shadow": True,
        "snapshot_url": url,
        "snapshot_bytes": len(jpeg),
        "n_faces": face_meta.get("n_faces"),
        "det_score": face_meta.get("det_score"),
        "soft_gender": face_meta.get("soft_gender"),
        "bbox": face_meta.get("bbox"),
        "frame_shape": face_meta.get("frame_shape"),
        "ts": now,
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not face_meta.get("ok"):
        base["ok"] = False
        base["error"] = face_meta.get("error") or "face_failed"
        _STATE.last_live_snap = base
        _STATE.last_live_ts = now
        _STATE.n_live_snap += 1
        _append_jsonl("live_snap.jsonl", base)
        return base
    if not face_meta.get("emb"):
        base["ok"] = True
        base["error"] = "no_face_in_fov"
        base["note"] = "Camera FOV has no faceid face — aim desk/door person then retry"
        _STATE.last_live_snap = base
        _STATE.last_live_ts = now
        _STATE.n_live_snap += 1
        _append_jsonl("live_snap.jsonl", base)
        return base

    v_suggest, v_sim = (None, None)
    if attach_voice:
        v_suggest, v_sim = _fetch_voice_soft()
        # ignore weak anonymous voice noise (no id + low sim)
        if v_suggest is None and (v_sim is None or float(v_sim) < 0.35):
            v_sim = None

    # Closed population = named SoT slots. If empty (cold start), shadow-match
    # full face bank so live_snap still IDs a face without inventing headcount.
    pres_for_gate = presence
    roster = roster_from_presence(presence)
    open_bank = False
    if not roster:
        banks, _ = _banks_and_stamps()
        if banks:
            open_bank = True
            pres_for_gate = dict(presence or {})
            pres_for_gate["people"] = [
                {
                    "name": b.person_id,
                    "person_id": b.person_id,
                    "certain": False,
                    "gender": b.gender,
                }
                for b in banks
            ]
            # don't claim count is certain when we only have bank candidates
            pres_for_gate["count_certain"] = False

    v_out = verdict(
        emb=face_meta["emb"],
        presence=pres_for_gate,
        soft_gender=face_meta.get("soft_gender"),
        media_flag=bool(media_flag),
        det_score=face_meta.get("det_score"),
        voice_suggest=v_suggest,
        voice_sim=v_sim,
        person_id_hint=person_id_hint,
    )
    if open_bank and isinstance(v_out.get("verdict"), dict):
        det = v_out["verdict"].setdefault("details", {})
        if isinstance(det, dict):
            det["open_bank_match"] = True
    # tag source on nested verdict log
    if isinstance(v_out, dict):
        v_out["source"] = "live_snap"
        if isinstance(v_out.get("verdict"), dict):
            det = v_out["verdict"].setdefault("details", {})
            if isinstance(det, dict):
                det["source"] = "live_snap"
                det["n_faces"] = face_meta.get("n_faces")
                det["snapshot_bytes"] = len(jpeg)

    base["verdict_ok"] = bool(v_out.get("ok"))
    base["verdict"] = v_out.get("verdict")
    base["validation_ok"] = v_out.get("validation_ok")
    base["roster"] = v_out.get("roster")
    base["voice_suggest"] = v_suggest
    base["voice_sim"] = v_sim
    base["full_verdict"] = v_out

    # optional CPU LLM second opinion (slow — off by default)
    if run_cpu_llm and v_out.get("ok"):
        base["cpu_llm"] = _cpu_inspector_llm(presence, face_meta, v_out, v_suggest, v_sim)

    _STATE.last_live_snap = {k: base[k] for k in base if k != "full_verdict"}
    _STATE.last_live_ts = now
    _STATE.n_live_snap += 1
    if trigger:
        _STATE.last_auto = {
            k: base[k]
            for k in (
                "ok",
                "trigger",
                "source",
                "room_event",
                "n_faces",
                "verdict",
                "ts_local",
                "error",
            )
            if k in base
        }
        _STATE.last_auto_ts = now
        _STATE.n_auto += 1
        _append_jsonl("auto_door.jsonl", {**base, "op": "auto_door"})
        # soft-harvest into train inbox (no User stamp required)
        try:
            _maybe_harvest_async()
        except Exception:
            pass
    _append_jsonl("live_snap.jsonl", base)
    return base


def _maybe_harvest_async() -> None:
    """Throttle: run harvest_errors at most every 5 min after auto_door."""
    import threading

    now = time.time()
    last = getattr(_maybe_harvest_async, "_last", 0.0)
    if now - last < 300:
        return
    _maybe_harvest_async._last = now  # type: ignore[attr-defined]

    def _run() -> None:
        try:
            import subprocess
            import sys

            script = (
                _paths.PACK / "train" / "inspector" / "scripts" / "harvest_errors.py"
            )
            if script.is_file():
                subprocess.run(
                    [sys.executable, str(script)],
                    timeout=60,
                    check=False,
                    capture_output=True,
                )
        except Exception:
            pass

    threading.Thread(target=_run, name="inspector-harvest", daemon=True).start()


def maybe_auto_inspect(
    presence: dict | None,
    *,
    trigger: str,
    room_event: dict[str, Any] | None = None,
    person_id_hint: str | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """G2: door enter / re_id / soft promote → shadow live_snap.

    Never invents headcount. Never writes emb. Throttled. Prefer background=True
    so the door loop is not blocked by FaceID.
    """
    import threading

    global _auto_lock
    if _auto_lock is None:
        _auto_lock = threading.Lock()

    if not AUTO_DOOR:
        return {"ok": True, "skipped": True, "reason": "auto_door_off", "trigger": trigger}

    trig = (trigger or "unknown").strip().lower()
    # Only partnership-relevant triggers
    if trig not in (
        "enter",
        "door_enter",
        "re_id",
        "soft_id",
        "door_face",
        "assert",
        "witness_enter",
        "manual",
        # the User/Fable 2026-08-03: snap-snap the moment room is lit again
        "light_return",
        "lights_on",
    ):
        return {"ok": True, "skipped": True, "reason": f"trigger_ignored:{trig}"}

    now = time.time()
    with _auto_lock:
        if (now - _STATE.last_auto_ts) < AUTO_DOOR_THROTTLE and trig != "manual":
            _STATE.n_auto_throttled += 1
            return {
                "ok": True,
                "throttled": True,
                "trigger": trig,
                "throttle_remaining_sec": round(
                    AUTO_DOOR_THROTTLE - (now - _STATE.last_auto_ts), 2
                ),
                "last_auto": _STATE.last_auto,
            }

    presence = presence or {}
    hint = person_id_hint
    if not hint and isinstance(room_event, dict):
        names = room_event.get("door_names") or []
        if isinstance(names, list) and len(names) == 1:
            hint = str(names[0]).lower()
        beams = room_event.get("seat_beams") or {}
        if not hint and isinstance(beams, dict) and len(beams) == 1:
            hint = next(iter(beams.keys()))

    def _run() -> dict[str, Any]:
        try:
            # Prefer linked room_event from ring if caller did not pass one
            re = room_event
            if re is None:
                try:
                    from .room_events import last as room_last

                    items = room_last(1)
                    re = items[0] if items else None
                except Exception:
                    re = None
            out = live_snap(
                presence=presence,
                force=True,  # auto path has its own throttle
                attach_voice=True,
                person_id_hint=hint,
                run_cpu_llm=AUTO_CPU_LLM,
                trigger=trig,
                room_event=re if isinstance(re, dict) else None,
            )
            return out
        except Exception as e:
            err = {
                "ok": False,
                "op": "auto_door",
                "trigger": trig,
                "error": f"{type(e).__name__}: {e}"[:200],
                "ts": time.time(),
            }
            _STATE.last_auto = err
            _append_jsonl("auto_door.jsonl", err)
            return err

    if background:
        threading.Thread(target=_run, name="inspector-auto-door", daemon=True).start()
        return {
            "ok": True,
            "queued": True,
            "trigger": trig,
            "person_id_hint": hint,
            "shadow": True,
            "note": "auto live_snap running in background (shadow)",
        }
    return _run()


def _cpu_inspector_llm(
    presence: dict,
    face_meta: dict,
    v_out: dict,
    v_suggest: str | None,
    v_sim: float | None,
) -> dict[str, Any]:
    """Slow optional second opinion on :8197 — text evidence only."""
    import urllib.request

    url = os.environ.get("RAIL_INSPECTOR_LLM_URL") or _ports.brain()[0]   # the house's side channel; a pack's engine
    evidence = {
        "headcount": presence.get("headcount"),
        "count_trust": presence.get("count_trust"),
        "face": {
            "engine": "faceid",
            "det_score": face_meta.get("det_score"),
            "n_faces": face_meta.get("n_faces"),
        },
        "voice": {"match": v_suggest, "sim": v_sim},
        "code_gate": v_out.get("verdict"),
    }
    sys_p = (
        "You are House Inspector CPU. CODE already ran FaceID+voice. "
        "JSON only: stamp_level, store_allowed, person_id, agree_with_gate, note. "
        "Never invent headcount."
    )
    payload = {
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": json.dumps(evidence)},
        ],
        "temperature": 0.1,
        "max_tokens": 160,
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            chat = json.loads(r.read().decode())
        content = (chat.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return {"ok": True, "latency_s": round(time.time() - t0, 2), "content": content[:800]}
    except Exception as e:
        return {
            "ok": False,
            "latency_s": round(time.time() - t0, 2),
            "error": f"{type(e).__name__}: {e}"[:160],
        }


def handle(body: dict, presence: dict) -> dict[str, Any]:
    """Dispatch POST /v1/inspector body."""
    op = str(body.get("op") or body.get("action") or "status").strip().lower()
    if op in ("status", "health"):
        return status(presence)
    if op in ("hear", "heard", "beam_join"):
        ranks = None
        raw_ranks = body.get("voice_ranks")
        if isinstance(raw_ranks, list):
            parsed: list[tuple[str, float]] = []
            for row in raw_ranks:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        parsed.append((str(row[0]).lower(), float(row[1])))
                    except (TypeError, ValueError):
                        continue
            ranks = parsed
        peek_raw = body.get("peek") if "peek" in body else None
        peek_arg: bool | None
        if peek_raw is None:
            peek_arg = None
        else:
            peek_arg = bool(peek_raw)
        return hear(
            presence,
            peek=peek_arg,
            az_deg=body.get("az_deg") if body.get("az_deg") is not None else body.get("beam_angle"),
            voice_ranks=ranks,
        )
    if op in ("room_check", "roomcheck", "bodies"):
        peek_r = body.get("peek_regulars")
        if peek_r is None:
            peek_r = body.get("peek", True)
        return room_check(presence, peek_regulars=bool(peek_r))
    if op in ("who_is_here", "whos_here", "whoishere", "whos-here", "who"):
        return who_is_here(presence)
    if op in ("scan_room", "rescan", "rescan_room", "look_around"):
        return scan_room(presence)
    if op in ("error_check", "error-check", "errorcheck"):
        from .error_check import run_error_check
        from .server import get_svc

        return run_error_check(get_svc())
    if op in ("live_snap", "snap", "live"):
        re = body.get("room_event") if isinstance(body.get("room_event"), dict) else None
        out = live_snap(
            presence=presence,
            media_flag=bool(body.get("media_flag")),
            force=bool(body.get("force")),
            snapshot_url=body.get("snapshot_url"),
            attach_voice=body.get("attach_voice", True) is not False,
            person_id_hint=body.get("person_id"),
            run_cpu_llm=bool(body.get("cpu_llm") or body.get("run_cpu_llm")),
            trigger=body.get("trigger") or "manual",
            room_event=re,
        )
        return out
    if op in ("auto", "auto_door", "auto_inspect"):
        re = body.get("room_event") if isinstance(body.get("room_event"), dict) else None
        return maybe_auto_inspect(
            presence,
            trigger=str(body.get("trigger") or "manual"),
            room_event=re,
            person_id_hint=body.get("person_id"),
            background=bool(body.get("background", False)),
        )
    if op == "verdict":
        vs = body.get("voice_suggest")
        vsim = body.get("voice_sim")
        if vs is None and body.get("attach_voice", True) is not False:
            vs, vsim = _fetch_voice_soft()
        return verdict(
            emb=body.get("emb") or body.get("encoding") or body.get("face"),
            presence=presence,
            soft_gender=body.get("soft_gender") or body.get("gender"),
            media_flag=bool(body.get("media_flag")),
            det_score=body.get("det_score"),
            voice_suggest=vs,
            voice_sim=vsim,
            operator_person_id=body.get("operator_person_id") or body.get("operator"),
            person_id_hint=body.get("person_id"),
        )
    if op == "store":
        return store(
            person_id=str(body.get("person_id") or ""),
            emb=body.get("emb") or body.get("encoding"),
            presence=presence,
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else {},
            soft_gender=body.get("soft_gender"),
            media_flag=bool(body.get("media_flag")),
            det_score=body.get("det_score"),
        )
    if op == "stamp":
        embs = body.get("embs")
        if embs is None and body.get("emb") is not None:
            embs = [body.get("emb")]
        return stamp(
            presence=presence,
            embs=embs,
            soft_genders=body.get("soft_genders"),
            media_flag=bool(body.get("media_flag")),
            force=bool(body.get("force")),
            apply=bool(body.get("apply")),
        )
    return {
        "ok": False,
        "error": f"unknown op {op!r}",
        "ops": [
            "status",
            "hear",
            "who_is_here",
            "scan_room",
            "error_check",
            "room_check",
            "live_snap",
            "auto",
            "verdict",
            "stamp",
            "store",
        ],
    }
