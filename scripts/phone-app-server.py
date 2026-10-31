#!/usr/bin/env python3
"""The phone app's server (the User 2026-10-16: "see if you could serve the forward loop as an app"): the web app,
the phone packs, and her voice with its mouth sheet.

    GET  /                      the app (display/phone-app/)
    GET  /pack/<name>/<file>    a phone pack (lib/phone_pack.py), byte ranges for the video
    GET  /api/packs             the packs on this server
    POST /api/say   {"text"}    her voice saying it: {"audio": url, "sheet": {...}}   (lib/phone_sheet.py)
    POST /api/chat  {"text", "history"?, "say"?}  a reply from the agent, then said: {"reply", "audio", "sheet"};
                    "say": false answers the words only
    POST /api/hear[?chat=1]     16-bit mono PCM (X-Sample-Rate) -> {"text"} on this computer's /stt; chat=1 answers too
    POST /api/pair  {"code", "name"} -> {"token"} (with --pairing; GET /api/pair/code from the computer itself)
    POST /api/floor {"on"}      the phone's talk button has the floor (true; refresh every few s) or gives it back (false):
                                while the phone has it the desk ear takes no turn (lib/phone_floor)
    POST /api/mic               the phone's open mic, up to 2 s of 16-bit mono PCM a chunk (X-Sample-Rate), relayed to
                                the ear, which scores it against the desk mic (CRISSA_PHONE_MIC=1; CRISSA_EAR_PHONE_MIC=1)
    GET  /api/session           is she on the phone: {"active", "active_until", ...} (lib/phone_session)
    POST /api/session {"end"}   the page or app closed or hid: the session ends now, and the desk Wall walks her back in
    POST /api/session {"here"}  the page's heartbeat while visible (every 10 s): never a use; when beats stop the session
                                ends within CRISSA_PHONE_BEAT_S (25), end beacon or not (round 39; CRISSA_PHONE_BEAT=0)
    POST /api/handoff           the desk Wall's QR card (loopback only): {"url": "http://<lan>:<port>/?handoff=<token>",
                                "token", "expires", "lan", "port"}, a single-use token (lib/phone_handoff)
    POST /api/handoff/claim {"token", "name"?, "want_token"?}  the page or app that came from the QR, once:
                                {"ok", "house": "<lan>:<port>", "paired"}; with --pairing the claim IS the pairing (the
                                page gets an HttpOnly cookie its video and pack requests carry; "want_token" returns the
                                token for the app's Keychain). Refused: 403 {"why": used|expired|unknown, "error"}
    POST /api/handoff/leave     Disconnect: this phone's own pairing is forgotten and its cookie cleared
    GET  /api/connection        {"handoff", "house", "pairing", "paired"}: whether a page opened without the QR waits on
                                its Connect card (CRISSA_PHONE_HANDOFF=0: never; the page is on the house as before);
                                answered before pairing, so a page can tell it must scan
    POST /api/mb/<id>           house-speak's sheet for line <id> lands here (VO_MOUTH_URL, loopback only); GET answers empty

A line is house-speak's own: her voice (the install's own; the User 2026-10-17: the house Crissa voice, Grok
Imagine, never the one cut from the Nano take), the pack's own tune handed over explicitly (no bench
needed), rendered without playing (HOUSE_SPEAK_RENDER_ONLY; an older house-speak plays it into the silent
`house-mute` sink), kept with --out, and its sheet caught here. One line at a time. LAN only, no login: run it on a trusted network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# The Crissa install whose voice and mouth clock a line uses (its scripts/house-speak.py and lib/): CRISSA_ROOT, else
# the tree this file sits in (the house). The app's own repo keeps phone_sheet.py beside this file.
CRISSA = Path(os.environ.get("CRISSA_ROOT") or ROOT).expanduser().resolve()
for _p in (ROOT / "lib", CRISSA / "lib", HERE):     # this file's own folder first: its phone_sheet wins
    if str(_p) in sys.path:
        sys.path.remove(str(_p))
    sys.path.insert(0, str(_p))

import phone_sheet  # noqa: E402

APP_DIR = (ROOT / "app") if (ROOT / "app" / "index.html").is_file() else (ROOT / "display" / "phone-app")
def _state_home() -> Path:
    """The install's state home (lib/crissa_home: CRISSA_HOME, else its manifest's), ~/.crissa when it cannot say
    (round 28: a moved state home is followed, never a literal ~/.crissa)."""
    try:
        import crissa_home
        return Path(crissa_home.HOME)
    except Exception:  # noqa: BLE001
        return Path("~/.crissa").expanduser()


STATE = Path(os.environ.get("CRISSA_PHONE_HOME") or (_state_home() / "phone")).expanduser()


def pairing_default(env: dict | None = None) -> bool:
    """PAIRING IS ON IN A PACK (round 38, the User's "could someone else hijack her?", .101 round 37: with it off anyone
    on the Wi-Fi could chat on his agent, make her speak and walk her out of the desk). The QR's single-use claim is the
    pairing; every other device gets 401 and this computer is always let in. CRISSA_PHONE_PAIRING=0|1 decides; unset,
    the house keeps its own server as it was (no QR card there) and every other install is paired."""
    e = os.environ if env is None else env
    v = str(e.get("CRISSA_PHONE_PAIRING") or "").strip().lower()
    if v:
        return v in ("1", "true", "yes", "on")
    try:
        import crissa_home
        return not crissa_home.is_house()
    except Exception:  # noqa: BLE001  a copy with no install around it is a pack
        return True
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css",
         ".json": "application/json", ".webmanifest": "application/manifest+json", ".mp4": "video/mp4",
         ".jpg": "image/jpeg", ".png": "image/png", ".wav": "audio/wav", ".svg": "image/svg+xml"}
CFG: dict = {}
LINE_LOCK = threading.Lock()
CAUGHT: dict[str, list] = {}
MAX_TEXT = 600
PERSONA = ("You are Crissa, a warm, quick-witted assistant who lives on the user's phone as a talking face. "
           "Answer in one to three short spoken sentences, plain words, no lists, no markdown, no emoji.")


def pack_manifest(name: str = "") -> dict:
    p = Path(CFG["packs"]) / (name or CFG["pack"]) / "manifest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def line_env(lid: str) -> dict[str, str]:
    """One line's house-speak env: the pack's own tune (explicit, no conductor asked), its sheet caught here,
    rendered without playing where house-speak has HOUSE_SPEAK_RENDER_ONLY (else played into the silent sink)."""
    m = pack_manifest()
    sync0 = m.get("sync_zero_ms")
    if sync0 is None and m.get("pose_dir"):           # a pack baked before 2026-10-17 names its library's lock
        try:
            sync0 = json.loads((Path(m["pose_dir"]) / "mouth-dials.json").read_text(encoding="utf-8")).get("sync_zero_ms")
        except (OSError, ValueError):
            sync0 = 0.0
    env = dict(m.get("speak_env") or {"HOUSE_MOUTH_G2P": "espeak", "HOUSE_CLIP_FPS": "24"})
    env.update(phone_sheet.speak_env(m.get("dials") or {}, float(sync0 or 0.0)))
    env.update(phone_sheet.classes_env(m.get("classes")))     # round 41: never Ah/Aa to a pack that lacks them
    env.update({
        "VO_MOUTH_URL": "http://127.0.0.1:%d/api/mb/%s" % (CFG["port"], lid),
        "HOUSE_SPEAK_WHO_TAG": "phone",                   # not the house talking: the glass skips crissa@phone
        "HOUSE_SPEAK_BUFFER": "1",                        # made and aligned before her first word
        "HOUSE_SPEAK_RENDER_ONLY": "1",
    })
    if CFG.get("bench"):
        env["HOUSE_CONDUCTOR"] = CFG["bench"]
    return env


def _xfade_ms() -> float:
    """The mouth blend the page uses (the User 2026-10-20, "a touch of smoothing"): one semiframe, the desk Wall's own
    value since round 23 (config/crissa-mouth.json HOUSE_MOUTH_XFADE_MS). Kill: CRISSA_PHONE_XFADE=0 (hard swaps)."""
    if os.environ.get("CRISSA_PHONE_XFADE", "1").strip().lower() in ("0", "false", "off", "no"):
        return 0.0
    return round(1000.0 / 48.0, 3)


def _session_file() -> Path:
    return Path(CFG.get("session_file") or os.environ.get("CRISSA_PHONE_SESSION") or (_state_home() / "phone-session.json"))


def _session(why: str = "floor", hold_s: float = 0.0, end: bool = False) -> dict:
    """SHE IS ON THE PHONE (the User 2026-10-20, .101 ASK-20261020-0230; lib/phone_session): every use of the phone keeps
    the session active for its own length plus the idle window, and the desk Wall shows her empty office meanwhile.
    The open mic's chunks are not a use (a phone in a pocket streams them too); its turns take the floor. Never raises."""
    try:
        import phone_session
        if end:
            return phone_session.end(_session_file())
        if why == "here":                         # the page's heartbeat (round 39): never a use
            return phone_session.beat(_session_file())
        return phone_session.use(_session_file(), why, hold_s)
    except Exception as e:  # noqa: BLE001  the session never holds up a line
        print("[phone-app] session: %s: %s" % (type(e).__name__, e), file=sys.stderr, flush=True)
        return {}


def _floor(for_s: float = 0.0, on: bool = True, line: bool = False) -> dict:
    """THE PHONE HAS THE FLOOR (the User 2026-10-18 18:2x, "one or the other from that button"; lib/phone_floor): a take
    for at least `for_s` (default its TTL), or a release. Every voice-in, chat and line takes it on the server's side too,
    so a phone that never posts /api/floor still keeps the desk out while it talks. Never raises; {} when off.
    A take is also a use of the phone (_session), whether or not the floor is switched on."""
    if on:
        _session("line" if line else "floor", for_s if line else 0.0)
    try:
        import phone_floor
        if not phone_floor.enabled():
            return {}
        f = Path(CFG.get("floor_file") or (_state_home() / "phone-floor.json"))
        return phone_floor.take(f, for_s or phone_floor.TTL_S, line=line) if on else phone_floor.release(f)
    except Exception as e:  # noqa: BLE001  the floor never holds up a line
        print("[phone-app] floor: %s: %s" % (type(e).__name__, e), file=sys.stderr, flush=True)
        return {}


def _say(text: str) -> dict:
    """house-speak one line into the silent sink; its WAV and its sheet."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]
    if not text:
        return {"ok": False, "error": "nothing to say"}
    lid = uuid.uuid4().hex[:12]
    lines = Path(CFG["lines"])
    lines.mkdir(parents=True, exist_ok=True)
    wav = lines / (lid + ".wav")
    CAUGHT[lid] = []
    env = dict(os.environ)
    env.update(line_env(lid))
    t0 = time.time()
    with LINE_LOCK:
        argv = [CFG["python"], CFG["speak"], "--text", text, "--out", str(wav)]
        argv += ["--voice", CFG["voice"]] if CFG.get("voice") else []
        argv += ["--sink", CFG["sink"]] if CFG.get("sink") else []
        p = subprocess.run(argv,
                           env=env, capture_output=True, text=True, timeout=180)
    log = (p.stdout or "") + (p.stderr or "")
    payloads = CAUGHT.pop(lid, [])
    if p.returncode or not wav.is_file():
        return {"ok": False, "error": "the voice failed (%s)" % p.returncode, "log": log[-600:]}
    m = re.search(r"sheet lead (-?\d+) ms", log)
    lead = (int(m.group(1)) / 1000.0) if m else 0.0
    samples, sr = read_wav(wav)
    sh = phone_sheet.sheet(payloads, samples, int(sr), lead_s=lead, have=pack_manifest().get("classes"))
    # .101 round 30 (the User 2026-10-18: "she said a second sentence on mobile but her lips were still for 2nd
    # sentence"; the same 9 s line replayed warm had a whole sheet): one line per say, so a sheet that stops early
    # says why here instead of in house-speak's output, which was dropped
    try:
        cl = sh.get("classes") or []
        last = len(cl) - 1
        while last >= 0 and str(cl[last]).startswith("rest"):
            last -= 1
        moves_to = float(sh.get("t0") or 0.0) + (last + 1) * float(sh.get("hop") or 0.0)
        why = [ln.strip()[:160] for ln in log.splitlines()
               if any(k in ln for k in ("mouth clock", "mouth POST failed", "unstamped", "fell back", "align"))][:4]
        # round 32 (.101: the bad line's reply text could not be recovered): her words ride on the note, 200 chars
        print("[phone-app] line %s: %.2f s of voice, mouth to %.2f s, %d sheet posts, %d sentences%s; said %r"
              % (lid, float(sh.get("dur") or 0.0), moves_to, len(payloads), len(re.findall(r"[.!?](\s|$)", text)),
                 ("; " + " | ".join(why)) if why else "", text[:200]), flush=True)
    except Exception:  # noqa: BLE001  the line itself is never held up by its note
        pass
    _floor(float(sh.get("dur") or 0.0) + 3.0, line=True)   # her line plays on the phone next: the desk ear must not take it
    return {"ok": True, "id": lid, "text": text, "audio": "/api/line/%s.wav" % lid, "sheet": sh,
            "ms": int((time.time() - t0) * 1000)}


def read_wav(path: Path) -> tuple[list, int]:
    """A 16-bit PCM WAV as mono floats in -1..1 and its rate, with the standard library alone (round 28: the kit's
    own python has no soundfile, so every phone line failed on a fresh install)."""
    import array
    import wave
    with wave.open(str(path), "rb") as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        import soundfile as sf                   # not 16-bit: the reader that knows it
        a, sr = sf.read(str(path), dtype="float32")
        return (a.mean(1) if getattr(a, "ndim", 1) > 1 else a).tolist(), int(sr)
    a = array.array("h")
    a.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    if ch > 1:
        return [sum(a[i:i + ch]) / (ch * 32768.0) for i in range(0, len(a) - ch + 1, ch)], sr
    return [x / 32768.0 for x in a], sr


NOT_CONNECTED = "Chat needs your agent. On your computer, open Crissa's Wall and use Connect; Say works without it."


def agent() -> tuple[str, str, str]:
    """(url, key, model): --agent/--model, else the install's Connect file (<state home>/agent.json)."""
    if CFG.get("agent"):
        return CFG["agent"], os.environ.get("CRISSA_AGENT_KEY", ""), CFG.get("model") or ""
    try:                                 # the install's own reader: env overrides and a key kept by reference
        import crissa_agent
        d = crissa_agent.load(Path(CFG["agent_file"]).expanduser().parent)
    except Exception:  # noqa: BLE001  no Crissa lib here: read the file as it is
        try:
            d = json.loads(Path(CFG["agent_file"]).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", "", ""
    return str(d.get("url") or d.get("base_url") or ""), str(d.get("key") or d.get("api_key") or ""), str(d.get("model") or "")


def _wall_url() -> str:
    """Crissa's player on this computer (the Wall): --wall, else the port table's `player`."""
    if CFG.get("wall"):
        return str(CFG["wall"]).rstrip("/")
    try:
        import crissa_ports
        return "http://127.0.0.1:%d" % int(crissa_ports.port("player"))
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:8100"


def _ask_wall(text: str) -> dict | None:
    """ONE CONVERSATION (the User 2026-10-18, the iPhone app's M5): the phone's question asked through the Wall's own
    Conversation (POST /v1/ask, speak false: its history and session id, nothing said on the computer), so the phone
    and the Wall are one conversation. {"ok", "reply"} / {"ok": False, "error"}; None = no Wall to ask (no player, or
    one without Connect): the caller asks the agent itself, as before."""
    req = urllib.request.Request(_wall_url() + "/v1/ask", method="POST",
                                 data=json.dumps({"text": str(text)[:MAX_TEXT], "speak": False, "by": "phone"}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 409:                          # the Wall has no agent connected
            return {"ok": False, "error": NOT_CONNECTED}
        if e.code == 502:
            try:
                return {"ok": False, "error": str(json.load(e).get("error") or "the agent did not answer")}
            except ValueError:
                return {"ok": False, "error": "the agent did not answer"}
        return None                                # 404 and the rest: a player without /v1/ask
    except (OSError, ValueError):
        return None
    reply = str(d.get("reply") or "").strip()
    if not reply:
        return {"ok": False, "error": str(d.get("error") or "no answer")}
    out = {"ok": True, "reply": reply}
    try:                                           # the tools the agent ran for this reply, when the Wall says (show `tool`)
        if int(d.get("tools") or 0) > 0:
            out["tools"] = int(d["tools"])
    except (TypeError, ValueError):
        pass
    return out


def _secure() -> bool:
    """THE PHONE KNOWS SECURE (the User 2026-10-29: "Be sure conductor has those simple rules with Secure flag status (but
    no relevant Mobile loops) for future use"): the room's lamp from presence's /v1/visit_lamp, sage = Secure, read on
    each beat of the page; the app plays a show's casual clips only while it is true (Crissa Mobile has none yet).
    Anything unreadable is not Secure. Kill CRISSA_PHONE_SECURE=0."""
    if os.environ.get("CRISSA_PHONE_SECURE", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    try:
        import crissa_ports
        with urllib.request.urlopen(crissa_ports.url("presence") + "/v1/visit_lamp", timeout=0.5) as r:
            d = json.load(r)
        return str(d.get("visit_lamp") or d.get("color") or "").strip().lower() == "sage"
    except Exception:  # noqa: BLE001  no presence, no port table: not Secure
        return False


def _ask_agent(msgs: list) -> dict | None:
    """THE PHONE ASKS THROUGH THE KIT'S OWN AGENT CLIENT (the User 2026-10-29, the first-time walk: "getting http errors
    on TTS mobile connection ... when i press send" - every Send was HTTP 500, Claude's 400 to this file's own OpenAI
    call: it sent a temperature and vLLM's chat_template_kwargs, which Claude's endpoint refuses). lib/crissa_agent
    knows each provider (no temperature on Anthropic, one retry without what a server names) and the key by reference,
    as the desk does. The phone's persona and its 160-token cap stay. None = not used here (an --agent given, no kit
    lib): the old call runs. {"ok", "reply"|"error"} otherwise. Kill CRISSA_PHONE_CHAT_VIA_AGENT=0."""
    if CFG.get("agent") or os.environ.get("CRISSA_PHONE_CHAT_VIA_AGENT", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    try:
        import crissa_agent
        cfg = crissa_agent.load(Path(CFG["agent_file"]).expanduser().parent)
    except Exception:  # noqa: BLE001  the app's own repo has no kit lib
        return None
    if not crissa_agent.configured(cfg):
        return {"ok": False, "error": NOT_CONNECTED}
    cfg = dict(cfg, system_prompt=msgs[0]["content"], no_prompt=False, prompt_from="")
    try:
        reply = crissa_agent.ask(msgs[-1]["content"], cfg, history=msgs[1:-1], max_tokens=160, timeout=60.0)
    except Exception as e:  # noqa: BLE001  AgentError carries a plain reason, never the key
        return {"ok": False, "error": str(e) or type(e).__name__}
    reply = re.sub(r"<think>.*?</think>", "", str(reply or ""), flags=re.S).strip()
    return {"ok": True, "reply": reply} if reply else {"ok": False, "error": "the agent did not answer"}


def _chat(text: str, history: list, say: bool = True) -> dict:
    _floor()
    if CFG.get("one_conversation"):
        got = _ask_wall(text)
        if got is not None:
            if not got["ok"] or not say:
                return got
            out = _say(got["reply"])
            out["reply"] = got["reply"]
            return out
    msgs = [{"role": "system", "content": PERSONA}]
    for h in (history or [])[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:MAX_TEXT]})
    msgs.append({"role": "user", "content": str(text)[:MAX_TEXT]})
    got = _ask_agent(msgs)
    if got is not None:
        if not got["ok"] or not say:
            return got
        out = _say(got["reply"])
        out["reply"] = got["reply"]
        return out
    body = {"model": CFG.get("model") or "default", "messages": msgs, "max_tokens": 160, "temperature": 0.7,
            "chat_template_kwargs": {"enable_thinking": False}}
    url, key, model = agent()
    if not url:
        return {"ok": False, "error": NOT_CONNECTED}
    body["model"] = model or body["model"]
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.load(r)["choices"][0]["message"]["content"]
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()
    if not say:                                   # the app shows the words at once, then asks /api/say
        return {"ok": True, "reply": reply}
    out = _say(reply)
    out["reply"] = reply
    return out


HEAR_MAX_S = 30
HEAR_MAX_BYTES = HEAR_MAX_S * 48000 * 2
MIC_MAX_BYTES = 2 * 48000 * 2                 # one /api/mic chunk: 2 s at most (the ear refuses longer)
# THE JSON BODY'S SIZE (round 46): the biggest JSON this server reads is /api/chat's `history`, the page's own turns of
# at most MAX_TEXT characters each; 2 MB holds thousands of them and never a film. A dial, not a law:
# CRISSA_PHONE_JSON_MAX (bytes).
JSON_MAX_BYTES = int(os.environ.get("CRISSA_PHONE_JSON_MAX") or 2_000_000)


def mic_on() -> bool:
    """THE PHONE AS THE SECOND MIC (the User 2026-10-19, build 1): /api/mic relays the phone's open mic to the ear,
    which scores it against the desk mic (modules/ear/crissa_ear/phone_mic.py). Off unless CRISSA_PHONE_MIC=1."""
    return os.environ.get("CRISSA_PHONE_MIC", "0").strip().lower() in ("1", "true", "yes", "on")


def _ear_mic_url() -> str:
    """The ear's /v1/phone-mic on this computer (the port table's `stt`, the ear's turns server): --ear, else that."""
    if CFG.get("ear"):
        return str(CFG["ear"])
    try:
        import crissa_ports
        return "http://127.0.0.1:%d/v1/phone-mic" % int(crissa_ports.port("stt"))
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:8190/v1/phone-mic"


def _mic(pcm: bytes, rate: int) -> tuple[int, dict]:
    """One chunk of the phone's open mic to the ear, as it came (16-bit mono PCM at `rate`)."""
    req = urllib.request.Request(_ear_mic_url(), data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(int(rate))})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"ok": False, "error": "the ear said %d" % e.code}
    except (OSError, ValueError) as e:
        return 502, {"ok": False, "error": "the ear did not answer: %s" % type(e).__name__}


def _asr_url() -> str:
    """The speech-to-text service on this computer (the /stt contract: modules/voice/scripts/crissa-asr.py, or the
    house's own on the same port): --asr, else the port table's `asr`."""
    if CFG.get("asr"):
        return str(CFG["asr"])
    try:
        import crissa_ports
        return "http://127.0.0.1:%d/stt" % int(crissa_ports.port("asr"))
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:8096/stt"


def _hear(pcm: bytes, rate: int, chat: bool = False) -> dict:
    """VOICE IN (the User 2026-10-18, the iPhone app's push to talk / open mic): what the phone heard, as 16-bit mono
    PCM, turned into words on this computer; with chat, the words go to Chat and her answer comes back too.
    {"ok", "text", "decode_ms"} (+ the chat reply's keys)."""
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if not 8000 <= int(rate) <= 48000:
        return {"ok": False, "error": "sample rate must be 8000-48000"}
    _floor()
    req = urllib.request.Request(_asr_url(), data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(int(rate))})
    with urllib.request.urlopen(req, timeout=60) as r:
        got = json.load(r)
    text = str(got.get("text") or "").strip()
    out = {"ok": True, "text": text, "decode_ms": got.get("decode_ms")}
    if chat and text:
        res = _chat(text, [], say=True)
        out.update({k: v for k, v in res.items() if k != "ok"})
        out["chat_ok"] = bool(res.get("ok"))       # heard you, and did Chat answer: two different failures
    return out


# THIS COMPUTER IS ONLY ITS OWN PAGES (round 46, the local review of kit crissa-063a2813, 2026-10-23): a request from
# 127.0.0.1 skipped pairing on its address alone, so any web page open in a browser on this computer (a fetch, a form
# post, an <img>) or a page that rebinds its own name to 127.0.0.1 was "the computer itself": it could chat on his
# agent, make her speak, mint a QR hand-off and list or unpair the phones. The loopback skip now also needs the one
# loopback rule (lib/crissa_guard: a loopback Host, no foreign Origin, no cross-site mark without a loopback
# Referer); a request that fails it is another device and needs its pairing like any phone. And a request whose
# Origin is neither this machine nor this server's own Host (a page on another site, from the computer or a phone's
# browser) is refused 403 before any route reads it. The phone over the LAN is unchanged: its page is same-origin
# (Origin = its Host, the LAN address) and the iPhone app sends no Origin; both still pair as before. On in a pack;
# the house keeps today's server until it is flipped there (CRISSA_LOOPBACK_GUARD=1); =0 is the kill.
try:
    import crissa_guard as _guard
except ImportError:                                    # the app's own repo carries no lib: the rule, inline
    _guard = None


def _guard_default() -> str:
    try:
        import crissa_home
        return "0" if crissa_home.is_house() else "1"
    except Exception:  # noqa: BLE001  a copy with no install around it is a pack
        return "1"


GUARD_DEFAULT = _guard_default()


def guard_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_LOOPBACK_GUARD", GUARD_DEFAULT) or GUARD_DEFAULT).strip().lower() not in (
        "0", "false", "no", "off")


def _local_name(hostport: str) -> bool:
    if _guard is not None:
        return _guard.loopback_hostport(hostport)
    h = str(hostport or "").strip().lower()
    h = h[1:].split("]", 1)[0] if h.startswith("[") else (h.rsplit(":", 1)[0] if h.count(":") == 1 else h)
    return h in ("127.0.0.1", "localhost", "::1")


def foreign(headers) -> str:
    """Why a request is not this machine's own (the loopback rule); '' = it is."""
    if _guard is not None:
        return _guard.foreign_request(headers)
    host = str(headers.get("Host") or "").strip()
    if host and not _local_name(host):
        return "host"
    origin = str(headers.get("Origin") or "").strip()
    if origin:
        return "" if origin != "null" and _local_name(origin.split("://", 1)[-1].split("/", 1)[0]) else "origin"
    if str(headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
        ref = str(headers.get("Referer") or "").strip()
        return "" if ref and _local_name(ref.split("://", 1)[-1].split("/", 1)[0]) else "cross-site"
    return ""


def cross_site(headers) -> bool:
    """A present Origin that is neither this machine nor this server's own Host (same-origin): another site's page."""
    origin = str(headers.get("Origin") or "").strip()
    if not origin:
        return False
    if origin == "null":
        return True
    o_host = origin.split("://", 1)[-1].split("/", 1)[0].strip().lower()
    return not (_local_name(o_host) or o_host == str(headers.get("Host") or "").strip().lower())


def body_length(headers, cap: int) -> int | None:
    """The Content-Length to read, or None (not a number, negative - rfile.read(-1) reads until the peer closes - or
    over `cap`)."""
    if _guard is not None:
        return _guard.body_length(headers, cap)
    raw = str(headers.get("Content-Length") or "").strip()
    try:
        n = int(raw) if raw else 0
    except ValueError:
        return None
    return None if n < 0 or n > cap else n


class H(BaseHTTPRequestHandler):
    server_version = "phone-app/1"

    def log_message(self, fmt, *args):
        if "/api/mb/" not in (self.path or ""):
            sys.stderr.write("[phone-app] %s %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, d: dict, headers: dict | None = None) -> None:
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _file(self, p: Path, cache: str = "no-cache") -> None:
        if not p.is_file():
            return self._json(404, {"ok": False, "error": "not found"})
        size = p.stat().st_size
        ctype = TYPES.get(p.suffix.lower(), "application/octet-stream")
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command == "HEAD":
            return
        with p.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_HEAD(self):
        self.do_GET()

    # PAIRING (the User 2026-10-18, the iPhone app's M1; lib/phone_pairing). On in a pack since round 38
    # (pairing_default; --no-pairing or CRISSA_PHONE_PAIRING=0 turn it off): another device reaches /api/* and /pack/* only with its token (Authorization: Bearer, or
    # X-Crissa-Token); the computer itself never needs one, and only it can ask for a code or the list of phones.
    def _loopback(self) -> bool:
        if str(self.client_address[0]) not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        return not guard_on() or not foreign(self.headers)     # round 46: and the loopback rule (above)

    def _cross_site(self) -> bool:
        """Round 46: answered 403 when another site's page is asking (guard_on); False = go on."""
        if not guard_on() or not cross_site(self.headers):
            return False
        self._json(403, {"ok": False, "error": "this server's own pages only"})
        return True

    def _token(self) -> str:
        a = str(self.headers.get("Authorization") or "")
        if a.lower().startswith("bearer "):
            return a[7:].strip()
        return str(self.headers.get("X-Crissa-Token") or "").strip() or self._cookie_token()

    def _cookie_token(self) -> str:
        """The page's pairing, from the QR claim's cookie: a browser sends it with every fetch and every video and pack
        request, which cannot carry a header (the User 2026-10-20: the QR is the whole story for a phone)."""
        for part in str(self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == PAIR_COOKIE:
                return v.strip()
        return ""

    def _refused(self, path: str) -> bool:
        """True when this request was answered 401 (a guarded path, pairing on, another device, no valid token)."""
        if not CFG.get("pairing") or self._loopback() or path in ("/api/pair", "/api/connection", "/api/handoff/leave"):
            return False
        if not (path.startswith("/api/") or path.startswith("/pack/")):
            return False                              # the app's own files: a phone may load the page to pair
        import phone_pairing
        if phone_pairing.check(Path(CFG["pair_file"]), self._token()):
            return False
        self._json(401, {"ok": False, "error": ("This phone is not connected to your Crissa: scan the QR code on your computer, "
                                          "or pair the Crissa app with its code."), "pair": True})
        return True

    def _pairing_get(self, path: str) -> bool:
        if path not in ("/api/pair/code", "/api/pair/devices"):
            return False
        if not self._loopback():
            self._json(403, {"ok": False, "error": "only the computer itself can show a pairing code"})
            return True
        import phone_pairing
        if path == "/api/pair/code":
            self._json(200, dict(phone_pairing.new_code(Path(CFG["pair_file"])), ok=True, pairing=bool(CFG.get("pairing"))))
        else:
            self._json(200, {"ok": True, "pairing": bool(CFG.get("pairing")), "devices": phone_pairing.devices(Path(CFG["pair_file"]))})
        return True

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if self._cross_site() or self._pairing_get(path) or self._refused(path):
            return None
        if path in ("/", "/index.html"):
            return self._file(APP_DIR / "index.html")
        if path.startswith("/app/icon-"):         # her face comes with her pack (the films' rights), else the app's own
            own = _inside(Path(CFG["packs"]) / CFG["pack"], path[5:])
            return self._file(own if own.is_file() else _inside(APP_DIR, path[5:]))
        if path.startswith("/app/"):
            return self._file(_inside(APP_DIR, path[5:]))
        if path.startswith("/pack/"):
            return self._file(_inside(Path(CFG["packs"]), path[6:]), cache="public, max-age=600")
        if path.startswith("/api/line/"):
            return self._file(_inside(Path(CFG["lines"]), path[10:]))
        if path == "/api/agent":                  # is an agent connected (the User 2026-10-18: the phone is Crissa's second screen;
            url, key, model = agent()             # it says "connect on your computer" rather than offer a Chat that cannot answer).
            host = re.sub(r"^[a-z]+://", "", url).split("/")[0] if url else ""        # never the key, never the path
            return self._json(200, {"ok": True, "connected": bool(url), "host": host, "model": model})
        if path == "/api/show":                   # the clips she walks (phone-show/1) and each one's pack
            root = Path(CFG["packs"])
            try:
                sh = json.loads((root / "show.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sh = {"schema": "phone-show/1", "talk": CFG["pack"], "talk_hub": "up",
                      "clips": {CFG["pack"]: {"role": "loop", "start": "up", "end": "up", "pack": CFG["pack"]}}}
            packs = {}
            for k, c in (sh.get("clips") or {}).items():
                m = pack_manifest(c.get("pack") or k)
                if m:
                    packs[k] = m
            sh["clips"] = {k: c for k, c in (sh.get("clips") or {}).items() if k in packs}
            return self._json(200, {"ok": True, "show": sh, "packs": packs, "xfade_ms": _xfade_ms()})
        if path == "/api/packs":
            out = []
            for d in sorted(Path(CFG["packs"]).iterdir()):
                if (d / "manifest.json").is_file():
                    out.append(d.name)
            return self._json(200, {"ok": True, "packs": out, "default": CFG["pack"]})
        if path.startswith("/api/mb/"):
            return self._json(200, {"ok": True, "frames": []})
        if path == "/api/connection":
            import phone_handoff
            paired = None                             # pairing off: nothing to be
            if CFG.get("pairing"):                    # the computer itself is always let in
                import phone_pairing
                paired = self._loopback() or phone_pairing.check(Path(CFG["pair_file"]), self._token())
            return self._json(200, {"ok": True, "handoff": phone_handoff.enabled(), "house": _house(),
                                    "pairing": bool(CFG.get("pairing")), "paired": paired})
        if path == "/api/session":
            import phone_session
            return self._json(200, dict(phone_session.state(_session_file()), ok=True))
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if self._cross_site():
            return None
        # round 46: a negative or unreadable Content-Length was rfile.read(-1), which reads until the peer closes;
        # it is refused here, and every body is read only after the pairing check (below)
        n = body_length(self.headers, max(JSON_MAX_BYTES, HEAR_MAX_BYTES, MIC_MAX_BYTES))
        if n is None and os.environ.get("CRISSA_BODY_LENGTH", "1").strip().lower() in ("0", "off", "no", "false"):
            n = int(self.headers.get("Content-Length") or 0)                            # the kill: the old read
        if n is None:
            big = str(self.headers.get("Content-Length") or "").strip().isdigit()      # a number, only too big
            return self._json(413 if big else 400, {"ok": False, "error": "too big" if big else "bad Content-Length"})
        if path == "/api/hear":                       # audio, not JSON: read under its own cap, after the gate
            if self._refused(path):
                return None
            if n <= 0 or n > HEAR_MAX_BYTES:
                return self._json(413 if n > 0 else 400, {"ok": False, "error": "send 1 to %d s of 16-bit mono audio" % HEAR_MAX_S})
            q = dict(urllib.parse.parse_qsl(self.path.split("?", 1)[1])) if "?" in self.path else {}
            try:
                return self._json(200, _hear(self.rfile.read(n), int(self.headers.get("X-Sample-Rate") or 16000),
                                             chat=q.get("chat") == "1"))
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        if path == "/api/mic":                        # the open mic's chunks, relayed to the ear (mic_on)
            if self._refused(path):
                return None
            if not mic_on():
                return self._json(404, {"ok": False, "off": True, "error": "the phone mic is off (CRISSA_PHONE_MIC=1)"})
            if n <= 0 or n > MIC_MAX_BYTES:
                return self._json(413 if n > 0 else 400, {"ok": False, "error": "send up to 2 s of 16-bit mono audio"})
            try:
                rate = int(self.headers.get("X-Sample-Rate") or 16000)
            except ValueError:
                return self._json(400, {"ok": False, "error": "X-Sample-Rate is a number"})
            code, res = _mic(self.rfile.read(n), rate)
            return self._json(code, res)
        if n > JSON_MAX_BYTES:
            return self._json(413, {"ok": False, "error": "too big"})
        # round 46: nothing is read before the pairing check. The routes a phone reaches BEFORE it is paired (pairing
        # itself, the QR claim, Disconnect) and the computer-only ones (each answers 403 to another device) go on; any
        # other route is answered 401 here, its body unread.
        if path in POST_COMPUTER_ONLY and not self._loopback():
            return self._json(403, {"ok": False, "error": "only the computer itself may do that"})
        if path.startswith("/api/mb/") and not self._loopback():
            return self._json(403, {"ok": False, "error": "only her voice on this computer posts a sheet"})
        if path not in POST_BEFORE_PAIRING and path not in POST_COMPUTER_ONLY and self._refused(path):
            return None
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"ok": False, "error": "not json"})
        if path == "/api/pair":                       # the phone's half: {"code", "name"} -> {"token"}
            import phone_pairing
            res = phone_pairing.pair(Path(CFG["pair_file"]), str(body.get("code") or ""), str(body.get("name") or ""))
            return self._json(200 if res.get("ok") else (429 if res.get("locked") else 403), res)
        if path == "/api/handoff":                    # the Wall's QR card asks for a token: this computer only
            if not self._loopback():
                return self._json(403, {"ok": False, "error": "only the computer itself hands its session to a phone"})
            import phone_handoff
            if not phone_handoff.enabled():
                return self._json(404, {"ok": False, "off": True, "error": "the hand-off is off (CRISSA_PHONE_HANDOFF=0)"})
            t = phone_handoff.mint()
            lan, port = phone_handoff.lan_ip(), int(CFG.get("port") or _phone_port())
            url = ("http://%s:%d/?handoff=%s" % (lan, port, t["token"])) if lan else ""
            return self._json(200, dict(t, ok=True, url=url, lan=lan, port=port))
        if path == "/api/handoff/claim":              # the phone's half, before any pairing: the token is the proof
            import phone_handoff
            why = phone_handoff.claim_why(str(body.get("token") or ""))
            if why != "ok":
                return self._json(403, {"ok": False, "why": why, "error": phone_handoff.WHY[why]})
            print("[phone-app] handoff: a phone took the house's session from the QR", flush=True)
            res, hdr = {"ok": True, "house": _house(), "paired": False}, {}
            if CFG.get("pairing"):                    # the claim is the pairing: the page gets a cookie, the app a token
                import phone_pairing
                name = str(body.get("name") or "").strip() or "phone (QR)"
                tok = phone_pairing.grant(Path(CFG["pair_file"]), name, via="qr")
                hdr["Set-Cookie"] = "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict" % (PAIR_COOKIE, tok, PAIR_COOKIE_S)
                res["paired"] = True
                if body.get("want_token"):
                    res["token"] = tok
            return self._json(200, res, hdr)
        if path == "/api/handoff/leave":              # Disconnect: this phone gives its own pairing back
            forgot = False
            if CFG.get("pairing"):
                import phone_pairing
                forgot = phone_pairing.forget(Path(CFG["pair_file"]), self._token())
            return self._json(200, {"ok": True, "forgot": forgot},
                              {"Set-Cookie": "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" % PAIR_COOKIE})
        if path == "/api/pair/unpair":
            if not self._loopback():
                return self._json(403, {"ok": False, "error": "only the computer itself can unpair a phone"})
            import phone_pairing
            return self._json(200, {"ok": True, "unpaired": phone_pairing.unpair(Path(CFG["pair_file"]), str(body.get("id") or ""))})
        if path.startswith("/api/mb/"):
            lid = path[8:].split("/")[0]
            if lid in CAUGHT and isinstance(body, dict):
                body.pop("pcm16", None)
                CAUGHT[lid].append(body)
            return self._json(200, {"ok": True})
        try:
            if path == "/api/say":
                return self._json(200, _say(body.get("text", "")))
            if path == "/api/session":                # the page's pagehide / hidden beacon: {"end": true}; its beat: {"here": true}
                return self._json(200, {"ok": True, "session": _session(end=True) if body.get("end")
                                         else _session("here" if body.get("here") else "page"), "secure": _secure()})
            if path == "/api/floor":
                return self._json(200, {"ok": True, "floor": _floor(on=body.get("on", True) is not False)})
            if path == "/api/chat":
                return self._json(200, _chat(body.get("text", ""), body.get("history") or [], say=body.get("say", True) is not False))
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        return self._json(404, {"ok": False, "error": "not found"})


PAIR_COOKIE = "crissa_phone"
POST_BEFORE_PAIRING = ("/api/pair", "/api/handoff/claim", "/api/handoff/leave")   # a phone's own way in (and out)
POST_COMPUTER_ONLY = ("/api/handoff", "/api/pair/unpair")                           # each refuses another device
PAIR_COOKIE_S = 365 * 86400       # as long as a paired token lives (until Disconnect or an unpair on the computer)


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    return p if root.resolve() in p.parents else root / "__nope__"


def _house() -> str:
    """This computer as the phone sees it, <lan ip>:<port> ('' off any network)."""
    import phone_handoff
    lan = phone_handoff.lan_ip()
    return ("%s:%d" % (lan, int(CFG.get("port") or _phone_port()))) if lan else ""


def _phone_port() -> int:
    """The port table's `phone` (lib/crissa_ports: CRISSA_PORT_PHONE, else 8226 + CRISSA_PORT_OFFSET); 8226 without it."""
    try:
        import crissa_ports
        return int(crissa_ports.port("phone"))
    except Exception:  # noqa: BLE001  the app's own repo has no port table
        return 8226


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=_phone_port())
    ap.add_argument("--packs", default=str(STATE / "packs"))
    ap.add_argument("--pack", default="crissa-phone-listen")
    ap.add_argument("--lines", default=str(STATE / "lines"))
    ap.add_argument("--bench", default="", help="a conductor for anything else house-speak asks (default none)")
    ap.add_argument("--speak", default=str(CRISSA / "scripts" / "house-speak.py"),
                    help="the house-speak to run (one with HOUSE_SPEAK_RENDER_ONLY renders at machine speed)")
    ap.add_argument("--voice", default="", help="the voice name (default: the install's own)")
    ap.add_argument("--sink", default="", help="only for a house-speak without render-only: a silent sink to play into")
    ap.add_argument("--agent", default="", help="an OpenAI-compatible base URL (default: the install's Connect file)")
    ap.add_argument("--model", default="")
    ap.add_argument("--agent-file", default=str(_state_home() / "agent.json"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--pairing", action=argparse.BooleanOptionalAction, default=pairing_default(),
                    help="another device needs a paired token, from the QR (lib/phone_pairing); on in a pack, "
                         "CRISSA_PHONE_PAIRING=0 or --no-pairing turns it off")
    ap.add_argument("--pair-file", default=str(STATE / "pairing.json"))
    ap.add_argument("--asr", default="", help="a speech-to-text /stt URL for voice in (default: the port table's asr)")
    ap.add_argument("--ear", default="", help="the ear's /v1/phone-mic URL for the open mic (default: the port table's stt)")
    ap.add_argument("--one-conversation", action="store_true",
                    default=os.environ.get("CRISSA_PHONE_ONE_CONVERSATION", "0").strip().lower() in ("1", "true", "yes", "on"),
                    help="Chat goes through the Wall's own conversation (POST <wall>/v1/ask, speak false); CRISSA_PHONE_ONE_CONVERSATION=1")
    ap.add_argument("--floor-file", default=str(_state_home() / "phone-floor.json"),
                    help="the phone's floor, read by the desk ear (lib/phone_floor; CRISSA_PHONE_FLOOR=0 writes none)")
    ap.add_argument("--session-file", default="",
                    help="is she on the phone, read by the desk Wall (lib/phone_session; default <state home>/phone-session.json, "
                         "CRISSA_PHONE_SESSION; CRISSA_PHONE_SESSION=0 writes none)")
    ap.add_argument("--wall", default="", help="the Wall's player URL (default: the port table's player)")
    a = ap.parse_args(argv)
    CFG.update(vars(a))
    srv = ThreadingHTTPServer((a.host, a.port), H)
    if a.pairing:                                  # a phone app finds a paired-mode server by itself (lib/phone_bonjour)
        import phone_bonjour
        phone_bonjour.start(a.port)
    print("[phone-app] http://%s:%d/  packs %s  pairing %s" % (a.host, a.port, a.packs, "ON" if a.pairing else "off"), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
