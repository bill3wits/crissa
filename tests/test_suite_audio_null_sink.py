"""The suite's audio goes to a null sink (the User 2026-10-19: tests greeted the room and beeped during every suite run)."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest


def test_every_player_this_session_starts_lands_on_the_null_sink():
    if os.environ.get("HOUSE_TEST_LIVE_AUDIO", "0") == "1":
        pytest.skip("HOUSE_TEST_LIVE_AUDIO=1: the old behaviour, on purpose")
    if not shutil.which("pactl"):
        pytest.skip("no pactl here")
    r = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        pytest.skip("no audio server here")
    assert os.environ.get("PULSE_SINK") == "house-tests"
    assert "house-tests" in r.stdout.split(), "conftest loads module-null-sink house-tests once and leaves it"
    # a child inherits it: that is how house-speak's paplay and the enroll beep stay silent
    out = subprocess.run(["sh", "-c", "echo $PULSE_SINK"], capture_output=True, text=True).stdout.strip()
    assert out == "house-tests"
