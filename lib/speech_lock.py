"""Shared flock so the voice and PA never paplay the same sink together.

Measured 2026-08-25: house_announce flock was private; house-speak had none.
Overlap on one sink garbles (a wider window for the announcer, ~32 s a line).
"""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)

LOCK = Path(
    os.environ.get(
        "HOUSE_SPEECH_LOCK",
        str(crissa_home.HOME / "house-speech.lock"),
    )
).expanduser()


@contextmanager
def speech_lock(timeout: float = 120.0):
    """Exclusive lock around paplay / voice stream play."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.time() + max(1.0, float(timeout))
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                os.close(fd)
                raise TimeoutError("house-speech.lock busy")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
