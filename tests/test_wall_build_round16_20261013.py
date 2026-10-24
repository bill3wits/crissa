"""Round 16, the build's final touches (2026-10-13; docs/checkpoints/round16-build-20261013). .101's fresh Setup: the
machine card picked density 3.0 and the glass and the build stayed at 1.0 (its post went to an address a pack does not
have); Lila's welcome lost its lips while the build ran (the Wall drew 5-10/s, Setup's plate a whole-glass soft layer);
the house's Wall said disk=1.5/40GB while its look held 40 GB. Pinned here: the card posts to the record the Wall
reads and the Wall takes a new pick itself; a first Setup's build waits for the pick; the old look goes once the new
one is whole; the build yields while a film speaks; Setup's and Voice ID's plates go up in pieces; the disk tier
re-counts after a pre-fill.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import house_wall_ascii as WALL  # noqa: E402
import wall_ascii_disk as D  # noqa: E402
import crissa_build as B  # noqa: E402
from test_wall_prefill_20261012 import _Player, _spec  # noqa: E402

WALL_SRC = (ROOT / "lib" / "house_wall_ascii.py").read_text()
COND_SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


# ---- (a) the pick reaches the glass and the build ----------------------------------------------------------------
def test_the_wall_takes_a_pick_made_after_it_started_once(tmp_path):
    assert WALL.pick_to_take(tmp_path, 0.0, t0=100.0) is None                      # no pick
    (tmp_path / "density-pick.json").write_text(json.dumps({"density": 3.0, "ts": 90.0}))
    assert WALL.pick_to_take(tmp_path, 0.0, t0=100.0) is None                      # older than this Wall: a restart
    (tmp_path / "density-pick.json").write_text(json.dumps({"density": 3.0, "ts": 150.0}))
    assert WALL.pick_to_take(tmp_path, 0.0, t0=100.0) == (3.0, 150.0, "the machine card")   # round 30: who picked it
    assert WALL.pick_to_take(tmp_path, 150.0, t0=100.0) is None                    # taken once
    pump = WALL_SRC[WALL_SRC.index("    def pump():"):WALL_SRC.index("    floater = {")]
    assert "if TAKE_PICK and now - pick[\"at\"] >= 1.0 and not crissa_home.is_house():" in pump
    assert '_glass_post({"density": got_[0]})' in pump


def test_the_card_reads_and_posts_the_record_the_wall_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISSA_DENSITY_ONE_FINER", "0")      # round 27 has its own test
    import house_wall_prefs as WP
    import crissa_home
    import crissa_machine as M
    monkeypatch.setattr(WP, "PATH", tmp_path / "house-wall-ascii.prefs.json")
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    monkeypatch.setattr(crissa_home, "house_value", lambda key, paths=None: "")
    monkeypatch.delenv("HOUSE_WALL_HOUSE", raising=False)
    import crissa_ports
    assert M._glass_url() == crissa_ports.url("feed")                                # a pack: the feed, as the Wall
    posted = []
    rec = {"density": 1.0}
    out = M.apply_density({"pick": 3.0}, tmp_path, post=lambda q: posted.append((q.full_url, q.data)),
                          get=lambda u: json.dumps(rec).encode())
    assert out["applied"] and out["posted"] and out["was"] == 1.0
    assert posted[0][0].endswith("/v1/glass-prefs") and json.loads(posted[0][1]) == {"density": 3.0}
    pick = json.loads((tmp_path / M.DENSITY_PICK).read_text())
    assert pick["density"] == 3.0 and pick["ts"] > 0                                 # the Wall's clock
    rec["density"] = 2.0                                                             # the person turned the dial
    out = M.apply_density({"pick": 4.0}, tmp_path, post=posted.append, get=lambda u: json.dumps(rec).encode())
    assert not out["applied"] and out["why"] == "the person set 2"


def test_a_first_setups_build_waits_for_the_cards_pick(tmp_path):
    assert B.density_pending(tmp_path) is False                                    # no Setup
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
    assert B.density_pending(tmp_path) is True                                     # the card has not answered
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running", "only": ["voice_id"]}))
    assert B.density_pending(tmp_path) is False                                    # Voice ID again: never waits
    (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
    (tmp_path / "machine.json").write_text(json.dumps({"permission": "skip", "skipped": "user"}))
    assert B.density_pending(tmp_path) is False                                    # skipped: build this look
    (tmp_path / "machine.json").unlink()
    (tmp_path / "density-pick.json").write_text(json.dumps({"density": 3.0}))
    assert B.density_pending(tmp_path) is False


def test_the_build_holds_until_the_card_answers_then_draws(tmp_path):
    live = _Player()
    try:
        (tmp_path / "init-run.json").write_text(json.dumps({"status": "running"}))
        spec, _ = _spec(tmp_path, live.url, home=str(tmp_path))
        e = dict(os.environ, HOUSE_WALL_ASCII_DISK_FLOOR_GB="0", HOUSE_WALL_ASCII_PREFILL_WORKERS="2")
        p = subprocess.Popen([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                             cwd=str(ROOT / "lib"), env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(4.0)
        assert p.poll() is None and not list(Path(spec["dir"]).rglob("*.pic"))       # waiting, nothing drawn
        (tmp_path / "machine.json").write_text(json.dumps({"permission": "skip", "skipped": "user"}))
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0 and "waits for Setup's machine card" in err and "the card answered" in err, err[-600:]
        assert len(list(Path(spec["dir"]).rglob("*.pic"))) == 10
    finally:
        live.close()


def test_the_old_look_goes_once_the_new_one_is_whole(tmp_path):
    live = _Player()
    try:
        spec, _ = _spec(tmp_path, live.url, home=str(tmp_path))
        root = Path(spec["root"])
        old = root / "aaaaaaaaaaaaaaaaaaaa"
        (old / "listen").mkdir(parents=True)
        (old / "framing.json").write_text("{}")
        (old / "listen" / "00000-x.pic").write_bytes(b"x" * 1000)
        other = root / "not-a-look"
        other.mkdir()
        e = dict(os.environ, HOUSE_WALL_ASCII_DISK_FLOOR_GB="0", HOUSE_WALL_ASCII_PREFILL_WORKERS="2")
        r = subprocess.run([sys.executable, str(ROOT / "lib" / "wall_ascii_disk.py"), "--prefill", json.dumps(spec)],
                           cwd=str(ROOT / "lib"), env=e, capture_output=True, text=True, timeout=180)
        assert r.returncode == 0 and "look aaaaaaaaaaaaaaaaaaaa removed" in r.stderr, r.stderr[-600:]
        assert not old.exists() and other.exists() and Path(spec["dir"]).exists()
    finally:
        live.close()


def test_a_look_stopped_by_the_budget_keeps_the_old_one(tmp_path):
    root = tmp_path / "r"
    (root / "keep").mkdir(parents=True)
    (root / "old").mkdir()
    (root / "old" / "framing.json").write_text("{}")
    assert D.drop_other_looks(root, "keep", log=lambda m: None) >= 0 and not (root / "old").exists()
    src = (ROOT / "lib" / "wall_ascii_disk.py").read_text()
    assert "if not stopped and finished >= queued and DROP_OLD_LOOKS:" in src


# ---- (b) the build yields to a speaking film; Setup's plate in pieces --------------------------------------------
def test_the_build_yields_while_a_film_speaks(monkeypatch):
    state = {"on_air": False, "film_sound": False}

    class H_(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            b = json.dumps(state).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H_)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    try:
        assert D._on_air(url) is False
        state["film_sound"] = True
        assert D._on_air(url) is True                                                # Lila's welcome speaking
        monkeypatch.setattr(D, "FILM_SOUND_YIELDS", False)
        assert D._on_air(url) is False                                               # the kill switch
    finally:
        srv.shutdown()
    import clip_audio
    assert clip_audio.playing() is False
    assert '"film_sound": clip_audio.playing(),' in COND_SRC


def test_setups_and_voice_ids_plates_go_up_in_pieces_where_the_pump_places_them():
    assert WALL.PIECE_KEYS_PLACED == ("init", "register")
    pump = WALL_SRC[WALL_SRC.index("    def pump():"):WALL_SRC.index("    floater = {")]
    assert "if key in pkt and LAYER_PIECES and key in PIECE_KEYS_PLACED:" in pump
    assert "_blit_pieces(key, item, got[0], origin=got[1])" in pump and "_hide_pieces(key)" in pump


# ---- (c) the disk tier counts what the pre-fill wrote -------------------------------------------------------------
def test_the_disk_tier_recounts_after_a_prefill_ends(tmp_path):
    t = D.DiskTier(root=tmp_path, env={"HOUSE_WALL_ASCII_DISK": "1", "HOUSE_WALL_ASCII_PREFILL": "0"})
    t._scanned.wait(10)
    look = tmp_path / "bbbbbbbbbbbbbbbbbbbb"
    (look / "listen").mkdir(parents=True)
    (look / "framing.json").write_text("{}")
    (look / "listen" / "00000-x.pic").write_bytes(b"x" * 5000)                    # written by another process
    assert t.used == 0
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    t.prefill_p = done
    t.fdir = look                                                                    # (pre-fill off: nothing starts)
    t._prefill((64, 36, 1.0, True, None, None, ("whole", 1.0)))
    t0 = time.time()
    while t.used < 5000 and time.time() - t0 < 10:
        time.sleep(0.05)
    assert t.used >= 5000 and t.recounted == done.pid


def test_no_player_no_build_so_a_test_cache_never_builds_this_tree(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: started.append(a) or None)
    t = D.DiskTier(root=tmp_path, env={"HOUSE_WALL_ASCII_DISK": "1"})                 # no feed
    t.fdir = tmp_path / "cccccccccccccccccccc"
    t._prefill((64, 36, 1.0, True, None, None, ("whole", 1.0)))
    assert started == []
    said = []
    assert D.prefill({"root": str(tmp_path), "dir": str(tmp_path / "x"), "W": 64, "H": 36, "density": 1.0},
                     log=said.append) == 0
    assert said and "no player named" in said[0] and not (tmp_path / "x").exists()
    for f in ("test_wall_ascii_disk_20261012.py", "test_wall_prefill_20261012.py"):
        assert 'monkeypatch.setenv("HOUSE_WALL_ASCII_PREFILL", "0")' in (ROOT / "tests" / f).read_text()
