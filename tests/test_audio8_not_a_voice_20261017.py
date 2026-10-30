"""Audio8 is her ear, never a voice (the User 2026-10-17, round 27 on .101: Setup picked Audio8 for her voice though its
own speed test failed it - rtf 3.04, first audio 4.57 s - "slower and worse sounding"; Pocket "remarkably good" and
quicker). In a pack Audio8 never speaks: not her voice, her backup or the announcer, from the table, a saved pick, a
setting or Setup's ladder. Speech in (Edge0's ARK-ASR, modules/voice/scripts/crissa-asr.py) is a different program
and is untouched. Kill CRISSA_AUDIO8_TTS=1; the house is unchanged."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_voice_tiers as VT  # noqa: E402

# .101: 16 threads, 90 GB, no NVIDIA GPU - the machine Setup gave Audio8
FACTS101 = {"os": "linux", "arch": "x86_64", "cpu_cores": 16, "ram_gb": 90.8, "python": "3.12", "gpu": {"kind": "none"},
            "language": {"code": "en", "name": "English", "from": "Init (your answer)"}, "piper_model": False}


@pytest.fixture(autouse=True)
def _pack(monkeypatch):
    monkeypatch.setattr(VT, "is_house", lambda: False)
    monkeypatch.delenv("CRISSA_AUDIO8_TTS", raising=False)


def _no_audio8(rec):
    names = [rec.get("main"), rec.get("announcer")] + list(rec.get("chain") or []) + list(rec.get("announcer_chain") or [])
    return "audio8" not in names


def test_a_robust_cpu_machine_speaks_and_announces_with_pocket(tmp_path):
    rec = VT.pick(FACTS101, VT.table(home=tmp_path))
    assert rec["main"] == "pocket" and rec["chain"][0] == "pocket"
    assert rec["announcer_chain"][0] == "pocket"
    assert _no_audio8(rec)


def test_a_state_home_copy_cannot_put_audio8_back(tmp_path):
    (tmp_path / "crissa-voice-tiers.json").write_text(json.dumps(
        {"order": ["audio8", "pocket", "text"], "announcer": {"order": ["audio8", "pocket", "text"]}}))
    tab = VT.table(home=tmp_path)
    assert "audio8" not in tab["order"] and "audio8" not in tab["announcer"]["order"]


def test_a_saved_audio8_pick_is_made_again_without_it_even_the_persons_own(tmp_path, monkeypatch):
    monkeypatch.setattr(VT, "broken", lambda: {})
    monkeypatch.setattr(VT, "face_check", lambda rec, speak=None, live=None: {})
    monkeypatch.setattr(VT, "missing_line", lambda rec: "")
    old = {"schema": VT.SCHEMA, "main": "audio8", "tier": "audio8", "chain": ["audio8", "pocket", "text"],
           "announcer": "", "announcer_chain": ["audio8", "pocket", "text"], "picked_by": "user", "facts": FACTS101}
    VT._write(old, tmp_path)
    rec = VT.ensure(tmp_path, facts=FACTS101)
    assert rec["main"] == "pocket" and _no_audio8(rec)
    assert (tmp_path / VT.PREV_NAME).is_file()                      # the old pick is kept beside it


def test_the_runtime_never_reads_audio8_from_an_old_record():
    rec = {"main": "audio8", "tier": "audio8", "chain": ["audio8", "pocket", "kokoro", "text"],
           "announcer": "audio8", "announcer_chain": ["audio8", "pocket", "text"]}
    got = VT.clean_record(rec)
    assert got["main"] == "pocket" and got["chain"] == ["pocket", "kokoro", "text"]
    assert got["announcer_chain"] == ["pocket", "text"] and got["announcer"] == ""
    assert VT.chain(rec) == ["pocket", "kokoro", "text"]


def test_the_person_cannot_pick_audio8_as_her_voice(tmp_path):
    with pytest.raises(ValueError, match="not a voice"):
        VT.override("audio8", tmp_path)


def test_setups_ladder_never_times_audio8():
    import crissa_machine as MC
    got = MC.ladder(installed=lambda e: True, facts=FACTS101)
    assert "audio8" not in got and "pocket" in got


def test_the_voice_seam_never_speaks_with_audio8(monkeypatch, tmp_path):
    import crissa_tts as T
    monkeypatch.setattr(T, "_house", lambda: False)
    assert "audio8" not in T.with_installed(["pocket", "audio8", "kokoro"], "pocket", env={}, installed=lambda n: True)
    with pytest.raises(T.TTSError, match="not a voice"):
        T.synth("hello", "crissa", {"engine": "audio8"})


def test_the_switch_brings_the_old_order_back(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_AUDIO8_TTS", "1")
    rec = VT.pick(FACTS101, VT.table(home=tmp_path))
    assert rec["main"] == "audio8" and rec["announcer_chain"][0] == "audio8"


def test_the_house_is_unchanged(monkeypatch):
    monkeypatch.setattr(VT, "is_house", lambda: True)
    assert not VT.not_a_voice("audio8")


def test_speech_in_is_a_different_program_and_stays():
    asr = (ROOT / "modules/voice/scripts/crissa-asr.py").read_text()
    assert "ark-asr" in asr.lower() and "audio8" not in asr.lower()
