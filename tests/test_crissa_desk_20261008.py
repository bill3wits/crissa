"""The desk is the ASCII Wall, on every machine (2026-10-08).

One start plans her picture, the full-screen Wall, and the Control Panel. A weak voice tier does not
drop the Wall. A missing screen or a failed OpenCV install is one plain line and nothing starts.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_desk", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_desk_wall_is_full_screen_only_when_the_person_chose_it():
    """2026-10-10 (.101): a full-screen Wall covered GNOME's dock and top bar and nothing said how to leave. On Linux the
    Wall is the desktop's background unless the person chose full screen; a Mac keeps its full-screen window (no
    desktop layer there) unless the person turned it off."""
    z = _launcher()
    argv, note = z.desk_wall(sys.executable, plat="linux")
    assert Path(argv[1]).name == "house-wall-ascii" and "--fullscreen" not in argv and "--wallpaper" not in argv
    assert "background" in note and "Crissa key" in note
    argv, note = z.desk_wall(sys.executable, plat="linux", fullscreen=True)
    assert argv[-1] == "--fullscreen" and "F11" in note
    argv, mac = z.desk_wall(sys.executable, plat="darwin")
    assert argv[-1] == "--fullscreen" and "F11" in mac
    assert "--fullscreen" not in z.desk_wall(sys.executable, plat="darwin", fullscreen=False)[0]
    assert z.fullscreen_wanted(env={}, plat="linux") is False
    assert z.fullscreen_wanted(env={"CRISSA_FULLSCREEN": "1"}, plat="linux") is True
    assert z.fullscreen_wanted(env={}, plat="darwin") is True
    assert z.fullscreen_wanted(env={"CRISSA_FULLSCREEN": "0"}, plat="darwin") is False
    assert z.fullscreen_wanted(_args(fullscreen=True), env={}, plat="linux") is True
    assert z.fullscreen_wanted(_args(fullscreen=False), env={"CRISSA_FULLSCREEN": "1"}, plat="darwin") is False
    p = z.plan(_args(), env_in={})
    wall = next(k for k in p["children"] if k["name"] == "wall")
    assert ("--fullscreen" in wall["argv"]) == (sys.platform == "darwin")
    import inspect
    body = inspect.getsource(z.desk_wall).split('"""', 2)[-1]
    assert "ram_gb" not in body and "voice_tier" not in body and "tier" not in body


def test_a_weak_machine_still_gets_the_wall_and_the_panel():
    """Pocket, and a start with no voice at all, still plan the Wall. The desk does not read the tier."""
    z = _launcher()
    p = z.plan(_args(voice=ROOT / "modules" / "voice", with_pocket=True), env_in={})
    names = [k["name"] for k in p["children"]]
    assert names[-2:] == ["panel", "wall"]
    assert "turbo" not in names
    by = {k["name"]: k for k in p["children"]}
    assert by["wall"]["env"]["HOUSE_WALL_FEED"] == "http://127.0.0.1:8100"
    assert by["wall"]["env"]["HOUSE_WALL_PANEL"] == "http://127.0.0.1:8097/"
    # since 2026-10-09 the feed (scripts/voiceonly-home-server.py, :8099) is the wall's HOUSE in a pack too
    assert by["wall"]["env"]["HOUSE_WALL_HOUSE"] == "http://127.0.0.1:8099"
    assert names.index("feed") < names.index("panel")
    assert by["panel"]["port"] == 8097
    assert Path(by["panel"]["argv"][1]).name == "crissa-panel-server.py"
    assert by["panel"]["argv"][-2:] == ["--port", "8097"]
    bare = z.plan(_args(), env_in={})
    assert [k["name"] for k in bare["children"]][-2:] == ["panel", "wall"]


def test_no_screen_is_a_plain_sentence_and_starts_nothing(monkeypatch, tmp_path):
    z = _launcher()
    assert z.screen_problem(plat="linux", env={}, tk_ok=True).startswith("Crissa needs a screen")
    assert z.screen_problem(plat="linux", env={"WAYLAND_DISPLAY": "wayland-0"}, tk_ok=True) is None
    assert z.screen_problem(plat="darwin", env={}, tk_ok=False).startswith("Crissa needs Tk")
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.setattr(z.crissa_ports, "in_use", lambda port: False)
    monkeypatch.setattr(z, "screen_problem", lambda *a, **k:
                        "Crissa needs a screen, and this session has no display, so the desk did not start.")
    spawned = []
    monkeypatch.setattr(z.Group, "spawn", lambda self, *a, **k: spawned.append(a))
    rc = z.run(SimpleNamespace(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False,
                               with_aligner=False, allow_house=False, no_open=True))
    assert rc == 2 and spawned == []
    rec = json.loads((tmp_path / "launcher.json").read_text())
    assert "no display" in rec["last"]["why"] and "Traceback" not in rec["last"]["why"]


def test_a_failed_opencv_install_is_one_plain_line(monkeypatch, capsys):
    z = _launcher()
    monkeypatch.setattr(z, "missing_packages", lambda python=None: ["opencv-python-headless"])
    monkeypatch.setattr(z, "venv_python", lambda root=None: None)

    def boom(miss):
        # A new .venv does not see this interpreter's site-packages, so the
        # install list is every app package, not only the one this Python lacks.
        assert miss == [pip for _, pip in z.APP_PACKAGES]
        assert "opencv-python-headless" in miss
        return False, "No matching distribution"

    try:
        assert z.packages_ready([], env={}, install=boom) == 2
    finally:
        os.environ.pop("CRISSA_PACKAGE_INSTALLED", None)
    got = capsys.readouterr()
    assert "Installing OpenCV (cv2)" in got.out
    assert "OpenCV (cv2)" in got.err and "did not start" in got.err
    assert "Traceback" not in got.err and "Traceback" not in got.out


def test_a_successful_install_starts_again_in_the_venv(monkeypatch, tmp_path, capsys):
    z = _launcher()
    vpy = tmp_path / ".venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    vpy.write_text("")
    monkeypatch.setattr(z, "missing_packages", lambda python=None: ["opencv-python-headless"])
    monkeypatch.setattr(z, "venv_python", lambda root=None: vpy)
    calls = []

    def inst(miss):
        return True, "installed"

    try:
        z.packages_ready(["--no-voice"], env={}, execv=lambda exe, argv: calls.append((exe, argv)), install=inst)
    finally:
        os.environ.pop("CRISSA_PACKAGE_INSTALLED", None)
    assert calls and calls[0][0] == str(vpy)
    assert "Installing OpenCV (cv2)" in capsys.readouterr().out
    assert not (ROOT / ".venv").exists()


def test_a_new_venv_is_given_pillow_even_when_this_python_already_has_it(monkeypatch):
    """Clean room, 2026-10-08: system Python had Pillow, the new .venv did not.
    The installer installed only numpy and OpenCV, switched into the .venv, and
    then refused to install Pillow because it thought the install had already run."""
    z = _launcher()
    monkeypatch.setattr(z, "venv_python", lambda root=None: None)
    monkeypatch.setattr(z, "missing_packages", lambda python=None: ["opencv-python-headless"])
    got = []

    def inst(miss):
        got.append(list(miss))
        return False, "stop before a real venv"

    try:
        assert z.packages_ready([], env={}, install=inst) == 2
    finally:
        os.environ.pop("CRISSA_PACKAGE_INSTALLED", None)
    assert got == [[pip for _, pip in z.APP_PACKAGES]]
    assert "Pillow" in got[0]


def test_an_existing_venv_is_installed_for_what_it_cannot_import(monkeypatch, tmp_path):
    z = _launcher()
    vpy = tmp_path / ".venv" / "bin" / "python"
    vpy.parent.mkdir(parents=True)
    vpy.write_text("")

    def missing(python=None):
        if python:
            return ["numpy", "opencv-python-headless", "Pillow"]
        return ["opencv-python-headless"]

    monkeypatch.setattr(z, "missing_packages", missing)
    monkeypatch.setattr(z, "venv_python", lambda root=None: vpy)
    got = []

    def inst(miss):
        got.append(list(miss))
        return True, "installed"

    calls = []
    try:
        z.packages_ready(["--no-voice"], env={}, execv=lambda exe, argv: calls.append(exe), install=inst)
    finally:
        os.environ.pop("CRISSA_PACKAGE_INSTALLED", None)
    assert got == [["numpy", "opencv-python-headless", "Pillow"]]
    assert calls == [str(vpy)]


class _DeskRun:
    """systemctl stand-in: start/stop flip is-active, show returns MainPID."""

    def __init__(self, start_rc=0):
        self.calls = []
        self.active = "inactive"
        self.start_rc = start_rc
        self.pid = "4242"

    def __call__(self, argv, timeout=0):
        self.calls.append(list(argv))
        verb = argv[2] if len(argv) > 2 else ""
        if verb == "start":
            if self.start_rc == 0:
                self.active = "active"
            return (self.start_rc, "", "" if self.start_rc == 0 else "Failed to connect to bus")
        if verb == "stop":
            self.active = "inactive"
            return (0, "", "")
        if verb == "is-active":
            return (0 if self.active == "active" else 3, self.active + "\n", "")
        if verb == "show":
            return (0, self.pid + "\n", "")
        return (0, "", "")


def test_a_live_unit_hides_its_proc_tile_and_a_missing_one_does_not():
    import crissa_procs as procs
    prow = [{"unit": "proc:panel", "proc": "panel"}, {"unit": "proc:player", "proc": "player"}]
    rows = [{"unit": "crissa-panel", "active": "active"}, {"unit": "crissa-player", "active": "not-found"}]
    assert [t["proc"] for t in procs.without_live_units(prow, rows)] == ["player"]


def test_the_service_process_starts_the_panel_as_a_unit(monkeypatch, tmp_path):
    z = _launcher()
    monkeypatch.setenv("CRISSA_SYSTEMD_USER_DIR", str(tmp_path / "units"))
    run = _DeskRun()
    popped = []

    def popen(argv, *a, **k):
        popped.append(list(argv))
        raise AssertionError("a unit start must not fork the child")

    monkeypatch.setattr(z.subprocess, "Popen", popen)
    g = z.Group(tmp_path / "logs", use_units=True, unit_run=run)
    p = g.spawn("panel", [sys.executable, "panel.py"], {"CRISSA_FACE": "crissa"}, 8097)
    assert popped == []
    monkeypatch.undo()
    assert isinstance(p, z.crissa_service.UnitProc)
    assert p.pid == 4242 and p.unit == "crissa-panel.service"
    text = (tmp_path / "units" / "crissa-panel.service").read_text()
    assert "PartOf=crissa-launcher.service" in text and "WantedBy" not in text
    assert ["systemctl", "--user", "start", "crissa-panel.service"] in run.calls
    g.stop()
    assert ["systemctl", "--user", "stop", "crissa-panel.service"] in run.calls
    assert p.poll() is not None


def test_a_unit_that_does_not_start_falls_back_to_a_process(monkeypatch, tmp_path):
    z = _launcher()
    monkeypatch.setenv("CRISSA_SYSTEMD_USER_DIR", str(tmp_path / "units"))
    run = _DeskRun(start_rc=1)
    started = []

    class Dummy:
        pid = 77
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    def popen(*a, **k):
        started.append(a[0])
        return Dummy()

    monkeypatch.setattr(z.subprocess, "Popen", popen)
    g = z.Group(tmp_path / "logs", use_units=True, unit_run=run)
    p = g.spawn("panel", [sys.executable, "panel.py"], {}, 8097)
    assert p.pid == 77 and started
    log = (tmp_path / "logs" / "panel.log").read_text()
    assert "desk unit crissa-panel.service did not start" in log
    assert "starting it by hand" in log


def test_a_hand_start_hands_the_desk_over_after_the_screen_check(monkeypatch, tmp_path):
    z = _launcher()
    src = (ROOT / "scripts" / "crissa.py").read_text()
    body = src.split("def _run(", 1)[1].split("\ndef ", 1)[0]
    assert body.find("screen_problem(") < body.find("_handoff_now(") < body.find("Group(")
    assert body.find("stop_desk_units(") < body.find("port taken")
    assert z.crissa_service.handoff_allowed(env={}) is False
    monkeypatch.setenv("CRISSA_SERVICE_HANDOFF", "test")
    monkeypatch.delenv("CRISSA_SERVICE", raising=False)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.setattr(z.crissa_ports, "in_use", lambda port: False)
    monkeypatch.setattr(z, "screen_problem", lambda *a, **k: None)
    monkeypatch.setattr(z.crissa_service, "handoff_to_service",
                        lambda args, home, env=None, **k: {"handed": True, "note": "started", "pid": 9})
    spawned = []
    monkeypatch.setattr(z.Group, "spawn", lambda self, *a, **k: spawned.append(a))
    rc = z.run(SimpleNamespace(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False,
                               with_aligner=False, allow_house=False, no_open=True, args=["--face", "crissa"],
                               tier={}))
    assert rc == 0 and spawned == []
    rec = json.loads((tmp_path / "launcher.json").read_text())
    assert "ended_at" not in rec
    assert rec["refused"]["why"] == "handed to crissa-launcher.service"


def test_the_player_keeps_the_service_mark_when_the_launcher_is_the_service():
    z = _launcher()
    env = {k["name"]: k for k in z.plan(_args(), env_in={"CRISSA_SERVICE": "systemd"})["children"]}["player"]["env"]
    assert env.get("CRISSA_SERVICE") == "systemd"
    assert "CRISSA_DESK_HELD" not in env


def test_the_pack_rail_opens_the_desk_panel_without_a_url_in_the_key():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text()
    body = src.split("    def rail_key(name):")[1].split("    def on_click(ev):")[0]
    assert "HOUSE_WALL_PANEL" in body
    assert "http://" not in body
    assert "krew not installed" in (ROOT / "config" / "crissa-panel.json").read_text()
