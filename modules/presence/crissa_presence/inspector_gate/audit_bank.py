"""Read-only audit of identity-store face/voice emb files + cross-person geometry."""

from __future__ import annotations

from .. import paths as _paths

import argparse
import json
import sys
from pathlib import Path

from .gate import PersonBank, best_sim_to_bank, cosine
from .validate import validate_emb_record, l2_normalize


def load_banks(root: Path) -> tuple[list[PersonBank], list[dict]]:
    people = root / "people"
    banks: list[PersonBank] = []
    reports: list[dict] = []
    if not people.is_dir():
        return banks, reports
    for person_dir in sorted(people.iterdir()):
        if not person_dir.is_dir() or person_dir.name.startswith("_"):
            continue
        pid = person_dir.name.lower()
        gender = None
        pj = person_dir / "person.json"
        if pj.is_file():
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                g = data.get("gender") or data.get("sex")
                if g:
                    gender = str(g).strip().upper()[:1]
            except Exception as e:
                reports.append({"person_id": pid, "error": f"person.json {e}"})
        embs: list[list[float]] = []
        for modality, sub in (("face", "face"), ("voice", "voice")):
            d = person_dir / sub
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                if "staged" in f.parts or "retired" in f.parts:
                    continue
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except Exception as e:
                    reports.append({"file": str(f), "error": str(e)})
                    continue
                rec.setdefault("person_id", pid)
                rec.setdefault("modality", modality)
                vr = validate_emb_record(rec, expect_person_id=pid, accept_legacy=True)
                reports.append(
                    {
                        "file": str(f),
                        "person_id": pid,
                        "modality": modality,
                        "ok": vr.ok,
                        "errors": vr.errors,
                        "dim": vr.dim,
                        "model": vr.model,
                    }
                )
                if vr.ok and vr.normalized and modality == "face":
                    embs.append(vr.normalized)
        if embs:
            banks.append(PersonBank(pid, gender, embs))
    return banks, reports


def cross_person_stats(banks: list[PersonBank]) -> dict:
    """Measure max/mean cross-person face sim (ASSUMED floor calibration)."""
    pairs = []
    for i, a in enumerate(banks):
        for b in banks[i + 1 :]:
            # max sim any emb a vs bank b
            mx = -1.0
            for e in a.embs:
                mx = max(mx, best_sim_to_bank(e, b))
            for e in b.embs:
                mx = max(mx, best_sim_to_bank(e, a))
            pairs.append({"a": a.person_id, "b": b.person_id, "max_sim": mx})
    if not pairs:
        return {"n_pairs": 0}
    sims = [p["max_sim"] for p in pairs]
    sims_sorted = sorted(sims)
    return {
        "n_pairs": len(pairs),
        "max_cross_sim": max(sims),
        "mean_cross_sim": sum(sims) / len(sims),
        "p95_cross_sim": sims_sorted[int(0.95 * (len(sims_sorted) - 1))],
        "worst_pairs": sorted(pairs, key=lambda p: -p["max_sim"])[:5],
        "note": "Use for GateConfig.cross_person_floor calibration (ASSUMED 0.45 until measured)",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only identity-store emb bank audit")
    ap.add_argument(
        "--root",
        default=str(_paths.IDENTITY),
        help="HOUSE_ID_ROOT",
    )
    ap.add_argument("--json", action="store_true", help="print full JSON report")
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser()
    banks, reports = load_banks(root)
    ok_n = sum(1 for r in reports if r.get("ok"))
    bad_n = sum(1 for r in reports if r.get("ok") is False)
    geo = cross_person_stats(banks)
    out = {
        "root": str(root),
        "files_ok": ok_n,
        "files_bad": bad_n,
        "people_with_face_bank": len(banks),
        "cross_person_face_geometry": geo,
        "files": reports if args.json else f"{len(reports)} files (pass --json for detail)",
    }
    print(json.dumps(out, indent=2, default=str))
    return 0 if bad_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
