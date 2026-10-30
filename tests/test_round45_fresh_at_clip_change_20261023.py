"""Round 45 (.101 round 44: "2 of the last 130 pictures drawn fresh" an hour after the build, at a clip's first play).
Found: the desk Wall drew a frame about the face point /v1/state named (polled every 0.5 s), so for up to half a second
after a clip change the new clip was drawn about the old clip's point - a picture the build never made, too different for
a region, drawn whole (the house's own laps: 1.08 fresh a clip change, 0.1 a repeat lap); in a pack that picture then
replaced the build's entry on disk for good. Now every frame carries its own clip's X-Face-Point (kill
HOUSE_CONDUCTOR_FACE_HEADER=0) and the Wall draws about it (kill HOUSE_WALL_FRAME_FP_BY_CLIP=0); an entry drawn about
another point is replaced (kill HOUSE_WALL_ASCII_DISK_REPLACE_POINT=0); each fresh picture says why
(HOUSE_WALL_FRESH_WHY); and the build draws what plays first, first: her entrance, then listen and work
(kill HOUSE_WALL_ASCII_PREFILL_ENTRANCE_FIRST=0)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import pytest  # noqa: E402

COND = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
WALL = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")


def test_every_frame_names_its_own_clips_face_point():
    assert COND.count("face_point_value(ph.lib, f_clip)") == 2              # /frame.jpg and each /frame.mjpg part
    assert 'b"X-Face-Point: " + _fpv.encode("ascii")' in COND
    fn = COND.split("def face_point_value(", 1)[1].split("\ndef ", 1)[0]
    reply = COND.split("def clip_frame_reply(", 1)[1].split("\ndef ", 1)[0]
    assert '"%r,%r" % (float(fp[0]), float(fp[1]))' in fn and '"%r,%r" % (float(fp_[0]), float(fp_[1]))' in reply, \
        "the live frame's point is spelled as the build's"


def test_the_wall_draws_about_the_frames_own_clip():
    import house_wall_ascii as hw
    assert hw._face_point(b"0.517,0.549") == (0.517, 0.549) and hw._face_point(b"x") is None
    her = WALL.split("    def _her_frame(", 1)[1].split("\n    def ", 1)[0]
    assert "st.fp_by_clip.get(clip_key) if (FRAME_FP_BY_CLIP and clip_key) else None" in her
    assert "frame_clip[0] if isinstance(frame_clip, tuple) else None)" in WALL
    assert "st.fp_by_clip[_MJPG[\"clip\"][0]] = _MJPG[\"fp\"]" in WALL


@pytest.fixture
def disk(tmp_path, monkeypatch):
    for k, v in dict(HOUSE_WALL_ASCII_DISK="1", HOUSE_WALL_ASCII_DISK_DIR=str(tmp_path / "disk"),
                     HOUSE_WALL_ASCII_DISK_GB="1", HOUSE_WALL_ASCII_DISK_FLOOR_GB="0", HOUSE_WALL_FRESH_WHY="1",
                     HOUSE_WALL_ASCII_DISK_REPLACE="1", HOUSE_WALL_ASCII_DISK_REPLACE_POINT="1").items():
        monkeypatch.setenv(k, v)
    import cv2
    import numpy as np
    img = (np.random.RandomState(1).rand(704, 1296, 3) * 255).astype(np.uint8)
    cv2.circle(img, (650, 300), 150, (200, 180, 160), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def _cache(lines):
    import house_wall_ascii_render as R
    import wall_ascii_cache as C
    c = C.AsciiCache(log=lines.append)
    g = R.Geom(1280, 720, density=1.0)
    c.prepare(g)
    time.sleep(0.5)
    return c, g


def test_an_entry_drawn_about_another_point_is_replaced_and_said(disk):
    import wall_ascii_cache as C
    C._FW.update(t0=0.0, n=0, held=0, seen=set())
    built, stale = ("whole", 1.0, 0.51, 0.61), ("whole", 1.0, 0.517, 0.549)
    clip = ("work", 0, 120, "loop")
    lines: list = []
    c, g = _cache(lines)
    c.picture(disk, g, True, 0.0, None, None, None, frame=stale, clip=clip)    # the old clip's point, stored
    time.sleep(1.0)
    c2, g = _cache(lines)
    _p, how, _ = c2.picture(disk, g, True, 0.0, None, None, None, frame=built, clip=clip)
    assert how == "full"
    assert any("ascii fresh: work frame 0: key: face point" in ln for ln in lines), lines
    time.sleep(1.0)
    c3, g = _cache(lines)
    _p, how, _ = c3.picture(disk, g, True, 0.0, None, None, None, frame=built, clip=clip)
    assert how == "disk", "the right picture was stored back over the stale one"


def test_fresh_why_is_rate_limited_and_once_a_frame():
    import wall_ascii_cache as C
    C._FW.update(t0=0.0, n=0, held=0, seen=set())
    out: list = []
    env = {"HOUSE_WALL_FRESH_WHY": "1", "HOUSE_WALL_FRESH_WHY_PER_MIN": "2"}
    assert C.fresh_why(out.append, ("work", 1, 9, "loop"), "not built: x", env, now=100.0)
    assert not C.fresh_why(out.append, ("work", 1, 9, "loop"), "not built: x", env, now=100.1)   # once a frame
    assert C.fresh_why(out.append, ("work", 2, 9, "loop"), "not built: x", env, now=100.2)
    assert not C.fresh_why(out.append, ("work", 3, 9, "loop"), "not built: x", env, now=100.3)   # the cap
    assert C.fresh_why(out.append, ("work", 4, 9, "loop"), "not built: x", env, now=161.0)
    assert out[-1].endswith("(+1 not shown)")
    assert not C.fresh_why(out.append, ("work", 5, 9, "loop"), "x", {"HOUSE_WALL_FRESH_WHY": "0"}, now=300.0)


def test_the_build_draws_her_entrance_first(tmp_path, monkeypatch):
    import wall_ascii_disk as D
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(D, "entrance_keys", lambda: ["crissa-sit-hello", "crissa-walk-in"])
    (tmp_path / "display" / "crissa-poses").mkdir(parents=True)
    (tmp_path / "display" / "crissa-poses" / "inventory.json").write_text(json.dumps({"clips": {
        "listen": {"role": "loop"}, "work": {"role": "loop"}, "back-pangram-a": {"role": "harvest"},
        "crissa-walk-in": {"role": "oneshot"}, "crissa-sit-hello": {"role": "oneshot"}, "fl-chin": {"role": "flourish"}}}))
    order, loops = D.clip_order(tmp_path, {})
    assert order[:4] == ["crissa-sit-hello", "crissa-walk-in", "listen", "work"] and order.count("crissa-walk-in") == 1
    assert loops == {"listen", "work"}
    order, _ = D.clip_order(tmp_path, {}, env={"HOUSE_WALL_ASCII_PREFILL_ENTRANCE_FIRST": "0"})
    assert order[:2] == ["listen", "work"]
    src = (ROOT / "lib" / "wall_ascii_disk.py").read_text(encoding="utf-8")
    assert "listen_key = next((k for k in order if k in loops)" in src
