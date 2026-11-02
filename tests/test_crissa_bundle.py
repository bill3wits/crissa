"""Offline bundles (the User 2026-10-05): made where the internet is, installed where it is not.

The installs here run against fakes for micromamba, pip and the downloads, and every install-from-bundle test
fails if anything downloads; the real-file proof is in docs/checkpoints/offline-bundles-20261005."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "voice"))
sys.path.insert(0, str(ROOT / "modules" / "oracle"))
import crissa_bundle as B  # noqa: E402


def _folder(tmp_path, files=None):
    f = tmp_path / "piece"
    for rel, data in (files or {"a.bin": b"one", "sub/b.txt": b"two"}).items():
        (f / rel).parent.mkdir(parents=True, exist_ok=True)
        (f / rel).write_bytes(data)
    return f


# ---- the format ----------------------------------------------------------------------------------------------

def test_a_bundle_round_trips_and_lists_every_file_by_hash(tmp_path):
    tar = B.write(_folder(tmp_path), "oracle", pins={"x": 1})
    assert tar.name.endswith(B.SUFFIX)
    got, man = B.open_bundle(tar, "oracle")
    assert man["schema"] == B.SCHEMA and set(man["files"]) == {"a.bin", "sub/b.txt"}
    assert (got / "sub" / "b.txt").read_bytes() == b"two"
    shutil.rmtree(got)


@pytest.mark.parametrize("piece,python,why", [("asr", None, "for 'oracle', not 'asr'"),
                                              ("oracle", "2.7", None)])
def test_another_piece_is_refused_and_python_only_binds_when_recorded(tmp_path, piece, python, why):
    tar = B.write(_folder(tmp_path), "oracle", pins={})
    if why:
        with pytest.raises(B.BundleRefused, match=why):
            B.open_bundle(tar, piece, python=python)
    else:
        shutil.rmtree(B.open_bundle(tar, piece, python=python)[0])     # no python recorded: any Python


def test_another_platform_or_python_is_refused_but_any_platform_is_not(tmp_path):
    tar = B.write(_folder(tmp_path), "asr", pins={}, python="3.12", platform="windows-x86_64")
    with pytest.raises(B.BundleRefused, match="made on windows-x86_64"):
        B.open_bundle(tar, "asr")
    tar = B.write(_folder(tmp_path / "2"), "asr", pins={}, python="3.12")
    with pytest.raises(B.BundleRefused, match="Python 3.12"):
        B.open_bundle(tar, "asr", python="3.11")
    tar = B.write(_folder(tmp_path / "3"), "oracle", pins={}, platform="any")
    shutil.rmtree(B.open_bundle(tar, "oracle")[0])


def _retar(tar, tmp_path, edit):
    """Rewrite a bundle's tar with one change, keeping its manifest."""
    src = tmp_path / "x"
    with tarfile.open(tar) as t:
        t.extractall(src, filter="data")
    edit(src)
    out = tmp_path / "edited.tar"
    with tarfile.open(out, "w") as t:
        for p in sorted(src.rglob("*")):
            t.add(p, arcname=p.relative_to(src).as_posix(), recursive=False)
    return out


def test_a_changed_or_unlisted_file_refuses_the_whole_bundle(tmp_path):
    tar = B.write(_folder(tmp_path), "oracle", pins={})
    bad = _retar(tar, tmp_path / "c", lambda d: (d / "a.bin").write_bytes(b"ONE"))
    with pytest.raises(B.BundleRefused, match="does not match its hash: a.bin"):
        B.open_bundle(bad, "oracle")
    bad = _retar(tar, tmp_path / "u", lambda d: (d / "extra.sh").write_text("x"))
    with pytest.raises(B.BundleRefused, match="does not list: extra.sh"):
        B.open_bundle(bad, "oracle")


def test_a_path_out_of_the_bundle_or_a_link_is_refused(tmp_path):
    for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
        out = tmp_path / ("%s.tar" % kind.decode())
        with tarfile.open(out, "w") as t:
            ti = tarfile.TarInfo(name)
            ti.type = kind
            if kind == tarfile.SYMTYPE:
                ti.linkname = "/etc/passwd"
                t.addfile(ti)
            else:
                ti.size = 1
                t.addfile(ti, io.BytesIO(b"x"))
        with pytest.raises(B.BundleRefused):
            B.open_bundle(out, "oracle")


# ---- the aligner -----------------------------------------------------------------------------------------------

@pytest.fixture
def aligner(monkeypatch, tmp_path):
    import crissa_aligner as AL
    home = tmp_path / "home" / "aligner"
    for k, v in (("ROOT", home), ("ENV", home / "env"), ("MODELS", home / "english_us_arpa"),
                 ("MICROMAMBA", home / "bin" / "micromamba"), ("DONE", home / "installed.json")):
        monkeypatch.setattr(AL, k, v)
    monkeypatch.delenv("CRISSA_SEARCH_KILL", raising=False)
    return AL


def _fake_fetch(url, dest, *a, **k):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if "micromamba" in url:
        with tarfile.open(dest, "w:bz2") as t:
            ti = tarfile.TarInfo("bin/micromamba")
            ti.size = 4
            t.addfile(ti, io.BytesIO(b"mamb"))
    elif "/api/models/" in url:
        dest.write_text(json.dumps([{"type": "file", "path": "acoustic/m.zip"}, {"type": "file", "path": "dictionary/d.dict"}]))
    else:
        dest.write_bytes(b"model:" + url.encode())


class _Mamba:
    """micromamba as the installers call it: create (online, or --offline from the cache) and env export."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, env=None, **kw):
        self.calls.append(list(cmd))
        root = Path(env["MAMBA_ROOT_PREFIX"])
        assert env.get("CONDA_PKGS_DIRS") == str(root / "pkgs") and cmd[1] == "--no-rc", "a bundle borrows no cache"
        cmd = [cmd[0]] + cmd[2:]
        if cmd[1] == "create":
            prefix = Path(cmd[cmd.index("-p") + 1])
            url_dir = root / "pkgs" / "https" / "conda.anaconda.org" / "conda-forge" / "linux-64"
            if "--offline" in cmd:
                for u in Path(cmd[cmd.index("--file") + 1]).read_text().split()[1:]:
                    n = u.split("#")[0].rsplit("/", 1)[-1]
                    assert (url_dir / n).is_file() or (root / "pkgs" / n).is_file(), "offline create without the package"
            else:       # micromamba 2.x: one package under its URL, one flat (older), to cover both layouts
                url_dir.mkdir(parents=True, exist_ok=True)
                (url_dir / "mfa-3.4.2-py_0.conda").write_bytes(b"mfa")
                (root / "pkgs" / "kaldi-5.5-cpu_1.conda").write_bytes(b"kaldi")
            (prefix / "bin").mkdir(parents=True, exist_ok=True)
            (prefix / "bin" / "python").write_text("")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[1:3] == ["env", "export"]:
            out = "@EXPLICIT\n" + "".join("https://conda.anaconda.org/conda-forge/linux-64/%s#md5\n" % n
                                          for n in ("mfa-3.4.2-py_0.conda", "kaldi-5.5-cpu_1.conda"))
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        raise AssertionError(cmd)


def test_the_aligner_bundle_is_made_online_and_installed_offline(aligner, monkeypatch, tmp_path):
    AL = aligner
    monkeypatch.setattr(AL, "_fetch", _fake_fetch)
    mamba = _Mamba()
    tar = AL.build_bundle(tmp_path / "out", run=mamba, log=lambda *a: None)
    man = json.loads(tarfile.open(tar).extractfile(B.MANIFEST).read())
    assert man["piece"] == "aligner" and man["pins"]["model_rev"] == AL.MODEL_REV and man["pins"]["packages"] == 2
    assert {"bin/micromamba", "lock.txt", "pkgs/https/conda.anaconda.org/conda-forge/linux-64/mfa-3.4.2-py_0.conda",
            "pkgs/kaldi-5.5-cpu_1.conda", "english_us_arpa/acoustic/m.zip"} <= set(man["files"])
    assert [p.name for p in (tmp_path / "out").iterdir()] == [tar.name]           # the scratch is gone

    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")                                   # the offline machine
    monkeypatch.setattr(AL, "_fetch", lambda *a, **k: pytest.fail("downloaded during a bundle install"))
    mamba2 = _Mamba()
    got = AL.install_bundle(tar, run=mamba2, log=lambda *a: None)
    assert got == {"ok": True, "installed": True}
    assert "--offline" in mamba2.calls[0] and "-c" not in mamba2.calls[0]
    assert not (AL.ROOT / "mamba" / "pkgs").exists()                                 # the cache is not kept
    assert json.loads(AL.DONE.read_text())["from"] == "bundle"


def test_making_a_bundle_needs_the_internet_so_kill_search_refuses_it(aligner, monkeypatch, tmp_path):
    import crissa_egress
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    with pytest.raises(crissa_egress.Refused):
        aligner.build_bundle(tmp_path / "out", run=_Mamba(), log=lambda *a: None)


def test_a_bundle_with_another_model_is_refused_before_anything_is_written(aligner, monkeypatch, tmp_path):
    AL = aligner
    monkeypatch.setattr(AL, "_fetch", _fake_fetch)
    monkeypatch.setattr(AL, "MODEL_REV", "0" * 40)
    tar = AL.build_bundle(tmp_path / "out", run=_Mamba(), log=lambda *a: None)
    monkeypatch.setattr(AL, "MODEL_REV", "1" * 40)
    with pytest.raises(B.BundleRefused, match="model"):
        AL.install_bundle(tar, run=_Mamba(), log=lambda *a: None)
    assert not AL.ROOT.exists()


# ---- speech in and the face check ------------------------------------------------------------------------------

def test_the_speech_in_bundle_carries_wheels_for_this_python_and_installs_with_no_index(monkeypatch, tmp_path):
    from crissa_voice import asr_setup as S
    monkeypatch.setenv("CRISSA_ASR_ANY_PYTHON", "1")
    monkeypatch.delenv("CRISSA_SEARCH_KILL", raising=False)
    home = tmp_path / "home" / "asr"
    for k, v in (("ROOT", home), ("ENV", home / "env"), ("MODEL", home / "model"), ("BUILD", home / "build"),
                 ("DONE", home / "installed.json")):
        monkeypatch.setattr(S, k, v)
    calls = []

    def run(cmd):
        calls.append(cmd)
        if "download" in cmd:
            d = Path(cmd[cmd.index("--dest") + 1])
            (d / ("w%d-cp-any.whl" % len(calls))).write_bytes(b"wheel")
        if cmd[1:3] == ["-m", "venv"]:
            (Path(cmd[3]) / "bin").mkdir(parents=True)
            (Path(cmd[3]) / "bin" / "python").write_text("")

    def fetch(url, dest, *a):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "/api/models/" in url:
            dest.write_text(json.dumps([{"type": "file", "path": p} for p in
                                        ("runtime_manifest.json", "infer_ark_audio_onnx.py", "llm_kv_a.json")]))
        else:
            dest.write_text("x")
    tar = S.build_bundle(tmp_path / "out", run=run, fetch=fetch, log=lambda *a: None)
    man = json.loads(tarfile.open(tar).extractfile(B.MANIFEST).read())
    assert man["python"] == B.python_tag() and sum(1 for f in man["files"] if f.startswith("wheels/")) == 2
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    calls.clear()
    rec = S.install_bundle(tar, run=run, log=lambda *a: None)
    pip = [c for c in calls if "install" in c][0]
    assert "--no-index" in pip and "--find-links" in pip and rec["from"] == "bundle"
    assert (S.BUILD / "llm_kv_a.json").is_file() and (S.MODEL / "runtime_manifest.json").is_file()


def test_the_face_check_bundle_is_its_models_on_any_platform(monkeypatch, tmp_path):
    from crissa_oracle import setup as O
    monkeypatch.delenv("CRISSA_SEARCH_KILL", raising=False)
    models = tmp_path / "home" / "oracle" / "models"
    monkeypatch.setattr(O, "MODELS", models)
    monkeypatch.setattr(O, "LANDMARKER", models / "face_landmarker.task")
    monkeypatch.setattr(O, "AURAFACE", models / "auraface" / O.AURAFACE_FILE)

    def fetch(url, dest, *a):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(url.encode())
    tar = O.build_bundle(tmp_path / "out", fetch=fetch, env={"CRISSA_ORACLE_AURAFACE": "1"}, log=lambda *a: None)
    assert json.loads(tarfile.open(tar).extractfile(B.MANIFEST).read())["platform"] == "any"
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    assert O.install_bundle(tar, log=lambda *a: None) == {"landmarker": True, "auraface": True, "from": "bundle"}


@pytest.mark.parametrize("mod,pkg,fn", [("voice", "crissa_voice", "asr_setup"), ("oracle", "crissa_oracle", "setup"),
                                        ("voice", "crissa_voice", "kokoro_setup")])
def test_a_module_pack_carries_its_own_bundle_code(tmp_path, mod, pkg, fn):
    vend = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]["crissa-" + mod]["vendor"]
    assert "lib/crissa_bundle.py" in vend
    out = tmp_path / "pack" / pkg
    shutil.copytree(ROOT / "modules" / mod / pkg, out, ignore=shutil.ignore_patterns("__pycache__"))
    for src, dst in vend.items():
        shutil.copy2(ROOT / src, out / Path(dst).name)
    code = ("import sys, json; sys.path.insert(0, %r); from %s import %s as m; "
            "print(json.dumps(m._bundle_mod().__file__.startswith(%r)))" % (str(out.parent), pkg, fn, str(out)))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env={"CRISSA_HOME": str(tmp_path / "h"), "PATH": "/usr/bin:/bin"}, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) is True


# ---- the Kokoro voice (the User 2026-10-05: "the Kokoro offline build/install pair like the other three") ----------

@pytest.fixture
def kokoro(monkeypatch, tmp_path):
    from crissa_voice import kokoro_setup as K
    monkeypatch.setenv("CRISSA_TTS_ANY_PYTHON", "1")
    monkeypatch.delenv("CRISSA_SEARCH_KILL", raising=False)
    home = tmp_path / "home" / "tts"
    for k, v in (("ROOT", home), ("ENV", home / "env"), ("MODEL", home / "model"), ("DONE", home / "installed.json")):
        monkeypatch.setattr(K, k, v)
    monkeypatch.setattr(K, "g2p_dir", lambda accent, home=home: home / ("g2p-" + accent))
    return K


class _Pip:
    """pip and venv, faked: `wheel` drops a wheel into --wheel-dir, `venv` makes a python, `list` names `held`."""

    def __init__(self, held=()):
        self.calls, self.held = [], list(held)

    def __call__(self, cmd):
        self.calls.append(cmd)
        if "wheel" in cmd:
            d = Path(cmd[cmd.index("--wheel-dir") + 1])
            (d / ("w%d-py3-none-any.whl" % len(self.calls))).write_bytes(b"wheel")
        if cmd[1:3] == ["-m", "venv"]:
            (Path(cmd[3]) / "bin").mkdir(parents=True)
            (Path(cmd[3]) / "bin" / "python").write_text("")
        if "list" in cmd:
            return SimpleNamespace(stdout=json.dumps([{"name": n} for n in ["torch", "misaki"] + self.held]))
        return SimpleNamespace(stdout="")


def _kokoro_fetch(url, dest, *a):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if "/api/models/" in url:
        files = (["onnx/model.onnx", "tokenizer.json", "config.json", "voices/af_heart.bin", "onnx/model_q8.onnx"]
                 if "Kokoro" in url else ["config.json", "generation_config.json", "model.safetensors"])
        dest.write_text(json.dumps([{"type": "file", "path": p} for p in files]))
    else:
        dest.write_bytes(b"file:" + url.encode())


def test_the_kokoro_bundle_carries_wheels_and_its_three_models_and_installs_with_no_index(kokoro, monkeypatch, tmp_path):
    K = kokoro
    pip = _Pip()
    tar = K.build_bundle(tmp_path / "out", run=pip, fetch=_kokoro_fetch, log=lambda *a: None)
    assert tar.name == "kokoro-%s-py%s%s" % (B.platform_tag(), B.python_tag(), B.SUFFIX)
    man = json.loads(tarfile.open(tar).extractfile(B.MANIFEST).read())
    assert man["piece"] == "kokoro" and man["python"] == B.python_tag()
    assert sum(1 for f in man["files"] if f.startswith("wheels/")) == 3
    assert "model/onnx/model.onnx" in man["files"] and "model/onnx/model_q8.onnx" not in man["files"]
    assert {"g2p-us/model.safetensors", "g2p-gb/model.safetensors"} <= set(man["files"])
    wheel_calls = [c for c in pip.calls if "wheel" in c]
    assert any("--no-deps" in c and K.MISAKI in c for c in wheel_calls), "misaki without its dependencies"
    assert not any("misaki[en]" in " ".join(c) for c in pip.calls), "never the extra that brings espeak"
    assert not K.ROOT.exists(), "making a bundle never writes this machine's own install"

    def no_download(*a, **k):
        raise AssertionError("an offline install downloaded something")
    monkeypatch.setattr(K, "_fetch", no_download)
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    pip2 = _Pip()
    rec = K.install_bundle(tar, run=pip2, log=lambda *a: None)
    installs = [c for c in pip2.calls if "install" in c]
    assert installs and all("--no-index" in c and "--find-links" in c for c in installs)
    assert any(K.SPACY_MODEL_REQ in c for c in installs) and any("--no-deps" in c and K.MISAKI in c for c in installs)
    assert rec["from"] == "bundle" and rec["rev"] == K.MODEL_REV
    assert (K.MODEL / "onnx" / "model.onnx").is_file() and (K.MODEL / ".complete").is_file()
    assert all((K.g2p_dir(a) / ".complete").is_file() for a in K.G2P) and K.DONE.is_file()


def test_kill_search_refuses_making_a_kokoro_bundle_and_the_online_install(kokoro, monkeypatch, tmp_path):
    K = kokoro
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    for go in (lambda: K.build_bundle(tmp_path / "out", run=_Pip(), fetch=_kokoro_fetch, log=lambda *a: None),
               lambda: K.install(log=lambda *a: None)):
        with pytest.raises(Exception) as e:
            go()
        assert "kill-search" in str(e.value)
    assert not K.ROOT.exists()


def test_a_kokoro_bundle_with_other_models_is_refused_before_anything_is_written(kokoro, monkeypatch, tmp_path):
    K = kokoro
    tar = K.build_bundle(tmp_path / "out", run=_Pip(), fetch=_kokoro_fetch, log=lambda *a: None)
    monkeypatch.setattr(K, "G2P", dict(K.G2P, us=(K.G2P["us"][0], "0" * 40)))
    with pytest.raises(B.BundleRefused):
        K.install_bundle(tar, run=_Pip(), log=lambda *a: None)
    assert not K.ENV.exists() and not K.MODEL.exists()


def test_a_kokoro_bundle_install_still_refuses_espeak(kokoro, tmp_path):
    K = kokoro
    tar = K.build_bundle(tmp_path / "out", run=_Pip(), fetch=_kokoro_fetch, log=lambda *a: None)
    with pytest.raises(RuntimeError) as e:
        K.install_bundle(tar, run=_Pip(held=["phonemizer"]), log=lambda *a: None)
    assert "espeak" in str(e.value) and not K.DONE.exists()
