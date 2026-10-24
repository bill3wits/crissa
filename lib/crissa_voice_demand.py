"""A voice engine runs only while a role uses it (round 17, 2026-10-13).

the User 2026-10-13: "agree on stoping not used software. Constraintes verywhere everybit helps for getting these out to
regular boxes" - the target is regular machines (16 GB / 32 GB MacBooks, CPU-only Linux laptops).

MEASURED (.101, the CPU-only test laptop, 2026-10-11): three voice engines resident - Pocket (in use, 0.9 GB RSS),
Kokoro (1.1 GB) and Audio8 (1.8 GB) - about 3.8 GB of RAM for the one voice in use; their idle CPU is about 0. In the
round-16 run (2026-10-13) Setup's lines, Crissa's own lines and the announcer all spoke on Pocket (eve / azelma / anna),
so Kokoro and Audio8 sat idle all day. The launcher started every installed engine of a chain at boot
(scripts/crissa.py main: Kokoro whenever installed, a streamed voice whenever it is in her chain, Audio8 whenever it is
in her chain or the announcer's), and lib/crissa_tts.with_installed puts Audio8 in every chain where it is installed.

THE ROLES, and the engine each is configured to use (the same rule Init's three-face check reads,
lib/crissa_voice_tiers.face_check):
    her       Crissa's own replies: the voice tier record's `main` (Init's pick, or the person's own)
    lila      Lila and Setup: the same voice as hers (one seam, one engine; only the stock voice differs)
    announcer the first of the announcer's own order (`announcer_chain`), else her voice
A named engine (CRISSA_TTS, the launcher's --tts, agent.json's tts engine) is her voice and Lila's; the announcer then
says its lines with it too, except behind a named GPU voice (Turbo, Multilingual), where Pocket does
(lib/crissa_tts.config). Those engines start with Crissa and stay up, as before.

STANDBY. Every other installed engine in a chain (a backup) is planned by the launcher and HELD: state `standby` in
<home>/run/procs.json, "installed, not running" on the Control Panel (never red, never an error), and its lane is not
restarted by the watch (lib/crissa_watch).

ON DEMAND. When the ladder falls back to a held engine - house-speak's walk for a line (lib/crissa_tts.config with
demand=True), synth()'s order for the announcer and the backups, speak_audio8 - ensure() asks the launcher to start it
through the request queue Init's voice install and the machine check already use (lib/crissa_procs.request), says so
in the log (stderr, which a pack keeps in logs/house-speak.err, and <home>/logs/voice-demand.log), and waits for it to
answer for up to its load time (on_demand.wait_s: 60 s - the budget the launcher's own ready check gives a lane,
lib/crissa_procs.READY_S - and 120 s for a GPU voice, whose ready budget is 240 s, so that a line's wait stays under the
player's 150 s wedge limit for one line, house-conductor SPEAK_WEDGE_S). That is the wait a slow engine already gets:
the line waits for its audio, and the ladder goes on down when it does not come. A status read never starts anything (config() without
demand, the menu's start bar, the status marks, the Control Panel).

IDLE STOP. A held engine that was started (by a line, the machine check, a Start on the panel) is stopped again once
no role uses it and no line has asked it for on_demand.idle_stop_s (600 s), and held again; one a role uses now (a
re-pick in Setup or on the Connect page) is started and kept. Every line stamps the engine it asks
(<home>/run/voice-use/<engine>), and the launcher's pass (scripts/crissa.py voice_demand_pass) reads the stamps, so the
rule is the same on Linux (desk units) and on a Mac (one process group).
WHY 600 s: a backup speaks while her own voice is down, and the watch's whole back-off ladder (2+5+15+30+60 s) is under
2 minutes, so a backup that spoke once is usually asked again within a few minutes of the same conversation; ten
minutes covers that without paying a cold start per line, and gives the RAM back within ten minutes of rest. It is
the keep-warm's own measure of idle for a voice (keep_warm.interval_s 600: the first line after ten idle minutes paid
the cold model, 2026-10-06). A per-install copy of the tier table changes it (on_demand.idle_stop_s).

Kill CRISSA_VOICE_ENGINES_ON_DEMAND=0: every installed engine in a chain starts with Crissa and stays up (the behaviour
before round 17). Never the house (crissa_home.is_house(): it runs no launcher and names its own voices).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SWITCH = "CRISSA_VOICE_ENGINES_ON_DEMAND"
STANDBY = "standby"
STANDBY_WHY = "installed, not running: no role uses it now, so it is held; it starts when a line needs it"
# the launcher's lane for each engine it can start (lib/crissa_procs VOICE_PROC: Kokoro's lane is "tts")
ENGINE_LANE = {"kokoro": "tts", "multilingual": "multilingual", "turbo": "turbo", "pocket": "pocket",
               "audio8": "audio8"}
LANE_ENGINE = {v: k for k, v in ENGINE_LANE.items()}
NAMEABLE = ("openai", "say", "piper", "kokoro", "audio8", "multilingual", "turbo", "pocket")  # lib/crissa_tts.ENGINES
NAMED_GPU = ("turbo", "multilingual")       # a named one of these: the announcer is Pocket (lib/crissa_tts.config)
# the shipped values (config/crissa-voice-tiers.json `on_demand` wins, then the state-home copy, key by key)
DEFAULTS = {"idle_stop_s": 600, "wait_s": {"default": 60, "turbo": 120, "multilingual": 120}, "every_s": 5}
ROLE_WORDS = {"her": "her replies", "lila": "Lila and Setup", "announcer": "the announcer"}


def on(env: dict | None = None) -> bool:
    """The rule is on: a pack (never the house) whose switch is not 0."""
    e = os.environ if env is None else env
    if str(e.get(SWITCH, "1")).strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001  no home module: today's behaviour
        return False


def _home(home=None) -> Path:
    if home is not None:
        return Path(home)
    try:
        import crissa_home
        return Path(crissa_home.HOME)
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()


# ---- which engine each role uses ------------------------------------------------------------------------------------

def named_engine(env: dict | None = None, home=None) -> str:
    """The engine the person named: CRISSA_TTS, else agent.json's tts engine (lib/crissa_tts.config's order)."""
    e = os.environ if env is None else env
    n = str(e.get("CRISSA_TTS") or "").strip().lower()
    if not n:
        try:
            block = json.loads((_home(home) / "agent.json").read_text(encoding="utf-8")).get("tts") or {}
            n = str(block.get("engine") or "").strip().lower() if isinstance(block, dict) else ""
        except (OSError, ValueError, AttributeError):
            n = ""
    return n if n in NAMEABLE else ""


def roles(rec: dict | None, named: str = "") -> dict | None:
    """{role: engine} (ROLE_WORDS), or None when there is no pick to read (then nothing is held: today's)."""
    n = str(named or "").strip().lower()
    if n in NAMEABLE:
        return {"her": n, "lila": n, "announcer": "pocket" if n in NAMED_GPU else n}
    r = rec or {}
    main = str(r.get("main") or ((r.get("chain") or [""])[0]) or "").strip()
    if not main:
        return None
    ac = [str(x) for x in (r.get("announcer_chain") or []) if x]
    ann = ac[0] if ac else (str(r.get("announcer") or "").strip() or main)
    return {"her": main, "lila": main, "announcer": ann}


def in_use(rec: dict | None, named: str = "") -> set | None:
    """The engines some role is configured to use; None = no pick to read (nothing is held)."""
    got = roles(rec, named)
    return None if got is None else set(got.values())


def who_uses(rec: dict | None, named: str, engine: str) -> str:
    """'her replies, Lila and Setup' - the roles that use `engine`, in words; '' when none does."""
    got = roles(rec, named) or {}
    return ", ".join(ROLE_WORDS[k] for k in ("her", "lila", "announcer") if got.get(k) == engine)


def standby_at_start(planned, rec: dict | None, named: str = "", env: dict | None = None) -> set:
    """The planned voice lanes the launcher holds at its start: each whose engine no role uses."""
    if not on(env):
        return set()
    use = in_use(rec, named)
    if use is None:
        return set()
    return {lane for lane in planned if lane in LANE_ENGINE and LANE_ENGINE[lane] not in use}


# ---- the settings ---------------------------------------------------------------------------------------------------

def settings(home=None) -> dict:
    """on_demand from the tier table (the shipped file, then the state-home copy), over DEFAULTS."""
    out = json.loads(json.dumps(DEFAULTS))
    try:
        import crissa_voice_tiers
        got = crissa_voice_tiers.table(home).get("on_demand") or {}
    except Exception:  # noqa: BLE001  a broken table keeps the shipped values
        got = {}
    for k, v in (got.items() if isinstance(got, dict) else ()):
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = dict(out[k], **v)
        else:
            out[k] = v
    return out


def idle_stop_s(home=None) -> float:
    return float(settings(home).get("idle_stop_s") or DEFAULTS["idle_stop_s"])


def wait_s(engine: str, home=None) -> float:
    w = settings(home).get("wait_s") or {}
    w = w if isinstance(w, dict) else {"default": w}
    return float(w.get(engine) or w.get("default") or DEFAULTS["wait_s"]["default"])


def every_s(home=None) -> float:
    return float(settings(home).get("every_s") or DEFAULTS["every_s"])


# ---- the use stamps and the log -------------------------------------------------------------------------------------

def use_path(engine: str, home=None) -> Path:
    return _home(home) / "run" / "voice-use" / str(engine)


def touch(engine: str, home=None) -> None:
    """A line asked this engine now. Best effort; never raises."""
    p = use_path(engine, home)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        os.utime(p, None)
    except OSError:
        pass


def last_use(engine: str, home=None) -> float:
    """When a line last asked this engine (epoch seconds); 0 = never."""
    try:
        return use_path(engine, home).stat().st_mtime
    except OSError:
        return 0.0


def note(line: str, home=None) -> None:
    """One line in <home>/logs/voice-demand.log (every on-demand start and idle stop). Never raises."""
    try:
        d = _home(home) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "voice-demand.log", "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass


def _say(line: str, home=None) -> None:
    print(line, file=sys.stderr, flush=True)
    note(line, home)


# ---- the launcher's table -------------------------------------------------------------------------------------------

def held_engines(home=None) -> set:
    """The engines whose lane the running launcher holds in standby (for the panel's "installed, not running")."""
    if not on():
        return set()
    try:
        import crissa_procs
        tab = crissa_procs.load(_home(home))
        if not tab or not crissa_procs.launcher_alive(tab)[0]:
            return set()
    except Exception:  # noqa: BLE001
        return set()
    return {LANE_ENGINE[n] for n, c in (tab.get("children") or {}).items()
            if n in LANE_ENGINE and (c or {}).get("state") == STANDBY}


def _up_fn(engine: str, env: dict | None):
    import crissa_tts as T
    if engine == "audio8":
        return lambda: T.audio8_up(env)
    if engine == "kokoro":
        return lambda: T.kokoro_up(env)
    return lambda: T.stream_up(engine, env)


def ensure(engine: str, home=None, env: dict | None = None, *, up=None, request=None, sleep=time.sleep,
           clock=time.monotonic) -> bool | None:
    """At speak time: `engine` is about to be asked for a line. Stamps its use; when the launcher holds it in standby,
    asks the launcher to start it, says so, and waits for it to answer (wait_s). True = it answers now; False = it was
    held and did not come up (the ladder goes on); None = not this rule's (off, the house, no launcher lane, or not
    held: running, down for another reason, waiting for Init, stopped by the person)."""
    if engine not in ENGINE_LANE or not on(env):
        return None
    h = _home(home)
    touch(engine, h)
    lane = ENGINE_LANE[engine]
    try:
        import crissa_procs
        tab = crissa_procs.load(h)
    except Exception:  # noqa: BLE001
        return None
    if ((tab.get("children") or {}).get(lane) or {}).get("state") != STANDBY:
        return None
    try:
        import crissa_watch
        if crissa_watch.agent_closed(h):
            _say("voice: %s is installed, not running, and Close Agent holds her voices: not started" % engine, h)
            return False
    except Exception:  # noqa: BLE001  a record that cannot be read is not a close
        pass
    up = up or _up_fn(engine, env)
    if up():
        return True                               # started already (another line, the launcher's pass)
    if request is None:
        request = lambda: crissa_procs.request(h, lane, "start")   # noqa: E731
    try:
        got = request() or {}
    except Exception as e:  # noqa: BLE001
        got = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if got.get("ok") is False:
        _say("voice: %s is installed, not running, and could not be started on demand (%s)"
             % (engine, str(got.get("error") or "?")[:160]), h)
        return False
    limit = wait_s(engine, h)
    _say("voice: %s is installed, not running (no role uses it); started on demand for this line, waiting up to %d s "
         "while it loads" % (engine, int(limit)), h)
    t0 = clock()
    while clock() - t0 < limit:
        if up():
            touch(engine, h)
            _say("voice: %s answered %.1f s after its on-demand start" % (engine, clock() - t0), h)
            return True
        sleep(0.5)
    _say("voice: %s did not answer within %d s of its on-demand start; the next voice in the chain speaks"
         % (engine, int(limit)), h)
    return False


def decide(lanes: dict, use: set, last: dict, since: dict, now: float, idle_s: float) -> list:
    """The launcher's pass, pure: [(act, lane, why)], act start | stop | hold.
    lanes: {lane: {"alive", "standby", "waiting", "installed"}}; last: {engine: last use (epoch s, 0 = never)};
    since: {lane: when the pass first saw it running unused}, kept here between passes.
      start  a held lane whose engine a role uses now (a re-pick): it runs from now on
      stop   a running lane no role uses, unasked for idle_s since it was first seen so or last asked
      hold   a lane Init installed while it waited, that no role uses: held, not started"""
    out = []
    for lane, st in lanes.items():
        eng = LANE_ENGINE.get(lane)
        if not eng:
            continue
        if eng in use:
            since.pop(lane, None)
            if st.get("standby") and not st.get("alive"):
                out.append(("start", lane, "a role uses %s now" % eng))
            continue
        if st.get("standby") or not st.get("alive"):
            since.pop(lane, None)
            if st.get("waiting") and st.get("installed") and not st.get("standby"):
                out.append(("hold", lane, STANDBY_WHY))
            continue
        first = since.setdefault(lane, now)
        quiet = now - max(first, float(last.get(eng) or 0))
        if quiet >= idle_s:
            since.pop(lane, None)
            out.append(("stop", lane, "no role uses %s and no line asked it for %d min" % (eng, int(quiet // 60))))
    return out
