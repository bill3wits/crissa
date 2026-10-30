"""Grid-cell pins by angle + lip-landmark placement (the User 2026-09-29)."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import mouth_pins as MP  # noqa: E402


def test_rows_save_replace_and_interpolate(tmp_path):
    c = MP.cell_key("profile-45-r", "profile", "AH")
    MP.save_row(tmp_path, c, -20, 2, 0)
    MP.save_row(tmp_path, c, -40, 6, -4)
    MP.save_row(tmp_path, c, -40.3, 4, -2)          # within half a degree: replaces
    rows = MP.load(tmp_path)["cells"][c]["rows"]
    assert [r["yaw"] for r in rows] == [-40.3, -20.0]
    assert MP.pin_at(rows, -30.15) == (3.0, -1.0)   # halfway
    assert MP.pin_at(rows, -60) == (4.0, -2.0)      # held flat past the ends
    assert MP.pin_at(rows, 0) == (2.0, 0.0)
    assert MP.pin_at(None, -30) is None and MP.pin_at(rows, None) is None
    MP.drop_row(tmp_path, c, -20)
    assert [r["yaw"] for r in MP.load(tmp_path)["cells"][c]["rows"]] == [-40.3]
    MP.drop_row(tmp_path, c)
    assert c not in MP.load(tmp_path)["cells"]


def test_nudge_is_along_her_axes_at_the_reference_scale():
    d = MP.nudge_px(10, 0, [2, 0], [0, 5], 357)
    assert np.allclose(d, [10, 0])
    d = MP.nudge_px(0, 10, [0, 3], [-1, 0], 178.5)   # rotated axes, half scale
    assert np.allclose(d, [-5, 0])


def test_similarity_recovers_a_known_landing():
    src = np.array([[10.0, 20.0], [50.0, 22.0], [30.0, 25.0]])
    a = np.radians(12)
    R = 1.3 * np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    dst = src @ R.T + [100, 40]
    M = MP.similarity(src, dst)
    assert np.allclose(MP.apply(M, src), dst, atol=1e-6)


def test_lip_points_order_and_origin():
    m = {"cl": [100, 50], "cr": [140, 52], "up": [120, 48], "lo": [120, 56], "top": [0, 0], "bot": [0, 0]}
    P = MP.lip_points(m, origin=(100, 40))
    assert np.allclose(P, [[0, 10], [40, 12], [20, 12]])
    assert MP.lip_points(None) is None


def test_conductor_wiring_defaults_off_and_every_path_gets_the_pin():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_MOUTH_PIN_CELLS", "0")' in src
    assert 'os.environ.get("HOUSE_MOUTH_TURN_PLACE", "nose")' in src
    i = src.index("maps, Maff, dst, path, _pin_shift = self._lips_and_pins(")
    assert i < src.index("warped = cv2.remap(tile, mx, my", i)          # maps + affine after it
    assert "@ H" in src[src.index("H = warp_triad_3x3(src, dst, scale=MOUTH_SCALE)", i):][:400]  # triad too
    assert '"/v1/mouth-pins"' in src[src.index("AUTHORING_POST"):][:600]
    assert src.count('if head == "/v1/mouth-pins":') == 2


def test_the_page_saves_on_the_bench():
    page = (ROOT / "display" / "clip-doctor.html").read_text()
    assert 'bpost("/v1/mouth-pins"' in page and 'bget("/v1/mouth-pins")' in page


def test_face_mask_is_the_intersection_feathered():
    sq = np.array([[10, 10], [60, 10], [60, 60], [10, 60]], float)
    right = sq + [30, 0]                       # overlap x 40..60
    m = MP.face_mask((80, 100), [sq], [right], 1.5)
    assert m.shape == (80, 100) and m.dtype == np.float32
    assert m[35, 50] > 0.95                    # inside both
    assert m[35, 20] < 0.05 and m[35, 80] < 0.05   # inside one only
    assert m[5, 50] < 0.05                     # outside both
    assert MP.face_mask((10, 10), None, None, 1) is None
    g = MP.face_mask((80, 100), [sq + [10, 0]], [sq], 0.5, grow_px=4)
    assert g[35, 63] > 0.5 and m[35, 63] < 0.05    # HER outline grows past its edge
    t = MP.face_mask((80, 100), [sq], [sq + [10, 0]], 2.0, grow_px=4)
    assert t[35, 63] < 0.05                        # the TILE's never does: its own top stays out


def test_hull_points_origin():
    P = MP.hull_points({"hull": [[10, 10], [20, 10], [15, 20]]}, origin=(10, 10))
    assert np.allclose(P, [[0, 0], [10, 0], [5, 10]])
    assert MP.hull_points({"hull": [[1, 1]]}) is None


def test_lips_mask_wiring():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_MOUTH_LIPS_MASK", "1")' in src
    assert "cover_alpha=(jaw_on or _mask is not None)," in src
    assert 'if path == "lips" else None' in src


def test_outline_wins_over_hull():
    m = {"hull": [[0, 0], [10, 0], [5, 10]], "outline": [[1, 1], [9, 1], [5, 8], [5, 5]]}
    assert len(MP.hull_points(m)) == 4


def test_face_polys_join_the_lips_and_the_hole_stays_inside():
    m = {"outline": [[0, 0], [20, 0], [20, 20], [0, 20]], "lips_outer": [[18, 8], [30, 8], [30, 12], [18, 12]]}
    polys = MP.face_polys(m)
    assert len(polys) == 2
    a = MP.fill((40, 40), polys)
    assert a[10, 25] == 255 and a[30, 30] == 0      # the lips reach past the outline
    src = (ROOT / "lib" / "pose_mouth.py").read_text()
    assert "have = have & (np.asarray(hole_within) > 127)" in src
    assert "hole_within=(getattr(self, \"_lip_hole\", None)" in (ROOT / "scripts" / "house-conductor.py").read_text()


def test_similarity_with_a_fixed_scale_keeps_the_size():
    src = np.array([[10.0, 20.0], [50.0, 22.0], [30.0, 25.0]])
    dst = src * 1.6 + [100, 40]                     # corners say 1.6
    M = MP.similarity(src, dst, scale=1.1)          # the head says 1.1
    assert abs(np.hypot(M[0, 0], M[1, 0]) - 1.1) < 1e-9
    assert np.allclose(MP.apply(M, src).mean(0), dst.mean(0))   # still centred on her lips


def test_lips_scale_defaults_to_the_take_frame():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_MOUTH_LIPS_SCALE", "take")' in src
    assert 'return float(rec["s"]) * float((lm.get("wh") or [0])[0]) / paint_wh()[0]' in src
    assert '_ks, _kfrom = float(dst["s"]) / _ts, "take"' in src


def test_jaw_stretch_moves_only_along_her_face_axis():
    # her chin is 20% further from her lip line than the landed tile's; width untouched
    src = np.array([[0, 0], [40, 0], [20, 0], [20, -30], [20, 30]], float)
    dst = np.array([[0, 0], [40, 0], [20, 0], [20, -30], [20, 36]], float)
    M = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    J = MP.jaw_stretch(M, src, dst)
    out = MP.apply(J, src)
    assert np.allclose(out[:3], src[:3])                  # lips stay where they landed
    assert abs(out[4, 1] - 36) < 1e-6                     # chin reaches hers
    assert np.allclose(MP.apply(J, [[40, 0]]), [[40, 0]])  # width unchanged
    wild = MP.jaw_stretch(M, src, dst * [1, 5])           # clamped
    assert MP.apply(wild, src)[4, 1] <= 30 * 1.2 + 1e-6


def test_anchor_points_five_or_three():
    m = {"cl": [0, 0], "cr": [10, 0], "up": [5, -1], "lo": [5, 1], "nose": [5, -10], "chin": [5, 12]}
    assert MP.anchor_points(m).shape == (5, 2)
    del m["nose"]
    assert MP.anchor_points(m).shape == (3, 2)


def test_lips_all_is_off_by_default_and_skips_the_fade_only_when_it_owns_the_frame():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'os.environ.get("HOUSE_MOUTH_LIPS_ALL", "0")' in src
    assert src.count("self.lib.lips_own(self.key, int(self.i))") == 2


def test_mouth_region_limits_the_mask_to_the_lips():
    face = [np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float)]
    lips = np.array([[40, 60], [60, 60], [60, 66], [40, 66]], float)
    reg = MP.mouth_region((100, 100), lips, 5)
    m = MP.face_mask((100, 100), face, face, 1.0, region=reg)
    assert m[63, 50] > 0.9            # the mouth paints
    assert m[90, 50] < 0.05           # the jaw below does not
    assert m[20, 50] < 0.05           # nor the nose/eyes above


def test_match_skin_carries_her_shading_onto_the_tile():
    # her skin darkens left to right (a shadowed chin); the tile is evenly lit
    ramp = np.linspace(160, 100, 60)[None, :, None].repeat(60, 0).repeat(3, 2)
    base = ramp.astype(np.uint8)
    warped = np.full((60, 60, 3), 130, np.uint8)
    alpha = np.zeros((60, 60), np.float32); alpha[5:55, 5:55] = 1.0
    out = MP.match_skin(warped, base, alpha, None, sigma_px=4)
    assert out[30, 12, 0] > out[30, 48, 0] + 30       # the tile now darkens where she does
    assert abs(int(out[30, 12, 0]) - int(base[30, 12, 0])) < 12
    same = MP.match_skin(base.copy(), base, alpha, None, sigma_px=4)
    assert np.abs(same.astype(int) - base.astype(int)).max() <= 1


def test_pin_to_lands_the_lip_line_exactly_and_keeps_the_turn():
    M = np.array([[0.9, -0.2, 5.0], [0.2, 0.9, 7.0]])
    P = MP.pin_to(M, [10, 20], [100, 50])
    assert np.allclose(MP.apply(P, [10, 20]), [[100, 50]])
    assert np.allclose(P[:, :2], M[:, :2])
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert "S = MP.pin_to(S, ta[2], da[2])" in src


def test_lips_on_every_frame_only_for_clips_marked_land():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'if not lm.get("land"):' in src
    assert '(MOUTH_LIPS_ALL and bool((lm or {}).get("land")))' in src


def test_the_mask_is_only_for_a_turn():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert "if _turned and MOUTH_LIPS_REGION_S > 0" in src            # the mouth region only on a turn
    assert 'MOUTH_LIPS_SKIN and getattr(self, "_lip_turned", False)' in src
    i = src.index("_turned = yaw is not None and abs(yaw) >= TURN_FRONT_MAX")
    assert "if MOUTH_LIPS_MASK:" in src[i:i + 200]                   # the face mask on every lips frame
