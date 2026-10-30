"""What this computer can do (the User 2026-10-10, after .101: no NVIDIA GPU, no krew).

One function (lib/crissa_capability.features) decides; the :8097 Control Panel, the feed's :8099/panel and the Crissa
menu read it, so they agree. On a machine like .101 every feature it cannot run is greyed with its one reason, or
folded into "Not on this computer", never a live key or an error; every available feature stays live.

The pages are RENDERED here by a headless browser (its own profile, no window) against a stand-in server that serves
the real page files and answers their reads with what the real servers build for a no-GPU, no-krew machine
(crissa-panel-server.py's blank_unavailable, the capability's decide). The kit's clean-install trial renders the two
live panels end to end as well."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _kit import kit  # the kit's copy of a shipped file (the Crissa merge, 2026-10-11)

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_capability as C  # noqa: E402
import crissa_home  # noqa: E402

DOT101 = {"house": False, "nvidia": False, "krew": False, "camera": True}
GONE = {"gpu_modes", "render", "turbo", "multilingual", "keep_warm", "krew", "turbokrew", "house_glass", "house_only",
        "house_register"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_machine_like_101_lacks_the_gpu_and_krew_features_each_with_its_reason():
    f = C.decide(DOT101)
    assert {k for k, v in f.items() if not v["ok"]} == GONE
    for k in ("gpu_modes", "render", "turbo", "multilingual", "keep_warm"):
        assert f[k]["why"] == "needs an NVIDIA GPU", k
    assert f["krew"]["why"] == "krew is not installed"
    assert f["turbokrew"]["why"] == "needs krew and an NVIDIA GPU"
    assert f["camera"]["ok"] and f["camera"]["why"] == ""
    assert C.decide(dict(DOT101, camera=False))["camera"]["why"] == "no camera on this computer"
    gpu_krew = C.decide(dict(DOT101, nvidia=True, krew=True))
    assert {k for k, v in gpu_krew.items() if not v["ok"]} == {"house_glass", "house_only", "house_register"}
    assert "/init" in f["house_register"]["why"] and "Crissa key's menu" in f["house_register"]["why"]
    assert not [k for k, v in C.decide(dict(DOT101, house=True, nvidia=True, krew=True)).items() if not v["ok"]]


def test_the_facts_come_from_what_init_already_wrote(tmp_path, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    (tmp_path / "voice-tier.json").write_text(json.dumps({"facts": {"gpu": {"kind": "none"}}}))
    env = {"PATH": os.environ.get("PATH", "")}
    # A RECORDED "none" IS RE-ASKED (the User 2026-10-27: an RTX PRO 6000 box whose Init record said none): the live probe
    # decides, so the same record reads False on a box with no GPU and True on one that has it.
    import sys, types
    stub = types.ModuleType("crissa_voice_tiers")
    stub.probe = lambda env=None: {"gpu": {"kind": "none"}}
    monkeypatch.setitem(sys.modules, "crissa_voice_tiers", stub)
    assert C.facts(tmp_path, env)["nvidia"] is False
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    stub.probe = lambda env=None: {"gpu": {"kind": "cuda", "name": "x"}}
    assert C.facts(tmp_path, env)["nvidia"] is True
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    (tmp_path / "voice-tier.json").write_text(json.dumps({"facts": {"gpu": {"kind": "cuda"}}}))
    assert C.facts(tmp_path, env)["nvidia"] is True
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    assert C.facts(tmp_path, dict(env, CUDA_VISIBLE_DEVICES=""))["nvidia"] is False     # hidden from every engine
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    assert C.facts(tmp_path, dict(env, CRISSA_NO_CAMERA="1"))["camera"] is False
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    assert C.facts(tmp_path, env)["krew"] is False
    (tmp_path / "endpoints.d").mkdir()
    (tmp_path / "endpoints.d" / "krew.json").write_text(json.dumps({"name": "krew", "url": "http://127.0.0.1:1/v1",
                                                                     "by": "krew"}))
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    assert C.facts(tmp_path, env)["krew"] is True
    monkeypatch.setattr(C, "_CACHE", {"at": 0.0, "key": None, "facts": None})
    assert not C.features(tmp_path, dict(env, CRISSA_CAPABILITY="0"))["unavailable"]


def _cap(facts):
    feats = C.decide(facts)
    return {"facts": facts, "features": feats,
            "unavailable": [{"id": k, "label": feats[k]["label"], "why": feats[k]["why"]} for k in C.ORDER
                            if not feats[k]["ok"]]}


def _services_101():
    """/v1/services as crissa-panel-server.py builds it on a machine like .101 (its own blank_unavailable)."""
    panel = _load("crissa_panel_server_cap", ROOT / "scripts" / "crissa-panel-server.py")
    data = {"ok": True, "ts": 1.0, "fleet": {"total": 2, "down": 0, "warn": 0, "parked": 0}, "os_off": {},
            "systemd": "ok", "title": "Control Panel", "self_unit": "crissa-panel",
            "gpu": {"mode": "normal", "label": "Standard", "modes": [{"id": "beast", "label": "Beast"},
                                                                     {"id": "render", "label": "Render"}],
                    "off_label": "Park GPU"},
            "services": [{"unit": "proc:player", "label": "Crissa", "section": "Wall services", "health": "ok",
                          "active": "active", "sub": "running", "port": 8100},
                         {"unit": "proc:panel", "label": "Control Panel", "section": "Wall services", "health": "ok",
                          "active": "active", "sub": "running", "port": 8097}],
            "addons": {"section": "krew services", "sections": ["krew services"], "installed": [],
                       "missing": [{"slot": "krew", "label": "Krew", "note": "krew not installed", "state": "off",
                                    "mark": "o", "section": "krew services", "placeholder": True}], "errors": [],
                       "link": {"links": [], "note": "krew not installed - watching Crissa only"}},
            "launcher": {"alive": True, "present": True},
            "voices": {"section": "Voices", "tiles": [
                {"id": "turbo", "label": "Chatterbox", "state": "off", "mark": "o", "note": "not installed"},
                {"id": "pocket", "label": "Pocket", "state": "ok", "mark": "o", "note": "running"},
                {"id": "audio8", "label": "Audio8 (announcer)", "state": "ok", "mark": "o", "note": "running"},
                {"id": "warm", "label": "Keep-warm", "state": "off", "mark": "o", "note": "off"}]},
            "capability": _cap(DOT101)}
    panel.blank_unavailable(data, data["capability"])
    return data


def test_the_8097_server_blanks_what_this_computer_cannot_run():
    d = _services_101()
    by = {v["id"]: v for v in d["voices"]["tiles"]}
    assert by["turbo"]["blank"] == "needs an NVIDIA GPU" and by["warm"]["blank"] == "needs an NVIDIA GPU"
    assert "blank" not in by["pocket"] and "blank" not in by["audio8"]
    # greyed in its section, never folded away (the User 2026-10-28: "grey out any krew services if not present")
    assert "blank" not in d["addons"]["missing"][0] and d["addons"]["missing"][0]["note"] == "krew not installed"
    assert d["gpu"] is None                                        # no Mode row without the GPU


class _Stub:
    """Serves the real page files and answers their reads from `answers` ({} for anything else)."""

    def __init__(self, page: Path, answers: dict):
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                if path in ("/", "/panel"):
                    return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
                ans = answers.get(path, {"ok": True})
                return self._send(200, json.dumps(ans).encode(), "application/json")

            def do_POST(self):  # noqa: N802
                stub.posts.append(self.path)
                return self._send(200, b'{"ok": true}', "application/json")
        self.posts: list = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def _page(url, tmp_path):
    acc = _load("accept_cap", ROOT / "scripts" / "public-loops-accept.py")
    br = acc._browser()
    if not br:
        pytest.skip("no headless browser on this machine")
    return acc._Page(br, str(tmp_path / "prof"), url, str(tmp_path / "browser.log"))


def _wait(pg, expr, s=20.0):
    import time
    end = time.time() + s
    while time.time() < end:
        got = pg.js(expr)
        if got:
            return got
        time.sleep(0.3)
    return pg.js(expr)


def test_the_8097_page_folds_what_this_computer_cannot_run_and_keeps_the_rest_live(tmp_path):
    stub = _Stub(ROOT / "display" / "crissa-panel.html",
                 {"/v1/services": _services_101(), "/v1/caps": {"ok": True, "gpu": False, "title": "Control Panel"},
                  "/v1/mic": {"ok": True, "on": False, "ear": True}})
    pg = _page("http://127.0.0.1:%d/" % stub.port, tmp_path)
    try:
        assert _wait(pg, "document.querySelectorAll('#naGroup li').length")
        na = json.loads(pg.js("JSON.stringify([...document.querySelectorAll('#naGroup li')].map(function(li){"
                              "return [li.getAttribute('data-feature'), li.textContent];}))"))
        assert {f for f, _t in na} == GONE
        assert all(t.endswith(C.decide(DOT101)[f]["why"]) for f, t in na)
        assert pg.js("document.querySelectorAll('[data-voice=turbo],[data-voice=warm]').length") == 0
        assert pg.js("document.querySelectorAll('[data-voice=pocket],[data-voice=audio8]').length") == 2
        assert pg.js("document.querySelectorAll('#gpuKeys button').length") == 0
        # krew greyed in its own section, never folded away (the User 2026-10-28: "grey out any krew services if not
        # present"; the 10-29 scan of cut 12)
        assert pg.js("document.querySelectorAll('.unit.ph[data-slot=krew]').length") == 1
        assert pg.js("document.querySelector('.unit.ph[data-slot=krew]').textContent.indexOf('not installed') >= 0")
        assert pg.js("!!document.getElementById('bVoice') && !document.getElementById('bVoice').disabled")
    finally:
        pg.close()
        stub.close()


def test_the_8099_page_greys_each_unavailable_control_with_its_reason(tmp_path):
    house = {"ok": False, "error": "house upstream down", "gpu": {"mode": "normal", "label": "Standard"},
             "svc": {"services": [{"unit": "proc:player", "label": "Crissa", "health": "ok", "active": "active"}]},
             "voice": {"voice_on": False}, "capability": _cap(DOT101)}
    stub = _Stub(kit("display/voiceonly-panel.html"), {"/v1/house": house})
    pg = _page("http://127.0.0.1:%d/panel" % stub.port, tmp_path)
    try:
        assert _wait(pg, "document.querySelectorAll('#naGroup li').length")
        na = [f for f in json.loads(pg.js("JSON.stringify([...document.querySelectorAll('#naGroup li')]"
                                          ".map(function(li){return li.getAttribute('data-feature');}))"))]
        assert set(na) == GONE
        for sel in ("#gpuSwitch", "#rbKrew", "#kanbanDrill", "#studio"):
            assert pg.js("(function(e){return e.classList.contains('na')||e.classList.contains('na-box');})"
                         "(document.querySelector(%r))" % sel), sel
        # the house's own is hidden in a pack, not greyed (round 3, 2026-10-10), and listed under Not on this computer
        for sel in ("#rTurbo", "#rKiosk", "#rGlass", "#bZos", "#jan", "#spdCheck", "#bWanted"):
            assert pg.js("(function(e){return e.classList.contains('na-hidden') && e.offsetParent === null;})"
                         "(document.querySelector(%r))" % sel), sel
        assert pg.js("[...document.querySelectorAll('#gpuSwitch button')].every(function(b){return b.disabled;})")
        assert pg.js("document.querySelector('#bZos').disabled === true")
        whys = json.loads(pg.js("JSON.stringify([...document.querySelectorAll('.na-why')].map(function(w){"
                                "return [w.getAttribute('data-feature'), w.textContent];}))"))
        assert dict(whys)["gpu_modes"] == "needs an NVIDIA GPU" and dict(whys)["krew"] == "krew is not installed"
        # what this computer has stays live: the camera (it has one), the Mic, Dev mode
        for sel in ("#camera .acts", "#bVoice", "#bDevMode"):
            assert pg.js("(function(e){return !e.classList.contains('na')&&!e.classList.contains('na-box');})"
                         "(document.querySelector(%r))" % sel), sel
    finally:
        pg.close()
        stub.close()


def test_the_menu_shows_the_same_answer(monkeypatch, tmp_path):
    import crissa_menu as M
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(C, "features", lambda home=None, env=None: _cap(DOT101))
    monkeypatch.setattr(M, "_presence", lambda: {})
    v = M.view(tmp_path, ear_up=lambda: True, presence={})
    assert {x["id"] for x in v["unavailable"]} == {g for g in GONE if not g.startswith("house_")}
    import crissa_wall_key as K
    items = K.menu_items(v, fullscreen=False)
    assert ("Not on this computer:", "na", False) in items
    assert ("  Render and its studio - needs an NVIDIA GPU", "na", False) in items
