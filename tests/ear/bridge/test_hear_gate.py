"""Hear gate sits behind STT: ID always, Crissa only if to_her."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "ear"))

from crissa_bridge.hear_gate import classify_hear


def _turn(text, pid=None, *, media=False, sim=None):
    st = {"person_id": pid}
    if media:
        st["media_gate"] = {"verdict": "media", "media_on": True}
    if sim is not None:
        st["voice_emb_sim"] = sim
    return {"text": text, "person_id": pid, "stamp": st}


def test_tv_hey_zoe_scratch_does_not_unpause():
    g = classify_hear(_turn("Hey Crissa.", "s1"), session_open=False, uid="user")
    assert g["vocative"] is True
    assert g["to_her"] is False
    assert g["paused"] is True
    assert g["crissa_hears"] is False


def test_other_named_vocative_does_not_unpause():
    """Measured 2026-09-05: Hey Zoe stamped visitor opened LISTENING + desk
    FaceFinder, then crissa_skip not_to_her. Camera left the door; she did
    not answer; User roll stayed empty."""
    g = classify_hear(_turn("Hey Crissa.", "visitor"), session_open=False, uid="user")
    assert g["vocative"] is True
    assert g["other"] is True
    assert g["unpause"] is False
    assert g["paused"] is True
    assert g["crissa_hears"] is False
    assert g["log_user"] is False
    assert g["log_room"] is True


def test_unstamped_vocative_unpauses_as_guest():
    """Hey Crissa unpauses LISTENING; without ID she answers on Guest rails."""
    g = classify_hear(_turn("Hey Crissa."), session_open=False, uid="user")
    assert g["vocative"] is True
    assert g["unpause"] is True
    assert g["paused"] is False
    assert g["guest_rails"] is True
    assert g["user_ready"] is False
    assert g["to_her"] is False
    assert g["crissa_hears"] is True


def test_user_vocative_unpauses():
    """the User vocative unpauses. Below thresh stays Guest rails (she may guest-talk)."""
    g = classify_hear(_turn("Hey Crissa.", "user"), session_open=False, uid="user")
    assert g["vocative"] is True
    assert g["unpause"] is True
    assert g["paused"] is False
    assert g["user_ready"] is False
    assert g["guest_rails"] is True
    assert g["to_her"] is False
    assert g["crissa_hears"] is True
    assert g["log_user"] is True


def test_green_user_vocative_is_user():
    g = classify_hear(
        _turn("Hey Crissa.", "user", sim=0.62),
        session_open=False,
        uid="user",
    )
    assert g["user_ready"] is True
    assert g["guest_rails"] is False
    assert g["to_her"] is True
    assert g["crissa_hears"] is True


def test_paused_user_verified_does_not_hear():
    g = classify_hear(
        _turn("I need a left-handed mouse.", "user"),
        session_open=False,
        uid="user",
    )
    assert g["user"] is True
    assert g["to_her"] is False
    assert g["paused"] is True
    assert g["crissa_hears"] is False
    assert g["log_user"] is True
    assert g["log_room"] is False


def test_unpaused_user_logs_and_hears():
    g = classify_hear(
        _turn("What is the weather.", "user", sim=0.62),
        session_open=True,
        uid="user",
    )
    assert g["to_her"] is True
    assert g["paused"] is False
    assert g["user_ready"] is True
    assert g["crissa_hears"] is True
    assert g["log_user"] is True
    assert g["log_room"] is False


def test_listening_unstamped_followup_hears():
    """LISTENING lamp is a lie if pid=null follow-ups skip (2026-09-09)."""
    g = classify_hear(_turn("What month?"), session_open=True, uid="user")
    assert g["paused"] is False
    assert g["other"] is False
    assert g["crissa_hears"] is True
    g2 = classify_hear(_turn("What month?", "s1"), session_open=True, uid="user")
    assert g2["crissa_hears"] is False
    g3 = classify_hear(_turn("What month?", "visitor"), session_open=True, uid="user")
    assert g3["crissa_hears"] is False


def test_is_user_false_does_not_ride_open_session():
    """2026-09-09 09:38: wife 'take care of yourself' after Hey Zoe.
    is_user False, pid null — must not guest-dispatch as his follow-up."""
    t = _turn("You have to take care of yourself.")
    t["stamp"]["is_user"] = False
    g = classify_hear(t, session_open=True, uid="user")
    assert g["crissa_hears"] is False
    assert g["unpause"] is False
    t2 = _turn("Hey Crissa.")
    t2["stamp"]["is_user"] = False
    g2 = classify_hear(t2, session_open=False, uid="user")
    assert g2["unpause"] is False
    assert g2["crissa_hears"] is False


def test_room_chatter_ids_but_she_does_not_hear():
    g = classify_hear(
        _turn("Kids are home.", "second"),
        session_open=True,
        uid="user",
    )
    assert g["other"] is True
    assert g["to_her"] is False
    assert g["crissa_hears"] is False
    assert g["log_room"] is True
    assert g["log_user"] is False


def test_media_is_not_to_her():
    g = classify_hear(
        _turn("Coming up next on the news.", "user", media=True),
        session_open=True,
        uid="user",
    )
    assert g["media"] is True
    assert g["to_her"] is False
    assert g["crissa_hears"] is False
    assert g["log_user"] is False
    assert g["log_room"] is True


def test_ear_bridge_calls_gate():
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text()
    assert "classify_hear" in src
    assert "crissa_skip" in src
    assert "hear_gate" in src
    health = Path(__file__).resolve().parents[3] / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py"
    assert "paused" in health.read_text()


def test_gate_classifies_before_heard_and_before_mouth():
    """Pause sits in front of her reply, not behind the ear log."""
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text()
    loop = src.split("def main")[1]
    assert loop.find("classify_hear") < loop.find('"event": "heard"')
    assert loop.find("classify_hear") < loop.find("schedule_dispatch")
    assert "_drop_pending" in src
    assert "drain_drop" in src
    assert 'else "append"' in src
    assert '"keep"' in src.split("event\": \"coalesce\"")[1][:200]
    # His follow-up gather must not stretch LISTENING.
    flush = loop.split("if now > gather_until")[1].split("schedule_dispatch")[0]
    assert "hold_session" not in flush
    # Render "asleep" mouth is behind crissa_hears. Cancel/confirm stay
    # ahead of the skip so pause does not eat an in-flight cancel.
    assert loop.find('if not gate["crissa_hears"]') < loop.find("render_skips_crissa")
    heard = loop.find('"event": "heard"')
    yield_turn = loop.find("pa_yield_for_hear(gate")
    assert heard < yield_turn
    assert yield_turn < loop.find('if not gate["crissa_hears"]')
    assert "session_live(now) and ear_still_hearing()" in loop
    assert '"event": "mouth_drop_pass"' in src
    assert '"why": "idle_mouth"' in src
    assert "--pa-yield" in src
    assert "media_turn" in src
    assert "is_scratch" in src.split("def name_interrupt_hit")[1].split("\ndef ")[0]


def test_vocative_hold_defaults_to_now(monkeypatch):
    """Greet while turning. Sit-up still cues; paplay does not wait 6–8 s."""
    import crissa_bridge.ear_bridge as eb

    monkeypatch.delenv("EAR_BRIDGE_WAKE_ACK_HOLD_SEC", raising=False)
    assert eb.vocative_ack_hold_sec(False) == 0.0
    monkeypatch.setattr(
        eb, "_get",
        lambda *a, **k: {"now": "work", "of": 120, "frame": 0, "fps": 24.0},
    )
    assert eb.vocative_ack_hold_sec(True) == 0.0
    assert eb.vocative_ack_hold_sec(True, auth_held=True) == 0.0
    monkeypatch.setenv("EAR_BRIDGE_WAKE_ACK_HOLD_SEC", "6")
    assert eb.vocative_ack_hold_sec(True) == 6.0
    monkeypatch.setenv("EAR_BRIDGE_WAKE_ACK_HOLD_SEC", "0")
    assert eb.vocative_ack_hold_sec(True) == 0.0


def test_vocative_situp_always_says_yeah():
    """Scripted Yeah?. Sit-up cues in parallel; paplay does not wait the enter."""
    src = (
        Path(__file__).resolve().parents[3]
        / "modules" / "ear"
        / "crissa_bridge"
        / "ear_bridge.py"
    ).read_text()
    ack = src.split("def bare_wake_ack")[1].split("\ndef dispatch")[0]
    assert '"Yeah?"' in ack
    assert "skip_quiet=True" in ack
    assert "not_before" in ack
    assert "cue_user_talk(named=bool(who)" in ack        # round 4 BB: the player greets by name only when the ear named
    assert "vocative_ack_hold_sec" in ack
    assert "auth_held=_presence_auth_held()" in ack
    assert "EAR_BRIDGE_WAKE_ACK_HOLD_SEC" in ack
    assert '"Here."' not in ack
    assert "4.833" not in ack
    wake = src.split("# Vocative lean-in always says the scripted Yeah?")[1].split(
        "if gather_until and now <= gather_until"
    )[0]
    assert "target=bare_wake_ack" in wake
    assert "has_ask" in wake
    assert "buf = [residual]" in wake
    speak = src.split("def speak_blocking(")[1].split("\ndef cue_user_talk")[0]
    # skip_quiet must not fall through the wait-quiet else (that returns).
    assert "if not skip_quiet:" in speak
    assert "USER_TALK_ENTER_SEC = 6.04" in src
    assert "laptop-closeup-eyes-down" in src
    assert "/v1/cue" in src
    cue = src.split("def cue_user_talk")[1].split("\ndef ")[0]
    assert "/v1/vocative" in cue or "CONDUCTOR_VOCATIVE" in cue
    assert '"key": USER_TALK_KEY' not in cue
    assert "VOCATIVE_KEY" in cue or '"wake"' in cue
    assert "timeout=8.0" in cue
    ack = src.split("def bare_wake_ack")[1].split("\ndef dispatch")[0]
    assert "bare_wake_ack_deferred" in ack
    assert "if cued and not addressed:" in ack


def test_pa_yield_for_hear_on_user_ready_or_vocative(tmp_path, monkeypatch):
    """Authed User speech cuts PA even when pause skips her reply."""
    play = tmp_path / "play.json"
    play.write_text("{}")
    speak = tmp_path / "house-speak.py"
    speak.write_text("pass\n")
    import crissa_bridge.ear_bridge as eb

    monkeypatch.setattr(eb, "PA_PLAY", play)
    monkeypatch.setattr(eb, "HOUSE_SPEAK", speak)
    called: dict = {}

    def fake_popen(cmd, **kwargs):
        called["cmd"] = cmd
        return object()

    monkeypatch.setattr(eb.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(eb, "_log", lambda d: None)
    eb.pa_yield_for_hear({"unpause": False, "user_ready": False})
    assert "cmd" not in called
    eb.pa_yield_for_hear({"unpause": False, "user_ready": True})
    assert called["cmd"][-1] == "--pa-yield"
    called.clear()
    play.unlink()
    eb.pa_yield_for_hear({"unpause": True, "user_ready": False})
    assert "cmd" not in called
    play.write_text("{}")
    eb._pa_yield_at = 0.0
    eb.pa_yield_for_hear({"unpause": True, "user_ready": False})
    assert called["cmd"][-1] == "--pa-yield"
    called.clear()
    eb._pa_yield_at = 0.0
    eb.pa_yield_for_hear({"unpause": False, "user_ready": False}, session_open=True)
    assert called["cmd"][-1] == "--pa-yield"


def test_echo_gate_consults_the_house_mouth_not_only_its_own_lines():
    # 2026-09-11: a Composer /v1/say line (tts_sim 0.81) passed as idle_mouth and
    # hit the wall as a STRANGER — the bridge only knew its own spawns.
    from pathlib import Path
    src = (Path(__file__).resolve().parents[3] / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    assert "def house_mouth_on_air" in src
    assert "_speaking or _speak_proc is not None or house_mouth_on_air()" in src
    import crissa_bridge.ear_bridge as eb
    eb._HOUSE_AIR_CACHE["t"] = 0.0
    eb._get = lambda url, timeout=1.0: {"air_at": 100.0, "air_until": None}
    assert eb.house_mouth_on_air(now=101.0) is True
    eb._HOUSE_AIR_CACHE["t"] = 0.0
    eb._get = lambda url, timeout=1.0: {"air_at": 100.0, "air_until": 104.0}
    assert eb.house_mouth_on_air(now=110.0) is True     # inside the 8 s tail (STT lands late)
    eb._HOUSE_AIR_CACHE["t"] = 0.0
    assert eb.house_mouth_on_air(now=113.0) is False
    eb._HOUSE_AIR_CACHE["t"] = 0.0
    eb._get = lambda url, timeout=1.0: (_ for _ in ()).throw(OSError("away"))
    assert eb.house_mouth_on_air(now=101.0) is False    # fail open
