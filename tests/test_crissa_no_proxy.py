"""A Mac's system proxy never carries a loopback call (the User 2026-10-04, the Mac scan).

urllib applies macOS's proxy settings to 127.0.0.1, so each root that talks to the player sets NO_PROXY on a Mac
before its first urlopen. The house is Linux: nothing changes there.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ["scripts/house-conductor.py", "scripts/house-speak.py", "scripts/crissa-ear.py", "scripts/crissa-mcp.py",
         "modules/voice/scripts/crissa-stt.py"]
GUARD = 'if sys.platform == "darwin" and os.environ.get("CRISSA_NO_PROXY", "1").strip() != "0":'


def _snippet(src: str) -> str:
    i = src.index(GUARD)
    return "\n".join(src[i:].splitlines()[:3]) + "\n"


@pytest.mark.parametrize("rel", ROOTS)
def test_each_root_sets_no_proxy_after_its_imports_and_before_any_call(rel):
    src = (ROOT / rel).read_text()
    assert src.count(GUARD) == 1
    snip = _snippet(src)
    assert '"NO_PROXY"' in snip and '"no_proxy"' in snip
    g = src.index(GUARD)
    assert src.index("\nimport os\n") < g and src.index("\nimport sys\n") < g
    if "urlopen(" in src:
        assert g < src.index("urlopen(")


def _run(plat, environ):
    src = (ROOT / ROOTS[0]).read_text()
    ns = {"sys": types.SimpleNamespace(platform=plat), "os": types.SimpleNamespace(environ=environ)}
    exec(_snippet(src), ns)
    return environ


def test_the_guard_on_a_mac_linux_and_with_the_kill():
    assert _run("darwin", {}) == {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    assert _run("darwin", {"NO_PROXY": "corp"})["NO_PROXY"] == "corp"
    assert _run("linux", {}) == {}
    assert _run("darwin", {"CRISSA_NO_PROXY": "0"}) == {"CRISSA_NO_PROXY": "0"}
