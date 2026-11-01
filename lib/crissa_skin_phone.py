"""THE PHONE FOLLOWS THE WORN AGENT (Fable's buyer-path review B5a, crissa-6a #6 for 1.0.6; the ruling 10-31:
"the phone follows the worn agent's mobile/packs and switches only when a phone session ends").

Before, the phone lane (scripts/phone-app-server.py) served one fixed folder, <state home>/phone/packs, and its
crissa-phone-listen pack whatever she wore: a buyer who wore Friday and walked to the phone met Crissa Mobile.

With the switch on, the phone's packs are the WORN library's own Mobile side when it has one:
    <worn library>/<library.json mobile_library>/packs/       (an agent folder: crissa-9f's build_agent.py layout)
and the fixed folder otherwise (a library with no Mobile side, a shipped face, nothing picked). The pack the phone
talks on is that folder's show.json `talk` (else crissa-phone-listen, the server's own default).

WHEN IT SWITCHES. A pick restarts the player lane at once (the Choose skin path, unchanged) and updates the phone
lane's spec; the phone LANE restarts only when no phone session is open (lib/phone_session's file, read as the desk Wall
reads it: the page or app in use, or her answer playing on it). An open session leaves an owed switch, <state home>/skin-phone-owed.json, and the Control
Panel's ticker restarts the phone lane on its first tick with no session open - the Each-day quiet rule, for the phone.
A pick that leaves the phone's folder where it was restarts nothing. What the lane was last started with is
<state home>/skin-phone.json, written by the launcher.

Switch CRISSA_AGENT_PHONE (the v2 plan's name, PLAN-20261031-agent-switcher-v2 row 8): ON in a pack, never on the house
(the User 2026-10-31: "put those on"); =0 = the fixed folder and today's argv, byte for byte.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import crissa_home

SWITCH = "CRISSA_AGENT_PHONE"
SERVED = "skin-phone.json"          # what the phone lane was last started with (the launcher writes it)
OWED = "skin-phone-owed.json"       # a pick that waits for the phone session to end
FIXED = Path("phone") / "packs"     # the folder the phone always served
DEFAULT_PACK = "crissa-phone-listen"
MOBILE_KEY = "mobile_library"


def enabled(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def _home(home) -> Path:
    return Path(home) if home is not None else Path(crissa_home.HOME)


def _has_packs(d: Path) -> bool:
    return d.is_dir() and any(d.glob("*/manifest.json"))


def mobile_packs(lib: Path) -> Path | None:
    """<lib>/<mobile_library>/packs when the library names a Mobile side holding phone packs; else None."""
    try:
        lj = json.loads((Path(lib) / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rel = lj.get(MOBILE_KEY) if isinstance(lj, dict) else None
    if not isinstance(rel, str) or not rel.strip() or Path(rel).is_absolute() or ".." in Path(rel).parts:
        return None
    d = Path(lib) / rel / "packs"
    return d if _has_packs(d) else None


def talk_pack(packs: Path) -> str:
    """The pack the phone talks on: the folder's show.json `talk` when that pack is there, else the default."""
    try:
        t = json.loads((packs / "show.json").read_text(encoding="utf-8")).get("talk")
        if isinstance(t, str) and (packs / t / "manifest.json").is_file():
            return t
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_PACK


def resolve(home=None, root: Path | None = None, env: dict | None = None) -> dict:
    """{packs, pack, from}: the folder and talk pack the phone lane should serve now. `from` = the library key whose
    Mobile side it is, or "fixed"."""
    h = _home(home)
    fixed = h / FIXED
    if enabled(env):
        import crissa_avatar
        ph = crissa_avatar.phone_choice(h, env)        # a PHONE look picked (a phone-only pack, Denim): it wins
        d = mobile_packs(Path(ph["dir"])) if ph else None
        if d is not None:
            return {"packs": str(d), "pack": talk_pack(d), "from": ph["key"]}
        key, pose = crissa_avatar.pose_dir(root, h, None, env)
        d = mobile_packs(Path(pose))
        if d is not None:
            return {"packs": str(d), "pack": talk_pack(d), "from": key}
    return {"packs": str(fixed), "pack": talk_pack(fixed) if fixed.is_dir() else DEFAULT_PACK, "from": "fixed"}


def argv_for(base: list, r: dict) -> list:
    """The phone lane's argv with this folder and pack (any earlier --packs / --pack dropped)."""
    out, skip = [], False
    for a in base:
        if skip:
            skip = False
            continue
        if a in ("--packs", "--pack"):
            skip = True
            continue
        if a.startswith("--packs=") or a.startswith("--pack="):
            continue
        out.append(a)
    return out + ["--packs", r["packs"], "--pack", r["pack"]]


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + p.stem + "-", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


def mark_served(r: dict, home=None) -> None:
    """The launcher: the phone lane starts with `r` now."""
    _write(_home(home) / SERVED, dict(r, at=time.strftime("%Y-%m-%dT%H:%M:%S")))


def served(home=None) -> dict:
    return _read(_home(home) / SERVED)


def session_open(home=None, now: float | None = None) -> bool:
    """A phone session is open: the desk Wall's own reader of the phone server's file (lib/crissa_phone_room.active,
    active_until in the future; the idle window is already folded in)."""
    import crissa_phone_room
    p = Path(os.environ.get("CRISSA_PHONE_SESSION") or (_home(home) / "phone-session.json"))
    return crissa_phone_room.active(p, now)


def after_pick(home=None, request=None, now: float | None = None) -> dict:
    """The pick path, after the player's restart was asked: restart the phone lane now when its folder changes and no
    session is open; owe it when one is. `request()` asks the launcher (crissa_procs.request(home, "phone", "restart"))."""
    if not enabled():
        return {"phone": "off"}
    h = _home(home)
    want, was = resolve(h), served(h)
    if was and want["packs"] == was.get("packs") and want["pack"] == was.get("pack"):
        try:
            (h / OWED).unlink()
        except OSError:
            pass
        return {"phone": "same", "packs": want["packs"]}
    if session_open(h, now):
        _write(h / OWED, dict(want, since=time.strftime("%Y-%m-%dT%H:%M:%S")))
        return {"phone": "owed", "why": "a phone session is open", "packs": want["packs"]}
    res = request() if request else {"ok": False, "error": "no request path"}
    if res.get("absent"):              # the launcher runs no phone lane (no packs at its start): nothing to switch
        try:
            (h / OWED).unlink()
        except OSError:
            pass
        return {"phone": "no lane"}
    if res.get("ok"):
        try:
            (h / OWED).unlink()
        except OSError:
            pass
        return {"phone": "restarting", "packs": want["packs"]}
    _write(h / OWED, dict(want, since=time.strftime("%Y-%m-%dT%H:%M:%S"), error=str(res.get("error") or "")[:200]))
    return {"phone": "owed", "why": str(res.get("error") or "the restart was refused"), "packs": want["packs"]}


def tick(home=None, request=None, now: float | None = None) -> dict:
    """The Control Panel's ticker: an owed phone switch goes on the first tick with no session open."""
    h = _home(home)
    if not enabled() or not (h / OWED).is_file():
        return {}
    if session_open(h, now):
        return {"phone": "waiting", "why": "a phone session is open"}
    return after_pick(h, request, now)
