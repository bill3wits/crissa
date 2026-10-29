#!/usr/bin/env python3
"""VoiceOnly home screen — server for Crissa's ambient display.

NEW SURFACE, side by side with face-shell (:8088). Touches none of the
face-shell files: display/face-shell.html, scripts/{crissa-display,
display-surface.sh,face-shell-server.py} are Grok's lane and were dirty at
build time (Claude 2026-08-10).

Serves on :8099 and gives the page a same-origin view of things it may not
fetch cross-port:

  GET  /                 the screen (display/voiceonly-home.html)
  GET  /v1/house         proxy of face-shell :8088/v1/house (the real SoT read)
  GET  /v1/sound         live beam snapshot (Presence sound + glyph). Not a roster.
  GET  /v1/sound/stream  SSE of the same, ~5 Hz — VoiceOnly lobe must not wait
                         on the 2s /v1/house poll
  GET  /frame            proxy of rail-camera :8192/snapshot (JPEG, for ASCII)
  GET  /v1/coach         SSE passthrough of the enroll coach :8196/events, so
                         initialization renders as a state of THIS screen
  GET  /v1/screen        the state Crissa authored (mood/headline/note/focus/chips)
  POST /v1/screen        Crissa writes the screen; merged over live truth
  POST /v1/action        allowlisted bridge to the panel rails (Hybrid chrome)
  GET  /v1/script        allowlisted unit start script + unit file (Control Panel)
  GET  /v1/log-whole     the house's whole log, text/plain, keys masked, nothing cut (lib/panel_wiring, house only)
  GET  /v1/watchers      who watches what and who watches the watchers (lib/panel_wiring, house only)
  GET  /v1/render-studio Render-only Lila studio snapshot (no-op off Render)
  POST /v1/render-studio Render-only Lila turn (talk + maybe generate + look)
  POST /v1/render-studio-mic   arm/disarm room-ear latch (Render only)
  POST /v1/render-studio-listen one-shot :8190/v1/listen if ear closed
  GET  /render-still/X   allowlisted PNG under the Comfy output folder
  GET  /v1/render-wip    what Comfy draws now + every version this Render (lib/render_wip)
  GET  /render-wip.img   Comfy's newest preview frame (the watcher's file)
  GET  /render-out/<rel> allowlisted output (png/jpg/mp4/webm) anywhere under the Comfy output folder
  GET  /v1/init          first-boot User wizard snapshot (init-sequence.json)
                         ?glass=1 = the wall is showing it; holds the mic for it
  POST /v1/init          wizard start/answer/skip/abort (no Mic, no Crissa generate)
  GET  /init-wizard.js   injected wall walker (does not unlock VoiceOnly HTML)
  GET  /v1/health-report?job=<id>  async battery job (also latest.json)
  GET  /v1/pose          character-kit inventory (etag + hashed /pose src)
  POST /v1/pose/cue      fire an emote specialist (emote-surprise / emote-woe)
  POST /v1/pose/cue/ack  glass consumed the cue
  GET  /ft/talk-back     A/B filmed talking take for the FaceTime window
  GET  /health

Crissa control:
  curl -X POST :8099/v1/screen -H 'Content-Type: application/json' \
       -d '{"mood":"speaking","headline":"Welcome back."}'

The screen renders from /v1/house regardless, so it degrades to plain truth
if Crissa never posts. Screen state persists across restarts.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import crissa_home  # noqa: E402   (the state directory; this machine's own paths from config/house-machine.json)
import crissa_ports  # noqa: E402   (the loopback port table: the camera cell's grabber, 2026-10-10)
import crissa_guard  # noqa: E402   (round 46: the one loopback rule every server of a pack keeps)

# EVERY ROUTE IS THIS MACHINE'S, NOT THE WEB'S (round 46, the local review of kit crissa-063a2813, 2026-10-23): any
# page open in the buyer's browser could reach this feed on 127.0.0.1 and drive Setup, the Mode keys and the logs
# through it; a page that rebinds its own name to 127.0.0.1 sends no Origin at all. lib/crissa_guard refuses a request
# whose Host is not this machine, whose Origin is foreign, or that the browser marks cross-site with no loopback
# Referer, before any route reads it. On in a pack; the house keeps today's way until it is flipped there
# (CRISSA_LOOPBACK_GUARD=1); =0 is the kill. The house's pages also come in on its own LAN names
# (config/house-machine.json `panel_origins`: panel.home.arpa and wall.home.arpa, which /etc/hosts points at
# 127.0.0.1), so those hosts count as this machine; a pack carries no such file and is loopback only.
FEED_GUARD_DEFAULT = "0" if crissa_home.is_house() else "1"


def _feed_guard_names() -> tuple:
    names = []
    for o in crissa_home.house_value("panel_origins").split(","):
        h = o.strip().split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip().lower()
        if h:
            names.append(h)
    return tuple(names)


FEED_GUARD_NAMES = _feed_guard_names()
# THE CALLER TRAVELS WITH A FORWARD (round 46): the Init keys and Security go on to the player, whose own guard
# (connect_origin_ok, foreign_request) reads the Origin - and this feed's forward sent none, so the player passed any
# caller the feed let through. Each request's Origin is kept for its own thread (ThreadingHTTPServer: one per
# request) and every forward to the player carries it. An Origin naming one of the house's own names above is this
# machine (the player knows only loopback), so it travels as none. Kill: VO_FORWARD_ORIGIN=0.
FORWARD_ORIGIN = os.environ.get("VO_FORWARD_ORIGIN", "1").strip().lower() not in ("0", "off", "no", "false")
_CALLER = threading.local()


def feed_guard(handler) -> bool:
    """First thing in every do_*: True = go on; False = refused (403) and answered. Keeps the caller's Origin."""
    _CALLER.origin = str(handler.headers.get("Origin") or "").strip()
    return crissa_guard.check(handler, names=FEED_GUARD_NAMES, default=FEED_GUARD_DEFAULT)


def caller_headers() -> dict:
    """The headers a forward to the player carries for the caller of this request (its Origin)."""
    o = getattr(_CALLER, "origin", "") if FORWARD_ORIGIN else ""
    if not o:
        return {}
    if FEED_GUARD_NAMES and crissa_guard.loopback_url(o, FEED_GUARD_NAMES) and not crissa_guard.loopback_url(o):
        return {}
    return {"Origin": o}


def capability() -> dict:
    """What this computer can do (lib/crissa_capability, 2026-10-10): the Control Panel page greys what it cannot,
    with one reason, as the :8097 panel and the Crissa menu do. The house answers everything available."""
    try:
        import crissa_capability
        return crissa_capability.features()
    except Exception as e:  # noqa: BLE001  never 500 the screen
        return {"features": {}, "unavailable": [], "error": "%s: %s" % (type(e).__name__, str(e)[:120])}

ROOT = _LIB.parent
# WHERE THIS FEED KEEPS ITS FLAGS AND ROLLS (2026-10-09): the install's state directory, one place for every pack
# and the house alike (the house's avatar manifest names the directory it has always used). A machine's own paths
# (its camera log, its identity root, its screen record) come from config/house-machine.json through
# crissa_home.house_value, each with a default under the state directory; no path is spelled here.
STATE = crissa_home.HOME


def _machine_path(key: str, default: Path) -> Path:
    got = crissa_home.house_value(key)
    return Path(got).expanduser() if got else default


def _panel_cfg() -> dict:
    """This machine's Control Panel roster: config/house-machine.json `panel` (the house); {} in a pack."""
    try:
        got = json.loads(crissa_home.HOUSE_MACHINE.read_text(encoding="utf-8")).get("panel")
    except (OSError, ValueError, AttributeError):
        return {}
    return got if isinstance(got, dict) else {}


_PANEL = _panel_cfg()
# THE AGENT FRAMEWORK'S ROWS ARE THE HOUSE'S (2026-10-09, the Crissa parity build): the per-profile gateway rows,
# the host tile, a profile's tape and SOUL, the restart that is owed to the window - all of it moved line for line
# into lib/feed_house_rows.py, which a pack does not carry. Every shim below answers the neutral value without it,
# and krew's own tiles reach the panel through its add-on slot (lib/crissa_panel_addons) when krew is installed.
try:
    import feed_house_rows as _rows  # noqa: E402
except ImportError as _e:
    if _e.name != "feed_house_rows":
        raise
    _rows = None
if _rows is not None:
    # ONE COPY PER LOAD (the Crissa merge, 2026-10-11): the module binds the server's globals it was installed into,
    # so a second load of this server in one process (the house's tests load several) gets its own copy rather than
    # re-pointing the first one's. In service there is one load and nothing changes.
    try:
        import importlib.util as _ilu
        _rows_spec = _ilu.spec_from_file_location("feed_house_rows_%x" % id(globals()), _LIB / "feed_house_rows.py")
        _rows_own = _ilu.module_from_spec(_rows_spec)
        _rows_spec.loader.exec_module(_rows_own)
        _rows = _rows_own
    except Exception:  # noqa: BLE001 - the shared module still serves this load
        pass
# which row is Crissa's agent gateway and which the agent dashboard: named in the machine config, "" in a pack
CRISSA_GATEWAY_ROW = str(_PANEL.get("crissa_gateway_row") or "")
DASHBOARD_ROW = str(_PANEL.get("dashboard_row") or "")
# THE HOUSE'S CONTROL PANEL WIRING (the User 2026-10-06, rebuilt on the merged tree 2026-10-11 without the Chatterbox
# parts): paged, marked, masked tapes and the whole log, named failure reasons, Run now, each avatar's keys, a tile for
# every running house unit, who watches what - lib/panel_wiring.py, house-only and absent-ok, one copy per load like
# the rows above. Absent (a pack) or HOUSE_CP_WIRING=0: every hook below answers None and the feed is the one it was.
try:
    import panel_wiring as _cpw  # noqa: E402
except ImportError as _e:
    if _e.name not in ("panel_wiring", "panel_logs"):
        raise
    _cpw = None
if _cpw is not None:
    try:
        import importlib.util as _ilu
        _cpw_spec = _ilu.spec_from_file_location("panel_wiring_%x" % id(globals()), _LIB / "panel_wiring.py")
        _cpw_own = _ilu.module_from_spec(_cpw_spec)
        _cpw_spec.loader.exec_module(_cpw_own)
        _cpw = _cpw_own
    except Exception:  # noqa: BLE001 - the shared module still serves this load
        pass
import mouth_mailbox  # noqa: E402   (the viseme sheet store, shared with the benches)
import panel_units  # noqa: E402   (CP restart post-check, journals by scope, krew searches)
from audio_sink import live_mouth_line  # noqa: E402
from fov_window import latest_bounds as read_kiosk_bounds, start_sampler as start_fov_sampler  # noqa: E402
from vo_error_check import ERROR_CHECK_ACTIONS, run_operator_error_check  # noqa: E402
from vo_sound import build_sound_payload, enrich_sound, room_frame, seats_fixed  # noqa: E402
from glass_prefs import GlassPrefs  # noqa: E402   (Connect Walls step 2: the shared prefs record)
# THE HOUSE'S INIT WIZARD (Register, the first-boot walk) is the house's: a pack's first boot is lib/crissa_init on
# the player (2026-10-05). Absent, every Init key here answers the same mark and the page draws Register greyed.
try:
    from init_wizard import (  # noqa: E402
        apply as init_apply,
        attend as init_attend,
        heard_since as init_heard,
        master_done as init_master_done,
        mouth_busy as init_mouth_busy,
        snapshot as init_snapshot,
    )
    INIT_WIZARD = True
except ImportError as _e:
    if _e.name != "init_wizard":
        raise
    INIT_WIZARD = False
    NO_INIT_WIZARD = {"ok": False, "running": False, "mark": "○", "note": "no Init wizard in this edition"}

    def init_apply(act, body=None):          # noqa: D103
        return dict(NO_INIT_WIZARD, action=act)

    def init_attend():                        # noqa: D103
        return dict(NO_INIT_WIZARD)

    def init_heard(since, ignore=None):       # noqa: D103
        return {"ok": True, "heard": [], "since": since, "note": NO_INIT_WIZARD["note"]}

    def init_master_done():                   # noqa: D103
        return False

    def init_mouth_busy():                    # noqa: D103
        return False

    def init_snapshot():                      # noqa: D103
        return dict(NO_INIT_WIZARD)

# A PACK'S INIT IS THE PLAYER'S (2026-10-09, the Crissa parity build): the id module serves the real Init on the
# conductor's /v1/init (lib/crissa_init -> crissa_id.init_wizard) and the Tk wall asks the player for it directly
# (HOUSE_WALL_INIT). This server's own page, its Register keys and the /v1/house `init` key read the SAME walk
# through here: with no wizard of its own and a player named - HOUSE_WALL_FEED, what the launcher hands every
# feed; VO_PLAYER_URL wins - every Init key forwards to the player. Unnamed (the house, where the wizard is here;
# a bare probe), nothing is forwarded and the keys answer the mark above. Kill: VO_INIT_FORWARD=0.
PLAYER_INIT_URL = ""
if not INIT_WIZARD and os.environ.get("VO_INIT_FORWARD", "1").strip().lower() not in ("0", "off", "no", "false"):
    PLAYER_INIT_URL = (os.environ.get("VO_PLAYER_URL") or os.environ.get("HOUSE_WALL_FEED") or "").strip().rstrip("/")


def _player_init(path: str, body=None, timeout: float = 3.0) -> dict:
    """One call to the player's Init routes; a player that does not answer leaves the pack's mark, plus `player`."""
    url = PLAYER_INIT_URL + path
    try:
        if body is None:
            req = urllib.request.Request(url, headers=caller_headers())
        else:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers=dict(caller_headers(), **{"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read().decode() or "{}")
        return got if isinstance(got, dict) else {"ok": False, "error": "the player's Init answered no object"}
    except urllib.error.HTTPError as e:
        return dict(NO_INIT_WIZARD, player=url, error="the player answered %d" % e.code)
    except Exception as e:  # noqa: BLE001
        return dict(NO_INIT_WIZARD, player=url, error="%s: %s" % (type(e).__name__, str(e)[:120]))


if PLAYER_INIT_URL:
    def init_apply(act, body=None):          # noqa: D103,F811  the walker's actions; listen blocks on the ear
        b = dict(body or {}, action=act)
        return _player_init("/v1/init", b, timeout=30.0)

    def init_attend():                        # noqa: D103,F811
        return _player_init("/v1/init?glass=1")

    def init_heard(since, ignore=None):       # noqa: D103,F811
        q = urllib.parse.urlencode({"since": since, "ignore": ignore or ""})
        return _player_init("/v1/init-heard?" + q)

    def init_mouth_busy():                    # noqa: D103,F811
        return bool(_player_init("/v1/init-mouth", timeout=1.5).get("speaking"))

    def init_snapshot():                      # noqa: D103,F811
        return _player_init("/v1/init")
from pose_inventory import cue_ack, cue_set, pose_clips, pose_payload  # noqa: E402

HOST = os.environ.get("VO_HOME_HOST", "127.0.0.1")
PORT = int(os.environ.get("VO_HOME_PORT", "8099"))

HOUSE_URL = os.environ.get("VO_HOUSE_URL", "http://127.0.0.1:8088/v1/house")
PRESENCE_URL = os.environ.get(
    "VO_PRESENCE_URL", "http://127.0.0.1:8191/v1/presence"
)
# THE CAMERA CELL (2026-10-10): the room camera's grabber from the port table (the house's is the same 8192), and with
# the camera off (CRISSA_NO_CAMERA=1, the launcher's --no-camera) the cell answers {"camera": "off"} and asks nothing.
FRAME_URL = os.environ.get("VO_FRAME_URL") or crissa_ports.url("camera") + "/snapshot"
CAMERA_OFF = str(os.environ.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on")
# The live face ID - the same values the visor paints on the presence service; which route is this
# machine's (config `speaker_url`), "" in a pack until its oracle serves one.
SPEAKER_URL = os.environ.get("VO_SPEAKER_URL") or crissa_home.house_value("speaker_url")
# Where the lens is pointing. The wall turns her head when the camera turns,
# so it needs the pan, not a motion event — motion is something she cannot see.
FIRMWARE_URL = os.environ.get("VO_FIRMWARE_URL") or crissa_ports.url("camera") + "/v1/firmware"
# Her rendered poses (Grok, 2026-08-27). Shot into this same chair at pan
# a seat pan / tilt -15, so the clip already carries the room — the wall does not
# composite her onto anything, it just runs the frame through the ramp the way
# it runs the camera. Names are allowlisted: this serves video off disk.
POSE_DIR = Path(__file__).resolve().parent.parent / "display" / "crissa-poses"
REGISTER_DIR = Path(__file__).resolve().parent.parent / "display" / "register"
REGISTER_INTRO = {
    "crissa-intro.mp4": "video/mp4",
    "crissa-intro.jpg": "image/jpeg",
}
INIT_HOST_DIR = Path(__file__).resolve().parent.parent / "display" / "init-host"
INIT_HOST_TYPES = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
# SoT is display/crissa-poses/inventory.json (character_kit/v1).
# Look up /pose/<key> live — a minted clip must 200 without bouncing this unit.
# boot snapshot only; handlers call pose_clips(). A feed with no face library beside it (the feed package alone,
# before the player's faces are installed) starts with none: the player serves her mouth, not this server.
try:
    POSE_CLIPS = pose_clips()
except (OSError, ValueError) as _e:
    POSE_CLIPS = {}
    print(f"pose inventory not read ({type(_e).__name__}): no face library beside the feed", file=sys.stderr, flush=True)

# Crissa's turn is shot at her chair: a real camera frame, so her channel carries
# the same room, the same light and the same ASCII texture as the live ones.
#
# Captured by hand at station 11, pan a seat pan / tilt -15deg. Not the patrol's
# st12 reference, for two reasons. That frame is tilted too far down to leave
# headroom above the chair back, and "station 12" is not a direction this
# camera has: seat_12 and stand_11 share pan a seat pan and differ only in tilt.
# True 12 o'clock would need ~600000 pan and the hardware stops at 522000,
# where it sees a bare wall corner.
#
# -15deg is the relief: the chair sits centre-frame with wall above it, which
# is the room the rendered talking head gets composited into. Nothing here
# moves the camera — we only read a file.
REF12_PATH = Path(
    os.environ.get("VO_REF12") or _machine_path("ref12", STATE / "ref12.jpg")
).expanduser()
SOUND_TICK_SEC = float(os.environ.get("VO_SOUND_TICK_SEC", "0.2"))
# Ear health, for the posture peek only. Short timeout on purpose (below).
STT_HEALTH_URL = os.environ.get("VO_STT_HEALTH_URL", "http://127.0.0.1:8190/health")
# Rail-voice hear gate (session_open / paused). Same :8194 the house poll
# already reads. Timeout must stay under SOUND_TICK_SEC.
HEAR_HEALTH_URL = os.environ.get("VO_HEAR_HEALTH_URL", "http://127.0.0.1:8194/health")
# enroll coach SSE (house-id house_enroll.coach) — initialization events
COACH_URL = os.environ.get("VO_COACH_URL", "http://127.0.0.1:8196/events")

# THE FEED'S NEIGHBOURS (2026-10-10): every address the rail actions and the health probes below ask comes from this
# install's port table, so a pack beside another install asks its own pieces, never that install's. The house's own
# services that are not Crissa pieces - its presence rail (8191), its engines, its voice server, its ComfyUI - are
# named in config/house-machine.json (top-level presence_url / comfy_url, panel.engine_urls / voice_health_url); a
# pack has no such file, so it asks its own presence service and render module's ComfyUI, and an engine or a voice
# server it does not run is simply not probed (its row is not drawn in a pack: the rows are the launcher's children).
STT_BASE = crissa_ports.url("stt")
ASR_BASE = crissa_ports.url("asr")
BRIDGE_BASE = crissa_ports.url("bridge")
ENROLL_BASE = crissa_ports.url("enroll")
PRESENCE_BASE = (crissa_home.house_value("presence_url") or crissa_ports.url("presence")).rstrip("/")
# THE SPEAKER ROW IN A PACK (2026-10-10, a round-3 leftover: SPEAKER_URL was only ever the house's visor, so a pack's
# Wall printed "Speaker None" for good): this install's own presence serves the visor's shape at /v1/faceid (faces,
# locked, speaker, frame size). VO_SPEAKER_URL or the house's speaker_url still win.
if not SPEAKER_URL and not crissa_home.is_house():
    SPEAKER_URL = PRESENCE_BASE + "/v1/faceid"
COMFY_BASE = (os.environ.get("CRISSA_COMFY_URL") or crissa_home.house_value("comfy_url")
              or crissa_ports.url("comfy")).rstrip("/")

# Hybrid rail actions. action -> (url, default payload). Every target is one the
# existing Control Panel (rail-stt/panel_ui.py) already drives in production, so
# this bridge adds no new authority — it only makes them reachable same-origin.
# The Voice master keeps its local-only 403 guard upstream; we surface the
# refusal rather than working around it.
# A STRANGER IS LOCKED OUT OF ADJUSTING (round 17 follow-ups, the User 2026-10-13: "a stranger should then be locked out
# from any further adjusting. All tools or switches requesting auth gets face match sequence"): while the kit's Security
# is On or above, a key of this feed that changes a setting or a switch runs the kit's one face-match sequence first
# (lib/crissa_home.face_gate) and does nothing without his face. Open to anyone: what only stops or quiets her
# (FEED_OPEN, named in lib/crissa_home.OPEN_TO_ANYONE) and what changes no setting (FEED_NO_SETTING: checks, the
# camera's look, the window's place, presence's own Secure keys - presence checks a step down itself). Anything not
# named in either is locked. /v1/init goes to the player, which runs the same gate. Kill: CRISSA_SECURITY_LOCK_ADJUST=0.
FEED_OPEN = {"voice_off": "mic_off", "voice_pause": "pause", "voice_hold_off": "pause", "enroll_cancel": "cancel",
             "announcer_off": "pause"}
FEED_NO_SETTING = frozenset((
    "health_check", "speed_check", "health_battery", "camera_home", "camera_aim", "who_is_here", "auth_join",
    "enroll_prearm", "register_glass", "kiosk_register", "kiosk_return_cp", "kiosk_home", "raise_panel", "kiosk_panel",
    "kiosk_wall", "fullscreen_on", "fullscreen_off", "visit_click", "visit_hold", "voice_listen", "voice_hold_on"))


def _sec_presence() -> dict:
    """Presence's state for the face-match sequence (this install's presence, PRESENCE_BASE); {"_err"} when down."""
    try:
        with urllib.request.urlopen(PRESENCE_BASE + "/v1/presence", timeout=1.0) as r:
            d = json.loads(r.read(400000).decode("utf-8", "replace") or "{}")
            return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        return {"_err": type(e).__name__}


def _sec_look() -> dict:
    """A camera that turns: presence's own look at his seat (POST /v1/visit_lamp {op: face_check})."""
    try:
        req = urllib.request.Request(PRESENCE_BASE + "/v1/visit_lamp", data=json.dumps({"op": "face_check"}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12.0) as r:
            d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
            return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "checked": False, "error": type(e).__name__}


def feed_face_refused(act: str) -> dict | None:
    """Before a feed key (above): None = go on; else the answer, nothing changed. Never on the house."""
    if crissa_home.is_house():
        return None
    a = str(act or "")
    if not a or a in FEED_NO_SETTING or a in ERROR_CHECK_ACTIONS:
        return None
    try:
        got = crissa_home.face_gate(FEED_OPEN.get(a, "feed_" + a), fetch=_sec_presence, look=_sec_look)
    except Exception:  # noqa: BLE001  an older crissa_home: the feed as before
        return None
    return None if got is None else dict(got, action=a, note=got.get("error"))


def security_house_block(house: dict) -> dict | None:
    """The kit's Security toggle for /v1/house (crissa_home.security_state): its positions, the lock and the ask."""
    try:
        return crissa_home.security_state(presence={"visit_lamp_info": house.get("visit_lamp_info") or
                                                    (house.get("presence") or {}).get("visit_lamp_info") or {}})
    except Exception:  # noqa: BLE001
        return None


def security_set(body: dict) -> dict:
    """POST /v1/security from this feed's pages: the player's Crissa menu action (its own gate, Secure's steps through
    presence); the password, for Double Secure, is passed once and never kept here."""
    url = (os.environ.get("VO_PLAYER_URL") or os.environ.get("HOUSE_WALL_FEED") or "").strip().rstrip("/")
    if not url:
        return {"ok": False, "error": "no player to save Security"}
    b = {"action": "security", "mode": str(body.get("mode") or ""), "by": "the feed"}
    if body.get("password"):
        b["password"] = str(body.get("password"))
    try:
        req = urllib.request.Request(url + "/v1/menu", data=json.dumps(b).encode(), method="POST",
                                     headers=dict(caller_headers(), **{"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=25.0) as r:
            got = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        got = {"ok": False, "error": "the player answered %d" % e.code}
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "error": "the player did not answer (%s)" % type(e).__name__}
    b.pop("password", None)
    return got if isinstance(got, dict) else {"ok": False, "error": "the player answered no object"}


ACTIONS: dict[str, tuple[str, dict]] = {
    # NEVER a bare {} here: /panel/voice reads an absent flag as OFF, so an
    # empty payload silently disarms the house Voice master (it did, once,
    # during testing on 2026-08-10). The intent must always be explicit.
    "voice_on":  (STT_BASE + "/panel/voice", {"on": True,  "on_by": "user"}),
    "voice_off": (STT_BASE + "/panel/voice", {"on": False, "on_by": "user"}),
    # PAUSE / LISTEN is not the mic. The wall chip toggles the session.
    # Clicking Paused is a bare wake (no extra words). Voice on/off stays
    # a Control Panel switch for a private conversation.
    "voice_pause": (BRIDGE_BASE + "/v1/session/pause", {}),
    "voice_listen": (BRIDGE_BASE + "/v1/session/open", {}),
    # Comms hold: stops only the ear's idle expiry. Fixed bodies — the
    # action map does not pass the page's payload through for these.
    "voice_hold_on": (BRIDGE_BASE + "/v1/session/hold", {"on": True}),
    "voice_hold_off": (BRIDGE_BASE + "/v1/session/hold", {"on": False}),
    "camera_home":  (STT_BASE + "/panel/camera-home", {}),
    # CAMERA JOYSTICK (the User 2026-09-06): the User aims the gimbal to SHOW
    # Crissa something. Presence owns it: AUTH required, lease on :8192, and
    # Secure drops on a nudge unless the keyboard hold is on. payload
    # {op: nudge, dx, dy} in degrees, or {op: home}.
    "camera_aim":   (PRESENCE_BASE + "/v1/camera/aim", {"op": "nudge", "dx": 0, "dy": 0}),
    # Re-scan room = Inspector who_is_here (wide frame + FaceFinder zoom).
    # error_check / scan_room aliases still POST Oracle :8191/v1/error_check
    # (same stills + FF, plus body/seat scoring).
    "who_is_here": (
        PRESENCE_BASE + "/v1/inspector",
        {"op": "who_is_here", "source": "voiceonly_panel"},
    ),
    "enroll_start": (ENROLL_BASE + "/start", {"mode": "voice-register", "station": "desk"}),
    "enroll_cancel": (ENROLL_BASE + "/cancel", {}),
    # the wall host's pre-arm (2026-09-11): the coach parks the camera on the
    # desk, closes the ear and cues the listen loop BEFORE the press, so
    # Register's t=0 is Lila's first line, not a slew. Idempotent.
    "enroll_prearm": (ENROLL_BASE + "/prearm", {"station": "desk"}),
    # Visit lamp click. Desk FaceFinder. Glass must not toast.
    "visit_click": (PRESENCE_BASE + "/v1/visit_lamp/click", {}),
    # PIP Connecting… join. Desk FaceFinder grant. Not the Secure toggle.
    "auth_join": (
        PRESENCE_BASE + "/v1/ensure_user_auth",
        {"force_facefind": True, "reason": "pip_connect"},
    ),
    # KEYBOARD HOLD (the User 2026-09-06). Double-click on the Secure chip asks
    # for the account password; presence checks it through PAM and holds
    # Secure until the next single click. The page sends {op:"hold",
    # password} as `payload`; it passes through untouched and is not logged.
    "visit_hold": (PRESENCE_BASE + "/v1/visit_lamp", {"op": "hold"}),
}

# House lanes shown in the menu, in reading order. Restart is allowlisted to
# exactly these - the page may never name an arbitrary unit. THE LIST IS THE ORDER;
# section membership lives in SERVICE_SECTIONS (the page buckets by section name and
# takes block order from first appearance here). Units in a summary group (SVC_GROUPS)
# are pulled out by the page and rendered under their own chip block.
# WHERE THE LIST LIVES (2026-10-09): this machine's config, config/house-machine.json
# `panel.feed_rows` / `feed_sections` / `feed_href` - one machine's unit names in its
# own file (the User 2026-10-06), moved there verbatim. A pack carries no such file: its
# rows are the launcher's children (lib/crissa_procs, merged in services_snapshot) and
# the add-on tiles under <state>/panel.d, so the shipped default is empty.
SERVICES: list[tuple[str, str, int | None]] = [tuple(r) for r in (_PANEL.get("feed_rows") or [])]
SERVICE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (str(name), tuple(units)) for name, units in (_PANEL.get("feed_sections") or []))
# Operator pages only (health JSON is not a Control Panel destination); unit-derived
# journal and script need no entry here.
OPEN_HREF: dict[str, str] = dict(_PANEL.get("feed_href") or {})
_SECTION_OF = {u: name for name, units in SERVICE_SECTIONS for u in units}


# Announcer master. The keeper drops every announcement while this file exists,
# so presence of the file == announcer muted.
ANNOUNCE_MUTE = STATE / "house-announce-mute"
VOICE_ONLY_FLAG = STATE / "voice-only"
# One gag, and it is opt-in. When a security event puts a face on the doc
# sheet, the sheet opens as a WANTED poster for a beat and then settles into
# the ordinary log item it always was. Off by default: a house that jokes
# about strangers without being asked is not funny, it is strange.
WANTED_FLAG = STATE / "wanted-poster"
# THE RENDER STUDIO AND THE MODE SWITCH ARE KREW'S WHEN KREW IS INSTALLED (2026-10-10, the Crissa parity build): the
# studio (Lila's turns, the hang still, the listen mic) rides the agent framework's board and its GPU mode switch, so
# it ships with krew. krew's crew install writes one pointer here, <state>/krew-render.json:
#   {"schema": "krew-render/1", "lib": "<krew>/crew/lib", "mode_bin": "<krew>/bin/krew-mode", "gpu_mode_file": "..."}
# `lib` holds render_studio (loaded only when this tree carries none), `mode_bin` is the Mode row's switch when this
# tree has no house mode switch of its own, `gpu_mode_file` is the flag that switch writes. No pointer = no studio
# (its routes answer ok:false with the reason) and the render module's switch and flag, as before.
KREW_RENDER_POINTER = STATE / "krew-render.json"


def _krew_render() -> dict:
    try:
        d = json.loads(KREW_RENDER_POINTER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


_KREW = _krew_render()
# the GPU mode flag: the house's mode switch and the render module (modules/render) read the same rule; krew's
# switch writes its own (named in the pointer), read when this machine names none
GPU_MODE_FILE = Path(os.environ.get("HOUSE_GPU_MODE_FILE") or _machine_path(
    "gpu_mode_file", Path(os.path.expanduser(str(_KREW.get("gpu_mode_file") or ""))) if _KREW.get("gpu_mode_file")
    else STATE / "render" / "gpu-mode"))
PROD_LOCK_FILE = STATE / "house-prod-lock"
GPU_STATE_FILE = STATE / "house-gpu-state.json"
GPU_PENDING_FILE = Path(os.environ.get("HOUSE_GPU_PENDING_FILE") or (STATE / "house-gpu-pending"))
TRAIN_HOLD_FILE = Path(os.environ.get("HOUSE_TRAIN_HOLD", "/tmp/house-train-hold"))
# Crissa's gateway is the one the house mode switch stops in Render (2026-08-28); the
# dispatcher stays up and is bounced, not parked. Both rows are this machine's (config).
RENDER_SLEEP_UNITS = frozenset(_PANEL.get("render_sleep_units") or [])
RENDER_WAKE_UNITS = frozenset(_PANEL.get("render_wake_units") or [])
# The mode switch the Mode row runs: the house's own (scripts/house-mode, which parks and wakes its brains), else
# the render module's (modules/render: the flag and Comfy alone, the User 2026-10-09 parity build). Neither in
# the tree = the Mode row says so.
_RENDER_MODULE_BIN = ROOT / "modules" / "render" / "scripts" / "crissa-render.py"
_KREW_MODE_BIN = Path(os.path.expanduser(str(_KREW.get("mode_bin") or ""))) if _KREW.get("mode_bin") else None
HOUSE_MODE_BIN = Path(os.environ.get("HOUSE_MODE_BIN") or (
    ROOT / "scripts" / "house-mode" if (ROOT / "scripts" / "house-mode").is_file()
    else _KREW_MODE_BIN if _KREW_MODE_BIN is not None and _KREW_MODE_BIN.is_file() else _RENDER_MODULE_BIN))


# ALL OS OFF IS ALL OF IT (the User 2026-10-25: "I want AllOS off to shut it all down. We have a Wall on/off").
# On: the panel's All OS Off runs lib/house_os_power.py off (every house unit down but this panel, snapshot first),
# and while that snapshot is in force Standard / Render / Beast run `on --mode` (exactly the snapshot back, then the
# mode). Off (default): the old GPU park through house-mode, byte for byte.
OS_POWER_FULL = os.environ.get("HOUSE_OS_POWER", "0").strip().lower() in ("1", "true", "yes", "on")
OS_POWER_BIN = ROOT / "lib" / "house_os_power.py"
OS_POWER_LOG = crissa_home.HOME / "house-os-power.log"      # the state directory (the house's own, by its manifest)
_OS_POWER_PROC: "subprocess.Popen | None" = None
_OS_POWER_MODES = {"mode_normal": "normal", "mode_render": "render", "mode_beast": "beast"}


def os_power_route(act: str, *, popen=subprocess.Popen) -> "dict | None":
    """The panel's mode keys under HOUSE_OS_POWER=1. None = not ours (the GPU path handles it)."""
    global _OS_POWER_PROC
    if not OS_POWER_FULL or not OS_POWER_BIN.is_file():
        return None
    try:
        import house_os_power
        snap = house_os_power.read_snapshot()
    except Exception as e:  # noqa: BLE001 - never 500 the panel
        return {"ok": False, "action": act, "error": f"os power: {type(e).__name__}: {e}"[:160]}
    if act == "mode_off":
        argv, note = [sys.executable, str(OS_POWER_BIN), "off"], (
            "All OS Off — every house service stops but this panel. Standard, Render or Beast turns it all back on.")
        if snap:
            return {"ok": True, "action": act, "already": True, "os_power": True,
                    "note": "Already All OS Off — %d units wait in the snapshot" % len(snap.get("units") or [])}
    elif act in _OS_POWER_MODES and snap:
        mode = _OS_POWER_MODES[act]
        argv = [sys.executable, str(OS_POWER_BIN), "on", "--mode", mode]
        note = "All OS On — %d units come back, then %s" % (len(snap.get("units") or []), GPU_LABELS.get(mode, mode))
    else:
        return None
    if _OS_POWER_PROC is not None and _OS_POWER_PROC.poll() is None:
        return {"ok": True, "action": act, "started": False, "pending": True, "os_power": True,
                "note": "All OS Off / On is already running — see %s" % OS_POWER_LOG}
    try:
        OS_POWER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with OS_POWER_LOG.open("ab") as log:
            log.write(("\n== %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), " ".join(argv[1:]))).encode())
            log.flush()
            _OS_POWER_PROC = popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(ROOT))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": act, "error": f"{type(e).__name__}: {e}"[:160]}
    return {"ok": True, "action": act, "started": True, "os_power": True, "note": note}


def _render_studio():
    """lib/render_studio: this tree's own, else krew's (the pointer's `lib`); ImportError with the reason when neither.

    krew's lib is appended LAST to sys.path, so every module this tree carries still wins and only the studio and the
    modules it imports lazily that this tree lacks resolve to krew's copies."""
    try:
        import render_studio
        return render_studio
    except ImportError as e:
        if e.name != "render_studio":
            raise
    lib = str(_KREW.get("lib") or "")
    if not lib:
        raise ImportError(f"no Render studio in this edition (krew not installed: no {KREW_RENDER_POINTER})",
                          name="render_studio")
    lib = os.path.expanduser(lib)
    if not os.path.isfile(os.path.join(lib, "render_studio.py")):
        raise ImportError(f"no render_studio.py in {lib} (named by {KREW_RENDER_POINTER})", name="render_studio")
    if lib not in sys.path:
        sys.path.append(lib)
    import importlib
    importlib.invalidate_caches()
    try:
        import render_studio
    except ImportError as e:
        raise ImportError(f"krew's render_studio in {lib} does not import: {e}", name="render_studio") from e
    return render_studio
HOUSE_TURBO_KREW_BIN = ROOT / "scripts" / "house-turbo-krew.py"
# :8190/panel/surface-mode refuses voice-only while the mouth lock is off
# ("Voice-OFF cannot be voice-only"). Display mode and the Voice master are
# different switches — do not call that route. Write the kiosk flag here.

PAGE = Path(__file__).resolve().parent.parent / "display" / "voiceonly-home.html"

# display/voiceonly-home.html is immutable (lsattr i). Inject barge-in toast
# and SSE mouth captions at serve time.
_INTERRUPT_TOAST_JS = (
    b"<script>(function(){"
    b"if(window.__interruptToastWrap)return;"
    b"window.__interruptToastWrap=true;"
    b"var last='';"
    b"function upsertMouth(z){"
    b"if(!z||!z.speaking)return;"
    b"var t=String(z.last_reply||'').trim();"
    b"var who=String(z.who||'crissa').toLowerCase();"
    b"if(!t||who==='announcer')return;"
    b"if(typeof lastLineNode!=='undefined'&&lastLineNode&&lastLineWho===who){"
    b"var lt=lastLineText||'';"
    b"if(t===lt||t.indexOf(lt)===0||lt.indexOf(t)===0){"
    b"if(t.length>=lt.length){"
    b"var said=lastLineNode.querySelector&&lastLineNode.querySelector('.said');"
    b"if(said)said.textContent=t; lastLineText=t;"
    b"}return;}}"
    b"if(typeof rollPush!=='function'||typeof document==='undefined')return;"
    b"var row=document.createElement('div');"
    b"row.className='line from-speech';"
    + (b"var w=document.createElement('div'); w.className='who'; w.textContent=who;" if crissa_home.is_house() else
       b"var w=document.createElement('div'); w.className='who'; w.textContent=(/^(crissa|zoe|crissa_her_tts)$/i.test(who)?'crissa':who);")
    + b""
    b"var s=document.createElement('div'); s.className='said'; s.textContent=t;"
    b"row.appendChild(w); row.appendChild(s);"
    b"rollPush(row);"
    b"lastLineNode=row; lastLineText=t; lastLineWho=who;"
    b"if(typeof noteSpeech==='function')noteSpeech();"
    b"}"
    b"function wrap(fn){return function(h){fn(h);try{"
    b"var z=h&&h.crissa;"
    b"var n=z&&z.notice;"
    b"if(n&&n!==last){last=n;"
    b"if(typeof toast==='function')"
    b"toast(n==='interrupted'?'Interrupted \\u00b7 message cancelled':String(n));"
    b"}"
    b"upsertMouth(z);"
    b"}catch(e){}}};"
    b"if(typeof ingestSound==='function'){"
    b"ingestSound=wrap(ingestSound);"
    b"window.__voIngestSound=ingestSound;}"
    b"})();</script></body>"
)
# Persistent seconds readout (User 2026-08-30, carried card t_b3a206c8 ->
# t_dc823928). The page's own clock() stamps #time with h:mm on a 10s tick and
# never paints seconds, so the room's clock read as frozen between refreshes.
# The HTML is lsattr i, so this rides the same serve-time injection as the
# barge-in toast: a style block plus a 1s ticker that inserts a .time-sec span
# right before the meridiem span. clock() rewrites #time.innerHTML on its 10s
# tick, which drops the span; the next 1s tick re-inserts it, so the seconds
# live continuously with at most a 1s blip at each :00 minute. Tabular nums
# (inherited from .time) + a 2ch min-width keep the digits from shifting the
# meridiem. Runs in the page's own timezone; the kiosk is a live browser, so
# nothing here needs a server-side clock.
_TIME_READOUT_JS = (
    b"<style>.time .time-sec{font-size:.42em;letter-spacing:.14em;"
    b"color:var(--ink-3);margin-left:.35em;vertical-align:.55em;"
    b"min-width:2ch;display:inline-block;text-align:left}</style>"
    b"<script>(function(){"
    b"if(window.__voTimeSecs)return;"
    b"window.__voTimeSecs=true;"
    b"function tick(){"
    b"var el=document.getElementById('time');"
    b"if(!el)return;"
    b"var old=el.querySelector('.time-sec');"
    b"if(old)old.parentNode.removeChild(old);"
    b"var d=new Date();"
    b"var s=String(d.getSeconds()).padStart(2,'0');"
    b"var span=document.createElement('span');"
    b"span.className='time-sec';"
    b"span.textContent=s;"
    b"var mer=el.querySelector('.mer');"
    b"if(mer){el.insertBefore(span,mer);}else{el.appendChild(span);}"
    b"}"
    b"tick();"
    b"setInterval(tick,1000);"
    b"})();</script></body>"
)
_WIZARD_ASSETS = (
    b'<link rel="stylesheet" href="/init-wizard.css?v=glass1">'
    b'<script src="/init-wizard.js?v=glass1"></script>'
)
# Sage dropped because door-watch can't run (joystick off the door).
# Keyboard hold stays green (faces do not matter; casuals stay). 1 s, then clear.
_CAM_NOTE_JS = (
    b"<script>(function(){"
    b"if(window.__camNoteToast)return;"
    b"window.__camNoteToast=true;"
    b"function show(n){"
    b"if(typeof toast!=='function')return;"
    b"toast(n||'SECURE OFF \\u00b7 CAMERA IS YOURS');"
    b"setTimeout(function(){var t=document.getElementById('toast');"
    b"if(t)t.classList.remove('on');},1000);}"
    b"var orig=typeof joyPost==='function'?joyPost:null;"
    b"if(!orig)return;"
    b"joyPost=function(payload){"
    b"if(JOY.busy)return;"
    b"JOY.busy=true;"
    b"fetch('/v1/action',{method:'POST',headers:{'Content-Type':'application/json'},"
    b"body:JSON.stringify({action:'camera_aim',payload:payload})})"
    b".then(function(r){return r.json();}).catch(function(){return null;})"
    b".then(function(d){JOY.busy=false;"
    b"var res=d&&d.result;if(res&&res.secure_dropped)show(res.note);});"
    b"};"
    b"})();</script></body>"
)
# User on the glass is presence.user_auth (house.auth echo). Do not mint
# User from sage colour while the camera says Guest (the User 2026-09-09).
_WHO_AUTH_JS = (
    b"<script>(function(){"
    b"if(window.__whoAuthSot)return;"
    b"window.__whoAuthSot=true;"
    b"isAuthed=function(){"
    b"var h=house||{};"
    b"if(h.auth&&h.auth.user_auth===true)return true;"
    b"var p=h.presence||{};"
    b"if(p.user_auth===true)return true;"
    b"if((p.user||{}).auth===true)return true;"
    b"if((SPK.stack||{}).user)return true;"
    b"return false;"
    b"};"
    b"})();</script></body>"
)


# THE PAGE'S TWO ADDRESSES (2026-10-10): the pip frame's visor (the presence service) and FEED_BASE (the player: its
# /frame.jpg and /v1/state) are written into the page at serve time from this install's port table, so a pack beside
# another install shows its own camera and its own player. The page carries __VO_PRESENCE__ / __VO_PLAYER__.
def page_addresses() -> dict[str, str]:
    player = (os.environ.get("HOUSE_WALL_CONDUCTOR") or crissa_home.house_value("wall_conductor")
              or crissa_ports.url("player"))
    return {"__VO_PRESENCE__": PRESENCE_BASE, "__VO_PLAYER__": player.rstrip("/")}


# A PACK'S PANEL IS CRISSA'S (the User 2026-10-10, .101's screenshots: "ZOS house controls"). The house keeps its crown.
PACK_CROWN = ((b'<span class="wordmark"><b>Z</b>OS</span>', b'<span class="wordmark">Crissa</span>'),
              (b'<span class="crown-sub">House controls</span>', b'<span class="crown-sub">Controls</span>'),
              (b"<title>Control Panel</title>", b"<title>Crissa Controls</title>"))


def panel_page_html() -> bytes:
    """The Control Panel page with this install's player (the Lila poll's fallback) and demo helper written in."""
    raw = PANEL_PAGE.read_bytes().replace(b"__VO_DEMO__", crissa_ports.url("demo").encode())
    if not crissa_home.is_house():
        for a, b in PACK_CROWN:
            raw = raw.replace(a, b, 1)
    raw = raw.replace(b"__VO_PLAYER__", page_addresses()["__VO_PLAYER__"].encode())
    # THE OFF KEY BY EDITION (the fold, 2026-10-28): the house's key is All OS Off (the GPU park, or every service under
    # HOUSE_OS_POWER=1); a pack's is Park GPU, a toggle, with the whole-house switch apart (lib/crissa_os_power)
    for k, v in (PANEL_OFF_HOUSE if crissa_home.is_house() else PANEL_OFF_PACK).items():
        raw = raw.replace(k.encode(), v.encode("utf-8"))
    return her_named(raw)


PANEL_OFF_HOUSE = {
    "__GPU_OFF_LEGEND__": "all os off",
    "__GPU_OFF_WORD__": "All OS Off",
    "__GPU_OFF_TIP__": ("Clears VRAM and RAM \u2014 pure computer, as if a brand-new Ubuntu. Parks ops, crew, the voice, "
                        "Comfy. Ear and presence stay on CPU. House goes mute. Not a host poweroff. Confirm on click."),
}
PANEL_OFF_PACK = {
    "__GPU_OFF_LEGEND__": "park gpu",
    "__GPU_OFF_WORD__": "Park GPU",
    "__GPU_OFF_TIP__": ("Park GPU \u2014 ops, crew, the voice and Comfy off the card. Everything else keeps running (for the "
                        "whole house, use All OS Off below). It is a toggle: press it again to return to the mode you "
                        "were in."),
}


def proc_tape(name: str, lines: int = 200) -> dict:
    """The last lines of one launcher piece's own log (lib/crissa_procs: the table names it), never a path from the page."""
    try:
        import crissa_procs
        c = (crissa_procs.load(STATE).get("children") or {}).get(name) if crissa_procs.NAME.fullmatch(name) else None
        if not c or not c.get("log"):
            return {"ok": False, "unit": "proc:" + name, "lines": [], "error": "%r is not one of the launcher's pieces" % name}
        got = crissa_procs.read_log(c["log"], lines)
        return {"ok": True, "unit": "proc:" + name, "lines": list(got.get("lines") or [])}
    except Exception as e:  # noqa: BLE001  a tape never 500s the panel
        return {"ok": False, "unit": "proc:" + name, "lines": [], "error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def pack_voice() -> dict:
    """A pack's EAR row and Mic key: the ear's own lock (GET /v1/house/voice on the ear), the one source the :8097
    panel's Mic key and lamp read too (the User 2026-10-10: the row said "off" while the ear listened - it read the
    house's voice record, which a pack's upstream does not carry). An ear that does not answer is said, never on."""
    st, body, _ = fetch(STT_BASE + "/v1/house/voice", timeout=0.6)
    if st != 200 or not body:
        return {"voice_on": False, "ear": False}
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return {"voice_on": False, "ear": False}
    return {"voice_on": str((d or {}).get("voice") or "off").strip().lower() == "on", "ear": True}


def pack_fleet(rows: list[dict], cap: dict | None = None) -> tuple[list[dict], dict]:
    """A pack's rows as the page shows them and the header's count over exactly those (the User 2026-10-10, .101:
    "2 UNITS DOWN" over a GPU voice this computer cannot run). A launcher piece this computer cannot run (Turbo,
    Multilingual, the keep-warm without an NVIDIA GPU) is folded into "Not on this computer", never counted; each
    down or warn tile is named."""
    feats = (cap or {}).get("features") or {}
    need = {"proc:turbo": "turbo", "proc:multilingual": "multilingual", "proc:warm": "keep_warm"}
    shown = []
    for r in rows:
        f = feats.get(need.get(str(r.get("unit") or ""), ""))
        if f and f.get("ok") is False:
            continue
        shown.append(r)
    counted = [r for r in shown if not r.get("link") and not r.get("report") and not r.get("placeholder")]
    down = [str(r.get("label") or r.get("unit")).split(" - ")[0] for r in counted if r.get("health") == "bad"]
    warn = [str(r.get("label") or r.get("unit")).split(" - ")[0] for r in counted
            if r.get("health") == "warn" and not r.get("parked")]
    return shown, {"total": len(counted), "ok": sum(1 for r in counted if r.get("health") == "ok"),
                   "warn": len(warn), "down": len(down), "down_names": down, "warn_names": warn}


def pack_running() -> dict:
    """A pack's header count, "N units running": THE ONE COUNT the Control Panel (:8097) says too, read from the
    launcher's table by lib/crissa_procs.running_count (the User 2026-10-10, .101 round 5: 16 here, 24 there)."""
    try:
        import crissa_procs
        if not crissa_procs.load(STATE):
            return {}
        got = crissa_procs.running_count(STATE)
        return {"running": int(got["running"]), "running_what": got["what"]}
    except Exception as e:  # noqa: BLE001  a count never 500s the panel
        log(f"pack running count: {type(e).__name__}: {e}")
        return {}


def page_html() -> bytes:
    raw = PAGE.read_bytes()
    for mark, val in page_addresses().items():
        raw = raw.replace(mark.encode(), val.encode())
    label, tip = composer_key()
    raw = raw.replace(b"__COMPOSER_LABEL__", label.encode("utf-8")).replace(b"__COMPOSER_TIP__", tip.encode("utf-8"))
    raw = raw.replace(b"__VO_ROOM__", json.dumps(wall_room(), separators=(",", ":")).encode("utf-8"))
    # the speaker row names her while she talks in a pack (2026-10-12); the house's row holds (the User 09-07)
    raw = raw.replace(b"__SPEAKER_NAMES_HER__", b"" if crissa_home.is_house() else b"__HER_NAME__")
    raw = her_named(raw)
    # One </body> closes the document: each injected block carries its own
    # closer, so strip the readout's and fold every block into the toast's.
    extra = _INTERRUPT_TOAST_JS.replace(
        b"</body>",
        _TIME_READOUT_JS.replace(b"</body>", b"", 1)
        + _CAM_NOTE_JS.replace(b"</body>", b"", 1)
        + _WHO_AUTH_JS.replace(b"</body>", b"", 1)
        + _WIZARD_ASSETS
        + b"</body>",
        1,
    )
    if b"</body>" in raw:
        return raw.replace(b"</body>", extra, 1)
    return raw + extra

# Register overlay on the wall. Python POSTs phase/text; the page polls this
# so the sentence does not depend on coach SSE surviving a proxy blip.
REGISTER_GLASS: dict = {"phase": "idle", "text": "", "n": 0, "ts": 0.0}
REGISTER_GEN: dict = {"n": 0}


def set_register_glass(phase: str, text: str = "", *, pip: str = "", line: str | None = None,
                       seconds: float | None = None, beep: bool = False, mode: str = "") -> dict:
    REGISTER_GLASS["phase"] = str(phase or "idle")
    REGISTER_GLASS["text"] = str(text or "")
    REGISTER_GLASS["pip"] = str(pip or "")
    # the take sentence, handed over WITH the intro so the wall can surface it
    # when the host's eyes drop to it, before the first beep (the User 2026-09-09);
    # kept through the phases, cleared on idle/error
    if line is not None:
        REGISTER_GLASS["line"] = str(line or "")
    elif phase in ("idle", "error"):
        REGISTER_GLASS["line"] = ""
    # the wall host's take bar and beep flash (2026-09-11): seconds is the
    # open take window, beep stamps the instant the tone went out; both
    # clear with the phase so a stale bar never drains on the next take
    try:
        REGISTER_GLASS["seconds"] = float(seconds) if seconds else 0.0
    except (TypeError, ValueError):
        REGISTER_GLASS["seconds"] = 0.0
    if beep:
        REGISTER_GLASS["beep_ts"] = time.time()
    elif phase in ("idle", "error", "done", "thanks", "watch"):
        REGISTER_GLASS["beep_ts"] = 0.0
    REGISTER_GLASS["mode"] = str(mode or "")
    REGISTER_GLASS["n"] = int(REGISTER_GLASS.get("n") or 0) + 1
    REGISTER_GLASS["ts"] = time.time()
    return dict(REGISTER_GLASS)
def register_glass_view(fetch_player=None) -> dict:
    """GET /v1/register. In a pack the Register routine writes its glass to the player's conductor (lib/crissa_init),
    never here, so while this copy is idle the player's is the answer (the User 2026-10-10, .101 V: a page reading
    this feed never saw the sentence to read). The house keeps its own copy, as before."""
    own = dict(REGISTER_GLASS, ok=True)
    if crissa_home.is_house() or str(REGISTER_GLASS.get("phase") or "idle") != "idle":
        return own
    if fetch_player is None:
        def fetch_player():
            st, body, _ = fetch(page_addresses()["__VO_PLAYER__"] + "/v1/register", timeout=0.8)
            return json.loads(body) if st == 200 and body else None
    try:
        got = fetch_player()
    except Exception:  # noqa: BLE001
        got = None
    if isinstance(got, dict) and got.get("phase"):
        return dict(got, ok=True, source="player")
    return own


PANEL_PAGE = Path(__file__).resolve().parent.parent / "display" / "voiceonly-panel.html"
# The framed board. Krew is the agent framework's own React app on :9119, a tree we may
# not patch (its updates reset it), so :8099 serves ONE wrapper that
# frames it and carries the house toolbar — a way back to the wall and the
# Control Panel. Its address is this machine's (config `panel.board_url`); "" in a pack
# (krew's own install names it there). THE COMPOSER IS NOT FRAMED (2026-09-11, the User: "8199 must be
# the improved version"): it is our own page on :8199 and carries the same
# rail itself (lib/house_nav.py), so /composer 302s there. Framing it gave
# two bars and ate ?key= deep links — the "two Composers" the User saw.
FRAME_PAGE = Path(__file__).resolve().parent.parent / "display" / "voiceonly-frame.html"
FRAMED = {
    # route: (button id on the toolbar, tab/frame title, what to frame)
    "/krew": ("rbKrew", "Krew", str(_PANEL.get("board_url") or "")),
}
# Where /composer goes. One origin for the Composer; the wallpaper's COMP
# key, the keeper's home tabs and Crissa's `composer` view already open it.
# its own zoom bucket on the house: Chromium keys zoom by host, not port (2026-09-16). A pack's /composer goes where its
# Crissa key's Composer item goes (lib/crissa_home.composer_url: the store), never the house's host (2026-10-10).
COMPOSER_URL = ("https://store.zoeyos.dev/store/" if crissa_home.is_house()
                else crissa_home.composer_url().rstrip("/") + "/")


def frame_html(route: str) -> bytes:
    """The one wrapper, dressed for one board. Two static copies would drift."""
    btn, name, src = FRAMED[route]
    html = FRAME_PAGE.read_text(encoding="utf-8")
    # Light the key you are standing on. The toolbar is byte-identical on every
    # surface, so this is the only per-page difference there is.
    # Anchored on the ID, not on `class="railbtn"`: matching the class literal
    # meant the frame's keys could not also wear `sym` (the 15px symbol size
    # every other surface uses), so the board carried an extra CSS rule to make up
    # the difference by eye. Now its keys block is byte-identical to the CP's.
    html = html.replace(f'id="{btn}"', f'id="{btn}" aria-current="page"', 1)
    return her_named(html.replace("__FRAME_NAME__", name)
                     .replace("__FRAME_SRC__", src)).encode("utf-8")


def her_named(text):
    """The install's name for her in a served page (`__HER_NAME__`: the house's config `her_name`, else Crissa). The
    fold (2026-10-28): the frame, the document sheet and the web manifest were two copies that differed by her name."""
    name = crissa_home.her_name()
    marks = (("__HER_NAME_UP__", name.upper()), ("__HER_NAME_LOW__", name.lower()), ("__HER_NAME__", name))
    if isinstance(text, bytes):
        for k, v in marks:
            text = text.replace(k.encode(), v.encode("utf-8"))
        return text
    for k, v in marks:
        text = text.replace(k, v)
    return text


def composer_key() -> tuple[str, str]:
    """The Wall page's ▶ key, (label, tip): the house's own Composer, a pack's store (its key opens the store, the User
    2026-10-07). Her name is filled after (her_named)."""
    if crissa_home.is_house():
        return "Composer", "Composer \u2014 the cue rack for __HER_NAME__'s body: queue, seams, freelance"
    return "Store", "Store \u2014 packs of new loops for __HER_NAME__"


def wall_room() -> dict:
    """This install's room for the Wall page when Setup taught none: the house's office from config/house-machine.json
    `wall_room` (the literals the page carried until the fold, 2026-10-28); {} in a pack, so the dial knows no room."""
    try:
        got = json.loads(crissa_home.HOUSE_MACHINE.read_text(encoding="utf-8")).get("wall_room")
    except (OSError, ValueError, AttributeError):
        return {}
    return got if isinstance(got, dict) else {}
GALLERY_DIR = Path(__file__).resolve().parent.parent / "display" / "voiceonly-gallery"
TTS_GATE = Path(os.environ.get("HOUSE_TTS_GATE") or (STATE / "house-tts-gate.json")).expanduser()
# Bounded log tails for the Control Panel. Only these three lanes, only the tail,
# never an arbitrary path from the page — the same allowlist discipline as ACTIONS.
# Camera is the door-glance visor's log (product ID), not door-fanout / Argus mill. Which files: this machine's
# (config/house-machine.json camera_log_dir / voice_logs / security_logs), else the install's own lane files
# under the state directory - a pack's voice module writes logs/turns.jsonl there.


def _machine_paths(key: str, default: list[Path]) -> list[Path]:
    got = crissa_home.house_value(key)
    if not got:
        return default
    return [Path(x.strip()).expanduser() for x in got.split(",") if x.strip()]


CAMERA_LOG_DIR = _machine_path("camera_log_dir", STATE / "camera_log")
LOG_LANES: dict[str, list[Path]] = {
    "camera": [],  # resolved live from CAMERA_LOG_DIR
    "voice": _machine_paths("voice_logs", [STATE / "logs" / "turns.jsonl", STATE / "logs" / "house-speak.jsonl"]),
    #           crystal-gate.jsonl looked right by name and is not: it is the
    #           crystal plugin's own error log, 71 identical lines. The
    #           refusals live in store_face (ok:false + reason).
    "security": _machine_paths("security_logs", [STATE / "logs" / "house-commands.jsonl",
                                                 STATE / "logs" / "store_face.jsonl"]),
}
LOG_TAIL_LINES = int(os.environ.get("VO_LOG_TAIL", "80"))
# Long enough to scroll sideways in the panel. Don't ship a multi-meg line.
LOG_LINE_MAX = int(os.environ.get("VO_LOG_LINE_MAX", "8192"))
INCIDENTS_DIR = Path(
    os.environ.get("RAIL_SECURITY_INCIDENTS")
    or (Path(os.environ.get("RAIL_INSPECTOR_LOG") or _machine_path("inspector_log", STATE / "inspector")) / "incidents")
)
DOC_PAGE = Path(__file__).resolve().parent.parent / "display" / "voiceonly-doc.html"
# How long a document may sit before it clears itself. the User's rule: when we move
# on, it goes away on its own — so there is no such thing as a permanent doc.
DOC_TTL_DEFAULT = float(os.environ.get("VO_DOC_TTL", "900"))
# Letter sheet. Crissa is supposed to home/clear when the look is over.
# 15 min is a forgotten leftover (Vera smoke-test loop, 2026-08-19), not
# the plan. Hover × is the hands-on hatch.
NOTES_TTL_DEFAULT = float(os.environ.get("VO_NOTES_TTL", "900"))
# Ceiling on how long the roll holds a piece still. Soft: any log line breaks it.
ART_HOLD_DEFAULT = float(os.environ.get("VO_ART_HOLD", "90"))
ART_HOLD_MAX = 600.0
STATE_PATH = Path(os.environ.get("VO_SCREEN_STATE") or _machine_path("screen_state", STATE / "crissa-screen.json"))

# Crissa's vocabulary. Unknown values fall back rather than erroring — a bad post
# must never blank the room's screen.
MOODS = ("resting", "attending", "speaking", "alert", "away")
FOCI = ("none", "clock", "room", "transcript")
# --- shape: Crissa composes the FORM, not just the words (User 2026-08-10) ---
# figure = what occupies the ambient ground
#   room    live door camera as ASCII (default)
#   portrait centre-cropped, high-contrast render — figurative, per User's
#            ASCII-portrait reference
#   field   abstract breathing field, no camera
#   art     Crissa's OWN ascii in `art` (falls back to room if she sent none)
#   off     bare ground
#
# 2026-08-13: `art` NO LONGER takes the ambient ground. On 08-13 a placeholder
# card ("NEWS HEADLINE OF THE DAY / Waiting for headline feed...") was posted
# here and centred itself over the whole room — the User: "Where's the ASCII art?
# It's blinking off and being overtaken by ugly stamps." Art now rides the log
# roll on the left. `art` stays in FIGURES so old posts are accepted, but the
# page treats it as `room`.
FIGURES = ("room", "portrait", "field", "art", "off")
# instrument = what the right side plots
INSTRUMENTS = ("clock", "day", "levels", "none")
DENSITIES = ("airy", "normal", "dense")
# What her face is doing on her own channel. She posts one the same way she
# posts a mood or a piece of art:  {"face": "smile"}. Modulations of the same
# features — none of them change who the figure is.
FACES = ("neutral", "smile", "grin", "wink", "surprise", "thinking", "sad")
ACCENTS = ("warm", "neutral", "cool")

# --- density ramps (measured 2026-08-13, not guessed) ---
# Each glyph was rendered at 48px in its own font and its ink-per-cell measured.
# The finding that shaped this table: CJK is NOT denser than ASCII (darkest CJK
# 黼 = 0.299 ink/cell, ASCII M = 0.271) — full-width glyphs spread their ink
# over two cells. The real density surrogates are the block elements, which
# reach 1.000. Density and resolution are separate axes; nothing installed here
# maxes both, so Crissa picks the ramp that fits the piece.
#
#   ramp      ink range     resolution   note
#   ascii     0.00-0.27     1x1          grandpa art, renders anywhere
#   cjk       0.00-0.30     1x1          full-width texture, not extra density
#   shade     0.00-1.00     1x1          flat tone
#   eighth    0.00-1.00     1x1          linear 9-step ladder, best tone
#   quadrant  0.00-1.00     2x2          best all-round for hi-res
#   braille   0.06-0.25     2x4          most detail, cannot go dark
#
# Sextants (2x3) are deliberately absent: only Noto Sans Symbols2 has real ones
# and they measure two cells wide, which breaks the grid. Unifont's are .notdef.
ART_RAMPS: dict[str, dict] = {
    "ascii":    {"font": "mono",    "probe": "M"},
    "cjk":      {"font": "cjk",     "probe": "黼"},
    "shade":    {"font": "dejavu",  "probe": "▓"},
    "eighth":   {"font": "dejavu",  "probe": "▅"},
    "quadrant": {"font": "dejavu",  "probe": "▟"},
    "braille":  {"font": "unifont", "probe": "⣿"},
}
# CSS font stacks the page knows how to apply. Braille only renders in Unifont —
# DejaVu Sans Mono and Noto CJK draw .notdef boxes for it, which is how a wall
# of tofu happens.
ART_FONTS = ("mono", "dejavu", "cjk", "unifont")
# Her canvas. Big enough for real ASCII art, small enough that a runaway
# generation can't wedge the page. Rendered as textContent, never as HTML.
ART_MAX_CHARS = 12000
ART_MAX_LINES = 120
# The notes surface (Claude 2026-08-11). headline/note are 120/160 chars of
# serif in a ~17ch column — a board summary posted there wrapped into a thin
# tower down the left edge. This is the readable body: monospace, wide, scrolls.
NOTES_MAX_CHARS = 64000
NOTES_MAX_LINES = 2000

_lock = threading.RLock()
_screen: dict = {
    "mood": "resting",
    "headline": None,
    "note": None,
    "focus": "none",
    "chips": [],
    "figure": "room",
    "instrument": "clock",
    "density": "normal",
    "accent": "warm",
    "face": "neutral",
    # Hybrid only: let HER scene ride the kiosk hole the way the camera does —
    # the window scans its own slice of the frame instead of rescaling it.
    # Off by default, because her framing is the one that reads best.
    "crissa_hole": False,
    # How wide a piece of the plane the hybrid hole scans. 1.0 is the camera's
    # 1:1. Below 1 widens — her 736x400 clip is otherwise magnified ~3.6x by a
    # plane pinned to screen height, so the window sees a sliver of her.
    "fov_zoom": 1.0,
    # HER channel's hole width. The camera's FOV already spans the desktop so
    # 1.0 is its 1:1; her 736x400 clip against a screen-height plane is a ~3.6x
    # magnification, so 1.0 on her channel is always the smear. One shared
    # default meant clicking AR was guaranteed to look broken. Measured
    # 2026-08-29 (kiosk x=120 y=187 1280x923 -> source x 46-401, y 52-308).
    "fov_zoom_crissa": 1.0,   # 2026-09-07: her hole samples the wallpaper's ZFRAME plane; 1.0 = its own scale
    "art": None,
    "art_label": None,
    "art_ramp": None,   # which density ramp the piece is drawn in
    "art_hold": None,   # epoch until which the roll holds it still (soft)
    # Bumped every time a piece is posted, so the page can tell "she posted this
    # again" from "the poll returned the same thing". Keying on the art text
    # alone meant re-posting a piece silently did nothing and its hold never
    # armed; keying on `rev` would re-arm on every unrelated screen change.
    "art_seq": 0,
    "notes": None,
    "notes_title": None,
    "notes_ttl": None,  # epoch; None on disk is armed to NOTES_TTL_DEFAULT
    "prev_headline": None,  # tracks which headline the notes belong to
    # The document surface. This does NOT render on the wall — it is served at
    # GET /doc and opened in its own window on top, so the right side of the
    # room (dial, speaker location, stars) is never covered. It always carries a
    # TTL so a forgotten document cannot sit on the glass.
    "doc": None,
    "doc_title": None,
    # One picture on the doc sheet. A TAG, never a path — the route below
    # resolves it, so a caller can never point the wall at an arbitrary file.
    "doc_mug": None,
    "doc_ttl": None,
    "doc_font": None,
    "rev": 0,          # bumps on every accepted post; the page animates on change
    "ts": 0.0,
}

# Frame cache — the camera is a shared rail; don't hammer it once per client.
_frame_lock = threading.Lock()
_frame: bytes | None = None
_frame_ts = 0.0
FRAME_TTL = float(os.environ.get("VO_FRAME_TTL", "1.0"))


def log(msg: str) -> None:
    print(f"[vo-home] {msg}", flush=True)


def resolve_ttl(val, default: float, now: float | None = None) -> float:
    """Seconds-from-now or epoch → epoch. None/invalid → now+default (min 5s)."""
    now = time.time() if now is None else now
    if val is None:
        return now + max(5.0, float(default))
    try:
        n = float(val)
    except (TypeError, ValueError):
        return now + max(5.0, float(default))
    if n >= 1_000_000_000:
        return n
    return now + max(5.0, n)


# THE SHARED GLASS PREFS (docs/glass-contract.yaml prefs.shared): FT/ROOM, size,
# Blur, the LOGS chips, the folds, the oracle dot — one record both glasses
# read from /v1/house["glass"] and write through POST /v1/glass-prefs.
# Last-write-wins by rev; an empty store hydrates from the wallpaper's file.
_glass = GlassPrefs()

# One label law for the GPU mode word: the Control Panel's names.
GPU_LABELS = {"render": "Render", "normal": "Standard", "beast": "Beast", "off": "All OS Off",
              "heavy-render": "Heavy Render"}


def load_state() -> None:
    global _screen
    try:
        if STATE_PATH.is_file():
            d = json.loads(STATE_PATH.read_text())
            if isinstance(d, dict):
                with _lock:
                    _screen.update({k: d[k] for k in d if k in _screen})
                    if _screen.get("notes") and _screen.get("notes_ttl") is None:
                        _screen["notes_ttl"] = time.time() + NOTES_TTL_DEFAULT
                log(f"restored screen state from {STATE_PATH}")
    except Exception as e:
        log(f"state load failed ({e!r}) — starting on defaults")


def save_state() -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            STATE_PATH.write_text(json.dumps(_screen, indent=2) + "\n")
    except Exception as e:
        log(f"state save failed: {e!r}")


_FONT_FAMILY = {
    "mono":    "DejaVu Sans Mono",
    "dejavu":  "DejaVu Sans Mono",
    "cjk":     "Noto Sans Mono CJK JP",
    "unifont": "Unifont",
}


def probe_ramps() -> set[str]:
    """Which ramps this box can actually draw, asked of fontconfig once.

    A ramp whose glyphs the font does not carry renders as a wall of .notdef
    boxes. Cheaper to find that out at boot than on the wall.
    """
    ok: set[str] = set()
    for name, spec in ART_RAMPS.items():
        fam = _FONT_FAMILY.get(spec["font"], "DejaVu Sans Mono")
        try:
            out = subprocess.run(
                ["fc-list", f":charset={ord(spec['probe']):x}:family={fam}", "family"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if out:
                ok.add(name)
            else:
                log(f"ramp {name!r} unavailable — {fam} lacks {spec['probe']!r}")
        except Exception as e:
            # fontconfig missing or slow: trust the ramp rather than blank her
            # palette. A tofu wall is recoverable; an empty palette is not.
            log(f"ramp probe failed for {name!r} ({e!r}) — allowing")
            ok.add(name)
    return ok


RAMPS_OK: set[str] = set()

# Bookkeeping the page has no business seeing. Absolute epochs in particular:
# the page polls, so it would race the server on when a thing expires.
_INTERNAL = ("notes_ttl", "prev_headline", "doc_ttl", "art_hold")


def public_screen() -> dict:
    """The screen as the page is allowed to see it. Call with _lock held."""
    s = dict(_screen)
    for k in _INTERNAL:
        s.pop(k, None)
    # The page needs to know how much longer to hold art still, but a wall clock
    # it cannot trust is useless — hand it seconds remaining instead.
    hold = _screen.get("art_hold")
    s["art_hold_s"] = max(0.0, round(hold - time.time(), 1)) if hold else 0.0
    return s


def _voice_on_from_house(house: dict) -> bool:
    v = house.get("voice") or {}
    return bool(
        v.get("voice_on") is True
        or (v.get("voice") or {}).get("voice") == "on"
    )


def screen_for_house(house: dict) -> dict:
    """Screen for the wall poll. Voice lock owns Listening / Speaking.

    The page prefers ``screen.mood`` over ``voice_on``. A leftover
    ``attending`` (measured 2026-08-18) kept LISTENING up after Voice off.
    """
    s = public_screen()
    if _voice_on_from_house(house):
        return s
    if s.get("mood") in ("attending", "speaking"):
        s["mood"] = "away"
        if _screen.get("mood") in ("attending", "speaking"):
            _screen["mood"] = "away"
            log("mood attending/speaking dropped — Voice is off")
    return s


def _doc_window(cmd: str) -> None:
    """Float or kill the sheet-sized doc window. Fire-and-forget."""
    if cmd not in ("open", "close"):
        return
    if not DOC_WINDOW_SH.is_file():
        return
    try:
        subprocess.Popen(
            [str(DOC_WINDOW_SH), cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log(f"doc-window {cmd} failed ({e!r})")


def _clear_expired() -> bool:
    """Expire the timed surfaces: the doc, then notes.

    Must be called with _lock held. Returns True if anything was cleared.
    """
    cleared = False

    # The document always carries a TTL — that is what makes it self-clearing,
    # so the wall comes back on its own when we move on.
    if _screen.get("doc") is not None:
        dttl = _screen.get("doc_ttl")
        if dttl is not None and time.time() > dttl:
            log("doc expired (TTL) — clearing")
            _screen["doc"] = None
            _screen["doc_title"] = None
            _screen["doc_ttl"] = None
            cleared = True
            _doc_window("close")

    return _clear_expired_notes() or cleared


def _clear_expired_notes() -> bool:
    """Clear notes when their TTL has elapsed.

    Untimed leftovers (old 'permanent' posts) get NOTES_TTL_DEFAULT from
    the first poll so a forgotten sheet cannot sit. Must hold _lock.
    """
    if _screen.get("notes") is None:
        return False

    ttl = _screen.get("notes_ttl")
    if ttl is None:
        _screen["notes_ttl"] = time.time() + NOTES_TTL_DEFAULT
        return False
    if time.time() > ttl:
        log("notes expired (TTL) — clearing")
        _screen["notes"] = None
        _screen["notes_title"] = None
        _screen["notes_ttl"] = None
        _screen["prev_headline"] = None
        return True
    return False


def presence_heard() -> tuple[dict | None, bool]:
    """Mic-frame hearing + session look. Presence SoT only.

    Copies Oracle ``people[].short_id`` onto the hearing field so the
    glass never has to guess a letter from the name. ``f2f`` rides the
    sound SSE so attention does not wait on the 2s house poll.
    """
    status, body, _ = fetch(PRESENCE_URL, timeout=1.5)
    if status != 200 or not body:
        return None, False
    try:
        pres = json.loads(body)
    except Exception:
        return None, False
    if not isinstance(pres, dict):
        return None, False
    f2f = bool(pres.get("f2f"))
    s = pres.get("sound")
    if not isinstance(s, dict):
        return None, f2f
    pid = str(s.get("person_id") or "").strip().lower()
    if not pid or s.get("short_id"):
        return s, f2f
    for p in pres.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("person_id") or "").strip().lower()
        sid = p.get("short_id")
        if name == pid and sid:
            out = dict(s)
            out["short_id"] = sid
            return out, f2f
    return s, f2f


def presence_sound() -> dict | None:
    s, _f2f = presence_heard()
    return s


# --- the mouth air clock -------------------------------------------------
# house-speak posts the envelope of what paplay is ABOUT to play. Each frame's
# pts is a future instant, so this is a short score, not a report: the wall
# reads it ahead of the room hearing it and can ease into a syllable.
#
# THE STORE MOVED TO lib/mouth_mailbox.py (2026-09-15) when the Composer got
# a bench conductor that serves its own /v1/mouth. One implementation, not
# two: the 08-27 retire-on-read, the 08-30 new-turn PCM reset and the bounded
# lookahead ring all live in the class now. These names stay bound to this
# process's instance because the routes below and tests/test_w2l_pcm.py call
# them by name.
_MOUTH_BOX = mouth_mailbox.MouthMailbox()
_MOUTH: dict = _MOUTH_BOX.d
_MOUTH_PCM_ROWS = mouth_mailbox.PCM_ROWS
_MOUTH_LOCK = _MOUTH_BOX.lock
_MOUTH_HOLD_S = mouth_mailbox.HOLD_S
_mouth_wake = _MOUTH_BOX.wake

mouth_ingest = _MOUTH_BOX.ingest
mouth_snapshot = _MOUTH_BOX.snapshot


_MOUTH_SINK = {"ts": 0.0, "live": False, "dirty": True, "sub": None}
_MOUTH_SINK_MAX_AGE = 3.0   # re-list even with no event, in case the subscriber died


def _mouth_sink_reap() -> None:
    """End the previous subscriber, if any (also at exit)."""
    pr = _MOUTH_SINK.get("sub")
    if pr is None or pr.poll() is not None:
        return
    try:
        pr.terminate()
        pr.wait(1.0)
    except Exception:  # noqa: BLE001
        try:
            pr.kill()
        except Exception:  # noqa: BLE001
            pass


atexit.register(_mouth_sink_reap)


def _mouth_sink_subscribe() -> None:
    """ONE long-lived `pactl subscribe`; a sink-input event marks the cache
    dirty. Before 2026-09-06 every state build ran `pactl list sink-inputs`
    behind a 0.12 s cache: ~6 short-lived audio clients a second, and every
    other PipeWire client (gsd-media-keys at 13–24 % of a core, gnome-shell)
    paid to watch them come and go.

    2026-09-19: the child leaked. pactl ignores SIGPIPE and never checks its
    writes, so a dead reader never ends it; 31 orphans held 31 of PipeWire's 64
    client slots. It now dies with this process (setpriv --pdeathsig, which a
    SIGKILL cannot skip), a new one never starts beside an old one, and only
    main() starts this thread — an import spawns nothing.
    Kill (plain pactl, as before): HOUSE_PACTL_PDEATHSIG=0."""
    argv = ["pactl", "subscribe"]
    if os.environ.get("HOUSE_PACTL_PDEATHSIG", "1") != "0" and os.path.exists("/usr/bin/setpriv"):
        argv = ["/usr/bin/setpriv", "--pdeathsig", "TERM", *argv]
    while True:
        _mouth_sink_reap()
        try:
            pr = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
            _MOUTH_SINK["sub"] = pr
            for line in pr.stdout or []:
                if "sink-input" in line:
                    _MOUTH_SINK["dirty"] = True
        except OSError:
            pass
        _MOUTH_SINK["dirty"] = True
        time.sleep(2.0)



# RENDER WORK IN PROGRESS (the User 2026-09-18 "show WIP always"): hold Comfy's
# websocket as the house client id while in Render; lib/render_wip.py writes
# state + the preview frame the studio stage polls. Off Render it naps.
# Kill: HOUSE_RENDER_WIP=0.
def _render_wip_thread() -> None:
    try:
        from render_wip import watch_forever
    except Exception as e:  # noqa: BLE001
        print(f"render-wip: not started: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return
    watch_forever(log=lambda m: print(m, file=sys.stderr, flush=True))



def _za():
    """lib/crissa_audio when CRISSA_AUDIO_SHIM=1, else None (today's direct read).

    OFF BY DEFAULT (Phase 4, 2026-09-22). :8099 is resident, but its unit is
    Restart=always: a crash loads whatever is on disk, so an ON default would put
    the glass on the shim without the restart window. The window sets 1 on
    voiceonly-home.service and restarts the glass. Only the sink-input READ is
    behind it; the long-lived subscribe above stays the glass's own child."""
    if os.environ.get("CRISSA_AUDIO_SHIM", "0").strip() != "1":
        return None
    try:
        import crissa_audio
    except ImportError:
        return None
    return crissa_audio


def _her_mouth_row(binary, name) -> bool:
    """The glass's own test, applied to a parsed row exactly as the direct read
    applies it: the binary contains paplay, or the name is exactly paplay.
    On this box /usr/bin/paplay is a symlink to pacat, so its binary reads
    "pacat" and only the name branch ever fires: a paplay started with
    --client-name is NOT hers on either path (cold room, 2026-09-22). The binary
    branch stays for parity on a box where paplay is its own binary. Not the
    shim's is_player: that counts pacat, and it stays so because the duck must
    keep skipping pacat."""
    return "paplay" in str(binary or "").lower() or str(name or "").strip().lower() == "paplay"


def mouth_sink_live() -> bool:
    """True only while paplay occupies a sink (Cosy / PA).

    AEC's standing Echo-Cancel Playback input is NOT her mouth. Measured
    2026-08-28: that stream stays RUNNING idle and would never hard-stop.
    """
    now = time.time()
    if not _MOUTH_SINK["dirty"] and now - float(_MOUTH_SINK["ts"] or 0) < _MOUTH_SINK_MAX_AGE:
        return bool(_MOUTH_SINK["live"])
    # cleared BEFORE the read, either path: a read that times out must not
    # retry on every state build
    _MOUTH_SINK["dirty"] = False
    za = _za()
    if za is not None:
        try:
            rows = za.inputs(timeout=0.4, strict=True)
            live = any(_her_mouth_row(r.get("binary"), r.get("name")) for r in rows)
        except za.AudioUnavailable:
            # "I do not know" keeps the last answer, as the direct read does
            live = bool(_MOUTH_SINK["live"])
        _MOUTH_SINK["ts"] = now
        _MOUTH_SINK["live"] = live
        return live
    live = False
    try:
        r = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=0.4,
        )
        blob = ""
        for line in (r.stdout or "").splitlines():
            if line.startswith("Sink Input"):
                blob = line + "\n"
                continue
            blob += line + "\n"
            low = line.lower()
            if "application.process.binary" in low and "paplay" in low:
                live = True
                break
            if 'application.name = "paplay"' in low:
                live = True
                break
    except (OSError, subprocess.TimeoutExpired):
        live = bool(_MOUTH_SINK["live"])
    _MOUTH_SINK["ts"] = now
    _MOUTH_SINK["live"] = live
    return live


def crissa_mouth() -> dict:
    """Is Crissa's voice on the air? Same gate the ear uses to darken the lobe.

    `speaking` is the official mouth-open flag. `level` is 1 while she is
    broadcasting so the centre rings can bloom like the outer mic lamps.
    `mouth_sink` is paplay on Pulse — glass hard-stops the speak pose when
    this is false even if air_until still lies in the future.
    """
    speaking = False
    ts = 0.0
    notice = ""
    last_reply = ""
    who = ""
    air: dict = {"air_at": None, "air_until": None, "air_firm": False}
    try:
        if TTS_GATE.is_file():
            g = json.loads(TTS_GATE.read_text(encoding="utf-8"))
            if isinstance(g, dict):
                speaking = bool(g.get("speaking"))
                # A dead line's flag is not her voice (10-03: 40 min of 'speaking' hid his beam). lib/tts_gate_stale.
                if speaking:
                    try:
                        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
                        import tts_gate_stale
                        if tts_gate_stale.is_stale(g, sink_live=mouth_sink_live()):
                            speaking = False
                            g = dict(g, air_at=None, air_firm=False)
                    except ImportError:
                        pass
                # The GLASS's clock. Carried through untouched — `speaking`
                # is the ear's gate and must NOT be re-derived from these.
                air["air_firm"] = bool(g.get("air_firm"))
                for k in ("air_at", "air_until"):
                    try:
                        v = g.get(k)
                        air[k] = None if v is None else float(v)
                    except (TypeError, ValueError):
                        air[k] = None
                last_reply = str(g.get("last_reply") or "").strip()
                who = str(g.get("who") or "").strip().lower()
                last = last_reply.lower()
                if last == "announcer" or who == "announcer" or who.endswith("@bench"):
                    # @bench: a Say on the Composer's bench conductor. It is
                    # heard in the room (one sink) and the ear is gated for it
                    # by the same file, but the GLASS must not bloom or caption
                    # a tuning line — the wall is not what the bench paints.
                    speaking = False
                    last_reply = ""
                try:
                    ts = float(g.get("updated_ts") or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                if not speaking and ts:
                    # short tail so the bloom does not clip the last syllable
                    if (time.time() - ts) <= 0.35:
                        speaking = True
                raw_notice = str(g.get("notice") or "").strip()
                if raw_notice and ts and (time.time() - ts) <= 5.0:
                    notice = raw_notice
    except Exception:
        speaking = False
    out = {
        "speaking": speaking,
        "level": 1.0 if speaking else 0.0,
        "ts": ts if speaking else 0.0,
    }
    if notice:
        out["notice"] = notice
    if last_reply:
        out["last_reply"] = last_reply[:800]
        out["who"] = who or "crissa"
    out["air_at"] = air["air_at"]
    out["air_until"] = air["air_until"]
    out["air_firm"] = air["air_firm"]
    # the sink flag votes alone on the wall's lobe and the glass star
    # (house_wall_ascii_render.on_air); the announcer and a bench line are
    # heard in the room but are not Crissa talking (2026-09-18, the filter
    # crissa_mouth() already applies to `speaking` above)
    out["mouth_sink"] = mouth_sink_live() and not (who == "announcer" or who.endswith("@bench"))
    return out


def ear_pierce_ts() -> float | None:
    """Ear two-frame VAD piercing (~0.9s into speech), for the glass peek.

    The earliest honest "someone in this room is talking" the house has, and
    the ONLY signal the peek may use. Never the puck's ``sound.speech``: that
    is the XVF's own VAD, it lights for keystrokes, and it reads true in a
    quiet room.

    Posture only. This does not name anyone, move the camera, or grant
    permission — those stay the Master flag AND on the rail.

    Rides the sound SSE rather than a second page XHR, because one of those
    already failed here (see the services_snapshot note in house_payload).
    Timeout must stay under SOUND_TICK_SEC (0.2s). A hung ear then
    degrades the peek and cannot stall the 5 Hz lobe.

    ``session_open`` rides this same SSE (see sound_payload) so the glass
    pose machine does not wait on the 2s house poll.

    """
    status, body, _ = fetch(STT_HEALTH_URL, timeout=0.08)
    if status != 200 or not body:
        return None
    try:
        data = json.loads(body) or {}
    except Exception:
        return None
    if data.get("voice_on") is not True:
        return None
    ear = data.get("ear")
    if not isinstance(ear, dict):
        return None
    try:
        ts = float(ear.get("pierce_ts") or 0.0)
    except (TypeError, ValueError):
        return None
    return ts or None


def sound_payload() -> dict:
    heard, f2f = presence_heard()
    pay = build_sound_payload(heard)
    pay["crissa"] = crissa_mouth()
    pay["f2f"] = f2f
    st, body, _ = fetch(STT_HEALTH_URL, timeout=0.08)
    voice_on = False
    pierce = None
    in_utt = False
    if st == 200 and body:
        try:
            data = json.loads(body) or {}
        except Exception:
            data = {}
        voice_on = data.get("voice_on") is True
        if voice_on:
            ear = data.get("ear") if isinstance(data.get("ear"), dict) else {}
            try:
                pierce = float(ear.get("pierce_ts") or 0.0) or None
            except (TypeError, ValueError):
                pierce = None
            in_utt = ear.get("in_utterance") is True
    pay["voice_on"] = voice_on
    pay["ear_pierce_ts"] = pierce
    pay["ear_in_utterance"] = in_utt
    # Pose SoT. Absent key = no news (do not close a live session on a hung
    # :8194). Glass ingestSound only writes HEAR.sessionOpen when this is set.
    hs, hb, _ = fetch(HEAR_HEALTH_URL, timeout=0.08)
    if hs == 200 and hb:
        try:
            hd = json.loads(hb) or {}
        except Exception:
            hd = {}
        if isinstance(hd, dict) and "session_open" in hd:
            pay["session_open"] = hd.get("session_open") is True
            pay["paused"] = hd.get("paused") is True
            pay["dispatch_busy"] = hd.get("dispatch_busy") is True
            if "comms_hold" in hd:
                pay["comms_hold"] = hd.get("comms_hold") is True
                pay["comms_hold_until"] = float(hd.get("comms_hold_until") or 0.0)
    return pay


def gallery_manifest() -> dict:
    p = GALLERY_DIR / "manifest.json"
    if not p.is_file():
        return {"hold_s": 15, "quiet_s": 4, "gap_s": 3, "pieces": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"hold_s": 15, "quiet_s": 4, "gap_s": 3, "pieces": []}
    if not isinstance(data, dict):
        return {"hold_s": 15, "pieces": []}
    # Parked 2026-08-18 — delight/ASCII cron + in-page hourly rotator.
    if data.get("parked"):
        out = dict(data)
        out["pieces"] = []
        return out
    return data


def gallery_file(rel: str) -> tuple[bytes | None, str]:
    """Allowlisted read of a gallery piece. Path must stay inside GALLERY_DIR."""
    rel = (rel or "").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None, ""
    root = GALLERY_DIR.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None, ""
    if not path.is_file():
        return None, ""
    suffix = path.suffix.lower()
    mime = {
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(suffix, "application/octet-stream")
    return path.read_bytes(), mime


def fetch(url: str, timeout: float = 4.0) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception:
        return 0, b"", ""


_spk: dict = {}
_spk_ts = 0.0
_spk_lock = threading.Lock()


def _cam_pan():
    """Absolute pan, or None. Cheap: a v4l control read, already local."""
    if CAMERA_OFF:
        return None
    status, body, _ = fetch(FIRMWARE_URL, timeout=1.0)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body).get("pan_actual")
    except (json.JSONDecodeError, AttributeError):
        return None


def get_speaker() -> dict:
    """The live face ID, for the wall's speaker row.

    The visor on :8191 is Crissa's plain working feed; the wall shows the same
    numbers dressed for the room. Proxied here rather than fetched from the
    page so the glass keeps talking to one origin, like it does for /frame.
    """
    global _spk, _spk_ts
    now = time.time()
    with _spk_lock:
        if _spk and (now - _spk_ts) < 1.0:
            return _spk
    status, body, _ = fetch(SPEAKER_URL, timeout=1.5)
    if status == 200 and body:
        try:
            b = json.loads(body)
        except json.JSONDecodeError:
            b = None
        if isinstance(b, dict):
            faces = b.get("faces") or []
            sp = b.get("speaker") if isinstance(b.get("speaker"), dict) else {}
            pid = str(sp.get("person_id") or "").strip().lower() or None
            lock = b.get("locked")
            top = next((f for f in faces if f.get("name") == lock), None)
            if top is None and faces and lock:
                top = next((f for f in faces if f.get("name")), None)
            out = {
                "ok": True,
                "locked": str(lock or "") or None,
                "name": str((top or {}).get("name") or ""),
                "sim": (top or {}).get("sim"),
                "n_faces": b.get("n_faces") or 0,
                "stack": b.get("speaker") or {},
                # The wall draws the visor's own reticles when you speak, so it
                # needs the boxes and the frame they were measured in.
                "faces": [
                    {
                        "name": str(f.get("name") or ""),
                        "sim": f.get("sim"),
                        "bbox": f.get("bbox"),
                    }
                    for f in faces
                    if isinstance(f, dict)
                ][:6],
                "frame_w": b.get("frame_w") or 0,
                "frame_h": b.get("frame_h") or 0,
                "cam_pan": _cam_pan(),
            }
            with _spk_lock:
                _spk, _spk_ts = out, now
            return out
    with _spk_lock:
        return _spk or {"ok": False}


def get_frame() -> bytes | None:
    global _frame, _frame_ts
    if CAMERA_OFF:
        return None
    now = time.time()
    with _frame_lock:
        if _frame is not None and (now - _frame_ts) < FRAME_TTL:
            return _frame
    status, body, _ = fetch(FRAME_URL, timeout=3.0)
    if status == 200 and body:
        with _frame_lock:
            _frame, _frame_ts = body, now
        return body
    with _frame_lock:
        return _frame  # stale frame beats a blank wall


CDP_URL = os.environ.get("VO_CDP_URL", "http://127.0.0.1:9222")


def kiosk_bounds() -> dict:
    """Physical kiosk rect. Cached sampler (~4 Hz); not window.screenX."""
    return read_kiosk_bounds()


def kiosk_window(state: str) -> dict:
    """Drive the kiosk Brave window state over CDP.

    Deliberately NOT --kiosk: User 2026-08-09 — "NEVER auto --kiosk on ensure",
    kiosk + Wayland left a grey full-screen wall that could not be minimised
    (logout required). setWindowBounds is reversible; round-trip verified
    fullscreen 2560x1440 -> normal 1899x800 before shipping.

    2026-08-18: do not bounce through ``normal`` first (that is the 1s bar
    flash). If already in the requested state, no-op. Retry fullscreen once
    if Wayland drops it to maximized.
    """
    import websocket  # available in the vllm env; optional dependency

    want = str(state or "").strip() or "normal"
    tabs = json.loads(urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read())
    mine = [t for t in tabs if str(PORT) in (t.get("url") or "")]
    if not mine:
        return {"ok": False, "error": "kiosk tab not found"}
    ver = json.loads(urllib.request.urlopen(CDP_URL + "/json/version", timeout=5).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=10,
                                     suppress_origin=True)
    try:
        def send(i, m, params):
            ws.send(json.dumps({"id": i, "method": m, "params": params}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == i:
                    return r
        g = send(1, "Browser.getWindowForTarget", {"targetId": mine[0]["id"]})
        wid = g["result"]["windowId"]
        before = (g.get("result") or {}).get("bounds") or {}
        if before.get("windowState") == want:
            return {"ok": True, "bounds": before, "skipped": "already"}
        n = 2
        if want == "fullscreen" and before.get("windowState") == "minimized":
            send(n, "Browser.setWindowBounds",
                 {"windowId": wid, "bounds": {"windowState": "normal"}})
            n += 1
            time.sleep(0.2)
        send(n, "Browser.setWindowBounds",
             {"windowId": wid, "bounds": {"windowState": want}})
        n += 1
        time.sleep(0.25)
        g2 = send(n, "Browser.getWindowForTarget", {"targetId": mine[0]["id"]})
        n += 1
        after = (g2.get("result") or {}).get("bounds") or {}
        if want == "fullscreen" and after.get("windowState") != "fullscreen":
            send(n, "Browser.setWindowBounds",
                 {"windowId": wid, "bounds": {"windowState": "fullscreen"}})
            n += 1
            time.sleep(0.2)
            g3 = send(n, "Browser.getWindowForTarget", {"targetId": mine[0]["id"]})
            after = (g3.get("result") or {}).get("bounds") or {}
        return {
            "ok": after.get("windowState") == want or want != "fullscreen",
            "bounds": after,
            "before": before,
        }
    finally:
        try:
            ws.close()
        except Exception:
            pass


_vo_fs_assert_ts = 0.0
_vo_fs_lock = threading.Lock()


def _zos_background() -> bool:
    """ZOS Brave in the background under the wallpaper (the User 2026-10-02, lib/zos_brave.py, OFF by default)."""
    try:
        import zos_brave
        return zos_brave.background()
    except Exception:  # noqa: BLE001
        return False


def ensure_vo_os_fullscreen(bounds: dict | None = None) -> None:
    """If Voice-Only is on and the OS window is not fullscreen, restore it.

    Page class and Brave window are two switches. Reload, panel pin,
    occupancy restart, and GNOME mouse-to-edge drop the window to
    maximized while the ``voice-only`` flag stays set — then the GNOME
    header (the top browser ribbon) shows. Do not block the house poll.
    """
    if not VOICE_ONLY_FLAG.is_file():
        return
    if _zos_background():
        return    # a hidden ZOS Brave is never un-minimised by a /v1/house poll (lib/zos_brave.py)
    b = bounds
    if b is None:
        try:
            b = kiosk_bounds()
        except Exception:
            b = {}
    if isinstance(b, dict) and b.get("ok") and b.get("fullscreen"):
        return
    now = time.time()
    with _vo_fs_lock:
        global _vo_fs_assert_ts
        if now - _vo_fs_assert_ts < 4.0:
            return
        _vo_fs_assert_ts = now

    def _go() -> None:
        try:
            kiosk_window("fullscreen")
        except Exception as e:
            log(f"vo fullscreen re-assert failed: {e!r}")

    threading.Thread(target=_go, name="vo-fs", daemon=True).start()


def kiosk_panel_dest(current_url: str) -> str | None:
    """Control Panel — Lila's studio. None if the kiosk is already there."""
    u = current_url or ""
    path = _kiosk_url_path(u)
    if f":{PORT}" in u and path.endswith("/panel"):
        return None
    # The CP's own hostname (2026-09-16). Chromium keys saved zoom by HOST, not
    # host:port, so every surface on 127.0.0.1 shared one zoom level; the CP
    # answers on panel.home.arpa (/etc/hosts, 127.0.0.1) and keeps its own.
    # Steering the glass here numerically would have been the one path that
    # still put it in the wall's bucket. The "already there" test above is
    # host-agnostic on purpose — port plus path — so a CP tab the User opened
    # by its name is recognised and not re-navigated.
    return f"http://panel.home.arpa:{PORT}/panel"


def kiosk_wall_dest(
    current_url: str, *, form: bool = False, init: str | None = None
) -> str | None:
    """Where the kiosk should go so the teleprompter can win.

    None = already on the wall (just raise). Voice-register reads the line on
    :8099, never :8099/panel and never the other-server panel at :8190.
    init=user is Claude's Init User routine (same wall overlay, not a fork).
    """
    u = current_url or ""
    path = _kiosk_url_path(u)
    foreign = any(s in u for s in (":8190/panel", ":8196", ":9119"))
    on_a_sub_page = path.endswith("/panel") or path in FRAMED
    on_wall = (f":{PORT}" in u) and (not on_a_sub_page) and (not foreign)
    if init == "user":
        if on_wall and "init=user" in u:
            return None
        return f"http://wall.home.arpa:{PORT}/?init=user"
    if form:
        if on_wall and "init=register" in u:
            return None
        return f"http://wall.home.arpa:{PORT}/?init=register"
    if on_wall:
        return None
    # The wall's own hostname, like every other surface (2026-09-16). The
    # "already there" tests above are port+path and host-agnostic on purpose,
    # so a wall tab the User reached by any name is recognised, not
    # re-navigated into a different zoom bucket.
    return f"http://wall.home.arpa:{PORT}/"


def _kiosk_url_path(url: str) -> str:
    u = url or ""
    path = urllib.parse.urlparse(u).path.rstrip("/") or "/"
    return path


def _is_kiosk_surface(url: str) -> bool:
    """Wall or Control Panel on :8099 — not Krew/Composer.

    Init/Register must land on the room glass, not whichever :8099 tab
    CDP lists first (often the clicking Control Panel tab).
    """
    u = url or ""
    if f":{PORT}" not in u:
        return False
    path = _kiosk_url_path(u)
    if path in FRAMED or path.endswith("/krew") or path.endswith("/composer"):
        return False
    return path == "/" or path.endswith("/panel")


def _kiosk_open_url(url: str) -> dict | None:
    """Open a wall or panel tab. Used when CDP only has Krew/Composer."""
    import websocket

    want = (url or "").strip()
    if not want:
        return None
    try:
        ver = json.loads(
            urllib.request.urlopen(CDP_URL + "/json/version", timeout=5).read()
        )
        ws = websocket.create_connection(
            ver["webSocketDebuggerUrl"], timeout=8, suppress_origin=True
        )
    except Exception as e:
        log(f"kiosk open tab: {e!r}")
        return None
    tid = None
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Target.createTarget",
            "params": {"url": want},
        }))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == 1:
                tid = (r.get("result") or {}).get("targetId")
                break
    except Exception as e:
        log(f"kiosk createTarget: {e!r}")
        tid = None
    finally:
        try:
            ws.close()
        except Exception:
            pass
    time.sleep(0.4)
    try:
        tabs = json.loads(
            urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read()
        )
    except Exception:
        return None
    if tid:
        for t in tabs:
            if t.get("id") == tid or t.get("targetId") == tid:
                return t
    for t in tabs:
        if t.get("type") != "page":
            continue
        if _is_kiosk_surface(t.get("url") or ""):
            return t
    return None


def _x11_active_window() -> str:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY") or ":0"}
    try:
        r = subprocess.run(
            ["xdotool", "getactivewindow"],
            env=env, timeout=1, capture_output=True, text=True,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _x11_activate(wid: str) -> None:
    if not wid:
        return
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY") or ":0"}
    try:
        subprocess.run(
            ["xdotool", "windowactivate", "--sync", wid],
            env=env, timeout=1, capture_output=True,
        )
    except Exception:
        pass


def _raise_crissa_window(*, steal_focus: bool = False) -> None:
    """Map the wall. Hybrid must not yank the User's keyboard into Brave."""
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY") or ":0"}
    if not steal_focus:
        # Raise without activate. Always-on-top (add,above) steals the
        # pointer even after we restore the CLI window.
        try:
            subprocess.run(
                ["xdotool", "search", "--class", "crissa-display", "windowraise"],
                env=env, timeout=2, capture_output=True,
            )
        except Exception:
            pass
        return
    for cmd in (
        ["wmctrl", "-xa", "crissa-display"],
        ["wmctrl", "-a", crissa_home.her_name()],      # the page's own title says her name (the Crissa merge)
        ["xdotool", "search", "--class", "crissa-display", "windowactivate"],
    ):
        try:
            r = subprocess.run(cmd, env=env, timeout=2, capture_output=True)
            if r.returncode == 0:
                return
        except Exception:
            continue


_user_display_cache: dict = {"at": 0.0, "v": ""}
_voice_id_cache: dict = {"at": 0.0, "v": {}}


def voice_id_snapshot() -> dict:
    """The live voice ID as the Ear reports it: encoder family and the bank
    counts (user 9 …), cached 10 s. WeSpeaker has no unit of its own — it
    runs inside rail-stt — so the Control Panel shows it as a readout, not
    a tile (the User 2026-09-09: "we should have wespeaker too there")."""
    now = time.time()
    if now - _voice_id_cache["at"] < 10.0:
        return dict(_voice_id_cache["v"])
    v: dict = {}
    try:
        with urllib.request.urlopen(STT_HEALTH_URL, timeout=1.5) as r:
            hd = json.loads(r.read().decode() or "{}")
        v = {"encoder": hd.get("voice_encoder"), "bank": hd.get("voice_bank"), "ok": bool(hd.get("voice_bank_ok"))}
    except Exception:
        v = {}
    _voice_id_cache.update(at=now, v=v)
    return dict(v)


def user_display() -> str:
    """The User's display name from people/<HOUSE_USER_ID>/person.json (the
    identity root), cached 10 s; '' when unknown. The Control Panel's New
    User prompt starts from it."""
    now = time.time()
    if now - _user_display_cache["at"] < 10.0:
        return _user_display_cache["v"]
    v = ""
    try:
        import house_user  # the User's id from the identity record, never a literal (the User 2026-10-02)

        uid = house_user.user_id()
        root = Path(os.environ.get("HOUSE_ID_ROOT") or _machine_path("identity_root", STATE / "identity"))
        pj = root / "people" / uid / "person.json"
        if pj.is_file():
            d = json.loads(pj.read_text(encoding="utf-8"))
            v = str(d.get("display") or d.get("name") or uid).strip()
        else:
            v = uid
    except Exception:
        v = ""
    _user_display_cache.update(at=now, v=v)
    return v


def wall_routine_desk(show_wall: bool) -> str:
    """A routine on the WALLPAPER needs the wall in view: the Control Panel's
    window (Crissa's Brave or the User's own) sat on top and the routine ran
    behind it (the User 2026-09-09: "we just need to bring User back to wall for
    the routine. It stayed on CP"). Mutter's own show-desktop, the dock's
    Clear desktop lever (lib/house_wall_dock.show_desktop) — every window
    away at the start, and Mutter brings the same windows back at the end,
    so the panel that sent him returns."""
    try:
        import house_wall_dock  # lib/, on sys.path
        how = house_wall_dock.show_desktop(bool(show_wall))
        log(f"wall routine: {'desk clear' if show_wall else 'windows back'} via {how}")
        return how
    except Exception as e:
        log(f"wall routine desk: {type(e).__name__}: {e}"[:120])
        return "failed"


def pack_asks_house_units() -> bool:
    """A PACK HAS NO HOUSE UNITS (round 14, 2026-10-12, .101: `systemd --user` at 40-90 %). The desk Wall polls /v1/house
    about three times a second, and each answer asked systemd whether house-wall-ascii.service and crissa-display.service
    were active; a pack has neither, so the manager loaded each missing unit and dropped it again - 58 and 56 times in
    20 s (`busctl monitor`: UnitNew / UnitRemoved). In a pack both answers were always "not active", and every pack path
    was built and tested on that answer: it is given without asking. CRISSA_FEED_HOUSE_UNITS=1 asks as before."""
    if crissa_home.is_house():
        return True
    return str(os.environ.get("CRISSA_FEED_HOUSE_UNITS", "")).strip().lower() in ("1", "on", "true", "yes")


def wallpaper_active() -> bool:
    """Is house-wall-ascii (the wallpaper) the glass right now?"""
    if not pack_asks_house_units():
        return False
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", "house-wall-ascii.service"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return False
    return out == "active"


WALL_TAB_FRONTS = os.environ.get("HOUSE_HOME_KEY_FRONTS_WALL", "1") != "0"


def _wall_page_url() -> str:
    return f"http://wall.home.arpa:{PORT}/"


def kiosk_home() -> dict:
    """\u2302 on a Brave rail: to the WALL.

    When the wall is this browser's own tab the page follows its link (reply
    wall=tab) \u2014 ZOS Wallpaper OFF, a plain Brave window, nothing here
    touches it.

    UNDER THE WALLPAPER \u2302 FRONTS HER WALL TAB (the User 2026-09-21: "I do want
    the Wall page to work on Brave. Let's do keep the Wall button functional").
    It used to MINIMISE every Brave window instead, on the reading that the
    wallpaper is already the wall so \u2302 means "get out of the way".
    Together with the launcher dropping :8099 from the tab list in this mode
    (2026-09-14, "tools tabs only, no wall page") that left no route to the Wall
    page at all \u2014 "Now we can't open the Wall page. So yall are
    over-correcting." Killing the FORCED open was right; killing the open was
    not.

    So: find her Wall tab, open one if she has none, activate it.

    IT DOES NOT TOUCH THE WINDOW'S SIZE. A first cut also brought a
    monitor-covering window down to a near-full box; the User pulled that
    (2026-09-21: "I should have stopped us at changing the resolution ... Leave
    res and collapse features as they were"). The ONLY window change left is
    un-minimising, because the \u2302 this replaces is what minimised her Brave
    in the first place \u2014 activating a tab inside a minimised window shows
    nothing, and the key would read as dead all over again. Restoring a
    minimised window is not a resize: Chromium hands back the bounds it already
    had.

    HOUSE_HOME_KEY_FRONTS_WALL=0 restores the 2026-09-08 minimise.
    """
    if not wallpaper_active():
        return {"ok": True, "wall": "tab"}
    if not WALL_TAB_FRONTS:
        return _kiosk_home_minimise()

    want = _wall_page_url()
    try:
        tabs = json.loads(urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read())
    except Exception as e:  # noqa: BLE001
        # her browser is not up: let the page follow its own link rather than
        # swallow the press
        log(f"kiosk_home: no CDP ({e!r})")
        return {"ok": False, "wall": "tab", "error": "no crissa brave"}
    pages = [t for t in tabs if t.get("type") == "page"]
    # port + path, host-agnostic \u2014 a wall tab the User reached by any name
    # is the wall tab (the same rule _kiosk_wall_url uses)
    hit = next((t for t in pages
                if f":{PORT}" in (t.get("url") or "") and _kiosk_url_path(t.get("url") or "") == "/"),
               None)
    how = "activated"
    if hit is None:
        hit = _kiosk_open_url(want)
        how = "opened"
        if not hit:
            return {"ok": False, "wall": "tab", "error": "could not open the wall page"}
    else:
        try:
            urllib.request.urlopen(f"{CDP_URL}/json/activate/{hit['id']}", timeout=3).read()
        except Exception as e:  # noqa: BLE001
            log(f"kiosk_home: activate {hit.get('id')}: {e!r}")
            return {"ok": False, "wall": "tab", "error": "activate failed"}
    return {"ok": True, "wall": "wallpaper", "tab": how,
            "window": _unminimise_only(hit.get("id"))}


def _unminimise_only(target_id) -> str:
    """Bring her window back if it is minimised. NOTHING ELSE.

    No size, no position, no state change of any other kind \u2014 a window the
    User has sized is the User's (the User 2026-09-21: "Leave res ... as they
    were"). Minimised is the common case only because the \u2302 this replaces
    is what minimised it.
    """
    if not target_id:
        return "no target"
    try:
        import websocket
    except Exception as e:  # noqa: BLE001
        return f"skipped ({type(e).__name__})"
    try:
        ver = json.loads(urllib.request.urlopen(CDP_URL + "/json/version", timeout=5).read())
        ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=8, suppress_origin=True)
    except Exception as e:  # noqa: BLE001
        return f"skipped ({type(e).__name__})"
    n = 0

    def send(m, params):
        nonlocal n
        n += 1
        ws.send(json.dumps({"id": n, "method": m, "params": params}))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == n:
                return r
    try:
        g = send("Browser.getWindowForTarget", {"targetId": target_id})
        res = g.get("result") or {}
        wid = res.get("windowId")
        st = (res.get("bounds") or {}).get("windowState")
        if wid is None:
            return "no window"
        if st != "minimized":
            return f"left {st}"
        send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
        return "minimized -> normal"
    except Exception as e:  # noqa: BLE001
        return f"bounds: {type(e).__name__}"
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _kiosk_home_minimise() -> dict:
    """The 2026-09-08 \u2302: put every Brave window on her profile away.

    Kept behind HOUSE_HOME_KEY_FRONTS_WALL=0. Crissa's Brave is an --app window
    (no tab strip), so a rail key that opens a tab gets a SECOND, normal window
    \u2014 "seems messy having two open. Could the Home button close the 2nd
    instance?" (the User 2026-09-08). The app window is the first one Brave made
    (lowest windowId); everything in any other window is closed (the window
    goes with its last tab), and the app window is put away \u2014 through
    normal first, since Mutter ignores fullscreen -> minimized in one step
    (that was "home does not work from the board").
    """
    import websocket

    tabs = json.loads(urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read())
    pages = [t for t in tabs if t.get("type") == "page"]
    ver = json.loads(urllib.request.urlopen(CDP_URL + "/json/version", timeout=5).read())
    ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
    closed = 0
    app_state = None
    try:
        n = 0

        def send(m, params):
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, "method": m, "params": params}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == n:
                    return r
        win_of: dict = {}
        bounds_of: dict = {}
        for t in pages:
            g = send("Browser.getWindowForTarget", {"targetId": t["id"]})
            wid = (g.get("result") or {}).get("windowId")
            if wid is not None:
                win_of[t["id"]] = wid
                bounds_of[wid] = (g.get("result") or {}).get("bounds") or {}
        if not win_of:
            return {"ok": False, "wall": "wallpaper", "error": "no Brave window"}
        app_win = min(win_of.values())
        for t in pages:
            if win_of.get(t["id"]) not in (None, app_win):
                send("Target.closeTarget", {"targetId": t["id"]})
                closed += 1
        st = (bounds_of.get(app_win) or {}).get("windowState")
        if st != "minimized":
            if st == "fullscreen":
                send("Browser.setWindowBounds", {"windowId": app_win, "bounds": {"windowState": "normal"}})
                time.sleep(0.25)
            send("Browser.setWindowBounds", {"windowId": app_win, "bounds": {"windowState": "minimized"}})
            time.sleep(0.2)
        keep = next((t for t in pages if win_of.get(t["id"]) == app_win), None)
        if keep:
            g = send("Browser.getWindowForTarget", {"targetId": keep["id"]})
            app_state = ((g.get("result") or {}).get("bounds") or {}).get("windowState")
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return {"ok": True, "wall": "wallpaper", "closed": closed, "app_window": app_state}


def raise_panel_window() -> dict:
    """Bring Control Panel forward. Do not navigate the wall tab.

    Render host stays on the glass (wallpaper / :8100). The studio is a
    window, not a kiosk steal. the User 2026-09-16: seamless, no sign.
    """
    import websocket

    want = f"http://panel.home.arpa:{PORT}/panel"
    try:
        tabs = json.loads(
            urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read()
        )
    except Exception as e:
        return {"ok": False, "glass": "untouched", "error": str(e)[:120]}
    pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    target = None
    for t in pages:
        u = t.get("url") or ""
        if f":{PORT}" in u and _kiosk_url_path(u).endswith("/panel"):
            target = t
            break
    opened = False
    if target is None:
        target = _kiosk_open_url(want)
        opened = bool(target)
    if not target:
        return {"ok": False, "glass": "untouched", "error": "no panel tab"}
    shown = _kiosk_show_window(target)
    try:
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=6, suppress_origin=True
        )
        try:
            ws.send(json.dumps({"id": 1, "method": "Page.bringToFront"}))
            ws.settimeout(2)
            ws.recv()
        finally:
            ws.close()
    except Exception as e:
        shown = f"{shown}; bringToFront {type(e).__name__}"
    return {
        "ok": True,
        "glass": "untouched",
        "raised": True,
        "opened": opened,
        "shown": shown,
        "url": target.get("url") or want,
    }


def kiosk_pin_wall(*, form: bool = False, panel: bool = False, init: str | None = None) -> dict:
    """Raise the room Brave. Wall is the teleprompter; panel is Lila's studio.

    Register yanked :8099/panel back to the wall. Render does the inverse so a
    Voice-Only (no mouse) user sees the studio. house-mode must not call CDP —
    it POSTs /v1/action and this function owns the glass.
    """
    if wallpaper_active():
        # the wallpaper is the glass: it paints Register / Init from /v1/register
        # and /v1/init itself; Brave is not pinned (the User 2026-09-09)
        return {"ok": True, "glass": "wallpaper", "skipped": True}
    import websocket

    tabs = json.loads(urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read())
    pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    surfaces = [t for t in pages if _is_kiosk_surface(t.get("url") or "")]
    if not surfaces:
        if panel:
            want = f"http://127.0.0.1:{PORT}/panel"
        elif init == "user":
            want = f"http://127.0.0.1:{PORT}/?init=user"
        elif form:
            want = f"http://127.0.0.1:{PORT}/?init=register"
        else:
            want = f"http://127.0.0.1:{PORT}/"
        opened = _kiosk_open_url(want)
        if opened:
            surfaces = [opened]
        else:
            target = None
            for t in pages:
                if f":{PORT}" in (t.get("url") or "") and _is_kiosk_surface(
                    t.get("url") or ""
                ):
                    target = t
                    break
            if target is None and pages:
                # Last resort: do not treat Krew/Composer as the wall.
                return {"ok": False, "error": "kiosk tab not found", "hint": want}
            surfaces = [target] if target else []
    if not surfaces:
        return {"ok": False, "error": "kiosk tab not found"}

    def _cdp_send(ws, n, method, params=None):
        n += 1
        ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
        ws.settimeout(2.5)
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == n:
                return n, r

    navigated = False
    url = surfaces[0].get("url") or ""
    dest = None
    for t in surfaces:
        u = t.get("url") or ""
        d = (
            kiosk_panel_dest(u)
            if panel
            else kiosk_wall_dest(u, form=form, init=init)
        )
        url = u
        dest = d
        if not d:
            continue
        ws_one = None
        try:
            ws_one = websocket.create_connection(
                t["webSocketDebuggerUrl"], timeout=8, suppress_origin=True
            )
            n = 0
            n, _ = _cdp_send(ws_one, n, "Page.enable")
            n, _ = _cdp_send(ws_one, n, "Page.navigate", {"url": d})
            navigated = True
        except Exception:
            pass
        finally:
            if ws_one is not None:
                try:
                    ws_one.close()
                except Exception:
                    pass
    if navigated:
        time.sleep(0.45)

    target = surfaces[0]
    for t in surfaces:
        u = t.get("url") or ""
        if init == "user" and "init=user" in u:
            target = t
            break
        if form and "init=register" in u:
            target = t
            break
        if _kiosk_url_path(u) == "/" and not panel:
            target = t
    url = target.get("url") or ""
    dest = (
        kiosk_panel_dest(url)
        if panel
        else kiosk_wall_dest(url, form=form, init=init)
    )
    shown = _kiosk_show_window(target)      # the WINDOW first: bringToFront cannot un-minimise it
    ws = websocket.create_connection(
        target["webSocketDebuggerUrl"], timeout=8, suppress_origin=True
    )
    try:
        n = 0

        def send(method, params=None):
            nonlocal n
            n, _r = _cdp_send(ws, n, method, params)
            return _r

        vo = VOICE_ONLY_FLAG.is_file()
        keep = "" if vo else _x11_active_window()
        send("Page.enable")
        if vo:
            send("Page.bringToFront")
        try:
            ver = json.loads(
                urllib.request.urlopen(CDP_URL + "/json/version", timeout=3).read()
            )
            bws = websocket.create_connection(
                ver["webSocketDebuggerUrl"], timeout=6, suppress_origin=True
            )
            try:
                bn = 0

                def bsend(method, params=None):
                    nonlocal bn
                    bn += 1
                    bws.send(json.dumps({"id": bn, "method": method, "params": params or {}}))
                    while True:
                        r = json.loads(bws.recv())
                        if r.get("id") == bn:
                            return r

                g = bsend("Browser.getWindowForTarget", {"targetId": target["id"]})
                wid = (g.get("result") or {}).get("windowId")
                bounds = (g.get("result") or {}).get("bounds") or {}
                if wid is not None:
                    st = bounds.get("windowState")
                    if VOICE_ONLY_FLAG.is_file():
                        # VO owns chrome-less FS. Minimized-only restore left
                        # a maximized window with the GNOME header (2026-08-21).
                        if st != "fullscreen":
                            bsend("Browser.setWindowBounds", {
                                "windowId": wid,
                                "bounds": {"windowState": "fullscreen"},
                            })
                    elif st == "minimized":
                        # a WINDOW, never maximized (the User 2026-09-16: "kept open
                        # no maximized. Never!")
                        bsend("Browser.setWindowBounds", {
                            "windowId": wid,
                            "bounds": {"windowState": "normal"},
                        })
            finally:
                try:
                    bws.close()
                except Exception:
                    pass
        except Exception:
            pass
        _raise_crissa_window(steal_focus=vo)
        if keep:
            _x11_activate(keep)
        return {
            "ok": True,
            "url": url,
            "navigated": navigated,
            "dest": dest if navigated else url,
        }
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _kiosk_surface_tabs() -> list[dict]:
    tabs = json.loads(urllib.request.urlopen(CDP_URL + "/json/list", timeout=5).read())
    return [
        t
        for t in tabs
        if t.get("type") == "page"
        and t.get("webSocketDebuggerUrl")
        and _is_kiosk_surface(t.get("url") or "")
    ]


def _kiosk_cdp_navigate(tab: dict, dest: str) -> bool:
    import websocket

    ws = None
    try:
        ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=8, suppress_origin=True
        )
        n = 0

        def send(method, params=None):
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
            ws.settimeout(2.5)
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == n:
                    return r

        send("Page.enable")
        send("Page.navigate", {"url": dest})
        return True
    except Exception as e:
        log(f"kiosk nav: {e!r}")
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _kiosk_cdp_raise(tab: dict) -> None:
    import websocket

    ws = None
    try:
        ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=8, suppress_origin=True
        )
        n = 0

        def send(method, params=None):
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
            ws.settimeout(2.5)
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == n:
                    return r

        send("Page.enable")
        send("Page.bringToFront")
    except Exception as e:
        log(f"kiosk raise: {e!r}")
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _x_raise_crissa_window() -> str:
    """Bring Crissa's Brave WINDOW up the way the wall's dock does: an EWMH
    _NET_ACTIVE_WINDOW message (source 2, a pager) on the crissa-display X
    window — Mutter un-minimises and focuses it. CDP's Page.bringToFront
    only fronts the TAB inside a window that may be HIDDEN (the User 2026-09-09:
    "click took me nowhere. A sentence popped up below said look at wall")."""
    try:
        ids = re.findall(r"0x[0-9a-f]+", subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"],
                                                         capture_output=True, text=True, timeout=2).stdout)
        found, live = [], []
        for w in ids:
            r = subprocess.run(["xprop", "-id", w, "WM_CLASS", "_NET_WM_STATE"], capture_output=True, text=True, timeout=2).stdout
            if "crissa-display" in r:
                found.append(w)
                if "_NET_WM_STATE_HIDDEN" not in r:
                    live.append(w)
        xid = (live or found or [None])[0]
        if not xid:
            return "no crissa-display X window"
        from ctypes import CDLL, Structure, Union, byref, c_char_p, c_int, c_long, c_ulong, c_void_p
        X = CDLL("libX11.so.6")
        X.XOpenDisplay.restype = c_void_p
        X.XOpenDisplay.argtypes = [c_char_p]
        X.XInternAtom.restype = c_ulong
        X.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        X.XDefaultRootWindow.restype = c_ulong
        X.XDefaultRootWindow.argtypes = [c_void_p]
        X.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, c_void_p]
        X.XFlush.argtypes = [c_void_p]
        X.XCloseDisplay.argtypes = [c_void_p]
        dpy = X.XOpenDisplay(None)
        if not dpy:
            return "no X display"

        class XCM(Structure):
            _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                        ("window", c_ulong), ("message_type", c_ulong), ("format", c_int), ("data", c_long * 5)]

        class XEv(Union):
            _fields_ = [("xclient", XCM), ("pad", c_long * 24)]

        ev = XEv()
        ev.xclient.type = 33
        ev.xclient.window = int(xid, 16)
        ev.xclient.message_type = X.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
        ev.xclient.format = 32
        ev.xclient.data[0] = 2
        X.XSendEvent(dpy, X.XDefaultRootWindow(dpy), 0, (1 << 20) | (1 << 19), byref(ev))
        X.XFlush(dpy)
        X.XCloseDisplay(dpy)
        return f"activated {xid}" + ("" if live else " (was hidden)")
    except Exception as e:  # noqa: BLE001
        return f"x raise failed: {type(e).__name__}"


def _kiosk_show_window(tab: dict) -> str:
    """The tab's WINDOW, restored (CDP, browser level) and activated (X)."""
    import websocket

    how = []
    try:
        ver = json.loads(urllib.request.urlopen(CDP_URL + "/json/version", timeout=5).read())
        ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=6, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Browser.getWindowForTarget", "params": {"targetId": tab.get("id")}}))
            ws.settimeout(4)
            r = json.loads(ws.recv())
            wid = (r.get("result") or {}).get("windowId")
            st = ((r.get("result") or {}).get("bounds") or {}).get("windowState")
            if wid is not None and st == "minimized":
                ws.send(json.dumps({"id": 2, "method": "Browser.setWindowBounds",
                                    "params": {"windowId": wid, "bounds": {"windowState": "normal"}}}))
                ws.recv()
                how.append("restored")
        finally:
            ws.close()
    except Exception as e:  # noqa: BLE001
        how.append(f"cdp window: {type(e).__name__}")
    how.append(_x_raise_crissa_window())
    return "; ".join(how)


def kiosk_pin_register() -> dict:
    """Register overlay on the wall. Leave an existing Control Panel tab.

    2026-09-11: RETIRED. The wall is the register's only renderer (the User:
    "the served version never worked once"); the Brave page's overlay is
    never navigated to. Kept as a no-op so every caller still gets a dict."""
    return {"ok": True, "glass": "wall", "skipped": True, "retired": "2026-09-11"}
    if wallpaper_active():
        return {"ok": True, "glass": "wallpaper", "skipped": True}
    dest = f"http://127.0.0.1:{PORT}/?init=register"
    tabs = _kiosk_surface_tabs()
    walls = [
        t
        for t in tabs
        if not _kiosk_url_path(t.get("url") or "").endswith("/panel")
    ]
    panels = [
        t for t in tabs if _kiosk_url_path(t.get("url") or "").endswith("/panel")
    ]
    targets = walls or panels
    if not targets:
        opened = _kiosk_open_url(dest)
        shown = _kiosk_show_window(opened) if opened else "no tab opened"
        log(f"kiosk register: fresh tab opened={bool(opened)} window={shown}")
        out = {
            "ok": bool(opened),
            "opened": bool(opened),
            "url": (opened or {}).get("url") or dest,
            "dest": dest,
            "window": shown,
        }
        if not opened:
            out["error"] = "The wall did not take the overlay (Brave opened no tab)."
        return out
    navigated = False
    already = any("init=register" in (t.get("url") or "") for t in targets)
    for t in targets:
        u = t.get("url") or ""
        if "init=register" in u:
            continue
        if _kiosk_cdp_navigate(t, dest):
            navigated = True
    opened = None
    if not navigated and not already:
        # every target's page socket timed out (a tab mid-close, a wedged
        # renderer): a fresh tab from the browser socket instead of a
        # cheerful ok (2026-09-09: four nav timeouts, then "took me nowhere")
        opened = _kiosk_open_url(dest)
        if opened:
            targets = [opened] + targets
    shown = _kiosk_show_window(targets[0])
    _kiosk_cdp_raise(targets[0])
    ok = bool(navigated or already or opened)
    out = {
        "ok": ok,
        "navigated": navigated,
        "opened": bool(opened),
        "dest": dest,
        "url": targets[0].get("url") or dest,
        "left_panel": bool(walls) and bool(panels),
        "window": shown,
    }
    if not ok:
        out["error"] = "The wall did not take the overlay (Brave's tabs did not answer)."
    log(f"kiosk register: navigated={navigated} already={already} opened={bool(opened)} window={shown}")
    return out


def kiosk_return_cp() -> dict:
    """After Register/Init: wall back to /, raise the same Control Panel tab.

    Do not turn the wall into a second /panel tab.
    """
    set_register_glass("idle", "", pip="off")
    if wallpaper_active():
        return {"ok": True, "glass": "wallpaper", "desk": wall_routine_desk(False)}
    tabs = _kiosk_surface_tabs()
    panels = [
        t for t in tabs if _kiosk_url_path(t.get("url") or "").endswith("/panel")
    ]
    overlays = [
        t
        for t in tabs
        if "init=register" in (t.get("url") or "")
        or "init=user" in (t.get("url") or "")
    ]
    wall = f"http://127.0.0.1:{PORT}/"
    panel = f"http://127.0.0.1:{PORT}/panel"
    if panels:
        for t in overlays:
            if t in panels:
                continue
            _kiosk_cdp_navigate(t, wall)
        _kiosk_cdp_raise(panels[0])
        return {
            "ok": True,
            "raised": "panel",
            "url": panels[0].get("url") or panel,
        }
    if overlays:
        for t in overlays:
            _kiosk_cdp_navigate(t, panel)
        return {"ok": True, "navigated": "panel", "url": panel}
    return kiosk_pin_wall(panel=True)


_svc_cache: dict = {"ts": 0.0, "data": None}
SVC_TTL = 5.0
_LEVEL_RANK = {"ok": 0, "warn": 1, "bad": 2}
# Wall + Control Panel summary chips — AND of these units (any warn → warn).
# Three chips: STT · Crissa (gateway/CUA/Cosy/Ops) · Krew (crew + Ada + Steward + Lila + dashboard).
# STT MIN is the House "ear open" chip. Cosy (TTS) lives in Crissa MIN;
# House "VOICE LIVE" is the voice lock, warn-tinted if Cosy is down.
# Crew bounce still must not orange Crissa — ops is Crissa's LLM, krew is the other.
# The three chip groups the wall reads (STT / Crissa / Krew): this machine's units from
# config `panel.feed_groups`; a pack's default is the launcher's children by name
# (lib/crissa_procs: proc:<name> tiles), its Krew group empty until krew's add-on lands.
# THE LAMPS READ WHAT THE PANELS READ (the User 2026-10-10, .101 round 4: the browser Wall said EAR QUIET, STT and
# CRISSA red while the ear listened and stt was up). In a pack a group is the launcher's own pieces that are running
# here: a piece this install never started (the old "ear" piece; a voice Init did not pick) is not down, and the
# house's own probes (its gateway, CUA, its voice server) are never asked. Crissa's group is the player and every voice
# piece the launcher runs (Init's pick, the announcer), as the Control Panel's Voices row shows them.
_PACK_GROUPS = {
    "stt": ("STT", ("proc:stt", "proc:asr", "proc:ear", "proc:bridge"), ""),
    "zoe": (crissa_home.her_name(), ("proc:player", "proc:tts", "proc:turbo", "proc:multilingual", "proc:pocket", "proc:audio8"), ""),
    "krew": ("Krew", (), ""),
}
SVC_GROUPS: dict[str, tuple[str, tuple[str, ...], str]] = (
    {k: (str(v[0]), tuple(v[1]), str(v[2])) for k, v in _PANEL["feed_groups"].items()}
    if _PANEL.get("feed_groups") else dict(_PACK_GROUPS))
_SVC_CHIP = {"stt": "STT", "zoe": crissa_home.her_name(), "krew": "Krew"}
# the document sheet's window: the house's doc-window.sh (prod-guarded), else the pack's copy (crissa-panel)
DOC_WINDOW_SH = next((p for p in (Path(__file__).resolve().parent / "doc-window.sh",
                                  Path(__file__).resolve().parent / "crissa-doc-window.sh") if p.is_file()),
                     Path(__file__).resolve().parent / "doc-window.sh")
TOOL_ROLL = STATE / "crissa-tool-roll.jsonl"
OMNI_URL = os.environ.get("VO_OMNI_URL", "http://127.0.0.1:8190/v1/omni")
_omni_oracle_cache: dict = {"ts": 0.0, "lines": []}


def tool_roll_kind(row: dict) -> str:
    """Board chatter vs other info-only callouts."""
    tool = str(row.get("tool") or row.get("name") or "").strip().lower()
    text = str(row.get("text") or "").strip().lower()
    if tool.startswith("board") or "board" in tool:
        return "board"
    if "board" in text or " a card" in text or "that card" in text:
        return "board"
    return "tool"


HEARD_ROLL = STATE / "heard-roll.jsonl"
HEARD_ROLL_S = 600.0
# A NOTE IS NOT HER LINE (round 19, .101 2026-10-14: "Listening off. Say Crissa to talk again." and the cancel summaries
# were drawn under her name, as words she said): a `status` or `cancel` row is the system's, so it carries `note` and
# both Walls draw it dimmed under "Note:", never under her name and never as the User's line. Only words she says carry
# her label. Kill: CRISSA_LOG_NOTES=0 (drawn as before).
LOG_NOTES = os.environ.get("CRISSA_LOG_NOTES", "1").strip().lower() not in ("0", "false", "no", "off")


def recent_heard_roll(n: int = 24, now: float | None = None) -> list[dict]:
    """What the ear heard that was not sent to her, her cancels and the session's end, for the roll (round 15 (d)(e)(g),
    the pack's bridge writes <state>/heard-roll.jsonl; the house's never does). A heard row reads "<words> · <why>"."""
    if not HEARD_ROLL.is_file():
        return []
    now = time.time() if now is None else float(now)
    try:
        rows = HEARD_ROLL.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []
    out: list[dict] = []
    for raw in rows:
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        text = str(d.get("text") or "").strip()
        ts = float(d.get("ts") or 0) or 0.0
        if not text or (ts and now - ts > HEARD_ROLL_S):
            continue
        why = str(d.get("why") or "").strip()
        kind = str(d.get("kind") or "heard")
        # SIDE TALK (round 18, the User 2026-10-13: "Id like to talk with someone else while she speaks without
        # interrupting her"): the bridge wrote it with lane "room" - room speech on the log, the plain words, the
        # ROOM lane (log_room), never "sent"; one_row_chat shows it as a speech row once it has taken the ear's own
        room = kind == "heard" and str(d.get("lane") or "") == "room"
        note = LOG_NOTES and kind in ("status", "cancel")
        shown = ("%s \u00b7 %s" % (text, why)) if (why and kind == "heard" and not room) else text
        row = {"who": str(d.get("who") or "?").strip().lower() or "?", "text": shown[:260], "ts": ts,
               "kind": "heard" if kind == "heard" else kind, "source": "heard_roll",
               "log_user": not (room or note), "stamped": True, "certain": True, "said": text[:260]}
        if room:
            row.update(lane="room", log_room=True, why=why)
        if note:
            row["note"] = True                   # round 19: the system's line, drawn "Note:", dimmed
        out.append(row)
    return out


# ONE HEARD LINE, ONE ROW, IN TIME ORDER, ONE SPEAKER (round 17, .101 2026-10-13 05:54-05:56, the Wall's log): "Keep
# reading \u00b7 said over her line: answered after it" was in the chat 6 times; the heard roll's rows came after the ear's
# whatever their time; "Hello! What can we do together?" was listed first and last (the face shell's chat already holds
# the mouth's line, and this feed appended it again when nothing was live); and "Keep reading, please." was a stranger
# row from the ear and a User row from the bridge. Now: identical heard rows inside HEARD_SAME_S are one; a heard row
# takes the place of the ear's own row of the same words inside HEARD_MERGE_S (the bridge's speaker wins; the row names
# the ear's row in `replaces`, so a Wall that already drew it rewrites it in place); the mouth's line is not added twice;
# the whole roll is in time order. House effect: the house's bridge writes no heard roll, so on the house this only
# keeps the mouth's line from being listed twice and sorts rows that the face shell already sorted.
# Kill: VO_CHAT_ONE_ROW=0 (round 16).
VO_CHAT_ONE_ROW = os.environ.get("VO_CHAT_ONE_ROW", "1").strip().lower() not in ("0", "false", "no", "off")
HEARD_SAME_S = 30.0
HEARD_MERGE_S = 30.0


def _chat_words(text) -> list[str]:
    return re.sub(r"[^\w\s']+", " ", str(text or "").lower()).split()


def _chat_ts(row: dict) -> float:
    try:
        return float(row.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ear_row(row: dict) -> bool:
    """A row the ear wrote for room speech (not hers, not the bridge's, not the door, not a tool)."""
    src = str(row.get("source") or "")
    return not (src in ("heard_roll", "mouth_air", "tool_callout") or src.startswith("door_")
                or str(row.get("who") or "").lower() in ("crissa", "zoe", "door")
                or str(row.get("kind") or "speech") not in ("speech", ""))


def _same_words(ear: list[str], heard: list[str]) -> bool:
    if not ear or not heard:
        return False
    if ear == heard:
        return True
    n = len(ear)
    return n >= 2 and any(heard[i:i + n] == ear for i in range(len(heard) - n + 1))


# THE LOG NEVER WAITS FOR WHO (round 17, the User 2026-10-13: "i imagine logs will run with same timing unless we just
# run live and post label speaker in history when available maybe on the way up it gets labeled by voice"). Under the
# kit's Security ID-only and Off (lib/crissa_home.security_mode) the ear sends a turn with no speaker
# (modules/ear/crissa_ear/id_trail.py: stamp source `id_trailing` / `id_off`) and, in ID-only, names it after the fact
# in <state>/ear/speaker-labels.jsonl. The face shell turns such a turn into a "stranger" row; here it is the neutral
# mark (NEUTRAL_WHO) at once, and when the label lands the SAME row carries the speaker ("probably": certain stays
# false) with `replaces` naming the keys it was drawn under, so a Wall that already drew it rewrites it where it has
# scrolled to (the Tk Wall's HOUSE_WALL_ROLL_REPLACES; the browser glass's own copy of the rule), never a second line.
# A heard row that takes such a row's place (one_row_chat) carries the same keys. On the house no turn carries those
# sources and no label file is written: nothing changes. Kill: CRISSA_LOG_LABEL_LATER=0 (the face shell's rows as they
# come).
LOG_LABEL_LATER = os.environ.get("CRISSA_LOG_LABEL_LATER", "1").strip().lower() not in ("0", "false", "no", "off")
SPEAKER_LABELS = STATE / "ear" / "speaker-labels.jsonl"
NEUTRAL_WHO = "?"
_TRAIL_SOURCES = ("id_trailing", "id_off")


def speaker_labels(path: Path | None = None, tail_bytes: int = 65536) -> dict:
    """{(ts rounded to ms, text): the latest row for that turn} from the ear's label file; {} when there is none."""
    p = Path(path or SPEAKER_LABELS)
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    out: dict = {}
    for raw in lines:
        try:
            d = json.loads(raw)
            key = (round(float(d.get("ts") or 0), 3), str(d.get("text") or "").strip()[:220])
        except (ValueError, TypeError, AttributeError):
            continue
        if key[1]:
            old = out.get(key)
            if old is None or not old.get("who") or d.get("who"):
                out[key] = d                     # a label never goes back to "no speaker yet"
    return out


def _user_pid() -> str:
    try:
        return str((json.loads((STATE / "user.json").read_text(encoding="utf-8")) or {}).get("person_id") or "") \
            .strip().lower()
    except (OSError, ValueError, AttributeError):
        return ""


def label_rows(chat: list[dict], labels: dict | None = None, user: str | None = None) -> list[dict]:
    """The face shell's rows with the speaker the ear named after the fact (see LOG_LABEL_LATER)."""
    rows = [dict(c) for c in (chat or []) if isinstance(c, dict)]
    if not LOG_LABEL_LATER:
        return rows
    labels = speaker_labels() if labels is None else labels
    uid = _user_pid() if user is None else str(user or "").strip().lower()
    for r in rows:
        if not _ear_row(r):
            continue
        text = str(r.get("text") or "").strip()
        ts = round(_chat_ts(r), 3)
        lab = labels.get((ts, text[:220]))
        if lab is None and str(r.get("source") or "") not in _TRAIL_SOURCES:
            continue                             # a stamped row (Security On, the house): as the face shell gave it
        raw = str(r.get("who") or "")
        who = str((lab or {}).get("who") or "").strip().lower()
        if who in ("crissa", "zoe", "crissa_her_tts"):
            who = ""                             # her own voice heard back is no speaker's line (the roll drops echoes)
        if not who:
            r["who"] = NEUTRAL_WHO
            r["probably"] = None
            r["log_user"], r["log_room"] = True, False      # the person at this computer, not yet named
            if raw and raw != NEUTRAL_WHO:
                r["replaces"] = [[raw, text, ts]]
            continue
        r["who"] = who
        r["probably"] = True
        r["certain"] = False
        r["stamped"] = True
        r["label_via"] = (lab or {}).get("via")
        r["log_user"] = bool(uid) and who == uid
        r["log_room"] = not r["log_user"]
        r["replaces"] = [[k, text, ts] for k in dict.fromkeys((NEUTRAL_WHO, raw)) if k and k != who]
        if LOG_NAME_FALLBACK:
            r["replaces"].append([ANY_WHO, text, ts])      # round 18: whatever name a Wall drew it under (a guess)
    return rows


def one_row_chat(chat: list[dict], heard: list[dict], mouth: dict | None = None) -> list[dict]:
    """The roll as one row per line (see VO_CHAT_ONE_ROW): `chat` the face shell's, `heard` recent_heard_roll()'s,
    `mouth` live_mouth_line()'s."""
    rows = [dict(c) for c in (chat or []) if isinstance(c, dict)]
    kept: list[dict] = []
    for h in sorted((dict(x) for x in (heard or []) if isinstance(x, dict)), key=_chat_ts):
        if any(k.get("who") == h.get("who") and k.get("text") == h.get("text") and k.get("kind") == h.get("kind")
               and abs(_chat_ts(h) - _chat_ts(k)) < HEARD_SAME_S for k in kept):
            continue
        kept.append(h)
    for h in kept:
        if h.get("kind") != "heard":
            continue
        hw = _chat_words(h.get("said") or h.get("text"))
        took = [r for r in rows if _ear_row(r) and -HEARD_MERGE_S <= _chat_ts(h) - _chat_ts(r) <= HEARD_MERGE_S
                and _same_words(_chat_words(r.get("text")), hw)]
        if took:
            h["replaces"] = [[str(r.get("who") or ""), str(r.get("text") or "").strip(), round(_chat_ts(r), 3)] for r in took]
            # round 17: the keys a labelled row was drawn under (label_rows) go with it
            h["replaces"] += [list(x) for r in took for x in (r.get("replaces") or ()) if list(x) not in h["replaces"]]
            if h.get("who") in ("", "?"):
                h["who"] = str(took[0].get("who") or "?")
                if took[0].get("probably") and h["who"] != NEUTRAL_WHO:
                    h["probably"] = True             # named after the fact: the heard row was drawn as "?" before
                    h["replaces"].append([NEUTRAL_WHO, str(h.get("text") or "").strip(), round(_chat_ts(h), 3)])
                    if LOG_NAME_FALLBACK:
                        h["replaces"].append([ANY_WHO, str(h.get("text") or "").strip(), round(_chat_ts(h), 3)])
                for k in ("label_via", "guess"):     # round 18: the heard row keeps how the ear's row was named
                    if took[0].get(k) is not None and h.get(k) is None:
                        h[k] = took[0][k]
            ids = {id(r) for r in took}
            rows = [r for r in rows if id(r) not in ids]
    for h in kept:
        if h.get("lane") == "room" and h.get("kind") == "heard":
            h["kind"] = "speech"                     # round 18: side talk is room speech on the log (log_room)
    rows.extend(kept)
    if mouth and not any(r.get("live") for r in rows):
        same = [r for r in rows if str(r.get("who") or "").lower() == str(mouth.get("who") or "").lower()
                and str(r.get("text") or "").strip() == str(mouth.get("text") or "").strip()]
        if not same:
            rows.append(dict(mouth))
        elif mouth.get("live"):
            same[-1]["live"] = True
    rows.sort(key=_chat_ts)
    return rows


# A NAME BEFORE THE ROW SCROLLS OFF (round 18, the User 2026-10-13: "names on the rows before they scroll off"; .101
# round 17, 15:08: "logs are not getting names on there before scrolls off" - his voice was never recognised, `pid null`
# throughout, so the "?" rows stayed "?"). Under Security ID-only a row is named by the first recogniser that knows:
# the voice print or the face at the camera while the line was said (the ear's speaker-labels.jsonl, via voice / face /
# voice+face; crissa_ear/id_trail.py names by the face a few ms after the row). A row no recogniser has named yet, or
# that none could name, is drawn at once as "probably <name>" (guess: true; label_via says why) rather than "?":
#   session    the person a recogniser last named in this conversation: the latest named line at or before this one
#              inside NAME_FALLBACK_S (300 s, the window her own reference note uses), else the first one after it
#   only_user  nobody named yet, and this install knows one person, the User (the identity store's people/ holds
#              him alone): the person at this computer
# Never her own words heard back (a row whose words are all inside one of her lines or rows of the ten minutes before
# it stays as it is, so both Walls still drop it as her echo), never a row the ear stamped (Security On, the house),
# never in Off (no recognition there by the User's own rule). A recogniser's later name rewrites the same line in place:
# every row named after the fact lists ANY_WHO in `replaces` (whatever name a Wall drew it under).
# Kill: CRISSA_LOG_NAME_FALLBACK=0 (round 17: "?" until a voice print names it; the ear's face label goes with it).
LOG_NAME_FALLBACK = os.environ.get("CRISSA_LOG_NAME_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")
NAME_FALLBACK_S = float(os.environ.get("CRISSA_LOG_NAME_FALLBACK_S", "300") or 300)
ANY_WHO = "*"
_HER_WHO = ("crissa", "zoe", "crissa_her_tts")
_NOT_PEOPLE = ("crissa", "zoe", "crissa_her_tts", "announcer", "crew_tts")


def _person(who) -> bool:
    w = str(who or "").strip().lower()
    return bool(w) and w not in _NOT_PEOPLE and w not in (NEUTRAL_WHO, ANY_WHO, "stranger", "unknown", "door",
                                                            "oracle", "someone") \
        and not (w[0] == "s" and w[1:].isdigit())


def _people_known() -> list[str]:
    """The people this install knows (the identity store's people/), her own prints and housekeeping dirs left out."""
    root = Path(os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT")
                or _machine_path("identity_root", STATE / "identity")) / "people"
    try:
        return sorted(p.name.strip().lower() for p in root.iterdir()
                      if p.is_dir() and _person(p.name) and not p.name.startswith(("_", ".")))
    except OSError:
        return []


def _her_echo(row: dict, her: list[tuple[float, list[str]]]) -> bool:
    """Are the row's words all inside one of her lines (or her rows) begun in the HEARD_ROLL_S before it?"""
    w = _chat_words(row.get("said") or row.get("text"))
    ts = _chat_ts(row)
    return bool(w) and any(ts - HEARD_ROLL_S <= at <= ts + 5.0 and _same_words(w, hw) for at, hw in her)


def her_lines(chat: list[dict], rows: list[dict] | None = None) -> list[tuple[float, list[str]]]:
    """(start, words) of her lines on the roll and of her rows (whole), for the echo test."""
    out = [(_chat_ts(r), _chat_words(r.get("text"))) for r in chat or []
           if str(r.get("who") or "").lower() in _HER_WHO]
    out += [(_chat_ts(r), _chat_words(r.get("_full") or r.get("text"))) for r in rows or []]
    return out


def name_fallback(chat: list[dict], labels: dict | None = None, user: str | None = None, mode: str | None = None,
                  people: list[str] | None = None, her: list[tuple[float, list[str]]] | None = None) -> list[dict]:
    """Rows still "?" under ID-only as "probably <name>" (see A NAME BEFORE THE ROW SCROLLS OFF)."""
    rows = [dict(c) for c in (chat or []) if isinstance(c, dict)]
    if not (LOG_NAME_FALLBACK and LOG_LABEL_LATER):
        return rows
    if mode is None:
        try:
            mode = crissa_home.security_mode()
        except Exception:  # noqa: BLE001 - unreadable: no guess
            mode = ""
    if mode != "id_only":
        return rows
    labels = speaker_labels() if labels is None else labels
    uid = _user_pid() if user is None else str(user or "").strip().lower()
    known: list[tuple[float, str]] = sorted(
        (float(k[0]), str(v.get("who")).strip().lower()) for k, v in labels.items()
        if isinstance(v, dict) and _person(v.get("who")))
    known += [(_chat_ts(r), str(r.get("who")).strip().lower()) for r in rows
              if r.get("label_via") not in (None, "session", "only_user") and _person(r.get("who"))]
    known.sort()
    if her is None:
        her = [(_chat_ts(r), _chat_words(r.get("text"))) for r in rows
               if str(r.get("who") or "").lower() in _HER_WHO]
    only = None
    if uid:
        ppl = _people_known() if people is None else [str(x).lower() for x in people]
        if ppl == [uid]:
            only = uid
    for r in rows:
        if str(r.get("who") or "") != NEUTRAL_WHO:
            continue
        if not (str(r.get("source") or "") in _TRAIL_SOURCES or r.get("kind") == "heard"):
            continue
        if _her_echo(r, her):
            continue                             # her own words heard back: both Walls drop it as her echo
        ts = _chat_ts(r)
        before = [w for t, w in known if ts - NAME_FALLBACK_S <= t <= ts + 0.001]
        after = [w for t, w in known if ts < t <= ts + NAME_FALLBACK_S]
        who, via = (before[-1], "session") if before else ((after[0], "session") if after else (only, "only_user"))
        if not who:
            continue
        text = str(r.get("text") or "").strip()
        r["who"], r["probably"], r["guess"], r["label_via"] = who, True, True, via
        r["certain"] = False
        r["log_user"] = bool(uid) and who == uid
        r["log_room"] = not r["log_user"]
        reps = [list(x) for x in (r.get("replaces") or ())]
        for k in ([NEUTRAL_WHO, text, round(ts, 3)], [ANY_WHO, text, round(ts, 3)]):
            if k not in reps:
                reps.append(k)
        r["replaces"] = reps
    return rows


# HER WORDS AS THEY STREAM, BEFORE HER VOICE (round 18, the User 2026-10-13: "her words streamed live, before synthesis
# if possible, with any unspoken rest marked when she is cut"; .101 15:08: of 14 rows none was her reply - her long
# ones never reached the log, which carried only the piece on the air when a 2 s poll caught it). The pack's bridge
# writes each reply as one row while the model streams it (<state>/her-words.json, modules/ear/crissa_bridge/
# her_words.py): its text by whole clauses, the pieces handed to her voice, and where a stop cut her (the cancel
# record's own cut point). Here a row is ONE log line (`rid`, the same line on every read, so both Walls grow it in
# place), a window of her text around where her voice is: HER_BACK characters she has said and HER_AHEAD still to come
# while it is open, the cut and HER_CUT_AHEAD of what she never said after a stop, the end of it when it is done; "..."
# where the window leaves text out. `voice_n` (open) is how much of the line her voice has reached - her pieces by the
# bridge's own clock, SPEAK_CPS characters a second after SPEAK_LEAD_S (aired_part's numbers), never ahead of the voice
# - and `said_n` (cut, or a reply she did not finish) how much she said: the Walls dim what is still to come and strike
# what she never said, "(not said)". The mouth's line for a piece of a row is not listed again. On the house no bridge
# writes the file: nothing changes. Kill: CRISSA_LOG_LIVE_WORDS=0 (round 17's log: the mouth's line).
LOG_LIVE_WORDS = os.environ.get("CRISSA_LOG_LIVE_WORDS", "1").strip().lower() not in ("0", "false", "no", "off")
HER_WORDS = STATE / "her-words.json"
HER_BACK = 240
HER_AHEAD = 240
HER_CUT_AHEAD = 160
HER_TAIL = 400
HER_CPS = float(os.environ.get("EAR_BRIDGE_SPEAK_CPS", "14") or 14)
HER_LEAD_S = float(os.environ.get("EAR_BRIDGE_SPEAK_LEAD_S", "1.5") or 1.5)
HER_MORE = "…"


def _word_start_at(text: str, n: int) -> int:
    """`n` moved forward to the start of a word (n itself when it already is one)."""
    n = max(0, min(len(text), int(n)))
    if n == 0 or n >= len(text) or text[n - 1].isspace():
        return n
    sp = text.find(" ", n)
    return len(text) if sp < 0 else sp + 1


def _word_end_at(text: str, n: int) -> int:
    """`n` moved back to the end of a word (n itself when it already is one)."""
    n = max(0, min(len(text), int(n)))
    if n >= len(text) or text[n].isspace():
        return n
    sp = text.rfind(" ", 0, n)
    return max(0, sp)


def _voice_at(row: dict, now: float) -> int:
    """How far into the row her voice has reached: the pieces said whole, and the piece on the air by the clock."""
    reached = 0
    for p in row.get("pieces") or []:
        try:
            o0, o1, t0, t1 = int(p[0]), int(p[1]), float(p[2]), p[3]
        except (TypeError, ValueError, IndexError):
            continue
        if t1 is not None:
            reached = max(reached, o1)
        else:
            reached = max(reached, min(o1, o0 + int(max(0.0, now - t0 - HER_LEAD_S) * HER_CPS)))
    return reached


def her_words_rows(now: float | None = None, path: Path | None = None) -> list[dict]:
    """Her replies as log lines (see HER WORDS AS THEY STREAM), oldest first; [] with no file or the kill switch."""
    if not LOG_LIVE_WORDS:
        return []
    now = time.time() if now is None else float(now)
    try:
        d = json.loads(Path(path or HER_WORDS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for row in (d.get("rows") if isinstance(d, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        rid = str(row.get("rid") or "")
        try:
            ts = float(row.get("ts") or 0.0)
            at = float(row.get("at") or ts)
        except (TypeError, ValueError):
            continue
        if not text.strip() or not rid or now - at > HEARD_ROLL_S:
            continue
        state = str(row.get("state") or "done")
        said_n = row.get("said_n")
        said_n = None if said_n is None else max(0, min(len(text), int(said_n)))
        voice = _voice_at(row, now) if state == "open" else None
        if state == "open":
            mid = voice or 0
            lo, hi = mid - HER_BACK, mid + HER_AHEAD
            if lo < 0:
                lo, hi = 0, hi - lo
        elif said_n is not None:
            lo, hi = said_n - HER_BACK, said_n + HER_CUT_AHEAD
        else:
            lo, hi = len(text) - HER_TAIL, len(text)
        lo = _word_start_at(text, max(0, lo))
        hi = _word_end_at(text, min(len(text), hi)) if hi < len(text) else len(text)
        if hi <= lo:
            lo, hi = 0, len(text)
        head = (HER_MORE + " ") if lo > 0 else ""
        shown = head + text[lo:hi].strip() + ((" " + HER_MORE) if hi < len(text) else "")
        off = len(head) - lo

        def _in(n):
            return max(0, min(len(shown), n + off)) if n is not None else None
        r = {"who": "crissa", "rid": rid, "text": shown, "ts": round(ts, 3), "kind": "speech", "source": "her_words",
             "certain": True, "stamped": True, "log_user": False, "log_room": False, "open": state == "open",
             "full_chars": len(text), "_full": text}
        if state == "open":
            r["voice_n"] = _word_end_at(shown, _in(voice or 0)) if voice else (len(head) if head else 0)
        if said_n is not None and state != "open":
            r["said_n"] = _word_end_at(shown, _in(said_n)) if said_n > 0 else len(head)
            r["cut"] = state == "cut"
        out.append(r)
    out.sort(key=_chat_ts)
    return out


def her_words_merge(chat: list[dict], rows: list[dict]) -> list[dict]:
    """The log with her rows in it: a line of hers whose words are inside one of her rows (the mouth's line for a
    piece of it) is not listed again; the whole roll in time order."""
    if not rows:
        return chat
    full = [(r, _chat_words(r.get("_full") or r.get("text"))) for r in rows]
    out = []
    for c in chat or []:
        if str(c.get("who") or "").lower() in _HER_WHO and str(c.get("source") or "") in ("mouth_air", ""):
            w = _chat_words(c.get("text"))
            t = _chat_ts(c)
            if w and any(_chat_ts(r) - 5.0 <= t <= _chat_ts(r) + HEARD_ROLL_S and _same_words(w, fw) for r, fw in full):
                continue
        out.append(c)
    for r, _fw in full:
        r.pop("_full", None)
        out.append(r)
    out.sort(key=_chat_ts)
    return out


def recent_tool_chat(n: int = 24) -> list[dict]:
    """Crissa spoken tool-info lines for the wall roll."""
    if not TOOL_ROLL.is_file():
        return []
    out: list[dict] = []
    try:
        rows = TOOL_ROLL.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []
    for raw in rows:
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        text = str(d.get("text") or "").strip()
        if not text:
            continue
        kind = tool_roll_kind(d)
        who = str(d.get("who") or "crissa").strip().lower() or "crissa"
        out.append({
            "who": who,
            "text": text[:220],
            "ts": float(d.get("ts") or 0) or 0.0,
            "kind": kind,
            "source": "tool_callout",
            "stamped": True,
            "certain": True,
        })
    # One row per phrase. Keep last write so a later "Checking memory."
    # does not resurrect every retry from the jsonl.
    order: list[str] = []
    by: dict[str, dict] = {}
    for row in out:
        t = str(row.get("text") or "")
        if t not in by:
            order.append(t)
        by[t] = row
    return [by[t] for t in order]


def _oracle_prose(text: str) -> str:
    """Wall line: Argus sentence, not the trailing visit JSON."""
    t = str(text or "").strip()
    for sep in ("```json", "```"):
        i = t.find(sep)
        if i > 0:
            t = t[:i].strip()
            break
    return " ".join(t.split())[:220]


def recent_oracle_chat(n: int = 16) -> list[dict]:
    """Argus door-describe is dead. Wall does not show mill prose."""
    return []
    now = time.time()
    if _omni_oracle_cache["lines"] and (now - _omni_oracle_cache["ts"]) < 2.0:
        return list(_omni_oracle_cache["lines"])
    lines: list[dict] = []
    try:
        # Omni turns are newest-first. n=24 then lines[-16:] kept the OLDEST
        # Argus rows, so the wall missed the 08:26 lap (measured).
        status, body, _ = fetch(OMNI_URL + "?n=48", timeout=1.5)
        if status != 200 or not body:
            return list(_omni_oracle_cache["lines"] or [])
        payload = json.loads(body)
        for t in payload.get("turns") or []:
            if not isinstance(t, dict):
                continue
            stamp = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
            src = str(stamp.get("source") or t.get("via") or "")
            pid = str(t.get("person_id") or stamp.get("person_id") or "").strip().lower()
            if not (src.startswith("argus") or pid == "argus"):
                continue
            txt = _oracle_prose(t.get("text") or "")
            if not txt:
                continue
            try:
                ts = float(t.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            lines.append({
                "who": "oracle",
                "text": txt,
                "ts": ts,
                "kind": "oracle",
                "source": src or "argus",
                "stamped": False,
                "certain": False,
            })
        lines.sort(key=lambda r: float(r.get("ts") or 0))
        lines = lines[-n:]
        _omni_oracle_cache["ts"] = now
        _omni_oracle_cache["lines"] = lines
    except Exception:
        return list(_omni_oracle_cache["lines"] or [])
    return lines


def worst_level(*levels: str) -> str:
    """AND of lights: any yellow → yellow, any red → red."""
    worst = "ok"
    for lv in levels:
        key = lv if lv in _LEVEL_RANK else "bad"
        if _LEVEL_RANK[key] > _LEVEL_RANK[worst]:
            worst = key
    return worst


def journal_units(unit: str) -> list[str] | None:
    """Allowlisted unit names for one journalctl. 'all' = every CP service."""
    if _cpw is not None and _cpw.on():                # an avatar's own unit, a timer and its service (house only)
        return _cpw.journal_units(unit)
    allow = {n for n, _lbl, _port in SERVICES}
    name = str(unit or "all").strip().lower()
    if name in ("", "all", "*"):
        return sorted(allow)
    if name not in allow:
        return None
    if name in GROUP_TILES:
        return [u for _w, u, _p in GROUP_TILES[name]]
    return [real_unit(name)]


def unit_scope(name: str) -> str:
    """user|system for an allowlisted unit, from the services cache when warm."""
    data = _svc_cache.get("data") or {}
    for r in data.get("services") or []:
        if r.get("unit") == name and r.get("scope"):
            return r["scope"]
    return unit_status(name).get("scope") or "user"


# A PROFILE ROW'S TAPE AND SCRIPT UNDER THE HOST (the User 2026-10-01): lib/feed_house_rows (house-only).
_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")



def tail_log(path: Path, n: int, *, since: float | None = None, chunk: int = 65536) -> list[str]:
    """The last `n` lines of a log, read backwards from the end; `since` keeps lines stamped at or after it
    (a continuation line - a traceback - follows the stamp before it)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = pos = f.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= n:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
                if end - pos > 64 * chunk:          # 4 MiB is plenty for any n the page asks for
                    break
    except OSError as e:
        return [f"{path}: {type(e).__name__}"]
    lines = data.decode("utf-8", errors="replace").splitlines()
    if pos > 0 and lines:
        lines = lines[1:]                            # the first line may be cut mid-way
    if since is not None:
        kept, keep = [], False
        for ln in lines:
            m = _LOG_TS.match(ln)
            if m:
                try:
                    keep = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) >= float(since)
                except (ValueError, OverflowError):
                    keep = True
            if keep:
                kept.append(ln)
        lines = kept
    return lines[-n:]


def read_unit_logs(units: list[str], *, label: str = "all", n: int = 100,
                   since: float | None = None, **paged) -> dict:
    """The tape for a tile (or the fleet): each unit's journal in its OWN scope
    (a system unit read "-- No entries --" under --user), a unit's log files,
    and for Searxng the krew's searches first. On the house the tape pages (lib/panel_wiring: `paged` = n, before,
    file; a marker on every cut, keys masked, a Whole log link)."""
    got = _cpw.read_unit_logs(units, label=label, n=n, since=since, **paged) if _cpw is not None else None
    if got is not None:
        return got
    tape = _rows.profile_tape(units, label, n, since) if _rows else None   # a profile row under the host
    if tape:
        return tape
    out = panel_units.read_journal([(u, unit_scope(u)) for u in units], n=n,
                                   files=len(units) == 1, since=since)
    if units == ["searxng"]:
        try:
            out["lines"] = panel_units.search_lines(80) + ["", "── searxng journal ──"] + out["lines"]
        except Exception as e:  # noqa: BLE001
            out["lines"].insert(0, f"krew searches unreadable: {type(e).__name__}: {e}"[:160])
    out.update(unit=label, units=units)
    return out


def read_journal(units: list[str], *, label: str = "all", n: int = 100) -> dict:
    """One journalctl for one or many units — not N processes."""
    svc = [
        u if u.endswith(".service") else f"{u}.service"
        for u in units
    ]
    cmd = ["journalctl", "--user", "--no-pager", "-o", "short-iso", "-n", str(n)]
    for u in svc:
        cmd.extend(["-u", u])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout
    except Exception as e:
        return {
            "ok": False, "unit": label, "units": svc, "lines": [],
            "error": f"{type(e).__name__}: {e}"[:160],
        }
    lines = [ln[:LOG_LINE_MAX] for ln in (out or "").splitlines() if ln.strip()][-n:]
    return {"ok": True, "unit": label, "units": svc, "lines": lines}


def _camera_lane_files() -> list[Path]:
    """Newest daily face-ID jsonl. Not door-fanout / Argus mill."""
    if not CAMERA_LOG_DIR.is_dir():
        return []
    files = [
        p for p in CAMERA_LOG_DIR.glob("*.jsonl")
        if p.is_file() and p.name[:8].isdigit()
    ]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files[-1:]


def tail_lane(lane: str) -> dict:
    """Last N lines of a named lane's logs. Allowlisted names only.

    The page may ask for "camera"/"voice"/"security" and nothing else — it can
    never name a path, for the same reason ACTIONS is a fixed table.
    """
    got = _cpw.tail_lane(lane) if _cpw is not None else None     # paged, marked, a Whole log link (house only)
    if got is not None:
        return got
    key = str(lane or "").strip().lower()
    if key == "camera":
        paths = _camera_lane_files()
    else:
        paths = LOG_LANES.get(key)
    if key not in LOG_LANES:
        return {"ok": False, "error": f"unknown lane {lane!r}", "lines": []}
    if not paths:
        return {"ok": True, "lane": lane, "lines": [], "note": "no camera log yet"}
    lines: list[str] = []
    for p in paths:
        try:
            if not p.is_file():
                continue
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-LOG_TAIL_LINES:]
            if tail:
                lines.append(f"── {p.name} ──")
                lines += [t.rstrip("\n")[:LOG_LINE_MAX] for t in tail]
        except OSError:
            continue
    if not lines:
        # Say which file is gone. "Nothing recorded yet" reads like a quiet
        # house; a missing source is a wiring fault and must not look the same.
        missing = [str(x) for x in paths if not x.is_file()]
        note = ("no feed — source missing: " + ", ".join(missing)) if missing \
            else "source is present but empty"
        return {"ok": True, "lane": lane, "lines": [], "note": note}
    return {"ok": True, "lane": lane, "lines": lines[-LOG_TAIL_LINES * 2:]}


CRYSTAL_LOG = Path(
    os.environ.get("HOUSE_CRYSTAL_LOG") or (STATE / "logs" / "crystal-gate.jsonl")
).expanduser()
# What the last line of the gate log means for the lamp. The gate refuses a
# board complete with no deliverable, no decision pin and no host action; if
# it cannot load its checker it blocks everything instead, which is safe but
# is not the gate working. Both states need to be visible (2026-08-26).
_CRYSTAL_STATE = {
    "wrapped": ("ok", "armed"),
    "preflight": ("ok", "armed"),
    "gate_disabled": ("off", "switched off"),
    "checker_missing": ("bad", "checker will not load"),
    "import_fail": ("bad", "board tools did not import"),
    "no_complete_handler": ("bad", "nothing to gate"),
    "block_missing_checker": ("bad", "blocking every complete"),
}


def crystal_summary() -> dict:
    """Last word from the crystal gate, for the panel lamp."""
    out = {"health": "unknown", "text": "no word yet", "ts": None}
    try:
        if not CRYSTAL_LOG.is_file():
            out["text"] = "no gate log"
            return out
        # Take the last line whose event we recognise. A trailing one-off
        # (a bad ctx in a preflight, say) must not hide whether the gate is
        # armed — that is the whole question this lamp answers.
        row = None
        with CRYSTAL_LOG.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    cand = json.loads(line)
                except ValueError:
                    continue
                if str(cand.get("event") or "") in _CRYSTAL_STATE:
                    row = cand
        if row is None:
            return out
    except (OSError, ValueError):
        out["health"] = "bad"
        out["text"] = "gate log unreadable"
        return out
    ev = str(row.get("event") or "")
    health, text = _CRYSTAL_STATE.get(ev, ("warn", ev or "unknown"))
    out["health"], out["text"], out["ts"] = health, text, row.get("ts")
    return out


def _household_ids() -> set[str]:
    """Enrolled people on this box. A named regular is not an incident."""
    root = Path(os.environ.get("HOUSE_ID_ROOT") or _machine_path("identity_root", STATE / "identity")) / "people"
    ids: set[str] = set()
    try:
        for p in root.iterdir():
            name = p.name.strip().lower()
            if p.is_dir() and name and not name.startswith(("_", ".")):
                ids.add(name)
    except OSError:
        pass
    ids.update({"crissa", "crissa_her_tts", "announcer", "crew_tts"})
    return ids


def _incident_open(d: Path, today: str, known: set[str]) -> tuple[bool, float]:
    """Today + speaker this house cannot place. Archive dirs stay on disk."""
    ts = d.stat().st_mtime
    pid = ""
    report = d / "report.json"
    if report.is_file():
        try:
            r = json.loads(report.read_text(encoding="utf-8"))
            if r.get("ts") is not None:
                ts = float(r["ts"])
            sp = r.get("speaker") if isinstance(r.get("speaker"), dict) else {}
            pid = str(sp.get("person_id") or "").strip().lower()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    if time.strftime("%Y-%m-%d", time.localtime(ts)) != today:
        return False, ts
    if pid and pid in known:
        return False, ts
    return True, ts


def security_summary() -> dict:
    """Today's unknown-speaker incidents, plus whether the ear is holding audio.

    The on-disk archive is never pruned (evidence). The lamp is a live
    readout: midnight (local) clears it, and enrolled names do not light it.
    `archive` is the lifetime dir count, for the log, not the glass.
    """
    out: dict = {"ok": True, "incidents": 0, "latest": None, "cache": None,
                 "archive": 0, "crystal": crystal_summary()}
    try:
        if INCIDENTS_DIR.is_dir():
            dirs = [d for d in INCIDENTS_DIR.iterdir() if d.is_dir()]
            out["archive"] = len(dirs)
            today = time.strftime("%Y-%m-%d")
            known = _household_ids()
            open_rows: list[float] = []
            for d in dirs:
                try:
                    keep, ts = _incident_open(d, today, known)
                except OSError:
                    continue
                if keep:
                    open_rows.append(ts)
            out["incidents"] = len(open_rows)
            if open_rows:
                out["latest"] = time.strftime("%H:%M", time.localtime(max(open_rows)))
    except OSError:
        pass
    try:
        st, body, _ = fetch(STT_BASE + "/v1/security/voice_cache")
        if st == 200 and body:
            d = json.loads(body)
            if d.get("enabled"):
                sec = float(d.get("seconds") or 0)
                if sec < 0.5:
                    out["cache"] = "idle"
                else:
                    out["cache"] = f"{d.get('seconds', 0)}s held"
            else:
                out["cache"] = "off"
    except Exception:
        out["cache"] = "ear not answering"
    return out


def _probe_http(url: str, timeout: float = 0.4) -> str:
    st, body, _ = fetch(url, timeout=timeout)
    if not st:
        return "bad"
    if not (200 <= int(st) < 300):
        return "warn"
    if not body:
        return "ok"
    try:
        data = json.loads(body.decode() if isinstance(body, bytes) else body)
    except Exception:
        return "ok"
    if isinstance(data, dict) and data.get("ok") is False:
        return "warn"
    if isinstance(data, dict) and data.get("status") not in (None, "ok", "healthy"):
        return "warn"
    return "ok"


CUA_SOCK = Path.home() / ".cache/cua-driver/cua-driver.sock"
CUA_BIN = Path.home() / ".local/bin/cua-driver"


# THE RESTART KEY THAT IS OWED TO THE WINDOW, CRISSA'S ROW ON THE HOST (the User 2026-10-01 / 10-04): the rows
# whose Restart requests a window restart, the User's logged override and the shared-gateway row live in
# lib/feed_house_rows (house-only). These shims answer "no such row" without it.
def _row_name(unit: str) -> str:
    return unit[:-len(".service")] if unit.endswith(".service") else unit


def _override_on() -> bool:
    return bool(_rows and _rows._override_on())


def crissa_row_on_host(unit: str) -> bool:
    return bool(_rows and _rows.crissa_row_on_host(unit))


def window_restart_target(unit: str) -> str | None:
    return _rows.window_restart_target(unit) if _rows else None


def restart_confirm_text(unit: str) -> str:
    return _rows.restart_confirm_text(unit) if _rows else ""


def gateway_restart_to_window(unit: str) -> dict | None:
    return _rows.gateway_restart_to_window(unit) if _rows else None


def log_restart_override(unit: str, target: str, *, client: str = "", agent: str = "",
                         path: Path | None = None) -> dict:
    return _rows.log_restart_override(unit, target, client=client, agent=agent, path=path) if _rows else {}


# THE BULB GLOW ON VOICEONLY (the User 2026-10-02; OFF until HOUSE_VO_THINKING_PULSE=1). The Wall's K
# (87f50d6f, HOUSE_WALL_THINKING_PULSE): the ear bridge writes <state home>/crissa-thinking.json from the moment
# his turn is sent (EAR_BRIDGE_THINKING), and her dot breathes while it says `on`, until one of her lines
# reaches the roll (so the glow stops AT her line). Same rule here, computed once per /v1/house; the page draws
# the breath. Off: no `thinking` key at all - the page is today's, byte for byte.
VO_THINK_MAX_S = 180.0


def vo_thinking_pulse_on() -> bool:
    return os.environ.get("HOUSE_VO_THINKING_PULSE", "0").strip().lower() in ("1", "true", "yes", "on")


def _vo_thinking_file() -> Path:
    return STATE / "crissa-thinking.json"


def vo_thinking(chat: list, now: float | None = None, read=None) -> dict:
    """{pulse, since, seq}: pulse while the thinking file is `on`, fresh, and no Crissa line came after `since`."""
    now = time.time() if now is None else float(now)
    try:
        th = read() if read else json.loads(_vo_thinking_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        th = {}
    if not isinstance(th, dict) or th.get("on") is not True:
        return {"pulse": False}
    try:
        since = float(th.get("since") or 0.0)
    except (TypeError, ValueError):
        return {"pulse": False}
    if since <= 0 or not (0.0 <= now - since <= VO_THINK_MAX_S):
        return {"pulse": False}
    for c in chat or []:
        if str(c.get("who") or "").lower() in ("crissa", "zoe"):
            try:
                if float(c.get("ts") or 0.0) >= since:
                    return {"pulse": False}        # her line is up: stop
            except (TypeError, ValueError):
                continue
    return {"pulse": True, "since": since, "seq": th.get("seq")}


def _probe_cua() -> str:
    """House cua-driver socket — unit up is not enough if serve died."""
    if not CUA_SOCK.exists():
        return "bad"
    cmd = str(CUA_BIN) if CUA_BIN.is_file() else "cua-driver"
    try:
        r = subprocess.run(
            [cmd, "status", "--socket", str(CUA_SOCK)],
            capture_output=True, text=True, timeout=1.5,
        )
    except Exception:
        return "warn"
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "not running" not in out.lower():
        return "ok"
    return "warn"


def overlay_serve_health(rows: list[dict]) -> None:
    """systemd `active` is not serving. Loading vLLM is yellow, not red.

    Occupancy parks are yellow (expected). Dead + not parked is red.
    Gateways waiting on a loading/parked brain are yellow, not an average red.
    """
    by = {r.get("unit"): r for r in rows}
    mode = read_occupancy_mode()
    crew_p = _probe_served(_engine_url("crew", "/v1/models"), "crew")
    ops_p = _probe_served(_engine_url("ops", "/v1/models"), "ops")
    for unit in ("ops", "crew", "turbo-voice", *RENDER_WAKE_UNITS, *RENDER_SLEEP_UNITS, "crissa-display"):
        r = by.get(unit)
        if not r:
            continue
        if occupancy_parked(unit, mode):
            r["health"] = "warn"
            if unit in ("crissa-display", "crissa-display.service"):
                r["note"] = "parked in Wall desktop ON"
            else:
                r["note"] = f"parked in {mode}"
        elif occupancy_waking(unit, mode) and r.get("health") == "bad":
            r["health"] = "warn"
            r["note"] = "waking"
    for unit, probe, starting in (
        ("crew", crew_p, "starting — :8005 not serving yet"),
        ("ops", ops_p, "starting — :8000 not serving yet"),
    ):
        r = by.get(unit)
        if not r or occupancy_parked(unit, mode):
            continue
        if probe == "ok":
            continue
        if r.get("health") == "bad":
            continue
        r["health"] = "warn"
        r["note"] = starting
    # Ada rides crew in Standard and Render. Canonical Render parks ops.
    ada_probe = crew_p
    ada_wait = "waiting on krew"
    for unit, probe, waiting in (
        (u, ada_probe, ada_wait) for u in (_PANEL.get("brain_wait_rows") or [])   # the agent rows riding crew
    ):
        r = by.get(unit)
        if not r or r.get("health") == "bad" or occupancy_parked(unit, mode):
            continue
        if probe == "ok":
            continue
        r["health"] = "warn"
        r["note"] = waiting
    dash = by.get(DASHBOARD_ROW) if DASHBOARD_ROW else None
    if dash and dash.get("health") != "bad" and crew_p != "ok" and ops_p != "ok":
        dash["health"] = "warn"
        dash["note"] = "waiting on a brain"
    _overlay_host_panel(by, crew_p, ops_p)


def _probe_served(url: str, want: str, timeout: float = 0.4) -> str:
    st, body, _ = fetch(url, timeout=timeout)
    if st != 200 or not body:
        return "bad"
    try:
        data = json.loads(body.decode() if isinstance(body, bytes) else body)
    except Exception:
        return "bad"
    ids = [
        str(x.get("id") or "")
        for x in (data.get("data") or [])
        if isinstance(x, dict)
    ]
    if want in ids:
        return "ok"
    return "warn" if ids else "bad"


_SCRIPTS = Path(__file__).resolve().parent
# Occupancy parks these GPU tenants. Restart from the panel must not
# resurrect them (Beast: ops onto leftover-fit Unsloth). Canonical Render
# parks ops so Comfy fits next to crew. Crissa's mouth sleeps.
# ... which units each occupancy parks is this machine's (config `panel.occupancy_parked`);
# a pack parks nothing but its own Comfy, which the render module does itself.
OCCUPANCY_PARKED: dict[str, frozenset[str]] = {
    str(k): frozenset(v or []) for k, v in (_PANEL.get("occupancy_parked") or {}).items()}
for _m in ("beast", "render", "normal", "heavy-render", "off"):
    OCCUPANCY_PARKED.setdefault(_m, frozenset())


def read_occupancy_mode() -> str:
    """The GPU mode flag file. Missing file = Normal (same as the mode switch)."""
    try:
        first = GPU_MODE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return "normal"
    if first in {"render", "normal", "beast", "off", "heavy-render"}:
        return first
    return "normal"


_BATTERY_JOBS: dict[str, dict] = {}
_BATTERY_LOCK = threading.Lock()
T3_CONFIRM = "will speak, move camera, restart ws-shadow"


def _battery_latest() -> dict:
    latest = Path.home() / ".local" / "state" / "house" / "health" / "latest.json"
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        return {
            "state": data.get("state") or "done",
            "tier": data.get("tier_run"),
            "worst": data.get("worst"),
            "ok": data.get("ok"),
            "job_id": data.get("job_id") or "",
            "clamped": data.get("clamped"),
            "note": data.get("note") or "",
        }
    except (OSError, ValueError):
        return {"state": "idle"}


def _battery_public() -> dict:
    """In-flight job wins so the Health pip goes yellow while a check runs."""
    with _BATTERY_LOCK:
        running = [
            dict(r) for r in _BATTERY_JOBS.values() if r.get("state") == "running"
        ]
    if running:
        rec = running[-1]
        rec.setdefault("worst", "")
        rec.setdefault("ok", None)
        rec["state"] = "running"
        rec.setdefault("note", "Health check running")
        return rec
    return _battery_latest()


# --- Run-once speed check -------------------------------------------------
# The User 2026-09-05: "just a Run once button ... keep it from running when no one
# cares". There was nothing continuous to switch off -- vllm-log.service reads
# logs and has spent 7 CPU seconds in 28 hours -- so the gap was the other way
# round: the tok/s and TTFT numbers only existed when someone ran a script by
# hand. This is that script, behind a button, and it runs ONLY when pressed.
_SPEED_LOCK = threading.Lock()
_SPEED_JOBS: dict[str, dict] = {}
_SPEED_ENGINES = (("ops", "/v1/chat/completions"),     # the path; the engine's base is _engine_url (house config)
                  ("crew", "/v1/chat/completions"))
SPEED_N = 4            # concurrent callers -- matches how the house actually loads them
SPEED_MAX_TOKENS = 48  # short: this measures the queue, not the essay


def _speed_one(url: str, model: str, i: int) -> tuple:
    """One streamed completion. Returns (ttft_s, total_s, chunks)."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user",
                      "content": f"Name three primary colours. (variant {i})"}],
        "max_tokens": SPEED_MAX_TOKENS, "temperature": 0, "stream": True,
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"content-type": "application/json"})
    t0 = time.time()
    ttft = None
    n = 0
    with urllib.request.urlopen(req, timeout=60) as f:
        for line in f:
            if not line.startswith(b"data: "):
                continue
            if line.strip() == b"data: [DONE]":
                break
            try:
                d = json.loads(line[6:])
            except ValueError:
                continue
            ch = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
            if ch:
                if ttft is None:
                    ttft = time.time() - t0
                n += 1
    return (ttft, time.time() - t0, n)


def _speed_engine(name: str, url: str) -> dict:
    """SPEED_N concurrent callers at one engine. Never raises."""
    out: list = []
    errs: list = []

    def go(i: int) -> None:
        try:
            out.append(_speed_one(url, name, i))
        except Exception as e:            # a parked engine is a result, not a crash
            errs.append(f"{type(e).__name__}")

    t0 = time.time()
    ts = [threading.Thread(target=go, args=(i,), daemon=True)
          for i in range(SPEED_N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=75)
    wall = time.time() - t0
    good = [r for r in out if r[0] is not None]
    if not good:
        return {"engine": name, "ok": False,
                "error": (errs[0] if errs else "no answer")}
    ttfts = sorted(r[0] for r in good)
    lats = sorted(r[1] for r in good)
    chunks = sum(r[2] for r in good)
    mid = len(ttfts) // 2
    return {
        "engine": name, "ok": True, "n": len(good),
        "ttft_ms": int(ttfts[mid] * 1000),
        "lat_s": round(lats[mid], 2),
        # SSE deltas per second, not true tokens -- MTP can carry several per
        # delta. Comparable between engines and between runs; do not quote it
        # as a raw token rate.
        "rate": round(chunks / wall, 1) if wall > 0 else 0.0,
        "wall_s": round(wall, 2),
    }


def start_speed_job() -> dict:
    """Fire one speed check. Refuses if one is already in flight."""
    import uuid
    mode = read_occupancy_mode()
    if mode in ("render", "off"):
        return {"ok": False, "error": "refused",
                "note": f"Not in {mode} — the engines are parked. "
                        "Switch to Standard first."}
    with _SPEED_LOCK:
        for r in _SPEED_JOBS.values():
            if r.get("state") == "running":
                return {"ok": False, "error": "busy",
                        "note": "A speed check is already running."}
        job_id = uuid.uuid4().hex[:12]
        rec = {"state": "running", "job_id": job_id, "engines": []}
        _SPEED_JOBS[job_id] = rec
        # Keep the last few only; this dict is process-lifetime.
        for old in list(_SPEED_JOBS)[:-6]:
            _SPEED_JOBS.pop(old, None)

    def _go() -> None:
        rows = []
        try:
            for name, path in _SPEED_ENGINES:
                url = _engine_url(name, path)
                rows.append(_speed_engine(name, url) if url else {"engine": name, "ok": False})
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:200]
        parts = []
        for r in rows:
            if r.get("ok"):
                parts.append(f"{r['engine']} {r['ttft_ms']}ms TTFT · "
                             f"{r['lat_s']}s · {r['rate']}/s")
            else:
                parts.append(f"{r['engine']} no answer")
        with _SPEED_LOCK:
            rec["engines"] = rows
            rec["note"] = f"{SPEED_N} concurrent · " + " | ".join(parts)
            rec["ok"] = any(r.get("ok") for r in rows)
            rec["state"] = "done"
            rec["ts"] = time.strftime("%H:%M:%S")

    threading.Thread(target=_go, name="house-speedcheck", daemon=True).start()
    return {"ok": True, "job_id": job_id,
            "note": f"Speed check running — {SPEED_N} concurrent on ops and crew."}


def start_battery_job(tier: int, *, disruptive: bool = False) -> dict:
    import uuid
    from house_battery import run_battery
    from house_battery.interlock import evaluate

    inter = evaluate(tier)
    if tier >= 3 and inter.clamped and inter.run == 1:
        return {"ok": False, "error": "refused", "reason": inter.reason, "exit": 2}
    with _BATTERY_LOCK:
        for r in _BATTERY_JOBS.values():
            if r.get("state") == "running":
                return {"ok": False, "error": "busy",
                        "note": "A health check is already running."}
        job_id = uuid.uuid4().hex[:12]
        rec = {"state": "running", "job_id": job_id, "tier": inter.run,
               "note": "Health check running"}
        _BATTERY_JOBS[job_id] = rec
        for old in list(_BATTERY_JOBS)[:-6]:
            _BATTERY_JOBS.pop(old, None)

    def _go() -> None:
        try:
            report = run_battery(
                tier=tier, disruptive=disruptive, job_id=job_id, interlock=inter
            )
            rec.update(report.as_dict())
            rec["state"] = "done"
            worst = rec.get("worst") or "SKIP"
            rows = rec.get("results") or []
            fails = sum(
                1 for r in rows
                if (r.get("status") if isinstance(r, dict) else "") == "FAIL"
            )
            rec["note"] = f"Health {worst}" + (f" · {fails} failed" if fails else "")
            rec["ts"] = time.strftime("%H:%M:%S")
        except Exception as e:
            rec["state"] = "done"
            rec["ok"] = False
            rec["worst"] = "FAIL"
            rec["error"] = f"{type(e).__name__}: {e}"[:200]
            rec["note"] = "Health FAIL"

    threading.Thread(target=_go, name="house-battery", daemon=True).start()
    return {"ok": True, "job_id": job_id, "tier": inter.run, "clamped": inter.clamped,
            "reason": inter.reason, "note": "Health check running"}


def prod_locked() -> bool:
    try:
        first = PROD_LOCK_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return False
    return first in ("on", "1", "true", "lock", "prod")


def occupancy_parked(name: str, mode: str | None = None) -> bool:
    mode = read_occupancy_mode() if mode is None else mode
    bare = name[:-8] if str(name).endswith(".service") else str(name)
    if bare in OCCUPANCY_PARKED.get(mode, frozenset()) or name in OCCUPANCY_PARKED.get(mode, frozenset()):
        return True
    # Wallpaper is the glass: Crissa Brave kiosk down is law (not a crash).
    if bare == "crissa-display":
        try:
            import glass_mode
            if glass_mode.read_mode() == "wallpaper":
                return True
        except Exception:
            pass
    return False


def train_hold_units() -> set[str]:
    """Keeper hold file — Render-sleep names with or without .service."""
    try:
        lines = TRAIN_HOLD_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    out: set[str] = set()
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        bare = s[:-8] if s.endswith(".service") else s
        out.add(bare)
        out.add(bare + ".service")
    return out


def occupancy_waking(name: str, mode: str | None = None) -> bool:
    """Ada bounce / Crissa sleep around a Render flip — warn, not fleet-red.

    Ada is not parked in Render (she dispatches). She is restarted on enter
    and leave, so pending render|normal paints her waking, not dead.
    """
    if name not in RENDER_WAKE_UNITS:
        return False
    mode = read_occupancy_mode() if mode is None else mode
    if occupancy_parked(name, mode):
        return False
    pending = read_occupancy_pending()
    if pending in ("normal", "render"):
        return True
    return name in train_hold_units() or f"{name}.service" in train_hold_units()


def occupancy_exec_paths(name: str, mode: str | None = None) -> list[str]:
    """Start script for this unit in this occupancy (display order).

    crew.service ExecStart is always start-crew.sh. Beast execs start-beast.sh
    (leftover-fit Unsloth). SCRIPT used to dump Muse flags underneath.
    """
    mode = read_occupancy_mode() if mode is None else mode
    if name == "crew":
        if mode == "beast":
            return [str(_SCRIPTS / "start-beast.sh")]
        return [str(_SCRIPTS / "start-crew.sh")]
    if name == "ops":
        return [str(_SCRIPTS / "start-ops.sh")]
    return []


def live_cmd_line(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    text = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    return text or None


def live_engine_script(name: str, live: str | None, mode: str | None = None) -> list[str]:
    """Prefer the process that is actually running over the occupancy flag."""
    if live:
        if name == "crew":
            if "start-beast" in live or (
                "Qwen3.8-27B-NVFP4-unsloth" in live
                and "served-model-name crew" in live
            ):
                return [str(_SCRIPTS / "start-beast.sh")]
            if "Muse-Glimmer" in live or "muse-glimmer" in live or "muse_glimmer" in live:
                return [str(_SCRIPTS / "start-crew.sh")]
        if name == "ops" and (
            "Qwen3.8" in live or "served-model-name ops" in live or "start-ops" in live
        ):
            return [str(_SCRIPTS / "start-ops.sh")]
    return occupancy_exec_paths(name, mode)


def panel_brain(method: str, body: dict | None = None) -> tuple[int, dict]:
    """GET|POST /panel/brain, the ops brain picker (the User 2026-10-20): house-only, the wiring's one `brain` hook
    (lib/panel_wiring -> lib/brain_picker). A pack, or a tree without the wiring, answers 404."""
    if _cpw is None or not crissa_home.is_house() or not hasattr(_cpw, "brain"):
        return 404, {"ok": False, "error": "no brain picker in this edition"}
    return _cpw.brain(method, body or {})


def restart_blocked_reason(unit: str, mode: str | None = None) -> str | None:
    mode = read_occupancy_mode() if mode is None else mode
    if unit in NO_PANEL_RESTART:
        return NO_PANEL_RESTART[unit]
    if occupancy_parked(unit, mode):
        return f"{unit} is parked in {mode}"
    return None


def _pending_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_occupancy_pending() -> str:
    """In-flight mode switch (panel click or a card). Empty if idle or writer died."""
    try:
        lines = GPU_PENDING_FILE.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    first = lines[0].strip().lower()
    if first not in ("render", "normal", "beast", "off", "heavy-render"):
        return ""
    if len(lines) < 2:
        try:
            GPU_PENDING_FILE.unlink()
        except OSError:
            pass
        return ""
    try:
        pid = int(lines[1].strip())
    except ValueError:
        try:
            GPU_PENDING_FILE.unlink()
        except OSError:
            pass
        return ""
    if not _pending_pid_alive(pid):
        try:
            GPU_PENDING_FILE.unlink()
        except OSError:
            pass
        return ""
    return first


def occupancy_glass(
    mode: str,
    *,
    comfy: bool,
    ops_ok: bool,
    crew_ok: bool,
    cosy_ok: bool,
) -> str | None:
    """Silicon glass — same tests as Already-* on /v1/action. None = in flight."""
    if mode == "render" and comfy and (ops_ok or crew_ok):
        return "render"
    if mode == "beast" and not comfy and crew_ok and not ops_ok:
        return "beast"
    if mode == "normal" and not comfy and ops_ok and crew_ok:
        return mode
    if mode == "off" and not comfy and not ops_ok and not crew_ok and not cosy_ok:
        return "off"
    # heavy-render: crew off on purpose, ops (Crissa's brain) + Comfy up; Comfy down between batches
    # reads as in flight
    if mode == "heavy-render" and comfy and ops_ok and not crew_ok:
        return "heavy-render"
    return None


_ACT_TO_MODE = {
    "mode_render": "render",
    "mode_normal": "normal",
    "mode_beast": "beast",
    "mode_off": "off",
}

_TURBO_KREW_MOD = None


def _turbo_krew_mod():
    global _TURBO_KREW_MOD
    if _TURBO_KREW_MOD is not None:
        return _TURBO_KREW_MOD
    import importlib.util

    path = HOUSE_TURBO_KREW_BIN
    # the house's TurboKrew flame (scripts/house-turbo-krew.py) rides its crew engine; a pack has neither, so the
    # load by path is absent-ok: the readout says "no TurboKrew in this edition" and nothing else changes
    try:
        if not path.is_file():
            raise FileNotFoundError(str(path))
        spec = importlib.util.spec_from_file_location("house_turbo_krew", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"turbo krew missing: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"no TurboKrew in this edition ({type(e).__name__})") from e
    _TURBO_KREW_MOD = mod
    return mod


def turbo_krew_status() -> dict:
    """Cheap JSON+YAML pin read. No HTTP. Panel polls this every 2s."""
    try:
        return _turbo_krew_mod().status_snapshot()
    except Exception:
        return {"on": False, "remaining_s": 0, "ok": False}


def occupancy_already(
    act: str,
    mode: str,
    *,
    pending: str = "",
    comfy: bool,
    ops_ok: bool,
    crew_ok: bool,
    cosy_ok: bool,
) -> str | None:
    """Destination if this action is a no-op. Pending never Already (B3/B4)."""
    want = _ACT_TO_MODE.get(act)
    if not want or pending:
        return None
    glass = occupancy_glass(
        mode, comfy=comfy, ops_ok=ops_ok, crew_ok=crew_ok, cosy_ok=cosy_ok
    )
    return want if glass == want else None


def _health_from_svc_cache(unit: str) -> str | None:
    data = _svc_cache.get("data")
    if not isinstance(data, dict):
        return None
    for row in data.get("services") or []:
        if isinstance(row, dict) and row.get("unit") == unit:
            h = row.get("health")
            return str(h) if h else None
    return None


# ALL OS OFF IN A PACK (the User 2026-10-28, "Crissa Wall + Krew = house now"; the gap list's item 2): this page's
# All OS Off key posts house_off / house_on and reads gpu.os_off, and until now a pack's feed had no half for either
# (the house's half is lib/house_os_power, house-only). The switch lives in the kit's Control Panel server
# (scripts/crissa-panel-server.py, lib/crissa_os_power: os_off / os_on, GET /v1/os_power), so the feed forwards:
# the state into gpu.os_off, the two actions as os_off / os_on, and /v1/os_power as it is. House: untouched.
# Kill CRISSA_FEED_OS_POWER=0 (the key stays hidden, as before).
PACK_OS_POWER_TIMEOUT_S = 8.0


def pack_os_power_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return (not crissa_home.is_house()) and str(e.get("CRISSA_FEED_OS_POWER", "1")).strip().lower() not in ("0", "off", "false", "no")


def _panel_base() -> str:
    return (os.environ.get("HOUSE_WALL_PANEL") or crissa_ports.url("panel") or "").rstrip("/")


def pack_os_power_call(path: str, body: dict | None = None, timeout: float = PACK_OS_POWER_TIMEOUT_S,
                       opener=None) -> dict:
    """One call to the kit panel server's power routes; a panel that does not answer is said, never a 500."""
    base = _panel_base()
    if not base:
        return {"ok": False, "error": "no Control Panel server in this install (crissa_ports has no panel)"}
    url = base + path
    try:
        if body is None:
            req = urllib.request.Request(url, headers=caller_headers())
        else:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers=dict(caller_headers(), **{"Content-Type": "application/json"}))
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            got = json.loads(r.read().decode() or "{}")
        return got if isinstance(got, dict) else {"ok": False, "error": "the Control Panel answered no object"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "the Control Panel did not answer (%s): %s" % (type(e).__name__, str(e)[:120])}


def pack_os_off_state(opener=None) -> dict | None:
    """The kit switch's state for gpu.os_off ({in_force, n, paused, since, failed, return_mode}); None off / unknown."""
    if not pack_os_power_on():
        return None
    got = pack_os_power_call("/v1/house", opener=opener, timeout=3.0)
    st = got.get("os_off") if isinstance(got.get("os_off"), dict) else None
    return st


def pack_os_power_action(act: str, opener=None) -> dict | None:
    """house_off / house_on from this page -> the kit panel's os_off / os_on. None when not a pack or switched off."""
    if act not in ("house_off", "house_on") or not pack_os_power_on():
        return None
    got = pack_os_power_call("/v1/action", {"action": {"house_off": "os_off", "house_on": "os_on"}[act]}, opener=opener)
    out = dict(got, action=act, os_power=True)
    if got.get("ok") and "started" not in got:
        out["started"] = True
    return out


def gpu_mode_snapshot() -> dict:
    mode = read_occupancy_mode()
    pending = read_occupancy_pending()
    state: dict = {}
    try:
        raw = json.loads(GPU_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state = raw
    except (OSError, json.JSONDecodeError):
        pass
    comfy = False
    try:
        with urllib.request.urlopen(COMFY_BASE + "/system_stats", timeout=1) as r:
            comfy = r.status == 200
    except Exception:
        comfy = False
    ops_ok = _health_from_svc_cache("ops") == "ok"
    crew_ok = _health_from_svc_cache("crew") == "ok"
    cosy_ok = _health_from_svc_cache("turbo-voice") == "ok"
    glass = occupancy_glass(
        mode, comfy=comfy, ops_ok=ops_ok, crew_ok=crew_ok, cosy_ok=cosy_ok
    )
    crissa_kiosk = False
    glass_kind = ""
    try:
        import glass_mode
        crissa_kiosk = glass_mode.is_active(glass_mode.BRAVE) if pack_asks_house_units() else False
        glass_kind = glass_mode.read_mode()
    except Exception:
        pass
    parked = list(state.get("parked") or [])
    if occupancy_parked("crissa-display", mode) and "crissa-display" not in parked:
        parked.append("crissa-display")
    turbo: dict = {"on": False, "remaining_s": 0}
    try:
        turbo = turbo_krew_status()
    except Exception:
        turbo = {"on": False, "remaining_s": 0}
    return {
        "mode": mode,
        "pending": pending or None,
        "glass": glass,
        "label": GPU_LABELS.get(str(glass or mode or ""), str(glass or mode or "—")),
        "recipe": state.get("recipe"),
        "parked": parked,
        "comfy": comfy,
        "crissa_kiosk": crissa_kiosk,
        "glass_kind": glass_kind,
        "turbo_krew": turbo,
        # a pack: the kit switch's state, so this page's All OS Off key shows and says what is paused (2026-10-28)
        **({"os_off": _pack_os_off} if (_pack_os_off := pack_os_off_state()) is not None else {}),
    }


def _part_tile_row(name: str, label: str, port) -> dict:
    base, kind = PART_TILES[name]
    r = dict(unit_status(base), label=label, port=port, href=OPEN_HREF.get(name),
             section=_SECTION_OF.get(name, "Also running"))
    r["unit"], r["part_of"] = name, base
    if r.get("active") == "active":
        level, note = part_tile_health(kind, _part_tile_body(port))
        if _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(str(r.get("health") or "ok"), 0):
            r["health"] = level
        if level != "ok":       # the page draws a note in the title row: only when it is asking for you
            r["note"] = note
        r["detail"] = note
    return r


def unit_truth(t: dict, status=None) -> dict:
    """A launcher tile whose child runs as its own unit (service mode) reads the UNIT's state (the User 2026-10-10, .101
    round 3: this page said "1 UNIT DOWN: STT" while crissa-stt had run steadily for minutes). The launcher's table
    keeps the pid it started; systemd's own restarts give the unit a new one, so the old pid read as "its process is
    gone". The Control Panel (:8097) already let a live crissa-<name> unit speak for its tile (without_live_units);
    here the tile keeps its name, log and keys and takes the unit's active / sub / health / note. A child with no
    unit (process mode, or no user manager) keeps the table's reading."""
    name = str(t.get("proc") or "")
    if not name:
        return t
    if status is None:
        # only a child the launcher wrote a unit for is asked (process mode asks systemd nothing: no idle cost)
        try:
            import crissa_service
            if not (crissa_service.unit_dir() / crissa_service.desk_unit_name(name)).is_file():
                return t
        except Exception:  # noqa: BLE001
            return t
    u = (status or unit_status)("crissa-%s" % name)
    if str(u.get("active") or "") in ("", "not-found", "unknown"):
        return t
    if t.get("standby") and str(u.get("active") or "") != "active":
        return t      # a voice the launcher holds, "installed, not running" (round 17): its stopped unit is not a fault
    out = dict(t)
    out.update(active=u.get("active") or "", sub=u.get("sub") or "", health=u.get("health") or "",
               restarts=int(u.get("restarts") or 0), note=u.get("note") or "", unit_scope=u.get("scope"),
               service="crissa-%s.service" % name)
    if out["health"] == "ok":
        out.pop("parked", None)
    return out


def _pack_rows() -> list[dict]:
    """A pack's rows when no house unit list is configured (2026-10-09): the launcher's children
    (lib/crissa_procs: proc:<name> tiles with their state, port, log and a named reason when one is down) and the
    Control Panel's add-on tiles (lib/crissa_panel_addons: an installed add-on's manifest under <state>/panel.d,
    krew's for one; a slot with no manifest is its placeholder with the off mark). A unit tile an add-on names is
    read with unit_status like any house row. Nothing here runs on the house (its SERVICES is never empty)."""
    rows: list[dict] = []
    try:
        import crissa_procs
        pv = crissa_procs.view(STATE)
        for t in pv.get("tiles") or [] if pv.get("present") else []:
            rows.append(unit_truth(dict(t, href=None, scope="launcher")))
    except Exception as e:  # noqa: BLE001
        log(f"pack rows: launcher table unreadable: {type(e).__name__}: {e}")
    try:
        import crissa_panel_addons as addons
        import crissa_panel_conf as conf
        av = addons.view(conf.load(), [])
        for t in av.get("tiles") or []:
            common = {"label": t.get("label"), "port": t.get("port"), "href": t.get("href"), "section": t["section"],
                      "addon": t["addon"], "id": t.get("id") or "", "run": t.get("run") or None, "off": t.get("off") or None}
            if t["kind"] == "link":
                rows.append(dict(common, unit="", scope="", active="", sub="", restarts=0, health="", note="", link=True))
            elif t["kind"] == "status":
                st = addons.read_status(t["status"], t.get("stale_s") or 900)
                rows.append(dict(common, unit=conf.bare(t.get("unit") or ""), scope=t.get("scope") or "", active="",
                                 sub="", restarts=0, health=st["health"], note=st["note"], report=True))
            else:
                r = dict(unit_status(conf.bare(t["unit"])), **common)
                if t.get("masked"):
                    r.update(health="warn", note="masked - off by design", parked=True)
                rows.append(r)
        for p in av.get("missing") or []:
            rows.append({"unit": "", "scope": "", "active": "", "sub": "", "restarts": 0, "health": "", "note": p["note"],
                         "label": p["label"], "section": p["section"], "mark": p["mark"], "placeholder": True,
                         "slot": p["slot"], "href": None, "port": None})
    except Exception as e:  # noqa: BLE001
        log(f"pack rows: add-ons unreadable: {type(e).__name__}: {e}")
    return rows


def services_snapshot() -> dict:
    """Service health, cached — 13 units x systemctl is too heavy for a 2s poll."""
    now = time.time()
    if _svc_cache["data"] is not None and (now - _svc_cache["ts"]) < SVC_TTL:
        return _svc_cache["data"]
    rows = [dict(unit_status(n), label=lbl, port=port, href=OPEN_HREF.get(n),
                 section=_SECTION_OF.get(n, "Also running"))
            if n not in PART_TILES and n not in GROUP_TILES
            else (_part_tile_row(n, lbl, port) if n in PART_TILES else group_tile_row(n, lbl))
            for n, lbl, port in SERVICES]
    pack_cap = None
    if not SERVICES:
        rows += _pack_rows()      # a pack: the launcher's children and the add-on tiles are the roster
        pack_cap = capability()
        every = {r.get("unit") for r in rows}
        rows, _ = pack_fleet(rows, pack_cap)
        folded = every - {r.get("unit") for r in rows}     # a piece this computer cannot run: not in any group
    for r in rows:
        if _host_tile() is not None and _row_name(r["unit"]) == _host_gateway_row():
            r["profiles"] = host_profile_dots(str(r.get("health") or ""))
            r["shared_by"] = host_shared_by(r["profiles"])
        if r["unit"] in NO_PANEL_RESTART:
            r.update(restart=False, restart_why=NO_PANEL_RESTART[r["unit"]])
        elif window_restart_target(r["unit"]):
            r.update(restart_via_window=True, restart_confirm=restart_confirm_text(r["unit"]))
    overlay_serve_health(rows)
    if _cpw is not None:
        rows = _cpw.decorate(rows)       # the extra tiles' kind and fold, each avatar's lamp, who watches it (house)
    by_unit = {r["unit"]: r["health"] for r in rows}
    summary = []
    mode = read_occupancy_mode()
    by_row = {r["unit"]: r for r in rows}
    for sid, (label, units, desc) in SVC_GROUPS.items():
        if pack_cap is not None:
            # only the pieces this launcher runs (a row in its table): one it never started is not down (round 4)
            units = tuple(u for u in units if u not in folded and u in by_unit)
        live = units
        level = worst_level(*(by_unit.get(u, "bad") for u in live))
        if pack_cap is not None and not live:
            level = "off"                         # none of this group runs in this install: neither green nor red
        # /v1/models already folded into crew/ops by overlay_serve_health
        # (starting = yellow). Do not probe again — connection-refused would
        # turn a loading brain red.
        if sid == "zoe" and pack_cap is None and _host_panel():
            level = worst_level(level, _crissa_on_host(by_unit))
        if pack_cap is not None:
            pass                                  # a pack: its pieces' own rows ARE the lamp (no house probes)
        elif sid == "zoe" and level != "bad":
            zoe_bits = [by_unit.get("cua-driver", "bad") if _host_panel() else _probe_cua()]
            if not _crissa_gateway_parked(mode):
                zoe_bits.append(_probe_http(_crissa_health_url()))
            if not occupancy_parked("turbo-voice", mode):
                if _voice_health_url():
                    zoe_bits.append(_probe_http(_voice_health_url()))
            level = worst_level(level, *zoe_bits)
        elif sid == "stt" and level != "bad":
            level = worst_level(
                level,
                _probe_http(STT_BASE + "/health"),
                _probe_http(ASR_BASE + "/health"),
            )
        why = ""
        if sid == "krew":
            why = (by_row.get("crew") or {}).get("note") or (
                "starting — :8005 not serving yet" if level == "warn" else ""
            )
        elif sid == "zoe" and level != "ok" and pack_cap is not None:
            # a pack names its own pieces (never the house's gateway, CUA, Cosy, ops)
            why = " · ".join("%s %s" % (str((by_row.get(u) or {}).get("label") or u).split(" - ")[0],
                                         (by_row.get(u) or {}).get("note") or "down")
                             for u in live if by_unit.get(u, "bad") != "ok" and u in by_row)
        elif sid == "zoe" and level != "ok":
            bits = []
            if _crissa_gateway_parked(mode):
                bits.append(f"gateway parked ({mode})")
            elif _host_panel() and _crissa_on_host(by_unit) != "ok":
                bits.append("gateway " + _crissa_host_note(by_row))
            elif not _host_panel() and CRISSA_GATEWAY_ROW and by_unit.get(CRISSA_GATEWAY_ROW) != "ok":
                bits.append(
                    "gateway "
                    + ((by_row.get(CRISSA_GATEWAY_ROW) or {}).get("note") or "down")
                )
            if by_unit.get("cua-driver") != "ok":
                bits.append("CUA " + ((by_row.get("cua-driver") or {}).get("note") or "down"))
            if occupancy_parked("turbo-voice", mode):
                bits.append(f"Cosy parked ({mode})")
            elif by_unit.get("turbo-voice") != "ok":
                bits.append("Cosy " + ((by_row.get("turbo-voice") or {}).get("note") or "down"))
            if occupancy_parked("ops", mode):
                bits.append(f"Ops parked ({mode})")
            elif by_unit.get("ops") != "ok":
                bits.append("Ops " + ((by_row.get("ops") or {}).get("note") or "down"))
            why = " · ".join(bits)
        elif sid == "stt" and level == "warn":
            why = "ear or speech HTTP not answering"
        summary.append({
            "id": sid,
            "label": label,
            "chip": _SVC_CHIP.get(sid, label),
            "desc": desc,
            "level": level,
            "href": "/panel#svc",
            "section": sid in ("stt", "zoe", "krew"),
            "parts": {u: by_unit.get(u, "bad") for u in units},
            "why": why,
        })
    data = {
        "services": rows,
        "summary": summary,
        "fleet": dict(pack_fleet(rows)[1], **pack_running()) if pack_cap is not None else {
            "total": len(rows),
            "ok": sum(1 for r in rows if r["health"] == "ok"),
            "warn": sum(1 for r in rows if r["health"] == "warn"),
            "down": sum(1 for r in rows if r["health"] == "bad"),
        },
        "announcer_muted": ANNOUNCE_MUTE.is_file(),
                "wanted_poster": WANTED_FLAG.is_file(),
        "voice_only": VOICE_ONLY_FLAG.is_file(),
        "os_power_full": OS_POWER_FULL,
        # the page is one file for both editions since the fold (2026-10-28): it reads the edition here, never guesses
        "pack": not crissa_home.is_house(),
    }
    # which rows carry Lila's tape is this machine's (config panel.render_log_rows); both editions since the fold, the
    # house's page named the same two rows as literals before
    data["render_log_rows"] = list(_PANEL.get("render_log_rows") or [])
    if _cpw is not None:
        _cpw.finish(data)                # the count without hidden rows, the sections' folds, the watcher table
    _svc_cache["data"], _svc_cache["ts"] = data, now
    return data


# ── THE AGENT FRAMEWORK'S ROWS (the User's service sweep 2026-10-01, host tile 10-03, tile audit 10-03) ───
# All of it is lib/feed_house_rows now (house-only, 2026-10-09): the four per-profile gateway rows under ONE host
# gateway, the host tile with a dot per profile, the CUA tile read from the gateway, the sweep tiles and the
# tile-audit rows. The switches (HOUSE_PANEL_HOST_GATEWAY, HOUSE_PANEL_HOST_TILE) are read there; _rows.install()
# below applies them to the tables above exactly where the module-level calls stood. Without the module (a pack)
# the tables stay as the config and the launcher's children make them.
NO_PANEL_RESTART: dict[str, str] = {}
PART_TILES: dict[str, tuple[str, str]] = {}     # view name -> (real unit, what the tile reads)
SCRIPT_READ_ROOTS_EXTRA: tuple[Path, ...] = ()


def _host_panel():
    return globals().get("HOST_PANEL") if _rows else None      # the server's own copy (feed_house_rows.EXPORTS)


def _host_tile():
    return globals().get("HOST_TILE") if _rows else None


def _host_gateway_row() -> str:
    return _rows.HOST_GATEWAY_ROW[0] if _rows else ""


def _crissa_gateway_parked(mode: str) -> bool:
    if _rows:
        return _rows._crissa_gateway_parked(mode)
    return bool(CRISSA_GATEWAY_ROW) and occupancy_parked(CRISSA_GATEWAY_ROW, mode)


def _crissa_on_host(by_unit: dict) -> str:
    return _rows._crissa_on_host(by_unit) if _rows else "ok"


def _crissa_host_note(by_row: dict) -> str:
    return _rows._crissa_host_note(by_row) if _rows else ""


def _overlay_host_panel(by: dict, crew_p: str, ops_p: str) -> None:
    if _rows and _host_panel():          # only with the host-gateway panel on, as before the cut (the Crissa merge)
        _rows._overlay_host_panel(by, crew_p, ops_p)


def host_profile_dots(host_health: str) -> list[dict]:
    return _rows.host_profile_dots(host_health) if _rows else []


def host_shared_by(dots: list[dict]) -> str:
    return _rows.host_shared_by(dots) if _rows else ""


def real_unit(name: str) -> str:
    """The systemd unit behind a tile name (a part tile reads its service's unit, a group tile its first)."""
    if name in GROUP_TILES:
        return GROUP_TILES[name][0][1]
    return PART_TILES.get(name, (name, ""))[0]


def part_tile_health(kind: str, body: dict | None) -> tuple[str, str]:
    """(level, note) for a part tile from its service's /health body; None body = no answer."""
    if not isinstance(body, dict) or not body.get("ok"):
        return "bad", "no /health answer"
    if kind == "voice":
        if not body.get("voice_on"):
            return "warn", "voice ID is off in the Ear"
        if not body.get("voice_bank_ok"):
            return "warn", "voice bank not loaded"
        return "ok", f"encoder {body.get('voice_encoder') or '?'}"
    if body.get("camera_blind") or not body.get("camera_open"):
        return "warn", "camera closed or blind"
    if body.get("capture_fresh") is False:
        return "warn", "camera frames are stale"
    return "ok", "faces read from fresh frames"


def _part_tile_body(port) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/health", timeout=0.6) as r:
            return json.loads(r.read(200_000) or b"{}")
    except Exception:  # noqa: BLE001
        return None


if _rows is not None:
    _rows.install(globals())      # the house's framework rows, applied to the tables above


# ── ONE COMPOSER GROUP (the User 2026-10-03, same switch) ──────────────────────────────────────────────
# "Composer · Crissa" and "Composer · Lila" become one "Composer" group of three tiles - Composer, Bench, Clip
# doctor - each listing its avatars with ports ("Crissa :8110, Lila :8101"). The avatars come from
# config/composer-avatars.json, so a new avatar adds a line, not a group. A group tile's lamp is the worst of
# its avatars' units; Journal reads all of them, Script the first; no Restart (it would name several units -
# restart one by hand: the refusal says how). Running services are untouched: this is the panel's view only.
COMPOSER_AVATARS = Path(os.environ.get("HOUSE_COMPOSER_AVATARS")
                        or (Path(__file__).resolve().parent.parent / "config" / "composer-avatars.json"))
GROUP_TILES: dict[str, list[tuple[str, str, int | None]]] = {}   # tile -> [(avatar, unit, port)]
_COMPOSER_KINDS = (("composer", "Composer", "composer"), ("bench", "Bench", "bench"),
                   ("clip-doctor", "Clip doctor", "doctor"))


def composer_avatars(path: Path | None = None) -> list[dict]:
    try:
        data = json.loads((path or COMPOSER_AVATARS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [a for a in (data.get("avatars") or []) if isinstance(a, dict) and a.get("name")]


def composer_line(members: list[tuple[str, str, int | None]]) -> str:
    return ", ".join(f"{who} :{port}" if port else who for who, _u, port in members)


def _apply_composer_group() -> None:
    if _host_tile() is None:
        return
    avatars = composer_avatars()
    if not avatars:
        return
    groups = {tile: [(a["name"], (a.get(key) or {}).get("unit"), (a.get(key) or {}).get("port"))
                     for a in avatars if (a.get(key) or {}).get("unit")]
              for tile, _title, key in _COMPOSER_KINDS}
    members = {u for g in groups.values() for _w, u, _p in g}
    names = [r[0] for r in SERVICES]
    at = min((names.index(u) for u in members if u in names), default=len(SERVICES))
    rows = [(tile, f"{title} - {composer_line(groups[tile])}", None)
            for tile, title, _k in _COMPOSER_KINDS if groups[tile]]
    kept = [r for r in SERVICES[:at] if r[0] not in members]
    SERVICES[:] = kept + rows + [r for r in SERVICES[at:] if r[0] not in members]
    for tile, _l, _p in rows:
        GROUP_TILES[tile] = groups[tile]
        _SECTION_OF[tile] = "Composer"
        units = ", ".join(u for _w, u, _p in groups[tile])
        NO_PANEL_RESTART[tile] = (f"the {tile} tile is every avatar's - restart one by hand: "
                                  f"systemctl --user restart <unit> ({units})")


def group_tile_row(name: str, label: str) -> dict:
    members = GROUP_TILES[name]
    sts = [(who, unit_status(u)) for who, u, _p in members]
    level = worst_level(*(str(st.get("health") or "bad") for _w, st in sts))
    down = [f"{who} {st.get('active') or '?'}" for who, st in sts if st.get("health") != "ok"]
    return {"unit": name, "scope": "user", "active": "active" if not down else "degraded",
            "sub": "running" if not down else "", "restarts": sum(int(st.get("restarts") or 0) for _w, st in sts),
            "health": level, "note": "; ".join(down), "label": label, "port": None, "href": None,
            "section": _SECTION_OF.get(name, "Composer"),
            "members": [{"avatar": who, "unit": u, "port": p} for who, u, p in members]}


_apply_composer_group()
if _cpw is not None:
    _cpw.install(globals())       # the house's CP wiring: the untiled running units join SERVICES (house only)


def _crissa_health_url() -> str:
    if _rows:
        return _rows._crissa_health_url()
    return str(_PANEL.get("crissa_health_url") or "")


def _engine_url(name: str, path: str = "") -> str:
    """The house's model engine `name` (ops, crew) from config/house-machine.json panel.engine_urls, plus `path`;
    "" on a machine that names none (a pack: its brain is Connect's endpoint, and its feed draws no engine row)."""
    base = str((_PANEL.get("engine_urls") or {}).get(name) or "").rstrip("/")
    return base + path if base else ""


def _voice_health_url() -> str:
    """The house's voice server health (panel.voice_health_url); "" in a pack, whose voice is a launcher child."""
    return str(_PANEL.get("voice_health_url") or "")


def _systemctl(args: list[str], scope: str) -> str:
    if _rows:
        args = _rows.systemctl_args(args, scope)   # a per-profile gateway row is asked of the host unit
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=8).stdout.strip()
    except Exception:
        return ""


def unit_status(name: str) -> dict:
    """State of one unit, trying user scope then system.

    yellow = up but has been restarting; that is the signal that matters on a
    wall display — a service can be 'active' and still be thrashing.
    On the house a down unit names its reason (lib/panel_wiring.unit_status).
    """
    got = _cpw.unit_status(name) if _cpw is not None else None
    if got is not None:
        return got
    unit = name if name.endswith((".service", ".timer")) else name + ".service"
    for scope in ("user", "system"):
        out = _systemctl(["show", unit, "-p", "LoadState", "-p", "ActiveState",
                          "-p", "SubState", "-p", "NRestarts",
                          "-p", "ActiveEnterTimestampMonotonic"], scope)
        kv = dict(
            line.split("=", 1) for line in out.splitlines() if "=" in line
        )
        if kv.get("LoadState") in (None, "", "not-found"):
            continue
        active = kv.get("ActiveState", "")
        _prof, _served = _rows.profile_status(unit, active) if _rows else (None, None)
        if _prof and _served is False:
            # the host is healthy and does not serve this profile: parked, or not yet picked up
            active = "inactive"
            kv["SubState"] = "parked" if _rows._gateway_topology().parked(_prof) else "not served"
        try:
            nrest = int(kv.get("NRestarts") or 0)
        except ValueError:
            nrest = 0
        try:
            entered = int(kv.get("ActiveEnterTimestampMonotonic") or 0)
        except ValueError:
            entered = 0
        try:
            now_us = time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000
        except Exception:
            now_us = 0
        age_sec = ((now_us - entered) / 1e6) if entered and now_us else 0
        # Yellow only while it is still bouncing. Three restarts at dawn
        # and a 3-hour clean run is not thrashing — NRestarts does not
        # decay on its own.
        bouncing = nrest >= 3 and age_sec < 600
        if active == "active":
            health = "warn" if bouncing else "ok"
        elif active == "activating":
            health = "warn"
        else:
            health = "bad"
        note = ""
        if active == "not-found":
            note = "not installed"
        elif health == "bad":
            note = (kv.get("SubState") or active or "down")
        elif bouncing:
            note = f"{nrest} restarts"
        elif active == "activating":
            note = "starting"
        return {"unit": name, "scope": scope, "active": active,
                "sub": kv.get("SubState", ""), "restarts": nrest,
                "health": health, "note": note}
    return {"unit": name, "scope": None, "active": "not-found",
            "sub": "", "restarts": 0, "health": "bad", "note": "not installed"}


# Control Panel SCRIPT button: only files under these trees. The page names a
# unit; it never names a path.
_RAILS_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_READ_ROOTS: tuple[Path, ...] = (
    _RAILS_ROOT,
    Path.home() / "projects",
    Path.home() / ".config" / "systemd",
    Path("/etc/systemd"),
    Path("/usr/lib/systemd"),
    Path("/usr/local/bin"),
    Path.home() / ".local" / "bin",
)
# A profile's SOUL.md, and only that file (the User 2026-10-04): lib/feed_house_rows.soul_ok says which.
SCRIPT_MAX_BYTES = 200_000
_SCRIPT_EXTS = {".sh", ".bash", ".py", ".service", ".conf", ".timer", ".target"}
_EXEC_ARGV_RE = re.compile(r"argv\[\]=([^;]*)")
_EXEC_PATH_RE = re.compile(r"\bpath=([^;]+)")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def parse_exec_start(raw: str) -> list[str]:
    """Turn systemd ExecStart= (cgroup v2 {path=; argv[]=} or a bare line) into argv."""
    raw = raw or ""
    m = _EXEC_ARGV_RE.search(raw)
    blob = m.group(1).strip() if m else ""
    if not blob and raw.startswith("{"):
        p = _EXEC_PATH_RE.search(raw)
        return [p.group(1).strip()] if p else []
    if not blob:
        blob = raw.strip()
    if not blob:
        return []
    try:
        return shlex.split(blob)
    except ValueError:
        return blob.split()


def _safe_read_text(path: Path) -> str | None:
    try:
        real = path.expanduser().resolve()
    except OSError:
        return None
    if not real.is_file():
        return None
    soul = bool(_rows and _rows.soul_ok(real))
    if not soul and not any(_is_under(real, root) for root in SCRIPT_READ_ROOTS + SCRIPT_READ_ROOTS_EXTRA):
        return None
    try:
        size = real.stat().st_size
        with real.open("rb") as fh:
            data = fh.read(min(size, SCRIPT_MAX_BYTES) + 1)
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    truncated = len(data) > SCRIPT_MAX_BYTES
    text = data[:SCRIPT_MAX_BYTES].decode("utf-8", "replace")
    if truncated:
        text += f"\n… truncated at {SCRIPT_MAX_BYTES} bytes"
    return text


def unit_script(name: str) -> dict:
    """Start script + unit file for one allowlisted service. No path from the page."""
    if name not in {n for n, _lbl, _port in SERVICES} and not (_cpw is not None and _cpw.script_allowed(name)):
        return {"ok": False, "error": f"unit not allowlisted: {name!r}", "files": []}
    row = name
    name = _rows.host_tile_unit(row, real_unit(name)) if _rows else real_unit(name)
    st = unit_status(name)
    scope = st.get("scope") or "user"
    unit = name if name.endswith((".service", ".timer")) else name + ".service"
    out = _systemctl(
        [
            "show",
            unit,
            "-p",
            "FragmentPath",
            "-p",
            "DropInPaths",
            "-p",
            "ExecStart",
            "-p",
            "MainPID",
        ],
        scope,
    )
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    fragment = (kv.get("FragmentPath") or "").strip()
    drop_ins = [p for p in (kv.get("DropInPaths") or "").split() if p]
    argv = parse_exec_start(kv.get("ExecStart") or "")
    mode = read_occupancy_mode()
    try:
        main_pid = int(kv.get("MainPID") or 0)
    except ValueError:
        main_pid = 0
    live = live_cmd_line(main_pid)
    engine = live_engine_script(name, live, mode)
    files: list[dict] = []
    seen: set[str] = set()

    def add(path_s: str, kind: str) -> None:
        if not path_s or path_s in seen:
            return
        text = _safe_read_text(Path(path_s))
        if text is None:
            return
        seen.add(path_s)
        files.append({"path": path_s, "kind": kind, "text": text})

    for path_s in engine:
        add(path_s, "exec")
    for tok in argv:
        suf = Path(tok).suffix.lower()
        if suf in _SCRIPT_EXTS or tok.endswith(".sh"):
            add(tok, "wrapper")
    soul = _rows.soul_path(row, name) if _rows else None
    if soul:
        add(soul, "profile")   # the profile's SOUL.md, never its config / .env (keys)
    add(fragment, "unit")
    for d in drop_ins:
        add(d, "dropin")
    return {
        "ok": True,
        "unit": name,
        "scope": scope,
        "occupancy": mode,
        "parked": occupancy_parked(name, mode),
        "up": bool(live),
        "live": live,
        "engine": engine[0] if engine else None,
        "exec_start": argv,
        "fragment": fragment,
        "drop_ins": drop_ins,
        "files": files,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet; systemd journal does the logging
        pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_media(self, raw: bytes, ctype: str, cache: str = "public, max-age=3600") -> None:
        """A clip for a <video>: honour Range (206), advertise Accept-Ranges.
        Chromium's media loader reads a whole-file 200 only as far as its
        first window and then STALLS when the server cannot range — the
        Register curtain froze at 4.1 s buffered and every wizard host clip
        would too (measured 2026-09-09: buffered [0, 4.1], readyState 2,
        currentTime pinned). Same bytes, same cache header; 416 on a bad range."""
        total = len(raw)
        rng = (self.headers.get("Range") or "").strip()
        start, end = 0, total - 1
        partial = False
        m = re.match(r"bytes=(\d*)-(\d*)$", rng)
        if m and total:
            a, b = m.group(1), m.group(2)
            try:
                if a == "" and b != "":          # suffix: the last N bytes
                    n = int(b)
                    start = max(0, total - n)
                elif a != "":
                    start = int(a)
                    if b != "":
                        end = min(total - 1, int(b))
                partial = True
            except ValueError:
                partial = False
            if partial and (start > end or start >= total):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        body = raw[start:end + 1] if partial else raw
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        if not feed_guard(self):                  # round 46: this machine's pages only (FEED_GUARD_DEFAULT)
            return
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            if not PAGE.is_file():
                return self._json(500, {"ok": False, "error": f"missing {PAGE}"})
            return self._send(200, page_html(), "text/html; charset=utf-8")

        if path == "/pose-demo":
            demo = PAGE.parent / "pose-trans-demo.html"
            if not demo.is_file():
                return self._json(404, {"ok": False, "error": "missing pose-trans-demo.html"})
            return self._send(200, demo.read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/demo/"):
            name = path[len("/demo/"):].split("/")[-1]
            if name not in ("pingpong.mp4", "listen-to-work.mp4", "sits-up.mp4", "sit-down.mp4", "down-up-down.mp4"):
                return self._json(404, {"ok": False, "error": "unknown demo"})
            f = POSE_DIR / "demo" / name
            if not f.is_file():
                return self._json(404, {"ok": False, "error": f"missing {name}"})
            raw = f.read_bytes()
            return self._send_media(raw, "video/mp4", cache="no-store")


        if path == "/v1/pose":
            return self._json(200, pose_payload())

        if path == "/v1/register":
            return self._json(200, register_glass_view())

        # Dock / --app favicon. Same ZOS mark Ubuntu should show instead of a gear.
        icon_dir = PAGE.parent
        if path == "/zos.svg":
            p = icon_dir / "zos.svg"
            if p.is_file():
                return self._send(200, p.read_bytes(), "image/svg+xml")
        if path in ("/zos.png", "/favicon.ico"):
            p = icon_dir / "zos.png"
            if p.is_file():
                return self._send(200, p.read_bytes(), "image/png")
        if path in ("/zos.webmanifest", "/manifest.webmanifest"):
            p = icon_dir / "zos.webmanifest"
            if p.is_file():
                return self._send(200, her_named(p.read_bytes()), "application/manifest+json")

        if path == "/health":
            hs, _, _ = fetch(HOUSE_URL, timeout=2.0)
            with _lock:
                _clear_expired()
                s = public_screen()
            return self._json(200, {
                "ok": True, "port": PORT, "house_upstream": hs,
                "frame_cached": _frame is not None, "screen": s,
                "ramps": sorted(RAMPS_OK),
            })

        if path == "/panel/brain":
            # the ops brain picker (the User 2026-10-20): house-only, the wiring's; a pack has no ops lane
            code, out = panel_brain("GET")
            return self._json(code, out)

        if path == "/v1/restart-report":
            from urllib.parse import parse_qs, urlparse as _up

            job = (parse_qs(_up(self.path).query).get("job") or [""])[0]
            return self._json(200, panel_units.restart_report(job))

        if path == "/v1/speed-report":
            qs = {}
            if "?" in self.path:
                from urllib.parse import parse_qs
                qs = {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1]).items()}
            with _SPEED_LOCK:
                rec = dict(_SPEED_JOBS.get(qs.get("job") or "") or {})
            if not rec:
                rec = {"state": "idle"}
            return self._json(200, rec)

        if path == "/v1/health-report":
            qs = {}
            if "?" in self.path:
                from urllib.parse import parse_qs
                qs = {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1]).items()}
            job = qs.get("job") or ""
            with _BATTERY_LOCK:
                rec = dict(_BATTERY_JOBS.get(job) or {})
            if not rec:
                rec = _battery_public()
                rec["state"] = rec.get("state") or "done"
            return self._json(200, rec)

        if path == "/v1/os_power":
            if pack_os_power_on():
                return self._json(200, pack_os_power_call("/v1/os_power"))
            return self._json(404, {"ok": False, "error": "no os_power here"})
        if path == "/v1/house":
            status, body, _ = fetch(HOUSE_URL)
            if status != 200 or not body:
                # never 500 the screen; it renders what it can.
                # GPU still attached so occupancy buttons follow the board/CLI.
                svc = services_snapshot()
                return self._json(200, {
                    "ok": False,
                    "error": "house upstream down",
                    "gpu": gpu_mode_snapshot(),
                    "svc": svc,
                    "battery": _battery_public(),
                    **({} if crissa_home.is_house() else {"capability": capability()}),
                    **({} if crissa_home.is_house() else {"voice": pack_voice()}),
                })
            try:
                house = json.loads(body)
            except Exception:
                svc = services_snapshot()
                return self._json(200, {
                    "ok": False,
                    "error": "house payload unparseable",
                    "gpu": gpu_mode_snapshot(),
                    "svc": svc,
                    **({} if crissa_home.is_house() else {"capability": capability()}),
                    **({} if crissa_home.is_house() else {"voice": pack_voice()}),
                })
            with _lock:
                # The page polls this every 1.5s, so it is also the heartbeat
                # that retires the doc and stale notes on time.
                _clear_expired()
                house["screen"] = screen_for_house(house)
                # which glass is up and whom the house calls the User: the
                # Control Panel's Register / New User keys decide from these
                # (the wallpaper paints the routine itself; Brave gets pinned)
                house["glass_mode"] = "wallpaper" if wallpaper_active() else "brave"
                house["user_display"] = user_display()
                house["voice_id"] = voice_id_snapshot()
            # Service health rides the ONE poll the page already makes.
            # A second XHR from the page reliably never returned (request fired,
            # response never arrived — the page holds an SSE plus 1.5s frame
            # polling), while the identical call from the console always worked.
            # Rather than fight the connection pool, remove the extra request.
            house["svc"] = services_snapshot()
            house["gpu"] = gpu_mode_snapshot()
            house["glass"] = _glass.record()
            house["battery"] = _battery_public()
            if not crissa_home.is_house():         # the pack page greys from it; the house page never read it
                house["capability"] = capability()
            if not crissa_home.is_house():
                house["voice"] = dict(house.get("voice") or {}, **pack_voice())
            # which Restart keys the page may offer (system units need User)
            house["panel_restart"] = panel_units.restart_gate(house)
            st, body, _ = fetch(BRIDGE_BASE + "/health", timeout=0.4)
            hear = {}
            if st == 200 and body:
                try:
                    hear = json.loads(body) if isinstance(body, (bytes, str)) else {}
                except Exception:
                    hear = {}
            if isinstance(hear, dict):
                house["hear"] = {
                    "paused": hear.get("paused"),
                    "session_open": hear.get("session_open"),
                    "speaking": hear.get("speaking"),
                    "comms_hold": hear.get("comms_hold") is True,
                    # epoch s; both glasses draw HELD mm:ss from it (60 min window)
                    "comms_hold_until": float(hear.get("comms_hold_until") or 0.0),
                }
            # Glass kill switch for quiet-spin abort-to-speak. Default on.
            house["pose_spin_abort"] = os.environ.get("HOUSE_POSE_SPIN_ABORT", "1") != "0"
            # Voice during the vocative sit-up takes the speak clip, so the
            # ack is not spoken with a still mouth. 0 = hold the rise through.
            house["wake_mouth"] = os.environ.get("HOUSE_WAKE_MOUTH", "1") != "0"
            # Mouth visemes composite onto the playing clip. Default off:
            # the still bank mixes listen-user rest with the phoneme take, so
            # one window cannot serve all four. visemePaint stays gated.
            house["viseme_overlay"] = os.environ.get("HOUSE_VISEME_OVERLAY", "0") != "0"
            # Studio snapshot is cheap (json on disk). Only attach in Render
            # so Standard/Beast polls never import the Comfy cycle helpers.
            if house["gpu"].get("mode") == "render":
                try:
                    studio_snapshot = _render_studio().snapshot

                    house["studio"] = studio_snapshot()
                except Exception as e:
                    house["studio"] = {"ok": False, "error": str(e)[:80]}
            # Same glyph mapping the stream uses, so a 2s poll fallback cannot
            # paint a guest for scratch id s12. Stream is still the live path.
            if isinstance(house.get("sound"), dict):
                house["sound"] = enrich_sound(house.get("sound")) or house["sound"]
            house["oracle_chat"] = recent_oracle_chat()
            # THE FACE READ ON THE NEUTRAL KEY (the fold, 2026-10-28): the Control Panel page reads presence.face_id; the
            # house's presence names that block after its engine, and the house's adapter (lib/presence_house, absent in
            # a pack, whose presence already sends face_id) gives it the neutral name. Only face_id is copied.
            _pr = house.get("presence")
            if isinstance(_pr, dict) and not isinstance(_pr.get("face_id"), dict):
                try:
                    import presence_house
                    _fid = presence_house.neutral(_pr).get("face_id")
                    if isinstance(_fid, dict):
                        house["presence"] = dict(_pr, face_id=_fid)
                except ImportError:
                    pass
            if not crissa_home.is_house():
                sec = security_house_block(house)    # the kit's Security toggle, its lock and the ask (round 17)
                if sec is not None:
                    house["security"] = sec
            # Speech roll: drop journal-queued Crissa replies (no source). Keep
            # the line only while Cosy is paplaying it.
            chat = [c for c in (house.get("chat") or []) if isinstance(c, dict)]
            chat = [
                c
                for c in chat
                if c.get("live")
                or str(c.get("who") or "").lower() not in ("crissa", "zoe")
                or c.get("source")
            ]
            mouth = live_mouth_line()
            chat = label_rows(chat)                  # round 17: the speaker named after the fact (LOG_LABEL_LATER)
            if VO_CHAT_ONE_ROW:
                chat = one_row_chat(chat, recent_heard_roll(), mouth)    # round 17: one line, one row, by time
            else:
                chat.extend(recent_heard_roll())          # round 15: heard but not sent, cancels, listening off
                if not any(c.get("live") for c in chat):
                    if mouth:
                        chat.append(mouth)
            her_rows = her_words_rows()              # round 18: her replies as they stream (LOG_LIVE_WORDS)
            chat = name_fallback(chat, her=her_lines(chat, her_rows))   # round 18: "probably <name>", never "?"
            chat = her_words_merge(chat, her_rows)
            house["chat"] = chat
            if vo_thinking_pulse_on():
                house["thinking"] = vo_thinking(chat)
            tools = recent_tool_chat()
            spoken = {
                str(c.get("text") or "").strip()
                for c in chat
                if str(c.get("who") or "").lower() in ("crissa", "zoe")
                and str(c.get("text") or "").strip()
            }
            if mouth and str(mouth.get("text") or "").strip():
                spoken.add(str(mouth.get("text") or "").strip())
            house["tool_chat"] = [
                t for t in tools if str(t.get("text") or "").strip() not in spoken
            ]
            # The DOOR tick on the dial. Presence only carries seat_clock INSIDE a
            # room_event, so between door events the page had nothing and fell back
            # to a hardcoded 12 — an amber mark glowing at 12 forever, which is not
            # where this door is (the User 2026-08-14: "12 still glows all the time").
            #
            # heading.door_hour (dial label, this office 10). Never
            # stations.door_beam (camera sitter, this office 12).
            # Fallback, not an override: a live room_event seat_clock still wins.
            door = room_frame().get("door_hour")
            if door is not None and not (
                isinstance(house.get("room_event"), dict)
                and isinstance(house["room_event"].get("seat_clock"), dict)
                and house["room_event"]["seat_clock"].get("door_hour") is not None
            ):
                house["door_hour"] = int(door)
            # The FIXED desks on the dial: the User's seat hour + registered id
            # (initials), Crissa the centre (the User 2026-09-07). Both glasses draw them.
            try:
                house["seats"] = seats_fixed()
            except Exception:  # noqa: BLE001
                pass
            house["kiosk"] = kiosk_bounds()
            ensure_vo_os_fullscreen(house["kiosk"])
            try:
                house["init"] = init_snapshot()
            except Exception as e:
                house["init"] = {"ok": False, "needed": False, "error": str(e)[:80]}
            return self._json(200, house)

        if path == "/v1/kiosk-bounds":
            return self._json(200, kiosk_bounds())

        if path == "/v1/init":
            # ?glass=1 says the wall's wizard walker is the one asking, which is
            # what keeps the mic held for it. A plain read never closes the ear.
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            glass = (qs.get("glass") or [""])[0] in ("1", "true", "yes")
            try:
                return self._json(200, init_attend() if glass else init_snapshot())
            except Exception as e:
                return self._json(200, {"ok": False, "needed": False, "error": str(e)[:160]})

        if path == "/v1/init-heard":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                since = float((qs.get("since") or ["0"])[0] or 0)
            except (TypeError, ValueError):
                since = 0.0
            ignore = (qs.get("ignore") or [""])[0]
            try:
                return self._json(200, init_heard(since, ignore=ignore))
            except Exception as e:
                return self._json(200, {"ok": False, "text": "", "error": str(e)[:120]})

        if path == "/v1/init-mouth":
            return self._json(200, {"ok": True, "speaking": bool(init_mouth_busy())})

        if path in ("/init-wizard.js", "/init-wizard.css"):
            p = PAGE.parent / path.lstrip("/")
            if not p.is_file() or p.parent != PAGE.parent:
                return self._json(404, {"ok": False, "error": "missing"})
            ctype = (
                "application/javascript; charset=utf-8"
                if path.endswith(".js")
                else "text/css; charset=utf-8"
            )
            return self._send(200, p.read_bytes(), ctype)

        if path == "/panel":
            if not PANEL_PAGE.is_file():
                return self._json(500, {"ok": False, "error": f"missing {PANEL_PAGE}"})
            return self._send(200, panel_page_html(), "text/html; charset=utf-8")

        if path == "/composer":
            # The one Composer lives on :8199 (2026-09-11). Keep the query
            # so a ?key= deep link from a bookmark or the panel still lands
            # on its take.
            q = urllib.parse.urlparse(self.path).query
            self.send_response(302)
            self.send_header("Location", COMPOSER_URL + ("?" + q if q else ""))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path in FRAMED:
            if not FRAME_PAGE.is_file():
                return self._json(500, {"ok": False, "error": f"missing {FRAME_PAGE}"})
            return self._send(200, frame_html(path), "text/html; charset=utf-8")

        if path == "/v1/logs":
            from urllib.parse import parse_qs, urlparse as _up

            lane = (parse_qs(_up(self.path).query).get("lane") or [""])[0]
            return self._json(200, tail_lane(lane))

        if path == "/v1/security/summary":
            return self._json(200, security_summary())

        if path == "/v1/journal":
            from urllib.parse import parse_qs, urlparse as _up

            _jq = parse_qs(_up(self.path).query)
            unit = (_jq.get("unit") or ["all"])[0]
            # since=<epoch>: Render's tape starts at engage (the User 2026-09-18)
            try:
                since = float((_jq.get("since") or ["0"])[0]) or None
            except ValueError:
                since = None
            if str(unit).startswith("proc:") and not crissa_home.is_house():
                # a pack's tiles are the launcher's pieces: their tape is the piece's own log file (2026-10-10, .101's
                # feed panel opened on "unit not allowlisted: 'proc:player'")
                return self._json(200, proc_tape(str(unit)[5:]))
            units = journal_units(unit)
            if units is None:
                return self._json(400, {"ok": False, "lines": [],
                                        "error": f"unit not allowlisted: {unit!r}"})
            return self._json(200, read_unit_logs(units, label=unit or "all", since=since,
                                                  **(_cpw.journal_query(_jq) if _cpw is not None else {})))

        if path == "/v1/script":
            from urllib.parse import parse_qs, urlparse as _up

            unit = (parse_qs(_up(self.path).query).get("unit") or [""])[0]
            payload = unit_script(unit)
            if _cpw is not None:
                payload = _cpw.mask_script(payload)     # keys masked in every file shown (house only)
            code = 400 if not payload.get("ok") else 200
            return self._json(code, payload)

        if path == "/v1/sound":
            return self._json(200, sound_payload())

        if path == "/gallery/manifest.json" or path == "/gallery/":
            return self._json(200, gallery_manifest())

        if path.startswith("/gallery/"):
            blob, mime = gallery_file(path[len("/gallery/"):])
            if blob is None:
                return self._json(404, {"ok": False, "error": "gallery piece not found"})
            return self._send(200, blob, mime)

        if path == "/v1/render-studio":
            gpu = gpu_mode_snapshot()
            try:
                studio_snapshot = _render_studio().snapshot

                d = studio_snapshot()
            except Exception as e:
                d = {"ok": False, "error": str(e)[:120]}
            d["gpu"] = gpu
            d["silo"] = gpu.get("mode") == "render" and bool(gpu.get("comfy"))
            return self._json(200, d)

        if path == "/v1/render-wip":
            try:
                from render_wip import wip_payload

                return self._json(200, wip_payload())
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)[:120]})

        if path == "/render-wip.img":
            try:
                from render_wip import preview_bytes
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)[:80]})
            blob, mime = preview_bytes()
            if not blob:
                return self._json(404, {"ok": False, "error": "no preview"})
            return self._send(200, blob, mime, cache="no-store")

        if path.startswith("/render-out/"):
            from urllib.parse import unquote as _unq

            try:
                from render_wip import version_bytes
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)[:80]})
            blob, mime = version_bytes(_unq(path[len("/render-out/"):]))
            if not blob:
                return self._json(404, {"ok": False, "error": "no such output"})
            if mime.startswith("video/"):
                return self._send_media(blob, mime, cache="private, max-age=120")
            return self._send(200, blob, mime, cache="private, max-age=120")

        if path.startswith("/render-still/"):
            name = path.rsplit("/", 1)[-1]
            try:
                still_bytes = _render_studio().still_bytes
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:80]})
            blob = still_bytes(name)
            if not blob:
                return self._json(404, {"ok": False, "error": "no still"})
            return self._send(200, blob, "image/png", cache="private, max-age=120")

        if path.startswith("/render-video/"):
            name = path.rsplit("/", 1)[-1]
            try:
                video_bytes = _render_studio().video_bytes
            except Exception as e:
                return self._json(500, {"ok": False, "error": str(e)[:80]})
            blob = video_bytes(name)
            if not blob:
                return self._json(404, {"ok": False, "error": "no video"})
            mime = "video/webm" if name.lower().endswith(".webm") else "video/mp4"
            return self._send(200, blob, mime, cache="private, max-age=120")

        if path == "/v1/mouth/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with _mouth_wake:
                        # flush on ARRIVAL, not on a tick — a 5Hz poll is the
                        # thing this endpoint exists to remove.
                        if _MOUTH["seq"] == seen:
                            _mouth_wake.wait(timeout=5.0)
                        fresh = _MOUTH["seq"] != seen
                        seen = _MOUTH["seq"]
                    if fresh:
                        # NO SAMPLES ON THE SSE. The glass reads air_at /
                        # air_until / frames and nothing else, but it still
                        # JSON.parses whatever arrives — measured ~197 KB per
                        # event once PCM rode along, every ~100 ms, into a
                        # kiosk EventSource buffer that never asked for it.
                        # The Conductor GETs /v1/mouth for samples; the glass
                        # gets the envelope. Also keeps the "live window, not a
                        # recording" claim true of the browser as well.
                        snap = mouth_snapshot()
                        snap.pop("pcm", None)
                        blob = json.dumps(snap, separators=(",", ":"))
                        self.wfile.write(f"event: mouth\ndata: {blob}\n\n".encode())
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            return

        if path == "/v1/mouth":
            return self._json(200, mouth_snapshot())

        if path == "/v1/sound/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last = None
            try:
                while True:
                    pay = sound_payload()
                    blob = json.dumps(pay, separators=(",", ":"))
                    if blob != last:
                        self.wfile.write(
                            f"event: sound\ndata: {blob}\n\n".encode()
                        )
                        last = blob
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(max(0.05, SOUND_TICK_SEC))
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            return

        if _cpw is not None and _cpw.handle_get(self, path):    # /v1/log-whole, /v1/watchers (house only)
            return

        if path == "/v1/janitor":
            # The Janitor tile (the User 2026-10-06): the janitor's last snapshot + every pause switch,
            # read live. Staleness is judged here - a janitor that stopped cannot say so itself.
            try:
                import house_watch
                return self._json(200, house_watch.panel_view())
            except Exception as e:  # noqa: BLE001 - never 500 the panel
                return self._json(200, {"ok": False, "level": "bad", "headline": f"janitor view: {e}"[:160]})

        if path == "/v1/services":
            snap = services_snapshot()
            return self._json(200, {
                "ok": True,
                "services": snap.get("services") or [],
                "announcer_muted": ANNOUNCE_MUTE.is_file(),
                "wanted_poster": WANTED_FLAG.is_file(),
                "voice_only": VOICE_ONLY_FLAG.is_file(),
                "os_power_full": OS_POWER_FULL,
                **{k: snap[k] for k in ("sections", "watch") if k in snap},
            })

        if path == "/v1/coach":
            # Self-healing SSE proxy: keep the client connection alive across
            # upstream disconnections. The old "Connection: close" caused EventSource
            # to reconnect on every upstream blip, creating zombie FIN-WAIT-2
            # connections that saturated the browser's 6-per-origin pool and froze
            # all other fetches (/v1/house, /frame).
            try:
                up = urllib.request.urlopen(COACH_URL, timeout=30)
            except Exception as e:
                return self._json(503, {"ok": False, "error": f"coach down: {e}"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                while True:
                    chunk = up.readline()
                    if not chunk:
                        break  # upstream EOF
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # Client (browser) navigated away or reloaded — just exit
                pass
            finally:
                try:
                    up.close()
                except Exception:
                    pass
            # Reconnect loop: if the client is still alive, try to restore upstream
            # without forcing the browser to reconnect (which would burn another
            # connection slot). Only retry while the client socket is still open.
            while True:
                # A dead client here is the normal case, not an error: the page
                # reloads and this socket goes with it. Every other write in
                # this loop was guarded and this one was not, so each reload
                # dumped a full traceback into the journal — thousands of them,
                # burying anything worth reading.
                try:
                    self.wfile.write(b"event: reconnecting\ndata: upstream lost - retrying\n\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                for _ in range(30):  # ~30s of retries before giving up
                    try:
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return  # client gone, exit cleanly
                    time.sleep(1)
                    try:
                        up = urllib.request.urlopen(COACH_URL, timeout=10)
                    except Exception:
                        continue
                    try:
                        self.wfile.write(b"event: reconnected\ndata: upstream restored\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        up.close()
                        return
                    # Resume streaming
                    try:
                        while True:
                            chunk = up.readline()
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        try:
                            up.close()
                        except Exception:
                            pass
            return

        if path == "/frame":
            if CAMERA_OFF:
                return self._json(503, {"ok": False, "camera": "off"})
            img = get_frame()
            if not img:
                return self._json(503, {"ok": False, "error": "no camera frame"})
            return self._send(200, img, "image/jpeg")

        if path == "/v1/speaker":
            return self._json(200, get_speaker())

        if path in ("/mark", "/mark.png"):
            # Decode-gap still. MONITOR is every clip's f0/last — never /frame12.
            f = POSE_DIR / "core-refs" / "MONITOR.png"
            if not f.is_file():
                return self._json(404, {"ok": False, "error": "missing MONITOR.png"})
            raw = f.read_bytes()
            return self._send_media(raw, "image/png")

        if path.startswith("/viseme/"):
            name = path[len("/viseme/"):].split(".")[0].lower()
            if name not in ("rest", "aa", "ee", "oh", "mouth-strip"):
                return self._json(404, {"ok": False, "error": "unknown viseme"})
            f = POSE_DIR / "visemes" / f"{name}.jpg"
            if not f.is_file():
                return self._json(404, {"ok": False, "error": f"missing {name}"})
            raw = f.read_bytes()
            return self._send_media(raw, "image/jpeg")

        if path.startswith("/pose/mouth/"):
            # Per-frame mouth track for one clip. The glass needs it to paint
            # visemes on a clip whose mouth TRAVELS (scoot: 61 px inside one
            # 1.2 s window); the inventory's static box only covers the clips
            # that hold still. The name comes straight from a row's
            # `mouth_sidecar`, so it is matched against that set and never
            # joined onto a path.
            from pose_inventory import load as _inv_load
            want = urllib.parse.unquote(path[len("/pose/mouth/"):])
            known = {
                str(row["mouth_sidecar"])
                for row in (_inv_load().get("clips") or {}).values()
                if isinstance(row, dict) and row.get("mouth_sidecar")
            }
            if want not in known:
                return self._json(404, {"ok": False, "error": "unknown mouth track",
                                        "tracks": sorted(known)})
            f = POSE_DIR / want
            if not f.is_file():
                return self._json(404, {"ok": False, "error": f"missing {want}"})
            raw = f.read_bytes()
            return self._send_media(raw, "application/json")

        if path == "/ft/talk-back":
            # Occupancy-free A/B for the conference window. Not an inventory
            # fidget — keep it off /pose/ so the bag cannot deal it.
            f = POSE_DIR / "_takes" / "20260831-ltx-speech" / "ltx-talk-back-24fps.mp4"
            if not f.is_file():
                return self._json(404, {"ok": False, "error": "missing talk-back take"})
            raw = f.read_bytes()
            return self._send_media(raw, "video/mp4")

        if path.startswith("/register/"):
            name = path[len("/register/"):].split("?")[0]
            ctype = REGISTER_INTRO.get(name)
            if not ctype:
                return self._json(404, {"ok": False, "error": "unknown register asset"})
            f = REGISTER_DIR / name
            if not f.is_file():
                return self._json(404, {"ok": False, "error": f"missing {name}"})
            raw = f.read_bytes()
            return self._send_media(raw, ctype)

        if path.startswith("/init-host/"):
            name = path[len("/init-host/"):].split("?")[0]
            if not name or name.startswith("/") or ".." in Path(name).parts:
                return self._json(404, {"ok": False, "error": "unknown init-host asset"})
            f = (INIT_HOST_DIR / name).resolve()
            if not str(f).startswith(str(INIT_HOST_DIR.resolve())) or not f.is_file():
                return self._json(404, {"ok": False, "error": f"missing {name}"})
            ctype = INIT_HOST_TYPES.get(f.suffix.lower())
            if not ctype:
                return self._json(404, {"ok": False, "error": "unknown init-host asset"})
            raw = f.read_bytes()
            return self._send_media(raw, ctype)

        if path.startswith("/pose/"):
            key = path[len("/pose/"):]
            clips = pose_clips()
            name = clips.get(key)
            if not name:
                return self._json(404, {"ok": False, "error": "unknown pose",
                                        "poses": sorted(clips)})
            f = POSE_DIR / name
            if not f.is_file():
                return self._json(404, {"ok": False, "error": f"missing {name}"})
            raw = f.read_bytes()
            return self._send_media(raw, "video/mp4")

        if path == "/frame12":
            # Her chair, empty. The age header is for whoever is debugging
            # this, not for the page: unlike the patrol's own references this
            # file is captured by hand and never refreshed, so its age only
            # says when we last re-shot her seat. The page does not read it.
            if not REF12_PATH.is_file():
                return self._json(503, {"ok": False, "error": "no station-12 ref"})
            try:
                raw = REF12_PATH.read_bytes()
                age = max(0.0, time.time() - REF12_PATH.stat().st_mtime)
            except OSError as e:
                return self._json(503, {"ok": False, "error": str(e)})
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Ref-Age-Sec", f"{age:.0f}")
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/v1/glass-prefs":
            return self._json(200, _glass.record())

        if path == "/v1/screen":
            with _lock:
                _clear_expired()
                save_state()
                return self._json(200, public_screen())

        if path.startswith("/mug/"):
            # The one picture the doc sheet may show: face.jpg from one
            # incident. Deliberately narrow — a TAG in, a fixed filename out.
            # No path is ever accepted from a caller, so there is nothing to
            # traverse with. Anything that is not a plain tag is a 404.
            tag = path[len("/mug/"):].split("/")[0]
            if not tag or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", tag):
                return self._json(404, {"ok": False, "error": "bad tag"})
            base = INCIDENTS_DIR.resolve()
            hits = sorted(d for d in base.glob(tag + "*") if d.is_dir())
            for d in hits:
                f = (d / "face.jpg").resolve()
                if f.is_file() and str(f).startswith(str(base) + "/"):
                    raw = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
            return self._json(404, {"ok": False, "error": "no mug"})

        if path == "/doc":
            # The document surface, served as its own page. doc-window.sh opens
            # this in a sheet-sized window ON TOP of the wall — it is never
            # painted into the wall itself, so the dial and the room stay whole.
            if not DOC_PAGE.is_file():
                return self._json(500, {"ok": False, "error": f"missing {DOC_PAGE}"})
            return self._send(200, her_named(DOC_PAGE.read_bytes()), "text/html; charset=utf-8")

        if path == "/v1/doc":
            with _lock:
                _clear_expired()
                return self._json(200, {
                    "ok": True,
                    "doc": _screen.get("doc"),
                    "doc_title": _screen.get("doc_title"),
                    "doc_font": _screen.get("doc_font"),
                    "doc_mug": _screen.get("doc_mug"),
                    "doc_gag": WANTED_FLAG.is_file(),
                    "rev": _screen.get("rev"),
                })

        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not feed_guard(self):                  # round 46: this machine's pages only (FEED_GUARD_DEFAULT)
            return
        # round 46: a negative or unreadable Content-Length is refused before any route reads it - rfile.read(-1)
        # reads until the peer closes. Each route keeps its own size cap; this is the sign. Kill: CRISSA_BODY_LENGTH=0.
        if (os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() not in ("0", "off", "no", "false")
                and crissa_guard.body_length(self.headers) is None):
            return self._json(400, {"ok": False, "error": "bad Content-Length"})
        path = self.path.split("?")[0]

        if path == "/v1/pose/cue":
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4096:
                    return self._json(413, {"ok": False, "error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "want an object"})
            return self._json(200, cue_set(str(body.get("key") or "")))

        if path == "/v1/pose/cue/ack":
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4096:
                    return self._json(413, {"ok": False, "error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            if not isinstance(body, dict):
                body = {}
            return self._json(200, {"ok": cue_ack(str(body.get("id") or ""))})

        if path == "/v1/mouth":
            # house-speak's envelope sidecar. Loopback only, like /v1/action.
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 262144:
                    return self._json(413, {"ok": False, "error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "want an object"})
            return self._json(200, mouth_ingest(body))

        if path == "/v1/init":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            act = str(body.get("action") or "snapshot")
            try:
                out = init_apply(act, body if isinstance(body, dict) else {})
            except Exception as e:
                return self._json(200, {
                    "ok": False,
                    "error": f"{type(e).__name__}: {str(e)[:160]}",
                })
            return self._json(200, out)

        if path == "/v1/render-studio":
            # Render silo. Standard/Beast get a polite refuse and never generate.
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            gpu = gpu_mode_snapshot()
            if gpu.get("mode") != "render" or not gpu.get("comfy"):
                return self._json(200, {
                    "ok": False,
                    "error": "silo: not Render",
                    "say": "Hit Render on this panel first.",
                    "gpu": gpu,
                })
            text = str(body.get("text") or body.get("say") or "").strip()
            hang = bool(body.get("hang"))
            engine = str(body.get("engine") or "").strip()
            source = str(body.get("source") or "typed").strip().lower() or "typed"
            try:
                _rs = _render_studio()
                hang_still, lila_turn, set_engine = _rs.hang_still, _rs.lila_turn, _rs.set_engine

                if hang:
                    out = hang_still(text)
                elif engine and not text:
                    out = set_engine(engine)
                else:
                    out = lila_turn(text, engine=engine, source=source)
            except Exception as e:
                import traceback

                traceback.print_exc()
                out = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {str(e)[:160]}",
                    "say": "I dropped the charcoal.",
                }
            if isinstance(out, dict):
                out["gpu"] = gpu
            return self._json(200, out)

        if path == "/v1/render-studio-mic":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            gpu = gpu_mode_snapshot()
            on = bool(body.get("on"))
            if on and (gpu.get("mode") != "render" or not gpu.get("comfy")):
                return self._json(200, {
                    "ok": False, "on": False, "error": "silo: not Render", "gpu": gpu,
                })
            try:
                listen_set = _render_studio().listen_set

                out = listen_set(on)
            except Exception as e:
                out = {"ok": False, "on": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
            if isinstance(out, dict):
                out["gpu"] = gpu
            return self._json(200, out)

        if path == "/v1/render-studio-listen":
            gpu = gpu_mode_snapshot()
            if gpu.get("mode") != "render" or not gpu.get("comfy"):
                return self._json(200, {
                    "ok": False, "error": "silo: not Render", "text": "", "gpu": gpu,
                })
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                body = {}
            try:
                one_listen = _render_studio().one_listen

                out = one_listen(body.get("seconds") or 4)
            except Exception as e:
                out = {"ok": False, "text": "", "error": f"{type(e).__name__}: {str(e)[:120]}"}
            if isinstance(out, dict):
                out["gpu"] = gpu
            return self._json(200, out)

        if path == "/panel/brain":
            # POST {key}: pin a brain for the NEXT ops start; nothing restarts here - the ops row's own Restart key
            # (or the window, which the pin owes) applies it. House-only; 409 on an unknown key or the kill switch.
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            code, out = panel_brain("POST", body if isinstance(body, dict) else {})
            return self._json(code, out)

        if path == "/v1/action":
            # Fixed allowlist — the page may not name arbitrary upstreams.
            # Every target is one panel_ui.py already proves in production; this
            # is a same-origin bridge, not a new control plane.
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            act = str(body.get("action") or "")
            refused = feed_face_refused(act)         # while Security is On: his face first (above)
            if refused is not None:
                return self._json(200, refused)

            # --- local actions (no upstream) ---
            if act == "health_check":
                return self._json(200, start_battery_job(1, disruptive=False))
            if act == "speed_check":
                return self._json(200, start_speed_job())
            if act == "health_battery":
                try:
                    tier = int(body.get("tier") or 2)
                except (TypeError, ValueError):
                    tier = 2
                if tier >= 3:
                    confirm = str(body.get("confirm") or "")
                    if confirm != T3_CONFIRM:
                        return self._json(200, {
                            "ok": False, "error": "confirm required",
                            "need": T3_CONFIRM,
                        })
                return self._json(200, start_battery_job(
                    tier, disruptive=(tier >= 3)
                ))

            if act in ("watch_pause", "watch_resume"):
                # Pause / Resume a listed unit's own off switch (config/house-watch.json off_file).
                # Writes or removes that one file; never stops or restarts a unit.
                try:
                    import house_watch
                    return self._json(200, house_watch.set_pause(str(body.get("unit") or ""),
                                                                 act == "watch_pause"))
                except Exception as e:  # noqa: BLE001
                    return self._json(200, {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]})

            if act in ERROR_CHECK_ACTIONS:
                # One operator walk: tell Oracle, then who_is_here
                # (wide + FaceFinder zoom). 90s.
                return self._json(200, run_operator_error_check())

            if act in ("house_off", "house_on"):
                fwd = pack_os_power_action(act)
                if fwd is not None:
                    _svc_cache["ts"] = 0.0
                    return self._json(200, fwd)
                return self._json(200, {"ok": False, "action": act, "error": "All OS Off has no server half here"})

            if act in ("mode_render", "mode_normal", "mode_beast", "mode_off"):
                routed = os_power_route(act)
                if routed is not None:
                    _svc_cache["ts"] = 0.0
                    return self._json(200, routed)
                if not HOUSE_MODE_BIN.is_file():
                    return self._json(500, {"ok": False, "error": "house-mode missing"})
                gpu = gpu_mode_snapshot()
                comfy = bool(gpu.get("comfy"))
                ops_up = _probe_http(_engine_url("ops", "/v1/models"), timeout=1.5) == "ok"
                crew_up = _probe_http(_engine_url("crew", "/v1/models"), timeout=1.5) == "ok"
                cosy_up = _probe_http(_voice_health_url(), timeout=1.5) == "ok"
                pending = str(gpu.get("pending") or "")
                already = occupancy_already(
                    act,
                    str(gpu.get("mode") or ""),
                    pending=pending,
                    comfy=comfy,
                    ops_ok=ops_up,
                    crew_ok=crew_up,
                    cosy_ok=cosy_up,
                )
                if pending and not already:
                    dest = GPU_LABELS.get(pending, pending)
                    return self._json(200, {
                        "ok": True, "action": act, "started": False, "pending": True,
                        "gpu": gpu,
                        "note": f"Switching to {dest} — already in flight",
                    })
                if already:
                    notes = {
                        "render": "Already in Render",
                        "normal": "Already Standard",
                        "beast": "Already Beast",
                        "off": "Already All OS Off",
                    }
                    return self._json(200, {
                        "ok": True, "action": act, "already": True,
                        "gpu": gpu, "note": notes[already],
                    })
                if prod_locked():
                    return self._json(200, {
                        "ok": False, "action": act, "started": False,
                        "gpu": gpu,
                        "note": "Prod lock — occupancy frozen at Normal",
                    })
                cmd = [str(HOUSE_MODE_BIN)]
                if act == "mode_render":
                    cmd += ["render", "turbo"]
                    note = "Switching to Render — crew parks, Comfy up. ~15s from Standard; minutes from Beast."
                elif act == "mode_beast":
                    cmd += ["beast"]
                    note = "Switching to Beast — one 27B on the whole card, 256k context, 5 seats. Loads in ~3.5 min. %s moves to :8005 and still talks and hears." % crissa_home.her_name()
                elif act == "mode_off":
                    cmd += ["off"]
                    note = "All OS Off — parking GPU (ops, crew, Cosy, Comfy). Ear stays. Not a host poweroff."
                else:
                    cmd += ["normal"]
                    note = "Switching to Standard — ~2–3 min from Render; ~3 min from Beast."
                logp = Path("/tmp/house-mode-panel.log")
                try:
                    subprocess.Popen(
                        cmd,
                        stdout=logp.open("ab"),
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act, "error": str(e)[:160]})
                _svc_cache["ts"] = 0.0
                return self._json(200, {
                    "ok": True,
                    "action": act,
                    "started": True,
                    "gpu": gpu,
                    "note": note,
                })

            if act == "mode_turbo_krew":
                gpu = gpu_mode_snapshot()
                turbo_on = bool((gpu.get("turbo_krew") or {}).get("on"))
                if str(gpu.get("mode") or "") != "normal" and not turbo_on:
                    return self._json(200, {
                        "ok": False,
                        "action": act,
                        "gpu": gpu,
                        "note": "TurboKrew is Standard only",
                    })
                try:
                    mod = _turbo_krew_mod()
                    result = (
                        mod.turn_off(reason="panel")
                        if turbo_on
                        else mod.turn_on(reason="panel")
                    )
                except Exception as e:
                    return self._json(200, {
                        "ok": False,
                        "action": act,
                        "error": str(e)[:160],
                        "gpu": gpu,
                    })
                gpu = gpu_mode_snapshot()
                ok = bool(result.get("ok"))
                return self._json(200, {
                    "ok": ok,
                    "action": act,
                    "gpu": gpu,
                    "turbo_krew": result,
                    "note": result.get("note") or result.get("error") or "",
                })

            if act in ("wanted_on", "wanted_off"):
                try:
                    if act == "wanted_on":
                        WANTED_FLAG.parent.mkdir(parents=True, exist_ok=True)
                        WANTED_FLAG.write_text(
                            "wanted-poster flash enabled from the Control Panel "
                            + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    else:
                        WANTED_FLAG.unlink(missing_ok=True)
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": str(e)[:160]})
                return self._json(200, {"ok": True, "action": act,
                                        "wanted": WANTED_FLAG.is_file()})

            if act in ("announcer_on", "announcer_off"):
                try:
                    if act == "announcer_off":
                        ANNOUNCE_MUTE.parent.mkdir(parents=True, exist_ok=True)
                        ANNOUNCE_MUTE.write_text(
                            "muted from the room screen "
                            + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    else:
                        ANNOUNCE_MUTE.unlink(missing_ok=True)
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": str(e)[:160]})
                return self._json(200, {"ok": True, "action": act,
                                        "announcer_muted": ANNOUNCE_MUTE.is_file()})

            if _cpw is not None and act in _cpw.ACTIONS:      # run_now (house only)
                code, out = _cpw.action(act, body)
                return self._json(code, out)

            if act == "restart":
                unit = str(body.get("unit") or "")
                ports = {n: port for n, _lbl, port in SERVICES}
                if _cpw is not None:
                    ports.update(_cpw.restart_ports())       # each avatar's own key in a group tile (house only)
                if unit not in ports:
                    return self._json(400, {"ok": False,
                                            "error": f"unit not allowlisted: {unit!r}"})
                mode = read_occupancy_mode()
                why = restart_blocked_reason(unit, mode)
                if why:
                    return self._json(200, {
                        "ok": False, "action": "restart", "unit": unit,
                        "error": why, "parked": True, "occupancy": mode,
                    })
                st = unit_status(unit)
                scope = st.get("scope") or "user"
                if scope == "system":
                    # A system unit restarts through the polkit rule, and only
                    # while the house says User (the User 2026-09-17: "give panel
                    # permission if User FF verifies or in User mode").
                    hs, hb, _ = fetch(HOUSE_URL, timeout=3.0)
                    try:
                        house = json.loads(hb) if hs == 200 and hb else {}
                    except Exception:
                        house = {}
                    allowed, who = panel_units.user_signal(house)
                    if not allowed:
                        return self._json(200, {"ok": False, "action": "restart",
                                                "unit": unit, "need_user": True,
                                                "error": f"{unit} is a system unit — {who}"})
                override = body.get("override") is True and body.get("confirmed") is True
                if override and window_restart_target(unit):
                    if not _override_on():
                        return self._json(200, {"ok": False, "action": "restart", "unit": unit,
                                                "error": "restart override is off (HOUSE_PANEL_RESTART_OVERRIDE=0)"})
                    log_restart_override(unit, window_restart_target(unit),
                                         client=str((self.client_address or ("",))[0]),
                                         agent=str(self.headers.get("User-Agent") or ""))
                    owed = None
                else:
                    owed = gateway_restart_to_window(unit)
                if owed is not None:
                    _svc_cache["ts"] = 0.0
                    return self._json(200, dict(owed, action="restart", unit=unit,
                                                scope=scope, occupancy=mode))
                res = panel_units.start_restart(unit, scope, ports.get(unit))
                _svc_cache["ts"] = 0.0
                return self._json(200, dict(res, action="restart", unit=unit,
                                            scope=scope, occupancy=mode))

            if act == "register_glass":
                p = body.get("payload") or {}
                phase = str(p.get("phase") or "idle")
                text = str(p.get("text") or "")
                pip = str(p.get("pip") or "")
                line = p.get("line")
                g = set_register_glass(phase, text, pip=pip, line=(str(line) if line is not None else None),
                                       seconds=p.get("seconds"), beep=bool(p.get("beep")),
                                       mode=str(p.get("mode") or ""))
                return self._json(200, dict(g, ok=True, action=act))

            if act == "kiosk_init_user":
                # The key says "First-boot routine on the wall" and that is
                # what it runs — on a new office and on this house alike. A
                # house that already walked master init RE-RUNS the wizard
                # (init_wizard.start force; Escape aborts); until 2026-09-09
                # this branch bounced an initialized house to the Register
                # overlay instead, so the redone routine never started here
                # (the User: "New User … Not working"). Register an ID stays its
                # own Control Panel key.
                rerun = bool(init_master_done())
                try:
                    wiz = init_apply("start", {"force": True})
                except Exception as e:
                    return self._json(200, {
                        "ok": False, "action": act,
                        "error": f"{type(e).__name__}: {str(e)[:140]}",
                    })
                try:
                    if wallpaper_active():
                        r = {"ok": True, "glass": "wallpaper", "desk": wall_routine_desk(True)}
                    else:
                        r = kiosk_pin_wall(init="user")
                except Exception as e:
                    r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}"}
                return self._json(200, {
                    "ok": True, "action": act, "init": wiz, "kiosk": r,
                    "href": "/?init=user", "rerun": rerun,
                })

            if act == "kiosk_register":
                form = bool((body.get("payload") or {}).get("form"))
                try:
                    r = kiosk_pin_register() if form else kiosk_pin_wall(form=False)
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act == "kiosk_return_cp":
                try:
                    r = kiosk_return_cp()
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act == "kiosk_home":
                try:
                    r = kiosk_home()
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act == "raise_panel":
                try:
                    r = raise_panel_window()
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act == "kiosk_panel":
                set_register_glass("idle", "")
                try:
                    r = kiosk_pin_wall(panel=True)
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act == "kiosk_wall":
                try:
                    r = kiosk_pin_wall(form=False)
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act in ("crissa_hole_on", "crissa_hole_off"):
                on = act.endswith("_on")
                with _lock:
                    _screen["crissa_hole"] = on
                    save_state()
                return self._json(200, {
                    "ok": True, "action": act, "crissa_hole": on,
                    "note": (("%s follows the window" if on
                              else "%s keeps her own framing") % crissa_home.her_name()),
                })

            if act == "fov_zoom":
                try:
                    z = float(body.get("value", 1.0))
                except (TypeError, ValueError):
                    z = 1.0
                z = round(max(0.25, min(4.0, z)), 2)
                # channel defaults to crissa: the AR chip is "Resize Crissa", and
                # the camera's 1:1 is not something the wall dial should move.
                field = ("fov_zoom" if str(body.get("channel") or "crissa") == "camera"
                         else "fov_zoom_crissa")
                with _lock:
                    _screen[field] = z
                    save_state()
                return self._json(200, {
                    "ok": True, "action": act, "field": field, "fov_zoom": z,
                    "note": ("1.0 is the camera 1:1; below 1 widens the hole "
                             "so her own clip is not magnified into a sliver"),
                })

            if act in ("fullscreen_on", "fullscreen_off"):
                # Hybrid poll used to re-assert FS (house.svc.voice_only stale)
                # after the window had already gone small.
                if act == "fullscreen_on" and not VOICE_ONLY_FLAG.is_file():
                    return self._json(200, {
                        "ok": True, "action": act, "skipped": "hybrid",
                        "note": "Hybrid — fullscreen is opt-in from here",
                    })
                try:
                    r = kiosk_window("fullscreen" if act == "fullscreen_on" else "normal")
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})
                return self._json(200, dict(r, action=act))

            if act == "glass_brave":
                # ZOS DESKTOP OFF — the other half of the wallpaper's own row
                # (the User 2026-09-16: "Only Wallpaper ON or OFF"). glass_mode
                # stops the wallpaper and starts crissa-display, so THIS window
                # becomes the glass; the page arms the press first, because
                # there are no menus on the glass once it gets there.
                try:
                    import glass_mode  # lib/, on sys.path above
                    r = glass_mode.apply("brave")
                    _svc_cache["ts"] = 0.0
                    _svc_cache["data"] = None
                    return self._json(200, dict(r, ok=True, action=act))
                except Exception as e:
                    log(f"glass_brave: {e!r}")
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})

            if act == "glass_wallpaper":
                # ZOS Wall ON from Brave's Advanced (the User 2026-09-08 pm: "not
                # both at once unless User does it"): the wallpaper is
                # enabled+started and its claim puts this Brave away once it
                # has painted (lib/glass_mode.hand_to_wallpaper). The reply
                # goes out before that, so the page can toast.
                try:
                    import glass_mode  # lib/, on sys.path above
                    r = glass_mode.hand_to_wallpaper()
                    _svc_cache["ts"] = 0.0
                    _svc_cache["data"] = None
                    return self._json(200, dict(r, ok=True, action=act))
                except Exception as e:
                    log(f"glass_wallpaper: {e!r}")
                    return self._json(200, {"ok": False, "action": act,
                                            "error": f"{type(e).__name__}: {str(e)[:140]}"})

            if act in ("mode_voiceonly", "mode_hybrid"):
                mode = "voice-only" if act == "mode_voiceonly" else "hybrid"
                # ONE control, and it means something (User 2026-08-11).
                #   Voice-Only = fullscreen + hands off. The page drops the rail
                #     (body.vo); mouse movement reveals only the top-right
                #     "⇱ Hybrid" escape. Crissa drives the screen.
                #   Hybrid     = windowed + yours. Rail on mouse-wake, Voice
                #     On/Off, clickable. Never minimize. Never snap back to FS.
                # Drop the VO flag BEFORE window bounds so the house poll
                # cannot re-assert fullscreen (the small→FS snap).
                # NOTHING WRITES THE FLAG, NOTHING FULLSCREENS BRAVE (the User
                # 2026-09-16 "kept open no maximized. Never!"; 09-17 "it insists
                # on fullscreen"): the Wall page's old Voice-Only toggle still
                # posts mode_voiceonly, and this handler wrote the flag and
                # fullscreened the window — after which the house poll kept
                # re-asserting it. Both asks now clear the flag and leave a
                # normal window. HOUSE_BRAVE_VO_FULLSCREEN=1 = the old toggle.
                _vo_fs = os.environ.get("HOUSE_BRAVE_VO_FULLSCREEN", "0") == "1"
                try:
                    if mode == "voice-only" and _vo_fs:
                        VOICE_ONLY_FLAG.parent.mkdir(parents=True, exist_ok=True)
                        VOICE_ONLY_FLAG.write_text("1\n")
                    else:
                        VOICE_ONLY_FLAG.unlink(missing_ok=True)
                except Exception as e:
                    log(f"voice-only flag: {e!r}")
                try:
                    kiosk_window("fullscreen" if (act == "mode_voiceonly" and _vo_fs) else "normal")
                except Exception as e:
                    log(f"window state change failed ({e!r}) — mode flag still set")
                try:
                    _svc_cache["ts"] = 0.0
                    _svc_cache["data"] = None
                    return self._json(200, {
                        "ok": True,
                        "action": act,
                        "mode": mode,
                        "voice_only": VOICE_ONLY_FLAG.is_file(),
                        "note": "display mode — does not change Voice master",
                    })
                except Exception as e:
                    return self._json(200, {"ok": False, "action": act,
                                            "error": str(e)[:160]})

            target = ACTIONS.get(act)
            if not target:
                return self._json(400, {"ok": False, "error": f"unknown action {act!r}"})
            if act == "enroll_cancel":
                with _lock:
                    REGISTER_GEN["n"] = int(REGISTER_GEN.get("n") or 0) + 1
                set_register_glass("idle", "")
            start_gen = None
            desk_cleared = False
            if act == "enroll_start":
                with _lock:
                    start_gen = int(REGISTER_GEN.get("n") or 0)
                if wallpaper_active():
                    # the entry itself clears the desk (the User 2026-09-09: "that
                    # entry could queue the clear screen") — before the coach
                    # answers, so the wall is in view as she starts; a start
                    # that fails brings the windows back below
                    desk_cleared = True
                    threading.Thread(target=lambda: wall_routine_desk(True), name="wall-desk", daemon=True).start()
            url, payload = target[0], (body.get("payload") or target[1])
            if act == "enroll_start":
                # REGISTER TAKES ONLY ITS OWN KEYS (the User 2026-10-04, Init v2): lib/enroll_payload.py
                import enroll_payload
                payload, why = enroll_payload.clean(body.get("payload"), target[1])
                if payload is None:
                    if desk_cleared:
                        threading.Thread(target=lambda: wall_routine_desk(False), name="wall-desk", daemon=True).start()
                    return self._json(403, {"ok": False, "action": act, "error": why})
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                timeout = 90.0 if act in ("who_is_here", "visit_click", "auth_join") else 12.0
                if act == "enroll_cancel":
                    timeout = 4.0
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read(8000).decode("utf-8", "replace")
                    parsed: dict | None
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = None
                    out = {"ok": True, "action": act, "status": r.status,
                           "body": raw[:400]}
                    if isinstance(parsed, dict):
                        out["result"] = parsed
                    if act == "enroll_start":
                        with _lock:
                            late = int(REGISTER_GEN.get("n") or 0) != start_gen
                        started = isinstance(parsed, dict) and parsed.get("ok") is True
                        if late or not started:
                            if late and started:
                                try:
                                    c = urllib.request.Request(
                                        ENROLL_BASE + "/cancel",
                                        data=b"{}",
                                        headers={"Content-Type": "application/json"},
                                        method="POST",
                                    )
                                    urllib.request.urlopen(c, timeout=4).read()
                                except Exception as e:
                                    log(f"late enroll_cancel: {e!r}")
                            set_register_glass("idle", "")
                            if desk_cleared:
                                threading.Thread(target=lambda: wall_routine_desk(False), name="wall-desk", daemon=True).start()
                            if late:
                                return self._json(200, {
                                    "ok": False, "cancelled": True, "action": act,
                                })
                            return self._json(200, out)
                        set_register_glass("watch", "Look here.")
                        # no Brave pin either way (2026-09-11): the wall is
                        # the register's renderer; a Brave glass shows the
                        # film through the page, the routine paints nothing
                        # there
                    if act == "voice_off":
                        with _lock:
                            if _screen.get("mood") in ("attending", "speaking"):
                                _screen["mood"] = "away"
                    elif act == "voice_on":
                        # Mic on lands PAUSED, never LISTENING (the User
                        # 2026-08-28). Turning the mic back on is not consent
                        # to be answered — the wake word is.
                        with _lock:
                            if _screen.get("mood") in ("away", "resting", None, ""):
                                _screen["mood"] = "paused"
                    elif act == "voice_pause":
                        # Optimistic: the lamp flips now, not on the 2s poll.
                        with _lock:
                            if _screen.get("mood") in ("attending", "speaking"):
                                _screen["mood"] = "paused"
                    return self._json(200, out)
            except urllib.error.HTTPError as e:
                # 403 from the Voice master is the local-only guard doing its job
                if desk_cleared:
                    threading.Thread(target=lambda: wall_routine_desk(False), name="wall-desk", daemon=True).start()
                return self._json(200, {"ok": False, "action": act, "status": e.code,
                                        "error": e.read(200).decode("utf-8", "replace")})
            except Exception as e:
                if desk_cleared:
                    threading.Thread(target=lambda: wall_routine_desk(False), name="wall-desk", daemon=True).start()
                return self._json(200, {"ok": False, "action": act,
                                        "error": f"{type(e).__name__}: {str(e)[:160]}"})

        if path == "/v1/security" and not crissa_home.is_house():
            # THE KIT'S SECURITY TOGGLE from this feed's pages (round 17 follow-ups): the Crissa menu's own action, which
            # runs the face-match sequence for a step down and Secure's own keys through presence (the player's POST /v1/menu)
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4096:
                    return self._json(413, {"ok": False, "error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("want an object")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            out = security_set(body)
            body.pop("password", None)
            return self._json(200, dict(out, note=out.get("note") or out.get("error") or ""))

        if path == "/v1/glass-prefs":
            # a partial dict merged over the record; the reply is the whole
            # record with its new rev, which the poster adopts
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4096:
                    return self._json(413, {"ok": False, "error": "too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._json(400, {"ok": False, "error": f"bad request: {e}"})
            if not isinstance(body, dict):
                return self._json(400, {"ok": False, "error": "want an object"})
            try:
                gname = crissa_home.glass_gate_name(body)    # the glass's own view is open (crissa_home)
            except Exception:  # noqa: BLE001
                gname = "glass_prefs"
            refused = feed_face_refused(gname) if gname else None   # while Security is On: his face first
            if refused is not None:
                return self._json(200, refused)
            return self._json(200, _glass.apply(body))

        if path != "/v1/screen":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n > 0 else b"{}"
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad request: {e}"})

        with _lock:
            # Always retire the timed surfaces before applying changes
            _clear_expired()

            # Explicit field clearing — callers can request specific fields be cleared
            # "notes" as a clear target clears the full notes group (notes, notes_title,
            # notes_ttl, prev_headline) so callers don't need to list all four.
            if "clear" in body:
                clear_fields = body.get("clear")
                if isinstance(clear_fields, list):
                    for field in clear_fields:
                        if field == "notes":
                            # Clear the full notes group
                            _screen["notes"] = None
                            _screen["notes_title"] = None
                            _screen["notes_ttl"] = None
                            _screen["prev_headline"] = None
                        elif field == "doc":
                            # same courtesy as notes: clear the whole group
                            _screen["doc"] = None
                            _screen["doc_title"] = None
                            _screen["doc_ttl"] = None
                            _doc_window("close")
                        elif field == "art":
                            _screen["art"] = None
                            _screen["art_label"] = None
                            _screen["art_ramp"] = None
                            _screen["art_hold"] = None
                            if _screen.get("figure") == "art":
                                _screen["figure"] = "room"
                        elif field in ("notes_title", "notes_ttl", "prev_headline"):
                            _screen[field] = None
                        elif field in _screen:
                            _screen[field] = None

            if "mood" in body:
                m = str(body.get("mood") or "").lower()
                want = m if m in MOODS else "resting"
                # Listening / Speaking require Voice ON. Sticky attending was
                # leaving the top-right chip on after Voice off (2026-08-18).
                if want in ("attending", "speaking"):
                    hs, raw, _ = fetch(HOUSE_URL, timeout=2.0)
                    try:
                        live = json.loads(raw) if hs == 200 and raw else {}
                    except Exception:
                        live = {}
                    if not _voice_on_from_house(live if isinstance(live, dict) else {}):
                        want = "away"
                _screen["mood"] = want
            if "focus" in body:
                f = str(body.get("focus") or "").lower()
                _screen["focus"] = f if f in FOCI else "none"
            if "headline" in body:
                h = body.get("headline")
                _screen["headline"] = str(h)[:120] if h else None
                # Track the headline so notes can be pinned to it
                _screen["prev_headline"] = _screen["headline"]
            if "note" in body:
                nt = body.get("note")
                _screen["note"] = str(nt)[:160] if nt else None
            if "chips" in body:
                c = body.get("chips")
                _screen["chips"] = (
                    [str(x)[:24] for x in c][:6] if isinstance(c, list) else []
                )
            # --- shape ---
            for key, allowed in (("figure", FIGURES), ("instrument", INSTRUMENTS),
                                 ("density", DENSITIES), ("accent", ACCENTS),
                                 ("face", FACES)):
                if key in body:
                    v = str(body.get(key) or "").lower()
                    # unknown -> keep what's on screen, never blank it
                    if v in allowed:
                        _screen[key] = v
            if "art" in body:
                a = body.get("art")
                if a:
                    lines = str(a).replace("\r", "").split("\n")[:ART_MAX_LINES]
                    _screen["art"] = "\n".join(lines)[:ART_MAX_CHARS]
                    _screen["art_seq"] = int(_screen.get("art_seq") or 0) + 1
                    # A new piece arrives uncaptioned unless THIS post captions
                    # it. Carrying the previous label forward put "the wedding"
                    # under a rose on 2026-08-13 — the caption from the piece
                    # before it. A caption belongs to one piece, not to the slot.
                    if "art_label" not in body:
                        _screen["art_label"] = None
                    # Hold is a CEILING, not a lock. The page drops it the moment
                    # a new line lands in the roll — if the User speaks, her art
                    # scrolls, no matter what.
                    hv = body.get("art_hold")
                    try:
                        secs = float(hv) if hv is not None else ART_HOLD_DEFAULT
                    except (TypeError, ValueError):
                        secs = ART_HOLD_DEFAULT
                    secs = max(0.0, min(secs, ART_HOLD_MAX))
                    _screen["art_hold"] = time.time() + secs if secs else None
                else:
                    _screen["art"] = None
                    # A cleared piece leaves nothing behind: the label and the
                    # hold went with it. (Before 08-13 the label stayed, so the
                    # screen carried art_label:"self portrait" with art:null.)
                    _screen["art_label"] = None
                    _screen["art_ramp"] = None
                    _screen["art_hold"] = None
                    if _screen.get("figure") == "art":
                        _screen["figure"] = "room"   # cleared her art: fall home
            if "art_label" in body:
                al = body.get("art_label")
                _screen["art_label"] = str(al)[:48] if al else None
            if "art_ramp" in body:
                r = str(body.get("art_ramp") or "").lower()
                if not r:
                    _screen["art_ramp"] = None
                elif r not in ART_RAMPS:
                    log(f"unknown art_ramp {r!r} — keeping {_screen.get('art_ramp')!r}")
                elif RAMPS_OK and r not in RAMPS_OK:
                    # The glyphs are not on this box; drawing them would print a
                    # wall of .notdef boxes. Fall to ascii, which renders anywhere.
                    log(f"art_ramp {r!r} has no font here — falling back to ascii")
                    _screen["art_ramp"] = "ascii"
                else:
                    _screen["art_ramp"] = r
            if "notes" in body:
                n = body.get("notes")
                if n:
                    lines = str(n).replace("\r", "").split("\n")[:NOTES_MAX_LINES]
                    _screen["notes"] = "\n".join(lines)[:NOTES_MAX_CHARS]
                    # Pin notes to current headline so headline transitions clear them
                    _screen["prev_headline"] = _screen.get("headline")
                    # Always a timer. None / junk → NOTES_TTL_DEFAULT.
                    # Seconds-from-now or an epoch both work.
                    _screen["notes_ttl"] = resolve_ttl(
                        body.get("notes_ttl"), NOTES_TTL_DEFAULT
                    )
                else:
                    _screen["notes"] = None
                    _screen["notes_title"] = None
                    _screen["notes_ttl"] = None
                    _screen["prev_headline"] = None
            if "notes_title" in body:
                nt = body.get("notes_title")
                _screen["notes_title"] = str(nt)[:60] if nt else None
            # --- the document surface ---
            # This never paints on the wall. doc-window.sh opens /doc in its own
            # sheet-sized window on top, and that window closes itself when the
            # doc clears — which it always eventually does, because a doc without
            # a TTL gets the default one.
            if "doc" in body:
                d = body.get("doc")
                if d:
                    lines = str(d).replace("\r", "").split("\n")[:NOTES_MAX_LINES]
                    _screen["doc"] = "\n".join(lines)[:NOTES_MAX_CHARS]
                    _screen["doc_ttl"] = resolve_ttl(
                        body.get("doc_ttl"), DOC_TTL_DEFAULT
                    )
                    _doc_window("open")
                else:
                    _screen["doc"] = None
                    _screen["doc_title"] = None
                    _screen["doc_ttl"] = None
                    _screen["doc_mug"] = None
                    _doc_window("close")
            if "doc_mug" in body:
                mg = str(body.get("doc_mug") or "")
                _screen["doc_mug"] = (
                    mg if re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", mg) else None
                )
            if "doc_title" in body:
                dt = body.get("doc_title")
                _screen["doc_title"] = str(dt)[:80] if dt else None
            if "doc_font" in body:
                df = str(body.get("doc_font") or "").lower()
                _screen["doc_font"] = df if df in ART_FONTS else None
            # asking for figure=art without ever sending art would blank the ground
            if _screen.get("figure") == "art" and not _screen.get("art"):
                _screen["figure"] = "room"
            _screen["rev"] = int(_screen.get("rev") or 0) + 1
            _screen["ts"] = time.time()
            out = public_screen()
            save_state()
            return self._json(200, {"ok": True, "screen": out})


def main() -> None:
    global RAMPS_OK
    load_state()
    try:
        log(f"glass prefs rev={_glass.load().get('rev')} from {_glass.path}")
    except Exception as e:  # noqa: BLE001
        log(f"glass prefs load failed: {type(e).__name__}: {e}")
    RAMPS_OK = probe_ramps()
    start_fov_sampler()
    # started here, never at import: a test that loads this file must not open an
    # audio client (the 09-19 pactl leak) nor take Comfy's one house-wip socket
    # from the live server during Render
    threading.Thread(target=_mouth_sink_subscribe, daemon=True, name="mouth-sink-sub").start()
    if os.environ.get("HOUSE_RENDER_WIP", "1") != "0":
        threading.Thread(target=_render_wip_thread, daemon=True, name="render-wip").start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"listening on http://{HOST}:{PORT}/  house={HOUSE_URL}  frame={FRAME_URL}")
    log(f"page={PAGE}")
    log(f"art ramps available: {', '.join(sorted(RAMPS_OK)) or 'none'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("bye")


if __name__ == "__main__":
    main()
