"""Round 38 (.101 round 37, the User: Lila's correction line came out in the wrong voice and louder than Crissa;
"Lila's correction lines as short Lila FILMS, like count-again / vid-left, not TTS"). Each correction line is her own
film on the one page, in BOTH pack copies of the routine; the kill CRISSA_LILA_CORRECTION_FILMS=0 says it as before."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COPIES = ["modules/id/crissa_id/house_enroll/voice_register.py",
          "modules/presence/crissa_presence/enroll_panel/voice_register.py"]
FILMS = {"turn-quarter": "Just a quarter turn. I need to see your face.",
         "turn-back": "I can't see your face. Turn back a little.",
         "look-once-more": "Look straight at the camera once more.",
         "rest-later": "I'll take the rest later."}


def _load(rel, monkeypatch):
    """Each copy as its own package (the id module's, the presence module's), as the packs import it."""
    import importlib
    if rel.startswith("modules/presence"):
        held = sys.modules.get("crissa_presence")
        if held is not None and not hasattr(held, "__path__"):     # lib/crissa_presence.py, the player's lamp
            for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
                monkeypatch.delitem(sys.modules, k)
        monkeypatch.syspath_prepend(str(ROOT / "modules" / "presence"))
        return importlib.import_module("crissa_presence.enroll_panel.voice_register")
    monkeypatch.syspath_prepend(str(ROOT / "modules" / "id"))
    return importlib.import_module("crissa_id.house_enroll.voice_register")


@pytest.fixture(autouse=True)
def _own_modules():
    """The packages this file imports leave with it, with what they set at import (paths.py's HOUSE_ID_ROOT):
    later files resolve house_enroll and the identity root to the house-id tree."""
    import os
    before, env, path = set(sys.modules), dict(os.environ), list(sys.path)
    yield
    for k in set(sys.modules) - before:
        sys.modules.pop(k, None)
    os.environ.clear()
    os.environ.update(env)
    sys.path[:] = path


@pytest.mark.parametrize("rel", COPIES)
def test_a_correction_line_is_her_film(rel, monkeypatch):
    vr = _load(rel, monkeypatch)
    assert {vr.CORRECTION_FILMS[line]: line for line in vr.CORRECTION_FILMS} == FILMS
    films, said = [], []
    monkeypatch.setattr(vr, "films_on", lambda: True)
    monkeypatch.setattr(vr, "_film", lambda fid, wait=True, about="": films.append((fid, wait)) or True)
    monkeypatch.setattr(vr, "_announce", lambda site, line, *a, **k: said.append(line))
    monkeypatch.delenv("CRISSA_LILA_CORRECTION_FILMS", raising=False)
    vr._correct(None, vr.TURN_QUARTER_LINE)
    vr._correct(None, vr.TURN_BLIND_LINE, wait=False)
    assert films == [("turn-quarter", True), ("turn-back", False)] and said == []
    films.clear()
    monkeypatch.setattr(vr, "_film", lambda fid, wait=True, about="": False)        # not cued: said as before
    vr._correct(None, vr.TURN_LATER_LINE)
    vr._correct(None, vr.TURN_BLIND_LINE, wait=False)                                # the glass hint already shows it
    assert said == [vr.TURN_LATER_LINE]
    said.clear()
    monkeypatch.setenv("CRISSA_LILA_CORRECTION_FILMS", "0")
    monkeypatch.setattr(vr, "_film", lambda fid, wait=True, about="": films.append(fid) or True)
    vr._correct(None, vr.TURN_FRONT_LINE)
    assert films == [] and said == [vr.TURN_FRONT_LINE], "the kill: the line is said as round 37 said it"
    src = (ROOT / rel).read_text(encoding="utf-8")
    for line in ("TURN_QUARTER_LINE", "TURN_FRONT_LINE", "TURN_LATER_LINE"):
        assert f"_announce(site, {line})" not in src, f"{line} still bypasses the film"


def test_the_films_are_cut_listed_shipped_and_traced():
    cfg = json.loads((ROOT / "config" / "setup-films.json").read_text(encoding="utf-8"))["films"]
    pkgs = (ROOT / "config" / "crissa-packages.json").read_text(encoding="utf-8")
    prov = json.loads((ROOT / "config" / "crissa-film-provenance.json").read_text(encoding="utf-8"))
    prov = prov.get("films", prov)
    for fid, line in FILMS.items():
        f = cfg[fid]
        assert f["line"] == line and f["optional"] and f["role"] == "line" and f["last_word_f"] < f["end_f"]
        assert (ROOT / "display" / "setup-films" / f"{fid}.mp4").stat().st_size > 10_000
        assert f'"display/setup-films/{fid}.mp4"' in pkgs
        row = prov[f"display/setup-films/{fid}.mp4"]
        assert row["status"] == "traced, no signature on the source" and row["segments"][0]["xai_request_id"]
