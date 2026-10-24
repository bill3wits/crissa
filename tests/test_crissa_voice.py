"""modules/voice (2026-10-01): the listening loop, the turns server, the first-use ASR install, and that no
voice ships. No microphone, no network: frames, the ASR and the downloads are stand-ins."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "modules" / "voice"
sys.path.insert(0, str(VOICE))
from crissa_voice import listen as L  # noqa: E402
from crissa_voice import asr_setup as S  # noqa: E402
from crissa_voice.vad import SpeechSpan  # noqa: E402

SILENT = np.zeros(L.FRAME, dtype=np.int16)
LOUD = (np.sin(np.arange(L.FRAME) * 0.3) * 8000).astype(np.int16)


def frames(*parts):
    for kind, seconds in parts:
        for _ in range(int(seconds * 1000 / L.FRAME_MS)):
            yield LOUD if kind == "speech" else SILENT


@pytest.fixture(autouse=True)
def energy_spans(monkeypatch):
    """Speech spans by energy, so the test does not depend on WebRTC's GMM."""
    def spans(audio, sr=16000, **kw):
        loud = np.abs(audio) > 0.05
        n = int(loud.sum())
        return [SpeechSpan(0.0, n / sr)] if n else []
    monkeypatch.setattr(L, "detect_speech_spans", spans)


def judge(f):
    return bool(np.abs(f).max() > 1000)


def run(parts, text="turn on the lights", speaking=lambda: False, asr=None):
    got, seen = [], []

    def fake_asr(pcm):
        seen.append(len(pcm))
        return {"text": text, "engine": "fake"}
    ls = L.Listener(asr or fake_asr, got.append, judge=judge, speaking=speaking, clock=lambda: 1000.0)
    ls.run(frames(*parts))
    return got, seen, ls


def test_speech_between_silences_is_one_turn_with_preroll_and_hangover():
    got, seen, ls = run([("silence", 1.0), ("speech", 1.2), ("silence", 2.0)])
    assert got == [{"ts": 1000.0, "text": "turn on the lights", "duration_s": got[0]["duration_s"], "via": "ear",
                    "engine": "fake", "her_voice": False}]
    secs = seen[0] / L.SR
    assert 1.2 + L.HANGOVER_S - 0.1 <= secs <= 1.2 + L.HANGOVER_S + L.PREROLL_S + 0.1, secs
    assert ls.stats["turns"] == 1


def test_two_sentences_with_a_pause_longer_than_the_hangover_are_two_turns():
    got, _, _ = run([("speech", 1.0), ("silence", 1.5), ("speech", 1.0), ("silence", 1.5)])
    assert len(got) == 2


def test_what_she_says_is_marked_and_the_ear_drops_it():
    got, _, _ = run([("speech", 1.0), ("silence", 1.5)], speaking=lambda: True)
    assert got[0]["her_voice"] is True
    ear = (ROOT / "scripts" / "crissa-ear.py").read_text()
    assert 'not t.get("her_voice")' in ear


def test_blips_noise_and_a_dead_asr_never_become_turns():
    got, _, ls = run([("speech", 0.09), ("silence", 1.5)])
    assert got == [] and ls.stats["utterances"] == 1 and ls.stats["dropped_short"] == 1, ls.stats
    got, _, ls = run([("speech", 1.0), ("silence", 1.5)], text="喵喵喵。")
    assert got == [] and ls.stats["dropped_quality"] == 1
    got, _, ls = run([("speech", 1.0), ("silence", 1.5)], text="Thank you for watching.")
    assert got == [] and ls.stats["dropped_quality"] == 1

    def boom(pcm):
        raise OSError("refused")
    got, _, ls = run([("speech", 1.0), ("silence", 1.5), ("speech", 1.0), ("silence", 1.5)], asr=boom)
    assert got == [] and ls.stats["asr_errors"] == 2, "the loop goes on"


def test_a_long_monologue_is_cut_at_the_cap():
    got, seen, _ = run([("speech", L.MAX_UTTER_S + 3), ("silence", 1.5)])
    assert len(got) == 2 and seen[0] / L.SR <= L.MAX_UTTER_S + 0.05


@pytest.fixture()
def fake_asr():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers["Content-Length"])
            self.rfile.read(n)
            raw = json.dumps({"text": "hello crissa", "sr": self.headers.get("X-Sample-Rate"), "bytes": n}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
            self.wfile.write(raw)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def test_the_asr_client_speaks_the_stt_contract(fake_asr):
    out = L.asr_client(fake_asr)(np.ones(1600, dtype=np.int16))
    assert out == {"text": "hello crissa", "sr": "16000", "bytes": 3200}


def test_the_turns_server_serves_newest_first_and_passes_stt_through(fake_asr):
    spec = importlib.util.spec_from_file_location("crissa_stt", VOICE / "scripts" / "crissa-stt.py")
    srv_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv_mod)
    srv_mod.STATE["asr"] = fake_asr
    for i in range(3):
        srv_mod.TURNS.append({"ts": float(i), "text": "t%d" % i, "via": "ear"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), srv_mod.make_handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        d = json.loads(urllib.request.urlopen(base + "/v1/turns?n=2", timeout=5).read())
        assert [t["text"] for t in d["turns"]] == ["t2", "t1"] and d["count"] == 2
        req = urllib.request.Request(base + "/stt", data=b"\x00\x00" * 800, method="POST", headers={"X-Sample-Rate": "16000"})
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["text"] == "hello crissa"
        h = json.loads(urllib.request.urlopen(base + "/health", timeout=5).read())
        assert h["ok"] and h["listening"] is False
    finally:
        srv.shutdown()


def test_the_asr_installs_on_first_use_with_the_cpu_torch(tmp_path, monkeypatch):
    for k, v in (("ROOT", tmp_path / "asr"), ("ENV", tmp_path / "asr" / "env"), ("MODEL", tmp_path / "asr" / "model"),
                 ("DONE", tmp_path / "asr" / "installed.json")):
        monkeypatch.setattr(S, k, v)
    monkeypatch.setenv("CRISSA_VOICE_PYENV", "0")   # the lock path; the micromamba env is test_crissa_voice_pyenv's
    cmds = []

    def run(cmd):
        cmds.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            (S.ENV / "bin").mkdir(parents=True); (S.ENV / "bin" / "python").write_text("")
    monkeypatch.setattr(sys, "platform", "linux")
    real_lock_path = S.locks.lock_path
    monkeypatch.setattr(S.locks, "lock_path", lambda *a, **k: None)      # the pins path (a machine with no lock)
    S.make_env(run=run, log=lambda m: None)
    assert cmds[1][-3:] == ["--index-url", S.TORCH_INDEX, "torch==2.6.0"], "the CPU wheel, not the CUDA one"
    assert cmds[2][-len(S.PACKAGES):] == S.PACKAGES and "librosa==0.10.2" in S.PACKAGES
    cmds.clear(); S.make_env(run=run, log=lambda m: None)
    assert [c[1:3] for c in cmds] == [["-m", "pip"], ["-m", "pip"]], "an existing env is repaired, not rebuilt"
    monkeypatch.setattr(S.locks, "lock_path", real_lock_path)           # and the hash lock, when there is one
    monkeypatch.setattr(S.locks.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(S.locks.sys, "version_info", (3, 12, 0))
    cmds.clear()
    assert S.make_env(run=run, log=lambda m: None) == "lock:asr-linux-x86_64-cpu-py312.txt"
    assert "--require-hashes" in cmds[-1] and "https://download.pytorch.org/whl/cpu" in cmds[-1]
    monkeypatch.setattr(sys, "prefix", str(S.ENV))
    assert S.in_env(), "a venv is recognised by its prefix, not by its (symlinked) interpreter"

    def fetch(url, dest, timeout=300.0):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps([{"type": "file", "path": p} for p in ("README.md",)]) if "/api/" in url else "x")
    monkeypatch.setattr(S, "_fetch", fetch)
    with pytest.raises(RuntimeError, match="runtime files"):
        S.get_model(fetch=fetch)
    monkeypatch.setattr(S, "BUILD", tmp_path / "asr" / "build")
    files = ["runtime_manifest.json", "infer_ark_audio_onnx.py", "llm_kv_cpu_fp32_int8.json", "llm_kv_fp32_qwen_native.json"]

    def fetch2(url, dest, timeout=300.0):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps([{"type": "file", "path": p} for p in files]) if "/api/" in url else dest.name)
    S.get_model(fetch=fetch2)
    assert sorted(p.name for p in S.BUILD.iterdir()) == ["llm_kv_cpu_fp32_int8.json", "llm_kv_fp32_qwen_native.json"]
    assert (S.MODEL / ".complete").read_text().strip() == S.MODEL_REV


def test_no_voice_ships_in_the_voice_module():
    """The User 2026-10-01: no cloned house voice ships. The module carries code, nothing that speaks, but TWO clips,
    each by its sha256: Crissa's own voice (the User 2026-10-08), cut from her Grok Imagine films' sound, and Lila's (the
    User 2026-10-12), cut from the sound of one of the house's Grok Imagine films."""
    import hashlib  # noqa: PLC0415
    files = [p for p in VOICE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    audio = [p for p in files if p.suffix.lower() in (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".npy", ".pt", ".onnx")]
    want = {"faces/crissa-imagine.wav": "bbf638c07031c14ed842351ebb434f96fa078c2b5fe1ef846f0f61e9d907d8fc",
            "faces/lila-imagine.wav": "1af95b61e22ede6285fe4084e98f0b59e0ab36a077369ac09faa868ae2c6cc84"}
    assert sorted(str(p.relative_to(VOICE)) for p in audio) == sorted(want), audio
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == want[str(p.relative_to(VOICE))] for p in audio)
    assert not any(part in ("voices", "registration", "refs") for p in files for part in p.parts)
    for p in files:
        if p.suffix in (".py", ".md", ".json"):
            # Audio8's own setting ARKTTS_VOICES_DIR (upstream's name for its registered-voice folder) is not ours
            t = p.read_text(errors="ignore").lower().replace("arktts_voices_dir", "")
            assert "crissa.wav" not in t and "lila.wav" not in t and "voices_dir" not in t, p


# ── speech-to-text refuses a Python or a Mac its pins cannot serve (the User 2026-10-04) ──────────────────────

import pytest  # noqa: E402


@pytest.mark.parametrize("version, plat, machine, want", [
    ((3, 12), "darwin", "arm64", ""),
    ((3, 12), "linux", "x86_64", ""),
    ((3, 13), "darwin", "arm64", "3.9 to 3.12"),
    ((3, 14), "linux", "x86_64", "3.9 to 3.12"),
    ((3, 8), "linux", "x86_64", "3.9 to 3.12"),
    ((3, 12), "darwin", "x86_64", "Apple Silicon"),
])
def test_unsupported_says_why_before_anything_downloads(monkeypatch, version, plat, machine, want):
    monkeypatch.delenv("CRISSA_ASR_ANY_PYTHON", raising=False)
    got = S.unsupported(version, plat, machine)
    assert (got == "") if not want else (want in got)


def test_unsupported_kill_switch(monkeypatch):
    monkeypatch.setenv("CRISSA_ASR_ANY_PYTHON", "1")
    assert S.unsupported((3, 14), "darwin", "x86_64") == ""


def test_install_refuses_before_building_anything(monkeypatch):
    monkeypatch.setattr(S, "unsupported", lambda *a, **k: "needs python3.12")
    def never(*a, **k):
        raise AssertionError("make_env ran on an unsupported Python")
    monkeypatch.setattr(S, "make_env", never)
    with pytest.raises(RuntimeError, match="python3.12"):
        S.install(log=lambda *a: None)


def test_the_readme_runs_the_installers_on_any_python3():
    """2026-10-07: the install makes its own pinned Python (pyenv.py); the README never asks for python3.12."""
    voice = Path(__file__).resolve().parents[1] / "modules" / "voice"
    readme = (voice / "README.md").read_text()
    assert "python3 modules/voice/scripts/crissa-asr.py --install" in readme
    assert "python3.12" not in readme and "micromamba" in readme and "conda-forge" in readme
    src = (voice / "scripts" / "crissa-asr.py").read_text()
    assert src.index("S.unsupported()") < src.index("S.install()")
