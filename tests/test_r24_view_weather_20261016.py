"""Round 24 small items (the User 2026-10-16): the reading view, ft-desk for the owner, weather never invented."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "modules" / "ear"))

from crissa_bridge import ear_bridge as EB  # noqa: E402


def test_reading_view_defaults_close(tmp_path):
    assert EB.reading_view({}, tmp_path) == "close"
    (tmp_path / "reading-view.json").write_text(json.dumps({"view": "multitask"}))
    assert EB.reading_view({}, tmp_path) == "multitask"
    assert EB.reading_view({"EAR_BRIDGE_READING_VIEW": "close"}, tmp_path) == "close"
    (tmp_path / "reading-view.json").write_text("{nonsense")
    assert EB.reading_view({}, tmp_path) == "close"


def test_a_reading_keeps_the_close_view(tmp_path):
    assert EB.view_for_request("read me the first chapter of Moby Dick aloud", {}, tmp_path) is False
    assert EB.view_for_request("what's on my calendar today", {}, tmp_path) is True
    assert EB.view_for_request("read me the first chapter aloud", {"EAR_BRIDGE_READING_VIEW": "multitask"}, tmp_path)


def test_dispatch_asks_before_moving():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert 'if not resume and view_for_request(user):\n        view_to_multitask("request")' in src


def test_ft_desk_is_not_a_guest_command():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    i = src.index("_GUEST_COMMANDS = {")
    block = src[i:src.index("}", i)]
    assert "/v1/ft-desk" not in block and "/v1/import" in block and "/v1/freelance" in block


def test_spoken_town():
    assert EB.spoken_town("I live in Asheville") == "Asheville"
    assert EB.spoken_town("my town is Santa Cruz.") == "Santa Cruz"
    assert EB.spoken_town("set my location to Leeds please")[:5] == "Leeds"
    assert EB.spoken_town("what's the weather, I'm in Boston") == "Boston"
    assert EB.spoken_town("I'm in the kitchen") == ""          # not a weather turn
    assert EB.spoken_town("how are you") == ""


def test_set_town_writes_weather_json(tmp_path):
    assert EB.set_weather_town("Asheville", tmp_path)
    assert json.loads((tmp_path / "weather.json").read_text())["location"] == "Asheville"


def test_weather_note_never_invents():
    assert EB.weather_note("how are you", fetch=lambda u, t: {}) == ""
    no_loc = EB.weather_note("what's the weather like", fetch=lambda u, t: {
        "weather": {"ok": False, "text": "", "mark": "no location set (Setup or <state home>/weather.json)"}}, guess=lambda: "")
    assert "Never guess" in no_loc and "ask for their town" in no_loc
    known = EB.weather_note("is it raining", fetch=lambda u, t: {"weather": {"ok": True, "text": "Light rain 12°C"}})
    assert "Light rain 12°C" in known

    def boom(u, t):
        raise OSError("down")
    assert "Never guess" in EB.weather_note("weather?", fetch=boom)


def test_turn_carries_the_weather_note():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert "turn_note += weather_note(spoken_user)" in src


def _conductor_src():
    return (ROOT / "scripts" / "house-conductor.py").read_text()


def test_headback_ramp():
    import importlib.util
    src = _conductor_src()
    i = src.index("def headback_k(")
    j = src.index("\n\n\n", i)
    ns = {"HEADBACK_FROM": 10.0, "HEADBACK_TO": 20.0}
    exec(src[i:j], ns)
    k = ns["headback_k"]
    assert k(-25) == 1.0 and k(10) == 1.0 and k(15) == 0.5 and k(20) == 0.0 and k(24.6) == 0.0


def test_headback_wiring():
    src = _conductor_src()
    assert 'MOUTH_HEADBACK_FADE = os.environ.get("HOUSE_MOUTH_HEADBACK_FADE", "1")' in src
    assert src.count('_hb = float(getattr(self, "_headback_k", 1.0))') == 2
    assert "cover_alpha=(jaw_on or _late or _hb < 1.0)" in src
    assert "cover_alpha=(jaw_on or _mask is not None or _hb < 1.0)" in src
    assert '_dbg["branch"] = "speech: filmed (head back)"' in src
    assert "abs(float(_yh)) < TURN_FRONT_MAX" in src
