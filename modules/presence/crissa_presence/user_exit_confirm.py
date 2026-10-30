"""User-exit confirm — Argus exit is a hypothesis; seat + count decide AUTH.

AUTH-first: clothing / door-empty / Argus prose must not de-AUTH the sudo User.
A real leave should: Argus says exit → snap User seat → room video count →
only then drop the slot and AUTH. Approach-and-return stays AUTH.
"""
from __future__ import annotations

from . import paths as _paths

import os
from pathlib import Path
from typing import Any

import numpy as np


def sudo_user_id() -> str:
    return (
        os.environ.get("HOUSE_USER_ID")
        or os.environ.get("RAIL_PRIMARY_USER")
        or "user"
    ).strip().lower() or "user"


def user_seat_pose(cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """Desk / User seat. Tilt 0 — the chair, not a −5 torso crop."""
    if cfg is None:
        p = Path(
            os.environ.get(
                "RAIL_CAMERA_STOPS",
                str(_paths.DATA / "camera-stops.json"),
            )
        )
        try:
            import json

            raw = json.loads(p.read_text()) if p.is_file() else {}
        except Exception:
            raw = {}
        cfg = raw if isinstance(raw, dict) else {}
    for s in cfg.get("stops") or []:
        if not isinstance(s, dict):
            continue
        from . import seat_slots as _seats

        desk = int(_seats.user_seat())
        if str(s.get("id") or "") == "desk" or int(s.get("station") or 0) == desk:
            return {
                "pan": int(s.get("pan") or 0),
                "tilt": int(s.get("tilt") or 0),
            }
    return {"pan": 0, "tilt": 0}


def user_face_on_bgr(
    frame_bgr,
    uid: str,
    *,
    engine=None,
    enrolled: dict | None = None,
    threshold: float = 0.50,
) -> dict[str, Any]:
    """Does this still contain the sudo User's face? Fail closed on errors."""
    uid = (uid or sudo_user_id()).strip().lower()
    out: dict[str, Any] = {
        "found": False,
        "sim": None,
        "n_faces": 0,
        "name": None,
    }
    if frame_bgr is None:
        out["error"] = "no_frame"
        return out
    try:
        if engine is None:
            from .face_engine import get_face_engine

            engine = get_face_engine()
        faces = engine.detect(frame_bgr) or []
        out["n_faces"] = len(faces)
        if not faces:
            return out
        if enrolled is None:
            from .enrollments import load_enrolled

            enrolled = load_enrolled()
        from .enrollments import match

        best_name, best_sim = None, -1.0
        for face in faces:
            emb = engine.embed(face)
            if emb is None:
                continue
            name, sim = match(np.asarray(emb), enrolled, threshold=threshold)
            if sim > best_sim:
                best_sim = float(sim)
                best_name = name
        out["sim"] = None if best_sim < 0 else round(best_sim, 4)
        out["name"] = best_name
        out["found"] = bool(best_name == uid and best_sim >= threshold)
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{e}"[:160]
        return out


def decide_user_exit(
    *,
    seat: dict[str, Any],
    sweep_hits: list[dict[str, Any]] | None = None,
    user_exit: bool | None = None,
    user_exit_set: bool = False,
) -> dict[str, Any]:
    """Seat face wins. Clip vote is additive. Sweep hits are extra 'still here'.

    null + empty seat is confirm_failed (keep AUTH), not a drop. Standing up
    or a bad angle is not a leave.
    """
    extra = {
        "user_exit": user_exit,
        "user_exit_set": bool(user_exit_set),
    }
    if seat.get("found"):
        return {
            "confirmed": False,
            "reason": "user_in_seat",
            "seat": seat,
            "sweep_hits": list(sweep_hits or []),
            **extra,
        }
    hits = [h for h in (sweep_hits or []) if h.get("found")]
    if hits:
        return {
            "confirmed": False,
            "reason": "user_in_room",
            "seat": seat,
            "sweep_hits": hits,
            **extra,
        }
    if seat.get("error"):
        return {
            "confirmed": False,
            "reason": "confirm_failed",
            "seat": seat,
            "sweep_hits": list(sweep_hits or []),
            **extra,
        }
    if user_exit_set and user_exit is False:
        return {
            "confirmed": False,
            "reason": "clip_said_stay",
            "seat": seat,
            "sweep_hits": list(sweep_hits or []),
            **extra,
        }
    if user_exit_set and user_exit is None:
        return {
            "confirmed": False,
            "reason": "confirm_failed",
            "seat": seat,
            "sweep_hits": list(sweep_hits or []),
            **extra,
        }
    return {
        "confirmed": True,
        "reason": "seat_empty_and_no_user_face",
        "seat": seat,
        "sweep_hits": list(sweep_hits or []),
        **extra,
    }
