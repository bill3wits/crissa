"""id_gate: what the kit's Security setting lets identity decide in her conversation (round 17, 2026-10-13).

The User 2026-10-13: "lets put in an off toggle for face and voice ID on the chat. Those are redundant to most laptops.
This is a chatbot which could perform way better with default security off ... It can trail fine for agent reference
just not a dependency for ID", then "maybe default is face only before tool calls ... we want default to be hybrid ID
mode and secure ON to be as is now with only User words and only User face to access User mode. Maybe then On,
ID-only, Off." What .101 showed that morning: his own words dropped as `no_emb`, 5-10 s to get a line through, a house
command skipped `await_voice` at user_voice_sim 0.478 (RESULTS-20261013-0055 6-9, ASK-20261013-0405).

The setting is lib/crissa_home.security_mode() (<state home>/security.json, the Crissa menu's Security item):

  on       today's bridge exactly (every function here answers None / True and the gate sites run as before)
  id_only  THE DEFAULT. Nothing in the conversation waits on or is decided by a voice print or a face: every turn is
           the conversation of the person at this computer (classify: no guest rails, no `no_emb`/`guest_unsure`/
           `await_voice`, her name wakes her from any voice, a follow-up from any voice keeps the session); the ear
           names the speaker after the turn is sent (crissa_ear/id_trail.py) and she gets that as a reference note
           (label_note). Before a TOOL CALL or a HOUSE ACTION his face must be at the camera NOW (act_face): the
           face block presence publishes, naming the User at the live-face bar (0.60; 0.40 for a profile while the
           camera is looking) and FRESH by presence's own clock - 2.5 s on a camera that turns (one missed visor tick
           of 1.2 s), 6 s on a fixed camera (its look comes once a second, built into presence's 1.2-2 s tick) -
           user_mode._live_face_user's windows, the ones that say "his face is there now", not "was seen a while
           ago". An action may wait up to ACT_FACE_WAIT_S (2 s, CRISSA_ACT_FACE_WAIT_S) for the camera to land on him;
           the chat never waits. No voice is needed. A failed check is said plainly (ACT_FACE_LINE) and nothing runs.
  off      no identity check and no gate anywhere; the computer's own lock is the security.

Her own-echo rejection is not identity and stays in every mode: the echo canceller, the ear's text subtraction
(self_echo) and own_voice's text rules (said_it_herself, on_air_not_user). Only own_voice's rule 3 (`guest_unsure`,
"nobody is identified") stands down outside On.

The gate sites call in here (hear_gate.classify_hear, user_mode.evaluate_turn, house_commands._is_authed, own_voice,
and in ear_bridge: the session follow-up, the near-name wake, schedule_dispatch's house commands, the confirm, the
dispatch's voice publish and crissa_reply's tool lock, tool guard and note). The house is always `on`.
Kill: CRISSA_SECURITY_DEFAULT_IDONLY=0 makes the default `on` (round 16's bridge); a saved choice stands.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from .home import EAR, PACK_ROOT

ACT_FACE_WAIT_S = float(os.environ.get("CRISSA_ACT_FACE_WAIT_S", "2.0") or 0)
ACT_FACE_POLL_S = 0.4
ACT_FACE_LINE = "I need to see your face at the camera before I do that."
ACT_NO_CAMERA_LINE = "I need to see your face at the camera before I do that, and I can't reach the camera right now."
LABEL_NOTE_S = float(os.environ.get("CRISSA_LABEL_NOTE_S", "300") or 300)
LABELS_FILE = "speaker-labels.jsonl"

# ROUND 20, ID-ONLY ACTIONS (the User 2026-10-14, .101 round 19: "give the FF some grace time for natural absence for
# picking something up ... If face no and voice yes then still yes?"; ruled: a 30 s grace, and voice is the fallback).
#   FACE GRACE  a face of the User's seen in this conversation keeps counting for a tool call or a house action for
#               FACE_GRACE_S (30 s) while the session stays open and no other face is at the camera: picking something
#               up or looking away does not drop him. A new face (another named one, or two faces in view) or the
#               session closing ends it at once (grace_end). Kill CRISSA_ACT_FACE_GRACE_S=0.
#   VOICE       with no face, the ear's label for THIS turn (speaker-labels.jsonl, matched by the turn's own words,
#               turn_label) naming the User by voice at VOICE_MIN_SIM (0.50, the User's floor; 0.14 % false accepts in
#               the house's trials) or better lets it go ahead. Kill CRISSA_ACT_VOICE_FALLBACK=0 (face only).
# Security On is untouched (face AND voice, as it always was): none of this runs there.
FACE_GRACE_S = float(os.environ.get("CRISSA_ACT_FACE_GRACE_S", "30") or 0)
VOICE_FALLBACK = str(os.environ.get("CRISSA_ACT_VOICE_FALLBACK", "1")).strip().lower() not in ("0", "off", "false", "no")
VOICE_MIN_SIM = float(os.environ.get("CRISSA_ACT_VOICE_MIN_SIM", "0.50") or 0.50)
TURN_LABEL_S = 120.0                         # a label older than this is not this turn's
_GRACE: dict = {"t": 0.0, "uid": "", "sim": None}
_TURN: dict = {"text": "", "t": 0.0}
_VOICE_VIA = ("voice", "voice+face")


def _zh():
    try:
        from . import crissa_home as H              # a copy beside this package
        return H
    except ImportError:
        pass
    try:
        import crissa_home as H                     # the source tree's / the pack's lib (home.py put it on the path)
        return H
    except ImportError:
        import sys
        sys.path.insert(0, str(PACK_ROOT / "lib"))
        import crissa_home as H                     # noqa: E402
        return H


def mode() -> str:
    """on | id_only | off. Unreadable: on (today's gates; never open by accident)."""
    try:
        m = _zh().security_mode()
    except Exception:  # noqa: BLE001
        return "on"
    return m if m in ("on", "id_only", "off") else "on"


def gating() -> bool:
    """True = Security On: identity decides as it always has."""
    return mode() == "on"


# ---- the hear gate (hear_gate.classify_hear) -------------------------------------------------------------------------

def open_classify(turn: dict | None, *, session_open: bool, resume_pending: bool = False) -> dict | None:
    """classify_hear's answer outside On (None in On): the person at this computer, whoever the voice. Her name wakes
    her and a follow-up keeps the session from any voice; only the media gate's own verdict (TV-shaped sound) holds a
    line back - never an identity hold. Same keys as classify_hear."""
    m = mode()
    if m == "on":
        return None
    from .hear_gate import _pid, _stamp, continue_phrase, pause_lift_on, wake_media
    from .wake import wake_match
    t = turn if isinstance(turn, dict) else {}
    text = str(t.get("text") or "").strip()
    st = _stamp(t)
    pid = _pid(t)
    wm = wake_match(text) if text else {"hit": False}
    vocative = bool(wm.get("hit"))
    media = wake_media(st)
    resume = bool(pause_lift_on() and resume_pending and not session_open and not vocative
                  and continue_phrase(text) and not media)
    unpause = bool((vocative or resume) and not media)
    to_her = bool(not media and (vocative or session_open or resume))
    return {
        "to_her": to_her,
        "paused": not bool(session_open or unpause),
        "unpause": unpause,
        "guest_rails": False,
        "user_ready": not media,
        "crissa_hears": to_her,
        "log_user": not media,
        "log_room": media,
        "vocative": vocative,
        "media": media,
        "user": not media,
        "other": False,
        "person_id": pid or None,
        "wake_kind": wm.get("kind") if vocative else None,
        "no_print": False,
        "resume": resume,
        "sole_user": False,
        "security": m,
    }


def any_voice_wakes(stamp: dict | None) -> bool:
    """The near-name wake ("Hey Krista") from any voice outside On (the media gate's own verdict still holds it)."""
    if gating():
        return False
    from .hear_gate import wake_media
    return not wake_media(stamp if isinstance(stamp, dict) else {})


# ---- the turn's level (user_mode.evaluate_turn) ---------------------------------------------------------------------

def grace_end(why: str = "") -> None:
    """The face grace is over: the session closed, or another face came (see ROUND 20, ID-ONLY ACTIONS)."""
    _GRACE.update(t=0.0, uid="", sim=None, why=why)


def turn_text(text: str) -> None:
    """The words of the turn being decided now (the bridge, as a turn reaches schedule_dispatch / dispatch)."""
    _TURN.update(text=str(text or ""), t=time.time())


def _another_face(p: dict, uid: str) -> bool:
    """Someone else at the camera: another named face, or two or more faces in view (one unnamed face is most often
    him, turned)."""
    buf = p.get("face") if isinstance(p.get("face"), dict) else {}
    if not buf:
        return False
    from .user_mode import _other_face_in_fov
    if _other_face_in_fov(buf, uid):
        return True
    try:
        n = int(buf.get("n_faces") or 0)
    except (TypeError, ValueError):
        n = 0
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    return max(n, len([f for f in faces if isinstance(f, dict)])) >= 2


def _grace(p: dict, uid: str, now: float | None = None) -> float | None:
    """His face's grace: the sim it was seen at while still in grace, else None (and a new face ends it)."""
    if FACE_GRACE_S <= 0 or not _GRACE.get("t") or _GRACE.get("uid") != uid:
        return None
    if p and _another_face(p, uid):
        grace_end("another_face")
        return None
    now = time.time() if now is None else float(now)
    if now - float(_GRACE["t"]) > FACE_GRACE_S:
        return None
    return _GRACE.get("sim") if _GRACE.get("sim") is not None else 0.0


def _voice(uid: str) -> float | None:
    """This turn's ear label naming the User by voice at VOICE_MIN_SIM or better: its sim, else None."""
    if not VOICE_FALLBACK:
        return None
    lab = turn_label()
    if not lab or str(lab.get("who") or "").strip().lower() != uid or lab.get("via") not in _VOICE_VIA:
        return None
    try:
        sim = float(lab.get("sim"))
    except (TypeError, ValueError):
        return None
    return sim if sim >= VOICE_MIN_SIM else None


def act_face(presence: dict | None, uid: str | None = None) -> tuple[bool, float | None, str]:
    """Is the User at the camera now, for an action? (ok, sim, why). why: "face" (his face now), "face_grace" (seen in
    this conversation within FACE_GRACE_S), "voice" (no face; this turn's ear label names him by voice - sim is the
    voice's), else the miss. See the module's note for the bar and the windows, and ROUND 20 above."""
    from .user_mode import _live_face_user, user_id_from_mode
    p = presence if isinstance(presence, dict) else {}
    u = str(uid or user_id_from_mode() or "").strip().lower()
    if not p or (p.get("error") and not isinstance(p.get("face"), dict)):
        miss: tuple = (False, None, "no_camera")
    elif not u:
        return False, None, "no_user"
    else:
        live, sim, fresh = _live_face_user(p, u)
        if live and fresh:
            if _another_face(p, u):
                grace_end("another_face")          # his face now counts; with someone beside him it opens no grace
            else:
                _GRACE.update(t=time.time(), uid=u, sim=sim, why="face")
            return True, sim, "face"
        miss = (False, sim, "not_seen" if fresh or not live else "stale")
    if u:
        g = _grace(p, u)
        if g is not None:
            return True, (g or None), "face_grace"
        v = _voice(u)
        if v is not None:
            return True, v, "voice"
    return miss


def owner_verdict(presence: dict | None, stamp: dict | None, *, person_id: str | None = None) -> dict | None:
    """evaluate_turn's answer outside On (None in On): the turn is the owner's conversation (level bau, auth held,
    never guest, never silent, never `await_voice`). `user_voice` / `tools_ok` / tier1 / tier2 - the keys every action
    gate already reads - carry the action rule: Off yes; ID-only only while his face is at the camera now."""
    m = mode()
    if m == "on":
        return None
    from .user_mode import user_id_from_mode
    st = stamp if isinstance(stamp, dict) else {}
    uid = user_id_from_mode()
    how = ""
    if m == "off":
        ok, sim, why = True, None, "security_off"
    else:
        ok, sim, how = act_face(presence, uid)
        why = ("id_only" if how == "face" else "id_only_" + how) if ok else "id_only_no_face:" + how
    by_face = bool(ok) and how in ("face", "face_grace")
    return {
        "mode": "security_" + m,
        "security": m,
        "user_id": uid,
        "user_present": bool(ok),
        "user_face_ok": by_face and m == "id_only",
        "host_unlocked": False,
        "user_face_sim": sim if by_face else None,
        "user_voice": bool(ok),
        "user_voice_sim": sim if how == "voice" else None,
        "speaker": turn_label() if m in ("id_only", "off") else None,   # round 20: who the ear named this turn (.101 r28: Off too)
        "scores": turn_scores() if (m == "id_only" and label_both_on()) else None,   # round 21: face AND voice, labelled
        "auth": True,
        "auth_reason": why,
        "level": "bau",
        "guest_policy": "polite",
        "bau": True,
        "tools_ok": bool(ok),
        "lights_out": False,
        "session_held": True,
        "strong_voice_age_s": None,
        "tier1_ok": bool(ok),
        "tier2_ok": bool(ok),
        "force_reprove": False,
        "auth_tier": None,
        "auth_age_s": None,
        "oracle_face": False,
        "face_why": why,
        "stamp_source": st.get("source"),
        "stamp_certain": st.get("certain"),
        "top_sim": st.get("top_sim"),
        "speaker_id": person_id,
    }


def act_authed(presence: dict | None) -> bool | None:
    """house_commands._is_authed outside On (None in On): Off yes; ID-only his face at the camera now."""
    m = mode()
    if m == "on":
        return None
    if m == "off":
        return True
    return act_face(presence)[0]


def refuse_line(on_line: str) -> str:
    """The words for an action refused: On's own line, or (ID-only) the face line."""
    if mode() != "id_only":
        return on_line
    return _zh().FACE_SEQ_FAIL if sequence_on() else ACT_FACE_LINE


# ONE FACE-MATCH SEQUENCE (round 17 follow-ups, the User 2026-10-13: "All tools or switches requesting auth gets face
# match sequence"): ID-only's check before a house action or a tool call is the kit's one sequence
# (lib/crissa_home.face_sequence - the same the menus and panels run before a setting while Security is On): his face
# there now goes at once; else she asks him to look at the camera for a moment (a house action: `ask`, her voice), on
# a camera that turns presence looks at his seat itself (presence_look), on a fixed camera presence is read again for
# up to 5 s; seen, the action runs; not seen, she says she could not see his face and nothing runs. A tool call comes
# mid-reply, so there she does not ask aloud (the turn's prompt line already told her to ask him to look and say it
# again) and the wait is ACT_FACE_WAIT_S. Kill: CRISSA_SECURITY_LOCK_ADJUST=0 (round 17's check: no ask, 2 s, its
# own lines).
def sequence_on() -> bool:
    try:
        return bool(_zh().lock_adjust_on())
    except Exception:  # noqa: BLE001
        return False


def presence_look() -> dict:
    """A camera that turns: presence's FaceFinder at his seat (POST /v1/visit_lamp {op: "face_check"})."""
    try:
        from .ear_bridge import PRESENCE, _post_json
        base = PRESENCE[: -len("/v1/presence")] if PRESENCE.rstrip("/").endswith("/v1/presence") else PRESENCE
        return _post_json(base.rstrip("/") + "/v1/visit_lamp", {"op": "face_check"}, timeout=12.0) or {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "checked": False, "error": type(e).__name__}


def act_check(presence: dict | None, fetch: Callable[[], dict] | None = None,
              wait_s: float | None = None, ask: Callable[[str], Any] | None = None,
              look: Callable[[], dict] | None = None) -> tuple[bool, str, dict]:
    """Before an action (ID-only): (ok, why, presence). The face-match sequence (above): `ask` says the ask, `look`
    is a camera that turns' own look, `wait_s` the wait (the sequence's 5 s when None); Off: (True, "security_off",
    presence) at once."""
    p = presence if isinstance(presence, dict) else {}
    m = mode()
    if m != "id_only":
        return True, "security_" + m, p
    ok0, _s0, why0 = act_face(p)
    if ok0:                                    # his face now, its grace, or this turn's voice (round 20): at once
        return True, why0, p
    if sequence_on():
        from .user_mode import user_id_from_mode
        got = _zh().face_sequence(fetch, uid=user_id_from_mode(), presence=p, ask=ask, look=look, wait_s=wait_s)
        return bool(got["ok"]), str(got["why"]), got.get("presence") or p
    ok, _sim, why = act_face(p)
    end = time.time() + (ACT_FACE_WAIT_S if wait_s is None else float(wait_s))
    while not ok and fetch is not None and time.time() < end:
        time.sleep(ACT_FACE_POLL_S)
        try:
            p = fetch() or {}
        except Exception:  # noqa: BLE001
            p = {}
        ok, _sim, why = act_face(p)
    return ok, why, p


def act_line(why: str) -> str:
    if sequence_on():
        H = _zh()
        return H.face_line(why) or H.FACE_SEQ_FAIL
    return ACT_NO_CAMERA_LINE if why == "no_camera" else ACT_FACE_LINE


def house_command(user: str, meta: dict, presence: dict | None, *, speak: Callable[[str], Any],
                  log: Callable[[dict], Any], fetch: Callable[[], dict] | None = None) -> bool:
    """schedule_dispatch's house-command step outside On. True = handled (run, or refused and said); False = not a
    command, the turn goes on to her. Off: run. ID-only: his face first (act_check); `cancel` always runs (an abort
    is safe, as in On)."""
    from .house_commands import match_control, try_handle_utterance
    cmd_m = match_control(user)
    if not cmd_m:
        return False
    cmd = str(cmd_m.get("cmd") or "")
    p = presence if isinstance(presence, dict) else {}
    if cmd != "cancel":
        seq = sequence_on()
        ok, why, p = act_check(p, fetch, ask=speak if seq else None, look=presence_look if seq else None)
        if not ok:
            log({"event": "house_cmd_face_refused", "cmd": cmd, "why": why, "security": mode()})
            speak(act_line(why))
            return True
    if try_handle_utterance(user, meta=meta, presence=p, speak=speak):
        log({"event": "house_cmd_handled", "user": user[:120], "security": mode()})
        return True
    return False


# ---- her reply (crissa_reply) -----------------------------------------------------------------------------------------

class ActRefused(Exception):
    """A tool call in her stream without his face at the camera (ID-only): the read stops here."""


def reply_line(act_ok: bool) -> str:
    """The system prompt's identity line outside On ("" in On)."""
    m = mode()
    if m == "off":
        return ("Security is Off on this computer: whoever is talking to you is the person at this computer; help "
                "fully, with tools when asked.\n")
    if m == "id_only":
        if act_ok:
            return ("Security is ID-only on this computer: the person at this computer is talking to you, and the "
                    "User's face is at the camera now, so tools and house actions are allowed this turn.\n")
        if sequence_on():
            return ("Security is ID-only on this computer: the person at this computer is talking to you; chat freely. "
                    "Tools and house actions need the User's face at the camera, and it is not there now: if this "
                    "turn needs a tool or an action, ask them plainly to look at the camera for a moment and say it "
                    "again, and do not act.\n")
        return ("Security is ID-only on this computer: the person at this computer is talking to you; chat freely. "
                "Tools and house actions need the User's face at the camera, and it is not there now: if this turn "
                "needs a tool or an action, say plainly that you need to see their face at the camera first, and do "
                "not act.\n")
    return ""


def tool_guard(on_tool: Callable[[int], Any], fetch: Callable[[], dict] | None,
               log: Callable[[dict], Any] | None = None) -> Callable[[int], Any]:
    """crissa_reply's on_tool outside On: ID-only checks his face at the first tool event of the turn and stops the
    stream (ActRefused) without it; the gateway interrupts its agent on the disconnect (as the revise turn's
    no-tools rule does). On / Off: on_tool unchanged."""
    if mode() != "id_only":
        return on_tool
    seen = {"ok": False}

    def _guard(n: int):
        if not seen["ok"]:
            try:
                p = fetch() if fetch is not None else {}
            except Exception:  # noqa: BLE001
                p = {}
            seq = sequence_on()
            ok, why, _p = act_check(p, fetch, wait_s=ACT_FACE_WAIT_S if seq else None,
                                    look=presence_look if seq else None)
            if not ok:
                if log is not None:
                    log({"event": "tool_face_refused", "why": why, "at": n})
                raise ActRefused(why)
            seen["ok"] = True
        return on_tool(n)
    return _guard


# HER CONTEXT CARRIES WHO SPOKE, HOW AND HOW SURE (round 20, .101 2026-10-14: the ear named the User by voice on 4 of 5
# turns at sims 0.54-0.66 while every dispatch carried pid null, so asked "what's my voice score?" she first made one up,
# then said she saw none). The note names this turn's label when the ear has written it (turn_label: matched by the
# turn's own words), else the last one, with how it was named and the score, and tells her to answer only from it.
# Kill CRISSA_LABEL_NOTE_SCORE=0 (round 19's note: the name only).
LABEL_NOTE_SCORE = str(os.environ.get("CRISSA_LABEL_NOTE_SCORE", "1")).strip().lower() not in ("0", "off", "false", "no")
NO_SCORE_LINE = (" No speaker recognition is ready for this line; if asked who is speaking or for a voice or face "
                 "score, say you do not have one yet.")


def _how(lab: dict) -> str:
    via = str(lab.get("via") or "")
    try:
        sim = "%.2f" % float(lab.get("sim"))
    except (TypeError, ValueError):
        sim = ""
    if via in _VOICE_VIA:
        return " by voice" + (" (voice match score %s of 1; the User's bar is %.2f)" % (sim, VOICE_MIN_SIM) if sim else "")
    if via == "face":
        return " by face" + (" (face match score %s of 1)" % sim if sim else "")
    return ""


# A NOTE SHE KNOWS, NEVER A LINE SHE SAYS; BOTH SCORES, LABELLED (round 21, .101 2026-10-14 round 20: she answered eight
# vague lines with exactly "<name>'s here." - round 20's note was a sentence she could echo - and asked "what's my voice
# score?" she gave the FACE sim, 0.65, as the voice score while his voice read 0.56-0.58: the note named one row, the
# face's, because the voice row is written after the turn is sent). The note is a bracketed background fact that says
# never to announce it; it carries this line's face match and its voice match, each labelled, the voice from the
# speaker's last line when this one has none yet, and tells her to answer in the second person and only from these
# numbers. Kill CRISSA_LABEL_NOTE_BOTH=0 (round 20's note).
NO_SCORE_BOTH = (" [Speaker check, background only: nothing is recognised for this line yet. Never mention it; if asked "
                 "who is speaking or for a face or voice score, say you do not have one yet.]")


def label_both_on() -> bool:
    return LABEL_NOTE_SCORE and os.environ.get("CRISSA_LABEL_NOTE_BOTH", "1").strip().lower() not in (
        "0", "off", "false", "no")


def _sim2(v) -> str:
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return ""


def turn_scores(text: str | None = None, now: float | None = None) -> dict | None:
    """This turn's speaker with BOTH scores: {"who", "face", "voice", "voice_from"} (voice_from "this line" | "your
    last line" | None). The face is this turn's face row; the voice is this turn's voice row (named or not), else the
    named speaker's newest voice row within LABEL_NOTE_S. None when the ear has written nothing for this turn."""
    t = _norm(_TURN.get("text") if text is None else text)
    if not t:
        return None
    now = time.time() if now is None else float(now)
    rows = _label_rows()
    best = None
    for d in rows:
        try:
            ts = float(d.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        lt = _norm(d.get("text") or "")
        if lt and lt in t and now - ts <= TURN_LABEL_S and (best is None or ts > best):
            best = ts
    if best is None:
        return None
    mine = ("crissa", "zoe", "crissa_her_tts")
    same = [d for d in rows if _same_ts(d, best) and str(d.get("who") or "").lower() not in mine
            and d.get("via") != "her_voice"]
    face = [d for d in same if d.get("via") == "face" and d.get("who")]
    voice = [d for d in same if d.get("via") in _VOICE_VIA
             or (d.get("via") is None and d.get("sim") is not None)]
    named_voice = [d for d in voice if d.get("who")]
    who = str((named_voice or face or [{}])[-1].get("who") or "") or None
    out = {"who": who, "face": face[-1].get("sim") if face else None, "voice": None, "voice_from": None}
    if voice:
        out.update(voice=voice[-1].get("sim"), voice_from="this line")
    elif who:
        for d in reversed(rows):
            try:
                age = now - float(d.get("at") or d.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if age > LABEL_NOTE_S:
                break
            if d.get("via") in _VOICE_VIA and str(d.get("who") or "").lower() == who.lower() and d.get("sim") is not None:
                out.update(voice=d.get("sim"), voice_from="your last line")
                break
    if not (out["who"] or out["face"] is not None or out["voice"] is not None):
        return None
    return out


def _note_both(text: str | None, now: float | None) -> str:
    sc = turn_scores(text, now)
    last = False
    if not sc or not sc.get("who"):
        lab = last_label(now)                          # nothing for this line yet: the speaker's last line, said so
        if not lab or not lab.get("who"):
            return NO_SCORE_BOTH
        via = str(lab.get("via") or "")
        sc = {"who": lab.get("who"), "face": lab.get("sim") if via == "face" else None,
              "voice": lab.get("sim") if via in _VOICE_VIA else None, "voice_from": "your last line"}
        last = True
    name = display_name(str(sc.get("who") or ""))
    face, voice = _sim2(sc.get("face")), _sim2(sc.get("voice"))
    parts = []
    parts.append(("face match %s of 1%s" % (face, ", from their last line" if last else "")) if face
                 else "face match: none for this line")
    if voice:
        parts.append("voice match %s of 1%s (the bar for the User's voice is %.2f)" % (
            voice, "" if sc.get("voice_from") == "this line" else ", from their last line", VOICE_MIN_SIM))
    else:
        parts.append("voice match: not scored yet")
    eg = "your face matched at %s%s" % (face or "-", (", your voice at %s" % voice) if voice else "")
    return (" [Speaker check, background only. Never say it, never announce who is here, and do not bring it up; use it "
            "only when asked who is speaking or for a score.] Speaker: %s (probably); %s. If asked, answer to them in "
            "the second person (\"%s\") and only from these numbers." % (name, "; ".join(parts), eg))


def label_note(now: float | None = None, text: str | None = None) -> str:
    """Outside On: who the ear named for this turn (or the last one), "probably ...", how and how sure, for her
    reference only, or "" (round 19's name-only note under CRISSA_LABEL_NOTE_SCORE=0). Read from
    <state home>/ear/speaker-labels.jsonl (crissa_ear/id_trail.py); never waited for. Round 21: both scores, a
    background fact (label_both_on)."""
    if gating():
        return ""
    if label_both_on():
        return _note_both(text, now)
    lab = (turn_label(text, now) if LABEL_NOTE_SCORE else None) or last_label(now)
    if lab and not lab.get("who"):
        lab = None
    if not lab:
        return NO_SCORE_LINE if LABEL_NOTE_SCORE else ""
    name = display_name(str(lab.get("who") or ""))
    said = " ".join(str(lab.get("text") or "").split())[:60]
    if not LABEL_NOTE_SCORE:
        return (" Recognised after the fact, for your reference only (not a check): the line \"%s\" was probably said "
                "by %s." % (said, name))
    return (" Recognised after the fact, for your reference only (not a check): the line \"%s\" was probably said by "
            "%s%s. If asked who is speaking or for a score, answer from this note only." % (said, name, _how(lab)))


def _label_rows() -> list:
    p = EAR / LABELS_FILE
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 16384))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for raw in tail:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", str(s or "").lower()).split())


def turn_label(text: str | None = None, now: float | None = None) -> dict | None:
    """The ear's label for THIS turn: the newest turn (by its ring ts, within TURN_LABEL_S) whose words are part of
    `text` (else the words turn_text last named), its best row - a name by voice over a name by face over none.
    {"who", "via", "sim", "ts"} or None. Her own voice is never the speaker."""
    t = _norm(_TURN.get("text") if text is None else text)
    if not t:
        return None
    now = time.time() if now is None else float(now)
    rows = _label_rows()
    best = None
    for d in rows:
        try:
            ts = float(d.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        lt = _norm(d.get("text") or "")
        if lt and lt in t and now - ts <= TURN_LABEL_S and (best is None or ts > best):
            best = ts
    if best is None:
        return None
    same = [d for d in rows if _same_ts(d, best)]
    named = [d for d in same if d.get("who") and str(d.get("who")).lower() not in ("crissa", "zoe", "crissa_her_tts")]
    voice = [d for d in named if d.get("via") in _VOICE_VIA]
    face = [d for d in named if d.get("via") == "face"]
    pick = (voice or face or [{}])[-1]
    text0 = next((d.get("text") for d in same if d.get("text")), "")
    return {"who": pick.get("who"), "via": pick.get("via"), "sim": pick.get("sim"), "ts": best, "text": text0}


def _same_ts(d: dict, ts: float) -> bool:
    try:
        return abs(float(d.get("ts") or 0) - ts) < 1e-6
    except (TypeError, ValueError):
        return False


def last_label(now: float | None = None) -> dict | None:
    now = time.time() if now is None else float(now)
    for d in reversed(_label_rows()):
        if not d.get("who"):
            continue
        if str(d.get("who")).lower() in ("crissa", "zoe", "crissa_her_tts"):
            continue                                 # her own voice heard back is no speaker of his turn
        try:
            if now - float(d.get("at") or d.get("ts") or 0) > LABEL_NOTE_S:
                return None
        except (TypeError, ValueError):
            return None
        return d
    return None


def display_name(who: str) -> str:
    """The User's name as Setup saved it (<state home>/user.json) when `who` is the User, else the ear's own id."""
    w = str(who or "").strip().lower()
    try:
        from .user_mode import user_id_from_mode
        uid = str(user_id_from_mode() or "").strip().lower()
    except Exception:  # noqa: BLE001
        uid = ""
    if w and w == uid:
        try:
            from .home import HOME
            u = json.loads((HOME / "user.json").read_text(encoding="utf-8"))
            nm = str((u or {}).get("name") or "").strip()
            if nm:
                return nm
        except (OSError, ValueError):
            pass
        return "the User"
    return w or "someone"
