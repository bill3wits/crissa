"""Init installs Crissa's voice (the User 2026-10-07, from the fresh-install test on Ubuntu).

A new user got a text-only Crissa and a terminal command. Now Init's machine step installs the voice the machine
can run, as a visible step with its progress, and falls down the chain when an install fails:

    the chain      lib/crissa_voice_tiers' record (Init's pick, best first): Turbo on a strong NVIDIA GPU, Audio8 on a
                   robust CPU machine, Pocket on a lighter CPU, the Mac's `say`, on-screen text. Nothing else is
                   picked here.
    her backup     when her voice's tier says installs_backup (Audio8), the next voice in her chain (Pocket) is
                   installed too, the same way; kept as `backup_install` in the record.
    each engine    already installed -> it speaks, done. Needs no install (say, text) -> it speaks, done. Else its
                   installer (the tier's `install` line, modules/voice/scripts/crissa-<engine>.py --install) runs
                   with this Python; the installer makes its own pinned env (modules/voice/crissa_voice/pyenv.py)
                   and every download goes through kill-search. A failure is kept with its short reason and the
                   next engine is tried.
    the record     <state home>/voice-tier.json gets `voice_install` {chosen, first, tried, at, line} and, when the chosen voice is
                   not Init's first pick, its main and chain start at the chosen one (picked_by stays "init").
    the launcher   a voice installed while the Wall runs is started through the launcher's request queue
                   (lib/crissa_procs), so she speaks without a restart.
    the announcer  then the same walk down the record's announcer_chain (the User 2026-10-07): Audio8 on a robust CPU
                   machine (the tier table's audio8.needs: RAM and cores, measured), then Pocket, Kokoro, say, text.
                   An announcer voice that is already there (Crissa's own Pocket, say) needs nothing; a failed Audio8
                   falls to the next. Kept as `announcer_install` in the record; CRISSA_INIT_ANNOUNCER_INSTALL=0 skips
                   it (the announcer then speaks with whatever is installed).

What the person reads is plain words, never a setting name or a command (line()): "Crissa's voice could not be
installed: <short reason>. She will speak in text for now; you can retry from the Connect page." The full output stays in
the log.

Kill switch: CRISSA_INIT_VOICE_INSTALL=0 (Init only picks, as before). Never on the house: the house runs its own
voices and never installs one here.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_voice_tiers as VT  # noqa: E402

try:
    import crissa_egress  # noqa: E402
except ImportError:  # pragma: no cover - ships beside it
    crissa_egress = None

SWITCH = "CRISSA_INIT_VOICE_INSTALL"
ANN_SWITCH = "CRISSA_INIT_ANNOUNCER_INSTALL"
ROOT = Path(__file__).resolve().parent.parent
NO_INSTALL = ("say", "text")          # speak with nothing to fetch (say: the Mac's own; text: the screen)
RETRY = "you can retry from the Connect page"     # its "Retry voice install" key (display/crissa-connect.html)
STATE_FILE = "voice-install.json"          # the install as it runs, for the Connect page (state, engine, lines, line)
STATE_LINES = 6
# THE PERSON'S PICK SURVIVES AN INSTALL (LL, .101 2026-10-11): an upgrade ran `crissa-voice-tier.py --install`, whose retry
# went back to Init's own first pick by probing the machine again - and that wrote over the person's Pocket pick with
# Audio8 and left "...voice is ready: Audio8 TTS." on the Connect page while she spoke Pocket. A retry now keeps a pick
# the person made (picked_by "user": --pick, Init's machine card) and installs THAT voice; only Init's own pick is made
# again. A --pick also rewrites the page's line (note_pick). CRISSA_VOICE_KEEP_PICK=0 is the old behaviour for both.
KEEP_PICK_SWITCH = "CRISSA_VOICE_KEEP_PICK"


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if VT.is_house():
        return False
    return str(e.get(SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def keep_pick_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(KEEP_PICK_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")


def announcer_enabled(env: dict | None = None) -> bool:
    """Install the announcer's voice: on, and krew is here (round 20, TWO VOICES WITHOUT KREW: no krew, no announcer)."""
    e = os.environ if env is None else env
    if not (enabled(e) and str(e.get(ANN_SWITCH, "1")).strip().lower() not in ("0", "off", "false", "no")):
        return False
    if str(e.get("CRISSA_CAMERA_VOICE", "1")).strip().lower() not in ("0", "off", "false", "no"):
        return True                              # round 23: THE CAMERA'S VOICE ships always (lib/crissa_init)
    try:
        import crissa_capability
        return not crissa_capability.two_voices(env=e)
    except Exception:  # noqa: BLE001  no capability module: as before
        return True


def label(engine: str, tab: dict | None = None) -> str:
    t = ((tab or VT.table())["tiers"].get(engine) or {})
    return {"say": "the Mac's own voice", "text": "text"}.get(engine) or str(t.get("label") or engine).split(" (")[0]


def installer_argv(engine: str, tab: dict | None = None, python: str | None = None,
                   env: dict | None = None) -> list[str] | None:
    """[python, <voice dir>/scripts/crissa-<engine>.py, --install] from the tier's `install` line; None = no installer.
    The voice module is looked for where the launcher looks (CRISSA_VOICE_DIR, else modules/voice here)."""
    e = os.environ if env is None else env
    line = str(((tab or VT.table())["tiers"].get(engine) or {}).get("install") or "")
    try:
        words = shlex.split(line)
    except ValueError:
        return None
    script = next((w for w in words if w.endswith(".py")), "")
    if not script:
        return None
    vdir = str(e.get("CRISSA_VOICE_DIR") or "").strip()
    p = Path(vdir) / script.split("modules/voice/", 1)[1] if vdir and "modules/voice/" in script else ROOT / script
    if not p.is_file():
        return None
    return [python or sys.executable, str(p)] + [w for w in words[words.index(script) + 1:]]


# Words that are for developers, not the person: setting names, interpreter commands, paths into the install.
_PAREN = re.compile(r"\s*\([^()]*(\b[A-Z][A-Z0-9]*_[A-Z0-9_]+|python3|`)[^()]*\)")   # a "(set X=0)" aside
_ENV = re.compile(r"\(?\b[A-Z][A-Z0-9]*_[A-Z0-9_]+(=\S*)?\)?")
_CMD = re.compile(r"`[^`]*`|\bpython3(\.\d+)?\b[^.;)]*|\buv (venv|pip)[^.;)]*|\bbrew install[^.;)]*")


def plain(why: str, limit: int = 160) -> str:
    """A short reason for the screen: its last meaningful line, no setting names, commands or tracebacks."""
    lines = [ln.strip() for ln in str(why or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith(("Traceback", "File ", "  ", "^"))] or lines
    s = lines[-1] if lines else ""
    s = re.sub(r"^[\w.-]+:\s+(?=\S)", "", s, count=1) if re.match(r"^crissa-[\w-]+:", s) else s
    s = re.sub(r"^(RuntimeError|PermissionError|Refused|OSError|ValueError):\s*", "", s)
    s = _CMD.sub("", _ENV.sub("", _PAREN.sub("", s)))
    s = re.sub(r"\s{2,}", " ", s).strip(" .;,:")
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s or "the install stopped without saying why"


def _stream(argv: list[str], progress) -> tuple[int, str]:
    """Run an installer, each output line to progress(); (exit code, its last lines)."""
    tail: list[str] = []
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                             env=dict(os.environ, PYTHONUNBUFFERED="1"))
    except OSError as e:
        return 127, "could not start the installer: %s" % e
    for line in p.stdout:                       # type: ignore[union-attr]
        line = line.rstrip()
        if not line:
            continue
        tail = (tail + [line])[-20:]
        progress(line)
    return p.wait(), "\n".join(tail)


def _killed() -> bool:
    try:
        return bool(crissa_egress and crissa_egress.killed())
    except Exception:  # noqa: BLE001
        return False


def her() -> str:
    """Her name as this install shows it (lib/crissa_home.her_name: Crissa in a pack). The voice lines below say it."""
    try:
        import crissa_home
        return crissa_home.her_name()
    except Exception:  # noqa: BLE001  a missing module only loses the house's own name
        return "Crissa"


def line(res: dict, tab: dict | None = None) -> str:
    """What the person reads after the install, in plain words."""
    chosen, first = res.get("chosen") or "text", res.get("first") or "text"
    failed = [t for t in res.get("tried") or [] if not t.get("ok")]
    why = failed[0]["why"] if failed else ""
    if first == "text":
        return VT.missing_line({"main": "text"}) or "No voice this machine can run: she will speak in text."
    if chosen == first and not failed:
        return "%s's voice is ready: %s." % (her(), label(chosen, tab))
    if chosen == "text":
        return "%s's voice could not be installed: %s. She will speak in text for now; %s." % (her(), why, RETRY)
    return ("%s could not be installed: %s. She will speak with %s for now; %s."
            % (label(failed[0]["engine"], tab), why, label(chosen, tab), RETRY))


def _home(home: Path | None = None) -> Path:
    return Path(home) if home is not None else VT._home(None)


def state(home: Path | None = None) -> dict:
    """The install as the Connect page shows it: {state: idle|installing|done|failed, engine, lines, line, at}."""
    try:
        import json
        d = json.loads((_home(home) / STATE_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"state": "idle"}
    except (OSError, ValueError):
        return {"state": "idle"}


def _put(home: Path | None, **kw) -> None:
    import json
    p = _home(home) / STATE_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        d = dict(state(home), **kw)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def running(home: Path | None = None, stale_s: float = 3600.0) -> bool:
    """An install is going (written in the last hour, by a process that is still alive): a second one is not started
    beside it. A launcher stopped mid-install (Ctrl-C) left "installing" behind, and for an hour every start skipped
    the install and the Connect page said "Installing her voice" while nothing was (2026-10-07): an install whose
    process is gone is not running."""
    st = state(home)
    if st.get("state") != "installing" or time.time() - float(st.get("t") or 0) >= stale_s:
        return False
    return _alive(st.get("pid"))


def note_pick(rec: dict, home: Path | None = None) -> None:
    """The Connect page says the person's own pick at once (LL, .101 2026-10-11: after `--pick pocket` the page still
    said "...voice is ready: Audio8 TTS." from the install before it). An install that is running keeps its line and
    says what it chose when it ends. Never raises."""
    if not keep_pick_on() or running(home):
        return
    eng = str((rec or {}).get("main") or "")
    if not eng:
        return
    try:
        have = VT.installed(eng)
    except Exception:  # noqa: BLE001  unknown: said as chosen, the page's Retry still offers the install
        have = None
    if eng == "text":
        said = VT.missing_line({"main": "text"}) or "No voice this machine can run: she will speak in text."
    elif have is False:
        said = "%s's voice is your choice: %s. It is not installed yet; %s." % (her(), label(eng), RETRY)
    else:
        said = "%s's voice is your choice: %s." % (her(), label(eng))
    _put(home, state="idle" if have is False else "done", engine=eng, line=said, lines=[], t=time.time(),
         at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))


def _alive(pid) -> bool:
    """True unless `pid` names a process that is gone (no pid recorded, an older state file: as before, True)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:                                  # someone else's process: it exists
        return True
    return True


def run(home: Path | None = None, *, progress=print, runner=None, installed=None, rec: dict | None = None,
        retry: bool = False, start=None) -> dict:
    """Install the best voice in the chain, falling down it on a failure. Returns
    {"ok", "chosen", "first", "tried": [{engine, ok, why}], "line", "at"}; never raises. The state file
    (<state home>/voice-install.json) follows it, for the Connect page."""
    shown: list[str] = []
    outer = progress

    def progress(text: str) -> None:            # noqa: F811  the caller's, plus the state file
        shown.append(plain(text, 120))
        del shown[:-STATE_LINES]
        _put(home, lines=list(shown), t=time.time())
        outer(text)
    _put(home, state="installing", engine="", lines=[], line="", t=time.time(), pid=os.getpid())
    try:
        res = _run(home, progress=progress, runner=runner, installed=installed, rec=rec, retry=retry, start=start)
    except Exception as e:  # noqa: BLE001  never raises: a broken run is a failed install, said plainly
        res = {"ok": False, "chosen": "text", "first": "", "tried": [],
               "line": "%s's voice could not be installed: %s. She will speak in text for now; %s."
               % (her(), plain("%s: %s" % (type(e).__name__, e)), RETRY)}
    _put(home, state="done" if res.get("ok") else "failed", engine=res.get("chosen"), line=res.get("line"),
         lines=list(shown), t=time.time(), at=res.get("at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return res


def _run(home, *, progress, runner, installed, rec, retry, start) -> dict:
    runner = runner or _stream
    installed = installed or VT.installed
    if retry:
        saved = VT.read(home)
        if saved and saved.get("picked_by") == "user" and keep_pick_on():
            rec = saved                            # the person's own pick: install THAT voice again (LL)
        else:
            rec = VT.ensure(home, reprobe=True)    # back to Init's own first pick, then try again
    rec = rec or VT.read(home) or VT.ensure(home)
    tab = VT.table(home)
    chain = [c for c in (rec.get("chain") or [rec.get("main") or "text"]) if c]
    if "text" not in chain:
        chain.append("text")
    first = chain[0]
    chosen, tried = _walk(chain, tab, progress, runner, installed, "%s's voice" % her())
    res = {"ok": chosen == first or (first == "text"), "chosen": chosen, "first": first, "tried": tried,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    res["line"] = line(res, tab)
    # HER BACKUP IS INSTALLED TOO when her voice's tier says so (installs_backup). Audio8 on a robust CPU machine
    # installs Pocket; Pocket on a weak machine or an 8 GB Mac installs Kokoro (the User 2026-10-08). The walk is the
    # rest of THIS chain, so a Turbo or Audio8 chain that does not contain Kokoro does not install it, even when the
    # install fell through to Pocket. A backup that was never installed backs nothing. A failure is said in plain
    # words and her voice stays the one chosen.
    bk = None
    if (tab["tiers"].get(chosen) or {}).get("installs_backup") and chosen in chain:
        rest = [c for c in chain[chain.index(chosen) + 1:] if c not in NO_INSTALL]
        if rest:
            b_chosen, b_tried = _walk(rest + ["text"], tab, progress, runner, installed, "%s's backup voice" % her())
            bk = {"chosen": b_chosen, "first": rest[0], "tried": b_tried, "at": res["at"]}
            res["backup"] = bk
            failed = [t for t in b_tried if not t.get("ok")]
            if failed:
                res["line"] += (" Her backup voice (%s) could not be installed: %s; %s speaks for her, and %s."
                                % (label(failed[0]["engine"], tab), failed[0]["why"], label(chosen, tab), RETRY))
    ann = None
    achain = [c for c in (rec.get("announcer_chain") or []) if c]
    if achain and announcer_enabled():
        if "text" not in achain:
            achain.append("text")
        # a voice whose install just failed for her (Audio8 is hers and the announcer's on a robust machine) is not
        # fetched a second time in the same run: the announcer goes on down its chain with the same reason
        failed = {t["engine"]: t["why"] for t in tried + list((bk or {}).get("tried") or []) if not t.get("ok")}
        a_chosen, a_tried = _walk(achain, tab, progress, runner, installed, "the announcer's voice", failed)
        ann = {"chosen": a_chosen, "first": achain[0], "tried": a_tried, "at": res["at"],
               "line": announcer_line(a_chosen, achain[0], a_tried, tab)}
        res["announcer"] = ann
        if ann["line"]:
            res["line"] += " " + ann["line"]
    _record(rec, res, chain, tab, home, ann, achain)
    use = _in_use(home)
    for eng, tr in ((chosen, tried), ((bk or {}).get("chosen"), (bk or {}).get("tried") or []),
                    ((ann or {}).get("chosen"), (ann or {}).get("tried") or [])):
        if use is not None and eng not in use:
            continue                       # her backup, installed, not started: a line that needs it starts it (round 17)
        if start is not None and eng and eng not in NO_INSTALL and any(t.get("ok") for t in tr):
            try:
                start(eng)
            except Exception:  # noqa: BLE001  the next start of Crissa starts it anyway
                pass
    return res


def _in_use(home) -> set | None:
    """The engines a role uses after this install (round 17, 2026-10-13, lib/crissa_voice_demand): only those are
    started here; a backup stays installed, not running. None = start every installed one (the switch off, the house,
    no pick to read: as before)."""
    try:
        import crissa_voice_demand as VD
        if not VD.on():
            return None
        return VD.in_use(VT.read(home), VD.named_engine(home=home))
    except Exception:  # noqa: BLE001  never stops an install: start as before
        return None


def _walk(chain: list, tab: dict, progress, runner, installed, who: str,
          failed: dict | None = None) -> tuple[str, list]:
    """Down the chain: the first voice already here (or needing no install) speaks; else each one's installer in
    turn, a failure kept with its short reason. `failed` {engine: why}: installs that already failed in this run,
    passed over with that reason rather than run again. (chosen, tried)."""
    tried: list[dict] = []
    for eng in chain:
        if eng in NO_INSTALL:
            return eng, tried
        have = installed(eng)
        if have is None or have:
            return eng, tried                         # nothing to install, or already there
        if failed and eng in failed:
            tried.append({"engine": eng, "ok": False, "why": failed[eng]})
            continue
        if _killed():
            tried.append({"engine": eng, "ok": False, "why": "downloads are switched off on this machine"})
            continue
        argv = installer_argv(eng, tab)
        if not argv:
            tried.append({"engine": eng, "ok": False, "why": "its installer is not in this install"})
            continue
        progress("Installing %s: %s …" % (who, label(eng, tab)))
        try:
            rc, out = runner(argv, progress)
        except Exception as e:  # noqa: BLE001  an installer that breaks is a reason, never a crash
            rc, out = 1, "%s: %s" % (type(e).__name__, e)
        if rc == 0 and installed(eng):
            tried.append({"engine": eng, "ok": True, "why": ""})
            return eng, tried
        why = plain(out)
        tried.append({"engine": eng, "ok": False, "why": why})
        progress("%s could not be installed: %s" % (label(eng, tab), why))
    return "text", tried


def announcer_line(chosen: str, first: str, tried: list, tab: dict | None = None) -> str:
    """The announcer's half of the line, in plain words; '' when its first voice is simply there."""
    failed = [t for t in tried if not t.get("ok")]
    if chosen == first and not failed:
        return "The announcer speaks with %s." % label(chosen, tab) if any(t.get("ok") for t in tried) else ""
    if not failed:
        return ""
    return ("The announcer's voice (%s) could not be installed: %s. %s says the announcer's lines for now; %s."
            % (label(failed[0]["engine"], tab), failed[0]["why"],
               "Text" if chosen == "text" else label(chosen, tab), RETRY))


def _record(rec: dict, res: dict, chain: list, tab: dict, home: Path | None, ann: dict | None = None,
            achain: list | None = None) -> None:
    """Keep what was chosen. A fallback moves the chain's head to the voice that is really there (the announcer's
    too)."""
    out = dict(rec)
    out.pop("say", None)
    out["voice_install"] = {k: res[k] for k in ("chosen", "first", "tried", "at", "line")}
    if res.get("backup"):
        out["backup_install"] = dict(res["backup"])
    if ann is not None:
        out["announcer_install"] = dict(ann)
        a = ann["chosen"]
        if a != (achain or [a])[0] and a in (achain or []):
            out["announcer_chain"] = achain[achain.index(a):]
    chosen = res["chosen"]
    if chosen != rec.get("main") and out.get("picked_by") != "user":
        rest = chain[chain.index(chosen) + 1:] if chosen in chain else []
        t = tab["tiers"].get(chosen) or {}
        out.update(main=chosen, tier=chosen, chain=[chosen] + [c for c in rest if c != chosen] +
                   ([] if chosen == "text" or "text" in rest else ["text"]),
                   label=t.get("label") or chosen, settings=dict(t.get("settings") or {}), install=t.get("install") or "")
        out["why"] = "%s (%s)" % (out.get("why") or "", res["line"])
    if ann is not None:
        head = (out.get("announcer_chain") or ["text"])[0]
        out["announcer"] = head if head != out.get("main") else ""
    try:
        VT._write(out, home)
    except OSError:
        pass


def launcher_start(home: Path | None = None):
    """start(engine) for run(): asks the running launcher to start the voice's lane (lib/crissa_procs). None when no
    launcher runs this Wall (the next start of Crissa starts it)."""
    if not os.environ.get("CRISSA_LAUNCHER_ARGS"):
        return None
    try:
        import crissa_home
        import crissa_procs
    except ImportError:
        return None
    h = home or crissa_home.HOME
    return lambda engine: crissa_procs.request(h, engine, "start")


# ---- the Connect page's "Her voice" box (GET/POST /v1/voice-install on a pack's player) ------------------------------

def view(home: Path | None = None) -> dict:
    """What the Connect page shows: {"ok", "on", "state", "engine", "lines", "line", "main", "label"}. Never raises."""
    rec = VT.read(home) or {}
    st = state(home)
    main = str(rec.get("main") or "")
    return {"ok": True, "on": enabled(), "state": st.get("state") or "idle", "engine": st.get("engine") or "",
            "lines": list(st.get("lines") or []), "line": st.get("line") or "", "main": main,
            "label": label(main) if main else "", "installed": VT.installed(main) if main else None}


def retry(home: Path | None = None, log=print) -> dict:
    """The Connect page's "Retry voice install": the install again from Init's first pick, in the background, and
    the voice's lane started when it is there. {"ok", "note"} or {"ok": False, "error"} in plain words."""
    if not enabled():
        return {"ok": False, "error": "installing her voice is switched off on this machine"}
    if running(home):
        return {"ok": True, "note": "her voice is already installing"}
    import threading
    _put(home, state="installing", engine="", lines=[], line="", t=time.time(), pid=os.getpid())   # the page sees it at once
    threading.Thread(target=lambda: run(home, progress=lambda s: log("voice-install: %s" % str(s)[:300]),
                                        retry=True, start=launcher_start(home)),
                     name="voice-install", daemon=True).start()
    return {"ok": True, "note": "installing her voice"}
