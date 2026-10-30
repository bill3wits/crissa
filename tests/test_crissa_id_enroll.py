"""The id module's enrol tools, as the house tested them (house-id's six test files, ported 2026-10-09).

Same assertions, two seams changed on copy: the package is crissa_id.house_enroll (its paths come from the state
home, never a records volume or a code tree), and the engine family is the pack's. The household-name scan stays
on the house (it reads a live people index).
"""
from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules" / "id"))
sys.path.insert(0, str(ROOT / "lib"))

from crissa_id import paths as idpaths  # noqa: E402
from crissa_id.house_enroll import coach, cues, local_values, paths, person, register_camera  # noqa: E402
from crissa_id.house_enroll import voice_register as vr  # noqa: E402
from crissa_id.house_enroll.import_clips import ensure_inbox_scaffold, inbox_readme  # noqa: E402
from crissa_id.house_enroll.room_frame import program_room_globals  # noqa: E402

HERE = ROOT / "modules" / "id" / "crissa_id" / "house_enroll"
SEEED = "alsa_input.usb-beam-array.analog-stereo"


# ---- the coach says which Register it runs (test_coach_health) ----------------------------------------------------
def test_health_says_the_token_path(monkeypatch):
    monkeypatch.setenv("HOUSE_REGISTER_TOKEN", "1")
    assert coach.health_body() == {"ok": True, "service": "house-enroll-coach", "register_token": True}


def test_health_says_the_legacy_path(monkeypatch):
    monkeypatch.delenv("HOUSE_REGISTER_TOKEN", raising=False)
    assert coach.health_body()["register_token"] is False


# ---- Register enrols on the mic the ear matches on (test_enroll_mic) ----------------------------------------------
def _site(mic=None):
    return SimpleNamespace(raw={"enroll": ({"mic": mic} if mic is not None else {})})


def test_unset_is_todays_behaviour():
    assert vr.enroll_mic(_site(), ear=lambda: SEEED, exists=lambda n: True) == ""


def test_ear_means_the_ears_own_source():
    assert vr.enroll_mic(_site("ear"), ear=lambda: SEEED, exists=lambda n: n == SEEED) == SEEED


def test_a_named_source_is_used_only_if_it_exists():
    assert vr.enroll_mic(_site("my-mic"), ear=lambda: "", exists=lambda n: n == "my-mic") == "my-mic"
    assert vr.enroll_mic(_site("gone-mic"), ear=lambda: "", exists=lambda n: False) == ""


def test_ear_with_no_ear_falls_back():
    assert vr.enroll_mic(_site("ear"), ear=lambda: "", exists=lambda n: True) == ""


# ---- Init step 5, the pack's side (test_init_blockers) -----------------------------------------------------------
def test_rails_root_is_this_install_and_the_variable_wins(monkeypatch):
    monkeypatch.delenv("HOUSE_RAILS_ROOT", raising=False)
    assert paths.rails_root() == idpaths.PACK == ROOT                 # the pack: never a path beside the home directory
    monkeypatch.setenv("HOUSE_RAILS_ROOT", "/opt/rails")
    assert paths.rails_root() == Path("/opt/rails") and paths.rails_path("lib") == Path("/opt/rails/lib")


def test_register_initials_never_crash_without_the_wall_lib(monkeypatch):
    monkeypatch.setenv("HOUSE_ID_RAILS_CONFIG", "1")
    monkeypatch.setitem(sys.modules, "vo_sound", None)
    assert person._glass_initials("Ada Lovelace", kind="user") == "AL"


def test_no_hardcoded_house_path_left():
    for f in sorted(HERE.glob("*.py")):
        if f.name == "paths.py":
            continue
        code = [l for l in f.read_text().splitlines() if not l.lstrip().startswith("#")]
        hits = [l for l in code if ('"house-rails"' in l or "projects/house-rails/" in l or "/data/" in l
                                    or '"/home/' in l) and "print(" not in l]
        assert not hits, (f.name, hits)


def test_the_desk_mic_name_is_the_houses_and_none_is_a_plain_refusal():
    src = (HERE / "voice_register.py").read_text()
    code = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
    assert not [l for l in code if 'pulse_source_named("' in l], "a device name in code"
    assert 'local("desk_mic_hint", "")' in src and "HOUSE_ENROLL_MIC_REQUIRED" in src
    assert "No microphone is chosen for Register. Pick one in Init" in src


def test_one_identity_root_under_the_state_home_never_the_code_tree(monkeypatch, tmp_path):
    monkeypatch.delenv("HOUSE_ID_ROOT", raising=False)
    monkeypatch.delenv("CRISSA_IDENTITY", raising=False)
    monkeypatch.setattr(idpaths, "STATE", tmp_path / "home")
    assert paths.house_id_root() == tmp_path / "home" / "identity"
    assert not str(paths.house_id_root()).startswith(str(ROOT))
    monkeypatch.setenv("CRISSA_IDENTITY", str(tmp_path / "store"))
    assert paths.house_id_root() == tmp_path / "store"
    monkeypatch.delenv("CRISSA_IDENTITY", raising=False)
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path / "theirs"))
    assert paths.house_id_root() == tmp_path / "theirs"                 # the house's variable still wins
    assert paths.site_example_path(tmp_path / "theirs") == ROOT / "modules" / "id" / "config" / "site.example.json"


# ---- Init programs geometry + sitters the rails read (test_room_frame) -------------------------------------------
def test_program_writes_geometry_and_sitters(tmp_path, monkeypatch):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"schema": "house_site/v1", "enroll": {"user_id": "user_a"}}))
    geom = tmp_path / "room_geometry.json"
    stops = tmp_path / "camera-stops.json"
    stops.write_text(json.dumps({
        "door": {"pan": 450000, "tilt": 0, "zoom": 150},
        "stops": [{"id": "desk", "pan": 0, "station": 99}, {"id": "regular_a_seat", "pan": 225000, "station": 99},
                  {"id": "stand_11", "pan": 515000, "station": 99}, {"id": "door", "pan": 450000, "station": 99}]}))
    monkeypatch.setenv("ROOM_GEOMETRY", str(geom))
    monkeypatch.setenv("RAIL_CAMERA_STOPS", str(stops))
    local = tmp_path / "house-local.json"
    local.write_text(json.dumps({"regular_stop_id": "regular_a_seat"}))
    monkeypatch.setenv("HOUSE_ID_LOCAL", str(local))
    monkeypatch.delenv("HOUSE_ID_GENERIC_FALLBACKS", raising=False)
    monkeypatch.setattr("crissa_id.house_enroll.room_frame.site_config_path", lambda root=None: site)
    # the live camera's own pan never reaches a test (the house read v4l2 here; its gimbal happened to sit at 450000)
    monkeypatch.setattr("crissa_id.house_enroll.room_frame._read_live_door_pose",
                        lambda: {"pan": 450000, "tilt": 0, "zoom": 150})
    out = program_room_globals(
        tmp_path, door=10, camera_door=10, media=7, furniture=[], stand=[9, 11], down=[], seats=[12],
        wall=[1, 2, 3, 4, 5], user_hour=6,
        regulars=[{"person_id": "user_a", "kind": "user", "home_beam": 6},
                  {"person_id": "regular_a", "kind": "regular", "home_beam": 8}],
        visitors=[{"person_id": "guest_a", "kind": "guest"}, {"person_id": "guest_b", "kind": "guest", "home_beam": 11}],
        user_id="user_a")
    assert out["door"] == 10 and out["heading_door"] == 10 and out["user"] == 6
    assert {6, 8, 9, 10, 11, 12} <= set(out["legal"]) and 4 in out["blocked"] and 7 in out["blocked"]
    g = json.loads(geom.read_text())
    assert g["user_seat"] == 6 and g["media_station"] == 7 and g["blanked_sit"] == [7] and g["multi_ok"] == [10]
    assert g["heading"]["door_hour"] == 10 and g["heading"]["door_az_deg"] == 300.0 and g["heading"]["twelve"] == "monitor"
    assert g["camera_door"] == 10 and g["heading"]["door_home_pan"] == 450000 and g["wall_hours"] == [1, 2, 3, 4, 5]
    st = json.loads(site.read_text())["stations"]
    assert st["schema"] == "site_stations/v2" and st["door_beam"] == 10 and st["heading"]["door_hour"] == 10
    assert st["sitters"]["6"]["usual"] == "user_a" and st["sitters"]["7"]["role"] == "media"
    assert st["sitters"]["8"]["usual"] == "regular_a" and st["sitters"]["9"]["role"] == "stand"
    assert st["sitters"]["10"]["role"] == "door_line" and st["sitters"]["12"]["role"] == "seat"
    assert st["guests_pool"] == ["guest_a", "guest_b"] and 11 in st["guest_beams"]
    by = {s["id"]: s["station"] for s in json.loads(stops.read_text())["stops"]}
    assert by == {"desk": 6, "regular_a_seat": 8, "door": 10, "stand_11": 11}


def test_explain_teaches_usb_twelve_and_final_orientation():
    from contextlib import redirect_stdout

    from crissa_id.house_enroll.room_frame import explain
    buf = io.StringIO()
    with redirect_stdout(buf):
        explain()
    text = buf.getvalue().lower()
    assert "usb" in text and "12" in text and "top" in text and "door" in text and "6" in text
    assert "remap" in text or "remumber" in text


def test_ask_user_yes_keeps_name(tmp_path, monkeypatch):
    from crissa_id.house_enroll.room_frame import ask_user
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"schema": "house_site/v1", "enroll": {"user_id": "user_a"},
                                "stations": {"user_display": "User A"}}))
    monkeypatch.setattr("crissa_id.house_enroll.room_frame.site_config_path", lambda root=None: site)
    who = ask_user(tmp_path, yes=True)
    assert who["user_id"] == "user_a" and who["display"] == "User A" and who["user_beam"] == 6
    assert json.loads(site.read_text())["enroll"]["user_id"] == "user_a"


# ---- Register without the lamp (test_register_no_lamp) -----------------------------------------------------------
def test_register_names_no_lamp():
    for f in ("voice_register.py", "register_camera.py"):
        assert "lamp" not in (HERE / f).read_text().lower(), f"{f} names the lamp"


@pytest.fixture
def calls(monkeypatch):
    posted: list = []

    def post(url, body, timeout=12.0):
        posted.append((url, dict(body)))
        if url.endswith("/v1/camera/register") and body.get("op") == "take":
            return {"ok": True, "lease_id": "Ltok", "act": "grant"}
        if url.endswith("/v1/lease"):
            return {"ok": True, "lease_id": "Lraw"}
        return {"ok": True}

    monkeypatch.setattr(vr, "_post", post)
    for name in ("release_ear_for_enroll", "_aim_verified", "_loop_enter_host", "_host_warm"):
        monkeypatch.setattr(vr, name, lambda *a, **k: None)
    monkeypatch.setattr(vr, "_hold_take", lambda: True)
    ff: list = []
    monkeypatch.setattr(vr, "_ff_set", lambda lid, on: ff.append((lid, on)))
    monkeypatch.setattr(vr, "DOOR_WATCH_SETTLE_SEC", 0.0)
    return posted, ff


def test_on_prearm_takes_the_token_and_never_touches_secure(monkeypatch, calls):
    posted, ff = calls
    monkeypatch.setenv(register_camera.SWITCH, "1")
    monkeypatch.setattr(vr, "_drop_secure", lambda: pytest.fail("the lamp was touched"))
    sys.modules.pop("crissa_id.house_enroll.secure_legacy", None)
    out = vr.prearm(site=None, write_marker=False)
    urls = [u for u, _ in posted]
    assert out["token"] is True and out["lease_id"] == "Ltok"
    assert urls == [f"{vr.PRESENCE}/v1/camera/register"]
    assert ff == [("Ltok", True)]
    assert "crissa_id.house_enroll.secure_legacy" not in sys.modules


def test_off_prearm_is_the_old_path(monkeypatch, calls):
    posted, _ = calls
    monkeypatch.delenv(register_camera.SWITCH, raising=False)
    dropped: list = []
    monkeypatch.setattr(vr, "_drop_secure", lambda: dropped.append(1) or {"ok": True})
    out = vr.prearm(site=None, write_marker=False)
    urls = [u for u, _ in posted]
    assert dropped == [1] and out["lease_id"] == "Lraw"
    assert f"{vr.CAMERA}/v1/pause" in urls and f"{vr.CAMERA}/v1/lease" in urls
    assert not any(u.endswith("/v1/camera/register") for u in urls)


def test_on_outside_user_mode_register_refuses_and_never_runs_facefinder_for_auth(monkeypatch, calls):
    posted, _ = calls
    monkeypatch.setenv(register_camera.SWITCH, "1")
    monkeypatch.setattr(register_camera, "user_mode", lambda presence: False)
    monkeypatch.setattr(vr, "_auth_desk_like_secure", lambda *a, **k: pytest.fail("FaceFinder auth ran"))
    monkeypatch.setattr(vr, "_prearm_read", lambda: {})
    said: list = []
    heard: list = []
    monkeypatch.setattr(vr, "_fail_register", lambda text: said.append(text))
    monkeypatch.setattr(vr.cues, "speak_announcer", lambda text: heard.append(text) or True)
    out = vr._run_wall(site=None, person_id="user", name="the User", line="x", turn_line="y",
                       station="desk", room_mic="")
    assert out == [] and said == [register_camera.REFUSE_TEXT]
    assert heard == ["Register needs User auth first."]
    assert posted == []
    assert vr._user_mode_gate() == {"ok": False, "error": register_camera.REFUSE_TEXT}


def test_in_user_mode_there_is_no_second_check(monkeypatch):
    monkeypatch.setattr(register_camera, "user_mode", lambda presence: True)
    monkeypatch.setattr(vr, "_auth_desk_like_secure", lambda *a, **k: pytest.fail("a second check ran"))
    assert vr._user_mode_gate() == {"ok": True, "via": "user_mode"}


def test_both_routines_use_the_gate_and_give_the_token_back():
    src = (HERE / "voice_register.py").read_text()
    assert src.count("_user_mode_gate(site, person_id)") >= 2      # round 20: the gate knows whose take it is
    assert src.count("register_camera.give_back(PRESENCE, _post)") >= 3


def test_the_user_mode_read_is_presence_auth_only(monkeypatch):
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(register_camera.urllib.request, "urlopen",
                        lambda url, timeout=3: R(json.dumps({"user_auth": True}).encode()))
    assert register_camera.user_mode("http://p") is True
    monkeypatch.setattr(register_camera.urllib.request, "urlopen",
                        lambda url, timeout=3: (_ for _ in ()).throw(OSError()))
    assert register_camera.user_mode("http://p") is False


def test_first_boot_with_no_user_enrolled_goes_and_a_guest_with_one_does_not(monkeypatch, tmp_path):
    (tmp_path / "people" / "someone").mkdir(parents=True)
    pj = tmp_path / "people" / "someone" / "person.json"
    site = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(register_camera, "user_mode", lambda presence: False)
    pj.write_text(json.dumps({"role": "guest"}))
    assert vr._user_mode_gate(site) == {"ok": True, "via": "first_user"}
    pj.write_text(json.dumps({"role": "user"}))
    assert vr._user_mode_gate(site) == {"ok": False, "error": register_camera.REFUSE_TEXT}


def test_the_deny_sounds_like_the_house_announcer(monkeypatch):
    calls: list = []
    ha = types.ModuleType("house_announce")
    ha.SPEED = 1.2
    ha.synth_audio8 = lambda text: (calls.append(("synth", text)), b"x" * 600)[1]
    ha.boost_pa_wav = lambda wav: (calls.append(("boost", len(wav))), wav)[1]
    ha._paplay = lambda path, payload=None: (calls.append(("play", path.endswith(".sped.wav"))), (True, "sink"))[1]
    monkeypatch.setitem(sys.modules, "house_announce", ha)
    monkeypatch.setattr(cues, "audio_stopped", lambda: False)
    monkeypatch.setattr(cues.subprocess, "run",
                        lambda cmd, **k: (open(cmd[-1], "wb").close(), SimpleNamespace(returncode=0))[1])
    monkeypatch.delenv("HOUSE_ENROLL_ANNOUNCER_RAW", raising=False)
    assert cues.speak_announcer("Register needs User auth first.") is True
    assert [c[0] for c in calls] == ["synth", "boost", "play"] and calls[-1] == ("play", True)
    calls.clear()
    monkeypatch.setenv("HOUSE_ENROLL_ANNOUNCER_RAW", "1")
    assert cues.speak_announcer("x") is True
    assert [c[0] for c in calls] == ["synth", "play"] and calls[-1] == ("play", False)


# ---- house fallbacks come from house-local.json, never from code (test_local_values) -----------------------------
@pytest.fixture
def house_file(tmp_path, monkeypatch):
    p = tmp_path / "house-local.json"
    p.write_text(json.dumps({"user_id": "user_a", "user_display": "User A", "regular_stop_id": "regular_a_seat",
                             "inbox_people": [{"id": "user_a", "display": "Alpha"}, {"id": "regular_a", "display": "Bravo"}],
                             "migrate_people": ["user_a"]}))
    monkeypatch.setenv("HOUSE_ID_LOCAL", str(p))
    monkeypatch.delenv(local_values.SWITCH, raising=False)
    return p


def test_off_reads_the_house_file(house_file):
    assert local_values.local("user_display", "the User") == "User A"
    assert local_values.local("regular_stop_id", "regular") == "regular_a_seat"


def test_on_gives_the_generic_value(house_file, monkeypatch):
    monkeypatch.setenv(local_values.SWITCH, "1")
    assert local_values.local("user_display", "the User") == "the User"
    assert local_values.local("inbox_people", []) == []


def test_missing_key_or_file_gives_the_generic_value(tmp_path, monkeypatch):
    monkeypatch.delenv(local_values.SWITCH, raising=False)
    monkeypatch.setenv("HOUSE_ID_LOCAL", str(tmp_path / "absent.json"))
    assert local_values.local("user_id", "user") == "user"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("HOUSE_ID_LOCAL", str(bad))
    assert local_values.local("user_id", "user") == "user"


def test_inbox_is_laid_out_from_the_house_file(house_file, tmp_path):
    inbox = tmp_path / "inbox"
    ensure_inbox_scaffold(inbox)
    assert sorted(p.name for p in inbox.iterdir()) == ["README.md", "regular_a", "user_a"]
    text = (inbox / "README.md").read_text()
    assert "| 1 | Alpha    | on    | `user_a/01_on.wav` |" in text
    assert "import-clips --person regular_a --replace" in text


def test_generic_inbox_names_nobody(house_file, tmp_path, monkeypatch):
    monkeypatch.setenv(local_values.SWITCH, "1")
    inbox = tmp_path / "inbox"
    ensure_inbox_scaffold(inbox)
    assert [p.name for p in inbox.iterdir()] == ["README.md"]
    assert "`<person_id>/01_on.wav`" in inbox_readme([])
