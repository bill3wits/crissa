"""Round 36 trial cut (2026-10-20): `import crissa_menu` inside do_GET's /phone-card branch made the name LOCAL to the
whole handler, so the menu route below it raised UnboundLocalError and the player never booted in a pack. The tests
that slice one route out of the conductor cannot see that; this one reads the handlers whole."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "house-conductor.py"


def _names(nodes):
    return {(a.asname or a.name).split(".")[0] for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names}


def test_no_handler_imports_a_name_the_module_already_has():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    top = _names(ast.walk(tree)) - _names(n for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                                          for n in ast.walk(f))
    top |= _names(tree.body)
    bad = []
    for f in ast.walk(tree):
        if isinstance(f, ast.FunctionDef) and f.name in ("do_GET", "do_POST", "do_HEAD", "do_PUT", "do_DELETE"):
            hit = _names(ast.walk(f)) & top
            if hit:
                bad.append((f.name, f.lineno, sorted(hit)))
    assert not bad, "a local import shadows a module name in a handler (UnboundLocalError on every route): %s" % bad
