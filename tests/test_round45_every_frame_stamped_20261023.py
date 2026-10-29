"""Round 45 (R44-1, the tester: "a tiny teeth-only frame during a dip", once, on work-multitask or a bowing flourish): a
painting clip never shows the legacy Strip mouth (the old 96 px mouth in an ellipse, house-conductor Strip.tile_for).

Two halves. The DATA: every frame of every clip that paints (a loop, or a playlist that says `paint`) has a stamp - a
playlist row for that film frame whose bin resolves a tile for EVERY class, the tile on disk with its map head. The
set is the inventory's own (never a hand list) and the frame count is the film's own (ffprobe), on both faces. The
CODE: a frame on air with no hop under `now` (Mouth.viseme() None) paints the closed rest, and a playlist clip whose
frame found no tile keeps her filmed mouth instead of falling through to the Strip. On in a pack, the house keeps its
mouth law. Kill HOUSE_MOUTH_PL_NO_STRIP=0.

One known data gap, Lila's bank only: lila-listen-settle's tile f0132 has no map head (a box paste, not the Strip, on
the house's Render host; a pack's Wall shows none of that bank without krew - D12). It is marked, never skipped: the
mark fails the day it is fixed, so it is taken off."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from mouth_playlist import lookup, playlist_path_for  # noqa: E402
from mouth_viseme import CLASSES  # noqa: E402

CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
FACES = [d for d in (ROOT / "display" / "crissa-poses", ROOT / "display" / "lila-poses") if (d / "inventory.json").is_file()]
MIRROR_TAG = "#mirror"
KNOWN_GAPS = {("lila-poses", "lila-listen-settle")}
BORROW_SKIP = ("talk-comms", "talk-comms-b", "talk-down")      # the conductor's Library.playlist face-bank borrow


def _face_bank(key: str) -> Path | None:
    for d in FACES:
        face = d.name.split("-")[0]
        if d.name != "crissa-poses" and key.lower().startswith(face + "-"):
            return d
    return None


def _playlist(pose: Path, key: str, row: dict) -> dict | None:
    """The playlist the conductor paints this clip from (Library.playlist): its own, else its face bank's."""
    named = row.get("playlist")
    path = (pose / str(named)) if named else playlist_path_for(str(row.get("file") or ""), pose)
    if not path.is_file():
        bank = _face_bank(key)
        if bank is None:
            return None
        want = "down" if str(row.get("start") or "").startswith("down") else "back"
        for cand in sorted(bank.glob("*.playlist.json")):
            d = json.loads(cand.read_text(encoding="utf-8"))
            if d.get("index") and str(d.get("pose") or "") == want and not str(d.get("clip") or "").endswith(BORROW_SKIP):
                return dict(d, _bank=str(bank))
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    return d if isinstance(d, dict) and d.get("index") else None


def _painting():
    out = []
    for pose in FACES:
        for key, row in json.loads((pose / "inventory.json").read_text(encoding="utf-8"))["clips"].items():
            if row.get("mouth") is False:
                continue
            pl = _playlist(pose, key, row)
            if str(row.get("role") or "") == "loop" or (pl and pl.get("paint")):
                marks = [pytest.mark.xfail(strict=True, reason="known: tile f0132 has no map head")] \
                    if (pose.name, key) in KNOWN_GAPS else []
                out.append(pytest.param(pose, key, id="%s:%s" % (pose.name, key), marks=marks))
    return out


def _frames(p: Path) -> int:
    return int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                               "stream=nb_read_frames", "-of", "csv=p=0", str(p)],
                              capture_output=True, text=True, check=True).stdout.strip().split(",")[0])


def _heads(bank: Path) -> set:
    mp = json.loads((bank / "visemes" / "phoneme-map" / "map.json").read_text(encoding="utf-8"))
    return {Path(str(e.get("tile") or "")).name for e in mp.get("entries") or [] if e.get("tile") and e.get("head")}


def test_the_set_is_the_inventorys_and_holds_the_suspects():
    keys = {p.values[1] for p in _painting() if p.values[0].name == "crissa-poses"}
    for k in ("work", "work-multitask", "listen", "flourish-nod", "flourish-type-work", "flourish-type-work-b",
              "flourish-notes"):
        assert k in keys, k


@pytest.mark.parametrize("pose,key", _painting())
def test_every_frame_of_a_painting_clip_has_a_stamp(pose, key):
    row = json.loads((pose / "inventory.json").read_text(encoding="utf-8"))["clips"][key]
    pl = _playlist(pose, key, row)
    assert pl, "%s paints with no playlist: every frame on air would wear the Strip" % key
    film = pose / str(row["file"])
    if not film.is_file() and _face_bank(key):
        film = _face_bank(key) / str(row["file"])
    n = _frames(film)
    rows = pl.get("frames") or []
    own = "_bank" not in pl
    if own:
        assert len(rows) == n, "%s: %d playlist rows for %d film frames" % (key, len(rows), n)
    bank = Path(pl["_bank"]) if not own else pose
    heads = _heads(bank)
    gaps = []
    for i in range(n):
        j = min(i, len(rows) - 1)
        fr = rows[j] if rows else None
        if not isinstance(fr, dict) or not fr.get("pitch_bin") or not fr.get("yaw_bin"):
            gaps.append((i, "row"))
            continue
        for k in CLASSES:
            hit = lookup(pl, i, k)
            if hit is None:
                gaps.append((i, k))
                continue
            for rel in (hit if isinstance(hit, list) else [hit]):
                rel = rel[: -len(MIRROR_TAG)] if rel.endswith(MIRROR_TAG) else rel
                if not (bank / rel).is_file():
                    gaps.append((i, k, "no png " + rel))
                elif Path(rel).name not in heads:
                    gaps.append((i, k, "no map head " + Path(rel).name))
    assert not gaps, "%s: %d frame/class gaps, first %s" % (key, len(gaps), gaps[:8])


def test_no_hop_under_now_is_the_closed_rest_not_the_strip():
    assert 'MOUTH_PL_NO_STRIP = os.environ.get("HOUSE_MOUTH_PL_NO_STRIP", "0" if crissa_home.is_house() else "1")' \
        in CONDUCTOR
    comp = CONDUCTOR.split("    def _composite(self, base: np.ndarray, dt: float) -> np.ndarray:", 1)[1].split(
        "\n    def ", 1)[0]
    pick = comp.split("vis = self.mouth.viseme() if (self.mouth.painting() and not held) else sheet_k", 1)[1]
    pick = pick.split("klass = viseme_of(vis)", 1)[0]
    assert 'if vis is None and MOUTH_PL_NO_STRIP and self.mouth.force is None:' in pick
    assert 'vis = "rest"' in pick


def test_a_playlist_clip_never_falls_through_to_the_strip():
    comp = CONDUCTOR.split("    def _composite(self, base: np.ndarray, dt: float) -> np.ndarray:", 1)[1].split(
        "\n    def ", 1)[0]
    belt = "if MOUTH_PL_NO_STRIP and self.mouth.force is None and self.lib.playlist(self.key):"
    assert comp.count(belt) == 1
    assert comp.index(belt) < comp.index("got = self.strip.tile_for(pose, lvl, vis)"), "the belt sits above the Strip"
    assert "return self._fade_out(base)" in comp.split(belt, 1)[1].split("got = self.strip.tile_for", 1)[0]
