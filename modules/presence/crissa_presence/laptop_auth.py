"""A LAPTOP'S CAMERA KEEPS HIM SIGNED IN BY A LIGHT FACE CHECK (round 27, the User 2026-10-16 on .101).

"These are laptops. Let's renew auth less frequently, simply in the background, and randomize between 30 s and 1 min.
If no auth, a 'face check requested' spot check. Something less heavy."

Round 26 on .101: the camera does not turn (a crop pan, fixed_cam.digital_fixed), yet the door watch ran on his seat
twice a second, decided he had left ("door_empty_after"), dropped his AUTH, and every face match after that was refused
"guest_until_secure" - Guest with his face in view - while presence and the camera took 125-205 % CPU.

On a camera that does not turn, in a pack (`on`):
  - the door watch does not run (no door on a seat camera); the presence loop runs `tick` instead;
  - every 30-60 s, at random (`next_gap`), one face check: one picture from the camera, his face matched in it; seen
    at the live-face bar, his AUTH is renewed (the same grant the Secure click's desk look makes; the one view IS his
    seat) and the restart's guest latch is let go, since no Secure click can ever come on this camera;
  - not seen: AUTH is not dropped here (the usual expiry still applies); two checks in a row without his face take
    him off the roster (headcount), one with it puts him back;
  - a spot check on demand (`check(why="face_check")`): the face-match sequence's look before a tool or a setting
    (lib/crissa_home.face_sequence calls presence's /v1/visit_lamp {op: "face_check"}).

Never on the house (a motor camera, is_house). Kill: CRISSA_LAPTOP_AUTH=0 (round 26: the door watch on every camera).
Interval: CRISSA_LAPTOP_AUTH_MIN_S (30), CRISSA_LAPTOP_AUTH_MAX_S (60).

A SESSION WHEN SECURITY IS ON (round 28, the User 2026-10-17 on .101: "I put security off so it was usable ... It really
needs to be usable if on. Maybe auth a session and let it time out if no face, movement or activity for 30 s."). On a
laptop Security defaults to Off (crissa_home.security_default); turned On, one face match signs in a SESSION that holds
while any of these is seen: his face (a check, or the oracle's look), movement in front of the camera (two small grey
pictures, only while a session is live), speech the ear heard, or keyboard/mouse use (the OS's own idle time). With
none of them for SESSION_S (30 s) the session ends (AUTH dropped, "laptop_session_idle"); the two-miss leave does not
apply under it. No door watch, no guest_until_secure; the next face match starts a new session, and any activity after
an ended session asks for that match at once. Kill: CRISSA_LAPTOP_SESSION=0 (round 27: AUTH renewed by the background
check and dropped after two misses). CRISSA_LAPTOP_SESSION_S (30), CRISSA_LAPTOP_MOTION_LEVEL (6.0, the idle gate's).
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

_OFFS = ("0", "off", "false", "no")
SOURCE = "desk_ff_laptop"          # an FF source (auth_first.FF_GRANT_SOURCES "desk_ff"): a seat look may mint


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def switch_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LAPTOP_AUTH", "1")).strip().lower() not in _OFFS


def on(svc: Any) -> bool:
    """A pack, the switch on, and a camera that does not turn (the service's fixed-camera rule)."""
    if not switch_on():
        return False
    try:
        from . import fixed_cam
        if fixed_cam.is_house():
            return False
        return bool(svc._fixed_cam())
    except Exception:  # noqa: BLE001
        return False


def session_switch_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LAPTOP_SESSION", "1")).strip().lower() not in _OFFS


def session_s() -> float:
    return max(5.0, _f("CRISSA_LAPTOP_SESSION_S", 30.0))


def security_on() -> bool:
    """Security On for this install (fixed_cam.security_mode, which reads lib/crissa_home's toggle)."""
    try:
        from . import fixed_cam
        return fixed_cam.security_mode() == "on"
    except Exception:  # noqa: BLE001  unknown: no session rule (round 27's behaviour)
        return False


_IDLE_CMD: list | None = None


def os_idle_s() -> float | None:
    """Seconds since the last keyboard or mouse input on this desktop, None when it cannot be read. GNOME (Mutter's idle
    monitor), any X11 desktop (xprintidle), macOS (IOHIDSystem's HIDIdleTime)."""
    global _IDLE_CMD
    if _IDLE_CMD is None:
        if sys.platform == "darwin" and shutil.which("ioreg"):
            _IDLE_CMD = ["ioreg", "-c", "IOHIDSystem", "-d", "4"]
        elif shutil.which("gdbus") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            _IDLE_CMD = ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor", "--object-path",
                         "/org/gnome/Mutter/IdleMonitor/Core", "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"]
        elif shutil.which("xprintidle") and os.environ.get("DISPLAY"):
            _IDLE_CMD = ["xprintidle"]
        else:
            _IDLE_CMD = []
    if not _IDLE_CMD:
        return None
    try:
        out = subprocess.run(_IDLE_CMD, capture_output=True, text=True, timeout=1.5).stdout
    except Exception:  # noqa: BLE001
        return None
    return parse_idle(_IDLE_CMD[0], out)


def parse_idle(cmd: str, out: str) -> float | None:
    import re
    try:
        if cmd == "ioreg":
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out or "")
            return int(m.group(1)) / 1e9 if m else None
        if cmd == "gdbus":
            m = re.search(r"uint64\s+(\d+)", out or "")
            return int(m.group(1)) / 1000.0 if m else None
        return int(str(out or "").strip()) / 1000.0
    except (ValueError, AttributeError):
        return None


def _small_grey(jpeg: bytes):
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_8)
        return None if img is None else cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA).astype("float32")
    except Exception:  # noqa: BLE001
        return None


def next_gap(rng: random.Random | None = None) -> float:
    lo = max(5.0, _f("CRISSA_LAPTOP_AUTH_MIN_S", 30.0))
    hi = max(lo, _f("CRISSA_LAPTOP_AUTH_MAX_S", 60.0))
    return (rng or random).uniform(lo, hi)


class LaptopAuth:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.next_at = 0.0                # the first check right away
        self.last: dict = {}
        self.checks = 0
        self.seen = 0
        self.misses = 0                   # checks in a row that saw no face of his
        self.activity_at = 0.0            # the session's last sign of him (face, movement, speech, keyboard/mouse)
        self.activity_why = ""
        self.session_ended_at = 0.0
        self._prev_grey = None

    def due(self, now: float | None = None) -> bool:
        return (time.time() if now is None else now) >= self.next_at

    def check(self, svc: Any, *, why: str = "background", snap=None) -> dict:
        """One face check: {"ok", "checked", "found", "sim", "n_faces", "why", "at"}. `snap()` returns one JPEG."""
        t0 = time.time()
        with self.lock:
            self.next_at = t0 + next_gap()
            self.checks += 1
            out: dict = {"ok": True, "checked": False, "found": False, "sim": None, "why": why, "at": t0,
                         "via": "laptop_check"}
            uid = svc._user_id()
            try:
                if snap is None:
                    from . import room_sweep as _rs
                    snap = _rs._snapshot
                jpeg = snap()
            except Exception as e:  # noqa: BLE001  the camera did not answer: nothing measured, nothing dropped
                out.update(ok=False, error=type(e).__name__)
                self.last = out
                return out
            out["checked"] = True
            sim = float(svc._match_user_in_jpegs(uid, [jpeg], source=SOURCE) or 0.0)
            thr = _f("RAIL_PRESENCE_MATCH_SIM", 0.60)
            out["sim"] = round(sim, 3)
            if sim >= thr:
                out["found"] = True
                self.seen += 1
                self.misses = 0
                svc._guest_until_secure = False         # no Secure click can come on this camera
                try:
                    svc.grant_user_face_auth(sim=sim, source=SOURCE, greet=False)
                except Exception as e:  # noqa: BLE001
                    out["grant_error"] = type(e).__name__
                try:
                    svc.assert_present([uid], source="laptop_check")
                except Exception:  # noqa: BLE001
                    pass
            else:
                self.misses += 1
                if self.misses == 2 and not self.session_rule():    # under a session, the session decides
                    try:
                        svc.assert_leave([uid], source="laptop_check")
                    except Exception:  # noqa: BLE001
                        pass
            out["wall_sec"] = round(time.time() - t0, 3)
            self.last = out
            try:
                from .service import seen_line
                said = seen_line(getattr(svc, "_last_seen", None), uid, sim, out["found"])
            except Exception:  # noqa: BLE001
                said = "%s sim=%.3f" % ("seen" if out["found"] else "not seen", sim)
            out["seen"] = said
            print("[laptop-auth] %s check: %s next in %.0f s" % (why, said, self.next_at - time.time()), flush=True)
            return out

    # ---- the session (Security On) ----------------------------------------------------------------------------------

    def session_rule(self) -> bool:
        return session_switch_on() and security_on()

    def note(self, why: str, ts: float | None = None) -> None:
        t = time.time() if ts is None else float(ts)
        if t > self.activity_at:
            self.activity_at, self.activity_why = t, why

    def sense(self, svc: Any, *, snap=None, idle_s=os_idle_s, now: float | None = None) -> str:
        """Gather the signs of him since the last tick into activity_at; returns the newest kind ('' none)."""
        t = time.time() if now is None else now
        before = self.activity_at
        try:
            hold = (getattr(svc, "_face_auth_hold", {}) or {}).get(svc._user_id()) or {}
            if hold.get("ts"):
                self.note("face", float(hold["ts"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import fixed_cam
            lk = fixed_cam.look() or {}
            if lk.get("ts") and lk.get("name") == svc._user_id():
                self.note("face", float(lk["ts"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            lv = getattr(svc, "_last_user_voice", None) or {}
            if lv.get("ts"):
                self.note("speech", float(lv["ts"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            idle = idle_s() if idle_s else None
            if idle is not None and idle >= 0:
                self.note("keyboard/mouse", t - float(idle))
        except Exception:  # noqa: BLE001
            pass
        try:
            if snap is None:
                from . import room_sweep as _rs
                snap = _rs._snapshot
            g = _small_grey(snap())
            if g is not None:
                if self._prev_grey is not None:
                    import numpy as np
                    if float(np.mean(np.abs(g - self._prev_grey))) > _f("CRISSA_LAPTOP_MOTION_LEVEL", 6.0):
                        self.note("movement", t)
                self._prev_grey = g
        except Exception:  # noqa: BLE001  no picture: movement is not read this tick
            pass
        return self.activity_why if self.activity_at > before else ""

    def session_tick(self, svc: Any, **kw) -> dict:
        """One session pass (the presence loop's 2 s tick, Security On only): an idle session ends; activity after an
        ended one asks for a face check now."""
        t = time.time() if kw.get("now") is None else kw["now"]
        uid = svc._user_id()
        holding = bool((getattr(svc, "_face_auth_hold", {}) or {}).get(uid))
        if not holding:
            got = self.sense(svc, **kw) if self.session_ended_at else ""
            if got and got != "face" and self.activity_at > self.session_ended_at and self.next_at > t + 5.0:
                self.next_at = t                  # he is back: one face check now starts the next session
            return {"session": False, "why": got}
        self.sense(svc, **kw)
        idle = t - self.activity_at if self.activity_at else 0.0
        if self.activity_at and idle > session_s():
            self.end_session(svc, idle, t)
            return {"session": False, "ended": True, "idle_s": round(idle, 1)}
        return {"session": True, "idle_s": round(idle, 1), "why": self.activity_why}

    def end_session(self, svc: Any, idle: float, now: float | None = None) -> None:
        uid = svc._user_id()
        try:
            (getattr(svc, "_face_auth_hold", {}) or {}).pop(uid, None)
            if hasattr(svc, "_auth_levels"):
                svc._auth_levels.pop(uid, None)
            svc._user_session = None
        except Exception:  # noqa: BLE001
            pass
        self.session_ended_at = time.time() if now is None else now
        self.activity_at, self.activity_why, self._prev_grey = 0.0, "", None
        print("[laptop-auth] session ended: no face, movement or activity for %.0f s" % idle, flush=True)
        try:
            svc._laptop_republish("laptop_session_idle")
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict:
        out = {"on": True, "checks": self.checks, "seen": self.seen, "misses": self.misses,
               "next_in_s": round(max(0.0, self.next_at - time.time()), 1), "last": dict(self.last)}
        if self.session_rule():
            out["session"] = {"timeout_s": session_s(), "last_sign": self.activity_why or None,
                              "idle_s": round(time.time() - self.activity_at, 1) if self.activity_at else None}
        return out
