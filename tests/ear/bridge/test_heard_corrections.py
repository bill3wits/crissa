import json
from crissa_bridge import heard_corrections as hc


def test_render_cue_becomes_render_queue(tmp_path, monkeypatch):
    f = tmp_path / "c.json"; f.write_text(json.dumps({"phrases": {"render cue": "render queue"}}))
    monkeypatch.setenv("EAR_BRIDGE_CORRECTIONS_FILE", str(f))
    assert hc.apply("Put the render cue on the wall.") == "Put the render queue on the wall."
    assert hc.apply("Put the Render Cue on the wall.") == "Put the render queue on the wall."
    assert hc.apply("Play the wake cue.") == "Play the wake cue."           # a real cue stays
    assert hc.apply("render cues") == "render cues"                        # whole words only


def test_kill_switch_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EAR_BRIDGE_CORRECTIONS_FILE", str(tmp_path / "none.json"))
    assert hc.apply("render cue") == "render cue"
    f = tmp_path / "c.json"; f.write_text(json.dumps({"phrases": {"render cue": "render queue"}}))
    monkeypatch.setenv("EAR_BRIDGE_CORRECTIONS_FILE", str(f))
    monkeypatch.setenv("EAR_BRIDGE_CORRECTIONS", "0")
    assert hc.apply("render cue") == "render cue"


def test_the_shipped_table_has_the_measured_row():
    rows = json.loads(hc.DEFAULT.read_text())["phrases"]
    assert rows.get("render cue") == "render queue" and rows.get("render cube") == "render queue" and "cue" not in rows
