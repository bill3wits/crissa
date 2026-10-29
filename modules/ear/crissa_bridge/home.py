"""Where the ear bridge keeps its state: the Crissa state home (2026-10-09).

The house's bridge read its flags, logs and the mouth's gate from a private dot-directory and an
agent framework's profile folders. A pack has one state home (`CRISSA_HOME`, else the avatar manifest's
`state_home`, else ~/.crissa; lib/crissa_home), and every path here comes from it:

    <home>/crissa-thinking.json      she is on it (the Wall's pulse reads it)
    <home>/tts-gate.json           the mouth's gate (the ear reads it)
    <home>/announce-play.json      a PA line on the air (house-speak writes it)
    <home>/logs/ear-bridge.jsonl   the bridge's own log
    <home>/ear/incidents/          security incidents (the intrusion module)
    <home>/ear/identity/people/    who the house knows (shared with the ear)
    <home>/agent.json              the Connect endpoint (lib/crissa_agent)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[3]      # modules/ear/crissa_bridge -> the pack


def _state_home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from . import crissa_home as H
        return Path(H.HOME)
    except ImportError:
        pass
    try:
        sys.path.insert(0, str(PACK_ROOT / "lib"))
        import crissa_home as H                     # noqa: E402
        return Path(H.HOME)
    except ImportError:
        return Path.home() / ".crissa"


HOME = _state_home()
EAR = HOME / "ear"


def home_path(*parts: str) -> Path:
    return HOME.joinpath(*parts)


def ear_path(*parts: str) -> Path:
    return EAR.joinpath(*parts)


def pack_script(name: str) -> Path:
    """A script of the pack this module ships in (scripts/house-speak.py and friends)."""
    return PACK_ROOT / "scripts" / name
