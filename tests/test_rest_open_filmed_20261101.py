"""Never paint a pause (the User 2026-11-01: "never paste rest frames for that reason. She is charming in Paused with no
paint, robotic in paused when painted"): with idle rest off, a rest-open run between words shows her film too. A pack's
default, the house wall keeps the 09-13 law; a Composer class hold still paints the tile; the "paused exception" dial
(HOUSE_MOUTH_REST_OPEN_FILM_FRAMES) is 0 = off."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import mouth_playlist as MPL  # noqa: E402

SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


def test_every_run_is_a_pause_by_default():
    assert MPL.rest_open_is_pause(0.0, 0.01, 0, 24) is True
    assert MPL.rest_open_is_pause(None, None, 0, 24) is True


def test_the_paused_exception_dial():
    # 4 film frames at 24 fps = 166.7 ms
    assert MPL.rest_open_is_pause(0.0, 0.10, 4, 24) is False
    assert MPL.rest_open_is_pause(0.0, 0.17, 4, 24) is True
    assert MPL.rest_open_is_pause(None, 0.1, 4, 24) is True       # unknown span: a pause


def test_the_switch_is_a_packs_default_and_the_dial_is_off():
    assert 'os.environ.get("HOUSE_MOUTH_REST_OPEN_FILMED", house_default("0", "1"))' in SRC
    assert 'os.environ.get("HOUSE_MOUTH_REST_OPEN_FILM_FRAMES", "0")' in SRC


def test_the_painter_films_rest_open_before_the_closed_rest_branch():
    a = SRC.index('if (klass == "rest-open" and not MOUTH_IDLE_REST and MOUTH_REST_OPEN_FILMED')
    b = SRC.index('if klass == "rest" and not MOUTH_IDLE_REST:')
    assert a < b
    blk = SRC[a:b]
    assert "self.mouth.force_class is None" in blk, "a Composer class hold still paints the tile it tunes"
    assert "return self._fade_out(base)" in blk


def test_a_store_bench_shows_it():
    src = (ROOT / "lib" / "composer_libraries.py").read_text()
    assert '{"HOUSE_MOUTH_REST_OPEN_FILMED": "1"}' in src and "HOUSE_COMPOSER_LIB_REST_OPEN" in src
