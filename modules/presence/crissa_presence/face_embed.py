"""Run the embedder on a frame Argus chose. Serving, not deciding.

the User 2026-08-16: *"Argus owns and serves the oracle"*, and, on the frame-picking
heuristics: *"could Argus treat Inspector as a tool. Run-embeds"*.

That inverts the flow. Until now code selected the face frame — by bbox area,
then by sharpness, then by sharpness plus yaw — and every version of that rule
was wrong in a way only visible by eye:

    bbox area                    picked a 166 px face blurred to mush   sim 0.217
    min(w,h)^2 "most pixels"     no better on that event                sim 0.217
    landmark hull area           same trap                              sim 0.217
    sharpness alone              picked the sharpest frame, a PROFILE   sim 0.395
    sharpness + yaw              best available on one event            sim 0.596

Each fix created the next failure, because every one of those measures is a
proxy for "is this a good look at a face" and none of them is the thing itself.
Argus can just look. Asked which frame showed the most face on the User's 07:11
entry it answered frame 1 and explained why — "most front-on... visibility
decreases as he rotates toward profile in later frames" — which is the actual
structure of the event.

So this module does no selection at all. It takes a frame someone else chose,
runs the detector and the bank, and reports what it found. The heuristics stay
in entry_face for the parallel path, so identity keeps two independent
selectors that can agree or disagree — Argus picking is not allowed to become a
gate on FaceID working.

Reports the quality measures alongside the match so a caller can see WHY a
frame scored badly, rather than only that it did.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def embed_frame(event_id: str, frame: str) -> dict[str, Any]:
    """Detect, embed and match the largest face in one stored evidence frame.

    Path-confined to the evidence directory — a caller may name a frame, never
    a filesystem location.
    """
    from .evidence_page import EVIDENCE_DIR

    if not event_id or not frame:
        return {"ok": False, "error": "need event_id and frame"}
    if "/" in frame or ".." in frame or ".." in event_id or "/" in event_id:
        return {"ok": False, "error": "bad name"}
    p = EVIDENCE_DIR / event_id / frame
    try:
        p = p.resolve()
        if EVIDENCE_DIR.resolve() not in p.parents or not p.is_file():
            return {"ok": False, "error": "not found"}
    except OSError:
        return {"ok": False, "error": "not found"}
    return embed_path(p)


def embed_path(p: Path) -> dict[str, Any]:
    """The same, for a path the rail already trusts."""
    import cv2

    from .enrollments import load_enrolled, load_person_stamps, match
    from .entry_face import FACEID_MIN_PX, sharpness, yaw_offset
    from .face_engine import FaceEngine, get_face_engine
    from .sweep import MATCH_SIM

    img = cv2.imread(str(p))
    if img is None:
        return {"ok": False, "error": "unreadable"}
    eng = get_face_engine()
    try:
        faces = eng.detect(img)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:120]}
    if not faces:
        return {"ok": True, "frame": p.name, "face": False, "reason": "no_face"}

    best: dict[str, Any] | None = None
    for f in faces:
        bb = FaceEngine.bbox(f)
        if bb is None:
            continue
        w = float(bb[2]) - float(bb[0])
        h = float(bb[3]) - float(bb[1])
        roi = img[max(0, int(bb[1])):int(bb[3]), max(0, int(bb[0])):int(bb[2])]
        cand = {
            "face_px": [int(w), int(h)],
            # Pixels that actually reach the embedder: it consumes a square, so
            # the short side is the limit however wide the box is.
            "embed_px": int(min(w, h)) ** 2,
            "sharpness": round(sharpness(roi), 1),
            "yaw": (lambda y: round(y, 2) if y is not None else None)(yaw_offset(f)),
            "det_score": round(FaceEngine.det_score(f), 4),
            "too_small": bool(min(w, h) < FACEID_MIN_PX),
        }
        emb = FaceEngine.embed(f)
        if emb is not None:
            nm, sim = match(
                emb, load_enrolled(), threshold=MATCH_SIM,
                stamps=load_person_stamps(),
            )
            cand["name"] = nm
            cand["sim"] = round(float(sim), 4)
        if best is None or cand["embed_px"] > best["embed_px"]:
            best = cand
    if best is None:
        return {"ok": True, "frame": p.name, "face": False, "reason": "no_bbox"}
    return {"ok": True, "frame": p.name, "face": True, "match_sim_bar": MATCH_SIM, **best}
