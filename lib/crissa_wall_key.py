"""The Crissa key on the desk Wall (the User 2026-10-10, after the first real-hardware test on .101: "looks great but
lacks features to operate it"; a full-screen Wall covered the dock and the top bar and nothing on screen said how to
leave).

A small key, always on screen at the bottom left of the Wall's work area (right of its left column) (inside the dock and the top bar, which a
desktop-background window sits under), opens the Crissa menu. The menu's state and actions are the player's
(lib/crissa_menu, GET/POST /v1/menu), so the browser Wall's key says and does the same:

    Microphone: on / off      her ear's lock, with its state
    Connect an agent…         the browser Connect page (Local, Claude API, xAI)
    Control Panel…            the Control Panel in the browser
    Store…                    the Crissa store's catalog (2026-10-12); Composer… when it opens one (the person's own)
    Voice ID again…           Register for the User, spoken (2026-10-12): Begin here (the login password in this
                              dialog when the player asks for proof), then counts by ear; with the player's routine
                              "video", one page in her own window (/init?voice_id=1), as on 2026-10-11
    Lip sync…                 this machine's one timing dial (2026-10-11): - / + in half film frames, the value, the
                              reachable range, and a test line (/v1/speak); kept in the state home, never the library
    Help                      the keys, how to stop, how to start again
    Exit full screen          only while this Wall is full screen (the label follows the window's own state)
    Close Agent… / Open Agent asks, then the agent goes: she stops listening and speaking and her window closes; this
                              Wall stays as the desktop (2026-10-12; was Close the Wall, which stopped this Wall).
                              Ctrl+Q and the Control Panel still close this Wall on purpose
    All OS Off…               asks, then stops everything of Crissa (both modes; was Stop Crissa until 2026-10-12)

ONE LOOK, ON BOTH WALLS (the User 2026-10-12, .101 round 14: "I like the Brave Crissa menu formatting better than the
Wall's menu format. Can we match? They both need to be more muted on the weakened template to match others", and "it
can hide with mouse still like the rest"). The key wears the rail keys' muted face (house_wall_ascii_render
_railbtn_face: the grey gradient, #99A0A9 type, the dark hairline; brighter under the pointer). The menu is the browser
page's panel (display/crissa-wall.html .cmenu), in the glass's muted palette: rows with room, hairline separators,
INK2 type, INK on a HAIR wash under the pointer, INK3 when not available, SAGE for a state that is on. And the key
fades out with the idle chrome (hide()) and comes back on the next move; while hidden its rectangle leaves the
input shape, so the glass there passes clicks to the desktop like the rest. CRISSA_CRISSA_KEY_HIDES=0 keeps the
10-10 behaviour (it dims and never goes); CRISSA_CRISSA_MENU_PANEL=0 brings back the plain Tk menu.

Its rectangle is added to the Wall's input shape (box()) while it shows, so it takes clicks while empty glass still
passes them to the desktop. Never on the house (its own rail); CRISSA_CRISSA_KEY=0 hides it.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request

GROUND, GROUND2, INK, INK2, EMBER, EMBER_DEEP = "#10131A", "#151922", "#E9E5DB", "#8B93A6", "#D9A05B", "#7A5223"
INK3, HAIR, SAGE = "#4A5163", "#242A37", "#6F9B7E"
FONT = ("DejaVu Sans Mono", 11)
# the rail keys' muted face (house_wall_ascii_render: _railbtn_face rest, _RAIL_FG, :hover brightness 1.16)
KEY_BG, KEY_BG_HOVER = "#2C2F36", "#333740"
KEY_FG, KEY_FG_HOVER = "#99A0A9", "#B1B9C4"
KEY_BD = "#15171B"
KEY_FONT = ("DejaVu Sans Mono", 9)
# the menu: the browser page's panel in the glass's muted palette
MENU_FONT = ("DejaVu Sans Mono", 10)
MENU_PAD_X, MENU_PAD_Y = 14, 7
FADE_MS, FADE_STEPS = 300, 6
# ONE MENU, ONE MARK (the User 2026-10-28: "change it from Crissa to 'Menu' ... they may change their agent and name
# so may not want to see the old name there ... I assume a whitelabel would be desired"): the key shows the mark
# and the word Menu; the agent's name appears only inside, from her manifest (_her_name). The same key opens
# everything a desk Wall offers: the places (Store/Composer, Control Panel, Krew), the quick keys (Microphone,
# Agent only, Your phone, Security), the people items, Display settings (the Wall's own Advanced panel), Help,
# Close Agent, All OS Off. The dock's keys stay as shortcuts to the same things.
KEY_TEXT = "◆ MENU"
MARGIN = 16                       # px inside the work area
REFRESH_S = 5.0                   # the menu's state is read in the background, so a click never waits on the network
WORKAREA_S = 30.0

_S: dict = {"box": None, "view": {}, "at": 0.0, "wa": None, "wa_at": 0.0, "key": None}

# THE MENU'S FOLLOW-UPS RUN ON THE TK THREAD (the User 2026-10-11: the dialogs after Microphone, Voice ID again and Stop
# Crissa never showed). Each of those waits on the player in a worker thread, and the worker called root.after(0, ...)
# to show its answer; the kit's Tcl is not built threaded, so in an idle Tk that call never ran - the lip-sync dialog's
# own finding (the clean-install trial, 2026-10-11), fixed there with a queue its dialog drains. The menu's other
# follow-ups now go the same way: a worker queues (soon), and drain() on the Tk thread runs what is queued, every
# DRAIN_MS, from install() on.
_UI: "queue.SimpleQueue" = queue.SimpleQueue()
DRAIN_MS = 100


# THE MENU'S PRESS IS TIMED (the User 2026-10-29, the first-time walk: "it takes forever to come up or it requires a
# second press"): the view answers in 0.04 s, so the wait is the Tk thread's. A 100 ms ticker keeps the Tk loop's
# longest late beat over the last TICK_WINDOW_S; each press of the key and of an item appends one line to
# <state home>/logs/menu-timing.jsonl: {"ts", "what", "shown_ms" (press to the panel drawn), "tk_late_ms"}. Report only.
# Kill CRISSA_MENU_TIMING=0.
TICK_MS, TICK_WINDOW_S = 100, 5.0
_TICK: dict = {"last": 0.0, "late": []}


def timing_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_MENU_TIMING", "1")).strip().lower() not in ("0", "off", "false", "no")


def tick(root, now=None) -> float:
    """On the Tk thread: how late this beat came (ms), kept for TICK_WINDOW_S; then the next beat."""
    now = time.time() if now is None else now
    late = max(0.0, (now - _TICK["last"]) * 1000.0 - TICK_MS) if _TICK["last"] else 0.0
    _TICK["last"] = now
    _TICK["late"] = [(t, v) for t, v in _TICK["late"] if now - t <= TICK_WINDOW_S] + [(now, late)]
    if root is not None:
        try:
            root.after(TICK_MS, tick, root)
        except Exception:  # noqa: BLE001  the Wall is closing
            pass
    return late


def tk_late_ms(now=None) -> float:
    now = time.time() if now is None else now
    return round(max([v for t, v in _TICK["late"] if now - t <= TICK_WINDOW_S] or [0.0]), 1)


def note_timing(what: str, t0: float, home=None) -> dict:
    """One line for a press (above); never raises, never waits."""
    row = {"ts": round(t0, 3), "what": str(what)[:40], "shown_ms": round((time.time() - t0) * 1000.0, 1),
           "tk_late_ms": tk_late_ms()}
    if not timing_on():
        return row
    try:
        if home is None:
            import crissa_home
            home = crissa_home.HOME
        d = os.path.join(str(home), "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "menu-timing.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return row


def soon(fn) -> None:
    """A worker hands Tk work to the Tk thread (never root.after from a worker)."""
    _UI.put(fn)


def drain(root, every_ms: int = DRAIN_MS) -> int:
    """On the Tk thread: run what the workers queued, then look again in every_ms. Returns how many ran."""
    n = 0
    while True:
        try:
            fn = _UI.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        except Exception:  # noqa: BLE001  one follow-up that fails never stops the next
            pass
        n += 1
    try:
        root.after(int(every_ms), drain, root, every_ms)
    except Exception:  # noqa: BLE001  the Wall is closing
        pass
    return n


def post_then(feed: str, body: dict, *, say, ok_note: str | None = None, fail_title: str = "Crissa", post=None,
              refresh=None, start: bool = True) -> threading.Thread:
    """One menu action that waits on the player (Microphone, Voice ID again, Stop Crissa): the POST on a worker thread,
    its follow-up (the player's error, or `ok_note`) queued for the Tk thread, where say(title, text) shows it."""
    post = post or (lambda b: _post(feed, b))

    def run():
        got = post(body) or {}
        if refresh is not None:
            refresh()
        if not got.get("ok") and got.get("error"):
            err = str(got.get("error"))
            soon(lambda: say(fail_title, err))
        elif ok_note:
            soon(lambda: say("Crissa", ok_note))
    t = threading.Thread(target=run, daemon=True, name="crissa-key-post")
    if start:
        t.start()
    return t


# VOICE ID AGAIN OPENS IN HER OWN WINDOW (the User 2026-10-11, .101 round 9): the key opened the browser Wall in the
# person's own browser for the password, no take ever reached the coach, and "no name entry avail either". Now it opens
# one page in her window (lib/crissa_window, raised): the name, ready to change, the password when the player asks for
# it, and Begin, which starts the coach's take (lib/crissa_menu act voice_id). This Wall says where it is.
VOICE_ID_PATH = "/init?voice_id=1"


def voice_id_page(feed: str) -> str:
    return feed.rstrip("/") + VOICE_ID_PATH


def _her_name() -> str:
    try:
        import crissa_home
        return crissa_home.her_name() or "Crissa"
    except Exception:  # noqa: BLE001
        return "Crissa"


def _own_window_on() -> bool:
    """Her pages open in her own window here (a pack, CRISSA_OWN_WINDOW on, no opener of the house's named)."""
    if os.environ.get("HOUSE_WALL_OPEN"):
        return False
    try:
        import crissa_window
        return bool(crissa_window.enabled())
    except Exception:  # noqa: BLE001
        return False


def voice_id_words(opened: bool, url: str, own: bool) -> str:
    """What this Wall says after the key opened (or could not open) the Voice ID page."""
    if opened and own:
        return "Voice ID is open in %s's window." % _her_name()
    if opened:
        return "Voice ID is open in your web browser."
    try:
        import crissa_window
        none_here = crissa_window.find() is None
    except Exception:  # noqa: BLE001
        none_here = False
    if none_here:
        return ("%s opens Voice ID in her own window, in Brave, Chromium or Google Chrome, and none of them is on this "
                "computer. Open this address in your web browser to do it there: %s" % (_her_name(), url))
    return "%s's window did not open. Open this address in a web browser: %s" % (_her_name(), url)


def open_voice_id(feed: str, open_url, say, own=None, start: bool = True) -> threading.Thread:
    """Open the Voice ID page (open_url: her own window for her pages), then say where it is, on the Tk thread."""
    url = voice_id_page(feed)

    def run():
        try:
            ok = bool(open_url(url))
        except Exception:  # noqa: BLE001
            ok = False
        mine = _own_window_on() if own is None else bool(own)
        text = voice_id_words(ok, url, mine)
        soon(lambda: say("Voice ID again", text))
    t = threading.Thread(target=run, daemon=True, name="crissa-key-voice-id")
    if start:
        t.start()
    return t


# ONE PRESS NEVER CLOSES THE WALL (the User 2026-10-24, .101 on 1.0.0: `click (3286,1420) -> wall_off` closed the Wall
# at once with no word, and the Shell went down behind it). The first press ARMS the key and the Wall says so; a second
# press within CRISSA_WALL_KEY_CONFIRM_S closes it. A second press rather than a dialog: a confirm box is a new
# top-level window over the Wall for the Shell to manage in the same instant, which is the one thing the close must
# not add, and the key the person is already on is the plainest "yes". The window is 3 s (a deliberate second press
# lands inside a second or two; longer and a press forgotten minutes ago would make an unrelated later click a
# close). Kill: CRISSA_WALL_KEY_CONFIRM=0 (one press closes, as 1.0.0).
WALL_KEY_CONFIRM_S = 3.0
WALL_KEY_ARMED_WORDS = "Press WALL again to close the Wall"


def confirm_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_WALL_KEY_CONFIRM", "1")).strip().lower() not in ("0", "off", "false", "no")


def confirm_window_s(env=None) -> float:
    e = os.environ if env is None else env
    try:
        return max(0.0, float(e.get("CRISSA_WALL_KEY_CONFIRM_S", WALL_KEY_CONFIRM_S)))
    except (TypeError, ValueError):
        return WALL_KEY_CONFIRM_S


def wall_off_press(mem: dict, now: float | None = None, env=None) -> "tuple[bool, str]":
    """One press of the WALL key. (close, words): close is True only on the second press inside the window (or at once
    with the confirm off); words is what the Wall says when it does not close ('' when it does). `mem` keeps the
    arming time across presses (the caller's dict)."""
    now = time.time() if now is None else now
    if not confirm_on(env):
        mem.pop("wall_off_armed", None)
        return True, ""
    armed = float(mem.get("wall_off_armed") or 0.0)
    if armed and 0.0 <= now - armed <= confirm_window_s(env):
        mem.pop("wall_off_armed", None)
        return True, ""
    mem["wall_off_armed"] = now
    return False, WALL_KEY_ARMED_WORDS


def house_key_on(env=None) -> bool:
    """THE HOUSE WALL ON THE SAME KEY (the User 2026-10-28, "Crissa Wall + Krew = house"): the house's Tk wall can wear
    the MENU key too - its places (Krew, Composer, Control Panel: the rail's own fronting of her Brave tabs), the
    microphone (the house's own voice_on / voice_off) and the Display rows. Opt-in on the house while its rail and dock
    keys are what the User uses: HOUSE_WALL_MENU_KEY=1 on house-wall-ascii (a drop-in, the restart window's)."""
    e = os.environ if env is None else env
    return str(e.get("HOUSE_WALL_MENU_KEY", "0")).strip().lower() in ("1", "on", "true", "yes")


def enabled(env=None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_CRISSA_KEY", "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import crissa_home
        return house_key_on(e) if crissa_home.is_house() else True
    except ImportError:
        return True


def hides(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CRISSA_KEY_HIDES", "1")).strip().lower() not in ("0", "off", "false", "no")


def _mix(a: str, b: str, t: float) -> str:
    """#rrggbb from a to b at t (0..1)."""
    t = max(0.0, min(1.0, float(t)))
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(round(x + (y - x) * t)) for x, y in zip(pa, pb))


def fade_colours(k: float) -> dict:
    """The key's colours at visibility k (1 = shown, 0 = gone into the glass's ground). Pure: tests read it."""
    return {"fg": _mix(GROUND, KEY_FG, k), "bg": _mix(GROUND, KEY_BG, k), "bd": _mix(GROUND, KEY_BD, k)}


def dim(idle: bool) -> None:
    """The key follows the Wall's chrome (the User 2026-10-12, round 14: "it can hide with mouse still like the rest"):
    with the idle chrome it fades into the ground and goes (its rectangle leaves the input shape); on the next move it
    comes back. Not while its menu is open. CRISSA_CRISSA_KEY_HIDES=0: the 10-10 rule, dimmed and never gone."""
    key = _S.get("key")
    if key is None or _S.get("dim") == bool(idle):
        return
    if idle and (_S.get("menu") is not None or panel_open()):
        return                                       # never while its menu or one of its pop-ups is open
    _S["dim"] = bool(idle)
    if not hides():
        try:
            key.configure(fg=INK3 if idle else KEY_FG, highlightbackground=KEY_BD)
            key.lift()
        except Exception:  # noqa: BLE001  a key being torn down
            pass
        return
    _S["fade_gen"] = _S.get("fade_gen", 0) + 1
    gen = _S["fade_gen"]
    shown_box = _S.get("shown_box")

    def step(i):
        if _S.get("fade_gen") != gen:
            return                                   # a newer show or hide took over
        k = 1.0 - i / float(FADE_STEPS) if idle else i / float(FADE_STEPS)
        c = fade_colours(k)
        try:
            key.configure(fg=c["fg"], bg=c["bg"], highlightbackground=c["bd"])
            if idle and i >= FADE_STEPS:
                key.place_forget()
                _S["box"] = None
                cb = _S.get("on_box")
                if cb:
                    cb()
                return
            if not idle and i == 0 and shown_box:
                key.place(x=shown_box[0], y=shown_box[1])
                _S["box"] = shown_box
                key.lift()
                cb = _S.get("on_box")
                if cb:
                    cb()
            if i < FADE_STEPS:
                key.after(max(1, FADE_MS // FADE_STEPS), step, i + 1)
        except Exception:  # noqa: BLE001  a key being torn down
            pass
    step(0)


def box():
    """The key's rectangle on the Wall (x0, y0, x1, y1), for the input shape; None before it is up."""
    return _S["box"]


# CLOSE AGENT SHOWS (round 15, .101 13:37: "just closed agent, nothing happened"): while the player's menu says her
# agent is closed, a small muted plate sits centred near the foot of the Wall - "Agent closed · Open Agent" - and a
# click on it opens her again; the Wall itself rests on a dimmed still (lib/house_wall_ascii agent_closed_look).
PLATE_TEXT = "Agent closed  ·  Open Agent"
PLATE_TICK_MS = 1000


def agent_closed(view: dict | None = None) -> bool:
    """Her agent is closed, as the player's menu last said (the refresh loop reads it every REFRESH_S)."""
    v = _S.get("view") if view is None else view
    return bool(((v or {}).get("agent") or {}).get("closed"))


def plate_box():
    """The plate's rectangle while it shows (for the Wall's input shape), else None."""
    return _S.get("plate_box")


# ONE PLATE AREA, THE START BAR FIRST (round 15, the User: "a bar up ... starting services ... then turns green"): the
# plate shows, in order, her agent closed (a key: Open Agent), the start bar while she still waits for her ear, a voice
# print or her voice ("Starting Crissa… ear, voice"), and once she can really hear, "Crissa is ready" in green for
# READY_HOLD_S, then nothing until the next start (keyed on the launcher's start). A later plate (the round 15 build's
# "Building Crissa…") takes the place after the start bar here, in plate_state.
READY_HOLD_S = 4.0
START_FAST_S = 1.5                  # the menu read while the start bar shows (REFRESH_S otherwise)


def plate_state(view: dict | None, now: float, mem: dict) -> dict:
    """{"kind": closed|starting|ready|building|"", "text", "click"}: what the plate shows now. `mem` carries the start
    it is following (the launcher's start time) between calls. Pure: tests read it. One plate, in this order: Agent
    closed, then the start bar, then "Building Crissa…" (round 15: her pictures for this screen being built while her
    loops are not, and Setup not covering the glass - the menu's `build` view, lib/crissa_build.plate_view)."""
    v = view or {}
    if agent_closed(v):
        return {"kind": "closed", "text": PLATE_TEXT, "click": "agent_open"}
    got = _start_plate(v, now, mem)
    if got is not None:
        return got
    b = v.get("build") or {}
    if b.get("show") and b.get("title"):
        return {"kind": "building", "text": building_text(b), "click": None}
    return {"kind": "", "text": "", "click": None}


# .101 round 29 (the User: "she was late on hits prob due to processing. Can we add that the render will improve after
# initial loading? Also, is it loading? I see no loading bar"): the Building plate was one line of text. It carries a
# bar of how much is built (the build's own count: loops built of total, or the last lap's pictures not drawn fresh)
# and says why she may lag. Kill CRISSA_BUILD_BAR=0 (the one line as before).
BUILD_BAR_CELLS = 24
BUILD_LAG_LINE = "Her timing sharpens as she finishes building."


def building_text(b: dict) -> str:
    """The Building plate's text: title · line, then (bar on) a bar of b["done"] (0..1) and the lag line. Pure."""
    head = "%s  \u00b7  %s" % (b.get("title") or "", b.get("line") or "")
    if b.get("off") or os.environ.get("CRISSA_BUILD_BAR", "1").strip().lower() in ("0", "off", "false", "no"):
        return head                            # round 40 N16: a cache that is off has no bar and no build to wait on
    try:
        f = min(1.0, max(0.0, float(b.get("done") or 0.0)))
    except (TypeError, ValueError):
        f = 0.0
    k = int(round(f * BUILD_BAR_CELLS))
    return "%s\n%s%s  %d%%\n%s" % (head, "\u2588" * k, "\u2591" * (BUILD_BAR_CELLS - k), int(round(f * 100)),
                                   BUILD_LAG_LINE)


def _start_plate(v: dict, now: float, mem: dict) -> dict | None:
    """The start bar's part of plate_state: a plate, or None when the start has nothing to say."""
    st = v.get("start") or {}
    if not st.get("applies"):
        return None
    key = str(st.get("started") or "")
    if mem.get("key") != key:
        mem.clear()
        mem["key"] = key
    if mem.get("done"):
        return None
    if not st.get("ready"):
        mem["shown"] = True
        return {"kind": "starting", "text": str(st.get("line") or "Starting Crissa…"), "click": None}
    if not mem.get("shown"):                     # ready at the first look (the Wall came up later): nothing to say
        mem["done"] = True
        return None
    mem.setdefault("ready_at", now)
    if now - mem["ready_at"] < READY_HOLD_S:
        return {"kind": "ready", "text": "Crissa is ready", "click": None}
    mem["done"] = True
    return None


def plate_place(wa: tuple, pw: int, ph: int) -> tuple:
    """(x, y) of the plate: centred in the work area, a little above its foot. Pure: tests read it."""
    x0, y0, w, h = wa
    return int(x0 + (w - pw) / 2), int(y0 + h - ph - 56)


def workarea(W: int, H: int, run=subprocess.run) -> tuple:
    """(x, y, w, h) of the desktop's work area (_NET_WORKAREA: the screen less the top bar and a fixed dock); the
    whole screen when it cannot be read."""
    try:
        r = run(["xprop", "-root", "_NET_WORKAREA"], capture_output=True, text=True, timeout=1)
        rhs = (r.stdout or "").split("=", 1)[-1]
        nums = [int(x) for x in rhs.replace(",", " ").split() if x.lstrip("-").isdigit()]
        if len(nums) >= 4 and nums[2] > 0 and nums[3] > 0:
            x, y, w, h = nums[:4]
            return (max(0, x), max(0, y), min(W, w), min(H, h))
    except Exception:  # noqa: BLE001
        pass
    return (0, 0, W, H)


HUD_COLUMN_U = 15.2               # the Wall's left column (the dial and the room panel) is about 14.4 u wide (u = W/88)


def key_place(wa: tuple, kw: int, kh: int, W: int | None = None, H: int | None = None) -> tuple:
    """(x, y) of the key's top-left: the bottom of the work area, MARGIN inside it, just right of the Wall's left
    column (its panel's frame reaches the bottom edge) - or the work area's own left edge when that is further in.
    The column is sized to a 16:9 screen's width at most, as the Wall's own tiles are (house_wall_ascii_render.Geom)."""
    x, y, w, h = wa
    wf = min(float(W), float(H) * 16.0 / 9.0) if (W and H) else float(W or 0)
    col = int(round(HUD_COLUMN_U * wf / 88.0)) if W else 0
    return (max(x + MARGIN, col), y + h - MARGIN - kh)


def _get(feed: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(feed.rstrip("/") + "/v1/menu", timeout=timeout) as r:
        d = json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
        return d if isinstance(d, dict) else {}


# 20 s (was 10, round 17 follow-ups): a change while Security is On waits up to 5 s for his face (lib/crissa_home's
# face-match sequence), on a camera that turns presence's own look, and the mic's own call to the ear after it.
def _post(feed: str, body: dict, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(feed.rstrip("/") + "/v1/menu", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return {"ok": False, "error": "HTTP %d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "the player did not answer (%s)" % type(e).__name__}


PLACES = ("composer", "panel", "krew")      # the icon row at the top of the menu: one key per place


def place_items(view: dict) -> list:
    """The places row [(label, verb, enabled)]: Store or Composer, the Control Panel, Krew (greyed when not
    installed - its reason is in the view's `unavailable`). Pure: tests read it."""
    v = view or {}
    na = {str(x.get("id") or "") for x in (v.get("unavailable") or []) if isinstance(x, dict)}
    comp = v.get("composer") or {}
    panel = v.get("panel") or {}
    krew = v.get("krew") or {}
    rows = [(str(comp.get("label") or "Composer"), "composer", bool(comp.get("url"))),
            (str(panel.get("label") or "Control Panel"), "panel", bool(panel.get("url"))),
            (str(krew.get("label") or "Krew"), "krew", bool(krew.get("url")) and "krew" not in na)]
    skin = v.get("skin") or {}
    look = v.get("look") or {}
    if look.get("action"):                  # THE AGENT LOOK POP-UP (2026-10-31): her looks, one tap each
        rows.append((str(look.get("label") or "Agent Look"), "look", True))
    elif skin.get("url"):                   # CHOOSE SKIN (the User 2026-10-31): present only when the player offers it
        rows.append((str(skin.get("label") or "Agent Look"), "skin", True))
    return rows


DIAL_ROWS = (("brightness", "Brightness", "{:.2f}"), ("density", "Resolution", "{:.2f}"), ("flourish_pct", "Flourish", "{:d} %"),
             ("work_start", "Day starts", "{:d}:00"), ("work_end", "Day ends", "{:d}:00"))
TOGGLE_ROWS = (("folders", "Desktop folders", ("shown", "hidden")), ("leaves_at_end", "Leaves at day end", ("on", "off")))


def _old_advanced_on() -> bool:
    try:
        import house_wall_dock
        return house_wall_dock.advanced_on()
    except Exception:  # noqa: BLE001  no dock module (a test, the browser Wall): the row stays as it was
        return True


def display_items(prefs: dict | None) -> list:
    """The Advanced panel's rows as menu rows (the User 2026-10-28, "bring the popup menus together"): the dials
    (verb disp:<key>, stepped - / +) and the two switches (verb disp:<key>, toggled), each carrying its value in
    its label, read from the Wall's own prefs. [] with no prefs (the browser Wall, a test). Pure."""
    if not isinstance(prefs, dict):
        return []
    out = []
    for key, name, fmt in DIAL_ROWS:
        v = prefs.get(key)
        try:
            val = fmt.format(int(round(float(v)))) if "d" in fmt else fmt.format(float(v))
        except (TypeError, ValueError):
            val = "\u2014"
        out.append(("%s: %s" % (name, val), "disp:" + key, v is not None))
    for key, name, (on_, off_) in TOGGLE_ROWS:
        out.append(("%s: %s" % (name, on_ if prefs.get(key) else off_), "disp:" + key, True))
    return out


HOUSE_HELP = ["This menu: the places, the microphone and the display. The dock's keys open the same places.",
              "More display settings opens the Wall's Advanced panel (Wall desktop, the corner dials)."]


def house_items(view: dict, fullscreen: bool, advanced: bool = False, display: dict | None = None) -> list:
    """The house Wall's menu (view["house"]): the places, the microphone, the Display rows, Help. Nothing of a kit's
    agent (Connect, Voice ID, Close Agent, All OS Off live on the house's own Control Panel). Pure."""
    v = view or {}
    mic = v.get("mic") or {}
    items = [*place_items(v), (None, None, False),
             (str(mic.get("label") or "Microphone"), "mic", bool(mic.get("ear", True)) or bool(mic.get("on"))),
             (None, None, False),
             *display_items(display),
             *([("More display settings" if display else "Display settings", "advanced", True)] if advanced else []),
             (str((v.get("help") or {}).get("label") or "Help"), "help", True)]
    if fullscreen:
        items.append(("Exit full screen", "fullscreen", True))
    return items


def menu_items(view: dict, fullscreen: bool, advanced: bool = False, display: dict | None = None) -> list:
    """[(label, verb, enabled)] in order; verb None is a separator. Pure: tests read it.
    The order since 2026-10-28 (one menu for the desk Wall): the places row; the quick keys (Microphone, Back to her
    desk, Agent only, Your phone, Security); the people and tuning items (Connect, Voice ID, Face photos, visitors,
    guests, Lip sync, Face only); Display settings (the Wall's own Advanced panel, `advanced` = the Wall gave the
    key its opener), Help, Exit full screen; Close Agent; Not on this computer; All OS Off."""
    v = view or {}
    if v.get("house"):
        return house_items(v, fullscreen, advanced, display)
    mic = v.get("mic") or {}
    ao = v.get("agent_only") or {}
    items = [*place_items(v),
             (None, None, False),
             (str(mic.get("label") or "Microphone"), "mic", bool(mic.get("ear", True)) or bool(mic.get("on"))),
             *([(str(v["desk"].get("label") or "Back to her desk"), "desk", True)]
               if isinstance(v.get("desk"), dict) else []),             # round 33: out of the close view
             *([(str(ao.get("label") or "Agent only"), "agent_only", bool(ao.get("ok", True)))]
               if isinstance(v.get("agent_only"), dict) and ao.get("label") else []),   # 2026-10-28: the Wall closes, she stays
             *([(str(v["phone"].get("label") or "Your phone"), "phone", bool(v["phone"].get("url")))]
               if isinstance(v.get("phone"), dict) else []),               # round 29: her phone's address (the QR card)
             *([(str(v["security"].get("label") or "Security"), "security", True)]
               if isinstance(v.get("security"), dict) else []),         # round 17: On / ID-only / Off
             (None, None, False),
             (str((v.get("connect") or {}).get("label") or "Connect an agent"), "connect", True),
             (str((v.get("voice_id") or {}).get("label") or "Voice ID again"), "voice_id", True),
             *([(str(v["face_again"].get("label") or "Face photos again"), "face_again", True)]
               if isinstance(v.get("face_again"), dict) else []),       # round 34: the photos alone, his proof
             *visitor_items(v),                                          # round 20: Add a visitor, Visitors
             *([(str(v["guests"].get("label") or "Edit guest list"), "guests", bool(v["guests"].get("ok", True)))]
               if isinstance(v.get("guests"), dict) and v["guests"].get("label") else []),   # round 41 (F2)
             (str((v.get("lip_sync") or {}).get("label") or "Lip sync"), "lip_sync", bool(v.get("lip_sync"))),
             *([(str(v["face"].get("label") or "Face only"), "face", True)]
               if (v.get("face") or {}).get("action") else []),             # 2026-10-16: her face in its own window
             (None, None, False),
             *display_items(display),                                        # the Advanced rows, here (2026-10-28)
             # the old panel is the house's (2026-10-29, house_wall_dock.advanced_on): no row once its rows are here
             *([("More display settings" if display else "Display settings", "advanced", True)]
               if advanced and (not display or _old_advanced_on()) else []),
             (str((v.get("help") or {}).get("label") or "Help"), "help", True)]
    if fullscreen:               # this window's own state, read as the menu opens (EE, 2026-10-10)
        items.append(("Exit full screen", "fullscreen", True))
    # CLOSE AGENT, NOT CLOSE THE WALL (the User 2026-10-12, .101 09:18: "Close the Wall" here stopped the desktop Wall
    # and he meant to let Crissa go): the agent goes, the Wall stays as the desktop. All OS Off is the menu's last item
    ag = v.get("agent") or {}
    items.append((str(ag.get("label") or "Close Agent"), str(ag.get("action") or "agent_close"), True))
    na = [x for x in (v.get("unavailable") or []) if isinstance(x, dict)]
    if na:                       # what this computer cannot do: greyed, with its reason (lib/crissa_capability)
        items.append((None, None, False))
        items.append(("Not on this computer:", "na", False))
        items += [("  %s - %s" % (x.get("label") or x.get("id"), x.get("why") or ""), "na", False) for x in na]
    items += [(None, None, False), (str((v.get("stop") or {}).get("label") or "All OS Off"), "stop", True)]
    return items


def visitor_items(view: dict) -> list:
    """ADD A VISITOR's rows (round 20): Add a visitor… (greyed with its reason while Setup runs), and Visitors… once there
    is one. None when the player offers neither (switched off, or no User yet). Pure: tests read it."""
    vs = (view or {}).get("visitors") or {}
    if not isinstance(vs, dict):
        return []
    rows = [(str(vs.get("label")), "add_visitor", bool(vs.get("ok", True)))] if vs.get("label") else []
    if vs.get("list"):
        # ONE BUTTON (round 21): adding is the Voice ID item's "Someone new"; Visitors… stays to list and remove
        rows.append((str(vs.get("list_label") or "Visitors…"), "visitors", True))
    return rows


def guest_lines(view: dict) -> list:
    """One line per kept person for Edit guest list: '<name>: N voice prints, M photos. Guest (small talk ...)'."""
    levels = (view or {}).get("access") or {}
    out = []
    for r in (view or {}).get("list") or []:
        v, f = int(r.get("voice") or 0), int(r.get("face") or 0)
        lv = levels.get(str(r.get("access") or "guest")) or {}
        out.append("%s: %d voice print%s, %d photo%s. %s (%s)" % (
            r.get("display") or r.get("person_id"), v, "" if v == 1 else "s", f, "" if f == 1 else "s",
            lv.get("label") or "Guest", lv.get("help") or ""))
    return out or ["No one is kept yet but you."]


def guest_buttons(view: dict, row: dict, *, redo, remove, access) -> list:
    """One person's keys (round 41, F2): Cancel, Remove, (User access / Guest), Redo - the last is Return's. Pure."""
    keys = [("Cancel", None), ("Remove", remove)]
    if (view or {}).get("user_access"):
        to = "guest" if str(row.get("access") or "guest") == "user" else "user"
        keys.append((str(((view.get("access") or {}).get(to) or {}).get("label") or to), lambda: access(to)))
    keys.append(("Redo", redo))
    return keys


def visitor_lines(rows: list) -> list:
    """One line per visitor for the Visitors panel: '<name>: N voice prints, M photos'. Pure: tests read it."""
    out = []
    for r in rows or []:
        v, f = int(r.get("voice") or 0), int(r.get("face") or 0)
        out.append("%s: %d voice print%s, %d photo%s" % (r.get("display") or r.get("person_id"), v, "" if v == 1 else "s",
                                                         f, "" if f == 1 else "s"))
    return out or ["No visitors yet."]


def name_popup(root, title: str, lines: list, on_text, ok_label: str = "Begin", password: bool = False,
               value: str = ""):
    """A name box on the Wall (round 20, Add a visitor): the panel with an unmasked entry; on_text(text) runs once on the
    Tk thread when he takes the default. Its own small window when the panels are off. `password` (round 21, adding a
    person needs the User): a masked box for this computer's login password below it, and on_text(text, secret)."""
    import tkinter as tk
    if panels_on():
        body, close, show, rec = _host(root, title, minw=380)
    else:
        top = tk.Toplevel(root)
        top.title(title)
        top.configure(bg=GROUND2)
        body = top

        def close(*_a):
            try:
                top.destroy()
            except tk.TclError:
                pass
        rec, show = {"keys": {}}, (lambda: None)
    for ln in lines:
        _p_text(body, str(ln))
    txt = tk.StringVar(master=body, value=str(value or ""))      # round 39 (N8): the name kept on a way back
    fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=6)
    fr.pack(fill="x")
    tk.Label(fr, text="Name", fg=INK2, bg=GROUND2, font=MENU_FONT).pack(side="left")
    entry = tk.Entry(fr, textvariable=txt, width=24, fg=INK, bg=GROUND, insertbackground=INK, relief="flat",
                     font=MENU_FONT, highlightthickness=1, highlightbackground=HAIR, highlightcolor=INK3)
    entry.pack(side="left", padx=8)
    keys_note = tk.Label(body, text="", fg=INK2, bg=GROUND2, font=MENU_FONT)       # round 39 (N12)
    keys_note.pack(fill="x")
    entry.bind("<Button-1>", lambda ev: take_keys(entry, ev, keys_note), add="+")
    pw = None
    if password:
        pw = tk.StringVar(master=body)
        pf = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=6)
        pf.pack(fill="x")
        tk.Label(pf, text="Password", fg=INK2, bg=GROUND2, font=MENU_FONT).pack(side="left")
        tk.Entry(pf, textvariable=pw, show="•", width=20, fg=INK, bg=GROUND, insertbackground=INK, relief="flat",
                 font=MENU_FONT, highlightthickness=1, highlightbackground=HAIR, highlightcolor=INK3).pack(side="left",
                                                                                                     padx=8)

    def ok(*_a):
        text = " ".join(txt.get().split())
        if not text:
            return "break"
        close()
        if pw is not None:
            secret = pw.get()
            pw.set("")
            on_text(text, secret)
        else:
            on_text(text)
        return "break"
    _p_sep(body)
    _p_row(body, ok_label, ok)
    _p_row(body, "Cancel", close)
    rec["keys"]["return"] = ok
    show()
    entry.bind("<Return>", ok)
    entry.bind("<KP_Enter>", ok)
    entry.bind("<Escape>", lambda _e: (close(), "break")[1])
    try:
        entry.focus_set()
    except tk.TclError:
        pass
    return body


VISITOR_ASK = ["Who is the visitor? Crissa learns their voice and face so she can name them on the log.",
               "They sit in front of the camera, listen, and count to six at each beep."]
# ONE BUTTON (round 21): "Someone new" under Voice ID / add a person…
PERSON_ASK = ["Who is it? Crissa learns their voice and face so she can name them on the log. They are never you.",
              "They sit in front of the camera, listen to Lila, and count to six at each beep."]
PERSON_PW = "Crissa did not see your face at the camera: type this computer's login password, or look at the camera."


def name_only_body(name: str, secret: str = "", again: bool = False, proof: str = "") -> dict:
    """The one POST of the name-only Voice ID (round 23). Pure: tests read it. `proof`: the menu proof's token (round
    41, F3), so the run it starts takes the User's proof on this screen as its own."""
    body = {"action": "voice_id", "who": "name", "name": name, "by": "the desk Wall"}
    if secret:
        body["password"] = secret
    if again:
        body["again"] = True
    if proof:
        body["proof"] = proof
    return body


def ids_proof_first_on(env=None) -> bool:
    """Round 39 (.101 round 38 N10): Add/Edit IDs asks the User's proof on the menu screen, before the name box. Kill
    CRISSA_IDS_PROOF_FIRST=0 (round 38: only the run's Ready asks it)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_IDS_PROOF_FIRST", "1")).strip().lower() not in ("0", "off", "false", "no")


IDS_PROOF_PW = "Crissa could not see your face. Type this computer's login password to add or edit IDs."


def proof_then(root, feed: str, *, say, then, post=None, popup=None) -> threading.Thread:
    """The User's proof (POST {"action": "ids_proof"}): his face, else the password box; `then()` on the Tk thread
    only when it passed. Anyone else is told why and never reaches the name box."""
    post = post or (lambda b: _post(feed, b))

    def go_on(got):
        tok = str(got.get("proof") or "")
        try:
            import inspect
            takes = bool(inspect.signature(then).parameters)
        except (TypeError, ValueError):
            takes = False
        soon((lambda: then(tok)) if takes else then)   # round 41 (F3): the token rides into the name box

    def with_password(secret):
        def run2():
            got = post({"action": "ids_proof", "by": "desk wall", "password": secret}) or {}
            if got.get("ok"):
                go_on(got)
            else:
                soon(lambda: say("Add/Edit IDs", str(got.get("why") or "That is not this computer's login password.")))
        threading.Thread(target=run2, daemon=True, name="crissa-ids-proof-pw").start()

    def run():
        got = post({"action": "ids_proof", "by": "desk wall"}) or {}
        if got.get("ok"):
            go_on(got)
        elif got.get("password"):
            def ask():
                if (popup or password_popup)(root, "Add/Edit IDs", [IDS_PROOF_PW], with_password, ok_label="Go on") is None:
                    say("Add/Edit IDs", str(got.get("why") or "Crissa could not see your face."))   # the box is off
            soon(ask)
        else:
            soon(lambda: say("Add/Edit IDs", str(got.get("why") or "Crissa could not tell it is you.")))
    t = threading.Thread(target=run, daemon=True, name="crissa-ids-proof")
    t.start()
    return t


def kept_name_redo_on(env=None) -> bool:
    """Round 39 (.101 round 38 N8): a kept name asks "<name> already exists. Redo?" in its own pop-up, and the name box
    keeps the name. Kill CRISSA_KEPT_NAME_REDO=0 (round 23: the box opens again, empty, with the question)."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_KEPT_NAME_REDO", "1")).strip().lower() not in ("0", "off", "false", "no")


def name_only_popup(root, feed: str, vi: dict, *, say, refresh=None, post=None, popup=None, again_name: str = "",
                    lead: str = "", value: str = "", dialog=None, proof: str = ""):
    """Voice ID / add a person… as one name box (round 23). A name already kept comes back with the player's question:
    the box opens again with it, and Begin with the same name records them again. `proof`: the menu proof's token."""
    no = vi.get("name_only") or {}
    post = post or (lambda b: _post(feed, b))
    lines = ([lead] if lead else []) + [str(no.get("help") or "")] + ([PERSON_PW] if no.get("password") else [])

    def go(name, secret=""):
        name = " ".join(str(name or "").split())
        again = bool(again_name) and name.casefold() == again_name.casefold()

        def run():
            got = post(name_only_body(name, secret, again, proof)) or {}
            if refresh is not None:
                refresh()
            if got.get("ask_again") and kept_name_redo_on():
                # round 39 (N8, the User: "I like leaving the name populated, make a different popup warning window";
                # "'Already exists. Redo?' is fine"): Redo records them again under their file; Cancel is the name box
                # with the name still in it
                def redo():
                    threading.Thread(target=lambda: _after(post(name_only_body(name, secret, True, proof)) or {}),
                                     daemon=True, name="crissa-key-voice-id-redo").start()

                def back():
                    name_only_popup(root, feed, vi, say=say, refresh=refresh, post=post, popup=popup, value=name,
                                    dialog=dialog, proof=proof)
                soon(lambda: (dialog or _dialog)(root, "Add/Edit IDs", ["%s already exists. Redo?" % name],
                                                [("Cancel", back), ("Redo", redo)]))
            elif got.get("ask_again"):
                soon(lambda: name_only_popup(root, feed, vi, say=say, refresh=refresh, post=post, popup=popup,
                                             again_name=name, lead=str(got.get("error") or ""), proof=proof))
            elif not got.get("ok") and got.get("error"):
                err = str(got.get("error"))
                soon(lambda: say("Voice ID", err))
        threading.Thread(target=run, daemon=True, name="crissa-key-voice-id").start()

    def _after(got):
        if refresh is not None:
            refresh()
        if not got.get("ok") and got.get("error"):
            err = str(got.get("error"))
            soon(lambda: say("Voice ID", err))
    kw = {"value": value} if value else {}
    (popup or name_popup)(root, str(no.get("ask") or "Voice ID"), [ln for ln in lines if ln], go,
                          password=bool(no.get("password")), **kw)


def who_buttons(vi: dict, me, new) -> list:
    """The one button's question (round 21): [(label, fn)] for _dialog - Cancel, Someone new, It's me (the last is
    Return's: the User's own Voice ID). Pure: tests read it."""
    who = (vi or {}).get("who") or {}
    return [("Cancel", None), (str((who.get("new") or {}).get("label") or "Someone new"), new),
            (str((who.get("me") or {}).get("label") or "It's me"), me)]


def _speak(feed: str, text: str, timeout: float = 10.0) -> dict:
    """POST /v1/speak: her test line, through the player like any line she says."""
    req = urllib.request.Request(feed.rstrip("/") + "/v1/speak", data=json.dumps({"text": text, "by": "lip sync"}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(200000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return {"ok": False, "error": "HTTP %d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "the player did not answer (%s)" % type(e).__name__}


# ---- POP-UPS ON THE WALL ITSELF (the User 2026-10-12, .101 round 15: "format update to popups, more like menu maybe")
# The Lip sync window opened as its own desktop window with a title bar and its own Close button, and so did every other
# Crissa pop-up (the Voice ID password box, the follow-up and error notes, the confirms). They are drawn on the Wall now,
# in the Crissa menu's muted panel (the same frame, palette and rows as menu_panel): a frame placed in the Wall's own
# window, never a window of its own, so it never takes the focus from the User's other windows and it stays under them
# like the Wall. While one is open the whole glass takes the clicks (the Wall's input shape, as with the Advanced
# panel), so a click off it closes it; Escape closes it, Return takes its default, - and + step the lip sync. One at a
# time: a new pop-up closes the last. CRISSA_CRISSA_PANELS_ON_WALL=0 gives each its own small window again.
TITLE_FONT = ("DejaVu Sans Mono", 10, "bold")
PANEL_WRAP = 460


def panels_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CRISSA_PANELS_ON_WALL", "1")).strip().lower() not in ("0", "off", "false", "no")


# WHERE HE IS LOOKING, AND HIS KEYS (round 16, .101 2026-10-13 00:52: "Close Agent... no box showed"; 00:54: "the
# Close Agent box ... under the User's terminal window"). The box was round 15's panel on the Wall, drawn at the middle
# of the work area - and the desk Wall is a desktop-layer window (_NET_WM_WINDOW_TYPE_DESKTOP), so everything drawn on
# it is under every other window; his terminal covered the middle. And the Wall takes no keyboard (WM_HINTS
# input=False, so a click on it never focus-raises it over the desktop icons): Return and Escape went to the window
# that had the keyboard, his terminal. Now every pop-up takes the menu's place, above the key - the spot he just
# clicked, so in view - and a pop-up opened by his click asks the Wall for the keyboard (install's `keyboard`
# hooks: the Wall lets itself take input and asks the window manager to activate it) and hands it back to the window
# that had it when the last such pop-up closes. A note that arrives later (an error from a request) is drawn in the
# same place and never takes the keyboard. Every pop-up's place is logged. CRISSA_CRISSA_POPUP_FRONT=0: round 15's
# middle of the work area, no keyboard.
def popup_front(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CRISSA_POPUP_FRONT", "1")).strip().lower() not in ("0", "off", "false", "no")


# A POP-UP OVER EVERY WINDOW (.101 10-19, the User: "Can we have the popup menu front everything when it comes up? if
# you can see the button to press it it should go above the browser next to it so i don't have to ... move something
# out of the way to see menu choices"): a pop-up on the Wall is a frame in the desk Wall's own window, a desktop-layer
# window under every other one, so a browser beside the key covered the menu above it while the key (its own window,
# kept up) stayed visible. The pop-up keeps its place above the key but is drawn in its own borderless window kept
# above every other, with the keyboard while it is open. Kill CRISSA_POPUP_ABOVE_WINDOWS=0 (a frame on the Wall).
def popup_above_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_POPUP_ABOVE_WINDOWS", "1")).strip().lower() not in ("0", "off", "false", "no")


def key_anchor(key=None):
    """The key's top-left on the Wall, also while it is faded out with the idle chrome (its last shown box)."""
    sb = _S.get("shown_box") or _S.get("box")
    if sb:
        return int(sb[0]), int(sb[1])
    if key is not None:
        try:
            return int(key.winfo_x()), int(key.winfo_y())
        except Exception:  # noqa: BLE001
            return None
    return None


def _kbd(want: bool) -> None:
    """Ask the Wall for the keyboard (want) or hand it back; idempotent. No hooks (a test, the windowed Wall): nothing."""
    fns = _S.get("kbd_fns")
    if not fns or bool(_S.get("kbd_on")) == bool(want):
        return
    _S["kbd_on"] = bool(want)
    try:
        (fns[0] if want else fns[1])()
    except Exception as e:  # noqa: BLE001  the keyboard is a nicety; the pop-up works by the mouse
        lg = _S.get("log")
        if lg:
            lg("crissa key: keyboard %s failed (%s: %s)" % ("grant" if want else "release", type(e).__name__, e))


def click_keys_on(env=None) -> bool:
    """Round 39 (.101 round 38 N12): a click in a pop-up's box takes the keyboard. Kill CRISSA_POPUP_CLICK_KEYS=0."""
    e = os.environ if env is None else env
    return str(e.get("CRISSA_POPUP_CLICK_KEYS", "1")).strip().lower() not in ("0", "off", "false", "no")


KEYS_ELSEWHERE = "The keyboard is in another window: click in the box once more."


def take_keys(entry, ev=None, note=None) -> None:
    """A CLICK IN THE BOX TAKES THE KEYBOARD (round 39, .101 round 38 N12: the 'Whose voice?' box showed a cursor and no
    key reached it while her browser window was open; the entry's focus_set() moves the focus only inside the Wall, and
    the Wall's ask for the keyboard carried no time, which the window manager may refuse while a newer window holds it).
    The click asks again with its own X time - his action - then focuses the box; when the keys still go elsewhere a
    line under the box says so (the pop-up asks for it)."""
    if not click_keys_on():
        return
    fns = _S.get("kbd_fns")
    ts = int(getattr(ev, "time", 0) or 0)
    if fns:
        try:
            try:
                fns[0](ts)
            except TypeError:
                fns[0]()
            _S["kbd_on"] = True
        except Exception as e:  # noqa: BLE001
            lg = _S.get("log")
            if lg:
                lg("crissa key: keyboard on a click failed (%s: %s)" % (type(e).__name__, e))
    try:
        entry.focus_force()
    except Exception:  # noqa: BLE001
        pass

    def check():
        try:
            has = entry.focus_get() is entry
        except Exception:  # noqa: BLE001
            has = True
        if note is not None:
            try:
                note.configure(text="" if has else KEYS_ELSEWHERE)
            except Exception:  # noqa: BLE001
                pass
        lg = _S.get("log")
        if lg and not has:
            lg("crissa key: a click in the box did not bring the keyboard (another window has it)")
    try:
        entry.after(300, check)
    except Exception:  # noqa: BLE001
        pass


def _kbd_settle() -> None:
    """After a pop-up closed: keep the keyboard if the pop-up that replaced it wants it, else hand it back."""
    p = _S.get("panel")
    _kbd(bool(p and p.get("kbd")))


def panel_open() -> bool:
    """Is a pop-up open on the Wall (the Wall then lets the whole glass take clicks)?"""
    return _S.get("panel") is not None


def panel_box():
    p = _S.get("panel")
    return p.get("box") if p else None


def close_panel() -> bool:
    p = _S.get("panel")
    if not p:
        return False
    p["close"]()
    return True


def panel_key(name: str) -> bool:
    """The Wall's keys while a pop-up is open: escape, return, minus, plus. True when the pop-up took it."""
    p = _S.get("panel")
    if not p:
        return False
    if name == "escape":
        p["close"]()
        return True
    fn = (p.get("keys") or {}).get(name)
    if fn:
        fn()
        return True
    return False


def panel_place(anchor: str, wa: tuple, size: tuple, key_xy: tuple | None = None, gap: int = 6) -> tuple:
    """Where a pop-up of `size` (w, h) goes: above the key (x of the key, its top less the gap) or centred across the
    work area `wa` (x, y, w, h), a third of the way down. Pure: tests read it."""
    w, h = int(size[0]), int(size[1])
    if anchor == "at" and key_xy is not None:          # beside what he pressed: under it, never off the work area
        x = max(int(wa[0]), min(int(key_xy[0]) - w // 2, int(wa[0] + wa[2]) - w))
        y = int(key_xy[1]) + gap
        if y + h > int(wa[1] + wa[3]):
            y = max(int(wa[1]), int(key_xy[1]) - h - gap)
        return x, y
    if anchor == "key" and key_xy is not None:
        return int(key_xy[0]), max(int(wa[1]), int(key_xy[1]) - h - gap)
    return int(wa[0] + max(0, (wa[2] - w) // 2)), int(wa[1] + max(0, (wa[3] - h) // 3))


def _host(root, title: str | None, *, anchor: str = "center", key=None, minw: int = 300, keyboard: bool = True,
          at=None):
    """(body, close, show, rec) for one pop-up: `body` takes its widgets; show() places it; close() takes it away. On
    the Wall (a frame in the Wall's own window) unless CRISSA_CRISSA_PANELS_ON_WALL=0 (its own small window). Round 16:
    above the key and, when `keyboard` (opened by his click), with the keyboard while it is open."""
    import tkinter as tk
    close_panel()
    on_wall = panels_on()
    front = popup_front()
    if front:
        anchor = "key"
    if at is not None and popup_at_press_on():
        anchor = "at"                              # placed at the press; the keyboard is still granted (front)
    above = bool(on_wall and popup_above_on())
    if on_wall and above:
        outer = tk.Toplevel(root, bg=HAIR, bd=0, highlightthickness=0)
        outer.withdraw()
        outer.overrideredirect(True)
        try:
            outer.attributes("-topmost", True)
        except tk.TclError:
            pass
    elif on_wall:
        outer = tk.Frame(root, bg=HAIR, bd=0, highlightthickness=0)
    else:
        outer = tk.Toplevel(root)
        outer.title(title or "Crissa")
        outer.configure(bg=HAIR)
        try:
            outer.attributes("-type", "dialog")
        except tk.TclError:
            pass
    body = tk.Frame(outer, bg=GROUND2, padx=0, pady=6)
    body.pack(padx=1, pady=1, fill="both", expand=True)
    if title:
        tk.Label(body, text=title, fg=INK, bg=GROUND2, font=TITLE_FONT, anchor="w", padx=MENU_PAD_X,
                 pady=MENU_PAD_Y).pack(fill="x")
    rec = {"frame": outer, "box": None, "keys": {}, "on_wall": on_wall, "closed": False,
           "kbd": bool(on_wall and front and keyboard), "title": title or "menu"}

    def close(*_a):
        if rec["closed"]:
            return
        if os.environ.get("CRISSA_POPUP_CLOSE_TRACE", "0") != "0":     # .101 round 31 probe: who closed the pop-up (on: =1)
            import traceback as _tb
            lg = _S.get("log")
            if lg:
                lg("crissa key: pop-up %r closed by %s" % (rec.get("title"), " <- ".join(
                    "%s:%d" % (f.name, f.lineno) for f in _tb.extract_stack()[-6:-1][::-1])))
        rec["closed"] = True
        if _S.get("panel") is rec:
            _S["panel"] = None
        try:
            outer.destroy()
        except tk.TclError:
            pass
        cb = _S.get("on_box")
        if cb and on_wall:
            try:
                cb()
            except Exception:  # noqa: BLE001  the Wall going away
                pass
        if rec["kbd"]:
            try:                                     # a pop-up opened in the same click keeps it (menu -> confirm)
                root.after_idle(_kbd_settle)
            except tk.TclError:
                _kbd_settle()
    rec["close"] = close

    def show():
        outer.update_idletasks()
        w, h = max(minw, outer.winfo_reqwidth()), outer.winfo_reqheight()
        if on_wall:
            W_ = root.winfo_width() if root.winfo_width() > 1 else root.winfo_screenwidth()
            H_ = root.winfo_height() if root.winfo_height() > 1 else root.winfo_screenheight()
            wa = _S.get("wa") or (0, 0, W_, H_)
            if anchor == "at":
                kxy = (int(at[0]), int(at[1]))
            elif front:
                kxy = key_anchor(key)
            else:
                kxy = (key.winfo_x(), key.winfo_y()) if (key is not None and anchor == "key") else None
            x, y = panel_place(anchor if kxy is not None else "center", wa, (w, h), kxy)
            if above:
                outer.geometry("%dx%d+%d+%d" % (w, h, root.winfo_rootx() + x, root.winfo_rooty() + y))
                outer.deiconify()
                try:
                    outer.attributes("-topmost", True)
                except tk.TclError:
                    pass
                outer.lift()
                outer.bind("<Escape>", close)
                for nm, seqs in (("return", ("<Return>", "<KP_Enter>")), ("minus", ("<minus>", "<KP_Subtract>")),
                                 ("plus", ("<plus>", "<KP_Add>"))):
                    for seq in seqs:
                        outer.bind(seq, lambda _e, n=nm: (rec["keys"].get(n) or (lambda: None))())
                # .101 10-19 (the User: "close it if I click anywhere else"): a click in another window makes that
                # window the active one (_NET_ACTIVE_WINDOW), and that closes the pop-up. Round 31: the focus was the
                # wrong signal - on GNOME the pointer moving onto the pop-up took its focus and the menu closed under
                # the hover ("wont stay open"). The active window does not change on a hover. A click on the Wall
                # still closes it (gate_click); Escape too.
                try:
                    outer.focus_force()
                except tk.TclError:
                    pass

                def _active():
                    import re
                    try:
                        r = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True,
                                           timeout=1)
                        m = re.findall(r"0x[0-9a-fA-F]+", r.stdout or "")
                        return int(m[0], 16) if m else None
                    except Exception:  # noqa: BLE001  no xprop: only the Wall and Escape close it
                        return None

                def _watch(base=None):
                    if rec["closed"]:
                        return
                    now = _active()
                    if base is None:
                        base = now if now is not None else -1
                    elif now not in (None, 0, base):
                        close()
                        return
                    try:
                        outer.after(300, lambda: _watch(base))
                    except tk.TclError:
                        pass
                outer.after(400, _watch)
            else:
                outer.place(x=x, y=y, width=w, height=h)
            outer.lift()
            rec["box"] = (x, y, x + w, y + h)
            cb = _S.get("on_box")
            if cb:
                cb()
            if rec["kbd"]:
                _kbd(True)
            lg = _S.get("log")
            if lg:
                lg("crissa key: pop-up %r on the Wall at %d,%d-%d,%d%s" % (rec["title"], x, y, x + w, y + h,
                                                                         ", keyboard" if _S.get("kbd_on") else ""))
        else:
            sw, sh = outer.winfo_screenwidth(), outer.winfo_screenheight()
            outer.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))
            outer.bind("<Escape>", close)
            for nm, seq in (("return", "<Return>"), ("minus", "<minus>"), ("plus", "<plus>")):
                outer.bind(seq, lambda _e, n=nm: (rec["keys"].get(n) or (lambda: None))())
            outer.lift()
    if on_wall:
        _S["panel"] = rec
    return body, close, show, rec


def _p_text(body, text: str, fg: str = INK2):
    import tkinter as tk
    lab = tk.Label(body, text=text, fg=fg, bg=GROUND2, font=MENU_FONT, justify="left", anchor="w",
                   wraplength=PANEL_WRAP, padx=MENU_PAD_X, pady=2)
    lab.pack(fill="x")
    return lab


def _p_sep(body):
    import tkinter as tk
    tk.Frame(body, bg=HAIR, height=1).pack(fill="x", pady=6)


def _p_row(body, label: str, fn, enabled: bool = True, fg: str = INK2):
    """A menu row: muted type, INK on a HAIR wash under the pointer, a click runs fn."""
    import tkinter as tk
    fg_ = fg if enabled else INK3
    row = tk.Label(body, text=label, fg=fg_, bg=GROUND2, font=MENU_FONT, anchor="w", padx=MENU_PAD_X,
                   pady=MENU_PAD_Y, cursor="hand2" if enabled else "arrow")
    row.pack(fill="x")
    if enabled:
        row.bind("<Enter>", lambda _e: row.config(bg=HAIR, fg=SAGE if fg_ == SAGE else INK))
        row.bind("<Leave>", lambda _e: row.config(bg=GROUND2, fg=fg_))
        row.bind("<Button-1>", lambda _e: (fn(), "break")[1])
    row.enabled = enabled
    return row


def _p_set(row, enabled: bool) -> None:
    """Enable or grey a row made by _p_row (the lip sync's ends of range)."""
    row.enabled = bool(enabled)
    row.config(fg=INK2 if enabled else INK3, cursor="hand2" if enabled else "arrow")


PLACE_MARKS = {"composer": "\u25b6", "panel": "\u2699", "krew": "\u2692", "skin": "\u25c8", "look": "\u25c8"}   # the dock keys' own glyphs


def _p_places(body, items: list):
    """The places row: the dock keys' glyphs and names side by side, one key each ([(label, fn, enabled)]); a place
    this install does not have stands greyed, never gone (the User 2026-10-28)."""
    import tkinter as tk
    fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=4)
    fr.pack(fill="x")
    out = []
    for i, (label, fn, enabled) in enumerate(items):
        cell = tk.Frame(fr, bg=HAIR, padx=1, pady=1)
        cell.pack(side="left", expand=True, fill="x", padx=(0 if i == 0 else 8, 0))
        lab = tk.Label(cell, text=label, fg=INK2 if enabled else INK3, bg=GROUND2, font=MENU_FONT, pady=MENU_PAD_Y,
                       cursor="hand2" if enabled else "arrow")
        lab.pack(fill="x")
        lab.enabled = bool(enabled)
        if enabled:
            lab.bind("<Button-1>", lambda _e, f=fn: (f(), "break")[1])
            lab.bind("<Enter>", lambda _e, lb=lab: lb.config(bg=HAIR, fg=INK))
            lab.bind("<Leave>", lambda _e, lb=lab: lb.config(bg=GROUND2, fg=INK2))
        out.append(lab)
    return out


def _p_dial(body, label: str, down, up, enabled: bool = True):
    """A Display row: the name and value at the left, - and + keys at the right (the Advanced panel's quiet_step)."""
    import tkinter as tk
    fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=2)
    fr.pack(fill="x")
    lab = tk.Label(fr, text=label, fg=INK2 if enabled else INK3, bg=GROUND2, font=MENU_FONT, anchor="w", pady=MENU_PAD_Y - 2)
    lab.pack(side="left", fill="x", expand=True)
    keys = []
    for text, fn in (("+", up), ("\u2212", down)):
        cell = tk.Frame(fr, bg=HAIR, padx=1, pady=1)
        cell.pack(side="right", padx=(6, 0))
        k = tk.Label(cell, text=text, fg=INK2 if enabled else INK3, bg=GROUND2, font=MENU_FONT, padx=10, pady=1,
                     cursor="hand2" if enabled else "arrow")
        k.pack()
        if enabled:
            k.bind("<Button-1>", lambda _e, f=fn: (f(), "break")[1])
            k.bind("<Enter>", lambda _e, kk=k: kk.config(bg=HAIR, fg=INK))
            k.bind("<Leave>", lambda _e, kk=k: kk.config(bg=GROUND2, fg=INK2))
        keys.append(k)
    return lab, keys


def _display_prefs() -> dict | None:
    """The Wall's prefs for the Display rows (None where the Wall gave the key no display hooks)."""
    d = _S.get("display")
    try:
        return d["view"]() if d and callable(d.get("view")) else None
    except Exception:  # noqa: BLE001  a prefs read that fails leaves the rows out, never the menu
        return None


def _fullscreen(root) -> bool:
    try:
        return bool(root.attributes("-fullscreen"))
    except Exception:  # noqa: BLE001
        return False


def _p_pair(body, items: list):
    """Two keys side by side, as the browser menu's lip-row: [(label, fn)] -> the rows."""
    import tkinter as tk
    fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=4)
    fr.pack(fill="x")
    out = []
    for i, (label, fn) in enumerate(items):
        cell = tk.Frame(fr, bg=HAIR, padx=1, pady=1)
        cell.pack(side="left", expand=True, fill="x", padx=(0 if i == 0 else 8, 0))
        lab = tk.Label(cell, text=label, fg=INK2, bg=GROUND2, font=MENU_FONT, pady=MENU_PAD_Y, cursor="hand2")
        lab.pack(fill="x")
        lab.enabled = True

        def click(_e, lb=lab, f=fn):
            if getattr(lb, "enabled", True):
                f()
            return "break"
        lab.bind("<Button-1>", click)
        lab.bind("<Enter>", lambda _e, lb=lab: lb.config(bg=HAIR, fg=INK) if lb.enabled else None)
        lab.bind("<Leave>", lambda _e, lb=lab: lb.config(bg=GROUND2, fg=INK2 if lb.enabled else INK3))
        out.append(lab)
    return out


def lip_sync_dialog(root, feed: str, ls: dict, post=None, speak=None):
    """LIP SYNC on the Wall, in the menu's panel (2026-10-12, round 15): the value, - later / + earlier side by side,
    the test line, back to 0, the range, the note, Done. The same calls as below (the old window): each network call
    on a worker, its answer drained on the Tk thread."""
    if not panels_on():
        return _lip_sync_window(root, feed, ls, post=post, speak=speak)
    import queue
    import tkinter as tk
    post = post or (lambda body: _post(feed, body))
    speak = speak or (lambda text: _speak(feed, text))
    st = {"ls": dict(ls or {})}
    answers: queue.Queue = queue.Queue()
    body, close, show, rec = _host(root, "Lip sync", minw=340)
    value = _p_text(body, "", fg=INK)

    def send(b):
        def run():
            got = post(dict(b, action="lip_sync"))

            def back():
                if isinstance(got.get("lip_sync"), dict) and got["lip_sync"]:
                    st["ls"] = got["lip_sync"]
                note.configure(text=str(got.get("note") or ("" if got.get("ok") else got.get("error") or "")))
                paint()
            answers.put(back)
        threading.Thread(target=run, daemon=True).start()

    def test():
        def run():
            got = speak(str(st["ls"].get("test_line") or "Watch my lips meet on every p, b and m."))
            answers.put(lambda: note.configure(text="Saying the test line…" if got.get("ok") else
                                               str(got.get("error") or "She could not say it.")))
        threading.Thread(target=run, daemon=True).start()
    minus, plus = _p_pair(body, [("-  later", lambda: send({"step": -1})), ("+  earlier", lambda: send({"step": 1}))])
    rng = _p_text(body, "")
    how = _p_text(body, "")
    _p_sep(body)
    _p_row(body, "Say a test line", test)
    _p_row(body, "Back to 0", lambda: send({"reset": True}))
    note = _p_text(body, "")
    _p_sep(body)
    _p_row(body, "Done", close)

    def paint():
        ls_ = st["ls"]
        value.configure(text="Now: %s" % (ls_.get("value") or "0"))
        rng.configure(text=str(ls_.get("range") or ""))
        how.configure(text=str(ls_.get("how") or ""))
        k, lo, hi = int(ls_.get("halves") or 0), ls_.get("min_halves"), ls_.get("max_halves")
        for lab, ok in ((minus, not (lo is not None and k <= int(lo))), (plus, not (hi is not None and k >= int(hi)))):
            lab.enabled = ok
            lab.config(fg=INK2 if ok else INK3, cursor="hand2" if ok else "arrow")

    def drain():
        try:
            while True:
                answers.get_nowait()()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if not rec["closed"]:
            try:
                rec["frame"].after(100, drain)
            except tk.TclError:
                pass
    rec["keys"].update(minus=lambda: send({"step": -1}) if minus.enabled else None,
                       plus=lambda: send({"step": 1}) if plus.enabled else None, **{"return": close})
    paint()
    show()
    drain()
    return rec["frame"]


def _lip_sync_window(root, feed: str, ls: dict, post=None, speak=None):
    """LIP SYNC (the User 2026-10-11, before the .101 demo): this machine's one timing dial. - / + move half a film
    frame and save at once (POST /v1/menu {"action": "lip_sync", "step": -1|+1}); the value and the reachable range are
    the player's answer; "Say a test line" speaks through /v1/speak, so her next line already wears the change.
    Network calls run off the Tk thread and hand their answer back through a queue the dialog drains on its own thread
    (the kit's Tcl is not built threaded: a worker thread's root.after never ran, so the value stayed put - found by the
    clean-install trial, 2026-10-11)."""
    import queue
    import tkinter as tk
    post = post or (lambda body: _post(feed, body))
    speak = speak or (lambda text: _speak(feed, text))
    st = {"ls": dict(ls or {})}
    answers: queue.Queue = queue.Queue()
    top = tk.Toplevel(root)
    top.title("Lip sync")
    top.configure(bg=GROUND)
    try:
        top.attributes("-type", "dialog")
    except tk.TclError:
        pass
    top.attributes("-topmost", True)
    tk.Label(top, text="Lip sync", fg=EMBER, bg=GROUND, font=("DejaVu Sans Mono", 12, "bold")).pack(
        anchor="w", padx=16, pady=(14, 6))
    value = tk.Label(top, text="", fg=INK, bg=GROUND, font=("DejaVu Sans Mono", 12), justify="left", wraplength=620)
    value.pack(anchor="w", padx=16, pady=2)
    row = tk.Frame(top, bg=GROUND)
    row.pack(anchor="w", padx=16, pady=8)
    note = tk.Label(top, text="", fg=INK2, bg=GROUND, font=FONT, justify="left", wraplength=620)
    rng = tk.Label(top, text="", fg=INK2, bg=GROUND, font=FONT, justify="left", wraplength=620)
    how = tk.Label(top, text="", fg=INK2, bg=GROUND, font=FONT, justify="left", wraplength=620)

    def paint():
        ls_ = st["ls"]
        value.configure(text="Now: %s" % (ls_.get("value") or "0"))
        rng.configure(text=str(ls_.get("range") or ""))
        how.configure(text=str(ls_.get("how") or ""))
        k, lo, hi = int(ls_.get("halves") or 0), ls_.get("min_halves"), ls_.get("max_halves")
        minus.configure(state="disabled" if (lo is not None and k <= int(lo)) else "normal")
        plus.configure(state="disabled" if (hi is not None and k >= int(hi)) else "normal")

    def send(body):
        def run():
            got = post(dict(body, action="lip_sync"))

            def back():
                if isinstance(got.get("lip_sync"), dict) and got["lip_sync"]:
                    st["ls"] = got["lip_sync"]
                note.configure(text=str(got.get("note") or ("" if got.get("ok") else got.get("error") or "")))
                paint()
            answers.put(back)
        threading.Thread(target=run, daemon=True).start()

    def test():
        def run():
            got = speak(str(st["ls"].get("test_line") or "Watch my lips meet on every p, b and m."))
            answers.put(lambda: note.configure(text="Saying the test line…" if got.get("ok") else
                                               str(got.get("error") or "She could not say it.")))
        threading.Thread(target=run, daemon=True).start()

    def drain():
        """On the Tk thread: apply each answer the workers handed back, then look again in 100 ms."""
        try:
            while True:
                answers.get_nowait()()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            top.after(100, drain)
        except tk.TclError:                     # the dialog is closed
            pass

    def btn(parent, label, fn, **kw):
        return tk.Button(parent, text=label, command=fn, fg=INK, bg=GROUND2, activebackground=EMBER_DEEP,
                         activeforeground=INK, disabledforeground=INK2, relief="flat", font=FONT, padx=14, **kw)
    minus = btn(row, "-  later", lambda: send({"step": -1}))
    minus.pack(side="left", padx=(0, 4))
    plus = btn(row, "+  earlier", lambda: send({"step": 1}))
    plus.pack(side="left", padx=4)
    btn(row, "Say a test line", test).pack(side="left", padx=(16, 4))
    btn(row, "Back to 0", lambda: send({"reset": True})).pack(side="left", padx=4)
    rng.pack(anchor="w", padx=16, pady=2)
    how.pack(anchor="w", padx=16, pady=2)
    note.pack(anchor="w", padx=16, pady=(2, 4))
    bar = tk.Frame(top, bg=GROUND)
    bar.pack(anchor="e", padx=16, pady=14)

    def close(*_a):
        try:
            top.destroy()
        except tk.TclError:
            pass
    btn(bar, "Close", close).pack(side="left", padx=4)
    top.bind("<Escape>", close)
    top.bind("<minus>", lambda _e: send({"step": -1}))
    top.bind("<plus>", lambda _e: send({"step": 1}))
    paint()
    drain()
    top.update_idletasks()
    sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
    top.geometry("+%d+%d" % ((sw - top.winfo_reqwidth()) // 2, (sh - top.winfo_reqheight()) // 3))
    top.lift()
    try:
        top.focus_force()
    except tk.TclError:
        pass
    return top


# VOICE ID AGAIN, SPOKEN, FROM THIS WALL (the User 2026-10-12, .101 round 10: her window never came in front of the
# full-screen Wall, the takes ran with nothing to read, and the plate had no way out). With the player's Voice ID
# routine "verbal" (lib/crissa_init.voice_id_routine) the key asks here and starts the take itself: Begin, or, when the
# player asks for proof, the computer's login password typed into this dialog (sent once to the player, which checks it
# through presence's own PAM path; never kept, never logged). The takes are then spoken and counted; this Wall's own
# plate follows them and ends by itself. CRISSA_VOICE_ID_KEY_VERBAL=0 opens the page again.
VERBAL_KEY = os.environ.get("CRISSA_VOICE_ID_KEY_VERBAL", "1").strip().lower() not in ("0", "off", "false", "no")
VERBAL_LINES = ["Four short recordings, by ear: you hear a count to six, then you count the same way at each beep,",
                "facing forward, then with your head turned a little to each side. Nothing to read. Your voice",
                "prints are added to the ones she has."]


def voice_id_verbal_body(vi: dict, password: str = "") -> dict:
    """The POST the dialog sends: {"action": "voice_id", "by": "desk wall"} and the password when one was typed."""
    body = {"action": "voice_id", "by": "desk wall"}
    if vi.get("name"):
        body["name"] = str(vi["name"])[:40]
    if password:
        body["password"] = password
    return body


def voice_id_verbal_start(root, feed: str, vi: dict, *, say, refresh=None, post=None, dialog=None) -> str:
    """The desk key's spoken Voice ID again: 'password' (the one box, only when the player asks for proof) or 'started'
    (the POST at once, nothing on screen but the coach's own small plate; only an error is said)."""
    if vi.get("ok") is False and vi.get("password"):
        (dialog or voice_id_verbal_dialog)(root, feed, vi, say=say, refresh=refresh, post=post)
        return "password"
    post_then(feed, voice_id_verbal_body(vi), say=say, ok_note=None, fail_title="Voice ID again", refresh=refresh,
              post=post)
    return "started"


def voice_id_verbal_dialog(root, feed: str, vi: dict, *, say, refresh=None, post=None):
    """Begin (and the password when asked) for a spoken Voice ID again, on the Wall in the menu's panel (round 15): the
    words, the password box when the player asks for proof, Cancel and Begin (Return). The POST on a worker. A refused
    password says so and the menu opens it again."""
    if not panels_on():
        return _voice_id_window(root, feed, vi, say=say, refresh=refresh, post=post)
    import tkinter as tk
    need_pw = bool(vi.get("ok") is False and vi.get("password"))
    body, close, show, rec = _host(root, "Voice ID again", minw=380)
    for ln in (["Voice ID again needs this computer's login password first."] if need_pw else VERBAL_LINES):
        _p_text(body, ln)
    pw = tk.StringVar(master=body)
    entry = None
    if need_pw:
        fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=6)
        fr.pack(fill="x")
        tk.Label(fr, text="Login password", fg=INK2, bg=GROUND2, font=MENU_FONT).pack(side="left")
        entry = tk.Entry(fr, textvariable=pw, show="\u2022", width=24, fg=INK, bg=GROUND, insertbackground=INK,
                         relief="flat", font=MENU_FONT, highlightthickness=1, highlightbackground=HAIR,
                         highlightcolor=INK3)
        entry.pack(side="left", padx=8)

    def begin(*_a):
        secret = pw.get() if need_pw else ""
        if need_pw and not secret:
            return
        pw.set("")
        close()
        post_then(feed, voice_id_verbal_body(vi, secret), say=say, ok_note=None, fail_title="Voice ID again",
                  refresh=refresh, post=post)          # no "starting" box: the glass's plate says it (2026-10-12)
    _p_sep(body)
    _p_row(body, "Begin", begin)
    _p_row(body, "Cancel", close)
    rec["keys"]["return"] = begin
    show()
    if entry is not None:
        entry.bind("<Return>", begin)
        entry.bind("<Escape>", close)
        try:
            entry.focus_set()                 # inside the Wall only: never takes the focus from another window
        except tk.TclError:
            pass
    return rec["frame"]


def face_again_dialog(root, feed: str, *, say, refresh=None, post=None):
    """FACE PHOTOS AGAIN (round 34): the login password box when the player asks for proof (Voice ID again's own rule),
    on the Wall in the menu's panel; Begin posts it on a worker. The password is never kept."""
    import tkinter as tk
    body, close, show, rec = _host(root, "Face photos again", minw=380)
    _p_text(body, "Face photos again needs this computer's login password first.")
    pw = tk.StringVar(master=body)
    fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=6)
    fr.pack(fill="x")
    tk.Label(fr, text="Login password", fg=INK2, bg=GROUND2, font=MENU_FONT).pack(side="left")
    entry = tk.Entry(fr, textvariable=pw, show="\u2022", width=24, fg=INK, bg=GROUND, insertbackground=INK,
                     relief="flat", font=MENU_FONT, highlightthickness=1, highlightbackground=HAIR, highlightcolor=INK3)
    entry.pack(side="left", padx=8)

    def begin(*_a):
        secret = pw.get()
        if not secret:
            return
        pw.set("")
        close()
        post_then(feed, {"action": "face_again", "by": "the desk Wall", "password": secret}, say=say, ok_note=None,
                  fail_title="Face photos again", refresh=refresh, post=post)
    _p_sep(body)
    _p_row(body, "Begin", begin)
    _p_row(body, "Cancel", close)
    rec["keys"]["return"] = begin
    show()
    entry.bind("<Return>", begin)
    entry.bind("<Escape>", close)
    try:
        entry.focus_set()
    except tk.TclError:
        pass
    return rec["frame"]


def _voice_id_window(root, feed: str, vi: dict, *, say, refresh=None, post=None):
    """The 10-12 morning's own window (CRISSA_CRISSA_PANELS_ON_WALL=0)."""
    import tkinter as tk
    need_pw = bool(vi.get("ok") is False and vi.get("password"))
    top = _dialog_window(root, "Voice ID again", ["Voice ID again needs this computer's login password first."]
                         if need_pw else VERBAL_LINES, [])
    pw = tk.StringVar(master=top)
    entry = None
    if need_pw:
        row = tk.Frame(top, bg=GROUND)
        row.pack(anchor="w", padx=16, pady=(6, 2), fill="x")
        tk.Label(row, text="Login password", fg=INK2, bg=GROUND, font=FONT).pack(side="left")
        entry = tk.Entry(row, textvariable=pw, show="\u2022", width=28, fg=INK, bg=GROUND2, insertbackground=INK,
                         relief="flat", font=FONT)
        entry.pack(side="left", padx=8)
    bar = tk.Frame(top, bg=GROUND)
    bar.pack(anchor="e", padx=16, pady=14)

    def close(*_a):
        try:
            top.destroy()
        except tk.TclError:
            pass

    def begin(*_a):
        secret = pw.get() if need_pw else ""
        if need_pw and not secret:
            return
        pw.set("")
        close()
        post_then(feed, voice_id_verbal_body(vi, secret), say=say, ok_note=None, fail_title="Voice ID again",
                  refresh=refresh, post=post)          # no "starting" box: the glass's plate says it (2026-10-12)
    for label, fn in (("Cancel", close), ("Begin", begin)):
        tk.Button(bar, text=label, command=fn, fg=INK, bg=GROUND2, activebackground=EMBER_DEEP, activeforeground=INK,
                  relief="flat", font=FONT, padx=14).pack(side="left", padx=4)
    top.bind("<Return>", begin)
    if entry is not None:
        try:
            entry.focus_force()
        except tk.TclError:
            pass
    return top


VERBAL_NOTE = "Voice ID is starting: listen, and count to six at each beep."


def _dialog(root, title: str, lines: list, buttons: list, keyboard: bool = True):
    """A note or a confirm, on the Wall in the menu's panel (round 15): the title, the lines, and each button as a
    menu row [(label, fn)]; the last is the default (Return), Escape closes. CRISSA_CRISSA_PANELS_ON_WALL=0: its own
    small window, as before."""
    if not panels_on():
        return _dialog_window(root, title, lines, buttons)
    body, close, show, rec = _host(root, title, minw=340, keyboard=keyboard)
    for ln in lines:
        _p_text(body, ln)
    if buttons:
        _p_sep(body)
    last = None
    for label, fn in reversed(buttons):          # the confirm first, Cancel last, as the browser menu reads
        act = (lambda f=fn: (close(), f() if f else None))
        _p_row(body, label, act)
        last = last or act
    if last:
        rec["keys"]["return"] = last
    show()
    return rec["frame"]


def popup(root, title: str, lines: list):
    """A short note opened by his click elsewhere on the Wall (round 17: the Secure note on a camera that cannot turn):
    the Crissa pop-up, above the key and with the keyboard while it is up (round 16's front path), Close to end it."""
    return _dialog(root, title, [str(x) for x in lines], [("Close", None)])


# A PASSWORD HE IS ASKED FOR ON THE WALL (round 17, 2026-10-13; .101: "one early popup had to be found behind the
# terminal"): the desk Wall's Hold Secure asked with zenity, a window of its own that the window manager may stack under
# the window that has the keyboard. In a pack it is a Crissa pop-up now, above the key with the keyboard (round 16's
# front path): the masked box, the default key (Return) and Cancel (Escape). The house keeps zenity. Kill:
# CRISSA_CRISSA_HOLD_POPUP=0 (zenity, as in round 16).
def password_popup_on(env=None) -> bool:
    e = os.environ if env is None else env
    return (panels_on(e) and str(e.get("CRISSA_CRISSA_HOLD_POPUP", "1")).strip().lower()
            not in ("0", "off", "false", "no"))


# THE PASSWORD IS ASKED WHERE HE PRESSED, LARGE (the User's walk of 1.0.1, 2026-10-26: "the sudo password is really hard
# to see esp on mine as it takes it in the menu (2nd menu) left bottom and Secure is top right - not in menu"): a
# caller that knows the press passes `at` (the Wall's own x, y of it) and the pop-up opens under it, kept on the work
# area; its words and box are the menu's type x PASSWORD_SCALE. Kill CRISSA_POPUP_AT_PRESS=0 (above the key, menu type).
PASSWORD_SCALE = 1.4                    # the pop-up's own type: the menu's, larger, for one box he must find at once
LOGIN_PASSWORD_WORDS = "This computer's login password (the one sudo asks for)"


def popup_at_press_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_POPUP_AT_PRESS", "1")).strip().lower() not in ("0", "off", "false", "no")


def _scaled(font, k: float):
    return (font[0], max(int(font[1]), int(round(font[1] * k)))) + tuple(font[2:])


def password_popup(root, title: str, lines: list, on_secret, ok_label: str = "Hold", at=None):
    """The password pop-up; on_secret(secret) runs once on the Tk thread when he takes the default. None when
    switched off (the caller asks its own way). The box keeps no copy of what was typed. `at`: where he pressed."""
    if not password_popup_on():
        return None
    import tkinter as tk
    big = at is not None and popup_at_press_on()
    font = _scaled(MENU_FONT, PASSWORD_SCALE) if big else MENU_FONT
    body, close, show, rec = _host(root, title, minw=380, at=at)
    for ln in lines:
        if big:
            tk.Label(body, text=str(ln), fg=INK, bg=GROUND2, font=font, anchor="w", justify="left",
                     padx=MENU_PAD_X, pady=4, wraplength=int(PANEL_WRAP * PASSWORD_SCALE)).pack(fill="x")
        else:
            _p_text(body, str(ln))
    pw = tk.StringVar(master=body)
    fr = tk.Frame(body, bg=GROUND2, padx=MENU_PAD_X, pady=6)
    fr.pack(fill="x")
    tk.Label(fr, text="Password", fg=INK2, bg=GROUND2, font=font).pack(side="left")
    entry = tk.Entry(fr, textvariable=pw, show="\u2022", width=24, fg=INK, bg=GROUND, insertbackground=INK,
                     relief="flat", font=font, highlightthickness=1, highlightbackground=HAIR, highlightcolor=INK3)
    entry.pack(side="left", padx=8)
    keys_note = tk.Label(body, text="", fg=INK2, bg=GROUND2, font=MENU_FONT)       # round 39 (N12)
    keys_note.pack(fill="x")
    entry.bind("<Button-1>", lambda ev: take_keys(entry, ev, keys_note), add="+")

    def ok(*_a):
        secret = pw.get()
        if not secret:
            return "break"
        pw.set("")
        close()
        on_secret(secret)
        return "break"
    _p_sep(body)
    _p_row(body, ok_label, ok)
    _p_row(body, "Cancel", close)
    rec["keys"]["return"] = ok
    show()
    entry.bind("<Return>", ok)
    entry.bind("<KP_Enter>", ok)
    entry.bind("<Escape>", lambda _e: (close(), "break")[1])
    try:
        entry.focus_set()                     # inside the Wall only, which has the keyboard while this is up
    except tk.TclError:
        pass
    return rec["frame"]


def _dialog_window(root, title: str, lines: list, buttons: list):
    """A small dark dialog in its own window: lines of text and buttons [(label, fn)]. Escape closes it."""
    import tkinter as tk
    top = tk.Toplevel(root)
    top.title(title)
    top.configure(bg=GROUND)
    try:
        top.attributes("-type", "dialog")
    except tk.TclError:
        pass
    top.attributes("-topmost", True)
    tk.Label(top, text=title, fg=EMBER, bg=GROUND, font=("DejaVu Sans Mono", 12, "bold")).pack(
        anchor="w", padx=16, pady=(14, 6))
    for ln in lines:
        tk.Label(top, text=ln, fg=INK, bg=GROUND, font=FONT, justify="left", wraplength=620).pack(
            anchor="w", padx=16, pady=2)
    bar = tk.Frame(top, bg=GROUND)
    bar.pack(anchor="e", padx=16, pady=14)

    def close(*_a):
        try:
            top.destroy()
        except tk.TclError:
            pass
    for label, fn in buttons:
        tk.Button(bar, text=label, command=(lambda f=fn: (close(), f() if f else None)), fg=INK, bg=GROUND2,
                  activebackground=EMBER_DEEP, activeforeground=INK, relief="flat", font=FONT, padx=14).pack(
            side="left", padx=4)
    top.bind("<Escape>", close)
    top.update_idletasks()
    sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
    top.geometry("+%d+%d" % ((sw - top.winfo_reqwidth()) // 2, (sh - top.winfo_reqheight()) // 3))
    top.lift()
    try:
        top.focus_force()
    except tk.TclError:
        pass
    return top


def security_lines(sec: dict) -> list:
    """The Security pop-up's lines: each mode and its one line, the current one marked. Pure: tests read it."""
    out = []
    for m in (sec or {}).get("modes") or []:
        out.append("%s%s: %s" % ("> " if m.get("on") else "  ", m.get("label") or m.get("mode"), m.get("about") or ""))
    return out


def security_buttons(sec: dict, act, password=None) -> list:
    """[(label, fn)] for _dialog: the positions as drawn, highest first (the current one marked "now"), then Cancel.
    Pure. ONE TOGGLE (round 17 follow-ups): Double Secure asks for the login password first (`password(mode, then)`,
    the Crissa pop-up's password box) and sends it once with the choice."""
    rows = []
    for m in reversed((sec or {}).get("modes") or []):
        mode = str(m.get("mode") or "")
        label = str(m.get("label") or mode) + ("  (now)" if m.get("on") else "")

        def choose(md=mode, pw_first=bool(m.get("password"))):
            body = {"action": "security", "mode": md, "by": "the desk Wall"}
            if pw_first and password is not None:
                return password(md, lambda secret: act(dict(body, password=secret), fail_title="Security"))
            return act(body, fail_title="Security")
        rows.append((label, choose))
    return [("Cancel", None)] + rows


def look_lines(lk: dict) -> list:
    """The Agent Look pop-up's lines: her looks, the one she wears marked; what is still being built. Pure: tests read it."""
    out = ["%s%s" % ("> " if l.get("on") else "  ", l.get("title") or l.get("key")) for l in (lk or {}).get("looks") or []]
    if not out:
        out.append("No other look is installed yet.")
    if (lk or {}).get("pending"):
        n = int(lk["pending"])
        out.append("%d %s still being built." % (n, "look is" if n == 1 else "looks are"))
    return out


def look_buttons(lk: dict, act, open_url=None) -> list:
    """[(label, fn)] for _dialog: Cancel, the Control Panel's Choose skin, then each look (the one she wears says
    "(now)" and changes nothing). A tap is the player's POST /v1/menu {"action": "look", "key"}. Pure."""
    rows = []
    for l in reversed((lk or {}).get("looks") or []):
        key = str(l.get("key") or "")
        label = str(l.get("title") or key) + ("  (now)" if l.get("on") else "")
        rows.append((label, None if l.get("on") else (lambda k=key: act({"action": "look", "key": k, "by": "the desk Wall"},
                                                                       fail_title="Agent Look"))))
    more = str((lk or {}).get("more") or "")
    extra = [("More in the Control Panel", (lambda: open_url(more)))] if more and open_url is not None else []
    return [("Cancel", None)] + extra + rows


def look_dialog(root, lk: dict, act, open_url=None):
    """THE AGENT LOOK POP-UP (the User 2026-10-31, "quick switch outfit for crissa here in release 1.0"): her looks in the
    Crissa pop-up, one tap wears one (the Control Panel's own pick path, lib/crissa_avatar.wear); the Wall says "Changing
    to ..." while her player restarts. The browser Wall shows the same (display/crissa-wall.html agentLook)."""
    return _dialog(root, str((lk or {}).get("label") or "Agent Look"), look_lines(lk), look_buttons(lk, act, open_url))


def security_dialog(root, sec: dict, act):
    """SECURITY (round 17, the User 2026-10-13): the positions, one line each, in the Crissa pop-up; a choice is saved
    by the player (POST /v1/menu {"action": "security", "mode"}) and her next turn wears it. ONE TOGGLE (round 17
    follow-ups): Secure and Double Secure where the camera turns; the toggle's note under the lines."""
    lines = security_lines(sec)
    if (sec or {}).get("note"):
        lines.append(str(sec["note"]))

    def password(mode, then):
        words = ("%s: holds Secure until you lower it." % LOGIN_PASSWORD_WORDS if popup_at_press_on()
                 else "Account password: holds Secure until you lower it.")
        return password_popup(root, "Double Secure", [words], then)
    return _dialog(root, "Security", lines, security_buttons(sec, act, password))


# THE ASK BEFORE A LOCKED CHANGE (round 17 follow-ups, the User 2026-10-13): while Security is On or above the player
# runs the face-match sequence before a change (lib/crissa_home); this Wall says the ask first, above the key, so he
# knows to look at the camera. What only stops or quiets her never asks (crissa_home.OPEN_TO_ANYONE). Pure: tests read it.
ASK_OPEN = frozenset(("agent_close", "stop", "wall_close", "voice_id", "desk", "face_again"))


def needs_ask(view: dict | None, body: dict | None) -> str:
    """The ask to show before this menu POST, or ''."""
    sec = (view or {}).get("security") or {}
    b = body or {}
    a = str(b.get("action") or "")
    if not sec.get("lock") or a in ASK_OPEN or (a == "mic" and not b.get("on", True)):
        return ""
    if a == "security":
        mode = str(b.get("mode") or "")
        if not any(m.get("mode") == mode and m.get("face") for m in sec.get("modes") or []):
            return ""
    return str(sec.get("ask") or "Look at the camera for a moment.")


def menu_panel_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_CRISSA_MENU_PANEL", "1")).strip().lower() not in ("0", "off", "false", "no")


def menu_rows(items: list) -> list:
    """[(kind, label, verb, enabled, on)] for the panel: kind "sep" | "note" | "item"; `on` = a state that is on (the
    microphone's). Pure: tests read it."""
    rows = []
    for label, verb, enabled in items:
        if verb is None:
            if rows and rows[-1][0] != "sep":
                rows.append(("sep", "", None, False, False))
            continue
        if verb == "na":
            rows.append(("note", label, verb, False, False))
            continue
        if verb.startswith("disp:"):   # 2026-10-28: a Display row - a dial with - / +, or a switch
            key = verb[5:]
            rows.append(("dial" if key in {k for k, _n, _f in DIAL_ROWS} else "toggle", label, verb, bool(enabled), False))
            continue
        if verb in PLACES:       # 2026-10-28: the places stand side by side as one row of keys
            if rows and rows[-1][0] == "places":
                rows[-1][1].append((label, verb, bool(enabled)))
            else:
                rows.append(("places", [(label, verb, bool(enabled))], None, True, False))
            continue
        on = verb == "mic" and str(label).strip().lower().endswith(": on")
        rows.append(("item", label, verb, bool(enabled), on))
    return rows


def menu_panel(root, key, items: list, do):
    """The Crissa menu as the browser page draws it (.cmenu), muted, on the Wall itself above the key (round 15; it was
    a window of its own on round 14), closed by a choice, a click off it or Escape."""
    if not panels_on():
        return _menu_window(root, key, items, do)
    body, close, show, rec = _host(root, None, anchor="key", key=key, minw=260)
    _S["menu"] = rec["frame"]

    def choose(verb):
        rec["close"]()
        do(verb)

    def redo(verb):
        # a Display row acts and the menu stays, redrawn with the new value (the Advanced panel's own manner)
        rec["close"]()
        do(verb)
        soon(lambda: menu_panel(root, key, menu_items(_S.get("view") or {}, _fullscreen(root),
                                                     advanced=callable(_S.get("advanced")), display=_display_prefs()), do))
    for kind, label, verb, enabled, on in menu_rows(items):
        if kind == "sep":
            _p_sep(body)
        elif kind == "note":
            _p_text(body, label, fg=INK3)
        elif kind == "places":
            _p_places(body, [(lb, (lambda v=vb: choose(v)), en) for lb, vb, en in label])
        elif kind == "dial":
            _p_dial(body, label, (lambda v=verb: redo(v + ":-")), (lambda v=verb: redo(v + ":+")), enabled=enabled)
        elif kind == "toggle":
            _p_row(body, label, (lambda v=verb: redo(v)), enabled=enabled, fg=INK2)
        else:
            _p_row(body, label, (lambda v=verb: choose(v)), enabled=enabled, fg=SAGE if on else INK2)
    orig = rec["close"]

    def close_menu(*_a):
        if _S.get("menu") is rec["frame"]:
            _S["menu"] = None
        orig()
    rec["close"] = close_menu
    show()
    return rec["frame"]


def _menu_window(root, key, items: list, do):
    """Round 14's menu, in its own borderless window (CRISSA_CRISSA_PANELS_ON_WALL=0)."""
    import tkinter as tk
    old = _S.get("menu")
    if old is not None:
        try:
            old.destroy()
        except tk.TclError:
            pass
    top = tk.Toplevel(root)
    _S["menu"] = top
    top.overrideredirect(True)
    try:
        top.attributes("-topmost", True)
    except tk.TclError:
        pass
    top.configure(bg=HAIR)                           # the 1 px hairline round the panel
    body = tk.Frame(top, bg=GROUND2, padx=0, pady=6)
    body.pack(padx=1, pady=1, fill="both")

    def close(*_a):
        _S["menu"] = None
        try:
            top.grab_release()
        except tk.TclError:
            pass
        try:
            top.destroy()
        except tk.TclError:
            pass

    def choose(verb):
        close()
        do(verb)

    for kind, label, verb, enabled, on in menu_rows(items):
        if kind == "sep":
            tk.Frame(body, bg=HAIR, height=1).pack(fill="x", pady=6)
            continue
        if kind == "places":
            _p_places(body, [(lb, (lambda v=vb: choose(v)), en) for lb, vb, en in label])
            continue
        fg = (SAGE if on else INK2) if enabled else INK3
        row = tk.Label(body, text=label, fg=fg, bg=GROUND2, font=MENU_FONT, anchor="w", padx=MENU_PAD_X,
                       pady=MENU_PAD_Y, cursor="hand2" if enabled else "arrow")
        row.pack(fill="x")
        if enabled and kind == "item":
            row.bind("<Enter>", lambda _e, r=row, c=fg: r.config(bg=HAIR, fg=SAGE if c == SAGE else INK))
            row.bind("<Leave>", lambda _e, r=row, c=fg: r.config(bg=GROUND2, fg=c))
            row.bind("<Button-1>", lambda _e, v=verb: (choose(v), "break")[1])
    top.update_idletasks()
    w, h = max(260, top.winfo_reqwidth()), top.winfo_reqheight()
    x0, y0 = key.winfo_rootx(), key.winfo_rooty()
    top.geometry("%dx%d+%d+%d" % (w, h, x0, max(0, y0 - h - 6)))

    def outside(ev):
        x, y = ev.x_root, ev.y_root
        tx, ty = top.winfo_rootx(), top.winfo_rooty()
        if not (tx <= x < tx + top.winfo_width() and ty <= y < ty + top.winfo_height()):
            close()
    top.bind("<Escape>", close)
    top.bind("<Button-1>", outside, add="+")
    top.lift()
    try:
        top.grab_set()
        top.focus_force()
    except tk.TclError:
        pass
    return top


def gate_click(ev) -> str | None:
    """A click on the Wall's glass while a pop-up is open: off the pop-up it closes it and goes no further."""
    p = _S.get("panel")
    if not p or not p.get("on_wall"):
        return None
    b = p.get("box")
    x, y = getattr(ev, "x_root", None), getattr(ev, "y_root", None)
    if b and x is not None:
        try:
            ox, oy = p["frame"].master.winfo_rootx(), p["frame"].master.winfo_rooty()
        except Exception:  # noqa: BLE001
            ox = oy = 0
        if b[0] <= x - ox < b[2] and b[1] <= y - oy < b[3]:
            return None
    p["close"]()
    return "break"


def install(root, *, feed: str, open_url, log=print, W: int | None = None, H: int | None = None, on_box=None,
            close=None, canvas=None, keyboard=None, advanced=None, display=None, house=None):
    """Put the key on the Wall's root window. open_url(url) opens a page in the browser; on_box() is called when
    the key's rectangle changes (the Wall re-sets its input shape). `canvas`, the Wall's glass, closes an open pop-up
    when clicked off it (round 15). `keyboard`, (grant, release), lets a pop-up he opened have the keys (round 16)."""
    import tkinter as tk
    W = int(W or root.winfo_screenwidth())
    H = int(H or root.winfo_screenheight())
    key = tk.Label(root, text=KEY_TEXT, fg=KEY_FG, bg=KEY_BG, font=KEY_FONT, padx=13, pady=5, cursor="hand2",
                   highlightthickness=1, highlightbackground=KEY_BD, highlightcolor=KEY_BD)
    _S["key"] = key
    _S["on_box"] = on_box
    _S["log"] = log
    _S["advanced"] = advanced          # the Wall's Advanced panel opener (2026-10-28): Display settings in this menu
    _S["display"] = display            # {"view", "step", "toggle", "paint"}: the Advanced rows as menu rows (2026-10-28)
    _S["house"] = house                # {"view", "place", "mic"}: the house Wall's own view and verbs (HOUSE_WALL_MENU_KEY)

    def fetch_view() -> dict:
        """The menu's state: the house Wall's own (no player menu on the house), else the player's /v1/menu."""
        if house and callable(house.get("view")):
            return dict(house["view"]() or {}, house=True)
        return _get(feed)
    # keyboard = (grant, release): the Wall takes the keyboard while a pop-up he opened is up, and gives it back
    # (lib/house_wall_ascii.py passes them in wallpaper mode; None = the Wall already gets keys by a click)
    _S["kbd_fns"] = keyboard if (keyboard and popup_front()) else None
    _S["kbd_on"] = False

    def place():
        now = time.time()
        if _S["wa"] is None or now - _S["wa_at"] > WORKAREA_S:
            _S["wa"], _S["wa_at"] = workarea(W, H), now
        key.update_idletasks()
        kw, kh = key.winfo_reqwidth(), key.winfo_reqheight()
        x, y = key_place(_S["wa"], kw, kh, W, H)
        newbox = (x, y, x + kw, y + kh)
        _S["shown_box"] = newbox
        if _S.get("dim") and hides():
            pass                                     # gone with the idle chrome: it comes back where it belongs
        elif newbox != _S["box"]:
            key.place(x=x, y=y)
            _S["box"] = newbox
            if on_box:
                on_box()
        key.lift()
        root.after(int(WORKAREA_S * 1000), place)

    def refresh_loop():
        while True:
            try:
                _S["view"], _S["at"] = fetch_view(), time.time()
            except Exception:  # noqa: BLE001  the player not up yet: the menu shows what it last knew
                pass
            st = (_S.get("view") or {}).get("start") or {}
            # the start bar follows her pieces closely while it shows (the menu's own pace otherwise)
            time.sleep(START_FAST_S if (st.get("applies") and not st.get("ready")) else REFRESH_S)

    def refresh_now():
        def _one():
            try:
                _S.update(view=fetch_view(), at=time.time())
            except Exception:  # noqa: BLE001  the menu shows what it last knew
                pass
        threading.Thread(target=_one, daemon=True).start()

    def say(title, text):
        # a note that answers a request arrives later: it shows above the key and never takes the keyboard
        _dialog(root, title, [text], [("Close", None)], keyboard=False)

    def act(body, fail_title="Crissa", ok_note=None):
        ask = needs_ask(_S.get("view"), body)
        if ask:
            say("Crissa", ask)                       # Security On: look at the camera (the player checks his face)
            ok_note = ok_note or "Done."
        post_then(feed, body, say=say, ok_note=ok_note, fail_title=fail_title, refresh=refresh_now)

    def do(verb):
        v = _S.get("view") or {}
        log("crissa key: %s" % verb)
        note_timing("item " + str(verb), time.time())
        h_ = _S.get("house")
        if h_ and verb in PLACES and callable(h_.get("place")):
            h_["place"](verb)                        # the house rail's own path: front her Brave tab for it
            return
        if h_ and verb == "mic" and callable(h_.get("mic")):
            h_["mic"](not bool((v.get("mic") or {}).get("on")))
            refresh_now()
            return
        if h_ and verb == "help":
            _dialog(root, "Help", list((v.get("help") or {}).get("lines") or HOUSE_HELP), [("Close", None)])
            return
        if verb == "mic":
            act({"action": "mic", "on": not bool((v.get("mic") or {}).get("on"))}, fail_title="Microphone")
        elif verb == "desk":
            act({"action": "desk", "by": "the desk Wall"}, fail_title="Back to her desk")
        elif verb == "face_again":
            fa = v.get("face_again") or {}
            if fa.get("ok") is False and fa.get("password") and panels_on():
                face_again_dialog(root, feed, say=say, refresh=refresh_now)
            elif fa.get("ok") is False:
                say("Face photos again", str(fa.get("why") or "Face photos again cannot run now."))
            else:
                post_then(feed, {"action": "face_again", "by": "the desk Wall"}, say=say, ok_note=None,
                          fail_title="Face photos again", refresh=refresh_now)
        elif verb == "connect":
            url = (v.get("connect") or {}).get("url") or (feed.rstrip("/") + "/connect")
            threading.Thread(target=open_url, args=(url,), daemon=True).start()
        elif verb == "phone":
            # round 36: the QR card (the phone page itself only under CRISSA_PHONE_POPUP=1, a test: one Crissa per screen)
            ph_ = v.get("phone") or {}
            try:
                import crissa_phone_room
                pop = crissa_phone_room.popup_on()
            except Exception:  # noqa: BLE001
                pop = False
            url = ph_.get("url") if pop else (ph_.get("card") or "")
            if url:
                log("menu: your phone -> %s" % ("the phone page (CRISSA_PHONE_POPUP=1)" if pop else "the QR card"))
                threading.Thread(target=open_url, args=(url,), daemon=True).start()
            else:
                say("Your phone", str(ph_.get("how") or ph_.get("label") or "No phone module on this computer."))
        elif verb == "face":
            act({"action": "face"}, fail_title="Face only")
        elif verb in ("panel", "composer", "krew", "skin"):
            # krew's tiles, or its "not installed" placeholder, are the Control Panel's add-on slot (rail_key does the same)
            url = (v.get(verb) or {}).get("url") or ((v.get("panel") or {}).get("url") if verb == "krew" else "") or ""
            if url:
                threading.Thread(target=open_url, args=(url,), daemon=True).start()
        elif verb == "agent_only":
            # AGENT ONLY (the User 2026-10-28: "closes ascii and leaves render window for agent only view"): the desk
            # Wall closes on purpose and her face window stays on top (the player's wall_close: lib/crissa_face
            # wall_off). Open the Wall brings the glass back (the Control Panel, the browser Wall's menu).
            ao_ = v.get("agent_only") or {}
            _dialog(root, "Agent only", [str(ao_.get("ask") or "Agent only: this Wall closes and her face stays on top "
                                             "of your windows. Open the Wall (Control Panel, or her window's menu) "
                                             "brings the glass back.")],
                    [("Cancel", None), ("Agent only", lambda: act({"action": "wall_close", "by": "the desk Wall (Agent only)"},
                                                                  fail_title="Agent only"))])
        elif verb == "advanced":
            fn_ = _S.get("advanced")
            if callable(fn_):
                fn_()
        elif verb.startswith("disp:"):
            # a Display row: disp:<key>:- / disp:<key>:+ steps a dial, disp:<key> flips a switch (the Wall's own
            # step_dial / toggle_display: the panel's click path, so the glass and Brave's dials follow as before)
            d_ = _S.get("display") or {}
            parts = verb.split(":")
            key_, sign_ = parts[1], (parts[2] if len(parts) > 2 else "")
            try:
                if sign_ in ("-", "+") and callable(d_.get("step")):
                    d_["step"](key_, -1 if sign_ == "-" else 1)
                elif callable(d_.get("toggle")):
                    d_["toggle"](key_)
                if callable(d_.get("paint")):
                    d_["paint"]()
            except Exception as e:  # noqa: BLE001  a dial that fails is said, never a dead menu
                say("Display", "%s: %s" % (type(e).__name__, str(e)[:120]))
        elif verb == "voice_id" and isinstance((v.get("voice_id") or {}).get("name_only"), dict):
            # THE NAME ONLY (round 23, the User 2026-10-15: "just ask the name and you will determine conflict"): one
            # box; Crissa decides whether it is his own Voice ID again or a new person (lib/crissa_menu resolve_name)
            vi_ = v.get("voice_id") or {}
            if ids_proof_first_on():
                # round 39 (N10): his proof on this screen, before the name box
                proof_then(root, feed, say=say, then=lambda tok="": name_only_popup(root, feed, vi_, say=say,
                                                                                     refresh=refresh_now, proof=tok))
            else:
                name_only_popup(root, feed, vi_, say=say, refresh=refresh_now)
        elif verb == "voice_id" and isinstance((v.get("voice_id") or {}).get("who"), dict):
            # ONE BUTTON (round 21): "Is this you, or someone new?" - It's me runs his own Voice ID (below, as before);
            # Someone new asks the name (and the password while his face is not seen) and adds a visitor
            vi = v.get("voice_id") or {}
            who = vi["who"]

            def me():
                m = who.get("me") or {}
                if m.get("ok") is False and not m.get("password") and m.get("why"):
                    say("Voice ID", str(m["why"]))
                elif str(vi.get("routine") or "verbal") == "verbal" and VERBAL_KEY:
                    voice_id_verbal_start(root, feed, vi, say=say, refresh=refresh_now)
                else:
                    open_voice_id(feed, open_url, say)

            def new():
                n = who.get("new") or {}
                if not n.get("ok") and n.get("why"):
                    say("Add a person", str(n["why"]))
                    return

                def go(name, secret=""):
                    body = {"action": "voice_id", "who": "new", "name": name, "by": "the desk Wall"}
                    if secret:
                        body["password"] = secret
                    elif not n.get("proved"):
                        say("Add a person", "Look at the camera for a moment.")   # the player checks his face now
                    post_then(feed, body, say=say, ok_note="Adding %s: they listen, and count to six at each beep." % name,
                              fail_title="Add a person", refresh=refresh_now)
                name_popup(root, "Add a person", PERSON_ASK + ([PERSON_PW] if n.get("password") else []), go,
                           password=bool(n.get("password")))
            _dialog(root, "Voice ID", [str(who.get("ask") or "Is this you, or someone new?")], who_buttons(vi, me, new))
        elif verb == "voice_id":
            vi = v.get("voice_id") or {}
            if vi and vi.get("ok") is False and not vi.get("password") and vi.get("why"):
                say("Voice ID again", str(vi["why"]))   # nothing typed could change it (Setup running, presence down)
            elif str(vi.get("routine") or "verbal") == "verbal" and VERBAL_KEY:
                # SPOKEN, FROM HERE (2026-10-12): no page, no window to bring forward. ONE PLATE (.101: three, one closed
                # by hand): it starts at once, and the coach's small plate on the glass is all there is; only when the
                # player asks for proof does the password box come first. Nothing pops up after it starts
                voice_id_verbal_start(root, feed, vi, say=say, refresh=refresh_now)
            else:
                # the name, the password when asked and Begin: one page, in her own window (2026-10-11)
                open_voice_id(feed, open_url, say)
        elif verb == "add_visitor":
            vs = v.get("visitors") or {}
            if vs.get("ok") is False and vs.get("why"):
                say("Add a visitor", str(vs["why"]))
            else:
                name_popup(root, "Add a visitor", VISITOR_ASK,
                           lambda name: act({"action": "add_visitor", "name": name, "by": "the desk Wall"},
                                            fail_title="Add a visitor",
                                            ok_note="Adding %s: they listen, and count to six at each beep." % name))
        elif verb == "guests":
            # EDIT GUEST LIST (round 41, .101 round 40 F2): his proof on this screen (his face, else the password box),
            # then every kept person with Redo and Remove; the proof's token rides with each key
            gv = v.get("guests") or {}
            if gv.get("ok") is False and gv.get("why"):
                say("Edit guest list", str(gv["why"]))
            else:
                def guests_panel(tok=""):
                    def one(r):
                        who = str(r.get("display") or r.get("person_id"))

                        def redo():
                            act({"action": "voice_id", "who": "name", "name": who, "again": True, "proof": tok,
                                 "by": "the desk Wall"}, fail_title="Edit guest list",
                                ok_note="%s's page is open in Crissa's window: press Ready there." % who)

                        def remove():
                            _dialog(root, "Remove %s" % who,
                                    ["Remove %s? Their voice prints and photos are deleted from this computer." % who],
                                    [("Cancel", None),
                                     ("Remove", lambda: act({"action": "visitor_remove", "person_id": r.get("person_id"),
                                                             "proof": tok, "by": "the desk Wall"},
                                                            fail_title="Edit guest list", ok_note="%s is forgotten." % who))])

                        def access(level):
                            act({"action": "guest_access", "person_id": r.get("person_id"), "access": level,
                                 "proof": tok, "by": "the desk Wall"}, fail_title="Edit guest list",
                                ok_note="%s: %s." % (who, ((gv.get("access") or {}).get(level) or {}).get("label") or level))
                        _dialog(root, who, guest_lines({"list": [r], "access": gv.get("access")}),
                                guest_buttons(gv, r, redo=redo, remove=remove, access=access))
                    rows = list(gv.get("list") or [])
                    # _dialog draws its buttons last-first and the last is Return's: Close is the default
                    _dialog(root, "Edit guest list", guest_lines(gv),
                            [("%s…" % (r.get("display") or r.get("person_id")), (lambda r=r: one(r)))
                             for r in reversed(rows)] + [("Close", None)])
                proof_then(root, feed, say=say, then=guests_panel)
        elif verb == "visitors":
            rows = list((v.get("visitors") or {}).get("list") or [])

            def forget(r):
                who = str(r.get("display") or r.get("person_id"))
                _dialog(root, "Remove a visitor",
                        ["Remove %s? Their voice prints and photos are deleted from this computer." % who],
                        [("Cancel", None),
                         ("Remove", lambda: act({"action": "visitor_remove", "person_id": r.get("person_id"),
                                                 "by": "the desk Wall"}, fail_title="Remove a visitor",
                                                ok_note="%s is forgotten." % who))])
            # _dialog draws its buttons last-first and the last is Return's: Close is the default, never a removal
            _dialog(root, "Visitors", visitor_lines(rows),
                    [("Remove %s…" % (r.get("display") or r.get("person_id")), (lambda r=r: forget(r)))
                     for r in reversed(rows)] + [("Close", None)])
        elif verb == "lip_sync":
            lip_sync_dialog(root, feed, v.get("lip_sync") or {})
        elif verb == "security":
            security_dialog(root, v.get("security") or {}, act)
        elif verb == "look":
            look_dialog(root, v.get("look") or {}, act,
                        lambda u: threading.Thread(target=open_url, args=(u,), daemon=True).start())
        elif verb == "help":
            lines = list((v.get("help") or {}).get("lines") or []) or [
                "Full screen: F11 leaves it; Ctrl+Q closes this Wall window when it has the keyboard.",
                "Close Agent (in this menu): she stops listening and speaking; the Wall stays.",
                "All OS Off (in this menu) stops everything."]
            _dialog(root, "Help", lines, [("Close", None)])
        elif verb == "wall_close":
            _dialog(root, "Close the Wall", ["Close the Wall? Crissa keeps running; the Wall stays closed until you open "
                                             "it again (Open the Wall in the Control Panel or the browser Wall's menu)."],
                    [("Cancel", None), ("Close the Wall", lambda: (close or root.destroy)())])
        elif verb == "agent_close":
            ag = v.get("agent") or {}
            _dialog(root, "Close Agent", [str(ag.get("ask") or "Close Agent? Crissa stops listening and speaking; the "
                                              "Wall stays as your desktop.")],
                    [("Cancel", None), ("Close Agent", lambda: act({"action": "agent_close", "by": "the desk Wall"},
                                                                   fail_title="Close Agent"))])
        elif verb == "agent_open":
            act({"action": "agent_open", "by": "the desk Wall"}, fail_title="Open Agent")
        elif verb == "fullscreen":
            try:
                root.attributes("-fullscreen", False)
                _note_screen(root)
            except tk.TclError:
                pass
        elif verb == "stop":
            st = v.get("stop") or {}
            ask = str(st.get("ask") or "All OS Off? Everything of Crissa stops.")
            _dialog(root, "All OS Off", [ask], [("Cancel", None),
                                                ("All OS Off", lambda: act({"action": "stop", "confirm": True},
                                                                           fail_title="All OS Off"))])

    _S["do"] = do                                    # open_item: another key of this Wall opens a menu item

    def open_menu(_ev=None):
        t0 = time.time()
        if _S.get("menu") is not None and panel_open():
            close_panel()                            # the key again: the menu closes (round 15)
            note_timing("close", t0)
            return "break"
        try:
            fs = bool(root.attributes("-fullscreen"))
        except tk.TclError:
            fs = False
        if menu_panel_on():
            try:
                refresh_now()
                menu_panel(root, key, menu_items(_S.get("view") or {}, fs, advanced=callable(_S.get("advanced")), display=_display_prefs()), do)
                key.update_idletasks()
                note_timing("menu", t0)
                return "break"
            except tk.TclError:
                pass                                 # the plain menu below
        m = tk.Menu(root, tearoff=0, bg=GROUND2, fg=INK, activebackground=EMBER_DEEP, activeforeground=INK,
                    disabledforeground=INK2, font=FONT, bd=1, relief="flat")
        for label, verb, on in menu_items(_S.get("view") or {}, fs, advanced=callable(_S.get("advanced")), display=_display_prefs()):
            if verb is None:
                m.add_separator()
            else:
                m.add_command(label=label, command=(lambda vb=verb: do(vb)), state="normal" if on else "disabled")
        refresh_now()
        x0, y0 = key.winfo_rootx(), key.winfo_rooty()
        m.update_idletasks()
        try:
            m.tk_popup(x0, max(0, y0 - m.winfo_reqheight() - 4))
        finally:
            m.grab_release()
        return "break"

    key.bind("<Button-1>", open_menu)
    # the pop-ups' keys and the click off one (round 15): Escape, Return, - and + go to the open pop-up first
    for seq, nm in (("<Escape>", "escape"), ("<Return>", "return"), ("<KP_Enter>", "return"), ("<minus>", "minus"),
                    ("<KP_Subtract>", "minus"), ("<plus>", "plus"), ("<KP_Add>", "plus")):
        root.bind(seq, lambda _e, n=nm: "break" if panel_key(n) else None, add="+")
    if canvas is not None:
        try:
            canvas.bindtags(("CrissaPanelGate",) + tuple(t for t in canvas.bindtags() if t != "CrissaPanelGate"))
            root.bind_class("CrissaPanelGate", "<Button-1>", gate_click)
        except tk.TclError:
            pass
    key.bind("<Enter>", lambda _e: key.config(fg=KEY_FG_HOVER, bg=KEY_BG_HOVER))
    key.bind("<Leave>", lambda _e: key.config(fg=KEY_FG, bg=KEY_BG))
    threading.Thread(target=refresh_loop, daemon=True, name="crissa-key").start()
    root.after(200, place)

    plate = tk.Label(root, text=PLATE_TEXT, fg=INK2, bg=GROUND2, font=KEY_FONT, padx=14, pady=6, cursor="hand2",
                     highlightthickness=1, highlightbackground=HAIR, highlightcolor=HAIR)
    _S["plate"] = plate
    pmem: dict = {}
    _S["plate_kind"] = ""

    def plate_click(_e):
        if _S.get("plate_kind") == "closed":
            log("crissa key: agent_open (the plate)")
            act({"action": "agent_open", "by": "the desk Wall"}, fail_title="Open Agent")
    plate.bind("<Button-1>", plate_click)
    plate.bind("<Enter>", lambda _e: plate.config(fg=INK) if _S.get("plate_kind") == "closed" else None)
    plate.bind("<Leave>", lambda _e: plate.config(fg=INK2) if _S.get("plate_kind") == "closed" else None)

    def plate_tick():
        ps = plate_state(_S.get("view"), time.time(), pmem)
        shown = _S.get("plate_box") is not None
        try:
            if ps["kind"]:
                if ps["kind"] != _S.get("plate_kind") or plate.cget("text") != ps["text"]:
                    plate.config(text=ps["text"], fg=SAGE if ps["kind"] == "ready" else INK2,
                                 cursor="hand2" if ps["click"] else "arrow",
                                 highlightbackground=SAGE if ps["kind"] == "ready" else HAIR)
                    log("crissa key: the plate says %r" % ps["text"])
                _S["plate_kind"] = ps["kind"]
                plate.update_idletasks()
                pw, ph = plate.winfo_reqwidth(), plate.winfo_reqheight()
                x, y = plate_place(_S["wa"] or (0, 0, W, H), pw, ph)
                newbox = (x, y, x + pw, y + ph)
                if newbox != _S.get("plate_box"):
                    plate.place(x=x, y=y)
                    _S["plate_box"] = newbox
                    if on_box:
                        on_box()
                plate.lift()
            elif shown:
                plate.place_forget()
                _S["plate_box"] = None
                _S["plate_kind"] = ""
                log("crissa key: the plate goes")
                if on_box:
                    on_box()
        except Exception:  # noqa: BLE001  a Wall being torn down
            pass
        root.after(PLATE_TICK_MS, plate_tick)
    root.after(PLATE_TICK_MS, plate_tick)
    root.after(DRAIN_MS, drain, root)            # the follow-ups the workers queue (soon) run here, on the Tk thread
    if timing_on():
        root.after(TICK_MS, tick, root)          # the Tk loop's late beats, for the press timing (above)
    log("crissa key: up at the work area's bottom left (CRISSA_CRISSA_KEY=0 hides it)")
    return key


def open_item(verb: str) -> bool:
    """Open one item of the Crissa menu from another key of this Wall (round 17 follow-ups: on a camera that does not
    turn the Secure dot's place opens the Security toggle). False when the key is not up."""
    fn = _S.get("do")
    if fn is None:
        return False
    soon(lambda: fn(verb))
    return True


def _note_screen(root) -> None:
    """The window's full screen, noted for the player's menu view (lib/crissa_menu.desk_screen, EE 2026-10-10)."""
    try:
        import crissa_menu
        crissa_menu.note_desk_screen(None, bool(root.attributes("-fullscreen")))
    except Exception:  # noqa: BLE001
        pass


def _safe_get(feed: str) -> dict:
    try:
        return _get(feed)
    except Exception:  # noqa: BLE001
        return _S.get("view") or {}
