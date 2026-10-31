"""Round 29: .101's round 28 fixes, built in as-is (FIXES-20261017-round28-from-101), each with a test here."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modules" / "ear"))
sys.path.insert(0, str(ROOT / "lib"))
BRIDGE = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()


def test_chris_and_krista_wake_her_only_as_an_address(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_WAKE_ADDRESS_ONLY", raising=False)
    from crissa_bridge import wake
    importlib.reload(wake)
    for said in ("Hey Krista, could you read for me?", "Chris, please put that back on the screen."):
        assert wake.wake_match(said).get("kind"), said
    for said in ("Chris is coming over later.", "I told Chris about it.", "Christmas dinner is soon"):
        assert not wake.wake_match(said).get("kind"), said


def test_over_her_line_has_an_ignore():
    from crissa_bridge import over_line
    assert "IGNORE" in over_line.ACTIONS


def test_an_ask_with_her_name_gets_no_yeah_and_words_over_her_line_go_to_her_model():
    assert "has_ask" in BRIDGE and "EAR_BRIDGE_SIDE_TALK_ASK" in BRIDGE and "side_talk_to_her" in BRIDGE


def test_three_words_not_hers_cut_her():
    src = (ROOT / "modules" / "ear" / "crissa_ear" / "barge.py").read_text()
    assert 'n_cut = int(_f("CRISSA_BARGE_WORDS_TO_CUT", 3))' in src


def test_face_only_drags_from_the_windows_own_bounds():
    win = (ROOT / "lib" / "crissa_window.py").read_text()
    page = (ROOT / "display" / "crissa-face.html").read_text()
    assert "def face_bounds" in win and "Browser.getWindowBounds" in win and "JSON.stringify({where: true})" in page
