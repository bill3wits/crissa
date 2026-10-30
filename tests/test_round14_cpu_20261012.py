"""Round 14 (2026-10-12, .101: `systemd --user` at 40-90 %). The desk Wall polls the feed server's /v1/house about three
times a second; each answer asked systemd about house-wall-ascii.service and crissa-display.service, which a pack does not
have, so the manager loaded and dropped each one: 58 and 56 times in 20 s (`busctl monitor`, UnitNew / UnitRemoved). A
pack now answers "not active" (the answer it always got) without asking; the house asks as before."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_home  # noqa: E402


def _feed(monkeypatch, house: bool):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: house)
    spec = importlib.util.spec_from_file_location("vo_feed_r14", ROOT / "scripts" / "voiceonly-home-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _record_runs(monkeypatch, m):
    asked = []

    class _R:
        stdout = "active\n"

    def run(argv, *a, **k):
        asked.append(list(argv))
        return _R()
    monkeypatch.setattr(m.subprocess, "run", run)
    return asked


def test_a_pack_never_asks_systemd_about_the_house_wall(monkeypatch):
    m = _feed(monkeypatch, house=False)
    asked = _record_runs(monkeypatch, m)
    monkeypatch.delenv("CRISSA_FEED_HOUSE_UNITS", raising=False)
    assert m.wallpaper_active() is False and asked == []


def test_the_house_still_asks_and_the_switch_brings_the_question_back(monkeypatch):
    m = _feed(monkeypatch, house=True)
    asked = _record_runs(monkeypatch, m)
    assert m.wallpaper_active() is True and asked == [["systemctl", "--user", "is-active", "house-wall-ascii.service"]]
    m2 = _feed(monkeypatch, house=False)
    asked2 = _record_runs(monkeypatch, m2)
    monkeypatch.setenv("CRISSA_FEED_HOUSE_UNITS", "1")
    assert m2.wallpaper_active() is True and len(asked2) == 1


def test_the_gpu_snapshot_asks_about_crissa_display_only_on_the_house():
    src = (ROOT / "scripts" / "voiceonly-home-server.py").read_text(encoding="utf-8")
    body = src[src.index("def gpu_mode_snapshot()"):src.index("def gpu_mode_snapshot()") + 2000]
    assert "glass_mode.is_active(glass_mode.BRAVE) if pack_asks_house_units() else False" in body
