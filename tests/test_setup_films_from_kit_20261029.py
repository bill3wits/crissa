"""Setup's films whatever face she wears (the User 2026-10-29, the first-time walk of 1.0.1: with Crissa desk v2 worn the
player refused Lila's Setup films, so Setup stalled and Crissa sat on the Wall before her entrance)."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _fns(pose: Path, is_house: bool = False) -> dict:
    ns = {"json": json, "os": os, "Path": Path, "ROOT": ROOT, "POSE_DIR": pose, "IS_HOUSE": is_house}
    code = ""
    for name in ("_setup_library", "with_setup_films"):
        code += "def %s(" % name + SRC.split("def %s(" % name, 1)[1].split("\ndef ", 1)[0] + "\n"
    exec(code, ns)
    return ns


def _worn(tmp_path) -> tuple[Path, dict]:
    pose = tmp_path / "libraries" / "crissa-desk-v2"
    pose.mkdir(parents=True)
    inv = {"schema": "character_kit/v1", "hubs": {"down": "hub-bezel.png"},
           "clips": {"v2-listen-screen": {"file": "clip-v2-listen-screen.mp4", "role": "loop", "start": "down", "end": "down"}}}
    (pose / "inventory.json").write_text(json.dumps(inv))
    return pose, inv


def test_a_worn_library_without_lilas_films_borrows_them_and_her_hub(tmp_path):
    pose, inv = _worn(tmp_path)
    out = _fns(pose)["with_setup_films"](inv)
    base = ROOT / "display" / "crissa-poses"
    want = {k for k in json.loads((base / "inventory.json").read_text())["clips"] if k.startswith("lila-")}
    assert want and want <= set(out["clips"])
    row = out["clips"]["lila-reg-listen"]
    assert Path(row["file"]).is_absolute() and Path(row["file"]).is_file()
    assert "lila_listen" in out["hubs"] and Path(out["hubs"]["lila_listen"]).is_file()
    assert out["hubs"]["down"] == "hub-bezel.png"                        # the worn library's own hub is untouched
    assert "lila-reg-listen" not in inv["clips"]                          # the worn inventory itself is not changed
    assert not any(k.startswith(("crissa-", "crissa-")) and k not in inv["clips"] for k in out["clips"])   # only Lila's


def test_the_house_and_the_switch_borrow_nothing(tmp_path, monkeypatch):
    pose, inv = _worn(tmp_path)
    assert _fns(pose, is_house=True)["with_setup_films"](inv) is inv
    monkeypatch.setenv("CRISSA_SETUP_FILMS_FROM_KIT", "0")
    assert _fns(pose)["with_setup_films"](inv) is inv


def test_the_kits_own_library_borrows_nothing():
    base = ROOT / "display" / "crissa-poses"
    inv = json.loads((base / "inventory.json").read_text())
    assert _fns(base)["with_setup_films"](inv) is inv


def test_every_inventory_read_of_the_player_goes_through_it():
    assert SRC.count("_inv_load_file(") == 1 and "def inv_load(path=None):" in SRC
    assert '(inv_load().get("hubs") or {}).items()' in SRC                  # the hub plates know Lila's hub too
