"""Oracle hand ↔ DoorInventory membership (the User 2026-08-10).

Law: clothing cards in oracle_hand.json must equal **named people in inventory**
at all times. Inventory is SoT for who is here; the hand is only wardrobe
refs for Who Exit? — not a second headcount.

Careful (this is where we mess other stuff up):
  * Only **named** roster keys (the user, another person, …). Never invent U*/unknown.
  * **Purge** cards for people no longer in inventory (object or User leave).
  * **Add** only provisional seed cards when inventory has a name and an
    entry photo already exists on disk — never mint a blank identity.
  * Do **not** delete hand_cards/*.jpg files (keep last clothing for re-deal).
  * Do **not** write inventory, presence, or facefinder from this module.
  * File lock + atomic replace only.

Called after inventory membership changes (assert_leave / assert_present /
VL leave) and from door-stamp when serving the hand.
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_REPO = _paths.ROOT
OCC = Path(
    os.environ.get("RAIL_OCCUPANT_CACHE", str(_REPO / "data" / "occupants"))
)
HAND_PATH = Path(
    os.environ.get("DOOR_STAMP_ORACLE_HAND", str(OCC / "oracle_hand.json"))
)
HAND_DIR = Path(
    os.environ.get("DOOR_STAMP_ORACLE_HAND_DIR", str(OCC / "hand_cards"))
)
_lock = threading.RLock()

# Pseudo-names that must never become clothing cards
_SKIP = frozenset(
    {
        "",
        "*",
        "all",
        "empty",
        "unknown",
        "crowd_noise",
        "nobody",
        "guest",
    }
)


def _is_named_person(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n or n in _SKIP:
        return False
    if n.startswith("__"):
        return False
    if n.startswith("u") and n[1:].isdigit():
        return False
    if n.startswith("unknown"):
        return False
    return True


def _normalize_names(names: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        n = str(raw or "").strip().lower()
        if not _is_named_person(n) or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _load_hand() -> dict[str, Any]:
    if HAND_PATH.is_file():
        try:
            d = json.loads(HAND_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("cards", [])
                return d
        except Exception:
            pass
    return {
        "schema": "oracle_hand/v1",
        "law": "cards == inventory named people",
        "cards": [],
    }


def _save_hand(hand: dict[str, Any]) -> None:
    HAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    hand["ts"] = time.time()
    hand["ts_local"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = HAND_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(hand, indent=2), encoding="utf-8")
    tmp.replace(HAND_PATH)


def _photo_for(slug: str) -> Path | None:
    for p in (
        HAND_DIR / f"{slug}.jpg",
        OCC / f"{slug}_entry.jpg",
    ):
        if p.is_file():
            return p
    return None


def sync_hand_to_inventory(
    present_names: list[str] | set[str] | tuple[str, ...],
    *,
    reason: str = "inventory_sync",
    seed_missing: bool = True,
) -> dict[str, Any]:
    """Make oracle hand cards exactly the named people in inventory.

    present_names: inventory.names() or presence people names.
    seed_missing: if True, add provisional seed cards when photo exists.
    """
    want = _normalize_names(present_names)
    want_set = set(want)
    with _lock:
        hand = _load_hand()
        old_cards = list(hand.get("cards") or [])
        by_who: dict[str, dict] = {}
        for c in old_cards:
            if not isinstance(c, dict):
                continue
            w = str(c.get("who") or "").strip().lower()
            if _is_named_person(w):
                by_who[w] = c

        removed = [w for w in by_who if w not in want_set]
        for w in removed:
            by_who.pop(w, None)

        added: list[str] = []
        if seed_missing:
            HAND_DIR.mkdir(parents=True, exist_ok=True)
            for w in want:
                if w in by_who:
                    continue
                src = _photo_for(w)
                if src is None:
                    # Inventory has them but no clothing still — no card yet
                    # (entry deal will add). Do not invent.
                    continue
                dest = HAND_DIR / f"{w}.jpg"
                try:
                    if src.resolve() != dest.resolve():
                        shutil.copy2(src, dest)
                except Exception:
                    dest = src
                by_who[w] = {
                    "token": str(uuid.uuid4()),
                    "who": w,
                    "photo": str(dest),
                    "dealt_event_id": f"sync:{reason}",
                    "dealt_frame": Path(src).name,
                    "dealt_ts": time.time(),
                    "dealt_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "seed": True,
                    "note": f"Synced from inventory ({reason}) — refresh on entry deal",
                }
                added.append(w)

        # Preserve inventory order for stable UI
        cards = [by_who[w] for w in want if w in by_who]
        hand["cards"] = cards
        hand["schema"] = "oracle_hand/v1"
        hand["law"] = (
            "cards == inventory named people (the User 2026-08-10). "
            "Purge on leave/correct; seed only with existing photo."
        )
        hand["sync_reason"] = reason
        hand["sync_present"] = want
        _save_hand(hand)
        return {
            "ok": True,
            "reason": reason,
            "present": want,
            "hand_who": [c.get("who") for c in cards],
            "n": len(cards),
            "removed": removed,
            "added_seed": added,
            "missing_photo": [w for w in want if w not in by_who],
        }


def sync_hand_from_inventory_obj(inv: Any, *, reason: str = "inventory") -> dict[str, Any]:
    """Convenience: DoorInventory or object with .names()."""
    try:
        names = list(inv.names()) if hasattr(inv, "names") else []
    except Exception:
        names = []
    return sync_hand_to_inventory(names, reason=reason)
