#!/usr/bin/env python3
"""house_wall — the wall without a browser.

Backup glass for when Brave wedges. Default is wallpaper (beside the
kiosk). Fullscreen is User-gated failover, not the boot default.

A Tk window that shows what display/voiceonly-home.html shows, drawn by
house_wall_render.py from the same three sources the page reads:

    :8100/frame.jpg      Crissa (736x400, mouth already pasted)   ~12 fps
    :8100/v1/state       frame_no — the health signal            2 Hz
    :8099/v1/house       chips / headline / roll / clock         0.5 Hz
    :8099/frame          door camera (PiP + fallback ASCII)      0.7 Hz
    :8191/v1/presence    AUTH / Secure                           0.5 Hz

No Chromium, no CDP, no :9222, no --user-data-dir. If :8100 stops
advancing the wall keeps the last frame and shows FEED STALE; it never
wedges the way a renderer main thread does, and systemd Restart=always
covers a crash.

Clicks open real windows in the User Brave profile (scripts/user-brave):
    FaceTime pop  -> :8199/ (the Composer)
    dial / chips  -> :8099/panel
    camera PiP    -> :8191/visor
Empty wall click is ignored.

Env:
    HOUSE_WALL_MODE     wallpaper (default) | fullscreen
    HOUSE_WALL_FPS      target frame rate (default 12)
    HOUSE_WALL_SIZE     WxH override (default: the screen)
    HOUSE_WALL_FEED     default the player (house config wall_conductor, else the port table's player)
    HOUSE_WALL_HOUSE    default this machine's home server (house config panel_url); none in a pack
    HOUSE_WALL_PRESENCE default this machine's presence server (house config presence_url); none in a pack
    HOUSE_WALL_OPEN     command to open a URL (default scripts/user-brave, else xdg-open)
    HOUSE_WALL_ZOOM     override screen.fov_zoom_crissa for the ASCII hole (e.g. 1.0);
                        unset = follow /v1/house like the kiosk does

CLI:
    house-wall                       run the glass
    house-wall --wallpaper           desktop-type window, below everything
    house-wall --preview out.png     render one frame from the LIVE endpoints
                                     to a PNG and exit (no window)
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)

import house_wall_render as R

def _player_url() -> str:
    try:
        import crissa_ports
        return crissa_ports.url("player")
    except (ImportError, KeyError, AttributeError):
        return "http://127.0.0.1:8100"


# This machine's own services (config/house-machine.json, 2026-10-06); "" in a pack, where reads fail quietly.
FEED = os.environ.get("HOUSE_WALL_FEED") or crissa_home.house_value("wall_conductor") or _player_url()
HOUSE = os.environ.get("HOUSE_WALL_HOUSE") or crissa_home.house_value("panel_url")
PRESENCE = os.environ.get("HOUSE_WALL_PRESENCE") or crissa_home.house_value("presence_url")
STALE_S = 8.0            # frame_no unchanged this long = stale (the doc's discriminator)
LOG_EVERY_S = 60.0       # heartbeat line in the journal

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HEALTH = Path(
    os.environ.get(
        "HOUSE_WALL_HEALTH",
        str(crissa_home.HOME / "house-wall.health.json"),
    )
)


def log(msg: str):
    print(f"house-wall: {msg}", file=sys.stderr, flush=True)


def http_get(url: str, timeout: float = 2.0) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def http_get_stamped(url: str, timeout: float = 2.0) -> tuple[bytes | None, float]:
    """Body + the conductor's X-Frame-T (when that frame's mouth was picked;
    0.0 from a conductor that does not stamp). The glass canary, 2026-09-17."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            try:
                t = float(r.headers.get("X-Frame-T") or 0.0)
            except (TypeError, ValueError):
                t = 0.0
            return r.read(), t
    except Exception:
        return None, 0.0


def http_json(url: str, timeout: float = 2.0):
    b = http_get(url, timeout)
    if not b:
        return None
    try:
        return json.loads(b.decode("utf-8", "replace"))
    except Exception:
        return None


class State:
    """Everything the fetch threads write and the render loop reads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frame: bytes | None = None
        self.frame_at = 0.0
        self.frame_stamp = 0.0            # X-Frame-T of st.frame
        self.lags: list = []              # ms, frame picked -> handed to Tk
        self.frame_no = None
        self.frame_no_at = time.time()   # grace at boot: not stale until STALE_S with no frame_no
        self.house: dict = {}
        self.presence: dict = {}
        self.camera: bytes | None = None
        self.stop = threading.Event()

    def stale(self) -> bool:
        with self.lock:
            return (time.time() - self.frame_no_at) > STALE_S


def t_frames(st: State, fps: float):
    """Pull frame.jpg at the target rate; one in flight; back off when :8100 is down."""
    period = 1.0 / max(1.0, fps)
    misses = 0
    while not st.stop.is_set():
        t0 = time.time()
        b, stamp = http_get_stamped(f"{FEED}/frame.jpg", timeout=2.0)
        if b and len(b) > 500:
            with st.lock:
                st.frame = b
                st.frame_at = t0
                st.frame_stamp = stamp
            misses = 0
        else:
            misses += 1
        dt = time.time() - t0
        time.sleep(max(0.0, (period if misses == 0 else 0.5) - dt))


def t_glass(st: State):
    """THE GLASS CANARY (2026-09-17). Every 2 s: how long a frame took from the
    conductor picking its mouth to Tk holding the picture, over the last draws.
    The eye tunes lead against THIS glass; a drill cannot see it."""
    import statistics
    while not st.stop.is_set():
        time.sleep(2.0)
        with st.lock:
            lags, st.lags = st.lags, []
        if len(lags) < 5:
            continue
        lags.sort()
        body = {"who": "wall-tk", "median_ms": round(statistics.median(lags), 1),
                "p90_ms": round(lags[int(0.9 * (len(lags) - 1))], 1), "min_ms": round(lags[0], 1),
                "n": len(lags), "fps": round(len(lags) / 2.0, 1),
                "how": "X-Frame-T -> Tk image set + update_idletasks"}
        try:
            req = urllib.request.Request(f"{FEED}/v1/glass-lag", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=1.5).read()
        except Exception:
            pass


def t_state(st: State):
    """Health: frame_no from :8100/v1/state. This is the only 'is it alive' signal."""
    last = None
    while not st.stop.is_set():
        d = http_json(f"{FEED}/v1/state", timeout=2.0)
        if isinstance(d, dict):
            n = d.get("frame_no", d.get("frames", d.get("frame")))
            if n is not None and n != last:
                last = n
                with st.lock:
                    st.frame_no = n
                    st.frame_no_at = time.time()
        time.sleep(0.5)


def t_house(st: State):
    while not st.stop.is_set():
        h = http_json(f"{HOUSE}/v1/house", timeout=3.0)
        if isinstance(h, dict) and h.get("ok", True):
            with st.lock:
                st.house = h
        p = http_json(f"{PRESENCE}/v1/presence", timeout=3.0)
        if isinstance(p, dict):
            with st.lock:
                st.presence = p
        time.sleep(2.0)


def t_camera(st: State):
    while not st.stop.is_set():
        b = http_get(f"{HOUSE}/frame?t={int(time.time() * 1000)}", timeout=3.0)
        if b and len(b) > 500:
            with st.lock:
                st.camera = b
        time.sleep(1.5)


def open_url(url: str):
    """User Brave only. Never the glass's own browser profile, never xdg-open into the kiosk."""
    cmd = os.environ.get("HOUSE_WALL_OPEN")
    if not cmd:
        ub = REPO / "scripts" / "user-brave"
        cmd = str(ub) if ub.exists() else ""
    if not cmd or crissa_home.is_own_browser(cmd):
        log(f"refusing opener {cmd!r} for {url}")
        return
    try:
        subprocess.Popen(
            [cmd, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log(f"open {url}")
    except Exception as e:
        log(f"open failed: {e}")


def _frame_xid(xid: int) -> int:
    """Tk winfo_id is the inner drawable. EWMH wants the WM frame."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    try:
        out = subprocess.check_output(
            ["xwininfo", "-id", hex(int(xid))],
            env=env, text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return int(xid)
    for line in out.splitlines():
        if "Parent window id:" not in line:
            continue
        raw = line.split(":", 1)[1].split()[0]
        try:
            parent = int(raw, 16)
        except ValueError:
            return int(xid)
        if "root" in line.lower() or parent == 0:
            return int(xid)
        return parent
    return int(xid)


def pin_desktop(xid: int) -> None:
    """_NET_WM_WINDOW_TYPE_DESKTOP + below/sticky. Not layer-shell."""
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    hex_id = hex(_frame_xid(xid))
    subprocess.run(
        [
            "xprop", "-id", hex_id,
            "-f", "_NET_WM_WINDOW_TYPE", "32a",
            "-set", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DESKTOP",
        ],
        env=env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "xprop", "-id", hex_id,
            "-f", "_NET_WM_STATE", "32a",
            "-set", "_NET_WM_STATE",
            "_NET_WM_STATE_BELOW, _NET_WM_STATE_STICKY, "
            "_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER",
        ],
        env=env, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def write_health(payload: dict) -> None:
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEALTH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(HEALTH)


HOTSPOT_URL = {
    "ft": f"{FEED}/composer",
    "dial": f"{HOUSE}/panel",
    "mode": f"{HOUSE}/panel",
    "secure": f"{HOUSE}/panel",
    "camera": f"{PRESENCE}/visor",
}


def run_preview(out: str, size: tuple[int, int] | None):
    """One frame from the live endpoints, no window. Verification without touching the glass."""
    W, H = size or (2560, 1440)
    frame = http_get(f"{FEED}/frame.jpg")
    cam = http_get(f"{HOUSE}/frame")
    house = http_json(f"{HOUSE}/v1/house") or {}
    pres = http_json(f"{PRESENCE}/v1/presence") or {}
    state = http_json(f"{FEED}/v1/state") or {}
    log(f"preview: frame={'ok' if frame else 'MISSING'} camera={'ok' if cam else 'none'} "
        f"house={'ok' if house else 'MISSING'} presence={'ok' if pres else 'none'} "
        f"frame_no={state.get('frame_no', state.get('frames'))}")
    im = R.render(frame, cam, house, pres, W, H, stale=not state)
    im.save(out)
    log(f"wrote {out} ({W}x{H})")
    return 0


def run_glass(mode: str, fps: float, size: tuple[int, int] | None) -> int:
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("house-wall")
    root.configure(bg="#10131A")
    W, H = size or (root.winfo_screenwidth(), root.winfo_screenheight())
    g = R.Geom(W, H)

    def pin_now(_evt=None):
        try:
            pin_desktop(int(root.winfo_id()))
        except Exception:
            pass
        try:
            root.lower()
            root.tk.call("focus", "")
        except Exception:
            pass

    if mode == "wallpaper":
        # Tk -type desktop on the inner drawable is not enough; xprop the WM frame.
        try:
            root.attributes("-type", "desktop")
        except tk.TclError as e:
            log(f"wm -type desktop failed ({e}); xprop pin still runs")
        root.geometry(f"{W}x{H}+0+0")
        root.overrideredirect(False)
        root.bind("<Map>", pin_now)
        root.bind("<FocusIn>", pin_now)
        root.after(250, pin_now)
        root.after(300, pin_now)
    else:
        root.attributes("-fullscreen", True)
        root.geometry(f"{W}x{H}+0+0")
        try:
            root.config(cursor="none")
        except tk.TclError:
            pass

    cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bd=0, bg="#10131A")
    cv.pack(fill="both", expand=True)
    bg_item = cv.create_image(0, 0, anchor="nw")
    hud_item = cv.create_image(0, 0, anchor="nw")
    ft_item = cv.create_image(0, 0, anchor="nw")
    photos = {}   # keep PhotoImage refs alive

    st = State()
    for fn, args in ((t_frames, (st, fps)), (t_state, (st,)), (t_house, (st,)), (t_camera, (st,)), (t_glass, (st,))):
        threading.Thread(target=fn, args=args, daemon=True).start()

    out_q: queue.Queue = queue.Queue(maxsize=2)

    def render_worker():
        """Draw off the Tk thread. Background + FT per new frame; HUD once a second."""
        last_frame_at = -1.0
        last_hud = 0.0
        last_house_rev = None
        period = 1.0 / max(1.0, fps)
        while not st.stop.is_set():
            t0 = time.time()
            with st.lock:
                frame, frame_at, frame_stamp = st.frame, st.frame_at, st.frame_stamp
                house, pres, cam = st.house, st.presence, st.camera
            stale = st.stale()
            crissa = R._open(frame) if frame else None
            camim = R._open(cam) if cam else None
            pkt = {}
            if frame_at != last_frame_at or crissa is None:
                last_frame_at = frame_at
                src, own = (crissa, True) if crissa is not None else (camim, False)
                hz = R.hole_zoom_of(house) if crissa is not None else None
                if hz is not None and os.environ.get("HOUSE_WALL_ZOOM"):
                    try:
                        hz = float(os.environ["HOUSE_WALL_ZOOM"])
                    except ValueError:
                        pass
                pkt["bg"] = R.render_background(src, g, own, t0, hz)
                if crissa is not None:
                    pkt["ft"] = R.render_ft(crissa, g)
                    pkt["stamp"] = frame_stamp
            rev = (house.get("screen") or {}).get("rev")
            if (t0 - last_hud) >= 1.0 or rev != last_house_rev:
                last_hud = t0
                last_house_rev = rev
                pkt["hud"] = R.render_hud(house, pres, camim, g, t0, crissa is not None, stale)
            if pkt:
                try:
                    out_q.put(pkt, timeout=period)
                except queue.Full:
                    pass
            dt = time.time() - t0
            time.sleep(max(0.005, period - dt))

    threading.Thread(target=render_worker, daemon=True).start()

    stats = {"frames": 0, "last_log": time.time(), "was_stale": None}

    def pump():
        try:
            while True:
                pkt = out_q.get_nowait()
                if "bg" in pkt:
                    photos["bg"] = ImageTk.PhotoImage(pkt["bg"])
                    cv.itemconfig(bg_item, image=photos["bg"])
                    stats["frames"] += 1
                if "ft" in pkt:
                    im, (x, y) = pkt["ft"]
                    photos["ft"] = ImageTk.PhotoImage(im)
                    cv.itemconfig(ft_item, image=photos["ft"])
                    cv.coords(ft_item, x, y)
                if "hud" in pkt:
                    photos["hud"] = ImageTk.PhotoImage(pkt["hud"])
                    cv.itemconfig(hud_item, image=photos["hud"])
                if pkt.get("stamp"):
                    root.update_idletasks()          # the picture is Tk's now
                    lag = (time.time() - float(pkt["stamp"])) * 1000.0
                    if 0.0 <= lag < 5000.0:
                        with st.lock:
                            st.lags.append(lag)
                            del st.lags[:-600]
        except queue.Empty:
            pass
        now = time.time()
        stale = st.stale()
        if stale != stats["was_stale"]:
            stats["was_stale"] = stale
            log("feed STALE — frame_no not advancing" if stale else "feed live — frame_no advancing")
        if now - stats.get("last_health", 0) >= 1.0:
            stats["last_health"] = now
            with st.lock:
                fno = st.frame_no
                who_house, who_pres = st.house, st.presence
            auth, secure = R.who_auth(who_house, who_pres)
            mk = R.mood_of(who_house)
            listen = "LISTENING" if mk == "attending" else mk.upper()
            write_health(
                {
                    "ts": now,
                    "frame_no": fno,
                    "jpeg_n": stats["frames"],
                    "stale": stale,
                    "mode": mode,
                    "chips": {
                        "who": "User" if auth else "Guest",
                        "auth": auth,
                        "secure": secure,
                        "listen": listen,
                    },
                    "pid": os.getpid(),
                }
            )
        if now - stats["last_log"] >= LOG_EVERY_S:
            with st.lock:
                fno = st.frame_no
            log(f"ok frames={stats['frames']} ({stats['frames'] / LOG_EVERY_S:.1f}/s) frame_no={fno} stale={stale}")
            stats["frames"] = 0
            stats["last_log"] = now
        root.after(15, pump)

    def on_click(ev):
        x, y = ev.x, ev.y
        for name, (x0, y0, x1, y1) in R.HOTSPOTS.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                url = HOTSPOT_URL.get(name)
                if url:
                    open_url(url)
                return
        # empty wall: ignore (GNOME will not pass a click through a desktop window anyway)

    cv.bind("<Button-1>", on_click)
    root.bind("<Control-q>", lambda e: root.destroy())
    if mode != "wallpaper":
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))

    log(f"glass up: mode={mode} {W}x{H} grid={g.COLS}x{g.ROWS} fps={fps} feed={FEED} house={HOUSE}")
    root.after(50, pump)
    try:
        root.mainloop()
    finally:
        st.stop.set()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="house-wall — the wall without a browser")
    ap.add_argument("--wallpaper", action="store_true", help="desktop-type window below everything (default)")
    ap.add_argument("--fullscreen", action="store_true", help="take the glass (User-gated failover)")
    ap.add_argument("--fps", type=float, default=float(os.environ.get("HOUSE_WALL_FPS", "12")))
    ap.add_argument("--size", default=os.environ.get("HOUSE_WALL_SIZE", ""), help="WxH (default: the screen)")
    ap.add_argument("--preview", metavar="OUT.png", help="render one frame from the live endpoints and exit")
    a = ap.parse_args(argv)
    size = None
    if a.size:
        w, h = a.size.lower().split("x")
        size = (int(w), int(h))
    if a.preview:
        return run_preview(a.preview, size)
    mode = os.environ.get("HOUSE_WALL_MODE", "wallpaper")
    if a.wallpaper:
        mode = "wallpaper"
    if a.fullscreen:
        mode = "fullscreen"
    return run_glass(mode, a.fps, size)


if __name__ == "__main__":
    raise SystemExit(main())
