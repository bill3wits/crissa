"""One motion trigger, three witnesses, no witness gating another.

the User's architecture, stated 2026-08-14 and unbuilt until now:

    motion trigger ──┬→ Argus/Glimmer ─ video ──→ event, clothes, hair, sex
      seq = row id   ├→ FaceID ─────── face ───→ person_id
                     └→ Oracle ──────── opens the row on that seq

Identity (the User 2026-08-27): **FaceID only.** Argus is not a witness on this
path. the agent (Connect) stays for ad-hoc camera asks.

What ran before this module, measured 2026-08-16 02:13 on a real walk:

    5 camera triggers (energy up to 41.1)  ->  0 door events
                                           ->  0 packs
                                           ->  0 Argus calls

The camera writes every trigger to a ring at :8192/v1/triggers. Nothing in the
live tree reads it — grabber.py SERVES it and only oracle_bench.py (a benchmark)
consumes it. Argus learned about a door event solely by a pack file appearing on
disk, and a pack was only written if observe_door minted an event first, which
required FaceID to find a face in the door buffer:

    FaceID faces -> bodies -> door_names -> observe_door -> pack -> Argus

That is serial, not parallel. FaceID failing did not degrade to Argus; it
silenced Argus. A single rectangular pixel mask blinded FaceID for weeks and
took Argus down with it, and the Argus gateway sat idle for ten hours while the
camera was firing triggers the whole time.

This module reads the ring and fans it out. Both witnesses see the same raw
frames at the same seq, neither can veto the other, and each writes only its own
fields of one row keyed on the trigger.

## Laws this obeys

- **Never a second ledger.** Rows live in oracle_event; this only stamps them.
- **Argus stays blind to FaceID's name.** Different questions, no cross-feed.
- **FaceID owns identity, Argus owns the event.** Neither writes the other's
  fields, so there is nothing to vote on.
- **Guests are not occupancy.** Counting stays with apply_visit as before.
- **Deduped against the pack path.** claim_crossing() on the onset seq means the
  existing door_capture route and this one cannot both count one crossing.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request
from typing import Any

TRIGGERS_URL = os.environ.get(
    "RAIL_DOOR_TRIGGERS", _ports.camera("/v1/triggers")
)
ENABLED = os.environ.get("RAIL_DOOR_FANOUT", "1") not in ("0", "false", "no")
POLL_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_POLL", "1.0"))
# Triggers this close together are one crossing, not several. The camera fired
# five for a single walk-through on 2026-08-16 (02:13:01 -> 02:13:15).
# One crossing's triggers spread wider than first measured. the User's 02:13 lap
# fired five over 14 s, and on 2026-08-16 08:07 a single entry fired at :28 and
# :37 — nine seconds, just past an 8 s window — so both fired, both applied, and
# one walk minted s1 AND s2.
#
# Same-walk spray is one energy envelope (eat_burst). Do not drop a later
# crossing on a timer — wave then sneak-in must still fire (the User 2026-08-26).
# COALESCE_SEC remains only as the envelope gap used to COUNT waiting
# crossings (queue depth), never to discard one.
COALESCE_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_COALESCE", "4.0"))
DOOR_QUEUE_MAX = int(os.environ.get("RAIL_DOOR_QUEUE_MAX", "3"))
# Waiting-item spray merge only. A 4s in-then-out must stay TWO items
# (the User's verification lap). eat_burst's MAX_WAIT window used to swallow it.
SPRAY_SEC = float(os.environ.get("RAIL_DOOR_SPRAY_SEC", "1.25"))
DOOR_MIN_FRAMES = int(os.environ.get("RAIL_DOOR_MIN_FRAMES", "8"))
DOOR_MILL_TTL = float(os.environ.get("RAIL_DOOR_MILL_LEASE_TTL", "30"))
PENDING_PATH = pathlib.Path(
    os.environ.get(
        "RAIL_DOOR_PENDING_PATH",
        str(_paths.DATA / "door_pending.json"),
    )
)
# Deleted as a floor (the User 2026-08-16: "what is this? Delete."). A blind
# stopwatch was the ORIGINAL reason clips ended mid-crossing; _room_restored now
# gates when we ask, so waiting a fixed 3 s on top only delayed every verdict by
# 3 s and answered nothing. Kept at 0 so the knob still exists.
SETTLE_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_SETTLE", "0"))
MIN_ENERGY = float(os.environ.get("RAIL_DOOR_FANOUT_MIN_ENERGY", "8.0"))
# Share of the frame that must change between consecutive frames to read as a
# camera slew rather than a person. A pan moves everything; a body moves a region.
# Peak adjacent-frame change above which the CAMERA moved, not the room.
# Measured: 0.823 / 0.632 slews vs 0.305 clean exit, 0.016 empty.
SLEW_PEAK = float(os.environ.get("RAIL_DOOR_FANOUT_SLEW_PEAK", "0.50"))
CREW_METRICS_URL = os.environ.get(
    "RAIL_CREW_METRICS", ""   # the house's crew engine counters; a pack names its engine's /metrics here or has none
)


def _crew_metrics() -> dict[str, Any]:
    """Best-effort scrape. Door must not stall if crew /metrics is slow."""
    out: dict[str, Any] = {
        "waiting_capacity": None,
        "waiting": None,
        "preemptions": None,
    }
    try:
        with urllib.request.urlopen(CREW_METRICS_URL, timeout=0.4) as r:
            body = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return out
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            name, val_s = line.rsplit(" ", 1)
            val = float(val_s)
        except ValueError:
            continue
        if name.startswith("vllm:num_requests_waiting_by_reason{") and 'reason="capacity"' in name:
            out["waiting_capacity"] = val
        elif name.startswith("vllm:num_requests_waiting{"):
            out["waiting"] = val
        elif name.startswith("vllm:num_preemptions_total{"):
            out["preemptions"] = val
    return out


def _argus_timing(before: dict[str, Any], after: dict[str, Any], wall_ms: float) -> dict[str, Any]:
    """Argus HTTP wall + crew wait *at dispatch*. Not a per-request queue split.

    Crew request_queue_time_seconds is a mean over every completion during the
    POST — under a Bash fan-out that is Bash's average, not Argus. If waiting
    is 0 at dispatch, she did not queue. infer_ms = wall_ms only then.
    """
    waiting = before.get("waiting")
    wait_cap = before.get("waiting_capacity")
    queued: bool | None = None
    if waiting is not None or wait_cap is not None:
        queued = bool((waiting or 0) > 0 or (wait_cap or 0) > 0)
    pre0, pre1 = before.get("preemptions"), after.get("preemptions")
    pre_delta = None
    if pre0 is not None and pre1 is not None:
        pre_delta = pre1 - pre0
    infer = round(wall_ms, 1) if queued is False else None
    return {
        "wall_ms": round(wall_ms, 1),
        "waiting": waiting,
        "waiting_capacity": wait_cap,
        "queued": queued,
        "infer_ms": infer,
        "preemptions_delta": pre_delta,
    }


# Even frames handed to Argus (patch_temporal=2). Default 20 slides.
# Encoder kwargs (fps=N/duration) tell Qwen to see them; HF default 2 fps
# would thin a 20-frame clip again. 0 = encode the even full window.
ARGUS_MAX_FRAMES = int(os.environ.get("RAIL_DOOR_FANOUT_ARGUS_MAX_FRAMES", "20"))
# Two adjacent frames is a quarter-second of empty doorway (ev_1787688740112).
# If the cap/rest cut leaves fewer than this, even-sample the whole window.
ARGUS_MIN_FRAMES = int(os.environ.get("RAIL_DOOR_FANOUT_ARGUS_MIN_FRAMES", "4"))
# Occupied FOV never matches the empty-door ref, so 25s was every entry's
# backstop (the User 2026-08-16). Crossing is in the 20s ring within ~2s of trigger.
MAX_WAIT_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_MAX_WAIT", "6.0"))
# Occupied/"quiet" waits for True empty-ref, then asks. 45s (2026-08-25 lap)
# was ~30s of dead air: a dog in the jamb blocked the match, fire at 51s,
# and the return trigger sat in the ring. A walk is 2–4s. 8s is the cap.
LINGER_WAIT_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_LINGER_WAIT", "8.0"))
# Occupied FOV used to Busy the wall. Linger/Busy are not a product
# (the User 2026-08-26). Knob kept so old env files don't crash.
BUSY_ON_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_BUSY_ON", "3.0"))
BUSY_URL = os.environ.get(
    "RAIL_ROOM_BUSY", _ports.url("presence", "/v1/room_busy")
)
_BUSY_SENT = [False]
_BUSY_TRIG_TS = [0.0]
# One-still FaceID glance on a new door trigger. Not occupancy.
_GLANCE = [None]


def set_glance(fn) -> None:
    _GLANCE[0] = fn


_GLANCE_TS = [0.0]


def _fire_glance() -> None:
    """AUTH still on the door FOV. One JPEG. Missing an entry log is fine."""
    fn = _GLANCE[0]
    if fn is None:
        return
    now = time.time()
    if now - float(_GLANCE_TS[0] or 0.0) < 2.0:
        return
    _GLANCE_TS[0] = now
    try:
        fn()
    except Exception as e:
        print(f"[door-fanout] glance failed: {type(e).__name__}: {e}"[:160], flush=True)


# Argus clip: 4s around the ROI peak, plus the trigger frame.
CLIP_CAP_SEC = float(os.environ.get("RAIL_DOOR_FANOUT_CLIP_CAP_SEC", "20.0"))
# FaceID scans every Nth frame, then rescans the winner's neighbours at full
# rate. the User 2026-08-16: "You could halve the frame rate here (and turn it back
# on for scans) and leave all else the same."
#
# Measured over six events: every-2nd-frame runs ~2.5x faster (4.51s -> 1.52s)
# and the best sim moves by at most 0.012. The refine pass is what makes that
# safe — a stride can still step over the one good frame, and on the User's 07:11
# entry a stride of 3 hid the best face in the window entirely. Two extra
# frames buy that back.
#
# Argus is untouched: it gets the whole window, because frames are nearly free
# to it once they are an mp4.
FACEID_STRIDE = int(os.environ.get("RAIL_FANOUT_FACEID_STRIDE", "2"))
FACEID_REFINE = int(os.environ.get("RAIL_FANOUT_FACEID_REFINE", "2"))
# AUTHORITATIVE (the User 2026-08-16, after the first live lap read both legs).
# When on, this writes the visit and the pack path stands down — see the gate in
# argus_notify. Two writers answering one question is the vote_split the
# one-object law exists to prevent, so exactly one of them counts.
APPLY = os.environ.get("RAIL_DOOR_FANOUT_APPLY", "1") not in ("0", "false", "no")
# Linger-as-enter deleted 2026-08-25. A 30s FOV clock
# (RAIL_DOOR_FANOUT_LINGER_SEC) rewrote mill `sighting` to `enter` — the User's
# 11:48 leave printed LINGERER and never −1. Mill event type stands.

_LOCK = threading.Lock()
_SEEN: set[int] = set()
_PENDING: dict[int, dict[str, Any]] = {}
_DONE: dict[int, str] = {}
_DOOR_MILL_LEASE: dict[str, Any] = {}
_LAST_FIRE = [0.0]
# Triggers whose action has actually started (see _room_restored).
_ACTION_SEEN: dict[int, bool] = {}
# unresolved once: put the seq back so the next poll can add ~2s of buffer.
_UNRESOLVED_TRIED: set[int] = set()
# (event, ts) of the last crossing actually written — the time half of dedupe.
_LAST_APPLIED: tuple[str, float] = ("", 0.0)
# Last Argus sentence + clock — fed into the next prompt so he knows where
# he sits in the event, not only this clip. Survives a presence restart.
_LOOK_PATH = pathlib.Path(
    os.environ.get(
        "RAIL_ARGUS_LAST_LOOK",
        str(_paths.DATA / "argus_last_look.json"),
    )
)
_LAST_LOOK: dict[str, Any] = {}
_THREAD: threading.Thread | None = None
_STATS: dict[str, Any] = {
    "triggers_seen": 0,
    "events_fired": 0,
    "argus_ok": 0,
    "faceid_named": 0,
    "last_error": None,
    "last_fire_ts": None,
}


def stats() -> dict[str, Any]:
    with _LOCK:
        out = dict(_STATS)
        out["queue_depth"] = sum(
            1
            for it in _PENDING.values()
            if it.get("state") in ("waiting", "ready", "firing")
        )
        out["pending_n"] = len(_PENDING)
        out["lease_owner"] = (_DOOR_MILL_LEASE or {}).get("owner")
        out["lease_id"] = (_DOOR_MILL_LEASE or {}).get("lease_id")
        return out


def pending_work() -> bool:
    """Queue items the mill still has to judge. Busy is a wall state, not work.

    Including _BUSY_SENT here held door_mill after the only item was done
    (11:48 4s lap): lease beat 1 Hz, energy stayed -1, the return never
    triggered, linger never saw empty-ref.
    """
    with _LOCK:
        return any(
            it.get("state") in ("waiting", "ready", "firing")
            for it in _PENDING.values()
        )


def _pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _persist_pending() -> None:
    if _pytest():
        return
    try:
        now = time.time()
        with _LOCK:
            items = []
            for it in _PENDING.values():
                row = {k: v for k, v in it.items() if k != "spray_seqs"}
                items.append(row)
            done = dict(list(_DONE.items())[-200:])
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PENDING_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"ts": now, "items": items, "done": done}, default=str)
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(PENDING_PATH)
    except OSError:
        pass


def _load_pending() -> None:
    if _pytest() or not PENDING_PATH.is_file():
        return
    try:
        body = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    now = time.time()
    with _LOCK:
        _PENDING.clear()
        for it in body.get("items") or []:
            if not isinstance(it, dict):
                continue
            try:
                seq = int(it.get("seq") or 0)
                ts = float(it.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if not seq:
                continue
            if now - ts > 120:
                it["state"] = "done"
                it["last_reason"] = "restart_stale"
                _DONE[seq] = "restart_stale"
                _SEEN.add(seq)
                continue
            if it.get("state") == "firing":
                it["state"] = "waiting"
            _PENDING[seq] = it
            if it.get("state") == "done":
                _SEEN.add(seq)
        for k, v in (body.get("done") or {}).items():
            try:
                _DONE[int(k)] = str(v)
                _SEEN.add(int(k))
            except (TypeError, ValueError):
                pass


def _item_as_trigger(it: dict[str, Any]) -> dict[str, Any]:
    meta = dict(it.get("meta") or {})
    if it.get("onset_seq") is not None:
        meta.setdefault("onset_seq", it.get("onset_seq"))
        meta.setdefault("onset_ts", it.get("onset_ts"))
    return {
        "seq": it["seq"],
        "ts": it["ts"],
        "kind": "motion",
        "meta": meta,
        "_pending": True,
    }


def _ingest(triggers: list[dict[str, Any]], now: float) -> int:
    changed = False
    n_new = 0
    with _LOCK:
        for t in triggers:
            try:
                seq = int(t.get("seq") or 0)
                ts = float(t.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if not seq or ts <= 0:
                continue
            if seq in _PENDING or seq in _DONE or seq in _SEEN:
                continue
            if now - ts > 120:
                _DONE[seq] = "too_old"
                _SEEN.add(seq)
                continue
            en = _energy(t)
            onset_seq, onset_ts = trigger_onset(t)
            joined = None
            for it in _PENDING.values():
                if it.get("state") != "waiting":
                    continue
                if abs(ts - float(it.get("ts") or 0)) <= SPRAY_SEC:
                    joined = it
                    break
            if joined is not None:
                joined["spray"] = int(joined.get("spray") or 1) + 1
                joined.setdefault("spray_seqs", []).append(seq)
                if en > float(joined.get("energy") or 0):
                    joined["energy"] = en
                _SEEN.add(seq)
                _DONE[seq] = "spray"
                changed = True
                continue
            if en < MIN_ENERGY:
                _DONE[seq] = "low_energy"
                _SEEN.add(seq)
                changed = True
                continue
            _PENDING[seq] = {
                "seq": seq,
                "ts": ts,
                "energy": en,
                "onset_seq": onset_seq,
                "onset_ts": onset_ts,
                "blind_gap_s": (t.get("meta") or {}).get("blind_gap_s")
                if isinstance(t.get("meta"), dict)
                else None,
                "state": "waiting",
                "first_seen_ts": now,
                "spray": 1,
                "spray_seqs": [],
                "attempts": 0,
                "last_reason": None,
                "meta": t.get("meta") if isinstance(t.get("meta"), dict) else {},
            }
            n_new += 1
            changed = True
    if changed:
        _persist_pending()
    return n_new


def _promote(now: float) -> None:
    door = _door_in_view()
    changed = False
    with _LOCK:
        items = sorted(_PENDING.values(), key=lambda x: float(x.get("ts") or 0))
    for it in items:
        st = it.get("state")
        if st not in ("waiting", "ready"):
            continue
        seq = int(it["seq"])
        t_ts = float(it["ts"])
        age = now - t_ts
        if not door:
            it["door_away"] = True
            it.setdefault("blind_from_ts", now)
            it["blind_to_ts"] = now
            it["last_reason"] = "door_away"
            changed = True
            continue
        if it.get("door_away") and door:
            # Returned. If the window while away is uncoverable, mark blind.
            bf = float(it.get("blind_from_ts") or 0)
            if bf and (now - bf) >= 1.0:
                it["state"] = "blind"
                it["last_reason"] = "blind_gap"
                it["blind_gap"] = {
                    "from_ts": bf,
                    "to_ts": now,
                    "holder": (_DOOR_MILL_LEASE.get("owner") if _DOOR_MILL_LEASE else None)
                    or "door_away",
                    "why": "door_not_in_view",
                }
                _SEEN.add(seq)
                changed = True
                continue
            it.pop("door_away", None)
        if age < SETTLE_SEC:
            continue
        try:
            restored = _room_restored(t_ts, now)
        except Exception:
            restored = False
        empty = restored is True
        if not empty and age < LINGER_WAIT_SEC:
            it["last_reason"] = "linger"
            it["state"] = "waiting"
            changed = True
            continue
        it["state"] = "ready"
        it["restored"] = empty
        changed = True
    if changed:
        _persist_pending()


def _service(now: float) -> dict[str, Any] | None:
    with _LOCK:
        ready = sorted(
            (it for it in _PENDING.values() if it.get("state") == "ready"),
            key=lambda x: float(x.get("ts") or 0),
        )
        it = ready[0] if ready else None
        if it is None:
            return None
        it["state"] = "firing"
        it["attempts"] = int(it.get("attempts") or 0) + 1
        seq_i = int(it["seq"])
        retry_unresolved = seq_i in _UNRESOLVED_TRIED
    _SEEN.add(seq_i)
    with _LOCK:
        _STATS["events_fired"] += 1
        _STATS["last_fire_ts"] = now
        _LAST_FIRE[0] = now
        _STATS["triggers_seen"] = _STATS.get("triggers_seen", 0) + 1
    trig = _item_as_trigger(it)
    if it.get("restored"):
        trig["_rest_ts"] = now
    out = fan_out(trig)
    if it.get("restored"):
        _ensure_busy(False, "empty_ref")
    argus_ev = str(((out or {}).get("argus") or {}).get("event") or "")
    reason = str(
        ((out or {}).get("applied") or {}).get("reason")
        or (out or {}).get("reason")
        or ""
    )
    if out and (argus_ev == "unresolved" or reason == "unresolved"):
        if seq_i not in _UNRESOLVED_TRIED:
            _UNRESOLVED_TRIED.add(seq_i)
            it["state"] = "waiting"
            it["last_reason"] = "unresolved_retry"
            _SEEN.discard(seq_i)
            if len(_UNRESOLVED_TRIED) > 64:
                _UNRESOLVED_TRIED.clear()
            _persist_pending()
            return out
    it["state"] = "done"
    it["last_reason"] = reason or argus_ev or "fired"
    _DONE[seq_i] = it["last_reason"]
    _persist_pending()
    return out


def _beat_door_mill() -> None:
    if _pytest():
        return
    want = pending_work()
    lid = (_DOOR_MILL_LEASE or {}).get("lease_id")
    if not want:
        if lid:
            try:
                req = urllib.request.Request(
                    os.environ.get("RAIL_CAMERA_LEASE_RELEASE", _ports.camera("/v1/lease/release")),
                    data=json.dumps({"lease_id": lid}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=1.5).read()
            except Exception:
                pass
            _DOOR_MILL_LEASE.clear()
        return
    body = {"owner": "door_mill", "ttl_sec": DOOR_MILL_TTL}
    if lid:
        body["lease_id"] = lid
    try:
        req = urllib.request.Request(
            os.environ.get("RAIL_CAMERA_LEASE", _ports.camera("/v1/lease")),
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.5) as r:
            got = json.loads(r.read().decode())
        if got.get("ok"):
            _DOOR_MILL_LEASE.update({
                "owner": "door_mill",
                "lease_id": got.get("lease_id"),
                "until": got.get("until"),
            })
        elif got.get("error") == "camera_leased":
            print(
                f"[door-fanout] lease_contended owner={got.get('owner')}",
                flush=True,
            )
    except Exception as e:
        print(f"[door-fanout] door_mill lease: {type(e).__name__}: {e}"[:180], flush=True)


def _mark_blind_count_trust(it: dict[str, Any]) -> None:
    """Flag SoT unknown. Never +1. Recheck is door-buffer face, already home."""
    gap = it.get("blind_gap") or {}
    print(
        f"[door-fanout] blind_gap seq={it.get('seq')} "
        f"from={gap.get('from_ts')} to={gap.get('to_ts')} "
        f"holder={gap.get('holder')} — no count change",
        flush=True,
    )
    if _pytest():
        return
    try:
        req = urllib.request.Request(
            os.environ.get("RAIL_PRESENCE_URL", os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence"))).replace(
                "/v1/presence", "/v1/blind_gap"
            ),
            data=json.dumps({
                "from_ts": gap.get("from_ts"),
                "to_ts": gap.get("to_ts"),
                "holder": gap.get("holder"),
                "why": gap.get("why"),
                "seq": it.get("seq"),
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:
        pass


def _fetch_triggers() -> list[dict[str, Any]]:
    with urllib.request.urlopen(TRIGGERS_URL, timeout=4.0) as r:
        body = json.loads(r.read().decode())
    out = body.get("triggers") or []
    return [t for t in out if isinstance(t, dict)]


def _fresh(triggers: list[dict[str, Any]], now: float) -> dict[str, Any] | None:
    """Oldest live pending item as a trigger dict. Tests and ingest share this."""
    _ingest(triggers, now)
    with _LOCK:
        live = [
            it
            for it in _PENDING.values()
            if it.get("state") in ("waiting", "ready")
            and int(it["seq"]) not in _SEEN
        ]
        live.sort(key=lambda x: float(x.get("ts") or 0))
        if not live:
            return None
        it = live[0]
        _SEEN.add(int(it["seq"]))
        _STATS["triggers_seen"] = int(_STATS.get("triggers_seen") or 0) + 1
        return _item_as_trigger(it)


CAMERA_HEALTH = os.environ.get(
    "RAIL_CAMERA_HEALTH", _ports.camera("/v1/health")
)


def _door_in_view() -> bool:
    """Is the camera actually pointed at the door, and settled?

    the User 2026-08-16, on a clip that was 32 frames of the dresser sliding past:
    "argus perceived camera movement as motion. No door in fov = camera motion
    or full lense blockage. Not motion."

    A slewing camera changes every pixel, so it clears any diff-against-the-room
    test the same way a person does. That clip was the tilt being re-aimed. Argus
    called it correctly — "an empty hallway with a camera shift/pan, so no
    crossing is observed" — but the call should never have been spent.

    Fails OPEN: if the camera cannot be asked, judge the clip anyway rather than
    going blind.
    """
    try:
        with urllib.request.urlopen(CAMERA_HEALTH, timeout=3.0) as r:
            h = json.loads(r.read().decode())
    except Exception:
        return True
    if h.get("frozen"):
        return False
    if h.get("at_door") is False:
        return False
    # The door pan comes from INIT, not from the grabber's startup constant.
    #
    # The camera reports door_home_pan from whatever it loaded at boot. Move
    # the door in camera-stops.json — as we did today, the old door pan -> the re-measured door pan — and
    # that constant goes stale, so this compared the live pan against the OLD
    # door and returned False for every trigger. The fan-out then fired for
    # nothing at all and no lap was recorded, silently.
    #
    # Init is the source of truth for where the door is; the health endpoint is
    # only the source of truth for where the camera actually IS.
    home = None
    try:
        import json as _json

        from .seat_shot import door_pose

        home = int(door_pose()["pan"])
    except Exception:
        home = None
    try:
        act = int(h.get("pan_actual"))
    except (TypeError, ValueError):
        return True
    if home is None:
        try:
            home = int(h.get("door_home_pan"))
        except (TypeError, ValueError):
            return True
    # Slewing? Only compare with the command when the camera gives us one.
    try:
        cmd = int(h.get("cmd_pan"))
        if abs(act - cmd) > 3600:
            return False
    except (TypeError, ValueError):
        pass
    # Parked somewhere other than the door. One degree of slack, as before.
    return abs(act - home) <= 3600


def _room_restored(trigger_ts: float, now: float) -> bool | str:
    """Has the doorway gone back to being the room?

    The same reference the clip is bounded by, asked as a question about NOW.
    Unknown counts as restored — a missing reference must not hold an event
    open forever.
    """
    try:
        import cv2
        import numpy as np

        from .raw_clip import ACTION_FG, DIFF_ABS, _gray, raw_frames, still_ref

        ref = still_ref(trigger_ts)
        if ref is None:
            return True
        kern = np.ones((5, 5), np.uint8)

        def _active(path) -> bool:
            g = _gray(path)
            if g is None or g.shape != ref.shape:
                return False
            m = (cv2.absdiff(g, ref) > DIFF_ABS).astype(np.uint8)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)
            return float(m.mean()) >= ACTION_FG

        # Restoration may only END an event we have seen BEGIN.
        #
        # the User's rule is "end of action is when fov MATCHES REFS", and that is
        # right — but a trigger fires on the FIRST hint of motion, and at that
        # instant the room still matches the reference, because the walker has
        # not crossed yet. Asking "is it quiet now?" one poll after the trigger
        # therefore answers YES on the run-up and closes the event before it
        # starts. The window then ends AT the trigger and contains only the
        # approach.
        #
        # the User's 10:20 return, measured: 372 raw frames of the actual crossing
        # existed (through 10:20:37) and Argus was handed the 100 frames BEFORE
        # the trigger — an empty room plus the tail of the exit-confirmation
        # sweep. He answered "No person is visible... only an empty room with
        # camera movement" and returned unresolved. Correct about the footage,
        # which never contained the entry. A framing bug, not a model error.
        #
        # MAX_WAIT_SEC remains the backstop, so a trigger with no real action
        # behind it still fires and is judged rather than held open forever.
        key = int(trigger_ts * 1000)
        if not _ACTION_SEEN.get(key):
            if not any(_active(p) for _t, _s, p in raw_frames(trigger_ts, now)[::2]):
                return False
            _ACTION_SEEN[key] = True
            if len(_ACTION_SEEN) > 64:
                for k in sorted(_ACTION_SEEN)[:32]:
                    _ACTION_SEEN.pop(k, None)

        tail = raw_frames(now - 1.5, now)
        if not tail:
            return True
        empty = not any(_active(p) for _t, _s, p in tail[-4:])
        if empty:
            return True
        # (b) Motion stopped — FOV still occupied. Not linger-ended.
        # Quiet ≠ empty door. poll_once must keep waiting for True empty-ref
        # (a guest linger → later empty frame). Do not treat "quiet" as restored.
        #
        # the User 2026-08-16: "entry verdicts now land at the 25s backstop rather
        # than instantly. To what end?" To none, and he is right. An occupied
        # room never matches the empty reference again, so (a) can never fire on
        # an entry and every entry ran the full MAX_WAIT_SEC. Once the walker is
        # inside and settled the extra seconds buy no more crossing — only
        # latency, a heavier mp4 and more frames for FaceID.
        #
        # "End of action" is about the action, not about the room being empty.
        gs = [_gray(p) for _t, _s, p in tail[-5:]]
        gs = [g for g in gs if g is not None]
        if len(gs) >= 3 and all(g.shape == gs[0].shape for g in gs):
            moved = 0.0
            for a, b in zip(gs, gs[1:]):
                d = (cv2.absdiff(a, b) > DIFF_ABS).astype(np.uint8)
                moved = max(moved, float(cv2.morphologyEx(d, cv2.MORPH_OPEN, kern).mean()))
            if moved < ACTION_FG:
                return "quiet"
        return False
    except Exception:
        return True


def _door_roi() -> tuple[float, float, float, float] | None:
    """Same rect as door_capture.direction_check (`RAIL_DOOR_V2_ROI`)."""
    raw = os.environ.get("RAIL_DOOR_V2_ROI", "").strip()
    if not raw:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw.split(","))
        if 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1:
            return (x0, y0, x1, y1)
    except (TypeError, ValueError):
        return None
    return None


def _roi_mean(mask: Any, roi: tuple[float, float, float, float] | None) -> float:
    if roi is None:
        return float(mask.mean())
    h, w = mask.shape[:2]
    x0, y0, x1, y1 = roi
    xa, xb = int(x0 * w), int(x1 * w)
    ya, yb = int(y0 * h), int(y1 * h)
    if xb <= xa or yb <= ya:
        return float(mask.mean())
    patch = mask[ya:yb, xa:xb]
    if patch.size == 0:
        return float(mask.mean())
    return float(patch.mean())


def _keep_clip_indices(
    window: list,
    n: int,
    *,
    trigger_seq: int | None = None,
    trigger_ts: float | None = None,
    peak_i: int | None = None,
) -> list[int]:
    """n indices spanning trigger ∪ ROI-peak. Cap ~4s around the peak.

    Always keeps the trigger frame (if in the window) and the peak frame.
    Does not force the window's first/last — those were 30s of occupied room.
    """
    if n <= 0 or not window:
        return []
    if len(window) <= n:
        return list(range(len(window)))
    times = [float(w[0]) for w in window]
    seqs = [int(w[1]) for w in window]
    trig_i: int | None = None
    if trigger_seq is not None:
        want = int(trigger_seq)
        for i, s in enumerate(seqs):
            if s == want:
                trig_i = i
                break
    if trig_i is None and trigger_ts is not None:
        tref = float(trigger_ts)
        trig_i = min(range(len(times)), key=lambda i: abs(times[i] - tref))
    if peak_i is None:
        peak_i = trig_i if trig_i is not None else len(window) // 2
    peak_i = max(0, min(len(window) - 1, int(peak_i)))
    peak_t = times[peak_i]
    half = CLIP_CAP_SEC / 2.0
    lo_t, hi_t = peak_t - half, peak_t + half
    span = [i for i, t in enumerate(times) if lo_t <= t <= hi_t]
    if not span:
        span = list(range(len(window)))
    keep: set[int] = {peak_i}
    if trig_i is not None:
        keep.add(trig_i)
    if len(span) <= max(1, n - len(keep)):
        keep.update(span)
    else:
        step_n = max(1, n)
        for j in range(step_n):
            keep.add(
                span[min(len(span) - 1, int(j * (len(span) - 1) / max(1, step_n - 1)))]
            )
    idx = sorted(keep)
    if len(idx) > n:
        must = {i for i in (trig_i, peak_i) if i is not None}
        rest = sorted(
            (i for i in idx if i not in must),
            key=lambda i: abs(times[i] - peak_t),
        )
        idx = sorted(list(must) + rest[: max(0, n - len(must))])
    return idx[:n]


def _action_before_rest(window: list, rest_ts: float | None) -> list:
    """rest_ts is end of movement. Clip frames are strictly before it."""
    if not window or rest_ts is None:
        return window
    try:
        cut = float(rest_ts)
    except (TypeError, ValueError):
        return window
    before = [fr for fr in window if float(fr[0]) < cut]
    return before or window


def pick_frames(
    window: list,
    n: int,
    *,
    trigger_seq: int | None = None,
    trigger_ts: float | None = None,
) -> list:
    """The n frames that carry the crossing, oldest first.

    Scores foreground *inside the door ROI* (not the whole frame — in an
    occupied room the global peak is whoever has the most body-pixels).
    Six even frames around that peak, plus the trigger frame. Window ends
    are not kept: they were the 05:49:54 117-frame hole through the plane.
    """
    if n <= 0 or len(window) <= n:
        return window
    even_idx = [
        min(len(window) - 1, int(i * (len(window) - 1) / float(max(1, n - 1))))
        for i in range(n)
    ]
    even = [window[i] for i in even_idx]
    peak_i: int | None = None
    try:
        import cv2

        from .raw_clip import ACTION_FG, DIFF_ABS, _gray, still_ref
    except Exception:
        idx = _keep_clip_indices(
            window, n, trigger_seq=trigger_seq, trigger_ts=trigger_ts, peak_i=None
        )
        return [window[i] for i in idx] if idx else even
    ref = still_ref(window[0][0])
    if ref is None:
        idx = _keep_clip_indices(
            window, n, trigger_seq=trigger_seq, trigger_ts=trigger_ts, peak_i=None
        )
        return [window[i] for i in idx] if idx else even
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    roi = _door_roi()
    scores: list[float] = []
    for _t, _s, p in window:
        g = _gray(p)
        if g is None or g.shape != ref.shape:
            scores.append(0.0)
            continue
        m = (cv2.absdiff(g, ref) > DIFF_ABS).astype("uint8")
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)
        scores.append(_roi_mean(m, roi))
    if any(v >= ACTION_FG for v in scores):
        peak_i = int(max(range(len(scores)), key=lambda i: scores[i]))
    idx = _keep_clip_indices(
        window,
        n,
        trigger_seq=trigger_seq,
        trigger_ts=trigger_ts,
        peak_i=peak_i,
    )
    return [window[i] for i in idx] if idx else even


def slew_profile(window: list, sample: int = 40) -> dict[str, Any]:
    """Peak adjacent-frame change plus contiguous slew spans."""
    peak = camera_moved(window, sample=sample)
    spans: list[tuple[int, int, float]] = []
    paths = [p for _t, _s, p in (window or [])]
    n = len(paths)
    if n < 4:
        return {"peak": peak, "spans": spans, "pairs": 0}
    try:
        import cv2
        from .raw_clip import DIFF_ABS, _gray
    except Exception:
        return {"peak": peak, "spans": spans, "pairs": 0}
    step = max(1, n // max(2, sample))
    run_i0 = None
    run_pk = 0.0
    pairs = 0
    for i in range(0, n - 1, step):
        a, b = _gray(paths[i]), _gray(paths[i + 1])
        if a is None or b is None or a.shape != b.shape:
            continue
        d = float((cv2.absdiff(a, b) > DIFF_ABS).mean())
        pairs += 1
        if d > SLEW_PEAK:
            if run_i0 is None:
                run_i0 = i
                run_pk = d
            else:
                run_pk = max(run_pk, d)
        elif run_i0 is not None:
            spans.append((run_i0, i, run_pk))
            run_i0, run_pk = None, 0.0
    if run_i0 is not None:
        spans.append((run_i0, n - 1, run_pk))
    return {"peak": peak, "spans": spans, "pairs": pairs}


def _pan_off_door() -> bool:
    """Absolute pose vs door home. NOT a pan delta (FF follow is a delta)."""
    try:
        with urllib.request.urlopen(CAMERA_HEALTH, timeout=1.5) as r:
            h = json.loads(r.read().decode())
        act = int(h.get("pan_actual"))
        home = int(h.get("door_home_pan") or 0)
        try:
            from .seat_shot import door_pose
            home = int(door_pose()["pan"])
        except Exception:
            pass
        return abs(act - home) > 3600 or h.get("at_door") is False
    except Exception:
        return False


def _slew_corroborated(window: list) -> tuple[bool, list[str]]:
    """Mill slew / off-door / lease / HTTP aim — not a bare pan delta."""
    why: list[str] = []
    if not window:
        return False, why
    lo = float(window[0][0])
    hi = float(window[-1][0])
    if _pan_off_door():
        why.append("pan_off_door")
    try:
        with urllib.request.urlopen(CAMERA_HEALTH, timeout=1.0) as r:
            h = json.loads(r.read().decode())
        lease = h.get("lease") or {}
        owner = (lease or {}).get("owner") if isinstance(lease, dict) else None
        if owner and owner != "door_mill":
            why.append("lease:" + str(owner))
    except Exception:
        pass
    try:
        q = f"{os.environ.get('RAIL_CAMERA_COVERAGE', _ports.camera('/v1/coverage'))}?from={lo}&to={hi}"
        with urllib.request.urlopen(q, timeout=1.5) as r:
            cov = json.loads(r.read().decode())
        for sp in cov.get("why") or []:
            why.append(str(sp))
        if cov.get("blind_spans"):
            why.append("coverage_blind")
    except Exception:
        pass
    return bool(why), why


def camera_moved(window: list, sample: int = 40) -> float:
    """Fraction of the window during which the CAMERA was slewing, not the room.

    `_door_in_view()` asks the camera where it is pointing NOW. But the fan-out
    deliberately waits for the action to finish before it judges, so "now" is up
    to MAX_WAIT_SEC after the footage was shot — long enough for the camera to
    have swung back to the door. The check passes and the clip is judged anyway.

    the User 2026-08-16, on another person's entry: "it has beginning of another person entry
    then end but no crossing." The evidence showed exactly that — frame 0 is
    another person already seated, close up, camera pointed at a SEAT; the last frame
    is the empty doorway. The camera was aimed away while she crossed and swung
    home afterwards. Argus said "no crossing of the doorway is visible", which
    is true of that footage, and the call was spent proving the camera was
    looking the wrong way.

    A pan changes essentially every pixel between consecutive frames. A person
    walking, even close to the lens, changes a region. So: consecutive-frame
    difference, and if most of the frame changes, the camera moved.

    Returns 0.0..1.0 — the PEAK adjacent-frame change. Compare with SLEW_PEAK.
    """
    try:
        import cv2
        import numpy as np

        from .raw_clip import DIFF_ABS, _gray
    except Exception:
        return 0.0
    paths = [p for _t, _s, p in (window or [])]
    if len(paths) < 4:
        return 0.0
    # ADJACENT pairs, sampled across the window. Comparing frames far apart
    # measures the whole event and reads as movement every time — that mistake
    # made a clean exit look identical to a camera pan.
    #
    # The signal is the PEAK, not the average: a slew is a brief violent event
    # inside an otherwise ordinary clip, so a mean drowns it. Measured on four
    # real events:
    #
    #   0.823  camera on a seat then swung home (another person's entry)
    #   0.632  "unresolved, no person visible" — also a slew
    #   0.305  clean exit, camera still, resolved correctly
    #   0.016  genuinely empty doorway
    #
    # so the door between "a person moved" and "the camera moved" sits near 0.5.
    n = len(paths)
    step = max(1, n // max(2, sample))
    peak = 0.0
    for i in range(0, n - 1, step):
        a, b = _gray(paths[i]), _gray(paths[i + 1])
        if a is None or b is None or a.shape != b.shape:
            continue
        peak = max(peak, float((cv2.absdiff(a, b) > DIFF_ABS).mean()))
    return peak


def _energy(t: dict[str, Any]) -> float:
    try:
        return float((t.get("meta") or {}).get("energy") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _waiting_crossings(triggers: list[dict[str, Any]] | None = None, now: float | None = None) -> int:
    """Live pending envelopes. Used only to raise Busy. Never to drop a trigger."""
    with _LOCK:
        return sum(
            1
            for it in _PENDING.values()
            if it.get("state") in ("waiting", "ready", "firing")
        )


def _eat_burst(start_ts: float, now: float, triggers: list[dict[str, Any]] | None = None) -> int:
    """Mark every trigger in this rest-window seen. One walk, one Argus call.

    Only seqs with ts in [start_ts, now] — a later crossing after the door
    rested is not in that span.
    """
    n = 0
    try:
        ring = triggers if triggers is not None else _fetch_triggers()
    except Exception:
        return 0
    lo = float(start_ts) - 0.5
    # Do not eat a later crossing that is already sitting in the mill ring.
    # MAX_WAIT is the wait for THIS walk's rest, not a 20s swallow.
    hi = min(float(now), float(start_ts) + MAX_WAIT_SEC) + 0.05
    with _LOCK:
        for t in ring:
            try:
                seq = int(t.get("seq") or 0)
                ts = float(t.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if seq and lo <= ts <= hi:
                _SEEN.add(seq)
                n += 1
    return n


def _jpeg_at(ts: float) -> tuple[bytes | None, float | None, int | None]:
    """Nearest tape jpeg to ts — the empty-ref / event-end slide."""
    try:
        from . import tape
        from pathlib import Path as _P

        # rest_ts is "now" at fire; tape can lag ~1s. 0.25s window missed
        # ev_1787685480929 (rest_ts set, no argus_end.jpg).
        rows = tape.frames_around(float(ts), before_sec=2.0, after_sec=0.5)
    except Exception:
        return None, None, None
    if not rows:
        return None, None, None
    best = min(rows, key=lambda r: abs(float(r["ts"]) - float(ts)))
    try:
        return _P(str(best["path"])).read_bytes(), float(best["ts"]), int(best["seq"])
    except OSError:
        return None, None, None


def _post_busy(on: bool, reason: str) -> None:
    """Tell presence SoT. Failures are logged; a door event still proceeds."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    url = (BUSY_URL or "").strip()
    if not url or url.lower() in ("0", "false", "no", "off"):
        return
    body = json.dumps({"on": bool(on), "reason": str(reason or "")}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1.5) as r:
        r.read()


def _ensure_busy(
    on: bool, reason: str, *, trigger_ts: float | None = None
) -> None:
    """One POST per edge. Busy-on drops AUTH; Busy-off does not re-grant."""
    if on and trigger_ts is not None:
        try:
            _BUSY_TRIG_TS[0] = float(trigger_ts)
        except (TypeError, ValueError):
            pass
    if bool(_BUSY_SENT[0]) == bool(on):
        return
    try:
        _post_busy(on, reason)
        _BUSY_SENT[0] = bool(on)
        print(
            f"[door-fanout] room_busy={'on' if on else 'off'} reason={reason}",
            flush=True,
        )
    except Exception as e:
        print(
            f"[door-fanout] room_busy POST failed: {type(e).__name__}: {e}"[:200],
            flush=True,
        )


def trigger_onset(trigger: dict[str, Any]) -> tuple[int | None, float | None]:
    """Onset the camera energy ring already walked (gap A). Missing → (None, None)."""
    meta = trigger.get("meta") if isinstance(trigger.get("meta"), dict) else {}
    seq = meta.get("onset_seq", trigger.get("onset_seq"))
    ts = meta.get("onset_ts", trigger.get("onset_ts"))
    try:
        seq_i = int(seq) if seq is not None else None
    except (TypeError, ValueError):
        seq_i = None
    try:
        ts_f = float(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_f = None
    return seq_i, ts_f


def window_before_s(
    trigger_ts: float,
    onset_ts: float | None,
    *,
    lead: float = 0.3,
    default: float = 8.0,
) -> float:
    """Look-back from trigger. Measured onset beats a blind pad (H4)."""
    if onset_ts is None:
        return float(default)
    try:
        span = float(trigger_ts) - float(onset_ts) + float(lead)
    except (TypeError, ValueError):
        return float(default)
    return max(0.5, min(float(default), span))


def fan_out(trigger: dict[str, Any]) -> dict[str, Any]:
    """Open the row on this trigger, then ask both witnesses in parallel.

    Returns a summary. Never raises — a door event must not be able to kill the
    poll loop, and a witness that fails is recorded rather than retried.
    """
    from .oracle_event import event_id_from_ts, get_event, open_event, set_onset

    ts = float(trigger.get("ts") or time.time())
    seq = int(trigger.get("seq") or 0)
    rest_ts = trigger.get("_rest_ts")
    try:
        rest_ts = float(rest_ts) if rest_ts is not None else None
    except (TypeError, ValueError):
        rest_ts = None
    eid = event_id_from_ts(ts)
    out: dict[str, Any] = {
        "event_id": eid, "trigger_seq": seq, "trigger_ts": ts,
        "rest_ts": rest_ts,
    }

    if get_event(eid) is None:
        open_event(ts, pack_id=eid)
    onset_seq, onset_ts = trigger_onset(trigger)
    if onset_seq is not None and onset_ts is not None:
        set_onset(eid, seq=int(onset_seq), ts=float(onset_ts))
        out["onset_seq"] = int(onset_seq)
        out["onset_ts"] = float(onset_ts)
    else:
        set_onset(eid, seq=seq, ts=ts)

    argus_res: dict[str, Any] = {}
    faceid_res: dict[str, Any] = {}

    # ONE window, computed once, handed to both.
    #
    # Having each witness call action_window() separately was meant to keep them
    # ungated, and instead gave them different footage: still_ref() picks the
    # longest still run out of a 90 s look-back, and that run MOVES as new
    # frames land, so two calls seconds apart choose different references and
    # bound different actions. Measured on ev_1786880910456 — a clean entry
    # walking toward the camera — Argus got 14 frames (seq 417093-417110) and
    # FaceID got 1 (seq 417065), windows that do not even overlap. FaceID
    # reported no_face on an entry with a face in it.
    #
    # Preparing shared input is not gating. Both witnesses still run in
    # parallel, still answer different questions, and neither can veto the
    # other — they simply see the same thing. It also halves the work, since
    # still_ref scans the same 90 s of raw either way.
    from .raw_clip import action_window

    cap_ts = trigger.get("capture_ts")
    try:
        win_ts = float(cap_ts) if cap_ts is not None else ts
    except (TypeError, ValueError):
        win_ts = ts
    try:
        window = action_window(ts, window_before=window_before_s(win_ts, onset_ts))
    except Exception:
        window = []

    # Was the CAMERA looking at the door while this happened?
    #
    # _door_in_view() asks where the camera points NOW, and "now" is up to
    # MAX_WAIT_SEC after the footage — long enough for it to have swung home, so
    # the check passes on a clip shot from a seat. another person's entry
    # (ev_1786899516827): frame 0 is her already seated in close-up, the last
    # frame is the empty doorway. Argus answered "no crossing of the doorway is
    # visible", spent on proving the camera looked the wrong way, and the
    # crossing was recorded as unresolved rather than as not-filmed.
    #
    # A missed crossing is a fact about the camera, not a verdict about the
    # person. Say so, and do not spend two model calls saying it.
    # `unobserved`, not `unresolved`. Argus, asked to design this 2026-08-16:
    #
    #   "The clip you were given is not an ambiguous crossing, it is a
    #    non-observation. `unresolved` means 'I see something but I cannot
    #    decide enter/exit'. A blind interval should be logged as unobservable
    #    and the presence decision left open until the door is covered again."
    #
    # The distinction is the whole point: "I did not see it" is not "it did not
    # happen". A blind window with a person in the room afterwards and no
    # recorded exit is evidence of a MISSED entry, which is a thing to go and
    # resolve — while `unresolved` reads as a judged event and closes the book.
    prof = slew_profile(window)
    slew = float(prof.get("peak") or 0.0)
    slew_why: list[str] = []
    trimmed_spans: list = []
    if slew > SLEW_PEAK:
        corr, slew_why = _slew_corroborated(window)
        if corr:
            spans = list(prof.get("spans") or [])
            drop = set()
            for i0, i1, _pk in spans:
                for i in range(int(i0), int(i1) + 1):
                    drop.add(i)
            kept = [fr for i, fr in enumerate(window) if i not in drop]
            trimmed_spans = spans
            if len(kept) < DOOR_MIN_FRAMES:
                blind = {
                    "from_ts": window[0][0] if window else ts,
                    "to_ts": window[-1][0] if window else ts,
                    "slew_peak": round(slew, 3),
                    "n_frames": len(window),
                    "slew_corroborated": True,
                    "slew_why": slew_why,
                    "trimmed_spans": spans,
                }
                out.update(
                    ok=True, applied=False, reason="unobserved",
                    unobserved=True, blind_window=blind,
                    slew_peak=round(slew, 3), slew_corroborated=True,
                )
                print(
                    f"[door-fanout] seq={seq} UNOBSERVED slew={slew:.2f} "
                    f"n={len(window)} kept={len(kept)} why={slew_why} "
                    f"— blind window, not a verdict",
                    flush=True,
                )
                return out
            window = kept
            out["slew"] = "trimmed"
            out["clip_gap"] = [
                f"CLIP GAP {i0}-{i1} camera moved"
                for i0, i1, _pk in spans
            ]
        else:
            out["slew"] = "uncorroborated"
            print(
                f"[door-fanout] seq={seq} slew_peak={slew:.2f} "
                f"slew=uncorroborated — judging",
                flush=True,
            )
    out["slew_peak"] = round(slew, 3)
    out["slew_corroborated"] = bool(slew_why)
    out["trimmed_spans"] = trimmed_spans
    out["window"] = {
        "n": len(window),
        "seqs": [s for _t, s, _p in window][:200],
    }

    def _faceid() -> None:
        try:
            faceid_res.update(_ask_faceid(eid, ts, window) or {})
        except Exception as e:
            faceid_res["error"] = f"{type(e).__name__}: {e}"[:160]

    # Identity is FaceID only. Argus stays on the Connect address for ad-hoc camera asks.
    # Do not join a 180s VLM thread on the door path (the User 2026-08-27).
    argus_res.update({"ok": False, "skipped": "faceid_only"})
    tb = threading.Thread(target=_faceid, name=f"fanout-faceid-{seq}", daemon=True)
    tb.start()
    tb.join(timeout=180.0)

    out["argus"] = argus_res
    out["faceid"] = faceid_res
    if APPLY:
        out["applied"] = _apply(eid, seq, argus_res, faceid_res)
    out["row"] = get_event(eid)
    return out


def _leave_candidates() -> list[dict[str, Any]]:
    """Open enter cards plus still-present inventory robe/hair."""
    from .oracle_event import open_visits

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(pid: Any, clothes: Any, hair: Any, sex: Any) -> None:
        p = str(pid or "").strip().lower()
        if not p or p in seen or p == "unknown":
            return
        if not (clothes or hair or sex):
            return
        seen.add(p)
        out.append(
            {"person_id": p, "clothes": clothes, "hair": hair, "sex": sex}
        )

    present_ids: set[str] | None = None
    try:
        from . import server as _server

        # Never start PresenceService from a naming helper — that is how unit
        # tests used to boot the live rail as a side effect of _person_for.
        svc = getattr(_server, "_svc", None)
        if svc is None:
            raise RuntimeError("no live svc")
        inv = svc.inventory
        present_ids = {
            str(p).strip().lower()
            for p in (getattr(inv, "present", {}) or {})
            if p
        }
        visits = getattr(svc, "_oracle_visits", {}) or {}
        for pid in list(getattr(inv, "present", {}) or {}):
            if pid in seen:
                continue
            v = visits.get(pid) or {}
            tl = (getattr(inv, "traits_live", {}) or {}).get(pid) or {}
            clothes = (
                v.get("clothes")
                or tl.get("clothes")
                or tl.get("shirt")
                or tl.get("summary")
            )
            hair = v.get("hair") or tl.get("hair")
            sex = v.get("sex") or (getattr(inv, "gender", {}) or {}).get(pid)
            _add(pid, clothes, hair, sex)
    except Exception:
        inv = None
    for r in open_visits() or []:
        if str(r.get("event") or "").lower() == "exit":
            continue
        pid = str(r.get("person_id") or "").strip().lower()
        if present_ids is not None and pid not in present_ids:
            continue
        _add(pid, r.get("clothes"), r.get("hair"), r.get("sex"))
    return out


def _leave_clip(argus: dict[str, Any] | None) -> dict[str, Any]:
    """Leave robe/hair/sex. Harvest from Argus prose when JSON clothes is empty."""
    argus = argus or {}
    clothes = argus.get("clothes")
    if not clothes:
        from .traits_leave import clothes_from_prose

        clothes = clothes_from_prose(argus.get("comment") or argus.get("note") or "")
    return {
        "clothes": clothes,
        "hair": argus.get("hair"),
        "sex": argus.get("sex"),
    }


def _leave_contradicts(argus: dict[str, Any], pid: str) -> bool:
    """True when the leave clip cannot be this enter card (sex / robe)."""
    from .traits_leave import leave_contradicts_card

    leave = _leave_clip(argus)
    for c in _leave_candidates():
        if c["person_id"] != pid:
            continue
        if leave_contradicts_card(leave, c):
            return True
    return False


def _leave_sex_mismatch(argus: dict[str, Any], pid: str) -> bool:
    """True when leave sex and this enter card disagree (M vs F)."""
    from .traits_leave import sex_mismatch

    leave = _leave_clip(argus)
    for c in _leave_candidates():
        if c["person_id"] != pid:
            continue
        return sex_mismatch(leave, c)
    return False


def _household_id(who: str) -> bool:
    """True if who is a household person id (not a mouth, not scratch)."""
    who = (who or "").strip().lower()
    if not who or who.startswith("s") and who[1:].isdigit():
        return False
    if who in ("crissa_her_tts", "crew_tts", "announcer", "announcer", "dry_enroll"):
        return False
    try:
        from .enrollments import house_id_people

        d = house_id_people() / who
        if d.is_dir() and (d / "person.json").is_file():
            return True
        idx = house_id_people() / "_index.json"
        if idx.is_file():
            data = json.loads(idx.read_text(encoding="utf-8"))
            return any(
                str(p.get("id") or "").strip().lower() == who
                for p in (data.get("people") or [])
                if isinstance(p, dict)
            )
    except Exception:
        pass
    return False


def _who_left_by_clothes(
    argus: dict[str, Any],
) -> tuple[str | None, float, dict[str, Any]]:
    """Pick the open visit whose enter clothes/hair match this leave clip."""
    from .traits_leave import match_leave_occupant

    return match_leave_occupant(_leave_clip(argus), _leave_candidates())


def _person_for(
    eid: str, event: str, faceid: dict[str, Any],
    argus: dict[str, Any] | None = None,
) -> str:
    """Who this visit belongs to. FaceID names; exits fall back to the roster.

    An exit shows the camera a back, so a face cannot name it — that is physics,
    not a bug, and it is why the row has to carry identity forward from the
    entry. Same ladder the pack path already used, reused rather than reinvented.
    """
    from .argus_notify import _roster_user_for_exit
    from .oracle_event import get_event, mint_scratch, open_visits

    pid = ""
    if event != "exit":
        pid = str(faceid.get("name") or "").strip().lower()
        if not pid:
            row = get_event(eid) or {}
            pid = str(row.get("person_id") or "").strip().lower()
    # Exit: never FaceID. A back is not a face. Robe/hair/sex vs enter cards
    # first (the User 2026-08-19). Then sole visit / Argus who.
    if event == "exit" and argus:
        picked, _sc, _why = _who_left_by_clothes(argus)
        if picked:
            pid = picked
    # Whoever is still in the room. the User 2026-08-16: "it appears that an unnamed
    # entry does not become S1 like we planned. If it were, a sighting then
    # recorded would become a sighting of s1 not unnamed."
    #
    # He is right, and the miss was in this function: it looked only at the row
    # for THIS trigger, which is seconds old and empty, so it never found the
    # scratch id minted at the entry. On another person's 06:36 sequence s1 was minted
    # correctly and then the two sightings behind it resolved to nothing.
    #
    # This also replaces the roster fallback as the FIRST answer for an exit.
    # The old order asked "who is the sole roster user" and got `the user`, so a
    # woman in a light cardigan walking out was recorded as the User leaving.
    # The sole open visit is the person actually in the room.
    if (not pid or pid == "unknown") and event in ("sighting", "exit"):
        here = [
            str(r.get("person_id") or "").strip().lower()
            for r in open_visits()
            if r.get("person_id")
        ]
        here = sorted(set(p for p in here if p))
        # Clothes mismatch (stale robe vs today's shirt) is apply_visit's
        # problem — confirm or refuse. Skipping the sole body here made
        # 08:20 unnamed_exit: roster fallback never returns the User.
        # Sex mismatch still skips (a man leaving is not the F card).
        if len(here) == 1 and not (
            event == "exit"
            and argus
            and _leave_sex_mismatch(argus, here[0])
        ):
            pid = here[0]
    if (not pid or pid == "unknown") and event == "exit":
        cands = _leave_candidates()
        if len(cands) == 1 and not (
            argus and _leave_sex_mismatch(argus, str(cands[0]["person_id"]))
        ):
            pid = str(cands[0]["person_id"])
    # WP-4: Argus `who` is not identity. Clothes/hair/sex vs enter cards already
    # ran above. Prose may harvest a robe (clothes_from_prose); it must not
    # stamp a name.
    # Only when nobody is on an open visit does an exit fall back to the roster.
    if (not pid or pid == "unknown") and event == "exit":
        pid = _roster_user_for_exit() or pid
    # ENTRY match game: Argus names the guest from a labelled line-up.
    #
    # the User 2026-08-16: "Can Argus take FaceID task with a labeled family
    # portrait? Let FaceID be there for AUTH but let Argus try to name the
    # guests with FaceID to serve as RL corrections for training." /
    # "Match game for entry and exits".
    #
    # Runs ONLY after FaceID has declined to name the face, so it can never
    # override a real bank match — the alternative here is minting a stranger.
    # another person entered twice this morning at sim 0.0931 and 0.5283 and both
    # became a fresh `s1`; Argus named her from the line-up both times, and
    # named the User correctly on his own crops rather than answering "another person"
    # to everything.
    #
    # AUTH is untouched: that still requires FaceID. This names a visit, and
    # writes nothing to the face bank.
    # ANY unnamed face, not just an enter.
    #
    # the User 2026-08-16: "all you have to do is show argus who entered and he
    # will know."
    #
    # Gating this to `enter` meant it almost never ran. Replayed over today's
    # kept crops, Argus named the User in SIX events FaceID had scored 0.41-0.59
    # and the room had recorded nothing at all for — every one of them logged
    # as `sighting` or `unresolved`, so the line-up never fired live. The crops
    # were on disk the whole time.
    #
    # Naming is a different question from counting, and keeping them separate
    # is the point: this only decides WHO a face belongs to. Whether anyone
    # crossed is still Argus's event verdict, and only enter/exit moves the
    # count — so a named sighting stamps identity without inventing a crossing.
    # Argus clip-who and face-lineup are dead. Identity is FaceID only.
    if pid and pid != "unknown":
        return pid
    # ONLY an enter may mint a body.
    #
    # A sighting is a look at someone who is already in the room; if we cannot
    # say who, the honest answer is silence, not a new person. Minting here made
    # the fan-out a phantom factory the moment it became authoritative: at
    # 03:05:45 a faceless sighting ("teal long sleeve shirt, dark pants" — the
    # User, already present and already counted) minted s1, and the room read 3
    # for one man. An exit likewise cannot mint — you cannot leave a room you
    # were never counted in.
    if event != "enter":
        return ""
    scratch = mint_scratch(eid)
    return str(((scratch or {}).get("row") or {}).get("person_id") or "")


def _apply(
    eid: str, seq: int, argus: dict[str, Any], faceid: dict[str, Any]
) -> dict[str, Any]:
    """Write the visit. Authoritative: this is the count, not a shadow of it.

    Guarded by claim_crossing on the trigger seq, so a second trigger inside the
    same crossing cannot count twice. The pack path stands down entirely when
    this is on — two writers answering one question is the vote_split that the
    one-object law exists to prevent.
    """
    global _LAST_APPLIED

    from .argus_notify import claim_crossing, post_oracle_visit

    def _dev(code: dict[str, Any]) -> dict[str, Any]:
        # Argus Dev ledger is not identity. Do not mint a# / X# from reports.
        return {"ok": True, "skipped": "faceid_only"}

    event = str(argus.get("event") or "").strip().lower()
    if event == "unresolved":
        # A real answer, recorded and not applied. Argus saw the clip end while
        # someone was still moving, so it cannot say where they went — and a
        # visit row must not assert a presence the evidence does not carry.
        return {"ok": True, "applied": False, "reason": "unresolved"}
    if event not in ("enter", "exit", "sighting"):
        return {"ok": False, "reason": "no_event"}
    # Seq claim is the duplicate key. A time window here dropped a sneak-in
    # enter after a wave that had already counted as enter (the User 2026-08-26).
    claim = claim_crossing(seq, eid)
    if not claim.get("ok"):
        return {"ok": True, "applied": False, "reason": "duplicate_crossing",
                "owner": claim.get("owner")}
    pid = _person_for(eid, event, faceid, argus)
    if not pid:
        # Nobody nameable and nothing to invent. Recorded, not counted.
        res = {"ok": True, "applied": False, "reason": "unnamed_" + event}
        res["argus_dev"] = _dev(res)
        return res
    leave_match: dict[str, Any] | None = None
    clip = _leave_clip(argus) if event == "exit" else {}
    if event == "exit":
        picked, sc, why = (
            _who_left_by_clothes(argus) if argus else (None, 0.0, {})
        )
        # Clothes/hair win. FaceID on a back is ignored (the User 2026-08-19).
        if picked:
            pid = picked
        if picked and picked == pid:
            method = "clothes_hair"
        elif str((argus or {}).get("who") or "").strip().lower() == pid:
            method = "argus_who"
        else:
            method = "attributed"
        leave_match = {
            "person_id": pid,
            "clothes_pick": picked,
            "method": method,
            "score": round(float(sc), 3),
            **why,
        }
    try:
        body = {
            "event": event,
            "person_id": pid,
            "event_id": eid,
            "pack_id": eid,
            "clothes": clip.get("clothes") if event == "exit" else argus.get("clothes"),
            "hair": argus.get("hair"),
            "sex": argus.get("sex"),
            "faceid": {
                "name": faceid.get("name"),
                "sim": faceid.get("sim"),
                "face_px": faceid.get("face_px"),
            } if faceid.get("name") else None,
            "note": (argus.get("comment") or "door_fanout"),
            "comment": argus.get("comment"),
        }
        if leave_match:
            body["leave_match"] = leave_match
        if argus.get("user_exit_set") or "user_exit" in argus:
            body["user_exit"] = argus.get("user_exit")
            body["user_exit_set"] = True
        res = post_oracle_visit(body)
        if leave_match:
            res["leave_match"] = leave_match
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
        res["argus_dev"] = _dev(res)
        return res
    if res.get("applied") or res.get("ok"):
        with _LOCK:
            _LAST_APPLIED = (event, time.time())
    # Apply remaps ghost names (06:32:14 who=another person, clothes=User → the user).
    # Do not overwrite that pid with the pre-POST guess.
    applied_pid = str(res.get("person_id") or "").strip().lower()
    res["person_id"] = applied_pid or pid
    res["argus_dev"] = _dev(res)
    return res


def _load_last_look() -> dict[str, Any]:
    """Disk first, then Argus Dev last — RAM dies on restart."""
    try:
        if _LOOK_PATH.is_file():
            d = json.loads(_LOOK_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict) and (d.get("event") or d.get("comment")):
                return d
    except (OSError, ValueError):
        pass
    try:
        from .argus_dev import load

        last = (load() or {}).get("last") or {}
        if last.get("event"):
            return {
                "event": last.get("event"),
                "who": last.get("who"),
                "comment": str(last.get("action") or ""),
                "eid": last.get("eid"),
                "ts_local": "",
            }
    except Exception:
        pass
    return {}


def _save_last_look() -> None:
    try:
        _LOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LOOK_PATH.with_suffix(".tmp")
        with _LOCK:
            body = dict(_LAST_LOOK)
        tmp.write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(_LOOK_PATH)
    except OSError:
        pass


def _code_sot_ids() -> tuple[int, set[str]]:
    """Named certain occupants on Code SoT. Ghost U# / Dev aN do not count."""
    try:
        from . import server as _server

        svc = getattr(_server, "_svc", None)
        st = svc.get() if svc is not None else None
    except Exception:
        st = None
    if st is None:
        return 0, set()
    ids: set[str] = set()
    for p in getattr(st, "people", None) or []:
        n = str(getattr(p, "name", "") or "").strip().lower()
        if not n or n.startswith(("unknown", "crowd")):
            continue
        if len(n) >= 2 and n[0] in "ufm" and n[1:].isdigit():
            continue
        if getattr(p, "certain", True) is False:
            continue
        ids.add(n)
    try:
        hc = int(getattr(st, "headcount", 0) or 0)
    except (TypeError, ValueError):
        hc = len(ids)
    return hc, ids


def _hold_line() -> str:
    """Code SoT occupants only. Argus Dev ghosts stay off the clip (H5)."""
    hc, code_ids = _code_sot_ids()
    bits: list[str] = []
    seen: set[str] = set()
    try:
        from .argus_dev import load

        st = load()
    except Exception:
        st = {}
    for p in st.get("people") or []:
        pid = str((p or {}).get("id") or "").strip().lower()
        if not pid or pid not in code_ids:
            continue
        desc = ", ".join(
            str(p[k])[:60] for k in ("clothes", "hair", "sex") if (p or {}).get(k)
        )
        bits.append(f"{pid}={desc}" if desc else pid)
        seen.add(pid)
    for n in sorted(code_ids):
        if n not in seen:
            bits.append(n)
    held = "; ".join(bits) if bits else "empty"
    return (
        f"YOU HOLD hc={hc} [{held}]. Occupancy from the room SoT, "
        "not a second ledger.\n"
    )


def _clock_prompt(
    trigger_ts: float,
    ts_local: str,
    rest_ts: float | None,
    end_seq: int | None,
    eid: str | None = None,
    clip_gap: list | None = None,
) -> str:
    """Event clock + last look. Argus needs to know where he sits in time."""
    if not _LAST_LOOK:
        loaded = _load_last_look()
        if loaded:
            with _LOCK:
                if not _LAST_LOOK:
                    _LAST_LOOK.update(loaded)
    lines = [
        f"\nCLOCK trigger_ts={trigger_ts} ts_local={ts_local}",
        _hold_line().rstrip(),
    ]
    if rest_ts:
        lines.append(
            f"rest_ts={rest_ts} end_seq={end_seq} "
            "FOV matched empty-door ref. That still is the END of movement. "
            "All action is BEFORE it. Empty last still is expected after "
            "enter and after exit — not 'no crossing.' Judge the clip."
        )
    else:
        lines.append("rest_ts=null FOV has not matched empty-door ref yet.")
    for g in clip_gap or []:
        lines.append(str(g))
    with _LOCK:
        prev = dict(_LAST_LOOK)
    same = bool(eid) and prev.get("eid") == eid
    if (prev.get("event") or prev.get("comment")) and not same:
        ev = prev.get("event") or "?"
        who = prev.get("who") or "null"
        when = prev.get("ts_local") or prev.get("eid") or "prior"
        extra = " ".join(
            f"{k}={prev[k]}" for k in ("clothes", "hair", "sex") if prev.get(k)
        )
        comment = str(prev.get("comment") or "").replace("\n", " ")[:180]
        lines.append(
            f"LAST you said at {when}: event={ev} who={who} {extra} {comment}".rstrip()
        )
    return "\n".join(x for x in lines if x) + "\n"


def _ask_argus(
    eid: str,
    trigger_ts: float,
    window: list | None = None,
    rest_ts: float | None = None,
    clip_gap: list | None = None,
) -> dict[str, Any]:
    """Video of the crossing -> event / clothes / hair / sex. Never identity."""
    from .argus_notify import (
        oracle_system,
        _post_glimmer_video,
        _reply_text,
        parse_argus_visit,
        present_block,
        video_sampling_kwargs,
    )
    from .oracle_event import stamp_argus
    from .raw_clip import clip_for_event

    if window:
        from .raw_clip import encode_mp4

        # Cap at ARGUS_MAX_FRAMES even slides (default 20). 0 = full even
        # window. media_io_kwargs fps=N/duration is what Qwen actually sees.
        #
        # rest_ts = end of movement. Empty tail is the extra still, not the clip.
        full = list(window)
        window = _action_before_rest(window, rest_ts)
        if len(window) < ARGUS_MIN_FRAMES and len(full) >= ARGUS_MIN_FRAMES:
            window = full
        if ARGUS_MAX_FRAMES > 0 and len(window) > ARGUS_MAX_FRAMES:
            trig_seq = None
            if trigger_ts:
                trig_seq = min(
                    window, key=lambda fr: abs(float(fr[0]) - float(trigger_ts))
                )[1]
            elif window:
                trig_seq = window[0][1]
            picked = pick_frames(
                window,
                ARGUS_MAX_FRAMES,
                trigger_seq=int(trig_seq or 0),
                trigger_ts=trigger_ts,
            )
            if len(picked) < ARGUS_MIN_FRAMES:
                n = ARGUS_MAX_FRAMES if ARGUS_MAX_FRAMES % 2 == 0 else ARGUS_MAX_FRAMES - 1
                n = min(n, len(window) - len(window) % 2)
                if n >= 2:
                    picked = [
                        window[int(i * (len(window) - 1) / max(1, n - 1))]
                        for i in range(n)
                    ]
            window = picked
        paths_in = [p for _t, _s, p in window]
        times_in = [float(_t) for _t, _s, _p in window]
        if len(paths_in) % 2:
            paths_in = paths_in[:-1]
            times_in = times_in[:-1]
        mp4 = (
            encode_mp4(paths_in, timestamps=times_in)
            if len(paths_in) >= 2
            else None
        )
        dur = None
        if len(times_in) >= 2:
            try:
                dur = float(times_in[-1]) - float(times_in[0])
            except (TypeError, ValueError):
                dur = None
        clip = {
            "mp4": mp4, "n_frames": len(paths_in), "paths": paths_in,
            "duration_s": dur,
            "onset": {"ts": window[0][0], "seq": window[0][1],
                      "source": "shared_window"},
        } if mp4 else None
    else:
        clip = clip_for_event(trigger_ts, onset_ts=trigger_ts)
    if not clip:
        return {"ok": False, "reason": "no_clip"}
    # Keep the exact bytes posted, and the exact frames encoded into them.
    paths = clip.get("paths") or []
    seqs: list[int] = []
    import re as _re

    for i, p in enumerate(paths):
        m = _re.search(r"_(\d+)\.jpg$", p.name)
        if m:
            seqs.append(int(m.group(1)))
        try:
            _keep(eid, f"argus_{i:03d}_seq{m.group(1) if m else i}.jpg", p.read_bytes())
        except OSError:
            pass
    _keep(eid, "argus.mp4", clip["mp4"])
    stills: list[bytes] = []
    end_jpeg, end_ts, end_seq = (None, None, None)
    if rest_ts:
        end_jpeg, end_ts, end_seq = _jpeg_at(rest_ts)
        if end_jpeg:
            _keep(eid, "argus_end.jpg", end_jpeg)
            stills.append(end_jpeg)
    ts_local = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(float(trigger_ts or 0))
    )
    facts = present_block() + _clock_prompt(
        trigger_ts, ts_local, rest_ts, end_seq, eid=eid, clip_gap=clip_gap
    )
    ask = "Judge this clip. Oldest first. USER EXIT? true, false, or null.\n"
    prompt = (facts.rstrip() + "\n" + ask) if facts.strip() else ask
    _keep(eid, "prompt.txt", prompt.encode())
    sys_p = oracle_system()
    if sys_p:
        _keep(eid, "system.txt", sys_p.encode())
    before = _crew_metrics()
    t0 = time.perf_counter()
    n_enc = int(clip.get("n_frames") or len(paths) or 0)
    dur = clip.get("duration_s")
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    out = _post_glimmer_video(
        clip["mp4"], prompt, stills=stills or None,
        n_frames=n_enc or None, duration_s=dur,
    )
    wall_ms = (time.perf_counter() - t0) * 1000.0
    timing = _argus_timing(before, _crew_metrics(), wall_ms)
    usage = out.get("usage") if isinstance(out, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    vkw = video_sampling_kwargs(n_enc or None, dur)
    v_io = (vkw.get("media_io_kwargs") or {}).get("video") or {}
    timing["n_encoded"] = n_enc
    timing["video_num_frames"] = v_io.get("num_frames")
    timing["video_fps"] = v_io.get("fps")
    timing["prompt_tokens"] = usage.get("prompt_tokens")
    timing["completion_tokens"] = usage.get("completion_tokens")
    msg = _reply_text(out)
    visit = parse_argus_visit(msg) or {}
    if not visit:
        return {"ok": False, "reason": "unparsed", "reply_len": len(msg or ""),
                "n_frames": len(paths), "seqs": seqs, "reply": (msg or "")[:400],
                "prompt": prompt, **timing}
    stamp_argus(
        eid,
        event=visit.get("event"),
        clothes=visit.get("clothes"),
        hair=visit.get("hair"),
        sex=visit.get("sex"),
        note=msg[:200],
    )
    with _LOCK:
        _STATS["argus_ok"] += 1
        _LAST_LOOK.clear()
        _LAST_LOOK.update({
            "ts_local": ts_local,
            "trigger_ts": trigger_ts,
            "event": visit.get("event"),
            "who": visit.get("who"),
            "clothes": visit.get("clothes"),
            "hair": visit.get("hair"),
            "sex": visit.get("sex"),
            "comment": (msg or "")[:240],
            "eid": eid,
        })
    _save_last_look()
    return {"ok": True, "n_frames": clip["n_frames"], "seqs": seqs,
            "reply": (msg or "")[:400], "prompt": prompt, **timing, **visit}


def _ask_faceid(eid: str, trigger_ts: float, window: list | None = None) -> dict[str, Any]:
    """Largest face in the crossing window -> person_id. Never the event."""
    from .entry_face import best_face_in_pack
    from .oracle_event import stamp_faceid
    from .raw_clip import action_window, best_face_window

    # THE SAME frames Argus gets. the User 2026-08-16: "the movie got an arm and the
    # faceid no face got the exit. Something wrong here." Argus was handed 8
    # action-bounded frames while this scanned a fixed 16, so FaceID ran on
    # past the crossing into a back at the dresser that Argus never saw. Two
    # witnesses judging different footage cannot corroborate each other.
    #
    # Computed here rather than passed in: the two run in parallel threads on
    # purpose, and handing one the other's work would make it a dependency.
    win = [p for _t, _s, p in (window or action_window(trigger_ts))]
    if not win:
        win = best_face_window(trigger_ts, start_frame=0)
    full = list(win)
    if FACEID_STRIDE > 1 and len(full) > 4:
        win = full[::FACEID_STRIDE]
    if not win:
        return {"ok": False, "reason": "no_frames"}
    # Every frame FaceID was shown, kept at fire time.
    import re as _re

    seqs: list[int] = []
    for i, p in enumerate(win):
        m = _re.search(r"_(\d+)\.jpg$", p.name)
        if m:
            seqs.append(int(m.group(1)))
        try:
            _keep(eid, f"faceid_{i:03d}_seq{m.group(1) if m else i}.jpg", p.read_bytes())
        except OSError:
            pass
    # best_face_in_pack scores face_area x det_score and crops; pack_dir is only
    # used for the glob it is being handed an explicit list instead of.
    v = best_face_in_pack(win[0].parent, frames=win)
    # Refine at full rate around whatever the coarse pass liked best.
    if v and FACEID_STRIDE > 1 and len(full) > len(win):
        try:
            wi = next(i for i, f in enumerate(full) if str(f) == str(v.get("frame")))
            near = full[max(0, wi - FACEID_REFINE):wi + FACEID_REFINE + 1]
            v2 = best_face_in_pack(win[0].parent, frames=near)
            if v2 and float(v2.get("score") or 0) > float(v.get("score") or 0):
                v = v2
        except (StopIteration, ValueError, TypeError):
            pass
    if not v:
        return {"ok": False, "reason": "no_face", "n_frames": len(win), "seqs": seqs}
    # The winning frame and the crop that was actually matched against the bank.
    try:
        chosen = pathlib.Path(str(v.get("frame") or ""))
        if chosen.is_file():
            _keep(eid, "faceid_chosen.jpg", chosen.read_bytes())
            from .entry_face import _crop_face

            crop = _crop_face(chosen.read_bytes(), v.get("bbox"))
            if crop:
                _keep(eid, "faceid_crop.jpg", crop)
    except Exception:
        pass
    stamp_faceid(
        eid,
        name=v.get("name"),
        sim=v.get("sim"),
        face_px=v.get("face_px"),
        door_view=v.get("door_view"),
        gender=v.get("soft_gender"),
    )
    try:
        named = str(v.get("name") or "").strip().lower()
        sim_f = float(v.get("sim") or 0)
    except (TypeError, ValueError):
        named, sim_f = "", 0.0
    if not named or sim_f < 0.60:
        try:
            from .stranger_x import record_event

            chosen = pathlib.Path(str(v.get("frame") or ""))
            jpeg = chosen.read_bytes() if chosen.is_file() else None
            crop = None
            try:
                from .entry_face import _crop_face

                crop = _crop_face(jpeg, v.get("bbox")) if jpeg else None
            except Exception:
                crop = None
            record_event(
                kind="enter",
                jpeg=jpeg,
                crop=crop,
                bbox=v.get("bbox"),
                sex=v.get("soft_gender"),
                source="door_mill",
                event_id=eid,
                named_sim=sim_f if sim_f else None,
            )
        except Exception:
            pass
    if v.get("name"):
        with _LOCK:
            _STATS["faceid_named"] += 1
    return {"ok": True, "name": v.get("name"), "sim": v.get("sim"),
            "face_px": v.get("face_px"), "bbox": v.get("bbox"),
            "det_score": v.get("det_score"), "door_view": v.get("door_view"),
            "n_frames": len(win), "seqs": seqs,
            "chosen": pathlib.Path(str(v.get("frame") or "")).name}


def poll_once(now: float | None = None) -> dict[str, Any] | None:
    """Ingest → promote every item → fire oldest ready. One Argus at a time."""
    now = time.time() if now is None else now
    try:
        triggers = _fetch_triggers()
    except Exception as e:
        with _LOCK:
            _STATS["last_error"] = f"{type(e).__name__}: {e}"[:160]
        return None
    n_new = _ingest(triggers, now)
    if n_new:
        _fire_glance()
    _promote(now)
    _beat_door_mill()
    with _LOCK:
        blinds = [
            it for it in _PENDING.values()
            if it.get("state") == "blind" and not it.get("blind_logged")
        ]
        for it in blinds:
            it["blind_logged"] = True
    for it in blinds:
        _mark_blind_count_trust(it)
    out = _service(now)
    if out:
        return out
    if not pending_work() and _BUSY_SENT[0] and _door_in_view():
        tref = _BUSY_TRIG_TS[0] or now
        try:
            rest = _room_restored(tref, now)
        except Exception:
            rest = False
        if rest is True:
            _ensure_busy(False, "empty_ref")
            _beat_door_mill()
    return None


EVIDENCE_DIR = pathlib.Path(os.environ.get(
    "RAIL_DOOR_FANOUT_EVIDENCE",
    str(_paths.DATA / "fanout_evidence"),
))
# ~2 MB an event (11 full-rate jpegs plus the mp4). Capped, because an evidence
# store that grows without bound becomes the next thing to debug.
EVIDENCE_KEEP = int(os.environ.get("RAIL_DOOR_FANOUT_EVIDENCE_KEEP", "40"))
EVIDENCE_ON = os.environ.get("RAIL_DOOR_FANOUT_EVIDENCE_ON", "1") not in (
    "0", "false", "no",
)


def evidence_dir(eid: str) -> pathlib.Path:
    return EVIDENCE_DIR / str(eid)


def _prune_evidence() -> None:
    try:
        dirs = sorted(
            (d for d in EVIDENCE_DIR.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
        )
    except OSError:
        return
    import shutil

    for d in dirs[:-EVIDENCE_KEEP] if len(dirs) > EVIDENCE_KEEP else []:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _keep(eid: str, name: str, data: bytes) -> str:
    """Write one piece of evidence. Returns the stored filename, or ''.

    Captured at fire time on purpose: raw is pruned within the hour, so anything
    reconstructed afterwards is a survivor-biased subset rather than what the
    model was actually handed.
    """
    if not EVIDENCE_ON:
        return ""
    try:
        d = evidence_dir(eid)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        return name
    except OSError:
        return ""


LOG_PATH = os.environ.get(
    "RAIL_DOOR_FANOUT_LOG",
    str(_paths.DATA / "door_fanout.jsonl"),
)


def _record(out: dict[str, Any]) -> None:
    """Say what happened, on stdout and on disk.

    The first version of this module logged nothing and wrote nothing, so after
    a real lap on 2026-08-16 there was no way to tell whether it had fired, and
    the answer had to be inferred from the trigger ring. A witness that leaves
    no trace cannot be debugged and should not be trusted.
    """
    a = out.get("argus") or {}
    b = out.get("faceid") or {}
    ap = out.get("applied") or {}
    line = (
        f"[door-fanout] seq={out.get('trigger_seq')} "
        f"event={a.get('event') or a.get('reason') or a.get('error')} "
        f"who={b.get('name') or b.get('reason') or b.get('error')} "
        f"sim={b.get('sim')} "
        f"applied={ap.get('applied')} pid={ap.get('person_id')} "
        f"wall_ms={a.get('wall_ms')} queued={a.get('queued')} "
        f"waiting={a.get('waiting')} wait_cap={a.get('waiting_capacity')} "
        f"infer_ms={a.get('infer_ms')} "
        f"n_enc={a.get('n_encoded')} n_req={a.get('video_num_frames')} "
        f"fps={a.get('video_fps')} pt={a.get('prompt_tokens')} "
        f"reason={out.get('reason') or ap.get('reason') or ap.get('error') or ''} "
        f"unobserved={bool(out.get('unobserved'))} "
        f"slew_peak={out.get('slew_peak')} slew={out.get('slew')} "
        f"slew_corroborated={out.get('slew_corroborated')} "
        f"queue_depth={stats().get('queue_depth')}"
    )
    print(line, flush=True)
    comment = str(a.get("comment") or a.get("reply") or "").strip()
    # Glass keeps the last Argus line. Unapplied tails ("no one crossed")
    # must not replace a verdict the house actually took.
    if comment and a.get("ok") and ap.get("applied"):
        try:
            from .argus_notify import post_omni_note

            post_omni_note(comment, pack_id=str(out.get("event_id") or ""))
        except Exception:
            pass
    # The evidence dir is self-describing: the page renders from meta.json plus
    # the jpegs beside it, with no need to re-derive anything from raw.
    eid = str(out.get("event_id") or "")
    if eid and EVIDENCE_ON:
        try:
            d = evidence_dir(eid)
            if d.is_dir():
                (d / "meta.json").write_text(
                    json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8"
                )
            _prune_evidence()
        except OSError:
            pass
    try:
        p = pathlib.Path(LOG_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                "trigger_seq": out.get("trigger_seq"),
                "trigger_ts": out.get("trigger_ts"),
                "event_id": out.get("event_id"),
                "window": out.get("window"),
                "argus": a,
                "faceid": b,
                "applied": ap,
                "row": out.get("row"),
                "reason": out.get("reason") or ap.get("reason"),
                "unobserved": bool(out.get("unobserved")),
                "blind_window": out.get("blind_window"),
                "slew_peak": out.get("slew_peak"),
                "slew_corroborated": out.get("slew_corroborated"),
                "slew": out.get("slew"),
                "trimmed_spans": out.get("trimmed_spans"),
                "queue_depth": stats().get("queue_depth"),
                "lease_owner": stats().get("lease_owner"),
            }, default=str) + "\n")
    except OSError:
        pass


def _loop() -> None:
    while True:
        try:
            out = poll_once()
            if out:
                _record(out)
        except Exception as e:
            with _LOCK:
                _STATS["last_error"] = f"{type(e).__name__}: {e}"[:160]
            print(f"[door-fanout] error: {type(e).__name__}: {e}"[:200], flush=True)
        time.sleep(POLL_SEC)


def start() -> bool:
    """Start the poller once. Returns False when disabled or already running."""
    global _THREAD
    if not ENABLED:
        return False
    if _ports.camera_off():                      # the poller reads the camera's trigger ring (2026-10-10)
        _ports.say_once("the door trigger fan-out does not start")
        return False
    _load_pending()
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _THREAD = threading.Thread(target=_loop, name="door-fanout", daemon=True)
        _THREAD.start()
    return True
