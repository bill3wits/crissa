"""Round 26 weather (.101 10-16): "I'm actually in Huntersville" was never saved, and a yes to her guess got a second
ask ("I can tell you the weather now if you want") instead of the weather."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_bridge import ear_bridge as EB  # noqa: E402


def test_the_101_rename_is_heard():
    assert EB.spoken_town("I'm actually in Huntersville. Can you tell me the weather?") == "Huntersville"
    assert EB.spoken_town("Hey, I'm actually in Huntersville, which is near Charlotte. Can you tell me the weather") \
        == "Huntersville"
    assert EB.spoken_town("I'm actually in a meeting") == ""          # no weather ask: not a town


def test_a_town_given_for_the_weather_gets_its_weather_now():
    note, had = EB.town_note("Huntersville", True, fetch=lambda t: "Sunny +70°F")
    assert had and "Right now in Huntersville it is: Sunny +70°F" in note and "Give them that weather" in note


def test_without_an_answer_or_an_ask_she_only_says_it_is_saved():
    note, had = EB.town_note("Huntersville", True, fetch=lambda t: "")
    assert not had and "It is saved" in note
    note, had = EB.town_note("Huntersville", False, fetch=lambda t: "Sunny +70°F")
    assert not had and "Right now" not in note


def test_wttr_s_refusals_are_no_weather(monkeypatch):
    assert EB.town_weather("Nowhere", fetch=lambda t: "Unknown location; please try ~1,2") == ""
    assert EB.town_weather("X", fetch=lambda t: "<html>") == ""

    def boom(t):
        raise OSError("offline")
    assert EB.town_weather("X", fetch=boom) == ""
    monkeypatch.setattr(EB, "TOWN_WEATHER_ON", False)
    assert EB.town_weather("X", fetch=lambda t: "Sunny") == ""


def test_the_turn_wires_it():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert '_asked = bool(_GUESS.get("place")) or bool(WEATHER_ASK.search(spoken_user))' in src
    assert "_tn, _had = town_note(_town, _asked)" in src and "if not _had:" in src
