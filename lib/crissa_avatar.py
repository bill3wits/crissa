"""Which avatar library she wears (the User 2026-10-26: "find the spot to allow choice from library ... a director
arrow for a small popup. Toggle won't work if three avatars to pick ... I just want it finished"; later the same day:
"it will need to be a dropdown menu as many will start with two, but gain more over time. Choose skin should be the
section" - so the Control Panel draws one section, Choose skin, with one dropdown, whatever the count).

A pack can hold more than one library: the shipped faces (the launcher's face table, each a poses folder in the tree)
and any library a person adds under <state home>/libraries/<key>/ - a store hub, a hub they made - which is a folder
with its own inventory.json. The Control Panel lists them (GET /panel/avatar), the person picks one (POST
/panel/avatar {key}), the choice is ONE file, <state home>/avatar.json, and the launcher reads it when the player
starts or restarts: HOUSE_POSE_DIR is the chosen folder. The player holds its library for its life, so a pick is
applied by a restart of the player lane, which the panel asks the launcher for the way its Restart key does
(lib/crissa_procs.request); the Wall draws the player's frames and follows by itself.

A LIBRARY OF ANOTHER SIZE PAINTS AT ITS OWN SIZE. The player paints the pack's native frame unless
HOUSE_FRAME_FROM_LIBRARY is on (lib/head_frame.frame_wh reads the inventory's `size` then). A store hub made from 480p
renders is 848x480, so `player_env` asks for that switch for any library whose inventory names a size other than the
native ones, the way the store intake does for itself (scripts/imagine-pack.py intake_env); the launcher puts it on the
player at plan and on the restart that applies a pick, and a variable already in the environment wins.

A PACK MAY CARRY A LIBRARY AND NAME IT THE DEFAULT SKIN (2026-10-27, cut 9: Crissa desk v2 first, Crissa second). A
library folder in the tree under libraries/<key>/ is listed as shipped beside the face table, and
config/crissa-default-skin.json {"key": K} names the one she wears when nobody has picked and the command line names no
face; a default whose library is not there (the house tree, a pack without it) falls back to the face table's default.
Kill CRISSA_DEFAULT_SKIN=0 (CRISSA_ in a pack): the face table's default, as before.

Kill CRISSA_AVATAR_PICKER=0 (CRISSA_ in a pack): the list still answers, a pick is refused, the launcher wears the
face its command line names. A missing or broken avatar.json is no choice. Pack only: the house answers 404.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import crissa_home

ROOT = Path(__file__).resolve().parents[1]
# the faces shipped in the tree (the launcher's --face table reads this one)
SHIPPED = {"crissa": "display/crissa-poses", "lila": "display/lila-poses"}
DEFAULT_FACE = "crissa"
DEFAULT_SKIN_FILE = Path("config") / "crissa-default-skin.json"   # {"key": K}: the library she wears unpicked
LIBRARIES_DIR = "libraries"          # <state home>/libraries/<key>/inventory.json = a library a person added
STATE_FILE = "avatar.json"
INVENTORY = "inventory.json"
BUILT_MARK = Path("visemes") / "phoneme-map" / "map.json"   # a library with its mouth map is built
# the frames the player paints with the switch off (lib/head_frame CLIP_W/CLIP_H and the 1296x704 films the house's
# intake fits to); any other inventory `size` needs HOUSE_FRAME_FROM_LIBRARY (lib/head_frame.FRAME_FROM_LIBRARY_ENV)
NATIVE_SIZES = ((736, 400), (1296, 704))
FRAME_FROM_LIBRARY_ENV = "HOUSE_FRAME_FROM_LIBRARY"
PLAYER_ENV_KEYS = (FRAME_FROM_LIBRARY_ENV,)


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_AVATAR_PICKER", "1")).strip().lower() not in ("0", "off", "no", "false")


def default_skin_enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_DEFAULT_SKIN", "1")).strip().lower() not in ("0", "off", "no", "false")


def default_face(root: Path | None = None, home: Path | None = None, env: dict | None = None) -> str:
    """The library she wears when nobody picked one: the pack's default skin when it is there and built, else the
    face table's default."""
    root = ROOT if root is None else Path(root)
    if default_skin_enabled(env):
        try:
            key = str(json.loads((root / DEFAULT_SKIN_FILE).read_text(encoding="utf-8")).get("key") or "").strip().lower()
        except (OSError, ValueError, AttributeError):
            key = ""
        if key:
            for l in libraries(root, home):
                if l["key"] == key and l["built"]:
                    return key
    return DEFAULT_FACE


def state_path(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / STATE_FILE


def libraries_dir(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / LIBRARIES_DIR


def _label(d: Path, key: str) -> str:
    """The library's own name when it says one (library.json or manifest.json `name`), else its key."""
    for f in ("library.json", "manifest.json"):
        try:
            n = json.loads((d / f).read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(n, str) and n.strip():
            return n.strip()
    return key


def size(d: Path) -> list | None:
    """The library's frame [w, h] from its inventory `size`, or None when it names none."""
    try:
        v = json.loads((d / INVENTORY).read_text(encoding="utf-8")).get("size")
    except (OSError, ValueError, AttributeError):
        return None
    try:
        if isinstance(v, dict):
            w, h = int(v.get("w") or 0), int(v.get("h") or 0)
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            w, h = int(v[0]), int(v[1])
        else:
            return None
    except (TypeError, ValueError):
        return None
    return [w, h] if w > 0 and h > 0 else None


# A LIBRARY'S OWN TURNS AND BOWS (2026-10-28, cut 10; the Composer bench has worn them since 10-27, 33c72de41 and
# b4e073eb5): a library that carries turn-fill.env (lib/pack_turns: its turned takes as its profile libraries) hands its
# HOUSE_TURN_* / HOUSE_MOUTH_TURN_* keys to the player, over the shipped mouth defaults (whose HOUSE_TURN_LIB_45 is the
# Crissa face's profile-45, a key Crissa desk v2 does not have: every frame past HOUSE_TURN_FRONT_MAX painted nothing);
# a library that carries bow maps (clip-*.bow.json, pose-bow-map.py) gets HOUSE_MOUTH_BOW=1. Kills
# CRISSA_LIBRARY_TURN_ENV=0, CRISSA_LIBRARY_BOW=0.
TURN_ENV_FILE = "turn-fill.env"
BOW_ENV = "HOUSE_MOUTH_BOW"


def is_library_key(key: str) -> bool:
    """A player variable that belongs to the library worn (dropped when another library is put on)."""
    return key in PLAYER_ENV_KEYS or key == BOW_ENV or key.startswith("HOUSE_TURN_") or key.startswith("HOUSE_MOUTH_TURN_")


def _off(env: dict | None, name: str) -> bool:
    e = os.environ if env is None else env
    return str(e.get(name, "1")).strip().lower() in ("0", "off", "no", "false")


def turn_env(d: Path) -> dict:
    out: dict = {}
    try:
        lines = (Path(d) / TURN_ENV_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for ln in lines:
        k, eq, v = ln.strip().partition("=")
        if eq and not k.startswith("#") and (k.startswith("HOUSE_TURN_") or k.startswith("HOUSE_MOUTH_TURN_")):
            out[k] = v
    return out


def player_env(pose_dir: str | os.PathLike, env: dict | None = None) -> dict:
    """What the player's environment needs for this library beyond HOUSE_POSE_DIR: the frame-from-library switch for
    a library of another size (a 480p store hub is 848x480), its own turn libraries, the bow switch when it carries
    bow maps; nothing for a native library that carries neither."""
    d = Path(pose_dir)
    out: dict = {}
    got = size(d)
    if got and tuple(got) not in NATIVE_SIZES:
        out[FRAME_FROM_LIBRARY_ENV] = "1"
    if not _off(env, "CRISSA_LIBRARY_TURN_ENV"):
        out.update(turn_env(d))
    if not _off(env, "CRISSA_LIBRARY_BOW") and d.is_dir() and any(d.glob("clip-*.bow.json")):
        out[BOW_ENV] = "1"
    return out


def _source(d: Path) -> str:
    """store: a pack folder's receipt (pack.json) is there; yours: anything else a person put there."""
    return "store" if (d / "pack.json").is_file() else "yours"


# A SHIPPED FACE CAN STAY IN THE BOX BUT OFF THE LIST (the User 2026-10-28: "User has Crissa v2 and crissa Mobile and Lila
# and no others"; 1.0.1 hides the classic face, 1.0.2 removes it): the default-skin file's `hidden` keys are left out of
# Choose skin and the Control Panel (and so cannot be picked); the fallback face (DEFAULT_FACE) still wears when no
# default skin is built. Kill CRISSA_SKIN_HIDDEN=0 (every shipped face listed, as before).
def hidden_skins(root: Path | None = None, env: dict | None = None) -> set:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_SKIN_HIDDEN", "1")).strip().lower() in ("0", "off", "no", "false"):
        return set()
    try:
        got = json.loads(((ROOT if root is None else Path(root)) / DEFAULT_SKIN_FILE).read_text(encoding="utf-8")).get("hidden")
    except (OSError, ValueError, AttributeError):
        return set()
    return {str(k).strip().lower() for k in got} if isinstance(got, list) else set()


def libraries(root: Path | None = None, home: Path | None = None) -> list[dict]:
    """Every library this install can wear, shipped first: {key, label, source, dir, built, size}."""
    root = ROOT if root is None else Path(root)
    out, seen = [], set()
    hidden = hidden_skins(root)
    for key, rel in SHIPPED.items():
        if key in hidden:
            continue
        d = root / rel
        if (d / INVENTORY).is_file():
            out.append({"key": key, "label": _label(d, key), "source": "shipped", "dir": str(d),
                        "built": (d / BUILT_MARK).is_file(), "size": size(d)})
            seen.add(key)
    # the libraries the pack itself carries (<tree>/libraries/<key>/), shipped like the face table's
    pd = root / LIBRARIES_DIR
    if pd.is_dir():
        for d in sorted(p for p in pd.iterdir() if p.is_dir()):
            key = d.name.strip().lower()
            if not key or key in seen or not (d / INVENTORY).is_file():
                continue
            out.append({"key": key, "label": _label(d, key), "source": "shipped", "dir": str(d),
                        "built": (d / BUILT_MARK).is_file(), "size": size(d)})
            seen.add(key)
    ld = libraries_dir(home)
    if ld.is_dir():
        for d in sorted(p for p in ld.iterdir() if p.is_dir()):
            key = d.name.strip().lower()
            if not key or key in seen or not (d / INVENTORY).is_file():
                continue
            out.append({"key": key, "label": _label(d, key), "source": _source(d), "dir": str(d),
                        "built": (d / BUILT_MARK).is_file(), "size": size(d)})
            seen.add(key)
    return out


def choice(home: Path | None = None) -> dict:
    """The saved pick, or {} (missing, broken, or naming a library that is gone is no choice)."""
    try:
        d = json.loads(state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict) or not isinstance(d.get("key"), str):
        return {}
    return d


def pose_dir(root: Path | None = None, home: Path | None = None, face: str | None = None,
             env: dict | None = None) -> tuple[str, str]:
    """(face key, poses folder) the player wears: the saved pick when the picker is on and the library is still
    there, else `face` (the launcher's --face), else the default face. The folder is a path string as the launcher
    passes it (HOUSE_POSE_DIR)."""
    root = ROOT if root is None else Path(root)
    libs = {l["key"]: l for l in libraries(root, home)}
    want = face or default_face(root, home, env)
    if enabled(env):
        c = choice(home)
        if c and c.get("key") in libs:
            want = c["key"]
    if want in libs:
        return want, libs[want]["dir"]
    return want, str(root / SHIPPED.get(want, SHIPPED[DEFAULT_FACE]))


def choose(key: str, root: Path | None = None, home: Path | None = None, by: str = "control-panel",
           env: dict | None = None) -> dict:
    """Save the pick (temp + os.replace). Refuses an unknown library and the kill switch; restarts nothing."""
    key = str(key or "").strip().lower()
    libs = {l["key"]: l for l in libraries(root, home)}
    if key not in libs:
        return {"ok": False, "error": "unknown library %r" % key, "libraries": sorted(libs)}
    if not enabled(env):
        return {"ok": False, "error": "avatar picker is off (CRISSA_AVATAR_PICKER=0)", "refused": True}
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"key": key, "dir": libs[key]["dir"], "by": str(by)[:40], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    fd, tmp = tempfile.mkstemp(prefix=".avatar-", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, p)
    return dict(rec, ok=True)


def running_face(home: Path | None = None) -> str:
    """The face the launcher's live table records for this run (<state home>/run/procs.json, launcher.face; the
    launcher writes it at start and on a player restart that applied a pick), '' when unknown."""
    try:
        d = json.loads(((home or crissa_home.HOME) / "run" / "procs.json").read_text(encoding="utf-8"))
        v = (d.get("launcher") or {}).get("face")
    except (OSError, ValueError, AttributeError):
        return ""
    return v if isinstance(v, str) else ""


def status(root: Path | None = None, home: Path | None = None, env: dict | None = None) -> dict:
    """What the panel draws: the libraries, the pick, the face running, whether a restart is still owed."""
    libs = libraries(root, home)
    c = choice(home)
    chosen = c.get("key") if c.get("key") in {l["key"] for l in libs} else ""
    running = running_face(home)
    for l in libs:
        l["chosen"] = l["key"] == chosen
        l["running"] = l["key"] == running
    return {"ok": True, "enabled": enabled(env), "libraries": libs, "chosen": chosen, "running": running,
            "apply_needed": bool(chosen and running and chosen != running), "state_file": str(state_path(home))}
