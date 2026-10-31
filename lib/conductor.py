"""The Conductor's sequencing — what she does next, and what she may do next.

Runs in `scripts/house-conductor.py` on its own port, but lives here as plain
logic so it can be tested without a server, a browser, or a GPU.

THE LAWS THIS ENFORCES, all of them earned on the wall 2026-08-29/30:

1. **Attach.** A loop starts and ends on its hub. A loop on ANOTHER hub is
   reachable only by its slide — taking it directly is the cut the User kept
   seeing ("the shift from pushed back to pushed forward as a snap, no Rev
   Transition"). Slides are exempt: crossing is their whole job.

2. **The bag, not the die.** Uniform random reads as repetition on a wall you
   watch for an hour, and leaves clips unseen all evening. Draw without
   replacement: every clip in the pool gets a turn before any repeats.

3. **The cadence is the rule; the clip is a draw.** Naming a clip in a
   scheduler path made her do the same tuck every ~28 s while the bag never
   decremented, because that path never asked it.

4. **Nothing fires off LISTENING, air, or the ear.** Only three things move
   her: the Conductor, her own f2f, and a clip running out. A mode the user
   explicitly asks for is a request, not a trigger.

5. **Two gates, not one.** `element` is the checker's verdict — may she be in
   this clip at all. `scheduled` is narrower — may the BAG deal it as a fidget.
   Her rest, the slides and the emote specialists are `element: true,
   scheduled: false`: real and enterable, but never rolled. Conflating them
   meant she chained flourish into flourish and could never reach her own desk.
"""

from __future__ import annotations

import random
import os
import time
from typing import Any, Callable, Iterable

from house_wall_prefs import wraps_before_fidget, DEFAULTS as WALL_PREF_DEFAULTS


def _her_name() -> str:
    """Her name on this install (lib/crissa_home.her_name: the house's own, else Crissa; the Crissa merge 2026-10-11)."""
    try:
        import crissa_home
        return crissa_home.her_name()
    except (ImportError, AttributeError):
        return "Crissa"

# Three wraps of a rest loop, then a fidget. ~28 s on a 5 s loop; the number is
# the rule at flourish_pct=35, and which clip fills it is always a draw.
# Live mix is Sequencer.flourish_pct (Advanced Flourish %, non-speech only).
WRAPS_BEFORE_FIDGET = 3
FLOURISH_PCT_DEFAULT = int(WALL_PREF_DEFAULTS["flourish_pct"])
# User-talk FaceTime. Child of HOME (laptop closeup). Most common talking
# scene. Formal / Conference (sit-up on `back`) is the webcam meeting.
# Webcam / monitor view (store FT). Hey Crissa: 0.6 s camera-connection
# (interference) first, then this already-leaned-in loop. Not a lean-in
# slide. The wall FaceTime tile stays — same crop as before. Greet after
# the loop starts. Fade back to room on pause / Secure timeout.
USER_TALK_KEY = "laptop-closeup-eyes-down"
USER_TALK_ENTER = "laptop-closeup-enter"   # deleted — TV static cut, not a scoot
USER_TALK_LEAVE = "laptop-closeup-leave"   # deleted — fade / first tool call
USER_TALK_CUT_S = 0.6
USER_TALK_FADE_S = 0.5
# After the first tool call, FT fades to heads-down MULTITASKING
# (Imagine: work-multitask). Until that loop lands, fade to work.
# No leave slide — TV static in, fade out. Older names stay fade-legal.
WORK_GLANCE_KEY = "work-multitask"
# THE NAMED MOMENTS BY ROLE (the User 2026-10-29: "Could we just rename the related clips for check door. Just
# substitute titles and locations?"; "when i call just make whatever is playing talk"): the classic library's moment
# keys the house code cues by name, and the role each one is. A worn library that lacks the key plays its own clip for
# the role (Graph.roles, from its manifest or library.json `roles`); a role it does not name is not played, so she stays
# where she is and talks there (no camera cut, no event clip). Kill CRISSA_MOMENT_ROLES=0.
MOMENT_ROLES = {"crissa-walk-in": "walk_in", "crissa-walk-out": "walk_out", "crissa-walk-off": "walk_out", "crissa-sit-hello": "hello",
                "flourish-glance": "glance", "flourish-nod": "nod", "flourish-notes": "notes",
                "flourish-shift": "shift", "flourish-yawn-work": "yawn", "work-door": "door",
                "work-multitask": "tool_work", "laptop-closeup-eyes-down": "close"}


def moment_roles_on() -> bool:
    return os.environ.get("CRISSA_MOMENT_ROLES", "1").strip().lower() not in ("0", "false", "off", "no")
WORK_GLANCE_ALIASES = frozenset({WORK_GLANCE_KEY, "work-active-listen", "work-talk-glance"})
# Name-call from work (Law 4). Laptop-closeup stays desk FT, not this.
# Unauthed stand-in. Familiar ~1 s clip is vocative-up when AUTH already held.
VOCATIVE_KEY = "wake"
VOCATIVE_FAMILIAR = "vocative-up"
# Heads-down door look. Cue on a door face (the camera's face count, not an identity), never the bag.
# flourish-glance is the same motion that used to roll from pools.work.
# Sit-up motion is listen-door once Law 1 tails are back↔back. The linger
# take (flourish-listen-door, look up and hold) is a fidget, not this cue.
DOOR_GLANCE_KEY = "work-door"
DOOR_GLANCE_SIT = "listen-door"
DOOR_GLANCE_SIT_NOW = "flourish-listen-ponder"
DOOR_FIDGET_KEY = "flourish-glance"
DOOR_LINGER_KEY = "flourish-listen-door"
DOOR_GLANCE_BY_HUB = {"down": DOOR_GLANCE_KEY, "back": DOOR_GLANCE_SIT}
DOOR_LOOK_KEYS = frozenset({
    DOOR_GLANCE_KEY, DOOR_FIDGET_KEY, DOOR_GLANCE_SIT, DOOR_GLANCE_SIT_NOW,
})
DOOR_ENTER_KINDS = frozenset({"enter", "door_enter"})
USER_VOICE_IDS = frozenset({"user"})   # + the house's own enrolled ids (lib/presence_house.USER_IDS)
DESK_BEAM_HOUR = 6.0
DESK_BEAM_SLACK = 0.75
# Vocative abort-to-hub (JPEG Playhead.i). Not Law 2's 1.2 s wait/skip.
# Loop remaining-to-last at or under this waits the tail; else seek 0.
ABORT_TAIL_S = 0.25
ABORT_FPS = 24.0
# Body playhead. Mouth JPEG outruns this so 20 ms viseme hops land instead of
# waiting a clip frame (41.7 ms). HOUSE_MOUTH_FPS, default 60, clamp 24–72.
CLIP_FPS = 24.0
MOUTH_RENDER_FPS_DEFAULT = 60.0
MOUTH_RENDER_FPS_MAX = 72.0


def mouth_render_fps(raw: float | None = None) -> float:
    """JPEG / composite rate. Faster than the clip. Not W2L's grid."""
    if raw is None:
        try:
            raw = float(os.environ.get("HOUSE_MOUTH_FPS", str(MOUTH_RENDER_FPS_DEFAULT)))
        except (TypeError, ValueError):
            raw = float(MOUTH_RENDER_FPS_DEFAULT)
    return max(CLIP_FPS, min(MOUTH_RENDER_FPS_MAX, float(raw)))


def clip_frames_to_advance(
    accum_s: float, dt: float, clip_fps: float = CLIP_FPS,
) -> tuple[int, float]:
    """How many body frames to step this mouth tick.

    Mouth may fire twice on the same clip frame. A stall must not skip a
    whole take: cap the catch-up at 0.5 s (12 frames at 24 fps).
    """
    fps = max(1.0, float(clip_fps))
    acc = float(accum_s) + max(0.0, float(dt))
    if acc > 0.5:
        acc = 0.5
    n = int(acc * fps)
    n = max(0, min(n, 12))
    acc -= n / fps
    if acc < 0.0:
        acc = 0.0
    return n, acc


def _house_presence(p: dict) -> dict:
    """A presence payload on the neutral keys. The house's own presence service names its face-ID
    block after its engine; lib/presence_house (house only, absent in a pack) maps it. Absent: as given."""
    try:
        import presence_house
    except ImportError as e:
        if e.name != "presence_house":
            raise
        return p
    return presence_house.neutral(p)


def _face_namers() -> tuple:
    try:
        import presence_house
    except ImportError as e:
        if e.name != "presence_house":
            raise
        return ()
    return tuple(presence_house.FACE_NAMERS)


def _house_user_ids() -> frozenset:
    try:
        import presence_house
    except ImportError as e:
        if e.name != "presence_house":
            raise
        return frozenset()
    return frozenset(presence_house.USER_IDS)


def _face_id(p: dict) -> dict:
    """The face-ID block: `face_id` (the neutral key, the house's adapter fills it), else `faceid` - the name the
    pack's own presence serves it under (2026-10-10, a round-3 leftover: the face-lock shortcut in auth_already_held
    and the door-face signature never fired in a pack)."""
    for key in ("face_id", "faceid"):
        b = p.get(key)
        if isinstance(b, dict):
            return b
    return {}


def auth_already_held(presence: dict | None) -> bool:
    """Familiar vocative-up gate. Same precheck that skips FaceFinder.

    The source of truth is presence ``user_auth``. A Secure lamp may hold that grant; it is not a
    second one. A live face-ID lock on the household's user (``face_id.locked`` == ``user_id``,
    ``age_s`` <= 2.5) is the same grant. Without either the cue stays ``wake``.
    """
    p = _house_presence(presence if isinstance(presence, dict) else {})
    if p.get("user_auth"):
        return True
    buf = _face_id(p)
    uid = str(p.get("user_id") or "").strip().lower()
    if not uid:
        return False
    locked = str(buf.get("locked") or "").strip().lower()
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    return bool(locked == uid and (age is None or age <= 2.5))


def door_face_sig(presence: dict | None) -> str:
    """Stable id of a door-FOV face, or '' if none.

    Read from the face-ID block's ``n_faces``, not a lock. Occupancy mill is
    off (``RAIL_OCCUPANCY=0``); ``last_event`` is usually ``door_hold``
    and must not fire. Enter kinds still count if the mill is on.
    Desk visor (amber + source visor) is not the door. Sage visor is:
    Secure holds the camera on the door FOV.
    """
    p = _house_presence(presence if isinstance(presence, dict) else {})
    le = p.get("last_event") if isinstance(p.get("last_event"), dict) else {}
    kind = str(le.get("kind") or "").strip().lower()
    if kind in DOOR_ENTER_KINDS:
        stamp = le.get("ts") or le.get("capture_ts") or le.get("frame_seq")
        return "enter:%s:%s" % (kind, stamp if stamp is not None else "")
    buf = _face_id(p)
    try:
        n = int(buf.get("n_faces") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    source = str(buf.get("source") or "").strip().lower()
    lamp = str(p.get("visit_lamp") or "").strip().lower()
    at_door = source == "door_glance" or lamp == "sage"
    if not at_door:
        return ""
    names = buf.get("names") if isinstance(buf.get("names"), dict) else {}
    who = ",".join(sorted(str(k).strip().lower() for k in names if k))
    return "face:%s:%d:%s" % (source or "door", n, who)


def door_glance_key(graph: "Graph", here: str | None) -> str | None:
    """Motion door look for the hub she is on. The linger flourish is not this.

    ``work-door`` on ``down``. On ``back``: ``listen-door`` once Law 1
    tails are that hub; until then ``flourish-listen-ponder`` (glance
    her right). ``flourish-listen-door`` (look up and linger) stays a
    bag fidget.
    """
    if here == "down":
        keys = (DOOR_GLANCE_KEY,)
    elif here == "back":
        keys = (DOOR_GLANCE_SIT, DOOR_GLANCE_SIT_NOW)
    else:
        return None
    for key in keys:
        if key == DOOR_LINGER_KEY:
            continue
        key = graph.resolve(key)     # the worn library's door clip (v2: v2-fl-turn-door; the 10-29 review of cut 13)
        if not graph.compose_ok(key):
            continue
        if graph.start(key) != here or graph.end(key) != here:
            continue
        return key
    return None


def vocative_from_work_key(graph: "Graph", auth_held: bool) -> str:
    """From-work sit-up. vocative-up replaced 5 s wake; wake is archived.

    auth_held is kept for logs. Unauthed also uses vocative-up now that
    clip-trans-back is off the wall. Fall back to wake only if the 1 s
    clip is missing.
    """
    if graph.compose_ok(VOCATIVE_FAMILIAR):
        return VOCATIVE_FAMILIAR
    return VOCATIVE_KEY


def abort_to_hub_action(
    role: str,
    i: int,
    n: int,
    fps: float = ABORT_FPS,
    tail_s: float = ABORT_TAIL_S,
) -> str:
    """What abort-to-hub does on the JPEG playhead. Not a glass <video> seek.

    ``seek0`` — work (or any rest loop) far from the tail: ``Playhead.i = 0``
    (wrap is ``i == 0``; f0 = last = hub PNG).
    ``wait_tail`` — loop with remaining-to-last ≤ tail_s: let ``_advance`` wrap.
    ``wait_end`` — flourish / oneshot / slide already crossing: nearest hub is
    the tail. Never FaceFinder, never AUTH, never afterBeatMark 1.2 s.

    ``insert()`` still does not cut. Cue first, then abort, so take_cue can
    start the queued vocative ``wake``.
    """
    fps = float(fps) or ABORT_FPS
    tail_s = max(0.0, float(tail_s))
    n = int(n)
    i = int(i)
    role = (role or "").strip().lower()
    if n <= 0:
        return "seek0" if role == "loop" else "wait_end"
    i = max(0, min(i, n - 1))
    if role != "loop":
        return "wait_end"
    remaining_s = (n - 1 - i) / fps
    if remaining_s <= tail_s:
        return "wait_tail"
    return "seek0"


def desk_beam(sound: dict | None) -> bool:
    """True when the mic clock is on desk hour 6 (180°).

    Measured: beam 6.0 at 180°, 6.4 at 191° (docs/BEAM-IS-FF-CLUE.md).
    Hour = az_deg / 30. Slack 0.75 h ≈ ±22°.
    """
    if not isinstance(sound, dict):
        return False
    hour = sound.get("beam")
    if hour is None:
        hour = sound.get("hour") or sound.get("look_hour")
    az = sound.get("az_deg")
    try:
        if hour is None and az is not None:
            hour = float(az) / 30.0
        if hour is None:
            return False
        h = float(hour) % 12.0
        delta = abs(h - DESK_BEAM_HOUR)
        d = min(delta, 12.0 - delta)
        return d <= DESK_BEAM_SLACK
    except (TypeError, ValueError):
        return False


# How fresh a voice stamp must be to mean "he is speaking NOW". The master
# flag's own clock is 6 s on id_ts, so this is that number, not a new one.
VOICE_NAME_STALE_S = float(os.environ.get("HOUSE_VOICE_NAME_STALE_S", "6"))
# After a Hey <User>, a flickering desk beam must not re-arm greet
# (measured bursts 07:28:15 / 07:28:28 / 07:29:22, still the two-day loop).
GREET_COOLDOWN_S = float(os.environ.get("HOUSE_GREET_COOLDOWN_S", "120"))
# Hey the User on FaceTime land. Enter is 6.04 s plus abort/decode slack.
# After this, a leftover close-up must not mint a greeting (measured
# 2026-09-08 10:50:39 / 10:54:24 with no Hey Crissa).
FT_GREET_S = float(os.environ.get("HOUSE_FT_GREET_S", "20"))
# HEY CRISSA WAITS FOR THE SEAM (the User 2026-10-30: "I want the scripted reply, and even the direction in queue to up.
# I just don't want it to cut to up immediately anymore"; "we swapped to Crissa v2 only. That is why this fix completes
# it"): on a library with no close-up the wake's listen loop is QUEUED and taken at the next seam (the end of the loop or
# flourish she is on); the ear's ack is said at once where she is. Kill HOUSE_VOCATIVE_AT_SEAM=0 (heads up at once).
VOCATIVE_AT_SEAM = os.environ.get("HOUSE_VOCATIVE_AT_SEAM", "1").strip().lower() not in ("0", "false", "no", "off")
FT_SITTING = (USER_TALK_ENTER, USER_TALK_KEY)

# THE COMMS CORES (the User 2026-09-10: "she can switch cores in comms just with a
# controlled set ... I would like Conductor to run this loop ... use the LLM
# with guidance as much as is possible vs code"). In a call she sits on one of
# these 1st-person loops — eyes on the User — and the BRAIN, coached by
# docs/CRISSA-BODY-COMMS.md, names the next one at a seam. The law still
# vetoes: a core that is not on the wall (element:false) or has no slide path
# from where she sits is not offered. A pick on another hub is QUEUED, so
# take_cue walks the slides (or the Connecting cover hides the hop). The bag
# deals no fidgets in comms; she talks in loops. A core holds for at least
# COMMS_MIN_WRAPS wraps, and the roll never runs while the voice is on the air, so
# a switch lands between sentences, never inside one. the User 2026-09-10 pm:
# "Add that casual sit back core too. Add them all" — every eye-contact loop,
# the casual one only while the wall lamp is Secure (comms_offer's casual gate).
# Heads-down talking at the desk (loop-talk-work a/b, talk-down-work) is her
# TOOL-CALLING posture in a call and today's best affine talking pose (the User
# 2026-09-10 pm) — no eye contact, so the do-list sends her there when she is
# doing the work, not when she is answering.
COMMS_CORES: tuple[str, ...] = tuple(
    k.strip() for k in os.environ.get(
        "HOUSE_COMMS_CORES",
        "talk-close-user-listen,talk-angled-user-listen,listen,loop-listen-back,loop-listen-back-b,listen-alert,talking-head,talk-back-casual,loop-talk-work,loop-talk-work-b,talk-down-work," + USER_TALK_KEY,
    ).split(",") if k.strip()
)


# THE CORE LOOPS ROTATE (the User 2026-10-29, the anchor model: "the hubs were very long but we split into shorter ones
# for variation ... 1. randomize the core hub players 2. each flourish attaches to all hubs"; "she is stuck in forward
# even when paused"): a library whose pools name a listen set and a work set rests on a random loop of the set that
# fits - the listen set in a conversation, the work set when paused - and, when a core loop has had its turns, takes
# another of the set or a flourish (CORE_ROTATE_P). A pack only (the house's Crissa keeps her hubs). Kill
# CRISSA_CORE_ROTATE=0; CRISSA_CORE_ROTATE_P the share of turns that change core (0.5).
def core_rotate_on() -> bool:
    v = os.environ.get("CRISSA_CORE_ROTATE", "").strip().lower()
    if v:
        return v not in ("0", "false", "off", "no")
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


def core_rotate_p() -> float:
    try:
        return max(0.0, min(1.0, float(os.environ.get("CRISSA_CORE_ROTATE_P", "0.5"))))
    except ValueError:
        return 0.5


def listen_loop_wins_on() -> bool:
    return os.environ.get("CRISSA_LISTEN_LOOP_WINS", "1").strip().lower() not in ("0", "off", "false", "no")


def listen_cores(cores: tuple[str, ...], declared: str | None, clips: dict[str, Any],
                 base: tuple[str, ...] = COMMS_CORES) -> tuple[str, ...]:
    """The face's declared listen loop leads the cores, and another LOOP on its hub leaves them (round 38: the kit's
    listen-warm replaces listen; the call would otherwise sit on whichever came first). A declared loop that is not
    a loop on a hub, or the kill CRISSA_LISTEN_LOOP_WINS=0, keeps the cores as given; on the house the declared loop
    is listen, already a core, and the order is kept."""
    row = clips.get(declared or "") or {}
    hub = row.get("start")
    if not declared or not listen_loop_wins_on() or row.get("role") != "loop" or not hub or row.get("end") != hub:
        return cores
    if declared in base or os.environ.get("HOUSE_COMMS_CORES"):
        return cores                 # the house's listen, or a list named by hand: kept as given
    def same_hub(k: str) -> bool:
        r = clips.get(k) or {}
        return r.get("role") == "loop" and r.get("start") == hub and r.get("end") == hub
    return (declared,) + tuple(k for k in cores if k != declared and not same_hub(k))


def comms_cores_with(declared: str | None, base: tuple[str, ...] = COMMS_CORES) -> tuple[str, ...]:
    """The cores for one player: the list above plus the loop its face's manifest declares
    (`listen_loop`, 2026-10-02), so a pack's own face has a core without its key being written
    here. A loop already listed changes nothing; a list named by HOUSE_COMMS_CORES is kept as given."""
    if not declared or declared in base or os.environ.get("HOUSE_COMMS_CORES"):
        return base
    return base + (declared,)


# Lila on the glass in Render (the User 2026-09-13): she talks on her listening
# loop — the one with a mouth sidecar and her bank's tiles.
LILA_COMMS_CORES: tuple[str, ...] = tuple(
    k.strip() for k in os.environ.get("HOUSE_LILA_COMMS_CORES", "lila-reg-listen").split(",") if k.strip()
)
COMMS_MIN_WRAPS = int(os.environ.get("HOUSE_COMMS_MIN_WRAPS", "2"))
# THE WARM LOOP IS A TREAT, NOT A HABIT (round 39, .101's N2, the User: "keeping the smile in that loop too many times
# may not look natural either. Let's cap that at 1 or 2x before a more neutral loop"): the rest loop plays at most
# WARM_MAX_PLAYS times in a row, then her listen loop for WARM_NEUTRAL_PLAYS, then it may return. Only a face that
# declares a rest loop; never while she speaks.
WARM_MAX_PLAYS = max(1, int(os.environ.get("CRISSA_WARM_MAX_PLAYS", "2") or 2))
WARM_NEUTRAL_PLAYS = max(1, int(os.environ.get("CRISSA_WARM_NEUTRAL_PLAYS", "2") or 2))
# THE CAP NEVER KEEPS HER OFF THE DESK (round 42, .101's R41-1, the User 10-22: "on paused she should be more on heads
# down work. I see her mostly looking up"): the cap's swap was a pick ahead of the bag, so on `back` after a conversation
# she went rest -> listen -> rest for ever and the wrap that walks her home never came (30 min idle from listen-warm:
# 59 % listen-warm, 41 % listen, 0 % work). Off the home hub with no conversation on, no swap is due: her rest loop
# wraps, the bag on `back` is empty and the wrap walks her home to `work`. Kill CRISSA_WARM_CAP_GOES_HOME=0.
def warm_cap_goes_home_on() -> bool:
    return os.environ.get("CRISSA_WARM_CAP_GOES_HOME", "1").strip().lower() not in ("0", "off", "false", "no")
# FLOURISHES DURING SPEECH (the User 2026-09-27: "we could tag some as inappropriate during speech
# ... some of them could still run during speech for realism"; "yawn nod and notes would get those
# tags"). A row with `speech: false` waits for a pause, as every flourish did before; any other
# flourish may start while she speaks (painted, so its mouth moves with the line). A call still
# deals nothing (comms). Kill HOUSE_FLOURISH_SPEECH=0: no flourish starts on air again.
FLOURISH_SPEECH = os.environ.get("HOUSE_FLOURISH_SPEECH", "1").strip().lower() not in ("0", "false", "no", "off")
# MORE CLIPS WAIT FOR A PAUSE, BY NAME (round 23, the User 2026-10-15, .101 "tile picking is not great": with no brain
# the bag dealt yawn-work, stretch-shoulders, stretch-eyes, stretch-neck under her reading). HOUSE_SPEECH_HOLD_CLIPS is a
# comma list of keys or globs held like a `speech: false` row; empty (the house) changes nothing. A pack sets it in
# config/crissa-mouth.json.
SPEECH_HOLD_CLIPS: tuple[str, ...] = tuple(
    k.strip() for k in os.environ.get("HOUSE_SPEECH_HOLD_CLIPS", "").split(",") if k.strip())


def speech_held(key: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatchcase(str(key), pat) for pat in SPEECH_HOLD_CLIPS)
# NO NEW FLOURISH WHILE A CONVERSATION IS ON (the User 2026-10-01: "flourishes should hold until there's a pause in
# the conversation, never during listening or speaking"; "the others should wind down gracefully once talking
# starts"; "her flourishes should start immediately once Paused comms"). hold_flourishes only covered SPEAKING (her
# mouth painting) and it CUTS a playing flourish; listening and the gaps between turns had no hold, so the brain
# rolled a flourish every seam (flourish_pct 90) through a conversation. This holds only NEW picks - the bag/brain
# roll, the loop's fidget clock, a queued flourish/glance - while `conversation` is set (the service sets it from
# :8099 hear.paused == false); a flourish already playing finishes; heads-down `work` (a loop) and the tool cut to
# work-multitask are never held. OFF on the house by default: HOUSE_FLOURISH_CONVERSATION_HOLD=1 arms it (its wall
# unit's drop-in). ON in a pack (the User 2026-10-14, "wire it to the ear"): the player reads the ear bridge's /health.


def _conv_hold_default() -> str:
    try:
        import crissa_home
        return "0" if crissa_home.is_house() else "1"
    except Exception:  # noqa: BLE001 - no manifest to read: the house's default
        return "0"


CONV_HOLD = os.environ.get("HOUSE_FLOURISH_CONVERSATION_HOLD", _conv_hold_default()).strip().lower() in ("1", "true", "yes", "on")


def _brain_log():
    """conductor_brain.brain_log when HOUSE_BRAIN_LOG=1, else None (read at import; off by default)."""
    try:
        import conductor_brain as _cb
    except Exception:  # noqa: BLE001
        return None
    return _cb.brain_log if _cb.BRAIN_LOG else None
# A DOOR FACE THAT BLINKS OUT IS STILL THE SAME FACE (the User 2026-10-01). The door snapshot (every ~5 s) read
# faces=1,0,1,1,1,0 for one person already seated in the room as her face turned; every 0 -> 1 was a "new" face, so
# work-door fired 5 times in 6 min (cooling is only 8 s). With this > 0, a door face whose signature (count +
# names) comes back within this many seconds of leaving is the same face: no new glance. 0 = today's rule (OFF).
DOOR_FACE_SAME_S = float(os.environ.get("HOUSE_DOOR_FACE_SAME_S", "0") or 0)
TOUR_DWELL_S = float(os.environ.get("HOUSE_TOUR_DWELL_S", "12"))
# Spoken Connecting / Authenticating cover is gone (the User 2026-09-15).
# The lean-in / push-back slides are the move. Do not restore.
# Demand-only. Bag/brain must not deal FaceTime — that seek0'd the close-up
# (measured 2026-09-08, why=brain, no Connecting).
FT_DEMAND = frozenset({USER_TALK_KEY, USER_TALK_ENTER, USER_TALK_LEAVE})


def voice_names_user(speaker: dict | None) -> bool:
    """Voice embed named the User, RECENTLY. Face AUTH is not this gate.

    the User 2026-09-02: Hey <User> fires when voice returns the User, not when
    AUTH lands. AUTH-fail speech stays the AUTH path.

    THE STAMP IS NOT AN EVENT (2026-09-05). the User: "No reason for that to fire
    with no words from me." This gate read person_id / voice_green / user and
    no clock at all, but the stamp is a LATCH -- it keeps naming him long after
    he stops talking. Measured live while the room was silent:
    `voice_age_s = 81.86`. So any beam swing to desk hour 6 during that minute
    reads as "voice-the User at the desk" and greets him for saying nothing. It
    also explains bursts rather than a cadence: `note_desk_user` re-arms
    `_greeted` every time `live` drops, so a flickering beam over one stale
    stamp can greet repeatedly (07:28:15, 07:28:28, 07:29:22 today).

    `envelope_at` directly below has had `stale_s=0.5` all along -- the
    discipline existed, this gate just never used it. The stamp publishes
    `voice_age_s`; require it.
    """
    if not isinstance(speaker, dict):
        return False
    if speaker.get("voice_green") is False:
        return False
    age = speaker.get("voice_age_s")
    if age is None:
        age = speaker.get("age_s")
    if age is not None:
        try:
            if float(age) > VOICE_NAME_STALE_S:
                return False
        except (TypeError, ValueError):
            pass
    pid = str(speaker.get("person_id") or "").strip().lower()
    named_by = str(
        speaker.get("named_by") or speaker.get("source") or ""
    ).strip().lower()
    if named_by in ("face", "face_id", "auth", "camera") + _face_namers():
        return False
    if pid in USER_VOICE_IDS or pid in _house_user_ids():
        return True
    if speaker.get("user") is True and speaker.get("voice_green") is True:
        return True
    return False


def envelope_at(frames: list, now: float, stale_s: float = 0.5) -> float | None:
    """RMS at `now` from `/v1/mouth` frames `[[pts, rms], ...]`.

    None if empty or the newest stamp is older than `stale_s`. Do NOT return
    1.0 as a fallback: `/v1/sound` crissa.level is 1 whenever she is on the air
    (a lamp, not an envelope), and scoring the strip from that pegs the most
    open tile for the whole turn — the User 2026-08-30, Composer: mouth never
    moved, just that shape.
    """
    if not frames:
        return None
    try:
        last_t = float(frames[-1][0])
        if last_t < now - stale_s:
            return None
        chosen: float | None = None
        for row in reversed(frames):
            if float(row[0]) <= now:
                chosen = float(row[1])
                break
        if chosen is None:
            # Before the first hop. Buffered/script POSTs the whole
            # envelope up front; using hop 0 here ran the mouth ~0.7 s
            # early (400 ms lead-silence pad + viseme lead).
            return None
        return max(0.0, min(1.0, chosen))
    except (TypeError, ValueError, IndexError):
        return None


def viseme_at(frames: list, now: float, stale_s: float = 0.5) -> str | None:
    """Class name at `now` from `/v1/mouth` frames `[[pts, rms, viseme], ...]`.

    None if empty, stale, or the hop has no third column — the strip then
    falls back to loudness. Does not invent a class from RMS.
    """
    from mouth_viseme import viseme_of

    if not frames:
        return None
    try:
        last_t = float(frames[-1][0])
        if last_t < now - stale_s:
            return None
        row = None
        for r in reversed(frames):
            if float(r[0]) <= now:
                row = r
                break
        if row is None:
            # now is before the first stamp. Prep window (air_at − lead)
            # may read hop 0; the 400 ms paplay pad must not.
            first_t = float(frames[0][0])
            try:
                from mouth_playlist import viseme_lead_s
                lead = viseme_lead_s()
            except Exception:
                lead = 0.1
            if now >= first_t - lead:
                row = frames[0]
            else:
                return None
        if len(row) < 3:
            return None
        return viseme_of(row[2])
    except (TypeError, ValueError, IndexError):
        return None


def note_span(
    frames: list, now: float, stale_s: float = 0.5
) -> tuple[float, float] | None:
    """[t0, t1) of the consecutive same-class run containing `now`.

    t1 is the first hop of a different class, or last-hop + hop-dt if this
    run is the tail. None if hops are missing, stale, or have no class —
    the painter then uses SERIES_ONCE_S.
    """
    from mouth_viseme import viseme_of

    if not frames:
        return None
    try:
        last_t = float(frames[-1][0])
        if last_t < now - stale_s:
            return None
        cur_i = None
        for i in range(len(frames) - 1, -1, -1):
            if float(frames[i][0]) <= now:
                cur_i = i
                break
        if cur_i is None:
            cur_i = 0
        row = frames[cur_i]
        if len(row) < 3:
            return None
        klass = viseme_of(row[2])
        if not klass:
            return None
        t0_i = cur_i
        while t0_i > 0:
            prev = frames[t0_i - 1]
            if len(prev) < 3 or viseme_of(prev[2]) != klass:
                break
            t0_i -= 1
        t1_i = cur_i
        while t1_i + 1 < len(frames):
            nxt = frames[t1_i + 1]
            if len(nxt) < 3 or viseme_of(nxt[2]) != klass:
                break
            t1_i += 1
        t0 = float(frames[t0_i][0])
        if t1_i + 1 < len(frames):
            t1 = float(frames[t1_i + 1][0])
        else:
            if t1_i > t0_i:
                dt = (float(frames[t1_i][0]) - t0) / (t1_i - t0_i)
            else:
                dt = 0.02
            t1 = float(frames[t1_i][0]) + max(0.02, dt)
        return t0, t1
    except (TypeError, ValueError, IndexError):
        return None


# After the last syllable, close on the strip, then hand back to film.
# Same 0.4 s as scripts/crissa-tool-callout.py TAIL_S.
MOUTH_TAIL_S = 0.4
# The voice's first hops can lag the speaking flag by a beat. Past this, a flag
# with no envelope is a stuck gate, not a mouth.
MOUTH_PRE_HOP_S = 2.0


def mouth_on_air(
    frames: list,
    now: float,
    air_at: float | None,
    air_until: float | None,
    stale_s: float = 0.5,
    pre_hop_s: float = MOUTH_PRE_HOP_S,
) -> bool:
    """Picture air. The speaking FLAG is not enough — it sticks.

    Fresh hops are the truth. A predicted window covers the first-byte gap
    before hops land. A flag with neither is a stuck gate: filmed rest, not
    fish-lips frozen at 1.0.
    """
    if envelope_at(frames, now, stale_s) is not None:
        return True
    if air_at is None:
        return False
    try:
        at = float(air_at)
    except (TypeError, ValueError):
        return False
    if air_until is not None:
        try:
            return at <= now < float(air_until)
        except (TypeError, ValueError):
            return False
    return 0.0 <= (now - at) < pre_hop_s


def mouth_paints_on(role: str) -> bool:
    """Live viseme paste is for rest loops only.

    Slides keep the filmed jaw (body mid-move). Flourishes can cover the
    mouth — yawn-work is a hand over the lips — so a paste reads as a
    talking mouth through the hand. Law 3 still harvests boxes on those
    clips; it does not mean paste over an occlusion. the User 2026-09-07.
    """
    return (role or "").strip().lower() == "loop"


def mouth_painting(
    speaking: bool,
    last_air: float,
    now: float,
    tail_s: float = MOUTH_TAIL_S,
) -> bool:
    """Paint the strip while she is on the air, and for tail_s after.

    Not always-on: a never-spoken playhead (last_air <= 0) stays filmed rest.
    A Composer hold is speaking (force is not None).
    """
    if speaking:
        return True
    if last_air <= 0:
        return False
    age = now - last_air
    return 0.0 <= age < tail_s

# REGISTERS. the User, 2026-08-30: "Presenting is talking head min. Then office
# work is a whole larger group of movement etc."
#
# A register is a VOCABULARY, not a mood — the range of movement that suits
# what is going on. Presenting, she holds still and lets her face carry it;
# working, the whole desk is available. The boundaries are MEASURED off the
# per-frame mouth tracks rather than hand-tagged, in mouth-box widths per
# second (p90), so a new clip files itself:
#
#   talking-head 0.99   listen-alert 1.03   work 1.36   <- held, presenting
#   listen 1.50   wake 1.58   tuck-work 1.60   notes 1.61   shift 1.85
#   nod 2.25   makeup-am 2.70   emote-woe 3.27   glance 4.60  <- big gestures
#
# THIS IS NOT A TRIGGER, and the distinction is the whole of Law 4. What is
# banned is a hidden reflex — the ear twitches and her head jerks. Choosing the
# vocabulary that suits the moment is the opposite: it is what a conductor is
# for, it is visible in `/v1/state`, and it never interrupts a gesture.
# The ceilings are set FROM the desk pool, not guessed at it. Sorted p90:
#   tuck-work 1.60   notes 1.61   shift 1.85   nod 2.25   makeup-am 2.70
#   glance 4.60
# `work` itself is 1.36, so a true talking-head-minimal register does not
# exist at the desk yet: nothing scheduled there is quieter than 1.60, and a
# 1.40 ceiling emptied the pool entirely. 1.70 is the first line that actually
# separates something — a strand tucked back and a glance at the notes, and
# nothing that moves her head. Raise it when Grok's quiet desk clips land.
REGISTERS = {
    "presenting": 1.70,   # held: hands and eyes only, the face does the work
    "working":    2.40,   # ordinary desk life, no big turns
    "desk":       9.99,   # everything the bag has, including the door glance
}
DEFAULT_REGISTER = "desk"
# How many played clips the history keeps. the User, 2026-08-31: "use another
# method like History section for played clips in order. Just transfer from
# queue to History once completed." Dimming a played button would have
# collided with the hollow/cut-out grammar the kit now uses for availability,
# so what she DID gets its own list instead of a second meaning for grey.
HISTORY_CAP = 40
# Composer DJ home: laptop, eyes on the top bevel. The only flourish start
# the kit needs. Away-hub clips still exist; Composer brings her here first.
HOME = "down"


# LILA IS NOT IN CRISSA'S IDLE (the User 2026-10-13, .101 round 15: right after a session ended the wall walked to
# lila-listen-enter, lila-reg-listen, lila-listen-leave with nothing asking for it). A guest host's clips (her rack,
# GUEST_RACKS) are only enterable while their own owner says so: in a pack the player sets GUEST_GATE to "Setup is
# running or Lila is on her plate" (lib/crissa_init.lila_may_play); otherwise no route, rest or cue reaches them. A
# forced play (Setup's own) never asks compose_ok. Unset (the house, a bench, tests): as before.
GUEST_RACKS = ("host-lila",)
GUEST_GATE: "Callable[[], bool] | None" = None
_GUEST_MEMO = {"t": 0.0, "ok": True}


def _guest_allowed() -> bool:
    now = time.time()
    if now - _GUEST_MEMO["t"] > 1.0:          # read at most once a second: compose_ok runs for every clip on a route
        try:
            _GUEST_MEMO["ok"] = bool(GUEST_GATE()) if GUEST_GATE is not None else True
        except Exception:  # noqa: BLE001  an unreadable gate keeps her clips out, as a pack's idle should
            _GUEST_MEMO["ok"] = False
        _GUEST_MEMO["t"] = now
    return bool(_GUEST_MEMO["ok"])


# THE GUEST HOLDS HER PLATE (the User 2026-10-13, .101 round 16: right after Lila's welcome the player went
# lila-listen-leave and Crissa's work / flourish loops for the whole This computer step, and Crissa was on screen for
# the her-voice step, before her intro). Lila's loop had nothing camped on it (Init cues it, and a cue clears the camp),
# so three wraps of lila-reg-listen (~30 s) found an empty bag on her hub and bring_home(by="wrap") walked her to the
# desk. While the guest's owner says she holds (in a pack, lib/crissa_init.lila_holding: a first boot's Setup with
# Lila on her plate) and the clip on is one of hers, nothing walks her home and no clip of another rack is queued over
# her; Setup's own forced plays (her lines, her leave, the landing) never ask. Unset (the house, a bench, tests): as
# before. Kill: CRISSA_LILA_HOLDS_PLATE=0 (read by lila_holding).
GUEST_HOLD: "Callable[[], bool] | None" = None
GUEST_HOLDS_WHY = "the guest host holds the screen (Setup)"


def _guest_holds(row: dict[str, Any] | None) -> bool:
    if GUEST_HOLD is None or str((row or {}).get("rack") or "") not in GUEST_RACKS:
        return False
    try:
        return bool(GUEST_HOLD())
    except Exception:  # noqa: BLE001  an unreadable owner holds nothing: the old walk
        return False


class Bag:
    """Shuffled draw without replacement, refilled when empty or when the pool
    changes. Never returns the same key twice running while an alternative
    exists."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._bag: list[str] = []
        self._of: tuple[str, ...] = ()
        self.last: str = ""

    @property
    def depth(self) -> int:
        return len(self._bag)

    def _fill(self, pool: Iterable[str]) -> None:
        self._bag = list(pool)
        self._rng.shuffle(self._bag)
        self._of = tuple(pool)

    def draw(self, pool: list[str], allowed: Callable[[str], bool]) -> str | None:
        """Next key from `pool` that `allowed` accepts, drawing without
        replacement. `allowed` is where the attach law bites: a key on another
        hub is skipped, not played, and stays in the bag for when she is
        standing somewhere it fits."""
        ok = [k for k in pool if allowed(k)]
        if not ok:
            return None                       # nothing legal here: stay put
        if tuple(pool) != self._of or not self._bag:
            self._fill(pool)
        for i, k in enumerate(self._bag):
            if k not in ok:
                continue                      # wrong hub right now
            if k == self.last and len(ok) > 1:
                continue                      # never twice running
            self._bag.pop(i)
            self.last = k
            return k
        # Everything still in the bag is illegal here. Refill and draw plainly
        # rather than stall — a still wall is worse than a repeat.
        self._fill(pool)
        for k in ok:
            if k != self.last:
                self.last = k
                return k
        self.last = ok[0]
        return ok[0]


class Graph:
    """The clip library as a graph of hubs. Answers one question: standing
    where she is, may this clip be played next?"""

    def __init__(self, clips: dict[str, Any], pools: dict[str, list[str]],
                 motion: dict[str, float] | None = None,
                 face: dict[str, float] | None = None,
                 child_paths: dict[str, Any] | None = None,
                 kit: dict[str, Any] | None = None) -> None:
        self.clips = clips
        self.pools = pools
        self.core_now: str | None = None      # the core loop she rests on now (core_rotate_on: chosen by the sequencer)
        self.roles: dict[str, str] = {}        # the worn library's named moments (MOMENT_ROLES), set by the service
        # p90 head travel in mouth-box widths/sec, and the fraction of frames
        # her face is in shot. Both measured from the sidecars by the service;
        # absent means "unknown", which must never exclude a clip.
        self.motion = motion or {}
        self.face = face or {}
        self.child_paths = child_paths or {}
        self.kit = kit if isinstance(kit, dict) else {}
        # the face's declared listening loop (manifest `listen_loop`), set by the player at boot; see rest_for
        self.listen_loop: str | None = None
        # the loop she rests on between lines (manifest `rest_loop`, round 39); she never talks on it, see rest_for
        self.rest_loop: str | None = None

    def parent_hub(self, hub: str | None) -> str | None:
        """Parent hub of a child path, or None if `hub` is a mode hub."""
        if not hub:
            return None
        for rec in (self.child_paths or {}).values():
            if not isinstance(rec, dict):
                continue
            if rec.get("hub") == hub:
                return rec.get("parent")
        return None

    def within(self, key: str, ceiling: float) -> bool:
        """Is this clip inside the register's movement ceiling. An unmeasured
        clip is allowed — a missing measurement is not evidence of anything."""
        v = self.motion.get(key)
        return True if v is None else v <= ceiling

    def row(self, key: str) -> dict[str, Any]:
        return self.clips.get(key) or {}

    def start(self, key: str) -> str | None:
        return self.row(key).get("start")

    def end(self, key: str) -> str | None:
        return self.row(key).get("end")

    def role(self, key: str) -> str:
        return str(self.row(key).get("role") or "")

    def kit_keys(self) -> frozenset[str] | None:
        """Temporary wall roster. None = the full element library.

        the User 2026-09-08: prove heads-down work + Hey Crissa FaceTime; park
        Imagine morphs and the rest until quality earns them back.
        """
        rec = self.kit
        if not rec.get("focus"):
            return None
        keys = rec.get("keys") or ()
        return frozenset(str(k) for k in keys) | frozenset(getattr(self, "_guest", ()))

    def admit(self, key: str) -> list[str]:
        """A HOST'S GUEST on the focused kit (the User 2026-09-09: Register ID
        hosted by the formal rest (listen / FT), which the
        working kit parks). The routine cues it `by: register`; the clip and
        the slides that touch its hub join the roster until `evict_guests()`
        — the everyday kit the User curated is untouched, and the bag never deals
        a guest on its own because it is gone again when the routine ends.
        Element and hubs still rule: nothing that cannot close on a hub gets in."""
        guest: set[str] = set(getattr(self, "_guest", set()))
        row = self.row(key)
        if not (bool(row.get("element")) and row.get("start") and row.get("end")):
            return []
        hubs = {str(row.get("start")), str(row.get("end"))}
        added = [key]
        for k, r in self.clips.items():
            if str(r.get("role") or "") != "slide" or not bool(r.get("element")):
                continue
            if str(r.get("start")) in hubs or str(r.get("end")) in hubs:
                added.append(k)
        guest.update(added)
        self._guest = guest
        return added

    def evict_guests(self) -> list[str]:
        gone = sorted(getattr(self, "_guest", set()))
        self._guest = set()
        return gone

    def compose_ok(self, key: str) -> bool:
        """May she be IN this clip at all. `element` is the checker's verdict —
        a clip that cannot close on a hub is not on the wall, whatever else is
        true of it — and both hubs must be named or there is no way to reason
        about entering or leaving.

        This is deliberately wider than `rollable`. Her rest (`work`), the
        slides (`wake` / `rest`) and the emote specialists are all
        `element: true, scheduled: false`: real, enterable, and not things the
        bag deals out. Conflating the two meant she chained flourish into
        flourish and could never reach her own desk — measured on the service
        2026-08-30, 13 switches with no rest between any of them."""
        row = self.row(key)
        if not (bool(row.get("element")) and bool(row.get("start")) and bool(row.get("end"))):
            return False
        allowed = self.kit_keys()
        if allowed is not None and key not in allowed:
            return False
        if GUEST_GATE is not None and str(row.get("rack") or "") in GUEST_RACKS and not _guest_allowed():
            return False
        return True

    def keepers(self) -> list[str]:
        """Spectacular takes kept off the wall. Not compose_ok — no hubs —
        so the bag and the brain never name them. Casual, when the wall
        lamp is sage."""
        return [k for k, row in self.clips.items() if row.get("keeper")]

    def category(self, key: str) -> str:
        """Formal (office register) or casual (Secure-room takes).

        Default is formal. Keepers are casual unless the row says otherwise.
        Casual is invisible and ineligible unless the wall lamp is sage."""
        row = self.row(key)
        cat = str(row.get("category") or "").strip().lower()
        if cat in ("casual", "formal"):
            return cat
        return "casual" if row.get("keeper") else "formal"

    def casual(self, key: str) -> bool:
        return self.category(key) == "casual"

    def casuals(self) -> list[str]:
        return [k for k in self.clips if self.casual(k)]

    def rollable(self, key: str) -> bool:
        """May the BAG deal this as a fidget. `scheduled` is the narrower gate:
        it means the checker passed it AND it is meant to come round on its
        own, rather than being played only when something asks for it."""
        return self.compose_ok(key) and bool(self.row(key).get("scheduled"))

    def hub_after(self, key: str) -> str | None:
        """Where she IS once this clip finishes — its END hub. Reading the
        start hub instead sent her home within a frame of every excursion
        (measured 2026-08-29), because a slide starts where it left."""
        return self.end(key) or self.start(key)

    def may_follow(self, key: str, here: str | None) -> bool:
        """Law 1. Slides cross hubs; that is their job. Everything else must
        already be standing where it begins.

        User-talk FT is a child of HOME. Hey Crissa is a camera cut onto
        `laptop-closeup-eyes-down` (already leaned in), not the lean-in
        slide. Formal sit-up still needs its own slide — do not jump `back`.
        """
        if key == USER_TALK_KEY:
            there = self.start(key)
            parent = self.parent_hub(there)
            if here is None or here == there or here == parent:
                return True
            return bool(self.path(here, parent or HOME))
        if is_ft_fade_dest(self, key) and here == self.start(USER_TALK_KEY):
            return True                   # fade to room, not push-back leave
        if not self.compose_ok(key):
            return False
        if self.role(key) == "slide":
            return True
        there = self.start(key)
        if here is None or there is None:
            return True                       # unknown hub must not block
        return here == there

    def slide_between(self, here: str | None, there: str | None) -> str | None:
        """The one legal way to cross. None means no bridge exists — and the
        honest answer is then to stay put, not to cut."""
        hops = self.path(here, there)
        return hops[0] if hops else None

    def path(self, here: str | None, there: str | None) -> list[str]:
        """Slide keys, in order, from `here` to `there`. Empty if already
        there or no compose_ok chain exists. One hop at a time is how
        take_cue walks it — this is the whole route."""
        if here is None or there is None or here == there:
            return []
        edges: dict[str, list[tuple[str, str]]] = {}
        for key in self.clips:
            if self.role(key) != "slide" or not self.compose_ok(key):
                continue
            a, b = self.start(key), self.end(key)
            if not a or not b or a == b:
                continue
            edges.setdefault(a, []).append((b, key))
        # SHARE GROUPS (the User 2026-09-13: "rotated equally ... in the same
        # equal share group"): slides on one hub pair that carry the same
        # `share` take turns — the edge offers only the variant whose turn it
        # is (turned() advances when one plays). No `share` = first-found, so
        # every library without the field routes exactly as before.
        turns = self.__dict__.get("_share_turn", {})
        for a, outs in edges.items():
            picked: list[tuple[str, str]] = []
            groups: dict[tuple[str, str], list[str]] = {}
            for b, key in outs:
                g = self.share(key)
                if g:
                    groups.setdefault((b, g), []).append(key)
                else:
                    picked.append((b, key))
            for (b, g), keys in groups.items():
                picked.append((b, keys[turns.get((a, b, g), 0) % len(keys)]))
            edges[a] = picked
        seen = {here}
        q: list[tuple[str, list[str]]] = [(here, [])]
        while q:
            at, hops = q.pop(0)
            for nxt, key in edges.get(at, []):
                if nxt in seen:
                    continue
                route = hops + [key]
                if nxt == there:
                    return route
                seen.add(nxt)
                q.append((nxt, route))
        return []

    def share(self, key: str) -> str:
        """The row's share group ("" when it has none)."""
        return str((self.clips.get(key) or {}).get("share") or "")

    def turned(self, key: str) -> None:
        """A share-group slide played: the next route on its hub pair takes
        the group's next variant."""
        g = self.share(key)
        if g:
            t = self.__dict__.setdefault("_share_turn", {})
            k = (self.start(key), self.end(key), g)
            t[k] = t.get(k, 0) + 1

    def resolve(self, key: str) -> str:
        """A moment key the worn library lacks -> its own clip for that role (MOMENT_ROLES / self.roles); else the key."""
        if not key or key in self.clips or not moment_roles_on():
            return key
        role = MOMENT_ROLES.get(key, "")
        # SECURE WEARS THE CASUAL TAKE (the User 2026-10-29: "Be sure Conductor gets Secure flag so it knows which
        # transitions/flourishes"): while the lamp is sage (apply_privacy sets `secure`), a role with a `<role>_casual`
        # clip in the library plays that one (v2's long-t-shirt walk-in / walk-out); amber keeps the formal one
        if getattr(self, "secure", False):
            casual = self.roles.get(role + "_casual", "") if role else ""
            if casual and casual in self.clips:
                return casual
        target = self.roles.get(role, "")
        return target if target and target in self.clips else key

    def core_set(self, hub: str | None, working: bool) -> list[str]:
        """THE CORE LOOPS OF A SET (the User 2026-10-29: "randomize the core hub players"; "paused" = working): the loops
        on this hub listed in the library's pools whose name says listen (working False) or work (working True), in
        pool order, each once. [] when the library names no such pool."""
        out: list[str] = []
        for name, keys in (self.pools or {}).items():
            n = str(name).lower()
            if ("work" in n) != bool(working) or not ("work" in n or "listen" in n):
                continue
            for k in keys or []:
                if k not in out and self._loop_on(k, hub):
                    out.append(k)
        return out

    def _loop_on(self, key: str, hub: str | None) -> bool:
        row = self.clips.get(key) or {}
        return bool(hub) and row.get("start") == hub and self.compose_ok(key) and self.role(key) == "loop"

    def talk_rest_for(self, hub: str | None) -> str | None:
        """Where she rests on this hub WHILE SHE SPEAKS: her listen loop (a still filmed mouth, the 09-27 law), else
        the hub's other loops with the rest loop skipped; never the rest loop when the hub has another (round 39)."""
        lk = self.listen_loop
        if lk and self._loop_on(lk, hub):
            return lk
        rk = self.rest_loop
        others = [k for k in self.clips if k != rk and self._loop_on(k, hub)]
        idle = [k for k in others if (self.clips.get(k) or {}).get("idle")]
        if idle or others:
            return (idle or others)[0]
        return self.rest_for(hub)

    def rest_for(self, hub: str | None) -> str | None:
        """Where she idles on this hub.

        Prefer a clip marked `idle: true` (the long home flourish, once it
        exists — it may be a flourish, not a loop). Else the first compose_ok
        loop on that hub. The mark is what stops `rest_for("back")` from
        silently becoming `listen` when HOME flips. No hub NAME is written
        down: `up` was renamed `back` mid-flight and every place that had
        hard-coded a name broke silently."""
        if hub is None:
            return None
        if self.core_now and self._loop_on(self.core_now, hub):
            return self.core_now                      # the core the sequencer chose (core_rotate_on)
        # THE DECLARED LISTENING LOOP IS ITS HUB'S REST (round 38, the User 2026-10-21: "add/swap the crissa desk loops
        # should work fine for listening"): a face whose manifest names its listen loop rests there on that hub, ahead
        # of an idle mark, so the kit's listen-warm is where she sits, not the old listen she would wrap back to. On
        # the house the declared loop IS the hub's idle rest (listen), so nothing moves. Kill CRISSA_LISTEN_LOOP_WINS=0.
        # THE REST LOOP IS WHERE SHE SITS BETWEEN LINES (round 39, .101's N1): a face that declares one apart from its
        # listen loop rests there; a line on the air goes to talk_rest_for (the listen loop), never this one.
        rk = self.rest_loop
        if rk and self._loop_on(rk, hub):
            return rk
        lk = self.listen_loop
        if lk and listen_loop_wins_on() and self._loop_on(lk, hub):
            return lk
        fallback = None
        for key, row in self.clips.items():
            if not self.compose_ok(key) or row.get("start") != hub:
                continue
            if row.get("idle"):
                return key
            if fallback is None and self.role(key) == "loop":
                fallback = key
        return fallback


def is_ft_fade_dest(graph: Graph, key: str) -> bool:
    """Webcam view dissolves to these — no leave slide."""
    rest = graph.rest_for(HOME) or "work"
    return key in (rest,) or key in WORK_GLANCE_ALIASES


class Sequencer:
    """What she does next. Holds no clock and touches no pixels — the service
    calls `wrapped()` when a loop comes round and `ended()` when a one-shot
    finishes, and asks `next_key()` for the answer."""

    def __init__(
        self,
        graph: Graph,
        pool_name: str = "work",
        rng: random.Random | None = None,
        brain: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.graph = graph
        self.pool_name = pool_name
        self.register = DEFAULT_REGISTER
        # User playlist. One cue used to overwrite; a 12 s TTL then ate a
        # four-clip stack before the third seam. Items expire only when ttl>0.
        self._q: list[dict[str, Any]] = []
        # STABLE ROW IDS, because the queue moves under the operator. `take_cue`
        # pops _q[0] at every seam, so between the render that drew a row at
        # index 3 and the drag that drops it at index 0, the head may already be
        # gone and every index has shifted by one. An index-addressed reorder
        # would then move the wrong clip — silently, and only sometimes. Ids are
        # minted once and never reused, so a stale click is a miss (safe) rather
        # than a hit on a neighbour (wrong). Duplicates are the whole point of a
        # playlist, so the key cannot be the identity.
        self._qseq = 0
        # WHAT SHE DID, in play order. The queue is the future and empties into
        # this; nothing reads it back to decide anything, so it stays a log and
        # not a second ledger. `_taken_*` carry one cue's provenance from the
        # seam that popped it to the `entered()` that records it.
        self.history: list[dict[str, Any]] = []
        self._taken_by: str | None = None
        self._taken_for: str | None = None
        # Playing, not yet history. the User: transfer to History once completed.
        self._pending_hist: dict[str, Any] | None = None
        self.bag = Bag(rng)
        self.brain = brain
        # The object behind the hook, when there is one, so she can be told
        # what she actually did. Without this she has no memory of her own
        # moves and repeats herself — measured on the service 2026-08-30:
        # flourish-shift twice inside a minute, because `recent` was empty.
        self.brain_obj = None
        self.wraps = 0
        # STAY (the User 2026-09-12: "I pick heads up and it loops to heads
        # down"): a forced play with stay=True camps her on that loop — the
        # bag draws nothing and home() is the camp — until the next play,
        # cue or bring_home clears it. For tuning a loop, not a law.
        self.camp: str | None = None
        # THE TOUR (the User 2026-09-13: "cycle through every loop from now on to
        # test all boxes and pins"): every loop, hub by hub, one pass + a dwell
        # each, crossing hubs on the slides. tour_hold() -> reason pauses it.
        self.tour: dict[str, Any] = {"on": False, "keys": [], "i": 0, "dwell_s": TOUR_DWELL_S,
                                     "since": None, "visited": 0, "skipped": [], "laps": 0}
        self.tour_hold = None
        self.now: str = ""
        self.reason: str = "boot"
        # Wall visit lamp. Sage = Secure = Casual visible and queueable.
        # Fail closed (not Secure) until the conductor's poll proves sage.
        self.private: bool = False
        # Hub she was on when a casual take started. Casual clips have no
        # attach hubs; without this, `here` goes None and the bag deals
        # every fidget. The lamp abort cuts back to this hub's rest.
        self._casual_from: str | None = None
        # Voice-the User at desk beam 6. Rising edge queues User-talk FT.
        # AUTH is not this latch — fail-AUTH speech stays the AUTH path.
        self._desk_user: bool = False
        self._greeted: bool = False
        self._greet_on_ft: bool = False
        # One-shot: Hey the User only after THIS vocative's lean-in, not a
        # leftover close-up the bag or a wrap plays later.
        self._ft_greet_from: str | None = None
        self._ft_greet_deadline: float = 0.0
        # Door-FOV face. None = not armed (first poll consumes without a glance).
        self._door_face_sig: str | None = None
        self._door_face_at: float = 0.0
        self._door_face_gone: tuple[str, float] | None = None   # (signature that left, when) - DOOR_FACE_SAME_S
        self._greeted_at: float = 0.0
        # The voice on the air (not a Composer viseme hold). Bag fidgets wait;
        # queued flourishes stay on the list until the line ends. An
        # in-progress flourish is cut back to the hub rest so visemes paint
        # (the User 2026-09-08: talking through a flourish used the filmed jaw).
        self.hold_flourishes: bool = False
        # Set by the service every frame: a conversation is on (listening or speaking). Read only under CONV_HOLD.
        self.conversation: bool = False
        # Non-speech mix. Speech never reads this (hold_flourishes wins).
        self.flourish_pct: int = FLOURISH_PCT_DEFAULT
        # In a call: the brain rotates the comms cores; the bag sits out.
        # Set by the vocative, cleared by bring_home / end_comms.
        self.comms: bool = False
        self.comms_cores: tuple[str, ...] = COMMS_CORES
        self._core_wraps: int = 0

    # ---- the insert lane -------------------------------------------------
    @property
    def cue(self) -> str | None:
        """Next in the playlist, or None. Kept as a name because /v1/state and
        the glass already print it."""
        return self._q[0]["key"] if self._q else None

    def queued(self) -> list[str]:
        return [row["key"] for row in self._q]

    def _queue(self, key: str, expires: float | None, front: bool = False,
               at: int | None = None, by: str = "user") -> dict:
        """Put one row on the shared list. WHO PUT IT THERE: there is exactly
        one queue — this list, on the one Sequencer — and the Composer, the
        wall and Crissa all edit it through the same two routes. A shared list
        has to say whose row is whose, or the operator cannot tell his own pick
        from hers. One queue, three hands: [[one-object-law]] applies here the
        way it does to the room."""
        self._qseq += 1
        row = {"id": "q%d" % self._qseq, "key": key, "expires": expires,
               "by": str(by or "user")}
        if at is not None:
            try:
                at_i = max(0, min(int(at), len(self._q)))
            except (TypeError, ValueError):
                at_i = len(self._q)
            self._q.insert(at_i, row)
        elif front:
            self._q.insert(0, row)
        else:
            self._q.append(row)
        return row

    def hub_at_tail(self) -> str | None:
        """Where she will be standing once the LAST queued row has played.

        Eligibility is asked of this, not of `here`. Validating against where
        she is standing *now* made the queue unable to say "bridge, then this":
        a desk flourish queued behind a `rest` is perfectly legal by the time
        it is reached, and refusing it at the door is what forced the operator
        to time his own clicks. the User, 2026-08-31: "on any cross selections the
        transition just attaches to it for bridge automatically." """
        hub = self.here
        for row in self._q:
            nxt = self.graph.hub_after(row.get("key"))
            if nxt:
                hub = nxt
        return hub

    def route(self, here: str | None, there: str | None) -> list[str]:
        """The rows that carry her from `here` to `there` (2026-09-17).

        `Graph.path` is slides only. The webcam view has none — Hey Crissa is a
        camera cut in and a fade to the room's rest loop out (`is_ft_fade_dest`)
        — so from `down_close` every slide and every other loop read "no way
        there", and a Composer camped on the lapcam showed a dead Transitions
        list. The fade IS the way out: from the webcam hub the first row is the
        parent's rest loop, then the slides from the parent."""
        if not here or not there or here == there:
            return []
        hops = self.graph.path(here, there)
        if hops or here != self.graph.start(USER_TALK_KEY):
            return hops
        parent = self.graph.parent_hub(here) or HOME
        rest = self.graph.rest_for(parent) or self.graph.rest_for(HOME) or "work"
        if not self.graph.compose_ok(rest):
            return []
        if there == parent:
            return [rest]
        onward = self.graph.path(parent, there)
        return [rest] + onward if onward else []

    def bridge_to(self, key: str) -> list[str]:
        """The slides that carry her from the queue's tail to this clip's hub.

        Empty when it already starts where the queue ends — and empty when no
        chain exists, which is the honest "cannot get there from here"."""
        there = self.graph.start(key)
        tail = self.hub_at_tail()
        if not there or not tail or there == tail:
            return []
        if key == USER_TALK_KEY:
            parent = self.graph.parent_hub(there) or HOME
            if tail == parent:
                return []
            return self.graph.path(tail, parent)
        if is_ft_fade_dest(self.graph, key) and tail == self.graph.start(USER_TALK_KEY):
            return []                     # fade to room
        return self.route(tail, there)

    def can_take(self, key: str, at_hub: str | None = None) -> dict:
        """May she honour this cue from where she stands — the ONE rule.

        `insert()` enforces it and `/v1/kit` reads it, so a button the Composer
        offers is a cue the Sequencer will accept. the User, 2026-08-31: "As it is
        I can start one from either set and they jump frames." The menu was
        filtering on `compose_ok` alone — is this clip on the wall at all —
        which is a property of the CLIP, not of where she is sitting. Two
        copies of the rule is how the menu and the gate drift apart; this is
        the copy.

        TWO KINDS OF INSERT. Grok, 2026-08-30: "A face does not scoot the
        chair."

          TRAVEL — a loop or a slide. Rush back, sit back to present.
          GESTURE — a flourish, a glance, a one-shot. It belongs to the hub it
          was filmed on. Asking for an emote while she is across the room is
          no, not a walk over there to make the face.

        NO going_home BYPASS. With HOME == "down" every desk gesture is "going
        home", so that gate stopped firing and `emote-woe` from `back` came
        back taken. Home-start flourishes wait for the plate and the idle clip
        (docs/POSE-ONE-FRAME-OR-NONE.md). Loops and slides are already exempt,
        so bring_home() still travels: it inserts a rest LOOP, not a gesture.

        `hops` is the slides she must cross first, in order — empty when the
        clip starts where she already is. The menu says "travels" with it, so a
        cross-hub loop stops being a button that silently starts a journey."""
        key = self.graph.resolve(key)
        if key != USER_TALK_KEY and self.graph.casual(key) and not self.private:
            return {"ok": False, "why": "%s is casual; the room is not Secure" % key,
                    "hops": []}
        if key == USER_TALK_KEY:
            there = self.graph.start(key)
            parent = self.graph.parent_hub(there) or HOME
            here = self.hub_at_tail() if at_hub is None else at_hub
            if here is None or here == there or here == parent:
                # Camera cut from the desk (parent) — not the lean-in slide.
                return {"ok": True, "why": "", "hops": []}
            hops = self.graph.path(here, there)
            if not hops and here != parent:
                hops = self.graph.path(here, parent)
            if hops and (self.private or not any(self.graph.casual(h) for h in hops)):
                return {"ok": True, "why": "", "hops": hops}
            if hops:
                return {"ok": False, "hops": [],
                        "why": "the scoot is casual; Formal/Conference needs its own slide"}
            return {"ok": False, "hops": [],
                    "why": "%s is laptop-talking; no slide from %s" % (key, here)}
        here = self.hub_at_tail() if at_hub is None else at_hub
        if here == self.graph.start(USER_TALK_KEY) and is_ft_fade_dest(self.graph, key):
            rest = self.graph.rest_for(HOME) or "work"
            if key == rest or self.graph.compose_ok(key):
                return {"ok": True, "why": "", "hops": []}
        if not self.graph.compose_ok(key):
            # Keepers and other casual oneshots have no hubs. Private
            # already passed; they queue without a slide.
            if self.graph.casual(key):
                return {"ok": True, "why": "", "hops": []}
            return {"ok": False, "why": "%s is not on the wall" % key, "hops": []}
        there = self.graph.start(key)
        here = self.hub_at_tail() if at_hub is None else at_hub
        if here and there and here != there:
            role = self.graph.role(key)
            if role not in ("loop", "slide"):
                return {"ok": False, "hops": [],
                        "why": "%s is a gesture on %s; she is on %s and a face "
                               "does not scoot the chair" % (key, there, here)}
            hops = self.route(here, there)
            if not hops:
                return {"ok": False, "hops": [],
                        "why": "%s lives on %s and there is no slide from %s" % (key, there, here)}
            if not self.private and any(self.graph.casual(h) for h in hops):
                return {"ok": False, "hops": [],
                        "why": "the scoot is casual; the room is not Secure"}
            return {"ok": True, "why": "", "hops": hops}
        return {"ok": True, "why": "", "hops": []}

    def insert(self, key: str, now: float, ttl: float = 12.0,
               front: bool = False, by: str = "user",
               at: int | None = None, bridge: bool = False) -> dict:
        """Ad hoc, into the running feed. the User, 2026-08-30: "Emote on demand?
        ... Insert into sequence ad hoc."

        It does NOT cut. A cue waits for the next seam — the end of the gesture
        she is in, or the wrap of the loop she is resting on — because cutting
        a gesture to be expressive is the thing that reads as a glitch, and an
        emote that lands a beat late still lands. If it cannot be reached
        before `ttl` it is dropped and said so: a surprise that arrives twelve
        seconds after the line is worse than no surprise.

        ttl<=0 means it stays until played or deleted — a user playlist of
        fidgets cannot use the 12 s emote TTL or the third clip expires on
        the chair.

        Returns what will happen, never a bare ok — a cue that is going to be
        dropped must say so at the door, not silently."""
        key = self.graph.resolve(key)
        if (_guest_holds(self.graph.row(self.now))
                and str((self.graph.row(key) or {}).get("rack") or "") not in GUEST_RACKS):
            return {"taken": False, "why": GUEST_HOLDS_WHY}      # THE GUEST HOLDS HER PLATE (above)
        tail = self.hub_at_tail()
        verdict = self.can_take(key, at_hub=tail)
        bridged: list[str] = []
        if not verdict["ok"] and bridge and self.graph.compose_ok(key):
            # THE BRIDGE IS PART OF THE PICK. A gesture still does not chase her
            # across the room on its own — that law is intact — but the operator
            # asking for one from the other set is not a chase: he is asking for
            # the crossing AND the clip, in that order, and the queue is the
            # right place to say so. The hops go in as their own rows so he can
            # see the scoot sitting in front of his flourish, and delete it.
            hops = self.route(tail, self.graph.start(key))
            for h in hops:
                self._queue(h, None, front=front, at=at)
                bridged.append(h)
                if at is not None:
                    at = int(at) + 1
            if hops:
                verdict = {"ok": True, "why": "", "hops": []}
        if not verdict["ok"]:
            return {"taken": False, "why": verdict["why"]}
        expires = None if ttl is None or ttl <= 0 else now + float(ttl)
        # WHO PUT IT THERE. There is exactly one queue — this list, on the one
        # Sequencer — and the Composer, the wall and Crissa all edit it through
        # the same two routes. A shared list has to say whose row is whose, or
        # the operator cannot tell his own pick from hers. One queue, three
        # hands: [[one-object-law]] applies here the way it does to the room.
        row = self._queue(key, expires, front=front, at=at, by=by)
        at_out = self.index_of(row["id"])
        if at_out == 0:
            when = "now" if self.at_seam() else "at the next seam"
        else:
            when = "queued"
        return {"taken": True, "key": key, "when": when,
                "id": row["id"],
                "bridged": bridged,
                "at": at_out,
                "queue": self.queued(), "queue_rows": self.queue_rows(now),
                "expires_in": None if expires is None else round(float(ttl), 1)}

    def cue_vocative_from_work(self, now: float, *,
                               auth_held: bool = False) -> dict:
        """Hey Crissa from work: 0.6 s connection, then the webcam loop.

        Interference first (camera switch), then she is already leaned in
        (`laptop-closeup-eyes-down`). No lean-in slide, no spoken Connecting.
        Greet when that loop starts.
        """
        if self.here and self.here.startswith("lila_"):
            # Lila has the glass (Render): no FaceTime lean-in, she answers
            # on the listening loop she is already on.
            return {"taken": False, "stay": True,
                    "why": "Lila has the glass — she stays on her listen loop (here=%s)" % self.here,
                    "queued": self.queued()}
        if not (self.graph.clips.get(self.graph.resolve(USER_TALK_KEY)) or {}).get("file"):
            # NO CLOSE-UP IN THIS LIBRARY, NO CAMERA CUT (the User's walk of cut 13, 2026-10-29: "I said Crissa and we got
            # the scripted interference but no reply. We dont want nor do we have the upclose now"): v2 names no close
            # role, so the cut played its static over nothing and the wake's "Yeah?", deferred to the close-up's start,
            # was never said. Not taken = the ear says it at once and she answers where she is.
            # HEADS UP ON THE WAKE (the same walk: "not even heads up transition when hey crissa. need that trigger in
            # conductor to fire"): on a library whose core sets say listen vs work, the wake takes her from a work loop
            # (heads down) or a flourish onto a listen loop (eyes up at the screen's edge) now - the player plays it -
            # and she stays there while the conversation lasts (core_turn keeps her in the listen set)
            out = {"taken": False, "stay": True, "why": "no close-up in this library: she answers where she is",
                   "queued": self.queued()}
            cores = self.graph.core_set(self.here, working=False) if core_rotate_on() else []
            if cores and self.now not in cores:
                pick = random.choice(cores)
                self.graph.core_now = pick
                out["listen_now"] = pick
            return out
        sitting = (USER_TALK_KEY,)
        if self.now in sitting:
            return {"taken": False, "why": "already in FT",
                    "queued": self.queued()}
        if any(k in self.queued() for k in sitting):
            qk = next(k for k in sitting if k in self.queued())
            return {"taken": True, "why": "already queued", "when": "queued",
                    "key": qk, "queued": self.queued()}
        if self.here not in (None, "down", "down_close"):
            return {"taken": False,
                    "why": "Hey %s FaceTime is from work/down (here=%s)" % (_her_name(), self.here),
                    "queued": self.queued()}
        r = self.insert(USER_TALK_KEY, now, ttl=0, front=True, by="vocative")
        if r.get("taken"):
            self.comms = True
            self._core_wraps = 0
            self.arm_ft_greet(now, from_enter=False)
            r["key"] = USER_TALK_KEY
            r["cut_s"] = USER_TALK_CUT_S
            r["queue"] = self.queued()
        return r

    def end_comms(self, why: str = "session end") -> None:
        """The call is over: the bag deals again and home() may walk her back."""
        if self.comms:
            self.reason = "comms off: %s" % why
        self.comms = False
        self._core_wraps = 0

    def comms_offer(self) -> list[str]:
        """The cores she may sit on for the call, from where she is: on the
        wall (compose_ok), not casual under amber, and reachable — same hub or a
        slide path. The FT loop counts as a core so comms can start there."""
        here = self.here
        if here and here.startswith("lila_"):
            # Lila has the glass (Render, the User 2026-09-13): her listen loop is
            # the core; every one of Crissa's is a slide away from her plate.
            return [k for k in LILA_COMMS_CORES if k in self.graph.clips]
        out: list[str] = []
        for k in self.comms_cores:
            if k not in self.graph.clips or not self.graph.compose_ok(k):
                continue
            if self.graph.casual(k) and not self.private:
                continue
            there = self.graph.start(k)
            if here is not None and there and there != here and not self.graph.path(here, there):
                continue
            out.append(k)
        return out

    def arm_ft_greet(self, now: float, *, from_enter: bool = True) -> None:
        """Hey the User is allowed once, until deadline, after this vocative."""
        self._greet_on_ft = True
        self._ft_greet_from = USER_TALK_ENTER if from_enter else None
        self._ft_greet_deadline = float(now) + FT_GREET_S

    def abandon_ft(self, *, drop_queue: bool = True) -> None:
        """Leave the FaceTime path. A leftover flag must not greet later."""
        self._greet_on_ft = False
        self._ft_greet_from = None
        self._ft_greet_deadline = 0.0
        if drop_queue:
            sitting = set(FT_SITTING)
            self._q = [row for row in self._q if row.get("key") not in sitting]

    def take_ft_greet(self, now: float, key: str, prev: str | None) -> bool:
        """True once: close-up land of THIS vocative, after the 0.6 s connection.

        A wrap of the close-up, a hop that skipped enter, a leftover flag
        after she went back to work, or a cue older than ``FT_GREET_S``
        must not speak. the User 2026-09-08: Hey the User still random fires.
        """
        if key == USER_TALK_ENTER:
            return False
        if key != USER_TALK_KEY:
            self.abandon_ft(drop_queue=True)
            return False
        armed = self._greet_on_ft
        need = self._ft_greet_from
        deadline = self._ft_greet_deadline
        self.abandon_ft(drop_queue=False)
        if not armed:
            return False
        if prev == USER_TALK_KEY:
            return False
        if deadline and now > deadline:
            return False
        if need is not None and prev != need:
            return False
        return True

    def note_desk_user(self, now: float, *, speaker: dict | None,
                       sound: dict | None, session_open: bool = False) -> dict:
        """The User's voice at the desk does not lean-in and does not greet.

        The User 2026-09-08: she still greeted him by name. FaceTime is Hey Crissa
        (``cue_vocative_from_work``), not a stale voice stamp + beam 6.
        """
        return {"queued": False, "greet": False, "why": "hey-crissa owns FT"}

    def note_door_face(self, now: float, *, presence: dict | None) -> dict:
        """Cue the hub's motion door look when a face appears. Does not cut.

        Identity-blind — do not wait for a face-ID lock. ``down`` →
        ``work-door``. ``back`` → ``listen-door`` once that clip is Law 1
        on ``back`` (not the linger fidget, not hub ``up``). Vocative
        already queued owns the body. First poll arms without glancing
        so a restart does not fire on whoever is already in frame.
        """
        sig = door_face_sig(presence)
        if self._door_face_sig is None:
            self._door_face_sig = sig
            return {"queued": False, "why": "armed", "sig": sig}
        if not sig:
            if DOOR_FACE_SAME_S > 0 and self._door_face_sig:
                self._door_face_gone = (self._door_face_sig, now)
            self._door_face_sig = ""
            return {"queued": False, "why": "no door face"}
        if sig == self._door_face_sig:
            return {"queued": False, "why": "same door face", "sig": sig}
        gone = self._door_face_gone
        if DOOR_FACE_SAME_S > 0 and gone and sig == gone[0] and (now - gone[1]) < DOOR_FACE_SAME_S:
            # the same face blinked out and back (turned away, a missed detection): not a new arrival
            self._door_face_sig = sig
            return {"queued": False, "why": "same door face (back within %gs)" % DOOR_FACE_SAME_S, "sig": sig}
        sitting = (VOCATIVE_KEY, VOCATIVE_FAMILIAR, USER_TALK_KEY, USER_TALK_ENTER)
        if self.now in sitting or any(k in self.queued() for k in sitting):
            self._door_face_sig = sig
            return {"queued": False, "why": "vocative owns the body", "sig": sig}
        glancing = tuple(DOOR_LOOK_KEYS - {DOOR_FIDGET_KEY})
        if self.now in glancing or any(k in self.queued() for k in glancing):
            return {"queued": False, "why": "already glancing", "sig": sig}
        key = door_glance_key(self.graph, self.here)
        if not key:
            return {"queued": False,
                    "why": "no door glance on %s" % self.here,
                    "sig": sig}
        if self._door_face_at and (now - self._door_face_at) < 8.0:
            return {"queued": False, "why": "door glance cooling", "sig": sig}
        r = self.insert(key, now, ttl=0, front=True, by="door-face")
        if r.get("taken"):
            self._door_face_sig = sig
            self._door_face_at = now
        return {"queued": bool(r.get("taken")),
                "key": key if r.get("taken") else None,
                "when": r.get("when"), "why": r.get("why") or "",
                "sig": sig, "insert": r}

    def queue_rows(self, now: float | None = None) -> list[dict]:
        """The playlist with its identities and clocks, for an operator UI.

        `queued()` stays a list of keys — /v1/state, the glass and the tests all
        read it and a playlist of names is the right shape for them. This is the
        same list with the two things a UI cannot reconstruct: which row is
        which (duplicates share a key) and how long each has before it expires.
        """
        t = time.time() if now is None else now
        out = []
        for row in self._q:
            exp = row.get("expires")
            out.append({
                "id": row.get("id"),
                "key": row["key"],
                "by": row.get("by") or "user",
                "expires_in": None if exp is None else round(exp - t, 1),
            })
        return out

    def index_of(self, item_id: str) -> int:
        for i, row in enumerate(self._q):
            if row.get("id") == item_id:
                return i
        return -1

    def move(self, item_id: str, to: int) -> dict:
        """Reorder by id. `to` is the destination index in the list AFTER the
        row is lifted out, which is what a drag actually means.

        A move that names a row already gone (played at a seam while the drag
        was in flight) is a no-op that still returns the list — the operator
        dragged something real, it simply landed first. Do not 404 a race.
        """
        i = self.index_of(item_id)
        if i < 0:
            return {"ok": False, "why": "%s already played or was removed" % item_id,
                    "queue": self.queued(), "queue_rows": self.queue_rows()}
        row = self._q.pop(i)
        try:
            to = int(to)
        except (TypeError, ValueError):
            to = i
        to = max(0, min(to, len(self._q)))
        self._q.insert(to, row)
        return {"ok": True, "moved": row["key"], "id": item_id, "at": to,
                "queue": self.queued(), "queue_rows": self.queue_rows()}

    def drop_id(self, item_id: str) -> dict:
        """Remove by id. Same race rule as move: a miss is not an error."""
        i = self.index_of(item_id)
        if i < 0:
            return {"ok": False, "why": "%s already played or was removed" % item_id,
                    "queue": self.queued(), "queue_rows": self.queue_rows()}
        gone = self._q.pop(i)
        return {"ok": True, "dropped": gone["key"], "id": item_id,
                "queue": self.queued(), "queue_rows": self.queue_rows()}

    def drop(self, at: int) -> dict:
        """Pull one item out of the playlist. Missing index is a no-op that
        still returns the list — the wall should not 404 a click."""
        if 0 <= at < len(self._q):
            gone = self._q.pop(at)
            return {"ok": True, "dropped": gone["key"], "id": gone.get("id"),
                    "queue": self.queued(), "queue_rows": self.queue_rows()}
        return {"ok": False, "why": "nothing at %s" % at,
                "queue": self.queued(), "queue_rows": self.queue_rows()}

    def clear_queue(self) -> dict:
        self._q.clear()
        return {"ok": True, "queue": [], "queue_rows": []}

    def at_seam(self) -> bool:
        """A seam is the only place an insert may land: she is resting, or the
        gesture she was doing has finished."""
        return self.graph.role(self.now) == "loop"

    def take_cue(self, now: float) -> str | None:
        """What the service asks at every seam. Hands back the next queued
        key, or the slide that reaches it, or nothing."""
        while self._q:
            head = self._q[0]
            exp = head.get("expires")
            if exp is not None and now > exp:
                self._q.pop(0)
                self.reason = "cue expired before a seam"
                continue
            key, here = head["key"], self.here
            if self.hold_flourishes and not self.speech_ok(key):
                # Leave it queued. A yawn over the voice painted a mouth through
                # the covering hand (the User 2026-09-07).
                self.reason = "holding flourish (on air)"
                return None
            if self.conversation_holds(key):
                # Leave it queued: it plays at the first seam once comms pause (2026-10-01).
                self.reason = "holding flourish (in conversation)"
                return None
            if key != USER_TALK_KEY and self.graph.casual(key) and not self.private:
                self._q.pop(0)
                self.reason = "casual cancelled: room is not Secure"
                continue
            there = self.graph.start(key)
            # Fade back to the room from the webcam view — not push-back leave.
            ft_hub = self.graph.start(USER_TALK_KEY)
            if is_ft_fade_dest(self.graph, key) and here == ft_hub:
                self._q.pop(0)
                self.reason = "cue: %s (fade to room)" % key
                self._taken_by, self._taken_for = head.get("by"), None
                return key
            if here and there and here != there:
                # DROP BEFORE YOU WALK. She moved between the ask and the seam.
                # A gesture belongs to the hub it was filmed on and does not
                # chase her across the room, so it is dropped HERE — before
                # path() is consulted. Checking hops first handed back `rest`
                # for an `emote-woe` queued from `back`: the walk to the desk
                # to make a face, which is the defect this order prevents.
                if key == USER_TALK_KEY:
                    parent = self.graph.parent_hub(there) or HOME
                    if here == parent:
                        # Camera cut — she is already leaned in. No enter slide.
                        self._q.pop(0)
                        self.reason = "cue: %s (webcam cut)" % key
                        self._taken_by, self._taken_for = head.get("by"), None
                        return key
                    hops = self.graph.path(here, there)
                    if not hops:
                        hops = self.graph.path(here, parent)
                    if hops and (self.private or not any(self.graph.casual(h) for h in hops)):
                        self.reason = "crossing to %s for %s" % (there, key)
                        self._taken_by, self._taken_for = head.get("by"), key
                        return hops[0]
                    self._q.pop(0)
                    self.reason = "cue stranded: no slide to %s for laptop-talk" % parent
                    continue
                if self.graph.role(key) not in ("loop", "slide"):
                    self._q.pop(0)
                    self.reason = "cue dropped: %s is a gesture on %s, she is on %s" % (key, there, here)
                    continue
                hops = self.route(here, there)
                if hops:
                    # One hop. The cue stays pending — next seam walks the rest.
                    # Loops and slides travel; that is what they are for.
                    self.reason = "crossing to %s for %s" % (there, key)
                    # The hop is on the hand that asked, not on her: history
                    # should not read as though she wandered off on her own.
                    self._taken_by, self._taken_for = head.get("by"), key
                    return hops[0]
                self._q.pop(0)
                self.reason = "cue stranded: no slide to %s" % there
                continue
            self._q.pop(0)
            self.reason = "cue: %s" % key
            self._taken_by, self._taken_for = head.get("by"), None
            return key
        return None

    # ---- what the service tells us -------------------------------------
    def drop_casual(self) -> list[str]:
        """Pull every casual row. No wind-down. Returns the dropped keys."""
        kept: list[dict[str, Any]] = []
        gone: list[str] = []
        for row in self._q:
            if self.graph.casual(row["key"]):
                gone.append(row["key"])
            else:
                kept.append(row)
        self._q = kept
        if gone:
            self.reason = "casual cancelled: room is not Secure"
        return gone

    def cut_next(self) -> str | None:
        """Next queued key, no attach walk. Casual abort is a cut, not a slide."""
        while self._q:
            key = self._q[0]["key"]
            if self.graph.casual(key) and not self.private:
                self._q.pop(0)
                continue
            row = self._q.pop(0)
            self._taken_by, self._taken_for = row.get("by"), None
            self.reason = "cut: %s" % key
            return key
        return None

    def entered(self, key: str, at: float | None = None) -> None:
        """The service says a clip is on. History does not get it yet.

        A row that came off the queue carries the hand that put it there;
        anything else is hers. `for` marks a travel slide with the clip it
        is crossing toward. `finished()` is what actually appends."""
        prev_hub = self.here
        if self.graph.casual(key):
            self._casual_from = prev_hub
        else:
            self._casual_from = None
        self.now = key
        self.wraps = 0
        if self.tour.get("on") and self._tour_target() == key:
            self.tour["since"] = time.time() if at is None else float(at)
        if self.graph.role(key) == "slide":
            self.graph.turned(key)
        if key in self.comms_cores:
            self._core_wraps = 0
        self._pending_hist = {
            "key": key,
            "by": self._taken_by if self._taken_by is not None else "crissa",
            "for": self._taken_for,
        }
        self._taken_by = self._taken_for = None
        saw = getattr(self.brain_obj, "saw", None)
        if callable(saw):
            saw(key)

    def finished(self, at: float | None = None) -> None:
        """The clip that was on has run out. This is the History write.

        Every completed clip, in play order: user cues, her bag draws, and
        travel slides. A loop that only wraps is still the same clip — it
        is not a completion. `at` is when it ended."""
        row = self._pending_hist
        if not row:
            return
        self.history.append({
            "key": row["key"],
            "by": row.get("by") or "crissa",
            "at": at,
            "for": row.get("for"),
        })
        del self.history[:-HISTORY_CAP]
        self._pending_hist = None

    def history_rows(self, limit: int = HISTORY_CAP) -> list[dict]:
        """What played, most recent first. A log of the past, not a second
        queue: nothing reads it back to decide anything."""
        rows = self.history[-int(max(0, limit)):]
        return [dict(r) for r in reversed(rows)]

    def wrapped(self) -> None:
        self._core_wraps += 1
        self._wrapped_inner()

    def _wrapped_inner(self) -> None:
        self.wraps += 1

    # ---- what the service asks ------------------------------------------
    @property
    def here(self) -> str | None:
        # Hub-legal clips (wake, listen) still name their end hub.
        # Keepers have none — keep the hub she left so the bag does not empty.
        if self.now:
            hub = self.graph.hub_after(self.now)
            if hub:
                return hub
            if self.graph.casual(self.now):
                return self._casual_from
        return None

    def warm_swap(self) -> str | None:
        """The rest loop's cap (WARM_MAX_PLAYS): after that many plays her listen loop, after WARM_NEUTRAL_PLAYS of that
        the rest loop again. None = no swap due, or no rest loop declared, or she is speaking."""
        rk, here = self.graph.rest_loop, self.here
        if not rk or self.hold_flourishes or not self.now:
            return None
        if warm_cap_goes_home_on() and not self.conversation and here != HOME:
            return None                                   # paused: the way home wins (round 42, .101's R41-1)
        talk = self.graph.talk_rest_for(here)
        if not talk or talk == rk:
            return None
        if self.now == rk and self.wraps >= WARM_MAX_PLAYS:
            return talk
        if self.now == talk and self.wraps >= WARM_NEUTRAL_PLAYS and self.graph._loop_on(rk, here):
            return rk
        return None

    def conversation_holds(self, key: str) -> bool:
        """Is this a NEW flourish/glance pick that waits for comms to pause? (CONV_HOLD, 2026-10-01)"""
        if not (CONV_HOLD and self.conversation):
            return False
        # the worn library's own clip for each counts too (Fable's review of cut 13: v2's door glance is v2-fl-turn-door)
        if key in (WORK_GLANCE_KEY, self.graph.resolve(WORK_GLANCE_KEY)):    # the tool cut: multitasking during comms
            return False
        if key in (DOOR_GLANCE_KEY, self.graph.resolve(DOOR_GLANCE_KEY)):    # the door: she may look up mid-talk (10-01)
            return False
        return self.graph.role(key) in ("flourish", "glance")

    def due(self) -> bool:
        """A rest loop holds for the mix's wraps; anything else runs to its
        own end and the service asks then. Speech freezes this clock unless a flourish here
        may play during speech."""
        if self.hold_flourishes and not (FLOURISH_SPEECH and self.pool()):
            return False
        if self.warm_swap():
            return True
        if CONV_HOLD and self.conversation and self._core_turn_due():
            return True                                   # a core loop's turn in a conversation: another core, no flourish
        if CONV_HOLD and self.conversation:
            self.reason = "holding flourish (in conversation)"
            return False
        if self.graph.role(self.now) != "loop":
            return False
        n = wraps_before_fidget(self.flourish_pct)
        if n is None:
            return self._core_turn_due()                  # Flourish 0 %: no flourish, but her core loops still turn
        return self.wraps >= n

    def speech_ok(self, key: str) -> bool:
        """May this clip start, or keep playing, while she speaks?"""
        if self.graph.role(key) in ("loop", "slide"):
            return True
        return FLOURISH_SPEECH and (self.graph.row(key) or {}).get("speech") is not False and not speech_held(key)

    def pool(self) -> list[str]:
        """What the bag may deal WHERE SHE IS STANDING, narrowed to the register.

        THE POOL FOLLOWS THE HUB. It used to be one fixed name (`work`), which
        is only right while she is at the desk: measured 2026-08-30, standing on
        `back` gave 6 clips of which 0 were legal and `next_key` returned None
        for ever — she would have sat at the pushed-back rest doing nothing,
        and the flourish that lives there could never play.

        The hub is read from the clips, never from a pool's name. Pools are a
        flavour list, not a graph, and `up` was renamed `back` mid-flight once
        already — anything holding a hub name breaks silently when that
        happens. Any pool Grok adds files itself.

        If a register empties the pool it is ignored rather than obeyed —
        holding still is a choice, but standing frozen because a ceiling was set
        too low is a bug wearing a choice's clothes."""
        here = self.here
        if self.comms:
            return []          # a call: she talks in loops, the bag sits out
        rolls = []
        for keys in self.graph.pools.values():
            for k in keys:
                if k in rolls or not self.graph.rollable(k):
                    continue
                if self.graph.casual(k) and not self.private:
                    continue
                if here is None or self.graph.start(k) == here:
                    rolls.append(k)
        if self.hold_flourishes:
            rolls = [k for k in rolls if self.speech_ok(k)]
        ceil = REGISTERS.get(self.register, REGISTERS[DEFAULT_REGISTER])
        held = [k for k in rolls if self.graph.within(k, ceil)]
        return held or rolls

    def offer(self) -> list[str]:
        """What the BRAIN may name from here. Wider than `pool()`.

        The bag still draws only `scheduled` fidgets. She may name a hub-legal
        flourish or rest the bag would not deal (soften, an emote on this hub).
        Slides stay off this list — talk does not request wake. Law 1 still
        vetoes the pick.
        """
        here = self.here
        if self.comms:
            return self.comms_offer()
        keys: list[str] = []
        for k, row in self.graph.clips.items():
            if not self.graph.compose_ok(k):
                continue
            if self.graph.casual(k) and not self.private:
                continue
            if self.graph.role(k) in ("slide", "glance"):
                continue
            if k in DOOR_LOOK_KEYS:
                continue
            if k in FT_DEMAND:
                continue
            if here is not None and self.graph.start(k) != here:
                continue
            keys.append(k)
        ceil = REGISTERS.get(self.register, REGISTERS[DEFAULT_REGISTER])
        held = [k for k in keys if self.graph.within(k, ceil)]
        return held or keys

    def next_key(self, context: dict[str, Any] | None = None,
                 now: float | None = None) -> str | None:
        """A cue jumps the queue — that is what on demand means. Everything
        after this is the ordinary roll.

        A playlist in flight is not a suggestion. `next_key()` used to skip
        `take_cue` when `now` was omitted, so the bag could draw over a stack
        the operator had already queued. If anything is queued, consume it
        first; if it cannot land this seam, do not roll the bag over it.
        """
        if now is None and self._q:
            now = time.time()
        if now is not None:
            cued = self.take_cue(now)
            if cued:
                return cued
        if self._q:
            return None
        w = self.warm_swap()
        if w:
            self.reason = "rest loop cap (%d plays)" % (WARM_MAX_PLAYS if w != self.graph.rest_loop else WARM_NEUTRAL_PLAYS)
            return w
        c = self.core_turn()
        if c:
            return c
        return self._roll(context)

    def core_pick(self) -> str | None:
        """A random core loop of the set that fits now (listen in a conversation, work when paused), another than the one
        playing when the set has more; it becomes the hub's rest (Graph.core_now). None = no sets / switched off."""
        if not core_rotate_on() or self.camp:
            return None
        cores = self.graph.core_set(self.here, working=not self.conversation)
        if not cores:
            return None
        k = random.choice([c for c in cores if c != self.now] or cores)
        self.graph.core_now = k
        return k

    def _core_turn_due(self) -> bool:
        """A core loop has had its turns and its set has another to go to."""
        if not core_rotate_on() or not self.now or self.graph.role(self.now) != "loop":
            return False
        n = wraps_before_fidget(self.flourish_pct)
        if n is None:          # Flourish 0 % holds flourishes, not her core loops (the 10-29 review): the dial's default pace
            n = wraps_before_fidget(WALL_PREF_DEFAULTS.get("flourish_pct"))
        cores = self.graph.core_set(self.here, working=not self.conversation)
        return n is not None and self.wraps >= n and (len(cores) > 1 or (bool(cores) and self.now not in cores))

    def core_turn(self) -> str | None:
        """At a core loop's turn: a loop outside the set that fits changes at once (paused -> the work set); else a
        change of core on CORE_ROTATE_P of the turns, the rest go to the flourish roll."""
        if not core_rotate_on() or self.hold_flourishes or not self.now or self.graph.role(self.now) != "loop":
            return None
        cores = self.graph.core_set(self.here, working=not self.conversation)
        if not cores:
            return None
        talking = bool(CONV_HOLD and self.conversation)     # a conversation holds flourishes: its turns change core
        if self.now not in cores or talking or random.random() < core_rotate_p():
            k = self.core_pick()
            if k and k != self.now:
                self.reason = "core rotate (%s set)" % ("listen" if self.conversation else "work")
                return k
        return None

    def rest_while_speaking(self, key: str) -> str | None:
        """Hub rest loop if `key` is a gesture that cannot take viseme paste.

        Slides stay (vocative rise keeps the filmed jaw). Yawn/notes cover
        the mouth — do not paste, do not keep talking on that clip. Land
        on the rest loop so Law 3 mouths paint.

        Harvest / Tune-stay oneshots stay put (the User 2026-09-16: Say on a
        close harvest must not snap to the heads-down rest / first drill).
        Covering-hand is flourish and glance only.
        """
        if not self.hold_flourishes:
            return None
        if self.camp:
            return None
        if key and key == self.graph.rest_loop:
            # round 39 (.101's N1): she never talks on her rest loop - its filmed smile showed through every pass
            # the paint could not keep up with; the line goes to her listen loop on the same hub
            talk = self.graph.talk_rest_for(self.graph.start(key))
            return talk if talk and talk != key else None
        row = self.graph.row(key) or {}
        if str(row.get("rack") or "").strip().lower() == "harvest":
            return None
        if self.graph.role(key) not in ("flourish", "glance"):
            return None
        if self.speech_ok(key):
            return None
        rest = self.graph.talk_rest_for(self.here) or self.graph.talk_rest_for(HOME)
        if rest and rest != key:
            return rest
        return None

    def _roll(self, context: dict[str, Any] | None = None) -> str | None:
        """The fidget, plus one pick-log row per roll when HOUSE_BRAIN_LOG=1 (lib/conductor_brain.brain_log)."""
        key = self._roll_pick(context)
        log = _brain_log()
        if log is not None:
            log({"kind": "roll", "here": self.here, "now": self.now, "comms": bool(self.comms),
                 "conversation": bool(self.conversation), "on_air": bool(self.hold_flourishes),
                 "reason": self.reason, "played": key})
        return key

    def _roll_pick(self, context: dict[str, Any] | None = None) -> str | None:
        """The fidget. A brain may choose it; the bag is both the fallback and
        the veto — a brain that names an illegal clip is overruled, not obeyed,
        so no model can put her through a wall."""
        if self.hold_flourishes and not FLOURISH_SPEECH:
            return None
        if CONV_HOLD and self.conversation:
            self.reason = "holding flourish (in conversation)"
            self._shadow_ask(context)
            return None
        if self.camp:
            if self.now == self.camp:
                self.reason = "camp: staying on %s" % self.camp
                return None
            if self.graph.may_follow(self.camp, self.here):
                self.reason = "camp: back to %s" % self.camp
                return self.camp
        if self.tour.get("on") and not self.comms:
            held = self.tour_hold() if callable(self.tour_hold) else ""
            if held:
                self.reason = "tour held: %s" % held
            else:
                return self._tour_step(time.time())
        here = self.here
        legal = lambda k: (  # noqa: E731
            k not in FT_DEMAND and self.graph.may_follow(k, here)
        )

        if self.brain is not None:
            try:
                pick = self.brain(dict(context or {}, here=here, pool=self.offer(), comms=self.comms))
            except Exception:
                pick = None
            if self.comms and pick:
                # A call. The pick must be one of the offered cores, and a
                # core holds a few wraps before she moves (no ping-pong).
                if pick not in self.comms_offer():
                    self.reason = "brain overruled: %s is not a comms core from %s" % (pick, here)
                    return None
                if pick == self.now:
                    self.reason = "brain: stay"
                    return None
                if self._core_wraps < COMMS_MIN_WRAPS:
                    self.reason = "brain: %s waits (%d/%d wraps)" % (pick, self._core_wraps, COMMS_MIN_WRAPS)
                    return None
                if legal(pick) and self.graph.start(pick) == here:
                    self.bag.last = pick
                    self.reason = "brain (comms)"
                    return pick
                r = self.insert(pick, time.time(), ttl=0, by="brain", bridge=True)
                self.reason = "brain (comms) queued %s: %s" % (pick, "taken" if r.get("taken") else r.get("why"))
                return None
            if pick and legal(pick):
                self.bag.last = pick
                self.reason = "brain"
                return pick
            if pick:
                self.reason = "brain overruled: %s is not legal from %s" % (pick, here)

        key = self.bag.draw(self.pool(), legal)
        if key:
            self.reason = "bag" if self.reason.startswith("b") is False else self.reason
            if not self.reason.startswith("brain overruled"):
                self.reason = "bag"
        return key

    def _shadow_ask(self, context: dict[str, Any] | None) -> None:
        """With the pick log on, ask the brain during a conversation anyway - logged, never played - so the
        call moments the hold skips can still be scored (the User 2026-10-01). Off: HOUSE_BRAIN_LOG_SHADOW=0."""
        if self.brain is None or _brain_log() is None:
            return
        try:
            from conductor_brain import BRAIN_LOG_SHADOW
        except Exception:  # noqa: BLE001
            return
        if not BRAIN_LOG_SHADOW:
            return
        try:
            self.brain(dict(context or {}, here=self.here, pool=self.offer(), comms=self.comms,
                            conversation=True, shadow=True))
        except Exception:  # noqa: BLE001
            pass

    # ---- the tour -----------------------------------------------------------
    def tour_keys(self) -> list[str]:
        """Every element loop, grouped by hub, nearest hubs to home first."""
        loops = [k for k, r in self.graph.clips.items()
                 if str(r.get("role") or "") == "loop" and bool(r.get("element"))
                 and r.get("start") and r.get("start") == r.get("end")]
        def dist(hub: str) -> int:
            return 0 if hub == HOME else (len(self.graph.path(HOME, hub)) or 99)
        return sorted(loops, key=lambda k: (dist(str(self.graph.start(k))), str(self.graph.start(k)), k))

    def tour_start(self, keys: list[str] | None = None, dwell_s: float | None = None) -> dict:
        loops = list(keys) if keys is not None else self.tour_keys()
        for k in loops:
            self.graph.admit(k)                  # a non-kit loop and its hub's slides join as guests
        self.tour.update({"on": True, "keys": loops, "i": 0, "since": None, "visited": 0,
                          "skipped": [], "laps": 0,
                          "dwell_s": float(dwell_s) if dwell_s is not None else float(self.tour.get("dwell_s") or TOUR_DWELL_S)})
        return self.tour_status()

    def tour_stop(self) -> dict:
        self.tour["on"] = False
        self.graph.evict_guests()
        return self.tour_status()

    def tour_status(self) -> dict:
        t = self.tour
        n = len(t.get("keys") or [])
        return {"on": bool(t.get("on")), "target": self._tour_target(), "at": (t["i"] % n) + 1 if n else 0,
                "of": n, "laps": t.get("laps", 0), "visited": t.get("visited", 0),
                "dwell_s": t.get("dwell_s"), "skipped": list(t.get("skipped") or [])[-12:]}

    def _tour_target(self) -> str | None:
        keys = self.tour.get("keys") or []
        return keys[self.tour["i"] % len(keys)] if keys else None

    def _tour_advance(self, skipped: str | None = None) -> None:
        t = self.tour
        if skipped:
            t["skipped"].append(skipped)
        else:
            t["visited"] += 1
        t["i"] += 1
        t["since"] = None
        if t["keys"] and t["i"] % len(t["keys"]) == 0:
            t["laps"] += 1

    def _tour_step(self, now: float) -> str | None:
        keys = self.tour.get("keys") or []
        for _ in range(len(keys) + 1):
            target = self._tour_target()
            if target is None:
                return None
            n = len(keys); at = (self.tour["i"] % n) + 1
            if self.now == target:
                if self.tour.get("since") is None:       # the tour started on the clip she was already in
                    self.tour["since"] = now
                since = self.tour["since"]
                if self.wraps < 1 or (now - since) < float(self.tour.get("dwell_s") or 0):
                    self.reason = "tour: on %s (%d/%d)" % (target, at, n)
                    return None                  # stay: the loop wraps
                self._tour_advance()
                continue
            hub = self.graph.start(target)
            if not self.graph.compose_ok(target) or (self.graph.casual(target) and not self.private):
                # a casual loop only plays while the wall is Secure — skipped this lap, tried again next
                self._tour_advance(skipped=target)
                continue
            if self.here == hub:
                self.reason = "tour: %s (%d/%d)" % (target, at, n)
                return target
            hops = self.graph.path(self.here, hub)
            if hops:
                self.reason = "tour: crossing to %s for %s (%d/%d)" % (hub, target, at, n)
                return hops[0]
            self._tour_advance(skipped=target)
        return None

    def home(self) -> str | None:
        """Back to the rest she belongs on. Crossing hubs takes the slide."""
        if self.camp:
            return self.camp if self.camp != self.now else None
        rest = self.graph.rest_for(self.here)
        if rest and rest != self.now:
            return rest
        return None

    def at_home(self) -> bool:
        return self.here == HOME

    def bring_home(self, now: float, by: str = "user") -> dict:
        """DJ: put her on the laptop. From there every flourish starts.

        Already on down sitting `work`: nothing to queue. On down in a fidget:
        queue the rest loop. Away: queue `work`; take_cue walks the slides.
        """
        rest = self.graph.rest_for(HOME) or "work"
        if _guest_holds(self.graph.row(self.now)):
            # THE GUEST HOLDS HER PLATE: no wrap, routine end or other hand walks her off it (Setup's own leave does)
            return {"taken": False, "why": GUEST_HOLDS_WHY, "at_home": False, "queue": self.queued(),
                    "queue_rows": self.queue_rows(now)}
        if self.camp and by == "wrap":
            return {"taken": False, "why": "camp %s" % self.camp,
                    "at_home": False, "queue": self.queued(),
                    "queue_rows": self.queue_rows(now)}
        if self.camp:
            print("[conductor] camp: cleared by bring_home (%s)" % by, flush=True)
        self.camp = None
        self.end_comms("home")
        if self.here == HOME and self.now == rest:
            return {"taken": True, "key": rest, "when": "already home",
                    "at_home": True, "queue": self.queued(),
                    "queue_rows": self.queue_rows(now)}
        from_webcam = self.here == self.graph.start(USER_TALK_KEY)
        # None = a guest host (Lila on Crissa's glass) whose row is in the
        # Library so the film plays, but not in this Graph, so hub_after
        # is empty. Render leave 2026-09-16: `no way home from None` cleared
        # the camp and left her parked on the render host. An unknown hub
        # must still take the desk rest — may_follow already allows it.
        if (
            self.here is not None
            and self.here != HOME
            and not self.graph.path(self.here, HOME)
            and not from_webcam
        ):
            return {"taken": False, "why": "no way home from %s" % self.here,
                    "at_home": False, "queue": self.queued(),
                    "queue_rows": self.queue_rows(now)}
        r = self.insert(rest, now, ttl=0, by=by)
        r["at_home"] = False
        return r
