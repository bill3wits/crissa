"""Phoneme families (the User 2026-10-21: "let's map every phoneme to its phonetic family of sounds").
config/phoneme-families.json maps every phoneme of the three alphabets the house touches to its family (= the mouth
class), today and under his v2 set; lib/mouth_viseme reads its tables from it. Pins: nothing mapped before moves,
every phoneme each alphabet can emit has a row, and a row's family is what its phones say."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import mouth_viseme as mv  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _nineteen(monkeypatch):
    """Round 41: his classes ship ON (HOUSE_MOUTH_CLASSES_V2); this file pins the 19, so it runs with V2 off. His V2
    shapes are pinned in test_mouth_classes_v2_20261021."""
    import importlib
    import mouth_viseme
    monkeypatch.setenv("HOUSE_MOUTH_CLASSES_V2", "0")
    importlib.reload(mouth_viseme)
    yield
    monkeypatch.undo()
    importlib.reload(mouth_viseme)


DOC = json.loads((ROOT / "config" / "phoneme-families.json").read_text(encoding="utf-8"))
OLD = json.loads((ROOT / "tests" / "fixtures" / "mouth-viseme-maps-20261021.json").read_text(encoding="utf-8"))

# CMUdict's 39 (stress-free) = exactly what the aligner's MFA english_us_arpa dictionary uses (counted 2026-10-21 over
# its 208,651 lines; plus `spn`, spoken noise, which is not a phoneme)
ARPABET_39 = ("AA AE AH AO AW AY B CH D DH EH ER EY F G HH IH IY JH K L M N NG OW OY P R S SH T TH UH UW V W Y "
              "Z ZH").split()
# every token espeak-ng -x en-us emitted over 43,948 words of that dictionary (2026-10-21), stress marks removed
ESPEAK_SEEN = ("n s l I t @ k r d m a z E p b 3 i oU eI f I2 0 g V v N u: i: aI w t# I# h S ; dZ 3: @L A@ a# tS j T "
               "o@ O: aU A: i@ aa O2 O@ U r- e@ OI i@3 U@ D aI3 Z ? n- t2 aI@ @- x : l# O A~ O~ | !").split()
# misaki 0.9.4 US_VOCAB (its declared US phonemes), stress marks included
MISAKI_US_VOCAB = "AIOWYbdfhijklmnpstuvwzæðŋɑɔəɛɜɡɪɹɾʃʊʌʒʔʤʧˈˌθᵊᵻ"
# what misaki actually EMITTED over 5,493 dictionary words (2026-10-21): its US output rewrites the flap ɾ to T, which
# its own US_VOCAB does not list (punctuation removed)
MISAKI_SEEN = "AIOTWYbdfhijklmnpstuvwzæðŋɑɔəɛɜɡɪɹʃʊʌʒʤʧˈˌθᵊᵻ"


def test_nothing_mapped_before_moves():
    for ph, cls in OLD["PHONE_TO_CLASS"].items():
        assert mv.PHONE_TO_CLASS[ph] == cls, ph
    assert {k: list(v) for k, v in mv.DIPHTHONG.items()} == OLD["DIPHTHONG"], "diphthongs stay two notes"
    for tok, phones in OLD["ESPEAK_PHONE"].items():
        assert mv.ESPEAK_PHONE[tok] == phones, tok


def test_every_phoneme_each_alphabet_emits_has_a_row():
    assert set(ARPABET_39) <= set(DOC["arpabet"]), sorted(set(ARPABET_39) - set(DOC["arpabet"]))
    unread = [t for t in ESPEAK_SEEN if mv.espeak_token(t) is None]
    assert not unread, unread
    assert not [c for c in MISAKI_US_VOCAB + MISAKI_SEEN if c not in DOC["misaki"]]


def test_a_rows_family_is_what_its_phones_say_and_every_family_is_a_class():
    classes, v2 = set(DOC["classes"]), set(DOC["classes_v2"])
    for alpha in ("arpabet", "espeak", "misaki", "house"):
        for ph, row in DOC[alpha].items():
            assert row["family"] == [mv.PHONE_TO_CLASS[p] for p in row["phones"]], (alpha, ph)
            assert set(row["family"]) <= classes and set(row["family_v2"]) <= v2, (alpha, ph)


# HIS DECIDED COLUMN (the User 2026-10-21, his phoneme map https://claude.ai/artifact/7k69hf5zCMaBCn4yJyngbY and his
# answers in chat): every ARPAbet phoneme's v2 family is his pick; one he skipped keeps today's, merged ("Apply the
# 10-18 merges"). Today's AH tile is Uh; Ah (father, five, the laugh) and Aa (cat, now) are new; Aw is the true Ah, not
# a class ("Aw = the true Ah"); NG and the glottal stop are KG; day ends on the merged H+Y.
DECIDED = {
    "AA": ["Ah"], "AE": ["Aa"], "AH": ["Uh"], "AO": ["OH"], "AW": ["Aa", "WOO"], "AY": ["Ah", "EE"], "B": ["MBP"],
    "CH": ["SH/CH"], "D": ["TDN+S/Z"], "DH": ["TH"], "EH": ["EH"], "ER": ["ER"], "EY": ["EH", "H+Y"], "F": ["FV"],
    "G": ["KG"], "HH": ["H+Y"], "IH": ["EE"], "IY": ["EE"], "JH": ["SH/CH"], "K": ["KG"], "L": ["L"], "M": ["MBP"],
    "N": ["TDN+S/Z"], "NG": ["KG"], "OW": ["OH", "WOO"], "OY": ["OH", "EE"], "P": ["MBP"], "R": ["R"], "S": ["TDN+S/Z"],
    "SH": ["SH/CH"], "T": ["TDN+S/Z"], "TH": ["TH"], "UH": ["WOO"], "UW": ["WOO"], "V": ["FV"], "W": ["WOO"],
    "Y": ["H+Y"], "Z": ["TDN+S/Z"], "ZH": ["SH/CH"], "AX": ["Uh"], "AXR": ["ER"], "IX": ["EE"], "UX": ["WOO"],
    "DX": ["TDN+S/Z"], "EL": ["L"], "EM": ["MBP"], "EN": ["TDN+S/Z"], "NX": ["TDN+S/Z"], "Q": ["KG"],
}


def test_his_decided_column_is_his_picks():
    assert {ph: row["family_v2"] for ph, row in DOC["arpabet"].items()} == DECIDED
    assert DOC["classes_v2"] == ["rest", "rest-open", "MBP", "FV", "TH", "WOO", "OH", "EE", "EH", "ER", "TDN+S/Z",
                                 "SH/CH", "L", "R", "KG", "H+Y", "Uh", "Ah", "Aa"]
    assert len(DOC["classes_v2"]) == len(DOC["classes"]) == 19, "the same complexity"
    assert "Aw" not in DOC["classes_v2"] and DOC["v2_open"] == []


def test_every_alphabet_follows_his_picks_by_sound():
    def members(fam):
        return {f"{a}:{ph}" for a in ("arpabet", "espeak", "misaki") for ph, r in DOC[a].items()
                if fam in r["family_v2"]}
    assert {"arpabet:AA", "misaki:ɑ", "espeak:0"} <= members("Ah")          # father / lot / loch
    assert {"arpabet:AE", "misaki:æ", "espeak:a"} <= members("Aa")          # cat
    assert {"arpabet:AO", "misaki:ɔ", "espeak:O:"} <= members("OH")         # all / caught
    assert {"arpabet:AH", "arpabet:AX", "misaki:ʌ", "misaki:ə"} <= members("Uh")
    assert {"arpabet:T", "arpabet:D", "arpabet:N", "arpabet:S", "arpabet:Z", "misaki:ɾ"} <= members("TDN+S/Z")
    assert {"arpabet:HH", "arpabet:Y"} <= members("H+Y")
    assert "arpabet:IY" not in members("H+Y"), "Keep EE (teeth 3 vs 2)"
    glide = {(a, t): DOC[a][t]["family_v2"] for a, t in (("espeak", "aI"), ("misaki", "I"), ("espeak", "eI"),
                                                          ("misaki", "A"), ("espeak", "aU"), ("misaki", "W"))}
    assert glide == {("espeak", "aI"): ["Ah", "EE"], ("misaki", "I"): ["Ah", "EE"],       # five: his AY, not cat's Aa
                     ("espeak", "eI"): ["EH", "H+Y"], ("misaki", "A"): ["EH", "H+Y"],     # day: his EY
                     ("espeak", "aU"): ["Aa", "WOO"], ("misaki", "W"): ["Aa", "WOO"]}     # now: his AW
    tags = {t: r["family_v2"] for t, r in DOC["turbo_tags"].items() if r["kind"] == "sound"}
    assert tags == {"[laugh]": ["H+Y", "Ah"], "[chuckle]": ["EH", "rest-open"], "[cough]": ["rest-open", "OH"],
                    "[clear throat]": ["rest-open", "EH"], "[sigh]": ["EE", "rest-open"], "[shush]": ["SH/CH"],
                    "[gasp]": ["OH"], "[groan]": ["OH", "WOO"], "[sniff]": ["rest", "MBP"]}


def test_espeak_words_no_longer_fall_back_to_the_letters_on_a_syllabic_l_or_a_palatal_mark():
    """10.6 % of 43,948 dictionary words fell back to the letter rules under HOUSE_MOUTH_G2P=espeak, nearly all on
    `;` (2,529) and `@L` (2,115)."""
    assert mv.espeak_parse("'eI_b_@L") == ["EH", "B", "L"]               # able
    assert mv.espeak_parse("h_'a_p_i_;") == ["HH", "AE", "P", "IY"]      # happy


def test_every_turbo_tag_has_a_row():
    """Chatterbox Turbo's bracket tags (the User: "Turbo also accepts many expressives like cough, chuckle")."""
    tags = ("[advertisement] [angry] [chuckle] [clear throat] [cough] [crying] [dramatic] [fear] [gasp] [groan] "
            "[happy] [laugh] [narration] [sarcastic] [shush] [sigh] [sniff] [surprised] [whispering]")
    want = {"[" + t.strip("[]") + "]" for t in tags.replace("] [", "]|[").split("|")}
    assert want == set(DOC["turbo_tags"]) and len(want) == 19
    for tag, row in DOC["turbo_tags"].items():
        assert set(row["family"]) <= set(DOC["classes"]) and set(row["family_v2"]) <= set(DOC["classes_v2"]), tag
        assert (row["kind"] == "style") == (row["family"] == []), tag
