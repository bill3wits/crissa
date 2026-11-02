"""A true first start runs Setup with no switch (the User 2026-10-29: "why would it need the flag if it was your first
time"): no init.json in a pack's state home turns the Init switch on; a finished or cancelled Setup, an explicit
CRISSA_INIT=0, the house and the kill switch do not."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))


def _launcher():
    spec = importlib.util.spec_from_file_location("crissa_launcher_first_start", ROOT / "scripts" / "crissa.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _home(tmp_path, monkeypatch, house=False):
    import crissa_home
    monkeypatch.setattr(crissa_home, "HOME", tmp_path)
    monkeypatch.setattr(crissa_home, "is_house", lambda *a, **k: house)
    return tmp_path


def test_no_setup_record_is_a_first_start(tmp_path, monkeypatch):
    L = _launcher()
    _home(tmp_path, monkeypatch)
    assert L.first_start({}) is True


def test_a_finished_or_cancelled_setup_an_explicit_off_and_the_kill_are_not(tmp_path, monkeypatch):
    L = _launcher()
    h = _home(tmp_path, monkeypatch)
    assert L.first_start({"CRISSA_INIT": "0"}) is False
    assert L.first_start({"CRISSA_INIT": "1"}) is False                 # already on: nothing to turn on
    assert L.first_start({"CRISSA_INIT_ON_FIRST_START": "0"}) is False
    (h / "init.json").write_text(json.dumps({"done": False, "cancelled_at": "x"}))
    assert L.first_start({}) is False


def test_the_house_never(tmp_path, monkeypatch):
    L = _launcher()
    _home(tmp_path, monkeypatch, house=True)
    assert L.first_start({}) is False


def test_a_test_run_never_turns_it_on_in_the_suites_environment(tmp_path, monkeypatch):
    L = _launcher()
    _home(tmp_path, monkeypatch)
    assert L.first_start() is False


def test_main_turns_it_on_for_the_unit_too_and_acceptance_keeps_the_old_start():
    src = (ROOT / "scripts" / "crissa.py").read_text(encoding="utf-8")
    i = src.index("if not a.uninstall and (not a.service or a.service == \"install\") and first_start():")
    assert 'PERSON_ENV["CRISSA_INIT"] = "1"' in src[i:i + 400]
    assert i < src.index('    if a.uninstall or a.service:\n        return service_cmd(')
    acc = (ROOT / "scripts" / "public-loops-accept.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("CRISSA_INIT_ON_FIRST_START", "0")' in acc


def test_an_unreadable_or_unfinished_record_is_a_first_start_and_an_old_cancel_is_not(tmp_path, monkeypatch):
    L = _launcher()
    h = _home(tmp_path, monkeypatch)
    (h / "init.json").write_text("{not json")
    assert L.first_start({}) is True
    (h / "init.json").write_text(json.dumps({"done": False}))
    assert L.first_start({}) is True
    (h / "init.json").write_text(json.dumps({"done": True}))
    assert L.first_start({}) is False
