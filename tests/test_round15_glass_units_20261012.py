"""Round 15 (2026-10-12, .101: `systemd --user` 50 % with her silent, after round 14). busctl monitor: still 44 + 44
loads of house-wall-ascii.service and crissa-display.service in 25 s. The source was glass_mode.read_mode(): a pack has no
mode file, so every call asked is-active and is-enabled on both house units. In a pack glass_mode answers "no" for them
at once (what it always answered); the house asks as before."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import glass_mode  # noqa: E402
import crissa_home  # noqa: E402


def _record(monkeypatch):
    asked = []

    class _R:
        stdout = "active\n"
    monkeypatch.setattr(glass_mode.subprocess, "run", lambda argv, **k: asked.append(list(argv)) or _R())
    return asked


def test_a_pack_reads_off_without_asking_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(glass_mode, "PATH", tmp_path / "house-glass-mode")         # a pack never writes one
    monkeypatch.delenv("CRISSA_GLASS_ASK_UNITS", raising=False)
    asked = _record(monkeypatch)
    assert glass_mode.read_mode() == "off" and asked == []
    assert glass_mode.is_active(glass_mode.WALL) is False and glass_mode.is_enabled(glass_mode.BRAVE) is False
    assert asked == []
    glass_mode.is_active("crissa-wall.service")                                      # any other unit is still asked
    assert asked == [["systemctl", "--user", "is-active", "crissa-wall.service"]]


def test_the_house_asks_as_before_and_the_switch_asks_in_a_pack(monkeypatch, tmp_path):
    monkeypatch.setattr(glass_mode, "PATH", tmp_path / "house-glass-mode")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    asked = _record(monkeypatch)
    assert glass_mode.read_mode() == "wallpaper" and asked[0] == ["systemctl", "--user", "is-active",
                                                                   "house-wall-ascii.service"]
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setenv("CRISSA_GLASS_ASK_UNITS", "1")
    asked.clear()
    assert glass_mode.is_active(glass_mode.BRAVE) is True and len(asked) == 1
