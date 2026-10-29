"""Door rest subtract uses nightly live refs, not archive folders."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import door_background as db


def _touch_jpg(path: Path, age_sec: float = 0.0) -> None:
    path.write_bytes(b"\xff\xd8\xff\xd9")
    when = time.time() - age_sec
    os.utime(path, (when, when))


def test_ref_paths_uses_nightly_closed_not_superseded(tmp_path: Path, monkeypatch):
    live = tmp_path / "refs"
    audit = tmp_path / "audit"
    live.mkdir()
    audit.mkdir()
    arch = live / "_superseded-20250501-stale"
    arch.mkdir()
    _touch_jpg(live / "home-empty-closed.jpg")
    _touch_jpg(live / "home-empty-open.jpg")
    _touch_jpg(live / "room-empty-stop-wanda.jpg")
    _touch_jpg(arch / "door-empty-1.jpg")
    _touch_jpg(arch / "home-empty-closed.jpg")
    _touch_jpg(audit / "door_home_live.jpg")
    monkeypatch.setattr(db, "REF_DIR", live)
    monkeypatch.setattr(db, "BODY_AUDIT_DIR", audit)
    names = [p.name for p in db._ref_paths()]
    assert "home-empty-closed.jpg" in names
    assert "door_home_live.jpg" in names
    assert "home-empty-open.jpg" not in names  # closed rest wins; do not smear
    assert "room-empty-stop-wanda.jpg" not in names
    assert not any("_superseded" in str(p) for p in db._ref_paths())


def test_ref_paths_drops_three_month_stale(tmp_path: Path, monkeypatch):
    live = tmp_path / "refs"
    live.mkdir()
    _touch_jpg(live / "home-empty-closed.jpg", age_sec=100 * 86400)
    monkeypatch.setattr(db, "REF_DIR", live)
    monkeypatch.setattr(db, "BODY_AUDIT_DIR", tmp_path / "none")
    monkeypatch.setattr(db, "MAX_AGE_SEC", 90 * 86400)
    assert db._ref_paths() == []


def test_ref_paths_open_only_when_no_closed(tmp_path: Path, monkeypatch):
    live = tmp_path / "refs"
    live.mkdir()
    _touch_jpg(live / "home-empty-open.jpg")
    monkeypatch.setattr(db, "REF_DIR", live)
    monkeypatch.setattr(db, "BODY_AUDIT_DIR", tmp_path / "none")
    names = [p.name for p in db._ref_paths()]
    assert names == ["home-empty-open.jpg"]


def test_still_face_in_rest_is_a_picture():
    import crissa_presence.door_background as db

    db._PIC_TRACKS.clear()
    box = (40, 10, 52, 22)
    t0 = 1_000_000.0
    for i in range(8):
        db.note_rest_face_boxes([box], at_door=True, now=t0 + i * 0.2)
    assert db.is_still_picture(box, now=t0 + 1.6) is True
    walker = (8, 8, 28, 40)
    db.note_rest_face_boxes([walker], at_door=True, now=t0 + 1.7)
    assert db.is_still_picture(walker, now=t0 + 1.7) is False


def test_adopt_empty_this_frame_is_not_bodies(tmp_path: Path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import crissa_presence.door_background as db

    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(db, "REF_DIR", live)
    monkeypatch.setattr(db, "BODY_AUDIT_DIR", tmp_path / "audit")
    empty = np.full((48, 64, 3), 40, np.uint8)
    empty[10:22, 40:52] = 200  # a print that used to be "people"
    db._REST_RING.clear()
    out = db.adopt_empty_bgr(empty)
    assert out["ok"] is True
    assert (live / "home-empty-closed.jpg").is_file()
    assert db.is_furniture(empty, (40, 10, 52, 22)) is True
    walker = empty.copy()
    walker[8:40, 8:28] = 255
    assert db.is_furniture(walker, (8, 8, 28, 40)) is False


def test_rest_stream_blanks_print_keeps_walker(tmp_path: Path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    import crissa_presence.door_background as db

    monkeypatch.setattr(db, "REF_DIR", tmp_path / "none")
    monkeypatch.setattr(db, "BODY_AUDIT_DIR", tmp_path / "none")
    db._REST_RING.clear()
    db._CACHE.update({"key": None, "bg_gray": None, "n": 0})
    empty = np.full((48, 64, 3), 40, np.uint8)
    empty[10:22, 40:52] = 180
    walker = empty.copy()
    walker[8:40, 8:28] = 255
    db.note_rest_frames([empty, empty, empty], at_door=True)
    poster = (40, 10, 52, 22)
    assert db.is_furniture(empty, poster) is True
    assert db.is_furniture(walker, (8, 8, 28, 40)) is False
    db.note_rest_frames([walker], at_door=True)
    # walker must not become the background
    assert db.is_furniture(walker, (8, 8, 28, 40)) is False


def test_same_rest_frame_is_furniture(tmp_path: Path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    live = tmp_path / "refs"
    live.mkdir()
    img = np.full((48, 64, 3), 40, np.uint8)
    # "print" on the wall — a mid-tone patch that lives in the empty ref
    img[10:22, 40:52] = 180
    cv2.imwrite(str(live / "home-empty-closed.jpg"), img)
    ref = cv2.imread(str(live / "home-empty-closed.jpg"))
    monkeypatch.setattr(db, "REF_DIR", live)
    monkeypatch.setattr(db, "BODY_AUDIT_DIR", tmp_path / "none")
    monkeypatch.setattr(db, "MIN_REFS", 1)
    db._REST_RING.clear()
    db._CACHE.update({"key": None, "bg_gray": None, "n": 0})
    poster = (40, 10, 52, 22)
    assert db.is_furniture(ref, poster) is True
    person = ref.copy()
    person[8:40, 8:28] = 255  # someone walked in
    assert db.is_furniture(person, (8, 8, 28, 40)) is False
    # the print is still furniture while they stand elsewhere
    assert db.is_furniture(person, poster) is True
