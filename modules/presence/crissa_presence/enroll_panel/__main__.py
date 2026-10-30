#!/usr/bin/env python3
"""CLI: python -m crissa_presence.enroll_panel <command> ...

Commands:
  init               MASTER init — **User only**, then Inspector handoff + optional Add Regular
  add-regular        On-demand high-quality desk enroll for a family/office Regular
  asr-ab             Live A/B: parakeet-onnx vs Audio8 ark-asr (enroll mic path)
  init-site          copy site.example.json → site.json
  doctor / status / legend / person / voice / face / family / room-refs
"""

from __future__ import annotations

from .. import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import argparse
import sys
from pathlib import Path

# Allow `python tools/house_enroll/...` and `python -m crissa_presence.enroll_panel` from tools/
_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


def main(argv: list[str] | None = None) -> int:
    # round 46 (2026-10-23): every face ref, wav and print this process (the coach, every session) writes is the
    # owner's alone - umask 077, pack_store's 0600 (person.private_on; kill CRISSA_ID_PRIVATE=0)
    from .person import private_umask
    private_umask()
    from .site import init_site, load_site
    from . import cues
    from .doctor import run_doctor
    from .person import ensure_person, list_people, voice_bank_files
    from .status import print_status
    from .voice_session import run_voice_session, run_face_only_session
    from .sectioned_session import run_quality_session
    from .room_refs import capture_room_refs
    from .master_init import InitOpts, run_master_init, run_add_regular, PHASES

    ap = argparse.ArgumentParser(
        prog="house-enroll",
        description="House identity — User init + on-demand Regular enroll",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # accept legacy "voice" as alias for "user" phase
    phase_choices = list(PHASES) + ["voice"]
    p_master = sub.add_parser(
        "init",
        help="MASTER init — User only at desk, then Inspector handoff / Add Regular Now?",
    )
    p_master.add_argument(
        "--from",
        dest="from_phase",
        default="site",
        choices=phase_choices,
        help="Resume from phase (default: site). Alias: voice→user",
    )
    p_master.add_argument(
        "--stop-after",
        default=None,
        choices=phase_choices,
        help="Stop after this phase",
    )
    p_master.add_argument(
        "--person",
        default=None,
        help="Override User id for this run (default: site.enroll.user_id)",
    )
    p_master.add_argument("--skip-legend", action="store_true")
    p_master.add_argument("--skip-room-refs", action="store_true")
    p_master.add_argument("--force-refs", action="store_true")
    p_master.add_argument("--quiet", action="store_true")
    p_master.add_argument("--no-coach", action="store_true")
    p_master.add_argument(
        "--yes",
        action="store_true",
        help="Continue past non-fatal doctor issues",
    )
    p_master.add_argument("--min-takes", type=int, default=1)
    p_master.add_argument(
        "--add-regular",
        action="store_true",
        help="After User handoff, enroll a Regular (needs --regular-id or TTY prompt)",
    )
    p_master.add_argument(
        "--no-add-regular",
        action="store_true",
        help="Skip Add Regular Now? offer",
    )
    p_master.add_argument("--regular-id", default=None, help="With --add-regular")
    p_master.add_argument("--regular-display", default=None)

    p_reg = sub.add_parser(
        "add-regular",
        help="On-demand: high-quality desk enroll for a family/office Regular",
    )
    p_reg.add_argument("--id", required=True, dest="regular_id", help="person id e.g. <regular_id>")
    p_reg.add_argument("--display", default=None, dest="regular_display")
    p_reg.add_argument("--no-coach", action="store_true")
    p_reg.add_argument("--quiet", action="store_true")

    p_init = sub.add_parser("init-site", help="Create site.json from example")
    p_init.add_argument("--force", action="store_true")

    p_wc = sub.add_parser(
        "wake-corpus",
        help="Wake-word corpus session (the User 2026-08-05: part of User init) — "
             "Crissa-voice prompts, live screen text, tool-hold, mic preflight")
    p_wc.add_argument("--part", choices=["a", "b", "c"], required=False)
    p_wc.add_argument("--status", action="store_true")

    sub.add_parser("doctor", help="Preflight checks")
    sub.add_parser("status", help="Roster + bank status")
    sub.add_parser("legend", help="Speak/print beep legend only")

    p_person = sub.add_parser("person", help="Person scaffold")
    p_ps = p_person.add_subparsers(dest="person_cmd", required=True)
    p_add = p_ps.add_parser("add", help="Add people/<id>")
    p_add.add_argument("id")
    p_add.add_argument("--display", default=None)

    p_voice = sub.add_parser(
        "voice",
        help="Quality enroll: face section → quality check → voice paragraph",
    )
    p_voice.add_argument("id")
    p_voice.add_argument(
        "--only",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="Legacy single take (with --legacy-voice)",
    )
    p_voice.add_argument(
        "--replace",
        action="store_true",
        help="With --only N, overwrite NNN.json instead of appending",
    )
    p_voice.add_argument("--seconds", type=float, default=None)
    p_voice.add_argument(
        "--plan",
        default="quality",
        choices=[
            "quality",
            "sectioned",
            "paragraph",
            "para",
            "reenroll",
            "p0",
            "desk",
            "room",
            "legacy",
        ],
        help="quality=face then voice (default); desk/room/legacy need --legacy-voice",
    )
    p_voice.add_argument(
        "--legacy-voice",
        action="store_true",
        help="Classic interleaved voice_session (skip sectioned chapters)",
    )
    p_vreg = sub.add_parser(
        "voice-register",
        help="Desk register: Look here, Announcer, two beeps, FaceID face+profile, then CP",
    )
    p_vreg.add_argument("id")
    p_vreg.add_argument("--display", default=None)
    p_vreg.add_argument("--station", default="desk")
    p_vreg.add_argument("--mic", default="")
    p_vreg.add_argument("--first-boot", action="store_true", help="New User: the opening line first")
    sub.add_parser(
        "voice-mouths",
        help="Her own voice through the speakers: Crissa and the announcer say a few lines, the mic records them, "
             "and the ear's own-voice prints are made (mouth_prints)",
    )

    p_voice.add_argument("--no-face", action="store_true", help="Skip face section")
    p_voice.add_argument("--no-voice", action="store_true", help="Face section only")
    p_voice.add_argument("--no-coach", action="store_true", help="No browser coach UI")
    p_voice.add_argument(
        "--quiet",
        action="store_true",
        help="No spoken coach (tones only)",
    )

    p_face = sub.add_parser(
        "face",
        help="Face/profile snaps only (backfill — no voice re-record)",
    )
    p_face.add_argument("id")
    p_face.add_argument("--plan", default="desk", choices=["desk", "room", "legacy"])
    p_face.add_argument("--no-coach", action="store_true")
    p_face.add_argument("--quiet", action="store_true")

    p_fam = sub.add_parser(
        "family",
        help="Queue family: paragraph voice + face snap (joined User-init quality path)",
    )
    p_fam.add_argument(
        "--min-takes",
        type=int,
        default=1,
        help="Skip person if they already have this many voice files (default 1)",
    )
    p_fam.add_argument(
        "--plan",
        default="paragraph",
        choices=["paragraph", "para", "reenroll", "p0", "desk", "room", "legacy"],
        help="Default paragraph = full read-aloud + face on/off (joined quality path)",
    )
    p_fam.add_argument("--no-face", action="store_true", help="Skip camera face snaps")
    p_fam.add_argument("--no-coach", action="store_true")
    p_fam.add_argument("--quiet", action="store_true")
    p_fam.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="Limit to these ids (default: all active in index)",
    )

    p_refs = sub.add_parser("room-refs", help="Empty-room reference capture")
    p_refs.add_argument("--force", action="store_true", help="Skip empty-room gate")

    p_coach = sub.add_parser(
        "coach",
        help="Keep enroll coach UI up on the enroll port (display only; run voice/init separately)",
    )
    p_coach.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override ENROLL_UI_PORT (default: the port table's enroll, 8196 + CRISSA_PORT_OFFSET)",
    )
    p_coach.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab",
    )

    p_asr = sub.add_parser(
        "asr-ab",
        help="Live A/B: parakeet-onnx (:8094) vs Audio8 ark-asr — same GO/STOP mic path as init",
    )
    p_asr.add_argument(
        "--mode",
        choices=("script", "free"),
        default="script",
        help="script = read-aloud lines; free = ENTER each free take",
    )
    p_asr.add_argument(
        "--script",
        default=None,
        help="Read-aloud text (default: scripts/asr-ab-readaloud.txt)",
    )
    p_asr.add_argument(
        "--seconds",
        type=float,
        default=8.0,
        help="Record window after GO beep (default 8)",
    )
    p_asr.add_argument(
        "--out",
        default=None,
        help="Results dir (default: bench/presence-abc/results/asr-ab-live-STAMP)",
    )
    p_asr.add_argument(
        "--max-takes",
        type=int,
        default=0,
        help="Stop after N takes (0 = full script / until quit)",
    )
    p_asr.add_argument(
        "--keep-ear-closed",
        action="store_true",
        help="Do not reopen continuous ear after session",
    )

    p_imp = sub.add_parser(
        "import-clips",
        help="Import Fable-prepped room-mic WAVs → voice bank (ECAPA re-enroll wire)",
    )
    p_imp.add_argument(
        "--dir",
        default=None,
        help="Inbox dir (default: HOUSE_ID_ROOT/site/reenroll-inbox)",
    )
    p_imp.add_argument(
        "--person",
        action="append",
        dest="persons",
        default=None,
        help="Limit to person id (repeatable). Default: all with WAVs in inbox",
    )
    p_imp.add_argument(
        "--replace",
        action="store_true",
        help="Overwrite 001.json, 002.json, … instead of appending",
    )
    p_imp.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip pairwise self-sim check after import",
    )
    p_imp.add_argument(
        "--min-sec",
        type=float,
        default=1.5,
        help="Reject clips shorter than this (default 1.5, P0 gate)",
    )
    p_imp.add_argument(
        "--scaffold",
        action="store_true",
        help="Only create inbox dirs + README for Fable; do not import",
    )

    args = ap.parse_args(argv)

    if args.cmd == "init":
        add_reg: bool | None = None
        if args.no_add_regular:
            add_reg = False
        elif args.add_regular:
            add_reg = True
        stop = args.stop_after
        if stop == "voice":
            stop = "user"
        opts = InitOpts(
            from_phase=args.from_phase,
            stop_after=stop,
            person=args.person,
            skip_legend=args.skip_legend,
            quiet=args.quiet,
            force_refs=args.force_refs,
            min_voice_takes=args.min_takes,
            skip_room_refs=args.skip_room_refs,
            yes=args.yes,
            add_regular=add_reg,
            regular_id=args.regular_id,
            regular_display=args.regular_display,
            no_coach=args.no_coach,
        )
        return run_master_init(opts)

    if args.cmd == "add-regular":
        site = load_site()
        written = run_add_regular(
            site,
            args.regular_id,
            display=args.regular_display,
            coach=not args.no_coach,
            quiet=args.quiet,
        )
        return 0 if written or voice_bank_files(args.regular_id, site.root) else 1

    if args.cmd == "init-site":
        path = init_site(force=args.force)
        print(f"site config: {path}")
        print("Edit audio.mic, rails URLs, room_refs.stops_json for this office.")
        return 0

    if args.cmd == "asr-ab":
        from .asr_ab import run_asr_ab

        out = Path(args.out) if args.out else None
        script = Path(args.script) if args.script else None
        return run_asr_ab(
            site=load_site(),
            mode=args.mode,
            script_path=script,
            seconds=args.seconds,
            out=out,
            max_takes=args.max_takes,
            keep_ear_closed=args.keep_ear_closed,
        )

    if args.cmd == "wake-corpus":
        import subprocess as _sp
        from pathlib import Path as _P
        from .paths import rails_path

        script = rails_path("scripts", "wake-corpus-session.py")
        cmd = ["python3", str(script)]
        if getattr(args, "status", False):
            cmd.append("--status")
        elif getattr(args, "part", None):
            cmd += ["--part", args.part]
        else:
            cmd.append("--status")
        return _sp.run(cmd).returncode

    if args.cmd == "doctor":
        return run_doctor()

    if args.cmd == "status":
        return print_status()

    if args.cmd == "legend":
        site = load_site()
        cues.speak_legend(site)
        print("Demo tones: GO … STOP … SESSION")
        cues.go_beep(site, seconds=0.8)
        cues.done_chirp(site)
        cues.session_end(site)
        return 0

    if args.cmd == "person" and args.person_cmd == "add":
        d = ensure_person(args.id, display=args.display)
        print(f"person ready: {d}")
        return 0

    if args.cmd == "voice":
        if args.quiet:
            import os

            os.environ["HOUSE_ENROLL_QUIET"] = "1"
        site = load_site()
        plan = (args.plan or "quality").strip().lower()
        use_quality = (
            not args.legacy_voice
            and plan in ("quality", "sectioned", "paragraph", "para", "reenroll", "p0")
            and args.only is None
        )
        if use_quality:
            # paragraph aliases still run full face→voice sections
            voice_plan = "paragraph" if plan in (
                "quality",
                "sectioned",
                "paragraph",
                "para",
                "reenroll",
                "p0",
            ) else plan
            out = run_quality_session(
                site,
                args.id,
                coach=not args.no_coach,
                skip_face=args.no_face,
                skip_voice=args.no_voice,
                voice_plan=voice_plan,
            )
            written = out.get("voice") or []
            print(
                f"quality session face_pass={out.get('face_pass')} "
                f"voice={len(written)} sit={out.get('sit_station')} rest=ingress"
            )
            return 0 if written or args.no_voice or voice_bank_files(args.id, site.root) else 1
        written = run_voice_session(
            site,
            args.id,
            only=args.only,
            replace_slots=args.replace,
            seconds=args.seconds,
            plan_name="desk" if plan in ("quality", "sectioned") else plan,
            snap_face=not args.no_face,
            coach=not args.no_coach,
        )
        return 0 if written or voice_bank_files(args.id, site.root) else 1

    if args.cmd == "voice-mouths":
        from .mouth_prints import run_mouths

        got = run_mouths(load_site())
        print("voice-mouths saved=%s quiet=%s" % (got.get("saved"), got.get("quiet")), flush=True)
        return 0 if any((got.get("saved") or {}).values()) else 1
    if args.cmd == "voice-register":
        from .voice_register import run_voice_register

        site = load_site()
        written = run_voice_register(
            site,
            args.id,
            display=args.display,
            station=args.station or "desk",
            mic=args.mic or None,
            first_boot=bool(getattr(args, "first_boot", False)),
        )
        print(f"voice-register {args.id} takes={len(written)}")
        return 0 if written else 1

    if args.cmd == "face":
        if args.quiet:
            import os

            os.environ["HOUSE_ENROLL_QUIET"] = "1"
        site = load_site()
        recs = run_face_only_session(
            site,
            args.id,
            plan_name=args.plan,
            coach=not args.no_coach,
        )
        n_ok = sum(1 for r in recs if r.get("ok"))
        print(f"face snaps ok={n_ok}/{len(recs)}")
        return 0 if n_ok else 1

    if args.cmd == "family":
        if args.quiet:
            import os

            os.environ["HOUSE_ENROLL_QUIET"] = "1"
        site = load_site()
        people = list_people(site.root)
        if args.ids:
            want = {i.lower() for i in args.ids}
            people = [p for p in people if p["id"] in want]
        people = [p for p in people if p.get("active", True)]
        todo = [
            p for p in people if len(voice_bank_files(p["id"], site.root)) < args.min_takes
        ]
        if not todo:
            print("Everyone already has a voice bank (or roster empty).")
            print_status()
            return 0
        plan = getattr(args, "plan", "paragraph") or "paragraph"
        print(f"Family identity queue: {[p['id'] for p in todo]}  plan={plan}")
        cues.speak(
            site,
            f"Family identity enroll. {len(todo)} people. "
            "For each person: face section with quality check, then register voice. "
            "Long beep means go after it ends. Short chirp means stop.",
        )
        for p in todo:
            print(f"\n======== {p['id']} ========")
            if plan in ("paragraph", "para", "reenroll", "p0", "quality", "sectioned"):
                run_quality_session(
                    site,
                    p["id"],
                    coach=not args.no_coach,
                    skip_face=args.no_face,
                    voice_plan="paragraph",
                )
            else:
                run_voice_session(
                    site,
                    p["id"],
                    plan_name=plan,
                    snap_face=not args.no_face,
                    coach=not args.no_coach,
                )
        print("\n=== family pass complete ===")
        return print_status()

    if args.cmd == "room-refs":
        site = load_site()
        try:
            n = capture_room_refs(site, force=args.force)
        except RuntimeError as e:
            print(e)
            return 1
        return 0 if n else 1

    if args.cmd == "coach":
        import os
        import time as _time

        from .coach import coach_emit, coach_start

        if args.port:
            os.environ["ENROLL_UI_PORT"] = str(args.port)
        if args.no_browser:
            os.environ["HOUSE_ENROLL_NO_BROWSER"] = "1"
        url = coach_start(open_browser=not args.no_browser)
        coach_emit(type="clear")
        coach_emit(type="phase", text="idle", phase="idle")
        coach_emit(
            type="line",
            text="Coach ready. Run: house-enroll voice <id>  (or add-regular).",
            cls="muted",
        )
        coach_emit(
            type="coach",
            text="Screen is optional. Loud voice commands lead face and voice takes.",
        )
        print(f"coach idle on {url}  (Ctrl+C to stop)", flush=True)
        print("  live panel (not this): %s" % _ports.url("stt", "/panel"), flush=True)
        try:
            while True:
                _time.sleep(3600)
        except KeyboardInterrupt:
            print("\ncoach stopped", flush=True)
            return 0

    if args.cmd == "import-clips":
        from pathlib import Path as _Path
        from .import_clips import (
            default_inbox,
            ensure_inbox_scaffold,
            import_clips,
            print_import_report,
        )

        site = load_site()
        inbox = _Path(args.dir).expanduser() if args.dir else default_inbox(site.root)
        ensure_inbox_scaffold(inbox)
        if args.scaffold:
            print(f"inbox ready for Fable: {inbox}")
            print("  drop WAVs under <person>/01_on.wav 02_off.wav …")
            return 0
        out = import_clips(
            site,
            inbox=inbox,
            person_ids=args.persons,
            replace=args.replace,
            verify=not args.no_verify,
            min_sec=float(args.min_sec),
        )
        return print_import_report(out)

    ap.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
