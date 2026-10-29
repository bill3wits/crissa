"""Hair color estimator should not invent plant/wall green on people."""

from crissa_presence.oracle_bench import _color_name_hsv, apply_appearance_baseline


def test_hair_mode_rejects_green_cyan():
    # bright green → forced away from green in hair mode
    assert _color_name_hsv(60, 180, 150, hair=True) != "green"
    assert _color_name_hsv(90, 180, 150, hair=True) != "cyan"


def test_hair_mode_maps_dark_warm_to_brown_or_black():
    assert _color_name_hsv(15, 80, 90, hair=True) in ("brown", "black", "dark_gray")


def test_apply_baseline_overrides_bad_live_hair(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    people = tmp_path / "people" / "wanda"
    people.mkdir(parents=True)
    (people / "appearance.json").write_text(
        json.dumps(
            {
                "person_id": "wanda",
                "hair_colors": ["brown", "gray"],
                "top_colors": ["gray", "black"],
                "source": "enroll_refs",
            }
        )
    )
    monkeypatch.setenv("HOUSE_ID_ROOT", str(tmp_path))
    live = {
        "hair_colors": ["green", "yellow"],
        "top_colors": ["red"],
        "bottom_colors": [],
        "height_cue": "average",
        "distance_cue": "mid",
    }
    fixed = apply_appearance_baseline("wanda", live)
    assert "green" not in (fixed.get("hair_colors") or [])
    assert "brown" in (fixed.get("hair_colors") or [])
    assert fixed.get("hair_source") == "enroll_ref"
