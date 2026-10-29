"""Round 32, the desk's neck skirt (the User 2026-10-19 on round 31's desk Wall: "neck skirt bad on the up angles", "the
lower shoulder comes from the tile shoulders lowering over her chest", on the phone "a small strip of tile just below her
jaw. Seems to be flawless at these angles using just the rendered neck", and the bun wobble's end, "her mouth tile shows
its backside").

Why the phone's fix did not reach the desk: the chin cut needs the TILE's own lip marks (anchors + face outline), and the
desk's talking tiles come from five pangram takes that had none, so it never fired there. And nothing on the phone ever
addressed pitch. Three parts, all the kit desk face's (crissa/overlay), the house's own files untouched:
  - the five takes' lip marks (pose-lip-marks.py), shipped through the edition overlay;
  - front_cut {"jaw": true}: under front_cut's angle the patch stops at her own face outline (no profile-line cut,
    which sliced a frontal face), the chin cut's taper kept, an open jaw's drop kept inside the tile's own outline;
  - "from": 20: the bend landing (a turned tile, its wall showing beside her mouth) takes over at 20 deg, not ~12.
Kill: HOUSE_MOUTH_JAW_CUT=0."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
TAKES = ("back-pangram-a", "back-pangram-b", "close-pangram-a", "close-pangram-b", "down-pangram-b")


def _src():
    return (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def test_the_jaw_cut_is_the_outline_without_the_profile_line_and_has_a_kill():
    s = _src()
    assert 'MOUTH_JAW_CUT = os.environ.get("HOUSE_MOUTH_JAW_CUT", "1")' in s
    keep = s.split("    def _front_keep(", 1)[1].split("\n    def ", 1)[0]
    assert 'if jaw and not (MOUTH_JAW_CUT and fc.get("jaw") is True):' in keep
    assert "if not jaw:\n" in keep.split("keep = MP.fill(hw, dh)", 1)[1].split("cv2.fillPoly(side", 1)[0]
    assert '"jaw": jaw})' in keep
    paste = s.split("_fk = self._front_keep(rel)", 1)[1].split("if tri is not None and a_cut is not None:", 1)[0]
    assert 'if _ex.get("jaw"):' in paste and '_cc0.get("chin_half", 0.22)' in paste


def test_the_desk_kit_face_carries_it_and_the_house_does_not():
    desk = json.loads((ROOT / "crissa/overlay/display/crissa-poses/mouth-turn.json").read_text())
    house = json.loads((ROOT / "display/crissa-poses/mouth-turn.json").read_text())
    assert desk["front_cut"] == {"from": 20, "jaw": True} and desk["from"] == 20
    assert "front_skin" not in desk                  # it tinted her teeth yellow-green on the desk tiles (10-19 renders)
    assert "front_cut" not in house and house["from"] == "nearest"
    lila = json.loads((ROOT / "crissa/overlay/display/lila-poses/mouth-turn.json").read_text())
    assert lila["front_cut"] == {"from": 20}


def test_the_five_takes_marks_ship_in_the_kit_only():
    ov = json.loads((ROOT / "config/crissa-packages.json").read_text())["edition_overlay"]
    for k in TAKES:
        rel = "display/crissa-poses/clip-%s.lipmarks.json" % k
        assert ov[rel] == "crissa/overlay/" + rel
        d = json.loads((ROOT / "crissa/overlay" / rel).read_text())
        assert len(d["frames"]) == 360 and all(d["frames"]) and "check" in d
        assert not (ROOT / rel).exists()             # the house's landings do not change
