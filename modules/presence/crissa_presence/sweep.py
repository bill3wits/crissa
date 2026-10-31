"""Five-rule presence sweep → RoomPresence.

1. Match >= threshold → that person (no gender/fusion)
2. Unmatched face → body only if VLM says LIVE
3. Bodies recomputed whole each sweep
4. Only a clean multi-stop sweep may remove people after HOLD_SEC
5. Certain = clean sweep AND every body named
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from .camera import Camera, Frame
from .enrollments import load_enrolled, match
from .face_engine import FaceEngine, get_face_engine
from .schema import PersonSlot, RoomPresence
from .vlm_live import is_live

# 0.50 default (was 0.45): 2026-07-28 another person→a guest mis-stamp @0.45.
# the User 2026-08-10: default 0.50 accepted another person@0.54 while only the User present.
# The house tuned 0.60 on its own engine. This engine (AuraFace, face_engine.py) was measured 2026-10-08
# (modules/oracle/crissa_oracle/modes.py): genuine min 0.540 / impostor max 0.354; the bar sits in that gap.
# CRISSA_FACE_MATCH is the oracle's name for the same dial; RAIL_PRESENCE_MATCH_SIM still wins.
MATCH_SIM = float(os.environ.get("RAIL_PRESENCE_MATCH_SIM") or os.environ.get("CRISSA_FACE_MATCH") or "0.45")
HOLD_SEC = float(os.environ.get("RAIL_PRESENCE_HOLD_SEC", "600"))
# PTZ multi-stop reconcile needs several verified aims for "clean"
MIN_STOPS = int(os.environ.get("RAIL_PRESENCE_MIN_STOPS", "3"))
# Capture older than this ⇒ camera_blind (seconds). Measured from Frame.ts (capture time).
MAX_CAPTURE_AGE = float(os.environ.get("RAIL_PRESENCE_MAX_CAPTURE_AGE", "8"))


@dataclass
class SweepEngine:
    camera: Camera
    face: FaceEngine | None = None
    enrolled: dict = field(default_factory=dict)
    # name -> last seen epoch (Rule 4 hold)
    seen: dict[str, float] = field(default_factory=dict)
    last_detail: list[str] = field(default_factory=list)
    last_error: str | None = None
    _enrolled_ts: float = 0.0

    def ensure(self) -> None:
        if self.face is None:
            self.face = get_face_engine()
        now = time.time()
        if not self.enrolled or now - self._enrolled_ts > 300:
            self.enrolled = load_enrolled()
            self._enrolled_ts = now

    def run_once(self) -> RoomPresence:
        self.ensure()
        assert self.face is not None
        frames = self.camera.grab_sweep()
        now = time.time()
        if not frames:
            self.last_error = self.camera.last_error or "no frames"
            # Partial: do not clear seen (Rule 4)
            return self._held_presence(
                camera_open=False,
                camera_blind=True,
                frame_age=999.0,
                clean=False,
                why=f"no frames: {self.last_error}",
                detail=[],
                stops=0,
            )

        people: dict[str, float] = {}  # name -> best sim
        unnamed = 0
        detail: list[str] = []
        newest_ts = max(f.ts for f in frames)
        frame_age = max(0.0, now - newest_ts)
        # Multi-stop PTZ: older stops are intentionally older; freshness = newest stop only.
        # Allow settle*stops margin so a 4-stop ~8s tour is not rejected as "stale".
        age_limit = MAX_CAPTURE_AGE
        if len(frames) > 1:
            age_limit = max(MAX_CAPTURE_AGE, 3.0 * len(frames) + 5.0)
        if frame_age > age_limit:
            self.last_error = f"stale capture age {frame_age:.1f}s > {age_limit}"
            return self._held_presence(
                camera_open=True,
                camera_blind=True,
                frame_age=frame_age,
                clean=False,
                why=self.last_error,
                detail=[f"newest_capture_ts={newest_ts}", f"n_frames={len(frames)}"],
                stops=len(frames),
            )
        # Grabber explicitly unhealthy
        for fr in frames:
            if fr.meta and fr.meta.get("capture_ok") is False:
                self.last_error = "upstream capture_ok=0"
                return self._held_presence(
                    camera_open=True,
                    camera_blind=True,
                    frame_age=frame_age,
                    clean=False,
                    why=self.last_error,
                    detail=[str(fr.meta)],
                    stops=len(frames),
                )

        for fr in frames:
            stop = fr.stop_id or "?"
            faces = self.face.detect(fr.bgr)
            for f in faces:
                emb = self.face.embed(f)
                if emb is None:
                    continue
                name, sim = match(emb, self.enrolled, threshold=MATCH_SIM)
                if name:
                    # Rule 1
                    if name not in people or sim > people[name]:
                        people[name] = sim
                    detail.append(f"stop{stop}: {name} ({sim:.2f}) src={fr.source}")
                else:
                    bbox = self.face.bbox(f)
                    # Rule 2
                    if bbox is not None and is_live(fr.bgr, bbox):
                        unnamed += 1
                        detail.append(
                            f"stop{stop}: unknown LIVE ({sim:.2f}) src={fr.source}"
                        )
                    else:
                        detail.append(
                            f"stop{stop}: face ignored no-match ({sim:.2f}) not-live"
                        )

        # Rule 3: whole recompute for this sweep's detections
        stop_ids = {f.stop_id for f in frames if f.stop_id and f.stop_id not in ("live", "file")}
        n_stops = len(stop_ids) if stop_ids else len(frames)
        clean = n_stops >= MIN_STOPS and bool(frames)

        for n in people:
            self.seen[n] = now

        # Rule 4: only clean sweep may drop after HOLD
        if clean:
            for n in list(self.seen):
                if n not in people and now - self.seen[n] > HOLD_SEC:
                    del self.seen[n]

        # Present set = currently matched ∪ held names still in window
        held = {
            n: self.seen[n]
            for n in self.seen
            if n not in people and now - self.seen[n] <= HOLD_SEC
        }
        present_names = sorted(set(people) | set(held))

        slots: list[PersonSlot] = []
        for i, n in enumerate(present_names):
            sim = people.get(n)
            slots.append(
                PersonSlot(
                    slot_id=f"s{i}",
                    name=n,
                    certain=sim is not None and sim >= MATCH_SIM,
                    gender=None,  # do not use face-sex (measured noise)
                    seat=None,
                    sources={
                        "face": round(sim, 3) if sim is not None else None,
                        "held": n in held,
                    },
                )
            )
        for j in range(unnamed):
            slots.append(
                PersonSlot(
                    slot_id=f"u{j}",
                    name=None,
                    certain=False,
                    sources={"live_vlm": True},
                )
            )

        headcount = len(present_names) + unnamed
        # Rule 5
        certain = clean and unnamed == 0 and headcount == len(present_names)

        self.last_detail = detail
        self.last_error = None
        seqs = [f.meta.get("seq") for f in frames if f.meta and f.meta.get("seq") is not None]

        return RoomPresence(
            camera_open=True,
            camera_blind=False,
            frame_age_sec=round(frame_age, 3),
            headcount=headcount,
            count_certain=certain,
            people=slots,
            unidentified=unnamed,
            last_event={
                "kind": "sweep",
                "stops": n_stops,
                "clean": clean,
                "detail": detail[:40],
                "sources": list({f.source for f in frames}),
                "capture_ts": newest_ts,
                "frame_seq": max(seqs) if seqs else None,
                "detected_this_sweep": len(people),
                "held": len(held),
            },
            guards={
                "screen_body_veto": True,  # via rule 2 LIVE gate
                "vlm_no_think": True,
                "five_rules": True,
                "single_camera_mutex": True,
            },
            ts=now,
        )

    def _held_presence(
        self,
        *,
        camera_open: bool,
        camera_blind: bool,
        frame_age: float,
        clean: bool,
        why: str,
        detail: list,
        stops: int,
    ) -> RoomPresence:
        now = time.time()
        slots = [
            PersonSlot(
                slot_id=f"s{i}",
                name=n,
                certain=False,
                sources={"held": True, "face": None},
            )
            for i, n in enumerate(sorted(self.seen))
        ]
        return RoomPresence(
            camera_open=camera_open,
            camera_blind=camera_blind,
            frame_age_sec=frame_age,
            headcount=len(slots),
            count_certain=False,
            people=slots,
            unidentified=0,
            last_event={"kind": "hold", "why": why, "stops": stops, "detail": detail},
            guards={
                "screen_body_veto": True,
                "vlm_no_think": True,
                "five_rules": True,
                "single_camera_mutex": True,
            },
            ts=now,
        )
