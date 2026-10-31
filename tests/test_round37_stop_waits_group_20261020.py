"""Round 37 (2026-10-20): Crissa's stop waits until every process in the children's group is gone.

Round 36's cut 9 and rehearsal (B): under load the acceptance's "Stop" saw the desk Wall's prefill decoder
(house-conductor.py --clip-frames 0) still alive after the launcher had stopped, because the stop waited only for its own
children. Here a child starts a grandchild that takes 1.5 s to leave on TERM: after stop() the group is empty.
"""
import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_r37", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SLOW_GRANDCHILD = r"""
import signal, subprocess, sys, time
kid = subprocess.Popen([sys.executable, "-c", '''
import signal, time
def slow(*a):
    time.sleep(1.5)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, slow)
print("up", flush=True)
time.sleep(60)
'''], stdout=subprocess.PIPE)
kid.stdout.readline()
open(sys.argv[1], "w").write(str(kid.pid))
time.sleep(60)
"""


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups")
def test_stop_waits_for_a_slow_grandchild(tmp_path, monkeypatch):
    L = _launcher()
    monkeypatch.delenv("CRISSA_STOP_WAITS_GROUP", raising=False)
    g = L.Group(tmp_path / "logs")
    pidf = tmp_path / "grandchild.pid"
    g.spawn("slow", [sys.executable, "-c", SLOW_GRANDCHILD, str(pidf)], dict(os.environ))
    for _ in range(100):
        if pidf.is_file() and pidf.read_text():
            break
        time.sleep(0.05)
    kid = int(pidf.read_text())
    g.stop()
    assert not L._group_alive(g.pgid), "a process of the group outlived the stop"
    with pytest.raises(ProcessLookupError):
        os.kill(kid, 0)


def test_the_switch_and_the_probe():
    L = _launcher()
    assert L.stop_waits_group_on({}) and not L.stop_waits_group_on({"CRISSA_STOP_WAITS_GROUP": "0"})
    assert L._group_alive(None) is False and L._group_alive(os.getpgid(0)) is True
