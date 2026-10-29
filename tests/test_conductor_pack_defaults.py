"""A pack outside the house defaults differently from the house's own install (the User 2026-10-04, the Mac scan).

The house keeps every default it always had; a pack (no `state_home` in its manifest, so is_house() is False)
gets its own. The functions are lifted out of the conductor by name, the way tests/test_crissa_agent.py does.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


def _fn(name: str, **globs):
    tree = ast.parse(SRC)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = dict(globs)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns[name]


def _handler_block(anchor: str, span: int = 1200) -> str:
    i = SRC.index(anchor)
    return SRC[i:i + span]


# ── 2. `/` -> /connect ──────────────────────────────────────────────────────────────────────────────────────────

def test_root_goes_to_the_composer_when_there_is_one_else_connect():
    rl = _fn("root_location")
    assert rl("", True) == "/connect"
    assert rl("", False) == "/frame.jpg"
    assert rl("http://127.0.0.1:8199", True) == "http://127.0.0.1:8199/"


def test_the_root_handler_uses_root_location():
    block = _handler_block('"/composer-classic.html",')
    assert "root_location(COMPOSER_URL" in block
    assert 'COMPOSER_URL + "/"' not in block


# ── 3. test tour off and no Lila bank outside the house ────────────────────────────────────────────────────────

def test_house_default_answers_both_ways():
    assert _fn("house_default", IS_HOUSE=True)("1", "0") == "1"
    assert _fn("house_default", IS_HOUSE=False)("1", "0") == "0"


def test_tour_and_face_banks_default_by_install():
    assert 'os.environ.get("HOUSE_FACE_BANKS", house_default("lila=display/lila-poses", ""))' in SRC
    assert 'os.environ.get("HOUSE_CONDUCTOR_TOUR", house_default("1", "0")) != "0"' in SRC
    assert 'os.environ.get("HOUSE_CONDUCTOR_TOUR", "1")' not in SRC


def test_is_house_is_known_before_the_face_banks_are_read():
    assert SRC.index("IS_HOUSE = crissa_home.is_house()") < SRC.index("FACE_BANKS: dict[str, Path] = {}")
    assert SRC.count("IS_HOUSE = ") == 1 and SRC.count("def house_default(") == 1


# ── 4. no house-service calls outside the house ────────────────────────────────────────────────────────────────

class _NoNet:
    class request:
        @staticmethod
        def urlopen(*a, **k):
            raise AssertionError("a house-service call was made outside the house")


def test_conversation_and_pause_are_off_with_no_house_url():
    g = dict(IS_BENCH=False, BENCH_IGNORES_PAUSE=True, HOUSE_URL="", CONV_URL="", urllib=_NoNet, time=__import__("time"),
             json=__import__("json"), _HOUSE_CONV={"t": 0.0, "on": False}, _HOUSE_PAUSED={"t": 0.0, "on": False})
    assert _fn("house_in_conversation", **g)() is False
    assert _fn("house_paused", **g)() is False


def _bridge(body: dict, seen: list):
    import contextlib
    import io
    import json

    class request:
        @staticmethod
        def urlopen(url, timeout=None):
            seen.append(url)
            return contextlib.closing(io.BytesIO(json.dumps(body).encode()))
    return type("U", (), {"request": request})


def test_in_a_pack_the_conversation_is_the_ear_bridges_open_session():
    """The User 2026-10-14 ("wire it to the ear"): a pack has no :8099, so the kit's hold reads the bridge's /health,
    whose `paused` is the top-level key; pause itself stays the house's alone."""
    url = "http://127.0.0.1:8194/health"
    for paused, want in ((False, True), (True, False)):
        seen: list = []
        g = dict(IS_BENCH=False, BENCH_IGNORES_PAUSE=True, HOUSE_URL="", CONV_URL=url, time=__import__("time"),
                 json=__import__("json"), urllib=_bridge({"ok": True, "paused": paused, "session_open": not paused}, seen),
                 _HOUSE_BG={"run": False}, _HOUSE_CONV={"t": 0.0, "on": False}, _HOUSE_PAUSED={"t": 0.0, "on": False})
        assert _fn("house_in_conversation", **g)() is want
        assert seen == [url]
        assert _fn("house_paused", **g)() is False
    g.update(urllib=_bridge({"ok": True}, []), _HOUSE_CONV={"t": 0.0, "on": False})
    assert _fn("house_in_conversation", **g)() is False, "a body without the key is no conversation"
    g.update(urllib=_NoNet, _HOUSE_CONV={"t": 0.0, "on": False}, IS_BENCH=True)
    assert _fn("house_in_conversation", **g)() is False, "a bench never asks"


def test_the_bridge_is_read_only_in_a_pack_with_the_hold_armed():
    assert ('CONV_URL = HOUSE_URL or ((os.environ.get("VO_HEAR_HEALTH_URL") or "").strip()\n'
            '                         if (CONV_HOLD and CONV_FROM_BRIDGE and not IS_HOUSE) else "")') in SRC
    # the launcher's address for this install, never the port table's default (8194 is the house's own bridge)
    conv = SRC[SRC.index("CONV_URL = "):SRC.index("CONV_URL = ") + 200]
    assert 'crissa_ports.url("bridge")' not in conv
    assert SRC.index("CONV_URL = ") > SRC.index("HOUSE_URL = ") > SRC.index("IS_HOUSE = crissa_home.is_house()")
    poll = SRC.split("def house_poll_start() -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "not CONV_URL" in poll and "urlopen(CONV_URL" in poll
    assert 'paused = bool(hear.get("paused")) if HOUSE_URL else False' in poll
    paused = SRC.split("def house_paused() -> bool:", 1)[1].split("\ndef ", 1)[0]
    assert "CONV_URL" not in paused and "urlopen(HOUSE_URL" in paused


def test_the_three_rail_urls_default_by_install():
    # the house's own addresses come from config/house-machine.json (2026-10-06), only on the house
    for name, var, key, path in (("LAMP_URL", "RAIL_VISIT_LAMP", "presence_url", "/v1/visit_lamp"),
                                 ("PRESENCE_URL", "RAIL_PRESENCE", "presence_url", "/v1/presence"),
                                 ("HOUSE_URL", "RAIL_HOUSE", "panel_url", "/v1/house")):
        assert f'{name} = os.environ.get("{var}", _house_url("{key}", "{path}"))' in SRC
    hu = _fn("_house_url", crissa_home=type("H", (), {"house_value": staticmethod(
        lambda k: {"presence_url": "http://127.0.0.1:8191", "panel_url": "http://127.0.0.1:8099"}.get(k, ""))}),
        house_default=lambda house, pack: house)
    assert hu("presence_url", "/v1/visit_lamp") == "http://127.0.0.1:8191/v1/visit_lamp"
    assert hu("panel_url", "/v1/house") == "http://127.0.0.1:8099/v1/house"
    assert _fn("_house_url", crissa_home=type("H", (), {"house_value": staticmethod(lambda k: "")}),
               house_default=lambda house, pack: house)("panel_url", "/v1/house") == ""
    assert "if LAMP_URL:" in SRC and "_json_get(PRESENCE_URL) if PRESENCE_URL else None" in SRC
    assert "_http_json_get(PRESENCE_URL) if PRESENCE_URL else None" in SRC and "            if HOUSE_URL:\n" in SRC


# ── 7. house-speak errors to a log; /v1/ask reports a silent line ──────────────────────────────────────────────

class _Proc:
    def __init__(self, rc, pid=4242):
        self.rc, self.pid = rc, pid

    def poll(self):
        return self.rc


def _log_globals(tmp_path, on=True, maximum=1_000_000):
    import os
    import subprocess
    import time
    return dict(SPEAK_LOG_ON=on, SPEAK_LOG=tmp_path / "logs" / "house-speak.err", SPEAK_LOG_MAX=maximum,
                subprocess=subprocess, os=os, time=time, _SPEAK_LOG_AT={})


def test_speak_failed_fast_reports_a_quick_failure_only(tmp_path):
    g = _log_globals(tmp_path)
    g["speak_log_tail"] = _fn("speak_log_tail", **g)
    ff = _fn("_speak_failed_fast", **g)
    assert "could not speak (exit 1)" in ff(_Proc(1), 0.2)
    assert ff(_Proc(None), 0.2) == ""
    assert ff(_Proc(0), 0.2) == ""
    assert ff(_Proc(1), 0) == ""                                 # the house: no wait, no check


def test_speak_log_opens_rotates_and_falls_back(tmp_path):
    import subprocess
    g = _log_globals(tmp_path, maximum=10)
    op = _fn("_speak_log_open", **g)
    f, at = op()
    assert at == 0 and (tmp_path / "logs" / "house-speak.err").read_text().startswith("--- ")
    f.write(b"x" * 50)
    f.close()
    f2, at2 = op()
    f2.close()
    assert (tmp_path / "logs" / "house-speak.err.1").stat().st_size > 10 and at2 == 0
    assert _fn("_speak_log_open", **_log_globals(tmp_path, on=False))() == (subprocess.DEVNULL, -1)
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("")
    g3 = dict(_log_globals(tmp_path), SPEAK_LOG=blocked / "logs" / "house-speak.err")
    assert _fn("_speak_log_open", **g3)() == (subprocess.DEVNULL, -1)


def test_speak_log_tail_reads_after_the_offset(tmp_path):
    g = _log_globals(tmp_path)
    log = g["SPEAK_LOG"]
    log.parent.mkdir(parents=True)
    log.write_text("old line\n--- 2026-10-04 line\nconnect: refused\ntts fail: cosy down\n")
    g["_SPEAK_LOG_AT"][7] = len("old line\n")
    assert _fn("speak_log_tail", **g)(7) == "tts fail: cosy down"
    assert _fn("speak_log_tail", **g)(8) == ""


def test_spawn_keeps_its_own_session_and_ask_takes_check_s():
    i = SRC.index("def _spawn_speak(")
    assert "start_new_session=True" in SRC[i:i + 2500]
    assert "def connect_ask(text: str, who: str, by: str, check_s: float = 0.0, speak: bool = True) -> dict:" in SRC   # speak: 10-18 phone
    assert SRC.count("connect_ask(text, who, by, check_s=ASK_SPEAK_CHECK_S)") == 2
    assert 'SPEAK_LOG_ON = os.environ.get("CRISSA_SPEAK_LOG", house_default("0", "1"))' in SRC
    assert 'ASK_SPEAK_CHECK_S = env_seconds("CRISSA_ASK_SPEAK_CHECK_S", house_default("0", "3"))' in SRC


# ── follow-up 2: no speech check, no `spoke` claim (X-hi review 2026-10-04) ────────────────────────────────────

def _ask_globals(rc):
    import threading
    import time
    import types

    class _Agent:
        class AgentError(Exception):
            pass

        @staticmethod
        def load():
            return {"url": "http://x/v1"}

        @staticmethod
        def speakable(reply):
            return reply

    class _Conv:
        def ask(self, text, cfg, **kw):
            return "Hello."

    class _Lane:                                   # the speech lane (2026-10-06): say() spawns the line
        def say(self, line, who, queue=False):
            return _Proc(rc)

        def begin_turn(self):
            pass

        def end_turn(self):
            pass

    sys.path.insert(0, str(ROOT / "lib"))
    import crissa_speechq

    talk = types.SimpleNamespace(load=lambda: {"reply": {"max_chars": 0, "stream_sentences": True,
                                                         "min_sentence_chars": 12, "working_filler": []}},
                                 keep=lambda t, n, label: (t, None))
    return dict(time=time, crissa_agent=_Agent, CONNECT={"conv": _Conv(), "lock": threading.Lock(), "last": None},
                LANE=_Lane(), crissa_talk=talk, crissa_speechq=crissa_speechq, note_cut=lambda *a: None,
                say_stop_all=lambda: None, _spawn_speak=lambda line, who: _Proc(rc), action=lambda *a: None,
                _speak_failed_fast=_fn("_speak_failed_fast", time=time,
                                       speak_log_tail=lambda pid: "cosy refused", SPEAK_LOG="x"))


def test_connect_ask_leaves_spoke_out_when_no_check_ran():
    rec = _fn("connect_ask", **_ask_globals(rc=1))("hi", "crissa", "t")            # the house: check_s 0
    assert "spoke" not in rec and rec["error"] is None and rec["reply"] == "Hello."


def test_connect_ask_reports_spoke_when_the_check_ran():
    assert _fn("connect_ask", **_ask_globals(rc=0))("hi", "crissa", "t", check_s=0.2)["spoke"] is True
    rec = _fn("connect_ask", **_ask_globals(rc=1))("hi", "crissa", "t", check_s=0.2)
    assert rec["spoke"] is False and "could not speak" in rec["error"]


# ── follow-up 3: a bad CRISSA_ASK_SPEAK_CHECK_S falls back to the default (X-hi review 2026-10-04) ─────────────

@pytest.mark.parametrize("raw, want", [
    (None, 3.0), ("1.5", 1.5), ("0", 0.0), ("", 0.0),
    ("abc", 3.0), ("-2", 3.0), ("nan", 3.0), ("inf", 3.0),
])
def test_env_seconds_parses_or_falls_back(monkeypatch, raw, want):
    import os
    if raw is None:
        monkeypatch.delenv("CRISSA_ASK_SPEAK_CHECK_S", raising=False)
    else:
        monkeypatch.setenv("CRISSA_ASK_SPEAK_CHECK_S", raw)
    assert _fn("env_seconds", os=os)("CRISSA_ASK_SPEAK_CHECK_S", "3") == want


# ── follow-up 4: on the house the conductor IS the house, and her line output stays on /dev/null ───────────────

def test_the_conductor_reads_is_house_true_in_the_house_tree():
    """The conductor's own lines up to IS_HOUSE, run alone in a fresh process from this tree (2026-10-04)."""
    import json
    import os
    tree = ast.parse(SRC)
    want = []
    for n in tree.body:
        seg = ast.get_source_segment(SRC, n) or ""
        if seg.startswith(("ROOT = ", "sys.path.insert(0, str(ROOT / \"lib\"))", "import crissa_home",
                           "IS_HOUSE = ")):
            want.append(seg)
        if seg.startswith("IS_HOUSE = "):
            break
    code = ("import sys\nfrom pathlib import Path\n__file__ = %r\n" % str(ROOT / "scripts" / "house-conductor.py")
            + "\n".join(want) + "\nprint(IS_HOUSE)\n")
    env = {k: v for k, v in os.environ.items() if k != "CRISSA_HOME"}
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, cwd="/")
    house = "state_home" in json.loads((ROOT / "avatars" / "crissa" / "manifest.json").read_text())
    assert len(want) == 4, want
    assert out.stdout.strip() == str(house), out.stderr[-400:]


def test_spawn_speak_keeps_dev_null_on_the_house(tmp_path):
    import os
    import time
    import types
    hd = _fn("house_default", IS_HOUSE=True)
    on = os.environ.get("CRISSA_SPEAK_LOG", hd("0", "1")).strip() != "0"
    assert on is False or "CRISSA_SPEAK_LOG" in os.environ
    seen = {}

    class _P:
        pid = 99

    def popen(cmd, **kw):
        seen.update(kw)
        return _P()
    sp = types.SimpleNamespace(DEVNULL=-3, STDOUT=-2, Popen=popen)
    g = dict(sys=sys, os=os, time=time, ROOT=ROOT, subprocess=sp, speak_timing_env=lambda: {},
             crissa_talk=types.SimpleNamespace(speak_env=lambda: {}),
             reap_wedged_speakers=lambda: None, _SPEAKERS=[], _SPEAK_AT={}, _SPEAK_LOG_AT={},
             _ALIGN_FIRST={})          # the lip-sync test line's marks (NN, 2026-10-11); none on the house
    g["_speak_log_open"] = _fn("_speak_log_open", SPEAK_LOG_ON=False, SPEAK_LOG=tmp_path / "x.err",
                               SPEAK_LOG_MAX=1, subprocess=sp, os=os, time=time)
    _fn("_spawn_speak", **g)("hello", "crissa")
    assert seen["stdout"] == sp.DEVNULL and seen["stderr"] == sp.DEVNULL and seen["stdin"] == sp.DEVNULL
    assert seen["start_new_session"] is True and g["_SPEAK_LOG_AT"] == {}
    assert not (tmp_path / "x.err").exists()
