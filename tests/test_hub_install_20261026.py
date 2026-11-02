"""A finished hub becomes its own avatar (the User 2026-10-26: "the store loop from portrait and backdrop to new loop in
library and a switch to pick which loop"): lib/crissa_hub_install copies a finished library whole into
<state home>/libraries/<key>/ with a pack.json, and the Avatar picker lists it as a store hub."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_avatar as A  # noqa: E402
import crissa_hub_install as H  # noqa: E402


def _finished(d: Path) -> Path:
    (d / "visemes" / "phoneme-map" / "tiles").mkdir(parents=True)
    (d / "inventory.json").write_text(json.dumps({"clips": {"listen-screen": {}}}), encoding="utf-8")
    (d / "visemes" / "phoneme-map" / "map.json").write_text("{}", encoding="utf-8")
    (d / "visemes" / "phoneme-map" / "tiles" / "AH.png").write_bytes(b"png")
    (d / "clip-listen-screen.mp4").write_bytes(b"film")
    return d


def test_a_finished_hub_lands_whole_and_the_picker_lists_it_as_a_store_hub(tmp_path):
    src = _finished(tmp_path / "order-9" / "lib")
    home = tmp_path / "home"
    out = H.install(src, "longhair", home, order="order-9", name="Longhair", now=0)
    dest = home / "libraries" / "longhair"
    assert out["ok"] and out["files"] == 4 and not out["replaced"]
    assert (dest / "clip-listen-screen.mp4").read_bytes() == b"film"
    assert (dest / "visemes" / "phoneme-map" / "tiles" / "AH.png").is_file()
    r = json.loads((dest / "pack.json").read_text())
    assert r["source"] == "store" and r["order"] == "order-9" and r["key"] == "longhair" and r["schema"] == H.SCHEMA
    libs = {l["key"]: l for l in A.libraries(root=tmp_path / "no-tree", home=home)}
    assert libs["longhair"]["source"] == "store" and libs["longhair"]["built"] and libs["longhair"]["label"] == "Longhair"
    assert not [p for p in (home / "libraries").iterdir() if p.name.startswith(".")]   # no part / old folder left


def test_a_folder_without_an_inventory_is_refused(tmp_path):
    src = tmp_path / "lib"; src.mkdir()
    with pytest.raises(H.Refused, match="inventory.json"):
        H.install(src, "x", tmp_path / "home")
    assert not (tmp_path / "home" / "libraries" / "x").exists()


def test_a_symlink_anywhere_in_the_library_is_refused_before_anything_is_written(tmp_path):
    src = _finished(tmp_path / "lib")
    (src / "visemes" / "leak.png").symlink_to("/etc/hostname")
    with pytest.raises(H.Refused, match="symlink"):
        H.install(src, "x", tmp_path / "home")
    assert not (tmp_path / "home" / "libraries").exists()
    link = tmp_path / "link"; link.symlink_to(_finished(tmp_path / "real"))
    with pytest.raises(H.Refused, match="symlink"):
        H.install(link, "y", tmp_path / "home")


def test_an_installed_key_is_never_overwritten_without_replace(tmp_path):
    home = tmp_path / "home"
    H.install(_finished(tmp_path / "a"), "hub", home)
    b = _finished(tmp_path / "b"); (b / "clip-listen-screen.mp4").write_bytes(b"new film")
    with pytest.raises(H.Refused, match="already installed"):
        H.install(b, "hub", home)
    assert (home / "libraries" / "hub" / "clip-listen-screen.mp4").read_bytes() == b"film"
    out = H.install(b, "hub", home, replace=True)
    assert out["replaced"] and (home / "libraries" / "hub" / "clip-listen-screen.mp4").read_bytes() == b"new film"
    assert not [p for p in (home / "libraries").iterdir() if p.name.startswith(".")]


def test_a_bad_key_and_an_overlapping_source_are_refused(tmp_path):
    src = _finished(tmp_path / "lib")
    for bad in ("", "Has Space", "../up", "-lead", "x" * 65):
        with pytest.raises(H.Refused):
            H.install(src, bad, tmp_path / "home")
    home = tmp_path / "home"
    inside = _finished(home / "libraries" / "mine")
    with pytest.raises(H.Refused, match="overlap"):
        H.install(inside, "copy", home)


def test_a_receipt_the_library_already_carries_is_kept_beside_the_new_one(tmp_path):
    src = _finished(tmp_path / "lib")
    (src / "pack.json").write_text('{"schema": "zoeyos-loop-pack/1"}', encoding="utf-8")
    H.install(src, "hub", tmp_path / "home")
    d = tmp_path / "home" / "libraries" / "hub"
    assert json.loads((d / "pack.json").read_text())["previous"] == "pack.json.orig"
    assert json.loads((d / "pack.json.orig").read_text())["schema"] == "zoeyos-loop-pack/1"


def test_off_by_default_and_never_on_the_house(tmp_path, monkeypatch):
    monkeypatch.setattr(H.crissa_home, "is_house", lambda: False)
    assert not H.enabled({})
    assert H.enabled({"CRISSA_HUB_INSTALL": "1"})
    monkeypatch.setattr(H.crissa_home, "is_house", lambda: True)
    assert not H.enabled({"CRISSA_HUB_INSTALL": "1"})


def test_the_cli_is_off_unless_switched_on(tmp_path):
    src = _finished(tmp_path / "lib")
    env = {"PATH": "/usr/bin:/bin", "CRISSA_HOME": str(tmp_path / "home")}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "hub-install.py"), str(src), "--key", "k"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and "off" in r.stdout
