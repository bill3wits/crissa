"""Round 32 (2026-10-19): .101's round 31 fixes as built, each pinned by the line .101 greps the kit for (source
spellings): 18 a first boot opens on Lila, 16b the key's menu stays open under the pointer, 13b her phone window waits
for its page."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = lambda p: (ROOT / p).read_text(encoding="utf-8")  # noqa: E731


def test_18_a_first_boot_opens_on_lila_never_on_the_house():
    i = T("lib/crissa_init.py")
    assert "def boot_host_loop" in i and "CRISSA_BOOT_ON_LILA" in i
    c = T("scripts/house-conductor.py")
    assert "    if not IS_HOUSE and not IS_BENCH:\n        # A FIRST BOOT OPENS ON LILA" in c   # the house never
    assert "crissa_init.boot_host_loop()" in c


def test_16b_the_menu_closes_on_another_active_window_not_on_focus_out():
    s = T("lib/crissa_wall_key.py")
    assert "_NET_ACTIVE_WINDOW" in s and "<FocusOut>" not in s and "def popup_above_on" in s


def test_13b_her_phone_window_waits_for_its_page():
    w = T("lib/crissa_window.py")
    assert "def _wait_page" in w and "def open_phone" in w
    assert "_wait_page(" in w.split("def open_phone", 1)[1].split("\ndef ", 1)[0]


def test_5_the_first_piece_size_is_a_switch_that_stays_40():
    import importlib.util
    spec = importlib.util.spec_from_file_location("zpocket", ROOT / "modules/voice/scripts/crissa-pocket.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.settings(env={})["chunk_first_max"] == 40
    assert m.settings(env={"POCKET_FIRST_MAX": "24"})["chunk_first_max"] == 24
    assert m.settings(env={"POCKET_FIRST_MAX": "x"})["chunk_first_max"] == 40
    import sys
    sys.path.insert(0, str(ROOT / "modules/voice"))
    from crissa_voice import chunker
    line = "Sure, I can put that on the screen for you right now. Here it is."
    assert len(chunker.chunk_text(line, first_max=24)[0]) <= 24 < len(chunker.chunk_text(line, first_max=40)[0])
