"""Round 28 (the User 2026-10-17: "our imagine voice must stick ... that is the TM and our brand"; "We could offer this
voice as English Accent option ... Simple toggle at setup?"). Her Grok Imagine clip as a Pocket voice state ships in the
pack (no cloning on the machine, no download); her face speaks with it by default, Setup's toggle can choose Pocket's
English accent (azelma), and the Pocket server reads the choice on every line."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "modules" / "voice"):
    sys.path.insert(0, str(p))

import crissa_voice_tiers as VT  # noqa: E402
from crissa_voice import pocket_setup as S  # noqa: E402

FILE = ROOT / "modules/voice/faces/crissa-pocket.safetensors"
SHA = "012eb0e3fbc0e67560dcacc52ca2be3afef11a2004b01d36c3f2e90e58d3698f"


def test_the_file_is_the_pinned_one():
    assert hashlib.sha256(FILE.read_bytes()).hexdigest() == SHA and FILE.stat().st_size == 5703480
    assert S.OWN_VOICES["crissa"] == (FILE, SHA, 5703480) and S.own_voice_ok("crissa")
    prov = json.loads((ROOT / "modules/voice/faces/crissa-pocket.json").read_text())
    assert prov["sha256"] == SHA and "Created with Grok" in prov["credit"]


def test_it_is_loaded_from_the_pack_never_downloaded():
    assert S.voice_path("crissa") == FILE
    assert "crissa" in S.voices("en") and "crissa" not in S.VOICES
    assert not any("crissa" in str(p) for p in S.wanted())


def test_her_face_speaks_with_it_and_the_accent_is_azelma(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_HER_VOICE", raising=False)
    monkeypatch.setattr(VT, "_home", lambda h=None: Path(h) if h else tmp_path)
    table = {"crissa": "crissa", "lila": "eve", "default": "azelma"}
    assert VT.her_voice() == "her" and VT.pocket_face_voice("crissa", table) == "crissa"
    got = VT.set_her_voice("english_accent", by="test")
    assert got["style"] == "english_accent" and got["label"] == "Non-branded voice (UK English)"
    assert VT.pocket_face_voice("crissa", table) == "azelma"
    assert S.voice_for("crissa", dict(table, crissa=VT.pocket_face_voice("crissa", table)))[0] == "azelma"
    assert VT.pocket_face_voice("lila", table) is None, "Lila keeps her own map"
    assert VT.set_her_voice("loud")["ok"] is False
    monkeypatch.setenv("CRISSA_HER_VOICE", "english_accent")
    VT.set_her_voice("her")
    assert VT.her_voice() == "english_accent", "the env forces round 27's voice"


def test_a_tampered_file_is_not_her_voice(monkeypatch):
    monkeypatch.setitem(S._OWN_OK, "crissa", False)
    assert S.voice_for("crissa", {"crissa": "crissa", "default": "azelma"})[0] == "azelma"


def test_the_wiring():
    srv = (ROOT / "modules/voice/scripts/crissa-pocket.py").read_text()
    assert 'S.voice_for(name, _her_style_map(name, RT["settings"].get("voices")), RT.get("lang", "en"))' in srv
    tab = json.loads((ROOT / "config/crissa-voice-tiers.json").read_text())
    assert tab["tiers"]["pocket"]["settings"]["voices"]["crissa"] == "crissa"
    exp = (ROOT / "scripts/crissa-export.py").read_text()
    assert '"modules/voice/faces/crissa-pocket.safetensors":\n                 "%s"' % SHA in exp
    pk = json.loads((ROOT / "config/crissa-packages.json").read_text())["packages"]["crissa-voice"]["files"]
    assert "modules/voice/faces/crissa-pocket.safetensors" in pk and "modules/voice/faces/crissa-pocket.json" in pk
    init = (ROOT / "lib/crissa_init.py").read_text()
    assert 'if verb == "her_voice":' in init
    page = (ROOT / "display/crissa-init.html").read_text()
    assert 'act("machine", { verb: "her_voice", style })' in page
    assert '["her", "Crissa\'s voice"], ["english_accent", "Non-branded voice (UK English)"]' in page
