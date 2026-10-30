"""Where the id module keeps and finds things: one place, read from the state home (2026-10-09).

The house's enrol tools resolved their roots from fixed machine paths (an identity directory under a records
volume, the house's own repo beside the home directory, service interpreters by absolute path). A pack has one
state home (lib/crissa_home: CRISSA_HOME, else ~/.crissa) and one install root, so every path here comes from
those two, and every service address from the shared port table (lib/crissa_ports), a variable still winning.

    identity_root()   the identity store: CRISSA_IDENTITY, else HOUSE_ID_ROOT, else <state home>/identity.
                      people/<id>/{person.json, face/*.json, voice/*.json}, people/_index.json, site.json,
                      site/{init-sequence.json, init-wizard.json, init-state.json, first-boot.json}.
                      The presence module reads the same tree (modules/id/NOTE-presence-format.md).
    PACK              this install's root (the pack): scripts/house-speak.py, display/crissa-poses, config/.
    STATE             the state home itself (caches, logs, the id env, the models).

Importing this module exports HOUSE_ID_ROOT into the process when it is unset, so every reader of the house's
variable (lib/house_user, the presence module) lands on the same store without being told twice.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
MODULE = _HERE.parent                 # modules/id
PACK = MODULE.parent.parent           # the install root


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
        return Path(H.HOME)
    except ImportError:
        pass
    try:
        sys.path.insert(0, str(PACK / "lib"))
        import crissa_home as H                     # noqa: E402  the source tree's
        return Path(H.HOME)
    except ImportError:
        return Path.home() / ".crissa"


STATE = _home()


def identity_root() -> Path:
    env = (os.environ.get("CRISSA_IDENTITY") or os.environ.get("HOUSE_ID_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    return STATE / "identity"


def is_house() -> bool:
    """The house's own install (its avatar manifest names state_home); a pack's copy of crissa_home never does."""
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
    except ImportError:
        try:
            sys.path.insert(0, str(PACK / "lib"))
            import crissa_home as H                 # noqa: E402  the source tree's
        except ImportError:
            return False
    try:
        return bool(H.is_house())
    except Exception:  # noqa: BLE001
        return False


def presence_data() -> Path:
    """Where the presence module keeps its room geometry and camera stops (CRISSA_PRESENCE_DATA overrides)."""
    env = os.environ.get("CRISSA_PRESENCE_DATA", "").strip()
    return Path(env).expanduser() if env else STATE / "presence" / "data"


# ---- the port table (lib/crissa_ports), read inline so the module installs on its own -------------------------
_DEFAULT_PORTS = {"player": 8100, "asr": 8096, "stt": 8190, "tts": 8189, "oracle": 8197, "presence": 8219,
                  "camera": 8192, "enroll": 8196, "bridge": 8194}
_LEGACY_PORTS = {"player": "HOUSE_CONDUCTOR_PORT", "oracle": "CRISSA_ORACLE_PORT", "tts": "CRISSA_TTS_PORT",
                 "enroll": "ENROLL_UI_PORT"}


def port(name: str, env=None) -> int:
    e = os.environ if env is None else env
    for k in ("CRISSA_PORT_" + name.upper(), _LEGACY_PORTS.get(name)):
        v = str(e.get(k) or "").strip() if k else ""
        if v.isdigit() and 0 < int(v) < 65536:
            return int(v)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    return _DEFAULT_PORTS[name] + off


def _url(name: str) -> str:
    return "http://127.0.0.1:%d" % port(name)


def player_url() -> str:
    return _url("player")


def presence_url() -> str:
    return _url("presence")


def camera_url() -> str:
    return _url("camera")


def enroll_url() -> str:
    return _url("enroll")


def stt_url() -> str:
    return _url("stt")


def ear_bridge_url() -> str:
    return _url("bridge")          # the port table's name (CRISSA_PORT_BRIDGE), 2026-10-10


def speak_url() -> str:
    """The coach's spoken lines go through the player's own voice (POST /v1/speak {text, voice})."""
    return player_url() + "/v1/speak"


# ---- the brain: the Connect endpoint (lib/crissa_agent), never an agent runtime ---------------------------------
def _agent_cfg() -> dict:
    try:
        try:
            from . import crissa_agent as za
        except ImportError:
            sys.path.insert(0, str(PACK / "lib"))
            import crissa_agent as za               # noqa: E402
        cfg = za.load() or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001  no Connect yet: the walker's splitter answers instead of the brain
        return {}


def brain_url() -> str:
    env = os.environ.get("CRISSA_BRAIN_URL", "").strip()
    if env:
        return env
    base = str(_agent_cfg().get("url") or "").rstrip("/")
    return (base + "/chat/completions") if base else ""


def brain_model() -> str:
    return os.environ.get("CRISSA_BRAIN_MODEL", "").strip() or str(_agent_cfg().get("model") or "")


def brain_key() -> str:
    """The Connect endpoint's key, resolved the way the player resolves it (crissa_agent.resolve_key); '' = none."""
    env = os.environ.get("CRISSA_BRAIN_KEY", "").strip()
    if env:
        return env
    try:
        try:
            from . import crissa_agent as za
        except ImportError:
            sys.path.insert(0, str(PACK / "lib"))
            import crissa_agent as za               # noqa: E402
        return str(za.resolve_key(_agent_cfg().get("key")) or "")
    except Exception:  # noqa: BLE001
        return ""


def rails_root() -> Path:
    """Where the pack lives (the house's HOUSE_RAILS_ROOT, a config value there): this install."""
    env = os.environ.get("HOUSE_RAILS_ROOT", "").strip()
    return Path(env).expanduser() if env else PACK


def rails_path(*parts: str) -> Path:
    return rails_root().joinpath(*parts)


# ---- the camera switch (2026-10-10) -----------------------------------------------------------------------------
# With the camera off (CRISSA_NO_CAMERA=1, the launcher's --no-camera) nothing in this module talks to a camera: the
# coach's camera posts answer {"ok": false, "camera": "off"}, and every other request to a camera address in a process
# that loaded this package is refused before it leaves (URLError "camera: off", said once). With a camera on, nothing
# is wrapped.
CAMERA_OFF = {"ok": False, "camera": "off"}
_said: set = set()
_orig_urlopen = None


def camera_off(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on")


def say_camera_off(what: str) -> None:
    if what not in _said:
        _said.add(what)
        print("[id] camera: off - %s" % what, file=sys.stderr, flush=True)


def _netloc(u: str) -> str:
    import urllib.parse
    try:
        p = urllib.parse.urlsplit(u)
        host = "127.0.0.1" if p.hostname in ("localhost", "::1") else p.hostname
        return "%s:%d" % (host, p.port or 80) if p.scheme in ("http", "https") and host else ""
    except ValueError:
        return ""


def guard_camera() -> bool:
    global _orig_urlopen
    if not camera_off():
        return False
    if _orig_urlopen is not None:
        return True
    import urllib.error
    import urllib.request
    _orig_urlopen = urllib.request.urlopen

    def urlopen(target, *args, **kwargs):
        u = target.get_full_url() if isinstance(target, urllib.request.Request) else str(target or "")
        cams = {_netloc(os.environ.get(k) or "") for k in ("RAIL_CAMERA_URL", "CRISSA_CAMERA_URL")} | {_netloc(camera_url())}
        if camera_off() and _netloc(u) and _netloc(u) in cams:
            say_camera_off("no call to the camera (and every later one)")
            raise urllib.error.URLError("camera: off")
        return _orig_urlopen(target, *args, **kwargs)

    urllib.request.urlopen = urlopen
    return True


os.environ.setdefault("HOUSE_ID_ROOT", str(identity_root()))
guard_camera()
