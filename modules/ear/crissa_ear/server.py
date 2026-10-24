"""Minimal loopback HTTP API — stdlib only besides the pipeline."""

from __future__ import annotations

from . import settings as _S  # this install's addresses (the shared port table, 2026-10-10)

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

# SO_REUSEADDR: prevent 'Address already in use' on restart (TIME_WAIT socket)
ThreadingHTTPServer.allow_reuse_address = True

import numpy as np

from . import audio as audio_mod
from .config import Config
from .dashboard import DASHBOARD_HTML
from .ear_loop import get_ear_loop
from .home import EAR, HOME
from .house_id import index_ids
from .pipeline import Transcript, Transcriber
from .presence import (
    fetch_presence,
    post_sound_heard,
    post_user_voice,
    request_f2f,
    request_surprise,
)
from .stamp import stamp_turn
from .turn_log import get_turn_log

_transcriber: Optional[Transcriber] = None

# parec capture is one-at-a-time; concurrent /v1/listen gets 409.
_listen_lock = threading.Lock()

# Only voice_emb-family stamps carry a sim worth asserting to presence;
# sole_present sims are floor noise and must never refresh user_auth.
_VOICE_STAMP_SOURCES = ("voice_emb", "voice_emb_vs_sole")


def _maybe_post_sound(st: dict) -> None:
    """Utterance-close DOA → presence.sound. Dark while Crissa talks.

    Identity must still land when the stamp has no beam_angle — the 1 Hz
    DOA loop already has the bearing, and last-heard on the glass needs
    the name. Fall back to the loop's last az; presence will keep the
    previous bearing if that is also missing.
    """
    az = st.get("beam_angle")
    if az is None:
        try:
            from .doa_loop import get_doa_loop

            az = get_doa_loop().last_az
        except Exception:
            az = None
    if az is None and not st.get("person_id"):
        return
    src = str(st.get("source") or "")
    if src in ("tts_echo_veto", "media_gate", "media_hold_sole") or src.startswith(
        "media"
    ):
        st["beam_suppressed"] = src or "media"
        return
    try:
        from .tts_gate import mouth_open

        if mouth_open(grace_s=0.3):
            st["beam_suppressed"] = "mouth_open"
            return
    except Exception:
        pass
    try:
        threading.Thread(
            target=post_sound_heard,
            kwargs={
                "az_deg": az,
                "conf": st.get("beam_conf"),
                "person_id": st.get("person_id"),
                "speech": st.get("beam_speech"),
                "source": "xvf3800",
                "id_source": st.get("source"),
                "certain": st.get("certain"),
                "voice_emb_sim": st.get("voice_emb_sim"),
            },
            daemon=True,
        ).start()
    except Exception:
        pass


def _maybe_post_voice_stamp(d: dict) -> None:
    """Per-utterance voice stamp → presence user_auth, fire-and-forget.

    Daemon thread: the ear loop is single-threaded, so a slow presence POST
    on this path would stall mic capture."""
    st = d.get("stamp") or {}
    pid = st.get("person_id")
    sim = st.get("voice_emb_sim")
    if not pid or sim is None or st.get("source") not in _VOICE_STAMP_SOURCES:
        return
    try:
        threading.Thread(
            target=post_user_voice, args=(str(pid), float(sim)), daemon=True
        ).start()
    except Exception:
        pass


# A second voice scorer beside the ear, log only ("" = none). The house ran one; a pack ships none.
_WS_SHADOW_URL = os.environ.get("RAIL_WS_SHADOW_URL", "")


def _shadow_voice(pcm: bytes, sr: int, st: dict, ts: float) -> None:
    """Relay the utterance to a shadow voice scorer (RAIL_WS_SHADOW_URL), fire-and-forget.

    the User 2026-08-04: shadow A/B on LIVE audio — the sidecar logs its verdict
    next to the live stamp (join on ts). Fail-open; never touches auth.
    Since the 08-04 encoder flip the LIVE encoder is WeSpeaker(ORT) and the
    shadow runs ECAPA (the revert path); keys are encoder-neutral."""
    if not _WS_SHADOW_URL:
        return

    def _post() -> None:
        try:
            import base64
            import urllib.request

            meta = {
                "ts": ts,
                "live_encoder": os.environ.get("HOUSE_VOICE_ENCODER")
                or os.environ.get("RAIL_VOICE_ENCODER")
                or "wespeaker",
                "live_pid": st.get("person_id"),
                "live_sim": st.get("voice_emb_sim"),
                "source": st.get("source"),
            }
            body = json.dumps({
                "pcm16_b64": base64.b64encode(pcm).decode(),
                "sr": sr,
                "meta": meta,
            }).encode()
            req = urllib.request.Request(
                _WS_SHADOW_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=8.0).read()
        except Exception:
            pass

    try:
        threading.Thread(target=_post, daemon=True).start()
    except Exception:
        pass


def attach_stamp(
    result: Transcript,
    pcm: bytes | None = None,
    sr: int = 16000,
    *,
    voice_emb=None,
    embed: bool = True,
) -> Transcript:
    """Presence + voice emb + vocative / sole-body stamp. Fail-open if presence down.
    ``embed`` False (round 17, Security ID-only / Off: id_trail.py): never embed here, the turn does not wait for it.

    Inverse of ghost timer: occupancy 0 + any in-room voice, not media →
    nudge presence to scan-to-add (HTTP only; queued, no cooldown). Voice id
    is not the trigger. ``voice_emb`` may be precomputed in parallel with ASR.
    """
    try:
        presence = fetch_presence()
        if voice_emb is None and embed and pcm and len(pcm) > 16000:  # >0.5s int16 @16k
            try:
                voice_emb = _try_ecapa(pcm, sr)
            except Exception:
                voice_emb = None
        # Duration from capture when known (P0 min-duration gate)
        dur_s = None
        if pcm and sr:
            try:
                dur_s = len(pcm) / (2.0 * float(sr))  # int16 mono
            except Exception:
                dur_s = None
        if dur_s is None and getattr(result, "duration_s", None):
            try:
                dur_s = float(result.duration_s)
            except Exception:
                dur_s = None
        beam_angle = None
        beam_conf = None
        beam_meta = None
        beam_speech = False
        try:
            from .doa import read_doa, stamp_beam

            beam_meta = read_doa()
            beam_angle, beam_conf, beam_speech = stamp_beam(beam_meta)
            # Silero already opened this turn. Puck VAD often misses desk /
            # Shure talk (2026-08-29: 8/9 turns beam_speech False, az None).
            # Keep the live HID azimuth; mark speech true because we have text.
            if beam_angle is None and isinstance(beam_meta, dict):
                raw_az = beam_meta.get("az_deg")
                if raw_az is not None:
                    beam_angle = float(raw_az) % 360.0
                    try:
                        beam_conf = float(beam_meta.get("conf") or 0.25)
                    except (TypeError, ValueError):
                        beam_conf = 0.25
                    beam_speech = True
        except Exception:
            beam_meta = None
            beam_angle, beam_conf, beam_speech = None, None, False
        st = stamp_turn(
            result.text or "",
            presence,
            extra_names=index_ids(),
            voice_emb=voice_emb,
            speech_duration_s=dur_s,
            beam_angle=beam_angle,
            beam_conf=beam_conf,
        )
        st["beam_speech"] = bool(beam_speech)
        if isinstance(beam_meta, dict):
            if beam_meta.get("ok") is False and beam_meta.get("error"):
                st["beam_error"] = beam_meta.get("error")
        result.stamp = st
        result.person_id = st.get("person_id")
        if pcm and len(pcm) > 16000:
            import time as _time

            _shadow_voice(pcm, sr, st, _time.time())
        try:
            if getattr(result, "rms_db", None) is not None:
                st["rms_db"] = float(result.rms_db)
        except Exception:
            pass

        # Always feed media-vs-person gate on every ear turn
        try:
            from .media_gate import observe_turn

            _pres = st.get("presence") if isinstance(st.get("presence"), dict) else {}
            try:
                _hc = int((_pres or {}).get("headcount") or 0)
            except (TypeError, ValueError):
                _hc = 0
            # Named people only for media bar bias (U1/U2 ghosts must not raise it)
            _people = (_pres or {}).get("people") or []
            _named = 0
            for _p in _people:
                if not isinstance(_p, dict):
                    continue
                _n = str(_p.get("name") or "")
                if _n and not _n.upper().startswith("U") and _n.lower() not in (
                    "unknown",
                    "unnamed",
                ):
                    _named += 1
            _emb_sim = st.get("voice_emb_sim")
            try:
                _emb_sim_f = float(_emb_sim) if _emb_sim is not None else None
            except (TypeError, ValueError):
                _emb_sim_f = None
            st["media_gate"] = observe_turn(
                duration_s=dur_s,
                rms_db=st.get("rms_db"),
                text=result.text or "",
                person_id=st.get("person_id"),
                room_hc=_hc,
                named_hc=_named,
                emb_top_sim=_emb_sim_f,
            )
            # Media hold: do not paint household id on TV/podcast ASR
            mg = st.get("media_gate") or {}
            if mg.get("media_on") or mg.get("verdict") == "media":
                if st.get("person_id"):
                    st["media_suppressed_id"] = st.get("person_id")
                    st["media_suppressed_source"] = st.get("source")
                st["person_id"] = None
                st["source"] = "media_gate"
                st["certain"] = False
                st["id_status"] = None
                result.person_id = None
            elif st.get("source") == "sole_present" and mg.get("phase") != "clear_human":
                # the User 2026-08-04 (Shotwell earnings-call leak): the gate flickers
                # per chunk, and missed chunks fell through to "only person in the
                # room must be speaking" at sims as low as 0.23. Until the session
                # is proven human, sole-presence needs the VOICE to corroborate at
                # the match threshold; a real spoken turn (the user 0.6+) keeps its
                # stamp even mid-TV.
                from .voice_bank import VOICE_MATCH_SIM

                _sim = st.get("voice_emb_sim")
                try:
                    _sim_f = float(_sim) if _sim is not None else None
                except (TypeError, ValueError):
                    _sim_f = None
                if _sim_f is None or _sim_f < VOICE_MATCH_SIM:
                    st["media_suppressed_id"] = st.get("person_id")
                    st["media_suppressed_source"] = "sole_present"
                    st["person_id"] = None
                    st["source"] = "media_hold_sole"
                    st["certain"] = False
                    st["id_status"] = None
                    result.person_id = None
            result.stamp = st
        except Exception as e:
            st["media_gate"] = {"error": str(e)[:100]}
            result.stamp = st

        # Unidentified voice → S1/S2 scratch folders (not inventory).
        if voice_emb is not None and isinstance(st, dict):
            try:
                from .scratch_speakers import assign_scratch

                scr = assign_scratch(
                    voice_emb, stamp=st, text=result.text or "", duration_s=dur_s
                )
                if scr.get("ok"):
                    st["scratch"] = scr
                    result.person_id = st.get("person_id")
                    result.stamp = st
            except Exception as e:
                st["scratch"] = {"ok": False, "error": str(e)[:120]}
                result.stamp = st

        # Wake shadow jsonl stays. Do not hang it on the turn unless asked.
        if pcm:
            try:
                from .wake_shadow import score_pcm

                ws = score_pcm(pcm, int(sr or 16000))
                if ws is not None and os.environ.get(
                    "RAIL_WAKE_SHADOW_PUBLISH", "0"
                ).strip().lower() in ("1", "true", "yes"):
                    st["wake_shadow"] = ws
                    result.stamp = st
            except Exception:
                pass

        # Live promote: high-conf emb stamps → house-id voice bank (gated)
        if voice_emb is not None and st.get("person_id") and st.get("source"):
            try:
                from .voice_bank import maybe_promote_live_print

                prom = maybe_promote_live_print(
                    person_id=str(st.get("person_id")),
                    emb=voice_emb,
                    stamp=st,
                    text=result.text or "",
                    speech_duration_s=dur_s,
                    pcm=pcm,
                    sr=sr,
                )
                if prom is not None:
                    st["voice_promote"] = prom
                    result.stamp = st
            except Exception as e:
                st["voice_promote"] = {"ok": False, "error": str(e)[:120]}
                result.stamp = st

        # the User 2026-08-26 — occupancy 0 + any in-room voice, not media →
        # scan to add named seat faces. Voice id is not the trigger: wrong
        # voice / misID while occupied does not scan. User voice while empty
        # also hunts AUTH (mode both). Kill-switch: RAIL_VOICE_AUTHORITY=0.
        hc = presence.get("headcount")
        try:
            hc_n = int(hc) if hc is not None else 0
        except (TypeError, ValueError):
            hc_n = 0
        text = (result.text or "").strip()
        speechy = bool(text) or voice_emb is not None
        pid_now = (st.get("person_id") or "").strip().lower() or None
        is_user = st.get("is_user") is True
        user_id = (
            str(os.environ.get("HOUSE_USER_ID") or "user").strip().lower()
        )
        user_voice = bool(is_user or pid_now == user_id)
        authority = os.environ.get("RAIL_VOICE_AUTHORITY", "1") not in (
            "0",
            "false",
            "no",
            "off",
        )
        empty_room_voice = bool(speechy and hc_n == 0)
        mg = st.get("media_gate") or {}
        media = bool(mg.get("media_on") or mg.get("verdict") == "media")
        src = str(st.get("source") or "")
        if media and empty_room_voice:
            st["surprise"] = {
                "requested": False,
                "skipped": "media",
                "mode": None,
                "ok": True,
                "error": None,
                "after_headcount": hc_n,
                "user_voice": bool(user_voice),
            }
        elif authority and empty_room_voice and False:
            src = "stt_voice_while_empty"
            mode = "both" if user_voice else "body_count"
            nudge = request_surprise(
                person_id=user_id if user_voice else st.get("person_id"),
                source=src,
                mode=mode,
                timeout=1.5,
                media=False,
            )
            st["surprise"] = {
                "requested": True,
                "mode": mode,
                "ok": bool(
                    nudge.get("ok")
                    or nudge.get("accepted")
                    or nudge.get("headcount") is not None
                ),
                "error": nudge.get("error"),
                "skipped": nudge.get("skipped"),
                "after_headcount": nudge.get("headcount"),
                "user_voice": user_voice,
            }
            result.stamp = st
        _maybe_post_sound(st)
        result.stamp = st
    except Exception as e:
        result.stamp = {"error": str(e)[:200], "person_id": None, "source": None}
        result.person_id = None
    return result


def _try_ecapa(pcm: bytes, sr: int):
    """Embed via the module's own voice encoder (crissa_ear.voice_embed: WeSpeaker ONNX, else ECAPA when
    speechbrain is installed). The house read a router from its identity tools; the pack carries the same
    code beside this file."""
    try:
        from . import voice_embed as mod
    except Exception:
        return None
    try:
        if mod.available():
            return mod.embed_pcm16(pcm, sr=sr)
    except Exception:
        pass
    return None


def _her_prints() -> dict:
    """Prints of her voice for the veto (round 16 (e), her_prints.py): how many, in use, stale, and `ask`."""
    try:
        from . import her_prints

        st = her_prints.status()
        return {k: st.get(k) for k in ("n", "used", "stale", "ask", "kinds_in_use", "at")} if st else {"n": None}
    except Exception:  # noqa: BLE001
        return {"n": None}


def _id_trail_stats() -> dict:
    try:
        from . import id_trail

        return id_trail.stats()
    except Exception:  # noqa: BLE001
        return {"mode": "unknown"}


def _voice_warm() -> dict:
    """The voice print's warm-up as /health shows it: warm | warming | none (no encoder) | error | off | cold."""
    try:
        from . import voice_embed

        return voice_embed.warm_status()
    except Exception:  # noqa: BLE001
        return {"state": "unknown"}


def get_transcriber(cfg: Optional[Config] = None) -> Transcriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = Transcriber(cfg or Config())
    return _transcriber


def _voice_encoder_ok() -> bool:
    """Is a voice encoder loadable (the WeSpeaker ONNX file is present, or ECAPA is installed)? Without one the
    ear stamps by presence alone, and /health says so (2026-10-09)."""
    try:
        from . import voice_embed

        return bool(voice_embed.available())
    except Exception:  # noqa: BLE001
        return False


def _glass_url() -> str:
    """Where the old HTML surfaces send a browser: the player's Wall page (the shared port table)."""
    from .settings import url as _url

    return os.environ.get("RAIL_STT_GLASS_URL") or (_url("player") + "/wall")


# THIS MACHINE ONLY (round 46, the local review of kit crissa-063a2813, 2026-10-23): every route answered any web page
# open in the buyer's browser - `Access-Control-Allow-Origin: *` let a page read /v1/turns (what was said in the room),
# clear them with DELETE, post omni notes and cut a voice clip to a path of its choosing. Every handler now runs the
# shared loopback guard first (guard.py, a byte-equal copy of lib/crissa_guard.py): a foreign Host, a non-loopback
# Origin, or a cross-site no-cors request is refused 403 before any route reads it, and a response names only a
# loopback Origin (this machine's own pages on another port still read across ports). The ear ships only in the kit,
# so the guard is on by default here. Kill: CRISSA_LOOPBACK_GUARD=0 (no guard, and the old `*` back).
from . import guard as _guard

# The biggest body any route takes is /stt's raw PCM16: one utterance, at most BODY_MAX_S seconds (120 = the ear's own
# longest take, audio.record_until_enter's max_seconds) at the highest rate a route accepts (96 kHz, the /v1/mic and
# /v1/phone-mic check). A Content-Length over that, negative (rfile.read(-1) waits for the peer to close) or not a
# number is refused 400 before a byte is read. Dial: CRISSA_EAR_BODY_MAX_S (0 = no size cap; a negative or non-numeric
# length is still refused - there is no reading of it that is not a hang).
BODY_MAX_RATE = 96000
try:
    BODY_MAX_S = float(os.environ.get("CRISSA_EAR_BODY_MAX_S", "120") or 120)
except ValueError:
    BODY_MAX_S = 120.0


def body_cap() -> int | None:
    return int(BODY_MAX_S * BODY_MAX_RATE * 2) if BODY_MAX_S > 0 else None


# A VOICE CLIP IS WRITTEN ONLY UNDER THE EAR'S OWN HOME (round 46): /v1/security/voice_clip took `dest` as is and
# voice_cache.write_clip made every parent directory, so any caller could write a wav anywhere this user can (over a
# config file, into an autostart folder). The one legitimate caller is the bridge's intrusion module, which files under
# <home>/ear/incidents/<id>/voice.wav; a dest is taken only when it resolves (symlinks followed) inside the ear's home
# (home.EAR, or RAIL_SECURITY_INCIDENTS when this ear names another incidents root) and ends in .wav. Kill:
# CRISSA_EAR_CLIP_CONFINE=0.
def clip_roots() -> list[Path]:
    roots = [EAR]
    extra = (os.environ.get("RAIL_SECURITY_INCIDENTS") or "").strip()
    if extra:
        roots.append(Path(extra).expanduser())
    return roots


def clip_dest_ok(dest: str) -> str:
    """'' when the clip may be written to `dest`; else why not."""
    if os.environ.get("CRISSA_EAR_CLIP_CONFINE", "1").strip().lower() in ("0", "false", "no", "off"):
        return ""
    try:
        p = Path(dest).expanduser()
        if not p.is_absolute():
            return "dest must be an absolute path"
        if p.suffix.lower() != ".wav":
            return "dest must be a .wav"
        rp = p.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return "dest unreadable"
    for root in clip_roots():
        try:
            rr = root.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if rp != rr and rp.is_relative_to(rr):
            return ""
    return "dest outside the ear's home"


def make_handler(cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _cors(self):
            # the Wall page (another loopback port) polls these rails: its own Origin is echoed, never `*` (round 46)
            origin = _guard.allow_origin(self.headers) if _guard.guard_on() else "*"
            if not origin:
                return
            self.send_header("Access-Control-Allow-Origin", origin)
            if origin != "*":
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _ours(self) -> bool:
            """The loopback guard, first in every do_* (round 46): False = refused 403 already."""
            return _guard.check(self)

        def _json(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _html(self, code: int, html: str):
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _gone(self, location: str):
            """Old HTML surfaces — send Crissa (and bookmarks) to VoiceOnly."""
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()

        def do_OPTIONS(self):
            if not self._ours():
                return
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            if not self._ours():
                return
            path = urlparse(self.path).path
            if path in ("/", "/dashboard"):
                return self._gone(_glass_url())
            if path in ("/omni", "/v1/omni/ui"):
                return self._gone(_glass_url())
            if path in ("/shell", "/crissa-shell", "/v1/shell"):
                return self._gone(_glass_url())
            if path in ("/omni/float.js", "/v1/omni/float.js"):
                js_path = Path(__file__).with_name("omni_float.js")
                try:
                    body = js_path.read_bytes()
                except OSError as e:
                    return self._json(500, {"error": str(e)})
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/v1/omni":
                from .omni import build_omni

                q = parse_qs(urlparse(self.path).query)
                try:
                    n = int(q.get("n", ["30"])[0])
                except ValueError:
                    n = 30
                return self._json(200, build_omni(n_turns=n))
            if path == "/v1/turns":
                q = parse_qs(urlparse(self.path).query)
                try:
                    n = int(q.get("n", ["50"])[0])
                except ValueError:
                    n = 50
                via = (q.get("via") or [None])[0]
                turns = get_turn_log().list(n=n, via=via)
                return self._json(200, {"turns": turns, "count": len(turns)})
            if path == "/v1/presence_proxy":
                # Same-origin relay for the page (:8191 sets no CORS). Fail-open dict.
                return self._json(200, fetch_presence())
            if path in ("/panel", "/v1/panel", "/control"):
                return self._gone(_S.url("feed", path="/panel"))
            if path in ("/panel/house-ui.css", "/static/house-ui.css", "/v1/panel/house-ui.css"):
                css_path = Path(__file__).with_name("static") / "house-ui.css"
                try:
                    body = css_path.read_bytes()
                except OSError as e:
                    return self._json(500, {"error": str(e)})
                self.send_response(200)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/panel/state", "/v1/panel/state"):
                from .panel_state import build_panel_state

                return self._json(200, build_panel_state())
            if path in ("/v1/media_locus", "/media_locus", "/v1/media_gate"):
                from .media_gate import status as media_status
                from .media_locus import get_media_locus

                if path.endswith("media_gate"):
                    return self._json(200, {"ok": True, **media_status()})
                return self._json(200, get_media_locus())
            if path == "/v1/house/voice":
                from .house_voice import read_state

                return self._json(200, read_state())
            if path in ("/v1/security/voice_cache", "/security/voice_cache"):
                from .voice_cache import status as _vc_status

                return self._json(200, _vc_status())
            if path == "/health":
                tr = get_transcriber(cfg)
                pk = tr.live_ear.health()
                pres = fetch_presence()
                from .house_voice import is_voice_on
                from .voice_bank import bank_health
                from .config import live_ear_url as _live_ear_url

                bh = bank_health()
                return self._json(
                    200,
                    {
                        "ok": True,
                        "service": "crissa-ear",
                        # cfg.engine is the CLIENT SLOT ("live-ear" = LiveEarEngine,
                        # a thin HTTP client), not the model. The worker on the
                        # other end is Audio8 ark-asr :8096 today. `engine` on
                        # this payload is the published worker name (audio8-ark).
                        "engine": os.environ.get("RAIL_STT_ASR_ENGINE") or cfg.engine,
                        "asr_engine": os.environ.get("RAIL_STT_ASR_ENGINE") or cfg.engine,
                        "asr_url": _live_ear_url(),
                        "live_ear": pk,
                        "parakeet": pk,  # alias — do not read this in new code
                        "whisper": cfg.whisper_model if cfg.engine in ("whisper", "dual") else None,
                        "presence_headcount": pres.get("headcount"),
                        "presence_ok": "error" not in pres or pres.get("headcount") is not None,
                        "house_ids": index_ids(),
                        "voice_bank": bh.get("counts") or {},
                        "voice_bank_ok": bh.get("ok"),
                        "voice_encoder": bh.get("encoder"),
                        "voice_on": is_voice_on(),
                        "mic": os.environ.get("RAIL_STT_MIC", "auto"),
                        # the source that preference resolves to now: Register records on it and Init names it (the
                        # User 2026-10-10: one microphone everywhere)
                        "mic_source": _mic_source(),
                        "aec": _aec_view(),
                        "voice_encoder_ok": _voice_encoder_ok(),
                        "voice_warm": _voice_warm(),
                        "asr_warm": getattr(tr, "asr_warm", None),   # round 33: warming | warm | cold | None
                        "her_prints": _her_prints(),
                        # round 19: what the trailing voice recognition came to (ID-only), and the prints it added
                        "id_trail": _id_trail_stats(),
                        "ear": get_ear_loop().status(),
                        "doa_loop": __import__(
                            "crissa_ear.doa_loop", fromlist=["get_doa_loop"]
                        ).get_doa_loop().status(),
                    },
                )
            if path == "/v1/ear/status":
                _es = get_ear_loop().status()
                try:
                    from . import phone_mic

                    if phone_mic.on():
                        _es = dict(_es, phone_mic=phone_mic.get(int(cfg.sample_rate)).status())
                except Exception:  # noqa: BLE001 - the ear's status never fails on the phone's
                    pass
                return self._json(200, _es)
            return self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if not self._ours():
                return
            path = urlparse(self.path).path
            n = _guard.body_length(self.headers, body_cap())
            if n is None:                     # round 46: -1 read until the peer closed; a huge one filled memory
                self.close_connection = True
                return self._json(400, {"ok": False, "error": "bad Content-Length (0..%s bytes)" % body_cap()})
            raw = self.rfile.read(n) if n else b""
            if path in ("/stt", "/transcribe"):
                return self._post_stt(raw)
            # The Wall page's microphone (2026-10-09): the player forwards the page's PCM here when the ear
            # listens through the page (RAIL_STT_MIC=page); the ear loop reads these frames in place of parec.
            if path == "/v1/mic":
                from .capture import PAGE, pushed

                if os.environ.get("RAIL_STT_MIC", "auto") != PAGE:
                    return self._json(404, {"ok": False, "error": "this service listens on the host (RAIL_STT_MIC is a device)"})
                try:
                    sr = int(self.headers.get("X-Sample-Rate") or 16000)
                except ValueError:
                    return self._json(400, {"ok": False, "error": "X-Sample-Rate is a number"})
                if not 8000 <= sr <= 96000:
                    return self._json(400, {"ok": False, "error": "X-Sample-Rate 8000-96000"})
                return self._json(200, {"ok": True, "frames": pushed().push(raw, sr)})
            # The phone as the second mic (2026-10-19, phone_mic.py): the phone server relays the phone's PCM here
            # while its mic is open; the ear loop scores it against the desk mic and the winner's copy is decoded.
            if path == "/v1/phone-mic":
                from . import phone_mic

                if not phone_mic.on():
                    return self._json(404, {"ok": False, "off": True,
                                            "error": "the phone mic is off on this ear (CRISSA_EAR_PHONE_MIC=1)"})
                try:
                    sr = int(self.headers.get("X-Sample-Rate") or 16000)
                except ValueError:
                    return self._json(400, {"ok": False, "error": "X-Sample-Rate is a number"})
                if not 8000 <= sr <= 96000:
                    return self._json(400, {"ok": False, "error": "X-Sample-Rate 8000-96000"})
                res = phone_mic.get(int(cfg.sample_rate)).push(raw, sr)
                return self._json(200 if res.get("ok") else 400, res)
            if path == "/v1/listen":
                return self._post_listen(raw)
            if path == "/v1/ear/open":
                return self._post_ear_open(raw)
            if path == "/v1/ear/close":
                return self._json(200, get_ear_loop().close(reason="manual"))
            # Security: cut a clip out of the rolling audio cache. Called by
            # rail-voice when it files an intrusion attempt -- the words are
            # only recognised as an attempt after decode, so the clip has to be
            # taken retroactively. Writes where the caller asks (Inspector tree)
            # and reports the span actually written, never a promised one.
            if path in (
                "/v1/security/voice_cache/release",
                "/security/voice_cache/release",
            ):
                from .voice_cache import release as _vc_release

                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                if not isinstance(obj, dict):
                    obj = {}
                return self._json(200, _vc_release(obj))
            if path in ("/v1/security/voice_clip", "/security/voice_clip"):
                from .voice_cache import write_clip

                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                if not isinstance(obj, dict):
                    obj = {}
                dest = str(obj.get("dest") or "").strip()
                if not dest:
                    return self._json(400, {"ok": False, "error": "dest required"})
                why = clip_dest_ok(dest)      # round 46: only under the ear's own home (clip_dest_ok)
                if why:
                    return self._json(403, {"ok": False, "error": why})
                return self._json(
                    200,
                    write_clip(
                        dest,
                        seconds=obj.get("seconds"),
                        t_end=obj.get("t_end"),
                    ),
                )
            # Init: mark media speaker hour on room clock (dial × locus)
            if path in ("/v1/media_locus", "/media_locus"):
                from .media_locus import parse_init_utterance, set_media_locus

                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                mode = obj.get("mode")
                station = obj.get("station") or obj.get("hour") or obj.get("media_station")
                if obj.get("text") and (mode is None or station is None):
                    parsed = parse_init_utterance(str(obj.get("text")))
                    if parsed.get("ok"):
                        mode = mode or parsed.get("mode")
                        if station is None:
                            station = parsed.get("station")
                    elif station is None and mode is None:
                        return self._json(400, parsed)
                if mode is None and station is not None:
                    mode = "external"
                if mode is None:
                    return self._json(
                        400,
                        {
                            "ok": False,
                            "error": "need mode=internal|external, or text "
                            "(e.g. 'same computer' / 'nine' / 'desk')",
                        },
                    )
                out = set_media_locus(
                    int(station) if station is not None else None,
                    mode=str(mode),
                    label=obj.get("label"),
                    source=str(obj.get("source") or "init_interview"),
                    user_seat=obj.get("user_seat"),
                    contiguous_with_user=obj.get("contiguous_with_user"),
                )
                return self._json(200 if out.get("ok") else 400, out)
            # Media gate: reset / hot-reload scoring module (no full STT restart)
            if path in ("/v1/media_gate", "/media_gate"):
                import importlib
                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                action = str(obj.get("action") or "status").lower()
                import crissa_ear.media_gate as mg

                if action in ("reload", "hot_reload"):
                    importlib.reload(mg)
                    mg.reset()
                    return self._json(
                        200,
                        {"ok": True, "action": "reload", **mg.status()},
                    )
                if action in ("reset", "clear", "off"):
                    mg.reset()
                    return self._json(
                        200,
                        {"ok": True, "action": "reset", **mg.status()},
                    )
                return self._json(200, {"ok": True, **mg.status()})
            if path in ("/panel/voice", "/v1/panel/voice", "/v1/house/voice"):
                from .house_voice import write_state

                # Local User only — agents/tools on other hosts get 403
                peer = (self.client_address or ("",))[0]
                if peer not in ("127.0.0.1", "::1", "localhost"):
                    return self._json(
                        403,
                        {
                            "ok": False,
                            "error": "voice_lock_local_only",
                            "peer": peer,
                            "note": "Flip Voice from the desk console only",
                        },
                    )
                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                if "on" in obj and obj.get("voice") is None and obj.get("state") is None:
                    voice = "on" if obj.get("on") else "off"
                else:
                    voice = obj.get("voice") or obj.get("state") or "off"
                on_by = str(obj.get("on_by") or "user")
                st = write_state(str(voice), on_by=on_by)
                st["ok"] = True
                st["voice_on"] = st.get("voice") == "on"
                return self._json(200, st)
            if path in ("/panel/camera-home", "/v1/panel/camera-home"):
                # Best-effort aim door rest via presence camera HTTP.
                # Pose from room_geometry heading (init); fallback 450000.
                import urllib.request

                try:
                    pan, tilt = 450000, 18000
                    try:
                        # NO nested `from pathlib import Path` here. Python binds
                        # a name assigned anywhere in a function as local for the
                        # WHOLE function, so that import made module-level Path
                        # (server.py:9) unreachable everywhere else in do_POST --
                        # and /panel/surface-mode below died with UnboundLocalError,
                        # which is what broke Voice-Only mode (2026-08-15).
                        geom = Path(os.environ.get("RAIL_ROOM_GEOMETRY") or (EAR / "room_geometry.json"))
                        h = (json.loads(geom.read_text()).get("heading") or {})
                        if h.get("door_home_pan") is not None:
                            pan = int(h["door_home_pan"])
                        if h.get("door_home_tilt") is not None:
                            tilt = int(h["door_home_tilt"])
                    except Exception:
                        pass
                    if _S.camera_off():           # no camera: the aim is a no-op that says so (2026-10-10)
                        return self._json(200, {"ok": False, "camera": "off"})
                    cam = _S.camera()
                    body = json.dumps({"pan": pan, "tilt": tilt}).encode()
                    req = urllib.request.Request(
                        cam.rstrip("/") + "/aim",
                        data=body,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=3.0) as r:
                        r.read()
                    return self._json(200, {"ok": True, "aimed": "door", "pan": pan, "tilt": tilt})
                except Exception as e:
                    return self._json(200, {"ok": False, "error": str(e)[:160]})
            if path in ("/panel/display", "/v1/panel/display"):
                from .display_surface import set_on, status as display_status

                peer = (self.client_address or ("",))[0]
                if peer not in ("127.0.0.1", "::1", "localhost"):
                    return self._json(
                        403,
                        {"ok": False, "error": "display_local_only", "peer": peer},
                    )
                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                if "on" not in obj:
                    return self._json(200, {"ok": True, **display_status()})
                return self._json(200, set_on(bool(obj.get("on"))))
            # Surface mode (hybrid vs voice-only). NEVER toggles house Voice master.
            # Display mode ≠ mouth lock. Voice-OFF used to force hybrid and
            # wipe the voice-only flag (the wall dropped to 1280×800, 2026-08-18).
            if path in ("/panel/surface-mode", "/v1/panel/surface-mode"):
                peer = (self.client_address or ("",))[0]
                if peer not in ("127.0.0.1", "::1", "localhost"):
                    return self._json(403, {"ok": False, "error": "local_only"})
                try:
                    obj = json.loads(raw.decode() or "{}") if raw else {}
                except Exception:
                    obj = {}
                mode = str(obj.get("mode") or "hybrid").strip().lower()
                flag = HOME / "voice-only"
                try:
                    from .house_voice import is_voice_on

                    voice_on = is_voice_on()
                except Exception:
                    voice_on = True
                try:
                    if mode in ("voice-only", "voice"):
                        flag.parent.mkdir(parents=True, exist_ok=True)
                        flag.write_text("1\n")
                        mode = "voice-only"
                    else:
                        if flag.is_file():
                            flag.unlink()
                        mode = "hybrid"
                except OSError as e:
                    return self._json(500, {"ok": False, "error": str(e)[:160]})
                return self._json(
                    200,
                    {
                        "ok": True,
                        "mode": mode,
                        "voice_on": voice_on,
                        "voice_only_flag": flag.is_file(),
                        "note": "surface mode only — does not change Voice master",
                    },
                )
            if path in ("/v1/omni/note", "/v1/omni/event"):
                return self._post_omni_note(raw)
            if path == "/v1/file":
                return self._post_file(raw)
            return self._json(404, {"ok": False, "error": "not found"})

        def do_DELETE(self):
            if not self._ours():
                return
            path = urlparse(self.path).path
            if path == "/v1/turns":
                return self._json(200, {"ok": True, "cleared": get_turn_log().clear()})
            return self._json(404, {"ok": False, "error": "not found"})

        def _post_stt(self, raw: bytes):
            sr = int(self.headers.get("X-Sample-Rate", "16000"))
            ctype = (self.headers.get("Content-Type") or "").lower()
            tr = get_transcriber(cfg)
            try:
                if "json" in ctype:
                    obj = json.loads(raw.decode() or "{}")
                    # base64 wav path optional later; for now require path
                    if "path" not in obj:
                        return self._json(400, {"error": "json body needs path"})
                    result = attach_stamp(tr.transcribe_file(obj["path"]))
                else:
                    pcm = np.frombuffer(raw, dtype=np.int16)
                    result = attach_stamp(
                        tr.transcribe_pcm16(pcm, sr=sr), pcm=raw, sr=sr
                    )
                return self._json(200, get_turn_log().append(result.to_dict(), via="stt"))
            except Exception as e:
                return self._json(500, {"error": str(e)[:300]})

        def _process_listen_array(
            self, wav, sr: int, via: str = "listen", *, log_silence: bool = True,
            voice_fut=None,
        ) -> dict:
            tr = get_transcriber(cfg)
            pcm16 = audio_mod.float_to_pcm16(wav)
            pcm = pcm16.tobytes()
            # round 17 (Security ID-only / Off, id_trail.py): the conversation's chunk is sent with no voice print in
            # its way - none beside the decode, no wait after it - and named after it is on the ring
            from . import id_trail
            _sec = id_trail.mode() if via == "ear" else "on"
            trail = not id_trail.parallel_ok(_sec)

            def _pub(row: dict) -> dict:
                entry = get_turn_log().append(row, via=via)
                if trail:
                    id_trail.after_publish(entry, pcm, int(sr), _sec)
                return entry
            if trail:
                voice_fut = None                    # a print kicked before the mode changed is not waited for
            if voice_fut is None and not trail:
                try:
                    from .voice_live import kick

                    voice_fut = kick(pcm, int(sr))
                except Exception:
                    voice_fut = None
            # The phone has the floor (2026-10-18, veto_first.phone_floor_held): what the room heard while he talked to
            # her on the phone, or while her answer played there, is the phone's conversation, not a turn here.
            if via == "ear":
                try:
                    from . import veto_first

                    _fd = len(pcm16) / float(max(int(sr), 1))
                    # the phone as the second mic (phone_mic.py): while its mic is open the compete replaces the
                    # button's hold; the phone's own copy is always its own turn, and a desk copy is dropped only
                    # while her line played on the phone
                    from .ear_loop import decode_meta as _dm

                    _pmeta = _dm()
                    if _pmeta.get("mic") == "phone":
                        _held = False
                    elif _pmeta.get("phone_open"):
                        _held = veto_first.phone_floor_held(_fd, line=True)
                    else:
                        _held = veto_first.phone_floor_held(_fd)
                    if _held:
                        veto_first.record({"skip": True, "why": "phone_floor", "dur": round(_fd, 2)})
                        return {"text": "", "reason": "phone_floor", "duration_s": round(_fd, 2)}
                except Exception:
                    pass                          # fail open: the desk hears as before
            # Veto before decode (2026-09-19, bench only): the house's own
            # line heard back through the raw mic is not sent to the ASR.
            if via == "ear" and voice_fut is not None:
                try:
                    import time as _vt

                    from . import veto_first
                    from .tts_gate import read_gate

                    dur = len(pcm16) / float(max(int(sr), 1))
                    gate = read_gate()
                    if veto_first.enabled() and veto_first.in_scope(gate, dur, _vt.time()):
                        info = voice_fut.result(timeout=veto_first.EMBED_WAIT_S)
                        emb = info.get("emb") if isinstance(info, dict) else None
                        vd = veto_first.decide(gate=gate, dur=dur, emb=emb)
                        veto_first.record(vd)
                        if vd.get("skip"):
                            return {"text": "", "reason": "tts_echo_veto_predecode",
                                    "duration_s": round(dur, 2), "veto_first": vd}
                except Exception as e:  # fail open: decode as before, but say so
                    try:
                        from . import veto_first

                        veto_first.record({"skip": False, "why": "error",
                                           "error": f"{type(e).__name__}: {e}"[:200]})
                    except Exception:
                        pass
            result = tr.transcribe_array(wav, sr)
            d = result.to_dict()
            text = (d.get("text") or "").strip()
            silent = (not text) or d.get("reason") == "silence_rms"
            # Continuous ear must not flood the ring with empty chunks (omni loses speech history).
            if via == "ear" and silent and not d.get("error") and not log_silence:
                return d
            if via == "ear" and silent and not d.get("error"):
                return d  # never log ear silence
            voice_emb = None
            if voice_fut is not None:
                try:
                    info = voice_fut.result(timeout=4.0)
                    if isinstance(info, dict):
                        voice_emb = info.get("emb")
                except Exception:
                    voice_emb = None
            # Stamp (presence + WeSpeaker + crissa_her_tts veto). Mouth echo
            # is an identity fact, not a mute timer — embed ran ∥ ASR.
            result = attach_stamp(result, pcm=pcm, sr=sr, voice_emb=voice_emb, embed=not trail)
            d = result.to_dict()
            if trail:
                d = id_trail.neutral(d, _sec)
            text = (d.get("text") or "").strip()
            st = d.get("stamp") if isinstance(d.get("stamp"), dict) else {}
            # HER OWN LINE HEARD BACK is no turn (round 13, .101 2026-10-12): self_echo.py.
            if via == "ear":
                try:
                    import time as _st_t

                    from . import self_echo
                    from .ear_loop import decode_meta

                    _m = decode_meta()
                    _dur = len(pcm16) / float(max(int(sr), 1))
                    _end = _m.get("speech_end_ts") or (_st_t.time() - 1.5)
                    _start = _m.get("speech_start_ts") or (_end - _dur)
                    d["speech_start_ts"], d["speech_end_ts"] = _start, _end
                    if _m.get("barge_id"):
                        d["barge_id"] = _m["barge_id"]     # round 17: the utterance that cut her line (barge.py)
                    # round 14: her words are SUBTRACTED, never a whole chunk dropped for containing them; what is
                    # left is the person's turn, and no field of the turn keeps her words (they go to self-echo.jsonl)
                    _se = self_echo.subtract(text, float(_start), float(_end), stamp=st,
                                             barge=bool(_m.get("barge_id")))
                    if _se.get("action") == "drop":
                        _row = {k: v for k, v in _se.items() if k != "action"}
                        self_echo.record({"text": text[:160], "dur": round(_dur, 2), **_row})
                        return {"text": "", "reason": "self_echo", "duration_s": round(_dur, 2),
                                "self_echo": _row}
                    if _se.get("action") == "rewrite":
                        self_echo.record({"text": text[:160], "kept": _se["text"][:160], "dur": round(_dur, 2),
                                          "rule": "her_words_taken_out", "removed": _se.get("removed"),
                                          "match": _se.get("match"), "named": _se.get("named")})
                        text = _se["text"]
                        d["text"] = text
                        d["words"], d["candidates"], d["speech_spans"] = [], {}, []
                        d["self_echo"] = {"kept": True, "removed": _se.get("removed"), "named": _se.get("named")}
                        if isinstance(d.get("stamp"), dict):
                            d["stamp"]["mixed_with_her"] = True    # the voice print heard her too: not his alone
                        _shown_name(d, text)
                        return _pub(d)
                    _shown_name(d, text)
                except Exception as _se_e:  # noqa: BLE001 - the echo check never deafens her, but it says why, once
                    if not getattr(self.__class__, "_self_echo_said", False):
                        self.__class__._self_echo_said = True
                        import sys as _se_sys

                        print("[ear] self-echo check off: %s: %s" % (type(_se_e).__name__, str(_se_e)[:160]),
                              file=_se_sys.stderr, flush=True)
            if st.get("source") == "tts_echo_veto":
                d["reason"] = "tts_echo_veto"
                # Still log — do not post as a human voice stamp
                return _pub(d)
            _maybe_post_voice_stamp(d)
            return _pub(d)

        def _post_listen(self, raw: bytes):
            try:
                obj = json.loads(raw.decode() or "{}") if raw else {}
            except Exception:
                obj = {}
            try:
                seconds = float(obj.get("seconds", 3))
            except (TypeError, ValueError):
                seconds = 3.0
            seconds = max(1.0, min(15.0, seconds))
            if get_ear_loop().status().get("open"):
                return self._json(
                    409, {"error": "ear open — close continuous ear first or use that stream"}
                )
            if not _listen_lock.acquire(blocking=False):
                return self._json(409, {"error": "listen busy"})
            try:
                # end_on_silence (2026-09-12): the window is a cap, the
                # answer returns when the speaker stops + hangover
                if bool(obj.get("end_on_silence")):
                    try:
                        hang = float(obj.get("hangover_sec", 0.8))
                    except (TypeError, ValueError):
                        hang = 0.8
                    wav = audio_mod.record_until_silence(
                        seconds, mic=cfg.mic, sample_rate=cfg.sample_rate,
                        hangover_sec=max(0.3, min(3.0, hang)))
                else:
                    wav = audio_mod.record_seconds(
                    seconds, mic=cfg.mic, sample_rate=cfg.sample_rate
                )
            except Exception as e:
                turn = get_turn_log().append(
                    {"text": "", "error": f"mic: {str(e)[:200]}"}, via="listen"
                )
                return self._json(500, turn)
            finally:
                _listen_lock.release()
            try:
                return self._json(200, self._process_listen_array(wav, cfg.sample_rate))
            except Exception as e:
                turn = get_turn_log().append(
                    {"text": "", "error": str(e)[:300]}, via="listen"
                )
                return self._json(500, turn)

        def _post_ear_open(self, raw: bytes):
            try:
                obj = json.loads(raw.decode() or "{}") if raw else {}
            except Exception:
                obj = {}
            try:
                # Legacy name: fixed-window seconds. For vad mode this becomes max_utter floor.
                # Round 26 (.101 10-16: "...can you tell me the " cut at 12 s): an open that names no length takes the
                # ear's own cap (max_utter_sec, RAIL_STT_MAX_UTTER_SEC: 30 in a pack), never a silent 12.
                # Kill: CRISSA_EAR_OPEN_DEFAULT_12=1.
                raw_chunk = obj.get("chunk_sec", obj.get("seconds"))
                if raw_chunk is None and os.environ.get("CRISSA_EAR_OPEN_DEFAULT_12", "0").strip() in ("1", "true", "on"):
                    raw_chunk = 12
                chunk = float(raw_chunk) if raw_chunk is not None else None
            except (TypeError, ValueError):
                chunk = None
            try:
                idle = float(obj.get("idle_timeout_sec", 45))
            except (TypeError, ValueError):
                idle = 45.0
            try:
                max_open = float(obj.get("max_open_sec", 600))
            except (TypeError, ValueError):
                max_open = 600.0
            try:
                frame_sec = float(obj.get("frame_sec", 0.3))
            except (TypeError, ValueError):
                frame_sec = 0.3
            try:
                hangover_sec = float(obj.get("hangover_sec", 1.5))
            except (TypeError, ValueError):
                hangover_sec = 1.5
            try:
                max_utter = obj.get("max_utter_sec")
                max_utter_sec = float(max_utter) if max_utter is not None else None
            except (TypeError, ValueError):
                max_utter_sec = None
            mode = str(obj.get("mode") or "vad").strip().lower()
            if mode not in ("vad", "fixed"):
                mode = "vad"

            def process_chunk(wav, sr: float, voice_fut=None):
                return self._process_listen_array(
                    wav, int(sr), via="ear", voice_fut=voice_fut
                )

            out = get_ear_loop().open(
                cfg=cfg,
                process_chunk=process_chunk,
                listen_lock=_listen_lock,
                chunk_sec=chunk,
                idle_timeout_sec=idle,
                max_open_sec=max_open,
                frame_sec=frame_sec,
                hangover_sec=hangover_sec,
                max_utter_sec=max_utter_sec,
                mode=mode,
            )
            return self._json(200, out)

        def _post_omni_note(self, raw: bytes):
            """House note into the omni ring. Description only — not a headcount."""
            peer = (self.client_address or ("",))[0]
            if peer not in ("127.0.0.1", "::1", "localhost"):
                return self._json(403, {"ok": False, "error": "local_only"})
            try:
                obj = json.loads(raw.decode() or "{}") if raw else {}
            except Exception:
                return self._json(400, {"ok": False, "error": "bad_json"})
            text = str(obj.get("text") or "").strip()
            if not text:
                return self._json(400, {"ok": False, "error": "need text"})
            via = str(obj.get("via") or "argus").strip() or "argus"
            pid = str(obj.get("person_id") or via).strip().lower() or "argus"
            stamp = obj.get("stamp") if isinstance(obj.get("stamp"), dict) else {}
            stamp = {
                "person_id": stamp.get("person_id") or pid,
                "source": stamp.get("source") or "argus_describe",
                "certain": False,
            }
            rec = get_turn_log().append(
                {
                    "text": text[:400],
                    "person_id": pid,
                    "stamp": stamp,
                    "pack_id": obj.get("pack_id"),
                    "engine": "argus",
                },
                via=via,
            )
            return self._json(200, {"ok": True, "turn": rec})

        def _post_file(self, raw: bytes):
            try:
                obj = json.loads(raw.decode() or "{}")
            except Exception:
                return self._json(400, {"error": "json body needs path"})
            fpath = obj.get("path")
            if not fpath:
                return self._json(400, {"error": "json body needs path"})
            tr = get_transcriber(cfg)
            try:
                wav, sr = audio_mod.load_wav_mono16k(fpath)
                result = tr.transcribe_array(wav, sr)
                pcm16 = audio_mod.float_to_pcm16(wav)
                result = attach_stamp(result, pcm=pcm16.tobytes(), sr=int(sr))
                return self._json(
                    200, get_turn_log().append(result.to_dict(), via="file")
                )
            except Exception as e:
                return self._json(500, {"error": str(e)[:300]})

    return Handler



_MIC_SOURCE: dict = {"at": 0.0, "name": ""}


def _mic_source(ttl: float = 5.0) -> str:
    """resolve_mic(RAIL_STT_MIC) - the device the ear captures from - cached a few seconds; '' when none resolves."""
    import time as _t
    if _t.time() - float(_MIC_SOURCE["at"]) < ttl:
        return str(_MIC_SOURCE["name"])
    pref = os.environ.get("RAIL_STT_MIC", "auto")
    try:
        from . import audio as _audio
        name = pref if pref == "page" else _audio.resolve_mic(pref)
    except Exception:  # noqa: BLE001
        name = ""
    _MIC_SOURCE.update(at=_t.time(), name=str(name or ""))
    return str(_MIC_SOURCE["name"])


def _shown_name(d: dict, text: str) -> None:
    """(round 15 (f)) her name where it addresses her, written "Crissa" in the turn; what the ear wrote stays in
    `heard_as`. Over her line (the player's gate) the over-her-line spellings count too."""
    try:
        from . import self_echo
        from .ear_loop import her_air_now

        st = d.get("stamp") if isinstance(d.get("stamp"), dict) else {}
        from .house_id import user_id as _user_id

        user = _user_id()
        pid = str(d.get("person_id") or st.get("person_id") or "").strip().lower()
        # round 16 (d): "Hey" + a near-name from the recognised User is her name
        shown = self_echo.shown_text(text, on_air=her_air_now(), user_voice=bool(pid) and pid == user)
        if shown and shown != text:
            d["heard_as"] = text
            d["text"] = shown
    except Exception:  # noqa: BLE001 - never lose a turn over a spelling
        pass


def _aec_view() -> dict:
    """The echo canceller as the ear runs it (aec.json): on, why, the microphone and speaker it sits between."""
    try:
        from . import aec

        st = aec.read_state()
        return {k: st.get(k) for k in ("on", "why", "mic", "speaker", "source", "sink")} if st else {"on": False}
    except Exception:  # noqa: BLE001
        return {"on": False}


_STOP_T0 = [0.0]


def serve(host: str = "127.0.0.1", port: Optional[int] = None, cfg: Optional[Config] = None) -> None:
    port = _S.port("stt") if port is None else port   # the port table's stt (2026-10-10)
    cfg = cfg or Config()
    tr = get_transcriber(cfg)
    from .config import live_asr_name

    # the voice print is built beside the engines and ready before the ear can be opened (round 15: voice_embed.warm)
    try:
        from . import voice_embed as _ve

        _ve.start_warm()
    except Exception as e:  # noqa: BLE001
        _ve = None
        print(f"[rail-stt] voice print warm-up: {type(e).__name__}: {e}"[:200], flush=True)
    print(f"[rail-stt] warming engines (engine={live_asr_name()})…", flush=True)
    warm = tr.warm()
    print(f"[rail-stt] warm={warm}", flush=True)
    try:
        from .voice_bank import bank_health

        print(f"[rail-stt] voice_bank={bank_health()}", flush=True)
    except Exception as e:
        print(f"[rail-stt] voice_bank health failed: {e}", flush=True)
    if _ve is not None:
        vw = _ve.wait_warm()
        print(f"[rail-stt] voice print {vw.get('state')} in {vw.get('ms')} ms (waited {vw.get('waited_ms')} ms)"
              + (f": {vw.get('error')}" if vw.get("error") else ""), flush=True)
    class _QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            import sys as _sys

            exc = _sys.exception()
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            super().handle_error(request, client_address)

    httpd = _QuietServer((host, port), make_handler(cfg))
    try:
        from .doa_loop import get_doa_loop

        get_doa_loop().start()
        print(f"[rail-stt] doa_loop hz={get_doa_loop().status().get('hz')}", flush=True)
    except Exception as e:
        print(f"[rail-stt] doa_loop start failed: {e}", flush=True)
    try:
        # ECHO CANCELLATION the house's way (round 14, aec.py): between this ear's microphone and her speaker, released
        # when the ear stops.
        import atexit
        import signal

        from . import aec as _aec

        _aec.start_watch(lambda: (cfg.mic if str(cfg.mic) == "page" else audio_mod.resolve_mic(cfg.mic)),
                         lambda m: print(m, flush=True))
        def _stopped():                      # round 23 (.101): a stop is seen in stt.log, with what it took
            if _STOP_T0[0]:
                print("[rail-stt] the ear stopped in %d ms" % int((time.monotonic() - _STOP_T0[0]) * 1000), flush=True)
        atexit.register(_stopped)            # before the release: atexit runs last-in first, so this one prints last
        atexit.register(lambda: _aec.release(why="the ear stopped"))

        def _stop(*_):                       # a unit stop is SIGTERM: leave through atexit so ours is unloaded
            _STOP_T0[0] = time.monotonic()
            raise SystemExit(0)


        signal.signal(signal.SIGTERM, _stop)
    except Exception as e:  # noqa: BLE001
        print(f"[rail-stt] aec: {type(e).__name__}: {e}"[:200], flush=True)
    print(
        f"[rail-stt] http://{host}:{port}  dashboard /  POST /stt  GET /health",
        flush=True,
    )
    httpd.serve_forever()
