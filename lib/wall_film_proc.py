"""The wall's film, drawn in its own process (the User 2026-10-10: "Split the film renderer into its own process").

The wall is Python 3.12: its Tk painter and its film thread shared one interpreter lock, and together they sat at
80-100 % of one core all day, so anything else on the box (a file search, a busy agent) showed as a slower glass.
`render_background` is a pure function of the frame and a few dials (it reads only constants), so it runs here, in
a child process with its own interpreter; the wall's film thread only hands over the JPEG and reads the pixels back.

Protocol (child = `python wall_film_proc.py --child W H SHM`, stdin/stdout, binary):
    request:  u32 length + pickle {jpeg, density, own, t, hole_zoom, brightness, shift, cache, lean?, box?, mouth?,
              frame?}   (frame: lib/wall_frame.py's ("whole", zoom, fx, fy); absent or None = the kiosk frame)
    reply:    u32 length + pickle {ok, ms, size, box, mode, pal?, how, cache, error?}; the pixels are in the shared
              frame file SHM (/dev/shm, else the temp directory: frame_dir; W*H*3 bytes, one frame: the parent copies
              it out before it asks for the next one). mode "RGB": W*H*3 bytes, today's render_background. mode "L"
              (the ASCII cache answered, lib/wall_ascii_cache.py): W*H bytes of coverage, painted by the parent
              through `pal` - the same palette convert render_background ends with, so the same RGB. `box` is what
              changed since the child's previous picture (x0, y0, x1, y1), None = everything. `how` says how the
              child drew it (today | bytes | grid | region | full); `cache` is the cache's running counts.
    lean (2026-10-11, the .101 speed): a parent that sends `lean` takes today's picture as its coverage too (mode
              "L" + `pal`, render_background's own last step done on this side: 5 MB through the frame file instead
              of 15, no palette convert in the child), and gets `box` only when it sends `box` true (it patches);
              otherwise box is None and the child skips the diff. A parent without `lean` (an older wall over a
              child respawned on new code) gets today's RGB reply and diff, unchanged. Kill: HOUSE_WALL_FILM_LEAN=0.
    mouth:    the conductor's mouth box for this frame (X-Mouth-Box), passed to the render as `mouth`: the
              exposure leaves her mouth out (house_wall_ascii_render.norm_cells). None = today's exposure.

Any failure (the child died, a reply late past REPLY_S, a bad reply) returns None and the caller draws in its own
thread exactly as before; the child is started again at most once per RESPAWN_S. The child lives in the wall
unit's cgroup, so it carries the wall's CPU weight. Kill: HOUSE_WALL_FILM_PROCESS=0.
"""
from __future__ import annotations

import mmap
import os
import pickle
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time

REPLY_S = float(os.environ.get("HOUSE_WALL_FILM_REPLY_S", "1.0"))
# A FRESH CHILD STARTS COLD (2026-10-11, measured under the .101-like budget): its first reply builds the glyph atlas,
# the vignette and the cache's geometry, 0.96-1.8 s at 3440x1440 against REPLY_S's 1.0 s. A miss killed it, the film
# thread drew for RESPAWN_S, and the next child started cold again. Its first COLD_REPLIES replies get COLD_S.
COLD_S = float(os.environ.get("HOUSE_WALL_FILM_COLD_S", "10.0"))
COLD_REPLIES = 2
RESPAWN_S = 30.0
_HDR = struct.Struct("<I")


# A PICTURE GOES BACK TO THE OS WHEN IT IS FREED (.101 round 46 O3, 2026-10-24: the film child at 15.9 GB RSS while the
# cache said held=5032MB; the house child 6.2 GB over held=3613MB). Measured on the house child (/proc/<pid>/smaps, read
# only): 5.5 GB of its 6.2 GB was the brk heap, 64 of its 126 heap mappings carried the `hg` advice (numpy madvises
# every array of 4 MiB or more MADV_HUGEPAGE, and once glibc's dynamic mmap threshold has climbed past a picture - it
# does on the first free of one - those arrays, and every unpacked picture (W*H bytes, 24 a second), come out of the
# heap), and 3.96 GB of those 4.07 GB were huge pages: a hole left by a freed picture in a 2 MB page stays resident,
# and khugepaged (max_ptes_none 511) fills the rest. The count was right; the allocator kept what the count let go.
# The remedy is glibc's own: a FIXED mmap threshold (mallopt M_MMAP_THRESHOLD, which also stops the dynamic climb), so
# a block the size of a picture or bigger is mapped on its own and unmapped on free, and the heap keeps only the zstd
# pictures (a quarter to a third of a picture each), where a freed one's hole fits the next. Half a picture is the
# line: the dial is HOUSE_WALL_FILM_MMAP_KB (KiB; 0 = off, glibc's dynamic default as before). Simulated at 2560x1440
# (docs/checkpoints/wall-child-memory-20261024): +0.25 ms a frame on the cached path (one 3.7 MB map + faults).
MMAP_MIN_KB, MMAP_MAX_KB = 128, 32 * 1024          # glibc's own range for M_MMAP_THRESHOLD (DEFAULT_MMAP_THRESHOLD_MAX)


def mmap_threshold_kb(W: int, H: int, env=None) -> int:
    """KiB from which the film child's allocator maps a block on its own: the dial, else half a picture."""
    env = os.environ if env is None else env
    raw = str(env.get("HOUSE_WALL_FILM_MMAP_KB", "")).strip().lower()
    if raw in ("0", "off", "false", "no"):
        return 0
    try:
        kb = int(float(raw)) if raw else (W * H // 2) // 1024
    except ValueError:
        kb = (W * H // 2) // 1024
    return max(MMAP_MIN_KB, min(MMAP_MAX_KB, kb))


def pictures_out_of_heap(W: int, H: int, env=None, log=None) -> int:
    """Set glibc's mmap threshold for this process (Linux + glibc; elsewhere a no-op). Returns the KiB set, 0 = off."""
    kb = mmap_threshold_kb(W, H, env)
    if not kb or not sys.platform.startswith("linux"):
        return 0
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.mallopt(-3, kb * 1024) != 1:          # M_MMAP_THRESHOLD (malloc.h)
            return 0
    except (OSError, AttributeError) as e:
        if log:
            log("film child: mmap threshold not set (%s: %s)" % (type(e).__name__, e))
        return 0
    return kb


def enabled() -> bool:
    return os.environ.get("HOUSE_WALL_FILM_PROCESS", "1").strip().lower() not in ("0", "false", "no", "off")


def lean_enabled() -> bool:
    return os.environ.get("HOUSE_WALL_FILM_LEAN", "1").strip().lower() not in ("0", "false", "no", "off")


def _clip_of(raw) -> tuple | None:
    """A request's clip as (key, frame, frames, role), else None (a bad clip is no clip, never a fault)."""
    try:
        key, i, n, role = raw
        return (str(key), int(i), int(n), str(role))
    except (TypeError, ValueError):
        return None


def box_lean_enabled() -> bool:
    """round 12 (2026-10-12): the parent names its patch limit (`box_max`), so the child skips the pixel diff of a
    picture whose changed cells already span more than that, and writes only a patch's rows into the frame file.
    Kill: HOUSE_WALL_FILM_BOX_LEAN=0 (the request then carries no box_max: the child as it was)."""
    return os.environ.get("HOUSE_WALL_FILM_BOX_LEAN", "1").strip().lower() not in ("0", "false", "no", "off")


def mouth_of(raw) -> tuple | None:
    """A request's mouth box as four floats with a positive size, else None (a bad box is today's exposure, never a
    fault in the cache)."""
    try:
        v = tuple(float(x) for x in raw)
    except (TypeError, ValueError):
        return None
    if len(v) != 4 or not (v[2] > 0 and v[3] > 0) or any(x != x for x in v):
        return None
    return v


def _read_exact(fd: int, n: int, deadline: float) -> bytearray | None:
    """n bytes from fd by `deadline` (time.monotonic), else None. os.readv releases the GIL while it waits."""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            return None
        k = os.readv(fd, [view[got:]])
        if k <= 0:
            return None
        got += k
    return buf


# A MAC HAS NO /dev/shm (2026-10-13): FilmProcess opened its frame file there, so on a Mac the open failed, the wall
# logged "film process: off" and its film thread drew every frame itself (house_wall_ascii.py), with no ASCII cache, no
# disk tier and no pre-fill, which all live in this child. Where /dev/shm is not a directory the frame file goes in the
# temp directory (tempfile.gettempdir(): $TMPDIR, per user on a Mac); the child needs only a path it can mmap. Off a
# tmpfs the kernel writes the file's dirty pages back now and then (one frame's bytes at a time, never one per frame).
# Not yet run on a Mac. Linux is unchanged: /dev/shm is a directory there, and the path is the same string.
# Kill: HOUSE_WALL_FILM_SHM_FALLBACK=0 (/dev/shm only: no film child on a Mac, as before).
SHM_DIR = "/dev/shm"


def frame_dir(env=None) -> str:
    """Where the frame file goes: /dev/shm, else (fallback on) the temp directory."""
    env = os.environ if env is None else env
    if os.path.isdir(SHM_DIR):
        return SHM_DIR
    if str(env.get("HOUSE_WALL_FILM_SHM_FALLBACK", "1")).strip().lower() in ("0", "false", "no", "off"):
        return SHM_DIR
    return tempfile.gettempdir()


def _sweep_stale(where: str = SHM_DIR) -> None:
    """A wall killed hard leaves its frame file; remove the ones whose wall is gone."""
    try:
        names = os.listdir(where)
    except OSError:
        return
    for n in names:
        if not n.startswith("house-wall-film-"):
            continue
        try:
            pid = int(n.rsplit("-", 1)[1])
            os.kill(pid, 0)
        except ProcessLookupError:
            try:
                os.unlink(os.path.join(where, n))
            except OSError:
                pass
        except (ValueError, PermissionError):
            pass


def _write_all(fd: int, data: bytes, deadline: float) -> bool:
    """All of `data` into fd by `deadline`, else False: a frozen child must not hold the film thread on a full pipe."""
    view = memoryview(data)
    sent = 0
    while sent < len(data):
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        _, w, _ = select.select([], [fd], [], left)
        if not w:
            return False
        try:
            sent += os.write(fd, view[sent:sent + 65536])
        except BlockingIOError:
            continue
    return True


# THE WALL HANDS ITS PLAYER TO THE CHILD (2026-10-13): round 16's "no player, no build" (wall_ascii_disk._prefill) reads
# HOUSE_WALL_FEED / CRISSA_PLAYER from the child's environment, and the house's Wall names neither - it finds its player
# in the house config (wall_conductor, house_wall_ascii.FEED) - so after its 09:04 restart the house Wall never
# pre-filled (writes only as it drew; "prefill" absent from its log). A real Wall now passes the feed it resolved; the
# child gets HOUSE_WALL_FEED only when neither name is already set, so a pack (its launcher sets them) is unchanged, and
# a FilmProcess made without a feed (a test) still builds nothing. Kill: HOUSE_WALL_FEED_TO_CHILD=0 (round 16's env).
def child_env(feed: str | None, env=None) -> dict | None:
    """The child's environment: None (inherit) unless a feed must be handed down."""
    env = os.environ if env is None else env
    if not feed or str(env.get("HOUSE_WALL_FEED_TO_CHILD", "1")).strip().lower() in ("0", "false", "no", "off"):
        return None
    if env.get("HOUSE_WALL_FEED") or env.get("CRISSA_PLAYER"):
        return None
    out = dict(env)
    out["HOUSE_WALL_FEED"] = str(feed)
    return out


class FilmProcess:
    """The parent's handle. `render(...)` -> PIL.Image or None (None = draw it yourself)."""

    def __init__(self, W: int, H: int, log=None, feed: str | None = None):
        self.W, self.H = int(W), int(H)
        self.feed = feed
        self.log = log or (lambda m: None)
        self.p: subprocess.Popen | None = None
        self.dead_at = 0.0
        self.frames = 0
        self.fails = 0
        self.answered = 0               # replies from the current child (its first ones are cold)
        self.how = None                 # how the child drew the last picture
        self.child_ms = None            # the child's own time for it (the round trip less the pipe and the copy-out)
        self.cache_stats = None         # the ASCII cache's running counts, from the child's last reply
        self._lock = threading.Lock()
        self.size = self.W * self.H * 3
        self.lean = lean_enabled()
        where = frame_dir()
        self.shm_path = os.path.join(where, "house-wall-film-%d" % os.getpid())
        _sweep_stale(where)
        with open(self.shm_path, "wb") as f:
            f.truncate(self.size)
        with open(self.shm_path, "r+b") as f:
            self.mm = mmap.mmap(f.fileno(), self.size)
        self._start()

    def _start(self) -> None:
        try:
            self.p = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "--child", str(self.W), str(self.H), self.shm_path],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
                                      cwd=os.path.dirname(os.path.abspath(__file__)), env=child_env(self.feed))
            os.set_blocking(self.p.stdin.fileno(), False)
            self.answered = 0
            self.log("film process: started pid %d" % self.p.pid)
        except OSError as e:
            self.p = None
            self.dead_at = time.monotonic()
            self.log("film process: could not start (%s), the film thread draws" % e)

    def _kill(self, why: str) -> None:
        self.fails += 1
        if self.p is not None:
            self.log("film process: %s, the film thread draws until it is back" % why)
            try:
                self.p.kill()
                self.p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        self.p = None
        self.dead_at = time.monotonic()

    def alive(self) -> bool:
        if self.p is not None and self.p.poll() is None:
            return True
        if self.p is not None:
            self._kill("child exited rc=%s" % self.p.returncode)
        if time.monotonic() - self.dead_at >= RESPAWN_S:
            self._start()
        return self.p is not None

    def render(self, jpeg: bytes | None, density: float, own: bool, t: float, hole_zoom=None, brightness=None,
               shift=None, cache: bool = False, mouth=None, frame=None):
        """The whole picture, or None."""
        got = self.render_patch(jpeg, density, own, t, hole_zoom, brightness, shift, patch_max=0.0, cache=cache,
                                mouth=mouth, frame=frame)
        return got[0] if got else None

    def render_patch(self, jpeg: bytes | None, density: float, own: bool, t: float, hole_zoom=None,
                     brightness=None, shift=None, patch_max: float = 0.4, cache: bool = False, mouth=None,
                     clip=None, quiet=None, norm=None,
                     frame=None):
        """(image, xy): xy None = the whole picture; else `image` is only the part that changed since the previous
        picture, to be laid at xy over it (a mouth step between two film frames). A change larger than `patch_max`
        of the picture comes back whole. (None, None) = nothing changed. None = draw it yourself. `cache`: the
        child may answer from the ASCII cache (lib/wall_ascii_cache.py: the fresh render, exact). `mouth`: her
        mouth box in the frame (x, y, w, h), left out of the exposure; None = today's. `frame`: the film's frame
        (lib/wall_frame.py), None = the kiosk frame; sent only when set, so a child of either age reads the request."""
        with self._lock:
            if not self.alive():
                return None
            ask = {"jpeg": jpeg, "density": density, "own": own, "t": t, "hole_zoom": hole_zoom,
                   "brightness": brightness, "shift": shift, "cache": bool(cache)}
            if frame is not None:
                ask["frame"] = tuple(frame)
            if clip is not None:
                ask["clip"] = tuple(clip)                 # X-Clip (round 13): the disk tier's key; sent only when set
            if norm is not None:
                ask["norm"] = bool(norm)                  # round 14: the pre-fill leaves her mouth out as the Wall does
            if self.lean:
                ask.update(lean=True, box=patch_max > 0, mouth=tuple(mouth) if mouth is not None else None)
                if patch_max > 0 and box_lean_enabled():
                    ask["box_max"] = float(patch_max)
            elif mouth is not None:
                ask["mouth"] = tuple(mouth)
            req = pickle.dumps(ask, protocol=pickle.HIGHEST_PROTOCOL)
            wait_s = REPLY_S if self.answered >= COLD_REPLIES else max(REPLY_S, COLD_S)
            deadline = time.monotonic() + wait_s
            try:
                ok = _write_all(self.p.stdin.fileno(), _HDR.pack(len(req)) + req, deadline)
            except (OSError, ValueError) as e:
                ok = False
                self.log("film process: write failed (%s)" % e)
            if not ok:
                self._kill("request not taken in %.1f s" % wait_s)
                return None
            fd = self.p.stdout.fileno()
            hdr = _read_exact(fd, _HDR.size, deadline)
            meta = _read_exact(fd, _HDR.unpack(hdr)[0], deadline) if hdr else None
            if meta is None:
                self._kill("no reply in %.1f s" % wait_s)
                return None
            self.answered += 1
            info = pickle.loads(bytes(meta))
            if not info.get("ok"):
                self.log("film process: %s" % str(info.get("error"))[:160])
                return None
            w, h = info["size"]
            mode = "L" if info.get("mode") == "L" else "RGB"
            n = 1 if mode == "L" else 3
            if w * h * n > self.size:
                self._kill("frame %dx%d does not fit" % (w, h))
                return None
            from PIL import Image
            self.frames += 1
            self.how = info.get("how")
            self.child_ms = info.get("ms")
            if info.get("cache") is not None:
                self.cache_stats = info["cache"]

            def paint(im):
                if mode == "L":                     # the coverage through render_background's own last step
                    im.putpalette(info["pal"])
                    im = im.convert("RGB")
                return im
            box = info.get("box")
            if box is not None and patch_max > 0:
                x0, y0, x1, y1 = box
                if x1 <= x0 or y1 <= y0:
                    return (None, None)
                if (x1 - x0) * (y1 - y0) <= patch_max * w * h:
                    rows = b"".join(self.mm[(y * w + x0) * n:(y * w + x1) * n] for y in range(y0, y1))
                    return (paint(Image.frombytes(mode, (x1 - x0, y1 - y0), rows)), (x0, y0))
            if mode == "L":
                # straight off the frame file: one copy (putpalette copies a mapped picture), then the palette
                return (paint(Image.frombuffer("L", (w, h), self.mm, "raw", "L", 0, 1)), None)
            return (paint(Image.frombytes(mode, (w, h), self.mm[:w * h * n])), None)

    def close(self) -> None:
        if self.p is not None:
            try:
                self.p.stdin.close()
                self.p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                self.p.kill()
            self.p = None
        try:
            self.mm.close()
            os.unlink(self.shm_path)
        except OSError:
            pass


def _lay(mm, px, size, box, box_max) -> None:
    """The picture into the frame file - only the rows of the box when the parent will read only those (a patch
    within its `box_max`, the same test the parent makes), nothing when nothing changed, else all of it."""
    w, h = size
    if box is not None and box_max is not None:
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            return                                    # nothing changed: the parent reads nothing
        if (x1 - x0) * (y1 - y0) <= float(box_max) * w * h:
            src = memoryview(px)
            for y in range(y0, y1):
                a = y * w
                mm[a + x0:a + x1] = src[a + x0:a + x1]
            return
    mm[:len(px)] = px


def _child(W: int, H: int, shm_path: str) -> int:
    # the pixel pipe is ours alone: anything an import prints goes to stderr (the wall's journal)
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    kb = pictures_out_of_heap(W, H, log=lambda m: print("house-wall-ascii: " + m, file=sys.stderr, flush=True))
    if kb:
        print("house-wall-ascii: film child: blocks of %d KiB and more are mapped on their own (half a picture)" % kb,
              file=sys.stderr, flush=True)
    import house_wall_ascii_render as R
    inp = sys.stdin.buffer
    with open(shm_path, "r+b") as f:
        mm = mmap.mmap(f.fileno(), W * H * 3)
    from PIL import ImageChops
    prev = None                 # today's path: the last RGB picture sent
    prev_pic = prev_pal = None  # the cache's path: the last coverage sent and its palette
    prev_cov = None             # today's picture, lean: the last coverage sent (and prev_pal its palette)
    cache = None
    geoms: dict = {}
    while True:
        hdr = inp.read(_HDR.size)
        if len(hdr) < _HDR.size:
            return 0
        req = pickle.loads(inp.read(_HDR.unpack(hdr)[0]))
        t0 = time.perf_counter()
        try:
            g = geoms.get(req["density"])
            if g is None:
                g = geoms[req["density"]] = R.Geom(W, H, density=req["density"])
            use = bool(req.get("cache"))
            if use and cache is None:
                try:
                    import wall_ascii_cache
                    cache = wall_ascii_cache.AsciiCache(log=lambda m: print("house-wall-ascii: " + m, file=sys.stderr,
                                                                            flush=True))
                except Exception as e:  # noqa: BLE001 - no cache: today's picture, said once
                    cache = False
                    print("house-wall-ascii: ascii cache unavailable (%s: %s)" % (type(e).__name__, e),
                          file=sys.stderr, flush=True)
            if use:
                use = bool(cache) and cache.usable() and cache.holds(W, H)
            got = None
            lean = bool(req.get("lean"))
            want_box = bool(req.get("box", True))     # an older parent always reads the box
            box_max = req.get("box_max")              # the parent's patch limit (round 12), else None
            mouth = mouth_of(req.get("mouth"))
            if use:
                try:
                    # the box is against the picture actually sent last: after today's path, the whole picture
                    if "norm" in req and getattr(cache, "disk", None) is not None:
                        cache.disk.norm = bool(req["norm"])
                    p, how, region = cache.picture(req["jpeg"], g, bool(req["own"]), req["t"], req["hole_zoom"],
                                                   req["shift"], prev_pic, mouth=mouth, frame=req.get("frame"),
                                                   clip=_clip_of(req.get("clip")))
                    pal = R.palette_for(req["brightness"])
                    got = (p, how, pal, cache.box(prev_pic, prev_pal, p, pal, region, whole_over=box_max)
                           if want_box else None)
                except Exception as e:  # noqa: BLE001 - a fault in the cache turns it off, never the film
                    cache.dead = "%s: %s" % (type(e).__name__, e)
                    print("house-wall-ascii: ascii cache: %s; off until the wall restarts" % cache.dead,
                          file=sys.stderr, flush=True)
            if got is not None:
                p, how, pal, box = got
                prev, prev_cov, prev_pic, prev_pal = None, None, p, pal
                if len(p.px) > len(mm):
                    raise ValueError("frame %dx%d is larger than the shared frame" % p.size)
                _lay(mm, p.px, p.size, box, box_max)
                meta = pickle.dumps({"ok": True, "size": p.size, "box": box, "mode": "L", "pal": pal, "how": how,
                                     "cache": cache.stats(), "ms": (time.perf_counter() - t0) * 1000.0})
                out.write(_HDR.pack(len(meta)) + meta)
                out.flush()
                try:                                  # the frame is on its way: warm the next ones from disk
                    cache.read_ahead(g, _clip_of(req.get("clip")))
                except Exception:  # noqa: BLE001
                    pass
                continue
            if prev_pic is not None:
                # back from the cache: today's glow memo is from before it, so it starts again
                prev_pic = prev_pal = None
                R._GLOW.update(n=0, im=None, fp=None, out=None)
            src = R._open(req["jpeg"])
            if lean:
                # today's picture, as its coverage: render_background up to its palette step (the parent does that
                # step, the same convert). The box only when the parent patches, on the coverage: a pixel whose
                # coverage did not change did not change colour (a palette change sends the whole picture).
                cov = R.render_coverage(src, g, bool(req["own"]), req["t"], hole_zoom=req["hole_zoom"],
                                        shift=req["shift"], mouth=mouth, frame=req.get("frame"))
                pal = R.palette_for(req["brightness"])
                box = None
                if want_box and prev_cov is not None and prev_cov.size == cov.size and prev_pal == pal:
                    bb = ImageChops.difference(prev_cov, cov).getbbox()
                    box = tuple(bb) if bb else (0, 0, 0, 0)
                prev, prev_cov, prev_pal = None, cov, pal
                px = cov.tobytes()
                if len(px) > len(mm):
                    raise ValueError("frame %dx%d is larger than the shared frame" % cov.size)
                mm[:len(px)] = px
                meta = pickle.dumps({"ok": True, "size": cov.size, "box": box, "mode": "L", "pal": pal,
                                     "how": "today", "cache": cache.stats() if cache else None,
                                     "ms": (time.perf_counter() - t0) * 1000.0})
                out.write(_HDR.pack(len(meta)) + meta)
                out.flush()
                continue
            prev_cov = None
            im = R.render_background(src, g, bool(req["own"]), req["t"],
                                     hole_zoom=req["hole_zoom"], brightness=req["brightness"], shift=req["shift"],
                                     mouth=mouth, frame=req.get("frame"))
            if im.mode != "RGB":
                im = im.convert("RGB")
            box = None
            if prev is not None and prev.size == im.size:
                bb = ImageChops.difference(prev, im).getbbox()
                box = tuple(bb) if bb else (0, 0, 0, 0)
            prev = im
            px = im.tobytes()
            if len(px) > len(mm):
                raise ValueError("frame %dx%d is larger than the shared frame" % im.size)
            mm[:len(px)] = px
            meta = pickle.dumps({"ok": True, "size": im.size, "box": box, "how": "today",
                                 "cache": cache.stats() if cache else None,
                                 "ms": (time.perf_counter() - t0) * 1000.0})
            out.write(_HDR.pack(len(meta)) + meta)
        except Exception as e:  # noqa: BLE001
            meta = pickle.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
            out.write(_HDR.pack(len(meta)) + meta)
        out.flush()


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        try:                                # the Wall's film draws on the Wall's own core (lib/paint_cores)
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import paint_cores
            paint_cores.pin_thread("wall", log=lambda m: print(m, file=sys.stderr, flush=True))
        except Exception:  # noqa: BLE001
            pass
        sys.exit(_child(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]))
    print(__doc__)
