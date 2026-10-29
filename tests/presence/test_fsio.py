import json

from crissa_presence.fsio import append_jsonl_capped, atomic_write_text


def test_atomic_write_replaces_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_text(p, json.dumps({"a": 1}))
    atomic_write_text(p, json.dumps({"a": 2}))
    assert json.loads(p.read_text()) == {"a": 2}
    assert list(tmp_path.glob("*.tmp")) == []


def test_append_jsonl_caps_to_newest(tmp_path):
    p = tmp_path / "hist.jsonl"
    for i in range(50):
        append_jsonl_capped(p, json.dumps({"i": i}), max_bytes=200, keep_lines=5)
    lines = p.read_text().splitlines()
    # bounded: rotation fired (never all 50) and size stays near the cap
    assert len(lines) < 50
    assert p.stat().st_size <= 200 + 20  # max_bytes + one appended line
    assert json.loads(lines[-1]) == {"i": 49}


def test_publish_report_roundtrip(tmp_path):
    from crissa_presence import cosmos_operator as co

    report = {"schema": "cosmos_room_speaker/v1", "note": "fsio smoke"}
    out = co.publish_report(report, path=tmp_path / "report.json")
    assert json.loads(out.read_text())["note"] == "fsio smoke"
    hist = (tmp_path / "report.jsonl").read_text().splitlines()
    assert json.loads(hist[-1])["note"] == "fsio smoke"
