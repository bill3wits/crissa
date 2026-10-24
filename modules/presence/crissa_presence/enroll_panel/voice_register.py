"""Voice register — two desk takes in about fifteen seconds, hosted on the wall.

THE WALL HOST (the User 2026-09-11: "bang on New ID … a 15 sec thing", "the
served version never worked once", "save Crissa for the final intro handoff").
Lila's rendered clips carry the lines (audio baked in, played beside the
conductor's frames); the front picture goes to FaceID at t=0 while the
line plays; the profile picture at the end of the turn line, before its
beep; nothing waits on an embedding — the takes are banked and the sweep
stored AFTER "Thanks", with the camera going home and the ear reopening.
The name is never spoken: it is on the glass in the sentence. HOST=loop is
the 2026-09-09 routine (live Cosy lines, ~90 s) kept for rollback.

Wall (loop host): Look here + Crissa PIP while she speaks. Sentence appears
only after the intro, a beat before the GO beep. Every take opens and closes
on the same GO beep (the User 2026-10-11; the sweep's tick + chirp only under
HOUSE_ENROLL_SAME_GO=0), BEEP_GAP_S after her last word. Ding = take in.
Session-end tones wrap it.

Camera is Inspector work: lease → desk (hour 6) → go_home WHILE leased →
/v1/lease/release so Oracle gets the door FOV.
"""
from __future__ import annotations

from .. import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)
from .. import paths as _ppaths  # is_house (2026-10-10)

import base64
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .paths import rails_path  # where the Wall repo lives: config, never a guess when HOUSE_ID_RAILS_CONFIG=1
from typing import Any

from . import cues
from .coach import coach_emit
from .cues import DESK_LINE_FRONT, DESK_LINE_TURN
from .face_snap import (
    embed_jpegs,
    enroll_hold_snap,
    go_home,
    hold_fov,
    station_pt,
    sweep_zoom,
)
from .face_yaw import WANT_BINS, best_per_bin
from .take_plans import VOICE_DESK_SECONDS, VOICE_TURN_SECONDS, read_window_s, voiced_bar
from .person import assert_not_frozen, display_name, ensure_person
from .roles import is_primary_user
from .site import Site
from .voice_session import (
    _tool_hold_acquire,
    _tool_hold_release,
    embed_wav,
    live_encoder_meta,
    next_slot,
    peak_pcm,
    take_fault,
    TAKE_FAULT_WORDS,
    write_live_voice_print,
    record_fixed,
    record_go,
    go_with_beep_on,
    release_ear_for_enroll,
    wait_ear_released,
    reopen_ear_after_enroll,
    unmute_mic,
    pulse_source_named,
    voice_print_path,
    voiced_fraction,
)

CAMERA = os.environ.get("RAIL_CAMERA_URL", _ports.camera()).rstrip("/")
PRESENCE = (os.environ.get("RAIL_PRESENCE_URL") or os.environ.get("RAIL_PRESENCE_BASE")  # this install's own (2026-10-10)
            or _ports.url("presence")).rstrip("/")
WALL = os.environ.get("VO_HOME_URL", _ports.url("feed")).rstrip("/")
PLAYER_INIT = os.environ.get("CRISSA_PLAYER_INIT_URL") or _ports.url("player", "/v1/init")   # ADD A VISITOR: the player's /v1/init

# Desk: vowel-complete lines, ~8 s voiced. Turn line is also the sweep.
CUE = DESK_LINE_FRONT
TURN_CUE = DESK_LINE_TURN
LOOK_HERE = "Look here."
TEXT_LEAD_SEC = 1.2
# Cosy chapter script on the curtain studio clip, measured 16.12 s (2026-09-09).
INTRO_SEC = 16.4
# THE HOST (the User 2026-09-09: "finish wiring it with the standard formal face
# up talking loop for now"). loop = Crissa's formal face-on talking loop on the
# wall (conductor `talk-angled`) with the lines spoken live through
# house-speak so the conductor paints her mouth; curtain = the studio render
# with the script baked in (display/register/crissa-intro.mp4, Brave's overlay).
HOST = (os.environ.get("HOUSE_REGISTER_HOST") or "wall").strip().lower()
# THE HOST CLIPS (wall host). Lila's register takes, rendered with the line
# baked in (the User 2026-09-11: "use the ones we already rendered … save Crissa
# for the final intro handoff"). Inventory keys; a missing clip degrades to
# tones + the glass (the words are on the wall either way).
HOST_CLIPS = {
    "okay": "lila-reg-okay",        # "Okay. Look at the screen and read the sentence. Start at the beep."
    "listen": "lila-reg-listen",    # silent, loops under both takes
    "turn": "lila-reg-turn",        # "Now turn all the way to the side, away from the camera."
    "thanks": "lila-reg-thanks",    # "That's it. Thanks!"
    "handoff": "crissa-reg-handoff",  # Crissa, once, after the routine — the first thing a new User meets
}
# a beat to leave when a host clip is missing, so the beep does not land on
# the same instant as the words on the glass
# with no clip yet the words are only on the glass: leave time to READ
# them before the beep (16:14 run: the front take was 20% voiced — the beep
# came 1.2 s after the sentence appeared), and to turn before the tick
HOST_FALLBACK_SEC = {"okay": 6.0, "turn": 5.5, "thanks": 0.6, "handoff": 0.0,
                     "turn_read": 5.5, "face_count": 5.0}      # the four takes' two new beats (glass only, 2026-10-11)
# HER LAST WORD, THEN THE BEEP (the User 2026-10-11: "less time from her saying her last word and the beep would
# prevent false starts"). Measured: lila-reg-okay carries 3.19 s of silence after "...until the next one." and
# lila-reg-turn 3.15 s after "...Turn away to start." (their intake speech spans end at 8.84 s and 6.88 s); the beep
# came 3.2 s after her last word. The lines that end on a beep play cut 30 ms after their last sound (cues.trim_wav,
# cached as enroll-host-<key>.trim.wav), and the beep keeps cues.BEEP_GAP_S (0.30 s) from it. Her picture is not
# cut: the clip plays out silent under the take, and the listen loop (now cued at her last word) takes the wall at
# its end, the conductor's next seam. Kill: HOUSE_ENROLL_BEEP_GAP=0 (whole clips, the old 3.2 s).
TRIM_KEYS = ("okay", "turn")
# the work between the pre-beep wait and the tone's start (the glass, the take's setup, the mic's pactl, the tone
# built): measured 2026-10-11 on the bench, 30-50 ms; go_beep tops up whatever is left
PRE_BEEP_S = 0.08
_HOST_TAIL: dict[str, tuple[float, float]] = {}    # key -> (trimmed file's mtime, seconds kept after her last sound)
# THE BEATS AROUND A BEEP (the User 2026-09-12: "we all want to start saying it
# when we 1 understand the instruction 2 see the words pop up 3 the beep
# surprises us and we stop"): the words are on the glass BEFORE the line,
# the count fades in as she says "count to six like you see now", and after
# a closing beep there is a breath before the next line so a last word is
# never cut. Her clips carry three extra seconds at both ends of an
# instruction; the fallback leads above stand in for them.
TURN_TEXT_AT_SEC = 1.5      # into the turn line: "…like you see now" — the count appears
AFTER_BEEP_SEC = 0.5
POSES_DIR = rails_path("display", "crissa-poses")
HOST_WAV_DIR = Path.home() / ".cache" / "house-enroll" / "host"
# the pre-arm marker: the coach (or an early enroll_prearm) parks the camera
# on the desk, closes the ear and cues the listen loop BEFORE the press, so
# t=0 is the first line, not a slew
PREARM_FILE = Path(os.environ.get("HOUSE_ENROLL_PREARM", "/tmp/house-enroll.prearm.json"))
PREARM_MAX_AGE_SEC = 90.0
# THE GIVE-BACK'S OWN TIMEOUTS, NAMED (round 46, 2026-10-23): the coach's Cancel lets this routine's finally give the
# camera, the loop, the ear and the hold back before it kills (cancel_release_budget_sec sums the chain).
CONDUCTOR_TIMEOUT_SEC = 4.0       # one POST to the player (the routine never waits on the picture)
FF_TIMEOUT_SEC = 5.0              # FaceFinder on/off
RESUME_TIMEOUT_SEC = 5.0          # the door watchdogs' resume
LEASE_RELEASE_TIMEOUT_SEC = 8.0   # the camera lease's release
CONDUCTOR = os.environ.get("HOUSE_CONDUCTOR_URL", _ports.url("player")).rstrip("/")
LOOP_KEY = os.environ.get("HOUSE_REGISTER_LOOP", "talk-angled")
# The lines, verbatim (the User 2026-09-09). Short: each is spoken to the end
# before the next beat, and Cosy's first byte is ~2.5 s.
LINE_OPENING = "I'm Crissa. This house is about to learn you. Sit down, this takes a minute."
LINE_WELCOME = "Welcome to my studio. Sit at the desk and look at the camera. I'll take your face shot."
LINE_READ = "Now read the sentence on the screen in your ordinary voice, from the beep to the beep."
LINE_TURN = "Now look away, about ninety degrees, and count to ten out loud."
LINE_DONE = "Done. Thank you, the house knows you now."
SPEAK_BIN = rails_path("scripts", "house-speak.py")



# ENROL ON THE MIC THE EAR MATCHES ON (the User 2026-10-02). Register at the desk took the Shure by name, while the
# ear matches voices on its own mic (the Seeed beam array here): the same sentence crossing mics scored 0.82 against
# its own print, and short phrases fell under the bar. site.json `enroll.mic` decides, in config, never in code:
#   "ear"           -> the source the ear listens on (rail-stt's RAIL_STT_MIC, read from its unit)
#   "<source name>" -> that Pulse source, if it exists
#   unset           -> today's behaviour byte for byte (desk = the Shure, else site.mic)
def ear_mic() -> str:
    if not _ppaths.is_house():
        return ear_source()                     # a pack has no rail-stt unit to read: ask the ear itself
    try:
        r = subprocess.run(["systemctl", "--user", "show", "rail-stt", "-p", "Environment"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    for tok in (r.stdout or "").replace("Environment=", " ").split():
        if tok.startswith("RAIL_STT_MIC="):
            return tok.split("=", 1)[1].strip()
    return ""


def ear_source(timeout: float = 2.0) -> str:
    """THE EAR'S MICROPHONE, in a pack (the User 2026-10-10: one microphone everywhere): the source the ear says it
    listens on (its /health `mic_source`, the same resolve_mic it captures with), else the sound server's default input
    (`pactl get-default-source`, the ear's own "auto" rule), else ''. The recorder and Init's plate use this answer."""
    import urllib.request
    url = (os.environ.get("RAIL_STT_URL") or _ports.url("stt")).rstrip("/")
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as r:
            got = str((json.loads(r.read().decode() or "{}") or {}).get("mic_source") or "").strip()
        if got and got != "page" and not got.startswith("none"):
            return got
    except Exception:  # noqa: BLE001  the ear down: the same rule it would use
        pass
    try:
        out = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        out = ""
    return "" if (not out or out.endswith(".monitor")) else out


def source_exists(name: str) -> bool:
    try:
        r = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=3)
    except Exception:
        return False
    return any(len(c) > 1 and c[1] == name for c in (ln.split("\t") for ln in (r.stdout or "").splitlines()))


def enroll_mic(site, *, ear=ear_mic, exists=source_exists) -> str:
    want = str(((getattr(site, "raw", None) or {}).get("enroll") or {}).get("mic") or "").strip()
    if not want:
        return ""
    name = ear() if want.lower() == "ear" else want
    return name if name and exists(name) else ""

def _speak_loop(text: str, *, timeout: float = 40.0) -> bool:
    """Say a line on the wall's loop: house-speak, foreground. It returns when
    playback ends, so a line never runs into a beep; its hop envelopes are
    what the conductor paints as her mouth. Never `_say` (cues.speak plays
    Cosy straight to paplay — no envelopes, no mouth)."""
    line = (text or "").strip()
    if not line or _cancelled():
        return False
    if cues.text_only():                     # Setup's cues are text only (2026-10-10): the line's time, no sound
        print(f"  (text only, not spoken) {line[:48]}", flush=True)
        cues._quiet_wait(min(12.0, 0.9 + 0.06 * len(line)))
        return True
    if not SPEAK_BIN.is_file():
        print(f"  speak: no {SPEAK_BIN}", flush=True)
        return False
    try:
        import subprocess
        import sys

        t0 = time.time()
        r = subprocess.run(
            [sys.executable, str(SPEAK_BIN), "--voice", "crissa", "--text", line],
            capture_output=True, text=True, timeout=timeout,
        )
        print(f"  said ({time.time() - t0:.1f}s rc={r.returncode}): {line[:48]}", flush=True)
        _wait_air_clear()
        return r.returncode == 0
    except Exception as e:
        print(f"  speak failed: {e!r}", flush=True)
        return False


def _wait_air_clear(max_s: float = 6.0, settle_s: float = 0.6) -> float:
    """house-speak returns when it has handed the audio over, up to ~2 s
    before the room hears the end of the line (the sink's buffering). A take
    that opened the desk mic inside that tail banked HER voice with the
    User's — two prints of 2026-09-09 13:23 / 13:33 anti-correlated with
    every other print of his. The conductor's on_air is the sink-timed
    truth (mouth_on_air, air_until): wait for it to drop, then a beat."""
    import urllib.request as _ur

    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            with _ur.urlopen(f"{CONDUCTOR}/v1/state", timeout=2) as r:
                st = json.loads(r.read().decode() or "{}")
            if not st.get("on_air"):
                break
        except Exception:
            break
        time.sleep(0.2)
    time.sleep(settle_s)
    waited = time.time() - t0
    if waited > settle_s + 0.3:
        print(f"  air clear after {waited:.1f}s", flush=True)
    return waited


def _conductor(path: str, payload: dict | None = None, *, timeout: float = CONDUCTOR_TIMEOUT_SEC):
    """POST to the conductor; None on any failure (the routine never waits on the picture)."""
    try:
        return _post(f"{CONDUCTOR}{path}", payload or {}, timeout=timeout)
    except Exception as e:
        print(f"  conductor {path}: {e!r}", flush=True)
        return None


def _loop_enter() -> str | None:
    """Cue the formal face-on loop with a bridge and narrow her head movement
    for the routine. Returns the register ceiling to restore, or None."""
    prev = None
    try:
        import urllib.request as _ur

        with _ur.urlopen(f"{CONDUCTOR}/v1/state", timeout=3) as r:
            st = json.loads(r.read().decode() or "{}")
        prev = ((st.get("sequencer") or {}).get("register")) or st.get("register")
    except Exception:
        prev = None
    _conductor("/v1/register", {"name": "presenting"})
    out = _conductor("/v1/cue", {"key": LOOP_KEY, "ttl": 0, "bridge": True, "by": "register"})
    print(f"  loop cue {LOOP_KEY} → {out}", flush=True)
    return prev if isinstance(prev, str) else None


def _loop_leave(prev: str | None) -> None:
    _conductor("/v1/home", {"by": "register"})
    if prev:
        _conductor("/v1/register", {"name": prev})

# Two seat takes — front, then a slow head-turn sweep.
SEAT_TAKES = (
    {"id": "seat_on", "pose": "front", "angle": "on"},
    {"id": "seat_off", "pose": "sweep", "angle": "off", "sweep": "1"},
)

_SNAP_THREADS: list[threading.Thread] = []
_CANCELLED = threading.Event()


def _cancelled() -> bool:
    return _CANCELLED.is_set()


def _request_cancel(signum=None, frame=None) -> None:
    """SIGTERM/INT: stop beeps and raise so finally still releases the camera."""
    _CANCELLED.set()
    try:
        cues.request_audio_stop()
    except Exception:
        pass
    if signum is not None:
        raise SystemExit(143)


def _arm_cancel() -> None:
    _CANCELLED.clear()
    try:
        cues.clear_audio_stop()
    except Exception:
        pass
    signal.signal(signal.SIGTERM, _request_cancel)
    signal.signal(signal.SIGINT, _request_cancel)


def record_seconds(line: str) -> float:
    """Front paragraph → 9 s. Count / away lines → 6 s (60% gate still reachable)."""
    words = max(1, len((line or "").split()))
    if words >= 20:
        return float(VOICE_DESK_SECONDS)
    return max(float(VOICE_TURN_SECONDS), words / 2.2 + 1.0)


def _post(url: str, body: dict, timeout: float = 12.0) -> dict[str, Any]:
    if _ports.camera_off() and str(url).startswith(CAMERA):   # no camera: a no-op that says so (2026-10-10)
        _ports.say_once("camera calls are no-ops")
        return dict(_ports.OFF)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode() or "{}")
            if isinstance(parsed, dict):
                parsed.setdefault("ok", False)
                parsed.setdefault("status", e.code)
                return parsed
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:200], "status": e.code}


def _pause_watchdogs(*, ttl_sec: float = 180.0) -> dict[str, Any]:
    """Same barge as Rescan / camera_call.hold — door watchdogs yield."""
    out = _post(
        f"{CAMERA}/v1/pause",
        {
            "owner": "voice-register",
            "ttl_sec": ttl_sec,
            "reason": "voice-register",
            "steal": True,
        },
        timeout=5,
    )
    print(f"  pause watchdogs → {out}", flush=True)
    return out


def _resume_watchdogs() -> dict[str, Any]:
    out = _post(
        f"{CAMERA}/v1/resume",
        {"owner": "voice-register", "force": True},
        timeout=RESUME_TIMEOUT_SEC,
    )
    print(f"  resume watchdogs → {out}", flush=True)
    return out


def _aim(site: Site, station: str, lease_id: str | None) -> None:
    """Desk (hour 6) with the lease — FF off so firmware does not steal pan."""
    if lease_id:
        for _ in range(2):
            try:
                _post(
                    f"{CAMERA}/v1/facefinder",
                    {"on": False, "lease_id": lease_id},
                    timeout=5,
                )
            except Exception as e:
                print(f"  ff off before aim: {e!r}", flush=True)
            time.sleep(0.15)
    pan, tilt = station_pt(site, station)
    body: dict[str, Any] = {
        "pan": pan,
        "tilt": tilt,
        "zoom": sweep_zoom(site),
    }
    if lease_id:
        body["lease_id"] = lease_id
    try:
        r = _post(f"{CAMERA}/v1/aim", body, timeout=15)
        print(
            f"  aim {station} pan={pan} tilt={tilt} zoom={body['zoom']} "
            f"actual={r.get('pan_actual')} lease={bool(lease_id)}",
            flush=True,
        )
        time.sleep(max(0.8, float((site.raw.get("enroll") or {}).get("settle_sec") or 1.0)))
        hold_fov(station)
    except Exception as e:
        print(f"  aim {station} failed: {e!r}", flush=True)


def _pin_wall() -> None:
    """Keep the room kiosk on the wall line, not Control Panel."""
    try:
        _post(f"{WALL}/v1/action", {"action": "kiosk_register"}, timeout=6)
    except Exception as e:
        print(f"  wall pin: {e!r}", flush=True)


_BUSY_SKIP = frozenset({"leased", "room_busy", "door_pending"})
_CANNOT_SEE = frozenset({"lights_out", "host_locked"})


def _auth_desk_like_secure(site: Site, person_id: str) -> dict[str, Any]:
    """From Guest, FaceFinder the desk the way Secure click does.

    POST /v1/ensure_user_auth mode=desk force_facefind. Presence code is
    unchanged. Must run before this process takes a camera lease.
    """
    if not is_primary_user(site, person_id):
        return {"ok": True, "skipped": "not_user_id"}
    uid = (person_id or "").strip().lower()
    body = {
        "mode": "desk",
        "force_facefind": True,
        "reason": "voice-register",
        "person_id": uid,
    }
    deadline = time.time() + 6.0
    last: dict[str, Any] = {}
    while True:
        if _cancelled():
            return {"ok": False, "why": "cancelled", "error": "cancelled"}
        last = _post(f"{PRESENCE}/v1/ensure_user_auth", body, timeout=40.0)
        if last.get("auth") is True or last.get("user_auth") is True:
            via = str(last.get("via") or "")
            print(
                f"  auth desk via={via} inherited={via == 'sticky'} "
                f"wall_sec={last.get('wall_sec')}",
                flush=True,
            )
            return {
                "ok": True,
                "via": via,
                "inherited": via == "sticky",
                "resp": last,
            }
        hunt = last.get("hunt") if isinstance(last.get("hunt"), dict) else {}
        skipped = str(hunt.get("skipped") or last.get("skipped") or "")
        reason = str(last.get("reason") or "")
        busy = skipped in _BUSY_SKIP or reason in _BUSY_SKIP
        if reason in _CANNOT_SEE:
            return {
                "ok": False,
                "why": "cannot_see",
                "error": "cannot see — lights or lock",
                "resp": last,
            }
        if busy:
            if time.time() >= deadline:
                return {
                    "ok": False,
                    "why": "busy",
                    "error": "camera busy — try again",
                    "resp": last,
                }
            time.sleep(1.0)
            continue
        sim = hunt.get("best_sim")
        if sim is None:
            sim = last.get("best_sim")
        n_faces = hunt.get("n_faces")
        if n_faces is None:
            n_faces = last.get("n_faces")
        return {
            "ok": False,
            "why": "not_user",
            "error": f"not you (sim={sim} faces={n_faces})",
            "resp": last,
        }


def _fail_register(text: str) -> None:
    msg = (text or "Could not confirm you.").strip()
    _glass("error", msg, pip="off")
    coach_emit(type="error", text=msg)
    print(f"  register abort: {msg}", flush=True)


def _glass(phase: str, text: str = "", **extra: Any) -> None:
    """Wall overlay + coach SSE. extra: seconds, mode, yaw, bin, det, …"""
    payload = {"phase": phase, "text": text, **extra}
    if phase == "turn":
        _LAST_TURN_GLASS.clear()
        _LAST_TURN_GLASS.update(text=text, **extra)     # round 33: the plate a look hint is put on (_turn_hint)
    try:
        _post(
            f"{WALL}/v1/action",
            {"action": "register_glass", "payload": payload},
            timeout=3,
        )
    except Exception as e:
        print(f"  glass: {e!r}", flush=True)
    coach_emit(type=phase, text=text, **extra)


_LAST_TURN_GLASS: dict[str, Any] = {}


def _close_to_cp() -> None:
    try:
        _post(f"{WALL}/v1/action", {"action": "kiosk_return_cp"}, timeout=6)
    except Exception as e:
        print(f"  close to CP: {e!r}", flush=True)


def _say(site: Site, text: str) -> None:
    """Cosy Crissa — never type the line onto the glass."""
    if _cancelled():
        return
    cues.speak(site, text, voice="crissa")


class _BarStart(list):
    """record_fixed's `started` hook (round 23): the moment the recorder starts is the moment the page's bar starts, so
    the bar runs out with the closing beep. Posted on its own thread so the recorder never waits for the glass."""

    def append(self, t) -> None:
        super().append(t)
        if os.environ.get("CRISSA_BAR_FROM_RECORDING", "1").strip().lower() in ("0", "off", "false", "no"):
            return
        try:
            if _ppaths.is_house():                  # the house's glass (:8099) has no rec_start: it would read idle
                return
        except Exception:  # noqa: BLE001
            return
        threading.Thread(target=_post, args=(f"{WALL}/v1/action",
                                             {"action": "register_glass", "payload": {"rec_start": True}}),
                         kwargs={"timeout": 3}, name="bar-start", daemon=True).start()


def _beep(site: Site, *, settle: bool = True) -> None:
    if _cancelled():
        return
    coach_emit(type="beep", text="GO", ms=int(max(0.35, float(site.beep_sec or 0.4)) * 1000))
    cues.go_beep(site, settle=settle)


def _go_and_record(site: Site, wav: Path, seconds: float, *, started: list | None = None, after=None) -> None:
    """GO, then the take (round 33): under go_with_beep_on the recorder is already running when the tone plays and the
    take starts at the tone's end; otherwise the beep, then after(), then record_fixed as before. Either way the log
    says how long it was from the GO stamp to the kept window's start (.101 read 0.75-0.78 s on the old path)."""
    stamp = started if started is not None else []
    t_go = time.monotonic()
    if go_with_beep_on():
        record_go(wav, site, seconds, lambda: _beep(site, settle=False), started=stamp,
                  after=(lambda: after() if after and not _cancelled() else None))
        how = "with the beep"
    else:
        _beep(site)
        if after:
            after()
        if _cancelled():
            return
        record_fixed(wav, site, seconds, started=stamp)
        how = "after the beep"
    if stamp:
        print("  GO to recording %.2f s (%s)" % (stamp[-1] - t_go, how), flush=True)


def _text_then_take(site: Site, line: str, seconds: float, *, phase: str = "read", pip: str = "hold", **take_kw: Any):
    """Put the take line on the glass, a beat, then the tones."""
    _glass(phase, line, seconds=seconds, mode="screen", pip=pip)
    if _cancelled():
        return None
    time.sleep(max(0.4, float(TEXT_LEAD_SEC)))
    if _cancelled():
        return None
    return _take_voice(site, take_kw.pop("person_id"), line=line, **take_kw)


def _join_announcer(talk: threading.Thread | None, *, timeout: float = 25.0) -> bool:
    """Block until the opener has finished playing. True = safe to beep.

    A short join left Audio8 talking over the front take (quiet 34–46%).
    Init's voice_session path is serial: utterance ends, then beep, then record.
    """
    if talk is None or not talk.is_alive():
        return not _cancelled()
    deadline = time.time() + max(1.0, float(timeout))
    while talk.is_alive() and not _cancelled() and time.time() < deadline:
        talk.join(timeout=0.4)
    if talk.is_alive() and not _cancelled():
        print("  announcer overrun — stopping playback before the beep", flush=True)
        try:
            cues.request_audio_stop()
        except Exception:
            pass
        talk.join(timeout=4.0)
    if talk.is_alive():
        print("  announcer still live — not recording over it", flush=True)
        return False
    return not _cancelled()


def _ff_set(lease_id: str | None, on: bool) -> None:
    """Firmware FaceFinder. Off for the slew; on for the takes."""
    if not lease_id:
        print(f"  ff {'on' if on else 'off'} skip — no lease", flush=True)
        return
    try:
        r = _post(
            f"{CAMERA}/v1/facefinder",
            {"on": bool(on), "lease_id": lease_id},
            timeout=FF_TIMEOUT_SEC,
        )
        print(f"  ff {'on' if on else 'off'} → {r}", flush=True)
    except Exception as e:
        print(f"  ff {'on' if on else 'off'} failed: {e!r}", flush=True)


def _join_snaps(timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    for t in list(_SNAP_THREADS):
        remain = deadline - time.time()
        if remain <= 0:
            break
        t.join(timeout=remain)


def _ff_face_jpeg(
    site: Site, station: str, lease_id: str | None
) -> tuple[bytes | None, dict[str, Any]]:
    """Re-scan face still: FaceFinder reframes, then one JPEG, then FF off."""
    ff_sec = float(os.environ.get("RAIL_PRESENCE_FACEFINDER_ENGAGE_SEC", "2.5"))
    if lease_id:
        try:
            _post(
                f"{CAMERA}/v1/facefinder",
                {"on": True, "lease_id": lease_id},
                timeout=5,
            )
            time.sleep(max(0.4, ff_sec))
        except Exception as e:
            print(f"  ff on for face snap: {e!r}", flush=True)
    jpeg, meta = enroll_hold_snap(site, station)
    if lease_id:
        for _ in range(2):
            try:
                _post(
                    f"{CAMERA}/v1/facefinder",
                    {"on": False, "lease_id": lease_id},
                    timeout=5,
                )
            except Exception as e:
                print(f"  ff off after face snap: {e!r}", flush=True)
            time.sleep(0.15)
    meta["facefinder"] = True
    return jpeg, meta


def _security_snap(
    site: Site,
    person_id: str,
    *,
    station: str,
    pose: str,
    take_plan_id: str,
    lease_id: str | None = None,
    have: list[str] | None = None,
) -> Path | None:
    """Desk JPEG → Inspector FaceID (`/v1/store_face`) → people/<id>/face JSON.

    Same engine as Re-scan (`get_face_engine` / `_faceidargest_from_jpeg`).
    FaceFinder reframes first so the still is a usable face, not a 25px speck.
    """
    try:
        assert_not_frozen(person_id, root=site.root)
    except Exception as e:
        print(f"  face snap skipped: {e}", flush=True)
        return None
    ensure_person(person_id, root=site.root)
    jpeg, meta = _ff_face_jpeg(site, station, lease_id)
    if not jpeg:
        print(f"  face snap empty pose={pose}", flush=True)
        return None
    stored = _post(
        f"{PRESENCE}/v1/store_face",
        {
            "person_id": person_id,
            "image_b64": base64.b64encode(jpeg).decode(),
            "said_by": "voice-register",
            "enroll": True,
            "pose": pose,
            "station": station,
            "note": take_plan_id,
        },
        timeout=60,
    )
    print(
        f"  inspector store pose={pose} pan={meta.get('pan_actual')} "
        f"ok={stored.get('ok')} px={stored.get('px')} det={stored.get('det')} "
        f"yaw={stored.get('yaw')} bin={stored.get('bin')} "
        f"json={stored.get('json')} err={stored.get('error')}",
        flush=True,
    )
    if stored.get("ok"):
        coach_emit(
            type="face_ok",
            text="Face saved",
            yaw=stored.get("yaw"),
            bin=stored.get("bin"),
            det=stored.get("det"),
            has_emb=True,
        )
        b = stored.get("bin")
        if have is not None and b and b not in have:
            have.append(str(b))
            _emit_angles(have)
    jp = stored.get("json")
    return Path(jp) if jp else None


def _emit_angles(have: list[str]) -> None:
    coach_emit(type="angles", have=list(have), want=list(WANT_BINS))


def _sweep_store(
    site: Site,
    person_id: str,
    *,
    station: str,
    paths: list[Path],
    have: list[str],
) -> list[str]:
    """Batch-embed sweep JPEGs; store_face the best det per yaw bin."""
    if not paths:
        _emit_angles(have)
        return have
    recs = embed_jpegs(paths)
    ok = [r for r in recs if r.get("ok") and r.get("bin")]
    for w in best_per_bin(ok):
        b = str(w.get("bin") or "")
        if not b or b in have:
            continue
        if turn_check_on() and not b.startswith("front"):
            # round 34: the turned stills are the counts' own, each checked after the head moved; a sweep frame of a
            # turned bin is never banked (.101 round 33: a back-of-head frame read three_quarter_L, det 1.0)
            print(f"  sweep skip bin={b}: the counts' checked stills carry the turns", flush=True)
            continue
        try:
            jpeg = Path(w["path"]).read_bytes()
        except OSError:
            continue
        stored = _post(
            f"{PRESENCE}/v1/store_face",
            {
                "person_id": person_id,
                "image_b64": base64.b64encode(jpeg).decode(),
                "said_by": "voice-register",
                "enroll": True,
                "pose": b[:20],
                "station": station,
                "note": "sweep",
            },
            timeout=60,
        )
        if not stored.get("ok"):
            print(f"  sweep store skip bin={b} err={stored.get('error')}", flush=True)
            continue
        have.append(stored.get("bin") or b)
        coach_emit(
            type="face_ok",
            text="Face saved",
            yaw=stored.get("yaw") if stored.get("yaw") is not None else w.get("yaw"),
            bin=stored.get("bin") or b,
            det=stored.get("det") if stored.get("det") is not None else w.get("det_score"),
            has_emb=True,
        )
        print(
            f"  sweep kept bin={have[-1]} det={stored.get('det')} yaw={stored.get('yaw')}",
            flush=True,
        )
    _emit_angles(have)
    return have


def voice_freeze_check_on() -> bool:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): the face snap (and voice_session) refuse a frozen
    identity with assert_not_frozen, but Register's voice path never asked, so a frozen bank took new prints. Now
    _take_voice and _bank_wav ask first, the face snap's way: said and skipped. Kill: CRISSA_VOICE_FREEZE_CHECK=0."""
    return os.environ.get("CRISSA_VOICE_FREEZE_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def _voice_bank_open(site: Site, person_id: str) -> bool:
    if not voice_freeze_check_on():
        return True
    try:
        assert_not_frozen(person_id, root=getattr(site, "root", None))
    except Exception as e:  # noqa: BLE001  the face snap's way: said, skipped
        print(f"  voice take skipped: {e}", flush=True)
        return False
    return True


def _take_voice(
    site: Site,
    person_id: str,
    *,
    line: str,
    pose: str,
    angle: str,
    take_id: str,
    station: str,
    mic: str,
    pre_snap=None,
    snap_into: list[Path] | None = None,
    kind: str = "talk",
    seconds: float | None = None,
    defer_bank: bool = False,
) -> Path | dict[str, Any] | None:
    """seconds: the wall host passes the take length (take_plans); None =
    record_seconds(line) as the loop host always did. defer_bank: return
    the recording {wav, slot, seconds, …} and let _bank_wav() embed and
    write it after "Thanks" — no join on the snap burst either (its paths
    are collected by the teardown)."""
    if _cancelled():
        return None
    if not _voice_bank_open(site, person_id):     # round 46: a frozen identity takes no new take
        return None
    out_dir = ensure_person(person_id, root=site.root) / "voice"
    slot = next_slot(out_dir)
    seconds = float(seconds) if seconds else record_seconds(line)
    wav = out_dir / f"enroll_{int(time.time())}_{slot}.wav"
    old_mic = os.environ.get("HOUSE_ENROLL_MIC")
    os.environ["HOUSE_ENROLL_MIC"] = mic
    stop = threading.Event()
    snap_th: threading.Thread | None = None
    if snap_into is not None and not _ports.camera_off():        # no camera: no stills beside the take (2026-10-10)

        def _cam_loop() -> None:
            i = 0
            t0 = time.time()
            window = float(seconds) + 0.5
            while not stop.is_set() and not _cancelled() and (time.time() - t0) < window:
                jpeg, _meta = enroll_hold_snap(site, station)
                if jpeg:
                    p = (
                        ensure_person(person_id, root=site.root)
                        / "face"
                        / "refs"
                        / f"take_{take_id}_{int(t0)}_{i:02d}.jpg"
                    )
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(jpeg)
                    snap_into.append(p)
                    i += 1
                if stop.wait(0.7):
                    break

        snap_th = threading.Thread(target=_cam_loop, name=f"cam-{take_id}", daemon=True)
        snap_th.start()
    try:
        unmute_mic(site)
        if _cancelled():
            return None
        waited = wait_ear_released()
        if waited > 0.2:
            print("  GO waited %.2f s for the ear to let go of the mic (take %s)" % (waited, take_id), flush=True)
        if kind == "count" and not cues.same_go():
            cues.count_tick(site)
            if pre_snap:
                pre_snap()
            if _cancelled():
                return None
            record_fixed(wav, site, seconds, started=_BarStart())
        else:
            # _beep(site)       # every take's GO is the same beep (the User 2026-10-11), played inside the take now
            _go_and_record(site, wav, seconds, started=_BarStart(), after=pre_snap)
        if not _cancelled():
            if kind == "count" and not cues.same_go():
                cues.done_chirp(site)
            else:
                _beep(site)
    finally:
        stop.set()
        if snap_th is not None:
            if defer_bank:
                _SNAP_THREADS.append(snap_th)   # the burst drains on its own; the teardown joins it
            else:
                snap_th.join(timeout=12)
        if old_mic is None:
            os.environ.pop("HOUSE_ENROLL_MIC", None)
        else:
            os.environ["HOUSE_ENROLL_MIC"] = old_mic
    if _cancelled():
        return None
    if defer_bank:
        return {"wav": wav, "slot": slot, "seconds": seconds, "line": line, "pose": pose,
                "angle": angle, "take_id": take_id, "station": station, "mic": mic, "out_dir": out_dir}
    return _bank_wav(site, person_id, wav, slot=slot, seconds=seconds, line=line, pose=pose,
                     angle=angle, take_id=take_id, station=station, mic=mic, out_dir=out_dir, retry=True)


QUIET_TAKES: list[dict[str, Any]] = []     # this routine's takes the quiet gate dropped (the User 2026-10-10, .101 V)
# A SILENT TAKE IS SAID, AND RETRIED (the User 2026-10-10: both takes on .101 were dropped as QUIET because no sentence
# was on the screen, and Init then read "skipped"). Init shows this with Retry.
NOTHING_HEARD = "Nothing was heard. Please read the sentence aloud at the beep, in your normal voice, then press Retry."
NOTHING_HEARD_COUNT = "Nothing was heard. Please count aloud at the beep, in your normal voice, then press Retry."
# THE COUNT'S QUIET GATE (the User 2026-10-10, .101 round 5 GG: a brisk "One two three four five six", 2.6 s in the
# 4.0 s window, read 30 % voiced against 35 % and was dropped). Measured with this coach's own voiced_fraction on the
# 18 stock Pocket voices of the kit saying the cue in the 4.0 s window: brisk counts 0.25 to 0.54 (median 0.315; 12 of
# 18 under 35 %), counts with small pauses 0.305 to 0.525 (3 of 18 under 35 %); .101's own take reproduced at 0.305.
# A quiet take - what the gate is for - reads at most 18 % (the house's room clips, voice_session.voiced_fraction) and
# a click in silence 0.5 %. The gate is now 25 %: 1.0 voiced second of the window, every measured count passes, still 7
# points over the quietest room. The window is unchanged (a calm count runs to 4.2 s; a longer window would lower the
# share a brisk count reads). The read take's bar is unchanged (voiced_bar).
COUNT_MIN_ACTIVE = 0.25
# ONE TAKE AGAIN (GG): Init's "Redo the count" (or "Redo the sentence") starts the routine with this set to the take
REGISTER_ONLY_ENV = "CRISSA_REGISTER_ONLY"


def register_only() -> str:
    """'read' or 'count' when the coach asked for that take alone, else '' (both takes)."""
    v = str(os.environ.get(REGISTER_ONLY_ENV) or "").strip().lower()
    return v if v in ("read", "count") else ""


# FOUR PRINTS, NOT TWO (the User 2026-10-11, .101 round 9: his voice sat at 0.27-0.63 against the 0.50 line on the two
# prints of a night-time Init): the sentence and the count, each facing the camera and turned away, at the usual seat.
# The order keeps each existing line true: the sentence facing (Lila's "okay"), the sentence turned away, the count
# facing, the count turned away (Lila's "turn": "Now turn away from the screen and count"). The two new instructions
# have no Lila clip yet: their words are on the glass (crissa_init.glass_cue, `away`), with a HOST_FALLBACK_SEC beat
# to read them and turn before the beep. On in a pack; the house keeps its two. CRISSA_REGISTER_TAKES=2|4 decides.
REGISTER_TAKES_ENV = "CRISSA_REGISTER_TAKES"
COUNT_TAKE_IDS = ("seat_off", "seat_count_on", "seat_count_on_2", "seat_off_2")   # _2: the verbal routine


def four_takes() -> bool:
    v = str(os.environ.get(REGISTER_TAKES_ENV) or "").strip()
    if v in ("2", "4"):
        return v == "4"
    try:
        return not _ppaths.is_house()
    except Exception:  # noqa: BLE001  a tree that cannot tell: the house's two
        return False


def _tell_coach(**event: Any) -> None:
    """An event the coach's own /session must carry (GG, the User 2026-10-11, .101 round 6: the count was dropped and
    the coach that serves :8196 answered /session with no `dropped`). This routine runs as the coach's child and never
    calls coach_start(), so coach_emit() publishes to a hub of its own that nothing serves; the event goes to the coach
    that spawned it (ENROLL_UI_URL, set by its start_enroll) by its /emit. Without that address (run by hand) it is
    coach_emit() as before. Never raises."""
    url = str(os.environ.get("ENROLL_UI_URL") or "").strip().rstrip("/")
    if not url:
        coach_emit(**event)
        return
    try:
        _post(url + "/emit", dict(event), timeout=3.0)
    except Exception as e:  # noqa: BLE001  a lost event never stops the banking
        print(f"  coach event {event.get('type')}: {type(e).__name__}", flush=True)


def _bank_wav(
    site: Site,
    person_id: str,
    wav: Path,
    *,
    slot: int,
    seconds: float,
    line: str,
    pose: str,
    angle: str,
    take_id: str,
    station: str,
    mic: str,
    out_dir: Path,
    retry: bool = True,
    min_active: float = 0.60,
    min_peak: int = 0,
) -> Path | None:
    """Quiet gate (min_active: 0.60 for a sentence; a four-second count is
    half silence by nature, the wall host passes 0.40 for it), one retry
    (loop host only), WeSpeaker embed, the print. The wall host runs this
    after "Thanks" with retry=False: a quiet take goes to quarantine and the
    coach card says so; nobody re-beeps."""
    if not _voice_bank_open(site, person_id):     # round 46: a frozen identity takes no print (the take stays on disk)
        return None
    pk = peak_pcm(wav) if wav.is_file() else 0
    if not wav.is_file():
        return None
    active = voiced_fraction(wav)
    clipped = pk >= 32767
    fault = take_fault(wav)                    # round 20: noise or a steady hum is not a voice (voice_session)
    coach_emit(
        type="take",
        text=line,
        active=round(active, 3),
        peak=pk,
        clipped=clipped,
        fault=fault,
        seconds=seconds,
    )
    if _cancelled():
        return None
    if (active < min_active or fault) and retry:
        # Room-tone ECAPA is a false-accept cluster (COMMS 2026-08-24 §5b).
        coach_emit(
            type="take",
            text=("That one was only noise, not a voice — let us take it again." if fault else
                  "That one was mostly quiet — let us take it again."),
            active=round(active, 3),
            ok=False,
        )
        _glass("read", line, seconds=seconds, mode="screen")
        wav2 = out_dir / f"enroll_{int(time.time())}_{slot}b.wav"
        try:
            unmute_mic(site)
            _go_and_record(site, wav2, seconds)
            _beep(site)
        except Exception as e:
            print(f"  quiet retry failed: {e!r}", flush=True)
            wav2 = wav
        if wav2.is_file():
            wav = wav2
            pk = peak_pcm(wav)
            active = voiced_fraction(wav)
            clipped = pk >= 32767
            fault = take_fault(wav)
            coach_emit(
                type="take",
                text=line,
                active=round(active, 3),
                peak=pk,
                clipped=clipped,
                seconds=seconds,
            )
    if _cancelled():
        return None
    usable = (active >= min_active and not (min_peak > 0 and pk < min_peak)    # min_peak: the verbal routine's (V_MIN_PEAK)
              and not fault)
    meta = live_encoder_meta()
    emb = embed_wav(wav) if usable else None
    if usable and emb is None:
        print("  EMBED fail — not banking (live encoder returned none)", flush=True)
        return None
    rec = {
        "person_id": person_id,
        "modality": "voice",
        "model": meta["model"],
        "dim": int(len(emb)) if emb is not None else int(meta["dim"]),
        "encoding": None if emb is None else [float(x) for x in emb.tolist()],
        "source": "voice_register" if usable else "voice_register_quiet",
        "usable": usable,
        "active": round(active, 3),
        "clipped": clipped,
        "fault": fault,
        "site_id": site.site_id,
        "cue": line,
        "angle": angle,
        "station": station,
        "pose": pose,
        "take_plan_id": take_id,
        "mic": mic,
        "wav": str(wav),
        "peak": pk,
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if usable:
        jp = voice_print_path(
            out_dir, slot, ts=rec["ts"], family=str(meta["family"])
        )
        # deferred banking writes both takes in the same second (03:02 today
        # the turn print overwrote the front print): never reuse a name
        while jp.is_file():
            slot += 1
            jp = voice_print_path(out_dir, slot, ts=rec["ts"], family=str(meta["family"]))
    else:
        qdir = out_dir / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        jp = qdir / voice_print_path(
            qdir, slot, ts=rec["ts"], family=str(meta["family"])
        ).name
        try:
            dest = qdir / wav.name
            if wav.is_file() and wav.parent != qdir:
                wav.replace(dest)
                rec["wav"] = str(dest)
        except OSError:
            pass
        print(
            f"  {'NOISE' if fault else 'QUIET'} skip bank {jp.name} active={active:.0%} peak={pk}"
            + (f" fault={fault} ({TAKE_FAULT_WORDS.get(fault, fault)})" if fault else ""),
            flush=True,
        )
        QUIET_TAKES.append({"take_id": take_id, "active": round(active, 3), "peak": pk, "mic": mic, "fault": fault})
        # Init reads this from the coach's session and offers that take again (GG); `why` says noise from quiet
        _tell_coach(type="take_dropped", take_id=take_id, take=("count" if take_id in COUNT_TAKE_IDS else "read"),
                    active=round(active, 3), need=round(float(min_active), 3), peak=pk, why=fault or "quiet")
        return None
    write_live_voice_print(jp, rec)
    print(
        f"  OK {jp.name} station={station} pose={pose} peak={pk} "
        f"active={active:.0%} sec={seconds:.1f} model={meta['model']}",
        flush=True,
    )
    return jp


# ── THE WALL HOST ───────────────────────────────────────────────────────────
def _inventory_clip(key: str) -> Path | None:
    """The clip file for an inventory key, or None (a host clip not rendered yet)."""
    try:
        inv = json.loads((POSES_DIR / "inventory.json").read_text(encoding="utf-8"))
        row = (inv.get("clips") or {}).get(key) or {}
        f = str(row.get("file") or "")
    except Exception:
        return None
    if not f:
        return None
    path = POSES_DIR / f
    return path if path.is_file() else None


def _host_wav(key: str) -> Path | None:
    """The host clip's audio as a WAV beside the beeps' cache (ffmpeg -vn,
    once per clip mtime). None when the clip is missing or silent."""
    clip = _inventory_clip(HOST_CLIPS.get(key, ""))
    if clip is None:
        return None
    HOST_WAV_DIR.mkdir(parents=True, exist_ok=True)
    wav = HOST_WAV_DIR / f"enroll-host-{key}.wav"
    try:
        if wav.is_file() and wav.stat().st_mtime >= clip.stat().st_mtime and wav.stat().st_size > 1000:
            return wav
    except OSError:
        pass
    import subprocess

    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "48000",
         "-f", "wav", str(wav)],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0 or not wav.is_file() or wav.stat().st_size < 1000:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return wav


def _host_line(key: str) -> tuple[Path | None, float | None]:
    """The audio a host beat plays, and how long it runs on after her last sound (None = whole clip, no stamp). A line
    in TRIM_KEYS is cut 30 ms after her last word (cues.trim_wav), once per extracted WAV."""
    wav = _host_wav(key)
    if wav is None or key not in TRIM_KEYS or not cues.beep_gap_on():
        return wav, None
    out = wav.with_name(wav.stem + ".trim.wav")
    try:
        got = _HOST_TAIL.get(key)
        if got and out.is_file() and out.stat().st_mtime == got[0] and out.stat().st_mtime >= wav.stat().st_mtime:
            return out, got[1]
        kept = cues.trim_wav(wav, out)
        _HOST_TAIL[key] = (out.stat().st_mtime, kept)
        return out, kept
    except Exception as e:  # noqa: BLE001  a trim that fails plays the whole clip, as before
        print(f"  host {key}: trim failed ({e!r}) — whole clip", flush=True)
        return wav, None


def _host_warm() -> dict[str, bool]:
    """Extract every host clip's audio ahead of the routine (and cut the lines that end on a beep)."""
    return {k: _host_line(k)[0] is not None for k in HOST_CLIPS}


def _drop_register_cues() -> list[str]:
    """Every pending queue row the register put there. A forced host line is
    a one-shot; a stale `lila-reg-listen` cue (the pre-arm's, or a take's
    that never found its seam) fires at the line's FIRST seam and cuts it —
    Crissa's handoff showed one second on 2026-09-12 06:34 before the loop
    took the wall while her voice carried on for sixteen. Force-play only
    on an empty register queue."""
    gone: list[str] = []
    try:
        with urllib.request.urlopen(f"{CONDUCTOR}/v1/queue", timeout=2.0) as r:
            q = json.loads(r.read().decode() or "{}")
    except Exception:
        return gone
    rows = q.get("queue_rows") if isinstance(q, dict) else None
    if rows is None and isinstance(q, dict):
        rows = q.get("rows")
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("by") or "") != "register":
            continue
        rid = row.get("id")
        if rid is None:
            continue
        out = _conductor("/v1/queue", {"op": "delete", "id": str(rid)}, timeout=2.0)
        if isinstance(out, dict) and out.get("ok", True):
            gone.append(f"{row.get('key')}#{rid}")
    if gone:
        print(f"  dropped stale register cues: {', '.join(gone)}", flush=True)
    return gone


def _host_play(site: Site, key: str, *, cue: bool = True) -> float:
    """One host beat: cue the clip on the conductor (force — host clips are
    not in the focus kit) and play its audio to the room, blocking for the
    clip's length. Missing clip → the words are on the glass already; leave
    HOST_FALLBACK_SEC so the beep is not on top of them. Returns seconds."""
    if _cancelled():
        return 0.0
    t0 = time.time()
    ikey = HOST_CLIPS.get(key, "")
    wav, tail = _host_line(key)        # resolved before the picture is cued: nothing between the cue and her voice
    if cue and ikey:
        _drop_register_cues()
        print(f"  host {key} at {time.strftime('%H:%M:%S')}", flush=True)
        _conductor("/v1/play", {"key": ikey, "force": True, "by": "register"}, timeout=3.0)
    if wav is None:
        print(f"  host {key}: no clip ({ikey}) — glass only", flush=True)
        time.sleep(HOST_FALLBACK_SEC.get(key, 0.8))
        return time.time() - t0
    cues.play_wav(wav, sink=site.sink, spoken_tail=tail)
    return time.time() - t0


# LILA SHOWS THE TURN (round 21, the User 2026-10-14: "Lila's 'turn your head' clip fits there"; "it would depend on turn
# your head left to come first"). Her Register turn film (one turn toward the viewer's left, -35..-38 degrees, back to
# front by 7 s, seams 0.9997 on her listen loop) plays under the routine's first turn cue: the picture only - the cue's
# own words are said live and painted on it (house-conductor LIPS_BORROW_FILMS; its turn frames take her profile tiles
# from clip-lila-reg-turn.mouth.turnfill.json) - and her listen loop is queued after it. Kill: CRISSA_TURN_FILM=0.
TURN_FILM_KEY = "lila-reg-turn"


def turn_film_on() -> bool:
    if os.environ.get("CRISSA_TURN_FILM", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return not _house_tree()


def turn_film_under_cue(site: Site) -> bool:
    """Cue Lila's turn film under the next line, then her listen loop. True when the film was cued."""
    if _cancelled() or not turn_film_on() or _inventory_clip(TURN_FILM_KEY) is None:
        return False
    _drop_register_cues()
    print(f"  host turn film {TURN_FILM_KEY} under the turn cue at {time.strftime('%H:%M:%S')}", flush=True)
    _conductor("/v1/play", {"key": TURN_FILM_KEY, "force": True, "by": "register"}, timeout=3.0)
    ikey = HOST_CLIPS.get("listen", "")
    if ikey and _inventory_clip(ikey) is not None:
        _conductor("/v1/cue", {"key": ikey, "ttl": 0, "bridge": False, "by": "register"}, timeout=3.0)
    return True


def _host_loop_under_take(site: Site) -> None:
    """The listen loop under a take (silent); a missing clip keeps whatever plays."""
    ikey = HOST_CLIPS.get("listen", "")
    if ikey and _inventory_clip(ikey) is not None:
        _conductor("/v1/cue", {"key": ikey, "ttl": 0, "bridge": False, "by": "register"}, timeout=3.0)


def _snap_now(site: Site, person_id: str, *, station: str, tag: str, note: str) -> threading.Thread | None:
    """One JPEG now → FaceID (/v1/store_face) on a thread. Nothing waits on
    it; the teardown joins it. tag is the caller's pose hint — FaceID
    measures the real yaw and bins it."""
    if _cancelled() or _ports.camera_off():
        return None
    jpeg, meta = enroll_hold_snap(site, station)
    if not jpeg:
        print(f"  snap {tag}: empty", flush=True)
        return None
    t_snap = time.time()

    def _store() -> None:
        stored = _post(
            f"{PRESENCE}/v1/store_face",
            {
                "person_id": person_id,
                "image_b64": base64.b64encode(jpeg).decode(),
                "said_by": "voice-register",
                "enroll": True,
                "pose": tag,
                "station": station,
                "note": note,
            },
            timeout=60,
        )
        print(
            f"  faceid {tag} (+{time.time() - t_snap:.2f}s) ok={stored.get('ok')} "
            f"bin={stored.get('bin')} yaw={stored.get('yaw')} det={stored.get('det')} front_sim={stored.get('front_sim')} "
            f"err={stored.get('error')}",
            flush=True,
        )
        if stored.get("ok"):
            if tag == "front":
                _FRONT_OK.add(person_id)                         # round 34: a front banked in the counts
            if tag == "front" and stored.get("yaw") is not None:
                _FRONT_YAW[person_id] = float(stored["yaw"])     # this person's front at this camera (round 19)
            coach_emit(type="face_ok", text="Face saved", yaw=stored.get("yaw"), bin=stored.get("bin"),
                       det=stored.get("det"), has_emb=True)

    th = threading.Thread(target=_store, name=f"faceid-{tag}", daemon=True)
    th.start()
    _SNAP_THREADS.append(th)
    return th


def _snap_soon(site: Site, person_id: str, *, station: str, tag: str, note: str, side: str = ""):
    """_snap_now on a thread: the profile still is still taken at her last word, but its camera grab is no longer
    inside the 0.30 s between her last word and the beep (the User 2026-10-11). The teardown joins it with the other
    snaps. Kill: HOUSE_ENROLL_BEEP_GAP=0 grabs it in line, as before. A turned still (tag "profile") waits for the
    turn (round 17, _snap_turned; `side` is the side the cue asked for)."""
    if tag == "profile" and turn_wait_on():
        th = threading.Thread(target=_snap_turned, args=(site, person_id),
                              kwargs={"station": station, "tag": tag, "note": note, "side": side},
                              name=f"snap-turned-{side or tag}", daemon=True)
        th.start()
        _SNAP_THREADS.append(th)
        return th
    if not cues.beep_gap_on():
        _snap_now(site, person_id, station=station, tag=tag, note=note)
        return
    th = threading.Thread(target=_snap_now, args=(site, person_id),
                          kwargs={"station": station, "tag": tag, "note": note},
                          name=f"snap-{tag}", daemon=True)
    th.start()
    _SNAP_THREADS.append(th)


# THE TURNED STILL WAITS FOR THE TURN (round 17, .101 2026-10-13 on round 16: both turned stills of the counts, "turn your
# head a little to your left / right", answered `profile (+0.02s) ok=False err=no_face`: one grab the moment the cue
# ended, before the head had moved; round 15's three-quarter left at +0.46 s was the luck of timing). The grab now looks
# again and again (presence's /v1/face_yaw: the face and its yaw in one still, nothing stored) until the face has turned
# past TURN_YAW_MIN toward the side asked for (left = a negative yaw, an _L bin; right = positive, _R), for at most
# TURN_WAIT_S after the cue, and stores that still; at the time limit it stores the most turned still it saw (a turn to
# the other side before a front one), else the last look, as before. A look where the engine found nothing is simply
# looked at again, and a store that finds no face in a still the look had a face in is tried once more. A presence
# without the route (404) stores the first still at once, as round 16 did. The house never comes here (its coach is
# house-id's). Kill: CRISSA_TURN_GRAB_WAIT=0 (one still at once, round 16); CRISSA_TURN_GRAB_WAIT_S, CRISSA_TURN_YAW_MIN.
#
# PAST THE FRONT, AND PAST THEIR OWN FRONT (round 19, .101 2026-10-14: the left still was yaw -0.19 at +0.03 s, one look -
# "barely turned" - while the face looking at the camera read -0.156: a desk camera off to one side). The bar is past the
# front band (face_yaw FRONT_MAX 0.18, was 0.06), 0.22 (was 0.15), so a turned still is binned to its side; and when a
# front still of this person was stored in this routine (counts 1 and 3), the turn must also move TURN_FROM_FRONT (0.12)
# from that yaw toward the side asked - their own front, not the camera's axis. CRISSA_TURN_YAW_MIN=0.15 and
# CRISSA_TURN_FROM_FRONT=0 are round 18's rule.
TURN_WAIT_S = float(os.environ.get("CRISSA_TURN_GRAB_WAIT_S", "3.0") or 0)
TURN_YAW_MIN = float(os.environ.get("CRISSA_TURN_YAW_MIN", "0.22") or 0)
TURN_FROM_FRONT = float(os.environ.get("CRISSA_TURN_FROM_FRONT", "0.12") or 0)
TURN_LOOK_GAP_S = 0.2
# THE TURN IS LOOKED AT THROUGH ITS COUNT (round 21, .101 2026-10-14 round 20: the User's turned stills read "no picture in
# 0 looks" on both turns and a visitor's "15 looks, yaw None": 3 s after the cue and the looks stopped, though the head
# stays turned for the whole count after the beep). With no usable look by TURN_WAIT_S the looks go on, up to
# TURN_LOOK_MAX_S, until the next count's cue starts (_TURN_GEN); a look that fails says why once (the camera's error).
# Kill: CRISSA_TURN_LOOK_MAX_S=0 (round 20: TURN_WAIT_S only).
TURN_LOOK_MAX_S = float(os.environ.get("CRISSA_TURN_LOOK_MAX_S", "9.0") or 0)
_TURN_GEN: dict[str, int] = {"n": 0}       # bumped as each count's cue starts and after the last count
TURN_SIGN = {"left": -1.0, "right": 1.0}
_FRONT_YAW: dict[str, float] = {}         # person id -> the yaw of their last stored front still (_snap_now)
_FRONT_OK: set = set()                    # person ids with a front still stored in this routine (round 34)


def _house_tree() -> bool:
    for mod in ("_paths", "_ppaths"):
        m = globals().get(mod)
        fn = getattr(m, "is_house", None) if m is not None else None
        if callable(fn):
            try:
                return bool(fn())
            except Exception:  # noqa: BLE001
                return False
    return False


def turn_wait_on() -> bool:
    if os.environ.get("CRISSA_TURN_GRAB_WAIT", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return not _house_tree()


# EVERY COUNT AT THE CAMERA TAKES A FRONT STILL (round 19, .101 2026-10-14: count 1's still came out binned turned and
# count 3, "look at the camera again", took none - so Face photos asked "Please look at the camera" at the end). The
# User: "Why not during the speech at camera and then turned away for pics and all would be invisible." Kill:
# CRISSA_FRONT_STILL_EVERY_COUNT=0 (count 1 only, round 18).
def front_every_count() -> bool:
    return os.environ.get("CRISSA_FRONT_STILL_EVERY_COUNT", "1").strip().lower() not in ("0", "false", "no", "off")


def turn_side(words: str) -> str:
    """The side a cue asks the head to turn to: "left", "right", or "" (a turn with no side named)."""
    w = (words or "").lower()
    return "left" if " left" in w else ("right" if " right" in w else "")


def _turn_look(site: Site, station: str) -> tuple[bytes | None, str]:
    """One camera still for a look (the FOV is held: the front still aimed it), and why there is none."""
    try:
        from .face_snap import snapshot_jpeg
        jpeg = snapshot_jpeg(site) or None
        return jpeg, ("" if jpeg else "an empty picture")
    except Exception as e:  # noqa: BLE001
        return None, repr(e)[:120]


def turn_rank(yaw: float, side: str, det: float = 0.0, front: float | None = None) -> tuple:
    """How good a look is for the turn asked for: past the bar toward the side asked (and, with `front` - their own
    front yaw - at least TURN_FROM_FRONT away from it that way), then past the bar either way (a camera that
    mirrors), the more turned the better; under the bar, the most toward the side asked (the most turned with no side
    named); then the detection."""
    sign = TURN_SIGN.get(side, 0.0)
    toward = yaw * sign if sign else abs(yaw)
    either = abs(yaw) >= TURN_YAW_MIN
    moved = front is None or not sign or (yaw - float(front)) * sign >= TURN_FROM_FRONT
    return (toward >= TURN_YAW_MIN and moved, either, abs(yaw) if either else toward, det)


def _snap_turned(site: Site, person_id: str, *, station: str, tag: str, note: str, side: str = "",
                 sleep=None, clock=None) -> dict[str, Any]:
    """The turned still, once the head has turned (see THE TURNED STILL WAITS FOR THE TURN). Returns what happened.
    The looks are counted as well as timed, so a stubbed sleep never spins."""
    sleep, clock = sleep or time.sleep, clock or time.time
    if _cancelled() or _camera_off():
        return {"skipped": True}
    t0 = clock()
    deadline = t0 + max(0.0, TURN_WAIT_S)
    longest = t0 + max(0.0, TURN_WAIT_S, TURN_LOOK_MAX_S)
    gen = _TURN_GEN["n"]
    front = _FRONT_YAW.get(person_id)         # their own front at this camera, when a front still was stored
    best: tuple | None = None                 # (rank, jpeg, look)
    last, looks, why, err = None, 0, "time", ""
    blind, hinted, reasons = 0, False, set()     # round 33: looks with a picture and no face; the reasons said once
    for _try in range(int(max(0.0, TURN_WAIT_S, TURN_LOOK_MAX_S) / TURN_LOOK_GAP_S) + 2):
        if _cancelled() or _TURN_GEN["n"] != gen:
            break
        jpeg, look_err = _turn_look(site, station)
        err = err or look_err
        if jpeg:
            last, looks = jpeg, looks + 1
            try:
                got = _post(f"{PRESENCE}/v1/face_yaw", {"image_b64": base64.b64encode(jpeg).decode()}, timeout=5)
            except Exception as e:  # noqa: BLE001 - presence not answering: this still, as round 16
                got = {"ok": False, "status": 0, "error": repr(e)[:80]}
            if not isinstance(got, dict) or got.get("status") in (0, 404, 405):
                why = "no look route"
                break
            if not (got.get("ok") and got.get("yaw") is not None) and turn_check_on():
                reason = str(got.get("error") or got.get("why") or got.get("reason") or "no face")[:80]
                if reason not in reasons:
                    reasons.add(reason)
                    print(f"  turned still {tag} ({side or 'either side'}): a look found no face: {reason} "
                          f"(look {looks}, +{clock() - t0:.2f}s)", flush=True)
                blind += 1
                if blind >= TURN_BLIND_LOOKS and not hinted:
                    hinted = True
                    _turn_hint(TURN_BLIND_LINE)
                    _correct(site, TURN_BLIND_LINE, wait=False)   # round 38: her film too, not waited on
            if got.get("ok") and got.get("yaw") is not None:
                blind = 0
                if hinted:
                    hinted = False
                    _turn_hint("")
                look = {"yaw": float(got["yaw"]), "bin": got.get("bin"), "det": float(got.get("det") or 0.0),
                        "at": round(clock() - t0, 2)}
                rank = turn_rank(look["yaw"], side, look["det"], front)
                if best is None or rank > best[0]:
                    best = (rank, jpeg, look)
                if rank[0]:
                    why = "turned"
                    break
        now = clock()
        if now >= longest or (now >= deadline and best is not None):
            break                             # a face seen by TURN_WAIT_S: the best of it; none yet: look on
        sleep(TURN_LOOK_GAP_S)
    if hinted:
        _turn_hint("")
    jpeg = best[1] if best is not None else last
    look = best[2] if best is not None else {}
    if turn_check_on() and side and not (best is not None and best[0][0]):
        # round 34 (the User, .101 round 33: the left count banked its look from +0.49 s, before the head moved): a
        # turned still is one taken AFTER the head moved - past the bar toward the side asked and away from their own
        # front - never the best look of the time; the count's check asks again
        print(f"  turned still {tag} ({side}): not stored - no look turned to the {side} "
              f"(best yaw {look.get('yaw')} at +{look.get('at')}s, own front {front}, {looks} looks)", flush=True)
        return {"stored": False, "looks": looks, "why": "not turned", "look": look}
    if not jpeg or _cancelled():
        print(f"  turned still {tag} ({side or 'either side'}): no picture in {looks} looks"
              + (f" (the camera: {err})" if err else ""), flush=True)
        return {"stored": False, "looks": looks, "why": "no picture", "error": err}
    print(f"  turned still {tag} ({side or 'either side'}): {why} after {clock() - t0:.2f}s, {looks} looks, "
          f"yaw {look.get('yaw')} at +{look.get('at')}s (own front {front})", flush=True)
    stored: dict[str, Any] = {}
    for attempt in (1, 2):
        t_snap = clock()
        try:
            stored = _post(
                f"{PRESENCE}/v1/store_face",
                {"person_id": person_id, "image_b64": base64.b64encode(jpeg).decode(), "said_by": "voice-register",
                 "enroll": True, "pose": tag, "station": station, "note": note,
                 "match_front": bool(side) and turn_check_on()},     # round 35: a turned still must be his face
                timeout=60,
            )
        except Exception as e:  # noqa: BLE001 - presence not answering: no still, said in the log
            stored = {"ok": False, "error": repr(e)[:120]}
        print(f"  turned still {tag} store (+{clock() - t_snap:.2f}s) ok={stored.get('ok')} bin={stored.get('bin')} "
              f"yaw={stored.get('yaw')} det={stored.get('det')} front_sim={stored.get('front_sim')} "   # round 36
              f"err={stored.get('error')}", flush=True)
        if stored.get("ok") or stored.get("error") != "no_face" or not look or attempt == 2:
            break
        sleep(0.2)                            # the look saw a face in this very still: the engine missed it once
    if stored.get("ok"):
        coach_emit(type="face_ok", text="Face saved", yaw=stored.get("yaw"), bin=stored.get("bin"),
                   det=stored.get("det"), has_emb=True)
        if side and why == "turned":
            _TURNED_OK.add(side)                # a real turn to that side is banked (the face stage skips it)
    return {"stored": bool(stored.get("ok")), "looks": looks, "why": why, "look": look, "store": stored}


_TURNED_OK: set = set()


# THE TURN IS CHECKED AS ITS COUNT ENDS (round 33, the User 2026-10-19 on .101's round 32: "If turn left waits for the
# beep, insert check on voice and pic; if either failed then run [the] correct-it script. Then when 1-6 turn right ends
# successfully, then we know we can move on - there is no Left Right separate section"; "she says need a 1/4 turn not
# full profile until she gets the shot"). Each turned count is checked after its take: the voice (the quiet retry, as
# before) and a turned still banked to its side (_TURNED_OK). A side not banked gets her correct-it line (TURN_QUARTER_LINE)
# and that count again, up to TURN_CHECK_TRIES times, before the next count. The turn cues ask for a quarter turn
# (quarter_words; Lila's filmed turn lines on the one page keep their own words). While a turned count's looks find no
# face she says so on the page (TURN_BLIND_LINE), and each reason a look found no face is logged once.
# Round 34 (the User, .101 round 33: "delete the entire Face photos section"): there is no Face photos section at all
# on the one page. Each count is checked until it passes, up to turn_check_tries() (3), then "I'll take the rest
# later." (TURN_LATER_LINE: a camera that cannot see never traps him; Cancel is there too). The front is checked the
# same way after the last facing count (_front_check).
# A pack only. Kill: CRISSA_TURN_CHECK=0 (round 32: the counts as they come, then Your face's two turns).
TURN_QUARTER_LINE = "Just a quarter turn. I need to see your face."
TURN_BLIND_LINE = "I can't see your face. Turn back a little."
TURN_LATER_LINE = "I'll take the rest later."           # round 34: the tries are spent, she moves on
TURN_FRONT_LINE = "Look straight at the camera once more."   # round 34: the front, checked like the turns
# LILA'S CORRECTION LINES AS HER FILMS (round 38, .101 round 37, the User: her correction line was Pocket's eve, "wrong
# voice", louder than Crissa; "Lila's correction lines as short Lila FILMS, like count-again / vid-left, not TTS"): on
# the one page each line is her own film (config/setup-films.json), the rest as before. Kill
# CRISSA_LILA_CORRECTION_FILMS=0.
CORRECTION_FILMS = {TURN_QUARTER_LINE: "turn-quarter", TURN_BLIND_LINE: "turn-back",
                    TURN_FRONT_LINE: "look-once-more", TURN_LATER_LINE: "rest-later"}


def _correction_films_on() -> bool:
    return os.environ.get("CRISSA_LILA_CORRECTION_FILMS", "1").strip().lower() not in ("0", "off", "false", "no")


def _correct(site: Site, line: str, wait: bool = True) -> None:
    """One of her correction lines: her film on the one page, else said as before."""
    fid = CORRECTION_FILMS.get(line)
    if fid and _correction_films_on() and films_on() and _film(fid, wait=wait):
        return
    if wait:
        _announce(site, line)
TURN_BLIND_LOOKS = 8                       # ~2 s of looks with a picture and no face
TURN_CHECK_WAIT_S = 3.0                    # the count's looks may run on this long after its take


def turn_check_on() -> bool:
    if os.environ.get("CRISSA_TURN_CHECK", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return not _house_tree()


def turn_check_tries() -> int:
    try:
        return max(0, min(5, int(os.environ.get("CRISSA_TURN_CHECK_TRIES", "3") or 0)))
    except ValueError:
        return 3


def quarter_words(words: str) -> str:
    """A turn cue asks for a quarter turn (a full profile hides the face from the camera)."""
    if not turn_check_on():
        return words
    return (words or "").replace("a little to your left", "a quarter turn to your left").replace(
        "a little to your right", "a quarter turn to your right")


def _turn_hint(text: str) -> None:
    """The count's plate again with `text` as its line (no new beep: the bar keeps running)."""
    p = dict(_LAST_TURN_GLASS)
    if not p:
        return
    p.pop("beep", None)
    p["line"] = text
    _glass("turn", str(p.pop("text", "") or ""), **p)


def _turn_wait(th) -> None:
    """The count's looks have their last say (TURN_CHECK_WAIT_S), then stop; its still is stored before the check."""
    if th is None:
        return
    th.join(timeout=TURN_CHECK_WAIT_S)
    if th.is_alive():
        _TURN_GEN["n"] += 1
        th.join(timeout=TURN_WAIT_S + 8.0)


def count_profiles_on() -> bool:
    """ROUND 23 (the User 2026-10-15, .101: "let's at least try to get the profiles during speech, then we can say that
    section to start but then she notices complete and plays that clip in our library"): on the one page the turned
    counts take the face profiles again (round 21's way), and Your face plays `already` when both sides are in. Kill:
    CRISSA_ONE_PAGE_COUNT_PROFILES=0 (round 22: the face photos are their own stage only)."""
    return os.environ.get("CRISSA_ONE_PAGE_COUNT_PROFILES", "1").strip().lower() not in ("0", "off", "false", "no")


def face_shutter_on() -> bool:
    """ROUND 23 (the User 2026-10-15: "when in photos, let it beep when it takes one so user knows next one is
    coming"): the face stage plays the take-in ding as each photo is stored. Kill: CRISSA_FACE_SHUTTER=0."""
    return os.environ.get("CRISSA_FACE_SHUTTER", "1").strip().lower() not in ("0", "off", "false", "no")


def _prearm_read() -> dict[str, Any]:
    """A fresh pre-arm marker (the coach parked the camera before the press)."""
    try:
        d = json.loads(PREARM_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and time.time() - float(d.get("ts") or 0) < PREARM_MAX_AGE_SEC:
            return d
    except Exception:
        pass
    return {}


TOOL_HOLD_FILE = Path(os.environ.get("HOUSE_TOOL_HOLD", "/tmp/house-tool-hold.json"))
KEEP_HOLD_OWNERS = ("init_wizard",)


def _hold_owner() -> str:
    try:
        d = json.loads(TOOL_HOLD_FILE.read_text(encoding="utf-8"))
        if float(d.get("until") or 0) > time.time():
            return str(d.get("owner") or "")
    except Exception:
        pass
    return ""


def _hold_take() -> bool:
    """The tool hold, unless the wizard already holds the room: inside New
    User Init the walker's lease (owner init_wizard) stays, so the ear comes
    back on the wizard's terms, not ours. True = ours to release."""
    if _hold_owner() in KEEP_HOLD_OWNERS:
        print(f"  tool hold kept by {_hold_owner()} — not taking it", flush=True)
        return False
    _tool_hold_acquire(reason="voice-register", owner="voice_register", sec=600.0)
    return True


from . import register_camera  # noqa: E402  (Init step 4: Register through presence's token, OFF by default)


def _deny_register(text: str) -> None:
    """The User-mode deny (Init step 4): on the glass and the coach like any abort, and spoken by the announcer, so the
    User hears why Register did not begin (the User 2026-10-03)."""
    _fail_register(text)
    try:
        # one sentence = one Audio8 piece: two pieces were synthesised in turn before anything played (~6 s, 10-03)
        cues.speak_announcer(text.split(". ")[0].rstrip(".") + ".")
    except Exception as e:  # noqa: BLE001 - the glass already carries it
        print(f"  deny speak: {e!r}", flush=True)


def _user_mode_gate(site: Site | None = None, person_id: str = "") -> dict[str, Any]:
    """Register starts only in User mode (Init step 4): already authenticated = go, no second check; otherwise
    refuse and say why. Never a FaceFinder look to authenticate. A house with no User enrolled yet (first boot) has
    nobody to sign in as, so its first Register goes (the User 2026-10-03). A visitor's own take (ADD A VISITOR,
    round 20) goes outside Security On when the coach marked it and the player vouches for exactly this person."""
    if site is not None and not register_camera.user_enrolled(site.root):
        return {"ok": True, "via": "first_user"}
    if register_camera.user_mode(PRESENCE):
        return {"ok": True, "via": "user_mode"}
    if _visitor_take(person_id):
        return {"ok": True, "via": "visitor"}
    return {"ok": False, "error": register_camera.REFUSE_TEXT}


def _visitor_take(person_id: str) -> str:
    """The visitor's id when this process runs ADD A VISITOR's take for exactly them and the player vouches, else ''."""
    vis = os.environ.get(register_camera.VISITOR_ENV, "").strip().lower()
    pid = str(person_id or "").strip().lower()
    if vis and pid and vis == pid and register_camera.visitor_vouched(PLAYER_INIT, pid):
        return pid
    return ""


def _drop_secure() -> dict[str, Any]:
    """The old way, while HOUSE_REGISTER_TOKEN is off (secure_legacy); with it on Register never touches Secure."""
    from .secure_legacy import drop_secure

    return drop_secure(PRESENCE, _post)


def prearm(site: Site, *, station: str = "desk", write_marker: bool = True) -> dict[str, Any]:
    """Everything that can happen BEFORE the press: the tool hold (one
    owner), the ear closed, the camera paused + leased + aimed at the desk
    with FaceFinder on, the listen loop cued, the host audio warm. Callable
    from the coach's /prearm (writes the marker the routine adopts) and from
    the routine itself when no marker is fresh. Returns {lease_id, loop_prev}."""
    out: dict[str, Any] = {"ts": time.time(), "station": station}
    out["hold_ours"] = _hold_take()
    release_ear_for_enroll()
    lease_id = None
    if _ports.camera_off():
        # NO CAMERA (CRISSA_NO_CAMERA=1, 2026-10-10): Register records the voice; no token, no lease, no aim, no still
        out["voice_only"] = True
        _ports.say_once("Register records the voice only")
    elif register_camera.on():
        # presence's register token: Secure is never touched, the door watch yields to it, giving it back homes
        tok = register_camera.take(PRESENCE, _post, why="register",
                                   visitor=os.environ.get(register_camera.VISITOR_ENV, "").strip().lower())
        out["token"] = bool(tok.get("ok"))
        print(f"  register token → {tok}", flush=True)
        if tok.get("ok"):
            lease_id = tok.get("lease_id")
        else:
            out["refused"] = str(tok.get("error") or "refused")
            out["refused_text"] = register_camera.refusal_text(tok)     # what the plate says, true to the refusal
    else:
        out["secure"] = _drop_secure()
        try:
            _pause_watchdogs(ttl_sec=180.0)
        except Exception as e:
            print(f"  pause watchdogs failed: {e!r}", flush=True)
        try:
            lease = _post(f"{CAMERA}/v1/lease", {"owner": "voice-register", "ttl_sec": 180})
            lease_id = lease.get("lease_id") if lease.get("ok") is not False else None
            print(f"  camera lease → {lease}", flush=True)
        except Exception as e:
            print(f"  camera lease failed: {e!r}", flush=True)
    # THE DOOR WATCH POLLS THE GRABBER ON A CACHE. 15:16:52 today: our aim
    # to the desk (pan 0) and presence's door mill homing the camera landed
    # in the same second — it had not yet seen the pause and the lease, and
    # its return_door reset the grabber's cmd_pan so pan 0 read as a
    # factory reset; every still was of the door (no_face ×16). A beat
    # first, then verify the pan after the aim and re-assert it.
    if (lease_id or not register_camera.on()) and not out.get("voice_only"):
        time.sleep(DOOR_WATCH_SETTLE_SEC)
        _aim_verified(site, station, lease_id)
        _ff_set(lease_id, True)      # FaceFinder frames the registree for the take, never authenticates
    out["lease_id"] = lease_id
    # NO LILA ON THE GLASS FOR A SPOKEN ROUTINE (the User 2026-10-12, .101 Voice ID again: "Lila showed but wrong voice
    # and lips did not paint" - her listen loop stood there while the announcer spoke): the announcer is an off-screen
    # voice, so Crissa stays on her own loop, nothing is cued, and nothing has to be left at the end (loop_entered).
    verbal_now = routine() == "verbal"
    out["loop_entered"] = not verbal_now
    out["loop_prev"] = None if verbal_now else _loop_enter_host()
    out["host"] = {} if verbal_now else _host_warm()
    if write_marker:
        try:
            PREARM_FILE.write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass
    return out


DOOR_WATCH_SETTLE_SEC = 1.2
AIM_TRIES = 3


def _camera_pan() -> int | None:
    try:
        import urllib.request as _ur

        with _ur.urlopen(f"{CAMERA}/v1/health", timeout=3) as r:
            d = json.loads(r.read().decode() or "{}")
        v = d.get("pan_actual")
        return int(v) if v is not None else None
    except Exception:
        return None


def _aim_verified(site: Site, station: str, lease_id: str | None) -> bool:
    """_aim, then read the camera's pan back and re-assert until it is on
    the station (face_snap._pan_near, 12000). True = on station."""
    from .face_snap import _pan_near

    pan, _tilt = station_pt(site, station)
    for i in range(AIM_TRIES):
        _aim(site, station, lease_id)
        actual = _camera_pan()
        if _pan_near(actual, pan):
            if i:
                print(f"  aim {station} held on try {i + 1} (pan {actual})", flush=True)
            return True
        print(f"  aim {station} yanked (pan {actual}, want {pan}) — re-asserting", flush=True)
        time.sleep(0.6)
    print(f"  aim {station} NOT held after {AIM_TRIES} tries", flush=True)
    return False


def cancel_release_budget_sec() -> float:
    """Round 46 (2026-10-23): how long the coach's Cancel waits after SIGTERM before SIGKILL. SIGTERM raises out of the
    take into this routine's finally, which gives back, in order: the loop (two player POSTs), FaceFinder off, the
    camera (presence's token give_back, else the lease release and the watchdogs' resume), the ear, then the hold. A kill
    inside that chain leaves the call it was in undone (the old 0.7 s killed it inside the first one), so the wait is the
    chain's own bound: the sum of each call's timeout, the point where the routine itself would have given up. On a
    healthy machine the routine is gone in a second or two and the wait ends there; the coach repeats every give-back
    after the kill anyway. Dial: CRISSA_ENROLL_CANCEL_GRACE_S."""
    from . import voice_session as _vs
    camera = max(float(register_camera.GIVE_BACK_TIMEOUT_SEC), LEASE_RELEASE_TIMEOUT_SEC + RESUME_TIMEOUT_SEC)
    return (2 * CONDUCTOR_TIMEOUT_SEC + FF_TIMEOUT_SEC + camera
            + float(_vs.EAR_REOPEN_TIMEOUT_SEC) + float(_vs.HOLD_RELEASE_TIMEOUT_SEC))


def prearm_adopt_on() -> bool:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): the coach's pre-arm sleeps PREARM_MAX_AGE_SEC and
    then expires the marker; the routine keeps the marker until its own finally, so a Begin pressed inside those 90 s
    had the camera, the ear and the hold handed back MID-TAKE. The routine now stamps the marker with its pid when it
    adopts it, and an expiry leaves an adopted marker alone while that pid lives (the routine gives it all back in its
    finally; a routine that died without one is expired as before). Kill: CRISSA_PREARM_ADOPT=0."""
    return os.environ.get("CRISSA_PREARM_ADOPT", "1").strip().lower() not in ("0", "false", "no", "off")


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prearm_adopt(arm: dict[str, Any]) -> dict[str, Any]:
    """Stamp the marker as this routine's (adopted_pid): the coach's timer no longer expires it under the take."""
    if not prearm_adopt_on() or not arm:
        return arm
    out = dict(arm, adopted_pid=os.getpid(), adopted_ts=time.time())
    try:
        tmp = PREARM_FILE.with_name(PREARM_FILE.name + ".%d.tmp" % os.getpid())
        tmp.write_text(json.dumps(out), encoding="utf-8")
        os.replace(tmp, PREARM_FILE)
    except OSError as e:
        print(f"  pre-arm adopt: {e!r}", flush=True)
    return out


def prearm_adopted(arm: dict[str, Any] | None = None) -> bool:
    """A routine that is still running holds this marker (adopted_pid alive)."""
    if not prearm_adopt_on():
        return False
    if arm is None:
        try:
            arm = json.loads(PREARM_FILE.read_text(encoding="utf-8"))
        except Exception:
            return False
    return isinstance(arm, dict) and _pid_alive(arm.get("adopted_pid"))


def prearm_expire(*, force: bool = False) -> dict[str, Any]:
    """A pre-arm nobody adopted (no press within PREARM_MAX_AGE_SEC): give
    everything back — the camera, the door watch, the ear, the hold — so a
    parked camera and a closed ear never outlive the routine they were for.
    force (round 46): the routine's own give-back, or the coach's Cancel after the routine is dead."""
    arm = {}
    try:
        arm = json.loads(PREARM_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": True, "expired": False, "reason": "no marker"}
    if not force and prearm_adopted(arm if isinstance(arm, dict) else {}):
        return {"ok": True, "expired": False, "reason": "adopted", "pid": arm.get("adopted_pid")}
    try:
        PREARM_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    out: dict[str, Any] = {"ok": True, "expired": True}
    lease_id = arm.get("lease_id")
    if arm.get("loop_entered", True):        # a spoken routine cued no loop: nothing to leave (2026-10-12)
        _loop_leave(arm.get("loop_prev") if isinstance(arm.get("loop_prev"), str) else None)
    if lease_id and arm.get("token"):
        try:
            _ff_set(lease_id, False)
            out["camera"] = register_camera.give_back(PRESENCE, _post)
        except Exception as e:
            out["camera"] = repr(e)[:120]
        lease_id = None
    if lease_id:
        try:
            _ff_set(lease_id, False)
            site = None
            try:
                from .site import load_site

                site = load_site()
            except Exception:
                pass
            if site is not None:
                go_home(site, lease_id)
            _post(f"{CAMERA}/v1/lease/release", {"lease_id": lease_id}, timeout=LEASE_RELEASE_TIMEOUT_SEC)
        except Exception as e:
            out["camera"] = repr(e)[:120]
    try:
        _resume_watchdogs()
    except Exception:
        pass
    if arm.get("hold_ours", True):
        reopen_ear_after_enroll()
        _tool_hold_release(owner="voice_register")
    print("  pre-arm expired — camera, door watch, ear and hold given back", flush=True)
    return out


# ── THE VERBAL ROUTINE ──────────────────────────────────────────────────────
# VOICE ID IS SPOKEN, NOT READ (the User 2026-10-12, .101 round 10: the takes ran with no sentence on show - her window
# never came in front of the full-screen Wall, the two new beats had no Lila clip, and the end had no way out: "Look at
# the camera and count to 6 like this: 'One, two...' at the right pace and user will just copy perfectly. Verbal alone
# would be fine there"; then "just use your best judgement and make it work"). Every take is a count of six that the
# announcer says first, at the pace to copy; the same beep opens and closes it; a quiet take is asked again out loud
# once; the end is said ("Thanks, that's done."). Nothing waits on a window, a page or a clip: the lines are the kit's
# shipped announcer (lib/crissa_tts.synth(text, "announcer"): Audio8 anna, else Pocket anna, then the tier's chain -
# stock VCTK voices, CC BY 4.0, fetched by sha256; no clone), rendered once and cached on disk
# (<state home>/cache/id/announcer/), played here with the beep BEEP_GAP_S after the last word. Where nothing can
# render, the player says the line (/v1/speak, voice announcer); with no speakers (cues.text_only) the words are on
# the glass alone. The glass shows the count and "At the beep, count aloud", never a sentence to read.
# Which routine runs: CRISSA_VOICE_ID_ROUTINE=verbal|video, else <state home>/voice-id.json {"routine": ...}, else
# verbal in a pack (every entry: Setup's Voice ID, Voice ID again, the Control Panel) and video on the house. "video"
# is the 2026-10-11 routine (Lila's clips and the sentence on the glass), byte for byte.
ROUTINE_ENV = "CRISSA_VOICE_ID_ROUTINE"
ROUTINE_FILE = "voice-id.json"
V_COUNT = "One, two, three, four, five, six."
V_FIRST = "Let's learn your voice. Look at the camera, and count to six, like this:"
V_FIRST_AFTER = "Start at the beep."
V_TAKES = (   # (take id, pose, angle, away, the words before the beep) - the count facing and turned away, twice
    ("seat_count_on", "front", "on", False, ""),
    ("seat_off", "sweep", "off", True, "Now turn your head a little to your left, and count to six again. "
                                      "Start at the beep."),
    ("seat_count_on_2", "front", "on", False, "Look at the camera again, and count to six once more. Start at the beep."),
    ("seat_off_2", "sweep", "off", True, "Last one. Turn your head a little to your right, and count to six. "
                                        "Start at the beep."),
)
# NO DEVICE IN A CUE THAT MAY NOT BE THERE (the User 2026-10-12, .101: "turn away from the microphone" on a camera with
# no microphone): a turn is "turn your head a little away", never from a device; with no camera (CRISSA_NO_CAMERA)
# "look at the camera" is "face forward". _cue() says each line as this machine has it.
NO_CAMERA_WORDS = (("Keep your head where the camera can see it, and count", "Count"),
                   ("still facing the camera", "still facing forward"),
                   ("Look at the camera again", "Face forward again"), ("Look at the camera", "Face forward"))
# LILA'S OWN SCRIPT, LEFT THEN RIGHT (round 20, the User 2026-10-14: "it would depend on turn your head left to come
# first. Your script for whatever reason sounds like it does a lookup before she says left or right"; "you are writing
# that script so yes i think the order needs to match that clip"; "add a sentence to keep your head in camera fov when
# you turn"). Her filmed Register script - lila-reg-okay "Okay. ... Start at the beep", lila-reg-turn "Now turn away
# from the screen and count to six", lila-reg-thanks "That's it. Thanks!" - gives the routine its words and its order:
# both counts facing the camera first, then the two turns back to back, left first as her turn film turns (yaw -35 to
# -38 degrees toward the viewer's left at 2.5-4.75 s, back to front by 7 s), then right, so no "look at the camera" sits
# between them. Each turn asks to keep the head where the camera can see it. The front stills stay on the facing counts
# (now 1 and 2); a turned still's side still comes from its words (turn_side). It names no one, so the same cues serve
# any person the routine is started for. Kill: CRISSA_VOICE_ID_SCRIPT=r19 (front, left, front, right, round 19's words).
V_FIRST_R20 = "Okay. Look at the camera, and count to six, like this:"
V_TAKES_R20 = (
    ("seat_count_on", "front", "on", False, ""),
    ("seat_count_on_2", "front", "on", False, "Once more, still facing the camera. Count to six at the beep."),
    ("seat_off", "sweep", "off", True, "Now turn away from the screen, a little to your left. Keep your head where the "
                                       "camera can see it, and count to six at the beep."),
    ("seat_off_2", "sweep", "off", True, "And now a little to your right. Keep your head where the camera can see it, "
                                         "and count to six at the beep."),
)
V_THANKS_R20 = "That's it. Thanks!"


def script_r20(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_VOICE_ID_SCRIPT", "") or "").strip().lower() != "r19"


def script() -> tuple:
    """(the first cue, the takes, the thanks) the routine says now."""
    return (V_FIRST_R20, V_TAKES_R20, V_THANKS_R20) if script_r20() else (V_FIRST, V_TAKES, V_THANKS)


def plan_takes(four: bool, only: str = "") -> list:
    """The counts to take: all four, or (two, or one kind again) the first facing count and the first turn."""
    _first, takes, _thanks = script()
    if four and not only:
        return list(takes)
    turn = next((t for t in takes if t[3]), None)
    return [takes[0]] + ([turn] if turn else [])


def _camera_off() -> bool:
    for mod in ("_paths", "_ports"):
        m = globals().get(mod)
        fn = getattr(m, "camera_off", None) if m is not None else None
        if callable(fn):
            try:
                return bool(fn())
            except Exception:  # noqa: BLE001
                return False
    return False


def _cue(text: str) -> str:
    text = quarter_words(text)                  # round 33: a turn cue asks for a quarter turn
    if text and _camera_off():
        for a, b in NO_CAMERA_WORDS:
            text = text.replace(a, b)
    return text
V_REDO = "I didn't hear that. Once more: count to six, at the beep."
V_THANKS = "Thanks, that's done."
V_WINDOW_MIN_S = 4.0
V_WINDOW_MAX_S = 9.0
# A TAKE WITH NO VOICE IN IT (.101 03:06: take _3 banked at peak 286 of 32767 with "94 % voiced", the room's own hiss)
# is quiet whatever its voiced share: under MIN_PEAK it is asked again. Measured takes of a real count: peak 1494-1538
# on .101's Shure, 10631 on a read. CRISSA_ENROLL_MIN_PEAK sets it; 0 = off.
V_MIN_PEAK = int(float(os.environ.get("CRISSA_ENROLL_MIN_PEAK", "600") or 0))
_V_PCM: dict[str, tuple[bytes, int, float]] = {}       # line -> (pcm cut after the last word, sr, seconds kept after it)
_V_LOCK = threading.Lock()


def routine(env: dict | None = None) -> str:
    """'verbal' or 'video' (see THE VERBAL ROUTINE)."""
    e = os.environ if env is None else env
    v = str(e.get(ROUTINE_ENV) or "").strip().lower()
    if v in ("verbal", "video"):
        return v
    home = cues._state_home()
    if home is not None:
        try:
            got = str((json.loads((Path(home) / ROUTINE_FILE).read_text(encoding="utf-8")) or {}).get("routine") or "")
            if got.strip().lower() in ("verbal", "video"):
                return got.strip().lower()
        except (OSError, ValueError, AttributeError):
            pass
    try:
        return "video" if _ppaths.is_house() else "verbal"
    except Exception:  # noqa: BLE001  a tree that cannot tell: the house's
        return "video"


def verbal_lines() -> list[str]:
    """Every line the verbal routine says, in the order it says them (what verbal_warm renders)."""
    first, takes, thanks = script()
    return [_cue(t) for t in [first, V_COUNT, V_FIRST_AFTER] + [t[4] for t in takes if t[4]] + [V_REDO, thanks]]


def _ann_lib():
    lib = rails_path("lib")
    import sys as _sys
    if lib.is_dir() and str(lib) not in _sys.path:
        _sys.path.insert(0, str(lib))
    import crissa_tts  # noqa: PLC0415  the pack's own voices (the tier table's announcer)
    return crissa_tts


# ONE VOICE FOR SETUP (the User 2026-10-13, .101 round 15: "the second voice needs to match" - Lila's lines spoke as
# eve and the Voice ID cues as the announcer anna while Lila was the face on screen). In Setup's first run the coach
# hands its child CRISSA_SETUP_CUE_VOICE=lila, and every cue - the spoken counts here, the face photos' words - speaks
# in Lila's voice from the tiers (her own Grok Imagine clip on a voice that clones it, else her one stock voice: eve on
# Pocket). Voice ID again and the Control Panel's Register (Crissa on screen, no Lila) keep the announcer. Kill:
# CRISSA_SETUP_ONE_VOICE=0 (the announcer everywhere, as before).
def cue_voice(env: dict | None = None) -> str:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_SETUP_ONE_VOICE", "1")).strip().lower() in ("0", "off", "false", "no"):
        return "announcer"
    return str(e.get("CRISSA_SETUP_CUE_VOICE") or "").strip().lower() or "announcer"


def _ann_cache_path(text: str, speaker: str = "announcer") -> Path | None:
    home = cues._state_home()
    if home is None:
        return None
    try:
        tts = _ann_lib()
        cfg = tts.config()
        sig = json.dumps({k: cfg.get(k) for k in ("engine", "announcer", "announcer_chain", "voices")}, sort_keys=True,
                         default=str) + "|" + speaker
    except Exception:  # noqa: BLE001
        sig = "|" + speaker
    import hashlib
    h = hashlib.sha256(("%s\n%s" % (sig, text)).encode()).hexdigest()[:24]
    return Path(home) / "cache" / "id" / "announcer" / ("%s.wav" % h)


def _ann_render(text: str, speaker: str | None = None) -> tuple[bytes, int, float] | None:
    """The cue voice's line (cue_voice(): the announcer, or Lila's in Setup) as PCM cut after its last word: memory,
    else the disk cache, else rendered (and cached)."""
    speaker = speaker or cue_voice()
    key = text if speaker == "announcer" else "%s|%s" % (speaker, text)
    with _V_LOCK:
        got = _V_PCM.get(key)
    if got:
        return got
    import wave
    path = _ann_cache_path(text, speaker)
    pcm, sr = b"", 0
    if path is not None and path.is_file():
        try:
            with wave.open(str(path), "rb") as w:
                sr, pcm = w.getframerate(), w.readframes(w.getnframes())
        except (OSError, wave.Error, EOFError):
            pcm, sr = b"", 0
    if not pcm:
        try:
            pcm, sr = _ann_lib().synth(text, speaker, timeout=90.0)
        except Exception as e:  # noqa: BLE001  no voice here: the caller falls back
            print(f"  announcer render failed: {type(e).__name__}: {str(e)[:120]}", flush=True)
            return None
        if path is not None and pcm:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".part")
                with wave.open(str(tmp), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(int(sr))
                    w.writeframes(pcm)
                os.replace(tmp, path)
            except OSError:
                pass
    if not pcm or not sr:
        return None
    cut, kept = cues.trim_tail(pcm, int(sr))
    out = (cut, int(sr), float(kept))
    with _V_LOCK:
        _V_PCM[key] = out
    return out


def verbal_warm(lines: list[str] | None = None, speaker: str | None = None) -> dict[str, bool]:
    """Render (or load) every verbal line ahead of the routine; {line: ready}. Safe to call on any thread. speaker (round
    20): the voice that will say them (Init's /warm names Lila's), else the cue voice of this process."""
    out = {}
    for t in (lines or verbal_lines()):
        if _cancelled():
            break
        out[t] = _ann_render(t, speaker) is not None
    return out


def _player_say(text: str) -> bool:
    """The last voice: the player says the line as the announcer (/v1/speak); waits about its length."""
    try:
        body = json.dumps({"text": text[:400], "voice": cue_voice(), "by": "voice id"}).encode()
        req = urllib.request.Request(CONDUCTOR + "/v1/speak", data=body, headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001
        print(f"  player say: {e!r}", flush=True)
        return False
    if ok:
        deadline = time.time() + min(20.0, 1.2 + 0.065 * len(text))
        while time.time() < deadline and not _cancelled():
            time.sleep(0.1)
        cues.mark_spoken(0.0)
    return ok


# LILA'S LIPS ON HER CUES (round 18, the User 2026-10-13, .101 round 17: "lila on screen no lips moving"; lib/crissa_init
# LILA'S LIPS ON HER LINES). In Setup's first run the cues are Lila's (cue_voice), and Lila is the face on the glass: the
# lines of one beat are rendered here as before, joined (gap_s between them) and said by the player as ONE line with
# the rendered audio (mouth_prints.say_through_player: /v1/speak with `wav`), so her mouth moves on them; the call
# returns when the player has said it and stamps its end, so the GO beep still follows her last word. A player that
# does not take it (an older one, no ffmpeg) or a line not rendered: played here, as before. The announcer's cues
# (Voice ID again, the Control Panel) and the house: as before. Kill: CRISSA_LILA_LIPS=0.
def lips_on_player() -> bool:
    if cue_voice() != "lila":
        return False
    try:
        from .mouth_prints import _is_house
        if _is_house():
            return False
    except Exception:  # noqa: BLE001
        return False
    return os.environ.get("CRISSA_LILA_LIPS", "1").strip().lower() not in ("0", "off", "false", "no")


def _announce_on_player(site: Site, texts: list, gap_s: float) -> float | None:
    """The beat's lines as one line through the player; the LAST line's seconds, or None when it was not said there."""
    from .mouth_prints import lila_cue_player_voice_on, say_text_through_player
    if lila_cue_player_voice_on():
        # round 37: the player renders her cue in the voice and level of her page lines (mouth_prints says why)
        for text in texts:
            print(f"  🔊 {text}", flush=True)
        got = say_text_through_player(" ".join(texts), cue_voice(), by="voice id", site=site)
        if got is not None:
            return got
    parts = []
    for text in texts:
        got = _ann_render(text)
        if got is None or (parts and got[1] != parts[0][1]):
            return None
        parts.append(got)
    if not parts:
        return None
    sr = parts[0][1]
    gap = b"\x00\x00" * int(sr * max(0.0, gap_s))
    pcm = gap.join(p[0] for p in parts)
    for text in texts:
        print(f"  🔊 {text}", flush=True)
    from .mouth_prints import say_through_player
    if not say_through_player(" ".join(texts), cue_voice(), pcm, sr, kept=parts[-1][2], by="voice id", site=site):
        return None
    return max(0.0, len(parts[-1][0]) / 2.0 / sr - parts[-1][2])


def _announce(site: Site, *texts: str, gap_s: float = 0.18) -> float:
    """Say the lines, one after the other, as the announcer (see THE VERBAL ROUTINE); returns the seconds of the LAST
    line's audio (the demo count's length is the pace to copy), 0.0 when it was not rendered."""
    if lips_on_player() and site.speak_enabled and not cues.text_only() and not _cancelled():
        got = _announce_on_player(site, [t for t in texts if t], gap_s)
        if got is not None:
            return got
    last = 0.0
    for i, text in enumerate(t for t in texts if t):
        if _cancelled():
            return last
        print(f"  🔊 {text}", flush=True)
        if cues.text_only() or not site.speak_enabled:
            time.sleep(min(8.0, 0.6 + 0.06 * len(text)))
            cues.mark_spoken(0.0)
            continue
        got = _ann_render(text)
        if got is None:
            _player_say(text) or time.sleep(min(8.0, 0.6 + 0.06 * len(text)))
            last = 0.0
            continue
        pcm, sr, kept = got
        if i:
            time.sleep(gap_s)
        cues._paplay_pcm(pcm, sr, site.sink, spoken_tail=kept, speaker=cue_voice())
        last = max(0.0, len(pcm) / 2.0 / sr - kept)
    return last


# LILA'S FILMS ON THE ONE PAGE (round 22, the User 2026-10-15). In a pack's one-page Setup (lib/crissa_init ONE-PAGE
# SETUP, read once from GET /v1/init) Lila's words here are her own filmed lines, played by the Setup page: the routine
# CUES each one on the player (/v1/action setup_film) and waits until the page says her last word is out (/v1/register
# `film.said`), capped at the film's length + FILM_WAIT_SLACK_S, then beeps as after a spoken cue. Counts 1 and 2 have
# one film ("Twice facing the screen"), 3 and 4 one each; a quiet count gets "That one was a little quiet"; nothing is
# said after the last count. The face photos are SEPARATE from the counts (fewer retakes): no turned stills while
# counting, then the routine's last stage - her "Two quick photos" film, a turn to the left and one to the right, each
# taken by itself once the head has turned (_snap_turned), with "A little further" once if a side is not taken after
# FACE_FURTHER_S. Anything else (Voice ID again, the house, a card walk): as before. Kill: CRISSA_SETUP_ONE_PAGE=0 (Init).
FILM_WAIT_SLACK_S = 6.0
FACE_FURTHER_S = float(os.environ.get("CRISSA_FACE_FURTHER_S", "5.0") or 0)
_FILMS: dict[str, Any] = {}


def films_on() -> bool:
    """A one-page Setup is running on this player (asked once per routine)."""
    if "on" not in _FILMS:
        on = False
        try:
            with urllib.request.urlopen(f"{CONDUCTOR}/v1/init", timeout=3) as r:
                snap = json.loads(r.read().decode() or "{}")
            on = bool(snap.get("one_page")) and str(snap.get("status") or "") == "running"
        except Exception:  # noqa: BLE001 - no Init here: the spoken cues
            on = False
        _FILMS["on"] = on
        if on:
            print("  one-page Setup: Lila's films on the page carry the cues", flush=True)
    return bool(_FILMS["on"])


def _film_said() -> tuple[int, float]:
    """(the last cue the page said, how long the page's film on the air still runs)."""
    said, ahead, _ = _film_said_at()
    return said, ahead


def _film_said_at() -> tuple[int, float, float]:
    """(the last cue the page said, the page's film still on the air in s, when the page said it - epoch, 0 unknown)."""
    try:
        with urllib.request.urlopen(f"{CONDUCTOR}/v1/register", timeout=2) as r:
            f = (json.loads(r.read().decode() or "{}") or {}).get("film") or {}
        return int(f.get("said") or 0), float(f.get("ahead_s") or 0.0), float(f.get("said_at") or 0.0)
    except Exception:  # noqa: BLE001
        return 0, 0.0, 0.0


# THE BEEP KEEPS ITS GAP FROM HER LAST WORD (round 23, the User 2026-10-15, .101: "the and again had a lag between when
# she finished and when it beeped so I false started"; "make the final beep right on the end of bar. Late for first
# voice"): the page says her last word as it happens (said_at); the routine polled for it every 0.15 s and then started
# BEEP_GAP_S from the moment it noticed. It polls every FILM_POLL_S now and counts the gap from said_at. Kill:
# CRISSA_BEEP_FROM_WORD=0 (round 22: the gap from the notice).
FILM_POLL_S = 0.05


def _said_kept_s(said_at: float, now: float | None = None) -> float:
    if os.environ.get("CRISSA_BEEP_FROM_WORD", "1").strip().lower() in ("0", "off", "false", "no") or not said_at:
        return 0.0
    now = time.time() if now is None else now
    return max(0.0, min(2.0, now - float(said_at)))


# NO TAKE OVER HER OWN VOICE (round 45, .101 round 43 R43-1: "count 1/4 take 1: voiced 48% peak 806 - heard 0 number
# words ('Hi, I'm Lila. I'll set Chrissa up with y')"): the page plays its own lines too (her welcome, "already", "Take
# your time"), and one started under a take - the microphone heard her through the speakers. Before every GO beep the
# take waits while a film of the page's is still on the air (the page says each one as it starts: /v1/register
# film.ahead_s), at most that film's own length + FILM_WAIT_SLACK_S, and the beep then keeps its gap from her line's
# end. The page itself holds its own lines while a take runs (crissa-init.html opTick). Kill:
# CRISSA_TAKE_AFTER_HER_LINE=0 (round 44: the beep from the routine's own film alone).
def take_waits_her_line_on() -> bool:
    return os.environ.get("CRISSA_TAKE_AFTER_HER_LINE", "1").strip().lower() not in ("0", "off", "false", "no")


def _her_line_clear() -> float:
    """Wait while a film of the page's is still sounding; the seconds waited (0.0: nothing on the air)."""
    if not take_waits_her_line_on() or not films_on():
        return 0.0
    t0 = time.time()
    _, ahead, _ = _film_said_at()
    if ahead <= 0:
        return 0.0
    cap = t0 + ahead + FILM_WAIT_SLACK_S
    while ahead > 0 and not _cancelled() and time.time() < cap:
        time.sleep(FILM_POLL_S)
        _, ahead, _ = _film_said_at()
    waited = time.time() - t0
    cues.mark_spoken(0.0)                       # the beep keeps its gap from the end of her line
    print(f"  her line was still sounding: the take waited {waited:.1f} s for it to end", flush=True)
    return waited


def _beep_wait(lead_s: float) -> float:
    """cues.wait_for_beep, after any line of hers still on the air (NO TAKE OVER HER OWN VOICE)."""
    _her_line_clear()
    return cues.wait_for_beep(lead_s)


def _film(fid: str, wait: bool = True, about: str = "") -> bool:
    """Cue one of Lila's films on the page; with `wait`, until her last word is out (see LILA'S FILMS)."""
    if _cancelled():
        return False
    got = _post(f"{CONDUCTOR}/v1/action", {"action": "setup_film", "id": fid, "by": "voice id",
                                            **({"about": about} if about else {})}, timeout=3)
    if not got.get("ok"):
        print(f"  film {fid}: not cued ({got.get('error')})", flush=True)
        return False
    print(f"  🎬 Lila: {fid}", flush=True)
    if not wait:
        return True
    seq = int(got.get("seq") or 0)
    deadline = time.time() + float(got.get("seconds") or 6.0) + float(got.get("ahead_s") or 0.0) + FILM_WAIT_SLACK_S
    cap = deadline + 30.0                       # the page's own films ahead of it push the wait on, this far at most
    kept = 0.0
    while not _cancelled():
        said, ahead, said_at = _film_said_at()
        if said >= seq:
            kept = _said_kept_s(said_at)
            break
        now = time.time()
        if now >= cap or (now >= deadline and ahead <= 0):
            print(f"  film {fid}: the page did not say it was played; going on", flush=True)
            break
        time.sleep(FILM_POLL_S)
    cues.mark_spoken(kept)                      # the beep follows her last word as it follows a spoken cue
    if kept:
        print(f"  film {fid}: her last word {kept:.2f} s ago", flush=True)
    return True


def count_film(i: int, away: bool, words: str) -> str:
    """Her film before count i on the one page ('' = none: count 2 follows count 1 with no line)."""
    if i == 1:
        return "vid-facing"
    if away:
        return {"left": "vid-left", "right": "vid-right"}.get(turn_side(words), "vid-left")
    return ""


def _face_stage(site: Site, person_id: str, *, station: str) -> list[str]:
    """The one page's face photos (see LILA'S FILMS): the sides taken, in order."""
    if _cancelled() or _camera_off():
        return []
    _TURN_GEN["n"] += 1
    have = [x for x in ("left", "right") if x in _TURNED_OK] if count_profiles_on() else []
    if len(have) == 2:
        # both sides came in during the counts: Your face opens, she sees it is done and says so
        _glass("turn", "", stage="face", pip="loop", faces=list(have), takes=0)
        # .101 round 30: her own face intro's first sentence ("Two quick photos.") says what `already` is about
        _film("face-start", about="face")
        _film("already", about="face")
        print("  face: both sides taken during the counts; `already`", flush=True)
        return have
    _glass("turn", "", stage="face", pip="loop", faces=list(have), takes=0)
    _film("face")
    got: list[str] = list(have)
    further = False
    for side in [x for x in ("left", "right") if x not in have]:
        if _cancelled():
            break
        res: dict[str, Any] = {}
        th = threading.Thread(target=lambda side=side: res.update(_snap_turned(
            site, person_id, station=station, tag="profile", note="face %s" % side, side=side)),
            name=f"face-{side}", daemon=True)
        t0 = time.time()
        th.start()
        while th.is_alive():
            th.join(timeout=0.2)
            if not further and FACE_FURTHER_S > 0 and th.is_alive() and time.time() - t0 >= FACE_FURTHER_S:
                further = True                  # once: "A little further. There it is."
                _film("further", wait=False)
        if res.get("stored"):
            got.append(side)
            if face_shutter_on():
                cues.success_ding(site)
        _glass("turn", "", stage="face", pip="loop", faces=list(got), takes=0)
        print(f"  face {side}: {'taken' if res.get('stored') else 'not taken'} ({res.get('why')})", flush=True)
    return got


def verbal_window_s(demo_s: float) -> float:
    """The take's length: the demo count at the pace it was said, a quarter longer for a calmer copy, and 1.5 s to react
    to the beep; between V_WINDOW_MIN_S and V_WINDOW_MAX_S (4.0 s, the count's window since 2026-09-11, when unknown)."""
    if not demo_s or demo_s <= 0:
        return V_WINDOW_MIN_S + 1.0
    return round(min(V_WINDOW_MAX_S, max(V_WINDOW_MIN_S, demo_s * 1.25 + 1.5)), 1)


def take_is_quiet(wav: Path, min_active: float = COUNT_MIN_ACTIVE, min_peak: int | None = None) -> tuple[bool, float, int]:
    """(quiet, voiced share, peak) for a take just recorded: under the count's voiced bar, or under MIN_PEAK."""
    try:
        pk = int(peak_pcm(wav)) if wav.is_file() else 0
        active = float(voiced_fraction(wav)) if wav.is_file() else 0.0
    except Exception:  # noqa: BLE001
        return True, 0.0, 0
    floor = V_MIN_PEAK if min_peak is None else int(min_peak)
    return (active < float(min_active) or (floor > 0 and pk < floor)), active, pk


# A TAKE THAT IS NOT A COUNT (round 43, .101 round 42: three of ted's four kept takes were room noise - voiced 39-66 %,
# peak 2692-4180, mean -36..-41 dB - and the kit's own ASR heard "啊。", "啊。", "嗯。" in them; the gate was loudness only).
# A take is kept only when the ASR hears the count in it: COUNT_WORDS_MIN number words (digits or one..twenty). Measured
# 2026-10-23 on the house's five real count takes (the same /stt contract): 9, 10, 10, 10 and 6 number words heard; on
# .101's three silent takes: 0. The bar is half a count to six, under the shortest real count. A take that fails is asked
# again, COUNT_TRIES takes in all (2 = the routine's take and its one ask again, as before); after the last the count is
# kept as nothing and the run says it heard no counting. An ASR that does not answer is not a verdict: the take stands on
# its loudness, as before, and the log says so. A pack only (the house's routine is the video one).
# CRISSA_ENROLL_COUNT_WORDS_MIN / CRISSA_ENROLL_COUNT_TRIES set them; kill CRISSA_ENROLL_HEARS_COUNT=0 (loudness only,
# the last take banked whatever it was, round 42).
COUNT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
               "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
COUNT_NO_COUNT_SAID = "I didn't hear counting in that one, so nothing was kept for it."


def hears_count_on(env=None) -> bool:
    e = os.environ if env is None else env
    v = str(e.get("CRISSA_ENROLL_HEARS_COUNT") or "").strip().lower()
    if v:
        return v not in ("0", "off", "false", "no")
    try:
        return not _ppaths.is_house()
    except Exception:  # noqa: BLE001  cannot tell: a pack's rule
        return True


def count_words_min(env=None) -> int:
    e = os.environ if env is None else env
    try:
        return max(1, int(str(e.get("CRISSA_ENROLL_COUNT_WORDS_MIN") or "3").strip()))
    except ValueError:
        return 3


def count_tries(env=None) -> int:
    e = os.environ if env is None else env
    try:
        return max(1, int(str(e.get("CRISSA_ENROLL_COUNT_TRIES") or "2").strip()))
    except ValueError:
        return 2


def count_words_in(text: str) -> int:
    """How many number words (digits or one..twenty) the text holds."""
    import re as _re
    toks = _re.findall(r"[a-z]+|\d+", str(text or "").lower())
    return sum(1 for t in toks if t.isdigit() or t in COUNT_WORDS)


def _asr_url() -> str:
    return (os.environ.get("CRISSA_ENROLL_ASR_URL") or _ports.url("asr")).rstrip("/")


def take_hears_count(wav: Path) -> tuple[bool | None, int, str]:
    """(heard, number words, text) for a take: None when the ASR could not be asked (the take stands on its loudness)."""
    import wave as _wave
    try:
        with _wave.open(str(wav), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None, 0, ""
            sr, pcm = w.getframerate(), w.readframes(w.getnframes())
        secs = len(pcm) / 2.0 / float(max(sr, 1))
        req = urllib.request.Request(_asr_url() + "/stt", data=pcm,
                                     headers={"X-Sample-Rate": str(sr), "Content-Type": "application/octet-stream"})
        # the decode runs on the CPU at a fraction of real time: the take's own length is the wait, never a fixed one
        with urllib.request.urlopen(req, timeout=max(5.0, secs * 3.0)) as r:
            got = json.loads(r.read().decode() or "{}")
        if not isinstance(got, dict) or got.get("error"):
            return None, 0, str((got or {}).get("error") or "")[:80] if isinstance(got, dict) else ""
        text = str(got.get("text") or "")
        nw = count_words_in(text)
        return nw >= count_words_min(), nw, text
    except Exception as e:  # noqa: BLE001  no ASR is no verdict
        return None, 0, ("%s" % type(e).__name__)[:80]


_NO_COUNT = {"said": False}


def _no_count_said(site) -> None:
    """Once a run: she heard no counting (on screen with the take's card, and in the log; no new spoken line)."""
    if _NO_COUNT["said"]:
        return
    _NO_COUNT["said"] = True
    print("  " + COUNT_NO_COUNT_SAID, flush=True)
    try:
        coach_emit(type="coach", text=COUNT_NO_COUNT_SAID)
    except Exception:  # noqa: BLE001  the note is a help, never a failure
        pass


def _quarantine(rec: dict) -> None:
    try:
        qdir = Path(rec["out_dir"]) / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        Path(rec["wav"]).replace(qdir / Path(rec["wav"]).name)
    except (OSError, KeyError, TypeError):
        pass


def _turn_check(site: Site, person_id: str, *, i: int, n: int, side: str, th, take_id: str, pose: str, angle: str,
                station: str, room_mic: str, window: float, sweep_paths: list, takes: list, rec) -> bool:
    """THE TURN IS CHECKED AS ITS COUNT ENDS: its side banked, else her quarter-turn line and the count again. A good
    voice take from the count again replaces the count's first one (that one goes to quarantine); a quiet one is set
    aside and the first stays. False when cancelled."""
    _turn_wait(th)
    tries = 0
    while side not in _TURNED_OK and tries < turn_check_tries():
        if _cancelled():
            return False
        tries += 1
        print(f"  count {i}: no turned still to the {side}; a quarter turn, count {i} again "
              f"({tries} of {turn_check_tries()})", flush=True)
        time.sleep(AFTER_BEEP_SEC)
        _correct(site, TURN_QUARTER_LINE)
        if _cancelled():
            return False
        _TURN_GEN["n"] += 1
        th = _snap_soon(site, person_id, station=station, tag="profile", note="verbal away again", side=side)
        ok, rec = _count_again(site, person_id, i=i, n=n, take_id=take_id, pose=pose, angle=angle, station=station,
                               room_mic=room_mic, window=window, snap_into=sweep_paths, away=True, takes=takes,
                               rec=rec)
        if not ok:
            return False
        _turn_wait(th)
    if side not in _TURNED_OK and tries and not _cancelled():
        _correct(site, TURN_LATER_LINE)        # round 34: a camera that cannot see never traps him
    print(f"  count {i}: the turn to the {side} {'banked' if side in _TURNED_OK else 'NOT banked after %d tries' % tries}",
          flush=True)
    return True


def _count_again(site: Site, person_id: str, *, i: int, n: int, take_id: str, pose: str, angle: str, station: str,
                 room_mic: str, window: float, snap_into, away: bool, takes: list, rec):
    """The count taken again after a correct-it line: (False when cancelled, the count's take now). A good take replaces
    the count's first one (that one goes to quarantine); a quiet one is set aside and the first stays."""
    _beep_wait(PRE_BEEP_S)
    _glass("turn", TURN_CUE, seconds=window, mode="screen", pip="loop", line="", beep=True, take=i, takes=n,
           mic=room_mic, away=away, verbal=True)
    again = _take_voice(site, person_id, line=TURN_CUE, pose=pose, angle=angle, take_id=take_id, station=station,
                        mic=room_mic, snap_into=snap_into, kind="count", seconds=window, defer_bank=True)
    if _cancelled():
        return False, rec
    if isinstance(again, dict):
        quiet, active, pk = take_is_quiet(Path(again["wav"]))
        if not quiet and hears_count_on():        # round 43: a take with no count in it is set aside like a quiet one
            heard, nw, said = take_hears_count(Path(again["wav"]))
            if heard is False:
                quiet = True
                print(f"  count {i} again: heard {nw} number words ({said[:40]!r}) - NO COUNT", flush=True)
        print(f"  count {i} again: voiced {active:.0%} peak {pk}{' - QUIET, the first take stays' if quiet else ''}",
              flush=True)
        if quiet:
            _quarantine(again)
        else:
            again["min_peak"] = V_MIN_PEAK
            if rec is not None and rec in takes:
                takes[takes.index(rec)] = again
                _quarantine(rec)
            else:
                takes.append(again)
            rec = again
    return True, rec


def _front_wait(person_id: str, th=None) -> None:
    """The count's front still has its say (stored or not) before the check."""
    for t in ([th] if th is not None else [x for x in list(_SNAP_THREADS) if x.name == "faceid-front"]):
        t.join(timeout=TURN_WAIT_S + 8.0)


def _front_check(site: Site, person_id: str, *, i: int, n: int, take_id: str, pose: str, angle: str, station: str,
                 room_mic: str, window: float, front_paths: list, takes: list, rec) -> bool:
    """THE FRONT IS CHECKED LIKE THE TURNS (round 34, the User's rule "if either failed then run the correct-it script",
    applied to the front): after the last facing count with no front still banked, "Look straight at the camera once
    more." and that count again, up to turn_check_tries() times. False when cancelled."""
    _front_wait(person_id)
    tries = 0
    while person_id not in _FRONT_OK and tries < turn_check_tries():
        if _cancelled():
            return False
        tries += 1
        print(f"  count {i}: no front still banked; count {i} again, facing ({tries} of {turn_check_tries()})",
              flush=True)
        time.sleep(AFTER_BEEP_SEC)
        _correct(site, TURN_FRONT_LINE)
        if _cancelled():
            return False
        th = _snap_now(site, person_id, station=station, tag="front", note="count %d again" % i)
        ok, rec = _count_again(site, person_id, i=i, n=n, take_id=take_id, pose=pose, angle=angle, station=station,
                               room_mic=room_mic, window=window, snap_into=front_paths, away=False, takes=takes,
                               rec=rec)
        if not ok:
            return False
        _front_wait(person_id, th)
    if person_id not in _FRONT_OK and tries and not _cancelled():
        _correct(site, TURN_LATER_LINE)
    print(f"  count {i}: the front {'banked' if person_id in _FRONT_OK else 'NOT banked after %d tries' % tries}",
          flush=True)
    return True


def _verbal_takes(site: Site, person_id: str, *, station: str, room_mic: str, takes: list, front_paths: list,
                  sweep_paths: list, four: bool, only: str, t0: float) -> bool:
    """The takes of the verbal routine (see THE VERBAL ROUTINE); False when cancelled."""
    plan = plan_takes(four, only)               # one kind again (GG): the first facing count and the first turn
    n = len(plan)
    _TURNED_OK.clear()
    _FRONT_OK.discard(person_id)
    _NO_COUNT["said"] = False
    print(f"  verbal routine: {n} counts", flush=True)
    films = films_on()
    if not films:
        warm = threading.Thread(target=verbal_warm, name="verbal-warm", daemon=True)
        warm.start()
    window = verbal_window_s(0.0)
    for i, (take_id, pose, angle, away, words) in enumerate(plan, start=1):
        this_rec = None
        if _cancelled():
            return False
        _TURN_GEN["n"] += 1                     # the last turn's looks end as this cue starts (round 21)
        _glass("turn", TURN_CUE, seconds=window, mode="screen", pip="loop", line="", take=i, takes=n, mic=room_mic,
               away=away, verbal=True, **({"stage": "counts"} if films else {}))
        if films:
            fid = count_film(i, away, words)
            if fid:
                _film(fid)
            else:
                time.sleep(AFTER_BEEP_SEC)      # count 2: the next beep, no line between
        elif i == 1:
            _announce(site, _cue(script()[0]), V_COUNT, V_FIRST_AFTER)
            # the pace to copy is the demo count's own length (the middle line): read back from the rendered line
            got = _ann_render(V_COUNT)
            demo_s = (len(got[0]) / 2.0 / got[1] - got[2]) if got else 0.0
            window = verbal_window_s(demo_s)
            print(f"  count window {window:.1f} s (the demo count {demo_s:.2f} s)", flush=True)
        else:
            if away and not any(t[3] for t in plan[:i - 1]):
                turn_film_under_cue(site)       # the first turn: Lila shows it as she says it (round 21)
            _announce(site, _cue(words))
        if _cancelled():
            return False
        # FACE PHOTOS DURING THE COUNTS (the User 2026-10-12, .101 fresh install: "why don't we do face photos in the
        # background while they say the six counts at the camera?"): every count takes face stills, facing and turned
        # (to the left, then to the right), and the sweep after Thanks keeps the best of each angle, so Setup's Face
        # photos step can move on by itself (lib/crissa_init face_banked). A turned still the face engine cannot use
        # (too_small, no_face) is dropped quietly: it never fails or warns a take, which is a voice take first.
        # CRISSA_VOICE_ID_TURNED_STILLS=0: facing stills only (round 13).
        turned_stills = os.environ.get("CRISSA_VOICE_ID_TURNED_STILLS", "1").strip().lower() not in (
            "0", "off", "false", "no") and (not films or count_profiles_on())   # the one page: round 23 tries them again
        turn_th = None
        if away and turned_stills:
            turn_th = _snap_soon(site, person_id, station=station, tag="profile", note="verbal away", side=turn_side(words))
        elif i == 1 or front_every_count():
            _snap_now(site, person_id, station=station, tag="front", note="first" if i == 1 else "count %d" % i)
        hears = hears_count_on()
        tries = count_tries() if hears else 2
        for attempt in range(1, tries + 1):
            _beep_wait(PRE_BEEP_S)
            _glass("turn", TURN_CUE, seconds=window, mode="screen", pip="loop", line="", beep=True, take=i, takes=n,
                   mic=room_mic, away=away, verbal=True)
            rec = _take_voice(
                site, person_id, line=TURN_CUE, pose=pose, angle=angle, take_id=take_id, station=station,
                mic=room_mic, snap_into=((sweep_paths if turned_stills else None) if away else front_paths),
                kind="count", seconds=window,
                defer_bank=True,
            )
            if _cancelled():
                return False
            if not isinstance(rec, dict):
                break
            quiet, active, pk = take_is_quiet(Path(rec["wav"]))
            heard, nw, said = (None, 0, "")
            if hears and not quiet:
                heard, nw, said = take_hears_count(Path(rec["wav"]))
            no_count = heard is False
            print(f"  count {i}/{n} take {attempt}: voiced {active:.0%} peak {pk}{' - QUIET' if quiet else ''}"
                  + ("" if not hears or quiet else
                     (" - the ASR could not be asked (%s), loudness only" % (said or "no answer")) if heard is None else
                     " - heard %d number word%s%s" % (nw, "" if nw == 1 else "s", " (%r), NO COUNT" % said[:40]
                                                       if no_count else "")),
                  flush=True)
            if (not quiet and not no_count) or (attempt == tries and not hears):
                rec["min_peak"] = V_MIN_PEAK
                takes.append(rec)      # with the kill: a second quiet one is banked as quiet and reported (round 42)
                this_rec = rec
                break
            if attempt == tries:       # round 43: after the tries nothing is kept for this count, and the run says so
                _quarantine(rec)
                print(f"  count {i}/{n}: heard no counting after {tries} takes - nothing kept", flush=True)
                _no_count_said(site)
                break
            _quarantine(rec)           # the quiet one is never a print: set aside with the routine's other quiet takes
            time.sleep(AFTER_BEEP_SEC)
            if films:
                _film("count-again")
            else:
                _announce(site, V_REDO)
        if (not away and turn_check_on() and (i == 1 or front_every_count())
                and not any(not t[3] for t in plan[i:])):              # the last facing count: the front checked
            if not _front_check(site, person_id, i=i, n=n, take_id=take_id, pose=pose, angle=angle, station=station,
                                room_mic=room_mic, window=window, front_paths=front_paths, takes=takes,
                                rec=this_rec):
                return False
        if away and turned_stills and turn_check_on() and turn_side(words):
            if not _turn_check(site, person_id, i=i, n=n, side=turn_side(words), th=turn_th, take_id=take_id,
                               pose=pose, angle=angle, station=station, room_mic=room_mic, window=window,
                               sweep_paths=sweep_paths, takes=takes, rec=this_rec):
                return False
        print(f"  count {i} in at +{time.time() - t0:.1f}s ({time.strftime('%H:%M:%S')})", flush=True)
        time.sleep(AFTER_BEEP_SEC)
    # the turned stills are stored before Thanks (round 17): this routine runs in the coach's child process, and its
    # exit right after the teardown ends every daemon thread still storing. Each is done long before; this is the bound
    _TURN_GEN["n"] += 1                         # the counts are over: a turn still looking stops at its next look
    deadline = time.time() + TURN_WAIT_S + 8.0
    for th in [t for t in list(_SNAP_THREADS) if t.name.startswith("snap-turned-")]:
        th.join(timeout=max(0.0, deadline - time.time()))
    if films:
        if turn_check_on():
            # round 34 (the User): no Face photos section - each count was checked as it ended
            print("  face: no face photos section (the counts were checked)", flush=True)
        else:
            _face_stage(site, person_id, station=station)
    return True


def _loop_enter_host() -> str | None:
    """The wall host's picture: Lila's listen loop on the conductor (the
    talk-angled loop when it is not rendered yet), her head narrowed."""
    prev = None
    try:
        import urllib.request as _ur

        with _ur.urlopen(f"{CONDUCTOR}/v1/state", timeout=3) as r:
            st = json.loads(r.read().decode() or "{}")
        prev = ((st.get("sequencer") or {}).get("register")) or st.get("register")
    except Exception:
        prev = None
    _conductor("/v1/register", {"name": "presenting"})
    key = HOST_CLIPS["listen"] if _inventory_clip(HOST_CLIPS["listen"]) is not None else LOOP_KEY
    out = _conductor("/v1/cue", {"key": key, "ttl": 0, "bridge": True, "by": "register"})
    print(f"  host loop {key} → {out}", flush=True)
    return prev if isinstance(prev, str) else None


def _run_wall(
    site: Site,
    person_id: str,
    *,
    name: str,
    line: str,
    turn_line: str,
    station: str,
    room_mic: str,
    handoff: bool = True,
) -> list[Path]:
    """The fifteen-second routine. Nothing on the user's side of "Thanks"
    waits on the camera, an embedding or a store."""
    written: list[Path] = []
    if register_camera.on() and not _user_mode_gate(site, person_id)["ok"]:
        # User mode first, and never a FaceFinder look to get there (Init step 4): say why and stop
        if _prearm_read():
            prearm_expire(force=True)
        _deny_register(register_camera.REFUSE_TEXT)
        return written
    arm = _prearm_read()
    if arm:
        arm = _prearm_adopt(arm)       # round 46: ours now; the coach's pre-arm timer leaves it alone
        print(f"  pre-armed {time.time() - float(arm.get('ts') or 0):.1f}s ago", flush=True)
        hold_fov(station)   # this process's snap path: the camera is already there
        if arm.get("loop_entered", True):    # Lila's clips only for the routine that plays them (2026-10-12)
            _host_warm()
    else:
        arm = prearm(site, station=station, write_marker=False)
    if (register_camera.on() and not arm.get("token") and arm.get("refused") == "not_user_mode"
            and _visitor_take(person_id)):
        # ADD A VISITOR: a marker armed before the press had no visitor to name; this take asks again, as the visitor's
        tok = register_camera.take(PRESENCE, _post, why="register", visitor=_visitor_take(person_id))
        print(f"  register token (visitor) → {tok}", flush=True)
        if tok.get("ok"):
            arm = dict(arm, token=True, lease_id=tok.get("lease_id"), refused=None)
    lease_id = arm.get("lease_id")
    loop_prev = arm.get("loop_prev")
    if register_camera.on() and not arm.get("token") and not arm.get("voice_only"):
        # presence refused the token (not User mode, or the User's joystick holds the camera): say so, give all back
        prearm_expire(force=True) if _prearm_read() else None
        if arm.get("hold_ours", True):
            reopen_ear_after_enroll()
            _tool_hold_release(owner="voice_register")
        (_deny_register if arm.get("refused") == "not_user_mode" else _fail_register)(
            register_camera.REFUSE_TEXT if arm.get("refused") == "not_user_mode"
            else (arm.get("refused_text") or register_camera.refusal_text({"error": arm.get("refused")})))
        return written
    takes: list[dict[str, Any]] = []
    front_paths: list[Path] = []
    sweep_paths: list[Path] = []
    have_bins: list[str] = []
    QUIET_TAKES.clear()
    only = register_only()                 # one take again (GG): '' = both, as before
    four = four_takes()                    # the sentence and the count, each facing and turned away (2026-10-11)
    per = 2 if four else 1
    n_takes = per * (1 if only else 2)
    count_no = n_takes                     # the count turned away is always the last take
    if four:
        print(f"  four takes: {n_takes} this run", flush=True)
    if only:
        print(f"  one take again: {only}", flush=True)
    read_s = read_window_s(line)        # the sentence read calmly, with margin (AB, 2026-10-10)
    verbal = routine() == "verbal"      # THE VERBAL ROUTINE (2026-10-12): every take a count, the announcer's cues
    if verbal:
        print("  routine: verbal (counts of six, the announcer's cues; CRISSA_VOICE_ID_ROUTINE=video = the clips)",
              flush=True)
    else:
        print(f"  read window {read_s:.1f} s for {len(line.split())} words", flush=True)
    t0 = time.time()
    print(f"  t0 {time.strftime('%H:%M:%S')}", flush=True)
    try:
        if verbal and not _verbal_takes(site, person_id, station=station, room_mic=room_mic, takes=takes,
                                        front_paths=front_paths, sweep_paths=sweep_paths, four=four, only=only, t0=t0):
            return written
        if not verbal and only != "count":    # the sentence (skipped when only the count is taken again, GG)
            # t=0: the sentence on the glass, the front still on its way to FaceID,
            # and the first line — all at once
            _glass("read", line, seconds=read_s, mode="screen", pip="loop", line=line,
                   take=1, takes=n_takes, mic=room_mic, away=False)
            _snap_now(site, person_id, station=station, tag="front", note="first")
            _host_play(site, "okay")
            if _cancelled():
                return written
            _host_loop_under_take(site)
            _beep_wait(PRE_BEEP_S)      # the glass's GO lands with the beep, BEEP_GAP_S after her last word
            _glass("read", line, seconds=read_s, mode="screen", pip="loop", line=line, beep=True,
                   take=1, takes=n_takes, mic=room_mic, away=False)
            rec = _take_voice(
                site, person_id, line=line, pose="front", angle="on", take_id="seat_on",
                station=station, mic=room_mic, snap_into=front_paths, kind="talk",
                seconds=read_s, defer_bank=True,
            )
            if isinstance(rec, dict):
                takes.append(rec)
            if _cancelled():
                return written
            print(f"  front take in at +{time.time() - t0:.1f}s ({time.strftime('%H:%M:%S')})", flush=True)
            time.sleep(AFTER_BEEP_SEC)
            if four:                  # the sentence again, turned away from the camera (2026-10-11)
                _glass("read", line, seconds=read_s, mode="screen", pip="loop", line=line,
                       take=2, takes=n_takes, mic=room_mic, away=True)
                _host_play(site, "turn_read")
                _snap_soon(site, person_id, station=station, tag="profile", note="read away")
                if _cancelled():
                    return written
                _host_loop_under_take(site)
                _beep_wait(PRE_BEEP_S)
                _glass("read", line, seconds=read_s, mode="screen", pip="loop", line=line, beep=True,
                       take=2, takes=n_takes, mic=room_mic, away=True)
                rec = _take_voice(
                    site, person_id, line=line, pose="sweep", angle="off", take_id="seat_read_off",
                    station=station, mic=room_mic, snap_into=sweep_paths, kind="talk",
                    seconds=read_s, defer_bank=True,
                )
                if isinstance(rec, dict):
                    takes.append(rec)
                if _cancelled():
                    return written
                print(f"  read-away take in at +{time.time() - t0:.1f}s ({time.strftime('%H:%M:%S')})", flush=True)
                time.sleep(AFTER_BEEP_SEC)
        if not verbal and only != "read":     # the turn and the count (skipped when only the sentence is taken again)
            if four:                  # the count facing the camera first (2026-10-11)
                face_no = count_no - 1
                _glass("turn", turn_line, seconds=float(VOICE_TURN_SECONDS), mode="screen", pip="loop", line=line,
                       take=face_no, takes=n_takes, mic=room_mic, away=False)
                _host_play(site, "face_count")
                if _cancelled():
                    return written
                _host_loop_under_take(site)
                _beep_wait(PRE_BEEP_S)
                _glass("turn", turn_line, seconds=float(VOICE_TURN_SECONDS), mode="screen", pip="loop", line=line,
                       beep=True, take=face_no, takes=n_takes, mic=room_mic, away=False)
                rec = _take_voice(
                    site, person_id, line=turn_line, pose="front", angle="on", take_id="seat_count_on",
                    station=station, mic=room_mic, snap_into=front_paths, kind="count",
                    seconds=float(VOICE_TURN_SECONDS), defer_bank=True,
                )
                if isinstance(rec, dict):
                    takes.append(rec)
                if _cancelled():
                    return written
                print(f"  count-facing take in at +{time.time() - t0:.1f}s ({time.strftime('%H:%M:%S')})", flush=True)
                time.sleep(AFTER_BEEP_SEC)
            # the turn: the line; the count fades in as she says "like you see
            # now" (TURN_TEXT_AT_SEC into it, at once when there is no clip);
            # the profile still at her last word; then the tick
            if _host_wav("turn") is not None:
                threading.Timer(TURN_TEXT_AT_SEC, lambda: _glass("turn", turn_line, seconds=float(VOICE_TURN_SECONDS),
                                                                    mode="screen", pip="loop", line=line,
                                                                    take=count_no, takes=n_takes, mic=room_mic,
                                                                    away=True)).start()
            else:
                _glass("turn", turn_line, seconds=float(VOICE_TURN_SECONDS), mode="screen", pip="loop", line=line,
                       take=count_no, takes=n_takes, mic=room_mic, away=True)
            _host_play(site, "turn")
            _snap_soon(site, person_id, station=station, tag="profile", note="first")   # her last word, off the gap
            if _cancelled():
                return written
            _host_loop_under_take(site)
            _beep_wait(PRE_BEEP_S)
            _glass("turn", turn_line, seconds=float(VOICE_TURN_SECONDS), mode="screen", pip="loop", line=line, beep=True,
                   take=count_no, takes=n_takes, mic=room_mic, away=True)
            rec = _take_voice(
                site, person_id, line=turn_line, pose="sweep", angle="off", take_id="seat_off",
                station=station, mic=room_mic, snap_into=sweep_paths, kind="count",
                seconds=float(VOICE_TURN_SECONDS), defer_bank=True,
            )
            if isinstance(rec, dict):
                takes.append(rec)
            if _cancelled():
                return written
            print(f"  turn take in at +{time.time() - t0:.1f}s ({time.strftime('%H:%M:%S')})", flush=True)
            time.sleep(AFTER_BEEP_SEC)
        if verbal and films_on():
            _glass("thanks", "", pip="loop", line="")     # the one page: her next film is the next section's
        elif verbal:
            _glass("thanks", "That's it. Thanks!", pip="loop", line="")
            _announce(site, script()[2])
        else:
            _glass("thanks", "That's it. Thanks!", pip="loop", line=line)
            _host_play(site, "thanks")
        print(f"  thanks at +{time.time() - t0:.1f}s ({time.strftime('%H:%M:%S')})", flush=True)
        # Crissa's handoff — the first thing a new User meets; the wizard waits
        # for it (the register phase stays `thanks` until it ends)
        if handoff:
            _host_play(site, "handoff")
        _glass("done", "", pip="off")
        _close_to_cp()
    finally:
        # the user is done; everything below is the house's own time.
        # ORDER IS THE SEAM (the User 2026-09-12: "no awkward thinking time
        # waiting turns … must be a concurrency setup"). Measured: the sweep
        # is a cold CPU FaceID subprocess, 5.0 s per call, and it ran TWICE
        # in series before the ear, the hold and the camera were released —
        # the next chapter could not listen or look for 12–15 s. Now: the
        # two voice prints (in-process, ~0.3 s each), then everything the
        # house needs back — camera home, lease, watchdogs, ear, hold — at
        # once; the face sweep (ONE embed pass over both bursts) runs on a
        # daemon thread behind the next chapter.
        t1 = time.time()
        for i, rec in enumerate(takes):
            try:
                jp = _bank_wav(
                    site, person_id, rec["wav"], slot=int(rec["slot"]) + i, seconds=rec["seconds"],
                    line=rec["line"], pose=rec["pose"], angle=rec["angle"], take_id=rec["take_id"],
                    station=rec["station"], mic=rec["mic"], out_dir=rec["out_dir"], retry=False,
                    # a six-second window with a beat of reaction time: 45% is a
                    # read sentence (60% was the 9 s take's bar); a longer read window
                    # keeps the same voiced seconds (voiced_bar, AB); the count's bar is
                    # COUNT_MIN_ACTIVE (25 %, measured: GG, 2026-10-10; was 35 %)
                    min_active=(COUNT_MIN_ACTIVE if rec["take_id"] == "seat_off" or rec["take_id"] in COUNT_TAKE_IDS
                                else voiced_bar(0.45, rec["seconds"])),
                    min_peak=int(rec.get("min_peak") or 0),
                )
                if jp:
                    written.append(jp)
            except Exception as e:
                print(f"  bank {rec.get('take_id')}: {e!r}", flush=True)
        t_bank = time.time()
        if takes and not written and not _cancelled():
            quiet = len(QUIET_TAKES) >= len(takes)
            msg = ((NOTHING_HEARD_COUNT if (only == "count" or verbal) else NOTHING_HEARD) if quiet
                   else "No voice print was made from the recordings. Please press Retry.")
            print(f"  no voice print: {len(QUIET_TAKES)} of {len(takes)} takes quiet, recorded on {room_mic or '?'}",
                  flush=True)
            _glass("error", msg, pip="off", mic=room_mic)
            coach_emit(type="error", text=msg, quiet=quiet)
        if arm.get("loop_entered", True):    # a spoken routine cued no loop: she carries on, no /v1/home (2026-10-12)
            _loop_leave(loop_prev)
        if lease_id and arm.get("token"):
            try:
                _ff_set(lease_id, False)
            except Exception as e:
                print(f"  ff off: {e!r}", flush=True)
            print(f"  register token given back → {register_camera.give_back(PRESENCE, _post)}", flush=True)
        elif lease_id:
            try:
                _ff_set(lease_id, False)
            except Exception as e:
                print(f"  ff off: {e!r}", flush=True)
            try:
                go_home(site, lease_id)
            except Exception as e:
                print(f"  go_home: {e!r}", flush=True)
            try:
                rel = _post(f"{CAMERA}/v1/lease/release", {"lease_id": lease_id}, timeout=LEASE_RELEASE_TIMEOUT_SEC)
                print(f"  lease/release → {rel}", flush=True)
            except Exception as e:
                print(f"  lease/release: {e!r}", flush=True)
            try:
                _resume_watchdogs()
            except Exception as e:
                print(f"  resume watchdogs: {e!r}", flush=True)
        try:
            PREARM_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        t_cam = time.time()
        if arm.get("hold_ours", True):
            reopen_ear_after_enroll()
            _tool_hold_release(owner="voice_register")
        else:
            print("  hold stays with the wizard; the ear is its to reopen", flush=True)
        t_free = time.time()
        print(f"  released: prints {t_bank - t1:.1f}s camera {t_cam - t_bank:.1f}s ear+hold {t_free - t_cam:.1f}s "
              f"→ the house is free {t_free - t1:.1f}s after Thanks (routine {t1 - t0:.1f}s)", flush=True)

        paths_all = list(front_paths) + list(sweep_paths)
        if paths_all and not _cancelled():
            def _sweep_bg() -> None:
                ts = time.time()
                _join_snaps(timeout=60.0)          # the two enroll stills' store_face threads
                try:
                    _sweep_store(site, person_id, station=station, paths=paths_all, have=have_bins)
                except Exception as e:
                    print(f"  sweep store: {e!r}", flush=True)
                print(f"  sweep (background, one embed pass over {len(paths_all)} stills) {time.time() - ts:.1f}s", flush=True)
            threading.Thread(target=_sweep_bg, name="enroll-sweep", daemon=True).start()
        else:
            _join_snaps(timeout=20.0)
        print(f"  teardown {time.time() - t1:.1f}s on the user's side (routine {t1 - t0:.1f}s)", flush=True)
    return written


# A PACK RECORDS FROM THE COMPUTER'S DEFAULT INPUT (2026-10-10, the kit's clean-install trial: Voice ID stopped on
# "site.audio.mic is empty - set in site.json"). A fresh install's site.json names no microphone and the short walk
# does not ask for one, so the take had no device. PulseAudio's (and PipeWire's) @DEFAULT_SOURCE@ is the computer's
# own default input. The house names its own (site.json enroll.mic / audio.mic, house-local.json desk_mic_hint) and
# never reaches it; HOUSE_ENROLL_MIC_REQUIRED=1 keeps the refusal ("No microphone is chosen").
DEFAULT_SOURCE = "@DEFAULT_SOURCE@"


def room_mic_for(site: Site, mic: str | None, station: str) -> str:
    """The microphone Register records from: the caller's, site.json enroll.mic, the desk hint, site.json audio.mic,
    and in a pack the default input."""
    room_mic = (mic or "").strip()
    if not room_mic:
        room_mic = enroll_mic(site)          # site.json enroll.mic: "ear" or a source name (the User 10-02)
    if not room_mic and station == "desk":
        # the desk mic's name is this house's, not the code's (Init step 5): house-local.json "desk_mic_hint"
        from .local_values import local

        hint = str(local("desk_mic_hint", "") or "")
        room_mic = (pulse_source_named(hint) or "") if hint else ""
    if not room_mic:
        room_mic = site.mic
    if not room_mic and not _ppaths.is_house() and os.environ.get("HOUSE_ENROLL_MIC_REQUIRED", "").strip().lower() not in (
            "1", "on", "true", "yes"):
        # the ear's microphone by name (one rule for the ear, Init's plate and this recorder), the server's
        # default only when nothing answers (2026-10-10)
        room_mic = ear_source() or DEFAULT_SOURCE
    if room_mic:
        print(f"  microphone: {room_mic}", flush=True)
    return room_mic


def run_voice_register(
    site: Site,
    person_id: str,
    *,
    display: str | None = None,
    station: str = "desk",
    mic: str | None = None,
    first_boot: bool = False,
) -> list[Path]:
    _arm_cancel()
    person_id = person_id.strip().lower()
    ensure_person(person_id, display=display, root=site.root)
    name = display or display_name(person_id, site.root)
    line = CUE.format(name=name)
    turn_line = TURN_CUE.format(name=name)
    room_mic = room_mic_for(site, mic, station)
    if not room_mic and os.environ.get("HOUSE_ENROLL_MIC_REQUIRED", "").strip().lower() in ("1", "on", "true", "yes"):
        # no guess at a device (Init step 5): say what is missing instead of recording silence
        _fail_register("No microphone is chosen for Register. Pick one in Init, then press Register again.")
        return []
    written: list[Path] = []
    lease_id = None
    _SNAP_THREADS.clear()
    if HOST == "wall":
        # inside New User Init (first_boot) the wizard's own last chapter is
        # Crissa's handoff; standalone (Control Panel Register an ID) plays it here
        return _run_wall(site, person_id, name=name, line=line, turn_line=turn_line,
                         # Crissa is the OPENER of the first boot now (the User 2026-09-12: "use
                         # that Crissa clip for the very opening scene"), never a handoff
                         # after the register: the routine ends on Lila's Thanks and goes home
                         station=station, room_mic=room_mic, handoff=False)

    loop_prev: str | None = None
    loop_host = HOST == "loop"

    def _acquire_and_open() -> None:
        # Mouth hold, then opener. Camera/PID run on the other thread —
        # nobody talks until the beep, so this can overlap the load.
        _tool_hold_acquire(
            reason="voice-register", owner="voice_register", sec=600.0
        )
        try:
            import subprocess
            import sys

            hold = rails_path("scripts", "house-tool-hold.py")
            if hold.is_file():
                subprocess.run(
                    [
                        sys.executable,
                        str(hold),
                        "acquire",
                        "--reason",
                        "voice-register",
                        "--owner",
                        "voice_register",
                        "--sec",
                        "600",
                        "--allow-voice",
                        "crissa",
                        "--allow-voice",
                        "zoe",
                        "--allow-voice",
                        "announcer",
                    ],
                    capture_output=True,
                    timeout=15,
                )
        except Exception:
            pass
        if loop_host:
            # the opener, live on the loop, while the other thread does the
            # desk look and the camera (the User's lines, 2026-09-09)
            if first_boot:
                _speak_loop(LINE_OPENING)
            _speak_loop(LINE_WELCOME)

    # Pace on the glass while hold + camera come up. Ear closed first so
    # the opener is not stamped as a user turn. Do not POST phase=phase —
    # that clobbers /v1/register so the wall never sees the sentence.
    intro_t0 = time.time()
    if loop_host:
        loop_prev = _loop_enter()
    # the take line rides WITH the intro: the wall surfaces it under the
    # host's eyes as they drop to it, before the first beep (the User 2026-09-09)
    _glass("watch", LOOK_HERE, pip=("loop" if loop_host else "intro"), line=line)
    _pin_wall()
    release_ear_for_enroll()
    have_bins: list[str] = []
    talk: threading.Thread | None = None
    # Opener starts at Look here so synth overlaps the desk AUTH look.
    # Tool-hold closes the ear and allows announcer; it does not take the camera.
    talk = threading.Thread(target=_acquire_and_open, name="say-welcome")
    talk.start()
    try:
        if register_camera.on():
            # User mode first; Register never runs FaceFinder to authenticate (Init step 4)
            auth = _user_mode_gate(site, person_id)
        else:
            auth = _auth_desk_like_secure(site, person_id)
        if not auth.get("ok"):
            try:
                cues.request_audio_stop()
            except Exception:
                pass
            if register_camera.on():
                _deny_register(str(auth.get("error") or register_camera.REFUSE_TEXT))
            else:
                _fail_register(str(auth.get("error") or "Could not confirm you."))
            return written
        # Rescan-the-room sequence (who_is_here) plus the 2026-08-14 barge:
        # pause watchdogs → lease → desk aim → FF on → take (voice + cam) →
        # go_home WHILE leased → lease/release → resume so Oracle is back
        # on the door.
        token_held = False
        if register_camera.on():
            tok = register_camera.take(PRESENCE, _post, why="register", visitor=_visitor_take(person_id))
            print(f"  register token → {tok}", flush=True)
            token_held = bool(tok.get("ok"))
            lease_id = tok.get("lease_id") if token_held else None
            if not token_held:
                _fail_register(register_camera.refusal_text(tok))
                return written
        else:
            try:
                _pause_watchdogs(ttl_sec=180.0)
            except Exception as e:
                print(f"  pause watchdogs failed: {e!r}", flush=True)
            try:
                lease = _post(
                    f"{CAMERA}/v1/lease",
                    {"owner": "voice-register", "ttl_sec": 180},
                )
                lease_id = lease.get("lease_id") if lease.get("ok") is not False else None
                print(f"  camera lease → {lease}", flush=True)
            except Exception as e:
                print(f"  camera lease failed: {e!r}", flush=True)

        _aim(site, station, lease_id)
        if _cancelled():
            return written
        _ff_set(lease_id, True)
        if not _join_announcer(talk, timeout=25.0):
            if _cancelled():
                return written
            _fail_register("Could not start — try Begin again.")
            return written
        if loop_host:
            _speak_loop(LINE_READ)
        else:
            remain = float(INTRO_SEC) - (time.time() - intro_t0)
            if remain > 0.2 and not _cancelled():
                time.sleep(remain)
        if _cancelled():
            return written

        front_paths: list[Path] = []
        path = _text_then_take(
            site,
            line,
            float(VOICE_DESK_SECONDS),
            pip=("loop" if loop_host else "hold"),
            person_id=person_id,
            pose="front",
            angle="on",
            take_id="seat_on",
            station=station,
            mic=room_mic,
            snap_into=front_paths,
            kind="talk",
        )
        if path:
            written.append(path)
        if front_paths:
            try:
                _sweep_store(
                    site,
                    person_id,
                    station=station,
                    paths=front_paths,
                    have=have_bins,
                )
            except Exception as e:
                print(f"  front store: {e!r}", flush=True)
        elif not _cancelled():
            _security_snap(
                site,
                person_id,
                station=station,
                pose="front",
                take_plan_id="seat_on",
                lease_id=lease_id,
                have=have_bins,
            )
        if _cancelled():
            return written
        if not _cancelled():
            cues.success_ding(site)

        _emit_angles(have_bins)
        if _cancelled():
            return written
        if loop_host:
            _speak_loop(LINE_TURN)
        sweep_paths: list[Path] = []
        path = _text_then_take(
            site,
            turn_line,
            float(VOICE_TURN_SECONDS),
            phase="turn",
            pip=("loop" if loop_host else "hold"),
            person_id=person_id,
            pose="sweep",
            angle="off",
            take_id="seat_off",
            station=station,
            mic=room_mic,
            snap_into=sweep_paths,
            kind="count",
        )
        if path:
            written.append(path)
        try:
            _sweep_store(
                site,
                person_id,
                station=station,
                paths=sweep_paths,
                have=have_bins,
            )
        except Exception as e:
            print(f"  sweep store: {e!r}", flush=True)
        if _cancelled():
            return written

        if not _cancelled():
            cues.session_end(site)
        if loop_host:
            _speak_loop(LINE_DONE)
        _glass("done", "", pip="off")
        _close_to_cp()
    finally:
        if talk is not None and talk.is_alive():
            talk.join(timeout=2)
        if loop_host:
            _loop_leave(loop_prev)
        if lease_id and register_camera.on():
            try:
                _ff_set(lease_id, False)
            except Exception as e:
                print(f"  ff off: {e!r}", flush=True)
            print(f"  register token given back → {register_camera.give_back(PRESENCE, _post)}", flush=True)
            lease_id = None
        if lease_id:
            try:
                _ff_set(lease_id, False)
            except Exception as e:
                print(f"  ff off: {e!r}", flush=True)
            # Same close as Rescan: home WHILE leased, then give the lease back,
            # then resume the door watch so Oracle is back on the door.
            try:
                go_home(site, lease_id)
            except Exception as e:
                print(f"  go_home: {e!r}", flush=True)
            try:
                rel = _post(
                    f"{CAMERA}/v1/lease/release", {"lease_id": lease_id}, timeout=8
                )
                print(f"  lease/release → {rel}", flush=True)
            except Exception as e:
                print(f"  lease/release: {e!r}", flush=True)
            try:
                _resume_watchdogs()
            except Exception as e:
                print(f"  resume watchdogs: {e!r}", flush=True)
        _join_snaps()
        reopen_ear_after_enroll()
        _tool_hold_release(owner="voice_register")
    return written
