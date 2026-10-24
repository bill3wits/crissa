"""The Control Panel's add-on slot (the User 2026-10-06): one panel, in the Wall; an add-on registers only its own few
tiles in the bottom section, and with nothing installed that section shows a placeholder, the off mark and the slot's
words from the settings ("... plugin not installed"), the way the clock marks a missing camera. Nothing lit, nothing
to press, not counted as a service.

Same harness as tests/test_crissa_panel.py: a fresh install in tmp from the shipped files, a stand-in systemctl.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import test_crissa_panel as base

ROOT = base.ROOT
svc = base.svc


def manifest(**over) -> dict:
    m = {"schema": "crissa-panel-addon/v1", "id": "krew", "title": "Krew", "by": "krew-panel-tiles",
         "tiles": [
             {"unit": "agent-gateway", "label": "Gateway - every profile on one gateway", "port": 8642},
             {"match": "agent-*-gateway.service", "label": "{unit} - one profile's gateway"},
             {"unit": "agent-dashboard", "label": "Dashboard - the agent's page", "port": 9119,
              "href": "http://127.0.0.1:9119/"},
             {"label": "Board - the work", "href": "http://127.0.0.1:9119/board"},
             {"label": "Janitor - what it watches", "status": "{report}", "stale_s": 900, "unit": "agent-janitor.timer"},
         ]}
    m.update(over)
    return m


def addon_units(root: Path) -> dict:
    u = base.fresh_units(root)
    u["user"].update({
        "agent-gateway.service": svc(exec_path="/opt/agent/bin/gateway"),
        "agent-ada-gateway.service": svc(active="inactive", exec_path="/opt/agent/bin/gateway", LoadState="masked",
                                         UnitFileState="masked"),
        "agent-lab-gateway.service": svc(exec_path="/opt/agent/bin/gateway"),
        "agent-dashboard.service": svc(exec_path="/opt/agent/bin/dashboard"),
        "agent-janitor.timer": {"ActiveState": "active", "SubState": "waiting", "UnitFileState": "enabled",
                                "Triggers": "agent-janitor.service"},
        "agent-janitor.service": svc(active="inactive", Type="oneshot", UnitFileState="static",
                                     TriggeredBy="agent-janitor.timer"),
    })
    return u


def install(m: base.Machine, man: dict | None = None, name: str = "krew.json") -> Path:
    d = m.state / "panel.d"
    d.mkdir(exist_ok=True)
    report = m.state / "janitor-latest.json"
    report.write_text(json.dumps({"level": "warn", "headline": "1 unit red: agent-lab-gateway", "epoch": time.time()}))
    f = d / name
    f.write_text(json.dumps(man or manifest()).replace("{report}", str(report)))
    return f


@pytest.fixture
def addon_machine(tmp_path):
    return base.Machine(tmp_path, addon_units(tmp_path / "install"))


def test_the_shipped_defaults_hold_one_slot_and_the_code_names_none():
    d = json.loads((ROOT / "config/crissa-panel.json").read_text())["addons"]
    assert d["section"] == "krew services" and d["paths"] == ["{state}/panel.d"]
    assert [s["id"] for s in d["slots"]] == ["krew"] and d["slots"][0]["missing"] == "krew not installed"
    code = (ROOT / "lib/crissa_panel_addons.py").read_text() + (ROOT / "display/crissa-panel.html").read_text()
    assert "krew" not in code.lower()   # the house word list runs in test_crissa_panel_house (source only)


def test_without_the_addon_the_bottom_section_is_a_placeholder_not_a_service(addon_machine):
    s = base.Server(addon_machine)
    try:
        d = s.get("/v1/services")
        assert d["addons"]["installed"] == [] and d["addons"]["errors"] == []
        miss = [dict(m) for m in d["addons"]["missing"]]
        # what this computer cannot do is folded into "Not on this computer" with its reason (2026-10-10,
        # lib/crissa_capability): krew's placeholder carries "krew is not installed" when the capability says so
        assert all(m.pop("blank", "krew is not installed") == "krew is not installed" for m in miss)
        assert miss == [{"slot": "krew", "label": "Krew", "note": "krew not installed",
                         "state": "off", "mark": "○", "section": "krew services", "placeholder": True}]
        units = {r["unit"] for r in d["services"]}
        assert not any(r.get("addon") for r in d["services"])
        assert not any(u.startswith("agent-") for u in units)          # not this install's until an add-on says so
        assert d["fleet"]["total"] == 4 and d["fleet"]["down"] == 0    # the placeholder is not counted, never red
        code, _ = s.post("/v1/action", {"action": "restart", "unit": "agent-gateway"})
        assert code == 400                                              # and nothing of the add-on can be pressed
    finally:
        s.stop()


def test_an_installed_addon_registers_its_tiles_at_the_bottom_with_their_keys(addon_machine):
    install(addon_machine)
    s = base.Server(addon_machine)
    try:
        d = s.get("/v1/services")
        assert d["addons"]["missing"] == [] and [a["id"] for a in d["addons"]["installed"]] == ["krew"]
        mine = [r for r in d["services"] if r.get("addon") == "krew"]
        assert [r["unit"] for r in mine] == ["agent-gateway", "agent-ada-gateway", "agent-lab-gateway",
                                             "agent-dashboard", "", "agent-janitor.timer"]
        assert d["services"][-len(mine):] == mine                       # the bottom section, in the add-on's order
        assert {r["section"] for r in mine} == {"krew services"}
        by = {r["label"].split(" - ")[0]: r for r in mine}
        assert by["agent-ada-gateway"]["parked"] and "masked" in by["agent-ada-gateway"]["note"]
        assert by["Dashboard"]["href"] == "http://127.0.0.1:9119/" and by["Board"]["link"]
        assert by["Janitor"]["report"] and by["Janitor"]["health"] == "warn" and "agent-lab-gateway" in by["Janitor"]["note"]
        assert by["Gateway"]["health"] == "ok" and by["Gateway"]["why"] == "add-on krew"
        assert d["fleet"]["total"] == 4 + 4 and d["fleet"]["parked"] == 1  # 4 add-on units; link and report are not
        # full tiles: journal, script and the keys reach the add-on's units
        assert s.get("/v1/script?unit=agent-gateway")["ok"] is not False
        code, r = s.post("/v1/action", {"action": "restart", "unit": "agent-gateway"})
        assert code == 200, r
        calls = addon_machine.systemd()["calls"]
        assert ["user", "restart", "agent-gateway.service"] in calls
        assert "pipewire" not in {r["unit"] for r in d["services"]}
    finally:
        s.stop()


def test_all_os_off_covers_the_addons_units(addon_machine):
    install(addon_machine)
    plan = addon_machine.py("import crissa_units as zu\nfrom crissa_os_power import Power\n"
                            "zu.refresh(rules=zu.Rules.from_conf())\nprint(json.dumps(Power().plan()))")
    text = json.dumps(plan)
    assert "agent-gateway.service" in text and "agent-dashboard.service" in text
    assert "pipewire.service" not in text


def test_a_curated_tile_the_addon_claims_moves_to_the_bottom_once(tmp_path):
    m = base.Machine(tmp_path, addon_units(tmp_path / "install"),
                     extra_cfg={"tiles": [{"unit": "agent-dashboard", "label": "Dashboard", "section": "Agents"}]})
    install(m)
    s = base.Server(m)
    try:
        rows = [r for r in s.get("/v1/services")["services"] if r["unit"] == "agent-dashboard"]
        assert len(rows) == 1 and rows[0]["section"] == "krew services" and rows[0]["addon"] == "krew"
    finally:
        s.stop()


def test_a_broken_manifest_is_named_and_the_slot_stays_a_placeholder(addon_machine):
    d = addon_machine.state / "panel.d"
    d.mkdir()
    (d / "a-bad.json").write_text("{not json")
    (d / "b-other.json").write_text(json.dumps({"schema": "something-else", "id": "krew"}))
    install(addon_machine, manifest(id="extra", title="Extra",
                                    tiles=[{"label": "x", "href": "javascript:alert(1)"},
                                           {"unit": "agent-gateway; rm -rf /"}, {"match": "*"},
                                           {"label": "fine", "href": "http://127.0.0.1:1/"}]), name="c-extra.json")
    s = base.Server(addon_machine)
    try:
        v = s.get("/v1/addons")
        whys = " | ".join(e["why"] for e in v["errors"])
        assert "cannot read it" in whys and "not a crissa-panel-addon/v1 manifest" in whys
        assert "href must be an http(s) address" in whys and "bad unit name" in whys and "bad match pattern" in whys
        assert [m["id"] for m in v["installed"]] == ["extra"] and [p["slot"] for p in v["missing"]] == ["krew"]
        assert [t["label"] for t in v["tiles"]] == ["fine"]
        assert s.get("/v1/services")["ok"]
    finally:
        s.stop()


def test_removing_the_manifest_brings_the_placeholder_back(addon_machine):
    f = install(addon_machine)
    s = base.Server(addon_machine)
    try:
        assert s.get("/v1/services")["addons"]["missing"] == []
        f.unlink()
        got = base.wait_for(lambda: s.get("/v1/services")["addons"]["missing"], "the placeholder back", timeout=15)
        assert got[0]["note"] == "krew not installed"
    finally:
        s.stop()


def test_a_stale_report_is_said_by_the_panel():
    import crissa_panel_addons as A
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "r.json"
        p.write_text(json.dumps({"level": "ok", "headline": "all quiet", "epoch": 1000.0}))
        assert A.read_status(str(p), 900, now=1500.0) == {"health": "ok", "note": "all quiet"}
        st = A.read_status(str(p), 900, now=1000.0 + 3600)
        assert st["health"] == "bad" and st["stale"] and "60 min" in st["note"]
        assert A.read_status(str(Path(t) / "none.json"), 900)["note"] == "no report yet"


def test_the_page_shows_the_placeholder_and_the_installed_tiles(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    jsdom = next((p for p in base.JSDOM if p and Path(p).is_dir()), "jsdom")
    out = subprocess.run([node, str(ROOT / "tests/js/crissa_panel_addons_sim.js")], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ, JSDOM_PATH=jsdom))
    if out.returncode == 3:
        pytest.skip("jsdom not installed: " + out.stderr.strip()[:200])
    assert out.returncode == 0, out.stderr
    g = json.loads(out.stdout)
    assert g["missing"]["heads"][0] == "Wall services" and g["missing"]["heads"][-1] == "krew services"
    assert "○ krew not installed" in g["missing"]["placeholder"]
    assert g["missing"]["word"] == "All normal"
    assert g["missing"]["ph_has_unit"] is False and g["missing"]["ph_tab"] is False
    assert g["missing"]["after_click"] == {"posts": 0, "opened": 0, "tape": "journal · every service"}
    assert g["installed"]["heads"][-1] == "krew services" and g["installed"]["placeholders"] == 0
    assert g["installed"]["tiles"] == ["Gateway", "Dashboard", "Board", "Keeper"]
    assert g["deck"] == {"work": True, "units": True, "log": True, "ears": True}
    assert g["installed"]["keeper"] == {"run": True, "off": "Off", "off_hidden": False}
    assert g["installed"]["link_opened"] == ["http://127.0.0.1:9999/board"]
    assert g["installed"]["open_key"] and g["installed"]["opened"][-1] == "http://127.0.0.1:9999/"
    assert g["installed"]["gateway"] == {"open_key": False, "restart_key": True, "tape": "journal · agent-gateway"}


def test_sections_run_and_off_follow_the_contract(tmp_path, monkeypatch):
    """A manifest's sections, run and off (PANEL-CONTRACT part 2). A bad key is an errors row and is not drawn.
    The tile still draws. A v1 manifest with no sections stays in the settings section."""
    import crissa_panel_addons as A

    home = tmp_path / "addon-home"
    home.mkdir()
    monkeypatch.setenv("KREW_HOME", str(home))
    off = home / "keeper.off"
    outside = tmp_path / "elsewhere.off"
    outside.write_text("")
    link = home / "link.off"
    link.symlink_to(outside)
    panel_d = tmp_path / "panel.d"
    panel_d.mkdir()
    (panel_d / "a-v1.json").write_text(json.dumps({
        "schema": "crissa-panel-addon/v1", "id": "old", "title": "Old",
        "tiles": [{"unit": "agent-gateway", "label": "Gateway"}],
    }))
    (panel_d / "b-sections.json").write_text(json.dumps({
        "schema": "crissa-panel-addon/v1", "id": "krew", "title": "Krew",
        "sections": ["krew services"],
        "tiles": [
            {"id": "keeper", "unit": "krew-keeper.timer", "label": "Keeper", "section": "krew services",
             "run": {"unit": "krew-keeper.service"}, "off": {"file": str(off)}},
            {"id": "stray", "unit": "other.service", "label": "Stray", "section": "not listed"},
            {"id": "badrun", "unit": "bad.service", "label": "Bad run", "section": "krew services",
             "run": {"unit": "bad unit"}},
            {"label": "Bad file", "href": "http://127.0.0.1:9/", "section": "krew services",
             "off": {"file": str(outside)}},
            {"label": "Bad link", "href": "http://127.0.0.1:9/x", "section": "krew services",
             "off": {"file": str(link)}},
            {"id": "boot", "unit": "boot.service", "label": "Boot", "section": "krew services",
             "off": {"unit": "boot.timer"}},
            {"unit": "plain.service", "label": "Plain"},
        ],
    }))
    cfg = {"addons": {"section": "krew services", "home_env": "KREW_HOME", "paths": [str(panel_d)],
                      "slots": [{"id": "absent", "title": "Absent", "missing": "krew not installed"}]}}
    reg = [{"unit": "crissa-wall.service", "role": "service", "scope": "user"},
           {"unit": "agent-gateway.service", "role": "service", "scope": "user"}]
    v = A.view(cfg, reg)
    by = {t.get("id") or t["label"]: t for t in v["tiles"]}
    assert by["Gateway"]["section"] == "krew services" and by["Gateway"]["addon"] == "old"
    assert "crissa-wall.service" not in {t.get("unit") for t in v["tiles"]}
    assert by["keeper"]["section"] == "krew services"
    assert by["keeper"]["run"] == {"unit": "krew-keeper.service"}
    assert by["keeper"]["off"] == {"file": str(off)} and by["keeper"]["off_now"] is False
    assert by["stray"]["section"] == "Krew" and "run" not in by["stray"]
    assert "run" not in by["badrun"] and by["badrun"]["label"] == "Bad run"
    assert "off" not in by["Bad file"] and "off" not in by["Bad link"]
    assert by["boot"]["off"] == {"unit": "boot.timer"}
    assert by["Plain"]["section"] == "Krew"
    whys = " | ".join(e["why"] for e in v["errors"])
    assert "run needs a unit name" in whys
    assert "off file must be an absolute path under the add-on's home" in whys
    assert v["sections"] == ["krew services", "Krew"]
    assert v["missing"] == [{"slot": "absent", "label": "Absent", "note": "krew not installed",
                             "state": "off", "mark": "○", "section": "krew services", "placeholder": True}]
    off.write_text("")
    again = {t["id"]: t for t in A.view(cfg, reg)["tiles"] if t.get("id")}
    assert again["keeper"]["off_now"] is True
    monkeypatch.setenv("KREW_HOME", "/")
    assert A.off_file_ok(str(off), "KREW_HOME") is None


def test_run_now_and_the_off_file_follow_the_manifest(addon_machine):
    home = addon_machine.state / "addon-home"
    home.mkdir()
    addon_machine.env["KREW_HOME"] = str(home)
    off = home / "keeper.off"
    addon_machine.set_units("user", **{
        "krew-keeper.service": svc(active="inactive", exec_path="/opt/agent/bin/keeper"),
        "boot.service": svc(exec_path="/opt/agent/bin/boot"),
    })
    install(addon_machine, manifest(
        sections=["krew services"],
        tiles=[{"id": "keeper", "unit": "krew-keeper.service", "label": "Keeper",
                "section": "krew services", "run": {"unit": "krew-keeper.service"},
                "off": {"file": str(off)}},
               {"id": "boot", "unit": "boot.service", "label": "Boot",
                "section": "krew services", "off": {"unit": "boot.service"}}]))
    s = base.Server(addon_machine)
    try:
        d = s.get("/v1/services")
        tile = next(r for r in d["services"] if r.get("id") == "keeper")
        assert tile["section"] == "krew services" and tile["run"]["unit"] == "krew-keeper.service"
        assert tile["off"]["file"] == str(off) and tile["off_now"] is False
        wall = [r for r in d["services"] if r["unit"] == "crissa-wall"]
        assert wall and wall[0]["section"] == "Wall services" and "addon" not in wall[0]
        code, body = s.post("/v1/action", {"action": "run_now", "id": "keeper"})
        assert code == 200 and body["ok"] and body["unit"] == "krew-keeper.service"
        assert ["user", "start", "krew-keeper.service"] in addon_machine.systemd()["calls"]
        code, body = s.post("/v1/action", {"action": "run_now", "id": "nope"})
        assert code == 400
        code, body = s.post("/v1/action", {"action": "mark_off", "id": "keeper"})
        assert code == 200 and body["ok"] and body["off_now"] is True and off.is_file()
        code, body = s.post("/v1/action", {"action": "mark_on", "id": "keeper"})
        assert code == 200 and body["ok"] and body["off_now"] is False and not off.exists()
        code, body = s.post("/v1/action", {"action": "mark_off", "id": "boot"})
        assert code == 200 and body["ok"] and body["off_now"] is True
        assert ["user", "disable", "--now", "boot.service"] in addon_machine.systemd()["calls"]
        code, body = s.post("/v1/action", {"action": "mark_on", "id": "boot"})
        assert code == 200 and body["ok"] and body["off_now"] is False
        assert ["user", "enable", "--now", "boot.service"] in addon_machine.systemd()["calls"]
    finally:
        s.stop()
