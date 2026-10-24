"""Sole-owner camera grabber — ONE process opens V4L and serves JPEG + ring buffer.

STONE (the User 2026-07-22): 15–30 fps continuous, keep ~5s (~150 @ 30fps) for face ID /
VLM / Inspector. Sweep and listen for ID become rare — faces are routine from the buffer.

  RAIL_CAMERA_HZ=30
  RAIL_CAMERA_BUFFER_SEC=5
  RAIL_CAMERA_BUFFER_LEN=150   # optional hard cap; default hz*sec
  RAIL_CAMERA_JPEG_Q=75
  RAIL_CAMERA_STALE_SEC=2
  RAIL_CAMERA_HFLIP=1          # true-handed write: dresser LEFT (room law)
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths
from . import guard as _guard  # round 46: this machine only (the module's copy of lib/crissa_guard)

import base64
import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

from .ptz import (
    DOOR_HOME_PAN,
    DOOR_HOME_TILT,
    DOOR_HOME_ZOOM,
    aim as ptz_aim,
    aim_door_home,
    get_ctrl,
    pan_error,
    set_ctrl,
    wrap_pan,
)

DEVICE = os.environ.get("RAIL_CAMERA_DEVICE", "0")
V4L_DEV = os.environ.get("RAIL_CAMERA_V4L_DEV", "/dev/video0")
PORT = int(os.environ.get("RAIL_CAMERA_PORT") or _ports.port("camera"))
HOST = os.environ.get("RAIL_CAMERA_HOST", "127.0.0.1")
JPEG_Q = int(os.environ.get("RAIL_CAMERA_JPEG_Q", "75"))
# 30fps × 5s = 150 frames for VLM/Inspector face routine
HZ = float(os.environ.get("RAIL_CAMERA_HZ", "30"))
BUFFER_SEC = float(os.environ.get("RAIL_CAMERA_BUFFER_SEC", "5"))
_buf_env = os.environ.get("RAIL_CAMERA_BUFFER_LEN", "").strip()
BUFFER_LEN = int(_buf_env) if _buf_env else max(1, int(round(HZ * BUFFER_SEC)))
# Energy ring is independent of the JPEG ring so onset can walk back past a
# 25 s camera excursion (JPEG is ~40 s at 15 Hz / 5 s at 30 Hz).
ENERGY_LEN = int(os.environ.get("RAIL_CAMERA_ENERGY_LEN", "5400"))
STALE_SEC = float(os.environ.get("RAIL_CAMERA_STALE_SEC", "2"))
# True-handed at write (HARDCODED 2026-08-11). Measured door frame: dresser +
# microwave LEFT, macrame RIGHT after flip. Raw USB is mirrored; every consumer
# sees room L/R. Env RAIL_CAMERA_HFLIP=0 is emergency only (breaks train/pack law).
_hflip_env = os.environ.get("RAIL_CAMERA_HFLIP", "1").strip().lower()
HFLIP = _hflip_env not in ("0", "false", "no")
# Product default is always flip; refuse silent dual-path "maybe later"
if not HFLIP:
    import sys

    print(
        "[camera] WARN: RAIL_CAMERA_HFLIP=0 — camera_raw; "
        "do not train direction; packs must not be labeled true_handed",
        file=sys.stderr,
        flush=True,
    )
# Motion thr only when aimed at door (ego-motion during pan is not a door event)
MOTION_DOOR_ONLY = os.environ.get("RAIL_CAMERA_MOTION_DOOR_ONLY", "1") not in (
    "0",
    "false",
    "no",
)
MOTION_PAN_TOL = int(os.environ.get("RAIL_CAMERA_MOTION_PAN_TOL", "12000"))
_ROOT = _paths.ROOT
STOPS_PATH = Path(
    os.environ.get("RAIL_CAMERA_STOPS", str(_ROOT / "data" / "camera-stops.json"))
)

_lock = threading.RLock()
_cap = None
_last_jpeg: bytes | None = None
_last_ts = 0.0
_last_err: str | None = None
_frames = 0
_seq = 0
_identical_streak = 0
_last_sha: str | None = None
_stop = threading.Event()
_sweeping = threading.Event()
_last_sweep: dict | None = None
# Ring buffer: oldest → newest. Each item is (seq, ts, sha, jpeg_bytes)
_buffer: deque = deque(maxlen=BUFFER_LEN)
# Per-frame absdiff energy, same length as JPEG ring. energy < 0 = no sample
# (pan / lease / sweep). Sub-threshold values stay so find_onset can walk
# back through the approach the trigger discarded (gap A).
_energy: deque = deque(maxlen=max(1, ENERGY_LEN))
# Timeline triggers → point into the running 30fps buffer by seq.
# the User: Gemma should only load these frames after a trigger, not re-eat the world every 2 min.
_triggers: deque = deque(maxlen=int(os.environ.get("RAIL_CAMERA_TRIGGER_MAX", "64")))
# Persist JPEGs for trigger seqs so Gemma can load after the 5s ring ages out.
_trigger_jpegs: dict[int, bytes] = {}
_TRIGGER_JPEG_MAX = int(os.environ.get("RAIL_CAMERA_TRIGGER_JPEG_MAX", "48"))
_prev_gray_small = None  # motion energy baseline
_trigger_lock = threading.Lock()
TRIGGER_MOTION = os.environ.get("RAIL_CAMERA_TRIGGER_MOTION", "1") not in (
    "0",
    "false",
    "no",
)
TRIGGER_MOTION_MIN = float(os.environ.get("RAIL_CAMERA_TRIGGER_MOTION_MIN", "8.0"))
TRIGGER_COOLDOWN_SEC = float(os.environ.get("RAIL_CAMERA_TRIGGER_COOLDOWN_SEC", "0.35"))
_last_trigger_ts = 0.0
# Factory/firmware home is pan≈0 — never legitimate for door watch.
# Facefinder mode-OFF / XU release snaps gimbal to center; re-aim door instantly.
FACTORY_HOME_TOL = int(os.environ.get("RAIL_CAMERA_FACTORY_HOME_TOL", "7200"))
DOOR_HOME_GUARD = os.environ.get("RAIL_CAMERA_DOOR_HOME_GUARD", "1") not in (
    "0",
    "false",
    "no",
)
# the User 2026-08-12: look_around/facefind left cam at chair pan=300000 overnight;
# factory-home guard only catches pan≈0. Rest FOV must be door after tours end.
# Tight rest: tours must not leave seat FOV more than a few seconds (door law).
REST_OFF_DOOR_SEC = float(os.environ.get("RAIL_CAMERA_REST_OFF_DOOR_SEC", "3"))
_last_factory_guard_ts = 0.0
_factory_recoveries = 0
_last_rest_guard_ts = 0.0
_rest_recoveries = 0
# XU frame GET_CUR is sticky-1 on Link 2 (survives SET 0 and USB). Do not
# treat it as "off door" — that re-aimed every 2 s (recovery#2000+ while
# pan/tilt already at door, 2026-08-16). Pose drift is the recover signal.
# Last *intentional* pan from /aim or return_door. Desk/sit is pan≈0 in this office —
# without this, capture_loop treats every desk aim as "factory home" and yanks door
# (measured enroll fight: FACTORY HOME pan=0 → immediate door 450000).
_cmd_pan: int | None = None
_cmd_pan_ts: float = 0.0
_hold_until: float = 0.0  # lease: skip factory→door recovery until epoch
# Tool barge (the User 2026-08-14): pause the door watchdogs the same way TTS
# speaking=True pauses the ear. POST /v1/pause, take the lease, work, home,
# /v1/lease/release, /v1/resume. Without this the factory/REST recoveries
# and presence _ensure_door_aim yank the gimbal back to the door.
_pause_until: float = 0.0
_pause_owner: str = ""
_pause_reason: str = ""
PAUSE_TTL_MAX = float(os.environ.get("RAIL_CAMERA_PAUSE_TTL_MAX", "180"))
# HTTP + PTZ mutation log (who called what; the User: something overrides firmware)
_http_audit: deque = deque(maxlen=int(os.environ.get("RAIL_CAMERA_HTTP_AUDIT", "80")))


def note_intentional_aim(pan: int, *, hold_sec: float = 0.0) -> None:
    """Record commanded FOV so factory-home watchdog does not fight desk/sit."""
    global _cmd_pan, _cmd_pan_ts, _hold_until
    _cmd_pan = int(pan)
    _cmd_pan_ts = time.time()
    if hold_sec and hold_sec > 0:
        _hold_until = max(_hold_until, time.time() + float(hold_sec))


def clear_aim_hold() -> None:
    global _hold_until
    _hold_until = 0.0


def pause_watchdogs(
    owner: str = "tool",
    ttl_sec: float = 15.0,
    reason: str = "",
    steal: bool = True,
) -> dict:
    """Yield factory/rest recoveries so AUTH / a tool can own the gimbal.

    the User 2026-08-14: pause the oracle service, take the tool, turn it back on.
    AUTH barges an older lease the same way speech barges TTS.
    """
    global _pause_until, _pause_owner, _pause_reason, _hold_until, _lease
    ttl = max(1.0, min(float(ttl_sec or 0) or 15.0, PAUSE_TTL_MAX))
    now = time.time()
    stolen = None
    if steal:
        with _lease_lock:
            if _lease and float(_lease.get("until") or 0) > now:
                stolen = dict(_lease)
                _lease = None
    _pause_until = now + ttl
    _pause_owner = str(owner or "tool")[:40]
    _pause_reason = str(reason or "")[:80]
    _hold_until = max(_hold_until, _pause_until)
    out = {
        "ok": True,
        "paused": True,
        "owner": _pause_owner,
        "reason": _pause_reason,
        "ttl_sec": ttl,
        "remaining_sec": round(_pause_until - now, 2),
    }
    if stolen:
        out["stole_lease"] = stolen.get("owner")
    return out


def resume_watchdogs(*, force: bool = False) -> dict:
    """Door watch runs again. A live named lease keeps the pause unless force."""
    global _pause_until, _pause_owner, _pause_reason, _hold_until
    if not force and lease_state():
        return {
            "ok": True,
            "paused": True,
            "kept": True,
            "reason": "lease_active",
            "owner": _pause_owner,
        }
    _pause_until = 0.0
    _pause_owner = ""
    _pause_reason = ""
    if not lease_state():
        _hold_until = 0.0
    return {"ok": True, "paused": False, "resumed": True}


def watchdogs_paused(now: float | None = None) -> bool:
    """True while a tool barge, lease, sweep, or timed hold is live."""
    now = time.time() if now is None else now
    if _sweeping.is_set():
        return True
    if _pause_until and now < _pause_until:
        return True
    if _hold_until and now < _hold_until:
        return True
    return lease_state(now) is not None


# ---- camera lease (DESIGN-ROOM-SWEEP) --------------------------------------
# One named owner holds FOV for a whole tour; a second tool gets a refusal
# instead of silently stealing mid-arc. The bare _hold_until stays for
# anonymous short aims; an active lease extends it so the factory guard and
# the presence door pipeline pause exactly as for a tool hold.
_lease_lock = threading.Lock()
# Is the firmware facefinder engaged, and until when does its wake still count
# as camera motion? the User 2026-08-16: "Turn off motion trigger when FF is engaged
# so argus does not get notification right as it moves off the door at his
# behest ... Would seem like someone enters every time he calls the tool."
_ff_engaged: bool = False
_ff_quiet_until: float = 0.0
# Seconds after the camera is handed back before motion counts again — the swing
# home is itself motion, and it is motion we caused.
CAMERA_QUIET_SEC = float(os.environ.get("RAIL_CAMERA_QUIET_SEC", "2.5"))
_lease: dict | None = None  # {"owner", "lease_id", "until"}
LEASE_TTL_MAX = float(os.environ.get("RAIL_CAMERA_LEASE_TTL_MAX", "120"))


def note_facefinder(on: bool) -> None:
    """Record facefinder engage/release, and start the quiet window on release."""
    global _ff_engaged, _ff_quiet_until
    _ff_engaged = bool(on)
    if not on:
        _ff_quiet_until = time.time() + CAMERA_QUIET_SEC


def camera_is_ours(now: float | None = None) -> bool:
    """Is something of OURS moving the camera right now (or just was)?

    A motion trigger raised while we are driving is ego-motion, not a person.
    The pan check below only catches the camera once it has ALREADY left the
    door — it cannot catch the first frames of the slew, which is exactly when
    the trigger fires and exactly what reached Argus as "someone entered".
    """
    now = time.time() if now is None else now
    if _ff_engaged or now < _ff_quiet_until:
        return True
    return lease_state(now) is not None


def lease_state(now: float | None = None) -> dict | None:
    """Active lease or None; expiry pruned in place."""
    global _lease
    now = time.time() if now is None else now
    with _lease_lock:
        if _lease and _lease["until"] <= now:
            _lease = None
        return dict(_lease) if _lease else None


def acquire_lease(
    owner: str, ttl_sec: float, lease_id: str | None = None
) -> dict:
    """Grant a new lease, or renew when lease_id matches the active one."""
    global _lease, _hold_until
    owner = str(owner or "tool")[:40]
    ttl = max(1.0, min(float(ttl_sec or 0) or 12.0, LEASE_TTL_MAX))
    now = time.time()
    with _lease_lock:
        if _lease and _lease["until"] > now and lease_id != _lease["lease_id"]:
            return {
                "ok": False,
                "error": "camera_leased",
                "owner": _lease["owner"],
                "remaining_sec": round(_lease["until"] - now, 2),
            }
        renewing = bool(_lease and lease_id == _lease.get("lease_id"))
        lid = _lease["lease_id"] if renewing else f"L{int(now * 1000):x}"
        _lease = {"owner": owner, "lease_id": lid, "until": now + ttl}
        _hold_until = max(_hold_until, now + ttl)
        return {
            "ok": True,
            "lease_id": lid,
            "owner": owner,
            "until": _lease["until"],
            "ttl_sec": ttl,
            "renewed": renewing,
        }


def release_lease(lease_id: str | None) -> dict:
    """Release when lease_id matches; expired/absent release is a no-op ok."""
    global _lease, _hold_until
    with _lease_lock:
        if _lease is None or _lease["until"] <= time.time():
            _lease = None
            return {"ok": True, "released": False, "reason": "no_active_lease"}
        if lease_id != _lease["lease_id"]:
            return {
                "ok": False,
                "error": "wrong_lease_id",
                "owner": _lease["owner"],
            }
        _lease = None
        # The swing home is motion we caused; do not report it as a person.
        global _ff_quiet_until
        _ff_quiet_until = time.time() + CAMERA_QUIET_SEC
        _hold_until = 0.0
        return {"ok": True, "released": True}


def _http_audit_log(path: str, *, client: str = "", body_keys: list | None = None, note: str = "") -> None:
    try:
        pan = get_ctrl(V4L_DEV, "pan_absolute")
        rec = {
            "ts": time.time(),
            "ts_local": time.strftime("%H:%M:%S"),
            "path": path,
            "client": client,
            "body_keys": body_keys or [],
            "pan": pan,
            "note": note,
        }
        _http_audit.append(rec)
        print(
            f"[cam-http] {rec['ts_local']} {path} pan={pan} client={client or '?'} {note}",
            flush=True,
        )
    except Exception:
        pass


def _load_stops() -> dict:
    if STOPS_PATH.is_file():
        return json.loads(STOPS_PATH.read_text())
    return {
        "door": {
            "pan": DOOR_HOME_PAN,
            "tilt": DOOR_HOME_TILT,
            "zoom": DOOR_HOME_ZOOM,
        },
        "settle_sec": 1.4,
        "pan_tol": 3600,
        "stops": [
            {"id": "door", "pan": DOOR_HOME_PAN, "tilt": DOOR_HOME_TILT},
            {"id": "desk", "pan": 150000, "tilt": 0},
            {"id": "chair", "pan": 300000, "tilt": 0},
        ],
    }


def _open() -> bool:
    global _cap, _last_err
    with _lock:
        if _cap is not None and _cap.isOpened():
            return True
        idx = int(DEVICE) if str(DEVICE).isdigit() else DEVICE
        _cap = cv2.VideoCapture(idx)
        if not _cap.isOpened():
            _last_err = f"cannot open V4L {idx}"
            _cap = None
            return False
        _cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        _cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        try:
            _cap.set(cv2.CAP_PROP_FPS, HZ)
        except Exception:
            pass
        # Prefer MJPEG for USB bandwidth at 30fps when driver allows
        try:
            _cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        _last_err = None
        global _OPENED_AT
        _OPENED_AT = time.time()      # each open of the device: a re-plugged camera is measured again (2026-10-12)
        _CAM_NAME.clear()             # and named again (it may be another camera)
        return True


_OPENED_AT = 0.0


def record_trigger(
    *,
    kind: str,
    seq: int | None = None,
    capture_ts: float | None = None,
    note: str | None = None,
    meta: dict | None = None,
    jpeg: bytes | None = None,
) -> dict:
    """Stamp a timeline pointer into the running 30fps buffer.

    Also keeps a short JPEG side-cache so Gemma can still fetch the frame after
    the 5s ring ages out (was: 'no trigger frames in ring (aged out?)').
    """
    global _last_trigger_ts
    with _lock:
        use_seq = int(seq if seq is not None else _seq)
        use_ts = float(capture_ts if capture_ts is not None else (_last_ts or time.time()))
        if jpeg is None:
            # pull from live ring if still present
            for s, ts, sha, j in _buffer:
                if s == use_seq:
                    jpeg = j
                    use_ts = float(ts)
                    break
            if jpeg is None and _last_jpeg and use_seq == _seq:
                jpeg = _last_jpeg
    now = time.time()
    if now - _last_trigger_ts < TRIGGER_COOLDOWN_SEC and kind == "motion":
        return {"ok": True, "skipped": "cooldown", "seq": use_seq}
    _last_trigger_ts = now
    ev = {
        "seq": use_seq,
        "capture_ts": use_ts,
        "ts": now,
        "kind": str(kind or "mark"),
        "note": note,
        "meta": meta or {},
        "pan_actual": get_ctrl(V4L_DEV, "pan_absolute"),
        "jpeg_kept": bool(jpeg),
    }
    with _trigger_lock:
        _triggers.append(ev)
        if jpeg:
            _trigger_jpegs[use_seq] = jpeg
            # bound memory: drop oldest keys
            while len(_trigger_jpegs) > _TRIGGER_JPEG_MAX:
                oldest = min(_trigger_jpegs.keys())
                _trigger_jpegs.pop(oldest, None)
    return {"ok": True, **ev}


def list_triggers(*, since_ts: float | None = None, max_n: int = 32) -> dict:
    with _trigger_lock:
        items = list(_triggers)
    if since_ts is not None:
        items = [t for t in items if float(t.get("ts") or 0) > float(since_ts)]
    items = items[-max(1, int(max_n)) :]
    with _lock:
        buf_n = len(_buffer)
        newest = _seq
        oldest = _buffer[0][0] if _buffer else None
    return {
        "ok": True,
        "n": len(items),
        "triggers": items,
        "buffer_seq_newest": newest,
        "buffer_seq_oldest": oldest,
        "buffer_n": buf_n,
        "note": "point Gemma/Cosmos at trigger.seq in the running 30fps ring",
    }


def get_frame_by_seq(seq: int) -> dict | None:
    """Return one frame by seq: live ring first, then trigger JPEG cache."""
    import hashlib

    want = int(seq)
    with _lock:
        for s, ts, sha, jpg in _buffer:
            if s == want:
                return {
                    "ok": True,
                    "seq": s,
                    "capture_ts": ts,
                    "sha": sha,
                    "bytes": len(jpg),
                    "jpeg_b64": base64.b64encode(jpg).decode(),
                    "source": "ring",
                }
    with _trigger_lock:
        jpg = _trigger_jpegs.get(want)
        # also search trigger events for ts
        ts = None
        for t in _triggers:
            if int(t.get("seq") or -1) == want:
                ts = t.get("capture_ts")
                break
    if jpg:
        return {
            "ok": True,
            "seq": want,
            "capture_ts": ts,
            "sha": hashlib.sha256(jpg).hexdigest()[:16],
            "bytes": len(jpg),
            "jpeg_b64": base64.b64encode(jpg).decode(),
            "source": "trigger_cache",
        }
    return None


def get_frames_by_seqs(seqs: list[int]) -> list[dict]:
    out: list[dict] = []
    for s in seqs:
        fr = get_frame_by_seq(int(s))
        if fr:
            out.append(fr)
    return out


def note_energy(seq: int, ts: float, energy: float) -> None:
    """Append one energy sample. energy < 0 = no sample (pan / lease)."""
    row = {"seq": int(seq), "ts": float(ts), "energy": float(energy)}
    with _lock:
        _energy.append(row)


def list_energy(*, since_seq: int | None = None, max_n: int | None = None) -> dict:
    with _lock:
        rows = list(_energy)
        newest = _seq
        oldest = _buffer[0][0] if _buffer else None
        buf_n = len(_buffer)
    if since_seq is not None:
        want = int(since_seq)
        rows = [r for r in rows if int(r.get("seq") or 0) > want]
    cap = ENERGY_LEN if max_n is None else max(1, int(max_n))
    rows = rows[-cap:]
    last = dict(rows[-1]) if rows else None
    return {
        "ok": True,
        "n": len(rows),
        "rows": rows,
        "last": last,
        "buffer_seq_newest": newest,
        "buffer_seq_oldest": oldest,
        "buffer_n": buf_n,
        "hz": HZ,
        "buffer_len_cap": BUFFER_LEN,
        "energy_len_cap": ENERGY_LEN,
        "note": "energy<0 = no sample (pan/lease/sweep); find_onset walks this ring",
    }


def door_coverage(from_ts: float, to_ts: float) -> dict:
    """Was the door covered in [from_ts, to_ts]? Energy<0, HTTP slew, FF track.

    Used by door_fanout slew corroboration and the blind-gap marker.
    """
    lo, hi = (float(from_ts), float(to_ts)) if from_ts <= to_ts else (float(to_ts), float(from_ts))
    if hi <= 0:
        hi = time.time()
    spans: list[dict] = []
    why: list[str] = []
    with _lock:
        rows = list(_energy)
        http = list(_http_audit)
    run_from = None
    for r in rows:
        try:
            ts = float(r.get("ts") or 0)
            en = float(r.get("energy"))
        except (TypeError, ValueError):
            continue
        if ts < lo or ts > hi:
            if en < 0 and run_from is not None and ts > hi:
                spans.append({"from": run_from, "to": hi, "why": "energy_blind", "holder": "pan"})
                run_from = None
            continue
        if en < 0:
            if run_from is None:
                run_from = ts
        elif run_from is not None:
            spans.append({"from": run_from, "to": ts, "why": "energy_blind", "holder": "pan"})
            why.append("energy_blind")
            run_from = None
    if run_from is not None:
        spans.append({"from": run_from, "to": hi, "why": "energy_blind", "holder": "pan"})
        why.append("energy_blind")
    slew_paths = (
        "/v1/aim", "/aim", "/v1/look_around", "/look_around",
        "/v1/return_door", "/return_door", "/v1/facefinder", "/facefinder",
        "/v1/facelock", "/facelock",
    )
    for rec in http:
        try:
            ts = float(rec.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < lo or ts > hi:
            continue
        path = str(rec.get("path") or "")
        if path in slew_paths:
            spans.append({
                "from": ts,
                "to": ts,
                "why": "http_" + path.rsplit("/", 1)[-1],
                "holder": path,
                "pan": rec.get("pan"),
            })
            why.append("http")
    try:
        from .firmware_facelock import firmware_audit_tail
        for rec in firmware_audit_tail(64):
            try:
                ts = float(rec.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ts < lo or ts > hi:
                continue
            if rec.get("kind") == "track":
                spans.append({
                    "from": ts,
                    "to": ts,
                    "why": "fw_track",
                    "holder": "firmware",
                    "enable": rec.get("enable"),
                })
                why.append("fw_track")
    except Exception:
        pass
    return {
        "ok": True,
        "from_ts": lo,
        "to_ts": hi,
        "covered": not spans,
        "blind_spans": spans,
        "why": sorted(set(why)),
    }


def energy_rows() -> list[dict]:
    with _lock:
        return list(_energy)


def onset_at_seq(seq: int) -> dict:
    """Walk the energy ring back from this seq to first motion (gap A)."""
    from .onset import find_onset

    rows = energy_rows()
    trig_i = len(rows) - 1 if rows else 0
    want = int(seq)
    for i, r in enumerate(rows):
        try:
            if int(r.get("seq") or -1) == want:
                trig_i = i
                break
        except (TypeError, ValueError):
            continue
    return find_onset(rows, trig_i)


def _maybe_motion_trigger(frame_bgr, seq: int, ts: float) -> None:
    """Cheap motion energy → timeline trigger (door FOV only).

    Ego-motion while panning/sweeping is NOT a door event — energy spikes of
    40–70 during PTZ were filling gemma_events with desk/pan strips (the User).

    Every frame is recorded on the energy ring, including sub-threshold
    approach. Pan/lease/sweep store energy=-1 (no sample).
    """
    global _prev_gray_small
    if not TRIGGER_MOTION or frame_bgr is None:
        note_energy(seq, ts, -1.0)
        return
    # Never thr while sweep/smooth_scan owns the gimbal
    if _sweeping.is_set():
        _prev_gray_small = None  # reset baseline after pan
        note_energy(seq, ts, -1.0)
        return
    if _hold_until and time.time() < _hold_until:
        _prev_gray_small = None
        note_energy(seq, ts, -1.0)
        return
    # Detach the motion sensor from OUR OWN camera motion.
    if camera_is_ours():
        _prev_gray_small = None   # baseline is stale once we have moved
        note_energy(seq, ts, -1.0)
        return
    pan_now = get_ctrl(V4L_DEV, "pan_absolute")
    if MOTION_DOOR_ONLY and pan_now is not None:
        if pan_error(int(pan_now), DOOR_HOME_PAN) > MOTION_PAN_TOL:
            _prev_gray_small = None
            note_energy(seq, ts, -1.0)
            return
    try:
        small = cv2.resize(frame_bgr, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if _prev_gray_small is not None:
            diff = cv2.absdiff(gray, _prev_gray_small)
            energy = float(diff.mean())
            note_energy(seq, ts, energy)
            if energy >= TRIGGER_MOTION_MIN:
                onset: dict = {}
                try:
                    onset = onset_at_seq(seq)
                except Exception:
                    onset = {}
                # jpeg filled inside record_trigger from ring by seq
                record_trigger(
                    kind="motion",
                    seq=seq,
                    capture_ts=ts,
                    note=f"door_motion energy={energy:.1f}",
                    meta={
                        "energy": round(energy, 2),
                        "onset_seq": onset.get("onset_seq"),
                        "onset_ts": onset.get("onset_ts"),
                        "rest_baseline": onset.get("rest_baseline"),
                        "threshold": onset.get("threshold"),
                        "trigger_offset_s": onset.get("trigger_offset_s"),
                        "onset_why": onset.get("why"),
                        "pan_actual": pan_now,
                        "door_home_pan": DOOR_HOME_PAN,
                        "hflip": HFLIP,
                        "orientation": "true_handed" if HFLIP else "camera_raw",
                    },
                )
        else:
            # First door-home frame after a reset: honest rest, not a trigger.
            note_energy(seq, ts, 0.0)
        _prev_gray_small = gray
    except Exception:
        note_energy(seq, ts, -1.0)


def _capture_once() -> bool:
    global _last_jpeg, _last_ts, _last_err, _frames, _seq, _identical_streak, _last_sha
    import hashlib

    with _lock:
        if not _open():
            return False
        assert _cap is not None
        # Low rate: flush stale sensor buffer. High rate: one read (flush kills 30fps).
        if HZ <= 5:
            for _ in range(2):
                _cap.read()
        ok, frame = _cap.read()
        if not ok or frame is None:
            _last_err = "v4l read failed"
            return False
        # True-handed: HFLIP before any consumer sees the JPEG (buffer, events, stamp UI)
        if HFLIP:
            frame = cv2.flip(frame, 1)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])
        if not ok:
            _last_err = "jpeg encode failed"
            return False
        jpg = buf.tobytes()
        sha = hashlib.sha256(jpg).hexdigest()[:16]
        if sha == _last_sha:
            _identical_streak += 1
        else:
            _identical_streak = 0
            _last_sha = sha
        _last_jpeg = jpg
        _last_ts = time.time()
        _frames += 1
        _seq += 1
        _last_err = None
        _buffer.append((_seq, _last_ts, sha, jpg))
        seq_now, ts_now = _seq, _last_ts
        jpg_out = jpg
    # outside lock: motion stamp into timeline + security raw disk ring
    try:
        _maybe_motion_trigger(frame, seq_now, ts_now)
    except Exception:
        pass
    # Lights-out: same grey mean measure as event-clip CLIP_DARK_LUM
    try:
        from .luminance import gray_mean_bgr, observe as _lum_observe

        _lum_observe(gray_mean_bgr(frame))
    except Exception:
        pass
    try:
        from .security_raw import offer_frame as _sec_offer

        _sec_offer(seq_now, ts_now, jpg_out)
    except Exception:
        pass
    return True


def _loop() -> None:
    period = 1.0 / max(HZ, 0.2)
    while not _stop.is_set():
        if _sweeping.is_set():
            _stop.wait(0.02)
            continue
        t0 = time.time()
        try:
            # the User: factory home (pan 0) is never rest — recover before capture
            _recover_from_factory_home(reason="capture_loop")
            # Stuck tour stop (chair 300k / seats) is never rest either
            _recover_rest_to_door(reason="capture_loop")
            _capture_once()
        except Exception as e:
            global _last_err
            _last_err = f"loop: {e}"[:200]
        elapsed = time.time() - t0
        # spin tightly at high fps; don't sleep longer than remaining period
        _stop.wait(max(0.0, period - elapsed))


def buffer_meta() -> dict:
    with _lock:
        n = len(_buffer)
        if n == 0:
            return {
                "ok": True,
                "n": 0,
                "hz": HZ,
                "buffer_sec_target": BUFFER_SEC,
                "buffer_len_cap": BUFFER_LEN,
                "oldest_seq": None,
                "newest_seq": None,
                "oldest_ts": None,
                "newest_ts": None,
                "span_sec": 0.0,
                "bytes_approx": 0,
            }
        oldest = _buffer[0]
        newest = _buffer[-1]
        nbytes = sum(len(x[3]) for x in _buffer)
        return {
            "ok": True,
            "n": n,
            "hz": HZ,
            "buffer_sec_target": BUFFER_SEC,
            "buffer_len_cap": BUFFER_LEN,
            "oldest_seq": oldest[0],
            "newest_seq": newest[0],
            "oldest_ts": oldest[1],
            "newest_ts": newest[1],
            "span_sec": round(newest[1] - oldest[1], 3),
            "bytes_approx": nbytes,
        }


def get_buffer(
    *,
    since_seq: int | None = None,
    max_frames: int | None = None,
    stride: int = 1,
    include_jpeg: bool = True,
) -> dict:
    """Return ring-buffer frames for face ID / VLM / Inspector.

    since_seq: only frames with seq > since_seq (delta pull — preferred).
    max_frames: cap count (from newest if no since_seq).
    stride: keep every Nth frame (2 ⇒ ~15 face-fps from 30 capture).
    """
    stride = max(1, int(stride))
    with _lock:
        items = list(_buffer)
    if since_seq is not None:
        items = [x for x in items if x[0] > int(since_seq)]
    if stride > 1:
        items = items[::stride]
    if max_frames is not None and max_frames > 0 and len(items) > max_frames:
        items = items[-int(max_frames) :]
    frames = []
    for seq, ts, sha, jpg in items:
        fr = {
            "seq": seq,
            "capture_ts": ts,
            "sha": sha,
            "bytes": len(jpg),
        }
        if include_jpeg:
            fr["jpeg_b64"] = base64.b64encode(jpg).decode()
        frames.append(fr)
    meta = buffer_meta()
    return {
        "ok": True,
        "n": len(frames),
        "since_seq": since_seq,
        "stride": stride,
        "hz": HZ,
        "buffer_len_cap": BUFFER_LEN,
        "buffer_sec_target": BUFFER_SEC,
        "meta": meta,
        "frames": frames,
    }


_PAN_SEEN = False


def door_view(pan_i, door_tol: int) -> bool:
    """Is the camera on its door view? A pan near door home, as always. A CAMERA THAT HAS NEVER REPORTED A PAN HAS ONE
    VIEW, and it is the door's (2026-10-10, found by the round-3 sandbox trial): a webcam with no pan control (most of
    them, every Mac's, a film standing in for one) read as "off the door" forever, so presence never scanned the door
    and the door glance always skipped - she saw nobody. A camera that reported a pan once and then cannot be read is
    off the door as before (a PTZ head mid-move or unplugged). RAIL_CAMERA_FIXED_AT_DOOR=0 is the old rule."""
    global _PAN_SEEN
    if pan_i is not None:
        _PAN_SEEN = True
        return abs(pan_i - int(DOOR_HOME_PAN)) <= door_tol
    if str(os.environ.get("RAIL_CAMERA_FIXED_AT_DOOR", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    return not _PAN_SEEN


_CAM_NAME: list = []


def camera_name() -> str:
    """The V4L2 device's own name (/sys/class/video4linux/<dev>/name), read once; '' when there is none (a Mac, a film)."""
    if not _CAM_NAME:
        name = ""
        try:
            dev = os.path.basename(os.path.realpath(str(V4L_DEV)))
            name = Path("/sys/class/video4linux", dev, "name").read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            name = ""
        _CAM_NAME.append(name[:80])
    return _CAM_NAME[0]


def camera_ptz() -> str:
    """'motor' (a gimbal that turns: fixed_cam.MOTOR_PTZ names it), 'digital' (it reports a pan, but the pan is a crop of
    one view, like the Elgato Facecam 4K on .101), or 'none' (no pan ever reported)."""
    try:
        from . import fixed_cam
        return fixed_cam.ptz_kind(camera_name(), bool(_PAN_SEEN))
    except Exception:  # noqa: BLE001
        return "motor" if _PAN_SEEN else "none"


def health() -> dict:
    age = (time.time() - _last_ts) if _last_ts else None
    stale = age is None or age > STALE_SEC
    # frozen: identical for ~1s at current hz
    frozen = _identical_streak >= int(max(HZ, 1) * 1.5)
    ok = (
        _cap is not None
        and _cap.isOpened()
        and _last_err is None
        and not stale
        and _frames > 0
        and not frozen
        and not _sweeping.is_set()
    )
    if _sweeping.is_set():
        ok = _cap is not None and _cap.isOpened() and _last_err is None
    bm = buffer_meta()
    now = time.time()
    hold_rem = max(0.0, _hold_until - now) if _hold_until else 0.0
    pause_rem = max(0.0, _pause_until - now) if _pause_until else 0.0
    pan_actual = get_ctrl(V4L_DEV, "pan_absolute")
    try:
        pan_i = int(pan_actual) if pan_actual is not None else None
    except (TypeError, ValueError):
        pan_i = None
    # Door FOV = pan near door home (not mid-tool tour)
    door_tol = int(os.environ.get("RAIL_CAMERA_DOOR_PAN_TOL", "24000"))
    at_door = door_view(pan_i, door_tol)
    # Tool hold = active sweep / timed aim lease / named lease only.
    # Do NOT treat "not at door" as hold — stuck chair pan (300k) used to
    # freeze the presence door pipeline all night (the User 2026-08-12).
    lease_active = False
    try:
        with _lease_lock:
            if _lease and float(_lease.get("until") or 0) > now:
                lease_active = True
    except Exception:
        lease_active = False
    tool_hold = bool(
        _sweeping.is_set() or hold_rem > 0 or lease_active or pause_rem > 0
    )
    _e_n, _e_last = _energy_health()
    return {
        "ok": bool(ok),
        "service": "rail-camera",
        "device": DEVICE,
        "v4l_dev": V4L_DEV,
        "frames": _frames,
        "seq": _seq,
        "frame_age_sec": round(age, 3) if age is not None else None,
        "identical_streak": _identical_streak,
        "stale": stale and not _sweeping.is_set(),
        "frozen": frozen,
        "sweeping": _sweeping.is_set(),
        # Tool lease: presence door pipeline MUST pause while true
        "tool_hold": tool_hold,
        "hold_remaining_sec": round(hold_rem, 2),
        "paused": bool(pause_rem > 0 or lease_active or _sweeping.is_set()),
        "pause_owner": _pause_owner or None,
        "pause_reason": _pause_reason or None,
        "pause_remaining_sec": round(pause_rem, 2),
        # Named lease (lease_id withheld — only the holder knows it)
        "lease": (
            {
                "owner": _lease_view["owner"],
                "remaining_sec": round(_lease_view["until"] - now, 2),
            }
            if (_lease_view := lease_state(now))
            else None
        ),
        "cmd_pan": _cmd_pan,
        "at_door": at_door,
        # the camera has reported a pan at least once (a webcam with no pan control never does): presence's fixed
        # camera rule reads it (crissa_presence.fixed_cam, 2026-10-11)
        "pan_seen": bool(_PAN_SEEN),
        # what the camera says it is, and whether its pan is a motor or a crop (crissa_presence.fixed_cam, 2026-10-12)
        "camera_name": camera_name(),
        "camera_ptz": camera_ptz(),
        "opened_at": round(_OPENED_AT, 3) if _OPENED_AT else None,
        "error": _last_err,
        "sole_owner": True,
        "hz": HZ,
        "buffer_sec": BUFFER_SEC,
        "buffer_len": bm.get("n"),
        "buffer_len_cap": BUFFER_LEN,
        "energy_n": _e_n,
        "energy_last": _e_last,
        "buffer_span_sec": bm.get("span_sec"),
        "buffer_bytes": bm.get("bytes_approx"),
        "hflip": HFLIP,
        "orientation": "true_handed" if HFLIP else "camera_raw",
        "orientation_note": (
            "HARDCODED true_handed (2026-08-11 door check: dresser LEFT). "
            "HFLIP at grabber write only — no per-pack polarity patches."
            if HFLIP
            else "camera_raw EMERGENCY — dresser RIGHT; do not train direction."
        ),
        "stale_sec_limit": STALE_SEC,
        "pan_actual": pan_actual,
        "last_sweep_stops": (_last_sweep or {}).get("n_ok"),
        "last_sweep_ts": (_last_sweep or {}).get("ts"),
        "factory_recoveries": _factory_recoveries,
        "rest_recoveries": _rest_recoveries,
        "door_home_pan": DOOR_HOME_PAN,
        "factory_home_tol": FACTORY_HOME_TOL,
        "firmware": _firmware_status_safe(),
        "fw_audit_tail": _fw_audit_safe(8),
        "http_audit_tail": list(_http_audit)[-8:],
        **_luminance_health(),
    }


def _energy_health() -> tuple[int, dict | None]:
    with _lock:
        n = len(_energy)
        last = dict(_energy[-1]) if _energy else None
    return n, last


def _luminance_health() -> dict:
    """lights_out + luminance for presence SoT (shared CLIP_DARK_LUM)."""
    try:
        from .luminance import lights_block, observe

        block = lights_block(observe(None))
        return {
            "lights_out": block["lights_out"],
            "luminance": block.get("luminance"),
            "luminance_threshold": block.get("threshold"),
            "lights": block,
        }
    except Exception as e:
        return {
            "lights_out": False,
            "luminance": None,
            "lights": {"error": str(e)[:120]},
        }


def _firmware_status_safe() -> dict:
    try:
        from .firmware_facelock import firmware_status

        return firmware_status(V4L_DEV)
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _fw_audit_safe(n: int = 8) -> list:
    try:
        from .firmware_facelock import firmware_audit_tail

        return firmware_audit_tail(n)
    except Exception:
        return []


def get_jpeg() -> tuple[bytes | None, dict]:
    h = health()
    with _lock:
        if _last_jpeg is None:
            if not _capture_once():
                return None, h
            h = health()
        meta = {
            "capture_ts": _last_ts,
            "seq": _seq,
            "frame_age_sec": h.get("frame_age_sec"),
            "sha": _last_sha,
        }
        return _last_jpeg, {**h, **meta}


def _recover_from_factory_home(*, reason: str = "watchdog") -> dict | None:
    """If gimbal is at firmware center (pan≈0), slam door home immediately.

    the User: factory home is never legitimate *as rest*. Facefinder mode-OFF / release
    often snaps here and *stays* until something re-aims. Do NOT call release_and_hold
    first — that re-triggers the snap. Absolute pan only.

    Exception (2026-08-01 enroll fight): this office **desk stop is pan≈0**. If the
    last intentional /aim was desk/sit (cmd pan near 0) or a hold lease is active,
    do **not** yank door — that was the enroll vs capture_loop thrash.
    """
    global _last_factory_guard_ts, _factory_recoveries
    if not DOOR_HOME_GUARD or _sweeping.is_set() or watchdogs_paused():
        return None
    now = time.time()
    # Enroll / explicit hold: leave FOV alone
    if now < _hold_until:
        return None
    # Intentional desk/sit (cmd pan near factory center)
    if _cmd_pan is not None and abs(int(_cmd_pan)) <= FACTORY_HOME_TOL * 2:
        return None
    # allow recovery every 0.8s max (avoid fight during intentional pans)
    if now - _last_factory_guard_ts < 0.8:
        return None
    pan_now = get_ctrl(V4L_DEV, "pan_absolute")
    if pan_now is None:
        return None
    if abs(int(pan_now)) > FACTORY_HOME_TOL:
        return None
    _last_factory_guard_ts = now
    _factory_recoveries += 1
    cfg = _load_stops()
    door = cfg.get("door") or {}
    want = int(door.get("pan", DOOR_HOME_PAN))
    tilt = int(door.get("tilt", DOOR_HOME_TILT))
    zoom = int(door.get("zoom", DOOR_HOME_ZOOM))
    tol = int(cfg.get("pan_tol", 3600))
    print(
        f"[camera] FACTORY HOME pan={pan_now} — immediate door "
        f"{want} ({reason}) recovery#{_factory_recoveries} "
        f"(cmd_pan={_cmd_pan})",
        flush=True,
    )
    ar = aim_door_home(
        V4L_DEV,
        pan=want,
        tilt=tilt,
        zoom=zoom,
        settle_sec=1.2,
        pan_tol=tol,
        retries=8,
    )
    # still near 0? one more hard aim
    if (not ar.ok) or abs(int(ar.pan_actual or 0)) <= FACTORY_HOME_TOL:
        ar = aim_door_home(
            V4L_DEV,
            pan=want,
            tilt=tilt,
            zoom=zoom,
            settle_sec=1.6,
            pan_tol=tol,
            retries=10,
        )
    return {
        "ok": bool(ar.ok),
        "reason": reason,
        "pan_before": int(pan_now),
        "pan_cmd": want,
        "pan_actual": ar.pan_actual,
        "recovery_n": _factory_recoveries,
    }


def pose_off_door(
    pan_now: int,
    tilt_now: int | None,
    *,
    want_pan: int,
    want_tilt: int,
    pan_tol: int,
    tilt_tol: int,
) -> bool:
    """True when rest pose is not door. Ignores firmware frame GET.

    Link 2 XU selector 19 GET_CUR stays 1 after SET 0 and after USB power
    cycle (stored default, Fable 2026-08-05). Using that bit as "off door"
    made capture_loop re-aim every 2 s while pan/tilt already matched
    (REST OFF DOOR recovery#2000+, 2026-08-16, tilt +18000 / pan 450000).
    Real wander still shows up on the axes (Claude: 374400 / −18000).
    """
    try:
        pan_ok = abs(int(pan_now) - int(want_pan)) <= int(pan_tol)
    except (TypeError, ValueError):
        return False
    tilt_ok = True
    if tilt_now is not None:
        try:
            tilt_ok = abs(int(tilt_now) - int(want_tilt)) <= int(tilt_tol)
        except (TypeError, ValueError):
            tilt_ok = True
    return not (pan_ok and tilt_ok)


def _recover_rest_to_door(*, reason: str = "watchdog") -> dict | None:
    """If rest FOV is stuck off door (e.g. chair 300000 after look_around), re-home.

    Factory guard only catches pan≈0. Tours that fail mid-return leave the
    camera on a seat stop — presence then watches the wrong place all night.
    """
    global _last_rest_guard_ts, _rest_recoveries
    if not DOOR_HOME_GUARD or _sweeping.is_set() or watchdogs_paused():
        return None
    now = time.time()
    if now < _hold_until:
        return None
    try:
        with _lease_lock:
            if _lease and float(_lease.get("until") or 0) > now:
                return None
    except Exception:
        pass
    if now - _last_rest_guard_ts < 2.0:
        return None
    pan_now = get_ctrl(V4L_DEV, "pan_absolute")
    if pan_now is None:
        return None
    door_tol = int(os.environ.get("RAIL_CAMERA_DOOR_PAN_TOL", "24000"))
    want = int(DOOR_HOME_PAN)
    cfg = _load_stops()
    door = cfg.get("door") or {}
    want_tilt = int(door.get("tilt", DOOR_HOME_TILT))
    tilt_tol = int(os.environ.get("RAIL_CAMERA_DOOR_TILT_TOL", "8000"))
    tilt_now = get_ctrl(V4L_DEV, "tilt_absolute")
    if not pose_off_door(
        int(pan_now),
        None if tilt_now is None else int(tilt_now),
        want_pan=want,
        want_tilt=want_tilt,
        pan_tol=door_tol,
        tilt_tol=tilt_tol,
    ):
        return None
    # Fresh intentional off-door aim (mid-tour): leave alone until REST_OFF_DOOR_SEC
    if (
        _cmd_pan is not None
        and abs(int(_cmd_pan) - want) > door_tol
        and (now - float(_cmd_pan_ts or 0)) < REST_OFF_DOOR_SEC
    ):
        return None
    # cmd said door but actual drifted, or tilt parked down — recover
    _last_rest_guard_ts = now
    _rest_recoveries += 1
    tilt = want_tilt
    zoom = int(door.get("zoom", DOOR_HOME_ZOOM))
    tol = int(cfg.get("pan_tol", 3600))
    print(
        f"[camera] REST OFF DOOR pan={pan_now} tilt={tilt_now} "
        f"— door {want}/{want_tilt} "
        f"({reason}) recovery#{_rest_recoveries} cmd_pan={_cmd_pan} "
        f"age={round(now - float(_cmd_pan_ts or 0), 1)}s",
        flush=True,
    )
    # Framing may have steered the axes; release only if AI track is live.
    try:
        from .firmware_facelock import facelock_live, firmware_status, release_and_hold

        if facelock_live(firmware_status(V4L_DEV)):
            release_and_hold(V4L_DEV)
            time.sleep(0.25)
    except Exception:
        pass
    ar = aim_door_home(
        V4L_DEV,
        pan=want,
        tilt=tilt,
        zoom=zoom,
        settle_sec=1.4,
        pan_tol=tol,
        retries=8,
    )
    note_intentional_aim(want, hold_sec=0)
    clear_aim_hold()
    return {
        "ok": bool(ar.ok),
        "reason": reason,
        "pan_before": int(pan_now),
        "pan_cmd": want,
        "pan_actual": ar.pan_actual,
        "recovery_n": _rest_recoveries,
    }


def _verify_door_picture(pan: int, tilt: int, zoom: int, tol: int, settle_sec: float) -> dict | None:
    """After a return to the door, ask the PICTURE (door_picture) - the readback said door for five hours on 10-06
    while the picture was on the User's seat. Not the door: head-framing OFF, one real re-home (one step off the door
    pan, then the door), check again. Never moves anywhere but the door. Kill RAIL_DOOR_PICTURE_VERIFY=0."""
    from . import door_picture
    from .firmware_facelock import frame_off

    if not door_picture.enabled():
        return None
    jpg, _meta = get_jpeg()
    first = door_picture.check(jpg) if jpg else {"ok": False, "error": "no frame"}
    out = {"first": first}
    # A frame grabbed while the gimbal is still settling reads "not the door" on a move that worked (10-09, door 5
    # twice). Look again on fresh frames before judging; any one door read settles it. Kill RAIL_DOOR_PICTURE_SETTLE=0.
    if first.get("ok") and not first.get("is_door") and os.environ.get("RAIL_DOOR_PICTURE_SETTLE", "1") != "0":
        tries = max(0, int(os.environ.get("RAIL_DOOR_PICTURE_SETTLE_TRIES", "3")))
        gap = max(0.0, float(os.environ.get("RAIL_DOOR_PICTURE_SETTLE_SEC", "0.7")))
        reads = []
        for _ in range(tries):
            time.sleep(gap)
            _capture_once()
            jpg_n, _m = get_jpeg()
            r = door_picture.check(jpg_n) if jpg_n else {"ok": False, "error": "no frame"}
            reads.append({k: r.get(k) for k in ("ok", "door", "best_other", "is_door")})
            if r.get("ok") and r.get("is_door"):
                first = r
                break
        out["settle_reads"] = reads
        out["settled"] = first
    if first.get("ok") and not first.get("is_door"):
        out["frame_off"] = frame_off(V4L_DEV)
        step = -3600 if pan > 0 else 3600
        ptz_aim(V4L_DEV, pan + step, tilt, zoom=zoom, settle_sec=0.8, pan_tol=tol, retries=1)
        ar2 = aim_door_home(V4L_DEV, pan=pan, tilt=tilt, zoom=zoom, settle_sec=max(settle_sec, 1.8),
                            pan_tol=tol, retries=6)
        _capture_once()
        jpg2, _m2 = get_jpeg()
        out["rehome_ok"] = bool(ar2.ok)
        out["second"] = door_picture.check(jpg2) if jpg2 else {"ok": False, "error": "no frame"}
    try:
        from .firmware_facelock import _audit
        _audit("door_picture", **{k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items()
                                                                          if kk != "error"}) for k, v in out.items()})
    except Exception:
        pass
    return out


def _finish_at_door_home(*, settle_sec: float = 1.6) -> dict:
    """Release facefinder, then force door FOV (450000) — never leave at pan 0.

    Facefinder release often drops the gimbal toward firmware forward (0).
    We release first, wait for firmware to free the gimbal, aim door hard.
    Skip a final release after success — that release re-snaps to factory home.
    """
    cfg = _load_stops()
    door = cfg.get("door") or {}
    pan = int(door.get("pan", DOOR_HOME_PAN))
    tilt = int(door.get("tilt", DOOR_HOME_TILT))
    zoom = int(door.get("zoom", DOOR_HOME_ZOOM))
    tol = int(cfg.get("pan_tol", 3600))
    # Live lock is AI track (mode==1). Selector 19 GET stays 1 after SET 0
    # and must not trigger XU — that was 4× OFF per home (2026-08-26).
    try:
        from .firmware_facelock import facelock_live, firmware_status, release_and_hold

        if facelock_live(firmware_status(V4L_DEV)):
            release_and_hold(V4L_DEV)
            time.sleep(0.3)
            if facelock_live(firmware_status(V4L_DEV)):
                release_and_hold(V4L_DEV)
                time.sleep(0.2)
    except Exception:
        pass
    # If release already dumped us at factory home, aim immediately (no 2nd release)
    ar = aim_door_home(
        V4L_DEV,
        pan=pan,
        tilt=tilt,
        zoom=zoom,
        settle_sec=settle_sec,
        pan_tol=tol,
        retries=6,
    )
    tilt_tol = int(os.environ.get("RAIL_CAMERA_DOOR_TILT_TOL", "8000"))
    bad_tilt = False
    try:
        if ar.tilt_actual is not None and abs(int(ar.tilt_actual) - int(tilt)) > tilt_tol:
            bad_tilt = True
    except (TypeError, ValueError):
        bad_tilt = False
    # If still near firmware center (0), far from door pan, or tilt stuck
    # at seats_1_2 down-angle (−54000), re-aim ONLY (no release)
    if (
        (not ar.ok)
        or pan_error(ar.pan_actual, pan) > tol
        or abs(int(ar.pan_actual or 0)) <= FACTORY_HOME_TOL
        or bad_tilt
    ):
        time.sleep(0.15)
        ar = aim_door_home(
            V4L_DEV,
            pan=pan,
            tilt=tilt,
            zoom=zoom,
            settle_sec=max(settle_sec, 1.8),
            pan_tol=tol,
            retries=8,
        )
    # Mark rest intent as door so off-door rest watchdog does not fight us.
    note_intentional_aim(pan, hold_sec=0)
    clear_aim_hold()
    # No post-success track OFF — was re-snapping to factory desk every home.
    if ar.ok:
        _capture_once()
    picture = _verify_door_picture(pan, tilt, zoom, tol, settle_sec) if ar.ok else None
    return {
        "ok": bool(ar.ok),
        "picture": picture,
        "facelock": False,
        "pan_cmd": pan,
        "pan_actual": ar.pan_actual,
        "tilt_actual": ar.tilt_actual,
        "error": ar.error,
        "note": (
            "door home hold"
            if ar.ok
            else f"door aim failed actual={ar.pan_actual} (0=firmware forward)"
        ),
    }


def _peek_targets(cfg: dict, door_pan: int, stops: list[dict] | None = None) -> dict:
    """Left/right pan targets for two-way peek from door home.

    Prefer named seat stops (desk/chair). If the stop table only has pans on
    one side of the door (this house: both seats are CCW of door 450000),
    mirror the left span across the door for the opposite peek.
    """
    door_pan = int(door_pan)
    by_id = {
        str(s.get("id") or ""): int(s.get("pan") or 0)
        for s in (cfg.get("stops") or [])
        if isinstance(s, dict)
    }
    # Explicit overrides in camera-stops.json
    peek_left = cfg.get("peek_left_pan")
    peek_right = cfg.get("peek_right_pan")
    if peek_left is not None:
        left = wrap_pan(int(peek_left))
    else:
        left = by_id.get("desk") or by_id.get("chair")
        if left is None and stops:
            pans = [int(s.get("pan") or 0) for s in stops]
            left = min(pans) if pans else wrap_pan(door_pan - 180000)
        if left is None:
            left = wrap_pan(door_pan - 180000)
        left = int(left)

    if peek_right is not None:
        right = wrap_pan(int(peek_right))
    else:
        # Seat on the far side of door if any; else chair if it differs from left; else mirror
        right = None
        for sid in ("peek_right", "right", "sofa"):
            if sid in by_id and by_id[sid] != door_pan:
                right = by_id[sid]
                break
        if right is None and "chair" in by_id and by_id["chair"] != left:
            # Both seats often same side of door — still use chair as second peek
            # only when it is meaningfully closer to door than desk (shallower left).
            if abs(by_id["chair"] - door_pan) < abs(left - door_pan):
                right = by_id["chair"]
        if right is None:
            # True opposite side: mirror left offset about door (wrap-safe)
            delta = left - door_pan
            right = wrap_pan(door_pan - delta)
        right = int(right)

    # If left/right collapsed, force a minimum bilateral span
    if abs(wrap_pan(left) - wrap_pan(right)) < 20000:
        left = wrap_pan(door_pan - 180000)
        right = wrap_pan(door_pan + 180000)
    return {
        "door": door_pan,
        "left": wrap_pan(left),
        "right": wrap_pan(right),
    }


def run_smooth_scan(
    stops: list[dict] | None = None,
    *,
    step_pan: int = 36000,
    step_sleep: float = 0.04,
    zoom: int | None = 100,
    include_waypoints: bool = True,
    archive_every: int = 1,
    archive_max: int = 90,
    out_dir: str | None = None,
    budget_sec: float = 10.0,
    two_way: bool | None = None,
    tilt: int | None = None,
    end_pan: int | None = None,
) -> dict:
    """Two-way peek from door: left → home → right → home (body-count orient).

    Design (the User 2026-07-24, two-way 2026-07-24):
      • Start at door home.
      • Smooth pan left (desk/room), capture during move, return door.
      • Smooth pan right (other side), capture, return door.
      • Several frames = **camera motion**, not multiple bodies.
      • Facefind snaps only for unknown bodies (separate tool).
      • Legacy one-arc: pass two_way=False.

    Capture loop pauses under _sweeping. Always ends door home, facefinder OFF.
    """
    global _last_sweep, _last_err
    from .firmware_facelock import release_and_hold

    if two_way is None:
        two_way = os.environ.get("RAIL_CAMERA_TWO_WAY_PEEK", "1") not in (
            "0",
            "false",
            "no",
        )

    cfg = _load_stops()
    door = cfg.get("door") or {"pan": DOOR_HOME_PAN, "tilt": DOOR_HOME_TILT}
    door_pan = int(door.get("pan", DOOR_HOME_PAN))
    # Room pan extremes from stop table (or overrides)
    raw = stops or [
        s
        for s in (cfg.get("stops") or [])
        if str(s.get("id") or "") != "door"
        and int(s.get("pan") or -1) != door_pan
    ]
    if not raw:
        raw = [
            {"id": "desk", "pan": 150000, "tilt": 0},
            {"id": "chair", "pan": 300000, "tilt": 0},
        ]
    peeks = _peek_targets(cfg, door_pan, raw)
    left_pan = int(peeks["left"])
    right_pan = int(peeks["right"])
    if tilt is None:
        tilt = 0
    else:
        tilt = int(tilt)
    # Two peeks need more wall time than one arc (default 10s)
    if two_way and budget_sec <= 5.0:
        budget_sec = float(os.environ.get("RAIL_CAMERA_PEEK_BUDGET_SEC", "10"))

    try:
        release_and_hold(V4L_DEV)
    except Exception:
        pass
    if zoom is not None:
        try:
            set_ctrl(V4L_DEV, "zoom_absolute", int(zoom))
        except Exception:
            pass

    with _lock:
        seq0 = _seq
        ts0 = _last_ts
    _sweeping.set()  # pause idle loop; we capture ourselves
    visited: list[dict] = []
    # (seq, pan_actual, jpeg_bytes)
    archive: list[tuple[int, int | None, bytes]] = []
    last_arch_seq = -1
    arch_stride = max(1, int(archive_every))
    arch_cap = max(8, int(archive_max))
    t_scan0 = time.time()

    def _capture_and_archive() -> None:
        """Grab at ~step rate during pan (background loop is paused)."""
        nonlocal last_arch_seq
        try:
            _capture_once()
        except Exception:
            return
        with _lock:
            seq = _seq
            jpg = _last_jpeg
        if not jpg or seq == last_arch_seq:
            return
        if last_arch_seq >= 0 and (seq - last_arch_seq) < arch_stride:
            return
        pan_a = get_ctrl(V4L_DEV, "pan_absolute")
        archive.append((seq, pan_a, jpg))
        last_arch_seq = seq
        if len(archive) > arch_cap:
            archive[:] = archive[::2][-arch_cap:]

    def _slew(target: int, *, tag: str, settle: float = 0.0) -> None:
        """Step pan continuously toward target; capture every step. No facefind."""
        target = wrap_pan(int(target))
        cur = get_ctrl(V4L_DEV, "pan_absolute")
        if cur is None:
            cur = target
        direction = 1 if target >= cur else -1
        pos = int(cur)
        step = max(3600, int(step_pan))
        sleep_s = max(0.02, float(step_sleep))
        while abs(pos - target) > step:
            # hard budget: abort arc early, still return door
            if time.time() - t_scan0 > float(budget_sec):
                break
            pos = pos + direction * step
            if (direction > 0 and pos > target) or (direction < 0 and pos < target):
                pos = target
            set_ctrl(V4L_DEV, "pan_absolute", wrap_pan(pos))
            set_ctrl(V4L_DEV, "tilt_absolute", tilt)
            time.sleep(sleep_s)
            _capture_and_archive()
        set_ctrl(V4L_DEV, "pan_absolute", target)
        set_ctrl(V4L_DEV, "tilt_absolute", tilt)
        if settle > 0:
            time.sleep(settle)
        _capture_and_archive()
        if include_waypoints:
            visited.append(
                {
                    "id": tag,
                    "pan_cmd": target,
                    "pan_actual": get_ctrl(V4L_DEV, "pan_absolute"),
                }
            )

    def _home_door(*, tag: str = "door_home") -> dict:
        door_ret = _finish_at_door_home(settle_sec=0.45)
        _capture_and_archive()
        if include_waypoints:
            visited.append(
                {
                    "id": tag,
                    "pan_cmd": door_pan,
                    "pan_actual": door_ret.get("pan_actual"),
                }
            )
        return door_ret

    try:
        # Always start at door home
        door_ret = _home_door(tag="door_start")
        if two_way:
            # door → left → door → right → door
            _slew(left_pan, tag="peek_left", settle=0.06)
            door_ret = _home_door(tag="door_after_left")
            if time.time() - t_scan0 < float(budget_sec):
                _slew(right_pan, tag="peek_right", settle=0.06)
            door_ret = _home_door(tag="door_end")
            mode = "two_way_peek"
            arc_meta = {
                "door": door_pan,
                "left": left_pan,
                "right": right_pan,
                "path": "door→left→door→right→door",
            }
            law = (
                "SMOOTH SCAN — two-way peek from door.\n"
                "Path: door home → LEFT → door → RIGHT → door.\n"
                "Several frames during each pan = CAMERA MOVEMENT (ego-motion), "
                "NOT multiple bodies.\n"
                "Count bodies only when distinct in a single frame or stably "
                "co-present after ego-motion is accounted for.\n"
                "Facefind snaps only for unknown bodies (separate tool).\n"
            )
            note = (
                f"Two-way peek L={left_pan} R={right_pan} door={door_pan} "
                f"in ~{{wall}}s (budget {budget_sec}s). "
                "Ghosts=heading/ego-motion; do not parse smear as multi-body."
            )
        else:
            # Legacy one continuous arc (far → door)
            pans = [int(s.get("pan") or 0) for s in raw] + [door_pan]
            if end_pan is not None:
                pans.append(int(end_pan))
            pan_lo, pan_hi = min(pans), max(pans)
            if end_pan is not None:
                ep = int(end_pan)
                if abs(ep - pan_hi) <= abs(ep - pan_lo):
                    arc_start, arc_end = pan_lo, ep
                else:
                    arc_start, arc_end = pan_hi, ep
            elif abs(door_pan - pan_hi) <= abs(door_pan - pan_lo):
                arc_start, arc_end = pan_lo, door_pan
            else:
                arc_start, arc_end = pan_hi, door_pan
            set_ctrl(V4L_DEV, "pan_absolute", wrap_pan(arc_start))
            set_ctrl(V4L_DEV, "tilt_absolute", tilt)
            time.sleep(0.12)
            _capture_and_archive()
            if include_waypoints:
                visited.append(
                    {
                        "id": "arc_start",
                        "pan_cmd": arc_start,
                        "pan_actual": get_ctrl(V4L_DEV, "pan_absolute"),
                    }
                )
            _slew(arc_end, tag="arc_end_door", settle=0.08)
            door_ret = _home_door(tag="door_end")
            mode = "one_arc_continuous"
            arc_meta = {"start": arc_start, "end": arc_end, "door": door_pan}
            law = (
                "SMOOTH SCAN — one continuous pan.\n"
                "Several frames = CAMERA MOVEMENT (ego-motion), NOT multiple bodies.\n"
                "Ghost trails = heading of movers or pan smear; do not invent headcount "
                "from blur or double-image.\n"
                "Count bodies only when the same person is distinct in a single frame "
                "or stably co-present across frames after ego-motion is accounted for.\n"
                "Facefind snaps only for unknown bodies (separate tool).\n"
            )
            note = (
                f"One continuous arc in ~{{wall}}s (budget {budget_sec}s). "
                "Ghosts=heading/ego-motion; do not parse multi-frame smear as multi-body."
            )

        with _lock:
            seq1 = _seq
            ts1 = _last_ts

        dest = Path(
            out_dir
            or os.environ.get(
                "RAIL_CAMERA_SCAN_DIR",
                str(
                    _paths.DATA / "work/oracle_live/smooth_scan"
                ),
            )
        )
        dest.mkdir(parents=True, exist_ok=True)
        jpegs = [j for _, _, j in archive]
        motion: dict = {}
        if jpegs:
            try:
                from .motion_vision import build_motion_pack

                motion = build_motion_pack(
                    jpegs, out_dir=dest, scale=0.35, write_video=True, fps=15.0
                )
                if isinstance(motion.get("heading"), dict):
                    motion["heading"]["parse_rule"] = (
                        "frames_are_camera_motion_not_multiple_bodies"
                    )
            except Exception as me:
                motion = {"ok": False, "error": str(me)[:200]}
            for seq, pan_a, jpg in archive[:: max(1, len(archive) // 8)][:8]:
                (dest / f"kf_seq{seq}_pan{pan_a}.jpg").write_bytes(jpg)
            (dest / "SCAN_LAW.txt").write_text(law, encoding="utf-8")

        span = (
            round(float(ts1 or 0) - float(ts0 or 0), 3) if ts0 and ts1 else None
        )
        wall = round(time.time() - t_scan0, 3)
        result = {
            "ok": True,
            "tool": "smooth_scan",
            "ts": time.time(),
            "mode": mode,
            "two_way": bool(two_way),
            "arc": arc_meta,
            "peeks": peeks if two_way else None,
            "seq_start": seq0,
            "seq_end": seq1,
            "n_frames_approx": max(0, int(seq1) - int(seq0)),
            "n_archived": len(archive),
            "span_sec": span,
            "wall_sec": wall,
            "budget_sec": float(budget_sec),
            "waypoints": visited,
            "door_return": door_ret,
            "door_ok": bool(door_ret.get("ok")),
            "scan_dir": str(dest),
            "motion": {
                "ok": motion.get("ok"),
                "heading": motion.get("heading"),
                "trail": motion.get("trail"),
                "heat": motion.get("heat"),
                "heading_img": motion.get("heading_img"),
                "video": motion.get("video"),
            }
            if motion
            else None,
            "parse_rule": "frames_are_camera_motion_not_multiple_bodies",
            "note": note.format(wall=wall),
        }
        _last_sweep = {
            "ts": result["ts"],
            "n_ok": len(visited),
            "n_stops": len(visited),
            "ids": [v.get("id") for v in visited],
            "door_pan": door_ret.get("pan_actual"),
            "tool": "smooth_scan",
            "mode": mode,
            "n_archived": len(archive),
            "wall_sec": wall,
        }
        return result
    except Exception as e:
        _last_err = str(e)[:300]
        try:
            door_ret = _finish_at_door_home(settle_sec=0.8)
        except Exception:
            door_ret = {"ok": False}
        return {
            "ok": False,
            "tool": "smooth_scan",
            "error": str(e)[:300],
            "door_return": door_ret,
        }
    finally:
        try:
            _finish_at_door_home(settle_sec=0.6)
        except Exception:
            pass
        _sweeping.clear()


def run_video_sweep(
    *,
    start_pan: int | None = None,
    end_pan: int | None = None,
    tilt: int | None = None,
    zoom: int | None = None,
    budget_sec: float = 20.0,
    fps: float = 30.0,
    archive_max: int = 300,
    out_dir: str | None = None,
) -> dict:
    """One pan command + keep recording. Not a staircase of settled stills.

    Live VIDEO and empty-ref VIDEO share camera-stops.json video_sweep (tilt −5
    unless that block says otherwise). Diff = body only at the same pitch.
    FaceFinder off. Always ends door home (tilt 0).
    """
    global _last_sweep, _last_err
    from .firmware_facelock import release_and_hold
    from .sweep_pose import video_sweep_pose

    pose = video_sweep_pose()
    start_pan = wrap_pan(int(pose["start_pan"] if start_pan is None else start_pan))
    end_pan = wrap_pan(int(pose["end_pan"] if end_pan is None else end_pan))
    tilt = int(pose["tilt"] if tilt is None else tilt)
    if zoom is None:
        zoom = int(pose["zoom"])
    period = 1.0 / max(5.0, float(fps))
    arch_cap = max(16, int(archive_max))
    budget_sec = float(budget_sec)

    try:
        release_and_hold(V4L_DEV)
    except Exception:
        pass
    if zoom is not None:
        try:
            set_ctrl(V4L_DEV, "zoom_absolute", int(zoom))
        except Exception:
            pass

    with _lock:
        seq0 = _seq
        ts0 = _last_ts
    _sweeping.set()
    archive: list[tuple[int, int | None, int | None, bytes]] = []
    last_arch_seq = -1
    t0 = time.time()
    tilt_cmd = tilt

    def _capture_and_archive() -> None:
        nonlocal last_arch_seq
        try:
            _capture_once()
        except Exception:
            return
        with _lock:
            seq = _seq
            jpg = _last_jpeg
        if not jpg or seq == last_arch_seq:
            return
        pan_a = get_ctrl(V4L_DEV, "pan_absolute")
        tilt_a = get_ctrl(V4L_DEV, "tilt_absolute")
        archive.append((seq, pan_a, tilt_a if tilt_a is not None else tilt_cmd, jpg))
        last_arch_seq = seq
        if len(archive) > arch_cap:
            archive[:] = archive[::2][-arch_cap:]

    def _pace() -> None:
        left = period - (time.time() - loop_t)
        if left > 0:
            time.sleep(left)

    try:
        # One blocking set-ctrl for the whole arc starves JPEG + pan readback
        # (measured 2026-08-15: 67 frames but only pans 0 and 514800).
        # Step 2° and grab every step — that is the video.
        step = 7200  # 2°
        tilt_cmd = tilt
        loop_t = time.time()
        set_ctrl(V4L_DEV, "tilt_absolute", tilt, timeout=8.0)

        def _step_to(target: int, *, tag: str) -> None:
            nonlocal tilt_cmd, loop_t
            target = wrap_pan(int(target))
            cur = get_ctrl(V4L_DEV, "pan_absolute")
            if cur is None:
                cur = target
            direction = 1 if target >= cur else -1
            pos = int(cur)
            while abs(pos - target) > step:
                if time.time() - t0 > budget_sec + 12.0:
                    break
                pos = pos + direction * step
                if (direction > 0 and pos > target) or (direction < 0 and pos < target):
                    pos = target
                set_ctrl(V4L_DEV, "pan_absolute", wrap_pan(pos), timeout=4.0)
                set_ctrl(V4L_DEV, "tilt_absolute", tilt_cmd, timeout=4.0)
                loop_t = time.time()
                _capture_and_archive()
                _pace()
            set_ctrl(V4L_DEV, "pan_absolute", target, timeout=4.0)
            set_ctrl(V4L_DEV, "tilt_absolute", tilt_cmd, timeout=4.0)
            time.sleep(0.08)
            loop_t = time.time()
            _capture_and_archive()
            _pace()

        _step_to(start_pan, tag="start")
        for _ in range(2):
            loop_t = time.time()
            _capture_and_archive()
            _pace()
        _step_to(end_pan, tag="arc")

        # Measured sitter holds (same path as empty-ref video). Seat 12 −15°,
        # chair-under-cam −20°. −5° main arc does not see those laps.
        for sit in pose.get("sitters") or []:
            try:
                s_pan = wrap_pan(int(sit["pan"]))
                s_tilt = int(sit["tilt"])
                s_hold = float(sit.get("hold_sec") or 1.0)
            except (TypeError, ValueError, KeyError):
                continue
            tilt_cmd = s_tilt
            set_ctrl(V4L_DEV, "tilt_absolute", s_tilt, timeout=8.0)
            _step_to(s_pan, tag=str(sit.get("id") or "sitter"))
            t_sit = time.time()
            while time.time() - t_sit < s_hold:
                loop_t = time.time()
                _capture_and_archive()
                _pace()

        dest = Path(
            out_dir
            or os.environ.get(
                "RAIL_CAMERA_SCAN_DIR",
                str(
                    _paths.DATA / "work/oracle_live/smooth_scan"
                ),
            )
        )
        dest.mkdir(parents=True, exist_ok=True)
        frames_meta: list[dict] = []
        for i, (seq, pan_a, tilt_a, jpg) in enumerate(archive):
            name = f"v{i:03d}_pan{pan_a}_t{tilt_a}.jpg"
            (dest / name).write_bytes(jpg)
            frames_meta.append(
                {
                    "i": i,
                    "seq": int(seq),
                    "pan": pan_a,
                    "tilt": tilt_a,
                    "file": name,
                }
            )
        law = (
            "VIDEO SWEEP — one pan + sitter holds, frames while the gimbal moves.\n"
            "Live count and empty refs run this SAME path.\n"
            "Live minus these frames = the body. Not station stills.\n"
            "Several frames = CAMERA MOVEMENT, not multiple bodies.\n"
        )
        (dest / "SCAN_LAW.txt").write_text(law, encoding="utf-8")
        manifest = {
            "kind": "empty_sweep_video",
            "tilt": tilt,
            "tilt_deg": round(tilt / 3600.0, 2),
            "start_pan": start_pan,
            "end_pan": end_pan,
            "zoom": zoom,
            "sitters": pose.get("sitters") or [],
            "fps_target": float(fps),
            "n_frames": len(frames_meta),
            "wall_sec": round(time.time() - t0, 3),
            "frames": frames_meta,
        }
        (dest / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        motion: dict = {}
        jpegs = [j for _, _, _, j in archive]
        if jpegs:
            try:
                from .motion_vision import build_motion_pack

                motion = build_motion_pack(
                    jpegs, out_dir=dest, scale=0.35, write_video=True, fps=min(15.0, fps)
                )
            except Exception as me:
                motion = {"ok": False, "error": str(me)[:200]}

        door_ret = _finish_at_door_home(settle_sec=0.45)
        with _lock:
            seq1 = _seq
        wall = round(time.time() - t0, 3)
        result = {
            "ok": True,
            "tool": "video_sweep",
            "mode": "one_pan_video",
            "ts": time.time(),
            "arc": {
                "start": start_pan,
                "end": end_pan,
                "tilt": tilt,
                "sitters": pose.get("sitters") or [],
            },
            "seq_start": seq0,
            "seq_end": seq1,
            "n_archived": len(archive),
            "span_sec": round(float((_last_ts or 0) - (ts0 or 0)), 3) if ts0 else None,
            "wall_sec": wall,
            "budget_sec": budget_sec,
            "scan_dir": str(dest),
            "manifest": str(dest / "manifest.json"),
            "door_return": door_ret,
            "door_ok": bool(door_ret.get("ok")),
            "motion": {
                "ok": motion.get("ok"),
                "video": motion.get("video"),
            }
            if motion
            else None,
            "parse_rule": "frames_are_camera_motion_not_multiple_bodies",
            "note": (
                f"One-pan video {start_pan}→{end_pan} tilt={tilt} "
                f"{len(archive)} frames in {wall}s."
            ),
        }
        _last_sweep = {
            "ts": result["ts"],
            "tool": "video_sweep",
            "mode": "one_pan_video",
            "n_archived": len(archive),
            "wall_sec": wall,
            "door_pan": door_ret.get("pan_actual"),
        }
        return result
    except Exception as e:
        _last_err = str(e)[:300]
        try:
            door_ret = _finish_at_door_home(settle_sec=0.8)
        except Exception:
            door_ret = {"ok": False}
        return {
            "ok": False,
            "tool": "video_sweep",
            "error": str(e)[:300],
            "door_return": door_ret,
        }
    finally:
        try:
            _finish_at_door_home(settle_sec=0.5)
        except Exception:
            pass
        _sweeping.clear()


def run_error_check_sweep(
    *,
    fps: float = 8.0,
    n: int = 96,
    budget_sec: float = 12.0,
    out_dir: str | None = None,
) -> dict:
    """Deprecated video path. Body count is station stills on :8191."""
    return {
        "ok": False,
        "tool": "error-check",
        "facefinder": False,
        "error": "not_video",
        "note": (
            "error-check is one still per programmed station plus one "
            "−10° snap at hour 12 via POST :8191/v1/error_check. "
            "No 4 fps / 8 fps video."
        ),
    }


def run_sweep(stops: list[dict] | None = None) -> dict:
    """Multi-stop PTZ capture under exclusive lock. Returns frames as base64 JPEG."""
    global _last_sweep, _last_err
    cfg = _load_stops()
    settle = float(cfg.get("settle_sec", 1.4))
    tol = int(cfg.get("pan_tol", 3600))
    door = cfg.get("door") or {"pan": DOOR_HOME_PAN, "tilt": DOOR_HOME_TILT}
    stop_list = stops or cfg.get("stops") or []
    sweep_zoom = int(cfg.get("sweep_zoom", 100))
    out_frames: list[dict] = []
    try:
        from .firmware_facelock import release_and_hold

        release_and_hold(V4L_DEV)
    except Exception:
        pass
    _sweeping.set()
    try:
        time.sleep(0.1)
        for s in stop_list:
            sid = str(s.get("id") or s.get("pan"))
            pan = int(s["pan"])
            tilt = int(s.get("tilt") or 0)
            ar = ptz_aim(
                V4L_DEV,
                pan,
                tilt,
                zoom=sweep_zoom,
                settle_sec=settle,
                pan_tol=tol,
            )
            if not ar.ok:
                out_frames.append(
                    {
                        "id": sid,
                        "ok": False,
                        "pan_cmd": pan,
                        "pan_actual": ar.pan_actual,
                        "error": ar.error,
                    }
                )
                continue
            if not _capture_once():
                out_frames.append(
                    {
                        "id": sid,
                        "ok": False,
                        "pan_cmd": pan,
                        "pan_actual": ar.pan_actual,
                        "error": _last_err or "capture failed",
                    }
                )
                continue
            with _lock:
                jpg = _last_jpeg
                ts = _last_ts
                seq = _seq
                sha = _last_sha
            out_frames.append(
                {
                    "id": sid,
                    "ok": True,
                    "pan_cmd": pan,
                    "pan_actual": ar.pan_actual,
                    "tilt_cmd": tilt,
                    "tilt_actual": ar.tilt_actual,
                    "capture_ts": ts,
                    "seq": seq,
                    "sha": sha,
                    "jpeg_b64": base64.b64encode(jpg).decode() if jpg else None,
                }
            )
        # Always end at door home (450000), never firmware forward 0
        door_ret = _finish_at_door_home(settle_sec=max(settle, 1.6))
        n_ok = sum(1 for f in out_frames if f.get("ok"))
        result = {
            "ok": n_ok > 0,
            "ts": time.time(),
            "n_stops": len(stop_list),
            "n_ok": n_ok,
            "door_return": door_ret,
            "frames": out_frames,
        }
        _last_sweep = {
            "ts": result["ts"],
            "n_ok": n_ok,
            "n_stops": len(stop_list),
            "ids": [f.get("id") for f in out_frames if f.get("ok")],
            "door_pan": door_ret.get("pan_actual"),
        }
        return result
    finally:
        # Ensure door even if loop raised
        try:
            _finish_at_door_home()
        except Exception:
            pass
        _sweeping.clear()


def _look_around_stops(
    stops: list[dict] | None = None,
    stop_ids: list | None = None,
) -> list[dict]:
    """Room FOV stops only — door is HOLD destination, never a look stop."""
    cfg = _load_stops()
    # Default few room stops only (not full wrap set — board/Cosmos want a small grid)
    room_ids = ("desk", "another person")
    default_stops = [
        s for s in (cfg.get("stops") or []) if s.get("id") in room_ids
    ] or [
        {"id": "desk", "pan": 150000, "tilt": 0},
        {"id": "chair", "pan": 300000, "tilt": 0},
    ]
    if not stops and stop_ids:
        want = {str(x) for x in stop_ids}
        stops = [s for s in default_stops if s.get("id") in want] or default_stops
    if not stops:
        stops = default_stops
    door_pan = int((cfg.get("door") or {}).get("pan", DOOR_HOME_PAN))
    stops = [
        s
        for s in stops
        if str(s.get("id") or "") != "door" and int(s.get("pan") or -1) != door_pan
    ]
    return stops or default_stops


def run_look_around(
    *,
    stops: list[dict] | None = None,
    stop_ids: list | None = None,
    engage_sec: float = 1.8,
    settle_sec: float | None = None,
    use_facefinder: bool = True,
) -> dict:
    """On-demand look-around tool (NOT a routine patrol).

    At each room stop:
      release facefinder → aim → firmware facefinder ON → engage → capture → release
    Always finish: facefinder OFF + door home (450000). Never leave locked on a face.
    """
    global _last_sweep, _last_err
    from .firmware_facelock import firmware_track, release_and_hold

    cfg = _load_stops()
    settle = float(
        settle_sec if settle_sec is not None else (cfg.get("settle_sec") or 1.2)
    )
    door_pan = int((cfg.get("door") or {}).get("pan", DOOR_HOME_PAN))
    stop_list = _look_around_stops(stops, stop_ids)
    engage = max(0.4, float(engage_sec))
    out_frames: list[dict] = []
    facelock_events: list[dict] = []

    try:
        release_and_hold(V4L_DEV)
    except Exception as e:
        facelock_events.append({"op": "pre_release", "ok": False, "err": str(e)[:80]})

    _sweeping.set()
    try:
        # Two-way peek pattern: door → stop → door → stop → door
        # (never chain seats without re-homing — the User 2026-07-24)
        door_ret = _finish_at_door_home(settle_sec=max(settle * 0.5, 0.6))
        facelock_events.append(
            {"stop": "door_start", "op": "home", "ok": bool(door_ret.get("ok"))}
        )
        for s in stop_list:
            sid = str(s.get("id") or s.get("pan"))
            pan = int(s["pan"])
            tilt = int(s.get("tilt") or 0)
            # free gimbal before absolute aim (facefinder holds pan otherwise)
            rel = release_and_hold(V4L_DEV)
            facelock_events.append({"stop": sid, "op": "release_before_aim", "ok": rel.get("ok")})
            ar = ptz_aim(V4L_DEV, pan, tilt, settle_sec=settle)
            if not ar.ok:
                out_frames.append(
                    {
                        "id": sid,
                        "ok": False,
                        "pan_cmd": pan,
                        "pan_actual": ar.pan_actual,
                        "error": ar.error,
                        "facefinder": False,
                    }
                )
                # still re-home so next peek starts from door
                _finish_at_door_home(settle_sec=max(settle * 0.5, 0.5))
                continue
            ff_on = False
            try:
                if use_facefinder:
                    ff_on = bool(firmware_track(True, V4L_DEV))
                    facelock_events.append(
                        {"stop": sid, "op": "facefinder_on", "ok": ff_on}
                    )
                    time.sleep(engage)
                else:
                    time.sleep(min(0.6, engage))
                _capture_once()
            finally:
                # ALWAYS release facefinder before next stop or door
                rel2 = release_and_hold(V4L_DEV)
                facelock_events.append(
                    {"stop": sid, "op": "facefinder_off", "ok": rel2.get("ok")}
                )
            with _lock:
                jpg = _last_jpeg
                ts = _last_ts
                seq = _seq
                sha = _last_sha
            # Always persist stop JPEG on disk — HTTP b64 is secondary (can be huge / dropped)
            path_str = None
            if jpg:
                try:
                    frame_dir = Path(
                        os.environ.get(
                            "RAIL_LOOKAROUND_FRAME_DIR",
                            str(
                                _paths.DATA / "work/oracle_live/look_around_frames"
                            ),
                        )
                    )
                    frame_dir.mkdir(parents=True, exist_ok=True)
                    fp = frame_dir / f"{sid}.jpg"
                    fp.write_bytes(jpg)
                    path_str = str(fp.resolve())
                except Exception:
                    path_str = None
            out_frames.append(
                {
                    "id": sid,
                    "ok": bool(jpg),
                    "pan_cmd": pan,
                    "pan_actual": ar.pan_actual,
                    "tilt_cmd": tilt,
                    "tilt_actual": ar.tilt_actual,
                    "capture_ts": ts,
                    "seq": seq,
                    "sha": sha,
                    "facefinder": bool(use_facefinder and ff_on),
                    "engage_sec": engage if use_facefinder else 0.0,
                    "path": path_str,
                    # keep b64 for local stitch; clients may ignore size
                    "jpeg_b64": base64.b64encode(jpg).decode() if jpg else None,
                }
            )
            # Re-home door between peeks (two-way: left then right from door)
            mid = _finish_at_door_home(settle_sec=max(settle * 0.5, 0.55))
            facelock_events.append(
                {
                    "stop": f"door_after_{sid}",
                    "op": "home",
                    "ok": bool(mid.get("ok")),
                    "pan_actual": mid.get("pan_actual"),
                }
            )
        # Critical: facefinder OFF + door home (450000), never firmware pan 0
        door_ret = _finish_at_door_home(settle_sec=max(settle, 1.2))
        pan_actual = door_ret.get("pan_actual")
        if pan_actual is None or abs(int(pan_actual) - door_pan) > 8000:
            door_ret = _finish_at_door_home(settle_sec=max(settle, 1.5))
        n_ok = sum(1 for f in out_frames if f.get("ok"))
        result = {
            "ok": n_ok > 0,
            "tool": "look_around",
            "routine": False,
            "mode": "two_way_peek",
            "path": "door→stop→door→…→door",
            "ts": time.time(),
            "n_stops": len(stop_list),
            "n_ok": n_ok,
            "use_facefinder": use_facefinder,
            "engage_sec": engage,
            "facelock": False,
            "facelock_events": facelock_events[-40:],
            "frames": out_frames,
            "door_return": door_ret,
            "door_ok": bool(door_ret.get("ok")),
            "door_pan": door_ret.get("pan_actual"),
            "default_hold": "door",
            "note": (
                "look-around two-way peek: door → each seat → door between peeks, "
                "facefinder ON only at seat; always end door home"
            ),
        }
        _last_sweep = {
            "ts": result["ts"],
            "n_ok": n_ok,
            "n_stops": len(stop_list),
            "ids": [f.get("id") for f in out_frames if f.get("ok")],
            "door_pan": door_ret.get("pan_actual"),
            "tool": "look_around",
        }
        return result
    except Exception as e:
        try:
            release_and_hold(V4L_DEV)
        except Exception:
            pass
        try:
            door_ret = _finish_at_door_home()
        except Exception:
            door_ret = {"ok": False}
        return {
            "ok": False,
            "tool": "look_around",
            "routine": False,
            "error": str(e)[:300],
            "frames": out_frames,
            "facelock": False,
            "door_return": door_ret,
            "door_ok": bool(door_ret.get("ok")),
            "default_hold": "door",
            "note": "look-around failed — forced facefinder OFF + door home",
        }
    finally:
        try:
            release_and_hold(V4L_DEV)
        except Exception:
            pass
        try:
            _finish_at_door_home()
        except Exception:
            pass
        _sweeping.clear()


# ROUND 46 (the local review of kit crissa-063a2813, 2026-10-23, the launch blocker): the grabber answered any web page
# open in the buyer's browser. /v1/mjpeg carried `Access-Control-Allow-Origin: *`, so any site could read the room
# camera, and a cross-site form post could aim it. Every do_* now asks the guard first (Host and Origin must be this
# machine; a cross-site no-Origin GET such as an <img> elsewhere needs a loopback Referer) and a response names only a
# loopback Origin back, never `*`. The launcher, presence and the player call with urllib and send none of those
# headers: they pass. Kill: CRISSA_LOOPBACK_GUARD=0 (the old open port and the old `*`).
def _cors_origin(headers) -> str:
    """Access-Control-Allow-Origin here: the loopback Origin echoed, '' = no header; `*` only under the kill."""
    return _guard.allow_origin(headers) if _guard.guard_on() else "*"


def _own_names(bind) -> tuple:
    """Round 46: a server bound to one named address (RAIL_CAMERA_HOST) answers to that name as well as to loopback;
    a wildcard bind (0.0.0.0, ::) adds nothing, so a LAN caller by name is refused until the install names it."""
    b = str(bind or "").strip().strip("[]").lower()
    return () if b in ("", "0.0.0.0", "::", "*") else (b,)


# Round 46: a Content-Length of -1 read the socket until the peer closed (rfile.read(-1)), and a word in it was a 500.
# The guard refuses both with a 400 before anything reads. No size cap by default: the biggest body here is a request,
# not a picture, and the guard already keeps the web out; an install that wants one names it in bytes
# (RAIL_CAMERA_BODY_MAX). Kill: CRISSA_PRESENCE_BODY_CHECK=0 (the old int() read).
def _body_check_on() -> bool:
    return os.environ.get("CRISSA_PRESENCE_BODY_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def _body_cap() -> int | None:
    try:
        n = int(os.environ.get("RAIL_CAMERA_BODY_MAX", "").strip() or 0)
    except ValueError:
        return None
    return n if n > 0 else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _guarded(self) -> bool:
        return _guard.check(self, names=_own_names(HOST))

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Client hung up (Inspector tab, short timeout). Not a camera fault.
            return

    def do_GET(self):
        if not self._guarded():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path in ("/health", "/v1/health"):
            h = health()
            return self._json(200 if h["ok"] else 503, h)
        if path in ("/firmware", "/v1/firmware", "/v1/firmware/status"):
            st = _firmware_status_safe()
            st["pan_actual"] = get_ctrl(V4L_DEV, "pan_absolute")
            st["fw_audit_tail"] = _fw_audit_safe(24)
            st["http_audit_tail"] = list(_http_audit)[-24:]
            st["factory_recoveries"] = _factory_recoveries
            return self._json(200, st)
        if path in ("/snapshot", "/frame.jpg"):
            jpg, meta = get_jpeg()
            if not jpg:
                return self._json(
                    503, {"ok": False, "error": meta.get("error") or "no frame"}
                )
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Capture-Ts", str(meta.get("capture_ts") or 0))
            self.send_header("X-Frame-Seq", str(meta.get("seq") or 0))
            self.send_header("X-Frame-Age-Sec", str(meta.get("frame_age_sec") or ""))
            self.send_header("X-Capture-Ok", "1" if meta.get("ok") else "0")
            self.send_header("X-Frame-Sha", str(meta.get("sha") or ""))
            self.end_headers()
            self.wfile.write(jpg)
            return
        # Always-on door video for board / analysis (low-res MJPEG).
        # the User: "this video should be playing all the time"
        if path in (
            "/mjpeg",
            "/stream",
            "/v1/mjpeg",
            "/v1/stream",
            "/v1/stream.mjpg",
        ):
            scale = float((qs.get("scale", ["0.5"])[0] or "0.5"))
            scale = max(0.2, min(1.0, scale))
            # Target stream fps (capture stays at HZ; we subsample for clients)
            try:
                stream_hz = float((qs.get("hz", ["12"])[0] or "12"))
            except ValueError:
                stream_hz = 12.0
            stream_hz = max(2.0, min(30.0, stream_hz))
            period = 1.0 / stream_hz
            boundary = b"frame"
            self.send_response(200)
            self.send_header(
                "Content-Type", f"multipart/x-mixed-replace; boundary={boundary.decode()}"
            )
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            _acao = _cors_origin(self.headers)
            if _acao:
                self.send_header("Access-Control-Allow-Origin", _acao)
                self.send_header("Vary", "Origin")
            self.end_headers()
            last_seq = -1
            try:
                while not _stop.is_set():
                    t0 = time.time()
                    jpg, meta = get_jpeg()
                    seq = int(meta.get("seq") or 0)
                    if jpg and seq != last_seq:
                        last_seq = seq
                        out = jpg
                        if scale < 0.99:
                            try:
                                import numpy as np

                                arr = np.frombuffer(jpg, dtype=np.uint8)
                                im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                                if im is not None:
                                    h, w = im.shape[:2]
                                    im = cv2.resize(
                                        im,
                                        (max(1, int(w * scale)), max(1, int(h * scale))),
                                        interpolation=cv2.INTER_AREA,
                                    )
                                    ok, buf = cv2.imencode(
                                        ".jpg",
                                        im,
                                        [int(cv2.IMWRITE_JPEG_QUALITY), 70],
                                    )
                                    if ok:
                                        out = buf.tobytes()
                            except Exception:
                                out = jpg
                        header = (
                            b"--" + boundary + b"\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(out)).encode() + b"\r\n"
                            b"X-Frame-Seq: " + str(seq).encode() + b"\r\n"
                            b"\r\n"
                        )
                        self.wfile.write(header)
                        self.wfile.write(out)
                        self.wfile.write(b"\r\n")
                        try:
                            self.wfile.flush()
                        except Exception:
                            break
                    # sleep remainder of period
                    elapsed = time.time() - t0
                    time.sleep(max(0.0, period - elapsed))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception:
                pass
            return
        if path in ("/buffer/meta", "/v1/buffer/meta"):
            return self._json(200, buffer_meta())
        # Rolling raw on /data (security) — not the 5s RAM ring
        if path in (
            "/records/raw",
            "/v1/records/raw",
            "/v1/records/raw/status",
        ):
            try:
                from .security_raw import status as _sec_status

                return self._json(200, _sec_status())
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:200]})
        # Timeline triggers: pointers into the always-on 30fps ring
        if path in ("/triggers", "/v1/triggers"):
            since = qs.get("since_ts", [None])[0]
            since_ts = float(since) if since not in (None, "") else None
            mx = int((qs.get("max", ["32"])[0] or "32"))
            return self._json(200, list_triggers(since_ts=since_ts, max_n=mx))
        if path in ("/energy", "/v1/energy"):
            since_s = qs.get("since_seq", [None])[0]
            max_s = qs.get("max", [None])[0]
            try:
                since_seq = int(since_s) if since_s not in (None, "") else None
                max_n = int(max_s) if max_s not in (None, "") else None
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "bad since_seq or max"})
            return self._json(200, list_energy(since_seq=since_seq, max_n=max_n))
        # One or more frames by seq (Gemma loads only these after a trigger)
        if path in ("/frame", "/v1/frame", "/v1/buffer/frame"):
            seq_s = qs.get("seq", [None])[0]
            seqs_s = qs.get("seqs", qs.get("seq_list", [None]))[0]
            if seqs_s:
                try:
                    seqs = [int(x) for x in str(seqs_s).replace(";", ",").split(",") if x.strip()]
                except ValueError:
                    return self._json(400, {"ok": False, "error": "bad seqs"})
                frames = get_frames_by_seqs(seqs)
                return self._json(
                    200,
                    {
                        "ok": bool(frames),
                        "n": len(frames),
                        "requested": seqs,
                        "frames": frames,
                        "missing": [s for s in seqs if s not in {f["seq"] for f in frames}],
                    },
                )
            if seq_s is None or seq_s == "":
                return self._json(400, {"ok": False, "error": "need seq= or seqs="})
            fr = get_frame_by_seq(int(seq_s))
            if not fr:
                return self._json(
                    404,
                    {
                        "ok": False,
                        "error": "seq not in ring (aged out of 5s buffer)",
                        "seq": int(seq_s),
                    },
                )
            return self._json(200, fr)
        if path in ("/buffer", "/v1/buffer"):
            # Face ID / VLM / Inspector ring buffer
            since = qs.get("since_seq", [None])[0]
            since_seq = int(since) if since not in (None, "") else None
            # seq= / seqs= also work on /buffer for one-stop shopping
            seq_s = qs.get("seq", [None])[0]
            seqs_s = qs.get("seqs", [None])[0]
            if seq_s or seqs_s:
                if seqs_s:
                    seqs = [int(x) for x in str(seqs_s).replace(";", ",").split(",") if x.strip()]
                    frames = get_frames_by_seqs(seqs)
                    return self._json(200, {"ok": bool(frames), "n": len(frames), "frames": frames})
                fr = get_frame_by_seq(int(seq_s))
                return self._json(200 if fr else 404, fr or {"ok": False, "error": "seq aged out"})
            mx = qs.get("max", qs.get("max_frames", [None]))[0]
            max_frames = int(mx) if mx not in (None, "") else None
            stride = int(qs.get("stride", ["1"])[0] or 1)
            # meta_only=1 → no jpeg payload
            meta_only = (qs.get("meta_only", ["0"])[0] or "0") in ("1", "true", "yes")
            # default: if no since_seq and no max, return meta only to avoid 20MB accidents
            if since_seq is None and max_frames is None and not meta_only:
                # Full buffer is intentional for Inspector when max set; else meta + hint
                if (qs.get("full", ["0"])[0] or "0") not in ("1", "true", "yes"):
                    m = buffer_meta()
                    m["hint"] = (
                        "pass since_seq=N (delta), max=150&full=1 (full ring), "
                        "seq=N / seqs=a,b,c (timeline pointer), or meta_only=1; "
                        "GET /v1/triggers for motion/face stamps"
                    )
                    return self._json(200, m)
                max_frames = BUFFER_LEN
            body = get_buffer(
                since_seq=since_seq,
                max_frames=max_frames,
                stride=stride,
                include_jpeg=not meta_only,
            )
            return self._json(200, body)
        if path in ("/sweep/status", "/v1/sweep/status"):
            return self._json(200, _last_sweep or {"ok": False, "error": "no sweep yet"})
        if path in ("/coverage", "/v1/coverage"):
            try:
                a = float((qs.get("from") or qs.get("from_ts") or [0])[0] or 0)
                b = float((qs.get("to") or qs.get("to_ts") or [0])[0] or 0)
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "from= and to= unix"})
            return self._json(200, door_coverage(a, b))
        return self._json(404, {"ok": False})

    def do_POST(self):
        if not self._guarded():
            return
        path = self.path.split("?")[0]
        if _body_check_on():
            n = _guard.body_length(self.headers, _body_cap())
            if n is None:
                return self._json(400, {"ok": False, "error": "bad Content-Length"})
        else:
            n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if _body_check_on():
            # round 46: a JSON body that is not an object was a 500 from the first body.get(); it is a 400 now
            try:
                _top = json.loads(raw or b"{}")
            except Exception:
                _top = {}                       # unparsable: each route keeps its own answer, as before
            if not isinstance(_top, dict):
                return self._json(400, {"ok": False, "error": "the body is a JSON object"})
        client = (
            self.headers.get("X-Forwarded-For")
            or self.headers.get("User-Agent")
            or (self.client_address[0] if self.client_address else "")
        )
        # Audit PTZ / firmware mutators (the User: something overrides settings)
        _mut = (
            "/sweep",
            "/v1/sweep",
            "/smooth_scan",
            "/v1/smooth_scan",
            "/error_check_sweep",
            "/v1/error_check_sweep",
            "/aim",
            "/v1/aim",
            "/facelock",
            "/v1/facelock",
            "/facefinder",
            "/v1/facefinder",
            "/return_door",
            "/v1/return_door",
            "/door_home",
            "/v1/door_home",
            "/look_around",
            "/v1/look_around",
            "/trigger",
            "/v1/trigger",
            "/lease",
            "/v1/lease",
            "/lease/release",
            "/v1/lease/release",
            "/pause",
            "/v1/pause",
            "/resume",
            "/v1/resume",
        )
        if path in _mut:
            keys: list = []
            try:
                _b = json.loads(raw or b"{}")
                if isinstance(_b, dict):
                    keys = list(_b.keys())[:12]
            except Exception:
                pass
            _http_audit_log(path, client=str(client)[:80], body_keys=keys)
        if path in ("/pause", "/v1/pause"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            out = pause_watchdogs(
                owner=str(body.get("owner") or "tool"),
                ttl_sec=body.get("ttl_sec") or body.get("ttl") or 15,
                reason=str(body.get("reason") or "http"),
                steal=body.get("steal", True) is not False,
            )
            return self._json(200, out)
        if path in ("/resume", "/v1/resume"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            return self._json(200, resume_watchdogs(force=bool(body.get("force"))))
        if path in ("/lease", "/v1/lease"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "bad json"})
            out = acquire_lease(
                body.get("owner"),
                body.get("ttl_sec") or body.get("lease_sec"),
                lease_id=body.get("lease_id"),
            )
            return self._json(200 if out.get("ok") else 409, out)
        if path in ("/lease/release", "/v1/lease/release"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "bad json"})
            out = release_lease(body.get("lease_id"))
            return self._json(200 if out.get("ok") else 409, out)
        if path in ("/sweep", "/v1/sweep"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            stops = body.get("stops")
            result = run_sweep(stops)
            return self._json(200 if result.get("ok") else 503, result)
        # Continuous pan; 30fps ring is the product (video/ghosts). Not facefind.
        if path in ("/smooth_scan", "/v1/smooth_scan"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            tw = body.get("two_way")
            if tw is None:
                two_way = None
            else:
                two_way = bool(tw)
            video = bool(body.get("video")) or str(body.get("mode") or "") == "video"
            if video:
                result = run_video_sweep(
                    start_pan=body.get("start_pan"),
                    end_pan=body.get("end_pan"),
                    tilt=body.get("tilt"),
                    zoom=body.get("zoom"),
                    budget_sec=float(body.get("budget_sec") or 20.0),
                    fps=float(body.get("fps") or 30.0),
                    archive_max=int(body.get("archive_max") or 300),
                    out_dir=body.get("out_dir"),
                )
            else:
                result = run_smooth_scan(
                    stops=body.get("stops"),
                    step_pan=int(body.get("step_pan") or 36000),
                    step_sleep=float(body.get("step_sleep") or 0.04),
                    zoom=body.get("zoom", 100),
                    budget_sec=float(body.get("budget_sec") or 10.0),
                    archive_every=int(body.get("archive_every") or 1),
                    archive_max=int(body.get("archive_max") or 90),
                    two_way=two_way,
                    tilt=body.get("tilt"),
                    end_pan=body.get("end_pan"),
                    out_dir=body.get("out_dir"),
                )
            return self._json(200 if result.get("ok") else 503, result)
        if path in ("/error_check_sweep", "/v1/error_check_sweep"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            result = run_error_check_sweep(
                fps=float(body.get("fps") or 8.0),
                n=int(body.get("n") or 96),
                budget_sec=float(body.get("budget_sec") or 12.0),
                out_dir=body.get("out_dir"),
            )
            return self._json(200 if result.get("ok") else 503, result)
        if path in ("/aim", "/v1/aim"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "bad json"})
            pan = int(body["pan"])
            tilt = int(body.get("tilt") or 0)
            # Zoom was accepted and silently dropped. ptz.aim() has taken a
            # zoom argument all along; this handler simply never passed it, so
            # every caller that asked for a zoom got whatever the camera was
            # already at — which is the door's 2x, because that is where it
            # rests. Every station reference captured today came out zoomed
            # into a corner of the room (the User 2026-08-16: "you stayed zoomed
            # unbelievably"), and a reference at the wrong field of view makes
            # its diff meaningless.
            _zoom = body.get("zoom")
            zoom = int(_zoom) if _zoom is not None else None
            # An active lease owns FOV outright: other callers are refused
            # instead of silently stealing mid-arc and re-arming the hold.
            lease = lease_state()
            body_lid = str(body.get("lease_id") or "")
            if lease and body_lid != lease["lease_id"]:
                return self._json(
                    409,
                    {
                        "ok": False,
                        "error": "camera_leased",
                        "owner": lease["owner"],
                        "remaining_sec": round(
                            lease["until"] - time.time(), 2
                        ),
                    },
                )
            # hold_sec / lease_sec: tool owns FOV — factory guard + presence door pause
            hold_sec = float(body.get("hold_sec") or body.get("lease_sec") or 0)
            door_tol = int(os.environ.get("RAIL_CAMERA_DOOR_PAN_TOL", "24000"))
            near_door = abs(pan - int(DOOR_HOME_PAN)) <= door_tol
            # Default hold: desk/sit (factory center) OR any non-door FOV tool tour.
            # Measured thrash: multi-stop /aim with hold_sec=0 → presence ate mid-pan
            # frames as door + return_door fought the tool (the User 2026-08-03).
            # Under a matched lease the lease itself is the hold — no per-stop re-arm.
            if lease:
                hold_sec = 0.0
            elif hold_sec <= 0 and not near_door:
                # Short default holds only. Long 12s holds + at_door=false
                # freeze left the cam on chair overnight (door-hold law).
                if abs(pan) <= FACTORY_HOME_TOL * 2:
                    hold_sec = float(os.environ.get("RAIL_CAMERA_DESK_HOLD_SEC", "6"))
                else:
                    hold_sec = float(os.environ.get("RAIL_CAMERA_TOOL_HOLD_SEC", "4"))
            note_intentional_aim(pan, hold_sec=hold_sec)
            _sweeping.set()
            try:
                # Default 1.2 s / 2 retries. Visit-lamp click passes 0.5 s / 0
                # retries so the pad is the experiment, not a hidden retry loop.
                settle = float(body["settle_sec"]) if "settle_sec" in body else 1.2
                retries = int(body["retries"]) if "retries" in body else 2
                ar = ptz_aim(
                    V4L_DEV, pan, tilt, zoom=zoom,
                    settle_sec=settle, retries=retries,
                )
                if ar.ok:
                    _capture_once()
                return self._json(
                    200 if ar.ok else 503,
                    {
                        "ok": ar.ok,
                        "pan_cmd": ar.pan_cmd,
                        "pan_actual": ar.pan_actual,
                        # tilt read back by the same ptz.aim (Init v2, the User 2026-10-04): additive
                        "tilt_actual": ar.tilt_actual,
                        "error": ar.error,
                        "hold_sec": hold_sec,
                        "cmd_pan": _cmd_pan,
                        "tool_hold": hold_sec > 0,
                        "at_door": near_door,
                    },
                )
            finally:
                _sweeping.clear()
        # Stamp a timeline pointer (face/firmware/operator) into the 30fps ring
        if path in ("/trigger", "/v1/trigger"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            kind = str(body.get("kind") or body.get("source") or "mark")
            seq = body.get("seq")
            # Optional bounded firmware facefinder pulse → capture → release → stamp
            if body.get("firmware_face") or body.get("facefinder_pulse"):
                try:
                    from .firmware_facelock import firmware_track, release_and_hold

                    engage = float(body.get("engage_sec") or 0.45)
                    firmware_track(True, V4L_DEV)
                    time.sleep(max(0.15, engage))
                    _capture_once()
                    release_and_hold(V4L_DEV)
                    with _lock:
                        seq = _seq
                        ts = _last_ts
                    return self._json(
                        200,
                        record_trigger(
                            kind="firmware_face",
                            seq=seq,
                            capture_ts=ts,
                            note=str(body.get("note") or "facefinder pulse ref"),
                            meta={"engage_sec": engage},
                        ),
                    )
                except Exception as e:
                    try:
                        from .firmware_facelock import release_and_hold

                        release_and_hold(V4L_DEV)
                    except Exception:
                        pass
                    return self._json(500, {"ok": False, "error": str(e)[:200]})
            return self._json(
                200,
                record_trigger(
                    kind=kind,
                    seq=int(seq) if seq is not None else None,
                    capture_ts=body.get("capture_ts"),
                    note=body.get("note"),
                    meta=body.get("meta") if isinstance(body.get("meta"), dict) else {},
                ),
            )
        # Firmware facefinder (UVC XU9) — release before manual PTZ / multi-stop
        # docs: camera-control-reference.md + FIRMWARE-INSTA360-LINK2.md
        if path in ("/facelock", "/v1/facelock", "/facefinder", "/v1/facefinder"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            enable = body.get("enable")
            if enable is None:
                enable = body.get("on")
            if enable is None and str(body.get("action") or "").lower() in (
                "release",
                "off",
                "disable",
            ):
                enable = False
            if enable is None and str(body.get("action") or "").lower() in (
                "engage",
                "on",
                "enable",
                "track",
            ):
                enable = True
            if enable is None:
                return self._json(
                    400,
                    {
                        "ok": False,
                        "error": "need enable true|false or action release|engage",
                    },
                )
            try:
                from .firmware_facelock import (
                    facelock_live,
                    firmware_status,
                    firmware_track,
                    release_and_hold,
                )

                # FaceFinder's correction, measured BEFORE the release and any re-aim (ff_track_log, OFF by default).
                from . import ff_track_log

                def _frame():
                    return get_jpeg()[0]

                if not enable:
                    ff_track_log.releasing(lambda c: get_ctrl(V4L_DEV, c), _frame, client=str(client)[:80])
                    res = release_and_hold(V4L_DEV)
                    note_facefinder(False)
                    live = facelock_live(firmware_status(V4L_DEV))
                    return self._json(
                        200 if res.get("ok") else 503,
                        {
                            "ok": bool(res.get("ok")),
                            "facelock": bool(live),
                            "device": V4L_DEV,
                            "skipped": res.get("skipped"),
                            "note": (
                                "already off (selector 19 GET may stay 1)"
                                if res.get("skipped")
                                else "released — manual pan free (track OFF)"
                            ),
                        },
                    )
                ok = firmware_track(True, V4L_DEV)
                note_facefinder(bool(ok))
                if ok:
                    ff_track_log.engaged(lambda c: get_ctrl(V4L_DEV, c), _frame, client=str(client)[:80])
                return self._json(
                    200 if ok else 503,
                    {
                        "ok": ok,
                        "facelock": True,
                        "device": V4L_DEV,
                        "note": "engaged (bounded use only — must release after)",
                    },
                )
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:200]})
        # After auth/facefind: hard release facefinder + door home (NOT firmware pan 0)
        if path in ("/return_door", "/v1/return_door", "/door_home", "/v1/door_home"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            settle = float(body.get("settle_sec") or 1.6)
            force = bool(body.get("force") or body.get("force_door"))
            # Enroll / multi-take desk hold: presence door-loop must NOT clear the
            # lease or slam door (measured 2026-08-02: return_door every ~5s
            # undid pan=0 → no_face on another person enroll).
            now = time.time()
            # A NAMED LEASE BEATS FORCE. The User's joystick (owner
            # user_joystick, 2026-09-06) held a live lease and the door
            # loop's return_door {force:true} every 2–5 s still yanked the
            # gimbal home ("camera still fighting me"). force may override a
            # bare hold (enroll aim_hold), never another owner's lease.
            lease_now = lease_state()
            # The lease's OWN holder may home it (the User 2026-10-09). bffc609's rule was "never ANOTHER owner's
            # lease" but refused every lease, so a look's own go_home return_door (sent before its lease release)
            # was skipped on every look since 09-06, and with it 840f496's picture check + head-framing OFF.
            # Kill RAIL_DOOR_OWN_LEASE=0.
            own_lease = bool(
                lease_now
                and body.get("lease_id")
                and body.get("lease_id") == lease_now.get("lease_id")
                and os.environ.get("RAIL_DOOR_OWN_LEASE", "1") != "0"
            )
            if now < _hold_until and (not force or (lease_now and not own_lease)):
                pa = get_ctrl(V4L_DEV, "pan_absolute")
                return self._json(
                    200,
                    {
                        "ok": True,
                        "skipped": True,
                        "reason": "lease_active" if lease_now else "aim_hold_active",
                        "owner": (lease_now or {}).get("owner"),
                        "hold_remaining_sec": round(_hold_until - now, 1),
                        "cmd_pan": _cmd_pan,
                        "pan_actual": pa,
                        "pan_cmd": _cmd_pan,
                        "note": "hold lease active — not yanking door (enroll/desk)",
                    },
                )
            # Mark door as intentional so factory recovery is armed again after desk lease
            note_intentional_aim(DOOR_HOME_PAN, hold_sec=0)
            clear_aim_hold()
            _sweeping.set()
            try:
                ret = _finish_at_door_home(settle_sec=settle)
                # Home is the release — door watch runs again. Named lease
                # still held → resume_watchdogs keeps pause unless force.
                ret["resume"] = resume_watchdogs(force=True)
                return self._json(200 if ret.get("ok") else 503, ret)
            finally:
                _sweeping.clear()
        # Look-around TOOL (on-demand only — not a timer/routine).
        # Per stop: aim → firmware facefinder ON → capture → release.
        # ALWAYS ends: facefinder OFF + door home (450000). Never leave locked on a face.
        if path in ("/look_around", "/v1/look_around"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            result = run_look_around(
                stops=body.get("stops"),
                stop_ids=body.get("stop_ids"),
                engage_sec=float(body.get("engage_sec") or 1.8),
                settle_sec=body.get("settle_sec"),
                use_facefinder=body.get("facefinder", body.get("facelock", True))
                is not False,
            )
            # Default: paths only (Cosmos operator context). Opt-in base64 with include_jpeg.
            include_jpeg = body.get("include_jpeg", body.get("jpeg_b64", False))
            if include_jpeg is False or (
                isinstance(include_jpeg, str)
                and include_jpeg.lower() in ("0", "false", "no", "off")
            ):
                for fr in result.get("frames") or []:
                    if isinstance(fr, dict) and "jpeg_b64" in fr:
                        fr.pop("jpeg_b64", None)
            code = 200 if result.get("ok") or result.get("door_ok") else 503
            if result.get("error") and not result.get("frames"):
                code = 500
            return self._json(code, result)
        # Bounded facefind: use firmware facefinder at desk/chair, then door home
        if path in ("/facefind", "/v1/facefind"):
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            cfg = _load_stops()
            # desk + chair (face-visible); door is HOLD home after, not a facefind stop
            default_stops = [
                s
                for s in (cfg.get("stops") or [])
                if s.get("id") in ("desk", "chair")
            ] or [
                {"id": "desk", "pan": 150000, "tilt": 0},
                {"id": "chair", "pan": 300000, "tilt": 0},
            ]
            stops = body.get("stops") or default_stops
            engage_sec = float(body.get("engage_sec") or 1.8)
            settle = float(body.get("settle_sec") or cfg.get("settle_sec") or 1.2)
            out: list[dict] = []
            try:
                from .firmware_facelock import firmware_track, release_and_hold

                release_and_hold(V4L_DEV)  # free gimbal before absolute aims
                _sweeping.set()
                for s in stops:
                    sid = str(s.get("id") or s.get("pan"))
                    pan = int(s["pan"])
                    tilt = int(s.get("tilt") or 0)
                    ar = ptz_aim(V4L_DEV, pan, tilt, settle_sec=settle)
                    if not ar.ok:
                        out.append(
                            {
                                "id": sid,
                                "ok": False,
                                "error": ar.error,
                                "pan_actual": ar.pan_actual,
                            }
                        )
                        continue
                    try:
                        # Facefinder ON for this stop only
                        firmware_track(True, V4L_DEV)
                        time.sleep(engage_sec)
                        _capture_once()
                    finally:
                        release_and_hold(V4L_DEV)
                    with _lock:
                        jpg = _last_jpeg
                        ts = _last_ts
                        seq = _seq
                        sha = _last_sha
                    out.append(
                        {
                            "id": sid,
                            "ok": True,
                            "pan_cmd": pan,
                            "pan_actual": ar.pan_actual,
                            "capture_ts": ts,
                            "seq": seq,
                            "sha": sha,
                            "jpeg_b64": base64.b64encode(jpg).decode() if jpg else None,
                        }
                    )
                # Critical: door home is 450000, NOT firmware forward 0
                door_ret = _finish_at_door_home(settle_sec=max(settle, 1.6))
                return self._json(
                    200 if door_ret.get("ok") or any(f.get("ok") for f in out) else 503,
                    {
                        "ok": any(f.get("ok") for f in out),
                        "n": len(out),
                        "frames": out,
                        "facelock": False,
                        "door_return": door_ret,
                        "door_pan": door_ret.get("pan_actual"),
                        "door_ok": bool(door_ret.get("ok")),
                        "note": "facefind done → door home (not pan 0)",
                    },
                )
            except Exception as e:
                try:
                    _finish_at_door_home()
                except Exception:
                    pass
                return self._json(500, {"ok": False, "error": str(e)[:300]})
            finally:
                _sweeping.clear()
        if path in ("/buffer", "/v1/buffer"):
            # POST body: {since_seq, max, stride, include_jpeg}
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            result = get_buffer(
                since_seq=body.get("since_seq"),
                max_frames=body.get("max") or body.get("max_frames"),
                stride=int(body.get("stride") or 1),
                include_jpeg=body.get("include_jpeg", True),
            )
            return self._json(200, result)
        # Freeze full retain window for stranger/inject security review
        if path in (
            "/records/freeze",
            "/v1/records/freeze",
            "/records/incident",
            "/v1/records/incident",
        ):
            # do_POST already drained the body into `raw` at the top of this
            # method. This branch used to try to read Content-Length bytes AGAIN
            # from the same stream, which blocks forever on an empty socket — so
            # POST /v1/records/freeze hung every time it was called and no
            # caller ever got a reply. That is why intrusion.py could record
            # that "the freeze path had never fired" in 2026-08-14: it was
            # reachable, it just never returned. Parse what we already have.
            try:
                body = json.loads((raw or b"{}").decode() or "{}")
            except Exception:
                body = {}
            try:
                from .security_raw import freeze_incident

                res = freeze_incident(
                    reason=str((body or {}).get("reason") or "security"),
                    seconds=(body or {}).get("seconds"),
                    t_start=(body or {}).get("t_start"),
                    t_end=(body or {}).get("t_end"),
                    extra={
                        k: (body or {}).get(k)
                        for k in ("person", "note", "source", "slot")
                        if (body or {}).get(k) is not None
                    },
                )
                return self._json(200, res)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:200]})
        return self._json(404, {"ok": False})


def main():
    if _ports.camera_off():                      # no camera: the grabber does not open the device (2026-10-10)
        print("[camera] camera: off (CRISSA_NO_CAMERA=1 or RAIL_PRESENCE_CAMERA=off); not opening the camera", flush=True)
        return
    if not _open():
        print(f"[camera] open FAIL: {_last_err}", flush=True)
    else:
        print(
            f"[camera] sole owner V4L {DEVICE} ({V4L_DEV}) "
            f"hz={HZ} buffer={BUFFER_LEN}f/{BUFFER_SEC}s",
            flush=True,
        )
    t = threading.Thread(target=_loop, name="rail-camera-loop", daemon=True)
    t.start()
    try:
        from .security_raw import start as _sec_start

        _sec_start()
        print(
            "[camera] security raw rolling store → <records> "
            "(15m–1h disk; pin on event; freeze on inject)",
            flush=True,
        )
    except Exception as e:
        print(f"[camera] security raw start skipped: {e}", flush=True)
    for _ in range(int(max(HZ, 5))):
        if _last_jpeg is not None:
            break
        time.sleep(0.05)
    class _QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            import sys as _sys

            exc = _sys.exception()
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            super().handle_error(request, client_address)

    httpd = _QuietServer((HOST, PORT), Handler)
    # large buffer JSON responses
    try:
        httpd.request_queue_size = 16
    except Exception:
        pass
    print(
        f"[camera] :{PORT} snapshot + /v1/mjpeg (always-on door video) "
        f"+ /v1/buffer + POST /smooth_scan + /look_around  hz={HZ} ring={BUFFER_LEN}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    finally:
        _stop.set()


if __name__ == "__main__":
    main()
