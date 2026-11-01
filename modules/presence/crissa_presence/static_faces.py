"""Static-face killbook — faces that live in the EMPTY room can never be people.

Root cause 2026-08-07 (presence-decision-trace-20260807.md): the hallway
portraits seen through the open door and the Smiths/Nirvana posters on the
door's inner face were minting/refreshing "another person" (portrait woman matched
at sim 0.7477 in auth_hold). Any face detected in a verifiably-empty-room
reference frame is by definition not a person: enroll it here and refuse it
at identity-match time.

Build (run on every ref promotion; refs from Grok's room-reference-capture,
which only promotes bright verified-empty frames):

    PYTHONPATH=src python -m crissa_presence.static_faces build

Threshold doctrine: a LIVE frame of the same physical portrait re-embeds at
~0.85+ against its ref embedding (same image, angles covered by the
open/closed ref variants). A real person compared to a *portrait of
themselves* lands ~0.55-0.75 (different photo). So the strict gate
RAIL_STATIC_FACE_SIM=0.80 suppresses the object without ever suppressing the
person. Kill-switch: RAIL_STATIC_FACE_KILLBOOK=0.
"""
from __future__ import annotations

from . import paths as _paths

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from .refs import LIVE_DIR as REF_DIR

_REPO = _paths.ROOT
KILLBOOK_PATH = Path(
    os.environ.get("RAIL_STATIC_FACES", str(_REPO / "data" / "static_faces.json"))
)
SIM_THR = float(os.environ.get("RAIL_STATIC_FACE_SIM", "0.80"))
# Positional blanking: how far outside a reference face's box the centre of a
# detection may fall (fraction of that box), and how different in size the two
# may be before they are no longer "the same thing in the same place".
STATIC_POS_PAD = float(os.environ.get("RAIL_STATIC_POS_PAD", "0.6"))
STATIC_SIZE_RATIO = float(os.environ.get("RAIL_STATIC_SIZE_RATIO", "2.2"))
ENABLED = os.environ.get("RAIL_STATIC_FACE_KILLBOOK", "1") == "1"


class KillBook:
    """mtime-reloading store of static-face embeddings."""

    def __init__(self, path: Path = KILLBOOK_PATH):
        self.path = Path(path)
        self._mtime = 0.0
        self._embs: np.ndarray | None = None  # (N, 512) L2-normalised
        self._meta: list[dict] = []

    def _load(self) -> None:
        try:
            m = self.path.stat().st_mtime
        except OSError:
            self._embs, self._meta, self._mtime = None, [], 0.0
            return
        if m == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text())
            entries = data.get("faces") or []
            embs = np.asarray(
                [e["emb"] for e in entries], dtype=np.float32
            )
            if embs.ndim == 2 and len(embs):
                n = np.linalg.norm(embs, axis=1, keepdims=True)
                n[n < 1e-6] = 1.0
                self._embs = embs / n
                self._meta = entries
            else:
                self._embs, self._meta = None, []
            self._mtime = m
        except Exception:
            # unreadable killbook must never take identity down with it
            self._embs, self._meta, self._mtime = None, [], m

    def static_sim(self, emb) -> tuple[float, dict | None]:
        """Best cosine sim against the killbook (and the matching entry)."""
        self._load()
        if self._embs is None:
            return -1.0, None
        e = np.asarray(emb, dtype=np.float32)
        n = float(np.linalg.norm(e))
        if n < 1e-6:
            return -1.0, None
        sims = self._embs @ (e / n)
        i = int(np.argmax(sims))
        return float(sims[i]), self._meta[i]

    def static_at(
        self, bbox, frame_shape=None, stop: str | None = None
    ) -> tuple[bool, dict | None]:
        """Is there a reference face at THIS PLACE, of about this size?

        `stop` scopes the boxes to the pose they were captured at. A desk-ref
        box must not blank a face at seat_12 (measured 2026-08-18: a guest).

        Embedding matching cannot blank the hallway portraits and never could.
        Measured 2026-08-16 against references captured seconds earlier at the
        identical pose: the same physical pictures re-detect at 0.67-0.79, all
        below SIM_THR 0.80, so 0 of 5 were blanked. A 15-25px face yields an
        unstable vector — the docstring's assumed "~0.95 re-detect" holds for a
        large face and not for these.

        Position does not drift. A picture on a wall is in the same pixels in
        every reference frame taken at that pose, which is precisely what a
        reference frame is FOR (the User 2026-08-16: "back to reference frame as
        base ... I have handled baloons and phantoms since the begining but
        cant seem to get the references blanked out properly").

        Size agreement is what keeps this safe. The old hand-drawn rectangle
        mask vetoed REAL faces because it blanked a region outright. Here a
        detection must also be about as small as the reference face, and a
        person's face at the door is 96-272px against a 14-25px portrait — so
        someone standing directly in front of the pictures is never blanked.

        Re-derives itself whenever the refs are recaptured, so a re-aim or a
        zoom change fixes it instead of breaking it.
        """
        self._load()
        if not self._meta:
            return False, None
        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
        except (TypeError, ValueError):
            return False, None
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
        for m in self._meta:
            if stop and not _source_for_stop(m.get("source_ref"), stop):
                continue
            rb = m.get("bbox")
            if not rb:
                continue
            try:
                rx1, ry1, rx2, ry2 = [float(v) for v in rb]
            except (TypeError, ValueError):
                continue
            rw, rh = max(1.0, rx2 - rx1), max(1.0, ry2 - ry1)
            pad_x, pad_y = rw * STATIC_POS_PAD, rh * STATIC_POS_PAD
            if not (rx1 - pad_x <= cx <= rx2 + pad_x and ry1 - pad_y <= cy <= ry2 + pad_y):
                continue
            # same place — but is it the same SIZE of thing?
            ratio = max(w / rw, rw / w, h / rh, rh / h)
            if ratio <= STATIC_SIZE_RATIO:
                return True, m
        return False, None

    def is_static(self, emb, sim_thr: float = SIM_THR) -> tuple[bool, float]:
        if not ENABLED:
            return False, -1.0
        s, meta = self.static_sim(emb)
        # Per-entry adaptive gate: entries that resemble an enrolled person
        # (e.g. the family portrait CONTAINS another person) carry a raised "thr"
        # (enrolled_max + margin) so the physical object (~0.95 re-detect)
        # is still killed while the real person keeps distance from the gate.
        thr = sim_thr
        if meta and meta.get("thr"):
            try:
                thr = max(thr, float(meta["thr"]))
            except (TypeError, ValueError):
                pass
        return s >= thr, s


_SINGLETON: KillBook | None = None


def killbook() -> KillBook:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = KillBook()
    return _SINGLETON


# ── Face zone masks (the User 2026-08-07: "block out the screen area for faces —
# it is zone 9"). Per-PTZ-stop normalized rects where face detections are
# DISCARDED: the desk screens (station 9, media) play faces that are neither
# static (killbook can't enroll a movie) nor people. data/face_zone_masks.json:
#   {"stop5": [{"x0":..,"y0":..,"x1":..,"y1":..,"label":"ultrawide"}, ...]}
ZONE_MASKS_PATH = Path(
    os.environ.get(
        "RAIL_FACE_ZONE_MASKS", str(_REPO / "data" / "face_zone_masks.json")
    )
)
_ZONE_CACHE: tuple[float, dict] = (0.0, {})


# Minimum face size for IDENTITY (the User 2026-08-09 03:35 — "Oracle thinks I am
# another person again"). At night the hallway portraits re-embed differently and
# slid under the killbook gate (0.63-0.65) while matching another person at 0.52-0.61.
# But their faces are 14x21 px across a hallway; a person at the door is 5-10x
# that. Size is lighting-, position- and pose-independent, so it holds when
# cosine cannot. Anything smaller may still be SEEN — it just cannot NAME.
MIN_FACE_PX = int(os.environ.get("RAIL_MIN_FACE_PX", "45"))

# Position boxes are pose-specific. Tokens must match the source_ref name.
_STOP_REF_TOKENS = {
    "desk": ("desk", "st6"),
    "another person": ("another person", "st8"),
    "stand_11": ("stand_11", "stand11"),
    "seat_12": ("seat_12", "seat12"),
    "door": (
        "door-closed",
        "home-empty-closed",
        "room-empty-stop-door",
        "door_home",
    ),
    "door_home": (
        "door-closed",
        "home-empty-closed",
        "room-empty-stop-door",
        "door_home",
    ),
}


def _source_for_stop(source_ref: str | None, stop: str | None) -> bool:
    """True when this killbook entry was taken at `stop` (or stop unknown)."""
    if not stop:
        return True
    ref = (source_ref or "").lower()
    if not ref:
        return False
    st = str(stop).lower().replace("-", "_")
    tokens = _STOP_REF_TOKENS.get(st)
    if tokens is None:
        return st in ref
    return any(t in ref for t in tokens)


def too_small_to_name(bbox, min_px: int | None = None) -> bool:
    """True when a face is too small to be a person in this room."""
    if bbox is None:
        return False
    try:
        h = float(bbox[3]) - float(bbox[1])
        w = float(bbox[2]) - float(bbox[0])
    except Exception:
        return False
    thr = MIN_FACE_PX if min_px is None else min_px
    return max(h, w) < thr


def in_masked_zone(stop: str | None, bbox, frame_shape) -> bool:
    """True when a face bbox center falls inside a masked zone for this stop."""
    global _ZONE_CACHE
    if stop is None or bbox is None:
        return False
    try:
        m = ZONE_MASKS_PATH.stat().st_mtime
    except OSError:
        return False
    if m != _ZONE_CACHE[0]:
        try:
            _ZONE_CACHE = (m, json.loads(ZONE_MASKS_PATH.read_text()))
        except Exception:
            _ZONE_CACHE = (m, {})
    zones = _ZONE_CACHE[1].get(str(stop)) or []
    if not zones:
        return False
    try:
        h, w = int(frame_shape[0]), int(frame_shape[1])
        cx = (float(bbox[0]) + float(bbox[2])) / 2.0 / max(w, 1)
        cy = (float(bbox[1]) + float(bbox[3])) / 2.0 / max(h, 1)
    except Exception:
        return False
    for z in zones:
        try:
            if z["x0"] <= cx <= z["x1"] and z["y0"] <= cy <= z["y1"]:
                return True
        except Exception:
            continue
    return False


# Manually verified person-free frames covering angles the refs miss (e.g.
# the fully-open-door hallway view). Drop a frame here ONLY after a human/
# operator eye-check confirms no live person is in it.
EXTRA_SOURCES = Path(
    os.environ.get(
        "RAIL_STATIC_FACE_SOURCES", str(_REPO / "data" / "static_face_sources")
    )
)


def build(ref_dir: Path = REF_DIR, out_path: Path = KILLBOOK_PATH) -> dict:
    """Scan empty-room reference frames (+ verified extras) -> static_faces.json."""
    import cv2

    from .face_engine import FaceEngine

    eng = FaceEngine()
    faces: list[dict] = []
    scanned: list[str] = []
    sources = sorted(ref_dir.glob("*.jpg"))
    if EXTRA_SOURCES.is_dir():
        sources += sorted(EXTRA_SOURCES.glob("*.jpg"))
    seen_hash: set[str] = set()
    for fn in sources:
        try:
            digest = hashlib.md5(fn.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in seen_hash:
            scanned.append(f"{fn.name} (dup {digest[:8]})")
            continue
        seen_hash.add(digest)
        img = cv2.imread(str(fn))
        if img is None:
            continue
        scanned.append(fn.name)
        for f in eng.detect(img):
            emb = FaceEngine.embed(f)
            if emb is None:
                continue
            bbox = FaceEngine.bbox(f)
            faces.append(
                {
                    "emb": [round(float(x), 6) for x in emb],
                    "bbox": [int(v) for v in bbox] if bbox is not None else None,
                    "det_score": round(FaceEngine.det_score(f), 4),
                    "source_ref": fn.name,
                    "ref_mtime": fn.stat().st_mtime,
                }
            )
    # Merge the persistent side-bank: embeddings harvested from VERIFIED
    # phantom packs (frames may be pruned later; the embeddings must survive
    # every rebuild). data/static_faces_extra.json, same entry shape.
    extra_path = out_path.parent / "static_faces_extra.json"
    if extra_path.is_file():
        try:
            extra = json.loads(extra_path.read_text()).get("faces") or []
            faces.extend(extra)
            scanned.append(f"static_faces_extra.json ({len(extra)})")
        except Exception:
            pass
    # Per-entry adaptive thresholds: entries resembling an enrolled person
    # (portrait CONTAINING a resident) get thr = enrolled_max + 0.05 so the
    # object is still killed at ~0.95 re-detect while the live person keeps
    # margin below the gate.
    try:
        from .enrollments import load_enrolled

        bank = load_enrolled()
        for f in faces:
            e = np.asarray(f["emb"], dtype=np.float32)
            n = float(np.linalg.norm(e))
            if n > 1e-6:
                e = e / n
            emax = max(
                (float(np.dot(e, k)) for embs in bank.values() for k in embs),
                default=-1.0,
            )
            if emax + 0.05 > SIM_THR:
                f["thr"] = round(emax + 0.05, 4)
    except Exception:
        pass
    out = {
        "built_ts": time.time(),
        "ref_dir": str(ref_dir),
        "scanned": scanned,
        "faces": faces,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(out_path)
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "build":
        res = build()
        by_ref: dict[str, int] = {}
        for f in res["faces"]:
            by_ref[f["source_ref"]] = by_ref.get(f["source_ref"], 0) + 1
        print(
            f"killbook: {len(res['faces'])} static face(s) "
            f"from {len(res['scanned'])} ref(s) -> {KILLBOOK_PATH}"
        )
        for k, v in sorted(by_ref.items()):
            print(f"  {k}: {v}")
    else:
        print("usage: python -m crissa_presence.static_faces build")


def blanked_by_reference(
    emb=None, bbox=None, frame_shape=None, stop: str | None = None
) -> tuple[bool, str]:
    """Does a reference frame already contain this face, here, at this size?

    Named apart from door_background.is_furniture on purpose: that one asks
    "is there any foreground here at all" by subtraction, this one asks "is
    this exact face already in the empty-room reference". They are the same
    idea at different resolutions and both are wanted.

    Two comparisons against the same reference set, because they fail in
    opposite conditions:

      embedding — reliable for a large, well-lit static face; useless below
        about 30px, where the same picture re-detects at 0.62-0.79 (measured
        2026-08-16 against refs taken seconds earlier at the identical pose).
      position  — reliable for anything bolted to a wall, at any size, and it
        is exactly what a reference frame records. Guarded by size agreement so
        a person standing in front of the pictures is never blanked.

    Returns (verdict, why) so the reason can be logged rather than inferred.
    """
    kb = killbook()
    if emb is not None:
        try:
            if kb.is_static(emb)[0]:
                return True, "static_emb"
        except Exception:
            pass
    if bbox is not None:
        try:
            hit, meta = kb.static_at(bbox, frame_shape, stop=stop)
            if hit:
                return True, "static_pos:" + str((meta or {}).get("source_ref") or "ref")
        except Exception:
            pass
    return False, ""

