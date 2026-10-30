"""A playlist's rows must cover its head track as well as its film (2026-10-03): the crop rescue gave Lila's 90 takes
89 + 163 heads that rows written from an older track did not have; `sync_frames` rewrites rows (frames + n only, the
index and `how` untouched) when a row has no yaw where the track has a head."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import mouth_playlist as MPL  # noqa: E402


def _head(n, have):
    return [({"s": 100.0, "pose": [0.0, 30.0, 0.0], "nose": [100, 100], "chin": [100, 150], "u": [1, 0], "v": [0, 1],
              "R": [1, 0, 0, 0, 1, 0, 0, 0, 1], "t": [0, 0, 0], "A": [[1, 0], [0, 1]], "open": 0.3} if i in have else None)
            for i in range(n)]


def test_rows_behind_head_counts_rows_without_a_yaw_where_the_track_has_a_head():
    pl = {"index": {"profile/0/rest": "x"}, "frames": [{"yaw": 30.0} if i < 5 else {"yaw": None} for i in range(10)], "n": 10}
    assert MPL.rows_behind_head(pl, _head(10, set(range(10)))) == 5
    assert MPL.rows_behind_head(pl, _head(10, set(range(5)))) == 0
    assert MPL.rows_behind_head(pl, []) == 0


def test_sync_frames_rewrites_rows_for_the_gap_and_keeps_the_index_and_how():
    pl = {"index": {"profile/0/rest": "x"}, "how": {"profile/0/rest": "eye"},
          "frames": [{"yaw": 30.0, "pitch": 0.0, "pitch_bin": "profile", "yaw_bin": "0", "open": 0.3 + i * 0.01} if i < 5
                     else {"yaw": None, "pitch_bin": "profile", "yaw_bin": "0", "open": 0.3 + i * 0.01} for i in range(10)],
          "n": 10, "fps": 24.0, "bins": ["profile/0"]}
    assert MPL.frames_stale(pl, 10) == ""                                  # the film is covered: the old rule is satisfied
    did = MPL.sync_frames(pl, _head(10, set(range(10))), 10, "profile")
    assert did.startswith("5 row(s) without a head the track has")
    assert len(pl["frames"]) == 10 and pl["index"] == {"profile/0/rest": "x"} and pl["how"] == {"profile/0/rest": "eye"}
    assert all(isinstance(r, dict) and r.get("yaw") is not None for r in pl["frames"])


def test_the_tool_passes_the_reason_through():
    t = (ROOT / "scripts" / "pose-loop-playlist.py").read_text()
    assert "gap = rows_behind_head(pl, head)" in t and "sync_frames(pl, head, n_film, playlist_station(pl, row, file_name), why=stale)" in t
