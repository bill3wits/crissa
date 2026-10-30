"""Hard-rail house control phrases — code match after Crissa wake (the User 2026-08-02).

Vocab (aside from name tag):
  hybrid-mode | voice-only | voice-on | voice-off
  system-off | system-reboot
Confirm path: confirm | cancel
After confirm: 10s countdown; cancel aborts.

Does NOT call Crissa LLM for these. Speaks via callback. Never invents presence.
"""

from __future__ import annotations

from .settings import url as _url  # this install's addresses (the shared port table, 2026-10-10)

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .home import HOME, pack_script
from typing import Any, Callable

LOG = Path(
    os.environ.get(
        "HOUSE_CMD_LOG",
        str(HOME / "logs" / "house-commands.jsonl"),
    )
)
PANEL_VOICE = os.environ.get(
    "HOUSE_CMD_VOICE", _url("stt", path="/panel/voice")
)
PANEL_SURFACE = os.environ.get(
    "HOUSE_CMD_SURFACE", _url("stt", path="/panel/surface-mode")
)
COUNTDOWN_SEC = float(os.environ.get("HOUSE_CMD_COUNTDOWN_SEC", "10"))
CONFIRM_WINDOW_SEC = float(os.environ.get("HOUSE_CMD_CONFIRM_WINDOW_SEC", "12"))
SYSTEM_SCRIPT = Path(
    os.environ.get(
        "HOUSE_SYSTEM_SCRIPT",
        str(pack_script("house-system-action.sh")),
    )
)

SpeakFn = Callable[[str], None]
LogFn = Callable[[dict[str, Any]], None]


def _log(row: dict[str, Any]) -> None:
    row = {**row, "ts": time.time(), "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


# House law (SCOPE-20260803): guests get "no house commands, and NEVER an
# explanation of why". The old wording ("Only an authenticated user can do
# that") told a stranger there is an auth system, that they lack it, and that
# somebody else has it. Polite and uninformative is the whole point.
_REFUSAL = "Sorry, I can\'t help with that one."


def _refusal() -> str:
    """The refusal's words: On's, or (Security ID-only, round 17) that her face check failed (id_gate)."""
    try:
        from .id_gate import refuse_line
        return refuse_line(_REFUSAL)
    except Exception:  # noqa: BLE001
        return _REFUSAL


def _maybe_intrusion(text: str, cmd: str | None, meta: dict, presence: dict | None) -> dict:
    """File an intrusion attempt for an UNKNOWN voice asking for something gated.

    Known regulars and today's visitors are refused without a report. Best
    effort in every case: evidence capture must never break the refusal.
    Outside Security On (round 17, id_gate) no turn is anyone's but the person at this computer's, so a refusal for a
    face not at the camera is no intrusion: nothing is filed.
    """
    try:
        from .id_gate import gating
        if not gating():
            return {"skipped": "security_not_on"}
    except Exception:  # noqa: BLE001
        pass
    try:
        from .intrusion import maybe_report

        return maybe_report(text=text, cmd=cmd, meta=meta, presence=presence)
    except Exception as e:
        return {"filed": False, "error": f"{type(e).__name__}: {e}"[:120]}


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"[^\w\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # ASR: hybrid mode / hybridmode
    s = s.replace("hybrid mode", "hybrid-mode")
    s = s.replace("voice only", "voice-only")
    s = s.replace("voice on", "voice-on")
    s = s.replace("voice off", "voice-off")
    s = s.replace("system off", "system-off")
    s = s.replace("system reboot", "system-reboot")
    s = s.replace("system on", "system-on")
    s = s.replace("power down", "system-off")
    s = s.replace("power off", "system-off")
    s = s.replace("shut down", "system-off")
    s = s.replace("shutdown", "system-off")
    s = s.replace("re boot", "system-reboot")
    s = s.replace("reboot", "system-reboot")
    return s


# phrase → command id
_CMD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("hybrid-mode", re.compile(r"\bhybrid[\s\-]?mode\b")),
    ("voice-only", re.compile(r"\bvoice[\s\-]?only\b")),
    ("voice-on", re.compile(r"\bvoice[\s\-]?on\b")),
    ("voice-off", re.compile(r"\bvoice[\s\-]?off\b")),
    ("system-off", re.compile(r"\b(system[\s\-]?off|power[\s\-]?down|power[\s\-]?off|shut[\s\-]?down)\b")),
    ("system-reboot", re.compile(r"\b(system[\s\-]?reboot|re[\s\-]?boot)\b")),
    ("system-on", re.compile(r"\bsystem[\s\-]?on\b")),
    # Strong leave — clears User sticky; not empty-FOV leave (AUTH-first).
    (
        "user-leave",
        re.compile(
            # _norm strips apostrophes: "I'm" → "i m"
            r"\b("
            r"i\s*m (?:heading |going )?out|"
            r"i am (?:heading |going )?out|"
            r"i\s*m leav(?:e|ing)|"
            r"i am leav(?:e|ing)|"
            r"log me out|"
            r"sign me out|"
            r"clear my auth|"
            r"i left"
            r")\b"
        ),
    ),
]

_CONFIRM = re.compile(r"\b(confirm|yes\s+confirm|i\s+confirm|do\s+it|proceed)\b")
_CANCEL = re.compile(r"\b(cancel|abort|stop|nevermind|never\s+mind|no)\b")


def match_control(text: str) -> dict[str, Any] | None:
    """If text is primarily a control phrase, return {cmd, raw}. Else None."""
    n = _norm(text)
    if not n or len(n) > 80:
        return None
    # Prefer cancel/confirm when short
    if _CANCEL.search(n) and len(n.split()) <= 4:
        return {"cmd": "cancel", "raw": text}
    if _CONFIRM.search(n) and len(n.split()) <= 5:
        return {"cmd": "confirm", "raw": text}
    hits = []
    for cmd, pat in _CMD_PATTERNS:
        if pat.search(n):
            hits.append(cmd)
    if not hits:
        return None
    # voice-on before voice-only substring issues — patterns are distinct
    if "voice-only" in hits and "voice-on" in hits:
        # "voice only" shouldn't also hit voice-on
        hits = [h for h in hits if h != "voice-on"]
    if len(hits) > 1:
        # pick longest cmd name as more specific
        hits.sort(key=len, reverse=True)
    return {"cmd": hits[0], "raw": text}


def _post_json(url: str, body: dict, timeout: float = 4.0) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _shell_cdp(expr: str) -> None:
    """Best-effort Crissa surface mode via crissa-display."""
    script = pack_script("crissa-display")
    if not script.is_file():
        return
    try:
        if "hybrid" in expr:
            subprocess.Popen(
                ["bash", str(script), "hybrid"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif "voice-only" in expr:
            subprocess.Popen(
                ["bash", str(script), "voice-only"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception:
        pass


@dataclass
class CommandState:
    phase: str = "idle"  # idle | await_confirm | countdown
    action: str | None = None  # system-off | system-reboot | system-on
    deadline: float = 0.0
    requester: str | None = None
    timer: threading.Timer | None = field(default=None, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock)


_STATE = CommandState()


def pending_phase() -> str:
    with _STATE.lock:
        if _STATE.phase != "idle" and time.time() > _STATE.deadline + 1:
            # expire await without cancel speech
            if _STATE.phase == "await_confirm":
                _STATE.phase = "idle"
                _STATE.action = None
        return _STATE.phase


def _cancel_timer() -> None:
    t = _STATE.timer
    _STATE.timer = None
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass


def _is_authed(meta: dict, presence: dict | None) -> bool:
    """Control-plane inject requires A+B: AUTHed User + this-turn User voice.

    session_held alone never authorizes. In user_auth_only mode, failures
    fail **closed** (False) — never fall through to legacy certain/authed_ids.
    """
    from .user_mode import evaluate_turn, is_user_auth_only

    try:                                         # round 17: Security Off yes, ID-only his face now (id_gate)
        from .id_gate import act_authed
        opened = act_authed(presence)
    except Exception:  # noqa: BLE001
        opened = None
    if opened is not None:
        return bool(opened)
    try:
        user_auth_only = bool(is_user_auth_only())
    except Exception:
        # Unknown mode → strict (no guest inject on sticky)
        user_auth_only = True

    if user_auth_only:
        try:
            stamp = meta.get("stamp") if isinstance(meta.get("stamp"), dict) else {}
            ev = evaluate_turn(
                presence if isinstance(presence, dict) else {},
                stamp,
                person_id=(meta or {}).get("person_id"),
            )
        except Exception:
            return False  # fail closed — never legacy grant after eval error
        if ev.get("force_reprove"):
            return False
        if ev.get("tools_ok") or ev.get("tier2_ok"):
            return True
        if ev.get("auth") and ev.get("user_voice"):
            return True
        return False

    # Legacy house mode only (not user_auth_only)
    pid = (meta.get("person_id") or "").strip().lower()
    if not pid or (pid.startswith("u") and pid[1:].isdigit()):
        return False
    stamp = meta.get("stamp") if isinstance(meta.get("stamp"), dict) else {}
    if stamp.get("certain") and pid in ("user", "user"):
        return True
    presence = presence or {}
    auth = presence.get("auth") if isinstance(presence.get("auth"), dict) else {}
    authed = {str(x).lower() for x in (auth.get("authed_ids") or [])}
    if pid in authed:
        return True
    for p in presence.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").lower()
        if name == pid and p.get("certain") and pid in authed:
            return True
    return False


def _run_system(action: str) -> None:
    _log({"event": "system_execute", "action": action})
    if not SYSTEM_SCRIPT.is_file():
        _log({"event": "system_missing_script", "path": str(SYSTEM_SCRIPT)})
        return
    try:
        subprocess.Popen(
            ["bash", str(SYSTEM_SCRIPT), action],
            stdout=open(HOME / "logs" / "house-system-action.log", "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        _log({"event": "system_spawn_error", "error": str(e)[:160]})


def _start_countdown(action: str, speak: SpeakFn) -> None:
    with _STATE.lock:
        _cancel_timer()
        _STATE.phase = "countdown"
        _STATE.action = action
        _STATE.deadline = time.time() + COUNTDOWN_SEC

        def _fire() -> None:
            with _STATE.lock:
                if _STATE.phase != "countdown" or _STATE.action != action:
                    return
                act = _STATE.action
                _STATE.phase = "idle"
                _STATE.action = None
                _STATE.timer = None
            speak(f"Executing {act.replace('-', ' ')} now.")
            _run_system(act or "system-off")

        _STATE.timer = threading.Timer(COUNTDOWN_SEC, _fire)
        _STATE.timer.daemon = True
        _STATE.timer.start()
    secs = int(COUNTDOWN_SEC)
    speak(
        f"Confirmed. {secs} seconds to cancel. Say cancel to stop."
    )


def handle_control(
    text: str,
    *,
    meta: dict | None = None,
    presence: dict | None = None,
    speak: SpeakFn | None = None,
    require_wake: bool = True,
) -> dict[str, Any]:
    """Process control phrase or confirm/cancel.

    Returns {handled: bool, ...}. If handled, caller must NOT send text to LLM.
    """
    meta = meta or {}
    speak = speak or (lambda t: None)
    m = match_control(text)
    if not m:
        return {"handled": False}

    cmd = m["cmd"]
    _log({"event": "match", "cmd": cmd, "text": (text or "")[:120], "pid": meta.get("person_id")})

    # --- confirm / cancel while pending ---
    with _STATE.lock:
        phase = _STATE.phase
        action = _STATE.action

    if cmd == "cancel":
        if phase in ("await_confirm", "countdown"):
            with _STATE.lock:
                _cancel_timer()
                _STATE.phase = "idle"
                _STATE.action = None
            speak("Canceled. No system change.")
            _log({"event": "canceled", "was_phase": phase, "action": action})
            return {"handled": True, "cmd": "cancel", "phase": "idle"}
        return {"handled": False}  # cancel with nothing pending → LLM

    if cmd == "confirm":
        # Non-User must never advance await_confirm → countdown (inject).
        # Cancel stays ungated so a wrong speaker can abort, not complete.
        if phase in ("await_confirm", "countdown") and action:
            if not _is_authed(meta, presence):
                speak(_refusal())
                _log(
                    {
                        "event": "confirm_denied_auth",
                        "phase": phase,
                        "action": action,
                        "pid": (meta or {}).get("person_id"),
                        "intrusion": _maybe_intrusion(text, "confirm", meta, presence),
                    }
                )
                return {
                    "handled": True,
                    "cmd": "confirm",
                    "denied": "auth",
                    "phase": phase,
                    "action": action,
                }
        if phase == "await_confirm" and action:
            if time.time() > _STATE.deadline:
                with _STATE.lock:
                    _STATE.phase = "idle"
                    _STATE.action = None
                speak("Confirm window expired. Say the system command again.")
                return {"handled": True, "cmd": "confirm", "expired": True}
            _start_countdown(action, speak)
            return {"handled": True, "cmd": "confirm", "phase": "countdown", "action": action}
        if phase == "countdown":
            speak(f"Already counting down for {action}. Say cancel to stop.")
            return {"handled": True, "cmd": "confirm", "phase": "countdown"}
        return {"handled": False}

    # --- surface / voice — same A+B gate as system (no non-User inject) ---
    if cmd in ("hybrid-mode", "voice-only", "voice-on", "voice-off"):
        if not _is_authed(meta, presence):
            speak(_refusal())
            _log(
                {
                    "event": "surface_denied_auth",
                    "cmd": cmd,
                    "pid": (meta or {}).get("person_id"),
                    "intrusion": _maybe_intrusion(text, cmd, meta, presence),
                }
            )
            return {"handled": True, "cmd": cmd, "denied": "auth"}

    if cmd == "hybrid-mode":
        try:
            _post_json(PANEL_SURFACE, {"mode": "hybrid"})
        except Exception as e:
            _log({"event": "surface_error", "error": str(e)[:120]})
        _shell_cdp("hybrid")
        speak("Hybrid mode.")
        return {"handled": True, "cmd": cmd}

    if cmd == "voice-only":
        try:
            r = _post_json(PANEL_SURFACE, {"mode": "voice-only"})
            if r.get("mode") != "voice-only":
                speak("Voice is off. Hybrid only until voice is on.")
                return {"handled": True, "cmd": cmd, "refused": "voice_off"}
        except Exception as e:
            _log({"event": "surface_error", "error": str(e)[:120]})
        _shell_cdp("voice-only")
        speak("Voice only mode.")
        return {"handled": True, "cmd": cmd}

    if cmd == "voice-on":
        try:
            _post_json(PANEL_VOICE, {"on": True, "on_by": "voice_cmd"})
        except Exception as e:
            _log({"event": "voice_error", "error": str(e)[:120]})
            speak("Could not turn voice on.")
            return {"handled": True, "cmd": cmd, "error": True}
        speak("Voice on.")
        return {"handled": True, "cmd": cmd}

    if cmd == "voice-off":
        try:
            _post_json(PANEL_VOICE, {"on": False, "on_by": "voice_cmd"})
            _post_json(PANEL_SURFACE, {"mode": "hybrid"})  # force hybrid
        except Exception as e:
            _log({"event": "voice_error", "error": str(e)[:120]})
        _shell_cdp("hybrid")
        speak("Voice off. Hybrid mode.")
        return {"handled": True, "cmd": cmd}

    # --- strong User leave (clears sticky; not empty-FOV leave) ---
    if cmd == "user-leave":
        # Non-User must not de-AUTH User. Require this-turn User voice (or
        # person_id == sudo User with face sticky) — never guest inject leave.
        ublk = (
            (presence or {}).get("user")
            if isinstance((presence or {}).get("user"), dict)
            else {}
        )
        uid = str(
            ublk.get("user_id")
            or (presence or {}).get("user_id")
            or "user"
        ).strip().lower() or "user"
        pid = str((meta or {}).get("person_id") or "").strip().lower()
        speaker_is_user = bool(pid == uid or _is_authed(meta, presence))
        present = bool(
            ublk.get("auth")
            or ublk.get("user_present")
            or ublk.get("user_face_sticky")
            or (presence or {}).get("user_auth")
            or (presence or {}).get("user_face_sticky")
        )
        if not present or not speaker_is_user:
            speak("Only the house user can sign out that way.")
            return {"handled": True, "cmd": cmd, "denied": "auth"}
        try:
            base = os.environ.get(
                "EAR_BRIDGE_PRESENCE", _url("presence", path="/v1/presence")
            ).rstrip("/")
            if base.endswith("/v1/presence"):
                base = base[: -len("/v1/presence")]
            elif base.endswith("/presence"):
                base = base[: -len("/presence")]
            r = _post_json(
                f"{base}/v1/leave",
                {"name": uid, "source": "assert_leave"},
                timeout=5.0,
            )
            _log(
                {
                    "event": "user_leave",
                    "uid": uid,
                    "ok": True,
                    "hc": r.get("headcount") if isinstance(r, dict) else None,
                }
            )
        except Exception as e:
            _log({"event": "user_leave_error", "error": str(e)[:160]})
            speak("I could not clear your session. Try again.")
            return {"handled": True, "cmd": cmd, "error": True}
        speak("You're signed out. Have a good one.")
        return {"handled": True, "cmd": cmd, "left": uid}

    # --- system * ---
    if cmd in ("system-off", "system-reboot", "system-on"):
        if not _is_authed(meta, presence):
            speak(_refusal())
            _log({"event": "system_denied_auth", "cmd": cmd, "pid": meta.get("person_id"),
                  "intrusion": _maybe_intrusion(text, cmd, meta, presence)})
            return {"handled": True, "cmd": cmd, "denied": "auth"}
        with _STATE.lock:
            _cancel_timer()
            _STATE.phase = "await_confirm"
            _STATE.action = cmd
            _STATE.requester = meta.get("person_id")
            _STATE.deadline = time.time() + CONFIRM_WINDOW_SEC
        nice = cmd.replace("-", " ")
        speak(
            f"Confirm {nice}? Say confirm to proceed, or cancel. "
            f"You have {int(CONFIRM_WINDOW_SEC)} seconds."
        )
        _log({"event": "await_confirm", "action": cmd, "pid": meta.get("person_id")})
        return {"handled": True, "cmd": cmd, "phase": "await_confirm"}

    return {"handled": False}


def command_may_execute(ev: dict | None) -> bool:
    """Post-evaluate_turn gate for house command inject (ear_bridge).

    Face chase / sticky alone is not enough — this-turn User voice (tools_ok)
    required. Used after ensure_user_auth so wrong-voice stamps never execute.
    """
    if not isinstance(ev, dict):
        return False
    if ev.get("force_reprove"):
        return False
    if ev.get("tools_ok") or ev.get("tier2_ok"):
        return True
    return bool(ev.get("user_voice") and ev.get("auth"))


def try_handle_utterance(
    text: str,
    *,
    meta: dict | None = None,
    presence: dict | None = None,
    speak: SpeakFn | None = None,
) -> bool:
    """True if fully handled (do not LLM).

    Always runs handle_control's own _is_authed gates — never bypasses them.
    """
    r = handle_control(text, meta=meta, presence=presence, speak=speak)
    return bool(r.get("handled"))
