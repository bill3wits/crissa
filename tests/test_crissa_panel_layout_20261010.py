"""The log area beside the tiles has a bounded size (the User 2026-10-10, on .101's 3440x1440 ultrawide: "the log
container needs resizing, it crowds the tiles").

The Control Panel (:8097) and the feed's panel (:8099/panel) are rendered by a headless browser (its own profile, no
window) at 3440x1440, 2560x1440, 1920x1080 and 420x900, against a stand-in server with many tiles and a long journal.
At every size: the log area's box overlaps no tile's box, it is no wider than its bound, the page does not run off the
right edge, and the log scrolls on its own. Its fold (Hide log / Hide tape) is each viewer's own and survives a reload.
The browser Wall (/wall) has no log area beside its tiles (checked here); the desk Wall's roll is drawn by the Tk Wall."""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _kit import kit  # the kit's copy of a shipped file (the Crissa merge, 2026-10-11)

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIZES = [(3440, 1440), (2560, 1440), (1920, 1080), (1366, 768), (420, 900), (390, 844)]
LOG = ["2026-10-10 14:%02d:%02d crissa[%d]: a log line that is fairly long, as journal lines are, with a unit name and "
       "some detail %d" % (i // 60, i % 60, 1000 + i, i) for i in range(300)]
SERVICES = {"ok": True, "ts": 1.0, "fleet": {"total": 14, "down": 0, "warn": 1, "parked": 0}, "os_off": {},
            "systemd": "ok", "title": "Control Panel", "self_unit": "crissa-panel", "gpu": None,
            "services": [{"unit": "proc:p%d" % i, "label": "Piece %d" % i, "section": "Wall services" if i < 9 else
                          "Crissa lanes", "health": "ok", "active": "active", "sub": "running", "port": 8100 + i}
                         for i in range(14)],
            "addons": {"section": "krew services", "sections": [], "installed": [], "missing": [], "errors": [],
                       "link": {"links": [], "note": ""}},
            "launcher": {"alive": True, "present": True},
            "voices": {"section": "Voices", "tiles": [{"id": "pocket", "label": "Pocket", "state": "ok", "note": "on"},
                                                      {"id": "audio8", "label": "Audio8", "state": "ok", "note": "on"}]},
            "capability": {"features": {}, "unavailable": []}}
HOUSE = {"ok": True, "gpu": {"mode": "normal", "label": "Standard"}, "voice": {"voice_on": True},
         "svc": {"services": SERVICES["services"]}, "capability": {"features": {}, "unavailable": []}}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Stub:
    def __init__(self):
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
                p = self.path.split("?")[0]
                page = {"/": "crissa-panel.html", "/panel": "voiceonly-panel.html", "/wall": "crissa-wall.html"}.get(p)
                if page:
                    return self._send(200, kit("display/" + page).read_bytes(), "text/html; charset=utf-8")
                if p == "/v1/journal":
                    return self._send(200, json.dumps({"ok": True, "lines": LOG, "text": "\n".join(LOG)}).encode(),
                                      "application/json")
                ans = {"/v1/services": SERVICES, "/v1/house": HOUSE,
                       "/v1/init": {"ok": True, "needed": False, "status": "done"}}.get(p, {"ok": True})
                return self._send(200, json.dumps(ans).encode(), "application/json")

            def do_POST(self):  # noqa: N802
                return self._send(200, b'{"ok": true}', "application/json")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def browser():
    acc = _load("accept_layout", ROOT / "scripts" / "public-loops-accept.py")
    br = acc._browser()
    if not br:
        pytest.skip("no headless browser on this machine")
    stub = _Stub()
    yield acc, br, stub
    stub.srv.shutdown()


GEO = ("JSON.stringify((function(){function r(e){var b=e.getBoundingClientRect(); return [b.left,b.top,b.right,b.bottom];}"
       "var L=document.querySelector(%r); var T=[...document.querySelectorAll(%r)].filter(function(e){"
       "var b=e.getBoundingClientRect(); return b.width>0&&b.height>0;});"
       "var S=document.querySelector(%r);"
       "return {log: L? r(L): null, tiles: T.map(r), sw: document.documentElement.scrollWidth, vw: innerWidth,"
       "scrolls: S ? (getComputedStyle(S).overflowY==='auto'||getComputedStyle(S).overflowY==='scroll') : null};})())")


def _check(acc, br, stub, tmp_path, url, logsel, tilesel, scrollsel, bound):
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d%s" % (stub.port, url), str(tmp_path / "b.log"))
    try:
        for w, h in SIZES:
            pg._call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1,
                                                            "mobile": w < 600})
            time.sleep(2.5)
            g = json.loads(pg.js(GEO % (logsel, tilesel, scrollsel)))
            assert g["log"] and g["tiles"], (url, w)
            lb = g["log"]
            hit = [t for t in g["tiles"] if not (t[2] <= lb[0] + 0.5 or t[0] >= lb[2] - 0.5 or t[3] <= lb[1] + 0.5
                                                 or t[1] >= lb[3] - 0.5)]
            assert not hit, "%s at %dx%d: the log area %s overlaps %d tile(s), e.g. %s" % (url, w, h, lb, len(hit), hit[:1])
            rem = float(pg.js("parseFloat(getComputedStyle(document.documentElement).fontSize)") or 16)
            assert lb[2] - lb[0] <= bound(w, rem) + 1, "%s at %dx%d: the log is %d px wide" % (url, w, h, lb[2] - lb[0])
            assert g["sw"] <= g["vw"] + 1, "%s at %dx%d: the page runs off the right edge (%d)" % (url, w, h, g["sw"])
            assert g["scrolls"] is True, (url, w)
    finally:
        pg.close()


def test_the_8097_journal_never_overlaps_or_crowds_the_tiles(browser, tmp_path):
    acc, br, stub = browser
    _check(acc, br, stub, tmp_path, "/", "#logPane", "#svcRow .unit", "#svcJ",
           lambda w, rem: w - 32 if w <= 900 else max(340, min(720, 0.30 * w)))


def test_the_8099_tape_never_overlaps_or_crowds_the_tiles(browser, tmp_path):
    acc, br, stub = browser
    _check(acc, br, stub, tmp_path, "/panel", ".tape", ".unit, .lane, .occupancy, .master-lamp", "#svcJ",
           # the page stacks at max-width 88rem, a media query's rem being the browser's 16 px (not the page's own)
           lambda w, rem: w - 32 if w <= 88 * 16 else max(22 * rem, min(48 * rem, 0.30 * w)))


@pytest.mark.parametrize("url,key,folded", [("/", "#bLogFold", ".work.log-folded"),
                                            ("/panel", "#bTapeFold", ".deck.tape-folded")])
def test_the_fold_is_each_viewers_own_and_survives_a_reload(browser, tmp_path, url, key, folded):
    acc, br, stub = browser
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d%s" % (stub.port, url), str(tmp_path / "b.log"))
    try:
        time.sleep(2.0)
        first = url == "/"                  # round 26: the :8097 journal starts folded until a tile or Show log opens it
        assert pg.js("!!document.querySelector(%r)" % folded) is first
        pg.click(key)
        time.sleep(0.5)
        assert pg.js("!!document.querySelector(%r)" % folded) is not first
        pg._call("Page.reload", {})
        time.sleep(2.5)
        assert pg.js("!!document.querySelector(%r)" % folded) is not first, "the fold did not survive a reload"
        pg.click(key)
        time.sleep(0.5)
        assert pg.js("!!document.querySelector(%r)" % folded) is first
    finally:
        pg.close()


def test_the_browser_wall_has_no_log_area_beside_its_tiles(browser, tmp_path):
    acc, br, stub = browser
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d/wall?noinit=1" % stub.port, str(tmp_path / "b.log"))
    try:
        time.sleep(2.0)
        assert pg.js("document.querySelectorAll('#roll, .roll, #logs, .tape, #logPane').length") == 0
    finally:
        pg.close()


# ---- the desk Wall (Tk): its vitals, its oracle dial over the camera and FT tiles, its roll ------------------------
FIX = ROOT / "tests" / "fixtures" / "house-wall-ascii"


@pytest.mark.parametrize("W,H", SIZES[:4] + [(1280, 1024)])
def test_the_desk_walls_vitals_and_roll_keep_off_the_dial_and_its_tiles(W, H, monkeypatch):
    """On .101's 3440x1440 the type scaled with the width alone, so the vitals rows (LOGS last) ran 346 px into the
    oracle dial and the roll's lines ran across it. The body unit and the tiles are now sized to a 16:9 screen's width
    at most: the vitals end above the dial at every size, 16:9 and taller are unchanged, and in a pack the roll's text
    column starts right of the dial's column."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "lib"))
    monkeypatch.setenv("HOUSE_WALL_U_FIT", "1")   # the pack's fit; the house's glass keeps its layout (the Crissa merge)
    import house_wall_ascii_render as R
    house = json.loads((FIX / "house.json").read_text())
    pres = json.loads((FIX / "presence.json").read_text())
    g = R.Geom(W, H)
    R.render_hud(house, pres, None, g, 0.0, True)
    vb = R.ANCHORS["vitals_box"]
    dial, dial_bottom = g.oracle_box()
    assert vb[1] + vb[3] < dial_bottom - dial, "%dx%d: the vitals end at %d, under the dial's top %d" % (
        W, H, vb[1] + vb[3], dial_bottom - dial)
    if W * 9 <= H * 16:                                # 16:9 and taller: exactly the page's geometry, as before
        assert g.u == W / 88.0 and g.pip_w == 0.16 * W
    assert g.u_film == W / 88.0                          # the film's grid is never touched
    monkeypatch.setattr(R, "ROLL_CLEAR_DIAL", True)
    roll = R.Roll(since=0.0)
    roll.feed([{"who": "room", "text": "a long line of the room's talk " * 6, "ts": 1.0}], now=10.0)
    lay = roll.draw(g, now=10.0)
    bbox = lay.getbbox()
    assert bbox is None or bbox[0] >= g.page_left + dial, "%dx%d: the roll runs across the dial (%s)" % (W, H, bbox)


def test_the_8097_tiles_use_the_width_and_a_long_note_spans_two_columns(browser, tmp_path, monkeypatch):
    """.101 round 3 at 3440x1440: the tiles stayed small in wide empty space and the launcher's tile stood tall with
    its note in a narrow column. The tiles grow with the screen, and a long note lies under the name across two."""
    acc, br, stub = browser
    long_note = ("running under systemd: restarted by it x0 - last: the run before (pid 2369132, systemd) was stopped "
                 "on purpose (systemctl stop, or All OS Off)")
    rows = [dict(r) for r in SERVICES["services"]]
    rows[9] = dict(rows[9], unit="proc:launcher", label="Crissa launcher (crissa-launcher.service)", note=long_note,
                   health="warn")
    monkeypatch.setitem(SERVICES, "services", rows)
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d/" % stub.port, str(tmp_path / "b.log"))
    try:
        pg._call("Emulation.setDeviceMetricsOverride", {"width": 3440, "height": 1440, "deviceScaleFactor": 1,
                                                        "mobile": False})
        time.sleep(2.5)
        g = json.loads(pg.js("JSON.stringify([...document.querySelectorAll('#svcRow .unit')].map(function(e){"
                             "var b=e.getBoundingClientRect(); return {w:b.width,h:b.height,long:e.classList.contains("
                             "'long'),u:e.getAttribute('data-unit')};}))"))
    finally:
        pg.close()
    normal = [t for t in g if not t["long"]]
    longs = [t for t in g if t["long"]]
    assert longs and longs[0]["u"] == "proc:launcher", g
    assert min(t["w"] for t in normal) >= 300, "tiles stay small at 3440: %s" % sorted({int(t["w"]) for t in normal})
    assert longs[0]["w"] >= 2 * min(t["w"] for t in normal), longs
    assert longs[0]["h"] <= 2.2 * max(t["h"] for t in normal), "the long tile still stands tall: %s" % longs


def test_the_8099_panel_hides_the_houses_own_and_says_why_register_is_greyed(browser, tmp_path, monkeypatch):
    """.101 round 3: ZOS desktop, the kiosk and Brave glass, the janitor, TurboKrew and the wanted poster still showed
    on a pack's panel, and Register / New user were greyed with no reason. In a pack the house's own is hidden and
    listed under Not on this computer; Register and New user say where Voice ID lives in Crissa."""
    acc, br, stub = browser
    why_house, why_reg = "the house's own, not in Crissa", "Crissa's Setup is /init; Voice ID again is in the Crissa menu"
    feats = {"house_glass": {"ok": False, "why": why_house, "label": "The house's glass keys"},
             "house_only": {"ok": False, "why": why_house, "label": "The house's janitor and engine journal"},
             "turbokrew": {"ok": False, "why": "needs krew and an NVIDIA GPU", "label": "TurboKrew"},
             "house_register": {"ok": False, "why": why_reg, "label": "The house's Register an ID and New User keys"},
             "gpu_modes": {"ok": False, "why": "needs an NVIDIA GPU", "label": "Graphics card modes"}}
    cap = {"facts": {"house": False}, "features": feats,
           "unavailable": [{"id": k, "label": v["label"], "why": v["why"]} for k, v in feats.items()]}
    monkeypatch.setitem(HOUSE, "capability", cap)
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d/panel" % stub.port, str(tmp_path / "b.log"))
    try:
        pg._call("Emulation.setDeviceMetricsOverride", {"width": 3440, "height": 1440, "deviceScaleFactor": 1,
                                                        "mobile": False})
        time.sleep(3.0)
        got = json.loads(pg.js("JSON.stringify({hidden: ['#bZos','#jan','#bWanted','#rTurbo','#rKiosk','#rGlass']"
                               ".map(function(s){var e=document.querySelector(s); return [s, !e || e.offsetParent===null];}),"
                               "reg: (document.querySelector('#goRegister > span')||{}).textContent,"
                               "user: (document.querySelector('#goInitUser > span')||{}).textContent,"
                               "gpu: !!document.querySelector('#gpuSwitch.na-box, #gpuSwitch .na, #gpuSwitch.na'),"
                               "na: (document.querySelector('#naGroup')||{}).textContent||''})"))
    finally:
        pg.close()
    assert all(h for _s, h in got["hidden"]), got["hidden"]
    assert got["reg"] == why_reg and got["user"] == why_reg
    assert "Not on this computer (5)" in got["na"] and "TurboKrew" in got["na"]


def test_an_empty_voice_bank_in_a_pack_is_no_voice_id_yet(monkeypatch, capsys):
    import re
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "modules" / "ear"))
    from crissa_ear import voice_bank as VB
    monkeypatch.setattr(VB, "bank_counts", lambda: {})
    monkeypatch.setattr(VB, "_is_house", lambda: False)
    monkeypatch.setattr(VB, "_EMPTY_WARNED", False)
    assert VB.bank_health()["ok"] is False
    out = capsys.readouterr().out
    assert "no Voice ID yet" in out
    # the launcher's tile reads a log line as an error by these words (lib/crissa_procs._ERRLINE): none of them here
    import importlib
    procs = importlib.import_module("crissa_procs")
    assert not procs._ERRLINE.search(out), out
    assert not re.search(r"EMPTY BANK", out)


# ---- the browser Wall's bottom row (the User 2026-10-10, .101 round 3) ------------------------------------------------
CORNERS = ("#pipwrap", "#arToggle", "#arZoom", "#asciiRes", "#asciiBrt", "#ftPop", "#ftCtl")


class _WallStub:
    """/wall (the page), /glass (the feed's glass page, same origin), the JSON the two read."""

    def __init__(self, krew_ok=False):
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
                p = self.path.split("?")[0]
                pages = {"/wall": "crissa-wall.html", "/glass": "voiceonly-home.html"}
                if p in pages:
                    raw = kit("display/" + pages[p]).read_bytes().replace(b"__VO_PRESENCE__", b"http://127.0.0.1:9")
                    raw = raw.replace(b"__VO_PLAYER__", b"http://127.0.0.1:%d" % stub.port)
                    return self._send(200, raw, "text/html; charset=utf-8")
                cap = {"features": {"krew": {"ok": krew_ok, "why": "krew is not installed", "label": "krew"}},
                       "unavailable": []}
                ans = {"/v1/menu": {"ok": True, "glass": {"on": True, "up": True, "port": stub.port, "path": "/glass"},
                                    "mic": {"on": True, "ear": True, "label": "Microphone: on"}, "unavailable": [],
                                    "voice_id": {"ok": True, "label": "Voice ID again"}, "wall": {"present": False},
                                    "connect": {"url": "/connect", "label": "Connect"}, "help": {"label": "Help", "lines": []},
                                    "panel": {"url": "", "label": "Control Panel"}, "stop": {"label": "Stop", "ok": True},
                                    "composer": {"url": "", "label": "Composer"}},
                       "/v1/init": {"ok": True, "needed": False, "status": "done"},
                       "/v1/house": {"ok": True, "chat": [], "voice": {"voice_on": True}, "capability": cap}}.get(p, {"ok": True})
                return self._send(200, json.dumps(ans).encode(), "application/json")

            def do_POST(self):  # noqa: N802
                return self._send(200, b'{"ok": true}', "application/json")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


ROW = ("JSON.stringify((function(){function r(e){if(!e)return null;var b=e.getBoundingClientRect();"
       "return (b.width>0&&b.height>0)?[b.left,b.top,b.right,b.bottom]:null;}"
       "var f=document.getElementById('glass'), d=f&&f.contentDocument;"
       "return {key: r(document.getElementById('ckey')), say: r(document.getElementById('say')),"
       "bar: r(document.querySelector('.bar')), glass: document.body.classList.contains('glass'),"
       "keyop: parseFloat(getComputedStyle(document.getElementById('ckey')).opacity), sw: document.documentElement.scrollWidth,"
       "vw: innerWidth, corners: d ? %s.map(function(s){var e=d.querySelector(s); var b=e&&r(e); return b?[s].concat(b):null;})"
       ".filter(Boolean) : [], krew: d && d.getElementById('logKanban') ? [d.getElementById('logKanban').disabled,"
       "d.getElementById('logTools').disabled, d.getElementById('logKanban').title] : null};})())")


def _hit(a, b):
    return bool(a and b and not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3]))


def test_the_say_bar_and_the_crissa_key_share_the_middle_and_cover_nothing(browser, tmp_path):
    """The Say bar sits centred at the bottom with the Crissa key beside it; neither covers the glass's own corner keys
    (the camera box and its dials, her picture and its keys) nor each other, at 3440x1440, 2560x1440, 1920x1080 and a
    narrow 420x900; the key goes with the idle (round 14, 2026-10-12: "it can hide with mouse still like the rest") and
    comes back on a move; without krew the glass greys its Board and Tools chips."""
    acc, br, _stub = browser
    stub = _WallStub(krew_ok=False)
    pg = acc._Page(br, str(tmp_path / "prof"), "http://127.0.0.1:%d/wall" % stub.port, str(tmp_path / "b.log"))
    try:
        for w, h in ((3440, 1440), (2560, 1440), (1920, 1080), (420, 900)):
            pg._call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1,
                                                            "mobile": w < 600})
            time.sleep(4.0)                                   # past the key's idle (1 s): faded out, still laid out
            g = json.loads(pg.js(ROW % json.dumps(list(CORNERS))))
            assert g["glass"] and g["key"] and g["say"] and g["corners"], (w, g)
            for nm in ("key", "say", "bar"):
                bad = [c[0] for c in g["corners"] if _hit(g[nm], c[1:])]
                assert not bad, "%dx%d: the %s covers %s" % (w, h, nm, bad)
            assert not _hit(g["key"], g["say"]), (w, g["key"], g["say"])
            assert g["sw"] <= g["vw"] + 1, (w, "runs off the right edge")
            if w >= 1920:                                     # centred: the row's middle within 15% of the screen's
                mid = (min(g["key"][0], g["bar"][0]) + max(g["key"][2], g["bar"][2])) / 2.0
                assert abs(mid - w / 2.0) < 0.15 * w, (w, mid)
            assert g["keyop"] < 0.05, "the key goes with the idle (%s)" % g["keyop"]
            assert g["krew"] and g["krew"][0] is True and g["krew"][1] is True and "krew" in g["krew"][2]
        pg._call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 100, "y": 100})
        time.sleep(0.6)
        assert float(pg.js("parseFloat(getComputedStyle(document.getElementById('ckey')).opacity)")) > 0.95
    finally:
        pg.close()
        stub.srv.shutdown()
