"""The close-up's "Yeah, {name}?" after a name-call from work names the User by the name Init kept (the User 2026-10-10).

The conductor spelled the house's own name into the line, so a pack spoke the exporter's rewrite of it ("Yeah, the
User?"). wake_greet_line reads user.json in the state home (Init's Voice ID name), else the mode file's user id, never
the generic "user", else the bare ack. The function is lifted out of scripts/house-conductor.py by its source (the
conductor needs cv2 and a display to import)."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def _fn(home: Path):
    tree = ast.parse(SRC)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "wake_greet_line")
    mod = ast.Module(body=[node], type_ignores=[])
    g = {"Path": Path, "json": json, "os": os, "crissa_home": SimpleNamespace(HOME=str(home))}
    exec(compile(mod, "wake_greet_line", "exec"), g)
    return g["wake_greet_line"]


def test_the_name_init_kept_is_the_one_she_says(tmp_path, monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_WAKE_ACK_NAMED", raising=False)
    monkeypatch.delenv("EAR_BRIDGE_BARE_WAKE_TEXT", raising=False)
    f = _fn(tmp_path)
    assert f() == "Yeah?", "no name anywhere: the bare ack"
    (tmp_path / "house-mode.json").write_text(json.dumps({"auth": {"user_id": "user"}}))
    assert f() == "Yeah?", "the generic id is not a name"
    (tmp_path / "house-mode.json").write_text(json.dumps({"auth": {"user_id": "sam"}}))
    assert f() == "Yeah, Sam?"
    (tmp_path / "user.json").write_text(json.dumps({"name": "Robin Q", "person_id": "robin", "role": "user"}))
    assert f() == "Yeah, Robin?", "Init's name wins, first word"
    monkeypatch.setenv("EAR_BRIDGE_WAKE_ACK_NAMED", "Hi {name}.")
    assert f() == "Hi Robin."
    (tmp_path / "user.json").write_text("{not json")
    (tmp_path / "house-mode.json").unlink()
    monkeypatch.setenv("EAR_BRIDGE_BARE_WAKE_TEXT", "Mm?")
    assert f() == "Mm?"
    assert _fn(tmp_path / "missing")() == "Mm?"


def test_the_greet_reads_the_line_and_spells_no_name():
    i = SRC.index("if self.seq.take_ft_greet(")
    block = SRC[i:i + 600]
    # the ear's word on THIS wake rides along since d65892c52 (round 4 BB: a guest's wake gets the bare ack)
    assert 'line = wake_greet_line(named=STATE.get("vocative_named"))' in block and ".replace(\"{name}\"," not in block


def test_the_ear_varies_its_wake_ack_at_random():
    """The User 2026-10-30: "Hey is good if we can vary it just random. With name too"; "random Yeah in there as well"."""
    import importlib.util, random
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "pick_wake_ack")
            or (isinstance(n, ast.Assign) and any(getattr(t_, "id", "") in ("WAKE_ACK_BARE", "WAKE_ACK_NAMED") for t_ in n.targets))]
    g: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "ear_bridge", "exec"), g)
    random.seed(3)
    bare = {g["pick_wake_ack"](g["WAKE_ACK_BARE"], "Yeah?") for _ in range(200)}
    assert bare == {"Hey.", "Hey there.", "Yeah?", "Hi."}, bare
    named = {g["pick_wake_ack"](g["WAKE_ACK_NAMED"], "Yeah, {name}?", "Sam") for _ in range(200)}
    assert named == {"Hey, Sam.", "Hey there, Sam.", "Yeah, Sam?", "Hi, Sam."}, named
    assert g["pick_wake_ack"]("Hey.", "Yeah?") == "Hey.", "one value = that value (the house's drop-in)"
    assert g["pick_wake_ack"]("", "Yeah?") == "Yeah?"
    assert 'pick_wake_ack(os.environ.get("EAR_BRIDGE_BARE_WAKE_TEXT", WAKE_ACK_BARE)' in src
    assert 'pick_wake_ack(os.environ.get("EAR_BRIDGE_WAKE_ACK_NAMED", WAKE_ACK_NAMED)' in src
