"""Round 33, item 6 (.101 round 32: after a restart the laps read "memory 390, disk 0, fresh 2" while the disk line read
hits=0 bases=0 writes=0 replaced=0). The read-ahead brings the disk tier's pictures into memory before their frame, so
the lap serves them as memory. The disk line now names them `ahead=N`."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

from test_r25_wall_read_ahead_20261016 import _cache, _Disk  # noqa: E402


def test_the_read_ahead_is_counted_on_the_disk_line():
    d = _Disk({1, 2, 3})
    d.n = {"hits": 0}
    c = _cache(d, {"HOUSE_WALL_ASCII_READ_AHEAD": "1"})
    assert c.read_ahead(None, ("listen", 0, 10, "loop"), n=3, budget_s=1.0) == 3
    assert d.n["ahead"] == 3 and d.n["hits"] == 0


def test_the_kill_and_nothing_loaded_leave_the_line_as_it_was():
    d = _Disk({1})
    d.n = {}
    c = _cache(d, {"HOUSE_WALL_ASCII_READ_AHEAD": "1", "HOUSE_WALL_ASCII_DISK_AHEAD_COUNT": "0"})
    assert c.read_ahead(None, ("listen", 0, 10, "loop"), n=1, budget_s=1.0) == 1
    assert "ahead" not in d.n
    d2 = _Disk(set())
    d2.n = {}
    c2 = _cache(d2, {"HOUSE_WALL_ASCII_READ_AHEAD": "1"})
    assert c2.read_ahead(None, ("listen", 0, 10, "loop"), n=2, budget_s=1.0) == 0 and "ahead" not in d2.n


def test_the_line_prints_ahead_only_when_counted():
    src = (ROOT / "lib/house_wall_ascii.py").read_text(encoding="utf-8")
    assert '(" ahead=%d" % cs["disk"]["ahead"]) if "ahead" in cs["disk"] else ""' in src
