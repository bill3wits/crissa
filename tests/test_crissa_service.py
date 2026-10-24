"""Who restarts the launcher itself (2026-10-06): its service (lib/crissa_service), installed by Init, removed by
--uninstall, its state and the reason it was started again on the Control Panel's launcher tile. Every test writes
into tmp_path and fakes systemctl / launchctl: nothing here touches a real service manager."""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_service as S  # noqa: E402

GONE = 2 ** 22 + 12345      # a pid that is not running


class Fake:
    """systemctl / launchctl / journalctl: records each call, answers from a table keyed by the verb."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], dict(answers or {})

    def __call__(self, argv, timeout=0):
        self.calls.append(list(argv))
        key = argv[2] if argv[:2] == ["systemctl", "--user"] else argv[1] if len(argv) > 1 else argv[0]
        return self.answers.get(key, (0, "", ""))


def _env(tmp_path):
    return {"CRISSA_SYSTEMD_USER_DIR": str(tmp_path / "units"), "CRISSA_LAUNCHAGENTS_DIR": str(tmp_path / "agents")}


def test_the_unit_restarts_on_failure_and_never_retries_what_a_retry_cannot_fix(tmp_path):
    argv = S.launch_argv(["--face", "lila", "--plan", "--port-offset", "100", "--no-open"], python="/opt/py 3/bin/python")
    assert S.launch_argv(["--face", "lila"], python="/py")[2:] == ["--face", "lila"]    # --no-open only when given (T)
    assert argv[0] == "/opt/py 3/bin/python" and argv[1].endswith("scripts/crissa.py")
    assert argv[2:] == ["--face", "lila", "--port-offset", "100", "--no-open"]
    txt = S.unit_text(argv, home=tmp_path / "h", env={})
    for line in ("Restart=on-failure", "RestartSec=5", "RestartPreventExitStatus=2 3", "StartLimitBurst=5",
                 "WantedBy=default.target", 'Environment="CRISSA_SERVICE=systemd"',
                 'Environment="CRISSA_HOME=%s"' % (tmp_path / "h")):
        assert line + "\n" in txt, line
    assert 'ExecStart="/opt/py 3/bin/python" "%s"' % argv[1] in txt
    assert "WorkingDirectory=%s\n" % S.ROOT in txt                 # a bare path: systemd takes no quotes there
    assert '"100%"' not in S._sd_quote("100%") and S._sd_quote("100%") == '"100%%"'


def test_the_mac_agent_keeps_a_failing_launcher_alive(tmp_path):
    d = plistlib.loads(S.plist_bytes(["/usr/bin/python3", "x/crissa.py", "--no-open"], home=tmp_path, env={}))
    assert d["Label"] == "com.crissa.launcher" and d["RunAtLoad"] is False      # login start is a choice (2026-10-10)
    assert plistlib.loads(S.plist_bytes(["/usr/bin/python3"], home=tmp_path, env={"CRISSA_LOGIN": "1"}))["RunAtLoad"]
    assert d["KeepAlive"] == {"SuccessfulExit": False} and d["ThrottleInterval"] == 10
    assert d["EnvironmentVariables"]["CRISSA_SERVICE"] == "launchd"
    assert d["StandardOutPath"] == str(tmp_path / "logs" / "launcher-service.log")
    assert S.service_rc(3, env={"CRISSA_SERVICE": "launchd"}) == 0          # launchd cannot be told "not this code"
    assert S.service_rc(1, env={"CRISSA_SERVICE": "launchd"}) == 1
    assert S.service_rc(3, env={"CRISSA_SERVICE": "systemd"}) == 3          # systemd: RestartPreventExitStatus


def test_install_enables_it_and_starts_it_only_when_no_launcher_runs(tmp_path):
    env, run = _env(tmp_path), Fake()
    out = S.install(["--no-voice"], home=tmp_path, run=run, platform="linux", env=env, python="/py")
    f = tmp_path / "units" / "crissa-launcher.service"
    assert out["ok"] and out["started"] and f.exists() and "--no-voice" in f.read_text()
    # START CRISSA WHEN I LOG IN is the person's choice, off unless chosen (2026-10-10, .101 finding L)
    assert run.calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "disable", S.UNIT],
                         ["systemctl", "--user", "start", S.UNIT]]
    run = Fake()
    S.install(["--no-voice"], home=tmp_path, run=run, platform="linux", env=dict(env, CRISSA_LOGIN="1"), python="/py")
    assert ["systemctl", "--user", "enable", S.UNIT] in run.calls
    S.launcher_started(tmp_path, env={})                       # a launcher started by hand runs now (this pid)
    run = Fake()
    out = S.install([], home=tmp_path, run=run, platform="linux", env=env, python="/py")
    assert out["ok"] and not out["started"] and ["systemctl", "--user", "start", S.UNIT] not in run.calls
    assert "not started at login" in out["note"] and "Restart on the launcher tile" in out["note"]
    run = Fake({"disable": (1, "", "Failed to connect to bus")})
    out = S.install([], home=tmp_path, run=run, platform="linux", env=env)
    assert not out["ok"] and "Failed to connect to bus" in out["note"]


def test_install_on_a_mac_writes_the_agent_and_loads_it_when_nothing_runs(tmp_path):
    env, run = _env(tmp_path), Fake()
    out = S.install([], home=tmp_path, run=run, platform="darwin", env=env, python="/py")
    f = tmp_path / "agents" / "com.crissa.launcher.plist"
    assert out["ok"] and out["started"] and f.exists()
    assert run.calls == [["launchctl", "bootstrap", "gui/%d" % os.getuid(), str(f)],
                         ["launchctl", "kickstart", "gui/%d/%s" % (os.getuid(), S.LABEL)]]   # RunAtLoad off: run it now
    assert S.install([], run=Fake(), platform="win32", env=env)["ok"] is False


def test_uninstall_stops_disables_and_removes_it(tmp_path):
    env = _env(tmp_path)
    S.install([], home=tmp_path, run=Fake(), platform="linux", env=env)
    run = Fake()
    out = S.uninstall(run=run, platform="linux", env=env)
    assert out["removed"] and not (tmp_path / "units" / S.UNIT).exists()
    assert run.calls == [["systemctl", "--user", "disable", "--now", S.UNIT], ["systemctl", "--user", "daemon-reload"]]
    assert "no launcher service was installed" in S.uninstall(run=Fake(), platform="linux", env=env)["note"]
    S.install([], home=tmp_path, run=Fake(), platform="darwin", env=env)
    run = Fake()
    assert S.uninstall(run=run, platform="darwin", env=env)["removed"]
    assert run.calls == [["launchctl", "bootout", "gui/%d/com.crissa.launcher" % os.getuid()]]


def test_each_run_records_how_the_last_one_ended(tmp_path):
    d = S.launcher_started(tmp_path, env={"CRISSA_SERVICE": "systemd"}, pid=GONE)
    assert d["runs"] == 1 and d["previous"] is None and d["under"] == "systemd"
    S.launcher_ended(1, "the player stopped (exit -9): its log ends: boom", tmp_path)
    d = S.launcher_started(tmp_path, env={"CRISSA_SERVICE": "systemd"}, pid=GONE + 1)
    assert d["runs"] == 2 and d["previous"]["exit_code"] == 1 and d["previous"]["why"].startswith("the player stopped")
    d = S.launcher_started(tmp_path, env={}, pid=GONE + 2)       # the one before never wrote its end (SIGKILL)
    assert d["previous"]["exit_code"] is None and "ended without a word" in d["previous"]["why"]
    log = (tmp_path / "logs" / "launcher.log").read_text()
    assert "end (pid %d, exit 1): the player stopped" % GONE in log and "run 3" in log


def test_a_second_launcher_keeps_the_running_ones_record(tmp_path):
    S.launcher_started(tmp_path, env={}, pid=os.getpid())         # started by hand, running
    shadow = S.launcher_started(tmp_path, env={"CRISSA_SERVICE": "systemd"}, pid=GONE)
    assert shadow["shadow"] and shadow["held_by"] == os.getpid()
    S.launcher_ended(3, "port taken, nothing started: player 8100", tmp_path, shadow=shadow)
    rec = S.read_launcher(tmp_path)
    assert rec["pid"] == os.getpid() and rec["alive"] and "ended_at" not in rec
    assert rec["refused"]["why"].startswith("port taken") and rec["refused"]["under"] == "systemd"


def _show(**kv):
    base = {"ActiveState": "active", "SubState": "running", "NRestarts": "0", "Result": "success",
            "ExecMainStatus": "0", "UnitFileState": "enabled"}
    base.update(kv)
    return (0, "".join("%s=%s\n" % x for x in base.items()), "")


def test_the_tile_says_why_systemd_started_it_again(tmp_path):
    env = _env(tmp_path)
    S.install([], home=tmp_path, run=Fake(), platform="linux", env=env)
    S.launcher_started(tmp_path, env={"CRISSA_SERVICE": "systemd"}, pid=GONE)
    S.launcher_ended(1, "the player stopped (exit -9): its log ends: segfault", tmp_path)
    S.launcher_started(tmp_path, env={"CRISSA_SERVICE": "systemd"}, pid=os.getpid())
    st = S.status(home=tmp_path, run=Fake({"show": _show(NRestarts="1")}), platform="linux", env=env)
    assert st["installed"] and st["enabled"] and st["restarts"] == 1
    health, note = S.describe(st)
    assert health == "warn" and "restarted by it ×1" in note and "the player stopped (exit -9)" in note
    S.launcher_ended(1, "the player did not come up (exit 1): its log ends: ImportError", tmp_path)
    st = S.status(home=tmp_path, run=Fake({"show": _show(ActiveState="failed", SubState="failed", NRestarts="5",
                                                         Result="exit-code", ExecMainStatus="1")}),
                  platform="linux", env=env)
    health, note = S.describe(st)
    assert health == "bad" and "stopped retrying" in note and "ImportError" in note


def test_the_tile_says_a_hand_started_launcher_has_no_one_to_restart_it(tmp_path):
    env = _env(tmp_path)
    S.launcher_started(tmp_path, env={}, pid=os.getpid())
    health, note = S.describe(S.status(home=tmp_path, run=Fake(), platform="linux", env=env))
    assert health == "warn" and "not installed as a service" in note and "--service install" in note
    S.install([], home=tmp_path, run=Fake(), platform="linux", env=env)
    st = S.status(home=tmp_path, run=Fake({"show": _show(ActiveState="inactive", SubState="dead")}),
                  platform="linux", env=env)
    health, note = S.describe(st)
    assert health == "warn" and "started by hand" in note and "press Restart" in note
    health, note = S.describe(dict(st, manager=None))
    assert "no service manager" in note


def test_launchd_status_is_read_from_launchctl_print(tmp_path):
    env = _env(tmp_path)
    S.install([], home=tmp_path, run=Fake(), platform="darwin", env=env)
    out = "gui/501/com.crissa.launcher = {\n\tstate = running\n\truns = 3\n\tlast exit code = 1\n}\n"
    st = S.status(home=tmp_path, run=Fake({"print": (0, out, "")}), platform="darwin", env=env)
    assert st["active"] == "active" and st["restarts"] == 2 and st["exit_status"] == 1
    st = S.status(home=tmp_path, run=Fake({"print": (113, "", "Could not find service")}), platform="darwin", env=env)
    assert st["sub"] == "not loaded"


def test_restart_from_the_panel_hands_a_hand_started_launcher_over_to_the_service(tmp_path):
    env = _env(tmp_path)
    S.install([], home=tmp_path, run=Fake(), platform="linux", env=env)
    S.launcher_started(tmp_path, env={}, pid=os.getpid())
    stopped, run = [], Fake({"show": _show(ActiveState="inactive")})
    out = S.control("restart", home=tmp_path, run=run, platform="linux", env=env,
                    stop_hand_fn=lambda home: stopped.append(home) or {"ok": True, "stopped": True})
    assert out["ok"] and stopped == [tmp_path] and run.calls[-1] == ["systemctl", "--user", "start", S.UNIT]
    run = Fake()
    out = S.control("restart", home=tmp_path / "none", run=run, platform="linux", env={})
    assert not out["ok"] and "--service install" in out["note"]
    killed = []
    got = S.stop_hand(tmp_path, kill=lambda pid, sig: killed.append(pid), cmdline=lambda pid: "python3 other.py",
                      sleep=lambda s: None)
    assert not got["ok"] and killed == [] and "not the launcher" in got["note"]


def _panel(tmp_path):
    spec = importlib.util.spec_from_file_location("crissa_panel_s", ROOT / "scripts" / "crissa-panel-server.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CFG = {"_state": str(tmp_path), "port": 18097}
    return m


def test_the_panel_shows_the_launcher_tile_its_journal_and_drives_it(tmp_path, monkeypatch):
    env = _env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    m = _panel(tmp_path)
    m.run = Fake({"show": _show(NRestarts="2"), "--user": (0, "2026-10-06 journal line\n", "")})
    assert m.launcher_rows() == []                          # never ran, no service: no tile
    S.install([], home=tmp_path, run=Fake(), platform="linux", env=env)
    S.launcher_started(tmp_path, env={"CRISSA_SERVICE": "systemd"}, pid=GONE)
    S.launcher_ended(1, "the player stopped (exit -11): its log ends: x", tmp_path)
    monkeypatch.setenv("CRISSA_SERVICE", "systemd")
    S.launcher_started(tmp_path, pid=os.getpid())
    (row,) = m.launcher_rows()
    assert row["unit"] == "launcher" and row["section"] == "Crissa lanes" and row["restarts"] == 2
    assert row["health"] == "warn" and "the player stopped (exit -11)" in row["note"]
    assert row["label"] == "Crissa launcher (crissa-launcher.service)"
    lines = m.journal("launcher", 50)["lines"]
    assert any("end (pid %d, exit 1): the player stopped" % GONE in ln for ln in lines)
    m.run = Fake()
    code, out = m.do_action({"action": "restart", "unit": "launcher"})
    assert out["unit"] == "launcher" and m.run.calls[-1] == ["systemctl", "--user", "restart", S.UNIT]
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert "rows = [r for r in rows if conf.bare(r.get(\"unit\")) != conf.bare(crissa_service.UNIT)]" in src


def test_init_installs_the_service_when_a_first_boot_finishes(tmp_path):
    import crissa_init as I
    got = []
    fake = lambda args, home=None, start=None: got.append((args, start)) or {"ok": True, "manager": "systemd",
                                                                             "file": "f", "started": False, "note": "n"}
    assert I.install_service(env={}, install=fake)["skipped"].startswith("not started by the launcher")
    out = I.install_service(env={"CRISSA_LAUNCHER_ARGS": '["--face", "lila"]'}, install=fake)
    assert out == {"ok": True, "manager": "systemd", "file": "f", "started": False, "note": "n"}
    assert got == [(["--face", "lila"], True)]                   # started now: the tiles are the units
    got.clear()
    I.install_service(env={"CRISSA_LAUNCHER_ARGS": "[]", "CRISSA_DESK_HELD": "1"}, install=fake)
    assert got == [([], False)]                                  # this process holds the ports
    assert "CRISSA_SERVICE_INSTALL=0" == I.install_service(
        env={"CRISSA_LAUNCHER_ARGS": "[]", "CRISSA_SERVICE_INSTALL": "0"}, install=fake)["skipped"]
    assert "already runs as its service" in I.install_service(
        env={"CRISSA_LAUNCHER_ARGS": "[]", "CRISSA_SERVICE": "systemd"}, install=fake)["skipped"]
    src = (ROOT / "lib" / "crissa_init.py").read_text()
    assert "svc = install_service()" in src and '"service": svc' in src


def test_the_launcher_names_its_service_and_records_its_runs():
    src = (ROOT / "scripts" / "crissa.py").read_text()
    for s in ("crissa_service.launcher_started(", "crissa_service.launcher_ended(", "crissa_service.service_rc(rc)",
              '"--service", choices=("install", "status", "uninstall")', '"--uninstall"',
              "the player stopped (exit %s): its log ends: %s"):
        assert s in src, s


def test_service_output_is_one_json_document_and_reasons_keep_whole_lines(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("crissa_launcher_s", ROOT / "scripts" / "crissa.py")
    z = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(z)
    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    fake, real = Fake(), S.install                  # z.crissa_service is this same module: keep the real one
    monkeypatch.setattr(z.crissa_service, "install", lambda args, home=None: real(args, home=home, run=fake,
                                                                                  platform="linux"))
    assert z.main(["--service", "install", "--no-voice"]) == 0
    out = capsys.readouterr()
    d = json.loads(out.out)
    assert d["argv"][2:] == ["--no-voice"] and "installed" in out.err     # the service may open Setup once (T)
    log = tmp_path / "player.log"
    log.write_text("x" * 50 + "\n" + "\n".join("line %d" % i for i in range(80)) + "\n")
    assert z.why_tail(log).startswith("line ") and z.why_tail(log).endswith("line 79 | ")


def test_the_launcher_unit_carries_the_display_and_the_kill_switches_when_they_are_set(tmp_path):
    txt = S.unit_text(["/py", "crissa.py"], home=tmp_path, env={"DISPLAY": ":0", "CRISSA_SERVICE_HANDOFF": "0"})
    assert 'Environment="DISPLAY=:0"\n' in txt
    assert 'Environment="CRISSA_SERVICE_HANDOFF=0"\n' in txt
    bare = S.unit_text(["/py", "crissa.py"], home=tmp_path, env={})
    assert "DISPLAY" not in bare and "CRISSA_SERVICE_HANDOFF" not in bare


def test_a_child_unit_is_part_of_the_launcher_and_keeps_a_secret_out_of_the_unit_file(tmp_path):
    """2026-10-10 (.101): a unit carries its child's whole launch; a secret-named variable goes to a 0600 file the unit
    reads (EnvironmentFile=), never into the unit file, and still arrives. Nothing is dropped any more (RANDOM was)."""
    env = {"DISPLAY": ":0", "CRISSA_FACE": "crissa", "API_TOKEN": "nope", "HOUSE_X": "1", "RANDOM": "1"}
    out = S.write_desk_unit("panel", S.child_launch(["/py", "panel.py"], env, base={}), home=tmp_path,
                            units=tmp_path / "units")
    text = out["unit"].read_text()
    assert "PartOf=crissa-launcher.service\n" in text
    assert "TimeoutStopSec=10\n" in text
    assert "WantedBy" not in text
    run = Fake()
    assert S.stop_desk_units(["panel", "wall"], run=run) == [
        ["systemctl", "--user", "stop", "crissa-panel.service"],
        ["systemctl", "--user", "stop", "crissa-wall.service"]]
    assert "API_TOKEN" not in text and "nope" not in text
    ef = out["env_file"]
    assert ef is not None and (ef.stat().st_mode & 0o777) == 0o600 and (ef.parent.stat().st_mode & 0o777) == 0o700
    assert "EnvironmentFile=%s\n" % ef in text
    assert 'Environment="DISPLAY=:0"\n' in text
    assert 'Environment="CRISSA_FACE=crissa"\n' in text
    assert 'Environment="HOUSE_X=1"\n' in text and 'Environment="RANDOM=1"\n' in text
    assert S.read_unit(text)["env"] == env
    import pytest
    with pytest.raises(ValueError):
        S.desk_unit_text("panel", ["/py"], {"API_TOKEN": "x"}, home=tmp_path)     # never a unit that drops it


def test_desk_units_follow_the_service_and_stay_off_in_a_test():
    assert S.desk_units_on(env={"CRISSA_DESK_UNITS": "0"}, pytest_current="") is False
    assert S.desk_units_on(env={}, pytest_current="tests/x.py") is False
    assert S.desk_units_on(env={"CRISSA_DESK_UNITS": "1"}, pytest_current="tests/x.py") is False
    assert S.desk_units_on(env={"CRISSA_DESK_UNITS": "test"}, pytest_current="tests/x.py") is True
    assert S.desk_units_on(env={"CRISSA_SERVICE": "systemd"}, pytest_current="", platform="linux") is True
    assert S.desk_units_on(env={"CRISSA_SERVICE": "systemd"}, pytest_current="", platform="darwin") is False
    assert S.desk_units_on(env={}, pytest_current="", platform="linux") is False
    assert S.desk_units_on(env={"CRISSA_DESK_UNITS": "1"}, pytest_current="", platform="linux") is True
    assert S.desk_units_on(env={"CRISSA_DESK_UNITS": "1"}, pytest_current="", platform="darwin") is False


def test_a_test_does_not_hand_off_unless_the_flag_says_so():
    assert S.handoff_allowed(env={}, pytest_current="tests/x.py") is False
    assert S.handoff_allowed(env={"CRISSA_SERVICE_HANDOFF": "0"}, pytest_current="") is False
    assert S.handoff_allowed(env={"CRISSA_SERVICE": "systemd"}, pytest_current="") is False
    assert S.handoff_allowed(env={"CRISSA_SERVICE_HANDOFF": "test"}, pytest_current="tests/x.py") is True


def test_claim_service_pid_clears_the_hand_end(tmp_path):
    S.launcher_started(tmp_path, env={}, pid=os.getpid())
    S.launcher_ended(0, "handed off", tmp_path)
    d = S.claim_service_pid(tmp_path, 4242)
    assert d["pid"] == 4242 and d["under"] == "systemd" and "ended_at" not in d
    raw = json.loads((tmp_path / "launcher.json").read_text())
    assert raw["pid"] == 4242 and "ended_at" not in raw
