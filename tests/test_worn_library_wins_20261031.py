"""The worn library owns its own face (house-rails-6a 2026-10-31, MP 11.ae/11.ah): the Lila Mobile order's bench painted 0
of 8650 frames because a pack's built-in Lila bank (display/lila-poses, Setup's lips) claimed every lila-* key by prefix,
so lila-listen read the Setup bank's tiles and playlists, never the order's. With CRISSA_WORN_LIBRARY_WINS (ON in a pack,
never on the house) a worn library whose listen loop wears the face keeps the keys and tile files it carries; the bank
keeps her Setup keys; a library whose listen loop names no face (Crissa's, with her Setup lila-* rows) is untouched."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
TILE = "visemes/phoneme-map/tiles/lila-listen_f0064_forward_0_fc1.png"


def _block(tmp_path: Path, worn: Path, *, is_house: bool = False, env: dict | None = None) -> dict:
    """The bank block, worn_owns, face_of, bank_dir and _lila_host_key, run on a tree whose kit ships Lila's bank."""
    root = tmp_path / "tree"
    bank = root / "display" / "lila-poses"
    bank.mkdir(parents=True)
    (bank / "inventory.json").write_text(json.dumps({"clips": {"lila-reg-listen": {"file": "clip-lila-reg-listen.mp4"},
                                                               "lila-listen": {"file": "clip-lila-listen.mp4"}}}))
    i0 = CONDUCTOR.index("FACE_BANKS: dict[str, Path] = {}")
    i1 = CONDUCTOR.index("\n\n\ndef film_path(")
    j0 = CONDUCTOR.index("def _lila_host_key(key: str) -> bool:")
    j1 = CONDUCTOR.index("\n\n\n", j0)
    import os
    fake_os = SimpleNamespace(environ=dict(env or {}), path=os.path)
    ns = {"os": fake_os, "Path": Path, "json": json, "time": time, "ROOT": root, "POSE_DIR": worn,
          "IS_HOUSE": is_house, "house_default": lambda house, pack: house if is_house else pack}
    exec(CONDUCTOR[i0:i1] + "\n" + CONDUCTOR[j0:j1], ns)
    return ns


def _lila_library(d: Path, listen: str = "lila-listen") -> Path:
    d.mkdir(parents=True)
    (d / "library.json").write_text(json.dumps({"listen_loop": listen, "boot_loop": listen}))
    (d / "inventory.json").write_text(json.dumps({"clips": {"lila-listen": {"file": "clip-lila-listen.mp4"},
                                                            "lila-scroll": {"file": "clip-lila-scroll.mp4"}}}))
    (d / TILE).parent.mkdir(parents=True)
    (d / TILE).write_bytes(b"png")
    return d


def test_a_worn_lila_library_keeps_its_own_keys_and_tiles(tmp_path):
    worn = _lila_library(tmp_path / "lib")
    ns = _block(tmp_path, worn)
    assert ns["FACE_BANKS"].keys() == {"lila"}, "the kit's Setup bank is still named (her Setup lines need it)"
    for name in ("lila-listen", "lila-scroll", "clip-lila-listen", TILE):
        assert ns["face_of"](name) is None, name
        assert ns["bank_dir"](name) == worn, name
    assert ns["_lila_host_key"]("lila-listen") is False, "her own loop is not a guest row"


def test_the_bank_keeps_what_the_worn_library_does_not_carry(tmp_path):
    ns = _block(tmp_path, _lila_library(tmp_path / "lib"))
    for name in ("lila-reg-listen", "lila-ob-welcome", "visemes/phoneme-map/tiles/lila-reg-listen_f0001_fc1.png"):
        assert ns["face_of"](name) == "lila", name
    assert ns["_lila_host_key"]("lila-reg-listen") is True


def test_crissas_library_with_setup_rows_is_untouched(tmp_path):
    worn = tmp_path / "crissa"
    worn.mkdir()
    (worn / "library.json").write_text(json.dumps({"listen_loop": "listen"}))
    (worn / "inventory.json").write_text(json.dumps({"clips": {"listen": {}, "lila-listen-enter": {}, "lila-ids-done": {}}}))
    ns = _block(tmp_path, worn)
    for name in ("lila-listen-enter", "lila-ids-done", "lila-reg-listen"):
        assert ns["face_of"](name) == "lila", name
    worn2 = tmp_path / "nolib"
    worn2.mkdir()
    (worn2 / "inventory.json").write_text(json.dumps({"clips": {"lila-listen": {}}}))
    assert _block(tmp_path / "b", worn2)["face_of"]("lila-listen") == "lila", "no library.json: nothing claims the face"


def test_the_switch_and_the_house(tmp_path):
    worn = _lila_library(tmp_path / "lib")
    assert _block(tmp_path / "k", worn, env={"CRISSA_WORN_LIBRARY_WINS": "0"})["face_of"]("lila-listen") == "lila", \
        "=0: every prefix to its bank, as before"
    h = _block(tmp_path / "h", worn, is_house=True, env={"HOUSE_FACE_BANKS": "lila=" + str(tmp_path / "h" / "tree" / "display" / "lila-poses")})
    assert h["face_of"]("lila-listen") == "lila", "never on the house"
    assert h["_lila_host_key"]("lila-listen") is True


def test_an_intake_into_the_worn_library_is_seen_without_a_restart(tmp_path):
    worn = _lila_library(tmp_path / "lib")
    ns = _block(tmp_path, worn)
    assert ns["face_of"]("lila-wake") == "lila"
    inv = json.loads((worn / "inventory.json").read_text())
    inv["clips"]["lila-wake"] = {"file": "clip-lila-wake.mp4"}
    (worn / "inventory.json").write_text(json.dumps(inv, indent=1))
    ns["_WORN"]["at"] = 0.0    # past the one-second re-read
    assert ns["face_of"]("lila-wake") is None


def test_the_kill_switch_is_ledgered():
    rows = json.loads((ROOT / "config" / "owed-verdicts.json").read_text(encoding="utf-8"))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    names = json.dumps(rows)
    assert "CRISSA_WORN_LIBRARY_WINS" in names
