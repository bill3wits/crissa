"""The phone pack and its sheet (the User 2026-10-16: "start the phone pack and see if you could serve the forward
loop as an app")."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import phone_pack as PP  # noqa: E402
import phone_sheet as PS  # noqa: E402


def test_the_box_holds_every_painted_region_with_even_sides():
    x, y, bw, bh = PP.union_box([(120, 700, 400, 900), (130, 690, 410, 905)], 720, 1280, pad=24)
    assert (x, y) == (96, 666) and x + bw >= 410 + 24 - 1 and y + bh >= 905 + 24 - 1
    assert bw % 2 == 0 and bh % 2 == 0


def test_the_box_stays_in_the_frame():
    x, y, bw, bh = PP.union_box([(0, 0, 720, 1280)], 720, 1280)
    assert (x, y, bw, bh) == (0, 0, 720, 1280)


def test_nineteen_cells_fit_a_phone_decoder_beside_a_9x16_base():
    lay = PP.layout(19, 720, 1280, 402, 270)
    assert lay["w"] <= PP.MAX_W and lay["h"] == 1280
    cells = lay["cells"]
    assert len(cells) == 19 and len(set(cells)) == 19
    assert all(cx >= 720 and cx + 402 <= lay["w"] and cy + 270 <= lay["h"] for cx, cy in cells)


def test_a_big_box_grows_the_video_down_not_past_the_decoder():
    lay = PP.layout(19, 720, 1280, 488, 348)      # Crissa's listen loop, 2026-10-16: 3 rows were 4136 px wide
    assert lay["w"] <= PP.MAX_W and lay["h"] <= PP.MAX_W and lay["rows"] * lay["cols"] >= 19


def test_a_manifest_checks_and_a_broken_one_says_why():
    lay = PP.layout(3, 720, 1280, 400, 260)
    m = PP.manifest("loop", ["rest", "MBP", "OH"], 10, 24.0, (720, 1280), (100, 600, 400, 260), lay, "loop.mp4")
    assert PP.check(m) == []
    m["idle"] = "EE"
    assert any("idle" in b for b in PP.check(m))
    m["idle"], m["box"] = "rest", [500, 600, 400, 260]
    assert any("box" in b for b in PP.check(m))


def _payloads(classes, air_at=1000.0, lead=0.02, hop=1 / 48.0, per=5):
    out = []
    env = [0.0] * 10 + [0.5 + 0.4 * math.sin(i / 3.0) for i in range(len(classes) - 20)] + [0.0] * 10
    for k in range(0, len(classes), per):
        out.append({"air_at": air_at, "hop_ms": hop * 1000, "pts0": air_at + k * hop - lead,
                    "rms": env[k:k + per], "visemes": [PS.CLASSES.index(c) for c in classes[k:k + per]]})
    return out, env


def test_the_sheet_lands_on_the_wavs_clock_and_keeps_its_lead():
    hop, lead, pre = 1 / 48.0, 0.02, 0.5       # the WAV has 0.5 s before air_at
    classes = ["rest"] * 10 + ["MBP", "MBP", "AH", "AH", "AH", "EE", "EE"] * 6 + ["rest"] * 10
    pl, env = _payloads(classes, lead=lead, hop=hop)
    sr = 4800
    n = int(sr * hop)
    wav = [0.0] * int(pre * sr)
    for r in env:
        wav += [r if i % 2 else -r for i in range(n)]
    sh = PS.sheet(pl, wav, sr, lead_s=lead)
    assert abs(sh["t0"] - (pre - lead)) <= hop + 1e-6, sh["t0"]
    assert sh["classes"][:10] == ["rest"] * 10 and sh["classes"][10] == "MBP"
    assert PS.class_at(sh, sh["t0"] + 10.5 * hop) == "MBP"
    assert PS.class_at(sh, -1.0) == "rest" and PS.class_at(sh, 99.0) == "rest"


def test_the_server_serves_only_inside_its_roots(tmp_path):
    spec = importlib.util.spec_from_file_location("phone_app_server", ROOT / "scripts" / "phone-app-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    (tmp_path / "a.json").write_text("{}")
    assert m._inside(tmp_path, "a.json") == (tmp_path / "a.json").resolve()
    assert m._inside(tmp_path, "../etc/passwd").name == "__nope__"


def test_a_line_takes_the_packs_own_tune_and_asks_no_conductor():
    d = {"sheet_lead_ms": -62.5, "min_hold": 3, "cons_hold": 4, "sent_pad": 4, "word_pad": 4, "pause_words": 0.0,
         "timing_lead_ms": 0.0}
    e = PS.speak_env(d, sync_zero_ms=83.333)
    assert e["HOUSE_MOUTH_LEAD_SRC"] == "explicit" and e["HOUSE_MOUTH_SHEET_LEAD_MS"] == "21"
    assert (e["HOUSE_MOUTH_MIN_HOLD"], e["HOUSE_MOUTH_CONS_HOLD"], e["HOUSE_MOUTH_SENT_PAD"]) == ("3", "4", "4")


ROWS = {
    "listen": {"role": "loop", "start": "up", "end": "up", "rack": "v01"},
    "scroll": {"role": "loop", "start": "down", "end": "down", "rack": "v01"},
    "wake": {"role": "slide", "start": "down", "end": "up", "rack": "v01"},
    "settle": {"role": "slide", "start": "up", "end": "down", "rack": "v01"},
    "multitask": {"role": "flourish", "start": "down", "end": "down", "rack": "v01"},
    "forward-1": {"role": "oneshot", "rack": "harvest"},
}


def test_the_show_walks_the_hubs_and_leaves_the_harvest_out():
    sh = PP.show(ROWS, set(ROWS), "listen")
    assert sh["talk"] == "listen" and sh["talk_hub"] == "up"
    assert set(sh["clips"]) == {"listen", "scroll", "wake", "settle", "multitask"}
    assert PP.check_show(sh) == []


def test_a_hub_with_no_way_back_to_talk_is_refused():
    rows = {k: v for k, v in ROWS.items() if k != "wake"}
    assert any("no way back" in b for b in PP.check_show(PP.show(rows, set(rows), "listen")))


def test_a_film_pack_with_no_mouths_checks():
    m = {"schema": PP.SCHEMA, "video_wh": [720, 1280], "base": [0, 0, 720, 1280], "box": [0, 0, 0, 0], "cells": {},
         "idle": None}
    assert PP.check(m) == []


def test_a_talking_flourish_stays_inside_one_rank_of_tiles(monkeypatch):
    """The User 2026-10-18: a talking flourish must stay looking good - glance-2 turns to 43 deg, no tile is filmed near
    that, so it ships as film; hair (16.4) stays painted. config/talk-flourish-limits.json is the one rule."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("phone_pack_cli", ROOT / "scripts" / "phone-pack.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    lim = {"max_yaw": 16.0, "tolerance_deg": 0.5, "allow_rank_switch": False, "ranks": {"30": {"good_deg": [18, 32]}}}
    glance = {"frames": [{"yaw": -3.0}, {"yaw": -28.0}, {"yaw": -43.2}]}
    hair = {"frames": [{"yaw": -1.0}, {"yaw": -16.4}]}
    assert "43.2" in m.over_talk_limits(glance, lim) and m.over_talk_limits(hair, lim) == ""
    assert "43.2" in m.over_talk_limits(glance, dict(lim, allow_rank_switch=True))          # past every rank
    assert m.over_talk_limits({"frames": [{"yaw": -28.0}]}, dict(lim, allow_rank_switch=True)) == ""
    monkeypatch.setenv("HOUSE_PHONE_TALK_LIMITS", "0")
    assert m.over_talk_limits(glance, lim) == ""
    import json
    cfg = json.loads((ROOT / "config" / "talk-flourish-limits.json").read_text())
    assert cfg["max_yaw"] == 17.0 and cfg["allow_rank_switch"] is False
