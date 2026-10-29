"""Her voice plays in the Wall page, and the page says when it is heard (2026-10-05).

CRISSA_AUDIO_AT picks where a line is played:

    host   this machine's player (paplay / ffplay / afplay through lib/crissa_audio), as before.
           The default, and always the house's: is_house() reads `host` whatever the variable says.
    page   the Wall page (display/crissa-wall.html) plays it with WebAudio, in the browser that is
           showing her, and posts back the moment the first sample is heard. That moment, on this
           machine's clock, is the line's air_at - so her mouth and her voice share one clock even
           when the page is on another screen. No page watching, or no answer in time: the line
           plays on the host, as if the switch were off.

The flow, one line:

    house-speak  POST /v1/page-audio            the line's WAV (as the host player would have read it)
    page         GET  /v1/page-audio?v=N         long poll: the newest line, and the lines cancelled
    page         GET  /v1/page-audio/<id>.wav    the bytes
    page         POST /v1/page-audio/<id>/air    {"start_at": <this machine's clock>}  - when the file's
                                                 first sample leaves the speaker, output latency included
    house-speak  GET  /v1/page-audio/<id>/air    long poll for that answer; then it publishes the mouth
                                                 with air_at = start_at + the file's lead pad

A stop (the player's say_stop_all, or house-speak ending early) cancels the line, and the page's next
poll stops the sound. The page reads this machine's clock from GET /v1/clock and keeps the offset.

The store lives in the player's process; house-speak reaches it over loopback (`Client`).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from itertools import count

MODES = ("host", "page")
WATCH_S = 4.0          # a page that polled this recently is watching
KEEP = 8               # lines kept for a late fetch
MAX_BYTES = 40 * 1024 * 1024
ACK_PAST_S = 2.0       # an answer more than this far in the past or future is refused
ACK_FUTURE_S = 5.0


def _is_house() -> bool:
    try:
        import crissa_home
        return crissa_home.is_house()
    except ImportError:
        return False


def mode(env=None) -> str:
    """`page` only in a pack whose CRISSA_AUDIO_AT says so; everything else is `host`."""
    env = os.environ if env is None else env
    want = str(env.get("CRISSA_AUDIO_AT") or "host").strip().lower()
    if want != "page" or _is_house():
        return "host"
    return "page"


def enabled(env=None) -> bool:
    return mode(env) == "page"


class Store:
    """The player's side. Thread-safe; every change bumps `version` and wakes the long polls."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.cv = threading.Condition()
        self.version = 0
        self.lines: dict[str, dict] = {}
        self.order: list[str] = []
        self.ids = count(1)
        self.page_seen = 0.0          # the last poll from a page that can play (its sound is unlocked)
        self.pages: dict[str, float] = {}

    # -- house-speak ---------------------------------------------------------

    def push(self, wav: bytes, *, lead_s: float = 0.0, text: str = "") -> dict:
        """A new line. The newest line wins: anything still queued or playing is cancelled."""
        if not wav or len(wav) > MAX_BYTES:
            raise ValueError("a line is 1 byte to %d MB of WAV" % (MAX_BYTES // 1048576))
        with self.cv:
            for lid in self.order:
                ln = self.lines[lid]
                if ln["state"] in ("queued", "playing"):
                    ln["state"] = "cancelled"
            lid = "L%d" % next(self.ids)
            self.lines[lid] = {"id": lid, "wav": wav, "bytes": len(wav), "lead_s": float(lead_s),
                               "text": text[:200], "at": self.clock(), "state": "queued",
                               "start_at": None, "page": None}
            self.order.append(lid)
            while len(self.order) > KEEP:
                self.lines.pop(self.order.pop(0), None)
            self._bump()
            return self.meta(lid)

    def wait_air(self, lid: str, timeout: float) -> float | None:
        """The page's start_at for this line, or None when none came in `timeout` (or it was cancelled)."""
        end = self.clock() + timeout
        with self.cv:
            while True:
                ln = self.lines.get(lid)
                if ln is None or ln["state"] == "cancelled":
                    return None
                if ln["start_at"] is not None:
                    return ln["start_at"]
                left = end - self.clock()
                if left <= 0:
                    return None
                self.cv.wait(min(left, 0.5))

    def cancel(self, lid: str | None = None) -> int:
        """Cancel one line, or every live one (None). Returns how many changed."""
        n = 0
        with self.cv:
            for k in ([lid] if lid else list(self.order)):
                ln = self.lines.get(k)
                if ln and ln["state"] in ("queued", "playing"):
                    ln["state"] = "cancelled"
                    n += 1
            if n:
                self._bump()
        return n

    # -- the page ------------------------------------------------------------

    def poll(self, since: int, timeout: float, page: str = "", ready: bool = True) -> dict:
        """Long poll: returns when `version` passes `since` or at `timeout`. A page whose sound is still
        locked (no click yet, so the browser will not play) says ready=0 and does not count as watching:
        house-speak then plays the line on the host at once instead of waiting for an answer."""
        end = self.clock() + max(0.0, min(timeout, 25.0))
        with self.cv:
            self._seen(page, ready)
            while self.version <= since:
                left = end - self.clock()
                if left <= 0:
                    break
                self.cv.wait(min(left, 1.0))
                self._seen(page, ready)
            live = [self.meta(k) for k in self.order if self.lines[k]["state"] in ("queued", "playing")]
            gone = [k for k in self.order if self.lines[k]["state"] in ("cancelled", "done")]
            return {"v": self.version, "line": live[-1] if live else None, "stopped": gone}

    def wav(self, lid: str) -> bytes | None:
        with self.cv:
            ln = self.lines.get(lid)
            return ln["wav"] if ln else None

    def ack(self, lid: str, start_at: float, page: str = "", until: float | None = None) -> dict:
        """The page heard (or scheduled) the file's first sample at `start_at`, on this machine's clock."""
        now = self.clock()
        if not (now - ACK_PAST_S <= start_at <= now + ACK_FUTURE_S):
            raise ValueError("start_at %.3f is not near now (%.3f): is the page's clock offset stale?" % (start_at, now))
        with self.cv:
            ln = self.lines.get(lid)
            if ln is None:
                raise KeyError(lid)
            if ln["state"] == "cancelled":
                return self.meta(lid)
            if ln["start_at"] is None:          # the first page to answer owns the line
                ln["start_at"] = float(start_at)
                ln["page"] = page or None
                ln["state"] = "playing"
            if until is not None:
                ln["until"] = float(until)
            self._bump()
            return self.meta(lid)

    def done(self, lid: str) -> None:
        with self.cv:
            ln = self.lines.get(lid)
            if ln and ln["state"] in ("queued", "playing"):
                ln["state"] = "done"
                self._bump()

    # -- both ----------------------------------------------------------------

    def watching(self) -> bool:
        return self.clock() - self.page_seen <= WATCH_S

    def status(self) -> dict:
        with self.cv:
            last = self.meta(self.order[-1]) if self.order else None
            pages = sum(1 for t in self.pages.values() if self.clock() - t <= WATCH_S)
            return {"mode": "page", "watching": self.watching(), "pages": pages, "v": self.version, "last": last}

    def meta(self, lid: str) -> dict:
        ln = self.lines[lid]
        return {k: ln.get(k) for k in ("id", "bytes", "lead_s", "text", "at", "state", "start_at", "page")}

    def _seen(self, page: str, ready: bool = True) -> None:
        now = self.clock()
        if not ready:
            return
        self.page_seen = now
        if page:
            self.pages[page[:40]] = now
            if len(self.pages) > 32:
                for k, _t in sorted(self.pages.items(), key=lambda kv: kv[1])[:-32]:
                    self.pages.pop(k, None)

    def _bump(self) -> None:
        self.version += 1
        self.cv.notify_all()


# -- house-speak's side ------------------------------------------------------

class Client:
    """house-speak's loopback calls to the player. Every call answers None/False on any error:
    a page that cannot be reached means the host plays the line."""

    def __init__(self, base: str, timeout: float = 2.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _json(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None,
              timeout: float | None = None):
        import crissa_egress          # the one switch for what leaves (2026-10-05); off = urllib's own call
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        with crissa_egress.urlopen(req, timeout or self.timeout, "the player") as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def watching(self) -> bool:
        try:
            return bool(self._json("GET", "/v1/page-audio/status").get("watching"))
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def push(self, wav: bytes, lead_s: float, text: str = "") -> str | None:
        try:
            got = self._json("POST", "/v1/page-audio", data=wav,
                             headers={"Content-Type": "audio/wav", "X-Lead-S": "%.4f" % lead_s,
                                      "X-Text": text[:200].encode("ascii", "replace").decode()})
            return got.get("id")
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def wait_air(self, lid: str, timeout: float) -> float | None:
        try:
            got = self._json("GET", "/v1/page-audio/%s/air?wait=%.2f" % (lid, timeout), timeout=timeout + 2.0)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        v = got.get("start_at")
        return float(v) if v is not None else None

    def cancel(self, lid: str) -> None:
        try:
            self._json("POST", "/v1/page-audio/%s/stop" % lid, data=b"{}",
                       headers={"Content-Type": "application/json"}, timeout=1.0)
        except (OSError, ValueError, urllib.error.URLError):
            pass

    def done(self, lid: str) -> None:
        try:
            self._json("POST", "/v1/page-audio/%s/done" % lid, data=b"{}",
                       headers={"Content-Type": "application/json"}, timeout=1.0)
        except (OSError, ValueError, urllib.error.URLError):
            pass
