"""A pack has no scripted greeting (the User 2026-10-26: "remove the scripted good morning ... We can coach to that in
SOUL"); the house keeps its line. Loaded as tests/test_round29_asks does (the presence package, not lib's module)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "presence"))
from crissa_presence import auth_first as af  # noqa: E402


def test_a_pack_greets_through_her_soul_not_by_script(monkeypatch, tmp_path):
    monkeypatch.setattr(af._paths, "is_house", lambda: False)
    monkeypatch.delenv("HOUSE_GOOD_MORNING_TEXT", raising=False)
    monkeypatch.setattr(af, "setup_running", lambda home=None: False)
    monkeypatch.delenv("CRISSA_GREETING", raising=False)
    said = []
    out = af.maybe_good_morning("user", day_path=tmp_path / "u.day", today="2026-10-26", speak=said.append)
    assert out["emitted"] is False and out["reason"].startswith("off") and said == []
    assert (tmp_path / "u.day").read_text().strip() == "2026-10-26", "the day is still marked"
    monkeypatch.setenv("CRISSA_GREETING", "1")
    out = af.maybe_good_morning("user", day_path=tmp_path / "v.day", today="2026-10-26", speak=said.append)
    assert out["emitted"] and said == [out["text"]]
    monkeypatch.setattr(af._paths, "is_house", lambda: True)
    assert not af.greeting_off(), "the house keeps its own line"


