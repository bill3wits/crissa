"""The pre-fill makes room before it stops on the budget (2026-10-13, the User: "fix the prefill budget bug too").
docs/checkpoints/cache-janitor-20261013.

Seen on the house that morning: two old looks of a code print the running Wall does not have (33 GB + 95 MB) held the
disk; the 10:00 build wrote 4,479 pictures in 76 s and stopped "the disk budget; disk 39.2 GB" with the new look at
~7 GB, and nothing would ever have freed them (the build stops at 98 % of the budget, the Wall evicts only past 100 %,
the old looks go only once the new one is whole). Pinned: the deadlock reproduced on a tmp root and gone; the order
(dead looks, then other framings, oldest first; never the look on the glass, never one a live Wall marks, never a
directory that is not a look); each removal logged with its size and why; the Wall's own eviction in the same order;
the kill switch HOUSE_WALL_ASCII_PREFILL_MAKE_ROOM=0 is today's behaviour.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import wall_ascii_disk as D  # noqa: E402
from test_wall_ascii_cache import W, H  # noqa: E402
from test_wall_prefill_20261012 import _Player, _run, _spec  # noqa: E402

WH = W * H


@pytest.fixture
def player():
    p = _Player()
    yield p
    p.close()


def _look(root: Path, name: str, code: str, t: float, nbytes: int = 1000, clips=("listen",)) -> Path:
    d = root / name
    for c in clips:
        (d / c).mkdir(parents=True, exist_ok=True)
        (d / c / "00000-x.pic").write_bytes(b"\0" * (nbytes // len(clips)))
        os.utime(d / c, (t, t))
    (d / "framing.json").write_text(json.dumps({"framing": name, "code": code, "since": t}))
    os.utime(d / "framing.json", (t, t))
    os.utime(d, (t, t))
    return d


def test_room_goes_dead_looks_first_then_other_framings_oldest_first_never_the_glass(tmp_path):
    r = tmp_path / "ascii"
    _look(r, "glass", "live", 10)
    _look(r, "dead-new", "old1", 100)
    _look(r, "dead-old", "old2", 50)
    _look(r, "other-new", "live", 300)
    _look(r, "other-old", "live", 200)
    _look(r, "on-another-wall", "old3", 1)
    (r / "not-a-look" / "x").mkdir(parents=True)
    order = [d.name for d, _w, _t in D.room_order(r, "glass", "live", spare={"on-another-wall"})]
    assert order == ["dead-old", "dead-new", "other-old", "other-new"]
    whys = {d.name: w for d, w, _t in D.room_order(r, "glass", "live")}
    assert "never servable" in whys["dead-old"] and "old2" in whys["dead-old"]
    assert whys["other-old"].startswith("another framing, the oldest")


def test_make_room_frees_what_it_needs_and_logs_each_removal_with_its_size_and_why(tmp_path):
    r = tmp_path / "ascii"
    _look(r, "glass", "live", 10, nbytes=5000)
    _look(r, "dead", "old", 100, nbytes=4000)
    _look(r, "other", "live", 50, nbytes=3000)
    (r / "not-a-look").mkdir()
    said = []
    assert 4000 <= D.make_room(r, "glass", 1, "live", said.append) < 4200   # one dead look is enough
    assert not (r / "dead").exists() and (r / "other").exists()
    assert len(said) == 1 and "look dead removed (0.00 GB) to make room for glass" in said[0] and "never servable" in said[0]
    assert 3000 <= D.make_room(r, "glass", 10 ** 9, "live", said.append) < 3200   # then the other framing, never the glass
    assert not (r / "other").exists() and (r / "glass").exists() and (r / "not-a-look").exists()
    assert D.make_room(r, "glass", 10 ** 9, "live", said.append) == 0     # nothing left: the budget's stop


def test_a_look_another_live_wall_is_on_is_never_taken_and_a_dead_mark_is_ignored(tmp_path):
    r = tmp_path / "ascii"
    _look(r, "glass", "live", 10)
    _look(r, "theirs", "other-code", 5)
    other = subprocess.Popen(["sleep", "60"])
    try:
        time.sleep(0.1)
        (r / (".live-%d.json" % other.pid)).write_text(json.dumps(
            {"pid": other.pid, "start": D._proc_start(other.pid), "look": "theirs", "code": "other-code"}))
        assert D.live_looks(r) == {"theirs": "other-code"}
        assert D.make_room(r, "glass", 10 ** 9, "live", lambda m: None) == 0 and (r / "theirs").exists()
        # the same pid with another start is a reused pid, not that Wall
        (r / (".live-%d.json" % other.pid)).write_text(json.dumps(
            {"pid": other.pid, "start": -1, "look": "theirs", "code": "other-code"}))
        assert D.live_looks(r) == {}
    finally:
        other.kill()
        other.wait()
    # a Wall's own mark names its look and code; a gone process's mark is cleared by the next mark
    (r / ".live-999999999.json").write_text(json.dumps({"pid": 999999999, "start": 5, "look": "x", "code": "y"}))
    D.mark_live(r, "glass", "live")
    mine = json.loads((r / (".live-%d.json" % os.getpid())).read_text())
    assert mine["look"] == "glass" and mine["code"] == "live" and mine["start"] == D._proc_start(os.getpid())
    assert not (r / ".live-999999999.json").exists()
    assert D.live_looks(r) == {"glass": "live"} and D.live_looks(r, but=os.getpid()) == {}


def _dead_look_beside(spec, nbytes):
    """An old look of another code print in the build's root, holding `nbytes`."""
    root = Path(spec["root"])
    d = root / "0000deadlook0000dead"
    (d / "listen").mkdir(parents=True)
    (d / "listen" / "00000-old.pic").write_bytes(b"\0" * nbytes)
    (d / "framing.json").write_text(json.dumps({"framing": "old", "code": "d3959f55", "since": 1.0}))
    return d


def test_the_deadlock_a_dead_look_holds_the_budget_and_the_build_now_makes_room_and_finishes(tmp_path, player):
    spec, _ = _spec(tmp_path, player.url)
    dead = _dead_look_beside(spec, 11 * WH)                     # 11 of a 12-picture budget: not one more fits
    r = _run(spec, env={"HOUSE_WALL_ASCII_DISK_GB": str(12 * WH / 2 ** 30)})
    assert r.returncode == 0, r.stderr[-800:]
    prog = json.loads((Path(spec["dir"]) / "progress.json").read_text())
    assert prog["done"] and prog["built"] == 10 and prog["stopped"] == "", r.stderr[-800:]
    assert not dead.exists()
    assert "look 0000deadlook0000dead removed" in r.stderr and "drawn by code d3959f55" in r.stderr


def test_the_kill_switch_is_todays_stop_on_the_budget(tmp_path, player):
    spec, _ = _spec(tmp_path, player.url)
    dead = _dead_look_beside(spec, 11 * WH)
    r = _run(spec, env={"HOUSE_WALL_ASCII_DISK_GB": str(12 * WH / 2 ** 30), "HOUSE_WALL_ASCII_PREFILL_MAKE_ROOM": "0"})
    prog = json.loads((Path(spec["dir"]) / "progress.json").read_text())
    assert prog["stopped"] == "the disk budget" and not prog["done"] and prog["built"] < 10
    assert dead.exists() and "removed" not in r.stderr


def test_the_walls_own_eviction_takes_a_dead_look_first_and_the_kill_keeps_the_oldest_first(tmp_path, monkeypatch):
    t = D.DiskTier(root=tmp_path, env={"HOUSE_WALL_ASCII_DISK_GB": "1"})
    t._scanned.wait(5)
    t.framing(("B",))
    cur = t.fdir.name
    assert json.loads((tmp_path / (".live-%d.json" % os.getpid())).read_text())["look"] == cur
    _look(tmp_path, "same-code-old", D.code_print(), 10)
    _look(tmp_path, "dead-newer", "d3959f55", 500)
    for name in ("same-code-old", "dead-newer"):
        t.sizes[name] = {"listen": 100}
    t.sizes[cur] = {"work": 100}
    t.used = 300
    said = []
    t.log = said.append
    assert t._evict_one() and "dead-newer" not in t.sizes and "same-code-old" in t.sizes
    assert "look dead-newer evicted" in said[0] and "never servable" in said[0]
    monkeypatch.setattr(D, "MAKE_ROOM", False)
    _look(tmp_path, "dead-newer", "d3959f55", 500)
    t.sizes["dead-newer"] = {"listen": 100}
    assert t._evict_one() and "same-code-old" not in t.sizes and "dead-newer" in t.sizes   # today: oldest first


def test_the_wall_hands_the_build_its_own_code_print(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: started.append(a) or type("P", (), {"poll": lambda s: 0})())
    t = D.DiskTier(root=tmp_path / "ascii", env={"HOUSE_WALL_ASCII_DISK_GB": "1", "HOUSE_WALL_FEED": "http://127.0.0.1:9"})
    t._scanned.wait(5)
    t.framing((W, H, 1.0, True, None, None, ("whole", 1.0)))
    assert len(started) == 1
    spec = json.loads(started[0][0][-1])
    assert spec["code"] == D.code_print()


class _V:
    def __init__(self, v):
        self.value = v


def test_when_nothing_is_left_to_drop_the_build_counts_the_disk_again_freed_elsewhere(tmp_path, monkeypatch):
    """2026-10-13 10:02 the dead looks were removed by hand; the build that had counted them stayed stopped on the
    budget. With nothing left to drop, a worker counts the disk again (at most every RECOUNT_EVERY_S)."""
    r = tmp_path / "ascii"
    _look(r, "glass", "live", 10, nbytes=1000)
    spec = {"root": str(r), "dir": str(r / "glass"), "code": "live"}
    used, cap, said, t = _V(11.0 * WH), _V(12.0 * WH), [], [1000.0]
    monkeypatch.setitem(D._RECOUNT, "at", 0.0)
    assert D.room_for_one(spec, used, cap, 1.05 * WH, said.append, lambda: t[0])
    assert used.value < 2000 and "freed elsewhere" in said[0]
    used.value = 11.0 * WH                                      # counted again within RECOUNT_EVERY_S: not yet
    t[0] += D.RECOUNT_EVERY_S / 2
    assert not D.room_for_one(spec, used, cap, 1.05 * WH, said.append, lambda: t[0])
    monkeypatch.setattr(D, "MAKE_ROOM", False)                  # the kill: today's plain test, nothing counted
    t[0] += D.RECOUNT_EVERY_S * 2
    assert not D.room_for_one(spec, used, cap, 1.05 * WH, said.append, lambda: t[0]) and used.value == 11.0 * WH
    used.value = 1.0
    assert D.room_for_one(spec, used, cap, 1.05 * WH, said.append, lambda: t[0])
