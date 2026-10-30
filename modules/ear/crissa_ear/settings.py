"""The ear's settings: the house's twenty service drop-ins as one table of keys (2026-10-09).

The house tuned its ear with `Environment=` lines in systemd drop-ins. Each of those lines is a key
here, with the house's live value as the default where the value is not a device, a person or a path
of that one machine. The package's modules keep reading the environment as they always did; `apply()`
seeds it from this table (`os.environ.setdefault`, so a variable a service unit already pins wins),
and the scripts call it before they import the rest of the package.

Layers, later wins: DEFAULTS -> `<crissa home>/ear.json` ("ear" section) -> the environment.

    engine            which ASR path: live-ear (an HTTP /stt service), whisper, dual
    asr_url           the /stt service; default = this install's ASR from the shared port table
    asr_engine        the name written on turns for the live engine
    mic               the microphone: "auto" = the sound server's default input (2026-10-10), else the first input device that is not an echo-cancel node
                      (the house named its own array here: a PulseAudio source name such as
                      alsa_input.usb-<vendor>_<model>-00.analog-stereo; any substring of it works)
    mic_raw           the raw array behind an echo-cancel source ("" = none)
    mic_close         a near-field desk microphone the liveness check prefers when it is hot ("" = none;
                      the house named a desk microphone here)
    mic_mode          host (this machine's device) | page (the Wall page posts frames to /v1/mic)
    ear_open_frames   speech frames before the ear opens (house: 2, "open immediately")
    ear_preroll_frames  frames kept before the opening (house: 4 = 1.2 s, a missed first word still packs)
    vad               ten | silero | webrtc (house: ten)
    silero_thr        Silero speech probability (house 0.35: a quiet first "Hey" still counts)
    hangover_sec      silence that closes a turn (house 2.0: thinking pauses stay in one utterance)
    max_utter_sec     cap on one utterance (house 30)
    wake_hangover_sec silence frames while paused so the wake word is not clipped (house 0.6)
    voice_encoder     wespeaker | ecapa (house: wespeaker)
    match_sim         voice-ID floor (house 0.50: measured, 0.35 let a TV pass as the owner)
    match_sim_user    the owner's own floor (house 0.40: short room phrases still name him)
    vs_sole_max_sole_sim  (house 0.20)
    wake_shadow       score turns with a wake-word head, log only (house 1 with its own trained head;
                      0 here: no head ships)
    wake_head         the head file (<home>/ear/wake/head.npz)
    wake_shadow_thr   0.5
    identity_root     who the house knows: the identity store Init writes, <home>/identity (people/<id>/voice/*.json;
                      CRISSA_IDENTITY wins, as in the id module)
    mouths_root       her own voice prints: <home>/ear/mouths
    scratch_root      unnamed speakers: <home>/ear/scratch
    hf_home           model cache for whisper: <home>/hf
    ws_shadow_url     a second voice scorer, log only ("" = none; the house ran one beside the ear)
    user_id           the owner's id in identity records ("" = the person Init made the User: person.json role
                      "user" in the identity store, else "user"; a value pins it)
    turn_log          the turn log (<home>/ear/turns.jsonl)
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
    """The shared port table's rule (lib/crissa_ports), read inline: this module installs on its own."""
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


# key -> (environment variable the package reads, default)
TABLE = {
    "engine": ("RAIL_STT_ENGINE", "live-ear"),
    "asr_url": ("LIVE_EAR_URL", None),                       # None = the port table at apply()
    "asr_engine": ("RAIL_STT_ASR_ENGINE", "ark-asr"),
    "mic": ("RAIL_STT_MIC", "auto"),
    "mic_raw": ("RAIL_STT_MIC_RAW", ""),
    "mic_close": ("RAIL_STT_MIC_CLOSE", ""),
    "mic_mode": ("RAIL_STT_MIC_MODE", "host"),
    # echo cancellation the house's way (round 14, crissa_ear/aec.py): auto | on | off
    "aec": ("CRISSA_EAR_AEC", "auto"),
    "ear_open_frames": ("RAIL_STT_EAR_OPEN_FRAMES", "2"),
    "ear_preroll_frames": ("RAIL_STT_EAR_PREROLL_FRAMES", "4"),
    "vad": ("RAIL_STT_VAD", "ten"),
    "silero_thr": ("RAIL_STT_SILERO_THR", "0.35"),
    "hangover_sec": ("RAIL_STT_HANGOVER_SEC", "2.0"),
    "max_utter_sec": ("RAIL_STT_MAX_UTTER_SEC", "30"),
    "wake_hangover_sec": ("RAIL_STT_WAKE_HANGOVER_SEC", "0.6"),
    "voice_encoder": ("HOUSE_VOICE_ENCODER", "wespeaker"),
    "match_sim": ("RAIL_VOICE_MATCH_SIM", "0.50"),
    "match_sim_user": ("RAIL_VOICE_MATCH_SIM_USER", "0.40"),
    # live prints when the ear is certain it is the User (the house's guards: voice_bank._decide_promote_trust): OFF
    # again in a pack (the User 2026-10-16: "the only thing collecting embedding does is nearly immediately corrupt the
    # embeddings. It is not a good idea" - the house has kept it off since 08-03 for that reason); "1" turns it on
    "voice_bank_promote": ("RAIL_VOICE_BANK_PROMOTE", "0"),
    "vs_sole_max_sole_sim": ("RAIL_VOICE_VS_SOLE_MAX_SOLE_SIM", "0.20"),
    "wake_shadow": ("RAIL_WAKE_SHADOW", "0"),
    "wake_head": ("RAIL_WAKE_HEAD", str(EAR / "wake" / "head.npz")),
    "wake_models": ("RAIL_WAKE_MODELS", str(EAR / "wake")),
    "wake_shadow_thr": ("RAIL_WAKE_SHADOW_THR", "0.5"),
    "identity_root": ("HOUSE_ID_ROOT", str(Path(os.environ.get("CRISSA_IDENTITY") or (HOME / "identity")))),
    "mouths_root": ("HOUSE_MOUTHS_ROOT", str(EAR / "mouths")),
    "scratch_root": ("RAIL_SCRATCH_ROOT", str(EAR / "scratch")),
    "hf_home": ("HF_HOME", str(HOME / "hf")),
    "ws_shadow_url": ("RAIL_WS_SHADOW_URL", ""),
    "user_id": ("HOUSE_USER_ID", ""),
    "turn_log": ("RAIL_STT_TURN_JSONL_PATH", str(EAR / "turns.jsonl")),
    "voice_lock": ("HOUSE_VOICE_LOCK", str(HOME / "house-voice.json")),
    "tts_gate": ("HOUSE_TTS_GATE", str(HOME / "tts-gate.json")),
    "presence_url": ("RAIL_PRESENCE_URL", None),
    "work_dir": ("RAIL_STT_WORK", str(EAR / "work")),
}
FILE = HOME / "ear.json"
SECTION = "ear"


def defaults(env=None) -> dict:
    out = {k: v[1] for k, v in TABLE.items()}
    out["asr_url"] = url("asr", env)
    out["presence_url"] = url("presence", env) + "/v1/presence"
    return out


def load(path: Path | None = None, env=None) -> dict:
    """DEFAULTS under the file's section; unknown keys are kept (a later version may read them)."""
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
    """Seed the environment the package reads. A variable already set wins (a unit pins it)."""
    e = os.environ if env is None else env
    cfg = load(path, e)
    for key, (var, _d) in TABLE.items():
        v = cfg.get(key)
        if v is None or v == "":
            continue
        e.setdefault(var, str(v))
    return cfg


def view(env=None) -> dict:
    """What is in force, by key (for /health and the panel)."""
    e = os.environ if env is None else env
    return {k: e.get(var, d) for k, (var, d) in TABLE.items()}
