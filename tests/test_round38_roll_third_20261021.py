"""Round 38 (.101 round 37, the User: "wrap the Wall's log lines narrower: to the left third of the screen")."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import house_wall_ascii_render as R  # noqa: E402


def test_the_roll_wraps_in_the_left_third(monkeypatch):
    monkeypatch.setattr(R.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.delenv("CRISSA_ROLL_THIRD", raising=False)
    assert R.roll_third_on() is True
    monkeypatch.setenv("CRISSA_ROLL_THIRD", "0")
    assert R.roll_third_on() is False
    monkeypatch.delenv("CRISSA_ROLL_THIRD")
    monkeypatch.setattr(R.crissa_home, "is_house", lambda *a, **k: True)
    assert R.roll_third_on() is False, "the house's roll is as it was"
    src = (ROOT / "lib" / "house_wall_ascii_render.py").read_text(encoding="utf-8")
    draw = src.split("class Roll:", 1)[1]
    assert "right = min(right, g.W / 3.0)" in draw and draw.index("right = min(right, g.W / 3.0)") < draw.index(
        "maxw = right - (left + who_col)")
