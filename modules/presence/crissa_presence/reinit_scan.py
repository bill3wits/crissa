"""Flexible reinit / error-correct scan sequence (eval-driven).

Tight path (default):
  1) buffer_blast  (0 gap) — door ring evidence
  2) three_pulse   (0 gap) — fresh door snaps
  3) live_snap inspect (shadow) — emb stamp quality
  4) seat_scan only if eval says weak / roster mismatch / force_pan

User desk move / room reinit:
  force_refs=True → multi-stop empty-room reference capture (nightly path)
  One button: POST /v1/reinit_scan {"mode":"user_reinit"}

Look-around / rescan (the User 2026-08-02):
  One tool: look_around (aliases rescan, rescan_room). Init vs everyday
  differ **only by range** (full vs limited). See look_around.py.
"""

from __future__ import annotations

from . import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from . import paths as _paths

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import error_peek as peek
from . import seat_slots as seats
from .refs import LIVE_DIR as REF_DIR

PRESENCE = (os.environ.get("RAIL_PRESENCE_URL") or os.environ.get("RAIL_PRESENCE_BASE")  # this install's own (2026-10-10)
            or _ports.url("presence")).rstrip("/")

# Auto-rescan throttle (empty/sole-user path)
_auto_lock = threading.Lock()
_last_auto_ts = 0.0
_AUTO_MIN_INTERVAL_SEC = float(os.environ.get("RAIL_RESCAN_AUTO_MIN_SEC", "120"))
_layout_suspect = False
_layout_suspect_ts = 0.0
_layout_suspect_reason = ""


def _presence() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{PRESENCE}/v1/presence", timeout=4) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def eval_need_pan(presence: dict, inspect: dict | None) -> dict[str, Any]:
    """Decide whether seat_scan pan is worth the door gap."""
    reasons: list[str] = []
    hc = int(presence.get("headcount") or 0)
    trust = str(presence.get("count_trust") or "")
    certain = bool(presence.get("count_certain"))
    people = presence.get("people") or []
    named = [
        p.get("name")
        for p in people
        if isinstance(p, dict) and p.get("name") and not str(p.get("name")).startswith("U")
    ]
    door = presence.get("door_names") or []
    le = presence.get("last_event") or {}

    if trust in ("stale", "reconciling"):
        reasons.append("trust_stale")
    if not certain and hc > 0:
        reasons.append("count_uncertain")
    if named and not door and le.get("kind") == "door_hold":
        # claimed people but empty door FOV — ghost risk (another person case)
        reasons.append("named_but_door_empty")
    if any(str(p.get("name", "")).startswith("U") for p in people if isinstance(p, dict)):
        reasons.append("unnamed_slots")

    insp = inspect or {}
    v = insp.get("verdict") if isinstance(insp.get("verdict"), dict) else {}
    sim = v.get("best_sim")
    try:
        sim_f = float(sim) if sim is not None else -1.0
    except (TypeError, ValueError):
        sim_f = -1.0
    level = v.get("stamp_level")
    n_faces = insp.get("n_faces")
    if n_faces == 0 and hc > 0:
        reasons.append("inspect_no_face_but_hc")
    if sim_f >= 0 and sim_f < 0.35 and named:
        reasons.append("inspect_sim_low_vs_roster")
    if level in (None, "none") and named and (n_faces or 0) > 0:
        reasons.append("inspect_stamp_none_with_faces")

    need = bool(reasons)
    # quality-only skip: if door FOV already has strong certain sole match, no pan
    if (
        certain
        and hc == 1
        and len(named) == 1
        and level in ("certain", "provisional")
        and sim_f >= 0.5
    ):
        need = False
        reasons = ["skip_strong_sole_match"]

    return {"need_pan": need, "reasons": reasons, "sim": sim_f, "stamp_level": level}


def run_flexible_scan(
    *,
    force_pan: bool = False,
    force_refs: bool = False,
    inspect: bool = True,
    presence: dict | None = None,
) -> dict[str, Any]:
    """Eval-driven sequence. Wall never scanned (seat_scan only)."""
    t0 = time.time()
    pres = presence if presence is not None else _presence()
    steps: list[dict[str, Any]] = []

    b = peek.buffer_blast(n=10, stride=2)
    steps.append({"step": "buffer_blast", **{k: b.get(k) for k in ("ok", "n", "wall_sec", "door_gap_sec")}})

    p3 = peek.three_pulse(gap_sec=0.35)
    steps.append({"step": "three_pulse", **{k: p3.get(k) for k in ("ok", "n", "wall_sec")}})

    insp_out: dict[str, Any] | None = None
    if inspect:
        try:
            from .inspector import live_snap

            insp_out = live_snap(
                presence=pres,
                force=True,
                trigger="reinit_eval",
                attach_voice=True,
            )
            v = insp_out.get("verdict") if isinstance(insp_out.get("verdict"), dict) else {}
            steps.append(
                {
                    "step": "live_snap",
                    "ok": insp_out.get("ok"),
                    "n_faces": insp_out.get("n_faces"),
                    "stamp_level": v.get("stamp_level"),
                    "person_id": v.get("person_id"),
                    "best_sim": v.get("best_sim"),
                }
            )
        except Exception as e:
            steps.append({"step": "live_snap", "ok": False, "error": str(e)[:120]})

    decision = eval_need_pan(pres, insp_out)
    seat_out = None
    if force_pan or decision.get("need_pan"):
        seat_out = peek.seat_scan(settle=0.25)
        steps.append(
            {
                "step": "seat_scan",
                "ok": seat_out.get("ok"),
                "wall_sec": seat_out.get("wall_sec"),
                "wall_skipped": True,
                "forced": force_pan,
                "reasons": decision.get("reasons"),
            }
        )
        # second inspect after pan
        if inspect:
            try:
                from .inspector import live_snap

                insp2 = live_snap(
                    presence=_presence(),
                    force=True,
                    trigger="reinit_after_seat",
                )
                v2 = insp2.get("verdict") if isinstance(insp2.get("verdict"), dict) else {}
                steps.append(
                    {
                        "step": "live_snap_after_seat",
                        "ok": insp2.get("ok"),
                        "n_faces": insp2.get("n_faces"),
                        "stamp_level": v2.get("stamp_level"),
                        "person_id": v2.get("person_id"),
                        "best_sim": v2.get("best_sim"),
                    }
                )
                insp_out = insp2
            except Exception as e:
                steps.append({"step": "live_snap_after_seat", "ok": False, "error": str(e)[:80]})
    else:
        steps.append({"step": "seat_scan", "skipped": True, "reasons": decision.get("reasons")})

    refs_out = None
    if force_refs:
        refs_out = capture_reference_frames(force=True)
        steps.append({"step": "reference_capture", **{k: refs_out.get(k) for k in ("ok", "n_ok", "error", "ref_dir") if k in refs_out or True}})

    return {
        "ok": True,
        "schema": "reinit_scan/v1",
        "wall_sec": round(time.time() - t0, 3),
        "eval": decision,
        "steps": steps,
        "inspect": {
            "n_faces": (insp_out or {}).get("n_faces"),
            "stamp_level": ((insp_out or {}).get("verdict") or {}).get("stamp_level")
            if isinstance((insp_out or {}).get("verdict"), dict)
            else None,
            "person_id": ((insp_out or {}).get("verdict") or {}).get("person_id")
            if isinstance((insp_out or {}).get("verdict"), dict)
            else None,
            "best_sim": ((insp_out or {}).get("verdict") or {}).get("best_sim")
            if isinstance((insp_out or {}).get("verdict"), dict)
            else None,
        }
        if insp_out
        else None,
        "seat_scan": {"ran": seat_out is not None, "wall_sec": (seat_out or {}).get("wall_sec")},
        "refs": refs_out,
        "presence_after": {
            "headcount": _presence().get("headcount"),
            "people": [
                p.get("name")
                for p in (_presence().get("people") or [])
                if isinstance(p, dict)
            ],
        },
        "note": "flexible reinit — pan only when eval says so; wall never aimed",
    }


def capture_reference_frames(*, force: bool = False) -> dict[str, Any]:
    """Empty-room multi-stop refs for 35B poster/SCREEN baseline (M&S + one-button)."""
    import subprocess

    script = Path("/usr/local/bin/room-reference-capture")
    if not script.is_file():
        script = _paths.PACK / "scripts" / "room-reference-capture"
    if not script.is_file():
        return {"ok": False, "error": "room-reference-capture script missing"}
    cmd = ["bash", str(script)]
    if force:
        cmd.append("--force")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "RRC_REF_DIR": str(REF_DIR)},
        )
        return {
            "ok": r.returncode == 0,
            "rc": r.returncode,
            "ref_dir": str(REF_DIR),
            "stdout_tail": (r.stdout or "")[-800:],
            "stderr_tail": (r.stderr or "")[-400:],
            "n_ok": _count_refs(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:160], "ref_dir": str(REF_DIR)}


def _count_refs() -> int:
    if not REF_DIR.is_dir():
        return 0
    return len(list(REF_DIR.glob("room-empty-stop*.jpg"))) + len(
        list(REF_DIR.glob("home-empty-*.jpg"))
    )


def list_refs() -> dict[str, Any]:
    files = []
    if REF_DIR.is_dir():
        for p in sorted(REF_DIR.glob("*.jpg")):
            if p.name.startswith("_"):
                continue
            files.append(
                {
                    "name": p.name,
                    "bytes": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                    "mtime_local": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)
                    ),
                }
            )
    return {
        "ok": True,
        "ref_dir": str(REF_DIR),
        "n": len(files),
        "files": files,
        "cron": "M&S 22:30 + 23:55/07:15/12:30 empty VIDEO sweep (not station stills)",
    }


def layout_status() -> dict[str, Any]:
    """Panel/Oracle: layout_suspect flag + last auto rescan."""
    return {
        "ok": True,
        "layout_suspect": _layout_suspect,
        "layout_suspect_ts": _layout_suspect_ts or None,
        "layout_suspect_reason": _layout_suspect_reason or None,
        "last_auto_rescan_ts": _last_auto_ts or None,
        "auto_min_interval_sec": _AUTO_MIN_INTERVAL_SEC,
        "primary_user": seats.primary_user(),
        "user_seat": seats.user_seat(),
        "multi_ok": seats.multi_ok_stations(),
        "wall": seats.wall_stations(),
    }


def set_layout_suspect(reason: str, *, on: bool = True) -> dict[str, Any]:
    global _layout_suspect, _layout_suspect_ts, _layout_suspect_reason
    _layout_suspect = bool(on)
    _layout_suspect_ts = time.time() if on else 0.0
    _layout_suspect_reason = reason if on else ""
    return layout_status()


def scenery_shift_vs_refs(*, max_diff: float = 0.28) -> dict[str, Any]:
    """Cheap CODE scenery check: door JPEG vs newest empty rest ref.

    Not people-aware (use allow_auto only when empty/sole User).
    Returns high when mean abs-diff of downscaled gray is large.
    """
    try:
        import numpy as np
        from PIL import Image
        import io
    except Exception as e:
        return {"ok": False, "error": f"deps:{e}", "level": "unknown"}

    # live door snap
    cam = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
    try:
        with urllib.request.urlopen(f"{cam}/snapshot", timeout=5) as r:
            live = r.read()
    except Exception as e:
        return {"ok": False, "error": f"live:{e}", "level": "unknown"}

    refs = sorted(
        list(REF_DIR.glob("room-empty-stop*.jpg"))
        + list(REF_DIR.glob("home-empty-*.jpg")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not refs:
        return {
            "ok": True,
            "level": "no_ref",
            "diff": None,
            "note": "no empty refs yet — nightly or rescan when empty",
        }

    def _gray_small(raw: bytes) -> "np.ndarray":
        im = Image.open(io.BytesIO(raw)).convert("L").resize((64, 36))
        return np.asarray(im, dtype=np.float32) / 255.0

    try:
        a = _gray_small(live)
        b = _gray_small(refs[0].read_bytes())
        diff = float(np.mean(np.abs(a - b)))
    except Exception as e:
        return {"ok": False, "error": str(e)[:120], "level": "unknown"}

    if diff >= max_diff * 1.6:
        level = "high"
    elif diff >= max_diff:
        level = "med"
    else:
        level = "low"
    if level in ("high", "med"):
        set_layout_suspect(f"scenery_{level}_diff={diff:.3f}", on=True)
    return {
        "ok": True,
        "level": level,
        "diff": round(diff, 4),
        "ref": refs[0].name,
        "threshold": max_diff,
    }


def run_rescan_room(**kwargs: Any) -> dict[str, Any]:
    """Alias → look_around (limited unless range/init set)."""
    from .look_around import rescan_room

    return rescan_room(**kwargs)


def maybe_auto_rescan(
    presence: dict | None = None,
    *,
    reason: str = "empty_transition",
    scenery_check: bool = False,
) -> dict[str, Any]:
    """Alias → look_around.maybe_auto_look_around (no occupancy gate)."""
    from .look_around import maybe_auto_look_around

    _ = presence, scenery_check  # kept for callers
    return maybe_auto_look_around(reason=reason)
