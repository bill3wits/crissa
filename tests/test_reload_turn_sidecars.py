"""/v1/reload drops a cached turn sidecar (the User 2026-09-28): the turn-fix painter caches
<stem>.mouth.turn.json by file name, and reload_now only dropped names ending ".mouth.json", so a re-run turn
file never reached a running bench or the wall. reload_now is run for real, lifted out of the conductor
(the module itself boots a conductor), with its two collaborators stubbed."""
from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _reload_now():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "reload_now")
    ns = {"reset_tile_caches": lambda: None, "action": lambda *a, **k: None}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns["reload_now"]


def _ph(keys):
    lib = SimpleNamespace(_lock=threading.Lock(), _tracks={k: [1] for k in keys}, _pl_mt={"x": 1}, _pl_tiles={"t": 1})
    return SimpleNamespace(lib=lib, _take_k={"k": 1})


def test_reload_drops_turn_and_mouth_sidecars_and_keeps_head_tracks():
    keys = ["clip-flourish-glance.mouth.turn.json", "clip-flourish-glance.mouth.json", "pl:clip-work.playlist.json",
            "hf:map", "hf:clip-flourish-glance"]
    ph = _ph(keys)
    n = _reload_now()(ph)
    assert set(ph.lib._tracks) == {"hf:clip-flourish-glance"}           # head tracks stay (restart law)
    assert n == 4 and ph.lib._pl_mt == {} and ph.lib._pl_tiles == {}


def test_a_turn_name_is_not_a_mouth_name():
    # the bug in one line: why the old rule missed it
    assert not "clip-x.mouth.turn.json".endswith(".mouth.json")
