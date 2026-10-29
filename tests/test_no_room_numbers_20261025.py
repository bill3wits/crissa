"""No shipped file knows a room (the User 2026-10-25).

The 1.0.0 stranger install rested a stranger's turning camera on THIS office's door (pan 450000, "door is 10"), so every
face check read "not seen" and Voice ID could not bank a turn: "Maybe Users could call me up and ask me to hard code
their doors when they install". A room's camera pans, door hour and seat are measured in Setup and live in the
person's own state; the code carries none of them.

This test refuses this office's room numbers (the ones the house's own room files hold, plus the ones that were
ever in them) anywhere in the kit's shipped sources: the modules, the overlay the export lays over the house's files,
and the lib/scripts the public packages ship. A test or a checkpoint may name them; a shipped file may not.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_round26_fixed_cam_20261016 import F  # noqa: E402,F401  (loads the presence package from modules/)

# This office's pans, past and present (camera-stops.json door / seats / sweep, room_geometry door_home_pan).
OFFICE_PANS = {150000, 225000, 300000, 432000, 450000, 515000}
HOUSE_ROOM_FILES = (
    Path.home() / "projects" / "rail-presence" / "data" / "camera-stops.json",
    Path.home() / "projects" / "rail-presence" / "data" / "room_geometry.json",
)
SHIPPED_EXT = {".py", ".json", ".html", ".js"}


def _house_pans() -> set[int]:
    """Whatever the house's own room files hold today (absent on another machine: the fixed set still stands)."""
    out: set[int] = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if "pan" in k and isinstance(v, int) and abs(v) >= 36000:     # ten degrees and up: a place, not noise
                    out.add(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for p in HOUSE_ROOM_FILES:
        try:
            walk(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return out


def _shipped_files() -> list[Path]:
    files: list[Path] = []
    for base in (ROOT / "modules", ROOT / "crissa" / "overlay"):
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in SHIPPED_EXT and "tests" not in p.parts and "__pycache__" not in p.parts:
                files.append(p)
    pkgs = json.loads((ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8"))["packages"]
    for name, pkg in pkgs.items():
        if not pkg.get("public"):
            continue
        for rel in (["lib/" + m for m in pkg.get("lib", [])] + ["scripts/" + s for s in pkg.get("scripts", [])]
                    + list(pkg.get("files") or [])):
            p = ROOT / rel
            if (ROOT / "crissa" / "overlay" / rel).is_file():
                continue                       # the export ships the overlay's copy of this file, not the house's
            if p.is_file() and p.suffix in SHIPPED_EXT:
                files.append(p)
    return sorted(set(files))


def test_no_shipped_file_carries_this_offices_pans():
    pans = OFFICE_PANS | _house_pans()
    pat = re.compile(r"(?<![\d.])(" + "|".join(str(p) for p in sorted(pans)) + r")(?![\d.])")
    hits = []
    for p in _shipped_files():
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pat.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "this office's room numbers in shipped files:\n" + "\n".join(hits)


def test_an_untaught_room_rests_on_the_cameras_own_forward(F, tmp_path, monkeypatch):
    from crissa_presence import room_heading as R
    monkeypatch.setattr(R, "_GEOM", tmp_path / "none.json")
    monkeypatch.setattr(R, "_site_heading", lambda: {})
    monkeypatch.delenv("RAIL_PRESENCE_DOOR_PAN", raising=False)
    monkeypatch.delenv("RAIL_PRESENCE_DOOR_TILT", raising=False)
    assert R.door_home_pose() == {"pan": 0, "tilt": 0, "zoom": 100}
    assert R.door_hour() is None and R.door_az_deg() is None and R.door_taught() is False
    assert R.pose_hflip() is False


def test_a_taught_door_is_read_live_from_setups_heading(F, tmp_path, monkeypatch):
    from crissa_presence import room_heading as R
    monkeypatch.setattr(R, "_GEOM", tmp_path / "none.json")
    site = {"door_hour": 2, "door_home_pan": -123456, "door_home_tilt": 3600, "door_home_zoom": 120}
    monkeypatch.setattr(R, "_site_heading", lambda: site)
    monkeypatch.delenv("RAIL_PRESENCE_DOOR_PAN", raising=False)
    assert R.door_home_pose() == {"pan": -123456, "tilt": 3600, "zoom": 120}
    assert R.door_hour() == 2 and R.door_taught() is True
    site["door_home_pan"] = 7200                       # Setup re-taught it: the next read has it, no restart
    assert R.door_home_pose()["pan"] == 7200
