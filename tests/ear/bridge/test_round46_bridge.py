"""Round 46 (the local review of kit crissa-063a2813, 2026-10-23): the bridge and the ear's correctness items.

A2  the bridge's port (:8194) and `crissa-bridge serve` answer this machine only; a bad Content-Length is refused.
C1  the turns already seen keep the NEWEST when trimmed (a set trimmed at random replayed old wakes).
C2  a joined ask never takes a more trusted speaker from a later part (gather, coalesce, speculative restart).
C3  the User is the id Init gave, not only the literal "user" (id_matrix guard, stamp's voice column).
C4  "Crissa, one moment" is said to her, not her callout's echo.
C5  a recorder that dies is an error, never zeros.
C-lower  a short real line with "subscribe" or "www." is not a decoder ghost.
Each with its kill. Nothing here plays or records sound (parec is a fake).
"""
from __future__ import annotations

import http.client
import io
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

from crissa_bridge import cli as bridge_cli
from crissa_bridge import ear_bridge as eb
from crissa_bridge import hear_gate, speculate
from crissa_ear import audio, id_matrix, quality

SRC = Path(__file__).resolve().parents[3] / "modules" / "ear" / "crissa_bridge" / "ear_bridge.py"
EVIL = {"Origin": "https://evil.example"}


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def call(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_accept_encoding=True)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if body is not None and not (headers and "Content-Length" in headers):
        c.putheader("Content-Length", str(len(body)))
    c.endheaders()
    if body:
        c.send(body)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


# --- A2 ----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def bridge_port(monkeypatch):
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    httpd = _serve(eb._AbortHandler)
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_bridge_refuses_a_foreign_page(bridge_port, monkeypatch):
    monkeypatch.setitem(eb.__dict__, "_session_pause_req", False)
    assert call(bridge_port, "POST", "/v1/session/pause", EVIL, b"")[0] == 403
    assert eb.__dict__.get("_session_pause_req") is False               # nothing was paused
    assert call(bridge_port, "POST", "/v1/session/pause", {"Sec-Fetch-Site": "cross-site"}, b"")[0] == 403
    assert call(bridge_port, "GET", "/health", EVIL)[0] == 403
    assert call(bridge_port, "POST", "/v1/session/pause", {"Origin": "http://localhost:8100"}, b"")[0] == 200
    assert eb.__dict__.get("_session_pause_req") is True


def test_bridge_guard_kill(bridge_port, monkeypatch):
    monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
    monkeypatch.setitem(eb.__dict__, "_session_pause_req", False)
    assert call(bridge_port, "POST", "/v1/session/pause", EVIL, b"")[0] == 200


def test_bridge_bad_content_length(bridge_port, monkeypatch):
    assert call(bridge_port, "POST", "/v1/session/hold", {"Content-Length": "-1"})[0] == 400
    assert call(bridge_port, "POST", "/v1/barge", {"Content-Length": str(eb.BODY_MAX + 1)})[0] == 400
    monkeypatch.setitem(eb.__dict__, "_session_hold_req", None)
    st, body = call(bridge_port, "POST", "/v1/session/hold", None, b'{"on": true}')
    assert st == 200 and json.loads(body)["comms_hold"] is True


def test_bridge_cli_serve_guarded(monkeypatch):
    monkeypatch.delenv("CRISSA_LOOPBACK_GUARD", raising=False)
    httpd = _serve(bridge_cli.make_serve_handler())
    port = httpd.server_address[1]
    try:
        assert call(port, "GET", "/health", EVIL)[0] == 403
        assert call(port, "POST", "/v1/turn", EVIL, b"\0\0")[0] == 403
        assert call(port, "POST", "/v1/turn", {"Content-Length": "-5"})[0] == 400
        assert call(port, "GET", "/health")[0] == 200
        monkeypatch.setenv("CRISSA_LOOPBACK_GUARD", "0")
        assert call(port, "GET", "/health", EVIL)[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- C1 ----------------------------------------------------------------------------------------------------------


def test_seen_trim_keeps_the_newest():
    seen = eb.SeenTurns()
    for i in range(600):
        seen.add("k%d" % i)
    seen.trim(at=500, keep=300)
    assert len(seen) == 300
    assert "k599" in seen and "k300" in seen and "k299" not in seen and "k0" not in seen


def test_seen_keep_never_under_two_polls():
    assert eb.SEEN_KEEP >= 2 * eb._poll_n(eb.STT_TURNS)
    assert eb._poll_n("http://x/v1/turns?n=30&via=ear") == 30
    assert eb._poll_n("http://x/v1/turns") == 50


def test_seen_main_uses_the_ordered_record():
    src = SRC.read_text()
    main = src.split("\ndef main() -> int:")[1]
    assert "seen = new_seen()" in main and "seen = trim_seen(seen)" in main
    assert "set(list(seen)[-300:])" not in main


def test_seen_kill_is_the_old_set(monkeypatch):
    monkeypatch.setattr(eb, "SEEN_ORDERED", False)
    s = eb.new_seen()
    assert isinstance(s, set)
    s.update("k%d" % i for i in range(eb.SEEN_TRIM_AT + 1))
    assert len(eb.trim_seen(s)) == eb.SEEN_KEEP


# --- C2 ----------------------------------------------------------------------------------------------------------

UID = "alex"
USER = {"person_id": "alex", "stamp": {"person_id": "alex", "certain": True}}
GUEST = {"person_id": "sam", "stamp": {"person_id": "sam"}}
NOBODY = {"person_id": None, "stamp": {"source": "media_hold_sole"}}


@pytest.fixture()
def the_user(monkeypatch):
    monkeypatch.delenv("EAR_BRIDGE_JOIN_LEAST_TRUST", raising=False)
    monkeypatch.setattr(hear_gate, "_uid_now", lambda: UID)


def test_trust_rank(the_user):
    assert hear_gate.speaker_trust(USER, UID) == 2
    assert hear_gate.speaker_trust(GUEST, UID) == 1
    assert hear_gate.speaker_trust(NOBODY, UID) == 0
    assert hear_gate.speaker_trust(dict(USER, is_user=False), UID) == 0


def test_coalesce_guest_then_user_stays_guest(the_user):
    joined, m = eb.coalesce_followup(("unlock the", dict(GUEST, kind="session")), "door", dict(USER), vocative=False)
    assert joined == "unlock the door" and m["person_id"] == "sam" and m["stamp"]["person_id"] == "sam"


def test_coalesce_unnamed_then_user_stays_unnamed(the_user):
    _, m = eb.coalesce_followup(("open the", dict(NOBODY)), "garage", dict(USER), vocative=False)
    assert m["person_id"] is None


def test_coalesce_user_then_guest_drops_to_guest(the_user):
    _, m = eb.coalesce_followup(("turn off", dict(USER)), "the alarm", dict(GUEST), vocative=False)
    assert m["person_id"] == "sam"


def test_coalesce_user_then_user_keeps_user(the_user):
    _, m = eb.coalesce_followup(("turn off", dict(USER)), "the lights", dict(USER), vocative=False)
    assert m["person_id"] == "alex"


def test_no_claim_takes_the_later_part(the_user):
    _, m = eb.coalesce_followup(("see the", {"kind": "session"}), "feed", dict(USER), vocative=False)
    assert m["person_id"] == "alex"                                     # the earlier part claimed nobody


def test_gather_unstamped_later_part_demotes(the_user):
    meta = dict(USER, kind="hey_zoe")
    hear_gate.apply_join_speaker(meta, dict(meta), {"person_id": None, "stamp": None})
    assert meta["person_id"] is None


def test_coalesce_kill_is_the_later_part(the_user, monkeypatch):
    monkeypatch.setenv("EAR_BRIDGE_JOIN_LEAST_TRUST", "0")
    _, m = eb.coalesce_followup(("unlock the", dict(GUEST)), "door", dict(USER), vocative=False)
    assert m["person_id"] == "alex"


def test_speculate_restart_keeps_least_trusted(the_user, monkeypatch):
    monkeypatch.setattr(speculate, "on", lambda: True)
    rec = {"text": "unlock the", "meta": dict(GUEST), "committed": False, "cancelled": False, "bumped_to": None}
    monkeypatch.setitem(speculate._CUR, "rec", rec)
    got = speculate.restart("door", dict(USER), vocative=False, token=1)
    assert got is not None and got[0] == "unlock the door" and got[1]["person_id"] == "sam"


def test_gather_window_uses_the_join():
    src = SRC.read_text()
    gather = src.split("if gather_until and now <= gather_until:")[1].split("# Session follow-up")[0]
    assert "apply_join_speaker(meta" in gather and "join_least_trust_on()" in gather


# --- C3 ----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def pack_user(monkeypatch):
    monkeypatch.delenv("CRISSA_EAR_CANON_USER", raising=False)
    import crissa_ear.house_id as hid
    monkeypatch.setattr(hid, "user_id", lambda ttl=5.0: UID)


def test_paint_guard_fires_for_the_pack_user(pack_user, monkeypatch):
    monkeypatch.setattr(id_matrix.cfg, "IS_USER_GUARD", True)
    pid, src, note, sup = id_matrix.apply_is_user_paint_guard(UID, "sole_present", None, voice_usable=True)
    assert pid is None and sup is True and note == "uncertain_no_sole_user"
    pid, _, _, sup = id_matrix.apply_is_user_paint_guard("user", "sole_present", None, voice_usable=True)
    assert pid is None and sup is True                                 # the house's literal: as before
    pid, _, _, sup = id_matrix.apply_is_user_paint_guard("sam", "sole_present", None, voice_usable=True)
    assert pid == "sam" and not sup


def test_paint_guard_kill_is_the_literal(pack_user, monkeypatch):
    monkeypatch.setattr(id_matrix.cfg, "IS_USER_GUARD", True)
    monkeypatch.setenv("CRISSA_EAR_CANON_USER", "0")
    pid, _, _, sup = id_matrix.apply_is_user_paint_guard(UID, "sole_present", None, voice_usable=True)
    assert pid == UID and not sup


def test_user_column_and_other_lead(pack_user):
    assert id_matrix.user_key({"alex": 0.7, "sam": 0.3}) == "alex"
    assert id_matrix.user_key({"user": 0.7}) == "user"
    r = id_matrix.is_user_gate(user_sim=0.2, best_other_id="alex", best_other_sim=0.9)
    assert r.best_other_id is None                                      # the User is never her own "other"


def test_stamp_reads_the_users_column():
    src = (SRC.parents[1] / "crissa_ear" / "stamp.py").read_text()
    assert 'voice_sims.get("user", -1.0)' not in src
    assert "voice_sims.get(user_key(voice_sims), -1.0)" in src and 'if nm != "user":' not in src


# --- C4 ----------------------------------------------------------------------------------------------------------


def test_callout_with_her_name_is_not_her_echo(monkeypatch):
    monkeypatch.setattr(eb, "CALLOUT_WAKE_EXEMPT", True)
    assert eb.is_tool_callout_echo("One moment.") is True
    assert eb.is_tool_callout_echo("Crissa, one moment") is False
    assert eb.is_tool_callout_echo("Hey Crissa, one moment") is False
    monkeypatch.setattr(eb, "CALLOUT_WAKE_EXEMPT", False)
    assert eb.is_tool_callout_echo("Crissa, one moment") is True


# --- C5 ----------------------------------------------------------------------------------------------------------


class _FakeParec:
    def __init__(self, data: bytes, rc=1):
        self.stdout = io.BytesIO(data)
        self._rc = rc

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        pass

    def poll(self):
        return self._rc


@pytest.fixture()
def fake_mic(monkeypatch):
    monkeypatch.delenv("CRISSA_EAR_DEAD_MIC_RAISES", raising=False)
    monkeypatch.setattr(audio, "resolve_mic", lambda m="auto": "alsa_input.gone")
    monkeypatch.setattr(audio, "_capture_from", lambda d: d)
    monkeypatch.setattr(audio._capture, "pulse_available", lambda: True)

    def use(data: bytes, rc=1):
        monkeypatch.setattr(audio.subprocess, "Popen", lambda *a, **k: _FakeParec(data, rc))
    return use


def test_dead_recorder_raises(fake_mic):
    fake_mic(b"\0\0" * 100)
    with pytest.raises(audio.DeadRecorder) as e:
        audio.record_seconds(1.0, mic="gone")
    assert "alsa_input.gone" in str(e.value)
    fake_mic(b"")
    with pytest.raises(audio.DeadRecorder):
        audio.record_until_silence(2.0, mic="gone")


def test_full_window_is_fine(fake_mic):
    fake_mic(b"\0\0" * 16000, rc=None)
    assert audio.record_seconds(1.0, mic="x").shape == (16000,)


def test_dead_recorder_kill_pads(fake_mic, monkeypatch):
    monkeypatch.setenv("CRISSA_EAR_DEAD_MIC_RAISES", "0")
    fake_mic(b"")
    out = audio.record_seconds(1.0, mic="gone")
    assert out.shape == (16000,) and not np.any(out)


def test_ear_loop_sees_the_error_and_waits(fake_mic, monkeypatch):
    from crissa_ear import ear_loop
    from crissa_ear.config import Config

    fake_mic(b"")
    loop = ear_loop.EarLoop() if hasattr(ear_loop, "EarLoop") else ear_loop.get_ear_loop()
    waited = []
    monkeypatch.setattr(loop._stop, "wait", lambda s=None: waited.append(s))
    wav, err = loop._capture_frame(seconds=0.3, cfg=Config(), listen_lock=threading.Lock())
    assert wav is None and "recorder stopped" in err and waited == [0.3]


# --- C-lower ---------------------------------------------------------------------------------------------------


def test_real_lines_with_subscribe_or_www(monkeypatch):
    monkeypatch.delenv("CRISSA_EAR_GHOST_NARROW", raising=False)
    assert not quality.is_hallucination("Crissa, subscribe me to the newsletter")
    assert not quality.is_hallucination("Crissa, I want to subscribe to that")
    assert not quality.is_hallucination("go to www.example.com")
    for ghost in ("Please subscribe.", "Like and subscribe!", "Subscribe to my channel", "www.example.org",
                  "Subtitles by www.example.org", "Thanks for watching!"):
        assert quality.is_hallucination(ghost), ghost


def test_ghost_kill_is_the_substring_rule(monkeypatch):
    monkeypatch.setenv("CRISSA_EAR_GHOST_NARROW", "0")
    assert quality.is_hallucination("Crissa, subscribe me to the newsletter")
