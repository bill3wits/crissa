"""Beam microphone (XVF3800 array) direction-of-arrival — read-only, utterance-close. Optional: with no
such array plugged in every read fails open and the ear runs without a bearing.

Seeed's published control is a USB *vendor* IN (resid=20, cmdid=0x80|18),
not a HID feature report. Measured 2026-08-13 on this host:

  /dev/hidraw1  Seeed Studio reSpeaker XVF3800  (2886:001a, world-rw)
  HIDIOCGFEATURE reports are empty (report ids 0/1/18/20 → zeros)
  /dev/bus/usb/003/003 vendor IN → status + uint16 doa + uint16 VAD

hidraw1 is how we *identify* the puck. Degrees come from usbfs control
on the same USB device (same protocol as Seeed's pyusb SDK; no pyusb).

Never claims an ALSA interface. Never writes LEDs or AEC. Fail-open.

Physical orientation (the User 2026-08-14): the puck's native 12 o'clock is its
**USB cord**. Cord toward the screen ⇒ mic 0° = 12 at the TOP of the icon.
User sits at 6, looks at 12, door label is 10. Do not remap 12 to 10.
If the cord cannot point at the screen, record
stations.mic_placement.cord_points_to_hour + mic_zero_offset_hours in
the identity site.json rather than re-seating the puck. Camera sitters stay
site_stations/v2 (desk 8, a second seat 10, door 12).
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any

VID = 0x2886
PID = 0x001A
DOA_RESID = 20
DOA_CMDID = 18  # read bit OR'd at transfer time
# USBDEVFS_CONTROL = _IOWR('U', 0, struct usbdevfs_ctrltransfer) — 24 bytes on x86_64
USBDEVFS_CONTROL = 0xC0185500

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"dev": None, "dev_ts": 0.0}
_LAST_SPEECH: dict[str, Any] = {"az": None, "ts": 0.0}


class _Ctrl(ctypes.Structure):
    _fields_ = [
        ("bRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
        ("timeout", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
    ]


def parse_doa_payload(raw: bytes) -> dict[str, Any]:
    """Decode Seeed DOA_VALUE payload: status + two little-endian uint16."""
    if not raw or len(raw) < 5:
        raise ValueError(f"short DOA payload: {raw!r}")
    status = raw[0]
    az, vad = struct.unpack_from("<HH", raw, 1)
    az = int(az) % 360
    speech = bool(vad)
    return {
        "status": int(status),
        "az_deg": az,
        "speech": speech,
        "vad": int(vad),
    }


def _hidraw_is_xvf(path: Path) -> bool:
    try:
        uevent = (path / "device" / "uevent").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "2886:0000001A" in uevent.upper() or "2886:001A" in uevent.upper()


def find_xvf3800() -> dict[str, Any] | None:
    """Locate the puck. Cached ~5 s. None if unplugged."""
    now = time.time()
    cached = _CACHE.get("dev")
    if cached and (now - float(_CACHE.get("dev_ts") or 0.0)) < 5.0:
        return cached
    hidraw = None
    for p in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        if _hidraw_is_xvf(p):
            hidraw = f"/dev/{p.name}"
            break
    usb_path = None
    bus = dev = None
    for idv in Path("/sys/bus/usb/devices").glob("*/idVendor"):
        try:
            if idv.read_text().strip().lower() not in ("2886", "0x2886"):
                continue
            prod = (idv.parent / "idProduct").read_text().strip().lower()
            if prod not in ("001a", "0x001a"):
                continue
            bus = int((idv.parent / "busnum").read_text().strip())
            dev = int((idv.parent / "devnum").read_text().strip())
            cand = f"/dev/bus/usb/{bus:03d}/{dev:03d}"
            if os.path.exists(cand):
                usb_path = cand
                break
        except (OSError, ValueError):
            continue
    if not usb_path:
        _CACHE["dev"] = None
        _CACHE["dev_ts"] = now
        return None
    out = {
        "usb": usb_path,
        "hidraw": hidraw,
        "bus": bus,
        "dev": dev,
        "vid": VID,
        "pid": PID,
    }
    _CACHE["dev"] = out
    _CACHE["dev_ts"] = now
    return out


def _vendor_in(usb_path: str, resid: int, cmdid: int, length: int, timeout_ms: int = 250) -> bytes:
    buf = ctypes.create_string_buffer(max(length, 8))
    ctrl = _Ctrl()
    ctrl.bRequestType = 0xC0  # IN | VENDOR | DEVICE
    ctrl.bRequest = 0
    ctrl.wValue = 0x80 | (cmdid & 0x7F)
    ctrl.wIndex = resid
    ctrl.wLength = length
    ctrl.timeout = timeout_ms
    ctrl.data = ctypes.cast(buf, ctypes.c_void_p)
    fd = os.open(usb_path, os.O_RDWR)
    try:
        fcntl.ioctl(fd, USBDEVFS_CONTROL, ctrl)
    finally:
        os.close(fd)
    return buf.raw[:length]


def read_doa(*, hold_speech_s: float = 1.0) -> dict[str, Any]:
    """One DOA_VALUE read. Fail-open. Hold last speaking azimuth briefly.

    Returns ``ok`` plus ``az_deg`` / ``conf`` / ``speech`` / ``source``.
    """
    dev = find_xvf3800()
    if not dev:
        return {
            "ok": False,
            "error": "xvf3800_missing",
            "az_deg": None,
            "conf": None,
            "speech": False,
            "source": "xvf3800",
        }
    try:
        with _LOCK:
            raw = _vendor_in(dev["usb"], DOA_RESID, DOA_CMDID, 5)
        parsed = parse_doa_payload(raw)
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:160],
            "az_deg": None,
            "conf": None,
            "speech": False,
            "source": "xvf3800",
            "usb": dev.get("usb"),
            "hidraw": dev.get("hidraw"),
        }
    az = parsed["az_deg"]
    speech = parsed["speech"]
    now = time.time()
    conf = 1.0 if speech else 0.25
    held = False
    if speech:
        _LAST_SPEECH["az"] = az
        _LAST_SPEECH["ts"] = now
    elif (
        hold_speech_s > 0
        and _LAST_SPEECH.get("az") is not None
        and (now - float(_LAST_SPEECH.get("ts") or 0.0)) <= hold_speech_s
    ):
        # Remember last speaking az for probes only. Never treat it as this
        # utterance's beam — that was the 359° "door" stamp that was a held
        # desk bearing (COORD-CLAUDE-GROK-MIC-RESIDUAL-20260813).
        held = True
    return {
        "ok": True,
        "az_deg": az,
        "conf": conf,
        "speech": speech,
        "held": held,
        "held_az": _LAST_SPEECH.get("az") if held else None,
        "vad": parsed["vad"],
        "status": parsed["status"],
        "source": "xvf3800",
        "frame": "mic",
        "usb": dev.get("usb"),
        "hidraw": dev.get("hidraw"),
    }


def stamp_beam(doa: dict[str, Any] | None) -> tuple[float | None, float | None, bool]:
    """Angle for the STT stamp: live speech only. No held/stale bearing."""
    if not doa or not doa.get("ok"):
        return None, None, False
    if not doa.get("speech") or doa.get("held"):
        return None, None, False
    az = doa.get("az_deg")
    if az is None:
        return None, None, False
    try:
        az_f = float(az) % 360.0
    except (TypeError, ValueError):
        return None, None, False
    try:
        conf = float(doa["conf"]) if doa.get("conf") is not None else 1.0
    except (TypeError, ValueError):
        conf = 1.0
    return az_f, conf, True


def main() -> int:
    """Live print — clap at the door to record 12 o'clock residual."""
    print("xvf3800", find_xvf3800())
    try:
        while True:
            d = read_doa()
            print(
                f"ok={d.get('ok')} az={d.get('az_deg')} "
                f"speech={d.get('speech')} conf={d.get('conf')} "
                f"err={d.get('error')}",
                flush=True,
            )
            time.sleep(0.15)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
