"""Marketing-round test conftest (card t_b1135d97).

No network, ever, in these tests -- fixtures only. When the entry point's
--self-test is running (MARKETING_SELF_TEST=1), every socket is additionally
blocked at the socket layer, so a test that accidentally reaches for the
network fails loudly instead of leaking a live fetch into "the only allowed
live fetch" budget (the sample run).

The block lives in ``guard_network()`` (plain, testable); the autouse
fixture applies it so it covers the whole run automatically.
"""
from __future__ import annotations

import os
import socket

import pytest


class NetworkBlocked(RuntimeError):
    """Raised when a test (or anything it runs) touches a socket."""


def _blocked_socket(*_args, **_kwargs):
    raise NetworkBlocked("marketing-round tests must not open sockets")


def _blocked_create_connection(*_args, **_kwargs):
    raise NetworkBlocked("marketing-round tests must not open connections")


def guard_network(monkeypatcher) -> bool:
    """Block socket creation while the guard is armed.

    ``monkeypatcher`` needs ``setattr(target, name, value)`` and
    ``undo()`` (pytest's ``MonkeyPatch``). Returns True when the guard is
    armed (MARKETING_SELF_TEST=1), False otherwise.
    """
    armed = os.environ.get("MARKETING_SELF_TEST") == "1"
    if armed:
        monkeypatcher.setattr(socket, "socket", _blocked_socket)
        monkeypatcher.setattr(socket, "create_connection", _blocked_create_connection)
    return armed


@pytest.fixture(autouse=True)
def _no_network(request):
    """Autouse: offline by construction. The socket-level block is only
    armed during --self-test runs; the fixture tests are offline either way."""
    monkeypatcher = request.getfixturevalue("monkeypatch")
    guard_network(monkeypatcher)
    yield
