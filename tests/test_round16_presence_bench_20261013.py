"""Round 16 (2026-10-13, .101: presence 1.83 cores with the User seated, the door watch settled, never resting).
`resting` is the EMPTY-room rest (no face for 10 s), so with him in the seat it is false by design; the settled state
held. The 1.8 cores were the old A/B/C Oracle bench inside presence: the house runs its rail with a drop-in that turns
it off (rail-presence.service.d/ops-vram.conf: RAIL_ORACLE_BENCH=0, RAIL_ORACLE_GPU=0, RAIL_ORACLE_MODE=off) and the
pack's copy defaulted it on. Measured in a sandbox on a film camera: 1.82 cores (oracle-bench 0.63, its native threads
0.78, oracle-strip-live 0.10, the door loop 0.25) -> 0.35 with the house's switches."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PMOD = ROOT / "modules" / "presence"


def _script():
    spec = importlib.util.spec_from_file_location("crissa_presence_script_r16", PMOD / "scripts" / "crissa-presence.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_pack_launches_presence_with_the_houses_oracle_switches():
    m = _script()
    assert m.house_switches({}) == {"RAIL_ORACLE_BENCH": "0", "RAIL_ORACLE_GPU": "0", "RAIL_ORACLE_MODE": "off"}
    assert m.house_switches({"CRISSA_PRESENCE_ORACLE_BENCH": "1"}) == {}          # the kill: the copy's defaults


def test_the_switches_are_set_before_the_service_loads_and_never_over_a_set_value():
    src = (PMOD / "scripts" / "crissa-presence.py").read_text(encoding="utf-8")
    body = src[src.index("def main(argv=None)"):]
    i = body.index("for key, value in house_switches().items():\n        os.environ.setdefault(key, value)")
    assert i < body.index("from crissa_presence import server")


def test_the_bench_reads_its_switches_at_import():
    """The switches work only if the bench reads them when it loads, which the launcher's order relies on."""
    src = (PMOD / "crissa_presence" / "oracle_bench.py").read_text(encoding="utf-8")
    assert 'ENABLE = os.environ.get("RAIL_ORACLE_BENCH", "1")' in src
    start = src[src.index("    def start(self) -> None:"):]
    assert start.index("if not ENABLE:") < start.index("threading.Thread(")
