"""Empty-room refs: nearest pan → station, and the subtract map includes desk."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence.body_audit import (  # noqa: E402
    _SWEEP_REF_BY_PAN,
    harvest_station_stills,
    nearest_in_index,
    nearest_ref_for_pan,
    score_live_sweep,
)


def test_sweep_map_includes_desk_hour6() -> None:
    pans = {p: st for p, st in _SWEEP_REF_BY_PAN}
    assert pans[0] == 6
    assert pans[225000] == 8
    assert pans[450000] == 10
    assert pans[515000] == 12


def test_nearest_ref_prefers_existing_file(tmp_path: Path, monkeypatch) -> None:
    import crissa_presence.body_audit as ba

    monkeypatch.setattr(ba, "REFS_DIR", tmp_path)
    (tmp_path / "st8_tilt-15.jpg").write_bytes(b"x")
    (tmp_path / "st10_tilt-15.jpg").write_bytes(b"x")
    path, st, d = ba.nearest_ref_for_pan(230000, tilt_deg=-15)
    assert st == 8 and path is not None and path.name == "st8_tilt-15.jpg"
    assert d == 5000
    path, st, d = ba.nearest_ref_for_pan(440000, tilt_deg=-15)
    assert st == 10 and path is not None
    # hour 6 missing on disk → do not claim desk just because pan=0 is listed
    path, st, d = ba.nearest_ref_for_pan(0, tilt_deg=-15)
    assert path is None


def test_nearest_ref_desk_when_present(tmp_path: Path, monkeypatch) -> None:
    import crissa_presence.body_audit as ba

    monkeypatch.setattr(ba, "REFS_DIR", tmp_path)
    (tmp_path / "st6_tilt-15.jpg").write_bytes(b"x")
    path, st, d = ba.nearest_ref_for_pan(8000, tilt_deg=-15)
    assert st == 6 and d == 8000 and path is not None


def test_video_sweep_beats_station_still(tmp_path: Path, monkeypatch) -> None:
    import crissa_presence.body_audit as ba

    monkeypatch.setattr(ba, "REFS_DIR", tmp_path)
    (tmp_path / "st10_tilt-15.jpg").write_bytes(b"station")
    sweep = tmp_path / "sweep"
    sweep.mkdir()
    (sweep / "v000_pan0.jpg").write_bytes(b"v0")
    (sweep / "v010_pan448000.jpg").write_bytes(b"v10")
    (sweep / "v020_pan515000.jpg").write_bytes(b"v20")
    (sweep / "manifest.json").write_text(
        '{"frames":[{"pan":0,"file":"v000_pan0.jpg"},'
        '{"pan":448000,"file":"v010_pan448000.jpg"},'
        '{"pan":515000,"file":"v020_pan515000.jpg"}]}'
    )
    path, st, d = ba.nearest_ref_for_pan(450000, tilt_deg=-15)
    assert path is not None and path.name == "v010_pan448000.jpg"
    assert d == 2000
    assert st == 10


def test_harvest_station_stills_from_video(tmp_path: Path) -> None:
    sweep = tmp_path / "sweep"
    dest = tmp_path / "out"
    sweep.mkdir()
    (sweep / "v000_pan1200.jpg").write_bytes(b"desk")
    (sweep / "v008_pan224000.jpg").write_bytes(b"mich")
    (sweep / "v016_pan449000.jpg").write_bytes(b"door")
    (sweep / "v024_pan514000.jpg").write_bytes(b"seat")
    rows = harvest_station_stills(sweep, dest, tilt_deg=-15)
    names = {r["dest"]: r for r in rows}
    assert "st6_tilt-15.jpg" in names
    assert "st8_tilt-15.jpg" in names
    assert "st10_tilt-15.jpg" in names
    assert "st12_tilt-15.jpg" in names
    assert (dest / "st6_tilt-15.jpg").read_bytes() == b"desk"
    assert names["st10_tilt-15.jpg"]["d_pan"] == 1000
    # default harvest tilt follows camera-stops video_sweep (−5)
    dest5 = tmp_path / "out5"
    rows5 = harvest_station_stills(sweep, dest5)
    assert (dest5 / "st6_tilt-5.jpg").is_file()
    assert all(
        r["dest"].startswith("st6") or r["dest"].startswith("st8")
        or r["dest"].startswith("st10") or r["dest"].startswith("st12")
        or r["dest"].startswith("stchair")
        or "tilt-5" in r["dest"]
        or "tilt-15" in r["dest"]
        or "tilt-20" in r["dest"]
        for r in rows5
    )


def test_nearest_prefers_same_tilt() -> None:
    from pathlib import Path as P

    index = [
        (515000, -18000, P("/tmp/main.jpg")),
        (515000, -54000, P("/tmp/sit.jpg")),
    ]
    path, d = nearest_in_index(index, 515000, tilt=-54000)
    assert path is not None and path.name == "sit.jpg"
    path, d = nearest_in_index(index, 515000, tilt=-18000)
    assert path is not None and path.name == "main.jpg"


def test_score_live_sweep_max_not_sum(tmp_path: Path, monkeypatch) -> None:
    import cv2
    import numpy as np
    import crissa_presence.body_audit as ba

    monkeypatch.setattr(ba, "REFS_DIR", tmp_path)
    monkeypatch.setattr(ba, "LAST_DIR", tmp_path / "last")
    ref_d = tmp_path / "sweep"
    live_d = tmp_path / "live"
    ref_d.mkdir()
    live_d.mkdir()

    def jpg(path: Path, val: int) -> None:
        im = np.full((40, 60, 3), val, dtype=np.uint8)
        cv2.imwrite(str(path), im)

    jpg(ref_d / "v000_pan0_t-18000.jpg", 10)
    jpg(ref_d / "v001_pan225000_t-18000.jpg", 10)
    (ref_d / "manifest.json").write_text(
        '{"frames":[{"pan":0,"tilt":-18000,"file":"v000_pan0_t-18000.jpg"},'
        '{"pan":225000,"tilt":-18000,"file":"v001_pan225000_t-18000.jpg"}]}'
    )
    # live matches empty at desk; a blob at wanda
    jpg(live_d / "v000_pan0_t-18000.jpg", 10)
    blob = np.full((40, 60, 3), 10, dtype=np.uint8)
    blob[5:35, 10:50] = 200
    cv2.imwrite(str(live_d / "v001_pan225000_t-18000.jpg"), blob)
    (live_d / "manifest.json").write_text(
        '{"frames":[{"pan":0,"tilt":-18000,"file":"v000_pan0_t-18000.jpg"},'
        '{"pan":225000,"tilt":-18000,"file":"v001_pan225000_t-18000.jpg"}]}'
    )
    scored = score_live_sweep(live_d, ref_d)
    assert scored["ok"] is True
    assert scored["bodies_estimate"] >= 1
    assert scored["bodies_estimate"] <= scored["bodies_sum_raw"]
