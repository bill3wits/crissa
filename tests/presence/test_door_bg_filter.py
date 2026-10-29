"""C: a face that stays put across minutes, or sits on a resting-reference face, is door background (the User 2026-10-01)."""
from __future__ import annotations

import ast
from pathlib import Path

from crissa_presence import door_bg_filter as dbg

SEATED = [900, 300, 960, 360]  # a 60 px face


def _bg(tmp_path):
    return dbg.DoorBackground(tmp_path / "bg.json")


def test_default_mode_is_off(monkeypatch):
    monkeypatch.delenv(dbg.MODE_ENV, raising=False)
    assert dbg.mode() == "off"
    monkeypatch.setenv(dbg.MODE_ENV, "bogus")
    assert dbg.mode() == "off"
    monkeypatch.setenv(dbg.MODE_ENV, "on")
    assert dbg.mode() == "on"


def test_a_new_face_is_foreground(tmp_path):
    v = _bg(tmp_path).observe([SEATED], now=1000.0)
    assert v == [{"background": False, "why": "new", "track_age_s": 0.0, "track_hits": 0}]


def test_a_face_that_stays_minutes_becomes_background(tmp_path):
    b = _bg(tmp_path)
    t = 1000.0
    out = []
    while t <= 1000.0 + dbg.BG_SPAN_S + 30:
        out.append(b.observe([[x + 2 for x in SEATED]] if int(t) % 2 else [SEATED], now=t)[0])
        t += 15
    assert not out[0]["background"]
    assert not any(v["background"] for v in out if v["track_age_s"] < dbg.BG_SPAN_S)
    assert out[-1]["background"] and out[-1]["why"] == "stayed"


def test_a_face_that_moves_is_foreground_again(tmp_path):
    b = _bg(tmp_path)
    for i in range(12):
        b.observe([SEATED], now=1000.0 + 15 * i)
    assert b.observe([SEATED], now=1180.0)[0]["background"]
    moved = [1100, 300, 1160, 360]  # stood up and walked 200 px
    assert not b.observe([moved], now=1185.0)[0]["background"]


def test_a_face_that_comes_closer_is_foreground(tmp_path):
    b = _bg(tmp_path)
    for i in range(12):
        b.observe([SEATED], now=1000.0 + 15 * i)
    closer = [880, 280, 980, 380]  # same centre, 100 px: walking toward the camera
    assert not b.observe([closer], now=1185.0)[0]["background"]


def test_a_long_gap_forgets_the_seat(tmp_path):
    b = _bg(tmp_path)
    for i in range(12):
        b.observe([SEATED], now=1000.0 + 15 * i)
    later = 1165.0 + dbg.BG_GAP_S + 1
    v = b.observe([SEATED], now=later)[0]
    assert not v["background"] and v["why"] == "new"


def test_the_resting_reference_is_background_at_once(tmp_path):
    v = _bg(tmp_path).observe([SEATED], now=1000.0, static_at=lambda bb: (True, {"source_ref": "x"}))
    assert v[0]["background"] and v[0]["why"] == "reference"


def test_tracks_survive_a_restart(tmp_path):
    b = _bg(tmp_path)
    for i in range(12):
        b.observe([SEATED], now=1000.0 + 15 * i)
    b._save(2000.0, force=True)
    b2 = _bg(tmp_path)
    assert b2.observe([SEATED], now=1180.0)[0]["background"]


def test_a_face_is_never_its_own_witness(tmp_path):
    b = _bg(tmp_path)
    # three faces in one frame at one spot do not make a track of three sightings
    for _ in range(3):
        b.observe([SEATED, SEATED, SEATED], now=1000.0)
    assert b.tracks[0]["hits"] == 3
    assert not b.observe([SEATED], now=1001.0)[0]["background"]  # span too short


def test_the_service_splits_only_the_door_glance():
    src = (Path(__file__).resolve().parents[2] / "modules/presence/crissa_presence/service.py").read_text()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "_door_background_split"]
    assert len(calls) == 1  # one call site, and it is the door glance's
    assert 'if source == "door_glance":\n                    self._door_background_split(' in src
    # n_faces is replaced only in mode "on"
    assert 'if out.get("door_bg_mode") == "on":\n                out["n_faces"] = out["n_faces_fg"]' in src
