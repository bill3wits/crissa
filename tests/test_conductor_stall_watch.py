"""The stall watch is measure only and off by default (the User 2026-10-12); the quiet gc is on in a pack, off on the
house, and refreezes after what loads (round 15)."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "house-conductor.py").read_text(encoding="utf-8")


def test_the_watch_is_off_by_default_both_start_from_main():
    watch = SRC.split("def stall_watch_start() -> None:", 1)[1].split("\ndef ", 1)[0]
    quiet = SRC.split("def gc_quiet_start() -> None:", 1)[1].split("\ndef ", 1)[0]
    assert 'os.environ.get("HOUSE_CONDUCTOR_STALL_WATCH", "0")' in watch
    assert "if not gc_quiet_on():" in quiet
    main = SRC.split("def main() -> None:", 1)[1]
    assert "    stall_watch_start()\n    gc_quiet_start()\n" in main


def test_the_paint_reads_house_state_from_the_poller():
    conv = SRC.split("def house_in_conversation() -> bool:", 1)[1].split("\ndef ", 1)[0]
    paused = SRC.split("def house_paused() -> bool:", 1)[1].split("\ndef ", 1)[0]
    assert 'if _HOUSE_BG["run"]:\n        return bool(_HOUSE_BG["conv"])' in conv
    assert 'if _HOUSE_BG["run"]:\n        return bool(_HOUSE_BG["paused"])' in paused
    assert conv.index('_HOUSE_BG["run"]') < conv.index("urlopen")         # the poller wins before any GET
    assert 'HOUSE_CONDUCTOR_HOUSE_POLL_BG", "1"' in SRC
    assert "    house_poll_start()\n" in SRC.split("def main() -> None:", 1)[1]


def _quiet_ns(is_house):
    import ast
    import time
    tree = ast.parse(SRC)
    got = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("gc_quiet_on", "gc_refreeze_soon")]
    ns = {"os": __import__("os"), "time": time, "IS_HOUSE": is_house, "_GC_FREEZE": {"on": False, "due": 0.0, "why": ""}}
    exec(compile(ast.Module(body=got, type_ignores=[]), "src", "exec"), ns)
    return ns


def test_quiet_gc_is_on_in_a_pack_off_on_the_house_and_the_env_wins(monkeypatch):
    monkeypatch.delenv("HOUSE_CONDUCTOR_GC_QUIET", raising=False)
    assert _quiet_ns(False)["gc_quiet_on"]() is True
    assert _quiet_ns(True)["gc_quiet_on"]() is False
    monkeypatch.setenv("HOUSE_CONDUCTOR_GC_QUIET", "0")
    assert _quiet_ns(False)["gc_quiet_on"]() is False
    monkeypatch.setenv("HOUSE_CONDUCTOR_GC_QUIET", "1")
    assert _quiet_ns(True)["gc_quiet_on"]() is True


def test_a_clip_or_a_reload_asks_for_a_freeze_only_when_quiet():
    ns = _quiet_ns(False)
    ns["gc_refreeze_soon"]("clip work")
    assert ns["_GC_FREEZE"]["due"] == 0.0, "not quiet: nothing asked"
    ns["_GC_FREEZE"]["on"] = True
    ns["gc_refreeze_soon"]("clip work")
    assert ns["_GC_FREEZE"]["due"] > 0 and ns["_GC_FREEZE"]["why"] == "clip work"
    frames = SRC.split("    def frames(self, key: str)", 1)[1].split("\n    def ", 1)[0]
    assert 'gc_refreeze_soon("clip " + str(key))' in frames
    reload = SRC.split("def reload_debounced(ph, now", 1)[1].split("\ndef ", 1)[0]
    assert 'gc_refreeze_soon("a library reload")' in reload
    quiet = SRC.split("def gc_quiet_start() -> None:", 1)[1].split("\ndef ", 1)[0]
    assert "if time.time() - spoke < 3.0:" in quiet, "never while she speaks or pauses between sentences"
