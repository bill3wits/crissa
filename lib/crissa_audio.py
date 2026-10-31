"""One audio interface, so the player can leave Linux (Crissa Stage 3).

The shipping core is already portable everywhere else: lib/mouth_playlist,
mouth_timing, head_frame, pose_mouth, poly_cache, mouth_viseme and conductor
make zero Linux-specific calls, and the face engine appears only in head_frame.fit(),
which runs at authoring time. What is NOT portable is audio. Counted honestly,
because MENTIONS are not CALLS and the first version of this header said "52
direct calls" when 52 is the number of LINES containing the word:

    file                              mentions   lines that exec the tool
    scripts/house-speak.py                  58                          7
    lib/audio_sink.py                       35                         14
    lib/clip_audio.py                        6                          2
    scripts/voiceonly-home-server.py        18                          2
    scripts/house-conductor.py              10                          2

So the rewire is 19 lines, not 52. (audio_sink read 8 until a recount: the
line `_PAPLAY_NAMES = frozenset({"paplay", "pacat"})` is a name set, not an
argv. The test below recounts from source, so a wrong cell cannot stay green.)
The conductor's two cells are the 09-25 silent paint run (a1338e52): a null
sink that is queried and, when absent, loaded — `subprocess.run(["pactl",
...])`, which the recount counts as an exec (argv), not the mentions the
docstring carried (8 -> 10). The rewire count stands: it counts the three
player files, and the conductor's two lines are a sink probe, not playback.

This module is that surface, once, behind a backend. PHASE 3 (2026-09-22)
WIRED IT, BEHIND A SWITCH THAT IS OFF: house-speak, clip_audio and audio_sink
import it only when CRISSA_AUDIO_SHIM=1, and each exec line above is still in
its file as the switch-off path, verbatim. The conductor's two lines sit
beside it, behind the same null-sink decision: they probe and load the mute
sink the painted run plays into, not the speech path the switch rewires.
The switch is off in the committed tree because house-speak is not
resident: the wall conductor execs it from disk for every line, so an ON default
would reach her next Say at commit time instead of through the restart window.

PHASE 4 (2026-09-22) put the glass's sink-input READ behind the same switch:
voiceonly-home-server's mouth_sink_live() calls inputs(timeout=0.4, strict=True)
and applies its OWN predicate (binary contains paplay, or name exactly paplay)
to the rows — here paplay is a symlink to pacat, so only the name ever matches. Its column still reads 2 and should: the else-branch `list sink-inputs`
and the long-lived subscribe are both still in the file — one sits behind the
switch, the other is the glass's own child and never comes here. Off by default
there too: :8099 is resident, but Restart=always loads the disk on a crash.

WHY IT DOES NOT FAIL OPEN
-------------------------
lib/audio_sink.speak_sink() honours VOICE_SINK only if that sink exists and
otherwise falls through, one line later, to the room's echo-canceller sink:

    wanted = (explicit or os.environ.get("VOICE_SINK") or "").strip()
    if wanted and wanted not in ("", AEC_SINK, "auto"):
        if wanted in sinks and not _is_mic(wanted):
            return wanted
    if AEC_SINK and AEC_SINK in sinks:
        return AEC_SINK

So a module that failed to load, or a name off by one character, plays out loud
with no error at all. That is the fourth fail-open found in this library in one
day (with the mouth-tune read, map.json's tile_head, and this), and it is the
one with a microphone in front of it: on 2026-09-22 the cold room's teardown
unloaded the null sink BEFORE stopping the bench, which is exactly that window.

`resolve_sink` here returns None and says why. A caller that wants the old
behaviour asks for it in writing, with `fallback=`.
"""
from __future__ import annotations

import os
import platform
import signal
import shutil
import subprocess
from pathlib import Path

# THE SEAM: THIS MODULE NEVER KNOWS ABOUT THE AIR CLOCK. It owns "bytes to a
# device"; MouthAir owns "envelope to /v1/mouth". That is why the stream is a
# HANDLE you write to rather than a play() that blocks — the streamed voice's player keeps
# paplay open on stdin with --latency-msec and feeds MouthAir the same bytes as
# they are written, and a run(input=whole buffer) would publish the envelope
# only after the line had finished, which is the buffered path, not this one.
#
# Still not here, deliberately: :8099's long-lived `pactl subscribe` under
# setpriv --pdeathsig. That is how the wall hard-stops the speak pose, and a
# short-lived subscribe per line is exactly the 09-19 orphan leak (31 of 64
# PipeWire slots). It stays with the glass, which owns it.
__all__ = ["backend_name", "sinks", "resolve_sink", "describe", "denied",
           "play_pcm", "play_file", "open_stream", "start_file", "Stream", "default_sink",
           "inputs", "sink_index", "wait_for_player", "boost_player", "duck_others",
           "set_input_volume", "set_input_mute",
           "set_mute", "SinkNotFound", "AudioUnavailable"]


class AudioUnavailable(RuntimeError):
    """No usable audio backend on this machine."""


class SinkNotFound(RuntimeError):
    """The named sink is not present. Deliberately not a silent fallback."""


# ---- backend selection -----------------------------------------------------
# CRISSA_AUDIO_BACKEND pins it: pipewire | macos | null. `null` swallows audio
# and is what a test or a headless build wants — it is also the honest answer
# for a box with no sound card, instead of a traceback halfway through a line.

# HARDWARE RULES ARE CONFIGURATION, NOT CODE (the User 2026-09-22: "make these
# flexible connection if they can be. Differing hardware may need a seek to
# test"). Hardcoding two USB product strings into the module whose whole job is
# to leave this machine is the same mistake as `return have[0]` — right here,
# wrong everywhere else. The house's own values are the DEFAULTS; a different
# box overrides them without touching the code, and `describe()` is the seek.
#
#   CRISSA_AUDIO_DENY    substrings that must never be played into, ':'-joined.
#                        Default: this machine's mic array (house config
#                        mic_array_mark) — a MICROPHONE, so playing there feeds
#                        the ear its own output. None in a pack.
#   CRISSA_AUDIO_PREFER  substrings to prefer, in order, ':'-joined. Default:
#                        this machine's AEC sink (STT wants a loopback of the
#                        mouth), then its physical speaker (house config aec_sink,
#                        speaker_mark). None in a pack: the system default sink.
#
# The machine's own names live in config/house-machine.json (the User 2026-10-06:
# machine-specific values in config, never code); a pack carries none.
def _house_value(key: str) -> str:
    try:
        import crissa_home
        return crissa_home.house_value(key)
    except (ImportError, AttributeError):
        return ""


DENY_DEFAULT = _house_value("mic_array_mark")
PREFER_DEFAULT = ":".join(p for p in (_house_value("aec_sink"), _house_value("speaker_mark")) if p)


def _patterns(env: str, default: str) -> list[str]:
    raw = os.environ.get(env)
    raw = default if raw is None else raw
    return [p for p in (x.strip() for x in raw.split(":")) if p]


def denied(name: str) -> str | None:
    """The deny pattern this sink matches, or None."""
    return next((p for p in _patterns("CRISSA_AUDIO_DENY", DENY_DEFAULT) if p in name), None)


def describe() -> list[dict]:
    """Every sink with why it would or would not be chosen — the seek for a box
    nobody has tried yet. `crissa_audio.describe()` is meant to be readable
    output, not a test fixture."""
    pref = _patterns("CRISSA_AUDIO_PREFER", PREFER_DEFAULT)
    out = []
    for n in sinks():
        d = denied(n)
        rank = next((i for i, p in enumerate(pref) if p in n), len(pref))
        out.append({"sink": n, "denied": d, "prefer_rank": None if d else rank,
                    "why": ("denied by %r" % d) if d else
                           ("prefers %r" % pref[rank] if rank < len(pref) else "no preference matched")})
    return out


def default_sink() -> str:
    """The server's default sink name, or \"\"."""
    return _default_sink()


def _default_sink() -> str:
    if backend_name() != "pulse":
        return ""
    try:
        return _run(["pactl", "get-default-sink"], timeout=4).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def backend_name() -> str:
    want = (os.environ.get("CRISSA_AUDIO_BACKEND") or "").strip().lower()
    if want in ("pipewire", "pulse", "macos", "null"):
        return "pulse" if want == "pipewire" else want
    if platform.system() == "Darwin":
        return "macos"
    if shutil.which("pactl") and shutil.which("paplay"):
        return "pulse"
    if platform.system() == "Darwin" or shutil.which("afplay"):
        return "macos"
    return "null"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---- sinks -----------------------------------------------------------------

def sinks() -> list[str]:
    """Output device names, most-preferred first. [] when there is no audio."""
    b = backend_name()
    if b == "pulse":
        try:
            out = _run(["pactl", "list", "short", "sinks"], timeout=4).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return [ln.split("\t")[1] for ln in out.splitlines() if "\t" in ln]
    if b == "macos":
        # afplay always targets the system default, so there is exactly one
        # addressable sink until someone wires CoreAudio device selection.
        # Naming it rather than returning [] keeps resolve_sink's contract.
        return ["default"]
    return []


def resolve_sink(wanted: str | None = None, fallback: str | None = None) -> str | None:
    """The sink to play on, or None.

    A `wanted` name that is not present raises SinkNotFound unless `fallback`
    is given IN WRITING. That is the whole point of this function: the old
    speak_sink() fell through to the room's canceller sink when a sink went
    missing, silently, which is how a tuning drill ends up audible.
    """
    have = sinks()
    w = (wanted or os.environ.get("VOICE_SINK") or "").strip()
    if w and denied(w):
        raise SinkNotFound(
            "%r matches the deny list (pattern %r): it is a capture device, and playing "
            "into one feeds the ear its own output. Change CRISSA_AUDIO_DENY if this box "
            "disagrees." % (w, denied(w)))
    if w and w not in ("auto",):
        if w in have:
            return w
        if fallback is None:
            raise SinkNotFound(
                "sink %r is not loaded (have: %s). Pass fallback= to accept another, "
                "or load the sink — this used to fall through to the room silently."
                % (w, ", ".join(have) or "none"))
        # A FALLBACK IS STILL A SINK, SO IT STILL OBEYS THE DENY LIST. The first
        # version returned it as soon as it appeared in `have`, which is before
        # the denied names are dropped — so resolve_sink(missing, fallback=<the
        # mic array>) handed back the microphone. Refusing it by name and
        # accepting it by the back door is not a deny list.
        if denied(fallback):
            raise SinkNotFound("fallback %r matches the deny list (%r)" % (fallback, denied(fallback)))
        if fallback in have:
            return fallback
    if not have:
        return None
    # THE MIC ARRAY IS NEVER THE ANSWER. lib/audio_sink has refused it since
    # the beginning — "Physical speaker is the dongle (AEC sink_master).
    # Never the array's jack" — because it is the mic array: playing there feeds
    # the ear its own output. The first version of this function ended in
    # `return have[0]`, which is right on the house only because its canceller
    # sink happens to sort first. On a box without it, or with only
    # the array present, it selected the microphone.
    usable = [s for s in have if not denied(s)]
    if fallback and fallback in usable:
        return fallback
    for pat in _patterns("CRISSA_AUDIO_PREFER", PREFER_DEFAULT):
        hit = next((s for s in usable if pat in s), None)
        if hit:
            return hit
    d = _default_sink()
    if d and d in usable:
        return d
    # Only the array left: no usable sink is the honest answer, not the mic.
    return usable[0] if usable else None


# ---- playback --------------------------------------------------------------

def play_file(path: str | Path, sink: str | None = None, timeout: float | None = None) -> int:
    b = backend_name()
    p = str(path)
    if b == "pulse":
        cmd = ["paplay"] + (["--device=%s" % sink] if sink else []) + [p]
    elif b == "macos":
        cmd = ["afplay", p]
    else:
        return 0
    try:
        return _run(cmd, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError as e:
        raise AudioUnavailable(str(e)) from e


def play_pcm(pcm: bytes, sr: int = 24000, channels: int = 1,
             sink: str | None = None, timeout: float | None = None) -> int:
    """Raw signed 16-bit little-endian PCM, the format the mouth path carries."""
    b = backend_name()
    if b == "null":
        return 0
    if b == "pulse":
        cmd = ["paplay", "--raw", "--format=s16le",
               "--rate=%d" % int(sr), "--channels=%d" % int(channels)]
        if sink:
            cmd.append("--device=%s" % sink)
        try:
            return subprocess.run(cmd, input=pcm, capture_output=True, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 124
        except OSError as e:
            raise AudioUnavailable(str(e)) from e
    # macOS: afplay reads files, so raw PCM needs a container. The caller owns
    # the temp file it already writes for the buffered path; wav_to_pcm's twin
    # lives in house-speak and travels with the air clock, not with this shim.
    raise AudioUnavailable("play_pcm needs a wav on the macos backend; write one and call play_file")


def set_mute(muted: bool, sink: str | None = None) -> int:
    b = backend_name()
    if b != "pulse":
        return 0
    try:
        return _run(["pactl", "set-sink-mute", sink or "@DEFAULT_SINK@",
                     "1" if muted else "0"], timeout=4).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


# ---- a stream you can write to, and kill ----------------------------------

class Stream:
    """A player held open on stdin. `write` returns as soon as the pipe takes
    the bytes, so the caller can publish its envelope on the same bytes in the
    same loop — which is the whole reason this is a handle and not a call.

    WHO OWNS THE PROCESS GROUP IS THE CALLER'S DECISION, AND IT MATTERS BOTH
    WAYS (Phase 3, 2026-09-22). clip_audio's player owns its session so
    `stop()` can take the group. house-speak's stream player must NOT: the
    conductor's say_stop_all and rail-voice's abort_mouth SIGTERM house-speak's
    GROUP, and that one signal is what cuts her voice mid-word. A player in a
    session of its own survives it and plays out its latency buffer after the
    stop. So `kill` signals the group only when the stream owns one — on a
    shared group, killpg would SIGKILL the caller (house-speak) itself.

    Popen-alike (`pid`, `poll`, `wait`, `terminate`) because clip_audio.stop()
    and the conductor's pacer loop were written against a Popen.
    """

    def __init__(self, proc, sink: str | None, own_session: bool = True):
        self._p, self.sink, self.own_session = proc, sink, bool(own_session)

    @property
    def pid(self) -> int | None:
        return getattr(self._p, "pid", None)

    @property
    def returncode(self):
        return None if self._p is None else self._p.poll()

    def poll(self):
        return None if self._p is None else self._p.poll()

    def wait(self, timeout: float | None = None) -> int:
        return 0 if self._p is None else self._p.wait(timeout=timeout)

    def write(self, chunk: bytes) -> bool:
        """False once the player has gone, so a caller stops feeding its clock
        for a line nobody is hearing."""
        if self._p is None:
            return True               # null: silent by contract, never "gone"
        if not self._p.stdin:
            return False
        try:
            self._p.stdin.write(chunk)
            self._p.stdin.flush()
            return True
        except (BrokenPipeError, ValueError, OSError):
            return False

    def end(self) -> None:
        """Close stdin and return: the player drains what it holds. The caller
        waits later (house-speak's `sink_in.close()` ... `player.wait(300)`)."""
        if self._p is None or not self._p.stdin:
            return
        try:
            self._p.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def close(self, timeout: float | None = None) -> int:
        """Flush and let it finish — the natural end of a line."""
        if self._p is None:
            return 0
        self.end()
        try:
            return self._p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            return 124

    def stderr_text(self) -> str:
        """What a finished file player said on stderr ("" for a stream)."""
        err = getattr(self._p, "stderr", None)
        if err is None:
            return ""
        try:
            return err.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""

    def terminate(self) -> None:
        if self._p is not None and self._p.poll() is None:
            try:
                self._p.terminate()
            except OSError:
                pass

    def kill(self) -> None:
        """The hard stop: the whole group when this stream owns one, else only
        the player."""
        if self._p is None or self._p.poll() is not None:
            return
        if self.own_session:
            try:
                os.killpg(os.getpgid(self._p.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    self._p.kill()
                except OSError:
                    pass
        else:
            try:
                self._p.kill()
            except OSError:
                pass
        # AND REAP IT. Signalling the group does not wait, and the conductor's
        # child handler only polls processes it has stored — so a stream killed
        # from elsewhere stays a zombie until something calls returncode.
        try:
            self._p.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            pass


class _NullStream(Stream):
    """Nothing is playing, so it has already finished: poll() is 0, not None.
    A None would send clip_audio.stop() to killpg(None) and hold the
    conductor's pacer loop (`while p.poll() is None`) on a sound that never was."""

    def __init__(self):
        super().__init__(None, None)

    def poll(self):
        return 0

    @property
    def returncode(self):
        return 0


def open_stream(sr: int = 24000, channels: int = 1, sink: str | None = None,
                latency_ms: int | None = None, own_session: bool = True) -> Stream:
    """own_session=True for a player whose caller kills it by handle
    (clip_audio). house-speak passes False — see Stream — and a test pins it."""
    b = backend_name()
    if b == "null":
        return _NullStream()          # by contract: tests and headless boxes
    if b == "macos":
        return _mac_stream(sr, channels, own_session)
    if b != "pulse":
        # play_pcm already refuses here rather than guessing a container, and
        # the STREAM is the path the rewire will call — so a handle that
        # accepts writes and plays nothing would be the quietest possible bug.
        raise AudioUnavailable("no streaming player on the %s backend" % b)
    cmd = ["paplay", "--raw", "--format=s16le",
           "--rate=%d" % int(sr), "--channels=%d" % int(channels)]
    if latency_ms:
        cmd.append("--latency-msec=%d" % int(latency_ms))
    if sink:
        cmd.append("--device=%s" % sink)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=bool(own_session))
    except OSError as e:
        raise AudioUnavailable(str(e)) from e
    return Stream(proc, sink, own_session)


# THE MAC STREAM (2026-09-28): afplay reads only files, so a line could not
# start before it was whole. ffplay (Homebrew's ffmpeg, already a requirement
# for the tempo stage and the film audio) reads raw PCM on stdin and plays it
# through CoreAudio — the same stdin handle as paplay, so Stream, its process
# group rule and every caller stay as they are. No ffplay = no stream, said
# once by can_stream(); house-speak then takes its buffered path (afplay).
def mac_stream_cmd(sr: int = 24000, channels: int = 1) -> list[str] | None:
    exe = shutil.which("ffplay")
    if not exe:
        return None
    return [exe, "-nodisp", "-autoexit", "-loglevel", "error", "-fflags", "nobuffer",
            "-probesize", "32", "-analyzeduration", "0",
            "-f", "s16le", "-ar", str(int(sr)),
            "-ch_layout", "mono" if int(channels) == 1 else "stereo", "-i", "-"]


def can_stream() -> bool:
    """Can open_stream() play here? False only where it would raise."""
    b = backend_name()
    if b == "macos":
        return mac_stream_cmd() is not None
    return b in ("pulse", "null")


def _mac_stream(sr: int, channels: int, own_session: bool) -> Stream:
    cmd = mac_stream_cmd(sr, channels)
    if cmd is None:
        raise AudioUnavailable("no streaming player on macOS without ffplay (brew install ffmpeg)")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=bool(own_session))
    except OSError as e:
        raise AudioUnavailable(str(e)) from e
    return Stream(proc, None, own_session)


def start_file(path: str | Path, sink: str | None = None,
               own_session: bool = True) -> Stream:
    """Play a file WITHOUT blocking, and hand back the handle. play_file blocks
    and gives no pid, and boost_player needs the pid to raise the right line."""
    b = backend_name()
    if b == "null":
        return _NullStream()
    p = str(path)
    if b == "pulse":
        cmd = ["paplay"] + (["--device=%s" % sink] if sink else []) + [p]
    elif b == "macos":
        cmd = ["afplay", p]
    else:
        raise AudioUnavailable("no file player on the %s backend" % b)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, start_new_session=bool(own_session))
    except OSError as e:
        raise AudioUnavailable(str(e)) from e
    return Stream(proc, sink, own_session)


# ---- sink inputs: the boost and the duck ----------------------------------
# Both need the NUMERIC index of a stream on a sink, which sinks() (names) does
# not carry. WirePlumber restores a player at the last duck it saw — 0.125 —
# so the boost polls for the input to appear after the process starts and puts
# it back to 100 %. The duck moves every OTHER input and restores on exit,
# skipping the player itself, because restoring the mouth is what muted the
# next line.

PLAYER_NAMES = frozenset({"paplay", "pacat", "afplay", "ffplay"})


def _parse_inputs(text: str) -> list[dict]:
    out, cur = [], None
    for line in text.splitlines():
        t = line.strip()
        if t.startswith("Sink Input #"):
            cur = {"index": t.split("#", 1)[1].strip(), "sink": None,
                   "volume_pct": None, "name": "", "mute": False, "pid": None,
                   "binary": None}
            out.append(cur)
        elif cur is None:
            continue
        elif t.startswith("Sink:"):
            cur["sink"] = t.split(":", 1)[1].strip()
        elif t.startswith("Mute:"):
            cur["mute"] = t.split(":", 1)[1].strip().lower() in ("yes", "true", "1")
        elif t.startswith("Volume:") and cur["volume_pct"] is None:
            try:
                cur["volume_pct"] = int(t.split("/")[1].strip().rstrip("%"))
            except (IndexError, ValueError):
                pass
        elif t.startswith("application.name"):
            cur["name"] = t.split("=", 1)[-1].strip().strip('"')
        elif t.startswith("application.process.id"):
            # THE INPUT MUST BE PINNED TO THE PROCESS THAT MADE IT. Without this
            # boost_player raised the FIRST player on the sink, which is only the
            # new one when there happens to be exactly one — and the silence
            # proof had exactly one, so it passed by luck. A leftover player or a
            # second line takes the 100 % and the new stream stays at the
            # WirePlumber restore, 0.125. Same shape as the name-vs-index bug one
            # level up: the input is found, and it is the wrong one.
            cur["pid"] = t.split("=", 1)[-1].strip().strip('"') or None
        elif t.startswith("application.process.binary"):
            # Read, never judged here: the glass's own predicate checks it (binary
            # contains paplay, or name is exactly paplay). On this box paplay is a
            # symlink to pacat and reports binary "pacat", so that branch is for
            # other boxes. is_player stays name-only on purpose — the duck must
            # keep skipping pacat, and widening it would change what it spares.
            cur["binary"] = t.split("=", 1)[-1].strip().strip('"') or None
    for r in out:
        r["is_player"] = str(r.get("name") or "").strip().lower() in PLAYER_NAMES
    return out


def sink_index(name: str) -> str | None:
    """A sink NAME to the numeric index a sink-input reports.

    `pactl list sink-inputs` gives `Sink: 38`, an INDEX, while sinks() and
    VOICE_SINK are names — so filtering inputs by name matches nothing, silently
    and forever. Measured: the first version of inputs() did exactly that.
    """
    if backend_name() != "pulse":
        return None
    try:
        out = _run(["pactl", "list", "short", "sinks"], timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[1] == name:
            return parts[0].strip()
    return None


def inputs(sink: str | None = None, *, timeout: float = 5,
           strict: bool = False) -> list[dict]:
    """Streams currently on a sink: index, sink, volume_pct, name, mute, pid,
    binary, is_player. `sink` may be a NAME or an index; [] on a backend that
    cannot see them.

    strict=True raises AudioUnavailable instead of returning [] when the read
    FAILED (no backend that can see streams, the tool missing, a timeout).
    [] means "nothing is playing"; a failed read means "I do not know", and the
    glass keeps its last answer for that rather than dropping her mouth pose.
    `timeout` is the caller's: the glass reads on its state-build path at 0.4 s."""
    if backend_name() != "pulse":
        if strict:
            raise AudioUnavailable("backend %s cannot list streams" % backend_name())
        return []
    try:
        raw = _run(["pactl", "list", "sink-inputs"], timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError) as e:
        if strict:
            raise AudioUnavailable("sink-input read failed: %s" % type(e).__name__) from e
        return []
    rows = _parse_inputs(raw or "")
    if sink is None:
        return rows
    want = sink if sink.isdigit() else (sink_index(sink) or sink)
    return [r for r in rows if r.get("sink") == want]


def wait_for_player(sink: str | None = None, after_pid: int | None = None,
                    timeout_s: float = 2.0, poll_s: float = 0.1) -> dict | None:
    """The player's sink-input, once the server has it. POLLING IS REQUIRED:
    measured on this box, a paplay opened on stdin took about 0.9 s to appear —
    a single look right after Popen sees nothing, which is why boost_paplay has
    always waited up to two seconds."""
    import time as _t
    end = _t.monotonic() + max(0.0, timeout_s)
    want = None if after_pid is None else str(after_pid)
    while True:
        for r in inputs(sink):
            if not r.get("is_player"):
                continue
            if want is not None and r.get("pid") != want:
                continue        # someone else's line; not the one we just started
            return r
        if _t.monotonic() >= end:
            return None
        _t.sleep(poll_s)


def boost_player(sink: str | None = None, pct: int = 100, timeout_s: float = 2.0,
                 stream: "Stream | None" = None, pid: int | None = None) -> int:
    """Put the player back to full and unmute it.

    WirePlumber restores a stream at the last volume it saw it ducked to —
    0.125 — so without this every later line paints tiles on a nearly silent
    sink (the User 2026-09-13: no sound from TTS or speech in the Composer player).

    THE STREAM IS REQUIRED. With neither a stream nor a pid, wait_for_player
    has nothing to match and raises whichever player is already on the sink —
    the wrong-line defect c00fbafd closed, reopened by a call that forgets the
    handle. Safe by default, not by opt-in: pass what open_stream returned.
    """
    if pid is None and stream is not None:
        pid = stream.pid
    if pid is None:
        raise ValueError("boost_player needs the stream (or its pid): without one "
                         "it raises whichever player is first on the sink")
    row = wait_for_player(sink, after_pid=pid, timeout_s=timeout_s)
    if not row:
        return 1
    rc = set_input_volume(row["index"], pct)
    set_input_mute(row["index"], False)
    return rc


class duck_others:
    """Hold every OTHER stream on this sink down for the length of a line.

    Skips the player itself on purpose: restoring the mouth is what muted the
    next line. A context manager because the restore must happen even when the
    line is cut.
    """

    def __init__(self, sink: str | None = None, pct: int = 0):
        self.sink, self.pct, self._was = sink, int(pct), []

    def __enter__(self):
        # 0 AND 100 MEAN OFF, exactly as house-speak's Ducked has always meant
        # (`if self.pct <= 0 or self.pct >= 100: return self`), and the env
        # default HOUSE_SPEAK_DUCK_PCT is 0. Passed straight through, a 0 would
        # set every other input on the sink to silence instead of leaving them
        # alone — the opposite of what the dial says.
        if self.pct <= 0 or self.pct >= 100:
            return self
        for r in inputs(self.sink):
            if r.get("is_player") or r.get("volume_pct") is None:
                continue
            self._was.append((r["index"], r["volume_pct"]))
            set_input_volume(r["index"], self.pct)
        return self

    def __exit__(self, *exc):
        for idx, vol in self._was:
            set_input_volume(idx, vol)
        self._was = []
        return False


def set_input_volume(index: str | int, pct: int) -> int:
    if backend_name() != "pulse":
        return 0
    try:
        return _run(["pactl", "set-sink-input-volume", str(index), "%d%%" % int(pct)], timeout=4).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def set_input_mute(index: str | int, muted: bool) -> int:
    if backend_name() != "pulse":
        return 0
    try:
        return _run(["pactl", "set-sink-input-mute", str(index), "1" if muted else "0"], timeout=4).returncode
    except (OSError, subprocess.SubprocessError):
        return 1
