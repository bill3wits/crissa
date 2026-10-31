#!/usr/bin/env python3
"""The Crissa presence service, started by the launcher (2026-10-09). Loopback only.

Two shapes, one port (the port table's `presence`, never the house service's):

* the presence MODULE (modules/presence, the house's rail extracted: door watch, Secure and Guest, visitors,
  registration tokens, the room sweep) when it is installed (`modules/presence/scripts/crissa-presence.py --install`
  wrote <state home>/presence/install.json) - this script hands over to its own script, which re-runs itself in the
  module's env;
* else the stdlib lamp (lib/crissa_presence): Secure, Guest and the owner's enrollment, no camera of its own. Its one
  look (`_look`) is the module's camera path when the module's packages import here, else none - a weak machine with
  nothing installed still starts, and the routes answer the same shapes either way.

A start clears Secure and then uses a saved token, unless a test is running.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_presence as presence  # noqa: E402

MODULE = ROOT / "modules" / "presence"


def module_installed(home: Path) -> bool:
    return (MODULE / "scripts" / "crissa-presence.py").is_file() and (home / "presence" / "install.json").is_file()


def camera_look(home: Path):
    """The lamp's look, from the module's camera path: one frame from the grabber (CRISSA_CAMERA_URL / RAIL_CAMERA_URL),
    the module's face engine, the owner's numbers in <home>/enroll.json. None when the module cannot import here."""
    try:
        sys.path.insert(0, str(MODULE))
        import numpy as np  # noqa: F401
        from crissa_presence import enroll_store, face_engine  # noqa: F401  (the module's package shadows lib's name)
    except Exception:  # noqa: BLE001
        return None
    finally:
        # lib/crissa_presence stays the lamp; the module's package is reached by its path below
        sys.path.remove(str(MODULE))

    def look(source: str) -> dict:
        import json
        import urllib.request
        base = (os.environ.get("CRISSA_CAMERA_URL") or os.environ.get("RAIL_CAMERA_URL") or "").rstrip("/")
        if str(os.environ.get("CRISSA_NO_CAMERA") or "").strip().lower() in ("1", "true", "yes", "on"):
            return {"found": False, "skipped": "no_camera", "camera": "off"}     # --no-camera: never asked (2026-10-10)
        if not base:
            return {"found": False, "skipped": "no_camera"}
        try:
            import cv2
            import numpy as np
            sys.path.insert(0, str(MODULE))
            import importlib
            fe = importlib.import_module("crissa_presence.face_engine")
            es = importlib.import_module("crissa_presence.enroll_store")
            with urllib.request.urlopen(base + "/snapshot", timeout=3.0) as r:
                img = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return {"found": False, "skipped": "no_frame"}
            eng = fe.get_face_engine()
            if not eng.ready:
                return {"found": False, "skipped": "error", "why": eng.why}
            faces = eng.detect(img)
            if not faces:
                return {"found": False, "skipped": "no_face"}
            doc = es.read_enroll(home / "enroll.json")
            owner = str(doc.get("owner") or "").strip().lower()
            refs = [np.asarray(v, dtype=np.float32) for v in (doc.get("faces") or []) if isinstance(v, list)]
            best = -1.0
            for f in faces:
                e = fe.FaceEngine.embed(f)
                if e is None:
                    continue
                for ref in refs:
                    n = float(np.linalg.norm(ref))
                    if n > 0 and ref.size == e.size:
                        best = max(best, float(np.dot(e, ref / n)))
            matched = bool(owner) and best >= presence.MATCH
            return {"found": matched, "uid": owner if matched else "", "sim": round(best, 4), "n_faces": len(faces)}
        except Exception as exc:  # noqa: BLE001
            return {"found": False, "skipped": "error", "why": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
        finally:
            if str(MODULE) in sys.path:
                sys.path.remove(str(MODULE))
    return look


def cap_cpus(env=None, getaff=None, setaff=None) -> list | None:
    """PRESENCE KEEPS TO A FEW CORES (the User 2026-10-10, .101 round 3 AA: idle, nobody at the camera, presence ran at
    631 % - seven face-landmarker threads at 80-90 % - and his daily laptop felt slow). The door watch now rests on an
    empty room (modules/presence idle_gate); this bounds the rest of the time too: before anything starts a thread, the
    process (and the module's service it execs into, which inherits it) keeps to the last CRISSA_PRESENCE_CPUS (2) of
    the CPUs it may use, so the landmarker's thread pool and the recognizer share two cores however busy the door is.
    0 is no cap; a machine with no more CPUs than that, or no affinity call (macOS), is left as it is. Returns the CPUs
    kept, else None."""
    env = os.environ if env is None else env
    try:
        n = int(str(env.get("CRISSA_PRESENCE_CPUS", "2")).strip() or "2")
    except ValueError:
        n = 2
    getaff = getaff or getattr(os, "sched_getaffinity", None)
    setaff = setaff or getattr(os, "sched_setaffinity", None)
    if n <= 0 or getaff is None or setaff is None:
        return None
    try:
        cur = sorted(getaff(0))
        if len(cur) <= n:
            return None
        keep = whole_cores(cur, n, siblings) if pair_on(env) else cur[-n:]
        setaff(0, keep)
        return keep
    except OSError:
        return None


# ONE PHYSICAL CORE, NOT TWO HALVES (round 15, 2026-10-12, .101: an 8-core / 16-thread laptop where CPU n and n+8 are one
# core's two hyperthreads). "The last two CPUs" were 14 and 15 - the second hyperthreads of cores 6 and 7 - so presence's
# two busy threads slowed two physical cores, either of which the player's or the Wall's one busy thread could land on.
# Now the cap takes whole cores from the top: both hyperthreads of the last core (7 and 15), then the next core's, so
# presence uses n hyperthreads on n/2 physical cores and leaves the rest whole. A machine without hyperthreads (or no
# sibling list) keeps the last n CPUs as before. Kill: CRISSA_PRESENCE_CPU_PAIRS=0.
def pair_on(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PRESENCE_CPU_PAIRS", "1")).strip().lower() not in ("0", "off", "false", "no")


def siblings(cpu: int) -> list:
    """The CPUs sharing `cpu`'s physical core (Linux sysfs), or [cpu]."""
    try:
        txt = Path("/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list" % int(cpu)).read_text().strip()
    except OSError:
        return [int(cpu)]
    out = []
    for part in txt.split(","):
        a, _, b = part.partition("-")
        try:
            out.extend(range(int(a), int(b or a) + 1))
        except ValueError:
            return [int(cpu)]
    return sorted(set(out)) or [int(cpu)]


def whole_cores(allowed: list, n: int, sib=siblings) -> list:
    """n CPUs from `allowed`, whole physical cores first, starting from the highest CPU."""
    keep: list = []
    for c in sorted(allowed, reverse=True):
        if c in keep:
            continue
        core = [x for x in sib(c) if x in allowed and x not in keep]
        for x in sorted(core, reverse=True):
            if len(keep) < n:
                keep.append(x)
        if len(keep) >= n:
            break
    return sorted(keep)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crissa presence")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--home", default="")
    ap.add_argument("--lamp", action="store_true", help="the stdlib lamp even when the presence module is installed")
    args = ap.parse_args(argv)
    if "PYTEST_CURRENT_TEST" not in os.environ:
        kept = cap_cpus()
        if kept:
            print("presence keeps to CPUs %s (CRISSA_PRESENCE_CPUS, 0 = no cap)" % ",".join(map(str, kept)), flush=True)
    if args.home:
        home = Path(args.home)
    else:
        import crissa_home
        home = crissa_home.HOME
    port_n = presence.port() if args.port is None else int(args.port)
    if not args.lamp and "PYTEST_CURRENT_TEST" not in os.environ and module_installed(home):
        script = MODULE / "scripts" / "crissa-presence.py"
        os.environ.setdefault("CRISSA_HOME", str(home))
        os.execv(sys.executable, [sys.executable, str(script), "--port", str(port_n)])
    svc = presence.Service(home, look=camera_look(home))
    if "PYTEST_CURRENT_TEST" not in os.environ:
        svc.boot()
    httpd = presence.listen(svc, port_n)
    bound = httpd.server_address[1]
    print("presence on 127.0.0.1:%d (%s)" % (bound, "lamp; the presence module is not installed" if not module_installed(home) else "lamp"), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
