"""All OS Off keeps the public site up (the User 2026-10-26: "Keep the site, tunnel and store host up when the house is
Off. Agree."). Off at 03:01 that day put crissa.ai, zoeyos.dev and store.zoeyos.dev on Cloudflare 530. Pure: the plan
runs on given rows, nothing calls systemctl."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

SITE = ("crissa-site.service", "cloudflared-crissa.service", "house-clip-doctor.service")


def _row(unit, active="active"):
    return {"unit": unit, "scope": "user", "active": active, "sub": "running", "type": "simple", "role": "service"}


def _fresh(monkeypatch, value=None):
    if value is None:
        monkeypatch.delenv("HOUSE_OS_OFF_KEEP_SITE", raising=False)
    else:
        monkeypatch.setenv("HOUSE_OS_OFF_KEEP_SITE", value)
    import house_os_power
    return importlib.reload(house_os_power)


def test_off_keeps_the_panel_and_the_three_site_units(monkeypatch):
    hop = _fresh(monkeypatch)
    rows = [_row(u) for u in SITE + ("voiceonly-home.service", "house-conductor.service", "rail-stt.service")]
    p = hop.plan(rows=rows)
    assert {r["unit"] for r in p["keep"]} == set(SITE) | {"voiceonly-home.service"}
    assert {r["unit"] for r in p["stop"]} == {"house-conductor.service", "rail-stt.service"}
    for r in p["keep"]:
        assert r["why"], r["unit"]          # every kept unit says why on the panel


def test_the_kill_switch_puts_back_the_panel_and_the_sheet_watcher(monkeypatch):
    # The site kill switch empties SITE_KEEP; the two base (non-site) KEEP entries stay - the panel
    # and, since 2026-10-28 (Option B), the Decide-with-User sheet watcher. The site kill switch is
    # site-scoped (crissa.ai / zoeyos.dev / store), so it does not touch the sheet watcher.
    hop = _fresh(monkeypatch, "0")
    try:
        assert set(hop.KEEP) == {"voiceonly-home.service", "crissa-decide-sheet-watch.service"} and hop.SITE_KEEP == {}
        p = hop.plan(rows=[_row(u) for u in SITE])
        assert {r["unit"] for r in p["stop"]} == set(SITE)
    finally:
        _fresh(monkeypatch)


def test_on_never_restarts_a_kept_site_unit(monkeypatch):
    """On starts the snapshot minus KEEP; a site unit recorded by an older Off (10-26 03:01) is still left alone."""
    hop = _fresh(monkeypatch)
    src = (ROOT / "lib" / "house_os_power.py").read_text(encoding="utf-8")
    assert 'u["unit"] not in KEEP]' in src
    assert set(SITE) <= set(hop.KEEP)


def test_the_kit_switch_keeps_its_panel_alone():
    src = (ROOT / "lib" / "crissa_os_power.py").read_text(encoding="utf-8")
    assert "SITE_KEEP" not in src and "cloudflared" not in src and "crissa-site" not in src
