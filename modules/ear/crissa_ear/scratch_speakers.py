"""S1 / S2 scratch voice slots — unidentified speakers, not occupancy.

S is Visitor's initial on the log. Unknowns are S1, S2 so they never collide
with her. Folders: <crissa home>/ear/scratch/s1|s2/voice/ (not the identity people/ tree).
When a scratch slot later matches a named person, prints move into that named
folder and the slot is emptied so the next unknown can be S1 again.

Not AUTH. Not inventory.

Open: a maybe-User match must not mint S1 (live receipt 2026-08-13:
s1/001.json named_sim=0.344 still opened). Promote: never into the User
voice bank; only prints that match *this* turn move; leftovers are dropped
so they cannot corrupt the next S1.

Promote guard (2026-09-07): bank growth is not a stamp. A guest folder grows
only when the SoT shows that guest today (visitor face / a slot) and this turn
is not the User by the isUser gate or by margin. 2026-09-02 02:58-06:34 six
turns stamped visitor while presence was hc=1 [the user]; seven prints of the User's
own 5 AM talk moved into people/visitor/voice/ and visitor stamps went 1-3/day to
271 / 587. `guest_promote_hold` is the switch (RAIL_SCRATCH_PROMOTE_GUARD=0
turns it off).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from .house_id import people_dir
from .home import EAR
from .voice_bank import _encoder_filter, _next_slot, _rec_encoder_family


def scratch_root() -> Path:
    """S1/S2 live here — not house-id/people/ (Phase I seal)."""
    return Path(
        os.environ.get("RAIL_SCRATCH_ROOT") or str(EAR / "scratch")
    ).expanduser()


def _bases(root: Path | None) -> tuple[Path, Path]:
    """(scratch tree, named-people tree). Tests pass one root for both."""
    if root is not None:
        return root, root
    return scratch_root(), people_dir()


SCRATCH_IDS = ("s1", "s2")
# Named bank must be this weak before we open a new scratch slot.
NAMED_MAX_TO_OPEN = float(os.environ.get("RAIL_SCRATCH_NAMED_MAX", "0.40"))
# User bank is AUTH. A weak User hit must not mint S1 (live 0.344 did).
USER_MAX_TO_OPEN = float(os.environ.get("RAIL_SCRATCH_USER_MAX", "0.25"))
# Same unknown as an existing S-slot.
SCRATCH_MATCH = float(os.environ.get("RAIL_SCRATCH_MATCH_SIM", "0.50"))
# Promote S-slot → named folder when this turn matches both.
PROMOTE_SIM = float(os.environ.get("RAIL_SCRATCH_PROMOTE_SIM", "0.52"))
MIN_DUR = float(os.environ.get("RAIL_SCRATCH_MIN_DUR", "1.2"))
MAX_PRINTS = int(os.environ.get("RAIL_SCRATCH_MAX_PRINTS", "12"))
DEDUP = float(os.environ.get("RAIL_SCRATCH_DEDUP", "0.95"))
# Bank write needs more than the stamp margin (NOT_USER_MARGIN 0.05): the
# 09-02 04:18 trigger was visitor 0.585 / the user 0.533 and moved the two most
# the User-like prints. A false print in a guest bank is worse than a missed one.
PROMOTE_USER_MARGIN = float(os.environ.get("RAIL_SCRATCH_PROMOTE_USER_MARGIN", "0.15"))
PROMOTE_GUARD = os.environ.get("RAIL_SCRATCH_PROMOTE_GUARD", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_SKIP_NAMED = {
    "crissa_her_tts",
    "crissa-her-tts",
    "tts_echo",
    "announcer",
    "house",
    "crew_tts",
    "dry_enroll",
}


def _user_id() -> str:
    """Canonical User id — env, else house-id person.json role=user."""
    env = (
        os.environ.get("HOUSE_USER_ID")
        or os.environ.get("RAIL_PRIMARY_USER")
        or ""
    ).strip().lower()
    if env:
        return env
    try:
        root = people_dir()
        if root.is_dir():
            for pj in sorted(root.glob("*/person.json")):
                data = json.loads(pj.read_text(encoding="utf-8"))
                if data.get("active") is False:
                    continue
                if str(data.get("role") or "").strip().lower() == "user":
                    return str(data.get("id") or pj.parent.name).strip().lower()
    except Exception:
        pass
    return ""


def _is_user(name: str | None) -> bool:
    n = (name or "").strip().lower()
    return bool(n) and n == _user_id()


def is_scratch_id(pid: str | None) -> bool:
    return str(pid or "").strip().lower() in SCRATCH_IDS


def _norm(emb: Any) -> np.ndarray | None:
    e = np.asarray(emb, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(e))
    if n < 1e-8:
        return None
    return e / n


def _ensure_slot(root: Path, slot: str) -> Path:
    d = root / slot
    voice = d / "voice"
    voice.mkdir(parents=True, exist_ok=True)
    pj = d / "person.json"
    if not pj.is_file():
        pj.write_text(
            json.dumps(
                {
                    "id": slot,
                    "display": slot.upper(),
                    "role": "scratch_voice",
                    "scratch": True,
                    "active": False,
                    "note": "Temporary unknown-speaker slot. Emptied when stamped to a named id.",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return voice


def _slot_embs(root: Path, slot: str) -> list[np.ndarray]:
    voice = root / slot / "voice"
    if not voice.is_dir():
        return []
    want = _encoder_filter()
    out: list[np.ndarray] = []
    for f in sorted(voice.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if want and _rec_encoder_family(rec) != want:
            continue
        enc = rec.get("encoding") or rec.get("embedding")
        if not enc:
            continue
        e = _norm(enc)
        if e is not None:
            out.append(e)
    return out


def _best_sim(emb: np.ndarray, bank: list[np.ndarray]) -> float:
    if not bank:
        return -1.0
    return float(max(np.dot(emb, k) for k in bank))


def _named_best(emb: np.ndarray) -> tuple[str | None, float]:
    from .voice_bank import load_voice_bank, rank_voice

    bank = load_voice_bank()
    for s in SCRATCH_IDS:
        bank.pop(s, None)
    ranked = rank_voice(emb, bank=bank, top_k=1)
    if not ranked:
        return None, -1.0
    return ranked[0][0], float(ranked[0][1])


def _write_print(voice: Path, slot: str, emb: np.ndarray, *, cue: str, sim: float) -> Path:
    n = 0
    for f in voice.glob("*.json"):
        if not f.name.startswith("_"):
            n += 1
    if n >= MAX_PRINTS:
        return voice / "capped"
    e = emb
    for f in voice.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            enc = rec.get("encoding") or rec.get("embedding")
            if not enc:
                continue
            k = _norm(enc)
            if k is not None and float(np.dot(e, k)) >= DEDUP:
                return f
        except Exception:
            continue
    from .voice_bank import live_print_model

    model = live_print_model()
    idx = _next_slot(voice)
    rec = {
        "person_id": slot,
        "modality": "voice",
        "model": model,
        "dim": int(e.shape[0]),
        "encoding": [float(x) for x in e.tolist()],
        "source": "scratch_live",
        "scratch_slot": slot,
        "voice_emb_sim": round(float(sim), 3),
        "cue": (cue or "")[:200],
        "ts": time.time(),
        "role": "scratch_voice",
    }
    path = voice / f"{idx:03d}.json"
    path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return path


def empty_slot(slot: str, *, root: Path | None = None) -> dict[str, Any]:
    """Wipe S-slot voice prints so the next unknown can reuse the id."""
    slot = str(slot).strip().lower()
    if slot not in SCRATCH_IDS:
        return {"ok": False, "error": "not_scratch"}
    scratch, _named = _bases(root)
    voice = scratch / slot / "voice"
    n = 0
    if voice.is_dir():
        for f in list(voice.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                f.unlink()
                n += 1
    _ensure_slot(scratch, slot)
    return {"ok": True, "emptied": slot, "removed": n}


def promote_slot_to_named(
    slot: str,
    named_id: str,
    *,
    root: Path | None = None,
    emb: Any = None,
) -> dict[str, Any]:
    """Move matching S-slot prints onto a named person, then empty the slot.

    Never writes into the User voice bank (AUTH). When *emb* is this turn's
    print, only folder files that match it at PROMOTE_SIM move; the rest are
    deleted so they cannot ride along or seed the next S1.
    """
    slot = str(slot).strip().lower()
    named = str(named_id).strip().lower()
    if slot not in SCRATCH_IDS or not named or is_scratch_id(named):
        return {"ok": False, "error": "bad_ids"}
    if named in _SKIP_NAMED:
        return {"ok": False, "error": "not_a_person"}
    if _is_user(named):
        return {"ok": False, "error": "no_promote_user"}
    scratch, named_root = _bases(root)
    src = scratch / slot / "voice"
    dest = named_root / named / "voice"
    dest.mkdir(parents=True, exist_ok=True)
    trigger = _norm(emb) if emb is not None else None
    moved: list[str] = []
    dropped: list[str] = []
    if src.is_dir():
        for f in sorted(src.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            dest_name = f"{slot}_{f.name}"
            target = dest / dest_name
            rec = None
            rec_emb = None
            if f.suffix == ".json":
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                    rec_emb = _norm(rec.get("encoding") or rec.get("embedding"))
                except Exception:
                    rec = None
                    rec_emb = None
            if trigger is not None:
                sim = float(np.dot(trigger, rec_emb)) if rec_emb is not None else -1.0
                if sim < PROMOTE_SIM:
                    f.unlink()
                    dropped.append(f.name)
                    continue
            if rec is not None:
                rec["person_id"] = named
                rec["promoted_from"] = slot
                rec["promoted_ts"] = time.time()
                target.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
                f.unlink()
                moved.append(dest_name)
                continue
            if trigger is None:
                shutil.move(str(f), str(target))
                moved.append(dest_name)
            else:
                f.unlink()
                dropped.append(f.name)
    empty_slot(slot, root=root)
    return {
        "ok": True,
        "from": slot,
        "to": named,
        "moved": moved,
        "dropped": dropped,
    }


def _presence_guest_ids(pres: dict[str, Any]) -> list[str]:
    """Named guests the SoT shows today: visitor faces + named slots."""
    from .stamp import visitor_today_ids

    out = [n for n in visitor_today_ids(pres) if n]
    for p in pres.get("people") or []:
        if not isinstance(p, dict):
            continue
        n = str(p.get("name") or p.get("person_id") or "").strip().lower()
        if n and n not in out:
            out.append(n)
    return out


def guest_promote_hold(stamp: dict[str, Any], named_id: str) -> dict[str, Any] | None:
    """Why this turn may NOT move scratch prints into *named_id*'s bank.

    None = promote is allowed. Reads the stamp only (isUser gate, sims, the
    presence snapshot the stamp carries) — no second presence fetch.
    """
    if not PROMOTE_GUARD:
        return None
    named = str(named_id or "").strip().lower()
    if stamp.get("is_user") is True:
        return {"why": "user_voice", "is_user_reason": stamp.get("is_user_reason")}
    try:
        user_sim = float(stamp["user_sim"]) if stamp.get("user_sim") is not None else None
    except (TypeError, ValueError):
        user_sim = None
    try:
        named_sim = (
            float(stamp["voice_emb_sim"]) if stamp.get("voice_emb_sim") is not None else None
        )
    except (TypeError, ValueError):
        named_sim = None
    if user_sim is None:
        # PR D (2026-09-09, review item 5): no User sim — an empty User voice
        # bank or an encoder-family mismatch — leaves no margin to measure.
        # Fail closed as near_user: a guest face seen earlier today would
        # otherwise carry the User's own talk into that guest's bank.
        return {
            "why": "near_user",
            "user_sim": None,
            "named_sim": None if named_sim is None else round(named_sim, 3),
            "margin": None,
            "need": PROMOTE_USER_MARGIN,
            "note": "no_user_sim",
        }
    if user_sim is not None and named_sim is not None:
        margin = named_sim - user_sim
        if margin < PROMOTE_USER_MARGIN:
            return {
                "why": "near_user",
                "user_sim": round(user_sim, 3),
                "named_sim": round(named_sim, 3),
                "margin": round(margin, 3),
                "need": PROMOTE_USER_MARGIN,
            }
    pres = stamp.get("presence") if isinstance(stamp.get("presence"), dict) else {}
    seen = _presence_guest_ids(pres)
    if named not in seen:
        return {
            "why": "no_guest_face",
            "guests_today": seen,
            "headcount": pres.get("headcount"),
        }
    return None


def assign_scratch(
    emb: Any,
    *,
    stamp: dict[str, Any],
    text: str = "",
    duration_s: float | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Stamp S1/S2 or promote a scratch folder onto a named id.

    Mutates stamp in place when it assigns. Never touches inventory.
    """
    e = _norm(emb)
    if e is None:
        return {"ok": False, "skipped": "no_emb"}
    dur = float(duration_s or stamp.get("speech_duration_s") or 0.0)
    if dur < MIN_DUR:
        return {"ok": False, "skipped": "short"}
    src = str(stamp.get("source") or "")
    if src in ("tts_echo_veto", "media_gate", "media_hold_sole") or src.startswith(
        "media"
    ):
        return {"ok": False, "skipped": "media_or_tts"}
    caller_root = root
    scratch, _named_root = _bases(caller_root)
    pid = str(stamp.get("person_id") or "").strip().lower() or None
    named_id, named_sim = _named_best(e)

    # Named hit this turn — if it also matches an S-slot, merge and empty.
    # User is AUTH: never dump scratch prints into people/<user>/voice/.
    if pid and not is_scratch_id(pid) and pid not in _SKIP_NAMED:
        if src in ("voice_emb", "voice_emb_vs_sole", "voice_emb_visitor"):
            if _is_user(pid):
                return {"ok": False, "skipped": "no_promote_user"}
            hold = guest_promote_hold(stamp, pid)
            if hold:
                stamp["scratch_promote_hold"] = hold
                return {"ok": False, "skipped": hold["why"], "hold": hold}
            for slot in SCRATCH_IDS:
                sim_s = _best_sim(e, _slot_embs(scratch, slot))
                if sim_s >= PROMOTE_SIM:
                    promo = promote_slot_to_named(
                        slot, pid, root=caller_root, emb=e
                    )
                    stamp["scratch_promoted"] = promo
                    return {"ok": True, "action": "promoted", **promo}
        return {"ok": False, "skipped": "already_named"}

    # Weak sole inherit of a named id that the voice does not support → unknown.
    try:
        vsim = float(stamp.get("voice_emb_sim")) if stamp.get("voice_emb_sim") is not None else None
    except (TypeError, ValueError):
        vsim = None
    sole_lie = bool(src.startswith("sole") and (vsim is None or vsim < NAMED_MAX_TO_OPEN))

    if pid and not sole_lie and not is_scratch_id(pid):
        return {"ok": False, "skipped": "keep_named"}

    if named_id and named_sim >= NAMED_MAX_TO_OPEN and not sole_lie:
        # Too close to a known person to mint a new S-slot.
        return {
            "ok": False,
            "skipped": "near_named",
            "named": named_id,
            "named_sim": round(named_sim, 3),
        }
    if named_id and _is_user(named_id) and named_sim >= USER_MAX_TO_OPEN:
        # Weak User (0.25–0.40) used to open S1, including under sole* sources.
        # Do not skip this on sole_lie — that is exactly the 0.344 band.
        return {
            "ok": False,
            "skipped": "maybe_user",
            "named": named_id,
            "named_sim": round(named_sim, 3),
        }

    best_slot = None
    best_s = -1.0
    for slot in SCRATCH_IDS:
        sim_s = _best_sim(e, _slot_embs(scratch, slot))
        if sim_s > best_s:
            best_s = sim_s
            best_slot = slot

    if best_slot and best_s >= SCRATCH_MATCH:
        voice = _ensure_slot(scratch, best_slot)
        path = _write_print(voice, best_slot, e, cue=text, sim=best_s)
        stamp["person_id"] = best_slot
        stamp["source"] = f"scratch_{best_slot}"
        stamp["certain"] = False
        stamp["id_status"] = "provisional"
        stamp["display_name"] = best_slot.upper()
        stamp["scratch_sim"] = round(best_s, 3)
        stamp["scratch_path"] = str(path)
        return {"ok": True, "action": "match", "slot": best_slot, "sim": round(best_s, 3)}

    # Open the first empty slot (s1, then s2).
    for slot in SCRATCH_IDS:
        if not _slot_embs(scratch, slot):
            voice = _ensure_slot(scratch, slot)
            path = _write_print(voice, slot, e, cue=text, sim=named_sim)
            stamp["person_id"] = slot
            stamp["source"] = f"scratch_{slot}"
            stamp["certain"] = False
            stamp["id_status"] = "provisional"
            stamp["display_name"] = slot.upper()
            stamp["scratch_opened"] = True
            stamp["scratch_path"] = str(path)
            stamp["named_best"] = named_id
            stamp["named_sim"] = round(named_sim, 3) if named_sim is not None else None
            return {"ok": True, "action": "open", "slot": slot}

    return {"ok": False, "skipped": "slots_full", "best": best_slot, "sim": round(best_s, 3)}
