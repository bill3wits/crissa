"""Every profile the wake grammar knows reaches the ONE Connect endpoint; who was addressed rides in the overlay
(the house gave the orchestrator its own gateway and summoned the crew through Crissa's; a pack has one agent,
2026-10-09)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "ear"))

from crissa_bridge import connect
from crissa_bridge.ear_bridge import _overlay_for, _target_from_meta


@pytest.fixture(autouse=True)
def agent(tmp_path, monkeypatch):
    (tmp_path / "agent.json").write_text(json.dumps({"url": "http://127.0.0.1:9999/v1", "model": "agent-x",
                                                     "session_header": "X-Session"}))
    monkeypatch.setattr(connect.A, "path", lambda home=None: tmp_path / "agent.json")
    for k in ("CRISSA_AGENT_URL", "CRISSA_AGENT_KEY", "CRISSA_AGENT_MODEL", "CRISSA_AGENT_PRESET"):
        monkeypatch.delenv(k, raising=False)


CONNECT = "http://127.0.0.1:9999/v1/chat/completions"


def test_lila_summon_hits_crissa_http():
    gw, addressed = _target_from_meta({"profile": "lila", "route": "summon", "addressed": "lila"})
    assert gw["profile"] == "crissa"
    assert gw["url"] == CONNECT
    assert addressed == "lila"
    ov = _overlay_for(gw["profile"], addressed)
    assert "not Lila" in ov
    assert "card on the board for lila" in ov


def test_ada_direct_is_the_same_endpoint():
    gw, addressed = _target_from_meta({"profile": "ada", "route": "direct"})
    assert gw["profile"] == "ada"
    assert gw["url"] == CONNECT and gw["model"] == "agent-x"
    assert gw["session_header"] == "X-Session"
    assert addressed is None
    assert "board owner" in _overlay_for("ada", None)


def test_argus_direct_is_crissa():
    gw, addressed = _target_from_meta({"profile": "argus", "route": "direct"})
    assert gw["profile"] == "crissa"
    assert gw["url"] == CONNECT
    ov = _overlay_for("argus", None)
    assert "Crissa" in ov
    assert "Argus is gone" in ov


def test_crissa_default():
    gw, addressed = _target_from_meta({})
    assert gw["profile"] == "crissa"
    assert addressed is None
    assert _overlay_for("crissa", None) == ""


def test_nothing_connected_is_an_empty_url(tmp_path, monkeypatch):
    monkeypatch.setattr(connect.A, "path", lambda home=None: tmp_path / "none.json")
    gw, _ = _target_from_meta({})
    assert gw["url"] == "" and not connect.connected()
