"""The clean-user fixes (crissatest 2026-10-06): a fresh account on a Linux machine, no house anywhere.

Kokoro is the unnamed default when it answers; the voice log names the engine that spoke; Linux is not told
to use Homebrew; the voice and oracle services follow the port table and say so when a port is taken; the
oracle says why its face engine is missing and does not flood the log without a camera; a slow endpoint is
visible, never cut off.
"""
from __future__ import annotations

import ast
import importlib.util
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "modules" / "voice"
ORACLE = ROOT / "modules" / "oracle"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(VOICE))
sys.path.insert(0, str(ORACLE))

import crissa_tts as T  # noqa: E402
from crissa_oracle import camera, face  # noqa: E402
from crissa_voice import asr_setup, kokoro_setup  # noqa: E402

SRC = (ROOT / "scripts" / "house-conductor.py").read_text()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- 1. the voice default ------------------------------------------------------------------------------------

def test_kokoro_speaks_when_nothing_ahead_answers_and_when_named(monkeypatch, tmp_path):
    """2026-10-08: with no saved pick, Kokoro speaks only after Turbo, Audio8 and Pocket do not answer.
    Naming it still speaks it. It is not her voice on a Turbo or Audio8 chain (those chains omit it)."""
    monkeypatch.setattr(T, "kokoro_up", lambda e=None, timeout=0.3: True)
    monkeypatch.setattr(T, "_house", lambda: False)
    monkeypatch.setattr(T, "_platform", lambda: "linux")
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: False)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: False)
    assert T.config(env={}, home=tmp_path).get("engine") == "kokoro", "nothing ahead answers: Pocket's backup speaks"
    assert T.config(env={"CRISSA_TTS": "kokoro"}, home=tmp_path)["engine"] == "kokoro"


def test_without_a_voice_a_linux_pack_is_text_only_or_a_named_piper(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "kokoro_up", lambda e=None, timeout=0.3: False)
    monkeypatch.setattr(T, "stream_up", lambda name, e=None: False)
    monkeypatch.setattr(T, "_house", lambda: False)
    monkeypatch.setattr(T, "_platform", lambda: "linux")
    assert T.config(env={}, home=tmp_path) == {}
    model = tmp_path / "v.onnx"
    model.write_bytes(b"x")
    got = T.config(env={"CRISSA_TTS": "piper", "CRISSA_TTS_MODEL": str(model)}, home=tmp_path)
    assert got["engine"] == "piper" and got["model"] == str(model)


def test_the_house_never_takes_the_kokoro_default(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "kokoro_up", lambda e=None, timeout=0.3: True)
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda manifest=None: True)
    monkeypatch.setattr(T, "_house", lambda: True)
    assert T.config(env={}, home=tmp_path) == {}


def test_the_voice_log_names_the_engine_the_seam_chose():
    src = (ROOT / "scripts" / "house-speak.py").read_text()
    i = src.index("engine = pick_engine(args.engine)")
    block = src[i:i + 700]
    assert "tts_cfg = _crissa_tts_cfg()" in block
    assert 'engine = str(tts_cfg.get("engine") or "none")' in block
    assert block.index("tts_cfg = _crissa_tts_cfg()") < block.index('print(f"voice={voice} sink={sink} engine={_LINE[\'engine\']}"')


# ---- 3. per-OS advice ----------------------------------------------------------------------------------------

def test_linux_is_never_told_to_use_homebrew():
    for mod in (asr_setup, kokoro_setup):
        # 2026-10-07: the installer makes its own Python (pyenv.py), so no OS is told to fetch one
        for plat, mach in (("linux", "x86_64"), ("darwin", "arm64"), ("win32", "amd64")):
            said = mod.unsupported((3, 14), plat, mach)
            assert "brew" not in said and "python3.12" not in said and "uv venv" not in said, (mod.__name__, plat)
        assert mod.unsupported((3, 12), "linux", "x86_64") == ""


# ---- 4. ports ------------------------------------------------------------------------------------------------

def test_stt_asr_and_oracle_follow_the_port_table():
    stt = _load("crissa_stt_ports", VOICE / "scripts" / "crissa-stt.py")
    asr_src = (VOICE / "scripts" / "crissa-asr.py").read_text()
    orc_src = (ORACLE / "scripts" / "crissa-oracle.py").read_text()
    assert stt._port("stt", {}) == 8190 and stt._port("asr", {}) == 8096 and stt._port("player", {}) == 8100
    off = {"CRISSA_PORT_OFFSET": "1000"}
    assert (stt._port("stt", off), stt._port("asr", off), stt._port("player", off)) == (9190, 9096, 9100)
    assert stt._port("stt", {"CRISSA_PORT_STT": "7001", **off}) == 7001
    assert stt._port("oracle", {"CRISSA_ORACLE_PORT": "7002"}) == 7002
    assert 'default=_port("asr")' in asr_src and 'default=_port("oracle")' in orc_src


def test_a_taken_port_is_one_line_not_a_traceback(capsys):
    stt = _load("crissa_stt_bind", VOICE / "scripts" / "crissa-stt.py")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert stt.main(["--port", str(port), "--no-mic"]) == 2
    finally:
        s.close()
    err = capsys.readouterr().err
    assert "port %d is already in use" % port in err and "--port N" in err and "CRISSA_PORT_STT" in err
    assert "Traceback" not in err


# ---- 6. the oracle -------------------------------------------------------------------------------------------

def test_no_camera_backs_off_and_says_so_once(monkeypatch, capsys):
    opened = []

    class Cap:
        def __init__(self, idx):
            opened.append(idx)

        def isOpened(self):
            return False

        def release(self):
            pass

    fake = type(sys)("cv2")
    fake.VideoCapture = Cap
    monkeypatch.setitem(sys.modules, "cv2", fake)
    now = [100.0]
    cam = camera.Camera(0, clock=lambda: now[0])
    for _ in range(8):                      # eight looks inside one second: one open
        assert cam.read() is None
        now[0] += 0.1
    assert len(opened) == 1
    now[0] += 2.0
    cam.read()
    assert len(opened) == 2
    for _ in range(400):                    # ten minutes of looks: the wait doubles up to 30 s, never stops
        now[0] += 1.5
        cam.read()
    assert 2 < len(opened) < 40
    assert capsys.readouterr().err.count("no camera at index 0") == 1


def test_a_face_engine_that_will_not_import_says_why():
    why = face.import_reason(OSError("PortAudio library not found"))
    assert "sounddevice" in why and "libportaudio2" in why
    assert "not installed" in face.import_reason(ModuleNotFoundError("No module named 'mediapipe'"))
    from crissa_oracle import service
    assert "engine_why" in service.Oracle(cam=type("C", (), {"read": lambda s: None, "close": lambda s: None})())._blank()


# ---- 7. a slow endpoint is visible ---------------------------------------------------------------------------

def _fn(name: str, **globs):
    node = next(n for n in ast.parse(SRC).body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = dict(globs)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "house-conductor.py", "exec"), ns)
    return ns[name]


def test_a_slow_endpoint_is_named_while_it_lasts():
    connect = {"asking": None}
    w = _fn("connect_waiting", CONNECT=connect, ASK_WAIT_NOTE_S=5.0, time=time)
    assert w() is None
    connect["asking"] = {"since": 1000.0, "asked": "what time is it"}
    assert w(now=1003.0) is None
    got = w(now=1046.0)
    assert got["s"] == 46 and "Still waiting on your endpoint" in got["note"]
    assert w(now=1001.0, after_s=0.0)["s"] == 1


def test_the_wait_is_shown_and_nothing_times_out():
    i = SRC.index("def connect_ask(")
    body = SRC[i:SRC.index("\ndef ", i + 5)]          # the whole function: it streams sentence by sentence (2026-10-06)
    assert 'CONNECT["asking"] = {"since": time.time()' in body and 'CONNECT["asking"] = None' in body
    assert "timeout" not in body
    assert '"agent_waiting": connect_waiting()' in SRC
    page = (ROOT / "display" / "crissa-wall.html").read_text()
    assert "s.agent_waiting" in page and 'msg(j.note || "Asked.")' in page


# ---- 2/5/9. the README -----------------------------------------------------------------------------------------

def test_the_readme_says_venv_uv_init_and_no_open():
    src = (ROOT / "scripts" / "crissa-export.py").read_text()
    for need in ("python3 -m venv .venv", "uv venv --python 3.12", "Python 3.9 to 3.12", "--no-open",
                 "CRISSA_INIT=1", "## Her voice"):
        assert need in src, need
    assert "--port N" in (VOICE / "README.md").read_text()
