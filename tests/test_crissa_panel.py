"""The public Control Panel (package crissa-panel, 2026-10-06): a fresh install's panel discovers ITS OWN services,
All OS Off stops every one but the panel and Turn On All Services brings back exactly that, Park GPU is a toggle
where a GPU step is configured, the header never says "All normal" while things are paused, and nothing of one
machine is spelled in the code.

Every test builds a fresh install in a temp folder from the files the package ships (no config/house-machine.json,
no avatar manifest), and a stand-in systemd (tests/crissa_panel_fake/) on the PATH of its own subprocesses only. No
test touches the machine's systemd, its units or its ports beyond one free loopback port per server test.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "crissa_panel_fake"
SHIPPED = ("lib/crissa_panel_conf.py", "lib/crissa_units.py", "lib/crissa_os_power.py", "lib/crissa_home.py",
           "lib/crissa_panel_addons.py", "lib/crissa_voice_panel.py", "lib/crissa_guard.py",
           "lib/glass_prefs.py", "lib/house_wall_prefs.py",
           "lib/crissa_watch.py", "config/crissa-watch.json", "lib/crissa_service.py",
           "lib/crissa_procs.py", "lib/crissa_addon_link.py", "lib/crissa_capability.py",
           "scripts/crissa-panel-server.py", "scripts/crissa-os-power", "scripts/crissa-doc-window.sh",
           "display/crissa-panel.html", "display/crissa-doc.html", "config/crissa-panel.json")
CODE = [f for f in SHIPPED if not f.startswith("config/")]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Machine:
    """A fresh install + a stand-in systemd. `units` maps scope -> {unit: props}."""

    def __init__(self, tmp: Path, units: dict, *, unreachable=(), extra_cfg: dict | None = None):
        self.root = tmp / "install"
        for rel in SHIPPED:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, self.root / rel)
        self.state = tmp / "state"
        self.state.mkdir()
        self.unit_dir = tmp / "unit-files"
        self.unit_dir.mkdir()
        self.sysd = tmp / "systemd.json"
        self.sysd.write_text(json.dumps({"units": units, "unreachable": list(unreachable), "calls": []}))
        cfg = {"registry": {"unit_dir": str(self.unit_dir)}}
        for k, v in (extra_cfg or {}).items():
            cfg[k] = dict(cfg.get(k) or {}, **v) if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
        (self.state / "panel.json").write_text(json.dumps(cfg))
        self.env = dict(os.environ, CRISSA_HOME=str(self.state), FAKE_SYSTEMD_STATE=str(self.sysd),
                        PATH=f"{FAKE}:{os.environ.get('PATH', '')}", PYTHONDONTWRITEBYTECODE="1")
        for k in ("CRISSA_PANEL_CONFIG", "CRISSA_PORT_PANEL", "CRISSA_PORT_OFFSET", "DISPLAY", "WAYLAND_DISPLAY"):
            self.env.pop(k, None)
        # these tests pin the 10-06 Off (the panel kept); the whole Off of 2026-11-01 (the panel and krew too, a pack's
        # default) has its own file, tests/test_all_os_off_whole_20261101.py, which sets the switch on
        self.env["CRISSA_OS_OFF_WHOLE"] = "0"

    def py(self, code: str) -> dict:
        out = subprocess.run([sys.executable, "-c", "import json,sys; sys.path.insert(0, 'lib')\n" + code],
                             cwd=self.root, env=self.env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout.strip().splitlines()[-1])

    def systemd(self) -> dict:
        return json.loads(self.sysd.read_text())

    def set_units(self, scope: str, **changes):
        st = self.systemd()
        for u, props in changes.items():
            if props is None:
                st["units"][scope].pop(u, None)
            else:
                st["units"].setdefault(scope, {}).setdefault(u, {}).update(props)
        self.sysd.write_text(json.dumps(st))


def svc(active="active", exec_path="", **kw) -> dict:
    d = {"ActiveState": active, "SubState": "running" if active == "active" else "dead", "Type": "simple",
         "UnitFileState": "enabled", "Description": kw.pop("desc", "a test unit"),
         "ExecStart": f"{{ path={exec_path} ; argv[]=/usr/bin/python3 {exec_path} ; }}" if exec_path else ""}
    d.update(kw)
    return d


def fresh_units(m_root: Path) -> dict:
    """The fresh install's own units, plus units that are NOT its own (another app, another machine's names)."""
    return {"user": {
        "crissa-panel.service": svc(exec_path=f"{m_root}/scripts/crissa-panel-server.py"),
        "crissa-wall.service": svc(exec_path=f"{m_root}/scripts/house-wall-ascii"),
        "crissa-tts.service": svc(exec_path=f"{m_root}/modules/voice/scripts/crissa-tts.py"),
        "my-voice-player.service": svc(exec_path=f"{m_root}/scripts/house-conductor.py"),   # exec rule
        "crissa-night.timer": {"ActiveState": "active", "SubState": "waiting", "UnitFileState": "enabled",
                               "Triggers": "crissa-night.service"},
        "crissa-night.service": svc(active="inactive", Type="oneshot", UnitFileState="static",
                                    TriggeredBy="crissa-night.timer"),
        # not this install's: another program, and names another machine uses for its own services
        "pipewire.service": svc(exec_path="/usr/bin/pipewire"),
        "house-conductor.service": svc(exec_path="/srv/elsewhere/house-conductor.py"),
        "voiceonly-home.service": svc(exec_path="/srv/elsewhere/voiceonly-home-server.py"),
    }, "system": {"cron.service": svc(exec_path="/usr/sbin/cron")}}


@pytest.fixture
def machine(tmp_path):
    root = tmp_path / "install"
    return Machine(tmp_path, fresh_units(root))


def test_the_shipped_defaults_watch_only_the_install_itself():
    d = json.loads((ROOT / "config/crissa-panel.json").read_text())
    assert d["registry"]["name_prefixes"] == ["crissa-"]
    assert d["registry"]["exec_markers"] == ["{root}"]
    assert d["registry"]["scopes"] == ["user"]
    assert not d["registry"]["unit_dir_rule"] and not d["registry"]["system_user_rule"]
    assert d["tiles"] == [] and d["gpu"] == {} and d["self_unit"] == "crissa-panel.service"


# ---- settings ----------------------------------------------------------------------------------------------------
def test_settings_layers_and_placeholders(machine):
    got = machine.py("import crissa_panel_conf as c; d=c.load(); print(json.dumps(d))")
    assert got["_sources"] == ["builtin", "shipped", "install"]           # a fresh install has no house file
    assert got["registry"]["exec_markers"] == [str(machine.root)]
    assert got["registry"]["state_dir"] == str(machine.state / "units")
    assert got["port"] == 8097
    machine.env["CRISSA_PORT_OFFSET"] = "100"
    assert machine.py("import crissa_panel_conf as c; print(json.dumps(c.load()['port']))") == 8197
    machine.env["CRISSA_PORT_PANEL"] = "18123"
    assert machine.py("import crissa_panel_conf as c; print(json.dumps(c.load()['port']))") == 18123


def test_a_house_file_beside_the_code_is_a_layer_and_the_install_still_wins(tmp_path, machine):
    (machine.root / "config/house-machine.json").write_text(json.dumps(
        {"panel": {"port": 9999, "title": "House", "registry": {"name_prefixes": ["x-"]}}}))
    got = machine.py("import crissa_panel_conf as c; print(json.dumps(c.load()))")
    assert got["_sources"] == ["builtin", "shipped", "house", "install"]
    assert got["port"] == 9999 and got["title"] == "House" and got["registry"]["name_prefixes"] == ["x-"]
    assert got["registry"]["unit_dir"] == str(machine.unit_dir)        # the install's own key still wins


# ---- discovery ---------------------------------------------------------------------------------------------------
def test_a_fresh_install_discovers_its_own_services_and_nothing_else(machine):
    rows = machine.py("import crissa_units as u; print(json.dumps(u.discover()))")
    found = {r["unit"]: r["why"] for r in rows}
    assert set(found) == {"crissa-panel.service", "crissa-wall.service", "crissa-tts.service",
                          "my-voice-player.service", "crissa-night.timer", "crissa-night.service"}
    assert found["my-voice-player.service"] == f"exec into {machine.root}"
    assert found["crissa-panel.service"] == "name crissa-*"
    roles = {r["unit"]: r["role"] for r in rows}
    assert roles["crissa-night.timer"] == "timer" and roles["crissa-night.service"] == "job"
    assert not any(r["scope"] == "system" for r in rows), "a fresh install does not read the system scope"


def test_new_and_removed_units_are_noted_not_silent(machine):
    first = machine.py("import crissa_units as u; print(json.dumps(u.refresh(by='t')))")
    assert first["new"] == [] and first["n"] == 6              # the first look is a baseline
    machine.set_units("user", **{"crissa-extra.service": svc(), "crissa-tts.service": None})
    second = machine.py("import crissa_units as u; print(json.dumps(u.refresh(by='t')))")
    assert second["new"] == ["crissa-extra.service"] and second["removed"] == ["crissa-tts.service"]
    reg = json.loads((machine.state / "units/registry.json").read_text())
    assert [r for r in reg["units"] if r["unit"] == "crissa-extra.service"][0]["new_since"]


def test_without_a_systemd_user_session_the_unit_files_are_read_and_it_says_so(tmp_path):
    root = tmp_path / "install"
    m = Machine(tmp_path, {"user": {}}, unreachable=["user"])
    (m.unit_dir / "crissa-panel.service").write_text(
        f"[Unit]\nDescription=panel\n[Service]\nExecStart=/usr/bin/python3 {root}/scripts/crissa-panel-server.py\n")
    (m.unit_dir / "other.service").write_text("[Service]\nExecStart=/usr/bin/true\n")
    (m.unit_dir / "crissa-x@.service").write_text("[Service]\nExecStart=/usr/bin/true\n")
    rows = m.py("import crissa_units as u; print(json.dumps(u.discover()))")
    assert [r["unit"] for r in rows] == ["crissa-panel.service"]
    assert rows[0]["systemd"] == "unreachable" and "bus" in rows[0]["systemd_why"]


# ---- All OS Off / Turn On All Services ---------------------------------------------------------------------------
def test_all_off_stops_everything_but_the_panel_and_on_restores_exactly(machine):
    machine.set_units("user", **{"crissa-wall.service": {"ActiveState": "inactive", "SubState": "dead"}})
    off = machine.py("from crissa_os_power import Power; print(json.dumps(Power().all_off(settle_s=0, sleep=lambda s: None)))")
    assert off["ok"], off
    assert sorted(s["unit"] for s in off["stopped"]) == ["my-voice-player.service", "crissa-night.timer",
                                                         "crissa-tts.service"]
    assert [k["unit"] for k in off["kept"]] == ["crissa-panel.service"]
    assert "crissa-wall.service" in off["already_off"]
    st = machine.systemd()["units"]["user"]
    assert st["crissa-panel.service"]["ActiveState"] == "active"
    assert st["pipewire.service"]["ActiveState"] == "active", "Off stopped a unit that is not the install's"
    stops = [c for c in machine.systemd()["calls"] if c[1:2] == ["stop"]]
    assert stops[0][2:] == ["crissa-night.timer"], "restarters go first"
    summ = machine.py("from crissa_os_power import Power; print(json.dumps(Power().summary()))")
    assert summ["in_force"] and summ["n"] == 3
    on = machine.py("from crissa_os_power import Power; print(json.dumps(Power().all_on(settle_s=0, sleep=lambda s: None)))")
    assert on["ok"], on
    assert sorted(b["unit"] for b in on["back"]) == ["my-voice-player.service", "crissa-night.timer", "crissa-tts.service"]
    st = machine.systemd()["units"]["user"]
    assert st["crissa-wall.service"]["ActiveState"] == "inactive", "On started a unit that was off before Off"
    starts = [c for c in machine.systemd()["calls"] if c[1:2] == ["start"]]
    assert starts[-1][2:] == ["crissa-night.timer"], "restarters come back last"
    assert machine.py("from crissa_os_power import Power; print(json.dumps(Power().summary()))") == {"in_force": False}


def test_a_unit_that_will_not_stop_is_named_with_its_reason(machine):
    st = machine.systemd()
    st["refuse_stop"] = ["crissa-tts.service"]
    machine.sysd.write_text(json.dumps(st))
    off = machine.py("from crissa_os_power import Power; print(json.dumps(Power().all_off(settle_s=0, sleep=lambda s: None)))")
    assert not off["ok"]
    bad = {f["unit"]: f["why"] for f in off["failed"]}
    assert "polkit" in bad["crissa-tts.service"]
    assert "did NOT stop" in off["headline"]


def test_a_second_off_adds_to_the_snapshot_and_never_shrinks_it(machine):
    machine.py("from crissa_os_power import Power; print(json.dumps(Power().all_off(settle_s=0, sleep=lambda s: None)))")
    machine.set_units("user", **{"crissa-wall.service": {"ActiveState": "active"}})
    machine.py("from crissa_os_power import Power; print(json.dumps(Power().all_off(settle_s=0, sleep=lambda s: None)))")
    snap = json.loads((machine.state / "panel/os-off.snapshot.json").read_text())
    assert sorted(u["unit"] for u in snap["units"]) == ["my-voice-player.service", "crissa-night.timer",
                                                        "crissa-tts.service", "crissa-wall.service"]


def test_a_worker_named_in_the_settings_may_not_flip_the_switch(tmp_path):
    root = tmp_path / "install"
    m = Machine(tmp_path, fresh_units(root), extra_cfg={"refuse_env": ["AGENT_WORKER_TASK"]})
    m.env["AGENT_WORKER_TASK"] = "t_1"
    off = m.py("from crissa_os_power import Power; print(json.dumps(Power().all_off(settle_s=0, sleep=lambda s: None)))")
    assert not off["ok"] and "AGENT_WORKER_TASK" in off["error"]
    assert not [c for c in m.systemd()["calls"] if c[1:2] == ["stop"]]


def gpu_cfg(tmp: Path) -> dict:
    """A machine with a GPU step: a stand-in command that records its args and keeps a mode file."""
    tool = tmp / "gpu-mode"
    tool.write_text("#!/usr/bin/env python3\nimport json,sys,pathlib\nd=pathlib.Path(sys.argv[0]).parent\n"
                    "(d/'gpu-calls.txt').open('a').write(' '.join(sys.argv[1:])+'\\n')\n"
                    "m=sys.argv[1]; f=d/'gpu-mode.txt'; s=d/'gpu-state.json'\n"
                    "prev=f.read_text().strip() if f.exists() else 'normal'\n"
                    "cur=json.loads(s.read_text()) if s.exists() else {}\n"
                    "if m=='off': s.write_text(json.dumps({'mode':'off','prev_mode':prev,'prev_recipe':cur.get('recipe')}))\n"
                    "f.write_text(m+'\\n')\n")
    tool.chmod(0o755)
    (tmp / "gpu-mode.txt").write_text("render\n")
    (tmp / "gpu-state.json").write_text(json.dumps({"mode": "render", "recipe": "fast"}))
    return {"gpu": {"label": "the model servers", "cmd": [str(tool)], "park_args": ["off"], "default_mode": "normal",
                    "modes": {"normal": {"label": "Standard", "args": ["normal"]},
                              "render": {"label": "Render", "args": ["render", "{recipe}"], "recipe_default": "slow"}},
                    "mode_file": str(tmp / "gpu-mode.txt"), "state_file": str(tmp / "gpu-state.json")}}


def test_with_a_gpu_step_off_parks_it_and_on_returns_to_the_mode_it_left(tmp_path):
    root = tmp_path / "install"
    m = Machine(tmp_path, fresh_units(root), extra_cfg=gpu_cfg(tmp_path))
    off = m.py("from crissa_os_power import Power; print(json.dumps(Power().all_off(settle_s=0, sleep=lambda s: None)))")
    assert off["ok"] and off["gpu_policy"] == "park" and off["return_mode"] == "render"
    on = m.py("from crissa_os_power import Power; print(json.dumps(Power().all_on(settle_s=0, sleep=lambda s: None)))")
    assert on["ok"]
    assert (tmp_path / "gpu-calls.txt").read_text().split("\n")[:2] == ["off", "render fast"]


# ---- the server, as a fresh install runs it ----------------------------------------------------------------------
class Server:
    def __init__(self, m: Machine):
        self.port = free_port()
        self.m = m
        self.proc = subprocess.Popen([sys.executable, str(m.root / "scripts/crissa-panel-server.py"),
                                      "--port", str(self.port)], cwd=m.root, env=m.env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            try:
                self.get("/health")
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("the panel did not start: " + (self.proc.stderr.read() if self.proc.stderr else ""))

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=20) as r:
            body = r.read()
            return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body

    def post(self, path, obj, origin=None):
        req = urllib.request.Request(self.url(path), data=json.dumps(obj).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@pytest.fixture
def server(machine):
    s = Server(machine)
    yield s
    s.stop()


def wait_for(fn, what, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for {what}")


def test_the_page_and_the_tiles_come_from_this_install(server):
    page = server.get("/")
    assert b"<title>Control Panel</title>" in page
    d = server.get("/v1/services")
    units = [s["unit"] for s in d["services"]]
    assert units[0] == "crissa-panel"
    assert set(units) == {"crissa-panel", "crissa-wall", "crissa-tts", "my-voice-player"}
    assert d["gpu"] is None and d["fleet"]["total"] == 4
    assert {s["section"] for s in d["services"]} == {"Wall services"}
    # a panel installed without the voice code has no Voices section, and says why without a fault
    assert d["voices"]["tiles"] == [] and "no voice tier code" in d["voices"]["absent"]
    assert server.get("/v1/voices")["tiles"] == []


def test_the_switch_round_trip_over_http(server, machine):
    code, d = server.post("/v1/action", {"action": "os_off"})
    assert code == 200 and d["started"]
    wait_for(lambda: server.get("/v1/services")["os_off"].get("in_force"), "Off in force")
    wait_for(lambda: (server.get("/v1/os_power")["last"] or {}).get("action") == "off", "the Off report")
    view = server.get("/v1/os_power")
    assert view["last"]["ok"], view["last"]
    time.sleep(3.1)                                                     # the tile cache
    svc_ = server.get("/v1/services")
    tiles = {s["unit"]: s for s in svc_["services"]}
    assert tiles["crissa-panel"]["health"] == "ok"
    assert tiles["crissa-tts"].get("parked") and tiles["crissa-tts"]["note"] == "parked in All OS Off"
    assert svc_["os_off"]["n"] == 4
    code, d = server.post("/v1/action", {"action": "os_on"})
    assert code == 200 and d["started"]
    wait_for(lambda: not server.get("/v1/os_power")["in_force"], "Off lifted")
    assert machine.systemd()["units"]["user"]["crissa-tts.service"]["ActiveState"] == "active"
    code, d = server.post("/v1/action", {"action": "os_on"})
    assert d.get("already")


def test_unit_keys_only_reach_the_install_units_and_never_stop_the_panel(server, machine):
    code, d = server.post("/v1/action", {"action": "restart", "unit": "pipewire"})
    assert code == 400
    code, d = server.post("/v1/action", {"action": "stop", "unit": "crissa-panel"})
    assert not d["ok"] and "Control Panel" in d["note"]
    code, d = server.post("/v1/action", {"action": "stop", "unit": "crissa-tts"})
    assert d["ok"] and machine.systemd()["units"]["user"]["crissa-tts.service"]["ActiveState"] == "inactive"
    code, d = server.post("/v1/action", {"action": "start", "unit": "crissa-tts.service"})
    assert d["ok"]


def test_a_page_of_another_origin_cannot_press_a_key(server):
    code, d = server.post("/v1/action", {"action": "os_off"}, origin="http://evil.example")
    assert code == 403
    code, d = server.post("/v1/action", {"action": "os_on"}, origin=f"http://127.0.0.1:{server.port}")
    assert code == 200


def test_journal_and_script_read_only_the_install_units(server):
    j = server.get("/v1/journal?unit=crissa-tts")
    assert j["ok"] and "fake journal line for crissa-tts.service" in j["lines"][0]
    assert not server.get("/v1/journal?unit=pipewire")["ok"]
    allj = server.get("/v1/journal?unit=all")
    assert any("crissa-wall.service" in ln for ln in allj["lines"])
    try:
        server.get("/v1/script?unit=../../etc/passwd")
        raise AssertionError("a path was accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_the_document_sheet_posts_reads_and_clears(server):
    assert server.get("/v1/doc")["doc"] is None
    code, d = server.post("/v1/screen", {"doc": "# Hello\nline", "doc_title": "T", "doc_ttl": 60})
    assert d["ok"] and d["doc"]
    got = server.get("/v1/doc")
    assert got["doc"].startswith("# Hello") and got["doc_title"] == "T"
    assert b"Document" in server.get("/doc")
    server.post("/v1/screen", {"clear": ["doc"]})
    assert server.get("/v1/doc")["doc"] is None


def test_the_registry_card_says_why_each_unit_counts(server):
    r = server.get("/v1/registry")
    assert r["n"] == 6 and r["systemd"] == "ok"
    assert {u["unit"]: u["why"] for u in r["units"]}["crissa-night.timer"].startswith(("name", "starts"))


def test_park_gpu_is_a_toggle_on_a_machine_with_a_gpu_step(tmp_path):
    root = tmp_path / "install"
    m = Machine(tmp_path, fresh_units(root), extra_cfg=gpu_cfg(tmp_path))
    s = Server(m)
    try:
        g = s.get("/v1/services")["gpu"]
        assert g["mode"] == "render" and [x["id"] for x in g["modes"]] == ["normal", "render"]
        code, d = s.post("/v1/action", {"action": "mode_off"})
        assert d["ok"] and d["mode"] == "off" and "return to Render" in d["note"]
        wait_for(lambda: (tmp_path / "gpu-mode.txt").read_text().strip() == "off", "the park")
        code, d = s.post("/v1/action", {"action": "mode_off"})        # pressed again: back to where it was
        assert d["ok"] and d["mode"] == "render"
        wait_for(lambda: (tmp_path / "gpu-mode.txt").read_text().strip() == "render", "the return")
        assert (tmp_path / "gpu-calls.txt").read_text().split("\n")[:2] == ["off", "render fast"]
    finally:
        s.stop()


def test_without_systemd_the_panel_still_starts_and_says_why(tmp_path):
    root = tmp_path / "install"
    m = Machine(tmp_path, {"user": {}}, unreachable=["user"])
    (m.unit_dir / "crissa-panel.service").write_text(
        f"[Service]\nExecStart=/usr/bin/python3 {root}/scripts/crissa-panel-server.py\n")
    (m.unit_dir / "crissa-wall.service").write_text("[Service]\nExecStart=/usr/bin/true\n")
    s = Server(m)
    try:
        d = s.get("/v1/services")
        assert d["systemd"] == "unreachable"
        tiles = {t["unit"]: t for t in d["services"]}
        assert tiles["crissa-wall"]["note"].startswith("systemd unreachable")
        assert s.get("/v1/registry")["systemd"] == "unreachable"
        view = s.get("/v1/os_power")
        assert view["unknown"] == ["crissa-wall.service"]
    finally:
        s.stop()


def test_the_unit_template_names_this_install_and_no_other(machine):
    out = subprocess.run([sys.executable, "scripts/crissa-panel-server.py", "--unit"], cwd=machine.root,
                         env=machine.env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert f"{machine.root}/scripts/crissa-panel-server.py" in out.stdout and ":8097" in out.stdout



def test_krew_panel_compat_routes_house_logs_security(server, machine):
    """krew-panel calls /v1/house, /v1/logs, /v1/security/summary — Crissa must answer them (2026-10-06)."""
    caps = server.get("/v1/caps")
    assert caps.get("house_compat") is True and set(caps.get("log_lanes") or []) == {"camera", "voice", "security"}
    house = server.get("/v1/house")
    assert house["ok"] and "svc" in house
    units = [s["unit"] for s in house["svc"]["services"]]
    assert "crissa-panel" in units and house["svc"]["fleet"]["total"] == len(units)
    assert house.get("battery") is None and house["svc"].get("announcer_muted") is False
    # voice lane: empty sources say so plainly (no house paths)
    voice = server.get("/v1/logs?lane=voice")
    assert voice["ok"] and voice["lane"] == "voice" and voice["lines"] == [] and "no feed" in (voice.get("note") or "")
    cam = server.get("/v1/logs?lane=camera")
    assert cam["ok"] and cam["lines"] == [] and "no camera" in (cam.get("note") or "")
    sec = server.get("/v1/logs?lane=security")
    assert sec["ok"] and "house panel" in (sec.get("note") or "")
    bad = server.get("/v1/logs?lane=/etc/passwd")
    assert not bad["ok"] and bad["lines"] == []
    # with a Crissa voice log present, the lane returns it
    logdir = machine.state / "logs"
    logdir.mkdir(exist_ok=True)
    (logdir / "ear.log").write_text("heard: hello from ear\n" * 3)
    voice2 = server.get("/v1/logs?lane=voice")
    assert voice2["ok"] and any("heard: hello from ear" in ln for ln in voice2["lines"])
    summary = server.get("/v1/security/summary")
    assert summary["ok"] and summary["incidents"] == 0 and "no security incident" in (summary.get("note") or "").lower()


def test_glass_prefs_on_house_and_post(server, machine):
    """Connect Walls: /v1/house carries glass; POST /v1/glass-prefs merges and bumps rev."""
    house = server.get("/v1/house")
    assert house["ok"] and isinstance(house.get("glass"), dict) and "rev" in house["glass"]
    g0 = server.get("/v1/glass-prefs")
    assert "rev" in g0 and "density" in g0
    rev0 = int(g0["rev"])
    # flip a shared key the contract allows
    key = "vitals_folded"
    want = not bool(g0.get(key))
    code, g1 = server.post("/v1/glass-prefs", {key: want})
    assert code == 200 and int(g1["rev"]) >= rev0 and g1.get(key) is want
    house2 = server.get("/v1/house")
    assert house2["glass"]["rev"] == g1["rev"] and house2["glass"].get(key) is want



# ---- the page, driven in jsdom ------------------------------------------------------------------------------------
JSDOM = [os.environ.get("JSDOM_PATH", ""), *(str(p) for pat in ("*/node_modules/jsdom", "*/*/node_modules/jsdom",
                                                               ".*/*/node_modules/jsdom") for p in Path.home().glob(pat))]


def test_the_page_header_never_says_all_normal_while_off(tmp_path):
    node = shutil.which("node")
    sim = ROOT / "tests/js/crissa_panel_sim.js"
    if not node:
        pytest.skip("node not installed")
    jsdom = next((p for p in JSDOM if p and Path(p).is_dir()), "jsdom")
    out = subprocess.run([node, str(sim)], capture_output=True, text=True, timeout=60,
                         env=dict(os.environ, JSDOM_PATH=jsdom))
    if out.returncode == 3:
        pytest.skip("jsdom not installed: " + out.stderr.strip()[:200])
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["normal"]["word"] == "All normal"
    assert got["off"]["word"] == "OS off · 2 paused" and "Turn On All Services" in got["off"]["key"]
    assert got["off"]["tile_parked"] is True
    assert got["nogpu_keys"] == 0 and got["gpu_keys"] == ["Standard", "Render", "Park GPU"]
    assert got["parked_word"] == "GPU parked"
    assert got["off_click"] == "os_on"
