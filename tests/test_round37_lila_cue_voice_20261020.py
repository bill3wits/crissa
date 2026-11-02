"""Round 37 (.101 round 36: the correction cue was "wrong voice and too loud"): on the one page Lila's cue went out as
audio rendered in the coach (Pocket's eve at gain 0.83); the Lila he hears on the page is the player's own render. Now
the player is given the text and Lila's voice and renders it itself; the switch off renders here as round 36. Both
enroll copies."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
PKGS = ["crissa_id.house_enroll", "crissa_presence.enroll_panel"]


@pytest.fixture(autouse=True)
def _own_modules():
    held = {k: v for k, v in sys.modules.items() if k.split(".")[0] in ("crissa_presence", "crissa_id")}
    yield
    for k in [k for k in sys.modules if k.split(".")[0] in ("crissa_presence", "crissa_id")]:
        if k not in held:
            del sys.modules[k]
    sys.modules.update(held)


def _mods(pkg, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "modules" / ("id" if pkg.startswith("crissa_id") else "presence")))
    MP = importlib.import_module(pkg + ".mouth_prints")
    VR = importlib.import_module(pkg + ".voice_register")
    return MP, VR


@pytest.mark.parametrize("pkg", PKGS)
def test_her_cue_is_rendered_by_the_player_in_her_voice(pkg, monkeypatch):
    MP, VR = _mods(pkg, monkeypatch)
    monkeypatch.delenv("CRISSA_LILA_CUE_PLAYER_VOICE", raising=False)
    monkeypatch.setattr(VR, "cue_voice", lambda env=None: "lila")
    sent, spoken = [], []
    monkeypatch.setattr(MP, "_player_base", lambda site=None: "http://127.0.0.1:9")
    monkeypatch.setattr(MP, "_player_json", lambda url, body, timeout: sent.append((url, body)) or {"ok": True, "seconds": 2.5})
    monkeypatch.setattr(MP, "wait_player_line", lambda base, cap_s: True)
    monkeypatch.setattr(MP.cues, "mark_spoken", lambda kept: spoken.append(kept))
    rendered = []
    monkeypatch.setattr(VR, "_ann_render", lambda text, speaker=None: rendered.append(text))
    got = VR._announce_on_player(object(), ["Just a quarter turn.", "I need to see your face."], 0.18)
    assert got == 2.5 and spoken == [0.0] and rendered == [], "nothing rendered in the coach"
    url, body = sent[0]
    assert url.endswith("/v1/speak") and body["voice"] == "lila" and "wav" not in body
    assert body["text"] == "Just a quarter turn. I need to see your face."


@pytest.mark.parametrize("pkg", PKGS)
def test_the_switch_off_renders_here_as_round_36(pkg, monkeypatch):
    MP, VR = _mods(pkg, monkeypatch)
    monkeypatch.setenv("CRISSA_LILA_CUE_PLAYER_VOICE", "0")
    monkeypatch.setattr(VR, "cue_voice", lambda env=None: "lila")
    rendered = []
    monkeypatch.setattr(VR, "_ann_render", lambda text, speaker=None: rendered.append(text))   # None: not rendered
    assert VR._announce_on_player(object(), ["Just a quarter turn."], 0.18) is None
    assert rendered == ["Just a quarter turn."]


def test_both_copies_carry_the_same_change():
    a = (ROOT / "modules/id/crissa_id/house_enroll/mouth_prints.py").read_text(encoding="utf-8")
    b = (ROOT / "modules/presence/crissa_presence/enroll_panel/mouth_prints.py").read_text(encoding="utf-8")
    assert a == b and "def say_text_through_player" in a
    for d in ("modules/id/crissa_id/house_enroll", "modules/presence/crissa_presence/enroll_panel"):
        v = (ROOT / d / "voice_register.py").read_text(encoding="utf-8")
        assert "got = say_text_through_player(\" \".join(texts), cue_voice(), by=\"voice id\", site=site)" in v
