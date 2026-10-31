"""The Wall's memory warms ahead of the film (the User 2026-10-16)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wall_ascii_cache as C  # noqa: E402


class _Disk:
    def __init__(self, have):
        self.have = have
        self.asked = []

    def lookup(self, key, fr):
        self.asked.append(fr)
        return ("d", "p") if fr in self.have else None


class _Q:
    kept = True


def _cache(disk, env):
    c = C.AsciiCache.__new__(C.AsciiCache)
    c.disk, c.env, c.by_clip, c.fk_now, c.n = disk, env, {}, "fk", {}
    c.loaded = []

    def from_disk(g, clip, ent):
        c.loaded.append(clip[1])
        c.by_clip[("fk", clip[0], clip[1])] = 1
        return _Q()
    c._from_disk = from_disk
    return c


def test_reads_the_next_frames_wrapping_the_lap():
    d = _Disk({9, 0, 1})
    c = _cache(d, {"HOUSE_WALL_ASCII_READ_AHEAD": "1"})
    c.by_clip[("fk", "listen", 1)] = 7                 # already in memory: not read again
    assert c.read_ahead(None, ("listen", 8, 10, "loop"), n=4, budget_s=1.0) == 2
    assert c.loaded == [9, 0] and 1 not in d.asked
    assert c.n["ahead"] == 2


def test_off_and_house_default(monkeypatch):
    c = _cache(_Disk({1}), {"HOUSE_WALL_ASCII_READ_AHEAD": "0"})
    assert c.read_ahead(None, ("listen", 0, 10, "loop"), n=2, budget_s=1.0) == 0
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert C.read_ahead_on({}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert C.read_ahead_on({}) is True


def test_no_clip_no_disk_is_nothing():
    c = _cache(None, {"HOUSE_WALL_ASCII_READ_AHEAD": "1"})
    assert c.read_ahead(None, ("listen", 0, 10, "loop")) == 0
    c = _cache(_Disk(set()), {"HOUSE_WALL_ASCII_READ_AHEAD": "1"})
    assert c.read_ahead(None, None) == 0


def test_the_film_child_reads_ahead_after_sending():
    src = (ROOT / "lib" / "wall_film_proc.py").read_text()
    i = src.index("out.write(_HDR.pack(len(meta)) + meta)")
    assert "cache.read_ahead(g, _clip_of(req.get(\"clip\")))" in src[i:i + 400]


def test_hud_animates_slower_in_a_pack_and_is_counted():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'HUD_ANIM_HZ = _env_f("HOUSE_WALL_HUD_ANIM_HZ", 30.0 if crissa_home.is_house() else 12.0, 2.0, 30.0)' in src
    assert "time.sleep(1.0 / HUD_ANIM_HZ if animating else 0.1)" in src
    assert 'speed["hud_n"] = speed.get("hud_n", 0) + 1' in src and "x{_hud_n /" in src
