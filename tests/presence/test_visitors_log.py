"""Visitors log is door IDs, not occupancy and not a live MJPEG."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.stranger_x import record_event
from crissa_presence.visitors_log import VISITORS_HTML, list_today


def _emb(seed: int):
    import numpy as np

    rng = np.random.default_rng(seed)
    e = rng.normal(size=8).astype(np.float32)
    e /= np.linalg.norm(e)
    return e


def test_list_today_uses_x_stills(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path))
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path / "blog"))
    a = record_event(kind="enter", jpeg=b"\xff\xd8a", crop=b"\xff\xd8a", emb=_emb(1))
    assert a.get("ok")
    out = list_today(visitor_today=[], faceid_root=tmp_path / "blog")
    assert out["ok"] is True
    ids = [p["id"] for p in out["people"]]
    assert "x1" in ids
    row = out["people"][0]
    assert row["photo"] == "/v1/strangers/x1/photo.jpg"
    assert row["kind"] == "stranger"


def test_speech_look_of_user_is_not_a_visitor(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path))
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "x"))
    day = time.strftime("%Y%m%d")
    (tmp_path / f"{day}.jsonl").write_text(
        '{"ts": 1, "name": "user", "source": "beam_look", "sim": 0.8}\n',
        encoding="utf-8",
    )
    out = list_today(visitor_today=[], faceid_root=tmp_path)
    assert out["people"] == []


def test_user_is_not_a_named_visitor(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "x"))
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path / "blog"))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    day = time.strftime("%Y-%m-%d")
    out = list_today(
        visitor_today=[
            {"person_id": "user", "ts": time.time(), "source": "door_face", "day": day},
            {"person_id": "xena", "ts": time.time(), "source": "door_face", "day": day},
        ],
        faceid_root=tmp_path / "blog",
    )
    ids = [p["id"] for p in out["people"]]
    assert "user" not in ids
    assert "xena" in ids


def test_named_guest_is_a_visitor(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "x"))
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path / "blog"))
    day = time.strftime("%Y-%m-%d")
    out = list_today(
        visitor_today=[{"person_id": "xena", "ts": time.time(), "source": "door_face", "day": day}],
        faceid_root=tmp_path / "blog",
    )
    assert any(p["id"] == "xena" for p in out["people"])


def test_seen_since_midnight_user_counts_as_one(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "x"))
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    day = time.strftime("%Y%m%d")
    (tmp_path / f"{day}.jsonl").write_text(
        '{"ts": 10, "name": "user", "source": "visor", "sim": 0.8}\n'
        '{"ts": 11, "name": "user", "source": "door_glance", "sim": 0.7}\n'
        '{"ts": 12, "name": "xena", "source": "door_glance", "sim": 0.6}\n',
        encoding="utf-8",
    )
    from crissa_presence.visitors_log import seen_since_midnight

    out = seen_since_midnight(visitor_today=[], faceid_root=tmp_path)
    assert out["n"] == 2
    assert out["user_seen"] is True
    assert "user" in out["ids"]
    assert "xena" in out["ids"]
    by = {r["id"]: r for r in out["people"]}
    assert by["user"]["ts"] == 10.0
    assert by["user"]["last_ts"] == 11.0
    assert by["xena"]["ts"] == 12.0


def test_seen_since_midnight_zero_on_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "x"))
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path / "blog"))
    from crissa_presence.visitors_log import seen_since_midnight

    out = seen_since_midnight(visitor_today=[], faceid_root=tmp_path / "blog")
    assert out["n"] == 0
    assert out["user_seen"] is False
    assert out["ids"] == []


def test_list_today_n_includes_user(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "x"))
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    day = time.strftime("%Y%m%d")
    (tmp_path / f"{day}.jsonl").write_text(
        '{"ts": 10, "name": "user", "source": "visor", "sim": 0.8}\n',
        encoding="utf-8",
    )
    out = list_today(visitor_today=[], faceid_root=tmp_path)
    assert out["n"] == 1
    assert out["user_seen"] is True
    assert "user" not in [p["id"] for p in out["people"]]


def test_visitors_html_is_not_the_hud():
    assert "SECURITY FILES" in VISITORS_HTML
    assert "/v1/visitors" in VISITORS_HTML
    assert "LIVE FACE ID" not in VISITORS_HTML
    assert "/v1/camera/mjpeg" not in VISITORS_HTML
    assert 'href="/visor"' in VISITORS_HTML


def test_the_page_shows_the_door_skip_counter():
    """The User 2026-10-09: skipped-as-you and strangers-tagged on /visitors, from /v1/visitors door_skips."""
    import re
    import shutil
    import subprocess

    assert "skips(v.door_skips)" in VISITORS_HTML
    node = shutil.which("node")
    if not node:
        return
    fn = re.search(r"function skips\(d\)\{.*?\n\}", VISITORS_HTML, re.S).group(0)
    js = fn + "\nconsole.log(skips({skipped:2,logged:1,skip_min_sim:0.4}));console.log(JSON.stringify(skips(undefined)));"
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20).stdout.splitlines()
    assert out == [" · skipped as you 2 (≥0.40) · strangers tagged 1", '""']
