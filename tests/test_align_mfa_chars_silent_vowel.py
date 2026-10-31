"""The User 2026-10-18: "impressed" held one shape through four phonemes. Its letter runs i-mpr-e-ss-e-d onto the
phone runs IH-MPR-EH-ST tied with every mismatch at 1, and the tie gave i, mpr and e to IH's one frame and the ss to
the four-frame M-P closure. A silent vowel letter (most often the word's last: -ed, -es, -e) now costs less."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import align_mfa as A  # noqa: E402

IMPRESSED = [{"p": "IH0", "a": 2.74, "b": 2.78}, {"p": "M", "a": 2.78, "b": 2.858}, {"p": "P", "a": 2.858, "b": 2.95},
             {"p": "R", "a": 2.95, "b": 2.99}, {"p": "EH1", "a": 2.99, "b": 3.05}, {"p": "S", "a": 3.05, "b": 3.15},
             {"p": "T", "a": 3.15, "b": 3.20}]


def test_impressed_puts_m_and_p_on_her_closure():
    ch = A.chars_from_phones("impressed", 2.74, 3.20, IMPRESSED)
    at = {c["c"] + str(k): c for k, c in enumerate(ch)}
    m, p, e = at["m1"], at["p2"], at["e4"]
    assert 2.77 <= m["a"] <= 2.79 and p["b"] <= 2.991          # m-p-r inside M-P-R, not IH's frame
    assert abs(e["a"] - 2.99) < 0.005                          # the first e is the EH
    assert A._pair(A.letter_runs("impressed"), A.phone_runs(IMPRESSED)) == [[0], [1], [2], [3], [3], [3]]


def test_a_word_with_no_silent_letter_pairs_as_before():
    ph = [{"p": "B", "a": 0.0, "b": 0.1}, {"p": "IY1", "a": 0.1, "b": 0.3}, {"p": "ZH", "a": 0.3, "b": 0.4}]
    # 'b-ei-g-e': the final e is silent; the ei is the vowel
    assert A._pair(A.letter_runs("beige"), A.phone_runs(ph)) == [[0], [1], [2], [2]]
