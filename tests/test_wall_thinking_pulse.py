"""K (the User 2026-10-01): Crissa's dot pulses from his turn sent until her line is on the roll. No Tk, no network."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import house_wall_ascii_render as R      # noqa: E402
import house_wall_ascii as G             # noqa: E402  (no tkinter import at module level)

SINCE = 1790865411.7


def test_default_off():
    assert G.THINKING_PULSE is False


def test_pulse_runs_until_her_line_is_in_the_roll():
    th = {"on": True, "since": SINCE, "seq": 3}
    assert R.thinking_pulse(th, 0.0, SINCE + 0.1) is not None
    assert R.thinking_pulse(th, SINCE - 30, SINCE + 5) is not None        # an older line does not stop it
    assert R.thinking_pulse(th, SINCE + 2.9, SINCE + 3.0) is None          # her line entered: stop
    assert R.thinking_pulse(dict(th, on=False), 0.0, SINCE + 1) is None   # turn over, no line (cancelled)
    assert R.thinking_pulse(th, 0.0, SINCE + R.THINK_MAX_S + 1) is None   # a stale file never pulses
    assert R.thinking_pulse({}, 0.0, SINCE) is None and R.thinking_pulse(None, 0.0, SINCE) is None


def test_pulse_phase_cycles():
    th = {"on": True, "since": SINCE}
    ph = [R.thinking_pulse(th, 0.0, SINCE + 0.05 + k * 0.2) for k in range(9)]
    assert all(0.0 <= p < 1.0 for p in ph) and ph[8] < ph[7] and ph[7] > ph[0]   # wraps every 1.6 s


def test_lobe_tile_exists_for_the_pulse_alone():
    R.ANCHORS["oracle"] = [400.0, 400.0, 260.0]
    assert R.render_lobe(None, None, SINCE, crissa=None) is None            # dark, as before
    out = R.render_lobe(None, None, SINCE, crissa=None, pulse=0.3)
    assert out is not None
    im, _pos = out
    c = im.size[0] // 2
    assert im.getpixel((c, c))[3] == 255                                  # her core in full ink
    assert R.render_lobe(None, None, SINCE, crissa=None, pulse=0.3, collapsed=True) is None


def test_think_tick_reads_the_file_and_kicks_the_house_poll(tmp_path, monkeypatch):
    f = tmp_path / "crissa-thinking.json"
    f.write_text(json.dumps({"on": True, "since": SINCE, "seq": 1}))
    monkeypatch.setattr(G, "THINKING_FILE", f)
    monkeypatch.setattr(G.time, "time", lambda: SINCE + 2.0)

    class St:
        lock = threading.Lock()
        thinking: dict = {}
        crissa_line_ts = 0.0
        sound_crissa = {"mouth_sink": False}
        house_kick = threading.Event()

    st = St()
    G._think_tick(st)
    assert st.thinking["seq"] == 1 and not st.house_kick.is_set()        # thinking, mouth quiet: no kick
    st.sound_crissa = {"mouth_sink": True}
    G._think_tick(st)
    assert st.house_kick.is_set()                                         # her mouth is live: fetch her line now
    st.house_kick.clear()
    st.crissa_line_ts = SINCE + 1.5
    G._think_tick(st)
    assert not st.house_kick.is_set()                                     # line already in: nothing to wait for


def test_loop_wiring():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'os.environ.get("HOUSE_WALL_THINKING_PULSE", "0")' in src
    th = src.split("\ndef t_house(")[1].split("\ndef t_camera(")[0]
    assert "st.house_kick.wait(2.0)" in th and "time.sleep(2.0)" in th          # off: the old 2 s
    film = src.split('roll.feed(house.get("chat"), t0)')[1][:500]
    assert 'ln.get("who") == "crissa"' in film and "st.crissa_line_ts" in film
    lobe = src.split("def lobe_worker():")[1].split("def register_worker")[0]
    assert "pulse=pulse" in lobe and "R.thinking_pulse(th, zl_ts, t0) if THINKING_PULSE else None" in lobe
    assert "thinking pulse seq=" in lobe                                           # the live measurement


def test_the_glow_breathes_in_size_and_brightness():
    """The User 10-01: bigger, much more noticeable, breathing like a thinking spinner."""
    import numpy as np

    lo, hi = R.render_think_glow(1.0, 0.0), R.render_think_glow(1.0, 0.5)
    assert hi.size[0] > 2 * lo.size[0]                          # radius 16 S -> 56 S
    lit = lambda im: int((np.asarray(im)[..., 3] > 40).sum())
    assert lit(hi) > 6 * lit(lo)                                # far more lit area at the top of the breath
    assert hi.size[0] <= 2 * 72 + 3                             # stays inside the dial's 72 S ring
    assert R.think_breath(0.0) == 0.0 and abs(R.think_breath(0.5) - 1.0) < 1e-9
    assert R.THINK_PERIOD_S == 1.6


def test_the_tile_breathes_at_10_hz_only_while_pulsing():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    lobe = src.split("def lobe_worker():")[1].split("def register_worker")[0]
    assert "tick = 0.1 if pulse is not None else (0.2 if (fresh or lobe is not None) else 0.5)" in lobe
