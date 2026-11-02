"""One table of the loopback ports a Crissa install uses (2026-10-05).

Every piece that reaches another reads its port here, so a launcher that moves them (an
offset, to run beside another install on the same machine) moves all of them at once:

    player   the player (scripts/house-conductor.py): the Wall page, /frame.mjpg, /v1/*
    asr      speech to text on the CPU (modules/voice/scripts/crissa-asr.py, the /stt contract)
    stt      the microphone's turns (modules/voice/scripts/crissa-stt.py, /v1/turns)
    align    the word aligner (scripts/crissa-align.py, started by lib/crissa_aligner on first use)
    tts      the resident voice (modules/voice/scripts/crissa-tts.py, Kokoro)
    oracle   the face check and door feed (modules/oracle/scripts/crissa-oracle.py), 8197 since 2026-10-01
    turbo    the GPU voice (modules/voice/scripts/crissa-turbo.py, Chatterbox-Turbo), 8186
    audio8   the announcer on the CPU (modules/voice/scripts/crissa-audio8.py, Audio8 TTS), 8187
    multilingual  the optional GPU voice for other languages (modules/voice/scripts/crissa-multilingual.py,
             Chatterbox Multilingual V3), 8185
    pocket   the CPU voice (modules/voice/scripts/crissa-pocket.py, Pocket TTS, stock voices), 8188
    presence who is in the room (modules/presence/scripts/crissa-presence.py; the root scripts/crissa-presence.py starts
             it): Secure, Guest, the door watch, enrollment, 8219 - never the house service's 8191 (lib/crissa_presence)
    camera   the room camera's grabber (modules/presence/scripts/crissa-camera.py): /snapshot, /v1/mjpeg, PTZ, 8192
    enroll   the registration panel (modules/presence/scripts/crissa-enroll.py): the enroll coach, 8196
    panel    the Control Panel (scripts/crissa-panel-server.py), 8097; it also reads CRISSA_PORT_PANEL on its own
    bridge   the ear bridge (modules/ear/scripts/crissa-bridge.py): /health, /v1/abort, /v1/session/*, 8194
    feed     the wall's home feed (scripts/voiceonly-home-server.py: /v1/house, /v1/sound, the roll, Register), 8099
    comfy    the render module's ComfyUI (modules/render starts it with --port from here, or render.json comfy_url /
             CRISSA_COMFY_URL names one already running), 8184 - ComfyUI's own default, 8188, is Pocket's here
    demo     the demo helper (scripts/demo-hold.py, lib/demo_hold): Dev mode, the hold, Record your lines, 8124; the two
             panel pages are served with this address written in (2026-10-10)
    chip     the wall's parked browser chips (lib/house_chip.py), 8218 - the house's chip is on 8219, which is a pack's
             presence port here, so the house names its own in config/house-machine.json chip_url (2026-10-10)
    face_shell  the house-state provider the feed's /v1/house reads (scripts/face-shell-server.py: presence, the ear,
             chat, sitters, weather), 8088 - the house's own number (the User 2026-10-10, .101: it never shipped, so
             the Wall's headline, people, door and roll all read "house upstream down")
    browser  her own browser window's debugging port (lib/crissa_window: Crissa's own Brave / Chromium / Chrome
             instance, its profile under the state home - never the person's browser; the User 2026-10-11), 8183.
             Nothing of Crissa's binds it: the browser does, on loopback, when Crissa opens her window

port(name) reads, first hit wins:
    CRISSA_PORT_<NAME>            this one port
    the older per-piece variable  HOUSE_CONDUCTOR_PORT, CRISSA_ALIGN_PORT, CRISSA_TTS_PORT, CRISSA_ORACLE_PORT
    DEFAULTS[name] + CRISSA_PORT_OFFSET

Loopback only: nothing here binds or names another address.

ONE TABLE (2026-10-10). Every loopback address a shipped file names comes from here: directly, through a module's
inline copy of this table (a module installs on its own; tests/test_crissa_loopback_guard.py holds each copy equal,
rule for rule), or through a launch script that pins the variable before the module loads. That test fails on any
other loopback literal with a port in the shipped files, minus a documented allowlist.
"""
from __future__ import annotations

import os
import socket

DEFAULTS = {"player": 8100, "asr": 8096, "stt": 8190, "align": 8195, "tts": 8189, "oracle": 8197, "turbo": 8186, "multilingual": 8185, "audio8": 8187, "pocket": 8188, "presence": 8219, "camera": 8192, "enroll": 8196, "panel": 8097, "feed": 8099, "bridge": 8194, "comfy": 8184, "demo": 8124, "chip": 8218, "face_shell": 8088, "browser": 8183,
            "phone": 8226, "knowledge": 8227}

# the variables each piece read before this table existed; still honoured
LEGACY = {"player": "HOUSE_CONDUCTOR_PORT", "align": "CRISSA_ALIGN_PORT", "tts": "CRISSA_TTS_PORT",
          "oracle": "CRISSA_ORACLE_PORT", "face_shell": "FACE_SHELL_PORT"}

HOST = "127.0.0.1"


def _int(v: str | None) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 65536 else None


def offset(env=None) -> int:
    env = os.environ if env is None else env
    try:
        return int(str(env.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        return 0


def port(name: str, env=None) -> int:
    """The port for `name`. An unknown name is a KeyError: a typo must not find a free default."""
    env = os.environ if env is None else env
    base = DEFAULTS[name]
    for key in ("CRISSA_PORT_" + name.upper(), LEGACY.get(name)):
        if key:
            n = _int(env.get(key))
            if n is not None:
                return n
    return base + offset(env)


def url(name: str, env=None) -> str:
    return "http://%s:%d" % (HOST, port(name, env))


# THE ADDRESS KEYS (2026-10-10). The variables shipped code reads a whole address from, each with the piece and path it
# names. The pieces were the house's own before they were Crissa's, and their defaults are the house's ports, so beside
# another install (an offset) they asked that install: the trial of 2026-10-10 sent /v1/facelock and /v1/return_door to
# the reference machine's camera. The launcher hands every child these from this table (a value the caller already
# set wins). A variable whose readers disagree on its meaning (RAIL_PRESENCE_URL: a base in some, /v1/presence in
# others) is not here; its readers take the table directly.
ADDRESS_KEYS = {
    "VO_PRESENCE_URL": ("presence", "/v1/presence"),     # the feed and lib/vo_error_check
    "VO_ERROR_CHECK_URL": ("presence", "/v1/error_check"),
    "HOUSE_PRESENCE_URL": ("presence", ""),              # lib/vo_sound
    "VO_STT_HEALTH_URL": ("stt", "/health"),             # the feed
    "VO_OMNI_URL": ("stt", "/v1/omni"),
    "VO_HEAR_HEALTH_URL": ("bridge", "/health"),
    "VO_COACH_URL": ("enroll", "/events"),
    "VO_FRAME_URL": ("camera", "/snapshot"),             # the feed's camera cell (the launcher may name another)
    "VO_FIRMWARE_URL": ("camera", "/v1/firmware"),
    "HOUSE_WALL_CONDUCTOR": ("player", ""),              # the player's own idea of "the wall" (house-conductor.py)
    "CRISSA_PLAYER": ("player", ""),                     # crissa-ear.py, crissa_wall_verbs, the ear bridge
    "CRISSA_EAR_URL": ("bridge", ""),                    # scripts/xspaces-live.py
    "VO_HOUSE_URL": ("face_shell", "/v1/house"),         # the feed's house state (scripts/face-shell-server.py)
}


def addresses(ports: dict[str, int] | None = None, env=None) -> dict[str, str]:
    """Every ADDRESS_KEYS variable for this install: from `ports` (a launcher's resolved table), else port()."""
    out = {}
    for key, (name, path) in ADDRESS_KEYS.items():
        n = (ports or {}).get(name) or port(name, env)
        out[key] = "http://%s:%d%s" % (HOST, n, path)
    return out


def env_for(ports: dict[str, int]) -> dict[str, str]:
    """The variables that pin every piece to `ports` - what a launcher hands each child."""
    out = {"CRISSA_PORT_" + k.upper(): str(v) for k, v in ports.items()}
    for k, legacy in LEGACY.items():
        if k in ports:
            out[legacy] = str(ports[k])
    return out


FREE_OFFSET_STEPS = tuple(range(100, 1000, 50))


def free_offset(base_ports, in_use_=None, steps=FREE_OFFSET_STEPS) -> int | None:
    """The first offset at which every one of `base_ports` (this install's ports, without an offset) is free, else
    None. Measured, never a literal: the launcher's hint names it (2026-10-26)."""
    probe = in_use if in_use_ is None else in_use_
    ports = sorted({int(x) for x in (base_ports or []) if x})
    if not ports:
        return None
    for off in steps:
        if not any(probe(x + off) for x in ports):
            return int(off)
    return None


def in_use(p: int, timeout: float = 0.3) -> bool:
    """Does something already answer on this loopback port?"""
    try:
        with socket.create_connection((HOST, int(p)), timeout=timeout):
            return True
    except OSError:
        return False
