"""Round 23, the word aligner (the User 2026-10-15, .101 round 22: every line `lip clock: letter`, no aligner installed).

1. Setup's voice step installs the aligner even when the one-page walk has moved on before her voice is in
   (card_voice returned before _card_aligner when io.put said the run had left the card);
2. every start installs it once Setup is done, if better mouths are on by default and it is missing ("yes to adding it
   at startup"); a choice the person saved is theirs; never during Setup;
3. one throwaway line warms it before her first reply (cold 554-577 ms vs warm 232-250 ms on .101).
No download, no network: every hand is stood in.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


# ---- 1: the voice step -------------------------------------------------------------------------------------------
def _io(zi, put_ok: bool, calls: list):
    def put(**kw):
        calls.append(("put", kw))
        return put_ok if "state" in kw else True
    return zi.CardIO(lambda: {}, put, lambda retry=False: {"ok": True, "line": "Pocket is in"},
                     lambda: calls.append(("done", {})))


@pytest.mark.parametrize("put_ok", [True, False])
def test_the_aligner_step_runs_whether_or_not_the_walk_moved_on(monkeypatch, put_ok):
    import crissa_init as zi
    monkeypatch.delenv("CRISSA_ALIGNER_AFTER_VOICE_ALWAYS", raising=False)
    ran = []
    monkeypatch.setattr(zi, "aligner_after_voice", lambda: ran.append(1) or {"state": "installing"})
    calls = []
    zi.card_voice(_io(zi, put_ok, calls))
    assert ran == [1]
    assert (("done", {}) in calls) is put_ok, "the walk is advanced only from its own card"


def test_the_kill_brings_back_round_22(monkeypatch):
    import crissa_init as zi
    monkeypatch.setenv("CRISSA_ALIGNER_AFTER_VOICE_ALWAYS", "0")
    ran = []
    monkeypatch.setattr(zi, "aligner_after_voice", lambda: ran.append(1) or {"state": "on"})
    zi.card_voice(_io(zi, False, []))
    assert ran == []


# ---- 2 + 3: at start ---------------------------------------------------------------------------------------------
@pytest.fixture()
def A(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    for k in ("CRISSA_BETTER_MOUTHS", "CRISSA_BETTER_MOUTHS_DEFAULT", "CRISSA_ALIGNER_RAM_RULE", "CRISSA_ALIGNER_AT_START",
              "CRISSA_ALIGNER_WARM"):
        monkeypatch.delenv(k, raising=False)
    import crissa_home
    import crissa_aligner
    importlib.reload(crissa_home)
    mod = importlib.reload(crissa_aligner)
    monkeypatch.setattr(mod.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(mod, "applicable", lambda: True)
    monkeypatch.setattr(mod, "running", lambda: False)
    monkeypatch.setattr(mod, "default_on", lambda ram=None: True)
    started = []
    monkeypatch.setattr(mod.threading, "Thread", lambda target, name, daemon, **k: type(
        "T", (), {"start": lambda self: started.append(name)})())
    mod._started = started
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    mod._home_dir = tmp_path / "home"
    yield mod
    monkeypatch.undo()
    importlib.reload(crissa_home)
    importlib.reload(crissa_aligner)


def _setup(A, done: bool):
    (A._home_dir / "init.json").write_text(json.dumps({"done": done}))


def test_a_start_after_setup_installs_a_missing_aligner(A, monkeypatch):
    monkeypatch.setattr(A, "installed", lambda: False)
    _setup(A, True)
    A.boot()
    assert "better-mouths-install" in A._started
    assert json.loads(A.SETTINGS.read_text()) == {"better": True, "by": "init"}


def test_never_during_setup(A, monkeypatch):
    monkeypatch.setattr(A, "installed", lambda: False)
    _setup(A, False)
    A.boot()
    assert A._started == [] and not A.SETTINGS.exists()


def test_the_persons_own_choice_is_kept(A, monkeypatch):
    monkeypatch.setattr(A, "installed", lambda: False)
    _setup(A, True)
    A.SETTINGS.write_text(json.dumps({"better": False, "by": "you"}))
    A.boot()
    assert A._started == [] and json.loads(A.SETTINGS.read_text())["by"] == "you"


def test_the_start_check_has_a_kill(A, monkeypatch):
    monkeypatch.setattr(A, "installed", lambda: False)
    _setup(A, True)
    monkeypatch.setenv("CRISSA_ALIGNER_AT_START", "0")
    assert A.startup_install_due() is False


def test_an_installed_aligner_is_started_and_warmed(A, monkeypatch):
    monkeypatch.setattr(A, "installed", lambda: True)
    monkeypatch.setattr(A, "start", lambda: True)
    A.SETTINGS.write_text(json.dumps({"better": True, "by": "init"}))
    _setup(A, True)
    A.boot()
    assert A._started == ["aligner-warm"]


def test_the_warm_line_is_a_real_wav_and_runs_once(A, monkeypatch):
    import base64
    import io
    import socket
    import wave
    w = wave.open(io.BytesIO(base64.b64decode(A.warm_wav_b64())))
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 16000)

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _C())
    sent = []
    got = A.warm_once(post=lambda url, body: sent.append((url, json.loads(body))))
    assert got["state"] == "warm" and sent[0][0].endswith("/align") and sent[0][1]["text"]
    assert A.warm_once(post=lambda *a: sent.append(a))["state"] == "already" and len(sent) == 1


def test_no_aligner_answering_is_said_not_raised(A, monkeypatch):
    import socket

    def refuse(*a, **k):
        raise OSError("refused")
    monkeypatch.setattr(socket, "create_connection", refuse)
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(A.time, "time", lambda: float(next(clock)))
    assert A.warm_once(wait_s=20, sleep=lambda s: None)["state"] == "no aligner"
