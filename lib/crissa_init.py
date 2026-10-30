"""Crissa Init: the first-boot walk a pack runs (the User 2026-10-05; the short walk 2026-10-10).

THE SHORT WALK (the User 2026-10-10, after the .101 demo stopped at Connect): ONE walk for every Crissa pack.

    connect   a test call to the Connect endpoint. Continue needs the LATEST test to have passed - the one run here
              or the one run on the Connect page, as long as it tested the agent that is saved - or SET UP LATER
              carries the walk on with no agent (kept in init.json `connect`; CRISSA_CONNECT_REQUIRED=1 = the hard
              gate). TEST tests what is typed, else the Connect page's last passing agent, else the saved one.
    machine   THE MACHINE CARD (card_enter below): Allow measures (lib/crissa_machine) and shows the recommendation,
              Skip measures nothing; then the voice this machine can run is picked and installed, then the word
              aligner installs (aligner_after_voice; only the check's measured align stage turns it off)
    voice_id  VOICE ID: the name, then the Register coach's routine (mode voice-register: Lila's Register clips, the
              sentence read at the desk, the turn and the count; the voice prints, and face stills when there is a
              camera). Driven from here: the coach's /start, its /session and the register glass the routine writes
    face      FACE PHOTOS: the coach's face section (look at the camera, then to the left), the AuraFace prints in the
              identity store and enroll.json. CRISSA_NO_CAMERA=1: the step says why and skips
    room      YOUR ROOM (the User 2026-10-11, "work in the oracle dial settings ... into your low res method, better
              than videos"): one plain card on the clock (the oracle dial): your seat, the speakers' hour, where the
              camera sits, the door; then the wave (is your right hand on the right of the picture?). Defaults shown,
              every mark optional, Skip. Saved where the house's own Init saves it (site.json stations.heading
              user_hour / camera_hour / door_hour, media_mode / media_station, camera.orientation)
    hours     YOUR HOURS: the overnight window (default 18:00 to 02:00, site.json training_window) and quiet hours
              (manners.quiet_hours, and the announcer's own talk.json quiet_hours); defaults shown, Skip
    done      init.json says done, she lands on her listening loop and stays there

ROUND 18 (the User 2026-10-13, "in 43s wait we could be all done and one click to start"): in a pack's first boot This
computer's Allow is the one click - the measurement, the voice and the aligner go on in the background while the walk
moves on (SETUP, TUCKED IN PARALLEL), Face photos starts by itself, every line takes its turn in one queue (ONE VOICE
AT A TIME), Lila's lips move on every line of hers (LILA'S LIPS ON HER LINES), and Setup ends with the team introducing
itself, Crissa on her home loop and the microphone on (THE TEAM AT THE END, MIC ON AT THE END).

LILA OPENS IT AND VOICES THE TWO CARDS (the User 2026-10-11, "maybe add Lila piece back in"): her own filmed lines,
played on the player - lila-ob-welcome as a first boot starts; lila-ob-seats, lila-ob-sound-hour, lila-ob-wave and
lila-ob-mirrored (after a No) on Your room; lila-ob-overnight and lila-ob-manners on Your hours - with her slide onto
her plate and back. Never lila-ob-handoff or lila-ob-thanks-crissa (they name the house's character). Cues text-only
(setup-cues.json) or CRISSA_INIT_SILENT=1: no line plays; the cards carry the words. No film of the character, no
splash still, none of the house's free-form Lila steps (mornings, news, the room scan, the host's thanks to the
character). Its rows are config/init-sequence-pack.json `walk` (the house's own walk, config/init-sequence-v2.json, is
the house's and no pack ships it); the id module's copy is modules/id/config/init-sequence.json.

WHICH WALKER CARRIES IT (decided 2026-10-10): this file - the stand-in, made real. It is the only walker in every pack
(the core package); the short walk has no step that needs the id module's 13-step machinery (no dialogue, no room, no
host line of its own: the coach's routine plays Lila's clips); and one state file and one snapshot shape serve the Tk
glass (lib/init_v2_render.py) and the browser page (display/crissa-init.html, /init). The id module's Init v1 and v2
entry points hand over to it (modules/id/crissa_id/init_wizard.py), so every server walk runs the same four steps.
When the id module is installed it supplies the identity store (identity()): the person, the User's role, site.json,
user.json and enroll.json from the AuraFace prints. Without it the same records are written here in the same shapes.

REGISTER'S START NEEDS (the User 2026-10-10: "satisfied by the pack itself"): the coach must say register_token: true
(its token path: Register never turns Secure off). A pack's coach says so by default (modules/id register_camera.on()
is on off the house), the launcher hands HOUSE_REGISTER_TOKEN=1 / RAIL_REGISTER_TOKEN=1 to every child, and this
process sets HOUSE_REGISTER_TOKEN=1 for itself when it serves. Nobody sets a variable.

What it keeps, under the state directory (lib/crissa_home.HOME), never in the pack:
    init.json      {schema, done, at, version, steps_done, service, connect[, cancelled_at]}; missing = first boot
    init-run.json  the running walk (status, step, each step's state)
    user.json      {schema, name, person_id, role: "user"}
    enroll.json    the User's face as numbers only (AuraFace), mode 0600
    identity/      the identity store the coach writes (people/<id>/face, voice), and its site.json

THE LAUNCHER'S SERVICE (2026-10-06): when the walk finishes under the launcher (CRISSA_LAUNCHER_ARGS), Init installs
the launcher as a service of the login (lib/crissa_service). CRISSA_SERVICE_INSTALL=0 skips it.

Off unless CRISSA_INIT=1; on the house (crissa_home.is_house()) it never serves - the house's Init is its own.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import crissa_egress  # every outbound open goes through the one switch (kill-search, 2026-10-05)
import crissa_home
import crissa_ports

SWITCH = "CRISSA_INIT"
SCHEMA = "house_init_wizard/v2"           # the wall's glass reads this shape
MARKER_SCHEMA = "crissa-init/1"
USER_SCHEMA = "crissa-user/1"
VERSION = 2                                # 2 = the short walk (2026-10-10)
REPO = Path(__file__).resolve().parents[1]
SEQ_FILE = REPO / "config" / "init-sequence-pack.json"
SEQ_SCHEMA = "crissa_init/1"
ID_MODULE = REPO / "modules" / "id"
WALK = ("connect", "machine", "voice_id", "face", "room", "hours")
LILA_KINDS = ("room", "hours")             # the cards Lila voices (her plate stays up from one to the next)
TERMINAL = ("done", "aborted")
_LOCK = threading.RLock()
SYNC = False                               # tests: run step threads inline
_sleep = time.sleep                        # tests patch this
POLL_S = 0.5                               # how often a running Register / face section is read

# What the conductor hands over at boot (configure): its own address and its speech.
_CFG: dict[str, Any] = {"base": "", "speak": None, "speaking": None, "home": None, "busy": None}
_THREADS: dict[str, threading.Thread] = {}

# ── the register glass (the coach's routine writes it; the Wall and /init read it) ─────────────────────────────────
_GLASS: dict[str, Any] = {"phase": "idle", "text": "", "pip": "", "line": "", "seconds": 0.0, "beep_ts": 0.0,
                          "mode": "", "n": 0, "ts": 0.0, "take": 0, "takes": 0, "mic": "", "cue": "", "away": None,
                          "verbal": False}


def glass_cue(g: dict[str, Any], now: float | None = None) -> str:
    """The words over the sentence (the User 2026-10-10, .101 V: no sentence and no cue on the Voice ID plate): what to
    do now - wait for the beep, read it now (with the seconds left), or the count - one rule for every renderer."""
    now = time.time() if now is None else now
    phase = str(g.get("phase") or "idle")
    try:
        beep, secs = float(g.get("beep_ts") or 0.0), float(g.get("seconds") or 0.0)
    except (TypeError, ValueError):
        beep, secs = 0.0, 0.0
    live = beep > 0 and secs > 0 and 0.0 <= now - beep < secs
    quiet = cues_mode() == "text"                 # no beep to wait for: the word Recording is the start (2026-10-10)
    # FOUR TAKES (2026-10-11): `away` says which way the person faces for this take - the sentence turned away and
    # the count facing the camera are the two new ones; a glass without it is the two-take routine, as before
    away = g.get("away")
    if phase == "read":
        if live:
            return "Recording: read it aloud now (%d s)" % max(1, int(round(secs - (now - beep))))
        if away:
            return ("Now turn away from the camera. When this says Recording, say the sentence again:" if quiet else
                    "Now turn away from the camera. At the beep, say the sentence again:")
        return "When this says Recording, read this sentence aloud:" if quiet else "At the beep, read this sentence aloud:"
    if phase == "turn" and g.get("verbal"):
        # THE SPOKEN ROUTINE'S ONE LINE (the User 2026-10-12, .101: "turn away from the microphone" on a camera with no
        # microphone of its own): what to do, with no device in it - the voice comes in on whichever microphone is set
        if live:
            return "Recording: count to six now (%d s)" % max(1, int(round(secs - (now - beep))))
        return ("Turn your head a little away. At the beep, count to six." if away else
                "At the beep, count to six.")
    if phase == "turn":
        if live:
            return "Recording: count aloud now (%d s)" % max(1, int(round(secs - (now - beep))))
        if away is False:
            return ("Face the camera. When this says Recording, count aloud:" if quiet else
                    "Face the camera. At the beep, count aloud:")
        return ("Turn away from the camera. When this says Recording, count aloud:" if quiet else
                "Turn away from the camera. At the beep, count aloud:")
    if phase == "watch":
        return "Look at the camera. The sentence to read comes next."
    if phase == "thanks":
        return "Done recording."
    return ""
REGISTER_PHASES = ("watch", "read", "turn", "thanks", "ended")
AUX_GET_ROUTES = ("/v1/register", "/v1/init-mouth", "/v1/init-heard")
ACTION_ROUTE = "/v1/action"
ENROLL_ACTIONS = {"enroll_start": ("/start", {"mode": "voice-register", "station": "desk"}),
                  "enroll_cancel": ("/cancel", {}), "enroll_prearm": ("/prearm", {"station": "desk"})}


def register_glass() -> dict[str, Any]:
    """GET /v1/register: the coach's phase on the glass (the Wall and /init paint it at 5 Hz while Register runs)."""
    return dict(_GLASS, ok=True, cue=glass_cue(_GLASS), film=dict(film_bus(), ahead_s=film_ahead_s()))


def set_register_glass(payload: dict[str, Any]) -> dict[str, Any]:
    """The Register routine's own writes (voice_register._glass -> POST /v1/action register_glass)."""
    # THE BAR STARTS WITH THE RECORDING (round 23, the User 2026-10-15, .101: "make the final beep right on the end of
    # bar"; "one bar timer ran out well ahead of the beep"): beep_ts was stamped before the mic was unmuted, the GO
    # tone played and the recorder started, so the bar ran out that much before the closing beep. The routine moves it
    # to the recorder's start with `rec_start`, touching nothing else. Kill (routine side): CRISSA_BAR_FROM_RECORDING=0.
    if payload.get("rec_start"):
        was = float(_GLASS.get("beep_ts") or 0.0)
        _GLASS["beep_ts"] = time.time()
        _GLASS["rec_late_s"] = round(_GLASS["beep_ts"] - was, 3) if was else None
        print("[init] voice id: recording started %.2f s after the GO stamp (take %s)"
              % (_GLASS["rec_late_s"] or 0.0, _GLASS.get("take")), flush=True)
        _GLASS["n"] = int(_GLASS.get("n") or 0) + 1
        _GLASS["ts"] = time.time()
        return register_glass()
    phase = str(payload.get("phase") or "idle")
    _GLASS.update(phase=phase, text=str(payload.get("text") or ""), pip=str(payload.get("pip") or ""),
                  mode=str(payload.get("mode") or ""))
    # the take's place in the routine and the microphone it records on (2026-10-10): "Recording 1 of 2" on both renderers
    try:
        _GLASS["take"] = int(payload.get("take") or 0) if phase in ("read", "turn") else (
            int(_GLASS.get("take") or 0) if phase == "thanks" else 0)
        _GLASS["takes"] = int(payload.get("takes") or _GLASS.get("takes") or 0) if phase not in ("idle", "done") else 0
    except (TypeError, ValueError):
        _GLASS["take"], _GLASS["takes"] = 0, 0
    if phase in ("read", "turn"):
        _GLASS["away"] = bool(payload["away"]) if payload.get("away") is not None else None
    elif phase in ("idle", "done", "error"):
        _GLASS["away"] = None
    # the spoken routine marks its glass (2026-10-12): one small plate, words with no device in them (glass_cue)
    if payload.get("verbal") is not None:
        _GLASS["verbal"] = bool(payload.get("verbal"))
    elif phase in ("idle", "done", "error"):
        _GLASS["verbal"] = False
    if payload.get("mic") is not None:
        _GLASS["mic"] = str(payload.get("mic") or "")
    elif phase in ("idle", "done"):
        _GLASS["mic"] = ""
    if payload.get("line") is not None:
        _GLASS["line"] = str(payload.get("line") or "")
    elif phase in ("idle", "error"):
        _GLASS["line"] = ""
    try:
        _GLASS["seconds"] = float(payload.get("seconds") or 0.0)
    except (TypeError, ValueError):
        _GLASS["seconds"] = 0.0
    # ONE-PAGE SETUP (round 22): the routine's stage (counts, face) and the turned photos it took, for the one page
    if payload.get("stage") is not None:
        _GLASS["stage"] = str(payload.get("stage") or "")
    elif phase in ("idle", "error"):
        _GLASS["stage"] = ""
    if isinstance(payload.get("faces"), list):
        _GLASS["faces"] = [str(x) for x in payload["faces"]][:4]
    elif phase in ("idle", "error"):
        _GLASS["faces"] = []
    if payload.get("beep"):
        _GLASS["beep_ts"] = time.time()
    elif phase in ("idle", "error", "done", "thanks", "watch"):
        _GLASS["beep_ts"] = 0.0
    _GLASS["n"] = int(_GLASS.get("n") or 0) + 1
    _GLASS["ts"] = time.time()
    return register_glass()


def enroll_url() -> str:
    """The Register coach (the presence module's child; the id module's coach when it is installed):
    HOUSE_ENROLL_COACH_URL, else CRISSA_ENROLL_URL (the launcher's), else this install's port table."""
    for key in ("HOUSE_ENROLL_COACH_URL", "CRISSA_ENROLL_URL"):
        got = os.environ.get(key, "").strip()
        if got:
            return got.rstrip("/")
    return crissa_ports.url("enroll")


def get(head: str, query: str = "") -> dict[str, Any]:
    """The Wall's other reads beside /v1/init."""
    if head == "/v1/register":
        return register_glass()
    if head == "/v1/init-mouth":
        fn = _CFG.get("speaking")
        return {"ok": True, "speaking": bool(fn()) if callable(fn) else False}
    if head == "/v1/init-heard":
        return {"ok": True, "text": "", "note": "the short walk asks nothing by voice"}
    return {"ok": False, "error": "not a route"}


def action(body: dict[str, Any]) -> dict[str, Any]:
    """POST /v1/action, the subset the Register routine and the Wall send: the enroll verbs forwarded to the coach
    (whitelisted like lib/enroll_payload), the glass write, the kiosk no-ops."""
    act = str(body.get("action") or "")
    if act in ("enroll_start", "kiosk_init_user"):
        refused = _face_refused("setup_" + act)      # Security On: his face first (APPLY_LOCKED above)
        if refused is not None:
            return dict(refused, action=act)
    if act == "register_glass":
        pl = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        return {"ok": True, "action": act, "glass": set_register_glass(pl)}
    if act == "setup_film":                     # ONE-PAGE SETUP: the routine cues one of Lila's films on the page
        st = load_state()
        if st.get("status") != "running" or not one_page_on(st):
            return {"ok": False, "action": act, "error": "no one-page Setup is running"}
        n = film_cue(str(body.get("id") or ""), by=str(body.get("by") or "voice id"),
                     **({"about": str(body["about"])} if body.get("about") else {}))
        return {"ok": bool(n), "action": act, "seq": n, "seconds": film_seconds(str(body.get("id") or "")),
                "ahead_s": film_ahead_s()}
    if act in ENROLL_ACTIONS:
        path, default = ENROLL_ACTIONS[act]
        payload: dict[str, Any] | None = dict(default, **(body.get("payload") or {})) if isinstance(body.get("payload"), dict) else dict(default)
        if act == "enroll_start":
            try:
                import enroll_payload
                payload, why = enroll_payload.clean(body.get("payload"), default)
                if payload is None:
                    return {"ok": False, "action": act, "error": why}
            except ImportError:
                pass
        if act == "enroll_cancel":
            set_register_glass({"phase": "idle"})
        got = _coach_post(path, payload or {}, 12.0 if act != "enroll_cancel" else 4.0)
        out: dict[str, Any] = {"ok": got.get("_ok", False), "action": act, "body": json.dumps(got)[:400]}
        if got.get("_ok"):
            out["result"] = {k: v for k, v in got.items() if k != "_ok"}
            if act == "enroll_start" and got.get("ok") is False:
                out["ok"] = False
                out["error"] = str(got.get("error") or "the coach refused")[:160]
        else:
            out["error"] = got.get("_error") or "no answer"
        return out
    if act == "kiosk_init_user":
        return dict(apply("start", {"force": True}), action=act)
    if act in ("kiosk_register", "kiosk_return_cp", "kiosk_home"):
        return {"ok": True, "action": act, "note": "the Wall is the glass here; nothing to move"}
    return {"ok": False, "action": act, "error": "not an action here"}


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(SWITCH, "")).strip().lower() in ("1", "on", "true", "yes")


def serves() -> bool:
    """The conductor answers /v1/init only in a pack with the switch on - or while a step started from the Crissa menu
    runs (Voice ID again, the User 2026-10-10), so its plate shows on both Walls after the first boot too."""
    if crissa_home.is_house():
        return False
    return enabled() or again_running()


AGAIN_SHOW_S = 600.0                       # a finished menu re-run stays on /v1/init this long, for its done plate


def again_running(now: float | None = None) -> bool:
    """A step started again from the Crissa menu (start_voice_id) is running, or ended in the last AGAIN_SHOW_S (so
    the page and the Wall can show how it ended)."""
    try:
        st = _load_json(run_path())
    except Exception:  # noqa: BLE001
        return False
    if st.get("schema") != SCHEMA or not st.get("only"):
        return False
    if st.get("status") == "running":
        return True
    t = time.time() if now is None else now
    try:
        return t - float(st.get("ended_t") or 0) < AGAIN_SHOW_S
    except (TypeError, ValueError):
        return False


def configure(base_url: str = "", speak: Optional[Callable[[str, str], Any]] = None,
              speaking: Optional[Callable[[], bool]] = None, home: Path | None = None,
              busy: Optional[Callable[[], bool]] = None) -> None:
    """The conductor's address (for its own /v1/play), its speech and whether a line is on air. `busy` (round 18): a
    line is in flight, whoever speaks it (a house-speak alive, a film's own line playing) - Setup's one queue waits on
    it. `home`: tests."""
    _CFG.update(base=str(base_url or "").rstrip("/"), speak=speak, speaking=speaking, home=home, busy=busy)
    base = str(base_url or "").rstrip("/")
    if base:
        for key in ("HOUSE_CONDUCTOR_URL", "VO_HOME_URL"):
            os.environ.setdefault(key, base)       # the coach's routine names the player here
    if home:
        os.environ.setdefault("CRISSA_HOME", str(home))
    if serves():
        os.environ.setdefault("HOUSE_REGISTER_TOKEN", "1")      # the pack's own Register path (see REGISTER'S START)
    close_stale_run()


# A STALE RUN IS NEVER RESUMED (the User 2026-10-12, .101 after the round-12 upgrade: "It just restarted in a failed voice
# screen" - round 11's Voice ID again had left init-run.json at status running, step voice_id, register failed /
# cancelled, and the next start showed that walk's Voice ID card; Retry then met "Register needs User auth first").
# At start, with the first boot's marker done, a run still marked running was left by an earlier process, whose threads
# are gone: it is closed (status "stale", the old status and when kept in it, one log line), never shown or resumed.
# A first boot still walking (no marker yet) resumes as before. Kill: CRISSA_INIT_CLOSE_STALE=0.
def close_stale_run() -> dict[str, Any]:
    if crissa_home.is_house() or os.environ.get("CRISSA_INIT_CLOSE_STALE", "1").strip().lower() in (
            "0", "off", "false", "no"):
        return {}
    with _LOCK:
        st = load_state()
        if st.get("status") != "running" or not marker().get("done"):
            return {}
        what = "Voice ID again" if st.get("only") else "a walk"
        print("[init] %s left running before this start (step %s since %s, Voice ID %s): closed, not resumed"
              % (what, st.get("step_id") or "?", st.get("started_at") or "?",
                 (st.get("register") or {}).get("state") or "-"), flush=True)
        st.update(status="stale", stale={"was": "running", "at": _now(), "step_id": st.get("step_id")},
                  ended_t=0.0, finished_at=_now())
        save_state(st)
        return st


# SETUP'S CUES, SPOKEN OR TEXT ONLY (the User 2026-10-10, .101 round 4: Setup's spoken cues always go to the speakers,
# and at night they woke the house). An Init option, kept in <state home>/setup-cues.json {"mode": "spoken"|"text"}:
# "spoken" (the default, today's) - the coach's lines, the host's recorded lines and the beeps play; "text" - nothing
# plays: the words and the Recording count are on the screen, and each beat waits as long as its sound would have.
# The coach reads the same file (house_enroll/cues.py text_only). CRISSA_SETUP_CUES=text|spoken overrides the file.
CUES_FILE = "setup-cues.json"
CUES_MODES = ("spoken", "text")


def cues_mode() -> str:
    env = str(os.environ.get("CRISSA_SETUP_CUES") or "").strip().lower()
    if env in CUES_MODES:
        return env
    d = _load_json(_home() / CUES_FILE)
    m = str(d.get("mode") or "").strip().lower() if isinstance(d, dict) else ""
    return m if m in CUES_MODES else "spoken"


def set_cues(mode: str) -> dict[str, Any]:
    m = str(mode or "").strip().lower()
    if m not in CUES_MODES:
        return {"ok": False, "error": "cues are %s, not %r" % (" or ".join(CUES_MODES), mode)}
    _write_json(_home() / CUES_FILE, {"mode": m, "at": _now()})
    return {"ok": True, "cues": cues_mode()}


def cues_key() -> tuple:
    """The plate's key that flips the cues (both renderers draw it as a key): it names the cues as they are now, and a
    press turns them the other way."""
    return ("cues", "Cues: text only") if cues_mode() == "text" else ("cues", "Cues: spoken")


def _home() -> Path:
    return Path(_CFG.get("home") or crissa_home.HOME)


def marker_path() -> Path:
    return _home() / "init.json"


def run_path() -> Path:
    return _home() / "init-run.json"


def user_path() -> Path:
    return _home() / "user.json"


def enroll_path() -> Path:
    return _home() / "enroll.json"


def identity_root() -> Path:
    """The identity store the coach writes: CRISSA_IDENTITY, HOUSE_ID_ROOT, else <state home>/identity."""
    got = (os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or "").strip()
    return Path(got).expanduser() if got else _home() / "identity"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _load_json(p: Path) -> dict[str, Any]:
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, d: dict[str, Any], mode: int | None = None) -> None:
    """Atomic: a reader sees the old file or the new one, never half."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, p)


# ── the sequence: the pack's own rows ────────────────────────────────────────────────────────────────────────────────
def load_sequence() -> dict[str, Any]:
    d = _load_json(SEQ_FILE)
    if d.get("schema") != SEQ_SCHEMA:
        return {"schema": SEQ_SCHEMA, "walk": []}
    return d


def pack_cfg() -> dict[str, Any]:
    """The pack's walk file (config/init-sequence-pack.json): walk, host, landing, done."""
    return load_sequence()


def steps() -> list[dict[str, Any]]:
    """The pack's walk, in order: `walk` (connect, machine, voice_id, face). Nothing of the house's steps."""
    rows = [dict(s) for s in (pack_cfg().get("walk") or []) if isinstance(s, dict) and s.get("id")]
    return rows or [{"id": sid, "kind": {"voice_id": "register"}.get(sid, sid)} for sid in WALK]


def step_cfg(sid: str) -> dict[str, Any]:
    return next((s for s in steps() if s.get("id") == sid), {})


def cued_films() -> list[dict[str, str]]:
    """Every library row the walk plays, in order (the coach's routine plays Lila's on Voice ID; the walk plays her
    welcome and her lines on Your room and Your hours): no film of the character."""
    host = pack_cfg().get("host") or {}
    out = [{"step": "start", "film": str(host["welcome"]), "where": "library"}] if host.get("welcome") else []
    out += [{"step": "voice_id", "film": k, "where": "library"} for k in (step_cfg("voice_id").get("films") or [])]
    for k in (host.get("enter"), host.get("leave"), host.get("loop")):
        if k:
            out.append({"step": "voice_id", "film": str(k), "where": "library"})
    for sid in LILA_KINDS:
        out += [{"step": sid, "film": str(k), "where": "library"} for k in (step_cfg(sid).get("films") or [])]
    seen: set = set()
    out = [r for r in out if not (r["film"] in seen or seen.add(r["film"]))]
    for row in landing_steps():
        if row.get("cue"):
            out.append({"step": "done", "film": str(row["cue"]), "where": "library"})
    return out


# ── state ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def empty_state() -> dict[str, Any]:
    return {"schema": SCHEMA, "status": "idle", "run_id": "", "index": 0, "step_id": "", "connect": {},
            "machine": {}, "register": {}, "face": {}, "steps_done": [], "notes": [], "landing": {}}


def load_state() -> dict[str, Any]:
    d = _load_json(run_path())
    if d.get("schema") == SCHEMA:
        base = empty_state()
        base.update(d)
        return base
    if _CANCELLED["home"] and _CANCELLED["home"] == str(_home()):
        return dict(empty_state(), status="aborted")   # round 34: the cancel left no file; this process says so
    return empty_state()


def save_state(st: dict[str, Any]) -> None:
    with _LOCK:
        if _cancel_landed(st):
            return
        st["updated_at"] = _now()
        _write_json(run_path(), st)


def _cancel_landed(st: dict[str, Any]) -> bool:
    """A CANCELLED FIRST BOOT STAYS UNWRITTEN (2026-10-26, the acceptance's init-cancel on the 1.0.1 candidate: after the
    page's Cancel, init-run.json was back - a later write of the aborted run, from the page's own posts, made it again).
    Once its cancel has put the home back, the aborted run is never written. Kill CRISSA_CANCEL_NO_REWRITE=0."""
    if os.environ.get("CRISSA_CANCEL_NO_REWRITE", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return (str(st.get("status") or "") == "aborted" and bool(_CANCELLED.get("home"))
            and _CANCELLED["home"] == str(_home()))


def _note(st: dict[str, Any], text: str) -> None:
    st.setdefault("notes", []).append(str(text)[:200])
    st["notes"] = st["notes"][-30:]
    print(f"[init] {text}"[:240], flush=True)


def _mutate(run_id: str, fn) -> dict[str, Any] | None:
    with _LOCK:
        st = load_state()
        if st.get("status") != "running" or st.get("run_id") != run_id:
            return None
        fn(st)
        save_state(st)
        return st


def _running(run_id: str) -> bool:
    st = load_state()
    return st.get("status") == "running" and st.get("run_id") == run_id


def marker() -> dict[str, Any]:
    return _load_json(marker_path())


# A CANCELLED SETUP LEAVES NOTHING (round 34, the User 2026-10-19: "cancelled setup marks as first run for next time
# and stores nothing. Let them bail out and leave no record if they want." This reverses the 10-05 rule that every
# cancel writes init.json). A first boot's walk takes a copy of the state home as it starts (snapshot_before). Its
# cancel puts the home back exactly as it was (restore_before): every file the walk wrote is gone, and every file it
# changed is as before. That covers Connect, the voice pick and language, her voice style, the lip sync, the mic, the
# cues, the room, the hours, the window record, and the identity store with the voice prints and face stills it
# banked. No init.json is written, so the next start is a first boot, Lila's opening included. The launcher's
# init_needed then agrees by itself. This process does not start it again (_CANCELLED): a page reloaded after the
# cancel does not restart Setup.
# Kept on purpose:
# - what is software, not a record: the voice and ASR installs, models, caches, the window's browser profile;
# - the logs;
# - the camera's rolling frames;
# - files over CANCEL_MAX_FILE.
# A running service keeps in memory what it had loaded until it restarts (presence has no reload route).
# A cancelled "set up again" after a FINISHED Setup keeps the finished record, as before (it is not a bail-out of the
# install). Kill CRISSA_CANCEL_LEAVES_NOTHING=0 (the 10-05 marker).
CANCEL_SNAP = ".setup-before"
CANCEL_SKIP_TOP = ("logs", "cache", "browser", "models", "hf", "aligner", "asr", "audio8", "multilingual", "pocket",
                   "tts", "turbo", "turbo-mlx", "kokoro", "oracle", "id", CANCEL_SNAP, "first-boot-archive")
CANCEL_SKIP_ANY = ("env", "venv", ".venv", "model", "models", "site-packages", "node_modules", "__pycache__",
                   "frames")
CANCEL_MAX_FILE = 16 * 1024 * 1024
_CANCELLED: dict[str, str] = {"home": ""}      # the state home whose first boot was cancelled in this process


def cancel_leaves_nothing_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_CANCEL_LEAVES_NOTHING", "1").strip().lower() not in ("0", "off", "false", "no")


def _snap_roots() -> list[Path]:
    home = _home()
    roots = [home]
    try:
        ident = identity_root().resolve()
        if home.resolve() not in ident.parents and ident != home.resolve():
            roots.append(ident)                  # an identity store kept outside the home
    except OSError:
        pass
    return roots


def _snap_files(root: Path) -> dict[str, tuple[int, int]]:
    """{relative path: (size, mtime_ns)} of the records under root (see CANCEL_SKIP_*)."""
    out: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return out
    for dirpath, dirs, files in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        top = rel_dir.parts[0] if rel_dir.parts else ""
        if top in CANCEL_SKIP_TOP and root == _home():
            dirs[:] = []
            continue
        if not rel_dir.parts:
            dirs[:] = [d for d in dirs if d not in CANCEL_SKIP_TOP]
        dirs[:] = [d for d in dirs if d not in CANCEL_SKIP_ANY and not os.path.islink(os.path.join(dirpath, d))]
        for f in files:
            p = Path(dirpath) / f
            try:
                st = p.lstat()
            except OSError:
                continue
            if p.is_symlink() or st.st_size > CANCEL_MAX_FILE:
                continue
            out[str(rel_dir / f)] = (st.st_size, st.st_mtime_ns)
    return out


# A TRUE FIRST BOOT WITHOUT A REINSTALL (round 38, .101 round 37: "isn't there a flag you can flip easier"): the
# launcher's --first-boot moves Setup's records (exactly what a cancelled first boot would remove - not the installs,
# models, caches, logs or the window's profile; see CANCEL_SKIP_*) into <state home>/first-boot-archive/<time>/, with a
# manifest, so the next start is a first boot. Nothing is deleted; moving the files back undoes it. A pack only.
FIRST_BOOT_ARCHIVE = "first-boot-archive"


# A FIRST BOOT KEEPS EVERY INSTALL (round 40, .101 round 38 N19: --first-boot moved ear/installed.json and
# presence/install.json too; the launcher then started the plain ear with no echo canceller, Lila's own film lines were
# heard as his and Crissa answered them, and Voice ID stalled with the enroll/camera pieces missing). Setup's records
# go; an install marker, an installed environment or a fetched tool never does. Kill CRISSA_FIRST_BOOT_KEEP_INSTALLS=0.
INSTALL_TOP = ("micromamba", "espeak-ng", "envs", "bin", "lib", "share")
INSTALL_NAMES = ("installed.json", "install.json", "camera-kind.json", "lanes.json", "launcher.json", "espeak.json",
                 "voice-install.json", "voice-tier.json")
# round 41 (.101 round 40 N19): --first-boot moved her phone packs (phone/packs: 78 files - every manifest, poster,
# loop and icon, 22 empty folders left), so her phone had nothing to play. They are the kit's installed content, never
# one of Setup's records. A folder here is kept whole. Kill CRISSA_FIRST_BOOT_KEEP_PHONE_PACKS=0 (round 40).
INSTALL_DIRS = (("phone", "packs"),)


def keep_phone_packs_on() -> bool:
    return os.environ.get("CRISSA_FIRST_BOOT_KEEP_PHONE_PACKS", "1").strip().lower() not in ("0", "off", "false", "no")


def keep_installs_on() -> bool:
    return os.environ.get("CRISSA_FIRST_BOOT_KEEP_INSTALLS", "1").strip().lower() not in ("0", "off", "false", "no")


def install_record(rel: str) -> bool:
    """Is this state-home path an install's (kept by a first boot), not one of Setup's records?"""
    parts = Path(rel).parts
    if not parts:
        return False
    name = parts[-1].lower()
    if keep_phone_packs_on() and any(tuple(parts[:len(d)]) == d and len(parts) > len(d) for d in INSTALL_DIRS):
        return True
    return (parts[0] in INSTALL_TOP or name in INSTALL_NAMES or name.endswith(("-install.json", "-installed.json",
                                                                               "-tier.json")))


def fresh_first_boot() -> dict[str, Any]:
    if crissa_home.is_house():
        return {"ok": False, "why": "never on the house"}
    st = load_state()
    if st.get("status") == "running":
        return {"ok": False, "why": "Setup is running now"}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = _home() / FIRST_BOOT_ARCHIVE / stamp
    rows: dict[str, Any] = {}
    moved = 0
    for i, root in enumerate(_snap_roots()):
        files = {r: v for r, v in _snap_files(root).items() if not (keep_installs_on() and install_record(r))}
        for rel in files:
            d = dest / str(i) / rel
            try:
                d.parent.mkdir(parents=True, exist_ok=True)
                os.replace(root / rel, d)
                moved += 1
            except OSError:
                pass
        rows[str(i)] = {"root": str(root), "files": sorted(files)}
    _write_json(dest / "manifest.json", {"at": _now(), "roots": rows,
                                          "undo": "move each file back from <i>/<path> to <root>/<path>"})
    _CANCELLED["home"] = ""
    print("[init] first boot from scratch: %d records moved to %s; the next start runs Setup" % (moved, dest),
          flush=True)
    return {"ok": True, "moved": moved, "archive": str(dest)}


def snapshot_before(run_id: str) -> dict[str, Any]:
    """A first boot's state home as its walk starts: every record copied aside (CANCEL_SNAP), with a manifest."""
    import shutil
    snap = _home() / CANCEL_SNAP
    if snap.exists():
        shutil.rmtree(snap, ignore_errors=True)
    rows: dict[str, Any] = {}
    n = 0
    for i, root in enumerate(_snap_roots()):
        files = _snap_files(root)
        for rel in files:
            dst = snap / str(i) / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, dst)
                n += 1
            except OSError:
                pass
        rows[str(i)] = {"root": str(root), "files": {k: list(v) for k, v in files.items()},
                        "dirs": sorted({str(Path(k).parent) for k in files} | {"."})}
    _write_json(snap / "manifest.json", {"run_id": run_id, "at": _now(), "roots": rows})
    print("[init] Setup's start: %d records set aside, so a cancel can leave nothing" % n, flush=True)
    return {"files": n}


CANCEL_SECOND_PASS_S = 15.0                 # the coach's routine may still write as its cancel lands: a second pass


def restore_before(run_id: str = "", keep: bool = False, under: Path | None = None) -> dict[str, Any]:
    """The state home put back as the walk found it (snapshot_before): what the walk wrote is removed, what it changed
    is restored. Returns counts by top-level name, for the log and the READY's list. `under`: only the records below
    that folder (a restart's people, A RESTART PUTS THE PEOPLE BACK FIRST)."""
    import shutil
    snap = _home() / CANCEL_SNAP
    man = _load_json(snap / "manifest.json")
    if not man or (run_id and man.get("run_id") != run_id):
        return {"restored": False}
    removed: dict[str, int] = {}
    put_back = 0
    for i, row in (man.get("roots") or {}).items():
        root = Path(row.get("root") or "")
        before = {k: tuple(v) for k, v in (row.get("files") or {}).items()}
        now = _snap_files(root)
        if under is not None:
            inside = lambda rel, r=root: _is_under(r / rel, under)   # noqa: E731
            before = {k: v for k, v in before.items() if inside(k)}
            now = {k: v for k, v in now.items() if inside(k)}
        for rel, sig in now.items():
            if rel in before:
                if sig != before[rel]:
                    try:
                        shutil.copy2(snap / i / rel, root / rel)
                        put_back += 1
                    except OSError:
                        pass
                continue
            try:
                (root / rel).unlink()
                top = Path(rel).parts[0] if len(Path(rel).parts) > 1 else rel
                removed[top] = removed.get(top, 0) + 1
            except OSError:
                pass
        for rel in before:
            if rel not in now:
                try:
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(snap / i / rel, root / rel)
                    put_back += 1
                except OSError:
                    pass
        kept: set[str] = set()
        for d in row.get("dirs") or []:
            q = Path(d)
            kept.update(str(x) for x in [q, *q.parents])
        for dirpath, dirs, files in os.walk(root, topdown=False):
            rel_dir = Path(dirpath).relative_to(root)
            if not rel_dir.parts or str(rel_dir) in kept:
                continue
            if under is not None and not _is_under(Path(dirpath), under):
                continue
            if rel_dir.parts[0] in CANCEL_SKIP_TOP or set(rel_dir.parts) & set(CANCEL_SKIP_ANY):
                continue
            try:
                os.rmdir(dirpath)                # empty folders the walk made
            except OSError:
                pass
    if not keep:
        shutil.rmtree(snap, ignore_errors=True)
    print("[init] %s: Setup's records removed (%s); %d put back as they were"
          % ("restarted (the people)" if under is not None else "cancelled",
             ", ".join("%s %d" % kv for kv in sorted(removed.items())) or "none", put_back), flush=True)
    return {"restored": True, "removed": removed, "put_back": put_back}


def _is_under(p: Path, top: Path) -> bool:
    try:
        Path(p).resolve().relative_to(Path(top).resolve())
        return True
    except (ValueError, OSError):
        return False


def drop_snapshot() -> None:
    import shutil
    shutil.rmtree(_home() / CANCEL_SNAP, ignore_errors=True)


def needed(st: Optional[dict[str, Any]] = None) -> bool:
    """A first boot: the switch on, a pack, and no marker (a finished or a cancelled walk writes one)."""
    if not serves():
        return False
    st = st if st is not None else load_state()
    if st.get("status") == "running":
        return True
    if _CANCELLED["home"] and _CANCELLED["home"] == str(_home()):
        return False                              # cancelled in this process: the NEXT start is the first boot
    return not marker_path().is_file()


# ── the identity store: the id module's tools when it is installed, the same shapes here when it is not ─────────────
_IDM: dict[str, Any] = {"tried": False, "mods": None}


def identity():
    """The id module's identity tools (crissa_id.pack_store, house_enroll.roles), or None without it."""
    if _IDM["tried"]:
        return _IDM["mods"]
    _IDM["tried"] = True
    if str(os.environ.get("CRISSA_INIT_WALKER") or "").strip().lower() in ("standin", "stand-in", "0", "off"):
        return None
    if not (ID_MODULE / "crissa_id" / "pack_store.py").is_file():
        return None
    try:
        import importlib
        import types
        if str(ID_MODULE) not in sys.path:
            sys.path.insert(0, str(ID_MODULE))
        _IDM["mods"] = types.SimpleNamespace(pack_store=importlib.import_module("crissa_id.pack_store"),
                                             roles=importlib.import_module("crissa_id.house_enroll.roles"))
    except Exception as e:  # noqa: BLE001  the records are written here then, in the same shapes
        print("[init] the id module did not import (%s: %s); its records are written here" % (type(e).__name__, e),
              flush=True)
    return _IDM["mods"]


def walker_name() -> str:
    return "crissa"


def person_id_for(display: str) -> str:
    """The coach's own id rule for a first name (house_enroll.person.format_person_id): lower case, spaces to _."""
    s = re.sub(r"\s+", "_", str(display or "").strip().lower())
    s = re.sub(r"[^a-z0-9_\-]+", "", s)
    s = re.sub(r"[_\-]{2,}", "_", s).strip("_-")
    return s[:48]


def ensure_site(person_id: str = "") -> Path:
    """identity/site.json, so the coach can load it: a minimal one when missing (never the example's placeholders),
    with enroll.user_id = the User once there is one."""
    root = identity_root()
    p = root / "site.json"
    d = _load_json(p) if p.is_file() else {"schema": "house_site/v1", "site_id": "crissa", "display": "This room"}
    en = d.setdefault("enroll", {}) if isinstance(d.get("enroll", {}), dict) else {}
    if person_id and (not en.get("user_id") or str(en.get("user_id")).startswith("REPLACE")):
        en["user_id"] = person_id
    _write_json(p, d)
    return p


def make_user(person_id: str, display: str) -> None:
    """After Register: the person is the User (person.json role "user"), site.json names them, user.json keeps the name.
    A visitor never is (ADD A VISITOR): their own Voice ID ends here as any does, and nothing is made of it."""
    if is_visitor(person_id):
        print("[init] %s is a visitor: never made the User" % person_id, flush=True)
        return
    mods = identity()
    root = identity_root()
    if mods is not None:
        try:
            mods.roles.mark_role(person_id, "user", root=root, display=display)
        except Exception as e:  # noqa: BLE001
            print(f"[init] mark the User: {type(e).__name__}: {e}", flush=True)
    else:
        pj = root / "people" / person_id / "person.json"
        rec = _load_json(pj)
        rec.update(id=person_id, display=rec.get("display") or display, role="user",
                   schema=rec.get("schema") or "house_person/v1", active=True)
        rec.setdefault("tier", "command")
        _write_json(pj, rec)
    ensure_site(person_id)
    u = _load_json(user_path())
    u.update(schema=USER_SCHEMA, name=display, person_id=person_id, role="user", at=_now())
    u.setdefault("created_at", _now())
    _write_json(user_path(), u)


def sync_faces(person_id: str) -> int:
    """enroll.json from the identity store's AuraFace prints of the User (the shape lib/crissa_presence and the oracle
    read). Returns how many faces it holds. A visitor's faces stay in their own folder (presence reads every person's
    there): enroll.json is the owner's alone, so it is never written for them (ADD A VISITOR)."""
    if is_visitor(person_id):
        return face_prints(person_id)
    mods = identity()
    if mods is not None:
        try:
            return int(mods.pack_store.sync_from_store(person_id, identity_root()) or 0)
        except Exception as e:  # noqa: BLE001
            print(f"[init] enroll.json: {type(e).__name__}: {e}", flush=True)
            return 0
    faces = []
    for f in sorted((identity_root() / "people" / person_id / "face").glob("*.json")):
        d = _load_json(f)
        enc = d.get("encoding") or d.get("embedding")
        if isinstance(enc, list) and len(enc) >= 8 and str(d.get("model") or "").startswith("auraface"):
            faces.append([float(x) for x in enc])
    if faces:
        _write_json(enroll_path(), {"schema": "crissa-enroll/1", "owner": person_id, "faces": faces[-12:],
                                    "updated_at": _now()}, mode=0o600)
    return min(len(faces), 12)


# ── ADD A VISITOR (round 20, the User 2026-10-14: "Do we have a routine for face ID as well baked into voice? We need to
# for added visitors") ─────────────────────────────────────────────────────────────────────────────────────────────────
# The Crissa menu's Add a visitor runs the SAME Voice ID - the coach's spoken counts with a face still on each, then Face
# photos for an angle still missing - for a NEW person kept as a visitor: person.json role "guest", kind "visitor", in
# the same identity store, so the ear's voice bank and presence's faces (both read every person there) name them. A
# visitor is never the User: make_user refuses them, sync_faces never writes them into the owner's enroll.json, and the
# owner's voice floor is never theirs; their prints only name them on the log and in her context ("probably <name>").
# Who may start it is the menu's face rule (lib/crissa_home face_gate: Security On or above, the User's face; ID-only and
# Off, anyone at the computer). Outside Security On a visitor's take may run without User mode: the coach, the routine
# and presence each ask THIS player first (GET /v1/init `visitor`), and only a live visitor run for exactly that person
# vouches. The menu lists visitors and removes one (their prints and stills with them). Kill: CRISSA_ADD_VISITOR=0.
# Never on the house.
VISITOR_KIND = "visitor"


def visitor_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_ADD_VISITOR", "1")).strip().lower() not in ("0", "off", "false", "no")


def _person_rec(pid: str) -> dict[str, Any]:
    pid = str(pid or "").strip().lower()
    return _load_json(identity_root() / "people" / pid / "person.json") if pid else {}


def is_visitor(pid: str) -> bool:
    """A person Add a visitor made (person.json kind "visitor")."""
    return str(_person_rec(pid).get("kind") or "") == VISITOR_KIND


def _owner_id() -> str:
    return str(_load_json(user_path()).get("person_id") or "").strip().lower()


def visitor_id_for(display: str) -> str:
    """A visitor's person id: the first name's id (person_id_for), unless someone who is not this visitor holds it - the
    User above all - then the first free <id>_2, <id>_3... A visitor already kept under the same name is the same
    visitor (Add a visitor again adds to their prints)."""
    first = (str(display or "").split(" ") or [""])[0]
    base = person_id_for(first)
    owner = _owner_id()
    want = str(display or "").strip().lower()
    pid, n = base, 1
    while True:
        rec = _person_rec(pid)
        mine = (str(rec.get("kind") or "") == VISITOR_KIND
                and str(rec.get("display") or "").strip().lower() == want)
        free = not (identity_root() / "people" / pid).exists()
        if pid != owner and (mine or free):
            return pid
        n += 1
        pid = "%s_%d" % (base, n)


def _mark_visitor(pid: str, display: str) -> None:
    """people/<pid>/person.json says visitor before the coach makes the person (ensure_person keeps what is there)."""
    mods = identity()
    if mods is not None:
        try:
            mods.roles.mark_role(pid, "guest", root=identity_root(), display=display)
        except Exception as e:  # noqa: BLE001
            print(f"[init] mark the visitor: {type(e).__name__}: {e}", flush=True)
    pj = identity_root() / "people" / pid / "person.json"
    rec = _load_json(pj)
    rec.update(id=pid, display=rec.get("display") or display, role="guest", kind=VISITOR_KIND,
               schema=rec.get("schema") or "house_person/v1", active=True)
    rec.setdefault("tier", "guest")
    rec.setdefault("added_at", _now())
    _write_json(pj, rec)
    for sub in ("voice", "face"):
        (pj.parent / sub).mkdir(parents=True, exist_ok=True)


def list_visitors() -> list[dict[str, Any]]:
    """[{person_id, display, voice, face}] for every visitor, by name."""
    out = []
    d = identity_root() / "people"
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        rec = _load_json(p / "person.json")
        if p.is_dir() and str(rec.get("kind") or "") == VISITOR_KIND:
            out.append({"person_id": p.name, "display": str(rec.get("display") or p.name),
                        "voice": voice_prints(p.name), "face": face_prints(p.name),
                        "access": access_of(p.name)})
    return sorted(out, key=lambda r: r["display"].lower())


# A PERSON'S ACCESS (round 41, .101 round 40 F2, the User: "Edit access in guest list"; for launch two levels only,
# "Guest" - "small talk and the weather, nothing personal" - and "User access" - "can do everything you can, including
# changes"). Kept on the person's own person.json (`access`), set on the Edit guest list under his proof. Every kept
# person is a Guest unless he says otherwise, which is what the ear does for anyone who is not him today. The ear does
# NOT act on "user" yet: the level can be set only while CRISSA_GUEST_USER_ACCESS=1 (default off) - a change to who may
# act for him, waiting on the User's word.
ACCESS_LEVELS = ("guest", "user")
ACCESS_LABELS = {"guest": "Guest", "user": "User access"}
ACCESS_HELP = {"guest": "small talk and the weather, nothing personal",
               "user": "can do everything you can, including changes"}


def user_access_on() -> bool:
    return os.environ.get("CRISSA_GUEST_USER_ACCESS", "0").strip().lower() in ("1", "on", "true", "yes")


def access_of(pid: str) -> str:
    got = str(_person_rec(pid).get("access") or "guest").strip().lower()
    return got if got in ACCESS_LEVELS and (got == "guest" or user_access_on()) else "guest"


def set_access(person_id: str, level: str) -> dict[str, Any]:
    pid = str(person_id or "").strip().lower()
    level = str(level or "").strip().lower()
    if level not in ACCESS_LEVELS:
        return {"ok": False, "error": "Access is Guest or User access."}
    if level == "user" and not user_access_on():
        return {"ok": False, "error": "User access for another person is not switched on here."}
    if not pid or pid == _owner_id() or not is_visitor(pid):
        return {"ok": False, "error": "%s is not on the guest list." % (pid or "nobody")}
    pj = identity_root() / "people" / pid / "person.json"
    rec = _load_json(pj)
    rec["access"] = level
    _write_json(pj, rec)
    print("[init] guest list: %s's access is %s" % (pid, ACCESS_LABELS[level]), flush=True)
    return {"ok": True, "person_id": pid, "access": level, "visitors": list_visitors()}


def visitor_run(st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The visitor this run adds ({person_id, display}), {} when it is not Add a visitor."""
    st = st if st is not None else load_state()
    v = st.get("visitor")
    return dict(v) if isinstance(v, dict) and v.get("person_id") else {}


def start_visitor(name: str) -> dict[str, Any]:
    """ADD A VISITOR from the Crissa menu (the face rule already applied there): Voice ID, then Face photos for an angle
    still missing, for a new person kept as a visitor. Refused while a walk runs, before Setup has a User, and off."""
    if crissa_home.is_house():
        return {"ok": False, "error": "the house has its own Register"}
    if not visitor_on():
        return {"ok": False, "error": "Add a visitor is switched off on this computer."}
    st = load_state()
    if st.get("status") == "running":
        return dict(snapshot(st), ok=False, error="Setup is already running: finish it there first.")
    if not _owner_id():
        return {"ok": False, "error": "Run Setup first: Crissa adds visitors once she knows you."}
    display = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
    if not looks_like_a_name(display):
        return {"ok": False, "error": "Type the visitor's name. A first name is fine.", "name_error": True}
    seq = steps()
    i = next((k for k, s_ in enumerate(seq) if s_.get("id") == "voice_id"), None)
    if i is None:
        return dict(snapshot(st), ok=False, error="this install's walk has no Voice ID step")
    only = person_steps(seq, i, face=True)
    pid = visitor_id_for(display)
    _mark_visitor(pid, display)
    new = empty_state()
    new.update(status="running", run_id=uuid.uuid4().hex[:12], index=i, step_id="voice_id", started_at=_now(),
               step_at=time.time(), first_boot=False, only=only, connect=dict(st.get("connect") or {}),
               again={"replace": False, "proof_hold": False, "led_by": ""},
               visitor={"person_id": pid, "display": display, "at": _now()})
    save_state(new)
    print("[init] Add a visitor: %s (%s), %s" % (display, pid, " then ".join(only)), flush=True)
    if one_voice_id_on():
        _person_begin(new["run_id"])             # round 21: Lila on her plate with her lips, as for Voice ID
    _enter(new["run_id"])
    got = reg_name(_reg_io(new["run_id"], "voice_id", "register"), display)
    out: dict[str, Any] = {"visitor": {"person_id": pid, "display": display}}
    if not got.get("ok"):
        out["name_error"] = str((load_state().get("register") or {}).get("name_error") or got.get("error") or "")
    return dict(snapshot(load_state()), ok=True, **out)


def _drop_index_row(pid: str) -> None:
    """A person's row out of the identity index: people/_index.json (the store's own index, the one the ear and presence
    read), and the old identity/index.json this module once wrote (round 41, .101 round 40 F1: a removal left
    `{"id": "ted", "active": true}` in people/_index.json, so the store still listed him)."""
    for idx in (identity_root() / "people" / "_index.json", identity_root() / "index.json"):
        rows = _load_json(idx)
        if isinstance(rows.get("people"), list):
            keep = [r for r in rows["people"] if str((r or {}).get("id") or "").lower() != pid]
            if len(keep) != len(rows["people"]):
                rows["people"] = keep
                _write_json(idx, rows)


# A CHANGED STORE IS READ AT ONCE (round 41, .101 round 40 F1: after ted was removed, presence kept him in its face bank
# and every check of the User read ted until presence was restarted): a removal, a forgotten person and a cancelled run
# put back tell presence to drop its bank, which it re-reads on its next look. The ear reads the voice prints from disk
# on every match (crissa_ear.voice_bank), so it needs no word. Kill CRISSA_PEOPLE_RELOAD=0.
def people_reload_on() -> bool:
    return os.environ.get("CRISSA_PEOPLE_RELOAD", "1").strip().lower() not in ("0", "off", "false", "no")


def reload_banks(why: str) -> bool:
    if not people_reload_on():
        return False
    return _reload_post(why)


def _reload_post(why: str) -> bool:
    try:
        req = urllib.request.Request(crissa_ports.url("presence") + "/v1/people/reload",
                                     data=json.dumps({"why": str(why)[:80]}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with crissa_egress.urlopen(req, 4.0, what="Setup: the face bank re-reads the people") as r:
            ok = bool(json.loads(r.read(20000).decode("utf-8", "replace") or "{}").get("ok"))
    except Exception as e:  # noqa: BLE001  presence down: it reads the store when it starts
        print("[init] the face bank was not told (%s): it re-reads the people on its own" % type(e).__name__, flush=True)
        return False
    print("[init] the face bank re-reads the people (%s)" % why, flush=True)
    return ok


def remove_visitor(person_id: str) -> dict[str, Any]:
    """Forget a visitor: their folder (voice prints, face stills, person.json) and their row in the identity index.
    Never the User, never anyone not made by Add a visitor, never a visitor being added now."""
    if crissa_home.is_house():
        return {"ok": False, "error": "the house keeps its own identity system"}
    if not visitor_on():
        return {"ok": False, "error": "Add a visitor is switched off on this computer."}
    pid = str(person_id or "").strip().lower()
    people = identity_root() / "people"
    d = people / pid
    if not pid or pid != person_id_for(pid) or pid == _owner_id() or not is_visitor(pid):
        return {"ok": False, "error": "%s is not a visitor here." % (pid or "nobody")}
    display = str(_person_rec(pid).get("display") or pid)
    st = load_state()
    if st.get("status") == "running" and visitor_run(st).get("person_id") == pid:
        if not (one_voice_id_on() and remove_ends_run_on()):
            return {"ok": False, "error": "That visitor is being added right now: let it finish first."}
        # A RUN NEVER HOLDS A REMOVE FOR EVER (round 21, .101 round 20: the visitor's Face photos card waited in a closed
        # window, and Remove said "let it finish first" until the run was aborted by hand): the User asked to forget
        # this person, so adding them ends here, then they are removed. Kill CRISSA_REMOVE_ENDS_RUN=0.
        print("[init] Remove %s: their Voice ID was still running; it ends first" % pid, flush=True)
        abort(str(st.get("run_id") or ""), why="Adding this person was stopped: they are being removed.")
    if not d.exists():                            # round 41 (F6): the run's cancel already forgot the person it made
        _drop_index_row(pid)
        print("[init] visitor %s (%s) removed, with their prints and stills" % (display, pid), flush=True)
        reload_banks("%s removed" % pid)
        return {"ok": True, "removed": pid, "display": display, "visitors": list_visitors()}
    try:
        if d.resolve().parent != people.resolve():
            return {"ok": False, "error": "%s is not a visitor here." % pid}
        import shutil
        shutil.rmtree(d)
    except OSError as e:
        return {"ok": False, "error": "could not remove %s (%s)" % (pid, type(e).__name__)}
    _drop_index_row(pid)
    print("[init] visitor %s (%s) removed, with their prints and stills" % (display, pid), flush=True)
    reload_banks("%s removed" % pid)
    return {"ok": True, "removed": pid, "display": display, "visitors": list_visitors()}


# ── ONE VOICE ID ROUTINE (round 21, the User 2026-10-14, .101 round 20: "one Voice ID routine everywhere"; "Voice ID and
# Add a visitor become one button"; "Only the User can register them, but never as the User") ────────────────────────────
# Setup's Voice ID, the menu's Voice ID for the User and Add a visitor walk the same piece: Lila on her plate with her
# lips (her lines warmed, her walk on before the first cue and off at the end: _person_begin), her cues and the four
# counts with a face still on each, then Face photos for an angle still missing - starting by itself as in Setup, never
# a Start on a card (face_enter). Round 20's visitor run had neither Lila (Crissa stood on screen under Lila's voice)
# nor the start by itself (its Face photos card waited for Start in a closed window).
# NOTHING WAITS ON A CARD NOBODY CAN SEE (person_guard): a run started from the menu has no Setup window - hers closed
# at Setup's end - so whenever the run sits on a card that needs an answer (Voice ID: name, partial, failed, no coach;
# Face photos: ask, failed, no person) her window is opened, or brought forward, on that card (/init). A card that
# cannot be shown (no browser, no screen, her window off) is answered for the person at once - Face photos skipped
# with the reason, a partial Voice ID kept, a failed or nameless one ended - and a shown card nobody answers in
# CARD_WAIT_MAX_S ends the same way. An ended run says why on both Walls' Voice ID plate (_say_ended). Who may start
# it is the menu's (lib/crissa_menu person_gate). Kill: CRISSA_ONE_VOICE_ID=0 (round 20's two runs: Voice ID alone for
# the User, no Lila on the visitor's run, no guard).
CARD_WAIT_MAX_S = float(os.environ.get("CRISSA_CARD_WAIT_MAX_S", "180") or 180)
GUARD_POLL_S = 1.0
GUARD_SETTLE_S = 2.0                       # a card that moves on by itself within this is never shown or answered
ENDED_SHOW_S = 12.0                        # an ended run's line stays on the Voice ID plate this long
WAITING_STATES = {"register": ("name", "partial", "failed", "no_coach"), "face": ("ask", "failed", "no_person")}
STEP_KEYS = {"voice_id": "register", "face": "face"}


def one_voice_id_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_ONE_VOICE_ID", "1")).strip().lower() not in ("0", "off", "false", "no")


def person_run(st: dict[str, Any] | None = None) -> bool:
    """A run of the one routine from the Crissa menu (Voice ID for the User, or Add a visitor), not Setup's own walk."""
    if not one_voice_id_on():
        return False
    st = st if st is not None else load_state()
    return bool(st.get("only")) and st.get("status") == "running"


def person_steps(seq: list, i: int, face: bool | None = None) -> list[str]:
    """The run's steps: Voice ID, and Face photos when the walk has it right after (a visitor always, round 20; the
    User's own since round 21: Setup's portion, the stills the counts did not give)."""
    want_face = one_voice_id_on() if face is None else bool(face)
    has_face = i + 1 < len(seq) and seq[i + 1].get("id") == "face"
    return ["voice_id"] + (["face"] if (want_face and has_face) else [])


def remove_ends_run_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_REMOVE_ENDS_RUN", "1")).strip().lower() not in ("0", "off", "false", "no")


def player_base(env: dict | None = None) -> str:
    """This install's player as the launcher handed it to every child (CRISSA_PLAYER, lib/crissa_ports ADDRESS_KEYS),
    for a process where the player never configured Init (the desk Wall, the launcher, a hand's abort): '' when none -
    never the port table's default, which beside another install is that install's player (round 19's lesson)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PLAYER") or e.get("HOUSE_WALL_CONDUCTOR") or "").strip().rstrip("/")


def _person_begin(run_id: str) -> None:
    """The routine's shared beginning for a menu run: Lila's lines warmed, Lila onto her plate with her lips (round 20's
    Voice ID again; the visitor's run since round 21), and the card guard."""
    if two_voices_on() and not camera_voice_on():
        warm_cues()                              # round 20: her lines, from the disk cache when Setup rendered them
    if lila_hosts_again():
        # round 20 (TWO VOICES): Lila on her plate, her voice and lips on every cue, as in Setup ("Lila on screen with
        # voice of Lila and lips would be best"); she walks on before the coach's first cue and off when the run ends
        _HOST["gen"] += 1
        _spawn("lila-again", _host_run, run_id, "voice_id", [], _HOST["gen"])
    if one_voice_id_on() and not SYNC:           # its own thread, never inline (tests tick it: person_guard_tick)
        threading.Thread(target=person_guard, args=(run_id,), name="init-person-guard", daemon=True).start()


def waiting_card(st: dict[str, Any]) -> tuple[str, str] | None:
    """(step, state) when the run sits on a card that needs the person's answer, else None."""
    step = str(st.get("step_id") or "")
    key = STEP_KEYS.get(step)
    if not key:
        return None
    state = str((st.get(key) or {}).get("state") or "")
    return (step, state) if state in WAITING_STATES[key] else None


def show_card(run_id: str = "") -> dict[str, Any]:
    """Her window on the run's card (/init), opened or brought forward (lib/crissa_window.open_page: her own instance,
    never the person's browser). {"ok", "why"}."""
    base = str(_CFG.get("base") or "") or player_base()
    if not base:
        return {"ok": False, "why": "no player address"}
    url = base.rstrip("/") + "/init"
    try:
        import crissa_window
        if not crissa_window.enabled():
            return {"ok": False, "why": "her window is off here"}
        got = crissa_window.open_page(url, front=True)
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:80])}
    if got.get("ok"):
        mark_browser_opened(url, by="window")
    return {"ok": bool(got.get("ok")), "why": str(got.get("message") or got.get("why") or "")}


def resolve_unseen(run_id: str, card: tuple[str, str], why: str) -> str:
    """Answer a card for the person when it cannot be seen or is not answered: what was done, in a word."""
    step, state = card
    if step == "face":
        io = _reg_io(run_id, "face", "face")
        if state == "ask":
            face_act(io, "start")
            return "face started"
        face_act(io, "continue" if state == "no_person" else "skip")
        line = "Face photos were skipped: %s. Voice ID kept what it took; the face photos can be taken again." % why
        with _LOCK:
            cur = load_state()
            if cur.get("run_id") == run_id:
                _note(cur, line)
                save_state(cur)
        _say_ended(line)
        return "face skipped"
    io = _reg_io(run_id, "voice_id", "register")
    if state == "partial":
        reg_act(io, "continue")                      # what was taken is kept, as Continue keeps it
        return "partial kept"
    abort(run_id, why="Voice ID stopped: %s. Start it again from the Crissa menu." % why)
    return "ended"


def person_guard_tick(run_id: str, mem: dict[str, Any], now: float | None = None) -> str:
    """One look at the run: '' (nothing waits), 'shown' (her window is on the card now), 'resolved:<what>', 'ended'."""
    st = load_state()
    if st.get("run_id") != run_id or st.get("status") != "running":
        return "ended"
    card = waiting_card(st)
    t = time.time() if now is None else float(now)
    if card is None:
        mem.clear()
        return ""
    if mem.get("card") != card:                  # a card just came up: it may move on by itself (a name taken at once)
        mem.clear()
        mem.update(card=card, since=t)
        return ""
    if "shown" not in mem:
        if t - float(mem["since"]) < GUARD_SETTLE_S:
            return ""
        got = show_card(run_id)
        mem.update(shown=bool(got.get("ok")), why=str(got.get("why") or ""), shown_at=t)
        print("[init] Voice ID's card %s/%s waits for an answer: %s" % (
            card[0], card[1], "her window shows it" if mem["shown"] else "it cannot be shown (%s)" % mem["why"]),
            flush=True)
        if not mem["shown"]:
            return "resolved:" + resolve_unseen(run_id, card, "its card could not be shown (%s)" % (
                mem["why"] or "no window"))
        return "shown"
    if t - float(mem.get("shown_at") or t) >= CARD_WAIT_MAX_S:
        return "resolved:" + resolve_unseen(run_id, card, "nobody answered its card in %d s" % int(CARD_WAIT_MAX_S))
    return ""


def person_guard(run_id: str) -> None:
    mem: dict[str, Any] = {}
    while True:
        time.sleep(GUARD_POLL_S)
        try:
            if person_guard_tick(run_id, mem) == "ended":
                return
        except Exception as e:  # noqa: BLE001  the guard never takes the run down with it
            print("[init] Voice ID's guard: %s: %s" % (type(e).__name__, str(e)[:120]), flush=True)


def _say_ended(line: str) -> None:
    """The line a menu run ended with, on both Walls' Voice ID plate for ENDED_SHOW_S, then the plate goes."""
    set_register_glass({"phase": "ended", "text": str(line)[:240], "verbal": True})
    if SYNC:
        return

    def clear() -> None:
        if str(_GLASS.get("phase") or "") == "ended" and str(_GLASS.get("text") or "") == str(line)[:240]:
            set_register_glass({"phase": "idle"})
    t = threading.Timer(ENDED_SHOW_S, clear)
    t.daemon = True
    t.start()


# ── the coach (Register), over its loopback ──────────────────────────────────────────────────────────────────────────
def _coach_post(path: str, body: dict[str, Any], timeout: float = 12.0) -> dict[str, Any]:
    """The coach's answer as a dict with `_ok` (it answered) and `_error`; a 4xx with a JSON body is an answer."""
    req = urllib.request.Request(enroll_url() + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return _coach_open(req, timeout)


def _coach_get(path: str, timeout: float = 4.0) -> dict[str, Any]:
    return _coach_open(enroll_url() + path, timeout)


def _coach_open(req, timeout: float) -> dict[str, Any]:
    try:
        with crissa_egress.urlopen(req, timeout, what="init: the coach") as r:
            raw = r.read(64000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(8000).decode("utf-8", "replace")
            d = json.loads(raw)
            return dict(d, _ok=True, _status=e.code) if isinstance(d, dict) else {"_ok": False, "_error": "HTTP %d" % e.code}
        except (ValueError, OSError):
            return {"_ok": False, "_error": "HTTP %d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"_ok": False, "_error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return {"_ok": False, "_error": "the coach answered something that is not JSON"}
    return dict(d, _ok=True) if isinstance(d, dict) else {"_ok": False, "_error": "not an object"}


def face_prints(person_id: str) -> int:
    """Face records in the identity store that carry an embedding. A snap whose embed failed is a record with no
    numbers (the User 2026-10-10, .101: the coach's count said 21 - every JSON, JPEG and still - with 0 prints)."""
    n = 0
    for f in sorted((identity_root() / "people" / person_id / "face").glob("*.json")) if person_id else []:
        enc = _load_json(f).get("encoding") or _load_json(f).get("embedding")
        if isinstance(enc, list) and len(enc) >= 8:
            n += 1
    return n


def voice_prints(person_id: str) -> int:
    """Voice records in the identity store that carry an embedding (quarantined quiet takes are in a subfolder)."""
    n = 0
    for f in sorted((identity_root() / "people" / person_id / "voice").glob("*.json")) if person_id else []:
        enc = _load_json(f).get("encoding")
        if isinstance(enc, list) and len(enc) >= 8:
            n += 1
    return n


def _counts(person_id: str) -> dict[str, int]:
    """{voice, face} prints the identity store holds for the person: voice from the coach's /people (its live-encoder
    count), else the files; face ALWAYS from the files, counting only records with an embedding (face_prints)."""
    got = _coach_get("/people")
    for p in (got.get("people") or []) if got.get("_ok") else []:
        if str(p.get("id") or "") == person_id:
            return {"voice": int(p.get("voice") or 0), "face": face_prints(person_id)}
    d = identity_root() / "people" / person_id
    return {"voice": len(list((d / "voice").glob("*.json"))) if (d / "voice").is_dir() else 0,
            "face": face_prints(person_id)}


# ── the camera ───────────────────────────────────────────────────────────────────────────────────────────────────────
class CameraOff(OSError):
    """CRISSA_NO_CAMERA=1: this install has no camera; nothing was opened or asked."""


def camera_off(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on")


def camera_url() -> str:
    for key in ("CRISSA_CAMERA_URL", "RAIL_CAMERA_URL"):
        got = os.environ.get(key, "").strip()
        if got:
            return got.rstrip("/")
    return crissa_ports.url("camera")


def grab_frame(timeout: float = 4.0) -> bytes:
    """One JPEG from this install's camera. CRISSA_NO_CAMERA=1: CameraOff, and nothing is opened or asked (2026-10-10:
    this used to open a capture device whatever the switch said). Else the room camera's grabber (one owner of the
    device: CRISSA_CAMERA_URL / RAIL_CAMERA_URL, else the port table's camera, GET /snapshot); only when no grabber
    answers at all is the device opened here (CRISSA_CAMERA, default 0). Raises OSError on no picture."""
    if camera_off():
        raise CameraOff("camera: off")
    try:
        with crissa_egress.urlopen(camera_url() + "/snapshot", timeout, what="init: the camera") as r:
            data = r.read()
        if data[:2] == b"\xff\xd8":
            return data
        raise OSError("the camera's grabber gave no picture")
    except urllib.error.HTTPError as e:
        raise OSError("the camera's grabber answered HTTP %d" % e.code) from e
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ConnectionRefusedError):
            raise OSError("the camera's grabber did not answer (%s)" % getattr(e, "reason", e)) from e
    import cv2                                     # no grabber on this machine: the device itself, as before
    idx = os.environ.get("CRISSA_CAMERA", "0").strip()
    cap = cv2.VideoCapture(int(idx) if idx.isdigit() else idx)
    try:
        frame = None
        for _ in range(8):                         # the first frames of a webcam are often dark: let it settle
            ok, fr = cap.read()
            if ok and fr is not None:
                frame = fr
        if frame is None:
            raise OSError("the camera gave no picture")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise OSError("the picture could not be encoded")
        return bytes(buf)
    finally:
        cap.release()


# ── the snapshot (the glass) ─────────────────────────────────────────────────────────────────────────────────────────
def _connect_view() -> dict[str, Any]:
    try:
        import crissa_agent as za
        cfg = za.redacted(za.load())
        offers = [{k: o.get(k) for k in ("id", "name", "url", "model", "by", "has_key", "no_prompt")}
                  for o in za.offers() if o.get("ok")]
        return {"source": "connect", "url": cfg.get("url") or "", "model": cfg.get("model") or "",
                "preset": cfg.get("preset") or "", "has_key": bool(cfg.get("has_key")), "editable": True,
                "offers": offers, "key_ref": bool(cfg.get("key_ref")), "configured": bool(cfg.get("configured")),
                # the kinds Connect offers (round 17: the Claude kind only while CRISSA_CONNECT_CLAUDE_PRESET is on)
                "kinds": {k: {"url": v["url"], "model": v["model"], "label": v["label"]} for k, v in za.kinds().items()}}
    except Exception as e:  # noqa: BLE001
        return {"source": "connect", "url": "", "model": "", "has_key": False, "editable": True,
                "error": f"{type(e).__name__}: {e}"[:120]}


def _plate(st: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    kind = cfg.get("kind") or ""
    p: dict[str, Any] = {"eyebrow": cfg.get("eyebrow") or "", "kind": kind, "pack": True}
    if kind == "connect":
        last = connect_last(st)
        p.update(title=cfg.get("title") or "", body=cfg.get("body") or "", connect=_connect_view(),
                 tested=connect_passed(st), last=last, later=not connect_required(),
                 later_ask=bool((st.get("connect") or {}).get("later_ask")))
    elif kind == "machine":
        m = st.get("machine") or {}
        vt = st.get("voice_tier") or {}
        vi = {k: (st.get("voice_install") or {}).get(k) for k in ("state", "engine", "lines", "line")}
        view = card_view(m, vi)
        if view["state"] == "ask" and setup_parallel(st):
            view["lines"] = list(view["lines"]) + [PARALLEL_LINE]      # SETUP IN PARALLEL: what the one click starts
        if view["state"] == "ask" and setup_fold_on(st):
            view.update(fold_view(st))                                 # round 20: the name and the mic on this card
        p.update(title=view["title"], body=view["body"],
                 machine=dict(view, tier=m.get("tier"), reasons=m.get("reasons") or [], error=m.get("error"),
                              permission=m.get("permission")),
                 voice_install=vi, aligner=dict(st.get("aligner") or {}),
                 language={"code": vt.get("language") or "en", "line": next(
                     (x for x in (vt.get("say") or []) if "language:" in x), ""),
                     "error": vt.get("language_error") or "", "faces_ok": vt.get("faces_ok", True)})
    elif kind == "register":
        reg = st.get("register") or {}
        view = reg_view(reg, cfg)
        p.update(title=view["title"], body=view["body"], ask=cfg.get("ask") or "", name=reg.get("display") or "",
                 name_fixed=False, name_error=reg.get("name_error") or "", keys=view["keys"], mic=view.get("mic"),
                 cues=cues_mode(),
                 register=dict({k: reg.get(k) for k in ("state", "display", "person_id", "error", "voice", "face",
                                                         "sent_at", "pending", "sent", "done")},
                               driver="walker", hint=view["hint"], glass=view.get("glass") or {}))
    elif kind == "room":
        rm = st.get("room") or {}
        view = room_view(rm, cfg)
        p.update(title=view["title"], body=view["body"], hint=view["hint"], keys=view["keys"],
                 room={k: view.get(k) for k in ("state", "marks", "now", "order", "labels", "asks", "changed",
                                                "picture", "camera", "camera_url", "defaults", "computer",
                                                "door_unreachable")})
        if view.get("internal"):
            p["room"]["internal"] = view["internal"]        # the speakers' Internal tick (round 17), only while on
    elif kind == "hours":
        hr = st.get("hours") or {}
        view = hours_view(hr, cfg)
        p.update(title=view["title"], body=view["body"], hint=view["hint"], keys=view["keys"],
                 hours={k: view.get(k) for k in ("state", "overnight", "quiet", "defaults", "lines")})
        if st.get("ending"):                     # THE TEAM AT THE END: the last card stays, with no keys, while they speak
            p.update(body=(str(p.get("body") or "") + " Lila introduces the team.").strip(), keys=[])
    elif kind == "face":
        fc = st.get("face") or {}
        view = face_view(fc, cfg)
        p.update(title=view["title"], body=view["body"], keys=view["keys"],
                 face=dict({k: fc.get(k) for k in ("state", "why", "person_id", "display", "progress", "faces",
                                                    "error", "camera", "until")}, hint=view["hint"],
                           counter=view.get("counter")))
    return p


# A WAY OUT AT EVERY STEP (round 34, .101 round 33 on a Setup page's Voice ID step: "I can't escape anymore" - the
# classic page's Cancel was a small link under the steps list, below her picture, out of sight; .101 ended the walk by
# POST). The page draws a way-out bar that is always in view, at every step of both its forms (the cards and the one
# page): "Cancel setup" while a walk runs (asked first, then the walk's abort), and Esc does the same; with no walk
# running it is "Back to the Wall". The desk Wall's own plates keep their CANCEL key. A pack only (the house's Init
# page looks as it did). Kill CRISSA_SETUP_WAY_OUT=0.
def way_out_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_SETUP_WAY_OUT", "1").strip().lower() not in ("0", "off", "false", "no")


def _shown_steps() -> list[dict[str, Any]]:
    """The steps the page lists: no "Face photos" when the counts carry the photos (round 35, .101 round 34: /v1/init still
    named it, step 4 of 6). The walk keeps the step itself: it only reads the bank and syncs his face list."""
    return [s for s in steps() if not (s.get("id") == "face" and no_face_section())]


def _shown_index(st: dict[str, Any]) -> int:
    ids = [s["id"] for s in _shown_steps()]
    sid = str(st.get("step_id") or "")
    if sid in ids:
        return ids.index(sid)
    done = set(st.get("steps_done") or [])
    return sum(1 for i in ids if i in done)


# A STEP THAT IS CURRENT IS ENTERED (round 40, .101 round 38 N20: Voice ID sat current - index 2, `register: {}` -
# with no reg_enter, no film cue and no recording; the page showed "Up next" with no way on). A step whose own record is
# still empty STALL_S after it became current is entered again, once a step, and the log says so. Never before an IDs
# run's Ready (nothing is entered there on purpose), never while the team speaks. Kill CRISSA_REENTER_STALLED=0.
STALL_S = 8.0
_STALL_KEY = {"key": ""}
_STEP_RECORD = {"register": "register", "face": "face", "room": "room", "hours": "hours"}


def _reenter_stalled(st: dict[str, Any], now: Optional[float] = None) -> bool:
    if os.environ.get("CRISSA_REENTER_STALLED", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    if st.get("status") != "running" or st.get("ending"):
        return False
    if (st.get("ids") or {}).get("stage") in ("ready_user", "handover"):
        return False
    sid = str(st.get("step_id") or "")
    rec_key = _STEP_RECORD.get(str(step_cfg(sid).get("kind") or ""))
    if not rec_key or (st.get(rec_key) or {}):
        return False
    t = time.time() if now is None else now
    if t - float(st.get("step_at") or t) < STALL_S:
        return False
    key = "%s:%s:%s" % (st.get("run_id") or "", sid, st.get("step_at") or "")
    if _STALL_KEY["key"] == key:
        return False                              # once a step: a second stall says itself in the record it writes
    _STALL_KEY["key"] = key
    print("[init] %s was current %.0f s and never entered: entering it now" % (sid, t - float(st.get("step_at") or t)),
          flush=True)
    _spawn("reenter-" + sid, _enter, str(st.get("run_id") or ""))
    return True


def snapshot(st: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    st = st if st is not None else load_state()
    running = st.get("status") == "running"
    sid = st.get("step_id") or ""
    cfg = step_cfg(sid) if running else {}
    _her_stream_full(st)                        # round 38: Lila's films on Crissa's band (at most every 2 s)
    _reenter_stalled(st)                        # round 40 (N20): a current step never entered is entered
    return {
        "ok": True, "v2": True, "pack": True, "walker": walker_name(),
        "needed": needed(st),
        "status": st.get("status") or "idle",
        "run_id": st.get("run_id") or "",
        "index": _shown_index(st) if running else 0,
        "total": len(_shown_steps()),
        "steps": [{"id": s["id"], "eyebrow": s.get("eyebrow") or ""} for s in _shown_steps()],
        "step_id": sid if running else "",
        "step_at": float(st.get("step_at") or 0.0) if running else 0.0,
        "kind": cfg.get("kind") or "",
        "first_boot": bool(st.get("first_boot")) if running else False,
        "plate": _plate(st, cfg) if running else {},
        "note": (st.get("notes") or [""])[-1] if st.get("notes") else "",
        "camera_token": False,
        "camera": "off" if camera_off() else "on",
        "landing": st.get("landing") or {},
        "done": done_view(st) if st.get("status") == "done" else {},
        "failed": dict(st.get("failed") or {}) if st.get("status") == "failed" else {},   # round 20: a failed install
        "marker": {k: marker().get(k) for k in ("done", "at", "version")},
        "setup_again": not no_setup_again_on(),   # round 34: no "Set up again" on a pack (the page hides its buttons)
        "next_start": "setup" if (st.get("status") == "aborted" and not marker_path().is_file()) else "",   # round 35
        "voice_install": _voice_install_state(),     # the Connect page's "Her voice" box reads this
        "browser": browser_led(st),                  # Setup is open in her own window: the desk Wall stays passive
        "again": bool(st.get("only")) if running else False,   # Voice ID again: the desk Wall names it, not Setup
        "way_out": way_out_on(),                  # round 34: Cancel and Esc at every step (the page's way-out bar)
        # round 41 (.101 round 40 D8): an IDs run's one exit reads Cancel; kill CRISSA_EXIT_SAYS_CANCEL=0 (Back to the Wall)
        "exit_label": "Cancel" if exit_says_cancel_on() else "Back to the Wall",
        "done_keeps_fold": ids_done_kept_on(),   # round 43 (R42-3): a section not in this run never reads Done
        # THE SPOKEN VOICE ID AGAIN DRAWS ONE PLATE (the User 2026-10-12, .101: three plates with the same words, one
        # closed by hand): the coach's own small one, never the walk's plate or its done plate - running or ended
        # (house_wall_ascii reads this; the house's own walk never has it)
        "voice_id_routine": voice_id_routine(),
        "spoken_again": wall_spoken_again(st),
        # ADD A VISITOR: whom this run adds, and Security now - the vouch the coach, the routine and presence ask for
        "visitor": (dict(visitor_run(st), security=crissa_home.security_mode()) if running and visitor_run(st) else {}),
        "cancel_ask": bool(st.get("cancel_ask")) if running else False,   # the desk Wall's Cancel asks first
        # THE BUILD FOR THIS SCREEN (round 14, 2026-10-12): her pictures are built on this machine at idle from the start
        # of Setup, silently; the splash line is shown at Setup's end only while her loops are not built yet
        "build": _build_line(),
        # ONE-PAGE SETUP (round 22): the films, the film bus and what the page said; absent = the card walk
        "one_page": one_page_view(st) if running and one_page_on(st) else {},
        "lila_box": lila_box(st) if running else {},   # round 35: the page's picture box is Lila's film, never the office
        # round 36: Add/Edit IDs and Room. None when no IDs run is on: the page tests `if (snap.ids)`, and an empty {}
        # is TRUE in JavaScript, so a first boot drew the IDs plate in place of the name (the trial cut, 2026-10-20)
        "ids": ids_view(st) if running and st.get("ids") else None,
        # round 37 (.101 round 36, the User's walk-in): the team's end is on and she is not seated yet - the desk Wall
        # draws no middle plate and keeps her tile off over her walk-in (house_wall_ascii, init_v2_render)
        "ending": ending_view(),
    }


# ── ONE-PAGE SETUP (round 22, the User 2026-10-15) ──────────────────────────────────────────────────────────────────
# Two screens: Connect as before, then ONE page with every section on it (You and your microphone, This computer,
# Voice ID, Your face, Your room, Your hours). Lila is IN the page, on her own filmed lines (config/setup-films.json,
# display/setup-films/<id>.mp4, every film anchor to anchor, cut at its settle frame): she goes down the page from the
# top, the section she talks about is lit, a done section shows done, and a section already done when she gets there
# gets "Oh, you already did that, great!" instead of its line. A line that ends with its section open hands over to her
# listen loop until it is done; a section already done rolls straight into the next line. Every word she says is
# filmed, so nothing of hers is said live or on the player: her player plate holds (no lines, no enter/leave per card).
# THE FILM BUS: the page plays the walk's own lines (welcome, you-mic, computer, room, hours, already, wait); a line
# that must be timed against the person (the Voice ID counts, the face turns, the done line before Crissa walks in) is
# CUED here - by the Register routine (POST /v1/action setup_film) or the walk itself - the page plays it and posts
# film_said, and the cuer waits for that (capped, so a closed page never stalls Setup). A pack's first boot only.
# Kill: CRISSA_SETUP_ONE_PAGE=0 (round 21's six-card walk, Lila on the player).
FILMS_CFG = REPO / "config" / "setup-films.json"
_FILM: dict[str, Any] = {"seq": 0, "id": "", "said": 0, "at": 0.0, "by": "", "said_at": 0.0, "about": "", "word": 0}
FILM_SAID_SLACK_S = 8.0                    # a cued film is waited for its own length plus this,
FILM_AHEAD_MAX_S = 30.0                    # and for the page's own films before it, at most this much more


def one_page_on(st: dict[str, Any] | None = None) -> bool:
    if crissa_home.is_house():
        return False
    if os.environ.get("CRISSA_SETUP_ONE_PAGE", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    if not FILMS_CFG.is_file():
        return False
    return lila_holds(st) or ids_run(st if st is not None else load_state())   # round 36


def krew_url() -> str:
    """Where the Krew note under Lila links (round 23): setup-films.json `krew_url`; "" = no link. Only http(s)."""
    u = str((films_cfg() or {}).get("krew_url") or "").strip()
    return u if u.startswith(("https://", "http://")) else ""


def films_cfg() -> dict[str, Any]:
    return _load_json(FILMS_CFG)


def film_file(fid: str) -> Path | None:
    """The cut film for `fid` (a name in the manifest, nothing else), or None."""
    cfg = films_cfg()
    if fid not in (cfg.get("films") or {}):
        return None
    p = REPO / str(cfg.get("dir") or "display/setup-films") / f"{fid}.mp4"
    return p if p.is_file() else None


# LILA IN THE SETUP PAGE'S OWN PICTURE, ON EVERY STEP (round 35, the User on .101 round 34: "Empty room is for crissa
# office on ascii"; the Init box showed the EMPTY ROOM on Connect). The classic page (Connect, before the one page) drew
# the player's picture (/frame.mjpg), and the player holds Crissa's empty office while Lila hosts (THE OFFICE WAITS).
# Now, while a pack's Setup runs, the page's picture box plays Lila's own listen film (the one page's `listen`, muted, on
# a loop); the player - the Wall - stays on the office. Kill CRISSA_SETUP_PAGE_LILA=0 (the player's picture, as before).
def setup_page_lila_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_SETUP_PAGE_LILA", "1").strip().lower() not in ("0", "off", "false", "no")


def lila_box(st: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"url", "film"} of the film the page's picture box plays now, or {} (the player's picture)."""
    if not setup_page_lila_on():
        return {}
    f = setup_films().get("listen") or {}
    return {"url": f["url"], "film": "listen"} if f.get("url") else {}


def setup_films() -> dict[str, Any]:
    """What the page needs of each film: its address, its words, when her last word ends and how long it runs."""
    cfg = films_cfg()
    fps = float(cfg.get("fps") or 24)
    out: dict[str, Any] = {}
    for fid, f in (cfg.get("films") or {}).items():
        if film_file(fid) is None:
            continue
        row = {"url": f"/init/film/{fid}.mp4", "line": str(f.get("line") or ""), "role": str(f.get("role") or "")}
        if f.get("end_f") is not None:
            row["seconds"] = round((int(f["end_f"]) + 1) / fps, 3)
            row["last_word_s"] = round((int(f.get("last_word_f") or 0) + 1) / fps, 3)
        if f.get("optional"):
            row["optional"] = True
        vol = film_volume(fid)
        if vol is not None:
            row["volume"] = vol
        # THE KREW SENTENCE ONLY WHEN KREW IS MISSING (round 23, the User 2026-10-15): with krew here her done line stops
        # after "That's everything." (the window closes and Crissa walks in there, so nothing has to join it)
        if f.get("krew_stop_f") is not None and krew_here():
            row["seconds"] = row["last_word_s"] = round((int(f["krew_stop_f"]) + 1) / fps, 3)
            row["end_cut"] = True
            row["line"] = str(f.get("krew_line") or row["line"])
        out[fid] = row
        # .101 round 30 (the User 2026-10-18: "Oh you already did that" right after the fourth voice beep "seems like
        # a mistake almost"; "she could just say the beginning of her already scripted intro for that section"): the
        # film's first sentence alone, as `<fid>-start`, cut where start_stop_f says (as krew_stop_f cuts `done`)
        if f.get("start_stop_f") is not None and face_start_on():
            out[fid + "-start"] = dict(row, seconds=round((int(f["start_stop_f"]) + 1) / fps, 3),
                                       last_word_s=round((int(f["start_stop_f"]) + 1) / fps, 3), end_cut=True,
                                       line=str(f.get("start_line") or row["line"]))
    return out


def face_start_on() -> bool:
    return os.environ.get("CRISSA_FACE_START", "1").strip().lower() not in ("0", "off", "false", "no")


# LILA'S FILMS ON THE ONE LOUDNESS (.101 round 29, the User 2026-10-18: "crissa started much quieter than lila voice had
# to adjust"). Every voice in Setup is levelled to the band (lib/loudness LEVEL_TARGET_DB, ONE LOUDNESS THROUGH SETUP:
# .101's run kept lila 0.82, announcer 0.75, crissa 0.45 to -26 dB), but the page played Lila's films at full volume,
# and their speech sits at -9.1 to -12.2 dB on the same measure (loudness.speech_med_rms): Crissa's first word came
# 15-16 dB under Lila's last. Each film now plays at the volume that puts its own speech on the band (read once from
# its sound, a film's file being what it is); the listen loop, which has no speech, at the films' middle volume. Only
# ever down (a page's volume stops at 1). Kill: CRISSA_FILMS_ONE_LOUDNESS=0 (full volume, as before).
_FILM_VOL: dict[str, Any] = {}
_FILM_VOL_LOCK = threading.Lock()


def films_one_loudness_on() -> bool:
    return os.environ.get("CRISSA_FILMS_ONE_LOUDNESS", "1").strip().lower() not in ("0", "off", "false", "no")


def _film_speech_db(p: Path) -> float | None:
    """The film's speech level (dB, loudness.speech_med_rms on its sound at 24 kHz mono), or None (no sound/speech)."""
    import math
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    try:
        import loudness as L
        pcm = subprocess.run([ff, "-v", "quiet", "-i", str(p), "-vn", "-ac", "1", "-ar", "24000", "-f", "s16le", "-"],
                             capture_output=True, timeout=30).stdout
        med = L.speech_med_rms(pcm, 24000)
    except Exception:  # noqa: BLE001  a film that cannot be read plays as it is
        return None
    return 20.0 * math.log10(med) if med else None


def film_volume(fid: str) -> float | None:
    """The page's volume for this film (0..1), or None (full volume: switched off, the house, or nothing to read)."""
    if crissa_home.is_house() or not films_one_loudness_on():
        return None
    p = film_file(fid)
    if p is None:
        return None
    try:
        import loudness as L
        target = float(L.LEVEL_TARGET_DB)
    except Exception:  # noqa: BLE001
        return None
    with _FILM_VOL_LOCK:
        if not _FILM_VOL.get("read"):
            levels: dict[str, float] = {}
            for k in (films_cfg().get("films") or {}):
                fp = film_file(k)
                db = _film_speech_db(fp) if fp is not None else None
                if db is not None:
                    levels[k] = db
            _FILM_VOL.update(read=True, levels=levels)
            if levels:
                print("[init] Lila's films: speech %.1f..%.1f dB, played on the band (%.1f dB)"
                      % (min(levels.values()), max(levels.values()), target), flush=True)
        levels = dict(_FILM_VOL.get("levels") or {})
    if fid in levels:
        db = levels[fid]
    elif levels:
        db = sorted(levels.values())[len(levels) // 2]     # no speech of its own (the listen loop): the films' middle
    else:
        return None
    return round(min(1.0, 10.0 ** ((target - db) / 20.0)), 4)


_KREW: dict[str, Any] = {"at": 0.0, "here": False}


def krew_here(now: float | None = None) -> bool:
    """krew is installed beside this kit (lib/crissa_capability's own reading), read at most every 30 s."""
    now = time.time() if now is None else now
    if now - float(_KREW["at"]) < 30.0:
        return bool(_KREW["here"])
    try:
        import crissa_capability
        here = bool(crissa_capability._krew(Path(_home()), dict(os.environ)))
    except Exception:  # noqa: BLE001  cannot tell: as if it is not, so her line names it
        here = False
    _KREW.update(at=now, here=here)
    return here


def film_seconds(fid: str) -> float:
    return float((setup_films().get(fid) or {}).get("seconds") or 6.0)


def film_ahead_s(now: float | None = None) -> float:
    """How long the page's film on the air still runs (it said so as it started): a cue waits that much more."""
    now = time.time() if now is None else now
    return round(max(0.0, float(_Q.get("talking_until") or 0.0) - now), 2)


# LILA ON CRISSA'S BAND (round 38, .101 round 37: her films measured -44..-46 LUFS at the speakers against Crissa's -27;
# WirePlumber had remembered her browser's stream at 0.125 and applied it after the page's video.volume). While a Setup
# run is on, her own browser's streams (crissa_window.her_procs: her profile, never his Brave) are set to 100 % when
# they play lower - checked at most every 2 s from the snapshot, each change logged once. A pack only. Kill
# CRISSA_LILA_STREAM_FULL=0.
_STREAM_FULL: dict[str, Any] = {"at": 0.0, "done": set()}


def _her_stream_full(st: dict[str, Any]) -> list:
    if crissa_home.is_house() or st.get("status") != "running":
        return []
    if os.environ.get("CRISSA_LILA_STREAM_FULL", "1").strip().lower() in ("0", "off", "false", "no"):
        return []
    now = time.time()
    if now - float(_STREAM_FULL["at"]) < 2.0:
        return []
    _STREAM_FULL["at"] = now
    try:
        import audio_sink
        import crissa_window
        pids = {int(p["pid"]) for p in crissa_window.her_procs()}
        changed = audio_sink.full_volume_of(pids)
    except Exception:  # noqa: BLE001  her level is a help, never a failure
        return []
    for idx, had in changed:
        if idx not in _STREAM_FULL["done"]:
            _STREAM_FULL["done"].add(idx)
            print("[init] Lila's films: her window's stream %d was at %d %% (remembered by the sound server); set to "
                  "100 %% so she plays on Crissa's band" % (idx, round(had * 100)), flush=True)
    return changed


def film_cue(fid: str, by: str = "", about: str = "") -> int:
    """Ask the page to play `fid` next; the cue's number (0: no such film). `about` (round 23): the section the line is
    about when the film has none of its own (the routine's `already` on Your face)."""
    if fid not in setup_films():
        return 0
    with _LOCK:
        _FILM.update(seq=int(_FILM["seq"]) + 1, id=fid, at=time.time(), by=str(by or "")[:40],
                     about=str(about or "")[:20])
        n = int(_FILM["seq"])
    print("[init] film cue %d: %s (%s)" % (n, fid, by or "-"), flush=True)
    return n


# THE IDs RUN'S WALL PLAYS HER FILM (round 43, .101 round 42 R42-2: "Lips never moved". Her IDs prompts are films in her
# window, so nothing goes through the player and the plate taken in round 42 had no line to paint). While an IDs run is
# on, a film her window STARTS (the page's film_start names it) that has a Wall copy (`lila-ids-<film>` in the library:
# display/setup-films frame for frame, its ends dissolved onto her plate inside the film; scripts/lila-host-intake.py
# "timing": "keep") plays on the Wall too, with its own baked mouth and no sound (by "init": the window has the sound);
# her loop is cued back after it. Her prompts stay films (no spoken lines: no new path after the freeze).
# EVERY FILM, AND THE FIRST BOOT TOO (round 44, the User: "someone thought to report just 3 clips needed lips"): every
# film the page plays has a Wall copy (setup_films(), the page's own list, the `-start` cuts included; a test fails on
# one without), and a first boot's Setup plays them on the Wall as the IDs run does. Between her lines a first boot's
# Wall holds her empty office again (THE OFFICE WAITS, office_hold), OFFICE_BACK_EARLY_F frames before the copy's end:
# those are the dissolve onto her plate, and taking the office before the end leaves the player no ended clip to wrap
# home from. A pack only. Kill CRISSA_IDS_WALL_FILM=0 (round 42: the Wall holds her loop, or her office).
IDS_WALL_PREFIX = "lila-ids-"
OFFICE_BACK_EARLY_F = 2


def ids_wall_film_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_WALL_FILM", "1")).strip().lower() not in ("0", "off", "false", "no")


def setup_wall_film_on(env: dict | None = None) -> bool:
    """Round 45 (D12, the User: "the empty room for all on init and until Crissa walks in"): a first boot's Setup keeps
    her empty office on the Wall all through, no Lila film and no Lila loop there (the ASCII cache is cold at a first
    boot, her films ran late). An IDs run keeps its Wall films. CRISSA_SETUP_WALL_FILM=1 is round 44 (her films there)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SETUP_WALL_FILM", "0")).strip().lower() in ("1", "on", "true", "yes")


def ids_wall_key(fid: str) -> str:
    """The Wall's copy of a Setup film, or '' when the library has none."""
    key = IDS_WALL_PREFIX + str(fid or "")
    clips = _load_json(_poses_dir() / "inventory.json").get("clips") or {}
    return key if key in clips else ""


# THE RUN'S END IS THE END OF HER CUES (round 46, .101 round 45 R45-1): an IDs run's Wall film queued her loop behind it
# (`cue: lila-reg-listen` by init, 14:04:49 and :53), the run ended and her leave played (14:04:54), and the Wall then
# took that queued cue and walked her back on - lila-listen-enter restarted every ~1.5 s for minutes, and Go to the
# Wall left her there ("holding flourish (in conversation)"); Crissa never walked back. Now (a) a Wall film queues her
# loop only while its own run is still on and her plate has not been left since, (b) every leave of her plate first
# drops whatever of hers is still queued on the player, and (c) Go to the Wall with Lila still on the Wall and no run
# on drops her queued cues and lands Crissa on her desk. Kill CRISSA_IDS_END_CLEARS_LILA=0 (round 45).
def ids_end_clears_lila_on() -> bool:
    return os.environ.get("CRISSA_IDS_END_CLEARS_LILA", "1").strip().lower() not in ("0", "off", "false", "no")


def _lila_key(key: str) -> bool:
    return str(key or "").startswith("lila")


def drop_lila_cues(why: str = "") -> list:
    """Every clip of Lila's still queued on the player, dropped; the keys dropped."""
    if not ids_end_clears_lila_on():
        return []
    q = _player_get("/v1/queue")
    rows = (q or {}).get("queue_rows") if isinstance(q, dict) else None
    gone = []
    for row in rows or []:
        if isinstance(row, dict) and _lila_key(row.get("key")) and row.get("id"):
            if _post("/v1/queue", {"op": "delete", "id": row["id"]}).get("ok"):
                gone.append(str(row["key"]))
    if gone:
        print("[init] Lila's queued clips dropped%s: %s" % (" (%s)" % why if why else "", ", ".join(gone)), flush=True)
    return gone


def _ids_wall_film(fid: str) -> str:
    """The Wall copy of the film her window just started (an IDs run; a first boot's Setup keeps her office, round 45
    D12); the key played, or ''."""
    if not ids_wall_film_on():
        return ""
    st = load_state()
    if st.get("status") != "running" or not (st.get("ids") or (
            setup_wall_film_on() and one_page_on(st) and lila_holds(st))):
        return ""
    key = ids_wall_key(fid)
    if not key:
        return ""
    _spawn("ids-wall-film", _ids_wall_run, key, bool(office_now(st)), str(st.get("run_id") or ""), int(_HOST["gen"]))
    return key


def _ids_run_still_on(run_id: str, gen: int | None) -> bool:
    """The run that played this film is still on, and her plate has not been left since (R45-1)."""
    if not ids_end_clears_lila_on() or gen is None:
        return True
    st = load_state()
    return (st.get("status") == "running" and str(st.get("run_id") or "") == str(run_id or "")
            and int(_HOST["gen"]) == int(gen))


def _ids_wall_run(key: str, office: bool = False, run_id: str = "", gen: int | None = None) -> None:
    if not _host_play(key, False):
        print("[init] the Wall did not take her film %s: it stays as it was" % key, flush=True)
        return
    print("[init] the Wall plays her film %s (%s)" % (key, "Setup, her office between lines" if office else "IDs run"),
          flush=True)
    if not office:
        loop = host_keys().get("loop") or ""
        if loop and _ids_run_still_on(run_id, gen):   # queued behind the film: her loop when it ends (as _host_run's)
            _post("/v1/cue", {"key": loop, "ttl": 0, "bridge": False, "by": "init"})
        elif loop:
            print("[init] her loop not queued behind %s: the run is over" % key, flush=True)
        return
    fps = float(films_cfg().get("fps") or 24)
    _sleep(max(0.0, clip_seconds(key) - OFFICE_BACK_EARLY_F / fps))
    if _player_now_key() == key and office_now():     # a later film, her handover or the run's end has the Wall now
        office_hold()


def film_said(seq: Any = 0, fid: str = "", run_id: str = "") -> dict[str, Any]:
    """The page played a film to its end: a cue (seq) is marked said; a line of the page's own is kept on the run, so
    a page opened again never says it twice."""
    try:
        n = int(seq or 0)
    except (TypeError, ValueError):
        n = 0
    with _LOCK:
        if 0 < n <= int(_FILM["seq"]) and n > int(_FILM["said"]):
            _FILM.update(said=n, said_at=time.time())
    if fid and run_id:
        _mutate(run_id, lambda s: s.update(page_said=list(dict.fromkeys(list(s.get("page_said") or []) + [fid]))))
    return {"ok": True, "film": film_bus()}


def film_word(seq: Any = 0) -> dict[str, Any]:
    """The page says a cued film reached its last word (HER LINE UNDER LILA'S HANDOVER: the done line's)."""
    try:
        n = int(seq or 0)
    except (TypeError, ValueError):
        n = 0
    with _LOCK:
        if 0 < n <= int(_FILM["seq"]) and n > int(_FILM.get("word") or 0):
            _FILM["word"] = n
    return {"ok": True}


def film_word_wait(seq: int, cap_s: float) -> bool:
    """Until cue `seq`'s last word is said (or the film is said whole), at most cap_s (time AND polls)."""
    t0 = time.time()
    for _ in range(int(max(0.0, cap_s) / QUIET_POLL_S) + 1):
        if int(_FILM.get("word") or 0) >= seq or int(_FILM["said"]) >= seq:
            return True
        if time.time() - t0 >= cap_s:
            break
        _sleep(QUIET_POLL_S)
    return int(_FILM.get("word") or 0) >= seq or int(_FILM["said"]) >= seq


def film_bus() -> dict[str, Any]:
    # said_at (round 23): when the page said its last word, so the routine's beep keeps its gap from her word and not
    # from the moment its poll noticed (the User 2026-10-15: "the and again had a lag ... so I false started")
    return {k: _FILM.get(k, "" if k == "about" else 0) for k in ("seq", "id", "said", "said_at", "about")}


def film_wait(seq: int, cap_s: float, sleep=None, clock=None) -> bool:
    """Wait until the page says cue `seq` was played (True), or `cap_s` (False); a film of the page's own still on the
    air when the cap comes pushes it on (the cue plays after it), never past FILM_AHEAD_MAX_S more."""
    sleep, clock = sleep or time.sleep, clock or time.time
    t0 = clock()
    for _ in range(int((max(0.0, cap_s) + FILM_AHEAD_MAX_S) / 0.1) + 1):
        if int(_FILM["said"]) >= seq:
            return True
        now = clock()
        if now - t0 >= cap_s and film_ahead_s(now) <= 0:
            break
        sleep(0.1)
    return int(_FILM["said"]) >= seq


def film_play_wait(fid: str, by: str = "") -> bool:
    """Cue a film and wait for the page to play it (its length + FILM_SAID_SLACK_S at most)."""
    n = film_cue(fid, by)
    if not n:
        return False
    if SYNC:
        return True
    ok = film_wait(n, film_seconds(fid) + FILM_SAID_SLACK_S)
    if not ok:
        print("[init] film %s: the page did not say it was played (no page open?)" % fid, flush=True)
    return ok


ONE_PAGE_SECTIONS = ("you", "pc", "vid", "face", "room", "hours")


def meter_reads(now: float | None = None) -> bool:
    """The page's microphone meter has a reading from the last 10 s; with none (no sampler, a Mac, a sandbox, an input
    that gives nothing) You and your microphone is done with the name alone - it never waits for a level it cannot see."""
    now = time.time() if now is None else now
    at = float((_METER.get("level") or {}).get("at") or 0.0)
    return bool(mic_probe_on() and _sampler() is not None and at and now - at < 10.0)


# THIS COMPUTER WAITS FOR YOU (.101 10-19, the User: "then it stalled"): with one page, This computer runs in the
# background and its done() moved the walk to Voice ID whether or not the name was typed. A voice already installed
# finished it in 5 s (01:07:01 Connect, 01:07:06 done), so Voice ID opened with no name taken, fell back to its old
# name card (register state "name"), which the one page never shows - no line, no Start, the page stuck on "Up next".
# A fresh install raced the same way wherever the voice installs faster than a name is typed. Now its done() moves
# the walk only once You and your microphone is done (the page's own rule: the name, and the microphone heard or no
# level to read); else the name or the "heard" moves it (you_moves_on). The same gate (_leave_machine) holds the two
# other ways off This computer: an answer already on disk at its start (01:15:50 run: Voice ID at once, no name) and
# its Allow / Skip. Kill CRISSA_MACHINE_WAITS_YOU=0.
def machine_waits_you(st: dict[str, Any] | None = None) -> bool:
    if os.environ.get("CRISSA_MACHINE_WAITS_YOU", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return one_page_on(st)


def you_ready(st: dict[str, Any]) -> bool:
    m = st.get("machine") or {}
    return bool(m.get("name_taken")) and (bool(m.get("mic_heard")) or not meter_reads())


def _leave_machine(run_id: str) -> bool:
    """This computer is ready to leave (done, answered, or allowed: three paths, one gate): the walk moves on now, or,
    while You and your microphone is not done, it is marked to move on when it is (you_moves_on)."""
    if machine_waits_you() and not you_ready(load_state()):
        _mutate(run_id, lambda s: s.setdefault("machine", {}).update(leave_ready=True))
        print("[init] This computer: waits for You and your microphone before Voice ID", flush=True)
        return False
    _advance(run_id, expect="machine")
    return True


def you_moves_on(run_id: str) -> None:
    """The name or the microphone just finished You: the walk moves on when This computer was ready to leave."""
    st = load_state()
    if (machine_waits_you(st) and st.get("step_id") == "machine" and (st.get("machine") or {}).get("leave_ready")
            and you_ready(st)):
        print("[init] You and your microphone: done; Voice ID next", flush=True)
        _advance(run_id, expect="machine")


VID_WAIT_LINE = "Waiting for this computer's setup to finish"


def vid_wait_bg_on() -> bool:
    return os.environ.get("CRISSA_VID_WAIT_BG", "1").strip().lower() not in ("0", "off", "false", "no")


def one_page_sections(st: dict[str, Any]) -> dict[str, Any]:
    """{section: {"done": bool, "summary": str}} for the one page, from the walk (the page decides which one Lila is
    on; a section done before she gets there is the page's "already")."""
    sd = set(st.get("steps_done") or [])
    sid = str(st.get("step_id") or "")
    m, reg = st.get("machine") or {}, st.get("register") or {}
    taken = m.get("name_taken") or {}
    out: dict[str, Any] = {}
    # the microphone heard them, or there is no level to read here (no sampler: a Mac, a sandbox), where the name is all
    you = ("machine" in sd and not machine_waits_you(st)) or (bool(taken) and (bool(m.get("mic_heard")) or not meter_reads()))
    mic_label = str((_MIC_CACHE.get("view") or {}).get("label") or "")
    out["you"] = {"done": you, "summary": (str(taken.get("display") or "") + (
        " · %s hears you" % mic_label if mic_label else " · the microphone hears you")) if you and taken else ""}
    perm = str(m.get("permission") or "")
    out["pc"] = {"done": "machine" in sd, "summary": {"skip": "Skipped: Crissa picks from the hardware alone"}.get(
        perm, "Allowed · Crissa checks it in the background") if "machine" in sd else ""}
    g = _GLASS
    counted = sid == "voice_id" and str(g.get("stage") or "") == "face"
    vid = "voice_id" in sd or counted
    out["vid"] = {"done": vid, "summary": ("Already done: Crissa knows your voice" if reg.get("already") else
                                           "Your counts are in") if vid else ""}
    # VOICE ID SAYS WHAT IT WAITS FOR (round 43, .101 round 42: Voice ID waited ~1 min, nothing on screen, until "This
    # computer: done in the background" - her voices spoke, the aligner installed). While the walk is still on This
    # computer with You done and This computer answered, Voice ID's section says so. Text only: the order is unchanged.
    # Kill CRISSA_VID_WAIT_SAYS=0.
    # Round 45 (.101 rounds 43 and 44: the line never showed - the walk leaves This computer at once, so step_id is
    # already voice_id while Voice ID waits): keyed on the background check's own state instead - This computer
    # answered and its background work not done yet (machine.background_done), the walk on This computer or Voice ID,
    # and no take begun. Kill CRISSA_VID_WAIT_BG=0 (round 43's step rule).
    answered = bool(perm or str(m.get("state") or "") not in ("", "ask"))
    if vid_wait_bg_on():
        waits = (sid in ("machine", "voice_id") and setup_parallel(st) and not m.get("background_done")
                 and str(g.get("phase") or "idle") not in REGISTER_PHASES)
    else:
        waits = sid == "machine"
    if (not vid and waits and you and answered
            and os.environ.get("CRISSA_VID_WAIT_SAYS", "1").strip().lower() not in ("0", "off", "false", "no")):
        out["vid"]["wait"] = VID_WAIT_LINE
    fc = st.get("face") or {}
    faces = list(g.get("faces") or []) if sid == "voice_id" else []
    face = "face" in sd
    fsum = ""
    if face:
        fsum = {"no_camera": "No camera on this computer: Crissa knows you by your voice",
                "no_person": "Skipped"}.get(str(fc.get("state") or ""), "")
        if not fsum:
            n = int(fc.get("faces") or 0)
            fsum = ("%d face photo%s" % (n, "" if n == 1 else "s")) if n else "No photo was taken"
    out["face"] = {"done": face, "summary": fsum, "taken": faces}
    rm = st.get("room") or {}
    marks = rm.get("marks") or (room_saved().get("marks") if "room" in sd else {}) or {}
    snd = marks.get("speakers")
    out["room"] = {"done": "room" in sd, "summary": ("Sound %s · door at %s" % (
        "from this computer" if not snd else "at %s" % snd, marks.get("door", "-"))) if "room" in sd and marks else ""}
    hr = st.get("hours") or {}
    over = _window(hr.get("overnight")) or hours_saved().get("overnight")
    out["hours"] = {"done": "hours" in sd, "summary": ("Her working hours: %s" % window_text(over).replace(
        "-", " to ")) if "hours" in sd and over else ""}
    return out


# NO FACE PHOTOS SECTION (round 34, the User via .101 round 33: "delete the entire Face photos section"): on the one
# page each Voice ID count is checked until its photo is in (the enroll routine's CRISSA_TURN_CHECK), so the page shows
# no "Your face" section and the walk's face step only reads the bank. One switch with the routine's check: kill
# CRISSA_TURN_CHECK=0 brings both back.
def no_face_section() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_TURN_CHECK", "1").strip().lower() not in ("0", "off", "false", "no")


# NO TAKE OVER HER OWN VOICE (round 45, .101 round 43 R43-1): in an IDs run the routine's first film (vid-facing) won the
# page, and the page's own welcome followed it on its own - over count 1's first take, which heard "Hi, I'm Lila. I'll
# set Chrissa up with y". The page holds its own lines (welcome, already, a section's line, "Take your time") while the
# routine is in its takes (the glass's phase), its cues still play; an IDs run whose takes began drops the welcome. The
# routine also waits out any film still on the air before each beep (voice_register NO TAKE OVER HER OWN VOICE).
# Kill: CRISSA_TAKE_AFTER_HER_LINE=0 (both halves; round 44).
def take_holds_lines_on() -> bool:
    return os.environ.get("CRISSA_TAKE_AFTER_HER_LINE", "1").strip().lower() not in ("0", "off", "false", "no")


def one_page_view(st: dict[str, Any]) -> dict[str, Any]:
    """The one page's own read: the films, the bus, the lines the page already said on this run, each section's state,
    Voice ID's stage."""
    return {"films": setup_films(), "film": film_bus(), "page_said": list(st.get("page_said") or []),
            "sections": one_page_sections(st), "ending": bool(st.get("ending")),
            "face_section": not no_face_section(),   # round 34: the counts carry the photos (the User)
            "name_taken": bool((st.get("machine") or {}).get("name_taken")),
            "krew": krew_here(),                  # round 23: the note under her picture shows only without krew
            "krew_url": krew_url(),
            "mic_heard": bool((st.get("machine") or {}).get("mic_heard")),
            "takes_hold": take_holds_lines_on(),   # round 45 (R43-1): no line of the page's own while a take runs
            "glass": {k: _GLASS.get(k) for k in ("phase", "take", "takes", "away", "beep_ts", "seconds", "n", "stage",
                                                   "faces")}}


# THE QUESTION LISTENER (round 22, the User 2026-10-15: "The person is not expected to speak in Setup (except counts /
# mic test) until Setup ends and Crissa asks her question"). While a section is open on the one page - This computer,
# Your room, Your hours: never during her films, the microphone test, the counts or the photos - one stream from the
# Setup microphone is cut into utterances by their level; each goes to the speech-to-text service (transcribe only,
# the /stt contract). A QUESTION (ends in "?" or opens with a question word) plays her "I'm just here for setup.
# Crissa can answer that in a minute." ONCE, which opens a fixed QUESTION_WINDOW_S; a question inside that window
# replays the open section's own line instead; the window is not stretched by later questions, and once it is over
# the next question gets "just here for setup" again. The sound and the words are dropped at once; the log says only
# "a question was heard". A pack's one page only. Kill: CRISSA_SETUP_QUESTIONS=0.
QUESTION_WINDOW_S = 120.0
QUESTION_WORDS = ("who", "what", "when", "where", "why", "how", "which", "whose", "whom", "can", "could", "would",
                  "will", "should", "shall", "do", "does", "did", "is", "are", "am", "was", "were", "may", "might",
                  "have", "has", "had", "isn't", "aren't", "don't", "doesn't", "didn't", "won't", "can't")
Q_FRAME_S = 0.02
Q_END_QUIET_S = 0.7                        # an utterance ends after this much quiet
Q_MIN_VOICED_S = 0.5                       # shorter is a cough or a click, never sent
Q_MAX_S = 8.0
Q_FLOOR_RMS = 300.0                        # a voice is at least this loud (s16), and 3x the room's own level
_Q: dict[str, Any] = {"setup_only_at": 0.0, "talking_until": 0.0, "run": "", "heard": 0}
SECTION_LINES = {"machine": "computer", "room": "room", "hours": "hours"}


def questions_on() -> bool:
    return os.environ.get("CRISSA_SETUP_QUESTIONS", "1").strip().lower() not in ("0", "off", "false", "no")


def is_question(text: str) -> bool:
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not t:
        return False
    if t.rstrip(" .!").endswith("?"):
        return True
    first = re.sub(r"[^a-z']", "", t.split(" ", 1)[0])
    return first in QUESTION_WORDS


def question_open(st: dict[str, Any], now: float | None = None) -> str:
    """The open section's own line ('' = the listener is closed): This computer once You is done, Your room, Your hours;
    never while one of her films is on the air."""
    now = time.time() if now is None else now
    if st.get("status") != "running" or not one_page_on(st) or now < float(_Q["talking_until"]):
        return ""
    sid = str(st.get("step_id") or "")
    if sid == "machine":
        m = st.get("machine") or {}
        return "computer" if (m.get("name_taken") and m.get("mic_heard")) else ""
    return SECTION_LINES.get(sid, "")


def question_reply(line: str, now: float | None = None) -> str:
    """The film a question gets: "setup-only" once a window, else the open section's own line."""
    now = time.time() if now is None else now
    at = float(_Q["setup_only_at"] or 0.0)
    if not at or now - at >= QUESTION_WINDOW_S:
        _Q["setup_only_at"] = now
        return "setup-only"
    return line


def question_heard(text: str, run_id: str) -> str:
    """One utterance's words (never kept): the film cued, or ''."""
    st = load_state()
    if st.get("run_id") != run_id:
        return ""
    line = question_open(st)
    if not line or not is_question(text):
        return ""
    _Q["heard"] = int(_Q["heard"]) + 1
    fid = question_reply(line)
    print("[init] a question was heard", flush=True)
    film_cue(fid, by="question")
    _Q["talking_until"] = time.time() + film_seconds(fid) + 0.6
    return fid


class Utterances:
    """Cuts a stream of 16 kHz s16 frames into utterances by level (see THE QUESTION LISTENER)."""

    def __init__(self) -> None:
        self.buf: list[bytes] = []
        self.voiced = 0.0
        self.quiet = 0.0
        self.levels: list[float] = []

    def reset(self) -> None:
        self.buf, self.voiced, self.quiet = [], 0.0, 0.0

    def feed(self, frame: bytes) -> bytes | None:
        """One frame in; a whole utterance out when one just ended (else None)."""
        import array
        a = array.array("h")
        a.frombytes(frame[: len(frame) - (len(frame) % 2)])
        if not a:
            return None
        rms = (sum(x * x for x in a) / len(a)) ** 0.5
        self.levels = (self.levels + [rms])[-150:]
        floor = sorted(self.levels)[len(self.levels) // 5]
        voice = rms >= max(Q_FLOOR_RMS, 3.0 * floor)
        if voice:
            self.buf.append(frame)
            self.voiced += Q_FRAME_S
            self.quiet = 0.0
        elif self.buf:
            self.buf.append(frame)
            self.quiet += Q_FRAME_S
        if self.buf and (self.quiet >= Q_END_QUIET_S or len(self.buf) * Q_FRAME_S >= Q_MAX_S):
            out = b"".join(self.buf) if self.voiced >= Q_MIN_VOICED_S else None
            self.reset()
            return out
        return None


def _stt(pcm: bytes) -> str:
    """Transcribe only: the words of one utterance, '' when no service answers."""
    import urllib.request
    for url in [os.environ.get("CRISSA_SETUP_STT_URL", "")] + [crissa_ports.url(k) for k in ("stt", "asr")]:
        if not url:
            continue
        try:
            req = urllib.request.Request(url.rstrip("/") + "/stt", data=pcm, method="POST",
                                         headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": "16000"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return str((json.loads(r.read().decode() or "{}") or {}).get("text") or "")
        except Exception:  # noqa: BLE001 - the next address, else nothing heard
            continue
    return ""


def _question_listener(run_id: str) -> None:
    import shutil
    import subprocess
    st = load_state()
    mv = mic_view()
    src = str(mv.get("pick") or mv.get("source") or "")
    if not src or not shutil.which("parec"):
        print("[init] the question listener: no microphone to read here", flush=True)
        return
    n = int(16000 * 2 * Q_FRAME_S)
    cut = Utterances()
    import audio_sink
    dev = audio_sink.capture_source(src)            # .101 round 30: Lila's own lines are not a question
    if dev != src:
        print("[init] the question listener hears %s (the ear's echo canceller)" % dev, flush=True)
    proc = subprocess.Popen(["parec", "--device=" + dev, "--raw", "--format=s16le", "--rate=16000", "--channels=1",
                             "--latency-msec=40"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            frame = proc.stdout.read(n) if proc.stdout else b""
            if not frame:
                return
            st = load_state() if cut.buf or int(time.time() * 2) % 2 == 0 else st
            if st.get("run_id") != run_id or st.get("status") != "running":
                return
            if not question_open(st):
                cut.reset()
                continue
            utt = cut.feed(frame)
            if utt is not None:
                question_heard(_stt(utt), run_id)
                del utt                                  # nothing of what was said is kept
    finally:
        proc.terminate()


def start_question_listener(run_id: str) -> bool:
    if SYNC or not questions_on() or _Q.get("run") == run_id:
        return False
    _Q.update(run=run_id, setup_only_at=0.0, talking_until=0.0)
    _spawn("questions", _question_listener, run_id)
    return True


def _build_line() -> dict:
    try:
        import crissa_build
        b = crissa_build.build_view()
        return {"line": b.get("line") or "", "pct": b.get("pct"), "running": bool(b.get("running"))}
    except Exception:  # noqa: BLE001 - a line about the build never stops Setup
        return {"line": "", "pct": None, "running": False}


# ── SETUP IN THE BROWSER (the User 2026-10-10, .101 finding F) ──────────────────────────────────────────────────────
# A first boot opens /init in the person's browser once (scripts/crissa.py open_setup_once); from then on this Init's
# walk is led there, and the desk Wall shows a passive "Setup is open in your browser" plate (lib/init_v2_render)
# instead of its own keys and Tk dialogs. The record lives until Init is done or started again.
BROWSER_FILE = "init-opened.json"


def browser_path() -> Path:
    return _home() / BROWSER_FILE


def browser_record() -> dict[str, Any]:
    return _load_json(browser_path())


def mark_browser_opened(url: str, by: str = "launcher") -> None:
    _write_json(browser_path(), {"url": str(url), "by": str(by)[:20], "at": _now()})


def browser_led(st: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"led", "url", "by", "own", "window"}: a browser holds this Init's walk (opened by the launcher, the dock entry or
    the Wall, Init still running). `own`: it is her own window (lib/crissa_window, the User 2026-10-11); `window` says
    whether that window is running and shows Setup now, so the desk Wall says "Setup is open in Crissa's window" only
    while it is, and offers to open it again when it is not."""
    st = st if st is not None else load_state()
    rec = browser_record()
    url = str(rec.get("url") or "")
    led = bool(url) and st.get("status") == "running"
    out = {"led": led, "url": url, "by": str(rec.get("by") or ""), "own": str(rec.get("by") or "") in OWN_WINDOW_BY}
    if led and out["own"]:
        try:
            import crissa_window
            out["window"] = crissa_window.state(url)
        except Exception as e:  # noqa: BLE001  a window that cannot be read is said as unknown, never as open
            out["window"] = {"up": False, "setup": False, "error": type(e).__name__}
        _ids_end_with_window(st, out["window"])
    return out


# AN IDS RUN ENDS WITH ITS WINDOW (round 38, .101 round 37 D: after the page crashed the run outlived its window - status
# running, ids stage ready_user, no window - and the Wall kept its Setup look with no menu). Her window closed, or its
# browser gone, for IDS_WINDOW_GRACE_S: the run ends exactly as Back to the Wall (abort: nothing saved). An IDs run
# only; a first boot keeps the Wall's way back. Kill CRISSA_IDS_END_WITH_WINDOW=0.
IDS_WINDOW_GRACE_S = 8.0
_IDS_GONE: dict[str, Any] = {"run": "", "since": 0.0}


def _ids_end_with_window(st: dict[str, Any], win: dict[str, Any], now: float | None = None) -> bool:
    if os.environ.get("CRISSA_IDS_END_WITH_WINDOW", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    if not (st.get("ids") and st.get("status") == "running") or win.get("error"):
        _IDS_GONE.update(run="", since=0.0)
        return False
    run = str(st.get("run_id") or "")
    t = time.time() if now is None else float(now)
    if win.get("up") and win.get("setup"):
        _IDS_GONE.update(run=run, since=0.0)
        return False
    if _IDS_GONE.get("run") != run or not _IDS_GONE.get("since"):
        _IDS_GONE.update(run=run, since=t)
        return False
    if t - float(_IDS_GONE["since"]) < IDS_WINDOW_GRACE_S:
        return False
    _IDS_GONE.update(run="", since=0.0)
    print("[init] Add/Edit IDs: her window is gone; the run ends as Back to the Wall (nothing saved)", flush=True)
    abort(run, why="window gone")
    return True


OWN_WINDOW_BY = ("window", "dock", "wall")       # who opened Setup in her own window (mark_browser_opened `by`)


# ── her microphone (the done plate's key, the User 2026-10-10) ──────────────────────────────────────────────────────
def voice_lock_path() -> Path:
    """The ear's voice master lock, the file the Control Panel's Mic key writes (crissa_ear.house_voice): a missing
    file is OFF. HOUSE_VOICE_LOCK names another, as it does for the ear."""
    raw = (os.environ.get("HOUSE_VOICE_LOCK") or "").strip()
    return Path(raw).expanduser() if raw else _home() / "house-voice.json"


def mic_state() -> dict[str, Any]:
    """Is her microphone on? The lock's own answer; unreadable or missing is off (the ear reads it the same way)."""
    try:
        v = json.loads(voice_lock_path().read_text(encoding="utf-8")).get("voice")
    except (OSError, ValueError, AttributeError):
        v = "off"
    return {"on": str(v or "off").strip().lower() in ("on", "1", "true", "yes")}


def mic_setting(off: bool) -> dict[str, Any]:
    """This computer's Mic off (round 20, the User: "can we default to mic on and put an off button on microphone select
    screen?"): ticked, she stays deaf after Setup until it is turned on (MIC ON AT THE END keeps a "no" said during the
    run, and an ear already on is turned off); unticked, Setup's end turns her microphone on as usual."""
    st = load_state()
    if st.get("status") != "running":
        return {"ok": False, "error": "Setup is not running"}
    rid = str(st.get("run_id") or "")
    if off:
        _mutate(rid, lambda s: s.update(mic_off_by_person=_now()))
        if mic_state().get("on") and not (os.environ.get("PYTEST_CURRENT_TEST") and mic is _MIC):
            mic(False)
    else:
        _mutate(rid, lambda s: s.pop("mic_off_by_person", None))
    return {"ok": True, "mic_off": bool(off)}


def mic(on: bool) -> dict[str, Any]:
    """Turn her microphone on (or off) the way the Mic key on the panels does: the ear's own route (POST /panel/voice
    on the ear, the one writer of the lock). The ear not answering is said, and the lock is left as it was."""
    req = urllib.request.Request(crissa_ports.url("stt") + "/panel/voice",
                                 data=json.dumps({"on": bool(on), "on_by": "init"}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with crissa_egress.urlopen(req, 6.0, what="init: the microphone") as r:
            got = json.loads(r.read(8000).decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "mic": mic_state(),
                "error": "The ear is not answering (%s), so the microphone was not turned %s. Start Crissa with her "
                         "ear, then try again." % (type(e).__name__, "on" if on else "off")}
    now = mic_state()
    if bool(got.get("voice_on")) != bool(on) or now["on"] != bool(on):
        return {"ok": False, "mic": now, "error": "The ear did not turn the microphone %s." % ("on" if on else "off")}
    return {"ok": True, "mic": now}


_MIC = mic                                  # the real one: a test that has not stood it in never reaches an ear


def ids_done_both_on() -> bool:
    """Round 39 N4: an IDs run's done line names voice AND face. Kill CRISSA_IDS_DONE_BOTH=0 (round 38's Voice ID line)."""
    return os.environ.get("CRISSA_IDS_DONE_BOTH", "1").strip().lower() not in ("0", "off", "false", "no")


def ids_done_person_only_on() -> bool:
    """Round 41 D6: an IDs run's done plate shows the person's result only. Kill CRISSA_IDS_DONE_PERSON_ONLY=0 (round 40:
    the microphone key and Start Crissa when I log in on every done plate)."""
    return os.environ.get("CRISSA_IDS_DONE_PERSON_ONLY", "1").strip().lower() not in ("0", "off", "false", "no")


def ids_done_kept_on(env: dict | None = None) -> bool:
    """Round 43 (.101 round 42 R42-3): an IDs run's done plate counts what was KEPT - its face photos are the stills in
    the person's own folder (every one dropped as another face counts 0; the sync's count read 4) - and a section the
    run did not walk keeps "Not in this run" when the run ends (the page read "Your hours: Done"). Kill
    CRISSA_IDS_DONE_KEPT=0 (round 42's counts and every section Done)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_DONE_KEPT", "1")).strip().lower() not in ("0", "off", "false", "no")


def done_view(st: dict[str, Any]) -> dict[str, Any]:
    """What the last plate says: who she knows now, how she was set up, and whether her microphone is on."""
    d = pack_cfg().get("done") or {}
    reg, fc = st.get("register") or {}, st.get("face") or {}
    lines = []
    if reg.get("state") == "done" and st.get("ids") and reg.get("face") is not None and ids_done_both_on():
        # round 39 (.101 round 38 N4, the User: "please add 'Face' to Voice and Face ID section"): an Add/Edit IDs run
        # banks both, and its face count is the run's own (register.face; the first boot's face section is not run)
        nv, nf = int(reg.get("voice") or 0), int(reg.get("face") or 0)
        pid = str(reg.get("person_id") or "")
        if pid and ids_done_kept_on():
            nf = min(nf, face_prints(pid))      # round 43: a still dropped as another face was never kept
        lines.append("Voice and Face ID: %s, %d voice print%s, %d face photo%s." % (
            reg.get("display") or "you", nv, "" if nv == 1 else "s", nf, "" if nf == 1 else "s"))
    elif reg.get("state") == "done":
        lines.append("Voice ID: %s, %d voice print%s." % (reg.get("display") or "you", int(reg.get("voice") or 0),
                                                      "" if int(reg.get("voice") or 0) == 1 else "s"))
    elif reg.get("state") in ("skipped", "failed", "no_coach"):
        # Y (the User 2026-10-10, .101: "once Voice ID ended skipped there was no Retry"): said plainly, with its key
        lines.append("Voice ID: not made yet (%s). Press Voice ID again, here or in the Crissa menu." % (
            "skipped" if reg.get("state") == "skipped" else "it did not finish"))
    if fc.get("state") == "done":
        lines.append("Face photos: %d." % int(fc.get("faces") or 0))
    elif fc.get("state") in ("skipped", "no_camera", "no_person"):
        lines.append("Face photos: %s." % ("no camera on this computer" if fc.get("state") == "no_camera" else "skipped"))
    try:                                          # round 40: names espeak-ng while it is missing (lib/crissa_espeak)
        import crissa_espeak
        el = crissa_espeak.done_line()
        if el:
            lines.append(el)
    except Exception:  # noqa: BLE001  the plate never waits on it
        pass
    rm, hr = st.get("room") or {}, st.get("hours") or {}
    if rm.get("state") == "saved":
        lines.append("Your room: saved on the clock.")
    elif rm.get("state") == "skipped":
        lines.append("Your room: skipped; the clock keeps its defaults.")
    if rm.get("lip_sync"):
        lines.append(str(rm["lip_sync"]))           # round 17: what Setup did with her speakers' delay (_lip_sync_said)
    try:                                          # her voice at this computer's own loudness (lib/crissa_level, 2026-10-26)
        import crissa_level
        if crissa_level.on():
            ll = crissa_level.said(crissa_level.read(_home()))
            if ll:
                lines.append(ll)
    except Exception:  # noqa: BLE001  the plate never waits on it
        pass
    if hr.get("state") == "saved":
        lines.append("Your hours: overnight %s%s." % (window_words(hr.get("overnight")),
                                                       (", quiet %s" % window_words(hr.get("quiet"))) if hr.get("quiet")
                                                       else ", no quiet hours"))
    elif hr.get("state") == "skipped":
        lines.append("Your hours: skipped.")
    c = connect_record(st)
    lines.append("Agent: %s." % ({"tested": "connected", "later": "set up later, on the Connect page"}.get(
        c.get("state") or "", "not connected")))
    m = mic_state()
    person_only = bool(st.get("ids")) and ids_done_person_only_on()
    if person_only:
        # round 41 (.101 round 40 D6, the User: "Should we allow a guest to set start when I login? Who is I? ... Let's hide
        # that for this sequence"): an IDs run's plate is the person's result only - their Voice and Face ID line - with
        # no login-start box and no microphone key (those are the User's, on his Setup and the menu)
        lines = [ln for ln in lines if ln.startswith(("Voice and Face ID:", "Voice ID:", "Face photos:"))]
    return {"title": d.get("title") or "All set", "body": d.get("body") or "", "lines": lines,
            "person_only": person_only,
            "name": reg.get("display") or "", "mic": m, "login": login_view(),
            "phone": None if person_only else phone_access_view(),
            "voice_id_again": reg.get("state") != "done" and not (st.get("only") or []),
            "desk": desk_present(),
            "mic_line": ("Microphone: on. She hears you; say \"Hey Crissa\"." if m["on"] else
                         "Microphone: off. She cannot hear you until you turn it on.")}


def desk_present(home: Optional[Path] = None) -> bool:
    """This install has a desk Wall (the launcher's table names a wall lane, running or closed): Setup's end goes to it."""
    try:
        import crissa_procs
        return bool(crissa_procs.known(home or _home(), "wall"))
    except Exception:  # noqa: BLE001
        return False


def to_desk_wall(close=None, wall_open=None, delay_s: float = 0.8) -> dict[str, Any]:
    """GO TO THE WALL WHEN THE WALL IS THE DESKTOP (round 15, .101's fresh install: after Setup her browser Wall opened in
    the desktop Wall's place). Setup's window steps aside - her own pages on the player close (crissa_window
    close_agent_pages), a moment after this answer - so the desktop Wall shows; a Wall closed during Setup is opened
    (the launcher's setup_end_pass does it too). Never the browser Wall in between."""
    if ids_end_clears_lila_on() and load_state().get("status") != "running" and _lila_key(_player_now_key()):
        # R45-1: after an IDs run, Go to the Wall lands Crissa on her desk, whatever the run left queued
        drop_lila_cues("Go to the Wall")
        _HOST["on"] = False
        _land_on_listen()
        print("[init] Go to the Wall: Lila was still on the Wall; Crissa lands on her desk", flush=True)
    try:
        import crissa_watch
        if crissa_watch.closed(_home(), "wall"):
            import crissa_menu
            (wall_open or crissa_menu.wall_open)()
    except Exception:  # noqa: BLE001  the launcher's own pass opens it at Setup's end
        pass

    def later():
        time.sleep(delay_s)
        try:
            import crissa_ports
            import crissa_window
            (close or crissa_window.close_agent_pages)(crissa_ports.url("player"))
        except Exception:  # noqa: BLE001  the window stays; the Wall is the desktop under it
            pass
    threading.Thread(target=later, daemon=True, name="setup-to-wall").start()
    return {"ok": True, "note": "Her Wall is your desktop now; this window closes."}


_TO_DESK_WALL = to_desk_wall


def phone_access_view() -> Optional[dict[str, Any]]:
    """TO ENABLE THE MOBILE INTERFACE (the User 2026-10-28: "Can we 'To enable Mobile interface...' like that in setup?"):
    the last page's Allow / Remove keys for the phone, the same plan the phone card shows
    (lib/crissa_phone_firewall, served at POST /v1/phone/allow and /v1/phone/remove). None = no row: no phone module
    here, no firewall in the way, or the key switched off."""
    try:
        import crissa_menu
        import crissa_phone_firewall
        import crissa_ports
        if crissa_menu.phone_view() is None:
            return None
        p = crissa_phone_firewall.plan(int(crissa_ports.port("phone")))
    except Exception:  # noqa: BLE001  no phone module, no port table: no row
        return None
    rm = p.get("remove") or {}
    if not p.get("offer") and not rm.get("offer"):
        return None
    return {"offer": bool(p.get("offer")), "where": p.get("where") or "", "rule": p.get("rule") or "",
            "label": p.get("label") or crissa_phone_firewall.LABEL, "remove": bool(rm.get("offer")), "here": bool(rm.get("here")),
            "remove_where": rm.get("where") or "", "remove_label": crissa_phone_firewall.REMOVE_LABEL}


def login_view() -> dict[str, Any]:
    """START CRISSA WHEN I LOG IN, the last step's choice (lib/crissa_service LOGIN_*: off unless chosen)."""
    try:
        import crissa_service
        return {"on": bool(crissa_service.login_wanted(_home())), "why": crissa_service.LOGIN_WHY,
                "label": "Start Crissa when I log in"}
    except Exception as e:  # noqa: BLE001
        return {"on": False, "why": "", "label": "Start Crissa when I log in", "error": type(e).__name__}


def login(on: bool) -> dict[str, Any]:
    try:
        import crissa_service
        got = crissa_service.set_login(bool(on), home=_home(), by="init")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160]), "login": login_view()}
    out = {"ok": bool(got.get("ok")), "login": login_view(), "note": got.get("note") or ""}
    if not got.get("ok"):
        out["error"] = got.get("note") or got.get("error") or "the service did not take it"
    return out


# ── start / abort / finish ───────────────────────────────────────────────────────────────────────────────────────────
# NO SET UP AGAIN (round 34, the User 2026-10-19: "no one will ever need to set up again. why ever would they"; "It was
# Add an ID is all"). On a pack nothing offers or runs Setup again once it has finished: the page's "Set up again" and
# "Run Setup again" are gone, and a forced start (the page, the Control Panel's Init key, the restart action) is refused
# when the marker says done. The INITIAL Setup, cancelled or failed, runs again at the next start (it leaves no marker:
# A CANCELLED SETUP LEAVES NOTHING). Changing a finished Setup is round 35's Add/Edit IDs and Room. Kill
# CRISSA_NO_SETUP_AGAIN=0.
SETUP_DONE_LINE = "Setup is done on this computer."


def no_setup_again_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_NO_SETUP_AGAIN", "1").strip().lower() not in ("0", "off", "false", "no")


def start(*, force: bool = False) -> dict[str, Any]:
    st = load_state()
    if force and no_setup_again_on() and marker().get("done") and st.get("status") != "running":
        print("[init] Set up again refused: Setup is done here (round 34)", flush=True)
        return dict(snapshot(st), ok=False, error=SETUP_DONE_LINE)
    if st.get("status") == "running" and not force:
        return snapshot(st)
    if not force and not needed(st):
        return snapshot(st)
    if st.get("status") == "running":
        _reg_cancel(st)
        # A RESTART PUTS THE PEOPLE BACK FIRST (the User 2026-10-26: Run Setup pressed again mid-walk took a fresh
        # "before" copy that held the earlier walk's User "biill", so the next Voice ID met "Register needs User auth
        # first"). The people the running first boot made are undone, then the home is copied; the rest of what it
        # answered (the machine card's choice) stands, as a restart always kept it. Kill CRISSA_RESTART_RESTORES=0.
        if (st.get("snapshot") and not st.get("only") and cancel_leaves_nothing_on() and not marker().get("done")
                and os.environ.get("CRISSA_RESTART_RESTORES", "1").strip().lower() not in ("0", "off", "false", "no")):
            restore_before(str(st.get("run_id") or ""), keep=True, under=identity_root() / "people")
    seq = steps()
    new = empty_state()
    new.update(status="running", run_id=uuid.uuid4().hex[:12], index=0, step_id=seq[0]["id"] if seq else "",
               started_at=_now(), step_at=time.time(), first_boot=not marker_path().is_file() or setup_unfinished())
    if cancel_leaves_nothing_on() and not marker_path().is_file():
        try:
            new["snapshot"] = bool(snapshot_before(new["run_id"]).get("files") is not None)
        except Exception as e:  # noqa: BLE001  a snapshot that fails never stops Setup: the old cancel then
            print("[init] Setup's start: no snapshot (%s); a cancel writes the marker" % type(e).__name__, flush=True)
            new["snapshot"] = False
    save_state(new)
    if lila_holds(new):
        if not camera_voice_on():
            warm_cues()                       # round 20: Voice ID's lines rendered in her voice before its card opens
        host_arrive(new["run_id"])            # she walks on and waits on her loop; her welcome comes after Connect
    else:
        host_welcome(new["run_id"])           # Lila opens Init (the User 2026-10-11)
    _enter(new["run_id"])
    return snapshot(load_state())


def abort(run_id: str = "", why: str = "", by: str = "") -> dict[str, Any]:
    """Cancel: the walk ends, the marker says so (the next boot lands on listen; "set up again" starts it), a running
    Register or face section is cancelled at the coach, and she goes to her listening loop. `why` (round 21): the plain
    line the run ended with, kept on the run (`ended_why`) and shown on both Walls' Voice ID plate."""
    st = load_state()
    if st.get("status") != "running" or (run_id and run_id != st.get("run_id")):
        return snapshot(st)
    _reg_cancel(st)
    host_stop()
    st["status"] = "aborted"
    st["ended_t"] = time.time()
    st.pop("cancel_ask", None)
    # EVERY CANCEL NAMES ITS SENDER (the User 2026-10-26, Voice ID ended 3 s in and the log said only "cancelled"):
    # the Wall key, the page's Cancel, the walk itself - whoever ended it is in the line
    _note(st, "cancelled by %s" % (by or "the walk") + ((": " + str(why)) if why else ""))
    if why:
        st["ended_why"] = str(why)[:240]
    save_state(st)
    _release_hold(st, why="cancelled")
    if st.get("ids"):
        _ids_ended(st, cancelled=True)       # round 36
    if st.get("only"):
        _forget_led(st)
    if st.get("snapshot") and not st.get("only") and cancel_leaves_nothing_on() and not marker().get("done"):
        if cancel_stops_all_on():
            _cancel_everything_said()             # round 35: no line of hers or Lila's after a cancel
        if not (cancel_stays_office_on() and not st.get("ids") and office_hold()):   # (a pack: cancel_stays_office_on)
            _land_on_listen()                     # round 36: a first boot's cancel keeps her empty office instead
        else:
            print("[init] cancelled: the Wall stays on her empty office (she has not arrived)", flush=True)
        rid = str(st.get("run_id") or "")

        def unopened() -> None:
            for p in (browser_path(), run_path()):
                try:
                    p.unlink()                    # her window opens Setup again next time; and never an older run's
                except OSError:                   # file put back (.101 round 34: the old page after a cancel)
                    pass
        restore_before(rid, keep=True)            # init-run.json goes with the rest: no record of the walk
        unopened()
        _CANCELLED["home"] = str(_home())

        def again() -> None:
            _sleep(CANCEL_SECOND_PASS_S)
            restore_before(rid)                   # what the routine wrote as its cancel landed; then the copy goes
            unopened()
        if SYNC:
            again()
        else:
            threading.Thread(target=again, name="init-cancel-second-pass", daemon=True).start()
        if cancel_stops_all_on():
            _close_setup_window()                 # round 35: her Setup window goes; the Wall is the desktop again
        return dict(snapshot(load_state()), status="aborted", cancelled=True)
    if not marker().get("done"):
        _write_json(marker_path(), {"schema": MARKER_SCHEMA, "done": False, "cancelled_at": _now(),
                                    "version": VERSION, "steps_done": list(st.get("steps_done") or []),
                                    "connect": connect_record(st)})
    _land_on_listen()
    if why and st.get("only"):
        _say_ended(str(why))
    return snapshot(load_state())


# A CANCELLED FIRST BOOT KEEPS HER EMPTY OFFICE (round 36, the User on .101 round 35: "shows crissa at desk briefly on
# exit" - the cancel played `listen (forced)`). She has not arrived: the Wall stays on her empty office (THE OFFICE WAITS'
# held first frame) until Setup runs again. Add/Edit IDs (she is there already) lands as before. A pack only. Kill
# CRISSA_CANCEL_STAYS_OFFICE=0.
def cancel_stays_office_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_CANCEL_STAYS_OFFICE", "1").strip().lower() not in ("0", "off", "false", "no")


# A CANCEL STOPS EVERYTHING SHE WAS ABOUT TO SAY (round 35, .101 round 34: cancelled at Lila's handover, and after
# `cancelled` the player still spoke "Hi! I'm Crissa, on my way to your office now!" and her timing lines ran; then the
# open window drew the old Setup page). A cancelled first boot now:
# - stops the line on the air (the player's hard stop);
# - cancels the coach's job (her timing lines are a coach mouths run);
# - drops Init's own queue;
# - never puts an older run's init-run.json back;
# - closes her Setup window.
# /v1/init then reads status "aborted" with `next_start: "setup"`. `needed` stays false for this process: a page or Wall
# opened again must not start Setup at once after the person bailed out.
# Kill CRISSA_CANCEL_STOPS_ALL=0.
def cancel_stops_all_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_CANCEL_STOPS_ALL", "1").strip().lower() not in ("0", "off", "false", "no")


def _cancel_everything_said() -> None:
    _HOST["gen"] += 1                          # no runner of Lila's goes on
    _HUSH["on"] = False
    try:
        _coach_post("/cancel", {}, 4.0)        # her timing lines / the team's lines: a coach mouths run
    except Exception:  # noqa: BLE001
        pass
    _post("/v1/say", {"stop": True, "by": "init"})
    print("[init] cancelled: every line stopped (the player's line, the coach's run, Lila's queue)", flush=True)


def _reg_cancel(st: dict[str, Any]) -> None:
    live = ((st.get("register") or {}).get("state") in ("starting", "live")
            or (st.get("face") or {}).get("state") in ("starting", "live"))
    if live:
        _coach_post("/cancel", {}, 4.0)
        set_register_glass({"phase": "idle"})


def _finish(run_id: str, landing: bool = True) -> None:
    """The walk is done. `landing` False (THE TEAM AT THE END): the team's end does its own landing, microphone and
    closing line around this."""
    def fn(st: dict[str, Any]) -> None:
        st["status"] = "done"
        st["finished_at"] = _now()
        st["ended_t"] = time.time()
        _note(st, "done")
    st = _mutate(run_id, fn)
    if st is None:
        return
    _release_hold(st, why="done")
    if st.get("ids"):
        _ids_ended(st, cancelled=False)      # round 36
    if st.get("only") and _HOST.get("on"):
        host_leave(run_id)                            # round 20: Lila walks off after a Voice ID again she hosted
    if st.get("only") and (st.get("again") or {}).get("face"):
        face_again_settle(st)                         # round 34: the old stills set aside once the new ones bank
    if st.get("only"):
        # VOICE ID AGAIN (the Crissa menu): the first boot's marker stays as it was; this run is added to it
        m = marker() or {"schema": MARKER_SCHEMA, "done": True, "at": _now(), "version": VERSION}
        m.setdefault("again", []).append({"at": _now(), "steps": list(st.get("only") or []),
                                          "outcome": outcome(st)})
        m["again"] = m["again"][-10:]
        _write_json(marker_path(), m)
        _forget_led(st)
        if voice_id_routine() == "verbal":
            # NO FORCED CUT AFTER A SPOKEN VOICE ID (the User 2026-10-12, .101: "seemed to restart after it was done" -
            # the landing's forced `play: listen`): the routine moved her nowhere, so she carries on where she is
            print("[init] Voice ID again ended; she carries on where she is (no landing)", flush=True)
            with _LOCK:
                cur = load_state()
                if cur.get("run_id") == run_id:
                    cur["landing"] = {"done_at": _now(), "skipped": "spoken Voice ID"}
                    save_state(cur)
            return
        _spawn("landing", _landing, run_id)
        return
    drop_snapshot()                                   # round 34: a finished walk keeps what it made
    try:
        browser_path().unlink()                       # the first boot's browser open is spent
    except OSError:
        pass
    svc = install_service()
    _write_json(marker_path(), {"schema": MARKER_SCHEMA, "done": True, "at": _now(), "version": VERSION,
                                "steps_done": list(st.get("steps_done") or []), "service": svc,
                                "connect": connect_record(st), "outcome": outcome(st)})
    if not landing:
        return
    if st.get("first_boot"):
        mic_at_end(run_id)                            # before her opening line (MIC ON AT THE END)
    _spawn("landing", _landing, run_id)


def outcome(st: dict[str, Any]) -> dict[str, str]:
    """What each step really left behind, in words (the User 2026-10-10, .101: init.json listed the face step among
    steps_done with no photo saved). steps_done keeps the steps walked; this says what each one did."""
    out: dict[str, str] = {}
    c = connect_record(st)
    if c or "connect" in (st.get("steps_done") or []):
        out["connect"] = {"tested": "connected", "later": "set up later"}.get(c.get("state") or "", "not connected")
    m = st.get("machine") or {}
    if m:
        out["machine"] = ("measured: %s" % m.get("tier")) if m.get("tier") else (
            "skipped" if m.get("permission") == "skip" else "not measured")
    reg = st.get("register") or {}
    if reg:
        n = int(reg.get("voice") or 0)
        out["voice_id"] = ("%d voice print%s" % (n, "" if n == 1 else "s")) if reg.get("state") == "done" else (
            str(reg.get("state") or "not finished"))
    fc = st.get("face") or {}
    if fc:
        n = int(fc.get("faces") or 0)
        out["face"] = ("%d face photo%s" % (n, "" if n == 1 else "s")) if fc.get("state") == "done" else (
            {"no_camera": "no camera", "no_person": "not taken: no name was given"}.get(
                str(fc.get("state") or ""), str(fc.get("state") or "not finished")))
    for sid in LILA_KINDS:
        d = st.get(sid) or {}
        if d:
            out[sid] = str(d.get("state") or "not finished")
    return out


# VOICE ID IS SPOKEN (the User 2026-10-12, .101 round 10; the coach's voice_register "THE VERBAL ROUTINE"): every entry
# - Setup's Voice ID step, Voice ID again from either Wall's Crissa menu, the done plate's key - runs the counts with the
# announcer's cues, and nothing waits on her window: Voice ID again starts with the User's own name (no name plate, no
# page to bring forward). CRISSA_VOICE_ID_ROUTINE=video (or <state home>/voice-id.json {"routine": "video"}) is the
# 2026-10-11 routine and its page, byte for byte. The house never runs this (its own Register).
VOICE_ID_ROUTINE_ENV = "CRISSA_VOICE_ID_ROUTINE"
VOICE_ID_ROUTINE_FILE = "voice-id.json"


def voice_id_routine(env: dict | None = None, home: Path | None = None) -> str:
    """'verbal' (a pack's default) or 'video' (the house's, and the switch)."""
    e = os.environ if env is None else env
    v = str(e.get(VOICE_ID_ROUTINE_ENV) or "").strip().lower()
    if v in ("verbal", "video"):
        return v
    try:
        got = json.loads(((home or _home()) / VOICE_ID_ROUTINE_FILE).read_text(encoding="utf-8")) or {}
        r = str(got.get("routine") or "").strip().lower()
        if r in ("verbal", "video"):
            return r
    except (OSError, ValueError, AttributeError):
        pass
    return "video" if crissa_home.is_house() else "verbal"


def stored_user_name() -> str:
    """The User's name as Init kept it (user.json name), '' when there is none."""
    try:
        u = json.loads(user_path().read_text(encoding="utf-8")) or {}
        return str(u.get("name") or u.get("display") or "").strip()[:40]
    except (OSError, ValueError, AttributeError):
        return ""


# FACE PHOTOS AGAIN (round 34, the User 2026-10-19 on .101's round 31-32 ask "a way to retake only the photos"): the
# Crissa menu's item beside Voice ID again runs Setup's Face photos step alone, for the User, behind Voice ID again's
# own proof (lib/crissa_menu.voice_id_gate: his face through presence, or the login password). Every angle is asked
# (front, his left, his right), with the round 33 turn coaching. The stills she had stay until the new ones have
# banked every angle; then they are set aside in the person's folder (face-replaced-<time>/), so a retake that fails
# loses nothing. A pack only. Kill CRISSA_FACE_AGAIN=0.
def face_again_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_FACE_AGAIN", "1").strip().lower() not in ("0", "off", "false", "no")


def _face_files(pid: str) -> list[str]:
    return sorted(p.name for p in (identity_root() / "people" / pid / "face").glob("*.json")) if pid else []


def start_face_again(*, proof_hold: bool = False, led_by: str = "") -> dict[str, Any]:
    """FACE PHOTOS AGAIN: Setup's Face photos step alone, for the User; refused while a walk runs."""
    if not face_again_on():
        return {"ok": False, "error": "Face photos again is off here (CRISSA_FACE_AGAIN=0)"}
    st = load_state()
    if st.get("status") == "running":
        return dict(snapshot(st), ok=False, error="Setup is already running: finish it there first.")
    pid = _owner_id()
    if not pid:
        return dict(snapshot(st), ok=False, error="Run Setup first: Crissa takes your photos once she knows you.")
    seq = steps()
    i = next((k for k, s_ in enumerate(seq) if s_.get("id") == "face"), None)
    if i is None:
        return dict(snapshot(st), ok=False, error="this install's walk has no Face photos step")
    new = empty_state()
    new.update(status="running", run_id=uuid.uuid4().hex[:12], index=i, step_id="face", started_at=_now(),
               step_at=time.time(), first_boot=False, only=["face"], connect=dict(st.get("connect") or {}),
               again={"face": True, "proof_hold": bool(proof_hold), "led_by": str(led_by or "")[:40],
                      "face_before": _face_files(pid)})
    save_state(new)
    print("[init] Face photos again for %s (%d stills kept until the new ones bank)"
          % (pid, len(new["again"]["face_before"])), flush=True)
    if proof_hold:
        threading.Thread(target=_hold_cap, args=(new["run_id"],), name="init-face-again-hold", daemon=True).start()
    _enter(new["run_id"])
    return dict(snapshot(load_state()), ok=True)


def face_again_settle(st: dict[str, Any]) -> dict[str, Any]:
    """At a Face photos again run's end: with every angle banked in NEW stills, the old ones are set aside."""
    again = st.get("again") or {}
    pid = _owner_id()
    if not again.get("face") or not pid:
        return {}
    before = set(again.get("face_before") or [])
    folder = identity_root() / "people" / pid / "face"
    new_bins: dict[str, int] = {}
    as_asked = face_front_as_asked_on()
    for f in sorted(folder.glob("*.json")):
        if f.name in before:
            continue
        d = _load_json(f)
        enc = d.get("encoding") or d.get("embedding")
        if isinstance(enc, list) and len(enc) >= 8:
            b = str(d.get("bin") or d.get("pose") or "unknown")
            if as_asked and str(d.get("pose_asked") or "") == "front" and b.startswith("three_quarter"):
                b = "front"
            new_bins[b] = new_bins.get(b, 0) + 1
    missing = face_missing(new_bins)
    if missing or not before:
        print("[init] Face photos again: %s; the stills she had stay" % (
            "missing %s" % ", ".join(missing) if missing else "nothing to set aside"), flush=True)
        return {"replaced": 0, "missing": missing}
    aside = identity_root() / "people" / pid / ("face-replaced-%s" % time.strftime("%Y%m%d-%H%M%S"))
    aside.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in sorted(before):
        try:
            os.replace(folder / name, aside / name)
            n += 1
        except OSError:
            pass
    sync_faces(pid)
    print("[init] Face photos again: every angle new; %d old stills set aside in %s" % (n, aside.name), flush=True)
    return {"replaced": n, "missing": []}


def start_voice_id(name: str = "", *, replace: bool = False, proof_hold: bool = False,
                   led_by: str = "") -> dict[str, Any]:
    """VOICE ID AGAIN (the Crissa menu, the User 2026-10-10): Init's own Voice ID step, alone, on both Walls - the name
    Init kept (the person confirms or changes it), the coach's routine, new voice prints beside the old ones. The first
    boot's marker stays done; this run is added to it (`again`). Refused while a walk is already running.

    ONE PAGE, IN HER WINDOW (the User 2026-10-11, .101 round 9: the desk Wall's key opened the browser Wall for the
    password, no session ever reached the coach, and "no name entry avail either"): the page (/init?voice_id=1) sends
    `name` with the start, so the take begins at once - the plate's name state is skipped, and so is the desk Wall's own
    plate with only SKIP on it. The name only renames the User: the prints go to the User's own person id (reg_name).
    `replace`: the prints she had are set aside once the new ones are made (never before, so a failed take loses
    nothing). `proof_hold`: the password's Secure hold, released when this run ends. `led_by`: the page leading it
    (mark_led), so the desk Wall says "Voice ID is open in Crissa's window" and draws no second plate."""
    if crissa_home.is_house():
        return {"ok": False, "error": "the house has its own Register"}
    st = load_state()
    if st.get("status") == "running":
        return dict(snapshot(st), ok=False, error="Setup is already running: finish it there first.")
    seq = steps()
    i = next((k for k, s_ in enumerate(seq) if s_.get("id") == "voice_id"), None)
    if i is None:
        return dict(snapshot(st), ok=False, error="this install's walk has no Voice ID step")
    new = empty_state()
    new.update(status="running", run_id=uuid.uuid4().hex[:12], index=i, step_id="voice_id", started_at=_now(),
               step_at=time.time(), first_boot=False, only=person_steps(seq, i), connect=dict(st.get("connect") or {}),
               again={"replace": bool(replace), "proof_hold": bool(proof_hold), "led_by": str(led_by or "")[:40]})
    verbal = voice_id_routine() == "verbal"
    if verbal:
        # no page leads a spoken Voice ID: each Wall draws its own plate, which follows the takes and ends by itself
        new["again"]["led_by"] = ""
        if not str(name or "").strip():
            name = stored_user_name()            # the User's own name: no name plate (the .101 round 9 dead end)
    save_state(new)
    if led_by and not verbal:
        mark_led(led_by)
    if proof_hold:                               # its own thread, never inline: it waits for the run to end
        threading.Thread(target=_hold_cap, args=(new["run_id"],), name="init-voice-id-hold", daemon=True).start()
    _person_begin(new["run_id"])
    _enter(new["run_id"])
    out: dict[str, Any] = {}
    if str(name or "").strip():
        got = reg_name(_reg_io(new["run_id"], "voice_id", "register"), str(name))
        if not got.get("ok"):                    # the plate keeps the name, with what is wrong with it
            out["name_error"] = str((load_state().get("register") or {}).get("name_error") or got.get("error") or "")
    return dict(snapshot(load_state()), ok=True, **out)


AGAIN_HOLD_MAX_S = 900.0                   # the password's hold never outlives this, whatever happens to the run
HOLD_CHECK_S = 5.0


def _hold_cap(run_id: str) -> None:
    """The password's Secure hold is let go when its run ends (_release_hold); this is the belt: never longer than
    AGAIN_HOLD_MAX_S, even if the player is stopped in the middle of the take."""
    end = time.time() + AGAIN_HOLD_MAX_S
    tick = threading.Event()
    while time.time() < end:
        st = load_state()
        if st.get("run_id") != run_id or st.get("status") != "running":
            return                               # the run ended: _finish / abort let go of it
        tick.wait(HOLD_CHECK_S)
    _release_hold(load_state(), why="the run outlived %d s" % int(AGAIN_HOLD_MAX_S))


def _release_hold(st: dict[str, Any], why: str = "") -> None:
    """Let go of the password's Secure hold this run kept (crissa_menu.release_proof), once."""
    ag = st.get("again") if isinstance(st.get("again"), dict) else {}
    if not ag.get("proof_hold") or ag.get("released"):
        return
    rid = str(st.get("run_id") or "")
    with _LOCK:                                  # recorded on the run whatever its status now (done, aborted)
        cur = load_state()
        if rid and cur.get("run_id") == rid:
            if (cur.get("again") or {}).get("released"):
                return
            cur.setdefault("again", {})["released"] = True
            save_state(cur)
    try:
        import crissa_menu
        crissa_menu.release_proof()
        print("[init] Voice ID again: the password's hold is let go%s" % ((" (%s)" % why) if why else ""), flush=True)
    except Exception as e:  # noqa: BLE001
        print("[init] Voice ID again: the hold was not let go: %s: %s" % (type(e).__name__, e), flush=True)


def _forget_led(st: dict[str, Any]) -> None:
    """A Voice ID again that a page led leaves no record behind it, so a later Setup is never said to be open there."""
    if (st.get("again") or {}).get("led_by"):
        try:
            browser_path().unlink()
        except OSError:
            pass


def mark_led(page: str) -> None:
    """The page that leads a Voice ID again (/init?voice_id=1): her own window when it shows /init now, else a page in
    the person's browser. browser_led then makes the desk Wall step aside (init_v2_render.setup_in_her_window)."""
    url = (str(_CFG.get("base") or "") or crissa_ports.url("player")).rstrip("/") + "/init"
    by = "page"
    try:
        import crissa_window
        if crissa_window.enabled() and crissa_window.state(url).get("setup"):
            by = "window"
    except Exception:  # noqa: BLE001  a window that cannot be read is the person's page
        by = "page"
    mark_browser_opened(url, by=by)


def page_served(query: str = "") -> bool:
    """/init (the page) is served while serves() - and in a pack for Voice ID again (?voice_id=1) at any time, so the
    page in her window can show the name and the password before a run exists (the User 2026-10-11, .101 round 9)."""
    if serves():
        return True
    if crissa_home.is_house():
        return False
    import urllib.parse as _up
    return (_up.parse_qs(str(query or "")).get("voice_id") or [""])[0] == "1"


def install_service(env: dict | None = None, install=None) -> dict[str, Any]:
    """The launcher as a service of the login (lib/crissa_service). Only under the launcher (CRISSA_LAUNCHER_ARGS);
    started now unless this process already holds the desk (CRISSA_DESK_HELD=1) or already runs as the service."""
    env = os.environ if env is None else env
    raw = env.get("CRISSA_LAUNCHER_ARGS")
    if raw is None:
        return {"ok": False, "skipped": "not started by the launcher (scripts/crissa.py): nothing to install"}
    if str(env.get("CRISSA_SERVICE_INSTALL") or "1").strip() == "0":
        return {"ok": False, "skipped": "CRISSA_SERVICE_INSTALL=0"}
    if str(env.get("CRISSA_SERVICE") or "").strip():
        return {"ok": True, "skipped": "the launcher already runs as its service (%s)" % env.get("CRISSA_SERVICE")}
    try:
        args = json.loads(raw)
        if install is None:
            import crissa_service
            install = crissa_service.install
        start_now = str(env.get("CRISSA_DESK_HELD") or "").strip() != "1"
        got = install([str(x) for x in args or []], home=_home(), start=start_now)
    except Exception as e:  # noqa: BLE001  a failed install must not fail the first boot; it is reported
        got = {"ok": False, "note": "the launcher's service was not installed: %s: %s" % (type(e).__name__, e)}
    return {k: got.get(k) for k in ("ok", "manager", "file", "started", "note") if k in got}


def _spawn(name: str, fn, *args) -> None:
    if SYNC:
        fn(*args)
        return
    t = threading.Thread(target=fn, args=args, name=f"init-{name}", daemon=True)
    _THREADS[name] = t
    t.start()


def _advance(run_id: str, expect: str | None = None) -> None:
    """The next step. `expect`: only when the walk is still on that step (checked and moved under one lock, so two
    endings of one step - a click and a timer - never move the walk twice)."""
    seq = steps()
    moved = {"ok": False}

    def fn(st: dict[str, Any]) -> None:
        if expect is not None and st.get("step_id") != expect:
            return
        if st.get("ending"):
            return                                 # the team is speaking: the walk has nowhere further to go
        moved["ok"] = True
        done = list(st.get("steps_done") or [])
        if st.get("step_id") and st["step_id"] not in done:
            done.append(st["step_id"])
        st["steps_done"] = done
        i = int(st.get("index") or 0) + 1
        nxt = seq[i]["id"] if i < len(seq) else ""
        if st.get("only") and nxt not in (st.get("only") or []):
            nxt = ""                               # a step started again from the menu: it alone, then done
        if not nxt and (st.get("ids") or {}).get("stage") == "user_edits":
            # round 39 (N5): the User's house edits on a new person's run are done: hand over to the person
            st["ids"]["stage"] = "handover"
            st["only"] = ["voice_id"]
            st["step_id"] = ""
            st["step_at"] = time.time()
            moved["handover"] = True
            return
        if not nxt and team_end_on(st):
            # THE TEAM AT THE END: the run stays on its last card (no keys left on it) while the team speaks
            st["ending"] = {"phase": "team", "at": _now()}
            st["step_at"] = time.time()
            moved["team"] = True
            return
        st["index"] = i
        st["step_id"] = nxt
        st["step_at"] = time.time()
    st = _mutate(run_id, fn)
    if st is None or not moved["ok"]:
        return
    if moved.get("team"):
        _spawn("team-end", _team_end, run_id)
        return
    if moved.get("handover"):
        print("[init] Add/Edit IDs: the User's edits are saved; now %s's turn" % ((st.get("ids") or {}).get("name") or "the person"),
              flush=True)
        return
    if not st.get("step_id"):
        _finish(run_id)
        return
    if st.get("step_id") in ("room", "hours"):     # .101 round 29: her timing lines ready before the handover
        warm_timing_lines(run_id)
        warm_opener(run_id)                        # round 40 N22: her opener too, with the name Voice ID gave
    if one_page_on(st) and st.get("step_id") != "connect":
        start_question_listener(run_id)            # ONE-PAGE SETUP: THE QUESTION LISTENER, once a run
    if lila_holds(st):
        if (st.get("steps_done") or [])[-1:] == ["connect"]:
            host_after_connect(run_id, st["step_id"])   # her welcome, now that Connect is done; she stays on
    elif step_cfg(st["step_id"]).get("kind") not in LILA_KINDS:
        host_leave(run_id)                     # her cards are done: she slides back off her plate
    _enter(run_id)


def _enter(run_id: str) -> None:
    st = load_state()
    sid = st.get("step_id") or ""
    kind = step_cfg(sid).get("kind") or ""
    if kind == "machine":
        par = setup_parallel(st)
        io = _standin_io(run_id, anywhere=par)
        card_enter(io)
        if par and (io.get() or {}).get("state") not in ("ask", None):
            _leave_machine(run_id)                 # an answer already given goes on in the background (SETUP IN PARALLEL)
    elif kind == "register":
        reg_enter(_reg_io(run_id, "voice_id", "register"))
    elif kind == "face":
        face_enter(_reg_io(run_id, "face", "face"))
    elif kind == "room":
        room_enter(_reg_io(run_id, sid, "room"))
    elif kind == "hours":
        hours_enter(_reg_io(run_id, sid, "hours"))
    # connect waits for Test, Set up later or Continue


# ── connect ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _connect_cfg(body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """(the config Init's TEST uses, where it came from). Typed fields first; else the agent the Connect page just
    passed (its whole config, from this process's memory) when it is not the saved one; else the saved agent
    (2026-10-10: TEST tested only the saved agent.json, so a pass on the page that was not saved could never count)."""
    import crissa_agent as za
    cfg = za.load()
    if str(body.get("url") or "").strip():
        why = za.validate(body)
        if why:
            raise ValueError(why)
        same_host = za._host(str(body["url"])) == za._host(str(cfg.get("url") or ""))
        trial = {k: str(body[k]).strip() for k in ("url", "key", "model", "preset") if body.get(k)}
        if not trial.get("key") and same_host and cfg.get("key"):
            trial["key"] = cfg["key"]               # for the test call only; connect_test stores typed fields
        if same_host:
            trial.update({k: cfg[k] for k in ("session_header", "no_prompt", "system_prompt") if cfg.get(k)})
        return trial, "typed"
    page = za.last_pass_cfg()
    if page and za.fingerprint(page) != za.fingerprint(cfg):
        return page, "connect page"
    return cfg, "saved"


def connect_test(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    import crissa_agent as za
    cfg: dict[str, Any] = {}
    src = ""
    try:
        cfg, src = _connect_cfg(body)
    except ValueError as e:
        res = {"ok": False, "error": str(e)}
    else:
        timeout = float(step_cfg("connect").get("timeout_s") or 60)
        res = za.test(cfg, timeout=timeout) if za.configured(cfg) else {"ok": False, "error": "no endpoint address"}
        if res.get("ok") and src in ("typed", "connect page"):
            # only what was typed (or passed on the page) is stored: a key read from where it lives is never copied
            upd = {k: str(cfg[k]).strip() for k in ("url", "model", "preset") if cfg.get(k)}
            if str(cfg.get("key") or "").strip() and (src == "connect page" or str(body.get("key") or "").strip()):
                upd["key"] = str(cfg["key"]).strip()
            za.save(upd)

    def fn(st: dict[str, Any]) -> None:
        c = st.setdefault("connect", {})
        n = int(c.get("n") or 0) + 1
        c["n"] = n
        c["last"] = {"n": n, "ok": bool(res.get("ok")), "error": res.get("error"), "ms": res.get("ms"),
                     "reply": (res.get("reply") or "")[:120], "at": _now(), "t": time.time(), "where": "init",
                     "thinking": bool(res.get("thinking")), "from": src}
        if res.get("ok"):
            c["tested"] = {"n": n, "at": _now(), "model": str(cfg.get("model") or "")}
        else:
            c.pop("tested", None)                 # a failed test takes back any earlier pass
    _mutate(run_id, fn)
    return {"ok": bool(res.get("ok")), "error": res.get("error"), "ms": res.get("ms"), "thinking": bool(res.get("thinking"))}


def connect_adopt(run_id: str, offer_id: str) -> dict[str, Any]:
    """The user picked a Detected endpoint: store it (its key by reference) and test it, one click."""
    import crissa_agent as za
    try:
        za.adopt(offer_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return connect_test(run_id, {})


def _page_test() -> dict[str, Any]:
    """The Connect page's last test, when it tested the agent that is saved now ({} otherwise)."""
    try:
        import crissa_agent as za
        rec = za.last_test(_CFG.get("home"))
        cfg = za.load()
        if rec and za.configured(cfg) and rec.get("fp") == za.fingerprint(cfg):
            return rec
    except Exception:  # noqa: BLE001
        pass
    return {}


def connect_passed(st: dict[str, Any]) -> bool:
    """Continue needs the LATEST test to have passed: the walk's own (the rule since 2026-10-04), or, when it is newer,
    the Connect page's test of the agent that is saved now (2026-10-10: a pass on the page counts)."""
    c = st.get("connect") or {}
    last, tested = c.get("last") or {}, c.get("tested") or {}
    page = _page_test()
    if page and float(page.get("at") or 0) > float(last.get("t") or 0):
        return bool(page.get("ok"))
    return bool(last.get("ok")) and bool(tested) and tested.get("n") == last.get("n") == c.get("n")


def connect_last(st: dict[str, Any]) -> dict[str, Any]:
    """The latest test the plate shows: the walk's own, or the Connect page's when that one is newer."""
    last = dict((st.get("connect") or {}).get("last") or {})
    page = _page_test()
    if page and float(page.get("at") or 0) > float(last.get("t") or 0):
        return {"ok": bool(page.get("ok")), "where": "connect page", "model": page.get("model"),
                "ms": page.get("ms"), "reply": page.get("reply") or "",
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(float(page.get("at") or 0))),
                "error": None if page.get("ok") else "the test on the Connect page did not pass"}
    return last


# How she talks, waits and listens (config/crissa-talk.json under <crissa home>/talk.json, 2026-10-06).
TALK_SECTIONS = ("speak_tuning", "ear", "announce", "reply", "speak")


def talk(body: dict[str, Any]) -> dict[str, Any]:
    """`talk`: with {"talk": {section: {...}}} save it (validated), else show what is in force."""
    import crissa_talk
    upd = body.get("talk")
    if isinstance(upd, dict) and upd:
        upd = {k: v for k, v in upd.items() if k in TALK_SECTIONS}
        try:
            crissa_talk.save(upd)
        except ValueError as e:
            return {"ok": False, "error": str(e), "talk": _talk_view()}
    return {"ok": True, "talk": _talk_view()}


def _talk_view() -> dict[str, Any]:
    import crissa_talk
    cfg = crissa_talk.load()
    return {k: cfg.get(k) for k in TALK_SECTIONS}


# SET UP LATER (the User 2026-10-10): Connect's passing test stays the normal way on, but a walk no longer stops there
# when no agent answers. She says she is not connected yet (not_connected_line, the conductor's /v1/ask) until an agent
# is saved through Connect. Kill: CRISSA_CONNECT_REQUIRED=1 (the hard gate again).
CONNECT_REQUIRED = "CRISSA_CONNECT_REQUIRED"
NOT_CONNECTED_LINE = "I'm not connected to an agent yet. Open the Connect page to set one up, and I'll answer you."
NOT_CONNECTED_EVERY_S = 30.0
_NOT_CONNECTED = {"at": 0.0}


def connect_required(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(CONNECT_REQUIRED) or "").strip().lower() in ("1", "on", "true", "yes")


def connect_record(st: dict[str, Any]) -> dict[str, Any]:
    """init.json's `connect`: {"state": "tested", "at", "model"} or {"state": "later", "at"}; {} when neither."""
    c = st.get("connect") or {}
    if c.get("later"):
        return {"state": "later", "at": (c.get("later") or {}).get("at")}
    if c.get("tested"):
        return {"state": "tested", "at": (c.get("tested") or {}).get("at"), "model": (c.get("tested") or {}).get("model")}
    if c.get("page"):
        return {"state": "tested", "at": (c.get("page") or {}).get("at"), "model": (c.get("page") or {}).get("model"),
                "where": "connect page"}
    return {}


def connect_continue(run_id: str) -> dict[str, Any]:
    st = load_state()
    if not connect_passed(st):
        return {"ok": False, "error": "the latest test call has not passed"}
    page = _page_test()
    if page and not (st.get("connect") or {}).get("tested"):
        _mutate(run_id, lambda s: s.setdefault("connect", {}).update(
            page={"at": _now(), "model": page.get("model")}))
    _advance(run_id, expect="connect")
    return {"ok": True}


def connect_later(run_id: str, by: str = "") -> dict[str, Any]:
    """SET UP LATER, recorded with who chose it (`by`: the Wall's plate or the browser page). Both ask first (the User
    2026-10-10, .101: one click on the desk Wall's plate recorded it, after a Connect dialog with no presets); the walk
    itself records what it is told."""
    if connect_required():
        return {"ok": False, "error": "Connect needs a passing test on this install"}
    rec = {"at": _now()}
    if by:
        rec["by"] = str(by)[:20]

    def fn(s: dict[str, Any]) -> None:
        c = s.setdefault("connect", {})
        c.pop("later_ask", None)
        c.update(later=rec)
        _note(s, "connect: set up later%s (she answers that she is not connected yet)" % (" on the %s" % by if by else ""))
    st = _mutate(run_id, fn)
    if st is not None:
        _advance(run_id, expect="connect")
    return {"ok": st is not None, "later": True}


def connect_later_ask(run_id: str, back: bool = False) -> dict[str, Any]:
    """The desk Wall's first press of SET UP LATER: the plate asks (YES, SET UP LATER / BACK) instead of recording."""
    st = _mutate(run_id, lambda s: (s.setdefault("connect", {}).pop("later_ask", None) if back else
                                    s.setdefault("connect", {}).update(later_ask={"at": _now()})))
    return {"ok": st is not None, "asking": not back}


def not_connected_line(now: float | None = None, every_s: float = NOT_CONNECTED_EVERY_S) -> str:
    """What she says when someone talks to her and no agent is saved: the line, at most once every `every_s`;
    '' otherwise, and always '' on the house."""
    if crissa_home.is_house():
        return ""
    t = time.time() if now is None else float(now)
    if _NOT_CONNECTED["at"] and t - _NOT_CONNECTED["at"] < every_s:
        return ""
    _NOT_CONNECTED["at"] = t
    return NOT_CONNECTED_LINE


# ── the voice and the aligner ────────────────────────────────────────────────────────────────────────────────────────
def _machine_mod():
    try:
        import crissa_machine
        return crissa_machine
    except ImportError:
        return None


def _voice_tier() -> dict:
    """The voice tier (lib/crissa_voice_tiers.ensure): probed once, kept in <state home>/voice-tier.json."""
    try:
        import crissa_voice_tiers
        return crissa_voice_tiers.ensure(_home(), faces_live=True)   # this walk's own state; the faces speak
    except Exception as e:  # noqa: BLE001  a probe that breaks never stops the walk
        return {"say": ["voice: the machine probe failed (%s); the default order runs" % type(e).__name__]}


def _voice_install_mod():
    try:
        import crissa_voice_install
        return crissa_voice_install if crissa_voice_install.enabled() else None
    except ImportError:
        return None


VOICE_LINES = 6        # progress lines the plate keeps


def _voice_install_state() -> dict[str, Any]:
    """The voice install as it runs or last ended (lib/crissa_voice_install.state); {"state": "off"} without it."""
    mod = _voice_install_mod()
    if mod is None:
        return {"state": "off"}
    try:
        st = mod.state(_home())
    except Exception:  # noqa: BLE001
        st = {"state": "idle"}
    return {k: st.get(k) for k in ("state", "engine", "lines", "line", "at") if k in st}


def voice_install(run_id: str = "", *, retry: bool = False, put=None) -> dict[str, Any]:
    """Install her voice (lib/crissa_voice_install.run) with its progress on the plate. The voice's lane is started by
    the running launcher the moment it is installed (lib/crissa_voice_install.launcher_start -> lib/crissa_procs
    request -> scripts/crissa.py LATE_LANES), so she speaks without a restart. Never raises."""
    mod = _voice_install_mod()
    if mod is None:
        return {"ok": True, "state": "off"}
    own_put = put

    def put(**kw) -> None:  # noqa: F811
        if own_put is not None:
            own_put(**kw)
        elif run_id:
            _mutate(run_id, lambda s: s.setdefault("voice_install", {}).update(kw))

    lines: list[str] = []
    last = [0.0]

    def progress(text: str) -> None:
        print("[init] voice: %s" % str(text)[:300], flush=True)           # the whole output stays in the log
        lines.append(mod.plain(text, 120))
        del lines[:-VOICE_LINES]
        now = time.monotonic()
        if now - last[0] >= 0.5 or str(text).startswith(("Installing", "voice:")):
            last[0] = now
            put(lines=list(lines))

    put(state="installing", lines=[], line="")
    try:
        res = mod.run(_home(), progress=progress, retry=retry, start=mod.launcher_start(_home()))
    except Exception as e:  # noqa: BLE001  a broken installer never stops the walk
        res = {"ok": False, "chosen": "text", "line": "Her voice could not be installed: %s. She will speak in "
               "text for now; you can retry from the Connect page." % mod.plain("%s: %s" % (type(e).__name__, e))}
    state = "done" if res.get("ok") else "failed"
    if res.get("ok") and run_id:
        prime_her_level(run_id)              # round 19: before "done" lets her first live line start
    put(state=state, engine=res.get("chosen"), lines=list(lines), line=str(res.get("line") or ""))
    if run_id:
        _mutate(run_id, lambda s: _note(s, "voice: " + str(res.get("line") or "")))
    return dict(res, state=state)


# HER LEVEL, READ OFF A WHOLE LINE BEFORE SHE SPEAKS (round 19, the User 2026-10-14; lib/loudness ONE LOUDNESS THROUGH
# SETUP). Her first Setup line is said live (her welcome, round 19's films said in the install's voice), and a live line
# is levelled on its first second, which on this machine's Pocket read 2-7 dB hotter than the whole line; the coach's
# first whole line of hers would then replace that, a step in her level part-way through Setup. So once her voice is
# installed, and before the install says so (which is what her welcome waits for), one line of hers (TEAM_LINE) is
# rendered whole and its gain kept for the run: her live lines and the coach's sit on one level from her first word.
# About one render (1.5 s on .101's Pocket). Only her main voice is asked, once it answers (a lane the launcher is still
# starting: up to HER_LEVEL_WAIT_S), never a backup: a gain read off another engine is no gain for hers.
# Kill: CRISSA_SETUP_HER_LEVEL=0, or CRISSA_SETUP_ONE_LOUDNESS=0.
HER_LEVEL_WAIT_S = 20.0


def prime_her_level(run_id: str, synth=None, up=None) -> bool:
    """Keep Lila's Setup gain for `run_id` off one whole line of hers. True when kept. Never raises."""
    if crissa_home.is_house() or not run_id:
        return False
    if os.environ.get("CRISSA_SETUP_HER_LEVEL", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and synth is None:
        return False                         # a test never reaches a real voice
    try:
        import loudness as L
        if not L.setup_one_loudness_on() or L.setup_run_id(_home()) != run_id:
            return False
        import crissa_tts
        cfg = crissa_tts.config()
        eng = str(cfg.get("engine") or "")
        key = L.setup_level_key("lila", eng, L.LEVEL_TARGET_DB)
        if L.setup_gain(_home(), run_id, key)[1] == "line":
            return False
        if eng in getattr(crissa_tts, "STREAMED", {}):
            up = up or (lambda: crissa_tts.stream_up(eng))
            t0, polls = time.time(), int(HER_LEVEL_WAIT_S / 0.5) + 1     # time AND polls (a test's _sleep is a no-op)
            while not up() and polls > 0 and time.time() - t0 < HER_LEVEL_WAIT_S:
                polls -= 1
                _sleep(0.5)
        pcm, sr = (synth or crissa_tts.synth)(TEAM_LINE, "lila", cfg=dict(cfg, backup=[], fallback=False), timeout=30.0)
        g = L.line_gain(pcm, sr, L.LEVEL_TARGET_DB)
        if g is None or not L.setup_gain_keep(_home(), run_id, key, g, "line"):
            return False
        print("[init] her level: %s keeps gain %.2f for Setup (one whole line of hers)" % (key, g), flush=True)
        return True
    except Exception as e:  # noqa: BLE001  her line-start level stands, as before
        print("[init] her level: not read ahead (%s)" % type(e).__name__, flush=True)
        return False


def start_lanes(res: dict[str, Any]) -> list[str]:
    """THE VOICE INIT PICKED STARTS WITHOUT A RESTART (the User 2026-10-10): the launcher planned every streamed voice
    it did not start as a waiting lane (scripts/crissa.py LATE_LANES); Init asks it to start the ones it chose - her
    voice, her backup, the announcer's - through the request queue (lib/crissa_voice_install.launcher_start). The
    install itself asks only for a voice it installed just now; this also covers one that was already here when the
    launcher planned from its own unwritten pick. A lane already running answers "already running". Returns the
    engines asked for; [] with no launcher."""
    mod = _voice_install_mod()
    start = mod.launcher_start(_home()) if mod is not None else None
    if start is None:
        return []
    skip = set(getattr(mod, "NO_INSTALL", ("text", "say")) or ())
    asked = []
    for eng in (res.get("chosen"), (res.get("backup") or {}).get("chosen"), (res.get("announcer") or {}).get("chosen")):
        if eng and eng not in skip and eng not in asked:
            try:
                start(str(eng))
                asked.append(str(eng))
            except Exception:  # noqa: BLE001  the next start of Crissa starts it anyway
                pass
    return asked


def aligner_after_voice() -> dict[str, Any]:
    """THE WORD ALIGNER INSTALLS DURING INIT, AFTER THE VOICE, ON EVERY MACHINE (the User 2026-10-10). Only the machine
    check's measured align stage (after Allow, lib/crissa_machine.align_default) turns it off; a choice the person saved
    on the Connect page wins; CRISSA_ALIGNER_RAM_RULE=1 is the 2026-10-07 RAM rule again. The download runs in the
    background (lib/crissa_aligner.set_better); the walk does not wait for it. Returns what it did."""
    try:
        import crissa_aligner as A
    except ImportError:
        return {"state": "absent"}
    try:
        if not A.applicable():
            return {"state": "off", "why": "this install runs its own aligner, or CRISSA_BETTER_MOUTHS=0"}
        saved = A._saved()
        if saved and saved.get("by") not in ("default", "init"):
            return {"state": "yours", "on": bool(saved.get("better"))}
        want = A.default_on()
        if not want:
            return {"state": "off", "why": "by %s" % A.default_by()}
        if A.installed():
            A.set_better(True, by="init")
            return {"state": "on"}
        A.set_better(True, by="init")
        return {"state": "installing"}
    except Exception as e:  # noqa: BLE001  the aligner never stops the walk
        return {"state": "error", "why": "%s: %s" % (type(e).__name__, str(e)[:120])}


# ── THE MACHINE CARD (the User 2026-10-10): right after Connect, before the voice is picked or installed ────────────
# "The only hardware checkpoint on a new install is this one, and it decides the sound stack." The states:
#     ask          the card (lib/crissa_machine.card): what the check does here, how long, no sound, nothing leaves.
#                  ALLOW or SKIP
#     measuring    Allow: crissa_machine.record_allow, then run_check(quick, permission="allow") -> machine.json
#     ready        the recommendation (tier, reasons, the seconds it took): USE THIS (confirmed) or NO THANKS
#     voice        the voice picked from the hardware facts (lib/crissa_voice_tiers) and installed, then the aligner
#     voice_failed RETRY or CONTINUE (she answers in text until a voice is there)
# A saved answer is never asked again (crissa_machine.answered). Switched off (CRISSA_MACHINE_CHECK=0) or absent: no
# card, the voice step alone. What the user confirms is kept in machine.json (answer) and user.json (machine).
TIER_WORDS = {"local": "everything on this computer", "split": "the voice on another computer on your network",
              "remote": "a voice service you choose"}


class CardIO:
    """How an engine reads and writes one walk's step. get() -> the step's dict ({} when the run moved on);
    put(**kw) -> False when the run moved on; voice(retry) -> the voice step's result; done() advances the walk."""

    def __init__(self, get, put, voice, done):
        self.get, self.put, self.voice, self.done = get, put, voice, done


def card_quick() -> bool:
    return bool(step_cfg("machine").get("quick", True)) if steps() else True


def card_enter(io: CardIO) -> None:
    """The step begins: ask, or carry on from the answer this install already gave."""
    mod = _machine_mod()
    if mod is None or not mod.on():
        io.put(state="voice", why="absent" if mod is None else "off")
        _spawn("machine-voice", card_voice, io)
        return
    got = mod.answered(_home())
    perm = got.get("permission")
    if perm == "skip":
        io.put(state="voice", permission="skip", saved=True)
        _spawn("machine-voice", card_voice, io)
    elif perm == "allow" and got.get("measured") and got.get("answer"):
        io.put(state="voice", permission="allow", tier=got.get("tier"), answer=got.get("answer"), saved=True)
        _spawn("machine-voice", card_voice, io)
    elif perm == "allow" and got.get("measured"):
        io.put(state="ready", permission="allow", tier=got.get("tier"), reasons=_reasons(got.get("reasons")),
               seconds=got.get("elapsed_s"), saved=True)
    elif perm == "allow":
        io.put(state="measuring", permission="allow", card=_card_text(mod), saved=True)
        _spawn("machine-check", card_measure, io)
    else:
        io.put(state="ask", card=_card_text(mod))


def _card_text(mod) -> dict[str, Any]:
    try:
        return mod.card(quick=card_quick(), home=_home())
    except Exception as e:  # noqa: BLE001  a card that cannot be worked out still asks, in fewer words
        print("[init] machine card: %s: %s" % (type(e).__name__, e), flush=True)
        return {"title": "Check this computer?", "what": "Crissa times how fast this computer plays her video and "
                "suggests where her voice should run.", "facts": "No sound plays. Nothing leaves this computer.",
                "skip": "Skip, and the voice is chosen from this computer's hardware alone."}


def _reasons(rs: Any) -> list[str]:
    return [str(r)[:160] for r in (rs or [])][:6]


def card_act(io: CardIO, verb: str) -> dict[str, Any]:
    """The person's answer on the card: allow | skip (the card), use | decline (the recommendation), retry | continue
    (a voice that did not install)."""
    m = io.get() or {}
    state, verb = str(m.get("state") or ""), str(verb or "").strip().lower()
    mod = _machine_mod()
    if state == "ask" and verb in ("allow", "skip") and mod is not None:
        if verb == "skip":
            mod.record_skip(_home())
            io.put(state="voice", permission="skip")
            _spawn("machine-voice", card_voice, io)
        else:
            mod.record_allow(_home())
            io.put(state="measuring", permission="allow")
            _spawn("machine-check", card_measure, io)
        return {"ok": True, "verb": verb}
    if state == "ready" and verb in ("use", "decline") and mod is not None:
        rec = mod.answer(verb == "use", _home())
        if verb == "use":
            _user_update({"machine": {"tier": m.get("tier"), "accepted_at": _now(), "applied": bool(rec.get("confirmed"))}})
            # THE MEASURED VOICE BECOMES HERS (the User 2026-10-10): the largest voice that cleared the bars here is
            # kept as her pick; the card's voice step then starts its lane through the launcher - no restart. Round
            # 29 (.101 2026-10-17): kept as the MACHINE's pick, never "your choice" (a later start may make it again).
            # Kill CRISSA_MACHINE_PICK_AS_USER=1 (the person's own, as round 28).
            eng = str(((rec.get("voice") or {}) or m.get("voice") or {}).get("engine") or "")
            if eng:
                try:
                    import crissa_voice_tiers
                    crissa_voice_tiers.override(eng, _home(), by=(
                        "user" if os.environ.get("CRISSA_MACHINE_PICK_AS_USER", "0").strip() == "1" else "machine"))
                    io.put(voice_set=eng)
                except Exception as e:  # noqa: BLE001  a pick that cannot be kept leaves the hardware's pick
                    io.put(voice_set_error="%s: %s" % (type(e).__name__, str(e)[:120]))
        io.put(state="voice", answer=verb)
        _spawn("machine-voice", card_voice, io)
        return {"ok": True, "verb": verb}
    if state == "voice_failed" and verb == "retry":
        io.put(state="voice", line="")
        _spawn("machine-voice", card_voice, io, True)
        return {"ok": True, "verb": verb}
    if state == "voice_failed" and verb == "continue":
        _card_aligner(io)
        io.done()
        return {"ok": True, "verb": verb}
    return {"ok": False, "error": "%s is not an answer to the machine card now (%s)" % (verb or "nothing", state or "-")}


def card_measure(io: CardIO) -> None:
    mod = _machine_mod()
    try:
        res = mod.run_check(quick=card_quick(), home=_home(), permission="allow") or {}
    except Exception as e:  # noqa: BLE001  a check that breaks never stops the walk: the hardware decides
        res = {"error": f"{type(e).__name__}: {e}"[:160]}
    if not res.get("tier"):
        why = str(res.get("skipped") or res.get("error") or "no tier")
        if not io.put(state="voice", error=why):
            return
        card_voice(io)
        return
    io.put(state="ready", tier=str(res.get("tier")), reasons=_reasons(res.get("reasons")), seconds=res.get("elapsed_s"),
           voice=dict(res.get("voice") or {}))
    if getattr(io, "auto", False):
        # SETUP IN PARALLEL: the person is on Voice ID by now; the recommendation is taken (Use this), and the log says it
        print("[init] This computer, measured in the background: %s, taken (Use this)" % res.get("tier"), flush=True)
        card_act(io, "use")


def _card_aligner(io: CardIO) -> None:
    got = aligner_after_voice()
    io.put(aligner=got)
    print("[init] the word aligner after her voice: %s" % (got.get("state") or "-"), flush=True)


def aligner_always_on() -> bool:
    """ROUND 23 (.101, round 22: every line `lip clock: letter`, no ~/.crissa/aligner): the aligner step runs whether or
    not the walk is still on the machine card. Kill: CRISSA_ALIGNER_AFTER_VOICE_ALWAYS=0 (round 22: skipped)."""
    return str(os.environ.get("CRISSA_ALIGNER_AFTER_VOICE_ALWAYS", "1")).strip().lower() not in ("0", "off", "false", "no")


def card_voice(io: CardIO, retry: bool = False) -> None:
    """The voice, after the card's answer: picked from the hardware facts and installed, then the word aligner. A
    failed voice install stops on the plate (RETRY / CONTINUE); anything else moves the walk on."""
    try:
        res = io.voice(retry) or {}
    except Exception as e:  # noqa: BLE001  a broken install never stops the walk
        res = {"ok": False, "state": "failed", "line": "the voice could not be installed (%s)" % type(e).__name__}
    if res.get("state") == "failed" or res.get("ok") is False:
        io.put(state="voice_failed", line=str(res.get("line") or "")[:300])
        if getattr(io, "auto", False):
            # SETUP IN PARALLEL: no card to show RETRY on now; she answers in text until the Connect page's Retry
            print("[init] her voice did not install in the background (%s); the walk goes on, the Connect page retries"
                  % str(res.get("line") or "")[:160], flush=True)
            card_act(io, "continue")
        return
    if not io.put(state="voiced", line=str(res.get("line") or "")[:300]):
        # THE WALK MOVED ON BEFORE HER VOICE WAS IN (round 22 on .101: the one-page Setup runs this card in the
        # background, the voice finished after the walk had left it, and the aligner was never installed): the plate is
        # gone, the aligner is not
        if aligner_always_on():
            _card_aligner(io)
        return
    _card_aligner(io)
    io.done()


def card_view(m: dict[str, Any], vi: dict[str, Any] | None = None) -> dict[str, Any]:
    """What either glass draws for the card's state: title, body, hint, lines and keys [(verb, label)]."""
    c = m.get("card") if isinstance(m.get("card"), dict) else {}
    state, vi = str(m.get("state") or ""), vi or (m.get("voice") if isinstance(m.get("voice"), dict) else {}) or {}
    v: dict[str, Any] = {"state": state, "title": "", "body": "", "hint": "", "lines": [], "keys": []}
    if state == "ask":
        v.update(title=c.get("title") or "Check this computer?", body=c.get("what") or "", hint=c.get("facts") or "",
                 lines=[c.get("skip") or ""], keys=[("allow", "Allow"), ("skip", "Skip")])
    elif state == "measuring":
        v.update(title="Measuring this computer…", body=c.get("what") or "", hint=c.get("facts") or "")
    elif state == "ready":
        tier = str(m.get("tier") or "")
        secs = m.get("seconds")
        voice_ = (m.get("voice") or {}).get("label") or ""
        v.update(title="Suggested: " + TIER_WORDS.get(tier, tier) + ((" - her voice: %s" % voice_) if voice_ else ""),
                 lines=list(m.get("reasons") or [])[:3],
                 hint=(("Measured in %.0f s. " % float(secs)) if isinstance(secs, (int, float)) else "")
                 + ("Use this, or No thanks: the voice is then chosen from this computer's hardware alone."
                    + (" To run it on another computer, give its address on the Connect page; until then this "
                       "computer's own voice speaks." if tier == "split" else "")),
                 keys=[("use", "Use this"), ("decline", "No thanks")])
    elif state in ("voice", "voiced"):
        installing = vi.get("state") == "installing"
        v.update(title="Installing the voice…" if installing else "Setting up the voice…",
                 lines=[str(x)[:110] for x in (vi.get("lines") or [])[-3:]] if installing else [],
                 body=str(m.get("line") or "") if state == "voiced" else "")
    elif state == "voice_failed":
        v.update(title="The voice could not be installed", body=str(m.get("line") or ""),
                 hint="She answers in text until it is. Retry, or continue and retry later from the Connect page.",
                 keys=[("retry", "Retry"), ("continue", "Continue")])
    v["lines"] = [x for x in v["lines"] if x]
    return v


def _standin_io(run_id: str, anywhere: bool = False) -> CardIO:
    """`anywhere` (SETUP IN PARALLEL): the card's engine keeps reading and writing st["machine"] after the walk has
    moved on (the run still going), takes the recommendation by itself (`auto`), and its done() is the walk's own once."""
    def get() -> dict[str, Any]:
        st = load_state()
        ok = (st.get("status") == "running" and st.get("run_id") == run_id
              and (anywhere or st.get("step_id") == "machine"))
        return dict(st.get("machine") or {}) if ok else {}

    def put(**kw) -> bool:
        def fn(s: dict[str, Any]) -> None:
            if anywhere or s.get("step_id") == "machine":
                if "aligner" in kw:
                    s["aligner"] = kw.pop("aligner")
                s.setdefault("machine", {}).update(kw)
        st = _mutate(run_id, fn)
        return st is not None and (anywhere or st.get("step_id") == "machine")

    def voice(retry: bool = False) -> dict[str, Any]:
        res: dict[str, Any] = {"ok": True, "state": "off"}
        if _voice_install_mod() is not None:
            if not retry:
                try:                               # pick quietly first (no face speaks before a voice is there)
                    import crissa_voice_tiers
                    crissa_voice_tiers.ensure(_home(), faces_live=False)
                except Exception:  # noqa: BLE001  the install then reads the default order
                    pass
            res = voice_install(run_id, retry=retry)
            start_lanes(res)
        vt = _voice_tier()                         # after the install: what really speaks now, the faces tested

        def _vt(s: dict[str, Any]) -> None:
            s["voice_tier"] = {"main": vt.get("main"), "why": str(vt.get("why") or "")[:300],
                               "say": [str(x)[:300] for x in (vt.get("say") or [])][:10],
                               "language": (vt.get("language") or {}).get("code") or "en",
                               "faces_ok": bool((vt.get("face_voices") or {}).get("ok", True))}
            for line in vt.get("say") or []:
                _note(s, str(line)[:300])
        _mutate(run_id, _vt)
        return res

    def done() -> None:
        if anywhere:
            _mutate(run_id, lambda s: s.setdefault("machine", {}).update(background_done=_now()))
            print("[init] This computer: done in the background", flush=True)
        _leave_machine(run_id)
    io = CardIO(get, put, voice, done)
    io.auto = anywhere
    return io


# SETUP, TUCKED IN PARALLEL (round 18, the User 2026-10-13, .101 round 17: "she is sitting there while measuring. We
# should be taking photos and words right now"; "in 43s wait we could be all done and one click to start"; "Tuck them
# properly"). This computer's Allow (or Skip) is the one click: the measurement, the density pick (the build waits for
# it, round 17), her voice and the word aligner go on in the background - the card's own engine on a run-wide io
# (_standin_io anywhere) - and the walk moves on at once to Voice ID, whose spoken counts take the face stills while you
# count (round 13, round 17's turned grab); Face photos then starts by itself for the angles still missing, and is done
# when none is. The recommendation is taken (Use this) without a second click, and a voice that does not install is said
# in the log (she answers in text until the Connect page's Retry). The person's own steps stay his: Connect's key, the
# name, the room dial, the hours. A pack's first boot (lila_holds). Kill: CRISSA_SETUP_PARALLEL=0 (round 17's walk: the
# card waits for its answers, Face photos waits for Start).
PARALLEL_LINE = "Allow starts Setup: while this computer is measured, Crissa learns your voice and face."


def setup_parallel(st: dict[str, Any] | None = None) -> bool:
    if os.environ.get("CRISSA_SETUP_PARALLEL", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return lila_holds(st)


# THE NAME AND THE MICROPHONE ON THIS COMPUTER (round 20, the User 2026-10-14, .101 round 19: "Can we just combine the
# what should we call you and microphone from list with oracle screen? She should just roll through her script with
# small gap to indicate next section. Simple as can be but no simpler"). The card that asks to measure this computer
# also asks the name (required) and shows the microphones: the default preselected, a pick applied at once (no "Use
# this microphone", which re-drew the plate and wiped the typed name), its level live, and a plain Mic off. Allow (or
# Skip) takes the name; Voice ID then starts by itself after REG_AUTO_GAP_S, with no Begin. The cues' Spoken / Text only
# key leaves the cards (CRISSA_SETUP_CUES=text still sets it). An Allow that brings no name (the desk Wall's key) leaves
# the name to Voice ID's own card, as before. A pack's walk only. Kill: CRISSA_SETUP_FOLD=0 (round 19's cards).
REG_AUTO_GAP_S = 1.5


def setup_fold_on(st: dict[str, Any] | None = None) -> bool:
    if crissa_home.is_house():
        return False
    if os.environ.get("CRISSA_SETUP_FOLD", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    st = st if st is not None else load_state()
    return not st.get("only")


def fold_view(st: dict[str, Any]) -> dict[str, Any]:
    """The ask card's name and microphone, for either glass."""
    m = st.get("machine") or {}
    taken = m.get("name_taken") or {}
    mv = mic_view()
    pick = str(mv.get("pick") or mv.get("source") or "")
    return {"fold": True, "ask_name": step_cfg("voice_id").get("ask") or "What should Crissa call you?",
            "name": str(taken.get("display") or m.get("name_typed") or _load_json(user_path()).get("name") or ""),
            "name_error": str(m.get("name_error") or ""), "mic": mv, "level": mic_meter(pick),
            "mic_off": bool(st.get("mic_off_by_person"))}


def fold_take_name(run_id: str, text: str) -> dict[str, Any]:
    """The name typed on the card: a first name is fine; anything else is said on the card and the one click waits."""
    display = re.sub(r"\s+", " ", str(text or "")).strip()[:40]
    if not looks_like_a_name(display):
        _mutate(run_id, lambda s: s.setdefault("machine", {}).update(
            name_error="Type the name Crissa should call you. A first name is fine.", name_typed=display))
        return {"ok": False, "error": "Type the name Crissa should call you first.", "name_error": True}
    first = display.split(" ")[0]
    taken = {"display": display, "first": first, "person_id": person_id_for(first), "at": _now()}
    _mutate(run_id, lambda s: s.setdefault("machine", {}).update(name_taken=taken, name_error="", name_typed=display))
    print("[init] This computer: the name is taken here; Voice ID starts by itself", flush=True)
    return {"ok": True, "name": display}


# NO MICROPHONE, NO INSTALL (round 20, the User 2026-10-14: "No mic detected failed install is my gut. STS without mic
# is a fialed installation"). The one click on This computer needs a working microphone: the one named, else the
# sound server's default, else the first input that reads like a microphone (it is named for the ear and Voice ID).
# With none, Setup ends FAILED: no marker is written, so the next start runs Setup again, and the page says what to do.
# Voice ID that gets no usable take after REG_MAX_TRIES ends the same way. An install whose sound server cannot be
# asked (no pactl, a Mac) is not judged here; Voice ID's own take check still is. Kill: CRISSA_SETUP_MIC_REQUIRED=0.
NO_MIC_WHY = ("No working microphone was detected. Crissa talks with you by voice, so Setup cannot finish without one. "
              "Plug a microphone in, then run Setup again.")
REG_MAX_TRIES = 2


def mic_required_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_SETUP_MIC_REQUIRED", "1").strip().lower() not in ("0", "off", "false", "no")


def mic_ready(pactl=None) -> dict[str, Any]:
    """{"ok", "why", "source"}: is there a working microphone for the ear and Voice ID? Names one when the default
    reads only noise and another input reads like a microphone."""
    if not mic_required_on() or not mic_probe_on():
        return {"ok": True, "why": "not checked"}
    if _sampler() is None:
        return {"ok": True, "why": "nothing can be sampled here"}
    pactl = pactl or _pactl
    if not pactl("info").strip():
        return {"ok": True, "why": "no sound server answered"}
    srcs = mic_sources(pactl)
    if not srcs:
        return {"ok": False, "why": NO_MIC_WHY}
    probe = mic_probe(srcs, wait=True)
    if not probe:
        return {"ok": True, "why": "nothing could be sampled"}
    good = ("ok", "silent")
    named, default = mic_named(), pactl("get-default-source").strip()
    if named and probe.get(named) in good:
        return {"ok": True, "source": named}
    if default and probe.get(default) in good:
        if named:
            set_mic_source("auto", pactl)            # the named one reads only noise: back on the default
        return {"ok": True, "source": default}
    best = next((c["name"] for c in srcs if probe.get(c["name"]) == "ok"), "")
    if best:
        got = set_mic_source(best, pactl)
        print("[init] the default input reads only noise; the ear and Voice ID use %s" % best, flush=True)
        return {"ok": bool(got.get("ok")), "source": best, "why": str(got.get("error") or "")}
    return {"ok": False, "why": NO_MIC_WHY}


def fail_install(run_id: str, why: str) -> dict[str, Any]:
    """Setup ends FAILED (above): the coach is stopped, Lila leaves, nothing says Setup is done, and the next start (or
    the page's Run Setup again) runs it again."""
    if not mic_required_on():
        return dict(snapshot(), ok=False, error=why)
    st = load_state()
    if st.get("status") != "running" or (run_id and run_id != st.get("run_id")) or st.get("only"):
        return dict(snapshot(st), ok=False, error=why)        # Voice ID again / Add a visitor is not the install
    _reg_cancel(st)
    host_stop()
    st["status"] = "failed"
    st["failed"] = {"why": str(why)[:400], "at": _now(), "step_id": st.get("step_id")}
    st["ended_t"] = time.time()
    _note(st, "failed: " + str(why)[:200])
    save_state(st)
    _release_hold(st, why="failed")
    print("[init] Setup FAILED: %s" % why, flush=True)
    return dict(snapshot(load_state()), ok=False, error=why, failed=True)


def machine_act(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """The `machine` action: {"verb": allow|skip|use|decline|retry|continue}; the older {"accept": bool}
    reads as allow/skip on the card and use/decline on the recommendation."""
    par = setup_parallel()
    io = _standin_io(run_id, anywhere=par)
    verb = str(body.get("verb") or "").strip().lower()
    if not verb and "accept" in body:
        state = str((io.get() or {}).get("state") or "")
        verb = {"ask": ("allow", "skip"), "ready": ("use", "decline")}.get(state, ("", ""))[0 if body.get("accept") else 1]
    asked = str((io.get() or {}).get("state") or "")
    if verb == "her_voice":
        # round 28 (the User 2026-10-17: "offer this voice as English Accent option ... Simple toggle at setup"): her
        # Grok Imagine voice (the default) or Pocket's English accent; one file the voice reads on every line
        try:
            import crissa_voice_tiers
            got = crissa_voice_tiers.set_her_voice(str(body.get("style") or ""), _home(), by="Setup")
        except Exception as e:  # noqa: BLE001
            got = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
        if got.get("ok"):
            print("[init] her voice: %s" % got["label"], flush=True)
            _mutate(run_id, lambda s: s.setdefault("machine", {}).update(her_voice=got["style"]))
        return dict(got, verb=verb)
    if verb == "name" and ids_run():
        return {"ok": False, "error": "The name is set for this run; %s to start another." % (   # round 42, R41-4
            "Cancel" if exit_says_cancel_on() else "Back to the Wall")}
    if verb in ("name", "heard") and one_page_on():
        # ONE-PAGE SETUP: You and your microphone is its own section - the name is taken as it is typed, and the
        # page says when the microphone heard the person (kept, so a page opened again still shows it done)
        if verb == "heard":
            _mutate(run_id, lambda s: s.setdefault("machine", {}).update(mic_heard=_now()))
            you_moves_on(run_id)
            return {"ok": True, "verb": verb}
        got = fold_take_name(run_id, str(body.get("name") or ""))
        if got.get("ok"):
            you_moves_on(run_id)
        return got
    if asked == "ask" and verb in ("allow", "skip"):
        if setup_fold_on() and "name" in body:         # round 20: the name typed on this card, required
            got = fold_take_name(run_id, str(body.get("name") or ""))
            if not got.get("ok"):
                return got
        ready = mic_ready()                            # round 20: no working microphone is a failed install
        if not ready.get("ok"):
            return fail_install(run_id, str(ready.get("why") or NO_MIC_WHY))
    out = card_act(io, verb)
    if par and out.get("ok") and asked == "ask" and verb in ("allow", "skip"):
        print("[init] This computer: %s; it goes on in the background, the walk moves on" % verb, flush=True)
        _leave_machine(run_id)
    return out


def language_answer(run_id: str, text: str) -> dict[str, Any]:
    """Init asks for the preferred language (the User 2026-10-06): the voice is picked for it automatically."""
    try:
        import crissa_voice_tiers
        res = crissa_voice_tiers.set_language(text, _home())
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
    if not res.get("ok"):
        _mutate(run_id, lambda s: s.setdefault("voice_tier", {}).update(language_error=str(res.get("error"))[:200]))
        return {"ok": False, "error": res.get("error")}
    vt = res.get("rec") or {}

    def _vt(s: dict[str, Any]) -> None:
        s["voice_tier"] = {"main": vt.get("main"), "why": str(vt.get("why") or "")[:300],
                           "say": [str(x)[:300] for x in (vt.get("say") or [])][:10],
                           "language": res.get("code"), "faces_ok": bool((vt.get("face_voices") or {}).get("ok", True))}
        for line in vt.get("say") or []:
            _note(s, str(line)[:300])
    _mutate(run_id, _vt)
    _user_update({"language": {"code": res.get("code"), "at": _now()}})
    try:                                           # a language that moves her voice to one not installed: install it
        import crissa_voice_tiers
        if _voice_install_mod() is not None and crissa_voice_tiers.installed(str(vt.get("main") or "")) is False:
            _spawn("voice_install", voice_install, run_id)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "language": res.get("code"), "main": vt.get("main")}


def _user_update(fields: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        u = _load_json(user_path())
        if u.get("schema") != USER_SCHEMA:
            u = {"schema": USER_SCHEMA}
        u.update(fields)
        u["updated_at"] = _now()
        _write_json(user_path(), u)
        return u


# ── VOICE ID (the User 2026-10-10): the name, then the Register coach's routine on this machine's mic and camera ──────
#     name        "What should Crissa call you?" - a typed first name
#     starting    the coach is asked: /health (register_token true = its token path), then /start mode voice-register
#     live        the routine runs (the coach's /session says running): Lila's clips, the sentence read at the desk,
#                 the turn and the count; its phases and pips on the register glass (GET /v1/register)
#     done        it ended well: the voice prints (and face stills) are in the identity store; the person is the User
#     failed      it was refused or ended badly: RETRY or SKIP
#     no_coach    no coach answers on this machine (Register is not installed): RETRY or SKIP
#     skipped     the person skipped it
_NOT_A_NAME = re.compile(r"^(the|a|an|my|our|your|yes|no|ok|okay|sure|hello|hi|hey|thanks|thank|please|of|course|"
                         r"once|more|again|let'?s|go|me|i|it|that|this|is|um|uh|crissa|crissa|lila)$", re.I)


def looks_like_a_name(display: str) -> bool:
    head = str(display or "").split(",")[0].strip().rstrip(".")
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", head)
    if not 1 <= len(words) <= 2 or re.search(r"[.!?]", head):
        return False
    return not any(_NOT_A_NAME.match(w) for w in words)


def _reg_io(run_id: str, sid: str, key: str) -> CardIO:
    """The engine's view of one step's dict (st[key]) while the walk is on step `sid`."""
    def get() -> dict[str, Any]:
        st = load_state()
        ok = st.get("status") == "running" and st.get("run_id") == run_id and st.get("step_id") == sid
        return dict(st.get(key) or {}) if ok else {}

    def put(**kw) -> bool:
        def fn(s: dict[str, Any]) -> None:
            if s.get("step_id") == sid:
                s.setdefault(key, {}).update(kw)
        st = _mutate(run_id, fn)
        return st is not None and st.get("step_id") == sid

    def done() -> None:
        _advance(run_id, expect=sid)
    io = CardIO(get, put, lambda retry=False: {}, done)
    io.run_id = run_id
    io.sid = sid
    return io


# ── WHICH MICROPHONE (the User 2026-10-10, .101: the ear took the laptop's empty jack while the Shure was the desktop's
# default; Voice ID recorded on the default). Both now use the sound server's DEFAULT input unless one is named; the
# Voice ID step says which one that is and offers the others. A pick names it for both: ear.json {"ear": {"mic"}} (the
# ear's own setting, crissa_ear.settings) and identity/site.json {"enroll": {"mic"}} (what Register records on,
# voice_register.enroll_mic); "auto" puts both back on the default. The ear starts again on it (the launcher's restart
# request for the stt lane), so her next turn is heard on the same microphone her voice print was made on.
def _pactl(*args: str, timeout: float = 3.0) -> str:
    import subprocess
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def mic_sources(pactl=_pactl) -> list[dict[str, Any]]:
    """[{"name", "label", "unplugged"}] - every input that is not a monitor of an output, as `pactl list sources` names
    them; `unplugged` when the sound server says the input's active port is "not available" (a jack with nothing in it,
    round 20)."""
    out, cur, ports = [], {}, {}
    in_ports = False
    for ln in pactl("list", "sources").splitlines():
        t = ln.strip()
        if t.startswith("Name:"):
            cur = {"name": t.split(":", 1)[1].strip(), "label": "", "unplugged": False}
            ports, in_ports = {}, False
            out.append(cur)
        elif t.startswith("Description:") and cur:
            cur["label"] = t.split(":", 1)[1].strip()
        elif t == "Ports:" and cur:
            in_ports = True
        elif t.startswith("Active Port:") and cur:
            in_ports = False
            cur["unplugged"] = ports.get(t.split(":", 1)[1].strip()) is False
        elif in_ports and cur and ":" in t and "(" in t:
            ports[t.split(":", 1)[0].strip()] = "not available" not in t
        elif in_ports and not t:
            in_ports = False
    return [{"name": c["name"], "label": c["label"] or c["name"], "unplugged": bool(c.get("unplugged"))} for c in out
            if c["name"] and not c["name"].endswith(".monitor")
            and not c["name"].startswith("crissa_aec")]       # the ear's own echo canceller is never a pick (round 14)


# NO PHANTOM MICROPHONE, AND A LEVEL YOU CAN READ (round 20, the User 2026-10-14, .101 round 19: the Setup page's list
# offered "Ryzen HD Audio Controller Analog Stereo", an analog jack with nothing behind it that reads full-scale noise,
# and that input - not the Shure he picked - was saved for the ear, the echo canceller and Voice ID; "it did have a signal
# bar there": noise moves a level bar too). Each input is listened to for a moment at rest (MIC_PROBE_S, nothing kept):
# `noise` (clipping, or a loud steady sound with no pauses - the take check's rule, voice_session.pcm_fault) and an
# `unplugged` port are not offered, `silent` (digital zero) is offered and marked; the sound server's default input is
# preselected unless one is named or it is itself noise. The level under the list is the chosen input's, read every
# second while the card is looked at, with its peak and clipping. Kill: CRISSA_MIC_PROBE=0 (every input listed, no
# probe, no meter).
MIC_PROBE_S = 0.4
MIC_PROBE_TTL_S = 15.0
METER_S = 0.25
CLIP_LEVEL = 32000
_PROBE: dict[str, Any] = {"at": 0.0, "by": {}, "busy": False}
_METER: dict[str, Any] = {"until": 0.0, "source": "", "level": {}, "busy": False}


def mic_probe_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_MIC_PROBE", "1").strip().lower() not in ("0", "off", "false", "no")


def _mic_sample(source: str, seconds: float = MIC_PROBE_S) -> bytes | None:
    """`seconds` of 16 kHz mono s16 from one input (parec), nothing written anywhere; None when it cannot be read."""
    import shutil
    import subprocess
    if not shutil.which("parec") or not shutil.which("timeout"):
        return None
    try:
        import audio_sink
        dev = audio_sink.capture_source(source)     # .101 round 30: her films and voice are not the person's level
        r = subprocess.run(["timeout", "--signal=TERM", "%.2f" % (seconds + 0.35), "parec", "--device=" + dev,
                            "--raw", "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=40"],
                           capture_output=True, timeout=seconds + 3.0)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = r.stdout or b""
    return raw[: int(16000 * 2 * seconds)] if len(raw) >= 3200 else None


_REAL_SAMPLE = _mic_sample


def _sampler():
    """The sampler a probe uses: a test never reaches a real microphone (this box's are the house's) unless it stands
    its own in."""
    if os.environ.get("PYTEST_CURRENT_TEST") and _mic_sample is _REAL_SAMPLE:
        return None
    return _mic_sample


def mic_level(pcm: bytes) -> dict[str, Any]:
    """{"peak", "db", "clip", "verdict"} of a moment of s16 audio: verdict `noise` (clipping, or a loud steady sound
    with no pauses), `silent` (digital zero) or `ok`."""
    import array
    import math
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not a:
        return {"peak": 0, "db": None, "clip": False, "verdict": "silent"}
    peak = max(abs(x) for x in a)
    rms = (sum(x * x for x in a) / len(a)) ** 0.5
    db = round(20.0 * math.log10(max(rms, 1.0) / 32768.0), 1)
    clips = sum(1 for x in a if x >= CLIP_LEVEL or x <= -CLIP_LEVEL)
    clip = clips > 0.005 * len(a)
    n = 320                                         # 20 ms frames at 16 kHz
    fr = sorted((sum(x * x for x in a[i:i + n]) / n) ** 0.5 for i in range(0, len(a) - n + 1, n))
    steady = bool(fr) and fr[(9 * len(fr)) // 10] >= 300.0 and fr[len(fr) // 10] >= 0.5 * fr[(9 * len(fr)) // 10]
    verdict = "silent" if peak < 4 else ("noise" if (clip or steady) else "ok")
    return {"peak": int(peak), "db": db, "clip": bool(clip), "verdict": verdict}


def mic_probe(sources: list[dict[str, Any]] | None = None, sample=None, wait: bool = False) -> dict[str, str]:
    """{source name: ok | silent | noise | unplugged} for this computer's inputs, from a moment of each at rest; cached
    MIC_PROBE_TTL_S, refreshed on a thread of its own unless `wait`. {} when off or nothing can be sampled."""
    if not mic_probe_on():
        return {}
    sample = sample if sample is not None else _sampler()
    if sample is None:
        return {}
    srcs = sources if sources is not None else mic_sources(_pactl)

    def run() -> dict[str, str]:
        by: dict[str, str] = {}
        for c in srcs:
            if c.get("unplugged"):
                by[c["name"]] = "unplugged"
                continue
            pcm = sample(c["name"], MIC_PROBE_S)
            if pcm is not None:
                by[c["name"]] = mic_level(pcm)["verdict"]
        _PROBE.update(at=time.time(), by=dict(by), busy=False)
        return by
    fresh = time.time() - float(_PROBE["at"]) < MIC_PROBE_TTL_S
    if wait:
        return run()
    if not fresh and not _PROBE["busy"]:
        _PROBE["busy"] = True
        threading.Thread(target=run, name="init-mic-probe", daemon=True).start()
    return dict(_PROBE["by"])


def mic_meter(source: str) -> dict[str, Any]:
    """The chosen input's level now ({"db", "peak", "clip", "verdict", "source"}), read every second on a thread of its
    own for as long as the card keeps asking (3 s after its last read); {} when off or nothing can be sampled."""
    if not mic_probe_on() or not source or _sampler() is None:
        return {}
    if SYNC:                                       # tests: one reading, no thread
        pcm = _sampler()(source, METER_S)
        return dict(mic_level(pcm), source=source, at=time.time()) if pcm else {}
    _METER.update(until=time.time() + 3.0, source=source)
    if not _METER["busy"]:
        _METER["busy"] = True

        def loop() -> None:
            try:
                while time.time() < float(_METER["until"]):
                    src = str(_METER["source"])
                    smp = _sampler()
                    pcm = smp(src, METER_S) if smp is not None else None
                    _METER["level"] = dict(mic_level(pcm), source=src, at=time.time()) if pcm else {}
                    time.sleep(1.0)
            finally:
                _METER["busy"] = False
        threading.Thread(target=loop, name="init-mic-meter", daemon=True).start()
    lv = dict(_METER["level"] or {})
    return lv if lv.get("source") == source else {}


def _ear_json() -> Path:
    return _home() / "ear.json"


def mic_named() -> str:
    """The microphone named for the ear (ear.json, or the ear's RAIL_STT_MIC), '' = the default input."""
    v = str(((_load_json(_ear_json()).get("ear") or {}).get("mic")) or os.environ.get("RAIL_STT_MIC") or "").strip()
    return "" if v.lower() in ("", "auto") else v


_MIC_CACHE: dict[str, Any] = {"at": 0.0, "view": None}
MIC_CACHE_S = 5.0                          # the plate is read every second; the sound server is asked every 5 s


def ear_mic_source(timeout: float = 1.0) -> str:
    """The source the ear says it captures from (its /health mic_source), '' when it does not answer."""
    try:
        with crissa_egress.urlopen(crissa_ports.url("stt") + "/health", timeout, "Init: the ear's microphone") as r:
            got = str((json.loads(r.read().decode() or "{}") or {}).get("mic_source") or "").strip()
        return "" if (got == "page" or got.startswith("none")) else got
    except Exception:  # noqa: BLE001
        return ""


def mic_view(pactl=None, ear=None) -> dict[str, Any]:
    """{"source", "label", "named", "choices", "line"} - the microphone she listens on and Voice ID records on: the one
    named in Init, else the one the ear says it captures from, else the sound server's default input - the same rule
    the Register coach records by (voice_register.ear_source; the User 2026-10-10: one microphone everywhere)."""
    if pactl is None:
        if _MIC_CACHE["view"] is not None and time.time() - _MIC_CACHE["at"] < MIC_CACHE_S:
            return dict(_MIC_CACHE["view"])
        got = mic_view(_pactl, ear_mic_source)
        _MIC_CACHE.update(at=time.time(), view=dict(got))
        return got
    srcs = mic_sources(pactl)
    named = mic_named()
    default = pactl("get-default-source").strip()
    src = named or ((ear or (lambda: ""))() or "").strip() or default
    label = next((c["label"] for c in srcs if c["name"] == src), "") or (src if named else "")
    if src == "page":
        label = "the Wall page's microphone"
    if label:
        line = "Microphone: %s%s." % (label, "" if named else " (the computer's default)")
    elif not srcs:
        line = "Microphone: the computer's default input."      # no sound server to ask (it names nothing)
    else:
        line = "Microphone: none found - plug one in, or pick one in the desktop's Sound settings."
    out = {"source": src, "label": label, "named": bool(named), "choices": srcs, "line": line, "default": default}
    # NO PHANTOM MICROPHONE (round 20): each choice says what a moment at rest read; noise and unplugged ports go
    # a stood-in sampler (a test) answers at once; the real one refreshes on a thread of its own
    probe = mic_probe(srcs, wait=_mic_sample is not _REAL_SAMPLE) if mic_probe_on() and srcs else {}
    if probe:
        for c in srcs:
            c["state"] = probe.get(c["name"], "")
        bad = ("noise", "unplugged")
        shown = [c for c in srcs if c.get("state") not in bad]
        working = [c["name"] for c in shown if c.get("state") in ("ok", "")]
        pick = (named if named and probe.get(named) not in bad else
                (default if default and probe.get(default) not in bad and any(c["name"] == default for c in shown)
                 else (working[0] if working else "")))
        out.update(choices=shown, hidden=[c["name"] for c in srcs if c.get("state") in bad], pick=pick,
                   working=working, probed=True)
        if not shown:
            out["line"] = NO_MIC_LINE
    else:
        out["pick"] = named or (default if any(c["name"] == default for c in srcs) else "")
    for c in out["choices"]:
        c["default"] = c["name"] == default
    return out


NO_MIC_LINE = "No working microphone was detected. Plug one in; the list updates by itself."


def set_mic_source(name: str, pactl=_pactl, restart=None) -> dict[str, Any]:
    """Name the microphone for the ear and for Voice ID ("auto" = the default input again), and start the ear on it."""
    name = str(name or "").strip()
    if name.lower() != "auto" and name not in {c["name"] for c in mic_sources(pactl)}:
        return {"ok": False, "error": "That microphone is not on this computer now.", "mic_view": mic_view(pactl)}
    if name.lower() != "auto" and mic_probe_on():
        heard = mic_probe().get(name, "")              # round 20: an input that reads only noise is never saved
        if heard in ("noise", "unplugged"):
            return {"ok": False, "error": "That input gives only noise; nothing is plugged in there. Pick another "
                                          "microphone.", "mic_view": mic_view(pactl)}
    _MIC_CACHE["view"] = None
    ej = _load_json(_ear_json())
    ear = ej.setdefault("ear", {}) if isinstance(ej.get("ear", {}), dict) else {}
    if name.lower() == "auto":
        ear.pop("mic", None)
    else:
        ear["mic"] = name
    _write_json(_ear_json(), ej)
    site = ensure_site()
    d = _load_json(site)
    en = d.setdefault("enroll", {}) if isinstance(d.get("enroll", {}), dict) else {}
    if name.lower() == "auto":
        en.pop("mic", None)
    else:
        en["mic"] = name
    _write_json(site, d)
    if restart is None:
        def restart():
            import crissa_procs
            return crissa_procs.request(_home(), "stt", "restart")
    try:
        job = restart()
    except Exception as e:  # noqa: BLE001  the choice is written; the ear takes it at its next start
        job = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return {"ok": True, "mic_view": mic_view(pactl), "restart": job}


def reg_enter(io: CardIO) -> None:
    prev = _load_json(user_path())
    st = load_state()
    taken = (st.get("machine") or {}).get("name_taken") or {}
    if one_page_on(st) and taken.get("person_id") and voice_prints(str(taken["person_id"])) > 0:
        pid = str(taken["person_id"])
        print("[init] Voice ID: %s already has voice prints here; the one page moves on" % pid, flush=True)
        io.put(state="done", display=str(taken.get("display") or ""), person_id=pid, already=True, done=True,
               voice=voice_prints(pid))
        io.done()
        return
    if setup_fold_on(st) and taken.get("display") and taken.get("person_id"):
        # round 20: the name was taken on This computer - Voice ID starts by itself after a short gap, with no Begin
        io.put(state="starting", display=str(taken["display"]), first=str(taken.get("first") or taken["display"]),
               person_id=str(taken["person_id"]), name_error="", error="", auto=True)
        _spawn("voice-id", _reg_auto, io)
        return
    io.put(state="name", display=str(prev.get("name") or ""), name_error="", error="")


def _reg_auto(io: CardIO) -> None:
    if not SYNC:
        _sleep(REG_AUTO_GAP_S)
    if (io.get() or {}).get("state") == "starting":
        _reg_run(io)


# NO SKIP ON VOICE ID (round 20, the User 2026-10-14: "that is why Skip button makes no sense to me"; "STS without mic is
# a fialed installation"): a Voice ID that can be skipped leaves her deaf to him with no sign of it. Its cards offer no
# Skip; a run that gets no usable take REG_MAX_TRIES times ends Setup as a failed install (NO MICROPHONE, NO INSTALL).
# The page's Cancel still ends Setup. Kill: CRISSA_VOICE_ID_SKIP=1 (Skip back, and Retry as often as he likes).
def voice_id_skip_on() -> bool:
    if crissa_home.is_house():
        return True
    return os.environ.get("CRISSA_VOICE_ID_SKIP", "0").strip().lower() in ("1", "on", "true", "yes")


def reg_name(io: CardIO, text: str) -> dict[str, Any]:
    m = io.get() or {}
    if m.get("state") not in ("name", "failed", "no_coach"):
        return {"ok": False, "error": "Voice ID is %s now" % (m.get("state") or "not here")}
    display = re.sub(r"\s+", " ", str(text or "")).strip()[:40]
    if not looks_like_a_name(display):
        io.put(name_error="That doesn't look like a name. A first name is fine.", display=display)
        return {"ok": False, "error": "not a name"}
    first = display.split(" ")[0]
    pid = person_id_for(first)
    st_ = load_state()
    if visitor_run(st_):
        pid = str(visitor_run(st_)["person_id"])     # ADD A VISITOR: their own id, never the User's
    elif st_.get("only"):
        # VOICE ID AGAIN: the prints are the User's, whatever the name typed (the User 2026-10-11): a changed name
        # renames the User (make_user at the end), it never starts a second person who would then become the User
        pid = str(_load_json(user_path()).get("person_id") or "") or pid
    io.put(display=display, first=first, person_id=pid, name_error="", error="", state="starting")
    _spawn("voice-id", _reg_run, io)
    return {"ok": True, "name": display}


def _coach_ready() -> tuple[bool, str]:
    h = _coach_get("/health", 3.0)
    if not h.get("_ok"):
        return False, "no_coach"
    if h.get("register_token") is not True:
        return False, ("Register is not on its token path on this computer (the coach says register_token: %s)"
                       % json.dumps(h.get("register_token")))
    return True, ""


def _reg_run(io: CardIO) -> None:
    """The Voice ID step's start and watch; a spoken Voice ID again that stopped short anywhere (no coach, a refused
    start, a timeout) is closed with one spoken line, never left running (A SPOKEN VOICE ID AGAIN ENDS AS IT ENDS)."""
    try:
        _reg_start(io)
    finally:
        st = load_state()
        reg = st.get("register") or {}
        if (st.get("status") == "running" and spoken_again(st) and st.get("step_id") == "voice_id"
                and reg.get("state") in ("failed", "no_coach")):
            print("[init] Voice ID again (spoken) stopped: %s" % str(reg.get("error") or reg.get("state"))[:200],
                  flush=True)
            say(SPOKEN_FAILED_LINE, voice=cue_speaker())
            _finish(str(st.get("run_id") or ""))


def _reg_start(io: CardIO) -> None:
    m = io.get() or {}
    ok, why = _coach_ready()
    if not ok:
        if why == "no_coach":
            io.put(state="no_coach", error="Voice ID needs the Register coach, and it is not running on this computer.")
        else:
            io.put(state="failed", error=why)
        return
    pid, first = str(m.get("person_id") or ""), str(m.get("first") or m.get("display") or "")
    if (load_state().get("again") or {}).get("replace") and "before" not in m:
        io.put(before=_prints_now(pid))          # REPLACE: the prints she has now, set aside once new ones are made
    ensure_site()
    set_register_glass({"phase": "idle"})
    body = {"mode": "voice-register", "display": first, "person_id": pid, "station": "desk",
            "create": True, "first_boot": not load_state().get("only")}
    if one_page_lila_cues_on():
        body["cue_voice"] = "lila"                 # round 36: on the one page Lila hosts, so her correct-it lines are hers
    elif camera_voice_on():
        body["cue_voice"] = "announcer"            # round 23: THE CAMERA'S VOICE says every direction
    elif two_voices_on():
        body["cue_voice"] = "lila"                 # round 20: no announcer here, so Lila says Voice ID again's cues too
    if visitor_run().get("person_id") == pid:
        body["visitor"] = True                   # ADD A VISITOR: the coach keeps this id exactly and asks this player
    if m.get("redo") in ("read", "count"):
        body.update(only=m["redo"], first_boot=False)      # one take again (GG): that take alone, no opening line
    got = _coach_post("/start", body, 20.0)
    if not got.get("_ok") or got.get("ok") is False:
        io.put(state="failed", error=str(got.get("error") or got.get("_error") or "the coach refused")[:200])
        return
    pid = str(got.get("person_id") or pid)
    if not io.put(state="live", person_id=pid, sent_at=time.time(), sent=True, pending=False):
        return
    _coach_watch(io, "voice_id", float(step_cfg("voice_id").get("timeout_s") or 240))


# A SPOKEN VOICE ID AGAIN ENDS AS IT ENDS (the User 2026-10-12, .101): it has no plate of its walk to wait on (the coach's
# small one is its only plate), so whatever happened - prints made, a take dropped, cancelled, refused, nothing heard -
# the run is closed at once: the prints made stand (a dropped take is not offered again; the menu is the way to run it
# again), a failure is said by the announcer in one line, and nothing is left running for a later start to show.
SPOKEN_FAILED_LINE = "Voice ID did not save a voice print this time. You can try again from the Crissa menu."


# TWO VOICES WITHOUT KREW (round 20, the User 2026-10-14: "feels like a Lila and Crissa show only ... Maybe just ship two
# voices (with sound leveler)", the announcer being awkward in that mix without krew; lib/crissa_capability.two_voices): with
# no krew here there is no announcer - Lila says every cue (Setup's and Voice ID again's), the team's end is Lila then
# Crissa, and the voice install fetches no announcer voice. Round 19's one loudness covers both voices. Kill:
# CRISSA_TWO_VOICES=0 (the announcer as before).
# LILA SAYS THE CORRECTIONS ON THE ONE PAGE (round 36, the User on .101 round 35: "corrected me in announcer voice vs
# Lila's"): the camera's voice assumed the one page says nothing live (her films say the directions), but rounds 34-35
# added live correct-it lines - "Just a quarter turn. I need to see your face.", "Look straight at the camera once more.",
# "I'll take the rest later." - and they went out as the announcer (pocket anna). On the one page (first Setup, Add/Edit
# IDs) Lila is the host on screen, so the coach is told Lila (pocket eve, Setup's own assignment). Voice ID again off the
# one page keeps the camera's voice. A pack only. Kill CRISSA_ONE_PAGE_LILA_CUES=0.
def one_page_lila_cues_on(st: dict[str, Any] | None = None) -> bool:
    if crissa_home.is_house():
        return False
    if os.environ.get("CRISSA_ONE_PAGE_LILA_CUES", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return one_page_on(st if st is not None else load_state())


def camera_voice_on() -> bool:
    """THE CAMERA'S VOICE (round 23, the User 2026-10-15: "yes ship the camera voice always"): the directions said live
    (look at the camera, turn, count at the beep, once more, louder - Voice ID again, add a person, the card walk; the
    one-page Setup has none, her films say them) are an off-camera voice, the announcer's English voice, as if the
    camera speaks. Lila does not say them: she listens on her plate and no lips are painted on her for them. The voice
    always ships, krew or not (lib/crissa_voice_install announcer_enabled). Lila and Crissa keep their own lines.
    A pack only. Kill: CRISSA_CAMERA_VOICE=0 (round 20-22: Lila says the cues when there is no krew)."""
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_CAMERA_VOICE", "1").strip().lower() not in ("0", "off", "false", "no")


def two_voices_on() -> bool:
    try:
        import crissa_capability
        return bool(crissa_capability.two_voices(home=_home()))
    except Exception:  # noqa: BLE001  no capability module: the announcer as before
        return False


# THE FIRST CUE AS ITS CARD OPENS (round 20, .101 round 19: "a long gap between the Voice ID screen and Lila's first
# instruction ... Makes it seem we are just waiting for the beep. Made me look to left"). The coach renders the spoken
# Voice ID's lines ahead in the cue voice of its own process - the announcer - so Lila's, which the routine's child
# says, were rendered only when the card opened: three lines on a CPU voice before her first word. Init now asks the
# coach for them in her voice as Setup starts (and as a Voice ID again starts), once; the coach's disk cache keeps them.
# A pack only, Lila's cues only. Kill: CRISSA_VOICE_ID_WARM=0 (the coach's own warm alone).
def warm_cues() -> None:
    if crissa_home.is_house() or os.environ.get("CRISSA_VOICE_ID_WARM", "1").strip().lower() in (
            "0", "off", "false", "no") or not lines_on() or os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def go() -> None:
        got = _coach_post("/warm", {"voice": "lila"}, 4.0)
        print("[init] Voice ID's lines: %s" % ("rendering in Lila's voice" if got.get("_ok") else
                                               "the coach did not answer (%s)" % str(got.get("_error"))[:80]),
              flush=True)
    threading.Thread(target=go, name="init-warm-cues", daemon=True).start()


def lila_hosts_again() -> bool:
    """Voice ID again with Lila on her plate (two voices, her lips on, lines spoken). Kill: CRISSA_LILA_AGAIN=0."""
    return (two_voices_on() and lila_lips_on() and lines_on()
            and os.environ.get("CRISSA_LILA_AGAIN", "1").strip().lower() not in ("0", "off", "false", "no"))


def cue_speaker() -> str:
    """Who says a cue with no face of its own: the camera's voice (round 23), else Lila when there are two voices, else
    the announcer."""
    return "lila" if (two_voices_on() and not camera_voice_on()) else "announcer"


def spoken_again(st: dict[str, Any] | None = None) -> bool:
    st = st if st is not None else load_state()
    return bool(st.get("only")) and voice_id_routine() == "verbal"


def exit_says_cancel_on(env: dict | None = None) -> bool:
    """Round 41 (.101 round 40 D8): the one exit of an IDs run reads Cancel (init_v2_render's tile asks the same switch)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_EXIT_SAYS_CANCEL", "1")).strip().lower() not in ("0", "off", "false", "no")


# AN IDS RUN IS NOT A SPOKEN VOICE ID AGAIN ON THE DESK WALL (round 41, .101 round 40 N15/F7, 2nd round: no corner tile on
# a minimized or closed IDs run). An IDs run walks with only=["voice_id"], so under the verbal routine (a pack's default)
# the snapshot said spoken_again, and the desk Wall draws NOTHING for a spoken again (house_wall_ascii: `im = None if
# snap.get("spoken_again")`) - render_init was never called, so round 40's repaint signature could not help, and no
# `init: ... on the film` line was ever logged for an IDs run. The snapshot's key is False while an IDs run is on, so the
# Wall draws the run's plate (the corner tile when her window is not in front). spoken_again() itself (the routine's own
# ending) is unchanged. Kill: CRISSA_IDS_WALL_TILE=0 (round 40: an IDs run reads as a spoken again on the Wall).
def ids_wall_tile_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_WALL_TILE", "1")).strip().lower() not in ("0", "off", "false", "no")


def wall_spoken_again(st: dict[str, Any] | None = None) -> bool:
    st = st if st is not None else load_state()
    if st.get("ids") and ids_wall_tile_on():
        return False
    return spoken_again(st)


def _end_spoken_again(io: CardIO, s: dict[str, Any], pid: str, rc: Any, glass_error: str) -> None:
    n = _counts(pid)["voice"] if pid else 0
    failed = bool(s.get("cancelled") or rc not in (0, None) or glass_error or n < 1)
    if failed:
        why = str(glass_error or s.get("last_error") or ("cancelled" if s.get("cancelled") else "") or
                  "no recording was loud and long enough to use")[:200]
        io.put(state="failed", error=why)
        print("[init] Voice ID again (spoken) ended without a new print: %s" % why, flush=True)
        if not s.get("cancelled"):
            say(SPOKEN_FAILED_LINE, voice=cue_speaker())
    else:
        m = io.get() or {}
        make_user(pid, str(m.get("display") or pid))
        faces = sync_faces(pid)
        moved = _replace_old(io, pid)
        if io.put(state="done", voice=n - moved, face=max(_counts(pid)["face"], faces), done=True, dropped=[],
                  redo=""):
            io.done()                                # the walk's own end: _finish closes the run (no landing)
        return
    st = load_state()
    if st.get("status") == "running" and st.get("only"):
        _finish(str(st.get("run_id") or ""))         # closed now: nothing is left running for a later start


def _coach_watch(io: CardIO, what: str, timeout_s: float) -> None:
    """Follow one coach session to its end: its /session (running, last_rc, last_error, progress) and, for Register,
    the register glass the routine writes here. Then the step's own ending."""
    t0 = time.time()
    seen_running = False
    while True:
        m = io.get() or {}
        if m.get("state") != "live":
            return                                 # the walk moved on, was cancelled, or a retry took over
        s = _coach_get("/session", 3.0)
        running = bool(s.get("running")) if s.get("_ok") else seen_running
        seen_running = seen_running or running
        if what == "face" and s.get("_ok") and isinstance(s.get("progress"), dict):
            io.put(progress={k: s["progress"].get(k) for k in ("phase", "line", "done", "total", "why")})
        if s.get("_ok") and not running and (seen_running or time.time() - t0 > 4.0):
            break
        if time.time() - t0 > timeout_s:
            _coach_post("/cancel", {}, 4.0)
            io.put(state="failed", error="it did not finish in %d s" % int(timeout_s))
            return
        _sleep(POLL_S)
    m = io.get() or {}
    pid = str(m.get("person_id") or "")
    rc = s.get("last_rc")
    glass_error = _GLASS.get("text") if (what == "voice_id" and _GLASS.get("phase") == "error") else ""
    if what == "voice_id" and spoken_again():
        _end_spoken_again(io, s, pid, rc, glass_error)
        return
    redo = str(m.get("redo") or "") if what == "voice_id" else ""
    if redo and not s.get("cancelled") and (rc not in (0, None) or glass_error):
        n = _counts(pid)["voice"]
        if n >= 1:                                 # the one take again was dropped too: the earlier prints stand (GG)
            why = "" if redo in dropped_takes(s) else str(glass_error or s.get("last_error") or "")[:200]
            io.put(state="partial", voice=n, dropped=[redo], again=True, error=why)
            return
    if s.get("cancelled") or rc not in (0, None) or glass_error:
        io.put(state="failed", error=str(glass_error or s.get("last_error") or "it ended with code %s" % rc)[:200])
        return
    counts = _counts(pid)
    # NEVER DONE WITH NOTHING SAVED (the User 2026-10-10, .101: the face step was listed with no photo kept). A session
    # that ended well but left no print is a failure the plate says, with Retry and Skip.
    why = str(s.get("last_error") or "").strip()
    if what == "voice_id":
        if counts["voice"] < 1:
            tries = int(m.get("tries") or 0) + 1
            noise = take_faults(s)
            if noise:                                  # round 20: the takes were noise, not a quiet voice
                err = ("No voice print was made: %s, not a voice. Check that the microphone you speak into is the one "
                       "picked, then Retry." % noise[0])
            else:
                err = ("No voice print was made: no recording was loud and long enough to use%s. Retry and read at "
                       "your normal volume, near the microphone%s." % ((" (%s)" % why[:120]) if why else "",
                                                                       ", or skip" if voice_id_skip_on() else ""))
            if (not voice_id_skip_on() and tries >= REG_MAX_TRIES and not s.get("cancelled")
                    and not load_state().get("only")):      # a first Setup only: Voice ID again or a visitor retries
                fail_install(str(getattr(io, "run_id", "") or ""), NO_MIC_WHY + " (Voice ID: %s)" % (
                    noise[0] if noise else "nothing loud and long enough was heard"))
                return
            io.put(state="failed", error=err[:300], tries=tries)
            return
        dropped = dropped_takes(s)
        if dropped:
            # A DROPPED TAKE IS SAID, AND OFFERED AGAIN (the User 2026-10-10, .101 round 5 GG: the count was dropped as
            # too quiet and Init said done with 1 print without a word). The prints made stand; the plate names the
            # take that was not kept and offers it alone (Redo the count), or Continue with what was saved. The person
            # is made the User when the step ends (Continue, or a redo that saves): until then the coach's first-boot
            # rule still lets the redo record - with a User on file it needs User mode, which a first boot cannot have
            # yet (the sandbox trial of the round-6 kit: the redo was refused "Register needs User auth first").
            io.put(state="partial", voice=counts["voice"], face=counts["face"], dropped=dropped, again=bool(redo),
                   error="")
            return
        make_user(pid, str(m.get("display") or pid))
        faces = sync_faces(pid)
        moved = _replace_old(io, pid)
        if not io.put(state="done", voice=counts["voice"] - moved, face=max(counts["face"], faces), done=True,
                      dropped=[], redo=""):
            return
    else:
        faces = sync_faces(pid)
        n = max(counts["face"], faces)
        if n < 1:
            why = why or str(((io.get() or {}).get("progress") or {}).get("why") or "").strip()
            io.put(state="failed", error=("No face photo was saved%s. Retry facing the camera in good light, or skip."
                                          % ((": %s" % why[:160]) if why else ": the camera gave no picture of a face "
                                             "Crissa could use"))[:300])
            return
        if not str(_load_json(user_path()).get("person_id") or ""):
            make_user(pid, str(m.get("display") or pid))     # Voice ID was skipped: her face makes the person the User
        if not io.put(state="done", faces=n):
            return
    io.done()


def reg_act(io: CardIO, verb: str) -> dict[str, Any]:
    m = io.get() or {}
    state, verb = str(m.get("state") or ""), str(verb or "").strip().lower()
    if verb == "redo" and state == "partial" and m.get("person_id"):
        take = next((t for t in (m.get("dropped") or []) if t in ("read", "count")), "")
        if not take:
            return {"ok": False, "error": "no take to do again"}
        io.put(state="starting", error="", redo=take)
        _spawn("voice-id", _reg_run, io)
        return {"ok": True, "verb": verb, "take": take}
    if verb == "continue" and state == "partial":
        pid = str(m.get("person_id") or "")
        if pid:                                      # the step ends with what was saved: the person is the User now
            make_user(pid, str(m.get("display") or pid))
            faces = sync_faces(pid)
            moved = _replace_old(io, pid)
            io.put(face=max(int(m.get("face") or 0), faces), voice=max(0, int(m.get("voice") or 0) - moved))
        io.put(state="done", done=True, redo="")
        io.done()
        return {"ok": True, "verb": verb}
    if verb == "cues" and state in ("name", "failed", "no_coach", "partial"):
        got = set_cues("spoken" if cues_mode() == "text" else "text")
        io.put(cues=got.get("cues"))                 # the plate repaints with the other key
        return {"ok": bool(got.get("ok")), "verb": verb, "cues": got.get("cues")}
    if verb == "retry" and state in ("failed", "no_coach") and m.get("person_id"):
        io.put(state="starting", error="", redo="", dropped=[])
        _spawn("voice-id", _reg_run, io)
        return {"ok": True, "verb": verb}
    if verb == "skip" and state in ("name", "failed", "no_coach") and voice_id_skip_on():
        io.put(state="skipped")
        io.done()
        return {"ok": True, "verb": verb}
    return {"ok": False, "error": "%s is not an answer to Voice ID now (%s)" % (verb or "nothing", state or "-")}


def _prints_now(pid: str) -> list[str]:
    """The voice print files the User has now (voice/*.json; quarantined quiet takes and set-aside prints are in
    subfolders, which no reader takes)."""
    d = identity_root() / "people" / pid / "voice"
    return sorted(f.name for f in d.glob("*.json")) if pid and d.is_dir() else []


def _replace_old(io: CardIO, pid: str) -> int:
    """VOICE ID AGAIN WITH "REPLACE" TICKED (the User 2026-10-11): once the new prints are made, the ones she had before
    are moved to voice/replaced-<time>/ - kept on this computer, never deleted, and no longer matched (every reader takes
    voice/*.json only). Without the tick nothing moves: new prints are added beside the old. Returns how many moved."""
    m = io.get() or {}
    if not (load_state().get("again") or {}).get("replace") or not m.get("before") or m.get("set_aside") is not None:
        return 0
    d = identity_root() / "people" / pid / "voice"
    shelf = d / ("replaced-" + time.strftime("%Y%m%d-%H%M%S"))
    n = 0
    for name in m.get("before") or []:
        f = d / str(name)
        if f.is_file() and f.parent == d:
            try:
                shelf.mkdir(parents=True, exist_ok=True)
                f.rename(shelf / f.name)
                n += 1
            except OSError:
                pass
    io.put(set_aside=n)
    print("[init] Voice ID again: %d earlier voice print%s set aside in %s" % (n, "" if n == 1 else "s", shelf.name),
          flush=True)
    return n


def dropped_takes(session: dict[str, Any]) -> list[str]:
    """The takes ("read", "count") the coach's quiet gate dropped in this session (its /session progress), in order."""
    out = []
    for d in ((session or {}).get("progress") or {}).get("dropped") or []:
        t = str((d or {}).get("take") or "")
        if t in ("read", "count") and t not in out:
            out.append(t)
    return out


TAKE_FAULT_WORDS = {"clipped": "the microphone gave only full-scale noise or clipping",
                    "constant": "the microphone gave a steady sound with no pauses"}


def take_faults(session: dict[str, Any]) -> list[str]:
    """The plain words for takes the coach dropped as noise, not as quiet (round 20: its take_dropped `why`)."""
    out = []
    for d in ((session or {}).get("progress") or {}).get("dropped") or []:
        w = TAKE_FAULT_WORDS.get(str((d or {}).get("why") or ""))
        if w and w not in out:
            out.append(w)
    return out


def partial_take_name(m: dict[str, Any]) -> str:
    return "count" if "count" in (m.get("dropped") or []) else "sentence"


def partial_words(m: dict[str, Any]) -> dict[str, str]:
    """The plate when Voice ID saved prints but dropped a take (GG): which take was not kept, in plain words."""
    n = int(m.get("voice") or 0)
    prints = "%d voice print%s" % (n, "" if n == 1 else "s")
    take = partial_take_name(m)
    if take == "count":
        what, how = "The count", "turn away and count aloud at the beep, a little slower, with small pauses"
    else:
        what, how = "The sentence", "read it aloud at the beep, in your normal voice, near the microphone"
    if m.get("again") and m.get("error"):      # the redo did not run to a take (not a quiet one): its own reason
        body = "%s could not be recorded again: %s Voice ID has %s." % (what, str(m["error"]).rstrip(".") + ".", prints)
    elif m.get("again"):
        body = "%s was too short or too quiet again, so it was not saved. Voice ID has %s." % (what, prints)
    else:
        body = ("Voice ID saved %s. %s was too short or too quiet to use, so it was not saved."
                % (prints, what))
    return {"body": body, "hint": "Redo the %s to add it: %s. Or Continue with what was saved." % (take, how)}


def four_takes() -> bool:
    """The coach's routine in a pack takes four prints (voice_register.four_takes; CRISSA_REGISTER_TAKES=2 = two)."""
    return str(os.environ.get("CRISSA_REGISTER_TAKES") or "4").strip() != "2"


def reg_view(m: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or step_cfg("voice_id")
    state = str(m.get("state") or "")
    v: dict[str, Any] = {"state": state, "title": cfg.get("title") or "Voice ID", "body": "", "hint": "", "keys": []}
    if state == "name":
        mv = mic_view()
        line = mv["line"] + ("" if len(mv["choices"]) < 2 else " Another one can be picked on the Setup page.")
        cue_line = ("Cues: text only - no sound; the screen says when to read." if cues_mode() == "text" else
                    "Cues: spoken. For a quiet house (at night), press Cues for text only.")
        body = (cfg.get("body_verbal") if voice_id_routine() == "verbal" else None) or cfg.get("body") or ""
        fold = setup_fold_on()
        v.update(body=(body + "\n\n" + line + ("" if fold else " " + cue_line)).strip(),
                 hint=cfg.get("ask") or "What should Crissa call you?",
                 keys=(([("skip", "Skip")] if voice_id_skip_on() else []) + ([] if fold else [cues_key()])), mic=mv,
                 cues=cues_mode())
    elif state == "starting":
        v.update(body="Starting Voice ID…")
    elif state == "live":
        # THE SENTENCE IS ON THE PLATE (the User 2026-10-10, .101 V): the coach's glass rides in the snapshot, so a
        # renderer that reads only /v1/init still shows what to read, the beep cue and the take
        g = register_glass()
        four = four_takes()
        if voice_id_routine() == "verbal":
            v.update(body="Listen, then count to six at the beep, the way you heard it: facing forward, then with your "
                          "head turned a little to each side.",
                     glass={k: g.get(k) for k in ("phase", "text", "line", "cue", "take", "takes", "seconds", "beep_ts",
                                                  "mic", "pip", "away")})
            return v
        v.update(body={"count": "Follow the screen: count at the beep" + (", facing the camera, then turned away."
                                                                          if four else ", turned away."),
                       "read": "Follow the screen: read the sentence at the beep" + (
                           ", facing the camera, then turned away." if four else ".")}.get(
                           str(m.get("redo") or ""),
                           "Follow the screen: the sentence and the count, facing the camera and turned away, each at "
                           "its beep." if four else "Follow the screen: read the sentence at the beep, then turn and count."),
                 glass={k: g.get(k) for k in ("phase", "text", "line", "cue", "take", "takes", "seconds", "beep_ts",
                                              "mic", "pip", "away")})
    elif state == "done":
        v.update(body="Voice ID done: %d voice print%s." % (int(m.get("voice") or 0), "" if int(m.get("voice") or 0) == 1 else "s"))
    elif state == "partial":
        v.update(partial_words(m), keys=[("redo", "Redo the " + partial_take_name(m)), ("continue", "Continue")]
                 + ([] if setup_fold_on() else [cues_key()]), cues=cues_mode())
    elif state == "failed":
        skip = voice_id_skip_on()
        v.update(body=str(m.get("error") or "Voice ID did not finish."),
                 hint="Retry, or skip it for now." if skip else "Check the microphone, then Retry.",
                 keys=[("retry", "Retry")] + ([("skip", "Skip")] if skip else [])
                 + ([] if setup_fold_on() else [cues_key()]), cues=cues_mode())
    elif state == "no_coach":
        skip = voice_id_skip_on()
        v.update(body=str(m.get("error") or ""), hint="Retry once it runs, or skip it for now." if skip else
                 "Retry once it runs.", keys=[("retry", "Retry")] + ([("skip", "Skip")] if skip else []))
    return v


# ── FACE PHOTOS (the User 2026-10-10): the coach's face section, or a plain no when there is no camera ───────────────
#     no_camera   CRISSA_NO_CAMERA=1: says why and goes on by itself after `no_camera_s` (CONTINUE goes now)
#     no_person   Voice ID was skipped, so there is nobody to take photos of: CONTINUE
#     ask         START or SKIP; `camera` says whether this install's camera gave a picture just now
#     live        the coach's face section runs; its progress (look at the camera, look to the left) on the plate
#     done        the AuraFace prints are in the identity store and enroll.json
#     failed      RETRY or SKIP
FACE_POSES = 2                             # the coach's face section: look at the camera, look to the left


def face_enter(io: CardIO) -> None:
    cfg = step_cfg("face")
    reg = load_state().get("register") or {}
    if camera_off():
        n = float(cfg.get("no_camera_s") or 4)
        io.put(state="no_camera", why=str(cfg.get("no_camera") or "This computer has no camera for Crissa."),
               until=time.time() + n)
        _spawn("face-no-camera", _face_no_camera, io, n)
        return
    # THE NAME IS ENOUGH (the User 2026-10-10, .101: Voice ID failed, so the face step skipped itself and was listed
    # among the steps done with no photo). A name typed at Voice ID, or the User Init already knows, gets the photos.
    pid, display = str(reg.get("person_id") or ""), str(reg.get("display") or "")
    if not pid:
        u = _load_json(user_path())
        pid, display = str(u.get("person_id") or ""), str(u.get("name") or "")
    if not pid:
        io.put(state="no_person", why="Face photos need your name, and Voice ID was skipped before you gave one.")
        return
    if one_page_on():
        # ONE-PAGE SETUP: Your face is the Register routine's last stage (two turned photos after the counts, Lila's
        # own film asking), so this step only reads what is banked: every angle, done; anything less, done with what
        # there is (nothing more is asked in a voice that is not hers)
        bins = face_bins(pid)
        missing = face_missing(bins)
        if bins:
            sync_faces(pid)
        print("[init] Your face: %s" % ("every angle banked" if not missing else "banked %s, missing %s" % (
            ", ".join(sorted(bins)) or "nothing", ", ".join(missing))), flush=True)
        io.put(state="done" if not missing else "partial", person_id=pid, display=display, faces=sum(bins.values()),
               bins=sorted(bins), missing=missing, done=True)
        io.done()
        return
    if setup_parallel() or person_run() or (load_state().get("again") or {}).get("face"):
        # SETUP IN PARALLEL (round 18): no Start to press - the photos the counts did not give are asked at once (the
        # bank read at the start: _face_start), and with every angle banked the step is done. ONE VOICE ID ROUTINE
        # (round 21): Voice ID from the menu and Add a visitor walk the same, never a Start on a card nobody may see
        print("[init] face photos: starting by themselves (%s)" % ("Setup in parallel" if setup_parallel() else
                                                                  "one Voice ID routine"), flush=True)
        io.put(state="starting", person_id=pid, display=display, camera="checking", error="", progress={})
        _spawn("face", _face_start, io)
        return
    io.put(state="ask", person_id=pid, display=display, camera="checking")
    _spawn("face-camera", _face_camera, io)
    if face_banked_on():
        _spawn("face-banked", _face_banked, io, pid)


# FACE PHOTOS DURING THE COUNTS (the User 2026-10-12, .101 fresh install: "why don't we do face photos in the background
# while they say the six counts at the camera?"). The spoken Voice ID takes face stills on every count - facing the
# camera, then the head turned a little to one side and to the other - and stores the best of each angle (the coach's
# sweep, after its Thanks). This step then looks at what is stored: a front face and a turned one is what its own two
# photos would give (look at the camera, look to the left), so it says so and moves on by itself. Missing an angle, it
# asks as before (its photos add to what is there). It waits for the sweep for at most FACE_BANKED_WAIT_S; Start or
# Skip pressed meanwhile wins. Kill: CRISSA_FACE_FROM_VOICE_ID=0.
FACE_BANKED_WAIT_S = 20.0
FACE_BANKED_SHOW_S = 3.0


def face_banked_on() -> bool:
    return os.environ.get("CRISSA_FACE_FROM_VOICE_ID", "1").strip().lower() not in ("0", "off", "false", "no")


# A FACE AT THE CAMERA IS THIS CAMERA'S FRONT (round 19, .101 2026-10-14: count 1's still, taken while the User looked at
# the camera, read yaw -0.156 - a desk camera off to one side - and was binned three_quarter_L, so Face photos asked
# "Please look at the camera" at the end). A still the routine took while it asked for the camera (`pose_asked`
# "front": counts 1 and 3) counts as the front angle when its yaw reads a three-quarter one; a profile stays a profile.
# Kill: CRISSA_FACE_FRONT_AS_ASKED=0 (the yaw bin alone, round 18).
def face_front_as_asked_on() -> bool:
    return os.environ.get("CRISSA_FACE_FRONT_AS_ASKED", "1").strip().lower() not in ("0", "off", "false", "no")


def face_bins(person_id: str) -> dict[str, int]:
    """{bin: count} of the face records with an embedding (the face engine's angle bins: front, three_quarter_L/_R,
    profile_L/_R); a three-quarter still taken at the camera counts as front (see A FACE AT THE CAMERA)."""
    out: dict[str, int] = {}
    as_asked = face_front_as_asked_on()
    for f in sorted((identity_root() / "people" / person_id / "face").glob("*.json")) if person_id else []:
        d = _load_json(f)
        enc = d.get("encoding") or d.get("embedding")
        if isinstance(enc, list) and len(enc) >= 8:
            b = str(d.get("bin") or d.get("pose") or "unknown")
            if as_asked and str(d.get("pose_asked") or "") == "front" and b.startswith("three_quarter"):
                b = "front"
            out[b] = out.get(b, 0) + 1
    return out


# HIS LEFT IS THE PICTURE'S RIGHT (round 28, .101 2026-10-17: "It got two right profiles from me", bins front +
# three_quarter_R, left missing - and round 17's "never the right"). The angle bins are the picture's (face_yaw: the
# nose toward the image-right eye is _R), so in a true-handed picture - most webcams, .101's - a person who turns to
# HIS left lands in _R. "left" was read from _L, so his left turn was counted as his right and "look to the left" was
# asked again. His left is _R, his right _L; a picture the room step recorded as mirrored swaps them back. Kill:
# CRISSA_FACE_SIDE_AS_SEEN=0 (left = _L, round 27).
def face_side_bins() -> tuple[str, str]:
    """(the bin suffix his left turn lands in, his right's)."""
    if os.environ.get("CRISSA_FACE_SIDE_AS_SEEN", "1").strip().lower() in ("0", "off", "false", "no"):
        return "_L", "_R"
    try:
        mirrored = room_saved().get("picture") == "mirrored"
    except Exception:  # noqa: BLE001  no site record yet: a webcam's own picture, true-handed
        mirrored = False
    return ("_L", "_R") if mirrored else ("_R", "_L")


def face_missing(bins: dict[str, int]) -> list:
    """The angles still wanted, in the face routine's own names: front, left (his left), right (his right). Round 16
    (the User 2026-10-13, .101: the turn to the right came out front - no right side - and Face photos moved on anyway):
    both sides are wanted, and only what is missing is asked for. Which bin is his left: face_side_bins."""
    left_bin, right_bin = face_side_bins()
    have_l = any(n and str(b).endswith(left_bin) for b, n in bins.items())
    have_r = any(n and str(b).endswith(right_bin) for b, n in bins.items())
    return [x for x, ok in (("front", bool(bins.get("front"))), ("left", have_l), ("right", have_r)) if not ok]


def face_angles_banked(bins: dict[str, int]) -> bool:
    """A front face and both turned sides: nothing for the step to ask."""
    return not face_missing(bins)


# A BANKED SIDE IS NEVER ASKED AGAIN, AND EVERY MISSING ONE IS (round 17, .101 2026-10-13 on round 16: Voice ID banked the
# front, then Face photos asked "look at the camera" and "look to the left" and never the right). Round 16 named the
# missing poses only when its 20 s wait for the sweep ran out, and only when some angle was banked; Start pressed in
# those 20 s (or nothing banked) sent no poses, so the coach took its own two, front and left, whatever was there. Now
# the card names the missing sides as soon as it reads them, and Start asks the bank itself at the press: only what is
# missing, each once (front, left, right: all three when nothing is banked); nothing missing, and the step is done.
# Kill: CRISSA_FACE_ASK_MISSING=0 (round 16).
def face_ask_missing_on() -> bool:
    return os.environ.get("CRISSA_FACE_ASK_MISSING", "1").strip().lower() not in ("0", "off", "false", "no")


def _face_banked(io: CardIO, pid: str) -> None:
    deadline = time.time() + FACE_BANKED_WAIT_S
    polls = max(1, int(round(FACE_BANKED_WAIT_S / 1.0)))   # and counted in polls, so a test's no-op _sleep never spins
    while True:
        polls -= 1
        m = io.get() or {}
        if m.get("state") != "ask":
            return                                     # Start, Skip or the walk moved on: theirs
        bins = face_bins(pid)
        if face_angles_banked(bins):
            n = sum(bins.values())
            print("[init] face photos: taken during Voice ID (%s), the step moves on" % ", ".join(sorted(bins)),
                  flush=True)
            if not io.put(state="banked", faces=n, bins=sorted(bins)):
                return
            sync_faces(pid)
            _sleep(FACE_BANKED_SHOW_S)
            if (io.get() or {}).get("state") == "banked":
                io.put(state="done", faces=n, done=True)
                io.done()
            return
        if bins and face_ask_missing_on():
            missing = face_missing(bins)
            if missing and list(m.get("poses") or []) != missing:
                io.put(poses=missing, have=sorted(bins))        # the card says what is still wanted, now
        if time.time() >= deadline or polls <= 0:
            missing = face_missing(bins)
            if bins and missing and len(missing) < 3:
                # the counts gave some angles: the step asks for the rest only (the coach takes just those poses)
                print("[init] face photos: still wanted %s (have %s)" % (", ".join(missing), ", ".join(sorted(bins))),
                      flush=True)
                io.put(poses=missing, have=sorted(bins))
            return
        _sleep(1.0)


def _face_no_camera(io: CardIO, n: float) -> None:
    _sleep(max(0.0, n))
    if (io.get() or {}).get("state") == "no_camera":
        io.done()


def _face_camera(io: CardIO) -> None:
    try:
        grab_frame()
        io.put(camera="ready")
    except Exception as e:  # noqa: BLE001
        io.put(camera="no picture: %s" % str(e)[:120])


def face_act(io: CardIO, verb: str) -> dict[str, Any]:
    m = io.get() or {}
    state, verb = str(m.get("state") or ""), str(verb or "").strip().lower()
    if verb in ("start", "retry") and state in ("ask", "failed"):
        io.put(state="starting", error="", progress={})
        _spawn("face", _face_start, io)
        return {"ok": True, "verb": verb}
    if verb in ("skip", "continue") and state in ("ask", "failed", "no_camera", "no_person"):
        io.put(state="skipped" if state not in ("no_camera", "no_person") else state)
        io.done()
        return {"ok": True, "verb": verb}
    return {"ok": False, "error": "%s is not an answer to the face photos now (%s)" % (verb or "nothing", state or "-")}


def _face_start(io: CardIO) -> None:
    m = io.get() or {}
    ok, why = _coach_ready()
    if not ok:
        io.put(state="failed", error="the Register coach is not running on this computer" if why == "no_coach" else why)
        return
    pid = str(m.get("person_id") or "")
    have = (identity_root() / "people" / pid / "person.json").is_file() if pid else False
    body = {"mode": "face", "person_id": pid, "display": m.get("display") or "", "station": "desk",
            "create": not have, "first_boot": True}
    if ((load_state().get("again") or {}).get("face")):
        body["poses"] = "front,left,right"                      # FACE PHOTOS AGAIN: every angle, the old ones kept
        print("[init] Face photos again: asking front, left, right", flush=True)
    elif pid and face_banked_on() and face_ask_missing_on():
        bins = face_bins(pid)                                   # the bank at the press, never an older reading
        missing = face_missing(bins)
        if not missing:
            n = sum(bins.values())
            print("[init] face photos: every angle is banked (%s), nothing to ask" % ", ".join(sorted(bins)),
                  flush=True)
            sync_faces(pid)
            if io.put(state="done", faces=n, done=True, poses=[], have=sorted(bins)):
                io.done()
            return
        print("[init] face photos: asking %s (banked: %s)" % (", ".join(missing), ", ".join(sorted(bins)) or "none"),
              flush=True)
        body["poses"] = ",".join(missing)
    elif m.get("poses"):
        body["poses"] = ",".join(str(x) for x in m["poses"])   # the missing angles only (face_missing)
    got = _coach_post("/start", body, 20.0)
    if not got.get("_ok") or got.get("ok") is False:
        io.put(state="failed", error=str(got.get("error") or got.get("_error") or "the coach refused")[:200])
        return
    if not io.put(state="live", sent_at=time.time()):
        return
    _coach_watch(io, "face", float(step_cfg("face").get("timeout_s") or 180))


def face_view(m: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or step_cfg("face")
    state = str(m.get("state") or "")
    v: dict[str, Any] = {"state": state, "title": cfg.get("title") or "Face photos", "body": "", "hint": "", "keys": []}
    if state == "no_camera":
        v.update(body=str(m.get("why") or ""), keys=[("continue", "Continue")])
    elif state == "no_person":
        v.update(body=str(m.get("why") or ""), keys=[("continue", "Continue")])
    elif state == "ask":
        cam = str(m.get("camera") or "")
        body = cfg.get("body") or ""
        want = [str(x) for x in (m.get("poses") or [])]
        if want:
            words = {"front": "looking at the camera", "left": "looking to the left", "right": "looking to the right"}
            body = ("Your counts gave Crissa most of your face. One more photo%s: %s." % (
                "" if len(want) == 1 else "s", " and ".join(words.get(w, w) for w in want)))
        v.update(body=body, hint=("" if cam in ("ready", "checking", "") else "The camera: %s." % cam),
                 keys=[("start", "Start"), ("skip", "Skip")])
    elif state in ("starting", "live"):
        pr = m.get("progress") or {}
        total = int(pr.get("total") or 0) or (len(m.get("poses") or []) or FACE_POSES)
        v.update(body=str(pr.get("line") or "Look at the camera."),
                 hint="Photo %d of %d saved" % (min(int(pr.get("done") or 0), total), total), counter=[
                     min(int(pr.get("done") or 0), total), total])
    elif state == "banked":
        v.update(body="Your face photos were taken while you counted: %d, facing the camera and turned to each side. "
                      "Nothing more to do here." % int(m.get("faces") or 0))
    elif state == "done":
        v.update(body="Face photos done: %d." % int(m.get("faces") or 0))
    elif state == "failed":
        v.update(body=str(m.get("error") or "The face photos did not finish."), hint="Retry, or skip them for now.",
                 keys=[("retry", "Retry"), ("skip", "Skip")])
    return v


# ── CANCEL ASKS FIRST ON THE DESK WALL (the User 2026-10-11, .101 JJ: its one-click Cancel reset the walk) ─────────────
def cancel_ask(run_id: str, back: bool = False) -> dict[str, Any]:
    """The desk Wall's first press of Cancel: the plate asks (YES, CANCEL SETUP / BACK) instead of cancelling. The
    browser page asks with its own confirm box; either way the walk itself cancels only on `abort`."""
    st = _mutate(run_id, lambda s: (s.pop("cancel_ask", None) if back else s.update(cancel_ask={"at": _now()})))
    return {"ok": st is not None, "asking": bool(st and st.get("cancel_ask"))}


# ── LILA'S LINES (the User 2026-10-11: "maybe add Lila piece back in") ─────────────────────────────────────────────────
# Her own filmed lines, played on the player like the Register coach's (pack host rows: lila-ob-*): a first boot opens
# with lila-ob-welcome; Your room and Your hours each say theirs. She slides onto her plate first (host.enter), waits on
# her silent loop between lines (host.loop) and slides back (host.leave) when the walk moves past her cards. Never
# lila-ob-handoff or lila-ob-thanks-crissa: they name the house's character. Cues text-only (setup-cues.json, the coach's
# own rule) or CRISSA_INIT_SILENT=1: nothing plays - the cards carry the words. One sequence at a time: a newer one, a
# step that moved on, or a cancel stops the rest (the clip on air finishes).
_HOST: dict[str, Any] = {"gen": 0, "on": False, "secs": {}}


def lines_on() -> bool:
    if os.environ.get("CRISSA_INIT_SILENT", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return cues_mode() != "text" and bool(_CFG.get("base"))


def host_keys() -> dict[str, str]:
    h = pack_cfg().get("host") or {}
    return {k: str(h.get(k) or "") for k in ("enter", "leave", "loop", "welcome")}


def _poses_dir() -> Path:
    raw = (os.environ.get("HOUSE_POSE_DIR") or "display/crissa-poses").strip()
    p = Path(raw).expanduser()
    return p if p.is_absolute() else REPO / p


def clip_seconds(key: str) -> float:
    """How long one of her clips runs: its frames (OpenCV, the player's own package), else the row's spoken window
    plus its tail, else 5 s. Cached per key."""
    if key in _HOST["secs"]:
        return _HOST["secs"][key]
    secs = 0.0
    row = ((_load_json(_poses_dir() / "inventory.json").get("clips") or {}).get(key) or {})
    f = _poses_dir() / str(row.get("file") or "")
    if row.get("file") and f.is_file():
        try:
            import cv2
            cap = cv2.VideoCapture(str(f))
            n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 24.0
            cap.release()
            secs = float(n) / float(fps) if n and fps else 0.0
        except Exception:  # noqa: BLE001
            secs = 0.0
    if secs <= 0.0:
        sp = (row.get("host") or {}).get("speech") or []
        secs = (float(sp[1]) + 1.7) if len(sp) == 2 else 5.0
    _HOST["secs"][key] = round(secs, 2)
    return _HOST["secs"][key]


def _host_live(run_id: str, sid: str, gen: int) -> bool:
    if _HOST["gen"] != gen:
        return False
    st = load_state()
    return st.get("status") == "running" and st.get("run_id") == run_id and (not sid or st.get("step_id") == sid)


def _host_play(key: str, voice: bool) -> bool:
    """One of her clips on the player; True when the player took it. by "init": the picture only (the conductor's rule
    for a caller that plays its own sound); "setup": her line too."""
    return bool(_post("/v1/play", {"key": key, "force": True, "by": "setup" if voice else "init"}).get("ok"))


def _host_run(run_id: str, sid: str, keys: list, gen: int, leave_after: bool = False, wanted=None, done=None) -> None:
    """`wanted(key)` (round 18): is this item still wanted when its turn comes (ONE VOICE AT A TIME). `done()` (round
    21): every item has had its turn (said or dropped)."""
    hk = host_keys()
    keys = list(keys)
    if not _HOST["on"] and hk["enter"] and not sid and not keys and hk.get("loop") and no_desk_before_entrance() \
            and load_state().get("first_boot"):
        # round 30: Setup's open (host_arrive) - straight onto her loop, camped; the enter film starts on the desk
        if not _host_live(run_id, sid, gen):
            return
        if office_now():
            if not (OFFICE.get("key") and _player_now_key() == OFFICE["key"]) and not office_hold():
                if not play(hk["loop"], stay=True):
                    return
        elif not play(hk["loop"], stay=True):
            return
        _HOST["on"] = True
        print("[init] Lila opens on her loop (%s), no walk-on from the desk" % hk["loop"], flush=True)
    if not _HOST["on"] and hk["enter"]:
        if not _host_live(run_id, sid, gen):
            return
        if not _host_play(hk["enter"], False):
            return                                  # no player took it: nothing to wait for, no line to say
        _HOST["on"] = True
        # with her lips on her lines wait for her loop (lila_lips_on): her walk-in is a slide whose own playlist
        # carries Crissa's tiles, so a line said on it would paint Crissa's mouth on Lila
        if keys and str(keys[0]).startswith(SAY_PREFIX) and not lila_lips_on():
            # her spoken intro rides her walk-in (the slide is her picture while she talks; her films carry their own
            # mouths): said as she enters, the next line waits for both
            text = str(keys.pop(0))[len(SAY_PREFIX):]
            print("[init] Lila says: %s" % text[:80], flush=True)
            t0 = time.time()
            say(text, voice="lila")
            _sleep(max(0.0, clip_seconds(hk["enter"]) - (time.time() - t0)))
            _wait_said(text)
        else:
            _sleep(clip_seconds(hk["enter"]))
    for k in keys:
        if not _host_live(run_id, sid, gen):
            return

        def still(k=k) -> bool:
            return _host_live(run_id, sid, gen) and (wanted is None or bool(wanted(k)))
        if str(k).startswith(SAY_PREFIX):
            text = str(k)[len(SAY_PREFIX):]
            ran, _ = speak_turn(lambda text=text: _lila_says(text), wanted=still, what="Lila's line %r" % text[:40])
            if ran:
                _wait_said(text)
            continue
        if film_live(k):                       # round 19: her film's words in the install's own voice
            text = film_words(k)
            ran, _ = speak_turn(lambda text=text: _lila_says(text), wanted=still, what="Lila's %s, said live" % k)
            if ran:
                _wait_said(text)
            continue
        ran, played = speak_turn(lambda k=k: _lila_film(k), wanted=still, what="Lila's %s" % k)
        if ran and played:
            _sleep(clip_seconds(k))
    if done is not None:
        try:
            done()
        except Exception:  # noqa: BLE001  a bookkeeping hook never costs her the loop
            pass
    if leave_after:
        if _HOST["gen"] == gen:
            _host_leave_now()
        return
    if hk["loop"] and _host_live(run_id, sid, gen) and not office_now():
        _post("/v1/cue", {"key": hk["loop"], "ttl": 0, "bridge": False, "by": "init"})


def _lila_says(text: str) -> bool:
    print("[init] Lila says: %s" % text[:80], flush=True)
    say(text, voice="lila")
    return True


def _lila_film(key: str) -> bool:
    print("[init] Lila: %s" % key, flush=True)
    return _host_play(key, True)


# A LINE SHE SAYS LIVE (round 15 setup, the User 2026-10-12: "maybe an intro here" at Your room): "say:<words>" in a
# host sequence is spoken by the player in her own voice from the voice tiers (voice "lila": Pocket eve, or her own
# Grok Imagine clip on the voices that clone), never a film of unknown sound. _wait_said holds the next line until it
# is said (the player's own "is a line on air", else its length at a speaking pace).
SAY_PREFIX = "say:"


# HER FILMS IN THE INSTALL'S OWN VOICE (round 19, the User 2026-10-14, .101 round 18: "her voice on initial clip intro was
# her .100 voice, the rest were consistent"). Lila's Setup films carry her own clip's voice (lila-imagine, put on them by
# speech-to-speech for the pack, config/crissa-film-provenance.json); her live lines speak in the install's voice. Where
# that voice is a stock one - Pocket, Kokoro, the Mac's say, Piper: none of them clones - a film's words (host
# `film_words` in the walk file: what the pack's film says) are said live on her loop with her lips instead, so every
# line of hers in Setup is one voice. Where her voice clones her own clip (Turbo, Multilingual, Audio8) the film plays
# as filmed: that is her voice already. The welcome comes before a first boot's voice is picked (Connect, then This
# computer): it waits for the pick, at most WELCOME_WAIT_S and only while nothing else has been said in Setup's turn;
# the wait over, it plays as filmed. A pack, with her lips on. Kill: CRISSA_LILA_FILMS_LIVE=0 (her films as filmed).
STOCK_VOICE_ENGINES = ("pocket", "kokoro", "say", "piper")
WELCOME_WAIT_S = 120.0
WELCOME_POLL_S = 0.5


def films_live_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_LILA_FILMS_LIVE", "1").strip().lower() not in ("0", "off", "false", "no")


def film_words(key: str) -> str:
    """What one of her films says, from the walk file's host `film_words` ("" = not listed)."""
    fw = (pack_cfg().get("host") or {}).get("film_words") or {}
    return " ".join(str(fw.get(key) or "").split()) if isinstance(fw, dict) else ""


def her_engine() -> str:
    """The engine her voice speaks with, once it is known: the voice install's choice when it has ended well, else
    (no installer here) the voice record's main engine when it is not missing. "" = not known yet."""
    vi = _voice_install_state()
    if vi.get("state") == "done" and vi.get("engine"):
        return str(vi.get("engine"))
    if vi.get("state") == "failed":
        return str(vi.get("engine") or "text")                  # no voice installed: her films speak as filmed
    if vi.get("state") in ("installing", "idle"):
        return ""
    try:
        import crissa_voice_tiers as T
        main = str((T.read(_home()) or {}).get("main") or "")
        return main if main and T.installed(main) is not False else ""
    except Exception:  # noqa: BLE001  no voice tiers: not known
        return ""


def film_live(key: str) -> bool:
    """Is this film of hers said live in the install's voice now (HER FILMS IN THE INSTALL'S OWN VOICE)?"""
    return (films_live_on() and lila_lips_on() and bool(film_words(key))
            and her_engine() in STOCK_VOICE_ENGINES)


def _welcome_when_voiced(run_id: str, key: str, said0: int) -> None:
    """A first boot's welcome, once her voice is known (or the wait is over), while nothing has been said since."""
    t0 = time.time()
    polls = int(WELCOME_WAIT_S / WELCOME_POLL_S) + 1           # time AND polls: a test's no-op _sleep never spins
    while polls > 0 and time.time() - t0 < WELCOME_WAIT_S:
        polls -= 1
        st = load_state()
        if st.get("status") != "running" or st.get("run_id") != run_id or _SAID["n"] != said0:
            print("[init] Lila's welcome: not said (Setup went on without it)", flush=True)
            return
        if her_engine():
            break
        _sleep(WELCOME_POLL_S)
    print("[init] Lila's welcome after %.1f s: %s" % (time.time() - t0, "live, in %s" % her_engine()
                                                       if film_live(key) else "her film"), flush=True)
    host_say(run_id, "", [key], wanted=lambda _k: _SAID["n"] == said0)


def _wait_said(text: str, cap_s: float = 25.0) -> None:
    # round 18: the line in flight, whoever's (`busy`: its house-speak alive), else "on air" as before. "On air" is the
    # painter's own flag and read False for every Lila line her mouth could not paint, so her lines were timed by
    # their length alone (round 17: the next one started while the last still played)
    fn = _CFG.get("busy") or _CFG.get("speaking")
    est = min(cap_s, 1.0 + 0.068 * len(text))
    if not callable(fn):
        _sleep(est)
        return
    t0 = time.time()
    started = False
    while time.time() - t0 < cap_s:
        try:
            on = bool(fn())
        except Exception:  # noqa: BLE001
            on = False
        started = started or on
        if started and not on:
            return
        if not started and time.time() - t0 > min(3.0, est):
            _sleep(max(0.0, est - (time.time() - t0)))      # never seen on air: its length, then on
            return
        _sleep(0.15)


def host_say(run_id: str, sid: str, keys: list, wanted=None, done=None) -> bool:
    """Lila says `keys` in turn on step `sid` (onto her plate first). False when lines are off. `wanted(key)` (round
    18): read at each item's turn; an item no longer wanted (the person has answered it) is dropped. `done()` (round
    21): called once every item has had its turn."""
    keys = [str(k) for k in keys if k]
    if not keys or not lines_on():
        return False
    if one_page_on():                       # ONE-PAGE SETUP: her lines are her films, played by the page
        if done is not None:
            try:
                done()
            except Exception:  # noqa: BLE001
                pass
        return True
    _HOST["gen"] += 1
    _spawn("lila-" + sid, _host_run, run_id, sid, keys, _HOST["gen"], False, wanted, done)
    return True


# ONE VOICE AT A TIME, AND NOTHING LATE (round 18, the User 2026-10-13, .101 round 17: "two scripts played on" - Face
# photos' last cue, the coach's through the player's /v1/speak, was still on air when Your room's 112-character line,
# Init's own, began 2 s before it ended; and "i finished the dial before the lila script started playing"). Setup spoke
# two ways and neither waited for the other: Init spawned its lines straight into house-speak, the coach's went through
# /v1/speak, whose newest line stops what is on the air. Now every Setup line takes a TURN (speak_turn): under one lock
# it waits until nothing is in flight - no house-speak of the player's alive, no film's own line playing (the player's
# `busy`) - for QUIET_SETTLE_POLLS reads in a row, starts, and waits for it to be in flight, so the next turn waits for
# it to end. Init's lines (Lila's, the announcer's own, Crissa's), her films and the coach's lines through /v1/speak
# (queue_takes) all take it. A line tied to a step says whether it is still wanted (`wanted`, read at its turn): Your
# room's intro and a mark's line are dropped once the person has set that mark or gone past it, the wave's once it is
# answered, Your hours' once the hours are changed, saved or skipped. A pack only. Kill: CRISSA_SETUP_ONE_QUEUE=0 (each
# line as it comes, round 17).
QUIET_POLL_S = 0.05
QUIET_SETTLE_POLLS = 5                     # 0.25 s of nothing in flight
QUIET_CAP_S = 30.0                         # a line that never ends is not waited for longer than this
ON_AIR_CAP_S = 2.0                         # a line that never shows as in flight (a silent film) holds the turn this long
_TURN = threading.Lock()
_ENDING: dict[str, Any] = {"on": False, "seated": False}    # the team's end is speaking (status done already): the coach's lines queue
_SAID: dict[str, int] = {"n": 0}           # lines started in Setup's turn (round 19: the welcome waits only while 0 more)


def queue_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_SETUP_ONE_QUEUE", "1").strip().lower() not in ("0", "off", "false", "no")


def queue_takes() -> bool:
    """Does a line the player is asked to say (/v1/speak: the coach's cues) take Setup's turn now?"""
    if not queue_on():
        return False
    if _ENDING.get("on"):
        return True
    try:
        return load_state().get("status") == "running"
    except Exception:  # noqa: BLE001
        return False


def busy() -> bool:
    fn = _CFG.get("busy") or _CFG.get("speaking")
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


def wait_quiet(cap_s: float = QUIET_CAP_S) -> bool:
    """True once nothing has been in flight for QUIET_SETTLE_POLLS reads in a row; False at the cap (time AND polls:
    a test's no-op _sleep never spins)."""
    t0 = time.time()
    polls = max(1, int(round(cap_s / QUIET_POLL_S)))
    calm = 0
    while polls > 0 and time.time() - t0 < cap_s:
        polls -= 1
        calm = 0 if busy() else calm + 1
        if calm >= QUIET_SETTLE_POLLS:
            return True
        _sleep(QUIET_POLL_S)
    return False


def _in_flight(cap_s: float = ON_AIR_CAP_S) -> bool:
    polls = max(1, int(round(cap_s / QUIET_POLL_S)))
    t0 = time.time()
    while polls > 0 and time.time() - t0 < cap_s:
        polls -= 1
        if busy():
            return True
        _sleep(QUIET_POLL_S)
    return False


def speak_turn(fn, *, wanted=None, what: str = "a line", cap_s: float = QUIET_CAP_S) -> tuple[bool, Any]:
    """(ran, fn's answer): fn (which starts one line) in Setup's turn (ONE VOICE AT A TIME). Not run when `wanted()`
    says no at its turn. With the queue off: run at once, as before."""
    if not queue_on():
        return True, fn()
    t0 = time.time()
    got = _TURN.acquire(timeout=max(0.1, float(cap_s)))
    try:
        if not wait_quiet(max(0.1, float(cap_s) - (time.time() - t0))):      # one budget for the turn and the quiet
            print("[init] %s: something was still on the air after %d s; it goes now" % (what, int(cap_s)), flush=True)
        if wanted is not None:
            try:
                ok = bool(wanted())
            except Exception:  # noqa: BLE001  a check that breaks never keeps a line
                ok = True
            if not ok:
                print("[init] %s: not said, the person had already answered it" % what, flush=True)
                return False, None
        out = fn()
        if out is not False and out is not None:
            _SAID["n"] += 1
            _in_flight()                           # the next turn waits for this one, once it is in flight
        return True, out
    finally:
        if got:
            _TURN.release()


# LILA'S LIPS ON HER LINES (round 18, the User 2026-10-13, .101 round 17: "lila on screen no lips moving" - five of her
# cues were aligned and posted to the player and no row was painted: no `lip clock`, no `semis`, the glass on
# lila-reg-listen from memory). Two causes in the player, both a pack's: its painter lets "lila" move a mouth only while
# the house's Crissa gateway is down (Mouth.speaker_may_paint; a pack has no crissa_gateway module, so it read "up" and
# refused her every line), and her hold loop has no playlist of its own while a pack names no face bank, so there were
# no tiles to paint (Library.playlist's fallback to her bank's is the house's way). The player now lets her paint while
# she is on her plate (LILA_PAINTS = lila_lips_now) and names her shipped bank for her tiles only (house-conductor
# LIPS_ONLY_BANKS); every line of hers in Setup goes through the player - Init's, the coach's cues and its counts
# (voice_register._announce, through /v1/speak with the rendered line) - so her mouth moves on each. A pack only.
# Kill: CRISSA_LILA_LIPS=0 (round 17: her lines unpainted, the counts plain audio from the coach).
def lila_lips_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_LILA_LIPS", "1").strip().lower() not in ("0", "off", "false", "no")


def lila_lips_now() -> bool:
    """May the player paint a "lila" line now: she is on her plate (this process's own state) and her lips are on.
    Read by the painter every frame, so no file is read: the player wires it only off the house."""
    return bool(_HOST.get("on")) and os.environ.get("CRISSA_LILA_LIPS", "1").strip().lower() not in (
        "0", "off", "false", "no")


# LILA HOLDS THE SCREEN FOR ALL OF SETUP (the User 2026-10-13, .101 round 15: "keep Lila up for the whole Setup ... and
# introduce Crissa at the end"; her welcome played before Connect with no lips, then she slid off and Crissa's loops
# played, so the person met Crissa's face before she was introduced). In a pack's first boot: she walks on as Setup
# opens and waits on her own loop through Connect; her welcome film plays once Connect is done (it keeps its own film
# voice: it plays before the machine scan picks an engine); she stays on her loops between and after her cards; at the
# end she says "Here's Crissa. Crissa, meet <the name given>." (her voice from the tiers), slides off, Crissa's listen
# loop comes on and Crissa opens with "Hello! What can we do together?" in her own voice. Kill: CRISSA_LILA_HOLDS=0
# (the 10-11 walk: welcome first, off between cards, a landing on listen).
HANDOFF_LINE = "Here's Crissa."
HANDOFF_MEET = "Here's Crissa. Crissa, meet %s."
OPENING_LINE = "Hello! What can we do together?"


# A SETUP THAT NEVER FINISHED IS HOSTED WHEN IT RUNS AGAIN (round 33, the User 2026-10-19: "seeing Crissa at desk before
# her entrance during and before the init sequence"; he keeps the 10-05 rule, a cancel writes the marker). After a
# cancelled first boot the marker says done false, and the next start lands on listen as before. Its "Set up again"
# walked the old walk, with nothing of Lila's hold in it. The walk opened on the desk. Lila's cards left her plate,
# and the desk showed under every other card. A cancel and a step change landed on listen. There was no entrance.
# Now a whole walk started while the marker still says done false is Lila's walk, as the first one was. She opens on
# her loop with no walk-on, holds her plate, and hands over to the entrance. A player restart while that walk runs
# boots on her loop (boot_host_loop). A "set up again" after a finished Setup walks as before.
# Kill CRISSA_SETUP_AGAIN_HOSTED=0.
def setup_again_hosted_on() -> bool:
    return os.environ.get("CRISSA_SETUP_AGAIN_HOSTED", "1").strip().lower() not in ("0", "off", "false", "no")


def setup_unfinished() -> bool:
    """A marker is there and says Setup never finished (a cancelled walk's), and the switch is on."""
    if not setup_again_hosted_on() or not marker_path().is_file():
        return False
    return not marker().get("done")


def lila_holds(st: dict[str, Any] | None = None) -> bool:
    if crissa_home.is_house() or os.environ.get("CRISSA_LILA_HOLDS", "1").strip().lower() in (
            "0", "off", "false", "no"):
        return False
    st = st if st is not None else load_state()
    return bool(st.get("first_boot")) and not st.get("only")


def lila_may_play() -> bool:
    """May the player route to Lila's own clips now (lib/conductor GUEST_GATE): Setup is running, or she is on her plate
    (her leave after Setup, or a walk Setup asked for). Never otherwise: she is not in Crissa's idle."""
    if _HOST.get("on"):
        return True
    try:
        return load_state().get("status") == "running"
    except Exception:  # noqa: BLE001
        return False


# SHE HOLDS HER PLATE (round 17, the User 2026-10-13, .101 round 16: right after her welcome the player went
# lila-listen-leave and Crissa's work / flourish loops for the whole This computer step, and the card's picture - the
# player's own frame - showed Crissa at her laptop; Crissa was on screen for the her-voice step too). Her loop was
# cued, not camped, so the player's own wrap rule walked her home after three wraps (~30 s). lib/conductor GUEST_HOLD
# asks this: while it is true and one of her clips is on, the player never walks her home and takes no cue of another
# rack, so nothing of Crissa is drawn before the handoff. Kill: CRISSA_LILA_HOLDS_PLATE=0 (the round-16 walk).
def plate_hold_on() -> bool:
    return os.environ.get("CRISSA_LILA_HOLDS_PLATE", "1").strip().lower() not in ("0", "off", "false", "no")


# AN IDS RUN TAKES HER PLATE AS SETUP DOES (round 42, .101's R41-2, the User 10-22: Lila's lips stayed still on the Wall
# during an IDs run while her window showed her talking, and a minimize dealt Crissa back): the run's Lila was forced
# onto the player with her plate never taken, so lila_lips_now / lila_holding (both read _HOST["on"], which only
# Setup's walk-on set) said no. The run now walks her on with Setup's own host_arrive, and she holds her plate while it
# runs; its end walks her off (the "only" run's host_leave), a cancel stops her (host_stop). Not D9: one routine
# reused, nothing carved. Kill CRISSA_IDS_TAKES_PLATE=0 (round 41).
def ids_takes_plate_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_TAKES_PLATE", "1")).strip().lower() not in ("0", "off", "false", "no")


def lila_holding() -> bool:
    """Lila holds the screen now: a pack's first-boot Setup is running (lila_holds), or an IDs run (round 42), and she
    is on her plate."""
    if not plate_hold_on() or not _HOST.get("on"):
        return False
    try:
        st = load_state()
    except Exception:  # noqa: BLE001  unreadable: she holds nothing, the old walk
        return False
    return st.get("status") == "running" and (lila_holds(st) or (bool(st.get("ids")) and ids_takes_plate_on()))


# A FIRST BOOT OPENS ON LILA (.101 round 31, the User: "crissa loop flashed first again"): the player booted resting on
# Crissa's listen and Setup cued Lila only once her window's page started the walk - the Wall drew 81 pictures of
# Crissa (4.7 s) first. On a first boot the player now takes Lila's loop, camped, as its very first clip
# (boot_host_loop, called before its playhead runs); host_arrive then finds her on. Kill CRISSA_BOOT_ON_LILA=0.
# THE OFFICE WAITS (round 34, the User 2026-10-19 on .101's round 32: "Lila's lips are still on ascii and render window
# ... do not have a still lila on one half and a talking version in the other ... not a twin lila staring forward").
# On the one-page Setup her window plays Lila's own films, and the player camped on Lila's loop (fix 18, round 30), so
# the Wall showed a second, still Lila. Now, while the one page carries Setup, the player holds Crissa's entrance film
# on its first frame - her empty office - and that frame is the Wall's picture. Her walk-in at the end starts from the
# same frame, so she walks into the office the Wall has shown all along. One Lila, in her window. The page shows its
# own films, never the player's picture, so the window does not change.
# The alternatives the User named (the same render on both, the User's face on the Wall) are not built: the first
# needs the one page's films in the player's library, the second the camera on the Wall.
# A pack only. Kill CRISSA_OFFICE_WAITS=0 (round 32-33: Lila's loop on the player).
OFFICE: dict[str, str] = {"key": ""}


def office_waits_on() -> bool:
    if crissa_home.is_house() or not FILMS_CFG.is_file() or not entrance_on():
        return False
    return os.environ.get("CRISSA_OFFICE_WAITS", "1").strip().lower() not in ("0", "off", "false", "no")


def _office_keys() -> list[str]:
    return ([CRISSA_SIT_HELLO] if opener_one_line_on() else []) + [CRISSA_ENTRANCE]


def office_now(st: dict[str, Any] | None = None) -> bool:
    """The running walk is the one page's, Lila holding it: the player shows her empty office, not Lila."""
    st = st if st is not None else load_state()
    if st.get("ids") and ids_takes_plate_on():
        return False                               # round 42 (R41-2): an IDs run is hosted on her plate, not the office
    return office_waits_on() and st.get("status") == "running" and one_page_on(st)


def office_hold() -> bool:
    """Her empty office on the player: the entrance film held on its first frame. False: no entrance film here."""
    for key in _office_keys():
        got = _post("/v1/play", {"key": key, "force": True, "stay": True, "by": "init", "hold_first": True})
        if got.get("ok"):
            # the clip the player took (v2: v2-walk-in for crissa-walk-in), so the open path's compare with the player's
            # own key holds (the 10-29 review of cut 13)
            key = str(got.get("now") or "") or key
            OFFICE["key"] = key
            print("[init] the Wall shows her empty office (%s, first frame) while Lila is in Setup's window" % key,
                  flush=True)
            return True
    return False


def boot_office_key(clips: dict | None = None, resolve=None) -> str:
    """At a first boot the one page will carry: the entrance film to hold on its first frame, else ''. `resolve` (the
    player's Graph.resolve): the worn library's own clip for each classic key (the 10-29 review of cut 13: with v2 worn
    crissa-walk-in is not a row, so the first picture was Crissa at her desk, not her empty office)."""
    if not office_waits_on() or crissa_home.is_house() or not needed() or marker_path().is_file():
        return ""
    silent = os.environ.get("CRISSA_INIT_SILENT", "").strip().lower() in ("1", "true", "yes", "on")
    if silent or cues_mode() == "text" or not no_desk_before_entrance():
        return ""
    for key in _office_keys():
        if callable(resolve):
            try:
                key = resolve(key) or key
            except Exception:  # noqa: BLE001  the classic key as before
                pass
        if clips is None or (clips.get(key) or {}).get("file"):
            OFFICE["key"] = key
            _HOST["on"] = True                     # Lila is on, in Setup's window
            return key
    return ""


def boot_host_loop() -> str:
    """Lila's loop key when this boot is a first boot she opens (her plate taken now), else ''."""
    if os.environ.get("CRISSA_BOOT_ON_LILA", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    if crissa_home.is_house() or not needed():
        return ""
    if office_waits_on() and not marker_path().is_file():
        return ""                              # round 34: THE OFFICE WAITS (boot_office_key), not her loop
    if marker_path().is_file():
        st = load_state()                      # round 33: a hosted walk still running (a Setup run again) opens on her
        if not (setup_again_hosted_on() and st.get("status") == "running" and lila_holds(st)):
            return ""
    if os.environ.get("CRISSA_LILA_HOLDS", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    silent = os.environ.get("CRISSA_INIT_SILENT", "").strip().lower() in ("1", "true", "yes", "on")
    if silent or cues_mode() == "text" or not no_desk_before_entrance():
        return ""                              # lines_on() less its player address: configure() comes after the boot
    key = str(host_keys().get("loop") or "")
    if key:
        _HOST["on"] = True                     # her plate is taken: the guest gate lets her loop play before Setup runs
    return key


def host_arrive(run_id: str) -> bool:
    """She walks onto her plate and waits on her loop, saying nothing (Setup has opened on Connect)."""
    if not lines_on():
        return False
    _HOST["gen"] += 1
    _spawn("lila-arrive", _host_run, run_id, "", [], _HOST["gen"])
    return True


def host_after_connect(run_id: str, sid: str) -> bool:
    """Her welcome film once Connect is done, then her loop; said live in her voice once it is known (round 19)."""
    key = host_keys()["welcome"]
    if not key or not lines_on() or one_page_on():
        return False                       # ONE-PAGE SETUP: her welcome film opens the page itself
    if films_live_on() and lila_lips_on() and film_words(key) and not her_engine():
        _spawn("lila-welcome", _welcome_when_voiced, run_id, key, _SAID["n"])   # HER FILMS IN THE INSTALL'S OWN VOICE
        return True
    return host_say(run_id, sid, [key])


def handoff_words(name: str = "") -> str:
    name = " ".join(str(name or "").split())[:40]
    return (HANDOFF_MEET % name) if name else HANDOFF_LINE


def _player_now_key() -> str:
    h = _player_get("/v1/health")
    return str((h or {}).get("now") or "") if isinstance(h, dict) else ""


def _player_now(timeout: float = 1.5) -> dict[str, Any]:
    base = _CFG.get("base") or ""
    if not base:
        return {}
    try:
        req = urllib.request.Request(base + "/v1/state", headers={"User-Agent": "crissa-init"})
        with crissa_egress.urlopen(req, timeout, what="init: the player's state") as r:
            d = json.loads(r.read().decode() or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# HER PICTURE IS UP BEFORE SHE SPEAKS (round 21, .101 2026-10-14 round 20: "her loop not seen drawn in time" again, and
# "seemed like it took her awhile to look up"). The check read /v1/state - the player's heaviest answer, slow with the
# player at 100 % - and counted 12 new frames, while a pack's player paints at IDLE_FPS (2 a second) whenever no Wall
# reads it as watching (Setup's window over the desk Wall; the desk Wall reading at rest): 12 frames there is 6 s of
# the 8 s cap before the first slow answer. Now it reads /v1/health (the frame count and the clip, nothing else) and
# looks at the picture once a second (/frame.jpg, a reader: the player paints at its full rate at once), so 12 frames
# are half a second of real drawing. `not_key`: any clip but that one (the hub a slide lands on). Kill
# CRISSA_DRAWN_LOOK=0 (round 20's read).
def drawn_look_on() -> bool:
    return os.environ.get("CRISSA_DRAWN_LOOK", "1").strip().lower() not in ("0", "off", "false", "no")


def _player_get(path: str, timeout: float = 1.0) -> Any:
    base = _CFG.get("base") or ""
    if not base:
        return None
    try:
        req = urllib.request.Request(base + path, headers={"User-Agent": "crissa-init"})
        with crissa_egress.urlopen(req, timeout, what="init: the player's picture") as r:
            raw = r.read()
        return json.loads(raw.decode() or "{}") if path.startswith("/v1/") else raw
    except Exception:  # noqa: BLE001
        return None


def _player_frame_now() -> tuple[str, Any]:
    """(the clip the player is on, its monotonic frame count) from the light read, else /v1/state's."""
    if drawn_look_on():
        h = _player_get("/v1/health")
        if isinstance(h, dict) and "frames" in h:
            return str(h.get("now") or ""), h.get("frames")
    st = _player_now()
    return str(st.get("now") or ""), st.get("frame_no")


def wait_drawn(key: str, frames: int = 12, cap_s: float = 8.0, *, not_key: str = "") -> bool:
    """True once the player is on `key` (or, with not_key, on any clip but that one) and has moved `frames` frames on
    it (her picture is up, not just asked for)."""
    t0 = time.time()
    first, on = None, ""
    looked = 0.0
    polls = max(1, int(round(cap_s / 0.2)))                    # time AND polls: a no-op _sleep never spins
    while time.time() - t0 < cap_s and polls > 0:
        polls -= 1
        if drawn_look_on() and time.time() - looked >= 1.0:
            looked = time.time()
            _player_get("/frame.jpg")                          # a reader: the player paints at its full rate
        now, n = _player_frame_now()
        hit = (now and now != not_key) if not_key else (now == key)
        if hit:
            if first is None or now != on:
                first, on = n, now
            elif isinstance(n, int) and isinstance(first, int) and n - first >= frames:
                return True
        _sleep(0.2)
    return False


def handoff(run_id: str, name: str = "") -> None:
    """The end of Setup's first boot (LILA HOLDS THE SCREEN): Lila introduces Crissa, leaves, Crissa opens."""
    if lines_on():
        line = handoff_words(name)
        print("[init] Lila: %s" % line, flush=True)
        if _HOST["on"]:
            ran, _ = speak_turn(lambda: say(line, voice="lila"), what="Lila's handoff")   # in its turn (round 18)
            if ran:
                _wait_said(line)
        host_leave(run_id, wait=True)
    _land_on_listen()
    if lines_on():
        drawn = wait_drawn(_landing_key())
        print("[init] Crissa: %s%s" % (OPENING_LINE, "" if drawn else " (her loop not seen drawn in time)"),
              flush=True)
        speak_turn(lambda: say(OPENING_LINE, voice="crissa"), what="Crissa's opening")


def host_welcome(run_id: str) -> bool:
    """Lila opens a first boot (lila-ob-welcome), then slides back for Connect. Not for a step run again from the menu."""
    st = load_state()
    if st.get("only") or not st.get("first_boot"):
        return False
    key = host_keys()["welcome"]
    if not key or not lines_on():
        return False
    _HOST["gen"] += 1
    _spawn("lila-welcome", _host_run, run_id, "", [key], _HOST["gen"], True)
    return True


def _host_leave_now() -> None:
    drop_lila_cues("her plate is left")                       # R45-1: nothing queued walks her back on
    if _HOST["on"] and host_keys()["leave"] and not office_now() and _host_play(host_keys()["leave"], False):
        _sleep(clip_seconds(host_keys()["leave"]))
    _HOST["on"] = False


def host_leave(run_id: str, wait: bool = False) -> None:
    """She slides back off her plate (when she is on it)."""
    if not _HOST["on"]:
        return
    _HOST["gen"] += 1
    if wait or SYNC:
        _host_leave_now()
    else:
        _spawn("lila-leave", _host_leave_now)


def host_stop() -> None:
    """A cancel: no more of her lines; the landing cuts to the listening loop."""
    _HOST["gen"] += 1
    _HOST["on"] = False


def _said(io: CardIO, m: dict[str, Any], what: str, key: str, wanted=None) -> None:
    """Say `key` once per card (a mark picked twice does not repeat her line). `wanted`: ONE VOICE AT A TIME."""
    if not key or what in (m.get("said") or []):
        return
    io.put(said=list(m.get("said") or []) + [what])
    host_say(getattr(io, "run_id", ""), str(getattr(io, "sid", "")), [key], wanted=wanted)


def _card_now(run_id: str, key: str) -> dict[str, Any]:
    """The step dict `key` of this run while it is on that step, else {} (a tied line's check, ONE VOICE AT A TIME)."""
    st = load_state()
    if st.get("status") != "running" or st.get("run_id") != run_id or step_cfg(str(st.get("step_id") or "")).get(
            "kind") != key:
        return {}
    return dict(st.get(key) or {})


def room_line_wanted(run_id: str, mark: str = "", state: str = "marks"):
    """A Your room line is wanted while the card is on `state` and, for a mark's line, still asking that mark and the
    person has not set it; the intro (mark "" with `first`) only before any mark is set."""
    def ok(_key: str = "") -> bool:
        m = _card_now(run_id, "room")
        if str(m.get("state") or "") != state:
            return False
        if state != "marks":
            return True
        changed = list(m.get("changed") or [])
        if not mark:
            return not changed
        return str(m.get("now") or "") == mark and mark not in changed
    return ok


# ── YOUR ROOM (the User 2026-10-11): the clock, then the wave ───────────────────────────────────────────────────────────
#     marks       the clock (the camera is its middle, you are at 6): your seat, the speakers (an hour,
#                 or this computer), the door (the camera is the middle: never asked); a tap on an hour sets the mark being set and moves
#                 on to the next; every mark starts at its default (or what was saved before)
#     wave        "Stand in the doorway and wave your right hand. Is that wave on the right of the picture?" YES / NO
#     wave_again  after a No: "Then the picture is mirrored. I've noted it. Wave once more." YES / NO
#     saved       written where the house's own Init writes it (room_save); skipped: nothing written
ROOM_ORDER = ("seat", "speakers", "camera", "door")
# WHAT THE ROOM ASKS DEPENDS ON THE CAMERA (the User 2026-10-12, .101 fresh install: "camera = agent eyes"; "if laptop or
# fixed camera then it must be at the User and always in center"; "No need for the question ever when the door is
# labeled and the camera always in the center of the dial"). The camera is the dial's centre, so its mark is never
# asked (its saved value or default stands). A camera that turns (a motor gimbal) is asked your seat, the speakers and
# the door, then the wave; a fixed camera (a laptop's, a webcam's crop-only pan) or none is asked the door alone, and
# no wave. The kind is known before the first question: the camera's own grabber (/health camera_ptz: motor | digital |
# none), else presence's camera-kind.json, else fixed. CRISSA_ROOM_CAMERA=turns|fixed names it; kill
# CRISSA_ROOM_BY_CAMERA=0 (all four marks and the wave, as before).
# YOU ARE AT 6 (the User 2026-10-26, the house's clock: "Crissa is the middle of the clock. You are at 6 o'clock", Lila's
# 09-14 brief in config/init-sequence.json; house-id room_frame.py "On THIS clock you sit at 6"): the seat is the clock's
# definition, never a question, and the camera is its middle. Every turn is from the camera's rest facing you.
USER_HOUR = 6
ROOM_ASKED = {"turns": ("speakers", "door"), "fixed": ("speakers", "door"), "none": ("speakers", "door"),
              "all": ("speakers", "door")}   # never the seat (you are at 6), never the camera (it is the middle)
ROOM_INTRO = {"many": "Hi, I'm Lila. I set things up for Crissa, the one you'll be talking with. A few quick questions "
                      "about your room.",
              "one": "Hi, I'm Lila. I set things up for Crissa, the one you'll be talking with. Just one quick "
                     "question about your room."}
ROOM_DEFAULTS = {"seat": USER_HOUR, "speakers": 0}   # no door (one room's fact, unset until marked); no camera: it is the middle
ROOM_LABELS = {"seat": "Your seat", "speakers": "Speakers", "camera": "Camera", "door": "Door"}
ROOM_ASKS = {"speakers": "Tap the hour the speakers sit at, or This computer when the sound comes from it.",
             "door": "Tap the hour of the door."}
PICTURE_WORDS = {"true_handed": "your right hand is on the right of the picture", "mirrored": "the picture is mirrored"}
# ONE LINE UP FRONT, TWO CLICKS (the User 2026-10-14, .101 round 18: "'now label the main sound source and door location
# based on this diagram' should be enough for that screen. I found she fell behind ... Just those two clicks for that
# screen so make it short and all up front. If they struggle maybe give one more prompt to clarify camera placement is
# Crissa and sound source is for filtering out media"). Where the room asks only the speakers and the door (a fixed
# camera, or none), Lila says ONE line as the card opens - no intro, no line per mark - and the card finishes by itself
# once both are set (ROOM_ONE_DONE_S after the second click, so its mark shows lit). If either is still unset
# ROOM_NUDGE_S after the card opened, she says one more line, once. A turning camera keeps round 18's lines (its seat
# and the wave). The words live in the walk's `one_line` (say / nudge). Kill CRISSA_ROOM_ONE_LINE=0 (round 18's intro,
# a line per mark and the Save key); CRISSA_ROOM_NUDGE_S=0 (no second line).
ROOM_ONE_LINE = {"say": "Now label the main sound source and door location based on this diagram.",
                 "nudge": "The camera in the middle is Crissa. The sound source is where media plays, like a TV or "
                          "speakers, so she can filter it out. Then mark your door."}
ROOM_ONE_KINDS = ("fixed", "none")
ROOM_NUDGE_S = float(os.environ.get("CRISSA_ROOM_NUDGE_S", "25"))
ROOM_ONE_DONE_S = float(os.environ.get("CRISSA_ROOM_ONE_DONE_S", "1.2"))


def site_path() -> Path:
    return identity_root() / "site.json"


def _hour(v: Any, low: int = 1) -> int | None:
    try:
        h = int(v)
    except (TypeError, ValueError):
        return None
    return h if low <= h <= 12 else None


def room_saved() -> dict[str, Any]:
    """What site.json holds now for the card: {"marks", "picture"} (absent values are left out)."""
    d = _load_json(site_path())
    stn = d.get("stations") if isinstance(d.get("stations"), dict) else {}
    head = stn.get("heading") if isinstance(stn.get("heading"), dict) else {}
    marks: dict[str, int] = {}
    for mark, key in (("door", "door_hour"),):
        h = _hour(head.get(key))
        if h is not None:
            marks[mark] = h
    if d.get("media_mode") == "internal":
        marks["speakers"] = 0
    elif d.get("media_mode") == "external" and _hour(d.get("media_station")) is not None:
        marks["speakers"] = int(d["media_station"])
    cam = d.get("camera") if isinstance(d.get("camera"), dict) else {}
    pic = str(cam.get("orientation") or "")
    return {"marks": marks, "picture": pic if pic in PICTURE_WORDS else ""}


def room_cfg() -> dict[str, Any]:
    cfg = step_cfg("room")
    lines = cfg.get("lines") if isinstance(cfg.get("lines"), dict) else {}
    defaults = dict(ROOM_DEFAULTS)
    for k, v in (cfg.get("defaults") or {}).items():
        if k in ROOM_LABELS and k != "door" and (_hour(v, 0 if k == "speakers" else 1) is not None):
            defaults[k] = int(v)
    return {"lines": {k: str(v) for k, v in lines.items()}, "defaults": defaults}


def room_camera_kind() -> str:
    """'turns' | 'fixed' | 'none' | 'all' (the switch off): see WHAT THE ROOM ASKS DEPENDS ON THE CAMERA."""
    if os.environ.get("CRISSA_ROOM_BY_CAMERA", "1").strip().lower() in ("0", "off", "false", "no"):
        return "all"
    if camera_off():
        return "none"
    named = str(os.environ.get("CRISSA_ROOM_CAMERA") or "").strip().lower()
    if named in ("turns", "fixed"):
        return named
    try:
        req = urllib.request.Request(camera_url() + "/health", headers={"User-Agent": "crissa-init"})
        with crissa_egress.urlopen(req, 2.0, what="init: the camera's kind") as r:
            ptz = str((json.loads(r.read().decode() or "{}") or {}).get("camera_ptz") or "").strip().lower()
        if ptz in ("motor", "digital", "none"):
            return "turns" if ptz == "motor" else "fixed"
    except Exception:  # noqa: BLE001  no grabber answer: the presence record, else fixed
        pass
    rec = _load_json(_home() / "presence" / "camera-kind.json")
    if crissa_home.kind_turns(rec):                # round 39 (N14): a digital pan's measured move is not a turn
        return "turns"
    return "fixed"


def room_order(kind: str) -> tuple:
    return ROOM_ASKED.get(kind) or ROOM_ASKED["fixed"]


def room_one_line_on() -> bool:
    return os.environ.get("CRISSA_ROOM_ONE_LINE", "1").strip().lower() not in ("0", "off", "false", "no")


def room_one_line_words() -> dict[str, str]:
    cfg = step_cfg("room").get("one_line")
    cfg = cfg if isinstance(cfg, dict) else {}
    return {k: str(cfg.get(k) or v) for k, v in ROOM_ONE_LINE.items()}


def _room_all_set(m: dict[str, Any]) -> bool:
    return set(_room_order_of(m)) <= set(m.get("changed") or [])


def _room_nudge(io: CardIO, rid: str, sid: str) -> None:
    """One more line, once, while a mark is still unset ROOM_NUDGE_S after the card opened (one-line card only)."""
    if ROOM_NUDGE_S <= 0:
        return
    time.sleep(ROOM_NUDGE_S)

    def still(_key: str = "") -> bool:
        m = _card_now(rid, "room")
        return str(m.get("state") or "") == "marks" and not _room_all_set(m)
    if not still():
        return
    print("[init] Your room: a mark still unset after %.0f s, one more line" % ROOM_NUDGE_S, flush=True)
    io.put(said=list((io.get() or {}).get("said") or []) + ["nudge"])
    host_say(rid, sid, [SAY_PREFIX + room_one_line_words()["nudge"]], wanted=still)


def _room_one_done(io: CardIO, rid: str) -> None:
    """Both marks set on the one-line card: it finishes by itself, once its last mark has shown lit."""
    if ROOM_ONE_DONE_S > 0 and not SYNC:
        time.sleep(ROOM_ONE_DONE_S)
    m = io.get() or {}                           # a Save or Skip in the meantime moved it off "marks": nothing to do
    if str(m.get("state") or "") != "marks" or not _room_all_set(m):
        return
    print("[init] Your room: both marks set, saved by itself", flush=True)
    _room_done(io, m)


def room_enter(io: CardIO) -> None:
    saved = room_saved()
    marks = dict(room_cfg()["defaults"], **saved["marks"])
    kind = room_camera_kind()
    order = list(room_order(kind))
    one = room_one_line_on() and kind in ROOM_ONE_KINDS
    turns = kind == "turns" and not camera_off()
    if one_page_on():
        # ONE-PAGE SETUP: only the sound and the door are asked (the User 2026-10-15, the mock), whatever the camera.
        # A camera that turns is then shown the door (THE DOOR IS SHOWN, NOT GUESSED, below).
        kind, order, one = "fixed", list(room_order("fixed")), True
        if all(m in saved["marks"] for m in order) and not ids_redo_wants("room") and not (turns and door_aim_wanted()):
            print("[init] Your room: already saved (%s)" % ", ".join(order), flush=True)
            io.put(state="saved", marks=marks, order=order, kind=kind, already=True, one_line=True)
            io.done()
            return
    print("[init] Your room: the camera %s, asking %s" % ({"turns": "turns", "fixed": "is fixed", "none": "is off",
                                                          "all": "(by the old rule)"}.get(kind, kind),
                                                         ", ".join(order) + (" and the wave" if kind in ("turns", "all")
                                                                             else "")), flush=True)
    io.put(state="marks", marks=marks, now=order[0], order=order, kind=kind, changed=[], picture=saved["picture"],
           said=["one_line"] if one else [order[0]], error="", saved=sorted(saved["marks"]), one_line=one,
           turns=turns)
    if one:
        rid, sid = getattr(io, "run_id", ""), str(getattr(io, "sid", ""))
        host_say(rid, sid, [SAY_PREFIX + room_one_line_words()["say"]], wanted=room_line_wanted(rid))
        if ROOM_NUDGE_S > 0 and not SYNC and not one_page_on():   # the one page has her own "Take your time"            # a timer of its own, never inline (tests call _room_nudge)
            threading.Thread(target=_room_nudge, args=(io, rid, sid), name="init-room-nudge", daemon=True).start()
        return
    # SHE INTRODUCES HERSELF FIRST (the User 2026-10-12: "maybe an intro here"): her own voice, then the first mark's line
    lines = room_cfg()["lines"]
    which = "one" if len(order) == 1 else "many"
    cfg_intro = step_cfg("room").get("intro")
    intro = str((cfg_intro.get(which) if isinstance(cfg_intro, dict) else "") or ROOM_INTRO[which])
    seq = ([SAY_PREFIX + intro] if room_intro_on() else []) + ([lines[order[0]]] if lines.get(order[0]) else [])
    if seq:
        rid = getattr(io, "run_id", "")
        first = room_line_wanted(rid, order[0])
        intro_ok = room_line_wanted(rid)
        host_say(rid, str(getattr(io, "sid", "")), seq,
                 wanted=lambda k: (intro_ok(k) and first(k)) if str(k).startswith(SAY_PREFIX) else first(k))


def room_intro_on() -> bool:
    return os.environ.get("CRISSA_ROOM_INTRO", "1").strip().lower() not in ("0", "off", "false", "no")


# THE SPEAKERS' "INTERNAL" TICK (round 17, the User 2026-10-13, .101 round 16: the "This computer" click was taken -
# init-run.json changed ["speakers"], site.json media_mode internal, marks.speakers 0 - but the card showed nothing,
# so it "appears to do nothing"; speakers at 0 have no hour on the dial). The User's design: the computer and the door
# are equals. When the sound comes from this computer, a small tick sits by her at the dial's centre (the camera, the
# screen and the speakers are one place there), labelled "Internal": a checkbox, checked once this computer is the
# answer (taken now, or saved by an earlier Setup), lit once it is clicked here, as the door's mark is lit; while the
# speakers are being asked it is ringed like the door's hour. A click on it is THIS COMPUTER (verb "internal" is
# "computer"; the desk Wall posts init2_rm_internal). Kill: CRISSA_ROOM_INTERNAL_TICK=0 (no tick: round 16's card).
INTERNAL_LABEL = "Internal"


def room_internal_on() -> bool:
    return os.environ.get("CRISSA_ROOM_INTERNAL_TICK", "1").strip().lower() not in ("0", "off", "false", "no")


def room_internal(m: dict[str, Any], marks: dict[str, Any]) -> dict[str, Any] | None:
    """The tick's state for the dial, or None (switched off, or the speakers are not asked on this card)."""
    if not room_internal_on() or "speakers" not in _room_order_of(m):
        return None
    changed = list(m.get("changed") or [])
    answered = "speakers" in changed or "speakers" in (m.get("saved") or [])
    checked = marks.get("speakers") in (0, None) and answered
    return {"label": INTERNAL_LABEL, "checked": bool(checked), "lit": bool(checked and "speakers" in changed),
            "asking": str(m.get("now") or "") == "speakers"}


def _room_order_of(m: dict[str, Any]) -> list:
    got = [x for x in (m.get("order") or []) if x in ROOM_ORDER]
    return got or list(ROOM_ORDER)


def _room_next_mark(mark: str, order: list | None = None) -> str:
    order = list(order or ROOM_ORDER)
    i = order.index(mark) if mark in order else -1
    return order[i + 1] if 0 <= i < len(order) - 1 else ""


def _room_move_to(io: CardIO, m: dict[str, Any], mark: str) -> None:
    io.put(now=mark)
    if m.get("one_line"):
        return                                   # one line up front (2026-10-14): no line per mark
    _said(io, dict(m, now=mark), mark, room_cfg()["lines"].get(mark, ""),
          wanted=room_line_wanted(getattr(io, "run_id", ""), mark))


def room_act(io: CardIO, verb: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    m = io.get() or {}
    state, verb = str(m.get("state") or ""), str(verb or "").strip().lower()
    lines = room_cfg()["lines"]
    if verb == "skip" and state in ("marks", "wave", "wave_again"):
        io.put(state="skipped")
        io.done()
        return {"ok": True, "verb": verb}
    if state == "aim":
        return door_aim_act(io, m, verb)
    if verb in ("skip", "continue") and state == "mouths":
        _coach_post("/cancel", {}, 4.0)
        _lip_sync_said(io, skipped="her lines were stopped")
        io.put(state="saved")
        io.done()
        return {"ok": True, "verb": verb}
    if state == "marks":
        marks = dict(m.get("marks") or {})
        order = _room_order_of(m)
        if verb == "internal":
            verb = "computer"              # the dial's Internal tick is THIS COMPUTER (round 17)
        if verb in ("hour", "computer"):
            mark = str(body.get("mark") or m.get("now") or "")
            if verb == "computer":
                mark = "speakers"          # THIS COMPUTER is the speakers' answer; the glass key posts no mark (round 16)
            if mark not in order:
                return {"ok": False, "error": "Pick which mark to set first."}
            h = 0 if verb == "computer" else _hour(body.get("hour"))
            if h is None or (h == 0 and mark != "speakers"):
                return {"ok": False, "error": "An hour on the clock is 1 to 12."}
            if mark == "door" and h in door_unreachable(bool(m.get("turns")) and not camera_off()):
                return {"ok": False, "error": "The camera cannot turn to %d. Pick the nearest hour it can turn to." % h}
            marks[mark] = h
            changed = sorted(set(m.get("changed") or []) | {mark})
            io.put(marks=marks, changed=changed, error="")
            if mark == (m.get("now") or ""):
                nxt = _room_next_mark(mark, order)
                if nxt:
                    _room_move_to(io, io.get() or {}, nxt)
                else:
                    io.put(now="")
            if m.get("one_line") and set(order) <= set(changed):
                _spawn("room-one-done", _room_one_done, io, getattr(io, "run_id", ""))
            return {"ok": True, "verb": verb, "mark": mark, "hour": h}
        if verb == "pick":
            mark = str(body.get("mark") or "")
            if mark not in order:
                return {"ok": False, "error": "no such mark: %s" % mark}
            _room_move_to(io, m, mark)
            return {"ok": True, "verb": verb, "mark": mark}
        if verb in ("next", "save"):
            if verb == "save" or camera_off() or str(m.get("kind") or "all") not in ("turns", "all"):
                return _room_done(io, m)                 # a fixed camera has no wave to ask (the door label says it)
            io.put(state="wave", now="", error="")
            _said(io, io.get() or {}, "wave", lines.get("wave", ""),
                  wanted=room_line_wanted(getattr(io, "run_id", ""), state="wave"))
            return {"ok": True, "verb": verb}
    if state in ("wave", "wave_again"):
        if verb == "yes":
            io.put(picture="true_handed")
            return _room_done(io, io.get() or {})
        if verb == "no" and state == "wave":
            # the house's own rule (lib/init_wizard camera_orientation): a first No is noted, and the wave once more
            io.put(state="wave_again", picture="mirrored")
            _said(io, io.get() or {}, "mirrored", lines.get("mirrored", ""),
                  wanted=room_line_wanted(getattr(io, "run_id", ""), state="wave_again"))
            return {"ok": True, "verb": verb}
        if verb == "no":
            io.put(picture="mirrored")
            return _room_done(io, io.get() or {})
        if verb in ("save", "skip_wave"):
            return _room_done(io, m)
        if verb == "back":
            io.put(state="marks", now=_room_order_of(m)[0])
            return {"ok": True, "verb": verb}
    return {"ok": False, "error": "%s is not an answer to Your room now (%s)" % (verb or "nothing", state or "-")}


def _room_done(io: CardIO, m: dict[str, Any]) -> dict[str, Any]:
    if m.get("turns") and "aimed" not in m and door_aim_on() and _hour((m.get("marks") or {}).get("door")) is not None:
        return door_aim_enter(io, m)              # THE DOOR IS SHOWN, NOT GUESSED: the camera turns to it first
    try:
        room_save(m.get("marks") or {}, str(m.get("picture") or ""), door_pose=m.get("door_pose"))
    except OSError as e:
        io.put(error="Your room could not be saved: %s" % e)
        return {"ok": False, "error": str(e)}
    if team_end_on():
        # THE TEAM AT THE END (round 18): her voice and the announcer's are recorded as they introduce themselves
        print("[init] her voice through the speakers: at Setup's end, as the team introduces itself", flush=True)
    elif mouths_on():
        want, why = mouths_wanted()
        print("[init] her voice through the speakers: %s (%s)" % ("recording" if want else "not needed", why),
              flush=True)
        if want:
            io.put(state="mouths")
            _spawn("room-mouths", _room_mouths, io)
            return {"ok": True, "saved": True, "mouths": True}
        _lip_sync_said(io, skipped="her voice through the speakers was not needed: %s" % why)
    else:
        _lip_sync_said(io, skipped="her voice through the speakers is off here (%s)" % (
            "the cues are text only" if cues_mode() == "text" else "CRISSA_SETUP_MOUTHS=0"))
    io.put(state="saved")
    io.done()
    return {"ok": True, "saved": True}


# HER OWN VOICE THROUGH THE SPEAKERS (the User 2026-10-13, .101 round 15: the User's lines were dropped as hers, and the
# ear had no print of her voice as this room hears it). Once Your room has the speaker answer (and the machine scan has
# picked her voice), Crissa and the announcer each say a few short lines out loud while the microphone records, and the
# coach saves them as the ear's own-voice prints (crissa_her_tts, announcer: mouth_prints in the id module). One line on the
# plate says what is happening; nothing to do; the card moves on when it is done (or after MOUTHS_TIMEOUT_S, or with no
# coach). Only when the ear's report asks for them (mouths_wanted: none in use, or made with another voice or speaker),
# so Register or a later Setup can run it again. Never with the cues text-only (a quiet house).
# Kill: CRISSA_SETUP_MOUTHS=0; =always records them every time.
MOUTHS_TIMEOUT_S = 120.0


def mouths_on() -> bool:
    if os.environ.get("CRISSA_SETUP_MOUTHS", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return cues_mode() != "text" and not crissa_home.is_house()


# HER VOICE IS HEARD, NOT SEEN, BEFORE SHE IS INTRODUCED (round 17, the User 2026-10-13, .101 round 16: Crissa was on
# screen for this step, before her intro, and her lips did not move on her lines). Lila holds the screen on her silent
# loop through it (lila_holding), and the six lines stay plain audio from the coach (mouth_prints: rendered, played on
# the sink and recorded around each line), never the player's: a line through the player would draw a mouth on
# whoever is on the glass - Lila mouthing Crissa's voice, or Crissa before Lila has introduced her - and would move the
# record window off the line's own start. The card says she is heard and met in a moment. Kill:
# CRISSA_SETUP_MOUTHS_HEARD=0 (round 16's words).
def mouths_heard_on() -> bool:
    return os.environ.get("CRISSA_SETUP_MOUTHS_HEARD", "1").strip().lower() not in ("0", "off", "false", "no")


# A FIRST BOOT TIMES HER LINES FOR LIP SYNC (round 45, .101 round 44: "Setup took no lip-sync measure on this first
# boot" - "[init] lip sync, the glass as she is seen now: no Setup measurement to redo"). Her timing lines at Setup's
# end were gated on her voice prints alone (mouths_wanted): prints that hold left no lip-sync measurement on this
# machine at all, and her walk-in had nothing to re-measure on the glass. Now they also play when this machine has no
# measurement that came out (lip-sync-setup.json missing, or its outcome none). Kill CRISSA_LIPSYNC_MEASURE_WANTED=0.
def lip_measure_wanted() -> tuple[bool, str]:
    if os.environ.get("CRISSA_LIPSYNC_MEASURE_WANTED", "1").strip().lower() in ("0", "off", "false", "no"):
        return False, "off"
    try:
        import crissa_lipsync
        if not crissa_lipsync.measure_on():
            return False, "lip sync measuring is off"
        note = crissa_lipsync.setup_note(_home())
    except Exception:  # noqa: BLE001
        return False, "no lip sync module"
    if not note:
        return True, "this computer has no lip sync measurement yet"
    if note.get("outcome") not in ("set", "kept", "measured"):
        return True, "its last lip sync measurement came to nothing (%s)" % (note.get("why") or note.get("outcome"))
    return False, "lip sync measured"


def mouths_or_lip_wanted() -> tuple[bool, str]:
    """mouths_wanted, or a lip-sync measurement this machine still needs (A FIRST BOOT TIMES HER LINES)."""
    want, why = mouths_wanted()
    if want:
        return want, why
    lw, lwhy = lip_measure_wanted()
    return (True, "%s; %s" % (why, lwhy)) if lw else (False, why)


def mouths_wanted() -> tuple[bool, str]:
    """(record them?, why) from the ear's own report (<home>/ear/her-prints.json, crissa_ear.her_prints, round16-ear):
    wanted when it asks (a print stale, or none in use), when there is no report yet, or when the voice tier record is
    newer than it or names another tier or announcer (the scan may have just picked one, and the ear has not had a turn
    since). CRISSA_SETUP_MOUTHS=always records them every time."""
    if os.environ.get("CRISSA_SETUP_MOUTHS", "1").strip().lower() == "always":
        return True, "always"
    rp = _home() / "ear" / "her-prints.json"
    rep = _load_json(rp)
    if not rep:
        return True, "no report from the ear yet"
    if rep.get("ask"):
        return True, "the ear asks (%s)" % ("%d stale" % len(rep.get("stale") or []) if rep.get("stale") else
                                            "no print in use")
    tp = _home() / "voice-tier.json"
    tier = _load_json(tp)
    try:
        newer = tp.is_file() and tp.stat().st_mtime > float(rep.get("at") or 0)
    except (OSError, TypeError, ValueError):
        newer = True
    ctx = rep.get("context") if isinstance(rep.get("context"), dict) else {}
    t_now = str(tier.get("tier") or tier.get("main") or "")
    a_now = str(tier.get("announcer") or t_now)
    if t_now and (t_now != str(ctx.get("tier") or "") or a_now != str(ctx.get("announcer") or "")):
        return True, "her voice is now %s" % t_now
    if newer:
        return True, "the voice tier changed since the ear's report"
    return False, "her prints hold (%d in use)" % int(rep.get("used") or 0)


# LIP SYNC, SAID ONCE (round 17, the User 2026-10-13: "yes have setup measure the lip sync delay"). The six lines also
# time this machine's speakers, and the coach sets the Lip sync slider from them (mouth_prints; lib/crissa_lipsync
# from_setup, never over the person's own value). This log says what came of it, once, and the last plate carries the
# line ("Lip sync set to -125 ms for these speakers."). When the lines do not play or do not finish, the slider is left
# as it is and the log says so. Kill: CRISSA_SETUP_LIPSYNC_MEASURE=0 (nothing measured, nothing said).
def _glass_now() -> dict | None:
    """The player's glass lag now (the coach's own read: the desk Wall's when it reports, else the freshest)."""
    g = _player_get("/v1/glass-lag", 1.5)
    g = (g or {}).get("glass_lag") if isinstance(g, dict) else None
    fresh = {k: v for k, v in (g or {}).items() if isinstance(v, dict) and v.get("fresh") and v.get("median_ms") is not None}
    if not fresh:
        return None
    who = "wall-tk" if "wall-tk" in fresh else min(fresh, key=lambda k: float(fresh[k].get("age_s") or 0.0))
    return {"who": who, "median_ms": fresh[who]["median_ms"], "p90_ms": fresh[who].get("p90_ms")}


def _lip_sync_full_wall(run_id: str) -> dict:
    """Round 35, THE GLASS AS SHE WILL BE SEEN (lib/crissa_lipsync.reglass): after her walk-in, before her opener."""
    try:
        import crissa_lipsync
        if crissa_home.is_house() or not crissa_lipsync.full_wall_on() or not crissa_lipsync.measure_on():
            return {}
        rep = crissa_lipsync.reglass(_glass_now(), _home())
        rg = rep.get("reglass") or {}
        print("[init] lip sync, the glass as she is seen now: %s (glass %s ms, was %s ms behind Setup's window)"
              % (rep.get("log") or rep.get("why") or rep.get("outcome"), rg.get("now"), rg.get("was")), flush=True)
        return rep
    except Exception as e:  # noqa: BLE001  her opener never waits on this
        print("[init] lip sync, the glass now: %s: %s" % (type(e).__name__, e), flush=True)
        return {}


def _lip_sync_said(io: CardIO, since: float | None = None, skipped: str = "") -> None:
    try:
        import crissa_lipsync
        if crissa_home.is_house() or not crissa_lipsync.measure_on():
            return
        stays = crissa_lipsync.said_ms(crissa_lipsync.offset_ms(_home()))
        if skipped:
            print("[init] Lip sync not measured (%s); it stays at %s." % (skipped, stays), flush=True)
            return
        note = crissa_lipsync.setup_note(_home(), since=since)
        if not note:
            print("[init] Lip sync not measured (the coach did not report it); it stays at %s." % stays, flush=True)
            return
        print("[init] %s" % str(note.get("log") or note.get("outcome")), flush=True)
        if note.get("outcome") in ("set", "kept", "measured") and note.get("line"):   # round 36: the kit reports
            io.put(lip_sync=str(note["line"]))
    except Exception as e:  # noqa: BLE001  the room is saved whatever this says
        print("[init] lip sync: %s: %s" % (type(e).__name__, e), flush=True)


def _lip_measure_from(t: float) -> None:
    """Her timing lines start: the window the re-measure's set-on glass is the median of (crissa_lipsync)."""
    try:
        import crissa_lipsync
        crissa_lipsync.mark_measure_start(t)
    except Exception:  # noqa: BLE001  the snapshot stands
        pass


def _room_mouths(io: CardIO) -> None:
    asked = time.time()                          # the coach's lip sync note is newer than this (_lip_sync_said)
    _lip_measure_from(asked)                    # the hotfix: the set-on glass is this window's
    try:
        ok, why = _coach_ready()
        if not ok:
            print("[init] her voice through the speakers: skipped (%s)" % why, flush=True)
            _lip_sync_said(io, skipped="her lines did not play: %s" % why)
            return
        got = _coach_post("/start", {"mode": "mouths", "person_id": "crissa_her_tts", "create": False, "station": "desk"},
                          20.0)
        if not got.get("_ok") or got.get("ok") is False:
            print("[init] her voice through the speakers: the coach said %s" % str(got.get("error") or
                                                                                   got.get("_error"))[:160], flush=True)
            _lip_sync_said(io, skipped="her lines did not play")
            return
        t0, seen, n = time.time(), False, 0
        polls = max(1, int(round(MOUTHS_TIMEOUT_S / POLL_S)))  # time AND polls: a no-op _sleep never spins
        while time.time() - t0 < MOUTHS_TIMEOUT_S and n < polls:
            n += 1
            if (io.get() or {}).get("state") != "mouths":
                return                                   # Skip pressed, or the walk moved on
            sess = _coach_get("/session", 3.0)
            running = bool(sess.get("running")) if sess.get("_ok") else seen
            seen = seen or running
            if sess.get("_ok") and not running and (seen or time.time() - t0 > 4.0 or n * POLL_S > 4.0):
                print("[init] her voice through the speakers: done (rc %s)" % sess.get("last_rc"), flush=True)
                _lip_sync_said(io, since=asked)
                return
            _sleep(POLL_S)
        _coach_post("/cancel", {}, 4.0)
        print("[init] her voice through the speakers: did not finish in %d s" % int(MOUTHS_TIMEOUT_S), flush=True)
        _lip_sync_said(io, skipped="her lines did not finish")
    finally:
        if (io.get() or {}).get("state") == "mouths":
            io.put(state="saved")
            io.done()


# THE DOOR IS SHOWN, NOT GUESSED (the User 2026-10-25, the 1.0.0 stranger install: one-page Setup asked the door's
# hour and nothing more, so a camera that turns rested on THIS office's door pan in a stranger's room - "Maybe Users
# could call me up and ask me to hard code their doors"). On a camera that turns, once the door's hour is marked, the
# camera turns toward where the clock puts it (the angle at the camera between the seat and the door, on the clock's
# circle - a first guess, never saved) and the picture shows; the person turns it with the two keys until the doorway
# is in the middle and says This is my door. That pan, tilt and zoom are the room's door rest (site.json
# stations.heading door_home_*, read by the presence module's room_heading). "My door is out of view" saves no door
# rest: the camera then rests on the seat, as a fixed one does. The camera is leased for the step, so the rest loop
# does not pull it back. Kill: CRISSA_DOOR_AIM=0 (no aim step; the hour alone, as 1.0.0).
DOOR_AIM_WORDS = {
    "ask": "Turn the camera with the two keys until your door is in the middle of the picture, then press This is my "
           "door. If the camera cannot see your door, press My door is out of view.",
    "edge": "That is as far as the camera turns.",
}
DOOR_AIM_STEP_DEG = float(os.environ.get("CRISSA_DOOR_AIM_STEP_DEG", "8"))   # one key press: about a doorway's width


def units_per_deg() -> float:
    """The camera's units per degree from the camera profile the presence module reads (RAIL_CAMERA_PROFILE, else
    modules/presence/config/camera-profile.json; its camera_profile.deg_to_units) - one source, never a second number
    here (audit A3, 2026-10-26). A profile that cannot be read gives the presence module's own product default."""
    raw = os.environ.get("RAIL_CAMERA_PROFILE") or str(REPO / "modules" / "presence" / "config" / "camera-profile.json")
    try:
        v = float((_load_json(Path(raw)) or {}).get("units_per_deg") or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else _PROFILE_DEFAULT_UNITS


_PROFILE_DEFAULT_UNITS = 3600.0   # camera_profile.PRODUCT_DEFAULT["units_per_deg"], only when no profile can be read
_PROFILE_DEFAULT_PAN_LIMIT = 522000.0   # camera_profile.PRODUCT_DEFAULT["pan_limit"], only when no profile can be read


# A TURN IS CLAMPED, NEVER WRAPPED (the User's walk of 1.0.1, 2026-10-26: the door marked at 12 sent a 180-degree first
# guess, the grabber wrapped it modulo 2 x pan_limit and the camera stopped at +110, "it still thinks door is 10"; a
# Turn right past +145 swung it to -140). The presence module's own rule (camera_profile.clamp_pan: "Clamped, never
# wrapped: a wrap swings the camera to the other side"; pan_max = one step inside pan_limit), read from the same
# profile as units_per_deg. Kill CRISSA_DOOR_AIM_CLAMP=0 (the turn sent as asked).
def pan_max_deg() -> float:
    raw = os.environ.get("RAIL_CAMERA_PROFILE") or str(REPO / "modules" / "presence" / "config" / "camera-profile.json")
    try:
        lim = float((_load_json(Path(raw)) or {}).get("pan_limit") or 0)
    except (TypeError, ValueError):
        lim = 0.0
    upd = units_per_deg()
    return ((lim if lim > 0 else _PROFILE_DEFAULT_PAN_LIMIT) - upd) / upd      # one step (one degree) inside the limit


def door_aim_clamp_on() -> bool:
    return os.environ.get("CRISSA_DOOR_AIM_CLAMP", "1").strip().lower() not in ("0", "off", "false", "no")


# AN HOUR THE CAMERA CANNOT TURN TO IS GREYED FOR THE DOOR (the User 2026-10-26: "must grey out 12"): on a camera that
# turns (the room's `turns`: the one page names every camera fixed), an hour whose turn from you (door_guess_deg) is past the camera's own travel (pan_max_deg) is not offered as
# the door - nothing here names 12. An hour is the clock's 30-degree slice of the room (+-15 around its turn), so it is
# greyed only when NONE of its slice is inside the travel: on the Link's +-144, 12 (165-195) is greyed and 11 (135-165,
# his door, seen at the picture's edge) is not. A camera with more travel greys nothing; a fixed camera is never asked
# to turn. Kill CRISSA_DOOR_REACH_GREY=0.
HOUR_DEG = 30.0                  # the clock's own definition: twelve hours round the camera


def door_unreachable(turns: bool) -> list[int]:
    if not turns or not door_aim_on() or \
            os.environ.get("CRISSA_DOOR_REACH_GREY", "1").strip().lower() in ("0", "off", "false", "no"):
        return []
    lim = pan_max_deg()
    return [h for h in range(1, 13) if abs(door_guess_deg({"door": h}) or 0.0) - HOUR_DEG / 2.0 > lim]
DOOR_AIM_LEASE_S = 600.0       # the step's own lease; a Done, Skip or Cancel releases it before


def door_aim_on() -> bool:
    return os.environ.get("CRISSA_DOOR_AIM", "1").strip().lower() not in ("0", "off", "false", "no")


def door_aim_wanted() -> bool:
    """A camera that turns and no door rest measured yet (nor 'out of view' said)."""
    if not door_aim_on():
        return False
    head = ((_load_json(site_path()).get("stations") or {}).get("heading") or {})
    return head.get("door_home_pan") is None and head.get("door_in_view") is not False


def door_guess_deg(marks: dict[str, Any]) -> float | None:
    """The pan (degrees, + = the camera's right) from the camera's rest on the seat to the door. THE CAMERA IS THE
    MIDDLE OF THE CLOCK (the User 2026-10-26: "the camera is of course at the center of the clock"; the house's
    docs/SEAT-GUESS-FROM-MOTION.md and lib/init_v2.py _pose): each hour is 30 degrees from the camera, so the turn is
    (door - seat) * 30, wrapped to +-180 (a door across the room from the seat is 180: past most cameras' travel, the
    edge says so). None only when no door is marked. Kill CRISSA_DOOR_GUESS_CENTRE=0 (the old rim geometry)."""
    seat, door = USER_HOUR, _hour(marks.get("door"))            # you are at 6: a saved or tapped seat changes nothing
    if door is None:
        return None
    if os.environ.get("CRISSA_DOOR_GUESS_CENTRE", "1").strip().lower() in ("0", "off", "false", "no"):
        return _door_guess_rim(marks)
    return ((door - seat) * 30.0 + 180.0) % 360.0 - 180.0


def _door_guess_rim(marks: dict[str, Any]) -> float | None:
    """1.0.0's guess (the camera ON the rim at its own hour), kept only for the kill switch."""
    import math
    seat, cam, door = (_hour(marks.get(k)) for k in ("seat", "camera", "door"))
    seat, cam = seat or ROOM_DEFAULTS["seat"], cam or 12
    if door is None or door == cam:
        return None
    pt = lambda h: (math.sin(math.radians(h * 30.0)), math.cos(math.radians(h * 30.0)))   # noqa: E731
    c, s_, d = pt(cam), pt(seat), pt(door)
    deg = math.degrees(math.atan2(s_[1] - c[1], s_[0] - c[0]) - math.atan2(d[1] - c[1], d[0] - c[0]))
    return (deg + 180.0) % 360.0 - 180.0


def _camera_call(path: str, body: dict[str, Any] | None = None, timeout: float = 6.0) -> dict[str, Any]:
    url = camera_url() + path
    req = (urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                  headers={"Content-Type": "application/json", "User-Agent": "crissa-init"})
           if body is not None else urllib.request.Request(url, headers={"User-Agent": "crissa-init"}))
    try:
        with crissa_egress.urlopen(req, timeout, what="init: the camera") as r:
            d = json.loads(r.read().decode() or "{}")
            return d if isinstance(d, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read(4000).decode() or "{}")
            return dict(d, ok=False) if isinstance(d, dict) else {"ok": False, "error": "HTTP %d" % e.code}
        except (ValueError, OSError):
            return {"ok": False, "error": "HTTP %d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def _door_aim_to(io: CardIO, m: dict[str, Any], deg: float) -> dict[str, Any]:
    aim = dict(m.get("aim") or {})
    upd = units_per_deg()
    clamped = False
    if door_aim_clamp_on():
        lim = pan_max_deg()
        clamped = abs(deg) > lim
        deg = max(-lim, min(lim, deg))
    pan = int(round(deg * upd))
    got = _camera_call("/aim", {"pan": pan, "tilt": int(aim.get("tilt") or 0), "lease_id": aim.get("lease_id") or ""})
    now = got.get("pan_actual", got.get("pan"))
    try:
        real = float(now) / upd if now is not None else deg
    except (TypeError, ValueError):
        real = deg
    edge = clamped or abs(real - deg) > DOOR_AIM_STEP_DEG / 2.0   # the camera's own end of travel (or it stopped short)
    bad = not got.get("ok", True) and not edge and now is None     # an end of travel is not an error; no answer is
    aim.update(deg=round(real, 1), edge=edge, hint=DOOR_AIM_WORDS["edge"] if edge else "",
               error=str(got.get("error") or "the camera did not answer") if bad else "")
    io.put(aim=aim, error=aim["error"])
    return {"ok": not bad, "deg": aim["deg"], "edge": edge}


def door_aim_enter(io: CardIO, m: dict[str, Any]) -> dict[str, Any]:
    lease = _camera_call("/lease", {"owner": "setup-door", "ttl_sec": DOOR_AIM_LEASE_S})
    guess = door_guess_deg(m.get("marks") or {})
    print("[init] Your room: the camera turns - showing the door (first guess %s deg from the seat)"
          % ("behind the camera" if guess is None else "%+.0f" % guess), flush=True)
    io.put(state="aim", now="", aim={"lease_id": str(lease.get("lease_id") or ""), "tilt": 0, "deg": 0.0})
    _door_aim_to(io, io.get() or {}, 0.0 if guess is None else guess)
    return {"ok": True, "state": "aim"}


def _door_aim_release(m: dict[str, Any]) -> None:
    lid = str((m.get("aim") or {}).get("lease_id") or "")
    if lid:
        _camera_call("/lease/release", {"lease_id": lid}, timeout=3.0)


def door_aim_act(io: CardIO, m: dict[str, Any], verb: str) -> dict[str, Any]:
    aim = m.get("aim") or {}
    if verb in ("left", "right"):
        step = DOOR_AIM_STEP_DEG * (1.0 if verb == "right" else -1.0)
        return _door_aim_to(io, m, float(aim.get("deg") or 0.0) + step)
    if verb == "door_here":
        pose = {"pan": int(round(float(aim.get("deg") or 0.0) * units_per_deg())),   # the camera's own readback
                "tilt": int(aim.get("tilt") or 0)}
        print("[init] Your room: the door is at pan %s (shown by the person)" % pose["pan"], flush=True)
        _door_aim_release(m)
        io.put(aimed=True, door_pose=pose)
        return _room_done(io, io.get() or {})
    if verb in ("door_none", "skip"):
        print("[init] Your room: the door is out of the camera's view - the seat is its rest", flush=True)
        _door_aim_release(m)
        io.put(aimed=False, door_pose=False)
        return _room_done(io, io.get() or {})
    if verb == "back":
        _door_aim_release(m)
        io.put(state="marks", now=_room_order_of(m)[0])
        return {"ok": True, "verb": verb}
    return {"ok": False, "error": "%s is not an answer to the door's picture" % (verb or "nothing")}


def room_save(marks: dict[str, Any], picture: str = "", door_pose: dict[str, Any] | None = None) -> Path:
    """Write the room where the house's own Init writes it (lib/init_wizard _room_clock_commit and camera_orientation;
    lib/init_v2 room): identity/site.json - stations.heading user_hour, door_hour (and door_az_deg, the same clock),
    camera_hour; media_mode / media_station and stations.media_hour for the speakers; camera.orientation (mirrored: and
    hflip_at_grabber, as the house's mirrored answer writes). The dial reads it (lib/vo_sound.room_frame, the presence
    module's door tick)."""
    p = ensure_site()
    d = _load_json(p)
    stn = d.setdefault("stations", {}) if isinstance(d.get("stations", {}), dict) else {}
    d["stations"] = stn
    head = stn.setdefault("heading", {}) if isinstance(stn.get("heading", {}), dict) else {}
    stn["heading"] = head
    head.setdefault("frame", "monitor")
    head.setdefault("twelve", "monitor")
    door = _hour(marks.get("door"))
    head["user_hour"] = USER_HOUR                 # you are at 6: the clock's definition (2026-10-26), never asked
    if door is not None:
        head["door_hour"] = door
        head["door_az_deg"] = 0.0 if door == 12 else float(door) * 30.0
    head.pop("camera_hour", None)                 # the camera is the middle of the clock (2026-10-26): never an hour
    if isinstance(door_pose, dict):
        # the camera's measured door rest (THE DOOR IS SHOWN, NOT GUESSED): what the presence module rests on
        for k in ("pan", "tilt", "zoom"):
            if door_pose.get(k) is not None:
                head["door_home_" + k] = int(door_pose[k])
        head["door_home_by"] = "setup"
    elif door_pose is False:
        for k in ("pan", "tilt", "zoom", "by"):        # out of the camera's view: no door rest (the seat is its rest)
            head.pop("door_home_" + k, None)
        head["door_in_view"] = False
    sp = marks.get("speakers")
    if _hour(sp) is not None:
        d["media_mode"], d["media_station"] = "external", int(sp)
        stn["media_hour"] = int(sp)
    elif sp in (0, "0"):
        d["media_mode"], d["media_station"] = "internal", None
        stn.pop("media_hour", None)
    if picture in PICTURE_WORDS:
        c = d.setdefault("camera", {}) if isinstance(d.get("camera", {}), dict) else {}
        d["camera"] = c
        c["orientation"] = picture
        c["verified"] = _now()[:10]
        if picture == "mirrored":
            c["hflip_at_grabber"] = True
    setup = d.get("setup") if isinstance(d.get("setup"), dict) else {}
    setup["room_card"] = {"at": _now(), "by": "init", "marks": {k: marks.get(k) for k in ROOM_ORDER}, "picture": picture}
    d["setup"] = setup
    _write_json(p, d)
    return p


def room_view(m: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or step_cfg("room")
    state = str(m.get("state") or "")
    rc = room_cfg()
    marks = dict(rc["defaults"], **(m.get("marks") or {}))
    now = str(m.get("now") or "")
    kind = str(m.get("kind") or "all")
    waves = kind in ("turns", "all") and not camera_off()
    v: dict[str, Any] = {"state": state, "title": cfg.get("title") or "Your room", "body": "", "hint": "", "keys": [],
                         "marks": marks, "now": now, "order": _room_order_of(m), "labels": dict(ROOM_LABELS),
                         "user_hour": USER_HOUR,
                         "camera_kind": kind,
                         "asks": dict(ROOM_ASKS), "changed": list(m.get("changed") or []),
                         "picture": str(m.get("picture") or ""), "defaults": rc["defaults"],
                         "camera": "off" if camera_off() else "on", "computer": marks.get("speakers") in (0, None),
                         "door_unreachable": door_unreachable(bool(m.get("turns")) and not camera_off())}
    internal = room_internal(m, marks) if state == "marks" else None
    if internal is not None:
        v["internal"] = internal
    if not camera_off():
        v["camera_url"] = camera_url() + "/snapshot"
    if state == "marks":
        body = str(cfg.get("body") or "")
        if kind in ("fixed", "none"):
            body = str(cfg.get("body_fixed") or "") or ("The clock is your room seen from above: the camera is the middle "
                                                        "of the clock and you are at 6 o'clock. Where does your sound "
                                                        "come from, and where is the door?")
        hint = ROOM_ASKS.get(now) or "Change any mark by picking it, then tapping its hour. Next when it looks right."
        if internal is not None and now == "speakers":
            hint = ("Tap the hour the speakers sit at, or %s, by %s in the middle, when the sound comes from this "
                    "computer." % (INTERNAL_LABEL, crissa_home.her_name()))
        v.update(body=body, hint=hint, keys=[("next", "Next") if waves else ("save", "Save"), ("skip", "Skip")])
    elif state in ("wave", "wave_again"):
        v.update(body=str((cfg.get("wave") or {}).get("ask") if isinstance(cfg.get("wave"), dict) else "")
                 or "Stand in the doorway and wave your right hand. Is that wave on the right of the picture?",
                 hint="Noted: the picture is mirrored. Wave once more to check." if state == "wave_again" else
                 "The camera's picture is on the Setup page in %s's window." % crissa_home.her_name(),
                 keys=[("yes", "Yes, on the right"), ("no", "No, on the left"), ("save", "Save without it"),
                       ("skip", "Skip")])
    elif state == "aim":
        aim = m.get("aim") or {}
        v.update(body=DOOR_AIM_WORDS["ask"], hint=str(aim.get("hint") or ""),
                 keys=[("left", "◀ Turn left"), ("right", "Turn right ▶"), ("door_here", "This is my door"),
                       ("door_none", "My door is out of view")])
        v["aim"] = {"deg": aim.get("deg"), "edge": aim.get("edge")}
    elif state == "mouths":
        if mouths_heard_on():
            name = crissa_home.her_name()
            body = ("You will hear %s, then the announcer, say a few lines through your speakers, so %s knows her own "
                    "voice in this room. You meet her in a moment. Nothing to do." % (name, name))
        else:
            body = ("Crissa is saying a few lines through your speakers so she knows her own voice in this room. "
                    "Nothing to do.")
        v.update(body=body, keys=[("skip", "Skip")])
    elif state == "saved":
        v.update(body="Your room is saved.")
    elif state == "skipped":
        v.update(body="Skipped: the clock keeps its defaults.")
    if m.get("error"):
        v["hint"] = str(m["error"])
    return v


# ── YOUR HOURS (the User 2026-10-11): the overnight window and quiet hours ───────────────────────────────────────────
#     ask     the overnight window (default 18:00 to 02:00) and quiet hours (default none); whole hours; SAVE / SKIP
#     saved   site.json training_window (the house's Init's field, read by lib/house_hours.off_hours) and
#             manners.quiet_hours (house_hours.quiet_hours), and the announcer's own quiet hours in talk.json
#             (lib/crissa_talk announce.quiet_hours, what a pack's announcer keeps quiet by)
HOURS_DEFAULTS = {"overnight": [18, 2], "quiet": None}


def window_words(w: Any) -> str:
    if isinstance(w, (list, tuple)) and len(w) == 2:
        return "%02d:00 to %02d:00" % (int(w[0]), int(w[1]))
    return "none"


def window_text(w: Any) -> str:
    """[h1, h2] -> "HH:00-HH:00" (lib/house_hours's shape)."""
    return "%02d:00-%02d:00" % (int(w[0]), int(w[1]))


def _window(v: Any) -> list[int] | None:
    """[h1, h2] from [h1, h2] or "HH:MM-HH:MM" (minutes dropped); None for none or a bad value."""
    if v in (None, "", [], "none", False):
        return None
    if isinstance(v, str):
        m = re.match(r"^\s*(\d{1,2})(?::\d{2})?\s*-\s*(\d{1,2})(?::\d{2})?\s*$", v)
        v = [m.group(1), m.group(2)] if m else None
    try:
        h1, h2 = int(v[0]), int(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23) or h1 == h2:
        return None
    return [h1, h2]


def hours_saved() -> dict[str, Any]:
    d = _load_json(site_path())
    man = d.get("manners") if isinstance(d.get("manners"), dict) else {}
    out: dict[str, Any] = {}
    if _window(d.get("training_window")):
        out["overnight"] = _window(d.get("training_window"))
    if _window(man.get("quiet_hours")):
        out["quiet"] = _window(man.get("quiet_hours"))
    return out


def hours_cfg() -> dict[str, Any]:
    cfg = step_cfg("hours")
    defs = dict(HOURS_DEFAULTS)
    got = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    if _window(got.get("overnight")):
        defs["overnight"] = _window(got.get("overnight"))
    if "quiet" in got:
        defs["quiet"] = _window(got.get("quiet"))
    return {"defaults": defs, "films": [str(x) for x in (cfg.get("films") or [])]}


# QUICKER THAN HER LINES (round 21, the User 2026-10-14, .101 round 20: he pressed Go on Your hours, and Lila then said
# her 131-character hours line anyway - it had passed its turn's check and was being made when he answered - before
# "We're all set"). When Your hours is answered (saved or skipped) while her hours lines are still to come or in the
# air, the one in the air is stopped, the rest are dropped (their turn's check), and at Setup's end she says
# HOURS_DONE_LINE in place of her handover: "Oh, you already selected, great! That's all here. I'll turn you over to
# Crissa." It only shows when the person is quicker than her. Kill CRISSA_HOURS_DONE_LINE=0 (round 20: the line plays).
HOURS_DONE_SHORT = "Oh, you already selected, great! That's all here."
HOURS_DONE_LINE = HOURS_DONE_SHORT + " I'll turn you over to Crissa."
_HOURS_LINES: dict[str, str] = {}          # run id -> "saying" while her hours lines are still to come, "done" after


def hours_done_line_on() -> bool:
    return os.environ.get("CRISSA_HOURS_DONE_LINE", "1").strip().lower() not in ("0", "off", "false", "no")


def _hours_quicker(io: CardIO) -> bool:
    """Your hours answered while her hours lines were still to come: stop the one in the air, and note it on the card
    (never on the run's `ending`: that key is the walk's "the team is speaking")."""
    rid = getattr(io, "run_id", "")
    if not hours_done_line_on() or _HOURS_LINES.get(rid) != "saying":
        return False
    _HOURS_LINES[rid] = "cut"
    fn = _CFG.get("busy") or _CFG.get("speaking")
    try:
        on = bool(fn()) if callable(fn) else False
    except Exception:  # noqa: BLE001
        on = False
    if on:
        _post("/v1/say", {"stop": True, "by": "init"})        # her hours line in the air: cut (ONE VOICE AT A TIME)
    io.put(early=True)
    print("[init] Your hours: answered before her lines were done; %s" % (
        "her line is cut" if on else "her lines are dropped"), flush=True)
    return True


def hours_enter(io: CardIO) -> None:
    hc = hours_cfg()
    saved = hours_saved()
    if one_page_on() and saved.get("overnight") and not ids_redo_wants("hours"):
        # ONE-PAGE SETUP: hours this install already chose are kept, and Lila says she sees they are done
        print("[init] Your hours: already chosen (%s)" % window_text(saved["overnight"]), flush=True)
        io.put(state="saved", overnight=saved["overnight"], quiet=saved.get("quiet"), already=True)
        io.done()
        return
    io.put(state="ask", overnight=saved.get("overnight") or hc["defaults"]["overnight"],
           quiet=saved.get("quiet") if "quiet" in saved else hc["defaults"]["quiet"], said=[], error="")
    if hc["films"]:
        io.put(said=["hours"])
        rid = getattr(io, "run_id", "")

        def still(_key: str = "") -> bool:                    # ONE VOICE AT A TIME: not once he has changed them
            m = _card_now(rid, "hours")
            return str(m.get("state") or "") == "ask" and not m.get("acted")

        def over() -> None:
            if _HOURS_LINES.get(rid) == "saying":
                _HOURS_LINES[rid] = "done"
        _HOURS_LINES[rid] = "saying"                       # before the call: an inline runner is done when it returns
        if not host_say(rid, str(getattr(io, "sid", "")), hc["films"], wanted=still, done=over):
            _HOURS_LINES.pop(rid, None)


def hours_act(io: CardIO, verb: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    m = io.get() or {}
    state, verb = str(m.get("state") or ""), str(verb or "").strip().lower()
    if state != "ask":
        return {"ok": False, "error": "Your hours is %s now" % (state or "not here")}
    if verb in ("set", "save"):
        upd: dict[str, Any] = {}
        if "overnight" in body:
            w = _window(body.get("overnight"))
            if w is None:
                io.put(error="The overnight window is two different hours, 0 to 23.")
                return {"ok": False, "error": "not a window"}
            upd["overnight"] = w
        if "quiet" in body:
            q = _window(body.get("quiet"))
            if q is None and body.get("quiet") not in (None, "", "none", False, []):
                io.put(error="Quiet hours are two different hours, 0 to 23, or none.")
                return {"ok": False, "error": "not a window"}
            upd["quiet"] = q
        io.put(error="", acted=True, **upd)
        if verb == "set":
            return {"ok": True, "verb": verb}
        m = io.get() or {}
        try:
            hours_save(m.get("overnight"), m.get("quiet"))
        except (OSError, ValueError) as e:
            io.put(error="Your hours could not be saved: %s" % e)
            return {"ok": False, "error": str(e)}
        io.put(state="saved")
        _hours_quicker(io)
        io.done()
        return {"ok": True, "saved": True}
    if verb == "skip":
        io.put(state="skipped")
        _hours_quicker(io)
        io.done()
        return {"ok": True, "verb": verb}
    return {"ok": False, "error": "%s is not an answer to Your hours now" % (verb or "nothing")}


def hours_save(overnight: Any, quiet: Any) -> Path:
    """Write the hours where the house's own Init writes them: site.json training_window (with training_window_set_by,
    as lib/house_hours.set_off_hours does) and manners.quiet_hours. Quiet hours also go to the pack's announcer
    (talk.json announce.quiet_hours, whole hours); "none" takes away only quiet hours this walk set before."""
    w, q = _window(overnight), _window(quiet)
    if w is None:
        raise ValueError("the overnight window is not two hours")
    p = ensure_site()
    d = _load_json(p)
    d["training_window"] = window_text(w)
    d["training_window_set_by"] = "init"
    man = d.setdefault("manners", {}) if isinstance(d.get("manners", {}), dict) else {}
    d["manners"] = man
    talk_q: Any = "keep"
    if q is not None:
        man["quiet_hours"] = window_text(q)
        man["quiet_hours_set_by"] = "init"
        talk_q = {"from": q[0], "to": q[1]}
    elif man.get("quiet_hours_set_by") == "init":
        man.pop("quiet_hours", None)
        man.pop("quiet_hours_set_by", None)
        talk_q = None
    _write_json(p, d)
    if talk_q != "keep":
        try:
            import crissa_talk
            crissa_talk.save({"announce": {"quiet_hours": talk_q}}, home=_home())
        except Exception as e:  # noqa: BLE001  site.json holds the answer; the announcer's copy is said, not fatal
            print("[init] hours: the announcer's quiet hours: %s: %s" % (type(e).__name__, e), flush=True)
    return p


def hours_view(m: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or step_cfg("hours")
    state = str(m.get("state") or "")
    hc = hours_cfg()
    over = _window(m.get("overnight")) or hc["defaults"]["overnight"]
    quiet = _window(m.get("quiet")) if m.get("quiet") is not None else None
    v: dict[str, Any] = {"state": state, "title": cfg.get("title") or "Your hours", "body": "", "hint": "", "keys": [],
                         "overnight": over, "quiet": quiet, "defaults": hc["defaults"],
                         "lines": ["Overnight: %s (%s's own work, while you are away)." % (window_words(over),
                                                                                             crissa_home.her_name()),
                                   "Quiet hours: %s." % (window_words(quiet) if quiet else
                                                         "none (her announcements can speak at any hour)")]}
    if state == "ask":
        v.update(body=str(cfg.get("body") or ""), hint=str(m.get("error") or ""),
                 keys=[("save", "Save"), ("change", "Change"), ("skip", "Skip")])
    elif state == "saved":
        v.update(body="Your hours are saved.")
    elif state == "skipped":
        v.update(body="Skipped.")
    return v


# HER MICROPHONE IS ON WHEN SETUP ENDS (round 18, the User 2026-10-13, .101 round 17: no house-voice.json after Setup -
# missing is OFF - so she heard nothing until he pressed Mic at 15:04; "the mic on at Setup's end, as round 16"). Round
# 16's lock read `on_by: init` at 05:53: the one writer of that name is mic() above, which only the done plate's key and
# the Crissa menu's call, so it had been pressed there too; no round turned it on by itself. Now a first boot's Setup
# turns it on itself, the same way (the ear's own route, POST /panel/voice {on, on_by: "init"}), as it ends: before
# Crissa's closing question, whose 20 s open ear needs it (THE TEAM AT THE END), or before her opening line on the
# older ending. Not when the person turned it off during this run (either key: mic_off_by_person) or it is on already;
# an ear that does not answer is logged and the done plate keeps its key. Kill: CRISSA_SETUP_MIC_ON=0.
def mic_at_end_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_SETUP_MIC_ON", "1").strip().lower() not in ("0", "off", "false", "no")


# HER MICROPHONE ON AT START (round 23, the User 2026-10-15, .101: Setup's mic-on failed - "The ear is not answering
# (URLError)" - and she stayed deaf; "add mic ON at startup too. People will give up if they do not find CP"): every start
# after Setup, once the ear answers (MIC_START_WAIT_S), a microphone that was never turned on (no lock file at all, or
# Setup's own mic-on failed) is turned on. A microphone someone turned off stays off: the lock says so (the ear records
# `off_by` since round 23; an older lock that says off is taken as theirs), and so does Setup's Mic off. The house never
# comes here. Kill: CRISSA_MIC_AT_START=0.
MIC_START_WAIT_S = 180.0


def mic_at_start_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_MIC_AT_START", "1").strip().lower() not in ("0", "off", "false", "no")


def mic_at_start_due() -> tuple[bool, str]:
    """(turn it on?, why) - pure reads, no ear."""
    if not mic_at_start_on():
        return False, "off"
    if not bool(marker().get("done")):
        return False, "Setup is not done"
    st = load_state()
    if st.get("mic_off_by_person"):
        return False, "turned off during Setup"
    lock = voice_lock_path()
    if not lock.is_file():
        return True, "never turned on"
    try:
        d = json.loads(lock.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return False, "the lock cannot be read"
    if str(d.get("voice") or "off").strip().lower() in ("on", "1", "true", "yes"):
        return False, "on already"
    if (st.get("mic_at_end") or {}).get("ok") is False and not d.get("updated_at"):
        return True, "Setup's mic-on did not reach the ear"
    return False, "turned off by %s" % (d.get("off_by") or "someone")


def mic_at_start(wait_s: float = MIC_START_WAIT_S, sleep=time.sleep, clock=time.time, turn_on=None) -> dict[str, Any]:
    due, why = mic_at_start_due()
    if not due:
        return {"ok": False, "skipped": why}
    turn_on = turn_on or (lambda: mic(True))
    t0 = clock()
    while True:
        got = turn_on()
        if got.get("ok"):
            print("[init] her microphone: on at start (%s)" % why, flush=True)
            return dict(got, why=why)
        if clock() - t0 >= wait_s:
            print("[init] her microphone: not turned on at start: %s" % got.get("error"), flush=True)
            return dict(got, why=why)
        sleep(3.0)                      # the ear comes up after the player: ask again


def mic_at_start_later() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST") and (mic is _MIC or getattr(mic, "__wrapped__", None) is _MIC):
        return False                    # a test never reaches a real ear (nor through the suite's mic stand-in)
    if not mic_at_start_due()[0]:
        return False
    _spawn("mic-at-start", mic_at_start)
    return True


def _record_any(run_id: str, fn) -> None:
    """Write into this run's record whatever its status (the ending outlives `running`)."""
    with _LOCK:
        st = load_state()
        if st.get("run_id") == run_id:
            fn(st)
            save_state(st)


def mic_at_end(run_id: str) -> dict[str, Any]:
    if not mic_at_end_on():
        return {"ok": False, "skipped": "off"}
    st = load_state()
    if st.get("run_id") != run_id or st.get("only") or not st.get("first_boot"):
        return {"ok": False, "skipped": "not a first Setup"}
    if st.get("mic_off_by_person"):
        print("[init] her microphone stays off: it was turned off during Setup", flush=True)
        return {"ok": False, "skipped": "the person turned it off"}
    if mic_state().get("on"):
        print("[init] her microphone: on already", flush=True)
        return {"ok": True, "already": True}
    if os.environ.get("PYTEST_CURRENT_TEST") and mic is _MIC:
        return {"ok": False, "skipped": "test"}        # a test never reaches a real ear (this box's may be the house's)
    got = mic(True)
    print("[init] her microphone: %s" % ("on (Setup's end)" if got.get("ok") else "not turned on: %s" % got.get("error")),
          flush=True)
    _record_any(run_id, lambda s: s.update(mic_at_end={"ok": bool(got.get("ok")), "at": _now(),
                                                       "error": str(got.get("error") or "")[:200]}))
    return got


# THE TEAM AT THE END (round 18, the User 2026-10-13, .101 round 17: "Lila keeps her voice and lips the whole way, up to
# 'Let me introduce you to the team.' Then the announcer introduces itself (its role, when you hear it), which records
# its print. Crissa comes last and takes over while her prints are recorded: no formal voice check, one or two sentences
# each"; "then all of it closes while crissa is introing and she gets do what can we do today from the Wall homescreen to
# conclude init"). Round 17's her-voice step after Your room, its handoff and its opening fold into one sequence after
# Your hours, each line in its turn (ONE VOICE AT A TIME):
#   1 Lila, on her plate, her lips on: TEAM_LINE.
#   2 the announcer: ANNOUNCER_INTRO, while the raw microphone records - its prints and this machine's lip sync come
#     from these lines (the coach's mouths run, plain audio as round 17's six lines); Lila stays, listening
#   3 Lila slides off; Crissa's home loop (the landing) comes on and is drawn
#   4 Setup is done: init.json and init-run.json say so, so the desk Wall's Setup plate goes and its start bar takes over
#   5 Setup's own window closes (her pages on the player; a desk Wall closed during Setup opens): to_desk_wall
#   6 Crissa: CRISSA_INTRO on her home loop, her lips drawn, while the raw microphone records her prints (the coach's
#     mouths run, each line through the player's /v1/speak)
#   7 her microphone on (MIC ON AT THE END)
#   8 Crissa: CLOSING_LINE - a question in her own voice with Setup done and the microphone on, so the ear opens for
#     20 s when it ends (ear_bridge HER QUESTION OPENS THE EAR): his first words reach her without "Hey Crissa"
# The prints are recorded only when the ear asks for them (mouths_wanted): otherwise the announcer is not heard and
# Crissa says her lines plainly. Cues text-only or silent: no line, the same order. A pack's first boot (lila_holds).
# Kill: CRISSA_SETUP_TEAM=0 (round 17's ending).
TEAM_LINE = "Let me introduce you to the team."
# Round 19 (the User 2026-10-14, .101 round 18: "Announcer needs to be faster on intro"; "even crissa's opener sounded
# like she was sleeping. She is the extrovert and high energy"): the announcer's four lines are shorter (181 -> 149
# characters; four, because Setup's lip sync needs three that agree, lib/crissa_lipsync MIN_LINES), and Crissa's are
# written to be said brightly - on this machine's Pocket (azelma) her pitch sat 2.0 and 1.9 semitones higher on them,
# with more energy (docs/checkpoints/r19-voices-20261014). The closing question is unchanged: as it was, it was already
# her highest and quickest line ("So!" and "Okay!" in front of it made it slower and lower).
ANNOUNCER_INTRO = ("Hi, I'm the announcer.",
                   "With the krew add-on, I tell you when a job is done,",
                   "or when an agent gets stuck.",
                   "I keep it short, and quiet in your quiet hours.")
CRISSA_INTRO = ("And I'm Crissa! I'm the one you'll be talking with.",
                "Just say my name, any time you need me!")
CLOSING_LINE = "What can we do today?"
TEAM_MOUTHS_CAP_S = 90.0
# Round 20, TWO VOICES (no krew): Lila hands over in one line and Crissa introduces herself while her prints are
# recorded. Her lines are said through the player with her lips, and this machine's lip sync is timed on them (the
# coach's HER PLAYER LINES ARE TIMED TOO): four lines, because the lip sync needs three that agree.
TWO_VOICE_LINE = "We're all set. Now here's Crissa."
CRISSA_INTRO_TWO = ("Hi, I'm Crissa! I'm the one you'll be talking with.",
                    "Just say my name, any time you need me!",
                    "I can't wait to get started!",
                    "Ask me anything, big or small.")


# SHE ENTERS THE ROOM FOR HER HELLO (round 21, the User 2026-10-14, .101 round 20: "pretty long gap. can we tighten?
# seemed like it took her awhile to look up" - 15 s of nothing after "Now here's Crissa.": Lila's leave film, then up to
# 8 s waiting to see her listening loop drawn (never seen: the check counted 12 frames of a player painting at 2 a
# second, see HER PICTURE IS UP BEFORE SHE SPEAKS), then her first line made). The User's call: "Crissa enters the room"
# - her walk-in film under her scripted hello, so the handover IS her entrance. With two voices, after Lila's last line
# she steps off her plate with no leave film and the player cuts to CRISSA_ENTRANCE (the empty office, she walks in and
# sits down at her desk; no film sound, her mouth unpainted at that size: her hello is heard over it), Setup closes at
# once, and her introduction starts while she walks in. Her closing question waits for her desk loop to be drawn after
# the film, and is asked there. A player that refuses the film (not in this pack), or the switch off, falls back to the
# User's second choice: Lila leaves the old way and Crissa is on her listening loop, the sit-up loop. A pack only.
# Kill CRISSA_CRISSA_WALK_IN=0.
CRISSA_ENTRANCE = "crissa-walk-in"
_ENTERED: dict = {"key": ""}                # the entrance film the player took (round 28: hers or the walk-in)
ENTRANCE_DRAWN_CAP_S = 20.0                # the film is 11.6 s; her question waits at most this long for her desk


def entrance_on() -> bool:
    if crissa_home.is_house():
        return False
    return os.environ.get("CRISSA_CRISSA_WALK_IN", "1").strip().lower() not in ("0", "off", "false", "no")


# HEADS UP FOR HER OPENER (round 26, .101 2026-10-16: the walk-in ended before her first line and the whole opener -
# "Hi, I'm Crissa!", "Just say my name...", "What can we do today?" - was said on `work` and `flourish-notes`, heads
# down; the User: "needs to be heads up for the opener"). The walk-in film lands on her desk loop; the moment it has,
# she is put on the landing's heads-up loop (listen, held), as Setup does when there is no walk-in.
# Kill CRISSA_OPENER_HEADS_UP=0.
def opener_up_on() -> bool:
    return os.environ.get("CRISSA_OPENER_HEADS_UP", "1").strip().lower() not in ("0", "off", "false", "no")


# HER OPENER STARTS AFTER THE WALK-IN (round 27, .101 2026-10-16: "Hi, I'm Crissa!" and "Just say my name..." were said
# while she was still walking in - 280 passes, painted 0, the slide keeps its filmed mouth - and only lines 3-5 heads up;
# the User: "I missed the first two sentences"). Setup's window closes, she walks in and lands heads up, and only then
# does her introduction start (and its lip-sync timing, on her painted mouth). Kill: CRISSA_OPENER_AFTER_WALK_IN=0
# (round 26: her hello under the walk-in).
def opener_after_on() -> bool:
    return os.environ.get("CRISSA_OPENER_AFTER_WALK_IN", "1").strip().lower() not in ("0", "off", "false", "no")


# ONE LINE ON SITTING (round 28, the User 2026-10-17 on .101: "Let's change opener to her coming in, sitting down and
# saying 'Nice to meet you, <name>. What can we do today?' Simple, can be playing immediately upon sitting. I can add
# that clip from imagine today. Just the one will do."). Her entrance is CRISSA_SIT_HELLO when the pack has it (the
# User's Imagine clip: she walks in and sits, heads up, mouth still; no sound) and the walk-in until then; the moment she
# lands heads up she says OPENER_LINE - his name from Setup, else without it - and her microphone is on as it ends. It
# replaces her four introduction lines and her closing question. Her lip sync is not timed on one line (it needs three
# that agree): this machine keeps its default timing. Kill CRISSA_OPENER_ONE_LINE=0 (round 27's introduction).
CRISSA_SIT_HELLO = "crissa-sit-hello"
OPENER_LINE = "Nice to meet you, {name}. What can we do today?"
OPENER_LINE_NO_NAME = "Nice to meet you. What can we do today?"


# HER TIMING LINES BEFORE SHE ENTERS (round 28, the User 2026-10-17, via the conductor: "give her those lines from Init,
# on the intro, fade into her entrance after the third line so transition starts her walk in, sit down and say 'what
# can we do today?' right away ... take the three timings in Init screen with Lila live loop saying nothing after her
# intro of Crissa"). After Lila's handover she stays on her loop, silent - no mouth paints (mouth_hushed) - while three of
# Crissa's introduction lines play through the player as a voice and time this machine's lip sync; then her entrance,
# and her one line as she lands. Kill CRISSA_OPENER_TIMING_FIRST=0 (no timing lines; the default lip sync).
# Her four timing lines (the User 2026-10-17, via the conductor: "four if it makes it better"; "Can we make them better?
# More energetic? She is an extrovert and they sound very blase"): exclamations so her voice lifts, each with a p/b/m
# closure for the lip-sync fit.
# .101 round 30 (the User 2026-10-18: "Crissa says Hi and herself says on her way to your office now as her first
# sentence and walks in seamlessly at the end of it"; "if it can be shortened by a sentence from the middle even
# better"): her first line says she is coming, she walks in as it ends (_enter_under_last), the middle line is gone.
# Three lines are still crissa_lipsync.MIN_LINES. Kill CRISSA_ON_HER_WAY=0 (round 29's four).
# .101 10-19 (the User: "Gap between brilliant and today?"): Pocket breaks "Okay, let's make something brilliant today!"
# before "today" on every take (1.0-1.5 s gap; with or without the comma, or a period); without "today" it is one breath
# on every take. The User: "make something brilliant is fine". Still m and b closures for the fit.
CRISSA_TIMING_LINES = (("Hi! I'm Crissa, on my way to your office now!",
                        "Say my name any time, and I'll pop right up!",
                        "Okay, let's make something brilliant!")
                       if os.environ.get("CRISSA_ON_HER_WAY", "1").strip().lower() not in ("0", "off", "false", "no")
                       else ("Hi! I'm Crissa, and I am so happy you're here!",
                             "Say my name any time, and I'll pop right up!",
                             "Big plans, small jobs, bring me both!",
                             "Okay, let's make something brilliant today!"))
_HUSH = {"on": False}


def opener_timing_first_on() -> bool:
    return os.environ.get("CRISSA_OPENER_TIMING_FIRST", "1").strip().lower() not in ("0", "off", "false", "no")


def mouth_hushed() -> bool:
    """No mouth paints now (her timing lines over Lila's silent loop). Read by the painter every frame."""
    return bool(_HUSH["on"])


def opener_one_line_on() -> bool:
    return os.environ.get("CRISSA_OPENER_ONE_LINE", "1").strip().lower() not in ("0", "off", "false", "no")


def opener_line(name: str | None = None) -> str:
    n = (stored_user_name() if name is None else str(name or "")).strip().split(" ")[0][:40]
    return OPENER_LINE.replace("{name}", n) if n else OPENER_LINE_NO_NAME


def _entrance_then_up(cap_s: float = ENTRANCE_DRAWN_CAP_S) -> bool:
    """After the walk-in: her heads-up loop. True when she was put there."""
    if not wait_drawn("", frames=1, cap_s=cap_s, not_key=_ENTERED.get("key") or CRISSA_ENTRANCE):
        print("[init] the walk-in's end was not seen in time; she stays where it left her", flush=True)
        return False
    _land_on_listen()
    _ENDING["seated"] = True                    # round 37: her tile comes back on the Wall
    print("[init] Crissa heads up for her opener (%s)" % _landing_key(), flush=True)
    return True


def _crissa_enters(run_id: str, keep_line: bool = False) -> bool:
    """Lila steps off her plate with no leave film and Crissa's walk-in is cut in. False (Lila still on): refused.
    keep_line: her line is on the air (the walk-in under her last sentence) - the cut does not stop it."""
    if not entrance_on():
        return False
    was = bool(_HOST.get("on"))
    _HOST["gen"] += 1                           # no runner of Lila's goes on
    _HOST["on"] = False                         # her hold is off before the cut (the player's GUEST_HOLD reads it)
    keys = ([CRISSA_SIT_HELLO] if opener_one_line_on() else []) + [CRISSA_ENTRANCE]
    for key in keys:                            # round 28: the User's sit-down clip first, once the pack has it
        if play(key, keep_line=keep_line):
            _ENTERED["key"] = _PLAYED_AS.get(key) or key    # the clip the player took (v2: v2-walk-in), not the asked name
            print("[init] Crissa walks in (%s) under her hello" % _ENTERED["key"], flush=True)
            _team_phase(run_id, "entrance", film=key)
            return True
    _HOST["on"] = was
    print("[init] Crissa's walk-in was not taken here; Lila leaves and Crissa is on her loop", flush=True)
    return False


def _hours_early(run_id: str) -> bool:
    st = load_state()
    return st.get("run_id") == run_id and bool((st.get("hours") or {}).get("early"))


def team_end_on(st: dict[str, Any] | None = None) -> bool:
    if os.environ.get("CRISSA_SETUP_TEAM", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return lila_holds(st)


class _RoomNote:
    """Where the lip sync line is kept for the done plate (room.lip_sync), whatever step the run is on."""

    def __init__(self, run_id: str):
        self.run_id = run_id

    def put(self, **kw) -> bool:
        _record_any(self.run_id, lambda s: s.setdefault("room", {}).update(kw))
        return True


def _team_phase(run_id: str, phase: str, **kw) -> None:
    def fn(s: dict[str, Any]) -> None:
        e = s.setdefault("ending", {})
        e.update(phase=phase, **kw)
        e.setdefault("phases", []).append(phase)
    _record_any(run_id, fn)


def _team_mouths(plan: dict[str, Any], what: str, cap_s: float = TEAM_MOUTHS_CAP_S) -> bool:
    """One coach mouths run for `plan` ({ids, lines, player}: the coach's CRISSA_MOUTHS_PLAN), watched to its end.
    False when the coach is not there, refused it, or it did not finish in cap_s."""
    ok, why = _coach_ready()
    if not ok:
        print("[init] %s: not recorded (%s)" % (what, why), flush=True)
        return False
    got = _coach_post("/start", {"mode": "mouths", "person_id": "crissa_her_tts", "create": False, "station": "desk",
                                 "mouths": plan}, 20.0)
    if not got.get("_ok") or got.get("ok") is False:
        print("[init] %s: the coach said %s" % (what, str(got.get("error") or got.get("_error"))[:160]), flush=True)
        return False
    t0, seen, n = time.time(), False, 0
    polls = max(1, int(round(cap_s / POLL_S)))
    while time.time() - t0 < cap_s and n < polls:
        n += 1
        sess = _coach_get("/session", 3.0)
        running = bool(sess.get("running")) if sess.get("_ok") else seen
        seen = seen or running
        if sess.get("_ok") and not running and (seen or time.time() - t0 > 4.0 or n * POLL_S > 4.0):
            print("[init] %s: done (rc %s)" % (what, sess.get("last_rc")), flush=True)
            return True
        _sleep(POLL_S)
    _coach_post("/cancel", {}, 4.0)
    print("[init] %s: did not finish in %d s" % (what, int(cap_s)), flush=True)
    return False


# HER LINE UNDER LILA'S HANDOVER (.101 round 29, the User 2026-10-18: "she should say the sentence then the beep. no
# gaps. 'Here is Crissa' then Crissa. It should move"). .101's run: Lila's done film said "Now here's Crissa." (its last
# word 1.7 s before its end), Init waited for the film's END, only then asked the coach for her timing lines, and the
# coach started its session, rendered her lines and levelled them before the first was said - about 6.7 s of quiet
# after "Now here's Crissa." (her first line aired 12:09:49, its session started 12:09:44). Then, after her last line,
# the coach made her prints and her lip sync (12:10:11 -> 12:10:15) before her walk-in (11.3 s) began, and her opener
# came at 12:10:27: 16 s with no word. Now:
#   - her timing lines are rendered ahead, into the coach's disk cache, as the walk reaches Your room (warm_timing_lines;
#     Voice ID and the face photos are done, so Pocket is free);
#   - the coach's run starts as Lila's done film is cued; it holds her line (the plan's `go` file) until the page says
#     the film reached its last word (film_word), so she speaks on "Now here's Crissa." and Lila settles under her;
#   - the coach writes where her line is on the air (the plan's `air` file; her lines are one line, mouth_prints HER
#     LINES AS ONE LINE), and her walk-in starts with her LAST sentence (the User 2026-10-17: "fade into her entrance
#     after the third line so transition starts her walk in"), Setup's window closing as she comes in; her prints and
#     her lip sync are made while she walks.
# Kill: CRISSA_HANDOVER_OVERLAP=0 (round 29 as shipped: the film to its end, then her lines, then her walk-in).
# CRISSA_TIMING_WARM=0: no lines rendered ahead.
_TIMING_WARM = {"run": ""}


def handover_overlap_on() -> bool:
    return os.environ.get("CRISSA_HANDOVER_OVERLAP", "1").strip().lower() not in ("0", "off", "false", "no")


def warm_timing_lines(run_id: str) -> None:
    """Ask the coach to render her timing lines now (once a run), in her voice, into its disk cache."""
    if (crissa_home.is_house() or not run_id or _TIMING_WARM["run"] == run_id or os.environ.get("PYTEST_CURRENT_TEST")
            or os.environ.get("CRISSA_TIMING_WARM", "1").strip().lower() in ("0", "off", "false", "no")
            or not (lines_on() and opener_one_line_on() and opener_timing_first_on())):
        return
    _TIMING_WARM["run"] = run_id

    def go() -> None:
        got = _coach_post("/warm", {"voice": "crissa", "lines": list(CRISSA_TIMING_LINES)}, 4.0)
        print("[init] her timing lines: %s" % ("rendering ahead" if got.get("_ok") and got.get("ok") is not False else
                                               "not rendered ahead (%s)" % str(got.get("error") or got.get("_error"))[:80]),
              flush=True)
    threading.Thread(target=go, name="init-warm-timing", daemon=True).start()


# HER OPENER RENDERED AHEAD (round 40 N22; .101 round 38, the User 2026-10-21 after the first boot's handover: "good,
# just misses on the first sentence. Wish that was scripted for some time to warm. First impression."): her opener was
# rendered as she landed and its first piece played 0.39 s before its marks arrived. It is a scripted line and the name
# in it is known from Voice ID on, so Setup renders it in her voice into <state home>/cache/opener/ while the person
# is still in Setup (at the room/hours steps, and again under the handover if the name changed), and she says it from
# that file through the player's rendered-line path (house-speak --wav): the whole line is aligned before her first
# word, levelled like any of her lines. Not ready in time: spoken as any line, as before, and the log says so.
# Kill CRISSA_OPENER_AHEAD=0.
_OPENER_AHEAD: dict[str, Any] = {"line": "", "wav": "", "busy": ""}
_OPENER_LOCK = threading.Lock()


def opener_ahead_on() -> bool:
    return os.environ.get("CRISSA_OPENER_AHEAD", "1").strip().lower() not in ("0", "off", "false", "no")


def _opener_wav_path(line: str, voice: str = "crissa") -> Path:
    import hashlib
    sig = ""
    try:
        import crissa_tts
        cfg = crissa_tts.config()
        sig = json.dumps({k: cfg.get(k) for k in ("engine", "voices", "voice")}, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001  no voice config: the line and voice alone name it
        pass
    h = hashlib.sha256(("%s|%s\n%s" % (sig, voice, line)).encode()).hexdigest()[:24]
    return _home() / "cache" / "opener" / ("%s.wav" % h)


def render_opener(line: str, voice: str = "crissa", synth=None) -> str:
    """Her opener in her voice as a wav under the state home's cache (kept: the same line is not rendered twice).
    The path, or '' when it could not be rendered."""
    import wave
    path = _opener_wav_path(line, voice)
    if path.is_file() and path.stat().st_size > 1000:
        return str(path)
    try:
        if synth is None:
            import crissa_tts
            synth = crissa_tts.synth
        pcm, sr = synth(line, voice)
        if not pcm or not sr:
            return ""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            w.writeframes(pcm)
        os.replace(tmp, path)
        return str(path)
    except Exception as e:  # noqa: BLE001  no voice yet: she says it as any line
        print("[init] her opener: not rendered ahead (%s: %s)" % (type(e).__name__, str(e)[:80]), flush=True)
        return ""


def warm_opener(run_id: str = "", name: str | None = None, synth=None, wait: bool = False) -> None:
    """Render her opener ahead, on a thread (wait: here). Once a line; a new name renders the new line."""
    if (crissa_home.is_house() or not opener_ahead_on() or not (lines_on() and opener_one_line_on())
            or (os.environ.get("PYTEST_CURRENT_TEST") and synth is None)):
        return
    line = opener_line(name)
    with _OPENER_LOCK:
        if line in (_OPENER_AHEAD["line"], _OPENER_AHEAD["busy"]):
            return
        _OPENER_AHEAD["busy"] = line

    def go() -> None:
        t0 = time.time()
        wav = render_opener(line, "crissa", synth)
        with _OPENER_LOCK:
            _OPENER_AHEAD["busy"] = ""
            if wav:
                _OPENER_AHEAD.update(line=line, wav=wav)
        if wav:
            print("[init] her opener: rendered ahead (%.1f s): %s" % (time.time() - t0, line), flush=True)
    if wait:
        go()
    else:
        threading.Thread(target=go, name="init-warm-opener", daemon=True).start()


def opener_wav(line: str) -> str:
    """The rendered file for exactly this line, else ''."""
    with _OPENER_LOCK:
        wav = _OPENER_AHEAD["wav"] if _OPENER_AHEAD["line"] == line else ""
    return wav if wav and Path(wav).is_file() else ""


def _mouths_signals() -> tuple[Path, Path]:
    d = _home() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d / "mouths-go", d / "mouths-air.json"


def on_her_way() -> bool:
    return os.environ.get("CRISSA_ON_HER_WAY", "1").strip().lower() not in ("0", "off", "false", "no")


def no_desk_before_entrance() -> bool:
    """.101 round 30 (the User 2026-10-18: "we are flashing the wall picture 2 times before she enters the office the
    first time"): Lila's enter film starts on Crissa's desk and her leave film ends on it, so Crissa's desk showed at
    Setup's open and again under the handover before her walk-in. On a first boot Lila opens straight on her loop and
    stays camped on it until the walk-in is cut in from her. Kill CRISSA_NO_DESK_BEFORE_ENTRANCE=0."""
    return os.environ.get("CRISSA_NO_DESK_BEFORE_ENTRANCE", "1").strip().lower() not in ("0", "off", "false", "no")


def walkin_after_last_on() -> bool:
    return os.environ.get("CRISSA_WALKIN_AFTER_LAST", "1").strip().lower() not in ("0", "off", "false", "no")


def _enter_under_last(run_id: str, air: Path, th: threading.Thread) -> bool:
    """Her walk-in as her last sentence starts (Setup's window closing with it). False: her line never aired, it ended
    first, or the walk-in was not taken - the caller enters as before."""
    polls = int(TEAM_MOUTHS_CAP_S / QUIET_POLL_S) + 1
    d: dict[str, Any] = {}
    while th.is_alive() and polls > 0:
        polls -= 1
        d = _load_json(air) if air.is_file() else {}
        if d.get("air_at"):
            break
        _sleep(QUIET_POLL_S)
    try:
        starts = list(d.get("starts") or [0.0])
        # .101 round 30: she walks in as her first sentence ("on my way to your office now") ends - the second's start
        at = float(d["air_at"]) + float(starts[1] if (on_her_way() and len(starts) > 1) else starts[-1])
        # .101 round 32 (the User 2026-10-19: "She entered lips still during the last 2s of her script. Could we delay
        # that entrance for just after her last word?"): her timing lines are hushed (no mouth), so a walk-in under
        # them shows her talking with her lips still. She walks in as the whole line ends (air_at + its seconds).
        # Kill CRISSA_WALKIN_AFTER_LAST=0 (round 30-32: at the second sentence's start).
        if walkin_after_last_on() and d.get("seconds"):
            at = float(d["air_at"]) + float(d["seconds"])
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    polls = int(max(0.0, at - time.time()) / QUIET_POLL_S) + 2
    while th.is_alive() and polls > 0 and time.time() < at:
        polls -= 1
        _sleep(min(QUIET_POLL_S, max(0.0, at - time.time())))
    # .101 round 29 (the User: "Crissa final sentence got cut just before walk in"): the walk-in is a forced load, and a
    # forced load stops the line on the air (her one line ended on signal 15 as the walk-in was cut in); this one keeps
    # her line. Kill CRISSA_WALKIN_KEEPS_LINE=0.
    keep = os.environ.get("CRISSA_WALKIN_KEEPS_LINE", "1").strip().lower() not in ("0", "off", "false", "no")
    if not th.is_alive() or not _running(run_id) or not _crissa_enters(run_id, keep_line=keep):
        return False
    print("[init] Crissa walks in under her last line", flush=True)
    _close_setup_window()
    return True


def _timing_under_handover(run_id: str) -> tuple[bool, bool]:
    """(timed, entered): her timing lines from Lila's last word, her walk-in under her last sentence."""
    warm_opener(run_id)                            # round 40 N22: a name changed since the room step renders again
    go, air = _mouths_signals()
    for p in (go, air):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    _team_phase(run_id, "crissa")
    _team_phase(run_id, "timing")
    cap = film_seconds("done") + FILM_SAID_SLACK_S
    plan = {"ids": ["crissa_her_tts"], "lines": {"crissa_her_tts": list(CRISSA_TIMING_LINES)},
            "player": ["crissa_her_tts"], "go": str(go), "go_cap_s": cap, "air": str(air)}
    res: dict[str, Any] = {}
    asked = time.time()
    _lip_measure_from(asked)                    # the hotfix: the set-on glass is this window's
    _HUSH["on"] = True
    th = threading.Thread(target=lambda: res.update(timed=_team_mouths(plan, "Crissa's timing lines (under Lila's "
                                                                              "handover)")),
                          name="init-timing", daemon=True)
    th.start()
    entered = False
    try:
        loop = host_keys().get("loop")
        if no_desk_before_entrance() and _HOST.get("on") and loop and not (office_now() and office_hold()):
            play(loop, stay=True)                  # round 30: she holds her loop until the walk-in, never the desk
        n = film_cue("done", by="handover")       # ONE-PAGE SETUP: "That's everything ... Now here's Crissa."
        if n and not SYNC and not film_word_wait(n, cap):
            print("[init] film done: the page did not say her last word (no page open?)", flush=True)
        try:
            go.write_text("%.3f\n" % time.time(), encoding="utf-8")
        except OSError:
            pass
        entered = _enter_under_last(run_id, air, th)
        th.join(TEAM_MOUTHS_CAP_S + 5.0)
    finally:
        _HUSH["on"] = False
        for p in (go, air):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    timed = bool(res.get("timed"))
    if timed:
        _lip_sync_said(_RoomNote(run_id), since=asked)
    return timed, entered


def _crissa_says(text: str, wav: str = "") -> bool:
    print("[init] Crissa: %s" % text[:80], flush=True)
    if wav:
        say(text, voice="crissa", wav=wav)
    else:
        say(text, voice="crissa")                  # as before: a speaker that takes no file is never asked for one
    return True


def _landing_key() -> str:
    return str(landing_steps()[0].get("cue") or "listen")


def _close_setup_window() -> str:
    """Step 5: her Wall is the desktop again, nothing of Setup's in front of it (to_desk_wall). With no desk Wall in
    this install her window stays, on its done plate."""
    if not desk_present():
        print("[init] Setup's window stays (no desk Wall here): its done plate", flush=True)
        return "kept"
    if os.environ.get("PYTEST_CURRENT_TEST") and to_desk_wall is _TO_DESK_WALL:
        return "test"                              # a test never closes a real window
    try:
        got = to_desk_wall()
    except Exception as e:  # noqa: BLE001  a window that cannot be closed never stops her closing line
        print("[init] Setup's window: %s" % type(e).__name__, flush=True)
        return "error"
    print("[init] Setup's window closes: %s" % str(got.get("note") or "her Wall is the desktop"), flush=True)
    return "closed"


def _clear_screen_for_entrance() -> None:
    """Round 36 (lib/crissa_window.minimize_for_entrance): his windows down as Lila hands over, first boot only."""
    st = load_state()
    if crissa_home.is_house() or st.get("ids") or st.get("only") or st.get("again") or marker().get("done"):
        return

    def run() -> None:
        try:
            import crissa_window
            n, why = crissa_window.minimize_for_entrance()
        except Exception as e:  # noqa: BLE001  her entrance never waits on a window
            n, why = 0, "%s: %s" % (type(e).__name__, e)
        print("[init] handover: %d windows minimized for her entrance%s" % (n, (" (%s)" % why) if why and not n else ""),
              flush=True)
    if SYNC:
        run()
    else:
        threading.Thread(target=run, name="init-entrance-clear", daemon=True).start()


def _team_end(run_id: str) -> None:
    _clear_screen_for_entrance()                  # round 36: "minimize all windows when Lila begins to talk"
    _ENDING.update(on=True, seated=False)
    try:
        _team_end_walk(run_id)
    except Exception as e:  # noqa: BLE001  the walk is never left running for want of a line
        print("[init] Setup's end: %s: %s" % (type(e).__name__, str(e)[:160]), flush=True)
        if _running(run_id):
            _finish(run_id, landing=False)
    finally:
        _ENDING.update(on=False, seated=True)


def _team_end_two(run_id: str) -> None:
    """Round 20, TWO VOICES: Lila's one line -> Setup closes -> Crissa's introduction, her prints recorded and the lip
    sync timed on it -> her microphone on -> her question."""
    voiced = lines_on()
    print("[init] Setup's end: Lila hands over to Crissa (two voices, no krew here)", flush=True)
    handover = HOURS_DONE_LINE if _hours_early(run_id) else TWO_VOICE_LINE     # QUICKER THAN HER LINES
    want, why = mouths_or_lip_wanted() if (voiced and mouths_on()) else (
        False, "the cues are text only" if cues_mode() == "text" else "her lines are off here")
    print("[init] her timing lines (her prints and the lip sync): %s (%s)" % ("played" if want else "not played", why),
          flush=True)
    timed, early, overlapped = False, False, False
    if (one_page_on() and voiced and want and opener_one_line_on() and opener_timing_first_on()
            and handover_overlap_on()):
        timed, early = _timing_under_handover(run_id)      # .101 round 29: "'Here is Crissa' then Crissa"
        overlapped = True
        if not _running(run_id):
            return
    elif one_page_on():
        film_play_wait("done", by="handover")       # ONE-PAGE SETUP: "That's everything ... Now here's Crissa."
    elif voiced and _HOST.get("on"):
        ran, _ = speak_turn(lambda: _lila_says(handover), wanted=lambda: _running(run_id),
                            what="Lila's handover")
        if ran:
            _wait_said(handover)
    if not _running(run_id):
        return                                                                 # cancelled meanwhile
    if not overlapped:
        _team_phase(run_id, "crissa")
    if not overlapped and voiced and want and opener_one_line_on() and opener_timing_first_on():
        # round 28: her timing lines as a voice over Lila's silent loop, before her entrance
        _team_phase(run_id, "timing")
        _HUSH["on"] = True
        try:
            asked = time.time()
            _lip_measure_from(asked)                    # the hotfix: the set-on glass is this window's
            timed = _team_mouths({"ids": ["crissa_her_tts"], "lines": {"crissa_her_tts": list(CRISSA_TIMING_LINES)},
                                  "player": ["crissa_her_tts"]}, "Crissa's timing lines (over Lila's loop)")
        finally:
            _HUSH["on"] = False
        if timed:
            _lip_sync_said(_RoomNote(run_id), since=asked)
        if not _running(run_id):
            return
    entered = early or (voiced and _crissa_enters(run_id))                     # SHE ENTERS THE ROOM FOR HER HELLO
    after = entered and opener_up_on() and opener_after_on()
    if entered and opener_up_on() and not after:
        threading.Thread(target=_entrance_then_up, name="opener-up", daemon=True).start()   # round 26: heads up
    drawn = False
    if not entered:
        host_leave(run_id, wait=True)
        _land_on_listen()
        drawn = wait_drawn(_landing_key()) if voiced else False
    _finish(run_id, landing=False)
    _team_phase(run_id, "done")
    if not early:                               # .101 round 29: closed already as she walked in under her last line
        _close_setup_window()
    if after:                                   # round 27: her first line waits for her to land, heads up
        drawn = _entrance_then_up()
    if entered:
        _lip_sync_full_wall(run_id)             # round 35: the glass she is seen on now, before her opener
    said = False
    if voiced and opener_one_line_on():          # round 28: one line, said as she lands; the mic on as it ends
        line = opener_line()
        if not timed:
            _lip_sync_said(_RoomNote(run_id), skipped="her opener is one line and her timing lines did not play")
        wav = opener_wav(line) if opener_ahead_on() else ""
        if opener_ahead_on():
            print("[init] her opener: %s" % ("rendered ahead; aligned before her first word" if wav else
                                            "not rendered ahead in time; spoken as any line"), flush=True)
        ran, _ = speak_turn(lambda: _crissa_says(line, wav), what="Crissa's opener")
        if ran:
            _wait_said(line)
        mic_at_end(run_id)
        _record_any(run_id, lambda s: s.update(landing={"done_at": _now(), "team": True, "voices": 2,
                                                        "entrance": bool(entered), "opener": "one line",
                                                        "film": _ENTERED.get("key") or None}))
        return
    if voiced and want:
        asked = time.time()
        said = _team_mouths({"ids": ["crissa_her_tts"], "lines": {"crissa_her_tts": list(CRISSA_INTRO_TWO)},
                             "player": ["crissa_her_tts"]}, "Crissa introduces herself")
        if said:
            _lip_sync_said(_RoomNote(run_id), since=asked)
        else:
            _lip_sync_said(_RoomNote(run_id), skipped="Crissa's lines did not play")
    else:
        print("[init] Crissa's voice: not recorded (%s)" % why, flush=True)
        _lip_sync_said(_RoomNote(run_id), skipped="her voice through the speakers was not needed: %s" % why)
    if voiced and not said:
        for line in CRISSA_INTRO_TWO:
            ran, _ = speak_turn(lambda line=line: _crissa_says(line), what="Crissa's line")
            if ran:
                _wait_said(line)
    mic_at_end(run_id)
    if voiced:
        if entered and not (after and drawn):                                  # her desk, drawn, once she sits down
            drawn = wait_drawn("", cap_s=ENTRANCE_DRAWN_CAP_S, not_key=_ENTERED.get("key") or CRISSA_ENTRANCE)
        elif not drawn:
            drawn = wait_drawn(_landing_key())                                 # her loop, drawn, before she asks
        print("[init] Crissa's closing question%s" % ("" if drawn else " (her loop not seen drawn in time)"),
              flush=True)
        speak_turn(lambda: _crissa_says(CLOSING_LINE), what="Crissa's closing question")
    _record_any(run_id, lambda s: s.update(landing={"done_at": _now(), "team": True, "voices": 2,
                                                    "entrance": bool(entered)}))


# HER WALK-IN ON A CLEAR WALL (round 37, .101 round 36, the User: "it blocks the door in the walk in"; "on the walk in
# let's minimize the render window"). From Lila's handover until Crissa is seated for her opener, the snapshot says so,
# and the desk Wall draws no middle plate and keeps her lower-right tile off (the walk-in is on the Wall itself). A pack
# only. Kill CRISSA_WALKIN_CLEAR=0.
def walkin_clear_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if crissa_home.is_house():
        return False
    return str(e.get("CRISSA_WALKIN_CLEAR", "1")).strip().lower() not in ("0", "off", "false", "no")


def ending_view() -> dict[str, Any]:
    """{"on", "seated"} while the team's end runs (the Wall's walk-in), {} otherwise or switched off."""
    if not walkin_clear_on() or not _ENDING.get("on"):
        return {}
    return {"on": True, "seated": bool(_ENDING.get("seated"))}


def _team_end_walk(run_id: str) -> None:
    if two_voices_on():
        _team_end_two(run_id)
        return
    voiced = lines_on()
    print("[init] Setup's end: Lila introduces the team", flush=True)
    team_line = (HOURS_DONE_SHORT + " " + TEAM_LINE) if _hours_early(run_id) else TEAM_LINE
    if one_page_on():
        film_play_wait("done", by="handover")       # ONE-PAGE SETUP: her done film, then the team
    elif voiced and _HOST.get("on"):                                          # 1
        ran, _ = speak_turn(lambda: _lila_says(team_line), wanted=lambda: _running(run_id), what="Lila's team line")
        if ran:
            _wait_said(team_line)
    want, why = mouths_wanted() if (voiced and mouths_on()) else (
        False, "the cues are text only" if cues_mode() == "text" else "her lines are off here")
    recorded = False
    if want and _running(run_id):                                              # 2
        _team_phase(run_id, "announcer")
        asked = time.time()
        recorded = _team_mouths({"ids": ["announcer"], "lines": {"announcer": list(ANNOUNCER_INTRO)}},
                                "the announcer introduces itself")
        if recorded:
            _lip_sync_said(_RoomNote(run_id), since=asked)
        else:
            _lip_sync_said(_RoomNote(run_id), skipped="the announcer's lines did not play")
    else:
        print("[init] the team's voices: not recorded (%s)" % why, flush=True)
        _lip_sync_said(_RoomNote(run_id), skipped="her voice through the speakers was not needed: %s" % why)
    if not _running(run_id):
        return                                                                 # cancelled meanwhile
    _team_phase(run_id, "crissa")                                              # 3
    host_leave(run_id, wait=True)
    _land_on_listen()
    drawn = wait_drawn(_landing_key()) if voiced else False
    _finish(run_id, landing=False)                                             # 4
    _team_phase(run_id, "done")
    _close_setup_window()                                                      # 5
    if voiced:                                                                 # 6
        said = recorded and _team_mouths({"ids": ["crissa_her_tts"], "lines": {"crissa_her_tts": list(CRISSA_INTRO)},
                                          "player": ["crissa_her_tts"]}, "Crissa introduces herself")
        if not said:
            for line in CRISSA_INTRO:
                ran, _ = speak_turn(lambda line=line: _crissa_says(line), what="Crissa's line")
                if ran:
                    _wait_said(line)
    mic_at_end(run_id)                                                         # 7
    if voiced:                                                                 # 8
        print("[init] Crissa's closing question%s" % ("" if drawn else " (her loop not seen drawn in time)"),
              flush=True)
        speak_turn(lambda: _crissa_says(CLOSING_LINE), what="Crissa's closing question")
    _record_any(run_id, lambda s: s.update(landing={"done_at": _now(), "team": True}))


# ── the landing: she lands on her listening loop and stays ───────────────────────────────────────────────────────────
def landing_steps() -> list[dict[str, Any]]:
    """The landing's cues (config/init-sequence-pack.json `landing`). A cue `listen` means HER listening loop: the face's
    declared `listen_loop` when its manifest names another (round 38, the User 2026-10-21: the kit listens on
    listen-warm - Setup landed her on the old listen while every later boot came up on listen-warm). Kill
    CRISSA_LISTEN_LOOP_WINS=0 (the cue as written)."""
    rows = [dict(r) for r in ((pack_cfg().get("landing") or {}).get("steps") or [{"cue": "listen"}]) if isinstance(r, dict)]
    rows = rows or [{"cue": "listen"}]
    if os.environ.get("CRISSA_LISTEN_LOOP_WINS", "1").strip().lower() in ("0", "off", "false", "no"):
        return rows
    try:
        import avatar
        # the loop she RESTS on when the manifest declares one apart from her talking loop (round 39's rest_loop; the
        # User 2026-10-26, the crissatest walk: "walk in was great but we started on frownie face" - the landing sat
        # her on `listen`, the talking loop, where every later boot and every rest between lines is listen-warm)
        own = avatar.rest_loop(_poses_dir()) or avatar.listen_loop(_poses_dir())   # the face whose manifest names this library
    except Exception:  # noqa: BLE001  no manifest read: the cue as written
        own = None
    for r in rows:
        if r.get("cue") == "listen" and own:
            r["cue"] = own
    return rows


def _land_on_listen() -> None:
    for row in landing_steps():
        if row.get("cue"):
            play(str(row["cue"]), stay=True)


def _landing(run_id: str) -> None:
    st0 = load_state()
    if lila_holds(st0) and st0.get("run_id") == run_id:
        print("[init] Setup done: Lila introduces Crissa", flush=True)
        reg = st0.get("register") or {}
        handoff(run_id, str(reg.get("display") or "") or stored_user_name())
    else:
        print("[init] landing on listen", flush=True)
        host_leave(run_id, wait=True)               # Lila's plate, when her card was the last, slides back first
        _land_on_listen()
    with _LOCK:
        st = load_state()
        if st.get("run_id") == run_id:
            st["landing"] = {"done_at": _now()}
            save_state(st)


def _post(path: str, body: dict[str, Any], timeout: float = 4.0) -> dict[str, Any]:
    base = _CFG.get("base") or player_base()
    if not base:
        print(f"[init] no conductor address for {path}", flush=True)
        return {"ok": False, "error": "no conductor"}
    req = urllib.request.Request(f"{base}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with crissa_egress.urlopen(req, timeout, what="init: the conductor") as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        print(f"[init] the conductor did not take {path}: {type(e).__name__}: {e}"[:240], flush=True)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}


# THE CLIP THE PLAYER TOOK FOR A KEY (the 10-29 review of cut 13, BLOCKER 2): with v2 worn, crissa-walk-in plays as
# v2-walk-in (the worn library's role, lib/conductor.MOMENT_ROLES), so a wait keyed on the asked name saw the walk-in's
# first frame as "not that clip" and cut her entrance. /v1/play answers `now`; the entrance waits on that.
_PLAYED_AS: dict = {}


def play(key: str, *, stay: bool = False, keep_line: bool = False) -> bool:
    body = {"key": key, "force": True, "stay": bool(stay), "by": "init"}
    if keep_line:
        body["keep_line"] = True        # .101 round 29: her walk-in under her last line must not stop that line
    got = _post("/v1/play", body)
    ok = bool(got.get("ok"))
    if ok and isinstance(got.get("now"), str) and got.get("now"):
        _PLAYED_AS[key] = got["now"]
    return ok


def say(text: str, voice: str = "crissa", wav: str = "") -> None:
    """A live line through the pack's own voice when one is configured; the plate carries the words either way.
    `wav` (round 40 N22): the line already rendered (her opener), played with its whole sheet."""
    fn = _CFG.get("speak")
    if fn is None or os.environ.get("CRISSA_INIT_SILENT", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    try:
        if wav:
            try:
                fn(text, voice, wav=wav, level=True)
                return
            except TypeError:                       # a speaker that takes no file: the line as any line
                pass
        fn(text, voice)
    except Exception as e:  # noqa: BLE001
        print(f"[init] say: {type(e).__name__}", flush=True)


def _standin_voice_install() -> dict[str, Any]:
    mod = _voice_install_mod()
    if mod is None:
        return dict(snapshot(), ok=False, error="installing her voice is switched off on this machine")
    if mod.running(_home()):
        return dict(snapshot(), ok=True, note="her voice is already installing")
    st0 = load_state()
    rid0 = st0.get("run_id") if st0.get("status") == "running" else ""
    _spawn("voice_install", lambda: voice_install(rid0 or "", retry=True))
    return dict(snapshot(), ok=True, note="installing her voice")


# A STRANGER IS LOCKED OUT OF ADJUSTING (round 17 follow-ups, the User 2026-10-13): while the kit's Security is On or
# above, Setup's own settings - running Setup again, the talk, her voice's install, the mic on, starting at login, the
# cues, the microphone pick, the Register take from the Wall - need his face first (lib/crissa_menu.face_gate, the one
# face-match sequence). A Setup run he started goes on step by step; Cancel and the mic off stay open to anyone.
# Kill: CRISSA_SECURITY_LOCK_ADJUST=0.
APPLY_LOCKED = ("start", "restart", "talk", "voice_install", "login", "cues", "mic_pick")


def _face_refused(name: str) -> dict[str, Any] | None:
    try:
        import crissa_menu
        return crissa_menu.face_gate(name)
    except Exception:  # noqa: BLE001  no menu module: Setup as before
        return None


# ── the one entry point ──────────────────────────────────────────────────────────────────────────────────────────────
# ── ADD/EDIT IDS AND ROOM (round 36, the User 2026-10-19) ───────────────────────────────────────────────────────────────
# His design (.101 round 33 "DESIGN", round 34 results): one container, always - every new ID and every edit runs the
# CURRENT one page with Lila hosting, Voice ID with the per-count turn check and the front-print match, no Face photos
# section, no hours step for a new person. Nothing opens the old page; there is no "Set up again". ONE menu button,
# "Add/Edit IDs and Room": the typed name decides (round 23's name_only, round 21's proof) - the User's own name opens
# HIS file with every completed section shown and each redoable by a click; a new name is a new person's file, walked
# as new, with an optional seat on the room clock; a kept name is that person again. A READY press by whoever sits at
# the camera before anything is captured, everyone every time: the User's proof waits for the User's Ready; then the
# page hands over ("Now <guest>'s turn") and the guest's takes wait for the guest's Ready. Nothing records and no still
# is taken before that press (the coach is not started until then). The continual auth is SUSPENDED from the User's
# Ready to the run's end (lib/crissa_registration: the hold file presence and the ear read). Once a name is entered the
# file is open and cannot be renamed for the run; Back to the Wall / Cancel ends the run and the proof's hold with it.
# A cancelled run is simply not saved: a new person with no print banked is forgotten, the User's file is as it was.
# A pack only. Kill CRISSA_IDS_FLOW=0 (round 34's menu: Voice ID again, Face photos again, Add a person).
IDS_SECTIONS_ME = ("voice_id", "room", "hours")
IDS_LABELS = {"voice_id": "Voice and Face ID", "room": "Your room", "hours": "Your hours"}
IDS_HANDOVER = "Now %s's turn: sit where the camera can see you, then press Ready."


def ids_flow_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_FLOW", "1")).strip().lower() not in ("0", "off", "false", "no")


def ids_run(st: dict[str, Any] | None = None) -> bool:
    st = st if st is not None else load_state()
    return bool(st.get("ids")) and st.get("status") == "running"


def _ids_summaries(pid: str) -> dict[str, str]:
    """What the User's file holds today, one line a section (the page shows them with their Redo keys)."""
    out: dict[str, str] = {}
    nv, nf = voice_prints(pid), face_prints(pid)
    out["voice_id"] = ("%d voice print%s" % (nv, "" if nv == 1 else "s") if nv else "No voice print yet") + \
        (" · %d face still%s" % (nf, "" if nf == 1 else "s") if nf else "")
    marks = (room_saved().get("marks") or {})
    out["room"] = ("Sound %s · door at %s" % ("from this computer" if not marks.get("speakers") else "at %s" % marks.get("speakers"),
                                             marks.get("door", "-"))) if marks else "Not set"
    over = hours_saved().get("overnight")
    out["hours"] = ("Her working hours: %s" % window_text(over).replace("-", " to ")) if over else "Not set"
    return out


IDS_HOUSE = ("room", "hours")       # round 39: the sections the User may edit on a new person's run


def ids_new_order_on() -> bool:
    """Round 39 N9 + N6: a new person's page in the User's order, the seat on the dial. Kill CRISSA_IDS_NEW_ORDER=0."""
    return os.environ.get("CRISSA_IDS_NEW_ORDER", "1").strip().lower() not in ("0", "off", "false", "no")


def IDS_HOUSE_NOW() -> tuple:
    """The house sections the User may Edit on a new person's run: Your hours (Your room is the person's seat now); with
    the new order off, round 39 N5's room and hours."""
    if not ids_collapse_edit_on():
        return ()
    return ("hours",) if ids_new_order_on() else IDS_HOUSE


def ids_dial(pid: str) -> dict[str, Any]:
    """The room clock for a new person's seat (N6): every other registration at its hour, greyed on the page - the house's
    marks (the User's seat, speakers, camera, door) and every other person's usual seat; several on one hour is fine."""
    marks: list[dict[str, Any]] = []
    labels = {"seat": "You", "speakers": "Speakers", "door": "Door"}
    try:
        owner_name = stored_user_name() or "You"
    except Exception:  # noqa: BLE001
        owner_name = "You"
    for mk, h in ([("seat", USER_HOUR)] + list((room_saved().get("marks") or {}).items())):   # the User at 6, always
        hh = _hour(h)
        if hh is not None:
            marks.append({"hour": int(hh), "label": owner_name if mk == "seat" else labels.get(mk, mk), "kind": mk})
    try:
        for pj in sorted((identity_root() / "people").glob("*/person.json")):
            if pj.parent.name in (pid, _owner_id()):        # the person being added; the User (his seat is a mark)
                continue
            rec = _load_json(pj)
            hh = _hour(rec.get("seat_hour")) if rec.get("seat_hour") not in (None, "") else None
            if hh is not None:
                marks.append({"hour": int(hh), "label": str(rec.get("display") or pj.parent.name), "kind": "person"})
    except OSError:
        pass
    seat = _load_json(identity_root() / "people" / pid / "person.json").get("seat_hour") if pid else None
    # round 39 (the User: "house's speakers and door editable under User name only. grey out with hover to edit user ID
    # to change office"): the tip on a greyed office mark names where it is edited - his own file, by his stored name
    tip = "Office marks are edited in %s's ID: Add/Edit IDs > %s" % (owner_name, owner_name)
    return {"marks": marks, "seat": _hour(seat) if seat not in (None, "") else None, "office_tip": tip}


def ids_collapse_edit_on() -> bool:
    return os.environ.get("CRISSA_IDS_COLLAPSE_EDIT", "1").strip().lower() not in ("0", "off", "false", "no")


def ids_view(st: dict[str, Any]) -> dict[str, Any]:
    ids = dict(st.get("ids") or {})
    if not ids:
        return {}
    out = {k: ids.get(k) for k in ("who", "name", "pid", "stage", "redo", "seat", "need_password", "why", "proof",
                                   "dup")}
    out["sections"] = ["you", "vid"] + (["room", "hours"] if ids.get("who") == "me" or ids_collapse_edit_on() else [])
    out["labels"] = dict(IDS_LABELS)
    if ids.get("who") == "new" and ids_new_order_on():
        # round 39 (.101 round 38 N9 + N6, the User: "Just do Voice and Face ID first, delete the a new person section";
        # "grey out user and speaker and any other regs in the dial. Only new ID and in any seat even if taken by
        # someone else. Iow, dial spot can hold multiple usual IDs."): Voice and Face ID is the first section and holds
        # the Ready plate; Your room is the person's usual seat on the dial inside it (every other registration shown
        # greyed); This computer and Your hours fold (hours keeps the User's Edit)
        out["sections"] = ["vid", "hours"]
        out["drop"] = ["you", "room"]
        out["vid_title"] = "Voice and Face ID"
        out["dial"] = ids_dial(str(ids.get("pid") or ""))
    out["editable"] = list(IDS_HOUSE_NOW()) if ids.get("who") == "new" else []
    # round 39 (.101 round 38 N5, the User: "just keep the irrelevant sections collapsed but for User editable"): the
    # sections a run does not walk are collapsed, not greyed. On a NEW person's run the house sections (Your room, Your
    # hours) are on the page folded, each with Edit for the User before his Ready; what he picks is walked first under
    # his proof, then the handover (his own file walks them already, with its Redo keys). A visitor never sees Edit.
    # Kill CRISSA_IDS_COLLAPSE_EDIT=0 (round 38: a new person's run has no house sections).
    out["collapse_edit"] = ids_collapse_edit_on()
    # round 39 (N11): one exit on an IDs run (the page's Back to the Wall; Esc and the window's X the same). Kill
    # CRISSA_IDS_ONE_EXIT=0 (the bar's copy as well, round 38)
    out["one_exit"] = os.environ.get("CRISSA_IDS_ONE_EXIT", "1").strip().lower() not in ("0", "off", "false", "no")
    out["handover"] = IDS_HANDOVER % str(ids.get("name") or "the person") if ids.get("who") == "new" else ""
    if ids.get("who") == "me":
        out["summaries"] = _ids_summaries(str(ids.get("pid") or _owner_id()))
    return out


def start_ids(name: str, *, led_by: str = "ids", proof: str = "") -> dict[str, Any]:
    """ADD/EDIT IDS AND ROOM: the run opens on the one page in her window and waits for the User's Ready. Nothing is
    recorded, no still taken and no proof asked before that press. {"ok", "who", "stage", ...} or the refusal.
    `proof` (round 41, F3): the menu's token; his proof there is this run's, so a new person's page opens on the
    handover and his own file's Ready asks nothing again."""
    if crissa_home.is_house():
        return {"ok": False, "error": "the house has its own Register"}
    if not ids_flow_on():
        return {"ok": False, "error": "Add/Edit IDs is off here (CRISSA_IDS_FLOW=0)"}
    st = load_state()
    if st.get("status") == "running":
        return dict(snapshot(st), ok=False, error="Setup is already running: finish it there first.")
    if not _owner_id():
        return {"ok": False, "error": "Run Setup first: Crissa adds people once she knows you."}
    display = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
    if not looks_like_a_name(display):
        return {"ok": False, "error": "Type a name. A first name is fine.", "name_error": True}
    import crissa_menu
    who = crissa_menu.resolve_name(display, again=True)["who"]      # a kept name is that person again (the menu asked)
    if who == "me":
        pid, display = _owner_id(), stored_user_name() or display
    else:
        pid = visitor_id_for(display)
        who = "new"
    seq = steps()
    i = next((k for k, s_ in enumerate(seq) if s_.get("id") == "voice_id"), None)
    if i is None:
        return dict(snapshot(st), ok=False, error="this install's walk has no Voice ID step")
    carried = crissa_menu.take_ids_proof(proof)
    new = empty_state()
    new.update(status="running", run_id=uuid.uuid4().hex[:12], index=i, step_id="voice_id", started_at=_now(),
               step_at=time.time(), first_boot=False, only=["voice_id"], connect=dict(st.get("connect") or {}),
               again={"replace": False, "proof_hold": False, "led_by": str(led_by or "")[:40]},
               ids={"who": who, "name": display, "pid": pid, "stage": "ready_user", "redo": [], "seat": None,
                    "need_password": False, "why": "", "proof": "", "carried": carried})
    if who == "new":
        new["visitor"] = {"person_id": pid, "display": display, "at": _now()}
    if ids_cancel_restores_on():
        new["ids"]["created"] = not _ids_keep_before(new["run_id"], pid)    # round 41 (F6): before anything is written
    save_state(new)
    if who == "new":
        _mark_visitor(pid, display)                   # the file is open under this name: it cannot be renamed
    mark_led(led_by)
    print("[init] Add/Edit IDs: %s (%s, %s) waits for the User's Ready" % (display, pid, who), flush=True)
    if ids_takes_plate_on() and lines_on():
        host_arrive(new["run_id"])                # round 42 (R41-2): she walks on as Setup's walk-on does
    if carried and who == "new":
        ids_ready(new["run_id"])                  # round 41 (F3): proven on the menu - straight to the handover
    if not SYNC:
        threading.Thread(target=show_card, args=(new["run_id"],), name="init-ids-window", daemon=True).start()
    st_ = load_state()
    return dict(snapshot(st_), ok=True, who=who, name=display,
                stage=str((st_.get("ids") or {}).get("stage") or "ready_user"))


def ids_redo(run_id: str, section: str, on: bool = True) -> dict[str, Any]:
    """The User's file: which completed sections he wants to do again (chosen before his Ready; the run walks them)."""
    st = load_state()
    ids = st.get("ids") or {}
    if not ids or st.get("run_id") != run_id:
        return {"ok": False, "error": "no Add/Edit IDs run"}
    allowed = IDS_SECTIONS_ME if ids.get("who") == "me" else IDS_HOUSE_NOW()
    if not allowed:
        return {"ok": False, "error": "only your own file has Redo keys"}
    if ids.get("stage") != "ready_user":
        return {"ok": False, "error": "the run has started: Redo is chosen before Ready"}
    if section not in allowed:
        return {"ok": False, "error": "no such section: %s" % section}

    def fn(s: dict[str, Any]) -> None:
        redo = [x for x in (s["ids"].get("redo") or []) if x != section]
        if on:
            redo.append(section)
        s["ids"]["redo"] = [x for x in allowed if x in redo]
    _mutate(run_id, fn)
    return {"ok": True, "redo": list((load_state().get("ids") or {}).get("redo") or [])}


def ids_seat(run_id: str, hour: Any) -> dict[str, Any]:
    """A new person's seat on the room clock (optional, for the beamforming microphone): their own person.json."""
    st = load_state()
    ids = st.get("ids") or {}
    if not ids or st.get("run_id") != run_id or ids.get("who") != "new":
        return {"ok": False, "error": "no new person is being added"}
    h = _hour(hour) if hour not in (None, "", "skip") else None
    if hour not in (None, "", "skip") and h is None:
        return {"ok": False, "error": "An hour on the clock is 1 to 12."}
    pj = identity_root() / "people" / str(ids.get("pid")) / "person.json"
    rec = _load_json(pj)
    if h is None:
        rec.pop("seat_hour", None)
    else:
        rec["seat_hour"] = int(h)
    rec.setdefault("id", str(ids.get("pid")))
    _write_json(pj, rec)
    _mutate(run_id, lambda s: s["ids"].update(seat=h))
    return {"ok": True, "seat": h}


def ids_ready(run_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """The Ready press. Stage ready_user: the User's proof (his face through presence, or the login password typed on
    the page), then the hold, then his own takes or the handover plate. Stage handover: the guest's Ready, the takes."""
    body = body or {}
    st = load_state()
    ids = st.get("ids") or {}
    if not ids or st.get("run_id") != run_id or st.get("status") != "running":
        return {"ok": False, "error": "no Add/Edit IDs run"}
    stage = str(ids.get("stage") or "")
    if stage == "ready_user":
        import crissa_menu
        carried = str(ids.get("carried") or "")
        if carried:                               # round 41 (F3): his proof on the menu screen, used once
            _mutate(run_id, lambda s: s["ids"].update(carried=""))
            pr = {"ok": True, "how": "%s on the menu" % carried}
        else:
            pw = body.get("password")
            pr = crissa_menu.person_proof(password=str(pw) if pw else None, wait=True)
            pw = None
        if not pr.get("ok"):
            # round 39 (.101 round 38 N13): a refused Ready says so in the log, once a press
            print("[init] Add/Edit IDs: the User's proof refused (%s)" % (str(pr.get("why") or "no reason given")), flush=True)
            _mutate(run_id, lambda s: s["ids"].update(need_password=bool(pr.get("password")), why=str(pr.get("why") or "")))
            return {"ok": False, "error": str(pr.get("why") or "Crissa could not tell it is you."),
                    "password": bool(pr.get("password")), "stage": "ready_user"}
        import crissa_registration
        crissa_registration.set_hold(_home(), run_id, str(ids.get("who")), str(ids.get("name") or ""))
        print("[init] Add/Edit IDs: the User is ready (proof %s); the continual auth stands down" % pr.get("how"), flush=True)
        house = [x for x in IDS_HOUSE_NOW() if x in (ids.get("redo") or [])] if ids.get("who") == "new" else []
        if house:
            # round 39 (N5): the house sections he chose, walked now while it is his proof at the desk; their end
            # hands over to the person (_advance), never the end of the run
            _mutate(run_id, lambda s: (s.update(only=list(house)), s["ids"].update(
                stage="user_edits", need_password=False, why="", proof=str(pr.get("how") or ""))))
            print("[init] Add/Edit IDs: the User edits %s first" % ", ".join(house), flush=True)
            _ids_begin(run_id, house[0])
            return {"ok": True, "stage": "user_edits", "only": house}
        if ids.get("who") == "new":
            _mutate(run_id, lambda s: s["ids"].update(stage="handover", need_password=False, why="", proof=str(pr.get("how") or "")))
            return {"ok": True, "stage": "handover", "handover": IDS_HANDOVER % str(ids.get("name") or "the person")}
        redo = [x for x in IDS_SECTIONS_ME if x in (ids.get("redo") or [])]
        if not redo:
            _mutate(run_id, lambda s: s["ids"].update(stage="done", proof=str(pr.get("how") or "")))
            print("[init] Add/Edit IDs: nothing chosen to redo; the file stays as it was", flush=True)
            _finish(run_id)
            return {"ok": True, "stage": "done", "note": "Nothing to redo: your file stays as it was."}
        _mutate(run_id, lambda s: (s.update(only=list(redo)), s["ids"].update(stage="takes", proof=str(pr.get("how") or ""))))
        _ids_begin(run_id, redo[0])
        return {"ok": True, "stage": "takes", "only": redo}
    if stage == "handover":
        hit = _ids_dup_hit(ids) if (ids_dup_check_on() and not ids.get("dup_ok")) else {}
        if hit:
            line = IDS_DUP_ASK % hit["display"]
            _mutate(run_id, lambda s: s["ids"].update(stage="dup_ask", dup=dict(hit, ask=line)))
            print("[init] Add/Edit IDs: %s's Ready: the face at the camera is %s's (%.2f); asked: %s"
                  % (ids.get("name"), hit["pid"], hit["sim"], line), flush=True)
            return {"ok": True, "stage": "dup_ask", "ask": line}
        _mutate(run_id, lambda s: s["ids"].update(stage="takes"))
        print("[init] Add/Edit IDs: %s is ready; the takes begin" % ids.get("name"), flush=True)
        _ids_begin(run_id, "voice_id")
        return {"ok": True, "stage": "takes"}
    return {"ok": False, "error": "the run is past its Ready (%s)" % stage, "stage": stage}


# ONE FACE, ONE NAME (round 41, .101 round 40 F1: the User sat for "ted" himself, and his face under two names locked
# him out of face and voice ID; the User's words for the check: "Is this the user? We're getting a hit on an existing
# person"). At a new person's Ready, presence names the faces at the camera (POST /v1/people/who, at its match bar):
# a kept person there - not the one being added - turns the Ready into that question. Yes ends the run (nothing saved);
# No goes on to the takes. And at the end of a new person's takes, a still whose numbers match another kept person at
# that bar is not kept. Kill CRISSA_IDS_DUP_CHECK=0.
IDS_DUP_ASK = "Is this %s? We're getting a hit on an existing person."


def ids_dup_check_on() -> bool:
    return os.environ.get("CRISSA_IDS_DUP_CHECK", "1").strip().lower() not in ("0", "off", "false", "no")


def face_bar() -> float:
    """Presence's own face match bar (crissa_presence.sweep.MATCH_SIM: the same dials, the same order, the same measured
    default - genuine min 0.540 / impostor max 0.354, the bar in that gap); a test pins the two equal."""
    try:
        return float(os.environ.get("RAIL_PRESENCE_MATCH_SIM") or os.environ.get("CRISSA_FACE_MATCH") or "0.45")
    except ValueError:
        return 0.45


def _people_who() -> dict[str, Any]:
    req = urllib.request.Request(crissa_ports.url("presence") + "/v1/people/who", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with crissa_egress.urlopen(req, 12.0, what="Add/Edit IDs: who is at the camera") as r:
        d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
    return d if isinstance(d, dict) else {}


def _ids_dup_hit(ids: dict[str, Any]) -> dict[str, Any]:
    """{"pid", "display", "sim"} of a kept person at the camera who is not the person being added, else {}."""
    pid = str(ids.get("pid") or "")
    try:
        got = _people_who()
    except Exception as e:  # noqa: BLE001  presence down: the takes go on, and the end-of-run check still holds
        print("[init] Add/Edit IDs: who is at the camera was not checked (%s)" % type(e).__name__, flush=True)
        return {}
    best: dict[str, Any] = {}
    for f in got.get("faces") or []:
        name = str((f or {}).get("name") or "").strip().lower()
        try:
            sim = float((f or {}).get("sim") or 0.0)
        except (TypeError, ValueError):
            continue
        if name and name != pid and sim >= face_bar() and sim > float(best.get("sim") or 0.0):
            disp = stored_user_name() if name == _owner_id() else str(_person_rec(name).get("display") or name)
            best = {"pid": name, "display": disp or name, "sim": round(sim, 3)}
    return best


def ids_dup(run_id: str, same: bool) -> dict[str, Any]:
    """The answer to IDS_DUP_ASK: same (Yes) ends the run, nothing saved; not same (No) goes on to the takes."""
    st = load_state()
    ids = st.get("ids") or {}
    if not ids or st.get("run_id") != run_id or ids.get("stage") != "dup_ask":
        return {"ok": False, "error": "nothing is being asked"}
    dup = ids.get("dup") or {}
    if same:
        print("[init] Add/Edit IDs: yes, it is %s: the run ends, nothing saved" % dup.get("pid"), flush=True)
        abort(run_id, why="It is %s, who is already kept: nothing was saved." % (dup.get("display") or dup.get("pid")))
        return {"ok": True, "stage": "ended"}
    _mutate(run_id, lambda s: s["ids"].update(stage="takes", dup_ok=True))
    print("[init] Add/Edit IDs: not %s: %s's takes begin" % (dup.get("pid"), ids.get("name")), flush=True)
    _ids_begin(run_id, "voice_id")
    return {"ok": True, "stage": "takes"}


def _unit(v: Any) -> list[float] | None:
    if not isinstance(v, list) or len(v) < 8:
        return None
    try:
        xs = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    n = sum(x * x for x in xs) ** 0.5
    return [x / n for x in xs] if n > 1e-8 else None


def _face_vecs(pid: str) -> list[tuple[Path, list[float]]]:
    out = []
    for f in sorted((identity_root() / "people" / pid / "face").glob("*.json")) if pid else []:
        rec = _load_json(f)
        u = _unit(rec.get("encoding") or rec.get("embedding"))
        if u is not None:
            out.append((f, u))
    return out


def _ids_drop_others_stills(pid: str) -> int:
    """A new person's stills that match another kept person at the bar are not kept (their JSON and picture)."""
    mine = _face_vecs(pid)
    if not mine:
        return 0
    people = identity_root() / "people"
    banks = {p.name: [u for _f, u in _face_vecs(p.name)] for p in sorted(people.iterdir())
             if p.is_dir() and p.name != pid and not p.name.startswith((".", "_"))} if people.is_dir() else {}
    bar, n = face_bar(), 0
    for f, u in mine:
        hit, hs = "", 0.0
        for name, vecs in banks.items():
            for v in vecs:
                if len(v) == len(u):
                    s_ = sum(a * b for a, b in zip(u, v))
                    if s_ > hs:
                        hit, hs = name, s_
        if hit and hs >= bar:
            for g in (f, f.with_suffix(".jpg"), f.with_suffix(".png")):
                try:
                    g.unlink()
                except OSError:
                    pass
            n += 1
            print("[init] Add/Edit IDs: face still %s looked like %s (%.2f), not %s: not kept" % (f.stem, hit, hs, pid),
                  flush=True)
    return n


def _ids_begin(run_id: str, first: str) -> None:
    """The takes start: the run moves onto its first chosen step and the routine's shared beginning runs now (Lila on
    her plate, the guard), never before the Ready press."""
    seq = steps()
    i = next((k for k, s_ in enumerate(seq) if s_.get("id") == first), None)
    st = _mutate(run_id, lambda s: s.update(index=i if i is not None else s.get("index"), step_id=first, step_at=time.time()))
    if st is None:
        return
    _person_begin(run_id)
    _enter(run_id)
    if first == "voice_id":
        name = str((st.get("ids") or {}).get("name") or "")
        got = reg_name(_reg_io(run_id, "voice_id", "register"), name)
        if not got.get("ok"):
            print("[init] Add/Edit IDs: the name was not taken (%s)" % got.get("error"), flush=True)


def ids_redo_wants(sid: str) -> bool:
    """A step the User chose to do again on his own file: its 'already saved' shortcut does not apply."""
    st = load_state()
    ids = st.get("ids") or {}
    return bool(ids) and ids.get("who") == "me" and sid in (ids.get("redo") or [])


def _forget_person(pid: str) -> bool:
    """A person's folder and their row in the index (a cancelled Add before any print was banked)."""
    people = identity_root() / "people"
    d = people / pid
    try:
        if not pid or pid == _owner_id() or not d.is_dir() or d.resolve().parent != people.resolve():
            return False
        import shutil
        shutil.rmtree(d)
    except OSError:
        return False
    _drop_index_row(pid)
    return True


# A CANCELLED RUN SAVES NOTHING (round 41, .101 round 40 F6: two cancelled runs for a new person left face/001-004.jpg and the
# ledger - the User's face each time - because only a person with nothing banked was forgotten). start_ids copies the
# person's folder as it was (<state home>/cache/ids-before/<run>/) or marks the person as made by this run; a cancel
# forgets a person the run made and puts a kept person's folder back as it was, at once and again after
# CANCEL_SECOND_PASS_S (the coach may still write as its cancel lands). The end of a run drops the copy.
# Kill CRISSA_IDS_CANCEL_RESTORES=0 (round 36: only a person with nothing banked is forgotten).
def ids_cancel_restores_on() -> bool:
    return os.environ.get("CRISSA_IDS_CANCEL_RESTORES", "1").strip().lower() not in ("0", "off", "false", "no")


def _ids_before_dir(run_id: str) -> Path:
    return _home() / "cache" / "ids-before" / str(run_id)


def _ids_keep_before(run_id: str, pid: str) -> bool:
    """Copy the person's folder before the run (True), or nothing to copy: the run makes them (False)."""
    import shutil
    d = identity_root() / "people" / pid
    if not d.is_dir():
        return False
    dst = _ids_before_dir(run_id)
    try:
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(d, dst, symlinks=False)
    except OSError as e:
        print("[init] Add/Edit IDs: %s's folder could not be copied before the run (%s)" % (pid, type(e).__name__),
              flush=True)
    return True


def _ids_put_back(run_id: str, pid: str, created: bool) -> str:
    """The person as before the run: forgotten (made by it) or their folder put back. '' when nothing was done."""
    import shutil
    people = identity_root() / "people"
    d = people / pid
    if not pid or d.resolve().parent != people.resolve():
        return ""
    if created:
        if pid != _owner_id() and d.is_dir() and _forget_person(pid):
            return "forgotten"
        return ""
    src = _ids_before_dir(run_id)
    if not src.is_dir():
        return ""
    try:
        if d.is_dir():
            shutil.rmtree(d)
        shutil.copytree(src, d, symlinks=False)
    except OSError as e:
        print("[init] Add/Edit IDs: %s's folder could not be put back (%s)" % (pid, type(e).__name__), flush=True)
        return ""
    return "put back"


def _ids_drop_before(run_id: str) -> None:
    import shutil
    try:
        shutil.rmtree(_ids_before_dir(run_id))
    except OSError:
        pass


def _ids_ended(st: dict[str, Any], cancelled: bool) -> None:
    """The run is over, done or cancelled: the hold goes, the proof's Secure hold goes, and a cancelled new person with
    nothing banked is forgotten (the User: "a cancelled setup ID should not" run again, "simply not saved")."""
    ids = st.get("ids") or {}
    import crissa_registration
    crissa_registration.clear_hold(_home(), str(st.get("run_id") or ""))
    try:
        import crissa_menu
        crissa_menu.release_proof()
        crissa_menu.clear_ids_proof()             # round 41 (F3): a held menu proof never outlives its run
    except Exception:  # noqa: BLE001
        pass
    rid, pid = str(st.get("run_id") or ""), str(ids.get("pid") or "")
    if cancelled and "created" in ids and ids_cancel_restores_on():
        created = bool(ids.get("created"))

        def put_back(second: bool = False) -> None:
            how = _ids_put_back(rid, pid, created)
            if how:
                print("[init] Add/Edit IDs cancelled: %s %s%s (nothing saved)"
                      % (pid, how, " again" if second else ""), flush=True)
                reload_banks("%s %s" % (pid, how))
            if second or SYNC:
                _ids_drop_before(rid)
        put_back()

        def again() -> None:
            _sleep(CANCEL_SECOND_PASS_S)
            put_back(second=True)
        if SYNC:
            pass
        else:
            threading.Thread(target=again, name="init-ids-cancel-second-pass", daemon=True).start()
    elif cancelled and ids.get("who") == "new":
        if pid and voice_prints(pid) == 0 and face_prints(pid) == 0 and is_visitor(pid):
            if _forget_person(pid):
                print("[init] Add/Edit IDs cancelled: %s forgotten (nothing was banked)" % pid, flush=True)
                reload_banks("%s forgotten" % pid)
    elif not cancelled:
        _ids_drop_before(rid)
    if not cancelled and ids.get("who") == "new" and ids_dup_check_on() and pid:
        if _ids_drop_others_stills(pid):
            reload_banks("%s: stills of someone else dropped" % pid)
    drop_lila_cues("the IDs run ended")                       # R45-1
    print("[init] Add/Edit IDs %s: the continual auth resumes" % ("cancelled" if cancelled else "done"), flush=True)


def open_krew_link(opener=None) -> dict[str, Any]:
    """THE KREW LINK OPENS IN THE PERSON'S OWN BROWSER (the User 2026-10-29: "let repo link offer in User browser"):
    Setup runs in her window, where a plain link opened a tab of hers. The player opens the CONFIGURED address only
    (setup-films.json krew_url, never one the page sends) with the person's default browser. {"ok", "url"}; ok False =
    the page opens the link itself, as before. Kill CRISSA_KREW_LINK_DEFAULT_BROWSER=0."""
    url = krew_url()
    if not url or os.environ.get("CRISSA_KREW_LINK_DEFAULT_BROWSER", "1").strip().lower() in ("0", "off", "false", "no"):
        return {"ok": False, "url": url}
    try:
        if opener is None:
            import webbrowser
            opener = webbrowser.open
        return {"ok": bool(opener(url)), "url": url}
    except Exception as e:  # noqa: BLE001  the page's own link is the way then
        return {"ok": False, "url": url, "error": type(e).__name__}


def apply(action: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body = body or {}
    act = (action or "").strip().lower()
    if act in ("snapshot", "", "attend"):
        return snapshot()
    if act == "open_krew":                        # Setup's krew link, in the person's own browser (above)
        return open_krew_link()
    if act in APPLY_LOCKED or (act == "mic" and bool(body.get("on", True))):
        refused = _face_refused("setup_" + act)      # Security On: his face first (above)
        if refused is not None:
            return dict(snapshot(), **refused)
    if act in ("start", "restart"):
        if body.get("if_not_running") and load_state().get("status") == "running" and \
                os.environ.get("CRISSA_RUN_SETUP_ONCE", "1").strip().lower() not in ("0", "off", "false", "no"):
            # RUN SETUP STARTS ONE WALK (the User 2026-10-26: three presses of the Wall's RUN SETUP, three walks, each
            # copying the last one's records): a press while a walk runs is that walk. Kill CRISSA_RUN_SETUP_ONCE=0.
            print("[init] %s pressed while Setup runs: the running walk stands" % (body.get("by") or "a restart"),
                  flush=True)
            return snapshot()
        return start(force=bool(body.get("force")) or act == "restart")
    if act == "talk":
        return talk(body)
    if act == "voice_install":                    # the Connect page's retry, or the plate's RETRY key
        return _standin_voice_install()
    if act == "mic":                              # the done plate's key: her microphone on (or off), any time
        on = bool(body.get("on", True))
        if not on and load_state().get("status") == "running":
            st0 = load_state()                    # MIC ON AT THE END keeps a "no" said during the run
            _mutate(str(st0.get("run_id") or ""), lambda s: s.update(mic_off_by_person=_now()))
        return dict(snapshot(), **mic(on))
    if act == "login":                            # the last step's choice: start Crissa when I log in
        return dict(snapshot(), **login(bool(body.get("on"))))
    if act == "to_wall":                          # the done plate's Go to the Wall when the Wall is the desktop (round 15)
        return dict(snapshot(), **to_desk_wall())
    if act == "cues":                             # Setup's cues: spoken (default) or text only (2026-10-10)
        return dict(snapshot(), **set_cues(str(body.get("mode") or "")))
    if act == "mic_pick":                         # Voice ID's microphone: which one she listens and records on
        return dict(snapshot(), **set_mic_source(str(body.get("source") or "")))
    if act == "mic_setting":                      # round 20: This computer's Mic off (on by default at Setup's end)
        return dict(snapshot(), **mic_setting(bool(body.get("off"))))
    if act == "voice_id_again" and load_state().get("status") != "running":   # the done plate's key (Y, 2026-10-10)
        # THE SAME GATE AS THE MENU (DD, 2026-10-10): with the User's prints on file it needs the face or the login
        # password; it never adds to them for an unverified speaker
        import crissa_menu
        return dict(snapshot(), **crissa_menu.act("voice_id", {"password": body.get("password"), "page": "/init",
                                                               "name": body.get("name") or ""}))
    st = load_state()
    rid = str(body.get("run_id") or "")
    if st.get("status") != "running":
        return dict(snapshot(st), ok=False, error="not running")
    if act in ("abort", "cancel", "later"):
        return abort(rid or st.get("run_id") or "", why=str(body.get("why") or "")[:200],
                     by=str(body.get("by") or "a POST %s with no sender" % act)[:80])
    if act == "ids_ready":                        # round 36: the Ready press (the User's, then the guest's)
        return dict(snapshot(), **ids_ready(rid or str(st.get("run_id") or ""), body))
    if act == "ids_redo":
        return dict(snapshot(), **ids_redo(rid or str(st.get("run_id") or ""), str(body.get("section") or ""), bool(body.get("on", True))))
    if act == "ids_seat":
        return dict(snapshot(), **ids_seat(rid or str(st.get("run_id") or ""), body.get("hour")))
    if act == "ids_dup":                          # round 41 (F1): "Is this <name>?" - Yes ends the run, No goes on
        return dict(snapshot(), **ids_dup(rid or str(st.get("run_id") or ""), bool(body.get("same"))))
    if act == "film_start":                       # ONE-PAGE SETUP: one of her films is on the air (the listener waits)
        try:
            secs = max(0.0, min(30.0, float(body.get("seconds") or 0.0)))
        except (TypeError, ValueError):
            secs = 0.0
        _Q["talking_until"] = max(float(_Q["talking_until"]), time.time() + secs + 0.6)
        wall = _ids_wall_film(str(body.get("id") or "")[:40])   # round 43: the IDs run's Wall starts with her film
        return {"ok": True, **({"wall": wall} if wall else {})}
    if act == "film_word":                        # .101 round 29: a cued film's last word (her handover's)
        return film_word(body.get("seq"))
    if act == "film_said":                        # ONE-PAGE SETUP: the page played one of Lila's films to its end
        return dict(snapshot(), **film_said(body.get("seq"), str(body.get("id") or "")[:40],
                                            st.get("run_id") if rid == st.get("run_id") else ""))
    if act == "cancel_ask":                       # the desk Wall's Cancel asks first (the User 2026-10-11, JJ)
        got = cancel_ask(st.get("run_id") or "", back=bool(body.get("back")))
        return dict(snapshot(), **got)
    if rid != st.get("run_id"):
        return dict(snapshot(st), ok=False, error="stale run")
    sid = st.get("step_id") or ""
    out: dict[str, Any] = {"ok": True}
    if act == "connect_test" and sid == "connect":
        out = connect_test(rid, body)
    elif act == "connect_adopt" and sid == "connect":
        out = connect_adopt(rid, str(body.get("id") or ""))
    elif act == "connect_continue" and sid == "connect":
        out = connect_continue(rid)
    elif act == "connect_later" and sid == "connect":
        out = connect_later(rid, str(body.get("by") or ""))
    elif act == "connect_later_ask" and sid == "connect":
        out = connect_later_ask(rid, bool(body.get("back")))
    elif act == "machine" and sid == "machine":
        out = machine_act(rid, body)
    elif act == "language":
        out = language_answer(rid, str(body.get("language") or ""))
    elif act == "name" and sid == "voice_id":
        out = reg_name(_reg_io(rid, "voice_id", "register"), str(body.get("text") or ""))
    elif act in ("register", "voice_id") and sid == "voice_id":
        out = reg_act(_reg_io(rid, "voice_id", "register"), str(body.get("verb") or ""))
    elif act == "face" and sid == "face":
        out = face_act(_reg_io(rid, "face", "face"), str(body.get("verb") or ""))
    elif act == "room" and step_cfg(sid).get("kind") == "room":
        out = room_act(_reg_io(rid, sid, "room"), str(body.get("verb") or ""), body)
    elif act == "hours" and step_cfg(sid).get("kind") == "hours":
        out = hours_act(_reg_io(rid, sid, "hours"), str(body.get("verb") or ""), body)
    else:
        out = {"ok": False, "error": f"{act} is not an action for {sid or 'this step'}"}
    return dict(snapshot(), **{k: v for k, v in out.items() if k != "plate"})
