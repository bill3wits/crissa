"""Law 1 (docs/VISIT-LAMP.md, user 2026-09-05/06): all AUTH requires
FaceFinder + FaceID. From Guest only a FaceFinder look mints the hold; a
door still, a ring / FOV snapshot, a soft ID or an Oracle seat guess may
refresh a hold FaceFinder minted, never open one.

Incident: docs/AUTH-FROM-GUEST-20260906.md (the Wall repo).
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "modules", "presence"))

from crissa_presence import auth_first as af, sot_proof, visit_lamp as vl
from crissa_presence.service import PresenceService


class _FakeCam:
    mode = "dir"
    last_error = None

    def grab(self):
        return None

    def grab_buffer(self, **kw):
        return []


def _svc(tmp_path, monkeypatch):
    monkeypatch.setenv("RAIL_FACEID_LOG", str(tmp_path / "faceid"))
    monkeypatch.setenv("RAIL_STRANGER_ROOT", str(tmp_path / "strangers"))
    monkeypatch.setenv("RAIL_VISIT_LAMP", str(tmp_path / "visit_lamp.json"))
    monkeypatch.setenv("RAIL_STORE_FACE_AUDIT", str(tmp_path / "store_face.jsonl"))
    monkeypatch.setenv("HOUSE_USER_ID", "user")
    monkeypatch.setattr(sot_proof, "REV_PATH", tmp_path / "sot_rev.json")
    monkeypatch.setattr(sot_proof, "AUTH_LOG", tmp_path / "auth.jsonl")
    monkeypatch.setattr(vl, "LAMP_PATH", tmp_path / "visit_lamp.json")
    monkeypatch.setattr(vl, "TOKEN_PATH", tmp_path / "secure_token.json")
    monkeypatch.setattr(vl, "SECURITY_AUDIT", tmp_path / "store_face.jsonl")
    (tmp_path / "faceid").mkdir()
    svc = PresenceService(camera=_FakeCam())
    svc.last_grabber = {}
    monkeypatch.setattr(svc, "faceid_hud", lambda: {})
    monkeypatch.setattr(svc, "_visit_lamp_hold_door", lambda: None)
    monkeypatch.setattr(svc, "_beam_rest", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_ensure_door_aim", lambda **k: {"ok": True})
    monkeypatch.setattr(svc, "_maybe_first_auth_greeting", lambda *a, **k: None)
    monkeypatch.setattr(af, "host_lock_state", lambda: None)
    svc._face_auth_hold.clear()
    svc._user_session = None
    return svc


def _auth(svc) -> dict:
    return svc._user_auth_from_slots([], lights_out=False)


def test_drop_to_guest_clears_auth_even_when_lamp_already_amber(tmp_path, monkeypatch):
    """_visit_secure_off is a no-op if already insecure; restart still guests."""
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.82, source="desk_ff")
    assert "user" in svc._face_auth_hold
    svc._visit_secure = False
    assert svc._visit_secure_off(reason="restart") is False
    assert "user" in svc._face_auth_hold
    out = svc.drop_to_guest(reason="occupancy:render")
    assert out["ok"] and out["user_auth"] is False and out["secure"] is False
    assert "user" not in svc._face_auth_hold
    assert svc._user_session is None
    lamp = json.loads((tmp_path / "visit_lamp.json").read_text())
    assert lamp.get("secure") is False and lamp.get("hold") is False


NOT_FF = [
    "door_buffer",
    "blind_gap_door",
    "entry_best_face",
    "reauth_r0_live",
    "door_chase_ring",
    "soft_id:voice",
    "at_door_certain",
    "live_face",
    "seat_6",
    "door_face",
]


@pytest.mark.parametrize("source", NOT_FF)
def test_guest_stays_guest_without_facefinder(tmp_path, monkeypatch, source, capsys):
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.91, source=source)
    assert "user" not in svc._face_auth_hold
    blk = _auth(svc)
    assert not blk["user_auth"] and blk["user_auth_reason"] == "user_absent"
    assert blk["user_face_auth_source"] is None
    out = capsys.readouterr().out
    assert "[faceid] refuse guest_needs_ff user" in out and f"src={source}" in out


@pytest.mark.parametrize(
    "source",
    ["visit_lamp_click", "beam_look", "desk_ff", "reauth_ladder_ff", "unlock_ff", "door_chase_ff"],
)
def test_facefinder_mints_from_guest(tmp_path, monkeypatch, source, capsys):
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.77, source=source)
    assert svc._face_auth_hold["user"]["source"] == source
    blk = _auth(svc)
    assert blk["user_auth"] and blk["user_face_auth_source"] == source
    assert "[faceid] AUTH grant user" in capsys.readouterr().out


def test_refresh_keeps_the_minting_source(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.77, source="visit_lamp_click")
    t0 = svc._face_auth_hold["user"]["ts"]
    svc._note_face_auth("user", sim=0.66, source="door_buffer")
    h = svc._face_auth_hold["user"]
    assert h["source"] == "visit_lamp_click"  # how AUTH was minted
    assert h["seen_via"] == "door_buffer" and h["ts"] >= t0  # BAU FF tracking
    assert _auth(svc)["user_auth"]


def test_grant_api_is_not_a_way_around_law_1(tmp_path, monkeypatch, capsys):
    """PR A (2026-09-09, review item 1): grant_user_face_auth used to pass
    grant=True for any source, so the door chase minted AUTH from Guest off
    three stills of the current FOV. The grant vouches only for FaceFinder."""
    svc = _svc(tmp_path, monkeypatch)
    out = svc.grant_user_face_auth(sim=0.8, source="door_chase_ring", greet=False)
    assert out.get("auth") is False and "user" not in svc._face_auth_hold
    assert "[faceid] refuse guest_needs_ff user" in capsys.readouterr().out
    out = svc.grant_user_face_auth(sim=0.8, source="door_chase_ff", greet=False)
    assert out.get("auth") is True and "user" in svc._face_auth_hold
    assert svc._face_auth_hold["user"]["source"] == "door_chase_ff"
    # held: a ring match now refreshes, and the minting source stays
    svc.grant_user_face_auth(sim=0.7, source="door_chase_ring", greet=False)
    assert svc._face_auth_hold["user"]["source"] == "door_chase_ff"
    assert svc._face_auth_hold["user"]["seen_via"] == "door_chase_ring"


def test_chase_ring_match_from_guest_stays_guest(tmp_path, monkeypatch, capsys):
    """The chase itself: a ring hit (via=door_ring) from Guest is found but
    not AUTH — every ensure_user_auth caller (rail_stt command, PIP join,
    joystick join) goes through here in door_local mode."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_match_user_in_jpegs", lambda uid, snaps, source: 0.91)
    out = svc.chase_user_face_at_door(jpegs=[b"jpeg"], allow_door_ff=False)
    assert out["found"] is True and out["via"] == "door_ring"
    assert out["auth"] is False and out.get("reason") == "door_ring_needs_ff"
    assert "user" not in svc._face_auth_hold
    blk = _auth(svc)
    assert blk["user_auth"] is False
    assert "refuse guest_needs_ff user" in capsys.readouterr().out
    # once FaceFinder minted it, the same ring match refreshes the hold
    svc._note_face_auth("user", sim=0.8, source="desk_ff")
    ts0 = svc._face_auth_hold["user"]["ts"]
    out = svc.chase_user_face_at_door(jpegs=[b"jpeg"], allow_door_ff=False)
    assert out["auth"] is True
    assert svc._face_auth_hold["user"]["ts"] >= ts0
    assert svc._face_auth_hold["user"]["source"] == "desk_ff"


def test_roster_face_is_not_a_second_auth(tmp_path, monkeypatch):
    """A certain door face on the roster slot does not AUTH the User; the
    hold does (one object)."""
    from types import SimpleNamespace

    svc = _svc(tmp_path, monkeypatch)
    svc._id_sources["user"] = {"face": True, "door_buffer": True, "face_sim": 0.9}
    svc._id_certain["user"] = True
    a = svc._auth_for_person("user", sources=dict(svc._id_sources["user"]), certain=True)
    assert not a["authed"] and a["sources"]["face"] is False
    assert "user" not in svc._face_auth_hold
    p = SimpleNamespace(name="user", certain=True, sources=dict(svc._id_sources["user"]), auth=a)
    blk = svc._user_auth_from_slots([p], lights_out=False)
    assert not blk["user_auth"]


def test_ladder_from_guest_facefinders_the_seat(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    calls: list = []

    def _desk(uid, *, force=False, owner=""):
        calls.append(("desk", force, owner))
        return {"found": True, "best_sim": 0.81, "via": "desk_aim"}

    def _chase(**kw):
        raise AssertionError("ring / door chase is not a Guest grant")

    monkeypatch.setattr(svc, "_desk_look_once", _desk)
    monkeypatch.setattr(svc, "chase_user_face_at_door", _chase)
    monkeypatch.setattr(svc, "_rung0_current_fov", lambda uid: pytest.fail("rung0"))
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    svc._run_reauth_ladder("user")
    assert calls == [("desk", True, "reauth_ladder")]
    assert svc._face_auth_hold["user"]["source"] == "reauth_ladder_ff"
    blk = _auth(svc)
    assert blk["user_auth"] and blk["user_auth_reason"] != "ok"  # no forged voice


def test_ladder_miss_stays_guest(tmp_path, monkeypatch, capsys):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        svc, "_desk_look_once", lambda uid, **k: {"found": False, "best_sim": 0.2, "via": "desk"}
    )
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    svc._run_reauth_ladder("user")
    assert "user" not in svc._face_auth_hold
    assert "[faceid] ladder miss user" in capsys.readouterr().out


def test_ladder_does_not_leave_the_door_while_secure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    monkeypatch.setattr(svc, "_visit_is_secure", lambda: True)
    monkeypatch.setattr(svc, "_desk_look_once", lambda *a, **k: pytest.fail("looked"))
    svc._run_reauth_ladder("user")
    assert "user" not in svc._face_auth_hold


def test_ensure_snapshot_match_is_not_found_from_guest(tmp_path, monkeypatch):
    """ensure_user_auth: FaceID saw him in a current-FOV grab, the minter
    refused (Guest, no FaceFinder) → not found, no AUTH."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_auth_refused_lights_out", lambda: False)
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    monkeypatch.setattr(svc, "_door_work_pending", lambda: False)
    monkeypatch.setattr(svc, "_current_auth_tier", lambda **k: "stale")
    monkeypatch.setattr(
        svc, "_rung0_current_fov", lambda uid: {"found": True, "best_sim": 0.9, "via": "live"}
    )
    monkeypatch.setattr(svc, "chase_user_face_at_door", lambda **k: {"found": False})
    monkeypatch.setattr(svc, "_someone_talking", lambda **k: False)
    svc._ensure_auth_hunt_ts = 0.0
    out = svc.ensure_user_auth(person_id="user", reason="command")
    assert not out["auth"] and "user" not in svc._face_auth_hold


def test_secure_off_drops_the_source(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.8, source="visit_lamp_click")
    svc._visit_secure_on(reason="click")
    assert _auth(svc)["user_face_auth_source"] == "visit_lamp_click"
    svc._visit_secure_off(reason="entry_face")
    st = svc.get()
    assert not st.user_auth and st.user_face_auth_source is None
    assert "user" not in svc._face_auth_hold
    assert _auth(svc)["user_face_auth_source"] is None


def test_ledger_row_names_the_minter(tmp_path, monkeypatch):
    from crissa_presence.schema import RoomPresence

    st = RoomPresence(user_auth=True, user_face_auth_source="beam_look")
    row = sot_proof.auth_row(
        prev_auth=False, prev_reason="user_absent", st=st, writer={"who": "rail_stt", "why": "user_voice"}, rev=1, now=0.0
    )
    assert row["source"] == "beam_look"
    assert st.to_dict()["user"]["face_auth_source"] == "beam_look"


def test_is_ff_source():
    assert af.is_ff_source("visit_lamp_click")
    assert af.is_ff_source("secure_token")
    assert af.is_ff_source("restart_token")
    assert af.is_ff_source("desk_ff")
    assert af.is_ff_source("facefind:desk")
    assert not af.is_ff_source("door_buffer")
    assert not af.is_ff_source("soft_id:voice")
    assert not af.is_ff_source("reauth_r0_live")
    assert not af.is_ff_source("")


def test_click_sages_writes_a_token(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(svc, "_visit_camera_blocked", lambda **k: None)
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": True, "via": "desk_aim", "best_sim": 0.88},
    )
    out = svc.visit_lamp_click()
    assert out.get("ok") is True
    assert svc._visit_is_secure() is True
    tok = vl.load_token()
    assert tok and tok["uid"] == "user" and tok["id"]


def test_token_ff_hit_turns_the_button_sage(tmp_path, monkeypatch):
    """Restart inherit: FaceFinder hit → sage, not AUTH-without-sage."""
    from crissa_presence import secure_token as stok

    svc = _svc(tmp_path, monkeypatch)
    vl.save_token(stok.mint("user", now=1.0, reason="click", hold=False, token_id="abc"))
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": True, "via": "desk_aim", "best_sim": 0.86, "returned_door": True},
    )
    out = svc.visit_secure_from_token(reason="restart_token")
    assert out.get("ok") and out.get("via") == "token_ff"
    assert svc._visit_is_secure() is True
    assert svc.get().visit_lamp == "sage"
    assert "user" in svc._face_auth_hold
    assert svc._guest_until_secure is False
    assert vl.load_token()["id"]  # handed to the next bounce


def test_token_hold_never_returns_without_the_password(tmp_path, monkeypatch):
    """PR C (ruling 2026-09-09, BUTTON-MODE-MATRIX 2.4): a token with
    hold:true + a FaceFinder hit restores sage Secure, never keyboard Hold."""
    from crissa_presence import secure_token as stok

    svc = _svc(tmp_path, monkeypatch)
    vl.save_token(stok.mint("user", now=1.0, reason="click", hold=True, token_id="abc"))
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": True, "via": "desk_aim", "best_sim": 0.86, "returned_door": True},
    )
    out = svc.visit_secure_from_token(reason="restart_token")
    assert out.get("ok") and out.get("via") == "token_ff"
    assert out.get("token_hold") is True and out.get("hold") is False
    assert svc._visit_is_secure() is True
    assert getattr(svc, "_visit_hold", False) is False, "Hold is this process, this click, this password"
    assert svc.get().visit_lamp == "sage"


def test_token_ff_miss_cuts_secure_off(tmp_path, monkeypatch):
    from crissa_presence import secure_token as stok

    svc = _svc(tmp_path, monkeypatch)
    vl.save_token(stok.mint("user", now=1.0, reason="click"))
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "via": "desk_aim", "best_sim": 0.1, "n_faces": 0},
    )
    out = svc.visit_secure_from_token(reason="restart_token")
    assert out.get("via") == "token_miss"
    assert svc._visit_is_secure() is False
    assert svc.get().visit_lamp == "amber"
    assert "user" not in svc._face_auth_hold
    assert vl.load_token() is None


def test_token_ff_leased_does_not_burn(tmp_path, monkeypatch):
    from crissa_presence import secure_token as stok

    svc = _svc(tmp_path, monkeypatch)
    vl.save_token(stok.mint("user", now=1.0, reason="click", token_id="keepme"))
    monkeypatch.setattr(
        svc,
        "_desk_look_once",
        lambda uid, **k: {"found": False, "skipped": "leased"},
    )
    out = svc.visit_secure_from_token()
    assert out.get("deferred") is True
    assert vl.load_token()["id"] == "keepme"
    assert svc._visit_is_secure() is False


def test_occupancy_keeps_live_secure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_secure_on(reason="click")
    monkeypatch.setattr(svc, "_desk_look_once", lambda *a, **k: pytest.fail("looked"))
    out = svc.visit_secure_reauth(reason="occupancy:render")
    assert out.get("via") == "live" and out.get("inherited") is True
    assert svc._visit_is_secure() is True


def test_render_to_normal_with_sage_on_keeps_secure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._visit_secure_on(reason="click")
    tok = vl.load_token()["id"]
    monkeypatch.setattr(svc, "_desk_look_once", lambda *a, **k: pytest.fail("looked"))
    monkeypatch.setattr(svc, "drop_to_guest", lambda **k: pytest.fail("dropped"))
    for reason in ("occupancy:render", "occupancy:normal"):
        out = svc.visit_secure_reauth(reason=reason)
        assert out.get("via") == "live" and out.get("inherited") is True
        assert svc._visit_is_secure() is True
        assert svc._guest_until_secure is False
    assert vl.load_token()["id"] == tok


def test_occupancy_with_sage_off_does_not_latch_guest(tmp_path, monkeypatch):
    from crissa_presence import secure_token as stok

    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.8, source="desk_ff")
    before = _auth(svc)
    assert before["user_auth"] is True
    vl.save_token(stok.mint("user", now=1.0, reason="click", token_id="keep"))
    monkeypatch.setattr(svc, "_desk_look_once", lambda *a, **k: pytest.fail("looked"))
    monkeypatch.setattr(svc, "visit_secure_from_token", lambda **k: pytest.fail("facefinder"))
    monkeypatch.setattr(svc, "drop_to_guest", lambda **k: pytest.fail("dropped"))
    for reason in ("occupancy:render", "occupancy:normal"):
        out = svc.visit_secure_reauth(reason=reason)
        assert out.get("via") == "occupancy" and out.get("checked") is False
    assert svc._visit_is_secure() is False
    assert svc._guest_until_secure is False
    assert "user" in svc._face_auth_hold
    assert _auth(svc)["user_auth"] is True
    assert _auth(svc)["user_auth_reason"] == before["user_auth_reason"]
    assert vl.load_token()["id"] == "keep"


def test_entry_face_still_turns_secure_off(tmp_path, monkeypatch):
    import inspect

    svc = _svc(tmp_path, monkeypatch)
    svc._visit_secure_on(reason="click")
    svc.visit_secure_reauth(reason="occupancy:render")
    svc.visit_secure_reauth(reason="occupancy:normal")
    assert svc._visit_is_secure() is True
    assert '"entry_face"' in inspect.getsource(svc._visit_door_tripwire)
    assert svc._visit_secure_off(reason="entry_face") is True
    assert svc._visit_is_secure() is False
    assert vl.load_token() is None


def test_restart_reauth_still_guests_without_a_token(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.8, source="desk_ff")
    out = svc.visit_secure_reauth(reason="restart")
    assert out.get("user_auth") is False
    assert "user" not in svc._face_auth_hold
    assert svc._guest_until_secure is True


def test_restart_drop_keeps_token_for_the_next_ff(tmp_path, monkeypatch):
    from crissa_presence import secure_token as stok

    svc = _svc(tmp_path, monkeypatch)
    vl.save_token(stok.mint("user", now=1.0, reason="click", token_id="ride"))
    svc._note_face_auth("user", sim=0.8, source="visit_lamp_click")
    svc.drop_to_guest(reason="restart")
    assert "user" not in svc._face_auth_hold
    assert vl.load_token()["id"] == "ride"
    assert svc._guest_until_secure is True


def test_entry_face_burns_the_token(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._note_face_auth("user", sim=0.8, source="visit_lamp_click")
    svc._visit_secure_on(reason="click")
    assert vl.load_token()
    assert svc._visit_secure_off(reason="entry_face") is True
    assert vl.load_token() is None
    assert svc._visit_is_secure() is False


def test_ladder_skips_while_guest_until_secure(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc.drop_to_guest(reason="restart")
    monkeypatch.setattr(svc, "_auth_hunt_blocked_locked", lambda: False)
    monkeypatch.setattr(svc, "_desk_look_once", lambda *a, **k: pytest.fail("looked"))
    svc._run_reauth_ladder("user")
    assert "user" not in svc._face_auth_hold


def test_facefinder_mints_after_restart_guest(tmp_path, monkeypatch, capsys):
    """Guest = FF-for-AUTH. Restart drops sage; a look still mints User."""
    svc = _svc(tmp_path, monkeypatch)
    svc.drop_to_guest(reason="restart")
    assert svc._guest_until_secure is True
    svc._note_face_auth("user", sim=0.86, source="beam_look")
    assert svc._face_auth_hold["user"]["source"] == "beam_look"
    assert svc._visit_is_secure() is False
    assert "[faceid] AUTH grant user" in capsys.readouterr().out
    assert _auth(svc)["user_auth"] is True


def test_desk_ff_mints_after_restart_guest(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc.drop_to_guest(reason="restart")
    svc._note_face_auth("user", sim=0.86, source="desk_ff")
    assert "user" in svc._face_auth_hold
    assert svc._visit_is_secure() is False


def test_door_buffer_refused_after_restart_guest(tmp_path, monkeypatch, capsys):
    svc = _svc(tmp_path, monkeypatch)
    svc.drop_to_guest(reason="restart")
    svc._note_face_auth("user", sim=0.86, source="door_buffer")
    assert "user" not in svc._face_auth_hold
    out = capsys.readouterr().out
    assert "refuse guest_until_secure" in out and "src=door_buffer" in out


def test_visor_scan_during_look_grants_auth(tmp_path, monkeypatch):
    """Cam 100 during FF must publish user_auth, not wait for the look to home."""
    svc = _svc(tmp_path, monkeypatch)
    svc.drop_to_guest(reason="restart")
    svc._f2f = True
    seen = {"names": {"user": 0.81}, "n_faces": 1}
    assert svc._grant_user_from_look(seen, source="beam_look") is True
    assert "user" in svc._face_auth_hold
    assert svc.get().user_auth is True
    assert svc._visit_is_secure() is False


def test_visor_scan_idle_door_does_not_grant(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._f2f = False
    svc._f2f_looking = False
    seen = {"names": {"user": 0.81}, "n_faces": 1}
    assert svc._grant_user_from_look(seen, source="beam_look") is False
    assert "user" not in svc._face_auth_hold
