#!/usr/bin/env python3
"""Garbage-file guard for face references.

The User 2026-08-16, looking at people/<user_id>/face/refs/:

    "what are all these in my faceshots folder? garbage file guard"

They are `enroll_desk_*.jpg`, written 8/1–8/5 by a desk-enrolment routine that
saved a frame every time it fired and never asked whether a face was in it.
Seventeen of the User's thirty references contain no detectable face at all — an
empty desk, a monitor, a wall, a window blind.

A reference with no face is not merely useless. `face/refs/` is what a human
reads to decide whether a bank is healthy, and what the Argus line-up shows the
model as "this is what this person looks like". Desk photos make both worse: the
audit looks like corruption, and the line-up spends a cell on a wall.

So: nothing enters `face/refs/` without a face in it. Reject at write time, and
quarantine what already got in — moved, never deleted, following the existing
`_quarantine-*` convention in people/.

Usage:
    python3 crissa_id/ref_guard.py --audit
    python3 crissa_id/ref_guard.py --quarantine        # move failures aside
    python3 crissa_id/ref_guard.py --check <file.jpg>  # one file, exit 0/1
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time

from . import paths as _paths  # noqa: E402

ROOT = pathlib.Path(os.environ.get("HOUSE_ID_PEOPLE", _paths.identity_root() / "people"))

# A face smaller than this is a bystander or a reflection, not a reference.
MIN_FACE_PX = int(os.environ.get("REF_GUARD_MIN_PX", "80"))
MIN_DET = float(os.environ.get("REF_GUARD_MIN_DET", "0.55"))

from . import face_engine  # noqa: E402  (the engine the reference guard reads with)


def check_ref(path: str | pathlib.Path) -> dict:
    """Is this file fit to be a face reference?

    Reasons a file fails, in the order they are worth reporting:
      no_image   — unreadable
      no_face    — the desk photos; nothing to enrol
      too_small  — a face is present but too far away to carry identity
      low_det    — the detector is not confident it is a face
      many_faces — two or more faces: which one is this person? Ambiguous
                   references are how the wrong face gets bound to a name.
    """
    p = pathlib.Path(path)
    got = face_engine.faces_in(p)
    if not got.get("ok") and got.get("error") == "no_image":
        return {"ok": False, "reason": "no_image", "file": p.name}
    if not got.get("ok"):
        return {"ok": False, "reason": "no_face", "file": p.name, "error": got.get("error")}
    faces = int(got.get("faces") or 0)
    if not faces:
        return {"ok": False, "reason": "no_face", "file": p.name}
    bbox = got.get("bbox") or [0, 0, 0, 0]
    w = int(bbox[2] - bbox[0])
    h = int(bbox[3] - bbox[1])
    det = float(got.get("det_score") or 0.0)
    out = {"file": p.name, "faces": faces, "px": min(w, h), "det": round(det, 3)}
    if faces > 1:
        return {**out, "ok": False, "reason": "many_faces"}
    if min(w, h) < MIN_FACE_PX:
        return {**out, "ok": False, "reason": "too_small"}
    if det < MIN_DET:
        return {**out, "ok": False, "reason": "low_det"}
    return {**out, "ok": True, "reason": "ok"}


def is_frozen(pid: str) -> bool:
    """A frozen identity is frozen for cleanup too.

    Learned the hard way 2026-08-16: this guard quarantined a regular_3/4/5 as
    "too_small" without asking, and people/<regular_id>/FREEZE.md exists precisely
    because that bank was poisoned once already (the User's own face got into it,
    see _quarantine-<user_id>-poison-20260810). A freeze is a decision someone made
    after being burned; a tidiness heuristic does not get to overrule it.
    """
    import sys

    t = pathlib.Path(__file__).resolve().parent
    if str(t) not in sys.path:
        sys.path.insert(0, str(t))
    try:
        from crissa_id.house_enroll.person import is_id_frozen  # type: ignore

        return bool(is_id_frozen(pid))
    except Exception:
        # unreadable freeze state counts as FROZEN — fail safe on a bank
        return (ROOT / pid / "FREEZE.md").is_file()


def people(include_frozen: bool = False) -> dict[str, list[pathlib.Path]]:
    out: dict[str, list[pathlib.Path]] = {}
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.isupper():
            continue
        if not include_frozen and is_frozen(d.name):
            print(f"  {d.name}: FROZEN — skipped (see people/{d.name}/FREEZE.md)")
            continue
        refs = sorted((d / "face" / "refs").glob("*.jpg")) + sorted(
            (d / "face" / "refs").glob("*.png"))
        if refs:
            out[d.name] = refs
    return out


def audit() -> dict[str, list[dict]]:
    res: dict[str, list[dict]] = {}
    for name, refs in people().items():
        rows = [check_ref(p) for p in refs]
        res[name] = rows
        bad = [r for r in rows if not r["ok"]]
        print(f"\n{name.upper():10s} refs={len(rows):3d}  usable={len(rows)-len(bad):3d}  REJECT={len(bad):3d}")
        by = {}
        for r in bad:
            by.setdefault(r["reason"], []).append(r["file"])
        for reason, files in sorted(by.items()):
            head = ", ".join(files[:4]) + (f" (+{len(files)-4} more)" if len(files) > 4 else "")
            print(f"   {reason:11s} {len(files):3d}  {head}")
    return res


def quarantine(res: dict[str, list[dict]]) -> None:
    stamp = time.strftime("%Y%m%d")
    qroot = ROOT / f"_quarantine-no-face-{stamp}"
    moved = 0
    for name, rows in res.items():
        bad = [r for r in rows if not r["ok"]]
        if not bad:
            continue
        dst = qroot / name
        dst.mkdir(parents=True, exist_ok=True)
        for r in bad:
            src = ROOT / name / "face" / "refs" / r["file"]
            if src.is_file():
                shutil.move(str(src), str(dst / r["file"]))
                moved += 1
        (dst / "WHY.json").write_text(json.dumps(
            {"moved_ts": time.time(), "reasons": {r["file"]: r["reason"] for r in bad},
             "note": "no usable face — see crissa_id/ref_guard.py"}, indent=2))
    print(f"\nquarantined {moved} file(s) -> {qroot}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--quarantine", action="store_true")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.check:
        r = check_ref(a.check)
        print(json.dumps(r))
        return 0 if r["ok"] else 1
    res = audit()
    if a.quarantine:
        quarantine(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
