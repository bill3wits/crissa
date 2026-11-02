"""Per-avatar measurements, loaded from data instead of compiled in.

Before 2026-09-01 the numbers that describe a face — where her mouth sits
inside her face box, how far it may drift, how far open counts as open — were
constants in `lib/pose_mouth.py`, hand-measured off Crissa's `hub-up.png` on
2026-08-29. Any second avatar would have silently worn her geometry.

The split this enforces: **formulas in code, measurements in data.** The paste
ramp in `w2l.py` is the model — it is expressed in mouth-heights, so it re-fits
itself to whatever `mouth_frac` says. Nothing here should ever hold a formula.

    HOUSE_AVATAR=crissa        which manifest to load (default: crissa)
    HOUSE_AVATARS_DIR        where manifests live (default: <repo>/avatars)

A missing manifest raises. It is deliberately not a silent fallback: falling
back to a built-in default is exactly the bug this replaces — a second avatar
quietly running on the first one's face.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = "crissa"


def avatars_dir() -> Path:
    return Path(os.environ.get("HOUSE_AVATARS_DIR", str(ROOT / "avatars")))


def current_name() -> str:
    return (os.environ.get("HOUSE_AVATAR") or DEFAULT).strip() or DEFAULT


@lru_cache(maxsize=8)
def load(name: str | None = None) -> dict[str, Any]:
    """The manifest for `name`, or for HOUSE_AVATAR. Cached per name."""
    n = (name or current_name()).strip()
    p = avatars_dir() / n / "manifest.json"
    if not p.is_file():
        raise FileNotFoundError(
            "no avatar manifest at %s — set HOUSE_AVATAR or create it. "
            "Refusing to fall back to another avatar's face." % p)
    man = json.loads(p.read_text(encoding="utf-8"))
    man["_path"] = str(p)
    man["_name"] = n
    return man


def roles(poses_dir: str | os.PathLike | None = None, name: str | None = None) -> dict:
    """The library's named moments (the User 2026-10-29: "rename the related clips ... substitute titles"): {role: clip
    key} from its manifest or library.json `roles` (walk_in, walk_out, glance, door, tool_work, nod, notes, shift, yawn,
    close, hello). {} when it names none; the player then plays a moment only if the library has the classic key."""
    r = (_manifest_for(poses_dir, name) or {}).get("roles")
    return {str(k): str(v) for k, v in r.items() if v} if isinstance(r, dict) else {}


def listen_loop(poses_dir: str | os.PathLike | None = None, name: str | None = None) -> str | None:
    """The clip key this face listens and answers on, when its manifest declares one.

    `listen_loop` is OPTIONAL in avatars/<face>/manifest.json (2026-10-02). With `poses_dir` the face
    is whichever manifest names that library in its own `poses_dir` (a player is started on a
    library, not on a face name); without it, `name` or HOUSE_AVATAR. None = not declared, and the
    caller keeps its own rule. Kill: CRISSA_LISTEN_LOOP=0 reads every manifest as undeclared."""
    if os.environ.get("CRISSA_LISTEN_LOOP", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    key = str((_manifest_for(poses_dir, name) or {}).get("listen_loop") or "").strip()
    return key or None


def boot_loop(poses_dir: str | os.PathLike | None = None, name: str | None = None) -> str | None:
    """The clip key a player boots on, when its manifest declares one (`boot_loop`, the User 2026-10-05: a pack
    boots to listen). Since the fold (the User 2026-10-28, "i approved it all") the house's Crissa manifest declares it too
    (listen-warm, as the kit); a face whose manifest has none boots where it always has, and the exporter writes the
    face's `listen_loop` into a pack's copy. Kill: CRISSA_BOOT_LOOP=0 reads every manifest as
    undeclared."""
    if os.environ.get("CRISSA_BOOT_LOOP", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    key = str((_manifest_for(poses_dir, name) or {}).get("boot_loop") or "").strip()
    return key or None


def rest_loop(poses_dir: str | os.PathLike | None = None, name: str | None = None) -> str | None:
    """The loop she rests on BETWEEN lines, when the manifest declares one apart from her listen loop (`rest_loop`,
    round 39, .101's N1: listen-warm's filmed smile showed through every unpainted pass while she talked on it). She
    talks on `listen_loop` (a still filmed mouth, the 09-27 law) and rests on this one. The house's Crissa manifest
    declares it since the fold (2026-10-28), as the kit's always did.
    Kill: CRISSA_REST_LOOP=0 reads every manifest as undeclared."""
    if os.environ.get("CRISSA_REST_LOOP", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    key = str((_manifest_for(poses_dir, name) or {}).get("rest_loop") or "").strip()
    return key or None


def _manifest_for(poses_dir: str | os.PathLike | None = None, name: str | None = None) -> dict | None:
    """The manifest that names this library in its own `poses_dir`, else the face by `name` or HOUSE_AVATAR."""
    man = None
    if poses_dir is not None:
        want = Path(poses_dir).name
        for p in sorted(avatars_dir().glob("*/manifest.json")):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(m, dict) and Path(str(m.get("poses_dir") or "")).name == want:
                man = m
                break
        if man is None:
            # A LIBRARY DECLARES ITS OWN LOOPS (2026-10-27, Crissa desk v2 as the kit's default skin): a library no
            # avatar manifest names (a store hub, a library a pack carries) may say its listen / boot / rest loop in
            # its own library.json, the file the avatar picker reads its name from. Absent, the callers' rules hold.
            try:
                m = json.loads((Path(poses_dir) / "library.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                m = None
            man = m if isinstance(m, dict) else None
    else:
        try:
            man = load(name)
        except (OSError, ValueError):
            man = None
    return man if isinstance(man, dict) else None


def mouth_frac(name: str | None = None) -> tuple[float, float, float, float]:
    """Mouth box as a fraction of the derived face box: (x, y, w, h).

    Recomputed from the two measured boxes rather than read off the stored
    `mouth_frac`, so a hand-edited convenience value can never quietly win.
    """
    f = load(name)["face"]
    fx, fy, fw, fh = f["mouth_face_up"]
    mx, my, mw, mh = f["mouth_box_up"]
    return ((mx - fx) / fw, (my - fy) / fh, mw / fw, mh / fh)


def head_geom(name: str | None = None) -> dict:
    """Jaw-plane hinge (kw/kh) and optional throat. Formulas stay in head_frame.

    kw/kh size the matte / hide_clip_jaw ellipse, never the viseme tile.
    Missing key -> small defaults that do not pretend to be measured.
    """
    man = load(name)
    g = dict(man.get("head_geom") or {})
    g.setdefault("kw", 0.28)
    g.setdefault("kh", 0.18)
    g.setdefault("warp_cap_deg", 0.0)  # 0 = off
    g.setdefault("inpaint_gap_s", 0.08)
    g.setdefault("inpaint_past_chin_s", 0.12)
    g.setdefault("pad", 0.55)
    return g
