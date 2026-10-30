"""Door capture v2 — event-anchored ≤60-frame packs from the 30fps ring.

Spec: docs/designs/design-door-capture-v2.md (the Wall repo). On a door event,
walk the ring back to one analysis step before motion (onset), follow live
until the room is still again, then keep 12 dense front frames (~12fps from
onset), a ≤36-frame middle thinned to ~4fps, and 12 dense back frames ending
one frame after stillness returns. Hard cap 60 frames per event.

Two artifacts frozen at entry: entry_face.jpg (best FaceID face — the
VoiceOS AUTH input) and entry_body.jpg (settled full-body clothing still for
the exit "who left?" match game, CONTRACT-CLOTHING-LEAVE.md). Artifact names
deliberately do NOT match the f*.jpg frame glob.

Order/orientation guards (the discarded gemma/cosmo corpora lesson): every
manifest frame row carries (seq, ts, t_rel); rows are ordered by seq and
consumers must assert monotonicity, never sort filenames. Packs are stamped
orientation="true_handed" — no hflip anywhere in this path.

Default OFF: RAIL_DOOR_CAPTURE_V2=1 enables (provisional-scoring rule).
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
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .stillness import _energy, _gray_small  # 160x90 gray — shared metric

ENABLED = os.environ.get("RAIL_DOOR_CAPTURE_V2", "0") not in ("0", "false", "no", "")
CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
WORK = Path(
    os.environ.get(
        "RAIL_ORACLE_WORK",
        str(_paths.DATA / "work/oracle_live"),
    )
)
EVENTS_DIR = WORK / "gemma_events"

FRONT_N = int(os.environ.get("RAIL_DOOR_V2_FRONT_N", "12"))
BACK_N = int(os.environ.get("RAIL_DOOR_V2_BACK_N", "12"))
MID_MAX = int(os.environ.get("RAIL_DOOR_V2_MID_MAX", "36"))
TOTAL_MAX = int(os.environ.get("RAIL_DOOR_V2_TOTAL_MAX", "60"))
DENSE_FPS = float(os.environ.get("RAIL_DOOR_V2_DENSE_FPS", "12"))
MID_FPS = float(os.environ.get("RAIL_DOOR_V2_MID_FPS", "4"))
# Boundary judgment cadence — event-clip.py CLIP_ANALYSIS_FPS parity: adjacent
# 30fps deltas are too small for the 3.5 threshold family.
ANALYSIS_FPS = float(os.environ.get("RAIL_DOOR_V2_ANALYSIS_FPS", "5"))
STILL_ENERGY = float(os.environ.get("RAIL_DOOR_V2_STILL_ENERGY", "3.5"))
STILL_STEPS = int(os.environ.get("RAIL_DOOR_V2_STILL_STEPS", "10"))  # ~2s @5fps
ONSET_MARGIN = float(os.environ.get("RAIL_DOOR_V2_ONSET_MARGIN", "1.6"))
REST_MIN = float(os.environ.get("RAIL_DOOR_V2_REST_MIN", "2.5"))
# Occupied rooms may never rest — bound the onset walk-back (first live
# transit 2026-08-04: hc=2 kept energy above thr for the whole 20s ring, the
# fallback anchored the front 19.8s before the trigger).
MAX_PREROLL_SEC = float(os.environ.get("RAIL_DOOR_V2_MAX_PREROLL_SEC", "6"))
# Leave confirms fire ~2.5s AFTER the body left the FOV; the walk-back must
# skip that post-transit quiet gap or the pack shows only an empty doorway
# (ev_1785848993188: another person's genuine exit, crossing frames missed).
SKIP_QUIET_SEC = float(os.environ.get("RAIL_DOOR_V2_SKIP_QUIET_SEC", "5"))
# thin_follow must never thin the dense-front zone (same live transit: the
# front collapsed to 4 frames at 4fps before selection ran).
FRONT_PROTECT_SEC = float(os.environ.get("RAIL_DOOR_V2_FRONT_PROTECT_SEC", "2"))
MAX_FOLLOW_SEC = float(os.environ.get("RAIL_DOOR_V2_MAX_FOLLOW_SEC", "45"))
POLL_SEC = float(os.environ.get("RAIL_DOOR_V2_POLL_SEC", "0.4"))
RING_MAX = int(os.environ.get("RAIL_DOOR_V2_RING_MAX", "600"))
DEDUP_SEC = float(os.environ.get("RAIL_DOOR_V2_DEDUP_SEC", "20"))
FACE_LOW_RES_PX = float(os.environ.get("RAIL_DOOR_V2_FACE_LOW_RES_PX", "60"))
# Optional door region for direction sanity, normalized "x0,y0,x1,y1" in [0,1].
# Unset → check skipped, suspect_reversed stays null (never invent geometry).
ROI_ENV = os.environ.get("RAIL_DOOR_V2_ROI", "").strip()


def _roi() -> tuple[float, float, float, float] | None:
    if not ROI_ENV:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in ROI_ENV.split(","))
        if 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1:
            return (x0, y0, x1, y1)
    except Exception:
        pass
    return None


def _fetch(
    *,
    since_seq: int | None = None,
    max_frames: int = RING_MAX,
    stride: int = 1,
) -> list[dict[str, Any]]:
    """Ring frames with jpeg bytes; stillness.fetch_buffer_jpegs + since_seq."""
    url = (
        f"{CAMERA}/v1/buffer?max={max(1, int(max_frames))}"
        f"&full=1&stride={max(1, int(stride))}"
    )
    if since_seq is not None:
        url += f"&since_seq={int(since_seq)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            body = json.loads(r.read().decode())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for fr in body.get("frames") or []:
        b64 = fr.get("jpeg_b64")
        if not b64:
            continue
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if len(raw) < 500:
            continue
        out.append(
            {
                "seq": fr.get("seq"),
                "ts": float(fr.get("capture_ts") or time.time()),
                "jpeg": raw,
            }
        )
    return out


def analysis_rows(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Energy at ~ANALYSIS_FPS over full-rate frames.

    Each row: {"idx": index into frames, "ts", "energy", "gray"}. Row 0 has
    energy 0.0 (no previous step).
    """
    rows: list[dict[str, Any]] = []
    step = 1.0 / max(0.5, ANALYSIS_FPS)
    prev_g = None
    prev_ts = None
    for i, fr in enumerate(frames):
        ts = float(fr["ts"])
        if prev_ts is not None and ts - prev_ts < step:
            continue
        g = _gray_small(fr["jpeg"])
        if g is None:
            continue
        e = 0.0 if prev_g is None else _energy(prev_g, g)
        rows.append({"idx": i, "ts": ts, "energy": round(e, 3), "gray": g})
        prev_g = g
        prev_ts = ts
    return rows


def extend_rows(
    rows: list[dict[str, Any]], frames: list[dict[str, Any]], start_idx: int
) -> None:
    """Incrementally continue analysis_rows over frames[start_idx:]."""
    step = 1.0 / max(0.5, ANALYSIS_FPS)
    prev_g = rows[-1]["gray"] if rows else None
    prev_ts = rows[-1]["ts"] if rows else None
    for i in range(start_idx, len(frames)):
        fr = frames[i]
        ts = float(fr["ts"])
        if prev_ts is not None and ts - prev_ts < step:
            continue
        g = _gray_small(fr["jpeg"])
        if g is None:
            continue
        e = 0.0 if prev_g is None else _energy(prev_g, g)
        rows.append({"idx": i, "ts": ts, "energy": round(e, 3), "gray": g})
        prev_g = g
        prev_ts = ts


def onset_threshold(rows: list[dict[str, Any]]) -> float:
    """Rest baseline × margin. Baseline = median of the quietest half so the
    motion burst itself cannot inflate the estimate."""
    if not rows:
        return max(REST_MIN, STILL_ENERGY)
    es = sorted(r["energy"] for r in rows[1:]) or [0.0]
    quiet_half = es[: max(1, len(es) // 2)]
    base = float(np.median(quiet_half))
    return max(REST_MIN, base * ONSET_MARGIN)


def find_onset(rows: list[dict[str, Any]], trigger_ts: float) -> int:
    """frames-index of the last resting analysis step before the last motion
    run at/just before trigger_ts — 'one frame before motion'.

    Quiet steps within SKIP_QUIET_SEC of the trigger are skipped first: leave
    confirms fire seconds after the body left the FOV, and anchoring on that
    post-transit quiet would clip only an empty doorway. Falls back to the
    quiet step nearest the trigger (then the earliest frame) when no motion
    exists in the window."""
    if not rows:
        return 0
    thr = onset_threshold(rows)
    # row at or just before the trigger
    t_i = 0
    for j, r in enumerate(rows):
        if r["ts"] <= trigger_ts:
            t_i = j
        else:
            break
    # phase 1: skip the post-transit quiet gap (bounded)
    j = t_i
    while (
        j >= 0
        and rows[j]["energy"] <= thr
        and trigger_ts - rows[j]["ts"] <= SKIP_QUIET_SEC
    ):
        j -= 1
    if j < 0 or rows[j]["energy"] <= thr:
        for k in range(t_i, -1, -1):
            if rows[k]["energy"] <= thr:
                return rows[k]["idx"]
        return rows[0]["idx"]
    # phase 2: walk back through the motion run to the rest before it
    while j >= 0 and rows[j]["energy"] > thr:
        j -= 1
    return rows[j]["idx"] if j >= 0 else rows[0]["idx"]


def clamp_onset(
    frames: list[dict[str, Any]], onset_idx: int, trigger_ts: float
) -> int:
    """Bound the walk-back to MAX_PREROLL_SEC before the trigger."""
    min_ts = trigger_ts - MAX_PREROLL_SEC
    if float(frames[onset_idx]["ts"]) >= min_ts:
        return onset_idx
    for i in range(onset_idx, len(frames)):
        if float(frames[i]["ts"]) >= min_ts:
            return i
    return onset_idx


def find_still_end(
    rows: list[dict[str, Any]], after_ts: float
) -> int | None:
    """Row index of the first analysis step where STILL_STEPS consecutive
    steps (ending here) are all ≤ STILL_ENERGY, considering only steps after
    after_ts. None while motion continues."""
    run = 0
    for j, r in enumerate(rows):
        if r["ts"] <= after_ts:
            run = 0
            continue
        if r["energy"] <= STILL_ENERGY:
            run += 1
            if run >= STILL_STEPS:
                return j - STILL_STEPS + 1  # first still step of the run
        else:
            run = 0
    return None


def _nearest_idx(frames: list[dict[str, Any]], target_ts: float) -> int:
    lo, best, best_d = 0, 0, float("inf")
    for i, fr in enumerate(frames):
        d = abs(float(fr["ts"]) - target_ts)
        if d < best_d:
            best, best_d = i, d
    return best


def select_frames(
    frames: list[dict[str, Any]], front_start: int, end_anchor: int
) -> list[tuple[int, str]]:
    """(frames-index, segment) rows: FRONT_N @ DENSE_FPS from front_start,
    BACK_N @ DENSE_FPS ending at end_anchor, middle ≤MID_MAX @ ≤MID_FPS.
    Timestamp-nearest picks (index stride drifts: ring fps / dense fps is not
    an integer). Deduped, seq-ordered, hard-capped at TOTAL_MAX."""
    if not frames:
        return []
    end_anchor = min(max(end_anchor, front_start), len(frames) - 1)
    t0 = float(frames[front_start]["ts"])
    t_end = float(frames[end_anchor]["ts"])

    picked: dict[int, str] = {}
    for k in range(FRONT_N):
        i = _nearest_idx(frames, t0 + k / DENSE_FPS)
        picked.setdefault(i, "front")
    for k in range(BACK_N):
        i = _nearest_idx(frames, t_end - (BACK_N - 1 - k) / DENSE_FPS)
        picked.setdefault(i, "back")

    front_last_ts = t0 + (FRONT_N - 1) / DENSE_FPS
    back_first_ts = t_end - (BACK_N - 1) / DENSE_FPS
    if back_first_ts > front_last_ts:
        n_mid = int((back_first_ts - front_last_ts) * MID_FPS)
        targets = [
            front_last_ts + (j + 1) * (back_first_ts - front_last_ts) / (n_mid + 1)
            for j in range(min(n_mid, MID_MAX))
        ]
        for t in targets:
            i = _nearest_idx(frames, t)
            picked.setdefault(i, "mid")

    order = sorted(picked.items(), key=lambda kv: frames[kv[0]]["seq"] or 0)
    while len(order) > TOTAL_MAX:
        mids = [n for n, (_, seg) in enumerate(order) if seg == "mid"]
        drop = mids[len(mids) // 2] if mids else len(order) // 2
        order.pop(drop)
    return order


def thin_follow(
    frames: list[dict[str, Any]],
    *,
    keep_tail_sec: float = 3.0,
    protect_until_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Bound RAM during a long follow: keep the tail at full rate (back-dense
    source) and thin older follow frames to ~MID_FPS. Frames at or before
    protect_until_ts (the dense-front zone) are never thinned. Precedent:
    gemma_event_loop MAX_KEPT_FRAMES thinning."""
    if not frames:
        return frames
    cut_ts = float(frames[-1]["ts"]) - keep_tail_sec
    out: list[dict[str, Any]] = []
    last_kept: float | None = None
    for fr in frames:
        ts = float(fr["ts"])
        if ts >= cut_ts or (protect_until_ts is not None and ts <= protect_until_ts):
            out.append(fr)
        elif last_kept is None or ts - last_kept >= 1.0 / MID_FPS:
            out.append(fr)
            last_kept = ts
    return out


def direction_check(
    rows: list[dict[str, Any]], kind: str
) -> bool | None:
    """Door-region energy should lead interior energy on enter (trail on
    leave). True = suspect_reversed. None when no ROI configured or the
    signal is inconclusive."""
    roi = _roi()
    if roi is None or len(rows) < 3:
        return None
    x0, y0, x1, y1 = roi
    h, w = rows[0]["gray"].shape[:2]  # 90, 160
    dx0, dx1 = int(x0 * w), max(int(x0 * w) + 1, int(x1 * w))
    dy0, dy1 = int(y0 * h), max(int(y0 * h) + 1, int(y1 * h))

    def split_energy(g0: np.ndarray, g1: np.ndarray) -> tuple[float, float]:
        d = np.abs(g0.astype(np.int16) - g1.astype(np.int16))
        door = float(d[dy0:dy1, dx0:dx1].mean())
        mask = np.ones_like(d, dtype=bool)
        mask[dy0:dy1, dx0:dx1] = False
        interior = float(d[mask].mean()) if mask.any() else 0.0
        return door, interior

    thr = onset_threshold(rows)
    door_first: float | None = None
    interior_first: float | None = None
    for a, b in zip(rows, rows[1:]):
        de, ie = split_energy(a["gray"], b["gray"])
        if door_first is None and de > thr:
            door_first = b["ts"]
        if interior_first is None and ie > thr:
            interior_first = b["ts"]
        if door_first is not None and interior_first is not None:
            break
    if door_first is None or interior_first is None:
        return None
    door_leads = door_first <= interior_first
    return (not door_leads) if kind == "enter" else door_leads


def body_signature(
    frames: list[dict[str, Any]], sel_idxs: list[int]
) -> dict[str, Any] | None:
    """Exposure-normalized clothing/hair signature over the pack's body
    frames (who-left shadow matcher, CONTRACT-CLOTHING-LEAVE tier-1).
    Probe-validated 2026-08-04: raw brightness is useless under hallway-light
    swings; background-relative V + torso HS histogram ranked the labeled
    walk-out correctly. Median over many frames — single stills are fragile."""
    try:
        import cv2

        def gray_small(j):
            img = cv2.imdecode(np.frombuffer(j, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return None, None
            s = cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA)
            return s, cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype(np.int16)

        refs = []
        for ri in (sel_idxs[0], sel_idxs[-1]):
            _, g = gray_small(frames[ri]["jpeg"])
            if g is not None:
                refs.append(g)
        if not refs:
            return None
        feats = []
        for i in sel_idxs:
            img, g = gray_small(frames[i]["jpeg"])
            if img is None:
                continue
            for gr in refs:
                mask = (np.abs(g - gr) > 28).astype(np.uint8)
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                if not (0.02 <= mask.mean() <= 0.8):
                    continue
                n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
                if n < 2:
                    continue
                b = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                x, y, w, h, _a = stats[b]
                if h < 40:
                    continue
                blob = labels == b
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                torso = np.zeros_like(blob)
                torso[y + int(0.25 * h): y + int(0.70 * h), x: x + w] = True
                torso &= blob
                if torso.sum() < 100:
                    continue
                hair = np.zeros_like(blob)
                hair[y: y + max(2, int(0.12 * h)), x: x + w] = True
                hair &= blob
                V = hsv[..., 2].astype(np.float32)
                bg = float(V[~blob].mean()) + 1e-6
                hs = cv2.calcHist(
                    [hsv], [0, 1], torso.astype(np.uint8), [12, 3], [0, 180, 0, 256]
                )
                hs = (hs / (hs.sum() + 1e-9)).flatten()
                feats.append((
                    hs,
                    float(V[torso].mean() / bg),
                    float(V[hair].mean() / bg) if hair.sum() > 30 else float(V[torso].mean() / bg),
                ))
                break
        if len(feats) < 2:
            return None
        hist = np.median(np.stack([f[0] for f in feats]), axis=0)
        hist = hist / (hist.sum() + 1e-9)
        return {
            "v": 1,
            "hist": [round(float(x), 5) for x in hist],
            "torso_v": round(float(np.median([f[1] for f in feats])), 4),
            "hair_v": round(float(np.median([f[2] for f in feats])), 4),
            "n_body_frames": len(feats),
        }
    except Exception:
        return None


def sig_dist(a: dict[str, Any], b: dict[str, Any]) -> float:
    ha, hb = np.asarray(a["hist"], dtype=np.float32), np.asarray(b["hist"], dtype=np.float32)
    inter = float(np.minimum(ha, hb).sum())
    return round(
        0.6 * (1 - inter)
        + 0.25 * abs(float(a["torso_v"]) - float(b["torso_v"]))
        + 0.15 * abs(float(a["hair_v"]) - float(b["hair_v"])),
        4,
    )


def _jpeg_is_door_view(jpeg: bytes) -> bool | None:
    """True=door FOV, False=not a door frame, None=cannot tell.

    Undecodable bytes are FALSE, not None. Callers keep anything that is "not
    False", so returning None for a blob that is not an image at all let a
    placeholder \xff\xd8 through as an entry still — which is what
    test_door_enter_does_not_store_non_door_jpeg was written to catch.

    None is reserved for its real meaning: a decodable frame that cannot be
    judged because no door reference exists yet. That case must stay permissive,
    or a house with no reference captured could never store any evidence.
    """
    try:
        import cv2

        from .entry_face import is_door_view

        img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return False  # not an image, so certainly not the door
        return is_door_view(img)
    except Exception:
        return None


def filter_selection_door_view(
    frames: list[dict[str, Any]],
    selection: list[tuple[int, str]],
) -> tuple[list[tuple[int, str]], dict[str, Any]]:
    """Tag door-view. Do not drop.

    is_door_view is empty-door pixel change. A person standing IN the
    doorway is the largest change there is (ev_1786777424191 f300: 139px
    face, changed=0.715, vetoed). Chair zooms stay in the pack; they lose
    to a true door face in best_face / best_face_in_pack.
    """
    kept: list[tuple[int, str]] = []
    demoted = 0
    unknown = 0
    for i, seg in selection:
        if i < 0 or i >= len(frames):
            continue
        jpeg = frames[i].get("jpeg") or b""
        if not jpeg:
            kept.append((i, seg))
            unknown += 1
            continue
        dv = _jpeg_is_door_view(jpeg)
        if dv is False:
            demoted += 1
        elif dv is None:
            unknown += 1
        kept.append((i, seg))
    meta = {
        "door_view_filter": True,
        "door_view_policy": "demote",
        "selected_in": len(selection),
        "kept": len(kept),
        "dropped_non_door": 0,
        "demoted_non_door": demoted,
        "unknown_view": unknown,
    }
    return kept, meta


def best_face(
    frames: list[dict[str, Any]], idxs: list[int]
) -> dict[str, Any] | None:
    """Best FaceID face: det_score × min(1, h/112).

    Prefer a true door-view frame; fall back to a demoted frame (person in
    the doorway flips is_door_view to False). Do not skip those.
    """
    try:
        import cv2

        from .face_engine import get_face_engine

        eng = get_face_engine()
    except Exception:
        return None
    best_door: dict[str, Any] | None = None
    best_unknown: dict[str, Any] | None = None
    for i in idxs:
        try:
            jpeg = frames[i]["jpeg"]
            dv = _jpeg_is_door_view(jpeg)
            img = cv2.imdecode(
                np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
            )
            if img is None:
                continue
            for face in eng.detect(img):
                det = eng.det_score(face)
                bb = eng.bbox(face)
                if not bb:
                    continue
                face_h = float(bb[3] - bb[1])
                face_w = float(bb[2] - bb[0])
                if min(face_h, face_w) < FACE_LOW_RES_PX:
                    continue
                score = det * min(1.0, face_h / 112.0)
                cand = {
                    "idx": i,
                    "seq": frames[i]["seq"],
                    "score": round(score, 4),
                    "det_score": round(det, 4),
                    "bbox": [int(v) for v in bb],
                    "face_px": round(face_h, 1),
                    "face_low_res": face_h < FACE_LOW_RES_PX,
                    "door_view": dv,
                }
                bucket = best_door if dv is True else best_unknown
                if bucket is None or score > bucket["score"]:
                    if dv is True:
                        best_door = cand
                    else:
                        best_unknown = cand
        except Exception:
            continue
    return best_door or best_unknown


def capture_kind_for_event(event: dict[str, Any] | None) -> str | None:
    """Which pack kind to start, or None to skip.

    enter/leave stay classified packs. pending_crossing is motion-only:
    Argus may look; SoT stays nameless {null,+1,−1}.
    """
    if not isinstance(event, dict):
        return None
    k = str(event.get("kind") or "")
    if k == "crossing_cancelled":
        return None
    if k == "leave" and (event.get("leave_match") or {}).get("method") == "ghost_recovery":
        return None
    if k in ("enter", "leave"):
        return k
    if k == "pending_crossing":
        return "motion"
    # AUTH User already on roster: observe_door emits resight, not enter.
    # Still a door-plane event — open a motion pack so Oracle/Argus/FaceID
    # attach. Count stays explained_by_roster (no second +1).
    if k == "transit_dedup_noop" and str(event.get("source") or "") == "door_live_resight":
        return "motion"
    return None


def write_pack(
    *,
    kind: str,
    frames: list[dict[str, Any]],
    selection: list[tuple[int, str]],
    trigger_seq: int | None,
    trigger_ts: float,
    resolve: str,
    suspect_reversed: bool | None,
    face_meta: dict[str, Any] | None,
    body_idx: int | None,
    slot_key: str | None,
    wall_sec: float,
    energies: dict[int, float] | None = None,
    trait_sig: dict[str, Any] | None = None,
    events_dir: Path | None = None,
    door_view_meta: dict[str, Any] | None = None,
    filter_door_view: bool | None = None,
) -> dict[str, Any] | None:
    """Write ev_<start_ms>/ pack. Returns manifest dict, or None if a pack
    already covers this start (ev_<ms> naming is load-bearing: the Wall repo
    event-clip dedup parses it).

    Enter/leave packs default to door-FOV-only frames (filter_door_view).
    """
    if not selection:
        return None
    # Crossing packs: strip facefinder seat frames from the ring before write.
    dv_meta = door_view_meta
    if filter_door_view is None:
        filter_door_view = str(kind) in ("enter", "leave", "exit", "motion")
    if filter_door_view:
        selection, auto_meta = filter_selection_door_view(frames, selection)
        dv_meta = {**(dv_meta or {}), **auto_meta}
        if not selection:
            return None  # pure facefinder pollution — no door evidence
    ed_root = events_dir if events_dir is not None else EVENTS_DIR
    start_ms = int(float(frames[selection[0][0]]["ts"]) * 1000)
    ed = ed_root / f"ev_{start_ms}"
    if ed.exists():
        return None
    ed.mkdir(parents=True)

    rows: list[dict[str, Any]] = []
    last_seq = -1
    energies = energies or {}
    for n, (i, seg) in enumerate(selection):
        fr = frames[i]
        seq = int(fr["seq"] or 0)
        if seq <= last_seq:
            continue  # monotonic-by-construction; never trust later sorting
        last_seq = seq
        name = f"f{n:03d}_seq{seq:09d}.jpg"
        p = ed / name
        p.write_bytes(fr["jpeg"])
        rows.append(
            {
                "i": n,
                "seq": seq,
                "ts": round(float(fr["ts"]), 3),
                "t_rel": round(float(fr["ts"]) - trigger_ts, 3),
                "file": name,
                "path": str(p),
                "energy": energies.get(i),
                "seg": seg,
            }
        )

    artifacts: dict[str, Any] = {}

    def _ok_artifact(idx: int) -> bool:
        # Demote, do not drop: a person in the doorway fails is_door_view.
        return True

    kind_l = str(kind or "").strip().lower()
    if (
        face_meta is not None
        and 0 <= int(face_meta.get("idx", -1)) < len(frames)
        and kind_l not in ("leave", "exit")
    ):
        if _ok_artifact(int(face_meta["idx"])):
            (ed / "entry_face.jpg").write_bytes(frames[face_meta["idx"]]["jpeg"])
            artifacts["entry_face"] = {
                k: v for k, v in face_meta.items() if k not in ("idx",)
            } | {"file": "entry_face.jpg"}
    if body_idx is not None and 0 <= body_idx < len(frames):
        if _ok_artifact(body_idx):
            body_file = (
                "leave_body.jpg" if kind_l in ("leave", "exit") else "entry_body.jpg"
            )
            body_key = "leave_body" if kind_l in ("leave", "exit") else "entry_body"
            (ed / body_file).write_bytes(frames[body_idx]["jpeg"])
            artifacts[body_key] = {
                "file": body_file,
                "seq": frames[body_idx]["seq"],
            }

    try:
        from .oracle_event import (
            event_id_from_ts,
            get_event,
            open_event,
            rekey_event,
            set_onset,
        )

        trig_id = event_id_from_ts(trigger_ts)
        if get_event(trig_id) is None and get_event(ed.name) is None:
            open_event(trigger_ts, pack_id=trig_id)
        if trig_id != ed.name:
            rekey_event(trig_id, ed.name)
        else:
            open_event(trigger_ts, pack_id=ed.name)
        # The row was opened on the door tick, before anyone knew where motion
        # actually started. Now we do: rows[0] is frames[selection[0][0]], the
        # frame find_onset walked back to, and the one this pack is named after.
        if rows:
            set_onset(ed.name, seq=rows[0]["seq"], ts=rows[0]["ts"])
    except Exception:
        pass
    man = {
        "id": ed.name,
        "pack_dir": str(ed),
        "kind": kind,
        "slot_key": slot_key or None,
        "trigger_seq": trigger_seq,
        "trigger_ts": round(trigger_ts, 3),
        # First frame of motion, named. rows[0] IS frames[front_start] — the
        # step find_onset walked back to — so this costs nothing to record and
        # was previously thrown away.
        #
        # Why a SEQ and not a timestamp: the pack is named ev_<first-frame-ms>
        # while trigger_ts is the door tick, and those differ by up to the
        # pre-roll (measured 6.2 s on 2026-08-15). Two plausible "event times"
        # is how a visit lands on the wrong pack. A camera seq is a monotonic
        # counter — one value, no rounding, no second candidate.
        #
        # This is the index key: Oracle keys the visit row on it, Argus starts
        # its clip from it, and hardware FF's faceshot joins on it.
        "onset_seq": rows[0]["seq"] if rows else None,
        "onset_ts": rows[0]["ts"] if rows else None,
        "pre_roll_sec": round(max(0.0, trigger_ts - rows[0]["ts"]), 3) if rows else 0.0,
        "fps": round(len(rows) / max(0.1, rows[-1]["ts"] - rows[0]["ts"]), 2)
        if len(rows) > 1
        else 0.0,
        "capture_mode": "door_v2",
        "orientation": "true_handed",
        "resolve": resolve,
        "still_energy": STILL_ENERGY,
        "still_frames": STILL_STEPS,
        "suspect_reversed": suspect_reversed,
        "trait_sig": trait_sig,
        "max_energy": max((r["energy"] or 0.0) for r in rows) if rows else 0.0,
        "n": len(rows),
        "wall_sec": round(wall_sec, 3),
        "frames": rows,
        "artifacts": artifacts,
        "door_view": dv_meta,
        "spec": "design-door-capture-v2: onset-1 + 12dense/4fps-mid/12dense <=60",
    }
    payload = json.dumps(man, indent=2)
    (ed / "manifest.json").write_text(payload)
    (ed / f"{ed.name}.json").write_text(payload)
    return man


class DoorCaptureV2:
    """Single-flight per-event capture. start_event() returns immediately;
    the follow/assemble work runs on a daemon thread and must never block the
    0.5s door tick."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._last_start_ts = 0.0

    def start_event(
        self,
        *,
        kind: str,
        trigger_seq: int | None,
        trigger_ts: float,
        slot_key: str | None = None,
        on_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        with self._lock:
            if self._busy:
                return False  # one camera, one door: coalesce
            if trigger_ts - self._last_start_ts < DEDUP_SEC:
                return False
            self._busy = True
            self._last_start_ts = trigger_ts
        # Row must exist before FaceID / Argus fire. event_id = trigger ts.
        try:
            from .oracle_event import open_event

            open_event(trigger_ts)
        except Exception:
            pass
        t = threading.Thread(
            target=self._run,
            kwargs=dict(
                kind=kind,
                trigger_seq=trigger_seq,
                trigger_ts=trigger_ts,
                slot_key=slot_key,
                on_done=on_done,
            ),
            daemon=True,
            name=f"door-capture-v2-{kind}",
        )
        t.start()
        return True

    def _run(
        self,
        *,
        kind: str,
        trigger_seq: int | None,
        trigger_ts: float,
        slot_key: str | None,
        on_done: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        t_start = time.time()
        try:
            frames = _fetch(max_frames=RING_MAX, stride=1)
            if not frames:
                return
            rows = analysis_rows(frames)
            front_start = clamp_onset(frames, find_onset(rows, trigger_ts), trigger_ts)
            if front_start > 0:
                front_start -= 1  # one frame before motion, full-rate
            protect_ts = float(frames[front_start]["ts"]) + FRONT_PROTECT_SEC

            resolve = "timeout"
            end_anchor = len(frames) - 1
            last_moving_idx: int | None = None
            while time.time() - t_start < MAX_FOLLOW_SEC:
                still_row = find_still_end(rows, trigger_ts)
                if still_row is not None:
                    # one frame after stillness returns
                    end_anchor = min(rows[still_row]["idx"] + 1, len(frames) - 1)
                    if still_row > 0:
                        last_moving_idx = rows[still_row - 1]["idx"]
                    resolve = "still"
                    break
                time.sleep(POLL_SEC)
                prev_n = len(frames)
                new = _fetch(since_seq=frames[-1]["seq"], max_frames=RING_MAX)
                if new:
                    frames.extend(new)
                    extend_rows(rows, frames, prev_n)
                    frames_thinned = thin_follow(frames, protect_until_ts=protect_ts)
                    if len(frames_thinned) != len(frames):
                        kept = {id(fr) for fr in frames_thinned}
                        remap = [
                            n for n, fr in enumerate(frames) if id(fr) in kept
                        ]
                        idx_map = {old: new_i for new_i, old in enumerate(remap)}
                        frames = frames_thinned
                        rows = [
                            {**r, "idx": idx_map[r["idx"]]}
                            for r in rows
                            if r["idx"] in idx_map
                        ]
                        front_start = idx_map.get(front_start, 0)
            if resolve == "timeout":
                end_anchor = len(frames) - 1

            selection = select_frames(frames, front_start, end_anchor)
            if not selection:
                return
            suspect = direction_check(rows, kind)
            face_meta = None
            body_idx = None
            if kind in ("enter", "motion"):
                # whole selection, not just the front: the best face is often
                # mid-crossing (first live transit had zero front faces).
                # motion packs get the same artifacts so fusion has a crop
                # even when geometry could not assign enter/leave.
                face_meta = best_face(frames, [i for i, _ in selection])
                # clothing still = LAST MOVING frame, not the settled one: the
                # camera watches the door, so once the FOV is still the body
                # has usually walked out of view (first live entry_body caught
                # a half-occluded torso in the doorway)
                body_idx = last_moving_idx if last_moving_idx is not None else end_anchor
            elif kind in ("leave", "exit"):
                # FaceID has no back class. Leave crop is appearance, not a face.
                body_idx = last_moving_idx if last_moving_idx is not None else end_anchor
            man = write_pack(
                kind=kind,
                frames=frames,
                selection=selection,
                trigger_seq=trigger_seq,
                trigger_ts=trigger_ts,
                resolve=resolve,
                suspect_reversed=suspect,
                face_meta=face_meta,
                body_idx=body_idx,
                slot_key=slot_key,
                wall_sec=time.time() - t_start,
                energies={r["idx"]: r["energy"] for r in rows},
                trait_sig=body_signature(frames, [i for i, _ in selection]),
            )
            if man is not None and on_done is not None:
                on_done(man)
            # Standing order path: pack → Argus describe / leave (async, never block)
            if man is not None:
                try:
                    from .argus_notify import notify_pack_async

                    notify_pack_async(man)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            with self._lock:
                self._busy = False


_capture: DoorCaptureV2 | None = None
_capture_lock = threading.Lock()


def get_capture() -> DoorCaptureV2:
    global _capture
    with _capture_lock:
        if _capture is None:
            _capture = DoorCaptureV2()
        return _capture
