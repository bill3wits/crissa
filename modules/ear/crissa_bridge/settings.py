"""The ear bridge's settings: the house's ear-bridge drop-ins as one table of keys (2026-10-09).

Each `Environment=` line of the house's ear-bridge unit and its drop-ins is a key here, with the
house's live value as the default where it is not a device, a person, a path or an address of that one
machine. The modules keep reading the environment; `apply()` seeds it (`setdefault`: a pinned
variable wins) before the package is imported.

Layers, later wins: DEFAULTS -> `<crissa home>/ear.json` ("bridge" section) -> the environment.

    poll_s            how often the turns feed is read (house 0.2)
    listen_sec        the listening window after a wake (house 14)
    gather_sec        quiet that ends a gathered question (house 5.0: a whole thought, not fragments)
    gather_max        the longest gather (house 45)
    flush_fast        gather -> agent dispatch (house 0.3; the mouth start is speak_quiet_sec)
    tail_silence      0.35
    session_sec       idle seconds until the session pauses (20 in a pack since 2026-10-14, 45 before; house 180)
    speak_quiet_sec   quiet before her reply starts (house 2.0)
    max_tokens        overrides the talk dial reply.max_tokens (4096; round 15: no default seeded here)
    speak_chars       the longest spoken reply (house 16000)
    agent_timeout_s   0 = no deadline on the agent call (house 0)
    own_voice         drop | report: a turn that is her own voice coming back (house drop)
    oracle_face_wins  a fresh live face match of the owner decides the turn (house 1)
    thinking          write <home>/crissa-thinking.json while she thinks (house 1)
    tool_callouts     say one short phrase per tool call seen in the agent's stream (house 1; off by
                      itself, with a mark on /health, when the stream carries no tool events)
    callout_grace_s   0
    tool_event        the SSE event name (suffix) that marks a tool call in the agent's stream
    reply_mode        auto | voice | type
    user_id           the owner's id ("" = the person Init made the User, person.json role "user"; else "user")
    audio_sink        the output device house-speak plays on ("" = the system's; the house named an
                      echo-cancel sink here)
    speak_*           house-speak's dials, as the house ran them: tempo 1.0, follow_line 1, buffer 0,
                      read_bytes 4800, first_chunk_ms 15000, tail_starve_ms 800, align_lead_ms 50
    stt_url / presence_url / player_url   from the shared port table
    chalk_url         where a typed reply goes when the voice is down ("" = nowhere)
    camera_url        a camera service with /v1/security/freeze for incidents ("" = none)
    board_url         a board (krew) HTTP endpoint that takes a security card ("" = the incident is
                      written to <home>/ear/incidents only)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .home import HOME, EAR

# THE SHARED PORT TABLE (lib/crissa_ports.DEFAULTS and LEGACY), whole since 2026-10-10: the ear reached the camera,
# the feed and the enroll coach by the house's ports; tests/test_crissa_loopback_guard.py holds the copy equal
DEFAULT_PORTS = {"player": 8100, "asr": 8096, "stt": 8190, "align": 8195, "tts": 8189, "oracle": 8197, "turbo": 8186,
                 "multilingual": 8185, "audio8": 8187, "pocket": 8188, "presence": 8219, "camera": 8192,
                 "enroll": 8196, "panel": 8097, "feed": 8099, "bridge": 8194, "comfy": 8184, "demo": 8124, "chip": 8218, "face_shell": 8088, "browser": 8183, "phone": 8226}
LEGACY_PORTS = {"player": "HOUSE_CONDUCTOR_PORT", "align": "CRISSA_ALIGN_PORT", "tts": "CRISSA_TTS_PORT",
                "oracle": "CRISSA_ORACLE_PORT"}


def port(name: str, env=None) -> int:
    e = os.environ if env is None else env
    for k in ("CRISSA_PORT_" + name.upper(), LEGACY_PORTS.get(name)):
        v = str(e.get(k) or "").strip() if k else ""
        if v.isdigit() and 0 < int(v) < 65536:
            return int(v)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    return DEFAULT_PORTS[name] + off


def url(name: str, env=None, path: str = "") -> str:
    return "http://127.0.0.1:%d%s" % (port(name, env), path)


def camera(path: str = "", env=None) -> str:
    """The room camera: RAIL_CAMERA_URL / CRISSA_CAMERA_URL when named, else this install's grabber (2026-10-10)."""
    e = os.environ if env is None else env
    return (str(e.get("RAIL_CAMERA_URL") or e.get("CRISSA_CAMERA_URL") or "").strip().rstrip("/") or url("camera", e)) + path


def camera_off(env=None) -> bool:
    """CRISSA_NO_CAMERA=1 (the launcher's --no-camera): nothing here asks a camera (2026-10-10)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on")


def brain_base(env=None) -> str:
    """The engine's /v1 base for a health look (2026-10-10). The house probed its own engines by port (8000, 8005); a
    pack's engine is the Connect endpoint (<state home>/agent.json, CRISSA_AGENT_URL), looked at only when it is on
    this machine; '' = none, and the lane reads down."""
    import urllib.parse
    e = os.environ if env is None else env
    base = str(e.get("CRISSA_AGENT_URL") or "").strip()
    if not base:
        try:
            d = json.loads((HOME / "agent.json").read_text(encoding="utf-8"))
            base = str((d if isinstance(d, dict) else {}).get("url") or "").strip()
        except (OSError, ValueError):
            base = ""
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if not base or urllib.parse.urlsplit(base).hostname not in ("127.0.0.1", "localhost", "::1"):
        return ""
    return base


TABLE = {
    "poll_s": ("EAR_BRIDGE_POLL", "0.2"),
    "listen_sec": ("EAR_BRIDGE_LISTEN_SEC", "14"),
    "gather_sec": ("EAR_BRIDGE_GATHER_SEC", "5.0"),
    "gather_max": ("EAR_BRIDGE_GATHER_MAX", "45.0"),
    "flush_fast": ("EAR_BRIDGE_FLUSH_FAST", "0.3"),
    "tail_silence": ("EAR_BRIDGE_TAIL_SILENCE", "0.35"),
    # 20 s (the User 2026-10-14; 45 since 10-10, the house's 180): room for a follow-up after her answer (the clock
    # does not run while she speaks or a turn is in flight) without side talk to someone else reaching her for long;
    # only the session's own speaker, or speech naming her, extends it
    "session_sec": ("EAR_BRIDGE_SESSION_SEC", "20"),
    "speak_quiet_sec": ("EAR_BRIDGE_SPEAK_QUIET_SEC", "2.0"),
    # the reply budget lives on the talk dial (config/crissa-talk.json reply.max_tokens, round 15); set here (ear.json) or
    # in the environment it overrides the dial - so no default is seeded
    "max_tokens": ("EAR_BRIDGE_MAX_TOKENS", ""),
    "speak_chars": ("EAR_BRIDGE_SPEAK_CHARS", "16000"),
    "agent_timeout_s": ("EAR_BRIDGE_CRISSA_TIMEOUT", "0"),
    "own_voice": ("EAR_BRIDGE_OWN_VOICE", "drop"),
    "oracle_face_wins": ("HOUSE_ORACLE_FACE_WINS", "1"),
    "thinking": ("EAR_BRIDGE_THINKING", "1"),
    "tool_callouts": ("EAR_BRIDGE_TOOL_CALLOUTS", "1"),
    "callout_grace_s": ("EAR_BRIDGE_CALLOUT_GRACE_S", "0"),
    "tool_event": ("EAR_BRIDGE_TOOL_EVENT", "tool.progress"),
    "reply_mode": ("EAR_BRIDGE_REPLY_MODE", "auto"),
    "user_id": ("HOUSE_USER_ID", ""),
    "audio_sink": ("VOICE_SINK", ""),
    "speak_tempo": ("HOUSE_SPEAK_TEMPO", "1.0"),
    "speak_follow_line": ("HOUSE_SPEAK_FOLLOW_LINE", "1"),
    "speak_buffer": ("HOUSE_SPEAK_BUFFER", "0"),
    "speak_read_bytes": ("HOUSE_SPEAK_READ_BYTES", "4800"),
    "speak_first_chunk_ms": ("HOUSE_SPEAK_FIRST_CHUNK_MS", "15000"),
    "speak_tail_starve_ms": ("HOUSE_SPEAK_TAIL_STARVE_MS", "800"),
    "align_lead_ms": ("HOUSE_ALIGN_LEAD_MS", "50"),
    "stt_url": ("RAIL_STT_URL", None),
    "presence_url": ("EAR_BRIDGE_PRESENCE", None),
    "player_url": ("CRISSA_PLAYER", None),
    "bridge_port": ("EAR_BRIDGE_ABORT_PORT", None),
    "chalk_url": ("EAR_BRIDGE_FACE_CHALK", ""),
    "camera_url": ("RAIL_CAMERA_URL", ""),
    "board_url": ("EAR_BRIDGE_BOARD_URL", ""),
    "thinking_file": ("HOUSE_THINKING_FILE", str(HOME / "crissa-thinking.json")),
    "tts_gate": ("HOUSE_TTS_GATE", str(HOME / "tts-gate.json")),
    "log": ("EAR_BRIDGE_LOG", str(HOME / "logs" / "ear-bridge.jsonl")),
    "identity_root": ("HOUSE_ID_ROOT", str(Path(os.environ.get("CRISSA_IDENTITY") or (HOME / "identity")))),
    "incidents": ("RAIL_SECURITY_INCIDENTS", str(EAR / "incidents")),
    "mode_file": ("HOUSE_MODE_PATH", str(HOME / "house-mode.json")),
    "corrections_file": ("EAR_BRIDGE_CORRECTIONS_FILE", str(Path(__file__).resolve().parent.parent / "heard-corrections.json")),
}
FILE = HOME / "ear.json"
SECTION = "bridge"


def defaults(env=None) -> dict:
    out = {k: v[1] for k, v in TABLE.items()}
    out["stt_url"] = url("stt", env)
    out["presence_url"] = url("presence", env) + "/v1/presence"
    out["player_url"] = url("player", env)
    out["bridge_port"] = str(port("bridge", env))
    return out


def load(path: Path | None = None, env=None) -> dict:
    out = defaults(env)
    try:
        d = json.loads((path or FILE).read_text(encoding="utf-8"))
        sec = d.get(SECTION) if isinstance(d, dict) else None
        if isinstance(sec, dict):
            for k, v in sec.items():
                if v is not None:
                    out[k] = v
    except (OSError, ValueError):
        pass
    return out


def apply(env=None, path: Path | None = None) -> dict:
    """Seed the environment the package reads; a variable already set wins."""
    e = os.environ if env is None else env
    cfg = load(path, e)
    for key, (var, _d) in TABLE.items():
        v = cfg.get(key)
        if v is None or v == "":
            continue
        e.setdefault(var, str(v))
    stt = str(cfg["stt_url"]).rstrip("/")
    e.setdefault("EAR_BRIDGE_TURNS", stt + "/v1/turns?n=30&via=ear,stt")
    e.setdefault("EAR_BRIDGE_STT_HEALTH", stt + "/health")
    e.setdefault("EAR_BRIDGE_VOICE_LOCK", stt + "/v1/house/voice")
    e.setdefault("HOUSE_CMD_VOICE", stt + "/panel/voice")
    e.setdefault("HOUSE_CMD_SURFACE", stt + "/panel/surface-mode")
    e.setdefault("RAIL_VOICE_STT", stt + "/stt")
    return cfg


def view(env=None) -> dict:
    e = os.environ if env is None else env
    return {k: e.get(var, d) for k, (var, d) in TABLE.items()}
