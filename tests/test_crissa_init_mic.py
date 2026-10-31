"""Her microphone after Init (the User 2026-10-10, from the kit's clean-install trial): Init's done plate said "she is
listening" while the Mic was off - it stays off until the Mic key is pressed - and the Control Panel on :8097 had no
Mic key at all.

  * the done plate says plainly whether the microphone is on, and carries a "Turn on the microphone" key
    (POST /v1/init {action: "mic", on: true}), which turns it on through the ear's own route (POST /panel/voice);
  * the Control Panel has the feed panel's Mic key (#bVoice): GET /v1/mic reads the ear's voice lock, POST /v1/mic
    presses it.

The ear here is a loopback stand-in whose /panel/voice and /v1/house/voice are the ear's own lock functions
(crissa_ear.house_voice), so the lock file is the one the ear reads."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _kit import kit  # the kit's copy of a shipped file (the Crissa merge, 2026-10-11)

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "lib"), str(ROOT / "modules" / "id")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import crissa_home  # noqa: E402
import crissa_init as zi  # noqa: E402


def _house_voice(lock: Path):
    """The ear's own lock module (crissa_ear/house_voice.py), its state home pointed at this test's."""
    path = ROOT / "modules" / "ear" / "crissa_ear" / "house_voice.py"
    src = path.read_text(encoding="utf-8").replace("from .home import HOME", "HOME = Path(%r)" % str(lock.parent))
    mod = type(sys)("ear_house_voice_for_mic")
    mod.__dict__["Path"] = Path
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


@pytest.fixture
def ear(tmp_path, monkeypatch):
    """A stand-in ear on the loopback serving the ear's two Mic routes over the ear's own lock code."""
    home = tmp_path / "home"
    home.mkdir()
    hv = _house_voice(home / "house-voice.json")
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, d):
            raw = json.dumps(d).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            if self.path == "/v1/house/voice":
                return self._json(200, hv.read_state())
            return self._json(404, {})

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            obj = json.loads(self.rfile.read(n) or b"{}")
            seen.append((self.path, obj))
            if self.path == "/panel/voice":
                st = hv.write_state("on" if obj.get("on") else "off", on_by=str(obj.get("on_by") or "user"))
                return self._json(200, dict(st, ok=True, voice_on=st["voice"] == "on"))
            return self._json(404, {})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CRISSA_HOME", str(home))
    monkeypatch.setenv("CRISSA_PORT_STT", str(srv.server_address[1]))
    monkeypatch.delenv("HOUSE_VOICE_LOCK", raising=False)
    monkeypatch.delenv("CRISSA_PORT_OFFSET", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    zi.configure(base_url="http://conductor.invalid", home=home)
    yield {"home": home, "seen": seen, "lock": home / "house-voice.json", "port": srv.server_address[1]}
    srv.shutdown()
    zi.configure()


def _done_state() -> dict:
    return {"status": "done", "register": {"state": "done", "display": "Sam", "voice": 2},
            "face": {"state": "no_camera"}}


# ---- Init's done plate ------------------------------------------------------------------------------------------
def test_the_done_plate_says_the_microphone_is_off_and_never_that_she_is_listening(ear):
    assert not ear["lock"].exists()
    d = zi.done_view(_done_state())
    assert d["mic"] == {"on": False}
    assert d["mic_line"] == "Microphone: off. She cannot hear you until you turn it on."
    assert "listening" not in d["body"], d["body"]
    seq = json.loads((ROOT / "config" / "init-sequence-pack.json").read_text(encoding="utf-8"))
    assert "listening" not in json.dumps(seq["done"]), "the walk's own done text must not claim she hears you"


def test_the_done_plates_key_turns_the_microphone_on_through_the_ear(ear):
    got = zi.apply("mic", {"on": True})
    assert got["ok"] is True and got["mic"] == {"on": True}
    assert ear["seen"][-1] == ("/panel/voice", {"on": True, "on_by": "init"})
    assert json.loads(ear["lock"].read_text())["voice"] == "on", "the ear's own lock, the one it reads"
    d = zi.done_view(_done_state())
    assert d["mic"] == {"on": True} and d["mic_line"].startswith("Microphone: on.")
    got = zi.apply("mic", {"on": False})
    assert got["ok"] is True and zi.mic_state() == {"on": False}


def test_with_no_ear_answering_the_key_says_so_and_changes_nothing(ear, monkeypatch):
    monkeypatch.setenv("CRISSA_PORT_STT", "9")          # nothing listens there
    got = zi.apply("mic", {"on": True})
    assert got["ok"] is False and "ear is not answering" in got["error"] and got["mic"] == {"on": False}
    assert not ear["lock"].exists()


def test_the_init_page_draws_the_microphone_on_the_done_plate():
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    plate = page[page.index("function donePlate(snap)"):page.index("function idlePlate(snap)")]
    assert 'id: "mic-on"' in plate and 'text: "Turn on the microphone"' in plate
    assert 'act("mic", { on: true })' in plate and 'id: "mic-state"' in plate
    assert "((snap.done || {}).mic || {}).on" in page, "the plate redraws when the microphone changes"


# ---- the Control Panel's Mic key ----------------------------------------------------------------------------------
def _panel(monkeypatch):
    sys.path.insert(0, str(ROOT / "lib"))
    spec = importlib.util.spec_from_file_location("crissa_panel_server_for_mic", ROOT / "scripts" / "crissa-panel-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_control_panel_has_the_mic_key_and_it_reads_and_sets_the_ears_lock(ear, monkeypatch):
    P = _panel(monkeypatch)
    assert P.ear_url() == "http://127.0.0.1:%d" % ear["port"]
    assert P.mic_view() == {"ok": True, "on": False, "ear": True, "since": None}
    code, out = P.mic_set(True)
    assert code == 200 and out == {"ok": True, "on": True, "ear": True}
    assert ear["seen"][-1] == ("/panel/voice", {"on": True, "on_by": "user"}), "the feed panel's own request"
    assert P.mic_view()["on"] is True
    code, out = P.mic_set(False)
    assert code == 200 and out["on"] is False and P.mic_view()["on"] is False


def test_the_control_panel_says_when_the_ear_is_not_answering(ear, monkeypatch):
    P = _panel(monkeypatch)
    monkeypatch.setenv("CRISSA_PORT_STT", "9")
    v = P.mic_view()
    assert v["on"] is False and v["ear"] is False and "not answering" in v["why"]
    code, out = P.mic_set(True)
    assert code == 503 and out["ok"] is False


def test_the_ears_address_follows_the_port_table():
    P = _panel(None)
    assert P.ear_url({}) == "http://127.0.0.1:8190"
    assert P.ear_url({"CRISSA_PORT_OFFSET": "10000"}) == "http://127.0.0.1:18190"
    assert P.ear_url({"CRISSA_PORT_STT": "8290"}) == "http://127.0.0.1:8290"
    import crissa_ports
    assert crissa_ports.DEFAULTS["stt"] == 8190


def test_the_panel_page_carries_the_feed_panels_mic_key():
    page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert '<button type="button" class="key small" id="bVoice" data-act="voice" aria-pressed="false"' in page
    assert 'fetch("/v1/mic",{method:"POST"' in page and 'j("/v1/mic")' in page
    assert '"Mic on — click to turn off" : "Mic off — click to turn on"' in page
    feed = kit("display/voiceonly-panel.html").read_text(encoding="utf-8")
    assert 'id="bVoice" data-act="voice"' in feed, "the key it copies"
    srv = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    assert 'if path == "/v1/mic":\n                return self._json(200, mic_view())' in srv
    assert 'if path == "/v1/mic":\n                code, out = mic_set(bool(body.get("on")))' in srv
