"""Round 31 (2026-10-19): .101's items 10-17 as built, each pinned by the line .101 greps the kit for (source
spellings; the kit export renames them), and the house keeping its own score colours."""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = lambda p: (ROOT / p).read_text(encoding="utf-8")  # noqa: E731


def test_10_the_building_plate_reads_skipped_frames():
    assert "plate_jumpy_skips_on" in T("lib/crissa_build.py")
    assert "skipped=max" in T("lib/house_wall_ascii.py")


def test_11_and_12_this_computer_waits_for_you_and_the_last_line():
    s = T("lib/crissa_init.py")
    assert "def _leave_machine" in s and "def you_moves_on" in s and "CRISSA_MACHINE_WAITS_YOU" in s
    assert "Okay, let's make something brilliant!\")" in s


def test_13_the_phone_page_opens_in_her_window():
    w = T("lib/crissa_window.py")
    assert "def open_phone" in w and "def is_phone_page" in w
    assert "open_phone" in T("lib/house_wall_ascii.py")


def test_14_face_check_fade_faint_and_grey():
    assert "face_check" in T("modules/presence/crissa_presence/server.py")
    r = T("lib/house_wall_ascii_render.py")
    for k in ("def face_check_cell", "SCORE_FAINT", "SCORE_STALE_S", "def none_grey_on", "def held_ok", "def note_ok"):
        assert k in r, k


def test_14_the_house_keeps_its_own_score_colours(monkeypatch):
    sys.path.insert(0, str(ROOT / "lib"))
    r = importlib.import_module("house_wall_ascii_render")
    monkeypatch.delenv("HOUSE_WALL_NONE_GREY", raising=False)
    monkeypatch.delenv("HOUSE_WALL_SCORE_FAINT", raising=False)
    monkeypatch.setattr(r.crissa_home, "is_house", lambda: True)
    assert not r.none_grey_on() and not r.score_faint_on()
    monkeypatch.setattr(r.crissa_home, "is_house", lambda: False)
    assert r.none_grey_on() and r.score_faint_on()
    monkeypatch.setenv("HOUSE_WALL_NONE_GREY", "0")
    assert not r.none_grey_on()
    assert r.face_check_cell({}, 0.0) is None           # no face check (the house's presence) draws nothing new


def test_15_the_phone_sync_slider_reaches_600():
    s = T("display/phone-app/index.html")
    line = [x for x in s.splitlines() if 'id="lead"' in x and "<input" in x][0]
    assert 'min="-600"' in line and 'max="600"' in line


def test_16_the_key_popups_stay_above_and_close_on_focus_out():
    s = T("lib/crissa_wall_key.py")
    assert "def popup_above_on" in s    # round 32 (.101 16b): closes on another active window, not <FocusOut>


def test_17_the_bun_wobble_waits_for_a_pause():
    env = json.loads(T("config/crissa-mouth.json"))["env"]
    assert "flourish-bun-wobble-work" in env["HOUSE_SPEECH_HOLD_CLIPS"].split(",")
