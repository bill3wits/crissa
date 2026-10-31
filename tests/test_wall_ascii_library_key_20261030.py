"""Her pictures are kept per library (the User 2026-10-30, after 1.0.2 over 1.0.1's state home sat on "Building
Crissa... 0%" an hour in: "Library changes all that handled?"; "goodsimple do now then"). The disk tier's directory
named the code and the framing, never the library, so new mouth picks drew under the old directory for good."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wall_ascii_disk as D  # noqa: E402

FK = (3440, 1440, 3.15, True, None, None, ("whole", 1.0))


def _lib(tmp_path, name="lib", tile=b"x" * 10, pick="a.png"):
    lib = tmp_path / name
    (lib / "visemes" / "phoneme-map" / "tiles").mkdir(parents=True)
    (lib / "clip-a.playlist.json").write_text(json.dumps({"frames": [], "paint": True}))
    (lib / "mouth-dials.json").write_text(json.dumps({"pin": "nose+chin-pixel"}))
    (lib / "visemes" / "phoneme-map" / "map.json").write_text(json.dumps({"AH": pick}))
    (lib / "visemes" / "phoneme-map" / "tiles" / "a.png").write_bytes(tile)
    return lib


def _env(lib=None, **kw):
    e = {"HOUSE_POSE_DIR": str(lib)} if lib is not None else {}
    e.update(kw)
    return e


def _fresh():
    D._PRINT.pop("lib", None)


def test_no_library_named_keeps_the_old_directory_name_byte_for_byte():
    _fresh()
    import hashlib
    old = hashlib.blake2b(repr((D.code_print(), D.codec(), FK)).encode(), digest_size=10).hexdigest()
    assert D.framing_dir_name(FK, {}) == old
    assert D.framing_dir_name(FK, _env(Path("/nonexistent-library"))) == old


def test_a_library_is_its_own_directory_and_the_switch_turns_it_off(tmp_path):
    _fresh()
    lib = _lib(tmp_path)
    plain = D.framing_dir_name(FK, {})
    named = D.framing_dir_name(FK, _env(lib))
    assert named != plain
    assert D.framing_dir_name(FK, _env(lib, HOUSE_WALL_ASCII_LIBRARY_KEY="0")) == plain


def test_a_repick_a_dial_or_a_recut_tile_moves_the_directory(tmp_path):
    base = D.framing_dir_name(FK, _env(_lib(tmp_path, "a")))
    _fresh()
    repick = D.framing_dir_name(FK, _env(_lib(tmp_path, "b", pick="b.png")))
    _fresh()
    recut = D.framing_dir_name(FK, _env(_lib(tmp_path, "c", tile=b"y" * 11)))
    lib_d = _lib(tmp_path, "d")
    (lib_d / "mouth-dials.json").write_text(json.dumps({"pin": "nose-pixel"}))
    _fresh()
    dial = D.framing_dir_name(FK, _env(lib_d))
    assert len({base, repick, recut, dial}) == 4


def test_the_same_library_in_another_folder_shares_its_pictures(tmp_path):
    _fresh()
    a = D.framing_dir_name(FK, _env(_lib(tmp_path, "install-1")))
    _fresh()
    b = D.framing_dir_name(FK, _env(_lib(tmp_path, "install-2")))
    assert a == b                                   # a reinstall of the same library keeps what was drawn


def test_the_tier_names_its_library_in_framing_json(tmp_path):
    _fresh()
    lib = _lib(tmp_path)
    t = D.DiskTier(root=tmp_path / "cache", env=_env(lib, HOUSE_WALL_ASCII_DISK_DIR=str(tmp_path / "cache")))
    t._prefill = lambda fk: None                    # no pre-fill process in a test
    t.framing(FK)
    meta = json.loads((t.fdir / "framing.json").read_text())
    assert meta["library"] == D.library_print(_env(lib)) and meta["pose_dir"] == str(lib)
    assert t.fdir.name == D.framing_dir_name(FK, _env(lib))


def test_the_launcher_tells_the_wall_which_library_she_wears():
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    assert 'wall_env["HOUSE_POSE_DIR"] = str(pose_dir)' in src
    assert 'g.specs["wall"] = (wargv, dict(wenv, HOUSE_POSE_DIR=pose_dir), wport)' in src
