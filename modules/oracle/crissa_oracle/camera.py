"""The room camera, opened quietly (2026-10-01). No camera is a state, not an error.

QUIET MEANS QUIET (2026-10-06, crissatest): with no camera, OpenCV prints its own warnings on every failed open,
and the service looks once a second - about eight lines a second in the log. A failed open now waits before the
next try (2 s, doubling to 30 s; any frame resets it), and this says once, in one line, that there is no camera.
No cap: it keeps trying for as long as it runs. CRISSA_ORACLE_CAMERA_RETRY_S sets the longest wait.

THE CAMERA SWITCH (2026-10-10): with CRISSA_NO_CAMERA=1 (the launcher's --no-camera) or CRISSA_ORACLE_CAMERA=off this
never opens a capture device: every read is None, said once (`camera: off`), and the state reads camera false.

ONE OWNER FOR THE CAMERA (2026-10-12, .101 round 10: the User on guest rails most turns). With the presence module
installed, its grabber (crissa-camera, the port table's "camera") owns the device and says so (`sole_owner`); this
service then tried cv2.VideoCapture(0) every 30 s for a day ("can't open camera by index", 2927 times on .101), so it
never looked, never posted a look to presence, and the face block stayed the door glance's, 7-15 s old: the ear's "the
face decides" and the fixed camera's sole slot both wait on these looks. Where a grabber is configured (camera_url():
CRISSA_ORACLE_CAMERA_URL, which the launcher sets when it starts the grabber, else RAIL_CAMERA_URL) a read is one GET of
its /snapshot, and the device is never opened here. Without one (no presence module), the device as before.
Kill: CRISSA_ORACLE_FROM_GRABBER=0.
"""
from __future__ import annotations

import os
import sys
import time


def off(env: dict | None = None) -> bool:
    """No camera here: the launcher's --no-camera (CRISSA_NO_CAMERA=1), or CRISSA_ORACLE_CAMERA=off|none."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return str(e.get("CRISSA_ORACLE_CAMERA") or "").strip().lower() in ("off", "none", "-1")


def index(env: dict | None = None) -> int:
    e = os.environ if env is None else env
    try:
        return int(e.get("CRISSA_ORACLE_CAMERA", "0"))
    except ValueError:
        return 0


def retry_max(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return max(1.0, float(e.get("CRISSA_ORACLE_CAMERA_RETRY_S", "30")))
    except ValueError:
        return 30.0


def camera_url(env: dict | None = None) -> str:
    """The grabber that owns the room camera, or '' (see ONE OWNER above)."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_ORACLE_FROM_GRABBER", "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    return str(e.get("CRISSA_ORACLE_CAMERA_URL") or e.get("RAIL_CAMERA_URL") or "").strip().rstrip("/")


def _snapshot(url: str, timeout: float = 1.5):
    """One BGR frame from the grabber's /snapshot, or None."""
    import urllib.request
    import cv2
    import numpy as np
    req = urllib.request.Request(url + "/snapshot", headers={"User-Agent": "crissa-oracle"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # loopback only: the grabber on this machine
        if r.status != 200:
            return None
        data = r.read()
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


class Camera:
    def __init__(self, idx: int | None = None, clock=time.monotonic):
        self.idx = index() if idx is None else idx
        self._cap = None
        self._clock = clock
        self._next_try = 0.0
        self._wait = 0.0
        self.said_missing = False
        self.off = off()
        self.said_off = False
        self.url = "" if self.off else camera_url()
        self.said_url = False

    def _missed(self) -> None:
        self._wait = min(retry_max(), (self._wait * 2) if self._wait else 2.0)
        self._next_try = self._clock() + self._wait
        if not self.said_missing:
            self.said_missing = True
            if getattr(self, "url", ""):
                print("crissa-oracle: no picture from the grabber (%s/snapshot); trying again every %d s at most"
                      % (self.url, int(retry_max())), file=sys.stderr, flush=True)
            else:
                print("crissa-oracle: no camera at index %d; trying again every %d s at most (OpenCV may print a "
                      "line each try)" % (self.idx, int(retry_max())), file=sys.stderr, flush=True)

    def read(self):
        """One BGR frame, or None when there is no camera (or it is busy)."""
        if self.off:
            if not self.said_off:
                self.said_off = True
                print("crissa-oracle: camera: off (CRISSA_NO_CAMERA=1); no capture device is opened", file=sys.stderr,
                      flush=True)
            return None
        if self.url:
            if self._clock() < self._next_try:
                return None
            if not self.said_url:
                self.said_url = True
                print("crissa-oracle: camera: the grabber's %s/snapshot (it owns the device; this never opens it)"
                      % self.url, file=sys.stderr, flush=True)
            try:
                frame = _snapshot(self.url)
            except Exception:  # noqa: BLE001  the grabber is down or busy: a miss, retried with the same backoff
                frame = None
            if frame is None:
                self._missed()
                return None
            self._wait, self.said_missing = 0.0, False
            return frame
        try:
            import cv2
            if self._cap is None:
                if self._clock() < self._next_try:
                    return None
                cap = cv2.VideoCapture(self.idx)
                if not cap.isOpened():
                    cap.release()
                    self._missed()
                    return None
                self._cap = cap
                self._wait, self.said_missing = 0.0, False
            ok, frame = self._cap.read()
            if not ok:
                self.close()
                return None
            return frame
        except Exception:
            return None

    def close(self) -> None:
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
