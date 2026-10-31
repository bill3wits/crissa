"""The cheeks maps run AHEAD of the paint (the User 2026-10-13: "it just needs to pull from existing map ... some
frames in advance. So just a timing adjustment").

A cheeks map is (film frame x tile), and the tile is the sheet's class at that frame. Both are known before the
paint: the film plays its frames in order and the sheet arrives ahead of the voice. This stage
  - LEARNS: every paint tells it which map key (loop, frame, class) used (poly_cache.cheeks_maps_cached `tag`);
  - PLANS: the conductor hands it the next frames and the class the sheet holds at each (plan());
  - FETCHES: on its own thread, each planned key not already in memory comes in from the compressed memory tier or
    the disk (poly_cache.fetch_ahead), so the paint finds it in memory.
It never builds a map and never changes one: an entry is the same bytes the paint would have read itself, so the
picture is identical; only WHEN the read happens moves. What it learned is kept beside the disk tier
(ahead-journal.pkl), so a restart knows the keys at once. Kill: HOUSE_MOUTH_MAPS_AHEAD=0.
"""
from __future__ import annotations

import os
import pickle
import threading
import time
from collections import OrderedDict
from pathlib import Path

import poly_cache

PER_SLOT = 4             # keys remembered per (loop, frame, class): a note's series can use a few tiles
SLOTS = 60000            # ~ every frame of every loop x every class


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("HOUSE_MOUTH_MAPS_AHEAD", "1")).strip().lower() not in ("0", "false", "no", "off")


def frames_ahead(env=None) -> int:
    env = os.environ if env is None else env
    try:
        return max(1, min(48, int(env.get("HOUSE_MOUTH_MAPS_AHEAD_FRAMES", "12"))))
    except ValueError:
        return 12


class Ahead:
    def __init__(self, journal_path: Path | None = None, fetch=None, log=print, save_s: float = 120.0):
        self.path = Path(journal_path) if journal_path else None
        self.fetch = fetch or poly_cache.fetch_ahead
        self.log = log
        self.save_s = save_s
        self.slots: "OrderedDict[tuple, list]" = OrderedDict()
        self.stats = {"planned": 0, "hot": 0, "z": 0, "disk": 0, "none": 0, "plans": 0, "learned": 0}
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._plan: list = []
        self._dirty = False
        self._saved = time.monotonic()
        self._load()
        self._t = threading.Thread(target=self._run, name="maps-ahead", daemon=True)
        self._t.start()

    # ---- learn
    def note(self, loop: str, frame: int, tag: str, key, kb) -> None:
        slot = (loop, int(frame), tag)
        with self._lock:
            got = self.slots.get(slot)
            if got is None:
                got = self.slots[slot] = []
                if len(self.slots) > SLOTS:
                    self.slots.popitem(last=False)
            else:
                self.slots.move_to_end(slot)
            for i, (k, _b) in enumerate(got):
                if k == key:
                    if i:
                        got.insert(0, got.pop(i))
                    if kb is not None and got[0][1] is None:
                        got[0] = (key, kb)
                    return
            got.insert(0, (key, kb))
            del got[PER_SLOT:]
            self._dirty = True
            self.stats["learned"] += 1

    # ---- plan
    def plan(self, loop: str, frames_tags) -> None:
        """frames_tags: [(frame, tag), ...] nearest first. Replaces the previous plan."""
        with self._cv:
            self._plan = [(str(loop), int(f), str(t)) for f, t in frames_tags if t is not None]
            self.stats["plans"] += 1
            self._cv.notify()

    # ---- fetch
    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._plan:
                    self._cv.wait(timeout=self.save_s)
                    if not self._plan:
                        break
                plan, self._plan = self._plan, []
                keys = []
                for slot in plan:
                    keys.extend(self.slots.get(slot) or ())
            for key, kb in keys:
                try:
                    how = self.fetch(key, kb)
                except Exception as e:  # noqa: BLE001 - the ahead stage never breaks a paint
                    self.log("[maps-ahead] fetch failed: %s" % e)
                    how = "none"
                self.stats["planned"] += 1
                self.stats[how] = self.stats.get(how, 0) + 1
                with self._lock:
                    if self._plan:          # a newer plan came in: it starts nearer the playhead
                        break
            if self._dirty and time.monotonic() - self._saved > self.save_s:
                self.save()

    # ---- keep
    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            data = list(self.slots.items())
            self._dirty = False
        self._saved = time.monotonic()
        try:
            tmp = self.path.with_suffix(".tmp%d" % os.getpid())
            tmp.write_bytes(pickle.dumps(data, protocol=5))
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            self.log("[maps-ahead] save failed: %s" % e)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            for slot, keys in pickle.loads(self.path.read_bytes())[-SLOTS:]:
                self.slots[tuple(slot)] = list(keys)[:PER_SLOT]
        except Exception as e:  # noqa: BLE001 - a bad journal is an empty one
            self.log("[maps-ahead] journal unreadable, starting empty: %s" % e)
            self.slots.clear()

    def state(self) -> dict:
        with self._lock:
            return dict(self.stats, slots=len(self.slots), frames_ahead=frames_ahead())
