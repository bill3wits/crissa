"""Enroll coach display — black screen, line-by-line text synced to voice/beeps.

Lightweight HTTP + SSE on ENROLL_UI_PORT (default 8195).
Open http://127.0.0.1:8195/ on the desk browser.

Brand/marketing polish is Claude's design lane; this is the wire skeleton
with end-in-mind hooks (data-brand, CSS vars, session title).
"""

from __future__ import annotations

from .. import ports as _ports  # this install's addresses (lib/crissa_ports, 2026-10-10)

from .. import paths as _paths
from .. import guard as _guard  # round 46: this machine only (lib/crissa_guard, a byte-equal copy in the module)

import json
import os
import re
import queue
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_PORT = int(os.environ.get("ENROLL_UI_PORT") or _ports.port("enroll"))   # the port table's "enroll"

CAMERA = _ports.camera()


def guard_default() -> str:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): every route of this coach (:8196) was open to any
    web page in the browser - a cross-site POST /start with replace:true wiped a person's bank, /cancel, /prearm and
    /emit drove Setup, GET /events streamed it. Every request now passes lib/crissa_guard first (Host, Origin,
    Sec-Fetch-Site), and a negative or unreadable Content-Length is refused before a read. The house's own coach is
    house-id's tools/house_enroll (enroll-panel.service), never this copy; were the house to run this copy, it keeps
    today's way until flipped there. Kill: CRISSA_LOOPBACK_GUARD=0. Body cap dial: CRISSA_ENROLL_BODY_MAX (unset = none)."""
    try:
        return "0" if _paths.is_house() else "1"
    except Exception:  # noqa: BLE001
        return "1"


def body_cap() -> int | None:
    try:
        n = int(os.environ.get("CRISSA_ENROLL_BODY_MAX", "").strip() or 0)
    except ValueError:
        return None
    return n if n > 0 else None


OLD_CANCEL_WAIT_SEC = 0.7   # the wait before round 46 (kill switch only)


def cancel_grace_sec() -> float:
    """Round 46 (2026-10-23): SIGKILL after 0.7 s beat the routine's own give-back (its finally was still in its first
    call), so the camera token, the hold and the loop stayed taken and an immediate Retry was refused. The wait is now
    voice_register.cancel_release_budget_sec (the give-back chain's own timeouts, summed: the reason is there); a
    healthy routine is gone long before it and the wait ends at its exit. Dial: CRISSA_ENROLL_CANCEL_GRACE_S.
    Kill: CRISSA_CANCEL_WAITS_GIVE_BACK=0 (0.7 s, as before)."""
    raw = os.environ.get("CRISSA_ENROLL_CANCEL_GRACE_S", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    if os.environ.get("CRISSA_CANCEL_WAITS_GIVE_BACK", "1").strip().lower() in ("0", "false", "no", "off"):
        return OLD_CANCEL_WAIT_SEC
    try:
        from . import voice_register as _vr
        return float(_vr.cancel_release_budget_sec())
    except Exception as e:  # noqa: BLE001  the routine's module would not load: the old wait
        print(f"  cancel grace: {type(e).__name__}; {OLD_CANCEL_WAIT_SEC} s", flush=True)
        return OLD_CANCEL_WAIT_SEC


def kill_enroll_tree(pid: int | None, *, wait_sec: float | None = None) -> dict[str, Any]:
    """SIGTERM the enroll session group, then SIGKILL if it is still up after wait_sec (None = cancel_grace_sec()).

    Also stops enroll paplay (beep/announcer). Does not pkill Cosy.
    """
    import signal

    if wait_sec is None:
        wait_sec = cancel_grace_sec() if pid else 0.0

    killed: list[str] = []
    if pid:
        try:
            os.killpg(int(pid), signal.SIGTERM)
            killed.append(f"term_pg:{pid}")
        except ProcessLookupError:
            try:
                os.kill(int(pid), signal.SIGTERM)
                killed.append(f"term:{pid}")
            except ProcessLookupError:
                pass
        except Exception as e:
            killed.append(f"term_err:{type(e).__name__}")
    try:
        from . import cues as _cues

        killed.extend(_cues.request_audio_stop())
    except Exception:
        pass
    dead = True
    if pid:
        deadline = time.time() + max(0.15, float(wait_sec))
        while time.time() < deadline:
            try:
                os.kill(int(pid), 0)
                dead = False
                time.sleep(0.05)
            except ProcessLookupError:
                dead = True
                break
        if not dead:
            try:
                os.killpg(int(pid), signal.SIGKILL)
                killed.append(f"kill_pg:{pid}")
            except Exception:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                    killed.append(f"kill:{pid}")
                except Exception:
                    pass
            try:
                os.kill(int(pid), 0)
                dead = False
            except ProcessLookupError:
                dead = True
    return {"dead": dead, "pid": pid, "killed": killed}


def cancel_gives_back_on() -> bool:
    """Round 46 (2026-10-23): a Cancel gave back FaceFinder, the door, the watchdogs, the lease and the ear, but never
    presence's register token, the tool hold or the listen loop, nor the pre-arm marker (a Retry then adopted a marker
    whose token was gone). After the kill it now gives those back too: the marker by its own record
    (voice_register.prearm_expire), the token (presence ignores a give_back from a non-holder), the hold only when the
    hold is still ours (never the wizard's), and the loop for a Register that cued one. Kill: CRISSA_CANCEL_GIVES_BACK=0."""
    return os.environ.get("CRISSA_CANCEL_GIVES_BACK", "1").strip().lower() not in ("0", "false", "no", "off")


def give_back_after_cancel(mode: str = "") -> dict[str, Any]:
    """What the killed routine's finally would have given back (call only after the routine is dead)."""
    out: dict[str, Any] = {}
    try:
        from . import voice_register as _vr
        from . import voice_session as _vs
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    expired: dict[str, Any] = {}
    try:
        if _vr.PREARM_FILE.exists():
            expired = _vr.prearm_expire(force=True)       # its loop, token / lease, watchdogs, ear and hold
            out["prearm"] = expired
    except Exception as e:  # noqa: BLE001
        out["prearm"] = repr(e)[:120]
    try:
        if _vr.register_camera.on():
            out["token"] = _vr.register_camera.give_back(_vr.PRESENCE, _vr._post)
    except Exception as e:  # noqa: BLE001
        out["token"] = repr(e)[:120]
    try:
        owner = _vr._hold_owner()
        ours = {"voice_register", str((_vs._tool_hold_release.__kwdefaults__ or {}).get("owner") or "")}
        if owner and owner in ours:
            _vs._tool_hold_release(owner=owner)
            out["hold"] = owner
    except Exception as e:  # noqa: BLE001
        out["hold"] = repr(e)[:120]
    try:
        if mode == "voice-register" and not expired.get("expired") and _vr.routine() != "verbal":
            _vr._loop_leave(None)                            # a spoken routine cued no loop (2026-10-12)
            out["loop"] = "left"
    except Exception as e:  # noqa: BLE001
        out["loop"] = repr(e)[:120]
    return out


def abort_register_hw(mode: str = "") -> None:
    """Best-effort: FF off, drop the voice-register lease, ear back (round 46: the token, the hold and the loop too)."""
    import urllib.request

    def _post(url: str, body: dict[str, Any]) -> None:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass

    cam = CAMERA.rstrip("/")
    _post(f"{cam}/v1/facefinder", {"on": False})
    _post(f"{cam}/v1/return_door", {"force": True})
    _post(f"{cam}/v1/resume", {"owner": "voice-register", "force": True})
    _post(f"{cam}/v1/lease/release", {"owner": "voice-register"})
    if cancel_gives_back_on():
        print(f"  cancel gave back {give_back_after_cancel(mode)}", flush=True)
    try:
        from .voice_session import reopen_ear_after_enroll

        reopen_ear_after_enroll()
    except Exception:
        _post(_ports.url("stt", "/v1/ear/open"), {"mode": "vad"})


def expire_unadopted_prearm(publish, before: float) -> dict[str, Any]:
    """Round 46: a Register session has ended (its routine is dead); a pre-arm marker armed before it that it never gave
    back (it died before adopting it, or before its finally) is expired now - the coach's own timer stood aside while
    the session ran. A marker armed after the session started is a new pre-arm and is left alone."""
    try:
        from . import voice_register as _vr
        if not _vr.prearm_adopt_on():
            return {"expired": False, "reason": "off"}
        arm = json.loads(_vr.PREARM_FILE.read_text(encoding="utf-8"))
        if not isinstance(arm, dict) or float(arm.get("ts") or 0) > float(before):
            return {"expired": False, "reason": "newer"}
        r = _vr.prearm_expire()
        if r.get("expired"):
            publish({"type": "line", "text": "Pre-arm given back after the session.", "cls": "muted"})
        return r
    except FileNotFoundError:
        return {"expired": False, "reason": "no marker"}
    except Exception as e:  # noqa: BLE001
        return {"expired": False, "reason": repr(e)[:120]}


def replace_guard_on() -> bool:
    """Round 46 (2026-10-23, the local review of kit crissa-063a2813): start_enroll's replace:true cleared the bank
    BEFORE the session lock's "already running" check and never asked the freeze, so a double Confirm wiped a bank
    under a running session (and a frozen one too). The clear now comes after both checks, under a reservation a
    second Confirm cannot share. Kill: CRISSA_ENROLL_REPLACE_GUARD=0."""
    return os.environ.get("CRISSA_ENROLL_REPLACE_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")


def _camera_pans() -> tuple[int, int]:
    """(desk_pan, door_pan) from camera-stops.json; firmware truth documented
    there; untaught, both are the camera's own forward (pan 0) - no room's numbers (2026-10-25)."""
    desk, door = 0, 0
    try:
        from .site import load_site

        stops_path = (load_site().raw.get("room_refs") or {}).get("stops_json")
        if stops_path:
            stops = json.loads(Path(stops_path).read_text())
            door = int((stops.get("door") or {}).get("pan", door))
            desk = int((stops.get("factory_forward") or {}).get("pan", desk))
    except Exception:
        pass
    return desk, door


def _camera_post(path: str, body: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    import urllib.request

    # AUTH/enroll takes the camera; door watch yields.
    if path.rstrip("/") not in ("/v1/return_door", "/return_door", "/v1/resume", "/resume"):
        try:
            pre = urllib.request.Request(
                CAMERA + "/v1/pause",
                data=json.dumps(
                    {"owner": "enroll", "ttl_sec": 45, "reason": path}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(pre, timeout=3).read()
        except Exception:
            pass
    req = urllib.request.Request(
        CAMERA + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _camera_preposition(publish) -> None:
    """Lease → aim desk → settle → release. Best effort; failures are cues,
    not fatal — the session's own snaps will show an empty view if it failed."""
    desk, _ = _camera_pans()
    try:
        lease = _camera_post("/v1/lease", {"owner": "enroll-panel", "ttl_sec": 45})
        lid = lease.get("lease_id")
        aim = _camera_post("/v1/aim", {"pan": desk, "tilt": 0, "lease_id": lid})
        time.sleep(1.5)
        if lid:
            _camera_post("/v1/lease/release", {"lease_id": lid}, timeout=5)
        publish({"type": "line", "cls": "muted",
                 "text": f"Camera on the desk (pan {aim.get('pan_actual')})."})
    except Exception as e:
        publish({"type": "line", "cls": "err",
                 "text": f"Camera pre-position failed: {str(e)[:80]}"})


def _camera_hand_back(publish) -> None:
    """return_door; if an aim-hold blocks it, wait it out once, then force
    via lease. Never leave the camera on the desk."""
    _, door = _camera_pans()
    try:
        r = _camera_post("/v1/return_door", {})
        if r.get("skipped"):
            wait = min(float(r.get("hold_remaining_sec") or 0) + 2.0, 45.0)
            time.sleep(wait)
            r = _camera_post("/v1/return_door", {})
        if r.get("skipped") or not r.get("at_door", True):
            lease = _camera_post("/v1/lease", {"owner": "enroll-panel", "ttl_sec": 20})
            lid = lease.get("lease_id")
            _camera_post("/v1/aim", {"pan": door, "tilt": 0, "zoom": 150, "lease_id": lid})
            if lid:
                _camera_post("/v1/lease/release", {"lease_id": lid}, timeout=5)
        publish({"type": "line", "cls": "muted", "text": "Camera back on the door."})
    except Exception as e:
        publish({"type": "line", "cls": "err",
                 "text": f"Camera hand-back failed: {str(e)[:80]} — check the door aim"})

# Professional User-init coach UI (SSE protocol unchanged).
# Beep → big GO! flash; paragraph read card; calm desk-monitor density.
COACH_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>House · User identity</title>
<style>
/* Enrollment coach — Claude 2026-08-24.

   The Control Panel is an instrument you scan. This is the opposite: a person
   sits down once, and the screen has exactly one job at a time. So the
   paragraph they must read IS the page, and everything else is small and
   quiet around it.

   Same two faces as the panel so it reads as the same product: Barlow
   Condensed silkscreen for labels, IBM Plex Mono for anything the house
   measured. Loaded from the panel's origin when reachable, with real
   fallbacks — this screen must never wait on a network to be legible.

   Signature is the angle arc: five bins from Grok's measured yaw, filling as
   the sweep take catches them. It makes "turn your head" self-explanatory
   without a sentence, and it is the first time this surface has shown the
   person what it actually got rather than what it asked for. */
:root{
  --void:#0A0C0F;
  --face:#12151B;
  --face-2:#171B23;
  --well:#0B0D11;
  --edge:#1E232C;
  --hi:rgba(255,255,255,.06);
  --ink:#E7EAEF;
  --silk:#98A0AC;
  --silk-2:#6C7480;
  --lamp-g:#63C083;
  --lamp-a:#E9A63F;
  --lamp-r:#DC5A4C;
  --mark-z:#D9A05B;
  --mark-os:#6F9B7E;
  --silkface:"Barlow Condensed","Oswald","Arial Narrow",system-ui,sans-serif;
  --readout:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden}
html{font-size:clamp(14px,calc(11px+.72vh),26px)}
body{
  background:
    radial-gradient(130% 90% at 50% -10%,#161B26 0%,var(--void) 62%);
  color:var(--ink);font-family:var(--readout);font-size:1rem;line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
:focus-visible{outline:2px solid var(--lamp-a);outline-offset:3px}
.wrap{height:100%;display:flex;flex-direction:column;padding:1.1rem 1.8rem 1rem}

/* crown */
.crown{display:flex;align-items:baseline;gap:.8rem;flex:0 0 auto;
  padding-bottom:.6rem;border-bottom:1px solid var(--edge);box-shadow:0 1px 0 var(--hi)}
.mark{font-family:var(--silkface);font-weight:600;font-size:1.2rem;
  letter-spacing:.28em;text-transform:uppercase;color:var(--mark-os)}
.mark b{font-weight:700;font-size:1.2em;letter-spacing:.2em;color:var(--mark-z)}
.crown .what{font-family:var(--silkface);font-weight:500;font-size:.8rem;
  letter-spacing:.26em;text-transform:uppercase;color:var(--silk-2)}
.crown .sp{flex:1}
.who{font-family:var(--silkface);font-weight:600;font-size:.85rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--silk);opacity:0;transition:opacity .3s}
.who.show{opacity:1}
.phase{font-family:var(--readout);font-size:.72rem;color:var(--silk-2);letter-spacing:.04em}
.conn{font-family:var(--silkface);font-size:.7rem;letter-spacing:.18em;text-transform:uppercase;
  color:var(--silk-2)}
.hear{display:inline-flex;align-items:center;gap:.4rem;font-family:var(--silkface);
  font-size:.7rem;letter-spacing:.18em;text-transform:uppercase;color:var(--silk-2)}
.hear i{width:.5rem;height:.5rem;border-radius:50%;background:#2A3038;
  box-shadow:inset 0 1px 1px rgba(0,0,0,.6);transition:background .15s,box-shadow .15s}
.hear i.on{background:var(--lamp-g);box-shadow:0 0 8px rgba(99,192,131,.7)}

/* the stage: one thing at a time, vertically centred */
.stage{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:1.1rem;position:relative}

/* HERO — the paragraph. Sized to be read at arm's length across the desk. */
.read{
  width:min(58rem,92%);background:var(--face);border:1px solid var(--edge);
  border-radius:4px;box-shadow:inset 0 1px 0 var(--hi),0 1.2rem 3rem rgba(0,0,0,.45);
  padding:1.5rem 1.8rem 1.2rem;opacity:0;transform:translateY(.5rem);
  transition:opacity .45s ease,transform .45s ease;
}
.read.show{opacity:1;transform:none}
.read .lab{font-family:var(--silkface);font-weight:600;font-size:.76rem;
  letter-spacing:.24em;text-transform:uppercase;color:var(--lamp-a);
  display:block;margin-bottom:.7rem}
.read .body{font-family:var(--readout);font-size:1.55rem;line-height:1.5;
  color:var(--ink);text-wrap:pretty}
.read.repeat .lab{color:var(--silk)}
.read.repeat .body{font-size:1.9rem}
/* window progress — the take is running and this is how much is left */
.meter{margin-top:1.1rem;height:3px;background:var(--well);border-radius:2px;overflow:hidden;
  box-shadow:inset 0 1px 2px rgba(0,0,0,.6)}
.meter i{display:block;height:100%;width:0;background:var(--lamp-a);
  box-shadow:0 0 10px rgba(233,166,63,.6)}
.meter.run i{transition:width linear}
.left{margin-top:.45rem;font-size:.74rem;color:var(--silk-2);letter-spacing:.04em}

/* ANGLE ARC — the signature. Five measured bins, left profile to right. */
.angles{display:flex;flex-direction:column;align-items:center;gap:.5rem;
  opacity:0;transition:opacity .4s}
.angles.show{opacity:1}
.angles svg{display:block;overflow:visible}
.angles .seg{fill:none;stroke:var(--edge);stroke-width:7;stroke-linecap:round;
  transition:stroke .35s ease}
.angles .seg.got{stroke:var(--lamp-g)}
.angles .head{fill:none;stroke:var(--silk-2);stroke-width:1.4;opacity:.55}
.angles .cap{font-family:var(--silkface);font-weight:600;font-size:.72rem;
  letter-spacing:.22em;text-transform:uppercase;color:var(--silk-2)}
.angles .cap b{color:var(--lamp-g);font-weight:600}

/* viewfinder — face capture state */
.vf{position:relative;width:16rem;height:8rem;opacity:0;transition:opacity .3s}
.vf.show{opacity:1}
.vf i{position:absolute;width:1.5rem;height:1.5rem;border:2px solid var(--lamp-a);opacity:.85}
.vf i:nth-child(1){top:0;left:0;border-right:0;border-bottom:0}
.vf i:nth-child(2){top:0;right:0;border-left:0;border-bottom:0}
.vf i:nth-child(3){bottom:0;left:0;border-right:0;border-top:0}
.vf i:nth-child(4){bottom:0;right:0;border-left:0;border-top:0}
.vf.capturing i{animation:pulse 1.1s ease-in-out infinite}
.vf.ok i{border-color:var(--lamp-g);animation:none}
.vf .t{position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:.3rem;font-size:.8rem;color:var(--silk)}
.vf .t small{font-family:var(--silkface);letter-spacing:.16em;text-transform:uppercase;
  font-size:.66rem;color:var(--silk-2);white-space:nowrap}
@keyframes pulse{50%{opacity:.35}}

/* the spoken coach's own lines, stacked and fading in under the hero */
.stack{width:min(58rem,92%);display:flex;flex-direction:column;gap:.35rem;
  max-height:9rem;overflow:hidden}
.line{opacity:0;transform:translateY(.35rem);transition:opacity .4s,transform .4s;
  font-size:.92rem;color:var(--silk)}
.line.show{opacity:1;transform:none}
.line.muted{color:var(--silk-2);font-size:.84rem}
.line.coach{color:var(--ink)}
.line.ok{color:var(--lamp-g)}
.line.warn{color:var(--lamp-a)}
.line.choice{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.4rem}
.line.choice span{border:1px solid var(--edge);background:var(--face);border-radius:3px;
  padding:.5rem .9rem;font-family:var(--silkface);font-weight:600;font-size:.82rem;
  letter-spacing:.12em;text-transform:uppercase;color:var(--ink)}

/* chapter interstitial */
.chapter{position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:1rem;background:var(--void);
  opacity:0;pointer-events:none;transition:opacity .45s}
.chapter.show{opacity:1}
.chapter h2{font-family:var(--silkface);font-weight:600;font-size:3.2rem;margin:0;
  letter-spacing:.05em;text-transform:uppercase;color:var(--ink)}
.dots{display:flex;gap:.5rem}
.dots i{width:.55rem;height:.55rem;border-radius:50%;background:var(--edge)}
.dots i.on{background:var(--lamp-a);box-shadow:0 0 8px rgba(233,166,63,.6)}

/* GO — full-screen, only when the tone has ENDED */
.goflash{position:fixed;inset:0;z-index:20;display:flex;align-items:center;justify-content:center;
  background:rgba(10,12,15,.9);opacity:0;pointer-events:none;transition:opacity .18s}
.goflash.show{opacity:1}
.goflash span{font-family:var(--silkface);font-weight:700;font-size:16vmin;letter-spacing:.06em;
  color:var(--lamp-a);text-shadow:0 0 6vmin rgba(233,166,63,.45)}

/* done */
.done{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:.8rem;background:var(--void);opacity:0;pointer-events:none;
  transition:opacity .5s}
.done.show{opacity:1}
.done .tick{font-size:4rem;color:var(--lamp-g);line-height:1}
.done h2{font-family:var(--silkface);font-weight:600;font-size:2.2rem;margin:0;
  letter-spacing:.06em;text-transform:uppercase}
.done p{color:var(--silk-2);margin:0;font-size:.9rem}

/* launcher — the idle screen: who, and which pass */
.launch{width:min(46rem,94%);background:var(--face);border:1px solid var(--edge);
  border-radius:4px;box-shadow:inset 0 1px 0 var(--hi),0 1rem 2.6rem rgba(0,0,0,.45);
  padding:1.4rem 1.6rem 1.3rem;display:none;flex-direction:column;gap:1rem}
.launch.show{display:flex}
.launch .lab{font-family:var(--silkface);font-weight:600;font-size:.74rem;
  letter-spacing:.24em;text-transform:uppercase;color:var(--silk-2)}
.people{display:flex;flex-wrap:wrap;gap:.45rem}
.person{border:1px solid var(--edge);background:var(--well);border-radius:3px;
  padding:.5rem .8rem;cursor:pointer;text-align:left;color:var(--silk);
  font-family:var(--readout);font-size:.86rem;min-width:8rem}
.person b{display:block;font-family:var(--silkface);font-weight:600;font-size:1rem;
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink)}
.person small{display:block;font-size:.66rem;color:var(--silk-2);margin-top:.1rem}
.person.sel{border-color:var(--lamp-a);box-shadow:inset .18em 0 0 var(--lamp-a)}
.person.thin small{color:var(--lamp-a)}
.newname{width:100%;background:var(--well);border:1px solid var(--edge);border-radius:3px;
  color:var(--ink);padding:.6rem .7rem;font-family:var(--readout);font-size:.95rem}
.newname:focus{outline:none;border-color:#4A3D22}
.acts{display:flex;gap:.5rem;flex-wrap:wrap}
button.key{font-family:var(--silkface);font-weight:600;font-size:.86rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink);padding:.6rem 1.1rem;cursor:pointer;
  border:1px solid #15171B;border-radius:3px;
  background:linear-gradient(180deg,#242932,#1A1E26);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 1px 2px rgba(0,0,0,.55)}
button.key:hover{filter:brightness(1.18)}
button.key:disabled{opacity:.45;cursor:default}
button.key.primary{background:linear-gradient(180deg,#F0B45B,#D9922F);color:#0E1013;
  border-color:#7A5A22}
button.key.danger{color:#E8B6AF;background:linear-gradient(180deg,#33211E,#241715);
  border-color:#5C2E27}
button.key.danger.armed{color:#0E1013;border-color:#7A2E26;
  background:linear-gradient(180deg,#E8776A,#C8483B)}
.danger-row{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;
  padding-top:.9rem;margin-top:.2rem;border-top:1px solid var(--edge)}
.danger-why{font-size:.76rem;color:var(--silk-2)}
.name-warn{font-size:.78rem;color:var(--lamp-a);margin-top:.35rem;min-height:1rem}
.hint{font-size:.76rem;color:var(--silk-2)}

/* audio legend — the house's three tones, always true, always the same words */
.legend{flex:0 0 auto;display:flex;gap:1.6rem;flex-wrap:wrap;padding-top:.7rem;
  border-top:1px solid var(--edge);box-shadow:0 -1px 0 var(--hi);
  font-size:.72rem;color:var(--silk-2)}
.legend b{font-family:var(--silkface);font-weight:600;letter-spacing:.16em;
  text-transform:uppercase;color:var(--silk);margin-right:.35rem}

@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important}
}
@media (max-width:52rem){
  .read .body{font-size:1.2rem}
  .stage{gap:.8rem}
}
</style>
</head>
<body>
<div class="wrap">

  <header class="crown">
    <span class="mark"><b>Z</b>OS</span>
    <span class="what">Enroll</span>
    <span class="sp"></span>
    <span class="who" id="person"></span>
    <span class="phase" id="phase"></span>
    <span class="hear"><i id="hearDot"></i><span id="hearLab"></span></span>
    <span class="conn" id="conn">connecting</span>
  </header>

  <main class="stage">

    <div class="launch" id="launcher">
      <div>
        <span class="lab">Who is enrolling</span>
        <div class="people" id="rosterList"></div>
      </div>
      <div>
        <span class="lab">Or a new name</span>
        <input class="newname" id="newName" placeholder="First name is enough" autocomplete="off"/>
        <div class="name-warn" id="nameWarn"></div>
      </div>
      <div class="acts">
        <button class="key primary" id="bFull">Full enroll</button>
        <button class="key" id="bFace">Face only</button>
        <button class="key" id="bVoice">Voice only</button>
      </div>
      <div class="hint" id="launchHint">Two takes at the desk. First: read the card. Second: look away and count to ten.</div>
      <div class="danger-row" id="dangerRow">
        <button class="key danger" id="bErase" hidden>Erase and re-record</button>
        <span class="danger-why" id="eraseWhy"></span>
      </div>
    </div>

    <div class="read" id="readCard">
      <span class="lab" id="readLab">Read at your normal pace</span>
      <div class="body" id="readBody"></div>
      <div class="meter" id="meter"><i id="meterBar"></i></div>
      <div class="left" id="meterLeft"></div>
    </div>

    <div class="angles" id="angles">
      <svg width="230" height="74" viewBox="0 0 230 74" aria-hidden="true">
        <path class="head" d="M115 66 a30 30 0 0 0 0 -52 a30 30 0 0 0 0 52"/>
        <path class="seg" id="seg_profile_L"        d="M18 58 A62 62 0 0 1 42 24"/>
        <path class="seg" id="seg_three_quarter_L"  d="M50 18 A62 62 0 0 1 84 6"/>
        <path class="seg" id="seg_front"            d="M96 4  A62 62 0 0 1 134 4"/>
        <path class="seg" id="seg_three_quarter_R"  d="M146 6 A62 62 0 0 1 180 18"/>
        <path class="seg" id="seg_profile_R"        d="M188 24 A62 62 0 0 1 212 58"/>
      </svg>
      <div class="cap" id="angleCap">angles · <b>0</b> of 5</div>
    </div>

    <div class="vf" id="vf">
      <i></i><i></i><i></i><i></i>
      <div class="t"><span id="vfText">Hold still</span><small id="vfPose"></small></div>
    </div>

    <div class="stack" id="stack"></div>

    <div class="chapter" id="chapter">
      <h2 id="chTitle"></h2>
      <div class="dots" id="chDots"></div>
      <div class="line show muted" id="chSub"></div>
    </div>

    <div class="done" id="donebox">
      <div class="tick">&#10003;</div>
      <h2>Thank you</h2>
      <p id="doneSub">Camera returning to door watch.</p>
    </div>
  </main>

  <footer class="legend">
    <span><b>Long beep</b>speak when it ends</span>
    <span><b>Short chirp</b>stop</span>
    <span><b>Two tones</b>session over</span>
  </footer>
</div>

<div class="goflash" id="goBanner"><span>GO</span></div>

<script>
"use strict";
var $=function(id){return document.getElementById(id)};
var conn=$('conn'), phaseEl=$('phase'), personEl=$('person'), stack=$('stack');
var chapter=$('chapter'), chDots=$('chDots'), chTitle=$('chTitle'), chSub=$('chSub');
var vf=$('vf'), vfText=$('vfText'), vfPose=$('vfPose');
var goBanner=$('goBanner'), donebox=$('donebox'), launcher=$('launcher');
var readCard=$('readCard'), readLab=$('readLab'), readBody=$('readBody');
var meter=$('meter'), meterBar=$('meterBar'), meterLeft=$('meterLeft');
var anglesBox=$('angles'), angleCap=$('angleCap');
var BINS=['profile_L','three_quarter_L','front','three_quarter_R','profile_R'];
var goTimer=null, meterTimer=null;

function addLine(text, cls){
  if(!text) return;
  var d=document.createElement('div');
  d.className='line '+(cls||'');
  d.textContent=text;
  stack.appendChild(d);
  requestAnimationFrame(function(){ d.classList.add('show'); });
  while(stack.children.length>5) stack.removeChild(stack.firstChild);
}
function clearStack(){ stack.innerHTML=''; }
function setStatus(_c,_t){ /* status now lives in the card + legend, not a chip */ }

/* the read card, with the window it is actually being given */
function showRead(ev){
  var repeat = ev.mode==='repeat';
  readCard.classList.toggle('repeat', repeat);
  readLab.textContent = repeat ? 'Listen, then repeat after the coach'
                               : 'Read at your normal pace';
  readBody.textContent = ev.text||'';
  readCard.classList.add('show');
  var secs = parseFloat(ev.seconds||0);
  clearInterval(meterTimer); meterTimer=null;
  meter.classList.remove('run'); meterBar.style.transition='none'; meterBar.style.width='0';
  meterLeft.textContent = secs ? (secs.toFixed(1)+' s window') : '';
}
/* the window only actually opens on the beep, so run the bar from there */
function runMeter(secs){
  if(!secs) return;
  var end=Date.now()+secs*1000;
  meterBar.style.transition='none'; meterBar.style.width='0';
  requestAnimationFrame(function(){
    meter.classList.add('run');
    meterBar.style.transitionDuration=secs+'s';
    meterBar.style.width='100%';
  });
  clearInterval(meterTimer);
  meterTimer=setInterval(function(){
    var left=Math.max(0,(end-Date.now())/1000);
    meterLeft.textContent = left>0 ? (left.toFixed(1)+' s left') : 'window closed';
    if(left<=0){ clearInterval(meterTimer); meterTimer=null; }
  },100);
}
var lastSecs=0;

function armGo(ms){
  ms = isNaN(ms) ? 1800 : ms;
  if(goTimer) clearTimeout(goTimer);
  goTimer=setTimeout(function(){
    goBanner.classList.add('show');
    runMeter(lastSecs);
    setTimeout(function(){ goBanner.classList.remove('show'); }, 900);
  }, ms);
}

var HAVE=[], WANT=null;
function noteAngle(bin){ if(bin && HAVE.indexOf(bin)<0) HAVE.push(bin); }
function paintAngles(have, want){
  if(have){ HAVE=[]; have.forEach(noteAngle); }
  if(want && want.length) WANT=want;
  var got={}; HAVE.forEach(function(b){ got[b]=1; });
  var list = WANT && WANT.length ? WANT : BINS;
  list.forEach(function(b){
    var el=$('seg_'+b);
    if(el) el.classList.toggle('got', !!got[b]);
  });
  anglesBox.classList.add('show');
  angleCap.innerHTML='angles · <b>'+HAVE.length+'</b> of '+list.length;
}
function showChapter(ev){
  chTitle.textContent = ev.text||'';
  chSub.textContent = ev.sub||'';
  var m=String(ev.n||'')+'/'+String(ev.total||'');
  var digits=(String(ev.text||'').match(/(\d+)\s*\/\s*(\d+)/));
  var n = parseInt(ev.n,10) || (digits?parseInt(digits[1],10):0);
  var tot = parseInt(ev.total,10) || (digits?parseInt(digits[2],10):0);
  chDots.innerHTML='';
  for(var i=1;i<=(tot||0);i++){
    var d=document.createElement('i'); if(i<=n) d.className='on'; chDots.appendChild(d);
  }
  chapter.classList.add('show');
  setTimeout(function(){ chapter.classList.remove('show'); }, 2400);
}
function setLauncher(running, text){
  launcher.classList.toggle('show', !running);
  if(!running){
    readCard.classList.remove('show');
    anglesBox.classList.remove('show');
    vf.className='vf';
    if(text) $('launchHint').textContent=text;
  }
}

function onEvent(ev){
  var t=ev.type||'';
  if(ev.phase) phaseEl.textContent=ev.phase;
  if(ev.person){ personEl.textContent=ev.person; personEl.classList.add('show'); }
  if(t==='clear'){ clearStack(); chapter.classList.remove('show'); donebox.classList.remove('show'); return; }
  if(t==='session'){
    if(ev.running){ setLauncher(true); donebox.classList.remove('show');
      clearStack(); HAVE=[]; WANT=null; }
    else setLauncher(false, ev.text||'Session finished — pick a pass to go again.');
    return;
  }
  if(t==='phase'){ phaseEl.textContent=ev.text||ev.phase||''; return; }
  if(t==='section'){ phaseEl.textContent=ev.text||'section'; showChapter(ev); return; }
  if(t==='line'){
    if(ev.cls==='choice'){
      var d=document.createElement('div'); d.className='line choice';
      String(ev.text||'').split('·').forEach(function(p){
        var s=document.createElement('span'); s.textContent=p.trim(); d.appendChild(s);
      });
      stack.appendChild(d);
      requestAnimationFrame(function(){ d.classList.add('show'); });
      return;
    }
    addLine(ev.text, ev.cls||'muted'); return;
  }
  if(t==='coach'){ addLine(ev.text,'coach'); return; }
  if(t==='read'){ lastSecs=parseFloat(ev.seconds||0); showRead(ev); return; }
  if(t==='beep'){ armGo(parseInt(ev.ms,10)); return; }
  if(t==='chirp'){
    if(goTimer) clearTimeout(goTimer);
    goBanner.classList.remove('show');
    clearInterval(meterTimer); meterTimer=null;
    meterLeft.textContent='window closed';
    addLine(ev.text||'Window closed','ok');
    return;
  }
  if(t==='angles'){ paintAngles(ev.have, ev.want); return; }
  if(t==='face'){
    vf.className='vf show capturing';
    vfText.textContent=ev.text||'Hold still';
    vfPose.textContent=ev.pose?('asking for '+String(ev.pose).replace(/_/g,' ')):'';
    return;
  }
  if(t==='face_ok'){
    vf.className='vf show ok';
    vfText.textContent='Face saved';
    var bin=ev.bin?String(ev.bin).replace(/_/g,' '):'';
    var det=(ev.det!=null)?(' · det '+Number(ev.det).toFixed(2)):'';
    vfPose.textContent = bin ? (bin+det) : '';
    if(ev.bin){ noteAngle(ev.bin); paintAngles(null, null); }
    return;
  }
  if(t==='face_err'){
    vf.className='vf show';
    vfText.textContent='Capture was weak';
    vfPose.textContent='we can redo it at the end';
    addLine(ev.text||'Face capture weak — the voice take continues.','warn');
    return;
  }
  if(t==='ok'){ addLine(ev.text||'Take saved','ok'); return; }
  if(t==='done'){
    readCard.classList.remove('show'); anglesBox.classList.remove('show');
    vf.className='vf';
    if(ev.text) $('doneSub').textContent=ev.text;
    donebox.classList.add('show');
    return;
  }
  if(ev.text) addLine(ev.text, ev.cls||'');
}

/* ---- launcher: same wire as before (/people, /session, /start) ---- */
var ROSTER=[], SEL=null;
function isPerson(p){
  // house voice banks and scratch slots are not people you enrol.
  if(p.is_user) return true;
  if(/_tts$/.test(p.id) || p.id==='announcer' || p.id==='dry' || p.id==='ada') return false;
  if(/^s\d+$/.test(p.id)) return false;
  return (p.face||0) > 0;      // a real person has been seen at least once
}
function drawRoster(){
  var box=$('rosterList'); box.innerHTML='';
  ROSTER.filter(isPerson).forEach(function(p){
    var b=document.createElement('button');
    b.type='button';
    b.className='person'+(SEL===p.id?' sel':'')+((p.voice||0)<4?' thin':'');
    var note = (p.voice||0)<4 ? ('voice bank thin · '+(p.voice||0))
                              : ('face '+(p.face||0)+' · voice '+(p.voice||0));
    b.innerHTML='<b></b><small></small>';
    b.querySelector('b').textContent=p.display||p.id;
    b.querySelector('small').textContent=note+(p.at_desk?' · at the desk':'');
    b.onclick=function(){ SEL=p.id; $('newName').value=''; $('nameWarn').textContent='';
                          ARMED=false; drawRoster(); paintErase(); };
    box.appendChild(b);
  });
}
/* A first name is enough until it is not. We only ask for more when the house
   already knows someone by that first name — asking everyone for a surname to
   protect against a collision that usually does not exist is the kind of form
   this screen is trying not to be. */
function firstTok(x){ return String(x||'').trim().split(/\s+/)[0].toLowerCase(); }
function clashes(typed){
  var f=firstTok(typed);
  if(!f) return null;
  if(String(typed).trim().split(/\s+/).length > 1) return null;   // they gave more
  var hit=ROSTER.filter(isPerson).filter(function(p){
    return firstTok(p.display||p.id)===f;
  })[0];
  return hit || null;
}
function checkName(){
  var typed=($('newName').value||'').trim();
  var w=$('nameWarn');
  var hit = typed ? clashes(typed) : null;
  if(hit){
    w.textContent='The house already knows a '+(hit.display||hit.id)+
      '. Add a last name so they do not get mixed up.';
  } else { w.textContent=''; }
  return !hit;
}
function pick(){
  var typed=($('newName').value||'').trim();
  if(typed){
    if(clashes(typed)) return null;
    return {person_id:typed.toLowerCase().replace(/[^a-z0-9_]+/g,'_'), display:typed};
  }
  if(SEL){
    var p=ROSTER.filter(function(x){return x.id===SEL})[0]||{};
    return {person_id:SEL, display:p.display||SEL};
  }
  return null;
}
function start(mode){
  var r0=pick();
  if(!r0){
    if(!checkName()) $('newName').focus();
    else $('launchHint').textContent='Pick a name first, or type a new one.';
    return;
  }
  ['bFull','bFace','bVoice'].forEach(function(i){ $(i).disabled=true; });
  $('launchHint').textContent='Starting…';
  fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({person_id:r0.person_id,display:r0.display,create:true,replace:false,mode:mode})})
    .then(function(r){return r.json()})
    .then(function(d){
      if(d && d.ok===false) $('launchHint').textContent=(d.error||'Could not start')+'';
    })
    .catch(function(){ $('launchHint').textContent='Could not start — is the coach running?'; })
    .then(function(){ ['bFull','bFace','bVoice'].forEach(function(i){ $(i).disabled=false; }); });
}
$('bFull').onclick=function(){ start('full'); };
$('bFace').onclick=function(){ start('face'); };
$('bVoice').onclick=function(){ start('voice'); };
$('newName').addEventListener('input',function(){
  if(this.value.trim()){ SEL=null; drawRoster(); }
  checkName(); paintErase();
});

/* Erase is destructive, so it is scoped the way the User scoped it: the User, and
   only while the house can see their face. `replace:true` already existed on
   /start — this only surfaces it, behind an auth gate and a second press. */
var AUTH={user_auth:false,by_face:false,tier:null}, ARMED=false, armTimer=null;
function selectedPerson(){
  if(($('newName').value||'').trim()) return null;
  return ROSTER.filter(function(p){return p.id===SEL})[0]||null;
}
function paintErase(){
  var b=$('bErase'), why=$('eraseWhy'), p=selectedPerson();
  var eligible = AUTH.user_auth && AUTH.by_face;
  var hasBank = p && ((p.face||0)+(p.voice||0))>0;
  if(!eligible){
    b.hidden=true;
    why.textContent = p&&hasBank
      ? 'Erasing a bank needs the User signed in by face.' : '';
    return;
  }
  if(!hasBank){ b.hidden=true; why.textContent=''; return; }
  b.hidden=false;
  if(ARMED){
    b.classList.add('armed');
    b.textContent='Yes — erase and start';
    why.textContent='This deletes '+(p.display||p.id)+"'s "+(p.face||0)+
      ' face and '+(p.voice||0)+' voice records. It cannot be undone.';
  } else {
    b.classList.remove('armed');
    b.textContent='Erase and re-record';
    why.textContent='Records are kept and added to unless you erase first.';
  }
}
$('bErase').onclick=function(){
  var p=selectedPerson(); if(!p) return;
  if(!ARMED){
    ARMED=true; paintErase();
    clearTimeout(armTimer);
    armTimer=setTimeout(function(){ ARMED=false; paintErase(); }, 6000);
    return;
  }
  clearTimeout(armTimer); ARMED=false; paintErase();
  ['bFull','bFace','bVoice','bErase'].forEach(function(i){ $(i).disabled=true; });
  $('launchHint').textContent='Erasing '+(p.display||p.id)+' and starting a fresh enroll…';
  fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({person_id:p.id,display:p.display||p.id,create:true,
                         replace:true,mode:'full'})})
    .then(function(r){return r.json()})
    .then(function(d){ if(d&&d.ok===false) $('launchHint').textContent=(d.error||'Could not start'); })
    .catch(function(){ $('launchHint').textContent='Could not start — is the coach running?'; })
    .then(function(){ ['bFull','bFace','bVoice','bErase'].forEach(function(i){ $(i).disabled=false; });
                      loadRoster(); });
};
function loadAuth(){
  fetch('/v1/auth').then(function(r){return r.json()}).then(function(d){
    if(d&&d.ok){ AUTH={user_auth:!!d.user_auth,by_face:!!d.by_face,tier:d.tier}; }
    else AUTH={user_auth:false,by_face:false,tier:null};
    paintErase();
  }).catch(function(){ AUTH={user_auth:false,by_face:false,tier:null}; paintErase(); });
}
loadAuth(); setInterval(loadAuth, 5000);

function loadRoster(){
  fetch('/people').then(function(r){return r.json()}).then(function(d){
    ROSTER=(d&&d.people)||[];
    var me=ROSTER.filter(function(p){return p.is_user})[0];
    if(!SEL && me) SEL=me.id;
    drawRoster(); paintErase();
  }).catch(function(){});
}
function loadSession(){
  fetch('/session').then(function(r){return r.json()}).then(function(d){
    setLauncher(!!(d&&d.running));
  }).catch(function(){ setLauncher(false); });
}
loadRoster(); loadSession(); setInterval(loadRoster, 15000);

/* Hearing cue. Grok proxied VoiceOnly's lobes onto this origin rather than
   add a second level event, so the same stream the wall uses tells the reader
   the room is picking them up. No new wire, and it cannot disagree with the
   wall because it IS the wall's feed. */
function hearing(){
  var dot=$('hearDot'), lab=$('hearLab');
  if(!dot) return;
  var es;
  try { es=new EventSource('/v1/sound/stream'); } catch(e){ return; }
  var fade=null;
  es.onmessage=function(m){
    var d; try{ d=JSON.parse(m.data); }catch(e){ return; }
    var s=d.sound||d;
    if(!s) return;
    var live=!!s.speech;
    dot.classList.toggle('on', live);
    lab.textContent = live ? 'hearing the room' : 'quiet';
    if(fade) clearTimeout(fade);
    if(live) fade=setTimeout(function(){ dot.classList.remove('on'); lab.textContent='quiet'; }, 1200);
  };
  es.onerror=function(){ lab.textContent=''; dot.classList.remove('on'); };
}
hearing();

function connect(){
  var es=new EventSource('/events');
  es.onopen=function(){ conn.textContent='live'; };
  es.onerror=function(){ conn.textContent='reconnecting'; };
  es.onmessage=function(m){ try{ onEvent(JSON.parse(m.data)); }catch(e){} };
}
connect();
</script>
</body>
</html>
"""



def _init_vouches(person_id: str, mode: str, first_boot: bool) -> bool:
    """INIT'S FACE PHOTOS (the User 2026-10-10): the first boot's own walk may take the new User's face stills before
    anyone can sign in as them. Only when the request says first_boot, the mode is face, and the player's own Init
    (GET /v1/init on this install's loopback) is running its face step for exactly this person.
    (The id module's coach has the same rule; this copy serves an install without modules/id.)"""
    if mode != "face" or not first_boot:
        return False
    try:
        import urllib.request as _ur

        with _ur.urlopen(_ports.url("player", "/v1/init"), timeout=3) as r:
            snap = json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001  no answer is no vouch
        return False
    face = ((snap.get("plate") or {}).get("face") or {}) if isinstance(snap, dict) else {}
    return (snap.get("status") == "running" and snap.get("step_id") == "face"
            and str(face.get("person_id") or "") == str(person_id or "").strip().lower())


def health_body() -> dict:
    """GET /health. `register_token` says which Register this coach runs (the User 2026-10-04, Init v2): True = the
    token path (Register never touches Secure); False = the legacy path (secure_legacy clicks Secure off). Init v2
    refuses to start Register unless this reads True."""
    from . import register_camera
    return {"ok": True, "service": "house-enroll-coach", "register_token": bool(register_camera.on())}


# THE SPOKEN VOICE ID'S LINES, READY BEFORE ANYONE ASKS (2026-10-12): voice_register's verbal routine says its cues in the
# kit's announcer voice, rendered once and kept on disk; on a CPU the announcer renders about as fast as it speaks, so
# the coach renders them in the background a while after it starts (once per install; the disk cache answers after).
# Off on the house and under the video routine. Kill: CRISSA_VOICE_ID_WARM=0.
VERBAL_WARM_DELAY_S = float(os.environ.get("CRISSA_VOICE_ID_WARM_DELAY_S", "45") or 45)


def _verbal_warm_later() -> threading.Thread | None:
    if os.environ.get("CRISSA_VOICE_ID_WARM", "1").strip().lower() in ("0", "off", "false", "no"):
        return None

    def run() -> None:
        time.sleep(max(0.0, VERBAL_WARM_DELAY_S))
        try:
            from . import voice_register as vr
            if vr.routine() != "verbal":
                return
            # round 20: in a pack Lila's lines first (Setup's cue voice; Init's /warm may have come before this coach)
            try:
                lila_first = not _paths.is_house()
            except Exception:  # noqa: BLE001
                lila_first = False
            if lila_first:
                got = vr.verbal_warm(speaker="lila")
                print("  voice id: %d of %d spoken lines ready (lila)" % (sum(1 for v in got.values() if v), len(got)),
                      flush=True)
            got = vr.verbal_warm()
            print("  voice id: %d of %d spoken lines ready" % (sum(1 for v in got.values() if v), len(got)), flush=True)
        except Exception as e:  # noqa: BLE001  the routine renders what it needs itself
            print("  voice id: lines not rendered ahead (%s)" % type(e).__name__, flush=True)
    t = threading.Thread(target=run, name="voice-id-warm", daemon=True)
    t.start()
    return t

class CoachHub:
    """Thread-safe event fan-out for SSE clients + enroll start API."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = DEFAULT_PORT
        self.history: list[dict[str, Any]] = []
        self.last_read: dict[str, Any] | None = None
        self._session_lock = threading.Lock()
        self._generation = 0
        self._session: dict[str, Any] = {
            "running": False,
            "person_id": None,
            "pid": None,
            "started": None,
            "last_error": None,
            "last_rc": None,
            "cancelled": False,
            "generation": 0,
        }

    def session_status(self) -> dict[str, Any]:
        with self._session_lock:
            out = dict(self._session)
        out["progress"] = self.progress(float(out.get("started") or 0.0))
        return out

    def progress(self, since: float) -> dict[str, Any]:
        """What the session has said since it started (the User 2026-10-10: Init's face step shows it): the last phase,
        the last instruction, and the face photos stored so far against the poses asked for."""
        with self._lock:
            evs = [e for e in self.history if float(e.get("ts") or 0) >= since] if since else []
        phase = next((str(e.get("text") or "") for e in reversed(evs) if e.get("type") == "phase"), "")
        line = next((str(e.get("text") or "") for e in reversed(evs) if e.get("type") in ("coach", "face")), "")
        done = sum(1 for e in evs if e.get("type") == "face_ok")
        total = 0
        m = re.search(r"(\d+)\s*/\s*(\d+)", phase)
        if m:
            total = int(m.group(2))
        plan = next((e for e in reversed(evs) if e.get("type") == "face_plan"), None)
        if plan is not None:                       # the face section's own count, from before its first pose (2026-10-10)
            total = int(plan.get("total") or total or 0)
        why = next((str(e.get("text") or "") for e in reversed(evs) if e.get("type") == "face_fail"), "")
        out = {"phase": phase, "line": line.split(" — ")[0], "done": done, "total": total, "why": why}
        # a take the quiet gate dropped (the User 2026-10-10, .101 round 5 GG): Init says so and offers that take again
        dropped = [dict({k: e.get(k) for k in ("take", "take_id", "active", "need")},   # round 20: noise or quiet
                        **{k: e[k] for k in ("why", "peak") if k in e}) for e in evs
                   if e.get("type") == "take_dropped"]
        if dropped:
            out["dropped"] = dropped
        return out

    def _reserve_replace(self, person_id: str, gen: int, root: Any) -> dict[str, Any] | None:
        """Round 46: the replace's clear waits on nothing else - refuse it while a session runs, while another replace
        is clearing (a double Confirm), after a Cancel, or for a frozen identity; else reserve it (None)."""
        with self._session_lock:
            if self._session.get("running") or getattr(self, "_replacing", None):
                return {"ok": False, "error": "session already running",
                        "person_id": self._session.get("person_id") or getattr(self, "_replacing", None)}
            if int(self._generation or 0) != gen:
                return {"ok": False, "cancelled": True, "error": "cancelled"}
            self._replacing = person_id
        try:
            from .person import assert_not_frozen
            assert_not_frozen(person_id, root=root)
        except Exception as e:  # noqa: BLE001
            self._release_replace()
            return {"ok": False, "frozen": True, "person_id": person_id, "error": str(e)[:200]}
        return None

    def _release_replace(self) -> None:
        with self._session_lock:
            self._replacing = None

    def cancel_enroll(self) -> dict[str, Any]:
        """Stop the take for real: bump generation so a late start cannot spawn,
        kill the session group + enroll paplay, drop FF/lease, reopen the ear.
        """
        with self._session_lock:
            self._generation = int(self._generation or 0) + 1
            gen = self._generation
            pid = self._session.get("pid")
            running = bool(self._session.get("running"))
            person_id = self._session.get("person_id")
            was_mode = str(self._session.get("mode") or "")
            self._session["running"] = False
            self._session["cancelled"] = True
            self._session["generation"] = gen
            self._session["last_error"] = "cancelled"
        kill = kill_enroll_tree(int(pid) if pid else None)
        with self._session_lock:
            self._session["pid"] = None
        abort_register_hw(was_mode)
        self.last_read = None
        self.publish({"type": "coach", "text": "Cancelled. Start again when you are ready."})
        self.publish({"type": "done", "text": "Cancelled", "cancelled": True})
        return {
            "ok": True,
            "cancelled": True,
            "was_running": running,
            "person_id": person_id,
            "generation": gen,
            "kill": kill,
        }

    def warm_verbal(self, voice: str = "", lines: list | None = None) -> dict[str, Any]:
        """Round 20 (FIRST CUE AS ITS CARD OPENS): Init asks for the spoken Voice ID's lines now, in the voice that will
        say them (Lila's in Setup; the coach's own warm renders the cue voice of its own process, the announcer), so the
        first cue needs no render when the card opens. Returns at once. `lines` (.101 round 29, HER LINES READY BEFORE
        THE HANDOVER, lib/crissa_init): these lines in `voice`, rendered into the disk cache the mouths run reads, no
        level kept off them (the run reads its level off its own first lines, as before)."""
        if lines:
            texts = [str(t)[:400] for t in lines if str(t).strip()][:12]
            if not texts or not voice:
                return {"ok": False, "error": "lines need a voice"}

            def run_lines() -> None:
                try:
                    from . import voice_register as vr
                    n = sum(1 for t in texts if vr._ann_render(t, speaker=voice) is not None)
                    print("  her lines: %d of %d ready ahead (%s)" % (n, len(texts), voice), flush=True)
                except Exception as e:  # noqa: BLE001  the mouths run renders what it needs itself
                    print("  her lines: not rendered ahead (%s)" % type(e).__name__, flush=True)
            threading.Thread(target=run_lines, name="lines-warm-now", daemon=True).start()
            return {"ok": True, "warming": voice, "lines": len(texts)}
        if os.environ.get("CRISSA_VOICE_ID_WARM", "1").strip().lower() in ("0", "off", "false", "no"):
            return {"ok": True, "warming": False}

        def run() -> None:
            try:
                from . import voice_register as vr
                if vr.routine() != "verbal":
                    return
                got = vr.verbal_warm(speaker=voice or None)
                print("  voice id: %d of %d spoken lines ready (%s)" % (sum(1 for v in got.values() if v), len(got),
                                                                         voice or "the cue voice"), flush=True)
            except Exception as e:  # noqa: BLE001  the routine renders what it needs itself
                print("  voice id: lines not rendered ahead (%s)" % type(e).__name__, flush=True)
        threading.Thread(target=run, name="voice-id-warm-now", daemon=True).start()
        return {"ok": True, "warming": voice or "the cue voice"}

    def prearm_register(self, *, station: str = "desk") -> dict[str, Any]:
        """voice_register.prearm on a thread; returns at once. A session
        already running, or a fresh marker, is left alone."""
        with self._session_lock:
            if self._session.get("running"):
                return {"ok": False, "error": "session already running"}
        try:
            from . import voice_register as _vr

            if _vr._prearm_read():
                return {"ok": True, "prearmed": "fresh"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:160]}

        def _go() -> None:
            try:
                from .site import load_site

                _vr.prearm(load_site(), station=station, write_marker=True)
                self.publish({"type": "line", "text": "Camera on the desk — ready.", "cls": "muted"})
            except Exception as e:
                self.publish({"type": "line", "text": f"pre-arm failed: {e}"[:160], "cls": "muted"})
                return
            # nobody pressed: give it all back (the routine unlinks the
            # marker when it adopts it, so this finds nothing then)
            time.sleep(float(_vr.PREARM_MAX_AGE_SEC))
            with self._session_lock:
                pressed = bool(self._session.get("running"))
            if pressed and _vr.prearm_adopt_on():
                # round 46: Begin came inside the pre-arm's age - the routine adopts the marker and gives it all back
                # in its finally; the session's end expires a marker it never adopted (expire_unadopted_prearm)
                return
            try:
                r = _vr.prearm_expire()
                if r.get("expired"):
                    self.publish({"type": "line", "text": "Pre-arm expired — camera back on the door.", "cls": "muted"})
            except Exception as e:
                self.publish({"type": "line", "text": f"pre-arm expiry: {e}"[:160], "cls": "muted"})

        threading.Thread(target=_go, name="register-prearm", daemon=True).start()
        return {"ok": True, "prearmed": "started"}

    def start_enroll(
        self,
        person_id: str,
        *,
        display: str | None = None,
        create: bool = True,
        replace: bool = False,
        mode: str = "full",
        station: str = "desk",
        mic: str = "",
        first_boot: bool = False,
        only: str = "",
        poses: str = "",
        mouths: Any = None,
        cue_voice: str = "",
        visitor: bool = False,
    ) -> dict[str, Any]:
        """Create/store people/<id> if needed, spawn quality enroll. One at a time.

        Operator (User at desk, face-authed) types the enrollee's name; id is the slug.

        If that id already has face/voice records and ``replace`` is false, returns
        ``needs_confirm`` so the UI can alert: confirm to replace existing records?
        """
        from .person import (
            allocate_register_identity,
            bank_counts,
            clear_enroll_records,
            ensure_person,
            format_display_name,
            format_person_id,
        )

        mode = mode if mode in ("face", "voice", "full", "voice-register", "mouths") else "full"
        if mode == "mouths":
            # HER OWN VOICE THROUGH THE SPEAKERS (mouth_prints, 2026-10-13): no person is made or touched - the prints
            # go to the ear's mouths folder under crissa_her_tts / announcer
            person_id, display, create, replace = "crissa_her_tts", "her own voice", False, False
        with self._session_lock:
            gen = int(self._generation or 0)
        ident_role = None
        ident_initials = None
        raw_name = (display or person_id or "").strip()
        visitor_id = ""
        if mode == "voice-register" and visitor:
            # ADD A VISITOR (round 20): the player names the visitor's id; it is kept EXACTLY (a first name the User
            # shares is never a re-enroll of the User), and only while that player is adding exactly that person now
            from . import register_camera as _rc
            from .person import format_display_name as _fdn, format_person_id as _fpi

            visitor_id = _fpi(person_id or "")
            if not visitor_id or not _rc.visitor_run(_ports.url("player", "/v1/init"), visitor_id):
                return {"ok": False, "error": "no visitor is being added for %s on this computer" % (visitor_id or "nobody")}
            person_id, display = visitor_id, _fdn(display or raw_name) or visitor_id
            ident_role, ident_initials = "guest", None
        elif mode == "voice-register":
            try:
                from .site import load_site as _load_site_early

                _root = _load_site_early().root
            except Exception:
                _root = None
            ident = allocate_register_identity(
                raw_name, root=_root, requested_id=person_id
            )
            if ident.get("error"):                # round 46: a requested id that names somebody else
                return {"ok": False, "collision": True, "person_id": ident.get("person_id") or "",
                        "error": str(ident["error"])[:200]}
            person_id = ident["person_id"]
            display = ident["display"]
            ident_role = ident.get("role")
            ident_initials = ident.get("initials")
        else:
            person_id = format_person_id(person_id or raw_name)
            ident_role = None
            ident_initials = None
        if not person_id:
            return {
                "ok": False,
                "error": "enter a name (e.g. Jonathon or user_S) → stored as that id",
            }

        display = format_display_name(display or raw_name) or person_id
        operator_id = None
        cleared: dict[str, Any] | None = None
        try:
            from .site import load_site
            from .roles import is_primary_user, mark_role, user_id as primary_user

            site = load_site()
            counts = bank_counts(person_id, site.root)
            # Partial retakes (Register Face / Register Voice buttons) append
            # to the bank — no replace prompt, no clearing.
            if mode != "full":
                replace = False
            if mode == "voice-register":
                replace = False
            # Desk Register on :8099 is the operator ack (VOICE-REGISTER.md).
            # Identity AUTH is mill/FaceFinder; mill-off leaves AUTH guest even
            # when the User is at the glass, so gating voice-register on it
            # stuck Begin on Look here with no child and no sound.
            if is_primary_user(site, person_id) and mode != "voice-register" and not _init_vouches(person_id, mode, first_boot):
                authed = False
                try:
                    import urllib.request as _ur

                    with _ur.urlopen(
                        os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence")), timeout=1.5
                    ) as r:
                        pres = json.loads(r.read().decode())
                    u = pres.get("user") if isinstance(pres.get("user"), dict) else {}
                    prov = (
                        u.get("auth_provenance")
                        if isinstance(u.get("auth_provenance"), dict)
                        else {}
                    )
                    authed = str(prov.get("tier") or u.get("tier") or "") == "identity"
                except Exception:
                    authed = False
                if not authed:
                    return {
                        "ok": False,
                        "denial": "user_id",
                        "error": (
                            "Only User can register this voice. "
                            "Type your own name, or wait for User."
                        ),
                    }
            if mode == "full" and counts["total"] > 0 and not replace:
                return {
                    "ok": False,
                    "needs_confirm": True,
                    "person_id": person_id,
                    "display": display,
                    "face": counts["face"],
                    "voice": counts["voice"],
                    "error": (
                        f"ALERT: {person_id} already has face={counts['face']} "
                        f"voice={counts['voice']}. Confirm to replace existing records?"
                    ),
                    "alert": (
                        f"ALERT: {display} ({person_id}) already has "
                        f"face={counts['face']} voice={counts['voice']} records.\n\n"
                        "Confirm to REPLACE existing records and re-enroll from scratch?\n"
                        "(Use this if the bank is corrupted so Inspector can try again.)"
                    ),
                }
            if counts["total"] > 0 and replace:
                if replace_guard_on():
                    refused = self._reserve_replace(person_id, gen, site.root)
                    if refused:
                        return refused
                try:
                    cleared = clear_enroll_records(person_id, root=site.root)
                finally:
                    if replace_guard_on():
                        self._release_replace()
                if not cleared.get("ok"):
                    return {
                        "ok": False,
                        "error": (
                            f"could not clear bank for {person_id}: "
                            + "; ".join(cleared.get("errors") or ["unknown"])
                        )[:200],
                        "cleared": cleared,
                    }
            if create or replace:
                ensure_person(
                    person_id,
                    display=display,
                    root=site.root,
                    role=ident_role,
                    initials=ident_initials,
                )
                # New names are Regulars unless they are the office User id
                # or a colliding first-name guest (F/L initials).
                if ident_role == "guest":
                    mark_role(person_id, "guest", root=site.root, display=display)
                elif not is_primary_user(site, person_id) and not ident_role:
                    mark_role(person_id, "regular", root=site.root, display=display)

            # Soft note: who is stamped on presence (the User authed at desk)
            try:
                import urllib.request

                with urllib.request.urlopen(
                    os.environ.get("RAIL_PRESENCE", _ports.url("presence", "/v1/presence")), timeout=1.5
                ) as r:
                    pres = json.loads(r.read().decode())
                for p in pres.get("people") or []:
                    if p.get("certain") and p.get("name"):
                        operator_id = str(p["name"]).lower()
                        break
                if not operator_id and is_primary_user(site, primary_user(site)):
                    operator_id = primary_user(site)
            except Exception:
                operator_id = primary_user(site)
        except Exception as e:
            return {"ok": False, "error": f"cannot create person: {e}"[:160]}

        with self._session_lock:
            if int(self._generation or 0) != gen:
                return {"ok": False, "cancelled": True, "error": "cancelled"}
            if self._session.get("running"):
                return {
                    "ok": False,
                    "error": "session already running",
                    "person_id": self._session.get("person_id"),
                }
            self._session = {
                "running": True,
                "person_id": person_id,
                "display": display,
                "operator_id": operator_id,
                "mode": mode,
                "pid": None,
                "started": time.time(),
                "last_error": None,
                "last_rc": None,
                "cancelled": False,
                "generation": gen,
            }

        tools = Path(__file__).resolve().parent.parent
        house_enroll = tools / "house-enroll"
        env = os.environ.copy()
        env["HOUSE_ENROLL_NO_BROWSER"] = "1"
        env["HOUSE_ENROLL_KEEP_COACH"] = "1"
        # Child session must not bind its own coach — its cues stream to THIS
        # panel (the screen at :{port}) via POST /emit. See coach_start().
        env["ENROLL_UI_URL"] = f"http://127.0.0.1:{self.port}"
        # this module's parent folder (modules/presence) puts `crissa_presence.enroll_panel` on the child's path
        env["PYTHONPATH"] = str(tools.parent) + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        import sys
        py = env.get("HOUSE_ENROLL_PYTHON") or sys.executable
        if mode == "voice-register":
            cmd = [py, "-m", "crissa_presence.enroll_panel", "voice-register", person_id,
                   "--display", display, "--station", station or "desk"]
            if mic:
                cmd += ["--mic", mic]
            if first_boot:
                cmd.append("--first-boot")   # New User: the opening line before the welcome
            if visitor_id:
                env["CRISSA_REGISTER_VISITOR"] = visitor_id    # the routine and presence ask the player again
            else:
                env.pop("CRISSA_REGISTER_VISITOR", None)
            if only in ("read", "count"):
                # ONE TAKE AGAIN (the User 2026-10-10, .101 round 5 GG): Init's "Redo the count" records that take alone
                env["CRISSA_REGISTER_ONLY"] = only
            else:
                env.pop("CRISSA_REGISTER_ONLY", None)
        elif mode == "mouths":
            cmd = [py, "-m", "crissa_presence.enroll_panel", "voice-mouths"]
        elif house_enroll.is_file():
            cmd = ["bash", str(house_enroll), "voice", person_id]
        else:
            cmd = [py, "-m", "crissa_presence.enroll_panel", "voice", person_id]
        if mode == "face":
            cmd.append("--no-voice")
        elif mode == "voice":
            cmd.append("--no-face")
        # ONE VOICE FOR SETUP (2026-10-13): Setup's first run speaks every cue in Lila's voice (voice_register.cue_voice,
        # cues.setup_cue_voice); Voice ID again and the Control Panel's Register keep the announcer. A pack only.
        try:
            in_pack = not _paths.is_house()
        except Exception:  # noqa: BLE001
            in_pack = False
        # round 20 (TWO VOICES, lib/crissa_init): Init names Lila for Voice ID again too when the install has no
        # announcer (no krew); a request that names no voice keeps today's rule
        # THE CAMERA'S VOICE (round 23): Init names the announcer for the spoken directions; that wins over first boot
        lila_cues = (first_boot or str(cue_voice or "").strip().lower() == "lila") and \
            str(cue_voice or "").strip().lower() != "announcer"
        if lila_cues and in_pack and mode in ("voice-register", "face"):
            env["CRISSA_SETUP_CUE_VOICE"] = "lila"
        else:
            env.pop("CRISSA_SETUP_CUE_VOICE", None)
        # THE MISSING ANGLE ONLY (2026-10-13): Init names the face poses still wanted (front, left, right)
        if mode == "face" and poses:
            env["CRISSA_FACE_POSES"] = ",".join(x for x in str(poses).split(",") if x.strip() in ("front", "left",
                                                                                                  "right"))
        else:
            env.pop("CRISSA_FACE_POSES", None)
        # THE TEAM INTRODUCES ITSELF (round 18, mouth_prints): Setup's end names the voice, its words and whether the player
        # says them; without a plan the mouths run is round 17's six lines
        if mode == "mouths" and isinstance(mouths, dict) and mouths:
            env["CRISSA_MOUTHS_PLAN"] = json.dumps(mouths)
        else:
            env.pop("CRISSA_MOUTHS_PLAN", None)

        log_path = _paths.DATA / "enroll-session.log"

        claimed_at = time.time()

        def _run() -> None:
            rc = 1
            err = None
            proc = None
            cancelled = False
            # Working cadence (the User 2026-08-04): lease → aim desk → verify the
            # camera actually moved → enroll → hand back to the door. Without
            # the lease the desk aim is silently ignored while the door
            # aim-hold is live (grabber reports pan_actual=0 from the door).
            if mode in ("face", "full"):
                with self._session_lock:
                    stale = int(self._generation or 0) != gen or self._session.get(
                        "cancelled"
                    )
                if stale:
                    cancelled = True
                    rc = -15
                    err = "cancelled"
                else:
                    _camera_preposition(self.publish)
            try:
                with self._session_lock:
                    stale = int(self._generation or 0) != gen or self._session.get(
                        "cancelled"
                    )
                if stale:
                    cancelled = True
                    rc = -15
                    err = "cancelled"
                else:
                    with log_path.open("ab") as log:
                        log.write(
                            f"\n--- start id={person_id} display={display!r} "
                            f"op={operator_id} {time.strftime('%H:%M:%S')} ---\n".encode()
                        )
                        log.flush()
                        proc = subprocess.Popen(
                            cmd,
                            cwd=str(tools),
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        with self._session_lock:
                            stale = int(self._generation or 0) != gen or self._session.get(
                                "cancelled"
                            )
                            if stale:
                                cancelled = True
                            else:
                                self._session["pid"] = proc.pid
                        if cancelled:
                            kill_enroll_tree(proc.pid)
                            try:
                                proc.wait(timeout=1.5)
                            except Exception:
                                pass
                            rc = -15
                            err = "cancelled"
                        else:
                            self.publish(
                                {
                                    "type": "session",
                                    "running": True,
                                    "person_id": person_id,
                                    "person": person_id,
                                    "text": f"Enrolling {display} as id={person_id}"
                                    + (f" · operator {operator_id}" if operator_id else ""),
                                }
                            )
                            self.publish(
                                {
                                    "type": "line",
                                    "text": f"Stored person id: {person_id}"
                                    + (f" · display {display}" if display != person_id else ""),
                                    "cls": "muted",
                                }
                            )
                            rc = proc.wait()
                            if rc in (-15, 143, -9):
                                cancelled = True
                                err = "cancelled"
            except Exception as e:
                err = str(e)[:200]
                rc = 1
            with self._session_lock:
                stale = int(self._generation or 0) != gen
                if not stale:
                    self._session["running"] = False
                    self._session["last_rc"] = rc
                    self._session["last_error"] = err or (
                        None if rc == 0 else f"exit {rc} — see {log_path}"
                    )
                    self._session["pid"] = None
                    if cancelled:
                        self._session["cancelled"] = True
            if stale:
                return
            if cancelled:
                self.publish(
                    {
                        "type": "session",
                        "running": False,
                        "person_id": person_id,
                        "text": "Cancelled",
                        "ok": False,
                        "cancelled": True,
                    }
                )
            else:
                self.publish(
                    {
                        "type": "session",
                        "running": False,
                        "person_id": person_id,
                        "text": (
                            f"Done · {person_id} — enter another name or Start again"
                            if rc == 0
                            else f"Session ended with error (rc={rc})"
                        ),
                        "ok": rc == 0,
                    }
                )
                self.publish(
                    {
                        "type": "done",
                        "text": "Session complete — thank you." if rc == 0 else "Session failed",
                    }
                )
            # Hand the camera back to the door watch no matter how the
            # session ended (the User 2026-08-04: "stuck pointing at me").
            if proc is not None:
                _camera_hand_back(self.publish)
            expire_unadopted_prearm(self.publish, claimed_at)     # round 46: the timer stood aside while it ran

        threading.Thread(target=_run, daemon=True).start()
        out: dict[str, Any] = {
            "ok": True,
            "person_id": person_id,
            "display": display,
            "operator_id": operator_id,
            "created": create,
            "replaced": bool(replace and cleared),
            "log": str(log_path),
        }
        if cleared:
            out["cleared"] = {
                "removed_n": cleared.get("removed_n"),
                "before": cleared.get("before"),
                "after": cleared.get("after"),
            }
        return out

    def publish(self, event: dict[str, Any]) -> None:
        event = dict(event)
        event.setdefault("ts", time.time())
        with self._lock:
            self.history.append(event)
            if event.get("type") == "read":
                self.last_read = dict(event)
            if event.get("type") == "done":
                self.last_read = None
            if len(self.history) > 200:
                self.history = self.history[-100:]
            subs = list(self._subs)
        raw = json.dumps(event, ensure_ascii=False)
        for q in subs:
            try:
                q.put_nowait(raw)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.append(q)
            hist = list(self.history[-12:])
            if self.last_read and self.last_read not in hist:
                hist.insert(0, self.last_read)
        for ev in hist:
            try:
                q.put_nowait(json.dumps(ev, ensure_ascii=False))
            except queue.Full:
                break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def start(self, port: int | None = None, open_browser: bool = True) -> str:
        if self._server is not None:
            return f"http://127.0.0.1:{self.port}/"
        self.port = int(port or DEFAULT_PORT)
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # quiet
                return

            def _json(self, code: int, obj: dict[str, Any]) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if not _guard.check(self, default=guard_default()):     # round 46: this machine only
                    return
                path = urlparse(self.path).path
                if path in ("/", "/index.html", "/coach"):
                    body = COACH_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    q = hub.subscribe()
                    try:
                        # hello
                        hello = json.dumps({"type": "line", "text": "Connected.", "cls": "muted"})
                        self.wfile.write(f"data: {hello}\n\n".encode())
                        self.wfile.flush()
                        while True:
                            try:
                                msg = q.get(timeout=15.0)
                            except queue.Empty:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                continue
                            self.wfile.write(f"data: {msg}\n\n".encode())
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        hub.unsubscribe(q)
                    return
                if path in ("/v1/sound", "/v1/sound/stream"):
                    # Same-origin for the enroll card. Oracle lobes live on
                    # VoiceOnly :8099 — Claude hit 404 on :8196 (2026-08-24).
                    up = os.environ.get("VO_HOME_URL", _ports.url("feed")).rstrip(
                        "/"
                    )
                    import urllib.request

                    if path == "/v1/sound":
                        try:
                            with urllib.request.urlopen(up + "/v1/sound", timeout=3) as r:
                                body = r.read()
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("Content-Length", str(len(body)))
                            self.end_headers()
                            self.wfile.write(body)
                        except Exception as e:
                            return self._json(
                                503, {"ok": False, "error": f"sound up: {e}"[:160]}
                            )
                        return
                    try:
                        up_r = urllib.request.urlopen(up + "/v1/sound/stream", timeout=30)
                    except Exception as e:
                        return self._json(
                            503, {"ok": False, "error": f"sound stream: {e}"[:160]}
                        )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    try:
                        while True:
                            chunk = up_r.readline()
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    finally:
                        try:
                            up_r.close()
                        except Exception:
                            pass
                    return
                if path == "/v1/auth":
                    # Erasing a bank is destructive and the User scoped it to the
                    # User, face-authenticated. Auth lives on VoiceOnly :8099,
                    # same cross-origin problem as the lobes — proxied the same
                    # way. Read-only: this returns the house's answer, it never
                    # decides one. (Claude 2026-08-24)
                    up = os.environ.get("VO_HOME_URL", _ports.url("feed")).rstrip("/")
                    import urllib.request

                    try:
                        with urllib.request.urlopen(up + "/v1/house", timeout=3) as r:
                            house = json.loads(r.read().decode("utf-8"))
                    except Exception as e:
                        return self._json(503, {"ok": False, "error": f"auth up: {e}"[:160]})
                    a = house.get("auth") or {}
                    reason = str(a.get("reason") or "")
                    return self._json(200, {
                        "ok": True,
                        "user_auth": bool(a.get("user_auth")),
                        "by_face": "face" in reason,
                        "reason": reason,
                        "tier": a.get("user_auth_tier"),
                    })
                if path == "/health":
                    return self._json(200, health_body())
                if path == "/session":
                    return self._json(200, hub.session_status())
                if path == "/people":
                    from .local_values import local

                    try:
                        from .site import load_site
                        from .roles import is_primary_user, user_id
                        from .person import bank_counts, display_name, list_people

                        site = load_site()
                        people = []
                        for p in list_people(site.root):
                            if not p.get("active", True):
                                continue
                            pid = p["id"]
                            bc = bank_counts(pid, site.root)
                            people.append(
                                {
                                    "id": pid,
                                    "display": p.get("display")
                                    or display_name(pid, site.root),
                                    "at_desk": is_primary_user(site, pid),
                                    "is_user": pid == user_id(site),
                                    "face": bc["face"],
                                    "voice": bc["voice"],
                                    "has_bank": bc["total"] > 0,
                                }
                            )
                        if not people:
                            people = [
                                {
                                    "id": user_id(site),
                                    "display": local("user_display", "the User"),
                                    "at_desk": True,
                                    "is_user": True,
                                    "face": 0,
                                    "voice": 0,
                                    "has_bank": False,
                                },
                            ]
                        return self._json(200, {"ok": True, "people": people})
                    except Exception as e:
                        return self._json(
                            200,
                            {
                                "ok": True,
                                "people": [
                                    {
                                        "id": local("user_id", "user"),
                                        "display": local("user_display", "the User"),
                                        "at_desk": True,
                                        "is_user": True,
                                        "face": 0,
                                        "voice": 0,
                                        "has_bank": False,
                                    },
                                ],
                                "note": str(e)[:80],
                            },
                        )
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                if not _guard.check(self, default=guard_default()):     # round 46: this machine only
                    return
                path = urlparse(self.path).path
                if _guard.guard_on(default=guard_default()):
                    n = _guard.body_length(self.headers, body_cap())
                    if n is None:                    # negative: rfile.read(-1) reads until the peer closes
                        return self._json(400, {"ok": False, "error": "bad Content-Length"})
                else:
                    n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if path == "/start":
                    try:
                        obj = json.loads(raw.decode() or "{}") if raw else {}
                    except Exception:
                        obj = {}
                    # Prefer free-text name → slug id; fallback roster person_id
                    display = obj.get("display") or obj.get("name") or ""
                    person = str(
                        obj.get("person_id")
                        or obj.get("id")
                        or display
                        or ""
                    ).strip()
                    if not person and not display:
                        return self._json(
                            400,
                            {"ok": False, "error": "enter a name (stored as that person id)"},
                        )
                    if display and not obj.get("person_id"):
                        from .person import format_person_id

                        person = format_person_id(display)
                    replace = bool(
                        obj.get("replace")
                        or obj.get("overwrite")
                        or obj.get("force")
                    )
                    return self._json(
                        200,
                        hub.start_enroll(
                            person,
                            display=str(display or person),
                            create=bool(obj.get("create", True)),
                            replace=replace,
                            mode=str(obj.get("mode") or "full"),
                            station=str(obj.get("station") or "desk"),
                            mic=str(obj.get("mic") or ""),
                            first_boot=bool(obj.get("first_boot")),
                            only=str(obj.get("only") or ""),
                            poses=str(obj.get("poses") or ""),
                            mouths=obj.get("mouths") if isinstance(obj.get("mouths"), dict) else None,
                            cue_voice=str(obj.get("cue_voice") or ""),
                            visitor=bool(obj.get("visitor")),
                        ),
                    )
                if path == "/warm":
                    # .101 round 30: `obj` was parsed only under /start, so every /warm raised UnboundLocalError and
                    # the caller read RemoteDisconnected (round 29's "not rendered ahead"): its own body here.
                    try:
                        obj = json.loads(raw.decode() or "{}") if raw else {}
                    except Exception:
                        obj = {}
                    return self._json(200, hub.warm_verbal(str(obj.get("voice") or "")[:20],
                                                           lines=obj.get("lines") if isinstance(obj.get("lines"), list) else None))
                if path == "/prearm":
                    # park the camera, close the ear, cue the listen loop
                    # BEFORE the press (the wall host's t=0 is the first
                    # line, not a slew); the routine adopts the marker
                    try:
                        obj = json.loads(raw.decode() or "{}") if raw else {}
                    except Exception:
                        obj = {}
                    return self._json(200, hub.prearm_register(station=str(obj.get("station") or "desk")))
                if path == "/cancel":
                    return self._json(200, hub.cancel_enroll())
                if path == "/emit":
                    # Cue relay from a panel-spawned session (ENROLL_UI_URL).
                    try:
                        obj = json.loads(raw.decode() or "{}") if raw else {}
                    except Exception:
                        obj = {}
                    if isinstance(obj, dict) and obj:
                        hub.publish(obj)
                    return self._json(200, {"ok": True})
                self.send_error(404)

        # bind
        last_err = None
        for p in range(self.port, self.port + 5):
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
                self.port = p
                break
            except OSError as e:
                last_err = e
                self._server = None
        if self._server is None:
            raise RuntimeError(f"coach bind failed: {last_err}")

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        _verbal_warm_later()
        url = f"http://127.0.0.1:{self.port}/"
        print(f"  coach display → {url}", flush=True)
        if open_browser and os.environ.get("HOUSE_ENROLL_NO_BROWSER") != "1":
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return url

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None


# process-wide hub for one enroll session
_hub: CoachHub | None = None


def get_hub() -> CoachHub:
    global _hub
    if _hub is None:
        _hub = CoachHub()
    return _hub


_remote_url: str | None = None


def coach_start(open_browser: bool = True) -> str:
    # Spawned by the panel? Stream cues to it instead of binding our own
    # server — the panel IS the screen (ENROLL_UI_URL, set in start_enroll).
    global _remote_url
    url = os.environ.get("ENROLL_UI_URL")
    if url:
        _remote_url = url.rstrip("/")
        return _remote_url + "/"
    return get_hub().start(open_browser=open_browser)


def coach_emit(**event: Any) -> None:
    if _remote_url:
        import urllib.request

        try:
            req = urllib.request.Request(
                _remote_url + "/emit",
                data=json.dumps(event).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2.0).read()
        except Exception:
            pass
        return
    get_hub().publish(event)


def coach_stop() -> None:
    global _hub, _remote_url
    if _remote_url:
        _remote_url = None
        return
    if _hub is not None:
        _hub.stop()
        _hub = None
