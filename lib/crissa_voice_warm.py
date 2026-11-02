"""Keep-warm for the active GPU voice (2026-10-06, the User): Turbo, or Multilingual V3 when Init chose it.

A short warm-up line goes to the voice's /stream on start and every `interval_s` (the tier table's keep_warm block,
not hidden), and its audio to the aligner when one runs, so the first line after an idle spell does not pay the cold
model and aligner cost (about 2.9 s cold against 1.0 s warm, measured 2026-10-06 on the reference house). Nothing is
played: the audio is thrown away.

Default ON for the GPU tiers; off elsewhere. Every warm is kept in <state home>/voice-warm.json (last warm, how long it
took, ok or why not) and logged to <state home>/voice-warm.log, which the Control Panel's Keep-warm tile shows, with
a Run now key.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

GPU_VOICES = ("turbo", "multilingual")
# POCKET KEPT WARM TOO (round 28, .101 2026-10-17: "the first packet pauses ... We were trying a keep warm" - on Pocket
# the keep-warm said "nothing to warm" and her first line after a quiet spell paid the cold voice and aligner). on:
# "all" warms whatever streamed voice is hers (Pocket on a CPU machine, about a second of its CPU every interval_s);
# "gpu" is the GPU voices only (rounds 22-27). The kit's tier table says "all".
STREAMED_VOICES = GPU_VOICES + ("pocket",)
DEFAULTS = {"on": "gpu", "interval_s": 600, "line": "Mm, okay.", "face": "crissa", "aligner": True, "timeout_s": 60}


def _home(home: Path | None = None) -> Path:
    try:
        import crissa_voice_tiers as VT
        return VT._home(home)
    except Exception:  # noqa: BLE001
        return Path(home or os.environ.get("CRISSA_HOME") or (Path.home() / ".crissa")).expanduser()


def state_path(home: Path | None = None) -> Path:
    return _home(home) / "voice-warm.json"


def log_path(home: Path | None = None) -> Path:
    return _home(home) / "voice-warm.log"


def settings(home: Path | None = None) -> dict:
    try:
        import crissa_voice_tiers as VT
        tab = VT.table(home)
        return dict(DEFAULTS, **(tab.get("keep_warm") or {}))
    except Exception:  # noqa: BLE001
        return dict(DEFAULTS)


def target(home: Path | None = None) -> tuple[str, str]:
    """(engine, '') for the active GPU voice, or ('', why there is nothing to warm)."""
    st = settings(home)
    try:
        import crissa_voice_tiers as VT
        rec = VT.read(home) or {}
    except Exception:  # noqa: BLE001
        rec = {}
    main = str(rec.get("main") or "")
    on = st.get("on")
    if on is False or str(on).lower() in ("off", "0", "false", "no"):
        return "", "switched off (keep_warm.on is off in the tier table)"
    warms = STREAMED_VOICES if str(on).lower() == "all" else GPU_VOICES
    if main not in warms:
        return "", "Crissa's voice here is %s, not one kept warm (keep_warm.on %s): nothing to warm" % (
            main or "not picked yet", on)
    return main, ""


def state(home: Path | None = None) -> dict:
    try:
        return json.loads(state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _log(home: Path | None, **kv) -> None:
    kv = dict({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, **kv)
    try:
        with open(log_path(home), "a", encoding="utf-8") as f:
            f.write(json.dumps(kv) + "\n")
    except OSError:
        pass


def warm_once(home: Path | None = None, post=None) -> dict:
    """One warm-up: the line through the GPU voice's /stream (audio thrown away), then the aligner when it runs.
    Never raises; the result is kept and logged. post(url, body, timeout) -> (bytes, headers) is for tests."""
    st = settings(home)
    engine, why = target(home)
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engine": engine, "ok": False}
    if not engine:
        out.update(skipped=True, why=why)
    else:
        try:
            import crissa_tts as T
            url = T.stream_url(engine)
            body = json.dumps({"text": st["line"], "voice": st["face"]}).encode()
            t0 = time.monotonic()
            pcm, hdr = (post or _post)(url, body, float(st["timeout_s"]))
            out["voice_s"] = round(time.monotonic() - t0, 2)
            if len(pcm) < 2400:
                raise RuntimeError("the voice answered with %d bytes, not a line" % len(pcm))
            out["ok"] = True
            if st.get("aligner"):
                out.update(_warm_aligner(pcm, int(hdr.get("X-Sample-Rate") or 24000), st, post))
        except Exception as e:  # noqa: BLE001
            out["why"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    prev = state(home)
    rec = dict(prev, last=out, warms=int(prev.get("warms") or 0) + (1 if out["ok"] else 0),
               fails=int(prev.get("fails") or 0) + (0 if out["ok"] or out.get("skipped") else 1))
    if out["ok"]:
        rec["last_ok"] = out["at"]
    try:
        state_path(home).parent.mkdir(parents=True, exist_ok=True)
        state_path(home).write_text(json.dumps(rec) + "\n")
    except OSError:
        pass
    _log(home, **{k: v for k, v in out.items() if k != "at"})
    return out


def _post(url: str, body: bytes, timeout: float):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def _warm_aligner(pcm: bytes, sr: int, st: dict, post=None) -> dict:
    try:
        import crissa_aligner as A
        if not A.running():
            return {"aligner": "not running"}
        url = A.line_env().get("HOUSE_ALIGN_URL") or ""
    except Exception:  # noqa: BLE001
        return {"aligner": "none in this pack"}
    if not url:
        return {"aligner": "none"}
    t0 = time.monotonic()
    try:
        body = json.dumps({"text": st["line"], "sr": sr, "pcm16_b64": base64.b64encode(pcm).decode("ascii")}).encode()
        (post or _post)(url, body, 15.0)
        return {"aligner": "warm", "aligner_s": round(time.monotonic() - t0, 2)}
    except Exception as e:  # noqa: BLE001
        return {"aligner": "failed: %s" % str(e)[:120]}


def view(home: Path | None = None) -> dict:
    """The Control Panel's Keep-warm tile."""
    st = settings(home)
    engine, why = target(home)
    s = state(home)
    last = s.get("last") or {}
    tile = {"id": "warm", "label": "Keep-warm", "engine": engine, "interval_s": st.get("interval_s"),
            "last_ok": s.get("last_ok") or "", "last": last, "actions": ["run", "log"]}
    if not engine:
        tile.update(state="off", mark="○", note="off - " + why)
    elif last and not last.get("ok") and not last.get("skipped"):
        tile.update(state="bad", mark="◐", note="last warm failed: %s" % last.get("why"))
    else:
        tile.update(state="ok" if s.get("last_ok") else "off", mark="●" if s.get("last_ok") else "○",
                    note="on - %s every %ss; last warm %s%s" % (engine, st.get("interval_s"), s.get("last_ok") or "never",
                                                             (" (%.1f s)" % last["voice_s"]) if last.get("voice_s") else ""))
    return tile


def loop(home: Path | None = None, stop=None) -> None:
    """Warm on start, then every interval_s (read each round, so a change in the table takes effect)."""
    while True:
        warm_once(home)
        wait = max(30, int(settings(home).get("interval_s") or 600))
        for _ in range(wait):
            if stop is not None and stop():
                return
            time.sleep(1)
