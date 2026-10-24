"""Fast identity enroll — face 2 snaps, then voice fox ×2.

The User target script:
  Please look at the camera.          → snap 1
  Please look to the left.            → snap 2
  Done with face.
  Now voice. Look at the camera and say: «fox»  → beep → record
  Look to the left and say: «fox»             → beep → record
  ID initialization complete.

Camera: sit/desk pan≈0 during enroll; door rest pan≈450000 at end.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import cues
from .coach import coach_emit, coach_start, coach_stop
from .face_snap import (
    capture_face,
    enroll_begin_fov,
    face_quality,
    restore_door_home,
    sit_station_id,
)
from .person import display_name, ensure_person
from .roles import is_primary_user
from .site import Site
from .voice_session import (
    _tool_hold_acquire,
    _tool_hold_release,
    release_ear_for_enroll,
    reopen_ear_after_enroll,
    run_voice_session,
)

# Two face snaps only: camera, then left
FACE_SECTION_POSES: list[dict[str, str]] = [
    {
        "id": "face_on",
        "pose": "front",
        "speak": "Please look at the camera.",
    },
    {
        "id": "face_left",
        "pose": "profile",
        "speak": "Please look to the left.",
    },
]
# THE MISSING ANGLE ONLY (the User 2026-10-13, .101 round 15: the turn to the right came out front, so no right side was
# stored, and Face photos moved on anyway). Init names the poses still wanted - front, left, right - and the coach hands
# them over in CRISSA_FACE_POSES; only those are taken. Unset: the two above, as before.
FACE_POSE_BY_NAME: dict[str, dict[str, str]] = {
    "front": FACE_SECTION_POSES[0],
    "left": FACE_SECTION_POSES[1],
    "right": {"id": "face_right", "pose": "profile", "speak": "Please look to the right."},
}


# THE TURN COACHED ON THE FACE PHOTOS (round 34, the User 2026-10-19: Face photos again "with the turn check"): under
# the round 33 turn check (voice_register.turn_check_on, kill CRISSA_TURN_CHECK=0) a turned photo asks for a quarter
# turn by name, and a turned photo that did not take gets her correct-it line ("Just a quarter turn. I need to see your
# face.") and up to turn_check_tries() more tries, where it had "Again." and one try.
def _turn_coach() -> tuple[bool, int, str]:
    try:
        from .voice_register import TURN_QUARTER_LINE, turn_check_on, turn_check_tries
        return (bool(turn_check_on()), int(turn_check_tries()), TURN_QUARTER_LINE)
    except Exception:  # noqa: BLE001  the routine without the coaching
        return (False, 0, "")


def photo_beep_on() -> bool:
    """Round 28: each turned-away photo is heard (ding + "Got it."). Kill CRISSA_FACE_PHOTO_BEEP=0 (the short chirp)."""
    return os.environ.get("CRISSA_FACE_PHOTO_BEEP", "1").strip().lower() not in ("0", "off", "false", "no")


def face_poses(env: dict | None = None) -> list[dict[str, str]]:
    e = os.environ if env is None else env
    want = [x.strip() for x in str(e.get("CRISSA_FACE_POSES") or "").split(",") if x.strip() in FACE_POSE_BY_NAME]
    return [FACE_POSE_BY_NAME[x] for x in dict.fromkeys(want)] or list(FACE_SECTION_POSES)


def _section(title: str, subtitle: str = "") -> None:
    coach_emit(type="clear")
    coach_emit(type="section", text=title, phase=title.lower().replace(" ", "-"))
    coach_emit(type="phase", text=title, phase=title.lower().replace(" ", "-"))
    coach_emit(type="line", text=title, cls="beep")
    if subtitle:
        coach_emit(type="coach", text=subtitle)


def run_face_section(
    site: Site,
    person_id: str,
    *,
    max_retries: int = 0,
    at_desk: bool | None = None,
) -> list[dict[str, Any]]:
    """Two snaps: look at camera, look left. One attempt each (speed)."""
    person_id = person_id.strip().lower()
    ensure_person(person_id, root=site.root)
    name = display_name(person_id, site.root)
    if at_desk is None:
        # Loud cues / browser: primary User at monitor. FOV is always sit/desk below.
        at_desk = is_primary_user(site, person_id)
    # Stand-in-front product path: always sit/desk pan≈0 for face (the User 2026-08-02).
    # Door FOV for regulars was no_face when they stand at the camera, not the door.
    station = sit_station_id(site) or "desk"
    results: list[dict[str, Any]] = []

    _section("Face", name)
    try:
        enroll_begin_fov(site, station)
    except Exception as e:
        print(f"  enroll_begin_fov: {e!r}", flush=True)

    plan = face_poses()
    total = len(plan)
    # THE COUNTER (the User 2026-10-10, .101: "the photo part never incremented to 1 of two or two of two"): the plan
    # first, so Init shows "0 of 2" before the first pose; each saved print is a face_ok, each miss a face_fail with
    # the reason, so a plate with nothing saved can say why.
    coach_emit(type="face_plan", total=total, text=f"0 of {total}")
    # Fast path: no quality thrash (0 retries → one attempt)
    attempts = 1 + min(max(0, max_retries), 1)
    coach_turn, coach_tries, quarter = _turn_coach()
    for i, pose_plan in enumerate(plan, 1):
        pose = pose_plan["pose"]
        side = {"face_left": "left", "face_right": "right"}.get(str(pose_plan.get("id") or ""), "")
        turned = coach_turn and pose == "profile" and bool(side)
        pose_tries = (1 + coach_tries) if turned else attempts
        ok_rec: dict[str, Any] | None = None
        q: dict[str, Any] = {"pass": False}
        for attempt in range(1, pose_tries + 1):
            coach_emit(type="clear")
            coach_emit(
                type="phase",
                text=f"face {i}/{total}",
                phase=f"face-{i}/{total}",
            )
            coach_emit(type="coach", text=pose_plan["speak"])
            if attempt > 1:
                cues.speak_loud(site, quarter if turned else "Again.", pause=0.2)
            if turned:
                if attempt == 1:
                    cues.speak_loud(site, "Now a quarter turn to your %s." % side, pause=0.25)
            elif pose_plan.get("id") == "face_right":
                cues.speak_loud(site, pose_plan["speak"], pause=0.25)   # the other side, said as it is
            elif pose == "profile":
                cues.announce_turn_profile(site, at_desk=at_desk)
            else:
                cues.announce_hold_still(site, at_desk=at_desk)

            coach_emit(
                type="face",
                text=pose_plan["speak"] + " — facefinder framing",
                pose=pose,
                station=station,
            )
            # Settle after pose cue so profile turn finishes before FF locks
            time.sleep(0.55 if pose == "profile" else 0.35)
            # The User 2026-08-06: coached front + profile use facefinder center/zoom
            # (candid silent snaps stay hold-only elsewhere).
            rec = capture_face(
                site,
                person_id,
                station=station,
                pose=pose,
                take_plan_id=pose_plan["id"],
                aim=True,
                quality_map=True,
            )
            q = face_quality(rec)
            print(
                f"  face quality pass={q['pass']} det={q.get('det_score')} "
                f"reasons={q.get('reasons')}",
                flush=True,
            )
            if q["pass"]:
                ok_rec = rec
                coach_emit(
                    type="face_ok",
                    text=f"snap {i} ok",
                    pose=pose,
                    has_emb=True,
                )
                if pose == "profile" and photo_beep_on():
                    # round 28 (.101 2026-10-17: "neither told me when it was complete ... We are turned away from the
                    # screen"): a turned photo is heard - the two-tone take-in ding and "Got it." - not just a chirp
                    cues.success_ding(site)
                    cues.speak_loud(site, "Got it.", pause=0.15)
                else:
                    cues.done_chirp(site)
                break
            ok_rec = rec if rec else {"ok": False}
            coach_emit(type="face_fail", text=_face_why(rec, q), pose=pose, n=i, total=total)
            if turned:
                print(f"  face {side}: not taken ({_face_why(rec, q)}); try {attempt} of {pose_tries}", flush=True)
            if attempt < pose_tries:
                time.sleep(0.2)
        if ok_rec is None:
            ok_rec = {"ok": False}
        results.append({"rec": ok_rec, "quality": q, "pose": pose})

    n_pass = sum(1 for r in results if (r.get("quality") or {}).get("pass"))
    coach_emit(type="ok", text=f"Face done · {n_pass}/{total}")
    cues.announce_section_voice(site)  # "Done with face."
    return results


def _face_why(rec: dict[str, Any] | None, q: dict[str, Any]) -> str:
    """Why a pose saved no print, in words a person can act on (the engine's own error first)."""
    rec = rec or {}
    if not rec.get("ok"):
        return "the camera gave no picture" + ((": %s" % str(rec.get("error"))[:120]) if rec.get("error") else "")
    err = str(rec.get("embed_error") or "").strip()
    if err == "no_face":
        return "no face was found in the picture"
    if err:
        return "the face engine said: %s" % err[:160]
    reasons = [str(r) for r in (q.get("reasons") or [])]
    if any(r.startswith("det_score_low") for r in reasons):
        return "the face was not clear enough"
    if any(r.startswith("bbox_small") for r in reasons):
        return "the face was too small in the picture: come closer to the camera"
    return ", ".join(reasons)[:160] or "no print was made"


def announce_voice_bridge(site: Site, person_id: str) -> None:
    """No extra speech — face section already said Done with face."""
    _ = person_id
    _section("Voice", "fox line ×2")
    coach_emit(type="line", text="Voice", cls="beep")


def run_quality_session(
    site: Site,
    person_id: str,
    *,
    coach: bool = True,
    skip_face: bool = False,
    skip_voice: bool = False,
    voice_plan: str = "paragraph",
    restore_door: bool = True,
) -> dict[str, Any]:
    """Fast path: 2 face snaps → 2 voice fox takes → complete."""
    person_id = person_id.strip().lower()
    ensure_person(person_id, root=site.root)
    name = display_name(person_id, site.root)
    # Cues volume: primary User soft at desk; Regulars loud (stand-in-front).
    at_desk = is_primary_user(site, person_id)
    # FOV always desk (stand in front of camera) — not door for regulars.
    face_at_desk = True

    open_browser = bool(coach and at_desk)
    if coach:
        try:
            coach_start(open_browser=open_browser)
            time.sleep(0.2)
        except Exception as e:
            print(f"  coach display unavailable: {e!r}", flush=True)

    coach_emit(type="clear")
    coach_emit(type="phase", text="welcome", phase="welcome")
    coach_emit(type="line", text=f"Enroll · {name}", cls="muted")
    cues.speak_legend(site)  # print only

    # Pause Crissa/announcer free-air + hold mic for whole face+voice
    _tool_hold_acquire(reason="enroll", owner=f"house_enroll:{person_id}")
    release_ear_for_enroll()

    face_results: list[dict[str, Any]] = []
    voice_written: list[Path] = []
    try:
        if not skip_face:
            face_results = run_face_section(
                site, person_id, at_desk=face_at_desk
            )
            if not skip_voice:     # a face-only session (Init's face step) never shows the voice section's line
                announce_voice_bridge(site, person_id)
        if not skip_voice:
            voice_written = run_voice_session(
                site,
                person_id,
                plan_name=voice_plan,
                snap_face=False,
                coach=False,
                restore_door=False,
                skip_intro=True,
                at_desk=face_at_desk,  # desk FOV + loud-enough path
            )
    finally:
        if restore_door:
            restore_door_home(site)
        try:
            reopen_ear_after_enroll()
        except Exception as e:
            print(f"  ear reopen after quality: {e!r}", flush=True)
        _tool_hold_release(owner=f"house_enroll:{person_id}")

    coach_emit(type="clear")
    coach_emit(type="section", text="Complete", phase="done")
    coach_emit(type="done", text="ID initialization complete.")
    cues.announce_enroll_complete(site, name)
    cues.session_end(site)
    keep = os.environ.get("HOUSE_ENROLL_KEEP_COACH") == "1"
    if coach and not keep:
        time.sleep(0.6)
        try:
            coach_stop()
        except Exception:
            pass

    return {
        "person_id": person_id,
        "at_desk": at_desk,
        "face": face_results,
        "face_pass": sum(1 for r in face_results if (r.get("quality") or {}).get("pass")),
        "voice": [str(p) for p in voice_written],
        "sit_station": sit_station_id(site),
        "rest_station": "door",
    }
