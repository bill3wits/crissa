"""Mic toast for name-interrupt; HTML file is immutable so the server injects JS."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "voiceonly-home-server.py"
    spec = importlib.util.spec_from_file_location("voiceonly_home_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_page_html_injects_interrupt_toast():
    mod = _load()
    html = mod.page_html().decode("utf-8")
    assert "Interrupted" in html
    assert "message cancelled" in html
    assert "__interruptToastWrap" in html
    assert "upsertMouth" in html
    assert "last_reply" in html
    assert "__camNoteToast" in html
    assert "secure_dropped" in html
    assert "SECURE OFF" in html
    assert "__whoAuthSot" in html
    assert "isAuthed=function()" in html


def test_injected_wizard_script_pulls_mouth_faster_than_the_clip():
    """Wall HTML is lsattr-i (FEED_PULL_MS=40 ~24 fps). Mouth JPEG is 48 Hz.
    The wizard script is the serve-time inject that can still change the
    puller. 16 ms ~62 Hz so the glass is not the cap.
    """
    js = (ROOT / "display" / "init-wizard.js").read_text(encoding="utf-8")
    assert "FEED_PULL_MS = 16" in js
    assert "feedSchedule(0)" in js
    html = (ROOT / "display" / "voiceonly-home.html").read_text(encoding="utf-8")
    assert "var FEED_PULL_MS = 40;" in html


def test_page_html_injects_time_seconds_once():
    """Seconds ride the toast injection; each block's </body> must fold to one."""
    mod = _load()
    html = mod.page_html().decode("utf-8")
    assert html.count("</body>") == 1
    assert "__voTimeSecs" in html
    assert "time-sec" in html
    assert "init-wizard.js" in html
    assert "cornerTime" not in html


def test_crissa_mouth_notice_while_fresh(tmp_path, monkeypatch):
    mod = _load()
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps(
            {
                "speaking": False,
                "last_reply": "",
                "notice": "interrupted",
                # Older than the 0.35s speaking tail, newer than the 5s notice TTL.
                "updated_ts": time.time() - 1.0,
            }
        )
    )
    monkeypatch.setattr(mod, "TTS_GATE", gate)
    z = mod.crissa_mouth()
    assert z.get("notice") == "interrupted"
    assert z["speaking"] is False


def test_crissa_mouth_carries_last_reply_while_speaking(tmp_path, monkeypatch):
    mod = _load()
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps(
            {
                "speaking": True,
                "last_reply": "That's the whole loop in one breath.",
                "who": "crissa",
                "updated_ts": time.time(),
            }
        )
    )
    monkeypatch.setattr(mod, "TTS_GATE", gate)
    z = mod.crissa_mouth()
    assert z["speaking"] is True
    assert z["last_reply"].startswith("That's the whole loop")
    assert z["who"] == "crissa"


def test_crissa_mouth_notice_expires(tmp_path, monkeypatch):
    mod = _load()
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps(
            {
                "speaking": False,
                "notice": "interrupted",
                "updated_ts": 1.0,
            }
        )
    )
    monkeypatch.setattr(mod, "TTS_GATE", gate)
    z = mod.crissa_mouth()
    assert "notice" not in z
