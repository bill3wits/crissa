"""The playhead reads the scrub once (2026-10-28): a release between the check and the use killed the playhead thread
on the Mobile bench and froze its picture."""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def test_the_scrub_frame_is_read_once():
    assert '_sf, _sk = SCRUB.get("frame"), SCRUB.get("key")' in SRC
    assert "int(_sf)" in SRC
    assert 'int(SCRUB["frame"])' not in SRC
