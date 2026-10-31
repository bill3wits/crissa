"""A pass waits on frame 0 for its voice (the User 2026-09-27).

drill_begin plays the film at once; the voice lands seconds later. The film
rolled with the idle mouth and was set back to frame 0 when the air stamp
arrived — on lila-work under the AutoTuner, frame 82-89 of 241 back to 0 on
every pass. The render loop now holds frame 0 from the arm to the air stamp.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
HOLD = 'if DRILL_HOLD_ZERO and DRILL.get("t0") is None and not DRILL.get("pacer_on"):'


def _loop() -> str:
    """The render loop's drill branch: from the air stamp to the pass's end."""
    return SRC.split("clip locked to the voice", 1)[1].split('drill_stop("clip over")', 1)[0]


def test_it_carries_a_kill_and_defaults_on():
    line = [l for l in SRC.splitlines() if l.startswith("DRILL_HOLD_ZERO = ")]
    assert len(line) == 1
    assert 'os.environ.get("HOUSE_DRILL_HOLD_ZERO", "1")' in line[0]


def test_the_hold_is_one_line_in_the_render_loop():
    assert SRC.count(HOLD) == 1
    assert HOLD in _loop(), "inside `DRILL on and this is the drill's clip`, nowhere else"


def test_it_holds_the_first_frame_and_stops_the_advance():
    body = _loop().split(HOLD, 1)[1].split("\n", 2)[1]
    assert "self.i = 0" in body and "locked = True" in body, \
        "`locked` is what keeps the loop from advancing the film this tick"


def test_the_air_stamp_is_read_before_the_hold():
    """t0 is set from the air stamp ABOVE the hold, so the tick the voice
    arrives on takes the clip lock and never one more held frame."""
    assert SRC.index('DRILL["t0"] = float(aa)') < SRC.index(HOLD)
    assert SRC.index(HOLD) < SRC.index('if DRILL.get("t0") is not None:\n                        target = int(')


def test_a_calibration_pass_is_left_alone():
    """The pacer has no voice to wait for: it rewinds itself and posts its sheet
    from the playhead one frame later. Its measured +2 f must not move."""
    assert 'not DRILL.get("pacer_on")' in HOLD


def test_the_timeouts_still_fire_under_the_hold():
    """A voice that never comes still ends the pass: the hold is an `if`, not a
    branch that swallows the 40 s rule."""
    after = SRC.split(HOLD, 1)[1]
    assert after.index('drill_stop("no voice within 40 s")') < after.index("n_clip = 0")


def test_the_hold_behaves():
    """The line itself, run on a stand-in playhead."""
    import textwrap

    class P:
        i = 57

    def tick(drill, on=True):
        p, ns = P(), {"DRILL": drill, "DRILL_HOLD_ZERO": on, "locked": False}
        ns["self"] = p
        line = [l for l in SRC.splitlines() if l.strip() == HOLD][0]
        nxt = SRC.splitlines()[SRC.splitlines().index(line) + 1]
        exec(textwrap.dedent(line + "\n" + nxt), ns)  # noqa: S102
        return p.i, ns["locked"]

    assert tick({"t0": None}) == (0, True), "armed, no voice: frame 0, held"
    assert tick({"t0": 123.0}) == (57, False), "the voice is on: the clip lock owns the frame"
    assert tick({"t0": None, "pacer_on": True}) == (57, False), "calibration pass"
    assert tick({"t0": None}, on=False) == (57, False), "the kill puts the old roll back"
