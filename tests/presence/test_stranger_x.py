"""Security X# folders: stranger photos, grouped, not occupancy."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.oracle_event import get_event, open_visits, stamp_stranger
from crissa_presence.stranger_x import (
    display_of,
    list_strangers,
    match_xid,
    record_event,
)


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.normal(size=8).astype(np.float32)
    e /= np.linalg.norm(e)
    return e


def test_same_face_groups_into_one_x(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path))
    e = _unit(1)
    a = record_event(kind="enter", jpeg=b"\xff\xd8a", crop=b"\xff\xd8a", emb=e, source="test")
    b = record_event(kind="speech", jpeg=b"\xff\xd8b", crop=b"\xff\xd8b", emb=e, source="test")
    c = record_event(kind="hold_auth", jpeg=b"\xff\xd8c", crop=b"\xff\xd8c", emb=e, source="visit_lamp")
    assert c.get("kind") == "hold_auth"
    assert a["ok"] and b["ok"] and c["ok"]
    assert a["id"] == b["id"] == c["id"] == "x1"
    assert a["display"] == "X1"
    assert b["seq"] == 2
    folder = tmp_path / "X1"
    assert (folder / "events.jsonl").is_file()
    lines = (folder / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert "enter" in lines[0] and "speech" in lines[1] and "hold_auth" in lines[2]
    photos = list((folder / "photos").glob("*.jpg"))
    assert len(photos) >= 2
    rows = list_strangers(root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["n_events"] == 3
    assert rows[0]["display"] == "X1"


def test_different_faces_get_next_x(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path))
    a = record_event(kind="enter", jpeg=b"x", crop=b"x", emb=_unit(1))
    b = record_event(kind="enter", jpeg=b"y", crop=b"y", emb=_unit(99))
    assert a["id"] == "x1"
    assert b["id"] == "x2"
    assert (tmp_path / "X2" / "person.json").is_file()
    assert display_of("x4") == "X4"


def test_pytest_does_not_write_live_security(monkeypatch):
    monkeypatch.delenv("RAIL_STRANGER_ROOT", raising=False)
    out = record_event(kind="enter", jpeg=b"x", crop=b"x", emb=_unit(3))
    assert out.get("skipped") == "pytest"


def test_security_row_is_not_a_visit():
    from crissa_presence import oracle_event as oe

    oe._EVENTS.clear()
    out = stamp_stranger("x4", kind="enter", seq=1, photo="photos/001_enter.jpg")
    assert out["ok"] is True
    row = get_event("ev_x4")
    assert row["event"] == "security"
    assert row["person_id"] == "x4"
    assert row["short_id"] == "X4"
    assert row["counted"] is False
    assert not any(r.get("event_id") == "ev_x4" for r in open_visits())


def test_norm_none_and_nan_are_not_vectors():
    from crissa_presence.stranger_x import _norm

    assert _norm(None) is None
    assert _norm([float("nan")]) is None
    assert _norm(np.array([float("nan")], dtype=np.float32)) is None
    assert _norm(np.zeros(8, dtype=np.float32)) is None
    e = _norm(_unit(7))
    assert e is not None and e.size >= 2 and np.isfinite(e).all()


def test_record_without_emb_does_not_write_nan(tmp_path, monkeypatch):
    """No emb + no detectable jpeg → no_face, never dim-1 NaN files."""
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path))
    out = record_event(kind="enter", jpeg=b"\xff\xd8not-a-face", crop=b"tiny", emb=None)
    assert out.get("ok") is False
    faces = list(tmp_path.glob("X*/face/*.json"))
    assert faces == []


def test_match_xid_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path))
    xid, sim = match_xid(_unit(4), root=tmp_path)
    assert xid is None
    assert sim < 0.55
