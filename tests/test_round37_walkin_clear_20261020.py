"""Round 37 (.101 round 36, the User's walk-in): from Lila's handover until Crissa is seated, Setup's snapshot carries
`ending` and the desk Wall keeps her tile off and its middle clear. Kill CRISSA_WALKIN_CLEAR=0."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_init as zi  # noqa: E402


def test_the_snapshot_says_the_walk_in(monkeypatch):
    monkeypatch.setattr(zi.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.delenv("CRISSA_WALKIN_CLEAR", raising=False)
    monkeypatch.setitem(zi._ENDING, "on", False)
    assert zi.ending_view() == {}
    monkeypatch.setitem(zi._ENDING, "on", True)
    monkeypatch.setitem(zi._ENDING, "seated", False)
    assert zi.ending_view() == {"on": True, "seated": False}
    monkeypatch.setitem(zi._ENDING, "seated", True)
    assert zi.ending_view() == {"on": True, "seated": True}
    monkeypatch.setenv("CRISSA_WALKIN_CLEAR", "0")
    assert zi.ending_view() == {}
    monkeypatch.setattr(zi.crissa_home, "is_house", lambda *a, **k: True)
    monkeypatch.delenv("CRISSA_WALKIN_CLEAR")
    assert zi.ending_view() == {}, "never on the house"


def test_the_ending_sets_and_clears_it_and_the_wall_reads_it():
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    end = src.split("def _team_end(run_id: str) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert '_ENDING.update(on=True, seated=False)' in end and '_ENDING.update(on=False, seated=True)' in end
    up = src.split("def _entrance_then_up(", 1)[1].split("\ndef ", 1)[0]
    assert up.index("_land_on_listen()") < up.index('_ENDING["seated"] = True')
    assert '"ending": ending_view(),' in src
    wall = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert "ft_off = ft_off or _walkin_hides_tile()" in wall
    assert 'return isinstance(e_, dict) and bool(e_.get("on")) and not e_.get("seated")' in wall
    assert 'time.sleep(0.5 if (running or ending) else 5.0)' in wall
