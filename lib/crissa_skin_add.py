"""ADD A LIBRARY: a pack the buyer bought becomes a skin they can choose (the User 2026-10-31: "say a user buys one, how
do THEY choose which Crissa to run that day?", "is there a path to install the full libraries into theirs from Store?";
crissa-6a, MP store "a buyer's choice of skin", option A).

What the buyer downloads is a zoeyos-loop-pack/1 (lib/imagine_package/pack_sales.pack_zip: `<id>/pack.json`, `files/`,
`inventory-rows.json`, `map-entries.json`, `stamp-audit.json`), not a library: lib/crissa_hub_install wants a finished
library folder with an inventory.json at its root. So the pack is CONVERTED on the buyer's machine, every step checked
before anything reaches Choose skin:

    1. unzip into a part folder under <state home>/skin-staging/ (crissa_import's member check: no path that
       leaves the folder, no symlink, not larger than a pack may be); exactly one pack folder inside
    2. the store's stamp audit, as delivered: stamp-audit/1, no gap, and its pack_sha recomputed from these files
    3. a NEW, empty library seeded beside it (inventory: the pack's face, its plates' size, no clips)
    4. the import door's own plan (crissa_import.plan, src = the pack, bring_hubs) against that new folder - never the
       library she wears - then its files, map entries, hubs, pools and rows written there; the rows keep the state the
       pack gives them (the buyer's own pick is the yes the house's arm step asks the User for)
    5. lib/crissa_hub_install.install, whole: the copy, the part folder, the receipt. It is listed in Choose skin at
       once and never worn: wearing it stays a pick.

An agent library may later gain a Mobile side: the library sits at <state home>/libraries/<key>/, and a Mobile pack can
add <key>/mobile/ beside it (the Composer's `mobile_library` key); that second pack is not built here.

THE STAMP FORMULA IS A COPY. stamp_pack_sha is the store's (lib/imagine_package/pack.stamp_pack_sha on
store-pricing-20261029, 89b98cf97, and the store's runner/stamp_pack.py), carried here so a buyer's kit can check a pack
without the store's code; tests/test_crissa_skin_add.py pins it to a value the store computed and, when the store's
runner is on the machine, to that file itself. One copy goes when 89b98cf97 merges.

Switches, ON in a pack and never on the house (the User 2026-10-31: "On, whatever passes"): CRISSA_SKIN_ADD (the Control
Panel's Add a library) and CRISSA_SKIN_STORE (the Store's Export returns to Connect, which asks "Install <name>?");
=0 turns either off.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import time
import uuid
import zipfile
from pathlib import Path

import crissa_home

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "crissa-skin.json"
SWITCH = "CRISSA_SKIN_ADD"
STORE_SWITCH = "CRISSA_SKIN_STORE"
STAMP_AUDIT = "stamp-audit.json"
STAMP_SCHEMA = "stamp-audit/1"
PACK_SCHEMA = "zoeyos-loop-pack/1"
# the wire spelling the store writes, and the kit rename's spelling 1.0.6 tests wrote (crissa-6a, MP 11.ak 2026-10-31)
PACK_SCHEMAS = (PACK_SCHEMA, "crissa-loop-pack/1")
INVENTORY_DOC = "inventory-rows.json"         # a pack's default `inventory` (lib/imagine_package/pack.INVENTORY_DOC)
STAGING = "skin-staging"                      # <state home>/skin-staging/<token>/: beside libraries/, never inside it
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


class Refused(Exception):
    """Why a pack is not added. One plain line."""


def _on(name: str, env: dict | None = None) -> bool:
    """ON in a pack, OFF on the house (the User 2026-10-31: "On, whatever passes" for 1.0.5); NAME=0 turns it off."""
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(name, "1")).strip().lower() not in ("0", "off", "false", "no")


def enabled(env: dict | None = None) -> bool:
    return _on(SWITCH, env)


def store_enabled(env: dict | None = None) -> bool:
    return _on(STORE_SWITCH, env)


def config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))["add"]


# ── the store's stamp audit (a copy: see the module's note) ──────────────────────

def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp_pack_sha(pack_dir: Path) -> str | None:
    """sha256 of {"files": pack.json's files map, "inventory": sha256 of the rows file} as canonical JSON."""
    pack_dir = Path(pack_dir)
    try:
        meta = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    inv = pack_dir / str(meta.get("inventory") or INVENTORY_DOC)
    if not inv.is_file():
        return None
    doc = {"files": meta.get("files") or {}, "inventory": _sha_file(inv)}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_stamps(pack_dir: Path) -> list[str]:
    """Why the pack's stamp audit does not clear it; [] = clean."""
    try:
        a = json.loads((Path(pack_dir) / STAMP_AUDIT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        a = None
    if not isinstance(a, dict) or a.get("schema") != STAMP_SCHEMA:
        return ["it carries no stamp audit from the store"]
    out = []
    gaps = a.get("gaps")
    if not isinstance(gaps, dict):
        out.append("its stamp audit names no gaps map")
    elif gaps:
        out.append("the store's audit lists clips without their stamps (%s)" % ", ".join(sorted(gaps))[:200])
    if a.get("pack_sha") != stamp_pack_sha(pack_dir):
        out.append("its stamp audit is not for these files (the pack changed after the store checked it)")
    return out


# ── unzip, convert, install ──────────────────────────────────────────────────────

def shipped_key(key: str, root: Path | None = None) -> bool:
    """A key this box already carries as its own look (the face table, hidden ones included, or <tree>/libraries/<key>/).
    A bought library under that key installs but is never listed: libraries() lists the shipped one first and skips
    the home folder, so Choose skin wears the shipped face (2026-10-31, crissa-1e: Classic Lila keyed `lila`).
    Kill CRISSA_SKIN_SHIPPED_KEY=0."""
    if os.environ.get("CRISSA_SKIN_SHIPPED_KEY", "1") == "0":
        return False
    import crissa_avatar
    root = crissa_avatar.ROOT if root is None else Path(root)
    key = str(key or "").strip().lower()
    return key in crissa_avatar.SHIPPED or (root / crissa_avatar.LIBRARIES_DIR / key / crissa_avatar.INVENTORY).is_file()


def _shipped_refusal(name: str, key: str) -> str:
    return "%s was not added: its name %r is one of this Crissa's own looks, so it could never be worn" % (name, key)


def _libraries_dir(home: Path | None) -> Path:
    import crissa_hub_install
    return crissa_hub_install.libraries_dir(home)


def staging_root(home: Path | None = None) -> Path:
    """Where a pack is unzipped and converted before it is installed (hub_install refuses a source inside libraries/)."""
    return (home or crissa_home.HOME) / STAGING


def _png_size(p: Path) -> list[int] | None:
    """A PNG's width x height from its header (no image library needed); None for anything else."""
    try:
        with p.open("rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return [int(w), int(h)] if w and h else None


def unpack(zip_path: Path, work: Path) -> Path:
    """The sale zip into `work` (crissa_import's checked unzip); answers the one pack folder inside."""
    import crissa_import as door
    if not zipfile.is_zipfile(zip_path):
        raise Refused("that file is not a pack: it is not a .zip archive")
    dest = work / "unzipped"
    try:
        door._unzip(Path(zip_path), dest)
    except door.ImportRefused as e:
        raise Refused(str(e)) from e
    except (zipfile.BadZipFile, OSError) as e:
        raise Refused("the archive cannot be read: %s" % e) from e
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        for name in dirnames + filenames:
            if (Path(dirpath) / name).is_symlink():
                raise Refused("the pack holds a link: a pack ships its own files")
    tops = [p for p in dest.iterdir()]
    packs = [p for p in tops if p.is_dir() and (p / "pack.json").is_file()]
    if (dest / "pack.json").is_file() and not packs:
        return dest
    if len(packs) != 1 or len(tops) != 1:
        raise Refused("the archive is not one pack folder")
    return packs[0]


def _meta(pack: Path) -> dict:
    try:
        meta = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused("its pack.json cannot be read") from e
    if not isinstance(meta, dict) or meta.get("schema") not in PACK_SCHEMAS:
        raise Refused("it is not a %s" % PACK_SCHEMA)
    if not str(meta.get("face") or "") or not str(meta.get("id") or ""):
        raise Refused("its pack.json names no face or no id")
    return meta


def label_of(meta: dict) -> str:
    racks = meta.get("racks") or []
    lab = (racks[0].get("label") if racks and isinstance(racks[0], dict) else "") or ""
    return str(lab or (meta.get("listing") or {}).get("title") or meta["id"])[:80]


def build_library(pack: Path, lib: Path) -> dict:
    """Steps 3-4: a NEW library at `lib` (must not exist) seeded from the pack, the import door's plan against it,
    then its files, map, hubs, pools and rows written. Answers what landed."""
    import crissa_import as door
    meta = _meta(pack)
    hubs = meta.get("hubs") if isinstance(meta.get("hubs"), dict) else {}
    if not hubs:
        raise Refused("the pack brings no hub, so it cannot be a library of its own")
    size = None
    for plate in hubs.values():
        size = _png_size(pack / "files" / str(plate))
        if size:
            break
    lib.mkdir(parents=True)
    inv = {"schema": "character_kit/v1", "agent": str(meta["face"]), "fps": 24, "hubs": {}, "pools": {}, "clips": {},
           "racks": [r for r in (meta.get("racks") or []) if isinstance(r, dict)], "kit": {"keys": []},
           "note": "made from the pack %s on this machine (lib/crissa_skin_add)" % meta["id"]}
    if size:
        inv["size"] = size
    (lib / door.INVENTORY_REL).write_text(json.dumps(inv, indent=1) + "\n", encoding="utf-8")
    mp = lib / door.MAP_REL
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"schema": "phoneme_map/v1", "entries": [], "n": 0}, indent=1) + "\n", encoding="utf-8")
    # a library pack spells its map tiles the library's way (visemes/phoneme-map/tiles/x.png), which the door takes only
    # under CRISSA_IMPORT_TILE_PATHS - the house's own pack_install.plan sets it the same way, for the plan only
    old = os.environ.get("CRISSA_IMPORT_TILE_PATHS")
    os.environ["CRISSA_IMPORT_TILE_PATHS"] = "1"
    try:
        p = door.plan(meta["id"], lib, src=pack, bring_hubs=True)
    except door.ImportRefused as e:
        raise Refused(str(e)) from e
    finally:
        if old is None:
            os.environ.pop("CRISSA_IMPORT_TILE_PATHS", None)
        else:
            os.environ["CRISSA_IMPORT_TILE_PATHS"] = old
    files_root = pack / "files"
    for rel in p["add_files"]:
        dest = lib / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(files_root / rel, dest)
    inv["hubs"].update(p["hubs_add"])
    inv["clips"].update(p["clips_add"])
    for pool, keys in list(p["pools_new"].items()) + list(p["pools_add"].items()):
        cur = inv["pools"].setdefault(pool, [])
        cur.extend(k for k in keys if k not in cur)
    inv["kit"]["keys"] = sorted(k for k, r in p["clips_add"].items() if r.get("element"))
    (lib / door.INVENTORY_REL).write_text(json.dumps(inv, indent=1) + "\n", encoding="utf-8")
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["entries"] = list(m["entries"]) + p["map_add"]
    m["n"] = len(m["entries"])
    mp.write_text(json.dumps(m, indent=1) + "\n", encoding="utf-8")
    loops = [k for k in (meta.get("loops") or []) if k in inv["clips"]]
    # THE PACK'S OWN library.json WINS (Fable's buyer-path review B4, 2026-10-31): a re-stamped pack ships its roles
    # (walk_in, walk_out, glance, notes, yawn, tool_work) and may ship agent / flavor / name / picture; it was copied in
    # with the files above. Only what it does not say is added, and no key is ever dropped.
    try:
        lj = json.loads((lib / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lj = {}
    lj = lj if isinstance(lj, dict) else {}
    lj.setdefault("name", label_of(meta))
    lj["installed_from"] = pack_record(pack, meta)       # which pack and which audit: the version a later Add compares
    if loops:
        # the player boots, talks and rests on the pack's loop when the pack names none (lib/avatar reads these)
        for k in ("listen_loop", "boot_loop", "rest_loop"):
            lj.setdefault(k, loops[0])
    (lib / "library.json").write_text(json.dumps(lj, indent=1) + "\n", encoding="utf-8")
    tune = write_tune(lib, p["dials_add"], lj.get("listen_loop") or (loops[0] if loops else ""), meta["id"])
    return {"clips": len(p["clips_add"]), "files": len(p["add_files"]), "map_entries": len(p["map_add"]),
            "hubs": sorted(p["hubs_add"]), "live": inv["kit"]["keys"], "size": size, "loops": loops, "tune": tune}


# THE BOUGHT SKIN WEARS ITS TUNE (Fable's buyer-path review S3, crissa-6a #5 for 1.0.6): a sale pack built from an
# agent library carries that library's lock as dials.json (lib/imagine_package/pack.pack_dials: the lock's dials, one
# set per loop). Before, the converter wrote them only as per-loop sets (mouth-dials.clips.json) and the library had no
# lock, so the 10-27 standard (the chin pin in every library lock) was met on no bought skin and the one-level tune
# (the User 09-27) read three own tunes. With the switch on, the NEW library's lock (mouth-dials.json) is the set of
# the loop it listens on (else the set most loops share); a loop whose set differs keeps its own set, as before. Only
# ever a library this converter just made: build_library refuses a folder that exists, and the import door into a worn
# library still refuses mouth-dials.json (crissa_import.RESERVED). Switch CRISSA_SKIN_TUNE: ON in a pack, never on the house
# (the User 2026-10-31: "put those on"); =0 = the per-loop sets only.
TUNE_SWITCH = "CRISSA_SKIN_TUNE"
LOCK = "mouth-dials.json"


def tune_enabled(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(TUNE_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def lock_set(sets: dict, listen: str = "") -> tuple[str, dict]:
    """(the loop whose set becomes the lock, that set): the listen loop's when it has one, else the set most loops
    share (ties: the first loop by name)."""
    if not sets:
        return "", {}
    if listen in sets:
        return listen, dict(sets[listen])
    canon = {k: json.dumps(v, sort_keys=True) for k, v in sets.items()}
    best = max(sorted(sets), key=lambda k: sum(1 for c in canon.values() if c == canon[k]))
    return best, dict(sets[best])


def write_tune(lib: Path, sets: dict, listen: str, pack_id: str, env: dict | None = None, now: float | None = None) -> dict:
    """The pack's dials into a library this converter just made: the lock (switch on) and the per-loop sets that differ
    from it (every set, switch off). Answers what was written."""
    import crissa_import as door
    if not sets:
        return {"lock": None, "loop_sets": []}
    at = time.time() if now is None else now
    own = dict(sets)
    lock_from = None
    if tune_enabled(env):
        if (lib / LOCK).exists():
            raise Refused("this library already has a tune; a pack's dials are written only into a new library")
        lock_from, dials = lock_set(sets, listen)
        doc = dict(dials, locked=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at)),
                   by="the store's tune: pack %s, the %s set (lib/crissa_skin_add)" % (pack_id, lock_from))
        (lib / LOCK).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        own = {k: v for k, v in sets.items() if v != dials}
    if own:
        clip = {k: {"dials": v, "at": at, "by": "pack %s" % pack_id} for k, v in own.items()}
        (lib / door.CLIP_DIALS_REL).write_text(json.dumps({"clips": clip}, indent=1) + "\n", encoding="utf-8")
    return {"lock": lock_from, "loop_sets": sorted(own)}


def _stage(zip_path: Path, home: Path | None) -> dict:
    """Steps 1-4 into <libraries>/.skin-staging/<token>/: answers {token, key, name, ...}; nothing listed yet."""
    token = uuid.uuid4().hex
    work = staging_root(home) / token
    work.mkdir(parents=True)
    if zipfile.is_zipfile(zip_path) and agent_top(Path(zip_path)):
        try:
            return _stage_agent(Path(zip_path), work, token, home)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
    try:
        pack = unpack(Path(zip_path), work)
        meta = _meta(pack)
        bad = check_stamps(pack)
        if bad:
            raise Refused("not added: %s" % bad[0])
        import crissa_hub_install as hub
        key = str(meta["id"]).strip().lower()
        if not hub._KEY.match(key):
            raise Refused("its id %r cannot name a library" % key)
        if shipped_key(key):
            raise Refused(_shipped_refusal(label_of(meta), key))
        if (_libraries_dir(home) / key).exists():
            raise Refused(version_note(_libraries_dir(home) / key, pack_record(pack, meta), label_of(meta)) if remove_enabled()
                          else "%s is already installed" % label_of(meta))
        got = build_library(pack, work / "lib")
        shutil.rmtree(work / "unzipped", ignore_errors=True)
        rec = dict(got, token=token, key=key, name=label_of(meta), pack=meta["id"], at=time.time())
        (work / "staged.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
        return rec
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _staged(token: str, home: Path | None) -> tuple[Path, dict]:
    if not _TOKEN.match(str(token or "")):
        raise Refused("no such pack waiting")
    work = staging_root(home) / token
    try:
        rec = json.loads((work / "staged.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused("no such pack waiting (it may have been answered already)") from e
    return work, rec


def confirm(token: str, home: Path | None = None, *, order: str = "") -> dict:
    """Step 5 for a staged pack: install the library whole (crissa_hub_install); listed at once, never worn."""
    import crissa_hub_install as hub
    work, rec = _staged(token, home)
    try:
        out = hub.install(work / "lib", rec["key"], home, order=order, name=rec["name"], move=bool(rec.get("agent_pack")))
    except hub.Refused as e:
        raise Refused(str(e)) from e
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res = {"ok": True, "key": rec["key"], "name": rec["name"], "clips": rec["clips"], "files": out["files"],
           "worn": False, "note": "%s is in Choose skin. Pick it there to wear it." % rec["name"]}
    if rec.get("phone_only"):
        res.update(phone_only=True, note="%s is in Choose skin as a phone look. Pick it there to wear it on your phone." % rec["name"])
        return res
    import crissa_avatar
    rule = crissa_avatar.offer_rule()
    row = {"key": rec["key"], "dir": out["dir"], "label": rec["name"]}
    if not crissa_avatar.offered(row, rule):
        # CRISSA LOOKS ONLY IN 1.0.5: it lands in its folder, kept for the agent switcher, and says so plainly
        res.update(offered=False, note="%s is installed, but not offered yet. %s" % (rec["name"], rule["note"]))
    return res


# ── a whole agent in one zip (crissa-6a #7 for 1.0.6) ───────────────────────────────────────────────────────
# 6a's ruling (MASTER-PROMPT 2026-10-31): "a sale of an agent flavor delivers ONE zip of the whole agent folder (PC +
# mobile/ + mobile/packs), with a stamp audit per side bound into its pack_sha". The shape, agreed with crissa-9f
# (store side: runner/stamp_pack.py), zoeyos-agent-pack/1:
#
#   <key>/                       the PC library (inventory.json at its root; none = "no desk side yet", refused)
#   <key>/stamp-audit.json       the PC side's audit:     pack_sha = canon({"side": "pc", "files": PC file map})
#   <key>/<mobile_library>/      the 9:16 library (library.json "mobile_library", "mobile")
#   <key>/mobile/stamp-audit.json  the Mobile side's:     pack_sha = canon({"side": "mobile", "files": Mobile file map})
#   <key>/mobile/packs/<p>/stamp-audit.json  each phone pack's, the phone formula (manifest + video)
#   <key>/agent-pack.json        {schema, key, agent, flavor, name, pack_version, sides: {pc, mobile, packs: {p: sha}},
#                                 pack_sha = canon(sides)}: the one sha the listing and pack_sales name
#
# A side's file map is {posix path inside the side: sha256} over every file in it but its own stamp-audit.json and the
# EXCLUDED ones (build records with machine paths, intake history, install leftovers); the PC side leaves out the Mobile
# folder and agent-pack.json; the Mobile side keeps packs/ (posters, icons, show.json and each pack's own audit are
# files like any other). canon = sha256 of the JSON with sorted keys and separators (",", ":"). An excluded file in a
# zip is never installed. The folder is installed WHOLE as one skin, moved (not copied) into libraries/; its own
# mouth-dials.json is its tune. Its size is checked against this disk's room, not the loop-pack door's caps (config
# add.agent_keep_free_gb). Switch CRISSA_SKIN_AGENT_PACK: ON in a pack, never on the house (the User 2026-10-31: "put
# those on"); =0: an agent zip is refused in one line.
AGENT_SWITCH = "CRISSA_SKIN_AGENT_PACK"
AGENT_DOC = "agent-pack.json"
AGENT_SCHEMA = "zoeyos-agent-pack/1"
AGENT_SCHEMAS = (AGENT_SCHEMA, "crissa-agent-pack/1")      # wire, renamed (6a, MP 11.ak)
EXCLUDE_NAMES = frozenset({"BUILD.json", "bench.env"})
EXCLUDE_DIRS = frozenset({".pack-install", "__pycache__", "_takes", "museum", "picks-auto"})
EXCLUDE_RE = re.compile(r"\.(pre-|bak|runner-|before-|with-)|\.(writing|installing)$")   # build_agent.py's SKIP_RE


def agent_enabled(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(AGENT_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def canon(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    return any(d in EXCLUDE_DIRS for d in parts[:-1]) or parts[-1] in EXCLUDE_NAMES or bool(EXCLUDE_RE.search(parts[-1]))


def side_map(root: Path, skip_dir: str = "", skip_names=()) -> dict:
    """{posix rel: sha256} for every file under `root` but its own stamp-audit.json, `skip_names`, anything under
    `skip_dir`/ and the excluded."""
    out = {}
    for f in sorted(Path(root).rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        rel = f.relative_to(root).as_posix()
        if rel == STAMP_AUDIT or rel in skip_names or excluded(rel) or (skip_dir and rel.startswith(skip_dir + "/")):
            continue
        out[rel] = _sha_file(f)
    return out


def mobile_dir_of(folder: Path) -> str:
    try:
        rel = json.loads((Path(folder) / "library.json").read_text(encoding="utf-8")).get("mobile_library")
    except (OSError, ValueError, AttributeError):
        rel = None
    if not isinstance(rel, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", rel) or rel in (".", ".."):
        return ""
    return rel


def phone_pack_sha(pack: Path) -> str:
    """The store's phone-pack formula (runner/stamp_pack.phone_pack): its manifest and its film."""
    man = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    return canon({"manifest": _sha_file(pack / "manifest.json"), "video": _sha_file(pack / str(man.get("video") or "loop.mp4"))})


def is_mobile_only(folder: Path) -> bool:
    """A PHONE-ONLY agent pack (the User 2026-10-31 on Denim Crissa: "sell as Mobile pack standalone"; the rule agreed
    with crissa-9f): library.json `mobile_only: true` and no desk inventory at the root."""
    try:
        lj = json.loads((Path(folder) / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(lj, dict) and lj.get("mobile_only") is True and not (Path(folder) / "inventory.json").is_file()


def agent_sides(folder: Path) -> dict:
    """{pc, mobile, packs} recomputed from the files, as the store computes them. A mobile-only pack: pc is null and a
    `root` seal covers the root's own files (library.json names its agent, flavor and mobile side; no audit, no clips)."""
    mob = mobile_dir_of(folder)
    root_map = side_map(folder, skip_dir=mob, skip_names=(AGENT_DOC,))
    if is_mobile_only(folder):
        sides = {"pc": None, "root": canon({"side": "root", "files": root_map}),
                 "mobile": canon({"side": "mobile", "files": side_map(folder / mob)}) if mob and (folder / mob).is_dir() else "",
                 "packs": {}}
        for man in sorted((folder / mob / "packs").glob("*/manifest.json")) if mob else []:
            sides["packs"][man.parent.name] = phone_pack_sha(man.parent)
        return sides
    sides = {"pc": canon({"side": "pc", "files": root_map}),
             "mobile": canon({"side": "mobile", "files": side_map(folder / mob)}) if mob and (folder / mob).is_dir() else "",
             "packs": {}}
    if mob:
        for man in sorted((folder / mob / "packs").glob("*/manifest.json")):
            sides["packs"][man.parent.name] = phone_pack_sha(man.parent)
    return sides


def _audit_says(d: Path, sha: str, what: str) -> str:
    try:
        a = json.loads((d / STAMP_AUDIT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        a = None
    if not isinstance(a, dict) or a.get("schema") != STAMP_SCHEMA:
        return "%s carries no stamp audit from the store" % what
    if not isinstance(a.get("gaps"), dict):
        return "%s's stamp audit names no gaps map" % what
    if a["gaps"]:
        return "the store's audit of %s lists clips without their stamps (%s)" % (what, ", ".join(sorted(a["gaps"]))[:160])
    if a.get("pack_sha") != sha:
        return "%s's stamp audit is not for these files (it changed after the store checked it)" % what
    return ""


def check_agent(folder: Path) -> tuple[dict, list[str]]:
    """(agent-pack.json, why it does not clear; [] = clean): every side's audit against its files, the sides against
    agent-pack.json, and its pack_sha against the sides."""
    try:
        doc = json.loads((folder / AGENT_DOC).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict) or doc.get("schema") not in AGENT_SCHEMAS:
        return {}, ["its %s is not a %s" % (AGENT_DOC, AGENT_SCHEMA)]
    name = doc.get("name") or folder.name
    mobile_only = doc.get("mobile_only") is True
    if mobile_only:
        if (folder / "inventory.json").is_file():
            return doc, ["%s says mobile_only but carries a desk inventory" % name]
        if not is_mobile_only(folder):
            return doc, ["%s says mobile_only but its library.json does not" % name]
        if not phone_only_enabled():
            return doc, ["%s is a phone-only pack; this Crissa does not take phone-only packs yet" % name]
        mob = mobile_dir_of(folder)
        extra = sorted(f.name for f in folder.iterdir() if f.is_file() and f.name not in ("library.json", "lock-provenance.json", AGENT_DOC)
                       and not excluded(f.name))
        if extra:
            return doc, ["a phone-only pack carries only library.json beside its Mobile side (found %s)" % ", ".join(extra)[:120]]
    elif not (folder / "inventory.json").is_file():
        return doc, ["%s has no desk side yet" % name]
    mob = mobile_dir_of(folder)
    if not mob or not (folder / mob / "inventory.json").is_file():
        return doc, ["%s has no Mobile side (library.json names no mobile_library with an inventory)" % (doc.get("name") or folder.name)]
    sides = agent_sides(folder)
    bad = [x for x in ((_audit_says(folder, sides["pc"], "the desk side") if not mobile_only else ""),
                       _audit_says(folder / mob, sides["mobile"], "the Mobile side")) if x]
    for p, sha in sides["packs"].items():
        why = _audit_says(folder / mob / "packs" / p, sha, "the phone pack %s" % p)
        if why:
            bad.append(why)
    if doc.get("sides") != sides:
        bad.append("its %s does not name these sides (the pack changed after the store sealed it)" % AGENT_DOC)
    if doc.get("pack_sha") != canon(sides):
        bad.append("its pack_sha is not its sides' (the pack changed after the store sealed it)")
    return doc, bad


def agent_top(zip_path: Path) -> str:
    """The one top folder of a zip that is a whole agent (it holds <top>/agent-pack.json), else ''."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError):
        return ""
    tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
    if len(tops) != 1:
        return ""
    top = next(iter(tops))
    return top if "%s/%s" % (top, AGENT_DOC) in names else ""


def room(home: Path | None = None) -> int:
    """Bytes this disk can take for a whole agent and still keep config add.agent_keep_free_gb free."""
    d = staging_root(home)
    d.mkdir(parents=True, exist_ok=True)
    return int(shutil.disk_usage(d).free - float(config()["agent_keep_free_gb"]) * (1 << 30))


def _unzip_agent(zip_path: Path, dest: Path, home: Path | None) -> Path:
    """The zip into `dest`, every member checked before one is written: no path that leaves, no link, the unpacked size
    within this disk's room."""
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        if sum(i.file_size for i in infos) > room(home):
            raise Refused("this computer does not have the room for it (it needs %.1f GB, keeping %s GB free)"
                          % (sum(i.file_size for i in infos) / 1e9, config()["agent_keep_free_gb"]))
        for i in infos:
            name = i.filename
            if (name.startswith(("/", "~")) or "\\" in name or ".." in name.split("/")
                    or (i.external_attr >> 16) & 0o170000 == 0o120000):
                raise Refused("the archive holds a path that leaves its folder")
        z.extractall(dest)
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        for name in dirnames + filenames:
            if (Path(dirpath) / name).is_symlink():
                raise Refused("the pack holds a link: a pack ships its own files")
    tops = list(dest.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise Refused("the archive is not one agent folder")
    return tops[0]


PHONE_ONLY_SWITCH = "CRISSA_SKIN_PHONE_ONLY"


def phone_only_enabled(env: dict | None = None) -> bool:
    """A phone-only agent pack is taken and listed (CRISSA_SKIN_PHONE_ONLY: ON in a pack, never on the house; =0 kills)."""
    import crissa_avatar
    return crissa_avatar.phone_only_on(env)


def desk_library_of(agent: str, home: Path | None = None) -> bool:
    """This install has a desk library of that agent (shipped or added): a phone-only look is a look FOR it."""
    import crissa_avatar
    for l in crissa_avatar.libraries(home=home):
        if crissa_avatar.agent_flavor(Path(l["dir"]), l["key"], l.get("label") or l["key"])["agent"] == agent:
            return True
    return False


def agent_record(doc: dict, folder: Path) -> dict:
    try:
        v = int(doc.get("pack_version"))
    except (TypeError, ValueError):
        v = None
    try:
        at = json.loads((folder / STAMP_AUDIT).read_text(encoding="utf-8")).get("at") or ""
    except (OSError, ValueError, AttributeError):
        at = ""
    return {"id": str(doc.get("key") or ""), "pack_sha": str(doc.get("pack_sha") or ""), "version": v, "audited": str(at)}


def _stage_agent(zip_path: Path, work: Path, token: str, home: Path | None) -> dict:
    """Steps 1-2 for a whole agent: unzip, check every side, set aside what is not sealed; nothing to convert."""
    if not agent_enabled():
        raise Refused("this pack is a whole agent (desk and phone); this Crissa does not take whole agents yet")
    import crissa_hub_install as hub
    folder = _unzip_agent(zip_path, work / "unzipped", home)
    doc, bad = check_agent(folder)
    if bad:
        raise Refused("not added: %s" % bad[0])
    key = str(doc.get("key") or folder.name).strip().lower()
    name = str(doc.get("name") or key)[:80]
    if not hub._KEY.match(key):
        raise Refused("its key %r cannot name a library" % key)
    if shipped_key(key):
        raise Refused(_shipped_refusal(name, key))
    if doc.get("mobile_only") is True:
        agent = str(doc.get("agent") or "").strip().lower()
        if not desk_library_of(agent, home):
            raise Refused("%s is a phone look for %s and needs %s's desk library on this computer" % (name, agent.title(), agent.title()))
    if (_libraries_dir(home) / key).exists():
        raise Refused(version_note(_libraries_dir(home) / key, agent_record(doc, folder), name) if remove_enabled()
                      else "%s is already installed" % name)
    for f in sorted(folder.rglob("*"), reverse=True):        # what the seal does not cover is never installed
        rel = f.relative_to(folder).as_posix()
        if f.is_file() and excluded(rel):
            f.unlink()
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True):
        if d.name in EXCLUDE_DIRS:
            shutil.rmtree(d, ignore_errors=True)
    lj_p = folder / "library.json"
    try:
        lj = json.loads(lj_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lj = {}
    lj = lj if isinstance(lj, dict) else {}
    lj.setdefault("name", name)
    for k in ("agent", "flavor"):
        if doc.get(k):
            lj.setdefault(k, str(doc[k]))
    lj["installed_from"] = agent_record(doc, folder)
    lj_p.write_text(json.dumps(lj, indent=1) + "\n", encoding="utf-8")
    os.replace(folder, work / "lib")
    shutil.rmtree(work / "unzipped", ignore_errors=True)
    inv_p = work / "lib" / "inventory.json"
    if not inv_p.is_file():                        # a phone-only pack: its Mobile side's inventory
        inv_p = work / "lib" / mobile_dir_of(work / "lib") / "inventory.json"
    inv = json.loads(inv_p.read_text(encoding="utf-8"))
    rec = {"token": token, "key": key, "name": name, "pack": key, "agent_pack": True, "clips": len(inv.get("clips") or {}),
           "phone_only": doc.get("mobile_only") is True,
           "mobile": mobile_dir_of(work / "lib"), "phone_packs": sorted(doc.get("sides", {}).get("packs") or {}),
           "at": time.time()}
    (work / "staged.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return rec


# ── which version, and taking a look away (crissa-6a #12 for 1.0.6; Fable's buyer-path review N4, N5) ─────────
# Switch CRISSA_SKIN_REMOVE: ON in a pack, never on the house (the User 2026-10-31: "put those on"); =0 off. Remove a look in Choose skin, and a pack added again says whether
# it is the same version, a newer one or an older one, instead of only "already installed".
REMOVE_SWITCH = "CRISSA_SKIN_REMOVE"
REMOVED = "skin-removed"             # <state home>/skin-removed/<key>-<stamp>/: a removed look, moved aside, never deleted


def remove_enabled(env: dict | None = None) -> bool:
    if crissa_home.is_house():
        return False
    e = os.environ if env is None else env
    return str(e.get(REMOVE_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def pack_record(pack: Path, meta: dict) -> dict:
    """The pack a library was made from: its id, the store audit's pack_sha and when the store audited it."""
    try:
        a = json.loads((Path(pack) / STAMP_AUDIT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        a = {}
    a = a if isinstance(a, dict) else {}
    return {"id": str(meta.get("id") or ""), "pack_sha": str(a.get("pack_sha") or ""), "audited": str(a.get("at") or "")}


def version_note(installed: Path, new: dict, name: str) -> str:
    """One line for a pack whose look is already installed (`new` = pack_record / agent_record of the incoming one):
    the same version, a newer one, or an older one. The order is the pack's own `version` when both say one (a whole
    agent pack, from its listing), else the store's audit time (a loop pack's pack_version is a hash with no order); a
    library installed before this record says so."""
    try:
        was = json.loads((Path(installed) / "library.json").read_text(encoding="utf-8")).get("installed_from") or {}
    except (OSError, ValueError, AttributeError):
        was = {}
    if was.get("pack_sha") and was["pack_sha"] == new.get("pack_sha"):
        return "%s is already installed (this same version)" % name
    take = ("To take it: wear another look, remove %s in Choose skin, then add this file again." % name)
    if not was.get("audited") and not isinstance(was.get("version"), int):
        return "%s is already installed, from an earlier pack. %s" % (name, take)
    if isinstance(was.get("version"), int) and isinstance(new.get("version"), int) and was["version"] != new["version"]:
        if new["version"] > was["version"]:
            return "A newer version of %s is in this file (version %d; yours %d). %s" % (name, new["version"], was["version"], take)
        return "%s is already installed, and yours is newer (version %d)" % (name, was["version"])
    if str(new.get("audited") or "") > str(was.get("audited") or ""):
        return "A newer version of %s is in this file (checked by the store %s; yours %s). %s" % (
            name, str(new["audited"])[:10], str(was.get("audited") or "")[:10], take)
    return "%s is already installed, and yours is as new or newer (checked %s)" % (name, str(was.get("audited") or "")[:10])


def worn_keys(home: Path | None = None) -> set:
    """The look picked and the look running: neither is ever removed."""
    import crissa_avatar
    return {k for k in ((crissa_avatar.choice(home) or {}).get("key"), crissa_avatar.running_face(home),
                        crissa_avatar.phone_choice(home).get("key")) if k}


def remove(key: str, home: Path | None = None, now: float | None = None) -> dict:
    """Take a look the buyer added out of Choose skin: moved aside to <state home>/skin-removed/<key>-<stamp>/, never
    deleted. Never the worn look, never one shipped with her."""
    import crissa_hub_install as hub
    key = str(key or "").strip().lower()
    if not hub._KEY.match(key):
        return {"ok": False, "error": "no such look"}
    d = _libraries_dir(home) / key
    if not (d / "inventory.json").is_file() and not hub._phone_only(d):
        return {"ok": False, "error": "%s is not a look you added (a look shipped with her stays)" % key}
    if key in worn_keys(home):
        return {"ok": False, "error": "she is wearing %s: wear another look first" % key, "worn": True}
    t = time.time() if now is None else now
    dest_root = (home or crissa_home.HOME) / REMOVED
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / ("%s-%s" % (key, time.strftime("%Y%m%d-%H%M%S", time.localtime(t))))
    n = 1
    while dest.exists():
        n += 1
        dest = dest_root / ("%s-%s-%d" % (key, time.strftime("%Y%m%d-%H%M%S", time.localtime(t)), n))
    os.replace(d, dest)
    return {"ok": True, "key": key, "moved_to": str(dest),
            "note": "%s is out of Choose skin; its files are kept in %s" % (key, dest)}


def discard(token: str, home: Path | None = None) -> dict:
    work, rec = _staged(token, home)
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": True, "discarded": rec["name"]}


def sweep(home: Path | None = None, now: float | None = None) -> list[str]:
    """Remove staged packs nobody answered within config add.staged_keep_h."""
    keep = float(config()["staged_keep_h"]) * 3600.0
    t = time.time() if now is None else now
    gone = []
    root = staging_root(home)
    for d in (root.iterdir() if root.is_dir() else []):
        try:
            at = json.loads((d / "staged.json").read_text(encoding="utf-8")).get("at") or d.stat().st_mtime
        except (OSError, ValueError):
            at = d.stat().st_mtime
        if t - float(at) > keep:
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return gone


def add_zip(zip_path: Path, home: Path | None = None) -> dict:
    """(1) A zip the buyer chose by hand: their choice is the yes, so it is checked, converted and installed at once."""
    rec = _stage(Path(zip_path), home)
    return confirm(rec["token"], home)


def stage_sale(url: str, home: Path | None = None, *, opener=None, allowed_hosts=("store.zoeyos.dev",)) -> dict:
    """(1b) The Store's Export: the sale download (store.zoeyos.dev only, crissa_import.acceptable_sale_url) fetched,
    checked and converted, then HELD until the buyer answers "Install <name>?" (confirm / discard). Never worn."""
    import crissa_import as door
    if not door.acceptable_sale_url(url, allowed_hosts):
        raise Refused("that address is not a pack from the store")
    if opener is not None:
        status, _filename, chunks = opener(url)
    else:
        # a whole agent (CRISSA_SKIN_AGENT_PACK) may be past the loop-pack door's cap: half this disk's room (the zip,
        # then its unpacked folder) is the most it may bring
        lim = max(door.MAX_BYTES, room(home) // 2) if agent_enabled() else door.MAX_BYTES
        status, _filename, chunks = door._open_sale(url, lim)
    if status != 200:
        raise Refused("the store did not hand back the pack (the link may have expired)")
    part = staging_root(home) / (".fetch-%s.zip" % uuid.uuid4().hex)
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        with part.open("wb") as f:
            for chunk in chunks:
                f.write(chunk)
        rec = _stage(part, home)
    finally:
        part.unlink(missing_ok=True)
    return {"ok": True, "token": rec["token"], "name": rec["name"], "key": rec["key"], "clips": rec["clips"],
            "ask": "Install %s?" % rec["name"]}
