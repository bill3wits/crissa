"""A kit update's old cache folders are trimmed at start, in a pack only (the User 2026-10-16)."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import maps_disk  # noqa: E402
import wall_ascii_disk as D  # noqa: E402


def _look(root, name, code, age_s):
    d = root / name
    d.mkdir()
    (d / "framing.json").write_text(json.dumps({"code": code}))
    t = time.time() - age_s
    for p in (d, d / "framing.json"):
        os.utime(p, (t, t))
    return d


def test_old_looks_go_new_and_recent_stay(tmp_path):
    _look(tmp_path, "old", "c-old", 5 * 3600)
    _look(tmp_path, "mine", "c-new", 5 * 3600)
    _look(tmp_path, "recent", "c-old2", 600)
    (tmp_path / "notalook").mkdir()
    gone = D.trim_old_looks(tmp_path, "c-new")
    assert gone == ["old"]
    assert {p.name for p in tmp_path.iterdir()} == {"mine", "recent", "notalook"}


def test_old_maps_go_keep_and_adopt_source_stay(tmp_path):
    for n in ("fp-old", "fp-new", "fp-src"):
        d = tmp_path / n
        d.mkdir()
        (d / "ab").mkdir()
        t = time.time() - 5 * 3600
        os.utime(d / "ab", (t, t))
        os.utime(d, (t, t))
    (tmp_path / "adopt.json").write_text(json.dumps({"from": "fp-src", "to": "fp-new"}))
    gone = maps_disk.trim_old_prints(tmp_path, "fp-new", log=lambda *a: None)
    assert gone == ["fp-old"]


def test_house_never_trims(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert D.trim_old_on({}) is False and maps_disk.trim_old_on({}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert D.trim_old_on({}) is True and maps_disk.trim_old_on({}) is True
    assert D.trim_old_on({"CRISSA_ASCII_TRIM_OLD": "0"}) is False
    assert maps_disk.trim_old_on({"CRISSA_MAPS_TRIM_OLD": "0"}) is False
