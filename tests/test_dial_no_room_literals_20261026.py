"""The kit's dial knows no room (the User 2026-10-26, his 1.0.1 walk: the clock showed one office's door, wall and media
to a stranger). With no stations from Setup the overlay draws twelve plain hours, no DOOR / MEDIA / wall marks, and the
door halo is hidden; an untaught door is no door.

The fold (2026-10-28): the Wall page is one file for both editions. It still names no room: the house's office (the
literals this page carried until 10-26) lives in config/house-machine.json `wall_room`, and the feed writes it into
ROOM_DEFAULTS as it serves the page; a pack has no such file, so ROOM_DEFAULTS is {} and every default is empty."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "display" / "voiceonly-home.html"   # one page since the fold


def _draw_clock() -> str:
    s = OVERLAY.read_text(encoding="utf-8")
    i = s.index("function drawClock(")
    return s[i:s.index("\n}\n", i)]


def test_the_dial_defaults_name_no_station():
    body = _draw_clock()
    for k in ("legal_sit", "wall_stations", "blanked_sit"):
        assert "%s) || ROOM_DEFAULTS.%s || []" % (k, k) in body
    assert re.search(r"\|\|\s*\[\s*\d", body) is None, "a literal hour list is one office's room"


def test_an_untaught_door_is_no_door_and_media_never_stands_in():
    body = _draw_clock()
    assert "ROOM_DEFAULTS.door_hour || null" in body and ": null;" in body.split("var doorH")[1].split("\n")[0]
    assert 'doorH==null){ _halo.style.display="none"' in body
    # a blanked hour stands in for MEDIA only where this install's own room says so (the house's wall_room)
    assert "ROOM_DEFAULTS.media_from_blank && blank.length" in body


def test_the_room_is_filled_by_the_feed_and_a_pack_has_none(monkeypatch):
    import importlib.util, json, sys
    sys.path.insert(0, str(ROOT / "lib"))
    page = OVERLAY.read_text(encoding="utf-8")
    assert "return __VO_ROOM__;" in page
    spec = importlib.util.spec_from_file_location("feed_room_fold", ROOT / "scripts" / "voiceonly-home-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.crissa_home, "HOUSE_MACHINE", ROOT / "no-such-house-machine.json")
    assert m.wall_room() == {}
    assert b"return {};" in m.page_html()
    house = json.loads((ROOT / "config" / "house-machine.json").read_text(encoding="utf-8"))["wall_room"]
    assert house["door_pan"] == 450000 and house["legal_sit"] == [12, 1, 2, 3, 8, 10, 11]
