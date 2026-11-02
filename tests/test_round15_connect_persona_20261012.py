"""Round 15, Connect's persona key (.101's fresh install, ASK-20261012-1425-from-101, last paragraph; 2026-10-12).

"Use her stock persona" looked like a button that did nothing (the User: "looks like a button ... it does nothing, but
maybe label the text. Seems broken though"): on a fresh setup the box already holds her stock text, so a click
changed nothing. Now a label says what is in use and "Reset to her stock persona" shows only when the box differs.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "display" / "crissa-connect.html").read_text(encoding="utf-8")


def test_the_button_is_a_reset_hidden_until_the_box_differs():
    assert ">Use her stock persona<" not in PAGE                 # the old button that did nothing
    assert '<button id="stock" type="button" hidden>Reset to her stock persona</button>' in PAGE
    assert '<span class="msg" id="persona-use" aria-live="polite"></span>' in PAGE
    assert "personaState();" in PAGE.split("async function load()")[1].split("showOffers(")[0]


def _run(states):
    """personaState from the page, run in node with stand-in elements, for each (box, touched, none, source)."""
    js = PAGE.split("function sameText(a, b)")[1].split('$("stock").addEventListener')[0]
    js = "function sameText(a, b)" + js
    prog = """
const els = {prompt: {value: ""}, no_prompt: {checked: false}, "persona-use": {textContent: ""}, stock: {hidden: true}};
function $(id) { return els[id]; }
let personaTouched = false, stockText = "You are Crissa, an avatar.\\nVoice: warm.", src = "stock";
function persona() { return {source: src}; }
%s
const out = [];
for (const [box, touched, none, s, note] of %s) {
  els.prompt.value = box; personaTouched = touched; els.no_prompt.checked = none; src = s;
  personaState(note || undefined);
  out.push([els["persona-use"].textContent, els.stock.hidden]);
}
console.log(JSON.stringify(out));
""" % (js, json.dumps(states))
    node = shutil.which("node") or str(Path.home() / ".local" / "bin" / "node")
    r = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not (shutil.which("node") or (Path.home() / ".local" / "bin" / "node").exists()),
                    reason="node is not on this machine")
def test_the_label_says_what_is_in_use_and_the_reset_shows_only_when_needed():
    stock = "You are Crissa, an avatar.\nVoice: warm."
    got = _run([
        [stock, False, False, "stock", ""],                       # a fresh setup: her stock text
        ["You are Crissa, an avatar.   Voice: warm.  ", False, False, "stock", ""],   # spacing does not count
        ["You are a pirate.", False, False, "user", ""],          # a saved text of his own
        ["You are a pirate. Arr.", True, False, "user", ""],       # being edited
        [stock, True, False, "user", "Her stock persona is back in the box - Save to use it."],   # after a reset
        [stock, False, True, "none", ""],                         # None ticked
    ])
    assert got[0] == ["In use: her stock persona.", True]
    assert got[1] == ["In use: her stock persona.", True]
    assert got[2] == ["In use: your own text.", False]
    assert got[3] == ["In use: your own text (Save to keep it).", False]
    assert got[4] == ["Her stock persona is back in the box - Save to use it.", True]
    assert got[5][0].startswith("In use: none") and got[5][1] is True
