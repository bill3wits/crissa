"""Master identity init — **User only**, then optional Add Regular on-demand.

Phases:

  1. site
  2. doctor
  3. status
  4. legend
  5. people   (ensure User folder; roster may exist)
  6. user     desk voice+face for the primary User only
  7. handoff  Inspector offer + Add Regular Now?
  8. room-refs (optional; when empty)
  9. done

Regulars are **not** in the init queue. Add them with:

  modules/id/scripts/house-enroll add-regular --id <regular_id> --display <Regular>
  modules/id/scripts/house-enroll init --add-regular   # interactive after User handoff

State: site/init-state.json
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import cues
from .coach import coach_emit, coach_start, coach_stop
from .doctor import run_doctor
from .paths import house_id_root
from .person import (
    display_name,
    ensure_person,
    face_bank_files,
    list_people,
    voice_bank_files,
)
from .roles import (
    ADD_REGULAR_PROMPT,
    HANDOFF_INSPECTOR,
    is_user_ready,
    mark_role,
    user_id,
)
from .room_refs import capture_room_refs, presence_empty
from .site import Site, init_site, load_site
from .status import print_status

PHASES = (
    "site",
    "room",
    "doctor",
    "status",
    "legend",
    "people",
    "user",
    "wake",
    "handoff",
    "room-refs",
    "done",
)


def _state_path(root: Path) -> Path:
    return root / "site" / "init-state.json"


def load_state(root: Path) -> dict[str, Any]:
    p = _state_path(root)
    if not p.is_file():
        return {
            "schema": "house_init_state/v2",
            "started_iso": None,
            "last_phase": None,
            "completed_phases": [],
            "user_done": False,
            "regulars_added": [],
            "notes": [],
        }
    return json.loads(p.read_text())


def save_state(root: Path, state: dict[str, Any]) -> None:
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_iso"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p.write_text(json.dumps(state, indent=2) + "\n")


def _mark(state: dict[str, Any], phase: str) -> None:
    state["last_phase"] = phase
    done = state.setdefault("completed_phases", [])
    if phase not in done:
        done.append(phase)


@dataclass
class InitOpts:
    from_phase: str = "site"
    stop_after: str | None = None
    person: str | None = None  # override User id for this run only
    skip_legend: bool = False
    quiet: bool = False
    force_refs: bool = False
    min_voice_takes: int = 1
    skip_room_refs: bool = False
    yes: bool = False
    # After User handoff: offer / force add-regular
    add_regular: bool | None = None  # None=ask if TTY, True=force prompt path, False=skip
    regular_id: str | None = None
    regular_display: str | None = None
    no_coach: bool = False


def phase_site(root: Path, state: dict[str, Any]) -> Site:
    print("\n── phase: site ──")
    path = init_site(root, force=False)
    print(f"  site.json → {path}")
    site = load_site(root)
    print(f"  site_id={site.site_id}  display={site.display}")
    uid = user_id(site)
    print(f"  User (init only) = {uid}")
    _mark(state, "site")
    save_state(root, state)
    return site


def phase_room(root: Path, site: Site, state: dict[str, Any], opts: InitOpts) -> None:
    """The User labels their own clock: door hour, mic facing, who sits where.

    Runs before doctor so every later phase (and the wall dial) reads a frame the
    User actually confirmed, rather than one inherited from wherever a camera hangs.
    """
    print("\n── phase: room ──")
    from .room_frame import (
        ask_family,
        ask_layout,
        ask_room_frame,
        ask_user,
        ask_visitors,
        program_room_globals,
    )

    who = ask_user(root, yes=opts.yes)
    from .roles import mark_role

    mark_role(who["user_id"], "user", root=root, display=who["display"])
    got = ask_room_frame(root, yes=opts.yes)
    layout = ask_layout(root, yes=opts.yes)
    fam = ask_family(root, yes=opts.yes)
    vis = ask_visitors(root, yes=opts.yes)
    published = program_room_globals(
        root,
        door=int(got.get("heading_door") or got.get("door_beam") or 10),
        camera_door=int(got.get("camera_door") or 10),
        media=layout.get("media_hour"),
        furniture=list(layout.get("furniture_hours") or []),
        stand=list(layout.get("stand_hours") or []),
        down=list(layout.get("seats_down") or []),
        wall=list(layout.get("wall_hours") or []),
        regulars=list(fam),
        visitors=list(vis),
        user_id=who["user_id"],
        user_hour=int(who.get("user_beam") or 6),
    )
    print(
        f"  programmed rails: dial_door={published['heading_door']} "
        f"camera_door={published['door']} you={published['user']} "
        f"legal={published['legal']} → {published['geometry']}"
    )
    state["room_frame"] = {
        "user_id": who["user_id"],
        "user_display": who["display"],
        "door_beam": published["door"],
        "legal": published["legal"],
        "regulars": len(fam),
        "visitors": len(vis),
        "seated": sum(1 for r in fam if r.get("home_beam") is not None),
        "geometry": published["geometry"],
    }
    _mark(state, "room")
    save_state(root, state)


def phase_doctor(site: Site, state: dict[str, Any]) -> int:
    print("\n── phase: doctor ──")
    rc = run_doctor()
    if rc == 0:
        _mark(state, "doctor")
        save_state(site.root, state)
    else:
        print("  doctor reported issues — fix site.json / devices, re-run init")
    return rc


def phase_status(site: Site, state: dict[str, Any]) -> None:
    print("\n── phase: status ──")
    print_status()
    uid = user_id(site)
    print(f"\n  init scope: User only → {uid} ({display_name(uid, site.root)})")
    print("  Regulars: on-demand via add-regular (not required for init)")
    _mark(state, "status")
    save_state(site.root, state)


def phase_legend(site: Site, state: dict[str, Any], opts: InitOpts) -> None:
    print("\n── phase: legend ──")
    if opts.skip_legend:
        print("  skipped (--skip-legend)")
        _mark(state, "legend")
        save_state(site.root, state)
        return
    cues.speak_legend(site)
    print("  Demo: long GO … short STOP … session end")
    cues.go_beep(site, seconds=0.9)
    time.sleep(0.2)
    cues.done_chirp(site)
    time.sleep(0.2)
    cues.session_end(site)
    _mark(state, "legend")
    save_state(site.root, state)


def phase_people(site: Site, state: dict[str, Any]) -> None:
    print("\n── phase: people ──")
    uid = user_id(site)
    mark_role(uid, "user", root=site.root)
    d = ensure_person(uid, root=site.root)
    fv = len(face_bank_files(uid, site.root))
    vv = len(voice_bank_files(uid, site.root))
    print(f"  USER  {uid:12} face={fv} voice={vv}  → {d}")
    for p in list_people(site.root):
        if p["id"] == uid:
            continue
        print(
            f"  reg   {p['id']:12} face={len(face_bank_files(p['id'], site.root))} "
            f"voice={len(voice_bank_files(p['id'], site.root))}  (on-demand)"
        )
    print("  add later:  house-enroll add-regular --id NAME --display Name")
    _mark(state, "people")
    save_state(site.root, state)


def phase_user(site: Site, state: dict[str, Any], opts: InitOpts) -> bool:
    """Desk enroll for primary User only. Returns True if User bank ready."""
    print("\n── phase: user (init enrollee) ──")
    uid = (opts.person or user_id(site)).strip().lower()
    mark_role(uid, "user", root=site.root)
    name = display_name(uid, site.root)
    n = len(voice_bank_files(uid, site.root))
    if n >= opts.min_voice_takes and not opts.person:
        print(f"  User {uid} already has voice={n} — skipping re-record")
        state["user_done"] = True
        _mark(state, "user")
        save_state(site.root, state)
        return True

    print(f"  Initializing User: {uid} ({name}) — sectioned face then voice")
    from .sectioned_session import run_quality_session

    cues.speak(
        site,
        f"Welcome. This is house identity setup for you, {name}, the primary User. "
        "We only need you for initialization. "
        "Section one: face photos with a quality check. "
        "Section two: register voice. "
        "Long beep means go after it ends. Short chirp means stop.",
    )
    out = run_quality_session(
        site,
        uid,
        coach=not opts.no_coach,
        voice_plan="paragraph",
    )
    written = out.get("voice") or []
    ready = len(voice_bank_files(uid, site.root)) >= opts.min_voice_takes
    state["user_done"] = ready
    if written or ready:
        _mark(state, "user")
    save_state(site.root, state)
    if not ready:
        print(f"  User {uid} not ready yet (voice bank short) — re-run init --from user")
    return ready


def _ask_yes_no(prompt: str, *, default_no: bool = True) -> bool:
    if not sys.stdin.isatty():
        return False
    hint = "[y/N]" if default_no else "[Y/n]"
    try:
        ans = input(f"{prompt} {hint} ").strip().lower()
    except EOFError:
        return False
    if not ans:
        return not default_no
    return ans in ("y", "yes")


def phase_handoff(site: Site, state: dict[str, Any], opts: InitOpts) -> None:
    """Inspector handoff + optional Add Regular Now?"""
    print("\n── phase: handoff ──")
    uid = user_id(site)
    uname = display_name(uid, site.root)

    if not opts.no_coach:
        try:
            coach_start(open_browser=True)
        except Exception:
            pass
        coach_emit(type="clear")
        coach_emit(type="phase", text="handoff", phase="handoff")
        coach_emit(type="coach", text=f"You're set, {uname}.")
        coach_emit(type="coach", text=HANDOFF_INSPECTOR)
        coach_emit(type="line", text="Add Regular Now?  (optional high-quality desk enroll)", cls="muted")

    print()
    print("  " + "─" * 52)
    print(f"  User {uname} initialized.")
    print(f"  {HANDOFF_INSPECTOR}")
    print("  " + "─" * 52)
    print()
    cues.speak(site, f"You're set, {uname}. {HANDOFF_INSPECTOR}")

    want = opts.add_regular
    if want is None:
        # Interactive when possible; --yes alone does not auto-add (must be intentional)
        want = _ask_yes_no("  Add Regular Now?", default_no=True)
        if not sys.stdin.isatty():
            print("  (non-interactive — skip Add Regular; use: house-enroll add-regular --id …)")
            want = False

    if want:
        cues.speak(site, ADD_REGULAR_PROMPT.replace("Say yes to enroll", "Enrolling"))
        rid = opts.regular_id
        rdisp = opts.regular_display
        if not rid and sys.stdin.isatty():
            try:
                # One name only — id + display derived (no second ask)
                from crissa_id.house_enroll.person import format_display_name, format_person_id

                raw = input("  New Regular name: ").strip()
                rid = format_person_id(raw)
                rdisp = format_display_name(raw) or None
            except EOFError:
                rid = ""
        if rid:
            run_add_regular(
                site,
                rid,
                display=rdisp,
                coach=not opts.no_coach,
                quiet=opts.quiet,
            )
            state.setdefault("regulars_added", []).append(rid)
        else:
            print("  no id given — skipped Add Regular")
            print("  later: house-enroll add-regular --id <id> --display Name")
    else:
        print("  Leaving further people to Inspector (or run add-regular anytime).")
        cues.speak(
            site,
            "Understood. Inspector will take it from here. "
            "You can still add a family member anytime with full quality desk enroll.",
        )

    _mark(state, "handoff")
    save_state(site.root, state)
    if not opts.no_coach:
        try:
            coach_emit(type="done", text="Init handoff complete")
            time.sleep(0.8)
            coach_stop()
        except Exception:
            pass


def run_add_regular(
    site: Site,
    person_id: str,
    *,
    display: str | None = None,
    coach: bool = True,
    quiet: bool = False,
) -> list[Path]:
    """On-demand high-quality desk enroll for a family/office Regular."""
    if quiet:
        import os

        os.environ["HOUSE_ENROLL_QUIET"] = "1"
    pid = person_id.strip().lower()
    mark_role(pid, "regular", root=site.root, display=display)
    name = display_name(pid, site.root)
    print(f"\n── Add Regular: {pid} ({name}) ──")
    # Sectioned quality path: face + quality gate → bridge → voice paragraph → door rest
    from .sectioned_session import run_quality_session

    cues.speak(
        site,
        f"High quality enroll for {name}. "
        "First face photos with a quality check, then register voice.",
    )
    out = run_quality_session(site, pid, coach=coach, voice_plan="paragraph")
    print(f"  Regular {pid} voice files now: {len(voice_bank_files(pid, site.root))}")
    print(f"  face_pass={out.get('face_pass')} voice={out.get('voice')}")
    return [Path(p) for p in out.get("voice") or []]


def phase_room_refs(site: Site, state: dict[str, Any], opts: InitOpts) -> int:
    print("\n── phase: room-refs ──")
    if opts.skip_room_refs:
        print("  skipped (--skip-room-refs)")
        return 0
    if not is_user_ready(site, opts.min_voice_takes):
        print("  deferred — User not ready yet")
        return 0
    ok, msg = presence_empty(site)
    if not ok and not opts.force_refs:
        print(f"  room not empty ({msg}) — later: house-enroll room-refs")
        state.setdefault("notes", []).append(f"room-refs deferred: {msg}")
        save_state(site.root, state)
        return 0
    try:
        n = capture_room_refs(site, force=opts.force_refs or not ok)
    except RuntimeError as e:
        print(f"  room-refs failed: {e}")
        return 1
    print(f"  wrote {n} stop frames → {site.room_refs_dir()}")
    _mark(state, "room-refs")
    save_state(site.root, state)
    return 0


def phase_wake(site: Site, state: dict[str, Any], opts: InitOpts) -> int:
    """Section 3 — wake-word corpus (the User 2026-08-05, merged from the pack).

    Fresh takes of the User saying 'Crissa' from three positions feed the wake
    verifier head (card t_502f07eb). Silent screen-prompt mode; the session
    tool holds the mic, stops ear-bridge, and flags quiet takes for redo.
    """
    print("\n── WAKE CORPUS (Section 3 · say 'Crissa' from 3 positions) ──")
    import subprocess

    from .paths import rails_path

    script = rails_path("scripts", "wake-corpus-session.py")
    if not script.is_file():
        print("  wake-corpus-session.py missing — skipped")
        return 0
    if not opts.yes and not _ask_yes_no("  run the 3-part wake session now? [y/N] "):
        print("  skipped — later: house-enroll wake-corpus --part a|b|c")
        _mark(state, "wake_skipped")
        save_state(site.root, state)
        return 0
    for part in ("a", "b", "c"):
        rc = subprocess.run(
            ["python3", str(script), "--part", part, "--silent"]
        ).returncode
        if rc != 0:
            print(f"  part {part} rc={rc} — redo later: house-enroll wake-corpus --part {part}")
    subprocess.run(["python3", str(script), "--status"])
    print("  ear-bridge stays stopped through the session; restart when done:")
    print("    systemctl --user start ear-bridge")
    _mark(state, "wake")
    save_state(site.root, state)
    return 0


def run_master_init(opts: InitOpts) -> int:
    root = house_id_root()
    if opts.quiet:
        import os

        os.environ["HOUSE_ENROLL_QUIET"] = "1"

    state = load_state(root)
    if not state.get("started_iso"):
        state["started_iso"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(root, state)

    print("=" * 56)
    print("  HOUSE IDENTITY — MASTER INIT (User only)")
    print(f"  root={root}")
    print(f"  from={opts.from_phase}")
    print("=" * 56)

    start = opts.from_phase
    # accept old alias "voice" → "user"
    if start == "voice":
        start = "user"
    if start not in PHASES:
        print(f"unknown phase {start!r}; choose from {PHASES}")
        return 2
    start_i = PHASES.index(start)

    if start_i <= PHASES.index("site"):
        site = phase_site(root, state)
        if opts.stop_after == "site":
            return 0
    else:
        site = load_site(root)

    def run_from(name: str) -> bool:
        return PHASES.index(name) >= start_i

    if run_from("room") and PHASES.index("room") >= start_i:
        phase_room(root, site, state, opts)
        if opts.stop_after == "room":
            return 0
        site = load_site(root)  # door_beam / regulars may have just changed

    if run_from("doctor") and PHASES.index("doctor") >= start_i:
        rc = phase_doctor(site, state)
        if rc != 0 and not opts.yes:
            print("\nStopping after doctor failures. Re-run when fixed, or pass --yes.")
            return rc
        if opts.stop_after == "doctor":
            return rc

    if run_from("status") and PHASES.index("status") >= start_i:
        phase_status(site, state)
        if opts.stop_after == "status":
            return 0

    if run_from("legend") and PHASES.index("legend") >= start_i:
        phase_legend(site, state, opts)
        if opts.stop_after == "legend":
            return 0

    if run_from("people") and PHASES.index("people") >= start_i:
        phase_people(site, state)
        if opts.stop_after == "people":
            return 0

    if run_from("user") and PHASES.index("user") >= start_i:
        ready = phase_user(site, state, opts)
        if opts.stop_after == "user":
            return 0 if ready else 1
        if not ready and opts.from_phase == "user":
            return 1

    if run_from("wake") and PHASES.index("wake") >= start_i:
        phase_wake(site, state, opts)
        if opts.stop_after == "wake":
            return 0

    if run_from("handoff") and PHASES.index("handoff") >= start_i:
        if is_user_ready(site, opts.min_voice_takes) or state.get("user_done"):
            phase_handoff(site, state, opts)
        else:
            print("  handoff skipped — User not ready")
        if opts.stop_after == "handoff":
            return 0

    if run_from("room-refs") and PHASES.index("room-refs") >= start_i:
        if opts.from_phase == "room-refs" or is_user_ready(site, opts.min_voice_takes):
            # don't block init on occupied room
            if opts.from_phase == "room-refs" or opts.force_refs:
                rc = phase_room_refs(site, state, opts)
                if rc != 0:
                    return rc
            else:
                ok, msg = presence_empty(site)
                if ok:
                    phase_room_refs(site, state, opts)
                else:
                    print(f"\n── room-refs deferred ({msg}) ──")
                    print("  when empty:  modules/id/scripts/house-enroll room-refs")

    if is_user_ready(site, opts.min_voice_takes):
        _mark(state, "done")
        save_state(site.root, state)
        print("\n" + "=" * 56)
        print(f"  USER INIT complete — {user_id(site)}")
        print("  Regulars: house-enroll add-regular --id <id> --display Name")
        print(f"  state → {_state_path(root)}")
        print("=" * 56)
        cues.session_end(site)
    else:
        print("\n── User init incomplete ──")
        print(f"  continue: modules/id/scripts/house-enroll init --from user")
    return 0
