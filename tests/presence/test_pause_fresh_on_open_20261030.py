"""His wake reads the ear's pause fresh (the User 2026-10-30 walk: the first "Hey Crissa" turned nothing, the second
did). The bridge opens the session before it asks for the look; presence served a "paused" cached the second before."""
from __future__ import annotations

import io
import json
import time
import urllib.request

from crissa_presence import service


class _Svc:
    _ear_paused = service.PresenceService._ear_paused
    _paused_holds_home = service.PresenceService._paused_holds_home


def _ear(monkeypatch, paused):
    monkeypatch.setenv("RAIL_EAR_HEALTH_URL", "http://127.0.0.1:9/health")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({"paused": paused}).encode()))


def test_the_open_skips_a_stale_paused(monkeypatch):
    s = _Svc()
    s._ear_paused_cache = (time.time(), True)          # read a moment before the wake
    _ear(monkeypatch, False)                            # the wake has opened the session
    assert s._paused_holds_home() is True               # other looks keep the 1.5 s cache
    assert s._paused_holds_home(fresh=True) is False    # the session open asks the ear now


def test_kill_switch_keeps_the_cache(monkeypatch):
    s = _Svc()
    s._ear_paused_cache = (time.time(), True)
    _ear(monkeypatch, False)
    monkeypatch.setenv("RAIL_PAUSE_FRESH_ON_OPEN", "0")
    assert s._paused_holds_home(fresh=True) is True
