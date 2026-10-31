"""Presence's User comes from the identity record, never a name in code (the User 2026-10-02, Init step 5)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import house_user, store_face, visit_lamp, visitors_log  # noqa: E402


def _store(tmp_path, people):
    for pid, role in people.items():
        (tmp_path / "people" / pid).mkdir(parents=True)
        (tmp_path / "people" / pid / "person.json").write_text(json.dumps({"role": role}))


def test_the_user_is_the_records(tmp_path, monkeypatch):
    for k in ("HOUSE_USER_ID", "RAIL_PRIMARY_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))           # visit_lamp also reads ~/.the agent/house-mode.json first
    assert house_user.user_id() == "user"                          # no record: the generic User
    _store(tmp_path, {"alex": "user", "sam": "regular"})
    assert house_user.user_id() == "alex"
    assert visit_lamp.user_id() == "alex" and visitors_log._user_id() == "alex"
    monkeypatch.setenv("HOUSE_USER_ID", "robin")
    assert house_user.user_id() == "robin" and visit_lamp.user_id() == "robin"


def test_store_face_falls_back_to_the_record(tmp_path, monkeypatch):
    for k in ("HOUSE_USER_ID", "RAIL_PRIMARY_USER", "RAIL_USER_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    monkeypatch.setattr(store_face, "PEOPLE", tmp_path / "people")
    _store(tmp_path, {"alex": "user"})
    assert store_face._user_id() == "alex"
