"""With no town saved, she offers the connection's place and asks; a yes saves it (the User 2026-10-16)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_bridge import ear_bridge as EB  # noqa: E402

NO_LOC = {"weather": {"ok": False, "text": "", "mark": "no location set (Setup or <state home>/weather.json)"}}


def test_guess_is_asked_never_given(monkeypatch):
    monkeypatch.setattr(EB, "_GUESS", {"place": "", "at": 0.0})
    note = EB.weather_note("what's the weather", fetch=lambda u, t: NO_LOC, guess=lambda: "Charlotte, North Carolina, US")
    assert "near Charlotte, North Carolina, US" in note and "Do not give any weather until they confirm" in note
    assert EB._GUESS["place"] == "Charlotte, North Carolina, US"


def test_no_guess_asks_for_the_town(monkeypatch):
    monkeypatch.setattr(EB, "_GUESS", {"place": "", "at": 0.0})
    note = EB.weather_note("what's the weather", fetch=lambda u, t: NO_LOC, guess=lambda: "")
    assert note == EB.WEATHER_NO_LOCATION
    assert EB.guess_place(lambda: "Unknown location; please try ~1.2,3.4") == ""
    assert EB.guess_place(lambda: "<html>") == ""

    def boom():
        raise OSError("offline")
    assert EB.guess_place(boom) == ""


def test_yes_saves_the_guess(tmp_path, monkeypatch):
    monkeypatch.setattr(EB, "_GUESS", {"place": "Charlotte, North Carolina, US", "at": 1000.0})
    assert EB.confirm_guess("no, Asheville", now=1010.0, home=tmp_path) == ""
    assert EB.confirm_guess("yes that's right", now=1010.0 + EB.GUESS_TTL_S, home=tmp_path) == ""
    assert EB.confirm_guess("Yeah, that's it", now=1010.0, home=tmp_path) == "Charlotte, North Carolina, US"
    assert json.loads((tmp_path / "weather.json").read_text())["location"] == "Charlotte, North Carolina, US"
    assert EB.confirm_guess("yes", now=1011.0, home=tmp_path) == ""      # used up


def test_the_guess_goes_through_the_egress_switch():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    i = src.index("def guess_place(")
    body = src[i:src.index("\n\n\n", i)]
    assert "crissa_egress.urlopen(req, 4.0, \"the weather glance\")" in body
    assert "confirm_guess(spoken_user)" in src
