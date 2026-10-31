"""Lila's own voice ships (the User 2026-10-12: "I can't source Lila's current voice ... If that voice is fine from
Imagine clip swap that so I can source it properly in what ships").

Her reference clip is cut from the sound of one of the house's own Grok Imagine films: the 480p take of the drill
behind Crissa's "heads-up harvest · the first drill" (whose 720p take is that film). It is an English-accented
woman's voice that measures far from Crissa's on the shipped voice-ID model (0.16, where two different stock speakers
read 0.27-0.56), so the two faces do not sound alike. It ships beside Crissa's, pinned by sha256, with its transcript
and provenance; the voices that clone speak her with it, eve (VCTK p361) is her backup on them, and Pocket keeps eve.
The house's own Lila clip is untouched and never ships.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.error
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "modules" / "voice"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(VOICE))
import crissa_tts as T  # noqa: E402
from crissa_voice import audio8_setup as A8  # noqa: E402
from crissa_voice import faces as F  # noqa: E402

CLIP = VOICE / "faces" / "lila-imagine.wav"
SHA = "1af95b61e22ede6285fe4084e98f0b59e0ab36a077369ac09faa868ae2c6cc84"
SOURCE_480P = "665881bb042871641a891e7b79b6a9b837276348cb05e21242a1ee7e03ca2eab"


def _sidecar() -> dict:
    return json.loads((VOICE / "faces" / "lila-imagine.json").read_text(encoding="utf-8"))


def test_her_clip_ships_pinned_with_its_transcript_and_provenance():
    assert hashlib.sha256(CLIP.read_bytes()).hexdigest() == SHA == F.STOCK["lila"]["sha256"]
    assert CLIP.stat().st_size == F.STOCK["lila"]["bytes"] and F.shipped("lila") and F.present("lila")
    with wave.open(str(CLIP)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 24000, 2)
        assert 8.0 <= w.getnframes() / w.getframerate() <= 15.0, "8 to 15 s, what the cloning voices take"
    side = _sidecar()
    assert side["sha256"] == SHA and side["credit"] == "Created with Grok" and "Grok Imagine" in side["generator"]
    assert side["transcript"] == (VOICE / "faces" / "lila-imagine.txt").read_text(encoding="utf-8").strip()
    assert side["transcript"] == F.transcript("lila") and side["transcript"].endswith(".")
    assert [s["source_sha256"] for s in side["sources"]] == [SOURCE_480P]
    assert "nothing in it is a recording of a person" in side["_about"]
    assert side["measured"]["vs_crissa_clip"] < min(side["measured"]["two_different_people_stock_vctk"]), \
        "farther from Crissa's voice than two different stock speakers are from each other"


def test_the_credits_notices_and_provenance_name_it():
    lic = ROOT / "config" / "crissa-licences"
    assert "lila-imagine.wav" in (lic / "CREDITS.md").read_text(encoding="utf-8")
    assert "lila-imagine.wav" in (lic / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    prov = json.loads((ROOT / "config" / "crissa-film-provenance.json").read_text(encoding="utf-8"))
    assert prov["voices"]["modules/voice/faces/lila-imagine.wav"] == _sidecar(), "one record, two copies, equal"
    row = prov["films"]["display/crissa-poses/clip-talk-back-harvest.mp4"]
    assert row["status"] == "traced, no signature on the source" and row["segments"][0]["frames"] == [0, 241]


def test_the_cloning_voices_speak_her_with_her_own_clip_and_eve_backs_her(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    assert F.clips(None)["lila"] == CLIP
    p = F.plan(None, "built-in")["lila"]
    assert p["source"] == str(CLIP) and not p["fallback"] and "Created with Grok" in p["voice"]
    fetched = []

    def fetch(url, dest):
        fetched.append(url)
        raise OSError("offline")
    got = F.fetch_all(fetch, log=lambda *_: None)
    assert got["lila"] == "" and not any("lila" in u for u in fetched), "her own clip is checked, never fetched"
    assert any(u.endswith(F.EXTRA["eve"]["file"]) for u in fetched), "her backup clip (eve) is fetched"
    assert CLIP.is_file()


def test_without_her_clip_she_speaks_from_eves_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.setattr(F, "PACK_ROOT", tmp_path / "no-pack")
    eve = F.path("eve")
    eve.parent.mkdir(parents=True)
    eve.write_bytes(b"\0" * F.EXTRA["eve"]["bytes"])
    assert not F.present("lila") and F.clips(None)["lila"] == eve
    p = F.plan(None, "built-in")["lila"]
    assert p["source"] == str(eve) and "own clip is not at" in p["fallback"]


def test_audio8_registers_her_own_voice_and_eve_as_her_backup():
    clip, text, why = A8.voice_source("lila_imagine")
    assert why == "" and clip == CLIP and text == _sidecar()["transcript"]
    assert A8.FACE_VOICES["lila"] == "lila_imagine" and A8.FACE_BACKUP["lila"] == "eve"
    clip, text, why = A8.voice_source("eve")
    assert clip is None and "eve's clip is not in" in why, "eve is fetched at install, from its own stock entry"
    assert A8.PACK_VOICES["eve"]["clip"] == "eve" and A8.PACK_VOICES["eve"]["face"] == "lila"


def _wav(n=2400):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * n)
    return b.getvalue()


class _R:
    def __init__(self, d):
        self.d = d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.d


def test_audio8_speaks_her_own_voice_and_falls_back_to_eve(monkeypatch, capsys):
    asked, missing = [], {"on": False}

    def fake(req, timeout, why):
        v = json.loads(req.data)["voice_name"]
        asked.append(v)
        if v == "lila_imagine" and missing["on"]:
            raise urllib.error.HTTPError(req.full_url, 404, "no such voice", {}, None)
        return _R(_wav())
    monkeypatch.setattr(T.crissa_egress, "urlopen", fake)
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "lila", 5)
    assert asked == ["lila_imagine"]
    missing["on"], asked[:] = True, []
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "lila", 5)
    assert asked == ["lila_imagine", "eve"]
    assert "speaks this line as eve (for lila)" in capsys.readouterr().err
    assert "lila_imagine" in T.AUDIO8_WOMEN and T.she_voice("audio8", "lila", "lila_imagine") == "lila_imagine"


def test_pocket_keeps_eve_for_her():
    assert T.face_default("pocket", "lila") == "eve", "Pocket cannot clone (the User 2026-10-11: eve stays Lila)"


def test_the_export_and_the_voice_package_let_her_clip_through():
    x = ROOT / "scripts" / "crissa-export.py"
    if not x.is_file():
        pytest.skip("the exporter is not in this tree")
    src = x.read_text(encoding="utf-8")
    assert '"modules/voice/faces/lila-imagine.wav":' in src and SHA in src
    pk = json.loads((ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8"))
    text = json.dumps(pk)
    for f in ("lila-imagine.wav", "lila-imagine.txt", "lila-imagine.json"):
        assert "modules/voice/faces/" + f in text, f


def test_the_kill_switch_puts_eve_back_and_says_why(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.setenv("CRISSA_LILA_OWN_VOICE", "0")
    eve = F.path("eve")
    eve.parent.mkdir(parents=True)
    eve.write_bytes(b"\0" * F.EXTRA["eve"]["bytes"])
    assert not F.present("lila") and F.present("crissa") and F.clips(None)["lila"] == eve
    p = F.plan(None, "built-in")["lila"]
    assert p["source"] == str(eve) and "switched off (CRISSA_LILA_OWN_VOICE=0)" in p["fallback"]
    got = F.fetch_all(lambda url, dest: None, log=lambda *_: None)
    assert "switched off" in got["lila"] and got["crissa"] == ""
    assert "switched off" in A8.voice_source("lila_imagine")[2]
    assert CLIP.is_file(), "the switch never touches the pack's clip"
