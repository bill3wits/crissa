"""Crissa announces herself on the Wi-Fi so the phone finds her without an address (the User 2026-10-18, the iPhone
app's M1: "the app finds Crissa on the Wi-Fi by itself"). Bonjour service `_crissa._tcp` on the phone server's port,
named after the computer.

No new dependency: the system's own announcer, held open as a child process for as long as the server runs -
`dns-sd -R` on a Mac (always there), `avahi-publish-service` on Linux (the avahi-utils package). Without either the
server says so once and the app pairs by typing the computer's address. Kill: CRISSA_PHONE_BONJOUR=0.
"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess

SERVICE = "_crissa._tcp"


def enabled() -> bool:
    return os.environ.get("CRISSA_PHONE_BONJOUR", "1").strip().lower() not in ("0", "false", "off", "no")


def command(port: int, name: str = "", which=shutil.which) -> list[str] | None:
    """The announcer's argv for this machine, or None when it has none."""
    label = (name or "Crissa on %s" % socket.gethostname().split(".")[0])[:63]
    if which("dns-sd"):
        return ["dns-sd", "-R", label, SERVICE, "local", str(int(port)), "path=/", "app=crissa"]
    if which("avahi-publish-service"):
        return ["avahi-publish-service", label, SERVICE, str(int(port)), "path=/", "app=crissa"]
    return None


def start(port: int, name: str = "", log=print) -> subprocess.Popen | None:
    """Announce until this process exits. Returns the child (None when off or no announcer)."""
    if not enabled():
        return None
    argv = command(port, name)
    if argv is None:
        log("[phone-app] no Bonjour announcer here (dns-sd / avahi-publish-service): the phone pairs by this computer's address")
        return None
    try:
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        log("[phone-app] Bonjour announcer failed to start: %s" % e)
        return None
    atexit.register(lambda: p.poll() is None and p.terminate())
    log("[phone-app] announced as %s on port %d (%s)" % (SERVICE, port, argv[0]))
    return p
