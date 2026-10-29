"""Door-hold inventory — arithmetic at the chokepoint, not a room survey.

STONE: count is FACT while camera holds the door.

Enter vs walk-through (the User 2026-07-22, measured):
  She enters at door (0.61) → then walks *through* the door FOV *into the room*.
  That empties the doorway. That is NOT a leave. Leave only after she is already
  counted and later reappears at the door (exit path), then doorway empties.
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# After a NEW enter, ignore door-empty for this long (walk into room).
# the User 2026-08-11: entry grace DELETED (was 45s "walk into room, don't leave").
# Default 0 — leave/exit_arm no longer wait out a post-enter freeze.
# Param kept for call-site compat; non-zero only if env forced for A/B.
DEFAULT_ENTRY_GRACE_SEC = 0.0
# A stale exit-arm decays (disarms, removes nobody) — docs/DESIGN-OCCUPANCY-STREAM.md
DEFAULT_EXIT_ARM_TTL_SEC = 90.0
# One door = one chokepoint: crossings within this window are ONE transit
DEFAULT_TRANSIT_WINDOW_SEC = 12.0
# Law (a) evidence gate (2026-08-04, two phantom "leave the user" on hallway-light
# changes, ev_1785843480017 / ev_1785847541917): an empty-FOV leave confirm
# must have SEEN a body at the door this recently — light is not a transit.
DEFAULT_BODY_WITNESS_SEC = 15.0
# Law (d): the ONLY time-based remover; clock advances on door observations only
DEFAULT_GHOST_RECOVERY_SEC = 1800.0

# User (HOUSE_USER_ID) is not removed by time-empty / arm-empty alone
# (the User 2026-08-11 durable occupancy). Operator force_leave still works.
def _protected_person_ids() -> set[str]:
    raw = (
        os.environ.get("HOUSE_USER_ID")
        or os.environ.get("RAIL_PRIMARY_USER")
        or "user"
    )
    out = {str(raw).strip().lower()}
    extra = os.environ.get("RAIL_PRESENCE_PROTECTED_IDS", "")
    for p in extra.split(","):
        p = p.strip().lower()
        if p:
            out.add(p)
    return out


# Sources that may drop a protected User (operator or explicit VL −1).
# Timers (ghost_recovery, door_empty_after) are NOT listed.
# AUTH-first (COMMS 2026-08-12): entry_pic_match / exit_match (ops Oracle who-left)
# is NOT automatic User de-AUTH — wrong who-left would clear sticky all day.
# Opt-in: RAIL_USER_ALLOW_EXIT_MATCH_LEAVE=1 restores old behavior.
_USER_LEAVE_ALLOWED_SOURCES = frozenset(
    {
        "operator",
        "operator_leave",
        "force_leave",
        "leave_package",
        "instruct",
        "assert_leave",
        "gemma",
        "qwen",
        "cosmos",
        "vl",
        "vl_delta",
        "code",
        "argus_exit_confirmed",
        # Argus called exit + robe/hair match (or sole named body). Not a face.
        "argus_clothes_exit",
    }
)


# Every leave event mirrors here before the in-memory trim (stream audit).
SHADOW_HISTORY = _paths.DATA / "results/shadow_history.jsonl"
# Non-leave occupancy events (mints, absorptions, cancels, mode changes) —
# board RISES finally leave a persistent trace (presence-perfect §1.2).
OCCUPANCY_HISTORY = _paths.DATA / "results/occupancy_history.jsonl"
import sys as _sys
_IN_PYTEST = "pytest" in _sys.modules


@dataclass
class DoorInventory:
    """Body inventory maintained while holding the door."""

    present: dict[str, float] = field(default_factory=dict)
    sticky: set[str] = field(default_factory=set)
    seen_at_door: set[str] = field(default_factory=set)
    last_door_seen: dict[str, float] = field(default_factory=dict)
    # last observation with ANY body evidence at the door (name, unnamed, or
    # bodies-in-frame) — law (a) refuses to confirm a leave without it fresh
    last_body_at_door: float = 0.0
    # name -> M|F sticky for roster (do not null just because not in this door frame)
    gender: dict[str, str] = field(default_factory=dict)
    # name -> epoch until which door-empty must NOT mean leave (post-enter walk-in)
    entry_grace_until: dict[str, float] = field(default_factory=dict)
    # may leave when door empties: already-present person re-sighted at door (exit path)
    exit_armed: set[str] = field(default_factory=set)
    # when each arm happened; a stale arm decays (exit_disarmed) after ARM TTL
    armed_at: dict[str, float] = field(default_factory=dict)
    # slots the service holds in doubt (unconfirmed / held −1): ghost-recovery eligible
    doubt_since: dict[str, float] = field(default_factory=dict)
    # object-owned transit ledger — crossing metadata only, never bodies
    recent_transits: list[dict[str, Any]] = field(default_factory=list)
    # Threshold Game: guests are numbers by ARRIVAL ORDER (the User 2026-07-28).
    # The object owns the guestN -> slot mapping; voters never see identity.
    arrival_seq: dict[str, int] = field(default_factory=dict)
    _arrival_counter: int = 0
    # room-clock resting guess per slot (Inspector aims the beam mic here)
    station_guess: dict[str, int] = field(default_factory=dict)
    unnamed: int = 0
    last_door_names: set[str] = field(default_factory=set)
    last_door_unnamed: int = 0
    leave_since: dict[str, float] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    baseline_set: bool = False
    headcount_baseline: int = 0
    # Peak counts live on the object (code), never on the LLM.
    # session_* resets when headcount returns to 0; peak_* is process high-water.
    peak_headcount: int = 0
    peak_door_concurrent: int = 0
    session_peak_headcount: int = 0
    last_session_peak_headcount: int = 0
    peak_ts: float = 0.0
    # Live clothing/height on slots (CONTRACT-CLOTHING-LEAVE) — object owns these.
    # Named keys = person_id; unnamed use "__u0", "__u1", … (oldest first).
    traits_live: dict[str, dict] = field(default_factory=dict)
    # enter_still meta: key -> {paths, ts, source}
    enter_still: dict[str, dict] = field(default_factory=dict)
    # ordered unnamed slot keys matching unnamed count
    unnamed_keys: list[str] = field(default_factory=list)
    # U provenance: mint_ts/source/transit_ts/true_concurrent/corroborations
    unnamed_meta: dict[str, dict] = field(default_factory=dict)
    # Pending crossings (presence-perfect §2.1): mint debounce records for
    # ambiguous door_live rises. Hold NO headcount and NO key — metadata only.
    pending_crossings: list[dict] = field(default_factory=list)
    # Non-writing directional evidence (witnessed VL verdicts etc.) — feeds
    # pending resolution only; never bodies, never counts.
    recent_witnesses: list[dict] = field(default_factory=list)
    # Guest face/voice day-flag — not occupancy (GUEST-VISITOR-TODAY-20260813).
    # id -> {ts, source, day}
    visitor_today: dict[str, dict] = field(default_factory=dict)

    def set_baseline(self, names: list[str], unnamed: int = 0) -> None:
        now = time.time()
        from .guests import is_guest

        occ = [n for n in names if n and not is_guest(n)]
        guests = [n for n in names if n and is_guest(n)]
        self.present = {n: now for n in occ}
        self.arrival_seq = {}
        self._arrival_counter = 0
        for n in occ:
            self._note_arrival(n)
        for g in guests:
            self.note_visitor(g, now=now, source="baseline")
        self.unnamed = max(0, int(unnamed))
        self.last_door_names = set()
        self.last_door_unnamed = 0
        self.leave_since.clear()
        # baseline people are in-room; not armed for exit until they visit the door
        for n in names:
            self.entry_grace_until.pop(n, None)
            self.exit_armed.discard(n)
        self.baseline_set = True
        self.headcount_baseline = len(self.present) + self.unnamed
        self._touch_peaks(now=now)
        self.events.append(
            {
                "kind": "baseline",
                "names": sorted(self.present),
                "unnamed": self.unnamed,
                "headcount": self.headcount(),
                "ts": now,
            }
        )

    def headcount(self) -> int:
        return len(self.present) + max(0, self.unnamed)

    def names(self) -> list[str]:
        return sorted(self.present)

    def _touch_peaks(
        self, *, door_concurrent: int | None = None, now: float | None = None
    ) -> None:
        """Ratchet peak counts from inventory / door sensors — not from VLM text."""
        now = now if now is not None else time.time()
        hc = self.headcount()
        if hc > self.peak_headcount:
            self.peak_headcount = hc
            self.peak_ts = now
        if hc > self.session_peak_headcount:
            self.session_peak_headcount = hc
        if hc == 0 and self.session_peak_headcount > 0:
            self.last_session_peak_headcount = self.session_peak_headcount
            self.session_peak_headcount = 0
        if door_concurrent is not None:
            dc = max(0, int(door_concurrent))
            if dc > self.peak_door_concurrent:
                self.peak_door_concurrent = dc
                self.peak_ts = now

    def peaks_dict(self) -> dict[str, Any]:
        return {
            "headcount": int(self.peak_headcount),
            "session_headcount": int(self.session_peak_headcount),
            "last_session_headcount": int(self.last_session_peak_headcount),
            "door_concurrent": int(self.peak_door_concurrent),
            "ts": self.peak_ts or None,
            "owner": "sot_inventory",
            "note": "peaks ratcheted by code from ±1 apply + door concurrent; not LLM",
        }

    # service wires this to its resurrect-tombstone writer; EVERY leave path
    # goes through _drop, so no path can skip the tombstone again.
    on_leave: Any = None

    def is_protected(self, name: str) -> bool:
        n = (name or "").strip().lower()
        return bool(n) and n in _protected_person_ids()

    def may_drop_protected(self, name: str, *, source: str) -> bool:
        """User leave: strong sources only (AUTH-FIRST 2026-08-12).

        Approach-and-return (re-sight at door then empty FOV / sit at desk)
        must NOT de-AUTH. door_empty_after + exit_armed alone is **blocked**
        for the protected User — that path treated "walk to door and sit"
        as leave. Allow operator / VL / match / force / explicit instruct.
        Opt-in old arm-empty leave: RAIL_USER_ALLOW_ARM_EMPTY_LEAVE=1.
        """
        if not self.is_protected(name):
            return True
        src = (source or "").strip().lower()
        n = (name or "").strip().lower()
        # AUTH-first: arm+empty alone is not enough for sudo User
        if src in ("door_empty_after", "exit_armed"):
            if os.environ.get("RAIL_USER_ALLOW_ARM_EMPTY_LEAVE", "0") in (
                "1",
                "true",
                "yes",
            ):
                return n in self.exit_armed
            # Disarm instead of leave — approach-and-return safe
            self.exit_armed.discard(n)
            self.armed_at.pop(n, None)
            self.leave_since.pop(n, None)
            return False
        # Argus visit exit is a hypothesis until seat+count confirm.
        if src in ("oracle_visit", "argus", "argus_visit"):
            return False
        # Retired who-left names on old events — not automatic User de-AUTH
        if src in ("entry_pic_match", "exit_match", "who_left"):
            if os.environ.get("RAIL_USER_ALLOW_EXIT_MATCH_LEAVE", "0") in (
                "1",
                "true",
                "yes",
            ):
                return True
            return False
        if src in _USER_LEAVE_ALLOWED_SOURCES:
            return True
        if src.startswith("operator"):
            return True
        if src.startswith(("gemma", "qwen", "cosmos", "vl")):
            return True
        # Unit-test / ops shorthand used by apply_vl_delta callers
        if src in ("test", "op", "visual_still", "traits", "sole"):
            return True
        # Explicit opt-in for old janitor behavior
        if os.environ.get("RAIL_USER_ALLOW_GHOST_LEAVE", "0") in (
            "1",
            "true",
            "yes",
        ):
            return True
        return False

    def _drop(self, name: str, *, now: float, source: str) -> dict[str, Any]:
        n = (name or "").strip().lower()
        if n and not self.may_drop_protected(n, source=source):
            ev = {
                "kind": "leave_blocked",
                "name": n,
                "ts": now,
                "source": source,
                "reason": "protected_user_needs_leave_evidence",
            }
            self.events.append(ev)
            self._mirror_event(ev)
            # Reset empty-door clock so we don't spin-block every tick.
            # Do NOT clear exit_armed here — a real arm must survive blocked
            # ghost ticks until an armed empty-door leave succeeds or TTL.
            self.leave_since.pop(n, None)
            if (source or "") == "ghost_recovery":
                pass  # keep arm if any
            elif (source or "") != "door_empty_after":
                self.exit_armed.discard(n)
                self.armed_at.pop(n, None)
            return ev
        self.present.pop(name, None)
        self.sticky.discard(name)
        self.seen_at_door.discard(name)
        self.last_door_seen.pop(name, None)
        self.leave_since.pop(name, None)
        self.entry_grace_until.pop(name, None)
        self.exit_armed.discard(name)
        self.armed_at.pop(name, None)
        self.doubt_since.pop(name, None)
        self.gender.pop(name, None)
        traits = self.traits_live.pop(name, None)
        still = self.enter_still.pop(name, None)
        if name in self.unnamed_keys:
            self.unnamed_keys = [k for k in self.unnamed_keys if k != name]
        self.arrival_seq.pop(name, None)
        self.station_guess.pop(name, None)
        ev = {
            "kind": "leave",
            "name": name,
            "ts": now,
            "source": source,
            "traits_live": traits,
            "had_enter_still": bool(still),
        }
        self.events.append(ev)
        self._mirror_event(ev)
        if callable(self.on_leave):
            try:
                self.on_leave(name, ev)
            except Exception:
                pass
        return ev

    @staticmethod
    def _mirror_event(ev: dict[str, Any], *, history: Path | None = None) -> None:
        """Append occupancy events to persistent history before the trim.
        pytest runs are diverted (they were polluting production history)."""
        if _IN_PYTEST:
            return
        try:
            with (history or SHADOW_HISTORY).open("a") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        except Exception:
            pass

    # ---- transit ledger (one door = one chokepoint; metadata only) ----
    def _note_transit(
        self, direction: str, *, source: str, slot_key: str | None,
        door_concurrent: int = 1, now: float | None = None,
        window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
    ) -> None:
        now = now if now is not None else time.time()
        self.recent_transits.append(
            {"dir": direction, "ts": now, "source": source,
             "slot_key": slot_key, "door_concurrent": max(1, int(door_concurrent))}
        )
        cut = now - max(window_sec, 1.0) * 2
        self.recent_transits = [t for t in self.recent_transits if t["ts"] >= cut][-16:]

    def _sole_fresh_inward(self, name: str, now: float) -> bool:
        """Fresh-entry guard (2026-07-28 15:13 phantom drain): a sole-named
        drain must not fire while the slot's own entry is seconds old — the
        −1 is more likely the tail of the in-crossing (or a trailing pet)
        than a real exit. Fresh = entry grace active OR an enter transit for
        this slot newer than RAIL_PRESENCE_SOLE_DRAIN_MIN_AGE_SEC (default
        30; transits are retained ~2x the transit window, grace covers the
        rest). Never guess someone OUT on evidence seconds stale."""
        # Default OFF (2026-07-28 15:2x): the motivating "phantom" was
        # the User's REAL 13s quick-turnaround exit (traits-confirmed). Quick
        # in-and-out is a real household pattern; no observed false −1 in
        # the fresh window exists yet. Enable only on evidence.
        min_age = float(os.environ.get(
            "RAIL_PRESENCE_SOLE_DRAIN_MIN_AGE_SEC", "0"))
        if min_age <= 0:
            return False
        if now < self.entry_grace_until.get(name, 0.0):
            return True
        return any(
            t.get("dir") == "enter"
            and t.get("slot_key") == name
            and now - float(t.get("ts") or 0) <= min_age
            for t in self.recent_transits
        )

    def _same_transit(
        self, direction: str, *, now: float,
        window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
    ) -> dict[str, Any] | None:
        """A same-direction crossing within the window at door_concurrent<=1 is
        the SAME body seen by another writer — merge, don't mint (bodies>1 guard:
        two genuine simultaneous entrants must not collapse)."""
        for t in reversed(self.recent_transits):
            if (
                t["dir"] == direction
                and now - t["ts"] <= window_sec
                and t.get("door_concurrent", 1) <= 1
            ):
                return t
        return None

    def refresh_meta(
        self, slot_key: str | None, traits: dict | None, *,
        source: str, now: float | None = None,
    ) -> dict[str, Any]:
        """Arithmetic-free landing for a deduped +1's soft hints: touch the
        slot, attach traits. Count unchanged, traits never lost (Phase A)."""
        now = now if now is not None else time.time()
        if slot_key and slot_key in self.present:
            self.present[slot_key] = now
        if slot_key and traits:
            self.set_traits(slot_key, traits, source=source)
        ev = {
            "kind": "transit_dedup_noop",
            "slot_key": slot_key,
            "ts": now,
            "source": source,
            "traits_landed": bool(traits and slot_key),
        }
        self.events.append(ev)
        self._mirror_event(ev, history=OCCUPANCY_HISTORY)
        return ev

    # ---- pending crossings (presence-perfect §2.1) — the ONE rise debounce.
    # A pending holds no headcount and no key; it is crossing metadata that
    # resolves ONLY on observations (stalled grabber freezes it, never expires
    # it by wall clock). The only timer here delays an ADD ≤ window; it can
    # never produce a −1 (cancel decrements nothing — nothing was counted).
    @staticmethod
    def _witness_cancel_enabled() -> bool:
        return os.environ.get(
            "RAIL_PRESENCE_WITNESS_CANCEL", "0"
        ) not in ("0", "false", "no")

    def _room_bodies(self) -> int:
        """Named present + keyed unnamed — people the room already accounts for."""
        return len(self.present) + int(self.unnamed or 0)

    def _fov_explained_by_roster(self, true_concurrent: int) -> bool:
        """True when this FOV body is already on the occupancy roster.

        Guests are not occupancy — their faces never raise this count.
        Sequential guest walk-ins are visitor_today, not a second body.
        """
        try:
            tc = max(0, int(true_concurrent or 0))
        except (TypeError, ValueError):
            tc = 0
        return tc <= self._room_bodies()

    def _prune_visitor_today(self, day: str | None = None) -> None:
        from .guests import visitor_day

        want = day or visitor_day()
        keep = {
            k: v
            for k, v in (self.visitor_today or {}).items()
            if isinstance(v, dict) and v.get("day") == want
        }
        self.visitor_today = keep

    def note_visitor(
        self,
        person_id: str,
        *,
        now: float | None = None,
        source: str = "door_face",
    ) -> dict[str, Any] | None:
        """Remember a known guest face. Does not change headcount."""
        from .guests import is_guest, visitor_day

        n = (person_id or "").strip().lower()
        if not n or not is_guest(n):
            return None
        now = now if now is not None else time.time()
        day = visitor_day(now)
        self._prune_visitor_today(day)
        prev = (self.visitor_today or {}).get(n) or {}
        first = prev.get("day") != day
        self.visitor_today[n] = {"ts": now, "source": source, "day": day}
        if not first:
            return None
        ev = {
            "kind": "visitor",
            "name": n,
            "ts": now,
            "source": source,
            "day": day,
            "first_today": True,
        }
        self.events.append(ev)
        self._mirror_event(ev, history=OCCUPANCY_HISTORY)
        return ev

    def _evict_guests_from_occupancy(self, *, now: float) -> list[dict[str, Any]]:
        """Drop guest ids off inventory without calling leave."""
        from .guests import is_guest

        out: list[dict[str, Any]] = []
        for n in list(self.present):
            if not is_guest(n):
                continue
            self.present.pop(n, None)
            self.sticky.discard(n)
            self.exit_armed.discard(n)
            self.armed_at.pop(n, None)
            self.leave_since.pop(n, None)
            self.entry_grace_until.pop(n, None)
            self.arrival_seq.pop(n, None)
            ev = {
                "kind": "guest_not_occupancy",
                "name": n,
                "ts": now,
                "note": "guests are not occupancy",
            }
            self.events.append(ev)
            self._mirror_event(ev, history=OCCUPANCY_HISTORY)
            out.append(ev)
            self.note_visitor(n, now=now, source="evict_occupancy")
        return out

    def _cancel_pending(
        self,
        p: dict,
        *,
        now: float,
        reason: str,
        extra: dict | None = None,
    ) -> list[dict]:
        tc = int(p.get("true_concurrent") or 1)
        ev = {
            "kind": "crossing_cancelled",
            "reason": reason,
            "ts": now,
            "crossing_ts": p["ts"],
            "true_concurrent": tc,
            "witness_out": p.get("witness_out", 0),
            "witness_in": p.get("witness_in", 0),
        }
        if extra:
            ev.update(extra)
        self.events.append(ev)
        self._mirror_event(ev, history=OCCUPANCY_HISTORY)
        return [ev]

    def _mint_from_pending(
        self, p: dict, *, now: float, corroborations: list | None = None,
        transit_window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
    ) -> dict:
        """Keyed U from a resolved crossing — mint_ts/grace backdated to the
        crossing ts (entry grace 45s > window 12s, so grace never pre-expires
        at default config; do not set TRANSIT_WINDOW > entry grace)."""
        uk = self._new_unnamed_key(
            source="door_live", now=p["ts"],
            true_concurrent=p.get("true_concurrent", 1),
            transit_ts=p["ts"], corroborations=corroborations,
        )
        self.unnamed += 1
        self.entry_grace_until[uk] = p["ts"] + float(
            p.get("grace_sec") or DEFAULT_ENTRY_GRACE_SEC
        )
        self._note_transit(
            "enter", source="door_live", slot_key=uk,
            door_concurrent=p.get("true_concurrent", 1), now=now,
            window_sec=transit_window_sec,
        )
        ev = {
            "kind": "enter", "name": None, "unnamed_key": uk,
            "unnamed_delta": 1, "ts": now, "source": "door_live",
            "crossing_ts": p["ts"],
            "corroborations": list(corroborations or []),
        }
        self.events.append(ev)
        self._touch_peaks(now=now)
        return ev

    def _settle_pending(
        self, p: dict, *, now: float,
        transit_window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
        allow_expiry: bool = True,
    ) -> list[dict] | None:
        """Evaluate one pending. Returns events if resolved, None if still
        pending. Headcount deltas per outcome: cancel 0, merge 0, mint +1."""
        w = transit_window_sec
        tc = int(p.get("true_concurrent") or 1)
        # Room already has enough people to explain this FOV count — never +1.
        if self._fov_explained_by_roster(tc):
            out = self._cancel_pending(
                p, now=now, reason="explained_by_roster",
                extra={"room_bodies": self._room_bodies()},
            )
            if self.present:
                n = max(self.present.items(), key=lambda kv: float(kv[1]))[0]
                out.append(
                    self.refresh_meta(
                        n, None, source="pending_roster_resight", now=now
                    )
                )
            return out
        # cancel (nobody): outbound transit covers the crossing at true
        # concurrency ≤ 1 — the rise was a back going OUT (§2.2)
        if tc <= 1:
            for t in reversed(self.recent_transits):
                if (
                    t["dir"] == "leave"
                    and abs(t["ts"] - p["ts"]) <= w
                    and t.get("door_concurrent", 1) <= 1
                ):
                    return self._cancel_pending(
                        p, now=now, reason="outbound_resolved",
                        extra={"outbound_slot": t.get("slot_key")},
                    )
            # cancel by unanimous outbound witnesses (flagged rollout knob)
            if (
                self._witness_cancel_enabled()
                and p.get("witness_out", 0) > 0
                and p.get("witness_in", 0) == 0
            ):
                return self._cancel_pending(
                    p, now=now, reason="outbound_witness"
                )
            # merge (same body): another writer's enter transit covers the
            # crossing — refresh that slot instead of minting (§2.1)
            for t in reversed(self.recent_transits):
                if (
                    t["dir"] == "enter"
                    and abs(t["ts"] - p["ts"]) <= w
                    and t.get("door_concurrent", 1) <= 1
                    and t.get("slot_key")
                    and (
                        t["slot_key"] in self.present
                        or t["slot_key"] in self.unnamed_keys
                    )
                ):
                    ev = self.refresh_meta(
                        t["slot_key"], None, source="pending_merge", now=now
                    )
                    return [ev]
        # mint early: enter corroboration from a witnessed VL verdict
        if p.get("witness_in", 0) > 0:
            return [self._mint_from_pending(
                p, now=now, corroborations=["vl"],
                transit_window_sec=transit_window_sec)]
        # mint at expiry — GATED (Grok break-pass D, 2026-07-28): pure FOV
        # energy with ZERO enter corroboration cancels for single-body
        # pendings that are likely desk-edge / solo re-sight WHILE the room
        # already has people. Empty-room walk-ins must mint: oracle witness
        # is often empty (no VL / people=0) and used to cancel every real
        # enter as uncorroborated_expiry (measured 2026-08-11). Multi-body
        # FOV (true_conc>1) is itself concurrency evidence. Room re-sight is
        # already handled above via _fov_explained_by_roster.
        if allow_expiry and now >= float(p.get("expires") or 0):
            require_corr = os.environ.get(
                "RAIL_PRESENCE_PENDING_MINT_REQUIRES_CORROBORATION", "1"
            ) not in ("0", "false", "no")
            multi_body = tc > 1
            empty_room = self._room_bodies() == 0
            if (
                require_corr
                and not p.get("witness_in", 0)
                and not multi_body
                and not empty_room
            ):
                return self._cancel_pending(
                    p, now=now, reason="uncorroborated_expiry"
                )
            corr = None
            if empty_room and not p.get("witness_in", 0):
                corr = ["empty_room_door"]
            return [self._mint_from_pending(
                p, now=now, corroborations=corr,
                transit_window_sec=transit_window_sec)]
        return None

    def _resolve_pending(
        self, now: float, *,
        transit_window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
    ) -> list[dict]:
        """Observation-driven resolver — called from observe_door ticks and
        apply_vl_delta/note_witness. Never runs on wall clock alone."""
        if not self.pending_crossings:
            return []
        out: list[dict] = []
        keep: list[dict] = []
        for p in self.pending_crossings:
            evs = self._settle_pending(
                p, now=now, transit_window_sec=transit_window_sec)
            if evs is None:
                keep.append(p)
            else:
                out.extend(evs)
        self.pending_crossings = keep
        return out

    def note_witness(
        self, direction: str, *, ts: float, source: str,
        traits: dict | None = None,
        window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
    ) -> bool:
        """The ONE entry point for non-writing directional evidence (D6).
        Feeds pending-crossing resolution, raises doubt, lands traits.
        Arithmetic-free: never touches present/unnamed counts."""
        d = "leave" if direction == "leave" else "enter"
        self.recent_witnesses.append({"dir": d, "ts": ts, "source": source})
        cut = ts - max(window_sec, 1.0) * 2
        self.recent_witnesses = [
            w for w in self.recent_witnesses if w["ts"] >= cut
        ][-16:]
        covered = False
        for p in self.pending_crossings:
            if abs(p["ts"] - ts) <= window_sec:
                p["witness_out" if d == "leave" else "witness_in"] += 1
                covered = True
        if d == "leave" and not covered:
            # no pending and no matching leave transit → doubt only (arms
            # ghost recovery + look request); removes NOBODY (law 4)
            if self._same_transit("leave", now=ts, window_sec=window_sec) is None:
                target = None
                for uk in reversed(self.unnamed_keys):
                    if ts >= self.entry_grace_until.get(uk, 0.0):
                        target = uk  # newest settled U
                        break
                if target is None and self.present:
                    target = min(
                        self.present,
                        key=lambda n: float(
                            self.last_door_seen.get(n)
                            or self.present.get(n) or 0
                        ),
                    )
                if target:
                    self.raise_doubt(target, now=ts, reason="witness_out_unmatched")
        # a witness IS an observation — advance pending resolution now
        # (before traits landing so a witness_in early-mint can receive them)
        self._resolve_pending(ts, transit_window_sec=window_sec)
        if traits:
            # D7 fallback: land soft hints on the newest in-grace U
            for uk in reversed(self.unnamed_keys):
                if ts < self.entry_grace_until.get(uk, 0.0):
                    self.refresh_meta(
                        uk, traits, source=f"{source}_witness", now=ts)
                    break
        return covered

    def absorbable_u(
        self, now: float, *,
        window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
    ) -> str | None:
        """§2.1 proof gate: newest __uN that is PROVABLY the same body as a
        same-transit named enter. Missing unnamed_meta ⇒ NOT absorbable
        (fail toward overcount — 1.1 genuinely bounds it now)."""
        for uk in reversed(self.unnamed_keys):
            meta = self.unnamed_meta.get(uk)
            if not meta:
                continue
            fresh = (
                now - float(meta.get("mint_ts") or 0) <= window_sec
                or now < self.entry_grace_until.get(uk, 0.0)
            )
            if not fresh:
                continue
            if int(meta.get("true_concurrent") or 1) > 1:
                continue
            if "vl_second_body" in (meta.get("corroborations") or []):
                continue
            return uk
        return None

    def _drop_unnamed_key(self, uk: str, *, now: float, source: str,
                          reason: str = "", extra: dict | None = None,
                          kind: str | None = None,
                          transfer_to: str | None = None) -> dict:
        """The ONE way a __uN slot leaves the inventory (presence-perfect
        §1.4) — restores unnamed == len(unnamed_keys) at every removal.

        transfer_to: same-body merge target (absorption) — traits/stills move
        via transfer_slot_meta BEFORE the maps are cleared (law 5)."""
        if uk in self.unnamed_keys:
            self.unnamed_keys.remove(uk)
            if self.unnamed > 0:
                self.unnamed -= 1
        else:
            self._mirror_event({"kind": "invariant_breach", "ts": now,
                                "unnamed_key": uk, "unnamed": self.unnamed,
                                "keys": list(self.unnamed_keys)},
                               history=OCCUPANCY_HISTORY)
        if transfer_to:
            # uk already out of unnamed_keys → pure metadata move, no rename
            self.transfer_slot_meta(uk, transfer_to)
        for d in (self.traits_live, self.enter_still, self.entry_grace_until,
                  self.leave_since, self.arrival_seq, self.doubt_since,
                  self.unnamed_meta, self.station_guess):
            d.pop(uk, None)
        ev = {"kind": kind or ("leave" if source != "u_absorbed" else "u_absorbed"),
              "name": None, "unnamed_key": uk, "unnamed_delta": -1,
              "ts": now, "source": source, "reason": reason,
              "transfer_to": transfer_to}
        if extra:
            ev.update(extra)
        self.events.append(ev)
        self._mirror_event(ev, history=OCCUPANCY_HISTORY)
        return ev

    def raise_doubt(self, name: str, *, now: float | None = None, reason: str = "") -> None:
        """Doubt never removes — it demotes trust and arms ghost recovery."""
        now = now if now is not None else time.time()
        if name and name not in self.doubt_since:
            self.doubt_since[name] = now
            self.events.append(
                {"kind": "doubt_raised", "name": name, "ts": now, "reason": reason}
            )

    def clear_doubt(self, name: str) -> None:
        if name in self.doubt_since:
            del self.doubt_since[name]
            self.events.append(
                {"kind": "doubt_cleared", "name": name, "ts": time.time()}
            )

    def _note_arrival(self, key: str) -> None:
        if key and key not in self.arrival_seq:
            self._arrival_counter += 1
            self.arrival_seq[key] = self._arrival_counter

    def guest_roster(self) -> list[str]:
        """Current slots in arrival order — guest1 is roster[0]. The ONE
        mapping used by both leave_package and leave_guest resolution."""
        keys = list(self.present.keys()) + list(self.unnamed_keys[: max(0, self.unnamed)])
        return sorted(set(keys), key=lambda k: self.arrival_seq.get(k, 1 << 30))

    def _new_unnamed_key(self, *, source: str = "unknown",
                         now: float | None = None,
                         true_concurrent: int = 1,
                         transit_ts: float | None = None,
                         corroborations: list | None = None) -> str:
        n = 0
        while f"__u{n}" in self.unnamed_keys or f"__u{n}" in self.traits_live:
            n += 1
        k = f"__u{n}"
        self.unnamed_keys.append(k)
        self._note_arrival(k)
        now = now if now is not None else time.time()
        self.unnamed_meta[k] = {
            "mint_ts": now, "source": source,
            "transit_ts": transit_ts if transit_ts is not None else now,
            "true_concurrent": max(1, int(true_concurrent)),
            "corroborations": list(corroborations or []),
        }
        self._mirror_event({"kind": "u_mint", "unnamed_key": k, "ts": now,
                            "source": source,
                            "true_concurrent": max(1, int(true_concurrent))},
                           history=OCCUPANCY_HISTORY)
        return k

    def set_traits(self, key: str, traits: dict | None, *, source: str = "vl") -> None:
        if not key or not traits:
            return
        prev = dict(self.traits_live.get(key) or {})
        prev.update(traits)
        prev["source"] = source
        prev["ts"] = time.time()
        self.traits_live[key] = prev

    def transfer_slot_meta(self, old_key: str, new_key: str) -> None:
        """When soft_id / face names an unnamed body, move traits+stills."""
        if not old_key or not new_key or old_key == new_key:
            return
        if old_key in self.traits_live:
            self.traits_live[new_key] = self.traits_live.pop(old_key)
        if old_key in self.enter_still:
            self.enter_still[new_key] = self.enter_still.pop(old_key)
        if old_key in self.unnamed_keys:
            self.unnamed_keys = [
                new_key if k == old_key else k for k in self.unnamed_keys
            ]
        old_seq = self.arrival_seq.pop(old_key, None)
        if old_seq is not None:
            # same body: the name inherits the U-slot's (older) arrival
            self.arrival_seq[new_key] = min(
                self.arrival_seq.get(new_key, old_seq), old_seq)

    def note_gender(self, name: str, g: str | None) -> None:
        """Remember M/F for roster while person is present. Live face may update."""
        if not name or g not in ("M", "F"):
            return
        self.gender[name] = g

    def apply_reconcile(
        self,
        names: list[str],
        unnamed: int = 0,
        *,
        mode: str = "replace",
        full: bool = False,
    ) -> dict[str, Any]:
        """Merge seat/face evidence into present.

        mode:
          replace — present becomes (names ∪ sticky); unseen names are *left*
            (legacy full reinit). Dangerous on partial seat tours.
          union — only ADD camera-found names; never drop a named occupant
            without a leave path (the User 2026-08-11: body_count found the User and
            wiped another person still in the room).

        full — this was a COMPLETE tour: every station, facefinder on. Only
          then may `unnamed` come DOWN.

          Without it, union's floor made the error-correct unable to correct.
          the User 2026-08-17: "it keeps doing the room check with FF at every stop
          but it does not correct the count on one body discovered." The tour
          visited every seat, found one body, passed unnamed=0 — and
          max(current, 0) held the count at 5, so the count stayed wrong, the
          error-correct re-armed, and the camera never got back to the door.
          A 90s lease every 90s: 37 triggers, 1 event fired.

          Named occupants keep union's protection either way — a full tour may
          lower the UNNAMED floor only, so it can never wipe a another person who is
          sitting out of shot. Phantoms are unnamed by construction.

          The shrink goes through _drop_unnamed_key so unnamed_keys shrinks
          with it. Setting self.unnamed directly is what persisted
          `unnamed: 0` beside five live keys into inventory_snapshot.json,
          and load_into_inventory does `unnamed = len(unnamed_keys)` — which
          resurrected all five on the next restart.
        """
        now = time.time()
        old = set(self.present)
        found = {str(n).strip().lower() for n in (names or []) if n}
        mode_l = (mode or "replace").strip().lower()
        if mode_l in ("union", "add", "merge", "body_count"):
            new = set(old) | found | set(self.sticky)
            left: list[str] = []
        else:
            new = found | set(self.sticky)
            # Never replace-drop protected User (partial scan ≠ leave)
            for p in _protected_person_ids():
                if p in old:
                    new.add(p)
            left = sorted(old - new)
        entered = sorted(new - old)
        self.present = {n: now for n in new}
        for n in new:
            self._note_arrival(n)
        if mode_l in ("union", "add", "merge", "body_count"):
            want_u = max(0, int(unnamed))
            have_u = int(getattr(self, "unnamed", 0) or 0)
            if full and want_u < have_u:
                # A complete tour saw the whole room. Retire the surplus
                # phantoms newest-first, through the one choke point that
                # keeps unnamed == len(unnamed_keys).
                for uk in list(reversed(self.unnamed_keys))[: have_u - want_u]:
                    self._drop_unnamed_key(
                        uk, now=now, source="room_check_full",
                        reason="full_tour_found_fewer_bodies",
                        extra={"found_unnamed": want_u, "had_unnamed": have_u},
                    )
                self.unnamed = min(self.unnamed, want_u)
            else:
                # Partial scan: raise the floor only, never shrink.
                self.unnamed = max(have_u, want_u)
        else:
            self.unnamed = max(0, int(unnamed))
        self.leave_since.clear()
        self.baseline_set = True
        for n in found:
            self.present[n] = now
        for n in left:
            self.entry_grace_until.pop(n, None)
            self.exit_armed.discard(n)
            self.seen_at_door.discard(n)
        ev = {
            "kind": "reconcile",
            "mode": mode_l,
            "entered": entered,
            "left": left,
            "names": sorted(self.present),
            "sticky": sorted(self.sticky),
            "unnamed": self.unnamed,
            "headcount": self.headcount(),
            "ts": now,
        }
        self.events.append(ev)
        self._touch_peaks(now=now)
        return ev

    def mark_sticky(self, names: list[str], *, source: str = "voice") -> None:
        from .guests import is_guest

        now = time.time()
        for n in names:
            n = n.strip().lower()
            if not n:
                continue
            if is_guest(n):
                self.note_visitor(n, now=now, source=f"sticky:{source}")
                continue
            self.sticky.add(n)
            self.present[n] = now
            self._note_arrival(n)
        self.baseline_set = True
        self._touch_peaks(now=now)

    def force_leave(
        self,
        names: list[str],
        *,
        source: str = "operator",
        leave_match: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Explicit leave — always allowed for protected User (operator/API)."""
        now = time.time()
        out: list[dict[str, Any]] = []
        src = (source or "operator").strip().lower()
        # Do not launder Argus prose into operator_* — that skipped AUTH-first.
        if src in ("oracle_visit", "argus", "argus_visit"):
            pass
        elif not src.startswith("operator") and src not in _USER_LEAVE_ALLOWED_SOURCES:
            src = f"operator_{src}" if src else "operator"
        for n in names:
            n = (n or "").strip().lower()
            if not n:
                continue
            if n in self.present or n in self.sticky:
                ev = self._drop(n, now=now, source=src)
                if leave_match:
                    ev["leave_match"] = {
                        **dict(leave_match),
                        "person_id": leave_match.get("person_id") or n,
                    }
                out.append(ev)
        self._touch_peaks(now=now)
        return out

    def apply_vl_delta(
        self,
        entered: int,
        left: int,
        *,
        source: str = "gemma",
        stamp_names: list[str] | None = None,
        note: str | None = None,
        entry_grace_sec: float = DEFAULT_ENTRY_GRACE_SEC,
        now: float | None = None,
        enter_traits: dict | None = None,
        leave_traits: dict | None = None,
        leave_person_id: str | None = None,
        leave_match_conf: float | None = None,
        transit_window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
        protected_names: set[str] | None = None,
        force: bool = False,
        unnamed_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Apply VL count physics {+entered, −left} into this inventory (SoT path).

        Named stamp_names (FaceID after count) fill enters first; remainder
        becomes unnamed bodies. Leave pick order (CONTRACT-CLOTHING-LEAVE):
        sole → leave_person_id if present → traits score → unnamed/exit_armed/oldest.
        unnamed_only (subs_only writer mode, presence-perfect §3.6): a leave may
        drain __uN slots only — the named roster is structurally unreachable.
        """
        from .traits_leave import best_traits_match, normalize_traits

        now = now if now is not None else time.time()
        new_events_pre = self._resolve_pending(
            now, transit_window_sec=transit_window_sec)
        entered = max(0, int(entered or 0))
        left = max(0, int(left or 0))
        from .guests import is_guest

        stamps = [
            n.strip().lower()
            for n in (stamp_names or [])
            if n and str(n).strip() and not str(n).startswith("unknown")
        ]
        et = normalize_traits(enter_traits)
        lt = normalize_traits(leave_traits)
        leave_pid = (leave_person_id or "").strip().lower() or None
        if leave_pid and leave_pid.startswith("unknown"):
            leave_pid = None
        new_events: list[dict[str, Any]] = list(new_events_pre)
        guest_stamps = [n for n in stamps if is_guest(n)]
        stamps = [n for n in stamps if not is_guest(n)]
        for g in guest_stamps:
            ev = self.note_visitor(g, now=now, source=f"vl_stamp:{source}")
            if ev:
                new_events.append(ev)
        if leave_pid and is_guest(leave_pid):
            # Never call leave on a guest. Evict if they wrongly sit on inventory.
            new_events.extend(self._evict_guests_from_occupancy(now=now))
            leave_pid = None
        grace = float(entry_grace_sec)
        enter_keys: list[str] = []

        for n in stamps:
            if entered <= 0:
                break
            if n in self.present:
                self.present[n] = now
                # door-crossing VL still counts as door sighting (sole_empty path)
                self.seen_at_door.add(n)
                self.last_door_seen[n] = now
                enter_keys.append(n)
                if et:
                    self.set_traits(n, et, source=source)
                continue
            self.present[n] = now
            self.entry_grace_until[n] = now + grace
            self.exit_armed.discard(n)
            self._note_arrival(n)
            # VL enter at the chokepoint = seen at door (enables sole_empty leave)
            self.seen_at_door.add(n)
            self.last_door_seen[n] = now
            # Prefer absorbing a live unnamed VL body (with traits/stills)
            # over stacking the user + U1 — gated by the §2.1 same-body proof
            # (presence-perfect §2.2): settled guests are never eaten.
            uk_abs = self.absorbable_u(now, window_sec=transit_window_sec)
            if uk_abs is not None:
                self._drop_unnamed_key(
                    uk_abs, now=now, source=source, kind="u_absorbed",
                    reason="same_transit_vl_stamp", transfer_to=n)
            if self.unnamed != len(self.unnamed_keys):
                self._mirror_event(
                    {"kind": "invariant_breach", "ts": now,
                     "unnamed": self.unnamed,
                     "keys": list(self.unnamed_keys), "site": "vl_stamp_enter"},
                    history=OCCUPANCY_HISTORY)
            if et:
                self.set_traits(n, et, source=source)
            enter_keys.append(n)
            self._note_transit("enter", source=source, slot_key=n, now=now,
                              window_sec=transit_window_sec)
            # VL+face path: soft until face certain elsewhere; mark sticky optional
            ev = {
                "kind": "enter",
                "name": n,
                "ts": now,
                "source": source,
                "entry_grace_sec": grace,
                "note": note,
                "traits_live": dict(self.traits_live.get(n) or {}),
            }
            self.events.append(ev)
            new_events.append(ev)
            entered -= 1

        if entered > 0:
            # Transit ledger (docs/DESIGN-OCCUPANCY-STREAM.md §5): one door =
            # one chokepoint. A same-direction crossing within the window at
            # door_concurrent<=1 is the SAME body another writer already
            # counted — merge soft hints onto that slot instead of minting.
            # Replaces the three sole-shape heuristics; unifies with VL_DEDUP.
            t = None if force else self._same_transit(
                "enter", now=now, window_sec=transit_window_sec)
            if t is not None and entered == 1 and not stamps:
                slot = t.get("slot_key")
                if slot and (slot in self.present or slot in self.unnamed_keys):
                    ev = self.refresh_meta(slot, et, source=source, now=now)
                    ev["note"] = note
                    new_events.append(ev)
                    enter_keys.append(slot)
                    entered = 0

        if entered > 0:
            for _ in range(entered):
                uk = self._new_unnamed_key(source=source, now=now)
                self.unnamed = max(0, self.unnamed) + 1
                # unnamed slots are first-class: same grace shield as named
                self.entry_grace_until[uk] = now + grace
                if et:
                    self.set_traits(uk, et, source=source)
                enter_keys.append(uk)
                self._note_transit("enter", source=source, slot_key=uk, now=now,
                                  window_sec=transit_window_sec)
                ev = {
                    "kind": "enter",
                    "name": None,
                    "unnamed_key": uk,
                    "unnamed_delta": 1,
                    "ts": now,
                    "source": source,
                    "note": note,
                    "traits_live": dict(self.traits_live.get(uk) or {}),
                }
                self.events.append(ev)
                new_events.append(ev)

        for _ in range(left):
            leave_match: dict[str, Any] = {"source": source}
            cand: str | None = None
            method = "fallback"

            if unnamed_only:
                # subs_only (§3.6) ladder:
                #   1) guest-resolved named leave_pid (object mapped guestN)
                #   2) drain U slots
                #   3) sole named (Finding B)
                #   4) hold multi-named anonymous −1
                # Env RAIL_PRESENCE_SUBS_SOLE_NAMED=0 / SUBS_GUEST_NAMED=0
                # restore pure hold-on-named.
                import os as _os
                sole_named_ok = _os.environ.get(
                    "RAIL_PRESENCE_SUBS_SOLE_NAMED", "1"
                ).strip().lower() not in ("0", "false", "no", "off")
                guest_named_ok = _os.environ.get(
                    "RAIL_PRESENCE_SUBS_GUEST_NAMED", "1"
                ).strip().lower() not in ("0", "false", "no", "off")
                if (
                    guest_named_ok
                    and leave_pid
                    and leave_pid in self.present
                    and not leave_pid.startswith("__u")
                ):
                    cand = leave_pid
                    method = "subs_only_guest_named"
                elif self.unnamed > 0 and self.unnamed_keys:
                    # U drain: honor leave_pid only if it is a U key
                    if leave_pid and leave_pid in self.unnamed_keys:
                        cand = leave_pid
                    elif lt:
                        pool = {
                            uk: self.traits_live[uk]
                            for uk in self.unnamed_keys
                            if uk in self.traits_live
                        }
                        bid, sc = best_traits_match(lt, pool)
                        if bid:
                            cand = bid
                            leave_match["score"] = sc
                    if cand is None:
                        cand = self.unnamed_keys[-1]
                    method = "subs_only_unnamed"
                elif (
                    sole_named_ok
                    and self.unnamed == 0
                    and not self.unnamed_keys
                    and len(self.present) == 1
                    and self.headcount() == 1
                    and not self._sole_fresh_inward(
                        next(iter(self.present)), now)
                ):
                    cand = next(iter(self.present))
                    method = "subs_only_sole_named"
                else:
                    # HOLD: multi-named / no drainable U — doubt LRU, remove nobody
                    lru = None
                    if self.present:
                        lru = min(
                            self.present,
                            key=lambda n: float(
                                self.last_door_seen.get(n)
                                or self.present.get(n) or 0
                            ),
                        )
                        self.raise_doubt(lru, now=now,
                                         reason="subs_only_no_unnamed")
                    ev = {
                        "kind": "vl_leave_held",
                        "ts": now,
                        "source": source,
                        "note": note,
                        "doubt_on": lru,
                        "leave_match": {
                            "method": "subs_only_no_unnamed",
                            "person_id": None,
                            "leave_traits": lt or None,
                        },
                    }
                    self.events.append(ev)
                    new_events.append(ev)
                    continue

            # 1) sole occupant (one named, or one unnamed, or one named+0 unnamed)
            hc = self.headcount()
            if cand is None and hc == 1:
                if len(self.present) == 1:
                    cand = next(iter(self.present))
                    method = "sole"
                elif self.unnamed >= 1 and self.unnamed_keys:
                    cand = self.unnamed_keys[-1]
                    method = "sole"
                elif self.unnamed >= 1:
                    method = "sole"
                    self.unnamed -= 1
                    ev = {
                        "kind": "leave",
                        "name": None,
                        "unnamed_delta": -1,
                        "ts": now,
                        "source": source,
                        "note": note,
                        "leave_match": {"method": "sole", "person_id": None},
                    }
                    self.events.append(ev)
                    new_events.append(ev)
                    continue

            # 2) VL soft leave_person_id if on SoT
            if cand is None and leave_pid and (
                    leave_pid in self.present or leave_pid in self.unnamed_keys):
                cand = leave_pid
                method = "vl_soft"
                leave_match["conf"] = leave_match_conf

            # 3) traits score among named + unnamed keys with traits
            if cand is None and lt:
                pool: dict[str, dict] = {
                    n: self.traits_live[n]
                    for n in self.present
                    if n in self.traits_live
                }
                for uk in self.unnamed_keys:
                    if uk in self.traits_live:
                        pool[uk] = self.traits_live[uk]
                bid, sc = best_traits_match(lt, pool)
                if bid:
                    cand = bid
                    method = "traits"
                    leave_match["score"] = sc

            # 4) fallback: unnamed first, then exit_armed, then oldest present
            if cand is None:
                if self.unnamed > 0 and self.unnamed_keys:
                    cand = self.unnamed_keys[-1]
                    method = "fallback_unnamed"
                elif self.unnamed > 0:
                    self.unnamed -= 1
                    while len(self.unnamed_keys) > self.unnamed:
                        uk = self.unnamed_keys.pop()
                        self.traits_live.pop(uk, None)
                        self.enter_still.pop(uk, None)
                    ev = {
                        "kind": "leave",
                        "name": None,
                        "unnamed_delta": -1,
                        "ts": now,
                        "source": source,
                        "note": note,
                        "leave_match": {"method": "fallback_unnamed", "person_id": None},
                    }
                    self.events.append(ev)
                    new_events.append(ev)
                    continue
                else:
                    armed = [
                        n for n in self.present
                        if n in self.exit_armed
                        and now - self.armed_at.get(n, now) <= DEFAULT_EXIT_ARM_TTL_SEC
                    ]
                    protected = protected_names or set()
                    unprotected = [n for n in self.present if n not in protected]
                    if armed:
                        cand = min(armed, key=lambda n: float(self.present.get(n) or 0))
                        method = "fallback_exit_armed"
                    elif unprotected:
                        # least-recently-SIGHTED non-certain named (LRU, not
                        # oldest-entered) — certain people are never evicted
                        # by an unmatched −1 (docs/DESIGN-OCCUPANCY-STREAM.md §6)
                        cand = min(
                            unprotected,
                            key=lambda n: float(
                                self.last_door_seen.get(n)
                                or self.present.get(n) or 0
                            ),
                        )
                        method = "fallback_lru_unconfirmed"
                    elif self.present:
                        # HOLD: only id_certain people remain. One hallucinated
                        # −1 can never delete a face-certain seated person.
                        lru = min(
                            self.present,
                            key=lambda n: float(
                                self.last_door_seen.get(n)
                                or self.present.get(n) or 0
                            ),
                        )
                        self.raise_doubt(lru, now=now, reason="vl_leave_held")
                        ev = {
                            "kind": "vl_leave_held",
                            "ts": now,
                            "source": source,
                            "note": note,
                            "doubt_on": lru,
                            "leave_match": {
                                "method": "held_all_certain",
                                "person_id": None,
                                "leave_traits": lt or None,
                            },
                        }
                        self.events.append(ev)
                        new_events.append(ev)
                        continue

            leave_match["method"] = method
            leave_match["person_id"] = cand
            if lt:
                leave_match["leave_traits"] = lt
            if cand and cand.startswith("__u"):
                leave_match["traits_live"] = self.traits_live.get(cand)
                leave_match["had_enter_still"] = bool(self.enter_still.get(cand))
                ev = self._drop_unnamed_key(
                    cand, now=now, source=source, reason="vl_leave",
                    extra={"note": note, "leave_match": leave_match})
                new_events.append(ev)
                self._note_transit("leave", source=source, slot_key=cand, now=now,
                                  window_sec=transit_window_sec)
            elif cand:
                ev = self._drop(cand, now=now, source=source)
                if note:
                    ev["note"] = note
                ev["leave_match"] = leave_match
                new_events.append(ev)
                self._note_transit("leave", source=source, slot_key=cand, now=now,
                                  window_sec=transit_window_sec)

        self.baseline_set = True
        self._touch_peaks(now=now)
        if len(self.events) > 200:
            self.events = self.events[-100:]
        # tag enter events with keys for still attachment
        for ev in new_events:
            if ev.get("kind") == "enter" and enter_keys:
                ev["enter_slot_keys"] = list(enter_keys)
        return new_events

    def observe_door(
        self,
        door_names: list[str],
        door_unnamed: int = 0,
        *,
        leave_confirm_sec: float = 2.5,
        entry_grace_sec: float = DEFAULT_ENTRY_GRACE_SEC,
        now: float | None = None,
        ghost_recovery_sec: float | None = None,
        exit_arm_ttl_sec: float = DEFAULT_EXIT_ARM_TTL_SEC,
        transit_window_sec: float = DEFAULT_TRANSIT_WINDOW_SEC,
        door_bodies_max: int | None = None,
        body_witness_sec: float = DEFAULT_BODY_WITNESS_SEC,
        unmatched_live: int = 0,
        unmatched_sim_to_present: float | None = None,
    ) -> list[dict[str, Any]]:
        """One door FOV observation (docs/DESIGN-OCCUPANCY-STREAM.md).

        door_bodies_max: true per-frame concurrency from the caller's buffer
        (max bodies in any SINGLE frame) — recorded on transits/pendings so
        cross-frame unions never inflate the merge guard (presence-perfect
        §1.3). None → conservative fallback to the cross-frame union.

        Enter: new face at door → present (entry grace deleted 2026-08-11).
        In doorway: still present; clear leave clock.
        Exit arm: already-present person seen at door again → armed
        (timestamped; stale arms decay after exit_arm_ttl_sec).
        Leave (law a): armed AND door empty for leave_confirm_sec.
        Ghost recovery (law d, the ONLY time-based remover): a doubted or
        sole or unnamed slot whose door-empty observation stream reaches
        ghost_recovery_sec. Clocks advance on observations only — a stalled
        grabber freezes state instead of decaying it.
        """
        now = now if now is not None else time.time()
        if ghost_recovery_sec is None:
            # env compat: same knob the 1800s hotfix shipped under
            ghost_recovery_sec = float(
                os.environ.get(
                    "RAIL_PRESENCE_SOLE_EMPTY_LEAVE_SEC",
                    str(DEFAULT_GHOST_RECOVERY_SEC),
                )
            )
        from .guests import is_guest

        names = {n for n in door_names if n}
        # Guests are not occupancy: record visitor_today, never enter/leave.
        new_events: list[dict[str, Any]] = list(
            self._evict_guests_from_occupancy(now=now)
        )
        guest_at_door = sorted(n for n in names if is_guest(n))
        names = {n for n in names if not is_guest(n)}
        guest_n = len(guest_at_door)
        for g in guest_at_door:
            ev = self.note_visitor(g, now=now, source="door_face")
            if ev:
                new_events.append(ev)
        if guest_n and door_bodies_max is not None:
            try:
                door_bodies_max = max(0, int(door_bodies_max) - guest_n)
            except (TypeError, ValueError):
                door_bodies_max = 0
        if names or door_unnamed > 0 or (door_bodies_max or 0) > 0:
            self.last_body_at_door = now
        door_unnamed = max(0, int(door_unnamed))
        # unmatched_live kwargs kept for call-site compat — they must not
        # mint occupancy (guest census). Door game is nameless VL ±1.
        fov_busy = bool(
            names or door_unnamed > 0 or (door_bodies_max or 0) > 0
        )
        grace = float(entry_grace_sec)
        if door_bodies_max is not None and int(door_bodies_max) > 0:
            true_conc = int(door_bodies_max)
        else:
            true_conc = len(names) + door_unnamed
        # observation-driven pending resolution first (presence-perfect §2.1)
        new_events.extend(
            self._resolve_pending(now, transit_window_sec=transit_window_sec)
        )

        for n in names:
            if n not in self.present:
                # --- NEW ENTER ---
                self.present[n] = now
                self.entry_grace_until[n] = now + grace
                self.exit_armed.discard(n)
                self.armed_at.pop(n, None)
                self.clear_doubt(n)
                self._note_arrival(n)
                self._note_transit("enter", source="door_face", slot_key=n,
                                  door_concurrent=true_conc,
                                  now=now, window_sec=transit_window_sec)
                # Face enter absorbs a same-transit U (U1 + the user → the user only),
                # gated by the §2.1 same-body proof (presence-perfect §2.2):
                # a 20-minute-old guest is never eaten; their stills/traits/
                # arrival_seq stay theirs; roster order unchanged.
                absorbed_u = 0
                absorbed_key = self.absorbable_u(
                    now, window_sec=transit_window_sec)
                if absorbed_key is not None:
                    new_events.append(self._drop_unnamed_key(
                        absorbed_key, now=now, source="door_face",
                        kind="u_absorbed", reason="same_transit",
                        transfer_to=n))
                    absorbed_u = 1
                if self.unnamed != len(self.unnamed_keys):
                    self._mirror_event(
                        {"kind": "invariant_breach", "ts": now,
                         "unnamed": self.unnamed,
                         "keys": list(self.unnamed_keys),
                         "site": "door_face_enter"},
                        history=OCCUPANCY_HISTORY)
                ev = {
                    "kind": "enter",
                    "name": n,
                    "ts": now,
                    "source": "door_face",
                    "entry_grace_sec": grace,
                    "absorbed_unnamed": absorbed_u or None,
                    "absorbed_key": absorbed_key,
                    "traits_live": dict(self.traits_live.get(n) or {}) or None,
                }
                self.events.append(ev)
                new_events.append(ev)
            else:
                # --- already counted ---
                self.present[n] = now
                self.clear_doubt(n)
                # Re-sight at door arms exit (entry grace removed 2026-08-11)
                if n not in self.exit_armed:
                    self.exit_armed.add(n)
                    self.armed_at[n] = now
                    ev = {
                        "kind": "exit_armed",
                        "name": n,
                        "ts": now,
                        "source": "door_face",
                    }
                    self.events.append(ev)
                    new_events.append(ev)
                else:
                    self.armed_at[n] = now  # re-sight refreshes the arm
            self.seen_at_door.add(n)
            self.last_door_seen[n] = now
            self.leave_since.pop(n, None)

        sole = list(self.present.keys())
        sole_ok = (
            len(sole) == 1
            and self.unnamed == 0
            and not names
            and door_unnamed == 0
        )
        fov_empty = not names and door_unnamed == 0

        # Stale arms decay — disarm, remove nobody (approach-and-return safe)
        for n in list(self.exit_armed):
            if now - self.armed_at.get(n, now) > exit_arm_ttl_sec:
                self.exit_armed.discard(n)
                self.armed_at.pop(n, None)
                self.leave_since.pop(n, None)
                ev = {
                    "kind": "exit_disarmed",
                    "name": n,
                    "ts": now,
                    "reason": f"arm_ttl>{int(exit_arm_ttl_sec)}s",
                }
                self.events.append(ev)
                new_events.append(ev)

        for n in list(self.present):
            if n in names:
                continue
            # sticky/baseline never at door: stay — except sole occupant with empty FOV
            if n not in self.seen_at_door and not (sole_ok and n == sole[0]):
                continue
            # entry grace removed — leave clock may run once exit_armed / ghost
            armed = n in self.exit_armed
            ghost_eligible = (sole_ok and n == sole[0]) or n in self.doubt_since
            if not armed and not ghost_eligible:
                self.leave_since.pop(n, None)
                continue
            if not armed and not fov_empty:
                # ghost clock advances only on door-EMPTY observations
                continue
            if n not in self.leave_since:
                self.leave_since[n] = now
                continue
            held = now - self.leave_since[n]
            if armed and held >= leave_confirm_sec and any(
                w.get("dir") == "enter"
                and abs(now - float(w.get("ts") or 0)) <= transit_window_sec
                for w in self.recent_witnesses
            ):
                # 2026-07-28 15:30 group-return eat: observation-clocked
                # confirms evaluate at the NEXT motion tick — which is the
                # return itself. A fresh inward witness on the firing tick
                # means the door provably saw a body come IN: defer this
                # confirm one tick (clock keeps running; a later tick with
                # no inward witness still fires). Outbound same-tick pairing
                # (his own back at the door) carries no enter witness and is
                # untouched.
                continue
            if armed and held >= leave_confirm_sec and (
                now - self.last_body_at_door > body_witness_sec
            ):
                # law (a) evidence gate: nobody was SEEN at the door anywhere
                # near this leave window — hallway light / scene change, not a
                # transit (2026-08-04: two phantom "leave the user", frames show an
                # empty doorway end to end). Disarm, drop nobody.
                self.exit_armed.discard(n)
                self.armed_at.pop(n, None)
                self.leave_since.pop(n, None)
                ev = {
                    "kind": "exit_disarmed",
                    "name": n,
                    "ts": now,
                    "source": "door_empty_after",
                    "reason": f"no_body_witness>{int(body_witness_sec)}s",
                }
                self.events.append(ev)
                new_events.append(ev)
                continue
            if armed and held >= leave_confirm_sec:
                # law (a): the only fast streaming leave
                ev = self._drop(n, now=now, source="door_empty_after")
                ev["leave_match"] = {
                    "method": "exit_armed",
                    "person_id": n,
                    "source": "door_empty_after",
                }
                new_events.append(ev)
                self._note_transit("leave", source="door_face", slot_key=n,
                                  now=now, window_sec=transit_window_sec)
            elif not armed and held >= ghost_recovery_sec:
                # law (d): the ONLY time-based remover — now with two gates
                # (the User 2026-08-04, after another person's exit removed the User):
                # 1) fresh-evidence veto: a name the wider rail has seen
                #    recently (desk face / voice / held session) is NOT a
                #    ghost — reset its clock instead of dropping it.
                cb = getattr(self, "fresh_evidence_cb", None)
                if cb is not None:
                    try:
                        if cb(n):
                            self.leave_since[n] = now
                            ev = {
                                "kind": "ghost_vetoed", "name": n, "ts": now,
                                "reason": "fresh_evidence_outside_door",
                            }
                            self.events.append(ev)
                            new_events.append(ev)
                            continue
                    except Exception:
                        pass
                # 2) multi-occupant ambiguity → the WHO-LEFT question is not
                #    the janitor's to answer. Emit leave_pending; the match
                #    game (entry stills vs exit frames) picks the leaver.
                if len(self.present) > 1:
                    if not any(e.get("kind") == "leave_pending"
                               and now - float(e.get("ts") or 0) < 120
                               for e in self.events[-12:]):
                        doubt_ts = min(
                            [self.doubt_since.get(c) for c in self.present
                             if self.doubt_since.get(c)] or [now])
                        ev = {
                            "kind": "leave_pending", "ts": now,
                            "candidates": sorted(self.present),
                            "doubt_ts": doubt_ts,
                            "reason": ("sole_empty" if sole_ok
                                       else "doubt_matured"),
                        }
                        self.events.append(ev)
                        new_events.append(ev)
                    continue
                ev = self._drop(n, now=now, source="ghost_recovery")
                ev["leave_match"] = {
                    "method": "ghost_recovery",
                    "person_id": n,
                    "source": "ghost_recovery",
                    "reason": "sole_empty" if sole_ok else "doubt_matured",
                }
                new_events.append(ev)

        # Unnamed live count: a RISE at the door is enter evidence — but an
        # ambiguous single-witness rise becomes a PENDING crossing (mint
        # debounce ≤ window) instead of an immediate slot (presence-perfect
        # §2.1, D3). Runs AFTER the leave loop so a same-tick confirmed exit
        # resolves the crossing outbound (no pending, no U). A FALL is
        # telemetry only (door_live_gauge): door-FOV absence is NOT room
        # absence — the old shrink branch silently deleted VL-entered bodies.
        # Guest faces are stripped above — unmatched live must not mint a
        # guest census (GUEST-VISITOR-TODAY-20260813). Nameless +1 is VL.
        covered = self.unnamed + len(self.pending_crossings)
        if door_unnamed > covered:
            rise = door_unnamed - covered
        else:
            rise = 0
        if rise > 0:
            for _ in range(rise):
                # (1) same-transit merge as before — another writer already
                # counted this body (functional after §1.3 concurrency fix)
                t = self._same_transit("enter", now=now,
                                       window_sec=transit_window_sec)
                if t is not None and t.get("slot_key"):
                    ev = self.refresh_meta(t["slot_key"], None,
                                           source="door_live", now=now)
                    new_events.append(ev)
                    continue
                # (1b) FOV concurrency already explained by room roster —
                # re-sight of someone already counted (solo the User → no U1).
                if self._fov_explained_by_roster(true_conc):
                    if self.present:
                        n = max(
                            self.present.items(),
                            key=lambda kv: float(kv[1]),
                        )[0]
                        new_events.append(
                            self.refresh_meta(
                                n, None, source="door_live_resight", now=now
                            )
                        )
                    elif self.unnamed_keys:
                        new_events.append(
                            self.refresh_meta(
                                self.unnamed_keys[-1],
                                None,
                                source="door_live_resight",
                                now=now,
                            )
                        )
                    else:
                        new_events.append(
                            {
                                "kind": "crossing_cancelled",
                                "reason": "explained_by_roster",
                                "ts": now,
                                "crossing_ts": now,
                                "true_concurrent": true_conc,
                                "room_bodies": self._room_bodies(),
                            }
                        )
                    continue
                # (2) outbound transit covers this crossing → the rise was a
                # back going OUT; cancel, never create the pending
                t_out = self._same_transit("leave", now=now,
                                           window_sec=transit_window_sec)
                if t_out is not None and true_conc <= 1:
                    ev = {
                        "kind": "crossing_cancelled",
                        "reason": "outbound_resolved",
                        "ts": now, "crossing_ts": now,
                        "true_concurrent": true_conc,
                        "outbound_slot": t_out.get("slot_key"),
                        "witness_out": 0, "witness_in": 0,
                    }
                    self.events.append(ev)
                    self._mirror_event(ev, history=OCCUPANCY_HISTORY)
                    new_events.append(ev)
                    continue
                # (3) pending crossing — holds NO headcount and NO key
                # Empty room + FOV body: mint immediately. Waiting for a
                # corroboration that never arrives (oracle people=0) left
                # walk-ins as uncorroborated_expiry with headcount stuck at 0.
                p = {
                    "ts": now, "expires": now + transit_window_sec,
                    "true_concurrent": true_conc,
                    "witness_out": 0, "witness_in": 0,
                    "grace_sec": grace,
                }
                for w in self.recent_witnesses:
                    if abs(now - w["ts"]) <= transit_window_sec:
                        p["witness_out" if w["dir"] == "leave"
                          else "witness_in"] += 1
                if self._room_bodies() == 0 and true_conc >= 1:
                    mint_ev = self._mint_from_pending(
                        p,
                        now=now,
                        corroborations=["empty_room_door_immediate"],
                        transit_window_sec=transit_window_sec,
                    )
                    new_events.append(mint_ev)
                    continue
                settled = self._settle_pending(
                    p, now=now, transit_window_sec=transit_window_sec,
                    allow_expiry=False)
                if settled is not None:
                    # witness-cancelled or corroborated at birth
                    new_events.extend(settled)
                    continue
                self.pending_crossings.append(p)
                ev = {
                    "kind": "pending_crossing",
                    "ts": now, "expires": p["expires"],
                    "true_concurrent": true_conc,
                    "witness_out": p["witness_out"],
                    "witness_in": p["witness_in"],
                    "source": "door_live",
                }
                self.events.append(ev)
                self._mirror_event(ev, history=OCCUPANCY_HISTORY)
                new_events.append(ev)
        elif door_unnamed < self.unnamed:
            ev = {
                "kind": "door_live_gauge",
                "door_unnamed": door_unnamed,
                "room_unnamed": self.unnamed,
                "ts": now,
            }
            self.events.append(ev)

        # Unnamed slots age the same way — per-key ghost recovery only.
        # PAUSE (don't reset) while the FOV is busy: resetting made phantom
        # U-slots immortal in an occupied room (presence-perfect §1.1).
        for uk in list(self.unnamed_keys):
            # entry grace removed — U ghost clock runs on empty FOV only
            if not fov_empty:
                continue  # clock accrues only across door-empty observations
            if uk not in self.leave_since:
                self.leave_since[uk] = now
            elif now - self.leave_since[uk] >= ghost_recovery_sec:
                ev = self._drop_unnamed_key(
                    uk, now=now, source="ghost_recovery",
                    reason="unnamed_stale",
                    extra={"leave_match": {"method": "ghost_recovery",
                                           "person_id": None,
                                           "reason": "unnamed_stale"}})
                new_events.append(ev)

        self.last_door_names = names
        self.last_door_unnamed = door_unnamed
        # concurrent bodies this FOV — true per-frame when the caller gave
        # door_bodies_max (§1.3), else named at door + unnamed live
        self._touch_peaks(
            door_concurrent=true_conc if (names or door_unnamed) else 0,
            now=now,
        )
        if len(self.events) > 200:
            self.events = self.events[-100:]
        return new_events
