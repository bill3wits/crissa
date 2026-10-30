"""Spoken session end must pin VoiceOnly home, not only clear notes."""
from pathlib import Path


def test_clear_session_board_pins_kiosk_wall():
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text(encoding="utf-8")
    assert "kiosk_wall" in src
    assert "ACTION_URL" in src
    assert "session_wall_home" in src
    assert "session_body_home" in src
    assert "CONDUCTOR_HOME" in src
    assert "/v1/home" in src
    assert "request_session_look" in src
    look = src.split("def request_session_look")[1].split("def _env_http_timeout")[0]
    assert "visit_lamp" in look
    assert 'lamp == "sage"' in look
    assert '"skipped": "secure"' in look
    assert "or auth or" not in look
    assert "age_s" not in look
    assert "session_open" in src
    assert "session_end" in src
    assert "hold_session" in src
    assert "drop_session" in src
    assert "ear_still_decoding" in src
    assert 'or os.environ.get("EAR_BRIDGE_LISTEN_SEC")' in src
    assert 'or "60"' in src


def test_session_stays_live_while_he_is_still_talking():
    """2026-08-30: Hey Zoe / Yeah? then 'read me the article' landed
    `crissa_skip why:paused` because session_end fired mid-utterance.
    Keyboard VAD must not hold LISTENING forever; a 20s cap after idle
    is the follow-up window.
    """
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text(encoding="utf-8")
    live = src.split("def session_live(now: float)")[1].split("def hold_session")[0]
    assert "ear_still_hearing()" in live
    assert "(now - session_until) < 20.0" in live
    follow = src.split("Session follow-up")[1].split('event": "session_utt"')[0]
    assert "hold_session(now, meta.get(\"person_id\"))" in follow
    flush = src.split("Last speaker was him")[1].split("schedule_dispatch")[0]
    assert "hold_session(now, m.get(\"person_id\"))" in flush
    assert 'or "60"' in src


def test_session_look_skips_only_sage(monkeypatch):
    """E1: amber + user_auth still FaceFinds. Sage is the only skip."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "ear"))
    import crissa_bridge.ear_bridge as eb

    posted = []

    def fake_get(url, timeout=0.45):
        return {
            "visit_lamp": "amber",
            "user_auth": True,
            "user_id": "user",
            "face": {"locked": "user", "age_s": 0.4},
            "sound": {},
        }

    def fake_post(url, body, timeout=1.5):
        posted.append(body)
        return {"ok": True, "look": True}

    monkeypatch.setattr(eb, "_get", fake_get)
    monkeypatch.setattr(eb, "_post_json", fake_post)
    out = eb.request_session_look(on=True, reason="session_open")
    assert out.get("skipped") != "auth"
    assert posted and posted[0].get("look") is True

    def sage_get(url, timeout=0.45):
        return {"visit_lamp": "sage", "user_auth": True}

    monkeypatch.setattr(eb, "_get", sage_get)
    posted.clear()
    out = eb.request_session_look(on=True, reason="session_open")
    assert out.get("skipped") == "secure"
    assert posted == []


def test_session_end_leaves_the_news_on_the_glass(monkeypatch):
    """2026-10-09: every session end wiped the headline until the next hourly post."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "ear"))
    import crissa_bridge.ear_bridge as eb

    posted = []
    monkeypatch.setattr(eb, "_post_json", lambda url, body, timeout=2.0: posted.append((url, body)) or {"ok": True})
    monkeypatch.setattr(eb, "DOC_WINDOW", Path("/nonexistent/doc-window.sh"))
    monkeypatch.delenv("EAR_BRIDGE_CLEAR_NEWS", raising=False)
    eb.clear_session_board()
    clear = [b for u, b in posted if u == eb.SCREEN_URL][-1]["clear"]
    assert "headline" not in clear and "note" not in clear
    assert {"notes", "doc", "art"} <= set(clear)

    monkeypatch.setenv("EAR_BRIDGE_CLEAR_NEWS", "1")
    eb.clear_session_board()
    assert {"headline", "note"} <= set([b for u, b in posted if u == eb.SCREEN_URL][-1]["clear"])
