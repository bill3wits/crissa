"""Round 39 (.101 round 38 N1 + N2, the User). N1 "I am seeing her lips through the tiles": while she talked on
listen-warm, the passes the paint could not keep up with showed the Wan take's own smiling mouth. She now TALKS on her
listen loop (a still filmed mouth, the 09-27 law) and RESTS on listen-warm between lines (`rest_loop`). N2 "cap that
at 1 or 2x before a more neutral loop": the rest loop plays WARM_MAX_PLAYS times, then the listen loop
WARM_NEUTRAL_PLAYS times, then it may return. Kill CRISSA_REST_LOOP=0 (no rest loop: she stays on listen)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import avatar  # noqa: E402
import conductor as C  # noqa: E402

CLIPS = {"work": {"file": "w.mp4", "element": True, "role": "loop", "start": "down", "end": "down"},
         "listen": {"file": "l.mp4", "element": True, "role": "loop", "start": "back", "end": "back", "idle": True},
         "listen-warm": {"file": "lw.mp4", "element": True, "role": "loop", "start": "back", "end": "back"},
         "fl-nod": {"file": "n.mp4", "element": True, "role": "flourish", "start": "back", "end": "back",
                    "speech": False}}


def _seq(rest="listen-warm"):
    g = C.Graph(dict(CLIPS), {})
    g.listen_loop, g.rest_loop = "listen", rest
    s = C.Sequencer(g, pool_name="work")
    return g, s


def test_she_rests_on_the_rest_loop_and_talks_on_her_listen_loop():
    g, s = _seq()
    assert g.rest_for("back") == "listen-warm", "between lines"
    assert g.talk_rest_for("back") == "listen", "while she speaks"
    s.now, s.hold_flourishes = "listen-warm", True              # a line goes on the air while she rests
    assert s.rest_while_speaking("listen-warm") == "listen", "never talks on the rest loop"
    s.now = "fl-nod"
    assert s.rest_while_speaking("fl-nod") == "listen", "a covering flourish lands on the talk loop, not the warm one"
    s.hold_flourishes = False
    assert s.rest_while_speaking("listen-warm") is None, "not speaking: nothing to cut"


def test_the_rest_loop_is_capped_then_a_neutral_listen_then_it_returns():
    g, s = _seq()
    s.conversation = True                                      # round 42 (R41-1): paused, the way home wins instead
    s.now, s.wraps = "listen-warm", 0
    assert s.warm_swap() is None
    s.wraps = C.WARM_MAX_PLAYS
    assert s.warm_swap() == "listen" and s.due() and s.next_key() == "listen"
    s.now, s.wraps = "listen", C.WARM_NEUTRAL_PLAYS - 1
    assert s.warm_swap() is None
    s.wraps = C.WARM_NEUTRAL_PLAYS
    assert s.warm_swap() == "listen-warm"
    s.hold_flourishes = True
    assert s.warm_swap() is None, "never while she speaks"


def test_the_house_and_the_kill_are_unchanged():
    g, s = _seq(rest=None)                                     # the house: no rest loop declared
    assert g.rest_for("back") == "listen" and g.talk_rest_for("back") == "listen"
    s.now, s.wraps = "listen", 99
    assert s.warm_swap() is None
    house = ROOT / "avatars" / "crissa" / "manifest.json"
    assert avatar.rest_loop(name="crissa") is None or "rest_loop" not in house.read_text(), "the house declares none"


def test_the_kill_reads_no_rest_loop(monkeypatch):
    monkeypatch.setenv("CRISSA_REST_LOOP", "0")
    assert avatar.rest_loop(name="crissa") is None
    src = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
    assert "graph.rest_loop = _avatar_rest_loop(POSE_DIR)" in src
