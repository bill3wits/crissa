"""Round 26: the ear decodes again (.101 10-16 09:50). Round 23 pasted `asr_warm_wait_s()` at column 0 inside
`class Transcriber`, so `transcribe_array/_file/_pcm16` became dead defs nested in that function: every utterance
raised AttributeError, speech_chunks stayed 0 and she never woke by voice. The pack tests and both acceptances missed
it. Two guards: the Transcriber has its decode verbs, and no shipped module has a function holding `self` methods
(the shape that paste leaves behind)."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_ear import pipeline as P  # noqa: E402


def test_the_transcriber_has_its_decode_verbs():
    for name in ("transcribe_array", "transcribe_file", "transcribe_pcm16", "warm"):
        assert callable(getattr(P.Transcriber, name, None)), name


def _orphans(path: Path):
    """A module-level function whose body goes on with `def x(self…)` after a statement that always leaves it (a
    return or a try whose every branch returns): unreachable methods, the class tail a paste swallowed."""
    tree = ast.parse(path.read_text(), str(path))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        left = False
        for stmt in node.body:
            if left and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.args.args \
                    and stmt.args.args[0].arg == "self":
                yield f"{path.relative_to(ROOT)}:{stmt.lineno} {node.name}() holds {stmt.name}(self) after it returns"
            if isinstance(stmt, (ast.Return, ast.Raise)) or (
                    isinstance(stmt, ast.Try) and stmt.handlers and _leaves(stmt.body)
                    and all(_leaves(h.body) for h in stmt.handlers)):
                left = True


def _leaves(body) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise))


def test_no_module_function_swallowed_a_class_tail():
    found = []
    for base in ("modules", "lib"):
        for path in sorted((ROOT / base).rglob("*.py")):
            try:
                found += list(_orphans(path))
            except SyntaxError:
                continue
    assert found == [], "\n".join(found)
