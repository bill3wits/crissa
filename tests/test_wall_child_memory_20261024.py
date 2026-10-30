""".101 round 46 O3 (2026-10-24): the film child held 15.9 GB RSS while the picture cache said held=5032MB; the house
child 6.2 GB over held=3613MB. The count was right - glibc kept what it let go: once the dynamic mmap threshold had
climbed past a picture, every unpacked picture and every numpy array came out of the brk heap, numpy's MADV_HUGEPAGE
flagged 64 heap mappings and 3.96 GB of them were huge pages pinned by sub-2 MB holes. The child fixes the threshold
at half a picture (lib/wall_film_proc.pictures_out_of_heap), and the stats line says what is beyond the count (`over=`).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import wall_film_proc as F  # noqa: E402

PROBE = r"""
import sys, os
sys.path.insert(0, %r)
import wall_film_proc as F
W, H = 2560, 1440
kb = F.pictures_out_of_heap(W, H) if os.environ.get("FIX") == "1" else 0
b = bytes(W * H); del b                       # the first free of a picture: glibc's dynamic threshold climbs past it
b = bytes(W * H)                              # the next one: heap under the dynamic default, its own map under the fix
addr = id(b)
where = "?"
for line in open("/proc/self/maps"):
    lo, hi = (int(x, 16) for x in line.split()[0].split("-"))
    if lo <= addr < hi:
        where = line.split()[5] if len(line.split()) > 5 else "anon"
        break
print(kb, where)
"""


def _probe(fix: bool) -> tuple[int, str]:
    env = dict(os.environ, FIX="1" if fix else "0")
    env.pop("HOUSE_WALL_FILM_MMAP_KB", None)
    out = subprocess.run([sys.executable, "-c", PROBE % str(ROOT / "lib")], env=env, capture_output=True, text=True,
                         check=True, timeout=60).stdout.split()
    return int(out[0]), out[1]


def test_the_threshold_is_half_a_picture_with_a_dial_and_a_kill():
    assert F.mmap_threshold_kb(2560, 1440, {}) == (2560 * 1440 // 2) // 1024 == 1800
    assert F.mmap_threshold_kb(3440, 1440, {}) == 2418
    assert F.mmap_threshold_kb(640, 360, {}) == F.MMAP_MIN_KB                 # never below glibc's floor
    assert F.mmap_threshold_kb(32000, 32000, {}) == F.MMAP_MAX_KB             # never above glibc's ceiling
    assert F.mmap_threshold_kb(2560, 1440, {"HOUSE_WALL_FILM_MMAP_KB": "4096"}) == 4096
    for off in ("0", "off", "no"):
        assert F.mmap_threshold_kb(2560, 1440, {"HOUSE_WALL_FILM_MMAP_KB": off}) == 0
        assert F.pictures_out_of_heap(2560, 1440, {"HOUSE_WALL_FILM_MMAP_KB": off}) == 0
    assert F.mmap_threshold_kb(2560, 1440, {"HOUSE_WALL_FILM_MMAP_KB": "junk"}) == 1800


def test_a_picture_sized_block_is_mapped_on_its_own_under_the_fix():
    if not sys.platform.startswith("linux") or not os.path.exists("/proc/self/maps"):
        return
    kb, where = _probe(fix=False)
    assert kb == 0 and where == "[heap]", (kb, where)       # glibc's dynamic default: the second picture is heap
    kb, where = _probe(fix=True)
    assert kb == 1800 and where == "anon", (kb, where)      # fixed at half a picture: its own mapping


def test_the_stats_carry_what_is_beyond_the_count():
    import wall_ascii_cache as C
    c = C.AsciiCache(log=lambda m: None, env={"HOUSE_WALL_ASCII_CACHE_MB": "64", "HOUSE_WALL_ASCII_DISK": "0"})
    st = c.stats()
    assert "over" in st and "rss" in st
    if st["rss"] is not None:
        assert st["over"] is not None and 0 <= st["over"] <= st["rss"]
        assert c.over(c.rss0 + c.held_bytes() + 7) == 7                       # beyond the harness and the count
        assert c.over(c.rss0) == 0                                            # never negative
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    assert src.count(' over=%.0fMB') == 1 and ' rss=%.0fMB' in src            # the line says both
