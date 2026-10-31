"""Family voice + face enroll — desk-first User init.

Writes:
  people/<id>/voice/ws_enroll_*.json   live WeSpeaker (rail-stt match)
  people/<id>/voice/NNN.json           ECAPA museum, only if encoder=ecapa
  people/<id>/face/enroll_desk_NNN.json + refs/*.jpg   ArcFace when embed works

Coach browser (SSE) + spoken cues share the same lines.
"""

from __future__ import annotations

from .. import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

import array
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import os

from . import cues
from .coach import coach_emit, coach_start, coach_stop
from .face_snap import capture_face, restore_door_home
from .person import display_name, ensure_person
from .site import Site
from .take_plans import resolve_plan

_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


def _pack() -> bool:
    try:
        from .. import paths as _p
        return not bool(_p.is_house())
    except Exception:  # noqa: BLE001
        return True


def _source_muted(mic: str) -> bool:
    try:
        out = subprocess.run(["pactl", "get-source-mute", mic], capture_output=True, text=True, timeout=3).stdout
    except Exception:  # noqa: BLE001
        return False
    return "yes" in (out or "").lower()


def unmute_mic(site: Site) -> None:
    """The house: its own room mic unmuted and set to its level. A pack never sets the person's microphone volume;
    a muted microphone is lifted for the take alone by record_fixed and muted again after (the User 2026-10-10, U)."""
    mic = site.mic
    if not mic or _pack():
        return
    subprocess.run(["pactl", "set-source-mute", mic, "0"], capture_output=True)
    # Seeed at 150% hard-clips enroll (11/14 User clips peak 32768, measured
    # 2026-08-24). Link 2 sat low historically. Shure stays ~100%.
    vol = "150%" if ("Insta360" in mic or "Link_2" in mic) else "100%"
    subprocess.run(["pactl", "set-source-volume", mic, vol], capture_output=True)


def pulse_source_named(*needles: str) -> str | None:
    """First Pulse *input* whose name contains every needle.

    ``pactl list short sources`` includes sink monitors. The Shure MV6
    playback monitor matches ``Shure`` and is not a microphone — recording
    it captures what the headset is playing, not the desk mic.
    """
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    hits: list[str] = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[1]
        if name.endswith(".monitor"):
            continue
        hay = name.lower()
        if all(n.lower() in hay for n in needles):
            hits.append(name)
    for name in hits:
        if name.startswith(("alsa_input.", "bluez_input.")):
            return name
    return hits[0] if hits else None


def _tool_hold_script() -> Path:
    from .paths import rails_path

    return rails_path("scripts", "house-tool-hold.py")


def _tool_hold_acquire(
    *, reason: str = "enroll", owner: str = "crissa_presence.enroll_panel", sec: float = 900.0
) -> None:
    """Pause Crissa/ear-bridge/free-air; allow enroll coach voice (the announcer)."""
    script = _tool_hold_script()
    if not script.is_file():
        # A PACK HAS NO TOOL HOLD (the User 2026-10-10, .101: "tool-hold script missing" on every Register): the hold
        # pauses the house's own voices, and outside the house nothing reads it - house-speak reads a hold only on the
        # house, and this coach releases the ear itself (release_ear_for_enroll). So no script is shipped, and its
        # absence is said only on the house, where it is a fault.
        from .. import paths as _p
        if _p.is_house():
            print(f"  tool-hold script missing: {script}", flush=True)
        return
    allow = ["announcer"]  # enroll coach
    try:
        from .site import load_site

        sv = load_site().speak_voice
        if sv:
            allow.append(str(sv).lower())
    except Exception:
        pass
    cmd = [
        sys.executable,
        str(script),
        "acquire",
        "--reason",
        reason,
        "--owner",
        owner,
        "--sec",
        str(sec),
    ]
    for v in sorted(set(allow)):
        cmd.extend(["--allow-voice", v])
    # soft pause ear-bridge (polls hold); hard stop optional via env
    if os.environ.get("HOUSE_ENROLL_STOP_EAR_BRIDGE") == "1":
        cmd.append("--stop-ear-bridge")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    print(
        f"  tool-hold acquire rc={r.returncode} {(r.stdout or r.stderr or '')[:160].strip()}",
        flush=True,
    )


# THE GIVE-BACK'S OWN TIMEOUTS, NAMED (round 46, 2026-10-23): the coach's Cancel waits for the routine's give-back
# before it kills (voice_register.cancel_release_budget_sec sums the chain), so each call's bound lives in one place.
EAR_REOPEN_TIMEOUT_SEC = 5.0      # POST /v1/ear/open (the ear answers at once or is down)
HOLD_RELEASE_TIMEOUT_SEC = 15.0   # house-tool-hold.py release (a python start + one file write)


def _tool_hold_release(*, owner: str = "crissa_presence.enroll_panel") -> None:
    script = _tool_hold_script()
    if not script.is_file():
        return
    r = subprocess.run(
        [sys.executable, str(script), "release", "--owner", owner, "--force"],
        capture_output=True,
        text=True,
        timeout=HOLD_RELEASE_TIMEOUT_SEC,
    )
    print(
        f"  tool-hold release rc={r.returncode} {(r.stdout or '')[:120].strip()}",
        flush=True,
    )


def _stt_base() -> str:
    return os.environ.get("RAIL_STT_URL", _ports.url("stt")).rstrip("/")


def _mic_hold_path() -> Path:
    return Path(os.environ.get("HOUSE_ENROLL_MIC_HOLD", "/tmp/house-enroll.mic-hold"))


def _set_mic_hold(active: bool) -> None:
    """Signal house-keeper not to reopen ear while enroll owns the mic."""
    p = _mic_hold_path()
    try:
        if active:
            p.write_text(f"enroll {time.time():.0f}\n", encoding="utf-8")
        elif p.is_file():
            p.unlink()
    except OSError as e:
        print(f"  mic-hold file warning: {e!r}", flush=True)


def release_ear_for_enroll() -> dict:
    """Close continuous ear so enroll owns the Link mic (parec is exclusive).

    Measured 2026-08-02: ear open → concurrent parecord/parec peak≈0–17 even
    while speech reaches STT. Ear closed → same device records real peaks.

    Also drops ``/tmp/house-enroll.mic-hold`` so house-keeper does not reopen
    ear mid-session (keeper loop every ~60s was racing enroll).
    """
    import urllib.error
    import urllib.request

    _set_mic_hold(True)
    url = f"{_stt_base()}/v1/ear/close"
    try:
        req = urllib.request.Request(
            url, data=b"{}", method="POST", headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3.0) as r:
            body = r.read().decode()
        try:
            out = json.loads(body) if body else {}
        except json.JSONDecodeError:
            out = {"raw": body[:200]}
        print(f"  ear released for enroll: open={out.get('open')}", flush=True)
        return out if isinstance(out, dict) else {"ok": True}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ear release skipped ({e!r})", flush=True)
        return {"ok": False, "error": str(e)}


# THE GO WAITS FOR THE MIC (round 29; .101 2026-10-17: "ear release skipped (TimeoutError)", the recording started
# 3.77 s after the GO and his whole first count was lost): before a take's GO the routine asks the ear whether it has
# let go of the microphone and waits until it has, up to CRISSA_GO_WAIT_EAR_S (10). An ear that does not answer is not
# waited for (no ear, or a house without the route). Kill CRISSA_GO_WAITS_FOR_EAR=0.
def wait_ear_released(limit_s: float | None = None, poll_s: float = 0.1, get=None, now=time.monotonic,
                      sleep=time.sleep) -> float:
    """Seconds waited until the ear's /v1/ear/status says it is not open (0.0 at once, or when it cannot say)."""
    if os.environ.get("CRISSA_GO_WAITS_FOR_EAR", "1").strip().lower() in ("0", "off", "false", "no"):
        return 0.0
    if limit_s is None:
        try:
            limit_s = float(os.environ.get("CRISSA_GO_WAIT_EAR_S", "10"))
        except ValueError:
            limit_s = 10.0

    def _get():
        import urllib.request
        with urllib.request.urlopen(f"{_stt_base()}/v1/ear/status", timeout=1.5) as r:
            return json.loads(r.read() or b"{}")
    get = get or _get
    t0 = now()
    while True:
        try:
            st = get() or {}
        except Exception:  # noqa: BLE001  an ear that cannot say is not waited for
            return round(now() - t0, 3)
        if not st.get("open") or now() - t0 >= float(limit_s):
            return round(now() - t0, 3)
        sleep(poll_s)


def reopen_ear_after_enroll() -> dict:
    """Best-effort reopen continuous ear (presence still has people)."""
    import urllib.error
    import urllib.request

    _set_mic_hold(False)
    if os.environ.get("HOUSE_ENROLL_KEEP_EAR_CLOSED") == "1":
        return {"ok": True, "skipped": True}
    url = f"{_stt_base()}/v1/ear/open"
    # the keeper's own body (house-keeper.py EAR_OPEN_BODY): without
    # hangover_sec / max_utter_sec the ear came back guillotining free-air
    # until the keeper's next heal (2026-09-11)
    payload = json.dumps(
        {
            "mode": "vad",
            "idle_timeout_sec": 1800,
            "max_open_sec": 43200,
            "hangover_sec": 1.8,
            "max_utter_sec": 20,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            url, data=payload, method="POST", headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=EAR_REOPEN_TIMEOUT_SEC) as r:
            body = r.read().decode()
        try:
            out = json.loads(body) if body else {}
        except json.JSONDecodeError:
            out = {"raw": body[:200]}
        print(f"  ear reopened after enroll: open={out.get('open', out.get('ok'))}", flush=True)
        return out if isinstance(out, dict) else {"ok": True}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ear reopen skipped ({e!r})", flush=True)
        return {"ok": False, "error": str(e)}


def remute_in_finally() -> bool:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): a muted microphone lifted for the take was muted
    again only after the read, outside its finally, so a Cancel (SIGTERM raises out of the read) left the person's
    microphone open. The mute now goes back in the read's finally (record_fixed, mouth_prints._record_until).
    Kill: CRISSA_REMUTE_IN_FINALLY=0 (the old order)."""
    return os.environ.get("CRISSA_REMUTE_IN_FINALLY", "1").strip().lower() not in ("0", "false", "no", "off")


def _remute(mic: str) -> None:
    try:
        subprocess.run(["pactl", "set-source-mute", mic, "1"], capture_output=True)
    except Exception as e:  # noqa: BLE001  a failed re-mute is said, never raised over the take's own error
        print(f"  re-mute {mic}: {e!r}", flush=True)


def record_fixed(path: Path, site: Site, seconds: float, started: list | None = None) -> None:
    """Capture mono 16 kHz WAV via parec (same path as rail-stt).

    Uses fixed byte count + terminate — not ``timeout parecord`` (was racing
    ear / leaving near-silent files under concurrent STT).
    started (round 17, 2026-10-13: Setup's lip sync): gets time.monotonic() just before parec starts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mic = site.mic
    if not mic:
        raise RuntimeError("site.audio.mic is empty — set in site.json")
    # Ensure no continuous ear is stealing the device right before capture
    release_ear_for_enroll()
    unmute_mic(site)
    lifted = _pack() and _source_muted(mic)
    in_finally = remute_in_finally()
    if lifted:
        subprocess.run(["pactl", "set-source-mute", mic, "0"], capture_output=True)
    print(f"  recording on {mic} ({seconds:.1f} s)", flush=True)
    time.sleep(0.08)

    sr = 16000
    n_samples = max(1, int(float(seconds) * sr))
    nbytes = n_samples * 2  # s16le mono
    cmd = [
        "parec",
        "--device",
        mic,
        "--format=s16le",
        f"--rate={sr}",
        "--channels=1",
        "--latency-msec=20",
    ]
    if started is not None:
        started.append(time.monotonic())
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    raw = b""
    try:
        while len(raw) < nbytes:
            chunk = proc.stdout.read(min(65536, nbytes - len(raw)))
            if not chunk:
                break
            raw += chunk
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        if lifted and in_finally:         # round 46: a Cancel mid-read mutes it again too
            _remute(mic)

    if lifted and not in_finally:
        subprocess.run(["pactl", "set-source-mute", mic, "1"], capture_output=True)
    # Pad short reads so we still write a valid window (peak will show silence)
    if len(raw) < nbytes:
        raw = raw + (b"\x00" * (nbytes - len(raw)))
    raw = raw[:nbytes]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw)



def go_with_beep_on() -> bool:
    """Round 33 (.101 measured the GO beep to the recorder at 0.75-0.78 s on every take): the recorder starts BEFORE the
    GO beep and the take keeps what comes after the tone ends, so a first word said on the tone is never lost to the
    recorder's own start (the ear release, the unmute, parec's start and the 0.12 s settle all ran after the tone).
    Off on the house unless set; on in a pack. Kill: CRISSA_GO_REC_WITH_BEEP=0."""
    v = os.environ.get("CRISSA_GO_REC_WITH_BEEP", "").strip().lower()
    if v in ("0", "off", "false", "no"):
        return False
    if v in ("1", "on", "true", "yes"):
        return True
    return _pack()


def go_trim_tail_s() -> float:
    """What is cut after the tone's end besides the tone itself: the speaker-to-mic tail (default 40 ms)."""
    try:
        return max(0.0, min(0.3, float(os.environ.get("CRISSA_GO_TRIM_TAIL_MS", "40")) / 1000.0))
    except ValueError:
        return 0.04


def record_go(path: Path, site: Site, seconds: float, beep, started: list | None = None, after=None,
              popen=subprocess.Popen, now=time.monotonic) -> dict:
    """record_fixed with the GO beep inside it (go_with_beep_on): parec starts, beep() plays the tone (it returns when
    the tone has played), and the file is the `seconds` that follow the tone's end - the tone and its tail are cut by
    the clock, never by the sound. started gets the tone's end (the page's bar starts with the kept window); after()
    runs once the tone is over (the take's photo). Returns the timings for the log: lead (recorder up before the tone
    ended), trim (seconds cut off the front)."""
    import threading

    path.parent.mkdir(parents=True, exist_ok=True)
    mic = site.mic
    if not mic:
        raise RuntimeError("site.audio.mic is empty — set in site.json")
    release_ear_for_enroll()
    unmute_mic(site)
    lifted = _pack() and _source_muted(mic)
    if lifted:
        subprocess.run(["pactl", "set-source-mute", mic, "0"], capture_output=True)
    print(f"  recording on {mic} ({seconds:.1f} s, from the GO beep's end)", flush=True)

    sr = 16000
    nbytes = max(1, int(float(seconds) * sr)) * 2  # s16le mono
    cmd = ["parec", "--device", mic, "--format=s16le", f"--rate={sr}", "--channels=1", "--latency-msec=20"]
    t_spawn = now()
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    buf = bytearray()
    first = [0.0]              # time.monotonic() of the first audio parec captured (its first chunk, less its length)
    need = [1 << 62]           # bytes to read: unknown until the tone has ended
    skip = [0]                 # bytes cut off the front: the tone and its tail
    got_first = threading.Event()
    done = threading.Event()

    def _read() -> None:
        try:
            while len(buf) < need[0]:
                chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                if not first[0]:
                    first[0] = now() - len(chunk) / (2.0 * sr)
                    got_first.set()
                buf.extend(chunk)
        finally:
            got_first.set()
            done.set()

    reader = threading.Thread(target=_read, name="go-rec", daemon=True)
    reader.start()
    out = {"lead": 0.0, "trim": 0.0}
    try:
        beep()
        t_end = now()
        out["lead"] = round(t_end - t_spawn, 3)
        got_first.wait(timeout=2.0)
        origin = first[0] or t_end
        trim = max(0, int((t_end + go_trim_tail_s() - origin) * sr)) * 2
        out["trim"] = round(trim / (2.0 * sr), 3)
        skip[0] = trim
        need[0] = trim + nbytes
        if started is not None:
            started.append(t_end)
        if after is not None:
            after()
        done.wait(timeout=float(seconds) + 5.0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=1.0)
        if lifted:
            subprocess.run(["pactl", "set-source-mute", mic, "1"], capture_output=True)

    raw = bytes(buf[skip[0]:])
    if len(raw) < nbytes:      # pad a short read so the window is still valid (the peak shows the silence)
        raw = raw + (b"\x00" * (nbytes - len(raw)))
    raw = raw[:nbytes]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw)
    print("  GO: recorder up %.2f s before the tone ended; %.2f s cut off the front" % (out["lead"], out["trim"]),
          flush=True)
    return out

def voiced_fraction(
    path: Path, *, frame_ms: float = 20.0, peak_frac: float = 0.08
) -> float:
    """Fraction of 20 ms frames whose peak exceeds ``peak_frac`` of clip peak.

    Measured 2026-08-24: silence/room clips in the User bank were ≤18% and
    still produced confident ECAPA of the empty office. Gate is 60% on 9 s desk
    windows (30% was calibrated on 2.5 s lines in 5.5 s silence).
    """
    try:
        with wave.open(str(path), "rb") as w:
            sr = int(w.getframerate() or 16000)
            nch = int(w.getnchannels() or 1)
            raw = w.readframes(w.getnframes())
        a = array.array("h")
        a.frombytes(raw)
        if nch > 1:
            a = array.array("h", a[::nch])
        if not a:
            return 0.0
        peak = max(abs(x) for x in a)
        if peak < 80:
            return 0.0
        n = max(1, int(sr * (frame_ms / 1000.0)))
        thr = peak * float(peak_frac)
        n_fr = max(1, len(a) // n)
        hit = 0
        for i in range(n_fr):
            sl = a[i * n : (i + 1) * n]
            if sl and max(abs(x) for x in sl) > thr:
                hit += 1
        return hit / n_fr
    except Exception:
        return 0.0


def peak_pcm(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as w:
            a = array.array("h")
            a.frombytes(w.readframes(w.getnframes()))
        return max(abs(x) for x in a) if a else 0
    except Exception:
        return 0


# A TAKE THAT IS NOT A VOICE (round 20, the User 2026-10-14, .101 round 19: an analog input with nothing behind it gave
# four "prints" of full-scale noise - peak 32768, RMS 7,000-9,000 for the whole 6 s, active 1.0 - and the quiet gate
# took them as loud speech). Speech pauses: inside a take the quietest 20 ms frames sit far below the loudest, and a
# voice clips at most a few samples. A take is refused as `clipped` when more than CLIP_FRAC of its samples sit at full
# scale, or as `constant` when it never pauses (its 10th-percentile frame RMS is at least CONSTANT_RATIO of its 90th, at
# a level a quiet room never reads). Kill: CRISSA_TAKE_FAULT=0 (the quiet gate alone, as before).
CLIP_LEVEL = 32000
CLIP_FRAC = 0.005
CONSTANT_RATIO = 0.5
CONSTANT_MIN_RMS = 300.0
TAKE_FAULT_WORDS = {
    "clipped": "the microphone gave only full-scale noise or clipping",
    "constant": "the microphone gave a steady sound with no pauses, not a voice",
}


def take_fault_on() -> bool:
    return os.environ.get("CRISSA_TAKE_FAULT", "1").strip().lower() not in ("0", "off", "false", "no")


def pcm_fault(a, sr: int = 16000, frame_ms: float = 20.0) -> str:
    """'' for samples that can be a voice; 'clipped' or 'constant' for samples that cannot (see above)."""
    if not take_fault_on() or not a:
        return ""
    clip = sum(1 for x in a if x >= CLIP_LEVEL or x <= -CLIP_LEVEL)
    if clip > CLIP_FRAC * len(a):
        return "clipped"
    n = max(1, int(sr * frame_ms / 1000.0))
    rms = []
    for i in range(0, len(a) - n + 1, n):
        sl = a[i:i + n]
        rms.append((sum(x * x for x in sl) / len(sl)) ** 0.5)
    if len(rms) < 10:
        return ""
    rms.sort()
    p10, p90 = rms[len(rms) // 10], rms[(9 * len(rms)) // 10]
    if p90 >= CONSTANT_MIN_RMS and p10 >= CONSTANT_RATIO * p90:
        return "constant"
    return ""


def take_fault(path: Path) -> str:
    """pcm_fault of a WAV take (mono, or its first channel); '' when it cannot be read."""
    try:
        with wave.open(str(path), "rb") as w:
            sr = int(w.getframerate() or 16000)
            nch = int(w.getnchannels() or 1)
            raw = w.readframes(w.getnframes())
        a = array.array("h")
        a.frombytes(raw)
        if nch > 1:
            a = array.array("h", a[::nch])
        return pcm_fault(a, sr)
    except Exception:  # noqa: BLE001  a take that cannot be read is judged by the quiet gate alone
        return ""


def live_encoder_meta() -> dict:
    """model / dim / family the ear actually matches against."""
    from .. import voice_embed   # the presence module's own (crissa_presence/voice_embed.py, 2026-10-10)

    return {
        "model": voice_embed.model_id(),
        "dim": int(voice_embed.dim()),
        "family": voice_embed.encoder_name(),
    }


def rec_encoder_family(rec: dict) -> str:
    model = str(rec.get("model") or rec.get("encoder") or rec.get("family") or "").lower()
    if "wespeaker" in model or model.startswith("ws/") or "campplus" in model:
        return "wespeaker"
    if "ecapa" in model or "speechbrain" in model or "spkrec" in model:
        return "ecapa"
    dim = rec.get("dim")
    if dim == 256:
        return "wespeaker"
    if dim == 192:
        return "ecapa"
    return "unknown"


def write_live_voice_print(path: Path, rec: dict) -> Path:
    """Write a voice print only if it matches the live encoder family + dim.

    Register used to label ECAPA-192 as the live bank while rail-stt filtered
    WeSpeaker. An empty bank is silent; a wrong-family write is how it empties.
    """
    meta = live_encoder_meta()
    live = str(meta["family"])
    fam = rec_encoder_family(rec)
    enc = rec.get("encoding") or rec.get("embedding")
    if enc is None:
        raise RuntimeError("refusing to write voice print with no encoding")
    dim = int(len(enc))
    want = int(meta["dim"])
    if fam != live:
        raise RuntimeError(
            f"refusing to write {fam} print while live encoder is {live} "
            f"(model={rec.get('model')!r})"
        )
    if dim != want:
        raise RuntimeError(
            f"refusing dim={dim} while live {live} dim={want}"
        )
    rec = dict(rec)
    rec["dim"] = want
    rec["model"] = rec.get("model") or meta["model"]
    path.write_text(json.dumps(rec, indent=2) + "\n")
    return path


def embed_wav(path: Path):
    """Embed a take with the live encoder (WeSpeaker unless env says ECAPA)."""
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        sr = w.getframerate()
    from .. import voice_embed   # the presence module's own (crissa_presence/voice_embed.py, 2026-10-10)

    return voice_embed.embed_pcm16(raw, sr=sr)


def voice_print_path(
    out_dir: Path, slot: int, *, ts: float | None = None, family: str | None = None
) -> Path:
    t = int(ts if ts is not None else time.time())
    fam = family or str(live_encoder_meta()["family"])
    if fam == "wespeaker":
        return out_dir / f"ws_enroll_{t}_{slot}.json"
    return out_dir / f"{slot:03d}.json"


def next_slot(voice_dir: Path) -> int:
    n = 1
    while (voice_dir / f"{n:03d}.json").is_file():
        n += 1
    return n


# the work between the pre-beep wait and the GO tone's start (a coach event, the tone built, pactl): measured 2026-10-11
PRE_BEEP_S = 0.06


def _coach_and_speak(site: Site, text: str, *, kind: str = "coach") -> None:
    coach_emit(type=kind, text=text, phase="coach")
    cues.speak(site, text)


def run_one_take(
    site: Site,
    *,
    person_id: str,
    take_index: int,
    total: int,
    plan: dict[str, str],
    bank_slot: int | None = None,
    seconds: float | None = None,
    snap_face: bool = True,
    at_desk: bool = False,
) -> Path | None:
    name = display_name(person_id, site.root)
    line = plan["cue"].format(name=name)
    # Plan may pin record length (paragraph re-enroll needs full sentence window)
    if seconds is None and plan.get("seconds"):
        try:
            seconds = float(plan["seconds"])
        except (TypeError, ValueError):
            seconds = None
    seconds = site.voice_seconds if seconds is None else seconds
    # The User 2026-08-06: never cut the cue short — pad from word count if needed
    # (~2.2 w/s comfortable + 1.0s tail before done-chirp).
    _words = max(1, len(line.split()))
    _need = _words / 2.2 + 1.0
    if seconds < _need:
        seconds = min(12.0, _need)
        print(
            f"  record window raised to {seconds:.1f}s for {_words}-word cue",
            flush=True,
        )
    from .person import assert_not_frozen

    assert_not_frozen(person_id, root=site.root)
    out_dir = ensure_person(person_id, root=site.root) / "voice"
    slot = bank_slot if bank_slot is not None else next_slot(out_dir)
    station = plan.get("station") or "desk"
    pose = plan.get("pose") or "front"
    is_paragraph = (plan.get("kind") or "").lower() == "paragraph" or len(line) > 120

    coach_emit(type="clear")
    coach_emit(type="phase", text=f"take {take_index}/{total}", phase=f"{take_index}/{total}")
    print(f"\n[{take_index}/{total}] {plan['angle']}", flush=True)
    print(f"  « {line[:80]}{'…' if len(line) > 80 else ''} »", flush=True)

    coach_emit(type="line", text=f"Take {take_index} of {total}", cls="muted")
    coach_emit(type="coach", text=plan["speak_angle"])
    coach_emit(type="read", text=line)
    blind = plan.get("blind", "1" if is_paragraph else "0") not in ("0", "false", "no")

    # Face snap on look-and-say takes: facefinder center/zoom for high-quality
    # mapping (the User 2026-08-06 — candid hold left mouth-up when screen raised).
    # Candid face-only section stays hold/no-FF in sectioned_session.
    face_meta = None
    if snap_face and plan.get("snap_face", "1") not in ("0", "false", "no"):
        coach_emit(type="line", text="Look at camera — facefinder framing…", cls="muted")
        if pose == "profile":
            cues.announce_turn_profile(site, at_desk=at_desk)
        else:
            # Same spoken cue as the voice line will use — person faces cam now
            cues.speak_loud(site, "Please look at the camera.", pause=0.2)
        coach_emit(
            type="face",
            text="Look at the camera — facefinder centering",
            phase=f"{take_index}/{total}",
            pose=pose,
            station=station,
        )
        try:
            face_meta = capture_face(
                site,
                person_id,
                station=station,
                pose=pose,
                take_plan_id=plan.get("id"),
                aim=True,
                quality_map=True,  # FF center/zoom regardless of site use_facefinder
            )
            if face_meta.get("ok"):
                coach_emit(
                    type="ok",
                    text="Face capture saved"
                    + (" with embedding" if face_meta.get("encoding") else " (photo only)"),
                )
                coach_emit(
                    type="face_ok",
                    text="Face saved",
                    pose=pose,
                    has_emb=bool(face_meta.get("encoding")),
                )
                cues.speak_loud(site, "Got it.", pause=0.3)
            else:
                coach_emit(
                    type="face_err",
                    text="Face capture weak — voice take continues",
                    cls="muted",
                )
                cues.speak_loud(site, "Photo weak. Continuing.", pause=0.4)
        except Exception as e:
            print(f"  face snap exception: {e!r}", flush=True)
            coach_emit(type="face_err", text=f"Face error: {e}", cls="muted")

    # Fast path: instruction+sentence as ONE continuous TTS → beep → record.
    # (the User 2026-08-06: separate Cosy calls left ~3–9s dead air between
    # "look at the camera and say" and the line — sounded like disconnects.)
    # Placement is spoken from site.json: the User is asked for his own seat,
    # anyone else for where they actually stand (the User 2026-08-14).
    _role, _seat = cues.role_and_seat(site, person_id)
    if pose == "profile" or "off" in (plan.get("id") or ""):
        cues.announce_voice_off_angle(site, line, at_desk=at_desk, role=_role, seat=_seat)
    else:
        cues.announce_voice_line(site, line, at_desk=at_desk, role=_role, seat=_seat)
    # sentence already spoken inside announce_voice_* — do not teach twice
    # the GO beep is due BEEP_GAP_S after her last word (cues.wait_for_beep, the User 2026-10-11); wait here so the
    # coach page's GO lands with the tone, not ahead of it - go_beep tops up the last few ms itself
    cues.wait_for_beep(PRE_BEEP_S)

    print("  *** sentence → BEEP = GO ***", flush=True)
    coach_emit(type="beep", text="GO")
    ok = False
    try:
        cues.go_beep(site)
        coach_emit(type="line", text=f"Recording {seconds:.1f}s", cls="beep")
        print(f"  recording {seconds:.1f}s…", flush=True)
        wav = out_dir / f"enroll_{int(time.time())}_{slot}.wav"
        # Ensure mic unmuted right before record (USB Link can drop)
        unmute_mic(site)
        record_fixed(wav, site, seconds)
        pk = peak_pcm(wav) if wav.is_file() else 0
        nbytes = wav.stat().st_size if wav.is_file() else 0
        print(f"  capture done peak={pk} bytes={nbytes}", flush=True)

        if not wav.is_file() or nbytes < 2000 or pk < site.min_peak:
            # peak≈0/1 is dead mic — not "soft speech" (a regular, 2026-08-02 morning)
            if pk < 50:
                cues.speak_loud(
                    site,
                    "Microphone silent. Check the camera mic. Skipping this take.",
                    pause=0.4,
                )
            else:
                cues.speak_loud(site, "Too quiet. Skip.", pause=0.2)
            print(f"  FAIL weak capture peak={pk} (min {site.min_peak})", flush=True)
            return None

        emb = embed_wav(wav)
        if emb is None:
            cues.speak_loud(site, "Embed failed.", pause=0.2)
            print("  FAIL embed", flush=True)
            return None

        meta = live_encoder_meta()
        rec = {
            "person_id": person_id,
            "modality": "voice",
            "model": meta["model"],
            "dim": int(len(emb)),
            "encoding": [float(x) for x in emb.tolist()],
            "source": "crissa_presence.enroll_panel",
            "site_id": site.site_id,
            "cue": line,
            "angle": plan["angle"],
            "station": station,
            "pose": pose,
            "take_plan_id": plan.get("id"),
            "face_json": face_meta.get("json_path") if face_meta else None,
            "face_jpg": face_meta.get("jpg_path") if face_meta else None,
            "wav": str(wav),
            "peak": pk,
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        jp = voice_print_path(
            out_dir, slot, ts=rec["ts"], family=str(meta["family"])
        )
        write_live_voice_print(jp, rec)
        print(f"  OK {jp.name} peak={pk} dim={len(emb)} model={meta['model']}", flush=True)
        coach_emit(type="ok", text=f"Voice {take_index}/{total} saved")
        ok = True
        return jp
    except Exception as e:
        print(f"  FAIL exception: {e!r}", flush=True)
        coach_emit(type="line", text=f"Error: {e}", cls="muted")
        return None
    finally:
        try:
            cues.done_chirp(site)
            coach_emit(type="chirp", text="ok")
        except Exception as e:
            print(f"  (done_chirp failed: {e!r})", flush=True)
        time.sleep(0.2 if ok else 0.35)


def run_voice_session(
    site: Site,
    person_id: str,
    *,
    only: int | None = None,
    replace_slots: bool = False,
    seconds: float | None = None,
    plan_name: str = "desk",
    snap_face: bool = True,
    coach: bool = True,
    restore_door: bool = True,
    skip_intro: bool = False,
    at_desk: bool | None = None,
) -> list[Path]:
    from .roles import is_primary_user

    person_id = person_id.strip().lower()
    ensure_person(person_id, root=site.root)
    if at_desk is None:
        at_desk = is_primary_user(site, person_id)
    unmute_mic(site)
    # Pause Crissa / ear-bridge / free-air mouth; the coach voice still allowed
    _tool_hold_acquire(reason="enroll", owner=f"house_enroll:{person_id}")
    # Mic is exclusive with continuous ear — release once for whole session
    release_ear_for_enroll()
    takes = resolve_plan(plan_name)

    if only is not None:
        if only < 1 or only > len(takes):
            raise ValueError(f"--only must be 1..{len(takes)}")
        selected = [(only, takes[only - 1])]
    else:
        selected = list(enumerate(takes, 1))

    print(f"Enrolling voice+face for {person_id} @ site={site.site_id}", flush=True)
    print(f"  plan={plan_name}  mic={site.mic}  snap_face={snap_face}", flush=True)
    print(f"  takes={[i for i, _ in selected]}", flush=True)

    if coach:
        try:
            # Only User sits at desk monitor — Regulars get loud audio only
            coach_start(open_browser=bool(at_desk))
            time.sleep(0.4)
        except Exception as e:
            print(f"  coach display unavailable: {e!r}", flush=True)

    if not skip_intro:
        coach_emit(type="clear")
        coach_emit(type="phase", text="voice", phase="voice")
        coach_emit(type="line", text=f"Voice · {len(selected)} takes", cls="muted")
        cues.speak_legend(site)
        time.sleep(0.15)
    else:
        coach_emit(type="phase", text="voice", phase="voice")
        coach_emit(type="line", text=f"Voice · {len(selected)} takes", cls="muted")

    # Warm Cosy for fixed lines + this session's cues (gap-free joined play)
    try:
        extra = []
        for _, pl in selected:
            try:
                nm = display_name(person_id, site.root)
                extra.append(pl["cue"].format(name=nm))
            except Exception:
                pass
        print("  precaching coach TTS…", flush=True)
        cues.precache_enroll_lines(site, extra)
    except Exception as e:
        print(f"  precache skip: {e!r}", flush=True)

    written: list[Path] = []
    try:
        for take_index, plan in selected:
            if only is not None and replace_slots:
                bank_slot = only
            elif only is None:
                bank_slot = take_index
            else:
                bank_slot = None
            p = run_one_take(
                site,
                person_id=person_id,
                take_index=take_index,
                total=len(takes) if only is None else 1,
                plan=plan,
                bank_slot=bank_slot,
                seconds=seconds,
                snap_face=snap_face,
                at_desk=bool(at_desk),
            )
            if p is not None:
                written.append(p)
    finally:
        if restore_door:
            restore_door_home(site)
        # Hand mic back to continuous ear for the rest of the day
        try:
            reopen_ear_after_enroll()
        except Exception as e:
            print(f"  ear reopen failed: {e!r}", flush=True)
        _tool_hold_release(owner=f"house_enroll:{person_id}")

    bank = sorted((ensure_person(person_id, root=site.root) / "voice").glob("00*.json"))
    print(f"\ndone — voice bank: {[p.name for p in bank]}", flush=True)
    face_n = len(
        list((ensure_person(person_id, root=site.root) / "face").glob("enroll_*.json"))
    )
    print(f"      face enroll files: {face_n}", flush=True)

    if written:
        _coach_and_speak(
            site,
            f"Enroll finished. {len(written)} voice take{'s' if len(written) != 1 else ''} saved.",
        )
    else:
        _coach_and_speak(site, "Enroll finished with no new voice takes saved.")
    coach_emit(type="done", text="Session complete — thank you.")
    cues.session_end(site)
    if coach:
        time.sleep(1.5)
        # leave server up briefly for final frame; stop to free port
        # KEEP_COACH=1 when started from coach Start button (daemon stays up)
        if os.environ.get("HOUSE_ENROLL_KEEP_COACH") != "1":
            try:
                coach_stop()
            except Exception:
                pass
    return written


def run_face_only_session(
    site: Site,
    person_id: str,
    *,
    plan_name: str = "desk",
    coach: bool = True,
) -> list[dict]:
    """Camera face/profile snaps without re-recording voice (backfill)."""
    person_id = person_id.strip().lower()
    ensure_person(person_id, root=site.root)
    takes = resolve_plan(plan_name)
    if coach:
        try:
            coach_start(open_browser=True)
        except Exception as e:
            print(f"  coach unavailable: {e!r}", flush=True)

    coach_emit(type="clear")
    coach_emit(type="phase", text="face only", phase="face")
    _coach_and_speak(
        site,
        f"Face only capture for {display_name(person_id, site.root)}. "
        "Stay at the desk. No voice recording this pass.",
    )
    out = []
    try:
        for i, plan in enumerate(takes, 1):
            coach_emit(type="clear")
            coach_emit(type="phase", text=f"face {i}/{len(takes)}")
            coach_emit(type="coach", text=plan["speak_angle"])
            cues.speak(site, f"Pose {i}. {plan['speak_angle']}")
            pose = plan.get("pose") or "front"
            # Extra settle for profile turn before facefinder locks
            time.sleep(1.4 if pose == "profile" else 1.2)
            rec = capture_face(
                site,
                person_id,
                station=plan.get("station") or "desk",
                pose=pose,
                take_plan_id=plan.get("id"),
                aim=True,
                quality_map=True,  # front + profile coached snaps
            )
            out.append(rec)
            coach_emit(type="ok", text=f"Pose {i} captured")
            cues.done_chirp(site)
            time.sleep(0.8)
    finally:
        restore_door_home(site)
        coach_emit(type="done", text="Face capture complete.")
        cues.session_end(site)
        if coach:
            time.sleep(1.0)
            try:
                coach_stop()
            except Exception:
                pass
    return out
