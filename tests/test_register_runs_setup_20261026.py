"""No User yet, so Register is Setup; a cancel's words say what a cancel does (the User 2026-10-26).

His first 1.0.1 walk: Setup was cancelled, which by his 10-19 rule stores nothing, and then the menu's Register ID said
"Run Setup first" with no way to run it. And the Wall's Cancel plate promised "What Setup saved so far is kept".
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402
import crissa_init as zi  # noqa: E402
import crissa_menu as M  # noqa: E402


def _no_user(monkeypatch, *, status="aborted", done=False):
    monkeypatch.delenv("CRISSA_MENU_REGISTER_RUNS_SETUP", raising=False)
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    monkeypatch.setattr(zi, "_owner_id", lambda: "")
    monkeypatch.setattr(zi, "load_state", lambda: {"status": status})
    monkeypatch.setattr(zi, "marker", lambda: {"done": True} if done else {})
    calls = []
    monkeypatch.setattr(zi, "start", lambda force=False: calls.append(force) or {"status": "running"})
    return calls


def test_a_press_with_no_user_starts_setup(monkeypatch):
    calls = _no_user(monkeypatch)
    for action, body in (("voice_id", {"who": "name", "name": "Sam"}), ("ids_proof", {}), ("add_visitor", {})):
        got = M.act(action, body)
        assert got["ok"] is True and got["setup"] is True and got["note"] == M.SETUP_INSTEAD_NOTE, action
    assert calls == [True, True, True]                 # the walk's restart, as the Wall's RUN SETUP key


def test_never_while_setup_runs_after_it_finished_or_with_the_kill(monkeypatch):
    _no_user(monkeypatch, status="running")
    assert M.setup_first() is False
    _no_user(monkeypatch, done=True)
    assert M.setup_first() is False
    _no_user(monkeypatch)
    monkeypatch.setenv("CRISSA_MENU_REGISTER_RUNS_SETUP", "0")
    assert M.setup_first() is False
    monkeypatch.setattr(zi, "_owner_id", lambda: "sam")
    monkeypatch.delenv("CRISSA_MENU_REGISTER_RUNS_SETUP")
    assert M.setup_first() is False                    # a User exists: Register is Register


def test_the_cancel_words_say_nothing_is_kept():
    for rel in ("lib/init_v2_render.py", "display/crissa-init.html"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "What Setup saved so far is kept" not in src and "Nothing Setup saved is kept" in src, rel
