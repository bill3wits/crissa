"""The presence module beside another install (the User 2026-10-10).

The clean-install trial ran this pack on the reference machine with every port moved by an offset, and the presence
service's door-first start still sent /v1/facelock and /v1/return_door to the reference install's camera on 8192: the
service is the reference rail copied whole, and its other addresses default to that install's ports. The launch script
now pins every one of them to this install's port table before the service loads. These tests hold that: no pinned
address names a reference port under an offset, every reference-port default the module reads by an environment key
is pinned, and the base-URL readers of the presence address fall to this install's own.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PMOD = ROOT / "modules" / "presence"
REF = re.compile(r":(8190|8191|8192|8194|8099|8100)\b")


def _script():
    spec = importlib.util.spec_from_file_location("crissa_presence_script", PMOD / "scripts" / "crissa-presence.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_no_pinned_address_names_a_reference_port_under_an_offset():
    m = _script()
    env = {"CRISSA_PORT_OFFSET": "10000"}
    pins = m.pinned_addresses("http://127.0.0.1:%d" % m._port("camera", env), env)
    bad = {k: v for k, v in pins.items() if REF.search(v)}
    assert not bad, bad
    assert pins["RAIL_PRESENCE_GRABBER_RETURN_DOOR"] == "http://127.0.0.1:18192/v1/return_door"
    assert pins["RAIL_PRESENCE_GRABBER_FACELOCK"] == "http://127.0.0.1:18192/v1/facelock"
    assert pins["HOUSE_CONDUCTOR_URL"] == "http://127.0.0.1:18100" and pins["VO_HOME_URL"] == "http://127.0.0.1:18099"
    assert pins["RAIL_PRESENCE_BASE"] == "http://127.0.0.1:18219"


def test_a_camera_named_elsewhere_carries_every_camera_address():
    pins = _script().pinned_addresses("http://10.9.8.7:7000/")
    for k in ("RAIL_CAMERA_URL", "RAIL_PRESENCE_GRABBER_AIM", "RAIL_CAMERA_LEASE", "RAIL_DOOR_TRIGGERS"):
        assert pins[k].startswith("http://10.9.8.7:7000"), k


def test_every_reference_port_default_read_by_a_key_is_pinned():
    pinned = set(_script().pinned_addresses("http://127.0.0.1:18192"))
    # RAIL_PRESENCE_URL has two meanings in the copied rail (a base for four readers, the full /v1/presence for three):
    # the base readers fall to RAIL_PRESENCE_BASE (pinned), the full readers' own default is this install's 8219
    allowed = {"RAIL_PRESENCE_URL"}
    seen = set()
    for f in (PMOD / "crissa_presence").rglob("*.py"):
        for key, _port in re.findall(r'"([A-Z][A-Z0-9_]+)",\s*"http://127\.0\.0\.1:(8190|8191|8192|8194|8099|8100)', f.read_text()):
            seen.add(key)
    missing = sorted(seen - pinned - allowed)
    assert not missing, missing
    # since 2026-10-10 the copied rail names no reference port at all: every default is crissa_presence/ports.py's
    # (tests/test_crissa_loopback_guard.py scans every shipped file); the pins above stay for a value set by hand
    assert not seen, "a reference-port default came back: %s" % sorted(seen)


def test_the_base_readers_fall_to_this_installs_presence():
    for f in ("look_around.py", "reinit_scan.py", "gemma_event_loop.py", "enroll_panel/voice_register.py"):
        text = (PMOD / "crissa_presence" / f).read_text()
        assert 'os.environ.get("RAIL_PRESENCE_BASE")' in text, f
