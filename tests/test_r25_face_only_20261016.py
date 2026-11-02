"""Face only (the User 2026-10-15/16): her face in a 9:16 window of its own, kept where the person leaves it."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_face as F  # noqa: E402
import crissa_window as Z  # noqa: E402


def test_point_is_shares_of_the_picture():
    p = F.point({"nose": [368.0, 200.0], "s": 120.0}, (736, 400))
    assert p == {"ok": True, "cx": 0.5, "cy": 0.5, "s": 0.3, "w": 736, "h": 400}
    assert F.point(None, (736, 400))["ok"] is False


def test_bounds_kept_and_refused_when_tiny(tmp_path):
    assert F.bounds(tmp_path) is None
    assert F.save({"x": 10, "y": 20, "w": 300, "h": 533}, tmp_path)
    assert F.bounds(tmp_path) == {"x": 10, "y": 20, "w": 300, "h": 533}
    assert not F.save({"x": 0, "y": 0, "w": 50, "h": 50}, tmp_path)
    assert F.bounds(tmp_path)["w"] == 300


def test_first_window_is_nine_by_sixteen_at_the_right():
    b = F.first_bounds((1920, 1080))
    assert b["h"] == 594 and b["w"] == 334 and b["x"] == 1920 - 334 - 32


def test_on_off(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: True)
    assert F.on({}) is False and F.on({"CRISSA_FACE_ONLY": "1"}) is True
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: False)
    assert F.on({}) is True and F.on({"CRISSA_FACE_ONLY": "0"}) is False


def test_show_face_opens_a_new_window_at_the_kept_place(monkeypatch):
    calls = []
    monkeypatch.setattr(Z, "enabled", lambda e=None: True)
    monkeypatch.setattr(Z, "_in_test", lambda e=None: False)
    monkeypatch.setattr(Z, "up", lambda p=None, timeout=0.6: True)
    monkeypatch.setattr(Z, "pages", lambda p=None, timeout=1.0: [])
    monkeypatch.setattr(Z, "_browser_ws", lambda p: "ws://x")
    monkeypatch.setattr(Z, "cdp", lambda ws, c, timeout=4.0: (calls.append(c), [{"targetId": "T"} for _ in c])[1])
    monkeypatch.setattr(Z, "_wait_page", lambda p, u, w, sleep=None: {"id": "T", "url": u})
    monkeypatch.setattr(Z, "_window_state", lambda p, t: (7, "normal"))
    monkeypatch.setattr(Z, "_bring", lambda p, page, url, front, after_launch=False: {"ok": True, "how": ["front"]})
    monkeypatch.setattr(Z, "_open_app", lambda url, e, p_, **kw: False)     # round 26: no app window here: round 25's
    monkeypatch.setattr(Z, "_place", lambda p, page, b, **kw: calls.append(("place", b)) or True)
    monkeypatch.setattr(Z, "_frameless", lambda e=None, **kw: "test")
    out = Z.show_face("http://127.0.0.1:8100/face", {"x": 5, "y": 6, "w": 300, "h": 533})
    assert out["ok"] and out["opened"] and out["placed"]
    assert calls[0] == [("Target.createTarget", {"url": "http://127.0.0.1:8100/face", "newWindow": True})]
    assert calls[1] == ("place", {"x": 5, "y": 6, "w": 300, "h": 533})


def test_an_open_face_window_only_comes_forward(monkeypatch):
    monkeypatch.setattr(Z, "enabled", lambda e=None: True)
    monkeypatch.setattr(Z, "_in_test", lambda e=None: False)
    monkeypatch.setattr(Z, "up", lambda p=None, timeout=0.6: True)
    monkeypatch.setattr(Z, "pages", lambda p=None, timeout=1.0: [{"id": "T", "url": "http://127.0.0.1:8100/face",
                                                                   "type": "page"}])
    monkeypatch.setattr(Z, "pick", lambda ps, url: ps[0])
    monkeypatch.setattr(Z, "_bring", lambda p, page, url, front, after_launch=False: {"ok": True, "how": ["front"]})
    out = Z.show_face("http://127.0.0.1:8100/face", {"x": 5, "y": 6, "w": 300, "h": 533})
    assert out["ok"] and out["opened"] is False


def test_menu_offers_face_only_in_a_pack(monkeypatch):
    import crissa_menu as M
    import crissa_wall_key as K
    monkeypatch.setattr(F, "on", lambda env=None: True)
    assert M.face_view() == {"label": "Face only", "action": "face"} and "face" in M.ACTIONS
    items = K.menu_items({"face": M.face_view()}, False)
    assert ("Face only", "face", True) in items
    monkeypatch.setattr(F, "on", lambda env=None: False)
    assert M.face_view() == {}
    assert not any(v == "face" for _l, v, _e in K.menu_items({"face": M.face_view()}, False))


def test_routes_and_pages_are_wired():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert 'if head in ("/face", "/v1/face-point") and crissa_face.on():' in src
    assert src.count('if head == "/v1/face-window" and crissa_face.on():') == 1
    html = (ROOT / "display" / "crissa-wall.html").read_text()
    assert 'menuPost({action: "face"})' in html
    page = (ROOT / "display" / "crissa-face.html").read_text()
    assert 'src="/frame.mjpg"' in page and "/v1/face-point" in page and "/v1/face-window" in page
    man = json.loads((ROOT / "config" / "crissa-packages.json").read_text())["packages"]["crissa-avatar"]
    assert "crissa_face.py" in man["lib"] and "display/crissa-face.html" in man["files"]
