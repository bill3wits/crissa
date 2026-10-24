"""Round 38 (.101 round 37 D, the User). An IDs run ends with its window (it outlived a crashed page and the Wall kept
its Setup look with no menu), and while any Setup or IDs run is mid-process with her window not in front (closed,
minimized) a SMALL corner tile says so, with Open and Back to the Wall - never the big plate over the door."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import init_v2_render as V  # noqa: E402
import crissa_init as zi  # noqa: E402
import crissa_window as Wn  # noqa: E402


def _geom():
    return types.SimpleNamespace(W=1920, H=1080, u=1920 / 88.0, page_left=8.0)


def _snap(win, ids=None):
    s = {"status": "running", "step_at": 0, "walker": "crissa", "index": 0, "total": 6,
         "plate": {"kind": "machine", "title": "This computer", "eyebrow": "Setup"},
         "browser": {"led": True, "own": True, "url": "http://127.0.0.1:8100/init", "window": win}}
    if ids:
        s["ids"] = ids
    return s


def test_the_corner_tile_when_her_window_is_not_in_front(monkeypatch):
    monkeypatch.setattr(V.R.crissa_home, "her_name", lambda paths=None: "Crissa")
    for k in ("CRISSA_SETUP_CORNER_TILE", "CRISSA_WALKIN_CLEAR"):
        monkeypatch.delenv(k, raising=False)
    hits: dict = {}
    V.render(_geom(), _snap({"up": True, "setup": True, "visible": True}), hits)
    assert "init2_open_browser" not in hits, "in front: the Wall stays clear"
    hits = {}
    V.render(_geom(), _snap({"up": True, "setup": True, "visible": False}), hits)     # minimized
    # round 40 (N21): the tile is Setup's one exit, one click (init2_ids_back = abort, then the Wall); no CANCEL beside it
    assert {"init2_open_browser", "init2_ids_back"} <= set(hits) and "init2_cancel_ask" not in hits
    x0, y0, x1, y1 = hits["init2_open_browser"]
    assert y0 > 1080 * 0.7 and x1 < 1920 * 0.4, "low on the left, never the middle"
    hits = {}
    V.render(_geom(), _snap({"up": False, "setup": False}, ids={"who": "new", "stage": "ready_user"}), hits)
    assert {"init2_open_browser", "init2_ids_back"} <= set(hits), "an IDs run: Back to the Wall ends it"
    monkeypatch.setenv("CRISSA_SETUP_CORNER_TILE", "0")
    drawn = []
    real = V._lines
    monkeypatch.setattr(V, "_lines", lambda d, x, y, text, *a, **k: drawn.append(text) or real(d, x, y, text, *a, **k))
    V.render(_geom(), _snap({"up": False, "setup": False}), {})
    assert "Crissa's window is closed" in drawn, "the kill: round 37's middle plate"
    wall = (ROOT / "lib" / "house_wall_ascii.py").read_text(encoding="utf-8")
    back = wall.split('if name == "init2_ids_back":', 1)[1][:700]
    assert '_init_post("abort", {"why": "back to the wall"}' in back and '_init_post("to_wall", {}' in back


def test_back_to_the_wall_is_one_action_on_an_ids_run():
    """The User: "should not be three clicks to get back to wall with one window"."""
    page = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    h = page.split('$("cancel").addEventListener("click", async () => {', 1)[1].split("\n});", 1)[0]
    ids = h.split("if (ids) {", 1)[1].split("    return;\n  }", 1)[0]
    assert 'await act("abort");' in ids and 'act("to_wall");' in ids
    assert 'const recorded = !["ready_user", "handover", ""].includes(String(ids.stage || ""));' in ids
    assert "if (recorded && !confirm(" in ids, "no confirm before anything was recorded"


def test_an_ids_run_ends_with_its_window(monkeypatch):
    ended = []
    monkeypatch.setattr(zi, "abort", lambda run_id="", why="": ended.append((run_id, why)))
    monkeypatch.delenv("CRISSA_IDS_END_WITH_WINDOW", raising=False)
    monkeypatch.setitem(zi._IDS_GONE, "run", "")
    monkeypatch.setitem(zi._IDS_GONE, "since", 0.0)
    st = {"ids": {"who": "new"}, "status": "running", "run_id": "r1"}
    up = {"up": True, "setup": True}
    gone = {"up": False, "setup": False}
    assert not zi._ids_end_with_window(st, up, now=100.0)
    assert not zi._ids_end_with_window(st, gone, now=101.0)          # gone: the grace starts
    assert not zi._ids_end_with_window(st, gone, now=105.0)
    assert zi._ids_end_with_window(st, gone, now=109.5) and ended == [("r1", "window gone")]
    ended.clear()
    zi._ids_end_with_window(st, gone, now=200.0)
    assert not zi._ids_end_with_window(st, up, now=203.0)             # back in time: the grace resets
    assert not zi._ids_end_with_window(st, gone, now=210.0) and not ended
    assert not zi._ids_end_with_window({"status": "running", "run_id": "r2"}, gone, now=300.0), "a first boot: never"
    assert not zi._ids_end_with_window(st, {"up": False, "setup": False, "error": "OSError"}, now=400.0), \
        "an unread window never ends a run"


def test_visibility_is_read_from_the_page(monkeypatch):
    monkeypatch.setattr(Wn, "cdp", lambda ws, calls, timeout=1.0: [{"result": {"type": "string", "value": "hidden"}}])
    assert Wn._setup_visible([{"webSocketDebuggerUrl": "ws://x"}]) is False
    monkeypatch.setattr(Wn, "cdp", lambda ws, calls, timeout=1.0: [{"result": {"type": "string", "value": "visible"}}])
    assert Wn._setup_visible([{"webSocketDebuggerUrl": "ws://x"}]) is True
    monkeypatch.setattr(Wn, "cdp", lambda ws, calls, timeout=1.0: [{"error": "closed"}])
    assert Wn._setup_visible([{"webSocketDebuggerUrl": "ws://x"}]) is True, "unread is never said as hidden"
