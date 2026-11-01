#!/usr/bin/env python3
"""Ear bridge — STT → voice lock + auth → Crissa → Cosy.

Anti-berserk:
  - drop turns stamped tts_echo_veto (WeSpeaker vs crissa_her_tts bank)
  - poll via=ear,stt only — Argus door prose stays on omni, not wake
  - one in-flight Crissa call; coalesce follow-ups
  - no STT mute timer — mouth identity is an embedding, not a clock
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher

from . import own_voice
from . import id_gate                         # round 17: the Security setting (On / ID-only / Off)
from . import callouts, connect, thinking
from . import cancels, heard_roll, over_line   # round 15: the cancel record, the heard roll, said over her line
from . import barge as asr_barge               # round 17: the first word over her line cuts her (crissa_ear/barge.py)
from .home import HOME, EAR, pack_script
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .house_commands import (
    command_may_execute,
    match_control,
    pending_phase,
    try_handle_utterance,
)
from .ttfs import (
    _clause_number_on,
    first_speakable_clause,
    rest_after_clause,
    sse_delta_text,
    spoken_from_reply,
)
from .hear_gate import classify_hear, is_scratch, is_user_pid, media_turn, wake_media
from .hear_gate import apply_join_speaker, join_least_trust_on   # round 46: a joined ask keeps its least-trusted voice
from .user_mode import (
    GUEST_SYSTEM,
    authed_owner,
    evaluate_turn,
    is_user_auth_only,
    load_house_mode,
    user_id_from_mode,
    user_voice_prints,
)
from .occupancy_llm import chat as occupancy_chat
from .wake import barge_match, stop_only, user_near_wake, wake_match
from .wake import _NAME as _HER_NAME
from . import speculate                       # round 17: her turn before his is final, her mouth after (speculate.py)
from . import her_words                       # round 18: her reply on the Wall's log as it streams (her_words.py)
from . import display_call                    # round 26: her display tool on a spoken turn (display_call.py)
from . import own_tools                       # cut 15: her own tools and recent days (own_tools.py)
from . import prespeak                        # round 26: the rest of a reply starts while her clause plays

# THE SESSION IS ITS SPEAKER'S (the User 2026-10-10, .101: with the 180 s session any speech in the room kept it open and
# was taken as her conversation - test 10's no-wake sentences became `session_utt` minutes after the last wake). A
# follow-up is taken (and extends the session) only when the voice print says it is the speaker who woke her, or the
# words name her. A session opened by an unknown voice takes follow-ups that are unknown too, never another person's.
# Kill EAR_BRIDGE_SESSION_OWNER_ONLY=0.
SESSION_OWNER_ONLY = os.environ.get("EAR_BRIDGE_SESSION_OWNER_ONLY", "1").strip() != "0"
# NO STAMP IS NOT ANOTHER VOICE, AND THE USER TAKES A GUEST'S SESSION (MM, .101 2026-10-11: a question after a bare
# "Hey Crissa" was dropped as session_skip_other). A short turn often has no voice print at all (own_voice `no_emb`),
# so the owner was whatever the short wake was stamped with: a None wake then refused the User's own stamped
# question, and a User wake refused his next unstamped one. Now a turn with no stamp may always follow (it keeps the
# speaker of the turn before it, as a session turn always has), and the User's own voice takes a session that an
# unknown voice (no stamp, or a scratch s<n>) opened. A known other voice is still not her conversation.
# Kill EAR_BRIDGE_SESSION_NONE_FOLLOWS=0 (the 2026-10-10 rule).
SESSION_NONE_FOLLOWS = os.environ.get("EAR_BRIDGE_SESSION_NONE_FOLLOWS", "1").strip() != "0"


def session_follow_ok(owner: str | None, speaker: str | None, text: str, user: str | None = None) -> bool:
    """May this turn continue the open session? Its owner's voice, a turn with no stamp, the User's voice in a
    session an unknown voice opened, or speech that names her. `user`: the User's id (user_mode.user_id_from_mode)."""
    if not SESSION_OWNER_ONLY or not id_gate.gating():   # round 17: ID-only / Off - the person at this computer
        return True
    if _HER_NAME.search(text or ""):
        return True
    if not SESSION_NONE_FOLLOWS:
        if owner:
            return bool(speaker) and speaker == owner
        return not speaker
    if not speaker:
        return True                                  # no stamp: never counted as another voice
    if owner and speaker == owner:
        return True
    return session_takes_owner(owner, speaker, user)


def session_takes_owner(owner: str | None, speaker: str | None, user: str | None) -> bool:
    """Does this turn make the User the session's owner? His own voice, in a session an unknown voice opened."""
    if not (SESSION_OWNER_ONLY and SESSION_NONE_FOLLOWS and speaker and user):
        return False
    return is_user_pid(speaker, user) and (not owner or is_scratch(owner))

# Session lamp for logs / health. Voice ID does not read this.
_hear_session = {"open": False, "ts": 0.0}


def _set_hear_session(open_: bool) -> None:
    _hear_session["open"] = bool(open_)
    _hear_session["ts"] = time.time()
    if not open_:
        id_gate.grace_end("session_end")       # round 20: the face grace lasts only while the session is open
        view_call_reset()                      # round 20: the next session opens on the close view again


def hear_session_open() -> bool:
    return bool(_hear_session.get("open"))

from .settings import port as _port_num, url as _port_url

_STT = _port_url("stt")
_PRESENCE_BASE = _port_url("presence")
_PLAYER = os.environ.get("CRISSA_PLAYER", _port_url("player")).rstrip("/")
STT_TURNS = os.environ.get("EAR_BRIDGE_TURNS", _STT + "/v1/turns?n=30&via=ear,stt")
STT_HEALTH = os.environ.get("EAR_BRIDGE_STT_HEALTH", _STT + "/health")
STT_VOICE = os.environ.get("EAR_BRIDGE_VOICE_LOCK", _STT + "/v1/house/voice")
PRESENCE = os.environ.get("EAR_BRIDGE_PRESENCE", _PRESENCE_BASE + "/v1/presence")
F2F_URL = os.environ.get(
    "EAR_BRIDGE_F2F",
    (
        PRESENCE[: -len("/v1/presence")] + "/v1/f2f"
        if PRESENCE.rstrip("/").endswith("/v1/presence")
        else _PRESENCE_BASE + "/v1/f2f"
    ),
)
# THE AGENT IS THE CONNECT ENDPOINT (2026-10-09): the house posted to one framework's gateway, read its key
# and persona from the framework's profile folder and named its session header. A pack reads all of that
# from <crissa home>/agent.json through crissa_bridge.connect, at call time, so a change on the Connect page
# reaches the next turn with no restart. Guest chat goes to the same endpoint with no persona and no tools.
# These two stay as names for the handful of places that log them; `_gateway()` is the live read.
CRISSA_URL = connect.endpoint("crissa")["url"]
CRISSA_KEY = connect.endpoint("crissa")["key"]

# The glass: the player serves /v1/screen (what is on the Wall) and /v1/mouth (is a line on the air).
SCREEN_URL = os.environ.get("EAR_BRIDGE_SCREEN", _PLAYER + "/v1/screen")
ACTION_URL = os.environ.get("EAR_BRIDGE_ACTION", _PLAYER + "/v1/action")
# The house's kiosk tab: at a session's end the house asked its home server to navigate the browser tab back to the
# Wall ("kiosk_wall"). A Crissa Wall has no kiosk to move - the desk Wall and the /wall page ARE the glass, and the
# player answers /v1/action only while Init runs - so the call 404'd at every session end (.101, 2026-10-10, with
# --no-desk and with the desk). It is made only where a kiosk is named: EAR_BRIDGE_ACTION set, or
# EAR_BRIDGE_WALL_HOME=1; a 404 from it is "no kiosk here", logged once, quietly.
WALL_HOME = (os.environ.get("EAR_BRIDGE_WALL_HOME", "1" if os.environ.get("EAR_BRIDGE_ACTION") else "0")
             .strip().lower() in ("1", "true", "yes", "on"))
_wall_home_absent = {"said": False}
# The mouth: is a line on the air right now from ANY spawner (the Composer's /v1/say, the player, this
# bridge)? `_speaking` only knows this bridge's own lines, so a Composer line at tts_sim 0.81 passed the
# echo gate as "idle_mouth" and hit the wall as a STRANGER with garbled words (the User 2026-09-11 "wrong
# words totally"). Fail open (False) when the player is away.
MOUTH_URL = os.environ.get("EAR_BRIDGE_MOUTH", _PLAYER + "/v1/mouth")
_HOUSE_AIR_CACHE = {"t": 0.0, "on": False}


def house_mouth_on_air(now: float | None = None, tail_s: float = 8.0, lead_s: float = 0.3) -> bool:
    """True inside [air_at − lead, air_until + tail] as the wall's /v1/mouth reports it.

    tail 8 s, not 0.6: the STT turn lands seconds after the line ends (measured
    2026-09-11: a 0.71 tts_sim Composer line still passed with a 0.6 s tail).
    The gate also needs is_mouth_echo (voice similarity), so his own words
    right after her line are not eaten by this window."""
    now = time.time() if now is None else float(now)
    if now - _HOUSE_AIR_CACHE["t"] < 0.4:
        return bool(_HOUSE_AIR_CACHE["on"])
    on = False
    try:
        m = _get(MOUTH_URL, timeout=0.6) or {}
        air_at = m.get("air_at")
        if air_at is not None:
            until = m.get("air_until")
            on = (float(air_at) - lead_s) <= now and (until is None or now <= float(until) + tail_s)
    except Exception:  # noqa: BLE001
        on = False
    _HOUSE_AIR_CACHE["t"] = now
    _HOUSE_AIR_CACHE["on"] = on
    return on
DOC_WINDOW = Path(os.environ.get("EAR_BRIDGE_DOC_WINDOW", str(pack_script("doc-window.sh"))))

# Her mouth is the pack's own speaker (scripts/house-speak.py ships in the player package).
HOUSE_SPEAK = Path(os.environ.get("HOUSE_SPEAK", str(pack_script("house-speak.py"))))
# HER LINES RUN ON THIS BRIDGE'S PYTHON, NOT ON WHATEVER `python3` THE PATH FINDS (2026-10-13, round 17 on .101: the
# bridge runs on the ear's env, Python 3.12.15; that machine's system python3 is 3.14.4). The player's own lines
# (the tester, Setup) run house-speak on the player's interpreter (house-conductor: sys.executable); a reply ran on
# another one, whose pipe buffer is 131072 bytes against 3.12's 4096 (scripts/house-speak.py now flushes every write,
# so that alone no longer moves her voice) and whose packages are that machine's own (house-speak's lip clock,
# lib/mouth_timing, needs numpy; the ear's env carries it). EAR_BRIDGE_SPEAK_PYTHON names another; `python3` puts
# the PATH lookup back.
SPEAK_PYTHON = os.environ.get("EAR_BRIDGE_SPEAK_PYTHON", "").strip() or sys.executable or "python3"
CONDUCTOR_CUE = os.environ.get("EAR_BRIDGE_CONDUCTOR_CUE", _PLAYER + "/v1/cue")
CONDUCTOR_STATE = os.environ.get("EAR_BRIDGE_CONDUCTOR_STATE", _PLAYER + "/v1/state")
CONDUCTOR_VOCATIVE = os.environ.get("EAR_BRIDGE_CONDUCTOR_VOCATIVE", _PLAYER + "/v1/vocative")
CONDUCTOR_GREET_CANCEL = os.environ.get("EAR_BRIDGE_CONDUCTOR_GREET_CANCEL", _PLAYER + "/v1/greet-cancel")
# THE CLOSE VIEW IS THE CONNECTION (round 20, the User 2026-10-14: "close view looks phenominal but i think we want it to
# be the quick connect screen. She is too busy for f2f on a longer call. No switchback from mutitasking. Close will be
# only for initial connection screen"). The wake's close-up (cue_user_talk) is the connection screen. Once she starts on
# his request (dispatch: a real turn, not a resume), she moves to the multitasking view - the player's /v1/ft-desk, the
# same move a tool call makes on the house - no sooner than CLOSE_CONNECT_MIN_S after the close-up began, and stays
# there for the rest of the call: a name-call inside the same session is answered where she is, never a new close-up.
# The session's end resets it (_set_hear_session), so the next wake opens close again. On a turn with no close-up the
# player's answer is "skipped" and nothing moves. Kill EAR_BRIDGE_CLOSE_CONNECT_ONLY=0 (round 19: close until a tool).
CLOSE_CONNECT_ONLY = os.environ.get("EAR_BRIDGE_CLOSE_CONNECT_ONLY", "1").strip().lower() not in ("0", "off", "false", "no")
CLOSE_CONNECT_MIN_S = float(os.environ.get("EAR_BRIDGE_CLOSE_CONNECT_MIN_S", "2.0") or 0)
CONDUCTOR_FT_DESK = os.environ.get("EAR_BRIDGE_CONDUCTOR_FT_DESK", _PLAYER + "/v1/ft-desk")
_CALL_VIEW: dict = {"close_at": 0.0, "moved": False}


def view_call_reset() -> None:
    _CALL_VIEW.update(close_at=0.0, moved=False)


def view_close_opened(now: float | None = None) -> None:
    """The wake's close-up was cued: the call starts on the close view."""
    _CALL_VIEW.update(close_at=time.time() if now is None else float(now), moved=False)


def view_keeps_multitask() -> bool:
    """This call already moved to the multitasking view: a name-call does not bring the close-up back."""
    return CLOSE_CONNECT_ONLY and bool(_CALL_VIEW.get("moved"))


def view_to_multitask(why: str = "request", post=None, sleep=time.sleep, spawn=True) -> bool:
    """She has started on his request: to the multitasking view, once per call (see THE CLOSE VIEW IS THE CONNECTION).
    True when the move was sent now (on a thread unless spawn=False)."""
    if not CLOSE_CONNECT_ONLY or _CALL_VIEW.get("moved"):
        return False
    _CALL_VIEW["moved"] = True
    t0 = float(_CALL_VIEW.get("close_at") or 0.0)
    wait = max(0.0, t0 + CLOSE_CONNECT_MIN_S - time.time()) if t0 else 0.0

    def _go() -> None:
        if wait > 0:
            sleep(wait)
        try:
            r = (post or _post_json)(CONDUCTOR_FT_DESK, {"by": why}, timeout=3.0) or {}
        except Exception as e:  # noqa: BLE001 - a missing player is no fault of the turn
            r = {"error": type(e).__name__}
        _log({"event": "view_multitask", "why": why, "waited_s": round(wait, 2), "dest": r.get("dest"),
              "skipped": r.get("skipped"), "now": r.get("now"), "error": r.get("error")})
    if spawn:
        threading.Thread(target=_go, daemon=True, name="view-multitask").start()
    else:
        _go()
    return True
# THE READING VIEW (the User 2026-10-15 on .101: "I prefer this view over the other reader. Could we make this the
# preferred for reading?"): an ask to read aloud keeps her on the close-up she opened the call on ("close", the default)
# unless the person's preference is "multitask". Read at the moment of the move, so a start costs nothing: env
# EAR_BRIDGE_READING_VIEW, else <home>/reading-view.json {"view": "close"|"multitask"}. Kill: EAR_BRIDGE_READING_VIEW=multitask.
READING_VIEWS = ("close", "multitask")


def reading_view(env=None, home: Path | None = None) -> str:
    env = os.environ if env is None else env
    v = str(env.get("EAR_BRIDGE_READING_VIEW") or "").strip().lower()
    if v not in READING_VIEWS:
        try:
            d = json.loads((Path(home or HOME) / "reading-view.json").read_text(encoding="utf-8"))
            v = str((d or {}).get("view") or "").strip().lower()
        except (OSError, ValueError, AttributeError):
            v = ""
    return v if v in READING_VIEWS else "close"


def view_for_request(user: str, env=None, home: Path | None = None) -> bool:
    """Move to the multitasking view for this request? Not for an ask to read aloud while the preference is close."""
    if read_aloud_ask(user) and reading_view(env, home) == "close":
        _log({"event": "view_kept_close", "why": "reading view: close"})
        return False
    return True


CONDUCTOR_COMMS = os.environ.get("EAR_BRIDGE_CONDUCTOR_COMMS", _PLAYER + "/v1/comms")
CONDUCTOR_HOME = os.environ.get("EAR_BRIDGE_CONDUCTOR_HOME", _PLAYER + "/v1/home")
DISPLAY_TOOL_URL = os.environ.get("EAR_BRIDGE_DISPLAY_TOOL", _PLAYER + "/v1/display-tool")   # round 26
OWN_TOOL_GUARD = os.environ.get("EAR_BRIDGE_OWN_TOOL_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")
OWN_TOOL_ROUNDS = int(os.environ.get("EAR_BRIDGE_OWN_TOOL_ROUNDS", "3") or 0)   # cut 15: asks after a round of her own tools
# Desk FT lean-in. Vocative-from-work cues wake, not these.
USER_TALK_KEY = "laptop-closeup-eyes-down"
USER_TALK_ENTER = "laptop-closeup-enter"
USER_TALK_ENTER_SEC = 6.04
VOCATIVE_KEY = "wake"
PA_PLAY = Path(os.environ.get("HOUSE_ANNOUNCE_PLAY", str(HOME / "announce-play.json"))).expanduser()
# Voice-Only wall: type replies when the voice is busy/down (the User 2026-08-06).
# auto = try voice then chalk; type = always chalk; voice = voice only
REPLY_MODE = os.environ.get("EAR_BRIDGE_REPLY_MODE", "auto").strip().lower()
# Where a typed reply goes when the voice is down ("" = nowhere: the house chalked on its face shell)
FACE_CHALK = os.environ.get("EAR_BRIDGE_FACE_CHALK", "").rstrip("/")
# The voice service's health ("" = ask nothing: house-speak picks the voice and falls back by itself)
# and the stop that cuts a line on the air: the player's /v1/stop (house-speak's own process is
# killed first; this reaches a line another spawner started).
COSY_HEALTH = os.environ.get("COSY_HEALTH", os.environ.get("EAR_BRIDGE_VOICE_HEALTH", ""))
COSY_ABORT = os.environ.get("COSY_ABORT", os.environ.get("EAR_BRIDGE_VOICE_ABORT", _PLAYER + "/v1/stop"))


def _crissa_voice() -> str:
    """Her voice pin for house-speak (EAR_BRIDGE_CRISSA_VOICE; else "crissa": lib/crissa_tts picks the engine)."""
    return os.environ.get("EAR_BRIDGE_CRISSA_VOICE", "").strip() or "crissa"


CRISSA_VOICE = _crissa_voice()


def _gateway(profile: str) -> dict:
    """HTTP target + mouth pin: the Connect endpoint, read now, for every profile the wake grammar knows.
    The house gave each crew member its own gateway; a pack has one agent, and who was addressed rides in
    the prompt overlay (`_overlay_for`), as a summon always did."""
    p = (profile or "crissa").strip().lower() or "crissa"
    if p == "argus":
        p = "crissa"           # the camera look is Crissa's
    e = connect.endpoint(p)
    if p == "crissa":
        e["voice"] = CRISSA_VOICE
    return e


def _overlay_for(http_profile: str, addressed: str | None) -> str:
    if addressed:
        name = addressed.strip().capitalize()
        who = addressed.strip().lower()
        return (
            f" User addressed {name} by name. You are Crissa, not {name}. "
            "Do not impersonate them or speak as them. "
            "If tools are allowed this turn: file one card on the board for "
            f"{who} with the user's residual ask. "
            f"One spoken sentence that {name} is on it. They log as themselves "
            "and will page you. If tools are locked: say you heard them and "
            "need User voice to send the work."
        )
    if http_profile == "ada":
        return (
            " Free-air via the house ear. You are Ada, board owner — not Crissa. "
            "Speak one or two short sentences. Presence writes are not yours. "
            "Docker consultants only when tools are allowed this turn."
        )
    if http_profile == "argus":
        return (
            " Free-air via the house ear. Argus is gone. You are Crissa. "
            "If they ask about the camera or the room, look yourself. "
            "Do not write occupancy. One or two short sentences."
        )
    return ""


def _target_from_meta(meta: dict) -> tuple[dict, str | None]:
    """Return (gateway, addressed) for this turn.

    summon → Crissa HTTP, addressed = named crew.
    direct ada/argus → their gateway, same ear, User does not switch apps.
    """
    route = str(meta.get("route") or "direct").strip().lower()
    named = str(meta.get("profile") or "crissa").strip().lower() or "crissa"
    addressed = meta.get("addressed")
    if isinstance(addressed, str):
        addressed = addressed.strip().lower() or None
    else:
        addressed = None
    if route == "summon":
        return _gateway("crissa"), addressed or named
    return _gateway(named), None


def _wake_meta(wm: dict, t: dict, session_id: str) -> dict:
    route = str(wm.get("route") or "direct")
    profile = str(wm.get("profile") or "crissa")
    return {
        "kind": wm.get("kind"),
        "person_id": t.get("person_id"),
        "stamp": t.get("stamp"),
        "text": t.get("text"),
        "session_id": session_id,
        "profile": profile,
        "route": route,
        "addressed": profile if route == "summon" else None,
    }
# How often ear-bridge re-polls STT turns. Not random — fixed interval.
# Lower = faster log/dispatch after a turn lands (CPU/HTTP cost rises slightly).
POLL = float(os.environ.get("EAR_BRIDGE_POLL", "0.2"))
# Ear is VAD-endpointed (frame 0.3s + hangover). Turns land as whole utterances.
# Gather stitches residual ask chunks; bare "Zoe." does NOT wait 6s for a
# follow-on (the User 2026-08-12: that BARE_WAKE_SEC hold was deleted).
CHUNK_PERIOD = float(os.environ.get("EAR_BRIDGE_CHUNK_PERIOD", "2.0"))
GATHER_SEC = float(os.environ.get("EAR_BRIDGE_GATHER_SEC", "2.5"))
# Hard ceiling on how far a still-talking the User can push the window out.
GATHER_MAX = float(os.environ.get("EAR_BRIDGE_GATHER_MAX", "9.0"))
# Trailing silence (s) at the end of a block that means "he actually stopped".
TAIL_SILENCE = float(os.environ.get("EAR_BRIDGE_TAIL_SILENCE", "0.25"))
# Quiet needed before dispatching when the block ended cleanly — keeps short
# commands snappy instead of paying the full GATHER_SEC wait.
# This starts the LLM (predict). It is not when Cosy starts.
FLUSH_FAST = float(os.environ.get("EAR_BRIDGE_FLUSH_FAST", "1.2"))
# Quiet after last user speech before Cosy starts a *reply*. TTFS still
# streams the first clause during the wait; paplay holds for this many
# seconds so a thinking pause is not a non-sequitur. Vocative Yeah? uses
# skip_quiet (sit-up already covers). the User 2026-09-15: 2s, keep TEN VAD.
SPEAK_QUIET_SEC = float(os.environ.get("EAR_BRIDGE_SPEAK_QUIET_SEC", "2.0"))
# Cosy argv used to slice at 500 chars — that chopped reports. 4096 tok ≈ 16k chars.
SPEAK_CHARS = int(os.environ.get("EAR_BRIDGE_SPEAK_CHARS", "16000"))
# A LONG ANSWER IS READ IN PIECES (round 14, 2026-10-12, .101: "Cut at 799 of 6667 characters", a Moby-Dick reading
# stopped after 53 s). house-speak cuts any ONE line at its 800-character voice cap (scripts/house-speak.py
# VOICE_TEXT_CAP; the house's own bridge hands it a whole reply and is cut there too), so the bridge hands a long answer
# over a piece at a time, each ending on a sentence (else a word) and at most READ_CHUNK characters, up to SPEAK_CHARS in
# all - the bound the house already had. Between pieces a vocative still stops her (abort_mouth moves the cancel token).
# The size is the talk dial speak.piece_chars (config/crissa-talk.json). Kill EAR_BRIDGE_READ_CHUNK=0 (one line, as before).
def _piece_chars() -> int:
    """The piece size: EAR_BRIDGE_READ_CHUNK, else the talk dial speak.piece_chars (lib/crissa_talk, the speaker's own
    dial, so the bridge and house-speak cut alike), else 760."""
    raw = os.environ.get("EAR_BRIDGE_READ_CHUNK")
    if raw is not None and raw.strip():
        try:
            return int(float(raw))
        except ValueError:
            pass
    try:
        from .home import PACK_ROOT
        import sys as _sys
        lib = str(PACK_ROOT / "lib")
        if lib not in _sys.path:
            _sys.path.insert(0, lib)
        import crissa_talk
        return int(crissa_talk.piece_size())
    except Exception:  # noqa: BLE001 - no talk settings here: the default
        return 760


READ_CHUNK = _piece_chars()
# ... AND READ WHILE IT IS STILL ARRIVING (round 14, .101: "Understood." in 680 ms, then 45 s of silence while the model
# wrote 6,667 characters): once her first clause is said and the answer past it holds READ_ENGAGE characters, the rest
# is read as it streams in - first piece at READ_FIRST characters, then full pieces - and the reply is not sent through
# a revise turn (a follow-up that is not a vocative waits, as behind any line of hers). Kill EAR_BRIDGE_READ_STREAM=0.
READ_STREAM = os.environ.get("EAR_BRIDGE_READ_STREAM", "1").strip().lower() not in ("0", "false", "no", "off")
READ_ENGAGE = int(os.environ.get("EAR_BRIDGE_READ_ENGAGE", "300"))
ABANDON_HELD = os.environ.get("EAR_BRIDGE_ABANDON_HELD", "1").strip().lower() not in ("0", "false", "no", "off")
READ_FIRST = int(os.environ.get("EAR_BRIDGE_READ_FIRST", "240"))
# WHAT SHE SAID IS IN THE NEXT TURN (round 14, .101: "What book was that?" right after a Moby-Dick reading was answered
# with another book). The house's agent keeps each conversation itself (its session header); an endpoint with no
# session header (a plain chat API: xAI, Claude, a local server) remembers nothing, so the bridge sends the last
# HISTORY_TURNS exchanges (4) of the owner's own turns with each question - his words and what she actually said, each
# clipped to HISTORY_MSG_CHARS (600: the User, "she keeps up pretty well when we don't have too much in her context") -
# while the last one is under HISTORY_IDLE_S old. A reading is kept as its opening and where she stopped, so "what book
# was that?" and "go on" have what they need. A guest's turns are never kept or sent. Kill EAR_BRIDGE_HISTORY=0.
HISTORY = os.environ.get("EAR_BRIDGE_HISTORY", "1").strip().lower() not in ("0", "false", "no", "off")
HISTORY_TURNS = int(os.environ.get("EAR_BRIDGE_HISTORY_TURNS", "4"))
HISTORY_IDLE_S = float(os.environ.get("EAR_BRIDGE_HISTORY_IDLE_S", "900"))
HISTORY_MSG_CHARS = int(os.environ.get("EAR_BRIDGE_HISTORY_MSG_CHARS", "600"))
# 15s gap after the last speaker's sentence (him or her) for a tag-on.
# Then session_end (camera home + Speaker None). Wake word to reopen.
# Do not drop mid-dispatch / mid-mouth — Cosy can run longer than this.
# EAR_BRIDGE_LISTEN_SEC is the old unit name; SESSION_SEC wins if both set.
SESSION_SEC = float(
    os.environ.get("EAR_BRIDGE_SESSION_SEC")
    or os.environ.get("EAR_BRIDGE_LISTEN_SEC")
    or "60"
)


def session_idle_should_drop(
    now: float,
    session_until: float,
    *,
    live: bool = False,
    gather: bool = False,
) -> bool:
    """True when the idle clock may close the session.

    Comms Hold is not a freeze: it only made session_until later (idle_sec).
    live is session_live(now); gather is an in-flight gather window.
    """
    if not session_until or now <= session_until:
        return False
    if live or gather:
        return False
    return True
_session_extend_until = 0.0
# Set by POST /v1/session/pause and /v1/session/open (wall chip toggle).
# The poll loop consumes them so hold_session/drop_session stay the only
# open/close. Open is the chip equivalent of a bare wake word — no extra
# utterance required. It does not speak "Here."
_session_pause_req = False
_session_open_req = False
# POST /v1/session/hold {on: true|false}. None = no pending request.
# Consumed in the poll like pause/open. Name is comms_hold everywhere
# (not hold_session, not tool-hold, not hold_flourishes). Hold stops only
# the idle expiry; explicit pause still drop_session, which clears it.
_session_hold_req = None
_comms_hold = False
# Comms Hold is a DURATION, not a freeze (Grok plan 2026-09-09, the User: "Held
# and countdown from 60 min"): while held the quiet window is COMMS_HOLD_SEC
# instead of SESSION_SEC. Speech re-arms it like any session. Both glasses
# draw HELD mm:ss from comms_hold_until (= session_until while held) on
# /health. The idle drop is the same drop_session as BAU and clears the hold.
COMMS_HOLD_SEC = float(
    os.environ.get("EAR_BRIDGE_COMMS_HOLD_SEC")
    or os.environ.get("EAR_BRIDGE_HOLD_SEC")
    or "3600"
)
_session_until_pub = 0.0   # loop mirror of session_until for /health


def idle_sec(comms_hold: bool) -> float:
    """The quiet window: 60 s BAU, 3600 s while Comms Hold is on."""
    return COMMS_HOLD_SEC if comms_hold else SESSION_SEC


# HER QUESTION WAITS FOR THE ANSWER (round 26, .101 2026-10-16: "Looks like you're near Charlotte ... does that sound
# right?" and the session closed 20 s later with his "yes" unheard; the 20 s window caught him three times that morning).
# A reply of hers that ends on a question keeps the session QUESTION_SEC after her voice ends (the 20 s window stays for
# every other reply, the User's own 2026-10-14 call). Kill: EAR_BRIDGE_QUESTION_SEC=0.
QUESTION_SEC = float(os.environ.get("EAR_BRIDGE_QUESTION_SEC", "45") or 0)


def asked_question(said: str) -> bool:
    return str(said or "").rstrip(" \"')\u201d").endswith("?")


def after_reply_sec(said: str, comms_hold: bool) -> float:
    """The quiet window after one of her replies: longer when it asked them something."""
    base = idle_sec(comms_hold)
    _ASKED["open"] = asked_question(said)          # her question waits for an answer (NO REPLY below)
    if QUESTION_SEC > 0 and _ASKED["open"]:
        return max(base, QUESTION_SEC)
    return base


# NO REPLY (round 26, .101 2026-10-16: she asked "What can we do today?", heard nothing, and the session closed in
# silence at 09:31:06; the User: "handle no reply"). When the session ends on a question of hers that no turn answered,
# she says one short line that she is still there and how to reach her, and the log says the same. Never twice for one
# question. Kill: EAR_BRIDGE_NO_REPLY_LINE=0.
_ASKED: dict = {"open": False}
NO_REPLY_ON = os.environ.get("EAR_BRIDGE_NO_REPLY_LINE", "1").strip().lower() not in ("0", "false", "no", "off")


def no_reply_line(name: str = "") -> str:
    """Never her own name aloud (her voice must not wake her); the log line under it names her."""
    return "I didn't hear an answer. I'm here whenever you need me."


def note_turn_answered() -> None:
    _ASKED["open"] = False


# A LATE ANSWER STILL COUNTS (round 27, .101 2026-10-16: she asked "What's your favorite thing to do on a sunny day?",
# said her no-reply line 45 s later, and his "Sorry, I do like to go to the beach..." 24 s after that was dropped as
# paused; the User: "it just goes to paused"). For LATE_ANSWER_S after her no-reply line the next turn is heard as in
# the session, once - the reply is clearly to her question - without her name. Kill: EAR_BRIDGE_LATE_ANSWER_S=0.
LATE_ANSWER_S = float(os.environ.get("EAR_BRIDGE_LATE_ANSWER_S", "60") or 0)


def late_answer_open(now: float | None = None) -> bool:
    """True once, within LATE_ANSWER_S of her no-reply line: the turn being heard now answers her question."""
    until = float(_ASKED.get("late_until") or 0.0)
    now = time.time() if now is None else float(now)
    if LATE_ANSWER_S <= 0 or not until or now > until:
        return False
    _ASKED["late_until"] = 0.0
    return True


def say_no_reply(name: str, speak=None) -> str:
    """At a session's end: her line when her question went unanswered ("" when nothing is said)."""
    if not (NO_REPLY_ON and _ASKED.get("open")):
        return ""
    _ASKED["open"] = False
    _ASKED["late_until"] = time.time() + LATE_ANSWER_S
    line = no_reply_line(name)
    threading.Thread(target=(speak or (lambda t: speak_blocking(t, skip_quiet=True))), args=(line,),
                     name="no-reply", daemon=True).start()
    return line
# voice_on() is an HTTP GET and the loop runs at POLL=0.2s. Cache it, and keep
# the edge separately: a Voice toggle must land in PAUSED on the EDGE, not on
# whatever someone happens to say next (the User 2026-08-28).
_voice_cache_val: bool | None = None
_voice_cache_ts = 0.0
# Spoken twin of the wall's LISTENING chip. In-session only — pausing an
# already-paused ear is a no-op, and the wake word is the way back.
_VOICE_PAUSE = re.compile(r"(?i)^\W*(?:voice[\s,\-]*pause|pause[\s,\-]*voice)\W*$")
ECHO_RATIO = float(os.environ.get("EAR_BRIDGE_ECHO_RATIO", "0.55"))
LOG = Path(
    os.environ.get(
        "EAR_BRIDGE_LOG",
        str(HOME / "logs" / "ear-bridge.jsonl"),
    )
).expanduser()
SPEAK_LOG = Path(
    os.environ.get(
        "HOUSE_SPEAK_LOG",
        str(HOME / "logs" / "house-speak.jsonl"),
    )
).expanduser()
TTS_GATE = Path(
    os.environ.get(
        "HOUSE_TTS_GATE",
        str(HOME / "tts-gate.json"),
    )
).expanduser()


def own_voice_gate_paths() -> list[Path]:
    """The gate files that name her lines: this bridge's (TTS_GATE, HOUSE_TTS_GATE when set) and house-speak's
    (`house-tts-gate.json`, lib/audio_sink's name in the state home)."""
    out = [TTS_GATE, HOME / "tts-gate.json", HOME / "house-tts-gate.json"]
    return list(dict.fromkeys(out))          # round 14: house-speak's file is read whatever HOUSE_TTS_GATE names


HOUSE_MEMORY = Path(
    os.environ.get(
        "HOUSE_MEMORY_SCRIPT",
        str(pack_script("house_memory.py")),
    )
).expanduser()

# Free-air overlay only - the agent keeps its own persona (connect.system_prompt). Keep short.
SYSTEM = (
    "Free-air channel: every word is spoken aloud in clear American English only. "
    "Never use Chinese or any other language unless the User explicitly asks for it. "
    "Conversational, contractions, little expression — not a briefing. "
    "No markdown or tool dumps. "
    "Presence: when asked who/how many, state named people first, then unknowns. "
    # the User 2026-08-12: CALLOUT = tool-result info only (hard rule).
    "CALLOUT RULE: When you use any tool this turn, your spoken reply is a "
    "CALLOUT of the tool result only — pure information (counts, names, "
    "states, facts). Lead with the fact. One or two short sentences max. "
    "Forbidden in callouts: tool names, 'I checked', 'board survey complete', "
    "'looking up', 'here is the full picture', walking him through the screen, "
    "or offering to go through details. "
    "Example callout: 'Zero ready, zero running. Twenty-one done today.' "
    "Tools run silently; the callout is the only speech after tools. "
    "Board survey = your board tool; new work = one card on the board, "
    "to the orchestrator unless the user names someone. "
    "Long material: one-line callout + put body on the wall (team_display). "
    "No tool this turn: still answer briefly in plain chat. "
    "FIRST SENTENCE: confirm you understood the ask in one short clause, "
    "then the answer. Cosy starts on that sentence. Do not talk over him. "
    "If he keeps talking after that sentence, do not finish a guess that "
    "no longer matches — the remainder must follow what he actually said. "
    "AUTH: a camera glance (door entry ID) does not drop User AUTH. "
    "Hold it until you cannot find him — not because the frame is empty "
    "or someone else is in the doorway."
)

REVISE_SYSTEM = (
    "Free-air channel: every word is spoken aloud in clear American English only. "
    "You already said the first sentence aloud. Do not repeat it. "
    "He kept talking after that sentence. Speak ONLY the corrected remainder. "
    "If the first sentence is now wrong, one short correction then the real "
    "answer. No markdown, no tool dumps, no Chinese."
)

_speak_lock = threading.Lock()
_speaking = False
_last_reply = ""
# What she said and when, for own_voice rule 1 (2026-09-30: she answered her own replies for a minute).
_SAY = own_voice.SayLog()
_dispatch_lock = threading.Lock()
_dispatch_busy = False
_pending: tuple[str, dict] | None = None
_speak_proc: subprocess.Popen | None = None
_cancel_token = 0
_last_user_speech = 0.0


# A FINISHED ANSWER IS KEPT WHEN THE FOLLOW-UP ONLY RESTATES THE ASK (the User 2026-09-28):
# at 10:41 "show me the board" was answered in full, then "Her display is a display tool."
# landed while the first clause waited for quiet, and predict_revise re-sent both lines as a
# new turn - the finished answer was never spoken and the re-run sent the helper off course.
# A short line with no question, correction or new ask is a restatement: keep the answer.
# Kill EAR_BRIDGE_KEEP_ANSWER=0 (every follow-up re-asks, as before).
KEEP_ANSWER = os.environ.get("EAR_BRIDGE_KEEP_ANSWER", "1").strip().lower() not in ("0", "false", "no", "off")
_NEW_ASK = re.compile(
    r"\?|\b(no|not|don'?t|actually|instead|wait|rather|wrong|also|then|next|another|other|"
    r"different|stop|cancel|change|switch|what|how|why|who|where|when|can you|could you|"
    r"please|and)\b",
    re.I,
)


def followup_restates(extra: str, max_words: int = 12) -> bool:
    """True when a follow-up line adds nothing to the ask it follows."""
    t = (extra or "").strip()
    words = re.findall(r"[\w']+", t)
    if not words or len(words) > max_words:
        return False
    return not _NEW_ASK.search(t)


def coalesce_followup(
    old: tuple[str, dict] | None,
    user: str,
    meta: dict,
    *,
    vocative: bool,
) -> tuple[str, dict]:
    """Append a continuation onto the in-flight line. A vocative replaces."""
    user = (user or "").strip()
    meta = dict(meta or {})
    if old is None or vocative or not user:
        return user, meta
    old_u = (old[0] or "").strip()
    if not old_u:
        return user, meta
    joined = f"{old_u} {user}".strip()
    m = dict(old[1] or {})
    m.update({k: v for k, v in meta.items() if v is not None})
    m["text"] = joined
    apply_join_speaker(m, old[1], meta)       # round 46: never a more trusted voice (hear_gate.join_speaker)
    return joined, m


def remainder_revise_user(spoken: str, extra: str, draft_rest: str) -> str:
    """User turn for a remainder edit after the first clause already aired."""
    parts = [f'Already said aloud: "{(spoken or "").strip()}"']
    if (extra or "").strip():
        parts.append(f'He continued: "{extra.strip()}"')
    if (draft_rest or "").strip():
        parts.append(f'Draft remainder (may be wrong): "{draft_rest.strip()}"')
    parts.append("Speak only the corrected remainder. Do not repeat the first sentence.")
    return "\n".join(parts)


def _pending_text() -> str:
    with _dispatch_lock:
        if not _pending:
            return ""
        return str(_pending[0] or "").strip()


def _take_pending() -> tuple[str, dict] | None:
    global _pending
    with _dispatch_lock:
        nxt = _pending
        _pending = None
        return nxt


def _get(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ear_still_hearing() -> bool:
    """True while the ear is packing speech or Audio8 is still decoding it.

    speak_blocking used to wait only on the last completed turn. ASR of a
    long Shure take is 1–5 s with the capture loop stuck, then Cosy started
    on a stale quiet flag and talked over him.
    """
    try:
        d = _get(STT_HEALTH, timeout=0.35)
    except Exception:
        return False
    e = d.get("ear") if isinstance(d, dict) else None
    if not isinstance(e, dict):
        return False
    return bool(e.get("in_utterance") or e.get("decoding"))


def user_speaking_now() -> bool:
    """rail-stt's early 'the user is speaking' flag (RAIL_STT_USER_FLAG, not on main yet): False when absent,
    so until it lands a callout is never dropped for him speaking."""
    try:
        d = _get(STT_HEALTH, timeout=0.35)
    except Exception:
        return False
    e = d.get("ear") if isinstance(d, dict) else None
    return bool(isinstance(e, dict) and e.get("user_speaking") is True)


def ear_still_decoding() -> bool:
    """True only while ASR is in flight. Keyboard VAD is not this."""
    try:
        d = _get(STT_HEALTH, timeout=0.35)
    except Exception:
        return False
    e = d.get("ear") if isinstance(d, dict) else None
    if not isinstance(e, dict):
        return False
    return bool(e.get("decoding"))


def request_session_look(
    *,
    on: bool,
    person_id: str | None = None,
    reason: str | None = None,
) -> dict:
    """Snap the door camera when a Crissa session opens. Home on session_end.

    Fire-and-forget from the poll loop. Presence ignores leftover STT/impulse
    looks; this POST is the look trigger.
    """
    on = bool(on)
    body: dict = {
        "on": on,
        "look": on,
        "reason": reason or ("session_open" if on else "session_end"),
        "person_id": person_id,
    }
    if on:
        # Session look is the User desk (hour 6). Do not copy beam_hour —
        # that kept the camera on the door while he said good morning.
        # Secure watches the door: a wake must not FaceFinder the desk.
        try:
            p = _get(PRESENCE, timeout=0.45) or {}
            lamp = str(p.get("visit_lamp") or "").strip().lower()
            # Law 1: skip FaceFinder only while Secure. Held AUTH / a fresh
            # visor lock are not a skip (the User 2026-09-05; sticky-AUTH).
            # Presence ff_at_beam_blocked is sage-only; this matches it.
            if lamp == "sage":
                return {
                    "ok": True,
                    "skipped": "secure",
                    "look": False,
                    "f2f": False,
                }
            snd = p.get("sound") if isinstance(p.get("sound"), dict) else {}
            if not body.get("person_id"):
                pid = str(snd.get("person_id") or "").strip().lower() or None
                if pid:
                    body["person_id"] = pid
        except Exception:
            pass
    try:
        return _post_json(F2F_URL, body, timeout=1.5)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}:{e}"[:160]}


def _env_http_timeout(name: str, default: float) -> float | None:
    """urllib timeout seconds. <=0 / none / inf = no deadline (keep the call)."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        v = default
    else:
        s = str(raw).strip().lower()
        if s in ("none", "inf", "infinite"):
            return None
        try:
            v = float(s)
        except ValueError:
            v = default
    if v <= 0:
        return None
    return v


def _post_json(
    url: str,
    payload: dict,
    *,
    key: str = "",
    timeout: float | None = 90.0,
    session_id: str | None = None,
) -> dict:
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    hdr = connect.session_header()
    if session_id and hdr:
        headers[hdr] = session_id
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# THE ANSWER AFTER A TOOL IS THE REPLY (the User 2026-09-30, "find why the final answer wasn't spoken").
# The gateway streams one text for the whole turn: what she says while reaching for a tool ("good catch,
# let me pull up Scout's activity ... Let me pull up the logs"), then, after the tool, her answer. The
# callout cap keeps the FIRST two sentences of that text, so two sentences of preamble were the whole
# spoken reply and the answer was cut: 07:58 on 09-30 he heard only the preamble of a 782-char answer;
# 9 of 13 turns shaped "text, tool, text" since 09-27 lost their answer this way. The stream marks each
# tool (an `event:` line naming a tool call), so the reply is the text after the LAST mark, the cap counts the
# answer's sentences, and the first clause she already said is not matched against it (it is not a
# prefix of the answer; a failed match there is silence). Kill EAR_BRIDGE_ANSWER_AFTER_TOOL=0.
ANSWER_AFTER_TOOL = os.environ.get("EAR_BRIDGE_ANSWER_AFTER_TOOL", "1").strip().lower() not in ("0", "false", "no", "off")
# A TOOL'S ANSWER SURVIVES HIS TALKING OVER THE WAIT (armed by the User 2026-09-30: "keep a held answer as
# one short line spoken after I stop talking"). When a turn ran a tool the thing is already done (the
# board is up), and a long wait is when he talks: at 08:21 "The board's up on the screen." was dropped because
# he spoke during the 25 s, and his words were re-asked as one turn with the first. Now the finished
# after-tool answer is kept as ONE short line (held_line), spoken once he is quiet (speak_blocking waits
# for quiet), and his words stay pending and become the next turn. Kill EAR_BRIDGE_KEEP_TOOL_ANSWER=0.
KEEP_TOOL_ANSWER = os.environ.get("EAR_BRIDGE_KEEP_TOOL_ANSWER", "1").strip().lower() not in ("0", "false", "no", "off")
HELD_LINE_MAX = 140


def held_line(reply: str, limit: int = HELD_LINE_MAX) -> str:
    """The first sentence of a kept answer, cut at a word if it runs long."""
    t = re.sub(r"\s+", " ", (reply or "").strip())
    m = re.match(r"(.+?[.!?])(?:\s|$)", t)
    line = (m.group(1) if m else t).strip()
    if len(line) > limit:
        cut = line.rfind(" ", 0, limit)
        line = line[:cut if cut > 40 else limit].rstrip(" ,;:-") + "."
    return line


# SHE SAYS FILED / SHOWN / DONE ONLY WHEN A TOOL REPLY CONFIRMS IT (armed by the User 2026-09-30). Her
# hands' result ends with `confirmed:` lines built from what the tools actually replied (crissa
# lib/hands_confirm.py). Before a reply is spoken, a sentence that claims an effect with no `confirmed:`
# line behind it in THIS SESSION's tool replies is dropped and "I could not confirm that." is said instead
# (09-28 "I've filed it as a card for Ada", 09-30 "Filed it as a research card with Ada": no card).
# The session, not the turn: "the board's up on the screen" one turn after her hands put it up is true.
# A session with no confirming reply confirms nothing. If the turn ran a tool but its replies cannot be read yet,
# the reply passes and `claim_unverifiable` is logged - never a false "could not confirm" on a real
# filing. Kill EAR_BRIDGE_CLAIM_CHECK=0.
CLAIM_CHECK = os.environ.get("EAR_BRIDGE_CLAIM_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")
_HANDS_CONFIRM: dict = {}


# Tool events seen in the agent's stream: 0 after a turn that ran tools = the stream carries none, and the
# callouts (L) stay quiet with a mark on /health instead of a phrase that never comes.
_TOOL_EVENTS = {"seen": 0}


def _hands_confirm():
    if "mod" not in _HANDS_CONFIRM:
        mod = None
        try:
            import importlib.util
            path = Path(__file__).resolve().parents[3] / "lib" / "hands_confirm.py"
            if path.is_file():
                spec = importlib.util.spec_from_file_location("house_hands_confirm", path)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001 - no library, no check: she speaks as before
            mod = None
        _HANDS_CONFIRM["mod"] = mod
    return _HANDS_CONFIRM["mod"]


def session_tool_replies(session_id: str | None, profile: str = "crissa",
                         db: Path | None = None) -> tuple[list[str], list[str]] | None:
    """(every tool reply of the session, those of its newest turn), from the agent's session store when one is
    named (EAR_BRIDGE_SESSION_DB, a sqlite file with a `messages` table; the house read its framework's).
    None = unreadable, which the claim check reads as "cannot confirm either way"."""
    if not session_id:
        return None
    import sqlite3
    named = os.environ.get("EAR_BRIDGE_SESSION_DB", "").strip()
    if db is None and not named:
        return None
    path = db or Path(named).expanduser()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.5)
        try:
            rows = con.execute("select role, content from messages where session_id=? order by id", (session_id,)).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None
    last_user = max((i for i, r in enumerate(rows) if r[0] == "user"), default=-1)
    return ([r[1] or "" for r in rows if r[0] == "tool"],
            [r[1] or "" for r in rows[last_user + 1:] if r[0] == "tool"])


def claim_checked(content: str, *, session_id: str | None, profile: str = "crissa", after_tool: bool = False,
                  replies: list[str] | None = None, turn_replies: list[str] | None = None) -> str:
    """`content` with every unconfirmed claim replaced by one plain line; unchanged when nothing is claimed."""
    hc = _hands_confirm() if CLAIM_CHECK else None
    if hc is None or not (content or "").strip():
        return content
    try:
        kinds = hc.claims(content)
        if not kinds:
            return content
        if replies is None:
            got = session_tool_replies(session_id, profile)
            replies, turn_replies = got if got is not None else (None, None)
        if turn_replies is None:
            turn_replies = replies
        if replies is None or (after_tool and not turn_replies):
            _log({"event": "claim_unverifiable", "claims": kinds, "reply": content[:120]})
            return content
        miss = hc.unconfirmed(content, hc.effects_in(replies))
        if not miss:
            return content
        kept = hc.strip_claims(content, miss)
        out = (kept + " I could not confirm that.").strip()
        _log({"event": "claim_unconfirmed", "claims": miss, "said": content[:160], "spoken": out[:160]})
        return out
    except Exception as e:  # noqa: BLE001 - the check never costs her the reply
        _log({"event": "claim_check_error", "error": str(e)[:120]})
        return content


class AfterTool(str):
    """A reply that is the answer AFTER a tool ran - not a continuation of the first spoken clause."""
    after_tool = True


def split_after_tool(acc: str, mark: int) -> tuple[str, bool]:
    """(text to shape into the reply, was it cut at a tool mark). The whole text when there is no mark,
    when nothing followed the tool, or when the switch is off."""
    if not ANSWER_AFTER_TOOL or mark <= 0 or mark > len(acc or ""):
        return acc or "", False
    post = (acc or "")[mark:]
    return (post, True) if post.strip() else (acc or "", False)


# WHEN THE CHAT SERVICE SAYS NO (the User 2026-10-12, .101 round 10: an expired xAI key answered "HTTP 400: Incorrect
# API key" and she said "Crissa's desk is quiet. Try once more." on every turn, a line that named the house's face and
# said nothing about the cause). A refused key (401/403, or a 400 whose body names the key) is said as such, with where
# to fix it; any other failure is "I couldn't reach my chat service". Neither line names anyone: the kit's speaker is
# whoever her_name() says. The body is read once (error_body); it is never spoken, and since round 13 it is logged
# masked (logged_body: key-shaped runs become ***). Kill: EAR_BRIDGE_KEY_REFUSED_SAY=0 (the old line).
KEY_REFUSED_LINE = "My chat key was refused. Check it in Connect, then ask me again."
GATEWAY_DOWN_LINE = "I couldn't reach my chat service. Try once more."
# words that NAME THE KEY (a 401 needs none): "permission denied", "forbidden" or "unauthorized" alone do not
_KEY_WORDS = re.compile(r"api[ _-]?key|incorrect key|invalid key|invalid[_ ]x[_-]?api|"
                        r"key (?:is |was )?(?:invalid|incorrect|expired|revoked|missing)", re.I)


def _http_code(err: BaseException) -> int | None:
    code = getattr(err, "code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def key_refused(err: BaseException) -> bool:
    """True when the chat service refused the key: HTTP 401, or a 400 or 403 whose body names the key. A bare 403 is not
    a refused key (.101 2026-10-12: one 403 in 405 ms between turns where the same key worked said "My chat key was
    refused"): it is the service not answering this once, asked once more quietly (quiet_retry_ok)."""
    got = getattr(err, "_crissa_key_refused", None)
    if got is not None:
        return bool(got)                  # the body is read once: a second ask gets the first answer
    got = _key_refused(err)
    try:
        setattr(err, "_crissa_key_refused", got)
    except Exception:  # noqa: BLE001
        pass
    return got


def error_body(err: BaseException) -> str:
    """The HTTP error's body, read once and kept on the error (a second read of an HTTPError is empty)."""
    got = getattr(err, "_crissa_body", None)
    if got is not None:
        return got
    body = ""
    try:
        raw = err.read() if hasattr(err, "read") else b""        # type: ignore[attr-defined]
        body = (raw or b"").decode("utf-8", "replace")[:2000]
    except Exception:  # noqa: BLE001  an unreadable body is an empty one
        body = ""
    try:
        setattr(err, "_crissa_body", body)
    except Exception:  # noqa: BLE001
        pass
    return body


# A key-shaped run of characters, masked before a body is logged (a provider can echo a key prefix).
_KEYISH = re.compile(r"(?:sk|xai|key|pk|rk)[-_][A-Za-z0-9_\-]{6,}|Bearer\s+\S+|[A-Za-z0-9_\-]{32,}")


def logged_body(err: BaseException, n: int = 300) -> str:
    """The error body for the log: one line, masked, at most n characters (round 13: .101's three 400s had none)."""
    return _KEYISH.sub("***", " ".join(error_body(err).split()))[:n]


def _key_refused(err: BaseException) -> bool:
    code = _http_code(err)
    if code == 401:
        return True
    if code in (400, 403):
        return bool(_KEY_WORDS.search(error_body(err)))
    return False


# HER MODEL SAID NO TO THE REQUEST (round 17, .101 2026-10-13 05:54: both "Keep reading" turns got HTTP 403 with
# {"code": "permission-denied", "error": "I'm sorry, I can't help with that request."} in ~0.6 s, the key fine on every
# other turn, and she said "I couldn't reach my chat service" - the User thought the key or the network had broken).
# A 400/403/451 that does not name the key and whose body is a refusal (an apology, a policy or safety word, or a bare
# permission-denied that names no model, team, access or billing) is the model declining THIS request: it is said as
# that, logged as `gateway_refusal`, and never asked again (the same request is refused the same way). A real reach
# error keeps GATEWAY_DOWN_LINE. Kill: EAR_BRIDGE_REFUSAL_SAY=0 (round 16: the reach line, one quiet retry).
REFUSAL_LINE = "My model declined that one."
SERVICE_LINES = (REFUSAL_LINE, GATEWAY_DOWN_LINE, KEY_REFUSED_LINE)   # what she says about the service, not an answer
_REFUSAL_WORDS = re.compile(r"can'?t (?:help|assist) with|cannot (?:help|assist) with|(?:unable|not able) to (?:help|assist)|"
                            r"i'?m sorry|content (?:policy|filter|moderation)|usage (?:policy|policies|guidelines)|"
                            r"safety (?:system|filter|policy)|moderation|flagged|refus", re.I)
_PERMISSION_DENIED = re.compile(r"permission[-_ ]denied", re.I)
_ACCOUNT_WORDS = re.compile(r"\b(?:model|team|access|credits?|billing|quota|region|plan|subscription)\b", re.I)


def refusal_on() -> bool:
    return os.environ.get("EAR_BRIDGE_REFUSAL_SAY", "1").strip().lower() not in ("0", "false", "no", "off")


def model_refused(err: BaseException) -> bool:
    """True when the chat service declined this request's content (see REFUSAL_LINE), never for a refused key."""
    if not refusal_on() or _http_code(err) not in (400, 403, 451) or key_refused(err):
        return False
    body = error_body(err)
    if _REFUSAL_WORDS.search(body):
        return True
    return _http_code(err) == 403 and bool(_PERMISSION_DENIED.search(body)) and not _ACCOUNT_WORDS.search(
        _PERMISSION_DENIED.sub(" ", body))


# A REFUSAL IN THE STREAM (round 17, the Claude preset, the User 2026-10-13: "add the Claude preset"). Anthropic's
# safety classifiers decline with HTTP 200 and stop_reason "refusal"; through its OpenAI-compatible layer that arrives as
# the stream's finish_reason, read as "refusal" or OpenAI's "content_filter" (lib/crissa_agent.REFUSAL_FINISH). It is
# the same thing as round 17's refused body: she says REFUSAL_LINE (after her first clause when that was already said),
# it is logged as `gateway_refusal` with the finish_reason, never as a reach error, and it is not kept in her history.
# Kills: EAR_BRIDGE_REFUSAL_SAY=0 (round 17's whole refusal path), CRISSA_CONNECT_CLAUDE_PRESET=0 (this part).
class Declined(str):
    """A reply the model declined after her first clause was said: the clause, then REFUSAL_LINE."""
    declined = True


def claude_on() -> bool:
    try:
        return bool(connect.A.claude_preset_on())
    except Exception:  # noqa: BLE001  a Connect without the Claude preset: none of it applies
        return False


def finish_refused(meta: dict | None) -> bool:
    """Did the stream end as a refusal (finish_reason refusal / content_filter)?"""
    if not meta or not refusal_on() or not claude_on():
        return False
    try:
        return bool(connect.A.refusal_finish(meta.get("finish_reason")))
    except Exception:  # noqa: BLE001
        return False


def declined_reply(clause: str | None) -> str:
    """What she says for a refusal in the stream: REFUSAL_LINE, after the clause she already said (so the rest after
    that clause is the refusal line, not silence)."""
    c = str(clause or "").strip()
    return Declined(c + " " + REFUSAL_LINE) if c else REFUSAL_LINE


# CLAUDE'S WIRE (round 17, the Claude preset). Anthropic's OpenAI-compatible layer answers a temperature below 1 (any
# non-default sampling value on its current models) with HTTP 400, and the vLLM-only chat_template_kwargs means nothing
# there. Sent anyway, every process's first Claude turn paid a 400 and a second ask (repair_after_400) before her first
# word; to Anthropic's host they are never sent (every caller: her reply, a guest, the revise turn, the over-line ask).
# Kill: CRISSA_CONNECT_CLAUDE_PRESET=0 (the 400 and the repair, as before).
_CLAUDE_DROPS = ("temperature", "top_p", "chat_template_kwargs")


def claude_wire(body: dict, url: str) -> list[str]:
    """Drop what Anthropic's compatibility layer refuses or ignores, on its host only; the keys dropped."""
    try:
        host = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return []
    if not (host == "anthropic.com" or host.endswith(".anthropic.com")) or not claude_on():
        return []
    dropped = [k for k in _CLAUDE_DROPS if k in body]
    for k in dropped:
        body.pop(k, None)
    return dropped


# WHAT A PLAIN CHAT API REFUSES (round 13, .101 2026-10-12): three HTTP 400s in a row on turns that were not the User's
# own voice, while his own turns worked. Those turns carry `tool_choice: "none"` (the guest lock) with no `tools`,
# which an OpenAI-style public API may refuse, and the vLLM-only `chat_template_kwargs`. A public API runs no tools
# it was not sent, so `tool_choice` is never sent to one without `tools`; and a 400 that is not the key is asked ONCE
# more without the optional keys it names (or the vendor keys, when it names none), before anything is spoken; the
# endpoint then keeps that shape for the rest of the process. Kill: EAR_BRIDGE_PAYLOAD_REPAIR=0.
_PUBLIC_CHAT_HOSTS = ("api.x.ai", "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
                      "openrouter.ai", "api.groq.com", "api.mistral.ai", "api.deepseek.com")
_OPTIONAL_KEYS = ("tools", "tool_choice", "chat_template_kwargs", "temperature", "max_tokens")   # tools: round 26
_ENDPOINT_DROPS: dict[tuple[str, str], set] = {}


def payload_repair_on() -> bool:
    return os.environ.get("EAR_BRIDGE_PAYLOAD_REPAIR", "1").strip().lower() not in ("0", "false", "no", "off")


def _public_host(url: str) -> bool:
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _PUBLIC_CHAT_HOSTS)


def shape_payload(payload: dict, url: str) -> list[str]:
    """Before the call: drop what this endpoint refused before, and `tool_choice` without `tools` on a public API."""
    if not payload_repair_on():
        return []
    dropped = []
    for k in sorted(_ENDPOINT_DROPS.get((url, str(payload.get("model") or "")), ())):
        if k in payload:
            payload.pop(k, None)
            dropped.append(k)
    if "tool_choice" in payload and not payload.get("tools") and _public_host(url):
        payload.pop("tool_choice", None)
        dropped.append("tool_choice")
    return dropped


def repair_after_400(err: BaseException, payload: dict, url: str, *, spoken: bool) -> list[str]:
    """After an HTTP 400 that is not a refused key and before any of the answer was spoken: the optional keys to drop
    for ONE more ask (removed from `payload` and remembered for this endpoint), or [] for no retry."""
    if not payload_repair_on() or spoken or _http_code(err) != 400 or key_refused(err):
        return []
    # Round 17, the Claude preset: a 400 that is a refusal (Anthropic's "Output blocked by content filtering policy")
    # is refused the same way however the request is shaped - said at once, never re-asked.
    if claude_on() and model_refused(err):
        return []
    body = error_body(err).lower()
    named = [k for k in _OPTIONAL_KEYS if k in payload and (k in body or k.replace("_", " ") in body)]
    if "tool_choice" in payload and not payload.get("tools") and "tool" in body and "tool_choice" not in named:
        named.append("tool_choice")
    if not named:
        named = [k for k in ("chat_template_kwargs",) if k in payload]
        if "tool_choice" in payload and not payload.get("tools") and _public_host(url):
            named.append("tool_choice")
    for k in named:
        payload.pop(k, None)
    if named:
        _ENDPOINT_DROPS.setdefault((url, str(payload.get("model") or "")), set()).update(named)
    return named


RETRY_FRESH_S = float(os.environ.get("EAR_BRIDGE_RETRY_FRESH_S", "6") or 0)


def quiet_retry_ok(err: BaseException, *, spoken: bool, elapsed_s: float) -> bool:
    """Ask the chat service once more? Only an HTTP 403 / 429 / 5xx that is not a refused key, before any of the answer
    was spoken, within RETRY_FRESH_S of the question."""
    code = _http_code(err)
    if RETRY_FRESH_S <= 0 or spoken or elapsed_s > RETRY_FRESH_S or code is None:
        return False
    if model_refused(err):
        return False                      # round 17: a refusal is refused again (.101: the retry was the second 403)
    if code == 403 or code == 429 or 500 <= code < 600:
        return not key_refused(err)
    return False


def gateway_error_line(who: str | None, refused: bool) -> str:
    """What she says when the chat call failed (see KEY_REFUSED_LINE)."""
    if os.environ.get("EAR_BRIDGE_KEY_REFUSED_SAY", "1").strip().lower() in ("0", "off", "false", "no"):
        return f"{(who or 'them').capitalize()}'s desk is quiet. Try once more."
    return KEY_REFUSED_LINE if refused else GATEWAY_DOWN_LINE


_FINISH_RE = re.compile(rb'"finish_reason"\s*:\s*"(\w+)"')


def _stream_chat_text(
    url: str,
    payload: dict,
    *,
    key: str = "",
    timeout: float | None = 90.0,
    session_id: str | None = None,
    on_delta=None,
    on_tool=None,
    on_tool_info=None,
    meta: dict | None = None,
    stop=None,
    calls=None,
) -> str:
    """OpenAI-style SSE. stop(), when given and true, ends the read early with what has come (round 15: the turn is
    being asked again). on_delta(accum) after each content piece. meta, when given, gets "finish_reason" (round 15:
    "length" means the reply stopped on its token budget, mid-sentence).

    If the server ignores stream=true and returns JSON, that still works —
    on_delta fires once with the full text (no TTFS win).

    on_tool(len(accum)) fires at each tool event frame (`event: <...>.tool.progress`, connect.tool_event): the text
    before the LAST such mark is what she said while reaching for a tool, the text
    after it is her answer (see AfterTool).
    """
    body = dict(payload)
    body["stream"] = True
    claude_wire(body, url)                          # round 17: no temperature to Anthropic's compatibility layer
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    hdr = connect.session_header()
    if session_id and hdr:
        headers[hdr] = session_id
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    acc = ""
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ct = (r.headers.get("Content-Type") or "").lower()
        if "event-stream" not in ct:
            raw = r.read().decode()
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                return ""
            acc = _extract_chat_text(obj)
            if calls is not None and isinstance(obj, dict):
                calls.message(obj)                  # round 26: her display tool's call in a whole reply
            if meta is not None:
                try:
                    meta["finish_reason"] = (obj.get("choices") or [{}])[0].get("finish_reason")
                except Exception:  # noqa: BLE001
                    pass
            if on_delta and acc:
                on_delta(acc)
            return acc
        buf = b""
        tool_data_next = False  # L: the data line after a tool event carries {tool, label, status}
        while True:
            if stop is not None and stop():
                if meta is not None:
                    meta["abandoned"] = True
                return acc
            chunk = r.read(256)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if on_tool and connect.is_tool_event_line(line):
                    _TOOL_EVENTS["seen"] += 1
                    on_tool(len(acc))
                    tool_data_next = on_tool_info is not None
                    continue
                if tool_data_next and line.startswith(b"data:"):
                    tool_data_next = False
                    try:
                        on_tool_info(json.loads(line[5:].decode("utf-8", errors="replace").strip() or "{}"))
                    except Exception:
                        pass
                    continue
                if calls is not None:
                    calls.line(line)                # round 26: her display tool's call, folded as it streams
                if meta is not None and b"finish_reason" in line:
                    fm = _FINISH_RE.search(line)
                    if fm:
                        meta["finish_reason"] = fm.group(1).decode("ascii", "replace")
                piece = sse_delta_text(line.decode("utf-8", errors="replace"))
                if piece is None:
                    continue
                if piece == "":
                    return acc
                acc += piece
                if on_delta:
                    on_delta(acc)
    return acc


def _display_round(payload: dict, said: str, calls, gw: dict, session_id, meta: dict, stop, on_delta) -> str:
    """Round 26: run her display tool's calls at the conductor, then ask once more with the results for her short line
    about it (no tool in that answer is run: tool_choice none). Returns everything she said, both asks.
    Cut 15 (her own tools, own_tools.py): when a round ran one of her own tools (recall, web_read ...) the next ask
    keeps the tools, so she can look something up and then show it - at most OWN_TOOL_ROUNDS asks after the first;
    a round of display alone ends as before."""
    from . import own_tools
    head = said.strip()
    msgs = payload["messages"]
    asked = next((str(m.get("content") or "") for m in reversed(payload.get("messages") or [])
                  if isinstance(m, dict) and m.get("role") == "user"), "")
    rounds, all_ran, more = 0, [], ""
    while True:
        ran = display_call.run(calls, DISPLAY_TOOL_URL)
        rounds += 1
        all_ran += ran
        own_tools.note_ran(ran)
        for c in ran:
            if c.get("name") == "display":
                board_pin(False)              # a new page or a clear: the keep was for what was up before
            _log({"event": "display_tool" if c.get("name") == "display" else "own_tool", "profile": gw["profile"],
                  "name": c.get("name"), "args": str(c.get("args") or "")[:160],
                  "result": str(c.get("result") or "")[:160]})
        msgs = display_call.follow_up(msgs, said if rounds == 1 else more, ran)
        again = (rounds <= OWN_TOOL_ROUNDS and any(own_tools.handles(c.get("name")) for c in ran))
        second = dict(payload, messages=msgs)
        if again:
            second.pop("tool_choice", None)
            calls = display_call.Calls()
        else:
            second["tool_choice"] = "none"
            calls = None
            cap = display_call.short_after(all_ran, asked)        # round 27: a page up is read, not heard
            if cap:
                second["max_tokens"] = cap
        done_so_far = head

        def _more(acc2: str) -> None:
            on_delta((done_so_far + " " + acc2).strip() if done_so_far else acc2)
        try:
            more = _stream_chat_text(gw["url"], second, key=gw["key"],
                                     timeout=_env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 0.0),
                                     session_id=session_id, on_delta=_more, meta=meta, stop=stop, calls=calls)
        except Exception as e:  # noqa: BLE001 - the page is up or not; her line about it says which
            _log({"event": "display_tool_followup_error", "profile": gw["profile"], "error": str(e)[:200]})
            more = ("It's up on the screen." if display_call.shown(all_ran)
                    else "I couldn't put that on the screen." if any(c.get("name") == "display" for c in all_ran)
                    else "I couldn't finish that.")
            _more(more)
            calls = None
        head = (head + " " + more).strip() if head else more
        if not calls:
            return head
        if OWN_TOOL_GUARD:                                  # the next round's calls pass the same gates
            speculate.tool_guard(id_gate.tool_guard(lambda n: None, lambda: _get(PRESENCE, timeout=1.0),
                                                    _log))(len(head))


def _display_take_down(text: str, t: dict) -> bool:
    """Round 27 ("take it down", display_call.TAKE_DOWN): stop her, clear the sheet at the conductor, say what happened.
    True when it was handled (the caller drops the turn)."""
    if _speaking or _speak_proc is not None:
        abort_mouth("take_down")
    ok, res = display_call.take_down(DISPLAY_TOOL_URL)
    board_pin(False)
    _log({"event": "display_take_down", "text": text[:80], "pid": t.get("person_id"), "cleared": ok,
          "result": res[:120]})
    line = display_call.DOWN_LINE if ok else display_call.NOT_DOWN_LINE
    threading.Thread(target=lambda: speak_blocking(line, skip_quiet=True), name="take-down", daemon=True).start()
    return True


def session_board_fields() -> list[str]:
    """What a session end takes off the glass: her page and art, never the news.

    The headline and its source line belong to the hourly news job (crissa
    lib/vo_news_slot.py); clearing them at every session end left the wall blank
    for up to an hour (the User 2026-10-09). EAR_BRIDGE_CLEAR_NEWS=1 clears them again.
    """
    fields = ["notes", "notes_title", "doc", "doc_title", "art"]
    if os.environ.get("EAR_BRIDGE_CLEAR_NEWS", "0").strip().lower() in ("1", "true", "yes", "on"):
        fields += ["headline", "note"]
    return fields


# WHAT SHE LEFT UP CLEARS 30 s AFTER SHE PAUSES (the User 2026-10-30: "I want her to clear it as a habit ... at her
# discretion. If she leaves it up I want it to self-clear 30s after conversation ends"; "clears when Paused"). The
# session end cleared the sheet at once, so a 20 s pause mid-reading took Moby-Dick down under him. A new session in
# the 30 s keeps it. 0 = clear at the pause, as before. Kill EAR_BRIDGE_BOARD_CLEAR_AFTER_S=0.
BOARD_CLEAR_AFTER_S = float(os.environ.get("EAR_BRIDGE_BOARD_CLEAR_AFTER_S", "30") or 0)
_board_gen = {"n": 0}
_board_pin = {"on": False}            # he asked her to leave it up (display_call.KEEP_UP): no clear after the pause


def board_pin(on: bool) -> None:
    if _board_pin["on"] != bool(on):
        _board_pin["on"] = bool(on)
        _log({"event": "board_pin", "on": bool(on)})


def board_keep() -> None:
    """A session opened: a clear still waiting from the last pause does not run."""
    _board_gen["n"] += 1


def clear_session_board_later() -> None:
    """The pause's clear, BOARD_CLEAR_AFTER_S from now unless a session opens first."""
    if _board_pin["on"]:
        _log({"event": "session_board_clear_due", "skipped": "he asked to leave it up"})
        return
    if BOARD_CLEAR_AFTER_S <= 0:
        clear_session_board()
        return
    _board_gen["n"] += 1
    gen = _board_gen["n"]

    def _later() -> None:
        time.sleep(BOARD_CLEAR_AFTER_S)
        if _board_gen["n"] == gen and not _board_pin["on"]:
            clear_session_board()

    _log({"event": "session_board_clear_due", "in_s": BOARD_CLEAR_AFTER_S})
    threading.Thread(target=_later, name="board-clear-later", daemon=True).start()


def clear_session_board() -> None:
    """Drop Crissa's working paper when the spoken session ends.

    Notes/doc POST is not enough — a claimed page (panel/board) navigates
    the kiosk tab off the face. Ask VoiceOnly to pin the wall home.
    """
    try:
        _post_json(
            SCREEN_URL,
            {"clear": session_board_fields(),
             "mood": "resting",
             "focus": "none"},
            timeout=2.0,
        )
        _log({"event": "session_board_clear", "ok": True})
        board_pin(False)
    except Exception as e:
        _log({
            "event": "session_board_clear",
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:160],
        })
    try:
        if WALL_HOME:
            r = _post_json(ACTION_URL, {"action": "kiosk_wall"}, timeout=5.0)
            _log({"event": "session_wall_home", "ok": bool(r.get("ok", True))})
    except urllib.error.HTTPError as e:
        if e.code == 404:                              # no kiosk here: said once, never a failure
            if not _wall_home_absent["said"]:
                _wall_home_absent["said"] = True
                _log({"event": "session_wall_home", "skipped": "no kiosk to move here (404)"})
        else:
            _log({"event": "session_wall_home", "ok": False, "error": f"HTTPError: {e}"[:160]})
    except Exception as e:
        _log({
            "event": "session_wall_home",
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:160],
        })
    try:
        r = _post_json(CONDUCTOR_HOME, {"by": "session"}, timeout=2.0)
        _log({"event": "session_body_home",
              "ok": bool(r.get("taken") or r.get("ok"))})
    except Exception as e:
        _log({
            "event": "session_body_home",
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:160],
        })
    # The call is over for the body too (the User 2026-09-10 "wire ear to
    # conductor"): /v1/home already ends comms inside the Conductor, this
    # says it in so many words and shows in the journal. Fail open.
    try:
        r = _post_json(CONDUCTOR_COMMS, {"on": False, "why": "session end"}, timeout=2.0)
        _log({"event": "session_comms_off", "ok": bool(r.get("ok"))})
    except Exception as e:
        _log({"event": "session_comms_off", "ok": False,
              "error": f"{type(e).__name__}: {e}"[:160]})
    if DOC_WINDOW.is_file():
        try:
            subprocess.run(
                [str(DOC_WINDOW), "close"],
                timeout=3,
                capture_output=True,
            )
        except Exception:
            pass


def _log(ev: dict) -> None:
    ev = dict(ev)
    ev.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(json.dumps(ev, ensure_ascii=False), flush=True)


def voice_on() -> bool:
    try:
        s = _get(STT_VOICE, timeout=1.5)
        return str(s.get("voice") or "").lower() == "on"
    except Exception as e:
        quiet_log({"event": "voice_lock_error", "error": str(e)[:120]})
        return False


def voice_on_cached(ttl: float = 1.0) -> bool:
    """voice_on() at most once a second. The poll loop runs at POLL=0.2s and
    voice_on() is an HTTP GET with a 1.5s timeout — polling it every tick would
    put the whole ear behind the Voice lock's latency."""
    global _voice_cache_val, _voice_cache_ts
    now = time.time()
    if _voice_cache_val is not None and (now - _voice_cache_ts) < ttl:
        return _voice_cache_val
    _voice_cache_val = voice_on()
    _voice_cache_ts = now
    return _voice_cache_val


def presence_auth_ids(presence: dict) -> dict:
    """Pull auth block from RoomPresence SoT (Oracle/face/voice writers land here)."""
    auth = presence.get("auth") if isinstance(presence.get("auth"), dict) else {}
    def _ids(key: str) -> list[str]:
        raw = auth.get(key) or []
        if not isinstance(raw, list):
            return []
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    return {
        "anyone_authed": bool(auth.get("anyone_authed")),
        "authed_ids": _ids("authed_ids"),
        "soft_ids": _ids("soft_ids"),
    }


def authorize(turn: dict, presence: dict) -> str:
    """Auth level for free-air.

    user_auth_only (house-mode.json):
      bau | guest | silent  (BAU only face-authed User + User voice)
    house mode (legacy):
      allow | soft | deny
    """
    stamp = turn.get("stamp") if isinstance(turn.get("stamp"), dict) else {}
    pid = (turn.get("person_id") or "").strip().lower()
    if not id_gate.gating():
        return "bau"                              # round 17: Security ID-only / Off - the owner's conversation
    # Locktight User mode — hard gate (Fable SOUL is guidance; this is rail)
    if is_user_auth_only():
        ev = evaluate_turn(
            presence,
            stamp,
            person_id=pid or turn.get("person_id"),
            look_pending=bool(globals().get("_session_looking")),
        )
        return str(ev.get("level") or "silent")

    if presence.get("error") and presence.get("headcount") is None:
        return "soft"
    hc = presence.get("headcount")
    try:
        hc_n = int(hc) if hc is not None else -1
    except (TypeError, ValueError):
        hc_n = -1
    # AUTH-first: empty inventory does not deny when User AUTH holds
    user = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    user_plane = bool(
        presence.get("user_auth")
        or user.get("auth")
        or presence.get("user_present")
        or user.get("user_present")
        or presence.get("user_face_sticky")
        or user.get("user_face_sticky")
    )
    if hc_n == 0 and not user_plane:
        return "deny"
    if not pid or (pid.startswith("u") and pid[1:].isdigit()):
        # Still allow sudo User stamp while authed with empty roster
        uid = str(
            user.get("user_id") or presence.get("user_id") or "user"
        ).lower()
        if not (user_plane and pid == uid):
            return "soft"
    # SoT auth (includes Oracle/face/voice soft writes into the same object)
    auth = presence_auth_ids(presence)
    if pid in auth["authed_ids"]:
        return "allow"
    if user_plane and pid == str(
        user.get("user_id") or presence.get("user_id") or "user"
    ).lower():
        return "allow"
    if pid in auth["soft_ids"]:
        return "soft"
    people = presence.get("people") or []
    certain_names = {
        str(p.get("name") or "").lower()
        for p in people
        if isinstance(p, dict) and p.get("name") and p.get("certain")
    }
    if pid in certain_names or stamp.get("certain"):
        return "allow"
    return "soft" if pid else "deny"


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def still_talking(t: dict) -> bool:
    """True when speech ran to the very edge of its block.

    The ear cuts at a fixed ~3.0s boundary, so a block whose speech is still
    active at the edge was CUT, not finished — the rest of the User's sentence is
    in the next block and we must keep gathering. A block with trailing
    silence means he actually stopped, so we can dispatch immediately instead
    of making him wait out the full window. ``speech_spans`` is already in
    every turn payload (rail-stt runs webrtcvad over the block).
    """
    try:
        dur = float(t.get("duration_s") or 0.0)
        spans = t.get("speech_spans") or []
        if dur <= 0 or not spans:
            return False
        ends = []
        for s in spans:
            if isinstance(s, (list, tuple)) and len(s) >= 2:
                ends.append(float(s[1]))
            elif isinstance(s, dict):
                ends.append(float(s.get("end") or 0.0))
        if not ends:
            return False
        return (dur - max(ends)) <= TAIL_SILENCE
    except (TypeError, ValueError):
        return False


# Progress lines the tool-callout hook speaks. Cosy plays them; STT hears
# them; without this drop they become a new user turn and she loops.
_TOOL_CALLOUT_PHRASES = (
    "checking the board",
    "opening that card",
    "filing a card",
    "updating the board",
    "searching the board",
    "looking at the board",
    "looking at the room",
    "putting it on the wall",
    "checking the time",
    "checking the ear",
    "taking a look",
    "searching the web",
    "looking at a picture",
    "checking memory",
    "reading a file",
    "searching files",
    "opening the browser",
    "one moment",
)


# A CALLOUT PHRASE WITH HER NAME IS SAID TO HER (round 46, the local review of kit crissa-063a2813, 2026-10-23): this
# test reads the words only, so "Crissa, one moment" said over her line matched "one moment" and was dropped as her own
# echo. Her callouts never say her name; a line that hits her wake name (wake_match) is a person speaking to her and is
# not her echo. Kill: EAR_BRIDGE_CALLOUT_WAKE_EXEMPT=0.
CALLOUT_WAKE_EXEMPT = os.environ.get("EAR_BRIDGE_CALLOUT_WAKE_EXEMPT", "1").strip().lower() not in (
    "0", "false", "no", "off")


def is_tool_callout_echo(text: str) -> bool:
    n = _norm(text)
    if not n:
        return False
    if CALLOUT_WAKE_EXEMPT:
        try:
            if wake_match(text).get("hit"):
                return False
        except Exception:  # noqa: BLE001 - an unreadable wake list: the words alone, as before
            pass
    phrases = list(_TOOL_CALLOUT_PHRASES)
    if callouts.armed():
        # L: her own callouts coming back - not the bare "on it" (his "work on it now" is not her echo;
        # own_voice's said_it_herself catches hers inside her speaking window)
        phrases += [_norm(p) for p in callouts.all_phrases() if p != callouts.FALLBACK]
    for p in phrases:
        if n == p or n.startswith(p + " ") or n.startswith(p + "."):
            return True
        if p in n and len(n) <= len(p) + 24:
            return True
    return False


STUDIO_LISTEN = Path(
    os.environ.get(
        "RENDER_STUDIO_LISTEN",
        str(HOME / "render-studio-listen"),
    )
)
GPU_MODE_FILE = Path(
    os.environ.get(
        "HOUSE_GPU_MODE_FILE",
        str(HOME / "house-gpu-mode"),
    )
)
GPU_PENDING_FILE = Path(
    os.environ.get(
        "HOUSE_GPU_PENDING_FILE",
        str(HOME / "house-gpu-pending"),
    )
)
HANDOFF_FILE = Path(
    os.environ.get(
        "HOUSE_RENDER_HANDOFF",
        str(HOME / "render-handoff.json"),
    )
)
ABORT_HOST = os.environ.get("EAR_BRIDGE_ABORT_HOST", "127.0.0.1")
ABORT_PORT = int(os.environ.get("EAR_BRIDGE_ABORT_PORT") or _port_num("bridge"))
PENDING_MAX_AGE_SEC = float(os.environ.get("EAR_BRIDGE_PENDING_MAX_AGE", str(20 * 60)))


def occupancy_is_render() -> bool:
    try:
        first = GPU_MODE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return False
    return first == "render"


def occupancy_pending_render() -> bool:
    """Read-only. Never unlink house-mode's pending file. Bound by mtime."""
    try:
        st = GPU_PENDING_FILE.stat()
    except OSError:
        return False
    if time.time() - st.st_mtime > PENDING_MAX_AGE_SEC:
        return False
    try:
        lines = GPU_PENDING_FILE.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return False
    if not lines or lines[0].strip().lower() != "render":
        return False
    if len(lines) < 2:
        return False
    try:
        pid = int(lines[1].strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def studio_holds_ear() -> bool:
    """Render studio mic is on — User is talking to Lila, not Crissa.

    File + occupancy. A leftover flag in Heavy/Normal must not mute Crissa.
    """
    if not STUDIO_LISTEN.is_file():
        return False
    return occupancy_is_render()


_render_crissa_said = 0.0


def handoff_recent() -> bool:
    try:
        d = json.loads(HANDOFF_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(d, dict):
        return False
    try:
        ts = float(d.get("ts") or 0)
        ttl = float(d.get("ttl_sec") or 45)
    except (TypeError, ValueError):
        return False
    return bool(ts and ttl and (time.time() - ts) < ttl)


def render_skips_crissa() -> bool:
    """Render parks Crissa's gateway. Pending=render also skips (flag lags)."""
    return occupancy_is_render() or occupancy_pending_render()


# RENDER: LILA HAS THE GLASS (the User 2026-09-13: "Lila working and available for
# comms if you interrupt her"). 0 = the old asleep line for every Render turn.
RENDER_LILA_HOST = os.environ.get("HOUSE_RENDER_LILA_HOST", "1").strip().lower() not in ("0", "false", "no", "off")
STUDIO_URL = os.environ.get("EAR_BRIDGE_STUDIO", _port_url("feed", path="/v1/render-studio"))
# the render module's ComfyUI (CRISSA_COMFY_URL, else the port table's comfy), never the house's own (2026-10-10)
COMFY_HEALTH = os.environ.get("EAR_BRIDGE_COMFY") or (
    (os.environ.get("CRISSA_COMFY_URL") or _port_url("comfy")).rstrip("/") + "/system_stats")


def render_lila_host() -> bool:
    return RENDER_LILA_HOST


def to_other_mouth(wm: dict) -> bool:
    """A direct wake for someone who is not Crissa (Ada): never Lila's turn."""
    return bool(
        wm.get("hit")
        and str(wm.get("route") or "direct") == "direct"
        and str(wm.get("profile") or "crissa") != "crissa"
    )


def comfy_up(timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(COMFY_HEALTH, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def post_lila_turn(text: str) -> None:
    """Lila's studio turn, off the ear loop: she talks (Cosy `lila`, spoken by
    the studio itself), maybe draws, looks. This thread only logs her answer."""

    def _run() -> None:
        try:
            out = _post_json(STUDIO_URL, {"text": text, "source": "ear-session"}, timeout=600.0)
        except Exception as e:
            _log({"event": "render_lila_failed", "error": f"{type(e).__name__}: {e}"[:120]})
            return
        _log(
            {
                "event": "render_lila_said",
                "ok": out.get("ok"),
                "skipped": out.get("skipped"),
                "busy": out.get("busy"),
                "say": str(out.get("say") or "")[:80],
            }
        )

    threading.Thread(target=_run, name="render-lila-turn", daemon=True).start()


def is_mouth_echo(turn: dict) -> bool:
    """True when rail-stt stamped this turn as house TTS (embedding veto)."""
    st = turn.get("stamp") if isinstance(turn.get("stamp"), dict) else {}
    if st.get("source") == "tts_echo_veto":
        return True
    if st.get("tts_veto"):
        return True
    if is_tool_callout_echo(turn.get("text") or ""):
        return True
    return False


def is_oracle_feed(turn: dict) -> bool:
    """Argus/oracle describe lines belong on omni, not in Crissa's mouth.

    Posted with via=argus / person_id=argus / source=argus_describe so the
    log button still shows them. Free-air must not dispatch or speak them.
    """
    via = str(turn.get("via") or "").strip().lower()
    if via in {"argus", "oracle"}:
        return True
    pid = str(turn.get("person_id") or "").strip().lower()
    if pid in {"argus", "oracle"}:
        return True
    st = turn.get("stamp") if isinstance(turn.get("stamp"), dict) else {}
    src = str(st.get("source") or "").strip().lower()
    if src in {"argus_describe", "argus_visit", "oracle_visit", "oracle"}:
        return True
    return False


def is_spoken_via(turn: dict) -> bool:
    """Mic STT only. Wake is words, not Argus door prose."""
    via = str(turn.get("via") or "").strip().lower()
    return via in {"ear", "stt", "listen", ""}


def is_echo(text: str, last_reply: str) -> bool:
    """True if STT looks like our own TTS (feedback)."""
    a, b = _norm(text), _norm(last_reply)
    if not a or not b:
        return False
    if len(a) < 8 and a in b:
        return True
    if a in b or b in a:
        return True
    # token overlap
    ta, tb = set(a.split()), set(b.split())
    if not ta:
        return False
    overlap = len(ta & tb) / max(1, len(ta))
    if overlap >= 0.7 and len(ta) >= 3:
        return True
    return SequenceMatcher(None, a, b).ratio() >= ECHO_RATIO


def _roster_phrase(presence: dict) -> str:
    people = presence.get("people") or []
    bits = []
    for p in people:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("display") or "?").strip()
        if not name:
            continue
        if p.get("certain") is False or str(p.get("id_status") or "") == "unknown":
            if name.upper().startswith("U") and name[1:].isdigit():
                bits.append(f"unknown slot {name}")
            else:
                bits.append(f"{name} (uncertain)")
        else:
            bits.append(name)
    return ", ".join(bits) if bits else "empty"


def presence_facts_line(
    presence: dict, *, person_id: str | None, level: str
) -> str:
    """Auth-usable free-air inject from RoomPresence SoT only (no Oracle HTTP)."""
    auth = presence_auth_ids(presence)
    # Guest: no names, no roster IDs, no identity files
    if level in ("guest", "silent"):
        return (
            f"Auth level={level}. Guest mode — no personal names or ID files. "
            f"Room occupied={bool((presence.get('headcount') or 0) > 0)}. "
            f"lights_out={bool(presence.get('lights_out'))}."
        )
    user = presence.get("user") if isinstance(presence.get("user"), dict) else {}
    sticky = bool(
        presence.get("user_face_sticky") or user.get("user_face_sticky")
    )
    buf = presence.get("face") if isinstance(presence.get("face"), dict) else {}
    fov = str(buf.get("locked") or buf.get("source") or "door")
    looking = bool(presence.get("f2f"))
    return (
        f"Speaker person_id={person_id or 'unknown'}. Auth level={level}. "
        f"Room headcount={presence.get('headcount')} "
        f"roster=[{_roster_phrase(presence)}] "
        f"trust={presence.get('count_trust') or presence.get('count_certain')} "
        f"frame_age_sec={presence.get('frame_age_sec')} "
        f"auth_anyone={auth['anyone_authed']} "
        f"authed_ids={auth['authed_ids']} soft_ids={auth['soft_ids']} "
        f"user_present={presence.get('user_present')} "
        f"user_auth={presence.get('user_auth')} "
        f"auth_sticky={sticky} "
        f"camera={'looking' if looking else 'door_rest'} "
        f"fov={fov} "
        f"face_age={buf.get('age_s')} "
        f"auth_hold_s={buf.get('auth_hold_s')}. "
        "AUTH is the live the face check User face, or empty FOV for 5s after that lock."
    )


def _crissa_soul() -> str:
    """Her persona, as the Connect config gives it (the user's text, the stock persona, or nothing)."""
    try:
        return connect.system_prompt()
    except Exception:  # noqa: BLE001
        return ""


def _extract_chat_text(body: dict) -> str:
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, dict) and part.get("text"):
                bits.append(str(part["text"]))
            elif isinstance(part, str):
                bits.append(part)
        content = " ".join(bits)
    text = (content or "").strip()
    return re.sub(r"<think>[\s\S]*?</think>", " ", text, flags=re.I).strip()


def request_ensure_user_auth(
    ev: dict | None,
    *,
    force_facefind: bool | None = None,
    reason: str | None = None,
    mode: str | None = None,
) -> dict:
    """Face check for tools. Desk FaceFinder (hour 6) when hunting.

    Lock guest: no hunt. Re-AUTH waits for the unlock burst.
    Unlocked + not AUTH still hunts (normal).
    """
    ev = ev if isinstance(ev, dict) else {}
    if ev.get("auth_reason") == "host_locked":
        return {
            "ok": True,
            "auth": False,
            "reason": "host_locked",
            "via": "locked",
        }
    force_reprove = bool(
        ev.get("force_reprove")
        or ev.get("auth_reason") == "wrong_voice"
        or ev.get("auth_tier") == "contested"
    )
    if force_facefind is None:
        force_facefind = bool(force_reprove or not ev.get("user_voice"))
    if mode is None:
        mode = "desk" if force_facefind else "door_local"
    payload = {
        "person_id": ev.get("user_id") or "user",
        "voice_matched": bool(ev.get("user_voice")) and not force_reprove,
        "voice_sim": ev.get("user_voice_sim"),
        "reason": reason
        or ("wrong_voice_reprove" if force_reprove else "house_command"),
        "force_facefind": bool(force_facefind),
        "mode": mode,
    }
    base = PRESENCE.rstrip("/")
    if base.endswith("/v1/presence"):
        base = base[: -len("/v1/presence")]
    elif base.endswith("/presence"):
        base = base[: -len("/presence")]
    try:
        return _post_json(
            f"{base}/v1/ensure_user_auth",
            payload,
            timeout=90.0,
        )
    except Exception as e:
        return {"ok": False, "auth": False, "reason": f"ensure_fail:{e}"[:120]}


def crissa_guest_complete(user_text: str, *, system: str, on_first_clause=None) -> str:
    """A guest's reply: the same Connect endpoint, no persona of the house, no tools on the wire."""
    soul = _crissa_soul()
    sys = (soul + "\n\n" if soul else "") + system
    url, model = occupancy_chat()
    payload = {
        "model": model,
        "max_tokens": int(os.environ.get("EAR_BRIDGE_GUEST_MAX_TOKENS", "256")),
        "temperature": 0.35,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user_text},
        ],
    }
    t0 = time.time()
    kicked = {"v": False}

    def _kick(buf: str) -> None:
        if kicked["v"] or on_first_clause is None:
            return
        clause = first_speakable_clause(buf)
        if not clause:
            return
        kicked["v"] = True
        kicked["clause"] = clause
        _log({"event": "ttfs_clause", "ms": int((time.time() - t0) * 1000),
              "clause": clause[:80]})
        on_first_clause(clause)

    gmeta: dict = {}
    try:
        text = _stream_chat_text(
            url, payload, timeout=30.0, on_delta=_kick, meta=gmeta
        )
    except Exception as e:
        _log(
            {
                "event": "guest_ops_error",
                "error": str(e)[:200],
                "ms": int((time.time() - t0) * 1000),
            }
        )
        return "I can chat, but I can't do house work for that voice."
    if finish_refused(gmeta):                      # round 17: a refusal in the stream is said as one
        _log({"event": "gateway_refusal", "profile": "guest", "finish_reason": gmeta.get("finish_reason"),
              "code": None, "key_refused": False, "chars": len(text or ""), "ms": int((time.time() - t0) * 1000)})
        return declined_reply(kicked.get("clause"))
    _log(
        {
            "event": "guest_ops_ms",
            "ms": int((time.time() - t0) * 1000),
            "stream": True,
            "ttfs": kicked["v"],
        }
    )
    return text.strip() or "I can chat, but I can't do house work for that voice."


def _for_display(content: str) -> str:
    """Markdown, tool dumps, long ids and links out of a reply (lib/speakable.for_display when the pack carries it; the
    regex fallback below otherwise). Name phonetics stay in house-speak. Factored out of crissa_reply in round 14 so a
    reading's pieces are cleaned the same way; the behaviour is unchanged."""
    try:
        import importlib.util
        sp = Path(__file__).resolve().parents[3] / "lib" / "speakable.py"
        if sp.is_file():
            spec = importlib.util.spec_from_file_location("house_speakable", sp)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "for_display"):
                    return mod.for_display(content)
                return mod.strip_unspeakable(content)
    except Exception:
        pass
    content = re.sub(r"```[\s\S]*?(?:```|$)", " ", content)
    content = re.sub(r"^\s*\|.*\|.*$", " ", content, flags=re.M)
    content = re.sub(r"(?:https?://|www\.)\S+", "a link", content)
    content = re.sub(r"[*_`#|]+", " ", content)
    return re.sub(r"\s+", " ", content).strip()


def crissa_reply(
    user_text: str,
    *,
    person_id: str | None,
    presence: dict,
    level: str,
    session_id: str | None = None,
    user_voice: bool = False,
    force_reprove: bool = False,
    http_profile: str = "crissa",
    addressed: str | None = None,
    on_first_clause=None,
    on_tool_info=None,
    on_more=None,
    max_tokens: int | None = None,
    system_note: str = "",
    reply_meta: dict | None = None,
    stop=None,
    brief_reading: bool = False,
) -> str:
    """One turn to the agent. on_more(buf, tool_seen), when given, sees everything streamed so far (round 14: the
    reader that speaks a long answer as it arrives). brief_reading (round 17): a continue turn - her history's
    readings as one line each, and no cancel note (the note in system_note says where she stopped)."""
    if level in ("silent",):
        return ""
    if level == "deny":
        return "I need a clearer ID before I help with that."
    # Guest: verbal banter only, homescreen stays. No tools, never the wall.
    # House commands still hunt at desk via schedule_dispatch + match_control.
    if level == "guest":
        facts_g = presence_facts_line(presence, person_id=None, level="guest")
        sys_g = GUEST_SYSTEM + "\n" + facts_g
        sys_g += _overlay_for(http_profile, addressed)
        return crissa_guest_complete(
            user_text, system=sys_g, on_first_clause=on_first_clause
        )
    facts = presence_facts_line(presence, person_id=person_id, level=level)
    if level in OWNER_LEVELS:
        try:
            facts += own_tools.recent_days_note()     # cut 15: the last days, one line each (mid-term memory)
        except Exception:  # noqa: BLE001
            pass
    bullets = _today_memory_bullets()
    if level in ("bau", "allow") and bullets and "no house day events" not in bullets:
        facts += f"\nToday so far (short-term house memory):\n{bullets}"
    if level == "guest":
        sys = GUEST_SYSTEM + "\n" + facts
    else:
        sys = SYSTEM + "\n" + facts
        if level == "soft":
            sys += " Soft ID — help carefully; say if you're unsure who is speaking."
        if level in ("bau", "allow") and not id_gate.gating():
            sys = id_gate.reply_line(user_voice) + sys + id_gate.label_note(text=user_text)   # round 17/20
        elif level in ("bau", "allow"):
            if user_voice:
                # Ear is the authority. Hindsight/SOUL guest text must not win.
                sys = (
                    "THIS TURN IS BAU — not guest mode. The User is face-sticky "
                    "and this-turn voice matched. Full house service. Use tools "
                    "now for anything they ask (documents, wall, board, files). "
                    "Do not refuse. Do not offer instead of doing. Do not say "
                    "guest mode. Ignore memory that you are a guest.\n"
                    + sys
                    + " User is authenticated with this-turn voice — full house "
                    "service is allowed. Execute with tools this turn."
                )
            else:
                sys += (
                    " Face sticky session only — chat OK; do NOT use tools "
                    "(the board, display, memory write, system). Tools need User voice."
                )
    sys += _overlay_for(http_profile, addressed)
    _cancel_note = cancels.context_note()          # round 15 (g): the house agent's shape, once
    if READING_CLOSE and _READING_STATE.get("drop_note"):
        _READING_STATE["drop_note"] = False
        _cancel_note = ""                            # round 16: he ended it ("for good"); nothing to go on from
    elif _cancel_note and reading_closed_since(float((cancels.latest() or {}).get("ts") or 0)):
        _cancel_note = ""
    if brief_reading and CONTINUE_POSITION:
        _cancel_note = ""                            # round 17: used up here; the continue note says where she is
    if _cancel_note:
        sys += " The previous spoken reply was interrupted and cancelled." + _cancel_note
    if system_note:
        sys += system_note           # round 15: "go on" after a reading says where she stopped and asks for the next part
    elif not _cancel_note and _gate_notice() == "interrupted":
        sys += (
            " The previous spoken reply was interrupted and cancelled. "
            "Do not continue it. Answer only this turn."
        )
    gw = _gateway(http_profile)
    sys += model_line(gw)                          # round 16: her own model, from Connect's config
    payload = {
        "model": gw["model"],
        # Thinking ON burns completion tokens first — keep room for spoken content.
        # Override with EAR_BRIDGE_MAX_TOKENS; default matches Crissa profile max_tokens.
        # Round 15: the talk dial reply.max_tokens (EAR_BRIDGE_MAX_TOKENS overrides it), a reading's own budget.
        "max_tokens": int(max_tokens) if max_tokens else reply_budget(),
        "temperature": 0.35,
        "chat_template_kwargs": {"enable_thinking": False},
        # the owner's recent exchanges when the endpoint keeps none (round 14, history_for)
        "messages": [{"role": "system", "content": sys}]
        + history_for(level, gw, brief_reading=bool(brief_reading and CONTINUE_POSITION))
        + [{"role": "user", "content": user_text}],
    }
    if not payload["max_tokens"]:
        del payload["max_tokens"]                     # 0 on the dial: no cap of ours
    # Round 15 (the User: "limit prompt to 10k tok or so"): what is sent stays under reply.context_tokens; the oldest
    # history goes first, never the system prompt or the turn.
    payload["messages"], _dropped = fit_context(payload["messages"], context_cap())
    if _dropped:
        _log({"event": "context_trimmed", "dropped": _dropped, "est_tokens": est_tokens(payload["messages"])})
    # HARD tool lock: guest, contested, or any turn without this-turn User voice.
    # session_held alone never grants tools (2026-08-12 guest board inject).
    if (
        level in ("guest", "silent", "deny")
        or force_reprove
        or not user_voice
        or level not in ("bau", "allow", "legacy")
    ):
        # tool_choice=none suppresses tools at the API layer.
        payload["tool_choice"] = "none"
    # HER DISPLAY TOOL (round 26, display_call.py): offered on a turn whose tools are allowed, else she is told she
    # cannot show anything this turn (.101: "pulling it up now" three times with no tool)
    _d_tools = display_call.tools(gw["url"]) if level not in ("guest", "silent", "deny") else []
    if _d_tools and "tool_choice" not in payload:
        payload["tools"] = _d_tools
        if any(own_tools.handles(((t or {}).get("function") or {}).get("name")) for t in _d_tools):
            payload["messages"][0]["content"] += own_tools.PROMPT_LINE   # cut 15
        payload["messages"][0]["content"] += display_call.SCREEN_HABIT_LINE
    elif _d_tools:
        payload["messages"][0]["content"] += display_call.NO_SCREEN_LINE
    _d_calls = {"c": None}
    _d_offered = bool(payload.get("tools"))
    t0 = time.time()
    kicked = {"v": False}

    def _kick(buf: str) -> None:
        if kicked["v"] or on_first_clause is None:
            return
        clause = first_speakable_clause(buf)
        if not clause:
            return
        kicked["v"] = True
        kicked["clause"] = clause
        _log({
            "event": "ttfs_clause",
            "profile": gw["profile"],
            "ms": int((time.time() - t0) * 1000),
            "clause": clause[:80],
        })
        on_first_clause(clause)

    rm = reply_meta if reply_meta is not None else {}   # round 17: the finish_reason is read on every turn
    try:
        tool_mark = {"at": 0, "seen": False}

        def _delta(buf: str) -> None:
            _kick(buf)
            if on_more is not None:
                on_more(buf, tool_mark["seen"])

        def _call():
            _d_calls["c"] = display_call.Calls() if payload.get("tools") else None
            return _stream_chat_text(
                gw["url"],
                payload,
                key=gw["key"],
                timeout=_env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 0.0),
                session_id=session_id,
                on_delta=_delta,
                on_tool=speculate.tool_guard(                  # round 17: no tool before his turn is final
                    id_gate.tool_guard(lambda n: tool_mark.update(at=n, seen=True),
                                       lambda: _get(PRESENCE, timeout=1.0), _log)),   # round 17: ID-only, his face
                on_tool_info=on_tool_info,
                meta=rm,
                stop=stop,
                calls=_d_calls["c"],
            )
        _shaped = shape_payload(payload, gw["url"])
        if _shaped:
            _log({"event": "gateway_payload", "profile": gw["profile"], "dropped": _shaped})
        if not speculate.before_ask():           # round 17: a turn that reached for a tool is asked again only once final
            _log({"event": "spec_tool_not_asked", "profile": gw["profile"], "user": user_text[:80]})
            return ""
        try:
            content = _call()
        except Exception as e0:
            # ONE QUIET RETRY (2026-10-12, .101: a lone 403 in 405 ms): an HTTP refusal that does not name the key, while
            # nothing of the answer has been spoken and the turn is still fresh, is asked once more before she says
            # anything. Kill: EAR_BRIDGE_RETRY_FRESH_S=0.
            if quiet_retry_ok(e0, spoken=kicked["v"], elapsed_s=time.time() - t0):
                _log({"event": "gateway_retry", "profile": gw["profile"], "error": str(e0)[:200],
                      "body": logged_body(e0), "ms": int((time.time() - t0) * 1000)})
                content = _call()
            else:
                # A 400 THE REQUEST CAUSED (round 13): asked once more without the keys it names (repair_after_400).
                _fixed = repair_after_400(e0, payload, gw["url"], spoken=kicked["v"])
                if not _fixed:
                    raise
                _log({"event": "gateway_repair", "profile": gw["profile"], "error": str(e0)[:200],
                      "body": logged_body(e0), "dropped": _fixed, "ms": int((time.time() - t0) * 1000)})
                content = _call()
        if _d_calls["c"]:
            # HER OWN CALLS PASS THE SAME GATES (cut 15, 2026-10-29): a plain chat endpoint (Claude, xAI, a local server)
            # sends her tool calls as `tool_calls` deltas, never the gateway's tool event, so on_tool's guards - no tool
            # before his turn is final, and ID-only's face check (FaceFinder at his seat on a camera that turns) - never
            # ran for them. They run here, before a call is done; refused, the turn says the face line (ActRefused).
            # Kill EAR_BRIDGE_OWN_TOOL_GUARD=0.
            if OWN_TOOL_GUARD:
                speculate.tool_guard(id_gate.tool_guard(lambda n: None, lambda: _get(PRESENCE, timeout=1.0),
                                                        _log))(len(content))
            content = _display_round(payload, content, _d_calls["c"], gw, session_id, rm, stop, _delta)
        elif _d_offered and "tools" not in payload:
            display_call.refused(gw["url"])      # the endpoint refused `tools` (repair_after_400): not offered again
        content, after_tool = split_after_tool(content, tool_mark["at"])
        content = content.strip()
    except Exception as e:
        if isinstance(e, speculate.SpecTool):     # round 17: a tool before his turn was final - the read stopped, no tool
            _spec_tool_restart(str(e), user_text)
            return ""
        if isinstance(e, id_gate.ActRefused):     # round 17, Security ID-only: a tool call without his face at the camera
            _log({"event": "tool_refused_no_face", "profile": gw["profile"], "why": str(e),
                  "ms": int((time.time() - t0) * 1000)})
            return id_gate.act_line(str(e))
        if model_refused(e):                      # round 17: the model declined it - not a reach error
            _log({"event": "gateway_refusal", "profile": gw["profile"], "error": str(e)[:200],
                  "code": _http_code(e), "key_refused": False, "body": logged_body(e),
                  "ms": int((time.time() - t0) * 1000)})
            return REFUSAL_LINE
        refused = key_refused(e)
        _log(
            {
                "event": "gateway_error",
                "profile": gw["profile"],
                "error": str(e)[:200],
                "key_refused": bool(refused),
                # the provider's own words (masked, one line): .101's three 400s logged none (round 13)
                "body": logged_body(e) if _http_code(e) is not None else None,
                "ms": int((time.time() - t0) * 1000),
            }
        )
        return gateway_error_line(addressed or gw["profile"], refused)
    if finish_refused(rm):                         # round 17: the model declined in the stream (see Declined)
        _log({"event": "gateway_refusal", "profile": gw["profile"], "finish_reason": rm.get("finish_reason"),
              "code": None, "key_refused": False, "chars": len(content), "ms": int((time.time() - t0) * 1000)})
        return declined_reply(kicked.get("clause"))
    _log(
        {
            "event": "gateway_ms",
            "profile": gw["profile"],
            "addressed": addressed,
            "ms": int((time.time() - t0) * 1000),
            "stream": True,
            "ttfs": kicked["v"],
            "after_tool": bool(after_tool),
        }
    )
    # Speak answer only — never reasoning/thinking fields (the User: none spoken)
    # Drop leaked think blocks if parser left them in content
    content = re.sub(r"<think>[\s\S]*?</think>", " ", content, flags=re.I)
    content = re.sub(r"</?think>", " ", content, flags=re.I)
    content = spoken_from_reply(content)
    # Framework exceptions are not dialogue (the User 2026-08-04: "API call failed
    # after 3 retries: Connection error." went out her mouth verbatim). Keep
    # the raw words in the journal for the janitor/steward flow; speak human.
    if re.match(
        r"\s*(?:API call failed|Traceback \(most recent call last\)|"
        r"[A-Za-z_.]+(?:Error|Exception)\b)",
        content,
    ):
        _log({"event": "gateway_error_reply", "raw": content[:400]})
        return "My brain link hiccuped — give me a second and ask again."
    # Strip accidental markdown / tool-ish dumps / long IDs for the typed
    # reply + logs. Name phonetics (Ada→ā′də) stay in house-speak / Cosy —
    # never in the logged/typed string (the User 2026-08-13).
    content = _for_display(content)
    # Drop CJK if model slips into Chinese (Cosy will speak whatever we pass)
    if re.search(r"[\u4e00-\u9fff]", content):
        content = re.sub(r"[\u4e00-\u9fff]+", " ", content)
        content = re.sub(r"\s+", " ", content).strip()
        if not content or len(content) < 8:
            content = "Sorry — let me say that in English. What do you need?"
    if not content:
        return "I heard you, but I have nothing useful to say."
    # An answer speaks in full (the User 2026-10-02); the callout trim is for tool-result callouts only, and
    # a model answer is not one - so no cap here.
    content = _spoken_tool_callout(content)
    if not content:
        return "I heard you, but I have nothing useful to say."
    content = claim_checked(content, session_id=session_id, profile=gw["profile"], after_tool=after_tool)
    return AfterTool(content) if after_tool else content


# THE REVISE TURN SPEAKS, IT DOES NOT ACT (the User 2026-09-30: "block tool calls in the
# revise-the-rest-of-the-reply turn only"). 10:16 that turn - "he kept talking, say the corrected rest" - reached
# for her hands again and ran the display tool a third time: 65 more seconds of silence on a turn whose whole
# job is one sentence. The gateway has no per-request way to withhold tools from its agent, so the bridge
# enforces it: the prompt says no tools, and the first tool event frame ends the read. Closing the
# stream is how the gateway learns (it interrupts the agent on an SSE disconnect), and the turn returns TEXT
# ONLY - the draft it was asked to correct, else what she had said before reaching. Kill
# EAR_BRIDGE_REVISE_NO_TOOLS=0 (the revise turn may use tools again).
REVISE_NO_TOOLS = os.environ.get("EAR_BRIDGE_REVISE_NO_TOOLS", "1").strip().lower() not in ("0", "false", "no", "off")
REVISE_NO_TOOLS_LINE = " Do not call any tool in this turn: answer from what you already know."


class _ReviseToolCall(Exception):
    """The revise turn reached for a tool; the read stops here."""


def crissa_revise_remainder(
    *,
    spoken: str,
    extra: str,
    draft_rest: str,
    http_profile: str = "crissa",
    session_id: str | None = None,
    on_first_clause=None,
) -> str:
    """Regenerate the unspoken rest after he continued mid-reply."""
    gw = _gateway(http_profile)
    payload = {
        "model": gw["model"],
        "max_tokens": reply_budget() or 4096,   # round 15: the talk dial (EAR_BRIDGE_MAX_TOKENS overrides)
        "temperature": 0.35,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": REVISE_SYSTEM + (REVISE_NO_TOOLS_LINE if REVISE_NO_TOOLS else "")},
            {
                "role": "user",
                "content": remainder_revise_user(spoken, extra, draft_rest),
            },
        ],
    }
    t0 = time.time()
    kicked = {"v": False}
    heard = {"text": ""}

    def _no_tools(_at: int) -> None:
        raise _ReviseToolCall()

    def _kick(buf: str) -> None:
        if kicked["v"] or on_first_clause is None:
            return
        clause = first_speakable_clause(buf)
        if not clause:
            return
        kicked["v"] = True
        _log(
            {
                "event": "ttfs_clause",
                "profile": gw["profile"],
                "ms": int((time.time() - t0) * 1000),
                "clause": clause[:80],
                "revise": True,
            }
        )
        on_first_clause(clause)

    def _delta(buf: str) -> None:
        heard["text"] = buf
        _kick(buf)

    try:
        content = _stream_chat_text(
            gw["url"],
            payload,
            key=gw["key"],
            timeout=_env_http_timeout("EAR_BRIDGE_CRISSA_TIMEOUT", 0.0),
            session_id=session_id,
            on_delta=_delta,
            on_tool=_no_tools if REVISE_NO_TOOLS else None,
        ).strip()
    except _ReviseToolCall:
        # the stream is closed (the `with` in _stream_chat_text), which interrupts the agent on the gateway
        content = ((draft_rest or "").strip() or heard["text"].strip())
        _log({"event": "revise_tool_blocked", "profile": gw["profile"],
              "ms": int((time.time() - t0) * 1000), "chars": len(content),
              "kept": "draft" if (draft_rest or "").strip() else "said_before_the_tool"})
    except Exception as e:
        _log(
            {
                "event": "revise_error",
                "profile": gw["profile"],
                "error": str(e)[:200],
                "ms": int((time.time() - t0) * 1000),
            }
        )
        return (draft_rest or "").strip()
    content = re.sub(r"<think>[\s\S]*?</think>", " ", content, flags=re.I)
    content = re.sub(r"</?think>", " ", content, flags=re.I)
    content = spoken_from_reply(content)
    _log(
        {
            "event": "revise_ms",
            "profile": gw["profile"],
            "ms": int((time.time() - t0) * 1000),
            "stream": True,
            "ttfs": kicked["v"],
            "chars": len(content),
        }
    )
    return (content or "").strip()


CALLOUT_MAX = 220


def _spoken_tool_callout(content: str, *, callout: bool = False) -> str:
    """Reduce a TOOL-RESULT CALLOUT to info only (strip process / offers), short, ending at a sentence.

    The User 2026-10-02: an ordinary answer is NOT a callout - it gets no cap and speaks in full. Since
    2026-08-12 this ran on every free-air reply and cut it to two sentences and then 220 raw characters, so her
    answers stopped mid-word ("...persisted there, so when", 681 chars returned, 220 spoken). Only a caller
    that is speaking a tool result passes callout=True; everything else comes back as it went in.
    """
    if not callout:
        return (content or "").strip()
    t = (content or "").strip()
    # Drop process / framing openers (repeat until stable)
    for _ in range(3):
        nt = re.sub(
            r"^(?:okay[,.]?\s+|alright[,.]?\s+|sure[,.]?\s+|got it[,.]?\s+|done[,.]?\s+)?"
            r"(?:board survey complete[.!]?\s*|"
            r"here(?:'s| is) the full picture[^.]{0,40}[.!]?\s*|"
            r"i(?:'ve| have)? (?:just )?(?:checked|looked(?: that)? up|surveyed|"
            r"pulled|loaded|opened)[^.]{0,100}[.!]\s*|"
            r"looking (?:that|it) up[^.]{0,40}[.!]\s*|"
            r"let me (?:check|look|pull|file)[^.]{0,80}[.!]\s*|"
            r"the (?:kan ?ban )?board is (?:now )?(?:up|showing|visible)[^.]{0,80}[.!]\s*)",
            "",
            t,
            count=1,
            flags=re.I,
        ).strip()
        if nt == t:
            break
        t = nt
    # Drop trailing offers / capability tours (common after tool results)
    t = re.sub(
        r"\s*(?:want me to|would you like me to|shall i|i can also|"
        r"happy to walk|go through the details|walk through)[^?]*\??\s*$",
        "",
        t,
        flags=re.I,
    ).strip()
    # Drop "it's pointing to the board at a link" style junk
    t = re.sub(r"\s*(?:at a link|the link is|http\S+)\s*", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,.")
    if not t:
        return ""
    # Callout length: up to 2 sentences within CALLOUT_MAX chars, always ending at a sentence; a first
    # sentence that alone runs long is cut at its last whole word and closed - never mid-word.
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    out = ""
    for p in parts[:2]:
        cand = (out + " " + p).strip()
        if len(cand) > CALLOUT_MAX:
            break
        out = cand
    if not out and parts:
        first = parts[0]
        cut = first.rfind(" ", 0, CALLOUT_MAX)
        out = (first[:cut] if cut > 0 else first[:CALLOUT_MAX]).rstrip(" ,;:-") + "."
    return out


def _reset_speech_logs() -> None:
    """New process = new session. Do not replay last week's mouth.

    Truncates the jsonl the Control Panel tails, empties the TTS gate
    last_reply, and clears leftover chalkboard text. Journal history stays
    in systemd; this is the live speech surface.
    """
    for p in (LOG, SPEAK_LOG):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", encoding="utf-8")
        except OSError:
            pass
    _write_tts_gate(speaking=False, last_reply="", deaf_until=0.0)
    try:
        req = urllib.request.Request(
            FACE_CHALK,
            data=json.dumps({"kind": "clear", "target": "chalk"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def _write_tts_gate(
    *,
    speaking: bool,
    last_reply: str,
    deaf_until: float,
    notice: str = "",
) -> None:
    """Let rail-stt drop Cosy echo from the turn log (shared gate file)."""
    try:
        TTS_GATE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "house_tts_gate/v1",
            "speaking": bool(speaking),
            "last_reply": (last_reply or "")[:800],
            "who": "crissa",
            "deaf_until": float(deaf_until),
            "notice": (notice or "")[:80],
            "updated_ts": time.time(),
        }
        TTS_GATE.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    except OSError:
        pass


def _gate_notice() -> str:
    """Mic/TTS-gate notice, if still fresh (name-interrupt toast)."""
    try:
        if not TTS_GATE.is_file():
            return ""
        g = json.loads(TTS_GATE.read_text(encoding="utf-8"))
        if not isinstance(g, dict):
            return ""
        notice = str(g.get("notice") or "").strip().lower()
        if not notice:
            return ""
        ts = float(g.get("updated_ts") or 0.0)
        if ts and (time.time() - ts) > 8.0:
            return ""
        return notice
    except Exception:
        return ""


# HER NAME AND A STOP STOP HER AT ONCE, WHATEVER THE VOICE PRINT SAYS (round 16, .101 2026-10-13 00:40:39: "Crissa,
# stop." came with no voice print, `pid null` under the ear's identity hold, so the name-interrupt refused it and it
# went to the model over her line: ~5 s, ~10 s when the first try was dropped; "Chris, stop." with a print stopped her
# at once). Kill: EAR_BRIDGE_NAME_STOP_ANY_VOICE=0 (round 15: any media flag or a scratch voice holds it back).
NAME_STOP_ANY_VOICE = os.environ.get("EAR_BRIDGE_NAME_STOP_ANY_VOICE", "1").strip().lower() not in (
    "0", "false", "no", "off")


# A PAUSE IS FRESH for this long after her last stop or pause (cancels.json): his continue lifts it (hear_gate, round
# 16 (c)). Kill: EAR_BRIDGE_PAUSE_LIFT=0 (hear_gate reads it too).
PAUSE_LIFT_S = float(os.environ.get("EAR_BRIDGE_PAUSE_LIFT_S", "600"))


def resume_pending(now: float | None = None) -> bool:
    """Was she stopped or paused within PAUSE_LIFT_S?"""
    try:
        rec = cancels.latest()
        ts = float(rec.get("ts") or 0.0)
    except Exception:  # noqa: BLE001
        return False
    now = time.time() if now is None else float(now)
    return bool(ts) and 0.0 <= now - ts <= PAUSE_LIFT_S


def name_interrupt_hit(
    text: str,
    *,
    speaking: bool,
    busy: bool,
    stamp: dict | None = None,
    person_id: str | None = None,
) -> bool:
    """True when she is on the air (or generating) and he said her name.

    TV / scratch vocative must not cut her off — same test as unpause
    (cf3ef85). Pause means her mouth and her reflexes, not only the LLM.
    """
    if not (speaking or busy):
        return False
    pid = str(person_id or (stamp or {}).get("person_id") or "").strip()
    if not NAME_STOP_ANY_VOICE and (media_turn(stamp) or is_scratch(pid)):
        return False
    wm = barge_match(text)        # round 14: wake_match, then her name as the ear writes it over her line ("Chris")
    if not wm.get("hit"):
        return False
    if NAME_STOP_ANY_VOICE:
        # round 16: only the media gate's own verdict (TV-shaped sound) holds a barge-in back - an identity hold on a
        # short line with no voice print is not media (hear_gate.wake_media) - and a voice the ear could not name may
        # stop her with her name, never ask her something over her line
        if wake_media(stamp):
            return False
        if is_scratch(pid) and not stop_only(str(wm.get("residual") or "")):
            return False
    profile = str(wm.get("profile") or "").strip().lower()
    route = str(wm.get("route") or "direct").strip().lower()
    return profile == "crissa" and route == "direct"


_pa_yield_at = 0.0


def pa_yield_for_hear(gate: dict, *, session_open: bool = False) -> None:
    """Cut a running PA line on vocative, User-at-mic, or Active session speech.

    house-speak yields before Cosy paplay. That never ran when pause skipped
    the reply (measured 2026-08-28 16:50: PA on, user_ready, crissa_skip).
    Active/LISTENING used to wait for Cosy (2–4 s first hop) when the turn
    had no voice stamp yet (16:59:14 pid null). Subprocess — rails do not
    import crissa. Non-blocking: the yield waits up to 1.5s for paplay
    death and must not stall the ear loop.
    """
    user_mic = bool(gate.get("user_ready") or gate.get("user"))
    if not (gate.get("unpause") or user_mic or session_open):
        return
    if not PA_PLAY.is_file() or not HOUSE_SPEAK.is_file():
        return
    global _pa_yield_at
    now = time.time()
    if now - _pa_yield_at < 1.2:
        return
    _pa_yield_at = now
    try:
        subprocess.Popen(
            [SPEAK_PYTHON, str(HOUSE_SPEAK), "--pa-yield"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        why = (
            "unpause"
            if gate.get("unpause")
            else ("user_ready" if user_mic else "session_open")
        )
        _log({"event": "pa_yield", "why": why})
    except Exception:
        pass


def abort_mouth(reason: str = "interrupt") -> None:
    """Stop Cosy + paplay. Mic notice: interrupted. Do not finish the old line."""
    global _speaking, _speak_proc, _pending, _cancel_token
    if _speaking or _speak_proc is not None:
        # THE CANCEL RECORD (round 15 (g), cancels.py): where she stopped, for the glass's log and the next turn
        try:
            _rec = cancels.record(reason, _SAY.rows())
            if _rec:
                _log({"event": "cancel", "why": reason, "summary": _rec.get("summary"),
                      "last_sentence": _rec.get("last_sentence"), "spoken_chars": _rec.get("spoken_chars")})
                heard_roll.note("crissa", _rec.get("summary") or "", "cancelled", kind="cancel")
        except Exception as _ce:  # noqa: BLE001 - a record never keeps her talking
            _log({"event": "cancel_record_error", "error": str(_ce)[:160]})
            _rec = None
        her_words.cut(_rec, reason)           # round 18: the log marks the rest of her row as not said
    _cancel_token += 1
    prespeak.PRE.drop()                       # round 26: a rest started during her clause goes unheard too
    proc = _speak_proc
    _speak_proc = None
    if proc is not None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
    try:
        req = urllib.request.Request(
            COSY_ABORT,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.0)
    except Exception:
        pass
    _speaking = False
    with _dispatch_lock:
        _pending = None
    _write_tts_gate(
        speaking=False,
        last_reply="",
        deaf_until=0.0,
        notice="interrupted",
    )
    _log({"event": "interrupt", "reason": reason})


def barge_scope() -> tuple[bool, str]:
    """Is the line on the air one the ear's first word may cut (round 17, asr_barge)? Only this bridge's own lines -
    her replies, her readings, her callouts and acks: the announcer's board lines and Lila (Setup's host, the house's
    Render studio) are other processes' house-speak and never this bridge's, and while Setup walks (init-run.json
    running) nothing of hers is cut: Setup's lines are Setup's. (True, why) / (False, why)."""
    if not asr_barge.on():
        return False, "off"
    if not (_speaking or _speak_proc is not None):
        return False, "not_speaking"
    if setup_running():
        return False, "setup"
    return True, "her_line"


def barge_from_ear(body: dict) -> dict:
    """POST /v1/barge from the ear: {"phase": "cut", "id", "text", ...} stops her now; {"phase": "end", "id", "turn"}
    says how the utterance that cut her ended. The cut reads no voice print and changes no identity: the turn that
    follows is attributed by the normal path."""
    phase = str((body or {}).get("phase") or "cut")
    bid = str((body or {}).get("id") or "")
    now = time.time()
    if phase == "end":
        got = asr_barge.STATE.ended(bid, turn=bool(body.get("turn")), why=str(body.get("why") or ""),
                                    cut_why=str(body.get("cut_why") or ""), now=now)
        _log({"event": "barge_end", "id": bid, "turn": bool(body.get("turn")), "why": body.get("why"), "next": got})
        return {"ok": True, "next": got}
    if asr_barge.STATE.same(bid):
        return {"ok": True, "cut": True, "again": True}
    ok, why = barge_scope()
    if not ok:
        _log({"event": "barge_refused", "id": bid, "why": why, "text": str(body.get("text") or "")[:80]})
        return {"ok": True, "cut": False, "why": why}
    answering = bool(_dispatch_busy)
    abort_mouth("barge_asr")
    t_cut = time.time()
    asr_barge.STATE.cut(bid, partial=str(body.get("text") or ""), via="asr", answering=answering, now=t_cut,
                        cut_why=str(body.get("why") or ""))
    _barge_keep_session(t_cut)
    onset = body.get("onset_ts")
    _log({"event": "barge_cut", "id": bid, "text": str(body.get("text") or "")[:120], "why": body.get("why"),
          "look_ms": body.get("look_ms"), "abort_ms": round((t_cut - now) * 1000.0, 1),
          "since_onset_s": round(t_cut - float(onset), 3) if onset else None, "answering": answering})
    return {"ok": True, "cut": True, "why": why}


def _barge_aside(bt: bool, why: str, text: str = "") -> None:
    """The turn that cut her gave her no turn (asr_barge RESUME): she goes on by herself after RESUME_S. Round 18
    (words only): when its words only stop or hold her ("Hold on.", "Wait.") she stays stopped whoever said them - the
    interrupt words cut with no voice check, and going on over "hold on" would undo it ("go on" resumes)."""
    if not bt:
        return
    if text and asr_barge.words_only_on() and stop_only(text):
        asr_barge.STATE.took("stop (%s)" % why)
        _log({"event": "barge_stop", "text": text[:60], "why": why})
        return
    due = asr_barge.STATE.aside(why)
    _log({"event": "barge_aside", "why": why, "resume_in_s": asr_barge.RESUME_S if due else None,
          "state": asr_barge.STATE.snapshot().get("state")})


def _barge_turn_words(t: dict, text: str) -> int:
    """The ear's word rule (crissa_ear.barge.judge_words: words only, no voice print) on a whole turn, against what she
    was saying while it was spoken: 0 = hers or a listener's word, 1-2 = someone's. 2 when the rule is not here."""
    try:
        from crissa_ear.barge import judge_words
    except Exception:  # noqa: BLE001 - a bridge without the ear beside it: any words are someone's
        return 2
    try:
        s1 = float(t.get("speech_end_ts") or t.get("ts") or time.time())
        s0 = float(t.get("speech_start_ts") or (s1 - float(t.get("duration_s") or 2.0)))
        gates = own_voice_gate_paths()       # (not the phrase the round-13 test anchors on: that one is the main loop's)
        _SAY.sync_gates(gates)
        return int(judge_words(text, _SAY.spoken_during(s0, s1)).get("strength", 2))
    except Exception:  # noqa: BLE001
        return 2


def _barge_turn_cut(t: dict, text: str, now: float) -> bool:
    """A whole turn with someone's words over her line that the ear's looks did not cut (the look lane missed it, or an
    older ear): it cuts her now, before anything else is decided, as a look would have - no over-line ask. Its words are
    judged as a look's are (a whole utterance is its own confirmation, so one word of someone's is enough); what is hers
    goes on to the old path. True when it cut."""
    ok, why = barge_scope()
    words_only = asr_barge.words_only_on()
    if not ok and why == "not_speaking" and words_only and _dispatch_busy and text and not setup_running():
        # round 18: between her pieces (or while a tool works) she is still answering: an interrupt word said there
        # stops the rest of the answer, as one said over a line does (before, it was queued and answered after it)
        s1 = float(t.get("speech_end_ts") or t.get("ts") or now)
        ok = asr_barge.AIR.verdict(float(t.get("speech_start_ts") or s1), now)[0]
    # round 18 (words only): an interrupt word or her name (2); anything else over her answer is side talk, never a cut
    if not ok or not text or _barge_turn_words(t, text) < (2 if words_only else 1):
        return False
    answering = bool(_dispatch_busy)
    abort_mouth("barge_turn")
    key = "turn:" + turn_key(t)
    try:
        from crissa_ear.barge import judge_words as _jw
        _cw = str(_jw(text, _SAY.spoken_during(float(t.get("speech_start_ts") or now), now)).get("why") or "")
    except Exception:  # noqa: BLE001
        _cw = ""
    asr_barge.STATE.cut(key, partial=text, via="turn", answering=answering, now=time.time(), cut_why=_cw)
    asr_barge.STATE.is_turn({"barge_id": key})
    _barge_keep_session(time.time())
    _log({"event": "barge_cut", "id": key, "text": text[:120], "via": "turn", "answering": answering,
          "since_heard_s": round(time.time() - now, 3)})
    return True


def _side_talk_turn(t: dict, text: str, now: float) -> bool:
    """Round 18 (asr_barge SIDE TALK): words without an interrupt word or her name, said while she answers a turn, are
    side talk - not a cut, not a turn, not queued, not put to the over-line ask - and go to the Wall's log as room speech
    (one heard-roll row, lane "room") and nowhere else. Setup's lines and a pending confirm or countdown keep today's
    path (an answer she asked for). True when the turn was side talk (the caller drops it)."""
    if not text or not asr_barge.side_talk_on() or setup_running():
        return False
    try:
        if pending_phase() in ("await_confirm", "countdown"):
            return False
    except Exception:  # noqa: BLE001
        pass
    if _barge_turn_words(t, text) >= 2:
        return False              # an interrupt word or her name is never side talk (heard late, it takes today's path)
    try:
        s1 = float(t.get("speech_end_ts") or t.get("ts") or now)
        s0 = float(t.get("speech_start_ts") or (s1 - float(t.get("duration_s") or 0.0)))
    except (TypeError, ValueError):
        s0 = now
    side, why = asr_barge.AIR.verdict(s0, now)
    if not side:
        if why in ("his_turn_going_on", "answering_her_end"):
            _log({"event": "side_talk_not", "why": why, "text": text[:80]})
        return False
    # .101 round 28 (the User 15:0x: "Could you put that on the screen?" over her reading was dropped as side talk; "She
    # was supposed to get the ambiguous ones for eval herself as she is not busy during broadcasts ... She should be able
    # to stop it herself"): while her line plays, words with no name or interrupt word go to her model (over_line.py:
    # PAUSE / STOP / CONTINUE / IGNORE) instead of the room's log. Kill EAR_BRIDGE_SIDE_TALK_ASK=0.
    if (os.environ.get("EAR_BRIDGE_SIDE_TALK_ASK", "1").strip().lower() not in ("0", "false", "off", "no")
            and over_line.mode() == "model" and (_speaking or _speak_proc is not None)):
        _log({"event": "side_talk_to_her", "text": text[:80], "why": why, "since_start_s": round(now - s0, 2)})
        return False
    st = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
    who = str(t.get("person_id") or st.get("person_id") or "?")
    heard_roll.note(who, text, "side talk", lane="room", now=s0)
    _log({"event": "side_talk", "text": text[:80], "pid": who, "why": why, "since_start_s": round(now - s0, 2)})
    return True


def _barge_tick(now: float) -> None:
    """A cut that gave her no turn: she goes on by herself once RESUME_S has passed and nobody is talking (asr_barge)."""
    snap = asr_barge.STATE.cur
    st = snap.get("state")
    if st not in ("resume", "cut", "heard"):
        return
    busy = bool(_dispatch_busy or _speaking or _speak_proc is not None)
    if not busy and st == "resume" and now >= float(snap.get("resume_at") or 0.0):
        busy = ear_still_hearing()
    got = asr_barge.STATE.due(now, busy)
    if got == "resume":
        meta = asr_barge.STATE.resume_meta()
        _log({"event": "barge_resume", "why": asr_barge.STATE.snapshot().get("why"), "pid": (meta or {}).get("person_id"),
              "level": (meta or {}).get("resume_level")})
        if meta:
            schedule_dispatch(asr_barge.RESUME_TEXT, meta)
    elif got:
        _log({"event": "barge_" + got, **asr_barge.STATE.snapshot()})


THREE_WORD_RESUME_S = float(os.environ.get("EAR_BRIDGE_THREE_WORD_RESUME_S", "3") or 3)


def _three_word_run(user: str, meta: dict) -> None:
    """.101 round 28 (the User 2026-10-17: "build the three word barge rights on .101. Pause only to resume when quiet if
    was not to her. Just politely pausing ... resumes without permission on quiet 3s"): three words of someone's over her
    line stopped her (the ear, why "three_words"); her model says whether they were to her (over_line.decide):
      IGNORE    not to her: she goes on by herself after THREE_WORD_RESUME_S of quiet, no word from her
      CONTINUE  to her, not urgent: she goes on after the quiet, and his words are answered after her line
      STOP      to her, a request: done now (her line stays stopped)
      PAUSE     a hold: she stays paused; "go on" resumes"""
    global _pending
    who = str(meta.get("person_id") or "?")
    where = cancels.position(_SAY.rows())
    got = over_line.decide(user, where, _over_line_ask, request=str(cancels._REQ.get("text") or ""),
                           stop_only=stop_only)
    act = got.get("action") or "IGNORE"
    _log({"event": "three_word_barge", "user": user[:100], "action": act, "by": got.get("by"),
          "spoken_chars": where.get("spoken_chars")})
    if act == "PAUSE":
        asr_barge.STATE.took("paused (three words: a hold)")
        heard_roll.note(who, user, "said over her line: she paused")
        return
    if act == "STOP":
        asr_barge.STATE.took("turn (three words: to her)")
        heard_roll.note(who, user, "said over her line: she stopped to do it")
        schedule_dispatch(user, dict(meta, over_line_done=True))
        return
    if act == "CONTINUE":
        with _dispatch_lock:
            _pending = coalesce_followup(_pending, user, dict(meta, over_line_done=True), vocative=False)
        heard_roll.note(who, user, "said over her line: answered after it")
    else:
        heard_roll.note(who, user, "side talk", lane="room")
    due = asr_barge.STATE.aside("three words, %s" % act.lower(), wait_s=THREE_WORD_RESUME_S)
    _log({"event": "barge_aside", "why": "three words, %s" % act.lower(),
          "resume_in_s": THREE_WORD_RESUME_S if due else None, "state": asr_barge.STATE.snapshot().get("state")})


def _barge_keep_session(now: float) -> None:
    """The person who cut her is mid-sentence: the session stays open for their turn (the ear's engaged hangover, and
    the session follow-up that answers it), from now, as after any line of hers."""
    globals()["_session_extend_until"] = max(float(globals().get("_session_extend_until") or 0.0),
                                             now + idle_sec(bool(globals().get("_comms_hold"))))
    _set_hear_session(True)


# THIS MACHINE ONLY (round 46, the local review of kit crissa-063a2813, 2026-10-23): the bridge's port took a POST from
# any page in the buyer's browser - a form post could stop her (/v1/abort, /v1/barge), pause or hold the session, and a
# page that rebinds its name to 127.0.0.1 could read /health. Every handler runs the shared loopback guard first
# (guard.py, a byte-equal copy of lib/crissa_guard.py); the ear, the player and house-mode send none of the headers it
# refuses. The bridge ships only in the kit: on by default. Kill: CRISSA_LOOPBACK_GUARD=0.
from . import guard as _guard

# The biggest body the bridge reads is a barge cut's JSON, whose only long field is the words heard so far; nothing it
# handles is longer than SPEAK_CHARS characters (its longest line), at most 4 bytes each in UTF-8. A Content-Length
# over that, negative or not a number is refused 400 unread. Dial: EAR_BRIDGE_BODY_MAX (bytes; 0 = no size cap).
try:
    BODY_MAX = int(os.environ.get("EAR_BRIDGE_BODY_MAX", str(SPEAK_CHARS * 4)) or 0)
except ValueError:
    BODY_MAX = SPEAK_CHARS * 4


class _AbortHandler(BaseHTTPRequestHandler):
    """Loopback abort for house-mode. Same process as abort_mouth globals."""

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _body(self) -> bytes | None:
        """The request body, or None after a 400 for a bad Content-Length (round 46)."""
        n = _guard.body_length(self.headers, BODY_MAX if BODY_MAX > 0 else None)
        if n is None:
            self.close_connection = True
            self._send(400, {"ok": False, "error": "bad Content-Length"})
            return None
        return self.rfile.read(n) if n > 0 else b""

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if not _guard.check(self):
            return
        path = self.path.split("?")[0]
        if path == "/health":
            open_ = hear_session_open()
            self._send(
                200,
                {
                    "ok": True,
                    "speaking": bool(_speaking),
                    "session_open": open_,
                    "paused": not open_,
                    "dispatch_busy": bool(_dispatch_busy),
                    "comms_hold": bool(globals().get("_comms_hold")),
                    # HELD mm:ss on both glasses counts this down (epoch s)
                    "comms_hold_until": (
                        float(globals().get("_session_until_pub") or 0.0)
                        if globals().get("_comms_hold") else 0.0
                    ),
                    "comms_hold_sec": COMMS_HOLD_SEC,
                    "speak_quiet_sec": speculate.gap(SPEAK_QUIET_SEC),     # round 17: the kit's gap (speculate.py)
                    "flush_fast_sec": FLUSH_FAST,
                    "speculate": speculate.snapshot(),
                    # the Connect seam: is an agent connected, and does its stream carry tool events
                    "agent_connected": connect.connected(),
                    "tool_events_seen": _TOOL_EVENTS["seen"],
                    "callouts": ("on" if (callouts.armed() and _TOOL_EVENTS["seen"]) else
                                 ("waiting for tool events" if callouts.armed() else "off")),
                    # round 17: one of her conversation lines is on the air and the ear's first word may cut it
                    "barge": bool(barge_scope()[0]),
                },
            )
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not _guard.check(self):
            return
        path = self.path.split("?")[0]
        if path == "/v1/abort":
            try:
                abort_mouth("house-mode")
            except Exception:
                pass
            self._send(200, {"ok": True})
            return
        if path == "/v1/barge":
            raw = self._body()
            if raw is None:
                return
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            try:
                self._send(200, barge_from_ear(body if isinstance(body, dict) else {}))
            except Exception as e:  # noqa: BLE001 - the old path stands
                self._send(200, {"ok": False, "cut": False, "why": "error: %s" % str(e)[:80]})
            return
        if path == "/v1/session/pause":
            # Wall LISTENING chip / CP. Does NOT touch the mic — Voice off is
            # a different switch for a different job (private conversation).
            # The loop consumes this within POLL so drop_session() stays the
            # single place a session closes.
            globals()["_session_open_req"] = False
            globals()["_session_pause_req"] = True
            self._send(200, {"ok": True, "paused": True})
            return
        if path == "/v1/session/open":
            # Wall PAUSED chip. Same as a bare vocative: session_open, desk
            # look, no extra words, no "Here." Mic stays wherever the CP left it.
            globals()["_session_pause_req"] = False
            globals()["_session_open_req"] = True
            self._send(200, {"ok": True, "paused": False})
            return
        if path == "/v1/session/hold":
            # Comms hold. Stops only the idle expiry. Does NOT touch the mic.
            # The loop consumes this within POLL. Arming with no session is
            # ignored there; drop_session clears the flag.
            raw = self._body()
            if raw is None:
                return
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}
            if not isinstance(body, dict) or "on" not in body:
                self._send(400, {"ok": False, "error": "on required"})
                return
            on = body.get("on")
            if not isinstance(on, bool):
                self._send(400, {"ok": False, "error": "on must be bool"})
                return
            globals()["_session_hold_req"] = on
            self._send(200, {"ok": True, "comms_hold": on})
            return
        self._send(404, {"ok": False, "error": "not found"})


def start_abort_listener(port: int | None = None) -> bool:
    """Daemon thread. Bind failure must not take the ear down."""
    p = ABORT_PORT if port is None else int(port)
    try:
        srv = ThreadingHTTPServer((ABORT_HOST, p), _AbortHandler)
    except OSError as e:
        _log({"event": "abort_listen_fail", "port": p, "error": str(e)[:80]})
        return False
    t = threading.Thread(target=srv.serve_forever, name="ear-bridge-abort", daemon=True)
    t.start()
    _log({"event": "abort_listen", "port": p, "host": ABORT_HOST})
    return True


def _memory_append(kind: str, text: str, **meta: object) -> None:
    """Best-effort short-term day log (scripts/house_memory.py when the pack ships it; absent = none)."""
    if not HOUSE_MEMORY.is_file():
        return
    try:
        subprocess.run(
            [
                "python3",
                str(HOUSE_MEMORY),
                "append",
                "--kind",
                kind,
                "--text",
                (text or "")[:500],
                "--source",
                "ear-bridge",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        pass


def _today_memory_bullets() -> str:
    if not HOUSE_MEMORY.is_file():
        return ""
    try:
        r = subprocess.run(
            ["python3", str(HOUSE_MEMORY), "today"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return (r.stdout or "").strip()[:800]
    except Exception:
        return ""


def _cosy_up() -> bool:
    if not COSY_HEALTH:
        return True          # no voice health named: house-speak picks the voice and falls back by itself
    try:
        with urllib.request.urlopen(COSY_HEALTH, timeout=1.5) as r:
            if r.status != 200:
                return False
            body = json.loads(r.read().decode() or "{}")
            return bool(body.get("ok", True))
    except Exception:
        return False


def type_reply(text: str, *, title: str = "crissa") -> bool:
    """Voice-Only typed mouth — 20pt chalk on face-shell (TTS fallback)."""
    t = (text or "").strip()
    if not t or not FACE_CHALK:
        return False
    try:
        req = urllib.request.Request(
            FACE_CHALK,
            data=json.dumps(
                {
                    "kind": "chalk",
                    "typed": True,
                    "role": "reply",
                    "title": title,
                    "text": t[:4000],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            ok = 200 <= r.status < 300
        _log({"event": "type_reply", "ok": ok, "chars": len(t), "title": title})
        return ok
    except Exception as e:
        _log({"event": "type_reply_error", "error": str(e)[:160]})
        return False


def _wait_speak_quiet(token: int, *, quiet_need: float | None = None) -> bool:
    """True once the ear is idle and quiet_need has passed since last speech.

    False on name-interrupt, or if the deadline hits while he is still talking.
    SPEAK_QUIET_SEC (2s) is the mouth start — LLM/TTFS may already be running.
    Round 17 (speculate.py), in a pack: the quiet is turn.gap_s (0.3 s), his turn must be final (the ear quiet for
    turn.final_s), and a cancelled speculation never passes (CRISSA_SPEAK_GAP_S=2 puts the 2.0 s back).
    """
    global _cancel_token
    need = speculate.gap(SPEAK_QUIET_SEC) if quiet_need is None else float(quiet_need)
    spec = speculate.on()
    deadline = time.time() + max(8.0, need + 12.0)
    while time.time() < deadline:
        if _cancel_token != token:
            return False
        if speculate.refuse(token):
            _cancel_token += 1 if _cancel_token == token else 0   # its turn read the moved token: move it once more
            return False
        if (speculate.held() or not speculate.final_ok()) if spec else ear_still_hearing():
            time.sleep(0.05)
            continue
        if (time.time() - _last_user_speech) >= need:
            return speculate.commit(token)
        time.sleep(0.05)
    return (
        _cancel_token == token
        and not ((speculate.held() or not speculate.final_ok()) if spec else ear_still_hearing())
        and (time.time() - _last_user_speech) >= need
        and speculate.commit(token)
    )


def _speak_callout(phrase: str, token: int, gw: dict, turn=None) -> None:
    """L: one callout. Dropped - never queued - when he is speaking (rail-stt's user flag, once it lands), when
    the turn was cancelled, when her mouth is still busy after callouts.wait_s(), or when her first clause
    (arrived meanwhile) says the same; a name interrupt cuts it like any line."""
    if callouts.grace_s() > 0:
        time.sleep(callouts.grace_s())
    deadline = time.time() + callouts.wait_s()
    why = "mouth_busy"
    while time.time() < deadline:
        if _cancel_token != token:
            why = "cancelled"
            break
        if user_speaking_now():
            why = "user_speaking"
            break
        if not (_speaking or _speak_proc is not None or _speak_lock.locked()):
            why = ""
            break
        time.sleep(0.05)
    if not why and turn is not None and turn.repeats_clause(phrase):
        why = "her_clause"
    if not why and speculate.on():
        speculate.note_tool()                     # round 17: her mouth opens on a final turn only (speculate.py)
        if not _wait_speak_quiet(token):
            why = "not_final"
    if why:
        _log({"event": "callout_dropped", "phrase": phrase, "why": why})
        return
    speak_blocking(phrase, voice=gw.get("voice"), chalk_title=gw.get("profile") or "crissa", skip_quiet=True)


NO_PRINT_LINE = "I don't know your voice yet. Run Voice ID from the menu."
NO_PRINT_SAID = HOME / "no-voice-print-said.json"


def not_said_no_print() -> bool:
    """True the first time only (per state home, until a print exists and goes again): the notice is said once."""
    if NO_PRINT_SAID.is_file():
        return False
    try:
        NO_PRINT_SAID.write_text(json.dumps({"said_at": time.time(), "line": NO_PRINT_LINE}), encoding="utf-8")
    except OSError:
        pass
    return True


def speak_blocking(
    text: str,
    *,
    post_cooldown: float | None = None,
    voice: str | None = None,
    chalk_title: str = "crissa",
    skip_quiet: bool = False,
    not_before: float | None = None,
) -> None:
    """Speak (Cosy) and/or type on face-shell when mouth is busy/down.

    REPLY_MODE: auto (default) = Cosy then type on fail; type = wall only;
    voice = Cosy only (legacy).

    Waits until the ear is idle (not in an utterance, not decoding) plus
    SPEAK_QUIET_SEC trailing quiet so a thinking pause is not a reply.
    Name-interrupt (abort_mouth) cancels this call. skip_quiet: vocative
    sit-up ack — Cosy prefix is already ~3 s; waiting quiet pushes "Yeah?"
    past the clip. TTFS first-clause already waited before calling with
    skip_quiet=True.
    """
    global _speaking, _last_reply, _speak_proc
    if not text.strip():
        return
    token = _cancel_token
    if token != _cancel_token:
        return
    # Queue until his voice stops. Cap covers one follow-up utterance plus
    # the 2s mouth start. Name-interrupt aborts immediately.
    # SPEAK_QUIET_SEC is the mouth; FLUSH_FAST is LLM dispatch (predict).
    if not skip_quiet:
        if not _wait_speak_quiet(token):
            if _cancel_token != token:
                _log({"event": "speak_cancelled", "phase": "wait_quiet"})
                return
            _log({"event": "speak_held_user_talking", "chars": len(text)})
            return
    if _cancel_token != token:
        return
    mode = REPLY_MODE if REPLY_MODE in ("auto", "type", "voice") else "auto"
    with _speak_lock:
        if _cancel_token != token:
            return
        _speaking = True
        _last_reply = text
        _SAY.begin(text)
        her_words.on_air(text)                # round 18: where she is in her row on the log
        asr_barge.AIR.line_on(answering=bool(_dispatch_busy))      # round 18: her answer is on the air (side talk)
        # Mouth-open for the wall is Cosy paplay (house-speak), not synth start.
        ok = False
        typed = False
        # Type-only or Cosy clearly down: skip GPU fight, write the wall.
        force_type = mode == "type" or (mode == "auto" and not _cosy_up())
        if force_type:
            typed = type_reply(text, title=chalk_title)
            ok = typed
            _log({"event": "speak_typed", "reason": "mode_or_cosy_down", "ok": ok})
            _speaking = False
            asr_barge.AIR.line_off()
            # Wall chalk is not speech. Empty last_reply so logs/PA do not
            # treat a Cosy-down type as a spoken line.
            _write_tts_gate(speaking=False, last_reply="", deaf_until=0.0)
            return
        # Mouth-open for the wall is Cosy paplay (house-speak), not this
        # moment — logs/synth lead the air by seconds.
        tmp: Path | None = None
        try:
            if not HOUSE_SPEAK.is_file():
                _log({"event": "speak_missing", "path": str(HOUSE_SPEAK)})
            else:
                pin = (voice or CRISSA_VOICE).strip() or CRISSA_VOICE
                spoken = spoken_form(text)
                t0 = time.time()
                _pre = None if not_before else prespeak.PRE.take(spoken, token)   # round 26: started during her clause
                if _pre is not None:
                    proc, tmp = _pre
                    _log({"event": "prespeak_used", "chars": len(spoken)})
                else:
                    proc, tmp = _spawn_speak(spoken, pin, not_before)
                _LINE_START["t"] = t0
                _speak_proc = proc
                # house-speak blocks until paplay ends. A 4096-tok report
                # can run many minutes (≈10 chars/s spoken).
                speak_timeout = max(120.0, min(1800.0, 8.0 + len(spoken) / 10.0))
                try:
                    out, _ = proc.communicate(timeout=speak_timeout)
                except subprocess.TimeoutExpired:
                    abort_mouth("speak_timeout")
                    out = ""
                _speak_proc = None
                rc = proc.returncode if proc.returncode is not None else -1
                ok = speak_ok(rc, out)
                if _cancel_token != token:
                    ok = False
                    _log({"event": "speak_cancelled", "phase": "paplay", "ms": int((time.time() - t0) * 1000)})
                else:
                    _log(
                        {
                            "event": "speak",
                            "rc": rc,
                            "ms": int((time.time() - t0) * 1000),
                            "ok": ok,
                            "chars": len(spoken),
                            "out": (out or "")[:160],
                        }
                    )
        except Exception as e:
            _log({"event": "speak_error", "error": str(e)[:160]})
            ok = False
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
            _speak_proc = None
            _speaking = False
            _SAY.end()
            her_words.off_air()
            asr_barge.AIR.line_off()
            cancelled = _cancel_token != token
            # Busy Cosy / fail → type the same reply on Voice-Only surface
            # Do not type a cancelled line — the interrupt already posted notice.
            if not ok and mode == "auto" and not cancelled:
                typed = type_reply(text, title=chalk_title)
                _log({"event": "speak_fallback_type", "ok": typed})
                ok = ok or typed
            if not cancelled:
                _write_tts_gate(speaking=False, last_reply=text, deaf_until=0.0)


_LINE_START: dict = {"t": 0.0}       # round 26: when the line now speaking was started (prespeak waits for its air)


def spoken_form(text: str) -> str:
    """What speak_blocking hands house-speak for this text (its SPEAK_CHARS cap)."""
    return text[:SPEAK_CHARS] if SPEAK_CHARS > 0 else text


def _spawn_speak(spoken: str, pin: str, not_before: float | None = None):
    """Start house-speak on `spoken` with this voice: (proc, its text file)."""
    cmd = [SPEAK_PYTHON, str(HOUSE_SPEAK), "--voice", pin]
    # --text-file so a report is not chopped at 500 chars / ARG_MAX.
    fd, tmp_path = tempfile.mkstemp(prefix="ear-speak-", suffix=".txt")
    os.close(fd)
    tmp = Path(tmp_path)
    tmp.write_text(spoken, encoding="utf-8")
    cmd.extend(["--text-file", str(tmp)])
    if not_before and float(not_before) > 0:
        cmd.extend(["--not-before", f"{float(not_before):.3f}"])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    return proc, tmp


def _mouth_air_at() -> float | None:
    try:
        a = (_get(MOUTH_URL, timeout=0.6) or {}).get("air_at")
        return float(a) if a is not None else None
    except Exception:  # noqa: BLE001
        return None


def prespeak_rest(rest: str, voice: str | None, token: int) -> None:
    """Round 26 (prespeak.py): start the rest of a reply while her clause is on the air."""
    t = (rest or "").strip()
    if not t or (READ_CHUNK > 0 and len(t) > READ_CHUNK):
        return
    pin = (voice or CRISSA_VOICE).strip() or CRISSA_VOICE
    prespeak.PRE.arm(spoken_form(t), token, float(_LINE_START.get("t") or 0.0),
                     spawn=lambda txt: _spawn_speak(txt, pin), air_at=_mouth_air_at,
                     token_now=lambda: _cancel_token, log=_log)


def speak_ok(rc: int, out: str | None) -> bool:
    """Did house-speak SPEAK the line? Its own status line says so: "ok <engine>..." at the start of a line (ok
    pocket-stream, ok tts-pocket, ok kokoro-buffered ...), rc 0. "ok ref-fallback" played a reference clip, NOT the
    text, and "text-only:" spoke nothing. THE STATUS LINE IS FOUND WHEREVER IT IS (the User 2026-10-10, .101 round 4
    CC: `speak ok false` and `speak_fallback_type ok false` for lines Pocket spoke - house-speak-lines.jsonl rc 0,
    engine pocket - because a pack's voice prints its switches and its voice lines first, so the "ok " the old test
    looked for sat past the first 400 characters)."""
    if rc != 0:
        return False
    lines = [ln.strip().lower() for ln in str(out or "").splitlines()]
    if any(ln.startswith("ok ref-fallback") or ln.startswith("text-only:") for ln in lines):
        return False
    return any(ln.startswith("ok ") or ln.startswith("ok cosy") or " spoke" in (" " + ln) for ln in lines)


def wake_ack_name(meta: dict) -> str:
    """the face check's BEST GUESS at who just spoke — not a green lock.

    the User 2026-08-28: "after wake word, as soon as face says best guess the User.
    Sounded like me — real life like." So this deliberately does not wait for a
    strong letter: his voice embed sits around 0.45 and rarely clears the 0.55
    floor, while the face runs 0.75-0.85. Face is the source; the turn's own
    pid is taken first because it is free and already on the stamp.

    Returns "" when nobody is named, and the ack stays generic. Never guesses a
    name it was not given — being called someone else's name is worse than
    being called nothing.

    ONLY THE EAR NAMES THE USER (the User 2026-10-10, .101 round 4 BB: a guest's wake - pid None, guest_rails true -
    logged `named: <name>` and was answered "Yeah, <name>?"). The face's best guess and presence's last speaker are
    not this turn's voice: with nobody in view, the speaker block still named the User from the turn before, and a
    stranger was greeted by the User's name. So a speaker the ear did not name as the User (no pid, another pid, a
    guest turn) gets the nameless ack. EAR_BRIDGE_WAKE_NAME_FROM_FACE=1 brings the face's guess back (the house's 08-28
    rule) for a turn with no pid that is not on guest rails.
    """
    uid = (user_id_from_mode() or "").strip().lower()
    if not uid:
        return ""
    if meta.get("guest_rails"):
        return ""            # the ear put this turn on guest rails: never the User's name
    pid = str(meta.get("person_id") or "").strip().lower()
    if pid == uid:
        return uid
    if pid:
        return ""            # someone else is speaking; do not call them the User
    if meta.get("is_user") is False:
        return ""            # this mouth is not him (wife), even with no pid
    if os.environ.get("EAR_BRIDGE_WAKE_NAME_FROM_FACE", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return ""            # the ear did not name the User: nameless
    try:
        # Measured 2026-08-28: this endpoint answers in ~1.2ms, so it costs
        # nothing against Cosy's ~2.5s prefix. Short timeout anyway — a slow
        # presence must never delay the ack, it just makes it generic.
        p = _get(PRESENCE, timeout=0.4)
    except Exception:
        return ""
    if not isinstance(p, dict):
        return ""
    # Occupancy user_auth is mill leftover, not Speaker. Visor guess only.
    buf = p.get("face") if isinstance(p.get("face"), dict) else {}
    if str(buf.get("locked") or "").strip().lower() == uid:
        return uid
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    for rec in faces:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("name") or "").strip().lower() == uid:
            return uid
    spk = p.get("speaker") if isinstance(p.get("speaker"), dict) else {}
    if str(spk.get("person_id") or "").strip().lower() == uid:
        return uid
    return ""


def cue_user_talk(named: bool | None = None, out: dict | None = None) -> bool:
    """Name-call from work: POST /v1/vocative (cue then abort). Fail open.

    Conductor gates: already Authed → vocative-up; else wake.
    Laptop-closeup stays desk FT. Glass poseGo("wake") stays a FEED.live no-op.
    `named`: whether the ear named the User on this turn - the player's greet says the name only then (BB,
    2026-10-10); `out` gets the player's answer (its "greet" is the line it will say).
    """
    body = {"by": "vocative"}
    if named is not None:
        body["named"] = bool(named)
    try:
        r = _post_json(
            CONDUCTOR_VOCATIVE,
            body,
            # Enter dental warmup used to exceed 1 s, so this timed out,
            # Yeah, the User? still spoke, and conductor later said Hey the User.
            timeout=8.0,
        )
    except urllib.error.HTTPError as e:
        r = {}
        try:
            r = json.loads(e.read().decode())
        except Exception:
            r = {"taken": False, "why": "http-%s" % e.code}
        _log({
            "event": "cue_user_talk",
            "ok": bool(r.get("taken")),
            "key": VOCATIVE_KEY,
            "why": (r or {}).get("why"),
            "http": e.code,
        })
        return bool(r.get("taken"))
    except Exception as e:
        _log({
            "event": "cue_user_talk",
            "ok": False,
            "error": f"{type(e).__name__}:{e}"[:160],
        })
        return False
    taken = bool(isinstance(r, dict) and r.get("taken"))
    if out is not None and isinstance(r, dict):
        out.update(r)
    abort = (r or {}).get("abort") if isinstance(r, dict) else None
    _log({
        "event": "cue_user_talk",
        "ok": taken,
        "key": (r or {}).get("key") or VOCATIVE_KEY,
        "auth_held": (r or {}).get("auth_held"),
        "when": (r or {}).get("when"),
        "why": (r or {}).get("why"),
        "abort": (abort or {}).get("action") if isinstance(abort, dict) else None,
        "played": (abort or {}).get("played") if isinstance(abort, dict) else None,
    })
    return taken


def _presence_auth_held() -> bool:
    """AUTH already held — same precheck as FF-at-beam. Do not wait the sit-up."""
    try:
        p = _get(PRESENCE, timeout=0.45) or {}
    except Exception:
        return False
    if str(p.get("visit_lamp") or "").strip().lower() == "sage":
        return True
    if p.get("user_auth"):
        return True
    buf = p.get("face") if isinstance(p.get("face"), dict) else {}
    uid = str(p.get("user_id") or "user").strip().lower()
    locked = str(buf.get("locked") or "").strip().lower()
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    return bool(locked == uid and (age is None or age <= 2.5))


def vocative_ack_hold_sec(cued: bool, *, auth_held: bool = False) -> float:
    """When paplay of the vocative ack may start. Default 0 — greet now.

    Sit-up still cues in parallel. Waiting the 6.04 s enter (cap 8 s) was
    the measured 8–10 s Yeah? on AUTH and the same freeze for a stranger.
    A person answers while they turn; she does too, Authed or not.

    EAR_BRIDGE_WAKE_ACK_HOLD_SEC restores the old closeup wait (0–8).
    A failed cue must not wait. auth_held is kept so callers stay honest.
    """
    if auth_held:
        return 0.0
    raw = (os.environ.get("EAR_BRIDGE_WAKE_ACK_HOLD_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, min(8.0, float(raw)))
        except (TypeError, ValueError):
            pass
    return 0.0


# NO "YEAH?" OVER HIS ASK (round 28, .101 2026-10-17 05:31:57-58: "Hey, Crissa." then "Help me plan dinner." one second
# later; her "Yeah?" was on the air while he spoke and his reply was 1.5 s behind it). After a wake the ear watches
# its own capture (in an utterance, or decoding one) for ACK_WAIT_S: if he goes on speaking there is no ack - her own
# "Yeah?" is not said, and the conductor's close-up greet (held WAKE_GREET_HOLD_S there) is cancelled. A bare name
# still gets its ack, ACK_WAIT_S later at most. Kill: EAR_BRIDGE_ACK_WAIT_S=0.
def ack_wait_s() -> float:
    try:
        return max(0.0, float(os.environ.get("EAR_BRIDGE_ACK_WAIT_S", "1.2")))
    except ValueError:
        return 1.2


def asked_after_wake(wait_s: float, hearing=None, sleep=time.sleep, clock=time.time) -> bool:
    """True when the ear hears him again within wait_s (he is asking, not waiting for her)."""
    if wait_s <= 0:
        return False
    hear = hearing or ear_still_hearing
    end = clock() + wait_s
    while clock() < end:
        if hear():
            return True
        sleep(0.1)
    return False


# THE WAKE ACK VARIES (the User 2026-10-30: "Hey is good if we can vary it just random. With name too <User> of course";
# "random Yeah in there as well"): each value is a list split on "|", one picked at random per wake. The named list is
# said only when the ear named the User (wake_ack_name, the 10-10 rule); {name} is the User's own. One value = no variety.
WAKE_ACK_BARE = "Hey.|Hey there.|Yeah?|Hi."
WAKE_ACK_NAMED = "Hey, {name}.|Hey there, {name}.|Yeah, {name}?|Hi, {name}."
# THE NAME ONLY SOMETIMES (the User 2026-10-30: "even with the ID which i like we can still make name only occasionally"):
# when the ear named the User, this share of wakes uses the named list and the rest the bare one. A dial, his word
# "occasionally" read as about one wake in three; EAR_BRIDGE_WAKE_NAME_SHARE=1 = always (the 10-10 behaviour), 0 = never.
WAKE_NAME_SHARE = "0.33"


def name_this_wake() -> bool:
    """Whether a wake the ear named the User says his name this time (WAKE_NAME_SHARE, at random)."""
    import random
    try:
        share = float(os.environ.get("EAR_BRIDGE_WAKE_NAME_SHARE", WAKE_NAME_SHARE))
    except ValueError:
        share = float(WAKE_NAME_SHARE)
    return random.random() < share


def pick_wake_ack(raw: str, fallback: str, name: str = "") -> str:
    """One variant of a "|"-separated ack list, at random; {name} filled when given."""
    import random
    opts = [x.strip() for x in str(raw or "").split("|") if x.strip()] or [fallback]
    return random.choice(opts).replace("{name}", name)


def bare_wake_ack(meta: dict) -> None:
    """Museum pattern: bare name → fixed short ack, NO LLM; session stays open.

    Measured fail 2026-08-01: empty residual flushed as user='Yes?' into Crissa LLM,
    then TTS deaf ate the User's real question even when he waited for her answer.
    User-talk vocative always says this. Sit-up cues in parallel; paplay
    does not wait for the closeup (the User 2026-09-05 — greet while turning).
    Override EAR_BRIDGE_BARE_WAKE_TEXT if Cosy mangles the question mark.
    EAR_BRIDGE_WAKE_ACK_HOLD_SEC restores a closeup wait (0–8 s).
    """
    gw, addressed = _target_from_meta(meta)
    who = wake_ack_name(meta)      # resolve ONCE: the log must match the mouth
    if addressed:
        text = f"{addressed.capitalize()} — one moment."
        pin = CRISSA_VOICE
        title = "crissa"
    else:
        # Do not use "I'm here." — Cosy has collapsed I'm to a near-silent
        # clip (measured 2026-07-31). Bare wake must be audible.
        text = pick_wake_ack(os.environ.get("EAR_BRIDGE_BARE_WAKE_TEXT", WAKE_ACK_BARE), "Yeah?")
        # Once the face check has a best guess, she uses his name — she recognised
        # who called her, the way a person in the room would. No name, no
        # guess: the generic ack stands.
        if who and name_this_wake():
            text = pick_wake_ack(os.environ.get("EAR_BRIDGE_WAKE_ACK_NAMED", WAKE_ACK_NAMED), "Yeah, {name}?",
                                 who.capitalize())
        pin = gw["voice"]
        title = gw["profile"]
    # the ack's TEXT is logged, never its audio (the User 2026-10-10, BB): what she says to whom is on the record
    _log(
        {
            "event": "bare_wake_ack",
            "session_id": meta.get("session_id"),
            "pid": meta.get("person_id"),
            "guest_rails": bool(meta.get("guest_rails")),
            "profile": meta.get("profile"),
            "route": meta.get("route"),
            "addressed": addressed,
            "named": who or None,
            "ack": text,
        }
    )
    cue = {}
    if view_keeps_multitask():
        # round 20: this call is already on the multitasking view - she answers where she is, no new close-up
        cued = False
        _log({"event": "view_keeps_multitask", "session_id": meta.get("session_id")})
    else:
        cued = cue_user_talk(named=bool(who), out=cue)
        if cued:
            view_close_opened()
    if cued and not addressed:
        # Conductor covers the lean-in (Connecting / Authenticating) and
        # says Hey the User when the close-up loop starts. Yeah? on top of
        # that was a second mouth. It names the User only when the ear did (named=who).
        _log({"event": "bare_wake_ack_deferred", "to": "conductor",
              "session_id": meta.get("session_id"), "ack": cue.get("greet") or text})
        globals()["_session_extend_until"] = time.time() + idle_sec(bool(globals().get("_comms_hold")))
        if meta.get("has_ask") or asked_after_wake(ack_wait_s()):   # round 28: he asked (with the name, or went on) - no greet
            try:
                _post_json(CONDUCTOR_GREET_CANCEL, {"by": "ear"}, timeout=1.0)
            except Exception:  # noqa: BLE001  an older player: its greet stands
                pass
            _log({"event": "wake_ack_skipped", "why": "asked", "to": "conductor", "session_id": meta.get("session_id")})
        return
    # .101 round 28 (the User 14:59, "Hey, Crissa! Help me plan dinner." in one breath: "Yeah?" landed inside her answer):
    # the ask came with the name, so there is nothing to wait for - no "Yeah?".
    if not addressed and (meta.get("has_ask") or asked_after_wake(ack_wait_s())):   # round 28: no "Yeah?" over his ask
        _log({"event": "wake_ack_skipped", "why": "asked", "session_id": meta.get("session_id")})
        globals()["_session_extend_until"] = time.time() + idle_sec(bool(globals().get("_comms_hold")))
        return
    hold = vocative_ack_hold_sec(cued, auth_held=_presence_auth_held())
    not_before = (time.time() + hold) if hold > 0 else None
    speak_blocking(
        text,
        post_cooldown=1.0,
        voice=pin,
        chalk_title=title,
        skip_quiet=True,
        not_before=not_before,
    )
    globals()["_session_extend_until"] = time.time() + idle_sec(bool(globals().get("_comms_hold")))



# ---- round 14 (2026-10-12): a long answer read in pieces, as it arrives, and remembered ----------------------------

_PIECE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s|$)")


def next_reading_piece(rest: str, pos: int, *, target: int, chunk: int, done: bool) -> tuple[str | None, int]:
    """The next piece of `rest` (the answer after her first clause) from `pos`, or (None, pos) to wait for more.

    A piece ends at the last COMPLETE sentence end within `chunk` characters once `target` characters are there (or
    the answer has ended); a sentence longer than `chunk` ends at a word. Never mid-word, nothing skipped: the next
    piece starts where this one ended."""
    tail = rest[pos:]
    lead = len(tail) - len(tail.lstrip())
    tail = tail.lstrip()
    if not tail:
        return None, pos
    start = pos + lead
    if done and len(tail) <= chunk:
        return tail.strip(), len(rest)
    if not done and len(tail) < target:
        return None, pos
    window = tail[:chunk]
    ends = [m.end() for m in _PIECE_END.finditer(window)]
    if not done:
        ends = [e for e in ends if e < len(tail)]       # a sentence end at the very edge may still be growing
    if ends and (ends[-1] >= min(target, chunk) // 3 or len(tail) <= chunk):
        e = ends[-1]
        return tail[:e].strip(), start + e
    if len(tail) > chunk:
        sp = window.rfind(" ")
        e = sp if sp > chunk // 3 else chunk
        return tail[:e].strip(), start + e
    if done:
        return tail.strip(), len(rest)
    return None, pos


def _reading_rest(buf: str, clause: str | None) -> str:
    """The streamed answer after her first clause, think blocks out (an open one ends the usable text)."""
    t = re.sub(r"<think>[\s\S]*?</think>", " ", buf or "", flags=re.I)
    k = t.lower().find("<think>")
    if k >= 0:
        t = t[:k]
    return rest_after_clause(t, clause) if clause else ""


def _piece_clean(text: str) -> str:
    """One piece of a reading cleaned as a whole reply is (think tags, markdown and links, CJK)."""
    t = re.sub(r"</?think>", " ", text or "", flags=re.I)
    t = _for_display(t)
    if re.search(r"[一-鿿]", t):
        t = re.sub(r"[一-鿿]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


her_words.set_cleaner(_piece_clean)               # round 18: her row on the log reads as her pieces are cleaned


# ---- round 15 (2026-10-12): what the chat is asked for, and where a stopped reading stopped ------------------------

def _talk_cfg() -> dict:
    """The talk settings (lib/crissa_talk: config/crissa-talk.json, <home>/talk.json, the env overrides), or {}."""
    try:
        from .home import PACK_ROOT
        import sys as _sys
        lib = str(PACK_ROOT / "lib")
        if lib not in _sys.path:
            _sys.path.insert(0, lib)
        import crissa_talk
        return crissa_talk.load()
    except Exception:  # noqa: BLE001 - no talk settings: the defaults below
        return {}


def _as_int(v, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def reply_budget(*, reading: bool = False, cfg: dict | None = None) -> int:
    """max_tokens for a turn: talk reply.max_tokens (4096; EAR_BRIDGE_MAX_TOKENS overrides), and for a reading the
    smaller reply.reading_tokens (1000; EAR_BRIDGE_READING_TOKENS) - "go on" fetches the next part. 0 = no cap."""
    c = ((cfg if cfg is not None else _talk_cfg()).get("reply") or {})
    env = os.environ.get("EAR_BRIDGE_MAX_TOKENS", "").strip()
    total = _as_int(env, 4096) if env else _as_int(c.get("max_tokens"), 4096)
    if reading:
        env_r = os.environ.get("EAR_BRIDGE_READING_TOKENS", "").strip()
        r = _as_int(env_r, 1000) if env_r else _as_int(c.get("reading_tokens"), 1000)
        if r > 0:
            total = min(total, r) if total > 0 else r
    return total


def context_cap(cfg: dict | None = None) -> int:
    """The most a turn sends, in tokens: talk reply.context_tokens (10000; EAR_BRIDGE_CONTEXT_TOKENS). 0 = no cap."""
    env = os.environ.get("EAR_BRIDGE_CONTEXT_TOKENS", "").strip()
    if env:
        return _as_int(env, 10000)
    return _as_int(((cfg if cfg is not None else _talk_cfg()).get("reply") or {}).get("context_tokens"), 10000)


def est_tokens(messages: list[dict]) -> int:
    """About 4 characters a token, plus a few a message (no tokenizer for every provider; this errs on the high side
    for English prose)."""
    return sum(len(str(m.get("content") or "")) // 4 + 4 for m in messages)


def fit_context(messages: list[dict], cap: int) -> tuple[list[dict], int]:
    """`messages` with the OLDEST history dropped until the estimate is under `cap`: never the system prompt (first)
    or the turn (last); history goes in user/assistant pairs. Returns (messages, dropped messages)."""
    if cap <= 0 or est_tokens(messages) <= cap or len(messages) <= 2:
        return messages, 0
    head, hist, last = messages[:1], list(messages[1:-1]), messages[-1:]
    dropped = 0
    while hist and est_tokens(head + hist + last) > cap:
        n = 2 if len(hist) >= 2 and hist[0].get("role") == "user" else 1
        del hist[:n]
        dropped += n
    return head + hist + last, dropped


_READ_ASK = re.compile(r"\b(read|reading|recite|chapter|passage|paragraph|verse|page)\b", re.I)
_GO_ON = re.compile(
    r"\b(go on|keep going|keep reading|read on|continue|carry on|left off|where (?:you|we) (?:were|stopped)|"
    r"pick (?:it |that )?(?:back )?up|resume|start (?:it |that )?(?:again|over)|"
    r"next (?:part|bit|page|paragraph|section))\b", re.I)


# ---- round 16 (2026-10-13, .101 00:41): a stopped reading never comes back on its own -------------------------------
# .101: "Okay, you can stop for good. Tell me what model you're using." stopped her; "what LLM are you?" was answered
# (and used up the one-time cancel note); then the ear heard "Rock four chat." for "Grok 4 chat?" and she read the next
# Moby-Dick sentence - her history still held the reading's own text, and nothing told the model not to go on. Now:
#   - a stop that ends it ("for good", "that's enough", "no more", ...) closes the reading;
#   - a turn on another subject that is answered after a stop closes it too;
#   - a closed reading stays in her history as one line that says it is closed (not its text to carry on from);
#   - every turn that is not an explicit continue carries "do not continue an earlier reading unless asked".
# Only an explicit continue ("go on", "continue", "where you left off", "start again", "keep reading") resumes.
# Kill: EAR_BRIDGE_READING_CLOSE=0.
READING_CLOSE = os.environ.get("EAR_BRIDGE_READING_CLOSE", "1").strip().lower() not in ("0", "false", "no", "off")
_STOP_FOR_GOOD = re.compile(
    r"\b(for good|that'?s enough|enough (?:for now|of that|reading)|stop reading|no more|we'?re done|i'?m done|"
    r"don'?t (?:continue|go on|keep reading|read)|forget (?:it|the reading|the book|that))\b", re.I)
# round 38 (.101 round 37: "Can you display that document" closed her reading as a new subject): the document she is
# reading from, shown or named, is the reading too
_ABOUT_READING = re.compile(r"\b(book|story|novel|author|chapter|passage|poem|page|paragraph|reading|read|wrote|"
                            r"written|title|document|doc|text)\b", re.I)
_TITLE_SKIP = {"continued", "understood", "starting", "passage", "chapter", "paragraph", "reading", "here", "opening"}
GO_ON_REOPENS = os.environ.get("EAR_BRIDGE_GO_ON_REOPENS", "1").strip().lower() not in ("0", "false", "no", "off")
ASK_WINS = os.environ.get("EAR_BRIDGE_ASK_WINS", "1").strip().lower() not in ("0", "false", "no", "off")
NO_RESUME_NOTE = (" Earlier you read something aloud to them. Don't wander back into it on your own; answer what they "
                  "just said - unless they ask you to read or go on; then do it, from where you stopped if they say so. "
                  "If you are not sure which they mean, ask them (\"Want me to pick up where I stopped?\"); never "
                  "simply refuse.")   # round 38, the User: "unless specified or the like ... A bit unnerving"
_READING_STATE: dict = {"closed_at": 0.0, "why": ""}


def reading_closed_since(ts: float) -> bool:
    """True when a reading recorded at `ts` has been closed after it."""
    return READING_CLOSE and float(_READING_STATE.get("closed_at") or 0) >= float(ts or 0) > 0


def close_reading(why: str, now: float | None = None) -> None:
    """Close her last reading: "go on" no longer finds it and her history keeps it as a closed line."""
    if not READING_CLOSE:
        return
    # ONE STEP WITH THE CANCEL RECORD (round 16, the race on live: the next turn still got "[Context from your
    # interrupted spoken reply]" when cancel.json could not be read back): the close retires the pending cancel note
    # itself, and the next turn drops any note that is pending then, whichever came first, with no file read.
    _READING_STATE.update(closed_at=time.time() if now is None else float(now), why=str(why)[:40], drop_note=True)
    try:
        cancels.context_note(consume=True)
    except Exception:  # noqa: BLE001 - the note is a help, never a failure
        pass
    try:
        _CONVO.close_reading()
    except Exception:  # noqa: BLE001
        pass
    _log({"event": "reading_closed", "why": why})


def note_stop_words(text: str) -> bool:
    """His words with a stop: one that ends it ("for good", "that's enough") closes the reading. True when it did."""
    if READING_CLOSE and text and _STOP_FOR_GOOD.search(text):
        close_reading("stop_for_good")
        return True
    return False


_SHOW_IT = re.compile(r"\b(show|display|put|open|bring)\b.{0,24}\b(it|that|this)\b", re.I)   # round 38


def about_reading(text: str, rec: dict | None) -> bool:
    """Is this turn about her reading (so it does not close it)? A continue, an ask to read, a word about books or
    reading, showing it ("display that", round 38), or a word of its title."""
    t = text or ""
    if _GO_ON.search(t) or _READ_ASK.search(t) or _ABOUT_READING.search(t) or _SHOW_IT.search(t):
        return True
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", str((rec or {}).get("title") or ""))} - _TITLE_SKIP
    return any(w.lower() in words for w in re.findall(r"[A-Za-z]{4,}", t))


# HER OWN MODEL (round 16, .101: "what LLM are you?" -> "I'm Grok 4", where Connect says grok-4.20-0309-non-reasoning):
# one short line from Connect's config - the model id and who serves it - so she answers that truthfully. Never a
# literal: the line is built from the endpoint she is using. Kill: EAR_BRIDGE_MODEL_LINE=0.
MODEL_LINE = os.environ.get("EAR_BRIDGE_MODEL_LINE", "1").strip().lower() not in ("0", "false", "no", "off")
_PROVIDERS = (("x.ai", "xAI"), ("anthropic.com", "Anthropic"), ("openai.com", "OpenAI"),
              ("googleapis.com", "Google"), ("mistral.ai", "Mistral"), ("groq.com", "Groq"),
              ("openrouter.ai", "OpenRouter"), ("deepseek.com", "DeepSeek"))


# HER MODEL'S NAME, SAID ALOUD (round 17, .101 2026-10-13 05:57: "What model are you?" -> "grok-4.", Connect's model is
# grok-4.20-0309-non-reasoning: the id is a machine key, and its build stamp went out with the speech cleaner and took
# the ".20" with it). The model line also gives the id's spoken form - the family named, the version kept, date and
# build stamps dropped: "Grok 4.20, non-reasoning" - and the exact id stays in the line for an ask for the exact id.
# Kill: EAR_BRIDGE_MODEL_SPOKEN=0 (round 16's line, the id alone).
MODEL_SPOKEN = os.environ.get("EAR_BRIDGE_MODEL_SPOKEN", "1").strip().lower() not in ("0", "false", "no", "off")
_FAMILY_SAY = {"gpt": "GPT", "grok": "Grok", "claude": "Claude", "llama": "Llama", "qwen": "Qwen", "gemini": "Gemini",
               "gemma": "Gemma", "mistral": "Mistral", "mixtral": "Mixtral", "deepseek": "DeepSeek", "phi": "Phi",
               "glm": "GLM", "kimi": "Kimi", "command": "Command", "olmo": "OLMo", "granite": "Granite"}
# CLAUDE'S NAMES (round 17, the Claude preset, 2026-10-13): claude-haiku-5-5 is "Claude Haiku 5.5" (never "claude haiku
# five five"); an id of the older shape (claude-3-5-sonnet-20241022) puts the tier after the version, and it is said
# that way too, "Claude 3.5 Sonnet", not "Claude 3.5, sonnet". Kill: CRISSA_CONNECT_CLAUDE_PRESET=0 (the older shape as
# before).
_CLAUDE_TIERS = ("haiku", "sonnet", "opus", "fable", "mythos")
MODEL_SPOKEN_CLAUDE = claude_on()
_STAMP = re.compile(r"^(?:\d{4}|\d{6}|\d{8}|latest)$", re.I)       # 0309, 202405, 20250929, latest
_VERSION = re.compile(r"^\d+(?:\.\d+)*[a-z]?$", re.I)              # 4, 4.20, 3.5, 4o


def spoken_model(model: str) -> str:
    """A model id as it is said aloud: grok-4.20-0309-non-reasoning -> "Grok 4.20, non-reasoning"; claude-sonnet-4-5-
    20250929 -> "Claude Sonnet 4.5"; gpt-4o-mini -> "GPT 4o, mini". A provider prefix ("x-ai/") and date / build
    stamps are dropped. "" when nothing is left."""
    m = str(model or "").strip().split("/")[-1].split(":")[0]
    toks = [t for t in re.split(r"[-_\s]+", m) if t]
    # a dated id's YYYY-MM-DD (2024-08-06) is three tokens: drop it whole
    out, i = [], 0
    while i < len(toks):
        if re.fullmatch(r"\d{4}", toks[i]) and i + 2 < len(toks) and all(re.fullmatch(r"\d{2}", x) for x in toks[i + 1:i + 3]):
            i += 3
            continue
        out.append(toks[i])
        i += 1
    toks = [t for t in out if not _STAMP.match(t)]
    if not toks:
        return ""
    name: list[str] = []
    while toks and not _VERSION.match(toks[0]):
        t = toks.pop(0)
        name.append(_FAMILY_SAY.get(t.lower(), t.capitalize() if t.isalpha() and t.islower() else t))
    ver: list[str] = []
    while toks and _VERSION.match(toks[0]):
        ver.append(toks.pop(0))
    if len(ver) > 1 and all(v.isdigit() and len(v) <= 2 for v in ver):
        ver = [".".join(ver)]                                           # claude-...-4-5 -> 4.5
    head = " ".join(name + ver).strip()
    rest = "-".join(toks)
    if MODEL_SPOKEN_CLAUDE and name == ["Claude"] and rest.lower() in _CLAUDE_TIERS:
        head, rest = head + " " + rest.capitalize(), ""                # claude-3-5-sonnet -> "Claude 3.5 Sonnet"
    return (head + (", " + rest if rest else "")).strip(", ") if head else rest


def model_line(gw: dict) -> str:
    """" You run on the language model "<model>" from <provider>. ..." from the endpoint in use; "" without a model."""
    if not MODEL_LINE:
        return ""
    model = str((gw or {}).get("model") or "").strip()
    if not model or len(model) > 120:
        return ""
    host = (urllib.parse.urlsplit(str((gw or {}).get("url") or "")).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1") or host.startswith("192.168.") or host.endswith(".local"):
        who = "an engine on this computer or its own network"
    else:
        who = next((name for dom, name in _PROVIDERS if host == dom or host.endswith("." + dom)), host or "your provider")
    said = spoken_model(model) if MODEL_SPOKEN else ""
    if said and said.lower() != model.lower():
        return (' You run on the language model "%s" from %s; said aloud, its name is "%s". If they ask which model or '
                "LLM you are, say exactly that spoken name; give the exact id only if they ask for the exact id."
                % (model, who, said))
    return (' You run on the language model "%s" from %s. If they ask which model or LLM you are, say exactly that.'
            % (model, who))


def _bare_title(title: str) -> str:
    """A title without the "(continued)" marks a continuation adds (round 16: they stacked on .101)."""
    return re.sub(r"(\s*\(continued\))+\s*$", "", str(title or "")).strip()


def _short_title(title: str, n: int = 100) -> str:
    """The bare title in at most n characters, cut at its last sentence end inside them (else a word): a closed
    reading's line names it without carrying any of its text."""
    t = " ".join(_bare_title(title).split())
    if len(t) <= n:
        return t
    head = t[:n]
    ends = [m.end() for m in _PIECE_END.finditer(head)]
    if ends:
        return head[: ends[-1]].strip()
    return head[: head.rfind(" ")].strip() if " " in head else head


def reading_record(mem: "ConvoMemory | None" = None, now: float | None = None) -> dict | None:
    """Where her last reading (or stopped answer) stands, for "go on": the ear's cancel record (cancels.py,
    <home>/cancel.json: title / last_sentence / next_sentence / summary) when it is the newest and under HISTORY_IDLE_S
    old, else this bridge's own memory of a reading she finished or was stopped in."""
    now = time.time() if now is None else now
    mine = mem.last_reading(now=now) if mem is not None else None
    try:
        rec = cancels.latest() if cancels.on() else {}
    except Exception:  # noqa: BLE001 - the cancel record is a help, never a failure
        rec = {}
    ts = float(rec.get("ts") or 0) if isinstance(rec, dict) else 0.0
    if mine and reading_closed_since(float(mine.get("ts") or 0)):
        mine = None                                       # round 16: closed by "for good" or a new subject
    if ts and reading_closed_since(ts):
        ts = 0.0
    if (ts and now - ts <= HISTORY_IDLE_S and (rec.get("last_sentence") or rec.get("summary"))
            and ts >= float((mine or {}).get("ts") or 0) - 60):   # its own stop is noted just before the turn ends
        return {"title": _bare_title(rec.get("title") or rec.get("request") or ""),
                "last_sentence": rec.get("last_sentence") or "",
                "next_sentence": rec.get("next_sentence") or "", "summary": rec.get("summary") or "",
                "stopped": True, "ts": ts, "source": "cancel"}
    return mine


# A "GO ON" SENDS WHERE SHE IS, NEVER THE TEXT (round 17, .101 2026-10-13 05:54: both "Keep reading" turns were refused
# by xAI - 403 "I'm sorry, I can't help with that request." - while the first ask to read streamed fine and every other
# turn worked). The continue turn carried about 1,500 characters of the book (her history's reading, the cancel note's
# "said", the last sentence twice, the next one) and asked for what follows "word for word": the shape of asking a model
# to reproduce a long text it was handed, which a provider's filter blocks whatever the text is. The kit holds no text
# of its own (she reads from the model's own knowledge, as the first ask does), so a continue now looks like that
# first ask: the title and a place of a few words (ANCHOR_WORDS) - the end of the last sentence she said and the start
# of the one she was cut off in. Her history keeps each reading as one line with that place, and the cancel note (its
# "said") is not added on that turn. Kill: EAR_BRIDGE_CONTINUE_POSITION=0 (round 16's note, history and cancel note).
CONTINUE_POSITION = os.environ.get("EAR_BRIDGE_CONTINUE_POSITION", "1").strip().lower() not in (
    "0", "false", "no", "off")
ANCHOR_WORDS = 8


def _tail_words(text: str, n: int = ANCHOR_WORDS) -> str:
    w = " ".join(str(text or "").split()).split(" ")
    return " ".join(w[-n:]).strip()


def _head_words(text: str, n: int = ANCHOR_WORDS) -> str:
    w = " ".join(str(text or "").split()).split(" ")
    return " ".join(w[:n]).strip()


def position_note(last: dict) -> str:
    """The continue turn's note (CONTINUE_POSITION): what she was reading and where she stopped, in a few words."""
    title = _short_title(str(last.get("title") or "")) or "a text"
    note = " You were reading aloud to them: %s They asked you to go on." % title
    tail = _tail_words(last.get("last_sentence") or "")
    if tail:
        note += ' You stopped just after the words "...%s".' % tail
    head = _head_words(last.get("next_sentence") or "")
    if head:
        note += ' You were cut off in the sentence that begins "%s ..."; start again at the start of it.' % head
    return note + (" Read on aloud from there as you did before. Do not go back over what you already read, and do "
                   "not say where you are.")


# A FIRST ASK TO READ READS ON (round 20, .101 2026-10-14 08:57: "You start reading from Moby Dick." -> "Call me
# Ishmael." and nothing more, `rest_empty`; the User waited 19 s and said "Continue."; round 18's first reading stopped
# after one 259-character piece the same way). Only a continue carried a note (position_note); a first ask went to the
# model as a plain chat turn, and a voice chat's brief answer to "read X" is its first line. A first ask now carries
# READ_ASK_NOTE: read the text itself aloud from its start, at length, as one long answer the reader says piece by
# piece (READ_CHUNK) up to the reading budget. Kill: EAR_BRIDGE_READ_ASK_NOTE=0.
READ_ASK_NOTE_ON = os.environ.get("EAR_BRIDGE_READ_ASK_NOTE", "1").strip().lower() not in ("0", "false", "no", "off")
# The note goes only with an ask to read a text ALOUD (READ_ALOUD_ASK), never with any turn _READ_ASK matches ("did you
# read my email?" is a question, not a reading).
READ_ALOUD_ASK = re.compile(
    r"\b(?:start|begin|keep|go on)\s+reading\b"
    r"|\b(?:read|recite)\s+(?:me\s+|us\s+)?(?:aloud|out loud|to me|to us|from\b|the (?:opening|start|beginning|first|next)\b"
    r"|a (?:story|poem|chapter|passage|page|bit|little)\b|some\b|chapter\b)"
    r"|\b(?:read|recite)\b.{0,60}\b(?:aloud|out loud|to me|to us)\b", re.I)


def read_aloud_ask(text: str) -> bool:
    return bool(READ_ALOUD_ASK.search(text or ""))


# WEATHER (the User 2026-10-15 on .101: asked "weather" with no location set, the model drafted "It's 72 and sunny
# right now" - made up - then said "checking the weather" and nothing). A turn that asks about the weather carries the
# face shell's own glance (/v1/house "weather", wttr.in for the location the person gave), or, with no location, an
# order to say so and ask for their town - never a guess. "I live in X" / "my town is X" / "set my location to X" (or
# "I'm in X" in a weather turn) writes <home>/weather.json, which the face shell reads on its next glance.
# Kills: EAR_BRIDGE_WEATHER_NOTE=0, EAR_BRIDGE_WEATHER_TOWN=0.
WEATHER_NOTE_ON = os.environ.get("EAR_BRIDGE_WEATHER_NOTE", "1").strip().lower() not in ("0", "false", "no", "off")
WEATHER_TOWN_ON = os.environ.get("EAR_BRIDGE_WEATHER_TOWN", "1").strip().lower() not in ("0", "false", "no", "off")
WEATHER_ASK = re.compile(r"\b(?:weather|forecast|temperature|raining|snowing|sunny|umbrella"
                         r"|how (?:hot|cold|warm) is it)\b", re.I)
_TOWN_TAIL = r"\s+([A-Za-z][A-Za-z .'-]{1,40}?)\s*(?:[.,!?]|$)"
TOWN_SET = re.compile(r"\b(?:i\s+live\s+in|my\s+(?:town|city|location)\s+is|(?:set|change)\s+(?:my\s+)?"
                      r"(?:weather\s+)?location\s+to)" + _TOWN_TAIL, re.I)
TOWN_IN = re.compile(r"\b(?:i'?m|i\s+am)\s+(?:actually\s+|really\s+|now\s+|currently\s+|just\s+)?in"   # round 26:
                     + _TOWN_TAIL, re.I)                                       # "I'm actually in Huntersville"
WEATHER_NO_LOCATION = (" They asked about the weather. You do not know where they are, so you have no weather at all. "
                       "Say you don't have their location yet and ask for their town. Never guess or make up weather.")


def spoken_town(text: str) -> str:
    """The town a turn names for the weather, or ""."""
    t = text or ""
    m = TOWN_SET.search(t) or (TOWN_IN.search(t) if WEATHER_ASK.search(t) else None)
    town = (m.group(1) if m else "").strip(" .'-")
    if not town or town.lower() in ("the", "here", "the house", "my house", "my room", "the kitchen"):
        return ""
    return town


def set_weather_town(town: str, home: Path | None = None) -> bool:
    if not (WEATHER_TOWN_ON and town):
        return False
    try:
        path = Path(home or HOME) / "weather.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"location": town, "by": "spoken", "at": round(time.time(), 1)}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    _log({"event": "weather_town", "town": town})
    return True


# THE LOCATION GUESS (the User 2026-10-16: "can we check system for location info vs adding to scripted questions?"):
# the clock's zone is a whole coast and the OS location services need a permission prompt, so with no town saved her
# first weather turn asks wttr.in where this connection is (the weather service already sees the address on every
# fetch) and she ASKS whether that is right - no weather is given for a guess. A yes on the next turn (within
# GUESS_TTL_S) saves it as the town; another town saves that. Kill EAR_BRIDGE_WEATHER_GUESS=0.
WEATHER_GUESS_ON = os.environ.get("EAR_BRIDGE_WEATHER_GUESS", "1").strip().lower() not in ("0", "false", "no", "off")
GUESS_TTL_S = 300.0
_GUESS: dict = {"place": "", "at": 0.0}
YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|right|that'?s (?:right|correct|it)|it is|sure)\b", re.I)


def guess_place(fetch=None) -> str:
    """Where wttr.in places this connection ("" when it cannot be read)."""
    if not WEATHER_GUESS_ON:
        return ""
    try:
        if fetch is not None:
            raw = fetch()
        else:
            import crissa_egress
            req = urllib.request.Request("https://wttr.in/?format=%l", headers={"User-Agent": "crissa-bridge/1.0"})
            with crissa_egress.urlopen(req, 4.0, "the weather glance") as r:
                raw = r.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - no guess: she asks for the town
        return ""
    place = " ".join(str(raw or "").split()).strip()
    if not place or len(place) > 80 or "unknown" in place.lower() or "<" in place:
        return ""
    return place


def confirm_guess(text: str, now: float | None = None, home: Path | None = None) -> str:
    """A yes to her last location guess saves it; returns the town saved, else ""."""
    now = time.time() if now is None else float(now)
    place = str(_GUESS.get("place") or "")
    if not place or now - float(_GUESS.get("at") or 0) > GUESS_TTL_S or not YES.search(text or ""):
        return ""
    _GUESS.update(place="", at=0.0)
    return place if set_weather_town(place, home) else ""


# A TOWN JUST GIVEN GETS ITS WEATHER AT ONCE (round 26, .101 2026-10-16: his "yes" to her guess saved the town and she
# asked "I can tell you the weather now if you want" - a second ask; and the face shell's glance still held the old
# town's reading). The turn that saves a town after a weather ask reads that town's weather itself (wttr.in, the face
# shell's own source) and she gives it. Kill: EAR_BRIDGE_TOWN_WEATHER=0.
TOWN_WEATHER_ON = os.environ.get("EAR_BRIDGE_TOWN_WEATHER", "1").strip().lower() not in ("0", "false", "no", "off")


def town_weather(town: str, fetch=None) -> str:
    """"Sunny +70°F"-style words for a town now, or "" (no answer, an unknown place)."""
    if not (TOWN_WEATHER_ON and town):
        return ""
    try:
        if fetch is not None:
            raw = fetch(town)
        else:
            import crissa_egress
            url = "https://wttr.in/%s?format=%%C+%%t" % urllib.parse.quote(town)
            req = urllib.request.Request(url, headers={"User-Agent": "crissa-bridge/1.0"})
            with crissa_egress.urlopen(req, 4.0, "the weather for the town just named") as r:
                raw = r.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    txt = " ".join(str(raw or "").split()).strip()
    if not txt or len(txt) > 80 or "unknown" in txt.lower() or "<" in txt or "sorry" in txt.lower():
        return ""
    return txt


def town_note(town: str, asked: bool, fetch=None) -> tuple[str, bool]:
    """(the note for a turn that saved a town, whether it carries the weather): its weather now when the weather was
    what they wanted."""
    if asked:
        w = town_weather(town, fetch)
        if w:
            return (" They told you their town: %s. It is saved. Right now in %s it is: %s. Give them that weather in a "
                    "few words now; never add anything this line does not say." % (town, town, w)), True
    return (" They just told you their town for the weather: %s. It is saved; say so in a few words and, if they asked "
            "earlier, that you can tell them the weather now." % town), False


def weather_note(text: str, fetch=None, guess=None) -> str:
    """The note a weather turn carries ("" = not a weather turn)."""
    if not (WEATHER_NOTE_ON and WEATHER_ASK.search(text or "")):
        return ""
    try:
        w = ((fetch or _get)(_port_url("face_shell", path="/v1/house"), 4.0) or {}).get("weather") or {}
    except Exception:  # noqa: BLE001 - no face shell: say she does not know, never guess
        w = {}
    txt = str(w.get("text") or "").strip()
    if w.get("ok") and txt:
        return (" They asked about the weather. Right now where they are it is: %s. Say it in a few words; never add "
                "anything this line does not say." % txt)
    if "no location" in str(w.get("mark") or ""):
        place = guess_place(guess) if WEATHER_GUESS_ON else ""
        if place:
            _GUESS.update(place=place, at=time.time())
            return (" They asked about the weather. You do not know their town yet. From their internet connection it "
                    "looks like they are near %s. Ask whether that is right, in a few words. Do not give any weather "
                    "until they confirm. Never guess or make up weather." % place)
        return WEATHER_NO_LOCATION
    return (" They asked about the weather and the weather could not be read just now. Say so briefly. Never guess or "
            "make up weather.")


READ_ASK_NOTE = (" They asked you to read aloud. Read the text itself aloud from its beginning, at length: several "
                 "paragraphs in this one answer. Do not stop after the first line, do not introduce or summarize it, and "
                 "do not ask whether to go on.")


def reading_turn(text: str, mem: "ConvoMemory | None" = None) -> tuple[bool, str]:
    """(is this a reading?, a note for her system prompt). "Go on" / "continue" / "where you left off" after a reading
    continues it from the sentence after the one she stopped on; an ask to read is a reading (its own budget)."""
    t = text or ""
    last = reading_record(mem) if mem is not None else None
    if last is None and mem is not None and GO_ON_REOPENS and _GO_ON.search(t):
        # round 38 (.101 round 37, the User): an explicit go-on reopens her last reading where she stopped, closed or
        # not - a close was hers ("for good", a new subject), and his "continue" is his word over it
        last = mem.last_reading(closed_too=True)
        if last:
            _READING_STATE.update(closed_at=0.0, why="")
            mem.reopen_reading()
            _log({"event": "reading_reopened", "title": str(last.get("title") or "")[:80]})
    if last and _GO_ON.search(t) and CONTINUE_POSITION:
        return True, position_note(last)
    if last and _GO_ON.search(t):
        note = (" You were reading aloud to them: %s They asked you to go on. You stopped right after: \"%s\" "
                "Continue reading from the very next sentence of that same text, word for word. Do not repeat "
                "what you already read, do not summarize, and do not say where you are; just read."
                % (last.get("title") or last.get("summary") or "a text", last.get("last_sentence") or ""))
        if last.get("next_sentence"):
            note += " (The sentence you were in was: \"%s\" - start with it.)" % last["next_sentence"][:300]
        return True, note
    return bool(_READ_ASK.search(t)), ""


SPEAK_CPS = float(os.environ.get("EAR_BRIDGE_SPEAK_CPS", "14"))      # her speaking rate, characters a second
SPEAK_LEAD_S = float(os.environ.get("EAR_BRIDGE_SPEAK_LEAD_S", "1.5"))  # a line's start before its first word


def aired_part(piece: str, elapsed_s: float) -> str:
    """How much of `piece` was said when it was stopped `elapsed_s` after it was handed to the voice: the voice's
    start, then SPEAK_CPS characters a second, cut back to the last sentence end (else word) inside that."""
    n = int(max(0.0, elapsed_s - SPEAK_LEAD_S) * SPEAK_CPS)
    if n >= len(piece):
        return piece
    head = piece[:n]
    ends = [m.end() for m in _PIECE_END.finditer(head)]
    if _clause_number_on():                  # round 17: the cut at "4." of "4.20" is not a sentence end (ttfs)
        ends = [e for e in ends if not (e < len(piece) and piece[e].isdigit() and e >= 2 and head[e - 2].isdigit())]
    if ends:
        return head[: ends[-1]].strip()
    sp = head.rfind(" ")
    return head[:sp].strip() if sp > 0 else ""


def _drop_partial_tail(text: str) -> str:
    """A reply cut by its token budget ends mid-sentence: keep it to its last sentence end."""
    t = (text or "").rstrip()
    ends = [m.end() for m in _PIECE_END.finditer(t)]
    return t[: ends[-1]] if ends and ends[-1] < len(t) else t


class Said(str):
    """What she said, and whether a stop cut it short."""
    stopped = False


GO_ON_ASK = os.environ.get("EAR_BRIDGE_GO_ON_ASK", "Shall I go on?").strip()


class StreamReader:
    """Reads a long answer aloud while the rest of it is still arriving (round 14). One per turn.

    feed(buf, tool_seen) runs on the stream's thread with everything received so far; clause_said(clause) runs once
    her first clause has been said. It engages only when both hold and the answer past the clause has READ_ENGAGE
    characters - never after a tool event (an after-tool answer has its own path), never once close() has been called.
    finish() marks the end of the stream; join() waits for the last piece. A moved cancel token (a vocative, a stop)
    ends it between pieces; the piece on the air is stopped by the same abort that moved the token."""

    def __init__(self, speak, token_ok, *, engage=None, first=None, chunk=None, limit=None):
        self._speak = speak
        self._token_ok = token_ok
        self.engage_at = READ_ENGAGE if engage is None else engage
        self.first = READ_FIRST if first is None else first
        self.chunk = READ_CHUNK if chunk is None else chunk
        self.limit = SPEAK_CHARS if limit is None else limit
        self._cv = threading.Condition()
        self._buf = ""
        self._clause: str | None = None
        self._said_clause = False
        self._tool = False
        self._done = False
        self._cut_tail = False
        self._closed = False
        self.engaged = False
        self.capped = False
        self.stopped = False
        self.pieces: list[str] = []
        self.first_piece_at: float | None = None
        self._thread: threading.Thread | None = None
        self.title_cb = None                  # round 15: the reading's title for the ear's cancel record

    def feed(self, buf: str, tool_seen: bool = False) -> None:
        with self._cv:
            self._buf = buf or ""
            self._tool = self._tool or bool(tool_seen)
            self._maybe_engage()
            self._cv.notify_all()

    def clause_said(self, clause: str) -> None:
        with self._cv:
            self._clause = clause
            self._said_clause = True
            self._maybe_engage()
            self._cv.notify_all()

    def _maybe_engage(self) -> None:          # under the lock
        if (self.engaged or self._closed or self._tool or not self._said_clause or not READ_STREAM
                or self.chunk <= 0 or len(_reading_rest(self._buf, self._clause)) < self.engage_at):
            return
        self.engaged = True
        _log({"event": "reading_start", "chars_so_far": len(self._buf), "chunk": self.chunk})
        if self.title_cb is not None:
            try:
                self.title_cb(self._buf)
            except Exception:  # noqa: BLE001 - naming it is a help, never a failure
                pass
        self._thread = threading.Thread(target=self._run, name="reading", daemon=True)
        self._thread.start()

    def close(self) -> bool:
        """No engagement after this; True when this answer is being read."""
        with self._cv:
            self._closed = True
            return self.engaged

    def finish(self, buf: str | None = None, cut_tail: bool = False) -> None:
        """The stream ended; cut_tail: it ended on its token budget, so its last, unfinished sentence is not read."""
        with self._cv:
            if buf is not None:
                self._buf = buf
            self._cut_tail = bool(cut_tail)
            self._done = True
            self._cv.notify_all()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def said(self) -> str:
        return " ".join(self.pieces)

    def _run(self) -> None:
        pos, total = 0, 0
        while True:
            with self._cv:
                while True:
                    if not self._token_ok():
                        self.stopped = True
                        return
                    rest = _reading_rest(self._buf, self._clause)
                    if self._done and self._cut_tail:
                        rest = _drop_partial_tail(rest)
                    piece, npos = next_reading_piece(rest, pos, target=self.first if not self.pieces else self.chunk,
                                                     chunk=self.chunk, done=self._done)
                    if piece is not None or self._done:
                        break
                    self._cv.wait(0.25)
            if piece is None:
                return
            pos = npos
            piece = _piece_clean(piece)
            if not piece:
                continue
            if total + len(piece) > self.limit:
                self.capped = True
                _log({"event": "reading_cap", "said": total, "limit": self.limit})
                return
            if not self._token_ok():
                self.stopped = True
                return
            if self.first_piece_at is None:
                self.first_piece_at = time.time()
            t_piece = time.time()
            self._speak(piece)
            if not self._token_ok():
                # stopped during this piece (round 15): keep only what was said, for "go on"
                aired = aired_part(piece, time.time() - t_piece)
                if aired:
                    self.pieces.append(aired)
                self.stopped = True
                return
            self.pieces.append(piece)
            total += len(piece)


def speak_long(text: str, *, voice: str | None = None, chalk_title: str = "crissa", skip_quiet: bool = False) -> "Said":
    """speak_blocking for an answer of any length (round 14): longer than READ_CHUNK it is said a piece at a time, each
    ending on a sentence, up to SPEAK_CHARS in all; a vocative between pieces stops the rest. Returns what she said (for
    her history); stopped mid-piece, only the part that was said (round 15, aired_part) and .stopped is set."""
    t = (text or "").strip()
    if not t:
        return Said("")
    token = _cancel_token
    if READ_CHUNK <= 0 or len(t) <= READ_CHUNK:
        t0 = time.time()
        speak_blocking(t, voice=voice, chalk_title=chalk_title, skip_quiet=skip_quiet)
        if _cancel_token != token:
            out = Said(aired_part(t, time.time() - t0))
            out.stopped = True
            return out
        return Said(t)
    pos, total, said, stopped = 0, 0, [], False
    while True:
        piece, pos = next_reading_piece(t, pos, target=READ_CHUNK, chunk=READ_CHUNK, done=True)
        if piece is None:
            break
        if total + len(piece) > SPEAK_CHARS:
            _log({"event": "reading_cap", "said": total, "limit": SPEAK_CHARS})
            break
        if _cancel_token != token:
            stopped = True
            break
        t0 = time.time()
        speak_blocking(piece, voice=voice, chalk_title=chalk_title, skip_quiet=skip_quiet if not said else True)
        if _cancel_token != token:
            aired = aired_part(piece, time.time() - t0)
            if aired:
                said.append(aired)
            stopped = True
            break
        said.append(piece)
        total += len(piece)
    _log({"event": "speak_pieces", "chars": len(t), "pieces": len(said), "said": sum(len(x) for x in said),
          "stopped": stopped})
    out = Said(" ".join(said))
    out.stopped = stopped
    return out


OWNER_LEVELS = ("bau", "allow", "legacy", "soft")


def _history_said(said: str, *, reading: bool, msg_chars: int, stopped: bool = False) -> str:
    """What goes in her history for a reply: clipped; a reading keeps its opening and where she stopped."""
    t = " ".join((said or "").split())
    stop_note = "the User stopped her here" if stopped else "she stopped here"
    if len(t) <= msg_chars:
        return t + (" [%s]" % stop_note if reading and stopped else "")
    if reading:
        half = max(80, msg_chars // 2)
        head, tail = t[:half], t[-half:]
        head = head[: head.rfind(" ")] if " " in head else head
        tail = tail[tail.find(" ") + 1:] if " " in tail else tail
        return ("%s ... [read aloud: %d characters; %s:] ... %s" % (head, len(t), stop_note, tail)).strip()
    cut = t[:msg_chars]
    return (cut[: cut.rfind(" ")] if " " in cut else cut) + " ..."


def _opening(text: str, n: int = 220) -> str:
    """The start of a reading - where she names it - to the first sentence end after 40 characters, at most n."""
    t = " ".join((text or "").split())[: n + 40]
    for m in _PIECE_END.finditer(t):
        if m.end() >= 40:
            return t[: m.end()]
    return t[:n]


def _last_sentence(text: str, n: int = 300) -> str:
    t = " ".join((text or "").split())
    ends = [m.end() for m in _PIECE_END.finditer(t)]
    if len(ends) >= 2:
        t = t[ends[-2]:].strip()
    return t[-n:]


class ConvoMemory:
    """The owner's last few exchanges, for an endpoint that keeps no conversation of its own (round 14). Round 15: a
    reading is also kept as its opening (its title) and the last sentence she said, stopped or not, for "go on"."""

    def __init__(self, turns: int | None = None, idle_s: float | None = None, msg_chars: int | None = None):
        self.turns = HISTORY_TURNS if turns is None else turns
        self.idle_s = HISTORY_IDLE_S if idle_s is None else idle_s
        self.msg_chars = HISTORY_MSG_CHARS if msg_chars is None else msg_chars
        self._items: list[tuple[float, str, str, dict | None]] = []
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        if self._items and now - self._items[-1][0] > self.idle_s:
            self._items.clear()

    def add(self, user: str, said: str, *, reading: bool = False, stopped: bool = False, opening: str = "",
            now: float | None = None) -> None:
        u, a = " ".join((user or "").split()), (said or "").strip()
        if not u or not a or self.turns <= 0:
            return
        now = time.time() if now is None else now
        u = u if len(u) <= self.msg_chars else u[: self.msg_chars] + " ..."
        summ = _history_said(a, reading=reading, msg_chars=self.msg_chars, stopped=stopped)
        summ += own_tools.turn_note()             # cut 15: "[What I did this turn: display: shown ...]"
        # the field names the ear's cancel record uses (round 15: title / last_sentence / summary)
        info = ({"title": opening or _opening(a), "last_sentence": _last_sentence(a), "summary": summ,
                 "stopped": bool(stopped), "ts": now, "source": "memory"} if reading else None)
        with self._lock:
            self._expire(now)
            self._items.append((now, u, summ, info))
            del self._items[: -self.turns]

    def messages(self, now: float | None = None, brief_reading: bool = False) -> list[dict]:
        """The exchanges as chat messages. brief_reading (round 17, a continue turn): a reading is one line - what it
        was and the few words she stopped after - never its text."""
        now = time.time() if now is None else now
        with self._lock:
            self._expire(now)
            out: list[dict] = []
            for _, u, a, info in self._items:
                if brief_reading and info and not info.get("closed"):
                    a = '[I was reading %s aloud; %s just after the words "...%s".]' % (
                        _short_title(info.get("title")) or "something",
                        "the User stopped me" if info.get("stopped") else "I stopped",
                        _tail_words(info.get("last_sentence") or ""))
                out += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
            return out

    def last_reading(self, now: float | None = None, closed_too: bool = False) -> dict | None:
        """The newest reading still remembered: {"title", "last_sentence", "summary", "stopped", "ts"}, or None.
        closed_too (round 38): a closed one too (an explicit go-on reopens it)."""
        now = time.time() if now is None else now
        with self._lock:
            self._expire(now)
            for item in reversed(self._items):
                if item[3]:
                    return None if (item[3].get("closed") and not closed_too) else dict(item[3])
            return None

    def reopen_reading(self) -> None:
        """Round 38: the last reading is open again (an explicit go-on); its line says so no longer."""
        with self._lock:
            for i in range(len(self._items) - 1, -1, -1):
                ts, u, a, info = self._items[i]
                if info:
                    if info.get("closed"):
                        self._items[i] = (ts, u, "[I was reading %s aloud.]" % (_short_title(info.get("title"))
                                                                                or "something"),
                                          {k: v for k, v in info.items() if k != "closed"})
                    return

    def has_reading(self, now: float | None = None) -> bool:
        """Does her history hold a reading, open or closed?"""
        now = time.time() if now is None else now
        with self._lock:
            self._expire(now)
            return any(item[3] for item in self._items)

    def close_reading(self) -> None:
        """Round 16: every reading in her history becomes one closed line - what it was, and that it is closed - so
        the model has no text of it to carry on from."""
        with self._lock:
            for i, (ts, u, a, info) in enumerate(self._items):
                if info and not info.get("closed"):
                    info = dict(info, closed=True)
                    self._items[i] = (ts, u, "[I was reading %s aloud; that reading is closed.]"
                                      % (_short_title(info.get("title")) or "something"), info)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

_CONVO = ConvoMemory()


def history_for(level: str, gw: dict, brief_reading: bool = False) -> list[dict]:
    """The owner's recent exchanges for this turn: only when the endpoint keeps none itself (no session header), only
    on an owner's turn, and never for a guest. brief_reading: a continue turn's view (CONTINUE_POSITION)."""
    if not HISTORY or level not in OWNER_LEVELS or str((gw or {}).get("session_header") or "").strip():
        return []
    return _CONVO.messages(brief_reading=brief_reading) if brief_reading else _CONVO.messages()


def dispatch(user: str, meta: dict) -> None:
    global _dispatch_busy, _pending
    t_turn = time.time()
    note_turn_answered()                          # round 26: her question had an answer (NO REPLY)
    id_gate.turn_text(user)                       # round 20: the ear's label of THIS turn (id_gate.turn_label)
    thinking.start()  # K: his turn is sent - the wall's Crissa dot pulses from here
    own_tools.turn_start()                        # cut 15: what her tools do this turn goes in her history
    try:
        presence = _get(PRESENCE, timeout=1.5)
    except Exception:
        presence = {"headcount": None, "people": [], "error": "presence_down"}
    if globals().get("_session_looking") and isinstance(presence, dict):
        presence = dict(presence)
        presence["pending_look"] = True
    pid = meta.get("person_id")
    stamp = meta.get("stamp") if isinstance(meta.get("stamp"), dict) else {}
    # round 17 (asr_barge): she goes on by herself after a cut that gave her no turn - the "go on" of the turn she was
    # answering, at that turn's level, with no tools and no voice published (nobody spoke this turn)
    resume = bool(meta.get("barge_resume")) and asr_barge.on()
    level = (str(meta.get("resume_level") or "") if resume else "") or authorize(
        {"person_id": pid, "stamp": stamp}, presence
    )
    ev = evaluate_turn(
        presence,
        stamp,
        person_id=pid,
        look_pending=bool(globals().get("_session_looking")),
    )
    if resume:
        ev = dict(ev, user_voice=False)
    else:
        asr_barge.STATE.note_turn(meta, level)
    gw, addressed = _target_from_meta(meta)
    _log(
        {
            "event": "dispatch",
            "user": user[:200],
            "level": level,
            "pid": pid,
            "hc": presence.get("headcount"),
            "user_auth": ev,
            "mode": load_house_mode().get("mode"),
            "profile": gw["profile"],
            "route": meta.get("route"),
            "addressed": addressed,
            **({"barge_resume": True} if resume else {}),
        }
    )
    # Publish turn voice into Oracle so panel user_auth stays current (round 17: never on a resume - no voice was heard)
    try:
        if not id_gate.gating():
            raise LookupError("round 17: ID-only / Off publish no turn voice; the turn does not wait on presence")
        if not resume:
            _post_json(
                PRESENCE.replace("/v1/presence", "/v1/instruct"),
                {
                    "op": "user.voice",
                    "person_id": pid,
                    "voice_emb_sim": stamp.get("voice_emb_sim") or stamp.get("self_sim"),
                    "match": bool(ev.get("user_voice")),
                    "source": "ear_bridge",
                },
                timeout=1.5,
            )
    except Exception:
        pass
    # Silent: record only — no Crissa, no TTS (ear already logged the turn)
    if level == "silent":
        _log(
            {
                "event": "dispatch_silent",
                "reason": ev.get("auth_reason"),
                "user_present": ev.get("user_present"),
                "user_voice": ev.get("user_voice"),
            }
        )
        _finish_dispatch()
        return

    if not resume and view_for_request(user):
        view_to_multitask("request")              # round 20: she has started on his request (THE CLOSE VIEW ...)
    if not resume:                                # a resume answers the same ask: its request stays the cancel record's
        _memory_append("freeair_user", user, pid=pid or "", level=level)
        cancels.note_request(user)                # round 15: what she is about to answer, for a cancel record
    token = _cancel_token
    spoken_user = user
    co_turn = callouts.Turn()  # L: what she has called out this turn
    first = {"clause": None, "spoken": False}
    first_done = threading.Event()
    # A LONG ANSWER IS READ AS IT ARRIVES (round 14): see StreamReader. Round 15 (.101 13:09: her clause was held for
    # his follow-up, the turn was asked again, and the one reader, closed after the first reply, never read the second):
    # a fresh reader for every reply of the turn.
    owner = level in OWNER_LEVELS
    read_turn, read_note = reading_turn(spoken_user, _CONVO if (owner and HISTORY) else None)
    cont_opening = ((reading_record(_CONVO) or {}).get("title") or "") if read_note else ""
    # round 16: the reading this turn finds open (a new subject answered closes it), and the no-resume line
    open_reading = reading_record(_CONVO) if (owner and HISTORY and READING_CLOSE and not read_note) else None
    turn_note = read_note or (NO_RESUME_NOTE if (owner and HISTORY and READING_CLOSE
                                                 and (open_reading or _CONVO.has_reading())) else "")
    ask_note = bool(read_turn and not read_note and READ_ASK_NOTE_ON and read_aloud_ask(spoken_user))
    if ask_note and ASK_WINS:
        # round 38 (.101 round 37: three plain asks to read on were refused, "I understood, but no, I won't continue
        # reading it aloud", with NO_RESUME_NOTE and READ_ASK_NOTE both in the turn): an ask to read is his word, so it
        # is the only note; the no-resume line is for turns that do not ask. Kill EAR_BRIDGE_ASK_WINS=0 (both, round 37).
        turn_note = READ_ASK_NOTE
    elif ask_note:
        turn_note += READ_ASK_NOTE              # round 20: a first ask reads on (READ_ASK_NOTE)
    if resume:
        turn_note += asr_barge.RESUME_NOTE
    if not resume:
        _asked = bool(_GUESS.get("place")) or bool(WEATHER_ASK.search(spoken_user))   # round 26: weather was the ask
        _town = spoken_town(spoken_user)
        if _town:
            set_weather_town(_town)
            _GUESS.update(place="", at=0.0)
        else:
            _town = confirm_guess(spoken_user)
        _had = False
        if _town:
            _tn, _had = town_note(_town, _asked)   # round 26: its weather now, not a second "want the weather?"
            turn_note += _tn
        if not _had:
            turn_note += weather_note(spoken_user)  # 2026-10-16: the glance, or "no location", never a made-up sky
    rmeta: dict = {}

    def _new_reader() -> StreamReader:
        r = StreamReader(
            lambda piece: speak_blocking(piece, voice=gw["voice"], chalk_title=gw["profile"], skip_quiet=True),
            lambda: _cancel_token == token,
        )
        if not read_note:
            r.title_cb = lambda buf: _name_reading(buf)
        return r

    def _name_reading(buf: str) -> None:
        """The cancel record's title (cancels.note_title): what she is reading, from what the reply says first."""
        cancels.note_title(_opening(re.sub(r"<think>[\s\S]*?</think>", " ", buf or "", flags=re.I), 110))

    if read_note and cont_opening:
        cancels.note_title((_bare_title(cont_opening)[:100] + " (continued)").strip())
    rbox = {"r": _new_reader()}
    # HIS CONTINUATION ENDS THE FIRST STREAM AT ONCE (round 15, .101 13:09: her clause was held for "just keep reading
    # until I say stop", and the turn waited for the WHOLE first reply before asking again): the first reply's stream is
    # abandoned the moment her clause is held for his words, and the turn is asked again with them. Kill
    # EAR_BRIDGE_ABANDON_HELD=0.
    held = {"v": False, "rev": 0}
    if read_turn:
        _log({"event": "reading_turn", "continue": bool(read_note), "max_tokens": reply_budget(reading=True),
              "ask_note": ask_note})

    def _kick_clause(clause: str) -> None:
        if first["clause"] or _cancel_token != token:
            return
        first["clause"] = clause
        co_turn.clause = clause
        rd = rbox["r"]                    # this reply's reader (round 15)

        def _run() -> None:
            try:
                # Predict is already streaming. Hold Cosy for SPEAK_QUIET_SEC
                # so a pause is not a non-sequitur. If he continued during
                # the wait, skip this clause — remainder/restart edits.
                if not _wait_speak_quiet(token):
                    _log(
                        {
                            "event": "ttfs_held",
                            "why": "no_quiet",
                            "clause": clause[:80],
                        }
                    )
                    return
                extra = _pending_text()
                if extra and not wake_match(extra).get("hit"):
                    _log(
                        {
                            "event": "ttfs_held",
                            "why": "continuation",
                            "extra": extra[:80],
                            "clause": clause[:80],
                        }
                    )
                    if held["rev"] == 0 and ABANDON_HELD:
                        held["v"] = True
                    return
                speak_blocking(
                    clause,
                    voice=gw["voice"],
                    chalk_title=gw["profile"],
                    skip_quiet=True,
                )
                if _cancel_token == token:
                    first["spoken"] = True
                    rd.clause_said(clause)
                    if not first.get("reply_done"):
                        thinking.start()  # K: the clause is said, the answer is still coming
            finally:
                first_done.set()

        threading.Thread(target=_run, name="ttfs-first", daemon=True).start()

    def _on_tool_info(d: dict) -> None:
        """L: a tool call in the stream - say what she is doing, once, now."""
        if not callouts.armed() or not isinstance(d, dict):
            return
        phrase = co_turn.take(str(d.get("tool") or ""), str(d.get("label") or ""), str(d.get("status") or ""))
        if phrase:
            _log({"event": "callout", "tool": d.get("tool"), "phrase": phrase, "label": str(d.get("label") or "")[:80]})
            threading.Thread(target=_speak_callout, args=(phrase, token, gw, co_turn), name="callout",
                             daemon=True).start()

    def _one_reply(text: str) -> str:
        first["clause"] = None
        first["spoken"] = False
        first["reply_done"] = False
        first_done.clear()
        rbox["r"] = _new_reader()
        rmeta.clear()
        held["v"] = False
        her_words.begin()                         # round 18: her reply's row on the log (a revise takes the same row)
        _rd = rbox["r"]

        def _more(buf: str, tool_seen: bool = False) -> None:
            her_words.grow(buf)                   # round 18: her words as they stream, before her voice says them
            _rd.feed(buf, tool_seen)
        try:
            return crissa_reply(
            text,
            person_id=None if level == "guest" else pid,
            presence=presence,
            level=level,
            session_id=meta.get("session_id"),
            # THE TOOL TURN IS tools_ok, NOT THE RAW VOICE FLAG (the User 2026-10-26, Security On, Secure held: "I already
            # am in user mode ... I thought we fixed that"): evaluate_turn's tools_ok carries this-turn voice, the
            # Oracle's live face and Secure with his auth held (user_mode._secure_held); the hard lock below and the
            # prompt read this flag, so a Secure turn gets her tools too. Kill CRISSA_SECURE_IS_USER=0 (tools_ok falls
            # back to voice or the Oracle).
            user_voice=bool(ev.get("user_voice") or ev.get("tools_ok")),
            force_reprove=bool(ev.get("force_reprove")),
            http_profile=gw["profile"],
            addressed=addressed,
            on_first_clause=_kick_clause,
            on_tool_info=_on_tool_info if callouts.armed() else None,
            on_more=_more,
            max_tokens=reply_budget(reading=read_turn),
            system_note=turn_note,
            reply_meta=rmeta,
            brief_reading=bool(read_note),       # round 17: a continue sends where she is, never the text
            # held for his words, or stopped ("Hey Crissa, stop"): the rest of this stream is not needed (round 15)
            stop=lambda: held["v"] or (ABANDON_HELD and _cancel_token != token),
        )
        finally:
            first["reply_done"] = True

    reply = ""
    tool_kept = False
    reading = False
    for rev in range(2):
        held["rev"] = rev
        reply = _one_reply(spoken_user)
        if (first["clause"] and not first_done.is_set() and not getattr(reply, "after_tool", False)
                and not _pending_text() and _cancel_token == token):
            prespeak_rest(rest_after_clause(reply, first["clause"]), gw["voice"], token)   # round 26: no gap after it
        if first["clause"]:
            first_done.wait(timeout=120)
        reading = rbox["r"].close()
        if reading:
            break          # being read already: no revise turn; his words wait like any line of hers
        if _cancel_token != token:
            break
        extra = _pending_text()
        if extra and wake_match(extra).get("hit"):
            break
        abandoned = bool(rmeta.get("abandoned"))
        if abandoned:
            _log({"event": "stream_abandoned", "why": "continuation", "chars": len(reply or ""), "extra": extra[:80]})
        if extra and not first["spoken"] and rev == 0:
            if KEEP_TOOL_ANSWER and getattr(reply, "after_tool", False) and (reply or "").strip() and not abandoned:
                reply = AfterTool(held_line(reply))
                _log({"event": "tool_answer_kept", "extra": extra[:80], "reply": str(reply)[:140]})
                tool_kept = True          # his words stay pending: _finish_dispatch makes them the next turn
                break
            if KEEP_ANSWER and not abandoned and (reply or "").strip() and followup_restates(extra):
                nxt = _take_pending()
                more = (nxt[0] if nxt else extra).strip()
                _log({"event": "followup_kept_answer", "extra": more[:80],
                      "reply": (reply or "")[:80]})
                if more:
                    _memory_append("freeair_user", more, pid=pid or "", level=level)
                break
            nxt = _take_pending()
            more = (nxt[0] if nxt else extra).strip()
            if more:
                spoken_user = f"{spoken_user} {more}".strip()
                read_turn = read_turn or reading_turn(spoken_user)[0]
                _log(
                    {
                        "event": "predict_revise",
                        "n": rev + 1,
                        "user": spoken_user[:200],
                    }
                )
                _memory_append(
                    "freeair_user", more, pid=pid or "", level=level
                )
                continue
        break
    budget_cut = rmeta.get("finish_reason") == "length"
    if reading:
        reader = rbox["r"]
        reader.finish(cut_tail=budget_cut)
        reader.join()
        said = " ".join(x for x in (first["clause"] or "", reader.said()) if x).strip()
        stopped = bool(reader.stopped or _cancel_token != token)
        _log({"event": "reading", "pieces": len(reader.pieces), "chars": len(said), "reply_chars": len(reply or ""),
              "capped": reader.capped, "stopped": stopped, "budget_cut": budget_cut,
              "first_piece_s": (round(reader.first_piece_at - t_turn, 2) if reader.first_piece_at else None),
              "profile": gw["profile"]})
        if budget_cut and not stopped and GO_ON_ASK:
            speak_blocking(GO_ON_ASK, voice=gw["voice"], chalk_title=gw["profile"], skip_quiet=True)
        _memory_append("freeair_reply", reply or said, pid=pid or "")
        if owner and said:
            # its title from what the reply says first (even when the stop came before the reading was heard)
            _CONVO.add(spoken_user, said, reading=True, stopped=stopped,
                       opening=cont_opening or _opening(str(reply or said)))
            if READING_CLOSE and float(_READING_STATE.get("closed_at") or 0) >= t_turn:
                _CONVO.close_reading()            # round 16: he ended it during this very reading ("for good")
        globals()["_session_extend_until"] = time.time() + after_reply_sec(said or reply,
                                                                            bool(globals().get("_comms_hold")))
        her_words.done(reply, said, stopped)      # round 18: what she said of her row
        _finish_dispatch(reply=reply or said)
        return
    if not (reply or "").strip():
        _finish_dispatch()
        return
    if _cancel_token != token:
        _log(
            {
                "event": "reply_cancelled",
                "reply": (reply or "")[:80],
                "profile": gw["profile"],
            }
        )
        _finish_dispatch()
        return
    _log(
        {
            "event": "reply",
            "reply": reply[:240],
            "level": level,
            "profile": gw["profile"],
            "addressed": addressed,
            "ttfs": bool(first["spoken"]),
            "predicted": bool(first["clause"]),
        }
    )
    _memory_append("freeair_reply", reply, pid=pid or "")

    # A LONG ANSWER IS NEVER REVISED (round 15, .101: a 24 s revise pass over 18,123 characters before a word of the
    # reading): it is said as written; his words wait and become the next turn, as behind any line of hers. One that
    # stopped on its token budget ends mid-sentence: that unfinished sentence is not said.
    long_reply = READ_CHUNK > 0 and len(str(reply)) > READ_CHUNK
    if budget_cut and not getattr(reply, "after_tool", False):
        reply = _drop_partial_tail(str(reply))
    extra = ""
    peek = _pending_text()
    # THE ANSWER AFTER A TOOL IS SAID, EVEN WHEN SHE HAD ALREADY SAID "ON IT" (the User 2026-09-30 10:16,
    # "i do not hear her at all"). The loop above keeps a finished after-tool answer only when her first
    # clause had NOT been spoken. With the clause spoken ("Sure, pulling the board up now.") and him talking
    # during the 130 s the tool took, the finished answer went to a REMAINDER REVISE instead - a second model
    # turn, which called the same tool again (another 65 s) and was cut off by his "Hey Zoe": she said one
    # sentence and then nothing. The thing the tool did is done; say what happened, in one short line, once
    # he is quiet. His words stay pending and become the next turn.
    if (peek and not wake_match(peek).get("hit") and not tool_kept and first["spoken"]
            and KEEP_TOOL_ANSWER and getattr(reply, "after_tool", False) and (reply or "").strip()):
        reply = AfterTool(held_line(reply))
        _log({"event": "tool_answer_kept", "after_clause": True, "extra": peek[:80], "reply": str(reply)[:140]})
        tool_kept = True
    if peek and not tool_kept and long_reply and not wake_match(peek).get("hit"):
        _log({"event": "revise_skipped", "why": "long_reply", "chars": len(str(reply)), "extra": peek[:80]})
        peek = ""                                  # round 15: a long answer is never revised (see long_reply)
    if peek and not wake_match(peek).get("hit") and not tool_kept:
        nxt = _take_pending()
        extra = (nxt[0] if nxt else "").strip()

    if read_turn and not read_note and long_reply:
        _name_reading(str(reply))                 # a reading said after its stream ended: the cancel record's title
    said, said_rest = "", ""
    if first["spoken"]:
        # An after-tool answer is not a continuation of the clause she said before the tool:
        # matching it as one fails, and a failed match is silence (the 07:58 answer).
        rest = (str(reply) if getattr(reply, "after_tool", False)
                else rest_after_clause(reply, first["clause"]))
        # NEVER THE WHOLE REPLY AGAIN (the User 2026-09-28, "Crissa speaks full replies
        # twice"): the match is punctuation-blind now (ttfs.rest_after_clause); when it
        # still fails she has said the clause and says nothing more.
        if first["clause"] and not rest and reply and rest_after_clause(reply, None) != "":
            _log({"event": "rest_empty", "clause": (first["clause"] or "")[:80],
                  "reply": reply[:120]})
        if extra:
            edited = crissa_revise_remainder(
                spoken=first["clause"] or "",
                extra=extra,
                draft_rest=rest or "",
                http_profile=gw["profile"],
                session_id=meta.get("session_id"),
            )
            _log(
                {
                    "event": "remainder_revise",
                    "extra": extra[:80],
                    "chars": len(edited),
                }
            )
            if edited:
                said_rest = speak_long(
                    edited,
                    voice=gw["voice"],
                    chalk_title=gw["profile"],
                )
        elif rest and _cancel_token == token:
            said_rest = speak_long(
                rest,
                voice=gw["voice"],
                chalk_title=gw["profile"],
                # a kept tool answer waits until he stops talking; a plain remainder follows her clause at once
                skip_quiet=not tool_kept,
            )
        said = " ".join(x for x in ((first["clause"] or "") if not getattr(reply, "after_tool", False) else "",
                                    said_rest) if x).strip()
    elif _cancel_token == token:
        said = speak_long(
            reply,
            voice=gw["voice"],
            chalk_title=gw["profile"],
        )
    stopped = bool(getattr(said_rest, "stopped", False) or getattr(said, "stopped", False) or _cancel_token != token)
    if budget_cut and read_turn and not stopped and GO_ON_ASK and said:
        speak_blocking(GO_ON_ASK, voice=gw["voice"], chalk_title=gw["profile"], skip_quiet=True)
    if open_reading and said and not about_reading(spoken_user, open_reading):
        close_reading("new_subject")              # round 16: he moved on and was answered; the reading is closed
    # Round 15 (.101: a reading stopped by "Hey Crissa, stop" was never remembered, so "continue where you were" had
    # nothing): what she said is kept even when it was stopped - only the part that was said.
    # Round 17: a line about her chat service (a refusal, a reach or key error) is not her answer and is not kept: on a
    # refused "keep reading" it would have become the reading's place. Kill: EAR_BRIDGE_REFUSAL_SAY=0.
    if owner and said and not (refusal_on() and (str(reply or "").strip() in SERVICE_LINES
                                                 or getattr(reply, "declined", False))):
        _CONVO.add(spoken_user, said, reading=read_turn or len(said) > READ_CHUNK > 0, stopped=stopped,
                   opening=cont_opening or _opening(str(reply or said)))
        if READING_CLOSE and float(_READING_STATE.get("closed_at") or 0) >= t_turn:
            _CONVO.close_reading()                # round 16: he ended it during this turn ("for good")
    # 15s idle gap after HER reply as well as his (the User 2026-08-30).
    globals()["_session_extend_until"] = time.time() + after_reply_sec(said or reply,
                                                                        bool(globals().get("_comms_hold")))
    her_words.done(reply, said, stopped)          # round 18: what she said of her row
    _finish_dispatch(reply=reply)


def _drop_pending(why: str) -> None:
    """Room speech that landed while she was busy does not become a reply."""
    global _pending
    with _dispatch_lock:
        nxt = _pending
        _pending = None
    if nxt:
        _log(
            {
                "event": "pending_drop",
                "why": why,
                "user": str(nxt[0])[:80],
            }
        )


def _finish_dispatch(*, reply: str = "") -> None:
    """Clear busy. Only a vocative may ride behind her mouth."""
    global _dispatch_busy, _pending
    thinking.stop("done")  # K: the turn is over (the wall stops the pulse at her line, or now if none came)
    speculate.finished()   # round 17: her turn in flight is over (a cancelled one's refusals end with it)
    her_words.close()      # round 18: a row still open (cancelled before her first word) is marked not said
    prespeak.PRE.drop()    # round 26: a rest started for this turn and never said is not said later
    with _dispatch_lock:
        _dispatch_busy = False
        nxt = _pending
        _pending = None
    asr_barge.AIR.answer_end()          # round 18: her answer is over; the session's rules apply again (side talk)
    if not nxt:
        return
    u2, m2 = nxt
    if reply and is_echo(u2, reply):
        return
    if not wake_match(u2).get("hit") and not hear_session_open():
        _log({"event": "drain_drop", "why": "not_vocative", "user": u2[:100]})
        return
    _log({"event": "drain_pending", "user": u2[:100]})
    schedule_dispatch(u2, m2)


def listening_off(why: str) -> None:
    """(e) The session ended: a line on the glass's log, so words said after it are not lost silently (round 15; the
    User keeps the timeout)."""
    try:
        try:
            from . import crissa_home as _H          # the pack's vendored copy (home.py reads it the same way)
        except ImportError:
            import crissa_home as _H
        name = _H.her_name()
    except Exception:  # noqa: BLE001
        name = "Crissa"
    heard_roll.note("crissa", "Listening off. Say %s to talk again." % name, why, kind="status")
    if why == "timeout":
        said = say_no_reply(name)                  # round 26: her question went unanswered
        if said:
            _log({"event": "no_reply", "line": said})


def _over_line_owner(presence: dict, stamp: dict, meta: dict) -> bool:
    """Is this the person she answers to, or anyone when no voice print exists yet (round 15 (b'))? The person she
    answers to is the house's authed owner this turn (user_mode.authed_owner, round 15 auth): his voice alone with no
    face of his is a guest's turn there, and so it is here - his words wait behind her line, as before."""
    try:
        uid = user_id_from_mode()
        if uid and user_voice_prints(uid) == 0:
            return True
        ev = evaluate_turn(presence, stamp, person_id=meta.get("person_id"),
                           look_pending=bool(globals().get("_session_looking")))
        return authed_owner(ev)
    except Exception:  # noqa: BLE001 - unknown is the old queue
        return False


def _over_line_ask(msgs: list) -> str | None:
    """One short, quick ask of her chat model (over_line.py): the action word and at most one line."""
    gw = _gateway("crissa")
    payload = {"model": gw["model"], "messages": msgs, "max_tokens": 60, "temperature": 0.0}
    shape_payload(payload, gw["url"])
    try:
        return _stream_chat_text(gw["url"], payload, key=gw["key"], timeout=over_line.TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - over_line falls back on the words
        _log({"event": "over_line_error", "error": str(e)[:160],
              "body": logged_body(e) if _http_code(e) is not None else None})
        return None


# ONE DECISION FOR ONE LINE SAID OVER HERS (round 17, .101 2026-10-13 05:54: "Keep reading · said over her line:
# answered after it" was on the Wall's log 6 times, ~0.6 s apart, for one line the bridge heard once). CONTINUE handed
# his words to schedule_dispatch, and while she was still speaking with no turn in flight that sent them straight back
# to the over-line ask - one ask, one row, one more CONTINUE - until her line ended (each ask was a ~0.6 s 403 there).
# A line decided CONTINUE is marked and goes on to dispatch (or the queue) as it did before round 15.
# Kill: EAR_BRIDGE_OVER_LINE_ONCE=0.
OVER_LINE_ONCE = os.environ.get("EAR_BRIDGE_OVER_LINE_ONCE", "1").strip().lower() not in ("0", "false", "no", "off")


def _over_line_run(user: str, meta: dict) -> None:
    """Said over her line: her model decides PAUSE / STOP / CONTINUE (over_line.decide); CONTINUE queues his words
    behind her line, as before."""
    global _pending
    who = str(meta.get("person_id") or (meta.get("stamp") or {}).get("person_id") or "?")
    where = cancels.position(_SAY.rows())
    got = over_line.decide(user, where, _over_line_ask, request=str(cancels._REQ.get("text") or ""),
                           stop_only=stop_only)
    _log({"event": "over_line", "user": user[:100], "action": got.get("action"), "say": got.get("say"),
          "by": got.get("by"), "spoken_chars": where.get("spoken_chars")})
    if got.get("action") == "IGNORE":
        # .101 round 28: her model says it was not said to her - room talk, as side talk was (the log's, never hers)
        heard_roll.note(who, user, "side talk", lane="room")
        return
    if got.get("action") in ("PAUSE", "STOP"):
        if _speaking or _speak_proc is not None:
            abort_mouth("over_line_" + str(got["action"]).lower())
        if got.get("action") == "STOP":
            note_stop_words(user)                    # round 16: "stop for good" closes the reading
        heard_roll.note(who, user, "said over her line: she %s" % ("paused" if got["action"] == "PAUSE" else "stopped"))
        if got.get("say"):
            speak_blocking(got["say"], skip_quiet=True)
        if got.get("action") == "STOP" and not stop_only(user):
            # .101 round 28: a request that replaces her line ("put that on the screen") is done once she has stopped,
            # never dropped with the rest of her line
            schedule_dispatch(user, dict(meta, over_line_done=True))
        return
    heard_roll.note(who, user, "said over her line: answered after it")
    if OVER_LINE_ONCE:
        meta = dict(meta, over_line_done=True)     # round 17: decided; it never comes back here (see OVER_LINE_ONCE)
    with _dispatch_lock:
        if _dispatch_busy:
            joined, m = coalesce_followup(_pending, user, meta, vocative=False)
            _pending = (joined, m)
            _log({"event": "over_line_queued", "user": joined[:100]})
            return
    schedule_dispatch(user, meta)


def _spec_tool_restart(why: str, user: str = "") -> None:
    """Round 17 (speculate.tool_guard): her speculative turn reached for a tool before his turn was final. The read has
    stopped (the gateway interrupts its agent); the turn is cancelled silently, as a restart is, and its same words are
    queued to be asked once final, with tools ("cancelled": his next words already queued the whole utterance). A
    vocative already waiting is a new call and stays."""
    global _cancel_token, _pending
    got = speculate.tool_restart(_cancel_token) if why == "not_final" else None
    with _dispatch_lock:
        if got is not None:
            _cancel_token += 1
            if _pending is None:
                _pending = got
    _log({"event": "spec_tool", "why": why, "user": (user or "")[:80], "ask_at_final": got is not None})


def schedule_dispatch(user: str, meta: dict) -> None:
    global _dispatch_busy, _pending, _cancel_token
    id_gate.turn_text(user)                       # round 20: a house command's voice fallback reads this turn's label
    # Hard-rail house commands (hybrid/voice/system) — before LLM
    try:
        presence = _get(PRESENCE, timeout=1.5)
    except Exception:
        presence = {}
    if globals().get("_session_looking") and isinstance(presence, dict):
        presence = dict(presence)
        presence["pending_look"] = True
    stamp = meta.get("stamp") if isinstance(meta.get("stamp"), dict) else {}
    # SAID OVER HER LINE (round 15 (b'), over_line.py): not queued behind her line, and not a house command while she
    # speaks; her model decides at once. Her name + a stop word never gets here (the name-interrupt stopped her).
    if ((_speaking or _speak_proc is not None) and over_line.mode() != "off"
            and not (OVER_LINE_ONCE and meta.get("over_line_done"))
            and not wake_match(user).get("hit") and _over_line_owner(presence, stamp, meta)):
        threading.Thread(target=_over_line_run, args=(user, meta), name="ear-bridge-over-line", daemon=True).start()
        return
    # BAU only: face-authed User + User voice (user_auth_only) or legacy allow
    if not id_gate.gating():
        # round 17, Security ID-only / Off (id_gate.house_command): a house command needs his face at the camera
        # (ID-only) or nothing (Off); never a voice, never the desk hunt
        if id_gate.house_command(user, meta, presence, speak=lambda t: speak_blocking(t, post_cooldown=1.2),
                                 log=_log, fetch=lambda: _get(PRESENCE, timeout=1.0)):
            return
    elif is_user_auth_only():
        ev = evaluate_turn(
            presence,
            stamp,
            person_id=meta.get("person_id"),
            look_pending=bool(globals().get("_session_looking")),
        )
        # the User 2026-08-11 command Auth:
        #   voice=the User + face sticky → AUTH execute
        #   else → ensure_user_auth (facefind every patrol stop until the User)
        #   else → noAUTH, no execution
        cmd_m = None
        try:
            from .house_commands import match_control

            cmd_m = match_control(user)
        except Exception:
            cmd_m = None
        tier2_cmd = bool(cmd_m and str(cmd_m.get("cmd", "")).startswith("system"))

        def _ensure_auth_for_command() -> dict:
            return request_ensure_user_auth(ev)

        if cmd_m:
            # Commands always require Auth via voice or door-local face chase.
            # Never execute on session_held alone (non-User inject path).
            voice_ok = bool(ev.get("user_voice"))
            face_ok = bool(ev.get("user_present") or ev.get("host_unlocked"))
            tools_ok = bool(ev.get("tools_ok"))
            need_hunt = not (tools_ok and face_ok)
            # Contested / wrong_voice / not_user_voice: never session_held alone
            if (
                ev.get("force_reprove")
                or ev.get("auth_reason") == "wrong_voice"
                or str(ev.get("auth_reason") or "").startswith("not_user_voice")
            ):
                need_hunt = True
            # No this-turn User voice → hunt (tier1 age does not free-air inject)
            if not voice_ok:
                need_hunt = True
            # system-* always demand this-turn voice path
            if tier2_cmd and not voice_ok:
                need_hunt = True
            if ev.get("auth_reason") == "host_locked":
                need_hunt = False
            if need_hunt:
                _log(
                    {
                        "event": "house_cmd_ensure_auth",
                        "cmd": cmd_m.get("cmd"),
                        "voice_ok": voice_ok,
                        "face_ok": face_ok,
                        "tier2": tier2_cmd,
                    }
                )
                speak_blocking(
                    "One moment — confirming it is you.",
                    post_cooldown=0.4,
                )
                ens = _ensure_auth_for_command()
                _log({"event": "house_cmd_ensure_auth_result", **{
                    k: ens.get(k)
                    for k in ("auth", "reason", "via", "wall_sec", "user_auth")
                }})
                if not ens.get("auth"):
                    speak_blocking(
                        "I could not confirm you. No house command without Auth.",
                        post_cooldown=1.2,
                    )
                    return
                # refresh presence after hunt
                try:
                    presence = _get(PRESENCE, timeout=1.5)
                except Exception:
                    pass
                ev = evaluate_turn(
                    presence,
                    stamp,
                    person_id=meta.get("person_id"),
                    look_pending=bool(globals().get("_session_looking")),
                )
            # HARD: face chase proving User is present must NOT execute a
            # non-User stamp as control inject (tools_ok / this-turn voice).
            from .house_commands import command_may_execute

            if not command_may_execute(ev):
                _log(
                    {
                        "event": "house_cmd_denied_not_user_voice",
                        "cmd": cmd_m.get("cmd"),
                        "reason": ev.get("auth_reason"),
                        "tools_ok": ev.get("tools_ok"),
                        "user_voice": ev.get("user_voice"),
                        "force_reprove": ev.get("force_reprove"),
                        "pid": meta.get("person_id"),
                    }
                )
                speak_blocking(
                    "Only your voice can run house commands.",
                    post_cooldown=1.2,
                )
                return
            if try_handle_utterance(
                user,
                meta=meta,
                presence=presence,
                speak=lambda t: speak_blocking(t, post_cooldown=1.2),
            ):
                _log({"event": "house_cmd_handled", "user": user[:120]})
                return
            return
        from .house_commands import command_may_execute

        if not command_may_execute(ev):
            # Skip house commands; fall through to guest/silent dispatch
            _log(
                {
                    "event": "house_cmd_skipped_not_bau",
                    "reason": ev.get("auth_reason"),
                    "user": user[:80],
                    "tools_ok": ev.get("tools_ok"),
                }
            )
        else:
            if try_handle_utterance(
                user,
                meta=meta,
                presence=presence,
                speak=lambda t: speak_blocking(t, post_cooldown=1.2),
            ):
                _log({"event": "house_cmd_handled", "user": user[:120]})
                return
    else:
        if try_handle_utterance(
            user,
            meta=meta,
            presence=presence,
            speak=lambda t: speak_blocking(t, post_cooldown=1.2),
        ):
            _log({"event": "house_cmd_handled", "user": user[:120]})
            return
    with _dispatch_lock:
        if _dispatch_busy:
            # Append continuations so a mid-thought fragment is not replaced
            # by the next breath. A vocative replaces (new call).
            vocative = bool(wake_match(user).get("hit"))
            # round 17 (speculate.py): her turn began on the first part of his sentence and has not opened her mouth:
            # cancelled silently (nothing was played) and asked again with the whole utterance when it ends
            _sp = speculate.restart(user, meta, vocative=vocative, token=_cancel_token)
            if _sp is not None:
                _cancel_token += 1
                _pending = _sp
                _log({"event": "spec_restart", "user": _sp[0][:100], "n": _sp[1].get("spec_restart")})
                return
            joined, m = coalesce_followup(
                _pending, user, meta, vocative=vocative
            )
            _pending = (joined, m)
            _log({"event": "coalesce", "user": joined[:100],
                  "keep": "vocative" if vocative else "append"})
            heard_roll.note(str(meta.get("person_id") or "?"), user, "queued: she answers after this")
            return
        _dispatch_busy = True
        asr_barge.AIR.answer_begin()          # round 18: a turn is being answered (side talk starts at her first line)
        speculate.begin(user, meta, _cancel_token)     # round 17: speculative until her mouth's gate says go
    threading.Thread(
        target=dispatch, args=(user, meta), name="ear-bridge-dispatch", daemon=True
    ).start()


def turn_key(t: dict) -> str:
    return f"{t.get('ts')}|{t.get('text')}|{t.get('person_id')}|{t.get('via')}"


# THE TURNS ALREADY SEEN KEEP THE NEWEST (round 46, the local review of kit crissa-063a2813, 2026-10-23): the record was
# a set trimmed with `set(list(seen)[-300:])` - a set has no order, so the "last 300" were 300 at random, and a turn
# the ear still returned (a wake, a command) could fall out and be acted on again on the next poll. It is ordered by
# when each key was first seen now, and the trim keeps the newest. The sizes are the old ones, as dials: SEEN_TRIM_AT
# (500) is when to trim, SEEN_KEEP (300) how many stay; SEEN_KEEP never goes under twice the turns one poll returns
# (STT_TURNS' n=), so every turn the ear can still hand back is still known. Kill: EAR_BRIDGE_SEEN_ORDERED=0 (the set).
def _poll_n(url: str) -> int:
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return max(1, int((q.get("n") or ["50"])[0]))      # the ear's own default page is 50 (/v1/turns)
    except (ValueError, TypeError):
        return 50


SEEN_TRIM_AT = int(os.environ.get("EAR_BRIDGE_SEEN_TRIM_AT", "500") or 500)
SEEN_KEEP = max(int(os.environ.get("EAR_BRIDGE_SEEN_KEEP", "300") or 300), 2 * _poll_n(STT_TURNS))
SEEN_ORDERED = os.environ.get("EAR_BRIDGE_SEEN_ORDERED", "1").strip().lower() not in ("0", "false", "no", "off")


class SeenTurns:
    """Turn keys already taken, oldest first; trim() drops the oldest (a dict keeps insertion order)."""

    def __init__(self, keys=()) -> None:
        self._d: dict[str, None] = {}
        for k in keys:
            self.add(k)

    def add(self, key: str) -> None:
        self._d[key] = None

    def __contains__(self, key: object) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self):
        return iter(self._d)

    def trim(self, at: int | None = None, keep: int | None = None) -> None:
        at = SEEN_TRIM_AT if at is None else int(at)
        keep = SEEN_KEEP if keep is None else int(keep)
        if len(self._d) > at:
            for k in list(self._d)[: len(self._d) - keep]:
                del self._d[k]


def new_seen():
    """The bridge's record of turns already taken (SeenTurns; the old set under EAR_BRIDGE_SEEN_ORDERED=0)."""
    return SeenTurns() if SEEN_ORDERED else set()


def trim_seen(seen):
    """Trim the record when it is over SEEN_TRIM_AT: the newest SEEN_KEEP stay (the old random set trim when off)."""
    if isinstance(seen, SeenTurns):
        seen.trim()
        return seen
    if len(seen) > SEEN_TRIM_AT:
        return set(list(seen)[-SEEN_KEEP:])
    return seen


# ONLY TURNS NEWER THAN THIS BRIDGE (the User 2026-10-10, .101: on a restart the bridge started before the ear, gave up
# seeding after six seconds of errors, and then took the ear's whole day of turns as new - about ten old "Hey Crissa"
# wakes in one burst). A turn older than the bridge's own start (less TURN_START_SLACK_S) is never acted on, whatever
# the seed did; the seed waits quietly for the ear (SEED_WAIT_S), and the poll's errors are said once a minute.
# Kill EAR_BRIDGE_ONLY_NEW_TURNS=0.
STARTED_AT = time.time()
TURN_START_SLACK_S = 2.0
SEED_WAIT_S = float(os.environ.get("EAR_BRIDGE_SEED_WAIT_S", "180"))
ONLY_NEW_TURNS = os.environ.get("EAR_BRIDGE_ONLY_NEW_TURNS", "1").strip() != "0"
_QUIET: dict = {}


def turn_is_old(t: dict, started: float | None = None) -> bool:
    if not ONLY_NEW_TURNS:
        return False
    try:
        ts = float(t.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    return bool(ts) and ts < (STARTED_AT if started is None else started) - TURN_START_SLACK_S


def quiet_log(ev: dict, every_s: float = 60.0) -> None:
    """A repeating error (the ear not up yet) is logged once, then at most once every `every_s`."""
    k = str(ev.get("event"))
    now = time.time()
    if now - float(_QUIET.get(k) or 0) >= every_s:
        _QUIET[k] = now
        _log(ev)


# HER QUESTION OPENS THE EAR (round 17, the User 2026-10-13, .101: "maybe should start unpaused" - after Setup's ending
# "Hello! What can we do together?" his first words still needed "Hey Crissa"). Inside a conversation the ear already
# stays open after every reply of hers (idle_sec); a line of hers said by something else - Setup's opening line - left
# it closed. Now a line in her own voice that ends in a question mark, said while no conversation is open and not by
# this bridge, opens the ear when it ends: the same open as the LISTENING chip or a bare "Hey Crissa" (the lamp, the
# session's owner rules: the User or a voice the ear could not name, never another known person), but for
# QUESTION_OPEN_S only. A window, not "the next turn": the ear may write one answer as several turns, and the session's
# own rules already handle that; 20 s is time to think and answer an open question, and a room conversation a minute
# later still needs her name. His answer then extends it like any session turn, so the talk goes on as after a wake;
# with no answer it closes and the log says "Listening off". Never while Setup runs (its questions are answered to
# Setup), never on Lila's, the announcer's or a bench line, never on a line that was stopped.
# Kill: EAR_BRIDGE_QUESTION_OPENS=0.
QUESTION_OPENS = os.environ.get("EAR_BRIDGE_QUESTION_OPENS", "1").strip().lower() not in ("0", "false", "no", "off")
QUESTION_OPEN_S = float(os.environ.get("EAR_BRIDGE_QUESTION_OPEN_S", "20") or 20)
QUESTION_GRACE_S = 8.0                        # her line ended at most this long ago
_HER_GATE_WHO = ("crissa", "zoe", "crissa_her_tts")   # house-speak's speaker key for her voice (_AIR_WHO), whatever her name


def her_question_ended(now: float | None = None, paths=None) -> tuple[float, str] | None:
    """(when it ended, the words) of her newest line that ended in a question within QUESTION_GRACE_S, from the gate
    files that name her lines (house-speak's and this bridge's), or None."""
    now = time.time() if now is None else float(now)
    best = None
    for p in (own_voice_gate_paths() if paths is None else paths):
        try:
            g = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(g, dict) or g.get("speaking") or str(g.get("notice") or "").strip():
            continue
        if str(g.get("who") or "").strip().lower() not in _HER_GATE_WHO:
            continue
        text = str(g.get("last_reply") or "").strip()
        if not text.rstrip("\"'\u201d\u2019)] ").endswith("?"):
            continue
        try:
            end = float(g.get("air_until") or g.get("updated_ts") or 0.0)
        except (TypeError, ValueError):
            continue
        if end and 0.0 <= now - end <= QUESTION_GRACE_S and (best is None or end > best[0]):
            best = (end, text)
    return best


def setup_running() -> bool:
    """Is Setup walking now (<home>/init-run.json status running)? Its own questions are answered to Setup."""
    try:
        d = json.loads((HOME / "init-run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("status") == "running"


# SHE HOLDS HER ANSWERS WHILE A WALK IS OPEN (round 41, .101 round 40 F4, the User: "Crissa came on so she was listening and
# responding with Lila listen loop still"): Setup holds her by its own means (her microphone is off until Setup's end), but
# an Add/Edit IDs run walks with her microphone on, and the room's side talk reached her and she answered over Lila's page.
# While any walk runs (<home>/init-run.json running: Setup, an IDs run, a Voice ID again) the turns she hears are dropped
# here, like the tool hold: Lila leads, and the walk hears what it needs by its own route (never this bridge). They come
# back the moment the walk ends. Kill: CRISSA_WALK_HOLDS_ANSWERS=0 (round 40: she answered during an IDs run).
def walk_holds_answers() -> bool:
    if os.environ.get("CRISSA_WALK_HOLDS_ANSWERS", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return setup_running()


def _tool_hold_active() -> dict | None:
    """Soft pause while enroll/other exclusive tools own mouth+mic."""
    p = Path(os.environ.get("HOUSE_TOOL_HOLD", "/tmp/house-tool-hold.json"))
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
        until = float(d.get("until") or 0)
        if until and time.time() < until:
            return d if isinstance(d, dict) else {"held": True}
    except Exception:
        return None
    return None


def main() -> int:
    seen = new_seen()                         # round 46: ordered, the trim keeps the newest (SeenTurns)
    gather_until = 0.0
    buf: list[str] = []
    meta: dict = {}
    session_until = 0.0
    cmd_until = 0.0  # locked-voice window after any vocative for voice-on/off
    last_speech_in_gather = 0.0
    cut_mid_sentence = False
    free_session_id: str | None = None
    session_profile = "crissa"
    session_route = "direct"
    session_addressed: str | None = None
    session_owner: str | None = None          # the speaker who opened the session (2026-10-10)
    _hold_logged = False
    _walk_hold_logged = False                 # round 41 (F4): a walk's hold said once each way
    session_looking = False
    q_seen = 0.0                              # round 17: her last question already acted on (or heard in a session)

    def session_live(now: float) -> bool:
        """One expression of "the session is open".

        The hear gate, the follow-up branch and the expiry check must not
        disagree. MEASURED 2026-08-28 16:41:09: they did. The expiry refused
        to drop the session while she was thinking (gateway 14.6s) or speaking
        (18.0s), so no session_end was ever logged — but the gate read the bare
        `now <= session_until` and reported paused, and the User's follow-up was
        crissa_skip'd. He was deaf-by-mistake for 33s inside a live session.

        MEASURED 2026-08-30 14:20: wake 14:20:29, session_end 14:20:45
        (Yeah? + SESSION_SEC), follow-up ASR 14:20:50 `crissa_skip why:paused`.
        He started talking while the session was still open; the turn landed
        after idle because drop ignored in_utterance (keyboard-VAD fear).
        Hold while the ear is packing/decoding, capped so stuck VAD cannot
        keep LISTENING forever.
        """
        if not session_until:
            return False
        # Comms Hold is not read here: it only lengthened session_until.
        if now <= session_until:
            return True
        # Still thinking or still talking: the turn is not over.
        if _dispatch_busy or _speaking:
            return True
        if (globals().get("_session_extend_until") or 0.0) > now:
            return True
        if ear_still_hearing() and (now - session_until) < 20.0:
            return True
        return False

    def hold_session(now: float, pid: str | None = None, for_s: float | None = None) -> None:
        """Open or extend the spoken session. Camera look only on the open. for_s: a shorter first window (round 17,
        her question opens the ear); any turn in it extends by the usual idle window."""
        nonlocal session_until, session_looking, session_owner
        was = session_until
        until = now + (float(for_s) if for_s else idle_sec(bool(globals().get("_comms_hold"))))
        session_until = max(was, until) if was else until
        _set_hear_session(True)
        if was:
            return
        board_keep()                                # what she showed stays for the new session
        session_owner = pid or None                 # the speaker who opened it (session_follow_ok)
        session_looking = True
        globals()["_session_looking"] = True
        def _look_on() -> None:
            out = request_session_look(
                on=True, person_id=pid, reason="session_open"
            )
            _log(
                {
                    "event": "session_look",
                    "on": True,
                    "pid": pid,
                    "hour": (out or {}).get("hour"),
                    "stop": (out or {}).get("stop"),
                    "skipped": (out or {}).get("skipped")
                    or (out or {}).get("skipped_look"),
                    "error": (out or {}).get("error"),
                }
            )

        threading.Thread(
            target=_look_on,
            daemon=True,
            name="session-look-on",
        ).start()

    def drop_session(*, board: bool = True) -> None:
        """Close the spoken session and home the camera."""
        nonlocal session_until, session_looking, session_owner
        nonlocal free_session_id, session_profile, session_route, session_addressed
        session_until = 0.0
        session_owner = None
        globals()["_session_looking"] = False
        globals()["_comms_hold"] = False
        globals()["_session_hold_req"] = None
        _set_hear_session(False)
        free_session_id = None
        session_profile = "crissa"
        session_route = "direct"
        session_addressed = None
        if session_looking:
            session_looking = False
            threading.Thread(
                target=request_session_look,
                kwargs={"on": False, "reason": "session_end"},
                daemon=True,
                name="session-look-off",
            ).start()
        if board:
            clear_session_board_later()

    # Never inherit last week's mouth: truncate speech jsonl + TTS gate +
    # chalkboard. Stuck speaking=true used to deafen omni across a kill.
    try:
        _reset_speech_logs()
    except Exception:
        pass
    start_abort_listener()

    _log(
        {
            "event": "start",
            "poll": POLL,
            "bare_wake": "immediate_ack",
            "gather_sec": GATHER_SEC,
            "session_sec": SESSION_SEC,
            "speak_quiet_sec": speculate.gap(SPEAK_QUIET_SEC),
            "flush_fast_sec": FLUSH_FAST,
            "speculate": speculate.on(),
            "mouth_filter": "tts_echo_veto",
            "own_voice": own_voice.mode(),
            "own_voice_armed": sorted(own_voice.ARMED) if own_voice.mode() == "drop" else [],
            "wake": "vocative-crew+ada+argus",
        }
    )

    # Retry seed — rail-stt may still be binding after joint restart (seed miss
    # re-dispatches history as new wakes; the User 2026-08-09).
    seeded = False
    seed_end = time.time() + SEED_WAIT_S
    attempt = 0
    while time.time() < seed_end:                    # wait for the ear quietly (2026-10-10)
        attempt += 1
        try:
            data = _get(STT_TURNS, timeout=5.0)
            for t in data.get("turns") or []:
                seen.add(turn_key(t))
            seeded = True
            _log({"event": "seed_ok", "n": len(seen), "attempt": attempt})
            break
        except Exception as e:
            quiet_log({"event": "seed_wait", "error": str(e)[:120], "attempt": attempt,
                       "note": "waiting for the ear to answer"}, every_s=30.0)
            time.sleep(1.0)
    if not seeded:
        _log({"event": "seed_gave_up", "note": "turns older than this bridge's start are still skipped"})

    voice_edge_prev: bool | None = None

    while True:
        try:
            now = time.time()
            # A pause REQUEST (wall chip / CP / voice-pause) and the Voice
            # on/off EDGE both close the session now. the User 2026-08-28: a
            # toggle must land in PAUSED, and clicking LISTENING must not wait
            # out the idle window. Voice off used to drop the session only when
            # the NEXT utterance arrived, so a private conversation started
            # with her still nominally listening.
            if globals().get("_session_pause_req"):
                globals()["_session_pause_req"] = False
                globals()["_session_open_req"] = False
                globals()["_session_extend_until"] = 0.0
                if session_until:
                    _log({"event": "session_end", "why": "pause_request"})
                    listening_off("pause_request")
                    drop_session(board=True)
            if globals().get("_session_open_req"):
                globals()["_session_open_req"] = False
                if voice_on_cached():
                    hold_session(now)
                    _log({"event": "session_open", "why": "chip"})
            voice_now = voice_on_cached()
            if voice_edge_prev is not None and voice_now != voice_edge_prev:
                _log({"event": "voice_edge", "on": voice_now})
                globals()["_session_extend_until"] = 0.0
                if session_until:
                    _log({"event": "session_end", "why": "voice_edge"})
                    listening_off("voice_edge")
                    drop_session(board=True)
            voice_edge_prev = voice_now
            hold_req = globals().get("_session_hold_req")
            if hold_req is not None:
                # After pause/open/voice_edge so explicit pause wins.
                globals()["_session_hold_req"] = None
                want = bool(hold_req)
                if want:
                    if session_until:
                        globals()["_comms_hold"] = True
                        # the open session gets the long window from now
                        session_until = max(session_until, now + COMMS_HOLD_SEC)
                        _log({"event": "comms_hold", "on": True, "sec": COMMS_HOLD_SEC})
                    else:
                        globals()["_comms_hold"] = False
                        _log({"event": "comms_hold", "on": False, "why": "no_session"})
                else:
                    was = bool(globals().get("_comms_hold"))
                    globals()["_comms_hold"] = False
                    if was and session_until:
                        # Release is BAU again: a fresh SESSION_SEC from now.
                        session_until = now + SESSION_SEC
                    if was:
                        _log({"event": "comms_hold", "on": False})
            # Active/LISTENING + mouth open: cut PA now, do not wait for the
            # finished STT turn (that delay was the "took awhile" barge).
            if PA_PLAY.is_file() and session_live(now) and ear_still_hearing():
                pa_yield_for_hear({"user_ready": True}, session_open=True)
            # Enroll / tool hold: do not wake Crissa or speak over the mic
            th = _tool_hold_active()
            if th:
                if not _hold_logged:
                    _log(
                        {
                            "event": "tool_hold_pause",
                            "reason": th.get("reason"),
                            "owner": th.get("owner"),
                            "until_local": th.get("until_local"),
                        }
                    )
                    _hold_logged = True
                # clear in-flight gather so we don't fire after hold ends
                gather_until = 0.0
                buf = []
                meta = {}
                time.sleep(max(POLL, 0.5))
                continue
            if _hold_logged:
                _log({"event": "tool_hold_resume"})
                _hold_logged = False
            if walk_holds_answers():
                if not _walk_hold_logged:
                    _log({"event": "walk_hold", "on": True, "why": "a walk is open (Setup or Add/Edit IDs): Lila leads"})
                    _walk_hold_logged = True
                gather_until = 0.0
                buf = []
                meta = {}
                try:
                    for t in ((_get(STT_TURNS, timeout=4.0) or {}).get("turns") or []):   # heard meanwhile: never answered later
                        seen.add(turn_key(t))
                except (OSError, ValueError):
                    pass
                time.sleep(max(POLL, 0.5))
                continue
            if _walk_hold_logged:
                _log({"event": "walk_hold", "on": False})
                _walk_hold_logged = False
            # HER QUESTION OPENS THE EAR (round 17, QUESTION_OPENS): a question of hers that ended while no
            # conversation was open, said by something other than this bridge (Setup's opening line).
            if session_until or gather_until or _speaking or _speak_proc is not None or _dispatch_busy:
                q_seen = now                      # inside a conversation, or her own reply: nothing to open
            elif QUESTION_OPENS:
                q = her_question_ended(now)
                if q and q[0] > q_seen and voice_on_cached() and not setup_running():
                    q_seen = q[0]
                    hold_session(now, None, for_s=QUESTION_OPEN_S)
                    _log({"event": "session_open", "why": "her_question", "text": q[1][:80],
                          "for_s": QUESTION_OPEN_S})
            _spec_st = speculate.before_poll()      # round 17: the ear as of now, before this fetch (speculate.py)
            data = _get(STT_TURNS, timeout=4.0)
            new = []
            for t in data.get("turns") or []:
                k = turn_key(t)
                if k in seen:
                    continue
                seen.add(k)
                if turn_is_old(t):                     # the ear's backlog from before this bridge started
                    continue
                # Camera describe stays on omni. Do not journal it as wake.
                if is_oracle_feed(t) or not is_spoken_via(t):
                    continue
                new.append(t)
            new.reverse()

            for t in new:
                text = (t.get("text") or "").strip()
                if not text:
                    continue
                _bt = asr_barge.STATE.is_turn(t)       # round 17: the utterance that cut her line (asr_barge)
                # Measured mishearings (config/heard-corrections.json), before anything reads the turn.
                try:
                    from .heard_corrections import apply as _heard_fix
                    text = _heard_fix(text)
                except Exception:  # noqa: BLE001 - a broken table never drops a turn
                    pass

                # "Hey" + a near-name from the recognised User is her name (round 16 (d), wake.user_near_wake)
                _nw = user_near_wake(text, ask_ok=bool(_speaking or _speak_proc is not None
                                                        or resume_pending(time.time())))
                if _nw is not None:
                    _st_nw = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
                    _pid_nw = str(t.get("person_id") or _st_nw.get("person_id") or "").strip().lower()
                    if (_pid_nw and _pid_nw == user_id_from_mode() and not wake_media(_st_nw)) \
                            or id_gate.any_voice_wakes(_st_nw):           # round 17: any voice outside Security On
                        _log({"event": "near_name_wake", "heard": text[:80], "text": _nw[:80], "pid": _pid_nw})
                        text = _nw
                        t["text"] = text

                # Always mark seen. House mouth = embedding veto, not a mute clock.
                if is_mouth_echo(t):
                    wm_echo = wake_match(text)
                    # Vocative while her mouth is idle is him, not Cosy
                    # (measured 2026-09-09 06:24: "Hey Zoe, can you hear me?"
                    # tts_sim 0.51, speaking=false, never reached the gate).
                    if not (
                        _speaking or _speak_proc is not None or house_mouth_on_air()
                    ):
                        _log(
                            {
                                "event": "mouth_drop_pass",
                                "why": "idle_mouth",
                                "text": text[:80],
                                "tts_sim": ((t.get("stamp") or {}).get("tts_veto") or {}).get(
                                    "tts_fp_sim"
                                ),
                            }
                        )
                    else:
                        _log(
                            {
                                "event": "mouth_drop",
                                "text": text[:80],
                                "tts_sim": ((t.get("stamp") or {}).get("tts_veto") or {}).get(
                                    "tts_fp_sim"
                                ),
                            }
                        )
                        _barge_aside(_bt, "her own voice (mouth echo)")
                        continue

                # Her own voice must not come back as a guest (the User 2026-09-30). EAR_BRIDGE_OWN_VOICE=drop
                # arms the rules in EAR_BRIDGE_OWN_VOICE_RULES (1 + 2 by default; rule 3 reports). Checked
                # before the turn touches any session state.
                if own_voice.mode() != "off":
                    try:
                        _st = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
                        _idle = not (_speaking or _speak_proc is not None or house_mouth_on_air())
                        # a barge-in over her line ("hey Chris, stop!") is a vocative too (round 14)
                        _wk = bool(wake_match(text).get("hit")) or (not _idle and bool(barge_match(text).get("hit")))
                        _wk = _wk or _bt              # round 17: the turn that cut her is a barge-in (only her words go)
                        # Every line she said, whoever spoke it (house-speak's gate as well as this bridge's), and
                        # whether this turn's VOICE is the User's (a face alone at a fixed camera is not), round 13.
                        _SAY.sync_gates(own_voice_gate_paths())
                        try:
                            from .user_mode import _voice_is_user
                            _ov_uid = user_id_from_mode()
                            _ov_user = bool(_ov_uid) and bool(_voice_is_user(
                                _st, (t.get("person_id") or _st.get("person_id") or None), _ov_uid)[0])
                        except Exception:  # noqa: BLE001
                            _ov_user = False
                        ov = own_voice.verdict(
                            t, _SAY, guest=not (t.get("person_id") or _st.get("person_id")),
                            idle_wake=_idle and _wk, wake=_wk, user_voice=_ov_user)
                    except Exception as e:  # noqa: BLE001 - a filter must never deafen her
                        ov = {"rule": None}
                        _log({"event": "own_voice_error", "error": str(e)[:160]})
                    if ov.get("rule"):
                        _ov_drop = own_voice.drops(ov.get("rule"))
                        _log({"event": "own_voice", "mode": own_voice.mode(),
                              "dropped": _ov_drop, "text": text[:80], **ov})
                        if _ov_drop:
                            if ov.get("rule") == "on_air_not_user":
                                # (d) his attempt may be in it: on the log, marked not sent (round 15)
                                heard_roll.note(str(t.get("person_id") or _st.get("person_id") or "?"), text,
                                                "not sent: taken for her own voice")
                            _barge_aside(_bt, "her own voice (%s)" % ov.get("rule"))
                            continue
                    elif ov.get("strip") is not None and own_voice.mode() == "drop":
                        # his barge-in over her line: only his words go on (round 13)
                        _log({"event": "own_voice_strip", "text": text[:80], "kept": ov["strip"][:80],
                              "match": ov.get("match")})
                        text = ov["strip"]
                        t["text"] = text
                        if not text:
                            _barge_aside(_bt, "her own words only")
                            continue

                now = time.time()
                # Room speech always counts as hearing (mouth wait, ID, logs).
                # Pause only gates her *reply*.
                globals()["_last_user_speech"] = now
                if not _bt:
                    asr_barge.STATE.other_turn()     # round 17: someone speaks again; no resume of a false cut
                wm = wake_match(text)
                sess_open = bool(
                    session_live(now)
                    or (gather_until and now <= gather_until)
                )
                if not sess_open and not wm.get("hit") and late_answer_open(now):
                    sess_open = True                  # round 27: his late answer to her question
                    hold_session(now, t.get("person_id"))
                    _log({"event": "late_answer", "text": text[:80], "pid": t.get("person_id")})
                _uid = user_id_from_mode()
                _np = user_voice_prints(_uid)
                gate = classify_hear(
                    t, session_open=sess_open, uid=_uid,
                    user_has_prints=(None if _np is None else _np > 0),
                    resume_pending=(not sess_open) and resume_pending(now),
                )
                if gate.get("unpause") and wm.get("hit"):
                    hold_session(now, t.get("person_id"))
                if gate.get("resume"):
                    # round 16 (c): his continue lifts the pause; the line goes on as a session turn
                    hold_session(now, t.get("person_id"))
                    sess_open = True
                    _log({"event": "pause_lift", "text": text[:80], "pid": t.get("person_id"),
                          "sole_user": gate.get("sole_user")})
                # Face-shell chat wisp reads these on the turn ring.
                try:
                    t["crissa_hears"] = bool(gate["crissa_hears"])
                    t["log_user"] = bool(gate["log_user"])
                    t["log_room"] = bool(gate["log_room"])
                except Exception:
                    pass
                _log(
                    {
                        "event": "hear_gate",
                        "to_her": gate["to_her"],
                        "paused": gate["paused"],
                        "unpause": gate.get("unpause"),
                        "guest_rails": gate.get("guest_rails"),
                        "user_ready": gate.get("user_ready"),
                        "crissa_hears": gate["crissa_hears"],
                        "log_user": gate["log_user"],
                        "log_room": gate["log_room"],
                        "pid": gate["person_id"],
                        "media": gate["media"],
                        "no_print": gate.get("no_print"),
                    }
                )
                st_heard = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
                _log(
                    {
                        "event": "heard",
                        "text": text[:80],
                        "pid": t.get("person_id"),
                        "via": t.get("via") or "ear",
                        "beam_angle": st_heard.get("beam_angle"),
                        "paused": gate["paused"],
                        "crissa_hears": gate["crissa_hears"],
                    }
                )
                pa_yield_for_hear(gate, session_open=sess_open)
                if display_call.keep_heard(text) and (gate["crissa_hears"] or sess_open):
                    board_pin(True)           # "leave it up": the sheet skips the clear after her pause
                if (display_call.take_down_heard(text)
                        and (gate["crissa_hears"] or sess_open or _speaking or _speak_proc is not None or _dispatch_busy)
                        and _display_take_down(text, t)):
                    continue          # round 27: "take it down" is done at once, over her answer too
                if (_bt and asr_barge.STATE.cur.get("cut_why") == "three_words" and not wake_match(text).get("hit")
                        and not barge_match(text).get("hit") and not stop_only(text)):
                    # .101 round 28: three words of someone's cut her; her model decides (_three_word_run)
                    _m3 = {"kind": "session", "person_id": t.get("person_id"), "stamp": t.get("stamp"), "text": text,
                           "session_id": free_session_id, "profile": session_profile, "route": session_route,
                           "addressed": session_addressed}
                    threading.Thread(target=_three_word_run, args=(text, _m3), name="ear-bridge-three-words",
                                     daemon=True).start()
                    continue
                if name_interrupt_hit(
                    text,
                    speaking=_speaking or (_speak_proc is not None) or _bt,   # round 17: the turn that cut her
                    busy=_dispatch_busy,
                    stamp=st_heard,
                    person_id=t.get("person_id") or gate.get("person_id"),
                ):
                    _bm = barge_match(text)
                    residual = str(_bm.get("residual") or "").strip()
                    abort_mouth("name_interrupt")
                    gather_until = 0.0
                    buf = []
                    last_speech_in_gather = 0.0
                    cut_mid_sentence = False
                    _stop_only = stop_only(residual)
                    note_stop_words(text)            # round 16: "stop for good" closes the reading
                    _log(
                        {
                            "event": "name_interrupt",
                            "text": text[:80],
                            "residual": residual[:100],
                            "pid": t.get("person_id"),
                            "via": _bm.get("via"),
                            "stop_only": _stop_only,
                        }
                    )
                    if not residual or _stop_only:
                        continue            # "stop" / "wait" / "one second" after her name: stopped, nothing asked
                    if _bm.get("via") == "barge":
                        # her name as the ear wrote it ("Chris"): the ask below goes on as addressed to her
                        text = "Hey Crissa, " + residual
                        t["text"] = text
                    # Residual ask: fall through to the vocative path below.
                elif not _bt and _barge_turn_cut(t, text, now):
                    _bt = True        # round 17: the ear's looks missed it; the whole decode cuts her (no over-line ask)
                elif not _bt and _side_talk_turn(t, text, now):
                    continue          # round 18: said over her answer with no interrupt word: the log's, never hers

                now = time.time()
                if cmd_until and now > cmd_until:
                    cmd_until = 0.0
                wm_lock = wake_match(text)
                if wm_lock.get("hit") and not voice_on():
                    # Bare "Hey Crissa" while locked: keep a short window so
                    # the next "voice on" is a house command, not dropped.
                    cmd_until = now + 12.0
                m_lock = match_control(text)
                cmd_lock = (m_lock or {}).get("cmd")
                if cmd_lock in ("voice-on", "voice-off"):
                    allow_lock = bool(wm_lock.get("hit") or cmd_until)
                    if allow_lock:
                        try:
                            presence = _get(PRESENCE, timeout=1.0)
                        except Exception:
                            presence = {}
                        if try_handle_utterance(
                            text,
                            meta={
                                "person_id": t.get("person_id"),
                                "stamp": t.get("stamp"),
                                "text": text,
                            },
                            presence=presence,
                            speak=lambda s: speak_blocking(s, post_cooldown=1.2),
                        ):
                            _log(
                                {
                                    "event": "wake_cmd",
                                    "cmd": cmd_lock,
                                    "text": text[:80],
                                    "pid": t.get("person_id"),
                                }
                            )
                            cmd_until = 0.0 if cmd_lock == "voice-on" else cmd_until
                            continue

                # Confirm / cancel / countdown without wake (verbal only).
                # Confirm requires User voice (handle_control _is_authed + gate
                # here so free-air never injects on sticky alone).
                if pending_phase() in ("await_confirm", "countdown"):
                    try:
                        presence = _get(PRESENCE, timeout=1.0)
                    except Exception:
                        presence = {}
                    stamp_cc = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
                    meta_cc = {
                        "person_id": t.get("person_id"),
                        "stamp": stamp_cc,
                        "text": text,
                    }
                    m_cc = match_control(text)
                    cmd_cc = (m_cc or {}).get("cmd")
                    # cancel: always try (abort is safe). confirm/system: need B.
                    if cmd_cc == "confirm":
                        ev_cc = evaluate_turn(
                            presence,
                            stamp_cc,
                            person_id=t.get("person_id"),
                            look_pending=bool(globals().get("_session_looking")),
                        )
                        if not command_may_execute(ev_cc):
                            _log(
                                {
                                    "event": "confirm_freeair_denied",
                                    "pid": t.get("person_id"),
                                    "reason": ev_cc.get("auth_reason"),
                                    "tools_ok": ev_cc.get("tools_ok"),
                                }
                            )
                            speak_blocking(
                                id_gate.refuse_line("Only your voice can confirm that."),
                                post_cooldown=1.0,
                            )
                            gather_until = 0.0
                            buf = []
                            continue
                    if try_handle_utterance(
                        text,
                        meta=meta_cc,
                        presence=presence,
                        speak=lambda s: speak_blocking(s, post_cooldown=1.0),
                    ):
                        gather_until = 0.0
                        buf = []
                        continue

                if not gate["crissa_hears"]:
                    gather_until = 0.0
                    buf = []
                    last_speech_in_gather = 0.0
                    cut_mid_sentence = False
                    _drop_pending("paused" if gate["paused"] else "not_to_her")
                    _barge_aside(_bt, "paused" if gate["paused"] else "not to her", text)
                    _log(
                        {
                            "event": "crissa_skip",
                            "why": "paused" if gate["paused"] else "not_to_her",
                            "text": text[:80],
                            "pid": gate["person_id"],
                            "guest": gate.get("guest_rails"),
                        }
                    )
                    if gate["paused"] and is_user_pid(gate.get("person_id"), user_id_from_mode()):
                        # (d)/(e) his words after the session ended ("What book was that?"): on the log, not sent
                        heard_roll.note(str(gate.get("person_id") or "?"), text,
                                        "not sent: listening is off, say her name first")
                    continue

                if gate.get("no_print") and wm.get("hit") and not_said_no_print():
                    # Z (the User 2026-10-10): heard as a guest because the User has no voice print yet; said once,
                    # aloud and on the roll (her spoken line is the Wall's caption), then her answer goes on
                    _log({"event": "no_voice_print", "pid": gate["person_id"], "text": text[:80]})
                    speak_blocking(NO_PRINT_LINE, post_cooldown=0.4)

                if render_skips_crissa() and render_lila_host():
                    # RENDER: LILA HAS THE GLASS (the User 2026-09-13). Behind the
                    # gate (a vocative, or a turn inside the live session) the
                    # turn is Lila's: her studio answers on crew. Another
                    # mouth's direct wake (Ada) is not hers and falls through
                    # to its own gateway. Before Comfy is up the turn falls to
                    # the silent branch below (no scripted line, the User 2026-09-14).
                    if to_other_mouth(wm):
                        _log({"event": "render_other_mouth", "profile": wm.get("profile"), "text": text[:80]})
                    elif comfy_up():
                        _log({"event": "render_lila_turn", "vocative": bool(wm.get("hit")), "text": text[:80]})
                        post_lila_turn(text)
                        continue

                if render_skips_crissa() and not (render_lila_host() and to_other_mouth(wm)):
                    # Behind the gate: only vocative / LISTENING turns get a
                    # mouth line. Room chatter and TV stay silent in Render.
                    _log(
                        {
                            "event": "render_crissa_asleep"
                            if not studio_holds_ear()
                            else "studio_hold",
                            "text": text[:80],
                        }
                    )
                    # Nothing is said (the User 2026-09-14: "i want no scripted
                    # announcement of render mode"): Lila is on the glass.
                    continue

                if wm.get("hit"):
                    residual = str(wm.get("residual") or "").strip()
                    free_session_id = f"freeair-{int(now)}"
                    meta = _wake_meta(wm, t, free_session_id)
                    session_profile = str(meta.get("profile") or "crissa")
                    session_route = str(meta.get("route") or "direct")
                    session_addressed = meta.get("addressed")
                    # Hard-rail residual (hybrid/voice/system) even if voice-off
                    if residual:
                        try:
                            pres_cmd = _get(PRESENCE, timeout=1.0)
                        except Exception:
                            pres_cmd = {}
                        if try_handle_utterance(
                            residual,
                            meta=meta,
                            presence=pres_cmd,
                            speak=lambda s: speak_blocking(s, post_cooldown=1.2),
                        ):
                            _log(
                                {
                                    "event": "wake_cmd",
                                    "residual": residual[:100],
                                    "pid": t.get("person_id"),
                                }
                            )
                            hold_session(now, t.get("person_id"))
                            continue
                    # Non-command residual / bare wake needs voice lock for LLM
                    if not voice_on():
                        _log(
                            {
                                "event": "wake_locked",
                                "text": text[:80],
                                "kind": wm.get("kind"),
                            }
                        )
                        continue
                    # Vocative lean-in always says the scripted Yeah? (maybe
                    # more if there is a residual). Open session so a later
                    # ask is free-air follow-up, not a 6s hold.
                    hold_session(now, t.get("person_id"))
                    m = dict(meta)
                    st_w = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
                    if "is_user" in st_w:
                        m["is_user"] = st_w.get("is_user")
                    m["person_id"] = t.get("person_id")          # THIS turn's speaker, as the ear named it (BB)
                    m["guest_rails"] = bool(gate.get("guest_rails"))
                    has_ask = bool(residual and len(residual.strip()) >= 2)
                    m["has_ask"] = has_ask    # .101 round 28: the ask came with the name - no "Yeah?" (bare_wake_ack)
                    _log(
                        {
                            "event": "wake",
                            "kind": wm.get("kind"),
                            "text": text[:100],
                            "pid": t.get("person_id"),
                            "residual": residual[:100] if has_ask else "",
                            "session_id": free_session_id,
                            "bare_ack": "immediate",
                            "profile": session_profile,
                            "route": session_route,
                            "addressed": session_addressed,
                        }
                    )
                    threading.Thread(
                        target=bare_wake_ack,
                        args=(m,),
                        name="bare-wake-ack",
                        daemon=True,
                    ).start()
                    if not has_ask:
                        continue
                    buf = [residual]
                    last_speech_in_gather = now
                    gather_until = now + GATHER_SEC
                    hold_session(gather_until, t.get("person_id"))
                    continue

                if gather_until and now <= gather_until:
                    if wm.get("kind") != "denied_confusion":
                        buf.append(text)
                        last_speech_in_gather = now
                        cut_mid_sentence = still_talking(t)
                        if join_least_trust_on():
                            # round 46: a later part never lends the ask a more trusted voice (hear_gate.join_speaker)
                            apply_join_speaker(meta, dict(meta), {"person_id": t.get("person_id"),
                                                                  "stamp": t.get("stamp")})
                        elif t.get("person_id"):
                            meta["person_id"] = t.get("person_id")
                            meta["stamp"] = t.get("stamp")
                        # Extend by a full chunk period, not 0.5s: each arriving
                        # block is evidence the User is STILL TALKING, so keep the
                        # window open long enough for the next one to land.
                        gather_until = min(
                            gather_until + CHUNK_PERIOD, now + GATHER_MAX
                        )
                        hold_session(now, t.get("person_id"))
                    continue

                # Session follow-up — real speech (mouth echo already skipped)
                if session_live(now) and not gather_until:
                    if not voice_on_cached():
                        drop_session(board=False)
                        continue
                    if _VOICE_PAUSE.match(text):
                        _log({"event": "voice_pause", "text": text[:40]})
                        _log({"event": "session_end", "why": "voice_pause"})
                        listening_off("voice_pause")
                        drop_session(board=True)
                        continue
                    if wm.get("kind") == "denied_confusion":
                        _barge_aside(_bt, "denied confusion")
                        continue
                    _spk = t.get("person_id") or ((t.get("stamp") or {}).get("person_id") if isinstance(
                        t.get("stamp"), dict) else None)
                    try:
                        _user = user_id_from_mode() if SESSION_NONE_FOLLOWS else None
                    except Exception:  # noqa: BLE001  no User known: the owner rule alone
                        _user = None
                    if not session_follow_ok(session_owner, _spk, text, _user):
                        # another voice in the room: not her conversation, and it does not keep the session open
                        _log({"event": "session_skip_other", "text": text[:60], "pid": _spk, "owner": session_owner})
                        _barge_aside(_bt, "another voice", text)
                        continue
                    if session_takes_owner(session_owner, _spk, _user):
                        _log({"event": "session_owner_taken", "pid": _spk, "was": session_owner})
                        session_owner = _spk
                    # require a bit of content (avoid "Yeah." / "Uh" / "Mm-hmm." loops)
                    # Measured 2026-08-01: "Mm-hmm." bypassed the old list (hyphen)
                    # and got a full Crissa turn mid-session.
                    _tok = (
                        text.lower()
                        .strip()
                        .rstrip(".!?,…")
                        .replace("-", "")
                        .replace(" ", "")
                    )
                    if len(text) < 16 and _tok in (
                        "yeah",
                        "yes",
                        "yep",
                        "yup",
                        "uh",
                        "um",
                        "uhhuh",
                        "ok",
                        "okay",
                        "mm",
                        "mmm",
                        "hmm",
                        "mmhmm",
                        "mhm",
                        "mhmm",
                        "right",
                        "sure",
                        "k",
                        "kay",
                    ):
                        _log({"event": "session_skip_short", "text": text[:40]})
                        _barge_aside(_bt, "a listener's word")
                        continue
                    if _bt and stop_only(text):
                        # round 17: "Stop." / "Wait." over her line - she is stopped already; nothing is asked (round
                        # 14's name-interrupt rule), and a stop for good closes the reading
                        note_stop_words(text)
                        asr_barge.STATE.took("stop")
                        heard_roll.note(str(_spk or "?"), text, "said over her line: she stopped")
                        _log({"event": "barge_stop", "text": text[:60], "pid": _spk})
                        continue
                    buf = [text]
                    meta = {
                        "kind": "session",
                        "person_id": t.get("person_id") or meta.get("person_id"),
                        "stamp": t.get("stamp") or meta.get("stamp"),
                        "text": text,
                        "session_id": free_session_id or meta.get("session_id"),
                        "profile": session_profile,
                        "route": session_route,
                        "addressed": session_addressed,
                    }
                    last_speech_in_gather = now
                    gather_until = now + GATHER_SEC
                    if _bt:
                        # round 17: the turn that cut her is whole (the ear's hangover ended it): sent now, no gather
                        asr_barge.STATE.took("turn")
                        gather_until = now
                        last_speech_in_gather = now - FLUSH_FAST
                    hold_session(now, meta.get("person_id"))
                    _log(
                        {
                            "event": "session_utt",
                            "text": text[:100],
                            "pid": meta.get("person_id"),
                            **({"barge": True} if _bt else {}),
                        }
                    )
                    continue
                _barge_aside(_bt, "no turn for her")

            _barge_tick(time.time())                 # round 17: a false cut goes on by itself (asr_barge)
            now = time.time()
            if gather_until:
                # Wait the full window only when the last block was cut
                # mid-sentence; a block that ended in silence means the User
                # finished, so dispatch fast instead of stalling him.
                user_so_far = " ".join(buf).strip()
                if not user_so_far:
                    # No bare-wake hold — clear empty gather (should be rare)
                    if now > gather_until:
                        gather_until = 0.0
                        last_speech_in_gather = 0.0
                        cut_mid_sentence = False
                        buf = []
                else:
                    need_quiet = GATHER_SEC if cut_mid_sentence else FLUSH_FAST
                    need_quiet = speculate.flush(need_quiet, cut_mid_sentence)   # round 17: 0 while speculating
                    quiet = (
                        (now - last_speech_in_gather) >= need_quiet
                        if last_speech_in_gather
                        else False
                    )
                    if now > gather_until or (quiet and last_speech_in_gather):
                        user = user_so_far
                        gather_until = 0.0
                        last_speech_in_gather = 0.0
                        cut_mid_sentence = False
                        # Last speaker was him — 15s for a tag-on sentence.
                        m = dict(meta)
                        buf = []
                        hold_session(now, m.get("person_id"))
                        schedule_dispatch(user, m)

            speculate.after_poll(_spec_st)          # round 17: this poll's turns are handled: is her turn final?
            _ext = globals().get("_session_extend_until") or 0.0
            if _ext and _ext > session_until:
                session_until = _ext
                globals()["_session_extend_until"] = 0.0
            globals()["_session_until_pub"] = session_until
            if session_idle_should_drop(
                now,
                session_until,
                live=session_live(now),
                gather=bool(gather_until),
            ):
                _log({"event": "session_end"})
                listening_off("timeout")
                drop_session(board=True)

            seen = trim_seen(seen)

        except urllib.error.URLError as e:
            quiet_log({"event": "poll_error", "error": str(e)[:120]})
        except Exception as e:
            _log({"event": "loop_error", "error": f"{type(e).__name__}: {e}"[:200]})

        time.sleep(POLL)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _log({"event": "stop"})
        raise SystemExit(0)
