"""Degrees in, units once (the User 2026-10-04, Init v2).

Init sent /v1/look_at tilt -20 meaning degrees; the route passed it as units and
clamp_tilt snapped it to 0. `tilt_deg` is converted once by the camera profile;
`tilt` keeps meaning raw units.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

import pytest  # noqa: E402

from crissa_presence import camera_profile as cp  # noqa: E402
from crissa_presence import ptz, seat_shot  # noqa: E402


@pytest.fixture(autouse=True)
def _shipped_profile(monkeypatch):
    monkeypatch.delenv("RAIL_CAMERA_PROFILE", raising=False)


def test_the_shipped_profile_is_the_link_default():
    p = cp.load()
    assert p["source"].endswith("config/camera-profile.json")
    assert {k: p[k] for k in cp.PRODUCT_DEFAULT} == cp.PRODUCT_DEFAULT


def test_tilt_deg_minus_20_is_minus_72000():
    assert cp.deg_to_units(-20) == -72000
    assert cp.tilt_from_body({"tilt_deg": -20}) == -72000
    assert cp.tilt_from_body({"tilt_deg": "-20", "tilt": 5}) == -72000      # degrees win
    assert cp.tilt_from_body({"tilt": -20}) == -20                          # raw units, as before
    assert cp.tilt_from_body({}) is None
    assert seat_shot.clamp_tilt(cp.tilt_from_body({"tilt_deg": -20})) == -72000


@pytest.mark.parametrize("bad", ["down", float("nan"), float("inf")])
def test_a_bad_tilt_deg_is_refused(bad):
    with pytest.raises(ValueError):
        cp.tilt_from_body({"tilt_deg": bad})


def test_profile_override_and_broken_file(tmp_path, monkeypatch):
    f = tmp_path / "cam.json"
    f.write_text(json.dumps({"units_per_deg": 100, "pan_limit": 9000, "tilt_min": -500, "tilt_max": 500}))
    monkeypatch.setenv("RAIL_CAMERA_PROFILE", str(f))
    assert cp.deg_to_units(-20) == -2000
    assert cp.clamp_tilt(-2000) == -500
    f.write_text("{not json")
    assert cp.load()["source"] == "product_default" and cp.deg_to_units(-20) == -72000


def test_tilt_clamps_and_snaps():
    assert cp.clamp_tilt(-10_000_000) == -324000
    assert cp.clamp_tilt(10_000_000) == 360000
    assert cp.clamp_tilt(-71000) == -72000


def test_pan_is_clamped_never_wrapped():
    """Init's 11 o'clock is pan_deg 150 (5 h x 30 deg from the desk at 0): past the
    145 deg limit. A wrap would swing to the other side; the grabber also wraps
    +pan_limit itself to -pan_limit, so the clamp stops one step inside."""
    assert ptz.wrap_pan(522000) == -522000            # why the clamp is one step inside
    got, clamped = cp.clamp_pan(cp.deg_to_units(150))
    assert (got, clamped) == (518400, True) and got > 0
    assert ptz.wrap_pan(got) == got                   # survives the grabber unchanged
    assert cp.clamp_pan(-cp.deg_to_units(150)) == (-518400, True)
    assert cp.clamp_pan(432000) == (432000, False)
    assert cp.clamp_pan(540000)[0] == 518400


# ---------------------------------------------------------------- the route --

class _FakeSvc:
    def _identify_all_in_jpegs(self, frames, source=None, stop=None):
        return {"names": {}, "n_faces": 0}


@pytest.fixture
def server(monkeypatch):
    from crissa_presence import server as srv

    monkeypatch.setattr(srv, "_svc", _FakeSvc())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.make_handler())
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_look_at_route_takes_tilt_deg(server, monkeypatch):
    seen: list = []

    def fake_look_at(tgt, *, tilt=None, facefinder=True, n=3, owner="look_at"):
        seen.append(tilt)
        return {"ok": True, "frames": [], "tilt": seat_shot.clamp_tilt(tilt)}

    monkeypatch.setattr(seat_shot, "look_at", fake_look_at)
    code, out = _post(server, "/v1/look_at", {"station": "door", "tilt_deg": -20})
    assert code == 200 and seen[-1] == -72000 and out["tilt"] == -72000
    code, out = _post(server, "/v1/look_at", {"station": "door", "tilt": -72000})
    assert code == 200 and seen[-1] == -72000                       # raw units: unchanged
    code, out = _post(server, "/v1/look_at", {"station": "door", "tilt": -20})
    assert seen[-1] == -20 and out["tilt"] == 0                     # raw -20 units still snaps to 0
    n = len(seen)
    code, out = _post(server, "/v1/look_at", {"station": "door", "tilt_deg": "down"})
    assert code == 400 and out["error"] == "bad_tilt_deg" and len(seen) == n
