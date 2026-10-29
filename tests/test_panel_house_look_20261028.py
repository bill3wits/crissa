"""The kit's Control Panel (display/crissa-panel.html, :8097) wears the house panel's format (the User 2026-10-28:
"Many items did not transfer over and I prefer using house version. The shared version should be like house but
organized to accommodate the flexible installation on any system"): the crown, the master lamp beside the Graphics
card block, the three lanes, the Units field in sections, the tape down the right. What this computer lacks is drawn
greyed with its reason (a lane with no camera, a Graphics card block with no GPU, an add-on slot nothing fills), never
hidden and never red. The log keys say what they watch (caps.lane_units, the house's data-monitors idea)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
MARKUP = PAGE.split("<script>", 1)[0]
HOUSE = (ROOT / "display" / "voiceonly-panel.html").read_text(encoding="utf-8")


def test_the_page_has_the_house_format():
    for piece in ('<header class="crown">', '<span class="wordmark" id="title">', 'class="master-lamp" id="masterLamp"',
                  'class="occupancy" id="gpu"', '<div class="legend">Graphics card<i></i>', '<div class="lanes">',
                  '<div class="lane" id="camera">', '<div class="lane" id="ears">', '<div class="lane" id="security">',
                  'id="svcRow"', '<div class="pane" id="logPane">', '<span class="who" id="tape">'):
        assert piece in MARKUP, piece
    # the kit's own keys and panes stay, in the house's clothes
    for kid in ("bHouse", "bAgent", "bWall", "bWallHere", "bConnect", "gpuKeys", "bSecure", "bSecurity", "bVoice", "bAec",
                "bDevMode", "scriptDrawer", "lineBox", "lampTest", "bRefresh", "regPane", "powerPane", "bLogFold",
                "bEarlier", "bFollow", "bWhole", "bRestart", "bRun", "bOff", "bStart", "bStop", "bVoiceKey", "svcWhy"):
        assert 'id="%s"' % kid in MARKUP, kid


def test_the_lanes_and_the_gpu_block_grey_out_with_the_reason_never_hide():
    body = PAGE.split("function paintLanes(){", 1)[1].split("\n/* THE AVATAR", 1)[0]
    assert 'cam.classList.add("inhibited")' in body and "feats.camera" in body and "facts.camera" in body
    gpu = PAGE.split("function paintGpu(){", 1)[1].split("\nfunction paintServices", 1)[0]
    assert 'blk.classList.toggle("inhibited", !g)' in gpu and "gpu_modes" in gpu
    assert ".lane.inhibited" in MARKUP and ".occupancy.inhibited" in MARKUP
    # an add-on slot nothing fills is the greyed placeholder, not a key
    assert ".unit.ph{cursor:default" in MARKUP and "function placeholderTile" in PAGE


def test_the_log_keys_say_what_they_watch():
    for lane in ("camera", "voice", "security"):
        assert 'data-log="%s"' % lane in MARKUP, lane
    assert ">FaceID log<" in MARKUP and 'id="tipFaceId" data-lead="' in MARKUP
    tips = PAGE.split("function paintMonitorTips(){", 1)[1].split("\n/* THE LANES", 1)[0]
    assert "CAPS.lane_units" in tips and "Monitors:" in tips


def test_the_house_page_and_the_kit_page_share_one_key_look():
    # the same keycap, pip, tile and master rules word for word, so a change to the look is made in both
    for rule in ("button.key.lit{" if False else ".key.lit{", ".pip.ok{background:var(--lamp-g); box-shadow:0 0 6px rgba(99,192,131,.6)}",
                 ".master-word{", ".unit.parked{opacity:.52}"):
        assert rule in MARKUP, rule
    assert ".pip.ok{background:var(--lamp-g); box-shadow:0 0 6px rgba(99,192,131,.6)}" in HOUSE
    assert "@font-face{font-family:'Barlow Condensed'" in MARKUP and "@font-face{font-family:'IBM Plex Mono'" in MARKUP


def test_the_page_spells_no_house_word():
    sys_path = str(ROOT / "lib")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    spec = importlib.util.spec_from_file_location("zx_look", ROOT / "scripts" / "crissa-export.py")
    zx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zx)
    assert zx.scrub_text(PAGE) == {}


def test_caps_carry_the_lane_writers(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    spec = importlib.util.spec_from_file_location("zp_look", ROOT / "scripts" / "crissa-panel-server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    monkeypatch.setattr(srv, "proc_view", lambda: {"tiles": [{"unit": "proc:ear"}, {"unit": "proc:tts"}, {"unit": "proc:player"}]})
    assert srv.lane_units() == {"camera": [], "voice": ["proc:ear", "proc:tts"], "security": []}
    monkeypatch.setattr(srv, "proc_view", lambda: (_ for _ in ()).throw(RuntimeError("no table")))
    assert srv.lane_units() == {"camera": [], "voice": [], "security": []}
    assert '"lane_units": lane_units()' in (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
