"""Round 26: the Wall's memory cache is sized from the machine (.101 10-16: `held=4096MB of 4096MB` on 91 GB, new clips
came fresh; the User: "why isn't wall ascii cached?")."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wall_ascii_cache as C  # noqa: E402

G, MB = 2 ** 30, 2 ** 20


def test_the_101_machine_holds_a_tenth_of_its_memory():
    assert C.default_mb({}, total=91 * G) == 0.10 * 91 * 1024                 # the cap: 9.1 GB
    assert C.budget(0, {}, avail=79 * G, total=91 * G) == int(0.10 * 79 * G)   # under 10 % of free: 7.9 GB
    assert C.budget(0, {}, avail=79 * G, total=91 * G) > 1.9 * 4096 * MB


def test_a_small_laptop_keeps_4096_and_free_memory_still_caps_it():
    assert C.budget(0, {}, avail=48 * G, total=16 * G) == 4096 * MB
    assert C.budget(0, {}, avail=6 * G, total=91 * G) == int(0.6 * G)


def test_the_owners_word_and_the_kill():
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_MB": "4096"}, avail=79 * G, total=91 * G) == 4096 * MB   # the house unit
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_RAM_SHARE": "0"}, avail=79 * G, total=91 * G) == 4096 * MB
    assert C.default_mb({}, total=None) == 4096
