"""The house Control Panel's wiring, the kit's half (the User 2026-10-28, "Crissa Wall + Krew = house now"): keys are
masked in every line the kit panel shows (journal pages, lane tails, the whole log, a unit's script - lib/panel_logs,
the one masker, shipped with the panel package now), every tile says who watches it and when they last checked
(the launcher's watch for lanes and pieces, systemd for units, this panel for all), /v1/watchers lists them, and
long or unsorted sections start folded. Paging, the Whole log route, named failure reasons and Run now were already
the kit's. Switches: CRISSA_PANEL_LOG_MASK, CRISSA_PANEL_WATCHED_BY, CRISSA_PANEL_FOLD."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _server(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    spec = importlib.util.spec_from_file_location("zp_wiring", ROOT / "scripts" / "crissa-panel-server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    return srv


def test_every_shown_line_is_masked_and_the_switch_shows_it_raw(tmp_path, monkeypatch):
    srv = _server(tmp_path, monkeypatch)
    res = srv.mask_lines({"lines": ["api_key=sk-abcdef1234567890 ok", "plain"], "text": "password=hunter2", "unit_file": "x"})
    assert res["lines"] == ["api_key=[masked] ok", "plain"] and res["text"] == "password=[masked]"
    monkeypatch.setenv("CRISSA_PANEL_LOG_MASK", "0")
    assert srv.mask_lines({"lines": ["api_key=sk-abcdef1234567890"]})["lines"] == ["api_key=sk-abcdef1234567890"]
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    for route in ("mask_lines(tail_lane(", "mask_lines(journal(", "mask_lines(proc_script(", "mask_lines(script_view(*got))",
                  'if code == 200 and mask_on():'):
        assert route in src, route


def test_the_masker_ships_with_the_panel_and_spells_no_house_word():
    pk = json.loads((ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8"))["packages"]["crissa-panel"]
    assert "panel_logs.py" in pk["lib"]
    spec = importlib.util.spec_from_file_location("zx_wiring", ROOT / "scripts" / "crissa-export.py")
    zx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zx)
    assert zx.scrub_text((ROOT / "lib" / "panel_logs.py").read_text(encoding="utf-8")) == {}


def test_each_tile_says_who_watches_it(tmp_path, monkeypatch):
    srv = _server(tmp_path, monkeypatch)
    import crissa_watch
    monkeypatch.setattr(crissa_watch, "read_status", lambda home=None: {
        "launcher_alive": True, "lanes": {"ear": {"state": "up", "checked_at": 1_000_000.0}, "tts": {"state": "up", "checked_at": 0}}})
    rows = [{"unit": "lane:ear", "scope": "launcher"}, {"unit": "proc:player", "scope": "launcher"},
            {"unit": "crissa-panel", "scope": "user"}, {"unit": "krew", "placeholder": True}]
    out = srv.watch_decorate(rows, now=1_000_100.0)
    assert out[0]["watch_line"].startswith("the launcher's watch") and "this panel" in out[0]["watch_line"]
    assert out[0]["watched_by"][0]["fresh"] is True
    assert out[1]["watched_by"][0]["id"] == "launcher" and out[2]["watched_by"][0]["id"] == "systemd"
    assert "watch_line" not in out[3]                                   # a placeholder is nobody's tile
    # a lane the launcher has not checked in 15 min, or no launcher at all, reads STALE
    stale = srv.watch_decorate([{"unit": "lane:ear", "scope": "launcher"}], now=1_000_000.0 + 2000)
    assert "STALE" in stale[0]["watch_line"]
    monkeypatch.setenv("CRISSA_PANEL_WATCHED_BY", "0")
    assert "watch_line" not in srv.watch_decorate([{"unit": "lane:ear", "scope": "launcher"}])[0]
    w = srv.watchers_view(now=5.0)
    assert [x["id"] for x in w["watchers"]] == ["launcher", "systemd", "panel"] and w["watchers"][0]["alive"] is True


def test_long_and_unsorted_sections_fold_but_never_the_first(tmp_path, monkeypatch):
    srv = _server(tmp_path, monkeypatch)
    rows = [{"section": "Wall services"}] * 12 + [{"section": "Crissa lanes"}] * 3 + [{"section": "Tools"}] * 9 + \
           [{"section": srv.CFG.get("new_section") or "New · not yet sorted"}]
    folds = srv.section_folds(rows)
    assert "Wall services" not in folds and "Crissa lanes" not in folds
    assert folds["Tools"] == {"fold": True} and folds[srv.CFG.get("new_section") or "New · not yet sorted"] == {"fold": True}
    monkeypatch.setenv("CRISSA_PANEL_FOLD", "0")
    assert srv.section_folds(rows) == {}
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    assert '"sections": section_folds(rows)' in src and 'if path == "/v1/watchers":' in src


def test_the_page_draws_the_watched_by_line():
    page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert "watched by: '+esc(s.watch_line)" in page and ".unit .watch.stale{color:var(--lamp-a)" in page
    assert "if(folds[g] && folds[g].fold" in page                       # the server's folds are honoured
