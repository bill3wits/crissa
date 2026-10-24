"""Face embeddings for presence matching — read-only, never imported from Crissa.

Two shapes, one loader:

    identity-store person tree   people/<id>/face/*.json          identity SoT
    legacy flat dir        data/enrollments/<name>_*.json   pre-cutover shim

Default load order is layered: the identity-store tree wins, and the legacy flat dir
fills in only ids the tree doesn't carry yet. Pass an explicit directory and you
get exactly that directory (shape detected, no shim) — tests and tools stay honest.
"""

from __future__ import annotations

from . import paths as _paths

import json
import os
from pathlib import Path

import numpy as np

_ROOT = _paths.ROOT
_FLAT_DEFAULT = _ROOT / "data" / "enrollments"


def house_id_people() -> Path:
    """people/ under the house identity root (face and voice live side by side)."""
    root = os.environ.get("HOUSE_ID_ROOT") or str(_paths.IDENTITY)
    return Path(root).expanduser() / "people"


def face_sources() -> tuple[Path, Path]:
    """(primary, legacy) a default load_enrolled() reads, primary wins.

    RAIL_PRESENCE_FACES may point at either shape; which one is detected, so the
    live unit keeps working whether it names the flat dir or the identity store's people/.
    """
    env = os.environ.get("RAIL_PRESENCE_FACES", "")
    named = Path(env).expanduser() if env else None
    if named is not None and is_person_tree(named):
        return named, _FLAT_DEFAULT
    return house_id_people(), (named or _FLAT_DEFAULT)


def is_scratch_slot(name: str) -> bool:
    """Occupancy S-slots are not identity. Face/voice banks must not load them."""
    n = (name or "").strip().lower()
    if n in ("s1", "s2"):
        return True
    return bool(n.startswith("s") and n[1:].isdigit())


def is_stranger_slot(name: str) -> bool:
    """Security X# folders are not household identity."""
    n = (name or "").strip().lower()
    return bool(n.startswith("x") and n[1:].isdigit())


def is_person_tree(d: Path) -> bool:
    """True for people/ (has <id>/face/), False for a flat dir of *.json."""
    try:
        return any((c / "face").is_dir() for c in d.iterdir() if c.is_dir())
    except OSError:
        return False


def load_enrolled(faces_dir: Path | None = None) -> dict[str, list[np.ndarray]]:
    """name -> list of L2-normalised embeddings."""
    if faces_dir is not None:
        d = Path(faces_dir)
        return load_person_tree(d) if is_person_tree(d) else load_flat(d)

    primary, legacy = face_sources()
    out = load_person_tree(primary) if is_person_tree(primary) else load_flat(primary)
    if legacy != primary:
        for name, embs in load_flat(legacy).items():
            out.setdefault(name, embs)  # shim: only ids identity-store is missing
    # The pack's own enrollment (2026-10-09): Init writes <state home>/enroll.json, numbers only, the owner's
    # person. It is folded in beside the store so one enrollment serves presence, the oracle and Init.
    for name, embs in load_pack_enroll().items():
        out.setdefault(name, []).extend(e for e in embs if not any(np.array_equal(e, k) for k in out[name]))
    return out


def load_pack_enroll(path: Path | None = None) -> dict[str, list[np.ndarray]]:
    """{owner: [vectors]} from <state home>/enroll.json (enroll_store), or {} when there is none."""
    from . import enroll_store
    doc = enroll_store.read_enroll(path)
    owner = str(doc.get("owner") or "").strip().lower()
    if not owner or enroll_store.bad_id(owner):
        return {}
    embs = []
    for row in doc.get("faces") or []:
        try:
            e = np.asarray(row, dtype=np.float32)
        except (TypeError, ValueError):
            continue
        n = float(np.linalg.norm(e)) if e.ndim == 1 and e.size else 0.0
        if n >= 1e-6 and _same_family(e, {}):
            embs.append(e / n)
    return {owner: embs} if embs else {}


def _same_family(e: np.ndarray, rec: dict) -> bool:
    """A record is this engine's unless it NAMES another engine family. The house's store was another engine's
    (same size, a different space) and lives at another path; a record written here carries this family."""
    from .face_engine import FAMILY
    fam = str(rec.get("engine") or rec.get("encoder") or rec.get("family") or "").strip().lower()
    return not fam or fam == FAMILY


def load_person_tree(people_dir: Path) -> dict[str, list[np.ndarray]]:
    """Walk people/<id>/face/*.json. Folder name is the id presence publishes."""
    out: dict[str, list[np.ndarray]] = {}
    if not people_dir.is_dir():
        return out
    for person in sorted(p for p in people_dir.iterdir() if p.is_dir()):
        name = person.name.lower().strip()
        if (
            name.startswith((".", "_"))
            or is_scratch_slot(name)
            or is_stranger_slot(name)
            or not _is_active(person)
        ):
            continue
        faces = sorted((person / "face").glob("*.json"))
        embs = [e for f in faces if (e := _encoding(f)) is not None]
        if embs:
            out[name] = embs
    return out


def load_flat(faces_dir: Path) -> dict[str, list[np.ndarray]]:
    """Legacy: one dir of <name>_<n>.json, name from the record or the stem."""
    out: dict[str, list[np.ndarray]] = {}
    if not faces_dir.is_dir():
        return out
    for p in sorted(faces_dir.glob("*.json")):
        e = _encoding(p)
        if e is None:
            continue
        rec = _read(p) or {}
        raw = rec.get("name") or rec.get("person_id") or p.stem.rsplit("_", 1)[0]
        name = str(raw).lower().strip()
        if (
            not name
            or name.startswith("stranger")
            or is_scratch_slot(name)
            or is_stranger_slot(name)
        ):
            continue
        out.setdefault(name, []).append(e)
    return out


def _is_active(person: Path) -> bool:
    """A person is active unless their person.json says otherwise."""
    rec = _read(person / "person.json")
    return bool(rec is None or rec.get("active", True))


def load_person_stamps(people_dir: Path | None = None) -> dict[str, dict]:
    """Identity stamps from people/<id>/person.json (SoT for gender, tier, display).

    Returns name -> {gender, tier, display, id}. Gender is operator-stamped M|F,
    not live face genderage. Presence publishes this when a name is on the roster.
    """
    d = Path(people_dir) if people_dir is not None else house_id_people()
    out: dict[str, dict] = {}
    if not d.is_dir():
        return out
    for person in sorted(p for p in d.iterdir() if p.is_dir()):
        name = person.name.lower().strip()
        if (
            name.startswith((".", "_"))
            or is_scratch_slot(name)
            or is_stranger_slot(name)
            or not _is_active(person)
        ):
            continue
        rec = _read(person / "person.json") or {}
        g = rec.get("gender") or rec.get("sex")
        if isinstance(g, str):
            g = g.strip().upper()[:1]
            if g not in ("M", "F"):
                g = None
        else:
            g = None
        out[name] = {
            "id": rec.get("id") or name,
            "display": rec.get("display") or name,
            "gender": g,
            "tier": rec.get("tier"),
            "active": rec.get("active", True),
        }
    return out


def _read(p: Path) -> dict | None:
    try:
        rec = json.loads(p.read_text())
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def _encoding(p: Path) -> np.ndarray | None:
    """L2-normalised embedding from a face record, or None if unusable."""
    rec = _read(p)
    if not rec:
        return None
    try:
        e = np.asarray(rec.get("encoding") or rec.get("embedding"), dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if e.ndim != 1 or e.size == 0:
        return None
    if not _same_family(e, rec):
        return None
    n = float(np.linalg.norm(e))
    return e / n if n >= 1e-6 else None


def match(
    emb: np.ndarray,
    enrolled: dict[str, list[np.ndarray]],
    threshold: float = 0.45,
    *,
    gender: str | None = None,
    stamps: dict[str, dict] | None = None,
    min_margin: float | None = None,
) -> tuple[str | None, float]:
    """Best enrolled cosine sim; name only if >= threshold.

    Gender split (the User): when gender is M or F, only compare against enrolled
    ids stamped that sex. the User=M is separated from F bank up front; two F then
    compete only inside the F set (see assign_faces_same_frame).

    Margin gate (Finding #3 / 2026-07-28): require best_person − second_person
    ≥ min_margin (default RAIL_FACE_MARGIN=0.04, 0 disables). Stops the user↔another person
    class mis-stamps when two enrolled sims are nearly tied. Margin is across
    *people*, not across embeddings of the same person.
    """
    e = np.asarray(emb, dtype=np.float32)
    n = float(np.linalg.norm(e))
    if n < 1e-6:
        return None, -1.0
    e = e / n
    # Static-face killbook (2026-08-07): a face that also lives in the
    # empty-room reference frames (hallway portraits, door posters) is an
    # OBJECT — it must never match an enrolled person, mint, re-add, or
    # refresh last_seen. Strict sim (default 0.80) so a real person is never
    # suppressed by a portrait OF them (~0.55-0.75 cross-photo).
    try:
        from .static_faces import killbook

        is_static, s_sim = killbook().is_static(e)
        if is_static:
            return None, -1.0
    except Exception:
        pass  # killbook trouble must never break identity
    g = (gender or "").strip().upper()[:1]
    pool = enrolled
    if g in ("M", "F") and stamps:
        pool = {
            name: embs
            for name, embs in enrolled.items()
            if (stamps.get(name) or {}).get("gender") == g
        }
        if not pool:
            pool = enrolled
    per_name: dict[str, float] = {}
    for name, embs in pool.items():
        for k in embs:
            s = float(np.dot(e, k))
            if s > per_name.get(name, -1.0):
                per_name[name] = s
    if not per_name:
        return None, -1.0
    ranked = sorted(per_name.items(), key=lambda kv: kv[1], reverse=True)
    best, best_sim = ranked[0]
    second_sim = ranked[1][1] if len(ranked) > 1 else -1.0
    if min_margin is None:
        try:
            min_margin = float(os.environ.get("RAIL_FACE_MARGIN", "0.04"))
        except (TypeError, ValueError):
            min_margin = 0.04
    if (
        min_margin > 0
        and second_sim >= 0
        and (best_sim - second_sim) < min_margin
    ):
        # Ambiguous between two people — no stamp (better unknown than wrong)
        return None, best_sim
    if best_sim >= threshold:
        return best, best_sim
    return None, best_sim


def assign_faces_same_frame(
    face_rows: list[dict],
    enrolled: dict[str, list[np.ndarray]],
    *,
    stamps: dict[str, dict] | None = None,
    threshold: float = 0.45,
) -> list[dict]:
    """Same-frame multi-face assignment (the User).

    face_rows: [{emb, soft_gender M|F|None, ...}]
    Returns one result per face: {name|None, sim, label, gender, provisional}

    Rules:
    1. Soft M/F splits the frame (the User vs F bank).
    2. Within a gender bucket, assign **unique** enrolled names by best sim
       (two F → best match *of the two*, no double-claim of another person).
    3. Unmatched stay provisional F1/F2/M1 — IDs may emerge later; labels stay in logs.
    4. **F3+ (the User):** three or more concurrent F → abandon IDs; all F become
       **crowd_noise** (no F1/F2/F3, no certain names, no per-person log IDs).
    """
    stamps = stamps or {}
    # score every face against every enrolled (with gender pool when soft_g known)
    scored: list[dict] = []
    for i, row in enumerate(face_rows):
        emb = row.get("emb")
        soft_g = row.get("soft_gender")
        if soft_g not in ("M", "F"):
            soft_g = None
        name, sim = match(
            emb, enrolled, threshold=threshold, gender=soft_g, stamps=stamps
        )
        # full ranking inside gender pool for assignment
        ranks: list[tuple[str, float]] = []
        e = np.asarray(emb, dtype=np.float32)
        nrm = float(np.linalg.norm(e))
        static = False
        if nrm >= 1e-6:
            e = e / nrm
            try:
                from .static_faces import killbook

                static, _ = killbook().is_static(e)
            except Exception:
                static = False
        if nrm >= 1e-6 and not static:
            pool = enrolled
            if soft_g in ("M", "F") and stamps:
                filt = {
                    nm: embs
                    for nm, embs in enrolled.items()
                    if (stamps.get(nm) or {}).get("gender") == soft_g
                }
                if filt:
                    pool = filt
            for nm, embs in pool.items():
                best = max(float(np.dot(e, k)) for k in embs)
                ranks.append((nm, best))
            ranks.sort(key=lambda x: -x[1])
        scored.append(
            {
                "i": i,
                "soft_gender": soft_g,
                "ranks": ranks,
                "top_name": name,
                "top_sim": sim,
            }
        )

    # How many F faces in this frame? (soft F, or would be F-assigned)
    f_face_idxs = [s["i"] for s in scored if s["soft_gender"] == "F"]
    # Also count unknown soft_g that only rank into F bank? stick to soft F for crowd trip
    crowd_f = len(f_face_idxs) >= 3

    # Greedy unique assignment within each gender: best (face, name) pairs first
    claimed_names: set[str] = set()
    assigned: dict[int, dict] = {}
    pairs: list[tuple[float, int, str]] = []
    for s in scored:
        # F3+: do not assign any F-bank names
        if crowd_f and s["soft_gender"] == "F":
            continue
        for nm, sim in s["ranks"]:
            if sim >= threshold:
                pairs.append((sim, s["i"], nm))
    pairs.sort(key=lambda x: -x[0])
    claimed_faces: set[int] = set()
    for sim, fi, nm in pairs:
        if fi in claimed_faces or nm in claimed_names:
            continue
        # never assign F-stamped names under crowd
        if crowd_f and (stamps.get(nm) or {}).get("gender") == "F":
            continue
        claimed_faces.add(fi)
        claimed_names.add(nm)
        soft_g = scored[fi]["soft_gender"]
        g = (stamps.get(nm) or {}).get("gender") or soft_g
        assigned[fi] = {
            "name": nm,
            "sim": sim,
            "gender": g if g in ("M", "F") else soft_g,
            "provisional": False,
            "label": nm,
            "crowd_noise": False,
        }

    # Provisional F1/F2 only (never F3+). At F3 → crowd_noise for all F.
    f_n = m_n = 0
    for s in scored:
        fi = s["i"]
        if fi in assigned:
            continue
        soft_g = s["soft_gender"]
        if soft_g == "F":
            if crowd_f:
                assigned[fi] = {
                    "name": None,
                    "sim": s["top_sim"],
                    "gender": "F",
                    "provisional": True,
                    "label": "crowd_noise",
                    "crowd_noise": True,
                }
            else:
                f_n += 1
                if f_n >= 3:
                    # safety: should have been crowd_f already
                    assigned[fi] = {
                        "name": None,
                        "sim": s["top_sim"],
                        "gender": "F",
                        "provisional": True,
                        "label": "crowd_noise",
                        "crowd_noise": True,
                    }
                else:
                    lab = f"F{f_n}"
                    assigned[fi] = {
                        "name": None,
                        "sim": s["top_sim"],
                        "gender": "F",
                        "provisional": True,
                        "label": lab,
                        "crowd_noise": False,
                    }
        elif soft_g == "M":
            m_n += 1
            lab = f"M{m_n}"
            assigned[fi] = {
                "name": None,
                "sim": s["top_sim"],
                "gender": "M",
                "provisional": True,
                "label": lab,
                "crowd_noise": False,
            }
        else:
            lab = f"U{fi+1}"
            assigned[fi] = {
                "name": None,
                "sim": s["top_sim"],
                "gender": soft_g,
                "provisional": True,
                "label": lab,
                "crowd_noise": False,
            }

    # If crowd: force every F face to crowd_noise (including any that slipped named)
    if crowd_f:
        for s in scored:
            if s["soft_gender"] != "F":
                continue
            fi = s["i"]
            assigned[fi] = {
                "name": None,
                "sim": s["top_sim"],
                "gender": "F",
                "provisional": True,
                "label": "crowd_noise",
                "crowd_noise": True,
            }

    return [assigned[i] for i in range(len(face_rows))]
