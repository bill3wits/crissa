"""The presence module's tests run against modules/presence/crissa_presence from a scratch state home (2026-10-09).

Ported from the house's rail (its tests/), names and paths adapted: the household's ids are generic (user, person2,
guest1..3) and the guest/occupancy rosters, which the house carried as defaults, come from the environment here.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "modules" / "presence"
_HOME = Path(tempfile.mkdtemp(prefix="crissa-presence-tests-"))
os.environ["CRISSA_HOME"] = str(_HOME)
os.environ.setdefault("RAIL_GUEST_IDS", "val,xena,yuri")
os.environ.setdefault("RAIL_OCCUPANCY_IDS", "user,wanda")
os.environ.setdefault("HOUSE_USER_ID", "user")
if str(MOD) not in sys.path:
    sys.path.insert(0, str(MOD))


# Tests that measured THIS house and cannot be measured on a pack (skipped with the reason, never deleted):
#   the house's camera stations (data/camera-stops.json: desk, seats, the door rest) are one room's measurements and
#   do not ship; a pack mints its own at Init. The house's live identity store and its bench folders stay home too.
STATIONS = "the house's measured camera stations (camera-stops.json) do not ship; a pack mints its own at Init"
LIVE_STORE = "reads the house's live identity store"
BENCH = "reads the house's bench folders"
DOOR_PHOTO = "the house's own door photograph (data/body_audit_refs/door_home_live.jpg) does not ship"
SKIP = {
    "test_door_picture.py": {"test_the_door_reference_is_the_door_and_a_seat_is_not": DOOR_PHOTO},
    "test_beam_look.py": {"test_az_181_is_desk": STATIONS, "test_az_240_is_wanda": STATIONS,
                          "test_ff_at_beam_blocked_before_station_pick": STATIONS, "test_media_beam_does_not_look": STATIONS,
                          "test_media_characterizing_does_not_block_desk": STATIONS, "test_mill_lease_does_not_veto": STATIONS,
                          "test_same_station_does_not_reslew": STATIONS, "test_user_without_hour_falls_back_to_desk": STATIONS},
    "test_f2f.py": {"test_f2f_beam_az_feeds_desk_even_for_scratch": STATIONS, "test_f2f_session_open_skips_media_hour": STATIONS,
                    "test_f2f_session_open_without_hour_aims_user_seat": STATIONS, "test_f2f_user_without_hour_falls_back_to_desk": STATIONS,
                    "test_f2f_vocative_beam_hour_eight_is_wanda": STATIONS, "test_hour_from_az_picks_station_not_heading": STATIONS,
                    "test_pose_for_hour_uses_measured_stops": STATIONS, "test_session_open_does_not_keep_tv_vad_hour": STATIONS,
                    "test_unmapped_vocative_hour_falls_back_to_desk": STATIONS, "test_vad_while_looking_does_not_chase_az": STATIONS},
    "test_error_check.py": {"test_error_check_is_station_snaps_not_video": STATIONS, "test_seats_from_cards_one_row_per_hour": STATIONS},
    "test_patrol_monitor12.py": {"test_look_around_ends_door_and_stays_tiny": STATIONS, "test_seat_patrol_is_short_and_ordered": STATIONS},
    "test_sweep_pose.py": {"test_camera_stops_on_disk_matches_default": STATIONS, "test_default_pose_is_minus_five": STATIONS,
                           "test_error_check_stops_match_who_is_here": STATIONS},
    "test_guest_game.py": {"test_named_seats_come_from_init_and_guests_get_none": STATIONS},
    "test_argus_notify.py": {"test_room_paragraph_user_sits_behind_camera": STATIONS},
    "test_cosmos_operator.py": {"test_kit_dir_docs_exist": BENCH},
    "test_enrollments.py": {"test_load_family_from_house_id": LIVE_STORE, "test_self_match": LIVE_STORE},
}


def pytest_collection_modifyitems(config, items):
    import pytest
    for item in items:
        why = SKIP.get(Path(str(item.fspath)).name, {}).get(item.originalname or item.name)
        if why:
            item.add_marker(pytest.mark.skip(reason=why))
