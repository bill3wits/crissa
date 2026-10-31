"""The live lap on a pack (2026-10-27, cut 9 on +150: 1 of 19 beats played; the gaps for Wednesday's cut 10).

1. A `listen` cue is her listen loop: the worn library's declared one on a pack (Crissa desk v2: v2-listen-screen),
   `listen` on the house.
2. A `house_only` beat (the House Daily) is left out of a pack's lap.
3/4. A synth or render beat on an install without lib/demo_synth plays its fallback say beat (the recorded line).
5. The pack's lap has a player and an announcer (its own Pocket, a stock voice, every line led with ", ").
6. A flourish on the glass at the start: the lap cues her listen loop and waits a dial's worth, once.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import demo_hold  # noqa: E402
import xspaces_live as xl  # noqa: E402
import crissa_home  # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(xl, "OFF_FILE", tmp_path / "off")
    monkeypatch.setattr(xl, "ACTIVE_FILE", tmp_path / "active")
    monkeypatch.setattr(xl, "HOME", tmp_path)
    monkeypatch.setattr(demo_hold, "HOLD_FILE", tmp_path / "demo-hold")
    monkeypatch.setattr(demo_hold, "DEV_FILE", tmp_path / "demo-dev.on")
    monkeypatch.setattr(demo_hold, "DRILL_DIR", tmp_path / "drill")
    monkeypatch.setattr(demo_hold, "ROUNDS_LOG", tmp_path / "rounds.jsonl")
    monkeypatch.setattr(xl, "_show_off", lambda: None)
    monkeypatch.delenv("CRISSA_DEMO_SETS", raising=False)
    for k in ("CRISSA_DEMO_LISTEN_KEY", "CRISSA_DEMO_HOUSE_ONLY", "CRISSA_LISTEN_LOOP"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


@pytest.fixture
def v2_library(tmp_path, monkeypatch):
    d = tmp_path / "libraries" / "crissa-desk-v2"
    d.mkdir(parents=True)
    (d / "library.json").write_text(json.dumps({"name": "Crissa desk v2", "listen_loop": "v2-listen-screen"}))
    monkeypatch.setenv("HOUSE_POSE_DIR", str(d))
    return d


def _room(**kw):
    base = dict(sleep=lambda s: None, hold_start=lambda: "", log=lambda *a: None,
                cue=lambda key, ttl: {"taken": True, "why": ""},
                zoom_get=lambda: (False, 1.0), zoom_set=lambda hole, fov: None,
                shift_set=lambda origin: None, shift_live=lambda: False,
                say=lambda text: None, play=lambda pcm, rate: (True, "sink"),
                post=lambda pcm, rate: None, wait_reply=lambda timeout: ("done", 900),
                pull_controls=lambda: [], take=lambda take_id: b"\x00\x01" * 800,
                ear=lambda: {}, hands=lambda: False, on_air=lambda: False,
                speak_crissa=lambda text: {"ok": True}, show_note=lambda title, text: None)
    base.update(kw)
    return xl.Room(**base)


def _cfg():
    return {"opener": "Live lap.", "home": "listen", "hold_max_s": 1,
            "beats": [{"id": "open", "kind": "show", "cue": "listen", "watch_s": 0},
                      {"id": "daily", "kind": "pull", "house_only": True, "ask": "Show me the House Daily."},
                      {"id": "q-lookup", "kind": "synth", "beat": "lookup",
                       "fallback": {"id": "what-this-is", "kind": "say", "take": "what-this-is", "say": "Crissa, say.",
                                    "expect": {"answer": ["done"]}}},
                      {"id": "chat-1", "kind": "chat"},
                      {"id": "to-listen", "kind": "home", "cue": "listen", "watch_s": 0}]}


def _pack(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)


# 1
def test_listen_is_the_libraries_own_loop_on_a_pack(isolated, v2_library, monkeypatch):
    _pack(monkeypatch)
    assert xl.listen_key() == "v2-listen-screen"
    monkeypatch.setenv("CRISSA_DEMO_LISTEN_KEY", "0")
    assert xl.listen_key() == "listen"


def test_listen_stays_listen_on_the_house_and_when_nothing_declares_one(isolated, v2_library, monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert xl.listen_key() == "listen"
    _pack(monkeypatch)
    (v2_library / "library.json").write_text(json.dumps({"name": "no loops"}))
    assert xl.listen_key() == "listen"


def test_a_pack_lap_cues_the_library_loop_on_every_listen_cue_and_home(isolated, v2_library, monkeypatch):
    _pack(monkeypatch)
    monkeypatch.setattr(xl, "_synth", lambda: None)
    cues = []
    res = xl.run_lap(_cfg(), _room(cue=lambda key, ttl: cues.append(key) or {"taken": True}),
                     out_dir=isolated / "out", broadcast=False)
    assert cues and set(cues) == {"v2-listen-screen"}
    by = {r["beat"]: r for r in res["rows"]}
    assert by["open"]["pass"] and by["to-listen"]["pass"]


# 2
def test_the_house_daily_is_not_in_a_packs_lap_and_is_on_the_house(isolated, v2_library, monkeypatch):
    monkeypatch.setattr(xl, "_synth", lambda: None)
    _pack(monkeypatch)
    res = xl.run_lap(_cfg(), _room(), out_dir=isolated / "out", broadcast=False)
    assert "daily" not in [r["beat"] for r in res["rows"]]
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert xl.plays_here({"id": "daily", "house_only": True})
    _pack(monkeypatch)
    monkeypatch.setenv("CRISSA_DEMO_HOUSE_ONLY", "0")
    assert xl.plays_here({"id": "daily", "house_only": True})


def test_the_shipped_config_marks_every_daily_house_only():
    cfg = json.loads((ROOT / "config" / "crissa-demo.json").read_text())
    dailies = [b for b in cfg["beats"] + [b for s in cfg["sets"] for b in s["beats"]] if b.get("id") == "daily"]
    assert dailies and all(b.get("house_only") is True for b in dailies)


# 3/4
def test_a_synth_beat_without_the_module_plays_its_recorded_fallback(isolated, v2_library, monkeypatch):
    _pack(monkeypatch)
    monkeypatch.setattr(xl, "_synth", lambda: None)
    played = []
    res = xl.run_lap(_cfg(), _room(play=lambda pcm, rate: played.append(rate) or (True, "sink")),
                     out_dir=isolated / "out", broadcast=False)
    row = next(r for r in res["rows"] if r.get("fallback_for") == "q-lookup")
    assert row["beat"] == "what-this-is" and row["pass"] and played
    # a beat with no fallback is still one skipped row
    chat = next(r for r in res["rows"] if r["beat"] == "chat-1")
    assert chat["skipped"] and not chat["pass"]


def test_every_shipped_synth_fallback_names_a_recordable_beat():
    cfg = json.loads((ROOT / "config" / "crissa-demo.json").read_text())
    takes = {b.get("take") for b in cfg["beats"] if b.get("kind") == "say"}
    for s in cfg["sets"]:
        for b in s["beats"]:
            fb = b.get("fallback")
            if b.get("kind") in ("synth", "render") and isinstance(fb, dict):
                assert fb.get("take") in takes, (s["id"], b["id"])


# 6
def test_a_flourish_at_the_start_is_wound_down_onto_her_listen_loop(isolated, v2_library, monkeypatch):
    _pack(monkeypatch)
    monkeypatch.setattr(xl, "_synth", lambda: None)
    glass = ["v2-fl-glance-door"] * 3
    order = []
    room = _room(flourish=lambda: glass.pop() if glass else "",
                 cue=lambda key, ttl: order.append(("cue", key)) or {"taken": True},
                 say=lambda text: order.append(("say", text)))
    xl.run_lap(_cfg(), room, out_dir=isolated / "out", broadcast=False)
    assert order[0] == ("cue", "v2-listen-screen") and order[1] == ("say", "Live lap.")


def test_the_wind_down_waits_no_longer_than_its_dial(isolated):
    slept = []
    room = _room(flourish=lambda: "stuck", sleep=lambda s: slept.append(s))
    assert xl._wind_down(room, "v2-listen-screen", 2.0) == "stuck"
    assert sum(slept) == pytest.approx(2.0)
    assert xl._wind_down(_room(flourish=lambda: "stuck"), "x", 0) == "stuck"     # 0 = off: no cue, no wait


# 5
def _script(monkeypatch):
    spec = importlib.util.spec_from_file_location("xspaces_live_script_kit", ROOT / "scripts" / "xspaces-live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "house_announce", None)
    return mod


def test_a_pack_announcer_speaks_on_its_own_pocket_led_with_a_comma(monkeypatch, tmp_path):
    mod = _script(monkeypatch)
    monkeypatch.setattr(mod, "ANNOUNCE_CACHE", tmp_path / "cache")
    monkeypatch.delenv("CRISSA_DEMO_KIT_ANNOUNCER", raising=False)
    monkeypatch.setenv("CRISSA_DEMO_ANNOUNCER_VOICE", "anna")
    sent, played = [], []

    class R:
        def __init__(self, blob):
            self.blob = blob

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.blob

    def fake_urlopen(req, timeout=0):
        sent.append((req.full_url, json.loads(req.data)))
        return R(b"RIFF" + b"\x00" * 40)
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod, "_kit_play", lambda path, text="": played.append(path) or (True, "played"))
    mod.say("Pass. 9 seconds.")
    assert sent and sent[0][0].endswith("/v1/audio/speech")
    assert sent[0][1]["input"] == ", Pass. 9 seconds." and sent[0][1]["voice"] == "anna"
    assert played
    mod.say("Pass. 9 seconds.")                      # the second time from the cache
    assert len(sent) == 1 and len(played) == 2


def test_a_pack_lap_plays_through_its_own_player_and_the_kill_is_the_old_silence(monkeypatch, tmp_path):
    mod = _script(monkeypatch)
    import crissa_audio
    calls = []
    monkeypatch.setattr(crissa_audio, "play_file", lambda path, sink=None, timeout=None: calls.append(path) or 0)
    import audio_sink
    gates = []
    monkeypatch.setattr(audio_sink, "mark_tts_gate", lambda **kw: gates.append(kw))
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"")
    monkeypatch.delenv("CRISSA_DEMO_KIT_ANNOUNCER", raising=False)
    assert mod._play_wav(str(wav), "Crissa, say what this is.") == (True, "played")
    assert calls == [str(wav)]
    assert gates[0]["speaking"] and gates[0]["last_reply"] == "Crissa, say what this is."
    assert not gates[-1]["speaking"]
    monkeypatch.setenv("CRISSA_DEMO_KIT_ANNOUNCER", "0")
    assert mod._play_wav(str(wav)) == (False, "no player")
