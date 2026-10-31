"""First-boot User wizard.

Sequence: $HOUSE_ID_ROOT/site/init-sequence.json for a house that has walked
init, else the one shipped at config/init-sequence.json.

Glass is the VoiceOnly wall. This module is the walker + writes.
This house already initialized — needed() is false when master init-state
last_phase is done. Re-run: POST /v1/init {action:start, force:true}
or wall ?init=user.
"""
from __future__ import annotations

from . import paths as _paths
from . import pack_store

# THE PACK'S ONE WALK (the User 2026-10-10): Connect, the machine card, Voice ID, face photos, done. It is
# lib/crissa_init.py's (the player's): every public entry here (apply, snapshot, needed, start, abort, attend) hands
# over to it, so this module's 13-step routine never walks in a pack. Its sequence file (config/init-sequence.json)
# names the same four steps; the helpers below stay as they were extracted (the Register routine and the coach are
# this module's house_enroll, which the walk drives through the coach's HTTP surface).
ON_DONE: Any = None

import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

def _identity_root() -> Path:
    env = (os.environ.get("HOUSE_ID_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    sec = _paths.identity_root()
    if (sec / "people").is_dir() or (sec / "site.json").is_file():
        return sec
    legacy = _paths.identity_root()
    if legacy.is_dir() and (legacy / "people").is_dir():
        return legacy
    return sec


HOUSE_ID = _identity_root()
SEQ_FILE = Path(
    os.environ.get(
        "HOUSE_INIT_SEQUENCE",
        str(HOUSE_ID / "site" / "init-sequence.json"),
    )
)
# The sequence shipped with the product. A house that has walked init keeps its
# own copy under $HOUSE_ID_ROOT/site and that copy wins; a house that has never
# been initialized has no copy at all, and until 2026-08-25 the walker read an
# empty list there and started a wizard with nothing in it. The order of the
# routine is product, so it lives in the repo and travels with it.
SEQ_SHIPPED = _paths.MODULE / "config" / "init-sequence.json"
STATE_FILE = Path(
    os.environ.get(
        "HOUSE_INIT_WIZARD",
        str(HOUSE_ID / "site" / "init-wizard.json"),
    )
)
MASTER_STATE = HOUSE_ID / "site" / "init-state.json"
SITE_FILE = HOUSE_ID / "site.json"
SPEAK_BIN = _paths.PACK / "scripts" / "house-speak.py"
STT = os.environ.get("HOUSE_STT_URL") or _paths.stt_url().rstrip("/")
PRESENCE = os.environ.get("HOUSE_PRESENCE_URL") or _paths.presence_url().rstrip("/")
MEDIA = os.environ.get("HOUSE_MEDIA_LOCUS") or _paths.stt_url() + "/v1/media_locus"

SCHEMA = "house_init_wizard/v1"
_SLUG = re.compile(r"[^a-z0-9_\-]+")
_speak_proc: subprocess.Popen | None = None
_last_speak: tuple[float, str] = (0.0, "")
_HOLD = None
HOLD_OWNER = "init_wizard"
HOLD_SEC = 1800.0
# How long the wizard may go unseen before the hold is dropped. The glass polls
# every 2s while it is showing a plate, so anything past a couple of minutes
# means the wall navigated away, reloaded into something else, or died — and
# the house should be able to hear again without waiting out the lease.
GLASS_GRACE = float(os.environ.get("HOUSE_INIT_GLASS_GRACE", "120"))
# ONE WRITER AT A TIME, AND A STALE COPY NEVER UNDOES A CANCEL (the User 2026-09-13:
# "cancel needs to work. It does not now"). Measured 07:26-07:27: Cancel wrote
# `aborted`, a `listen` that had loaded the state before it came back from the
# ear 1 s later and saved `running`, and the routine registered the User. Every run
# carries a run_id; save_state refuses a running copy over a finished run.
_STATE_LOCK = threading.RLock()
TERMINAL = ("aborted", "done")
_host_proc: subprocess.Popen | None = None
_host_cancel = threading.Event()
# A PACK HAS NO BOARD AND NO AGENT RUNTIME (2026-10-09, the Crissa port): the house paused its board's dispatcher
# and named its gateway in the GPU hold for the routine; here the names stay for the state file's keys and every
# pause is a no-op (the functions below).
BOARD_UNIT = ""
BOARD_CARD = "init-wizard"
TRAIN_HOLD = Path(os.environ.get("HOUSE_TRAIN_HOLD", str(_paths.STATE / "id" / "train-hold")))
BOARD_MARKER = Path(os.environ.get("HOUSE_INIT_BOARD_MARKER", str(_paths.STATE / "id" / "board-paused.json")))
PACK_RENDER_SWITCH = False      # a pack has no Render mode to switch into for the Crissa-seat picture

def _live_side_effects() -> bool:
    """Tests run on the live box: nothing outside the wizard is touched silent."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return os.environ.get("HOUSE_INIT_SILENT", "").strip() not in ("1", "true", "yes")


def _hold_mod():
    global _HOLD
    if _HOLD is None:
        import importlib.util

        p = _paths.PACK / "scripts" / "house-tool-hold.py"
        spec = importlib.util.spec_from_file_location("house_tool_hold", p)
        if not spec or not spec.loader:
            raise ImportError("house-tool-hold.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _HOLD = mod
    return _HOLD


def _ear_was_open() -> bool:
    try:
        with urllib.request.urlopen(f"{STT}/health", timeout=1.5) as r:
            h = json.loads(r.read().decode() or "{}")
        return bool((h.get("ear") or {}).get("open"))
    except Exception:
        return False


def _silent_skips_hold() -> bool:
    """A silent Init (HOUSE_INIT_SILENT=1: a dry run, a sandbox) never touches the live ear hold - neither takes it
    nor releases it (the User 2026-10-03: a dry run's start() took the live hold and abort() could release one).
    Kill: HOUSE_INIT_SILENT_TOUCHES_HOLD=1 (silent takes and releases it again)."""
    if os.environ.get("HOUSE_INIT_SILENT_TOUCHES_HOLD", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return os.environ.get("HOUSE_INIT_SILENT", "").strip() in ("1", "true", "yes")


def _chat_hold_on() -> None:
    """Pause live Crissa chat. Scripted Crissa/the announcer still speak plates.

    Re-entrant. start() runs again on every relaunch — the kiosk_init_user
    action, a wall reload on ?init=user — and by then this hold has already
    shut the ear, so asking the ear a second time answers "it was closed" and
    the new lease would carry reopen_ear=false. The wizard then finished or
    cancelled with the house deaf until house-keeper timed the lease out
    (up to 30 min). Carry the FIRST answer forward instead.
    """
    if _silent_skips_hold():
        return
    try:
        mod = _hold_mod()
        held = mod.is_held() or {}
        ours = held.get("owner") == HOLD_OWNER or held.get("reason") == "init-user"
        was = bool(held.get("reopen_ear")) if ours else _ear_was_open()
        mod.acquire(
            reason="init-user",
            owner=HOLD_OWNER,
            sec=HOLD_SEC,
            allow_voices=["crissa", "zoe", "announcer", "lila", "host-lila"],   # Lila hosts (2026-09-12): her voice is allowed under the wizard's own hold
            close_ear=not EAR_LIVE,     # the live ear stays open; the hold keeps ear-bridge and the keeper off it
            stop_ear_bridge=False,
            reopen_ear=was,
        )
    except Exception:
        pass
    if EAR_LIVE:
        _ensure_ear_open()


def _ensure_ear_open() -> bool:
    """The continuous VAD ear cuts turns while Lila talks. The register chapter closes it
    for its own take, so every live listen asks again."""
    if not _live_side_effects():
        return False
    if _ear_was_open():
        return True
    r = _http_json(f"{STT}/v1/ear/open", EAR_LIVE_BODY, timeout=4.0)
    return bool((r or {}).get("open") or (r or {}).get("ok"))


def _hold_rows() -> list[str]:
    """Every line of the shared GPU hold, as written: other owners' stamps and unit lines are theirs."""
    try:
        return [r.strip() for r in TRAIN_HOLD.read_text(encoding="utf-8").splitlines() if r.strip()]
    except OSError:
        return []


def _hold_rows_write(rows: list[str]) -> None:
    if not [r for r in rows if not r.startswith("#")]:
        TRAIN_HOLD.unlink(missing_ok=True)
        return
    TRAIN_HOLD.parent.mkdir(parents=True, exist_ok=True)
    TRAIN_HOLD.write_text("\n".join(rows) + "\n", encoding="utf-8")


BOARD_OWNER_LINE = "# unit %s human" % BOARD_UNIT
BOARD_LIVE_STATUSES = frozenset({"running", "ready", "todo", "in_progress", "in-progress"})


def board_live_count() -> int:
    """A pack has no board: nothing is in flight (the house counted its live cards here)."""
    return 0


def _topology_pause_on() -> bool:
    return False


def _is_host() -> bool:
    return False


def _board_pause() -> bool:
    """Nothing to pause in a pack (the house stopped its board's dispatcher for the routine)."""
    return False


def _board_resume() -> bool:
    return False


def _stop_voices() -> None:
    """Cancel is instant: the host clip's audio and any live line stop now."""
    _host_cancel.set()
    for p in (_speak_proc, _host_proc):
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass


def _cancel_register_if_live(st: dict[str, Any]) -> None:
    if str(st.get("step_id") or "") in ("user_identity", "present_regulars_enroll") and _live_side_effects():
        _http_json(_paths.enroll_url() + "/cancel", {}, timeout=4.0)


def _reap_hold(st: dict[str, Any]) -> None:
    """Give the ear back the moment nothing is showing the wizard.

    Cancel and the last plate both go through _finish, but a wall that simply
    walked away — navigated to the Control Panel, reloaded into something
    else, kiosk restarted, tab closed — calls nothing, and the house stayed
    deaf until house-keeper timed the lease out half an hour later. So the
    hold follows the glass: not running, or running but unseen, and it goes.
    attend() takes it straight back when the plates come back on screen.
    """
    if str(st.get("status") or "idle") != "running":
        _chat_hold_off()
        _board_resume()          # a routine that ended (or died) gives board back
        return
    # No stamp at all means nothing has ever shown this session a plate —
    # a state left running by an older build, or one that never reached the
    # glass. Same answer either way: give the room back.
    if (time.time() - float(st.get("seen_at") or 0.0)) > GLASS_GRACE:
        _chat_hold_off()



# THE HANDOVER (2026-10-10): the pack's walk is lib/crissa_init (Init v1's 13 steps and Init v2's 8 are retired in a
# pack; HOUSE_INIT_V2 no longer chooses anything here). None only when the player's lib is not beside this module
# (the id module on its own: a probe, a test); then this module answers with its own state.
_WALK: dict[str, Any] = {"mod": None, "tried": False}


def _walk():
    if _WALK["tried"]:
        return _WALK["mod"]
    _WALK["tried"] = True
    lib = _paths.PACK / "lib"
    if not (lib / "crissa_init.py").is_file():
        return None
    try:
        import importlib
        import sys as _sys
        if str(lib) not in _sys.path:
            _sys.path.insert(0, str(lib))
        _WALK["mod"] = importlib.import_module("crissa_init")
    except Exception as e:  # noqa: BLE001
        print(f"[init] the pack's walk did not import ({type(e).__name__}: {e})", flush=True)
    return _WALK["mod"]


def _v2():
    """The old name of the handover (kept for the callers below)."""
    return _walk()

def attend() -> dict[str, Any]:
    """The wizard glass is on screen. Stamp it, and hold the room for it.

    Only the wall's wizard walker asks for this (GET /v1/init?glass=1), so a
    plain read of the wizard — a curl, the Control Panel, a health check —
    never closes the ear. An enrol session owns the mic in its own name while
    it runs; we do not take it off them.
    """
    if _v2():
        return _v2().apply("attend")
    st = load_state()
    if str(st.get("status") or "") != "running":
        return snapshot(st)
    st["seen_at"] = time.time()
    save_state(st)
    try:
        if not (_hold_mod().is_held() or {}):
            _chat_hold_on()
    except Exception:
        pass
    return snapshot(st)


def _chat_hold_off() -> None:
    """Drop the lease. Reopen STT ear only if it was open when we acquired."""
    if _silent_skips_hold():
        return
    try:
        mod = _hold_mod()
        h = mod.is_held()
        if not h:
            return
        if h.get("owner") not in (None, HOLD_OWNER) and h.get("reason") != "init-user":
            return
        mod.release(owner=str(h.get("owner") or HOLD_OWNER), force=True)
    except Exception:
        pass


def _touch() -> None:
    st = load_state()
    if str(st.get("status") or "") != "running":
        return
    st["seen_at"] = time.time()
    save_state(st)


def _finish(st: dict[str, Any], status: str = "done") -> dict[str, Any]:
    with _STATE_LOCK:
        disk = load_state()
        if (status != "aborted" and str(disk.get("status") or "") == "aborted"
                and str(disk.get("run_id") or "") == str(st.get("run_id") or "")):
            return snapshot(disk)    # a stale thread may not turn a Cancel into "done"
        st["status"] = status
        save_state(st, force=True)
    _stop_voices()
    if status == "aborted":
        _cancel_register_if_live(st)
    _chat_hold_off()
    _board_resume()
    try:
        done_ids = [s["id"] for s in steps()[: int(st.get("index") or 0)]]
        pack_store.write_marker(done=(status == "done"), steps_done=done_ids, cancelled=(status == "aborted"),
                                service=(ON_DONE() if (status == "done" and callable(ON_DONE)) else None))
    except Exception as e:  # noqa: BLE001  a record that failed never undoes the routine
        print(f"[init] marker: {type(e).__name__}: {e}", flush=True)
    if _host_end_ts > 0:
        host_home()          # Lila hosted: her plate → the desk (2026-09-12)
    return snapshot(st)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s: str) -> str:
    t = (s or "").strip().lower().replace(" ", "_")
    t = _SLUG.sub("", t)
    t = re.sub(r"[_\-]{2,}", lambda m: m.group(0)[0], t)
    return t.strip("_-")[:48]


def _read_sequence(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    steps = [s for s in (data.get("steps") or []) if isinstance(s, dict) and s.get("id")]
    if not steps:
        return None
    data = dict(data)
    data["steps"] = steps
    data["source"] = str(path)
    return data


def load_sequence() -> dict[str, Any]:
    """The routine shipped with the module (the pack's four steps). A copy in the identity store no longer wins
    (2026-10-10): a pack's routine is product, and an older install's copy would bring back the retired steps."""
    for path in (SEQ_SHIPPED,):
        data = _read_sequence(path)
        if data is not None:
            return data
    return {"schema": "init_sequence/v1", "steps": [], "source": ""}


def steps() -> list[dict[str, Any]]:
    """The routine's rows: the pack's four (config/init-sequence.json; the walk itself is lib/crissa_init's)."""
    return list(load_sequence().get("steps") or [])


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "idle",
        "index": 0,
        "step_id": "",
        "beat": 0,
        "roster": {
            "user": "",
            "user_id": "",
            "regulars": [],
            "pets": [],
            "present": [],
        },
        "enroll_queue": [],
        "enroll_who": "",
        "answers": {},
        "notes": [],
        "polarity_tries": 0,
        "started_at": None,
        "updated_at": None,
        # Last time the wall showed a plate. The mic hold follows this, not
        # the clock — see _reap_hold / attend.
        "seen_at": 0.0,
    }


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            base = empty_state()
            base.update(data)
            return base
    except (OSError, json.JSONDecodeError):
        pass
    return empty_state()


def save_state(st: dict[str, Any], *, force: bool = False) -> bool:
    """Write the wizard. A RUNNING copy never lands on a run that has finished
    (Cancel/done) or on a newer run; the caller's dict becomes what is on disk."""
    with _STATE_LOCK:
        if not force and str(st.get("status") or "") == "running":
            disk = load_state()
            same_run = str(disk.get("run_id") or "") == str(st.get("run_id") or "")
            stale = (str(disk.get("status") or "") in TERMINAL and same_run) or (
                str(disk.get("status") or "") == "running" and disk.get("run_id") and st.get("run_id") and not same_run)
            if stale:
                print("[init] stale save refused: run %s is %s" % (st.get("run_id"), disk.get("status")), flush=True)
                st.clear()
                st.update(disk)
                return False
        st["updated_at"] = _now()
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
        tmp.replace(STATE_FILE)
        return True


def master_done() -> bool:
    if pack_store.marker().get("done"):
        return True             # the pack's own marker (<state home>/init.json): a finished first boot
    try:
        data = json.loads(MASTER_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    last = str(data.get("last_phase") or "").strip().lower()
    done = data.get("completed_phases") or []
    return last == "done" or "done" in [str(x).lower() for x in done]


# FIRST BOOT, THEN RE-REGISTER (the User 2026-09-14: "Very first boot in Render mode. Re-registration must
# Use our established Render mode switch … First Boot is ONLY attempt at Register. All other attempts
# will be a mode switch unless already in Render … That would be a no Crissa intro session. Skip that
# video on re-register … First Boot only counts once the first values have been written for that
# machine"; "re-register just changes mode then. We have that keyed to Mode button. Do not freelance").
HOUSE_URL = os.environ.get("HOUSE_INIT_HOUSE_URL") or _paths.player_url().rstrip("/")
COMFY_URL = os.environ.get("HOUSE_COMFY_URL", "").rstrip("/")
GPU_MODE_FILE = Path(os.environ.get("HOUSE_GPU_MODE_FILE", str(_paths.STATE / "gpu-mode")))
COMFY_PLUGIN = _paths.STATE / "plugins" / "house-comfy" / "__init__.py"
CRISSA_REF_PLATE = _paths.PACK / "display" / "crissa-poses" / "hub-down.png"
CRISSA_SEAT_MODEL = os.environ.get("HOUSE_INIT_CRISSA_SEAT_MODEL", "qwen-edit-2511")
CRISSA_SEAT_PROMPT = ("Put the woman from the reference picture into this room: seated at a desk with a laptop, facing the "
                    "camera, hair up in a bun, dark top, relaxed. Keep the room, the camera angle and the light exactly as "
                    "they are. Photoreal.")
CRISSA_WALK_IN_KEY = "crissa-walk-in"          # first-boot bookend: curtain → door → listen → "Hi. This is nice." → work
WALK_IN_LINE = "Hi. This is nice."
WALK_IN_LISTEN_BEAT_S = 1.25               # one beat on listen before the line
WALK_IN_WAKE_S = 5.1                       # clip-trans-back (down → back)
WALK_IN_REST_S = 6.7                       # clip-scoot-in (back → down)
STANDARD_WAIT_S = 360.0
_CRISSA_SEAT: dict[str, Any] = {"state": "", "live": "", "hd": "", "error": "", "at": 0.0}
_READY_CACHE: dict[str, Any] = {"at": 0.0, "render": True}


def _first_boot_file() -> Path:
    return HOUSE_ID / "site" / "first-boot.json"


# TEMP-FIRST-BOOT (the User 2026-09-14: "throw a temp switch for me somewhere we can clean up after … a little
# tick to 'Run as First Boot'"): the Control Panel tick by New User initialization writes this flag.
def _first_boot_force_file() -> Path:
    return HOUSE_ID / "site" / "first-boot-force"


def first_boot_forced() -> bool:
    return _first_boot_force_file().is_file()


def is_first_boot() -> bool:
    """True until the first values are written on this machine (HOUSE_INIT_FIRST_BOOT=1 or the Control
    Panel's temp tick forces it)."""
    if os.environ.get("HOUSE_INIT_FIRST_BOOT", "").strip().lower() in ("1", "true", "yes") or first_boot_forced():
        return True
    return not _load_json(_first_boot_file()).get("values_at")


def _first_boot_values_stored(st: dict[str, Any]) -> None:
    """The first values are written. On a run the Control Panel tick forced, values_at is written again and
    the tick's lease ends here, and only here (the User 2026-09-14: "Leave it there until new values stored
    successfully and THEN the flag clears this tick lease")."""
    if st.get("first_boot_forced") and first_boot_forced():
        p = _first_boot_file()
        d = _load_json(p)
        d["values_at"] = _now()
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_json(p, d)
        _first_boot_force_file().unlink(missing_ok=True)
        print("[init] first-boot tick cleared: values stored on a forced first boot", flush=True)
        return
    _first_boot_mark("values_at")


def _first_boot_mark(key: str) -> None:
    p = _first_boot_file()
    d = _load_json(p)
    if not d.get(key):
        d[key] = _now()
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_json(p, d)


def _gpu_mode() -> str:
    try:
        first = GPU_MODE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return "normal"
    return first or "normal"


def _mode_action(act: str) -> dict[str, Any]:
    """The Mode button's own action on :8099 (mode_render / mode_normal): never a new switch."""
    if not _live_side_effects():
        return {}
    return _http_json(f"{HOUSE_URL}/v1/action", {"action": act}, timeout=10.0) or {}


def _url_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:
        return False


def render_ready() -> bool:
    """Render is up (the flag says render and the renderer answers). Always true in tests."""
    if not _live_side_effects():
        return True
    now = time.time()
    if now - float(_READY_CACHE["at"]) < 2.0:
        return bool(_READY_CACHE["render"])
    ok = _gpu_mode() == "render" and _url_ok(f"{COMFY_URL}/system_stats")
    _READY_CACHE.update(at=now, render=ok)
    return ok


def _no_render_wait() -> bool:
    """INIT NEVER WAITS ON RENDER TO SPEAK (the User 2026-10-02, Init step 5): when Init stays in Standard (a live board
    keeps it there) render_ready() is never true, so the wall waited forever before the first word and the run aborted at
    the welcome. On (HOUSE_INIT_NO_RENDER_WAIT=1): her lines go when they are ready; only the Crissa-seat picture still
    waits for its render. Off: unchanged."""
    return os.environ.get("HOUSE_INIT_NO_RENDER_WAIT", "1").strip().lower() in ("1", "on", "true", "yes")


def standard_ready() -> bool:
    """The brain answers: the Connect endpoint's /models (paths.brain_url, from this install's agent.json), never a
    fixed engine port (2026-10-10, the port table)."""
    base = _paths.brain_url().rsplit("/chat/completions", 1)[0].rstrip("/")
    return _gpu_mode() == "normal" and bool(base) and _url_ok(base + "/models")


def _comfy_plugin():
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("house_comfy_init", COMFY_PLUGIN)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prepare_crissa_seat() -> bool:
    """SHOT OFF AT THE ROOM SCAN, READY AT THE APPROVAL (the User 2026-09-14: "on room scan we shoot it off for
    processing and it is ready when we get to that part"): the camera at station 11, 20 degrees down;
    the frame; the renderer puts Crissa (her office plate) into it."""
    if not _live_side_effects() or _CRISSA_SEAT.get("state") in ("running", "ok"):
        return False

    def _run() -> None:
        _CRISSA_SEAT.update(state="running", error="", at=time.time())
        try:
            _http_json(f"{PRESENCE}/v1/look_at", _cam_body({"station": CRISSA_SEAT_STATION, "tilt": CRISSA_SEAT_TILT, "n": 1,
                                                 "facefinder": False, "owner": "init-crissa-seat"}), timeout=30.0)
            time.sleep(1.5)
            with urllib.request.urlopen(f"{HOUSE_URL}/frame?t={int(time.time() * 1000)}", timeout=5) as r:
                jpg = r.read()
            live = HOUSE_ID / "site" / "crissa-seat-live.jpg"
            live.write_bytes(jpg)
            _CRISSA_SEAT["live"] = str(live)
            comfy = _comfy_plugin()
            if not comfy.comfy_up():
                raise RuntimeError("the renderer is down")
            res = comfy.HouseComfyImage().generate(prompt=CRISSA_SEAT_PROMPT, aspect_ratio="landscape", model=CRISSA_SEAT_MODEL,
                                                   image_url=str(live), reference_image_urls=[str(CRISSA_REF_PLATE)])
            if not res.get("success"):
                raise RuntimeError(str(res.get("error") or "no picture")[:160])
            _CRISSA_SEAT.update(state="ok", hd=str(res.get("image") or ""))
        except Exception as e:  # noqa: BLE001
            _CRISSA_SEAT.update(state="error", error=f"{type(e).__name__}: {e}"[:160])

    threading.Thread(target=_run, name="init-crissa-seat-hd", daemon=True).start()
    return True


def needed(st: Optional[dict[str, Any]] = None) -> bool:
    """True → wall should be in the wizard.

    This house already walked master init (last_phase done). A new office
    with no done marker starts at first boot. A running session resumes.
    """
    if st is None and _walk() is not None:
        return bool(_walk().needed())
    st = st if st is not None else load_state()
    status = str(st.get("status") or "idle")
    if status == "running":
        return True
    if status == "done":
        return False
    return not master_done()


def current_step(st: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    st = st if st is not None else load_state()
    seq = steps()
    i = int(st.get("index") or 0)
    if 0 <= i < len(seq):
        return seq[i]
    return None


# ── LILA HOSTS THE WIZARD (the User 2026-09-11/12: "extend that excellence to
# the full Onboarding routine … maybe a bit more time for the elderly").
# Every plate's line is a rendered clip (audio baked in, played beside the
# conductor's frames) once the User renders it; until then the plate falls back
# to today's spoken line. docs/designs/ONBOARDING-LILA-20260911.md is the
# list. The register chapters are the subroutine (house-id, HOST=wall);
# Crissa opens (her clip through the conductor, then the dissolve chain into
# Lila) and is back at the desk when Lila hands the house over (2026-09-12).
PACE = max(0.5, float(os.environ.get("HOUSE_INIT_PACE", "1.5") or 1.5))
LISTEN_SEC = 3.0            # the one-shot ear window, × PACE
BREATH_SEC = 0.8            # after a line, before the ear opens, × PACE
LISTEN_HANGOVER_SEC = 0.8   # silence after the answer that ends the one-shot listen
# THE EAR STAYS OPEN FOR INIT (the User 2026-09-14: "why so long to record the User as name. 10s? She should
# be operating in parallel. Not taking turns"). HOUSE_INIT_EAR_LIVE=0 = the one-shot windows again.
EAR_LIVE = os.environ.get("HOUSE_INIT_EAR_LIVE", "1").strip().lower() not in ("0", "false", "no", "off")
EAR_WINDOW_SEC = 7.0        # after her line ends, how long the live ear waits for an answer, x PACE
EAR_LISTEN_CAP_SEC = 45.0   # one listen call never outlives the wall's HTTP wait (60 s)
EAR_LIVE_BODY = {"mode": "vad", "hangover_sec": 0.8, "max_utter_sec": 20, "idle_timeout_sec": 1800, "max_open_sec": 43200}
POSES_DIR = _paths.PACK / "display" / "crissa-poses"
HOST_WAV_DIR = _paths.STATE / "cache" / "id" / "host"
CONDUCTOR_URL = os.environ.get("HOUSE_CONDUCTOR_URL") or _paths.player_url().rstrip("/")
HOST_KEYS: dict[Any, Any] = {
    "welcome": ["crissa-opener", "lila-ob-welcome"],   # Crissa opens on the wall, the dissolves, then Lila (2026-09-12)
    ("regulars_roster", 0): "lila-ob-name",
    ("regulars_roster", 1): "lila-ob-regulars",
    ("regulars_roster", 2): "lila-ob-pets",
    ("regulars_roster", 3): "lila-ob-present",
    "present_regulars_enroll": "lila-ob-someone-else",
    "room_scan": "lila-ob-look-around",
    ("room_scan", "done"): "lila-ob-looked-around",   # the sweep ran under the roster; not rendered yet → her live voice + the listen loop
    "camera_orientation": "lila-ob-wave",
    ("camera_orientation", "mirrored"): "lila-ob-mirrored",
    "media_speakers": "lila-ob-sound",
    ("media_speakers", "hour"): "lila-ob-sound-hour",
    "media_ghost_mask": "lila-ob-close-door",
    "seat_map": "lila-ob-seats",
    "training_window": "lila-ob-overnight",
    "evidence_ack": "lila-ob-list",
    "brief_interview": "lila-ob-mornings",
    "news_topics": "lila-ob-news",
    "mornings": "lila-ob-mornings",            # the dialogue's opener; the turns after it are her live voice
    "house_manners": "lila-ob-manners",       # to render (placeholder: opener_say in her live voice)
    "voice_lock": "lila-ob-mic",
    "rehearsal": ["lila-ob-handoff"],   # Lila hands the house back; the wall dissolves to Crissa at the desk (BAU)
}
HOST_AGAIN_KEY = "lila-ob-again"
# THE OPENER IS A CLIP ON THE WALL, LIKE EVERY HOST LINE (2026-09-12 pm).
# The first cut played it as a fullscreen mpv window OVER the Tk wall: mpv
# taking and dropping fullscreen on Wayland broke the wall's stacking — the
# desktop folders, the dock and the camera came forward, CANCEL registered
# but nothing cleared ("the wall went berserk … everything disappeared").
# So it plays through the conductor (force) with its audio beside it, and
# the conductor's own dissolve chain carries the wall from Crissa's curtain to
# the desk to Lila's plate. HD is lost in the ASCII render either way.
HOST_OPENER_PREFIX = "crissa-"                 # a Crissa clip: no Lila enter before it
# THE INTRO IS CINEMA (the User 2026-09-12 pm, docs/designs/FIRST-BOOT-INTRO-20260912.md):
# the wall paints Crissa's HD file full screen itself, steps her down to the
# centre room-view box as she introduces Lila, hands the box to Lila on
# "Lila?", and folds the box into the film when Lila's first line starts.
# The wizard only names the file; the wall drives the picture and posts
# `cinema_done` when the file ends. HOUSE_INIT_CINEMA=0 → the conductor-clip
# opener (crissa-opener) as before.
CINEMA_FILES = [
    # THE SPLASH FIRST (the User 2026-09-13, Grok's pre-roll: Z → curtain → Crissa): the
    # join with the curtain's audio laid back under it (the join itself is silent)
    # and box_at at Crissa's step-down (15.75 s), not the splash seam.
    _paths.PACK / "display" / "register" / "crissa-hello-init.mp4",
    _paths.PACK / "display" / "register" / "crissa-hello-curtain.mp4",   # the real render, without the splash
    _paths.PACK / "display" / "register" / "crissa-intro.mp4",           # the curtain, until then
]
HOST_THANKS_CRISSA_KEY = "lila-ob-thanks-crissa"  # row 0c; until rendered: her live voice
THANKS_CRISSA_LINE = "Thanks, Crissa."
# A QUESTION TO ANSWER (the User 2026-09-14: "the dead space is right after Lila says '…set the house
# up with you'. Just dead air after"): her welcome clip ends on a statement, so nothing tells the
# User it is their turn. She asks, live, as the welcome ends.
WELCOME_ASK = os.environ.get("HOUSE_INIT_WELCOME_ASK", "Shall we begin?")
HOST_CURTAIN_HUB = "crissa_intro_hd"          # where the HD curtain lands (the film's last frame, NCC 0.978)
HOST_CURTAIN_LEAVE = "crissa-intro-hd-leave"  # HD curtain → desk, 24 frames (0.9999 → 0.9997)
# each curtain hub walks its own leave: the conductor-clip opener
# (HOUSE_INIT_CINEMA=0) still lands on the old crissa_intro plate
HOST_CURTAIN_LEAVES = {HOST_CURTAIN_HUB: HOST_CURTAIN_LEAVE, "crissa_intro": "crissa-intro-leave"}
HOST_HUB = "lila_listen"                  # her plate in the inventory (lila-host-intake)
HOST_ENTER_KEY = "lila-listen-enter"      # the desk → her plate, 24 frames
HOST_LISTEN_KEY = "lila-reg-listen"       # her listening loop, cued under the ear after every line
HOST_ENTER_SEC = 1.0
_host_thread: threading.Thread | None = None
_host_end_ts: float = 0.0
_AGAIN = re.compile(r"\b(again|say that again|one more time|repeat|pardon)\b", re.I)


def host_key_for(sid: str, st: dict[str, Any]) -> Any:
    """The clip key(s) for this plate and beat, or None."""
    beat = int(st.get("beat") or 0)
    if sid == "welcome" and st.get("first_boot") is False:
        return [k for k in (HOST_KEYS.get(sid) or []) if not str(k).startswith(HOST_OPENER_PREFIX)]   # no Crissa intro
    if sid == "regulars_roster":
        return HOST_KEYS.get((sid, min(max(beat, 0), 3)))
    if sid == "camera_orientation" and int(st.get("polarity_tries") or 0) > 0:
        return HOST_KEYS.get((sid, "mirrored"))
    if sid == "media_speakers":
        ans = (st.get("answers") or {}).get(sid) or {}
        if isinstance(ans, dict) and ans.get("need_hour"):
            return HOST_KEYS.get((sid, "hour"))
    if sid == "room_scan" and _prescan_done():
        return HOST_KEYS.get((sid, "done"))
    if _is_dialogue(sid) and beat > 0:
        return None                     # a live turn: no clip, her voice
    return HOST_KEYS.get(sid)


def cinema_file() -> Path | None:
    if os.environ.get("HOUSE_INIT_CINEMA", "1").strip() in ("0", "false", "no"):
        return None
    for c in CINEMA_FILES:
        if c.is_file():
            return c
    return None


def host_clip_file(key: str) -> Path | None:
    try:
        inv = json.loads((POSES_DIR / "inventory.json").read_text(encoding="utf-8"))
        f = str(((inv.get("clips") or {}).get(key) or {}).get("file") or "")
    except Exception:
        return None
    p = POSES_DIR / f if f else None
    return p if (p and p.is_file()) else None


def host_wav(key: str) -> Path | None:
    """The clip's audio as a WAV (ffmpeg -vn once per clip mtime); None when
    the clip is not rendered yet or carries no audio."""
    clip = host_clip_file(key)
    if clip is None:
        return None
    HOST_WAV_DIR.mkdir(parents=True, exist_ok=True)
    wav = HOST_WAV_DIR / f"init-host-{key}.wav"
    try:
        if wav.is_file() and wav.stat().st_mtime >= clip.stat().st_mtime and wav.stat().st_size > 1000:
            return wav
    except OSError:
        pass
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vn", "-ac", "1", "-ar", "48000",
                        "-f", "wav", str(wav)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not wav.is_file() or wav.stat().st_size < 1000:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return wav


def host_ready(keys: Any) -> list[str]:
    """The keys that have a clip with audio, in order; [] means fall back."""
    ks = keys if isinstance(keys, list) else ([keys] if keys else [])
    out = [k for k in ks if host_wav(str(k)) is not None]
    return out if len(out) == len(ks) else []


def _speech_sink() -> str:
    if not _live_side_effects():
        return os.environ.get("VOICE_SINK") or "echocancel_sink"   # tests never probe pactl
    try:
        from audio_sink import speak_sink
        return speak_sink()
    except Exception:  # noqa: BLE001
        return os.environ.get("VOICE_SINK") or "echocancel_sink"


def _boost_speech(sink: str) -> None:
    if not _live_side_effects():
        return                      # tests never touch the live sink inputs
    try:
        from audio_sink import boost_paplay
        threading.Thread(target=boost_paplay, args=(sink,), kwargs={"pct": 100, "wait_s": 2.0}, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def host_play(keys: list[str]) -> bool:
    """Play the clips in order: the conductor shows the frames (force — host
    clips are not in the focus kit), paplay carries the audio. Blocks the
    wizard's mouth (mouth_busy) until the last one ends."""
    global _host_thread, _host_end_ts
    if os.environ.get("HOUSE_INIT_SILENT", "").strip() in ("1", "true", "yes"):
        return False
    wavs = [(k, host_wav(k)) for k in keys]
    if not wavs or any(w is None for _k, w in wavs):
        return False
    if _host_thread is not None and _host_thread.is_alive():
        return False
    _host_cancel.clear()

    def _run() -> None:
        global _host_end_ts
        entered = False

        def _play(key: str, settle: float = 0.0) -> None:
            try:
                _http_json(f"{CONDUCTOR_URL}/v1/play", {"key": key, "force": True, "by": "init"}, timeout=3.0)
            except Exception:
                return
            if settle:
                time.sleep(settle)

        global _host_proc
        for k, w in wavs:
            if _host_cancel.is_set():
                break
            if not k.startswith(HOST_OPENER_PREFIX):
                # THE FADE TO LILA (the User 2026-09-12: "we need to fade to lila
                # screen after intro"). Once, before her first line: from
                # Crissa's curtain the 1 s slide back to the desk, then the 1 s
                # desk → Lila dissolve; from the desk just the second; on her
                # plate already, straight in. A forced play from anywhere
                # else would be a hard cut.
                if not entered:
                    entered = True
                    hub = _conductor_hub()
                    if hub in HOST_CURTAIN_LEAVES:
                        _play(HOST_CURTAIN_LEAVES[hub], HOST_ENTER_SEC)
                        hub = "down"
                    if hub not in (HOST_HUB, ""):
                        _play(HOST_ENTER_KEY, HOST_ENTER_SEC)
            _play(k)
            try:
                # HER LINE ON THE HOUSE SPEECH SINK, AT FULL VOLUME (the User 2026-09-14: Lila's
                # first hello sat silent after the handoff): a bare paplay went to the default
                # sink — the echo canceller never heard it, so the ear took her line as the
                # User — and started at WirePlumber's remembered duck volume. Same as
                # house-speak and clip_audio: speak_sink + boost_paplay.
                _sink = _speech_sink()
                _host_proc = subprocess.Popen(["paplay", f"--device={_sink}", str(w)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              start_new_session=True)
                _boost_speech(_sink)
                _host_proc.wait(timeout=120)
            except Exception:
                pass
        if entered and not _host_cancel.is_set():
            # THE LISTEN LOOP UNDER THE EAR. A forced one-shot that ends with
            # nothing queued replays itself, silently — the User 2026-09-12 08:04:
            # "her loops were repeating the script without sound". Cue her
            # listening loop for the seam at the clip's end (the register does
            # the same under its takes); it is in the kit so by:init may.
            try:
                _http_json(f"{CONDUCTOR_URL}/v1/cue", {"key": HOST_LISTEN_KEY, "ttl": 0, "bridge": False, "by": "init"}, timeout=3.0)
            except Exception:
                pass
        _host_end_ts = time.time()

    _host_thread = threading.Thread(target=_run, name="init-host", daemon=True)
    _host_thread.start()
    return True


def host_busy() -> bool:
    return _host_thread is not None and _host_thread.is_alive()


def _conductor_hub() -> str:
    """Where the conductor has her now ("" when it is not answering)."""
    try:
        with urllib.request.urlopen(f"{CONDUCTOR_URL}/v1/state", timeout=2.0) as r:
            return str((json.loads(r.read().decode() or "{}") or {}).get("hub") or "")
    except Exception:
        return ""


def _conductor_play(key: str, *, stay: bool = False) -> bool:
    """Force a clip on the JPEG playhead. by=init so the wizard owns the mouth."""
    if not _live_side_effects():
        return False
    try:
        r = _http_json(f"{CONDUCTOR_URL}/v1/play",
                       {"key": key, "force": True, "stay": bool(stay), "by": "init"},
                       timeout=3.0)
        return bool((r or {}).get("ok"))
    except Exception:
        return False


def _crissa_arrive_from_desk() -> None:
    """Until crissa-walk-in is on disk: sit back into Listening, the line, then work.

    The door walk (off-screen through the 11 o'clock / −20° doorway) is the
    missing film. This is the rest of the bookend on clips that already exist.
    """
    host_home()
    for _ in range(60):
        if _conductor_hub() == "down":
            break
        time.sleep(0.25)
    _conductor_play("wake")
    time.sleep(WALK_IN_WAKE_S)
    _conductor_play("listen", stay=True)
    time.sleep(WALK_IN_LISTEN_BEAT_S)
    if load_state().get("status") != "running":
        return
    speak(WALK_IN_LINE, "crissa")
    _wait_spoken(start_s=2.0, max_s=20.0)
    _conductor_play("rest")
    time.sleep(WALK_IN_REST_S)
    _conductor_play("work")


# THE CAMERA STEPS RUN THEMSELVES (the User 2026-09-12 pm: "no hanging renders,
# no awkward thinking time waiting turns"). room_scan used to wait for a
# Continue/"go" and then block the wizard for up to 12 s on a synchronous
# scan with a static glass. Now: Lila's line, then her silent "one moment"
# loop takes the wall while the scan runs on its own thread, and the step
# advances itself when the rail answers.
RAIL_STEPS = ("room_scan",)
HOST_WAIT_KEY = "lila-ob-one-moment"
# room_reference_capture left the routine 2026-09-13 (the User: "Oracle is auth
# only now and ID at door with logging") — the empty-room stills are the
# rails' nightly first-light job, not a chapter the User waits through.
_rail_threads: dict[str, threading.Thread] = {}

# THE PUZZLE (the User 2026-09-12 pm: "much of it can be done in the background.
# Room scan while the user is talking about dogs and cats"). The full sweep
# starts on its own thread the moment the roster reaches the pets beat; by
# the time the routine arrives at room_scan the room is known and Lila only
# says so. The register waits for the camera if the sweep is still panning
# (the wall reads `rail_busy`). HOUSE_INIT_PRESCAN=0 puts the scan back in
# its own step.
_PRESCAN: dict[str, Any] = {"state": "", "at": 0.0, "out": {}}
_prescan_thread: threading.Thread | None = None
ROOM_GEOMETRY = Path(os.environ.get("HOUSE_ROOM_GEOMETRY", str(_paths.presence_data() / "room_geometry.json")))


def _prescan_enabled() -> bool:
    return os.environ.get("HOUSE_INIT_PRESCAN", "1").strip() not in ("0", "false", "no")


def _prescan_start() -> bool:
    """Kick the full look-around in the background (once per routine)."""
    global _prescan_thread
    if not _prescan_enabled() or not _live_side_effects():
        return False
    if _prescan_thread is not None and _prescan_thread.is_alive():
        return False

    def _run() -> None:
        _PRESCAN.update(state="running", at=time.time(), out={})
        try:
            out = _http_json(f"{PRESENCE}/v1/rescan_room", _cam_body({"init": True, "range": "full"}), timeout=60) or {}
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "error": str(e)[:120]}
        ok = bool(out.get("ok", True)) and not out.get("error")
        _PRESCAN.update(state="ok" if ok else "error", at=time.time(), out=out)
        _prepare_crissa_seat()          # the sweep is done: the camera is free for station 11

    _prescan_thread = threading.Thread(target=_run, name="init-prescan", daemon=True)
    _prescan_thread.start()
    return True


def _prescan_reset() -> None:
    _PRESCAN.update(state="", at=0.0, out={})


def _prescan_done() -> bool:
    return _PRESCAN.get("state") == "ok"


def _rail_busy() -> bool:
    if _prescan_thread is not None and _prescan_thread.is_alive():
        return True
    return any(t.is_alive() for t in _rail_threads.values())


def legal_hours() -> list[int]:
    """The clock hours a person can sit at, from the room geometry the scan
    wrote (all twelve until it has)."""
    try:
        g = json.loads(ROOM_GEOMETRY.read_text(encoding="utf-8"))
        hs = [int(h) for h in (g.get("legal_stations") or [])]
        return hs or list(range(1, 13))
    except Exception:
        return list(range(1, 13))


def _rail_work(sid: str) -> dict[str, Any]:
    if sid == "room_scan":
        if _prescan_thread is not None and _prescan_thread.is_alive():
            _prescan_thread.join(45)
        if _PRESCAN.get("state") in ("ok", "error") and _PRESCAN.get("out"):
            out = dict(_PRESCAN["out"])
            out["background"] = True
            return out
        return _http_json(f"{PRESENCE}/v1/rescan_room", _cam_body({"init": True, "range": "full"}), timeout=40)
    return {"ok": True}


def _auto_rail(sid: str) -> None:
    t = _rail_threads.get(sid)
    if t is not None and t.is_alive():
        return

    def _run() -> None:
        # her line first (host_busy — or her live voice when the clip is not
        # rendered yet), then the wait loop on the wall
        for _ in range(200):
            if not host_busy():
                break
            time.sleep(0.2)
        _wait_spoken(start_s=1.5, max_s=40.0)
        already = sid == "room_scan" and _prescan_done()
        if not already and (host_wav(HOST_WAIT_KEY) is not None or host_clip_file(HOST_WAIT_KEY) is not None):
            try:
                _http_json(f"{CONDUCTOR_URL}/v1/play", {"key": HOST_WAIT_KEY, "force": True, "by": "init"}, timeout=3.0)
            except Exception:
                pass
        out = {}
        try:
            out = _rail_work(sid) or {}
        except Exception as e:
            out = {"ok": False, "error": str(e)[:120]}
        st = load_state()
        if st.get("status") != "running" or str(st.get("step_id") or "") != sid:
            return                      # cancelled or moved on while we worked
        st["answers"][sid] = {"ok": bool(out.get("ok", True)), "error": out.get("error"),
                              "background": bool(out.get("background")), "deferred": bool(out.get("deferred"))}
        if out.get("error"):
            st.setdefault("notes", []).append(f"{sid}: " + str(out.get("error"))[:120])
        _advance(st)

    t = threading.Thread(target=_run, name=f"init-rail-{sid}", daemon=True)
    _rail_threads[sid] = t
    t.start()


def _wait_spoken(start_s: float = 4.0, max_s: float = 40.0) -> None:
    """speak() returns before Cosy's first byte (~0.9 s); a bare "until not
    mouth_busy" fell through at once and the next line landed on top of the
    last (2026-09-12 pm, "Thanks, Crissa" under the welcome). Wait for the
    mouth to open, then to close."""
    t0 = time.time()
    while time.time() - t0 < start_s and not mouth_busy():
        time.sleep(0.1)
    while time.time() - t0 < max_s and mouth_busy():
        time.sleep(0.15)


_wrap_thread: threading.Thread | None = None


def _after_wrap(st: dict[str, Any]) -> None:
    """THE BOOKEND. Lila's goodbye is playing. Then Standard through the Mode
    button (when this routine brought Render up, or on first boot), "Leaving
    Render…" until Crissa's brain answers. First boot: curtain is over, she is
    off screen, walk-in through the office door (11 / −20°) into Listening,
    "Hi. This is nice.", then work. Until that film exists the landing is
    wake → listen → the line → rest → work. Re-register that found Render
    already up stays in Render and skips the walk-in."""
    global _wrap_thread
    if _wrap_thread is not None and _wrap_thread.is_alive():
        return

    def _run() -> None:
        for _ in range(200):
            if not host_busy():
                break
            time.sleep(0.2)
        st1 = load_state()
        if st1.get("status") != "running":
            return
        first = st1.get("first_boot") is not False
        if (st1.get("entered_render") or first) and _live_side_effects() and _gpu_mode() != "normal":
            _mode_action("mode_normal")
            st1["mode_note"] = "Leaving Render…"
            save_state(st1)
            t0 = time.time()
            while time.time() - t0 < STANDARD_WAIT_S and not standard_ready():
                if load_state().get("status") != "running":
                    return
                time.sleep(2.0)
            st1 = load_state()
            st1.pop("mode_note", None)
            save_state(st1)
        if first:
            played = False
            if host_clip_file(CRISSA_WALK_IN_KEY) is not None and host_play([CRISSA_WALK_IN_KEY]):
                for _ in range(600):
                    if not host_busy():
                        break
                    time.sleep(0.2)
                played = True
                _conductor_play("work")
            if not played:
                _crissa_arrive_from_desk()
        else:
            host_home()
        st3 = load_state()
        if st3.get("status") == "running" and str(st3.get("step_id") or "") == "rehearsal":
            st3["answers"]["rehearsal"] = True
            if first:
                _first_boot_mark("complete_at")
            _finish(st3, "done")

    _wrap_thread = threading.Thread(target=_run, name="init-wrap", daemon=True)
    _wrap_thread.start()


def host_home() -> bool:
    """The wizard is over (done, aborted, cancelled): Lila's plate dissolves
    back to the desk. The leave slides live in the kit (lila-host-intake), so
    /v1/home by:init always has a way home. Never joined; the wall's next
    seam does it."""
    if not _live_side_effects():
        return False
    try:
        r = _http_json(f"{CONDUCTOR_URL}/v1/home", {"by": "init"}, timeout=3.0)
        return bool((r or {}).get("taken"))
    except Exception:
        return False


def speak(text: str, voice: str = "crissa") -> None:
    """One mouth. Do not stack house-speak, do not re-say the same line."""
    global _speak_proc, _last_speak
    if os.environ.get("HOUSE_INIT_SILENT", "").strip() in ("1", "true", "yes"):
        return
    line = (text or "").strip()[:400]
    if not line or not SPEAK_BIN.is_file():
        return
    now = time.time()
    last_t, last_line = _last_speak
    if line == last_line and (now - last_t) < 20:
        return
    if _speak_proc is not None and _speak_proc.poll() is None:
        try:
            _speak_proc.terminate()
        except OSError:
            pass
    vl = (voice or "").lower()
    # Lila HOSTS (2026-09-12 pm): her voice passes through. This line used to
    # map every voice but announcer to crissa, so "Thanks, Crissa" came out in Crissa's
    # own voice right after her teaser — "the soundtracks are colliding".
    v = "announcer" if vl in ("announcer", "announcer") else ("lila" if vl in ("lila", "host-lila") else "crissa")
    print(f"[init] speak {v}: {line[:60]!r}", flush=True)
    try:
        # ONE PACE FOR LILA, 1.2 (the User 2026-09-14: "I want Lila to speak at 1.2"): the house's
        # rate everywhere. Her 29 speaking clips are re-voiced in the same Cosy `lila` voice, so
        # a live line (the ask, the nudge, the readbacks) and a filmed line sound like one person.
        _speak_proc = subprocess.Popen(
            [str(SPEAK_BIN), "--voice", v, "--text", line],
            env=dict(os.environ, HOUSE_SPEAK_TEMPO=os.environ.get("HOUSE_INIT_SPEAK_TEMPO", "1.2")),
            start_new_session=True,
        )
        _last_speak = (now, line)
    except OSError:
        _speak_proc = None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _patch_site(mut) -> dict[str, Any]:
    data = _load_json(SITE_FILE)
    mut(data)
    _write_json(SITE_FILE, data)
    return data


# LILA LIVE (the User 2026-09-13: "give Lila a few questions to handle live at
# the end. Would break User into live dialogue… she can artfully steer the
# conversation and even decide to cancel routine if she can't collect the
# info. Soft boundaries let her decide when to cancel setup and freeze to
# Restart menu buttons"). A step of kind "dialogue": her rendered opener
# clip is beat 0; every answer goes to the brain with her persona and the
# step's brief; the brain returns her next line, the fields it is now sure
# of, done, cancel. Each turn is a new beat, so the wall's own say → ear
# machinery carries it (her line in her live voice over the listen loop).
# done → the fields are written and the routine moves on after her wrap;
# cancel → the setup freezes on the Restart menu (Start over / Carry on /
# Later). Only the keys the step allows are ever written.
DIALOGUE_MAX_TURNS = 6
DIALOGUE_BRAIN_S = float(os.environ.get("HOUSE_INIT_DIALOGUE_BRAIN_S", "20"))
DIALOGUE_ENOUGH = re.compile(r"^\s*(that'?s (enough|all|it)|enough|we'?re done|i'?m done|done|stop|that will do|move on|next)\b[.!]?\s*$", re.I)
LILA_PERSONA = (
    "You are Lila, the house designer, finishing the setup of a home OS with its new owner (call them the User; "
    "use their name if you know it). You are talking out loud in their room. Sound like a person: warm, plain, brief — "
    "one or two short sentences, ONE question at a time, never a list, never a menu, never the words JSON, field or setting. "
    "Take what they say in their own words; do not make them repeat a format. If they wander, steer back gently. "
    "When you have what you need, or they want to stop, say a one-sentence wrap and set done true. "
    "If they are confused, tired, or cannot answer after a couple of tries, set cancel true and say one kind sentence "
    "that pauses the setup for another time. Do not invent values: only fields the User actually gave."
)


def _is_dialogue(sid: str) -> bool:
    for s_ in steps():
        if s_.get("id") == sid:
            return str(s_.get("kind") or "") == "dialogue"
    return False


def _dialogue_state(st: dict[str, Any], sid: str) -> dict[str, Any]:
    dl = st.setdefault("dialogue", {})
    return dl.setdefault(sid, {"turns": [], "fields": {}, "n": 0, "silence": 0, "say": "", "closed": False})


def _dialogue_fields_spec(step: dict[str, Any]) -> dict[str, Any]:
    f = step.get("fields")
    return f if isinstance(f, dict) else {}


def _dialogue_clean(spec: dict[str, Any], got: Any) -> dict[str, Any]:
    """Only the allowed keys, typed; allowed values filtered; nothing invented."""
    out: dict[str, Any] = {}
    if not isinstance(got, dict):
        return out
    for k, rule in spec.items():
        if k not in got or got[k] in (None, "", [], {}):
            continue
        v = got[k]
        typ = str((rule or {}).get("type") or "string")
        allowed = (rule or {}).get("allowed")
        if typ == "list":
            vs = v if isinstance(v, list) else [v]
            vs = [str(x).strip() for x in vs if str(x).strip()]
            if isinstance(allowed, list):
                al = {str(a).lower(): str(a) for a in allowed}
                vs = [al[x.lower()] for x in vs if x.lower() in al]
            if vs:
                out[k] = vs[:12]
        elif typ == "bool":
            if isinstance(v, bool):
                out[k] = v
            elif str(v).strip().lower() in ("yes", "true", "ok", "sure", "y"):
                out[k] = True
            elif str(v).strip().lower() in ("no", "false", "never", "n"):
                out[k] = False
        elif typ == "hour":
            h = _hour_of(v)
            if h is not None:
                out[k] = h
        elif typ == "seats":
            pairs = list(v.items()) if isinstance(v, dict) else [
                ((x or {}).get("name"), (x or {}).get("hour")) for x in (v if isinstance(v, list) else []) if isinstance(x, dict)]
            seats = {}
            for name, hour in pairs:
                n_, h = _clean_name(name), _hour_of(hour)
                if n_ and h is not None:
                    seats[n_] = h
            if seats:
                out[k] = seats
        else:
            sv = str(v).strip()[:200]
            if isinstance(allowed, list):
                sv = {str(a).lower(): str(a) for a in allowed}.get(sv.lower(), "")
            if sv:
                out[k] = sv
    return out


def _hour_of(v: Any) -> int | None:
    """A clock hour 1-12 from 9, "9", "nine", "nine o'clock"; None otherwise."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        h = int(v)
    else:
        h = spoken_hour(str(v))
        if h is None:
            return None
    return h if 1 <= h <= 12 else None


def _dialogue_brain(step: dict[str, Any], dlg: dict[str, Any], user_text: str, roster: dict[str, Any]) -> dict[str, Any] | None:
    """One conversational turn → {say, fields, done, cancel}; None when the brain is away."""
    spec = _dialogue_fields_spec(step)
    want = "; ".join(f"{k}: {(r or {}).get('about', '')}" + (f" (one of {', '.join(map(str, r['allowed']))})" if isinstance((r or {}).get("allowed"), list) else "")
                     for k, r in spec.items())
    system = (
        LILA_PERSONA
        + "\n\nThis conversation: " + str(step.get("brief") or "")
        + "\nYou are collecting these (only these keys may appear in fields): " + want
        + "\nKnown so far: " + json.dumps(dlg.get("fields") or {})
        + "\nThe User's name: " + (str(roster.get("user") or "") or "unknown")
        + f"\nTurn {int(dlg.get('n') or 0) + 1} of at most {int(step.get('max_turns') or DIALOGUE_MAX_TURNS)}; wrap up before the last."
        + (("\nSTAGE DIRECTIONS: set \"show\" to what the wall shows while you speak, one of: "
            + ", ".join(map(str, step.get("show_allowed") or [])) + ".") if step.get("show_allowed") else "")
        + '\n\nReply with ONE JSON object only, no words around it: {"say": "<what you say next, one or two sentences>", '
          '"fields": {<only keys you are now sure of>}, "done": true|false, "cancel": true|false'
        + (', "show": "<one stage direction>"' if step.get("show_allowed") else "") + '}'
    )
    msgs = [{"role": "system", "content": system}]
    opener = str(step.get("opener_say") or step.get("title") or "")
    if opener:
        msgs.append({"role": "assistant", "content": opener})
    for t in (dlg.get("turns") or [])[-8:]:
        msgs.append({"role": "user", "content": str(t.get("user") or "")})
        msgs.append({"role": "assistant", "content": str(t.get("say") or "")})
    msgs.append({"role": "user", "content": user_text})
    url, model = _brain_endpoint()
    if not url:
        return None
    body = {"model": model, "temperature": 0.4, "max_tokens": 220, "messages": msgs}
    if model == "crew":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    try:
        out = _http_json(url, body, timeout=DIALOGUE_BRAIN_S)
        text = str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except Exception:
        return None
    text = text.strip("`").strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        d = json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    return {
        "say": str(d.get("say") or "").strip()[:400],
        "fields": _dialogue_clean(spec, d.get("fields")),
        "done": bool(d.get("done")),
        "cancel": bool(d.get("cancel")),
        "show": (str(d.get("show") or "") if str(d.get("show") or "") in [str(x) for x in (step.get("show_allowed") or [])] else ""),
    }


def _dialogue_commit(st: dict[str, Any], step: dict[str, Any], fields: dict[str, Any]) -> None:
    """Write what she learned, through the same writers the old steps used."""
    sid = str(step.get("id") or "")
    spec = _dialogue_fields_spec(step)
    f = dict(fields or {})
    for k, rule in spec.items():
        if k not in f and (rule or {}).get("default") is not None and sid == "mornings":
            f[k] = (rule or {}).get("default")
    if any(k in f for k in ("cards", "wake_hour", "greeting")):
        uid = slug((st.get("roster") or {}).get("user_id") or "")
        if not uid:
            uid = str((_load_json(SITE_FILE).get("enroll") or {}).get("user_id") or "")
        if uid:
            path = HOUSE_ID / "people" / uid / "brief.json"
            brief = _load_json(path)
            for k in ("cards", "wake_hour", "greeting"):
                if k in f:
                    brief[k] = f[k]
            brief.setdefault("cards", ["weather", "headline", "house"])
            _write_json(path, brief)
    if f.get("topics") or f.get("news_freeform"):
        try:
            from news_topics import apply_freeform, apply_ids

            if f.get("topics"):
                apply_ids([str(x) for x in f["topics"]])
            if f.get("news_freeform"):
                apply_freeform(str(f["news_freeform"]), file_card=True)
        except Exception as e:  # noqa: BLE001
            st.setdefault("notes", []).append(f"{sid}: news {str(e)[:80]}")
    site_keys = {k: f[k] for k in ("window", "quiet_hours", "visitors") if k in f}
    if site_keys:

        def mut(site: dict[str, Any]) -> None:
            if "window" in site_keys:
                site["training_window"] = str(site_keys["window"])
            man = site.setdefault("manners", {})
            if isinstance(man, dict):
                if "quiet_hours" in site_keys:
                    man["quiet_hours"] = str(site_keys["quiet_hours"])
            if "visitors" in site_keys:
                site["guests_pool"] = [{"id": slug(n), "display": str(n)} for n in site_keys["visitors"] if slug(n)]

        _patch_site(mut)
    if sid == "room_clock":
        _room_clock_commit(st, f)
    if sid == "crissa_seat":

        def mut_seat(site: dict[str, Any]) -> None:
            rc = site.setdefault("room_clock", {})
            if isinstance(rc, dict):
                rc["crissa_seat_ok"] = f.get("crissa_seat_ok")
                if f.get("crissa_seat_note"):
                    rc["crissa_seat_note"] = f["crissa_seat_note"]
                rc["crissa_seat_pictures"] = {"live": _CRISSA_SEAT.get("live") or "", "hd": _CRISSA_SEAT.get("hd") or ""}

        _patch_site(mut_seat)
    st["answers"][sid] = {"fields": f, "turns": int(_dialogue_state(st, sid).get("n") or 0)}


CRISSA_SEAT_STATION = os.environ.get("HOUSE_INIT_CRISSA_SEAT_STATION", "11")
CRISSA_SEAT_TILT = int(os.environ.get("HOUSE_INIT_CRISSA_SEAT_TILT", "-20"))


PROCESSING_MAX_S = 45.0


def _processing(what: str) -> None:
    """Show (or clear, with "") the wall's 'Wait while we process…' line for the running routine."""
    st = load_state()
    if str(st.get("status") or "") != "running":
        return
    if what:
        st["processing"] = {"what": what, "at": time.time()}
    else:
        st.pop("processing", None)
    save_state(st)


def _aim_crissa_seat() -> None:
    """Stage direction "crissa_seat" (the User 2026-09-14: "pic of room station 11 down 20deg. That is
    Crissa's backdrop so they should ask if that looks good for Crissa's seat"): the camera looks
    there and the wall shows the live picture beside her rendered office."""
    if not _live_side_effects():
        return

    def _go() -> None:
        _processing("Wait while we turn the camera…")
        try:
            _http_json(f"{PRESENCE}/v1/look_at", _cam_body({"station": CRISSA_SEAT_STATION, "tilt": CRISSA_SEAT_TILT, "n": 1,
                                                 "facefinder": False, "owner": "init-crissa-seat"}), timeout=25.0)
        finally:
            _processing("")

    threading.Thread(target=_go, name="init-crissa-seat", daemon=True).start()


def _room_clock_commit(st: dict[str, Any], f: dict[str, Any]) -> None:
    """The room as the User said it (the User 2026-09-14): the door hour is the dial's DOOR tick
    (stations.heading.door_hour), the User at 6, the TV / speakers the media locus, and each
    named person's usual hour a sitter, matched to the roster by first name."""
    roster = st.get("roster") or {}
    known: dict[str, str] = {}
    if roster.get("user"):
        known[str(roster["user"]).split()[0].lower()] = slug(roster.get("user_id") or roster["user"])
    for r in roster.get("regulars") or []:
        if isinstance(r, dict) and r.get("display"):
            known[str(r["display"]).split()[0].lower()] = slug(r.get("id") or r["display"])
    sitters: dict[str, int] = {}
    for name, hour in (f.get("seats") or {}).items():
        first = (str(name).split() or [""])[0].lower()
        pid = known.get(first) or slug(first)
        if pid:
            sitters[pid] = int(hour)
    mode = str(f.get("media_mode") or "")
    mh = f.get("media_hour")
    if mh is not None and not mode:
        mode = "external"

    def mut(site: dict[str, Any]) -> None:
        stn = site.setdefault("stations", {})
        if not isinstance(stn, dict):
            return
        head = stn.setdefault("heading", {})
        if isinstance(head, dict):
            if f.get("door_hour") is not None:
                head["door_hour"] = int(f["door_hour"])
            head["user_hour"] = 6
        if sitters:
            stn["sitters"] = sitters
        if mode == "internal":
            site["media_mode"] = "internal"
            site["media_station"] = None
        elif mode == "external" and mh is not None:
            site["media_mode"] = "external"
            site["media_station"] = int(mh)
            stn["media_hour"] = int(mh)
        rc = {k: f[k] for k in ("room_ok", "crissa_seat_ok") if k in f}
        rc.update(at=_now(), crissa_seat={"station": CRISSA_SEAT_STATION, "tilt": CRISSA_SEAT_TILT})
        site["room_clock"] = rc

    _patch_site(mut)
    if _live_side_effects() and mode == "internal":
        _http_json(MEDIA, {"mode": "internal", "station": None}, timeout=4)
    elif _live_side_effects() and mode == "external" and mh is not None:
        _http_json(MEDIA, {"mode": "external", "station": int(mh)}, timeout=4)


def _dialogue_finish(st: dict[str, Any], step: dict[str, Any], say: str = "") -> dict[str, Any]:
    """Write, then her wrap line as a last beat; the wall's say advances us."""
    sid = str(step.get("id") or "")
    dlg = _dialogue_state(st, sid)
    _dialogue_commit(st, step, dlg.get("fields") or {})
    if not say:
        return _advance(st)
    dlg["say"] = say
    dlg["closed"] = True
    st["beat"] = int(st.get("beat") or 0) + 1
    save_state(st)
    return snapshot(st)


def _dialogue_answer(st: dict[str, Any], step: dict[str, Any], text: str, pill: str) -> dict[str, Any]:
    sid = str(step.get("id") or "")
    dlg = _dialogue_state(st, sid)
    raw = (text or "").strip()
    if dlg.get("closed"):
        return _advance(st)              # her wrap was said; whatever was heard is noise
    if st.get("frozen"):
        return snapshot(st)
    if pill in ("enough", "next") or DIALOGUE_ENOUGH.match(raw or ""):
        return _dialogue_finish(st, step, "All right — that's plenty. Thank you.")
    if not raw:
        dlg["silence"] = int(dlg.get("silence") or 0) + 1
        if dlg["silence"] < 2:
            save_state(st)
            return snapshot(st)          # the wall listens once more
        raw = "(the User said nothing)"
    dlg["silence"] = 0
    dlg["n"] = int(dlg.get("n") or 0) + 1
    # WAIT WHILE WE PROCESS (the User 2026-09-14: "lila fills out global values - even checks them. and
    # shows the screenshot of crissa for approval. Can that be done inside? Maybe with a 'Wait while we
    # process...'"): the wall shows it while the model thinks, not dead air
    _processing("Wait while we process…")
    got = _dialogue_brain(step, dlg, raw, st.get("roster") or {})
    _processing("")
    st.pop("processing", None)
    if got is None:
        # the brain is away: keep what we have and move on kindly
        st.setdefault("notes", []).append(f"{sid}: brain away on turn {dlg['n']}")
        return _dialogue_finish(st, step, "Let's leave the rest for another time.")
    for k, v in (got.get("fields") or {}).items():
        dlg["fields"][k] = v
    dlg["turns"].append({"user": raw[:300], "say": got.get("say") or ""})
    if got.get("show"):
        if got["show"] == "crissa_seat" and dlg.get("show") != "crissa_seat":
            _aim_crissa_seat()
        dlg["show"] = got["show"]
    say = got.get("say") or ""
    if got.get("cancel"):
        # SOFT BOUNDARY: she pauses the setup; the wall shows the Restart menu
        _dialogue_commit(st, step, dlg.get("fields") or {})
        st["frozen"] = {"step": sid, "say": say or "Let's leave the rest for another time.", "at": _now()}
        st.setdefault("notes", []).append(f"{sid}: paused by Lila on turn {dlg['n']}")
        dlg["say"] = st["frozen"]["say"]
        st["beat"] = int(st.get("beat") or 0) + 1
        save_state(st)
        return snapshot(st)
    if got.get("done") or dlg["n"] >= int(step.get("max_turns") or DIALOGUE_MAX_TURNS):
        return _dialogue_finish(st, step, say or "Thank you — that's what I needed.")
    dlg["say"] = say or "Go on."
    st["beat"] = int(st.get("beat") or 0) + 1
    save_state(st)
    return snapshot(st)


def _auto_advance_after_line(sid: str, beat: int) -> None:
    """Her wrap line was handed to the mouth: move on once it is said."""

    def _run() -> None:
        _wait_spoken(start_s=6.0, max_s=40.0)
        st = load_state()
        if st.get("status") != "running" or str(st.get("step_id") or "") != sid or int(st.get("beat") or 0) != beat:
            return
        _advance(st)

    threading.Thread(target=_run, name=f"init-dialogue-wrap-{sid}", daemon=True).start()


def plate_for(st: dict[str, Any]) -> dict[str, Any]:
    """Claude's wall plates (init-user-routine.html) — spoken + glass copy.

    Sequence JSON prompts are director notes. The glass says these lines.
    """
    sid = str(st.get("step_id") or "")
    if not sid:
        cur = current_step(st)
        sid = str((cur or {}).get("id") or "")
    beat = int(st.get("beat") or 0)
    who = str(st.get("enroll_who") or "them")
    user = str((st.get("roster") or {}).get("user") or "")
    seq = steps()
    i = int(st.get("index") or 0)
    req = [s for s in seq if not s.get("optional")]
    req_on = sum(1 for s in seq[: i + 1] if not s.get("optional")) if seq else 0
    plates: dict[str, dict[str, Any]] = {
        "welcome": {
            "eyebrow": "First boot",
            "cinema": (str(cinema_file()) if (cinema_file() and st.get("first_boot") is not False) else ""),   # re-register: no Crissa film
            "cinema_state": str(st.get("cinema_state") or ""),   # "" | "playing" | "box" | "done"
            "title": "I'm Crissa.",
            "coach": "This house is about to learn you. About ten minutes, mostly talking.",
            "hint": "Just say it — I'm listening.",
            "say": "I'm Crissa. This house is about to learn you. About ten minutes, mostly talking.",
            "host": {
                "clip": "/init-host/welcome.mp4?v=host1",
                "poster": "/init-host/welcome.jpg?v=host1",
            },
        },
        "regulars_roster": [
            {
                "eyebrow": "Who lives here",
                "title": "What should I call you?",
                "hint": "This is the name I say out loud, and the one that appears on the wall.",
                "say": "What should I call you?",
            },
            {
                "eyebrow": "Who lives here",
                "title": "Who else lives here?",
                "coach": "People who live or work here regularly.",
                "say": "Who else lives or works here regularly?",
            },
            {
                "eyebrow": "Who lives here",
                "title": "Any pets?",
                "coach": "Name plus look and size. Never counted as people.",
                "say": "Any pets? Name plus look and size. Never counted as people.",
            },
            {
                "eyebrow": "Who lives here",
                "title": "Who's in the room right now?",
                "title_sm": True,
                "say": "Who is in the room right now?",
            },
        ][min(max(beat, 0), 3)],
        # THE REGISTER ROUTINE, as two chapters, ON THE WALL (2026-09-11:
        # "the served version never worked once"). Control Panel's Register
        # an ID runs the same routine: Lila's rendered clips host it (the
        # lines are baked audio beside the conductor's frames), two short
        # takes, Crissa's handoff clip once at the end, ~15 s. The wizard says
        # nothing over it: "host" keeps say_for quiet. First-boot continues
        # the wizard after `enroll_done`; the card returns to /panel.
        # (the User 2026-09-07: rework Init to include the extracted Register
        # routine, so the ad hoc guest registration is that part taken out.)
        "user_identity": {
            "eyebrow": "You",
            "title": user or "You",
            "hint": "Sit at the desk. Look at the screen. Fifteen seconds.",
            "host": "wall",
            "say": "",
        },
        "present_regulars_enroll": {
            "eyebrow": "Someone else",
            "title": who,
            "coach": f"Still here? We can register {who} now, the same way, at the desk.",
            "hint": "Sit at the desk. Look at the screen. Fifteen seconds." if st.get("enroll_go") else "Say yes, or later.",
            "host": "wall" if st.get("enroll_go") else "",
            "say": "" if st.get("enroll_go") else f"Is {who} still here? We can register {who} now, the same way. Shall we?",
        },
        "room_scan": {
            "eyebrow": "The room",
            "title": "I looked around while we talked." if _prescan_done() else "I'm going to look around.",
            "title_sm": True,
            "say": "I had a look around the room while we talked." if _prescan_done() else "I'm going to look around the room now.",
            "host": {
                "clip": "/init-host/room_scan.mp4?v=host1",
                "poster": "/init-host/room_scan.jpg?v=host1",
                "loop": True,
            },
        },
        "camera_orientation": {
            "eyebrow": "The picture",
            "title": "Stand in the door and wave your right hand.",
            "title_sm": True,
            "say": "Stand in the door. Wave your right hand. Is that wave on the right of the picture?",
        },
        "media_speakers": {
            "eyebrow": "Sound",
            "title": "Where does the sound come from?",
            "title_sm": True,
            "hint": "A speaker is a voice with no body. Knowing where it sits is what keeps the television from becoming you.",
            "say": "Where does the sound come from? This computer, or speakers in the room?",
        },
        "media_ghost_mask": {
            "eyebrow": "The door",
            "title": "Close the door for a moment.",
            "title_sm": True,
            "say": "Printed faces — posters, photographs — look like people to a camera. I'll mark them so they're never counted.",
            "host": {
                "clip": "/init-host/media_ghost_mask.mp4?v=host1",
                "poster": "/init-host/media_ghost_mask.jpg?v=host1",
            },
        },
        "seat_map": {
            "eyebrow": "Seats",
            "title": "Where does each person usually sit?",
            "title_sm": True,
            "say": "Where does each person usually sit?",
        },
        "training_window": {
            "eyebrow": "Overnight",
            "title": "Overnight self-training.",
            "title_sm": True,
            "coach": "Default 18:00 to 02:00 local. Say another window, or later.",
            "say": "Overnight self-training window. Default 18:00 to 02:00. Change it, or say later.",
        },
        "evidence_ack": {
            "eyebrow": "The list",
            "title": "One list of who is here.",
            "title_sm": True,
            "coach": "We only add or remove someone with evidence. A timer never erases a person.",
            "say": "The house keeps one list of who is here. We only add or remove someone with evidence. A timer never erases a person.",
            "host": {
                "clip": "/init-host/evidence_ack.mp4?v=host1",
                "poster": "/init-host/evidence_ack.jpg?v=host1",
            },
        },
        "brief_interview": {
            "eyebrow": "Mornings",
            "title": "What should mornings look like?",
            "title_sm": True,
            "coach": "Cards, wake hour, greeting.",
            "say": "What should mornings look like? Cards, wake hour, greeting.",
        },
        "news_topics": {
            "eyebrow": "The hour's news",
            "title": "What should the hour's news cover?",
            "title_sm": True,
            "hint": "Pick from the list, or say it in your own words.",
            "say": "What should the hour's news cover? Pick from the list, or say it in your own words.",
        },
        "voice_lock": {
            "eyebrow": "The Mic",
            "title": "The house will not listen until you turn the Mic on.",
            "title_sm": True,
            "coach": "That is on the Control Panel, next to Announcer. Walk-ins do not unmute her. Init does not arm Mic.",
            "say": "The house will not listen or answer until you turn the Mic on. That is on the Control Panel, next to Announcer.",
            "host": {
                "clip": "/init-host/voice_lock.mp4?v=host1",
                "poster": "/init-host/voice_lock.jpg?v=host1",
            },
        },
        "rehearsal": {
            "eyebrow": "Tomorrow",
            "title": "A preview of tomorrow's Morning Hello.",
            "title_sm": True,
            "coach": "Coffee and all. Then land on the wall, Mic still off.",
            "say": "A 20-second preview of tomorrow's Morning Hello — coffee and all. Then land on the wall, Mic still off.",
            "host": {
                "clip": "/init-host/rehearsal.mp4?v=host1",
                "poster": "/init-host/rehearsal.jpg?v=host1",
            },
        },
    }
    plate = plates.get(sid) or {
        "eyebrow": sid.replace("_", " "),
        "title": sid.replace("_", " "),
        "say": "",
    }
    plate = dict(plate)
    cur = current_step(st) or {}
    if str(cur.get("kind") or "") == "dialogue" and str(cur.get("id") or "") == sid:
        dlg = (st.get("dialogue") or {}).get(sid) or {}
        frozen = st.get("frozen") if isinstance(st.get("frozen"), dict) else None
        if frozen:
            plate = {"eyebrow": "Paused", "title": str(frozen.get("say") or "Let's leave the rest for another time."),
                     "title_sm": True, "coach": "The setup is paused. Start over, carry on, or later.",
                     "say": str(frozen.get("say") or ""), "frozen": True}
        elif beat <= 0:
            plate = {"eyebrow": "Lila", "title": str(cur.get("title") or cur.get("prompt") or sid), "title_sm": True,
                     "hint": "Say it in your own words. \"That's enough\" ends it.",
                     "say": str(cur.get("opener_say") or cur.get("title") or "")}
        else:
            plate = {"eyebrow": "Lila", "title": str(dlg.get("say") or ""), "title_sm": True,
                     "hint": "" if dlg.get("closed") else "Say it in your own words. \"That's enough\" ends it.",
                     "say": str(dlg.get("say") or ""), "live": True, "closed": bool(dlg.get("closed"))}
    if sid == "media_speakers":
        # product copy only — never this house's hour or a sitter's name
        # (the User 2026-09-12 pm, the sheet: "Media is hour 7 here… <a regular>")
        ans = (st.get("answers") or {}).get(sid) or {}
        h = stored_media_hour()
        if isinstance(ans, dict) and ans.get("need_hour"):
            plate["title"] = "Which hour on the clock are the speakers?"
            plate["hint"] = "Twelve is the door. Say the hour."
            plate["say"] = "Which hour on the clock are the speakers? Twelve is the door."
        elif h is not None:
            plate["hint"] = f"The speaker mark is on hour {h} now. Answer again to move it."
        if h is not None:
            plate["media_hour"] = h
    # HER OWN LINES OVER THE PLATE: the pause menu, a readback, Lila live
    frozen_g = st.get("frozen") if isinstance(st.get("frozen"), dict) else None
    confirm = st.get("confirm") if isinstance(st.get("confirm"), dict) else None
    live = st.get("live") if isinstance(st.get("live"), dict) else None
    own = False
    if frozen_g:
        say_f = str(frozen_g.get("say") or "Let's pause the setup here.")
        plate = {"eyebrow": "Paused", "title": say_f, "title_sm": True,
                 "coach": "Say carry on, start over, or later.", "say": say_f, "frozen": True}
        own = True
    elif (confirm and confirm.get("sid") == sid and int(confirm.get("beat") or 0) == beat
          and not (live and live.get("sid") == sid and int(live.get("beat") or 0) == beat
                   and float(live.get("at") or 0) > float(confirm.get("at") or 0))):
        # HER LIVE LINE WINS WHEN IT IS NEWER (the User 2026-09-14: "Note said she was live" — the readback
        # outranked her live line, so "So <A> and <B> — is that right?" was said again instead)
        plate = {"eyebrow": "Lila", "title": str(confirm.get("say") or ""), "title_sm": True,
                 "hint": "Say yes, or tell me what's wrong.", "say": str(confirm.get("say") or ""), "live": True}
        own = True
    elif live and live.get("sid") == sid and int(live.get("beat") or 0) == beat:
        plate = {"eyebrow": "Lila", "title": str(live.get("say") or ""), "title_sm": True,
                 "hint": "Just say it.", "say": str(live.get("say") or ""), "live": True}
        own = True
    plate["dot_on"] = req_on
    plate["dot_n"] = len(req)
    plate["host_key"] = None if own else host_key_for(sid, st)
    return plate


def say_for(st: dict[str, Any]) -> str:
    """The line the wizard speaks for the current plate — nothing on a
    hosted chapter, where the curtain clip in the PIP is the voice."""
    p = plate_for(st)
    if p.get("host"):
        return ""
    return str(p.get("say") or p.get("title") or "").strip()


def stored_media_hour() -> int | None:
    """This house's media locus, from site.json (init SoT). Not a product default."""
    try:
        site = json.loads(SITE_FILE.read_text())
    except Exception:
        return None
    if not isinstance(site, dict):
        return None
    st = site.get("stations") if isinstance(site.get("stations"), dict) else {}
    for raw in (st.get("media_hour"), site.get("media_station")):
        if raw is None:
            continue
        try:
            h = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= h <= 12:
            return h
    return None


def snapshot(st: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if st is None and _v2():
        return _v2().apply("snapshot")
    st = st if st is not None else load_state()
    _reap_hold(st)
    seq = steps()
    running = str(st.get("status") or "") == "running"
    step = current_step(st) if running else None
    i = int(st.get("index") or 0) if running else 0
    ux = (step or {}).get("ux") if isinstance((step or {}).get("ux"), dict) else {}
    topics = []
    if (step or {}).get("id") == "news_topics":
        topics = list(ux.get("checkboxes") or [])
        try:
            from news_topics import load_catalog

            cat = load_catalog()
            labels = {
                str(t.get("id")): str(t.get("label") or t.get("id"))
                for t in (cat.get("topics") or [])
                if isinstance(t, dict) and t.get("id")
            }
        except Exception:
            labels = {}
        topics = [{"id": tid, "label": labels.get(tid, tid)} for tid in topics]
    cards = list(ux.get("cards") or []) if (step or {}).get("id") == "brief_interview" else []
    return {
        "ok": True,
        "needed": needed(st),
        "status": st.get("status") or "idle",
        "index": i,
        "total": len(seq),
        "step_id": (step or {}).get("id") or "",
        "kind": (step or {}).get("kind") or "",
        "optional": bool((step or {}).get("optional")),
        "voice": (step or {}).get("voice") or "crissa",
        "prompt": (step or {}).get("prompt") or "",
        "ear": (step or {}).get("ear") or "closed",
        "ux": ux,
        "beat": int(st.get("beat") or 0),
        "turn": int(st.get("turn") or 0),       # a new line on the same beat (readback, Lila live): the wall says it
        "roster": st.get("roster") or {},
        "enroll_who": st.get("enroll_who") or "",
        "enroll_go": st.get("enroll_go") or "",   # a spoken yes: the wall starts the register chapter
        "enroll_queue": list(st.get("enroll_queue") or []),
        "topics": topics,
        "cards": cards,
        "answers": st.get("answers") or {},
        "note": (st.get("notes") or [""])[-1] if st.get("notes") else "",
        "optional_marks": [
            j for j, s in enumerate(seq) if s.get("optional")
        ],
        "plate": plate_for(st) if running else {},
        "media_hour": stored_media_hour(),
        "rail_busy": _rail_busy() if running else False,
        "frozen": (st.get("frozen") if isinstance(st.get("frozen"), dict) else {}) if running else {},
        "dialogue": ({k: v for k, v in (((st.get("dialogue") or {}).get((step or {}).get("id") or "")) or {}).items() if k in ("n", "fields", "closed", "show")}
                     if (step or {}).get("kind") == "dialogue" else {}),
        "legal_hours": legal_hours() if (step or {}).get("id") in ("seat_map", "media_speakers", "room_clock") else [],
        "prescan": str(_PRESCAN.get("state") or ""),
        "ear_live": bool(EAR_LIVE and running),        # the wall listens beside her line, not after it
        "first_boot": bool(st.get("first_boot", True)) if running else False,
        "mode_note": str(st.get("mode_note") or "") if running else "",
        "entered_render": bool(st.get("entered_render")) if running else False,
        "board_live": int(st.get("board_live") or 0) if running else 0,
        "say_ready": (not running) or ((_no_render_wait() or render_ready()) and not (
            (step or {}).get("id") == "crissa_seat" and _CRISSA_SEAT.get("state") == "running"
            and time.time() - float(_CRISSA_SEAT.get("at") or 0) < 150)),
        "crissa_seat": {k: _CRISSA_SEAT.get(k) for k in ("state", "live", "hd")} if running else {},
        "processing": ((str((st.get("processing") or {}).get("what") or "")
                        if running and isinstance(st.get("processing"), dict)
                        and time.time() - float(st["processing"].get("at") or 0) < PROCESSING_MAX_S else "")
                       or ("Wait while we process…" if running and (step or {}).get("id") == "crissa_seat"
                           and _CRISSA_SEAT.get("state") == "running" else "")),
        "asked_at": float(st.get("asked_at") or 0.0),
    }


_LAST_RUN_ID = [0.0]


def _new_run_id() -> str:
    """Strictly increasing, same float-style text as before: a "start over" inside the same
    millisecond reused the run id, and a stale save could then pass as the new run."""
    t = max(time.time(), _LAST_RUN_ID[0] + 0.001)
    _LAST_RUN_ID[0] = t
    return "%.3f" % t


# WHO MAY CHANGE WHO THE USER IS (the User 2026-10-03): once a User is enrolled, a NEW Init run (New User
# initialization, the Restart menu, the boot walker) needs presence's User auth - from Guest anyone could reset every
# value, the User too. A run already going carries on; a house with no User yet (first boot) starts as before.
# The Control Panel greys its keys from identity_lock(); this is the refusal behind them. HOUSE_IDENTITY_NEEDS_AUTH=1, OFF.
IDENTITY_SWITCH = "HOUSE_IDENTITY_NEEDS_AUTH"
NEEDS_AUTH_TEXT = "Init needs User auth first. Sign in as the User, then start Init again."


def _presence_user_auth() -> bool:
    """presence's own user_auth, read only (never starts a look); unknown is not User mode."""
    try:
        with urllib.request.urlopen(f"{PRESENCE}/v1/presence", timeout=3) as r:
            d = json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001
        return False
    return bool(d.get("user_auth") or (d.get("presence") or {}).get("user_auth"))


def identity_lock() -> dict[str, Any]:
    """{on, enrolled, authed, locked, text}: locked = the switch is on, a User is enrolled and presence says Guest."""
    if os.environ.get(IDENTITY_SWITCH, "").strip().lower() not in ("1", "on", "true", "yes"):
        return {"ok": True, "on": False, "locked": False}
    try:
        import house_user
        if os.environ.get("HOUSE_IDENTITY_FAIL_CLOSED", "1").strip().lower() in ("0", "false", "no", "off"):
            enrolled = house_user.enrolled()
        else:
            # A FILE THAT CANNOT BE READ IS NOT PROOF THERE IS NO USER (the User 2026-10-04, Init v2): enrolled()
            # swallows every read error and says "no User", which opened this lock on one malformed person.json.
            # store_state() tells "none" from "unreadable"; only "none" opens it. HOUSE_IDENTITY_FAIL_CLOSED=0 = before.
            enrolled = house_user.store_state() != "none"
    except Exception:  # noqa: BLE001 - an unreadable store is not proof there is no User
        enrolled = True
    authed = _presence_user_auth() if enrolled else False
    locked = enrolled and not authed
    return {"ok": True, "on": True, "enrolled": enrolled, "authed": authed, "locked": locked,
            "text": NEEDS_AUTH_TEXT if locked else ""}


def start(*, force: bool = False) -> dict[str, Any]:
    if _walk() is not None:
        return _walk().apply("start", {"force": bool(force)})
    (HOUSE_ID / "people").mkdir(parents=True, exist_ok=True)
    (HOUSE_ID / "site").mkdir(parents=True, exist_ok=True)
    st = load_state()
    if not force and not needed(st) and st.get("status") != "running":
        return snapshot(st)
    if st.get("status") == "running" and not force:
        st["seen_at"] = time.time()
        save_state(st)
        _chat_hold_on()
        return snapshot(st)
    if identity_lock().get("locked"):
        print("[init] start refused: a User is enrolled and presence says Guest (HOUSE_IDENTITY_NEEDS_AUTH)", flush=True)
        raise PermissionError(NEEDS_AUTH_TEXT)
    st = empty_state()
    st["status"] = "running"
    st["run_id"] = _new_run_id()
    st["cinema_state"] = ""
    _prescan_reset()
    st["started_at"] = _now()
    st["seen_at"] = time.time()
    st["first_boot"] = is_first_boot()
    st["first_boot_forced"] = first_boot_forced()
    _CRISSA_SEAT.update(state="", live="", hd="", error="", at=0.0)
    live_n = board_live_count() if _live_side_effects() else 0
    st["board_live"] = live_n
    if PACK_RENDER_SWITCH and _live_side_effects() and _gpu_mode() != "render":
        if live_n > 0:
            # Occupancy law: do not park a live board for the wizard. Cinema
            # and the portrait still run in Standard. Seat-picture Comfy waits.
            st["entered_render"] = False
            st["mode_note"] = "Standard — board is live (%d cards); not switching to Render" % live_n
        else:
            r = _mode_action("mode_render")
            st["entered_render"] = bool(r.get("started") or r.get("pending") or r.get("already"))
            st["mode_note"] = "Switching to Render…"
    seq = steps()
    if seq:
        st["step_id"] = seq[0]["id"]
        st["index"] = 0
    save_state(st, force=True)
    _chat_hold_on()
    if live_n == 0:
        threading.Thread(target=_board_pause, name="init-board-pause", daemon=True).start()
    return snapshot(st)


def abort() -> dict[str, Any]:
    if _walk() is not None:
        return _walk().apply("abort", {})
    st = load_state()
    st["notes"] = list(st.get("notes") or []) + ["aborted"]
    return _finish(st, "aborted")


def _advance(st: dict[str, Any]) -> dict[str, Any]:
    seq = steps()
    i = int(st.get("index") or 0) + 1
    st["beat"] = 0
    st["enroll_who"] = ""
    for k in ("confirm", "live", "enroll_go", "silence"):
        st.pop(k, None)
    if i >= len(seq):
        st["index"] = len(seq)
        st["step_id"] = "done"
        return _finish(st, "done")
    st["index"] = i
    st["step_id"] = seq[i]["id"]
    save_state(st)
    step = seq[i]
    if step["id"] == "present_regulars_enroll":
        roster = st.get("roster") or {}
        uid = slug(roster.get("user") or "")
        present = [
            p
            for p in (roster.get("present") or [])
            if slug(str(p.get("id") or p.get("display") or p)) != uid
        ]
        st["enroll_queue"] = present
        if not present:
            return _advance(st)
        who = present[0]
        st["enroll_who"] = str(who.get("display") or who.get("id") or "")
        save_state(st)
    return snapshot(st)


_NOT_A_NAME = re.compile(r"^(my|our|the|a|an|his|her|their|of|once|let'?s|lets|go|again|yes|yeah|no|nope|ok|okay|sure|"
                         r"course|just|me|nobody|none|everyone|that|this|it'?s|its|and|or|so|well|um|uh|hmm|mm|more|makes|sense)$", re.I)


def looks_like_a_name(display: str) -> bool:
    """A person record is made only for a name (the User 2026-09-14: "i have quite a few
    registrations. we arent sotering them right?" -- the roster had made people called
    "Let's go again. Makes no sense.", "Of course", "Once more", "My wife")."""
    head = str(display or "").split(",")[0].strip().rstrip(".")
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", head)
    if not 1 <= len(words) <= 2 or re.search(r"[.!?]", head):
        return False
    return not any(_NOT_A_NAME.match(w) for w in words)


def _existing_person_id(display: str) -> str:
    """The id of a person the house already has whose id or first name is this name, else ''."""
    first = (re.findall(r"[A-Za-z][A-Za-z'\-]*", str(display or "").split(",")[0]) or [""])[0].lower()
    if not first:
        return ""
    people = HOUSE_ID / "people"
    for r in _load_json(people / "_index.json").get("people") or []:
        if isinstance(r, dict) and (str(r.get("id") or "").lower() == first
                                    or (str(r.get("display") or "").split() or [""])[0].lower() == first):
            return str(r.get("id") or "")
    return first if (people / first).is_dir() else ""


def _known_people_hint() -> str:
    rows = _load_json(HOUSE_ID / "people" / "_index.json").get("people") or []
    names = [str(r.get("display") or r.get("id")) for r in rows if isinstance(r, dict)
             and looks_like_a_name(str(r.get("display") or r.get("id") or "")) and not str(r.get("id") or "").endswith("_tts")]
    if not names:
        return ""
    return ("\nPeople this house already knows: " + ", ".join(names[:20]) + ". Give FIRST NAMES only. When the answer "
            "means one of them, use that name exactly; a relation with no name (\"my wife\") stays as said.")


def _ensure_person(person_id: str, display: str, role: str = "") -> None:
    if not looks_like_a_name(display or person_id):
        return
    pid = _existing_person_id(display or person_id) or slug(str(display or person_id).split(",")[0])
    if not pid:
        return
    people = HOUSE_ID / "people"
    folder = people / pid
    folder.mkdir(parents=True, exist_ok=True)
    person = folder / "person.json"
    if not person.is_file():
        rec = {"id": pid, "display": display or pid, "active": True}
        if role:
            rec["role"] = role
        _write_json(person, rec)
    idx_path = people / "_index.json"
    idx = _load_json(idx_path)
    rows = list(idx.get("people") or [])
    if not any(str(r.get("id") or "") == pid for r in rows if isinstance(r, dict)):
        row = {"id": pid, "display": display or pid, "active": True}
        if role:
            row["role"] = role
        rows.append(row)
        idx["schema"] = idx.get("schema") or "house_people/v1"
        idx["people"] = rows
        _write_json(idx_path, idx)


def _write_roster(st: dict[str, Any]) -> None:
    roster = st.get("roster") or {}
    user = str(roster.get("user") or "").strip()
    uid = slug(roster.get("user_id") or user)
    roster["user_id"] = uid
    st["roster"] = roster
    if user and uid:
        _ensure_person(uid, user, role="user")
        pack_store.write_user(user, uid)        # the player's own record of the owner
        _first_boot_values_stored(st)       # first boot counts from here (the User 2026-09-14)

        def mut(site: dict[str, Any]) -> None:
            enroll = site.setdefault("enroll", {})
            if isinstance(enroll, dict):
                enroll["user_id"] = uid

        _patch_site(mut)
    for r in roster.get("regulars") or []:
        if isinstance(r, dict):
            _ensure_person(str(r.get("id") or ""), str(r.get("display") or ""), role="regular")
    pets = []
    for p in roster.get("pets") or []:
        if isinstance(p, dict) and p.get("name"):
            pets.append(
                {
                    "name": str(p.get("name")),
                    "look": str(p.get("look") or ""),
                    "kind": "pet",
                }
            )

    def mut_pets(site: dict[str, Any]) -> None:
        site["pets"] = pets

    if pets:
        _patch_site(mut_pets)


# THE ROSTER IS PARSED BY THE BRAIN (2026-09-12 pm). The splitter turned
# "It is just me." into a person to enrol (the User cancelled on it) and "Dog
# and a cat. <Pet>, the dog, is black and white…" into pets named Dog, the,
# is. Ops :8000 is up during the wizard and a short JSON-only completion
# takes about a second (Argus owns Oracle: foster the model, delete
# heuristics). The splitter stays as the fallback when the brain is away.
BRAIN_URL = _paths.brain_url()
BRAIN_MODEL = _paths.brain_model()
BRAIN_TIMEOUT_S = 5.0
# LILA'S BRAIN IS WHICHEVER IS UP (the User 2026-09-13: "lila brains should be active
# here"): ops :8000 in Standard, crew :8005 when Render parks ops. Probed at most
# every 30 s; tests never probe.
CREW_URL = os.environ.get("HOUSE_CREW_URL", "").rstrip("/")
_BRAIN_PICK: dict[str, Any] = {"at": 0.0, "url": BRAIN_URL, "model": BRAIN_MODEL}


def _brain_endpoint() -> tuple[str, str]:
    if not BRAIN_URL:
        return "", ""           # no Connect endpoint yet: the splitter answers instead of the brain
    if not _live_side_effects():
        return BRAIN_URL, BRAIN_MODEL
    now = time.time()
    if now - float(_BRAIN_PICK["at"]) < 30.0:
        return str(_BRAIN_PICK["url"]), str(_BRAIN_PICK["model"])
    url, model = BRAIN_URL, BRAIN_MODEL
    try:
        with urllib.request.urlopen(BRAIN_URL.rsplit("/v1/", 1)[0] + "/v1/models", timeout=1.5) as r:
            r.read(64)
    except Exception:
        url, model = CREW_URL + "/v1/chat/completions", "crew"
    _BRAIN_PICK.update(at=now, url=url, model=model)
    return url, model
_NOBODY = re.compile(r"^\W*(it'?s |it is |that'?s |that is )?(just|only) me\W*$|^\W*(no ?one|nobody|none|no)( else)?\W*$", re.I)
_ROSTER_ASKS = {
    0: ('The house asked "What should the house call you?". Reply ONLY with JSON {"name": "<the name as said, no punctuation>"}.', "name"),
    1: ('The house asked "Who else lives or works here regularly?". Reply ONLY with JSON {"people": ["<each person as said, e.g. Sam, my wife, my daughter>"]}. "just me", "nobody", "no one" → {"people": []}.', "people"),
    2: ('The house asked "Any pets? Tell me the name and what they look like.". Reply ONLY with JSON {"pets": [{"name": "<pet name>", "look": "<species and how it looks>"}]}. No pets → {"pets": []}.', "pets"),
    3: ('The house asked "And who is in the room right now?" (besides the User). Reply ONLY with JSON {"present": ["<each other person as said>"]}. "it is just me", "only me", "nobody" → {"present": []}.', "present"),
}


def _brain_parse(beat: int, raw: str) -> Any:
    """One structured completion; None when the brain is away or unclear."""
    ask = _ROSTER_ASKS.get(beat)
    if not ask or not raw.strip():
        return None
    prompt, key = ask
    url, model = _brain_endpoint()
    if not url:
        return None
    body = {
        "model": model, "temperature": 0, "max_tokens": 160,
        "messages": [
            {"role": "system", "content": "You turn one spoken answer into JSON for a house setup form. Output the JSON object only — no words, no code fence."},
            {"role": "user", "content": prompt + _known_people_hint() + "\nThe answer was: " + raw.strip()},
        ],
    }
    try:
        if model == "crew":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        out = _http_json(url, body, timeout=BRAIN_TIMEOUT_S)
        text = str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except Exception:
        return None
    text = text.strip("`").strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        d = json.loads(text[i:j + 1])
    except json.JSONDecodeError:
        return None
    return d.get(key) if isinstance(d, dict) and key in d else None


def _clean_name(n: Any) -> str:
    return re.sub(r"[.!?,;:]+$", "", str(n or "").strip()).strip()


def _say_list(xs: list[str]) -> str:
    xs = [str(x) for x in xs if str(x).strip()]
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def _roster_answer(st: dict[str, Any], text: str, pill: str) -> dict[str, Any]:
    """Each beat is parsed, READ BACK, and written only on a yes (the User 2026-09-13:
    "Let lila verify … verbal verify that logs got it right")."""
    roster = st.setdefault("roster", empty_state()["roster"])
    step = current_step(st) or {"id": "regulars_roster"}
    beat = int(st.get("beat") or 0)
    raw = (text or pill or "").strip()
    if beat <= 0:
        if not raw:
            return snapshot(st)
        got = _brain_parse(0, raw)
        name = _clean_name(got) if isinstance(got, str) and got.strip() else _clean_name(raw.split(",")[0])
        if not name or len(name.split()) > 3:
            return _lila_live(st, step, raw, _goal_for(step, st), reason="no clear name")
        return _ask_confirm(st, "regulars_roster", f"I'll call you {name}. Is that right?", {"kind": "roster_name", "name": name})
    if beat == 1:
        got = _brain_parse(1, raw)
        if isinstance(got, list):
            names = [_clean_name(n) for n in got if _clean_name(n)]
        else:
            names = [] if (not raw or _NOBODY.match(raw)) else [_clean_name(n) for n in re.split(r"[,;/]| and ", raw, flags=re.I) if _clean_name(n)]
            if raw.lower() in ("none", "just me", "nobody", "no one", "skip"):
                names = []
        regs = [{"id": slug(n), "display": n} for n in names if slug(n)]
        say = ("So " + _say_list([r["display"] for r in regs]) + " — is that right?") if regs else "Just you, then — is that right?"
        return _ask_confirm(st, "regulars_roster", say, {"kind": "roster_regulars", "regulars": regs})
    if beat == 2:
        _prearm_register()   # the last roster beat: park the camera before the press
        pets = []
        got = _brain_parse(2, raw)
        if isinstance(got, list):
            for it in got:
                if isinstance(it, dict) and _clean_name(it.get("name")):
                    pets.append({"name": _clean_name(it.get("name")), "look": str(it.get("look") or "").strip()})
        elif raw and raw.lower() not in ("none", "no", "no pets", "skip") and not _NOBODY.match(raw):
            for chunk in re.split(r"[,;]", raw):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if "·" in chunk:
                    pname, look = [x.strip() for x in chunk.split("·", 1)]
                elif " - " in chunk:
                    pname, look = [x.strip() for x in chunk.split(" - ", 1)]
                else:
                    parts = chunk.split(None, 1)
                    pname, look = parts[0], (parts[1] if len(parts) > 1 else "")
                pets.append({"name": pname, "look": look})
        say = (_say_list([p["name"] + (", " + p["look"] if p.get("look") else "") for p in pets]) + " — is that right?") if pets else "No pets — is that right?"
        return _ask_confirm(st, "regulars_roster", say, {"kind": "roster_pets", "pets": pets})
    got = _brain_parse(3, raw)
    if isinstance(got, list):
        names = [_clean_name(n) for n in got if _clean_name(n)]
    elif not raw or _NOBODY.match(raw) or raw.lower() in ("just me", "me", "only me"):
        names = []                      # nobody ELSE: the User is here by definition
    else:
        names = [_clean_name(n) for n in re.split(r"[,;/]| and ", raw, flags=re.I) if _clean_name(n)]
    known = [{"id": roster.get("user_id"), "display": roster.get("user")}]
    known += list(roster.get("regulars") or [])
    if not names:
        present = [known[0]] if known[0].get("id") else []
    else:
        present = []
        for n in names:
            pid = slug(n)
            hit = next((k for k in known if k.get("id") == pid or str(k.get("display") or "").lower() == n.lower()), None)
            present.append(hit or {"id": pid, "display": n})
    # A PET IS IN THE ROOM, NOT ON THE REGISTER (the User 2026-09-14: "She tried to register <the pet>"
    # — "Right now it's you and <the pet>", "Sure", then enroll_start '<the pet>'): names from the pets beat
    # are not people to register.
    pet_ids = {slug(str(pt.get("name") or "")) for pt in (roster.get("pets") or []) if isinstance(pt, dict)}
    present = [p for p in present if p.get("id") and p.get("id") not in pet_ids]
    others = [str(p.get("display") or p.get("id")) for p in present if p.get("id") != roster.get("user_id")]
    say = ("Right now it's you and " + _say_list(others) + " — is that right?") if others else "Just you in the room right now — is that right?"
    return _ask_confirm(st, "regulars_roster", say, {"kind": "roster_present", "present": present})


def _prearm_register() -> None:
    """Fire-and-forget: the coach parks the camera on the desk, closes the
    ear and cues the listen loop before the Register chapter arrives, so
    its t=0 is Lila's first line (the User 2026-09-11: "bang on New ID")."""
    import threading

    if not _live_side_effects():
        return

    def _go() -> None:
        try:
            _http_json(_paths.enroll_url() + "/prearm", {"station": "desk"}, timeout=4.0)
        except Exception:
            pass

    threading.Thread(target=_go, name="register-prearm", daemon=True).start()


# INIT'S CAMERA UNDER REGISTER'S TOKEN (the User 2026-10-02, Init step 4): with HOUSE_REGISTER_TOKEN=1 the room sweep and
# the Crissa-seat look ask presence for the `register` token (below the User's joystick, above Secure's door watch), so
# Secure stays on and the camera goes home when they finish. Off: the calls are unchanged.
def _cam_body(body: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("HOUSE_REGISTER_TOKEN", "").strip().lower() in ("1", "on", "true", "yes"):
        return {**body, "token": "register"}
    return body


def _http_json_bearer(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + _paths.brain_key()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode() or "{}")
    return out if isinstance(out, dict) else {}


def _http_json(url: str, payload: dict[str, Any], timeout: float = 8.0) -> dict[str, Any]:
    if BRAIN_URL and url == BRAIN_URL and _paths.brain_key():
        return _http_json_bearer(url, payload, timeout)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}


_GO = re.compile(
    r"\b(let'?s go|lets go|go ahead|go|okay|ok|yes|yeah|yep|continue|ready|begin|sure)\b",
    re.I,
)
_SKIP = re.compile(r"\b(skip|later|not now|no thanks)\b", re.I)
# EVERY CHOICE IS SPOKEN (the User 2026-09-13: "get rid of all buttons but for cancel …
# booleans all verbal gates").
_YES = re.compile(r"\b(yes|yeah|yep|yup|correct|right|that'?s right|exactly|sure|okay|ok|mm-?hm|uh-?huh|please do|go ahead)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|wrong|not quite|incorrect|that'?s not)\b", re.I)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_RESTART_V = re.compile(r"\b(start over|start again|from the (top|beginning)|restart)\b", re.I)
_RESUME_V = re.compile(r"\b(carry on|carry|garry ?on|keep going|keep on|continue|go on|resume)\b", re.I)   # "Garryon." (2026-09-14)
_LATER_V = re.compile(r"\b(later|another time|not now|stop|cancel|quit)\b", re.I)


def is_yes(text: str) -> bool:
    return bool(_YES.search(text or "")) and not bool(_NO.search(text or ""))


def is_no(text: str) -> bool:
    return bool(_NO.search(text or "")) and not bool(re.search(r"\b(yes|yeah|correct)\b", text or "", re.I))


def spoken_hour(text: str) -> int | None:
    m = re.search(r"\b(1[0-2]|[1-9])\b", text or "")
    if m:
        return int(m.group(1))
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in _NUM_WORDS:
            return _NUM_WORDS[w]
    return None


def is_go(text: str) -> bool:
    t = re.sub(r"[^a-z0-9 ']+", " ", (text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return bool(_GO.search(t))


def is_skip(text: str) -> bool:
    return bool(_SKIP.search(text or ""))


def mouth_busy() -> bool:
    if host_busy():
        return True
    # HER LIVE LINE COUNTS FROM THE MOMENT IT IS SENT (the User 2026-09-14 02:17: the ear heard her own
    # "What name would you like me to use?" and "So <A> and <B>, is that right?"): the TTS
    # gate turns on only once Cosy is on air, 1-2.5 s after speak(), so the wall's "until not
    # speaking" fell through and opened the ear under her line. A live house-speak is speaking.
    if _speak_proc is not None and _speak_proc.poll() is None:
        return True
    p = _paths.STATE / "tts-gate.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(isinstance(d, dict) and d.get("speaking"))


def heard_since(since: float, ignore: str = "", skip=None, earliest: bool = False, salvage=None) -> dict[str, Any]:
    """A human STT turn after `since`: the newest, or with earliest=True the first.
    `skip(text)` drops a turn (her own line heard back). Does not open house Mic.

    If the continuous ear is already open, /v1/listen is 409 — read turns.
    """
    try:
        with urllib.request.urlopen(f"{STT}/v1/turns?n=16", timeout=2) as r:
            raw = json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:80]}
    items = raw if isinstance(raw, list) else (raw.get("turns") or [])
    ign = (ignore or "").strip().lower()
    rows = []
    for t in items:
        if not isinstance(t, dict):
            continue
        st = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
        if st.get("source") == "argus_describe":
            continue
        vetoed = str(t.get("reason") or "") == "tts_echo_veto" or st.get("source") == "tts_echo_veto"
        who = str(t.get("person_id") or t.get("who") or t.get("name") or "").lower()
        if who in {"crissa", "announcer", "announcer", "ada"}:
            continue
        text = str(t.get("text") or t.get("transcript") or "").strip()
        if vetoed:
            # HER VOICE LED THE TURN, HIS ANSWER RODE ITS TAIL (the User 2026-09-14 12:49:28: "I'll call you the User.
            # Is that right? Yes, that's right." was vetoed whole): only a salvage of what follows her line
            text = salvage(text) if salvage is not None else ""
            if not text:
                continue
        if len(text) < 2:
            continue
        if ign and (text.lower() in ign or ign in text.lower()):
            continue
        if skip is not None and skip(text):
            continue
        ts = float(t.get("ts") or t.get("t") or 0)
        if ts > 1e12:
            ts /= 1000.0
        if since and ts and ts < since - 0.15:
            continue
        rows.append((ts, text, t.get("duration_s")))
    if not rows:
        return {"ok": True, "text": "", "ts": 0.0}
    rows.sort(key=lambda r_: r_[0])
    ts, text, dur = rows[0] if earliest else rows[-1]
    return {"ok": True, "text": text, "ts": ts, "duration_s": dur}


# the last one-shot listen's utterance length: "嗯" from a short window is a "yeah"
_LAST_LISTEN: dict[str, Any] = {}
# Audio8's ARK ASR writes a short English "yeah" / "mm-hm" as the Chinese filler 嗯 (the User
# 2026-09-14 02:18: "it just hung when i said yeah" — two 嗯 after the readback were dropped
# as silence). In a yes/no moment a short one is a yes; a long noisy window is only unclear.
_FILLER = re.compile(r"^\W*(嗯+|嗯嗯|恩+|对|是的?|好的?|行)\W*$")
FILLER_YES_MAX_S = 2.5


def _listen_once(ignore: str = "", st: Optional[dict[str, Any]] = None) -> str:
    """Prefer turns while the house ear is open (409 on /v1/listen).

    PACE (HOUSE_INIT_PACE, 1.5 shipped) stretches the window for the
    elderly, and a breath after Lila's line keeps the ear from opening on
    her last word."""
    if EAR_LIVE and st is not None and _ensure_ear_open():
        return _listen_live(ignore, st)
    wait = _host_end_ts + BREATH_SEC * PACE - time.time()
    if 0 < wait < 10:
        time.sleep(wait)
    t0 = time.time()
    deadline = t0 + 5.0 * PACE
    while time.time() < deadline:
        heard = heard_since(t0 - 0.05, ignore=ignore)
        if heard.get("text"):
            _LAST_LISTEN.clear(); _LAST_LISTEN["dur"] = heard.get("duration_s")
            return str(heard["text"])
        try:
            # ONE window that ends when the User stops talking (rail-stt
            # end_on_silence, 2026-09-12): the seconds are the cap for
            # silence, not the wait. The old fixed 4–5 s window ran to its
            # end after a one-second answer — that was the dead air.
            req = urllib.request.Request(
                f"{STT}/v1/listen",
                data=json.dumps({"seconds": int(round(LISTEN_SEC * PACE)) + 3,
                                 "end_on_silence": True, "hangover_sec": LISTEN_HANGOVER_SEC}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                time.sleep(0.3)
                continue
            return ""
        except Exception:
            time.sleep(0.3)
            continue
        if isinstance(data, dict):
            text = str(data.get("text") or data.get("transcript") or "").strip()
            if text:
                _LAST_LISTEN.clear(); _LAST_LISTEN["dur"] = data.get("duration_s")
                return text
        break
    return ""


def _mark_asked() -> None:
    """Answers count from now (the live ear keeps what it heard before; that was not an answer)."""
    st = load_state()
    if str(st.get("status") or "") == "running":
        st["asked_at"] = time.time()
        save_state(st)


def _listen_live(ignore: str, st: dict[str, Any]) -> str:
    """PARALLEL, NOT TURNS (the User 2026-09-14): the answer is the first human turn since her
    question began. Heard while she is still talking, it cuts her line; otherwise the ear
    waits EAR_WINDOW_SEC after her mouth closes."""
    since = max(float(st.get("asked_at") or 0.0), float(st.get("heard_upto") or 0.0)) or time.time()
    t_call = time.time()
    quiet_at: float | None = None
    while time.time() - t_call < EAR_LISTEN_CAP_SEC:
        heard = heard_since(since - 0.05, ignore=ignore, skip=lambda text: _echo_of_host(text, st), earliest=True,
                            salvage=lambda text: _salvage_echo(text, st))
        if heard.get("text"):
            upto = float(heard.get("ts") or time.time()) + 0.01
            said = strip_echo(str(heard["text"]), st)
            if not said:                        # only her own line came back: keep listening
                since = upto
                st["heard_upto"] = upto
                continue
            heard["text"] = said
            if mouth_busy():
                _stop_voices()                 # she stops when the User answers over her
            st["heard_upto"] = upto
            fresh = load_state()
            if str(fresh.get("run_id") or "") == str(st.get("run_id") or ""):
                fresh["heard_upto"] = upto
                save_state(fresh)
            _LAST_LISTEN.clear(); _LAST_LISTEN["dur"] = heard.get("duration_s")
            return str(heard["text"])
        if mouth_busy():
            quiet_at = None
        elif quiet_at is None:
            quiet_at = time.time()
        elif time.time() - quiet_at > EAR_WINDOW_SEC * PACE:
            break
        time.sleep(0.25)
    return ""


def apply(action: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body = body or {}
    act = (action or "").strip().lower()
    if act == "first_boot_force" and identity_lock().get("locked"):
        # THE FIRST-BOOT TICK IS THE USER'S (the User 2026-10-04): once a User is enrolled, a Guest may not flip it.
        # Kill HOUSE_INIT_FORCE_NEEDS_AUTH=0.
        if os.environ.get("HOUSE_INIT_FORCE_NEEDS_AUTH", "1").strip().lower() not in ("0", "false", "no", "off"):
            return {"ok": False, "error": NEEDS_AUTH_TEXT, "forced": first_boot_forced(), "first_boot": is_first_boot()}
    if _v2() and act not in ("first_boot_force", "first_boot_status", "identity_lock"):
        return _v2().apply(act, body)
    if act in ("first_boot_force", "first_boot_status"):          # TEMP-FIRST-BOOT: the Control Panel tick
        if act == "first_boot_force":
            f = _first_boot_force_file()
            if body.get("on"):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(_now() + "\n", encoding="utf-8")
            else:
                f.unlink(missing_ok=True)
            # who moved it, and when (the User 2026-09-14: "why did the tick untick?")
            print("[init] first-boot tick %s by %s" % ("ON" if body.get("on") else "OFF", str(body.get("by") or "control-panel")), flush=True)
        return {"ok": True, "forced": first_boot_forced(), "first_boot": is_first_boot()}
    if act == "identity_lock":
        return identity_lock()
    if act == "start":
        return start(force=bool(body.get("force")))
    if act == "abort":
        return abort()
    if act == "restart":
        return start(force=True)
    if act == "resume":
        # CARRY ON from the Restart menu: the paused conversation is over,
        # what she learned is written; the next chapter
        st = load_state()
        if st.get("status") != "running":
            return snapshot(st)
        st.pop("frozen", None)
        st.setdefault("notes", []).append("resumed from the pause menu")
        return _advance(st)
    if act == "attend":
        return attend()
    if act == "snapshot":
        return snapshot()
    # Anything the user actually did is proof the glass is still there.
    _touch()
    if act in ("say", "again"):
        st = load_state()
        if st.get("status") != "running":
            return snapshot(st)
        step = current_step(st)
        if step:
            st["asked_at"] = time.time()       # the live ear takes answers from here on, even over her line
            st.pop("mode_note", None)          # Render is up: the wall said "Switching to Render…" until now
            save_state(st)
        plate = plate_for(st) if step else {}
        if act == "again":
            global _last_speak
            _last_speak = (0.0, "")
            body = dict(body, loop=True)
        if step and body.get("loop") and step.get("id") == "welcome" and plate.get("cinema") and act == "say":
            # CINEMA: the wall paints Crissa's file itself and posts cinema_done;
            # nothing to play here yet (the conductor stays on the desk)
            if str(st.get("cinema_state") or "") == "":
                st["cinema_state"] = "playing"
                save_state(st)
            return snapshot(st)
        if step and body.get("loop"):
            # LILA'S CLIP when it is rendered (the wall drives with loop=true)
            keys = host_ready(plate.get("host_key"))
            if keys:
                if act == "again" and host_wav(HOST_AGAIN_KEY) is not None:
                    keys = [HOST_AGAIN_KEY] + keys
                if host_play(keys):
                    if act == "say" and step.get("id") == "media_ghost_mask":
                        _auto_defer_door(st)
                    if act == "say" and step.get("id") in RAIL_STEPS:
                        _auto_rail(step["id"])
                    if act == "say" and step.get("id") == "rehearsal":
                        _after_wrap(st)
                    return snapshot(st)
        text = say_for(st) if step else ""
        if step and not text and body.get("loop"):
            # no clip yet: a plate the page carries as a clip is spoken here (2026-09-09)
            text = str(plate.get("say") or "")
        if step and text:
            speak(text, str(step.get("voice") or "crissa"))
            if act == "say" and body.get("loop") and step.get("id") in RAIL_STEPS:
                _auto_rail(step["id"])     # no clip yet: her live line, then the work
            if act == "say" and body.get("loop") and step.get("id") == "media_ghost_mask":
                _auto_defer_door(st)
            if act == "say" and body.get("loop") and plate.get("closed"):
                _auto_advance_after_line(str(step.get("id") or ""), int(st.get("beat") or 0))
        return snapshot(st)
    st = load_state()
    if st.get("status") != "running":
        # A late click or listen after Cancel never restarts the routine: only
        # an explicit start does (the User 2026-09-13, the restart trap).
        return snapshot(st)
    step = current_step(st)
    if not step:
        return _finish(st, "done")
    if act == "listen":
        text = str(body.get("text") or "").strip()
        if not text:
            text = _listen_once(ignore=say_for(st), st=st)
            fresh = load_state()          # the ear took seconds: Cancel, or another listen, may have moved us
            if (str(fresh.get("status") or "") != "running" or str(fresh.get("run_id") or "") != str(st.get("run_id") or "")
                    or str(fresh.get("step_id") or "") != str(st.get("step_id") or "") or int(fresh.get("beat") or 0) != int(st.get("beat") or 0)):
                return snapshot(fresh)
            st = fresh
        if text and _FILLER.fullmatch(text):
            conf_q = isinstance(st.get("confirm"), dict) and st["confirm"].get("sid") == step["id"]
            yes_no = bool(st.get("frozen")) or conf_q or step["id"] in ("welcome", "present_regulars_enroll")
            try:
                dur = float(_LAST_LISTEN.get("dur"))
            except (TypeError, ValueError):
                dur = 99.0
            if yes_no and dur <= FILLER_YES_MAX_S:
                text = "yes"
            elif yes_no:
                goal = ("a yes or no to: " + str(st["confirm"].get("say") or "")) if conf_q else _goal_for(step, st)
                return _lila_live(st, step, "", goal, reason="heard only 'mm'", keep_confirm=conf_q)
            else:
                text = ""
        if not text:
            return _silence(st, step)
        if _AGAIN.search(text) and len(text.split()) <= 5:
            return apply("again", {"loop": True})
        if _echo_of_host(text, st):
            return snapshot(st)            # her own line coming back through the ear
        if (st.get("frozen") or (isinstance(st.get("confirm"), dict) and st["confirm"].get("sid") == step["id"])
                or step["id"] == "present_regulars_enroll"):
            return _handle_answer(st, step, {"text": text})
        if is_skip(text) and (
            step.get("optional")
            or step["id"]
            in (
                "present_regulars_enroll",
                "training_window",
                "news_topics",
                "media_ghost_mask",
            )
        ):
            st["answers"][step["id"]] = {"skipped": True, "via": "voice"}
            return _advance(st)
        if (is_go(text) or (step["id"] == "welcome" and is_yes(text))) and step["id"] in (
            "welcome",
            "evidence_ack",
            "voice_lock",
            "rehearsal",
            "room_scan",
            "media_ghost_mask",
        ):
            return _handle_next(st, step, {})
        if step["id"] == "welcome":
            # the welcome moves only on a go word (2026-09-12 pm). Her own line is
            # filtered above; anything else heard gets Lila, not silence (the User
            # 2026-09-13: the ~30 s under her listen loop after the handoff)
            return _lila_live(st, step, text, _goal_for(step, st), reason="not a yes to begin")
        body = dict(body)
        body["text"] = text
        act = "answer"
    if act == "skip":
        if not step.get("optional") and step["id"] not in (
            "present_regulars_enroll",
            "training_window",
            "news_topics",
            "media_ghost_mask",
        ):
            return snapshot(st)
        st["answers"][step["id"]] = {"skipped": True}
        if step["id"] == "present_regulars_enroll":
            q = list(st.get("enroll_queue") or [])[1:]
            st["enroll_queue"] = q
            if q:
                st["enroll_who"] = str(q[0].get("display") or q[0].get("id") or "")
                save_state(st)
                return snapshot(st)
        return _advance(st)
    if act == "cinema_done":
        # the wall finished Crissa's file with the box on Lila (her listening
        # loop is on the conductor by now). Lila answers, then her welcome;
        # the wall folds the box into the film when cinema_state turns "min".
        if step["id"] != "welcome" or str(st.get("cinema_state") or "") == "done":
            return snapshot(st)
        st["cinema_state"] = "box"
        save_state(st)

        def _after() -> None:
            # Lila into the box first: the desk → her plate, and her listening loop
            try:
                if _conductor_hub() not in (HOST_HUB, ""):
                    _http_json(f"{CONDUCTOR_URL}/v1/play", {"key": HOST_ENTER_KEY, "force": True, "by": "init"}, timeout=3.0)
                    time.sleep(HOST_ENTER_SEC)
                _http_json(f"{CONDUCTOR_URL}/v1/cue", {"key": HOST_LISTEN_KEY, "ttl": 0, "bridge": False, "by": "init"}, timeout=3.0)
            except Exception:
                pass
            keys = host_ready(HOST_THANKS_CRISSA_KEY)
            if keys:
                host_play(keys)
                for _ in range(100):
                    if not host_busy():
                        break
                    time.sleep(0.2)
            else:
                speak(THANKS_CRISSA_LINE, "host-lila")
                _wait_spoken(start_s=4.0, max_s=12.0)
            st2 = load_state()
            if st2.get("status") != "running" or str(st2.get("step_id") or "") != "welcome":
                return
            st2["cinema_state"] = "min"         # the wall folds the box as her welcome starts
            save_state(st2)
            wk = host_ready([k for k in (HOST_KEYS.get("welcome") or []) if not str(k).startswith(HOST_OPENER_PREFIX)])
            if wk:
                host_play(wk)
                time.sleep(0.5)
                for _ in range(300):
                    if not host_busy():
                        break
                    time.sleep(0.2)
            else:
                speak(str(plate_for(st2).get("say") or ""), "host-lila")
                _wait_spoken(start_s=4.0, max_s=30.0)
            st_q = load_state()
            if WELCOME_ASK and st_q.get("status") == "running" and str(st_q.get("step_id") or "") == "welcome":
                _mark_asked()                      # the film's audio went through the live ear: answers count from here
                speak(WELCOME_ASK, "host-lila")
                _wait_spoken(start_s=3.0, max_s=10.0)
            # "done" only once her welcome has ENDED (+ a breath): the wall
            # opens the ear on "done", and set at the line's start it heard
            # "I'm Lila, the house designer" as the User's answer (13:55 run)
            time.sleep(BREATH_SEC * PACE)
            st3 = load_state()
            if st3.get("status") == "running" and str(st3.get("step_id") or "") == "welcome":
                st3["cinema_state"] = "done"
                save_state(st3)

        threading.Thread(target=_after, name="init-cinema-after", daemon=True).start()
        return snapshot(st)
    if act == "enroll_done":
        st.pop("enroll_go", None)
        if step["id"] == "user_identity":
            try:
                pack_store.sync_from_store(slug((st.get("roster") or {}).get("user_id") or (st.get("roster") or {}).get("user") or ""), HOUSE_ID)
            except Exception as e:  # noqa: BLE001
                print(f"[init] enroll.json: {type(e).__name__}", flush=True)
            return _advance(st)
        if step["id"] == "present_regulars_enroll":
            q = list(st.get("enroll_queue") or [])[1:]
            st["enroll_queue"] = q
            if q:
                st["enroll_who"] = str(q[0].get("display") or q[0].get("id") or "")
                save_state(st)
                return snapshot(st)
            return _advance(st)
        return snapshot(st)
    if act in ("next", "continue"):
        return _handle_next(st, step, body)
    if act == "answer":
        return _handle_answer(st, step, body)
    return snapshot(st)


def _handle_next(st: dict[str, Any], step: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    sid = step["id"]
    if sid == "welcome":
        return _advance(st)
    if sid == "regulars_roster":
        return _roster_answer(st, str(body.get("text") or ""), str(body.get("pill") or ""))
    if sid == "user_identity":
        return snapshot(st)
    if str(step.get("kind") or "") == "dialogue":
        # "That's enough" on the glass: keep what she has, move on
        return _dialogue_finish(st, step, "")
    if sid in RAIL_STEPS:
        t = _rail_threads.get(sid)
        if t is not None and t.is_alive():
            return snapshot(st)          # the work is running; it advances itself
    if sid == "room_scan":
        out = _http_json(
            f"{PRESENCE}/v1/rescan_room",
            _cam_body({"init": True, "range": "full"}),
            timeout=12,
        )
        st["answers"][sid] = {"ok": bool(out.get("ok", True)), "error": out.get("error")}
        if out.get("error"):
            st.setdefault("notes", []).append("room_scan: " + str(out.get("error"))[:120])
        return _advance(st)
    if sid == "evidence_ack":
        st["answers"][sid] = True
        return _advance(st)
    if sid == "voice_lock":
        st["answers"][sid] = {"mic": "off"}
        return _advance(st)
    if sid == "rehearsal":
        st["answers"][sid] = True
        return _finish(st, "done")
    if sid == "media_ghost_mask":
        st["answers"][sid] = {"deferred": True}
        st.setdefault("notes", []).append("printed-face mask later from Control Panel")
        return _advance(st)
    if sid in ("training_window", "news_topics") and step.get("optional"):
        return _advance(st)
    return _handle_answer(st, step, body)


def _handle_answer(st: dict[str, Any], step: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    sid = step["id"]
    text = str(body.get("text") or "").strip()
    pill = str(body.get("pill") or "").strip()
    if st.get("frozen"):
        return _frozen_answer(st, step, text or pill)
    conf = st.get("confirm") if isinstance(st.get("confirm"), dict) else None
    if conf and conf.get("sid") == sid:
        return _confirm_answer(st, step, text or pill)
    if sid == "present_regulars_enroll" and text:
        return _register_someone_answer(st, step, text)
    if str(step.get("kind") or "") == "dialogue":
        return _dialogue_answer(st, step, text, pill)
    hour = body.get("hour")
    if sid == "regulars_roster":
        return _roster_answer(st, text, pill)
    if sid == "camera_orientation":
        tl = text.lower()
        yes = pill.lower() in ("yes", "right", "yes — on the right", "on the right") or (
            "right" in tl and "left" not in tl
        ) or (is_yes(text) and "left" not in tl)
        no = pill.lower() in ("no", "left", "no — on the left", "on the left") or (
            "left" in tl and "right" not in tl
        ) or (is_no(text) and "right" not in tl)
        tries = int(st.get("polarity_tries") or 0)
        if yes:

            def mut(site: dict[str, Any]) -> None:
                cam = site.setdefault("camera", {})
                if isinstance(cam, dict):
                    cam["orientation"] = "true_handed"
                    cam["verified"] = _now()[:10]

            _patch_site(mut)
            st["answers"][sid] = {"orientation": "true_handed"}
            return _advance(st)
        if no and tries == 0:
            st["polarity_tries"] = 1
            st.setdefault("notes", []).append("picture mirrored — flip and wave again")
            save_state(st)
            speak("Then the picture is mirrored. I've noted it — wave once more.", "host-lila")
            return snapshot(st)
        if no:

            def mut2(site: dict[str, Any]) -> None:
                cam = site.setdefault("camera", {})
                if isinstance(cam, dict):
                    cam["orientation"] = "mirrored"
                    cam["verified"] = _now()[:10]
                    cam["hflip_at_grabber"] = True

            _patch_site(mut2)
            st["answers"][sid] = {"orientation": "mirrored"}
            return _advance(st)
        return _lila_live(st, step, text, _goal_for(step, st), reason="no right or left heard")
    if sid == "media_speakers":
        internal = pill.lower() in ("a", "internal", "this computer") or bool(
            re.search(r"\b(computer|internal|laptop|this machine)\b", text, re.I)
        )
        if internal or text.lower() in ("a", "this computer", "internal"):

            def mut(site: dict[str, Any]) -> None:
                site["media_mode"] = "internal"
                site["media_station"] = None

            _patch_site(mut)
            _http_json(MEDIA, {"mode": "internal", "station": None}, timeout=4)
            st["answers"][sid] = {"mode": "internal"}
            return _advance(st)
        h = None
        if hour is not None:
            try:
                h = int(hour)
            except (TypeError, ValueError):
                h = None
        ans_m = (st.get("answers") or {}).get(sid) or {}
        need_hour = isinstance(ans_m, dict) and bool(ans_m.get("need_hour"))
        external = pill.lower() in ("b", "speakers", "speakers in the room") or bool(
            re.search(r"\b(speakers?|room|external|stereo|sound ?bar|tv|television)\b", text, re.I))
        if h is None and need_hour:
            h = spoken_hour(text)
        if h is None:
            if external and not need_hour:
                st["answers"][sid] = {"mode": "external", "need_hour": True}
                _bump_turn(st)
                save_state(st)
                return snapshot(st)
            return _lila_live(st, step, text, _goal_for(step, st), reason="no computer, speakers or hour heard")
        if hour is not None:
            return _commit(st, step, {"kind": "media_hour", "hour": h})
        return _ask_confirm(st, sid, f"The speakers are at {h} o'clock. Is that right?", {"kind": "media_hour", "hour": h})
    if sid == "training_window":
        window = text or pill or "18:00-02:00"

        def mut(site: dict[str, Any]) -> None:
            site["training_window"] = window

        _patch_site(mut)
        st["answers"][sid] = {"window": window}
        return _advance(st)
    if sid == "brief_interview":
        cards = body.get("cards")
        if not isinstance(cards, list):
            cards = ["weather", "headline", "house"]
        uid = slug((st.get("roster") or {}).get("user_id") or "")
        if not uid:
            site = _load_json(SITE_FILE)
            uid = str((site.get("enroll") or {}).get("user_id") or "")
        if uid:
            brief = {
                "wake_hour": text or "",
                "greeting": text or "",
                "cards": [str(c) for c in cards],
            }
            _write_json(HOUSE_ID / "people" / uid / "brief.json", brief)
        st["answers"][sid] = {"cards": cards, "text": text}
        return _advance(st)
    if sid == "news_topics":
        ids = body.get("topics")
        try:
            from news_topics import apply_freeform, apply_ids

            if isinstance(ids, list) and ids:
                rec = apply_ids([str(x) for x in ids])
            elif text:
                rec = apply_freeform(text, file_card=True)
            else:
                rec = apply_ids(["general"])
        except Exception as e:
            rec = {"error": str(e)[:160]}
        st["answers"][sid] = rec
        return _advance(st)
    if sid == "seat_map":
        sitters = body.get("sitters")
        if not isinstance(sitters, dict) and text:
            return _ask_confirm(st, sid, f"I've noted: {text}. Is that right?", {"kind": "seats", "text": text})
        if isinstance(sitters, dict):

            def mut(site: dict[str, Any]) -> None:
                stn = site.setdefault("stations", {})
                if isinstance(stn, dict):
                    stn["sitters"] = sitters

            _patch_site(mut)
        st["answers"][sid] = {"sitters": sitters or text}
        return _advance(st)
    st["answers"][sid] = {"text": text, "pill": pill}
    return _advance(st)


# ── EVERY CHOICE SPOKEN, READ BACK, AND LILA LIVE WHEN IT IS NOT CLEAR ─────────
# (the User 2026-09-13: "Wherever we get voice input if not parsed it should bail out to
# lila live to get it back on track.") A readback waits for a yes before anything is
# written; a no, an unclear answer, or three silent listens hand the turn to Lila in
# her own words (her brain on ops, or crew when Render parks ops); after LIVE_MAX
# tries an optional step is skipped and a required one pauses on the spoken menu.
LIVE_MAX = 2
SILENCE_NUDGE = 3


def _bump_turn(st: dict[str, Any]) -> None:
    st["turn"] = int(st.get("turn") or 0) + 1


def _goal_for(step: dict[str, Any], st: dict[str, Any]) -> str:
    sid = str(step.get("id") or "")
    beat = int(st.get("beat") or 0)
    if st.get("frozen"):
        return "whether to carry on with the setup, start over, or leave it for later"
    if sid == "welcome":
        return "whether they are ready to begin (a yes)"
    if sid == "regulars_roster":
        return ["the name the house should call them",
                "who else lives or works here regularly, or that it is just them",
                "any pets, with each name and what they look like, or none",
                "who else is in the room right now, or nobody"][min(max(beat, 0), 3)]
    if sid == "camera_orientation":
        return "whether their wave shows on the RIGHT of the picture (yes) or the left (no)"
    if sid == "media_speakers":
        ans = (st.get("answers") or {}).get(sid) or {}
        if isinstance(ans, dict) and ans.get("need_hour"):
            return "which hour on the clock the room speakers sit at (twelve is the door)"
        return "whether the sound comes from this computer or from speakers in the room"
    if sid == "seat_map":
        return "where each person usually sits, as hours on the clock"
    if sid == "room_clock":
        return ("where the door is, where the TV or speakers are (or that sound only plays on this computer), "
                "and where each person usually sits, as hours on the clock")
    if sid == "present_regulars_enroll":
        return "whether to register %s now (yes) or later" % (st.get("enroll_who") or "them")
    return str(step.get("title") or step.get("prompt") or sid.replace("_", " "))


def _ask_confirm(st: dict[str, Any], sid: str, say: str, commit: dict[str, Any]) -> dict[str, Any]:
    st["confirm"] = {"sid": sid, "beat": int(st.get("beat") or 0), "say": say, "commit": commit, "at": time.time()}
    _remember_said(st, say)
    st.pop("live", None)
    _bump_turn(st)
    save_state(st)
    return snapshot(st)


def _commit(st: dict[str, Any], step: dict[str, Any], commit: dict[str, Any]) -> dict[str, Any]:
    kind = str(commit.get("kind") or "")
    roster = st.setdefault("roster", empty_state()["roster"])
    st.pop("live", None)
    st.pop("confirm", None)
    if kind == "roster_name":
        roster["user"] = str(commit.get("name") or "")
        roster["user_id"] = slug(roster["user"])
        st["beat"] = 1
    elif kind == "roster_regulars":
        roster["regulars"] = list(commit.get("regulars") or [])
        st["beat"] = 2
        _prescan_start()          # the room sweep runs under the pets beat
    elif kind == "roster_pets":
        roster["pets"] = list(commit.get("pets") or [])
        st["beat"] = 3
    elif kind == "roster_present":
        roster["present"] = list(commit.get("present") or [])
        _write_roster(st)
        st["answers"]["regulars_roster"] = roster
        return _advance(st)
    elif kind == "media_hour":
        h = int(commit.get("hour") or 0)

        def mut(site: dict[str, Any]) -> None:
            site["media_mode"] = "external"
            site["media_station"] = h

        _patch_site(mut)
        if _live_side_effects():
            _http_json(MEDIA, {"mode": "external", "station": h}, timeout=4)
        st["answers"][str(step.get("id") or "media_speakers")] = {"mode": "external", "station": h}
        return _advance(st)
    elif kind == "seats":
        st["answers"][str(step.get("id") or "seat_map")] = {"sitters": commit.get("text")}
        return _advance(st)
    else:
        return _advance(st)
    _bump_turn(st)
    save_state(st)
    return snapshot(st)


_CORRECTION_LEAD = re.compile(r"^\W*(no|nope|nah|not quite|wrong|actually|sorry)\b[\s,.:;!\-]*|^\W*(it'?s|it is|that'?s|they'?re|they are)\s+", re.I)


def correction_text(text: str) -> str:
    """ "No, it's <A> and <B>." -> "<A> and <B>." ('' when nothing but a no is left)."""
    t = str(text or "").strip()
    for _ in range(3):
        t2 = _CORRECTION_LEAD.sub("", t, count=1).strip()
        if t2 == t:
            break
        t = t2
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", t) if not re.fullmatch(r"(no|yes|the|a|and|is|right|wrong|ok|okay)", w, re.I)]
    return t if words else ""


def _confirm_answer(st: dict[str, Any], step: dict[str, Any], text: str) -> dict[str, Any]:
    conf = dict(st.get("confirm") or {})
    if is_yes(text):
        return _commit(st, step, dict(conf.get("commit") or {}))
    # A CORRECTION IS THE NEW ANSWER (the User 2026-09-14 16:50: "So <A2> and <B> — is that right?"
    # … "<A>, <B>." … "No, it's <A> and <B>. Check the log." — she read the same wrong
    # names back until she paused): at a roster readback, names in the reply are parsed again.
    if str(step.get("id") or "") == "regulars_roster" and correction_text(text):
        st.pop("confirm", None)
        st.setdefault("notes", []).append("regulars_roster: corrected at the readback")
        return _roster_answer(st, correction_text(text), "")
    if is_no(text):
        st.pop("confirm", None)
        return _lila_live(st, step, text, _goal_for(step, st), reason="the User said the readback was wrong: " + str(conf.get("say") or ""))
    return _lila_live(st, step, text, "a yes or no to: " + str(conf.get("say") or ""), reason="unclear answer to the readback", keep_confirm=True)


def _frozen_answer(st: dict[str, Any], step: dict[str, Any], text: str) -> dict[str, Any]:
    if _RESTART_V.search(text or ""):
        return start(force=True)
    if _RESUME_V.search(text or "") or is_yes(text):
        st.pop("frozen", None)
        st.setdefault("notes", []).append("resumed from the pause (voice)")
        return _advance(st)
    if _LATER_V.search(text or "") or is_no(text):
        st.setdefault("notes", []).append("left for later from the pause (voice)")
        return _finish(st, "aborted")
    return _lila_live(st, step, text, _goal_for(step, st), reason="unclear at the pause")


def _register_someone_answer(st: dict[str, Any], step: dict[str, Any], text: str) -> dict[str, Any]:
    who = str(st.get("enroll_who") or "")
    if st.get("enroll_go"):
        return snapshot(st)
    if is_yes(text) or re.search(r"\bregister\b", text or "", re.I):
        st["enroll_go"] = who            # the wall starts the register chapter
        _bump_turn(st)
        save_state(st)
        return snapshot(st)
    if is_no(text) or is_skip(text):
        st["answers"][step["id"]] = {"skipped": True, "via": "voice"}
        q = list(st.get("enroll_queue") or [])[1:]
        st["enroll_queue"] = q
        if q:
            st["enroll_who"] = str(q[0].get("display") or q[0].get("id") or "")
            _bump_turn(st)
            save_state(st)
            return snapshot(st)
        return _advance(st)
    return _lila_live(st, step, text, _goal_for(step, st), reason="no yes or later")


def _silence(st: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    if str(step.get("kind") or "") == "dialogue":
        return snapshot(st)
    key = "%s:%s:%s" % (step.get("id"), int(st.get("beat") or 0), int(st.get("turn") or 0))
    n = int((st.get("silence") or {}).get(key) or 0) + 1
    st["silence"] = {key: n}
    # the welcome asks a yes/no: one quiet window and Lila steps in (the User 2026-09-14: dead air)
    conf = st.get("confirm") if isinstance(st.get("confirm"), dict) and st["confirm"].get("sid") == step.get("id") else None
    quick = step.get("id") == "welcome" or conf is not None     # a yes/no: one quiet window (the User 2026-09-14)
    # any other question: two (the User 2026-09-14 05:10 — 23 s of noisy windows at the speakers
    # question before Lila spoke, then a cancel two seconds before "Speakers." was heard)
    if n >= (1 if quick else min(SILENCE_NUDGE, 2)):
        if conf is not None:
            return _lila_live(st, step, "", "a yes or no to: " + str(conf.get("say") or ""), reason="silence after the readback", keep_confirm=True)
        return _lila_live(st, step, "", _goal_for(step, st), reason="silence")
    save_state(st)
    return snapshot(st)


def _host_lines(st: dict[str, Any]) -> list[str]:
    sid = str(st.get("step_id") or "")
    keys = host_key_for(sid, st)
    ks = list(keys) if isinstance(keys, list) else ([keys] if keys else [])
    if sid == "welcome":
        ks.append(HOST_THANKS_CRISSA_KEY)
    out = []
    try:
        inv = json.loads((POSES_DIR / "inventory.json").read_text(encoding="utf-8"))
        for k in ks:
            line = str((((inv.get("clips") or {}).get(str(k)) or {}).get("host") or {}).get("line") or "")
            if line:
                out.append(line)
    except Exception:
        pass
    for k in ("confirm", "live", "frozen"):
        d = st.get(k)
        if isinstance(d, dict) and d.get("say"):
            out.append(str(d["say"]))
    # her last few live lines too: a readback replaces the nudge in `live`, and the ear still
    # heard the nudge's tail (02:17:28 "What name would you like me to use?")
    out.extend(str(x) for x in (st.get("said") or [])[-4:])
    if WELCOME_ASK:
        out.append(WELCOME_ASK)
    return out


def _remember_said(st: dict[str, Any], say: str) -> None:
    if say:
        st["said"] = (list(st.get("said") or []) + [str(say)])[-4:]


def strip_echo(text: str, st: dict[str, Any]) -> str:
    """Drop the sentences of a heard turn that are mostly her own recent lines (the User 2026-09-14: the ear
    heard "So, <A3> and <B>, is that right? No, it's <A> and <B>." as one turn)."""
    host = [set(re.findall(r"[a-z']+", line.lower())) for line in _host_lines(st)]
    keep = []
    for sent in re.split(r"(?<=[.?!])\s+", str(text or "").strip()):
        words = re.findall(r"[a-z']+", sent.lower())
        if len(words) >= 3 and any(hw and sum(1 for w in words if w in hw) >= 0.7 * len(words) for hw in host):
            continue
        keep.append(sent)
    return " ".join(k for k in keep if k).strip()


def _salvage_echo(text: str, st: dict[str, Any]) -> str:
    """A turn the echo filter dropped as her voice: what follows her own sentences, when it opened with
    them ('' when nothing of hers was cut, so a garbled echo like "Shut again." stays dropped)."""
    rest = strip_echo(text, st)
    return rest if rest and rest != str(text or "").strip() else ""


def _echo_of_host(text: str, st: dict[str, Any]) -> bool:
    """Her own line heard back through the ear is not the User's answer."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    if len(words) < 3:
        return False
    for line in _host_lines(st):
        lw = set(re.findall(r"[a-z']+", line.lower()))
        if lw and sum(1 for w in words if w in lw) >= 0.8 * len(words):
            return True
    return False


def _live_line(st: dict[str, Any], step: dict[str, Any], heard: str, goal: str, reason: str) -> str | None:
    """One short spoken sentence from her brain that gets the answer; None when away."""
    if not _live_side_effects():
        return None
    url, model = _brain_endpoint()
    if not url:
        return None
    roster = st.get("roster") or {}
    system = (LILA_PERSONA
              + "\n\nRight now you are on this part of the setup: " + str(step.get("title") or step.get("id") or "")
              + ". You need: " + goal + "."
              + (" The User's name: " + str(roster.get("user")) + "." if roster.get("user") else "")
              + '\nReply with ONE JSON object only: {"say": "<one short spoken sentence that gets that answer — rephrase kindly, no lists, no menu>"}')
    user = ("What the User just said: " + json.dumps(heard)) if heard else "The User has not answered yet."
    if reason:
        user += " (why you are stepping in: " + reason + ")"
    body = {"model": model, "temperature": 0.5, "max_tokens": 90,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if model == "crew":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    out = _http_json(url, body, timeout=DIALOGUE_BRAIN_S)
    try:
        text = str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        i, j = text.find("{"), text.rfind("}")
        say = str(json.loads(text[i:j + 1]).get("say") or "").strip() if i >= 0 and j > i else ""
    except Exception:
        say = ""
    return say[:300] or None


def _lila_live(st: dict[str, Any], step: dict[str, Any], heard: str, goal: str, reason: str = "",
               keep_confirm: bool = False) -> dict[str, Any]:
    sid = str(step.get("id") or "")
    key = "frozen" if st.get("frozen") else "%s:%s" % (sid, int(st.get("beat") or 0))
    counts = st.setdefault("live_n", {})
    n = int(counts.get(key) or 0) + 1
    counts[key] = n
    if n > LIVE_MAX:
        return _live_give_up(st, step)
    say = _live_line(st, step, heard, goal, reason)
    if not say:
        say = ("Sorry, I didn't quite catch that. " if heard else "Take your time. ") + "Could you tell me " + goal + "?"
    if not keep_confirm:
        st.pop("confirm", None)
    st["live"] = {"sid": sid, "beat": int(st.get("beat") or 0), "say": say, "n": n, "at": time.time()}
    _remember_said(st, say)
    st.setdefault("notes", []).append(("%s: Lila live (%s)" % (sid, reason or "unparsed"))[:140])
    _bump_turn(st)
    save_state(st)
    return snapshot(st)


def _live_give_up(st: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    sid = str(step.get("id") or "")
    if st.get("frozen"):
        st.setdefault("notes", []).append("the pause went unanswered: left for later")
        return _finish(st, "aborted")
    if step.get("optional") or sid in ("present_regulars_enroll", "media_ghost_mask", "seat_map", "training_window", "news_topics"):
        st["answers"][sid] = {"skipped": True, "via": "lila-live"}
        st.setdefault("notes", []).append(sid + ": skipped after Lila live")
        return _advance(st)
    st.pop("live", None)
    st.pop("confirm", None)
    st["frozen"] = {"step": sid, "say": "Let's pause the setup here. Say carry on, start over, or later.", "at": _now()}
    st.setdefault("notes", []).append(sid + ": paused after Lila live")
    _bump_turn(st)
    save_state(st)
    return snapshot(st)


def _auto_defer_door(st: dict[str, Any]) -> None:
    """The door step asks nothing: her line, then the mask is left for the Control Panel."""
    sid = "media_ghost_mask"
    st["answers"][sid] = {"deferred": True}
    st.setdefault("notes", []).append("printed-face mask later from Control Panel")
    save_state(st)
    _auto_advance_after_line(sid, int(st.get("beat") or 0))
