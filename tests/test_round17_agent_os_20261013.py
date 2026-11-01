"""Round 17 (2026-10-13, .101 after round 16's fresh install crissa-1493a79b; RESULTS-20261013-0630, ASK-20261013-0405
addenda 06:05-06:22): Close/Open Agent, All OS Off, Secure.

  1. All OS Off after Open Agent left crissa-player.service `failed` ("terminate called without an active exception",
     ABRT): the player returned into the interpreter's shutdown while its paint loop (OpenCV, which lets go of the GIL
     inside C++) ran; Python before 3.14 ends such a daemon thread with pthread_exit, and that unwind through OpenCV's
     noexcept frames calls std::terminate. Now the end stops the paint loop, waits for its pass, and leaves with
     os._exit. Kill HOUSE_CONDUCTOR_CLEAN_EXIT=0.
  2. Close Agent survived All OS Off and the next start: a close is a pause of one session. Kill
     CRISSA_AGENT_CLOSE_SESSION=0.
  3. Secure on a fixed camera: greyed with the reason everywhere, explained once, the click refused without touching
     AUTH (presence's half: tests/presence/test_round17_secure_fixed_cam.py). Kill RAIL_FIXED_CAM_SECURE=0.
  4. Pop-ups: the desk Wall's Hold Secure password is a Crissa pop-up in a pack (kill CRISSA_CRISSA_HOLD_POPUP=0), and
     her own window comes forward as a pager would ask (kill CRISSA_WINDOW_PAGER_RAISE=0).

The conductor is never loaded: its functions are lifted from the source. The Tk half runs on a private virtual display
only (xvfb-run -a env CRISSA_PANELS_XVFB=1 ...), never the User's screen.
"""
from __future__ import annotations

import ast
import json
import os
import resource
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import crissa_watch as WT  # noqa: E402
import crissa_window as WIN  # noqa: E402
from _kit import kit  # noqa: E402

CONDUCTOR = (ROOT / "scripts" / "house-conductor.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
WALL_SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
RENDER_SRC = (ROOT / "lib" / "house_wall_ascii_render.py").read_text(encoding="utf-8")
XVFB = bool(os.environ.get("CRISSA_PANELS_XVFB"))
needs_x = pytest.mark.skipif(not XVFB, reason="needs CRISSA_PANELS_XVFB=1 under xvfb-run (never the User's screen)")


def _lift(name: str, extra: dict | None = None) -> dict:
    tree = ast.parse(CONDUCTOR)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"os": os, "sys": sys, "signal": __import__("signal"), "CLEAN_EXIT_JOIN_S": 2.0}
    ns.update(extra or {})
    exec(compile(ast.Module([fn], []), "house-conductor.py", "exec"), ns)   # noqa: S102  the player's own lines
    return ns


# ── 1. the player's end ──────────────────────────────────────────────────────────────────────────────────────────────
def test_the_end_stops_the_paint_loop_waits_for_it_closes_the_server_and_exits_with_the_code():
    ns = _lift("clean_exit")
    ph = types.SimpleNamespace(_stop=False)
    passes = []

    def loop():
        while not ph._stop:
            passes.append(1)
            time.sleep(0.01)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    srv = types.SimpleNamespace(closed=False)
    srv.server_close = lambda: setattr(srv, "closed", True)
    exits, said = [], []
    did = ns["clean_exit"](ph, t, srv, 0, exit_=exits.append, log=lambda *a, **k: said.append(a[0]), hold_signals=False)
    assert ph._stop is True and not t.is_alive() and srv.closed and exits == [0]
    assert did == "paint loop stopped, server closed" and "clean exit" in said[0]


def test_a_paint_pass_that_will_not_end_is_waited_for_at_most_the_join_and_the_exit_still_comes():
    ns = _lift("clean_exit")
    stuck = threading.Event()
    t = threading.Thread(target=stuck.wait, daemon=True)
    t.start()
    exits = []
    t0 = time.time()
    did = ns["clean_exit"](types.SimpleNamespace(_stop=False), t, None, 3, exit_=exits.append, join_s=0.2,
                           log=lambda *a, **k: None, hold_signals=False)
    stuck.set()
    assert time.time() - t0 < 1.5 and exits == [3] and "still in a pass" in did


def test_a_step_that_fails_never_stops_the_exit():
    ns = _lift("clean_exit")

    def boom():
        raise OSError("gone")
    srv = types.SimpleNamespace(server_close=boom)
    exits = []
    did = ns["clean_exit"](None, None, srv, 0, exit_=exits.append, log=lambda *a, **k: None, hold_signals=False)
    assert exits == [0] and "server: OSError" in did


def test_the_player_ends_through_it_after_the_aligner_and_the_frame_source_too():
    assert ('CLEAN_EXIT_ON = os.environ.get("HOUSE_CONDUCTOR_CLEAN_EXIT", "1").strip().lower() not in '
            '("0", "false", "no", "off")') in CONDUCTOR
    assert 'ph_thread = threading.Thread(target=ph.run, daemon=True, name="playhead")\n    ph_thread.start()' in CONDUCTOR
    tail = CONDUCTOR[CONDUCTOR.index("        srv.serve_forever()\n    except KeyboardInterrupt:"):]
    tail = tail[:tail.index('\nif __name__ == "__main__":')]
    assert tail.index("crissa_aligner.stop()") < tail.index("clean_exit(ph, ph_thread, srv, 0)")
    assert "    if CLEAN_EXIT_ON:\n        clean_exit(ph, ph_thread, srv, 0)" in tail
    frames = CONDUCTOR[CONDUCTOR.index("def clip_frames_main("):CONDUCTOR.index("def clip_header_value(")]
    assert frames.rstrip().endswith("clean_exit(None, None, srv, 0)     # round 17: its frame routes may still be "
                                    "inside OpenCV (clean_exit below)")
    # round 16's aligner pin (tests/test_crissa_page_mic_and_launcher.py) still reads the block it reads
    i = CONDUCTOR.index("    if crissa_aligner.applicable():\n        # A PACK'S PLAYER TAKES ITS ALIGNER WITH IT")
    assert "    finally:\n        crissa_aligner.stop()" in CONDUCTOR[i:i + 1200]


_CHILD = textwrap.dedent("""
    import os, signal, sys, threading, time, types
    import numpy as np
    import cv2
    ns = {"os": os, "sys": sys, "signal": signal, "CLEAN_EXIT_JOIN_S": 2.0}
    exec(open(sys.argv[1]).read(), ns)
    frame = (np.random.rand(704, 1296, 3) * 255).astype(np.uint8)
    ph = types.SimpleNamespace(_stop=False)

    def paint():                     # the player's paint loop at full rate: inside OpenCV most of the time
        while not ph._stop:
            cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    t = threading.Thread(target=paint, daemon=True)
    t.start()

    def _term(signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGALRM, lambda *a: os.kill(os.getpid(), signal.SIGTERM))
    signal.setitimer(signal.ITIMER_REAL, 0.6)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[conductor] bye", flush=True)
    if sys.argv[2] == "clean":
        ns["clean_exit"](ph, t, None, 0)
""")


def _child_runs(tmp_path, mode: str, n: int) -> list:
    pytest.importorskip("cv2")
    tree = ast.parse(CONDUCTOR)
    fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "clean_exit")
    (tmp_path / "clean_exit.py").write_text(ast.unparse(fn), encoding="utf-8")
    (tmp_path / "child.py").write_text(_CHILD, encoding="utf-8")
    out = []
    for _ in range(n):
        p = subprocess.run([sys.executable, str(tmp_path / "child.py"), str(tmp_path / "clean_exit.py"), mode],
                           capture_output=True, text=True, timeout=60,
                           preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))   # no core files
        out.append((p.returncode, p.stdout + p.stderr))
    return out


def test_the_end_under_a_running_paint_loop_exits_0_every_time(tmp_path):
    runs = _child_runs(tmp_path, "clean", 4)
    assert all(rc == 0 for rc, _ in runs), runs
    assert all("clean exit: paint loop stopped" in txt and "terminate called" not in txt for _, txt in runs)


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="Python 3.14 parks a daemon thread at shutdown (no pthread_exit)")
def test_round_16s_return_into_the_shutdown_is_the_core_dump_101_saw(tmp_path):
    """The cause, kept as a test: the same paint loop and the old end (return into the shutdown) on Python < 3.14."""
    runs = _child_runs(tmp_path, "old", 3)
    assert any(rc in (-6, 134) or "terminate called without an active exception" in txt
               or "FATAL: exception not rethrown" in txt for rc, txt in runs), runs


# ── 2. Close Agent is a pause of one session ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path):
    return tmp_path


def _close(home, at: str | None = None):
    WT.set_closed(WT.AGENT, True, by="the desk Wall", home=home)
    if at:
        d = json.loads((home / WT.CLOSED).read_text())
        d["lanes"]["agent"]["at"] = at
        (home / WT.CLOSED).write_text(json.dumps(d))


@pytest.mark.parametrize("prev,opened", [
    ({"exit_code": 0, "why": "stopped (the Crissa menu)"}, True),                         # All OS Off from the menu
    ({"exit_code": 0, "why": "stopped (signal 15)"}, True),                               # systemctl stop, a logout
    ({"exit_code": 0, "why": "stopped (stopped with everything (systemctl stop))"}, True),
    ({"exit_code": None, "stop": {"result": "success", "code": "exited", "status": "0"}}, True),
    ({"exit_code": None, "stop": {"result": "signal", "code": "killed", "status": "TERM"}}, True),
    ({"exit_code": 1, "why": "the player stopped (exit -6)"}, False),                     # a crash: its service restarts
    ({"exit_code": None, "stop": {"result": "oom-kill", "code": "killed", "status": "KILL"}}, False),
    ({"exit_code": None, "stop": None}, False),                                           # killed, no record, same boot
])
def test_a_close_ends_with_its_session(home, prev, opened):
    _close(home)
    why = WT.agent_at_start(home, {"previous": prev}, env={}, booted=1.0)
    assert bool(why) is opened and bool(WT.agent_closed(home)) is (not opened)
    if opened:
        assert WT.agent_opened_at(home), "an opened agent is stamped, so the panel's stale Off rule reads it"


def test_a_power_loss_after_the_close_is_a_new_session(home):
    _close(home, at="2026-10-13T06:19:00-0400")
    booted = WT._stamp_epoch("2026-10-13T06:20:00-0400")
    assert WT.agent_at_start(home, {"previous": {"exit_code": None, "stop": None}}, env={}, booted=booted) == \
        "the computer started since the close"
    assert not WT.agent_closed(home)


def test_a_crash_in_the_same_boot_keeps_the_close(home):
    _close(home, at="2026-10-13T06:19:00-0400")
    booted = WT._stamp_epoch("2026-10-13T05:00:00-0400")
    assert WT.agent_at_start(home, {"previous": {"exit_code": 1}}, env={}, booted=booted) == ""
    assert WT.agent_closed(home)


def test_another_launcher_running_and_the_switch_keep_the_close(home):
    _close(home)
    assert WT.agent_at_start(home, {"shadow": True, "previous": {"exit_code": 0}}, env={}) == ""
    assert WT.agent_at_start(home, {"previous": {"exit_code": 0}}, env={"CRISSA_AGENT_CLOSE_SESSION": "0"}) == ""
    assert WT.agent_closed(home)
    assert WT.agent_at_start(home, {}, env={}) == "no run of Crissa before this one" and not WT.agent_closed(home)


def test_no_close_nothing_to_end(home):
    assert WT.agent_at_start(home, {"previous": {"exit_code": 0}}, env={}) == ""


def test_the_real_launcher_records_carry_it_from_all_os_off_to_the_next_start(home, monkeypatch):
    import crissa_service as S
    monkeypatch.setattr(S, "under", lambda env=None: "hand")
    S.launcher_started(home, env={}, pid=4242)
    _close(home)
    S.launcher_ended(0, "stopped (the Crissa menu)", home)                    # All OS Off: the launcher's clean end
    rec = S.launcher_started(home, env={}, pid=4243)
    assert "the run before ended on purpose (stopped (the Crissa menu))" == WT.agent_at_start(home, rec, env={})
    assert not WT.agent_closed(home)


def test_the_launcher_asks_at_its_start_before_anything_runs():
    run = LAUNCHER[LAUNCHER.index("def run(a) -> int:"):LAUNCHER.index("def take_back_leftovers(")]
    assert run.index("rec = crissa_service.launcher_started(crissa_home.HOME)") < run.index(
        "crissa_watch.agent_at_start(crissa_home.HOME, rec)") < run.index("rc = _run(a, crissa_home, why)")


def test_the_boot_time_reads():
    bt = WT.boot_time()
    assert bt is None or 0 < bt <= time.time()


# ── 3. Secure on a camera that cannot turn: the desk Wall and the pages (presence: tests/presence/) ──────────────────
def test_the_wall_reads_presences_offer_presence_first():
    import house_wall_ascii_render as R
    no = {"ok": False, "kind": "fixed", "why": "w", "about": "a"}
    assert R.secure_offer(None, {"visit_lamp_info": {"secure_offer": no}}) == no
    assert R.secure_offer({"visit_lamp_info": {"secure_offer": no}}, {}) == no
    assert R.secure_offer({"presence": {"visit_lamp_info": {"secure_offer": no}}}, None) == no
    assert R.secure_offer({"visit_lamp_info": {}}, {"visit_lamp_info": {"color": "amber"}}) is None   # the house


def test_the_wall_greys_its_dot_and_a_click_sends_nothing():
    assert "_so = secure_offer(house, presence)" in RENDER_SRC
    assert 'if _so is not None and _so.get("ok") is False and lamp_c != "sage":' in RENDER_SRC
    assert '        if name == "secure" and _secure_greyed():\n            return "break"' in WALL_SRC
    g = WALL_SRC[WALL_SRC.index("    def _secure_greyed() -> bool:"):WALL_SRC.index("    def secure_single():")]
    assert "crissa_home.is_house()" in g and 'R.secure_state(st.house, st.presence) == "sage"' in g
    assert 'crissa_wall_key.popup(root, "Secure", [str(off.get("about") or why)])' in g
    assert '"secure-note.json"' in g and "_wall_toast(why, 5.0)" in g
    assert "_lamp_post" not in g and "visit_click" not in g, "a greyed key never reaches presence"


@pytest.mark.parametrize("rel", ["display/crissa-panel.html", "display/voiceonly-panel.html",
                                 "display/voiceonly-home.html"])
def test_every_page_secure_key_greys_with_the_reason_and_explains_once(rel):
    src = kit(rel).read_text(encoding="utf-8")
    assert "secure_offer" in src and "SECURE_NA" in src
    assert "crissa.secureNote" in src and "window.alert(" in src and "toast(" in src
    assert ".ok===false" in src and '!=="sage"' in src


def test_the_houses_own_pages_are_untouched():
    # both pages are one file since the fold (2026-10-28) and read secure_offer, which the house's presence never sends
    for rel in ("display/voiceonly-panel.html", "display/voiceonly-home.html"):
        assert "secure_offer" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_the_kit_panel_says_presences_reason():
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_visit_note")
    ns: dict = {}
    exec(compile(ast.Module([fn], []), "crissa-panel-server.py", "exec"), ns)   # noqa: S102
    assert ns["_visit_note"]({"error": "secure_not_offered", "why": "This camera does not turn."}) == \
        "This camera does not turn."
    assert ns["_visit_note"]({"error": "secure_not_offered"}) == "Secure is not offered on this machine"


# ── 4. pop-ups: the Hold Secure password and her window ──────────────────────────────────────────────────────────────
def test_the_hold_password_is_a_crissa_pop_up_in_a_pack_and_zenity_on_the_house():
    d = WALL_SRC[WALL_SRC.index("    def secure_double(at=None):"):WALL_SRC.index("    def rail_key(name):")]
    assert d.index("if not crissa_home.is_house():") < d.index("crissa_wall_key.password_popup(") < d.index(
        'log("secure double-click -> hold dialog")')
    assert '"zenity", "--password"' in d


def test_the_hold_pop_up_switch(monkeypatch):
    import crissa_wall_key as K
    monkeypatch.delenv("CRISSA_CRISSA_HOLD_POPUP", raising=False)
    monkeypatch.delenv("CRISSA_CRISSA_PANELS_ON_WALL", raising=False)
    assert K.password_popup_on() is True
    monkeypatch.setenv("CRISSA_CRISSA_HOLD_POPUP", "0")
    assert K.password_popup_on() is False and K.password_popup(None, "Hold Secure", [], print) is None


XPROP = {
    ("xprop", "-root", "_NET_CLIENT_LIST_STACKING"): "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x2a00003, "
                                                     "0x3400001, 0x4c00004, 0x4c00009",
    ("xprop", "-id", "0x2a00003", "WM_CLASS"): 'WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"',
    ("xprop", "-id", "0x3400001", "WM_CLASS"): 'WM_CLASS(STRING) = "crissa", "Crissa"',
    ("xprop", "-id", "0x4c00004", "WM_CLASS"): 'WM_CLASS(STRING) = "brave-browser", "Brave-browser"',
    ("xprop", "-id", "0x4c00009", "WM_CLASS"): 'WM_CLASS(STRING) = "crissa", "Crissa"',
}


def test_her_windows_are_found_by_her_class_only_top_most_last():
    assert WIN.her_xids({}, run=lambda a: XPROP.get(tuple(a), "")) == [0x3400001, 0x4c00009]


def test_her_top_most_window_is_activated_as_a_pager_asks(monkeypatch):
    sent = []
    env = {"DISPLAY": ":99", "PYTEST_CURRENT_TEST": "x"}
    got = WIN.pager_raise(env, run=lambda a: XPROP.get(tuple(a), ""), send=lambda xid, d: sent.append((xid, d)) or True,
                          plat="linux")
    assert got == "activated 0x4c00009" and sent == [(0x4c00009, ":99")]
    assert WIN.pager_raise(dict(env, CRISSA_WINDOW_PAGER_RAISE="0"), run=lambda a: "", send=None, plat="linux") == "off"
    assert WIN.pager_raise(env, run=lambda a: "", send=lambda *a: True, plat="darwin") == "not Linux"
    assert WIN.pager_raise(env, run=lambda a: "", send=lambda *a: True, plat="linux") == "no window of hers"
    assert WIN.pager_raise({"PYTEST_CURRENT_TEST": "x"}, plat="linux") == "test", "a test never raises a real window"


def test_bring_adds_the_raise_only_when_it_comes_forward(monkeypatch):
    calls = []
    monkeypatch.setattr(WIN, "cdp", lambda ws, c, timeout=4.0: calls.append(c) or [{} for _ in c])
    monkeypatch.setattr(WIN, "_window_state", lambda p, t: (1, "normal"))
    monkeypatch.setattr(WIN, "pager_raise", lambda *a, **k: "activated 0x1")
    page = {"url": "http://127.0.0.1:8180/init", "webSocketDebuggerUrl": "ws://127.0.0.1:8183/x", "id": "abc"}
    out = WIN._bring(8183, page, "http://127.0.0.1:8180/init", True)
    assert out["how"] == ["front", "activated"]
    out = WIN._bring(8183, page, "http://127.0.0.1:8180/init", False)
    assert out["how"] == []


@needs_x
def test_the_pager_message_goes_to_a_real_x_server():
    assert WIN.x11_activate(0x200001, os.environ.get("DISPLAY")) is True


def _pump(r, s=0.25):
    end = time.time() + s
    while time.time() < end:
        r.update()
        time.sleep(0.02)


@pytest.fixture
def wall(monkeypatch):
    import tkinter as tk
    import crissa_wall_key as K
    keep = dict(K._S)
    K._S.update(panel=None, menu=None, kbd_fns=None, kbd_on=False, shown_box=None, box=None, log=None)
    for k in ("CRISSA_CRISSA_POPUP_FRONT", "CRISSA_CRISSA_PANELS_ON_WALL", "CRISSA_CRISSA_HOLD_POPUP"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRISSA_CRISSA_KEY_HIDES", "0")
    monkeypatch.setattr(K, "workarea", lambda W, H, run=None: (0, 0, 1280, 720))
    monkeypatch.setattr(K, "_post", lambda feed, body, timeout=10.0: {"ok": True})
    monkeypatch.setattr(K, "_get", lambda feed, timeout=3.0: {})
    kb, logs = [], []
    r = tk.Tk()
    r.geometry("1280x720+0+0")
    key = K.install(r, feed="http://127.0.0.1:9", open_url=lambda u: None, log=logs.append, W=1280, H=720,
                    keyboard=(lambda: kb.append("grant"), lambda: kb.append("release")))
    _pump(r, 0.5)
    yield r, key, kb, logs, K
    K.close_panel()
    r.destroy()
    K._S.clear()
    K._S.update(keep)


@needs_x
def test_the_hold_password_opens_above_the_key_with_the_keyboard_and_return_hands_it_over_once(wall):
    r, key, kb, logs, K = wall
    got = []
    K.password_popup(r, "Hold Secure", ["Account password: holds Secure until the next click."], got.append)
    _pump(r)
    x0, y0, x1, y1 = K.panel_box()
    kx, ky = K.key_anchor()
    assert x0 == kx and y1 <= ky and kb == ["grant"]
    entry = next(w for w in K._S["panel"]["frame"].winfo_children()[0].winfo_children()
                 if w.winfo_class() == "Frame" and any(c.winfo_class() == "Entry" for c in w.winfo_children()))
    e = next(c for c in entry.winfo_children() if c.winfo_class() == "Entry")
    assert e.cget("show") == "•"
    e.insert(0, "not-a-real-password")
    r.event_generate("<Return>")
    _pump(r, 0.5)
    assert got == ["not-a-real-password"] and not K.panel_open() and kb == ["grant", "release"]


@needs_x
def test_escape_cancels_the_hold_password(wall):
    r, key, kb, logs, K = wall
    got = []
    K.password_popup(r, "Hold Secure", ["x"], got.append)
    _pump(r)
    r.event_generate("<Escape>")
    _pump(r)
    assert got == [] and not K.panel_open() and kb == ["grant", "release"]


@needs_x
def test_the_secure_note_is_a_pop_up_above_the_key_with_the_keyboard(wall):
    r, key, kb, logs, K = wall
    K.popup(r, "Secure", ["Secure is for a camera that can turn."])
    _pump(r)
    p = K._S["panel"]
    assert p["title"] == "Secure" and p["kbd"] and kb == ["grant"]
    assert any("pop-up 'Secure' on the Wall" in ln for ln in logs)
