"""The import closure of a Crissa checkout: every Python file that ships imports, at load time, only the standard
library, another file that ships, or a package its requirements.txt names. Imports inside a function, a try or an if
are the optional engines (installed on first use into their own environments) and are not counted.

Runs on a fresh clone (no house file needed): python3 -m pytest tests/test_clone_imports.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "config" / "crissa-packages.json").is_file():   # the source tree: test_crissa_packages checks it there
    pytest.skip("not a Crissa checkout (the source tree has its own closure test)", allow_module_level=True)
# requirement name -> the module it installs, where they differ
DIST_MODULE = {"opencv-python-headless": "cv2", "opencv-python": "cv2", "pillow": "PIL",
               "websocket-client": "websocket", "webrtcvad-wheels": "webrtcvad", "pyyaml": "yaml"}


def _shipped() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT).parts
        if not p.is_file() or rel[0] in ("tests", ".git", ".venv", "venv") or "__pycache__" in rel:
            continue
        if p.suffix == ".py":
            out.append(p)
        elif not p.suffix and rel[0] == "scripts":
            try:
                head = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            except OSError:
                continue
            if head.startswith("#!") and "python" in head:
                out.append(p)
    return out


def _requirements() -> set[str]:
    names: set[str] = set()
    for req in [ROOT / "requirements.txt", *sorted(ROOT.glob("modules/*/requirements.txt"))]:
        if not req.is_file():
            continue
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            dist = re.split(r"[<>=!~\[; ]", line, 1)[0].strip().lower()
            names.add(DIST_MODULE.get(dist, dist.replace("-", "_")))
    return names


def _top_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """(top-level module, line) for imports that run when the file loads."""
    got: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            got += [(a.name.split(".")[0], node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            got.append((node.module.split(".")[0], node.lineno))
    return got


def _local(name: str, f: Path) -> bool:
    places = [ROOT / "lib", f.parent, f.parent.parent, *[d for d in ROOT.glob("modules/*") if d.is_dir()]]
    return any((d / f"{name}.py").is_file() or (d / name / "__init__.py").is_file() or (d / name).is_dir()
               for d in places)


SHIPPED = _shipped()


def test_there_is_code_to_check():
    assert any(p.parent.name == "lib" for p in SHIPPED) and any(p.parent.name == "scripts" for p in SHIPPED)


@pytest.mark.parametrize("path", SHIPPED, ids=[str(p.relative_to(ROOT)) for p in SHIPPED])
def test_every_load_time_import_resolves(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    reqs = _requirements()
    missing = [f"{name} (line {line})" for name, line in _top_imports(tree)
               if name != "__future__" and name not in sys.stdlib_module_names
               and name not in reqs and not _local(name, path)]
    assert not missing, f"{path.relative_to(ROOT)} imports what does not ship: {missing}"
