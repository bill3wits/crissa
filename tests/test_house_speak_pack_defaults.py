"""house-speak outside the house makes no house-service call (the User 2026-10-04, the Mac scan).

house-speak goes live at a merge (the wall execs it from disk for every line), so the house side of every default
is pinned here too: in a house tree `_HOUSE` is True and every URL is the old literal.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HS = ROOT / "scripts" / "house-speak.py"
SRC = HS.read_text()


def _house_speak():
    spec = importlib.util.spec_from_file_location("house_speak_pack", HS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _boom(*a, **k):
    raise AssertionError("a house-service call was made outside the house")


def test_no_voice_cache_post_and_no_mic_poll_outside_the_house(monkeypatch):
    hs = _house_speak()
    monkeypatch.setattr(hs, "VOICE_CACHE_URL", "")
    monkeypatch.setattr(hs, "HEAR_HEALTH_URL", "")
    monkeypatch.setattr(hs.urllib.request, "urlopen", _boom)
    hs._release_voice_cache()
    assert hs.mic_live() is False


def test_no_tool_hold_outside_the_house_even_with_a_hold_file(monkeypatch, tmp_path):
    hs = _house_speak()
    monkeypatch.delenv("HOUSE_TOOL_HOLD", raising=False)
    monkeypatch.setattr(hs, "_HOUSE", False)
    hold = tmp_path / "house-tool-hold.json"
    hold.write_text('{"until": 9999999999}')
    real_path = hs.Path
    monkeypatch.setattr(hs, "Path", lambda p, *a: real_path(hold) if str(p) == "/tmp/house-tool-hold.json" else real_path(p, *a))
    assert hs._tool_hold_blocks("crissa") is None


def test_the_house_side_keeps_every_old_value():
    """The house's addresses moved to config/house-machine.json (2026-10-06); the values are pinned there."""
    import json
    assert '_house_value("voice_cache_release_url") if _HOUSE else ""' in SRC
    assert '_house_value("hear_health_url") if _HOUSE else ""' in SRC
    assert '"/tmp/house-tool-hold.json" if _HOUSE else ""' in SRC
    cfg = json.loads((ROOT / "config" / "house-machine.json").read_text())
    assert cfg["voice_cache_release_url"] == "http://127.0.0.1:8190/v1/security/voice_cache/release"
    assert cfg["hear_health_url"] == "http://127.0.0.1:8194/health"


def test_a_fresh_house_speak_knows_it_is_the_house():
    """_HOUSE is read at import, before anything else puts lib on sys.path; loaded alone in a house tree it must be
    True with the old URLs (a first draft read False there and would have turned the house's ear calls off)."""
    code = ("import importlib.util as u; s = u.spec_from_file_location('hs', %r); m = u.module_from_spec(s); "
            "s.loader.exec_module(m); print(m._HOUSE, m.VOICE_CACHE_URL, m.HEAR_HEALTH_URL)" % str(HS))
    env = {k: v for k, v in os.environ.items() if k not in ("CRISSA_HOME", "HOUSE_VOICE_CACHE_URL", "HOUSE_HEAR_HEALTH_URL")}
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, cwd="/").stdout.split()
    house = '"state_home"' in (ROOT / "avatars" / "crissa" / "manifest.json").read_text()
    assert out[0] == str(house)
    if house:
        assert out[1:] == ["http://127.0.0.1:8190/v1/security/voice_cache/release", "http://127.0.0.1:8194/health"]


# ---- a pack's mouth is fed the bytes it hears (2026-10-05, the Mac's ~20 % long mouth) ----

def test_a_pack_feeds_the_mouth_the_stretched_bytes_and_drops_the_temp_file(monkeypatch, tmp_path):
    hs = _house_speak()
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.delenv("CRISSA_MOUTH_STRETCHED", raising=False)
    raw, heard = b"\x01\x00" * 1200, b"\x02\x00" * 1000
    monkeypatch.setattr(hs, "stretch_pcm", lambda pcm, sr, tempo: heard if pcm == raw else pcm)
    seen = {}

    def fake_paplay(path, sink, pcm=None, sr=24000, clock_tempo=None, **k):
        seen.update(exists=Path(path).exists(), pcm=pcm, clock_tempo=clock_tempo)
    monkeypatch.setattr(hs, "paplay", fake_paplay)
    out = tmp_path / "line.wav"
    assert hs.pack_plays_heard() is True
    assert hs.play_heard(raw, 24000, out, "sink") == heard
    assert seen == {"exists": True, "pcm": heard, "clock_tempo": 1.0}, "the mouth hops the bytes the file plays"
    assert not out.exists(), "the temp file is removed when the line ends"


def test_play_heard_keeps_a_file_the_caller_asked_for(monkeypatch, tmp_path):
    hs = _house_speak()
    monkeypatch.setattr(hs, "stretch_pcm", lambda pcm, sr, tempo: pcm)
    monkeypatch.setattr(hs, "paplay", lambda *a, **k: None)
    out = tmp_path / "kept.wav"
    hs.play_heard(b"\x01\x00" * 100, 24000, out, "sink", keep=True)
    assert out.exists()


def test_the_house_and_the_kill_switch_keep_the_old_play(monkeypatch):
    hs = _house_speak()
    monkeypatch.setattr(hs, "_HOUSE", True)
    assert hs.pack_plays_heard() is False
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setenv("CRISSA_MOUTH_STRETCHED", "0")
    assert hs.pack_plays_heard() is False
    assert "applied = pcm_to_wav(pcm, out, sr=sr)\n        paplay(out, sink, pcm=pcm, sr=sr, clock_tempo=applied)" in SRC


# ---- the voice tier (2026-10-06): same code path, the machine's config names the backup and the announcer ----------

def test_the_house_keeps_cosy_then_audio8_and_no_separate_announcer(monkeypatch):
    for k in ("HOUSE_SPEAK_BACKUP", "HOUSE_SPEAK_ANNOUNCER"):
        monkeypatch.delenv(k, raising=False)
    hs = _house_speak()
    assert hs.BACKUP == "audio8" and hs.ANNOUNCER == "", "the house's live path is exactly as before"


def test_a_pack_on_turbo_streams_through_the_house_path_with_its_announcer_order(monkeypatch):
    """Cosy left the packs (2026-10-06); a pack's streamed voice (Turbo here) takes the house's streaming path, and
    the announcer's own order (Audio8, Pocket, Kokoro) comes with it, never the house's Audio8 address."""
    hs = _house_speak()
    for k in ("HOUSE_SPEAK_BACKUP", "HOUSE_SPEAK_ANNOUNCER"):
        monkeypatch.delenv(k, raising=False)
    cfg = {"engine": "turbo", "url": "http://127.0.0.1:8186/stream", "backup": ["kokoro", "say"],
           "announcer": "audio8", "announcer_chain": ["audio8", "pocket", "kokoro", "text"], "passed": []}
    monkeypatch.setattr(hs, "_HOUSE", False)
    sys.path.insert(0, str(ROOT / "lib"))
    import crissa_tts
    monkeypatch.setattr(crissa_tts, "config", lambda env=None, home=None, demand=False: dict(cfg))   # a line: demand
    monkeypatch.setattr(crissa_tts, "note_choice", lambda c: None)
    monkeypatch.setattr(hs, "BACKUP", "audio8")
    monkeypatch.setattr(hs, "ANNOUNCER", "")
    assert hs._crissa_tts_cfg() is None, "None = house-speak's own streaming Cosy path"
    assert hs.VOICE_URL == "http://127.0.0.1:8186/stream" and hs.BACKUP == "kokoro" and hs.ANNOUNCER == "audio8"
    assert hs._STREAMED["announcer_chain"] == ["audio8", "pocket", "kokoro", "text"]
    assert 'if not _HOUSE and chain:' in SRC and '{"engine": chain[0], "announcer_chain": chain}' in SRC


def test_the_backup_and_announcer_are_config_not_code():
    assert '_house_value("tts_backup")' in SRC and '_house_value("tts_announcer")' in SRC
    assert "def _try_backup()" in SRC and "rc = _try_backup()" in SRC


# ---- tempo follows the voice engine (the User 2026-10-06: every voice at 1.0x on Turbo) ----

def test_a_named_tempo_always_wins():
    hs = _house_speak()
    assert hs.default_tempo({"HOUSE_SPEAK_TEMPO": "1.15"}, health={"engine": "chatterbox-turbo"}) == 1.15


def test_turbo_speaks_at_one_and_cosy_keeps_the_old_law():
    hs = _house_speak()
    assert hs.default_tempo({}, health={"ok": True, "engine": "chatterbox-turbo"}) == 1.0
    assert hs.default_tempo({}, health={"ok": True, "model": "Fun-CosyVoice3-0.5B-2512"}) == 1.2, "Cosy's health names no engine"
    assert hs.default_tempo({"HOUSE_SPEAK_TEMPO_FOLLOWS_ENGINE": "0"}, health={"engine": "chatterbox-turbo"}) == 1.2


def test_no_voice_server_is_the_old_law(monkeypatch):
    hs = _house_speak()
    monkeypatch.setattr(hs, "VOICE_URL", "http://127.0.0.1:9/stream")
    assert hs.default_tempo({}) == 1.2
