"""Where the ear keeps its state: one directory under the Crissa state home (2026-10-09).

The house ran this package from its own private directories (a security directory for identity, a
presence directory for scratch speakers, a models directory for the wake head, a dot-directory for
flags). A pack has none of those: everything is under `<crissa home>/ear/`, resolved here and nowhere
else. `CRISSA_HOME` names the state home; a pack puts `crissa_home.py` beside this file, the source
tree reads it from lib/.

    <home>/ear/identity/people/<id>/voice/*.json   voice prints (the voice bank), person.json aliases
    <home>/ear/mouths/<voice>/*.json                her own voice's prints (the TTS veto bank)
    <home>/ear/scratch/                             unnamed speakers S1, S2
    <home>/ear/wake/                                an optional wake-word head
    <home>/ear/turns.jsonl                          the turn log
    <home>/tts-gate.json                            the mouth's gate (the bridge writes it)
    <home>/house-voice.json                         the voice master lock
    <home>/voice-only                               the voice-only flag the Wall reads

Nothing here names a person, a device or another machine's path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _state_home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
        return Path(H.HOME)
    except ImportError:
        pass
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        import crissa_home as H                     # noqa: E402  the source tree's
        return Path(H.HOME)
    except ImportError:
        return Path.home() / ".crissa"


HOME = _state_home()


def is_house() -> bool:
    """The house's own install (lib/crissa_home.is_house); a pack's copy of crissa_home never names state_home."""
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
    except ImportError:
        try:
            import crissa_home as H                 # the source tree's (lib on the path)
        except ImportError:
            return False
    try:
        return bool(H.is_house())
    except Exception:  # noqa: BLE001
        return False
EAR = HOME / "ear"


def ear_path(*parts: str) -> Path:
    return EAR.joinpath(*parts)


def home_path(*parts: str) -> Path:
    return HOME.joinpath(*parts)
