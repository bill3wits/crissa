"""One rank picker (the User 2026-11-01: "a picker issue as those are down tiles on an up face ... It is systemic but maybe
in one place"): a clip with a bow map skips the pitch fold and lift; a pack's default; a store bench sets it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


def test_the_switch_is_a_packs_default():
    assert 'os.environ.get("HOUSE_MOUTH_BOW_ONE_PICKER", house_default("0", "1"))' in SRC


def test_a_bow_mapped_clip_skips_the_fold():
    i = SRC.index("_one_picker = MOUTH_BOW_ONE_PICKER and MOUTH_BOW and bool(self.lib.bow_map(self.key))")
    j = SRC.index("if PITCH_FOLD and not band and not _one_picker and _frs and bins")
    assert i < j < i + 400


def test_a_store_bench_sets_it():
    src = (ROOT / "lib" / "composer_libraries.py").read_text()
    assert '{"HOUSE_MOUTH_BOW_ONE_PICKER": "1"}' in src and "HOUSE_COMPOSER_LIB_ONE_PICKER" in src
