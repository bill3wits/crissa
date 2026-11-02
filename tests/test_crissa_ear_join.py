"""The ear's barge-in, follow-up window, wake variants, body cues and settings (2026-10-06).
A turns feed and a player, both stand-ins on loopback; time is passed in, so nothing sleeps."""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("crissa_ear_join", ROOT / "scripts" / "crissa-ear.py")
EAR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EAR)


class World:
    def __init__(self):
        self.turns: list[dict] = []
        self.on_air = False
        self.busy = 0
        self.last = None
        self.posts: list[tuple[str, dict]] = []
        self.stop_route = True

    def asked(self):
        return [b["text"] for p, b in self.posts if p == "/v1/ask"]

    def paths(self):
        return [p for p, _ in self.posts]


@pytest.fixture()
def world():
    w = World()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.startswith("/v1/turns"):
                return self._send(200, {"turns": list(w.turns)})
            if self.path == "/v1/state":
                return self._send(200, {"on_air": w.on_air})
            if self.path == "/v1/agent":
                return self._send(200, {"ok": True, "busy": w.busy, "last": w.last})
            self._send(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path == "/v1/stop" and not w.stop_route:
                return self._send(404, {"ok": False})
            w.posts.append((self.path, body))
            if self.path in ("/v1/stop", "/v1/say"):
                w.on_air = False
            self._send(202 if self.path == "/v1/ask" else 200, {"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    w.url = "http://127.0.0.1:%d" % srv.server_address[1]
    yield w
    srv.shutdown()


def _ear(w, **kw):
    logs: list[str] = []
    return EAR.Ear(w.url + "/v1/turns", w.url, log=logs.append, **kw), logs


def test_naming_her_first_while_she_speaks_stops_her_and_asks(world):
    ear, logs = _ear(world, wake=["crissa"], barge=True)
    ear.seed()
    world.on_air = True
    world.turns.append({"ts": 10.0, "text": "Crissa stop, what about tomorrow", "her_voice": True})
    ear.poll(now=100.0)
    assert world.paths() == ["/v1/stop"], "the stop goes first"
    ear.poll(now=102.0)
    assert world.asked() == ["Crissa stop, what about tomorrow"]
    assert any("barge-in" in ln for ln in logs)


def test_an_older_player_is_stopped_with_say_stop(world):
    world.stop_route = False
    ear, _ = _ear(world, wake=["crissa"], barge=True)
    ear.seed()
    world.on_air = True
    world.turns.append({"ts": 10.0, "text": "crissa, wait"})
    ear.poll(now=100.0)
    assert world.posts[0] == ("/v1/say", {"stop": True, "who": "ear"})


def test_her_echo_and_a_name_mid_sentence_are_not_a_barge_in(world):
    ear, logs = _ear(world, wake=["crissa"], barge=True)
    ear.seed()
    world.on_air = True
    world.last = {"reply": "Crissa here, the weather is sunny and warm today."}
    world.turns.append({"ts": 10.0, "text": "crissa here the weather is sunny"})
    world.turns.append({"ts": 11.0, "text": "I told my friend about crissa", "her_voice": True})
    ear.poll(now=100.0)
    ear.poll(now=105.0)
    assert world.posts == [] and any("dropped" in ln for ln in logs)


def test_barge_off_never_stops_her(world):
    ear, _ = _ear(world, wake=["crissa"], barge=False)
    ear.seed()
    world.on_air = True
    world.turns.append({"ts": 10.0, "text": "crissa stop"})
    ear.poll(now=100.0)
    assert world.posts == []


def test_a_variant_wakes_her(world):
    ear, _ = _ear(world, wake=["crissa"], variants=["joey", "zoe"])
    ear.seed()
    world.turns.append({"ts": 10.0, "text": "joey what time is it"})
    ear.poll(now=100.0)
    ear.poll(now=102.0)
    assert world.asked() == ["joey what time is it"]


def test_a_follow_up_needs_no_wake_word_until_the_window_closes(world):
    ear, logs = _ear(world, wake=["crissa"], follow_s=20.0, tail_s=1.0, body_cues=True)
    ear.seed()
    world.turns.append({"ts": 10.0, "text": "crissa what is the weather"})
    ear.poll(now=100.0)
    ear.poll(now=102.0)
    assert world.paths() == ["/v1/vocative", "/v1/comms", "/v1/ask"]
    assert world.posts[1][1]["on"] is True
    world.busy = 1                                   # she is thinking, then speaking
    ear.poll(now=103.0)
    world.busy, world.on_air = 0, True
    ear.poll(now=110.0)
    world.on_air = False
    ear.poll(now=115.0)                              # her line ended ~111: the window runs to ~131
    world.turns.append({"ts": 11.0, "text": "and tomorrow"})
    ear.poll(now=125.0)
    ear.poll(now=127.0)
    assert world.asked() == ["crissa what is the weather", "and tomorrow"]
    assert world.paths().count("/v1/vocative") == 1, "a follow-up is not a new call of her name"
    world.on_air = False
    ear.poll(now=200.0)                              # long after: the window is closed
    assert world.posts[-1] == ("/v1/comms", {"on": False, "why": "follow-up window over", "by": "ear"})
    world.turns.append({"ts": 12.0, "text": "unrelated chatter"})
    ear.poll(now=210.0)
    ear.poll(now=212.0)
    assert world.asked()[-1] == "and tomorrow" and any("no wake word" in ln for ln in logs)


def test_body_cues_off_by_default_in_the_class(world):
    ear, _ = _ear(world, wake=["crissa"], follow_s=20.0)
    ear.seed()
    world.turns.append({"ts": 10.0, "text": "crissa hello"})
    ear.poll(now=100.0)
    ear.poll(now=102.0)
    assert world.paths() == ["/v1/ask"]


def test_settings_come_from_talk_then_env(tmp_path, monkeypatch):
    monkeypatch.setattr(EAR.crissa_talk, "HOME", tmp_path)
    (tmp_path / "talk.json").write_text(json.dumps({"ear": {"wake": ["crissa"], "follow_s": 9}}))
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    s = EAR.settings(env={"CRISSA_EAR_VARIANTS": "zoe, joey", "CRISSA_EAR_BARGE": "0"})
    assert s["variants"] == ["zoe", "joey"] and s["barge"] is False
    assert s["gather_s"] == 1.2 and s["tail_s"] == 1.5


def test_wake_first_and_echo_helpers():
    assert EAR.wake_first("Hey Crissa, stop", ["crissa"]) is True
    assert EAR.wake_first("I was telling everyone about crissa", ["crissa"]) is False
    assert EAR.echo_of("the weather is sunny", "The weather is sunny and warm.") is True
    assert EAR.echo_of("crissa what about tomorrow", "The weather is sunny and warm.") is False
