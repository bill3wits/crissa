"""A pack's Composer button links out to the Crissa store (the User 2026-10-07: "the Composer button now links
out to store.zoeyos.dev. that needs to be wired in repo").

The house keeps its own Composer (config/house-machine.json composer_url, house_nav's COMPOSER); a pack, which
carries neither, opens https://store.zoeyos.dev; CRISSA_COMPOSER_URL overrides both. The player's root `/` never
goes to the store: in a pack it keeps opening the Wall page or Connect.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_home  # noqa: E402

STORE = "https://store.zoeyos.dev"
COND = (ROOT / "scripts" / "house-conductor.py").read_text()


def _fn(name: str, **globs):
    tree = ast.parse(COND)
    ns: dict = dict(globs)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in (name, "root_location"):
            exec(compile(ast.Module(body=[n], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns[name]


def test_composer_url_is_the_store_unless_the_house_or_the_user_says_otherwise(tmp_path):
    none = tmp_path / "absent.json"
    assert crissa_home.COMPOSER_STORE == STORE
    assert crissa_home.composer_url(env={}, paths=none) == STORE + "/store/"           # a pack: the catalog (10-12)
    assert crissa_home.composer_url(env={"CRISSA_STORE_LINK": "0"}, paths=none) == STORE   # the kill: the front door
    house = tmp_path / "house-machine.json"
    house.write_text(json.dumps({"composer_url": "http://127.0.0.1:8199/"}))
    assert crissa_home.composer_url(env={}, paths=house) == "http://127.0.0.1:8199/"   # the house keeps its own
    mine = {"CRISSA_COMPOSER_URL": "https://example.org/my-composer"}
    assert crissa_home.composer_url(env=mine, paths=house) == "https://example.org/my-composer"
    assert crissa_home.composer_url(env=mine, paths=none) == "https://example.org/my-composer"
    assert crissa_home.composer_url(env={"CRISSA_COMPOSER_URL": "  "}, paths=none) == STORE + "/store/"


def test_the_house_config_still_names_its_own_composer():
    if not (ROOT / "config" / "house-machine.json").exists():
        pytest.skip("the house's own config never ships in a pack")
    cfg = json.loads((ROOT / "config" / "house-machine.json").read_text())
    assert cfg["composer_url"] == "http://127.0.0.1:8199/"


def test_composer_goes_to_the_store_and_root_stays_home_in_a_pack():
    cl = _fn("composer_location")
    rl = _fn("root_location")
    # a pack: house_nav is not shipped, so COMPOSER_URL is "" and the link is the store
    assert cl("", STORE, True) == STORE
    assert cl("", STORE, True, True) == STORE
    assert rl("", True) == "/connect" and rl("", True, True) == "/wall" and rl("", False) == "/frame.jpg"
    # no link at all: /composer goes where / goes (never to itself)
    assert cl("", "", True) == "/connect" and cl("", "", False, True) == "/wall"
    # the house: both go to its own Composer, as always
    assert cl("http://composer.home.arpa:8199", STORE, True) == "http://composer.home.arpa:8199/"
    assert rl("http://composer.home.arpa:8199", True) == "http://composer.home.arpa:8199/"


def test_the_handler_lets_composer_through_the_gate_and_keeps_root_on_root_location():
    assert 'COMPOSER_LINK = crissa_home.composer_url()' in COND
    assert 'COMPOSER_PATHS = ("/composer", "/composer.html", "/composer-classic", "/composer-classic.html")' in COND
    assert "head.startswith(AUTHORING_GET) and head not in COMPOSER_PATHS" in COND
    i = COND.index('"/composer-classic.html",\n        ):')
    block = COND[i:i + 900]
    assert "composer_location(COMPOSER_URL, COMPOSER_LINK, on, WALL_PAGE)" in block
    assert "if head in COMPOSER_PATHS else root_location(COMPOSER_URL, on, WALL_PAGE)" in block
    # the pack branch never gives `/` an address: COMPOSER_URL stays "" there
    imp = COND[COND.index("except ImportError:\n    DOCTOR_BASE"):][:200]
    assert 'COMPOSER_URL = ""' in imp


def test_the_chip_and_the_wall_key_use_the_same_address():
    chip = (ROOT / "lib" / "house_chip.py").read_text()
    assert '"url": crissa_home.composer_url(),' in chip
    wall = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'if name == "composer":\n                    url = crissa_home.leave_url()' in wall
    # the house's own address is rewritten to the link out in a pack's lib (not in its tests): either spelling
    assert re.search(r'open_url\("[^"]+" if crissa_home\.is_house\(\) else crissa_home\.leave_url\(\)\)', wall)


def test_the_pack_build_defaults_to_the_store(monkeypatch, tmp_path):
    if not (ROOT / "scripts" / "crissa-export.py").exists():
        pytest.skip("the pack builder is source-side only")
    monkeypatch.delenv("CRISSA_COMPOSER_URL", raising=False)
    spec = importlib.util.spec_from_file_location("zx_store", ROOT / "scripts" / "crissa-export.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    assert ex.COMPOSER_LINK_OUT == STORE + "/store"
    out = tmp_path / "pack"
    (out / "lib").mkdir(parents=True)
    (out / "lib" / "house_wall_dock.py").write_text(
        '    "composer": ("c", "Composer", "▶", "http://composer.home.arpa:8199/"),\n')
    (out / "lib" / "house_wall_ascii.py").write_text(
        '    "ft": "http://127.0.0.1:8199/",\n    open_url("http://127.0.0.1:8199/")\n')
    ex.rewrite_public(out)
    assert '"▶", "https://store.zoeyos.dev/store/"' in (out / "lib" / "house_wall_dock.py").read_text()
    wall = (out / "lib" / "house_wall_ascii.py").read_text()
    assert wall.count("https://store.zoeyos.dev/store/") == 2 and "8199" not in wall
    src = (ROOT / "scripts" / "crissa-export.py").read_text()
    assert "Its button links out to the Crissa store: `{composer_url}`" in src
