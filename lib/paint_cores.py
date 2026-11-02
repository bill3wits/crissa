"""Her paint gets cores of its own (the User 2026-10-13/14, "it is maybe the most important thing ... try two cores",
"18 done now please build it").

Measured on the house (Ryzen 9950X, 16 cores / 32 threads): on a quiet machine her close-up mouth paint is ~14 ms of a
20.8 ms semiframe; with the ear's model on ~25 threads (load 15) the same paint ran 23-46 ms and a third of her mouth
rows were skipped, though the conductor already carried CPUWeight 10000 against 200-400. Weight decides who waits for
a free thread; it cannot stop a busy thread sharing her core (its SMT sibling) or the cache. While she reads, the
paint, the Wall and the Wall's film process take ~181 % of one thread on the desk shot and ~230 % on the close-up -
more than one physical core gives (two busy threads on a core do ~130 % of one), so a 16-core machine reserves two.

How (Linux; no root, no cgroup cpuset): every lane runs on the REST of the machine (lib/crissa_service writes it into
each desk unit as CPUAffinity=, process mode sets it before exec; the house does the same with the user manager's
CPUAffinity, scripts/paint-cores.py), and the paint moves itself onto its reserved core: the conductor's paint thread,
the Wall's drawing thread and its film process (pin_thread). sched_setaffinity on the calling thread is allowed
outside the parent's mask, and threads it creates afterwards inherit it.

How many: HOUSE_PAINT_CORES = auto (default in a pack) | 0/off (default on the house until the restart window) | 1 |
2. auto: two physical cores from 8 up (round 23, was 12: .101's 8 cores painted in 7 ms on two, 22 on one; the
player's, the Wall's), one from 6 (the player's; the Wall stays on the
rest), none below 6. The reserved cores are the highest-numbered ones (the second cache group on a two-group chip).

macOS cannot pin a thread (an affinity tag is a hint Apple Silicon ignores): the same calls set the paint threads'
QoS to user-interactive, which keeps them on the performance cores; nothing else changes there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PAINT_LANES = ("player", "wall")
_SYS = Path("/sys/devices/system/cpu")
_DONE: dict = {}


def mode(env=None) -> str:
    """'auto' | 'off' | '1' | '2' from HOUSE_PAINT_CORES; unset = auto in a pack, off on the house."""
    e = os.environ if env is None else env
    raw = str(e.get("HOUSE_PAINT_CORES", "")).strip().lower()
    if raw in ("0", "off", "false", "no"):
        return "off"
    if raw in ("1", "2", "auto"):
        return raw
    if raw in ("on", "true", "yes"):
        return "auto"
    try:
        import crissa_home
        return "off" if crissa_home.is_house() else "auto"
    except Exception:  # noqa: BLE001 - no state home to ask: leave the machine as it is
        return "off"


def physical_cores(root: Path | None = None) -> list[tuple[int, ...]]:
    """Each physical core as the sorted logical CPUs it carries (its SMT threads), online CPUs only, ordered by its
    first CPU. Empty when the topology cannot be read (not Linux)."""
    root = _SYS if root is None else Path(root)
    cores = set()
    try:
        for d in sorted(root.glob("cpu[0-9]*")):
            sib = (d / "topology" / "thread_siblings_list")     # an offline CPU has no topology folder
            if not sib.exists():
                continue
            cores.add(tuple(sorted(_parse_list(sib.read_text()))))
    except (OSError, ValueError):
        return []
    return sorted(cores)


def _parse_list(s: str) -> set[int]:
    out: set[int] = set()
    for part in s.strip().split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def plan(env=None, cores: list | None = None) -> dict | None:
    """{"player": cpus, "wall": cpus | None, "rest": cpus, "reserved": n} on Linux, {"qos": True} on macOS when on,
    else None (off, or a machine too small to give a core away)."""
    m = mode(env)
    if m == "off":
        return None
    if sys.platform == "darwin" and cores is None:
        return {"qos": True}
    cores = physical_cores() if cores is None else [tuple(c) for c in cores]
    n = len(cores)
    if m == "auto":
        k = 2 if n >= 8 else (1 if n >= 6 else 0)      # round 23: 8 up (.101's 8 cores: paint 22 -> 7 ms)
    else:
        k = int(m) if n >= int(m) + 2 else 0
    if k <= 0:
        return None
    every = set().union(*map(set, cores))
    player = set(cores[-k])
    wall = set(cores[-1]) if k >= 2 else None
    rest = every - player - (wall or set())
    return {"player": player, "wall": wall, "rest": rest, "reserved": k}


def _fmt(cpus) -> str:
    return " ".join(str(c) for c in sorted(cpus))


def rest_cpus(env=None) -> set | None:
    p = plan(env)
    return None if p is None or "rest" not in p else set(p["rest"])


def unit_line(name: str, env=None) -> str:
    """The desk unit's CPUAffinity= line (every lane, the paint lanes too: only their paint threads move), or ''."""
    r = rest_cpus(env)
    return ("CPUAffinity=%s\n" % _fmt(r)) if r else ""


def preexec_rest(env=None):
    """A preexec_fn for a process-mode child: start it on the rest of the machine. None when there is nothing to do."""
    r = rest_cpus(env)
    if not r:
        return None

    def _set():
        try:
            os.sched_setaffinity(0, r)
        except OSError:
            pass
    return _set


def pin_thread(role: str, log=print, env=None) -> str:
    """Move the CALLING thread onto its role's reserved core ('player' | 'wall'); macOS: user-interactive QoS. Returns
    what it did ('' = nothing). Threads this one starts afterwards inherit it. Never raises."""
    try:
        p = plan(env)
        if p is None:
            return ""
        if p.get("qos"):
            got = _qos_interactive()
            msg = "qos user-interactive" if got else ""
        else:
            cpus = p.get(role)
            if not cpus:
                return ""
            os.sched_setaffinity(0, cpus)
            msg = "cpus %s (reserved %d of %d physical cores)" % (_fmt(cpus), p["reserved"], len(physical_cores()))
        if msg and log is not None and _DONE.get(role) != msg:
            _DONE[role] = msg
            log("[paint-cores] %s: %s" % (role, msg))
        return msg
    except Exception:  # noqa: BLE001 - a core is a help, never a reason to stop painting
        return ""


def _qos_interactive() -> bool:
    """pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0) on macOS."""
    try:
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        return int(lib.pthread_set_qos_class_self_np(ctypes.c_uint(0x21), ctypes.c_int(0))) == 0
    except Exception:  # noqa: BLE001
        return False


def state(env=None) -> dict:
    p = plan(env)
    if p is None:
        return {"mode": mode(env), "on": False}
    if p.get("qos"):
        return {"mode": mode(env), "on": True, "qos": True}
    out = {"mode": mode(env), "on": True, "reserved": p["reserved"], "player": sorted(p["player"]),
           "wall": sorted(p["wall"]) if p["wall"] else None, "rest": len(p["rest"])}
    if not p["wall"]:
        # round 20 (.101 round 19 read `wall None` as a miss): by design below 8 physical cores (12 before round 23)
        out["wall_on"] = "rest"
        out["wall_why"] = "one core is reserved below 8 physical cores, and it is the player's paint"
    return out
