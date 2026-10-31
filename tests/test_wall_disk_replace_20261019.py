"""Round 32 (2026-10-19, .101: `disk=14.3/99GB hits=2 bases=1 writes=0` after a restart): the pre-fill draws each clip
frame with no mouth painted, her listen loop is published with her idle mouth on every frame, so its key never
matched and store() refused a frame that had an entry. A live picture now replaces a PRE-FILL entry it does not match,
once; the next restart reads it back. A live entry is never replaced. Kill HOUSE_WALL_ASCII_DISK_REPLACE=0."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_disk as D  # noqa: E402
from test_wall_ascii_cache import W, H, _jpeg, _mouth, _scene  # noqa: E402
from test_wall_ascii_disk_20261012 import FR, _fresh, _new_cache, _settle, disk  # noqa: E402,F401

BOX = (330, 220, 60, 22)


def _mark_prefill(root: Path) -> int:
    n = 0
    for f in root.rglob("*.pic"):
        b = f.read_bytes()
        k = D._U32.unpack_from(b, 4)[0]
        hdr = json.loads(b[8:8 + k])
        hdr["prefill"] = True
        h2 = json.dumps(hdr).encode()
        f.write_bytes(b[:4] + D._U32.pack(len(h2)) + h2 + b[8 + k:])
        n += 1
    return n


def _lap(c, g, jps):
    return [c.picture(jp, g, True, 0.0, frame=FR, clip=("listen", i, len(jps), "loop")) for i, jp in enumerate(jps)]


def _wait(c, key, n):
    t0 = time.time()
    while c.disk.stats()[key] < n and time.time() - t0 < 20:
        time.sleep(0.05)


def test_a_painted_lap_replaces_the_prefill_once_and_the_next_restart_hits(disk, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_REPLACE", "1")
    g = R.Geom(W, H, density=1.0)
    idle = [_scene(980 + i) for i in range(3)]
    a = _new_cache(); a.prepare(g)
    _lap(a, g, [_jpeg(im) for im in idle])                  # stands in for the pre-fill: no mouth painted
    _settle(a, 3)
    assert _mark_prefill(disk) == 3
    painted = [_jpeg(_mouth(im, BOX, 0)) for im in idle]     # the live loop: her idle mouth on every frame
    b = _new_cache(); b.prepare(g)
    got = _lap(b, g, painted)
    assert [h for _, h, _ in got] == ["disk-region"] * 3
    assert all(bytes(p.px) == _fresh(jp, g) for (p, _, _), jp in zip(got, painted))
    _wait(b, "replaced", 3)
    assert b.disk.stats()["replaced"] == 3 and b.disk.stats()["writes"] == 3
    assert len(list(disk.rglob("*.pic"))) == 3               # replaced, not added beside
    c = _new_cache(); c.prepare(g)                           # the next restart
    got = _lap(c, g, painted)
    assert [h for _, h, _ in got] == ["disk"] * 3
    assert all(bytes(p.px) == _fresh(jp, g) for (p, _, _), jp in zip(got, painted))


def test_a_live_entry_is_never_replaced_and_the_switch_turns_it_off(disk, monkeypatch):
    g = R.Geom(W, H, density=1.0)
    idle = [_scene(990 + i) for i in range(2)]
    a = _new_cache(); a.prepare(g)
    _lap(a, g, [_jpeg(im) for im in idle])                  # written by a live lap: no `prefill` in its header
    _settle(a, 2)
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_REPLACE", "1")
    b = _new_cache(); b.prepare(g)
    _lap(b, g, [_jpeg(_mouth(im, BOX, 1)) for im in idle])
    time.sleep(0.5)
    assert b.disk.stats()["writes"] == 0 and b.disk.stats()["replaced"] == 0
    _mark_prefill(disk)
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_REPLACE", "0")
    c = _new_cache(); c.prepare(g)
    _lap(c, g, [_jpeg(_mouth(im, BOX, 1)) for im in idle])
    time.sleep(0.5)
    assert c.disk.stats()["writes"] == 0 and c.disk.stats()["replaced"] == 0


def test_off_on_the_house_on_in_a_pack(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert not D.replace_prefill_on({}) and D.replace_prefill_on({"HOUSE_WALL_ASCII_DISK_REPLACE": "1"})
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    assert D.replace_prefill_on({}) and not D.replace_prefill_on({"HOUSE_WALL_ASCII_DISK_REPLACE": "0"})
