"""Round 13: the ASCII cache by clip, with a disk tier (2026-10-12; the User, on .101's first lap: "maybe we consider
the length of these clips and that they run sequentially but not in order"). docs/checkpoints/round13-cache-20261012.

Pinned: the player names each frame's clip (X-Clip); a picture drawn once per (framing, clip, frame) comes back from
disk after a restart byte for byte; a speaking frame takes its own idle picture from memory or disk as a region's
base; a damaged entry is a miss; another framing (or another kit's code) is evicted first, then whole clips least
recently played, never the one on the glass; the loops she returns to stay in memory first; an idle pre-fill draws the
loops into a new framing, exact; on in a pack, off on the house.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_cache as C  # noqa: E402
import wall_ascii_disk as D  # noqa: E402
from test_wall_ascii_cache import W, H, _cache, _fresh_state, _jpeg, _mouth, _scene  # noqa: E402

G = 2 ** 30
FR = ("whole", 1.0, 0.5, 0.32)


@pytest.fixture
def disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "1")
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_DIR", str(tmp_path / "ascii"))
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_FLOOR_GB", "0")
    monkeypatch.setenv("HOUSE_WALL_FEED", "")
    monkeypatch.setenv("HOUSE_WALL_ASCII_PREFILL", "0")     # round 16: never a real build of this tree from a test
    _fresh_state()
    return tmp_path / "ascii"


def _fresh(jp, g, frame=FR):
    return R.coverage_fresh(R._open(jp), g, True, 0.0, frame=frame).tobytes()


def _settle(c, n):
    t0 = time.time()
    while c.disk.stats()["writes"] < n and time.time() - t0 < 20:
        time.sleep(0.05)


def _new_cache():
    c = _cache()
    t0 = time.time()
    while not c.disk._scanned.is_set() and time.time() - t0 < 10:
        time.sleep(0.02)
    return c


def test_the_disk_budget_is_a_tenth_of_free_disk_capped_at_100_gb_and_keeps_20_gb_free():
    assert D.cap_bytes(1700 * G, 0, {}) == 100 * G                     # .101: 1.7 TB free
    assert D.cap_bytes(300 * G, 0, {}) == int(30 * G)
    assert D.cap_bytes(15 * G, 4 * G, {}) == 0                          # under the 20 GB floor: give back what is short
    assert D.cap_bytes(1700 * G, 0, {"HOUSE_WALL_ASCII_DISK_GB": "8"}) == 8 * G


def test_on_in_a_pack_off_on_the_house_and_the_word_wins(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert not D.enabled({})
    assert D.enabled({"HOUSE_WALL_ASCII_DISK": "1"})
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    assert D.enabled({})
    assert not D.enabled({"HOUSE_WALL_ASCII_DISK": "0"})


def test_a_restart_reads_the_lap_back_from_disk_byte_for_byte(disk):
    g = R.Geom(W, H, density=1.0)
    frames = [_jpeg(_scene(950 + i)) for i in range(6)]
    a = _new_cache(); a.prepare(g)
    for i, jp in enumerate(frames):
        a.picture(jp, g, True, 0.0, frame=FR, clip=("listen", i, 6, "loop"))
    _settle(a, 6)
    b = _new_cache(); b.prepare(g)                                       # a new process: memory empty
    hows = []
    for i, jp in enumerate(frames):
        p, how, _ = b.picture(jp, g, True, 0.0, frame=FR, clip=("listen", i, 6, "loop"))
        hows.append(how)
        assert bytes(p.px) == _fresh(jp, g), (i, how)
    assert hows == ["disk"] * 6
    # the second lap is memory again
    assert [b.picture(jp, g, True, 0.0, frame=FR, clip=("listen", i, 6, "loop"))[1]
            for i, jp in enumerate(frames)] == ["bytes"] * 6


def test_a_speaking_frame_takes_its_own_idle_picture_as_the_regions_base(disk):
    g = R.Geom(W, H, density=1.0)
    idle = [_scene(960 + i) for i in range(3)]
    a = _new_cache(); a.prepare(g)
    for i, im in enumerate(idle):
        a.picture(_jpeg(im), g, True, 0.0, frame=FR, clip=("listen", i, 3, "loop"))
    _settle(a, 3)
    b = _new_cache(); b.prepare(g)
    for i, im in enumerate(idle):
        jp = _jpeg(_mouth(im, (330, 220, 60, 22), i))
        p, how, _ = b.picture(jp, g, True, 0.0, frame=FR, clip=("listen", i, 3, "loop"))
        assert how == "disk-region", how
        assert bytes(p.px) == _fresh(jp, g)
    # in memory now: the next speaking pass reads no disk
    reads = b.disk.n["bases"]
    jp = _jpeg(_mouth(idle[0], (330, 220, 60, 22), 7))
    p, how, _ = b.picture(jp, g, True, 0.0, frame=FR, clip=("listen", 0, 3, "loop"))
    assert how == "region" and b.disk.n["bases"] == reads and bytes(p.px) == _fresh(jp, g)


def test_a_damaged_entry_is_a_miss_and_is_deleted(disk):
    g = R.Geom(W, H, density=1.0)
    jp = _jpeg(_scene(970))
    a = _new_cache(); a.prepare(g)
    a.picture(jp, g, True, 0.0, frame=FR, clip=("listen", 0, 1, "loop"))
    _settle(a, 1)
    f = next(disk.rglob("*.pic"))
    b = bytearray(f.read_bytes()); b[-10] ^= 0xFF; f.write_bytes(bytes(b))
    c = _new_cache(); c.prepare(g)
    p, how, _ = c.picture(jp, g, True, 0.0, frame=FR, clip=("listen", 0, 1, "loop"))
    assert how == "full" and bytes(p.px) == _fresh(jp, g)
    assert c.disk.n["damaged"] == 1


def test_the_framing_directory_names_the_code_that_drew_it(monkeypatch):
    fk = (3440, 1440, 0.859, True, None, None, ("whole", 1.0))
    a = D.framing_dir_name(fk)
    monkeypatch.setitem(D._PRINT, "p", "another kit")
    assert D.framing_dir_name(fk) != a
    assert D.framing_dir_name(fk[:2] + (1.49,) + fk[3:]) != D.framing_dir_name(fk)


def test_over_budget_another_framing_goes_first_then_the_least_recently_played_clip_never_the_one_on_the_glass(tmp_path):
    t = D.DiskTier(root=tmp_path, env={"HOUSE_WALL_ASCII_DISK_GB": "1"})
    t._scanned.wait(5)
    t.framing(("A",))
    old = t.fdir.name
    t.framing(("B",))
    cur = t.fdir.name
    for name, clips in ((old, {"listen": 300}), (cur, {"work": 200, "fl-chin": 100, "listen": 300})):
        for c, n in clips.items():
            (tmp_path / name / c).mkdir(parents=True, exist_ok=True)
            t.sizes.setdefault(name, {})[c] = n
    os.utime(tmp_path / cur / "work", (1, 1)); os.utime(tmp_path / cur / "fl-chin", (2, 2))
    t.used = 900
    t.playing = (cur, "work")                                          # oldest, but on the glass
    assert t._evict_one() and old not in t.sizes                        # another framing first
    assert t._evict_one() and "fl-chin" not in t.sizes[cur] and "work" in t.sizes[cur]
    assert t.n["evicted_framings"] == 1 and t.n["evicted_clips"] == 1


def test_a_full_memory_tier_evicts_a_clip_she_passes_through_before_a_loop(disk, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "0")
    g = R.Geom(W, H, density=1.0)
    c = _cache()
    p, _, _ = c.picture(_jpeg(_scene(980)), g, True, 0.0, clip=("listen", 0, 9, "loop"))
    c.cap = 4 * p.nbytes + 4 * 60000
    for i in range(1, 3):
        c.picture(_jpeg(_scene(980 + i)), g, True, 0.0, clip=("listen", i, 9, "loop"))
    c.picture(_jpeg(_scene(990)), g, True, 0.0, clip=("fl-chin", 0, 4, "flourish"))
    c.picture(_jpeg(_scene(983)), g, True, 0.0, clip=("listen", 3, 9, "loop"))
    c.picture(_jpeg(_scene(984)), g, True, 0.0, clip=("listen", 4, 9, "loop"))
    roles = [q.clip[2] for q in c.pics.values()]
    # the flourish made room for listen 3; listen 4 found only young loops (kept: a loop longer than the budget keeps
    # what fits, never thrashing)
    assert roles.count("flourish") == 0 and c.n["passed"] == 1 and c.n["refused"] == 1


def test_the_prefill_runs_at_idle_priority_and_waits_while_she_is_on_air():
    src = (ROOT / "lib" / "wall_ascii_disk.py").read_text()
    assert "os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))" in src and "os.nice(19)" in src
    assert "if _quiet(feed, ear):" in src and "go.clear()" in src
    assert D.prefill_enabled({}) and not D.prefill_enabled({"HOUSE_WALL_ASCII_PREFILL": "0"})


def test_the_player_names_each_frames_clip_and_the_wall_reads_it():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'CLIP_HEADER = os.environ.get("HOUSE_CONDUCTOR_CLIP_HEADER", "1")' in src
    assert "self.frame_clip = clip_id" in src and "self.frame_mouth = mouth_box" in src
    ns: dict = {}
    head = "CLIP_HEADER = " + src.split("\nCLIP_HEADER = ", 1)[1].split("# Mouth composite", 1)[0]
    exec("import os, re\n" + head, ns)
    assert ns["clip_header_value"](("listen", 12, 403, "loop")) == "listen;12;403;loop"
    assert ns["clip_header_value"](("a;b", 1, 2, "x y")) == "a_b;1;2;x_y"
    assert ns["clip_header_value"](None) == ""
    import house_wall_ascii as Wl
    assert Wl._clip_of(b" listen;12;403;loop") == ("listen", 12, 403, "loop")
    assert Wl._clip_of(b"garbage") is None


def test_a_lap_line_says_where_the_pictures_came_from(monkeypatch):
    import house_wall_ascii as Wl
    lines = []
    monkeypatch.setattr(Wl, "log", lines.append)
    lap = {"clip": None, "last": -1, "n": 0, "t0": 0.0, "mem": 0, "disk": 0, "fresh": 0}
    for i, src in enumerate(["disk", "disk", "fresh", "mem"]):
        Wl._lap_count(lap, ("listen", i, 4, "loop"), src, 10.0 + i)
    Wl._lap_count(lap, ("listen", 0, 4, "loop"), "mem", 14.0)
    assert lines == ["ascii lap: listen 4 pictures in 4.0 s: memory 1, disk 2, fresh 1 (4 frames in the clip)"]


def test_the_wall_package_carries_the_disk_tier():
    import json
    pk = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]["crissa-wall-linux"]
    assert "wall_ascii_disk.py" in pk["lib"]
