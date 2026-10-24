"""Round 26, the Control Panel once-over (.101 10-16, the User: "Messy top section and overall"; "those toggles always
confuse me - press for what I want? or are we in that mode now?"; "2 units down: Audio8, Wall" in red while both were off
on purpose; the journal "squeezes the tiles"; Open the Wall "does not work", then opened late)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_watch as WT  # noqa: E402

PAGE = (ROOT / "display" / "crissa-panel.html").read_text()


def _srv():
    spec = importlib.util.spec_from_file_location("crissa_panel_server_r26", ROOT / "scripts" / "crissa-panel-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_wall_closed_by_the_person_is_parked_not_down(tmp_path, monkeypatch):
    S = _srv()
    WT.set_closed("wall", True, by="the Control Panel", home=tmp_path)
    monkeypatch.setattr(S, "_lane_home", lambda: tmp_path)
    why = S.chosen_off("crissa-wall.service")
    assert why.startswith("closed by you") and "the Control Panel" in why
    assert S.chosen_off("crissa-asr.service") == ""
    assert S.chosen_off("something-else.service") == ""
    monkeypatch.setenv("CRISSA_PANEL_CHOSEN_OFF", "0")
    assert S.chosen_off("crissa-wall.service") == ""


def test_a_voice_no_role_uses_is_standby(tmp_path, monkeypatch):
    S = _srv()
    import crissa_voice_demand as VD
    import crissa_voice_tiers as VTI
    monkeypatch.setattr(S, "_lane_home", lambda: tmp_path)
    monkeypatch.setattr(VD, "held_engines", lambda home=None: set())
    monkeypatch.setattr(VTI, "read", lambda home=None: {"main": "pocket", "announcer": "pocket"})
    monkeypatch.setattr(VD, "named_engine", lambda env=None, home=None: "")
    lane = VD.ENGINE_LANE.get("audio8") or "audio8"
    assert S.chosen_off("crissa-%s.service" % lane).startswith("standby")
    monkeypatch.setattr(VTI, "read", lambda home=None: {"main": "audio8"})
    assert S.chosen_off("crissa-%s.service" % lane) == ""


def test_the_header_does_not_count_a_parked_row_as_down():
    S = _srv()
    f = S.fleet_of({"services": [{"label": "Wall", "health": "warn", "parked": True},
                                 {"label": "Ear", "health": "bad"}]})
    assert f["down"] == 1 and f["down_names"] == ["Ear"] and f["parked"] == 1


def test_unit_status_parks_by_choice():
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert 'why_off = chosen_off(name)' in src and 'row.update(health="warn", note=why_off, parked=True)' in src


def test_actions_and_security_are_two_rows():
    keys = PAGE.split('<div class="keys" id="keys">', 1)[1].split("</div>", 1)[0]
    sec = PAGE.split('<div class="keys" id="secKeys">', 1)[1].split("</div>", 1)[0]
    for k in ('id="bHouse"', 'id="bAgent"', 'id="bWall"', 'id="bConnect"'):
        assert k in keys and k not in sec
    for k in ('id="bSecurity"', 'id="bSecure"', 'id="vSecure"'):
        assert k in sec and k not in keys


def test_two_state_keys_say_their_state_and_security_says_its_mode():
    for lab in ("'Mic<span class=\"sv\">'", "'Echo cancel<span class=\"sv\">'", "'Dev mode<span class=\"sv\">'",
                "'Wall<span class=\"sv\">'", "'Agent<span class=\"sv\">'"):
        assert lab in PAGE, lab
    assert 'k.id="bSecCycle"' in PAGE and 'var LADDER=["off","id_only","on","secure","double_secure"]' in PAGE


def test_the_wall_key_answers_the_press_at_once():
    assert 'opening\\u2026' in PAGE and 'WALL_BUSY=Date.now(); b.classList.add("busy")' in PAGE


def test_the_journal_waits_for_a_tile():
    assert "var f=true; try{ var kept=localStorage.getItem(KEY)" in PAGE
    assert "if(svcOpen && window.unfoldLog) window.unfoldLog();" in PAGE
