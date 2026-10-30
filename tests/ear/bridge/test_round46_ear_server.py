"""Round 46 (the local review of kit crissa-063a2813, 2026-10-23): the ear's HTTP server answers this machine only.

A1: every handler runs the loopback guard (no `Access-Control-Allow-Origin: *`, /v1/turns unreadable cross-site, DELETE
and omni notes refused from a foreign page), a negative / oversize Content-Length is refused unread, and a voice clip is
written only under the ear's own home. Each with its kill. No audio is played or recorded: the routes used here read
the turn log and the rolling cache only.
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

from crissa_ear import server, voice_cache
from crissa_ear.config import Config
from crissa_ear.home import EAR


@pytest.fixture()
def ear():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(Config()))
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_host=bool(headers and "Host" in headers), skip_accept_encoding=True)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if body is not None and not (headers and "Content-Length" in headers):
        c.putheader("Content-Length", str(len(body)))
    c.endheaders()
    if body:
        c.send(body)
    r = c.getresponse()
    data = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, data


EVIL = {"Origin": "https://evil.example"}


def test_turns_answer_this_machine_and_never_star(ear, monkeypatch):
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    st, h, _ = call(ear, "GET", "/v1/turns")                       # curl, the bridge: no headers
    assert st == 200 and "access-control-allow-origin" not in h
    st, h, _ = call(ear, "GET", "/v1/turns", {"Origin": "http://127.0.0.1:8100"})   # the Wall page
    assert st == 200 and h.get("access-control-allow-origin") == "http://127.0.0.1:8100"
    st, h, body = call(ear, "GET", "/v1/turns", EVIL)
    assert st == 403 and "access-control-allow-origin" not in h and b"turns" not in body
    st, _, _ = call(ear, "GET", "/v1/turns", {"Host": "evil.example:8191"})   # a rebound name
    assert st == 403
    st, _, _ = call(ear, "GET", "/health", {"Sec-Fetch-Site": "cross-site"})  # a no-cors <img> from another site
    assert st == 403


def test_delete_omni_and_preflight_refused_cross_site(ear, monkeypatch):
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    assert call(ear, "DELETE", "/v1/turns", EVIL)[0] == 403
    assert call(ear, "POST", "/v1/omni/note", EVIL, b'{"text": "x"}')[0] == 403
    assert call(ear, "OPTIONS", "/v1/turns", EVIL)[0] == 403
    assert call(ear, "DELETE", "/v1/turns")[0] == 200                  # this machine still clears them


def test_guard_kill_brings_back_the_old_star(ear, monkeypatch):
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    st, h, _ = call(ear, "GET", "/v1/turns", EVIL)
    assert st == 200 and h.get("access-control-allow-origin") == "*"


def test_bad_content_length_refused_unread(ear, monkeypatch):
    st, _, body = call(ear, "POST", "/v1/omni/note", {"Content-Length": "-1"})
    assert st == 400 and b"Content-Length" in body
    st, _, _ = call(ear, "POST", "/v1/omni/note", {"Content-Length": "abc"})
    assert st == 400
    st, _, _ = call(ear, "POST", "/v1/omni/note", {"Content-Length": str(server.body_cap() + 1)})
    assert st == 400


def test_body_cap_is_a_dial(monkeypatch):
    assert server.body_cap() == int(server.BODY_MAX_S * server.BODY_MAX_RATE * 2)
    monkeypatch.setattr(server, "BODY_MAX_S", 0.0)
    assert server.body_cap() is None                                   # 0 = no size cap


@pytest.fixture()
def ring(monkeypatch):
    monkeypatch.setattr(voice_cache, "ENABLED", True)
    voice_cache.arm()
    voice_cache.offer(np.zeros(1600, dtype=np.int16), 16000)
    yield
    voice_cache.clear()


def test_voice_clip_only_under_the_ears_home(ear, ring, monkeypatch, tmp_path):
    monkeypatch.delenv("CRISSA_EAR_CLIP_CONFINE", raising=False)
    monkeypatch.delenv("RAIL_SECURITY_INCIDENTS", raising=False)
    out = tmp_path / "elsewhere" / "voice.wav"
    st, _, body = call(ear, "POST", "/v1/security/voice_clip", None, json.dumps({"dest": str(out)}).encode())
    assert st == 403 and not out.exists() and not out.parent.exists()
    sneaky = EAR / "incidents" / ".." / ".." / ".." / "x" / "voice.wav"
    assert call(ear, "POST", "/v1/security/voice_clip", None, json.dumps({"dest": str(sneaky)}).encode())[0] == 403
    notwav = EAR / "incidents" / "i1" / "person.json"
    assert call(ear, "POST", "/v1/security/voice_clip", None, json.dumps({"dest": str(notwav)}).encode())[0] == 403
    good = EAR / "incidents" / "i1" / "voice.wav"
    st, _, body = call(ear, "POST", "/v1/security/voice_clip", None, json.dumps({"dest": str(good)}).encode())
    got = json.loads(body)
    assert st == 200 and (got.get("ok") or got.get("error") == "cache empty")


def test_voice_clip_symlink_out_of_home_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_EAR_CLIP_CONFINE", raising=False)
    EAR.mkdir(parents=True, exist_ok=True)
    link = EAR / "r46-link"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(tmp_path)
    try:
        assert server.clip_dest_ok(str(link / "voice.wav")) == "dest outside the ear's home"
    finally:
        link.unlink()


def test_voice_clip_incidents_root_and_kill(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_SECURITY_INCIDENTS", str(tmp_path / "inc"))
    assert server.clip_dest_ok(str(tmp_path / "inc" / "a" / "voice.wav")) == ""
    monkeypatch.delenv("RAIL_SECURITY_INCIDENTS")
    assert server.clip_dest_ok(str(tmp_path / "inc" / "a" / "voice.wav"))
    monkeypatch.setenv("CRISSA_EAR_CLIP_CONFINE", "0")
    assert server.clip_dest_ok("/anywhere/at/all.txt") == ""
