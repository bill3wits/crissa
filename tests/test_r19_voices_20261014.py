"""Round 19, the voices (the User 2026-10-14, .101 RESULTS-20261014-0256 #4 #5 #6 #10; docs/checkpoints/r19-voices-20261014).

  * #6 SEAMS AS SHORT AS SPEECH'S (modules/voice/scripts/crissa-pocket.py): Pocket opens every chunk with 0.5-0.9 s of
    quiet and closes it with 0.2-0.5 s, so a sentence seam was 1.0-1.4 s of silence. From the second chunk on the
    lead-in is cut to SEAM_LEAD_S; a chunk that is not the line's last keeps SEAM_SENTENCE_S / SEAM_CLAUSE_S of its
    closing quiet. The first chunk's lead-in streams as made. Kill POCKET_SEAM_TRIM=0.
  * #4 ONE LOUDNESS THROUGH SETUP (lib/loudness.py, the coach's cues._level_pcm, house-speak's kept gain): one gain per
    voice per Setup run, on the house's one yardstick, a whole line's measure replacing a line-start one. Kill
    CRISSA_SETUP_ONE_LOUDNESS=0.
  * #5 THE TEAM SPEAKS AT THE PACE OF SPEECH (mouth_prints): every line rendered before the first is said, its lead-in
    cut, a short room tail, the prints after the last line. Kill CRISSA_MOUTHS_PACE=0.
  * #5 / #10 the lines (lib/crissa_init ANNOUNCER_INTRO, CRISSA_INTRO).

Nothing is played and no voice runs: Pocket's seams run on stand-in frames, the levels on tones."""
from __future__ import annotations

import array
import importlib
import importlib.util
import json
import math
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))
import loudness as L  # noqa: E402

SR = 24000
FRAME = 1920                         # Pocket's 80 ms frame, in samples


def _frame(amp: float, n: int = FRAME) -> bytes:
    return array.array("h", [int(amp * 32767 * math.sin(i / 3.0)) for i in range(n)]).tobytes()


Q, V = _frame(0.004), _frame(0.15)   # Pocket's lead-in quiet reads 0.002-0.009 RMS; her voice 0.02-0.2


def _pocket(trim: bool = True):
    sys.path.insert(0, str(ROOT / "modules" / "voice"))
    spec = importlib.util.spec_from_file_location("crissa_pocket_r19", ROOT / "modules" / "voice" / "scripts" /
                                                  "crissa-pocket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.SEAM_TRIM = trim                 # POCKET_SEAM_TRIM=1 (off by default: the lips bench, see the checkpoint)
    return mod


def _secs(frames) -> float:
    return sum(len(f) for f in frames) / 2.0 / SR


# ---- #6: Pocket's seams ------------------------------------------------------------------------------------------------

def test_6_a_later_chunk_loses_its_lead_in_and_a_seam_keeps_a_speakers_pause():
    P = _pocket()
    first = [Q] * 9 + [V] * 5 + [Q] * 6                            # 0.72 s lead-in, voice, 0.48 s closing quiet
    out = list(P.seamed(iter(first), True, False, "Understood."))
    assert out[:9] == [Q] * 9, "the first chunk's lead-in is as Pocket made it (the window its stamp is made in)"
    assert out[9:14] == [V] * 5
    assert _secs(out[14:]) == pytest.approx(P.SEAM_SENTENCE_S, abs=0.001), "a sentence end keeps 0.32 s"
    later = [Q] * 10 + [V] * 4 + [Q] * 2 + [V] * 3 + [Q] * 6       # a later chunk, cut mid-sentence (no stop)
    out = list(P.seamed(iter(later), False, False, "never mind how long precisely,"))
    assert _secs(out[:out.index(V)]) == pytest.approx(P.SEAM_LEAD_S, abs=0.001), "its lead-in: 0.08 s"
    keep = int(2 * SR * P.SEAM_CLAUSE_S)
    assert b"".join(out) == Q + V * 4 + Q * 2 + V * 3 + (Q * 6)[:keep], \
        "quiet inside a chunk is kept whole; a clause cut keeps 0.12 s of its closing quiet"
    last = [Q] * 10 + [V] * 3 + [Q] * 6
    out = list(P.seamed(iter(last), False, True, "Call me Ishmael."))
    assert out == [Q] + [V] * 3 + [Q] * 6, "the line's last chunk keeps its closing quiet; only its lead-in is cut"
    assert list(P.seamed(iter(first), True, True, "One.")) == first, "a one-chunk line is untouched"
    assert list(P.seamed(iter([Q] * 4), True, False, "...")) == [Q] * 4, "a first chunk with no voice: as it came"
    assert _secs(list(P.seamed(iter([Q] * 4), False, False, "..."))) == pytest.approx(P.SEAM_LEAD_S, abs=0.001)


def test_6_nothing_a_seam_keeps_is_held_back_from_the_stream():
    P = _pocket()
    pulled = []

    def frs(seq):
        for i, f in enumerate(seq):
            pulled.append(i)
            yield f
    seq = [Q] * 6 + [V] * 3 + [Q] * 8
    g = P.seamed(frs(seq), True, False, "Yeah, sure.")
    for k in range(6):                                              # the first chunk's lead-in
        assert next(g) == Q and pulled[-1] == k, "each lead-in frame goes out before the next is made"
    for k in range(3):
        assert next(g) == V
    for k in range(4):                                              # 0.32 s of closing quiet: as made
        assert next(g) == Q and pulled[-1] == 9 + k
    assert list(g) == [] and pulled[-1] == len(seq) - 1, "the quiet past the seam's keep is dropped at the chunk's end"


def test_6_the_switch_and_the_runner_use_it():
    P = _pocket(trim=False)
    seq = [Q] * 9 + [V] * 3 + [Q] * 6
    assert list(P.seamed(iter(seq), False, False, "One.")) == seq, "the default: every frame as made"
    src = (ROOT / "modules" / "voice" / "scripts" / "crissa-pocket.py").read_text(encoding="utf-8")
    assert 'SEAM_TRIM = os.environ.get("POCKET_SEAM_TRIM", "0").strip().lower() in ("1", "true", "yes", "on")' in src
    assert "fr = seamed(counted(frames(c, name)), i == 0, i == len(chunks) - 1, c)" in src
    assert src.index("fr = seamed(") < src.index('q.put(("mark", i, c))'), "a piece's mark still follows its audio"


def test_6_a_line_through_the_runner_has_short_seams_and_its_marks(monkeypatch):
    P = _pocket()
    texts = ["Understood.", "Here's the opening of Moby Dick.", "Call me Ishmael."]
    monkeypatch.setattr(P, "chunks_for", lambda text, a=None, b=None, st=None: list(texts))
    monkeypatch.setattr(P, "frames", lambda chunk, name: iter([Q] * 9 + [V] * 6 + [Q] * 5))
    monkeypatch.setitem(P.RT, "settings", dict(P.DEFAULTS, emit="frames"))
    got, marks = [], []
    P.run_job("x", "crissa", got.append, mark=lambda i, t: marks.append((i, len(b"".join(got)))))
    pcm = b"".join(got)
    a = array.array("h")
    a.frombytes(pcm)
    hop = FRAME
    quiet = [math.sqrt(sum(x * x for x in a[i:i + hop]) / hop) / 32768 < 0.01 for i in range(0, len(a), hop)]
    runs, n = [], 0
    for q in quiet + [False]:
        if q:
            n += 1
        elif n:
            runs.append(n * 0.08)
            n = 0
    inner = runs[1:-1]
    assert inner and max(inner) <= P.SEAM_SENTENCE_S + P.SEAM_LEAD_S + 0.001, inner
    assert [m[0] for m in marks] == [0, 1, 2] and marks[-1][1] == len(pcm)


# ---- #4: one loudness through Setup ------------------------------------------------------------------------------------

def _tone(amp: float, secs: float = 2.0) -> bytes:
    return array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * 220 * i / SR)) for i in range(int(secs * SR)))).tobytes()


def _med_db(pcm: bytes) -> float:
    return 20 * math.log10(L.speech_med_rms(pcm, SR))


def _run(home: Path, run_id="run-a", status="running"):
    home.mkdir(parents=True, exist_ok=True)
    (home / "init-run.json").write_text(json.dumps({"status": status, "run_id": run_id}), encoding="utf-8")


def test_4_a_whole_line_replaces_a_line_start_and_never_the_reverse(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_SETUP_ONE_LOUDNESS", raising=False)
    home = tmp_path / "home"
    _run(home)
    run = L.setup_run_id(home)
    key = L.setup_level_key("Lila@bench", "pocket", -26.0)
    assert run == "run-a" and key == "lila|pocket|-26.0"
    assert L.setup_gain_keep(home, run, key, 0.26, "start")
    assert not L.setup_gain_keep(home, run, key, 0.30, "start"), "a voice's first measure stands"
    assert L.setup_gain(home, run, key) == (0.26, "start")
    assert L.setup_gain_keep(home, run, key, 0.41, "line"), "a whole line's replaces a line start's"
    assert not L.setup_gain_keep(home, run, key, 0.20, "start") and not L.setup_gain_keep(home, run, key, 0.5, "line")
    assert L.setup_gain(home, run, key) == (0.41, "line")
    d = json.loads(L.setup_level_path(home).read_text())
    assert d["gains"] == {key: 0.41} and d["how"] == {key: "line"}, "round 17's `gains` map is unchanged in shape"
    assert L.setup_gain(home, "run-b", key) == (None, ""), "another run measures again"
    _run(home, status="done")
    assert L.setup_run_id(home) == ""
    monkeypatch.setenv("CRISSA_SETUP_ONE_LOUDNESS", "0")
    assert not L.setup_one_loudness_on()


def _cues(monkeypatch, home: Path, house=False):
    monkeypatch.syspath_prepend(str(ROOT / "modules" / "id"))
    C = importlib.import_module("crissa_id.house_enroll.cues")
    monkeypatch.setattr(C, "_house", lambda: house)
    monkeypatch.setattr(C, "_state_home", lambda: home)
    monkeypatch.setattr(C, "_ENGINE", {"lila": "pocket", "announcer": "pocket", "crissa": "pocket"})
    monkeypatch.delenv("CRISSA_SETUP_ONE_LOUDNESS", raising=False)
    return C


def test_4_the_coach_levels_every_voice_on_one_band_with_one_gain_each(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _run(home)
    C = _cues(monkeypatch, home)
    hot, quiet = _tone(0.5), _tone(0.08)
    out = C._level_pcm(hot, SR, "announcer")
    assert _med_db(out) == pytest.approx(L.LEVEL_TARGET_DB, abs=0.6), "on the band, not at a peak"
    assert L.setup_gain(home, "run-a", "announcer|pocket|-26.0")[1] == "line"
    again = C._level_pcm(_tone(0.35), SR, "announcer")
    g = L.setup_gain(home, "run-a", "announcer|pocket|-26.0")[0]
    assert _med_db(again) == pytest.approx(_med_db(_tone(0.35)) + 20 * math.log10(g), abs=0.3), "the same gain, no jump"
    up = C._level_pcm(quiet, SR, "crissa")
    assert _med_db(up) == pytest.approx(L.LEVEL_TARGET_DB, abs=0.6), "each voice its own gain, all on one band"
    peak = C._level_pcm(hot)                                       # no speaker named: the peak level, as before
    assert max(abs(x) for x in array.array("h", peak)) == pytest.approx(C.CUE_PEAK * 32767, abs=2)


def test_4_the_coach_replaces_house_speaks_line_start_gain_and_house_speak_reads_it(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _run(home)
    spec = importlib.util.spec_from_file_location("hs_r19_level", ROOT / "scripts" / "house-speak.py")
    hs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hs)
    monkeypatch.setattr(hs, "_HOUSE", False)
    monkeypatch.setattr(hs, "_crissa_home", lambda: home)
    monkeypatch.setattr(hs, "PLAY_MATCH_LOUD", True)
    monkeypatch.setattr(hs, "PLAY_TARGET_DB", -26.0)
    monkeypatch.setattr(hs, "PLAY_ALLOW_CUT", True)
    monkeypatch.setattr(hs, "_AIR_WHO", "lila")
    monkeypatch.delenv("CRISSA_SETUP_ONE_LEVEL", raising=False)
    hs._LINE["engine"] = "pocket"
    hs._LEVEL.update(run=None, key=None, gain=None)
    g_start = hs.boost_play_pcm(_tone(0.5, 0.6), SR)[1]            # house-speak's first Lila line, read off its start
    assert L.setup_gain(home, "run-a", "lila|pocket|-26.0") == (round(g_start, 4), "start")
    C = _cues(monkeypatch, home)
    C._level_pcm(_tone(0.3), SR, "lila")                           # the coach's whole Lila line
    g_line, how = L.setup_gain(home, "run-a", "lila|pocket|-26.0")
    assert how == "line" and g_line != pytest.approx(g_start, abs=1e-3)
    hs._LEVEL.update(run=None, key=None, gain=None)                 # her next house-speak line: a new process
    assert hs.boost_play_pcm(_tone(0.2), SR)[1] == pytest.approx(g_line, abs=1e-4), "both roads, one gain"
    hs._LEVEL.update(run=None, key=None, gain=None)
    monkeypatch.setattr(hs, "_AIR_WHO", "crissa")
    hs.boost_play_pcm(_tone(0.5, 0.6), SR)
    assert L.setup_gain(home, "run-a", "lila|pocket|-26.0") == (g_line, "line"), "house-speak never replaces a line"


def test_4_outside_setup_the_switch_and_the_house_keep_the_peak_level(tmp_path, monkeypatch):
    home = tmp_path / "home"
    C = _cues(monkeypatch, home)
    hot = _tone(0.5)
    peak = lambda p: max(abs(x) for x in array.array("h", p))      # noqa: E731
    assert peak(C._level_pcm(hot, SR, "lila")) == pytest.approx(C.CUE_PEAK * 32767, abs=2), "no Setup run"
    _run(home)
    monkeypatch.setenv("CRISSA_SETUP_ONE_LOUDNESS", "0")
    assert peak(C._level_pcm(hot, SR, "lila")) == pytest.approx(C.CUE_PEAK * 32767, abs=2), "the kill"
    monkeypatch.delenv("CRISSA_SETUP_ONE_LOUDNESS")
    C = _cues(monkeypatch, home, house=True)
    assert C._level_pcm(hot, SR, "lila") == hot, "the house's cue as it was"
    assert not L.setup_level_path(home).exists()


def test_4_every_coach_road_names_its_voice():
    a = ROOT / "modules/id/crissa_id/house_enroll"
    mp = (a / "mouth_prints.py").read_text()
    assert "wav = _lips_wav(cues._level_pcm(pcm, int(sr), voice), int(sr), text, voice)" in mp
    assert "cues._paplay_pcm(pcm, sr, site.sink, speaker=speaker)" in mp
    vr = (a / "voice_register.py").read_text()
    assert "cues._paplay_pcm(pcm, sr, site.sink, spoken_tail=kept, speaker=cue_voice())" in vr
    assert "cues.setup_gain_for(got[0], got[1], speaker or cue_voice())" in vr      # round 20: the voice warmed for
    c = (a / "cues.py").read_text()
    assert 'pcm = _level_pcm(pcm, sr, speaker or setup_cue_voice() or "announcer")' in c


# ---- #5: the team at the pace of speech --------------------------------------------------------------------------------

def test_5_a_line_loses_its_lead_in_quiet_faded_in():
    sys.path.insert(0, str(ROOT / "modules" / "id"))
    MP = importlib.import_module("crissa_id.house_enroll.mouth_prints")
    pcm = _tone(0.0, 0.7) + _tone(0.3, 1.0)
    out = MP.trim_head(pcm, SR)
    assert len(out) / 2 / SR == pytest.approx(1.0 + MP.HEAD_KEEP_S, abs=0.006)
    a = array.array("h", out)
    assert abs(a[0]) <= 1 and out[-200:] == pcm[-200:]
    assert MP.trim_head(_tone(0.3, 1.0), SR) == _tone(0.3, 1.0), "a line that starts at once is unchanged"


@pytest.mark.parametrize("pkg", ["crissa_id.house_enroll", "crissa_presence.enroll_panel"])
def test_5_each_line_is_rendered_before_its_turn_and_the_prints_come_after_the_last(pkg, monkeypatch, tmp_path):
    from test_round16_setup_20261013 import _mouths_mod
    MP, played = _mouths_mod(pkg, monkeypatch, tmp_path)
    monkeypatch.setattr(MP, "_is_house", lambda: False)
    monkeypatch.setattr(MP, "print_mic", lambda site: "alsa_input.raw")
    monkeypatch.setenv("CRISSA_SETUP_LIPSYNC_MEASURE", "0")
    ev = []
    pcm = array.array("h", [int(9000 * math.sin(i / 3.0)) for i in range(24000)]).tobytes()
    monkeypatch.setattr(MP, "_render", lambda text, speaker: ev.append(("render", text)) or (pcm, 24000, 0.0))
    monkeypatch.setattr(MP.cues, "_paplay_pcm", lambda p, sr, sink, **k: ev.append(("play", k.get("speaker"))))
    real_rec = MP.record_fixed
    secs = []
    monkeypatch.setattr(MP, "record_fixed", lambda wav, site, s: secs.append(s) or real_rec(wav, site, s))
    import numpy as np
    monkeypatch.setattr(MP, "embed_wav", lambda wav: ev.append(("print", Path(wav).name)) or np.ones(256) * 0.0625)
    lines = ["Hi, I'm the announcer.", "Two.", "Three.", "Four."]
    monkeypatch.setenv(MP.PLAN_ENV, json.dumps({"ids": ["announcer"], "lines": {"announcer": lines}}))
    got = MP.run_mouths(SimpleNamespace(mic="alsa_input.raw", sink="s"))
    assert got["saved"] == {"announcer": 4}
    kinds = [e[0] for e in ev]
    plays = [i for i, k in enumerate(kinds) if k == "play"]
    assert len(plays) == 4 and all(kinds[:p].count("render") >= n + 1 for n, p in enumerate(plays)), kinds
    assert kinds[plays[-1] + 1:] == ["print"] * 4 and "print" not in kinds[:plays[-1]], "the prints after the last line"
    assert [e[1] for e in ev if e[0] == "play"] == ["announcer"] * 4, "each line names its voice for Setup's level"
    assert secs and all(s == pytest.approx(MP.LEAD_S + 1.0 + MP.PLAIN_TAIL_S) for s in secs)
    assert not list((tmp_path / "cache" / "id" / "mouths").glob("*.wav")), "no audio is kept"
    ev.clear()
    secs.clear()
    monkeypatch.setenv("CRISSA_MOUTHS_PACE", "0")                                    # the kill: round 18's order
    got = MP.run_mouths(SimpleNamespace(mic="alsa_input.raw", sink="s"))
    assert [e[0] for e in ev] == ["render", "play", "print"] * 4
    assert all(s == pytest.approx(MP.LEAD_S + 1.0 + MP.TAIL_S) for s in secs)


# ---- #5 / #10: the lines ------------------------------------------------------------------------------------------------

def test_5_10_the_team_lines():
    import crissa_init as zi
    from crissa_lipsync import MIN_LINES
    assert len(zi.ANNOUNCER_INTRO) == 4 and len(zi.ANNOUNCER_INTRO) > MIN_LINES, "the lip sync keeps a line to spare"
    assert sum(len(t) for t in zi.ANNOUNCER_INTRO) <= 150 and zi.ANNOUNCER_INTRO[0] == "Hi, I'm the announcer."
    assert all("!" in t for t in zi.CRISSA_INTRO) and zi.CRISSA_INTRO[0].startswith("And I'm Crissa!")
    assert zi.CLOSING_LINE == "What can we do today?", "her question opens the ear (ear_bridge HER QUESTION)"


def test_4_her_level_is_read_off_a_whole_line_before_her_voice_says_it_is_ready(tmp_path, monkeypatch):
    import crissa_init as zi
    import crissa_tts
    home = tmp_path / "home"
    _run(home)
    monkeypatch.setattr(zi, "_home", lambda: home)
    monkeypatch.setattr(zi.crissa_home, "is_house", lambda *a, **k: False)
    monkeypatch.setattr(crissa_tts, "config", lambda *a, **k: {"engine": "pocket"})
    monkeypatch.delenv("CRISSA_SETUP_ONE_LOUDNESS", raising=False)
    asked = []
    synth = lambda text, who, cfg=None, timeout=0: asked.append((text, who, cfg)) or (_tone(0.5), SR)   # noqa: E731
    ups = iter([False, False, True])
    assert zi.prime_her_level("run-a", synth=synth, up=lambda: next(ups))
    assert [a[:2] for a in asked] == [(zi.TEAM_LINE, "lila")]
    assert asked[0][2]["backup"] == [] and asked[0][2]["fallback"] is False, "her own voice only, never a backup"
    g, how = L.setup_gain(home, "run-a", "lila|pocket|-26.0")
    assert how == "line" and _med_db(L.apply_gain(_tone(0.5), g)) == pytest.approx(-26.0, abs=0.6)
    assert not zi.prime_her_level("run-a", synth=synth, up=lambda: True) and len(asked) == 1, "once a run"
    assert not zi.prime_her_level("run-b", synth=synth, up=lambda: True), "not the run going on"
    monkeypatch.setenv("CRISSA_SETUP_HER_LEVEL", "0")
    assert not zi.prime_her_level("run-a", synth=synth, up=lambda: True)
    monkeypatch.delenv("CRISSA_SETUP_HER_LEVEL")
    assert not zi.prime_her_level("run-a"), "a test never reaches a real voice"
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    vi = src.split("def voice_install(", 1)[1].split("\ndef ", 1)[0]
    assert vi.index("prime_her_level(run_id)") < vi.index('put(state=state, engine=res.get("chosen")'), \
        "kept before the install says her voice is ready (what her welcome waits for)"


def test_4_5_the_team_s_level_is_read_off_its_first_lines_before_the_first_is_said(monkeypatch, tmp_path):
    from test_round16_setup_20261013 import _mouths_mod
    MP, played = _mouths_mod("crissa_id.house_enroll", monkeypatch, tmp_path)
    monkeypatch.setattr(MP, "_is_house", lambda: False)
    monkeypatch.setattr(MP, "print_mic", lambda site: "alsa_input.raw")
    monkeypatch.setenv("CRISSA_SETUP_LIPSYNC_MEASURE", "0")
    _run(tmp_path)                                                   # a Setup run under the state home (CRISSA_HOME)
    monkeypatch.setattr(MP.cues, "_house", lambda: False)
    monkeypatch.setattr(MP.cues, "_ENGINE", {"announcer": "pocket"})
    lines = {"Hi.": _tone(0.6, 1.0), "Two.": _tone(0.2, 2.0), "Three.": _tone(0.1, 1.0), "Four.": _tone(0.1, 1.0)}
    monkeypatch.setattr(MP, "_render", lambda text, speaker: (lines[text], SR, 0.0))
    seen = []
    monkeypatch.setattr(MP.cues, "_paplay_pcm",
                        lambda p, sr, sink, **k: seen.append(L.setup_gain(tmp_path, "run-a", "announcer|pocket|-26.0")))
    monkeypatch.setenv(MP.PLAN_ENV, json.dumps({"ids": ["announcer"], "lines": {"announcer": list(lines)}}))
    MP.run_mouths(SimpleNamespace(mic="alsa_input.raw", sink="s"))
    want = L.line_gain(lines["Hi."] + lines["Two."], SR)
    assert MP.LEVEL_LINES == 2
    assert seen and all(g == (round(want, 4), "line") for g in seen), "kept before the first line, off its first two"
    assert want != pytest.approx(L.line_gain(lines["Hi."], SR), abs=1e-3), "not off the first line alone"


# ---- #6: Pocket's pace counts what it made -----------------------------------------------------------------------------

def test_6_the_quiet_a_seam_drops_is_named_on_the_marks_and_counted_in_her_pace(monkeypatch):
    import io
    import socket
    import threading
    from http.server import ThreadingHTTPServer
    P = _pocket()
    texts = ["Understood.", "Here's the opening of Moby Dick."]
    chunk = [Q] * 9 + [V] * 4 + [Q] * 6
    monkeypatch.setattr(P, "chunks_for", lambda text, a=None, b=None, st=None: list(texts))
    monkeypatch.setattr(P, "frames", lambda c, name: iter(chunk))
    monkeypatch.setitem(P.RT, "settings", dict(P.DEFAULTS, emit="frames"))
    sent = []
    P.run_job("x", "crissa", sent.append)
    made = 2 * len(b"".join(chunk))
    assert P.RT["cut_bytes"] == made - len(b"".join(sent)) > 0, "the quiet dropped, counted"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), P.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = json.dumps({"text": "x", "voice": "crissa", "marks": True}).encode()
        s = socket.create_connection(("127.0.0.1", srv.server_address[1]))
        s.sendall(b"POST /stream HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                  % len(body) + body)
        raw = b""
        while True:
            got = s.recv(65536)
            if not got:
                break
            raw += got
        s.close()
    finally:
        srv.shutdown()
    import re
    cuts = [int(m) for m in re.findall(rb";cut=(\d+)", raw)]
    assert cuts and cuts[-1] == made - len(b"".join(sent)), "the last mark carries the line's whole cut"
    import turbo_stream
    stream = (b"4;piece-end=0;text=a;cut=1234\r\nabcd\r\n0;piece-end=1;text=b;cut=2000\r\n\r\n")
    marks = []
    r = turbo_stream.TurboReader(SimpleNamespace(fp=io.BytesIO(stream)), on_mark=lambda i, e, t: marks.append(i))
    r.done.wait(2)
    assert r.cut == 2000 and marks == [0, 1], "the reader keeps the largest cut; marks as before"
    src = (ROOT / "scripts" / "house-speak.py").read_text(encoding="utf-8")
    assert '_made = len(self.whole) + int(getattr(self.reader, "cut", 0) or 0)' in src
    assert "_pace = _made / 2.0 / float(self.mc.sr or 24000) / secs" in src
