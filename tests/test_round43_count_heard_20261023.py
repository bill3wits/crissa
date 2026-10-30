"""Round 43, 2026-10-23 (.101 round 42, R42-1): a take is a voice print only when the ASR hears the count in it.
Three of ted's four kept takes were room noise (voiced 39-66 %, peak 2692-4180) and the kit's ASR heard "啊。" / "嗯。".
Now a take with no count is asked again; after COUNT_TRIES the count keeps nothing and the run says it heard no counting.
An ASR that does not answer is no verdict (loudness only, as before). Kill CRISSA_ENROLL_HEARS_COUNT=0. Both pack
copies (the pack's Setup runs the id module's)."""
from __future__ import annotations

import importlib
import json
import math
import struct
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKGS = [("crissa_presence", "presence", "crissa_presence.enroll_panel"), ("crissa_id", "id", "crissa_id.house_enroll")]


@pytest.fixture(params=PKGS, ids=["presence", "id"])
def vr(request, monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_HOME", str(tmp_path / "home"))
    for k in ("CRISSA_ENROLL_HEARS_COUNT", "CRISSA_ENROLL_COUNT_WORDS_MIN", "CRISSA_ENROLL_COUNT_TRIES",
              "CRISSA_ENROLL_ASR_URL", "CRISSA_TURN_CHECK"):
        monkeypatch.delenv(k, raising=False)
    top, mdir, base = request.param
    for k in [k for k in sys.modules if k == top or k.startswith(top + ".")]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.syspath_prepend(str(ROOT / "modules" / mdir))
    mod = importlib.import_module(base + ".voice_register")
    monkeypatch.setattr(mod, "_cancelled", lambda: False)
    monkeypatch.setattr(mod, "AFTER_BEEP_SEC", 0.0)
    monkeypatch.setenv("CRISSA_ENROLL_HEARS_COUNT", "1")        # a pack's default (this test box is the house)
    monkeypatch.setattr(mod.cues, "wait_for_beep", lambda *a, **k: None)
    return mod


def _wav(path: Path, amp: int, secs: float = 1.0, sr: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(amp * math.sin(i * 0.3))) for i in range(int(secs * sr))))
    return path


class _ASR:
    """A stand-in for the kit's ASR (/stt: raw PCM in, {"text"} out) answering one fixed text."""

    def __init__(self, text: str):
        self.text, self.seen = text, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.seen.append((self.path, int(self.headers.get("X-Sample-Rate") or 0), len(self.rfile.read(n))))
                raw = json.dumps({"text": outer.text}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers()
                self.wfile.write(raw)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def test_the_count_words_and_the_dials(vr, monkeypatch):
    assert vr.count_words_in("One, two, three, four, five, six.") == 6
    assert vr.count_words_in("1 2 3 4") == 4
    assert vr.count_words_in("啊。") == 0 and vr.count_words_in("嗯。") == 0
    assert vr.count_words_in("Second take: status report for the house.") == 0
    assert vr.count_words_min() == 3 and vr.count_tries() == 2
    monkeypatch.setenv("CRISSA_ENROLL_COUNT_WORDS_MIN", "5")
    monkeypatch.setenv("CRISSA_ENROLL_COUNT_TRIES", "3")
    assert vr.count_words_min() == 5 and vr.count_tries() == 3


def test_a_loud_take_with_no_count_in_it_is_refused(vr, monkeypatch, tmp_path):
    asr = _ASR("嗯。")
    try:
        monkeypatch.setenv("CRISSA_ENROLL_ASR_URL", asr.url)
        wav = _wav(tmp_path / "noise.wav", 4000)
        assert vr.take_is_quiet(wav)[0] is False            # loud enough: round 42 kept it
        heard, nw, text = vr.take_hears_count(wav)
        assert heard is False and nw == 0 and text == "嗯。"
        assert asr.seen and asr.seen[0][0] == "/stt" and asr.seen[0][1] == 16000
        asr.text = "One, two, three, four, five, six."
        assert vr.take_hears_count(wav)[:2] == (True, 6)
    finally:
        asr.close()


def test_a_silent_wav_is_refused(vr, tmp_path):
    wav = _wav(tmp_path / "silent.wav", 0)
    assert vr.take_is_quiet(wav)[0] is True


def test_no_asr_is_no_verdict(vr, monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_ENROLL_ASR_URL", "http://127.0.0.1:9")
    heard, nw, _ = vr.take_hears_count(_wav(tmp_path / "a.wav", 4000))
    assert heard is None and nw == 0


def _run(vr, monkeypatch, heard_by_take: list, *, hears=True):
    """The verbal routine's counts with one facing count; `heard_by_take` = take_hears_count's answer per take."""
    monkeypatch.setattr(vr, "plan_takes", lambda four, only: [("t1", "front", "0", False, "count")])
    monkeypatch.setattr(vr, "films_on", lambda: True)
    monkeypatch.setattr(vr, "count_film", lambda i, away, words: "vid-facing")
    films, emitted, quarantined = [], [], []
    monkeypatch.setattr(vr, "_film", lambda fid: films.append(fid))
    monkeypatch.setattr(vr, "_glass", lambda *a, **k: None)
    monkeypatch.setattr(vr, "_snap_now", lambda *a, **k: None)
    monkeypatch.setattr(vr, "turn_check_on", lambda: False)
    monkeypatch.setattr(vr, "_face_stage", lambda *a, **k: None)
    monkeypatch.setattr(vr, "coach_emit", lambda **e: emitted.append(e))
    monkeypatch.setattr(vr, "_quarantine", lambda rec: quarantined.append(rec["wav"]))
    n = {"t": 0}

    def take(site, pid, **kw):
        n["t"] += 1
        return {"wav": "take-%d.wav" % n["t"], "out_dir": "x"}
    monkeypatch.setattr(vr, "_take_voice", take)
    monkeypatch.setattr(vr, "take_is_quiet", lambda wav: (False, 0.5, 3000))
    answers = list(heard_by_take)
    monkeypatch.setattr(vr, "take_hears_count", lambda wav: answers.pop(0))
    if not hears:
        monkeypatch.setenv("CRISSA_ENROLL_HEARS_COUNT", "0")
    takes: list = []
    assert vr._verbal_takes(None, "ted", station="desk", room_mic="m", takes=takes, front_paths=[], sweep_paths=[],
                            four=False, only="", t0=0.0)
    return takes, films, emitted, quarantined


def test_no_count_twice_keeps_nothing_and_says_so(vr, monkeypatch):
    takes, films, emitted, quarantined = _run(vr, monkeypatch, [(False, 0, "啊。"), (False, 0, "嗯。")])
    assert takes == []
    assert quarantined == ["take-1.wav", "take-2.wav"]
    assert films == ["vid-facing", "count-again"]               # asked again with her own film, once
    assert {"type": "coach", "text": vr.COUNT_NO_COUNT_SAID} in emitted


def test_a_count_on_the_second_take_is_kept(vr, monkeypatch):
    takes, films, emitted, quarantined = _run(vr, monkeypatch, [(False, 0, "啊。"), (True, 6, "one two three four five six")])
    assert [t["wav"] for t in takes] == ["take-2.wav"] and quarantined == ["take-1.wav"]
    assert not any(e.get("text") == vr.COUNT_NO_COUNT_SAID for e in emitted)


def test_no_asr_keeps_the_take_on_its_loudness(vr, monkeypatch):
    takes, _, _, quarantined = _run(vr, monkeypatch, [(None, 0, "URLError")])
    assert [t["wav"] for t in takes] == ["take-1.wav"] and quarantined == []


def test_the_kill_is_round_42(vr, monkeypatch):
    takes, films, _, _ = _run(vr, monkeypatch, [], hears=False)       # the ASR is never asked
    assert [t["wav"] for t in takes] == ["take-1.wav"] and films == ["vid-facing"]


def test_the_house_keeps_loudness_only(vr, monkeypatch):
    monkeypatch.delenv("CRISSA_ENROLL_HEARS_COUNT")
    house = "_paths" if hasattr(vr, "_paths") else "_ppaths"
    monkeypatch.setattr(getattr(vr, house), "is_house", lambda: True)
    assert vr.hears_count_on() is False
    monkeypatch.setattr(getattr(vr, house), "is_house", lambda: False)
    assert vr.hears_count_on() is True
    monkeypatch.setenv("CRISSA_ENROLL_HEARS_COUNT", "1")
    assert vr.hears_count_on() is True
