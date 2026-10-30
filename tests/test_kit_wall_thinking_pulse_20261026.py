"""The kit's desk Wall glows while she thinks (parity sweep 2026-10-26, gap 1): the ear bridge writes the thinking
file by default, and the launcher now turns the desk Wall's reader on. The house's wall default stays off (its
drop-in arms it); the kill is CRISSA_WALL_THINKING_PULSE=0 (CRISSA_ in the kit)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_pulse", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _wall(env_in):
    p = _launcher().plan(_args(), env_in=env_in)
    return {k["name"]: k for k in p["children"]}["wall"]["env"]


def test_the_desk_wall_pulses_by_default():
    assert _wall({})["HOUSE_WALL_THINKING_PULSE"] == "1"


def test_the_kill_and_a_callers_own_value():
    assert "HOUSE_WALL_THINKING_PULSE" not in _wall({"CRISSA_WALL_THINKING_PULSE": "0"})
    assert _wall({"HOUSE_WALL_THINKING_PULSE": "0"})["HOUSE_WALL_THINKING_PULSE"] == "0"


def test_only_the_wall_child_gets_it():
    p = _launcher().plan(_args(), env_in={})
    assert [k["name"] for k in p["children"] if "HOUSE_WALL_THINKING_PULSE" in k["env"]] == ["wall"]


def test_the_house_wall_default_is_still_off_and_the_ear_writes_the_file_by_default():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    assert 'os.environ.get("HOUSE_WALL_THINKING_PULSE", "0")' in src
    ear = (ROOT / "modules" / "ear" / "crissa_bridge" / "settings.py").read_text()
    assert '"thinking": ("EAR_BRIDGE_THINKING", "1")' in ear
