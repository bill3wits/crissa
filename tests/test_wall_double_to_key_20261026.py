"""A double-click's second press reaches the key it lands on (2026-10-26, the User: Secure double press did nothing).

Tk delivers the second press of a double to <Double-Button-1> ONLY when both are bound; the Wall's on_double (a pack's
"her picture back in the middle", 10-16) returned None on a key, so Secure heard one single. Proved on a real Tk canvas
under a private Xvfb, then pinned on the Wall's source.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import tkinter as tk
root = tk.Tk(); cv = tk.Canvas(root, width=50, height=50); cv.pack(); root.update()
got = []
def on_click(ev): got.append("click"); return "break"
def on_double(ev): return on_click(ev) if FORWARD else None
cv.bind("<Button-1>", on_click); cv.bind("<Double-Button-1>", on_double)
for t in (1000, 1100):
    cv.event_generate("<ButtonPress-1>", x=10, y=10, time=t); cv.event_generate("<ButtonRelease-1>", x=10, y=10, time=t + 20)
root.update(); print(len(got)); root.destroy()
'''


def _run(forward: bool) -> int:
    if not shutil.which("xvfb-run"):
        pytest.skip("no xvfb-run: never on the User's display")
    out = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", "FORWARD=%s\n" % forward + PROBE],
                         capture_output=True, text=True, timeout=60)
    return int(out.stdout.strip().splitlines()[-1])


def test_tk_hands_the_second_press_to_the_double_binding_only():
    assert _run(False) == 1                 # the bug: two presses, one click
    assert _run(True) == 2                  # forwarded: both presses reach the key


def test_the_wall_forwards_a_double_on_a_key_and_keeps_its_kill():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    body = src[src.index("    def on_double(ev):"): src.index('    cv.bind("<Double-Button-1>", on_double)')]
    assert re.search(r"HOUSE_WALL_DOUBLE_TO_KEY.*\n\s+return on_click\(ev\)", body)
