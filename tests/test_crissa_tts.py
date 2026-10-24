"""The voice seam (2026-09-28): any engine hands house-speak PCM; unset is the house's Cosy path.

Proved against a real loopback HTTP server (the openai engine), stand-in
executables on PATH (say, piper), and house-speak's own hook with a stand-in
player — nothing here makes a sound.
"""
from __future__ import annotations

import importlib.util
import io
import time
import json
import struct
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_tts as T  # noqa: E402


def _wav(sr=22050, ch=1, seconds=0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = b"".join(struct.pack("<h", (i * 37) % 3000) * ch for i in range(int(sr * seconds)))
        w.writeframes(frames)
    return buf.getvalue()


class _Speech(BaseHTTPRequestHandler):
    mode = "pcm"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                "body": json.loads(self.rfile.read(n) or b"{}")})
        if type(self).mode == "401":
            self.send_response(401)
            self.end_headers()
            return
        data = _wav() if type(self).mode == "wav" else b"\x10\x00" * 24000
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav" if type(self).mode == "wav" else "audio/pcm")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def speech():
    _Speech.mode, _Speech.seen = "pcm", []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Speech)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/v1" % srv.server_address[1]
    srv.shutdown()


def test_unset_is_the_house_path(tmp_path):
    assert T.config(env={}, home=tmp_path) == {}
    assert T.config(env={"CRISSA_TTS": "cosy"}, home=tmp_path) == {}


def test_env_beats_the_file_and_the_file_names_voices(tmp_path):
    (tmp_path / "agent.json").write_text(json.dumps(
        {"url": "http://agent/v1", "tts": {"engine": "openai", "url": "http://a/v1", "voices": {"lila": "shimmer"}}}))
    cfg = T.config(env={"CRISSA_TTS_URL": "http://b/v1"}, home=tmp_path)
    assert cfg["engine"] == "openai" and cfg["url"] == "http://b/v1"
    assert T.voice_for(cfg, "lila") == "shimmer" and T.voice_for(cfg, "crissa") == ""


def test_openai_engine_speaks_pcm(speech):
    pcm, sr = T.synth("hello there", "crissa", {"engine": "openai", "url": speech, "key": "sk-t",
                                              "voices": {"crissa": "nova"}})
    assert sr == 24000 and len(pcm) == 48000
    got = _Speech.seen[-1]
    assert got["path"] == "/v1/audio/speech" and got["auth"] == "Bearer sk-t"
    assert got["body"]["response_format"] == "pcm" and got["body"]["voice"] == "nova"


def test_a_server_that_sends_a_wav_is_resampled_to_24k(speech):
    _Speech.mode = "wav"
    pcm, sr = T.synth("hi", "crissa", {"engine": "openai", "url": speech})
    assert sr == 24000 and abs(len(pcm) // 2 - 12000) <= 4


def test_a_refusal_names_the_status_never_the_key(speech):
    _Speech.mode = "401"
    with pytest.raises(T.TTSError) as e:
        T.synth("hi", "crissa", {"engine": "openai", "url": speech, "key": "sk-secret"})
    assert "401" in str(e.value) and "sk-secret" not in str(e.value)


def _stand_in(tmp_path, name: str, body: str) -> Path:
    exe = tmp_path / name
    exe.write_text("#!%s\n%s\n" % (sys.executable, body))
    exe.chmod(0o755)
    return exe


def test_say_engine_writes_24k_wav(tmp_path, monkeypatch):
    wav = _wav(sr=24000, seconds=0.4)
    exe = _stand_in(tmp_path, "say", "import sys\nargs=sys.argv\nopen(args[args.index('-o')+1],'wb').write(%r)" % wav)
    monkeypatch.setattr(T.shutil, "which", lambda n: str(exe) if n == "say" else None)
    pcm, sr = T.synth("hello", "crissa", {"engine": "say", "voice": "Samantha"})
    assert sr == 24000 and len(pcm) == 2 * int(24000 * 0.4)


def test_piper_engine_reads_text_on_stdin(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"x")
    wav = _wav(sr=22050, ch=2, seconds=0.5)
    got = tmp_path / "stdin.txt"
    exe = _stand_in(tmp_path, "piper", "import sys\nargs=sys.argv\nopen(%r,'w').write(sys.stdin.read())\n"
                    "open(args[args.index('--output_file')+1],'wb').write(%r)" % (str(got), wav))
    monkeypatch.setattr(T.shutil, "which", lambda n: str(exe) if n == "piper" else None)
    pcm, sr = T.synth("hello piper", "lila", {"engine": "piper", "model": str(model)})
    assert got.read_text() == "hello piper"
    assert sr == 24000 and abs(len(pcm) // 2 - 12000) <= 4, "stereo reduced to mono, 22.05k to 24k"


def test_piper_without_a_model_says_so():
    with pytest.raises(T.TTSError, match="model"):
        T.synth("hi", "crissa", {"engine": "piper"})


def _house_speak():
    spec = importlib.util.spec_from_file_location("house_speak_tts", ROOT / "scripts" / "house-speak.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_house_speak_keeps_its_own_path_when_no_engine_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_TTS", raising=False)
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path))
    hs = _house_speak()
    monkeypatch.setattr(hs, "_crissa_home_dir", lambda: tmp_path)
    assert hs._try_crissa_tts("hi", "crissa", lambda *a: 0, 100) is None


def test_house_speak_plays_a_named_engines_line_through_play(speech, tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_TTS", "openai")
    monkeypatch.setenv("CRISSA_TTS_URL", speech)
    hs = _house_speak()
    played = []
    rc = hs._try_crissa_tts("hello", "crissa", lambda pcm, sr, tag: played.append((len(pcm), sr, tag)) or 0, 100)
    assert rc == 0 and played == [(48000, 24000, "tts-openai")]


def test_a_named_engine_that_fails_is_rc_1_not_a_wait_on_cosy(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_TTS", "openai")
    monkeypatch.setenv("CRISSA_TTS_URL", "http://127.0.0.1:9/v1")
    hs = _house_speak()
    assert hs._try_crissa_tts("hello", "crissa", lambda *a: 0, 100) == 1


# ---- quiet fallback (2026-10-01): off by default; on, this machine's own voice speaks ----

def _tts():
    import crissa_tts
    return crissa_tts


def test_fallback_is_off_by_default_and_the_named_error_stands(monkeypatch, tmp_path):
    T = _tts()
    monkeypatch.delenv("CRISSA_TTS_FALLBACK", raising=False)
    cfg = {"engine": "openai", "url": "http://127.0.0.1:9/v1"}
    assert T.fallback_engines(cfg) == [] and T.config({}, tmp_path) == {}
    monkeypatch.setattr(T, "_openai", lambda *a: (_ for _ in ()).throw(T.TTSError("address down")))
    import pytest
    with pytest.raises(T.TTSError, match="address down"):
        T.synth("hello", "crissa", cfg)


def test_fallback_speaks_with_say_when_the_address_is_down(monkeypatch, tmp_path):
    T = _tts()
    monkeypatch.setenv("CRISSA_TTS_FALLBACK", "1")
    monkeypatch.setattr(T, "VOICE_STATE", tmp_path / "voice-state.json")
    monkeypatch.setattr(T.shutil, "which", lambda name: "/usr/bin/say" if name == "say" else None)
    monkeypatch.setattr(T, "_openai", lambda *a: (_ for _ in ()).throw(T.TTSError("address down sk-secret")))
    seen = {}

    def fake_say(text, cfg, speaker, timeout):
        seen["cfg"] = cfg
        return b"\x01\x00" * 24000, 24000
    monkeypatch.setattr(T, "_say", fake_say)
    pcm, sr = T.synth("hello", "crissa", {"engine": "openai", "url": "http://127.0.0.1:9/v1", "voice": "nova"})
    assert sr == T.HOUSE_SR and len(pcm) == 48000
    assert seen["cfg"] == {"engine": "say"}, "a fallback never inherits another engine's voice"
    import json
    rec = json.loads((tmp_path / "voice-state.json").read_text())
    assert rec["spoke"] == "say" and rec["named"] == "openai" and rec["fallback"] is True


def test_fallback_with_no_engine_named_uses_this_machines_voice(monkeypatch, tmp_path):
    T = _tts()
    monkeypatch.setattr(T.shutil, "which", lambda name: None)
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"m")
    env = {"CRISSA_TTS_FALLBACK": "1", "CRISSA_TTS_PIPER_MODEL": str(model)}
    assert T.config(env, tmp_path) == {"engine": "piper", "model": str(model)}
    assert T.config({"CRISSA_TTS_FALLBACK": "1"}, tmp_path) == {}, "nothing on this machine: house-speak's own path, as before"


# ── a Mac outside the house speaks with `say` when nothing names a voice (the User 2026-10-04) ──────────────────

import crissa_home  # noqa: E402


def _fake_mac(monkeypatch, *, plat="darwin", house=False, say="/usr/bin/say"):
    monkeypatch.setattr(T, "_platform", lambda: plat)
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: house)
    monkeypatch.setattr(T.shutil, "which", lambda name: say if name == "say" else None)


def test_a_mac_outside_the_house_speaks_with_say(monkeypatch, tmp_path):
    _fake_mac(monkeypatch)

    def pick(env):
        # the pick, without `passed` (round 37: the tiers name each engine passed over, e.g. "pocket: no Pocket server
        # ... answers at http://127.0.0.1:8188"; whether one answers depends on this machine, not on the code)
        got = T.config(env, home=tmp_path)
        assert all("pocket" in str(n) for n in got.get("passed") or []), got.get("passed")
        return {k: v for k, v in got.items() if k != "passed"}
    assert pick({}) == {"engine": "say"}
    assert pick({"CRISSA_TTS_VOICE": "Samantha"}) == {"engine": "say", "voice": "Samantha"}


@pytest.mark.parametrize("fake, env", [
    ({"plat": "linux"}, {}),
    ({"house": True}, {}),
    ({"say": None}, {}),
    ({}, {"CRISSA_TTS_DEFAULT_SAY": "0"}),
    ({}, {"CRISSA_TTS": "off"}),
])
def test_no_say_default_on_linux_the_house_no_binary_the_kill_or_an_explicit_off(monkeypatch, tmp_path, fake, env):
    _fake_mac(monkeypatch, **fake)
    assert T.config(env, home=tmp_path) == {}


def test_house_speak_on_a_mac_uses_the_say_engine(monkeypatch, tmp_path):
    monkeypatch.delenv("CRISSA_TTS", raising=False)
    hs = _house_speak()
    monkeypatch.setattr(hs, "_crissa_home_dir", lambda: tmp_path)
    monkeypatch.setattr(hs, "_on_mac", lambda: True)
    monkeypatch.setattr(T, "config", lambda env=None, home=None, demand=False: {"engine": "say"})
    monkeypatch.setattr(T, "synth", lambda text, voice, cfg: (b"\x00\x01" * 12000, 24000))
    played = []
    assert hs._try_crissa_tts("hi", "crissa", lambda pcm, sr, tag: played.append(tag) or 0, 100) == 0
    assert played == ["tts-say"]


def test_the_status_mark_reads_the_say_engine_on_a_mac(monkeypatch, tmp_path):
    import crissa_status
    _fake_mac(monkeypatch)
    monkeypatch.setattr(T, "HOME", tmp_path)
    monkeypatch.setattr(T, "VOICE_STATE", tmp_path / "voice-state.json", raising=False)
    m = crissa_status.voice_out_mark({})
    assert m["state"] == "ok" and "machine" in m["note"]


def test_synth_rejects_zero_sample_rate_so_fallback_runs(monkeypatch):
    """X-Sample-Rate: 0 must be TTSError, never ZeroDivisionError past the fallback chain."""
    import crissa_tts as T

    def boom(text, cfg, speaker, timeout):
        return b"\x00\x01" * 2400, 0

    monkeypatch.setattr(T, "_streamed", boom)
    monkeypatch.setattr(T, "_openai", lambda *a, **k: (_ for _ in ()).throw(T.TTSError("no openai")))
    monkeypatch.setattr(T, "fallback_engines", lambda cfg: ["openai"])
    with pytest.raises(T.TTSError) as ei:
        T.synth("hello", "crissa", {"engine": "turbo", "backup": ["openai"]})
    assert "sample rate" in str(ei.value).lower() or "0" in str(ei.value)


# ---- Audio8 streams (the User 2026-10-08): first sound on the first chunk, cancel, the 72-char fallback ------------
import base64 as _b64
import threading as _thr


def _ndjson_server(mode: str, hits: list, chunk_s: float = 0.1, gap_s: float = 0.05):
    """A fake Audio8: `stream` answers /api/tts/stream with three chunks of `chunk_s` audio each `gap_s` apart,
    `nostream` 404s it and serves /api/tts."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            hits.append((self.path, body))
            if self.path == "/api/tts/cancel":
                raw = b'{"ok": true, "cancelled": true}'
                self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            if self.path == "/api/tts/stream":
                if mode != "stream":
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
                self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
                self.wfile.write((json.dumps({"event": "start", "sample_rate": 24000}) + "\n").encode()); self.wfile.flush()
                for seq in range(3):
                    pcm = bytes([seq + 1, 0]) * int(24000 * chunk_s)
                    self.wfile.write((json.dumps({"event": "audio_chunk", "seq": seq, "frame_count": 2400,
                                                  "pcm_b64": _b64.b64encode(pcm).decode()}) + "\n").encode())
                    self.wfile.flush()
                    time.sleep(gap_s)
                self.wfile.write((json.dumps({"event": "complete", "frame_count": 7200}) + "\n").encode())
                return
            if self.path == "/api/tts":
                import io, wave
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\x01\x00" * 24000)
                raw = buf.getvalue()
                self.send_response(200); self.send_header("Content-Type", "audio/wav"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    _thr.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def test_audio8_streams_and_the_first_chunk_plays_before_the_stream_ends(monkeypatch, tmp_path):
    monkeypatch.delenv("CRISSA_AUDIO8_STREAM", raising=False)
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "zh"))         # no rate note yet: the announcer streams
    monkeypatch.setenv("CRISSA_AUDIO8_STREAM_CHECK_S", "0.05")      # judge the rate on the first chunk alone
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_tts_fast_t", ROOT / "lib" / "crissa_tts.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    monkeypatch.setattr(T, "_stream_note_path", lambda: tmp_path / "zh" / "audio8-stream.json")
    hits = []
    srv, url = _ndjson_server("stream", hits)
    try:
        cfg = {"engine": "audio8", "url": url, "voice": "anna"}
        seen = []

        def play_chunks(chunks, sr, tag):
            for c in chunks:
                seen.append((time.time(), len(c)))
            return 0

        rc = T.speak_audio8("One. Two. Three.", "announcer", cfg, lambda *a: 0, 0, play_chunks=play_chunks)
        assert rc == 0 and len(seen) == 3 and all(n == 4800 for _, n in seen)
        assert seen[-1][0] - seen[0][0] >= 0.04          # the tail arrived as it was made, not after the stream ended
        assert hits[0][0] == "/api/tts/stream" and hits[0][1]["text"] == "One. Two. Three."
        assert "max_new_tokens" in hits[0][1] and len(hits) == 1   # one request, no pieces, no character cap of ours
    finally:
        srv.shutdown()


def test_audio8_falls_back_to_72_char_pieces_when_the_stream_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_AUDIO8_STREAM", "1")                 # a reply forced onto the stream
    monkeypatch.setattr(T, "_stream_note_path", lambda: tmp_path / "none.json")
    hits = []
    srv, url = _ndjson_server("nostream", hits)
    try:
        cfg = {"engine": "audio8", "url": url, "voice": "anna"}
        played = []
        text = ("A first sentence of a reply that is long enough to need pieces. A second sentence that is also long "
                "enough to need splitting into pieces. A third one.")
        rc = T.speak_audio8(text, "crissa", cfg, lambda pcm, sr, tag: played.append(len(pcm)) or 0, 0,
                            play_chunks=lambda *a: 0)
        assert rc == 0 and played
        pieces = [b["text"] for p, b in hits if p == "/api/tts"]
        assert pieces and all(len(p) <= 72 for p in pieces) and " ".join(pieces) == text   # 72 a request, nothing dropped
    finally:
        srv.shutdown()


def test_cancel_posts_to_the_server_the_stream_is_open_on():
    hits = []
    srv, url = _ndjson_server("stream", hits)
    try:
        T._AUDIO8_LIVE["base"] = url
        assert T.cancel_audio8() is True and hits[-1][0] == "/api/tts/cancel"
        T._AUDIO8_LIVE["base"] = ""
        assert T.cancel_audio8() is False
    finally:
        srv.shutdown()


def test_the_pieces_are_the_houses_and_the_announcer_is_capped_at_160():
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_tts_caps_t", ROOT / "lib" / "crissa_tts.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    assert T.AUDIO8_PIECE_CHARS == 72 and T.AUDIO8_LINE_CHARS == 160
    long = "word " * 60
    assert len(T.audio8_line(long, "announcer")) <= 160 and T.audio8_line(long, "crissa") == long.strip()
    assert all(len(p) <= 72 for p in T.audio8_pieces(long, 72)) and " ".join(T.audio8_pieces(long, 72)) == long.strip()


def test_a_stream_slower_than_real_time_plays_whole_and_the_next_line_takes_the_pieces(monkeypatch, tmp_path):
    """Measured 2026-10-08: the reference CPU streams at 0.25x real time and a cancel wedges the engine. Such a
    stream is read to its end and played whole (never cancelled), its rate is noted, and the next announcer line
    speaks in the 72-char pieces."""
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "zh"))
    monkeypatch.delenv("CRISSA_AUDIO8_STREAM", raising=False)
    monkeypatch.setenv("CRISSA_AUDIO8_STREAM_CHECK_S", "0.2")
    import importlib.util
    spec = importlib.util.spec_from_file_location("crissa_tts_slow_t", ROOT / "lib" / "crissa_tts.py")
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    monkeypatch.setattr(S, "_stream_note_path", lambda: tmp_path / "zh" / "audio8-stream.json")
    hits = []
    srv, url = _ndjson_server("stream", hits, chunk_s=0.1, gap_s=0.5)       # 0.1 s of audio every 0.5 s: 0.2x
    try:
        cfg = {"engine": "audio8", "url": url, "voice": "anna"}
        streamed, played = [], []
        rc = S.speak_audio8("One. Two. Three.", "announcer", cfg, lambda pcm, sr, tag: played.append((tag, len(pcm))) or 0,
                            0, play_chunks=lambda chunks, sr, tag: streamed.append(1) or 0)
        assert rc == 0 and not streamed and played == [("tts-audio8-stream-whole", 3 * 4800)]
        assert "/api/tts/cancel" not in [p for p, _ in hits]                   # never cancelled
        assert 0 < S.audio8_stream_note()["rate"] < 1
        assert S.audio8_stream_wanted("announcer") is False and S.audio8_stream_wanted("crissa") is False
        rc = S.speak_audio8("Four. Five.", "announcer", cfg, lambda pcm, sr, tag: played.append((tag, len(pcm))) or 0, 0,
                            play_chunks=lambda *a: 0)
        assert rc == 0 and [p for p, _ in hits].count("/api/tts") >= 1 and played[-1][0] == "tts-audio8"
    finally:
        srv.shutdown()


def test_the_announcer_streams_by_default_and_a_reply_does_not(monkeypatch, tmp_path):
    """Audio8 is the announcer's voice (the User 2026-10-08): announcer lines stream with no note against it; a
    spoken reply takes the pieces unless CRISSA_AUDIO8_STREAM=1; 0 switches the stream off for everyone."""
    monkeypatch.delenv("CRISSA_AUDIO8_STREAM", raising=False)
    monkeypatch.setattr(T, "_stream_note_path", lambda: tmp_path / "none.json")
    # round 23 (the User 2026-10-15): her replies stream too when Audio8's tier says stream_replies (on in the table)
    assert T.audio8_stream_wanted("announcer") is True and T.audio8_stream_wanted("crissa") is True
    monkeypatch.setattr(T, "audio8_streams_replies", lambda *a, **k: False)
    assert T.audio8_stream_wanted("crissa") is False, "a tier without stream_replies: a reply takes the pieces"
    assert T.audio8_stream_wanted("crissa", {"CRISSA_AUDIO8_STREAM": "1"}) is True
    assert T.audio8_stream_wanted("announcer", {"CRISSA_AUDIO8_STREAM": "0"}) is False
    hits = []
    srv, url = _ndjson_server("stream", hits)
    try:
        streamed, played = [], []
        rc = T.speak_audio8("One. Two.", "crissa", {"engine": "audio8", "url": url, "voice": "anna"},
                            lambda pcm, sr, tag: played.append(len(pcm)) or 0, 0,
                            play_chunks=lambda chunks, sr, tag: streamed.append(1) or 0)
        assert rc == 0 and not streamed and played and all(p == "/api/tts" for p, _ in hits)
    finally:
        srv.shutdown()
