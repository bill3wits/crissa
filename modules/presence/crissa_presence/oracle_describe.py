"""Describe a crossing from the TAPE, as a sequence, and say who moved.

This is the piece that finally worked on 2026-08-09. Everything before it
tried to infer motion from a single still — blob tracking, area weighting,
crossing windows, mover differencing — and every one of them broke on the case
the User produced deliberately: he walked out past a guest while she stood in the
doorway. A single frame cannot encode motion, so the largest body wins and the
description is of the person standing still.

Given the same window as a SEQUENCE, the 35B separated three people, marked
two stationary, named the man crossing, and got the direction right, with no
geometry from us at all.

Two things make it work, and both are non-negotiable:

  * TAPE, not pack. The pack for that exit held 20 frames and did not contain
    the crossing — the model was correct to say nobody crossed. The tape gave
    577 frames at 14.45fps with zero gaps over the same window.
  * TIME ORDER. Frames are sent oldest-first; that ordering IS the motion cue.

Model choice: ops (35B) is already resident and answers in under a second.
Qwen3-VL-4B matches it on people, sex, hair and garment and picks the same
crosser, but inverts direction and cannot fit in VRAM alongside the two vLLM
engines (14 MiB free, measured) — so it runs on CPU as a cross-check, not in
the live path.
"""
from __future__ import annotations

import base64
import json
import re
import os
import urllib.request
from typing import Any

from .occupancy_llm import chat as occupancy_chat
N_FRAMES = int(os.environ.get("RAIL_ORACLE_SEQ_FRAMES", "10"))
# the User 2026-08-10: pick slides from a SHORT window around the pending_crossing
# trigger. The old PRE=25/POST=15 let energy peak land on desk motion / door
# swing / wall portrait noise 15s away from the real transit.
PRE_SEC = float(os.environ.get("RAIL_ORACLE_PRE_SEC", "6"))
POST_SEC = float(os.environ.get("RAIL_ORACLE_POST_SEC", "4"))
# Energy refine stays INSIDE this core; never roam outside it.
CORE_PRE_SEC = float(os.environ.get("RAIL_ORACLE_CORE_PRE_SEC", "4"))
CORE_POST_SEC = float(os.environ.get("RAIL_ORACLE_CORE_POST_SEC", "3"))
# Door ROI (norm x0,y0,x1,y1) — energy only cares about doorway, not whole FOV.
# Default: right half (door opening). Override with RAIL_ORACLE_DOOR_ROI.
_DOOR_ROI_RAW = os.environ.get("RAIL_ORACLE_DOOR_ROI", "0.45,0.0,1.0,1.0")


def _door_roi() -> tuple[float, float, float, float]:
    try:
        parts = [float(x) for x in _DOOR_ROI_RAW.split(",")]
        if len(parts) == 4:
            return parts[0], parts[1], parts[2], parts[3]
    except ValueError:
        pass
    return 0.45, 0.0, 1.0, 1.0


PROMPT = (
    "Consecutive doorway-camera frames in TIME ORDER (oldest → newest). "
    "These frames are a SHORT burst around a door-crossing trigger.\n"
    "Geometry: ROOM is LEFT of frame; DOOR OPENING is RIGHT.\n"
    "IGNORE: wall photos, family portraits, TV/screens, reflections, pets, "
    "furniture-only motion. Only list LIVE people whose body is in 3D space.\n"
    "People standing still are NOT crossing. Crossing = body moves through "
    "the door opening across the sequence.\n"
    "List every LIVE person, numbered:\n"
    "For each: SEX: M|F | HAIR: <colour>, <length> | TOP: <colour> <garment> | "
    "MOTION: crossing-left|crossing-right|standing-still\n"
    "Then a final line EXACTLY:\n"
    "WHO CROSSED: <number or NONE> and DIRECTION: ENTERING|LEAVING|NONE\n"
    "Direction: body moving right→left = ENTERING the room; "
    "left→right = LEAVING the room. If no live body crosses, WHO CROSSED: NONE "
    "and DIRECTION: NONE."
)


VOTES = int(os.environ.get("RAIL_ORACLE_VOTES", "3"))


def describe_crossing(trigger_ts: float, *, model: str | None = None,
                      votes: int = 0) -> dict[str, Any]:
    """Read the tape around a trigger and return the crossing verdict.

    Voted, because the verdict is not reproducible from a single call. The
    same clip scored 5/6 on one pass and 3/5 on the next with identical code
    and temperature 0 — vLLM's continuous batching makes even greedy decoding
    non-bitwise-deterministic. A direction that flips between runs is worse
    than one that is wrong consistently, because nothing downstream can be
    trusted or debugged. Best of N, and the agreement level is reported so a
    split decision is visible instead of silently picked.
    """
    n = votes or VOTES
    model = model or occupancy_chat()[1]
    if n > 1:
        seen: list[dict[str, Any]] = []
        for _ in range(n):
            r = _describe_once(trigger_ts, model=model)
            if r.get("ok"):
                seen.append(r)
        if not seen:
            return _describe_once(trigger_ts, model=model)
        tally: dict[Any, int] = {}
        for r in seen:
            tally[r.get("direction")] = tally.get(r.get("direction"), 0) + 1
        best = max(tally.items(), key=lambda kv: kv[1])
        winner = next(r for r in seen if r.get("direction") == best[0])
        # Take the people list from whichever RUN described them best, not
        # from whichever run happened to win the direction vote. The model's
        # output format varies run to run: one call returns clean pipe-
        # delimited person records and the next narrates in prose, so the
        # winner frequently carried a correct direction and an empty roster —
        # which is the half Inspector actually needs for clothing matching.
        richest = max(seen, key=lambda r: len(r.get("people") or []))
        people = richest.get("people") or []
        if not people:
            # Direction without a roster is half an answer: Inspector matches
            # on the person strings, so a verdict with no descriptions cannot
            # retire anyone. The model drops the roster on most runs when it
            # is asked for both in one shot, so ask for the roster ALONE.
            people = _roster_only(trigger_ts, model=model)
        return winner | {"votes": f"{best[1]}/{len(seen)}",
                         "vote_split": {str(k): v for k, v in tally.items()},
                         "unanimous": best[1] == len(seen),
                         "people": people,
                         "people_from_vote": seen.index(richest) + 1}
    return _describe_once(trigger_ts, model=model)


def _describe_once(trigger_ts: float, *, model: str | None = None) -> dict[str, Any]:
    from . import tape

    pick = select_crossing_frames(trigger_ts, n=N_FRAMES)
    if not pick.get("ok"):
        return {
            "ok": False,
            "reason": pick.get("reason") or "no frames",
            "pick": pick,
            "coverage": pick.get("coverage"),
        }
    sel = pick["frames"]
    cov = pick.get("coverage") or tape.coverage(
        trigger_ts - PRE_SEC, trigger_ts + POST_SEC
    )

    content: list[dict] = [{"type": "text", "text": PROMPT}]
    for r in sel:
        raw = tape.read(r["path"])
        if raw is None:
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,"
                                 + base64.b64encode(raw).decode()}})

    url, live = occupancy_chat()
    model = model or live
    body = {
        "model": model, "max_tokens": 500, "temperature": 0.0,
        # ops is a reasoning model: without this it spends the whole budget in
        # `reasoning` and returns content=None.
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=300).read())
        msg = r["choices"][0]["message"]
        txt = (msg.get("content") or msg.get("reasoning") or "").strip()
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:120]}",
                "pick": pick}

    return {
        "ok": True,
        "text": txt,
        "frames": len(sel),
        "span_sec": round(sel[-1]["ts"] - sel[0]["ts"], 1) if sel else 0,
        "coverage": cov,
        "pick": {
            k: pick.get(k)
            for k in (
                "method",
                "trigger_ts",
                "window_pre",
                "window_post",
                "core_pre",
                "core_post",
                "offsets_sec",
                "door_roi",
                "energy_peak_offset",
            )
        },
    } | parse_verdict(txt)


ROSTER_PROMPT = (
    "Consecutive doorway-camera frames in time order. List EVERY person "
    "visible, one per line, numbered, and NOTHING else. Use exactly:\n"
    "1. SEX: M|F | HAIR: <colour>, <length> | TOP: <colour> <garment> | "
    "MOTION: crossing-left|crossing-right|standing-still\n"
    "If nobody is visible, write: NONE"
)


def _roster_only(trigger_ts: float, *, model: str | None = None) -> list[str]:
    """Second call, roster only — no direction question to crowd it out."""
    from . import tape

    pick = select_crossing_frames(trigger_ts, n=N_FRAMES)
    if not pick.get("ok"):
        return []
    content: list[dict] = [{"type": "text", "text": ROSTER_PROMPT}]
    for r in pick["frames"]:
        raw = tape.read(r["path"])
        if raw is not None:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(raw).decode()}})
    url, live = occupancy_chat()
    model = model or live
    body = {"model": model, "max_tokens": 400, "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": content}]}
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}), timeout=300).read())
        msg = r["choices"][0]["message"]
        txt = (msg.get("content") or msg.get("reasoning") or "")
    except Exception:
        return []
    return parse_verdict(txt).get("people") or []


def select_crossing_frames(
    trigger_ts: float, *, n: int | None = None
) -> dict[str, Any]:
    """WHERE + WHEN to pick oracle slides — law (the User 2026-08-10):

    1. **WHEN** = short tape window around the pending_crossing trigger_ts
       (default −6s…+4s). Not a 40s wander.
    2. **Energy refine** only inside a tighter CORE (−4s…+3s) so desk motion
       outside the door line cannot steal the peak.
    3. **WHERE (pixels)** = door ROI (right side by default) for energy only;
       full frame is still sent to the model so clothing/face stay readable.
    4. Fallback = even time samples centered on the trigger (never empty).
    """
    from . import tape

    n = int(n or N_FRAMES)
    pre, post = float(PRE_SEC), float(POST_SEC)
    cpre, cpost = float(CORE_PRE_SEC), float(CORE_POST_SEC)
    roi = _door_roi()
    cov = tape.coverage(trigger_ts - pre, trigger_ts + post)
    rows = tape.frames_around(trigger_ts, before_sec=pre, after_sec=post)
    if len(rows) < 4:
        return {
            "ok": False,
            "reason": f"only {len(rows)} tape frames in window",
            "coverage": cov,
            "trigger_ts": trigger_ts,
            "window_pre": pre,
            "window_post": post,
        }

    # Prefer rows inside the CORE for motion peak; keep outer for fallback fill.
    core = [
        r
        for r in rows
        if (trigger_ts - cpre) <= float(r["ts"]) <= (trigger_ts + cpost)
    ]
    if len(core) < 4:
        core = rows

    sel, method, peak_off = _sample_motion_peak(
        core, n=n, trigger_ts=trigger_ts, door_roi=roi
    )
    if len(sel) < max(4, n // 2):
        sel = _even_around_trigger(rows, n=n, trigger_ts=trigger_ts)
        method = "even_trigger_fallback"
        peak_off = 0.0

    offsets = [round(float(r["ts"]) - trigger_ts, 2) for r in sel]
    return {
        "ok": True,
        "frames": sel,
        "method": method,
        "trigger_ts": trigger_ts,
        "window_pre": pre,
        "window_post": post,
        "core_pre": cpre,
        "core_post": cpost,
        "door_roi": list(roi),
        "offsets_sec": offsets,
        "energy_peak_offset": peak_off,
        "coverage": cov,
        "n_window": len(rows),
        "n_core": len(core),
    }


def _even_around_trigger(
    rows: list[dict], *, n: int, trigger_ts: float
) -> list[dict]:
    """Even time samples preferring coverage of [trigger-pre…trigger+post]."""
    if not rows:
        return []
    if len(rows) <= n:
        return list(rows)
    # Rank by closeness to a uniform grid across the available span that
    # still brackets the trigger when possible.
    t0, t1 = float(rows[0]["ts"]), float(rows[-1]["ts"])
    if t1 <= t0:
        return rows[:n]
    targets = [t0 + (t1 - t0) * i / max(1, n - 1) for i in range(n)]
    # Bias grid toward trigger if trigger is inside span
    if t0 <= trigger_ts <= t1:
        # 60% of samples in the half nearer the trigger
        lo = max(t0, trigger_ts - (t1 - t0) * 0.35)
        hi = min(t1, trigger_ts + (t1 - t0) * 0.35)
        if hi > lo:
            targets = [lo + (hi - lo) * i / max(1, n - 1) for i in range(n)]
    picked: list[dict] = []
    used: set[int] = set()
    for tgt in targets:
        best_i, best_d = 0, 1e18
        for i, r in enumerate(rows):
            if i in used:
                continue
            d = abs(float(r["ts"]) - tgt)
            if d < best_d:
                best_d, best_i = d, i
        used.add(best_i)
        picked.append(rows[best_i])
    picked.sort(key=lambda r: float(r["ts"]))
    return picked


def _sample_motion_peak(
    rows: list[dict],
    *,
    n: int,
    trigger_ts: float,
    door_roi: tuple[float, float, float, float],
) -> tuple[list[dict], str, float]:
    """Energy peak inside CORE only; ROI-masked so wall/desk don't win."""
    import cv2

    if len(rows) <= n:
        return list(rows), "all_core", 0.0

    step = max(1, len(rows) // 48)
    probe = rows[::step]
    x0, y0, x1, y1 = door_roi
    energy: list[float] = []
    prev = None
    for r in probe:
        img = cv2.imread(r["path"], cv2.IMREAD_GRAYSCALE)
        if img is None:
            energy.append(0.0)
            continue
        h, w = img.shape[:2]
        xa, xb = int(max(0, x0) * w), int(min(1.0, x1) * w)
        ya, yb = int(max(0, y0) * h), int(min(1.0, y1) * h)
        if xb <= xa or yb <= ya:
            crop = img
        else:
            crop = img[ya:yb, xa:xb]
        g = cv2.resize(crop, (96, 72))
        energy.append(
            0.0 if prev is None else float((cv2.absdiff(prev, g) > 18).mean())
        )
        prev = g

    if not energy or max(energy) < 0.01:
        # Quiet door → even samples around trigger inside this row set
        return (
            _even_around_trigger(rows, n=n, trigger_ts=trigger_ts),
            "even_quiet_core",
            0.0,
        )

    # Short sliding window (~0.8–1.2s of probes) for peak motion
    win = max(2, min(6, len(energy) // 4 or 2))
    best_i, best_v = 0, -1.0
    for i in range(0, max(1, len(energy) - win + 1)):
        v = sum(energy[i : i + win])
        if v > best_v:
            best_v, best_i = v, i
    # Map probe peak back to row indices; expand to ~2s of real frames
    mid_probe = best_i + win // 2
    mid_row = min(len(rows) - 1, mid_probe * step)
    peak_ts = float(rows[mid_row]["ts"])
    peak_off = round(peak_ts - trigger_ts, 2)
    # Take a ±1.2s segment around peak, then even-sample n frames
    lo_ts, hi_ts = peak_ts - 1.2, peak_ts + 1.2
    seg = [r for r in rows if lo_ts <= float(r["ts"]) <= hi_ts] or rows
    if len(seg) <= n:
        return seg, "energy_peak_core", peak_off
    return (
        _even_around_trigger(seg, n=n, trigger_ts=peak_ts),
        "energy_peak_core",
        peak_off,
    )


# Back-compat name used by any external callers/tests
def _burst(rows: list[dict], n: int) -> list[dict]:
    if not rows:
        return []
    mid = float(rows[len(rows) // 2]["ts"])
    sel, _, _ = _sample_motion_peak(
        rows, n=n, trigger_ts=mid, door_roi=_door_roi()
    )
    return sel


def parse_verdict(txt: str) -> dict[str, Any]:
    """Pull the machine-usable decision out of the model's answer."""
    out: dict[str, Any] = {"direction": None, "crosser": None, "people": []}
    full = txt.upper()
    for line in txt.splitlines():
        u = line.upper()
        if "WHO CROSSED" in u:
            # Digit-scraping joined "1" and "2" from "...: 1 and DIRECTION: 2"
            # into crosser 12. Match the number that follows the label only.
            m = re.search(r"WHO\s+CROSSED\s*:?\s*(NONE|\d+)", u)
            out["crosser"] = (None if not m or m.group(1) == "NONE"
                              else int(m.group(1)))
            if "ENTERING" in u:
                out["direction"] = "add"
            elif "LEAVING" in u:
                out["direction"] = "sub"
            elif re.search(r"DIRECTION\s*:?\s*NONE", u):
                out["direction"] = None
            continue
        if out["direction"] is None and "DIRECTION" in u:
            if "ENTERING" in u:
                out["direction"] = "add"
            elif "LEAVING" in u:
                out["direction"] = "sub"
            continue
        # Keep any line carrying real person fields. Requiring SEX AND
        # MOTION AND a pipe on one line was too strict and dropped every
        # description on two of three clips — worse than the over-capture it
        # replaced, because Inspector matches on these strings. Accept a line
        # if it names at least two of the fields, in any layout, prose or not.
        hits = sum(1 for f in ("SEX", "HAIR", "TOP", "MOTION") if f + ":" in u)
        if hits >= 2:
            rec = re.sub(r"^[\s*\-#\d.):]+", "", line).strip()
            if rec and rec not in out["people"]:
                out["people"].append(rec)
    # Whole-text fallback if labels split across lines
    if out["direction"] is None:
        if re.search(r"DIRECTION\s*:?\s*ENTERING", full):
            out["direction"] = "add"
        elif re.search(r"DIRECTION\s*:?\s*LEAVING", full):
            out["direction"] = "sub"
    if out["crosser"] is None:
        m = re.search(r"WHO\s+CROSSED\s*:?\s*(NONE|\d+)", full)
        if m:
            out["crosser"] = (
                None if m.group(1) == "NONE" else int(m.group(1))
            )
    # A crosser index can only refer to a person that was actually listed.
    if out["crosser"] is not None and out["people"]:
        if not 1 <= out["crosser"] <= len(out["people"]):
            out["crosser_out_of_range"] = out["crosser"]
            out["crosser"] = None
    return out
