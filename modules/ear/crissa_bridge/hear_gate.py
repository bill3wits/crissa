"""Hear gate — sits immediately behind STT.

Order (the User 2026-08-27):

  1. STT
  2. Voice ID — always. Stamp on the turn even when Crissa is paused.
  3. Classify to_her / paused / media / chatter
  4. Room chatter may log (wall Room toggle prints named / scratch / ?)
  5. User (the User) log print — pause is Crissa only; UserOnly hides room
  6. Crissa replies only if to_her — vocative or in-session User, not media,
     not someone else. Verified the User is not enough if it is not to her.

Room can ID and log while Crissa comms and her UI reactions are paused.
She hears the room at STT/ID; she does not reply to paused speech
unless vocative. Speech that lands while she is answering does not
queue as a follow-up unless it is a vocative.

Display law (the User 2026-08-28, t_a4463137): the wall chat wisp is the SAME
gated channel as Crissa's ears. Only the User's own voice (the User), guest speech
while a session is open, and Crissa's own mouth air may paint the wisp.
Chatter / TV / scratch (non-guest) turns stay OUT of the wisp even though
voice ID still stamps them. The ear stamps the turn ring with
``crissa_hears`` from this gate; face-shell drops ring rows stamped
``crissa_hears: false``. Rows without the field fail open (pre-stamp turns,
other services) — never silently hide history.
"""
from __future__ import annotations

import re

from typing import Any

from .wake import wake_match


def _pid(turn: dict[str, Any]) -> str:
    st = turn.get("stamp") if isinstance(turn.get("stamp"), dict) else {}
    raw = turn.get("person_id") or (st.get("person_id") if isinstance(st, dict) else None) or ""
    return str(raw).strip().lower()


def _stamp(turn: dict[str, Any]) -> dict[str, Any]:
    st = turn.get("stamp")
    return st if isinstance(st, dict) else {}


def media_turn(stamp: dict[str, Any] | None) -> bool:
    st = stamp if isinstance(stamp, dict) else {}
    mg = st.get("media_gate") if isinstance(st.get("media_gate"), dict) else {}
    src = str(st.get("source") or "")
    if st.get("media_suppressed_id"):
        return True
    if mg.get("verdict") == "media" or mg.get("media_on") is True:
        return True
    if src.startswith("media_hold") or src.startswith("media_gate"):
        return True
    return False


def wake_media(stamp: dict[str, Any] | None) -> bool:
    """Media that holds her wake word back: the media gate's own verdict (TV- or podcast-shaped sound), never the
    identity hold. Round 15 (.101 13:34, the first "Hey, Chrisa." after Open Agent was skipped with `media: true`):
    a fresh ear starts every session uncertain, so a short wake line with no voice print by the sole person present was
    stamped `media_hold_sole` - "is the sole person really speaking?", an identity question - and media_turn() counted
    it as media, so the wake did not unpause. That hold still keeps the line off the User's lane (log_user, to_her and
    guest rails read media_turn as before); only the wake reads this narrower verdict. Kill: EAR_WAKE_SOLE_HOLD_BLOCKS=1
    (the hold blocks the wake again)."""
    import os

    if str(os.environ.get("EAR_WAKE_SOLE_HOLD_BLOCKS", "0")).strip().lower() in ("1", "true", "yes", "on"):
        return media_turn(stamp)
    st = stamp if isinstance(stamp, dict) else {}
    mg = st.get("media_gate") if isinstance(st.get("media_gate"), dict) else {}
    src = str(st.get("source") or "")
    if mg.get("verdict") == "media" or mg.get("media_on") is True:
        return True
    if src.startswith("media_gate"):
        return True
    if src.startswith("media_hold") and src != "media_hold_sole":
        return True
    if st.get("media_suppressed_id") and str(st.get("media_suppressed_source") or "") != "sole_present" \
            and src != "media_hold_sole":
        return True
    return False


# A PAUSE LIFTS ON HIS WORD (round 16 (c), .101 2026-10-13 00:36-00:39): after "Wait one second." paused her reading,
# "Okay. Continue." came with no voice print - the ear's identity hold on the sole person seen (`media_hold_sole`) -
# and was read as media (`not_to_her`); then "Hey, Chris. Go ahead and start again." and "Start where you left off."
# were skipped `paused`; only a clean "Hey, Crissa." lifted it. Now (a) a sole hold of the User is his line in a
# session, not media; (b) while a stop or a pause is fresh (the bridge passes `resume_pending`), a continue phrase from
# the User, or from the sole hold of the User, lifts the pause like her name. Kill: EAR_BRIDGE_PAUSE_LIFT=0.
_CONTINUE = re.compile(
    r"^(?:(?:hey|hi|ok|okay)[,\s]+[a-z']+[\s,.!?…]+)?"                         # "Hey, Chris." before it
    r"(?:(?:okay|ok|alright|all\s+right|yes|yeah|sure|now|so|please|well|right|then)[\s,.!?…]+)*"
    r"(?:(?:you\s+can|can\s+you|could\s+you|please)\s+)?"
    r"(?:continue|go\s+ahead|go\s+on|carry\s+on|keep\s+going|keep\s+reading|start\s+again|resume|"
    r"start\s+where\s+you\s+left\s+off|pick\s+(?:it\s+)?up\s+where\s+you\s+left\s+off|"
    r"(?:from\s+)?where\s+(?:you|we)\s+left\s+off)\b", re.I)


def pause_lift_on() -> bool:
    import os

    return os.environ.get("EAR_BRIDGE_PAUSE_LIFT", "1").strip().lower() not in ("0", "false", "no", "off")


def continue_phrase(text: str) -> bool:
    """Does the line open with a continue ("Okay. Continue.", "Go ahead and start again.", "Start where you left
    off.", "Hey, Chris. Go ahead...")?"""
    return bool(_CONTINUE.match((text or "").strip()))


def sole_user_hold(stamp: dict[str, Any] | None, uid: str) -> bool:
    """The ear held the sole person's stamp for want of a voice print, and that person is the User (round 16)."""
    st = stamp if isinstance(stamp, dict) else {}
    u = str(uid or "").strip().lower()
    return bool(u and str(st.get("source") or "") == "media_hold_sole"
                and str(st.get("media_suppressed_id") or "").strip().lower() == u and not wake_media(st))


def is_scratch(pid: str) -> bool:
    p = str(pid or "").strip().lower()
    return len(p) >= 2 and p[0] == "s" and p[1:].isdigit()


def is_user_pid(pid: str, uid: str) -> bool:
    p = str(pid or "").strip().lower()
    u = str(uid or "").strip().lower()
    return bool(p and u and p == u)


def is_other_named(pid: str, uid: str) -> bool:
    p = str(pid or "").strip().lower()
    if not p or p in ("", "unknown", "none", "stranger"):
        return False
    if is_scratch(p) or p.startswith("u") and p[1:].isdigit():
        return False
    if is_user_pid(p, uid):
        return False
    if p in ("crissa", "zoe", "crissa_her_tts", "announcer", "crew_tts"):
        return False
    return True


def _voice_sim(st: dict[str, Any]) -> float | None:
    try:
        v = st.get("voice_emb_sim")
        if v is None:
            v = st.get("self_sim")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# Same floor as live Speaker green (RAIL_VOICE_MATCH_SIM).
USER_READY_SIM = 0.50


def classify_hear(
    turn: dict[str, Any] | None,
    *,
    session_open: bool,
    uid: str,
    user_flag: bool | None = None,
    user_has_prints: bool | None = None,
    resume_pending: bool = False,
) -> dict[str, Any]:
    """Classify one STT turn. Voice ID is assumed already on the stamp.

    Hey Crissa unpauses LISTENING. User flag (and Auth) wait for the User at
    thresh/green. Scratch/TV vocative does not unpause. Guest rails stay
    on until User pops.
    """
    from .id_gate import open_classify          # round 17: Security ID-only / Off - the person at this computer
    opened = open_classify(turn, session_open=session_open, resume_pending=resume_pending)
    if opened is not None:
        return opened
    t = turn if isinstance(turn, dict) else {}
    text = str(t.get("text") or "").strip()
    st = _stamp(t)
    pid = _pid(t)
    wm = wake_match(text) if text else {"hit": False}
    vocative = bool(wm.get("hit"))
    media = media_turn(st)
    user = is_user_pid(pid, uid)
    other = is_other_named(pid, uid)
    scratch = is_scratch(pid)
    # NO VOICE PRINT YET (the User 2026-10-10, .101 Z): with an empty bank the ear names every voice a scratch id
    # (s1, s2 ...), the User's included, and a scratch vocative never unpaused - so a failed Voice ID left her deaf
    # to the whole room. While the User has no print, a scratch speaker is heard as a GUEST: never the User
    # (user_ready and to_her stay false, so nothing that needs it to be him runs), flagged `no_print`. Prints win:
    # once one exists the old rule stands unchanged. None = not known (the house): the old rule.
    no_print = bool(user_has_prints is False and scratch)
    if no_print:
        scratch = False
    sim = _voice_sim(st)
    if user_flag is None:
        user_ready = bool(user and sim is not None and sim >= USER_READY_SIM)
    else:
        user_ready = bool(user_flag)
    # Lamp: vocative unpauses even before ID, except TV scratch, media,
    # another named mouth, or is_user False (wife in his session).
    # Visitor "Hey Zoe" must not light LISTENING. Empty pid Hey Crissa still
    # replies as Guest. LISTENING + unstamped follow-up still hears
    # (2026-09-09 06:26). is_user False does not (2026-09-09 09:38).
    unnamed = not pid or no_print
    not_him = st.get("is_user") is False
    unpause = bool(
        vocative and not wake_media(st) and not scratch and not other and not not_him
    )
    lift = pause_lift_on()
    sole_user = bool(lift and sole_user_hold(st, uid))
    media_in_session = media and not sole_user          # (a) his line under the identity hold is not media
    resume = bool(
        lift and resume_pending and not session_open and not unpause and continue_phrase(text)
        and (user or sole_user) and not other and not not_him and not wake_media(st)
    )
    if resume:                                          # (b) his continue lifts the pause like her name
        unpause = True
    paused = not bool(session_open or unpause)
    to_her = bool(
        user_ready and not media and not other and not not_him and (vocative or session_open)
    )
    guest_rails = bool(
        (session_open or unpause) and not user_ready and not media and not not_him
    )
    crissa_hears = bool(
        to_her
        or (unpause and not other and not not_him)
        or (guest_rails and user and (vocative or session_open) and not scratch)
        or (
            session_open
            and unnamed
            and not media_in_session
            and not scratch
            and not other
            and not not_him
        )
    )
    return {
        "to_her": to_her,
        "paused": paused,
        "unpause": unpause,
        "guest_rails": guest_rails,
        "user_ready": user_ready,
        "crissa_hears": crissa_hears,
        # Pause is Crissa only. User roll is not dropped on pause.
        # Media (TV) is never the User lane, even if ID guessed the user.
        "log_user": bool(user and not media),
        "log_room": bool((not user) or media),
        "vocative": vocative,
        "media": media,
        "user": user,
        "other": other,
        "person_id": pid or None,
        "wake_kind": wm.get("kind") if vocative else None,
        "no_print": no_print,
        "resume": resume,
        "sole_user": sole_user,
    }


# A JOINED ASK KEEPS ITS LEAST-TRUSTED SPEAKER (round 46, the local review of kit crissa-063a2813, 2026-10-23): the
# gather window, a coalesced follow-up and a restarted speculation joined a later turn's words onto the ask and let
# that turn's person_id and stamp overwrite the first's - so a guest's "Hey Crissa, unlock ..." followed by a word from
# the User ran as the User. Now a later part never lends the ask a MORE trusted voice: by the rank this code already
# reads - the User (is_user_pid, not marked is_user False) above another named person above an unnamed, scratch or
# not-him voice - the later part's speaker is taken when it ranks the same or lower (a demotion, as before), and the
# earlier part's is kept when the later ranks higher. An earlier part that carries no speaker field at all made no
# claim and takes the later's (every live meta carries person_id, None or not). Kill: EAR_BRIDGE_JOIN_LEAST_TRUST=0
# (the later part wins, as before).
SPEAKER_FIELDS = ("person_id", "stamp", "is_user")


def join_least_trust_on() -> bool:
    import os

    return os.environ.get("EAR_BRIDGE_JOIN_LEAST_TRUST", "1").strip().lower() not in ("0", "false", "no", "off")


def speaker_trust(meta: dict[str, Any] | None, uid: str) -> int:
    """2 = the User, 1 = another named person, 0 = nobody the ear could name (or a voice marked not him)."""
    m = meta if isinstance(meta, dict) else {}
    st = m.get("stamp") if isinstance(m.get("stamp"), dict) else {}
    if m.get("is_user") is False or st.get("is_user") is False:
        return 0
    pid = str(m.get("person_id") or st.get("person_id") or "").strip().lower()
    if is_user_pid(pid, uid):
        return 2
    if is_other_named(pid, uid):
        return 1
    return 0


def _uid_now() -> str:
    try:
        from .user_mode import user_id_from_mode

        return str(user_id_from_mode() or "")
    except Exception:  # noqa: BLE001 - no User known: nobody ranks as the User
        return ""


def join_speaker(old: dict[str, Any] | None, new: dict[str, Any] | None, uid: str | None = None) -> dict[str, Any]:
    """The speaker fields a joined ask keeps: the later part's unless it ranks above the earlier's (then the earlier's).
    Fields the kept part does not carry are absent from the answer (the caller drops them, so the other part's never
    leak in)."""
    a = old if isinstance(old, dict) else {}
    b = new if isinstance(new, dict) else {}
    if not any(k in a for k in SPEAKER_FIELDS):
        keep = b
    else:
        u = _uid_now() if uid is None else str(uid or "")
        keep = a if speaker_trust(b, u) > speaker_trust(a, u) else b
    return {k: keep[k] for k in SPEAKER_FIELDS if k in keep}


def apply_join_speaker(joined: dict[str, Any], old: dict[str, Any] | None, new: dict[str, Any] | None,
                       uid: str | None = None) -> dict[str, Any]:
    """Set `joined`'s speaker fields to join_speaker(old, new) (in place; returned). Off: `joined` as it was."""
    if not join_least_trust_on():
        return joined
    keep = join_speaker(old, new, uid)
    for k in SPEAKER_FIELDS:
        if k in keep:
            joined[k] = keep[k]
        else:
            joined.pop(k, None)
    return joined
