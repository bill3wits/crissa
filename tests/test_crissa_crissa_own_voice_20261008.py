"""Crissa's own voice ships (the User 2026-10-08).

Her reference clip is cut from the sound of two of her own Grok Imagine films (xAI output; credit "Created with
Grok"); it is no real person's voice. It ships in the pack (modules/voice/faces/crissa-imagine.wav, with its transcript
and provenance), pinned by sha256. The voices that clone speak her with it: Chatterbox-Turbo and Multilingual
(faces.py) and Audio8 (registered as crissa_imagine). Pocket cannot clone: she stays eve there (the User 2026-10-09). jane is her backup
everywhere her own clip or voice is missing, and the log says so. Lila kept her stock voice (eve) until 2026-10-12,
when her own Imagine-sourced clip shipped too (tests/test_crissa_lila_own_voice_20261012.py). No other clip ships:
the export lets those files through by path and sha256 only.
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

CLIP = VOICE / "faces" / "crissa-imagine.wav"
SHA = "bbf638c07031c14ed842351ebb434f96fa078c2b5fe1ef846f0f61e9d907d8fc"


def _sidecar() -> dict:
    return json.loads((VOICE / "faces" / "crissa-imagine.json").read_text(encoding="utf-8"))


def test_her_clip_ships_pinned_with_its_transcript_and_provenance():
    assert hashlib.sha256(CLIP.read_bytes()).hexdigest() == SHA == F.STOCK["crissa"]["sha256"]
    assert CLIP.stat().st_size == F.STOCK["crissa"]["bytes"]
    with wave.open(str(CLIP)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 24000, 2)
        assert 5.0 <= w.getnframes() / w.getframerate() <= 15.0, "5 to 15 s, what the cloning voices take"
    side = _sidecar()
    assert side["sha256"] == SHA and side["credit"] == "Created with Grok" and "Grok Imagine" in side["generator"]
    assert side["transcript"] == (VOICE / "faces" / "crissa-imagine.txt").read_text(encoding="utf-8").strip()
    assert side["transcript"] == F.transcript("crissa")
    assert len(side["sources"]) == 2 and all(len(s["source_sha256"]) == 64 and s["prompt"] for s in side["sources"])
    assert "no real person" in side["_about"].lower() or "not a recording of a person" in side["_about"].lower() \
        or "nothing in it is a recording of a person" in side["_about"]


def test_the_credits_and_provenance_name_it():
    credits = next(p for p in (ROOT / "CREDITS.md", ROOT / "config" / "crissa-licences" / "CREDITS.md") if p.is_file())
    text = credits.read_text(encoding="utf-8")
    assert "crissa-imagine.wav" in text and "Created with Grok" in text
    for prov in (ROOT / "FILM-PROVENANCE.json", ROOT / "config" / "crissa-film-provenance.json"):
        if prov.is_file():                 # the films' provenance carries hers too (a code-only pack has none)
            v = json.loads(prov.read_text(encoding="utf-8"))["voices"]["modules/voice/faces/crissa-imagine.wav"]
            assert v["sha256"] == SHA and v["credit"] == "Created with Grok"


def test_the_cloning_voices_speak_her_with_her_own_clip(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    assert F.present("crissa") and F.clips(None)["crissa"] == CLIP and F.clips(None)["default"] == CLIP
    p = F.plan(None, "built-in")
    assert p["crissa"]["source"] == str(CLIP) and not p["crissa"]["fallback"]
    assert "Created with Grok" in p["crissa"]["voice"]
    assert p["lila"]["source"] == str(VOICE / "faces" / "lila-imagine.wav"), "Lila's own clip ships too (2026-10-12)"
    fetched = []

    def fetch(url, dest):
        fetched.append(url)
        raise OSError("offline")
    got = F.fetch_all(fetch, log=lambda *_: None)
    assert got["crissa"] == "", "her own clip is checked in the pack, never fetched"
    assert not any("crissa" in u for u in fetched)
    assert any(u.endswith(F.EXTRA["jane"]["file"]) for u in fetched), "her backup clip (jane) is fetched too"
    assert CLIP.is_file(), "a failed check never removes the pack's own clip"


def test_without_her_clip_she_speaks_from_janes_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.setattr(F, "PACK_ROOT", tmp_path / "no-pack")
    jane = F.path("jane")
    jane.parent.mkdir(parents=True)
    jane.write_bytes(b"\0" * F.EXTRA["jane"]["bytes"])
    assert not F.present("crissa") and F.clips(None)["crissa"] == jane
    p = F.plan(None, "built-in")["crissa"]
    assert p["source"] == str(jane) and "own clip is not at" in p["fallback"]


def test_audio8_registers_her_own_voice_and_jane_as_her_backup(monkeypatch, tmp_path):
    clip, text, why = A8.voice_source("crissa_imagine")
    assert why == "" and clip == CLIP and text == _sidecar()["transcript"]
    assert A8.FACE_VOICES == {"lila": "lila_imagine", "crissa": "crissa_imagine"}
    assert A8.FACE_BACKUP == {"crissa": "jane", "lila": "eve"}
    assert A8.PACK_VOICES["crissa_imagine"]["face"] == "crissa"
    src = (VOICE / "crissa_voice" / "audio8_setup.py").read_text(encoding="utf-8")
    assert "VoiceRegistration" in src, "Audio8's own runtime registers it (Apache-2.0), like the VCTK voices"


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


def test_audio8_speaks_her_own_voice_and_falls_back_to_jane(monkeypatch, capsys):
    asked = []

    def fake(req, timeout, why):
        v = json.loads(req.data)["voice_name"]
        asked.append(v)
        if v == "crissa_imagine" and missing:
            raise urllib.error.HTTPError(req.full_url, 404, "no such voice", {}, None)
        return _R(_wav())
    monkeypatch.setattr(T.crissa_egress, "urlopen", fake)
    missing = False
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "crissa", 5)
    assert asked == ["crissa_imagine"]
    assert "voice: audio8 speaks this line as crissa_imagine (for crissa)" in capsys.readouterr().err
    missing, asked[:] = True, []
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "crissa", 5)
    assert asked == ["crissa_imagine", "jane"]
    assert "speaks this line as jane (for crissa)" in capsys.readouterr().err
    asked[:] = []
    T._audio8("Hello.", {"url": "http://127.0.0.1:1"}, "lila", 5)
    assert asked == ["lila_imagine"], "Lila speaks her own voice (2026-10-12)"


def test_pocket_is_azelma_for_crissa_and_eve_for_lila():
    # the User 2026-10-11 (the Pocket voice page): "azelma is crissa. eve can stay as lila"
    assert T.face_default("pocket", "crissa") == "crissa" and T.face_default("pocket", "lila") == "eve"
    assert T.face_default("audio8", "crissa") == "crissa_imagine" and T.face_default("audio8", "lila") == "lila_imagine"
    assert "crissa_imagine" in T.AUDIO8_WOMEN and T.AUDIO8_BACKUP == {"crissa": "jane", "lila": "eve"}


def test_the_export_lets_only_her_clip_through():
    x = ROOT / "scripts" / "crissa-export.py"
    if not x.is_file():
        pytest.skip("the exporter is not in this tree")
    src = x.read_text(encoding="utf-8")
    assert '"modules/voice/faces/crissa-imagine.wav":' in src and SHA in src
    assert '(".wav", ".flac", ".mp3", ".ogg", ".m4a", ".onnx", ".pt", ".npy", ".safetensors")' in src
