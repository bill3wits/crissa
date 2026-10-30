"""Render studio mic must not mute Crissa outside Render."""
from pathlib import Path

from crissa_bridge.ear_bridge import (
    occupancy_is_render,
    occupancy_pending_render,
    render_skips_crissa,
    studio_holds_ear,
    _reset_speech_logs,
)


def test_studio_hold_needs_flag_and_render(tmp_path, monkeypatch):
    flag = tmp_path / "render-studio-listen"
    mode = tmp_path / "house-gpu-mode"
    monkeypatch.setattr("crissa_bridge.ear_bridge.STUDIO_LISTEN", flag)
    monkeypatch.setattr("crissa_bridge.ear_bridge.GPU_MODE_FILE", mode)
    assert studio_holds_ear() is False
    flag.write_text("render-studio\n")
    mode.write_text("heavy\n")
    assert studio_holds_ear() is False
    mode.write_text("render\n")
    assert studio_holds_ear() is True
    flag.unlink()
    assert studio_holds_ear() is False
    # Render with mic off still skips Crissa :8652 (gateway is parked).
    mode.write_text("render\n")
    assert occupancy_is_render() is True
    assert render_skips_crissa() is True
    assert studio_holds_ear() is False
    mode.write_text("normal\n")
    assert occupancy_is_render() is False
    assert render_skips_crissa() is False


def test_render_limp_line_does_not_wait_on_cosy_or_studio():
    """FaceTime still gets the CPU one-shot under studio_hold.

    2026-08-31 08:02: studio_hold continue ran before _cosy_up, so
    'Hey Zoe.' never reached a mouth. Lila still owns the 27B turn.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text(encoding="utf-8")
    block = src.split("if render_skips_crissa() and not (render_lila_host() and to_other_mouth(wm)):", 1)[1].split(
        "if wm.get(\"hit\"):", 1
    )[0]
    assert "not studio_holds_ear() and voice_on() and _cosy_up()" not in block
    # the User 2026-09-14: no scripted announcement of Render mode
    assert "speak_blocking(" not in block and "asleep while" not in block


def test_pending_render_skips_crissa_read_only(tmp_path, monkeypatch):
    import os
    import time

    mode = tmp_path / "house-gpu-mode"
    pending = tmp_path / "house-gpu-pending"
    mode.write_text("heavy\n")
    pending.write_text(f"render\n{os.getpid()}\n")
    monkeypatch.setattr("crissa_bridge.ear_bridge.GPU_MODE_FILE", mode)
    monkeypatch.setattr("crissa_bridge.ear_bridge.GPU_PENDING_FILE", pending)
    assert occupancy_is_render() is False
    assert render_skips_crissa() is True
    assert pending.is_file()
    pending.write_text("render\n99999999\n")
    assert render_skips_crissa() is False
    assert pending.is_file()
    pending.write_text(f"render\n{os.getpid()}\n")
    os.utime(pending, (time.time() - 21 * 60, time.time() - 21 * 60))
    assert render_skips_crissa() is False
    assert pending.is_file()


def test_abort_listener_loopback(monkeypatch):
    import json
    import urllib.request

    from crissa_bridge.ear_bridge import start_abort_listener

    monkeypatch.setattr("crissa_bridge.ear_bridge.abort_mouth", lambda reason="": None)
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert start_abort_listener(port) is True
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
        d = json.loads(r.read().decode())
    assert d["ok"] is True
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/abort",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        d = json.loads(r.read().decode())
    assert d["ok"] is True


def test_reset_speech_logs_truncates_and_clears_gate(tmp_path, monkeypatch):
    log = tmp_path / "ear-bridge.jsonl"
    speak = tmp_path / "house-speak.jsonl"
    gate = tmp_path / "house-tts-gate.json"
    log.write_text('{"event":"speak","ts":"2026-07-31"}\n')
    speak.write_text('{"ok":"cosy","ts":"2026-07-31"}\n')
    gate.write_text('{"last_reply":"old speech"}\n')
    monkeypatch.setattr("crissa_bridge.ear_bridge.LOG", log)
    monkeypatch.setattr("crissa_bridge.ear_bridge.SPEAK_LOG", speak)
    monkeypatch.setattr("crissa_bridge.ear_bridge.TTS_GATE", gate)
    monkeypatch.setattr(
        "crissa_bridge.ear_bridge.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no chalk")),
    )
    _reset_speech_logs()
    assert log.read_text() == ""
    assert speak.read_text() == ""
    import json

    g = json.loads(gate.read_text())
    assert g["speaking"] is False
    assert g["last_reply"] == ""


def test_render_ada_direct_is_not_lilas_turn():
    from crissa_bridge.ear_bridge import to_other_mouth
    from crissa_bridge.wake import wake_match

    assert to_other_mouth(wake_match("Hey Ada, status of the board")) is True
    assert to_other_mouth(wake_match("Crissa, make it warmer")) is False
    assert to_other_mouth(wake_match("Lila, make it warmer")) is False
    assert to_other_mouth({"hit": False}) is False


def test_render_turns_go_to_lila_before_the_asleep_line():
    """the User 2026-09-13: Lila has the glass in Render. Behind the gate a Crissa or
    Lila wake, or a session follow-up, posts to her studio; Ada falls through
    to her own gateway; before Comfy is up nothing is said (the User 2026-09-14)."""
    import crissa_bridge.ear_bridge as eb

    src = Path(eb.__file__).read_text()
    block = src.split("if render_skips_crissa() and render_lila_host():", 1)[1]
    lila, rest = block.split(
        "if render_skips_crissa() and not (render_lila_host() and to_other_mouth(wm)):", 1
    )
    assert lila.index("to_other_mouth(wm)") < lila.index("comfy_up()") < lila.index("post_lila_turn(text)")
    assert "speak_blocking(" not in rest.split('if wm.get("hit"):')[0]


def test_post_lila_turn_posts_ear_session(monkeypatch):
    import crissa_bridge.ear_bridge as eb

    sent = []
    monkeypatch.setattr(eb, "_post_json", lambda url, payload, **kw: sent.append((url, payload)) or {"ok": True, "say": "On it."})
    monkeypatch.setattr(eb, "_log", lambda row: None)

    class _Now:
        def __init__(self, target=None, name=None, daemon=None):
            self._t = target

        def start(self):
            self._t()

    monkeypatch.setattr(eb.threading, "Thread", _Now)
    eb.post_lila_turn("Crissa, make it warmer")
    assert sent == [(eb.STUDIO_URL, {"text": "Crissa, make it warmer", "source": "ear-session"})]
