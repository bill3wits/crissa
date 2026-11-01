"""Better mouths (2026-10-01): the aligner is off by default, downloads on first use in a pack, and never
touches the house. No network: the downloader and the env build are stand-ins."""
from __future__ import annotations

import base64
import importlib
import importlib.util
import io
import json
import sys
import tarfile
import threading
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


@pytest.fixture()
def pack(tmp_path, monkeypatch):
    """The module as a pack sees it: its own state home, not the house."""
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CRISSA_BETTER_MOUTHS", raising=False)
    import crissa_home
    import crissa_aligner
    importlib.reload(crissa_home)
    A = importlib.reload(crissa_aligner)
    monkeypatch.setattr(A.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(A, "running", lambda: False)
    monkeypatch.setattr(A, "ram_gb", lambda: 8.0)   # a small machine: unticked (the RAM default: its own test file)
    yield A
    A.stop()
    monkeypatch.undo()                       # the real state home back for every later test
    importlib.reload(crissa_home)
    importlib.reload(crissa_aligner)


def test_the_house_is_never_touched(monkeypatch):
    import crissa_aligner as A
    monkeypatch.setattr(A.crissa_home, "is_house", lambda *a, **k: True)
    assert A.applicable() is False
    assert A.line_env() == {}, "a house line's env is exactly what it was"
    out = A.set_better(True)
    assert out["ok"] is False and "own aligner" in out["error"]
    assert A.status()["clock"] == "letter" and A.status()["running"] is False


def test_a_pack_defaults_to_the_letter_clock_without_probing(pack):
    # on by default (the User 2026-10-10: Init installs it after the voice), but nothing is installed or running yet,
    # so every line keeps the letter clock and no port is probed
    assert pack.applicable() and pack.better() and not pack.installed()
    assert pack.line_env() == {"HOUSE_MOUTH_ALIGN": "0"}
    assert pack.status()["clock"] == "letter"


def test_the_kill_switch(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_BETTER_MOUTHS", "0")
    assert pack.applicable() is False and pack.line_env() == {}


def _fake_downloads(pack, monkeypatch, tmp_path, fail_env=False):
    tar = tmp_path / "mm.tar.bz2"
    with tarfile.open(tar, "w:bz2") as t:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("bin/micromamba"); info.size = len(data); info.mode = 0o755
        t.addfile(info, io.BytesIO(data))
    tree = [{"type": "file", "path": p} for p in (
        "acoustic/english_us_arpa/final.mdl", "dictionary/english_us_arpa.dict",
        "g2p/english_us_arpa/model.fst", "README.md")]
    fetched = []

    def fetch(url, dest, timeout=120.0):
        fetched.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "micro.mamba.pm" in url:
            dest.write_bytes(tar.read_bytes())
        elif "/api/models/" in url:
            dest.write_text(json.dumps(tree))
        else:
            dest.write_text("x")

    def run(cmd, **kw):  # the env build: no network, no solver
        assert "conda-forge" in cmd and any(c.startswith("kaldi=*=cpu") for c in cmd), cmd

        class R:
            returncode = 1 if fail_env else 0
            stdout = ""; stderr = "solver said no" if fail_env else ""
        if not fail_env:
            (pack.ENV / "bin").mkdir(parents=True, exist_ok=True)
            (pack.ENV / "bin" / "python").write_text("")
        return R()

    monkeypatch.setattr(pack, "_fetch", fetch)
    real_make_env = pack._make_env
    monkeypatch.setattr(pack, "_make_env", lambda: real_make_env(run=run))
    started = []
    monkeypatch.setattr(pack, "start", lambda: started.append(1) or True)
    return fetched, started


def test_switching_on_installs_once_then_starts(pack, monkeypatch, tmp_path):
    fetched, started = _fake_downloads(pack, monkeypatch, tmp_path)
    pack.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    pack.SETTINGS.write_text(json.dumps({"better": True}))
    pack._STATE.update(installing=True)
    pack.install()
    assert pack.installed() and pack._STATE["step"] == "done" and pack._STATE["error"] is None
    assert started == [1], "an install that finishes with the switch on starts the aligner"
    assert any(pack.MODEL_REV in u for u in fetched) and not any("README.md" in u for u in fetched)
    assert (pack.MODELS / ".complete").read_text().strip() == pack.MODEL_REV
    assert not (pack.ROOT / "model-tree.json").exists()
    rec = json.loads(pack.DONE.read_text())
    assert rec["model_rev"] == pack.MODEL_REV and rec["spec"] == pack.SPECS[pack._os()]


def test_a_failed_install_keeps_the_letter_clock_and_says_why(pack, monkeypatch, tmp_path):
    _fake_downloads(pack, monkeypatch, tmp_path, fail_env=True)
    pack.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    pack.SETTINGS.write_text(json.dumps({"better": True}))
    pack._STATE.update(installing=True)
    pack.install()
    st = pack.status()
    assert not st["installed"] and "solver said no" in st["error"] and st["installing"] is False
    assert pack.line_env() == {"HOUSE_MOUTH_ALIGN": "0"}


def test_a_running_aligner_reaches_the_line(pack, monkeypatch):
    pack.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    pack.SETTINGS.write_text(json.dumps({"better": True}))
    monkeypatch.setattr(pack, "running", lambda: True)
    env = pack.line_env()
    assert env == {"HOUSE_ALIGN_URL": "http://127.0.0.1:%d/align" % pack.PORT, "HOUSE_MOUTH_ALIGN": "auto"}
    assert pack.status()["clock"] == "aligned"


def test_switching_off_stops_the_aligner(pack, monkeypatch):
    stopped = []
    monkeypatch.setattr(pack, "stop", lambda: stopped.append(1))
    out = pack.set_better(False)
    assert out["ok"] and stopped == [1] and json.loads(pack.SETTINGS.read_text()) == {"better": False, "by": "you"}


# ---- the pack's aligner server ------------------------------------------------------------------

def _server_module():
    spec = importlib.util.spec_from_file_location("crissa_align_srv", ROOT / "scripts" / "crissa-align.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_the_server_keeps_rail_aligns_contract():
    m = _server_module()
    assert m.norm_words("Okay, so I'm 42 here") == (["okay", "so", "i'm", "here"], [0, 1, 2, 4])

    class Eng:
        def align(self, words, keep, path):
            with wave.open(path) as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 1
                n = w.getnframes()
            assert 15000 < n < 17000, n          # one second at 24 kHz resampled to 16 kHz
            return {"ok": True, "words": [{"w": w, "i": i, "a": 0.1 * i, "b": 0.1 * i + 0.05}
                                          for w, i in zip(words, keep)]}
    m._M.update(engine=Eng(), warm=True, err=None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        h = json.loads(urllib.request.urlopen(base + "/health", timeout=5).read())
        assert h["ok"] and h["warm"] and h["engine"] == "mfa"
        pcm = (b"\x00\x10" * 24000)
        req = urllib.request.Request(base + "/align", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"text": "hello there", "sr": 24000,
                                                      "pcm16_b64": base64.b64encode(pcm).decode()}).encode())
        out = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert out["ok"] and [w["w"] for w in out["words"]] == ["hello", "there"] and out["engine"] == "mfa"
        assert abs(out["audio_s"] - 1.0) < 0.01
    finally:
        srv.shutdown()


def test_a_cold_server_says_so_and_house_speak_waits():
    m = _server_module()
    m._M.update(engine=None, warm=False, err=None)
    assert m.align({"text": "hi", "pcm16_b64": ""})["error"] == "warming"


def test_the_conductor_wires_the_switch_and_the_line():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/mouths":') == 2, "one GET, one POST"
    env = src.split("def speak_timing_env", 1)[1].split("\ndef ", 1)[0]
    assert "**crissa_aligner.line_env()" in env
    post = src.split('if head == "/v1/mouths":')[2].split("if head", 1)[0]
    assert "connect_origin_ok" in post, "a foreign page cannot download 2 GB onto the machine"
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert 'id="mouths"' in page and "/v1/mouths" in page and "if (!m.applicable)" in page


# ---- 2026-10-05: one pin on both systems; a partial install is not "installed" ----

def test_the_mac_takes_the_linux_pin_and_its_kill_switch(pack, monkeypatch):
    assert pack.SPECS["darwin"] == pack.SPECS["linux"]
    assert any(s == "montreal-forced-aligner=3.4.2" for s in pack.SPECS["darwin"])
    monkeypatch.setattr(pack, "_os", lambda: "darwin")
    assert pack._specs() == pack.SPECS["linux"]
    monkeypatch.setenv("CRISSA_ALIGN_MAC_PIN", "0")
    assert pack._specs() == ["montreal-forced-aligner", "kaldi=*=cpu*"]
    assert pack.status()["tested_here"] is False, "a Mac is untested until measured there"


def _half_installed(pack, env_marker: bool, model_marker: bool):
    (pack.ENV / "bin").mkdir(parents=True, exist_ok=True)
    (pack.ENV / "bin" / "python").write_text("")
    if env_marker:
        (pack.ENV / pack.ENV_DONE).write_text("{}")
    (pack.MODELS / "acoustic").mkdir(parents=True, exist_ok=True)
    if model_marker:
        (pack.MODELS / ".complete").write_text(pack.MODEL_REV)
    pack.DONE.parent.mkdir(parents=True, exist_ok=True)
    pack.DONE.write_text("{}")


def test_a_half_built_env_or_model_is_not_installed(pack):
    _half_installed(pack, env_marker=False, model_marker=True)
    assert pack.installed() is False and pack.status()["clock"] == "letter"
    (pack.ENV / pack.ENV_DONE).write_text("{}")
    (pack.MODELS / ".complete").unlink()
    assert pack.installed() is False
    (pack.MODELS / ".complete").write_text(pack.MODEL_REV)
    assert pack.installed() is True


def test_a_half_built_env_is_built_again_from_nothing(pack):
    _half_installed(pack, env_marker=False, model_marker=True)
    (pack.ENV / "stale-file").write_text("x")
    calls = []

    def run(cmd, **kw):
        assert not (pack.ENV / "stale-file").exists(), "the partial env is removed before the new create"
        calls.append(cmd)
        (pack.ENV / "bin").mkdir(parents=True, exist_ok=True)
        (pack.ENV / "bin" / "python").write_text("")

        class R:
            returncode = 0; stdout = ""; stderr = ""
        return R()
    pack._make_env(run=run)
    assert len(calls) == 1 and (pack.ENV / pack.ENV_DONE).is_file()
    pack._make_env(run=run)
    assert len(calls) == 1, "a finished env is not built twice"
