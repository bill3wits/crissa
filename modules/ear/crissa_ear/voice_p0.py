"""Voice emb P0 logger — treaty TREATY-VOICE-EMB-P0-20260801.md

Soft-ID measurement only. Never writes count / vl_delta.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .home import HOME
from typing import Any

import numpy as np

# Default path Fable expects; override with RAIL_VOICE_P0_LOG
_DEFAULT = HOME / "logs" / "voice_emb_p0.jsonl"

# Recent emb ring for consecutive-turn overlap heuristic (Gate 1 2026-08-06)
_RECENT: list[dict[str, Any]] = []
_RECENT_MAX = 8


def log_path() -> Path:
    raw = os.environ.get("RAIL_VOICE_P0_LOG", "").strip()
    return Path(raw) if raw else _DEFAULT


def _enabled() -> bool:
    # Default ON so P0 collects; set RAIL_VOICE_P0=0 to disable
    return os.environ.get("RAIL_VOICE_P0", "1").strip() not in ("0", "false", "off", "no")


def _consecutive_overlap_flip(
    top_id: str | None,
    top_sim: float | None,
    voice_emb: Any,
) -> bool:
    """Flag when consecutive turns lock *different* people both strongly.

    Not "same print twice" (that is normal continuous speech). Flip of top_id
    with both sims hot within a short window is the mixed-speaker signature.
    """
    global _RECENT
    now = time.time()
    window = float(os.environ.get("RAIL_VOICE_OVERLAP_WINDOW_S", "4.0"))
    thr = float(os.environ.get("RAIL_VOICE_MATCH_SIM", "0.35"))
    flip = False
    if top_id and top_sim is not None and top_sim >= thr and _RECENT:
        prev = _RECENT[-1]
        if (
            now - float(prev.get("ts") or 0) <= window
            and prev.get("top_id")
            and prev["top_id"] != top_id
            and float(prev.get("top_sim") or 0) >= thr
        ):
            # Optional: require embeddings not nearly identical (same talker)
            try:
                a = np.asarray(voice_emb, dtype=np.float32).ravel()
                b = np.asarray(prev.get("emb"), dtype=np.float32).ravel()
                if a.size and b.size and a.size == b.size:
                    na = float(np.linalg.norm(a)) or 1.0
                    nb = float(np.linalg.norm(b)) or 1.0
                    cos = float(np.dot(a, b) / (na * nb))
                    # Different people but very high cos → mixed/clip bleed
                    if cos >= float(os.environ.get("RAIL_VOICE_OVERLAP_EMB_COS", "0.55")):
                        flip = True
                else:
                    flip = True
            except Exception:
                flip = True
    # Always record for next turn (best-effort)
    try:
        emb_list = None
        if voice_emb is not None:
            emb_list = np.asarray(voice_emb, dtype=np.float32).ravel()[:64].tolist()
        _RECENT.append(
            {"ts": now, "top_id": top_id, "top_sim": top_sim, "emb": emb_list}
        )
        del _RECENT[:-_RECENT_MAX]
    except Exception:
        pass
    return flip


def would_stamp(
    top_sim: float | None,
    margin: float | None,
    *,
    n_roster: int,
    thr_sim: float,
    thr_margin: float,
) -> bool:
    """Simulate soft stamp: need open or closed set + sim + margin (if 2+ candidates)."""
    if top_sim is None or top_sim < thr_sim:
        return False
    if n_roster >= 3:
        return False  # crowd: refuse soft invent
    if n_roster == 0:
        return False  # empty: soft log only, no stamp invent (ghost)
    if n_roster >= 2 and (margin is None or margin < thr_margin):
        return False
    return True


def build_p0_row(
    *,
    presence: dict[str, Any],
    text: str,
    voice_emb: Any,
    stamp: dict[str, Any] | None,
    model: str = "ecapa",
) -> dict[str, Any] | None:
    """Build one log row. Returns None if no emb (nothing to measure)."""
    if voice_emb is None:
        return None
    from .voice_bank import rank_voice

    people = presence.get("people") or []
    named = []
    for p in people:
        if not isinstance(p, dict):
            continue
        n = p.get("name") or p.get("person_id")
        if n and not str(n).upper().startswith("U") and str(n) not in ("unknown", "nobody"):
            # skip pure slot labels like U1
            if str(n)[0].upper() == "U" and str(n)[1:].isdigit():
                continue
            named.append(str(n).lower())
    named = list(dict.fromkeys(named))
    n_roster = len(named)
    visitors = []
    for row in presence.get("visitor_today") or []:
        if isinstance(row, dict):
            vid = str(row.get("person_id") or row.get("name") or "").strip().lower()
        else:
            vid = str(row or "").strip().lower()
        if vid and vid not in named and vid not in visitors:
            visitors.append(vid)

    # Closed rank on occupancy + visitor_today (guests are not occupancy).
    rank_set = list(dict.fromkeys(named + visitors))
    roster_for_rank = rank_set if rank_set else None
    ranked = rank_voice(voice_emb, roster=roster_for_rank, top_k=3)
    # Also open-set top for diagnostics when roster closed
    open_ranked = rank_voice(voice_emb, roster=None, top_k=3) if named else ranked

    top_id = ranked[0][0] if ranked else None
    top_sim = round(ranked[0][1], 4) if ranked else None
    second_id = ranked[1][0] if len(ranked) > 1 else None
    second_sim = round(ranked[1][1], 4) if len(ranked) > 1 else None
    margin = None
    if top_sim is not None and second_sim is not None:
        margin = round(top_sim - second_sim, 4)
    elif top_sim is not None and n_roster <= 1:
        margin = None  # sole / empty second N/A

    sole = n_roster == 1
    # Overlap suspect (Gate 1 final 2026-08-06): diarization fired <2% — add
    # dual-strong emb (two bank people both hot, tight margin) + multi vocative.
    text = text or ""
    voc = (stamp or {}).get("vocative_hits") or []
    thr_sim = float(os.environ.get("RAIL_VOICE_MATCH_SIM", "0.35"))
    dual_strong = bool(
        top_sim is not None
        and second_sim is not None
        and margin is not None
        and top_sim >= thr_sim
        and second_sim >= max(0.25, thr_sim - 0.08)
        and margin < float(os.environ.get("RAIL_VOICE_OVERLAP_MARGIN", "0.08"))
        and n_roster >= 2
    )
    long_multi = len(text.split()) >= 40 and n_roster >= 2
    # Consecutive-turn: same high sim to *different* bank tops within a few
    # seconds often means two voices in one clip (mixed emb flipping).
    consec = _consecutive_overlap_flip(top_id, top_sim, voice_emb)
    overlap_suspect = bool(len(voc) >= 2) or dual_strong or long_multi or consec

    row = {
        "schema": "voice_emb_p0/v1",
        "ts": time.time(),
        "ts_local": time.strftime("%Y-%m-%d %H:%M:%S"),
        "person_ids_roster": named,
        "n_roster": n_roster,
        "top_id": top_id,
        "top_sim": top_sim,
        "second_id": second_id,
        "second_sim": second_sim,
        "margin": margin,
        "sole_present": sole,
        "overlap_suspect": overlap_suspect,
        "overlap_reasons": {
            "multi_vocative": len(voc) >= 2,
            "dual_strong_emb": dual_strong,
            "long_multi_roster": long_multi,
            "consecutive_flip": consec,
        },
        "would_stamp_at_0.04": would_stamp(
            top_sim, margin, n_roster=n_roster, thr_sim=thr_sim, thr_margin=0.04
        ),
        "would_stamp_at_0.08": would_stamp(
            top_sim, margin, n_roster=n_roster, thr_sim=thr_sim, thr_margin=0.08
        ),
        "text_len": len(text.strip()),
        "speech_duration_s": (stamp or {}).get("speech_duration_s"),
        "voice_skip": (stamp or {}).get("voice_skip"),
        "model": model,
        "stamp_person_id": (stamp or {}).get("person_id"),
        "stamp_source": (stamp or {}).get("source"),
        "open_top_id": open_ranked[0][0] if open_ranked else None,
        "open_top_sim": round(open_ranked[0][1], 4) if open_ranked else None,
        "count_trust": presence.get("count_trust"),
        "headcount": presence.get("headcount"),
    }
    return row


def append_p0(row: dict[str, Any] | None) -> None:
    if not _enabled() or not row:
        return
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never break ear on log IO
