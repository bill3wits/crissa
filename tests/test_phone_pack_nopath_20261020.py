"""A phone pack carries no machine path (2026-10-20): the release scrub refused "pose_dir" in 7 of 17 manifests. The
bake writes sync_zero_ms, which is what the phone server reads; pose_dir was only the fallback for pre-10-17 packs."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_bake_writes_no_pose_dir_and_still_writes_sync_zero():
    src = (ROOT / "scripts" / "phone-pack.py").read_text(encoding="utf-8")
    extra = src.split('man = PP.manifest(a.loop,', 1)[1].split("PP.write_json(out / \"manifest.json\", man)", 1)[0]
    assert '"pose_dir"' not in extra and '"sync_zero_ms": float(' in extra


def test_the_server_reads_sync_zero_first_and_keeps_the_old_fallback():
    src = (ROOT / "scripts" / "phone-app-server.py").read_text(encoding="utf-8")
    assert 'sync0 = m.get("sync_zero_ms")' in src
    assert 'if sync0 is None and m.get("pose_dir"):' in src          # packs baked before 2026-10-17 still read
