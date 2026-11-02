"""Round 15, the frozen build (2026-10-12; docs/checkpoints/round15-build-20261012). On .101 the first start's build
froze her picture for four minutes while the Wall's speed line said 21-22/s "on the glass"; the User: "needs a popup
building crissa".

Measured on a .101-like box (glasssample.py reading the screen): the Wall PASTED 19-24 pictures a second and the screen
had 4.5-13, stills up to a second, the X server answering in under 2 ms - Tk draws the canvas as an idle callback that
a busy Wall never reaches, and each redraw read the whole glass back for the soft HUD, roll and chrome layers. Pinned
here: every paste is drawn into the window at once and the speed line counts that; the soft layers go up as the pieces
they draw (the same pixels); the build decodes its own frames in a process of its own and never asks the live player;
it runs on two workers under her film and on all of them only while Setup covers the glass; the plate's words.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii as WALL  # noqa: E402
import house_wall_ascii_render as R  # noqa: E402
import wall_ascii_disk as D  # noqa: E402
import crissa_build as B  # noqa: E402
from test_wall_prefill_20261012 import _Player, _spec  # noqa: E402

WALL_SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text()
COND_SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


# ---- the glass: drawn, not pasted --------------------------------------------------------------------------------
def test_the_round_15_switches_are_on_in_a_pack_off_on_the_house_and_the_word_decides(monkeypatch):
    import crissa_home
    for name in ("HOUSE_WALL_DRAW_NOW", "HOUSE_WALL_DRAWN_LINE", "HOUSE_WALL_LAYER_PIECES"):
        assert '_pack_switch("%s")' % name in WALL_SRC
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(crissa_home, "is_house", lambda: False)
        assert WALL._pack_switch(name) is True
        monkeypatch.setattr(crissa_home, "is_house", lambda: True)
        assert WALL._pack_switch(name) is False                  # the house's desk Wall as it was
        monkeypatch.setenv(name, "1")
        assert WALL._pack_switch(name) is True
        monkeypatch.setenv(name, "0")
        monkeypatch.setattr(crissa_home, "is_house", lambda: False)
        assert WALL._pack_switch(name) is False


def test_every_paste_is_drawn_into_the_window_at_once_and_the_switch_puts_idle_back():
    pump = WALL_SRC[WALL_SRC.index("    def pump():"):WALL_SRC.index("    floater = {")]
    assert pump.count("_draw_now()") == 2                     # after the whole picture and after a mouth patch
    i = pump.index('_blit("bg", bg_item, pkt["bg"])')
    assert pump.index("_draw_now()", i) < pump.index('if "bg_patch" in pkt', i)
    draw = WALL_SRC[WALL_SRC.index("    def _draw_now():"):WALL_SRC.index("    def pump():")]
    assert "if DRAW_NOW:" in draw and "cv.update_idletasks()" in draw and 'speed["drawn"] += 1' in draw
    assert "elif DRAWN_LINE and not redraw" in draw and "root.after_idle(_redrawn)" in draw   # count idle's redraws


def test_a_packs_speed_line_counts_what_was_drawn_and_the_house_keeps_its_line():
    line = WALL_SRC[WALL_SRC.index("# DRAWN, NOT MADE"):WALL_SRC.index("# the round-12 line (the house)")]
    line = "\n".join(ln for ln in line.splitlines() if not ln.strip().startswith("#"))      # the code, not its story
    assert "/s drawn:" in line and "speed['drawn'] / speed['every']" in line and "on the glass" not in line
    assert "; x {_ms('xrt')}" in line and ") if DRAWN_LINE else" in line
    assert "if DRAWN_LINE and now - speed[\"xrt_at\"] >= 1.0" in WALL_SRC and "root.winfo_pointerxy()" in WALL_SRC


def _layer(boxes, size=(1280, 720), seed=1):
    rnd = random.Random(seed)
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for b in boxes:
        d.rounded_rectangle(b, 9, fill=(rnd.randrange(255), 40, 40, rnd.choice((90, 150, 230))))
        d.text((b[0] + 4, b[1] + 4), "12:34", fill=(230, 230, 230, 255))
    return im


@pytest.mark.parametrize("boxes", [
    [(10, 10, 300, 80), (1000, 10, 1270, 150), (10, 600, 200, 710), (1100, 560, 1270, 710)],
    [(400, 600, 900, 660)],
    [(0, 0, 1279, 719)],
    [(5, 5, 7, 7), (1270, 700, 1279, 719)],
])
def test_a_soft_layer_in_pieces_is_the_same_pixels(boxes):
    im = _layer(boxes)
    got = WALL.layer_pieces(im)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    for x, y, crop in got:
        assert crop.getbbox() == (0, 0) + crop.size           # each piece is cropped to what it draws
        out.paste(crop, (x, y))
    assert out.tobytes() == im.tobytes()
    area = sum(c.size[0] * c.size[1] for _, _, c in got)
    assert area <= im.size[0] * im.size[1]


def test_four_corners_are_four_pieces_and_an_empty_layer_is_none():
    got = WALL.layer_pieces(_layer([(10, 10, 300, 80), (1000, 10, 1270, 150), (10, 600, 200, 710),
                                    (1100, 560, 1270, 710)]))
    assert len(got) == 4
    assert WALL.layer_pieces(Image.new("RGBA", (640, 360), (0, 0, 0, 0))) == []
    rgb = Image.new("RGB", (64, 64))
    assert WALL.layer_pieces(rgb) == [(0, 0, rgb)]


def test_the_hud_roll_and_chrome_go_up_in_pieces_and_keep_their_whole_picture():
    assert WALL.PIECE_KEYS == ("hud", "roll", "chrome")
    blit = WALL_SRC[WALL_SRC.index("    def _blit(key, item, im):"):WALL_SRC.index("    ontop = {")]
    assert "if LAYER_PIECES and key in PIECE_KEYS:" in blit and "last_im[key] = im" in blit
    pieces = WALL_SRC[WALL_SRC.index("    def _blit_pieces("):WALL_SRC.index("    def _blit(key, item, im):")]
    assert "cv.tag_raise(it, item)" in pieces                 # the layer's place in the stack
    assert 'state="hidden"' in pieces                          # pieces it no longer draws are hidden, not left


# ---- the build: its own frames, under her film ------------------------------------------------------------------
def test_two_workers_under_her_film_and_all_of_them_under_setup():
    assert D.WORKERS_UP == 2
    assert D.workers_up(4, {}) == 2
    assert D.workers_up(1, {}) == 1
    assert D.workers_up(4, {"HOUSE_WALL_ASCII_PREFILL_WORKERS_UP": "2"}) == 2
    assert D.workers_up(4, {"HOUSE_WALL_ASCII_PREFILL_WORKERS_UP": "0"}) == 4      # round 14's way
    assert D.workers_up(2, {"HOUSE_WALL_ASCII_PREFILL_WORKERS_UP": "9"}) == 2


def test_setup_covers_the_glass_only_while_it_runs(tmp_path):
    assert B.under_cover(tmp_path) is False
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
    assert B.under_cover(tmp_path) is True
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "done"}))
    assert B.under_cover(tmp_path) is False
    (tmp_path / "init-run.json").write_text("{")
    assert B.under_cover(tmp_path) is False


def _stub_source(pack: Path, serve: bool):
    """A pack whose scripts/house-conductor.py --clip-frames is a stand-in: noise on stdout, then its port line."""
    (pack / "scripts").mkdir(parents=True, exist_ok=True)
    body = ["import json, sys", "print('[conductor] the inventory says hello', flush=True)"]
    if serve:
        body += ["sys.path.insert(0, %r); sys.path.insert(0, %r)" % (str(ROOT / "lib"), str(ROOT / "tests")),
                 "from test_wall_prefill_20261012 import _Player", "p = _Player()",
                 "port = int(p.url.rsplit(':', 1)[1])"]
    else:
        body += ["port = 54321"]
    body += ["assert sys.argv[1:] == ['--clip-frames', '0'], sys.argv",
             "print(json.dumps({'clip_frames': port, 'pid': 1}), flush=True)", "sys.stdin.read()"]
    (pack / "scripts" / "house-conductor.py").write_text("\n".join(body) + "\n")


def test_the_builds_own_frames_start_from_the_packs_player_code_and_the_switch_says_player(tmp_path):
    _stub_source(tmp_path, serve=False)
    p, url = D.own_source(tmp_path, log=lambda m: None, env=dict(os.environ))
    try:
        assert url == "http://127.0.0.1:54321" and p is not None and p.poll() is None
    finally:
        p.stdin.close(); p.wait(timeout=10)                      # its parent's pipe closed: it goes
    assert D.own_source(tmp_path, env={"HOUSE_WALL_ASCII_PREFILL_SOURCE": "player"}) == (None, "")
    assert D.own_source(tmp_path / "nowhere", env={}) == (None, "")


def test_a_build_never_asks_the_live_player_for_a_frame_and_says_how_many_loops_are_left(tmp_path):
    live = _Player()
    live.frames = {}                                             # the live player has nothing to give a build
    try:
        spec, _ = _spec(tmp_path, live.url, home=str(tmp_path))
        _stub_source(Path(spec["pack"]), serve=True)
        e = dict(os.environ, HOUSE_WALL_ASCII_DISK_FLOOR_GB="0", HOUSE_WALL_ASCII_PREFILL_WORKERS="2",
                 HOUSE_WALL_ASCII_PREFILL_WORKERS_UP="1")
        r = subprocess.run([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                           cwd=str(ROOT / "lib"), env=e, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-800:]
        assert "frames from its own decoder" in r.stderr and "on 1 of 2 workers (under her film)" in r.stderr
        prog = json.loads((Path(spec["dir"]) / "progress.json").read_text())
        assert prog["done"] and prog["built"] == 10 and prog["workers_now"] == 1 and prog["cover"] is False
        assert prog["loops_total"] == 8 and prog["loops_built"] == 8 and prog["loops_counted"]
        # the ten-minute re-ask: every clip is whole on disk, so nothing is decoded and no source is started
        r2 = subprocess.run([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                            cwd=str(ROOT / "lib"), env=e, capture_output=True, text=True, timeout=180)
        assert r2.returncode == 0 and "wrote 0" in r2.stderr and "frames from" not in r2.stderr, r2.stderr[-600:]
    finally:
        live.close()


def test_under_setup_the_build_uses_every_worker(tmp_path, monkeypatch):
    live = _Player()
    try:
        (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
        (tmp_path / "machine.json").write_text(json.dumps({"permission": "skip", "skipped": "user"}))  # (round 16)
        spec, _ = _spec(tmp_path, live.url, home=str(tmp_path))
        e = dict(os.environ, HOUSE_WALL_ASCII_DISK_FLOOR_GB="0", HOUSE_WALL_ASCII_PREFILL_WORKERS="2",
                 HOUSE_WALL_ASCII_PREFILL_WORKERS_UP="1")
        r = subprocess.run([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                           cwd=str(ROOT / "lib"), env=e, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-800:]
        assert "on 2 of 2 workers (Setup covers the glass)" in r.stderr and "frames from the player" in r.stderr
        prog = json.loads((Path(spec["dir"]) / "progress.json").read_text())
        assert prog["done"] and prog["workers_now"] == 2 and prog["cover"] is True
    finally:
        live.close()


def test_the_players_route_and_the_builds_source_answer_from_one_function():
    assert COND_SRC.count('if head == "/v1/clip-frame":') == 1       # the tests' anchor stays single
    route = COND_SRC[COND_SRC.index('if head == "/v1/clip-frame":'):]
    route = route[:route.index("\n        if head ")]
    assert "return self._clip_frame(ph.lib, ph.seq.graph)" in route
    main_ = COND_SRC[COND_SRC.index("def clip_frames_main("):COND_SRC.index("\n\n\n", COND_SRC.index("def clip_frames_main("))]
    assert "return self._clip_frame(lib, graph)" in main_ and '"127.0.0.1"' in main_
    assert "Library(merge_face_bank_clips(clips))" in main_      # the player's own library, built as main() builds it
    meth = COND_SRC[COND_SRC.index("    def _clip_frame(self, lib, graph)"):]
    assert "clip_frame_reply(lib, graph, ck, ci)" in meth[:1500]
    tail = COND_SRC[COND_SRC.index('if __name__ == "__main__":'):]
    assert 'sys.argv[1] == "--clip-frames"' in tail and "main()" in tail


# ---- the plate ------------------------------------------------------------------------------------------------------
def _prog(d: Path, **kw):
    base = {"total": 13031, "built": 900, "done": False, "stopped": "", "loops_done": False, "loops_total": 2050,
            "loops_built": 900, "loops_counted": True, "rate": 10.0, "at": time.time()}
    base.update(kw)
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(base))


def test_the_plate_names_her_the_loops_pictures_and_a_time_left_and_goes_when_the_loops_are_built(tmp_path, monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "her_name", lambda *a, **k: "Crissa")
    d = tmp_path / "cache" / "ascii" / "abc"
    _prog(d)
    pv = B.plate_view(B.build_view(tmp_path))
    assert pv == {"show": True, "title": "Building Crissa…", "line": "900 of 2,050 pictures · about 2 min left",
                  "done": 900 / 2050}                                       # round 30: the Building bar's fraction
    _prog(d, rate=None)
    assert B.plate_view(B.build_view(tmp_path))["line"] == "900 of 2,050 pictures"
    _prog(d, loops_built=2000, rate=10.0)
    assert B.plate_view(B.build_view(tmp_path))["line"].endswith("less than a minute left")
    _prog(d, loops_counted=False)
    assert B.plate_view(B.build_view(tmp_path))["line"] == "counting her pictures"
    _prog(d, loops_counted=False, loops_total=403, loops_built=403)
    assert B.plate_view(B.build_view(tmp_path)) == {"show": False}          # nothing of hers to draw: no plate
    _prog(d, loops_done=True)
    assert B.plate_view(B.build_view(tmp_path)) == {"show": False}          # the rest goes on silently
    _prog(d, at=time.time() - 120)
    assert B.plate_view(B.build_view(tmp_path)) == {"show": False}          # a build that stopped writing is not running


def test_the_disk_tiers_own_dir_is_where_the_plate_looks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK_DIR", str(tmp_path / "elsewhere"))
    assert B.root() == tmp_path / "elsewhere"
    assert B.root(tmp_path) == tmp_path / "cache" / "ascii"


def test_one_plate_on_both_walls_closed_then_starting_then_ready_then_building(monkeypatch):
    import crissa_wall_key as K
    monkeypatch.setenv("CRISSA_BUILD_BAR", "0")      # the one line, as before (round 30's bar: test_round30_20261018)
    build = {"show": True, "title": "Building Crissa\u2026", "line": "900 of 2,050 pictures \u00b7 about 2 min left"}
    mem = {}
    start = lambda ready: {"applies": True, "ready": ready, "started": "a", "line": "Starting Crissa\u2026 ear"}  # noqa
    assert K.plate_state({"agent": {"closed": True}, "build": build}, 0, mem)["kind"] == "closed"
    assert K.plate_state({"start": start(False), "build": build}, 1, mem)["kind"] == "starting"   # the start first
    assert K.plate_state({"start": start(True), "build": build}, 2, mem)["kind"] == "ready"
    got = K.plate_state({"start": start(True), "build": build}, 2 + K.READY_HOLD_S + 0.1, mem)
    assert got == {"kind": "building", "click": None,
                   "text": "Building Crissa\u2026  \u00b7  900 of 2,050 pictures \u00b7 about 2 min left"}
    assert K.plate_state({"build": build}, 0, {})["kind"] == "building"                       # no start bar at all
    assert K.plate_state({"build": {"show": False}}, 0, {})["kind"] == ""
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert 'if (!closed && !kind && bd.show && bd.title) kind = "building";' in page
    assert "body.building #agentPlate" in page
    for word in ("build_item", "BUILD_PLATE", "render_build_plate"):          # one plate, not a second overlay
        assert word not in WALL_SRC and word not in (ROOT / "lib" / "house_wall_ascii_render.py").read_text()


def test_the_menu_carries_the_build_beside_the_start_and_never_while_setup_runs(tmp_path, monkeypatch):
    import crissa_home
    import crissa_menu as M
    monkeypatch.setattr(crissa_home, "her_name", lambda *a, **k: "Crissa")
    _prog(tmp_path / "cache" / "ascii" / "abc")
    got = M.build_view(tmp_path, init_running=False, env={})
    assert got["show"] and got["title"] == "Building Crissa\u2026"
    assert M.build_view(tmp_path, init_running=True, env={}) == {"show": False}           # Setup's own rule
    assert M.build_view(tmp_path, init_running=False, env={"CRISSA_BUILD_PLATE": "0"}) == {"show": False}
    assert '"build": build_view(home),' in (ROOT / "lib" / "crissa_menu.py").read_text()


# ---- her first words at the density this machine holds -----------------------------------------------------------
def _film(path: Path, n=4, size=(736, 400)):
    import cv2
    import numpy as np
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24, size)
    rng = np.random.default_rng(3)
    for _ in range(n):
        w.write(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    w.release()
    return path


def test_the_density_probe_picks_the_highest_that_fits_a_frame_and_says_what_it_timed(tmp_path):
    film = _film(tmp_path / "listen.mp4")
    got = D.density_probe(320, 180, film, candidates=(1.0, 5.0, 2.0), frames=3, fps=1.0)   # a bar any box clears
    assert got["frames"] == 3 and got["bar_ms"] == 1000.0
    assert got["pick"] == 5.0 and [c["density"] for c in got["candidates"]] == [5.0]     # highest first, stops there
    none = D.density_probe(320, 180, film, candidates=(2.0, 1.0), frames=2, fps=1e6)  # nothing fits a microsecond
    assert none["pick"] is None and [c["density"] for c in none["candidates"]] == [2.0, 1.0]
    assert D.density_probe(320, 180, tmp_path / "missing.mp4")["pick"] is None


def test_the_card_runs_the_walls_own_probe_on_the_walls_size(tmp_path, monkeypatch):
    import crissa_machine as M
    th = M.thresholds(tmp_path)
    assert M.stage_density(th, tmp_path, {})[1] == "density: the desk Wall has not said its size yet"
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "wall-glass.json").write_text(json.dumps({"W": 3440, "H": 1440}))
    monkeypatch.setattr(M, "_listen_film", lambda: tmp_path / "listen.mp4")
    seen = {}

    class R_:
        stdout = json.dumps({"pick": 5.0, "candidates": [{"density": 5.0, "p50_ms": 30.0}], "bar_ms": 41.7}) + "\n"

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return R_()
    st, why = M.stage_density(th, tmp_path, {}, run=run)
    assert why == "" and st["pick"] == 5.0
    assert seen["argv"][1].endswith("lib/wall_ascii_disk.py") and seen["argv"][2] == "--density-probe"
    arg = json.loads(seen["argv"][3])
    assert (arg["W"], arg["H"], arg["candidates"][0], arg["fps"]) == (3440, 1440, 5.0, 24.0)
    assert M.stage_density(th, tmp_path, {"CRISSA_MACHINE_DENSITY": "0"}) == (None, "")


def test_the_pick_becomes_her_density_unless_the_person_set_one(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_DENSITY_ONE_FINER", "0")      # round 27 has its own test
    import house_wall_prefs as WP
    import crissa_machine as M
    monkeypatch.setattr(WP, "PATH", tmp_path / "house-wall-ascii.prefs.json")
    posted = []
    out = M.apply_density({"pick": 5.0}, tmp_path, post=posted.append)
    assert out["applied"] and WP.load()["density"] == 5.0
    assert json.loads((tmp_path / M.DENSITY_PICK).read_text())["density"] == 5.0
    assert posted == []                                         # the house never posts (tests run as the house)
    out = M.apply_density({"pick": 4.0}, tmp_path, post=posted.append)   # still the card's own last pick: re-picked
    assert out["applied"] and WP.load()["density"] == 4.0
    WP.save(dict(WP.load(), density=2.5))                       # the person turned the dial
    out = M.apply_density({"pick": 5.0}, tmp_path, post=posted.append)
    assert not out["applied"] and out["why"] == "the person set 2.5" and WP.load()["density"] == 2.5
    assert M.apply_density({"pick": None}, tmp_path)["applied"] is False


def test_a_new_look_on_the_glass_stops_the_old_looks_build_and_starts_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_DISK", "1")
    t = D.DiskTier(root=tmp_path, env={"HOUSE_WALL_ASCII_DISK": "1", "HOUSE_WALL_FEED": "http://127.0.0.1:9"})
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    started = []

    class P_:
        pid = 0

        def poll(self):
            return None
    monkeypatch.setattr(D.subprocess, "Popen", lambda argv, **kw: started.append(argv) or P_())
    fk_old = (64, 36, 1.0, True, None, None, ("whole", 1.0))
    t.fdir = tmp_path / D.framing_dir_name(fk_old)
    t.prefill_p, t.prefill_for = old, t.fdir.name
    t._prefill(fk_old)
    assert not started and old.poll() is None                  # the same look: the build goes on
    fk_new = (64, 36, 5.0, True, None, None, ("whole", 1.0))
    t.fdir = tmp_path / D.framing_dir_name(fk_new)
    t._prefill(fk_new)
    assert old.wait(timeout=10) is not None                     # the old look's build stopped
    assert len(started) == 1 and started[0][2] == "--prefill" and t.prefill_for == t.fdir.name
    assert json.loads(started[0][3])["density"] == 5.0
