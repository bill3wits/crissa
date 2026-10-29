"""Argus names a frame; the rail runs the embedder. Serving, not selecting."""
from __future__ import annotations

import pathlib


def test_embed_is_path_confined():
    """A caller may name a frame, never a filesystem location."""
    from crissa_presence.face_embed import embed_frame

    for eid, fr in (("..", "x.jpg"), ("ev_1", "../../etc/passwd"),
                    ("ev_1", "a/b.jpg"), ("", ""), ("ev_1", "")):
        out = embed_frame(eid, fr)
        assert out["ok"] is False, f"{eid}/{fr} was not refused"


def test_a_missing_frame_is_not_found_not_a_crash():
    from crissa_presence.face_embed import embed_frame

    out = embed_frame("ev_does_not_exist", "faceid_000.jpg")
    assert out["ok"] is False and out["error"] == "not found"


def test_it_reports_why_a_frame_is_poor_not_only_that_it_is():
    """Argus needs to see embed_px / sharpness / yaw to understand a low sim —
    every selection heuristic that failed today was invisible as a number."""
    import inspect

    from crissa_presence import face_embed

    src = inspect.getsource(face_embed.embed_path)
    for k in ("embed_px", "sharpness", "yaw", "det_score"):
        assert f'"{k}"' in src, f"{k} must be reported"
