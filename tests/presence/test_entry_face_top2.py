"""Two distinct door faces keep a runner-up (launch miss #3)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.entry_face import _keep_top2, best_face_in_pack


def test_second_distinct_face_is_kept():
    bucket: dict = {}
    _keep_top2(bucket, {"score": 10.0, "bbox": [0, 0, 40, 40], "name": "a"})
    _keep_top2(bucket, {"score": 8.0, "bbox": [80, 80, 120, 120], "name": "b"})
    assert bucket["name"] == "a"
    assert bucket["_second"]["name"] == "b"


def test_better_distinct_face_promotes_and_keeps_old():
    bucket: dict = {}
    _keep_top2(bucket, {"score": 8.0, "bbox": [0, 0, 40, 40], "name": "a"})
    _keep_top2(bucket, {"score": 12.0, "bbox": [80, 80, 120, 120], "name": "b"})
    assert bucket["name"] == "b"
    assert bucket["_second"]["name"] == "a"


def test_same_box_does_not_mint_a_second_person():
    bucket: dict = {}
    _keep_top2(bucket, {"score": 10.0, "bbox": [0, 0, 40, 40], "name": "a"})
    _keep_top2(bucket, {"score": 11.0, "bbox": [2, 2, 42, 42], "name": "a"})
    assert bucket["name"] == "a"
    assert bucket["score"] == 11.0
    assert "_second" not in bucket


class _Face:
    def __init__(self, w, h):
        self.bbox = [10.0, 10.0, 10.0 + w, 10.0 + h]
        self.det_score = 0.8
        self.normed_embedding = np.ones(8, dtype=np.float32) / np.sqrt(8)
        self.sex = 1


class _Eng:
    def detect(self, img):
        return [_Face(90, 140)]

    def gender(self, f):
        return "M", 0.9


def test_best_face_in_pack_demotes_not_drops(tmp_path: Path, monkeypatch):
    """door_view=False used to skip before detect — the 139px face never scored."""
    import cv2

    import crissa_presence.entry_face as ef

    (tmp_path / "f000.jpg").write_bytes(b"\xff\xd8x")
    monkeypatch.setattr(ef, "is_door_view", lambda img: False)
    monkeypatch.setattr(
        cv2, "imread", lambda p: np.zeros((180, 320, 3), dtype=np.uint8)
    )

    class _KB:
        def is_static(self, emb):
            return False, None

    monkeypatch.setattr(
        "crissa_presence.static_faces.killbook", lambda: _KB()
    )
    monkeypatch.setattr(
        "crissa_presence.static_faces.in_masked_zone", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "crissa_presence.static_faces.too_small_to_name", lambda *a, **k: False
    )
    monkeypatch.setattr(
        "crissa_presence.enrollments.load_enrolled", lambda: {}
    )
    monkeypatch.setattr(
        "crissa_presence.enrollments.load_person_stamps", lambda: {}
    )
    monkeypatch.setattr(
        "crissa_presence.enrollments.match", lambda *a, **k: (None, 0.0)
    )

    v = best_face_in_pack(tmp_path, engine=_Eng())
    assert v
    assert v.get("door_view") is False
    assert v.get("from_door_view") is False
    assert min(v["face_px"]) >= 60
