"""ADD A LIBRARY: a pack the buyer bought becomes a skin they can choose (the User 2026-10-31: "say a user buys one, how
do THEY choose which Crissa to run that day?", "is there a path to install the full libraries into theirs from Store?";
crissa-6a, MP store "a buyer's choice of skin", option A).

What the buyer downloads is a crissa-loop-pack/1 (lib/imagine_package/pack_sales.pack_zip: `<id>/pack.json`, `files/`,
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
PACK_SCHEMA = "crissa-loop-pack/1"
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
    if not isinstance(meta, dict) or meta.get("schema") != PACK_SCHEMA:
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
    if p["dials_add"]:
        at = time.time()
        sets = {k: {"dials": v, "at": at, "by": "pack %s" % meta["id"]} for k, v in p["dials_add"].items()}
        (lib / door.CLIP_DIALS_REL).write_text(json.dumps({"clips": sets}, indent=1) + "\n", encoding="utf-8")
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
    if loops:
        # the player boots, talks and rests on the pack's loop when the pack names none (lib/avatar reads these)
        for k in ("listen_loop", "boot_loop", "rest_loop"):
            lj.setdefault(k, loops[0])
    (lib / "library.json").write_text(json.dumps(lj, indent=1) + "\n", encoding="utf-8")
    return {"clips": len(p["clips_add"]), "files": len(p["add_files"]), "map_entries": len(p["map_add"]),
            "hubs": sorted(p["hubs_add"]), "live": inv["kit"]["keys"], "size": size, "loops": loops}


def _stage(zip_path: Path, home: Path | None) -> dict:
    """Steps 1-4 into <libraries>/.skin-staging/<token>/: answers {token, key, name, ...}; nothing listed yet."""
    token = uuid.uuid4().hex
    work = staging_root(home) / token
    work.mkdir(parents=True)
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
        if (_libraries_dir(home) / key).exists():
            raise Refused("%s is already installed" % label_of(meta))
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
        out = hub.install(work / "lib", rec["key"], home, order=order, name=rec["name"])
    except hub.Refused as e:
        raise Refused(str(e)) from e
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res = {"ok": True, "key": rec["key"], "name": rec["name"], "clips": rec["clips"], "files": out["files"],
           "worn": False, "note": "%s is in Choose skin. Pick it there to wear it." % rec["name"]}
    import crissa_avatar
    rule = crissa_avatar.offer_rule()
    row = {"key": rec["key"], "dir": out["dir"], "label": rec["name"]}
    if not crissa_avatar.offered(row, rule):
        # CRISSA LOOKS ONLY IN 1.0.5: it lands in its folder, kept for the agent switcher, and says so plainly
        res.update(offered=False, note="%s is installed, but not offered yet. %s" % (rec["name"], rule["note"]))
    return res


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
    status, _filename, chunks = (opener or door._open_sale)(url)
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
