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


# BUILT = THE MAP HAS ENTRIES (Fable's buyer-path review N1, crissa-6a #13 for 1.0.6): Add a library writes an empty
# map.json before the door's plan, so a pack whose mouths are still pending read "built". With the switch on, a library
# is built only when its map holds at least one entry; the read is cached by the file's mtime and size, so a panel poll
# parses a map once. Switch CRISSA_SKIN_BUILT_ENTRIES: ON in a pack, never on the house (the User 2026-10-31: "put those
# on"); =0 = the file's presence, as before.
BUILT_SWITCH = "CRISSA_SKIN_BUILT_ENTRIES"
_BUILT_CACHE: dict = {}


def built_entries_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(BUILT_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def is_built(d: Path, env: dict | None = None) -> bool:
    """A library's mouth map is there (switch off) or holds entries (switch on)."""
    mp = Path(d) / BUILT_MARK
    if not built_entries_on(env):
        return mp.is_file()
    try:
        st = mp.stat()
    except OSError:
        return False
    sig = (st.st_mtime_ns, st.st_size)
    hit = _BUILT_CACHE.get(str(mp))
    if hit and hit[0] == sig:
        return hit[1]
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
        ok = isinstance(m, dict) and isinstance(m.get("entries"), list) and len(m["entries"]) > 0
    except (OSError, ValueError):
        ok = False
    _BUILT_CACHE[str(mp)] = (sig, ok)
    return ok


def cards_enabled(env: dict | None = None) -> bool:
    """CHOOSE SKIN AS PRODUCT (the User 2026-10-31: "a selection mechanism for User will matter to sales"): the panel's
    grouped agent -> flavor rows with a picture each. ON in a pack, never on the house (the User 2026-10-31: "On, whatever
    passes"); CRISSA_SKIN_CARDS=0 = the one dropdown as it was."""
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SKIN_CARDS", "1")).strip().lower() not in ("0", "off", "false", "no")


def menu_enabled(env: dict | None = None) -> bool:
    """The Wall MENU's Choose skin entry and the Wall's "Changing to ..." notice. ON in a pack, never on the house;
    CRISSA_SKIN_MENU=0 turns it off."""
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get("CRISSA_SKIN_MENU", "1")).strip().lower() not in ("0", "off", "false", "no")


CHANGING_FILE = "skin-changing.json"     # <state home>/skin-changing.json {key, label, at}: a pick is being put on
_CHANGING_C: dict = {"t": 0.0, "v": ""}


def mark_changing(key: str, label: str, home: Path | None = None) -> None:
    """A pick restarts the player lane: the desk Wall says "Changing to <label>..." over its stale feed meanwhile."""
    p = (home or crissa_home.HOME) / CHANGING_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".skin-changing-", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"key": key, "label": label, "at": time.time()}, f)
    os.replace(tmp, p)


def changing_text(home: Path | None = None, now: float | None = None, env: dict | None = None) -> str:
    """What the Wall's stale-feed corner says while a pick is put on ("Changing to Denim Crissa..."), else "". Read at
    most once a second (the Wall paints many frames a second); off unless CRISSA_SKIN_MENU=1."""
    t = time.time() if now is None else now
    if now is None and t - _CHANGING_C["t"] < 1.0:
        return _CHANGING_C["v"]
    v = ""
    if menu_enabled(env):
        try:
            d = json.loads(((home or crissa_home.HOME) / CHANGING_FILE).read_text(encoding="utf-8"))
            keep = float(json.loads((ROOT / "config" / "crissa-skin.json").read_text(encoding="utf-8"))["changing"]["notice_s"])
            if isinstance(d, dict) and 0 <= t - float(d.get("at") or 0) <= keep and d.get("label"):
                v = "Changing to %s\u2026" % str(d["label"])[:60]
        except (OSError, ValueError, KeyError, TypeError):
            v = ""
    if now is None:
        _CHANGING_C.update(t=t, v=v)
    return v


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


# AGENT AND FLAVOR (the User 2026-10-31: "agent and flavor"; "denim crissa, Friday Crissa"; crissa-6a MP store 10-31):
# a buyer picks in two levels - the agent (Crissa, Bash) and its flavor (the look or the day theme). library.json says
# both (`agent`, `flavor`; crissa-9f writes them); a library that does not falls back to its face (the inventory's
# `agent`, else its key) with its own name as the flavor - never hidden. Group by library.json's agent, never the
# inventory's (Friday's desk pack keeps the face "crissa"). Read by the Control Panel's grouped Choose skin only.
PICTURE_GLOBS = ("anchor.png", "anchor*.png", "portrait.png", "portrait*.png", "anchor.jpg", "portrait.jpg")


def agent_flavor(d: Path, key: str, label: str) -> dict:
    """{agent, flavor, title} for a library row: library.json's fields, else its face and its own name."""
    try:
        lj = json.loads((Path(d) / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lj = {}
    lj = lj if isinstance(lj, dict) else {}
    agent, flavor = str(lj.get("agent") or "").strip().lower(), str(lj.get("flavor") or "").strip().lower()
    if not agent:
        try:
            agent = str(json.loads((Path(d) / INVENTORY).read_text(encoding="utf-8")).get("agent") or "").strip().lower()
        except (OSError, ValueError, AttributeError):
            agent = ""
        agent = agent or key
    title = label if flavor else label
    if flavor and not lj.get("name"):
        title = ("%s %s" % (flavor, agent)).title() if flavor != "original" else agent.title()
    return {"agent": agent, "flavor": flavor or label, "title": title}


SKIN_CONFIG = Path("config") / "crissa-skin.json"


def offer_rule(root: Path | None = None, env: dict | None = None) -> dict:
    """CRISSA LOOKS ONLY IN 1.0.5 (the User 2026-10-31: "Crissa looks only for 1.0.5. V2 we can install the switcher"):
    {agents, note} from config/crissa-skin.json `offer`; agents None = every agent (the house, or CRISSA_SKIN_ALL_AGENTS=1)."""
    e = os.environ if env is None else env
    try:
        o = json.loads((ROOT / SKIN_CONFIG).read_text(encoding="utf-8")).get("offer") or {}   # the code's own rule
    except (OSError, ValueError, AttributeError):
        o = {}
    every = crissa_home.is_house() or str(e.get("CRISSA_SKIN_ALL_AGENTS", "0")).strip().lower() in ("1", "on", "true", "yes")
    agents = None if every or not o.get("agents") else {str(x).strip().lower() for x in o["agents"]}
    return {"agents": agents, "note": str(o.get("note") or "")}


def offered(lib: dict, rule: dict | None = None) -> bool:
    """Is this row offered to wear? A shipped face always (its own 10-28 list governs it); any other library when its
    agent (library.json `agent`, else its face) is one the rule names."""
    r = rule if rule is not None else offer_rule()
    if r["agents"] is None or lib.get("key") in SHIPPED:
        return True
    return agent_flavor(Path(lib["dir"]), lib["key"], lib.get("label") or lib["key"])["agent"] in r["agents"]


def picture(d: Path) -> Path | None:
    """The library's own small picture: library.json `picture` (a file inside it), else its anchor or portrait, else the
    first hub plate its inventory names. None when it has none."""
    d = Path(d)
    try:
        lj = json.loads((d / "library.json").read_text(encoding="utf-8"))
        cand = [lj.get("picture")] if isinstance(lj, dict) and lj.get("picture") else []
    except (OSError, ValueError):
        cand = []
    for c in cand:
        p = (d / str(c)).resolve()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg") and str(p).startswith(str(d.resolve()) + os.sep):
            return p
    for g in PICTURE_GLOBS:
        hit = sorted(d.glob(g))
        if hit and hit[0].is_file():
            return hit[0]
    try:
        hubs = json.loads((d / INVENTORY).read_text(encoding="utf-8")).get("hubs") or {}
    except (OSError, ValueError, AttributeError):
        hubs = {}
    for plate in (hubs.values() if isinstance(hubs, dict) else []):
        p = d / str(plate)
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg") and not p.is_symlink():
            return p
    return None


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
                        "built": is_built(d), "size": size(d)})
            seen.add(key)
    # the libraries the pack itself carries (<tree>/libraries/<key>/), shipped like the face table's
    pd = root / LIBRARIES_DIR
    if pd.is_dir():
        for d in sorted(p for p in pd.iterdir() if p.is_dir()):
            key = d.name.strip().lower()
            if not key or key in seen or not (d / INVENTORY).is_file():
                continue
            out.append({"key": key, "label": _label(d, key), "source": "shipped", "dir": str(d),
                        "built": is_built(d), "size": size(d)})
            seen.add(key)
    ld = libraries_dir(home)
    if ld.is_dir():
        for d in sorted(p for p in ld.iterdir() if p.is_dir()):
            key = d.name.strip().lower()
            if not key or key in seen or not (d / INVENTORY).is_file():
                continue
            out.append({"key": key, "label": _label(d, key), "source": _source(d), "dir": str(d),
                        "built": is_built(d), "size": size(d)})
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
    rule = offer_rule(root, env)
    if not offered(libs[key], rule):
        return {"ok": False, "error": "%s is installed, but not offered yet. %s" % (libs[key]["label"], rule["note"]),
                "refused": True}
    if not enabled(env):
        return {"ok": False, "error": "avatar picker is off (CRISSA_AVATAR_PICKER=0)", "refused": True}
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"key": key, "dir": libs[key]["dir"], "by": str(by)[:40], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    old = _state_raw(home)
    if isinstance(old.get("phone"), dict):
        rec["phone"] = old["phone"]          # a desk pick leaves the phone pick alone (6a 2026-10-31, Denim)
    fd, tmp = tempfile.mkstemp(prefix=".avatar-", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, p)
    return dict(rec, ok=True)


def _state_raw(home: Path | None = None) -> dict:
    try:
        d = json.loads(state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_state(d: dict, home: Path | None = None) -> None:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".avatar-", suffix=".json", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)


# A PHONE-ONLY LOOK (the User 2026-10-31 on Denim Crissa: "they will have V2 already so sell as Mobile pack standalone
# then"; "marries existing when we get the Mobile switcher in there"; crissa-6a): a bought Mobile-only pack installs as
# its own folder under libraries/ - library.json `mobile_only: true`, its `mobile_library` holding the phone side, no desk
# inventory - and is NEVER a desk library (libraries() does not list it, so nothing that wears a desk library can pick
# it up). Choose skin lists it under its agent marked "phone"; picking it sets the PHONE look only (avatar.json `phone`,
# beside the desk pick; the Wall keeps its skin); a desk pick leaves it alone until another phone look is picked or the
# phone is set to follow the desk look again. Switch CRISSA_SKIN_PHONE_ONLY: ON in a pack, never on the house; =0 = none
# listed and none picked (an existing phone pick is then ignored).
PHONE_ONLY_SWITCH = "CRISSA_SKIN_PHONE_ONLY"


def phone_only_on(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(PHONE_ONLY_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def _mobile_rel(d: Path) -> str:
    try:
        lj = json.loads((Path(d) / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    rel = lj.get("mobile_library") if isinstance(lj, dict) and lj.get("mobile_only") is True else None
    return rel if isinstance(rel, str) and rel and "/" not in rel and rel not in (".", "..") else ""


def phone_libraries(home: Path | None = None, env: dict | None = None) -> list[dict]:
    """Every phone-only look installed (<state home>/libraries/<key>/, library.json mobile_only + its mobile side)."""
    if not phone_only_on(env):
        return []
    ld = libraries_dir(home)
    out = []
    for d in sorted(p for p in ld.iterdir() if p.is_dir()) if ld.is_dir() else []:
        rel = _mobile_rel(d)
        if not rel or (d / INVENTORY).is_file() or not (d / rel / INVENTORY).is_file():
            continue
        key = d.name.strip().lower()
        out.append({"key": key, "label": _label(d, key), "source": _source(d), "dir": str(d), "mobile": str(d / rel),
                    "built": is_built(d / rel), "size": size(d / rel), "phone_only": True})
    return out


def phone_choice(home: Path | None = None, env: dict | None = None) -> dict:
    """The phone pick, or {} (none, switched off, or naming a look that is gone)."""
    ph = _state_raw(home).get("phone")
    if not isinstance(ph, dict) or not isinstance(ph.get("key"), str):
        return {}
    looks = {l["key"]: l for l in phone_libraries(home, env)}
    return dict(ph, mobile=looks[ph["key"]]["mobile"]) if ph["key"] in looks else {}


def choose_phone(key: str, home: Path | None = None, by: str = "control-panel", env: dict | None = None) -> dict:
    """Save the PHONE pick only (the desk pick and the Wall untouched); key '' = the phone follows the desk look again."""
    key = str(key or "").strip().lower()
    d = _state_raw(home)
    if not key:
        d.pop("phone", None)
        _write_state(d, home)
        return {"ok": True, "key": "", "phone": True}
    looks = {l["key"]: l for l in phone_libraries(home, env)}
    if key not in looks:
        return {"ok": False, "error": "unknown phone look %r" % key}
    rule = offer_rule(None, env)
    if not offered(looks[key], rule):
        return {"ok": False, "error": "%s is installed, but not offered yet. %s" % (looks[key]["label"], rule["note"]), "refused": True}
    d["phone"] = {"key": key, "dir": looks[key]["dir"], "by": str(by)[:40], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write_state(d, home)
    return dict(d["phone"], ok=True, phone=True, label=looks[key]["label"])


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
    rule = offer_rule(root, env)
    every = libraries(root, home)
    libs = [l for l in every if offered(l, rule)]
    c = choice(home)
    chosen = c.get("key") if c.get("key") in {l["key"] for l in libs} else ""
    running = running_face(home)
    for l in libs:
        l["chosen"] = l["key"] == chosen
        l["running"] = l["key"] == running
    looks = [l for l in phone_libraries(home, env) if offered(l, rule)]
    pc = phone_choice(home, env).get("key", "")
    for l in looks:
        l["chosen"] = l["key"] == pc
    out = {"ok": True, "enabled": enabled(env), "libraries": libs, "chosen": chosen, "running": running,
           **({"phone_looks": looks, "phone_chosen": pc} if looks else {}),
           "apply_needed": bool(chosen and running and chosen != running), "state_file": str(state_path(home))}
    if rule["agents"] is not None:
        out["agents_note"] = rule["note"]                 # one line where other agents would appear (1.0.5)
        if len(libs) < len(every):
            out["not_offered"] = len(every) - len(libs)   # installed, another agent's: counted, never listed
    return out


# THE AGENT LOOK POP-UP (the User 2026-10-31, relayed by crissa-84: "agent edit v2. quick switch outfit for crissa here
# in release 1.0"): one tap on the Wall MENU's places row opens her looks and one tap wears one, as easy as Wall On/Off;
# the agent switch and her edits stay the Control Panel's (v2). Her looks only: a built, offered library whose agent is one
# the offer rule names (else the worn look's own agent) - never a shipped face of another agent (the pack's Setup Lila).
# Switch CRISSA_SKIN_LOOK_POPUP: ON in a pack, never on the house; =0 = the row opens the Control Panel's Choose skin.
LOOK_POPUP_ENV = "CRISSA_SKIN_LOOK_POPUP"


def look_popup_on(env: dict | None = None) -> bool:
    if crissa_home.is_house() or not menu_enabled(env):
        return False
    e = os.environ if env is None else env
    return str(e.get(LOOK_POPUP_ENV, "1")).strip().lower() not in ("0", "off", "false", "no")


def looks(root: Path | None = None, home: Path | None = None, env: dict | None = None) -> dict:
    """{agent, worn, looks: [{key, title, flavor, on}], pending}: the pop-up's rows, the worn one first-marked `on`;
    `pending` counts her looks whose mouths are not built yet (wearing one would paint nothing; the panel lists them)."""
    st = status(root, home, env)
    worn = st["chosen"] or st["running"]
    rows = {l["key"]: dict(l, **agent_flavor(Path(l["dir"]), l["key"], l["label"])) for l in st["libraries"]}
    rule = offer_rule(root, env)
    agents = rule["agents"] or ({rows[worn]["agent"]} if worn in rows else None)
    mine = [r for r in rows.values() if agents is None or r["agent"] in agents]
    out = [{"key": r["key"], "title": r["title"], "flavor": r["flavor"], "on": r["key"] == worn}
           for r in mine if r.get("built")]
    agent = rows[worn]["agent"] if worn in rows else (next(iter(sorted(agents))) if agents else "")
    return {"agent": agent, "worn": worn, "looks": out, "pending": sum(1 for r in mine if not r.get("built"))}


def phone_follow(home: Path, procs, tick: bool = False) -> dict:
    """CRISSA_AGENT_PHONE (lib/crissa_skin_phone): the phone lane follows the worn library's Mobile packs - restarted now
    when no phone session is open, else owed to the ticker. Off, absent or broken: {} and nothing changes. `procs` is
    the caller's lib/crissa_procs (the launcher's request door)."""
    try:
        import crissa_skin_phone
    except ImportError:
        return {}

    def ask():
        if not procs.known(home, "phone"):
            return {"ok": False, "absent": True}
        return procs.request(home, "phone", "restart")
    try:
        return (crissa_skin_phone.tick if tick else crissa_skin_phone.after_pick)(home, ask)
    except Exception as e:  # noqa: BLE001  the pick is saved either way
        return {"phone": "error", "why": "%s: %s" % (type(e).__name__, str(e)[:120])}


def wear(key: str, by: str, home: Path, procs, root: Path | None = None, env: dict | None = None) -> dict:
    """THE ONE DESK PICK PATH (the Control Panel's Choose skin, the week's switch, the Wall MENU's Agent Look): save the
    pick, say "Changing to <name>..." on the desk Wall, ask the launcher to restart the player lane (`procs`, the
    caller's lib/crissa_procs), and let the phone follow. The pick is saved even when the launcher does not answer."""
    res = choose(key, root=root, home=home, by=by, env=env)
    if not res.get("ok"):
        return res
    libs = {l["key"]: l for l in libraries(root, home)}
    res["label"] = libs.get(res["key"], {}).get("label", res["key"])
    lib = libs.get(res["key"]) or {}
    name = agent_flavor(Path(lib["dir"]), res["key"], res["label"])["title"] if lib.get("dir") else res["label"]
    if menu_enabled(env):
        # the desk Wall says "Changing to <name>..." over its stale feed while the player lane restarts
        mark_changing(res["key"], name, home)
    try:
        res["restart"] = procs.request(home, "player", "restart")
    except Exception as e:  # noqa: BLE001  the pick is saved either way
        res["restart"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    res["note"] = "She wears %s%s" % (name, " · her player is restarting" if res["restart"].get("ok")
                                      else (" · " + str(res["restart"].get("error") or "")))
    res["phone"] = phone_follow(home, procs)
    if res["phone"].get("phone") == "owed":
        res["note"] += " · her phone follows when its conversation ends"
    return res
