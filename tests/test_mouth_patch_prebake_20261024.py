"""The finished mouth patches, kept and built ahead at install (the User 2026-10-24: "every painted patch for each loop
frame and tile could be built ahead <- we do this for all ascii frames too at first install too. Add it there").
lib/mouth_patch (memory), lib/mouth_patch_disk (disk + budget), the conductor's hook and its build child, and the
build view's mouths row."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import mouth_patch  # noqa: E402
import mouth_patch_disk  # noqa: E402

SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _frames():
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (72, 128, 3), dtype=np.uint8)
    out = base.copy()
    out[20:40, 50:90] = rng.integers(0, 255, (20, 40, 3), dtype=np.uint8)
    return base, out


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("HOUSE_MOUTH_PATCH_MB", "64")
    mouth_patch.clear()
    mouth_patch.disk = None
    mouth_patch.disk_writes = False
    yield
    mouth_patch.clear()
    mouth_patch.disk = None
    mouth_patch.disk_writes = False


def test_a_kept_patch_comes_back_byte_for_byte_with_its_side_record():
    base, out = _frames()
    k = mouth_patch.digest("k", 1)
    mouth_patch.put(k, base, out, {"skip": "", "_pin_dbg": {"ncc": 0.99}})
    got = mouth_patch.take(k, base)
    assert got is not None and np.array_equal(got[0], out) and got[1]["_pin_dbg"] == {"ncc": 0.99}


def test_another_film_frame_under_the_box_is_painted_not_copied():
    base, out = _frames()
    k = mouth_patch.digest("k", 2)
    mouth_patch.put(k, base, out)
    other = base.copy()
    other[25, 60] ^= 1
    assert mouth_patch.take(k, other) is None and mouth_patch.stats["refused"] >= 1


def test_the_disk_tier_round_trips_and_a_bad_file_is_a_miss(tmp_path):
    base, out = _frames()
    d = mouth_patch_disk.DiskTier("print1", home=tmp_path, cfg={"disk_share_of_free": 1.0, "max_gb": 1,
                                                                "keep_free_gb": 0}, env={})
    k = mouth_patch.digest("k", 3)
    ent = mouth_patch.entry_of(base, out, {"_lip_dbg": {"yaw": np.float64(-1.5)}})
    assert d.put(k, ent)
    back = d.get(k)
    assert back[0] == ent[0] and back[1] == ent[1] and np.array_equal(back[2], ent[2]) and back[3]["_lip_dbg"]["yaw"] == -1.5
    mouth_patch.disk = d                         # the memory tier falls through to it
    mouth_patch.put(mouth_patch.digest("glass"), base, out)
    assert not d.has(mouth_patch.digest("glass"))   # the glass never writes the disk; only the build does
    got = mouth_patch.take(k, base)
    assert got is not None and np.array_equal(got[0], out) and mouth_patch.stats["disk_hit"] >= 1
    p = d.path(k)
    raw = bytearray(p.read_bytes())
    raw[-1] ^= 0xFF
    p.write_bytes(bytes(raw))
    assert d.get(k) is None and not p.exists()


def test_the_budget_drops_dead_code_first_then_refuses(tmp_path):
    base, out = _frames()
    dead = tmp_path / "cache" / "mouth-patch" / "oldprint"
    dead.mkdir(parents=True)
    (dead / "x.mpt").write_bytes(b"0" * 20000)
    ent = mouth_patch.entry_of(base, out)
    size = len(mouth_patch_disk.pack(ent))
    d = mouth_patch_disk.DiskTier("print2", home=tmp_path, env={"HOUSE_MOUTH_PATCH_DISK_GB": str((size + 100) / (1 << 30))},
                                  log=lambda m: None)
    assert d.put(mouth_patch.digest("a"), ent)
    assert not dead.exists()                    # another code print's folder went first
    assert not d.put(mouth_patch.digest("b"), ent) and d.stats["refused_budget"] == 1


def test_the_budget_dials_live_in_config_with_their_measurement():
    cfg = json.loads((ROOT / "config" / "mouth-patch.json").read_text(encoding="utf-8"))
    for dial in ("disk_share_of_free", "max_gb", "keep_free_gb"):
        assert isinstance(cfg[dial], (int, float)) and len(cfg["_%s_why" % dial]) > 60, dial
    assert "80 KB" in cfg["_disk_share_of_free_why"]
    gb = 1 << 30
    assert mouth_patch_disk.cap_bytes(1000 * gb, 0, cfg, env={}) == int(min(cfg["disk_share_of_free"] * 1000, cfg["max_gb"]) * gb)
    assert mouth_patch_disk.cap_bytes(int(cfg["keep_free_gb"] * gb), 0, cfg, env={}) == 0     # never below the floor
    src = (ROOT / "lib" / "mouth_patch_disk.py").read_text(encoding="utf-8")
    for literal in ("0.05", "20.0", " 20 ", "100"):
        assert literal not in src.split('"""', 2)[2], literal


def test_the_code_print_names_the_conductor_and_every_lib_module():
    p = mouth_patch.code_print()
    assert len(p) == 20 and p == mouth_patch.code_print()
    src = (ROOT / "lib" / "mouth_patch.py").read_text(encoding="utf-8")
    assert '"house-conductor.py"' in src and 'rglob("*.py")' in src


def test_the_conductor_keeps_only_a_paint_with_no_state_from_before():
    body = SRC.split("    def _paste_head_kept(self, base: np.ndarray, box, tile, lip, rel: str", 1)[1].split("\n    def _patch_side", 1)[0]
    assert "if not _MOUTH_PATCH_ON:" in body                                     # the dissolve is blended from kept frames now
    assert "self._patch_unsafe or (self.key, int(self.i)) != at" in body          # a turn, or a seek mid-paint
    tri = SRC.split("    def _tri_landing(self,", 1)[1].split("\n    def ", 1)[0]
    assert "self._patch_unsafe = True" in tri
    key = SRC.split("    def _patch_key(self,", 1)[1].split("\n    def ", 1)[0]
    for part in ("self._patch_epoch(rel)", "self._turn_cfg()", "dials", "_pl_class", "_pl_yaw", "_pl_cell", "_pl_band",
                 "_pl_fold", "_headback_k", "mouth_patch.code_print()", "self._loop_ver()"):
        assert part in key, part


def test_the_build_is_the_conductor_itself_held_while_she_speaks():
    assert 'sys.argv[1] == "--mouth-patch-prefill"' in SRC and "mouth_patch_prefill_main()" in SRC
    setup = SRC.split("def mouth_patch_setup(ph) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "HOUSE_MOUTH_PATCH_CHILD" in setup and "prctl(1, signal.SIGKILL)" in setup
    run = SRC.split("    def run(self) -> None:", 1)[1][:2500]
    assert "mouth_patch_prefill_hold(bool(self.mouth.painting() or self.mouth.force_class is not None))" in run
    hold = SRC.split("def mouth_patch_prefill_hold(on_air: bool) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "signal.SIGSTOP if on_air else signal.SIGCONT" in hold
    child = SRC.split("def mouth_patch_prefill_main() -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "os.SCHED_IDLE" in child and "os.nice(19)" in child and "ph._composite(arr[i], dt)" in child
    assert "as_tiles(getattr(ph, \"_pl_hit\", None))" in child          # every tile of the class, not only the pick
    assert "mouth_patch_setup(ph)" in SRC.split("def main() -> None:", 1)[1]


def test_the_build_view_shows_the_mouths(tmp_path, monkeypatch):
    import crissa_build
    monkeypatch.delenv("HOUSE_MOUTH_PATCH_DISK_DIR", raising=False)
    d = tmp_path / "cache" / "mouth-patch" / "p"
    d.mkdir(parents=True)
    (d / "progress.json").write_text(json.dumps({"total": 200, "jobs_done": 50, "done": False, "stopped": "",
                                                 "pid": os.getpid(), "disk_used": 1 << 29, "at": 1.0}))
    m = crissa_build.build_view(home=tmp_path)["mouths"]
    assert m["pct"] == 25 and m["running"] and "50 of 200" in m["note"]
    (d / "progress.json").write_text(json.dumps({"total": 200, "jobs_done": 200, "done": True, "pid": 2 ** 22 + 7}))
    m = crissa_build.mouths_view(home=tmp_path)
    assert m["done"] and not m["running"] and m["note"].endswith("done")
    assert crissa_build.mouths_view(home=tmp_path / "none") is None


def test_the_dissolve_is_blended_from_two_kept_frames_on_this_film_frame():
    """2026-10-25 (.101's paint-budget ask): the kit's lock has xfade_ms 20.833, and the store was bypassed under any
    dissolve, so an install built nothing and painted nothing from it. A tile switch now blends the outgoing and the
    incoming tile, each a kept steady paint on THIS frame; the fade-out of a tile with no successor is the same."""
    body = SRC.split("    def _paste_head_kept(self, base: np.ndarray, box, tile, lip, rel: str", 1)[1].split("\n    def _kept_frame", 1)[0]
    assert "mouth_patch.lerp_frames(base, f_out, f_in, xfade_ease(t))" in body
    assert "self._kept_frame_as(base, box, pair[0], pair[1], xfk[0], xfk[2] if len(xfk) > 2 else None," in body   # the outgoing tile, on this frame, under ITS class and cell
    assert "MOUTH_XFADE_MS / 1000.0 / max(0.05, float(PLAY_RATE))" in body        # the live dissolve's own clock
    kept = SRC.split("    def _kept_frame(self, base: np.ndarray, box, tile, lip, rel: str", 1)[1].split("\n    def _patch_side", 1)[0]
    assert "self._xf_bypass = True" in kept and "self._xf_bypass = False" in kept    # the steady paint reads no dissolve state
    xf = SRC.split("    def _xfade(self, rel, warped, a):", 1)[1].split("\n    def ", 1)[0]
    assert 'if getattr(self, "_xf_bypass", False):' in xf
    fade = SRC.split("    def _fade_out(self, base: np.ndarray) -> np.ndarray:", 1)[1].split("\n    def ", 1)[0]
    assert "mouth_patch.lerp_frames(base, f_out, base, xfade_ease(t))" in fade
    nd = SRC.split("_PATCH_NOT_DIALS = (", 1)[1].split(")", 1)[0]
    assert "MOUTH_XFADE_MS" in nd and "MOUTH_FAST_TRACK" in nd                        # a steady paint's key ignores the dissolve
    child = SRC.split("def mouth_patch_prefill_main() -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "off under a dissolve" not in child and 'globals()["MOUTH_XFADE_MS"] = 0.0' in child


def test_the_blend_of_two_finished_frames_is_the_live_dissolves_picture():
    """pose_mouth.xfade_mix (premultiplied) stamped alpha-over equals the per-pixel lerp of the two finished frames."""
    import pose_mouth
    rng = np.random.default_rng(3)
    base = rng.integers(0, 255, (60, 90, 3), dtype=np.uint8)
    w0 = rng.integers(0, 255, base.shape, dtype=np.uint8)
    w1 = rng.integers(0, 255, base.shape, dtype=np.uint8)
    a0 = np.zeros(base.shape[:2], np.float32); a0[10:40, 20:50] = np.linspace(0.2, 1.0, 30)[:, None]
    a1 = np.zeros(base.shape[:2], np.float32); a1[15:45, 30:70] = np.linspace(1.0, 0.1, 40)[None, :]

    def over(w, a):
        return np.clip(np.rint(base * (1 - a[..., None]) + w.astype(np.float32) * a[..., None]), 0, 255).astype(np.uint8)
    f0, f1 = over(w0, a0), over(w1, a1)
    for t in (0.2, 0.5, 0.8):
        mw, ma = pose_mouth.xfade_mix(w0, a0, w1, a1, t)
        live = over(mw, ma)
        got = mouth_patch.lerp_frames(base, f0, f1, t)
        assert int(np.abs(live.astype(np.int16) - got.astype(np.int16)).max()) <= 2, t      # rounding only
        assert np.array_equal(got[:5], base[:5]) and np.array_equal(got[50:], base[50:])       # untouched outside the box
    assert mouth_patch.lerp_frames(base, f0, f1, 0.0) is f0 and mouth_patch.lerp_frames(base, f0, f1, 1.0) is f1


def test_the_runtime_hooks_are_not_dials_so_the_build_and_the_glass_key_alike():
    """2026-10-27: Setup installs LILA_PAINTS / MOUTH_HUSH as functions at boot; a name list taken after that left them
    out while the build child's carried them as None, and every prebaked key missed on the glass (disk read 0)."""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "house-conductor.py").read_text()
    i = src.index("_PATCH_NOT_DIALS = (")
    line = src[i:src.index("\n", i)]
    for n in ("LILA_PAINTS", "MOUTH_HUSH", "MOUTH_XFADE_MS"):
        assert '"%s"' % n in line, n
