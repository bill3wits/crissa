"""Rolling 1h raw + freeze debounce (not a second identity path)."""
from __future__ import annotations

from pathlib import Path

from crissa_presence import security_raw as sr


def test_freeze_debounces_second_copy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sr, "INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(sr, "FREEZE_DEBOUNCE_SEC", 120.0)
    monkeypatch.setattr(sr, "_LAST_FREEZE_TS", 0.0)
    monkeypatch.setattr(sr, "_LAST_FREEZE_ID", "")
    monkeypatch.setattr(sr, "list_frames_in_range", lambda a, b: [])
    a = sr.freeze_incident(reason="intrusion_attempt")
    assert a.get("ok") is True
    assert a.get("skipped") is None
    assert a.get("id")
    b = sr.freeze_incident(reason="security")
    assert b.get("skipped") == "debounce"
    assert b.get("id") == a.get("id")
    assert len(list((tmp_path / "incidents").glob("incident_*"))) == 1
