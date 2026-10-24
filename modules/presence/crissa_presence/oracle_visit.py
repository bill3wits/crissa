"""Oracle visit instruction — one object, Argus judges, rails write.

docs/ARGUS-ORACLE-LOOP-20260815.md
Does not grant AUTH. Guests never +1.
"""
from __future__ import annotations

from . import paths as _paths

import json
import time
from typing import Any

from .guests import is_guest

EVENTS = frozenset({"enter", "exit", "sighting"})

# Two-glyph wall ids. a guest's log S stays a guest — unknowns are S1/S2.
_SHORT = {       # the household's glyphs do not ship (2026-10-09): only the scratch slots; a person reads as initials
    "s1": "S1",
    "s2": "S2",
}


def occupant_cards(
    visits: dict[str, dict[str, Any]] | None,
    inventory: Any,
) -> list[dict[str, Any]]:
    """Still-present enter cards: robe/hair/sex we match a leave clip against."""
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    present = getattr(inventory, "present", {}) or {}
    traits = getattr(inventory, "traits_live", {}) or {}
    gender = getattr(inventory, "gender", {}) or {}

    def _add(pid: Any, clothes: Any, hair: Any, sex: Any) -> None:
        p = str(pid or "").strip().lower()
        if not p or p in seen or p == "unknown":
            return
        if not (clothes or hair or sex):
            return
        seen.add(p)
        cards.append(
            {"person_id": p, "clothes": clothes, "hair": hair, "sex": sex}
        )

    for pid, row in (visits or {}).items():
        if not isinstance(row, dict):
            continue
        if row.get("exit_ts") is not None:
            continue
        if str(row.get("event") or "").lower() == "exit":
            continue
        p = str(pid or row.get("person_id") or "").strip().lower()
        # Ghost visits (05:43 another person return, 05:49:54 sighting) are not
        # bodies. 06:32:14 applied exit another person with hc 2→2 because this
        # loop offered a card for someone who was not present.
        if p not in present:
            continue
        _add(p, row.get("clothes"), row.get("hair"), row.get("sex") or gender.get(p))
    for p in list(present):
        if p in seen:
            continue
        row = (visits or {}).get(p) or {}
        tl = traits.get(p) or {}
        clothes = (
            row.get("clothes")
            or tl.get("clothes")
            or tl.get("shirt")
            or tl.get("summary")
        )
        hair = row.get("hair") or tl.get("hair")
        sex = row.get("sex") or tl.get("sex") or gender.get(p)
        _add(p, clothes, hair, sex)
    return cards


def _is_unnamed_slot(pid: str) -> bool:
    n = (pid or "").strip().lower()
    if not n or n == "unknown" or n.startswith("unknown") or n.startswith("__"):
        return True
    if n.startswith("u") and n[1:].isdigit():
        return True
    return False


def named_present_ids(inventory: Any) -> list[str]:
    """Bodies with a real id. U1-style ghosts do not hide a sole User exit."""
    out: list[str] = []
    for p in (getattr(inventory, "present", {}) or {}):
        n = str(p or "").strip().lower()
        if n and not _is_unnamed_slot(n):
            out.append(n)
    return out


def named_sole_id(inventory: Any) -> str | None:
    ids = named_present_ids(inventory)
    return ids[0] if len(ids) == 1 else None


def short_id(person_id: str | None) -> str:
    """Wall glyph. Law: LAST-HEARD-GHOST + vo_sound.initials_for.

    Regular/User ``Bi`` (second letter of the first name lowercase). Guest
    ``BT`` (F/L, both caps) from person.json initials when Register stored them.
    """
    n = (person_id or "").strip().lower()
    if not n:
        return "?"
    if n in _SHORT:
        return _SHORT[n]
    if n.startswith("s") and n[1:].isdigit():
        return f"S{n[1:]}"
    if n.startswith("x") and n[1:].isdigit():
        return f"X{n[1:]}"
    rec = _person_card(n)
    if rec:
        ini = str(rec.get("initials") or "").strip()
        if ini:
            return ini
        role = str(rec.get("role") or "").strip().lower()
        disp = str(rec.get("display") or n)
        if role in ("guest", "visitor"):
            try:
                import sys
                from pathlib import Path

                lib = _paths.PACK / "lib"
                if lib.is_dir() and str(lib) not in sys.path:
                    sys.path.insert(0, str(lib))
                from vo_sound import initials_for

                mark = initials_for(disp, kind="visitor")
                if mark:
                    return mark
            except Exception:
                pass
    alnum = "".join(c for c in n if c.isalnum())
    if len(alnum) >= 2:
        return alnum[0].upper() + alnum[1].lower()
    return (alnum or n)[:2].title() or "?"


def _person_card(person_id: str) -> dict[str, Any] | None:
    try:
        from .enrollments import house_id_people

        pj = house_id_people() / person_id / "person.json"
        if not pj.is_file():
            return None
        rec = json.loads(pj.read_text())
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def apply_visit(
    *,
    inventory: Any,
    visits: dict[str, dict[str, Any]],
    event: str,
    person_id: str,
    now: float | None = None,
    clothes: str | None = None,
    hair: str | None = None,
    sex: str | None = None,
    faceid: dict[str, Any] | None = None,
    pack_id: str | None = None,
    note: str | None = None,
    event_id: str | None = None,
    leave_match: dict[str, Any] | None = None,
    user_exit: bool | None = None,
    user_exit_set: bool = False,
) -> dict[str, Any]:
    """Mutate inventory + visits overlay. Never writes auth hold."""
    now = float(now if now is not None else time.time())
    ev = str(event or "").strip().lower()
    pid = str(person_id or "").strip().lower()
    eid = str(event_id or pack_id or "").strip()
    lm = dict(leave_match) if isinstance(leave_match, dict) else None
    refuse = False
    refuse_why: dict[str, Any] = {}
    incoming = pid
    if ev == "exit":
        # Face ID on a back is noise. Who-left = robe/hair/sex vs enter cards,
        # else the one named body (U* ghosts do not hide a sole User).
        # Argus `who` is not identity (WP-4).
        from .traits_leave import (
            clothes_from_prose,
            leave_contradicts_card,
            match_leave_occupant,
            sex_mismatch,
        )
        from .user_exit_confirm import sudo_user_id

        pid = ""
        if not (clothes or hair):
            harvested = clothes_from_prose(note or "")
            if harvested:
                clothes = harvested
        cards = occupant_cards(visits, inventory)
        leave_clip = {"clothes": clothes, "hair": hair, "sex": sex}
        uid = sudo_user_id()
        user_card = next((c for c in cards if c.get("person_id") == uid), None)
        user_contradicts = bool(
            user_card
            and (clothes or hair or sex)
            and leave_contradicts_card(leave_clip, user_card)
        )
        user_sex_bad = bool(user_card and sex_mismatch(leave_clip, user_card))
        sole = named_sole_id(inventory)
        if clothes or hair or sex:
            picked, sc, why = match_leave_occupant(leave_clip, cards)
            if picked and picked == uid and user_contradicts:
                picked = None
            if picked:
                pid = picked
                lm = {
                    "method": "clothes_hair",
                    "person_id": picked,
                    "score": round(float(sc), 3),
                    **why,
                }
            elif (
                sole == uid
                and user_contradicts
                and not user_sex_bad
            ):
                # 08:20 unnamed_exit: sole User, enter card still a robe,
                # leave clip is today's shirt. Color mismatch is a stale
                # card, not a second body. Seat confirm decides AUTH.
                pid = uid
                lm = {
                    "method": "sole_stale_card",
                    "person_id": uid,
                    "score": 0.0,
                    "why": "stale_enter_card",
                }
            elif (
                sole
                and sole != uid
                and len(cards) == 1
                and leave_contradicts_card(leave_clip, cards[0])
                and not sex_mismatch(leave_clip, cards[0])
            ):
                pid = sole
                lm = {
                    "method": "sole_named",
                    "person_id": sole,
                    "score": 1.0,
                    "why": "stale_enter_card",
                }
            else:
                named = next(
                    (c for c in cards if c.get("person_id") == incoming), None
                )
                sole_card = cards[0] if len(cards) == 1 else None
                if user_contradicts or (
                    named and leave_contradicts_card(leave_clip, named)
                ) or (
                    not incoming
                    and sole_card
                    and leave_contradicts_card(leave_clip, sole_card)
                ):
                    refuse = True
                    refuse_why = {
                        "method": "refused",
                        "person_id": None,
                        "score": round(float(sc), 3),
                        **why,
                    }
                    if user_contradicts:
                        pid = uid
                    elif named:
                        pid = str(named["person_id"])
                    elif sole_card:
                        pid = str(sole_card["person_id"])
        if not pid and not refuse:
            if sole == uid and user_contradicts:
                if user_sex_bad:
                    refuse = True
                    refuse_why = {
                        "method": "refused",
                        "person_id": uid,
                        "score": 0.0,
                        "why": "user_card_contradicts",
                    }
                    pid = uid
                else:
                    pid = uid
                    lm = {
                        "method": "sole_stale_card",
                        "person_id": uid,
                        "score": 0.0,
                        "why": "stale_enter_card",
                    }
            elif sole:
                pid = sole
                lm = lm or {
                    "method": "sole_named",
                    "person_id": sole,
                    "score": 1.0,
                }
            elif incoming == uid and incoming in named_present_ids(inventory):
                # Two named, no robe: User exit stays a hypothesis (confirm).
                # Regulars: clothes or sole only — Argus `who` is not identity.
                pid = incoming
    if eid:
        try:
            from .oracle_event import get_event, stamp_argus, stamp_person

            if get_event(eid) is not None:
                stamped = stamp_argus(
                    eid,
                    event=ev,
                    clothes=clothes,
                    hair=hair,
                    sex=sex,
                    note=note,
                    pack_id=pack_id,
                )
                row0 = (stamped or {}).get("row") or {}
                if row0.get("person_id") and not pid:
                    pid = str(row0["person_id"])
                if pid and not refuse:
                    stamp_person(eid, pid)
        except Exception:
            pass
    out: dict[str, Any] = {
        "ok": False,
        "applied": False,
        "event": ev,
        "person_id": pid,
        "event_id": eid or None,
        "short_id": short_id(pid),
        "reason": "ok",
        "headcount_before": int(inventory.headcount()),
        "headcount_after": int(inventory.headcount()),
        "slot": None,
    }
    if ev not in EVENTS:
        out["reason"] = "bad_event"
        return out
    if refuse:
        out["reason"] = "appearance_contradicts"
        out["leave_match"] = refuse_why
        return out
    if not pid:
        out["reason"] = "need_person_id"
        return out

    if is_guest(pid):
        if hasattr(inventory, "note_visitor"):
            inventory.note_visitor(pid, now=now, source="oracle_visit")
        row = _merge_row(
            visits.get(pid),
            event="sighting",
            pid=pid,
            now=now,
            clothes=clothes,
            hair=hair,
            sex=sex,
            faceid=faceid,
            pack_id=pack_id,
            note=note,
            enter_ts=now,
            exit_ts=None,
        )
        visits[pid] = row
        out.update(
            ok=True,
            applied=True,
            reason="guest_visitor",
            event="sighting",
            headcount_after=int(inventory.headcount()),
            slot=row,
        )
        return out

    present = pid in (getattr(inventory, "present", {}) or {})
    if ev == "exit" and not present and pid not in (
        getattr(inventory, "sticky", set()) or set()
    ):
        out["reason"] = "not_present"
        return out
    applied_as = ev
    if ev == "enter" and present:
        applied_as = "sighting"
        out["reason"] = "already_present"

    if applied_as == "enter":
        inventory.present[pid] = now
        inventory.baseline_set = True
        if hasattr(inventory, "events"):
            inventory.events.append(
                {
                    "kind": "enter",
                    "name": pid,
                    "ts": now,
                    "source": "oracle_visit",
                    "pack": pack_id,
                }
            )
        if hasattr(inventory, "_touch_peaks"):
            inventory._touch_peaks(now=now)
        row = _merge_row(
            visits.get(pid),
            event="enter",
            pid=pid,
            now=now,
            clothes=clothes,
            hair=hair,
            sex=sex,
            faceid=faceid,
            pack_id=pack_id,
            note=note,
            enter_ts=now,
            exit_ts=None,
        )
        visits[pid] = row
        if eid:
            try:
                from .oracle_event import mark_counted

                mark_counted(eid, True)
            except Exception:
                pass
    elif applied_as == "sighting":
        if present:
            inventory.present[pid] = now
        row = _merge_row(
            visits.get(pid),
            event="sighting",
            pid=pid,
            now=now,
            clothes=clothes,
            hair=hair,
            sex=sex,
            faceid=faceid,
            pack_id=pack_id,
            note=note,
            enter_ts=(visits.get(pid) or {}).get("enter_ts")
            or (inventory.present.get(pid) if present else now),
            exit_ts=None,
        )
        visits[pid] = row
    else:  # exit
        prior = visits.get(pid) or {}
        enter_ts = prior.get("enter_ts") or (
            inventory.present.get(pid) if present else now
        )
        # Keep the enter robe/hair on the visit. The leave clip is evidence
        # of who walked out, not a replacement for the card we match against.
        enter_clothes = prior.get("clothes")
        enter_hair = prior.get("hair")
        enter_sex = prior.get("sex")
        from .user_exit_confirm import sudo_user_id

        method = str((lm or {}).get("method") or "")
        clothes_or_sole = method in ("clothes_hair", "sole_named", "sole")
        # User AUTH never drops on clothes/sole alone (2026-08-25: that skip
        # was the live hole). Clip verdict + seat snap decide. Regulars still
        # leave immediately on clothes_hair / sole_named.
        if (
            pid == sudo_user_id()
            and (present or pid in (getattr(inventory, "sticky", set()) or set()))
        ):
            row = _merge_row(
                prior,
                event="exit",
                pid=pid,
                now=now,
                clothes=enter_clothes,
                hair=enter_hair,
                sex=enter_sex,
                faceid=faceid,
                pack_id=pack_id,
                note=note,
                enter_ts=enter_ts,
                exit_ts=None,
            )
            _attach_leave_clip(row, clothes=clothes, hair=hair, sex=sex, leave_match=lm)
            row["pending_confirm"] = True
            visits[pid] = row
            out.update(
                ok=True,
                applied=True,
                event="exit",
                reason="user_exit_needs_confirm",
                headcount_after=int(inventory.headcount()),
                slot=row,
                leave_match=lm,
                user_exit=user_exit,
                user_exit_set=bool(user_exit_set),
            )
            return out
        leave_src = "argus_clothes_exit" if clothes_or_sole else "oracle_visit"
        row = _merge_row(
            prior,
            event="exit",
            pid=pid,
            now=now,
            clothes=enter_clothes,
            hair=enter_hair,
            sex=enter_sex,
            faceid=faceid,
            pack_id=pack_id,
            note=note,
            enter_ts=enter_ts,
            exit_ts=now,
        )
        _attach_leave_clip(row, clothes=clothes, hair=hair, sex=sex, leave_match=lm)
        visits[pid] = row
        if present or pid in (getattr(inventory, "sticky", set()) or set()):
            inventory.force_leave(
                [pid], source=leave_src, leave_match=lm
            )
        try:
            from .oracle_event import record_leave

            record_leave(eid, pid, now=now, leave_match=lm)
        except Exception:
            pass

    if applied_as != "exit":
        _stamp_traits(
            inventory,
            pid,
            clothes=clothes,
            hair=hair,
            sex=sex,
            now=now,
            event=applied_as,
        )
    hc = int(inventory.headcount())
    out.update(
        ok=True,
        applied=True,
        event=applied_as,
        headcount_after=hc,
        slot=visits.get(pid),
        leave_match=lm if applied_as == "exit" else None,
    )
    return out


def _attach_leave_clip(
    row: dict[str, Any],
    *,
    clothes: str | None,
    hair: str | None,
    sex: str | None,
    leave_match: dict[str, Any] | None,
) -> None:
    """Leave clip sits beside the enter card — it must not replace robe/hair."""
    if clothes:
        row["leave_clothes"] = str(clothes)[:240]
    if hair:
        row["leave_hair"] = str(hair)[:120]
    if sex in ("M", "F", "m", "f"):
        row["leave_sex"] = str(sex).upper()
    if leave_match:
        row["leave_match"] = dict(leave_match)


def _merge_row(
    prior: dict[str, Any] | None,
    *,
    event: str,
    pid: str,
    now: float,
    clothes: str | None,
    hair: str | None,
    sex: str | None,
    faceid: dict[str, Any] | None,
    pack_id: str | None,
    note: str | None,
    enter_ts: float | None,
    exit_ts: float | None,
) -> dict[str, Any]:
    row = dict(prior or {})
    row["person_id"] = pid
    row["short_id"] = short_id(pid)
    row["event"] = event
    row["enter_ts"] = enter_ts
    row["exit_ts"] = exit_ts
    if event == "enter":
        if clothes:
            row["clothes"] = str(clothes)[:240]
        if hair:
            row["hair"] = str(hair)[:120]
        if sex in ("M", "F", "m", "f"):
            row["sex"] = str(sex).upper()
    else:
        # Sighting/exit must not replace OR first-fill hair/sex (05:48 already
        # present painted a F robe onto the user). Clothes stay write-once: fill
        # only if the enter card is still empty (guest first look).
        if clothes and not row.get("clothes"):
            row["clothes"] = str(clothes)[:240]
    if isinstance(faceid, dict) and faceid:
        row["faceid"] = dict(faceid)
    row["argus"] = {"kind": event, "ts": now, "pack": pack_id or ""}
    if note:
        row["note"] = str(note)[:240]
    return row


def _stamp_traits(
    inventory: Any,
    pid: str,
    *,
    clothes: str | None,
    hair: str | None,
    sex: str | None,
    now: float,
    event: str = "",
) -> None:
    tl = dict((getattr(inventory, "traits_live", {}) or {}).get(pid) or {})
    ev = str(event or "").strip().lower()
    if ev == "enter":
        if clothes:
            tl["clothes"] = str(clothes)[:240]
        if hair:
            tl["hair"] = str(hair)[:120]
        if sex in ("M", "F", "m", "f"):
            g = str(sex).upper()
            tl["sex"] = g
            if hasattr(inventory, "note_gender"):
                inventory.note_gender(pid, g)
            elif hasattr(inventory, "gender") and isinstance(inventory.gender, dict):
                inventory.gender[pid] = g
    elif clothes and not tl.get("clothes"):
        tl["clothes"] = str(clothes)[:240]
    if tl.get("clothes"):
        tl["summary"] = tl["clothes"]
    if tl:
        tl["source"] = "oracle_visit"
        tl["ts"] = now
        if not hasattr(inventory, "traits_live") or inventory.traits_live is None:
            inventory.traits_live = {}
        inventory.traits_live[pid] = tl
