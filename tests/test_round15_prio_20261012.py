"""Round 15 (2026-10-12, .101 ASK-20261012-1308 addendum 13:43): the player and the Wall first. Read on .101: the lane
shares from round 13 are in force (player and Wall CPUWeight 400; presence, oracle, asr 50 with nice 10; the ASCII
pre-fill already runs SCHED_IDLE at nice 19 and holds while she is on air). What was not: presence kept to "the last two
CPUs", 14 and 15 - on .101 the second hyperthreads of TWO physical cores (6 and 7) - so its busy threads slowed two
cores either of which the player's or the Wall's one busy thread could land on. A paint-like thread measured with its
sibling hyperthread busy: p50 51.5 -> 63.8 ms (+24 %); with another core busy: 51.1 ms. Now presence takes whole cores."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
spec = importlib.util.spec_from_file_location("crissa_presence_launch_r15", ROOT / "scripts" / "crissa-presence.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)



def test_presence_takes_one_whole_core_on_a_101_shaped_machine():
    sib = lambda c: sorted({c % 8, c % 8 + 8})
    assert P.whole_cores(list(range(16)), 2, sib) == [7, 15]
    assert P.whole_cores(list(range(16)), 4, sib) == [6, 7, 14, 15]
    assert P.whole_cores(list(range(8)), 2, lambda c: [c]) == [6, 7]          # no hyperthreads: the last two, as before


def test_cap_cpus_sets_the_pair_and_the_switch_keeps_the_last_two():
    got = []
    sib = lambda c: sorted({c % 8, c % 8 + 8})
    orig = P.siblings
    try:
        P.siblings = sib
        kept = P.cap_cpus({"CRISSA_PRESENCE_CPUS": "2"}, getaff=lambda pid: set(range(16)),
                          setaff=lambda pid, cpus: got.append(list(cpus)))
    finally:
        P.siblings = orig
    assert kept == [7, 15] and got == [[7, 15]]
    got.clear()
    kept = P.cap_cpus({"CRISSA_PRESENCE_CPUS": "2", "CRISSA_PRESENCE_CPU_PAIRS": "0"},
                      getaff=lambda pid: set(range(16)), setaff=lambda pid, cpus: got.append(list(cpus)))
    assert kept == [14, 15]


def test_siblings_reads_the_kernels_list_and_falls_back_to_the_cpu():
    assert 0 in P.siblings(0)
    assert P.siblings(10_000) == [10_000]


def test_the_lane_shares_put_the_paint_first():
    import crissa_service as S
    for paint in ("player", "wall"):
        assert S.lane_priority(paint, {})[1] > max(S.lane_priority(x, {})[1] for x in ("presence", "oracle", "asr"))
