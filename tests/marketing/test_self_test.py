"""test_self_test: invoke the self-test path and assert rc=0 (card t_b1135d97).

The self-test path is ``--self-test`` on the entry point
(scripts/marketing_round.py): it runs the offline test set via subprocess
(``-m pytest tests/marketing``) and exits 0 on pass / 1 on fail. The child
run carries MARKETING_SELF_TEST=1, under which tests/marketing/conftest.py
blocks every socket at the socket layer -- so "must NOT hit the network" is
enforced by construction.

Recursion guard: under MARKETING_SELF_TEST=1 this file's entry-point test is
deselected, so ``--self-test`` cannot call itself forever.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def _load_conftest():
    spec = importlib.util.spec_from_file_location("mkt_conftest", HERE / "conftest.py")
    if spec is None or spec.loader is None:
        pytest.skip("cannot load conftest")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_self_test_flag_rc_zero():
    """The card's acceptance: --self-test exits 0 (fixture-only inside)."""
    if os.environ.get("MARKETING_SELF_TEST") == "1":
        pytest.skip("child of --self-test (recursion guard); the entry point measured itself")
    env = dict(os.environ, MARKETING_SELF_TEST="1")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "marketing_round.py"), "--self-test"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"self-test failed rc={proc.returncode}:\n{combined}"
    # Under the double -q (ini addopts + CLI) pytest suppresses the "N passed"
    # summary line; rc=0 plus progress dots is the real proof (rc 1 fail /
    # 2 interrupted / 4 usage error / 5 nothing collected).
    assert "self-test: pytest rc=0" in combined
    dots = sum(combined.count(c) for c in ".sE")
    assert dots >= 10, f"child collected too few tests ({dots}) — the set is shrinking"


def test_socket_guard_armed():
    """With MARKETING_SELF_TEST=1 the conftest guard blocks sockets."""
    module = _load_conftest()
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    saved = os.environ.get("MARKETING_SELF_TEST")
    os.environ["MARKETING_SELF_TEST"] = "1"
    try:
        assert module.guard_network(patcher) is True
        with pytest.raises(module.NetworkBlocked):
            socket.socket()
        with pytest.raises(module.NetworkBlocked):
            socket.create_connection(("127.0.0.1", 8096))
    finally:
        patcher.undo()
        if saved is None:
            del os.environ["MARKETING_SELF_TEST"]
        else:
            os.environ["MARKETING_SELF_TEST"] = saved


def test_guard_inert_without_marker():
    """Without the marker the guard is inert (tests stay portable).

    Skips under --self-test: the child run's autouse fixture has the socket
    block armed for the whole process, so "inert" is only provable in a
    plain run (this test then proves the armed path is opt-in, not global).
    """
    if os.environ.get("MARKETING_SELF_TEST") == "1":
        pytest.skip("child of --self-test runs with the socket block armed")
    module = _load_conftest()
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    saved = os.environ.pop("MARKETING_SELF_TEST", None)
    try:
        assert module.guard_network(patcher) is False
        s = None
        try:
            s = socket.socket()  # must NOT raise under no-marker
        finally:
            if s is not None:
                s.close()
            patcher.undo()
    finally:
        if saved is not None:
            os.environ["MARKETING_SELF_TEST"] = saved
