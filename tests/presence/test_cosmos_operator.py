"""Cosmos operator mode: flags, tools, report shape (no GPU required)."""

from __future__ import annotations

import json
from pathlib import Path

import crissa_presence.cosmos_operator as co


def test_operator_flags_default_off(monkeypatch):
    monkeypatch.delenv("RAIL_COSMOS_OPERATOR", raising=False)
    monkeypatch.delenv("RAIL_ORACLE_OPERATOR", raising=False)
    assert co.operator_mode_enabled() is False
    f = co.operator_flags()
    assert f["operator_mode"] is False
    assert f["auto_camera_probes"] is True


def test_operator_flags_on(monkeypatch):
    monkeypatch.setenv("RAIL_COSMOS_OPERATOR", "1")
    assert co.operator_mode_enabled() is True
    f = co.operator_flags()
    assert f["operator_mode"] is True
    assert f["auto_camera_probes"] is False
    assert f["refind_disabled"] is True
    assert f["auto_look_around_disabled"] is True


def test_build_room_speaker_report_shape():
    rep = co.build_room_speaker_report(
        in_room=[{"id": "user", "location": "desk"}, "wanda"],
        speaking={"id": "user", "text": "hi", "source": "voice"},
        note="test",
    )
    assert rep["schema"] == "cosmos_room_speaker/v1"
    assert "in_room" in rep and "speaking" in rep
    assert rep["in_room"]["headcount"] == 2
    assert rep["in_room"]["people"][0]["id"] == "user"
    assert rep["speaking"]["id"] == "user"
    assert rep["speaking"]["text"] == "hi"


def test_publish_and_load_report(tmp_path, monkeypatch):
    path = tmp_path / "room_speaker_report.json"
    monkeypatch.setenv("RAIL_COSMOS_REPORT", str(path))
    rep = co.build_room_speaker_report(in_room=["user"], speaking=None)
    out = co.publish_report(rep, path=path)
    assert out.is_file()
    loaded = co.load_report(path)
    assert loaded is not None
    assert loaded["in_room"]["people"][0]["id"] == "user"
    assert "speaking" in loaded


def test_parse_tool_calls():
    text = 'I will snapshot now.\nTOOL: {"name":"snapshot","args":{}}\nDone.'
    calls = co.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "snapshot"


def test_parse_tool_calls_nested_args():
    text = (
        'YES I CAN DO THE JOB\n'
        'Tool: {"name":"publish_report","args":{"in_room":[],"speaking":null,'
        '"note":"No one is visible in the current view."}}\n'
    )
    calls = co.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "publish_report"
    assert calls[0]["args"]["in_room"] == []


def test_affirms_can_do_job():
    yes = (
        "1) Video: yes, I have the door camera via tools.\n"
        "2) Audio: no reliable mic yet.\n"
        "3) YES I CAN DO THE JOB of reporting who is in the room and who is speaking "
        "using vision tools and publish_report."
    )
    assert co.affirms_can_do_job(yes) is True
    assert co.affirms_can_do_job("I cannot do anything") is False


def test_capability_brief_mentions_job_and_tools():
    b = co.capability_job_brief()
    assert "who is in the room" in b.lower() or "who-is-in-room" in b.lower()
    assert "speaking" in b.lower()
    assert "snapshot" in b
    assert "return_door" in b or "450000" in b
    assert "door" in b.lower() and ("entrance" in b.lower() or "450000" in b)
    assert "log_error" in b or "suspected_reason" in b
    assert "no root" in b.lower() or "do not need root" in b.lower()


def test_tool_catalog_lists_camera_tools():
    names = {t["name"] for t in co.TOOL_SPECS}
    for n in (
        "snapshot",
        "aim",
        "return_door",
        "facefinder",
        "look_around",
        "listen_stt",
        "publish_report",
        "camera_health",
        "list_kit",
        "read_kit_doc",
        "list_stops",
        "log_error",
        "rails_inventory",
        "sync_report_from_rails",
    ):
        assert n in names


def test_publish_merges_rails_when_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("RAIL_COSMOS_KIT", str(tmp_path))
    monkeypatch.setenv("RAIL_COSMOS_REPORT", str(tmp_path / "room_speaker_report.json"))

    def fake_inv():
        return {
            "ok": True,
            "headcount": 2,
            "count_trust": "stale",
            "people": [
                {"id": "user", "certain": True, "location": "in_room"},
                {"id": "wanda", "certain": True, "location": "in_room"},
            ],
        }

    monkeypatch.setattr(co, "fetch_rails_inventory", fake_inv)
    out = co.execute_tool(
        "publish_report",
        {"in_room": [], "speaking": None, "note": "door empty"},
    )
    assert out.get("ok") is True
    assert out.get("merged_rails") is True
    people = out["report"]["in_room"]["people"]
    assert {p["id"] for p in people} == {"user", "wanda"}


def test_kit_dir_docs_exist():
    kit = co.default_kit_dir()
    # created by install / implementer
    assert (kit / "CONTROL-SURFACE.md").is_file() or kit.exists()
    # if kit present, control surface must mention door home
    cs = kit / "CONTROL-SURFACE.md"
    if cs.is_file():
        text = cs.read_text()
        assert "450000" in text
        assert "facefinder" in text.lower() or "facelock" in text.lower()
    skill = kit / "SKILL.md"
    if skill.is_file():
        st = skill.read_text()
        assert "door" in st.lower()
        assert "listen_stt" in st or "STT" in st
        assert "suspected_reason" in st or "log_error" in st


def test_log_error_and_read_kit(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_COSMOS_KIT", str(tmp_path))
    monkeypatch.setenv("RAIL_COSMOS_ERROR_LOG", str(tmp_path / "errors.jsonl"))
    (tmp_path / "SKILL.md").write_text("# skill\ndoor watch\n")
    out = co.execute_tool(
        "log_error",
        {
            "error": "test fail",
            "suspected_reason": "unit test",
            "tool": "snapshot",
        },
    )
    assert out.get("ok") is True
    logp = tmp_path / "errors.jsonl"
    assert logp.is_file()
    row = json.loads(logp.read_text().strip().splitlines()[-1])
    assert row["error"] == "test fail"
    assert row["suspected_reason"] == "unit test"

    listed = co.execute_tool("list_kit", {})
    assert listed.get("ok") is True
    rd = co.execute_tool("read_kit_doc", {"path": "SKILL.md"})
    assert rd.get("ok") is True
    assert "door" in (rd.get("text") or "").lower()


def test_strip_heavy_fields_drops_b64():
    raw = {"frames": [{"id": "desk", "jpeg_b64": "x" * 5000, "path": "/tmp/a.jpg"}]}
    clean = co._strip_heavy_fields(raw)
    assert "stripped" in str(clean["frames"][0]["jpeg_b64"]).lower() or clean["frames"][0][
        "jpeg_b64"
    ].startswith("<stripped")
