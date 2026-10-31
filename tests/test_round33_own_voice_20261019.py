"""Round 33, 2026-10-19 (.101: "FACE FALLBACK: crissa - crissa's Pocket voice 'crissa' is not an allowed stock voice",
"faces: crissa: Pocket's eve", while Pocket spoke her lines as crissa): Setup's face check names the voice Pocket loads
for her face, and her own shipped voice is not a fallback. Pocket's reply names the voice it loads, her style included."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "voice"))
import crissa_voice_tiers as VT  # noqa: E402
import crissa_tts as T  # noqa: E402
from crissa_voice import pocket_setup as PS  # noqa: E402


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.delenv("CRISSA_HER_VOICE", raising=False)
    monkeypatch.delenv("CRISSA_OWN_VOICE_REPORT", raising=False)
    monkeypatch.setattr(VT, "_home", lambda h=None: Path(h) if h else tmp_path)
    monkeypatch.setattr(T, "_tiers_table", lambda: VT.table(tmp_path))
    return tmp_path


def _her(face="crissa"):
    return VT._plan_face("pocket", face, "en")


def test_her_own_voice_is_not_a_fallback(home, monkeypatch):
    monkeypatch.setattr(PS, "own_voice_ok", lambda n: True)
    assert _her() == {"voice": "Pocket's crissa", "fallback": ""}
    assert _her("crissa") == {"voice": "Pocket's crissa", "fallback": ""}
    fc = VT.face_check({"main": "pocket", "announcer_chain": ["pocket"]}, live=False)
    assert not any("FACE FALLBACK: crissa" in l for l in fc["say"])


def test_the_uk_style_is_named_too(home, monkeypatch):
    monkeypatch.setattr(PS, "own_voice_ok", lambda n: True)
    VT.set_her_voice("english_accent", home)
    assert _her() == {"voice": "Pocket's azelma", "fallback": ""}


def test_a_missing_or_altered_file_is_still_a_fallback(home, monkeypatch):
    monkeypatch.setattr(PS, "own_voice_ok", lambda n: False)
    got = _her()
    assert got["voice"] == "Pocket's eve" and "not an allowed stock voice" in got["fallback"]


def test_the_kill_is_the_stock_only_check(home, monkeypatch):
    monkeypatch.setattr(PS, "own_voice_ok", lambda n: True)
    monkeypatch.setenv("CRISSA_OWN_VOICE_REPORT", "0")
    assert "not an allowed stock voice" in _her()["fallback"]


def test_pockets_reply_names_the_voice_it_loads():
    src = (ROOT / "modules/voice/scripts/crissa-pocket.py").read_text()
    blk = src.split("stock = S.voice_for(voice, vmap", 1)[0].rsplit("\n\n", 1)[-1]
    assert "_her_style_map(voice, RT[\"settings\"].get(\"voices\")) if own" in blk
    assert "CRISSA_OWN_VOICE_REPORT" in blk
