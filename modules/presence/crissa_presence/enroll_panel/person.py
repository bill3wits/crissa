"""Scaffold people/<id>/ with face + voice siblings."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .paths import index_path, people_dir, person_dir


def private_on() -> bool:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): face refs, enroll wavs and voice / face prints were
    written with the default umask (0644 files, 0755 folders), readable by any account on the machine. pack_store writes
    the owner's enroll.json 0600; the identity banks now match it: the enroll processes (the coach and every session it
    spawns, __main__) run under umask 077, so every file they write is 0600 and every folder 0700, and the people tree's
    folders are taken to owner-only as a person is made or cleared (never adding a bit: a frozen bank stays read-only).
    Kill: CRISSA_ID_PRIVATE=0."""
    return os.environ.get("CRISSA_ID_PRIVATE", "1").strip().lower() not in ("0", "false", "no", "off")


PRIVATE_UMASK = 0o077     # owner only: files 0600, folders 0700 (pack_store's enroll.json mode)


def private_umask() -> int | None:
    """Set this process's umask to owner-only; returns the old one (None when off)."""
    if not private_on():
        return None
    return os.umask(PRIVATE_UMASK)


def make_private(path: Path, *, walk: bool = False) -> int:
    """Strip group and other bits from `path` (and, walk=True, everything under it). Owner bits are kept as they are, so
    a frozen (read-only) bank stays read-only. Returns how many modes changed."""
    if not private_on():
        return 0
    n = 0
    paths = [Path(path)]
    if walk and Path(path).is_dir():
        paths += list(Path(path).rglob("*"))
    for p in paths:
        try:
            st = p.lstat()
        except OSError:
            continue
        if p.is_symlink() or not (p.is_dir() or p.is_file()):
            continue
        mode = st.st_mode & 0o7777
        want = mode & ~PRIVATE_UMASK
        if want != mode:
            try:
                os.chmod(p, want)
                n += 1
            except OSError:
                pass
    return n


def load_index(root: Path | None = None) -> dict[str, Any]:
    p = index_path(root)
    if not p.is_file():
        return {"schema": "house_people/v1", "people": []}
    return json.loads(p.read_text())


def save_index(data: dict[str, Any], root: Path | None = None) -> None:
    p = index_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    make_private(p.parent)       # round 46: the people folder (every person's bank) is the owner's
    make_private(p)


def format_person_id(raw: str) -> str:
    """Normalize free-form name → person id (filesystem-safe).

    Allowed free form (examples): Jonathon, First_S, mary-jane, "First S".
    Rules:
      - trim; collapse whitespace
      - spaces → underscore (First S → first_s)
      - keep letters, digits, underscore, hyphen
      - lowercase
      - no leading/trailing _ or -
      - max 48 chars
    Display name is stored separately (original casing).
    """
    import re

    s = (raw or "").strip()
    if not s:
        return ""
    # spaces → underscore (prefer user_S style over hyphens for multi-word)
    s = re.sub(r"\s+", "_", s)
    s = s.lower()
    # keep alnum, underscore, hyphen only
    s = re.sub(r"[^a-z0-9_\-]", "", s)
    # collapse runs of separators
    s = re.sub(r"[_\-]{2,}", lambda m: m.group(0)[0], s)
    s = s.strip("_-")
    return s[:48]


def _glass_initials(display: str, *, kind: str) -> str:
    """Oracle/wall mark — `lib/vo_sound.initials_for` (LAST-HEARD-GHOST, ARGUS-ORACLE-LOOP)."""
    import sys

    from .paths import _on, rails_path

    lib = rails_path("lib")
    if lib.is_dir() and str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    if not _on("HOUSE_ID_RAILS_CONFIG") and (lib / "vo_sound.py").is_file():
        # vo_sound ships with the feed module; an install without it takes the fallback below (2026-10-10: Register
        # closed the connection on Voice ID's /start - "No module named 'vo_sound'" - on an install without the feed)
        from vo_sound import initials_for

        return initials_for(display, kind=kind) or ""
    # Register never 500s for want of the Wall repo (Init step 5): its own initials when vo_sound is not there
    try:
        from vo_sound import initials_for

        return initials_for(display, kind=kind) or ""
    except ImportError:
        words = [w for w in str(display or "").split() if w]
        return "".join(w[0].upper() for w in words[:2])


def register_id_honour_on() -> bool:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): the guest branch (a second person sharing a roster
    first name) minted first_l / first_la ... and ignored requested_id, so a guest's own Retry under the id it already
    has was minted a NEW id - two ids for one person. A requested id that differs from the typed name's own is now
    honoured: that guest again when the id is theirs, a new guest under it when it is free, and refused when it names
    somebody else (the roster's first-name person, or another display). Kill: CRISSA_REGISTER_ID_HONOUR=0."""
    return os.environ.get("CRISSA_REGISTER_ID_HONOUR", "1").strip().lower() not in ("0", "false", "no", "off")


def _requested_guest(rid: str, disp: str, first_id: str, have: set, root: Path | None) -> dict[str, Any]:
    if rid in have and rid != first_id:
        rec = next((p for p in list_people(root) if p.get("id") == rid), {}) or {}
        if " ".join(str(rec.get("display") or "").split()).casefold() == " ".join(disp.split()).casefold():
            return {
                "person_id": rid,
                "display": rec.get("display") or disp,
                "role": rec.get("role") or "guest",
                "initials": _glass_initials(disp, kind="visitor"),
                "kind": "reenroll",
            }
    if rid in have:
        return {
            "person_id": "",
            "display": disp,
            "role": None,
            "initials": "",
            "kind": "collision",
            "error": f"the id {rid} is already somebody else's; type the name alone to be given your own",
        }
    return {
        "person_id": rid,
        "display": disp,
        "role": "guest",
        "initials": _glass_initials(disp, kind="visitor"),
        "kind": "visitor",
    }


def allocate_register_identity(
    display: str,
    *,
    root: Path | None = None,
    requested_id: str | None = None,
) -> dict[str, Any]:
    """Person id + glass initials for Register an ID.

    Id form is `format_person_id` (``First S`` → ``first_s``). Glass marks are
    `vo_sound.initials_for`: regular/User ``Bi`` (second letter of the first
    name lowercase); visitor/guest ``BT`` (F/L, both caps). Docs:
    HOUSE-ID-LAYOUT, LAST-HEARD-GHOST-20260814, ARGUS-ORACLE-LOOP-20260815.

    A second person with the same first name as a roster id is a guest — grab
    the last initial from the typed surname. Typing just ``First`` when ``first``
    exists is a re-enroll.
    """
    import re

    disp = format_display_name(display) or format_display_name(requested_id or "")
    parts = [p for p in re.split(r"[\s_\-]+", disp) if p]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    first_id = format_person_id(first)
    have = {str(p.get("id") or "") for p in list_people(root) if p.get("id")}

    rid = format_person_id(requested_id or "") if register_id_honour_on() else ""
    if first_id and first_id in have and last and rid and rid != format_person_id(disp):
        # round 46: an id the caller named (not one made from the typed name) is honoured, never re-minted
        return _requested_guest(rid, disp, first_id, have, root)
    if first_id and first_id in have and last:
        # Documented user_S form: first name + last initial as a token.
        pid = format_person_id(f"{first} {last[0]}")
        n = 1
        while pid in have:
            n += 1
            chunk = last[:n] if n <= len(last) else f"{last}{n}"
            pid = format_person_id(f"{first} {chunk}")
        return {
            "person_id": pid,
            "display": disp,
            "role": "guest",
            "initials": _glass_initials(disp, kind="visitor"),
            "kind": "visitor",
        }

    pid = format_person_id(requested_id or disp or first)
    if first_id and first_id in have and not last:
        rec = next((p for p in list_people(root) if p.get("id") == first_id), {}) or {}
        return {
            "person_id": first_id,
            "display": rec.get("display") or disp,
            "role": rec.get("role"),
            "initials": _glass_initials(rec.get("display") or first, kind="regular"),
            "kind": "reenroll",
        }
    return {
        "person_id": pid,
        "display": disp,
        "role": None,
        "initials": _glass_initials(disp or first, kind="regular") if first else "",
        "kind": "new",
    }


def format_display_name(raw: str) -> str:
    """Human display from free form — preserve intentional spelling.

    - Collapse runs of spaces only (keep underscores: user_S stays user_S).
    - Mixed/upper case kept as typed (Jonathon, user_S).
    - All-lowercase plain words → Title Case (jonathon → Jonathon).
    """
    s = " ".join((raw or "").split())
    if not s:
        return ""
    # Keep user_S / mary-jane / Jonathon as typed
    if s != s.lower() or "_" in s or "-" in s:
        return s
    return s.title()


def ensure_person(
    person_id: str,
    *,
    display: str | None = None,
    root: Path | None = None,
    active: bool = True,
    role: str | None = None,
    initials: str | None = None,
) -> Path:
    """Create people/<id>/{person.json,face/,voice/} and index row if missing."""
    pid = format_person_id(person_id)
    if not pid or pid.startswith((".", "_")):
        raise ValueError(f"bad person id: {person_id!r}")
    d = person_dir(pid, root)
    (d / "face" / "refs").mkdir(parents=True, exist_ok=True)
    (d / "voice").mkdir(parents=True, exist_ok=True)
    pj = d / "person.json"
    if display:
        disp = format_display_name(display) or pid.replace("_", " ").replace("-", " ").title()
    else:
        disp = pid.replace("_", " ").replace("-", " ").title()
    if not pj.is_file():
        rec: dict[str, Any] = {
            "id": pid,
            "display": disp,
            "active": active,
            "schema": "house_person/v1",
            "created_ts": time.time(),
            "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if role:
            rec["role"] = role
        if initials:
            rec["initials"] = initials
        pj.write_text(json.dumps(rec, indent=2) + "\n")
    else:
        rec = json.loads(pj.read_text())
        changed = False
        if display and rec.get("display") != display:
            rec["display"] = display
            changed = True
        if role and not rec.get("role"):
            rec["role"] = role
            changed = True
        if initials and not rec.get("initials"):
            rec["initials"] = initials
            changed = True
        if changed:
            pj.write_text(json.dumps(rec, indent=2) + "\n")

    idx = load_index(root)
    people = idx.setdefault("people", [])
    ids = {p.get("id") for p in people}
    if pid not in ids:
        row: dict[str, Any] = {"id": pid, "display": disp, "active": active}
        if role:
            row["role"] = role
        people.append(row)
        idx["schema"] = "house_people/v1"
        save_index(idx, root)
    # round 46: owner-only folders (a folder written before keeps its files, now out of anyone else's reach)
    for sub in (d.parent, d, d / "face", d / "face" / "refs", d / "voice", pj):
        make_private(sub)
    return d


def list_people(root: Path | None = None) -> list[dict[str, Any]]:
    """Roster from index + disk folders."""
    idx = load_index(root)
    by_id = {p["id"]: dict(p) for p in idx.get("people") or [] if p.get("id")}
    base = people_dir(root)
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            by_id.setdefault(
                child.name,
                {"id": child.name, "display": child.name.title(), "active": True},
            )
    return list(by_id.values())


def is_id_frozen(person_id: str, root: Path | None = None) -> bool:
    """True if people/<id> is frozen (the User lock — no face/voice enroll writes)."""
    pid = format_person_id(person_id) or (person_id or "").strip().lower()
    if not pid:
        return False
    pj = person_dir(pid, root) / "person.json"
    if not pj.is_file():
        return False
    try:
        o = json.loads(pj.read_text())
    except Exception:
        return False
    fr = o.get("freeze") or {}
    if fr.get("frozen") is True:
        return True
    if o.get("id_frozen") or o.get("face_frozen") or o.get("voice_frozen"):
        return True
    # filesystem freeze marker
    if (person_dir(pid, root) / "FREEZE.md").is_file():
        return True
    return False


def assert_not_frozen(person_id: str, root: Path | None = None) -> None:
    """Raise if enroll tries to write a frozen identity bank."""
    pid = format_person_id(person_id) or (person_id or "").strip().lower()
    if is_id_frozen(pid, root):
        raise RuntimeError(
            f"identity FROZEN: people/{pid}/ — no face/voice enroll writes. "
            f"See people/{pid}/FREEZE.md. Unfreeze only with the User (chmod u+w + "
            f"person.json freeze.frozen=false)."
        )


def voice_bank_files(person_id: str, root: Path | None = None) -> list[Path]:
    """Live-encoder matching prints (what the ear loads).

    Numbered ``00*.json`` ECAPA files are museum when WeSpeaker is live.
    """
    return live_voice_files(person_id, root)


def live_voice_files(person_id: str, root: Path | None = None) -> list[Path]:
    pid = format_person_id(person_id) or (person_id or "").strip().lower()
    d = person_dir(pid, root) / "voice"
    if not d.is_dir():
        return []
    want = _live_encoder_family()
    out: list[Path] = []
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("."):
            continue
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if rec.get("role") == "tts_filter":
            continue
        if _rec_encoder_family(rec) != want:
            continue
        enc = rec.get("encoding") or rec.get("embedding_ecapa") or rec.get("embedding")
        if enc:
            out.append(f)
    return out


def face_bank_files(person_id: str, root: Path | None = None) -> list[Path]:
    d = person_dir(person_id, root) / "face"
    if not d.is_dir():
        return []
    # numbered 00N.json or ref_*.json (legacy house layout)
    nums = sorted(d.glob("00*.json"))
    if nums:
        return nums
    return sorted(p for p in d.glob("ref_*.json") if "_flip" not in p.name)


def _live_encoder_family() -> str:
    raw = (
        os.environ.get("HOUSE_VOICE_ENCODER")
        or os.environ.get("RAIL_VOICE_ENCODER")
        or "wespeaker"
    ).strip().lower()
    if raw in ("ecapa", "speechbrain", "sb", "spkrec"):
        return "ecapa"
    return "wespeaker"


def _rec_encoder_family(rec: dict) -> str:
    model = str(rec.get("model") or rec.get("encoder") or rec.get("family") or "").lower()
    if "wespeaker" in model or model.startswith("ws/") or "campplus" in model:
        return "wespeaker"
    if "ecapa" in model or "speechbrain" in model or "spkrec" in model:
        return "ecapa"
    dim = rec.get("dim")
    if dim == 256:
        return "wespeaker"
    if dim == 192:
        return "ecapa"
    return "unknown"


def live_voice_count(person_id: str, root: Path | None = None) -> int:
    """How many prints the ear will actually match (live encoder only)."""
    return len(live_voice_files(person_id, root))


def bank_counts(person_id: str, root: Path | None = None) -> dict[str, int]:
    """How many face/voice enroll records exist (for replace confirm).

    ``voice`` is live-encoder matching prints, not wavs + museum ECAPA JSON.
    """
    pid = format_person_id(person_id) or (person_id or "").strip().lower()
    d = person_dir(pid, root)
    face_n = 0
    face = d / "face"
    if face.is_dir():
        face_n = len(
            [
                p
                for p in face.iterdir()
                if p.is_file()
                and p.suffix.lower() in (".json", ".jpg", ".jpeg", ".png")
                and not p.name.startswith(".")
            ]
        )
        refs = face / "refs"
        if refs.is_dir():
            face_n += len(
                [
                    p
                    for p in refs.iterdir()
                    if p.is_file() and not p.name.startswith(".")
                ]
            )
    voice_n = live_voice_count(pid, root)
    return {"face": face_n, "voice": voice_n, "total": face_n + voice_n}


def clear_enroll_records(
    person_id: str,
    *,
    root: Path | None = None,
    face: bool = True,
    voice: bool = True,
) -> dict[str, Any]:
    """Wipe face/voice bank files so User can re-enroll from scratch.

    Keeps person.json + role metadata. Removes embeddings, snaps, wavs under
    people/<id>/{face,voice}/ (including face/refs/). Used when confirm-replace
    on an existing roster pick (corrupted bank → Inspector retry).
    """
    pid = format_person_id(person_id) or (person_id or "").strip().lower()
    if not pid or pid.startswith((".", "_")):
        raise ValueError(f"bad person id: {person_id!r}")
    d = person_dir(pid, root)
    removed: list[str] = []
    errors: list[str] = []

    def _rm_tree_files(base: Path) -> None:
        if not base.is_dir():
            return
        for p in sorted(base.rglob("*"), reverse=True):
            if p.is_file() and not p.name.startswith("."):
                try:
                    p.unlink()
                    removed.append(str(p.relative_to(d)))
                except OSError as e:
                    errors.append(f"{p.name}: {e}")
            elif p.is_dir() and p != base and p.name != "refs":
                # leave empty dirs; re-create structure below
                try:
                    if not any(p.iterdir()):
                        p.rmdir()
                except OSError:
                    pass

    before = bank_counts(pid, root)
    if face:
        _rm_tree_files(d / "face")
        (d / "face" / "refs").mkdir(parents=True, exist_ok=True)
    if voice:
        _rm_tree_files(d / "voice")
        (d / "voice").mkdir(parents=True, exist_ok=True)
    after = bank_counts(pid, root)
    make_private(d, walk=True)   # round 46: what was kept, and the folders made again, are the owner's
    return {
        "ok": not errors and after["total"] == 0,
        "person_id": pid,
        "removed_n": len(removed),
        "removed": removed[:40],
        "before": before,
        "after": after,
        "errors": errors[:10],
    }


def display_name(person_id: str, root: Path | None = None) -> str:
    pj = person_dir(person_id, root) / "person.json"
    if pj.is_file():
        try:
            return str(json.loads(pj.read_text()).get("display") or person_id)
        except Exception:
            pass
    return person_id.replace("-", " ").title()
