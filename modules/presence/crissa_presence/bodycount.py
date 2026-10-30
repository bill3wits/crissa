"""Count how many PEOPLE are in the room from one sweep's frames.

the User, 2026-07-22: "Can we program the body counter please? A sweep that counts how many people."

The hard part is not detection — it is NOT COUNTING THE SAME PERSON TWICE. One human is visible
from two stops, or twice in one frame's neighbours, and a naive counter returns two. Every phantom
in the old system was ultimately a double-count: a scalp crop counted beside its owner, a face on a
monitor counted as a guest, a near-miss score counted as a stranger standing next to himself.

So this counts CLUSTERS OF FACE EMBEDDINGS, not detections:

  1. every face in every frame of the sweep -> one embedding
  2. embeddings within DEDUPE_SIM of each other are THE SAME PERSON (one cluster)
  3. a cluster matching an enrolment above MATCH_SIM is that person, by name
  4. an unmatched cluster counts as a person ONLY if confirmed live (a face on a screen is not
     a body, and an unmatched face is exactly what a screen face looks like)
  5. the count is the number of clusters. Never a running tally, never incremented.

ANIMALS COUNT AS ZERO, FOR FREE. the User, 2026-07-22: "there is a dog but not a fourth" — correct,
and it is structural rather than lucky. A dog has no human face, so it never forms a cluster and
can never become a body. Asking a VLM "how many bodies are in this room" would very likely have
said four. This is a concrete reason to count FACE CLUSTERS rather than bodies: the question
"how many humans" is answered by the thing that is specifically human.

Deliberately NOT here: seat geometry, gender, posteriors, holds, guards. A count is a count.
Identity is attached where it is known and left blank where it is not — "3 people, two of them
the User and another person, one unknown" is a correct and useful answer, and far better than a guess.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

MATCH_SIM  = float(os.environ.get("RAIL_MATCH_SIM", "0.45"))    # enrolment match
DEDUPE_SIM = float(os.environ.get("RAIL_DEDUPE_SIM", "0.55"))   # same-person across frames


@dataclass
class Person:
    name: str | None = None          # None = present but unidentified
    best_sim: float = -1.0           # best enrolment similarity seen
    sightings: int = 0
    stops: list = field(default_factory=list)
    live_confirmed: bool = False
    _embs: list = field(default_factory=list)

    def centroid(self):
        m = np.mean(self._embs, axis=0)
        n = float(np.linalg.norm(m))
        return m / n if n > 1e-6 else m


def _norm(e):
    e = np.asarray(e, dtype=np.float32)
    n = float(np.linalg.norm(e))
    return None if n < 1e-6 else e / n


def count_bodies(detections, enrolled, is_live=None):
    """detections: iterable of (stop_id, embedding, live_check_callable_or_None).
    enrolled: {name: [normalised embeddings]}.
    is_live(det) -> bool, called ONLY for clusters that match nobody.

    Returns (people, detail) — people is the list of distinct humans found.
    """
    people: list[Person] = []
    detail: list[str] = []

    for stop, emb, live_ctx in detections:
        e = _norm(emb)
        if e is None:
            continue

        # Static-face killbook (2026-08-07): portraits/posters detected in a
        # sweep are objects — not bodies, not enrolments. Same gate as
        # enrollments.match() and oracle_bench's code room-check.
        try:
            from .static_faces import blanked_by_reference

            # bbox when the caller carried one — positional blanking is the
            # only thing that catches the hallway portraits, which re-detect at
            # 0.62-0.79 and never clear the 0.80 embedding gate.
            _bb = None
            if isinstance(live_ctx, dict):
                _bb = live_ctx.get("bbox") or live_ctx.get("box")
            if blanked_by_reference(e, _bb)[0]:
                continue
        except Exception:
            pass

        # 2. same person as one we already have?
        hit, hit_sim = None, -1.0
        for p in people:
            s = float(np.dot(e, p.centroid()))
            if s > hit_sim:
                hit_sim, hit = s, p
        if hit is not None and hit_sim >= DEDUPE_SIM:
            hit._embs.append(e)
            hit.sightings += 1
            if stop not in hit.stops:
                hit.stops.append(stop)
            detail.append(f"stop{stop}: {hit.name or 'unidentified person'} again "
                          f"({hit_sim:.2f}) — same person, not a new body")
            continue

        # 3. does this new cluster match an enrolment?
        name, sim = None, -1.0
        for nm, embs in enrolled.items():
            for k in embs:
                s = float(np.dot(e, k))
                if s > sim:
                    sim, name = s, nm
        if sim < MATCH_SIM:
            name = None

        # 4. an unmatched face must be confirmed live before it becomes a body
        live = True
        if name is None and is_live is not None:
            live = bool(is_live(live_ctx))
            if not live:
                detail.append(f"stop{stop}: face ignored — matches nobody ({sim:.2f}) "
                              f"and not confirmed live")
                continue

        p = Person(name=name, best_sim=sim, sightings=1, stops=[stop],
                   live_confirmed=(name is not None) or live, _embs=[e])
        people.append(p)
        detail.append(f"stop{stop}: {name or 'unidentified person'} "
                      f"({sim:.2f}){'' if name else ' — confirmed live'} — NEW body")

    return people, detail


def summarise(people, stops_covered: int, min_stops: int = 2) -> dict:
    """A count is only trustworthy if the sweep actually covered the room. One frame can never
    certify a room — that reasoning ('I saw nothing, so it is empty') cost five weeks."""
    named = sorted(p.name for p in people if p.name)
    unknown = sum(1 for p in people if not p.name)
    return {
        "bodies": len(people),
        "named": named,
        "unidentified": unknown,
        "stops_covered": stops_covered,
        "certain": stops_covered >= min_stops and unknown == 0 and len(people) > 0,
        "why": (f"{stops_covered} stops covered"
                + ("" if stops_covered >= min_stops else f" — need {min_stops} to certify")),
    }
