"""The wall's ASCII picture, remembered (the User 2026-10-11: "ascii cache sounds like a huge win with zero downside
... It is what we already do painting the mouth live but no need to paint cached loops").

The film child (lib/wall_film_proc.py) asks this module for each frame's COVERAGE: the L picture render_background
paints through the ember palette. Exact by construction, never "close enough":

- The reference is the fresh render, `R.coverage_fresh`: the glow made for this frame. render_background reuses the
  previous frame's glow every other call (`R._GLOW`), so it is not a function of its frame; this is. Every picture
  this module hands out equals `R.coverage_fresh` of its frame, byte for byte.
- A picture is keyed on the glyph index grid (`R.ascii_idx`: ROWS x COLS, everything the picture knows about the
  frame) plus the geometry and the glow switch, and the grid's own bytes are kept and compared on every hit, so a
  hash collision cannot hand out a wrong picture. In front of it, the JPEG's own bytes (kept, compared) name the grid
  without decoding: same bytes, same pixels, same grid.
- A frame whose grid differs from a remembered one (or from the previous picture) in a small block of cells - a
  mouth step - is drawn as a REGION: the block, padded by everything that reaches across it (the NEAREST map from
  the glyph sheet, the 8x8 BOX of the glow, the blur's three passes, the bilinear upsample), is drawn with the same
  PIL operations on crops and laid over the remembered picture; outside the padded block the two frames' masks,
  glow inputs and glows are equal, so the pictures are. It runs only where the arithmetic is position-independent
  (width and height multiples of 8), after a self-check per geometry spread over its first frames, and spot-checks
  itself against a whole render (1 in VERIFY_EVERY, the first 4 always): any mismatch turns the cache off until
  restart and says so.

Memory: a picture is W*H bytes (2560x1440: 3.7 MB). The budget is the smaller of HOUSE_WALL_ASCII_CACHE_MB (4096;
1024 until 2026-10-12) and 10 % of available memory (a Mac: vm_stat's free + inactive pages, mac_available), re-read
every 5 s; under HOUSE_WALL_ASCII_CACHE_FLOOR_MB (1024) of free memory it gives back what it holds. Eviction is
least-recently-used, but only of a picture unused for STALE_S: a loop longer than the budget keeps the part that fits
instead of evicting each frame just before it comes round again.
A picture of another FRAMING (geometry, density, glow, zoom, crop slide) can never be handed out while this one is
on the glass, so it goes first, stale or not (round 12, 2026-10-12: on .101 a density change left 1 GB of the old
grid's pictures held and every new picture refused for a minute; its 1 GB held 204 of the listen loop's 403
pictures at 3440x1440, so half of every lap was drawn whole).

Compressed (round 17, 2026-10-13): in a pack the memory tier holds each picture as the disk tier's zstd-1 bytes and
unpacks it when it is drawn (ZMEM below; the house's stays raw unless HOUSE_WALL_ASCII_CACHE_ZMEM=1).

Switch: HOUSE_WALL_ASCII_CACHE = on | fallback | off (off is a kill: the file below is ignored); the live switch file
<state home>/wall-ascii-cache, when present, names the mode (empty = on). `fallback` engages only while the wall
cannot hold the clip's rate (decided by the wall's film thread).
"""
from __future__ import annotations

import collections
import hashlib
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import weakref
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageChops, ImageFilter

import house_wall_ascii_render as R
import crissa_home

MODES = ("on", "fallback", "off")
DEFAULT_MODE = "on"
SWITCH_FILE = crissa_home.HOME / "wall-ascii-cache"
STALE_S = float(os.environ.get("HOUSE_WALL_ASCII_CACHE_STALE_S", "60"))
# THE BUDGET HOLDS A LOOP (round 12, 2026-10-12): 4096 MB, still capped at 10 % of available memory (budget()). A
# 3440x1440 picture is 5 MB, so 1024 MB held 204 of the listen loop's 403 frames; measured under the .101-like budget
# with the loop held, a speaking film frame is a region over its own idle picture (20.8 ms) instead of a whole render
# (38 ms) on half the lap. The house's unit sets HOUSE_WALL_ASCII_CACHE_MB=4096 itself (since 2026-10-11).
DEFAULT_MB = 4096
VERIFY_EVERY = int(os.environ.get("HOUSE_WALL_ASCII_CACHE_VERIFY", "64") or 0)
# A region larger than this share of the glass is drawn whole (cheaper). 0.16 since 2026-10-11 (was 0.08): the
# close-up's mouth paint reaches 13-14 % of the glass with its jaw (pose_mouth.hide_clip_jaw), and measured under the
# .101-like budget at 3440x1440 d2.08 such a region is 15.9 ms against 32.7 ms whole.
REGION_MAX = 0.16
BANDS = 12                 # the grid's row bands, for finding a remembered picture that differs in a few
# ... bands at most. 6 since 2026-10-11 (was 3): the close-up's jaw paint spans 4-5 of the 12 bands, so its own film
# frame was never found as a base and every speaking picture there was drawn whole. More slack only tries more
# candidates (the top 3 by votes, each diffed); what is drawn stays exact.
BAND_SLACK = 6
BLUR_REACH = 4             # small px a change spreads through the glow blur (3 box passes of radius < 1: 3)
UP_REACH = 2               # small px the bilinear x8 upsample reads on either side (support 1: <= 2)
MIN_PICTURES = 24          # a budget that holds fewer than this is no cache: off

# THE MEMORY TIER HOLDS PICTURES COMPRESSED (round 17, 2026-10-13; the User via .101: "keep the memory tier compressed").
# .101, three minutes into a long reading at density 2, 3440x1440: `held=4092MB of 4096MB pictures=816 refused=813`. A
# picture held unpacked is W*H bytes (4,953,600 there); the disk tier keeps the same picture as zstd level 1 (~1.36 MB a
# picture over .101's library). With ZMEM a kept picture is held as those zstd-1 bytes - the disk tier's own codec
# (wall_ascii_disk._zstd: 3.14's compression.zstd, else the zstandard wheel the kit installs) - and unpacked when it is
# drawn, so the same budget holds ~3.6x the pictures. Nothing is compressed on the film path: a picture read from the
# disk tier arrives as its file's bytes; a whole-drawn one is held unpacked (and counted so) until the disk writer, which
# compresses it anyway, hands its bytes back, or this module's own squeeze thread does (a picture the disk does not
# take). The last RAW_KEEP pictures drawn stay unpacked beside it (RAW_KEEP below): a mouth step's base and the picture
# the next one is diffed against are not unpacked twice. Exact as before: an unpacked picture is the bytes it was. No
# zstd in this Python: today's raw tier. On in a pack; on the house only with HOUSE_WALL_ASCII_CACHE_ZMEM=1 (its Wall
# restarts through its window). Kill: HOUSE_WALL_ASCII_CACHE_ZMEM=0.
RAW_KEEP = 4
SQUEEZE_Q = 16              # whole pictures waiting to be packed (a first lap draws at most 24/s; packing one is ms)


# WHY A PICTURE WAS DRAWN FRESH (round 45, .101 an hour after the build: "2 of the last 130 pictures drawn fresh" at a
# clip's first play): one line per fresh picture - the clip, its frame and the reason (no clip named / no disk tier /
# not built / entry unreadable / key: which field differs from the built entry / region: why no region) - at most
# FRESH_WHY_PER_MIN lines a minute and each clip frame once a process, the rest counted on the next line. The cap is a
# dial, not a measurement: a whole clip drawn fresh is 24 lines a second, and 20 a minute still shows every picture of
# an ordinary clip switch (0-12 of them) while keeping such a storm to a few lines. HOUSE_WALL_FRESH_WHY=1|0 (unset = on in
# a pack, off on the house); HOUSE_WALL_FRESH_WHY_PER_MIN.
FRESH_WHY_PER_MIN = 20
_FW = {"t0": 0.0, "n": 0, "held": 0, "seen": set()}


def _frame_point_pack() -> bool:
    """A pack (the Wall draws each frame about its own clip's point there by default, round 45)."""
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


def fresh_why_on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_FRESH_WHY", "")).strip().lower()
    if raw:
        return raw not in ("0", "false", "off", "no")
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


def fresh_why(log, clip, why: str, env=None, now=None) -> bool:
    """Say why this clip frame's picture was drawn fresh (rate-limited; see FRESH_WHY_PER_MIN). True when said."""
    env = os.environ if env is None else env
    if log is None or not fresh_why_on(env):
        return False
    key, fr = (clip[0], int(clip[1])) if isinstance(clip, tuple) and len(clip) >= 2 else ("-", -1)
    k = (key, fr, why.split(":", 1)[0])
    if k in _FW["seen"]:
        return False
    now = time.monotonic() if now is None else now
    if now - _FW["t0"] >= 60.0:
        _FW.update(t0=now, n=0)
    try:
        cap = int(env.get("HOUSE_WALL_FRESH_WHY_PER_MIN", FRESH_WHY_PER_MIN))
    except ValueError:
        cap = FRESH_WHY_PER_MIN
    if _FW["n"] >= cap:
        _FW["held"] += 1
        return False
    _FW["n"] += 1
    _FW["seen"].add(k)
    if len(_FW["seen"]) > 50000:
        _FW["seen"].clear()
    held, _FW["held"] = _FW["held"], 0
    log("ascii fresh: %s frame %d: %s%s" % (key, fr, why, (" (+%d not shown)" % held) if held else ""))
    return True


def glass_first_enabled(env=None) -> bool:
    """AsciiCache._evict_for_glass: =0 is a kill, =1 asks for it (the house), unset = on in a pack."""
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_ASCII_CACHE_GLASS_FIRST", "")).strip().lower()
    if raw in ("0", "off", "false", "no"):
        return False
    return raw in ("1", "on", "true", "yes") or not crissa_home.is_house()


def zmem_enabled(env=None) -> bool:
    """Hold the memory tier as zstd-1 bytes? =0 is a kill, =1 asks for it (the house), unset = on in a pack; never
    without zstd in this Python."""
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_ASCII_CACHE_ZMEM", "")).strip().lower()
    if raw in ("0", "off", "false", "no"):
        return False
    if raw not in ("1", "on", "true", "yes") and crissa_home.is_house():
        return False
    return _zstd_mod() is not None


def _zstd_mod():
    try:
        import wall_ascii_disk
        return wall_ascii_disk._zstd()
    except Exception:  # noqa: BLE001 - no disk module or no zstd: the raw tier
        return None


class _Zstd:
    """zstd level 1 both ways (the disk tier's format). One per thread: a context is not shared across threads."""

    def __init__(self):
        self.kind, self.z = _zstd_mod()
        if self.kind == "wheel":
            self._c = self.z.ZstdCompressor(level=1)
            self._d = self.z.ZstdDecompressor()

    def pack(self, raw) -> bytes:
        return self.z.compress(raw, 1) if self.kind == "std" else self._c.compress(raw)

    def unpack(self, data, size: int) -> bytes:
        return self.z.decompress(data) if self.kind == "std" else self._d.decompress(data, max_output_size=size)


def mode(env=None) -> str:
    """on | fallback | off. HOUSE_WALL_ASCII_CACHE=off is a kill; else the switch file's word; else the env word."""
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_ASCII_CACHE", "")).strip().lower()
    if raw in ("0", "off", "false", "no"):
        return "off"
    try:
        word = SWITCH_FILE.read_text(encoding="utf-8").split()
        return word[0].lower() if word and word[0].lower() in MODES else "on"
    except OSError:
        pass
    return raw if raw in MODES else DEFAULT_MODE


def mem_available(env=None) -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "darwin":
        return mac_available(env)
    return None


# A MAC HAS NO /proc/meminfo (2026-10-13): mem_available() read only Linux's MemAvailable, so on a Mac it answered None
# and budget() gave the kit's desk Wall no memory tier at all (the kit never sets HOUSE_WALL_ASCII_CACHE_MB): no
# remembered picture, and no region over its idle picture (20.8 ms against a 38 ms whole render, measured on .101). On
# darwin it reads vm_stat: (Pages free + Pages inactive) x the page size its first line names ("page size of 16384
# bytes" on Apple silicon) - psutil's macOS `available` less the speculative pages vm_stat lists apart. Not yet run on a
# Mac, and not enough alone there: this cache lives in the film child, which starts on a Mac only with its frame file
# outside /dev/shm (lib/wall_film_proc.py frame_dir, same day), and a region needs W and H multiples of GLOW_Q
# (_Geo.possible). One reading serves MAC_MEM_TTL_S (budget() is re-read every 5 s; this bounds any second caller); a
# failed one is held MAC_MEM_FAIL_S, so a vm_stat that hangs costs its 1 s timeout once a minute, not every 5 s. The
# call is posix_spawn-shaped (absolute path, close_fds=False: every fd Python opens is non-inheritable) because the
# film child may hold GBs and a fork() copies their page tables. Kill: HOUSE_WALL_ASCII_CACHE_MAC_MEM=0 (None on a
# Mac, as before).
VM_STAT = "/usr/bin/vm_stat"
MAC_MEM_TTL_S = 4.0
MAC_MEM_FAIL_S = 60.0
_MAC_MEM: dict = {"at": None, "val": None}


def parse_vm_stat(text: str) -> int | None:
    """(Pages free + Pages inactive) x page size from vm_stat's output. None when any of the three is missing."""
    m = re.search(r"page size of (\d+) bytes", text)
    pages: dict = {}
    for line in text.splitlines():
        key, sep, val = line.partition(":")
        val = val.strip().rstrip(".")
        if sep and val.isdigit():
            pages[key.strip()] = int(val)
    if not m or "Pages free" not in pages or "Pages inactive" not in pages:
        return None
    return (pages["Pages free"] + pages["Pages inactive"]) * int(m.group(1))


def mac_available(env=None, run=None, now=None) -> int | None:
    """Available memory on a Mac, from vm_stat; None when it cannot be read or the kill is set."""
    env = os.environ if env is None else env
    if str(env.get("HOUSE_WALL_ASCII_CACHE_MAC_MEM", "1")).strip().lower() in ("0", "off", "false", "no"):
        return None
    now = time.monotonic() if now is None else now
    at, val = _MAC_MEM["at"], _MAC_MEM["val"]
    if at is not None and now - at < (MAC_MEM_TTL_S if val is not None else MAC_MEM_FAIL_S):
        return val
    val = None
    exe = VM_STAT if os.path.exists(VM_STAT) else shutil.which("vm_stat")
    if exe:
        try:
            r = (run or subprocess.run)([exe], capture_output=True, text=True, timeout=1.0, close_fds=False)
            val = parse_vm_stat(r.stdout) if r.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            val = None
    _MAC_MEM["at"], _MAC_MEM["val"] = now, val
    return val


# THE CAP FOLLOWS THE MACHINE (round 26, .101 2026-10-16: `held=4096MB of 4096MB` and new clips came fresh on a machine
# with 91 GB; the User: "why isn't wall ascii cached?"). With no HOUSE_WALL_ASCII_CACHE_MB set (a pack), the cap is
# RAM_SHARE of the machine's memory (MemTotal) when that is more than DEFAULT_MB - 10 % of 91 GB is 9.1 GB; a 16 GB
# laptop keeps 4096 MB. The 10 % of AVAILABLE memory cap below still holds, as before. The house's unit sets its own MB.
# Kill: HOUSE_WALL_ASCII_CACHE_RAM_SHARE=0 (DEFAULT_MB everywhere).
RAM_SHARE = 0.10


def mem_total(env=None) -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def default_mb(env=None, total: int | None = -1) -> float:
    env = os.environ if env is None else env
    try:
        share = float(env.get("HOUSE_WALL_ASCII_CACHE_RAM_SHARE", str(RAM_SHARE)))
    except ValueError:
        share = RAM_SHARE
    total = mem_total(env) if total == -1 else total
    if share <= 0 or not total:
        return float(DEFAULT_MB)
    return max(float(DEFAULT_MB), share * total / 2 ** 20)


def budget(held: int = 0, env=None, avail: int | None = -1, total: int | None = -1) -> int:
    """Bytes the cache may hold now. `held` is what it holds (available memory already excludes it)."""
    env = os.environ if env is None else env
    explicit = "HOUSE_WALL_ASCII_CACHE_MB" in env
    try:
        mb = float(env["HOUSE_WALL_ASCII_CACHE_MB"]) if explicit else default_mb(env, total)
        floor = float(env.get("HOUSE_WALL_ASCII_CACHE_FLOOR_MB", "1024")) * 2 ** 20
    except ValueError:
        return 0
    avail = mem_available(env) if avail == -1 else avail
    if avail is None:
        return int(mb * 2 ** 20) if explicit else 0      # no way to see free memory: only on the owner's word
    cap = min(mb * 2 ** 20, 0.10 * (avail + held))
    if avail < floor:                                    # memory is short: give back what is short
        cap = min(cap, max(0.0, held - (floor - avail)))
    return int(max(0.0, cap))


# THE GLOW'S INPUT, BOXED IN A FIXED ORDER (.101 round 46, 2026-10-24: "a region answer differed from the fresh render at
# 3440x1440 (band base, box (1224, 200, 1624, 1016))", twice, the second time 2 min after a restart). Pillow's BOX resize
# runs two passes with a rounding between them, and Pillow 13 picks the pass order from the picture's shape: a TALL crop
# (her head and mouth: 400 x 816) is boxed vertical-first, the wide glass horizontal-first, so the region's changed
# blocks came out up to a level off the whole picture's (Pillow 12.3, the house's, keeps one order: never seen here).
# Two explicit passes, horizontal then vertical, are the whole glass's own order: byte-equal to glow_small on Pillow 12.3
# and 13.0 at every glass, and every crop's equal to its part of it. Measured: docs/checkpoints/wall-cache-3440-20261024.
# Kill HOUSE_WALL_ASCII_REGION_BOX2=0 (the one-call resize; on Pillow 13 the self-check then turns regions off).
BOX2 = os.environ.get("HOUSE_WALL_ASCII_REGION_BOX2", "1").strip().lower() not in ("0", "false", "no", "off")


def _box_down(im: Image.Image, w: int, h: int) -> Image.Image:
    if not BOX2:
        return im.resize((w, h), Image.Resampling.BOX)
    return im.resize((w, im.size[1]), Image.Resampling.BOX).resize((w, h), Image.Resampling.BOX)


# THE CHILD'S OWN SIZE ON THE STATS LINE (.101 round 46 O3, 2026-10-24: the film child held RSS 15.9 GB while this line
# said held=5032MB; the gap was only seen in a separate memlog). The resident size of the process the cache lives in
# (Linux /proc; elsewhere None, and the line leaves it out), read when the stats are, every few seconds.
def _own_rss() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _canon_lut(pal: bytes) -> list:
    """L value -> the first L value with the same RGB: two coverages paint the same pixel iff their canon is equal."""
    first: dict = {}
    out = []
    for i in range(256):
        out.append(first.setdefault(pal[3 * i:3 * i + 3], i))
    return out


class Pic:
    """One coverage picture, exact for its grid. `px` is the coverage's bytes (what the frame file carries): held as
    they are, or (ZMEM) as zstd-1 bytes `z` that `px` unpacks through its cache's raw LRU."""
    __slots__ = ("serial", "gk", "_idx", "zidx", "nidx", "idk", "_raw", "z", "zm", "size", "small", "bands", "nbytes",
                 "used", "kept", "fk", "rows", "clip", "__weakref__")

    def __init__(self, serial, gk, idx: bytes, cov, small, bands, fk=None, rows: int = 0, z=None, zm=None):
        self.serial, self.gk, self._idx, self.small, self.bands = serial, gk, idx, small, bands
        self.zidx, self.nidx, self.idk = None, len(idx), None     # the grid's zstd bytes (ZMEM), its length, digest
        self.fk, self.rows = fk, rows               # the framing it was drawn for; the grid's rows
        self.clip = None                            # (key, frame, role) when the feed named it (X-Clip, round 13)
        if isinstance(cov, tuple):                  # (bytes-like, (w, h)): a region drawn straight into its bytes
            self._raw, self.size = cov
        else:
            self._raw, self.size = cov.tobytes(), cov.size
        self.z, self.zm = z, zm                     # zstd-1 bytes (ZMEM) and a weak ref to the cache that unpacks them
        self.nbytes = self.held() + len(idx) + (small.size[0] * small.size[1] if small else 0) + 512
        self.used = time.monotonic()
        self.kept = False

    def held(self) -> int:
        """What the picture itself costs the memory tier: its zstd bytes when it has them, else its pixels."""
        return len(self.z) if self.z is not None else len(self._raw)

    def held_idx(self) -> int:
        return len(self.zidx) if self.zidx is not None else self.nidx

    @property
    def idx(self) -> bytes:
        """The glyph grid: as drawn, or (ZMEM) unpacked from its zstd bytes (~25x smaller: 713 KB -> 30 KB at 3440x1440
        density 5; 0.16 ms to unpack)."""
        r = self._idx
        if r is None:
            c = self.zm() if self.zm is not None else None
            return c.unpack_idx(self) if c is not None else _Zstd().unpack(self.zidx, self.nidx)
        return r

    def digest(self) -> bytes:
        """The grid's by_idx key (kept on the picture: an eviction does not hash the grid again)."""
        if self.idk is None:
            self.idk = hashlib.blake2b(self.idx, digest_size=16).digest()
        return self.idk

    @property
    def px(self):
        r = self._raw
        c = self.zm() if self.zm is not None else None
        if r is None:
            return c.unpack(self) if c is not None else _Zstd().unpack(self.z, self.size[0] * self.size[1])
        if c is not None and self.z is not None:
            c.raw_touch(self)
        return r

    @px.setter
    def px(self, v) -> None:
        self._raw, self.z = v, None

    def image(self) -> Image.Image:
        """The coverage as an L picture (read-only, shares px)."""
        return Image.frombuffer("L", self.size, self.px, "raw", "L", 0, 1)


class _Geo:
    """What a geometry needs for region drawing. Regions stay off until AsciiCache._setup has checked them."""

    def __init__(self, g):
        self.W, self.H = g.W, g.H
        self.cw, self.ch = R.cell_px(g)
        self.COLS, self.ROWS = g.COLS, g.ROWS
        self.atlas = R._glyph_atlas(g.ascii_fs, self.cw, self.ch)
        SW, SH = self.COLS * self.cw, self.ROWS * self.ch
        if (SW, SH) == (self.W, self.H):
            self.mapx, self.mapy = np.arange(self.W), np.arange(self.H)
        else:
            self.mapx = self._nearest_map(SW, self.W, horizontal=True)
            self.mapy = self._nearest_map(SH, self.H, horizontal=False)
        self.vinv = R._vignette_inv(self.W, self.H)
        q = R.GLOW_Q
        self.possible = (self.W % q == 0 and self.H % q == 0 and bool(np.all(np.diff(self.mapx) >= 0))
                         and bool(np.all(np.diff(self.mapy) >= 0)))
        self.region_ok = False
        self.setup = None                       # the checks still to run, one per frame (AsciiCache._setup)
        self.verified = 0

    @staticmethod
    def _nearest_map(n_src: int, n_out: int, horizontal: bool):
        line = np.arange(n_src, dtype=np.int32)
        im = Image.fromarray(line[None, :] if horizontal else line[:, None], "I")
        out = im.resize((n_out, 1) if horizontal else (1, n_out), Image.Resampling.NEAREST)
        return np.asarray(out, dtype=np.int64).reshape(-1)

    def axis_matches(self, SW: int, SH: int, horizontal: bool) -> bool:
        """PIL's 2-D NEAREST of a sheet takes every glass pixel's column (row) from mapx (mapy), on this Pillow at
        this size: a sheet whose every pixel holds its own column (row) number, resized, read back everywhere."""
        line = np.arange(SW if horizontal else SH, dtype=np.int32)
        code = np.ascontiguousarray(np.broadcast_to(line[None, :] if horizontal else line[:, None], (SH, SW)))
        got = np.asarray(Image.fromarray(code, "I").resize((self.W, self.H), Image.Resampling.NEAREST))
        want = (self.mapx[None, :] if horizontal else self.mapy[:, None]).astype(np.int32)
        return bool((got == want).all())

    def random_matches(self, SW: int, SH: int) -> bool:
        """... and an L sheet goes the same way (the glyph sheet's own mode)."""
        rnd = np.random.default_rng(7).integers(0, 256, (SH, SW), dtype=np.uint8)
        got8 = np.asarray(Image.fromarray(rnd, "L").resize((self.W, self.H), Image.Resampling.NEAREST))
        return bool(np.array_equal(got8, rnd[self.mapy][:, self.mapx]))

    def mask(self, idx2d, x0: int, x1: int, y0: int, y1: int):
        """mask_from_idx's pixels in [x0, x1) x [y0, y1), uint8."""
        sx, sy = self.mapx[x0:x1], self.mapy[y0:y1]
        k = idx2d[(sy // self.ch)[:, None], (sx // self.cw)[None, :]]
        return self.atlas[k, (sy % self.ch)[:, None], (sx % self.cw)[None, :]]


READ_AHEAD_N = int(os.environ.get("HOUSE_WALL_ASCII_READ_AHEAD_N", "6"))
READ_AHEAD_S = float(os.environ.get("HOUSE_WALL_ASCII_READ_AHEAD_MS", "6")) / 1000.0


def read_ahead_on(env=None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_ASCII_READ_AHEAD", "")).strip().lower()
    if raw:
        return raw not in ("0", "false", "off", "no")
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001
        return False


class AsciiCache:
    """Coverage pictures for the film child. picture() -> (Pic, how); how = bytes | grid | region | full."""

    def __init__(self, log=None, env=None):
        self.log = log or (lambda m: None)
        self.env = os.environ if env is None else env
        self._disk_prefill = False          # the last entry _from_disk read was a pre-fill's (round 32)
        self._rbase = ""                    # round 40 N16: the region answer being checked, its base (MISMATCH line)
        self._disk_hdr = None               # round 45: (frame, mouth, jpeg) of the last entry read from disk
        self._region_why = ""               # round 45: why the last _region gave no region
        self.pics: OrderedDict = OrderedDict()      # serial -> Pic, least recently used first
        self.by_idx: dict = {}                      # (gk, digest of grid) -> serial
        self.by_band: dict = {}                     # (gk, band, digest) -> {serial}
        self.by_jpeg: OrderedDict = OrderedDict()   # (geometry + framing, digest of JPEG) -> (jpeg, serial)
        self.jpeg_bytes = 0
        self.boxes: OrderedDict = OrderedDict()     # (serial, serial, palette digest) -> box
        self.canon: dict = {}
        self.canon_last = None                      # (serial, palette digest, region, canon picture): the next prev
        self.geos: dict = {}
        self.serial = 0
        self.bytes = 0
        self.cap = budget(0, self.env)
        self.cap_at = time.monotonic()
        self.dead = ""                              # a failed self-check: off until restart
        self.verify_n = 0
        self.n = dict(bytes=0, grid=0, region=0, full=0, kept=0, evicted=0, refused=0, verified=0, reframed=0,
                      disk=0, disk_region=0, passed=0, glass=0)
        self.glass_first = glass_first_enabled(self.env)   # the clip on the glass before a fresh picture of another
        self.fk_now = None                          # the framing on the glass (the last picture asked for)
        self.clip_now = None                        # the clip on the glass (X-Clip's key), round 13
        self.by_clip: dict = {}                     # (framing, clip, frame) -> serial of a kept picture, round 13
        self.zmem = zmem_enabled(self.env)          # hold pictures as zstd-1 bytes (round 17)
        self._z = _Zstd() if self.zmem else None    # this thread's (the film child's) zstd
        self._wref = weakref.ref(self) if self.zmem else None   # what a picture holds: no cycle, freed as before
        self.raw: OrderedDict = OrderedDict()       # serial -> Pic: the unpacked copies kept beside their zstd bytes
        self.raw_bytes = 0
        self.zdone = collections.deque()            # (Pic, zstd bytes) from the disk writer or the squeeze thread
        self.zq = None                              # the squeeze thread's queue, started on first use
        self.last = None                            # the last picture handed out
        self.zn = dict(unpacks=0, idx_unpacks=0, squeezed=0, writer=0, behind=0)
        self.zms: collections.deque = collections.deque(maxlen=512)   # recent unpack times, ms
        self.disk = None                            # the disk tier (lib/wall_ascii_disk.py), when on
        self.rss0 = _own_rss()                      # the process before its first picture: the harness (O3)
        try:
            import wall_ascii_disk
            if wall_ascii_disk.enabled(self.env):
                self.disk = wall_ascii_disk.DiskTier(log=self.log, env=self.env)
        except Exception as e:  # noqa: BLE001 - no disk tier: the memory tier as it was
            self.disk = None
            self.log("ascii disk: off (%s: %s)" % (type(e).__name__, e))

    # ---- budget -------------------------------------------------------------------------------------------------
    def usable(self) -> bool:
        if self.dead:
            if self.pics or self.by_jpeg or self.raw or self.last is not None:
                self.release()
            return False
        now = time.monotonic()
        if now - self.cap_at >= 5.0:
            self.cap_at = now
            self.cap = budget(self.bytes + self.jpeg_bytes + self.raw_bytes, self.env)
            self._fit(force=True)
        return not self.dead and self.cap > 0

    def held_bytes(self) -> int:
        return self.bytes + self.jpeg_bytes + self.raw_bytes

    # AN OFF CACHE HOLDS NOTHING (.101 round 46 O2, 2026-10-24: after the MISMATCH the stats said `held=5032MB of 6332MB
    # pictures=4389` for 3.7 h, serving nothing). Whatever turns the cache off - a failed check, a fault the film child
    # catches (it sets `dead`) - the next usable() gives every picture, JPEG name and unpacked copy back. The disk tier
    # keeps its files: they are this code's own and the next start reads them. Kill HOUSE_WALL_ASCII_CACHE_RELEASE=0.
    def release(self) -> None:
        if os.environ.get("HOUSE_WALL_ASCII_CACHE_RELEASE", "1").strip() == "0":
            return
        held = self.held_bytes()
        for coll in (self.pics, self.by_idx, self.by_band, self.by_jpeg, self.by_clip, self.raw, self.boxes,
                     self.canon):
            coll.clear()
        self.zdone.clear()
        self.canon_last = self.last = None
        self.bytes = self.jpeg_bytes = self.raw_bytes = 0
        self.log("ascii cache: off, %d MB given back" % (held >> 20))

    # ---- ZMEM: the zstd bytes, the raw LRU, the squeeze ---------------------------------------------------------
    def unpack(self, p: Pic):
        """p's pixels from its zstd bytes; the copy joins the raw LRU."""
        t0 = time.perf_counter()
        raw = self._z.unpack(p.z, p.size[0] * p.size[1])
        self.zms.append((time.perf_counter() - t0) * 1000.0)
        self.zn["unpacks"] += 1
        p._raw = raw
        self._raw_add(p)
        return raw

    def unpack_idx(self, p: Pic) -> bytes:
        """p's grid from its zstd bytes; kept unpacked while p is in the raw LRU."""
        raw = self._z.unpack(p.zidx, p.nidx)
        self.zn["idx_unpacks"] += 1
        if p.serial in self.raw:
            p._idx = raw
            self._raw_count()
        return raw

    @staticmethod
    def _lru_size(q: Pic) -> int:
        return ((len(q._raw) if (q.z is not None and q._raw is not None) else 0)
                + (len(q._idx) if (q.zidx is not None and q._idx is not None) else 0))

    def _raw_count(self) -> None:
        self.raw_bytes = sum(self._lru_size(q) for q in self.raw.values())

    def raw_touch(self, p: Pic) -> None:
        if p.serial in self.raw:
            self.raw.move_to_end(p.serial)

    def _raw_add(self, p: Pic) -> None:
        """Keep p's unpacked copy as the most recent of RAW_KEEP; the oldest gives its copy back."""
        if p.serial in self.raw:
            self.raw.move_to_end(p.serial)
            return
        self.raw[p.serial] = p
        while len(self.raw) > RAW_KEEP:
            self._raw_drop(next(iter(self.raw)), count=False)
        self._raw_count()

    def _raw_drop(self, serial, count: bool = True) -> None:
        q = self.raw.pop(serial)
        if q.z is not None:
            q._raw = None
        if q.zidx is not None:
            q._idx = None
        if count:
            self._raw_count()

    def _squeeze_later(self, p: Pic, px: bool = True) -> None:
        """p (kept, held unpacked) to the squeeze thread; zstd releases the GIL, so the film thread does not wait. The
        thread holds the cache and the pictures weakly: a picture gone before its turn is skipped, and the thread ends
        with its cache (a test makes dozens of caches; the film child one)."""
        if self.zq is None:
            self.zq = queue.Queue(maxsize=SQUEEZE_Q)
            threading.Thread(target=AsciiCache._squeezer, args=(weakref.ref(self), self.zq), daemon=True,
                             name="ascii-zmem").start()
        try:
            self.zq.put_nowait((weakref.ref(p), p._raw if (px and p.z is None) else None,
                                p._idx if p.zidx is None else None))
        except queue.Full:
            self.zn["behind"] += 1                  # far behind: this one stays unpacked (counted so) while held

    @staticmethod
    def _squeezer(cref, q) -> None:
        z = _Zstd()
        while True:
            try:
                pref, raw, idx = q.get(timeout=2.0)
            except queue.Empty:
                if cref() is None:
                    return
                continue
            p = pref()
            if p is None or not p.kept:
                continue                            # evicted or gone meanwhile
            try:
                zb = z.pack(raw) if (raw is not None and p.z is None) else None
                zi = z.pack(idx) if idx is not None else None
            except Exception:  # noqa: BLE001 - a picture that cannot be packed stays unpacked
                continue
            c = cref()
            if c is None:
                return
            c.zdone.append((p, zb, zi, False))
            del c, p

    def _writer_done(self, p: Pic):
        """The disk writer's hook: the zstd bytes it wrote for p come back here (the same codec, the same level)."""
        def done(zb, p=p, d=self.zdone):
            d.append((p, bytes(zb), None, True))
        return done

    def _drain(self) -> None:
        """Swap in the zstd bytes that arrived since the last picture (on the film thread: nothing else touches the
        budget). The picture handed out last keeps its unpacked copy in the raw LRU; the others give theirs back."""
        while self.zdone:
            p, zb, zi, from_writer = self.zdone.popleft()
            if not p.kept:
                continue                            # evicted while it was packed: nothing to hold
            self.bytes -= p.nbytes
            if zb is not None and p.z is None and p._raw is not None and len(p._raw) == p.size[0] * p.size[1]:
                p.nbytes += len(zb) - len(p._raw)
                p.z = zb
                self.zn["writer" if from_writer else "squeezed"] += 1
                if p is self.last:
                    self._raw_add(p)
                elif p.serial not in self.raw:
                    p._raw = None
            if zi is not None and p.zidx is None and p._idx is not None and len(p._idx) == p.nidx:
                p.nbytes += len(zi) - p.nidx
                p.zidx = zi
                if p.serial not in self.raw:
                    p._idx = None
            self.bytes += p.nbytes
            self._raw_count()
            if from_writer and p.zidx is None:
                self._squeeze_later(p, px=False)    # the grid: the writer packs only the pixels

    def holds(self, W: int, H: int) -> bool:
        """A budget too small to hold a second of film is no cache."""
        return self.cap >= MIN_PICTURES * W * H

    def _fit(self, force: bool = False) -> None:
        """The budget shrank (force) or the JPEG names grew: least recently used first."""
        while self.by_jpeg and (len(self.by_jpeg) > max(64, 4 * len(self.pics))
                                or (force and not self.pics and self.jpeg_bytes > self.cap)):
            _, (jb, _) = self.by_jpeg.popitem(last=False)
            self.jpeg_bytes -= len(jb)
        while force and self.pics and self.held_bytes() > self.cap:
            self._evict(next(iter(self.pics)))
        if force and not self.pics and self.jpeg_bytes > self.cap:
            self.by_jpeg.clear()
            self.jpeg_bytes = 0

    def _evict(self, serial) -> None:
        p = self.pics.pop(serial)
        if serial in self.raw:
            self._raw_drop(serial)
        if p.clip is not None and self.by_clip.get((p.fk, p.clip[0], p.clip[1])) == serial:
            del self.by_clip[(p.fk, p.clip[0], p.clip[1])]
        self.bytes -= p.nbytes
        self.by_idx.pop((p.gk, p.digest()), None)
        for b, d in enumerate(p.bands):
            s = self.by_band.get((p.gk, b, d))
            if s is not None:
                s.discard(serial)
                if not s:
                    del self.by_band[(p.gk, b, d)]
        p.kept = False
        self.n["evicted"] += 1

    def _keep(self, p: Pic) -> None:
        """Remember a whole-drawn picture: room is made only from pictures unused for STALE_S - or (GLASS_FIRST) from
        another clip's, for a picture of the clip on the glass."""
        while self.held_bytes() + p.nbytes > self.cap:
            if self._evict_other_framing() or self._evict_passing_clip():
                continue
            if not self.pics or time.monotonic() - next(iter(self.pics.values())).used < STALE_S:
                if self._evict_for_glass(p):
                    continue
                self.n["refused"] += 1
                return
            self._evict(next(iter(self.pics)))
        self.pics[p.serial] = p
        if p.clip is not None and p.fk is not None:
            self.by_clip[(p.fk, p.clip[0], p.clip[1])] = p.serial
        self.bytes += p.nbytes
        self.by_idx[(p.gk, p.digest())] = p.serial
        for b, d in enumerate(p.bands):
            self.by_band.setdefault((p.gk, b, d), set()).add(p.serial)
        p.kept = True
        self.n["kept"] += 1

    def _evict_other_framing(self) -> bool:
        """Evict the least recently used picture of a framing other than the one on the glass: it cannot be handed
        out until that framing comes back. False when there is none (kill: HOUSE_WALL_ASCII_CACHE_REFRAME=0)."""
        if self.fk_now is None or os.environ.get("HOUSE_WALL_ASCII_CACHE_REFRAME", "1").strip() == "0":
            return False
        for s, q in self.pics.items():
            if q.fk is not None and q.fk != self.fk_now:
                self._evict(s)
                self.n["reframed"] += 1
                return True
        return False

    def _evict_passing_clip(self) -> bool:
        """Evict the least recently used picture of a clip she passes through (anything but a loop, and not the clip
        on the glass): the loops she returns to stay whole in memory (round 13). It is on disk if the disk tier is on.
        Kill: HOUSE_WALL_ASCII_CACHE_LOOPS_FIRST=0."""
        if os.environ.get("HOUSE_WALL_ASCII_CACHE_LOOPS_FIRST", "1").strip() == "0":
            return False
        for s, q in self.pics.items():
            c = q.clip
            if c is not None and c[2] != "loop" and c[0] != self.clip_now:
                self._evict(s)
                self.n["passed"] += 1
                return True
        return False

    def _evict_for_glass(self, p: Pic) -> bool:
        """A picture of the loop on the glass may take the room of the least recently used picture of ANOTHER clip,
        stale or not; never of its own clip (a loop longer than the budget still keeps the part that fits), and a clip
        she passes through takes nothing (its pictures go first, _evict_passing_clip).
        THE CLIP ON THE GLASS FIRST (round 17, 2026-10-13, .101 three minutes into a long reading: `held=4092MB of
        4096MB pictures=816 refused=813`, refused=0 at rest before it). A speaking frame's base is its own idle picture,
        read from the disk tier and kept (_from_disk); with the tier full of pictures used in the last STALE_S (a loop
        she had just left, pictures she had just drawn) every one was refused - and a refused base is not in memory
        for the next semiframe or the next lap, so each mouth step read and unpacked it from disk again (two reads a
        film frame) for as long as the other loop stayed fresh. Kill: HOUSE_WALL_ASCII_CACHE_GLASS_FIRST=0; off on the
        house unless =1."""
        if (not self.glass_first or p.clip is None or self.clip_now is None or p.clip[0] != self.clip_now
                or p.clip[2] != "loop"):
            return False
        for s, q in self.pics.items():
            if q.clip is None or q.clip[0] != self.clip_now:
                self._evict(s)
                self.n["glass"] += 1
                return True
        return False

    # ---- pictures -----------------------------------------------------------------------------------------------
    def _geo(self, g) -> _Geo:
        """The geometry, and one more of its checks (each step tens of ms, so no reply waits on all of them)."""
        k = (g.W, g.H, g.density)
        geo = self.geos.get(k)
        if geo is None:
            geo = self.geos[k] = _Geo(g)
            if geo.possible:
                geo.setup = self._setup(g, geo)
            else:
                self.log("ascii cache: %dx%d density %.3f, grid %dx%d, regions off (not whole 8x8 glow blocks)"
                         % (g.W, g.H, g.density, g.COLS, g.ROWS))
            return geo
        if geo.setup is not None:
            try:
                done = next(geo.setup)
            except StopIteration:
                done = False
            if done is not None:
                geo.setup = None
                geo.region_ok = bool(done)
                self.log("ascii cache: %dx%d density %.3f, grid %dx%d, regions %s"
                         % (g.W, g.H, g.density, g.COLS, g.ROWS, "on" if done else "off"))
        return geo

    def prepare(self, g) -> _Geo:
        """Run every check of a geometry now (tests; the film child spreads them over frames)."""
        geo = self._geo(g)
        while geo.setup is not None:
            self._geo(g)
        return geo

    def _new(self, gk, idx: bytes, cov, small, rows: int) -> Pic:
        self.serial += 1
        step = max(1, -(-rows // BANDS))
        width = len(idx) // max(1, rows)
        bands = tuple(hashlib.blake2b(idx[r * width:(r + step) * width], digest_size=8).digest()
                      for r in range(0, rows, step))
        return Pic(self.serial, gk, idx, cov, small, bands, fk=self.fk_now, rows=rows, zm=self._wref)

    def _touch(self, p: Pic) -> Pic:
        p.used = time.monotonic()
        if p.kept:
            self.pics.move_to_end(p.serial)
        return p

    def picture(self, jpeg: bytes | None, g, own: bool, t: float, hole_zoom=None, shift=None, prev: Pic | None = None,
                mouth=None, frame=None, clip=None):
        """_picture (below), with ZMEM's zstd bytes swapped in first and the picture handed out remembered."""
        if self.zmem:
            self._drain()
        got = self._picture(jpeg, g, own, t, hole_zoom, shift, prev, mouth, frame, clip)
        self.last = got[0]
        return got

    def _picture(self, jpeg: bytes | None, g, own: bool, t: float, hole_zoom=None, shift=None, prev: Pic | None = None,
                 mouth=None, frame=None, clip=None):
        """(Pic, how, region) for this frame; how = bytes | grid | region | full | disk | disk-region (round 13: the
        disk tier answered the bytes, or gave the region its base). `clip`: X-Clip's (key, frame, frames, role). `prev` (the last picture handed
        out) is a base for a region; `region` is the output box when the picture is `prev` outside it, else None.
        `mouth` (her mouth box, left out of the exposure; R._luma_grid) is part of what the JPEG's bytes mean, and so
        is `frame` (lib/wall_frame.py: None = the kiosk frame, else whole + zoom + her face point): a new frame or
        zoom is a new key, so the same JPEG never hands out the old frame's picture (2026-10-12)."""
        shift_k = tuple(shift) if shift is not None else None
        mouth_k = tuple(float(v) for v in mouth) if mouth is not None else None
        # the FRAMING (round 12): what makes every picture of another one useless now - geometry, glow, AR hole, crop
        # slide, and the whole frame's mode + zoom; never her face point, which is per clip (a flourish's pictures
        # and the loop's share a framing, so a flourish does not push the loop out)
        self.fk_now = (g.W, g.H, g.density, bool(own or jpeg), hole_zoom, shift_k,
                       tuple(frame[:2]) if isinstance(frame, tuple) else frame)
        clip = clip if (own and isinstance(clip, tuple) and len(clip) == 4) else None
        self.clip_now = clip[0] if clip else None
        jk = None
        if jpeg:
            jk = ((g.W, g.H, g.density, bool(own), hole_zoom, shift_k, mouth_k, frame),
                  hashlib.blake2b(jpeg, digest_size=16).digest())
            got = self.by_jpeg.get(jk)
            if got is not None and got[0] == jpeg:
                p = self.pics.get(got[1])
                if p is not None:
                    self.by_jpeg.move_to_end(jk)
                    self.n["bytes"] += 1
                    return self._checked(self._touch(p), "bytes", jpeg, g, own, t, hole_zoom, shift, mouth,
                                         frame) + (None,)
        # THE DISK TIER (round 13): this clip frame's picture, if one was drawn before in this framing - the same
        # bytes (her idle loop's every lap after a restart) or, for a speaking frame, its own idle picture as a base
        disk_ent = disk_base = None
        ekey = None
        mem_base = None
        swap = False                                # round 32: this frame's entry is a pre-fill's its key does not match
        if clip is not None:                        # this clip frame's picture already in memory: no disk read
            s_ = self.by_clip.get((self.fk_now, clip[0], clip[1]))
            mem_base = self.pics.get(s_) if s_ is not None else None
        if self.disk is not None and clip is not None and jk is not None:
            ekey = hashlib.blake2b(jk[1] + repr((mouth_k, frame)).encode(), digest_size=12).hexdigest()
            self.disk.framing(self.fk_now)
            disk_ent = self.disk.lookup(clip[0], clip[1])
            if disk_ent is not None and mem_base is None:
                self._disk_prefill = False
                q = self._from_disk(g, clip, disk_ent)
                if q is not None and disk_ent[0] == ekey:
                    self._remember_jpeg(jk, jpeg, q)
                    self.n["disk"] += 1
                    self.disk.n["hits"] += 1
                    return self._checked(self._touch(q), "disk", jpeg, g, own, t, hole_zoom, shift, mouth,
                                         frame) + (None,)
                disk_base = q
                # round 45: an entry drawn about another face point (a live picture drawn in the half second the Wall
                # still held the previous clip's point, and stored over the build's) is replaced like a pre-fill's
                swap = (bool(self._disk_prefill) or self._other_point(frame)) and self._replace_on()
        src = R._open(jpeg) if jpeg else None
        glow = bool(own or src is not None)
        gk = (g.W, g.H, g.density, glow)
        idx2d = R.ascii_idx(src, g, own, t, hole_zoom, shift, mouth=mouth, frame=frame).astype(np.uint8)
        idx = idx2d.tobytes()
        s = self.by_idx.get((gk, hashlib.blake2b(idx, digest_size=16).digest()))
        p = self.pics.get(s) if s is not None else None
        if p is not None and p.idx == idx:
            self.n["grid"] += 1
            self._remember_jpeg(jk, jpeg, p)
            return self._checked(self._touch(p), "grid", jpeg, g, own, t, hole_zoom, shift, mouth, frame) + (None,)
        geo = self._geo(g)
        if glow and geo.region_ok:
            got = self._region(gk, idx2d, idx, geo, g, prev, extra=disk_base or mem_base)
            if got is not None:
                q, base, box = got
                via_disk = base is disk_base and disk_base is not None
                self.n["disk_region" if via_disk else "region"] += 1
                if via_disk:
                    self.disk.n["bases"] += 1
                q.clip = (clip[0], clip[1], clip[3]) if clip else None
                if swap and q.small is not None:  # the live picture takes the pre-fill's place on disk (round 32)
                    self.disk.store(clip[0], clip[1], ekey, g.W, g.H, g.ROWS, g.COLS, idx, q.small.size,
                                    q.small.tobytes(), jpeg, mouth_k, q.px, wframe=frame, role=clip[3],
                                    density=g.density, replace=True)
                # round 40 N16: which base the region was laid over, named in a MISMATCH line (.101 round 38's cause
                # is not found; the next one says where its base came from)
                self._rbase = "%s base, box %s, cells changed over serial %s" % (
                    ("disk pre-fill" if self._disk_prefill else "disk") if via_disk else
                    ("clip-frame" if base is mem_base else ("previous" if base is prev else "band")),
                    tuple(box[:4]) if box is not None else None, base.serial)
                q, how = self._checked(q, "region", jpeg, g, own, t, hole_zoom, shift, mouth, frame)
                self._rbase = ""
                if via_disk and how == "region":
                    how = "disk-region"
                same = box is not None and base is prev and q.serial != base.serial and not self.dead
                return q, how, (tuple(box[:4]) if same else None)
        # whole: the fresh render's own stages
        if fresh_why_on(self.env):
            fresh_why(self.log, clip, self._why_whole(jpeg, clip, disk_ent, disk_base, mem_base, mouth_k, frame, glow,
                                                     geo), self.env)
        mask = R.mask_from_idx(idx2d, g)
        small = None
        if glow:
            small = R.glow_small(mask, g)
            mask = ImageChops.screen(mask, R.glow_from_small(small, g))
        cov = ImageChops.multiply(mask, geo.vinv)
        p = self._new(gk, idx, cov, small, g.ROWS)
        p.clip = (clip[0], clip[1], clip[3]) if clip else None
        self._keep(p)
        self._remember_jpeg(jk, jpeg, p)
        self.n["full"] += 1
        # ZMEM: a kept picture is packed off the film path - by the disk writer when it stores this one (it packs it
        # anyway and hands the bytes back), else by the squeeze thread
        hook = self._writer_done(p) if (self.zmem and p.kept and self._writer_packs()) else None
        stored = False
        if ekey is not None and (disk_ent is None or swap) and small is not None:
            stored = self.disk.store(clip[0], clip[1], ekey, g.W, g.H, g.ROWS, g.COLS, idx, small.size,
                                     small.tobytes(), jpeg, mouth_k, p.px, wframe=frame, role=clip[3],
                                     density=g.density, done=hook, replace=swap)
        if self.zmem and p.kept and not (stored and hook is not None):
            self._squeeze_later(p)
        return p, "full", None

    def _other_point(self, frame) -> bool:
        """The disk entry just read was drawn in this frame's mode and zoom about another face point. Kill:
        HOUSE_WALL_ASCII_DISK_REPLACE_POINT=0."""
        raw = str(self.env.get("HOUSE_WALL_ASCII_DISK_REPLACE_POINT", "")).strip().lower()
        if raw in ("0", "false", "off", "no") or (not raw and not _frame_point_pack()):
            return False                # unset: a pack only (it ships with the Wall's own point, house_wall_ascii)
        hd = self._disk_hdr
        if hd is None or not isinstance(hd[0], list) or not isinstance(frame, tuple):
            return False
        return hd[0][:2] == list(frame[:2]) and hd[0][2:] != list(frame[2:])

    def _why_whole(self, jpeg, clip, disk_ent, disk_base, mem_base, mouth_k, frame, glow, geo) -> str:
        """Round 45: why this picture is about to be drawn whole (the fresh_why line)."""
        if clip is None:
            return "no clip: the frame carried no X-Clip"
        if self.disk is None:
            return "no disk tier"
        if disk_ent is None and mem_base is None:
            return "not built: no entry on disk for this frame"
        if disk_ent is not None and disk_base is None and mem_base is None:
            return "entry unreadable or drawn for another geometry"
        diff = []
        hd = self._disk_hdr if disk_base is not None else None
        if hd is not None:
            want_f = list(frame) if isinstance(frame, tuple) else frame
            if hd[0] != want_f:
                if isinstance(hd[0], list) and isinstance(want_f, list) and hd[0][:2] == want_f[:2]:
                    diff.append("face point %s, built %s" % (tuple(want_f[2:]), tuple(hd[0][2:])))
                else:
                    diff.append("frame %s, built %s" % (want_f, hd[0]))
            if (list(mouth_k) if mouth_k else None) != hd[1]:
                diff.append("mouth box %s, built %s" % (mouth_k, hd[1]))
            if hd[2] is not None and bytes(hd[2]) != (jpeg or b""):
                diff.append("film bytes")
        base = "disk" if disk_base is not None else "memory"
        why = ("key: " + "; ".join(diff)) if diff else ("%s base" % base)
        if not glow or not geo.region_ok:
            return why + "; regions off for this geometry"
        return why + "; region: " + (self._region_why or "none")

    def _replace_on(self) -> bool:
        try:
            import wall_ascii_disk
            return wall_ascii_disk.replace_prefill_on(self.env)
        except Exception:  # noqa: BLE001
            return False

    def _writer_packs(self) -> bool:
        """Does the disk writer pack what it stores in zstd-1 (the memory tier's format)?"""
        try:
            import wall_ascii_disk
            return self.disk is not None and wall_ascii_disk.codec(self.env) == "zstd-1"
        except Exception:  # noqa: BLE001
            return False

    def _from_disk(self, g, clip, ent):
        """This clip frame's picture read back from the disk tier (kept in memory like a whole-drawn one), or None
        (damaged, or drawn for another geometry)."""
        got = self.disk.load(clip[0], clip[1], ent[1], keep_z=self.zmem)
        if got is None:
            return None
        hd = got["hdr"]
        if (hd.get("w"), hd.get("h"), hd.get("rows"), hd.get("cols")) != (g.W, g.H, g.ROWS, g.COLS):
            return None
        self._disk_prefill = bool(hd.get("prefill"))
        self._disk_hdr = (hd.get("frame"), hd.get("mouth"), got.get("jpeg"))   # round 45: what a key miss differs in
        small = Image.frombytes("L", tuple(hd["small"]), got["small"])
        q = self._new((g.W, g.H, g.density, True), got["idx"], (got["px"], (g.W, g.H)), small, g.ROWS)
        q.clip = (clip[0], clip[1], clip[3])
        if self.zmem and got.get("zpx") is not None:
            q.z = got["zpx"]                        # held as the file's own zstd bytes: nothing to pack
            q.nbytes += len(q.z) - len(q._raw)
        self._keep(q)
        if self.zmem and q.kept:
            if q.z is not None:
                self._raw_add(q)                    # about to be drawn, or a mouth step's base: keep it unpacked
            self._squeeze_later(q, px=q.z is None)  # its grid (and the pixels of a raw-codec entry)
        return q

    def read_ahead(self, g, clip, n: int | None = None, budget_s: float | None = None) -> int:
        """THE WALL'S MEMORY WARMS AHEAD OF THE FILM (the User 2026-10-16, .101 round 22: the first lap after a start read
        every picture from disk on the frame's own time - memory 0, disk 302 - and the Wall fell to 9.5/s as she began to
        speak). After a frame is served, the next `n` frames of the clip on the glass are read from the disk tier into
        memory, within `budget_s`, so the lap that follows is served from memory. Pictures already in memory are skipped.
        Returns how many were loaded. Kill HOUSE_WALL_ASCII_READ_AHEAD=0 (house default off; a pack on)."""
        if self.disk is None or not isinstance(clip, tuple) or len(clip) != 4 or not read_ahead_on(self.env):
            return 0
        n = READ_AHEAD_N if n is None else int(n)
        budget_s = READ_AHEAD_S if budget_s is None else float(budget_s)
        key, f, frames, role = clip
        t0 = time.perf_counter()
        loaded = 0
        for j in range(1, n + 1):
            if time.perf_counter() - t0 >= budget_s:
                break
            fr = (int(f) + j) % int(frames) if int(frames) > 0 else int(f) + j
            if (self.fk_now, key, fr) in self.by_clip:
                continue
            ent = self.disk.lookup(key, fr)
            if ent is None:
                continue
            try:
                q = self._from_disk(g, (key, fr, frames, role), ent)
            except Exception:  # noqa: BLE001 - a read-ahead never costs the film
                q = None
            if q is not None and q.kept:
                loaded += 1
        self.n["ahead"] = self.n.get("ahead", 0) + loaded
        # THE DISK LINE COUNTS THE READ-AHEAD (round 33, .101 round 32: after a restart "listen 392 pictures: memory 390,
        # disk 0, fresh 2" beside `disk ... hits=0 bases=0 writes=0 replaced=0`). The read-ahead reads the disk tier's
        # pictures into memory before their frame comes up. The lap then serves them as memory, and nothing counted
        # them on the disk line. They are `ahead=N` there now. Kill HOUSE_WALL_ASCII_DISK_AHEAD_COUNT=0.
        dn = getattr(self.disk, "n", None)
        if loaded and isinstance(dn, dict) and str(self.env.get("HOUSE_WALL_ASCII_DISK_AHEAD_COUNT", "1")).strip(
                ).lower() not in ("0", "false", "off", "no"):
            dn["ahead"] = dn.get("ahead", 0) + loaded
        return loaded

    def _remember_jpeg(self, jk, jpeg, p: Pic) -> None:
        """These JPEG bytes draw p's grid: next time they skip the decode."""
        if jk is None or not p.kept:
            return
        old = self.by_jpeg.pop(jk, None)
        if old is not None:
            self.jpeg_bytes -= len(old[0])
        self.by_jpeg[jk] = (jpeg, p.serial)
        self.jpeg_bytes += len(jpeg)
        self._fit()

    def _bases(self, gk, idx: bytes, rows: int, prev: Pic | None):
        out = []
        if prev is not None and prev.gk == gk and prev.small is not None:
            out.append(prev)
        step = max(1, -(-rows // BANDS))
        width = len(idx) // max(1, rows)
        votes: dict = {}
        nb = 0
        for b, r in enumerate(range(0, rows, step)):
            nb += 1
            d = hashlib.blake2b(idx[r * width:(r + step) * width], digest_size=8).digest()
            for s in self.by_band.get((gk, b, d), ()):
                votes[s] = votes.get(s, 0) + 1
        for s, v in sorted(votes.items(), key=lambda kv: -kv[1])[:3]:
            if v >= nb - BAND_SLACK and s in self.pics and (prev is None or s != prev.serial):
                out.append(self.pics[s])
        return out

    def _region(self, gk, idx2d, idx: bytes, geo: _Geo, g, prev: Pic | None, extra: Pic | None = None):
        best = None
        tried, least = 0, None
        self._region_why = ""
        for base in ([extra] if extra is not None else []) + self._bases(gk, idx, g.ROWS, prev):
            if base.small is None or base.nidx != len(idx):
                continue
            diff = idx2d != np.frombuffer(base.idx, np.uint8).reshape(idx2d.shape)
            rows = np.flatnonzero(diff.any(axis=1))
            if rows.size == 0:
                return self._touch(base), base, None  # the same grid: the same picture
            cols = np.flatnonzero(diff.any(axis=0))
            box = self._padded(geo, int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1)
            if box is None:
                continue
            area = (box[2] - box[0]) * (box[3] - box[1])
            tried += 1
            least = area if least is None else min(least, area)
            if area <= REGION_MAX * geo.W * geo.H and (best is None or area < best[0]):
                best = (area, base, box)
        if best is None:
            self._region_why = ("no base picture to lay a region over" if not tried else
                                "the change is %.2f of the glass (a region is at most %.2f)"
                                % (least / float(geo.W * geo.H), REGION_MAX))
            return None
        _, base, box = best
        cov, small = self._draw_region(geo, g, idx2d, base, box)
        p = self._new(gk, idx, cov, small, g.ROWS)
        self._touch(base)
        return p, base, box

    @staticmethod
    def _padded(geo: _Geo, c0: int, c1: int, r0: int, r1: int):
        """The output box a change in cells [c0, c1) x [r0, r1) can reach, as (ox0, oy0, ox1, oy1, sx0, sy0, sx1,
        sy1) with the changed small (glow input) pixels; None when the changed cells reach no pixel."""
        q = R.GLOW_Q
        mx0 = int(np.searchsorted(geo.mapx, c0 * geo.cw, "left"))
        mx1 = int(np.searchsorted(geo.mapx, c1 * geo.cw, "left"))
        my0 = int(np.searchsorted(geo.mapy, r0 * geo.ch, "left"))
        my1 = int(np.searchsorted(geo.mapy, r1 * geo.ch, "left"))
        if mx1 <= mx0 or my1 <= my0:
            return None
        sx0, sx1, sy0, sy1 = mx0 // q, -(-mx1 // q), my0 // q, -(-my1 // q)
        pad = BLUR_REACH + UP_REACH
        ox0, ox1 = max(0, (sx0 - pad) * q), min(geo.W, (sx1 + pad) * q)
        oy0, oy1 = max(0, (sy0 - pad) * q), min(geo.H, (sy1 + pad) * q)
        return (ox0, oy0, ox1, oy1, sx0, sy0, sx1, sy1)

    def _draw_region(self, geo: _Geo, g, idx2d, base: Pic, box):
        """The fresh render inside `box`, laid over base (whose grid differs from idx2d only inside the cells the
        box was padded from)."""
        q = R.GLOW_Q
        ox0, oy0, ox1, oy1, sx0, sy0, sx1, sy1 = box
        SWs, SHs = geo.W // q, geo.H // q
        # the glow's input: base's, with the changed 8x8 blocks boxed again from the new glyphs
        blocks = Image.fromarray(np.ascontiguousarray(geo.mask(idx2d, sx0 * q, sx1 * q, sy0 * q, sy1 * q)), "L")
        small = base.small.copy()
        small.paste(_box_down(blocks, sx1 - sx0, sy1 - sy0), (sx0, sy0))
        # the glow over the box: blur a crop wide enough that its edges do not reach what is used, upsample a crop
        # one step inside that, keep the box
        os0, os1, ot0, ot1 = ox0 // q, ox1 // q, oy0 // q, oy1 // q
        gx0, gx1 = max(0, os0 - UP_REACH), min(SWs, os1 + UP_REACH)
        gy0, gy1 = max(0, ot0 - UP_REACH), min(SHs, ot1 + UP_REACH)
        bx0, bx1 = max(0, gx0 - BLUR_REACH), min(SWs, gx1 + BLUR_REACH)
        by0, by1 = max(0, gy0 - BLUR_REACH), min(SHs, gy1 + BLUR_REACH)
        blurred = small.crop((bx0, by0, bx1, by1)).filter(ImageFilter.GaussianBlur(R.GLOW_PX / 2.0 / q))
        lit = blurred.crop((gx0 - bx0, gy0 - by0, gx1 - bx0, gy1 - by0)).point(R._glow_lut())
        # the whole picture's own upsample (OpenCV, or PIL under HOUSE_WALL_GLOW_CV=0; 2026-10-13): another one here
        # fails the self-check below and turns regions off
        up = R.glow_up(lit, ((gx1 - gx0) * q, (gy1 - gy0) * q))
        glow = up.crop((ox0 - gx0 * q, oy0 - gy0 * q, ox1 - gx0 * q, oy1 - gy0 * q))
        mask = Image.fromarray(np.ascontiguousarray(geo.mask(idx2d, ox0, ox1, oy0, oy1)), "L")
        part = ImageChops.multiply(ImageChops.screen(mask, glow), geo.vinv.crop((ox0, oy0, ox1, oy1)))
        # straight into the new picture's bytes: one copy of base, the box's rows laid in (round 12: the old
        # copy + paste + tobytes was three passes over the whole picture; the bytes are the same)
        buf = bytearray(base.px)
        np.frombuffer(buf, np.uint8).reshape(base.size[1], base.size[0])[oy0:oy1, ox0:ox1] = np.asarray(part)
        return (buf, base.size), small

    def _setup(self, g, geo: _Geo):
        """A geometry's checks as steps (a generator: None = not yet, then True/False). The NEAREST map on every
        pixel, both axes and an L sheet; then region drawing against whole drawing on a random grid, changed in the
        middle and at every edge."""
        SW, SH = geo.COLS * geo.cw, geo.ROWS * geo.ch
        if (SW, SH) != (geo.W, geo.H):
            for horizontal in (True, False):
                if not geo.axis_matches(SW, SH, horizontal):
                    self.log("ascii cache: the NEAREST map differs at %dx%d: regions off" % (g.W, g.H))
                    yield False
                    return
                yield None
            if not geo.random_matches(SW, SH):
                self.log("ascii cache: the NEAREST map differs at %dx%d: regions off" % (g.W, g.H))
                yield False
                return
            yield None
        rng = np.random.default_rng(11)
        base_idx = rng.integers(0, len(R.RAMP), (g.ROWS, g.COLS)).astype(np.uint8)
        gk = ("check",)
        mask = R.mask_from_idx(base_idx, g)
        small = R.glow_small(mask, g)
        base = self._new(gk, base_idx.tobytes(), ImageChops.multiply(ImageChops.screen(mask, R.glow_from_small(small, g)),
                                                                     geo.vinv), small, g.ROWS)
        yield None
        R2, C2 = g.ROWS, g.COLS
        # ... and a TALL and a WIDE block near REGION_MAX (round 46: a library that boxes a crop in another order than
        # the whole glass differs only on a large crop of one shape; the six small ones above passed on .101)
        for (r0, c0, h, w) in ((R2 // 2, C2 // 2, 3, 5), (0, 0, 2, 3), (R2 - 2, C2 - 4, 2, 4), (0, C2 - 3, 3, 3),
                               (R2 - 3, 0, 3, 2), (R2 // 3, 1, 1, 1), (R2 // 8, C2 // 3, R2 * 5 // 8, max(1, C2 // 16)),
                               (R2 // 3, C2 // 4, max(1, R2 // 8), C2 * 3 // 8)):
            new = base_idx.copy()
            new[r0:r0 + h, c0:c0 + w] = rng.integers(0, len(R.RAMP), new[r0:r0 + h, c0:c0 + w].shape)
            new[r0, c0] = (int(base_idx[r0, c0]) + 1) % len(R.RAMP)
            box = self._padded(geo, c0, min(C2, c0 + w), r0, min(R2, r0 + h))
            if box is None:
                continue
            (buf, size), _ = self._draw_region(geo, g, new, base, box)
            cov = Image.frombuffer("L", size, bytes(buf), "raw", "L", 0, 1)
            m = R.mask_from_idx(new, g)
            want = ImageChops.multiply(ImageChops.screen(m, R.glow_from_small(R.glow_small(m, g), g)), geo.vinv)
            if cov.tobytes() != want.tobytes():
                self.log("ascii cache: region self-check failed at %dx%d (cells %d,%d): regions off" % (g.W, g.H, r0, c0))
                yield False
                return
            yield None
        yield True

    def _checked(self, p: Pic, how: str, jpeg, g, own, t, hole_zoom, shift, mouth=None, frame=None):
        """1 in VERIFY_EVERY answers (and a geometry's first 4 regions) against the fresh render; a mismatch turns
        the cache off until restart."""
        if VERIFY_EVERY <= 0:
            return p, how
        geo = self.geos.get((g.W, g.H, g.density))
        first = how == "region" and geo is not None and geo.verified < 4
        self.verify_n += 1
        if not first and self.verify_n % VERIFY_EVERY:
            return p, how
        if geo is not None and how == "region":
            geo.verified += 1
        want = R.coverage_fresh(R._open(jpeg) if jpeg else None, g, own, t, hole_zoom, shift, mouth=mouth, frame=frame)
        self.n["verified"] += 1
        if want.tobytes() != p.px:
            self.dead = "a %s answer differed from the fresh render at %dx%d" % (how, g.W, g.H)
            self.log("ascii cache: MISMATCH - %s%s; the cache is off until the wall restarts"
                     % (self.dead, (" (%s)" % self._rbase) if how == "region" and getattr(self, "_rbase", "") else ""))
            p = self._new(p.gk, p.idx, want, None, g.ROWS)
        return p, how

    # ---- what changed since the last picture --------------------------------------------------------------------
    def box(self, prev: Pic | None, prev_pal: bytes | None, p: Pic, pal: bytes, region=None, whole_over=None):
        """The RGB bounding box of what changed since `prev` (painted with prev_pal): (0, 0, 0, 0) for nothing,
        None for everything. Exact: two coverages paint a pixel the same iff their canon values are equal.
        `whole_over` (a share of the glass, the parent's patch limit): None at once when the changed GLYPH CELLS
        already span more than that, without the pixel diff (round 12: a film frame changes ~3 % of the cells
        scattered over the whole glass; the diff that proved it whole was 2.3 ms of every one)."""
        if prev is None or prev.size != p.size:
            return None
        if prev_pal != pal:
            return None                                   # a new palette: the whole picture
        if prev.serial == p.serial:
            return (0, 0, 0, 0)
        if region is None and whole_over is not None and self._cells_span(prev, p) > float(whole_over):
            return None
        pk = hashlib.blake2b(pal, digest_size=8).digest()
        mk = (prev.serial, p.serial, pk)
        got = self.boxes.get(mk)
        if got is not None:
            self.boxes.move_to_end(mk)
            return got
        lut = self.canon.get(pk)
        if lut is None:
            if len(self.canon) > 32:
                self.canon.clear()
            lut = self.canon[pk] = _canon_lut(pal)
        if region is not None:                            # p is prev outside `region`
            x0, y0, x1, y1 = region
            a, b = prev.image().crop(region).point(lut), p.image().crop(region).point(lut)
        else:
            x0 = y0 = 0
            last = self.canon_last
            a = last[3] if last is not None and last[:3] == (prev.serial, pk, None) else prev.image().point(lut)
            b = p.image().point(lut)
            self.canon_last = (p.serial, pk, None, b)
        bb = ImageChops.difference(a, b).getbbox()
        out = (bb[0] + x0, bb[1] + y0, bb[2] + x0, bb[3] + y0) if bb else (0, 0, 0, 0)
        if prev.kept and p.kept:
            self.boxes[mk] = out
            if len(self.boxes) > 8192:
                self.boxes.popitem(last=False)
        return out

    def _cells_span(self, prev: Pic, p: Pic) -> float:
        """A lower bound on the share of the glass the pixel box of prev -> p covers, from the glyph grids: a changed
        cell's pixels lie inside its own cell, so the box reaches at least from the first changed column's far edge
        to the last one's near edge (rows the same). 0.0 when it cannot say (other geometry, too few changes)."""
        if prev.gk != p.gk or prev.nidx != p.nidx or not p.rows:
            return 0.0
        geo = self.geos.get(p.gk[:3])
        if geo is None:
            return 0.0
        cols = p.nidx // p.rows
        d = (np.frombuffer(prev.idx, np.uint8).reshape(p.rows, cols)
             != np.frombuffer(p.idx, np.uint8).reshape(p.rows, cols))
        rr, cc = np.flatnonzero(d.any(axis=1)), np.flatnonzero(d.any(axis=0))
        if rr.size < 3 or cc.size < 3:
            return 0.0
        x0 = int(np.searchsorted(geo.mapx, (int(cc[0]) + 1) * geo.cw, "left"))
        x1 = int(np.searchsorted(geo.mapx, int(cc[-1]) * geo.cw, "left"))
        y0 = int(np.searchsorted(geo.mapy, (int(rr[0]) + 1) * geo.ch, "left"))
        y1 = int(np.searchsorted(geo.mapy, int(rr[-1]) * geo.ch, "left"))
        return max(0, x1 - x0) * max(0, y1 - y0) / float(geo.W * geo.H)

    def stats(self) -> dict:
        zm = None
        rss = _own_rss()
        if self.zmem:
            ms = sorted(self.zms)
            zm = dict(self.zn, raw=len(self.raw), raw_bytes=self.raw_bytes,
                      unpack_p50=ms[len(ms) // 2] if ms else None,
                      unpack_p90=ms[min(len(ms) - 1, int(len(ms) * 0.9))] if ms else None)
        return dict(self.n, bytes_held=self.held_bytes(), cap=self.cap, pictures=len(self.pics),
                    dead=self.dead, disk=self.disk.stats() if self.disk is not None else None, zmem=zm,
                    rss=rss, over=self.over(rss))

    def over(self, rss: int | None = None) -> int | None:
        """Bytes the process holds beyond the count and the harness it started with: what the allocator keeps (O3,
        2026-10-24: 2.6 GB on the house child, ~11 GB on .101, see lib/wall_film_proc.pictures_out_of_heap)."""
        rss = _own_rss() if rss is None else rss
        if rss is None or self.rss0 is None:
            return None
        return max(0, rss - self.rss0 - self.held_bytes())
