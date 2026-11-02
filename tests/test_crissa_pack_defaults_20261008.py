"""Pack defaults that match the house (the User 2026-10-08).

Tempo is handed to each line by the conductor. Mouth and Lila flags ride the
player env from config/crissa-mouth.json. Audio8 plays a sentence while the
next one renders. The Control Panel follows the krew panel contract. A hand
start becomes user systemd units.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_defaults", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_conductor_hands_each_line_tempo_1():
    """The line that wins is speak_timing_env's HOUSE_SPEAK_TEMPO, fed by MOUTH_TEMPO's default."""
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    body = src.split("def speak_timing_env()", 1)[1].split("\ndef ", 1)[0]
    assert '"HOUSE_SPEAK_TEMPO": "%.3f" % (float(MOUTH_TEMPO) * float(PLAY_RATE))' in body
    assert 'MOUTH_TEMPO = _env_float("HOUSE_SPEAK_TEMPO", 1.0, 0.6, 1.6)' in src
    speak = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
    assert 'os.environ.get("HOUSE_SPEAK_TEMPO", "1.0")' in speak


def test_the_player_starts_with_the_house_mouth_and_lila_pins():
    raw = json.loads((ROOT / "config" / "crissa-mouth.json").read_text(encoding="utf-8"))
    assert "HOUSE_SPEAK_TEMPO" not in raw["env"]
    assert raw["env"]["HOUSE_MOUTH_TURN_FILL"] == "1"
    assert raw["env"]["HOUSE_TURN_P45_MAX"] == "90"
    assert raw["env"]["HOUSE_TURN_FRONT_MAX"] == "27"
    assert raw["env"]["HOUSE_TURN_LIB_45"] == "profile-45"
    assert raw["env"]["HOUSE_TURN_LIB_90"] == "profile-90"
    assert raw["env"]["HOUSE_MOUTH_TURN_PLACE"] == "lips"
    assert raw["env"]["HOUSE_MOUTH_LIPS_ALL"] == "1"
    assert raw["env"]["HOUSE_FLOURISH_CONVERSATION_HOLD"] == "1"
    assert "HOUSE_TURN_FILL_MIRROR" not in raw["env"]
    assert "HOUSE_TURN_LIB_45_L" not in json.dumps(raw)
    lila = raw["faces"]["lila"]
    assert lila["HOUSE_TURN_FRONT_MAX"] == "20"
    assert lila["HOUSE_MOUTH_PIN_CELLS"] == "1"
    assert lila["HOUSE_MOUTH_PIN_SPAN"] == "8"
    pkg = json.loads((ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8"))
    avatar = pkg["packages"]["crissa-avatar"]
    assert "config/crissa-mouth.json" in avatar["files"]
    assert "config/crissa-mouth.json" in avatar["files_why"]
    # The code defaults the tests pin stay off. The json is what a pack turns on.
    conductor = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    assert 'HOUSE_MOUTH_TURN_FILL", "0"' in conductor
    assert 'HOUSE_MOUTH_TURN_PLACE", "nose"' in conductor
    assert 'HOUSE_MOUTH_LIPS_ALL", "0"' in conductor
    z = _launcher()
    crissa = {k["name"]: k for k in z.plan(_args(), env_in={})["children"]}["player"]["env"]
    assert crissa["HOUSE_TURN_FRONT_MAX"] == "27"
    assert crissa["HOUSE_MOUTH_TURN_PLACE"] == "lips"
    assert "HOUSE_MOUTH_PIN_CELLS" not in crissa
    assert crissa["CRISSA_DESK_HELD"] == "1"
    held = dict(HOUSE_TURN_FRONT_MAX="12")
    kept = {k["name"]: k for k in z.plan(_args(), env_in=held)["children"]}["player"]["env"]
    assert kept["HOUSE_TURN_FRONT_MAX"] == "12"
    lila_env = {k["name"]: k for k in z.plan(_args(face="lila"), env_in={})["children"]}["player"]["env"]
    assert lila_env["HOUSE_TURN_FRONT_MAX"] == "20"
    assert lila_env["HOUSE_MOUTH_PIN_CELLS"] == "1"
    assert lila_env["HOUSE_MOUTH_PIN_SPAN"] == "8"
    assert z.mouth_env("crissa", root=ROOT / "does-not-exist") == {}


def test_audio8_speaks_a_sentence_while_the_next_one_renders(monkeypatch):
    """Line 2 on the .101 desk waited 22.5 s for the whole clip. The first sentence plays while the next renders.
    Turbo stays on the streamed path. One sentence still honours the short-audio floor."""
    import threading
    import crissa_tts as tts

    assert tts.audio8_play_units("Hi. There is more, still one sentence.") == [
        "Hi.", "There is more, still one sentence."]
    assert len(tts.audio8_play_units("Hi.")) == 1

    started = threading.Event()

    def synth_one(piece):
        if piece == "Second.":
            started.set()
        return b"\x00\x00" * 8, 24000

    def play_one(pcm, sr):
        assert started.wait(2), "the next sentence was not rendering while the first played"
        return 0

    assert tts.play_while_rendering(["First.", "Second."], synth_one, play_one) == 0

    def fake_synth(text, speaker, cfg, timeout=90.0):
        return b"\x00\x00" * 8, 24000

    monkeypatch.setattr(tts, "synth", fake_synth)
    heard = []
    rc = tts.speak_audio8_sentences(
        "Hi. And then the rest.", "crissa", {"engine": "audio8"},
        lambda pcm, sr, tag: heard.append((tag, len(pcm))) or 0, 12000)
    assert rc == 0
    assert heard == [("tts-audio8", 16), ("tts-audio8", 16)]
    try:
        tts.speak_audio8_sentences(
            "Hi.", "crissa", {"engine": "audio8"},
            lambda pcm, sr, tag: 0, 12000)
        raise AssertionError("a one-sentence line under min_pcm must still fail")
    except tts.TTSError:
        pass

    speak = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
    body = speak.split("def _try_crissa_tts(", 1)[1].split("\ndef ", 1)[0]
    assert "crissa_tts.speak_audio8(" in body and "play_chunks=play_chunks" in body   # the stream first, the sentences behind it (2026-10-08)
    assert 'CRISSA_AUDIO8_SENTENCE", "1"' in body
    assert '"audio8"' in body
    cfg = speak.split("def _crissa_tts_cfg()", 1)[1].split("\ndef ", 1)[0]
    assert "STREAMED" in cfg and "return None" in cfg
