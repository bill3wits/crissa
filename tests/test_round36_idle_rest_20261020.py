"""Round 36, 2026-10-20 (.101 round 35 FAIL 1): idle rest stayed off only until a clip's own dials set it (flourish-glance
and both bun-wobbles carry idle_rest true, applied with honour_env=False). A set HOUSE_MOUTH_IDLE_REST now wins over
every dial set, and the export writes the kit's value into every shipped mouth-dials*.json."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _ns():
    """idle_rest_pinned + apply_dials and the two tables they read, run in a namespace of their own."""
    want_f = {"idle_rest_pinned", "apply_dials"}
    want_a = {"DIAL_ENV", "DIAL_CHOICES"}
    parts = []
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in want_f:
            parts.append(ast.get_source_segment(SRC, node))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = [t.id for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                     if isinstance(t, ast.Name)]
            if set(names) & want_a:
                parts.append(ast.get_source_segment(SRC, node))
    ns = {"os": os, "MOUTH_IDLE_REST": False}
    exec("\n\n".join(parts), ns)  # noqa: S102 - the conductor's own source
    return ns


def test_a_set_env_wins_over_a_clip_dial_set():
    ns = _ns()
    env = {"HOUSE_MOUTH_IDLE_REST": "0"}
    ns["apply_dials"]({"idle_rest": True, "scale": 1.0}, env=env, honour_env=False)   # what apply_preset does
    assert ns["MOUTH_IDLE_REST"] is False, "a clip's idle_rest true turned the rest tile back on"
    assert ns["idle_rest_pinned"](env) is False


def test_unset_env_the_dials_decide_as_before():
    ns = _ns()
    ns["apply_dials"]({"idle_rest": True}, env={}, honour_env=False)
    assert ns["MOUTH_IDLE_REST"] is True
    assert ns["idle_rest_pinned"]({}) is None


def test_the_kill_puts_round_35_back():
    ns = _ns()
    env = {"HOUSE_MOUTH_IDLE_REST": "0", "HOUSE_MOUTH_IDLE_REST_ENV_WINS": "0"}
    ns["apply_dials"]({"idle_rest": True}, env=env, honour_env=False)
    assert ns["MOUTH_IDLE_REST"] is True


def test_the_tune_route_and_one_switch_for_idle_rests_inside_a_line_and_paused():
    # the tune route honours the pin; the paint's three places read the one global
    assert 'if "idle_rest" in body and idle_rest_pinned() is None' in SRC
    assert '"idle: filmed mouth (%s)" % ("idle_rest off" if not MOUTH_IDLE_REST else "paused")' in SRC
    assert 'if klass == "rest" and not MOUTH_IDLE_REST:' in SRC
    assert SRC.count("MOUTH_IDLE_REST = ") == 3, "a new writer of the switch must honour idle_rest_pinned"


def _export():
    spec = importlib.util.spec_from_file_location("zx_r36", ROOT / "scripts" / "crissa-export.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["zx_r36"] = m
    spec.loader.exec_module(m)
    return m


def test_the_export_writes_the_kits_value_into_every_dials_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_EXPORT_KIT_IDLE_REST", raising=False)
    x = _export()
    for face in ("crissa-poses", "lila-poses"):
        d = tmp_path / "display" / face
        d.mkdir(parents=True)
        for name in ("mouth-dials.json", "mouth-dials.clips.json"):
            src = ROOT / "display" / face / name
            if src.is_file():
                (d / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    said = x.kit_dials_idle_rest(tmp_path)
    assert "flourish" not in said or "clips" in said
    for f in tmp_path.glob("display/*/mouth-dials*.json"):
        assert '"idle_rest": true' not in f.read_text(encoding="utf-8"), f
    # the house's own files are untouched
    assert '"idle_rest": true' in (ROOT / "display" / "crissa-poses" / "mouth-dials.clips.json").read_text()


def test_the_export_kill(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_EXPORT_KIT_IDLE_REST", "0")
    x = _export()
    assert x.kit_dials_idle_rest(tmp_path) == ""
