"""Crissa's test bootstrap.

lib/ is on the path (pytest.ini's pythonpath); scripts/ are loaded by file path, since their names carry hyphens.
Tests marked live or disruptive (they need running services, speak, or move a camera) are skipped unless asked for
with --live / --disruptive. No test writes a real speaking gate or posts to a running Wall: both addresses point into
a scratch folder, set before any test module loads.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

_GATE_DIR = Path(tempfile.mkdtemp(prefix="crissa-test-gate-"))
os.environ.setdefault("HOUSE_TTS_GATE", str(_GATE_DIR / "tts-gate.json"))
os.environ.setdefault("VO_MOUTH_URL", "http://127.0.0.1:9/v1/mouth")

# The tests' recorded times are the reference machine's (US Eastern), so on another zone (GitHub's runners are UTC) a
# night or a clock line read an hour or a day off. Pinned here so a clone tests the same anywhere; CRISSA_TEST_TZ moves it.
os.environ["TZ"] = os.environ.get("CRISSA_TEST_TZ", "America/New_York")
time.tzset()


# Tests that pass on the reference machine but not in a code-only clone (they read her library, which is a release
# download, or a file only the reference install carries). Skipped by name, with the reason; --no-ci-skip runs them.
CI_SKIP = Path(__file__).with_name("ci-skip.txt")


def _ci_skip() -> set[str]:
    try:
        return {ln.strip() for ln in CI_SKIP.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--no-ci-skip", action="store_true", default=False,
                     help="also run the tests listed in tests/ci-skip.txt (with her library in place)")
    parser.addoption("--live", action="store_true", default=False, help="run tests marked live (need running services)")
    parser.addoption("--disruptive", action="store_true", default=False,
                     help="run tests marked disruptive (speak / camera / restart)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_live = pytest.mark.skip(reason="needs --live (running services)")
    skip_dis = pytest.mark.skip(reason="needs --disruptive")
    listed = set() if config.getoption("--no-ci-skip") else _ci_skip()
    skip_ci = pytest.mark.skip(reason="tests/ci-skip.txt: needs her library (scripts/get-library.sh) or a file only the "
                                      "reference install carries")
    for item in items:
        if item.nodeid in listed:
            item.add_marker(skip_ci)
        if "live" in item.keywords and not config.getoption("--live"):
            item.add_marker(skip_live)
        if "disruptive" in item.keywords and not config.getoption("--disruptive"):
            item.add_marker(skip_dis)
