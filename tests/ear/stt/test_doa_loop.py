"""DOA loop: live puck beam, constant, not utterance-close."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crissa_ear.doa_loop import DoaLoop, should_publish


def test_should_publish_first_speech():
    assert (
        should_publish(
            now=10.0, last_pub=0.0, hz=1.0, ok=True, speech=True, mouth_open=False
        )
        is None
    )


def test_should_publish_quiet_beam():
    """Oracle lobe stays live. Quiet is still an az."""
    assert (
        should_publish(
            now=10.0, last_pub=0.0, hz=1.0, ok=True, speech=False, mouth_open=False
        )
        is None
    )
    assert (
        should_publish(
            now=10.0, last_pub=0.0, hz=1.0, ok=True, speech=True, mouth_open=True
        )
        == "mouth_open"
    )
    assert (
        should_publish(
            now=10.0, last_pub=0.0, hz=1.0, ok=False, speech=True, mouth_open=False
        )
        == "doa_bad"
    )


def test_one_hz_min_gap():
    assert (
        should_publish(
            now=10.4, last_pub=10.0, hz=1.0, ok=True, speech=True, mouth_open=False
        )
        == "min_gap"
    )
    assert (
        should_publish(
            now=11.0, last_pub=10.0, hz=1.0, ok=True, speech=True, mouth_open=False
        )
        is None
    )


def test_tick_posts_live_and_quiet():
    posted: list = []

    def read_speech():
        return {"ok": True, "az_deg": 233, "conf": 1.0, "speech": True, "held": False}

    def read_quiet():
        return {"ok": True, "az_deg": 40, "conf": 0.25, "speech": False}

    loop = DoaLoop(read=read_speech, post=lambda az, **k: posted.append((az, k)), mouth=lambda: False)
    assert loop.tick(now=100.0) is None
    assert posted[0][0] == 233.0
    assert posted[0][1]["speech"] is True
    assert "person_id" not in posted[0][1]

    posted.clear()
    loop_q = DoaLoop(read=read_quiet, post=lambda az, **k: posted.append((az, k)), mouth=lambda: False)
    assert loop_q.tick(now=100.0) is None
    assert loop_q.posts == 1
    assert posted[0][0] == 40.0
    assert posted[0][1]["speech"] is False
    assert loop_q.last_az == 40.0
