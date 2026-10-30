"""Comfy and the card: the GPU mode flag, free VRAM, Comfy up / busy, start / stop (2026-10-09).

Cut from the house's mode switch (scripts/house-mode: free_vram_gib, read_mode, comfy_up, comfy_busy and its
unknown clock, live_comfy_pids, remember_comfy_pid, start_comfy, stop_comfy, _kill_pid, _pids_listening_port,
_is_house_comfy), the same functions line for line where they are about Comfy. What did NOT come across is the
house's own business: parking and waking its two LLM engines and its agent gateways, the card holds, the dispatch
caps and the window gate. A pack has none of those processes, so its three modes are:

    render   the flag says render; Comfy is started (the user's own install, from render.json)
    normal   the flag says normal; Comfy is stopped
    off      the flag says off; Comfy is stopped (nothing else of the user's is touched)

The flag file is <state>/render/gpu-mode (one word), read by the feed's Render chips and lib/render_wip.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import settings

MODES = settings.MODES


def _state(cfg: dict | None) -> Path:
    return Path((cfg or settings.load())["state"])


def mode_file(cfg: dict | None = None) -> Path:
    return Path(os.environ.get("HOUSE_GPU_MODE_FILE") or (_state(cfg) / "gpu-mode"))


def pid_file(cfg: dict | None = None) -> Path:
    return _state(cfg) / "comfy.pid"


def unknown_state(cfg: dict | None = None) -> Path:
    return _state(cfg) / "comfy-unknown-since.json"


# ---- the flag -------------------------------------------------------------------------------------------------
def read_mode(text: Optional[str] = None, cfg: dict | None = None) -> str:
    if text is None:
        f = mode_file(cfg)
        if not f.is_file():
            return "normal"
        text = f.read_text(encoding="utf-8")
    first = text.strip().splitlines()[0].strip().lower() if text.strip() else ""
    if first in MODES:
        return first
    try:
        mode = str(json.loads(text).get("mode") or "").strip().lower()
    except json.JSONDecodeError:
        return "normal"
    return mode if mode in MODES else "normal"


def write_mode(mode: str, cfg: dict | None = None) -> bool:
    """Write the flag; True when it changed. The mtime is when this Render engaged (render_wip.render_since)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; want one of {MODES}")
    f = mode_file(cfg)
    if f.is_file() and read_mode(cfg=cfg) == mode:
        return False
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(mode + "\n", encoding="utf-8")
    os.replace(tmp, f)
    return True


# ---- the card -------------------------------------------------------------------------------------------------
def free_vram_gib(smi_text: Optional[str] = None) -> Optional[float]:
    """Parse nvidia-smi free MiB -> GiB. None if unread."""
    if smi_text is None:
        try:
            smi_text = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                text=True, timeout=5,
            )
        except Exception:  # noqa: BLE001
            return None
    try:
        line = smi_text.strip().splitlines()[0].strip()
        return float(line) / 1024.0
    except (IndexError, ValueError):
        return None


# ---- Comfy ----------------------------------------------------------------------------------------------------
def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def comfy_url(cfg: dict | None = None) -> str:
    return (cfg or settings.load())["comfy_url"]


def comfy_up(cfg: dict | None = None) -> bool:
    return http_ok(f"{comfy_url(cfg)}/system_stats")


# How long "unknown" may keep the flag in Render. Unreadable /queue means BUSY, and that is right for a blip, but
# it must not be right forever, or a Comfy that is alive on the socket and too sick to answer is a permanent latch.
UNKNOWN_MAX_S = float(os.environ.get("HOUSE_COMFY_UNKNOWN_MAX_S", "900") or 900)


def _unknown_expired(cfg: dict | None, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    f = unknown_state(cfg)
    try:
        since = float(json.loads(f.read_text()).get("since") or 0)
    except (OSError, ValueError, AttributeError):
        since = 0.0
    if not since:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"since": now}) + "\n")
        except OSError as e:
            print(f"comfy_busy: cannot record the unknown clock ({type(e).__name__}) -> staying busy")
        return False
    return (now - since) > UNKNOWN_MAX_S


def _unknown_clear(cfg: dict | None) -> None:
    try:
        unknown_state(cfg).unlink()
    except OSError:
        pass


def comfy_busy(cfg: dict | None = None) -> bool:
    """Is Comfy actually working (running or pending queue entries)? False when it is down; True while /queue
    cannot be read, but only for UNKNOWN_MAX_S."""
    cfg = cfg or settings.load()
    if not comfy_up(cfg):
        _unknown_clear(cfg)
        return False
    try:
        with urllib.request.urlopen(f"{comfy_url(cfg)}/queue", timeout=2.0) as r:
            if not (200 <= r.status < 300):
                return not _unknown_expired(cfg)
            q = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        if _unknown_expired(cfg):
            print(f"comfy_busy: /queue unreadable ({type(e).__name__}) for more than {int(UNKNOWN_MAX_S)}s "
                  "-> no longer treating as busy")
            return False
        print(f"comfy_busy: /queue unreadable ({type(e).__name__}) -> treating as busy")
        return True
    _unknown_clear(cfg)
    try:
        return bool(q.get("queue_running")) or bool(q.get("queue_pending"))
    except AttributeError:
        return True


def comfy_main(cfg: dict) -> str:
    return str(Path(cfg["comfy_dir"]) / "main.py") if cfg.get("comfy_dir") else ""


def _comfy_port(cfg: dict) -> int:
    tail = comfy_url(cfg).rsplit(":", 1)[-1]
    try:
        return int(tail.split("/")[0])
    except ValueError:
        return settings.P.port("comfy")


def _proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _is_our_comfy(pid: int, main: str, cmdline: Optional[str] = None) -> bool:
    """True for the python Comfy from the configured folder, not a shell that mentions the path."""
    if pid <= 1 or not main:
        return False
    cmd = cmdline if cmdline is not None else _proc_cmdline(pid)
    if main not in cmd:
        return False
    first = (cmd.split() or [""])[0]
    return Path(first).name.startswith("python")


def _pids_listening_port(port: int) -> list[int]:
    hexport = f"{port:04X}"
    inodes: set[str] = set()
    for tcp in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            text = Path(tcp).read_text()
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            if parts[1].rsplit(":", 1)[-1].upper() != hexport:
                continue
            inodes.add(parts[9])
    if not inodes:
        return []
    found: list[int] = []
    try:
        dirs = list(Path("/proc").iterdir())
    except OSError:
        return []
    for d in dirs:
        if not d.name.isdigit():
            continue
        try:
            for fd in (d / "fd").iterdir():
                try:
                    tgt = os.readlink(fd)
                except OSError:
                    continue
                if tgt.startswith("socket:[") and tgt[8:-1] in inodes:
                    found.append(int(d.name))
                    break
        except OSError:
            continue
    return found


def live_comfy_pids(cfg: dict | None = None) -> list[int]:
    """Our Comfy's PIDs: pid file, then pgrep main.py, then the port's listener."""
    cfg = cfg or settings.load()
    main = comfy_main(cfg)
    seen: list[int] = []

    def add(pid: int) -> None:
        if pid > 1 and pid not in seen and _is_our_comfy(pid, main):
            seen.append(pid)

    pf = pid_file(cfg)
    if pf.is_file():
        try:
            add(int(pf.read_text().strip().splitlines()[0]))
        except (ValueError, OSError, IndexError):
            pass
    if main:
        try:
            proc = subprocess.run(["pgrep", "-f", main], capture_output=True, text=True, timeout=5)
            for line in proc.stdout.splitlines():
                try:
                    add(int(line.strip()))
                except ValueError:
                    continue
        except (OSError, subprocess.TimeoutExpired):
            pass
    for pid in _pids_listening_port(_comfy_port(cfg)):
        add(pid)
    return seen


def remember_comfy_pid(pid: int, cfg: dict | None = None) -> None:
    if pid <= 1:
        return
    pf = pid_file(cfg)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid) + "\n", encoding="utf-8")


def start_comfy(cfg: dict | None = None, *, highvram: bool | None = None) -> int:
    """Launch the user's Comfy from render.json; a Comfy already up is reused as it is. Returns the pid (0 when
    one was up that this module did not start). Raises FileNotFoundError when no Comfy is configured."""
    cfg = cfg or settings.load()
    if comfy_up(cfg):
        pids = live_comfy_pids(cfg)
        if pids:
            remember_comfy_pid(pids[0], cfg)
            return pids[0]
        return 0
    main = comfy_main(cfg)
    if not main or not Path(main).is_file():
        raise FileNotFoundError("no Comfy configured: set comfy_dir in %s" % cfg["file"])
    env = os.environ.copy()
    # Comfy is its own Python: a path or env the launcher carries must not reach it (2026-10-08: an inherited
    # PYTHONPATH into another tree killed it on pydantic_core).
    for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    env["PYTORCH_CUDA_ALLOC_CONF"] = env.get("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    log = Path(os.environ.get("CRISSA_COMFY_LOG") or (Path(cfg["state"]) / "comfy.log"))
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("ab")
    py = cfg.get("comfy_python") or shutil.which("python3") or "python3"
    cmd = [py, main, "--port", str(_comfy_port(cfg)), "--listen", "127.0.0.1"]
    if cfg.get("highvram") if highvram is None else highvram:
        cmd.append("--highvram")
    # ITS OWN SCOPE where systemd is there: the feed's Render key runs this from inside the feed process, and a
    # restart of the feed must not take Comfy with it. systemd-run --scope execs in place: same pid, own cgroup.
    if os.environ.get("HOUSE_COMFY_SCOPE", "1") != "0" and shutil.which("systemd-run"):
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
               "--unit", f"crissa-comfy-{int(time.time())}"] + cmd
    proc = subprocess.Popen(cmd, cwd=cfg["comfy_dir"], env=env, stdout=fh, stderr=fh, start_new_session=True)
    remember_comfy_pid(proc.pid, cfg)
    return proc.pid


def _kill_pid(pid: int) -> None:
    if pid <= 1:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except (PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def stop_comfy(cfg: dict | None = None) -> bool:
    """SIGKILL our Comfy. True when its port is down. The pid file goes only then."""
    cfg = cfg or settings.load()
    for pid in live_comfy_pids(cfg):
        _kill_pid(pid)
    time.sleep(float(os.environ.get("HOUSE_COMFY_KILL_SETTLE_SEC", "3")))
    deadline = time.time() + 45
    while time.time() < deadline and comfy_up(cfg):
        for pid in live_comfy_pids(cfg):
            _kill_pid(pid)
        time.sleep(1)
    if comfy_up(cfg):
        return False
    pid_file(cfg).unlink(missing_ok=True)
    return True


def wait_http(url: str, timeout: float = 600.0, label: str = "", log=print) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_ok(url):
            return True
        log(f"waiting {label or url} ({int(deadline - time.time())}s left)")
        time.sleep(2)
    return False


# ---- the three modes ----------------------------------------------------------------------------------------
def status(cfg: dict | None = None) -> dict:
    cfg = cfg or settings.load()
    up = comfy_up(cfg)
    return {"mode": read_mode(cfg=cfg), "comfy": up, "comfy_busy": comfy_busy(cfg) if up else False,
            "free_vram_gib": free_vram_gib(), "pids": live_comfy_pids(cfg), "mark": settings.mark(cfg, up=up),
            "flag_file": str(mode_file(cfg))}


def enter_render(cfg: dict | None = None, *, highvram: bool | None = None, wait_s: float = 300.0, log=print) -> int:
    """Flag render, start Comfy, wait for it. 0 ok; 2 no Comfy configured; 4 Comfy did not answer."""
    cfg = cfg or settings.load()
    write_mode("render", cfg)
    try:
        pid = start_comfy(cfg, highvram=highvram)
    except FileNotFoundError as e:
        log(str(e))
        write_mode("normal", cfg)
        return 2
    log(f"comfy pid {pid or '(already up)'}; waiting for {comfy_url(cfg)}")
    if not wait_http(f"{comfy_url(cfg)}/system_stats", wait_s, "Comfy", log=log):
        log("Comfy did not come up; the flag stays render so the stage shows the log")
        return 4
    return 0


def leave_render(cfg: dict | None = None, *, to: str = "normal", log=print) -> int:
    cfg = cfg or settings.load()
    ok = stop_comfy(cfg)
    write_mode(to, cfg)
    log(f"comfy {'stopped' if ok else 'STILL UP'}; flag {to}")
    return 0 if ok else 4
