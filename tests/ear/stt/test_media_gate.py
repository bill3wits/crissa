"""Media vs hoarse desk speech (laryngitis 2026-08-24)."""
from crissa_ear.media_gate import _Turn, _score_window


def test_long_chunk_weak_emb_empty_room_is_media():
    turns = [_Turn(ts=1.0, dur=18.6, gap_before=0.0, rms_db=-12.0, text="x" * 80, person_id=None)]
    verdict, reason, _ = _score_window(turns, 48.0, room_hc=0, named_hc=0, emb_top_sim=0.40)
    assert verdict == "media"
    assert reason == "long_chunk_weak_emb"


def test_long_chunk_weak_emb_occupied_room_is_not_media():
    turns = [_Turn(ts=1.0, dur=18.6, gap_before=0.0, rms_db=-12.0, text="x" * 80, person_id=None)]
    verdict, reason, _ = _score_window(turns, 48.0, room_hc=1, named_hc=1, emb_top_sim=0.40)
    assert verdict != "media"
    assert reason != "long_chunk_weak_emb"
