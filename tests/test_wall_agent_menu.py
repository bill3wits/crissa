"""The ▶ Composer key on the Wall, the Control Panel and the frame drops the agent libraries the Composer lists (the
User 2026-10-16: "add to menu button on wall up top as well")."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("display/voiceonly-home.html", "display/voiceonly-panel.html", "display/voiceonly-frame.html")


def _block(rel):
    s = (ROOT / rel).read_text(encoding="utf-8")
    m = re.search(r'<!-- THE AGENT MENU on the Composer key.*?</script>\n', s, re.S)
    assert m, rel
    return s, m.group(0)


def test_one_block_on_every_surface_outside_the_rail():
    blocks = [_block(p) for p in PAGES]
    assert len({b for _, b in blocks}) == 1, "the same block on all three"
    for s, b in blocks:
        rail = re.search(r'<div id="rail"[^>]*>.*?</div>', s, re.S).group(0)
        assert "agentMenu" not in rail, "outside the rail: the toolbar tests read the rail to its first </div>"


def test_it_reads_the_composers_roster_and_falls_back_to_the_plain_link():
    _, b = _block(PAGES[0])
    assert '"faces"' in b and 'getAttribute("href")' in b and "catch(function(){ state = 0; })" in b
    assert "has-agents" in b, "the key's tip hides only once the menu has rows"


def test_the_composer_serves_the_roster_to_local_hosts_only():
    sh = (ROOT / "scripts" / "clip-doctor-shell.py").read_text(encoding="utf-8")
    i = sh.index("    def _faces(self)")
    body = sh[i:sh.index("    def _json(", i)]
    assert 'host.endswith(".home.arpa")' in body and "404" in body and "Access-Control-Allow-Origin" in body
    assert 'if head == "/faces":' in sh
