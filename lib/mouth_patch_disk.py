"""The finished mouth patches' DISK tier, filled ahead at install (the User 2026-10-24: "every painted patch for each
loop frame and tile could be built ahead <- we do this for all ascii frames too at first install too. Add it there").

lib/mouth_patch keeps what a paint made in memory, so a frame x tile is painted once per process. This tier keeps it on
disk, and the conductor's own build (house-conductor.py --mouth-patch-prefill, started by the conductor at boot, held
while she is on air) paints every hub loop frame x every tile that loop's playlist can paint into it ahead of time,
the way the desk Wall builds her ASCII pictures (lib/wall_ascii_disk.py). Measured 2026-10-24 on the v2 desk bench
(1280x720, v2-listen-screen, one line six times): a cold store painted 132 of 285 semiframes (composite p50 43.6 ms),
the warm one 303 of 285 with 5 skipped (composite p50 1.3 ms).

Exact: an entry is lib/mouth_patch's own (the box where the finished frame differs from the film frame, the film's
digest there, those pixels, the paint's side record) under the paint's whole key, with an adler32 of its payload (a
mismatch deletes the file and reads as a miss). The directory names the CODE that painted it (mouth_patch.code_print:
the conductor and every lib/ module, numpy, OpenCV), so new code is a new directory and never an old picture; the
key names the dials, the library files and the frame, so a retune is a miss, never a stale patch.

Layout: <state home>/cache/mouth-patch/<code print>/<key[:2]>/<key>.mpt: b"MPT1", u32 header length, a JSON header
(bbox, roi shape and dtype, adler32, the side record), the film digest (16 bytes), the pixels.

Budget: config/mouth-patch.json (each dial with the measurement that set it); over it, another code print's directory
goes first (dead: never servable), then the build stops. Progress: <code print>/progress.json, which
lib/crissa_build.build_view reads beside the ASCII build's.

On: HOUSE_MOUTH_PATCH_DISK=1|0; unset = the patch store's own default (lib/mouth_patch.enabled).
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import threading
import time
import zlib
from pathlib import Path

import numpy as np

GB = 1 << 30
MAGIC = b"MPT1"
ROOT = Path(__file__).resolve().parents[1]


def config(path=None) -> dict:
    try:
        return json.loads(Path(path or ROOT / "config" / "mouth-patch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_MOUTH_PATCH_DISK", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    import mouth_patch
    return mouth_patch.enabled(env)


def prefill_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return enabled(env) and str(env.get("HOUSE_MOUTH_PATCH_PREFILL", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def root(home=None) -> Path:
    if home is None and os.environ.get("HOUSE_MOUTH_PATCH_DISK_DIR"):
        return Path(os.environ["HOUSE_MOUTH_PATCH_DISK_DIR"])
    import crissa_home
    return Path(home or crissa_home.HOME) / "cache" / "mouth-patch"


def cap_bytes(free: int, used: int, cfg=None, env=None) -> int:
    """How much this tier may hold: the config's share of the disk it may still take (free + what it holds now), up to
    its ceiling, never leaving less than its floor free. HOUSE_MOUTH_PATCH_DISK_GB overrides."""
    env = os.environ if env is None else env
    cfg = config() if cfg is None else cfg
    if env.get("HOUSE_MOUTH_PATCH_DISK_GB"):
        try:
            return int(float(env["HOUSE_MOUTH_PATCH_DISK_GB"]) * GB)
        except ValueError:
            pass
    share = float(cfg.get("disk_share_of_free", 0.0))
    ceil = float(cfg.get("max_gb", 0.0)) * GB
    floor = float(cfg.get("keep_free_gb", 0.0)) * GB
    room = free + used
    return int(max(0.0, min(share * room, ceil, used + max(0.0, free - floor))))


def _dir_bytes(p: Path) -> int:
    n = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n


def _json_safe(v):
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer, np.bool_)):
        return v.item()
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def pack(ent) -> bytes:
    bbox, bdig, roi, side = ent
    hdr = {"bbox": list(bbox) if bbox is not None else None, "side": _json_safe(side or {})}
    body = b""
    if roi is not None:
        a = np.ascontiguousarray(roi)
        hdr.update(shape=list(a.shape), dtype=a.dtype.str)
        body = bytes(bdig) + a.tobytes()
    hdr["adler"] = zlib.adler32(body)
    h = json.dumps(hdr, separators=(",", ":")).encode()
    return MAGIC + struct.pack("<I", len(h)) + h + body


def unpack(raw: bytes):
    if raw[:4] != MAGIC:
        return None
    n = struct.unpack("<I", raw[4:8])[0]
    hdr = json.loads(raw[8:8 + n])
    body = raw[8 + n:]
    if zlib.adler32(body) != hdr.get("adler"):
        return None
    side = hdr.get("side") or {}
    if hdr.get("bbox") is None:
        return (None, b"", None, side)
    roi = np.frombuffer(body[16:], dtype=np.dtype(hdr["dtype"])).reshape(hdr["shape"])
    return (tuple(hdr["bbox"]), body[:16], roi, side)


class DiskTier:
    """One code print's directory. get() is a file read; put() writes atomically, refusing past the budget."""

    def __init__(self, print_: str, home=None, log=print, env=None, cfg=None):
        self.base = root(home)
        self.dir = self.base / print_
        self.log = log
        self.env = os.environ if env is None else env
        self.cfg = config() if cfg is None else cfg
        self.lock = threading.Lock()
        self.stats = {"read": 0, "miss": 0, "bad": 0, "wrote": 0, "refused_budget": 0}
        self.used = 0
        self.cap = 0
        self._sized = False

    def path(self, key: bytes) -> Path:
        h = key.hex()
        return self.dir / h[:2] / (h + ".mpt")

    def get(self, key: bytes):
        p = self.path(key)
        try:
            raw = p.read_bytes()
        except OSError:
            self.stats["miss"] += 1
            return None
        try:
            ent = unpack(raw)
        except (ValueError, KeyError, struct.error):
            ent = None
        if ent is None:
            self.stats["bad"] += 1
            try:
                p.unlink()
            except OSError:
                pass
            return None
        self.stats["read"] += 1
        return ent

    def has(self, key: bytes) -> bool:
        return self.path(key).exists()

    def size_up(self) -> None:
        """What every directory holds and what the budget is: once, before the first write."""
        self.base.mkdir(parents=True, exist_ok=True)
        used = _dir_bytes(self.base)
        try:
            free = shutil.disk_usage(self.base).free
        except OSError:
            free = 0
        self.used = used
        self.cap = cap_bytes(free, used, self.cfg, self.env)
        self._sized = True

    def make_room(self, need: int) -> bool:
        """Dead first: another code print's directory is never servable again."""
        for d in sorted((x for x in self.base.iterdir() if x.is_dir() and x != self.dir),
                        key=lambda x: x.stat().st_mtime):
            if self.used + need <= self.cap:
                break
            n = _dir_bytes(d)
            shutil.rmtree(d, ignore_errors=True)
            self.used -= n
            self.log("[mouth-patch] disk: dropped %s (another code print, %.2f GB)" % (d.name, n / GB))
        return self.used + need <= self.cap

    def put(self, key: bytes, ent) -> bool:
        raw = pack(ent)
        with self.lock:
            if not self._sized:
                self.size_up()
            if self.used + len(raw) > self.cap and not self.make_room(len(raw)):
                self.stats["refused_budget"] += 1
                return False
            p = self.path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp%d" % os.getpid())
            try:
                tmp.write_bytes(raw)
                os.replace(tmp, p)
            except OSError:
                return False
            self.used += len(raw)
            self.stats["wrote"] += 1
            return True

    def progress(self, st: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / ".progress.tmp"
            tmp.write_text(json.dumps(dict(st, disk_used=self.used, disk_cap=self.cap, at=time.time())))
            os.replace(tmp, self.dir / "progress.json")
        except OSError:
            pass

    def state(self) -> dict:
        return dict(self.stats, dir=str(self.dir), used_gb=round(self.used / GB, 3), cap_gb=round(self.cap / GB, 2))


def progress_view(home=None) -> dict | None:
    """The newest build's progress.json (lib/crissa_build.build_view shows it beside the ASCII build)."""
    best = None
    try:
        for d in os.scandir(root(home)):
            if d.is_dir():
                try:
                    st = json.loads((Path(d.path) / "progress.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if best is None or float(st.get("at") or 0) > float(best.get("at") or 0):
                    best = st
    except OSError:
        return None
    return best
