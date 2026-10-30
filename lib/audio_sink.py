"""Playback sink.

Where this machine has an echo canceller, mouths play its sink (STT cancels them); the physical
speaker is its dongle (AEC sink_master); never the mic array's jack. Those names are this machine's
own (config/house-machine.json: aec_sink, speaker_mark, mic_array_mark - 2026-10-06); a pack has
none and plays on the system default sink.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path

import crissa_home  # the state directory, resolved once (2026-09-26)

TTS_GATE = Path(
    os.environ.get("HOUSE_TTS_GATE", str(crissa_home.HOME / "house-tts-gate.json"))
).expanduser()
MOUTH_TEXT_CAP = 800

AEC_SINK = crissa_home.house_value("aec_sink")                # "" in a pack
DONGLE_SINK_MARK = crissa_home.house_value("speaker_mark")      # "" in a pack
MIC_ARRAY_MARK = crissa_home.house_value("mic_array_mark")      # "" in a pack
DEFAULT_SINK = "@DEFAULT_SINK@"   # PulseAudio/PipeWire's own name for the system default


def _is_mic(name: str) -> bool:
    return bool(MIC_ARRAY_MARK) and MIC_ARRAY_MARK in name


def _za():
    """lib/crissa_audio when CRISSA_AUDIO_SHIM=1, else None (today's direct
    calls). This module's RETURNS do not change either way: speak_sink's order
    and its mic-array check are what the wall, Init and the announcer depend on, so
    only the seven places that exec pactl route through the shim."""
    if os.environ.get("CRISSA_AUDIO_SHIM", "1" if sys.platform == "darwin" else "0").strip() != "1":
        return None
    try:
        import crissa_audio
    except ImportError:
        return None
    return crissa_audio


def _sink_names() -> list[str]:
    za = _za()
    if za is not None:
        return za.sinks()
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    names: list[str] = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def _default_sink() -> str:
    za = _za()
    if za is not None:
        return za.default_sink()
    try:
        r = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (r.stdout or "").strip()


def dongle_analog_sink(sinks: list[str] | None = None) -> str | None:
    if not DONGLE_SINK_MARK:
        return None
    names = sinks if sinks is not None else _sink_names()
    analog = None
    any_dongle = None
    for n in names:
        if DONGLE_SINK_MARK not in n or not n.startswith("alsa_output."):
            continue
        any_dongle = n
        if "analog-stereo" in n:
            analog = n
    return analog or any_dongle


def _as_float(v) -> float | None:
    """None stays None; a junk value must not resurrect a stale air window."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def mark_tts_gate(
    *,
    speaking: bool,
    last_reply: str = "",
    extra_sec: float = 6.0,
    who: str = "",
    air_at: float | None = None,
    air_until: float | None = None,
    air_firm: bool = False,
    air_off: bool = False,
) -> None:
    """Tell the ear a house mouth is open so STT can veto the echo.

    When speaking drops, keep last_reply / who / updated_ts so the wall can
    still land the line that just played (one paplay, not a journal queue).

    ``speaking`` is the EAR's gate and stays deliberately early — being deaf
    through the lead pad costs nothing, because the pad is silence.

    ``air_at`` / ``air_until`` are the GLASS's clock: when the sink actually
    starts and stops her voice. They are carried through every write, because
    a caller that knows nothing about them (rail-voice ``abort_mouth`` ->
    ``_write_tts_gate(speaking=False)``) must not erase the window on barge-in.
    Closing the gate with no ``air_until`` closes the air window at ``now`` —
    otherwise a killed mouth would leave the wall talking forever.
    """
    try:
        TTS_GATE.parent.mkdir(parents=True, exist_ok=True)
        prev: dict = {}
        if TTS_GATE.is_file():
            try:
                loaded = json.loads(TTS_GATE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    prev = loaded
            except (OSError, json.JSONDecodeError):
                prev = {}
        now = time.time()
        text = (last_reply or "").strip()[:MOUTH_TEXT_CAP]
        who_s = (who or "").strip().lower()
        prev_air_at = _as_float(prev.get("air_at"))
        prev_air_until = _as_float(prev.get("air_until"))
        if air_off:
            # HOUSE_MOUTH_AIR=0. `None` normally means "carry the window
            # forward" (the barge-in rule), so without an explicit off a stale
            # air_at from an air-enabled turn survives the kill switch and the
            # gate keeps advertising a clock nobody is maintaining.
            air_at = air_until = None
            air_firm = False
            prev_air_at = prev_air_until = None
        elif air_at is None:
            air_at = prev_air_at
        if speaking:
            updated = now
            notice = ""
            if air_until is None:
                # A NEW turn must not inherit the last turn's end. Left alone,
                # the gate advertises a window that closed before it opened,
                # and any reader that trusts it sees her as already finished.
                air_until = None if (air_at is not None and air_at != prev_air_at) \
                    else prev_air_until
        else:
            # A close with no air clock supplied ends the window NOW. This is
            # the barge-in path: killpg -> abort_mouth -> speaking=False.
            if air_until is None:
                air_until = now if air_at is not None else prev_air_until
            if not text:
                text = str(prev.get("last_reply") or "").strip()[:MOUTH_TEXT_CAP]
            if not who_s:
                who_s = str(prev.get("who") or "").strip().lower()
            try:
                updated = float(prev.get("updated_ts") or now)
            except (TypeError, ValueError):
                updated = now
            notice = str(prev.get("notice") or "")[:80]
        payload = (
            json.dumps(
                {
                    "schema": "house_tts_gate/v1",
                    "speaking": bool(speaking),
                    "last_reply": text,
                    "who": who_s,
                    "deaf_until": (now + extra_sec) if speaking else 0.0,
                    "notice": notice,
                    "updated_ts": updated,
                    "air_at": air_at,
                    "air_until": air_until,
                    # False while the voice is still synthesising: air_at is a
                    # PREDICTION then, and on a long line ffmpeg's atempo
                    # buffering makes it ~2s early. The glass must not put
                    # her in the speak pose on a guess.
                    "air_firm": bool(air_firm),
                }
            )
            + "\n"
        )
        fd, tmp = tempfile.mkstemp(
            prefix=".tts-gate-", suffix=".json", dir=str(TTS_GATE.parent)
        )
        try:
            os.write(fd, payload.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, TTS_GATE)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def live_mouth_line() -> dict | None:
    """Last voice paplay line. live=True only while it is on the air.

    Not a history window — one line, the utterance that actually played.
    """
    try:
        if not TTS_GATE.is_file():
            return None
        g = json.loads(TTS_GATE.read_text(encoding="utf-8"))
        if not isinstance(g, dict):
            return None
        text = str(g.get("last_reply") or "").strip()
        who = str(g.get("who") or "").strip().lower()
        # A BENCH SAY IS NOT A HOUSE LINE (2026-09-15). The Composer's bench
        # conductor tags who as "<voice>@bench": the ear gate above is
        # deliberately untouched (the room should go deaf while a tuning line
        # plays through the same speakers) but the caption is not the bench's
        # to write. Same filter the announcer has had.
        if not text or text.lower() == "announcer" or who == "announcer" or who.endswith("@bench"):
            return None
        try:
            ts = float(g.get("updated_ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        return {
            "who": who or "crissa",
            "text": text[:MOUTH_TEXT_CAP],
            "ts": ts,
            "stamped": True,
            "source": "mouth_air",
            "certain": True,
            "live": bool(g.get("speaking")),
        }
    except (OSError, json.JSONDecodeError):
        return None


def pack_aec_sink(sinks: list[str], wanted: str = "", default: str | None = None) -> str:
    """A PACK'S ECHO CANCELLER (round 14, the house's way: her voice plays on the canceller's sink, as the house's does
    on AEC_SINK): the sink the ear loaded (<home>/aec.json, modules/ear/crissa_ear/aec.py) when it is on, present, and
    sits in front of the speaker her voice would have used (the named VOICE_SINK, else the default output). '' on the
    house (AEC_SINK is the house's own), when it is off, or when the person moved to another speaker since."""
    if AEC_SINK:
        return ""
    try:
        st = json.loads((crissa_home.HOME / "aec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(st, dict) or not st.get("on"):
        return ""
    sink, spk = str(st.get("sink") or ""), str(st.get("speaker") or "")
    if not sink or sink not in sinks:
        return ""
    if wanted and wanted not in ("auto", sink):
        return sink if wanted == spk else ""
    d = _default_sink() if default is None else default
    return sink if (d in (spk, sink)) else ""


def _aec_state() -> dict:
    try:
        st = json.loads((crissa_home.HOME / "aec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return st if isinstance(st, dict) else {}


def setup_hears_aec() -> bool:
    """.101 round 30 (the User 2026-10-18: "sound bar moves for lila voice ... Should hers be ech cancelled?"): Setup's
    meter and question listener hear the room through the ear's canceller, as the ear does. Kill CRISSA_SETUP_MIC_AEC=0."""
    return os.environ.get("CRISSA_SETUP_MIC_AEC", "1").strip().lower() not in ("0", "off", "false", "no")


def capture_source(device: str) -> str:
    """The source to record `device` from: the ear's canceller source (aec.json) when it is on for this very microphone
    and present, else `device` (modules/ear/crissa_ear/aec.capture_source, for lib). Not for lip sync: that times the
    echo itself on the raw microphone."""
    if AEC_SINK or not device or not setup_hears_aec():
        return device
    st = _aec_state()
    src = str(st.get("source") or "")
    if not st.get("on") or not src or device != str(st.get("mic") or ""):
        return device
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return device
    return src if any(ln.split("\t")[1:2] == [src] for ln in (out or "").splitlines()) else device


def her_window_sink() -> str:
    """The sink her own window's sound belongs on: the canceller's when it is on (so Lila's films and anything else her
    page plays are the canceller's reference and never reach the ear), else '' (leave the stream where it is)."""
    if AEC_SINK or os.environ.get("CRISSA_WINDOW_SOUND_AEC", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    st = _aec_state()
    return str(st.get("sink") or "") if st.get("on") else ""


def move_inputs_of(pids: set[int], sink: str) -> list[int]:
    """Move every sink input whose process is one of `pids` (or a child of one) onto `sink`. [moved index]."""
    if not pids or not sink:
        return []
    try:
        rows = json.loads(subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True,
                                         text=True, timeout=3).stdout or "[]")
        sinks = {str(r.get("index")): str(r.get("name") or "") for r in json.loads(subprocess.run(
            ["pactl", "-f", "json", "list", "sinks"], capture_output=True, text=True, timeout=3).stdout or "[]")}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    if sink not in sinks.values():
        return []
    moved = []
    for r in rows:
        props = r.get("properties") or {}
        try:
            pid = int(props.get("application.process.id") or 0)
        except (TypeError, ValueError):
            continue
        if not pid or sinks.get(str(r.get("sink"))) == sink or not (_ancestors(pid) & pids):
            continue
        if subprocess.run(["pactl", "move-sink-input", str(r.get("index")), sink], capture_output=True,
                          timeout=3).returncode == 0:
            moved.append(int(r.get("index")))
    return moved


def full_volume_of(pids: set[int]) -> list[tuple[int, float]]:
    """Round 38 (.101 round 37: WirePlumber kept her browser's stream at 0.125, -18 dB, after the page's own volume):
    every sink input whose process is one of `pids` (or a child) and plays under 100 % is set to 100 %.
    [(index, the volume it had, 0..1)]. Setting it through pactl also updates what WirePlumber remembers."""
    if not pids:
        return []
    try:
        rows = json.loads(subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True,
                                         text=True, timeout=3).stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    out = []
    for r in rows:
        props = r.get("properties") or {}
        try:
            pid = int(props.get("application.process.id") or 0)
        except (TypeError, ValueError):
            continue
        if not pid or not (_ancestors(pid) & pids):
            continue
        vols = [float(str(c.get("value_percent") or "100%").rstrip("%")) / 100.0
                for c in (r.get("volume") or {}).values() if isinstance(c, dict)]
        had = min(vols) if vols else 1.0
        if had >= 0.995:
            continue
        if subprocess.run(["pactl", "set-sink-input-volume", str(r.get("index")), "100%"], capture_output=True,
                          timeout=3).returncode == 0:
            out.append((int(r.get("index")), round(had, 3)))
    return out


def speak_sink(explicit: str | None = None) -> str:
    """Sink for her voice / PA / announcer.

    When this machine's echo canceller is up, play through its sink (AEC_SINK) so STT's
    canceller source has a loopback of the mouth (the User 2026-08-20); its ``sink_master``
    is the speaker dongle. Never the mic array. A pack names none of these: the system
    default sink, else PulseAudio's own ``@DEFAULT_SINK@``.
    """
    sinks = _sink_names()
    dongle = dongle_analog_sink(sinks)
    wanted = (explicit or os.environ.get("VOICE_SINK") or "").strip()
    pa = pack_aec_sink(sinks, wanted)
    if pa:
        return pa
    if wanted and wanted not in ("", AEC_SINK or "auto", "auto"):
        if wanted in sinks and not _is_mic(wanted):
            return wanted
    if AEC_SINK and AEC_SINK in sinks:
        return AEC_SINK
    if dongle:
        return dongle
    default = _default_sink()
    if default and default in sinks and not _is_mic(default):
        return default
    for cand in (wanted, default, AEC_SINK, DEFAULT_SINK):
        if cand and cand not in ("auto",) and not _is_mic(cand):
            return cand
    return DEFAULT_SINK


# paplay / pacat. The Composer's duck (HOUSE_SPEAK_DUCK_PCT=50) used to hit
# these too; WirePlumber then restored channelVolumes=0.125, so every later
# Say and every live line painted tiles on a nearly silent sink
# (the User 2026-09-13, Composer player: no sound from TTS or speech).
_PAPLAY_NAMES = frozenset({"paplay", "pacat"})


def parse_sink_inputs(text: str) -> list[dict]:
    """[{index, sink, volume_pct, name, mute}] from `pactl list sink-inputs`.

    One parser: crissa_audio._parse_inputs (also reads pid/binary for the shim);
    this keeps the five fields callers here already use.
    """
    import crissa_audio as za
    return [{k: r.get(k) for k in ("index", "sink", "volume_pct", "name", "mute")}
            for r in za._parse_inputs(text)]


def list_sink_inputs() -> list[dict]:
    za = _za()
    if za is not None:
        return [{k: r.get(k) for k in ("index", "sink", "volume_pct", "name", "mute")}
                for r in za.inputs()]
    try:
        raw = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_sink_inputs(raw or "")


def is_paplay_input(row: dict) -> bool:
    return str(row.get("name") or "").strip().lower() in _PAPLAY_NAMES


def boost_paplay(sink: str | None = None, pct: int = 100, wait_s: float = 2.0,
                 pid: int | None = None) -> int:
    """Force house paplay inputs to pct. Returns how many it touched.

    WirePlumber remembers the last sink-input volume on application.name=paplay.
    A 50% duck therefore becomes the next line's start volume. Call this in a
    short thread right after paplay starts so Say / live speech come in at 100%.

    With `pid` (shim on) only THAT player is raised — the loop below raises
    every paplay on the sink, which lifts a leftover line and can miss the new
    one. Without a pid the loop stands, because the wall, Init and the
    announcer call it that way.
    """
    want = max(0, min(100, int(pct)))
    za = _za()
    if za is not None and pid is not None:
        return 1 if za.boost_player(sink, pct=want, timeout_s=wait_s, pid=pid) == 0 else 0
    idx = None
    if za is not None and sink:
        idx = za.sink_index(sink)
    elif sink:
        try:
            out = subprocess.run(
                ["pactl", "list", "short", "sinks"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in (out or "").splitlines():
                parts = line.split("\t") if "\t" in line else line.split()
                if len(parts) >= 2 and parts[1] == sink:
                    idx = parts[0]
                    break
        except (OSError, subprocess.TimeoutExpired):
            idx = None
    n = 0
    deadline = time.time() + max(0.05, float(wait_s))
    while time.time() <= deadline:
        for si in list_sink_inputs():
            if not is_paplay_input(si):
                continue
            if idx is not None and si.get("sink") not in (idx, sink):
                continue
            if za is not None:
                za.set_input_volume(si["index"], want)
                if si.get("mute"):
                    za.set_input_mute(si["index"], False)
            else:
                subprocess.run(
                    ["pactl", "set-sink-input-volume", si["index"], "%d%%" % want],
                    capture_output=True, timeout=5,
                )
                if si.get("mute"):
                    subprocess.run(
                        ["pactl", "set-sink-input-mute", si["index"], "0"],
                        capture_output=True, timeout=5,
                    )
            n += 1
        if n:
            return n
        time.sleep(0.05)
    return n


def _ancestors(pid: int, proc: Path = Path("/proc")) -> set[int]:
    """pid and every parent up to init (a setsid child keeps its parent: start_new_session
    starts a session, it does not reparent)."""
    out: set[int] = set()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            stat = (proc / str(pid) / "stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return out


def paplay_inputs_under(root_pid: int) -> list[dict]:
    """house paplay streams whose process descends from root_pid (the bench conductor):
    [{index, pid, mute}]. Read with `pactl -f json` for application.process.id; an
    unreadable list answers [] (touch nothing)."""
    try:
        raw = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"],
                             capture_output=True, text=True, timeout=5).stdout
        rows = json.loads(raw or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        props = r.get("properties") or {}
        if str(props.get("application.name") or "").strip().lower() not in _PAPLAY_NAMES:
            continue
        try:
            pid = int(props.get("application.process.id"))
        except (TypeError, ValueError):
            continue
        if root_pid in _ancestors(pid):
            out.append({"index": str(r.get("index")), "pid": pid, "mute": bool(r.get("mute"))})
    return out


def set_paplay_mute(muted: bool, sink: str | None = None, under_pid: int | None = None) -> int:
    """The Composer's 🔊: mute the BENCH's paplay (its Say TTS and drill lines).

    `under_pid` is the bench conductor: only streams descending from it are touched
    (the User 2026-09-28 - every paplay on the machine was muted, the wall's live
    speech included, and the page did it on every load). With no under_pid it
    touches NOTHING and answers 0; the old machine-wide sweep is gone. `sink` is
    accepted for the old call shape and ignored, as it always was."""
    if not under_pid:
        return 0
    flag = "1" if muted else "0"
    n = 0
    for si in paplay_inputs_under(int(under_pid)):
        za = _za()
        if za is not None:
            za.set_input_mute(si["index"], muted)
        else:
            subprocess.run(["pactl", "set-sink-input-mute", si["index"], flag],
                           capture_output=True, timeout=5)
        n += 1
    return n
