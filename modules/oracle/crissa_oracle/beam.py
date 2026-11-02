"""Is a beam microphone (a microphone array that reports direction) plugged in? (2026-10-01)

Decorative for now: the clock shows 'no beam sig' in tiny type when there is none.
Direction itself is coming soon. A Mac has none. Never raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

# USB ids of arrays that report a direction of arrival: (vendor, product)
KNOWN = {("2886", "001a")}
SYSFS = Path("/sys/bus/usb/devices")


def present(platform: str | None = None, sysfs: Path | None = None) -> bool:
    if not (sys.platform if platform is None else platform).startswith("linux"):
        return False
    try:
        for d in (SYSFS if sysfs is None else sysfs).iterdir():
            try:
                ids = ((d / "idVendor").read_text().strip().lower(), (d / "idProduct").read_text().strip().lower())
            except OSError:
                continue
            if ids in KNOWN:
                return True
    except OSError:
        pass
    return False


def label(platform: str | None = None, sysfs: Path | None = None) -> str:
    return "beam" if present(platform, sysfs) else "no beam sig"
