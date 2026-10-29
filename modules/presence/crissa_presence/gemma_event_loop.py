"""Gemma door watchman — ONE job only (the User 2026-07-27).

  On door motion → watch live frames → output occupancy delta:
      action: add | sub | null
  That delta is applied to presence SoT (POST /v1/vl_delta).

  ZERO security / naming / auth. CODE/FaceID/voice do IDs elsewhere.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import base64
import json
import os
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable

# ── knobs ────────────────────────────────────────────────────────────
# the User 2026-08-02: ONE episode per door action until FOV is still (off-frame).
# No fixed 5s / "oracle time" cut. Pre-roll from ring, then record until still.
# Back-to-back thr while the episode is open must STITCH, not new clips.
CAPTURE_FPS = float(os.environ.get("RAIL_GEMMA_EVENT_FPS", "8"))  # 30/stride≈8
STRIDE = max(1, int(round(30.0 / CAPTURE_FPS)))
# Ring dump before thr (thr is late — onset missing without this)
PRE_ROLL_SEC = float(os.environ.get("RAIL_GEMMA_EVENT_PREROLL_SEC", "2.0"))
# End episode only when FOV quiet (same energy family as grabber thr=8)
STILL_ENERGY = float(os.environ.get("RAIL_GEMMA_EVENT_STILL_ENERGY", "3.5"))
STILL_FRAMES = int(os.environ.get("RAIL_GEMMA_EVENT_STILL_FRAMES", "10"))  # ~1.25s @ 8fps
# Bound disk only: if episode is long (linger at door=12), thin kept frames — never
# force a VL verdict mid-action. Episode still ends only on still.
MAX_KEPT_FRAMES = int(os.environ.get("RAIL_GEMMA_EVENT_MAX_KEPT", "240"))  # ~30s @ 8fps dense
# legacy alias
WINDOW_SEC = float(os.environ.get("RAIL_GEMMA_EVENT_WINDOW_SEC", "0"))  # unused for end
MAX_EVENT_SEC = float(os.environ.get("RAIL_GEMMA_EVENT_MAX_SEC", "0"))  # 0 = no time end
# subsample for VL; fewer clearer frames beat many tiny ones
VL_FRAMES = int(os.environ.get("RAIL_GEMMA_EVENT_VL_FRAMES", "12"))
MAX_SIDE = int(os.environ.get("RAIL_GEMMA_EVENT_MAX_SIDE", "512"))
MAX_NEW = int(os.environ.get("RAIL_GEMMA_EVENT_MAX_NEW", "96"))
# Thr within this gap of last episode thr → same event (stitch), not a new clip
COALESCE_GAP_SEC = float(os.environ.get("RAIL_GEMMA_EVENT_COALESCE_SEC", "8.0"))
QUEUE_MAX = int(os.environ.get("RAIL_GEMMA_EVENT_QUEUE_MAX", "6"))
LESSONS_KEEP = int(os.environ.get("RAIL_GEMMA_EVENT_LESSONS", "8"))
# Peek / facefinder on delta: DEFAULT OFF. the User 2026-08-09: facefinder is
# reauth/error-correction only (sensitive request + uncertain AUTH, or after
# lights/power outage) — never routine count reconcile on every delta.
PEEK_ON_DELTA = os.environ.get("RAIL_GEMMA_PEEK_ON_DELTA", "0") not in (
    "0",
    "false",
    "no",
)
# Explicit opt-in for seat facefinder peeks from Gemma (normally never).
PEEK_FACEFINDER = os.environ.get("RAIL_GEMMA_PEEK_FACEFINDER", "0") not in (
    "0",
    "false",
    "no",
)
PRESENCE = (os.environ.get("RAIL_PRESENCE_URL") or os.environ.get("RAIL_PRESENCE_BASE")  # this install's own (2026-10-10)
            or _ports.url("presence")).rstrip("/")
CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
SMOOTH = os.environ.get(
    "RAIL_ORACLE_SMOOTH_SCAN", f"{CAMERA}/v1/smooth_scan"
)
WORK = Path(
    os.environ.get(
        "RAIL_ORACLE_WORK",
        str(
            _paths.DATA / "work/oracle_live"
        ),
    )
)
KIT = Path(
    os.environ.get(
        "RAIL_GEMMA_KIT",
        str(_paths.DATA / "gemma-home"),
    )
)
LESSONS_PATH = WORK / "gemma_lessons.jsonl"
EVENTS_DIR = WORK / "gemma_events"

_BRIEF_CACHE: str | None = None


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or time.time()))


def _http_json(url: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _load_brief() -> str:
    """Load SOUL + AGENT + SKILL from kit only (the User: schema lives in SKILL.md).

    Hands code does not invent structured-output shapes — SKILL.md is SoT for
    emit format; apply_vl_delta is SoT for metrics.
    """
    global _BRIEF_CACHE
    if _BRIEF_CACHE is not None:
        return _BRIEF_CACHE
    parts: list[str] = []
    # SKILL first so structured-output contract wins attention
    for name in ("SKILL.md", "SOUL.md", "AGENT.md"):
        p = KIT / name
        if p.is_file():
            # cap hard — long SOUL was drowning a small VL
            cap = 4000 if name == "SKILL.md" else 1200
            parts.append(p.read_text(encoding="utf-8", errors="replace")[:cap])
    if not any((KIT / n).is_file() for n in ("SKILL.md",)):
        parts.append(
            'FALLBACK SKILL missing on disk. JSON only: '
            '{"action":"add"|"sub"|"null","n":1,"need_peek":false,"note":"…"}'
        )
    _BRIEF_CACHE = "\n\n".join(parts)
    return _BRIEF_CACHE


def normalize_verdict(raw: dict | None) -> dict[str, Any]:
    """Normalize model JSON → streaming occupancy object + SoT deltas.

    Streaming object:
      action: add | sub | null
      n: int >= 1 (ignored when null)
      entered / left: ints for SoT apply
    """
    v = dict(raw or {})
    action = str(v.get("action") or v.get("op") or v.get("delta") or "").strip().lower()
    # legacy entered/left
    try:
        ent = max(0, int(v.get("entered") or 0))
    except (TypeError, ValueError):
        ent = 0
    try:
        left = max(0, int(v.get("left") or 0))
    except (TypeError, ValueError):
        left = 0
    try:
        n = max(1, int(v.get("n") or 1))
    except (TypeError, ValueError):
        n = 1

    if action in ("add", "enter", "in", "+1", "plus", "inc", "increment"):
        action = "add"
        ent, left = n, 0
    elif action in ("sub", "subtract", "leave", "exit", "out", "-1", "minus", "dec", "decrement"):
        action = "sub"
        ent, left = 0, n
    elif action in ("null", "none", "noop", "0", "zero", "nochange", "no_change"):
        action = "null"
        ent, left = 0, 0
    elif ent > 0 and left == 0:
        action = "add"
        n = ent
    elif left > 0 and ent == 0:
        action = "sub"
        n = left
    elif ent == 0 and left == 0:
        action = "null"
        n = 1
    else:
        # simultaneous in+out — treat as null unless forced (peek path)
        action = "null"
        ent, left = 0, 0
        n = 1
        v["need_peek"] = True

    note = str(v.get("note") or v.get("lesson") or "")[:80]
    return {
        "action": action,
        "n": n if action != "null" else 0,
        "entered": ent,
        "left": left,
        "need_peek": bool(v.get("need_peek")),
        "count_trust": "fact" if action != "null" or not v.get("need_peek") else "unsure",
        "note": note,
        "lesson": str(v.get("lesson") or note)[:80],
    }


def _read_lessons(n: int = LESSONS_KEEP) -> list[dict]:
    if not LESSONS_PATH.is_file():
        return []
    try:
        lines = LESSONS_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _append_lesson(rec: dict) -> None:
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        with LESSONS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def _shrink_jpeg(raw: bytes, max_side: int = MAX_SIDE) -> bytes:
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(raw, dtype=np.uint8)
        im = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if im is None:
            return raw
        h, w = im.shape[:2]
        m = max(h, w)
        if m > max_side:
            s = max_side / float(m)
            im = cv2.resize(im, (max(1, int(w * s)), max(1, int(h * s))))
        ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        return buf.tobytes() if ok else raw
    except Exception:
        return raw


def _buffer_delta(since_seq: int | None, *, stride: int = 1, max_n: int = 40) -> list[dict]:
    """Pull only NEW ring frames after since_seq (live follow, not full dump)."""
    try:
        body = _http_json(
            f"{CAMERA}/v1/buffer",
            {
                "since_seq": since_seq,
                "stride": max(1, int(stride)),
                "max": max_n,
                "include_jpeg": True,
                "jpeg_b64": True,
            },
            timeout=8,
        )
        return list(body.get("frames") or [])
    except Exception:
        return []


def _camera_seq() -> int:
    try:
        h = _http_json(f"{CAMERA}/health", timeout=2)
        return int(h.get("seq") or 0)
    except Exception:
        return 0


def _pull_preroll(
    *,
    trigger_seq: int,
    pre_roll_sec: float,
    fps: float,
    ed: Path,
) -> tuple[list[dict], list[Path], float | None]:
    """Pull ~pre_roll_sec of ring frames ending at/before trigger_seq.

    Returns (meta, paths, trigger_ts_est). t_rel will be negative relative to trigger.
    """
    if pre_roll_sec <= 0:
        return [], [], None
    period = 1.0 / max(1.0, float(fps))
    try:
        # Full recent ring (GET-style via POST without since_seq)
        body = _http_json(
            f"{CAMERA}/v1/buffer",
            {
                "max": int(pre_roll_sec * 30) + 16,
                "stride": 1,
                "include_jpeg": True,
                "jpeg_b64": True,
            },
            timeout=8,
        )
    except Exception:
        return [], [], None
    frames_in = list(body.get("frames") or [])
    if not frames_in:
        return [], [], None

    # Estimate trigger time from last frame at/under trigger_seq
    trigger_ts = None
    for fr in frames_in:
        try:
            si = int(fr.get("seq"))
        except (TypeError, ValueError):
            continue
        if si <= int(trigger_seq):
            trigger_ts = float(fr.get("capture_ts") or fr.get("ts") or 0) or trigger_ts
    if trigger_ts is None:
        # fallback: newest frame ts in pull
        try:
            trigger_ts = float(
                frames_in[-1].get("capture_ts") or frames_in[-1].get("ts") or 0
            )
        except Exception:
            trigger_ts = time.time()

    t_lo = float(trigger_ts) - float(pre_roll_sec)
    # keep frames in [t_lo, trigger_ts] with seq <= trigger_seq
    cand: list[dict] = []
    for fr in frames_in:
        try:
            si = int(fr.get("seq"))
            ts = float(fr.get("capture_ts") or fr.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if si > int(trigger_seq):
            continue
        if ts < t_lo - 0.02:
            continue
        cand.append(fr)
    # thin to ~fps
    paths: list[Path] = []
    meta: list[dict] = []
    prev_t = None
    for fr in cand:
        ts = float(fr.get("capture_ts") or fr.get("ts") or 0)
        if prev_t is not None and ts - prev_t < period * 0.85:
            continue
        b64 = fr.get("jpeg_b64")
        if not b64:
            continue
        try:
            raw = _shrink_jpeg(base64.b64decode(b64), MAX_SIDE)
        except Exception:
            continue
        si = fr.get("seq")
        i = len(paths)
        p = ed / f"f{i:03d}_seq{si}.jpg"
        p.write_bytes(raw)
        paths.append(p)
        meta.append(
            {
                "i": i,
                "seq": si,
                "ts": ts,
                "t_rel": round(ts - float(trigger_ts), 3),  # ≤ 0
                "path": str(p),
                "phase": "preroll",
            }
        )
        prev_t = ts
    return meta, paths, float(trigger_ts) if trigger_ts else None


def _frame_motion_energy(jpeg: bytes, prev_gray: Any) -> tuple[float, Any]:
    """Same family as grabber thr: 160×90 gray absdiff mean."""
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(jpeg, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 0.0, prev_gray
        small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            return 0.0, gray
        return float(cv2.absdiff(gray, prev_gray).mean()), gray
    except Exception:
        return 0.0, prev_gray


def capture_live_window(
    *,
    window_sec: float = 0.0,
    fps: float = CAPTURE_FPS,
    since_seq: int | None = None,
    pre_roll_sec: float | None = None,
) -> tuple[list[dict], list[Path]]:
    """ONE door episode until person leaves FOV / FOV goes still.

    Waiting-room model (the User 2026-08-02):
      Door FOV ≈ clock seat **12** is a transitional *waiting room*.
      While action continues there, it is still the **same event** — not a new
      timed clip. Record until FOV is quiet (still) so the person resolves to
      fully **in** or fully **out**. Enter+leave in one tape → net null.
      Back-to-back thr during the episode stitch into this tape (coalesce).

      Pre-roll (~2s) from ring before thr so onset is not already mid-room.
      Disk: thin kept frames if very long linger — never cut for "oracle time".
    """
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    eid = f"ev_{int(time.time() * 1000)}"
    ed = EVENTS_DIR / eid
    ed.mkdir(parents=True, exist_ok=True)

    period = 1.0 / max(1.0, float(fps))
    t_start = time.time()
    pre = float(PRE_ROLL_SEC if pre_roll_sec is None else pre_roll_sec)
    meta: list[dict] = []
    paths: list[Path] = []
    trigger_ts: float | None = None
    preroll_n = 0

    last_seq = int(since_seq) if since_seq is not None else _camera_seq()
    if since_seq is not None and pre > 0:
        pm, pp, trigger_ts = _pull_preroll(
            trigger_seq=int(since_seq),
            pre_roll_sec=pre,
            fps=fps,
            ed=ed,
        )
        meta.extend(pm)
        paths.extend(pp)
        preroll_n = len(pp)

    t0 = float(trigger_ts) if trigger_ts is not None else t_start
    prev_gray: Any = None
    still_run = 0
    resolve = "running"
    max_energy = 0.0

    if paths:
        try:
            e0, prev_gray = _frame_motion_energy(paths[-1].read_bytes(), None)
            max_energy = max(max_energy, e0)
        except Exception:
            pass

    # End ONLY on FOV still (waiting room empty / quiet). No oracle-time cut.
    while True:
        frs = _buffer_delta(last_seq, stride=1, max_n=24)
        if not frs:
            time.sleep(period)
            if (len(paths) - preroll_n) >= 4:
                still_run += 1
                if still_run >= STILL_FRAMES:
                    resolve = "waiting_room_clear"  # FOV quiet — in or out resolved
                    break
            continue
        for fr in frs:
            seq = fr.get("seq")
            if seq is None:
                continue
            si = int(seq)
            if si <= last_seq:
                continue
            ts = float(fr.get("ts") or fr.get("capture_ts") or time.time())
            if paths:
                prev_t = float(meta[-1].get("ts") or t0)
                if ts - prev_t < period * 0.85:
                    last_seq = si
                    continue
            b64 = fr.get("jpeg_b64")
            if not b64:
                last_seq = si
                continue
            try:
                raw = base64.b64decode(b64)
            except Exception:
                last_seq = si
                continue
            raw = _shrink_jpeg(raw, MAX_SIDE)
            energy, prev_gray = _frame_motion_energy(raw, prev_gray)
            max_energy = max(max_energy, energy)
            if energy < STILL_ENERGY:
                still_run += 1
            else:
                still_run = 0
            # Long linger in waiting room (12): thin disk, keep episode open
            if len(paths) >= MAX_KEPT_FRAMES and still_run == 0:
                # drop every other live frame from middle (keep ends + recent)
                live = [
                    (m, p)
                    for m, p in zip(meta, paths)
                    if m.get("phase") != "preroll"
                ]
                if len(live) > 32:
                    keep_idx = set(range(0, len(live), 2)) | set(
                        range(max(0, len(live) - 16), len(live))
                    )
                    prer = [
                        (m, p)
                        for m, p in zip(meta, paths)
                        if m.get("phase") == "preroll"
                    ]
                    kept = prer + [live[i] for i in sorted(keep_idx)]
                    # unlink dropped files
                    keep_paths = {p for _, p in kept}
                    for old in paths:
                        if old not in keep_paths:
                            try:
                                old.unlink(missing_ok=True)
                            except Exception:
                                pass
                    meta = [m for m, _ in kept]
                    paths = [p for _, p in kept]
                    for i, m in enumerate(meta):
                        m["i"] = i
            i = len(paths)
            p = ed / f"f{i:03d}_seq{si}.jpg"
            p.write_bytes(raw)
            paths.append(p)
            meta.append(
                {
                    "i": i,
                    "seq": si,
                    "ts": ts,
                    "t_rel": round(ts - t0, 3),
                    "path": str(p),
                    "phase": "waiting_room" if energy >= STILL_ENERGY else "live",
                    "energy": round(energy, 2),
                    "still_run": still_run,
                }
            )
            last_seq = si
            if still_run >= STILL_FRAMES and (len(paths) - preroll_n) >= 4:
                resolve = "waiting_room_clear"
                break
        if resolve == "waiting_room_clear":
            break
        time.sleep(min(period, 0.05))

    if not paths:
        resolve = "empty"
        capture_mode = "empty"
    else:
        capture_mode = (
            "waiting_room_until_clear+preroll"
            if preroll_n
            else "waiting_room_until_clear"
        )

    (ed / "manifest.json").write_text(
        json.dumps(
            {
                "id": eid,
                "pre_roll_sec": pre,
                "preroll_n": preroll_n,
                "still_energy": STILL_ENERGY,
                "still_frames": STILL_FRAMES,
                "trigger_seq": since_seq,
                "trigger_ts": trigger_ts,
                "fps": fps,
                "capture_mode": capture_mode,
                "resolve": resolve,
                "max_energy": round(max_energy, 2),
                "n": len(paths),
                "wall_sec": round(time.time() - t_start, 3),
                "frames": meta,
                "model": {
                    "door_fov": "waiting_room",
                    "clock_seat": 12,
                    "end": "only when FOV still (person resolved in or out)",
                    "stitch": "coalesce thr while open — not back-to-back clips",
                },
                "note": (
                    "Seat 12 / door FOV = waiting room for the remainder of the action. "
                    "One episode until clear; enter+leave same tape → null. "
                    f"Still = {STILL_FRAMES} frames energy<{STILL_ENERGY}."
                ),
            },
            indent=2,
        )
    )
    if meta:
        meta[0]["_episode_resolve"] = resolve
    return meta, paths


def pull_window_frames(
    *,
    window_sec: float = WINDOW_SEC,
    stride: int = STRIDE,
) -> tuple[list[dict], list[Path]]:
    """Backward-compatible name → live capture from now."""
    return capture_live_window(window_sec=window_sec, fps=CAPTURE_FPS)


def _even_paths(paths: list[Path], n: int) -> list[Path]:
    if len(paths) <= n:
        return paths
    step = (len(paths) - 1) / float(max(1, n - 1))
    return [paths[int(round(i * step))] for i in range(n)]


def _roster_line(room_memory: dict | None) -> str:
    """Count-only prior — never instruct Gemma to re-name people."""
    if not room_memory:
        return "prior headcount: unknown"
    present = room_memory.get("present") or []
    if isinstance(present, dict):
        hc = len(present)
        n_named = sum(1 for k in present if not str(k).startswith("unknown"))
        n_unk = hc - n_named
    else:
        hc = int(room_memory.get("headcount") or len(present))
        n_unk = sum(
            1
            for p in present
            if isinstance(p, dict) and str(p.get("id") or "").startswith("unknown")
        )
        n_named = hc - n_unk
    return (
        f"prior headcount={hc} (named_slots={n_named} unknown_slots={n_unk}). "
        "Do not assign names — security stamp is after your count."
    )


def build_event_question(
    *,
    meta: list[dict],
    pack: dict | None,
    room_memory: dict | None,
    lessons: list[dict],
    resolve: str | None = None,
) -> str:
    lines = [
        "DOOR ACTION — one job: occupancy change at THIS door.",
        f"{len(meta)} frames, time order (counter). Subsample may be shown.",
        "Door FOV is a WAITING ROOM (clock seat 12): while the body is still in that FOV,",
        "the episode is not finished. Tape runs until FOV is quiet (person fully in or out).",
        "Pre-thr onset may be included (t_rel<0).",
        "Did a body fully ENTER the room (add), fully LEAVE (sub), or neither (null)?",
        "Enter then leave in SAME episode → null (net zero). Prefer null if unsure.",
        "No names. Only in/out/null.",
    ]
    res = resolve or (meta[0].get("_episode_resolve") if meta else None)
    if res in ("waiting_room_clear", "off_frame_still", None):
        lines.append(
            "Episode ended when waiting room (door FOV) went still. "
            "Judge the WHOLE visit (plate set + pause + leave in one tape = null, not add)."
        )
    if meta:
        ctr = ", ".join(
            f"i={m['i']} t={m.get('t_rel')}s e={m.get('energy','?')}"
            for m in meta[:: max(1, len(meta) // 6)]
        )
        lines.append("time: " + ctr)
    if lessons:
        lines.append("recent outcomes:")
        for L in lessons[-4:]:
            act = L.get("action") or (
                "add"
                if int(L.get("entered") or 0) > 0
                else ("sub" if int(L.get("left") or 0) > 0 else "null")
            )
            lines.append(
                f"- {L.get('ts_local','')}: {act} — {L.get('note') or L.get('lesson') or '—'}"
            )
    lines.append(
        'JSON only: {"action":"add"|"sub"|"null","n":1,"need_peek":false,'
        '"note":"...","station_hint":null|12}'
    )
    return "\n".join(lines)


def apply_verdict_to_memory(
    verdict: dict,
    room_memory: Any,
) -> dict[str, Any]:
    """Apply entered/left **count** only. No security naming from Gemma.

    New bodies become unknown-* slots. Named leaves: unknowns first; if all
    named, drop oldest by last_seen and leave ID re-stamp to FaceID/voice.
    """
    entered = max(0, int(verdict.get("entered") or 0))
    left = max(0, int(verdict.get("left") or 0))

    present: dict[str, dict] = {}
    if hasattr(room_memory, "present"):
        present = dict(room_memory.present or {})
    elif isinstance(room_memory, dict):
        p = room_memory.get("present") or {}
        if isinstance(p, dict):
            present = dict(p)
        elif isinstance(p, list):
            present = {
                str(x.get("id")): x for x in p if isinstance(x, dict) and x.get("id")
            }

    before = len(present)
    now = time.time()

    # leaves: unknowns first (no "who left" from VL)
    for _ in range(left):
        unks = [k for k in present if str(k).startswith("unknown")]
        if unks:
            present.pop(unks[-1], None)
        elif present:
            oldest = min(
                present.items(),
                key=lambda kv: float((kv[1] or {}).get("last_seen") or 0),
            )[0]
            present.pop(oldest, None)

    for i in range(entered):
        uid = f"unknown-{int(now) % 100000}-{i}"
        present[uid] = {
            "id": uid,
            "first_seen": now,
            "last_seen": now,
            "last_sim": 0.0,
            "last_seen_local": _iso(now),
            "exit_armed": False,
            "appearance": {"summary": "body from motion event (unnamed)"},
            "enter_local": _iso(now),
            "needs_id_stamp": True,
        }

    # Ignore any names Gemma may have hallucinated in verdict.in_room
    # (security is FaceID/Qwen after this).

    if hasattr(room_memory, "present"):
        room_memory.present = present
        if hasattr(room_memory, "events"):
            room_memory.events.append(
                {
                    "kind": "gemma_count_event",
                    "entered": entered,
                    "left": left,
                    "ts": now,
                    "ts_local": _iso(now),
                    "note": verdict.get("note"),
                    "security": "deferred_to_faceid_voice",
                }
            )
        if hasattr(room_memory, "_save"):
            try:
                room_memory._save()
            except Exception:
                pass

    people = []
    for pid, prev in present.items():
        app = prev.get("appearance") if isinstance(prev.get("appearance"), dict) else {}
        is_unk = str(pid).startswith("unknown")
        people.append(
            {
                "id": pid,
                "certain": (not is_unk)
                and float(prev.get("last_sim") or 0) >= 0.45,
                "evidence": "count_slot" if is_unk else "stamped",
                "appearance": app.get("summary") if app else prev.get("appearance"),
                "needs_id_stamp": bool(prev.get("needs_id_stamp") or is_unk),
            }
        )
    return {
        "headcount": len(people),
        "people": people,
        "count_trust": verdict.get("count_trust") or "stale",
        "note": verdict.get("note"),
        "before": before,
        "after": len(people),
        "entered": entered,
        "left": left,
    }


def stamp_ids_after_count(
    room_memory: Any,
    pack: dict | None,
) -> list[str]:
    """After count is adjusted: apply FaceID (pack face_hits) to unknown slots.

    Voice/Qwen stamps arrive via STT/presence separately. Returns list of
    rename notes for the lesson log.
    """
    if room_memory is None or not hasattr(room_memory, "present"):
        return []
    fh = ((pack or {}).get("face_hits") or {}).get("people_best") or {}
    if not fh:
        return []
    present = room_memory.present or {}
    notes: list[str] = []
    # strongest sim first
    ranked = sorted(fh.items(), key=lambda x: -float(x[1] or 0))
    for name, sim in ranked:
        name = str(name).lower()
        sim = float(sim or 0)
        if sim < float(os.environ.get("RAIL_ORACLE_FACE_THR", "0.40")):
            continue
        if name in present:
            # refresh last_seen on already-stamped
            prev = present[name]
            prev["last_seen"] = time.time()
            prev["last_sim"] = sim
            prev["needs_id_stamp"] = False
            continue
        unks = [k for k in list(present.keys()) if str(k).startswith("unknown")]
        if not unks:
            # new named without unknown slot — only if we have room for a body
            # (count already includes them via enter); create named slot
            present[name] = {
                "id": name,
                "first_seen": time.time(),
                "last_seen": time.time(),
                "last_sim": sim,
                "last_seen_local": _iso(),
                "exit_armed": False,
                "enter_local": _iso(),
                "needs_id_stamp": False,
                "appearance": {"summary": f"faceid stamp sim={sim}"},
            }
            notes.append(f"faceid stamped {name} sim={sim:.2f} (new slot)")
            continue
        old_k = unks[0]
        old = present.pop(old_k)
        old["id"] = name
        old["last_sim"] = sim
        old["last_seen"] = time.time()
        old["last_seen_local"] = _iso()
        old["needs_id_stamp"] = False
        present[name] = old
        notes.append(f"faceid stamped {old_k}→{name} sim={sim:.2f}")
    room_memory.present = present
    if notes and hasattr(room_memory, "events"):
        room_memory.events.append(
            {
                "kind": "id_stamp",
                "source": "faceid",
                "notes": notes,
                "ts": time.time(),
                "ts_local": _iso(),
            }
        )
    if hasattr(room_memory, "_save"):
        try:
            room_memory._save()
        except Exception:
            pass
    return notes


def maybe_peek(verdict: dict) -> dict | None:
    """Optional seat facefinder — reauth/outage only (the User 2026-08-09).

    Facefinder is NOT Gemma's count-reconcile tool. Entry packs select the
    best door-FOV face; count stays on the door. Seat FF only when:
      * RAIL_GEMMA_PEEK_FACEFINDER=1 (explicit opt-in), and
      * note/trust signals outage or dark/lights failure, OR PEEK_ON_DELTA.

    Uses look_around (aim → FF ON → engage → capture → FF OFF → door).
    """
    if not PEEK_FACEFINDER:
        return None
    note = str(verdict.get("note") or "").lower()
    trust = str(verdict.get("count_trust") or "").lower()
    ent = int(verdict.get("entered") or 0)
    left = int(verdict.get("left") or 0)
    outage = any(
        w in note
        for w in (
            "outage",
            "power",
            "blackout",
            "lights_out",
            "lights out",
            "dark",
            "unreadable",
        )
    ) or trust in ("outage", "lights_out")
    hard = outage or (PEEK_ON_DELTA and (ent > 0 or left > 0 or bool(verdict.get("need_peek"))))
    if not hard:
        return None
    look_url = os.environ.get(
        "RAIL_ORACLE_LOOK_AROUND", f"{CAMERA}/v1/look_around"
    )
    engage = float(os.environ.get("RAIL_PRESENCE_FACEFINDER_ENGAGE_SEC", "2.5"))
    try:
        look = _http_json(
            look_url,
            {
                "facefinder": True,
                "engage_sec": engage,
                "include_jpeg": False,
            },
            timeout=180,
        )
        # hard re-home + facefinder OFF after peek (never leave stuck on a face)
        try:
            _http_json(f"{CAMERA}/v1/return_door", {}, timeout=8)
        except Exception:
            pass
        try:
            _http_json(f"{CAMERA}/v1/facefinder", {"on": False}, timeout=5)
        except Exception:
            pass
        return {
            "ok": bool(look.get("ok") or look.get("door_ok")),
            "mode": look.get("mode") or "look_around_facefinder",
            "n_stops": look.get("n_stops"),
            "n_ok": look.get("n_ok"),
            "engage_sec": engage,
            "door_ok": look.get("door_ok"),
            "note": "gemma facefinder reauth/outage peek",
            "tool": "gemma_peek_look_around",
            "policy": "reauth_or_outage_only",
        }
    except Exception as e:
        try:
            _http_json(f"{CAMERA}/v1/return_door", {}, timeout=8)
            _http_json(f"{CAMERA}/v1/facefinder", {"on": False}, timeout=5)
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:200]}


def run_motion_event(
    *,
    trigger: dict,
    ask_gemma: Callable[[list[Path], str], dict[str, str]],
    pack: dict | None,
    room_memory: Any,
) -> dict[str, Any]:
    """Full event cycle: clip → verdict → optional peek → memory → lesson."""
    t0 = time.time()
    event_id = f"motion_{int(t0 * 1000)}"
    lessons = _read_lessons()
    # Episode: pre-roll + live until FOV still (not fixed 5s)
    since = trigger.get("seq")
    try:
        since_i = int(since) - 1 if since is not None else None
    except (TypeError, ValueError):
        since_i = None
    meta, paths = capture_live_window(
        fps=CAPTURE_FPS, since_seq=since_i
    )
    if not paths:
        return {
            "ok": False,
            "error": "no frames in episode",
            "event_id": event_id,
            "trigger": trigger,
        }

    resolve = None
    try:
        man_path = paths[0].parent / "manifest.json"
        if man_path.is_file():
            resolve = json.loads(man_path.read_text()).get("resolve")
    except Exception:
        resolve = meta[0].get("_episode_resolve") if meta else None

    vl_paths = _even_paths(paths, VL_FRAMES)
    q = build_event_question(
        meta=meta,
        pack=pack,
        room_memory=None,  # do not feed roster — count-only, no memory bias
        lessons=lessons,
        resolve=resolve,
    )
    full_q = _load_brief() + "\n\n---\n\n" + q
    try:
        ans = ask_gemma(vl_paths, full_q)
        raw = ans.get("now") or ans.get("event") or next(iter(ans.values()), "")
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)[:400],
            "event_id": event_id,
            "latency_sec": round(time.time() - t0, 2),
            "n_frames": len(paths),
            "n_vl": len(vl_paths),
        }

    parsed = _parse_event_json(raw)
    verdict = normalize_verdict(parsed)
    # peek only if model flags unreadable (default: stay on door)
    # Facefinder peek only via maybe_peek policy (reauth/outage + opt-in env)
    peek = maybe_peek(verdict)
    if peek and peek.get("ok"):
        verdict["note"] = (str(verdict.get("note") or "") + " | peeked").strip(" |")

    ent = int(verdict.get("entered") or 0)
    left_n = int(verdict.get("left") or 0)
    action = str(verdict.get("action") or "null")

    # Debug board memory only — SoT write is oracle_bench → /v1/vl_delta
    applied = (
        apply_verdict_to_memory(verdict, room_memory)
        if room_memory is not None
        else {
            "headcount": None,
            "people": [],
            "entered": ent,
            "left": left_n,
            "before": None,
            "after": None,
        }
    )
    before_c = applied.get("before")
    # IDs are NOT Gemma's job — leave stamp to CODE/face on SoT
    stamp_notes: list[str] = []

    streaming = {
        "action": action,
        "n": int(verdict.get("n") or 0),
        "entered": ent,
        "left": left_n,
        "need_peek": bool(verdict.get("need_peek")),
        "note": verdict.get("note"),
        "event_id": event_id,
        "ts_local": _iso(t0),
        "source": "gemma",
    }

    lesson = {
        "ts": t0,
        "ts_local": _iso(t0),
        "event_id": event_id,
        "trigger": {
            "kind": trigger.get("kind"),
            "seq": trigger.get("seq"),
            "note": trigger.get("note"),
            "ts": trigger.get("ts"),
        },
        "n_frames": len(paths),
        "n_vl": len(vl_paths),
        "action": action,
        "n": streaming["n"],
        "entered": ent,
        "left": left_n,
        "before_count": before_c,
        "after_count": applied.get("after"),
        "need_peek": bool(verdict.get("need_peek")),
        "peek_ok": (peek or {}).get("ok") if peek else None,
        "id_stamps": stamp_notes,
        "security_role": "none",
        "note": verdict.get("note"),
        "lesson": verdict.get("lesson") or verdict.get("note") or action,
        "latency_sec": round(time.time() - t0, 2),
        "raw_preview": (raw or "")[:300],
        "streaming": streaming,
    }
    _append_lesson(lesson)

    # Pin rolling /data raw next to this event pack (security tape, not VL strip)
    raw_pin: dict[str, Any] | None = None
    try:
        from .security_raw import pin_for_event

        ed = paths[0].parent if paths else None
        if ed is not None:
            t_fr0 = float(meta[0]["ts"]) if meta else t0
            t_fr1 = float(meta[-1]["ts"]) if meta else t0
            # leaves / stranger-ish: wider pad; always pin something
            pad = 180.0 if action == "sub" else 120.0
            raw_pin = pin_for_event(
                ed,
                t0=t_fr0,
                t1=t_fr1,
                pad_sec=pad,
                reason=f"door_event:{action}",
            )
            # Stranger inject / untrusted add with no id → freeze full hour for user
            note_l = str(verdict.get("note") or "").lower()
            if action == "add" and any(
                k in note_l
                for k in ("stranger", "unknown", "inject", "intruder", "unfamiliar")
            ):
                from .security_raw import freeze_incident

                raw_pin["incident"] = freeze_incident(
                    reason="stranger_or_inject",
                    extra={"event_id": event_id, "note": verdict.get("note")},
                )
    except Exception as e:
        raw_pin = {"ok": False, "error": str(e)[:160]}

    return {
        "ok": True,
        "provider": "gemma",
        "mode": "door_watchman_one_job",
        "security_role": "none",
        "id_stamp": "not_gemma",
        "event_id": event_id,
        "ts_local": _iso(t0),
        "latency_sec": lesson["latency_sec"],
        "n_frames": len(paths),
        "n_vl": len(vl_paths),
        "streaming": streaming,
        "verdict": verdict,
        "in_room": {
            "headcount": applied.get("after"),
            "people": [],
            "count_trust": verdict.get("count_trust") or "fact",
            "note": f"action={action} n={streaming['n']}: {verdict.get('note') or ''}".strip(),
        },
        "id_stamps": stamp_notes,
        "peek": peek,
        "raw_security": raw_pin,
        "lesson": lesson,
        "raw_now": raw,
        "trigger": trigger,
        "is_driver": True,
        "label": "GEMMA door watchman — add|sub|null only",
    }


def _parse_event_json(raw: str) -> dict:
    if not raw:
        return {"action": "null", "n": 0, "entered": 0, "left": 0, "need_peek": False, "note": "empty model"}
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            if "{" in p:
                text = p
                break
    try:
        i, j = text.index("{"), text.rindex("}") + 1
        return json.loads(text[i:j])
    except Exception:
        # last-ditch: bare word in output
        low = raw.lower()
        if any(w in low for w in ("\"add\"", " action: add", "entered\": 1", "entered\":1")):
            return {"action": "add", "n": 1, "note": "parse_soft_add"}
        if any(w in low for w in ("\"sub\"", " action: sub", "left\": 1", "left\":1")):
            return {"action": "sub", "n": 1, "note": "parse_soft_sub"}
        return {
            "action": "null",
            "n": 0,
            "entered": 0,
            "left": 0,
            "need_peek": False,
            "count_trust": "unsure",
            "note": "parse_fail→null",
            "lesson": "emit {\"action\":\"add|sub|null\"}",
        }


class MotionEventQueue:
    """Coalesce door motion triggers; process one event at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._q: deque[dict] = deque()
        self._busy = False
        self._last_result: dict | None = None
        self._pending_window_end: float = 0.0

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    def offer_motion(self, trigger: dict) -> None:
        """Stack/coalesce motion events. Only kind=motion (door).

        Next pop starts episode capture: ring pre-roll + live until FOV still.
        """
        kind = str(trigger.get("kind") or "")
        if kind not in ("motion", "firmware_motion", "door_motion"):
            return
        ts = float(trigger.get("ts") or time.time())
        with self._lock:
            # coalesce: if last queued is recent, keep latest stamp (same live window)
            if self._q and (ts - float(self._q[-1].get("ts") or 0)) < COALESCE_GAP_SEC:
                self._q[-1] = trigger
                return
            while len(self._q) >= QUEUE_MAX:
                self._q.popleft()  # drop oldest under flood
            self._q.append(trigger)

    def pop_ready(self) -> dict | None:
        """Next motion event — process ASAP (live capture owns the 5s)."""
        with self._lock:
            if self._busy or not self._q:
                return None
            self._busy = True
            return self._q.popleft()

    def done(self, result: dict) -> None:
        with self._lock:
            self._busy = False
            self._last_result = result


_QUEUE = MotionEventQueue()


def get_queue() -> MotionEventQueue:
    return _QUEUE
