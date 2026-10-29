"""All OS Off in a pack has its server half (the User 2026-10-28, "Crissa Wall + Krew = house now"; KIT-GAPS item 2):
the pack's :8099 page posts house_off / house_on and reads gpu.os_off, and the pack's feed (crissa/overlay's copy of
scripts/voiceonly-home-server.py) forwards them to the kit's Control Panel server (os_off / os_on, /v1/os_power), the
one owner of the switch. The house copy is untouched. Kill CRISSA_FEED_OS_POWER=0."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

from _kit import kit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402


def _load(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("vo_pack_power", kit("scripts/voiceonly-home-server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(calls, answer):
    def open_(req, timeout=0):
        calls.append((req.full_url, req.get_method(), json.loads(req.data.decode()) if req.data else None, timeout))
        return _Resp(json.dumps(answer).encode())
    return open_


def test_the_two_actions_forward_to_the_kit_panel_as_os_off_and_os_on(tmp_path, monkeypatch):
    vo = _load(tmp_path, monkeypatch)
    monkeypatch.setenv("HOUSE_WALL_PANEL", "http://127.0.0.1:8097/")
    calls = []
    out = vo.pack_os_power_action("house_off", opener=_opener(calls, {"ok": True, "started": True, "note": "off"}))
    assert out["ok"] and out["action"] == "house_off" and out["os_power"] is True and out["started"] is True
    assert calls[0][0] == "http://127.0.0.1:8097/v1/action" and calls[0][2] == {"action": "os_off"}
    out = vo.pack_os_power_action("house_on", opener=_opener(calls, {"ok": True}))
    assert out["started"] is True and calls[-1][2] == {"action": "os_on"}
    assert vo.pack_os_power_action("mode_off") is None                       # the GPU keys keep their own path
    monkeypatch.setenv("CRISSA_FEED_OS_POWER", "0")
    assert vo.pack_os_power_action("house_off") is None


def test_the_state_rides_in_gpu_os_off_and_a_silent_panel_is_said(tmp_path, monkeypatch):
    vo = _load(tmp_path, monkeypatch)
    monkeypatch.setenv("HOUSE_WALL_PANEL", "http://127.0.0.1:8097")
    st = {"in_force": True, "n": 3, "paused": ["a.service"], "since": "2026-10-28T08:00:00", "failed": [], "return_mode": "normal"}
    assert vo.pack_os_off_state(opener=_opener([], {"ok": True, "os_off": st})) == st
    assert vo.pack_os_off_state(opener=_opener([], {"ok": True})) is None

    def dead(req, timeout=0):
        raise OSError("refused")
    got = vo.pack_os_power_call("/v1/os_power", opener=dead)
    assert got["ok"] is False and "did not answer" in got["error"]
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert vo.pack_os_off_state() is None and vo.pack_os_power_action("house_off") is None   # the house: untouched


def test_the_routes_are_wired_and_the_house_copy_is_not():
    src = kit("scripts/voiceonly-home-server.py").read_text(encoding="utf-8")
    assert 'if act in ("house_off", "house_on"):' in src and "fwd = pack_os_power_action(act)" in src
    assert 'if path == "/v1/os_power":' in src and '"os_off": _pack_os_off' in src
    # after the fold (kit-fold-20261028: one feed server behind is_house) the house file carries the pack half too;
    # what must hold either way is that the house never forwards (pack_os_power_on is False on the house)
    house = (ROOT / "scripts" / "voiceonly-home-server.py").read_text(encoding="utf-8")
    if "pack_os_power_action" in house:
        assert "def pack_os_power_on(" in house and "(not crissa_home.is_house()) and" in house
    page = kit("display/voiceonly-panel.html").read_text(encoding="utf-8")
    assert 'data-act="house_off"' in page and 'function hasOsPower(g){ return !!g && Object.prototype.hasOwnProperty.call(g, "os_off"); }' in page
