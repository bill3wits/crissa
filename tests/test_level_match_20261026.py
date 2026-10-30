"""Her voice at this computer's own loudness (the User 2026-10-26: "Levels have been way off between system sounds and
speech. Could we just match their system sound level and do a comparison in the init sequence?"). lib/crissa_level:
Setup's own-voice step compares this computer's alert sound with her lines as the microphone heard them and house-speak
adds the offset to her target in a pack. Kill CRISSA_LEVEL_MATCH=0; never on the house."""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_level as ZL  # noqa: E402

SR = 16000


def _tone_wav(path: Path, level_dbfs: float, seconds: float = 1.0, floor_dbfs: float = -60.0, gap_s: float = 0.5) -> Path:
    """gap_s of room floor, `seconds` of a tone at level_dbfs (RMS), gap_s of floor again - the shape of a recorded sound."""
    import random
    rnd = random.Random(7)
    amp = 10 ** (level_dbfs / 20) * math.sqrt(2) * 32768
    fl = 10 ** (floor_dbfs / 20) * 32768
    frames = []
    for i in range(int((2 * gap_s + seconds) * SR)):
        t = i / SR
        v = rnd.gauss(0, fl)
        if gap_s <= t < gap_s + seconds:
            v += amp * math.sin(2 * math.pi * 440 * t)
        frames.append(max(-32767, min(32767, int(v))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % len(frames), *frames))
    return path


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.delenv("CRISSA_LEVEL_MATCH", raising=False)
    monkeypatch.delenv("CRISSA_LEVEL_MATCH_SOUND", raising=False)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)


def test_the_alert_louder_than_her_lines_is_a_positive_offset(tmp_path, pack):
    alert = _tone_wav(tmp_path / "alert.wav", -20.0)
    lines = [_tone_wav(tmp_path / "l1.wav", -26.0, 2.0), _tone_wav(tmp_path / "l2.wav", -26.0, 2.0)]
    rec = ZL.measure(alert, lines)
    assert rec["outcome"] == "measured"
    assert rec["offset_db"] == pytest.approx(6.0, abs=0.6), rec
    assert rec["alert_db"] > rec["line_db"] > rec["floor_db"]
    ZL.write(tmp_path, rec)
    assert ZL.offset_db(tmp_path) == pytest.approx(rec["offset_db"])
    assert "louder" in ZL.said(rec)


def test_the_offset_is_clamped_both_ways(tmp_path, pack):
    rec = ZL.measure(_tone_wav(tmp_path / "a.wav", -10.0), [_tone_wav(tmp_path / "l.wav", -30.0, 2.0)])
    assert rec["outcome"] == "measured" and rec["offset_db"] == ZL.CAP_DB and rec["raw_db"] > ZL.CAP_DB
    rec2 = ZL.measure(_tone_wav(tmp_path / "a2.wav", -40.0), [_tone_wav(tmp_path / "l2.wav", -20.0, 2.0)])
    assert rec2["offset_db"] == -ZL.CAP_DB and "softer" in ZL.said(rec2)
    ZL.write(tmp_path, {"outcome": "measured", "offset_db": 40.0})
    assert ZL.offset_db(tmp_path) == ZL.CAP_DB


def test_too_close_to_the_floor_changes_nothing_and_says_so(tmp_path, pack):
    # the User's desk 2026-10-26: both sounds 4-7 dB over the floor at the microphone (speakers at 41 %)
    alert = _tone_wav(tmp_path / "alert.wav", -55.0, floor_dbfs=-60.0)
    lines = [_tone_wav(tmp_path / "l.wav", -56.0, 2.0, floor_dbfs=-60.0)]
    rec = ZL.measure(alert, lines)
    assert rec["outcome"] == "quiet", rec
    assert rec["offset_db"] == 0.0 and "turn the speakers up" in ZL.said(rec)
    ZL.write(tmp_path, rec)
    assert ZL.offset_db(tmp_path) == 0.0


def test_no_alert_sound_and_no_lines_are_named_not_measured(tmp_path, pack):
    assert ZL.measure(None, [tmp_path / "x.wav"])["outcome"] == "no_sound"
    assert ZL.measure(_tone_wav(tmp_path / "a.wav", -20.0), [])["outcome"] == "no_lines"
    assert ZL.measure_dbs(None, [], lines=0)["outcome"] == "no_sound"
    assert ZL.offset_db(tmp_path) == 0.0            # no file at all
    assert ZL.said({}) == ""


def test_off_switch_and_the_house_add_nothing(tmp_path, monkeypatch):
    import crissa_home
    ZL.write(tmp_path, {"outcome": "measured", "offset_db": 5.0})
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setenv("CRISSA_LEVEL_MATCH", "0")
    assert ZL.offset_db(tmp_path) == 0.0 and not ZL.on()
    monkeypatch.delenv("CRISSA_LEVEL_MATCH")
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert ZL.offset_db(tmp_path) == 0.0 and not ZL.on()
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert ZL.offset_db(tmp_path) == 5.0


def test_the_system_sound_is_this_computers_or_the_named_one(tmp_path, monkeypatch):
    snd = tmp_path / "mine.ogg"
    snd.write_bytes(b"x")
    assert ZL.system_sound({"CRISSA_LEVEL_MATCH_SOUND": str(snd)}) == snd
    monkeypatch.setattr(ZL, "SYSTEM_SOUNDS", (str(tmp_path / "none.oga"),))
    assert ZL.system_sound({}) is None


def test_the_wiring_is_in_place():
    """house-speak adds the offset to the target in a pack; Setup's step reads her takes and hears the alert; the done
    card says it (source pins: the three call sites)."""
    sp = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
    assert "return target + level_match_offset(), cut" in sp
    assert "crissa_level.offset_db(_crissa_home())" in sp and "if _HOUSE:\n        return 0.0" in sp
    mp = (ROOT / "modules" / "id" / "crissa_id" / "house_enroll" / "mouth_prints.py").read_text(encoding="utf-8")
    assert mp.count("_level_note(wav, speaker)") == 2            # the plain road and the player road
    assert 'out["level"] = _level_check(site' in mp and "LEVEL_WINDOWS.clear()" in mp
    init = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert "crissa_level.said(crissa_level.read(_home()))" in init


def test_house_speak_target_takes_the_offset_in_a_pack(tmp_path, monkeypatch):
    """play_target_for through the real module: the measured offset moves her target; the house's stays."""
    import importlib.util
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    monkeypatch.delenv("CRISSA_LEVEL_MATCH", raising=False)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    ZL.write(tmp_path, {"outcome": "measured", "offset_db": 4.0})
    spec = importlib.util.spec_from_file_location("hs_level_test", ROOT / "scripts" / "house-speak.py")
    hs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hs)
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(hs, "mic_live", lambda *a, **k: False)
    base = hs.play_target_for("crissa")[0] - hs.level_match_offset()
    assert hs.level_match_offset() == 4.0
    assert hs.play_target_for("crissa")[0] == pytest.approx(base + 4.0)
    monkeypatch.setattr(hs, "_HOUSE", True)
    assert hs.level_match_offset() == 0.0
