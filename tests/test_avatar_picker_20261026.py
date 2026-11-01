"""Which library she wears, picked from the Control Panel (the User 2026-10-26: "find the spot to allow choice from
library ... a director arrow for a small popup. Toggle won't work if three avatars to pick").

lib/crissa_avatar lists the libraries (shipped + <state home>/libraries/*), saves the pick in <state home>/avatar.json,
the launcher wears it at plan and on a player restart, the panel server (:8097) serves GET/POST /v1/avatar, and the
page draws one section, Choose skin, with one dropdown whatever the count (the User, later 2026-10-26: "it will need to
be a dropdown menu as many will start with two, but gain more over time. Choose skin should be the section"). A library
of another size (a 480p store hub, 848x480) puts HOUSE_FRAME_FROM_LIBRARY on the player."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_avatar as A  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib(d: Path, built=True, pack=False, name=None, size=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "inventory.json").write_text(json.dumps({"size": size} if size else {}), encoding="utf-8")
    if built:
        (d / "visemes" / "phoneme-map").mkdir(parents=True, exist_ok=True)
        (d / "visemes" / "phoneme-map" / "map.json").write_text("{}", encoding="utf-8")
    if pack:
        (d / "pack.json").write_text("{}", encoding="utf-8")
    if name:
        (d / "library.json").write_text(json.dumps({"name": name}), encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    root, home = tmp_path / "tree", tmp_path / "home"
    _lib(root / "display" / "crissa-poses")                       # the shipped face (lila's folder absent on purpose)
    _lib(home / "libraries" / "friday", pack=True, name="Friday")
    _lib(home / "libraries" / "mine", built=False)
    (home / "libraries" / "notalib").mkdir(parents=True)        # no inventory: not a library
    return root, home


# ---- the module ------------------------------------------------------------------------------------------------------
def test_the_libraries_are_the_shipped_faces_then_what_the_person_added(world):
    root, home = world
    libs = A.libraries(root, home)
    assert [(l["key"], l["source"], l["built"]) for l in libs] == [
        ("crissa", "shipped", True), ("friday", "store", True), ("mine", "yours", False)]
    assert {l["label"] for l in libs} == {"crissa", "Friday", "mine"}


def test_a_pick_is_one_file_and_the_launcher_reads_it(world):
    root, home = world
    assert A.pose_dir(root, home, "crissa") == ("crissa", str(root / "display" / "crissa-poses"))
    bad = A.choose("nope", root, home)
    assert bad["ok"] is False and "unknown library" in bad["error"] and not A.state_path(home).exists()
    got = A.choose("friday", root, home, by="test")
    assert got["ok"] and json.loads(A.state_path(home).read_text())["key"] == "friday"
    assert A.pose_dir(root, home, "crissa") == ("friday", str(home / "libraries" / "friday"))
    # the kill switch: the list answers, the pick is refused, the command line's face is worn
    off = {"CRISSA_AVATAR_PICKER": "0"}
    assert A.choose("mine", root, home, env=off)["refused"] is True
    assert A.pose_dir(root, home, "crissa", env=off)[0] == "crissa"
    # a library that is gone is no choice
    import shutil
    shutil.rmtree(home / "libraries" / "friday")
    assert A.pose_dir(root, home, "crissa")[0] == "crissa"


def test_status_says_what_is_worn_and_whether_a_restart_is_owed(world):
    root, home = world
    (home / "run").mkdir()
    (home / "run" / "procs.json").write_text(json.dumps({"launcher": {"face": "crissa"}}), encoding="utf-8")
    st = A.status(root, home)
    assert st["running"] == "crissa" and st["chosen"] == "" and st["apply_needed"] is False
    A.choose("friday", root, home)
    st = A.status(root, home)
    assert st["chosen"] == "friday" and st["apply_needed"] is True
    assert [l["key"] for l in st["libraries"] if l["chosen"]] == ["friday"]
    assert [l["key"] for l in st["libraries"] if l["running"]] == ["crissa"]


def test_a_library_of_another_size_asks_the_player_to_paint_at_it(tmp_path):
    _lib(tmp_path / "bash480", size=[848, 480])
    _lib(tmp_path / "native", size=[736, 400])
    _lib(tmp_path / "hd", size=[1296, 704])
    _lib(tmp_path / "nosize")
    assert A.player_env(tmp_path / "bash480") == {"HOUSE_FRAME_FROM_LIBRARY": "1"}
    assert A.player_env(tmp_path / "native") == {} and A.player_env(tmp_path / "hd") == {}
    assert A.player_env(tmp_path / "nosize") == {} and A.player_env(tmp_path / "missing") == {}
    assert A.size(tmp_path / "bash480") == [848, 480] and A.size(tmp_path / "nosize") is None
    import head_frame
    assert A.FRAME_FROM_LIBRARY_ENV == head_frame.FRAME_FROM_LIBRARY_ENV
    assert (head_frame.CLIP_W, head_frame.CLIP_H) in A.NATIVE_SIZES


def test_the_rows_carry_the_size(tmp_path):
    root, home = tmp_path / "tree", tmp_path / "home"
    _lib(root / "display" / "crissa-poses", size=[736, 400])
    _lib(home / "libraries" / "bash480", pack=True, size=[848, 480])
    assert [(l["key"], l["size"]) for l in A.libraries(root, home)] == [("crissa", [736, 400]), ("bash480", [848, 480])]


# ---- the launcher ----------------------------------------------------------------------------------------------------
def _launcher():
    return _load("crissa_launcher_avatar", ROOT / "scripts" / "crissa.py")


def _args(**kw):
    base = dict(face="crissa", audio_at="host", port_offset=0, voice=None, with_asr=False, with_aligner=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_launcher_plans_the_picked_library_as_the_players_poses(tmp_path, monkeypatch):
    z = _launcher()
    home = tmp_path / "home"
    _lib(home / "libraries" / "friday", pack=True)
    import crissa_home
    monkeypatch.setattr(crissa_home, "HOME", home)      # one module object: the launcher and the picker read it
    env = {k["name"]: k for k in z.plan(_args(), env_in={})["children"]}["player"]["env"]
    assert env["HOUSE_POSE_DIR"] == str(ROOT / "display" / "crissa-poses")
    A.choose("friday", ROOT, home)
    p = z.plan(_args(), env_in={})
    env = {k["name"]: k for k in p["children"]}["player"]["env"]
    assert env["HOUSE_POSE_DIR"] == str(home / "libraries" / "friday") and p["face"] == "friday"
    assert "HOUSE_FRAME_FROM_LIBRARY" not in env            # friday names no size: the native frame


def test_a_480p_library_puts_the_frame_switch_on_the_player_at_plan_and_restart(tmp_path, monkeypatch):
    z = _launcher()
    home = tmp_path / "home"
    _lib(home / "libraries" / "bash480", pack=True, size=[848, 480])
    _lib(home / "libraries" / "friday", pack=True, size=[736, 400])
    import crissa_home
    monkeypatch.setattr(crissa_home, "HOME", home)
    A.choose("bash480", ROOT, home)
    env = {k["name"]: k for k in z.plan(_args(), env_in={})["children"]}["player"]["env"]
    assert env["HOUSE_FRAME_FROM_LIBRARY"] == "1" and env["HOUSE_POSE_DIR"] == str(home / "libraries" / "bash480")
    env = {k["name"]: k for k in z.plan(_args(), env_in={"HOUSE_FRAME_FROM_LIBRARY": "0"})["children"]}["player"]["env"]
    assert "HOUSE_FRAME_FROM_LIBRARY" not in env            # the environment's own value is left to reach the child

    class Table:
        data = {"launcher": {}}

        def write(self):
            pass
    g = SimpleNamespace(specs={"player": (["p"], {"HOUSE_POSE_DIR": "old"}, 8100)}, table=Table())
    z.apply_avatar(g, home, env={})
    assert g.specs["player"][1]["HOUSE_FRAME_FROM_LIBRARY"] == "1"
    A.choose("friday", ROOT, home)                            # back to a native-size library: the switch goes with it
    z.apply_avatar(g, home, env={})
    assert "HOUSE_FRAME_FROM_LIBRARY" not in g.specs["player"][1]
    assert g.specs["player"][1]["HOUSE_POSE_DIR"] == str(home / "libraries" / "friday")


def test_a_player_restart_wears_the_pick(tmp_path, monkeypatch):
    z = _launcher()
    home = tmp_path / "home"
    _lib(home / "libraries" / "friday", pack=True)
    A.choose("friday", ROOT, home)

    class Table:
        def __init__(self):
            self.data = {"launcher": {}}
            self.writes = 0

        def write(self):
            self.writes += 1

    g = SimpleNamespace(specs={"player": (["python3", "player"], {"HOUSE_POSE_DIR": "old", "X": "1"}, 8100)},
                        table=Table())
    face = z.apply_avatar(g, home, env={})
    argv, env, port = g.specs["player"]
    assert face == "friday" and env["HOUSE_POSE_DIR"] == str(home / "libraries" / "friday") and env["X"] == "1"
    assert argv == ["python3", "player"] and port == 8100
    assert g.table.data["launcher"]["face"] == "friday" and g.table.writes == 1


def test_the_launchers_face_table_is_the_modules(monkeypatch):
    z = _launcher()
    assert z.FACES == A.SHIPPED
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    assert 'if name == "player":\n                apply_avatar(g, home)' in src, "a player Restart applies the pick"


# ---- the panel server ------------------------------------------------------------------------------------------------
def _panel():
    return _load("crissa_panel_server_avatar", ROOT / "scripts" / "crissa-panel-server.py")


def test_the_panel_serves_the_pick_and_asks_the_launcher_for_the_player(tmp_path, monkeypatch):
    P = _panel()
    home = tmp_path / "home"
    _lib(home / "libraries" / "friday", pack=True, name="Friday")
    # 1.0.5 offers Crissa's looks only (the User 2026-10-31): Friday is a Crissa flavor (house-rails-9f's library.json)
    (home / "libraries" / "friday" / "library.json").write_text(json.dumps({"name": "Friday", "agent": "crissa",
                                                                            "flavor": "friday"}), encoding="utf-8")
    P.CFG["_state"] = str(home)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    asked = []
    monkeypatch.setattr(P.procs, "request", lambda h, name, act: (asked.append((str(h), name, act)) or
                                                                  {"ok": True, "job": "j1", "proc": name}))
    view = P.avatar_view()
    assert view["ok"] and "friday" in {l["key"] for l in view["libraries"]}
    bad = P.avatar_set({"key": "nope"})
    assert bad["ok"] is False and bad["code"] == 409
    got = P.avatar_set({"key": "friday"})
    assert got["ok"] and got["label"] == "Friday" and got["restart"]["ok"] and asked == [(str(home), "player", "restart")]
    assert json.loads((home / "avatar.json").read_text())["key"] == "friday"
    assert "restarting" in got["note"]
    # the house gets 404 from both
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert P.avatar_view()["code"] == 404 and P.avatar_set({"key": "friday"})["code"] == 404


def test_the_route_is_wired_in_both_verbs():
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text(encoding="utf-8")
    assert src.count('if path == "/v1/avatar":') == 2
    page = (ROOT / "display" / "crissa-panel.html").read_text(encoding="utf-8")
    assert 'fetch("/v1/avatar",{cache:"no-store"})' in page and "data-avatar-select" in page
    assert 'groupHead("Choose skin")' in page and "data-avatar-more" not in page and "avatar-pop" not in page
    assert "avatarInsert(box)" in page and "pullAvatar(false)" in page


# ---- the page in a headless browser ----------------------------------------------------------------------------------
class _Stub:
    def __init__(self, page: Path, answers: dict):
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                if path in ("/", "/panel"):
                    return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
                ans = answers.get(path, {"ok": True})
                return self._send(200, json.dumps(ans).encode(), "application/json")

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                stub.posts.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
                return self._send(200, json.dumps({"ok": True, "label": "Friday", "note": "She wears Friday",
                                                   "restart": {"ok": True, "job": "j1"}}).encode(), "application/json")
        self.posts: list = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def _services():
    return {"ok": True, "ts": 1.0, "fleet": {"total": 2, "down": 0, "warn": 0, "parked": 0}, "os_off": {},
            "systemd": "ok", "title": "Control Panel", "self_unit": "crissa-panel", "gpu": None,
            "services": [{"unit": "proc:player", "label": "Crissa", "section": "Wall services", "health": "ok",
                          "active": "active", "sub": "running", "port": 8100},
                         {"unit": "proc:panel", "label": "Control Panel", "section": "Wall services", "health": "ok",
                          "active": "active", "sub": "running", "port": 8097}],
            "addons": {"section": "krew services", "sections": [], "installed": [], "missing": [], "errors": [],
                       "link": {"links": [], "note": ""}},
            "launcher": {"alive": True, "present": True}, "voices": {"section": "Voices", "tiles": []}}


def _avatar(n):
    keys = ["crissa", "friday", "bash"][:n]
    return {"ok": True, "enabled": True, "chosen": "", "running": "crissa", "apply_needed": False,
            "libraries": [{"key": k, "label": k.capitalize(), "source": "shipped" if k == "crissa" else "store",
                           "dir": "/x/" + k, "built": True, "chosen": False, "running": k == "crissa",
                           "size": [736, 400] if k == "crissa" else [848, 480]} for k in keys]}


def _page(url, tmp_path):
    acc = _load("accept_avatar", ROOT / "scripts" / "public-loops-accept.py")
    br = acc._browser()
    if not br:
        pytest.skip("no headless browser on this machine")
    return acc._Page(br, str(tmp_path / "prof"), url, str(tmp_path / "browser.log"))


def _wait(pg, expr, s=20.0):
    import time
    end = time.time() + s
    while time.time() < end:
        got = pg.js(expr)
        if got:
            return got
        time.sleep(0.3)
    return pg.js(expr)


def _skin_select(pg):
    return pg.js("document.querySelectorAll('[data-avatar-tile] select[data-avatar-select] option').length")


def test_three_libraries_are_one_dropdown_in_a_choose_skin_section_and_a_pick_posts(tmp_path):
    stub = _Stub(ROOT / "display" / "crissa-panel.html", {"/v1/services": _services(), "/v1/avatar": _avatar(3),
                                                           "/v1/caps": {"ok": True, "gpu": False}})
    pg = _page("http://127.0.0.1:%d/" % stub.port, tmp_path)
    try:
        assert _wait(pg, "document.querySelectorAll('[data-avatar-tile]').length") == 1
        assert pg.js("document.querySelector('[data-avatar-head] span').textContent") == "Choose skin"
        # the section sits right under the Wall services (the player's grid), before the Voices
        assert pg.js("document.querySelector('[data-unit=\"proc:player\"]').parentNode.nextSibling.getAttribute('data-avatar-head')") == "1"
        assert pg.js("document.querySelector('[data-avatar-tile] .st').textContent") == "wearing Crissa"
        assert _skin_select(pg) == 3
        assert pg.js("document.querySelector('[data-avatar-select]').value") == "crissa"
        assert pg.js("document.querySelectorAll('[data-avatar-more], .avatar-pop, [data-avatar-tile] button').length") == 0
        pg.js("var s=document.querySelector('[data-avatar-select]'); s.value='friday'; s.dispatchEvent(new Event('change',{bubbles:true}))")
        import time
        end = time.time() + 10
        while time.time() < end and not stub.posts:
            time.sleep(0.2)
        assert stub.posts and stub.posts[0] == ("/v1/avatar", {"key": "friday"})
    finally:
        pg.close()
        stub.close()


def test_two_libraries_are_the_same_dropdown(tmp_path):
    stub = _Stub(ROOT / "display" / "crissa-panel.html", {"/v1/services": _services(), "/v1/avatar": _avatar(2),
                                                           "/v1/caps": {"ok": True, "gpu": False}})
    pg = _page("http://127.0.0.1:%d/" % stub.port, tmp_path)
    try:
        assert _wait(pg, "document.querySelectorAll('[data-avatar-tile]').length") == 1
        assert _skin_select(pg) == 2
        assert pg.js("document.querySelectorAll('[data-avatar-tile] button, [data-avatar-more]').length") == 0
        # the option says what the library is: its source and size
        assert "480" in pg.js("document.querySelector('[data-avatar-select] option[value=friday]').textContent")
    finally:
        pg.close()
        stub.close()
