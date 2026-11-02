"""Init v2's text dialog (the User 2026-10-04). Runs only under a private X server: never on the User's screen.

    xvfb-run -a python3 -m pytest tests/test_init_v2_dialog.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

pytestmark = pytest.mark.skipif(not os.environ.get("INIT_V2_XVFB"), reason="needs INIT_V2_XVFB=1 under xvfb-run")


@pytest.fixture
def root():
    import tkinter as tk
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


def test_ok_hands_back_the_typed_values(root):
    import init_v2_dialog as D
    got = {}
    top = D.ask(root, "Your name", [("text", "What should Crissa call you?", "", False)], got.update)
    top._init2["entries"]["text"].insert(0, "  Alex ")
    top._init2["ok"]()
    assert got == {"text": "Alex"} and D._OPEN["top"] is None


def test_escape_cancels_and_a_secret_is_masked(root):
    import init_v2_dialog as D
    got, cancelled = {}, []
    top = D.ask(root, "Connect", [("url", "Address", "http://x/v1", False), ("key", "Key", "", True)], got.update,
                on_cancel=lambda: cancelled.append(1))
    assert top._init2["entries"]["key"].cget("show") == "•"
    assert top._init2["entries"]["url"].get() == "http://x/v1"
    top._init2["cancel"]()
    assert got == {} and cancelled == [1]


def test_one_dialog_at_a_time(root):
    import init_v2_dialog as D
    a = D.ask(root, "A", [("t", "t", "", False)], lambda v: None)
    b = D.ask(root, "B", [("t", "t", "", False)], lambda v: None)
    assert a is b
    a._init2["cancel"]()


def test_the_box_keeps_itself_above_the_wall_and_is_never_its_transient(root):
    """X (the User 2026-10-10, .101: a stray click sent the Language box behind the desk Wall's black plate)."""
    import init_v2_dialog as D
    top = D.ask(root, "Your language", [("language", "Which language?", "en", False)], lambda v: None)
    assert top.transient() == ""                         # a transient follows its parent's (desktop) layer
    seen = []                                            # Xvfb has no window manager to read -topmost back from
    real_attr, real_lift, real_after = top.attributes, top.lift, top.after
    top.attributes = lambda *a: seen.append(("attributes",) + a)
    top.lift = lambda *a: seen.append(("lift",))
    top.after = lambda ms, fn: seen.append(("after", ms))
    top._keep_above()                                    # the tick: topmost again, lifted, and the next tick
    assert seen == [("attributes", "-topmost", True), ("lift",), ("after", D.KEEP_ABOVE_MS)]
    top.attributes, top.lift, top.after = real_attr, real_lift, real_after
    top._init2["cancel"]()
