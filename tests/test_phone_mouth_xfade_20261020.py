"""A touch of smoothing on the phone (the User 2026-10-20): a change of mouth blends over one semiframe, as the desk
Wall ships it since round 23 (config/crissa-mouth.json HOUSE_MOUTH_XFADE_MS 20.833). Both mouths come from beside the
same decoded frame and land in the same box, so the blend is in time only: nothing moves where her mouth sits."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "display" / "phone-app" / "app.js").read_text()


def test_the_blend_is_one_semiframe_like_the_desk():
    assert 'let XFADE_MS = num("xfade", 1000 / 48);' in JS and "XFADE_MS = r.xfade_ms;" in JS
    desk = json.loads((ROOT / "config" / "crissa-mouth.json").read_text())["env"]["HOUSE_MOUTH_XFADE_MS"]
    assert abs(float(desk) - 1000 / 48) < 0.001


def test_both_mouths_land_in_the_same_box():
    body = JS.split("function paintMouth(", 1)[1].split("\n}\n", 1)[0]
    assert "const [bx, by, bw, bh] = m.box;" in body and "ctx.drawImage(patch, bx, by);" in body
    assert body.count("drawImage(v, ") == 1                       # the cell comes from the frame on the glass now
    assert "ctx.globalAlpha = 1;" in body


def test_the_old_mouth_goes_under_the_new_one_only_while_it_blends():
    f = JS.split("function frame()", 1)[1]
    assert "if (a < 1) paintMouth(v, m, m.cells[mouthWas], 1);" in f
    assert "paintMouth(v, m, m.cells[cls], Math.max(0, a));" in f
    assert f.index("m.cells[mouthWas], 1)") < f.index("m.cells[cls], Math.max(0, a))")
    assert len(re.findall(r"classNow\(m\)", f)) == 1                # classNow ends a spent line: read it once a frame


def test_the_server_sends_the_blend_and_its_kill(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("phone_server_xfade", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.delenv("CRISSA_PHONE_XFADE", raising=False)
    assert m._xfade_ms() == round(1000 / 48, 3)
    monkeypatch.setenv("CRISSA_PHONE_XFADE", "0")
    assert m._xfade_ms() == 0.0
    src = (ROOT / "scripts" / "phone-app-server.py").read_text()
    assert '"xfade_ms": _xfade_ms()' in src
