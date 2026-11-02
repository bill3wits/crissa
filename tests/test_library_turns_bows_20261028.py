"""A library's own turns and bows reach the kit's player (2026-10-28, cut 10).

Crissa desk v2 carries turn-fill.env (its 45 = v2-listen-screen) and bow maps (clip-*.bow.json); the Composer bench has
worn both since 10-27, the kit's player wore neither: the shipped mouth defaults name the Crissa face's profile-45, so
every turned frame painted nothing. The library's keys sit over the shipped defaults at plan AND on a skin switch, the
environment wins over both, and a library's keys leave with it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_avatar as A  # noqa: E402

TURN = "HOUSE_MOUTH_TURN_FILL=1\nHOUSE_MOUTH_LIPS_ALL=1\nHOUSE_TURN_LIB_45=v2-listen-screen\nHOUSE_TURN_FRONT_MAX=12\n"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib(d: Path, turns=False, bows=False, size=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "inventory.json").write_text(json.dumps({"size": size} if size else {}), encoding="utf-8")
    (d / "visemes" / "phoneme-map").mkdir(parents=True, exist_ok=True)
    (d / "visemes" / "phoneme-map" / "map.json").write_text("{}", encoding="utf-8")
    (d / "pack.json").write_text("{}", encoding="utf-8")
    if turns:
        (d / "turn-fill.env").write_text(TURN, encoding="utf-8")
    if bows:
        (d / "clip-v2-fl-bow.bow.json").write_text("{}", encoding="utf-8")
    return d


def test_turn_keys_and_the_bow_switch_come_from_the_library(tmp_path):
    d = _lib(tmp_path / "v2", turns=True, bows=True, size=[1280, 720])
    env = A.player_env(d, env={})
    assert env["HOUSE_TURN_LIB_45"] == "v2-listen-screen" and env["HOUSE_TURN_FRONT_MAX"] == "12"
    assert env["HOUSE_MOUTH_TURN_FILL"] == "1" and env["HOUSE_MOUTH_BOW"] == "1"
    assert "HOUSE_MOUTH_LIPS_ALL" not in env                 # only the turn keys, as the Composer bench takes them
    assert env["HOUSE_FRAME_FROM_LIBRARY"] == "1"
    assert A.player_env(_lib(tmp_path / "plain"), env={}) == {}


def test_the_kills(tmp_path):
    d = _lib(tmp_path / "v2", turns=True, bows=True)
    env = A.player_env(d, env={"CRISSA_LIBRARY_TURN_ENV": "0", "CRISSA_LIBRARY_BOW": "0"})
    assert not any(A.is_library_key(k) and k != "HOUSE_FRAME_FROM_LIBRARY" for k in env)


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_library_wins_over_the_shipped_mouth_defaults_at_plan_and_on_a_switch(tmp_path, monkeypatch):
    z = _load("crissa_launcher_turns", ROOT / "scripts" / "crissa.py")
    home = tmp_path / "home"
    _lib(home / "libraries" / "v2", turns=True, bows=True)
    _lib(home / "libraries" / "friday")
    import crissa_home
    monkeypatch.setattr(crissa_home, "HOME", home)
    shipped = z.mouth_env("v2")
    assert shipped.get("HOUSE_TURN_LIB_45") == "profile-45"   # the shipped default the library must beat
    A.choose("v2", ROOT, home)
    env = {k["name"]: k for k in z.plan(_args(), env_in={})["children"]}["player"]["env"]
    assert env["HOUSE_TURN_LIB_45"] == "v2-listen-screen" and env["HOUSE_MOUTH_BOW"] == "1"
    env = {k["name"]: k for k in z.plan(_args(), env_in={"HOUSE_TURN_LIB_45": "mine"})["children"]}["player"]["env"]
    assert "HOUSE_TURN_LIB_45" not in env or env["HOUSE_TURN_LIB_45"] == "mine"   # the environment wins

    class Table:
        data = {"launcher": {}}

        def write(self):
            pass
    g = SimpleNamespace(specs={"player": (["p"], {"HOUSE_POSE_DIR": "old", "X": "1"}, 8100)}, table=Table())
    z.apply_avatar(g, home, env={})
    penv = g.specs["player"][1]
    assert penv["HOUSE_TURN_LIB_45"] == "v2-listen-screen" and penv["HOUSE_TURN_FRONT_MAX"] == "12"
    assert penv["HOUSE_MOUTH_BOW"] == "1" and penv["X"] == "1"
    A.choose("friday", ROOT, home)                            # its keys leave with it; the shipped defaults come back
    z.apply_avatar(g, home, env={})
    penv = g.specs["player"][1]
    assert "HOUSE_MOUTH_BOW" not in penv and penv["HOUSE_TURN_LIB_45"] == "profile-45"
    assert penv["HOUSE_TURN_FRONT_MAX"] == shipped["HOUSE_TURN_FRONT_MAX"]
