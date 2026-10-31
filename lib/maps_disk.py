"""The cheeks maps' DISK tier: a restart starts warm (the User 2026-10-12, "go ahead with 2 and 3").

poly_cache keeps each (film frame x tile) pinned warp in memory (HOUSE_MOUTH_CHEEKS_MAPS, 4096 on the house), and a
process starts empty: after the 12:08 conductor restart her first close-up reading built every map fresh (c_maps
6-19 ms p50 against 0.3-0.9 warm) and the paint ran 21-28 ms in a 20.8 ms semiframe, half the mouth rows skipped.
This tier writes each map the memory tier builds to disk once and reads it back on a memory miss (~0.5 ms), so what
she has said on a shot before is warm after any restart.

Exact: an entry is the memory tier's own value (the ROI-cropped maps and the pin record, poly_cache's storage form),
pickled, under a header that carries the full key and a blake2b of the payload; a mismatch of either deletes the file
and reads as a miss. The directory names the CODE that builds a map (a fingerprint of the modules on the path and the
conductor methods the caller passes) and every key carries the caller's salt (the live mouth dials and the film's own
file), so a new kit, a changed dial or a replaced film is a different entry, never an old one.

The directory is <state home>/cache/mouth-maps (HOUSE_MOUTH_MAPS_DISK_DIR overrides). Writes go through one background thread (a paint never waits on the disk). Budget HOUSE_MOUTH_MAPS_DISK_GB (default
8); over it, the least recently used files go. On: HOUSE_MOUTH_MAPS_DISK=1|0; unset = on in a pack, off on the house
(crissa_home.is_house).

Compact form (2026-10-13): new files are HMD2, the payload lib/maps_pack's lossless shuffle+zstd (2.3x smaller);
HMD1 (plain pickle) files still read. get_packed() hands back the compact bytes for poly_cache's compressed memory
tier, and the writer passes each entry's compact bytes to on_packed (poly_cache fills that tier from it), so the
memory form costs no extra compression on the paint thread. HOUSE_MOUTH_MAPS_PACK=0 writes HMD1 as before.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import queue
import threading
import time
from pathlib import Path

import maps_pack
import crissa_home

MAGIC = b"HMD1"
MAGIC2 = b"HMD2"


def pack_on(env=None) -> bool:
    env = os.environ if env is None else env
    return maps_pack.available() and str(env.get("HOUSE_MOUTH_MAPS_PACK", "1")).strip().lower() not in (
        "0", "false", "no", "off")
GB = 1 << 30
_HERE = Path(__file__).resolve().parent


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_MOUTH_MAPS_DISK", "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return not crissa_home.is_house()


def code_fingerprint(extra_sources: tuple[str, ...] = ()) -> str:
    """The modules a cheeks map is built by, plus the caller's own methods' source."""
    h = hashlib.blake2b(digest_size=10)
    for name in ("poly_cache.py", "head_frame.py", "pose_mouth.py", "fast_reduce.py", "maps_disk.py"):
        try:
            h.update((_HERE / name).read_bytes())
        except OSError:
            h.update(b"missing:" + name.encode())
    for s in extra_sources:
        h.update(s.encode("utf-8", "replace"))
    try:
        import numpy, cv2  # noqa: E401
        h.update(("np%s cv%s" % (numpy.__version__, cv2.__version__)).encode())
    except Exception:  # noqa: BLE001
        pass
    return h.hexdigest()


def adopt(base: Path, fingerprint: str, log=print) -> bool:
    """A code change that does not change what a map IS (a cache's plumbing, a new caller argument) still renames the
    fingerprint, and the new directory starts cold. `<base>/adopt.json` {"from": old, "to": new}, written by hand with
    scripts/maps-disk-adopt.py after checking the change touches no map-building code, lets the new fingerprint take
    the old directory (one rename) the first time it starts. A missing or mismatched file adopts nothing."""
    try:
        import json
        rec = json.loads((Path(base) / "adopt.json").read_text(encoding="utf-8"))
        src, dst = Path(base) / str(rec["from"]), Path(base) / str(rec["to"])
        if str(rec["to"]) != fingerprint or not src.is_dir() or src == dst:
            return False
        if dst.exists():
            if any(dst.iterdir()):
                return False
            dst.rmdir()
        os.rename(src, dst)
        log("[maps-disk] adopted %s as %s (adopt.json)" % (src.name, dst.name))
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


# A KIT UPDATE LEAVES ITS OLD MAPS (2026-10-16, with the ascii trim): maps are filed under the code's fingerprint, and a
# new fingerprint never reads an old one's folder. Once per start, in a pack only, a sibling fingerprint folder nobody
# wrote to for TRIM_IDLE_S (and not adopt.json's source) is removed on a thread. Kill CRISSA_MAPS_TRIM_OLD=0.
TRIM_IDLE_S = 2 * 3600


def _newest(d: Path) -> float:
    best = 0.0
    try:
        best = d.stat().st_mtime
        for c in d.iterdir():
            best = max(best, c.stat().st_mtime)
    except OSError:
        pass
    return best


def trim_old_prints(base: Path, keep: str, now: float | None = None, idle_s: float = TRIM_IDLE_S, log=print) -> list:
    import json
    import shutil
    base = Path(base)
    now = time.time() if now is None else float(now)
    try:
        src = str(json.loads((base / "adopt.json").read_text(encoding="utf-8")).get("from") or "")
    except (OSError, ValueError, AttributeError):
        src = ""
    gone: list = []
    try:
        dirs = [d for d in base.iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return gone
    for d in dirs:
        if d.name in (keep, src) or now - _newest(d) < idle_s:
            continue
        try:
            shutil.rmtree(d)
            gone.append(d.name)
        except OSError:
            continue
    if gone:
        log("[maps-disk] trimmed %d folder(s) of an older fingerprint" % len(gone))
    return gone


def trim_old_on(env=None) -> bool:
    env = os.environ if env is None else env
    if str(env.get("CRISSA_MAPS_TRIM_OLD", "1")).strip().lower() in ("0", "false", "off", "no"):
        return False
    try:
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


class MapsDisk:
    def __init__(self, fingerprint: str, root: Path | None = None, cap_gb: float | None = None, log=print):
        if root is None and os.environ.get("HOUSE_MOUTH_MAPS_DISK_DIR"):
            root = os.environ["HOUSE_MOUTH_MAPS_DISK_DIR"]
        base = Path(root) if root is not None else crissa_home.HOME / "cache" / "mouth-maps"
        self.root = base / fingerprint
        self.base = base
        if cap_gb is None:
            try:
                cap_gb = float(os.environ.get("HOUSE_MOUTH_MAPS_DISK_GB", "8"))
            except ValueError:
                cap_gb = 8.0
        self.cap = int(max(0.1, cap_gb) * GB)
        self.log = log
        self.stats = {"hit": 0, "miss": 0, "bad": 0, "write": 0, "pruned": 0}
        adopt(base, fingerprint, log)
        self._q: queue.Queue = queue.Queue(maxsize=256)
        self._held = None
        self._lock = threading.Lock()
        self.on_packed = None                      # (mem_key, packed bytes) -> None, set by poly_cache
        self.root.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._writer, name="maps-disk", daemon=True).start()
        if trim_old_on():
            threading.Thread(target=trim_old_prints, args=(base, fingerprint), kwargs={"log": log},
                             daemon=True, name="maps-trim").start()

    @staticmethod
    def _name(key_bytes: bytes) -> str:
        return hashlib.blake2b(key_bytes, digest_size=16).hexdigest()

    def _path(self, key_bytes: bytes) -> Path:
        n = self._name(key_bytes)
        return self.root / n[:2] / (n + ".map")

    def _read(self, key_bytes: bytes):
        """(magic, payload) of a verified file, or None. Any doubt deletes the file and is a miss."""
        p = self._path(key_bytes)
        try:
            blob = p.read_bytes()
        except OSError:
            self.stats["miss"] += 1
            return None
        try:
            magic = blob[:4]
            if magic not in (MAGIC, MAGIC2):
                raise ValueError("magic")
            kl = int.from_bytes(blob[4:8], "little")
            k = blob[8:8 + kl]
            dig = blob[8 + kl:24 + kl]
            payload = blob[24 + kl:]
            if k != key_bytes or hashlib.blake2b(payload, digest_size=16).digest() != dig:
                raise ValueError("key or digest")
        except Exception:  # noqa: BLE001 - a bad file is deleted and read as a miss
            self._bad(p)
            return None
        self.stats["hit"] += 1
        try:
            os.utime(p, None)                    # least recently used goes first
        except OSError:
            pass
        return magic, payload, p

    def _bad(self, p: Path) -> None:
        self.stats["bad"] += 1
        try:
            p.unlink()
        except OSError:
            pass

    def get(self, key_bytes: bytes):
        """The stored (roi, extra), or None. Any doubt is a miss."""
        got = self.get_both(key_bytes)
        return None if got is None else got[0]

    def get_both(self, key_bytes: bytes):
        """(value, packed bytes or None), or None. A plain (HMD1) file comes back packed when packing is on."""
        r = self._read(key_bytes)
        if r is None:
            return None
        magic, payload, p = r
        try:
            if magic == MAGIC2:
                return maps_pack.unpack(payload), payload
            value = pickle.loads(payload)
        except Exception:  # noqa: BLE001
            self.stats["hit"] -= 1
            self._bad(p)
            return None
        return value, (maps_pack.pack(value) if pack_on() else None)

    def put(self, key_bytes: bytes, value, mem_key=None) -> None:
        try:
            self._q.put_nowait((key_bytes, value, mem_key))
        except queue.Full:
            pass                                 # a busy disk drops a write, never a paint

    def _writer(self) -> None:
        n = 0
        while True:
            key_bytes, value, mem_key = self._q.get()
            try:
                packed = maps_pack.pack(value) if pack_on() else None
                payload = packed if packed is not None else pickle.dumps(value, protocol=5)
                blob = ((MAGIC2 if packed is not None else MAGIC) + len(key_bytes).to_bytes(4, "little") + key_bytes
                        + hashlib.blake2b(payload, digest_size=16).digest() + payload)
                p = self._path(key_bytes)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp%d" % os.getpid())
                tmp.write_bytes(blob)
                os.replace(tmp, p)
                self.stats["write"] += 1
                if packed is not None and mem_key is not None and self.on_packed is not None:
                    self.on_packed(mem_key, packed)
                with self._lock:
                    if self._held is not None:
                        self._held += len(blob)
                n += 1
                if n % 64 == 1:
                    self.prune()
            except Exception as e:  # noqa: BLE001 - the disk tier never breaks a paint
                self.log("[maps-disk] write failed: %s" % e)

    def prune(self) -> None:
        """Over budget: the least recently used files go, other fingerprints' directories first."""
        try:
            files = []
            for p in self.base.rglob("*.map"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append((p.parent.parent != self.root, st.st_mtime, st.st_size, p))
            held = sum(f[2] for f in files)
            with self._lock:
                self._held = held
            if held <= self.cap:
                return
            files.sort(key=lambda f: (not f[0], f[1]))   # other code first, then oldest
            for other, _mt, size, p in files:
                if held <= int(self.cap * 0.9):
                    break
                try:
                    p.unlink()
                    held -= size
                    self.stats["pruned"] += 1
                except OSError:
                    pass
            with self._lock:
                self._held = held
        except Exception as e:  # noqa: BLE001
            self.log("[maps-disk] prune failed: %s" % e)

    def state(self) -> dict:
        return dict(self.stats, root=str(self.root), held_mb=round((self._held or 0) / 1e6, 1),
                    cap_gb=round(self.cap / GB, 1), queued=self._q.qsize())
