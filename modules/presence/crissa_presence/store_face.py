"""Store a face because an AUTH'd human said whose it is.

the User 2026-08-16:

    "I can literally say 'hey Argus, that is another person not a guest' right"
    "If I have Auth and I say Argus that is another person. Argus' job is to store
     her embeddings"
    "Do we even need an inspector with argus gateway open"

That last question is answered by the two before it. The Inspector's store gate
is a machine for deciding identity WITHOUT a human — margins, cross-person
floors, closed-population assignment, 3,146 lines of care. All of it exists
because nobody was there to say who it was. When the User is present and
authenticated and says the name out loud, the gate is answering a question that
has already been answered, and answering it less reliably: today FaceID scored
another person at 0.0931 and minted a stranger, while a labelled line-up named her
correctly and the man at the desk could have said so in three words.

So: an AUTH'd instruction outranks a similarity margin. This is the path that
takes it.

What still has to be true, and is enforced here rather than trusted:

  * AUTH is live. FaceID, and only FaceID, decides that the person giving the
    instruction is the User — an opinion cannot unlock the bank. Without it this
    is a stranger renaming the household.
  * The face is usable. The same rules as the ref guard: big enough, confident
    enough, exactly one, and not something a reference frame already contains.
    Storing a wall portrait as another person is how the bank got poisoned before.
  * Every write is auditable. Who authorised it, when, from which frame, and
    what the previous best match said — so a wrong call can be found and undone
    rather than silently becoming truth.

Nothing here overrides a refusal: a bad frame is refused even when the User
insists, because the failure would be permanent and invisible.
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
import pathlib
import time
from typing import Any

def _default_people() -> pathlib.Path:
    """Registration writes HOUSE_ID_ROOT/people; store must read the same tree.

    Default used to be ~/projects/<identity>/people (museum/backup). Voice-register
    2026-09-07 then failed `unknown_person` after ensure_person created the id
    under <records>/identity. RAIL_FACE_PEOPLE still overrides.
    """
    explicit = os.environ.get("RAIL_FACE_PEOPLE", "").strip()
    if explicit:
        return pathlib.Path(explicit)
    root = os.environ.get("HOUSE_ID_ROOT", "").strip() or str(_paths.IDENTITY)
    return pathlib.Path(root) / "people"


PEOPLE = _default_people()
AUDIT = pathlib.Path(
    os.environ.get("RAIL_STORE_FACE_AUDIT",
                   str(_paths.DATA / "store_face.jsonl"))
)
# Same bar as tools/ref_guard.py — a reference that cannot carry identity is
# worse than no reference, because it drags the centroid.
MIN_FACE_PX = int(os.environ.get("RAIL_STORE_FACE_MIN_PX", "80"))
MIN_DET = float(os.environ.get("RAIL_STORE_FACE_MIN_DET", "0.55"))
# Sealed bank (the User 2026-08-24): "cap security file to never be added to for
# now. Only the references collected at registration." A bank that grows during
# operation is how a person's refs drift — and a silent ref nobody chose is
# exactly what put room-tone into the User's voice bank. Phase 2 adds a deliberate
# Inspector collection routine; until then registration is the only writer.
#
# Scoped to non-USER first, per "at least for non USER to start" — the User's
# own bank stays open so the house can still learn its operator.
SEAL = os.environ.get("RAIL_STORE_FACE_SEAL", "1") not in ("0", "false", "no", "")

# A TURNED STILL MUST BE HIS FACE (round 35, the User via .101 round 33: "check pics, they are all back of head
# practically"; the engine scored the back of his head det 1.0, yaw -0.30, banked as three_quarter_L). A caller that asks
# (`match_front`, Voice ID's turned stills) gets the still compared with the person's own FRONT prints: below the bar it is
# refused ("not_his_face") and the count's check asks again. Measured 2026-10-19 on the kit's own engine (AuraFace on
# MediaPipe's points) over Lila's and Crissa's turn films: the same face against its front reads 0.35-0.65 at yaw 0.3-0.5
# and 0.18-0.32 in profile (|yaw| >= 0.8); another face reads up to 0.26 on a partial turn and up to 0.16 in profile.
# So the bar follows the turn: FRONT_MATCH_TURN under PROFILE_YAW, FRONT_MATCH_PROFILE past it. Every similarity is in the
# answer (`front_sim`) and the audit, so .101's real face tunes the two numbers. No front print yet: no check.
# Kill CRISSA_FACE_FRONT_MATCH=0.
FRONT_MATCH_TURN = float(os.environ.get("CRISSA_FACE_FRONT_MATCH_TURN", "0.30") or 0)
FRONT_MATCH_PROFILE = float(os.environ.get("CRISSA_FACE_FRONT_MATCH_PROFILE", "0.17") or 0)
PROFILE_YAW = 0.8


def front_match_on() -> bool:
    return os.environ.get("CRISSA_FACE_FRONT_MATCH", "1").strip().lower() not in ("0", "off", "false", "no")


def front_bar(yaw: float | None) -> float:
    return FRONT_MATCH_PROFILE if (yaw is not None and abs(float(yaw)) >= PROFILE_YAW) else FRONT_MATCH_TURN


def front_sim(emb, face_dir: pathlib.Path) -> float | None:
    """The best similarity of this embedding to the person's own front prints (None: no front print yet)."""
    import numpy as np

    best = None
    for f in sorted(face_dir.glob("*.json")) if face_dir.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not str(d.get("bin") or d.get("pose") or "").startswith("front"):
            continue
        e = d.get("embedding") or d.get("encoding")
        if not isinstance(e, list) or len(e) < 8:
            continue
        v = np.asarray(e, dtype=np.float32)
        n = float(np.linalg.norm(v)) or 1.0
        sim = float(np.dot(np.asarray(emb, dtype=np.float32), v / n))
        best = sim if best is None else max(best, sim)
    return best


def _face_family() -> str:
    """The family this package's face engine stamps (face_engine.FAMILY, "auraface-v1"); the house wrote "faceid"."""
    try:
        from .face_engine import FAMILY
        return str(FAMILY)
    except Exception:  # noqa: BLE001  numpy missing: the record still says what made it
        return "auraface-v1"


def _user_id() -> str:
    env = (
        os.environ.get("HOUSE_USER_ID")
        or os.environ.get("RAIL_PRIMARY_USER")
        or ""
    ).strip().lower()
    if env:
        return env
    try:
        site = json.loads(
            (PEOPLE.parent / "site.json").read_text(encoding="utf-8")
        )
        uid = str((site.get("enroll") or {}).get("user_id") or "").strip().lower()
        if uid:
            return uid
    except Exception:
        pass
    from .house_user import user_id as _record_user

    return _record_user()        # the identity record's User, never a name in code (Init step 5)


_REGISTER_SOURCES = frozenset(
    {"register", "enroll", "voice-register", "registration", "desk", "manual"}
)


def bank_write_allowed(person_id: str, source: str = "") -> bool:
    """Live writers: User only. Everyone else: registration. Rails copy (no shared import)."""
    pid = (person_id or "").strip().lower()
    src = (source or "").strip().lower()
    if not pid:
        return False
    if pid in ("s1", "s2") or (pid.startswith("s") and pid[1:].isdigit()):
        return False
    if src in _REGISTER_SOURCES:
        return True
    return pid == _user_id()


REQUIRE_AUTH = os.environ.get("RAIL_STORE_FACE_REQUIRE_AUTH", "1") not in (
    "0", "false", "no",
)


def _audit(row: dict[str, Any]) -> None:
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass


def _auth_ok(presence: dict | None) -> tuple[bool, str]:
    """Live bank write: identity tier only (fresh face + this-turn User voice)."""
    if not REQUIRE_AUTH:
        return True, "auth_not_required"
    from .auth_first import identity_ok_from_presence

    return identity_ok_from_presence(presence)


def _detect_enrol(engine: Any, img: Any) -> list:
    """Enrolment's detect: a still with no face is searched again on crops (face_engine A SMALL FACE IS LOOKED FOR ON
    CROPS, round 21). An engine without the rescue (a stand-in, an older engine) is asked plainly."""
    try:
        return list(engine.detect(img, rescue=True) or [])
    except TypeError:
        return list(engine.detect(img) or [])


def probe(jpeg: bytes) -> dict[str, Any]:
    """One still's largest face and its yaw, NOTHING stored and no bank touched (round 17, .101 2026-10-13: Voice ID's
    turned still was one grab the moment the cue ended, `no_face` before the head had moved; the coach now looks
    again and again through this until the head has turned, then stores the still with store()). The same engine and
    yaw rule as store(); no person, no audit, no embedding. {ok, n_faces, yaw, bin, det, px} or {ok: False, error}."""
    out: dict[str, Any] = {"ok": False}
    try:
        import cv2
        import numpy as np

        from .face_engine import get_face_engine

        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            out["error"] = "unreadable_frame"
            return out
        faces = _detect_enrol(get_face_engine(), img)
        out["n_faces"] = len(faces)
        if not faces:
            out["error"] = "no_face"
            return out

        def _area(face: Any) -> float:
            b = getattr(face, "bbox", None)
            return 0.0 if b is None else abs(float(b[2] - b[0]) * float(b[3] - b[1]))

        f = max(faces, key=_area)
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        out.update(px=min(x2 - x1, y2 - y1), det=round(float(getattr(f, "det_score", 0.0) or 0.0), 3))
        from .enroll_panel.face_yaw import yaw_from_face  # type: ignore

        yaw, bin_, _kps = yaw_from_face(f)
        if yaw is None:
            out["error"] = "no_yaw"
            return out
        out.update(ok=True, yaw=round(float(yaw), 4), bin=bin_)
        return out
    except Exception as e:  # noqa: BLE001 - a look that cannot be read is no answer, never a crash
        out["error"] = "probe: %s" % str(e)[:160]
        return out


def store(
    person_id: str,
    jpeg: bytes,
    *,
    presence: dict | None = None,
    said_by: str = "voice",
    note: str = "",
    enroll: bool = False,
    pose: str = "",
    station: str = "",
    prefix: str = "",
    match_front: bool = False,
) -> dict[str, Any]:
    """Write this face into person_id's bank, on an AUTH'd human's say-so.

    FaceID: Inspector `get_face_engine` (same as Re-scan `_faceidargest_from_jpeg`).
    Files: design-inspector-emb-safety.md §5 — validate → stage → promote to
    HOUSE-ID-LAYOUT `people/<id>/face/NNN.json`. JPEG also in `face/refs/`
    (ORACLE-IDENTITY-SETUP).
    """
    pid = (person_id or "").strip().lower()
    out: dict[str, Any] = {"ok": False, "person_id": pid, "said_by": said_by}
    if not pid:
        out["error"] = "need person_id"
        return out

    # Control Panel Register is the User naming the face (typed id). Same
    # say-so as "Argus, that is another person" — do not require a live door AUTH.
    if enroll or said_by in ("voice-register", "enroll"):
        out["auth"] = "enroll"
    elif SEAL and not bank_write_allowed(pid, said_by or "live"):
        # Not a registration, and not the User's own bank: refused.
        out["auth"] = "sealed"
        out["error"] = "sealed"
        out["detail"] = (
            f"{pid}'s bank is sealed — only registration adds references. "
            "Re-register to replace them."
        )
        _audit({
            "ts": time.time(), "person_id": pid, "said_by": said_by,
            "ok": False, "reason": "sealed", "note": note,
        })
        return out
    else:
        ok, why = _auth_ok(presence)
        out["auth"] = why
        if not ok:
            out["error"] = "no_auth"
            out["detail"] = "only an authenticated User may name a face"
            _audit({**out, "ts": time.time()})
            return out

    person_dir = PEOPLE / pid
    if not person_dir.is_dir():
        out["error"] = "unknown_person"
        out["detail"] = f"people/{pid}/ does not exist — enrol first"
        _audit({**out, "ts": time.time()})
        return out

    # frozen identities are frozen even for the User
    try:
        import sys

        from .enroll_panel.person import is_id_frozen  # type: ignore

        if is_id_frozen(pid):
            out["error"] = "frozen"
            _audit({**out, "ts": time.time()})
            return out
    except Exception:
        pass

    # --- the frame has to be worth keeping -------------------------------
    try:
        import sys

        import cv2
        import numpy as np

        from .face_engine import FaceEngine, get_face_engine
        from .static_faces import blanked_by_reference

        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            out["error"] = "unreadable_frame"
            _audit({**out, "ts": time.time()})
            return out
        # detect() is an instance method (the engine owns the session); the
        # bbox/embed/det_score helpers are static.
        faces = _detect_enrol(get_face_engine(), img)
        out["n_faces"] = len(faces)
        if not faces:
            out["error"] = "no_face"
            _audit({**out, "ts": time.time()})
            return out
        if len(faces) > 1 and not enroll:
            # Which one did they mean? Refuse rather than guess — an ambiguous
            # reference is how the wrong face gets bound to a name.
            out["error"] = "many_faces"
            _audit({**out, "ts": time.time()})
            return out
        # Enroll / Re-scan: largest face in the still (same as live_snap).
        def _area(face: Any) -> float:
            b = getattr(face, "bbox", None)
            if b is None:
                return 0.0
            return abs(float(b[2] - b[0]) * float(b[3] - b[1]))

        f = max(faces, key=_area) if enroll and faces else faces[0]
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        w, h = x2 - x1, y2 - y1
        det = float(getattr(f, "det_score", 0.0) or 0.0)
        bbox = [x1, y1, x2, y2]
        out.update(px=min(w, h), det=round(det, 3), bbox=bbox)
        if min(w, h) < MIN_FACE_PX:
            out["error"] = "too_small"
            _audit({**out, "ts": time.time()})
            return out
        if det < MIN_DET:
            out["error"] = "low_det"
            _audit({**out, "ts": time.time()})
            return out
        try:
            hit, _why = blanked_by_reference(None, [x1, y1, x2, y2], img.shape)
        except Exception:
            hit = False
        if hit is True:
            out["error"] = "reference_furniture"
            out["detail"] = "a reference frame already has a face here"
            _audit({**out, "ts": time.time()})
            return out

        yaw = bin_ = kps_list = None
        try:
            from .enroll_panel.face_yaw import yaw_from_face  # type: ignore

            yaw, bin_, kps_list = yaw_from_face(f)
        except Exception:
            yaw = bin_ = kps_list = None
        if yaw is not None:
            out["yaw"] = round(float(yaw), 4)
            out["bin"] = bin_
        emb = FaceEngine.embed(f)
        if emb is None:
            out["error"] = "embed_failed"
            _audit({**out, "ts": time.time()})
            return out
        if match_front and front_match_on():
            fs = front_sim(emb, person_dir / "face")
            out["front_sim"] = None if fs is None else round(fs, 4)
            if fs is not None and fs < front_bar(yaw):
                out["error"] = "not_his_face"
                out["detail"] = "%.3f against his own front prints, under the bar %.2f for yaw %s" % (
                    fs, front_bar(yaw), out.get("yaw"))
                _audit({**out, "ts": time.time()})
                return out
        # what did the bank think before we were told? audit value only.
        try:
            from .enrollments import load_enrolled, match

            was, sim = match(emb, load_enrolled(), threshold=0.0)
            out["was_named"] = was
            out["was_sim"] = round(float(sim), 4) if sim is not None else None
        except Exception:
            pass
    except Exception as e:
        out["error"] = f"{type(e).__name__}"
        out["detail"] = str(e)[:160]
        _audit({**out, "ts": time.time()})
        return out

    # Inspector store pipeline (docs/designs/design-inspector-emb-safety.md §5):
    # validate → stage → promote. Active files are people/<id>/face/NNN.json
    # (HOUSE-ID-LAYOUT). Enroll JPEGs also live in face/refs/ (ORACLE-IDENTITY-SETUP).
    import sys

    _gate = _paths.PACK / "lib"
    if _gate.is_dir() and str(_gate) not in sys.path:
        sys.path.insert(0, str(_gate))
    from .inspector_gate import validate_emb_record   # this package's copy: a pack has no lib/inspector_gate (.101, 2026-10-10)

    face_dir = person_dir / "face"
    refs = face_dir / "refs"
    staged_dir = face_dir / "staged"
    refs.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "person_id": pid,
        "modality": "face",
        "model": _face_family(),          # the engine that made the numbers (sync_from_store keeps only auraface prints)
        "dim": int(getattr(emb, "size", 512) or 512),
        "encoding": [float(x) for x in emb.tolist()],
        "source": said_by or "store_face",
        "det_score": out.get("det"),
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "station": station or None,
        "pose_asked": pose or None,
        "pose": bin_ or pose or None,
        "yaw": out.get("yaw"),
        "bin": bin_,
        "kps": kps_list,
        "bbox": out.get("bbox"),
        "ok": True,
        "note": note or None,
    }
    vr = validate_emb_record(rec, expect_person_id=pid, accept_legacy=False)
    if not vr.ok:
        out["error"] = "validation_fail"
        out["detail"] = vr.errors
        _audit({**out, "ts": time.time()})
        return out
    if vr.normalized:
        rec["encoding"] = vr.normalized
        rec["dim"] = vr.dim
    src = (said_by or "store_face").replace("/", "-")[:40]
    ts_tag = time.strftime("%Y%m%d-%H%M%S")
    staged_path = staged_dir / f"{ts_tag}_{src}.json"
    n = 1
    while (face_dir / f"{n:03d}.json").is_file() or (face_dir / f"{n:03d}.jpg").is_file():
        n += 1
    stem = f"{n:03d}"
    jpg_path = refs / f"{stem}.jpg"
    json_path = face_dir / f"{stem}.json"
    face_jpg = face_dir / f"{stem}.jpg"
    rec["ref"] = f"{stem}.jpg"
    rec["ref_path"] = str(jpg_path)
    rec["promoted_from"] = str(staged_path)
    try:
        staged_path.write_text(json.dumps(rec, indent=2) + "\n")
        json_path.write_text(json.dumps(rec, indent=2) + "\n")
        jpg_path.write_bytes(jpeg)
        try:
            face_jpg.write_bytes(jpeg)
        except OSError:
            pass
        ledger = face_dir / "ledger.jsonl"
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "op": "promote",
                        "person_id": pid,
                        "staged": str(staged_path),
                        "active": str(json_path),
                        "said_by": said_by,
                        "enroll": enroll,
                        "ts": rec["ts"],
                    }
                )
                + "\n"
            )
    except OSError as e:
        out["error"] = "write_failed"
        out["detail"] = str(e)[:160]
        _audit({**out, "ts": time.time()})
        return out

    try:
        from .server import get_svc

        eng = getattr(get_svc(), "engine", None)
        if eng is not None:
            eng._enrolled_ts = 0.0
            eng.enrolled = {}
    except Exception:
        pass

    out.update(
        ok=True,
        stored=str(jpg_path),
        json=str(json_path),
        staged=str(staged_path),
        promoted=True,
        dim=int(rec.get("dim") or 512),
        note=note,
        yaw=rec.get("yaw"),
        bin=rec.get("bin"),
        pose=rec.get("pose"),
    )
    _audit({**out, "ts": time.time()})
    return out
