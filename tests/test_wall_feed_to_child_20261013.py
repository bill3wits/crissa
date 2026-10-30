"""The Wall hands its resolved player to the film child (2026-10-13): round 16's "no player, no build" reads the
child's environment, and the house's Wall names its player only in the house config, so the house never pre-filled."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wall_film_proc as W  # noqa: E402


def test_no_feed_inherits():
    assert W.child_env(None, env={}) is None
    assert W.child_env("", env={}) is None


def test_a_feed_is_handed_down_when_nothing_names_one():
    env = W.child_env("http://127.0.0.1:8100", env={"PATH": "/bin"})
    assert env == {"PATH": "/bin", "HOUSE_WALL_FEED": "http://127.0.0.1:8100"}


def test_an_existing_name_wins_so_a_pack_is_unchanged():
    assert W.child_env("http://x", env={"HOUSE_WALL_FEED": "http://y"}) is None
    assert W.child_env("http://x", env={"CRISSA_PLAYER": "http://z"}) is None


def test_the_kill_switch_keeps_round_16():
    assert W.child_env("http://x", env={"HOUSE_WALL_FEED_TO_CHILD": "0"}) is None


def test_the_spawn_carries_it(monkeypatch, tmp_path):
    seen = {}

    class P:
        pid = 4242
        stdin = open(os.devnull, "wb")

        def poll(self):
            return None

    def fake_popen(argv, **kw):
        seen.update(kw)
        return P()

    monkeypatch.setattr(W, "frame_dir", lambda env=None: str(tmp_path))
    monkeypatch.setattr(W.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(W.os, "set_blocking", lambda *a: None)
    monkeypatch.delenv("HOUSE_WALL_FEED", raising=False)
    monkeypatch.delenv("CRISSA_PLAYER", raising=False)
    W.FilmProcess(8, 8, feed="http://127.0.0.1:8100")
    assert seen["env"]["HOUSE_WALL_FEED"] == "http://127.0.0.1:8100"
    seen.clear()
    W.FilmProcess(8, 8)
    assert seen["env"] is None


def test_the_wall_passes_its_feed():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert "wall_film_proc.FilmProcess(W, H, log=log, feed=FEED)" in src
