"""Round 46 (the local review of kit crissa-063a2813, 2026-10-23, the launch blocker): the presence module's two servers,
the camera grabber and presence itself, answer this machine only (modules/presence/crissa_presence/guard.py, the copy of
lib/crissa_guard.py). Each is started for real on a free loopback port and asked the way a web page would ask: a foreign
Origin, a foreign Host (DNS rebinding) and a cross-site <img> of the camera get 403 with no CORS header; this machine's
own pages on another port get their Origin back, never `*`; a Content-Length of -1 and a non-object body are 400; a
store_face `frame` must be one of this install's own files. Each kill puts the old behaviour back."""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "modules" / "presence"
os.environ.setdefault("CRISSA_HOME", tempfile.mkdtemp(prefix="crissa-r46-presence-"))
if str(MOD) not in sys.path:
    sys.path.insert(0, str(MOD))

LOOP = "http://127.0.0.1:8099"          # the Wall page, this machine, another port
EVIL = "https://evil.example"


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _ask(port, method, path, headers=None, body=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host or "127.0.0.1:%d" % port)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if body is not None and "Content-Length" not in (headers or {}):
        c.putheader("Content-Length", str(len(body)))
    c.endheaders()
    if body:
        c.send(body)
    r = c.getresponse()
    data = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
    c.close()
    return out


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # lib/crissa_presence.py (the player's presence reader) shares the package's name; a test that put lib/ first on
    # sys.path would hand that module to `from crissa_presence import ...`. The package is in place for each test and
    # whatever was there before comes back after.
    mod = sys.modules.get("crissa_presence")
    if mod is not None and not hasattr(mod, "__path__"):
        monkeypatch.delitem(sys.modules, "crissa_presence")
    if mod is None or not hasattr(mod, "__path__"):
        monkeypatch.syspath_prepend(str(MOD))
    for k in ("CRISSA_LOOPBACK_GUARD", "CRISSA_PRESENCE_BODY_CHECK", "CRISSA_PRESENCE_PATH_CONFINE",
              "RAIL_PRESENCE_HOST", "RAIL_PRESENCE_BODY_MAX", "RAIL_CAMERA_BODY_MAX"):
        monkeypatch.delenv(k, raising=False)


# ------------------------------------------------------------------------------------------------ the grabber --

@pytest.fixture
def grabber(monkeypatch):
    pytest.importorskip("cv2")
    from crissa_presence import grabber as g

    stop = threading.Event()
    stop.set()                                   # the mjpeg loop sends its headers and ends at once
    monkeypatch.setattr(g, "_stop", stop)
    monkeypatch.setattr(g, "buffer_meta", lambda: {"ok": True, "n": 0})
    httpd, port = _serve(g.Handler)
    yield g, port
    httpd.shutdown()
    httpd.server_close()


def test_grabber_mjpeg_is_unreadable_cross_site(grabber):
    _, port = grabber
    for hdrs, host in (({"Origin": EVIL}, None),
                       ({}, "evil.example:8192"),
                       ({"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "image"}, None),
                       ({"Sec-Fetch-Site": "cross-site", "Referer": EVIL + "/page"}, None)):
        st, h, _ = _ask(port, "GET", "/v1/mjpeg", hdrs, host=host)
        assert st == 403, (hdrs, host)
        assert "access-control-allow-origin" not in h, (hdrs, host)


def test_grabber_loopback_passes_and_gets_its_own_origin_never_a_star(grabber):
    _, port = grabber
    st, h, _ = _ask(port, "GET", "/v1/mjpeg", {"Origin": LOOP})
    assert st == 200 and h.get("access-control-allow-origin") == LOOP
    st, h, _ = _ask(port, "GET", "/v1/mjpeg")                       # urllib, curl: no Origin
    assert st == 200 and "access-control-allow-origin" not in h
    st, h, d = _ask(port, "GET", "/v1/buffer/meta")
    assert st == 200 and json.loads(d)["ok"] is True and "access-control-allow-origin" not in h


def test_grabber_post_refuses_foreign_and_bad_lengths(grabber):
    _, port = grabber
    st, h, _ = _ask(port, "POST", "/v1/nothing", {"Origin": EVIL, "Content-Type": "text/plain"}, b"{}")
    assert st == 403 and "access-control-allow-origin" not in h
    st, _, d = _ask(port, "POST", "/v1/nothing", {"Content-Length": "-1"})
    assert st == 400 and b"Content-Length" in d
    st, _, _ = _ask(port, "POST", "/v1/nothing", {"Content-Length": "lots"})
    assert st == 400
    st, _, d = _ask(port, "POST", "/v1/nothing", None, b"[1, 2]")
    assert st == 400 and b"object" in d
    st, _, _ = _ask(port, "POST", "/v1/nothing", None, b"{}")
    assert st == 404                                                  # a loopback object goes on to the routes


def test_grabber_kill_restores_the_open_port_and_the_star(grabber, monkeypatch):
    _, port = grabber
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    st, h, _ = _ask(port, "GET", "/v1/mjpeg", {"Origin": EVIL})
    assert st == 200 and h.get("access-control-allow-origin") == "*"


def test_grabber_body_kill_is_the_old_read(grabber, monkeypatch):
    _, port = grabber
    monkeypatch.setenv("CRISSA_PRESENCE_BODY_CHECK", "0")
    st, _, _ = _ask(port, "POST", "/v1/nothing", None, b"[1, 2]")
    assert st == 404


def test_grabber_body_cap_is_a_dial(grabber, monkeypatch):
    _, port = grabber
    monkeypatch.setenv("RAIL_CAMERA_BODY_MAX", "4")
    st, _, _ = _ask(port, "POST", "/v1/nothing", None, b'{"a": 1}')
    assert st == 400


# ---------------------------------------------------------------------------------------------- presence --

class _Engine:
    enrolled = {"x": 1}
    _enrolled_ts = 1.0


class _Svc:
    def __init__(self):
        self.engine = _Engine()


@pytest.fixture
def presence(monkeypatch):
    from crissa_presence import server as srv

    svc = _Svc()
    monkeypatch.setattr(srv, "_svc", svc)
    httpd, port = _serve(srv.make_handler())
    yield srv, svc, port
    httpd.shutdown()
    httpd.server_close()


def test_presence_refuses_the_web(presence):
    _, svc, port = presence
    for path in ("/v1/visitors", "/v1/camera/mjpeg", "/v1/presence", "/"):
        for hdrs, host in (({"Origin": EVIL}, None), ({}, "evil.example:8219"),
                           ({"Sec-Fetch-Site": "cross-site"}, None)):
            st, h, _ = _ask(port, "GET", path, hdrs, host=host)
            assert st == 403 and "access-control-allow-origin" not in h, (path, hdrs, host)
    # a cross-site form post cannot replace the owner's face
    st, h, _ = _ask(port, "POST", "/v1/enroll", {"Origin": EVIL, "Content-Type": "text/plain"},
                    json.dumps({"owner": "mallory", "faces": [[0.1] * 512]}).encode())
    assert st == 403 and "access-control-allow-origin" not in h
    st, _, _ = _ask(port, "POST", "/v1/people/reload", {"Origin": "null"}, b"{}")
    assert st == 403 and svc.engine.enrolled == {"x": 1}


def test_presence_this_machine_passes_with_its_own_origin(presence):
    _, svc, port = presence
    st, h, d = _ask(port, "POST", "/v1/people/reload", {"Origin": LOOP}, b'{"why": "test"}')
    assert st == 200 and json.loads(d)["ok"] is True and svc.engine.enrolled == {}
    assert h.get("access-control-allow-origin") == LOOP
    st, h, _ = _ask(port, "POST", "/v1/people/reload", None, b"{}")   # urllib: no Origin, no CORS header
    assert st == 200 and "access-control-allow-origin" not in h
    st, h, _ = _ask(port, "GET", "/", host="localhost:%d" % port)
    assert st == 200


def test_presence_bodies(presence):
    _, _, port = presence
    st, _, d = _ask(port, "POST", "/v1/people/reload", {"Content-Length": "-1"})
    assert st == 400 and b"Content-Length" in d
    st, _, d = _ask(port, "POST", "/v1/people/reload", None, b"[1, 2]")
    assert st == 400 and b"object" in d
    st, _, d = _ask(port, "POST", "/v1/enroll", None, b'"owner"')
    assert st == 400


def test_presence_kills(presence, monkeypatch):
    _, _, port = presence
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    st, h, _ = _ask(port, "POST", "/v1/people/reload", {"Origin": EVIL}, b"{}")
    assert st == 200 and h.get("access-control-allow-origin") == "*"
    monkeypatch.setenv("CRISSA_PRESENCE_BODY_CHECK", "0")
    try:                                                              # the old read: the route runs on a list and
        st = _ask(port, "POST", "/v1/people/reload", None, b"[1, 2]")[0]   # falls over (no answer, or a 500)
    except (http.client.RemoteDisconnected, ConnectionError):
        st = None
    assert st != 400


def test_store_face_frame_is_confined(presence, tmp_path):
    srv, _, port = presence
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8" + b"0" * 600)
    for frame in ("/etc/hostname", str(outside), "../../../../etc/passwd"):
        st, _, d = _ask(port, "POST", "/v1/store_face", None,
                        json.dumps({"person_id": "user", "frame": frame}).encode())
        assert st == 400 and b"state" in d, frame


def test_confined_path_unit(monkeypatch, tmp_path):
    from crissa_presence import paths as P
    from crissa_presence import server as srv

    home = Path(P.HOME)
    home.mkdir(parents=True, exist_ok=True)
    own = home / "r46-still.jpg"
    own.write_bytes(b"x")
    outside = tmp_path / "o.jpg"
    outside.write_bytes(b"x")
    link = home / "r46-link.jpg"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(outside)
    try:
        assert srv.confined_path(str(own)) == own.resolve()
        assert srv.confined_path(str(outside)) is None
        assert srv.confined_path(str(link)) is None                      # a symlink out is out
        assert srv.confined_path(str(home / ".." / "etc" / "passwd")) is None
        assert srv.confined_path("") is None and srv.confined_path(str(home)) is None
        monkeypatch.setenv("CRISSA_PRESENCE_PATH_CONFINE", "0")
        assert srv.confined_path(str(outside)) == outside
    finally:
        own.unlink()
        link.unlink()


def test_no_star_left_in_either_source():
    for rel in ("grabber.py", "server.py"):
        src = (MOD / "crissa_presence" / rel).read_text()
        assert '"Access-Control-Allow-Origin", "*"' not in src, rel
        for chunk in src.split("def do_")[1:]:
            assert chunk.split("\n", 2)[1].strip().startswith("if not self._guarded()"), rel


def test_a_named_bind_answers_to_its_name(presence, monkeypatch):
    _, _, port = presence
    monkeypatch.setenv("RAIL_PRESENCE_HOST", "192.0.2.7")
    st, _, _ = _ask(port, "GET", "/", host="192.0.2.7:%d" % port)
    assert st == 200
    monkeypatch.setenv("RAIL_PRESENCE_HOST", "0.0.0.0")
    st, _, _ = _ask(port, "GET", "/", host="192.0.2.7:%d" % port)
    assert st == 403
