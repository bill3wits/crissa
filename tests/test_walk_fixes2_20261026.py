"""The second set from the User's walks of 1.0.1 (2026-10-26): the ear's canceller never sits on another canceller's
sink and is released when its output is dead against the microphone; her window's right edge meets the sheet; a pack
has no scripted greeting; the IDs page's heard bar is relative to the room's floor."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "modules" / "ear", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from crissa_ear import aec  # noqa: E402
import crissa_face  # noqa: E402

MODS = ("536870916\tmodule-echo-cancel\taec_method=webrtc source_master=mic_a sink_master=hw_sink source_name=echocancel_source sink_name=echocancel_sink\n"
        "536870918\tmodule-echo-cancel\taec_method=webrtc source_master=shure sink_master=echocancel_sink source_name=crissa_aec_source sink_name=crissa_aec_sink\n")


def _pactl(*args, **kw):
    if args[:3] == ("list", "short", "modules"):
        return 0, MODS, ""
    return 0, "", ""


def test_the_canceller_follows_another_cancellers_sink_to_the_hardware():
    assert aec.hardware_sink("echocancel_sink", _pactl) == "hw_sink"
    assert aec.hardware_sink("hw_sink", _pactl) == "hw_sink"
    assert aec.hardware_sink("", _pactl) == ""


def test_a_dead_output_is_named_and_a_live_or_unmeasured_one_is_kept(monkeypatch):
    assert aec.dead_output("shure", levels=(-59.0, -82.0)).startswith("the canceller's output is dead")
    assert aec.dead_output("shure", levels=(-59.0, -62.0)) == ""
    assert aec.dead_output("shure", levels=(None, -82.0)) == ""
    assert aec.dead_output("shure", levels=(-90.0, -95.0)) == "", "a silent microphone proves nothing"
    monkeypatch.setenv("CRISSA_EAR_AEC_DEAD_CHECK", "0")
    assert aec.dead_output("shure", levels=(-59.0, -82.0)) == ""


def test_her_window_sits_against_the_sheets_left_edge(monkeypatch):
    box = [0.207, 0.191, 0.46, 0.618]              # the layout's box: the left 60 %, the sheet from 0.667
    b = crissa_face.beside_bounds(box, (3440, 1440))
    assert b["x"] + b["w"] == round((0.207 + 0.46) * 3440)
    monkeypatch.setenv("CRISSA_FACE_BESIDE_CENTRED", "1")
    c = crissa_face.beside_bounds(box, (3440, 1440))
    assert c["x"] < b["x"] and c["w"] == b["w"]


def test_the_heard_bar_is_the_rooms_floor_with_the_known_answer_as_the_fast_path():
    src = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert "OP_HEARD_OVER_FLOOR" in src and "S.op.floor" in src
    assert "OP_HEARD_OVER_FLOOR * (S.op.floor" in src and "(S.op.floorN || 0) >= 2" in src
    assert "Number(lv.peak || 0) >= OP_HEARD_PEAK" in src
