#!/usr/bin/env python3
"""Run the live lap: the user's recorded questions, Crissa answers, the announcer grades.

    CRISSA_DEMO=1 scripts/xspaces-live.py            # until the kill file
    CRISSA_DEMO=1 scripts/xspaces-live.py --once     # one lap

The kill file is xspaces-live.off in the Crissa data folder. Default off
when the env is unset. A read beat is her own voice on a written passage.
A question with no recording is skipped. Does not post to X. Files no card
itself: the crew drill key does that (lib/demo_krew_drill, house-only), and
only on its own word does the first lap file the demo cards.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import demo_lines  # noqa: E402
import xspaces_live as live  # noqa: E402
import crissa_home  # noqa: E402
import crissa_ports  # noqa: E402

# House modules a pack does not carry: absent = no announcer line, no hold, no oracle cover, no crew cards.
try:
    import demo_krew_drill  # noqa: E402
except ImportError:
    demo_krew_drill = None
try:
    import house_announce  # noqa: E402
except ImportError:
    house_announce = None
try:
    import oracle_cover  # noqa: E402
except ImportError:
    oracle_cover = None
try:
    import restart_window  # noqa: E402
except ImportError:
    restart_window = None


def _loop(env_name: str, port_name: str, house_env: str = "", house_key: str = "") -> str:
    """The env (the pack's name, then the house's old one), else this machine's own service (config/house-machine.json:
    the house's addresses, exactly as before the Crissa merge, 2026-10-11), else the port table."""
    got = os.environ.get(env_name, "").strip() or (os.environ.get(house_env, "").strip() if house_env else "")
    got = got or (crissa_home.house_value(house_key) if house_key else "")
    if got:
        return got.rstrip("/")
    return f"http://127.0.0.1:{crissa_ports.port(port_name)}"


def _under_home(key: str, default: str) -> Path:
    rel = default
    try:
        rel = str((live.settings().get("paths") or {}).get(key) or default)
    except (OSError, ValueError):
        rel = default
    path = Path(rel).expanduser()
    if path.is_absolute():
        return path
    return crissa_home.HOME / rel


PID_FILE = live.HOME / "xspaces-live.pid"
EAR_LOG = Path(os.environ.get("CRISSA_EAR_LOG") or os.environ.get("EAR_BRIDGE_LOG") or crissa_home.house_value("ear_log")
               or _under_home("ear_log", "logs/ear.jsonl"))
STT = (os.environ.get("CRISSA_STT_URL", "").strip() or os.environ.get("RAIL_STT_URL", "").strip()
       or f"http://127.0.0.1:{crissa_ports.port('stt')}/stt")
CONDUCTOR = _loop("CRISSA_PLAYER_URL", "player", "HOUSE_CONDUCTOR_URL", "wall_conductor")
GLASS = _loop("CRISSA_GLASS_URL", "player", "HOUSE_GLASS_URL", "panel_url")
POSE = ROOT / "display" / "crissa-poses"
WALL_PY = ROOT / "lib" / "house_wall_ascii.py"


class EarTail:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.pos = self.path.stat().st_size if self.path.is_file() else 0
        self.controls: list[str] = []
        self.reply = ""
        self.reply_ts = ""
        self.silent: list[str] = []

    def poll(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8", errors="replace") as fh:
            fh.seek(self.pos)
            data = fh.read()
            self.pos = fh.tell()
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") == "heard":
                word = live.control_of(str(row.get("text") or ""))
                if word:
                    self.controls.append(word)
            elif row.get("event") == "reply" and row.get("reply"):
                self.reply = str(row["reply"])
                self.reply_ts = str(row.get("ts") or "")
            elif row.get("event") == "dispatch" and row.get("level") == "silent":
                user = str(row.get("user") or "").strip()
                if user:
                    self.silent.append(user)


def shift_reader_live() -> bool | None:
    """True when the wall process started after house_wall_ascii.py changed.

    A process that imported the renderer earlier keeps the old crop. None
    when no wall process is visible.
    """
    try:
        mtime = WALL_PY.stat().st_mtime
        clk = os.sysconf("SC_CLK_TCK")
        boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return None
    found = False
    old = False
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            start = boot + int((d / "stat").read_text().split()[21]) / clk
        except (OSError, IndexError, ValueError):
            continue
        if "house_wall_ascii" not in cmd:
            continue
        found = True
        if start < mtime - 1.0:
            old = True
    if not found:
        return None
    return not old


def _json_post(url: str, body: dict, timeout: float = 10) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read() or b"{}")
        except ValueError:
            return {"taken": False, "ok": False, "why": f"HTTP {exc.code}"}


def _play_wav(path: str, text: str = "") -> tuple[bool, str]:
    if house_announce is None:
        return False, "no player"
    payload = {"category": "test", "title": "live lap"}
    if text:
        payload["text"] = text      # the ear subtracts the heard copy (lib/house_announce._paplay)
    return house_announce._paplay(path, payload)


def play_pcm(pcm: bytes, rate: int, text: str = "") -> tuple[bool, str]:
    # Her voice band is −26. A hot take comes down onto it. Playback only.
    import loudness
    pcm = demo_lines.level_pcm(pcm, target_db=loudness.LEVEL_TARGET_DB, allow_cut=True)
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="xspaces-live-")
    os.close(fd)
    try:
        demo_lines.write_wav(Path(path), pcm, rate)
        return _play_when_clear(path, wait_s=float(live.dial("speaker_wait_s")), text=text)
    finally:
        Path(path).unlink(missing_ok=True)
        Path(path + ".sped.wav").unlink(missing_ok=True)


def _play_when_clear(path: str, *, wait_s: float, text: str = "") -> tuple[bool, str]:
    """Paplay, waiting out the post-speech cooldown.

    mic_busy treats the turn we just fed, and her reply, as a live mic for
    HOUSE_ANNOUNCE_SPEECH_PAUSE_SEC (12s). A grade or the next line would
    otherwise be skipped. A real open mouth still fails the beat.
    """
    deadline = time.time() + wait_s
    ok, why = False, "unplayed"
    while True:
        ok, why = _play_wav(path, text)
        if ok or not str(why).startswith("mic_busy") or time.time() >= deadline:
            return ok, why
        time.sleep(0.4)


# THE ANNOUNCER'S LINES ARE MADE BEFORE THE LAP (the User 2026-10-21: "not our clone in demo"; the clone voices live on
# Audio8, which measured 23.7 s for a 5.3 s line under load where Pocket's stock voice took 3-4 s). Every line the lap
# knows in advance (the opener, the notices, the intros, the fillers, the grade lines) is synthesised once by
# `--prewarm` into ANNOUNCE_CACHE, keyed by engine + voice + text; `say` plays the cached wav when it exists and
# synthesises (and caches) only a line it has never said. Kill HOUSE_DEMO_ANNOUNCE_CACHE=0.
ANNOUNCE_CACHE = live.HOME / "demo-lines" / "announcer"


def _announce_key(text: str) -> Path:
    import hashlib
    eng = house_announce.pa_engine() if house_announce is not None and hasattr(house_announce, "pa_engine") else "audio8"
    voice = house_announce.pa_voice() if house_announce is not None and hasattr(house_announce, "pa_voice") else ""
    if eng == "pocket" and hasattr(house_announce, "pa_pocket"):
        voice = house_announce.pa_pocket(voice) or voice      # the voice that actually speaks (a remap is a new key)
    h = hashlib.sha1(f"{eng}|{voice}|{' '.join(text.split())}".encode("utf-8")).hexdigest()[:16]
    return ANNOUNCE_CACHE / f"{eng}-{h}.wav"


_VOICES_SEEN: dict = {}


def pocket_lists_voice() -> bool:
    """True unless the engine is Pocket and its server's /health does not list the voice the line would speak in (it
    then answers in a stand-in voice; caching that under the real voice's key would replay the wrong voice for good).
    Asked at most once a minute."""
    if house_announce is None or not hasattr(house_announce, "pa_engine") or house_announce.pa_engine() != "pocket":
        return True
    voice = house_announce.pa_pocket(house_announce.pa_voice()) if hasattr(house_announce, "pa_pocket") else ""
    now = time.time()
    if _VOICES_SEEN.get("at", 0) < now - 60:
        base = str(getattr(house_announce, "POCKET_TTS", "")).split("/v1/")[0]
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as r:
                _VOICES_SEEN["voices"] = set(json.loads(r.read() or b"{}").get("voices") or [])
        except Exception:  # noqa: BLE001
            _VOICES_SEEN["voices"] = set()
        _VOICES_SEEN["at"] = now
    return bool(voice) and voice in _VOICES_SEEN.get("voices", set())


def _announce_wav(text: str) -> tuple[bytes, float, bool]:
    """(the boosted wav, seconds it took, from cache). A Pocket line is cached only when the server lists its voice."""
    use = os.environ.get("HOUSE_DEMO_ANNOUNCE_CACHE", "1").strip().lower() not in ("0", "off", "false", "no")
    use = use and pocket_lists_voice()
    path = _announce_key(text)
    if use and path.is_file():
        return path.read_bytes(), 0.0, True
    t0 = time.time()
    wav = house_announce.boost_pa_wav(house_announce.synth_audio8(text))
    took = time.time() - t0
    if use:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(wav)
            os.replace(tmp, path)
        except OSError:
            pass
    return wav, took, False


def say(text: str) -> None:
    """Announcer voice. Skips the quiet-hours gate: this lap was asked for."""
    if house_announce is None:
        print(f"grade unsaid (no announcer): {text}", flush=True)
        return
    try:
        wav, took, cached = _announce_wav(text)
        if not cached and took > 2.0:
            print(f"announcer live synth {took:.1f} s: {text[:60]}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"grade unsaid ({type(exc).__name__}): {text}", flush=True)
        return
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="xspaces-grade-")
    os.close(fd)
    try:
        Path(path).write_bytes(wav)
        ok, why = _play_when_clear(path, wait_s=float(live.dial("speaker_wait_s")))
        if not ok:
            print(f"grade unheard ({why}): {text}", flush=True)
    finally:
        Path(path).unlink(missing_ok=True)


def open_session() -> None:
    """The wall's LISTENING chip. A command with no wake word is skipped while idle.

    The chip sets a flag the ear loop reads at the top of its poll, and that
    poll can be inside a 4s turn fetch. Wait until /health says the session
    is open before his line plays. Voice off never opens it.
    """
    base = os.environ.get("CRISSA_EAR_URL", os.environ.get("EAR_BRIDGE_HEALTH", "http://127.0.0.1:8194")).rstrip("/")
    req = urllib.request.Request(f"{base}/v1/session/open", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        r.read()
    deadline = time.time() + 6.0
    while time.time() < deadline:
        with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
            body = json.loads(r.read() or b"{}")
        if body.get("session_open"):
            return
        time.sleep(0.15)
    raise TimeoutError("session did not open")


def close_session() -> None:
    req = urllib.request.Request(f"{os.environ.get('CRISSA_EAR_URL', os.environ.get('EAR_BRIDGE_HEALTH', 'http://127.0.0.1:8194')).rstrip('/')}/v1/session/pause",
                                 data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        r.read()


def _reply_is_fresh(ts: str, t0: float) -> bool:
    """A reply logged before this wait belongs to the previous line."""
    if not ts:
        return True
    try:
        return datetime.fromisoformat(ts).timestamp() >= t0 - 1.0
    except ValueError:
        return True


DAILY_JSON = Path(crissa_home.house_value("daily_json")) if crissa_home.house_value("daily_json") \
    else _under_home("daily", "daily.json")


def improving_line() -> str:
    """The Improving lamp, from the file the page was built from. Empty if it is missing."""
    try:
        doc = json.loads(DAILY_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = ""
    for lamp in doc.get("lamps") or []:
        if isinstance(lamp, dict) and lamp.get("key") == "improving":
            value = str(lamp.get("value") or "").strip()
            break
    if not value and isinstance(doc.get("improving"), dict):
        value = str(doc["improving"].get("value") or "").strip()
    return f"Improving. {value}." if value else ""


def queue_note() -> str:
    """Pending and running render orders. There is no named render-queue screen."""
    try:
        import render_orders
        rows = render_orders.orders()
    except Exception as exc:  # noqa: BLE001
        return f"Order list unreadable ({type(exc).__name__})."
    pending = [o for o in rows if o.get("state") == "pending"]
    running = [o for o in rows if o.get("state") == "running"]
    parts = [f"Pending {len(pending)}.", f"Running {len(running)}."]
    for o in (running + pending)[:6]:
        parts.append(f"{o.get('id') or '?'} {o.get('workflow') or o.get('family') or ''}".strip())
    if not pending and not running:
        parts.append("Nothing waiting.")
    return " ".join(parts)


def ask_hands(ask: str, *, pause: bool = True) -> dict:
    """One crissa-hands job. ok is the process; the library still requires confirmed: shown.

    pause homes the wall. The broadcast path leaves the screen up, so it passes pause=False.
    """
    ask = " ".join((ask or "").split())
    if not ask:
        return {"ok": False, "text": "", "ms": 0, "why": "empty"}
    if pause:
        try:
            close_session()
        except Exception:  # noqa: BLE001
            pass
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "crissa-hands.py"), ask],
            capture_output=True, text=True, timeout=(float(live.dial("hands_timeout_s")) or None), cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return {"ok": False, "text": out[-1500:], "ms": int((time.time() - t0) * 1000), "why": "timeout"}
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 and not text:
        text = (proc.stderr or "").strip()[:500]
    low = text.lower()
    ok = (proc.returncode == 0 and "nothing changed" not in low
          and "did not finish" not in low and "hands are already busy" not in low)
    return {"ok": ok, "text": text[:2000], "ms": int((time.time() - t0) * 1000), "rc": proc.returncode}


def speak_crissa(text: str) -> dict:
    """Her voice. The announcer does not say this line. A reading is the whole passage."""
    text = " ".join((text or "").split())
    if not text:
        return {"ok": False, "why": "empty"}
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="xspaces-crissa-")
    os.close(fd)
    # 397 characters of the Melville line took 28 s on 2026-10-10. A longer
    # passage keeps that pace plus headroom, and never less than the old 90 s.
    # the speech wait scales with the text (measured 2026-10-10: 397 characters of Melville took 28 s): a floor and a
    # per-character allowance, both dials; it guards a hung house-speak, never cuts a line that is still playing
    timeout = max(float(live.dial("speak_wait_floor_s")), float(live.dial("speak_wait_per_char_s")) * len(text) + 40)
    try:
        Path(path).write_text(text + "\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "house-speak.py"),
             "--voice", "crissa", "--text-file", path],
            cwd=str(ROOT), timeout=timeout, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = err[-1][:180] if err else ""
            print(f"crissa unheard (rc {proc.returncode}): {tail}", flush=True)
            return {"ok": False, "why": tail or f"rc {proc.returncode}"}
        return {"ok": True}
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"crissa unheard ({type(exc).__name__}): {text[:80]}", flush=True)
        return {"ok": False, "why": type(exc).__name__}
    finally:
        Path(path).unlink(missing_ok=True)


def mouth() -> dict:
    """The wall's live dial. An empty body reads it and does not write."""
    return _json_post(f"{CONDUCTOR}/v1/mouth-tune", {}, timeout=2)


def mouth_set(semis: float) -> None:
    """One lead, for the line about to start. The caller puts the old value back."""
    _json_post(f"{CONDUCTOR}/v1/mouth-tune", {"sheet_lead_semis": semis}, timeout=2)


def screen(body: dict) -> dict:
    """The glass's screen route, whole: a still beat paints its art through it and reads the screen back."""
    _json_post(f"{GLASS}/v1/screen", body, timeout=5)
    with urllib.request.urlopen(f"{GLASS}/v1/screen", timeout=3) as r:
        return json.loads(r.read() or b"{}")


def show_note(title: str, text: str) -> None:
    """A glass note for the capture. The next beat's clear takes it down."""
    body: dict = {"notes": text}
    if title:
        body["notes_title"] = title
    _json_post(f"{GLASS}/v1/screen", body, timeout=3)


def post_stt(pcm: bytes, rate: int) -> None:
    req = urllib.request.Request(STT, data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(rate)})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def cue(key: str, ttl: float) -> dict:
    got = _json_post(f"{CONDUCTOR}/v1/cue", {"key": key, "ttl": ttl, "by": "xspaces-live", "bridge": True})
    if "taken" not in got:
        got["taken"] = False
        got.setdefault("why", "no taken")
    return got


def flourish_up() -> str:
    """What is on the glass when it is not a loop (a flourish's key), else ''. The lap waits for a flourish to end
    before its next beat (the User 2026-10-20: "she yawned but broadcast did not pause")."""
    try:
        with urllib.request.urlopen(f"{CONDUCTOR}/v1/state", timeout=2) as r:
            st = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return ""
    role = str(st.get("role") or "")
    return f"a flourish is on the glass ({st.get('now')})" if role == "flourish" else ""


def zoom_get() -> tuple[bool, float]:
    with urllib.request.urlopen(f"{GLASS}/v1/house", timeout=3) as r:
        house = json.loads(r.read() or b"{}")
    scr = house.get("screen") or {}
    fov = scr.get("fov_zoom_crissa")
    return bool(scr.get("crissa_hole")), float(fov) if isinstance(fov, (int, float)) else 1.0


def clear_pages() -> None:
    """Take the notes and the doc sheet down. The news line stays.

    Called as she moves on to the next beat. A page beat puts its own sheet
    up afterwards. Headline and the one-line note belong to the news cron.
    """
    try:
        _json_post(f"{GLASS}/v1/screen", {"clear": ["notes", "doc"]}, timeout=3)
    except Exception as exc:  # noqa: BLE001
        print(f"page stayed ({type(exc).__name__})", flush=True)
    else:
        try:
            import doc_render_shift
            doc_render_shift.release()
        except Exception:  # noqa: BLE001
            pass


def zoom_set(hole: bool, fov: float) -> None:
    _json_post(f"{GLASS}/v1/action", {"action": "crissa_hole_on" if hole else "crissa_hole_off"})
    _json_post(f"{GLASS}/v1/action", {"action": "fov_zoom", "value": fov, "channel": "crissa"})


def shift_set(origin) -> None:
    path = crissa_home.HOME / "crissa-render-shift.json"
    if not origin:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps({"cx": round(float(origin[0]), 4), "cy": round(float(origin[1]), 4)}) + "\n",
                    encoding="utf-8")


def cover_of(key: str) -> dict:
    if oracle_cover is None:
        return {"frames": 0, "covered": 0, "clear": False}
    doc = oracle_cover.load_head(POSE, key)
    if not doc:
        return {"frames": 0, "covered": 0, "clear": False}
    fx, fy = oracle_cover.oracle_screen_fraction()
    return oracle_cover.coverage(doc, fx, fy)


def origin_of(key: str):
    if oracle_cover is None:
        return None
    doc = oracle_cover.load_head(POSE, key)
    if not doc:
        return None
    fx, fy = oracle_cover.oracle_screen_fraction()
    return oracle_cover.origin_on_nose(doc, fx, fy)


def preflight() -> str:
    import subprocess
    try:
        clients = subprocess.run(["pactl", "list", "clients", "short"], capture_output=True, text=True, timeout=5)
        n = len([ln for ln in (clients.stdout or "").splitlines() if ln.strip()])
    except (OSError, subprocess.SubprocessError) as exc:
        return f"pactl: {type(exc).__name__}"
    if n >= 60:
        return f"pactl clients {n} of 64"
    try:
        import audio_sink
        sink = audio_sink.speak_sink()
        sinks = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=5)
        if sink and sink not in (sinks.stdout or ""):
            return f"speak sink {sink} is not loaded"
    except Exception as exc:  # noqa: BLE001
        return f"sink: {type(exc).__name__}"
    return ""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _hands_busy_probe():
    """lib/demo_lap_hold.hands_busy (her hands lock, probed and released at once), None when absent."""
    try:
        import demo_lap_hold
        return demo_lap_hold.hands_busy
    except ImportError:
        return None


def live_room(tail: EarTail) -> live.Room:
    posted = {"pcm": b"", "rate": 16000}

    def post(pcm: bytes, rate: int) -> None:
        posted["pcm"] = pcm
        posted["rate"] = rate
        post_stt(pcm, rate)

    def wait_reply(timeout: float):
        """Her reply, or one repost when the voice gate dropped this recorded question."""
        t0 = time.time()
        tail.reply = ""
        tail.reply_ts = ""
        retried = False

        def take():
            if not tail.reply:
                return None
            if not _reply_is_fresh(tail.reply_ts, t0):
                tail.reply = ""
                return None
            return tail.reply, int((time.time() - t0) * 1000)

        def consider(texts: list[str]) -> None:
            nonlocal retried
            if retried or tail.reply or not posted["pcm"]:
                return
            if not any(live.matches_tape(t) for t in texts):
                return
            retried = True
            print("voice gate was silent, posting the same line once", flush=True)
            try:
                post_stt(posted["pcm"], posted["rate"])
            except Exception as exc:  # noqa: BLE001
                print(f"repost failed ({type(exc).__name__})", flush=True)

        tail.poll()
        pending = list(tail.silent)
        tail.silent.clear()
        got = take()
        if got is None:
            consider(pending)
        while got is None and time.time() - t0 < timeout:
            tail.poll()
            got = take()
            if got:
                break
            fresh = list(tail.silent)
            tail.silent.clear()
            consider(fresh)
            time.sleep(0.25)
            got = take()
        try:
            close_session()
        except Exception:  # noqa: BLE001
            pass
        if got:
            return got
        return "", int((time.time() - t0) * 1000)

    def pull():
        tail.poll()
        got, tail.controls = tail.controls, []
        return got

    # A broadcast lap must not pause between drills: pause homes the wall.
    hands_ask = (lambda ask: ask_hands(ask, pause=False)) if live.broadcast_on() else ask_hands
    return live.Room(
        play=play_pcm, post=post, say=say, cue=cue,
        zoom_get=zoom_get, zoom_set=zoom_set, shift_set=shift_set, shift_live=shift_reader_live,
        hold_start=lambda: (restart_window.busy_now("") or "") if restart_window else "",
        ear=lambda: (restart_window.ear_health() or {}) if restart_window else {},
        hands=lambda: bool(restart_window and restart_window.proc_running("crissa-hands.py")),
        hands_busy=_hands_busy_probe(),
        on_air=lambda: bool(restart_window and restart_window.proc_running("house-speak", "house-speak.py")),
        flourish=flourish_up,
        wait_reply=wait_reply, pull_controls=pull,
        take=demo_lines.line_pcm, cover=cover_of, origin=origin_of, open_session=open_session,
        close_session=close_session, ask_hands=hands_ask,
        ask_dev=lambda ask: ask_hands(ask, pause=False),
        improving=improving_line, speak_crissa=speak_crissa,
        mouth=mouth, mouth_set=mouth_set, show_note=show_note, screen=screen,
        queue_note=queue_note, clear_pages=clear_pages,
        log=lambda *a: print(*a, flush=True),
    )


def known_lines() -> list[str]:
    """Every announcer line the lap can say that is known before it starts."""
    cfg = live.settings()
    out: list[str] = [str(cfg.get("opener") or ""), str(cfg.get("question_lead") or ""), "House Daily.", "Paused.",
                      "Pass.", "Slow, but it landed.", "Fail."]

    for b in live.all_beats(cfg) + [x.get("fallback") for x in live.all_beats(cfg) if isinstance(x.get("fallback"), dict)]:
        if isinstance(b, dict):
            out += [str(b.get("notice") or ""), str(b.get("after") or "")]
    ds = cfg.get("drill_sets") if isinstance(cfg.get("drill_sets"), dict) else {}
    out += [str(ds.get("intro") or ""), str(ds.get("filler") or "")] + [str(v) for v in (ds.get("grade_lines") or {}).values()]
    try:
        import demo_synth
        c = demo_synth.conf(cfg)
        out += [str(c.get("notice") or ""), str(c.get("after_fail_line") or "")]
        r = c.get("renders") if isinstance(c.get("renders"), dict) else {}
        out += [str(r.get(k) or "") for k in ("filler", "say_none")] + ["The scorer did not finish this time."]
        for t in demo_synth.stills():
            out.append(str(t.get("say") or ""))
    except Exception:  # noqa: BLE001
        pass
    # the timed grades last, the likely ones first (a beat's answer lands in 8 to 60 s)
    secs = list(range(8, 61)) + list(range(61, 121)) + list(range(1, 8))
    out += [f"Pass. {n} second{'s' if n != 1 else ''}." for n in secs]
    seen, rows = set(), []
    for t in out:
        t = " ".join(t.split())
        if t and t not in seen:
            seen.add(t)
            rows.append(t)
    return rows


def prewarm() -> int:
    if house_announce is None:
        print("prewarm: no announcer")
        return 2
    if not pocket_lists_voice():
        print(f"prewarm: refused, the Pocket server does not list {house_announce.pa_pocket(house_announce.pa_voice())} "
              f"(it would answer in a stand-in voice)", flush=True)
        return 3
    rows = known_lines()
    made = hit = bad = 0
    t0 = time.time()
    for t in rows:
        try:
            _, took, cached = _announce_wav(t)
            hit += cached
            made += not cached
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"prewarm failed ({type(exc).__name__}): {t[:60]}", flush=True)
    print(f"prewarm: {len(rows)} lines, {made} made, {hit} cached, {bad} failed, {time.time() - t0:.0f} s, "
          f"engine {house_announce.pa_engine()} voice {house_announce.pa_voice()} "
          f"(pocket {house_announce.pa_pocket(house_announce.pa_voice()) if hasattr(house_announce, 'pa_pocket') else '-'})", flush=True)
    return 0 if not bad else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="one lap, then stop")
    ap.add_argument("--laps", type=int, default=0, help="stop after N laps (0 = until the kill file)")
    ap.add_argument("--prewarm", action="store_true", help="synthesise every known announcer line into the cache, then exit")
    ap.add_argument("--prescore", action="store_true",
                    help="score every render take the lap can draw (off air) and keep the verdicts, then exit")
    args = ap.parse_args(argv)
    if args.prewarm:
        return prewarm()
    if args.prescore:
        syn = live._synth()
        if syn is None or not hasattr(syn, "prescore"):
            print("prescore: this install has no lib/demo_synth")
            return 2
        got = syn.prescore(live.settings(), log=lambda m: print(m, flush=True))
        print(f"prescore: {got}")
        return 0 if not got.get("failed") else 1
    if not live.enabled():
        print(f"off: set CRISSA_DEMO=1 and remove {live.OFF_FILE}")
        return 2
    if PID_FILE.is_file():
        try:
            old = int(PID_FILE.read_text().strip() or "0")
        except ValueError:
            old = 0
        if old and _pid_alive(old):
            print(f"already running as pid {old}")
            return 2
    why = preflight()
    if why:
        print(f"preflight: {why}")
        return 3
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    # THE LAP HOLDS HER HANDS (lib/demo_lap_hold, 10-21 laps 2-3: her Decide watch took them mid-lap and she passed on
    # three questions): her hands children carry HOUSE_DEMO_LAP=1, every other hands job waits for the lap.
    try:
        import demo_lap_hold
        os.environ["HOUSE_DEMO_LAP"] = "1"
        demo_lap_hold.arm(os.getpid())
    except ImportError:
        demo_lap_hold = None
    if live.broadcast_on():
        # Her hands say what they are doing (the User 2026-10-20: "doing a lookup", "editing the form" ... instead of
        # silence while she works): the tool-callout hook speaks in a hands child when this is set (it is silent in a
        # nested child otherwise, because the ear bridge speaks the callouts on the ear path).
        os.environ.setdefault("HOUSE_HANDS_CALLOUTS", "1")
    tail = EarTail(EAR_LOG)
    room = live_room(tail)
    rc = 0
    n = 0
    # The crew drill key files the demo cards now (the User 2026-10-09). The lap does only when that module asks.
    cards_lap = bool(demo_krew_drill is not None and demo_krew_drill.in_lap_wanted())
    try:
        while True:
            if not live.enabled():
                print("off")
                break
            res = live.run_lap(
                room=room,
                broadcast=live.broadcast_on() or bool(live.settings().get("broadcast")),
                cards=cards_lap,
            )
            cards_lap = False
            n += 1
            print(f"lap {res.get('stamp')} stop={res.get('stop')} ledger={res.get('ledger')}", flush=True)
            if res.get("stop", "").startswith("held"):
                rc = 3
                break
            if args.once or (args.laps and n >= args.laps):
                break
            time.sleep(2.0)
    finally:
        if demo_krew_drill is not None:
            demo_krew_drill.disarm()
        PID_FILE.unlink(missing_ok=True)
        if demo_lap_hold is not None:
            demo_lap_hold.release()
        try:
            shift_set(None)
        except OSError:
            pass
        try:
            close_session()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
