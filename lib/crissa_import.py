"""The import path: a returned loop pack adds to the user's library (2026-10-01).

WHY. The Composer site returns mouth-stamp deliverables (films, playlists, tiles and
their map entries), and the store sells hub loops and flourishes. Until now a pack
was whatever the export built: nothing could be ADDED to a library on the user's
machine. This is the one door.

A pack is a folder (or a .zip of it) dropped in `<crissa home>/packs/incoming/<id>/`:

    pack.json    {"schema": "zoeyos-loop-pack/1", "id", "face", "built_from",
                  "files": {"<path under the face's pose dir>": "<sha256>"},
                  "map_entries": "map-entries.json",      (optional)
                  "inventory": "inventory-rows.json"}     (optional)
    files/…      the files named in `files`
    map-entries.json     a list of phoneme-map entries (each names its `tile`)
    inventory-rows.json  {"clips": {key: row}, "pools": {pool: [key, …]}}
    dials.json           {"schema": "zoeyos-pack-dials/1", "sets": {loop key: {dial: value}}}  (optional)

A pack's TUNE (2026-10-07) is each of its loops' starting set, added to the library's per-clip sets
(mouth-dials.clips.json) for the pack's NEW clips only - never a key the library already has a set for,
never the library's own lock. The user tunes from there. CRISSA_IMPORT_DIALS=0 leaves them out.

IT ONLY ADDS. Every rule below refuses the WHOLE pack, before anything is written:
  - a path that leaves the pose dir, a symlink, a suffix that is not a film, a
    picture or a sidecar; the library's own inventory, phoneme map and tune files
    (those are merged from the two documents above, or are the user's own);
  - a file whose hash is not the one pack.json states, or a file it does not list;
  - a file, map entry or clip that already exists and DIFFERS (a tile is never
    overwritten; an identical one is skipped);
  - a clip whose start or end is not a hub the library already has.

After the files land, the phoneme map and the inventory are each written once
(temp file + os.replace), with the copies they replaced kept beside a receipt in
`packs/installed/<id>/`. New playlists and tiles need a reload; a new inventory row
needs the player restarted (inventory is read once at boot): the result says which.

OFF by default: `CRISSA_IMPORT=1` turns it on, and never on the house's own install
(its library is built by intake). Off, the player has no import route at all.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

import crissa_home

SCHEMA = "zoeyos-loop-pack/1"
# the wire spelling the store writes, and the kit rename's spelling 1.0.6 tests wrote (crissa-6a, MP 11.ak 2026-10-31)
SCHEMAS = (SCHEMA, "crissa-loop-pack/1")
SUFFIXES = (".mp4", ".png", ".jpg", ".jpeg", ".json")
MAP_REL = "visemes/phoneme-map/map.json"
INVENTORY_REL = "inventory.json"
# never taken as a file: merged from the pack's two documents, or the user's own tune
RESERVED = (INVENTORY_REL, MAP_REL, "mouth-dials.json", "mouth-dials.clips.json", "mouth-presets.json")
CLIP_DIALS_REL = "mouth-dials.clips.json"
DIALS_SCHEMA = "zoeyos-pack-dials/1"
DIALS_SCHEMAS = (DIALS_SCHEMA, "crissa-pack-dials/1")
MAX_FILES = 5000
MAX_BYTES = 2 * 1024 ** 3


class ImportRefused(ValueError):
    """Why a pack is not taken. The message is one plain line for the page."""


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_IMPORT", "0")).strip().lower() not in ("1", "on", "true", "yes"):
        return False
    return not crissa_home.is_house()


def tile_paths(env: dict | None = None) -> bool:
    """CRISSA_IMPORT_TILE_PATHS=1 (OFF by default): a map entry may spell its tile from the library's
    root (`visemes/phoneme-map/tiles/x.png`), which is how a library's own map and its playlists spell
    it. Off, only the older spelling relative to the map's folder (`tiles/x.png`) is taken."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IMPORT_TILE_PATHS", "0")).strip().lower() in ("1", "on", "true", "yes")


def root(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / "packs"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(rel: str) -> str:
    """A path under the pose dir, as given, or ImportRefused."""
    s = str(rel or "")
    parts = s.split("/")
    if (not s or s.startswith(("/", "~")) or "\\" in s or ":" in s
            or any(p in ("", ".", "..") for p in parts)):
        raise ImportRefused("a path that leaves the library: %r" % s[:80])
    if not s.lower().endswith(SUFFIXES):
        raise ImportRefused("not a film, a picture or a sidecar: %r" % s[:80])
    if s in RESERVED:
        raise ImportRefused("a pack never replaces %s" % s)
    return s


def _unzip(arc: Path, dest: Path) -> None:
    """A dropped <id>.zip becomes the folder <id>/. Members are checked before any is written."""
    with zipfile.ZipFile(arc) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES + 8 or sum(i.file_size for i in infos) > MAX_BYTES:
            raise ImportRefused("the archive is larger than a pack may be")
        for i in infos:
            name = i.filename
            if (name.startswith(("/", "~")) or "\\" in name or ".." in name.split("/")
                    or (i.external_attr >> 16) & 0o170000 == 0o120000):
                raise ImportRefused("the archive holds a path that leaves its folder")
        tmp = dest.with_name(dest.name + ".unpacking")
        if tmp.exists():
            shutil.rmtree(tmp)
        z.extractall(tmp)
    os.replace(tmp, dest)


def incoming(home: Path | None = None) -> list[str]:
    """Pack ids waiting in the drop folder (a .zip counts under its stem)."""
    d = root(home) / "incoming"
    try:
        names = sorted(d.iterdir())
    except OSError:
        return []
    out = []
    for p in names:
        if p.is_dir() and not p.name.endswith(".unpacking"):
            out.append(p.name)
        elif p.is_file() and p.suffix == ".zip" and p.stem not in out:
            out.append(p.stem)
    return sorted(set(out))


def _pack_dir(pack_id: str, home: Path | None = None) -> Path:
    if not pack_id or "/" in pack_id or pack_id.startswith(".") or "\\" in pack_id:
        raise ImportRefused("no such pack")
    d = root(home) / "incoming" / pack_id
    arc = d.with_name(pack_id + ".zip")
    if not d.is_dir() and arc.is_file():
        try:
            _unzip(arc, d)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ImportRefused("the archive cannot be read: %s" % exc) from exc
    if not d.is_dir():
        raise ImportRefused("no such pack")
    return d


def _load_json(p: Path, what: str):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImportRefused("%s cannot be read" % what) from exc


def plan(pack_id: str, pose_dir: Path, home: Path | None = None, *, src: Path | None = None,
         bring_hubs: bool = False) -> dict:
    """Everything the import would do, or ImportRefused. Writes nothing in the library.

    `src` (a pack folder, instead of the drop folder) and `bring_hubs` are for an AUTHORING install,
    never the player's own door: with `bring_hubs` the pack's `hubs` (each plate among its files) and
    pools the library does not have yet are part of the plan (`hubs_add`, `pools_new`)."""
    src = Path(src) if src is not None else _pack_dir(pack_id, home)
    meta = _load_json(src / "pack.json", "pack.json")
    if not isinstance(meta, dict) or meta.get("schema") not in SCHEMAS:
        raise ImportRefused("not a %s pack" % SCHEMA)
    inv_path = pose_dir / INVENTORY_REL
    inv = _load_json(inv_path, "the library's inventory")
    face = str(meta.get("face") or "")
    if not face or face != str(inv.get("agent") or ""):
        raise ImportRefused("this pack is for %r; this library is %r" % (face, inv.get("agent")))

    listed = meta.get("files")
    if not isinstance(listed, dict) or len(listed) > MAX_FILES:
        raise ImportRefused("pack.json lists no files, or too many")
    files_root = src / "files"
    on_disk = set()
    if files_root.is_dir():
        for p in files_root.rglob("*"):
            if p.is_symlink():
                raise ImportRefused("the pack holds a link: %s" % p.name)
            if p.is_file():
                on_disk.add(p.relative_to(files_root).as_posix())
    extra = on_disk - set(listed)
    if extra:
        raise ImportRefused("a file pack.json does not list: %s" % sorted(extra)[0])

    add_files, same_files, total = [], [], 0
    for rel, want in sorted(listed.items()):
        rel = _safe_rel(rel)
        f = files_root / rel
        if not f.is_file():
            raise ImportRefused("pack.json lists a file that is missing: %s" % rel)
        total += f.stat().st_size
        if total > MAX_BYTES:
            raise ImportRefused("the pack is larger than a pack may be")
        got = _sha(f)
        if got != str(want).lower():
            raise ImportRefused("a file is not the one pack.json states: %s" % rel)
        dest = pose_dir / rel
        if dest.is_symlink():
            raise ImportRefused("the library holds a link at %s" % rel)
        if dest.exists():
            if not dest.is_file() or _sha(dest) != got:
                raise ImportRefused("the library already has a different %s" % rel)
            same_files.append(rel)
        else:
            add_files.append(rel)

    # the phoneme map: entries are keyed by the tile they place
    map_add = []
    if meta.get("map_entries"):
        entries = _load_json(src / str(meta["map_entries"]), "the pack's map entries")
        if not isinstance(entries, list):
            raise ImportRefused("the pack's map entries are not a list")
        cur = _load_json(pose_dir / MAP_REL, "the library's phoneme map")
        have = {str(e.get("tile")): e for e in cur.get("entries") or [] if isinstance(e, dict)}
        map_dir = (pose_dir / MAP_REL).parent
        for e in entries:
            tile = str(e.get("tile") or "") if isinstance(e, dict) else ""
            if not tile:
                raise ImportRefused("a map entry names no tile")
            if tile.startswith(("/", "~")) or "\\" in tile or ".." in tile.split("/"):
                raise ImportRefused("a map entry's tile leaves the library: %s" % tile[:80])
            try:
                tile_rel = (map_dir / tile).relative_to(pose_dir).as_posix()
            except ValueError as exc:
                raise ImportRefused("a map entry's tile leaves the library: %s" % tile[:80]) from exc
            if tile_paths() and tile_rel and tile.startswith(MAP_REL.rsplit("/", 1)[0] + "/"):
                tile_rel = tile        # spelled from the library's root, as the library's own map spells it
            if not tile_rel or (tile_rel not in listed and not (pose_dir / tile_rel).is_file()):
                raise ImportRefused("a map entry's tile is not in the pack or the library: %s" % tile[:80])
            if tile in have:
                if have[tile] != e:
                    raise ImportRefused("the library already places %s differently" % tile[:80])
                continue
            map_add.append(e)

    # the inventory: new clips on hubs the library already has
    clips_add, pools_add, pools_new, hubs_add = {}, {}, {}, {}
    if bring_hubs:
        for hub, plate in sorted((meta.get("hubs") or {}).items() if isinstance(meta.get("hubs"), dict) else []):
            cur_plate = (inv.get("hubs") or {}).get(hub)
            if cur_plate is None:
                if str(plate) not in listed:
                    raise ImportRefused("hub %s has no plate in the pack" % hub)
                hubs_add[str(hub)] = str(plate)
            elif cur_plate != plate:
                raise ImportRefused("the library already has a different hub %s" % hub)
    if meta.get("inventory"):
        rows = _load_json(src / str(meta["inventory"]), "the pack's inventory rows")
        if not isinstance(rows, dict) or set(rows) - {"clips", "pools"}:
            raise ImportRefused("inventory rows may add clips and pool members, nothing else")
        hubs = set(inv.get("hubs") or {}) | set(hubs_add) | {str(v.get("hub")) for v in (inv.get("child_paths") or {}).values()
                                                              if isinstance(v, dict)}
        cur_clips = inv.get("clips") or {}
        for key, row in sorted((rows.get("clips") or {}).items()):
            if not isinstance(row, dict) or not row.get("file"):
                raise ImportRefused("clip %s has no film" % key)
            if key in cur_clips:
                if cur_clips[key] != row:
                    raise ImportRefused("the library already has a different clip %s" % key)
                continue
            for end in ("start", "end"):
                if row.get(end) is not None and str(row.get(end)) not in hubs:
                    raise ImportRefused("clip %s %ss on a hub this library does not have" % (key, end))
            file_rel = _safe_rel(str(row["file"]))
            if file_rel not in listed and not (pose_dir / file_rel).is_file():
                raise ImportRefused("clip %s names a film that is not in the pack" % key)
            row = dict(row, file=file_rel)
            clips_add[key] = row
        known = set(cur_clips) | set(clips_add)
        for pool, keys in sorted((rows.get("pools") or {}).items()):
            if pool not in (inv.get("pools") or {}):
                if not bring_hubs:
                    raise ImportRefused("the library has no pool %s" % pool)
                if any(k not in known for k in keys):
                    raise ImportRefused("pool %s names a clip nobody has" % pool)
                pools_new[pool] = list(keys)
                continue
            new = [k for k in keys if k not in inv["pools"][pool]]
            if any(k not in known for k in new):
                raise ImportRefused("pool %s names a clip nobody has" % pool)
            if new:
                pools_add[pool] = new

    # the pack's tune: a starting set for each of its NEW loops that the library has no set for yet
    dials_add = {}
    if meta.get("dials") and os.environ.get("CRISSA_IMPORT_DIALS", "1").strip() not in ("0", "off", "false"):
        d = _load_json(src / str(meta["dials"]), "the pack's dials")
        sets = d.get("sets") if isinstance(d, dict) and d.get("schema") in DIALS_SCHEMAS else None
        if not isinstance(sets, dict):
            raise ImportRefused("the pack's dials are not a %s" % DIALS_SCHEMA)
        cur = (pose_dir / CLIP_DIALS_REL)
        have = (_load_json(cur, "the library's clip sets").get("clips") or {}) if cur.is_file() else {}
        for key, dials in sorted(sets.items()):
            if not isinstance(dials, dict) or not all(isinstance(v, (bool, int, float, str)) for v in dials.values()):
                raise ImportRefused("the pack's dials for %s are not plain values" % key)
            if key in clips_add and key not in have:
                dials_add[key] = dials

    return {"id": pack_id, "face": face, "built_from": meta.get("built_from"), "src": str(src), "dials_add": dials_add,
            "add_files": add_files, "same_files": same_files, "map_add": map_add,
            "clips_add": clips_add, "pools_add": pools_add, "bytes": total,
            "hubs_add": hubs_add, "pools_new": pools_new}


def _write_json(dest: Path, data) -> None:
    tmp = dest.with_name(dest.name + ".importing")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, dest)


def apply(pack_id: str, pose_dir: Path, home: Path | None = None, now: float | None = None) -> dict:
    """Verify, then add. Returns what landed and whether a reload or a restart shows it."""
    p = plan(pack_id, pose_dir, home)
    src = Path(p["src"])
    kept = root(home) / "installed" / pack_id
    kept.mkdir(parents=True, exist_ok=True)
    files_root = src / "files"
    for rel in p["add_files"]:
        dest = pose_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".importing")
        shutil.copyfile(files_root / rel, tmp)
        os.replace(tmp, dest)
    if p["map_add"]:
        mp = pose_dir / MAP_REL
        shutil.copyfile(mp, kept / "map.before.json")
        cur = json.loads(mp.read_text(encoding="utf-8"))
        cur["entries"] = list(cur.get("entries") or []) + p["map_add"]
        if isinstance(cur.get("n"), int):
            cur["n"] = len(cur["entries"])
        _write_json(mp, cur)
    if p["clips_add"] or p["pools_add"]:
        ip = pose_dir / INVENTORY_REL
        shutil.copyfile(ip, kept / "inventory.before.json")
        inv = json.loads(ip.read_text(encoding="utf-8"))
        inv.setdefault("clips", {}).update(p["clips_add"])
        for pool, keys in p["pools_add"].items():
            inv["pools"][pool] = list(inv["pools"][pool]) + keys
        _write_json(ip, inv)
    if p["dials_add"]:
        cp = pose_dir / CLIP_DIALS_REL
        cur = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {"clips": {}}
        if cp.is_file():
            shutil.copyfile(cp, kept / "clip-dials.before.json")
        at = time.time() if now is None else now
        for key, dials in p["dials_add"].items():
            cur.setdefault("clips", {}).setdefault(key, {"dials": dials, "at": at, "by": "pack %s" % pack_id})
        _write_json(cp, cur)
    out = {"ok": True, "id": pack_id, "face": p["face"], "files_added": len(p["add_files"]),
           "dial_sets_added": sorted(p["dials_add"]),
           "files_already_there": len(p["same_files"]), "map_entries_added": len(p["map_add"]),
           "clips_added": sorted(p["clips_add"]), "restart_needed": bool(p["clips_add"] or p["pools_add"]),
           "at": time.time() if now is None else now}
    _write_json(kept / "receipt.json", dict(out, add_files=p["add_files"], pools_added=p["pools_add"],
                                            built_from=p["built_from"]))
    done = root(home) / "done"
    done.mkdir(parents=True, exist_ok=True)
    target = done / pack_id
    if target.exists():
        target = done / ("%s.%d" % (pack_id, int(out["at"])))
    os.replace(src, target)
    if p["clips_add"]:                             # round 14: the Wall builds the new clips for this screen at idle
        try:
            import crissa_build
            crissa_build.request_prefill(sorted(p["clips_add"]), home=root(home).parent)
        except Exception:  # noqa: BLE001 - the build is the Wall's; the import never fails on it
            pass
    arc = src.with_name(pack_id + ".zip")          # a pack that arrived as an archive: the archive leaves the
    if arc.is_file():                              # folder too, or the pack would still read as waiting
        os.replace(arc, target.with_name(target.name + ".zip"))
    return out


UPLOAD_PREFIX = ".upload-"
_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def upload_part(home: Path | None = None) -> Path:
    """Where the player streams an upload before it is checked: a dot-file in the drop folder (incoming()
    lists no dot-file and no non-.zip file, so a half-written upload is never offered)."""
    d = root(home) / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d / ("%s%d-%d.part" % (UPLOAD_PREFIX, os.getpid(), time.time_ns()))


def save_upload(part: Path, filename: str, pose_dir: Path, home: Path | None = None) -> dict:
    """THE UPLOAD (the User 2026-10-02): a loop file the user bought and downloaded themselves, chosen on the
    Connect page and streamed here by the player. Local only: no outbound call, no library sync. It becomes
    <id>.zip in the drop folder and the existing Add flow takes over - but only after the same check Add runs
    (plan): anything that is not a pack this library can take is removed again, with a plain reason.
    `part` is the streamed file; it is always consumed (moved in, or deleted)."""
    try:
        name = Path(str(filename or "")).name
        if not name.lower().endswith(".zip"):
            raise ImportRefused("choose a loop file ending in .zip")
        pid = name[:-4].strip()
        if not pid or pid.startswith(".") or len(pid) > 80 or not set(pid) <= _ID_OK:
            raise ImportRefused("rename the file to letters, numbers, dots, dashes or underscores (.zip)")
        if not zipfile.is_zipfile(part):
            raise ImportRefused("that file is not a loop pack: it is not a .zip archive")
        d = root(home) / "incoming"
        if (d / pid).exists() or (d / (pid + ".zip")).exists():
            raise ImportRefused("a pack named %s is already waiting; add or remove that one first" % pid)
        arc = d / (pid + ".zip")
        os.replace(part, arc)
    finally:
        if part.exists():
            part.unlink()
    try:
        pl = plan(pid, pose_dir, home)
    except ImportRefused as exc:
        arc.unlink(missing_ok=True)
        unpacked = d / pid
        if unpacked.is_dir():
            shutil.rmtree(unpacked, ignore_errors=True)
        raise ImportRefused("that file is not a loop pack this library can take: %s" % exc) from exc
    return {"ok": True, "id": pid, "files": len(pl["add_files"]), "clips": sorted(pl["clips_add"]),
            "map_entries": len(pl["map_add"])}


def status(pose_dir: Path | None = None, home: Path | None = None) -> dict:
    """For the page: is it on, where the drop folder is, what waits there."""
    on = enabled()
    out = {"on": on, "folder": str(root(home) / "incoming"), "waiting": []}
    if not on:
        return out
    for pid in incoming(home):
        row = {"id": pid}
        if pose_dir is not None:
            try:
                pl = plan(pid, pose_dir, home)
                row.update(ok=True, files=len(pl["add_files"]), clips=sorted(pl["clips_add"]),
                           map_entries=len(pl["map_add"]))
            except ImportRefused as exc:
                row.update(ok=False, why=str(exc))
        out["waiting"].append(row)
    return out


_PLAY_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SALE_PATH = re.compile(r"^/imagine/store/sale/sale-[a-z0-9-]+/download$")


def acceptable_sale_url(url: str, allowed_hosts=("store.zoeyos.dev",)) -> bool:
    """A pack download on the store, and nowhere else. Loopback is allowed only when the caller names it."""
    from urllib.parse import parse_qs, urlparse
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if host not in {str(h).lower() for h in allowed_hosts}:
        return False
    if host in ("127.0.0.1", "localhost", "::1"):
        if u.scheme != "http":
            return False
    elif u.scheme != "https":
        return False
    if u.username or u.password or u.fragment or not _SALE_PATH.match(u.path or ""):
        return False
    q = parse_qs(u.query)
    return list(q) == ["k"] and bool(q.get("k") and q["k"][0])


def _play_path(home: Path | None) -> Path:
    return root(home) / "director-play.json"


def remember_play(key: str, pack_id: str, home: Path | None = None) -> None:
    if not _PLAY_KEY.fullmatch(str(key or "")):
        return
    _write_json(_play_path(home), {"key": key, "pack": pack_id})


def pending_play(home: Path | None = None) -> str:
    try:
        data = json.loads(_play_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    key = str((data or {}).get("key") or "") if isinstance(data, dict) else ""
    return key if _PLAY_KEY.fullmatch(key) else ""


def clear_pending_play(home: Path | None = None) -> None:
    try:
        _play_path(home).unlink()
    except OSError:
        pass


def _chunks(resp, limit: int):
    n = 0
    while True:
        chunk = resp.read(1 << 20)
        if not chunk:
            break
        n += len(chunk)
        if n > limit:
            raise ImportRefused("that file is larger than a pack may be")
        yield chunk


# THE SALE STREAMS TO DISK (round 46, the local review): the whole download (up to MAX_BYTES) was held in memory as a
# list of chunks before the first byte was written. The chunks now go to the part file as they arrive, and the response
# closes when the last one is read or the write stops. Kill CRISSA_IMPORT_STREAM=0 (the list, round 45).
def import_stream_on() -> bool:
    return os.environ.get("CRISSA_IMPORT_STREAM", "1").strip().lower() not in ("0", "off", "false", "no")


def _streamed(resp, limit: int):
    try:
        yield from _chunks(resp, limit)
    finally:
        resp.close()


def _open_sale(url: str, limit: int = MAX_BYTES):
    """GET the sale zip. The caller has already checked the address. `limit`: the most it may bring (a whole agent
    pack, lib/crissa_skin_add, passes this disk's room)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Crissa"})
    resp = urllib.request.urlopen(req, timeout=60)  # noqa: S310  the address was checked
    streamed = False
    try:
        name = "pack.zip"
        disp = resp.headers.get("Content-Disposition") or ""
        found = re.search(r'filename="([A-Za-z0-9._-]+\.zip)"', disp)
        if found:
            name = found.group(1)
        if import_stream_on() and resp.status == 200:      # (any other answer is refused unread)
            streamed = True
            return resp.status, name, _streamed(resp, limit)
        return resp.status, name, list(_chunks(resp, limit))
    finally:
        if not streamed:
            resp.close()


def take_sale(url: str, play: str, pose_dir: Path, home: Path | None = None, *,
              opener=None, allowed_hosts=("store.zoeyos.dev",)) -> dict:
    """Fetch one paid pack from the store, check it, and add it. `play` is remembered when it is a
    clip this add brought in (or the library already had the pack). The address is the sale download
    and nothing else. The house never reaches this: enabled() is the route's gate."""
    if not acceptable_sale_url(url, allowed_hosts):
        raise ImportRefused("that address is not a pack from the store")
    play_key = str(play or "")
    if play_key and not _PLAY_KEY.fullmatch(play_key):
        raise ImportRefused("that loop cannot be played")
    status, filename, chunks = (opener or _open_sale)(url)
    if status != 200:
        raise ImportRefused("the store did not hand back the pack")
    part = upload_part(home)
    try:
        with part.open("wb") as f:
            for chunk in chunks:
                f.write(chunk)
        saved = save_upload(part, filename, pose_dir, home)
    except ImportRefused:
        if part.exists():
            part.unlink()
        raise
    except Exception:
        if part.exists():
            part.unlink()
        raise
    out = apply(saved["id"], pose_dir, home)
    played = ""
    if play_key and (play_key in out["clips_added"] or not out["clips_added"]):
        remember_play(play_key, out["id"], home)
        played = play_key
    out["play"] = played
    return out
