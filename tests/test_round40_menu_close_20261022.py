"""Round 40 (the User 2026-10-21 on the house Wall: "menu wont close"). Log: a dock press opened Advanced and its band;
a second press 34 s later re-opened it instead of closing; it closed only 36 s after that, on "input elsewhere". The
pointer-left rule had closed Advanced but left the band up, so the second press toggled the menu OPEN again. Now: one
close takes the menu, the band and the chooser down; the dock key closes what is open; a click off the band closes it at
once; Esc closes it; the dock grace is 1.5 s (was 4). The house-shell Poll the backstop reads measured 3 ms p50, 8 ms
max at load 10 (2026-10-21 11:03), every 0.15 s: the poll was never the delay. Kill HOUSE_WALL_MENU_ONE_CLOSE=0."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")


def _fn(start: str, end: str = "\n    def ") -> str:
    return SRC.split(start, 1)[1].split(end, 1)[0]


def test_one_close_takes_the_band_down_with_the_menu():
    body = _fn("    def close_menu(why: str) -> bool:")
    for rule in ('ui["menu"] = False', 'hot["hide"]()', 'dock_pick.update(i=None, wins=[], hold=None)',
                 'menu_dwell["grace_until"] = 0.0', 'log("menu closed (%s)" % why)'):
        assert rule in body, rule


def test_every_close_goes_through_it():
    assert 'close_menu("pointer left")' in SRC, "the desync: the pointer-left close now hides the band too"
    assert 'close_menu("input elsewhere")' in SRC and 'close_menu("click off the panel")' in SRC
    poll = SRC.split("    def _press_poll():", 1)[1].split("root.after(150, _press_poll)\n    root.after(150, _press_poll)", 1)[0]
    assert 'closing = name_ == "menu" and menu_one_close_on() and (ui["menu"] or hot.get("shown"))' in poll
    assert 'close_menu("dock key")' in poll, "a press while it is up closes it, never re-opens over it"
    assert 'root.bind_all("<Escape>", lambda _e: (close_menu("Esc")' in SRC
    assert 'os.environ.get("HOUSE_WALL_MENU_DOCK_GRACE_S", "1.5")' in SRC


def test_the_switch(monkeypatch):
    import house_wall_ascii as G
    monkeypatch.delenv("HOUSE_WALL_MENU_ONE_CLOSE", raising=False)
    assert G.menu_one_close_on() is True
    monkeypatch.setenv("HOUSE_WALL_MENU_ONE_CLOSE", "0")
    assert G.menu_one_close_on() is False


def test_n15_a_minimized_ids_window_repaints_the_plate(monkeypatch):
    """.101 round 38 N15: bob's IDs run, her window minimized, /v1/init said `visible: false` - and the Wall drew
    nothing: the plate repaints only when its signature changes, and minimizing changed nothing in it."""
    loop = SRC.split('            sig = (running, key, json.dumps(snap.get("plate") or {}, sort_keys=True)', 1)[1].split("            if sig != last_sig:", 1)[0]
    assert '((snap.get("browser") or {}).get("window") or {}).get(k) for k in ("up", "setup", "visible")' in loop
    assert '(snap.get("ids") or {}).get("stage")' in loop
    assert 'HOUSE_WALL_INIT_SIG_WINDOW' in loop
    import init_v2_render as V
    import types
    g = types.SimpleNamespace(W=1920, H=1080, u=1920 / 88.0, page_left=8.0)
    monkeypatch.setattr(V.R.crissa_home, "her_name", lambda paths=None: "Crissa")
    for k in ("CRISSA_SETUP_CORNER_TILE", "CRISSA_WALKIN_CLEAR"):
        monkeypatch.delenv(k, raising=False)
    snap = {"status": "running", "step_at": 0, "walker": "crissa", "index": 0, "total": 6,
            "plate": {"kind": "register", "title": "Voice ID", "eyebrow": "Add/Edit IDs"},
            "browser": {"led": True, "own": True, "url": "http://127.0.0.1:8100/init",
                        "window": {"up": True, "setup": True, "visible": False}},
            "ids": {"who": "new", "stage": "takes"}}
    hits: dict = {}
    V.render(g, snap, hits)
    assert "init2_open_browser" in hits, "the tile is drawn once the plate repaints"


def test_n21_setups_tile_is_one_exit_one_click(monkeypatch):
    """.101 round 38 N21: cancelling Setup from the corner tile took 4 clicks (click -> init2_cancel_ask x4), with a
    CANCEL key beside it; N11: "x = back to wall = cancel, all brings you back in one step"."""
    import init_v2_render as V
    import types
    g = types.SimpleNamespace(W=1920, H=1080, u=1920 / 88.0, page_left=8.0)
    monkeypatch.setattr(V.R.crissa_home, "her_name", lambda paths=None: "Crissa")
    for k in ("CRISSA_SETUP_CORNER_TILE", "CRISSA_WALKIN_CLEAR", "CRISSA_SETUP_TILE_ONE_EXIT"):
        monkeypatch.delenv(k, raising=False)
    snap = {"status": "running", "step_at": 0, "walker": "crissa", "index": 0, "total": 6,
            "plate": {"kind": "machine", "title": "This computer", "eyebrow": "Setup"},
            "browser": {"led": True, "own": True, "url": "http://127.0.0.1:8100/init",
                        "window": {"up": True, "setup": True, "visible": False}}}
    hits: dict = {}
    V.render(g, snap, hits)
    assert "init2_ids_back" in hits and "init2_cancel_ask" not in hits, hits
    monkeypatch.setenv("CRISSA_SETUP_TILE_ONE_EXIT", "0")
    hits = {}
    V.render(g, snap, hits)
    assert "init2_cancel_ask" in hits, "the kill: round 38's ask first and the CANCEL key"
