"""Pause is not the mic, and one expression of 'the session is open'.

Measured 2026-08-28 16:41:09: the hear gate read the bare `now <=
session_until` while the expiry check refused to drop the session during a
14.6s think + 18.0s speak. No session_end was ever logged, yet every
follow-up came back crissa_skip why:paused — 33s deaf inside a live session.
"""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[3] / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py"
CODE = SRC.read_text()


def test_session_live_is_the_single_liveness_expression():
    assert "def session_live(now: float) -> bool:" in CODE
    body = CODE.split("def session_live(now: float) -> bool:")[1]
    body = body.split("def hold_session")[0]
    # thinking or speaking keeps the session open
    assert "_dispatch_busy or _speaking" in body
    assert "_session_extend_until" in body

    # the hear gate must use it, not a bare comparison
    gate = CODE.split("sess_open = bool(")[1].split("gate = classify_hear")[0]
    assert "session_live(now)" in gate
    assert "now <= session_until" not in gate

    # the follow-up branch must agree with the gate
    assert "if session_live(now) and not gather_until:" in CODE
    assert "if session_until and now <= session_until and not gather_until:" not in CODE

    # expiry is a pure function so hold can be tested without the poll loop
    assert "def session_idle_should_drop(" in CODE
    idle = CODE.split("if session_idle_should_drop(")[1].split("drop_session(board=True)")[0]
    assert "comms_hold=" not in idle          # a duration, not a freeze
    assert "session_live(now)" in idle
    live = CODE.split("def session_live(now: float) -> bool:")[1].split("def hold_session")[0]
    assert "_comms_hold" not in live          # hold only lengthened session_until


def test_health_publishes_comms_hold():
    health = CODE.split('if path == "/health":')[1].split("return")[0]
    assert '"comms_hold"' in health
    assert "globals().get(\"_comms_hold\")" in health


def test_pause_route_exists_and_does_not_touch_the_mic():
    post = CODE.split("def do_POST")[1].split("def start_abort_listener")[0]
    assert 'if path == "/v1/session/pause":' in post
    assert 'if path == "/v1/session/open":' in post
    assert 'if path == "/v1/session/hold":' in post
    assert '_session_pause_req' in post
    assert '_session_open_req' in post
    assert '_session_hold_req' in post
    # pausing / opening / hold must never flip the Voice master
    assert "voice_on" not in post and "voice_off" not in post
    # the loop consumes it, so drop_session stays the one closer
    loop = CODE.split('if globals().get("_session_pause_req"):')[1]
    loop = loop.split("voice_edge_prev = voice_now")[0]
    assert "drop_session(board=True)" in loop
    assert '"why": "pause_request"' in loop
    assert 'if globals().get("_session_open_req"):' in CODE
    open_loop = CODE.split('if globals().get("_session_open_req"):')[1]
    open_loop = open_loop.split("voice_now = voice_on_cached()")[0]
    assert "hold_session(now)" in open_loop
    assert '"why": "chip"' in open_loop
    assert "bare_wake" not in open_loop
    assert "Here." not in open_loop


def test_voice_toggle_pauses_on_the_edge_not_the_next_utterance():
    loop = CODE.split("voice_now = voice_on_cached()")[1]
    loop = loop.split("voice_edge_prev = voice_now")[0]
    assert "voice_now != voice_edge_prev" in loop
    assert '"why": "voice_edge"' in loop
    assert "drop_session(board=True)" in loop
    # the loop runs at POLL=0.2s; voice_on() is a 1.5s-timeout GET
    assert "def voice_on_cached(" in CODE


def test_voice_pause_command_is_strict_and_in_session_only():
    import sys

    sys.path.insert(0, str(SRC.parents[1]))
    from crissa_bridge.ear_bridge import _VOICE_PAUSE

    for hit in ("voice pause", "Voice pause.", "voice-pause", "Voice, pause",
                "pause voice", "Pause voice."):
        assert _VOICE_PAUSE.match(hit), hit
    # bare "pause" is ordinary speech and must never drop the session
    for miss in ("pause", "voice", "can you pause the voice", "voice pause now",
                 "unpause", "pause that"):
        assert not _VOICE_PAUSE.match(miss), miss

    # it is checked inside the in-session branch, after the voice_on check
    branch = CODE.split("if session_live(now) and not gather_until:")[1]
    branch = branch.split("session_skip_short")[0]
    assert "_VOICE_PAUSE.match(text)" in branch
    assert branch.index("voice_on_cached()") < branch.index("_VOICE_PAUSE.match(text)")


def test_session_idle_should_drop_is_a_clock_not_a_freeze():
    """Hold lengthens session_until; the pure check knows nothing of it."""
    import sys

    sys.path.insert(0, str(SRC.parents[1]))
    from crissa_bridge.ear_bridge import session_idle_should_drop

    now = 1_000.0
    assert session_idle_should_drop(now, now - 90.0, live=False, gather=False) is True
    assert session_idle_should_drop(now, now - 90.0, live=True, gather=False) is False
    assert session_idle_should_drop(now, now - 90.0, live=False, gather=True) is False
    assert session_idle_should_drop(now, 0.0, live=False, gather=False) is False
    assert session_idle_should_drop(now, now + 3500.0, live=False, gather=False) is False


def test_drop_session_clears_comms_hold():
    drop = CODE.split("def drop_session")[1].split("start_abort_listener()")[0]
    assert 'globals()["_comms_hold"] = False' in drop
    assert 'globals()["_session_hold_req"] = None' in drop


def test_hold_release_rearms_session_until():
    loop = CODE.split('hold_req = globals().get("_session_hold_req")')[1]
    loop = loop.split("Active/LISTENING")[0]
    assert "session_until = now + SESSION_SEC" in loop
    assert '"event": "comms_hold"' in loop
    # pause is consumed first so explicit pause wins
    pause_at = CODE.find('if globals().get("_session_pause_req"):')
    hold_at = CODE.find('hold_req = globals().get("_session_hold_req")')
    assert 0 < pause_at < hold_at


def test_no_spoken_unpause_command():
    """Spoken 'unpause' is not a command. The wall chip POSTs /v1/session/open."""
    assert not re.search(r"unpause\s*=\s*re\.compile", CODE)
    assert "_VOICE_UNPAUSE" not in CODE
    assert '"/v1/session/open"' in CODE


def test_wake_ack_uses_faces_best_guess_not_a_green_lock(monkeypatch):
    """the User 2026-08-28: "after wake word, as soon as face says best guess
    the User. Sounded like me — real life like." His voice embed sits ~0.45 and
    rarely clears the 0.55 floor; the face runs 0.75-0.85. So the ack must not
    wait for a strong letter — but it must never guess a name it wasn't given.
    """
    import sys
    sys.path.insert(0, str(SRC.parents[1]))
    import crissa_bridge.ear_bridge as eb

    monkeypatch.setattr(eb, "user_id_from_mode", lambda *a, **k: "user")

    # the turn already named him: free, no presence call needed
    def boom(*a, **k):
        raise AssertionError("must not fetch presence when the turn names him")
    monkeypatch.setattr(eb, "_get", boom)
    assert eb.wake_ack_name({"person_id": "user"}) == "user"
    # someone else spoke — being called the User is worse than being called nothing
    assert eb.wake_ack_name({"person_id": "visitor"}) == ""
    assert eb.wake_ack_name({"is_user": False}) == ""
    # scratch is not a person
    assert eb.wake_ack_name({"person_id": "s1"}) == ""

    # No pid (the User 2026-10-10, .101 round 4 BB): a pack names only whom the ear named - the face's guess and
    # presence's last speaker are not this turn's voice, and a guest was greeted by the User's name
    monkeypatch.delenv("EAR_BRIDGE_WAKE_NAME_FROM_FACE", raising=False)
    monkeypatch.setattr(
        eb, "_get", lambda *a, **k: {"face": {"locked": "User"}, "speaker": {"person_id": "user"}})
    assert eb.wake_ack_name({}) == ""
    assert eb.wake_ack_name({"guest_rails": True}) == ""
    # the house's 08-28 rule by its switch: the VISOR's guess decides — face lock, a named face, or the
    # speaker row. Not occupancy `user_auth`: that is mill leftover and not a
    # name (Grok 2026-08-28, and the one-namer law in CAMERA-AUTH-AND-VISITORS).
    monkeypatch.setenv("EAR_BRIDGE_WAKE_NAME_FROM_FACE", "1")
    monkeypatch.setattr(
        eb, "_get", lambda *a, **k: {"face": {"locked": "User"}})
    assert eb.wake_ack_name({}) == "user"
    assert eb.wake_ack_name({"guest_rails": True}) == ""         # a guest turn is never named, switch or not
    monkeypatch.setattr(
        eb, "_get",
        lambda *a, **k: {"face": {"faces": [{"name": "User"}]}})
    assert eb.wake_ack_name({}) == "user"
    monkeypatch.setattr(
        eb, "_get", lambda *a, **k: {"speaker": {"person_id": "user"}})
    assert eb.wake_ack_name({}) == "user"
    # authed-but-unnamed is NOT a name
    monkeypatch.setattr(eb, "_get", lambda *a, **k: {"user_auth": True})
    assert eb.wake_ack_name({}) == ""
    monkeypatch.setattr(eb, "_get", lambda *a, **k: {"face": {"locked": None}})
    assert eb.wake_ack_name({}) == ""
    # a slow or broken presence makes the ack generic, never delays it
    def slow(*a, **k):
        raise TimeoutError("presence down")
    monkeypatch.setattr(eb, "_get", slow)
    assert eb.wake_ack_name({}) == ""


def test_named_ack_text_and_single_resolve(monkeypatch):
    """She says his name once the face check has him; the log matches the mouth."""
    import sys
    sys.path.insert(0, str(SRC.parents[1]))
    import crissa_bridge.ear_bridge as eb

    said = {}
    logged = []
    monkeypatch.setattr(eb, "speak_blocking",
                        lambda text, **k: said.setdefault("text", text))
    monkeypatch.setattr(eb, "_log", lambda d: logged.append(d))
    monkeypatch.setattr(eb, "user_id_from_mode", lambda *a, **k: "user")
    # No close-up cue: she is the only mouth, so she says the ack herself.
    monkeypatch.setattr(eb, "cue_user_talk", lambda **k: False)
    monkeypatch.setattr(eb, "vocative_ack_hold_sec", lambda cued, **k: 0.0)

    eb.bare_wake_ack({"person_id": "user", "profile": "crissa", "route": "direct"})
    assert said["text"] == "Yeah, User?"
    assert logged[0]["named"] == "user"

    # Cued (2026-09-08): the conductor says Hey the User after the lean-in, so the
    # ear must not put a second mouth on top of it — deferred, nothing spoken.
    said.clear(); logged.clear()
    monkeypatch.setattr(eb, "cue_user_talk", lambda **k: True)
    eb.bare_wake_ack({"person_id": "user", "profile": "crissa", "route": "direct"})
    assert "text" not in said
    assert [d["event"] for d in logged] == ["bare_wake_ack", "bare_wake_ack_deferred"]
    monkeypatch.setattr(eb, "cue_user_talk", lambda **k: False)

    said.clear(); logged.clear()
    eb.bare_wake_ack({"person_id": "visitor", "profile": "crissa", "route": "direct"})
    assert said["text"] == "Yeah?"          # unnamed stays generic
    assert logged[0]["named"] is None

    # both phrases stay tunable without a code change
    src = CODE
    assert "EAR_BRIDGE_BARE_WAKE_TEXT" in src
    assert "EAR_BRIDGE_WAKE_ACK_NAMED" in src
    # resolved once, so the journal cannot disagree with what she said
    body = src.split("def bare_wake_ack")[1].split("\ndef ")[0]
    assert body.count("wake_ack_name(meta)") == 1


def test_comms_hold_is_a_sixty_minute_window():
    """HOLD on = 3600 s quiet window, speech re-arms; HELD mm:ss counts session_until down."""
    import sys

    sys.path.insert(0, str(SRC.parents[1]))
    from crissa_bridge.ear_bridge import COMMS_HOLD_SEC, SESSION_SEC, idle_sec

    assert COMMS_HOLD_SEC == 3600.0
    assert idle_sec(True) == 3600.0
    assert idle_sec(False) == SESSION_SEC
    # every place the clock is armed uses idle_sec
    assert "time.time() + SESSION_SEC" not in CODE
    assert CODE.count("idle_sec(bool(globals().get(\"_comms_hold\")))") >= 4
    # arming mid-session gives the open session the long window from now
    arm = CODE.split('hold_req = globals().get("_session_hold_req")')[1].split("Active/LISTENING")[0]
    assert "session_until = max(session_until, now + COMMS_HOLD_SEC)" in arm
    # /health publishes the countdown while held only
    health = CODE.split('if path == "/health":')[1].split("return")[0]
    assert '"comms_hold_until"' in health and "_session_until_pub" in health
