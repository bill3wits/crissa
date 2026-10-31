"""Echo cancellation the house's way, in a pack (round 14, the User 2026-10-12: "is .100 reinventing this here? We
travelled these roads in the house version").

THE HOUSE (read 2026-10-12 on the house itself):
  - ~/.config/pipewire/pipewire-pulse.conf.d/50-echo-cancel.conf loads `module-echo-cancel aec_method=webrtc
    source_master=<the room mic> sink_master=<the speaker dongle> source_name=echocancel_source
    sink_name=echocancel_sink`;
  - her voice plays on echocancel_sink: lib/audio_sink.speak_sink plays to AEC_SINK (config/house-machine.json
    `aec_sink`), which is what house-speak's paplay is given;
  - its ear listens on the raw Seeed XVF3800 array (RAIL_STT_MIC), a beam that keeps the speaker out; rail-stt never
    picks echocancel_source by itself (rail_stt/audio.py: "sink != source").
A pack has no beam: .101 is a Shure MV6 and Razer speakers, and her own lines came back as turns.

HERE: the same module and method, loaded at runtime (`pactl load-module`, never a file in the person's home) between
the microphone the ear uses and the speaker her voice already plays on. Her playback goes to its sink (lib/audio_sink
reads <home>/aec.json); the ear listens on its source when the microphone does not cancel echo itself.
  - Never a device the person did not choose: the masters are the ear's microphone (Init's pick, else the sound
    server's default input) and her speaker (VOICE_SINK when named, else the default output). The module adds two
    nodes of its own (NODE_SOURCE, NODE_SINK). If the sound server then makes either of them the default, the
    person's default is put back. Our nodes are never taken for a microphone or a speaker the person chose.
  - A microphone that cancels echo itself (MIC_AEC_MARKS: arrays and speakerphones) is listened to raw, like the
    house's array.
  - No echo-cancel module, or no webrtc in it: the load fails, the ear listens on the microphone as before, and
    <home>/aec.json and the ear's log say why.
  - The setting is the ear's own (ear.json "aec", the Control Panel's Ears and mouth): auto (default) | on | off.
    "on" also cancels for a microphone that has its own; "off" unloads ours.
Kill: CRISSA_EAR_AEC=off.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .home import HOME

NODE_SOURCE = "crissa_aec_source"
NODE_SINK = "crissa_aec_sink"
MIC_AEC_MARKS = ("respeaker", "xvf3", "echo-cancel", "echocancel", "speakerphone", "jabra_speak", "speak_410",
                 "speak_510", "speak_710", "speak2", "poly_sync", "powerconf", "emeet")
WATCH_S = float(os.environ.get("CRISSA_EAR_AEC_WATCH_S", "30"))
_LOCK = threading.Lock()


def setting(env=None) -> str:
    e = os.environ if env is None else env
    v = str(e.get("CRISSA_EAR_AEC", "auto") or "auto").strip().lower()
    return v if v in ("auto", "on", "off") else "auto"


def state_path() -> Path:
    return HOME / "aec.json"


def read_state() -> dict[str, Any]:
    try:
        d = json.loads(state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(d: dict) -> dict:
    d = {**d, "at": round(time.time(), 3), "source": NODE_SOURCE, "sink": NODE_SINK}
    try:
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass
    return d


def _pactl(*args: str, timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", str(e)


def _names(kind: str, pactl=_pactl) -> list[str]:
    rc, out, _ = pactl("list", "short", kind)
    if rc != 0:
        return []
    return [ln.split("\t")[1] for ln in out.splitlines() if len(ln.split("\t")) > 1]


def ours(name: str) -> bool:
    return bool(name) and (name == NODE_SOURCE or name == NODE_SINK or name.startswith(NODE_SINK + "."))


def mic_cancels_itself(mic: str, env=None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("CRISSA_EAR_MIC_HAS_AEC", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    m = (mic or "").lower()
    return any(k in m for k in MIC_AEC_MARKS)


def speaker(pactl=_pactl, env=None) -> str:
    """The output her voice plays on: VOICE_SINK when named (lib/audio_sink's order), else the default output. Our own
    sink is never the answer: the speaker recorded under it is."""
    e = os.environ if env is None else env
    named = str(e.get("VOICE_SINK") or "").strip()
    if named and named not in ("auto",) and not ours(named):
        return named
    rc, out, _ = pactl("get-default-sink")
    got = out.strip() if rc == 0 else ""
    if ours(got):
        got = str(read_state().get("speaker") or "")
    return got


def _loaded(pactl=_pactl) -> list[tuple[str, str]]:
    """[(module index, its arguments)] for every echo canceller whose sink is ours."""
    rc, out, _ = pactl("list", "short", "modules")
    if rc != 0:
        return []
    got = []
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3 and parts[1] == "module-echo-cancel" and ("sink_name=" + NODE_SINK) in parts[2]:
            got.append((parts[0], parts[2]))
    return got


def release(pactl=_pactl, why: str = "off") -> dict:
    """Unload ours (only ours), and say so in aec.json."""
    with _LOCK:
        for idx, _ in _loaded(pactl):
            pactl("unload-module", idx)
        return _write_state({"on": False, "why": why})


def _unity(pactl, env=None) -> None:
    """THE CANCELLER IS NOT A VOLUME KNOB (the User 2026-10-29, the first-time walk: "now i can hardly hear her" - her
    sound passed crissa_aec_sink at 54% and then the speakers at 58%, about -30 dB together): its sink is held at 100%
    whenever it is loaded or kept, so the speakers are the one knob. Kill CRISSA_AEC_UNITY=0."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_AEC_UNITY", "1")).strip().lower() in ("0", "false", "off", "no"):
        return
    try:
        pactl("set-sink-volume", NODE_SINK, "100%")
    except Exception:  # noqa: BLE001 - a sound server that refuses keeps the canceller as it is
        pass


def ensure(mic: str, spk: str | None = None, *, pactl=_pactl, env=None, sleep=time.sleep) -> dict:
    """Make the canceller match the microphone and speaker in use (load, keep, reload or unload). Returns aec.json."""
    e = os.environ if env is None else env
    mode = setting(e)
    st = read_state()
    mic = (mic or "").strip()
    if ours(mic):
        mic = str(st.get("mic") or "")
    if mode == "off":
        return release(pactl, "off: the Ears and mouth setting")
    if not mic or mic == "page" or mic.endswith(".monitor"):
        return release(pactl, "no microphone of this machine's to cancel for")
    if mode == "auto" and mic_cancels_itself(mic, e):
        return release(pactl, "the microphone cancels echo itself")
    spk = (spk if spk is not None else speaker(pactl, e)).strip()
    if not spk or ours(spk):
        return release(pactl, "no speaker to cancel against")
    spk = hardware_sink(spk, pactl)      # 2026-10-26: another canceller's sink is not a speaker; its master is
    with _LOCK:
        loaded = _loaded(pactl)
        want = "source_master=%s sink_master=%s" % (mic, spk)
        if len(loaded) == 1 and want in loaded[0][1]:
            if NODE_SOURCE in _names("sources", pactl):
                _unity(pactl, e)
                return _write_state({"on": True, "mic": mic, "speaker": spk, "module": loaded[0][0],
                                     "why": "kept"})
        for idx, _ in loaded:
            pactl("unload-module", idx)
        rc0, d_sink, _ = pactl("get-default-sink")
        rc1, d_src, _ = pactl("get-default-source")
        rc, out, err = pactl("load-module", "module-echo-cancel", "aec_method=webrtc", "source_master=" + mic,
                             "sink_master=" + spk, "source_name=" + NODE_SOURCE, "sink_name=" + NODE_SINK)
        if rc != 0 or not out.strip():
            return _write_state({"on": False, "mic": mic, "speaker": spk,
                                 "why": "the sound server could not load its echo canceller: %s"
                                        % (err.strip() or out.strip() or "rc %s" % rc)[:200]})
        idx = out.strip().splitlines()[0].strip()
        for _ in range(20):
            if NODE_SOURCE in _names("sources", pactl):
                break
            sleep(0.1)
        else:
            pactl("unload-module", idx)
            return _write_state({"on": False, "mic": mic, "speaker": spk,
                                 "why": "the echo canceller loaded but its microphone never appeared"})
        # the person's defaults stay theirs: a server that made our nodes the default is put back
        restored = []
        sleep(0.3)
        for kind, before, rc_b in (("sink", d_sink.strip(), rc0), ("source", d_src.strip(), rc1)):
            rc_n, now, _ = pactl("get-default-" + kind)
            if rc_b == 0 and before and not ours(before) and rc_n == 0 and ours(now.strip()):
                pactl("set-default-" + kind, before)
                restored.append(kind)
        dead = dead_output(mic, pactl=pactl, env=e)
        if dead:
            pactl("unload-module", idx)
            return _write_state({"on": False, "mic": mic, "speaker": spk, "why": "released: " + dead,
                                 "defaults_put_back": restored})
        _unity(pactl, e)
        return _write_state({"on": True, "mic": mic, "speaker": spk, "module": idx, "why": "loaded",
                             "defaults_put_back": restored})


# THE CANCELLER'S OUTPUT IS MEASURED (the User's walk of 1.0.1, 2026-10-26: Setup's "This computer" card never moved
# on a Shure MV6 - the page's meter and the ear read our source, which gave -82 dBFS against -59 on the raw
# microphone, peak 3 against a bar of 1000; the person spoke louder and louder into a dead source). Two causes, both
# closed here: (1) the default output was ANOTHER echo canceller's sink (the house's), so ours was chained on a virtual
# sink and cancelled everything - `hardware_sink` follows a module-echo-cancel sink to its `sink_master`; (2) after the
# load, a second of our source is compared with a second of the microphone: ours DEAD_DB or more below a microphone
# that is not itself silent is a canceller giving nothing, and it is unloaded - the ear and the meter then read the
# microphone raw, as they do when the microphone cancels for itself. Kill CRISSA_EAR_AEC_DEAD_CHECK=0 (no sample);
# CRISSA_EAR_AEC_DEAD_DB sets the gap (20).
DEAD_DB = 20.0
DEAD_SAMPLE_S = 1.0


def hardware_sink(name: str, pactl=_pactl) -> str:
    """`name`, or the `sink_master` of the module-echo-cancel that made it (followed until a sink no canceller made)."""
    seen = set()
    cur = str(name or "")
    while cur and cur not in seen:
        seen.add(cur)
        rc, out, _ = pactl("list", "short", "modules")
        if rc != 0:
            return cur
        master = ""
        for ln in out.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 3 and parts[1] == "module-echo-cancel" and ("sink_name=" + cur) in parts[2].split():
                for tok in parts[2].split():
                    if tok.startswith("sink_master="):
                        master = tok[len("sink_master="):]
        if not master:
            return cur
        cur = master
    return cur


def _rms_db(source: str, seconds: float, run=subprocess.run) -> float | None:
    """The level of `seconds` of one source (16 kHz mono s16 through parec), None when it cannot be read."""
    try:
        r = run(["timeout", "--signal=TERM", "%.2f" % (seconds + 0.5), "parec", "--device=" + source, "--raw",
                 "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=40"],
                capture_output=True, timeout=seconds + 3.0)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = r.stdout or b""
    if len(raw) < 3200:
        return None
    import array
    import math
    a = array.array("h")
    a.frombytes(raw[: len(raw) - (len(raw) % 2)])
    rms = (sum(x * x for x in a) / len(a)) ** 0.5
    return 20.0 * math.log10(max(rms, 1.0) / 32768.0)


def dead_output(mic: str, *, pactl=_pactl, env=None, run=subprocess.run, levels=None) -> str:
    """'' when our source carries the microphone, else why it is dead (the words aec.json keeps)."""
    e = os.environ if env is None else env
    if str(e.get("CRISSA_EAR_AEC_DEAD_CHECK", "1")).strip().lower() in ("0", "off", "false", "no"):
        return ""
    try:
        gap = float(e.get("CRISSA_EAR_AEC_DEAD_DB", DEAD_DB))
    except (TypeError, ValueError):
        gap = DEAD_DB
    if levels is None:
        raw_db = _rms_db(mic, DEAD_SAMPLE_S, run)
        ours_db = _rms_db(NODE_SOURCE, DEAD_SAMPLE_S, run)
    else:
        raw_db, ours_db = levels
    if raw_db is None or ours_db is None:
        return ""                                   # unmeasured: kept (the old behaviour)
    if raw_db <= -80.0:
        return ""                                   # a silent microphone proves nothing
    if ours_db <= raw_db - gap:
        return "the canceller's output is dead against the microphone (%.0f vs %.0f dBFS)" % (ours_db, raw_db)
    return ""


def capture_source(device: str) -> str:
    """The source the ear records from: ours when the canceller is on for this microphone, else the microphone."""
    st = read_state()
    if st.get("on") and device and device == st.get("mic") and NODE_SOURCE in _names("sources"):
        return NODE_SOURCE
    return device


_WATCH = {"t": None}


def start_watch(get_mic: Callable[[], str], log: Callable[[str], None] = print) -> None:
    """Load (or not) now, and follow the microphone and the speaker every WATCH_S (a new default output reloads it)."""
    def once():
        try:
            mic = get_mic()
        except Exception as e:  # noqa: BLE001
            mic = ""
            log("[aec] no microphone: %s" % e)
        st = ensure(mic)
        return st

    st = once()
    log("[aec] %s: %s" % ("on" if st.get("on") else "off", st.get("why")))
    if _WATCH["t"] is not None or WATCH_S <= 0:
        return

    def loop():
        last = (st.get("on"), st.get("mic"), st.get("speaker"))
        while True:
            time.sleep(WATCH_S)
            got = once()
            now = (got.get("on"), got.get("mic"), got.get("speaker"))
            if now != last:
                log("[aec] %s: %s" % ("on" if got.get("on") else "off", got.get("why")))
                last = now

    t = threading.Thread(target=loop, name="aec-watch", daemon=True)
    _WATCH["t"] = t
    t.start()
