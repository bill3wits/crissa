"""Round 28 (.101 2026-10-17, ASK 20: "the wall buttons not fixed"): PAUSED and HOLD still ran the corner's 17em (about
650 px) because the wrap stretched them and `#commsHold.chip` (id + class) beat the round 27 rule. Measured in a
headless page at 3440x1440 with the no-360 sentence shown: 665/665 px before, 279/279 after, for PAUSED, LISTENING and
SPEAKING alike; the Security row and its sentence share the chips' right edge."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = (ROOT / "display/voiceonly-home.html").read_text()   # one page since the fold (2026-10-28)


def test_the_chips_keep_their_own_width():
    assert ".modewrap{align-items:flex-end}" in KIT
    rule = ".modewrap #modechip,.modewrap #commsHold.chip{align-self:flex-end;box-sizing:border-box;width:auto;min-width:11.5em}"
    assert rule in KIT
    assert KIT.index(rule) > KIT.index(".modewrap{max-width:17em;margin-top:44px}")


def test_the_security_row_shares_the_right_edge():
    assert ".modewrap .sec-row{align-self:stretch;justify-content:flex-end;text-align:right}" in KIT


def test_one_page_carries_the_corner():
    # the fold (2026-10-28, "Crissa Wall + Krew = house"): the Wall page is one file; the house wears round 28's corner too
    assert "min-width:11.5em" in (ROOT / "display/voiceonly-home.html").read_text()
    assert not (ROOT / "crissa/overlay/display/voiceonly-home.html").exists()
