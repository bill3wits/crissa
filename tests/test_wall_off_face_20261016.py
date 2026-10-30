"""The Wall off: her face on top (the User 2026-10-16: "persisted above all windows ... That is how I believe the ASCII
wall off mode will perform without the compute under it"), and the face tile's bar ("have the buttons show on tile on
hover for that toggle and size etc", "fade and collapse even with restore on hover mouse move as in wallpaper mode")."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import crissa_face as F  # noqa: E402
import crissa_home  # noqa: E402
import crissa_window as Z  # noqa: E402


# ---- the switch --------------------------------------------------------------------------------------------------------
def test_wall_off_face_follows_face_only_and_has_its_own_kill(monkeypatch):
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert F.wall_off_on({}) is True
    assert F.wall_off_on({"CRISSA_WALL_OFF_FACE": "0"}) is False
    assert F.wall_off_on({"CRISSA_FACE_ONLY": "0"}) is False
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert F.wall_off_on({}) is False, "the house keeps its Wall"


def test_wall_off_opens_the_face_on_top_and_wall_on_closes_it(monkeypatch):
    seen = {}
    monkeypatch.setattr(F, "wall_off_on", lambda env=None: True)
    monkeypatch.setattr(F, "open_window", lambda u, home=None, above=False: seen.update(open=(u, above)) or True)
    assert F.wall_off("http://127.0.0.1:8180", None) == "Her face stays on top in a window of its own."
    assert seen["open"] == ("http://127.0.0.1:8180", True)
    monkeypatch.setattr(F, "wall_off_on", lambda env=None: False)
    assert F.wall_off("http://127.0.0.1:8180", None) == ""


# ---- the bar's sizes ----------------------------------------------------------------------------------------------------
def test_size_steps_keep_the_shape_and_the_centre():
    b = {"x": 1000, "y": 200, "w": 334, "h": 594}
    up = F.resized(b, "face", 1, (1920, 1080))
    assert abs(up["w"] / up["h"] - 9 / 16) < 0.01 and up["h"] == round(594 * 1.12)
    assert abs((up["x"] + up["w"] / 2) - (1000 + 167)) <= 1 and abs((up["y"] + up["h"] / 2) - (200 + 297)) <= 1
    down = F.resized(b, "face", -1, (1920, 1080))
    assert down["h"] < 594


def test_changing_shape_keeps_the_long_side():
    b = {"x": 1000, "y": 200, "w": 334, "h": 594}
    room = F.resized(b, "room", 0, (1920, 1080))
    assert room["w"] == 594 and abs(room["w"] / room["h"] - 16 / 9) < 0.01
    back = F.resized(room, "face", 0, (1920, 1080))
    assert back["h"] == 594


def test_the_window_stays_on_the_screen():
    big = F.resized({"x": 1800, "y": 900, "w": 600, "h": 1066}, "face", 3, (1920, 1080))
    assert big["h"] <= 1080 * 0.95 + 1 and big["x"] + big["w"] <= 1920 and big["y"] + big["h"] <= 1080
    wide = F.resized({"x": 0, "y": 0, "w": 1900, "h": 1068}, "room", 3, (1920, 1080))
    assert wide["w"] <= 1920 and wide["h"] <= 1080


def test_the_shape_is_kept_with_the_place(tmp_path):
    assert F.shape(tmp_path) == "face"
    assert F.save({"x": 1, "y": 2, "w": 594, "h": 334}, tmp_path, "room")
    assert F.shape(tmp_path) == "room"
    assert F.save({"x": 5, "y": 2, "w": 594, "h": 334}, tmp_path)          # the page's own save keeps the shape
    assert F.shape(tmp_path) == "room" and F.bounds(tmp_path)["x"] == 5


# ---- her window kept above --------------------------------------------------------------------------------------------
def test_show_face_keeps_it_above_only_when_asked(monkeypatch):
    monkeypatch.setattr(Z, "enabled", lambda e=None: True)
    monkeypatch.setattr(Z, "_in_test", lambda e=None: False)
    monkeypatch.setattr(Z, "up", lambda p=None, timeout=0.6: True)
    monkeypatch.setattr(Z, "pages", lambda p=None, timeout=1.0: [{"id": "T", "url": "http://127.0.0.1:8180/face"}])
    monkeypatch.setattr(Z, "pick", lambda ps, url: ps[0])
    monkeypatch.setattr(Z, "_bring", lambda p, page, url, front, after_launch=False: {"ok": True, "how": ["front"]})
    asked = []
    monkeypatch.setattr(Z, "_keep_above", lambda e=None, **k: asked.append(1) or "above 0x2a")
    assert "above" not in Z.show_face("http://127.0.0.1:8180/face", None)
    assert Z.show_face("http://127.0.0.1:8180/face", None, above=True)["above"] == "above 0x2a" and asked == [1]


def test_place_and_close_never_act_in_a_test():
    assert Z.place_face("http://127.0.0.1:1/face", {"x": 0, "y": 0, "w": 300, "h": 300}) is False
    assert Z.close_face("http://127.0.0.1:1/face") is False


# ---- the wiring -----------------------------------------------------------------------------------------------------------
def test_menu_close_and_open_the_wall_carry_her_face(monkeypatch, tmp_path):
    import crissa_menu as M
    import crissa_procs
    calls = []
    monkeypatch.setattr(crissa_procs, "request", lambda home, lane, verb: {"ok": True})
    monkeypatch.setattr(F, "wall_off", lambda u, home=None: calls.append(("off", u)) or "Her face stays on top.")
    monkeypatch.setattr(F, "wall_on", lambda u: calls.append(("on", u)) or True)
    out = M.wall_close(tmp_path)
    assert out["face_on_top"] and out["note"].endswith("Her face stays on top.")
    M.wall_open(tmp_path)
    assert [c[0] for c in calls] == ["off", "on"]


def test_the_launcher_and_the_player_are_wired():
    z = (ROOT / "scripts" / "crissa.py").read_text()
    assert 'face = crissa_face.wall_off(p["player"], crissa_home.HOME)' in z
    c = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'if isinstance(body.get("resize"), dict):' in c and "crissa_face.resize_window(" in c
    assert "shape=crissa_face.shape()" in c


def test_the_tile_bar_fades_and_folds_and_comes_back_on_a_move():
    page = (ROOT / "display" / "crissa-face.html").read_text()
    assert '<div id="bar" class="idle">' in page
    assert "#bar.idle { opacity: 0; max-height: 0;" in page and "pointer-events: none" in page
    assert "const IDLE_MS = 1000;" in page
    assert '["mousemove", "mouseenter", "pointerdown", "keydown"].forEach((ev) => window.addEventListener(ev, wake));' in page
    for b in ('id="b-wall"', 'id="b-shape"', 'id="b-minus"', 'id="b-plus"'):
        assert b in page
    assert "resize: {b: bounds(), shape: newShape, step: step}" in page
    assert 'body: JSON.stringify({action: wall.action, by: "her face tile"})' in page
