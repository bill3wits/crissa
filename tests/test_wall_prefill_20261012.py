"""Round 14: her pictures built on this machine, at idle, from the first start (2026-10-12; the User: ship them or build
them? "build locally, in Setup"; "Give me a few minutes to build Crissa for your screen", "just on the initial loading
screen"). docs/checkpoints/round14-prefill-20261012.

Pinned: the player answers any clip frame unpainted (/v1/clip-frame); the pre-fill draws every clip from it - the listen
hub first, then the work hub, the other loops by how often she enters them, then the rest - on idle-priority workers held
while she or the person talks, skipping what is there, stopping at the disk budget, writing progress; what it draws is
the fresh render (a byte match for an unpainted live frame, a region base for a painted one); a landed pack asks for a
build; the opening screen and the Control Panel read the progress.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_disk as D  # noqa: E402
import wall_frame as WF  # noqa: E402
from test_wall_ascii_cache import W, H, _cache, _fresh_state, _jpeg, _mouth, _scene  # noqa: E402

BOX = (330.0, 220.0, 60.0, 22.0)
FP = (0.5171, 0.5487)
CLIPS = {"listen": ("loop", 5), "work": ("loop", 3), "fl-chin": ("flourish", 2)}


class _Player:
    """A stand-in for the pack's player: /v1/clip-frame for CLIPS (synthetic frames), /v1/state with on_air."""

    def __init__(self):
        self.on_air = False
        self.frames = {k: [_jpeg(_scene(2000 + 10 * j + i)) for i in range(n)]
                       for j, (k, (_, n)) in enumerate(CLIPS.items())}
        me = self

        class H_(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                u = urlparse(self.path)
                if u.path == "/v1/state":
                    b = json.dumps({"on_air": me.on_air}).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
                    self.wfile.write(b); return
                q = parse_qs(u.query)
                k, i = (q.get("key") or [""])[0], int((q.get("i") or ["-1"])[0])
                if u.path != "/v1/clip-frame" or k not in me.frames or not 0 <= i < len(me.frames[k]):
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
                b = me.frames[k][i]
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.send_header("X-Mouth-Box", "%.1f,%.1f,%.1f,%.1f" % BOX)
                self.send_header("X-Clip", "%s;%d;%d;%s" % (k, i, len(me.frames[k]), CLIPS[k][0]))
                self.send_header("X-Face-Point", "%r,%r" % FP)
                self.end_headers(); self.wfile.write(b)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H_)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def player():
    p = _Player()
    yield p
    p.close()


def _pack(tmp_path) -> Path:
    pk = tmp_path / "pack"
    (pk / "display" / "crissa-poses").mkdir(parents=True)
    (pk / "avatars" / "crissa").mkdir(parents=True)
    (pk / "display" / "crissa-poses" / "inventory.json").write_text(json.dumps(
        {"clips": {k: {"role": r, "file": k + ".mp4"} for k, (r, _) in CLIPS.items()}}))
    (pk / "avatars" / "crissa" / "manifest.json").write_text(json.dumps({"boot_loop": "listen"}))
    return pk


def _spec(tmp_path, player_url, density=1.0, **kw):
    g = R.Geom(W, H, density=density)
    root = tmp_path / "cache" / "ascii"
    fk = (g.W, g.H, g.density, True, None, None, ("whole", 1.0))
    spec = {"root": str(root), "dir": str(root / D.framing_dir_name(fk)), "W": W, "H": H, "density": g.density,
            "hole_zoom": None, "shift": None, "mz": ["whole", 1.0], "norm": True, "feed": player_url,
            "player": player_url, "ear": "", "pack": str(_pack(tmp_path)),
            "home": str(tmp_path)}                    # round 16: Setup's state is the test's own, never this machine's
    spec.update(kw)
    return spec, g


def _run(spec, env=None, timeout=120):
    e = dict(os.environ, HOUSE_WALL_ASCII_DISK_FLOOR_GB="0", HOUSE_WALL_ASCII_PREFILL_WORKERS="2", **(env or {}))
    return subprocess.run([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                          cwd=str(ROOT / "lib"), env=e, capture_output=True, text=True, timeout=timeout)


def test_the_order_is_the_listen_hub_the_work_hub_the_loops_by_use_then_the_rest(tmp_path):
    pk = _pack(tmp_path)
    order, loops = D.clip_order(pk, {"fl-chin": 9})
    assert order == ["listen", "work", "fl-chin"] and loops == {"listen", "work"}


def test_workers_are_a_quarter_of_the_cpus_one_to_four():
    assert 1 <= D.workers_for({}) <= 4
    assert D.workers_for({"HOUSE_WALL_ASCII_PREFILL_WORKERS": "9"}) == 4
    assert D.workers_for({"HOUSE_WALL_ASCII_PREFILL_WORKERS": "2"}) == 2


def test_a_first_run_prefill_draws_every_clip_exactly_and_says_how_far(tmp_path, player, monkeypatch):
    spec, g = _spec(tmp_path, player.url)
    r = _run(spec)
    assert r.returncode == 0, r.stderr[-800:]
    prog = json.loads((Path(spec["dir"]) / "progress.json").read_text())
    assert prog["done"] and prog["total"] == 10 and prog["built"] == 10 and prog["listen_done_s"] is not None
    # what it drew is the fresh render of the player's unpainted frame, and the live lap reads it back
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "1")
    monkeypatch.setenv("HOUSE_WALL_ASCII_PREFILL", "0")     # round 16: the cache reads what the run above wrote, no more
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_DIR", spec["root"])
    _fresh_state()
    c = _cache()
    t0 = time.time()
    while not c.disk._scanned.is_set() and time.time() - t0 < 10:
        time.sleep(0.02)
    c.prepare(g)
    frame = ("whole", 1.0) + tuple(WF.face_of(FP))                      # the whole frame (the house's switch says kiosk)
    for i, jp in enumerate(player.frames["listen"]):
        p, how, _ = c.picture(jp, g, True, 0.0, mouth=BOX, frame=frame, clip=("listen", i, 5, "loop"))
        assert how == "disk", (i, how)                                   # an unpainted live frame: a byte match
        assert bytes(p.px) == R.coverage_fresh(R._open(jp), g, True, 0.0, mouth=BOX, frame=frame).tobytes()
    # a painted idle mouth: a region over the pre-filled picture, exact
    c2 = _cache()
    while not c2.disk._scanned.is_set():
        time.sleep(0.02)
    c2.prepare(g)
    jp = _jpeg(_mouth(_scene(2000), (330, 220, 60, 22), 3))
    p, how, _ = c2.picture(jp, g, True, 0.0, mouth=BOX, frame=frame, clip=("listen", 0, 5, "loop"))
    assert how == "disk-region" and bytes(p.px) == R.coverage_fresh(R._open(jp), g, True, 0.0, mouth=BOX,
                                                                     frame=frame).tobytes()
    # a second run has nothing to draw
    r2 = _run(spec)
    assert r2.returncode == 0 and "wrote 0" in r2.stderr


def test_the_prefill_waits_while_she_is_on_air(tmp_path, player):
    spec, _ = _spec(tmp_path, player.url)
    player.on_air = True
    p = subprocess.Popen([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                         cwd=str(ROOT / "lib"), env=dict(os.environ, HOUSE_WALL_ASCII_DISK_FLOOR_GB="0",
                                                         HOUSE_WALL_ASCII_PREFILL_WORKERS="2"),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(4.0)
    assert not list(Path(spec["dir"]).rglob("*.pic"))                   # nothing drawn while she talks
    player.on_air = False
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0 and len(list(Path(spec["dir"]).rglob("*.pic"))) == 10, err[-600:]


def test_the_disk_budget_stops_it_and_says_so(tmp_path, player):
    spec, _ = _spec(tmp_path, player.url)
    r = _run(spec, env={"HOUSE_WALL_ASCII_DISK_GB": str(3.5 * W * H / 2 ** 30)})
    prog = json.loads((Path(spec["dir"]) / "progress.json").read_text())
    assert prog["stopped"] == "the disk budget" and not prog["done"] and prog["built"] < 10
    view = D.build_view(tmp_path)
    assert view["line"] == "" and "limited by the disk budget" in view["note"]


def test_the_opening_screens_line_and_the_panels_numbers(tmp_path, monkeypatch):
    d = tmp_path / "cache" / "ascii" / "abc"
    d.mkdir(parents=True)
    (d / "progress.json").write_text(json.dumps({"total": 200, "built": 68, "done": False, "stopped": "",
                                                 "loops_done": False, "disk_used": 2 ** 30, "disk_cap": 100 * 2 ** 30,
                                                 "at": time.time()}))
    import crissa_home
    monkeypatch.setattr(crissa_home, "her_name", lambda *a, **k: "Crissa")
    v = D.build_view(tmp_path)
    assert v["running"] and v["line"] == "Give me a few minutes to build Crissa for your screen: 34 %."
    assert "68 of 200" in v["note"] and "1.0 of 100 GB" in v["note"]
    for word in ("cache", "picture", "framing"):
        assert word not in v["line"].lower()
    (d / "progress.json").write_text(json.dumps({"total": 200, "built": 150, "done": False, "loops_done": True,
                                                 "at": time.time()}))
    assert D.build_view(tmp_path)["line"] == ""                          # her loops are built: no splash, it goes on silently
    (d / "progress.json").write_text(json.dumps({"total": 200, "built": 200, "done": True, "at": time.time()}))
    assert D.build_view(tmp_path)["line"] == ""                          # done: nothing on any screen


def test_the_splash_is_at_setups_end_only_and_the_panel_has_a_row():
    page = (ROOT / "display" / "crissa-init.html").read_text()
    assert 'if (snap.status === "done") kids.push(el("p", { class: "hint", id: "b-build", text: "" }));' in page
    assert 'snap.status === "done" ? ((snap.build || {}).line || "") : ""' in page
    assert '"build": _build_line(),' in (ROOT / "lib" / "crissa_init.py").read_text()
    panel = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert "rows += launcher_rows() + lane_rows() + build_rows()" in panel and '"label": "Built for this screen"' in panel


def test_a_landed_pack_asks_for_a_build_and_the_wall_starts_it(tmp_path, monkeypatch):
    assert "crissa_build.request_prefill(sorted(p[\"clips_add\"]), home=root(home).parent)" in \
        (ROOT / "lib" / "crissa_import.py").read_text()
    started = []
    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: started.append(a) or type("P", (), {"poll": lambda s: 0})())
    t = D.DiskTier(root=tmp_path / "cache" / "ascii", env={"HOUSE_WALL_ASCII_DISK_GB": "1",
                                                          "HOUSE_WALL_FEED": "http://127.0.0.1:9"})
    t._scanned.wait(5)
    t.prefill_asked = 0.0
    fk = (W, H, 1.0, True, None, None, ("whole", 1.0))
    t.framing(fk)                                                         # the first picture: a run
    t._prefill(fk)
    assert len(started) == 1                                             # not again within PREFILL_EVERY_S
    time.sleep(0.05)
    D.request_prefill(["new-loop"], home=tmp_path)                       # (re-exported from crissa_build)                       # then a pack lands
    req = json.loads((tmp_path / "cache" / "ascii" / "prefill.request").read_text())
    assert req["clips"] == ["new-loop"]
    t._prefill(fk)
    assert len(started) == 2                                             # the request is newer than the last run


def test_the_player_serves_a_clip_frame_unpainted_and_the_kill_hides_it():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/clip-frame":') == 1
    assert 'CLIP_FRAME_ROUTE = os.environ.get("HOUSE_CONDUCTOR_CLIP_FRAME", "1")' in src
    route = src.split('if head == "/v1/clip-frame":', 1)[1].split("        if head in (", 1)[0]
    # round 15: the body is clip_frame_reply, which the build's own decoder (--clip-frames) answers from too
    assert "return self._clip_frame(ph.lib, ph.seq.graph)" in route
    body = src[src.index("def clip_frame_reply("):src.index("def clip_frames_main(")]
    assert "lib.frames(ck)" in body and "cv2.imencode(\".jpg\", arr[ci], [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_Q])" in body
    assert "X-Face-Point" in body and "mouth_box_header(box_)" in body


def test_the_codec_is_zstd_when_this_python_has_it_else_raw_and_both_read_back(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_CODEC", "raw")
    assert D.codec() == "raw"
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_CODEC", "auto")
    assert D.codec() == ("zstd-1" if D._zstd() is not None else "raw")
    px = bytes(range(256)) * 64
    h_, payload = D.pack(64, 256, 1, 1, b"\0", (1, 1), b"\0", b"", "x", None, px)
    f = tmp_path / "e.pic"
    f.write_bytes(h_ + b"".join(payload))
    assert bytes(D.read_entry(f)["px"]) == px
