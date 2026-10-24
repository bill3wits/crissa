"""Round 20, view + identity + panel (the User 2026-10-14, .101 round 19 notes and his rulings):

1. the close view is the connection screen only: the first real request moves her to the multitasking view, once per
   call, never back to close inside the call; the next session opens close again;
2. ID-only: a face seen in this conversation keeps counting for actions for 30 s (no other face, session open);
3. ID-only: with no face, this turn's ear label naming the User by voice at 0.50 lets an action go ahead;
4. her context carries this turn's label (who, how, the score), so "what's my voice score?" has a true answer;
5. paint cores: the Wall on the rest below 12 physical cores is by design, and the state says so;
6. the Control Panel greys itself the moment All OS Off is accepted, and the switch starts after it has drawn.
No model, network, camera or sound: presence blocks are dicts, the ear's labels a file in tmp_path.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "modules" / "ear"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import crissa_home as H  # noqa: E402
from crissa_bridge import id_gate, user_mode  # noqa: E402

UID = "sam"


@pytest.fixture()
def kit(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "is_house", lambda manifest=None: False)
    monkeypatch.setattr(H, "HOME", tmp_path)
    for k in ("CRISSA_SECURITY_LOCK_ADJUST", "HOUSE_LIVE_FACE_AGE_S", "HOUSE_FIXED_FACE_AGE_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOUSE_USER_ID", UID)
    monkeypatch.setattr(user_mode, "HOUSE_MODE_PATH", tmp_path / "house-mode.json")
    user_mode._cache.update(ts=0.0, mode=None)
    (tmp_path / "user.json").write_text(json.dumps({"person_id": UID, "name": "Sam"}))
    (tmp_path / "security.json").write_text(json.dumps({"mode": "id_only"}))
    H._SEC_CACHE.clear()
    ear = tmp_path / "ear"
    ear.mkdir()
    monkeypatch.setattr(id_gate, "EAR", ear)
    monkeypatch.setattr(id_gate, "FACE_GRACE_S", 30.0)
    monkeypatch.setattr(id_gate, "VOICE_FALLBACK", True)
    monkeypatch.setattr(id_gate, "VOICE_MIN_SIM", 0.50)
    monkeypatch.setattr(id_gate, "LABEL_NOTE_SCORE", True)
    monkeypatch.setattr(id_gate, "sequence_on", lambda: False)
    id_gate.grace_end("test")
    id_gate.turn_text("")
    assert id_gate.mode() == "id_only"
    return ear


def _face(name=UID, sim=0.74, age=0.4, n=1, extra=()):
    faces = [{"name": name, "sim": sim}] + [dict(x) for x in extra]
    return {"user_id": UID, "face": {"locked": name, "faces": faces, "n_faces": n, "source": "visor", "age_s": age}}


NO_FACE = {"user_id": UID, "face": {"faces": [], "n_faces": 0, "source": "visor", "age_s": 0.4}}


def _label(ear, text, who, via, sim, ts=None):
    ts = round(time.time() if ts is None else ts, 3)
    with (ear / "speaker-labels.jsonl").open("a") as f:
        f.write(json.dumps({"ts": ts, "text": text, "who": None, "via": None, "sim": None, "at": ts}) + "\n")
        f.write(json.dumps({"ts": ts, "text": text, "who": who, "via": via, "sim": sim, "at": ts + 0.3}) + "\n")
    return ts


# ---- 2: the face grace ----------------------------------------------------------------------------------------------

def test_2_a_face_seen_keeps_counting_for_30_s(kit, monkeypatch):
    assert id_gate.act_face(_face())[:3:2] == (True, "face")
    ok, sim, why = id_gate.act_face(NO_FACE)
    assert ok and why == "face_grace" and sim == pytest.approx(0.74), "he picked something up: still him"
    t = time.time()
    monkeypatch.setattr(id_gate.time, "time", lambda: t + 31.0)
    assert id_gate.act_face(NO_FACE)[0] is False, "past 30 s the grace is over"


def test_2_another_face_ends_the_grace_at_once(kit):
    assert id_gate.act_face(_face())[0]
    assert id_gate.act_face(_face(name="kim", sim=0.8))[0] is False        # another named face, his not there
    assert id_gate.act_face(NO_FACE)[0] is False, "and it stays ended"


def test_2_his_face_beside_another_counts_now_but_opens_no_grace(kit):
    assert id_gate.act_face(_face())[0]
    both = _face(n=2, extra=({"name": "", "sim": None},))                  # him and someone else in view
    assert id_gate.act_face(both)[2] == "face", "his face now is enough, as it always was"
    assert id_gate.act_face(NO_FACE)[0] is False, "no grace once someone else was at the camera"


def test_2_the_session_closing_ends_the_grace_and_the_kill_switch(kit, monkeypatch):
    assert id_gate.act_face(_face())[0]
    id_gate.grace_end("session_end")
    assert id_gate.act_face(NO_FACE)[0] is False
    monkeypatch.setattr(id_gate, "FACE_GRACE_S", 0.0)
    assert id_gate.act_face(_face())[0]
    assert id_gate.act_face(NO_FACE)[0] is False, "CRISSA_ACT_FACE_GRACE_S=0: face now only"


def test_2_the_bridge_ends_the_grace_when_the_session_closes():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    fn = src.split("def _set_hear_session(open_: bool) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert 'id_gate.grace_end("session_end")' in fn and "view_call_reset()" in fn and "if not open_:" in fn


# ---- 3: voice is the fallback ---------------------------------------------------------------------------------------

def test_3_no_face_but_this_turns_voice_lets_it_go_ahead(kit):
    _label(kit, "turn off the lights", UID, "voice", 0.58)
    id_gate.turn_text("Turn off the lights.")
    ok, sim, why = id_gate.act_face(NO_FACE)
    assert ok and why == "voice" and sim == pytest.approx(0.58)
    v = id_gate.owner_verdict(NO_FACE, {})
    assert v["tools_ok"] and v["user_voice"] and not v["user_face_ok"] and v["user_face_sim"] is None
    assert v["user_voice_sim"] == pytest.approx(0.58) and v["auth_reason"] == "id_only_voice"
    assert v["speaker"]["who"] == UID and v["speaker"]["via"] == "voice"


@pytest.mark.parametrize("who, via, sim", [
    (UID, "voice", 0.45),          # under the User's 0.50
    ("kim", "voice", 0.80),        # another voice
    (UID, "face", 0.90),           # a face label is not a voice (and no face is at the camera now)
])
def test_3_a_voice_under_the_bar_or_not_his_does_not(kit, who, via, sim):
    _label(kit, "turn off the lights", who, via, sim)
    id_gate.turn_text("turn off the lights")
    assert id_gate.act_face(NO_FACE)[0] is False


def test_3_only_this_turns_label_counts(kit):
    t0 = _label(kit, "turn off the lights", UID, "voice", 0.60, ts=time.time() - 20)
    with (kit / "speaker-labels.jsonl").open("a") as f:           # a guest says the same words now: no label yet
        f.write(json.dumps({"ts": round(t0 + 15, 3), "text": "turn off the lights", "who": None, "via": None,
                            "sim": None, "at": t0 + 15}) + "\n")
    id_gate.turn_text("turn off the lights")
    assert id_gate.act_face(NO_FACE)[0] is False, "the newest turn with these words decides, not an older one"
    id_gate.turn_text("something else entirely")
    assert id_gate.turn_label() is None


def test_3_the_kill_switch_and_on_is_untouched(kit, monkeypatch):
    _label(kit, "turn off the lights", UID, "voice", 0.70)
    id_gate.turn_text("turn off the lights")
    monkeypatch.setattr(id_gate, "VOICE_FALLBACK", False)
    assert id_gate.act_face(NO_FACE)[0] is False
    (H.HOME / "security.json").write_text(json.dumps({"mode": "on"}))
    H._SEC_CACHE.clear()
    assert id_gate.owner_verdict(NO_FACE, {}) is None and id_gate.act_authed(NO_FACE) is None


def test_3_an_action_check_passes_at_once_on_grace_or_voice(kit, monkeypatch):
    monkeypatch.setattr(id_gate, "sequence_on", lambda: True)
    monkeypatch.setattr(H, "face_sequence", lambda *a, **k: pytest.fail("no sequence needed"), raising=False)
    _label(kit, "open the door", UID, "voice", 0.55)
    id_gate.turn_text("open the door")
    assert id_gate.act_check(NO_FACE) == (True, "voice", NO_FACE)


# ---- 4: her context carries the label --------------------------------------------------------------------------------

def test_4_her_note_names_how_and_the_score(kit, monkeypatch):
    monkeypatch.setenv("CRISSA_LABEL_NOTE_BOTH", "0")          # round 20's note (round 21's: its own test)
    _label(kit, "what's my voice score", UID, "voice", 0.59)
    note = id_gate.label_note(text="What's my voice score?")
    assert "what's my voice score" in note and "by voice" in note and "0.59" in note
    assert "answer from this note only" in note


def test_4_no_label_says_there_is_no_score(kit, monkeypatch):
    monkeypatch.setenv("CRISSA_LABEL_NOTE_BOTH", "0")          # round 20's note (round 21's: its own test)
    assert id_gate.label_note(text="hello there") == id_gate.NO_SCORE_LINE
    monkeypatch.setattr(id_gate, "LABEL_NOTE_SCORE", False)
    assert id_gate.label_note(text="hello there") == ""


def test_4_the_bridge_names_the_turn_and_hands_her_the_note():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    disp = src.split("def dispatch(user: str, meta: dict) -> None:", 1)[1].split("\ndef ", 1)[0]
    sched = src.split("def schedule_dispatch(user: str, meta: dict) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "id_gate.turn_text(user)" in disp and "id_gate.turn_text(user)" in sched
    assert "id_gate.label_note(text=user_text)" in src


# ---- 1: the close view is the connection ----------------------------------------------------------------------------

@pytest.fixture()
def eb(monkeypatch):
    import crissa_bridge.ear_bridge as eb
    monkeypatch.setattr(eb, "CLOSE_CONNECT_ONLY", True)
    monkeypatch.setattr(eb, "CLOSE_CONNECT_MIN_S", 2.0)
    monkeypatch.setattr(eb, "_log", lambda ev: None)
    eb.view_call_reset()
    return eb


def test_1_the_first_request_moves_her_once_and_never_back(eb):
    posts, slept = [], []
    eb.view_close_opened(now=time.time())
    assert not eb.view_keeps_multitask()
    sent = eb.view_to_multitask("request", post=lambda url, body, timeout: posts.append((url, body)) or {"dest": "x"},
                                sleep=slept.append, spawn=False)
    assert sent and posts == [(eb.CONDUCTOR_FT_DESK, {"by": "request"})]
    assert slept and 1.5 < slept[0] <= 2.0, "the close-up keeps its first 2 s"
    assert eb.view_keeps_multitask(), "a name-call in this call is answered where she is"
    assert eb.view_to_multitask("request", post=lambda *a, **k: posts.append(a), spawn=False) is False
    assert len(posts) == 1


def test_1_the_next_session_opens_close_again(eb):
    eb.view_to_multitask("request", post=lambda *a, **k: {}, sleep=lambda s: None, spawn=False)
    assert eb.view_keeps_multitask()
    eb._set_hear_session(False)
    assert not eb.view_keeps_multitask()


def test_1_the_kill_switch(eb, monkeypatch):
    monkeypatch.setattr(eb, "CLOSE_CONNECT_ONLY", False)
    assert eb.view_to_multitask("request", post=lambda *a, **k: pytest.fail("no move"), spawn=False) is False
    assert not eb.view_keeps_multitask()


def test_1_the_wake_and_the_request_are_wired():
    src = (ROOT / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py").read_text()
    ack = src.split("def bare_wake_ack(meta: dict) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "if view_keeps_multitask():" in ack and "view_close_opened()" in ack
    assert ack.index("view_keeps_multitask") < ack.index("cue_user_talk(")
    disp = src.split("def dispatch(user: str, meta: dict) -> None:", 1)[1].split("\ndef ", 1)[0]
    assert disp.index('if level == "silent":') < disp.index('view_to_multitask("request")')
    # round 24 (2026-10-16): a read-aloud ask keeps the close view (view_for_request); every other request still moves
    assert 'if not resume and view_for_request(user):\n        view_to_multitask("request")' in disp


# ---- 5: paint cores ---------------------------------------------------------------------------------------------------

def test_5_the_wall_on_the_rest_below_8_cores_is_said():
    import paint_cores as pc
    six = [(i, i + 6) for i in range(6)]
    p = pc.plan({"HOUSE_PAINT_CORES": "auto"}, cores=six)
    assert p["reserved"] == 1 and p["wall"] is None and p["player"] == {5, 11}
    import unittest.mock as um
    with um.patch.object(pc, "plan", lambda env=None: p):
        st = pc.state({"HOUSE_PAINT_CORES": "auto"})
    assert st["wall"] is None and st["wall_on"] == "rest" and "8 physical" in st["wall_why"]
    sixteen = [(i, i + 16) for i in range(16)]
    p2 = pc.plan({"HOUSE_PAINT_CORES": "auto"}, cores=sixteen)
    with um.patch.object(pc, "plan", lambda env=None: p2):
        assert "wall_on" not in pc.state({"HOUSE_PAINT_CORES": "auto"})


# ---- 6: the Control Panel says it before it goes ---------------------------------------------------------------------

def test_6_all_os_off_greys_the_page_and_waits_for_it():
    page = (ROOT / "display" / "crissa-panel.html").read_text()
    act = page.split("function act(payload){", 1)[1].split("\n}", 1)[0]
    assert "paintGone(true, true)" in act and "os_off|house_off" in act and "d.started" in act
    gone = page.split("function paintGone(on, going){", 1)[1].split("\n}", 1)[0]
    assert "GONE.hold=Date.now()+20000" in gone and "every service is stopping" in gone
    assert "if(GONE.on && Date.now()<GONE.hold) return;" in page
    srv = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert 'OFF_DRAW_S = float(os.environ.get("CRISSA_PANEL_OFF_DRAW_S", "1.5") or 0)' in srv
    off = srv.split('if act in ("os_off", "os_on"):', 1)[1].split("if act.startswith(\"mode_\"):", 1)[0]
    assert "threading.Timer(OFF_DRAW_S, _spawn_logged" in off and 'act == "os_off" and OFF_DRAW_S > 0' in off
