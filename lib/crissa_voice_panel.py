"""The Control Panel's Voices section (2026-10-06, the User): one tile per optional voice, its state in plain words.

    Chatterbox     Turbo: on (answering) / off, the GPU it runs on, and why it is not speaking when it is not
                   (not installed and how to get it, its self-test failed and why, this machine cannot carry it).
    Multilingual   Chatterbox Multilingual V3: its own tile once installed (on/off, language, GPU, failure named);
                   before that, only as an offer when Init found a language other than English.
    Pocket         Pocket TTS on the CPU: on / off / not installed.
    Audio8         the announcer on robust machines (Audio8 TTS, CPU): on/off, its voice, the announcer's order, the
                   failure named; its log and a Restart key.
    Keep-warm      the active GPU voice's keep-warm: on/off, last warm, failure named; its log and a Run now key.

It never installs a voice. Two keys act (action()): Restart on Audio8 and Run now on the keep-warm; log() shows a
tile's log. Everything else is read-only (the launcher starts the voices in Init's chain). The marks are
lib/crissa_status.py's: ● ok, ◐ fallback/attention, ○ off. Never raises: a broken voice module is a tile that says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _deps():
    import crissa_tts as T
    import crissa_voice_tiers as VT
    return T, VT


def _gpu(rec: dict, st: dict | None) -> str:
    if st and st.get("gpu"):
        return "%s (sm_%s)" % (st["gpu"], str(st.get("cap") or "").replace(".", "")) if st.get("cap") else st["gpu"]
    facts = (rec or {}).get("facts")
    if not facts:
        return "not probed yet (Init has not run)"
    g = facts.get("gpu") or {}
    if g.get("kind") == "cuda":
        return "%s, %.0f GB free at Init" % (g.get("name"), g.get("free_gb") or 0)
    return "no NVIDIA GPU"


def _self_test(engine: str, VT) -> dict | None:
    m = VT._voice_mod(engine + "_setup")
    try:
        st = (m.record() or {}).get("self_test") if m is not None and hasattr(m, "record") else None
    except Exception:  # noqa: BLE001
        st = None
    return st if isinstance(st, dict) else None


def tile(engine: str, title: str, rec: dict, up=None, installed=None, VT=None, T=None, held=None) -> dict:
    """{"id", "label", "state": ok|warn|bad|off, "mark", "note", "gpu"?, "picked", "install"?}. held: the engines the
    launcher holds (round 17, lib/crissa_voice_demand.held_engines): off, "installed, not running", never red."""
    if VT is None or T is None:
        T, VT = _deps()
    up = up if up is not None else (lambda e: T.audio8_up() if e == "audio8" else T.stream_up(e))
    installed = installed if installed is not None else VT.installed
    tab = VT.table()
    t = tab["tiers"].get(engine) or {}
    out = {"id": engine, "label": title, "picked": (rec or {}).get("main") == engine,
           "in_chain": engine in ((rec or {}).get("chain") or [])}
    st = _self_test(engine, VT) if engine in ("turbo", "multilingual", "audio8") else None
    if (t.get("needs") or {}).get("gpu") == "cuda":
        out["gpu"] = _gpu(rec, st)
    have = installed(engine)
    unmet = ((rec or {}).get("passed_over") or {}).get(engine) or []
    if have is False:
        out.update(state="off", mark="○", install=t.get("install") or "",
                   note="not installed" + ("; " + "; ".join(unmet) if unmet else "") +
                   (" - install: %s" % t.get("install") if t.get("install") and not unmet else ""))
        return out
    if st and st.get("ok") is False:
        out.update(state="bad", mark="◐", note="self-test failed: %s" % st.get("why"))
        return out
    try:
        on = bool(up(engine))
    except Exception:  # noqa: BLE001
        on = False
    if on:
        out.update(state="ok", mark="●", note="on" + (" - Init's pick" if out["picked"] else " - stand-in"))
    elif engine in (held or ()):
        out.update(state="off", mark="○", standby=True,
                   note="installed, not running - no role uses it now; a line that needs it starts it")
    elif unmet:
        out.update(state="off", mark="○", note="off - " + "; ".join(unmet))
    else:
        out.update(state="off", mark="○", note="off - installed; " + (
            "the launcher starts it with the Wall" if out["in_chain"] else "not in this machine's voice chain"))
    return out


def view(home: Path | None = None, up=None, installed=None) -> dict:
    """{"section": "Voices", "tiles": [...], "language": {...}} for the panel; {"error"} if the voice code is absent."""
    try:
        T, VT = _deps()
    except Exception as e:  # noqa: BLE001
        # a panel installed without the voice code shows no Voices section; it is not a fault
        return {"section": "Voices", "tiles": [], "absent": "no voice tier code in this install (%s)" % e}
    rec = VT.read(home) or {}
    lang = (rec.get("facts") or {}).get("language") or {}
    inst = installed if installed is not None else VT.installed
    held = _held(home)
    tiles = [tile("turbo", "Chatterbox", rec, up, inst, VT, T, held)]
    if inst("multilingual") is not False:
        m = tile("multilingual", "Chatterbox Multilingual V3", rec, up, inst, VT, T, held)
        m["language"] = lang.get("code") or "en"
        tiles.append(m)
    elif lang.get("code") not in (None, "", "en"):
        t = VT.table()["tiers"].get("multilingual") or {}
        tiles.append({"id": "multilingual", "label": "Chatterbox Multilingual V3", "state": "off", "mark": "○",
                      "offer": True, "install": t.get("install") or "", "language": lang.get("code"),
                      "note": "offered for %s (your language, from %s): %s" % (
                          lang.get("name"), lang.get("from"), "; ".join((rec.get("passed_over") or {}).get(
                              "multilingual") or []) or "opt in: %s" % t.get("install"))})
    tiles.append(tile("pocket", "Pocket", rec, up, inst, VT, T, held))
    if not (hasattr(VT, "not_a_voice") and VT.not_a_voice("audio8")):   # Audio8 is her ear, not a voice (10-17)
        tiles.append(audio8_tile(rec, up, inst, VT, T, held))
    announcer_titles(tiles, rec)
    try:
        import crissa_voice_warm as W
        tiles.append(W.view(home))
    except Exception as e:  # noqa: BLE001
        tiles.append({"id": "warm", "label": "Keep-warm", "state": "bad", "mark": "◐", "note": "keep-warm broken: %s" % e})
    return {"section": "Voices", "tiles": tiles, "language": lang, "main": rec.get("main") or ""}


def _held(home: Path | None = None) -> set:
    """The voices the running launcher holds, "installed, not running" (round 17); none off a pack or on error."""
    try:
        import crissa_voice_demand
        return crissa_voice_demand.held_engines(home)
    except Exception:  # noqa: BLE001  the panel never fails on it
        return set()


def announcer_titles(tiles: list, rec: dict) -> list:
    """"(announcer)" goes on the tile of the voice that IS the announcer here - the first of the announcer's order -
    and on no other (the User 2026-10-10, .101 round 4: the tile said "Audio8 (announcer)" while Pocket announced)."""
    chain = list((rec or {}).get("announcer_chain") or [])
    first = chain[0] if chain else ""
    for t in tiles:
        is_ann = bool(first) and t.get("id") == first
        t["announcer"] = is_ann
        if is_ann and "(announcer)" not in str(t.get("label") or ""):
            t["label"] = "%s (announcer)" % t.get("label")
    return tiles


def audio8_tile(rec: dict, up=None, installed=None, VT=None, T=None, held=None) -> dict:
    """Audio8's tile: the plain tile plus its voice, the announcer's order here, and the named failure. Its title says
    "(announcer)" only when it is the announcer here (announcer_titles)."""
    if VT is None or T is None:
        T, VT = _deps()
    out = tile("audio8", "Audio8", rec, up, installed, VT, T, held)
    chain = list((rec or {}).get("announcer_chain") or [])
    out["announcer_chain"] = chain
    out["actions"] = ["log", "restart"] if out.get("state") != "off" or not out.get("install") else ["log"]
    if out.get("standby"):
        out["actions"] = ["log"]     # held by the launcher (round 17): a line that needs it starts it, not this key
    m = VT._voice_mod("audio8_setup")
    r = {}
    try:
        r = m.record() if m is not None else {}
    except Exception:  # noqa: BLE001
        r = {}
    voice = r.get("voice") or ""
    try:
        voice = voice or m.voice_name()
    except Exception:  # noqa: BLE001
        pass
    out["voice"] = voice
    why = [str(v) for v in (r.get("voice_why") or {}).values() if v] if isinstance(r.get("voice_why"), dict) else []
    if why and out.get("state") == "ok":
        out.update(state="warn", mark="◐")
    if why:
        out["note"] += "; " + "; ".join(why)
    if chain:
        out["note"] += " - announcer order here: " + " → ".join(chain)
        if chain[0] != "audio8" and out.get("state") != "off":
            out["note"] += " (Audio8 is not first: %s)" % "; ".join(
                ((rec or {}).get("announcer_passed") or {}).get("audio8") or ["not capable here"])
    return out


def _log_file(tile_id: str) -> Path | None:
    if tile_id == "warm":
        import crissa_voice_warm as W
        return W.log_path()
    if tile_id == "audio8":
        _T, VT = _deps()
        m = VT._voice_mod("audio8_setup")
        return Path(m.LOG) if m is not None else None
    return None


def log(tile_id: str, lines: int = 120) -> dict:
    """The last `lines` of a tile's log: {"id", "path", "text"} or {"error"}. Never raises."""
    try:
        f = _log_file(tile_id)
    except Exception as e:  # noqa: BLE001
        return {"id": tile_id, "error": "no log: %s" % e}
    if f is None:
        return {"id": tile_id, "error": "no log for this tile"}
    try:
        text = "\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(lines)):])
    except OSError:
        return {"id": tile_id, "path": str(f), "text": "", "error": "nothing logged yet (%s)" % f}
    return {"id": tile_id, "path": str(f), "text": text}


def action(tile_id: str, act: str, run=None) -> dict:
    """Restart (Audio8) or Run now (keep-warm). {"ok", "out"} or {"ok": False, "why"}. run(argv) is for tests."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    if tile_id == "audio8" and act == "restart":
        _T, VT = _deps()
        if VT._voice_mod("audio8_setup") is None:
            return {"ok": False, "why": "Audio8 is not in this install"}
        vdir = Path(__import__("os").environ.get("CRISSA_VOICE_DIR") or (root / "modules" / "voice"))
        argv = [sys.executable, str(vdir / "scripts" / "crissa-audio8.py"), "--restart"]
    elif tile_id == "warm" and act == "run":
        argv = [sys.executable, str(root / "scripts" / "crissa-voice-warm.py"), "--once"]
    else:
        return {"ok": False, "why": "no %r key on the %r tile" % (act, tile_id)}
    try:
        if run is not None:
            rc, out = run(argv)
        else:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            rc, out = p.returncode, (p.stdout + p.stderr)[-2000:]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
    return {"ok": rc == 0, "out": out, **({} if rc == 0 else {"why": (out.strip().splitlines() or ["exit %d" % rc])[-1]})}
