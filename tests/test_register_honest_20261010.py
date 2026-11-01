"""Register on real hardware (the User 2026-10-10, the .101 test).

- "The camera is busy" only when the camera IS busy: each refusal presence gives has its own true line, in both copies
  of the coach (modules/id and modules/presence). On .101 every refusal said busy and the token was switched off.
- presence's Register token is ON in a pack unless set, as the coach's own switch already was (the two disagreed, and
  the service units carried neither switch).
- the camera's door-picture check is off, quietly, where its house pictures are not (camera.log: four imread failures
  of the house's own body_audit_refs on every home).
- the tool hold is absent in a pack, and quiet about it (id-session.log: "tool-hold script missing" on every Register).
Both coach copies are imported under their own package names, as tests/test_crissa_coach_copies.py does."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_crissa_coach_copies import COPIES, copy  # noqa: E402,F401  (copy is the both-copies fixture)


def test_each_refusal_says_what_really_happened(copy):
    rt = copy.rc.refusal_text
    assert rt({"ok": False, "error": "camera_leased", "owner": "user_joystick"}) == \
        "The camera is busy: you are steering it. Press Retry in a moment."
    assert rt({"ok": False, "error": "camera_leased", "owner": "someone-new"}) == copy.rc.BUSY_TEXT
    for err in ("off", "no_ttl", "camera:URLError", "not_user_mode"):
        assert "busy" not in rt({"ok": False, "error": err}), err
    assert "switched off" in rt({"ok": False, "error": "off", "status": 404})
    assert rt({"ok": False, "error": "not_user_mode"}) == copy.rc.REFUSE_TEXT
    assert "did not answer" in rt({"ok": False, "error": "Connection refused"})


def test_both_copies_say_it_the_same_way_and_never_the_old_line():
    texts = {}
    for name, (mod_dir, top, pkg) in COPIES.items():
        src = (mod_dir / top / pkg.split(".")[1] / "voice_register.py").read_text(encoding="utf-8")
        assert "The camera is busy - press Register again" not in src, name
        assert src.count("register_camera.refusal_text(") == 3, name
        texts[name] = (mod_dir / top / pkg.split(".")[1] / "register_camera.py").read_text(encoding="utf-8")
    assert texts["id"] == texts["presence"]


def test_presences_register_token_is_on_in_a_pack_unless_set(monkeypatch):
    sys.path.insert(0, str(ROOT / "modules" / "presence"))
    try:
        from crissa_presence import camera_token as ct
        from crissa_presence import paths as pp
        monkeypatch.delenv("RAIL_REGISTER_TOKEN", raising=False)
        monkeypatch.setattr(pp, "is_house", lambda: False)
        assert ct.register_on() is True and "register" in ct.ladder()
        monkeypatch.setattr(pp, "is_house", lambda: True)
        assert ct.register_on() is False                       # the house keeps its own rule
        monkeypatch.setattr(pp, "is_house", lambda: False)
        monkeypatch.setenv("RAIL_REGISTER_TOKEN", "0")
        assert ct.register_on() is False
    finally:
        sys.path.remove(str(ROOT / "modules" / "presence"))


def test_the_door_picture_check_is_off_where_its_pictures_are_not(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "modules" / "presence"))
    try:
        from crissa_presence import door_picture as dp
        monkeypatch.delenv("RAIL_DOOR_PICTURE_VERIFY", raising=False)
        monkeypatch.setattr(dp, "REFS", tmp_path / "body_audit_refs")
        assert dp.enabled() is False
        import cv2
        import numpy as np
        calls = []
        real = cv2.imread
        monkeypatch.setattr(cv2, "imread", lambda *a, **k: calls.append(a) or real(*a, **k))
        assert dp._gray(cv2, np, tmp_path / "body_audit_refs" / dp.DOOR_REF) is None and calls == []
        (tmp_path / "body_audit_refs").mkdir()
        cv2.imwrite(str(tmp_path / "body_audit_refs" / dp.DOOR_REF), np.zeros((40, 60, 3), dtype=np.uint8))
        assert dp.enabled() is True
    finally:
        sys.path.remove(str(ROOT / "modules" / "presence"))


@pytest.mark.parametrize("name", sorted(COPIES))
def test_a_pack_says_nothing_about_a_tool_hold_it_does_not_have(name, monkeypatch, tmp_path, capsys):
    mod_dir, top, pkg = COPIES[name]
    saved = {k: v for k, v in sys.modules.items() if k == top or k.startswith(top + ".")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(mod_dir))
    try:
        import importlib
        vs = importlib.import_module(pkg + ".voice_session")
        paths = importlib.import_module(top + ".paths")
        monkeypatch.setattr(vs, "_tool_hold_script", lambda: tmp_path / "scripts" / "house-tool-hold.py")
        monkeypatch.setattr(paths, "is_house", lambda: False)
        vs._tool_hold_acquire(reason="voice-register", owner="voice_register", sec=600.0)
        assert "tool-hold" not in capsys.readouterr().out
        monkeypatch.setattr(paths, "is_house", lambda: True)
        vs._tool_hold_acquire(reason="voice-register", owner="voice_register", sec=600.0)
        assert "tool-hold script missing" in capsys.readouterr().out      # on the house it is a fault, said
    finally:
        sys.path.remove(str(mod_dir))
        for k in [k for k in sys.modules if k == top or k.startswith(top + ".")]:
            del sys.modules[k]
        sys.modules.update(saved)
