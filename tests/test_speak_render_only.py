"""HOUSE_SPEAK_RENDER_ONLY (the User 2026-10-16, the phone app): a line made and its sheet posted at machine speed, with
nothing played, no gate, no lock. Off by default: every house line as before."""
from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "house-speak.py").read_text(encoding="utf-8")


def test_off_by_default():
    assert 'os.environ.get("HOUSE_SPEAK_RENDER_ONLY", "0")' in SRC


def test_every_render_only_branch_is_guarded():
    # no pacing, the whole sheet before exit, no gate, no player, no lock, no announcer cut, no unmute
    assert "while not RENDER_ONLY and p0 - time.time() > POST_AHEAD_S" in SRC
    assert "self._t.join(timeout=30.0 if RENDER_ONLY else 0.25)" in SRC
    assert "    if RENDER_ONLY:\n        return                          # nothing is on the air in this room" in SRC
    assert "contextlib.nullcontext() if RENDER_ONLY else _speech_lock_cm(180.0)" in SRC
    assert "            if RENDER_ONLY:\n                r = subprocess.CompletedProcess([], 0, \"\", \"\")" in SRC
    assert "    if not RENDER_ONLY:                 # render only: no sink, no announcer cut" in SRC
