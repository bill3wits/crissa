"""Insta360 Link 2 firmware AI face-lock via UVC Extension Unit 9.

Ported from documented protocol (camera-control-reference.md + oracle XU notes /
vrwallace Insta360 Link controller). No Crissa imports.

Use BOUNDED only: enable → wait → capture → disable before next pan.
Never free-run during multi-stop headcount.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import time
import traceback
from collections import deque

DEVICE = os.environ.get("RAIL_CAMERA_V4L_DEV", "/dev/video0")
# Ring of firmware mutations — who flipped track on/off (the User: something overrides)
_AUDIT: deque = deque(maxlen=int(os.environ.get("RAIL_CAMERA_FW_AUDIT", "64")))
# SET ON then GET_CUR 255 (11:02:29) is not mode==1, so release_and_hold
# skipped XU OFF and the gimbal kept following until the 11:10:45 bounce.
_TRACK_ARMED: bool = False


def firmware_audit_tail(n: int = 20) -> list[dict]:
    return list(_AUDIT)[-max(1, n) :]


def _audit(kind: str, **extra) -> None:
    try:
        # Prefer crissa_presence frames so HTTP path → grabber fn shows up
        stack = []
        for fr in traceback.extract_stack(limit=28)[:-1]:
            fn = fr.filename or ""
            if "firmware_facelock" in fn:
                continue
            if "threading.py" in fn or "socketserver.py" in fn:
                continue
            stack.append(f"{(fn or '?').rsplit('/', 1)[-1]}:{fr.lineno}:{fr.name}")
            if len(stack) >= 5:
                break
        if not stack:
            stack = ["?"]
        rec = {
            "ts": time.time(),
            "ts_local": time.strftime("%H:%M:%S"),
            "kind": kind,
            **extra,
            "stack": stack,
        }
        _AUDIT.append(rec)
        print(
            f"[fw-audit] {rec['ts_local']} {kind} "
            f"{extra.get('enable', extra.get('note', ''))} via {stack[0]}",
            flush=True,
        )
    except Exception:
        pass
_XU_UNIT = 9
_XU_SEL_MODE = 2
_XU_SEL_FRAME = 19
_XU_SEL_RESET = 14
_UVC_SET_CUR = 0x01
_UVC_GET_CUR = 0x81
_UVC_GET_LEN = 0x85
_UVCIOC_CTRL_QUERY = 0xC0107521


class _XUQ(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
    ]


def _xu(fd: int, sel: int, query: int, buf) -> None:
    q = _XUQ(_XU_UNIT, sel, query, len(buf))
    q.data = ctypes.cast(buf, ctypes.c_void_p)
    fcntl.ioctl(fd, _UVCIOC_CTRL_QUERY, q)


def firmware_status(device: str | None = None) -> dict:
    """Read XU mode + frame (AI track / head-framing) without changing them.

    Used to catch something else overriding firmware settings.
    """
    dev = device or DEVICE
    out: dict = {
        "ok": False,
        "device": dev,
        "mode": None,
        "mode_bytes": None,
        "frame": None,
        "track_on": None,
        "frame_on": None,
        "error": None,
    }
    try:
        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        try:
            ln = (ctypes.c_uint8 * 2)()
            _xu(fd, _XU_SEL_MODE, _UVC_GET_LEN, ln)
            n = max(int(ln[0] | (ln[1] << 8)), 1)
            buf = (ctypes.c_uint8 * n)()
            _xu(fd, _XU_SEL_MODE, _UVC_GET_CUR, buf)
            mode_bytes = [int(buf[i]) for i in range(n)]
            out["mode_bytes"] = mode_bytes
            out["mode"] = mode_bytes[0] if mode_bytes else None
            # Only explicit 0x01 is AI track. 0x00 = off; 0xFF / other = not
            # "track on" (mis-read as on caused release thrash and desk snaps —
            # the User 2026-08-12 stuck-desk diagnosis).
            out["track_on"] = bool(mode_bytes and int(mode_bytes[0]) == 1)
            try:
                fbuf = (ctypes.c_uint8 * 1)()
                _xu(fd, _XU_SEL_FRAME, _UVC_GET_CUR, fbuf)
                out["frame"] = int(fbuf[0])
                # GET_CUR stays 1 after SET 0 (2026-08-26: GET_DEF=0, GET_CUR=1
                # — latch, not factory default). Advisory only. Live lock is
                # track_on. Rest/return_door must use pan/tilt + track_on.
                out["frame_on"] = bool(fbuf[0])
            except Exception as e:
                out["frame_error"] = str(e)[:80]
            out["ok"] = True
        finally:
            os.close(fd)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:160]
    return out


def firmware_track(enable: bool, device: str | None = None) -> bool:
    """Enable/disable hardware AI face tracking + head framing.

    CRITICAL (the User 2026-07-24): XU mode block is multi-byte (~60B). Writing a
    zeroed buffer on OFF used to wipe *other* firmware fields and snap gimbal
    to factory pan≈0 (desk). Always GET_CUR first and only flip the mode bit.
    Frame (head framing) is a separate selector. SET 0 is still attempted on
    release; GET_CUR does not follow (do not treat that as a failed OFF).
    """
    dev = device or DEVICE
    before = firmware_status(device=dev)
    try:
        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        try:
            ln = (ctypes.c_uint8 * 2)()
            _xu(fd, _XU_SEL_MODE, _UVC_GET_LEN, ln)
            n = max(int(ln[0] | (ln[1] << 8)), 2)
            buf = (ctypes.c_uint8 * n)()
            # Preserve existing firmware settings — only toggle track flag
            try:
                _xu(fd, _XU_SEL_MODE, _UVC_GET_CUR, buf)
            except Exception:
                pass
            if enable:
                buf[0] = 0x01
                if n > 1:
                    buf[1] = 0x00
            else:
                buf[0] = 0x00
                # keep rest of buffer intact (was wiping → factory home)
            _xu(fd, _XU_SEL_MODE, _UVC_SET_CUR, buf)
            if enable:
                _xu(fd, _XU_SEL_FRAME, _UVC_SET_CUR, (ctypes.c_uint8 * 1)(0x01))
            else:
                # Explicit frame OFF — measured stuck frame=1 after "release"
                for _ in range(2):
                    try:
                        _xu(fd, _XU_SEL_FRAME, _UVC_SET_CUR, (ctypes.c_uint8 * 1)(0x00))
                    except Exception:
                        pass
        finally:
            os.close(fd)
        after = firmware_status(device=dev)
        global _TRACK_ARMED
        if enable:
            _TRACK_ARMED = True
        elif not after.get("track_on"):
            _TRACK_ARMED = False
        _audit(
            "track",
            enable=bool(enable),
            before_mode=before.get("mode"),
            after_mode=after.get("mode"),
            before_frame=before.get("frame"),
            after_frame=after.get("frame"),
            preserved_buf=True,
            armed=_TRACK_ARMED,
        )
        # Selector 19 GET staying 1 is expected. Failed OFF is track still on.
        if (not enable) and after.get("track_on"):
            _audit(
                "track_stuck_on",
                note="mode still 1 after OFF",
                after_mode=after.get("mode"),
            )
        return True
    except Exception as e:
        _audit("track_fail", enable=bool(enable), error=str(e)[:120])
        return False


def firmware_gimbal_reset(device: str | None = None) -> bool:
    dev = device or DEVICE
    try:
        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        try:
            _xu(fd, _XU_SEL_RESET, _UVC_SET_CUR, (ctypes.c_uint8 * 1)(0x01))
        finally:
            os.close(fd)
        _audit("gimbal_reset", note="XU sel14 — often returns factory center")
        return True
    except Exception:
        return False


def facelock_live(st: dict | None) -> bool:
    """True when firmware AI track is armed.

    Mode byte0 == 1 is the GET. SET ON then GET 255 still steers the gimbal
    (measured 11:02:29–11:10:45) — software-armed counts as live so OFF runs.
    Selector 19 GET is not the live lock.
    """
    if _TRACK_ARMED:
        return True
    return bool(st) and bool(st.get("track_on"))


def frame_off(device: str | None = None) -> bool:
    """Send head-framing OFF (selector 19, SET 0), whatever track mode reads (the User 2026-10-06).

    Head-framing was left ON for hours after a FaceFinder release: release_and_hold skipped every OFF once track mode
    read 0, and the frame selector's GET reads 1 whether or not a SET 0 took - so nothing here can verify it; the door
    PICTURE does (door_picture). Measured 10-06 at the door: SET 0 moves nothing. Kill RAIL_FRAME_OFF_ALWAYS=0.
    """
    if os.environ.get("RAIL_FRAME_OFF_ALWAYS", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    dev = device or DEVICE
    ok = False
    try:
        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        try:
            for _ in range(2):
                try:
                    _xu(fd, _XU_SEL_FRAME, _UVC_SET_CUR, (ctypes.c_uint8 * 1)(0x00))
                    ok = True
                except Exception:
                    pass
        finally:
            os.close(fd)
    except Exception:
        ok = False
    _audit("frame_off", ok=ok)
    return ok


def release_and_hold(device: str | None = None) -> dict:
    """Release facefinder when AI track is live. No-op if already off.

    Second OFF only if track_on is still true after the first. Do not retry on
    sticky selector-19 GET.
    """
    st = firmware_status(device)
    if not facelock_live(st):
        return {
            "ok": True,
            "facelock": False,
            "double_release": False,
            "skipped": "track_off",
            "frame_off": frame_off(device),
        }
    ok1 = firmware_track(False, device)
    st2 = firmware_status(device)
    # Head-framing OFF after a LIVE release too (the User 2026-10-09): 840f496 sent it only when track was
    # already off, so a look's real release (track on) left frame=1 and the picture stayed on his seat
    # while the readback said door (04:38-07:4x). frame_off honours RAIL_FRAME_OFF_ALWAYS=0.
    if facelock_live(st2):
        ok2 = firmware_track(False, device)
        return {"ok": ok1 or ok2, "facelock": False, "double_release": True, "frame_off": frame_off(device)}
    return {"ok": bool(ok1), "facelock": False, "double_release": False, "frame_off": frame_off(device)}

