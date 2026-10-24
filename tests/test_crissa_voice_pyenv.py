"""The voice module's own Python (2026-10-07, the User: Ubuntu ships 3.14 only, and every install line said python3.12).
Each engine's env is made by micromamba from conda-forge with the Python pinned in config; never the system Python.
No network here: micromamba and the runner are fakes."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "modules" / "voice"
sys.path.insert(0, str(VOICE))
from crissa_voice import egress, pocket_setup, pyenv  # noqa: E402


def test_the_pins_live_in_config_not_code():
    cfg = json.loads(pyenv.CONFIG.read_text())
    assert re.fullmatch(r"3\.12\.\d+", cfg["python"]) and cfg["channel"] == "conda-forge"
    mm = cfg["micromamba"]
    assert mm["version"] and all(re.fullmatch(r"[0-9a-f]{64}", v) for v in mm["sha256"].values())
    assert {"linux-64", "linux-aarch64", "osx-arm64"} <= set(mm["sha256"])
    src = (VOICE / "crissa_voice" / "pyenv.py").read_text()
    assert cfg["python"] not in src and mm["version"] not in src
    assert not any(v in src for v in mm["sha256"].values())
    assert pyenv.version() == (3, 12)


def test_a_state_home_copy_wins_key_by_key(tmp_path):
    (tmp_path / "voice-python.json").write_text(json.dumps({"python": "3.11.9", "micromamba": {"use_path": False}}))
    cfg = pyenv.config(tmp_path)
    assert cfg["python"] == "3.11.9" and cfg["micromamba"]["use_path"] is False and cfg["micromamba"]["sha256"]


def test_platform_names():
    assert pyenv.mamba_platform("linux", "x86_64") == "linux-64"
    assert pyenv.mamba_platform("linux", "aarch64") == "linux-aarch64"
    assert pyenv.mamba_platform("darwin", "arm64") == "osx-arm64"


def test_make_creates_a_pinned_conda_forge_env_never_the_running_python(tmp_path, monkeypatch):
    monkeypatch.delenv(pyenv.SWITCH, raising=False)
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "0")
    calls = []
    how = pyenv.make(tmp_path / "pocket" / "env", calls.append, log=lambda *_: None, home=tmp_path,
                     mm_fn=lambda home, log=None: Path("/fake/micromamba"), ver_fn=lambda p: None)
    cmd = calls[0]
    assert cmd[0] == "/fake/micromamba" and cmd[1] == "create"
    assert "--override-channels" in cmd and cmd[cmd.index("--channel") + 1] == "conda-forge"
    assert "python=%s" % pyenv.config()["python"] in cmd and "pip" in cmd
    assert cmd[cmd.index("--root-prefix") + 1] == str(tmp_path / "micromamba")
    assert cmd[cmd.index("--prefix") + 1] == str(tmp_path / "pocket" / "env")
    assert sys.executable not in cmd and "venv" not in cmd
    assert "micromamba" in how and "conda-forge" in how


def test_make_keeps_an_env_on_the_pinned_python(tmp_path, monkeypatch):
    monkeypatch.delenv(pyenv.SWITCH, raising=False)
    calls = []
    pyenv.make(tmp_path / "env", calls.append, log=lambda *_: None, home=tmp_path,
               mm_fn=lambda *a, **k: pytest.fail("no micromamba needed"), ver_fn=lambda p: (3, 12))
    assert calls == []


def test_make_remakes_an_env_on_the_wrong_python(tmp_path, monkeypatch):
    monkeypatch.delenv(pyenv.SWITCH, raising=False)
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "0")
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    calls = []
    pyenv.make(env, calls.append, log=lambda *_: None, home=tmp_path,
               mm_fn=lambda home, log=None: Path("/fake/mm"), ver_fn=lambda p: (3, 14))
    assert not env.exists() and calls and calls[0][1] == "create"


def test_kill_search_refuses_before_micromamba_runs(tmp_path, monkeypatch):
    monkeypatch.delenv(pyenv.SWITCH, raising=False)
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    calls = []
    with pytest.raises(egress.Refused):
        pyenv.make(tmp_path / "env", calls.append, log=lambda *_: None, home=tmp_path,
                   mm_fn=lambda *a, **k: pytest.fail("micromamba must not be fetched"), ver_fn=lambda p: None)
    assert calls == []


def test_the_micromamba_download_goes_through_kill_search(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_MICROMAMBA", raising=False)
    monkeypatch.setenv("CRISSA_SEARCH_KILL", "1")
    with pytest.raises(egress.Refused):
        pyenv.micromamba(tmp_path, log=lambda *_: None, which=lambda n: None, plat="linux", machine="x86_64")
    assert not (tmp_path / "micromamba" / "bin" / "micromamba").exists()


def test_micromamba_is_checked_by_sha256(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_MICROMAMBA", raising=False)
    good = b"#!/bin/sh\necho micromamba\n"
    (tmp_path / "voice-python.json").write_text(json.dumps(
        {"micromamba": {"sha256": {"linux-64": hashlib.sha256(good).hexdigest()}}}))
    urls = []

    def bad(url, dest):
        urls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"not it")
    with pytest.raises(RuntimeError, match="sha256"):
        pyenv.micromamba(tmp_path, fetch=bad, log=lambda *_: None, which=lambda n: None, plat="linux", machine="x86_64")
    assert not (tmp_path / "micromamba" / "bin" / "micromamba").exists()
    assert urls[0].startswith("https://github.com/mamba-org/micromamba-releases/releases/download/")
    assert urls[0].endswith("/micromamba-linux-64")

    def ok(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(good)
    got = pyenv.micromamba(tmp_path, fetch=ok, log=lambda *_: None, which=lambda n: None, plat="linux", machine="x86_64")
    assert got.read_bytes() == good and got.stat().st_mode & 0o111


def test_a_micromamba_on_path_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_MICROMAMBA", raising=False)
    assert pyenv.micromamba(tmp_path, fetch=lambda *a: pytest.fail("no fetch"), which=lambda n: "/opt/mm/micromamba") \
        == Path("/opt/mm/micromamba")


def test_the_switch_off_builds_from_the_running_python(tmp_path, monkeypatch):
    monkeypatch.setenv(pyenv.SWITCH, "0")
    calls = []
    pyenv.make(tmp_path / "env", calls.append, log=lambda *_: None, home=tmp_path)
    assert calls == [[sys.executable, "-m", "venv", str(tmp_path / "env")]]
    assert pyenv.target_version() == tuple(sys.version_info[:2])


def test_pocket_builds_its_env_through_pyenv_and_picks_the_lock_for_the_pinned_python(monkeypatch):
    monkeypatch.delenv(pyenv.SWITCH, raising=False)
    seen = {}
    monkeypatch.setattr(pyenv, "make", lambda prefix, run, log=print, **k: seen.setdefault("prefix", prefix) and "mm")
    monkeypatch.setattr(pocket_setup.locks, "pip_install",
                        lambda py, env, run, log, fallback, **k: seen.update(version=k.get("version")) or "lock")
    pocket_setup.make_env(run=lambda c: None, log=lambda *_: None)
    assert seen["prefix"] == pocket_setup.ENV and seen["version"] == (3, 12)
    # the installer itself may run on any Python 3: 3.14 is not refused while the env pins 3.12
    assert pocket_setup.unsupported(None, "linux", "x86_64") == ""


INSTALL_PATHS = [*sorted((VOICE / "crissa_voice").glob("*.py")), *sorted((VOICE / "scripts").glob("*.py")),
                 VOICE / "README.md", ROOT / "lib" / "crissa_tts.py", ROOT / "lib" / "crissa_voice_tiers.py",
                 ROOT / "config" / "crissa-voice-tiers.json", ROOT / "scripts" / "crissa-voice-tier.py",
                 ROOT / "lib" / "crissa_init.py"]


@pytest.mark.parametrize("path", INSTALL_PATHS, ids=lambda p: p.name)
def test_no_install_path_calls_python312(path):
    text = path.read_text()
    if path.name == "pyenv.py":      # its docstring tells the story of the bug
        text = text.split('"""', 2)[2]
    assert "python3.12" not in text, "%s still names python3.12" % path
