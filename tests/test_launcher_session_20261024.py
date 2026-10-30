"""The 1.0.0 tester findings of 2026-10-24 (.101), both must-fix for the Sunday cut.

F-A  After a reboot `systemctl --user start crissa-launcher` left the Wall in a crash loop ("couldn't connect to
     display :0"): the child units carried the INSTALL session's XAUTHORITY. Every start path now reads the live
     session's display keys (lib/crissa_service.live_session_env) and lays them over the launcher's environment
     (adopt_live_session) and each child's (child_launch); the handoff carries an allow-list of names, not the shell's
     whole environment (carried_name). Kills CRISSA_LIVE_SESSION_ENV=0, CRISSA_CARRY_ENV=all.
F-B  One press of the WALL key closed the Wall at once and GNOME Shell died behind the dock write-back. The key arms
     on the first press and closes on a second inside a window (lib/crissa_wall_key.wall_off_press, kill
     CRISSA_WALL_KEY_CONFIRM=0); the dock's values go back deferred and one at a time (lib/glass_mode.pack_dock_restore,
     kill CRISSA_DOCK_RESTORE_DEFER=0); a Stop request runs beside the heartbeat loop (scripts/crissa.py
     handle_request, kill CRISSA_STOP_OFF_LOOP=0).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import glass_mode as GM  # noqa: E402
import crissa_service as S  # noqa: E402
import crissa_wall_key as WK  # noqa: E402

INSTALL = {"PATH": "/usr/bin", "HOME": "/home/p", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0",
           "XAUTHORITY": "/run/user/1000/.mutter-Xwaylandauth.6CBGX3", "CRISSA_AUTHORING": "0"}


def _show_env(xauth: str):
    """`systemctl --user show-environment` of a login whose Xwayland auth file is `xauth`."""
    def run(argv, timeout=30.0):
        assert argv[:3] == ["systemctl", "--user", "show-environment"], argv
        return 0, "DISPLAY=:0\nWAYLAND_DISPLAY=wayland-0\nXAUTHORITY=%s\nXDG_RUNTIME_DIR=/run/user/1000\nFOO=bar\n" % xauth, ""
    return run


def _unit_text(tmp_path, env, session):
    launch = S.child_launch(["/py", "scripts/wall.py"], {"CRISSA_WALL_FULLSCREEN": "0"}, base=env, session=session)
    got = S.write_desk_unit("wall", launch, home=tmp_path / "home", units=tmp_path / "units")
    return got["unit"].read_text(encoding="utf-8"), launch


# ---- F-A -------------------------------------------------------------------------------------------------------------
def test_the_second_start_after_a_relogin_writes_the_wall_unit_with_the_live_xauthority(tmp_path):
    """The install session's key in the launcher's environment, a new login's in the user manager: the unit the
    second start writes carries the NEW one (the .101 reboot: 6CBGX3 in every unit, QIDSX3 live)."""
    first = S.live_session_env(run=_show_env(INSTALL["XAUTHORITY"]), proc_env={}, env={})
    text1, launch1 = _unit_text(tmp_path, INSTALL, first)
    assert "6CBGX3" in text1 and S.read_unit(text1)["env"]["XAUTHORITY"].endswith("6CBGX3")
    # ...reboot: the launcher's own environment (its unit) still says 6CBGX3, the session says QIDSX3
    second = S.live_session_env(run=_show_env("/run/user/1000/.mutter-Xwaylandauth.QIDSX3"), proc_env={}, env={})
    text2, launch2 = _unit_text(tmp_path, INSTALL, second)
    unit2 = S.read_unit(text2)
    assert unit2["env"]["XAUTHORITY"].endswith("QIDSX3") and "6CBGX3" not in text2
    assert unit2["env"]["DISPLAY"] == ":0" and unit2["env"]["WAYLAND_DISPLAY"] == "wayland-0"
    assert "FOO" not in unit2["env"], "only the session keys come from show-environment"
    assert launch2["env"]["CRISSA_WALL_FULLSCREEN"] == "0", "the plan's keys still sit on top"


def test_the_live_session_fills_a_key_the_user_manager_lacks_from_gnome_shells_own_environment():
    def no_xauth(argv, timeout=30.0):
        return 0, "DISPLAY=:0\n", ""
    shell = {"XAUTHORITY": "/run/user/1000/.mutter-Xwaylandauth.QIDSX3", "WAYLAND_DISPLAY": "wayland-0", "HOME": "/x"}
    got = S.live_session_env(run=no_xauth, proc_env=shell, env={})
    assert got == {"DISPLAY": ":0", "XAUTHORITY": shell["XAUTHORITY"], "WAYLAND_DISPLAY": "wayland-0"}


def test_the_shell_environment_is_read_from_proc_of_this_uid(tmp_path):
    import os
    proc = tmp_path / "proc"
    (proc / "41").mkdir(parents=True)
    (proc / "41" / "comm").write_text("gnome-shell\n")
    (proc / "41" / "environ").write_bytes(b"DISPLAY=:1\0XAUTHORITY=/run/u/.mutter-Xwaylandauth.QIDSX3\0")
    (proc / "42").mkdir()
    (proc / "42" / "comm").write_text("bash\n")
    (proc / "42" / "environ").write_bytes(b"DISPLAY=:9\0")
    got = S._shell_proc_env(proc, uid=os.getuid())
    assert got["DISPLAY"] == ":1" and got["XAUTHORITY"].endswith("QIDSX3")
    assert S._shell_proc_env(proc, uid=os.getuid() + 1) == {}, "another user's shell is not this session"


def test_adopt_live_session_lays_the_live_keys_over_the_launchers_environment_and_says_what_changed():
    env = dict(INSTALL)
    changed = S.adopt_live_session(env, session={"XAUTHORITY": "/run/user/1000/.mutter-Xwaylandauth.QIDSX3", "DISPLAY": ":0"})
    assert set(changed) == {"XAUTHORITY"} and changed["XAUTHORITY"][0].endswith("6CBGX3")
    assert env["XAUTHORITY"].endswith("QIDSX3") and env["DISPLAY"] == ":0"


def test_the_kill_keeps_the_units_snapshot(tmp_path):
    assert S.live_session_env(run=_show_env("/new"), proc_env={"XAUTHORITY": "/new"}, env={"CRISSA_LIVE_SESSION_ENV": "0"}) == {}
    text, _l = _unit_text(tmp_path, INSTALL, {})
    assert "6CBGX3" in text


def test_the_handoff_carries_the_names_she_reads_not_the_shells_whole_environment():
    shell = dict(INSTALL, CLAUDE_CODE_ENTRYPOINT="cli", CLAUDECODE="1", SOME_RANDOM="x", RAIL_STT_X="1",
                 EAR_BRIDGE_CLAIM_CHECK="0", XDG_RUNTIME_DIR="/run/user/1000", LC_ALL="C", TERM="xterm")
    got = S.carried_env(shell)
    for k in ("PATH", "HOME", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "CRISSA_AUTHORING", "RAIL_STT_X",
              "EAR_BRIDGE_CLAIM_CHECK", "XDG_RUNTIME_DIR", "LC_ALL"):
        assert got[k] == shell[k], k
    for k in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDECODE", "SOME_RANDOM", "TERM"):
        assert k not in got, k
    # the person adds a name or a prefix; the kill carries everything but what only a terminal has
    assert "SOME_RANDOM" in S.carried_env(dict(shell, CRISSA_CARRY_ENV_EXTRA="SOME_RANDOM"))
    assert "CLAUDE_CODE_ENTRYPOINT" in S.carried_env(dict(shell, CRISSA_CARRY_ENV_EXTRA="CLAUDE_"))
    all_ = S.carried_env(dict(shell, CRISSA_CARRY_ENV="all"))
    assert "SOME_RANDOM" in all_ and "CLAUDE_CODE_ENTRYPOINT" in all_ and "TERM" not in all_
    # Init's install from the player: the names the launcher passed it still rule
    named = S.carried_env(dict(shell, CRISSA_LAUNCHER_ENV_KEYS=json.dumps(["PATH", "SOME_RANDOM"])))
    assert set(named) == {"PATH", "SOME_RANDOM"}


# ---- F-B -------------------------------------------------------------------------------------------------------------
def test_one_press_of_the_wall_key_arms_and_the_second_inside_the_window_closes():
    mem = {}
    close, words = WK.wall_off_press(mem, now=100.0, env={})
    assert close is False and words == WK.WALL_KEY_ARMED_WORDS and mem["wall_off_armed"] == 100.0
    close, words = WK.wall_off_press(mem, now=100.0 + WK.confirm_window_s({}) - 0.1, env={})
    assert close is True and words == "" and "wall_off_armed" not in mem


def test_a_second_press_after_the_window_only_arms_again():
    mem = {}
    WK.wall_off_press(mem, now=100.0, env={})
    close, words = WK.wall_off_press(mem, now=100.0 + WK.confirm_window_s({}) + 0.5, env={})
    assert close is False and words and mem["wall_off_armed"] == 100.0 + WK.confirm_window_s({}) + 0.5
    assert WK.confirm_window_s({"CRISSA_WALL_KEY_CONFIRM_S": "1.0"}) == 1.0
    assert WK.confirm_window_s({"CRISSA_WALL_KEY_CONFIRM_S": "x"}) == WK.WALL_KEY_CONFIRM_S


def test_the_confirm_kill_closes_on_one_press():
    mem = {"wall_off_armed": 0.0}
    assert WK.wall_off_press(mem, now=5.0, env={"CRISSA_WALL_KEY_CONFIRM": "0"}) == (True, "")
    assert "wall_off_armed" not in mem


def test_the_wall_key_handler_asks_the_key_before_it_posts_the_close():
    src = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    i = src.index('elif name == "wall_off":')
    block = src[i:i + 1400]
    assert "crissa_wall_key.wall_off_press(ui)" in block
    assert block.index("wall_off_press") < block.index('"action": "wall_close"'), "the key is asked first"


def test_the_dock_goes_back_after_a_pause_one_key_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(GM.crissa_home, "HOME", tmp_path)
    (tmp_path / "dock-before.json").write_text(json.dumps({"schema": GM.DOCK_SCHEMA, "keys": {
        "dock-position": "'LEFT'", "dock-fixed": "true", "autohide": "false"}}))
    trail = []
    def run(argv, **kw):
        trail.append(("set", argv[3]))
        class R: returncode = 0; stdout = ""
        return R()
    def sleep(s):
        trail.append(("sleep", round(s, 3)))
    got = GM.pack_dock_restore(run=run, sleep=sleep, env={})
    assert got["ok"] and got["deferred_s"] == GM.DOCK_RESTORE_DELAY_S
    assert trail[0] == ("sleep", GM.DOCK_RESTORE_DELAY_S), "nothing is written while the Shell still has the window"
    sets = [t for t in trail if t[0] == "set"]
    assert [t[1] for t in sets] == ["dock-position", "dock-fixed", "autohide"]
    gaps = [t for t in trail[1:] if t[0] == "sleep"]
    assert gaps == [("sleep", GM.DOCK_RESTORE_GAP_S)] * 2, "one key at a time, a gap between"
    assert not (tmp_path / "dock-before.json").exists()


def test_the_dock_deferral_dials_and_its_kill(tmp_path, monkeypatch):
    assert GM.dock_restore_pace({"CRISSA_DOCK_RESTORE_DEFER": "0"}) == (0.0, 0.0)
    assert GM.dock_restore_pace({"CRISSA_DOCK_RESTORE_DELAY_S": "3", "CRISSA_DOCK_RESTORE_GAP_S": "0.5"}) == (3.0, 0.5)
    monkeypatch.setattr(GM.crissa_home, "HOME", tmp_path)
    (tmp_path / "dock-before.json").write_text(json.dumps({"keys": {"autohide": "false", "dock-fixed": "true"}}))
    slept = []
    def run(argv, **kw):
        class R: returncode = 0; stdout = ""
        return R()
    GM.pack_dock_restore(run=run, sleep=slept.append, env={"CRISSA_DOCK_RESTORE_DEFER": "0"})
    assert slept == [], "the kill writes at once, as 1.0.0"


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_session_test", ROOT / "scripts" / "crissa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Table:
    def __init__(self):
        self.calls = []
    def exited(self, *a, **kw):
        self.calls.append(("exited", a, kw))
    def mark(self, *a, **kw):
        self.calls.append(("mark", a, kw))


class _Group:
    def __init__(self, hold: threading.Event):
        self.specs = {"wall": (["/py", "wall"], {}, None)}
        self.table = _Table()
        self.hold = hold
        self.stopped = threading.Event()
    def stop_one(self, name):
        self.hold.wait(5.0)          # a `systemctl --user stop crissa-wall` waiting on the Wall's exit
        self.stopped.set()
        return 0
    def alive(self, name):
        return True


def test_a_stop_request_runs_beside_the_heartbeat_loop(tmp_path, monkeypatch):
    mod = _launcher()
    finished = []
    monkeypatch.setattr(mod.crissa_procs, "finish", lambda *a, **kw: finished.append(a))
    hold = threading.Event()
    g = _Group(hold)
    t0 = time.time()
    mod.handle_request(g, tmp_path, {"name": "wall", "action": "stop", "job": "j1"}, [])
    assert time.time() - t0 < 1.0 and not g.stopped.is_set(), "the loop (and its heartbeat) is not held by the stop"
    hold.set()
    assert g.stopped.wait(5.0)
    for _ in range(100):
        if finished:
            break
        time.sleep(0.02)
    assert finished and finished[0][3] is True and finished[0][1] == "wall"
    # the kill: the stop runs on the loop, as 1.0.0
    monkeypatch.setenv("CRISSA_STOP_OFF_LOOP", "0")
    g2 = _Group(threading.Event())
    g2.hold.set()
    mod.handle_request(g2, tmp_path, {"name": "wall", "action": "stop", "job": "j2"}, [])
    assert g2.stopped.is_set() and len(finished) == 2
