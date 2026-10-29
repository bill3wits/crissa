"""Round 13 (2026-10-12, .101): the picture and the voice first, the listeners on a few threads, the panel's unit
queries cached. On .101 while she spoke: asr 677 %, stt 200 %, presence 192 %; the player's composite 14 -> 33-38 ms with
40-55 % of the mouth rows lost; the user systemd manager at 91 % from the Control Panel's per-poll unit listing."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ASR = _load("crissa_asr_r13", ROOT / "modules" / "voice" / "scripts" / "crissa-asr.py")


# ---- the ASR: a few threads, no spinning, one decode at a time ---------------------------------------------------
@pytest.mark.parametrize("cpus,want", [(4, 2), (8, 2), (16, 4), (32, 4), (64, 4)])
def test_the_asr_takes_a_quarter_of_the_cpus_two_to_four(cpus, want):
    assert ASR.asr_threads({}, cpus=cpus) == want


def test_the_asr_thread_count_is_a_setting_and_zero_is_the_kill():
    assert ASR.asr_threads({"CRISSA_ASR_THREADS": "6"}, cpus=16) == 6
    assert ASR.asr_threads({"CRISSA_ASR_THREADS": "0"}, cpus=16) == 0


class _Opts:
    def __init__(self):
        self.entries = {}

    def add_session_config_entry(self, k, v):
        self.entries[k] = v


def _fake_ort():
    made = []
    ort = types.SimpleNamespace(
        SessionOptions=_Opts,
        GraphOptimizationLevel=types.SimpleNamespace(ORT_ENABLE_ALL="all"),
        ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL="seq"),
        get_available_providers=lambda: ["CPUExecutionProvider"],
        InferenceSession=lambda path, sess_options=None, providers=None: made.append((path, sess_options, providers))
        or ("session", path))
    return ort, made


def test_the_capped_options_stop_spinning_and_use_one_inter_op_thread():
    ort, _ = _fake_ort()
    o = ASR.capped_session_options(ort, 4)
    assert o.intra_op_num_threads == 4 and o.inter_op_num_threads == 1 and o.execution_mode == "seq"
    assert o.entries == {"session.intra_op.allow_spinning": "0", "session.inter_op.allow_spinning": "0"}


def test_cap_runtime_replaces_the_models_load_session_without_editing_its_file():
    ort, made = _fake_ort()
    mod = types.SimpleNamespace(ort=ort, load_session=lambda p: "the model's own")
    assert ASR.cap_runtime(mod, 4) is True
    assert mod.load_session("/m/llm.onnx") == ("session", "/m/llm.onnx")
    path, opts, providers = made[0]
    assert opts.intra_op_num_threads == 4 and providers == ["CPUExecutionProvider"]
    mod2 = types.SimpleNamespace(ort=ort, load_session=lambda p: "own")
    assert ASR.cap_runtime(mod2, 0) is False and mod2.load_session("x") == "own"


def test_decodes_queue_one_at_a_time(monkeypatch):
    import threading
    import time as _t
    inside, most = [0], [0]
    guard = threading.Lock()

    class RT:
        def transcribe(self, **kw):
            with guard:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
            _t.sleep(0.05)
            with guard:
                inside[0] -= 1
            return "x"
    monkeypatch.setitem(ASR.RT, "rt", RT())
    monkeypatch.delenv("CRISSA_ASR_THREADS", raising=False)
    pcm = b"\x00\x01" * 16000
    ts = [threading.Thread(target=ASR.transcribe, args=(pcm, 16000)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert most[0] == 1


# ---- the lanes' shares ------------------------------------------------------------------------------------------
import crissa_service as S  # noqa: E402


def test_the_paint_and_the_voice_come_first_and_the_listeners_yield():
    assert S.lane_priority("player", {}) == (0, 400) and S.lane_priority("wall", {}) == (0, 400)
    assert S.lane_priority("pocket", {})[1] > S.lane_priority("asr", {})[1]
    for lane in ("asr", "presence", "oracle"):
        nice, weight = S.lane_priority(lane, {})
        assert nice > 0 and weight < 100
    assert S.lane_priority("stt", {}) is None            # the ear's capture keeps the defaults: it must not drop audio


def test_a_lane_unit_carries_its_share_and_the_switch_takes_it_off(tmp_path):
    t = S.desk_unit_text("asr", ["/py", "asr.py"], {}, home=tmp_path)
    assert "Nice=10\nCPUWeight=50\n" in t
    assert "Nice=0\nCPUWeight=400\n" in S.desk_unit_text("player", ["/py"], {}, home=tmp_path)
    assert "CPUWeight" not in S.desk_unit_text("stt", ["/py"], {}, home=tmp_path)
    assert "CPUWeight" not in S.desk_unit_text("asr", ["/py"], {"CRISSA_LANE_PRIORITY": "0"}, home=tmp_path)
    assert "Nice=" not in S.desk_unit_text("asr", ["/py"], {"CRISSA_LANE_PRIORITY": "0"}, home=tmp_path)


def test_no_lane_asks_for_a_negative_nice():
    """A user's units cannot go below 0: systemd would refuse to start the unit."""
    assert all(n >= 0 for n, _w in S.LANE_PRIORITY.values())


def test_process_mode_nices_a_background_lane():
    src = (ROOT / "scripts" / "crissa.py").read_text()
    body = src[src.index("    def _spawn_popen"):src.index("    def alive(self, name")]
    assert "crissa_service.lane_priority(name" in body and "os.nice(nice)" in body


# ---- the Control Panel: the unit queries are shared and cached ----------------------------------------------------
def test_the_os_power_view_takes_a_plan_already_made():
    import crissa_os_power as P
    pw = P.Power.__new__(P.Power)
    pw.read_snapshot = lambda: {}
    pw._read_json = lambda p: {}
    pw.report_path = Path("/nonexistent")
    pw.has_gpu = lambda: False
    pw.keep = {}
    pw.switch_name, pw.on_name = "Off", "On"
    pw.plan = lambda run=None: (_ for _ in ()).throw(AssertionError("the view made its own plan"))
    plan = {"stop": [{"unit": "crissa-asr.service"}], "jobs": [], "already_off": [], "units": [1, 2], "unknown": []}
    v = pw.panel_view(plan=plan)
    assert v["would_stop"] == ["crissa-asr.service"] and v["units"] == 2


def test_the_panel_makes_its_plan_once_a_minute_and_a_press_drops_it(monkeypatch):
    src = (ROOT / "scripts" / "crissa-panel-server.py").read_text()
    assert "POWER.panel_view(run, plan=cached_plan())" in src
    assert src.count("drop_unit_caches()") >= 3                      # defined + after /v1/action + after a voice key
    ns = {}
    start = src.index("_plan_cache: dict")
    end = src.index("_jobs: dict = {}")
    calls = []
    fake_power = types.SimpleNamespace(plan=lambda run: calls.append(1) or {"n": len(calls)})
    import os
    import threading
    import time
    ns.update({"os": os, "threading": threading, "time": time, "POWER": fake_power, "run": None,
               "_reg_cache": {"ts": 0.0, "reg": None}})
    exec(compile(src[start:end], "panel", "exec"), ns)
    assert ns["cached_plan"](now=1000.0) == {"n": 1}
    assert ns["cached_plan"](now=1030.0) == {"n": 1} and len(calls) == 1
    assert ns["cached_plan"](now=1061.0) == {"n": 2}
    ns["drop_unit_caches"]()
    assert ns["cached_plan"](now=1062.0) == {"n": 3}
    monkeypatch.setenv("CRISSA_PANEL_PLAN_TTL", "0")
    ns["cached_plan"](now=1063.0)
    ns["cached_plan"](now=1063.5)
    assert len(calls) == 5


def test_a_hidden_panel_page_asks_nothing():
    page = (ROOT / "display" / "crissa-panel.html").read_text()
    assert "function whenShown(f){ return function(){ if(!document.hidden) f(); }; }" in page
    assert "setInterval(whenShown(paint), 3000)" in page
    assert "setInterval(whenShown(powerPoll), 30000)" in page
    assert "setInterval(powerPoll, 5000)" not in page and "setInterval(paint, 3000)" not in page
