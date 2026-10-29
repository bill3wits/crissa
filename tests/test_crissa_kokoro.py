"""Kokoro as the voice module's own TTS (2026-10-05): installed on first use with no espeak, served on loopback
as an OpenAI speech endpoint, picked per face, the default only behind a switch that is OFF.

No network and no model here: the downloader and pip are stand-ins, the engine behind the server is a stand-in,
and the seam is driven against a real loopback HTTP server."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "modules" / "voice"
sys.path.insert(0, str(VOICE))
sys.path.insert(0, str(ROOT / "lib"))
from crissa_voice import kokoro as K  # noqa: E402


# ---- the installer -------------------------------------------------------------------------------

@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CRISSA_TTS_ANY_PYTHON", raising=False)
    monkeypatch.setenv("CRISSA_VOICE_PYENV", "0")   # the lock path; the micromamba env is test_crissa_voice_pyenv's
    from crissa_voice import kokoro_setup
    S = importlib.reload(kokoro_setup)
    yield S
    monkeypatch.undo()
    importlib.reload(kokoro_setup)


def test_the_install_never_asks_for_misakis_espeak_extra(setup, monkeypatch):
    """With no hash lock for the machine (said on screen), the exact pins; the locks themselves are checked below."""
    cmds, said = [], []
    monkeypatch.setattr(setup.locks, "lock_path", lambda *a, **k: None)
    how = setup.make_env(run=lambda cmd: cmds.append(cmd), log=said.append)
    assert how.startswith("pins (no hash lock") and any("without a hash check" in x for x in said)
    flat = [" ".join(c) for c in cmds]
    assert not any("misaki[" in f for f in flat), "misaki[en] pulls phonemizer + espeakng-loader"
    assert any("--no-deps" in c and setup.MISAKI in c for c in cmds)
    assert not any(e in f for f in flat for e in setup.ESPEAK_DISTS)
    assert any(setup.SPACY_MODEL in c for c in cmds), "the spaCy model comes at install, never on a line"


def test_the_install_takes_the_hash_lock_when_the_machine_has_one(setup, monkeypatch):
    monkeypatch.setattr(setup.sys, "platform", "linux")
    monkeypatch.setattr(setup.locks.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(setup.locks.sys, "version_info", (3, 12, 0))
    cmds = []
    how = setup.make_env(run=lambda cmd: cmds.append(cmd), log=lambda m: None)
    assert how == "lock:kokoro-linux-x86_64-cpu-py312.txt"
    pip = [c for c in cmds if "pip" in c]
    assert len(pip) == 1 and "--require-hashes" in pip[0] and "--no-deps" in pip[0]
    assert pip[0][pip[0].index("--index-url") + 1].endswith("/whl/cpu"), "the CPU torch on Linux"


@pytest.mark.parametrize("lock", sorted((ROOT / "modules" / "voice" / "locks").glob("*-py312.txt")),
                         ids=lambda p: p.name)
def test_every_lock_pins_every_package_by_hash_and_carries_no_espeak(lock):
    import re
    text = lock.read_text()
    reqs = [ln for ln in text.splitlines() if ln and not ln.startswith((" ", "#", "-"))]
    assert reqs, lock.name
    for ln in reqs:
        assert "==" in ln or " @ " in ln, ln
    assert text.count("--hash=sha256:") >= len(reqs)
    names = {re.split(r"[=@ ]", ln, 1)[0].strip().lower().replace("_", "-") for ln in reqs}
    assert not names & {"espeakng-loader", "phonemizer", "phonemizer-fork", "espeak", "pykakasi", "wetext"}
    if lock.name.startswith("kokoro-"):
        assert "misaki" in names and "en-core-web-sm" in names and "num2words" in names
    if lock.name.startswith(("kokoro-linux", "asr-linux")):
        assert re.search(r"^torch==[\d.]+\+cpu", text, re.M), "the CPU torch build on Linux"


@pytest.mark.parametrize("dist", ["espeakng-loader", "espeakng_loader", "phonemizer-fork", "phonemizer"])
def test_an_env_holding_an_espeak_carrier_is_refused(setup, dist):
    class R:
        stdout = json.dumps([{"name": "numpy"}, {"name": dist}])
    with pytest.raises(RuntimeError, match="GPL espeak"):
        setup.check_no_espeak(run=lambda cmd: R())


def test_a_clean_env_passes(setup):
    class R:
        stdout = json.dumps([{"name": "numpy"}, {"name": "misaki"}, {"name": "spacy"}])
    setup.check_no_espeak(run=lambda cmd: R())


def test_models_come_from_the_pinned_revisions_and_only_what_is_needed(setup):
    fetched = []

    def fetch(url, dest):
        fetched.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "/api/models/" in url:
            files = ["onnx/model.onnx", "onnx/model_quantized.onnx", "tokenizer.json", "config.json",
                     "voices/af_heart.bin", "voices/bf_emma.bin", "README.md", "model.safetensors",
                     "english_to_phonemes.py", ".gitattributes", "generation_config.json"]
            dest.write_text(json.dumps([{"type": "file", "path": f} for f in files]))
        else:
            dest.write_text("x")
    setup.get_models(fetch=fetch)
    got = [u for u in fetched if "/resolve/" in u]
    assert all(setup.MODEL_REV in u for u in got if setup.MODEL_REPO in u)
    assert all(rev in u for repo, rev in setup.G2P.values() for u in got if repo in u)
    assert not any("model_quantized" in u or u.endswith((".md", ".py")) for u in got)
    assert (setup.MODEL / ".complete").read_text().strip() == setup.MODEL_REV
    for a in setup.G2P:
        assert (setup.g2p_dir(a) / ".complete").is_file()
    setup.DONE.write_text("{}")
    setup.ENV.joinpath("bin").mkdir(parents=True)
    setup.env_python().write_text("")
    assert setup.installed()


def test_unsupported_pythons_and_intel_macs_are_said_before_any_download(setup, monkeypatch):
    assert "3.9 to 3.12" in setup.unsupported((3, 13), "linux", "x86_64")
    assert "Apple Silicon" in setup.unsupported((3, 12), "darwin", "x86_64")
    assert setup.unsupported((3, 12), "darwin", "arm64") == ""
    monkeypatch.setenv("CRISSA_TTS_ANY_PYTHON", "1")
    assert setup.unsupported((3, 13), "darwin", "x86_64") == ""


# ---- the runner's pure parts ---------------------------------------------------------------------

def test_voice_ids_carry_the_accent():
    assert K.accent_of("af_heart") == "us" and K.accent_of("am_adam") == "us"
    assert K.accent_of("bf_emma") == "gb" and K.accent_of("bm_daniel") == "gb"


def test_long_phonemes_split_at_sentence_ends_and_fit_the_model():
    s = "ə " * 200 + ". " + "b " * 200 + "! " + "c" * 600
    out = K.chunks(s, limit=K.MAX_TOKENS)
    assert all(0 < len(c) <= K.MAX_TOKENS for c in out)
    assert "".join(out).replace(" ", "") == s.replace(" ", "")
    assert K.chunks("hˈɛlO. wˈɜɹld.") == ["hˈɛlO. wˈɜɹld."], "a short line is one piece"


# ---- the server, with a stand-in engine ----------------------------------------------------------

def _server_module():
    spec = importlib.util.spec_from_file_location("crissa_tts_server", VOICE / "scripts" / "crissa-tts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Engine:
    def __init__(self):
        self.calls = []

    def voices(self):
        return ["af_heart", "bf_emma"]

    def synth(self, text, voice, speed=1.0):
        if voice not in self.voices():
            raise KeyError("no Kokoro voice %r" % voice)
        self.calls.append((text, voice, speed))
        return b"\x05\x00" * 24000


@pytest.fixture()
def server():
    M = _server_module()
    eng = _Engine()
    M.RT.update(engine=eng, load_s=0.1)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), M.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], eng, M
    srv.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5)


def test_the_server_speaks_the_openai_contract(server):
    base, eng, _ = server
    with urllib.request.urlopen(base + "/health", timeout=5) as r:
        h = json.loads(r.read())
    assert h["ok"] and h["engine"] == "kokoro-82m"
    with _post(base + "/v1/audio/speech", {"input": "Hello there.", "voice": "bf_emma", "response_format": "pcm",
                                            "model": "kokoro", "speed": 9}) as r:
        assert r.headers["Content-Type"] == "audio/pcm" and len(r.read()) == 48000
    assert eng.calls == [("Hello there.", "bf_emma", 2.0)], "speed is clamped"
    with _post(base + "/v1/audio/speech", {"input": "Hi.", "response_format": "wav"}) as r:
        assert r.read()[:4] == b"RIFF"


def test_an_unknown_voice_is_a_400_with_its_name(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/v1/audio/speech", {"input": "Hi.", "voice": "zz_nobody"})
    assert e.value.code == 400 and "zz_nobody" in json.loads(e.value.read())["error"]


def test_the_port_follows_the_shared_table_env(monkeypatch):
    M = _server_module()
    for k in ("CRISSA_PORT_TTS", "CRISSA_TTS_PORT", "CRISSA_PORT_OFFSET"):
        monkeypatch.delenv(k, raising=False)
    assert M._port() == 8189
    monkeypatch.setenv("CRISSA_PORT_OFFSET", "1000")
    assert M._port() == 9189
    monkeypatch.setenv("CRISSA_PORT_TTS", "8297")
    assert M._port() == 8297


# ---- the seam: engine, face voice, default switch --------------------------------------------------

import crissa_tts as T  # noqa: E402


def test_the_kokoro_engine_asks_the_local_server_with_the_faces_voice(server, monkeypatch):
    base, eng, _ = server
    monkeypatch.setattr(T, "face_voice", lambda speaker, engine: {"lila": "bf_emma"}.get(speaker, ""))
    pcm, sr = T.synth("Good evening.", "lila", {"engine": "kokoro", "url": base + "/v1"})
    assert sr == T.HOUSE_SR and len(pcm) == 48000
    assert eng.calls[-1][1] == "bf_emma"
    T.synth("Good evening.", "crissa", {"engine": "kokoro", "url": base + "/v1"})
    assert eng.calls[-1][1] == "af_heart", "no face voice: the default voice"
    T.synth("Good evening.", "lila", {"engine": "kokoro", "url": base + "/v1", "voices": {"lila": "af_heart"}})
    assert eng.calls[-1][1] == "af_heart", "an agent.json voices map beats the manifest"



def test_a_zero_sample_rate_is_a_tts_error_and_the_backup_speaks(monkeypatch):
    monkeypatch.setattr(T, "_streamed", lambda text, cfg, speaker, timeout: (b"\0" * 4800, 0))
    monkeypatch.setattr(T, "_kokoro", lambda text, cfg, speaker, timeout: (b"\0" * 48000, T.HOUSE_SR))
    monkeypatch.setattr(T, "_note_spoke", lambda *a, **k: None)
    pcm, sr = T.synth("Good evening.", "crissa", {"engine": "turbo", "backup": ["kokoro"]})
    assert sr == T.HOUSE_SR and len(pcm) == 48000, "X-Sample-Rate: 0 is skipped, not a ZeroDivisionError"
    with pytest.raises(T.TTSError, match="sample rate"):
        T.synth("Good evening.", "crissa", {"engine": "turbo"})
    with pytest.raises(T.TTSError):
        T.to_house_rate(b"\0" * 100, 0)

def test_the_manifests_name_a_kokoro_voice_per_face():
    import avatar
    for face, accent in (("crissa", "us"), ("lila", "gb")):
        v = T.face_voice(face, "kokoro")
        assert v and K.accent_of(v) == accent, face
    assert T.face_voice("nobody-here", "kokoro") == "", "a missing face never raises"
    assert avatar  # imported by face_voice through lib


def test_the_kokoro_default_is_on_when_it_answers_and_never_on_the_house(server, monkeypatch, tmp_path):
    """ON since 2026-10-06 (crissatest: an unnamed voice on Linux fell through to the house's GPU ports)."""
    base, _, _ = server
    port = base.rsplit(":", 1)[1]
    env = {"CRISSA_PORT_TTS": port, "CRISSA_TTS_DEFAULT_KOKORO": "0"}
    monkeypatch.setattr(T, "mac_default", lambda e=None: False)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert T.config(env, tmp_path) == {}, "the kill switch: nothing named, no Kokoro"
    del env["CRISSA_TTS_DEFAULT_KOKORO"]
    got = T.config(env, tmp_path)
    assert got.get("engine") == "kokoro", \
        "nothing named and the voices ahead do not answer: Kokoro speaks (Pocket's backup, 2026-10-08)"
    assert T.config(dict(env, CRISSA_TTS="kokoro"), tmp_path)["engine"] == "kokoro", "named, it speaks"
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert T.config(env, tmp_path) == {}, "never the house"
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    env["CRISSA_PORT_TTS"] = "9"
    assert T.config(env, tmp_path) == {}, "a server that does not answer is not a default"


def test_a_pack_always_reaches_the_seam_and_the_house_only_when_named():
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    assert ('if _HOUSE and not (os.environ.get("CRISSA_TTS") or (_crissa_home_dir() / "agent.json").is_file() '
            'or _on_mac()):') in src
