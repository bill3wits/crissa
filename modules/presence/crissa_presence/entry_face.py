"""Best-face selection over a door event pack.

the User 2026-08-09: "there is a race to grab a frame after the event and so it
catches some and doesn't others. The video clips surely will include a good
face shot but must be selected based on face area available."

He is right. The old evidence path (`_capture_enter_stills`) waits
STILL_WAIT_SEC (~1s) after the mint and snapshots whatever the camera sees —
by then the person has walked out of the door FOV, so every one of the User's
stored "entry stills" is an EMPTY DOORWAY with the hallway portraits in it.
Useless as human-eye evidence and useless as identity.

The pack already holds 60-286 frames spanning the whole crossing. So: don't
race — ANALYSE. Detect faces across the pack, score by face AREA (pixels the
recognizer actually gets) weighted by detector confidence, and keep the best
frame. That frame becomes:

  1. the human-eye entry evidence (a real photo of who came in), and
  2. the identity sample — matched once, on the clearest look available,
     instead of on whichever buffer frame happened to win the race.

Gates from the phantom work still apply to every candidate: killbook,
door-zone mask, and the minimum-size rule (a hallway portrait is ~20 px).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Sample every Nth frame first (cheap pass), then refine around the winner.
STRIDE = int(os.environ.get("RAIL_ENTRY_FACE_STRIDE", "3"))
# A face must be at least this tall/wide to be considered a person at all.
# Hallway portraits are ~20 px. FaceID embed is 112×112; 60 px is the
# identity / "ship this shot" bar (same as RAIL_DOOR_V2_FACE_LOW_RES_PX).
MIN_PX = int(os.environ.get("RAIL_MIN_FACE_PX", "45"))
FACEID_MIN_PX = int(os.environ.get("RAIL_FACEID_MIN_PX", "60"))


# A face this blurred carries no identity no matter how large it is. Variance of
# the Laplacian is a focus measure, not a judgement — it is optics.
SHARP_FLOOR = float(os.environ.get("RAIL_FACE_SHARP_FLOOR", "90.0"))
# How hard a below-floor face is demoted. Not zero: a blurred crop still beats
# no crop when someone is standing at the door.
BLUR_DEMOTE = float(os.environ.get("RAIL_FACE_BLUR_DEMOTE", "0.05"))
# A true door view is preferred, not privileged. 1.5x breaks ties between
# comparable frames without letting a blurred close-up beat a sharp one.
DOOR_VIEW_BONUS = float(os.environ.get("RAIL_FACE_DOOR_VIEW_BONUS", "1.5"))


NO_EYES_DEMOTE = float(os.environ.get("RAIL_FACE_NO_EYES_DEMOTE", "0.02"))
MAX_YAW = float(os.environ.get("RAIL_FACE_MAX_YAW", "1.0"))
PROFILE_DEMOTE = float(os.environ.get("RAIL_FACE_PROFILE_DEMOTE", "0.05"))


def eyes_visible(face, shape) -> bool | None:
    """Are both eyes actually in the picture? None when there are no landmarks.

    the User 2026-08-16, on a crop that was a smiling mouth and chin with the head
    cut off above it: "This one needs eyes in the pic. We tell each other apart
    by the triangle from temples to chin i think."

    That is the criterion, and it is binary. Measured on ev_1786879907089:

        eyes in frame   (9 frames)   sim 0.567 - 0.649
        eyes cut off    (4 frames)   sim 0.219 - 0.309

    No overlap. And the eyeless frames are the LARGEST in the window — 202x243
    against 76x104 — because a face big enough to clip the frame edge is a face
    walking into the lens. So every area-ranking rule selects for exactly the
    frames that cannot identify anyone, and door_view=True boosts them again.
    """
    import numpy as np

    kps = getattr(face, "kps", None)
    if kps is None or len(kps) < 2:
        return None
    try:
        h, w = int(shape[0]), int(shape[1])
        return bool(
            all(0 <= float(kps[i][0]) < w and 0 <= float(kps[i][1]) < h
                for i in (0, 1))
        )
    except Exception:
        return None


def yaw_offset(face) -> float | None:
    """How far off-camera the head is turned. 0 = facing it. None = no landmarks.

    Nose displacement from the eye midpoint, in eye-separations. Pure geometry
    from the detector's own 5 keypoints — no model, no judgement.

    the User 2026-08-16, looking at a crop that was all ear and jaw: "you see the
    entry frames above the profile. wouldnt those be better?" They were, and by
    a lot. On his 07:11 entry:

        frame  w/h    yaw     sharp   sim
        001    0.60  -0.32    142.0   0.596   <- entry, best in the window
        016    0.55  -6.77    211.7   0.395   <- profile, SHARPEST in the window

    A profile is sharp precisely because the head has stopped turning, so a
    sharpness rule alone selects for it. Recognition needs a face, not an edge.
    """
    import numpy as np

    kps = getattr(face, "kps", None)
    if kps is None or len(kps) < 3:
        return None
    try:
        eye_mid = (kps[0] + kps[1]) / 2.0
        sep = float(np.linalg.norm(kps[1] - kps[0]))
        if sep <= 0:
            return None
        return float((kps[2][0] - eye_mid[0]) / sep)
    except Exception:
        return None


def sharpness(gray_or_bgr) -> float:
    """Focus of a face crop. Higher is sharper; motion blur drives it toward 0."""
    import cv2

    img = gray_or_bgr
    if img is None or getattr(img, "size", 0) == 0:
        return 0.0
    if len(img.shape) == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def _score(area_px: float, det: float, sharp: float | None = None,
           yaw: float | None = None, eyes: bool | None = None) -> float:
    """Sharp beats big. Measured 2026-08-16 on another person's 06:36 entry.

    The old rule was area x confidence, and on a real walk-in it chose the worst
    frame available. As she came closer the face grew and the detector's own
    confidence FELL, because she was moving fastest when nearest:

        frame     px        det     sharpness    sim vs bank
        004     68x95      0.829       201.6        0.586
        012    166x208     0.670        66.6        0.217   <- chosen by area

    Every large frame in that window scored ~0.2; every mid-size sharp one
    scored 0.54-0.59. Ranking by area picked a 166 px face blurred to mush and
    called her a stranger, and the room minted a scratch id for a woman it had
    the evidence to name. Area x confidence is anti-correlated with usable
    quality once the subject is moving.

    Sharpness is a CLIFF, not a gradient, so it gates rather than scales. In
    that same window 94.1 scored 0.593 and 66.6 scored 0.217 — a 1.4x difference
    in focus produced a 2.7x difference in similarity. Scaling by the ratio was
    tried first and still chose the blurred frame, because a 5x area advantage
    swamps any smooth penalty. Below the floor a face is demoted hard; above it,
    the biggest sharp face wins as before.

    A blurred face can still win when nothing else exists — the demotion is a
    factor, not a veto, because a bad crop beats no crop when someone is at the
    door. the User's framing (2026-08-16) is the physical one: take the RESTING
    frame, the moment of stillness, and lasso the face out of that.
    """
    base = float(area_px) * (0.5 + float(det))
    if sharp is not None and float(sharp) < SHARP_FLOOR:
        base *= BLUR_DEMOTE
    # A profile is not a face the bank can read. Demoted like blur, and for the
    # same reason: the pixels are there but the identity is not.
    if yaw is not None and abs(float(yaw)) > MAX_YAW:
        base *= PROFILE_DEMOTE
    # No eyes, no identity. Demoted hardest of the three: blur and pose degrade
    # a match, a missing temples-to-chin triangle removes it.
    if eyes is False:
        base *= NO_EYES_DEMOTE
    return base


def _iou(a, b) -> float:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    ax0, ay0, ax1, ay1 = [float(x) for x in a[:4]]
    bx0, by0, bx1, by1 = [float(x) for x in b[:4]]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    ub = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    den = ua + ub - inter
    return inter / den if den > 0 else 0.0


def _keep_top2(bucket: dict, cand: dict) -> None:
    """Keep the best face and a distinct runner-up (two people in one pack)."""
    if not bucket:
        bucket.update(cand)
        return
    same = _iou(bucket.get("bbox"), cand.get("bbox")) >= 0.35
    if same:
        if cand["score"] > float(bucket.get("score") or -1):
            second = bucket.get("_second")
            bucket.clear()
            bucket.update(cand)
            if second:
                bucket["_second"] = second
        return
    if cand["score"] > float(bucket.get("score") or -1):
        prev = {k: v for k, v in bucket.items() if k != "_second"}
        bucket.clear()
        bucket.update(cand)
        bucket["_second"] = prev
        return
    prev2 = bucket.get("_second")
    if prev2 is None or cand["score"] > float(prev2.get("score") or -1):
        bucket["_second"] = cand


# Fraction of the frame that may differ from the empty door-home view and
# still count as "the camera is looking at the door". A person in the doorway
# changes part of the frame; a facefinder zoom onto someone's chair changes
# nearly all of it.
DOOR_VIEW_MAX_CHANGED = float(os.environ.get("RAIL_DOOR_VIEW_MAX_CHANGED", "0.62"))


def is_door_view(img) -> bool | None:
    """Is this frame actually the door, or did the PTZ wander?

    the User 2026-08-09, on a 'leave the user' pack whose every frame was a facefinder
    close-up of another person in her chair: "A perfect full on face of another person
    called the user. How?" — because door_capture pulls from the camera ring
    buffer, so a pack carries whatever the PTZ happened to be aimed at, not
    necessarily the door. Crossing evidence must come from door frames only.

    None = cannot tell (no reference yet).
    """
    import cv2
    import numpy as np

    from .body_audit import DOOR_REF

    if not DOOR_REF.is_file() or img is None:
        return None
    ref = cv2.imread(str(DOOR_REF))
    if ref is None:
        return None
    h, w = img.shape[:2]
    r = cv2.resize(ref, (w, h)) if ref.shape[:2] != (h, w) else ref
    a = cv2.GaussianBlur(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY), (9, 9), 0)
    b = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (9, 9), 0)
    ma, mb = float(a.mean()), float(b.mean())
    if mb > 1.0:
        b = np.clip(b.astype(np.float32) * (ma / mb), 0, 255).astype("uint8")
    d = cv2.absdiff(a, b)
    changed = float((d > 30).mean())
    return changed <= DOOR_VIEW_MAX_CHANGED


def best_face_in_pack(
    pack_dir: str | Path,
    *,
    engine: Any = None,
    enrolled: dict | None = None,
    stamps: dict | None = None,
    max_scan: int | None = None,
    frames: list[Path] | list[str] | None = None,
) -> dict[str, Any]:
    """Scan a door pack; return the best face frame + its identity verdict.

    Returns {} when the pack holds no usable face (a real outcome — e.g. the
    person entered facing away; the body-delta path covers that case).
    max_scan caps how many frames the face engine sees (harvest keep-all packs).

    `frames` overrides the pack glob with an explicit, time-ordered list — used
    to hand this the ~10 raw frames around the crossing at FULL rate instead of
    a strided walk over a 300-frame harvest pack. The scan order is the only
    thing that changes: scoring, the door-view filter, the crop and the bank
    match are identical either way.

    Measured 2026-08-15: the User's 13:19 entry produced no face at all from the
    pack glob, while the frame where he is square to the camera (seq 107584)
    sat 8 frames after the motion onset — inside a window STRIDE stepped over.
    """
    import cv2
    import numpy as np

    from .enrollments import load_enrolled, load_person_stamps, match
    from .face_engine import FaceEngine
    from .door_background import foreground_mask, is_furniture
    from .static_faces import (blanked_by_reference, in_masked_zone,
                               killbook, too_small_to_name)
    from .sweep import MATCH_SIM

    pack = Path(pack_dir)
    # An explicit list is already the window the caller chose, so examine ALL of
    # it. STRIDE exists to skim a 300-frame harvest pack cheaply; applying it to
    # a hand-picked 17-frame window skips two frames in three. On the User's 07:11
    # entry that hid faceid_001 — the best face in the window at sim 0.596 —
    # and the scan settled for 0.479 from a frame it happened to land on.
    explicit = bool(frames)
    frames = [Path(p) for p in frames] if frames else sorted(pack.glob("f*.jpg"))
    if not frames:
        return {}
    eng = engine
    if eng is None:
        from .face_engine import get_face_engine

        eng = get_face_engine()
    bank = enrolled if enrolled is not None else load_enrolled()
    stamps = stamps if stamps is not None else load_person_stamps()
    kb = killbook()

    # the User 2026-08-09: entry selection is DOOR FOV only. Facefinder seat
    # zooms are reauth/error-correction evidence, never crossing evidence.
    # Prefer door_view=True; allow None (no ref yet); never False.
    best_door: dict[str, Any] = {}
    best_unknown: dict[str, Any] = {}
    step = 1 if explicit else max(1, STRIDE)
    if max_scan is not None and max_scan > 0 and len(frames) > max_scan * step:
        step = max(step, (len(frames) + max_scan - 1) // max_scan)
    for fp in frames[::step]:
        img = cv2.imread(str(fp))
        if img is None:
            continue
        door_view = is_door_view(img)
        # DEMOTE, do not drop (the User 2026-08-15). is_door_view calls a frame
        # "not the door" when too much changed against the empty-door reference
        # — but a person standing IN the doorway is the largest change there is.
        # Measured on ev_1786777424191: the only two usable faces, 139px and
        # 66px, were both door_view=False and thrown away, while the frames that
        # passed held 12-13px background faces. 795 harvest packs produced zero
        # crops. The preference for a true door view is kept (best_door still
        # wins below); what is removed is the veto that discarded the evidence.
        try:
            faces = eng.detect(img)
        except Exception:
            continue
        if not faces:
            continue
        # One diff per frame, shared by every face in it.
        fg = foreground_mask(img)
        for f in faces:
            bbox = FaceEngine.bbox(f)
            if bbox is None or too_small_to_name(bbox, MIN_PX):
                continue
            w = float(bbox[2]) - float(bbox[0])
            h = float(bbox[3]) - float(bbox[1])
            if min(w, h) < FACEID_MIN_PX:
                continue
            # Subtraction, not coordinates (the User 2026-08-15: "just give it refs
            # dont block pixels"). The rectangular door_home mask rejected his
            # own 74x115 px entry because his face centre fell 0.004 of frame
            # width inside its edge, while the portrait it was drawn for is
            # 12x15 px and sits at 0.0% foreground against the empty room.
            # The mask is kept only as a fallback for when no background model
            # exists, so a missing ref set cannot let posters through.
            if is_furniture(img, bbox, mask=fg):
                continue
            if fg is None and in_masked_zone("door_home", bbox, img.shape):
                continue
            emb = FaceEngine.embed(f)
            if emb is None:
                continue
            # Reference frames blank the hallway portraits. Embedding alone
            # never could at 15-25px (0/3); with position it is 3/3.
            if blanked_by_reference(emb, bbox)[0]:
                continue  # portrait / poster
            det = FaceEngine.det_score(f)
            # Focus of THIS face, not the frame: a sharp doorway with a smeared
            # head in it is the exact case that scored 0.217.
            x0, y0 = max(0, int(bbox[0])), max(0, int(bbox[1]))
            roi = img[y0:int(bbox[3]), x0:int(bbox[2])]
            sharp = sharpness(roi)
            yaw = yaw_offset(f)
            eyes = eyes_visible(f, img.shape)
            s = _score(w * h, det, sharp, yaw, eyes)
            # ONE bucket. door_view is a bonus inside the score, not a tier
            # above it (the User 2026-08-16 — "all visual bugs fall to me").
            #
            # Two buckets returned `best_door or best_unknown`, so any door-view
            # frame beat every non-door-view frame no matter how much better the
            # latter scored. Measured on another person's 06:36 entry that inverted the
            # choice completely, because is_door_view only says True once the
            # subject is close enough to FILL the doorway — which is exactly when
            # she is moving fastest and the face is smeared:
            #
            #   faceid_009   74x100  sharp 103.0  door_view False  score 9991
            #   faceid_012  166x208  sharp  66.6  door_view True   score 2020  <- won
            #
            # sim 0.217 from the winner, 0.593 available two frames earlier. The
            # door-view preference was selecting for motion blur.
            s = s * DOOR_VIEW_BONUS if door_view is True else s
            bucket = best_door
            g, _gc = eng.gender(f)
            soft_g = g if g in ("M", "F") else None
            name, sim = match(
                emb, bank, threshold=MATCH_SIM, gender=soft_g, stamps=stamps
            )
            name_ng, sim_ng = match(emb, bank, threshold=MATCH_SIM, stamps=stamps)
            cand = {
                "frame": str(fp),
                "score": s,
                "door_view": door_view,
                "face_px": [int(w), int(h)],
                "bbox": [int(v) for v in bbox],
                "face_area": int(w * h),
                "det_score": round(det, 4),
                "sharpness": round(sharp, 1),
                "yaw": round(yaw, 2) if yaw is not None else None,
                "eyes_visible": eyes,
                "soft_gender": soft_g,
                "name": name,
                "sim": round(float(sim), 4),
                "name_nogender": name_ng,
                "sim_nogender": round(float(sim_ng), 4),
            }
            _keep_top2(bucket, cand)
    winner = best_door or best_unknown
    if winner:
        # Say so when the face came from a frame is_door_view doubted. A crop is
        # still a crop; the reviewer should know how it was selected.
        winner["from_door_view"] = winner.get("door_view") is True
    if winner.get("_second"):
        winner["runner_up"] = winner.pop("_second")
    elif "_second" in winner:
        winner.pop("_second", None)
    return winner


def best_face_recent(
    pack_dir: str | Path,
    *,
    window_sec: float = 90.0,
    max_packs: int = 4,
    **kw: Any,
) -> dict[str, Any]:
    """Best face across THIS pack and the packs just before it.

    Measured 2026-08-09 (the User's 04:25 entry): the `enter the user` pack held 8
    frames and no usable face, while the motion pack 18 s earlier held the User's
    face at 286x453 px matching him at 0.84. The classifier mints late, so the
    crossing itself often lives in the preceding kind=None pack. Evidence and
    identity must look at the window, not at one pack.
    """
    pack = Path(pack_dir)
    try:
        man = json.loads((pack / "manifest.json").read_text())
        t0 = float(man.get("trigger_ts") or 0)
    except Exception:
        t0 = 0.0
    candidates: list[Path] = [pack]
    try:
        for d in sorted(pack.parent.glob("ev_*")):
            if d == pack or not d.is_dir():
                continue
            try:
                m = json.loads((d / "manifest.json").read_text())
                ts = float(m.get("trigger_ts") or 0)
            except Exception:
                continue
            if t0 and 0 < (t0 - ts) <= window_sec:
                candidates.append(d)
    except Exception:
        pass
    # newest-first after the anchor pack, bounded
    candidates = candidates[:1] + sorted(
        candidates[1:], key=lambda d: d.stat().st_mtime, reverse=True
    )[: max_packs - 1]
    best: dict[str, Any] = {}
    for d in candidates:
        v = best_face_in_pack(d, **kw)
        if v and v.get("score", -1) > best.get("score", -1):
            v["from_pack"] = d.name
            best = v
    return best


def write_entry_best(pack_dir: str | Path, verdict: dict[str, Any]) -> str | None:
    """Copy the winning frame as entry_best.jpg and a FaceID-sized crop."""
    if not verdict or not verdict.get("frame"):
        return None
    pack = Path(pack_dir)
    try:
        raw = Path(verdict["frame"]).read_bytes()
        (pack / "entry_best.jpg").write_bytes(raw)
        crop = _crop_face(raw, verdict.get("bbox"))
        if crop:
            (pack / "entry_best_face.jpg").write_bytes(crop)
            verdict["face_file"] = "entry_best_face.jpg"
        ru = verdict.get("runner_up") if isinstance(verdict.get("runner_up"), dict) else None
        if ru and ru.get("frame"):
            try:
                (pack / "entry_best_2.jpg").write_bytes(Path(ru["frame"]).read_bytes())
                crop2 = _crop_face(Path(ru["frame"]).read_bytes(), ru.get("bbox"))
                if crop2:
                    (pack / "entry_best_2_face.jpg").write_bytes(crop2)
                    ru["face_file"] = "entry_best_2_face.jpg"
            except Exception:
                pass
        (pack / "entry_best.json").write_text(json.dumps(verdict, indent=1))
        try:
            from .oracle_event import (
                event_id_from_pack,
                get_event,
                open_event,
                stamp_faceid,
            )

            eid = event_id_from_pack(pack)
            if eid and get_event(eid) is None:
                ms = eid.replace("ev_", "")
                if ms.isdigit():
                    open_event(int(ms) / 1000.0, pack_id=eid)
            if eid:
                stamp_faceid(
                    eid,
                    name=verdict.get("name"),
                    sim=verdict.get("sim"),
                    face_px=verdict.get("face_px"),
                    door_view=verdict.get("door_view"),
                    gender=verdict.get("gender"),
                )
        except Exception:
            pass
        return str(pack / "entry_best.jpg")
    except Exception:
        return None


def _crop_face(jpeg: bytes, bbox) -> bytes | None:
    if not bbox or len(bbox) < 4:
        return None
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in bbox[:4]]
    pad = int(0.25 * max(1, x1 - x0, y1 - y0))
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    if x1 - x0 < FACEID_MIN_PX or y1 - y0 < FACEID_MIN_PX:
        return None
    crop = img[y0:y1, x0:x1]
    ok, buf = cv2.imencode(".jpg", crop)
    return buf.tobytes() if ok else None
