"""Door background filter C (the User 2026-10-01): faces that belong to the room are not a glance. NOT door_background.py (the 08-15 subtraction).

The door view is wide. A face that sits in the same place, at the same size, sighting after sighting for
minutes - a portrait on the hallway wall, someone seated in the corner of the view - is background. Only a
NEW face, or one that MOVES, may count toward the door glance the wall turns its head for.

Background is two things, and nothing else (no masked regions, ever):
  1. the resting reference set: the killbook's `static_at` (same place + same size as a face found in an
     empty-room reference frame; the 08-16 rule that blanks the hallway portraits);
  2. repeat sightings: a face seen at least BG_MIN_HITS times at the same spot and size, spread over at least
     BG_SPAN_S seconds, with no gap longer than BG_GAP_S between sightings. A face that moves starts a new
     track and is foreground again until it, too, has stayed put for minutes.

Position + size only - never who it is - so the rule is the same for a picture and a person. Naming, AUTH and
the Secure tripwire never read this: it only changes the door glance's published `n_faces`.

RAIL_DOOR_BG_FILTER = off (default: nothing learned, nothing changed) | report (learn + publish the split,
`n_faces` unchanged) | on (`n_faces` is the foreground count; `n_faces_raw` keeps the detector's).
Tracks persist in data/door_background.json so a restart does not forget a seated face.
"""
from __future__ import annotations

from . import paths as _paths

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

MODE_ENV = "RAIL_DOOR_BG_FILTER"
BG_MIN_HITS = int(os.environ.get("RAIL_DOOR_BG_MIN_HITS", "3"))
BG_SPAN_S = float(os.environ.get("RAIL_DOOR_BG_SPAN_S", "120"))
BG_GAP_S = float(os.environ.get("RAIL_DOOR_BG_GAP_S", "120"))
BG_POS_PAD = float(os.environ.get("RAIL_DOOR_BG_POS_PAD", "0.35"))  # centre within 0.35 x face size
BG_SIZE_RATIO = float(os.environ.get("RAIL_DOOR_BG_SIZE_RATIO", "1.35"))
BG_SAVE_EVERY_S = 5.0
MAX_TRACKS = 64

DEFAULT_PATH = _paths.DATA / "door_bg_filter.json"


def mode() -> str:
    m = (os.environ.get(MODE_ENV) or "off").strip().lower()
    return m if m in ("off", "report", "on") else "off"


def _box(bbox) -> tuple[float, float, float] | None:
    try:
        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    except (TypeError, ValueError, IndexError):
        return None
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, max(w, h)


class DoorBackground:
    def __init__(self, path: Path | None = None, *, persist: bool = True) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.persist = persist
        self.tracks: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._saved_at = 0.0
        self._load()

    def _load(self) -> None:
        if not self.persist:
            return
        try:
            d = json.loads(self.path.read_text())
            self.tracks = [t for t in (d.get("tracks") or []) if isinstance(t, dict)]
        except (OSError, ValueError):
            self.tracks = []

    def _save(self, now: float, force: bool = False) -> None:
        if not self.persist or (not force and now - self._saved_at < BG_SAVE_EVERY_S):
            return
        self._saved_at = now
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"schema": "door_background/v1", "saved": now, "tracks": self.tracks}))
            os.replace(tmp, self.path)
        except OSError:
            pass

    @staticmethod
    def is_background_track(t: dict, now: float) -> bool:
        return (
            int(t.get("hits") or 0) >= BG_MIN_HITS
            and float(t.get("last", 0)) - float(t.get("first", 0)) >= BG_SPAN_S
            and now - float(t.get("last", 0)) <= BG_GAP_S
        )

    def _find(self, cx: float, cy: float, size: float) -> dict | None:
        best, best_d = None, None
        for t in self.tracks:
            ts = float(t["size"])
            if max(size / ts, ts / size) > BG_SIZE_RATIO:
                continue
            pad = BG_POS_PAD * max(size, ts)
            dx, dy = abs(cx - float(t["cx"])), abs(cy - float(t["cy"]))
            if dx > pad or dy > pad:
                continue
            d = dx + dy
            if best_d is None or d < best_d:
                best, best_d = t, d
        return best

    def observe(self, bboxes: list, *, now: float | None = None, static_at=None) -> list[dict[str, Any]]:
        """One door frame's detections -> per face {background, why, track_age_s}.

        A face is judged against what was known BEFORE this frame, then the frame is learned: a face is never
        its own witness. `static_at(bbox) -> (hit, meta)` is the killbook's resting-reference test.
        """
        now = time.time() if now is None else float(now)
        out: list[dict[str, Any]] = []
        with self._lock:
            # a track whose gap ran out is forgotten: whoever sat there left
            self.tracks = [t for t in self.tracks if now - float(t.get("last", 0)) <= BG_GAP_S]
            seen: set[int] = set()
            for bb in bboxes or []:
                b = _box(bb)
                if b is None:
                    out.append({"background": False, "why": "no_box"})
                    continue
                cx, cy, size = b
                ref = False
                if static_at is not None:
                    try:
                        ref = bool(static_at(bb)[0])
                    except Exception:
                        ref = False
                t = self._find(cx, cy, size)
                bg_track = t is not None and self.is_background_track(t, now)
                why = "reference" if ref else ("stayed" if bg_track else ("new" if t is None else "settling"))
                out.append({
                    "background": bool(ref or bg_track),
                    "why": why,
                    "track_age_s": round(now - float(t["first"]), 1) if t else 0.0,
                    "track_hits": int(t["hits"]) if t else 0,
                })
                if t is None:
                    t = {"cx": cx, "cy": cy, "size": size, "first": now, "last": now, "hits": 0}
                    self.tracks.append(t)
                if id(t) in seen:
                    continue  # two faces on one track in one frame count one sighting
                seen.add(id(t))
                # the track follows a face that settles, it does not run after one that moves (that is _find's job)
                t["cx"] = 0.8 * float(t["cx"]) + 0.2 * cx
                t["cy"] = 0.8 * float(t["cy"]) + 0.2 * cy
                t["size"] = 0.8 * float(t["size"]) + 0.2 * size
                t["last"] = now
                t["hits"] = int(t["hits"]) + 1
            if len(self.tracks) > MAX_TRACKS:
                self.tracks.sort(key=lambda t: -float(t.get("last", 0)))
                del self.tracks[MAX_TRACKS:]
            self._save(now)
        return out


_SINGLETON: DoorBackground | None = None


def door_background() -> DoorBackground:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = DoorBackground()
    return _SINGLETON
