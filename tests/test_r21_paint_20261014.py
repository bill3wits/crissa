"""Round 21's paint share (.101 round 20, RESULTS-20261014-1305 notes 5 and 6): the density pick times what the Wall
does, a click goes before a picture, and every clip on air says why its mouth went unpainted."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

FILM = ROOT / "display" / "crissa-poses" / "clip-listen-back.mp4"


# ---- 1. the density pick ---------------------------------------------------------------------------------------------

class _Child:
    """A film child whose round trip costs COST[density] ms on a shared fake clock."""
    COST = {5.0: 50.0, 4.0: 40.0, 3.0: 36.0, 2.0: 31.0, 1.5: 25.0, 1.0: 20.0}

    def __init__(self, clock, made):
        self.clock, made_ = clock, made
        made_.append(self)
        self.closed = False

    def render(self, jpeg, density, own, t, cache=False, frame=None):
        self.clock[0] += self.COST[round(float(density), 1)] / 1000.0
        return object()

    def close(self):
        self.closed = True


def _probe(**kw):
    if not FILM.is_file():
        pytest.skip("no listen film in this tree")
    import wall_ascii_disk as D
    clock, made = [0.0], []
    out = D.density_probe(3440, 1440, FILM, frames=4, fps=24.0, timer=lambda: clock[0],
                          child=lambda W, H: _Child(clock, made), **kw)
    return out, made


def test_round_20s_rule_through_the_child_picks_4_and_the_strict_rule_picks_2():
    loose, _ = _probe(via_child=True)                         # margin 1, nothing for the pass: round 20's bar
    assert loose["pick"] == 4.0
    strict, made = _probe(via_child=True, margin=1.1, paint_ms=5.0)
    assert strict["pick"] == 2.0
    rows = {r["density"]: r for r in strict["candidates"]}
    assert rows[4.0]["fit_ms"] == pytest.approx(49.0) and rows[3.0]["fit_ms"] == pytest.approx(44.6)
    assert rows[2.0]["fit_ms"] == pytest.approx(39.1) and all(r["how"] == "child" for r in rows.values())
    assert strict["rule"] == {"via": "child", "margin": 1.1, "paint_ms": 5.0}
    assert len(made) == 1 and made[0].closed, "one film child for the probe, closed at the end"


def test_without_a_child_the_render_is_timed_in_process(monkeypatch):
    if not FILM.is_file():
        pytest.skip("no listen film in this tree")
    import wall_ascii_disk as D
    out = D.density_probe(320, 180, FILM, candidates=(1.0,), frames=2, fps=24.0, via_child=True,
                          child=lambda W, H: (_ for _ in ()).throw(OSError("no child")))
    assert out["candidates"][0]["how"] == "thread" and out["pick"] == 1.0


def test_the_card_asks_for_the_strict_rule_unless_switched_off(monkeypatch, tmp_path):
    import crissa_machine as M
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "wall-glass.json").write_text(json.dumps({"W": 3440, "H": 1440}))
    monkeypatch.setattr(M, "_listen_film", lambda: FILM)
    seen = []

    class _R:
        stdout = json.dumps({"pick": 2.0}) + "\n"

    def run(argv, **kw):
        seen.append(json.loads(argv[-1]))
        return _R()
    th = M.thresholds(tmp_path)
    assert th["density_margin"] == 1.1 and th["density_paint_ms"] == 5.0
    got, why = M.stage_density(th, home=tmp_path, env={}, run=run)
    assert got == {"pick": 2.0} and not why
    assert seen[-1]["via_child"] is True and seen[-1]["margin"] == 1.1 and seen[-1]["paint_ms"] == 5.0
    M.stage_density(th, home=tmp_path, env={"CRISSA_DENSITY_PICK_STRICT": "0"}, run=run)
    assert "via_child" not in seen[-1] and "margin" not in seen[-1]


# ---- 2. a click before a picture ---------------------------------------------------------------------------------------

WALL = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")


def test_the_pump_hands_tk_the_persons_input_before_each_picture():
    assert 'INPUT_FIRST = _pack_switch("HOUSE_WALL_INPUT_FIRST")' in WALL
    pump = WALL.split("    def pump():", 1)[1].split("\n    def ", 1)[0]
    assert pump.index("_input_first()") < pump.index("out_q.get_nowait()"), "the input goes first"
    assert "if took and time.time() - hand[\"at\"] < INPUT_FIRST_S:" in pump and "break" in pump
    first = WALL.split("    def _input_first() -> None:", 1)[1].split("\n    def ", 1)[0]
    assert "root.tk.dooneevent(_tkinter.WINDOW_EVENTS | _tkinter.DONT_WAIT)" in first, "window events only, never timers"
    assert "motion_flag.is_set()" in first and "on_motion()" in first, "the polled pointer too (wallpaper mode)"
    assert "except Exception" in first, "the picture goes up whatever the input does"


# ---- 3. why a clip went without a mouth -------------------------------------------------------------------------------

def _tally_fn():
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Playhead")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_tally")
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns["_tally"], src


class _Mouth:
    def __init__(self):
        self.on, self.force = True, None

    def painting(self):
        return self.on


class _Ph:
    def __init__(self):
        self.mouth, self.key, self.i, self.painted, self.skip = _Mouth(), "flourish-x", 0, 0, ""


def test_a_clip_on_air_says_how_many_passes_went_unpainted_and_why(capsys):
    tally, src = _tally_fn()
    assert 'MOUTH_CLIP_TALLY = os.environ.get("HOUSE_MOUTH_CLIP_TALLY", "1")' in src
    assert "if MOUTH_CLIP_TALLY:\n            self._tally(p0)" in src
    ph = _Ph()
    for i in range(10):
        ph.i = i
        p0 = ph.painted
        if i in (4, 5):
            ph.skip = "head moving (3.1 w/s)"
        else:
            ph.skip = ""
            ph.painted += 1
        tally(ph, p0)
    assert capsys.readouterr().out == ""                       # nothing until the clip or the line ends
    ph.mouth.on = False
    tally(ph, ph.painted)
    out = capsys.readouterr().out
    assert "mouth on flourish-x while she spoke: 10 passes, painted 8, unpainted 2" in out
    assert "head moving (3.1 w/s): 2" in out and "frames 4-5" in out
    tally(ph, ph.painted)
    assert capsys.readouterr().out == "", "one line a clip"


def test_a_clip_change_on_air_closes_the_last_clips_line(capsys):
    tally, _ = _tally_fn()
    ph = _Ph()
    for _ in range(3):
        p0 = ph.painted
        ph.painted += 1
        tally(ph, p0)
    ph.key = "work"
    tally(ph, ph.painted)                                     # this pass painted nothing on the new clip
    out = capsys.readouterr().out
    assert "mouth on flourish-x while she spoke: 3 passes, painted 3, unpainted 0" in out and "work" not in out
