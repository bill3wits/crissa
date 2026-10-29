"""The launcher's main loop survives its second pass (round 10, 2026-10-11).

crissa-bc7c9777 failed acceptance on .100: `_run`'s loop called `keep_window(p["player"], ...)` (21f7c045a, Setup in her
own window), and two `for` loops later in the same loop body rebind `p` to each child process. On the second pass
`p["player"]` subscripted a UnitProc (service mode) or a Popen (process mode) and the launcher died a moment after
"ready": "the launcher itself failed: TypeError: 'UnitProc' object is not subscriptable". On .101 the launcher's unit
restarts on failure, 5 times in 10 minutes, then stays down.

The test runs the REAL statements of scripts/crissa.py, lifted from the source (from the `window_keep` line through the
end of the `while not stopping["why"]` loop), for three passes with stand-in children that are processes, not mappings.
On bc7c9777a's source it raises that TypeError on pass two.

Also pinned here: the two copies of the six-step short walk that a pack's Init now runs (the exporter's id proof and the
acceptance's walks) equal the player's own `lib/crissa_init.WALK`; both still named the four steps of 10-10.
"""
from __future__ import annotations

import ast
import textwrap
import time as _time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "crissa.py"
PLAYER = "http://127.0.0.1:8180"


def _loop_block(src: str) -> str:
    """The source of `_run`'s statements from `window_keep: dict = ...` through the main `while` loop, dedented."""
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        loops = [i for i, st in enumerate(node.body) if isinstance(st, ast.While)]
        starts = [i for i, st in enumerate(node.body)
                  if isinstance(st, ast.AnnAssign) and getattr(st.target, "id", "") == "window_keep"]
        if loops and starts:
            first, last = node.body[starts[0]], node.body[loops[0]]
            lines = src.splitlines()[first.lineno - 1:last.end_lineno]
            return textwrap.dedent("\n".join(lines))
    raise AssertionError("the launcher's main loop was not found in _run")


class _Proc:
    """A child as the loop holds it in g.procs: a process (UnitProc in service mode, Popen by hand), not a mapping."""

    def __init__(self, pid: int):
        self.pid, self.returncode = pid, None

    def poll(self):
        return None


class _G:
    def __init__(self):
        self.procs = {"player": _Proc(101), "ear": _Proc(102), "stt": _Proc(103)}
        self.expected: set = set()

    def alive(self, name: str) -> bool:
        return name in self.procs


class _Table:
    def __init__(self):
        self.data: dict = {}
        self.marked: list = []

    def child(self, name):
        return {"pid": {"player": 101, "ear": 102, "stt": 103}.get(name)}

    def mark(self, name, **kw):
        self.marked.append((name, kw))

    def exited(self, *a, **kw):
        pass

    def write(self):
        pass


def _run_loop(src: str, passes: int = 3) -> dict:
    """Run the lifted loop for `passes` passes; returns what keep_window was handed on each."""
    stopping = {"why": ""}
    seen: list = []
    state = {"passes": 0}

    def _sleep(_s):
        state["passes"] += 1
        if state["passes"] >= passes:
            stopping["why"] = "the test's last pass"

    def _keep_window(player_url, g, window_keep, home, *a, **kw):
        seen.append(player_url)
        return "wait"

    watch = types.SimpleNamespace(tick=lambda: None, kids={"ear": {}, "stt": {}})
    ns = {
        "stopping": stopping, "why": {"text": ""}, "checks": {}, "g": _G(), "table": _Table(), "logs": Path("/nonexistent"),
        "keep_window": _keep_window, "handle_request": lambda *a, **kw: None, "addon_pass": lambda *a, **kw: {},
        "agent_pass": lambda *a, **kw: "",          # Close Agent's pass (round 14, 2026-10-12): nothing to do here
        "voice_demand_pass": lambda *a, **kw: [],   # a voice runs only while a role uses it (round 17): nothing here
        "demand_state": {},
        "setup_end_pass": lambda *a, **kw: "",      # Setup's end (round 15): nothing to do here
        "stop_of_everything": lambda *a, **kw: False,   # a clean stop of everything (round 15): not this loop's case
        "_log": lambda *a, **kw: None, "_cut_note": lambda: None, "_cap": lambda s: s, "PLAIN_LANES": {},
        "lane_plain": lambda m: (), "log_tail": lambda *a, **kw: "", "why_tail": lambda *a, **kw: "",
        "time": types.SimpleNamespace(time=_time.time, sleep=_sleep),
        "crissa_home": types.SimpleNamespace(HOME=Path("/nonexistent/home")),
        "crissa_procs": types.SimpleNamespace(take_requests=lambda home: [], QUIT="quit", QUIT_NAME="Stop Crissa",
                                              finish=lambda *a, **kw: None, exit_reason=lambda *a, **kw: ""),
        "crissa_watch": types.SimpleNamespace(Watch=lambda *a, **kw: watch, agent_closed=lambda home=None: {}),
        "crissa_service": types.SimpleNamespace(UnitProc=_Proc),
        "crissa_addon_link": types.SimpleNamespace(LINK_EVERY_S=3600.0),
    }
    body = textwrap.indent(_loop_block(src), "    ")
    exec(compile("def _lifted(p):\n" + body + "\n", str(LAUNCHER), "exec"), ns)   # noqa: S102  the launcher's own lines
    plan = {"player": PLAYER, "children": [{"name": "player"}, {"name": "ear"}, {"name": "stt"}],
            "ports": {"player": 8180}}
    ns["_lifted"](plan)
    return {"seen": seen, "passes": state["passes"], "stopping": stopping["why"]}


def test_the_launchers_loop_survives_its_second_pass_with_children_that_are_processes():
    got = _run_loop(LAUNCHER.read_text(encoding="utf-8"), passes=3)
    assert got["passes"] == 3 and got["stopping"] == "the test's last pass"
    # keep_window is handed the player's ADDRESS on every pass, never a child process
    assert got["seen"] == [PLAYER, PLAYER, PLAYER]


def test_the_lifted_loop_is_the_one_that_failed_on_bc7c9777a():
    """The harness itself: bc7c9777a's loop (p["player"] read inside the loop) dies on pass two the way .100 saw it."""
    import pytest

    src = LAUNCHER.read_text(encoding="utf-8")
    old = src.replace("keep_window(player_url, g, window_keep, crissa_home.HOME)",
                      'keep_window(p["player"], g, window_keep, crissa_home.HOME)')
    assert old != src
    with pytest.raises(TypeError, match="not subscriptable"):
        _run_loop(old, passes=3)


def test_nothing_in_the_loop_subscripts_a_name_its_own_for_loops_rebind():
    """The class of the bug: inside the main loop, a name that a `for` over the children (g.procs) rebinds - `p`, the
    plan's own name before the loop - is never indexed by a string key (the plan's `p["player"]`, `p["ports"]`), so a
    later edit cannot bring the crash back another way. (A request dict, `req["action"]`, is not a child.)"""
    block = ast.parse(_loop_block(LAUNCHER.read_text(encoding="utf-8")))
    loop = next(n for n in block.body if isinstance(n, ast.While))
    rebound = {t.id for n in ast.walk(loop) if isinstance(n, ast.For) and "g.procs" in ast.unparse(n.iter)
               for t in ast.walk(n.target) if isinstance(t, ast.Name)}
    assert "p" in rebound
    bad = [ast.unparse(n) for n in ast.walk(loop)
           if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in rebound
           and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)]
    assert bad == [], bad


def _literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == name for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError("%s has no %s" % (path, name))


def test_the_exporter_and_the_acceptance_walk_the_players_own_six_steps():
    walk = list(_literal(ROOT / "lib" / "crissa_init.py", "WALK"))
    assert walk == ["connect", "machine", "voice_id", "face", "room", "hours"]
    assert list(_literal(ROOT / "scripts" / "crissa-export.py", "SHORT_WALK")) == walk
    assert list(_literal(ROOT / "scripts" / "public-loops-accept.py", "SHORT_WALK")) == walk
    four = '["connect", "machine", "voice_id", "face"]'
    for rel in ("scripts/crissa-export.py", "scripts/public-loops-accept.py"):
        assert four not in (ROOT / rel).read_text(encoding="utf-8"), rel
