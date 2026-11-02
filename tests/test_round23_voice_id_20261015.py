"""Round 23, Voice ID on the one page (the User 2026-10-15, .101 round 22 notes 2-5):
2. "make the final beep right on the end of bar": the bar starts when the recorder starts (rec_start), not at the GO
   stamp before the mic, the tone and the recorder;
3. "the and again had a lag ... so I false started": the beep's gap counts from her last word as the page said it
   (said_at), polled every 50 ms;
4. "get the profiles during speech ... she notices complete and plays that clip": the turned counts take the profiles,
   and Your face plays `already` (on Your face) when both sides are in, else asks only for the missing side;
5. "let it beep when it takes one": the take-in ding on each face photo.
Both copies of the routine; no camera, no sound, no network."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "lib", ROOT / "modules" / "id"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

COPIES = ("crissa_id.house_enroll.voice_register", "crissa_presence.enroll_panel.voice_register")


@pytest.fixture(autouse=True)
def _own_presence_modules():
    # lib/crissa_presence.py shadows the presence module's package (tests/test_round16_setup_20261013.py does the same)
    held = {k: v for k, v in sys.modules.items() if k == "crissa_presence" or k.startswith("crissa_presence.")}
    yield
    for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
        if k not in held:
            del sys.modules[k]
    sys.modules.update(held)


@pytest.fixture(params=COPIES)
def vr(request, monkeypatch):
    if request.param.startswith("crissa_presence"):
        monkeypatch.syspath_prepend(str(ROOT / "modules" / "presence"))
        for k in [k for k in sys.modules if k == "crissa_presence" or k.startswith("crissa_presence.")]:
            del sys.modules[k]
    for k in ("CRISSA_BAR_FROM_RECORDING", "CRISSA_BEEP_FROM_WORD", "CRISSA_ONE_PAGE_COUNT_PROFILES",
              "CRISSA_FACE_SHUTTER"):
        monkeypatch.delenv(k, raising=False)
    m = importlib.import_module(request.param)
    m._TURNED_OK.clear()
    yield m
    m._TURNED_OK.clear()


def _paths_of(vr):
    return getattr(vr, "_paths", None) or getattr(vr, "_ppaths")


# ---- 2: the bar ------------------------------------------------------------------------------------------------------
def test_the_bar_starts_when_the_recorder_starts(vr, monkeypatch):
    sent = []

    class _T:
        def __init__(self, target, args=(), kwargs=None, **k):
            self.f, self.a, self.k = target, args, kwargs or {}

        def start(self):
            self.f(*self.a, **self.k)
    monkeypatch.setattr(vr.threading, "Thread", _T)
    monkeypatch.setattr(vr, "_post", lambda url, body, timeout=12.0: sent.append((url, body)) or {})
    monkeypatch.setattr(_paths_of(vr), "is_house", lambda: False)
    b = vr._BarStart()
    b.append(12.5)
    assert list(b) == [12.5]
    assert sent and sent[0][0].endswith("/v1/action") and sent[0][1] == {
        "action": "register_glass", "payload": {"rec_start": True}}


def test_never_on_the_house_and_a_kill(vr, monkeypatch):
    sent = []
    monkeypatch.setattr(vr, "_post", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(_paths_of(vr), "is_house", lambda: True)
    vr._BarStart().append(1.0)
    monkeypatch.setattr(_paths_of(vr), "is_house", lambda: False)
    monkeypatch.setenv("CRISSA_BAR_FROM_RECORDING", "0")
    vr._BarStart().append(1.0)
    assert sent == []


def test_the_take_hands_record_fixed_the_hook():
    for mod in COPIES:
        src = (ROOT / "modules" / ("id/crissa_id/house_enroll" if "id." in mod else "presence/crissa_presence/enroll_panel")
               / "voice_register.py").read_text()
        assert "record_fixed(wav, site, seconds, started=_BarStart())" in src


def test_the_glass_moves_only_its_bar(monkeypatch):
    import crissa_init as zi
    zi.set_register_glass({"phase": "turn", "text": "Count", "seconds": 5.0, "beep": True, "take": 2, "takes": 4,
                           "stage": "counts", "line": "x"})
    before = dict(zi._GLASS)
    zi._GLASS["beep_ts"] -= 0.7                 # the GO stamp, 0.7 s before the recorder
    got = zi.set_register_glass({"rec_start": True})
    after = zi._GLASS
    assert after["beep_ts"] > before["beep_ts"] - 0.7 and 0.6 < after["rec_late_s"] < 1.0
    for k in ("phase", "text", "seconds", "take", "takes", "stage", "line"):
        assert after[k] == before[k], k
    assert got.get("beep_ts") == after["beep_ts"]
    zi.set_register_glass({"phase": "idle"})


# ---- 3: the gap from her word ----------------------------------------------------------------------------------------
def test_the_gap_counts_from_her_word(vr, monkeypatch):
    assert vr._said_kept_s(100.0, now=100.4) == pytest.approx(0.4)
    assert vr._said_kept_s(0.0, now=100.4) == 0.0
    assert vr._said_kept_s(100.0, now=110.0) == 2.0, "a stale stamp never pushes the beep into the past by more"
    monkeypatch.setenv("CRISSA_BEEP_FROM_WORD", "0")
    assert vr._said_kept_s(100.0, now=100.4) == 0.0
    assert vr.FILM_POLL_S <= 0.05


def test_a_film_marks_her_word_not_the_notice(vr, monkeypatch):
    monkeypatch.setattr(vr, "_post", lambda url, body, timeout=12.0: {"ok": True, "seq": 3, "seconds": 0.1})
    monkeypatch.setattr(vr, "_film_said_at", lambda: (3, 0.0, vr.time.time() - 0.35))
    marks = []
    monkeypatch.setattr(vr.cues, "mark_spoken", lambda kept: marks.append(kept))
    assert vr._film("count-again") is True
    assert 0.3 < marks[0] < 0.6


def test_the_bus_says_when(monkeypatch):
    import crissa_init as zi
    monkeypatch.setattr(zi, "setup_films", lambda: {"face": {}, "already": {}})
    n = zi.film_cue("already", by="t", about="face")
    zi.film_said(n)
    bus = zi.film_bus()
    assert bus["about"] == "face" and bus["said"] == n and bus["said_at"] > 0


# ---- 4 + 5: the face ---------------------------------------------------------------------------------------------
def _face(vr, monkeypatch, have=(), stored=True):
    films, glass, dings, snaps = [], [], [], []
    monkeypatch.setattr(vr, "_cancelled", lambda: False)
    monkeypatch.setattr(vr, "_camera_off", lambda: False)
    monkeypatch.setattr(vr, "_glass", lambda *a, **k: glass.append(k.get("faces")))
    monkeypatch.setattr(vr, "_film", lambda fid, wait=True, about="": films.append((fid, about)) or True)
    monkeypatch.setattr(vr.cues, "success_ding", lambda site: dings.append(1))
    monkeypatch.setattr(vr, "_snap_turned", lambda site, pid, **k: snaps.append(k["side"]) or {"stored": stored,
                                                                                               "why": "turned"})
    vr._TURNED_OK.update(have)
    got = vr._face_stage(object(), "p1", station="desk")
    return got, films, glass, dings, snaps


def test_both_sides_in_from_the_counts_is_already(vr, monkeypatch):
    got, films, glass, dings, snaps = _face(vr, monkeypatch, have=("left", "right"))
    # round 31 (.101 fix 4): "Two quick photos." (face-start) says what `already` is about
    assert got == ["left", "right"] and films == [("face-start", "face"), ("already", "face")] and snaps == [] and dings == []
    assert glass[-1] == ["left", "right"]


def test_one_side_in_asks_only_for_the_other_with_a_shutter(vr, monkeypatch):
    got, films, glass, dings, snaps = _face(vr, monkeypatch, have=("left",))
    assert films[0] == ("face", "") and snaps == ["right"] and got == ["left", "right"] and dings == [1]


def test_none_in_takes_both_with_two_shutters(vr, monkeypatch):
    got, films, glass, dings, snaps = _face(vr, monkeypatch)
    assert snaps == ["left", "right"] and dings == [1, 1]


def test_a_photo_not_taken_has_no_shutter(vr, monkeypatch):
    got, films, glass, dings, snaps = _face(vr, monkeypatch, stored=False)
    assert got == [] and dings == []


def test_the_kills(vr, monkeypatch):
    monkeypatch.setenv("CRISSA_ONE_PAGE_COUNT_PROFILES", "0")
    monkeypatch.setenv("CRISSA_FACE_SHUTTER", "0")
    got, films, glass, dings, snaps = _face(vr, monkeypatch, have=("left", "right"))
    assert films[0] == ("face", "") and snaps == ["left", "right"] and dings == []


def test_a_side_counts_only_when_she_really_turned(vr):
    src = (ROOT / "modules/id/crissa_id/house_enroll/voice_register.py").read_text()
    assert 'if side and why == "turned":\n            _TURNED_OK.add(side)' in src


# ---- the page --------------------------------------------------------------------------------------------------------
def test_the_page_opens_the_section_a_cue_is_about():
    page = (ROOT / "display" / "crissa-init.html").read_text()
    assert "opLine(bus.id, Number(bus.seq), bus.about ? [bus.about] : null)" in page
    assert "about.forEach((a) => S.op.addressed.add(a))" in page


def test_the_open_section_is_brought_into_view_once_per_change():
    page = (ROOT / "display" / "crissa-init.html").read_text()
    assert "function opShowNow(live)" in page and "opShowNow(live);" in page
    assert 'if (!id || key === S.op.shownKey) return;' in page, "once per change: a person's scroll is never fought"
    assert "scroll-margin: 16px;" in page


def test_the_krew_link_comes_from_config_and_only_web(monkeypatch):
    import crissa_init as zi
    for cfg, want in (({}, ""), ({"krew_url": "https://example.org/krew"}, "https://example.org/krew"),
                      ({"krew_url": "javascript:alert(1)"}, ""), ({"krew_url": "file:///etc/passwd"}, "")):
        monkeypatch.setattr(zi, "films_cfg", lambda cfg=cfg: cfg)
        assert zi.krew_url() == want
    page = (ROOT / "display" / "crissa-init.html").read_text()
    assert 'v.krew_url && !$("op-krew-link")' in page and 'rel: "noopener"' in page
