"""THE CHIN PIN STRETCHES A STILL MOUTH ONLY (the User 2026-10-30: "analyze that pin and see if it was aimed imprecisely.
Correct that"; nose_u / nose_v back to 0 in every lock). The stretch aims a tile's chin at her chin + the class baseline
taken on the clip's rest-pose frames - a model of a filmed jaw that does not move. On a clip whose lips part it added the
baseline on top of a chin her jaw had already dropped, so every tile landed low on its OWN source frame (kit bench at
nose 0: rel2.0 upper lip +3.48 px / chin +2.95, Aa's chin +15.4; Lite Friday +0.93 / +4.25). With no stretch there, her
own tiles land on themselves (rel2.0 0.01 / -0.44, Lite Friday 0.16 / 0.21, Lite Bash -0.02 / 0.08); a still loop keeps
the stretch byte for byte (store robust/chin-offset-20261030). Wiring pinned by its source; the talk test executed."""
import re
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


def _method(name: str, **glb):
    """one method's body, executed on its own (the conductor is not imported: it starts a server)"""
    body = SRC.split("    def %s(" % name, 1)[1].split("\n    def ", 1)[0]
    lines = ["def %s(" % name + body.split("\n", 1)[0]] + [ln[4:] for ln in body.split("\n")[1:]]
    ns = dict({"np": np, "Path": Path, "POSE_DIR": "/nonexistent", "MOUTH_CHIN_TALK_OPEN": 0.005,
               "paint_wh": lambda: (720, 1280), "unpack_head": lambda h: h}, **glb)
    exec("\n".join(lines), ns)          # noqa: S102 - our own source
    return ns[name]


def _clip(apertures, s=100.0, row=None):
    """a fake library: one clip whose lipmarks part by `apertures` (px at s), in the painter's own picture size"""
    frames = [{"up": [0.0, 0.0], "lo": [0.0, a]} for a in apertures]
    row = {"file": "clip-k.mp4", "pose_class": "back"} if row is None else row
    lib = types.SimpleNamespace(rows={"k": row}, lipmarks=lambda stem: {"wh": [720, 1280], "frames": frames},
                                head=lambda key, i: {"s": s})
    return types.SimpleNamespace(key="k", lib=lib, _loop_ver=lambda: 1)


def test_a_harvest_talks_only_when_her_lips_part_past_twice_open_floor():
    f = _method("_clip_talks")
    assert f(_clip([0.1, 0.24, 0.52])) is False          # lips never part (still listen loops peak 0.0016-0.0052)
    assert f(_clip([0.1, 1.0, 14.0])) is True            # a talking take (0.14)
    assert f(_clip([])) is None                          # not measurable: the stretch stays


def test_a_clip_that_does_not_speak_by_its_role_never_talks(tmp_path):
    """the role half (6a 10-30): a loop or flourish whose lips wobble past the line stays a still clip - Bash's
    listen-screen (the double chin this pin was built for) peaks at 0.0077, Lite Bash's fl-hair-clump-up at 0.040"""
    loop = {"file": "clip-k.mp4", "role": "loop"}
    assert _method("_clip_talks", POSE_DIR=str(tmp_path))(_clip([0.2, 4.0, 7.7], row=loop)) is False
    assert _method("_clip_talks", POSE_DIR=str(tmp_path))(_clip([0.2, 40.0], row={"file": "clip-k.mp4", "role": "flourish"})) is False
    (tmp_path / "visemes").mkdir()
    (tmp_path / "visemes" / "clip-k.timeline.json").write_text("{}")    # a words sidecar: it speaks
    assert _method("_clip_talks", POSE_DIR=str(tmp_path))(_clip([0.2, 40.0], row={"file": "clip-k.mp4", "role": "flourish"})) is True


def test_lips_apart_is_the_pickers_own_open_floor():
    pick = (ROOT / "scripts" / "pose-bow-same-spot.py").read_text()
    m = re.search(r"^OPEN_FLOOR = ([0-9.]+)$", pick, re.M)
    c = re.search(r'MOUTH_CHIN_TALK_OPEN = float\(os.environ.get\("HOUSE_MOUTH_CHIN_TALK_OPEN", "([0-9.]+)"\)\)', SRC)
    assert m and c and float(m.group(1)) == float(c.group(1))


def test_a_talking_clip_is_not_stretched_and_a_still_one_is_as_before():
    st = SRC.split("    def _chin_stretch(", 1)[1].split("\n    def ", 1)[0]
    gate = st.index("if MOUTH_CHIN_STILL_ONLY and self._clip_talks():")
    assert gate < st.index("ta = self.lib.tile_anchors(rel)")         # before any measurement: no stretch at all
    assert "return None, {\"how\": \"a clip that talks: her own tile's jaw stands (no stretch)\"}" in st
    # the still path is the old code, untouched: the same target and clamp
    assert "want = float(np.dot(her - tn, a)) + float(base_below)" in st
    assert "f = max(MOUTH_CHIN_PIN_LO, min(MOUTH_CHIN_PIN_HI, want / d))" in st


def test_on_by_default_with_a_kill_and_every_cache_knows_it():
    assert 'MOUTH_CHIN_STILL_ONLY = os.environ.get("HOUSE_MOUTH_CHIN_STILL_ONLY", "1")' in SRC
    # A DIAL THAT CHANGES PAINT IS IN EVERY KEY: the cheeks maps' pin_key names both settings; the finished-patch store
    # keys on every plain UPPERCASE module setting (_patch_dial_names) and on the code's own print
    assert '("still-only", MOUTH_CHIN_TALK_OPEN) if MOUTH_CHIN_STILL_ONLY else ()' in SRC
    names = SRC.split("def _patch_dial_names() -> list:", 1)[1].split("\ndef ", 1)[0]
    assert "k.isupper()" in names and "plain(v)" in names
    assert "mouth_patch.code_print()" in SRC.split("    def _patch_key(", 1)[1].split("\n    def ", 1)[0]
