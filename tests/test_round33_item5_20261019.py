"""Round 33 item 5 (.101 10-19: "a forced Setup re-run did not open her window itself (opened by hand, as before)"): a
whole walk started again on the player brings her window forward on /init and marks it hers. Voice ID again does not.
A pack only; kill CRISSA_SETUP_AGAIN_WINDOW=0."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402
import crissa_window as W  # noqa: E402


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    for k in ("CRISSA_SETUP_AGAIN_WINDOW", "CRISSA_OWN_WINDOW"):
        monkeypatch.delenv(k, raising=False)


def _run(act, body, got, show_out=None):
    shown, marked = [], []
    did = W.setup_again("http://127.0.0.1:8188", act, body, got,
                        show_=lambda u, **k: shown.append((u, k.get("front"))) or (show_out or {"ok": True}),
                        mark=lambda u, by: marked.append((u, by)))
    return did, shown, marked


def test_a_forced_walk_opens_her_window_on_setup(pack):
    for act, body in (("start", {"force": True}), ("restart", {})):
        did, shown, marked = _run(act, body, {"status": "running", "again": False})
        assert did == "opened"
        assert shown == [("http://127.0.0.1:8188/init", True)]
        assert marked == [("http://127.0.0.1:8188/init", "window")]


def test_not_for_a_plain_start_voice_id_again_or_a_walk_that_did_not_start(pack):
    assert _run("start", {}, {"status": "running"})[0] == ""                  # the first boot's: the launcher opens it
    assert _run("start", {"force": True}, {"status": "running", "again": True})[0] == ""
    assert _run("restart", {}, {"status": "aborted"})[0] == ""
    assert _run("abort", {}, {"status": "running"})[0] == ""
    did, _s, marked = _run("restart", {}, {"status": "running"}, show_out={"ok": False, "why": "no browser"})
    assert did == "not opened: no browser" and marked == []


def test_the_kill_and_the_house(pack, monkeypatch):
    monkeypatch.setenv("CRISSA_SETUP_AGAIN_WINDOW", "0")
    assert _run("restart", {}, {"status": "running"})[0] == "off"
    monkeypatch.delenv("CRISSA_SETUP_AGAIN_WINDOW")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert _run("restart", {}, {"status": "running"})[0] == "off"


def test_the_player_calls_it_after_the_walk_answers():
    c = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    assert "setup_window_again(str(body.get(\"action\") or \"\"), body, got)" in c
    fn = c.split("def setup_window_again", 1)[1].split("\ndef ", 1)[0]
    assert "crissa_home.is_house()" in fn and "threading.Thread(" in fn
