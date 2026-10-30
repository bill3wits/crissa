"""Round 42 (.101's R41-1, the User 10-22: "on paused she should be more on heads down work. I see her mostly looking
up"). Round 39's warm cap swapped listen-warm <-> listen ahead of the bag, so after a conversation `next_key()` always
had an answer on `back` and the service's wrap home never ran (30 min idle from listen-warm: 0 % work). Off the home
hub with no conversation on, no swap is due: the rest loop wraps, the bag on `back` is empty and the wrap walks her home.
In a conversation the cap is unchanged. Kill CRISSA_WARM_CAP_GOES_HOME=0. The house declares no rest loop, so its
conductor (the same lib/conductor.py) never reaches the cap at all."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import conductor as C  # noqa: E402

CLIPS = {"work": {"file": "w.mp4", "element": True, "role": "loop", "start": "down", "end": "down"},
         "listen": {"file": "l.mp4", "element": True, "role": "loop", "start": "back", "end": "back", "idle": True},
         "listen-warm": {"file": "lw.mp4", "element": True, "role": "loop", "start": "back", "end": "back"},
         "lean-back": {"file": "lb.mp4", "element": True, "role": "slide", "start": "down", "end": "back"},
         "lean-in": {"file": "li.mp4", "element": True, "role": "slide", "start": "back", "end": "down"}}


def _seq():
    g = C.Graph(dict(CLIPS), {})
    g.listen_loop, g.rest_loop = "listen", "listen-warm"
    return g, C.Sequencer(g, pool_name="work")


def _idle_from_warm(s, ticks=60):
    """The service's loop, small: a due rest loop asks next_key; nothing dealt off home = the wrap walks her home."""
    seen = []
    s.now, s.wraps = "listen-warm", 0
    for _ in range(ticks):
        seen.append(s.now)
        if s.now in ("lean-in", "lean-back"):            # a slide runs out: she lands on its end hub's rest
            s.now, s.wraps = s.graph.rest_for(s.graph.hub_after(s.now)), 0
            continue
        s.wraps += 1
        if not s.due():
            continue
        k = s.next_key()
        if k:
            s.now, s.wraps = k, 0
        elif not s.at_home():
            r = s.bring_home(0.0, by="wrap")
            if r.get("taken"):
                k = s.take_cue(0.0)
                if k:
                    s.now, s.wraps = k, 0
    return seen


def test_paused_she_goes_home_to_work():
    g, s = _seq()
    seen = _idle_from_warm(s)
    assert "work" in seen, seen
    assert seen.count("work") > len(seen) / 2, "idle from listen-warm is mostly heads-down work (was 0 %%): %s" % seen
    assert "listen" not in seen, "no cap swap while paused"


def test_in_conversation_the_cap_is_unchanged():
    g, s = _seq()
    s.conversation = True
    s.now, s.wraps = "listen-warm", C.WARM_MAX_PLAYS
    assert s.warm_swap() == "listen" and s.due() and s.next_key() == "listen"
    s.now, s.wraps = "listen", C.WARM_NEUTRAL_PLAYS
    assert s.warm_swap() == "listen-warm"


def test_paused_on_back_no_swap_is_due():
    g, s = _seq()
    s.now, s.wraps = "listen-warm", C.WARM_MAX_PLAYS
    assert s.warm_swap() is None
    s.now, s.wraps = "listen", C.WARM_NEUTRAL_PLAYS
    assert s.warm_swap() is None


def test_the_kill_brings_the_old_cap_back(monkeypatch):
    monkeypatch.setenv("CRISSA_WARM_CAP_GOES_HOME", "0")
    g, s = _seq()
    s.now, s.wraps = "listen-warm", C.WARM_MAX_PLAYS
    assert s.warm_swap() == "listen"
    seen = _idle_from_warm(s)
    assert "work" not in seen, "the kill is round 41's behaviour: she never goes home"


def test_the_house_declares_no_rest_loop():
    src = (ROOT / "avatars" / "crissa" / "manifest.json").read_text(encoding="utf-8")
    # one manifest since the fold (the User 2026-10-28: "manifest can unite"): the house wears the kit's rest and boot loop (the warm cap applies on the house too)
    assert '"rest_loop": "listen-warm"' in src
