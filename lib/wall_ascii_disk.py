"""The ASCII cache's DISK tier (round 13, 2026-10-12; the User, on .101's first lap: "maybe we consider the length of
these clips and that they run sequentially but not in order").

The memory tier (lib/wall_ascii_cache.py) is a process's own: every start, and every framing, density or zoom change,
began empty, and at 3440x1440 a picture is ~5 MB, so 4 GB held ~800 pictures (~33 s of film) of a 13,031-frame library.
This tier keeps each clip frame's picture on disk, ONCE per (framing, clip, frame), and reads it back for ~2 ms where a
fresh render is ~27-38 ms (measured 2026-10-12, 3440x1440 d0.859: raw read + adler32 1.3 ms warm, 2.0 ms out of the page
cache; zlib level 1 would store 1 MB instead of 4.95 MB but costs 7.2 ms to read and 22 ms to write, so it is raw).

Exact: an entry is written from a picture the cache drew (the fresh render's bytes), read back under an adler32 of its
payload (a mismatch deletes the file and reads as a miss) and, like every answer, spot-checked against a fresh render
(wall_ascii_cache._checked). Its directory names the FRAMING and the CODE that drew it (a fingerprint of the render, the
cache, the frame and this module, Pillow and numpy), so a new framing or a new kit is a new directory, never an old
picture.

Layout: <state home>/cache/ascii/<framing>/<clip>/<frame>-<jpeg digest>.pic, one file a picture: b"WAC1", u32 header
length, a JSON header (w, h, rows, cols, the glow input's size, the JPEG's digest, her mouth box, adler32, lengths),
then the glyph grid, the glow input, the source JPEG and the coverage. The source JPEG rides along so a new framing can
be filled at idle from frames already seen (prefill()).

Budget: HOUSE_WALL_ASCII_DISK_GB, else min(10 % of free disk, 100 GB), and never below 20 GB left free (a fifth of
.101's 1.7 TB free would be 340 GB; 100 GB holds Crissa's whole 13,031-frame library at 3440x1440 (65 GB) with room for
a second framing). Over budget, a look drawn by another code print goes first (dead: never servable), then another
framing's directory (oldest first), then whole clips, least recently played first, never the clip on the glass; the
pre-fill makes the same room before it stops on the budget (2026-10-13, MAKE_ROOM). Each live Wall names the look on its
glass in <root>/.live-<pid>.json, and no one's make-room takes that look.

On: HOUSE_WALL_ASCII_DISK=1|0; unset = on in a pack, off on the house (crissa_home.is_house).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

import crissa_home

ROOT = crissa_home.HOME / "cache" / "ascii"
MAGIC = b"WAC1"
_U32 = struct.Struct("<I")
GB = 1 << 30
DEFAULT_CAP_GB = 100.0
FREE_SHARE = 0.10
FLOOR_GB = 20.0
TOUCH_EVERY_S = 60.0          # a clip's directory mtime is its "last played", touched at most this often
RESCAN_S = 5.0                # a clip directory is listed again at most this often on a miss (prefill writes there)
PREFILL_EVERY_S = 600.0       # a finished pre-fill is asked again this often (a new loop, a new film, more budget)


def prefill_enabled(env=None) -> bool:
    """Idle pre-fill of the loops into a new framing (round 13). Kill: HOUSE_WALL_ASCII_PREFILL=0."""
    env = os.environ if env is None else env
    return str(env.get("HOUSE_WALL_ASCII_PREFILL", "1")).strip().lower() not in ("0", "false", "no", "off")
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_ASCII_DISK", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return not crissa_home.is_house()


# A LIVE PICTURE REPLACES A PRE-FILL ENTRY IT DOES NOT MATCH (round 32, .101 10-19: `disk=14.3/99GB hits=2 bases=1
# writes=0` after a restart, listen laps "memory 318, disk 3, fresh 60"). The pre-fill draws each clip frame with NO mouth
# painted (/v1/clip-frame); her listen loop is published with her idle mouth on every frame, so its bytes - the entry's
# key - never matched, and store() refused any frame that already had an entry: the Wall could not write back, and every
# restart drew listen again. Measured on the Crissa bench 10-19: 399 of 403 listen frames gave the same bytes on five laps
# (the other 4 vary inside one lap too), so the live key is stable: the first live picture of a frame whose entry is a
# pre-fill's (its header says `prefill`) replaces it once. Unset = on in a pack, off on the house (as the disk tier
# itself). Kill HOUSE_WALL_ASCII_DISK_REPLACE=0.
def replace_prefill_on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_ASCII_DISK_REPLACE", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return not crissa_home.is_house()


def cap_bytes(free: int, held: int, env=None) -> int:
    """The disk budget: the owner's GB, else min(10 % of what is free counting what we hold, 100 GB); and never so much
    that less than FLOOR_GB stays free."""
    env = os.environ if env is None else env
    try:
        explicit = float(env["HOUSE_WALL_ASCII_DISK_GB"]) * GB if "HOUSE_WALL_ASCII_DISK_GB" in env else None
        floor = float(env.get("HOUSE_WALL_ASCII_DISK_FLOOR_GB", FLOOR_GB)) * GB
    except ValueError:
        return 0
    cap = explicit if explicit is not None else min(FREE_SHARE * (free + held), DEFAULT_CAP_GB * GB)
    if free < floor:                                   # disk is short: give back what is short
        cap = min(cap, max(0.0, held - (floor - free)))
    return int(max(0.0, cap))


_PRINT: dict = {}


def code_print() -> str:
    """What drew a picture: the sources of the render, the cache, the frame and this module, Pillow and numpy, and
    what upsamples the glow."""
    if "p" not in _PRINT:
        h = hashlib.blake2b(digest_size=10)
        here = Path(__file__).resolve().parent
        for name in ("house_wall_ascii_render.py", "wall_ascii_cache.py", "wall_frame.py", "wall_ascii_disk.py"):
            try:
                h.update(name.encode() + b"\0" + (here / name).read_bytes())
            except OSError:
                h.update(name.encode() + b"\0-")
        try:
            import numpy
            import PIL
            h.update(("%s %s" % (PIL.__version__, numpy.__version__)).encode())
        except Exception:  # noqa: BLE001
            pass
        # what upsamples the glow (2026-10-13): OpenCV's version, or PIL under HOUSE_WALL_GLOW_CV=0 / without cv2 - the
        # two looks are 1 grey level apart, so they never share a directory (house_wall_ascii_render.glow_up)
        try:
            import house_wall_ascii_render as R
            h.update(("glow " + R.glow_method()).encode())
        except Exception:  # noqa: BLE001
            h.update(b"glow -")
        _PRINT["p"] = h.hexdigest()
    return _PRINT["p"]


def framing_dir_name(fk) -> str:
    return hashlib.blake2b(repr((code_print(), codec(), fk)).encode(), digest_size=10).hexdigest()


def clip_dir_name(key) -> str:
    return _SAFE.sub("_", str(key))[:80] or "_"


# THE DISK CODEC (round 14, measured 2026-10-12 on 20 listen pictures at 3440x1440, one free core): zstd level 1 makes
# a calm listen picture of 4.72 MB 0.76 MB (6.2x) - the whole library less so: .101's 13,031 pictures at density 5 are
# 20.9 GB, ~1.6 MB a picture, ~3.5x (round 15) - and reads it back in 1.6 ms p50 / 2.5 ms p90 (zlib level 1: 5.6x but 6.2 ms to read;
# raw 1.6-2.1 ms). Used when this Python has zstd (3.14's compression.zstd, or the zstandard wheel - the kit installs
# zstandard 0.25.0 with the Wall's packages since round 15, the User 2026-10-12 "yes to all four"); a Python without
# it writes raw. HOUSE_WALL_ASCII_DISK_CODEC = auto | zstd | raw. THE TWO FORMATS NEVER MIX (round 15): the codec is
# part of the look's directory name (framing_dir_name), so a raw cache and a zstd cache are two looks, and an entry
# whose codec is not this process's reads as a miss and is drawn again.
def _zstd():
    try:
        from compression import zstd as z            # Python 3.14
        return ("std", z)
    except ImportError:
        pass
    try:
        import zstandard as z
        return ("wheel", z)
    except ImportError:
        return None


def codec(env=None) -> str:
    env = os.environ if env is None else env
    want = str(env.get("HOUSE_WALL_ASCII_DISK_CODEC", "auto")).strip().lower()
    if want == "raw":
        return "raw"
    return "zstd-1" if _zstd() is not None else "raw"


def _squeeze(px) -> bytes:
    kind, z = _zstd()
    return z.compress(bytes(px), 1) if kind == "std" else z.ZstdCompressor(level=1).compress(bytes(px))


def _unsqueeze(data, size: int) -> bytearray:
    kind, z = _zstd()
    out = z.decompress(bytes(data)) if kind == "std" else z.ZstdDecompressor().decompress(bytes(data), max_output_size=size)
    return bytearray(out)


def pack(w, h, rows, cols, idx: bytes, small_size, small: bytes, jpeg: bytes, digest_hex: str, mouth, px,
         extra: dict | None = None):
    cd = codec()
    raw_len = len(px)
    if cd != "raw":
        px = _squeeze(px)
    payload = [bytes(idx), bytes(small), bytes(jpeg or b""), px]
    ad = 1
    for part in payload:
        ad = zlib.adler32(part, ad)
    head = {"w": w, "h": h, "rows": rows, "cols": cols, "small": list(small_size), "jpeg": digest_hex,
            "mouth": list(mouth) if mouth else None, "adler": ad,
            "lens": [len(payload[0]), len(payload[1]), len(payload[2]), len(px)], "codec": cd, "px_len": raw_len}
    head.update(extra or {})
    hdr = json.dumps(head).encode()
    return MAGIC + _U32.pack(len(hdr)) + hdr, payload


def read_header(path) -> dict | None:
    """Only an entry's header (and where its JPEG lies): what prefill() needs to choose its sources."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8 or head[:4] != MAGIC:
                return None
            n = _U32.unpack_from(head, 4)[0]
            hdr = json.loads(f.read(n))
            hdr["_jpeg_at"] = 8 + n + hdr["lens"][0] + hdr["lens"][1]
            return hdr
    except (OSError, ValueError, KeyError, struct.error):
        return None


def read_entry(path, keep_z: bool = False) -> dict | None:
    """The entry at `path` as {hdr, idx, small, jpeg, px (a memoryview)}; None for a short, foreign or damaged file.
    keep_z (round 17, the memory tier held compressed): also `zpx`, the coverage's zstd bytes as stored (None for a
    raw entry), so the memory tier holds the file's own bytes and packs nothing."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            buf = bytearray(size)
            got = f.readinto(buf)
        if got != size or buf[:4] != MAGIC:
            return None
        n = _U32.unpack_from(buf, 4)[0]
        hdr = json.loads(bytes(buf[8:8 + n]))
        mv = memoryview(buf)
        off = 8 + n
        parts = []
        for ln in hdr["lens"]:
            parts.append(mv[off:off + ln])
            off += ln
        if off != size:
            return None
        ad = 1
        for part in parts:
            ad = zlib.adler32(part, ad)
        if ad != hdr["adler"]:
            return None
        px = parts[3]
        zpx = None
        if hdr.get("codec", "raw") != codec():
            return None                             # the other format: a miss, drawn again in this one (round 15)
        if hdr.get("codec", "raw") != "raw":
            zpx = bytes(px) if keep_z else None
            px = memoryview(_unsqueeze(px, int(hdr["px_len"])))
            if len(px) != int(hdr["px_len"]):
                return None
        out = {"hdr": hdr, "idx": bytes(parts[0]), "small": bytes(parts[1]), "jpeg": bytes(parts[2]), "px": px}
        if keep_z:
            out["zpx"] = zpx
        return out
    except (OSError, ValueError, KeyError, struct.error):
        return None


def _on_air(feed: str) -> bool:
    """Someone is speaking on her film: her voice on air, or (round 16, 2026-10-13, Lila's welcome drawn at 6-8/s while
    the build ran on .101) a host film's own baked line (`film_sound`). The build yields to both."""
    if not feed:
        return False
    try:
        import urllib.request
        with urllib.request.urlopen(feed.rstrip("/") + "/v1/state", timeout=1.0) as r:
            st = json.loads(r.read())
        return bool(st.get("on_air")) or (bool(st.get("film_sound")) and FILM_SOUND_YIELDS)
    except Exception:  # noqa: BLE001 - no answer: nobody is talking that we can see
        return False


FILM_SOUND_YIELDS = str(os.environ.get("HOUSE_WALL_ASCII_PREFILL_FILM_YIELD", "1")).strip().lower() not in (
    "0", "false", "no", "off")
# THE OLD LOOK GOES ONCE THE NEW ONE IS WHOLE (round 16, 2026-10-13: .101 kept 13.8 GB of the density it left): when a
# pre-fill has drawn every clip of its look (not stopped by the budget), every other look's directory under the root
# is removed. Kill: HOUSE_WALL_ASCII_DROP_OLD_LOOKS=0.
DROP_OLD_LOOKS = str(os.environ.get("HOUSE_WALL_ASCII_DROP_OLD_LOOKS", "1")).strip().lower() not in (
    "0", "false", "no", "off")


def drop_other_looks(root, keep: str, log=print) -> int:
    """Remove every look's directory under `root` but `keep` (a look is a directory holding framing.json). Bytes freed."""
    freed = 0
    try:
        dirs = [d for d in Path(root).iterdir() if d.is_dir() and d.name != keep and (d / "framing.json").exists()]
    except OSError:
        return 0
    for d in dirs:
        n = 0
        for dp, _, fs in os.walk(d):
            for f in fs:
                try:
                    n += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        shutil.rmtree(d, ignore_errors=True)
        freed += n
        log("house-wall-ascii: ascii disk: look %s removed (%.1f GB): %s is whole" % (d.name, n / GB, keep))
    return freed


# THE PRE-FILL STOPS A LITTLE SHORT OF THE BUDGET (round 16: the house's run stopped 1.7 MB past its 40 GB, and once the
# Wall counts what the pre-fill wrote, a look over budget would make the Wall evict a clip the next run draws again)
PREFILL_FILL = 0.98


# THE PRE-FILL MAKES ROOM BEFORE IT STOPS (2026-10-13, the User: "fix the prefill budget bug"). The desk Wall's disk held
# two OLD looks drawn by a code print that is not the running one (33 GB and 95 MB, framing.json code d3959f55... where
# the Wall's is 58505428...): pictures it can never serve. After the 10:00 restart the pre-fill wrote 4,479 pictures in
# 76 s and logged "stopped: the disk budget; disk 39.2 GB" with the new look at ~7 GB. A deadlock on any disk too small
# for two looks (a pack on a small laptop disk too): the pre-fill stops at PREFILL_FILL of the budget counting every look
# on disk; the Wall's own _fit evicts another framing only once used > cap, which a 98 % stop never reaches; and
# DROP_OLD_LOOKS drops the others only once the new look is whole, which the budget prevents. Now, before the build stops
# on the budget (and in the Wall's own eviction), room is made in this order: (1) looks whose code print is not this
# Wall's (dead: never servable), (2) other framings, oldest first; never the look on the glass, and never a look another
# live Wall says it is on (its .live-<pid>.json). Each removal is logged with its size and why; the build stops on the
# budget only when nothing is left to drop. Kill: HOUSE_WALL_ASCII_PREFILL_MAKE_ROOM=0 (today's behaviour: no room made,
# the Wall evicts other framings oldest first, no live marks written).
MAKE_ROOM = str(os.environ.get("HOUSE_WALL_ASCII_PREFILL_MAKE_ROOM", "1")).strip().lower() not in (
    "0", "false", "no", "off")
LIVE_MARK = ".live-%d.json"


def _proc_start(pid: int):
    """The process's start time in clock ticks since boot (field 22 of /proc/<pid>/stat), else None: a pid and this
    number name one process, so a reused pid never passes for a live Wall."""
    try:
        with open("/proc/%d/stat" % int(pid), "rb") as f:
            raw = f.read().decode("ascii", "replace")
        return int(raw[raw.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def mark_live(root, look: str, code: str) -> None:
    """Say which look this process is on, so a watcher outside it (another Wall's make-room, the house's cache rules)
    never takes the look on its glass: <root>/.live-<pid>.json. Marks of processes that are gone are removed here."""
    root = Path(root)
    me = os.getpid()
    if _proc_start(me) is None:                        # no /proc here: a mark could never be checked, so none is left
        return
    try:
        for p in root.glob(".live-*.json"):
            try:
                pid = int(p.name[6:-5])
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if pid != me and _proc_start(pid) != rec.get("start"):
                p.unlink(missing_ok=True)
        tmp = root / (".live-%d.tmp" % me)
        tmp.write_text(json.dumps({"pid": me, "start": _proc_start(me), "look": look, "code": code,
                                   "at": time.time()}))
        os.replace(tmp, root / (LIVE_MARK % me))
    except OSError:
        pass


def live_looks(root, but: int | None = None) -> dict:
    """{look: code} of every live process's mark under `root` (pid alive with the same start), but `but`'s own."""
    out: dict = {}
    for p in Path(root).glob(".live-*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            pid = int(rec["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid != but and rec.get("start") is not None and _proc_start(pid) == rec.get("start"):
            out[str(rec.get("look"))] = str(rec.get("code") or "")
    return out


# A KIT UPDATE LEAVES ITS OLD LOOKS (the User 2026-10-16, .101's "keep caches trimmed": 17 GB of ascii on disk after a
# week of rounds). A look is drawn by one code print; a newer kit never reads an older print's pictures, so they sit
# until the 100 GB cap's make-room reaches them. Once per process, in a pack only (the house's janitor has its own
# report-only rule), a look whose code is not this Wall's, that no live Wall marks and that nobody touched for
# TRIM_IDLE_S is removed, on a thread. Kill CRISSA_ASCII_TRIM_OLD=0.
TRIM_IDLE_S = 2 * 3600


def trim_old_looks(root, own_code: str, now: float | None = None, idle_s: float = TRIM_IDLE_S, log=None) -> list:
    """Remove the dead looks under root; returns the names removed."""
    import shutil
    root = Path(root)
    now = time.time() if now is None else float(now)
    gone: list = []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return gone
    marked = live_looks(root, but=os.getpid())
    for d in dirs:
        code = look_code(d)
        if code is None or code == own_code or d.name in marked or code in marked.values():
            continue
        if now - _touched(d) < idle_s:
            continue
        try:
            shutil.rmtree(d)
            gone.append(d.name)
        except OSError:
            continue
    if gone and log:
        log("ascii disk: trimmed %d look(s) of an older code print" % len(gone))
    return gone


def trim_old_on(env=None) -> bool:
    env = os.environ if env is None else env
    if str(env.get("CRISSA_ASCII_TRIM_OLD", "1")).strip().lower() in ("0", "false", "off", "no"):
        return False
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001 - unsure = the house's behaviour (nothing removed)
        return False


def look_code(d) -> str | None:
    """The code print a look was drawn by (its framing.json), None for a directory that is not a look."""
    try:
        return str(json.loads((Path(d) / "framing.json").read_text(encoding="utf-8")).get("code") or "")
    except (OSError, ValueError, AttributeError):
        return None


def _touched(d: Path) -> float:
    """When a look was last used: the newest mtime of the look, its framing/progress files and its clip directories (the
    Wall touches the clip on the glass every TOUCH_EVERY_S; the build writes into them)."""
    best = 0.0
    for p in [d, d / "framing.json", d / "progress.json"]:
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            pass
    try:
        for e in os.scandir(d):
            if e.is_dir(follow_symlinks=False):
                try:
                    best = max(best, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return best


def _du(d) -> int:
    n = 0
    for dp, _, fs in os.walk(d):
        for f in fs:
            try:
                n += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return n


def room_order(root, keep: str, code: str, spare=()) -> list:
    """The looks that may go to make room, in the order they go: [(path, why, last used)]. Dead looks (another code
    print) first, then other framings of this code, each oldest first. Never `keep` (the look on the glass), never a
    look in `spare` (one another live Wall is on), never a directory without framing.json (not a look)."""
    dead, other = [], []
    try:
        dirs = [d for d in Path(root).iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return []
    for d in dirs:
        if d.name == keep or d.name in spare:
            continue
        c = look_code(d)
        if c is None:
            continue
        t = _touched(d)
        if c != code:
            dead.append((d, "drawn by code %s, this Wall's is %s: never servable" % (c[:8] or "?", code[:8]), t))
        else:
            other.append((d, "another framing, the oldest (last used %s)"
                          % time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), t))
    return sorted(dead, key=lambda r: r[2]) + sorted(other, key=lambda r: r[2])


def make_room(root, keep: str, need: float, code: str, log=print) -> int:
    """Remove looks in room_order until `need` bytes are free; the bytes freed (0 when nothing is left to drop)."""
    freed = 0
    for d, why, _t in room_order(root, keep, code, spare=live_looks(root, but=os.getpid())):
        if freed >= need:
            break
        n = _du(d)
        shutil.rmtree(d, ignore_errors=True)
        if d.exists():
            log("house-wall-ascii: ascii disk: look %s could not be removed (%.2f GB): %s" % (d.name, n / GB, why))
            continue
        freed += n
        log("house-wall-ascii: ascii disk: look %s removed (%.2f GB) to make room for %s: %s" % (d.name, n / GB, keep, why))
    return freed


def _room_log(m: str) -> None:
    print(m, file=sys.stderr, flush=True)


RECOUNT_EVERY_S = 30.0
_RECOUNT = {"at": 0.0}


def room_for_one(spec: dict, used, cap, per: float, log=_room_log, clock=time.monotonic) -> bool:
    """A pre-fill worker, under the shared lock: is there room for one more picture, made if it can be? Room first: a
    dead look, then another framing (make_room), one worker at a time; when nothing was left to drop, what is on disk is
    counted again (at most every RECOUNT_EVERY_S a worker): the Wall's own eviction or a hand may have freed it since the
    build counted (2026-10-13: the dead looks were removed by hand at 10:02, and the build that had counted them stayed
    stopped on the budget). The build stops on the budget only when this says no. Without MAKE_ROOM: today's plain test."""
    fill = cap.value * PREFILL_FILL
    if used.value + per <= fill:
        return True
    if not MAKE_ROOM:
        return False
    try:
        freed = make_room(spec["root"], Path(spec["dir"]).name, used.value + per - fill,
                          str(spec.get("code") or code_print()), log)
        used.value -= freed
        if not freed and clock() - _RECOUNT["at"] >= RECOUNT_EVERY_S:
            _RECOUNT["at"] = clock()
            on_disk = float(_du(spec["root"]))
            if on_disk < used.value:
                log("house-wall-ascii: ascii disk: the build counted %.2f GB, the disk holds %.2f GB: freed elsewhere"
                    % (used.value / GB, on_disk / GB))
                used.value = on_disk
    except Exception as e:  # noqa: BLE001 - a failed removal is the budget's stop, as before
        log("house-wall-ascii: ascii disk: making room failed (%s)" % e)
    return used.value + per <= fill


# round 16: a first Setup's build waits for the machine card's density pick (crissa_build.density_pending).
# Kill: HOUSE_WALL_ASCII_PREFILL_WAIT_PICK=0.
WAIT_FOR_PICK = str(os.environ.get("HOUSE_WALL_ASCII_PREFILL_WAIT_PICK", "1")).strip().lower() not in (
    "0", "false", "no", "off")


def wait_for_pick(home, name: str, log, *, sleep=time.sleep, clock=time.monotonic) -> str:
    """Hold a first Setup's build until its machine card has picked her density (crissa_build.density_wait): build that
    look, not this one (the Wall takes the pick live, its disk tier stops this run and starts the new look's; a skipped
    card lets this one go on). Round 17: a first boot whose walk has not started yet waits too, for at most
    crissa_build.START_GRACE_S. Returns how the wait ended: "" (no wait), "answered" or "no setup"."""
    import crissa_build
    why = crissa_build.density_wait(home)
    if not why:
        return ""
    log("house-wall-ascii: ascii disk: prefill %s: waits for Setup's machine card to pick her density%s" % (
        name, " (Setup has not started yet)" if why == "start" else ""))
    t_w = clock()
    while why:
        if why == "start" and clock() - t_w >= crissa_build.START_GRACE_S:
            log("house-wall-ascii: ascii disk: prefill %s: no Setup started in %.0f s, this look is built" % (
                name, clock() - t_w))
            return "no setup"
        sleep(1.0)
        why = crissa_build.density_wait(home)
    log("house-wall-ascii: ascii disk: prefill %s: the card answered after %.0f s" % (name, clock() - t_w))
    return "answered"


def _get_frame(player: str, key: str, i: int):
    """(jpeg, box, (key, frame, frames, role), face point) from the player's /v1/clip-frame, else None."""
    import urllib.parse
    import urllib.request
    url = "%s/v1/clip-frame?%s" % (player.rstrip("/"), urllib.parse.urlencode({"key": key, "i": int(i)}))
    try:
        with urllib.request.urlopen(url, timeout=10.0) as r:
            body = r.read()
            h = r.headers
    except Exception:  # noqa: BLE001 - no such clip, an older player, the player busy: this frame is the lap's
        return None
    box = clip = fp = None
    try:
        if h.get("X-Mouth-Box"):
            box = tuple(float(v) for v in h["X-Mouth-Box"].split(","))
        if h.get("X-Clip"):
            k_, i_, n_, r_ = h["X-Clip"].split(";")
            clip = (k_, int(i_), int(n_), r_)
        if h.get("X-Face-Point"):
            fp = tuple(float(v) for v in h["X-Face-Point"].split(","))
    except ValueError:
        return None
    return body, box, clip, fp


# LILA IS BUILT ONLY FOR WHAT SHE PLAYS ON THE WALL (round 45, via the conductor from the User, D12: a first boot's Setup
# shows her empty office on the Wall, and without krew Lila is not conversational - the question listener plays her
# fixed "just here for setup" film in her window only). About 80 lila-* rows (both inventories) were built at every
# first boot ahead of nothing. Without krew the list keeps only the IDs run's Wall clips - her lila-ids-* films and the
# plate the run hosts them on (the pack walk's `host` enter / loop / leave) - at the very end, after every Crissa clip;
# with krew installed all of hers come back, as before. Dial CRISSA_PREFILL_LILA = ids (default) | all; all is round 44.
PREFILL_LILA = ("ids", "all")


def prefill_lila(env=None) -> str:
    """ids | all; the house (Lila hosts its Render on the Wall) is always all."""
    env = os.environ if env is None else env
    try:
        import crissa_home
        if crissa_home.is_house():
            return "all"
    except Exception:  # noqa: BLE001
        pass
    v = str(env.get("CRISSA_PREFILL_LILA", "ids") or "ids").strip().lower()
    return v if v in PREFILL_LILA else "ids"


def lila_ids_keys(root: Path) -> set:
    """The clips an IDs run plays on the Wall's plate: the pack walk's host enter / loop / leave."""
    try:
        host = json.loads((root / "config" / "init-sequence-pack.json").read_text(encoding="utf-8")).get("host") or {}
    except (OSError, ValueError):
        host = {}
    return {str(host.get(k) or "") for k in ("enter", "loop", "leave")} - {""}


def krew_here(home) -> bool:
    """krew is installed for this home (lib/crissa_capability's own read); unknown = not."""
    try:
        import crissa_capability
        return bool(crissa_capability._krew(Path(home), dict(os.environ)))
    except Exception:  # noqa: BLE001
        return False


def entrance_keys() -> list:
    """Her entrance films in the order Setup tries them (lib/crissa_init._office_keys: the one-line opener's sit-hello,
    then the walk-in); [] on the house, with the walk-in off, or without the module."""
    try:
        import crissa_init
        if not crissa_init.entrance_on():
            return []
        return [str(k) for k in crissa_init._office_keys()]
    except Exception:  # noqa: BLE001 - no Setup here: no entrance to put first
        return []


# THE BUILD DRAWS WHAT PLAYS FIRST, FIRST (round 45): through Setup the Wall shows her entrance film held on its first
# frame (lib/crissa_init.office_hold) and plays it the moment Setup ends; then her boot loop, listen and work; the rest
# by how often she entered them here (plays.json). Before, the entrance sat among the "others" by name, after every
# loop. Kill HOUSE_WALL_ASCII_PREFILL_ENTRANCE_FIRST=0 (round 13's order).
def entrance_first_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("HOUSE_WALL_ASCII_PREFILL_ENTRANCE_FIRST", "1")).strip().lower() not in ("0", "false", "off", "no")


def clip_order(pack_root, plays: dict, krew: bool = False, env=None) -> list:
    """Every clip to pre-fill, in order: her entrance film (Setup's walk-in), her boot loop (the listen hub), her work hub, every other loop by how often she
    has entered it here (plays), then every other clip the same way; Crissa's before Lila's. From the pack's own
    inventories and manifest. Lila's: only the IDs run's Wall clips, last, until krew is installed (PREFILL_LILA)."""
    root = Path(pack_root)
    rows: dict = {}
    for inv_path in (root / "display" / "crissa-poses" / "inventory.json", root / "display" / "lila-poses" / "inventory.json"):
        try:
            got = json.loads(inv_path.read_text(encoding="utf-8")).get("clips") or {}
        except (OSError, ValueError):
            continue
        for k, v in got.items():
            if isinstance(v, dict) and k not in rows:
                rows[k] = v
    try:
        boot = json.loads((root / "avatars" / "crissa" / "manifest.json").read_text(encoding="utf-8")).get("boot_loop")
    except (OSError, ValueError):
        boot = None
    tail: list = []
    if prefill_lila(env) == "ids" and not krew:
        keep = lila_ids_keys(root)
        lila = [k for k in rows if k.startswith("lila")]
        tail = sorted((k for k in lila if k.startswith("lila-ids-") or k in keep),
                      key=lambda k: (k not in keep, -int(plays.get(k, 0)), k))
        for k in lila:
            rows.pop(k)
    loops = [k for k, v in rows.items() if v.get("role") == "loop"]
    first = list(dict.fromkeys(k for k in (boot, "listen", "work") if k in loops))
    if entrance_first_on(env):
        first = list(dict.fromkeys([k for k in entrance_keys() if k in rows] + first))
    by_use = lambda k: (-int(plays.get(k, 0)), k.startswith("lila"), k)       # noqa: E731
    rest_loops = sorted((k for k in loops if k not in first), key=by_use)
    others = sorted((k for k in rows if k not in loops), key=by_use)
    others = [k for k in others if k not in first]                     # the entrance is drawn once, first
    return first + rest_loops + others + tail, {k for k in first if k in loops} | set(rest_loops)


loop_order = clip_order          # round 13's name


# THE BUILD STAYS UNDER HER FILM (round 15, 2026-10-12, .101: four workers froze the picture for the whole build -
# the Wall's redraws, lib/house_wall_ascii.py DRAW_NOW): while the Wall is up the build runs on workers_up() workers;
# all workers_for() only while Setup covers the glass (crissa_build.under_cover), and it is asked again every half
# second. Two, measured (docs/checkpoints/round15-build-20261012): on two her loops were built in 48 s and the Wall
# was at full rate sooner than on one (74 s for the listen loop alone), with the screen matching the Wall either way.
# Kill: HOUSE_WALL_ASCII_PREFILL_WORKERS_UP=<n> (0 = all of them, round 14's way).
WORKERS_UP = 2


def workers_up(wide: int, env=None) -> int:
    """How many of the build's workers run while her film plays: HOUSE_WALL_ASCII_PREFILL_WORKERS_UP, else WORKERS_UP;
    0 = all."""
    env = os.environ if env is None else env
    try:
        n = int(env.get("HOUSE_WALL_ASCII_PREFILL_WORKERS_UP", str(WORKERS_UP)) or 0)
    except ValueError:
        n = WORKERS_UP
    return wide if n <= 0 else max(1, min(wide, n))


def workers_for(env=None) -> int:
    """How many pre-fill workers: HOUSE_WALL_ASCII_PREFILL_WORKERS, else a quarter of the CPUs this process may use
    (half the cores on a machine with two threads a core), 1 to 4."""
    env = os.environ if env is None else env
    try:
        n = int(env.get("HOUSE_WALL_ASCII_PREFILL_WORKERS", "0") or 0)
    except ValueError:
        n = 0
    if n <= 0:
        try:
            cpus = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            cpus = os.cpu_count() or 2
        n = cpus // 4
    return max(1, min(4, n))


# THE BUILD DECODES ITS OWN FRAMES (round 15, 2026-10-12): the pack's own player code in a process of its own
# (`house-conductor.py --clip-frames`, the library and /v1/clip-frame only), started at the build's idle priority, so a
# build never makes the live player decode the library while she plays. Its bytes are the player's (one function,
# clip_frame_reply). Kill: HOUSE_WALL_ASCII_PREFILL_SOURCE=player (round 14: the live player's route).
SOURCE_CACHE_MB = 512         # the source's own decoded clips (the build walks the library clip by clip)
SOURCE_START_S = 120.0


def own_source(pack_root, log=print, env=None):
    """(process, base URL) of a frame source of the pack's own, else (None, "") - the caller falls back to the player."""
    env = os.environ if env is None else env
    if str(env.get("HOUSE_WALL_ASCII_PREFILL_SOURCE", "own")).strip().lower() == "player":
        return None, ""
    script = Path(pack_root) / "scripts" / "house-conductor.py"
    if not script.is_file():
        return None, ""
    import select
    try:
        p = subprocess.Popen([sys.executable, str(script), "--clip-frames", "0"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=sys.stderr, cwd=str(Path(pack_root)),
                             env=dict(env, HOUSE_CONDUCTOR_CACHE_MB=str(SOURCE_CACHE_MB)))
    except OSError as e:
        log("house-wall-ascii: ascii disk: the build's own frames could not start (%s); the player's" % e)
        return None, ""
    t_end = time.monotonic() + SOURCE_START_S
    buf = b""
    while time.monotonic() < t_end and p.poll() is None:
        r, _, _ = select.select([p.stdout], [], [], 1.0)
        if not r:
            continue
        chunk = os.read(p.stdout.fileno(), 4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                port = int(json.loads(line.decode("utf-8", "replace")).get("clip_frames"))
            except (ValueError, TypeError, AttributeError):
                continue
            return p, "http://127.0.0.1:%d" % port
    try:
        p.kill()
    except OSError:
        pass
    log("house-wall-ascii: ascii disk: the build's own frames did not come up; the player's")
    return None, ""


def _quiet(feed: str, ear: str) -> bool:
    """Nobody talking: she is not on air, and the ear has no conversation open or turn in hand."""
    if _on_air(feed):
        return False
    if not ear:
        return True
    try:
        import urllib.request
        with urllib.request.urlopen(ear.rstrip("/") + "/health", timeout=1.0) as r:
            h = json.loads(r.read())
        return not (h.get("session_open") or h.get("dispatch_busy") or h.get("speaking"))
    except Exception:  # noqa: BLE001 - no ear answering: only her own voice can hold the build
        return True


from crissa_build import build_view, request_prefill   # noqa: E402,F401 - the progress the Setup page and the panel read


def _draw_one(spec, g, vinv, player, key, i, first=None):
    """One clip frame's picture into the framing's directory; its size in bytes, or 0."""
    import numpy as np
    from PIL import ImageChops
    import house_wall_ascii_render as R
    import wall_frame
    got = first if first is not None else _get_frame(player, key, i)
    if got is None:
        return 0
    jpeg, box, clip, fp = got
    mz = spec.get("mz")
    if mz is not None and mz[0] != "whole":
        return -1
    # the framing key names the frame; the clip's face point finishes it - wall_frame.frame_for's own tuple, without
    # asking this machine's switch again (the Wall already did: it is why this framing exists)
    frame = None if mz is None else ("whole", wall_frame.clamp_zoom(mz[1])) + tuple(wall_frame.face_of(fp))
    mouth = tuple(float(v) for v in box) if (spec.get("norm", True) and box) else None
    shift = tuple(spec["shift"]) if spec.get("shift") else None
    idx2d = R.ascii_idx(R._open(jpeg), g, True, 0.0, spec.get("hole_zoom"), shift, mouth=mouth,
                        frame=frame).astype(np.uint8)
    mask = R.mask_from_idx(idx2d, g)
    small = R.glow_small(mask, g)
    mask = ImageChops.screen(mask, R.glow_from_small(small, g))
    cov = ImageChops.multiply(mask, vinv)
    ekey = hashlib.blake2b(hashlib.blake2b(jpeg, digest_size=16).digest() + repr((mouth, frame)).encode(),
                           digest_size=12).hexdigest()
    h_, payload = pack(g.W, g.H, g.ROWS, g.COLS, idx2d.tobytes(), small.size, small.tobytes(), jpeg, ekey, mouth,
                       cov.tobytes(), {"frame": list(frame) if frame else None,
                                       "role": clip[3] if clip else "", "density": g.density, "clip": key,
                                       "prefill": True})
    d = Path(spec["dir"]) / clip_dir_name(key)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (".%05d-%s.%d.tmp" % (i, ekey, os.getpid()))
    with open(tmp, "wb") as f:
        f.write(h_)
        for part in payload:
            f.write(part)
    os.replace(tmp, d / ("%05d-%s.pic" % (i, ekey)))
    return len(h_) + sum(len(p_) for p_ in payload)


def _worker(spec, q, done, go, used, cap, me=0, allowed=None):
    import house_wall_ascii_render as R
    g = R.Geom(int(spec["W"]), int(spec["H"]), density=float(spec["density"]))
    vinv = R._vignette_inv(g.W, g.H)
    player = spec.get("player") or ""
    per = g.W * g.H * 1.05
    while True:
        while allowed is not None and me >= allowed.value:      # not one of the workers that run under her film
            time.sleep(0.25)
        item = q.get()
        if item is None:
            return
        key, i, url = item
        go.wait()
        with used.get_lock():
            if not room_for_one(spec, used, cap, per):
                done.put((key, i, "budget"))
                continue
        try:
            n = _draw_one(spec, g, vinv, url or player, key, i)
        except Exception as e:  # noqa: BLE001 - one bad frame is the lap's to draw
            n = 0
            done.put((key, i, "error %s" % type(e).__name__))
            continue
        if n > 0:
            with used.get_lock():
                used.value += n
        done.put((key, i, "ok" if n > 0 else ("frame" if n < 0 else "skip")))


def prefill(spec: dict, log=print) -> int:
    """Draw every clip's idle film frames (her listen loop first, then the hubs, then the rest) into the framing's
    directory from the player's own unpainted frames (/v1/clip-frame), on workers_for() processes at the lowest CPU
    priority (SCHED_IDLE, nice 19), all of them held while she is on air or the person is talking, skipping what is on
    disk, stopping at the disk budget. Writes <framing>/progress.json (built / total, disk, milestones) for the Control
    Panel and the first start's loading screen. Exact: the cache's own whole-picture stages. Returns how many it wrote."""
    if not (spec.get("feed") or spec.get("player")):
        log("house-wall-ascii: ascii disk: prefill: no player named, nothing to build for")
        return 0
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError):
        pass
    try:
        os.nice(19)
    except OSError:
        pass
    import multiprocessing as mp
    root, target = Path(spec["root"]), Path(spec["dir"])
    player = spec.get("player") or ""
    feed = spec.get("feed") or player

    ear = spec.get("ear") or ""
    t0 = time.monotonic()
    try:
        plays = json.loads((root / "plays.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plays = {}
    order, loops = clip_order(spec.get("pack") or Path(__file__).resolve().parent.parent, plays,
                              krew=krew_here(spec.get("home") or Path.home()))
    # the Wall's own code print names what is dead (this process reads the files on disk, which a commit since the
    # Wall started may have changed); an older Wall's spec has none: the look's own framing.json, else this process's
    spec.setdefault("code", look_code(target) or code_print())
    if spec.get("only"):
        order = [k for k in order if k in set(spec["only"])]
    target.mkdir(parents=True, exist_ok=True)       # (the root too: the budget reads its disk)
    used0 = 0
    for dp, _, fs in os.walk(root):
        for f in fs:
            try:
                used0 += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    try:
        cap0 = cap_bytes(shutil.disk_usage(root).free, used0, os.environ)
    except OSError:
        cap0 = 0
    target.mkdir(parents=True, exist_ok=True)
    nw = workers_for(os.environ)
    n_up = workers_up(nw, os.environ)
    src = {"p": None, "url": "", "tried": False}

    def source() -> str:
        """Where the build's frames come from: its own decoder, started the first time a clip needs drawing (a run
        that finds every clip on disk starts nothing), else the player."""
        if not src["tried"]:
            src["tried"] = True
            src["p"], src["url"] = own_source(spec.get("pack") or Path(__file__).resolve().parent.parent, log=log)
            log("house-wall-ascii: ascii disk: prefill %s: frames from %s" % (
                target.name, ("its own decoder (pid %d)" % src["p"].pid) if src["url"] else "the player"))
        return src["url"] or player
    import crissa_build
    home = Path(spec.get("home") or crissa_home.HOME)
    if WAIT_FOR_PICK:
        wait_for_pick(home, target.name, log)
    cover = crissa_build.under_cover(home)
    ctx = mp.get_context("fork")
    q, done, go = ctx.Queue(), ctx.Queue(), ctx.Event()
    used, cap = ctx.Value("d", float(used0)), ctx.Value("d", float(cap0))
    allowed = ctx.Value("i", nw if cover else n_up)
    t_ask, quiet0 = time.monotonic(), _quiet(feed, ear)
    if quiet0:  # 2026-10-13: asked before the first picture (go was set unasked: 2 drawn on air by warm-forked workers)
        go.set()
    procs = [ctx.Process(target=_worker, args=(spec, q, done, go, used, cap, k, allowed), daemon=True)
             for k in range(nw)]
    for p_ in procs:
        p_.start()
    log("house-wall-ascii: ascii disk: prefill %s: %d clips to look at, on %d of %d workers (%s); disk %.1f of %.1f GB"
        % (target.name, len(order), allowed.value, nw, "Setup covers the glass" if cover else "under her film",
           used0 / GB, cap0 / GB))
    # THE WORKERS START AT ONCE: each clip is looked at (its frame count, what is on disk) just before its frames are
    # queued, so her listen loop is being drawn while the rest of the library is still being counted (asking the player
    # for a clip's first frame makes it decode that clip: a library of 76 clips took minutes to count up front)
    total, have_n, queued = 0, 0, 0
    per_clip_left: dict = {}
    wrote, finished, stopped = 0, 0, ""
    marks = {"listen": None, "loops": None, "all": None}
    listen_key = next((k for k in order if k in loops), order[0] if order else None)   # round 45: the entrance may lead
    loops_left = 0
    paused_s, last_poll, last_prog, paused_at = 0.0, t_ask, 0.0, (None if quiet0 else t_ask)
    surveyed = 0

    rate_log: list = []                    # (monotonic, pictures finished) every progress(): the recent rate
    # round 45 (.101 round 44: "1,697 of 1,741, less than a minute" read faster than it finished): the rate over the
    # run as it goes now - wall time since the workers last changed (Setup's cover ending drops them), its pauses
    # under her voice included. The plate takes the slower of it and the recent rate (crissa_build.plate_view).
    run_mark = [t_ask, 0]

    def progress(final=False):
        now_m = time.monotonic()
        # round 46 (.101 round 45: the time left read 424, 374, 467 min while the build stalled for her lines): the
        # recent window says whether it held work - pictures finished and no pause under her voice inside it
        held = paused_s + (now_m - paused_at if paused_at is not None else 0.0)
        rate_log.append((now_m, finished, held))
        del rate_log[:-12]
        old_ = next((r_ for r_ in rate_log if now_m - r_[0] <= 24.0), rate_log[0])
        rate = (finished - old_[1]) / (now_m - old_[0]) if now_m - old_[0] >= 4.0 else None
        worked = bool(rate) and finished > old_[1] and held - old_[2] <= 0.0
        run_s = now_m - run_mark[0]
        rate_run = (finished - run_mark[1]) / run_s if run_s >= 4.0 else None
        st = {"framing": target.name, "total": total, "built": have_n + wrote, "wrote": wrote,
              "left": queued - finished, "counting": surveyed < len(order),
              "disk_used": used.value, "disk_cap": cap.value, "stopped": stopped, "done": final and not stopped,
              "workers": nw, "workers_now": nw if cover else n_up, "cover": cover,
              "paused_s": round(paused_s, 1), "since": time.time() - (now_m - t0),
              "listen_done_s": marks["listen"], "loops_done_s": marks["loops"], "all_done_s": marks["all"],
              "loops_done": marks["loops"] is not None, "codec": codec(), "at": time.time(),
              # round 15: the loops' own count (the plate on the glass waits on them) and the recent rate
              "loops_total": loops_total, "loops_built": loops_total - loops_left,
              "loops_counted": surveyed_loops(), "rate": round(rate, 2) if rate else None,
              "rate_run": round(rate_run, 2) if rate_run else None, "rate_worked": worked}
        try:
            tmp = target / ".progress.tmp"
            tmp.write_text(json.dumps(st))
            os.replace(tmp, target / "progress.json")
        except OSError:
            pass

    def pump(block_s):
        """Hold or free the workers, write the progress, and take one finished frame (or time out)."""
        nonlocal paused_at, paused_s, last_poll, last_prog, finished, wrote, stopped, listen_left, loops_left, cover
        now = time.monotonic()
        if now - last_poll >= 0.5:
            last_poll = now
            c_ = crissa_build.under_cover(home)
            if c_ != cover:
                cover = c_
                allowed.value = nw if cover else n_up
                run_mark[:] = [now, finished]          # round 45: the run's rate from the workers it has now
                rate_log.clear()                        # round 46: and the recent window starts with them too
                log("house-wall-ascii: ascii disk: prefill %s: %d of %d workers (%s)"
                    % (target.name, allowed.value, nw, "Setup covers the glass" if cover else "under her film"))
            if _quiet(feed, ear):
                if paused_at is not None:
                    paused_s += now - paused_at
                    paused_at = None
                go.set()
            else:
                if paused_at is None:
                    paused_at = now
                go.clear()
        if now - last_prog >= 2.0:
            last_prog = now
            progress()
        try:
            key, i, how = done.get(timeout=block_s)
        except Exception:  # noqa: BLE001 - queue.Empty
            return False
        finished += 1
        if how == "ok":
            wrote += 1
        elif how == "budget" and not stopped:
            stopped = "the disk budget"
        if key == listen_key:
            listen_left -= 1
            if listen_left == 0 and marks["listen"] is None:
                marks["listen"] = round(time.monotonic() - t0, 1)
        if key in loops:
            loops_left -= 1
            if loops_left == 0 and surveyed_loops() and marks["loops"] is None:
                marks["loops"] = round(time.monotonic() - t0, 1)
        return True

    loop_keys = [k for k in order if k in loops]

    def surveyed_loops():
        return surveyed >= len(loop_keys)

    listen_left = 0
    loops_total = 0
    for key in order:
        while pump(0.0):  # 2026-10-13: the count asks too, not only after a picture is done (she may start meanwhile)
            pass
        try:
            names = os.listdir(target / clip_dir_name(key))
        except OSError:
            names = []
        have = {int(n.split("-", 1)[0]) for n in names if n.endswith(".pic") and n.split("-", 1)[0].isdigit()}
        # A CLIP ON DISK IS NOT ASKED FOR (round 15): its frame count is kept beside its pictures (clip.json), so a
        # run that finds a clip whole decodes nothing for it - the ten-minute re-ask is a few listings
        n = 0
        try:
            n = int(json.loads((target / clip_dir_name(key) / "clip.json").read_text(encoding="utf-8"))["frames"])
        except (OSError, ValueError, KeyError, TypeError):
            n = 0
        url = None
        if n <= 0 or not have.issuperset(range(n)):
            url = source()
            first = _get_frame(url, key, 0)
            if first is None or first[2] is None:
                surveyed += 1
                continue
            n = int(first[2][2])
            try:
                (target / clip_dir_name(key)).mkdir(parents=True, exist_ok=True)
                (target / clip_dir_name(key) / "clip.json").write_text(json.dumps({"frames": n}))
            except OSError:
                pass
        surveyed += 1
        total += n
        have_n += len(have & set(range(n)))
        todo = [i for i in [0] + list(range(n - 1, 0, -1)) if i not in have]
        per_clip_left[key] = len(todo)
        if key == listen_key:
            listen_left = len(todo)
            if not todo:
                marks["listen"] = round(time.monotonic() - t0, 1)
        if key in loops:
            loops_left += len(todo)
            loops_total += n                                # (frames already on disk count as built)
        for i in todo:
            q.put((key, i, url))
        queued += len(todo)
        if surveyed_loops() and loops_left == 0 and marks["loops"] is None:
            marks["loops"] = round(time.monotonic() - t0, 1)
    for _ in range(nw):
        q.put(None)
    while finished < queued:
        if not pump(0.25) and not any(p_.is_alive() for p_ in procs):
            break
    marks["all"] = round(time.monotonic() - t0, 1)
    allowed.value = nw                      # every worker takes its own end
    for p_ in procs:
        p_.join(timeout=5)
    if src["p"] is not None:
        try:
            src["p"].stdin.close()
            src["p"].wait(timeout=5)
        except Exception:  # noqa: BLE001
            src["p"].kill()
    import resource
    cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    progress(final=True)
    if not stopped and finished >= queued and DROP_OLD_LOOKS:
        drop_other_looks(root, target.name, log)
    log("house-wall-ascii: ascii disk: prefill %s: wrote %d in %.0f s on %d workers (listen done %s s, loops %s s, all %s s;"
        " %.0f s CPU; held %.0f s while she or the person talked)%s; disk %.1f GB"
        % (target.name, wrote, time.monotonic() - t0, nw, marks["listen"], marks["loops"], marks["all"],
           cpu.ru_utime + cpu.ru_stime, paused_s, ("; stopped: " + stopped) if stopped else "", used.value / GB))
    return wrote


# HER FIRST WORDS AT THE DENSITY THIS MACHINE HOLDS (round 15, 2026-10-12, .101's fresh install: the Wall started at
# density 1.0 where the User's own 5.0 "looks far better"; "Looking for first live words to be a good impression"):
# Setup's machine card times the Wall's own whole render (render_background, the fresh picture a first lap or a
# speaking frame costs) at each candidate density on this glass's size, highest first, and the highest whose median
# fits one film frame (1000/24 ms) is the Wall's density from then on - before the build runs, so it builds at it.
# The card calls this as `wall_ascii_disk.py --density-probe JSON` (lib/crissa_machine.stage_density); its only
# output is the JSON on stdout. GLASS_NOTE is where the desk Wall says its size at glass up (a pack's only).
GLASS_NOTE = "run/wall-glass.json"
DENSITY_CANDIDATES = (5.0, 4.0, 3.0, 2.0, 1.5, 1.0)


# THE PICK TIMES WHAT THE WALL DOES (round 21, .101 round 20: density 4.0 picked where rounds 16-19 picked 2.0, and the
# glass drew 23-30/s at 100 % CPU). The probe timed render_background alone, in its own process, against one film
# frame at the median: 38.5-39.1 ms at 4.0 under the .101-like budget (wall-speed-101-20261011/bench/budget.sh), so
# 4.0 "fit" 41.7 ms - while the Wall's own pass at 4.0 is the film child's round trip (43-46 ms) plus the rest of the
# pass (the loop 48-51 ms), and painted 20.5/s against 26.3/s at 2.0 when the cache missed (docs/checkpoints/
# r21-paint-20261014). The strict rule (`via_child`, `margin`, `paint_ms`; lib/crissa_machine passes them) times the
# film child's round trip, as the Wall draws, and fits a density only when its median times the Wall's own stride
# margin (1.1, the film thread's) plus the rest of the pass (paint_ms) holds one film frame. Without them: the old rule.
def density_probe(W: int, H: int, film, candidates=DENSITY_CANDIDATES, frames: int = 6, fps: float = 24.0,
                  timer=time.perf_counter, via_child: bool = False, margin: float = 1.0, paint_ms: float = 0.0,
                  child=None) -> dict:
    """{"candidates": [{density, p50_ms, p90_ms}], "pick": density or None, "bar_ms"}: the Wall's whole render of
    `frames` frames of `film` (decoded and sized as the player publishes them) at each density, highest first,
    stopping at the first that fits the bar. `via_child`: timed through one film child (`child` (W, H) -> an object
    with render() and close(); default wall_film_proc.FilmProcess), each density's first replies given the child's
    cold allowance (a new grid builds its glyph sheet), the in-process render when no child answers; a density fits
    when p50 * margin + paint_ms <= the bar."""
    import cv2
    import house_wall_ascii_render as R
    import wall_frame
    cap = cv2.VideoCapture(str(film))
    jpegs = []
    while len(jpegs) < max(2, int(frames)):
        ok, img = cap.read()
        if not ok:
            break
        if img.shape[1] != 736 or img.shape[0] != 400:
            img = cv2.resize(img, (736, 400), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        if ok:
            jpegs.append(buf.tobytes())
    cap.release()
    bar = 1000.0 / float(fps)
    out = {"W": int(W), "H": int(H), "bar_ms": round(bar, 1), "frames": len(jpegs), "candidates": [], "pick": None}
    if not jpegs:
        out["why"] = "the film did not decode"
        return out
    srcs = [R._open(j) for j in jpegs]
    frame = ("whole", 1.0) + tuple(wall_frame.face_of(None))
    margin, paint_ms = max(1.0, float(margin or 1.0)), max(0.0, float(paint_ms or 0.0))
    out["rule"] = {"via": "child" if via_child else "thread", "margin": margin, "paint_ms": paint_ms}

    kid: dict = {}

    def _child_ms(d: float):
        """The film child's round trip for each frame at density d, or None when no child answers."""
        c = kid.get("c")
        if c is None and not kid.get("failed"):
            try:
                if child is not None:
                    c = child(int(W), int(H))
                else:
                    import wall_film_proc
                    c = wall_film_proc.FilmProcess(int(W), int(H), log=lambda m: None)
                kid["c"] = c
            except Exception:  # noqa: BLE001 - the in-process timing stands
                kid["failed"] = True
        if c is None:
            return None
        if hasattr(c, "answered"):
            c.answered = 0                     # a new grid builds its glyph sheet: the cold allowance (COLD_S) again
        for j in jpegs[:2]:                                                 # its glyph sheet and geometry
            if c.render(j, d, True, 0.0, cache=False, frame=frame) is None:
                return None
        ms = []
        for j in jpegs:
            t0 = timer()
            if c.render(j, d, True, 0.0, cache=False, frame=frame) is None:
                return None
            ms.append((timer() - t0) * 1000.0)
        return ms

    for d in sorted((float(c) for c in candidates), reverse=True):
        g = R.Geom(int(W), int(H), density=d)
        ms = _child_ms(g.density) if via_child else None
        how = "child" if ms else "thread"
        if not ms:
            R.render_background(srcs[0], g, True, 0.0, frame=frame)        # the glyph sheet for this grid, once
            ms = []
            for i, src in enumerate(srcs):
                R._GLOW.update(n=0, im=None, fp=None, out=None)             # a fresh picture each time, as a first lap
                t0 = timer()
                R.render_background(src, g, True, 0.0, frame=frame)
                ms.append((timer() - t0) * 1000.0)
        ms.sort()
        row = {"density": g.density, "p50_ms": round(ms[len(ms) // 2], 1), "p90_ms": round(ms[int(len(ms) * 0.9)], 1)}
        if via_child or margin != 1.0 or paint_ms:
            row.update(how=how, fit_ms=round(ms[len(ms) // 2] * margin + paint_ms, 1))
        out["candidates"].append(row)
        if ms[len(ms) // 2] * margin + paint_ms <= bar:
            out["pick"] = g.density
            break
    if kid.get("c") is not None:
        try:
            kid["c"].close()
        except Exception:  # noqa: BLE001
            pass
    return out


class DiskTier:
    """One per film child. lookup / load / store; a writer thread does every write and every eviction."""

    def __init__(self, root: Path | None = None, log=None, env=None):
        self.env = os.environ if env is None else env
        self.root = Path(root or self.env.get("HOUSE_WALL_ASCII_DISK_DIR") or ROOT)
        self.log = log or (lambda m: None)
        self.fdir: Path | None = None
        self.fk = None
        self.clips: dict = {}                 # (framing dir name, clip dir) -> {frame: (digest_hex, Path)}
        self.touched: dict = {}               # (framing dir name, clip dir) -> monotonic of the last mtime touch
        self.listed: dict = {}                # (framing dir name, clip dir) -> monotonic of the last listing
        self.prefill_p: subprocess.Popen | None = None
        self.prefill_for: str | None = None   # the look the running pre-fill builds
        self.recounted = None                 # the pid of the last ended pre-fill whose writes were counted
        self.prefill_at: dict = {}            # framing dir name -> monotonic of the last pre-fill start
        self.prefill_asked = time.time()      # a prefill.request newer than this starts a run at once
        self.norm = True                      # the Wall leaves her mouth out of the exposure (its request says)
        self.plays: dict = {}                 # clip -> times she entered it here (root/plays.json): the order
        self.plays_saved = 0.0
        try:
            self.plays = json.loads((self.root / "plays.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.plays = {}
        self.playing = None                   # (framing dir name, clip dir) on the glass: never evicted
        self.q: queue.Queue = queue.Queue(maxsize=48)
        self.used = 0
        self.sizes: dict = {}                 # framing dir name -> {clip dir: bytes}
        self.cap = 0
        self.n = dict(hits=0, bases=0, writes=0, dropped=0, damaged=0, evicted_clips=0, evicted_framings=0, replaced=0)
        self.dead = ""
        self._lock = threading.Lock()
        self._scanned = threading.Event()
        threading.Thread(target=self._writer, daemon=True, name="ascii-disk").start()

    # ---- framing ------------------------------------------------------------------------------------------------
    def framing(self, fk) -> None:
        if fk == self.fk:
            return
        self.fk = fk
        name = framing_dir_name(fk)
        self.fdir = self.root / name
        try:
            self.fdir.mkdir(parents=True, exist_ok=True)
            meta = self.fdir / "framing.json"
            if not meta.exists():
                meta.write_text(json.dumps({"framing": repr(fk), "code": code_print(), "since": time.time()}))
            os.utime(self.fdir)
            if MAKE_ROOM:
                mark_live(self.root, name, code_print())       # the look on this glass: no one else's make-room takes it
            if not getattr(self, "_trimmed", False) and trim_old_on(self.env):
                self._trimmed = True
                threading.Thread(target=trim_old_looks, args=(self.root, code_print()), kwargs={"log": self.log},
                                 daemon=True, name="ascii-trim").start()
        except OSError as e:
            self.dead = "cannot write %s (%s)" % (self.fdir, e)
            return
        self._prefill(fk)

    def _prefill(self, fk) -> None:
        """Start the idle pre-fill for the framing on the glass: at its first picture in this process, then again every
        PREFILL_EVERY_S once the last one has finished (it skips what is on disk, so a run with nothing to do is a few
        listings and five requests)."""
        ended = getattr(self.prefill_p, "pid", None) if self.prefill_p is not None and self.prefill_p.poll() is not None \
            else None
        if ended is not None and self.recounted != ended:
            self.recounted = ended                               # a pre-fill ended: count what it wrote (the writer)
            try:
                self.q.put_nowait(None)
            except queue.Full:
                self.recounted = None
        if not prefill_enabled(self.env) or not (isinstance(fk, tuple) and len(fk) == 7):
            return
        if self.prefill_p is not None and self.prefill_p.poll() is None:
            if getattr(self, "prefill_for", None) in (None, self.fdir.name):
                return
            # ANOTHER LOOK IS ON THE GLASS NOW (round 15: Setup's machine card set her density while the first start's
            # build ran at the default): that build stops, and this look's starts at once
            self.log("ascii disk: prefill %s stopped: the glass is %s now" % (self.prefill_for, self.fdir.name))
            try:
                os.killpg(self.prefill_p.pid, signal.SIGTERM)
            except (OSError, AttributeError):
                pass
            try:
                self.prefill_p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        now = time.monotonic()
        last = self.prefill_at.get(self.fdir.name)
        try:                                        # a pack just landed (lib/crissa_import.py): now, not in ten minutes
            asked = (self.root / "prefill.request").stat().st_mtime
        except OSError:
            asked = 0.0
        fresh_ask = asked > self.prefill_asked
        if last is not None and now - last < PREFILL_EVERY_S and not fresh_ask:
            return
        self.prefill_asked = max(self.prefill_asked, asked)
        self.prefill_at[self.fdir.name] = now
        self.prefill_for = self.fdir.name
        W, H, density, glow, hole_zoom, shift_k, mz = fk
        feed = self.env.get("HOUSE_WALL_FEED") or self.env.get("CRISSA_PLAYER") or ""
        if not feed:
            # NO PLAYER, NO BUILD (round 16, 2026-10-13: a test's cache with the disk tier on and no feed started a
            # real build of this tree's whole library through its own decoder, into a tmpfs, until the test scope
            # was killed): a Wall always has a feed; without one there is nothing to build for
            return
        spec = {"root": str(self.root), "dir": str(self.fdir), "W": W, "H": H, "density": density,
                "hole_zoom": hole_zoom, "shift": list(shift_k) if shift_k else None,
                "mz": list(mz) if mz else None, "norm": bool(self.norm), "feed": feed,
                "player": self.env.get("HOUSE_WALL_PREFILL_PLAYER") or feed,
                "ear": self.env.get("CRISSA_EAR_URL") or "",
                "pack": str(Path(__file__).resolve().parent.parent), "code": code_print()}
        try:
            self.prefill_p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--prefill", json.dumps(spec)],
                                              stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr,
                                              cwd=os.path.dirname(os.path.abspath(__file__)), start_new_session=True)
        except OSError as e:
            self.log("ascii disk: prefill could not start (%s)" % e)

    # ---- reading ------------------------------------------------------------------------------------------------
    def _clip(self, clip_key) -> dict:
        k = (self.fdir.name, clip_dir_name(clip_key))
        got = self.clips.get(k)
        if got is None:
            got = {}
            self.listed[k] = time.monotonic()
            d = self.fdir / k[1]
            try:
                for e in os.scandir(d):
                    if e.name.endswith(".pic"):
                        fr, _, dg = e.name[:-4].partition("-")
                        if fr.isdigit() and dg:
                            got[int(fr)] = (dg, Path(e.path))
            except OSError:
                pass
            self.clips[k] = got
        return got

    def lookup(self, clip_key, frame: int):
        """(digest_hex, path) of the entry for this clip frame in the framing on the glass, else None."""
        if self.dead or self.fdir is None:
            return None
        if int(frame) == 0:
            self._prefill(self.fk)                 # once a lap at most: a finished pre-fill is asked again in time
        self._played(clip_key)
        got = self._clip(clip_key).get(int(frame))
        if got is None:
            k = (self.fdir.name, clip_dir_name(clip_key))
            if time.monotonic() - self.listed.get(k, 0.0) >= RESCAN_S:      # a prefill may have written it since
                pend = {f: v for f, v in self.clips.pop(k, {}).items() if v[1] is None}
                fresh = self._clip(clip_key)
                for f, v in pend.items():
                    fresh.setdefault(f, v)
                got = fresh.get(int(frame))
        return got if (got is not None and got[1] is not None) else None      # a pending write is not there yet

    def load(self, clip_key, frame: int, path, keep_z: bool = False) -> dict | None:
        got = read_entry(path, keep_z=keep_z)
        if got is None:
            self.n["damaged"] += 1
            self._clip(clip_key).pop(int(frame), None)
            try:
                os.unlink(path)
            except OSError:
                pass
        return got

    def _played(self, clip_key) -> None:
        k = (self.fdir.name, clip_dir_name(clip_key))
        if k != self.playing:
            self.plays[str(clip_key)] = int(self.plays.get(str(clip_key), 0)) + 1
            if time.monotonic() - self.plays_saved >= TOUCH_EVERY_S:
                self.plays_saved = time.monotonic()
                try:
                    tmp = self.root / ".plays.tmp"
                    tmp.write_text(json.dumps(self.plays))
                    os.replace(tmp, self.root / "plays.json")
                except OSError:
                    pass
        self.playing = k
        now = time.monotonic()
        if now - self.touched.get(k, 0.0) >= TOUCH_EVERY_S:
            self.touched[k] = now
            try:
                os.utime(self.fdir / k[1])
            except OSError:
                pass

    # ---- writing ------------------------------------------------------------------------------------------------
    def store(self, clip_key, frame: int, digest_hex: str, w, h, rows, cols, idx, small_size, small, jpeg, mouth, px,
              wframe=None, role=None, density=None, done=None, replace: bool = False) -> bool:
        """Remember this clip frame's picture (once): queued for the writer; a full queue drops it (the next lap
        will offer it again). True when queued. `done` (round 17): called from the writer with the coverage's bytes
        as packed, once written - the memory tier holds them instead of packing the picture again. `replace` (round
        32): an entry already there under another key is replaced (the caller says it was a pre-fill's)."""
        if self.dead or self.fdir is None or not self._scanned.is_set() or self.cap <= 0:
            return False
        entries = self._clip(clip_key)
        old = None
        if int(frame) in entries:
            had = entries[int(frame)]
            if not replace or had[1] is None or had[0] == digest_hex:
                return False
            old = had[1]                                   # written: the writer removes it once the new one is down
        entries[int(frame)] = (digest_hex, None)          # claimed: a second offer of this frame is not queued
        try:
            self.q.put_nowait((self.fdir, clip_dir_name(clip_key), int(frame), digest_hex,
                               (w, h, rows, cols, idx, small_size, small, jpeg, mouth, px,
                                {"frame": list(wframe) if isinstance(wframe, tuple) else wframe, "role": role,
                                 "density": density, "clip": str(clip_key)}), done, old))
        except queue.Full:
            if old is not None:
                entries[int(frame)] = (had[0], old)        # the pre-fill's stays until a later lap offers it again
            else:
                entries.pop(int(frame), None)
            self.n["dropped"] += 1
            return False
        return True

    def _writer(self) -> None:
        self._scan()
        self._scanned.set()
        while True:
            item = self.q.get()
            if item is None:
                # A RE-COUNT (round 16, 2026-10-13: the house's Wall said disk=1.5/40GB while its look held 40 GB, every
                # picture the pre-fill wrote from its own process uncounted here): sizes from the disk as it is now
                with self._lock:
                    self.sizes.clear()
                self._scan()
                continue
            fdir, cdir, frame, dg, args = item[:5]
            done = item[5] if len(item) > 5 else None
            old = item[6] if len(item) > 6 else None
            try:
                hdr, payload = pack(*args[:8], dg, args[8], args[9], args[10])
                if done is not None and len(payload[3]) != len(args[9]) and codec() == "zstd-1":
                    try:
                        done(payload[3])                  # the memory tier's copy, packed once (round 17)
                    except Exception:  # noqa: BLE001 - a hook never stops the writer
                        pass
                d = fdir / cdir
                d.mkdir(parents=True, exist_ok=True)
                path = d / ("%05d-%s.pic" % (frame, dg))
                tmp = d / (".%05d-%s.tmp" % (frame, dg))
                with open(tmp, "wb") as f:
                    f.write(hdr)
                    for part in payload:
                        f.write(part)
                os.replace(tmp, path)
                size = len(hdr) + sum(len(p) for p in payload)
                if old is not None and Path(old) != path:
                    try:
                        size -= os.stat(old).st_size        # the pre-fill's entry it replaces (round 32)
                        os.unlink(old)
                        self.n["replaced"] += 1
                    except OSError:
                        pass
                with self._lock:
                    self.used += size
                    self.sizes.setdefault(fdir.name, {})[cdir] = self.sizes.get(fdir.name, {}).get(cdir, 0) + size
                got = self.clips.get((fdir.name, cdir))
                if got is not None:
                    got[frame] = (dg, path)
                self.n["writes"] += 1
                self._fit()
            except OSError as e:
                self.n["dropped"] += 1
                if "No space" in str(e):
                    self.dead = "disk full"
                    self.log("ascii disk: %s; the disk tier is off until the wall restarts" % e)

    def _scan(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for fd in os.scandir(self.root):
                if not fd.is_dir():
                    continue
                per = self.sizes.setdefault(fd.name, {})
                for cd in os.scandir(fd.path):
                    if cd.is_dir():
                        tot = 0
                        for e in os.scandir(cd.path):
                            try:
                                tot += e.stat().st_size
                            except OSError:
                                pass
                        per[cd.name] = tot
            self.used = sum(sum(v.values()) for v in self.sizes.values())
            self._budget()
            self.log("ascii disk: %s holds %.1f GB of %.1f GB (%d framings)"
                     % (self.root, self.used / GB, self.cap / GB, len(self.sizes)))
        except OSError as e:
            self.dead = "cannot read %s (%s)" % (self.root, e)

    def _budget(self) -> None:
        try:
            free = shutil.disk_usage(self.root).free
        except OSError:
            free = 0
        self.cap = cap_bytes(free, self.used, self.env)

    def _fit(self) -> None:
        if self.used <= self.cap and self.n["writes"] % 64:
            return
        self._budget()
        while self.used > self.cap:
            if not self._evict_one():
                break

    def _evict_one(self) -> bool:
        cur = self.fdir.name if self.fdir is not None else None
        others = [n for n in self.sizes if n != cur and self.sizes[n]]
        why = {}
        if others and MAKE_ROOM:
            # 2026-10-13: a dead look (another code print) first, then other framings oldest first (room_order), a
            # directory that is not a look after those; never a look another live Wall is on
            spare = live_looks(self.root, but=os.getpid())
            rank = {d.name: (k, w) for k, (d, w, _t) in enumerate(room_order(self.root, cur, code_print(), spare))}
            others = sorted((n for n in others if n not in spare),
                            key=lambda n: (rank.get(n, (len(rank), ""))[0], self._mtime(self.root / n)))
            why = {n: w for n, (_k, w) in rank.items()}
        if others:                                         # another framing (or another kit's code) goes first
            name = others[0] if MAKE_ROOM else min(others, key=lambda n: self._mtime(self.root / n))
            if MAKE_ROOM:
                self.log("ascii disk: look %s evicted (%.2f GB): %s" % (
                    name, sum((self.sizes.get(name) or {}).values()) / GB, why.get(name) or "not a look"))
            self._rm(self.root / name)
            with self._lock:
                self.used -= sum(self.sizes.pop(name, {}).values())
            self.clips = {k: v for k, v in self.clips.items() if k[0] != name}
            self.n["evicted_framings"] += 1
            return True
        per = self.sizes.get(cur) or {}
        cands = [c for c in per if (cur, c) != self.playing and per[c] > 0]
        if not cands:
            return False
        c = min(cands, key=lambda c: self._mtime(self.root / cur / c))   # least recently played
        self._rm(self.root / cur / c)
        with self._lock:
            self.used -= per.pop(c, 0)
        self.clips.pop((cur, c), None)
        self.n["evicted_clips"] += 1
        return True

    @staticmethod
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _rm(p: Path) -> None:
        shutil.rmtree(p, ignore_errors=True)

    def stats(self) -> dict:
        return dict(self.n, used=self.used, cap=self.cap, dead=self.dead, queued=self.q.qsize())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--density-probe":
        a = json.loads(sys.argv[2])
        print(json.dumps(density_probe(a["W"], a["H"], a["film"], a.get("candidates") or DENSITY_CANDIDATES,
                                       int(a.get("frames") or 6), float(a.get("fps") or 24.0),
                                       via_child=bool(a.get("via_child")), margin=float(a.get("margin") or 1.0),
                                       paint_ms=float(a.get("paint_ms") or 0.0))), flush=True)
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--prefill":
        sys.exit(0 if prefill(json.loads(sys.argv[2]), log=lambda m: print(m, file=sys.stderr, flush=True)) >= 0 else 1)
    print(__doc__)
