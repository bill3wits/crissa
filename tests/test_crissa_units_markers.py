"""Exec-marker path boundary: /opt/crissa must not claim /opt/crissa-dev."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import crissa_units as zu  # noqa: E402


def test_exec_marker_needs_a_path_boundary():
    rules = zu.Rules(exec_markers=("/opt/crissa",))
    assert zu.install_reason("user", "x.service", {"ExecStart": "/opt/crissa/bin/player"}, {}, rules)
    assert not zu.install_reason("user", "x.service", {"ExecStart": "/opt/crissa-dev/bin/player"}, {}, rules)


def test_prefix_markers_that_end_with_dash_still_match():
    rules = zu.Rules(exec_markers=("/opt/apps/svc-",))
    assert zu.install_reason(
        "user", "y.service", {"ExecStart": "/opt/apps/svc-alpha/run"}, {}, rules
    )
