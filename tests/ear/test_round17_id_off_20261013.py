"""Round 17: Security On / ID-only / Off for the kit's conversation (the User 2026-10-13; .101 RESULTS-20261013-0055 6-9,
ASK-20261013-0405: his words dropped `no_emb`, 5-10 s to get through, a house command held `await_voice`).

1. One setting, three positions (lib/crissa_home.security_mode, <state home>/security.json): ID-only by default, also
   on an install that never chose; CRISSA_SECURITY_DEFAULT_IDONLY=0 makes On the default; the house is always On. The
   Crissa menu (both Walls) shows and sets it.
2. ID-only and Off: no turn is dropped, held or decided by a voice print or a face (the hear gate, the turn's level,
   the session, the wake, own_voice's rule 3); TV-shaped sound and her own words still are.
3. ID-only: the ear sends the turn with no speaker and names it after (crissa_ear/id_trail.py); the log row is drawn at
   once with "?" and relabelled in place (the feed's label_rows, the Tk roll's replaces); she gets the label as a note.
4. ID-only: a tool call or a house action needs his face at the camera now, said plainly when it is not there; Off: no
   check. On: today's.
5. Secure / Double Secure are offered only in On: tests/presence/test_round17_id_off_secure_20261013.py.

No model, network, microphone, camera or sound: every service is a stub or a monkeypatch.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "modules" / "ear", ROOT / "lib", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_home  # noqa: E402
from _kit import kit, load as load_kit  # noqa: E402
from crissa_bridge import ear_bridge as eb  # noqa: E402
from crissa_bridge import hear_gate, house_commands, id_gate, own_voice, user_mode  # noqa: E402
from crissa_ear import id_trail  # noqa: E402

UID = "user"
FEED = "scripts/voiceonly-home-server.py"


@pytest.fixture()
def sec(tmp_path, monkeypatch):
    """A pack's state home in tmp_path; sec(mode) saves a choice, sec(None) leaves none (the default)."""
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.delenv("CRISSA_SECURITY_DEFAULT_IDONLY", raising=False)
    # ID-only is the default on a camera that turns (round 28: a laptop's fixed camera starts Off, its own tests)
    (tmp_path / "presence").mkdir(exist_ok=True)
    (tmp_path / "presence" / "camera-kind.json").write_text('{"kind": "motor"}')
    monkeypatch.setenv("HOUSE_USER_ID", UID)
    monkeypatch.setattr(id_trail, "EAR", tmp_path / "ear")
    monkeypatch.setattr(id_gate, "EAR", tmp_path / "ear")
    crissa_home._SEC_CACHE.clear()
    id_gate.grace_end("test")                     # round 20: no face grace or turn words left by an earlier test
    id_gate.turn_text("")

    def set_(mode):
        if mode is not None:
            (tmp_path / "security.json").write_text(json.dumps({"mode": mode}))
        crissa_home._SEC_CACHE.clear()
        return tmp_path
    return set_


def _face(age=0.4, name=UID, sim=0.74, fixed=False):
    f = {"locked": name, "faces": [{"name": name, "sim": sim}], "n_faces": 1, "source": "visor", "age_s": age}
    if fixed:
        f["fixed"] = True
    return {"headcount": 1, "face": f, "people": []}


# ---- 1: the setting -------------------------------------------------------------------------------------------------

def test_1_the_default_is_id_only_and_the_kill_makes_it_on(sec, monkeypatch):
    sec(None)
    assert crissa_home.security_mode() == "id_only"                      # an install that never chose
    monkeypatch.setenv("CRISSA_SECURITY_DEFAULT_IDONLY", "0")
    assert crissa_home.security_mode() == "on"                           # the kill: round 16's default
    sec("off")
    assert crissa_home.security_mode() == "off"                          # a saved choice stands under the kill
    sec("nonsense")
    assert crissa_home.security_mode() == "on"                           # an unreadable choice is the default


def test_1_the_house_is_always_on_and_cannot_save_one(sec, monkeypatch):
    sec("off")
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    assert crissa_home.security_mode() == "on"
    assert crissa_home.set_security_mode("off")["ok"] is False
    assert id_gate.gating() is True and id_trail.parallel_ok() is True


def test_1_set_and_read_back_at_once(sec):
    home = sec(None)
    for m in ("on", "off", "id_only"):
        assert crissa_home.set_security_mode(m, by="test") == {"ok": True, "mode": m}
        assert crissa_home.security_mode() == m and id_gate.mode() == m and id_trail.mode() == m
    assert json.loads((home / "security.json").read_text())["by"] == "test"
    assert crissa_home.set_security_mode("ID-only")["mode"] == "id_only"
    assert crissa_home.set_security_mode("loud")["ok"] is False


def test_1_the_menu_shows_and_sets_it(sec, monkeypatch):
    import crissa_menu as M
    home = sec(None)
    v = M.security_view(home)
    assert v["mode"] == "id_only" and v["label"] == "Security: ID-only" and v["chosen"] is False
    assert [m["mode"] for m in v["modes"]] == ["on", "id_only", "off"] and [m["on"] for m in v["modes"]] == [0, 1, 0]
    got = M.act("security", {"mode": "off"}, home=home)
    assert got["ok"] is True and got["security"]["mode"] == "off" and "lock" in got["note"]
    assert crissa_home.security_mode() == "off"
    import crissa_init
    monkeypatch.setattr(M, "_user", lambda: {"person_id": UID})
    monkeypatch.setattr(crissa_init, "voice_prints", lambda pid: 0)
    assert M.act("security", {"mode": "on"}, home=home)["note"] == M.SECURITY_ON_NO_PRINT
    assert M.act("security", {"mode": "x"}, home=home)["ok"] is False
    assert "security" in M.ACTIONS


def test_1_both_walls_carry_the_item():
    import crissa_wall_key as K
    sec_v = {"label": "Security: ID-only", "mode": "id_only",
             "modes": [{"mode": "on", "label": "On", "about": "a", "on": False},
                       {"mode": "id_only", "label": "ID-only", "about": "b", "on": True},
                       {"mode": "off", "label": "Off", "about": "c", "on": False}]}
    items = K.menu_items({"security": sec_v}, False)
    assert ("Security: ID-only", "security", True) in items
    assert ("Security: ID-only", "security", True) not in K.menu_items({}, False)   # an older player: no item
    assert K.security_lines(sec_v) == ["  On: a", "> ID-only: b", "  Off: c"]
    sent = []
    btn = K.security_buttons(sec_v, lambda body, fail_title="": sent.append(body))
    assert [b[0] for b in btn] == ["Cancel", "Off", "ID-only  (now)", "On"]      # _dialog draws them reversed
    btn[1][1]()
    assert sent == [{"action": "security", "mode": "off", "by": "the desk Wall"}]
    page = (ROOT / "display" / "crissa-wall.html").read_text(encoding="utf-8")
    assert "function security(box, sec)" in page and 'action: "security", mode: m.mode' in page


# ---- 2: the conversation is not gated outside On --------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["id_only", "off"])
@pytest.mark.parametrize("pid,source", [(None, "id_trailing"), ("s3", "voice_emb"), ("guest2", "voice_emb"),
                                        (None, "media_hold_sole")])
def test_2_any_voice_wakes_her_and_is_heard(sec, mode, pid, source):
    sec(mode)
    t = {"text": "Hey Crissa, what time is it?", "person_id": pid, "stamp": {"person_id": pid, "source": source}}
    g = hear_gate.classify_hear(t, session_open=False, uid=UID, user_has_prints=True)
    assert g["unpause"] and g["to_her"] and g["crissa_hears"] and not g["guest_rails"] and not g["no_print"]
    follow = dict(t, text="and tomorrow?")
    g2 = hear_gate.classify_hear(follow, session_open=True, uid=UID, user_has_prints=False)
    assert g2["to_her"] and g2["crissa_hears"] and g2["security"] == mode


def test_2_tv_shaped_sound_is_still_held(sec):
    sec("id_only")
    t = {"text": "Hey Crissa", "stamp": {"source": "media_gate", "media_gate": {"verdict": "media"}}}
    g = hear_gate.classify_hear(t, session_open=True, uid=UID)
    assert g["media"] and not g["unpause"] and not g["crissa_hears"]


def test_2_on_is_todays_hear_gate(sec):
    sec("on")
    t = {"text": "Hey Crissa", "person_id": "s3", "stamp": {"person_id": "s3", "source": "voice_emb"}}
    g = hear_gate.classify_hear(t, session_open=False, uid=UID, user_has_prints=True)
    assert not g["unpause"] and "security" not in g                      # a scratch voice never woke her


def test_2_the_turn_is_the_owners_never_await_voice(sec):
    sec("on")
    face_only = {"person_id": None, "source": "media_hold_sole"}
    on = user_mode.evaluate_turn(_face(), face_only)
    assert on["level"] == "silent" and on["auth_reason"] == "await_voice"   # today: his face alone waits for a voice
    sec("id_only")
    ev = user_mode.evaluate_turn({}, face_only)
    assert ev["level"] == "bau" and ev["auth"] is True and ev["auth_reason"].startswith("id_only_no_face")
    assert user_mode.authed_owner(ev) and eb.authorize({"stamp": face_only}, {}) == "bau"
    sec("off")
    ev = user_mode.evaluate_turn({}, {"person_id": "guest2"})
    assert ev["level"] == "bau" and ev["tools_ok"] is True and ev["auth_reason"] == "security_off"


def test_2_the_session_takes_any_voice_and_the_near_name_wakes(sec):
    sec("on")
    assert eb.session_follow_ok("user", "guest2", "and tomorrow?", UID) is False
    assert id_gate.any_voice_wakes({}) is False
    sec("id_only")
    assert eb.session_follow_ok("user", "guest2", "and tomorrow?", UID) is True
    assert id_gate.any_voice_wakes({"source": "id_trailing"}) is True
    assert id_gate.any_voice_wakes({"source": "media_gate", "media_gate": {"verdict": "media"}}) is False
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text(encoding="utf-8")
    assert "or id_gate.any_voice_wakes(_st_nw):" in src


def test_2_rule_3_stands_down_and_her_own_words_still_drop(sec):
    say = own_voice.SayLog()
    now = time.time()
    t = {"text": "Who is there?", "ts": now, "stamp": {"is_user_reason": "no_emb"}}
    sec("on")
    assert own_voice.verdict(t, say, guest=True, now=now)["rule"] == "guest_unsure"
    sec("id_only")
    assert own_voice.verdict(t, say, guest=True, now=now)["rule"] is None
    say.add(now - 3.0, now + 2.0, "The sea was calm and the ship sailed on through the night.")
    echo = {"text": "the ship sailed on through the night", "ts": now, "duration_s": 2.0, "stamp": {}}
    assert own_voice.verdict(echo, say, guest=True, now=now)["rule"] == "said_it_herself"


# ---- 3: the ear sends first and names after -------------------------------------------------------------------------

def _handler(monkeypatch, text="Hey Crissa, what time is it?"):
    from crissa_ear import media_gate, server, voice_live
    from crissa_ear.pipeline import Transcript
    monkeypatch.setattr(media_gate, "_state", media_gate.MediaGateState())
    kicks = []
    monkeypatch.setattr(voice_live, "kick", lambda pcm, sr: kicks.append(len(pcm)) or None)
    monkeypatch.setattr(server, "fetch_presence", lambda *a, **k: {"headcount": 1, "people": []})
    monkeypatch.setattr(server, "_maybe_post_sound", lambda st: None)
    monkeypatch.setattr(server, "_try_ecapa", lambda pcm, sr: pytest.fail("no embedding in the turn's path"))
    monkeypatch.setattr(server, "stamp_turn", lambda text, presence, **k: {
        "person_id": UID, "source": "media_hold_sole" if k.get("voice_emb") is None else "voice_emb",
        "media_suppressed_id": UID, "certain": True})

    class _Tr:
        def transcribe_array(self, wav, sr):
            return Transcript(text=text, confidence=0.9, engine="stub", duration_s=len(wav) / sr, rms_db=-28.0)
    monkeypatch.setattr(server, "get_transcriber", lambda cfg=None: _Tr())
    from crissa_ear.config import Config
    H = server.make_handler(Config())
    return H.__new__(H), kicks


@pytest.mark.parametrize("mode", ["id_only", "off"])
def test_3_the_turn_is_sent_with_no_speaker_and_no_print_in_its_way(sec, monkeypatch, mode):
    home = sec(mode)
    labelled = []
    monkeypatch.setattr(id_trail, "recognise", lambda pcm, sr, text: labelled.append(text) or
                        {"who": UID, "via": "voice", "sim": 0.61})
    h, kicks = _handler(monkeypatch)
    wav = np.zeros(16000, np.float32) + 0.01
    d = h._process_listen_array(wav, 16000, via="ear")
    assert kicks == []                                                   # nothing beside the decode
    assert d["person_id"] is None and d["id_trail"] == mode and "probably" in d
    assert d["stamp"]["source"] == ("id_trailing" if mode == "id_only" else "id_off")
    assert "media_suppressed_id" not in d["stamp"]                       # an identity hold is not media
    assert id_trail.wait_idle(5.0)
    rows = [json.loads(x) for x in (home / "ear" / "speaker-labels.jsonl").read_text().splitlines()]
    assert rows[0]["who"] is None and rows[0]["ts"] == round(d["ts"], 3)
    # Round 29 (.101's fix, the User 2026-10-17: "same task whether secure on or off"): Off labels the speaker like
    # ID-only, after the turn is sent, gating nothing (CRISSA_OFF_LABELS=0 = round 17: no recognition in Off).
    assert labelled and rows[-1]["who"] == UID and rows[-1]["via"] == "voice"
    assert d["probably"] == {"who": UID, "via": "voice", "sim": 0.61}       # the same ring row, filled in


def test_3_on_is_todays_ear(sec, monkeypatch):
    sec("on")
    h, kicks = _handler(monkeypatch)
    from crissa_ear import server
    monkeypatch.setattr(server, "_try_ecapa", lambda pcm, sr: None)
    d = h._process_listen_array(np.zeros(16000, np.float32) + 0.01, 16000, via="ear")
    assert kicks == [32000] and "id_trail" not in d and d["stamp"]["source"] == "media_hold_sole"
    from crissa_ear import ear_loop
    assert ear_loop._id_parallel() is True
    sec("id_only")
    assert ear_loop._id_parallel() is False


def test_3_the_log_row_is_drawn_at_once_and_relabelled_in_place(sec, tmp_path, monkeypatch):
    F = load_kit(FEED, "vo_feed_r17_idoff")
    R = importlib.import_module("house_wall_ascii_render")
    t = 1_800_000_000.123
    shell = [{"who": "stranger", "text": "What time is it?", "ts": t, "source": "id_trailing", "log_user": False}]
    first = F.label_rows(shell, labels={}, user=UID)
    assert first[0]["who"] == "?" and first[0]["log_user"] is True
    roll = R.Roll(since=0.0)
    roll.feed(first, now=t + 1)
    lab = {(round(t, 3), "What time is it?"): {"who": UID, "via": "voice", "sim": 0.6}}
    named = F.label_rows(shell, labels=lab, user=UID)
    assert named[0]["who"] == UID and named[0]["probably"] is True and named[0]["certain"] is False
    assert ["?", "What time is it?", round(t, 3)] in named[0]["replaces"]
    roll.feed(named, now=t + 4)
    assert len(roll.lines) == 1 and roll.lines[0]["who"] == UID and roll.lines[0]["at"] == t + 1   # where it stood
    assert roll.lines[0]["cls"] == "user"
    stamped = [{"who": UID, "text": "Hi.", "ts": t, "source": "voice_emb"}]
    assert F.label_rows(stamped, labels={}, user=UID) == stamped          # On / the house: as the face shell gave it
    heard = [{"who": "?", "text": "What time is it? · queued: she answers after this", "said": "What time is it?",
              "ts": t + 0.4, "kind": "heard", "source": "heard_roll"}]
    one = F.one_row_chat(named, heard)
    assert len(one) == 1 and one[0]["who"] == UID and ["?", "What time is it?", round(t, 3)] in one[0]["replaces"]
    src = kit(FEED).read_text(encoding="utf-8")
    assert "chat = label_rows(chat)" in src and "LOG_LABEL_LATER = " in src
    glass = kit("display/voiceonly-home.html").read_text(encoding="utf-8")
    assert "function rowRelabel(" in glass and "if(rowRelabel(item, who, t, ck, fromUser)) return;" in glass


def test_3_her_reference_note_is_the_last_label(sec, monkeypatch):
    monkeypatch.setenv("CRISSA_LABEL_NOTE_BOTH", "0")      # round 17/20's note (round 21's: test_round21_handover)
    home = sec("id_only")
    (home / "user.json").write_text(json.dumps({"person_id": UID, "name": "Tester"}))
    monkeypatch.setattr("crissa_bridge.home.HOME", home)
    (home / "ear").mkdir(parents=True, exist_ok=True)
    now = time.time()
    (home / "ear" / "speaker-labels.jsonl").write_text(
        json.dumps({"ts": now - 2, "text": "What time is it?", "who": None, "at": now - 2}) + "\n"
        + json.dumps({"ts": now - 2, "text": "What time is it?", "who": UID, "via": "voice", "at": now - 1}) + "\n")
    note = id_gate.label_note(now)
    assert "probably said by Tester" in note and "not a check" in note
    sec("on")
    assert id_gate.label_note(now) == ""
    sec("id_only")
    assert id_gate.label_note(now + 3600) == id_gate.NO_SCORE_LINE        # an old label is no help (round 20: said)


# ---- 4: actions -----------------------------------------------------------------------------------------------------

def test_4_a_house_command_needs_his_face_in_id_only(sec, monkeypatch):
    monkeypatch.setenv("CRISSA_SECURITY_LOCK_ADJUST", "0")   # round 17 as built: the kill of the face-match sequence
    sec("id_only")
    said, ran, logs = [], [], []
    monkeypatch.setattr(house_commands, "try_handle_utterance", lambda *a, **k: ran.append(a[0]) or True)
    monkeypatch.setattr(id_gate, "ACT_FACE_WAIT_S", 0.0)
    assert id_gate.house_command("voice off", {}, {}, speak=said.append, log=logs.append) is True
    assert said == [id_gate.ACT_NO_CAMERA_LINE] and ran == [] and logs[0]["event"] == "house_cmd_face_refused"
    said.clear()
    assert id_gate.house_command("voice off", {}, _face(age=9.0), speak=said.append, log=logs.append) is True
    assert said == [id_gate.ACT_FACE_LINE] and ran == []                # a face seen 9 s ago is not "at the camera"
    assert id_gate.house_command("voice off", {}, _face(), speak=said.append, log=logs.append) is True
    assert ran == ["voice off"]
    assert id_gate.house_command("what is the weather", {}, {}, speak=said.append, log=logs.append) is False
    id_gate.grace_end("test")                     # round 20: his face just now would carry 30 s; this is the wait
    frames = iter([{}, _face()])                                          # the camera lands on him while she waits
    monkeypatch.setattr(id_gate, "ACT_FACE_POLL_S", 0.0)
    ok, why, _p = id_gate.act_check({}, lambda: next(frames), wait_s=1.0)
    assert ok and why == "face"
    assert id_gate.act_check(_face(age=5.0, fixed=True))[0] is True       # a fixed camera's look: 6 s is "now"


def test_4_off_runs_it_and_on_keeps_todays_gate(sec, monkeypatch):
    monkeypatch.setenv("CRISSA_SECURITY_LOCK_ADJUST", "0")   # round 17 as built: the kill of the face-match sequence
    ran = []
    monkeypatch.setattr(house_commands, "try_handle_utterance", lambda *a, **k: ran.append(a[0]) or True)
    sec("off")
    assert id_gate.house_command("voice off", {}, {}, speak=lambda s: None, log=lambda r: None) and ran == ["voice off"]
    assert house_commands._is_authed({}, {}) is True
    sec("id_only")
    assert house_commands._is_authed({}, {}) is False and house_commands._is_authed({}, _face()) is True
    assert house_commands._refusal() == id_gate.ACT_FACE_LINE
    assert house_commands._maybe_intrusion("voice off", "voice-off", {}, {}) == {"skipped": "security_not_on"}
    sec("on")
    assert id_gate.act_authed({}) is None and house_commands._refusal() == house_commands._REFUSAL
    src = (ROOT / "modules/ear/crissa_bridge/ear_bridge.py").read_text(encoding="utf-8")
    assert "    if not id_gate.gating():\n" in src and "    elif is_user_auth_only():\n" in src


def _reply(monkeypatch, tool_at=None):
    seen = {}

    def fake_stream(url, payload, **kw):
        seen["payload"] = payload
        if tool_at is not None:
            kw["on_tool"](tool_at)
        return "The time is noon."
    monkeypatch.setattr(eb, "_stream_chat_text", fake_stream)
    monkeypatch.setattr(eb, "_gateway", lambda p: {"url": "http://127.0.0.1:9/v1/chat/completions", "model": "stub",
                                                   "key": "", "profile": "crissa"})
    monkeypatch.setattr(eb, "_log", lambda row: seen.setdefault("log", []).append(row))
    return seen


def test_4_a_tool_call_without_his_face_is_stopped_and_said(sec, monkeypatch):
    monkeypatch.setenv("CRISSA_SECURITY_LOCK_ADJUST", "0")   # round 17 as built: the kill of the face-match sequence
    sec("id_only")
    monkeypatch.setattr(id_gate, "ACT_FACE_WAIT_S", 0.0)
    monkeypatch.setattr(eb, "_get", lambda url, timeout=1.0: {})
    seen = _reply(monkeypatch, tool_at=7)
    out = eb.crissa_reply("put the board up", person_id=None, presence={}, level="bau", user_voice=False)
    assert out == id_gate.ACT_NO_CAMERA_LINE
    assert seen["payload"]["tool_choice"] == "none"                       # no face at the turn: no tools on the wire
    sys_txt = seen["payload"]["messages"][0]["content"]
    assert "say plainly that you need to see their face at the camera first" in sys_txt
    assert any(r.get("event") == "tool_refused_no_face" for r in seen["log"])
    monkeypatch.setattr(eb, "_get", lambda url, timeout=1.0: _face())
    seen = _reply(monkeypatch, tool_at=7)
    out = eb.crissa_reply("put the board up", person_id=None, presence=_face(), level="bau", user_voice=True)
    assert "noon" in str(out) and "tool_choice" not in seen["payload"]


def test_4_off_and_on_never_check_a_face_at_the_tool(sec, monkeypatch):
    marker = lambda n: n  # noqa: E731
    for m in ("on", "off"):
        sec(m)
        assert id_gate.tool_guard(marker, lambda: pytest.fail("no face check")) is marker
    sec("off")
    assert "Security is Off" in id_gate.reply_line(True)
    sec("on")
    assert id_gate.reply_line(True) == ""
