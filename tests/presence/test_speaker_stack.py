"""Speaker stack: beam is a clock (tenths), not a percent; stack ignores it."""
from crissa_presence.speaker_stack import clock_from_az, stack_speaker


def test_clock_from_az_tenths():
    assert clock_from_az(0.0) == 12.0
    assert clock_from_az(180.0) == 6.0
    assert clock_from_az(191.0) == 6.4
    assert clock_from_az(90.0) == 3.0
    assert clock_from_az(3.0) == 12.1
    assert clock_from_az(None) is None


def test_from_six_is_clock_not_percent():
    s = stack_speaker(az_deg=180.0, user_seat=6)
    assert s["beam"] == 6.0
    assert s["beam_hour"] == 6
    assert s["camera"] == 0.0
    assert s["stack"] == 0.0
    assert s["sex"] is None


def test_elsewhere_does_not_cut_embeds():
    at = stack_speaker(az_deg=180.0, user_seat=6, voice_sim=0.49)
    away = stack_speaker(az_deg=90.0, user_seat=6, voice_sim=0.49)
    assert at["embeds"] == away["embeds"] == 0.49
    assert at["voice"] == away["voice"] == 0.49
    assert at["beam"] == 6.0
    assert away["beam"] == 3.0
    assert at["stack"] == away["stack"] == 0.0


def test_embeds_and_sex_is_hundred():
    s = stack_speaker(
        beam_hour=10,
        user_seat=6,
        voice_sim=0.81,
        sex_live="M",
        sex_user="M",
    )
    assert s["embeds"] == 0.81
    assert s["voice"] == 0.81
    assert s["face"] is None
    assert s["embeds_ok"] is True
    assert s["voice_ok"] is True
    assert s["face_ok"] is False
    assert s["sex"] == 1.0
    assert s["stack"] == 1.0


def test_face_does_not_fill_embeds():
    s = stack_speaker(
        beam_hour=10,
        user_seat=6,
        face_sim=0.81,
        sex_live="M",
        sex_user="M",
    )
    assert s["embeds"] is None
    assert s["embeds_ok"] is False
    assert s["voice"] is None
    assert s["face"] == 0.81
    assert s["face_ok"] is True


def test_from_six_plus_match_is_hundred():
    s = stack_speaker(
        az_deg=180.0,
        user_seat=6,
        voice_sim=0.6,
        sex_live="M",
        sex_user="M",
    )
    assert s["beam"] == 6.0
    assert s["stack"] == 1.0


def test_sex_mismatch_beam_does_not_fill_stack():
    s = stack_speaker(
        az_deg=180.0,
        user_seat=6,
        face_sim=0.9,
        sex_live="F",
        sex_user="M",
    )
    assert s["sex"] == 0.0
    assert s["beam"] == 6.0
    assert s["stack"] == 0.0


def test_camera_face_and_sex_is_hundred():
    s = stack_speaker(
        beam_hour=10,
        user_seat=6,
        camera=1.0,
        sex_live="M",
        sex_user="M",
    )
    assert s["camera"] == 1.0
    assert s["stack"] == 1.0


def test_f2f_camera_half_without_lock():
    s = stack_speaker(beam_hour=10, user_seat=6, camera=0.5)
    assert s["camera"] == 0.5
    assert s["stack"] == 0.25


def test_camera_sex_mismatch_not_hundred():
    s = stack_speaker(
        beam_hour=10,
        user_seat=6,
        camera=1.0,
        face_sim=0.9,
        sex_live="F",
        sex_user="M",
    )
    assert s["sex"] == 0.0
    assert s["stack"] == 0.5



def test_the_speaker_row_uses_the_ears_bar_for_the_person():
    from crissa_presence.speaker_stack import voice_bar
    env = {"RAIL_VOICE_MATCH_SIM": "0.50", "RAIL_VOICE_MATCH_SIM_USER": "0.40"}
    assert voice_bar("user", env) == 0.40
    assert voice_bar("someone", env) == 0.50
    assert voice_bar(None, env) == 0.50
    assert voice_bar("user", dict(env, RAIL_PRESENCE_PERSON_BAR="0")) == 0.50
    assert voice_bar("user", {}) == 0.50
