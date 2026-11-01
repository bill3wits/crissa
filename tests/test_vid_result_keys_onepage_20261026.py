"""The one-page Voice ID section draws the result card's keys (the User 2026-10-26, his walk of 1.0.1: "click what" -
Voice ID ended partial and the page showed four count boxes and no key), and the section is titled Voice and Face ID
(round 39 N4's ask, landed only on the done plate's line until now)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_one_page_draws_redo_and_continue_when_voice_id_ends_partial():
    src = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert 'id: "op-vid-keys"' in src and 'id: "op-vid-hint"' in src
    assert "function opVidKeys(snap)" in src and 'r.state === "partial"' in src and 'keysRow(p.keys, "register")' in src
    assert src.index("opVidKeys(snap);") > src.index("function opPaint() {")


def test_the_section_the_eyebrow_and_the_ids_label_say_voice_and_face_id():
    src = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")
    assert '{ id: "vid", title: "Voice and Face ID"' in src
    cfg = json.loads((ROOT / "config" / "init-sequence-pack.json").read_text(encoding="utf-8"))
    walk = cfg.get("walk") or []
    assert any(s.get("id") == "voice_id" and s.get("eyebrow") == "Voice and Face ID" for s in walk)
    init = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert '"voice_id": "Voice and Face ID"' in init
