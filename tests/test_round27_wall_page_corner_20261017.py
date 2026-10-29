"""Round 27, the Wall page in Brave (.101 2026-10-16): the toolbar sat on the PAUSED and HOLD chips, the chips ran the
full width (the no-360 sentence stretched the corner), the Secure dot floated alone (the page set `hidden` on it and
`#visitDot.chip{display:grid}` beat it), and the log was hard to read over her face."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = (ROOT / "display/voiceonly-home.html").read_text()   # one page since the fold (2026-10-28)


def test_the_corner_keeps_its_width_and_clears_the_toolbar():
    assert re.search(r"\.modewrap\{max-width:17em;margin-top:44px\}", KIT)
    assert ".sec-row .sec-note{flex:1 1 100%;white-space:normal" in KIT


def test_a_hidden_secure_dot_is_not_drawn():
    assert "#visitDot[hidden]{display:none !important}" in KIT
    assert 'el.hidden=secHidden(h);' in KIT


def test_the_roll_reads_over_her_face():
    roll = KIT.split("\n#roll{", 1)[1].split("}", 1)[0]          # in #roll's own block since the fold (2026-10-28)
    assert "text-shadow:0 0 6px var(--ground)" in roll


def test_one_page_carries_the_corner():
    # the fold (2026-10-28, "Crissa Wall + Krew = house"): the Wall page is one file; the house wears round 27's corner too
    house = (ROOT / "display/voiceonly-home.html").read_text()
    assert "max-width:17em;margin-top:44px" in house
    assert not (ROOT / "crissa/overlay/display/voiceonly-home.html").exists()
