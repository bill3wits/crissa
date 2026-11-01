"""Free-air SoT auth feed — pure helpers on shipped ear_bridge functions."""
from __future__ import annotations

from crissa_bridge.ear_bridge import (
    authorize,
    presence_auth_ids,
    presence_facts_line,
    _roster_phrase,
)


def test_presence_auth_ids_empty():
    a = presence_auth_ids({"auth": {"authed_ids": [], "soft_ids": []}})
    assert a["authed_ids"] == []
    assert a["soft_ids"] == []
    assert a["anyone_authed"] is False


def test_presence_auth_ids_named():
    a = presence_auth_ids(
        {
            "auth": {
                "anyone_authed": True,
                "authed_ids": ["User"],
                "soft_ids": ["Second"],
            }
        }
    )
    assert a["authed_ids"] == ["user"]
    assert a["soft_ids"] == ["second"]
    assert a["anyone_authed"] is True


def test_authorize_prefers_sot_authed_ids(monkeypatch):
    # Legacy house-mode path (not user_auth_only)
    import crissa_bridge.ear_bridge as eb

    monkeypatch.setattr(eb, "is_user_auth_only", lambda: False)
    presence = {
        "headcount": 2,
        "auth": {"authed_ids": ["user"], "soft_ids": []},
        "people": [
            {"name": "U1", "certain": False, "id_status": "unknown"},
            {"name": "U2", "certain": False, "id_status": "unknown"},
        ],
    }
    assert authorize({"person_id": "user"}, presence) == "allow"


def test_authorize_soft_ids_from_sot(monkeypatch):
    import crissa_bridge.ear_bridge as eb

    monkeypatch.setattr(eb, "is_user_auth_only", lambda: False)
    presence = {
        "headcount": 1,
        "auth": {"authed_ids": [], "soft_ids": ["second"]},
        "people": [{"name": "U1", "certain": False}],
    }
    assert authorize({"person_id": "second"}, presence) == "soft"


def test_authorize_certain_roster_name(monkeypatch):
    import crissa_bridge.ear_bridge as eb

    monkeypatch.setattr(eb, "is_user_auth_only", lambda: False)
    presence = {
        "headcount": 1,
        "auth": {"authed_ids": [], "soft_ids": []},
        "people": [{"name": "user", "certain": True}],
    }
    assert authorize({"person_id": "user"}, presence) == "allow"


def test_authorize_u_slot_soft(monkeypatch):
    import crissa_bridge.ear_bridge as eb

    monkeypatch.setattr(eb, "is_user_auth_only", lambda: False)
    presence = {"headcount": 1, "auth": {}, "people": [{"name": "U1"}]}
    assert authorize({"person_id": "u0"}, presence) == "soft"


def test_roster_u_only_no_invented_names():
    presence = {
        "people": [
            {"name": "U1", "certain": False, "id_status": "unknown"},
            {"name": "U2", "certain": False, "id_status": "unknown"},
        ]
    }
    phrase = _roster_phrase(presence)
    assert "user" not in phrase.lower()
    assert "second" not in phrase.lower()
    assert "U1" in phrase and "U2" in phrase


def test_facts_line_includes_auth_ids_when_present():
    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "frame_age_sec": 0.5,
        "auth": {
            "anyone_authed": True,
            "authed_ids": ["user"],
            "soft_ids": ["second"],
        },
        "people": [
            {"name": "user", "certain": True},
            {"name": "second", "certain": False, "id_status": "soft"},
        ],
    }
    line = presence_facts_line(presence, person_id="user", level="allow")
    assert "authed_ids=['user']" in line or "authed_ids=[\"the user\"]" in line or "user" in line
    assert "soft_ids=" in line
    assert "second" in line
    assert "roster=[" in line
    assert "user" in line.lower()
    assert "live the face check User face" in line
    assert "auth_sticky=" in line


def test_facts_line_u_only_does_not_invent_named_ids():
    presence = {
        "headcount": 2,
        "count_trust": "fact",
        "frame_age_sec": 1.0,
        "auth": {"anyone_authed": False, "authed_ids": [], "soft_ids": []},
        "people": [
            {"name": "U1", "certain": False, "id_status": "unknown"},
            {"name": "U2", "certain": False, "id_status": "unknown"},
        ],
    }
    line = presence_facts_line(presence, person_id=None, level="soft")
    assert "authed_ids=[]" in line
    assert "soft_ids=[]" in line
    assert "person_id=user" not in line.lower() and "[user" not in line.lower()
    assert "second" not in line.lower()
    assert "unknown slot U1" in line or "U1" in line
