"""Control Panel logs: every cut says so, every page has the one before it, and the whole log is one click away.

The User 2026-10-06, after the CP wiring audit: "Logs get paging and an explicit 'earlier lines not shown'
marker on every cut (including the 8192-char line cut and the agent profile log), plus a whole-log route
... No hidden caps, nothing secret in logs shown (keys masked)."

What was there before: a tile's tape was the last 100 journal lines and the last 40 lines of each log file the
unit writes, every line cut at 8192 characters, the profile log read from at most its last 4 MiB, and nothing
said any of it had been cut or offered the rest.

- `journal_page(units, n, before)`: the newest `n` entries of one or more (unit, scope) journals, read
  newest-first with one entry extra so the page KNOWS whether more is above. When there is, the first line is
  a marker and `earlier` is a token that reads the page before (one journald cursor per scope). `n=None` reads
  everything: there is no upper cap.
- `file_page(path, n, before)`: the same for a file, by byte offset, read backwards from the end (a 4 GB log
  costs what its last page costs). The marker says how much is above.
- `cut_line(line)`: a line longer than LINE_MAX keeps its head and says how many characters are not shown;
  the whole log has the line entire.
- `mask(text)`: API keys, tokens, passwords and bearer headers become `[masked]` in everything shown,
  the whole log included.
- `whole_journal(units)` / `whole_file(path)`: the entire log as masked text lines, streamed by the server.
- `pick_reason(lines)`, `failure_reason(unit, scope, kv, run)`: a down unit's reason in words - systemd's
  Result and exit status, and the journal line that names the trouble.

Pure stdlib; it only reads. The house feed reaches it through lib/panel_wiring (absent-ok); since 2026-10-28 the kit's
Control Panel server (scripts/crissa-panel-server.py) ships it too, for mask() alone (the User: "Crissa Wall + Krew = house"). Rebuilt on the merged house tree 2026-10-11 (the User: "Rebuild it on
today's panel without the Chatterbox parts"); docs/checkpoints/house-cp-wiring-20261011.

Switches (read at call time): HOUSE_CP_LOG_MASK=0 shows lines unmasked (the masking is the default). Paging and the
Whole log route are lib/panel_wiring's (HOUSE_CP_PAGED_LOGS, HOUSE_CP_WHOLE_LOG).
"""
from __future__ import annotations

import base64
import json
import os
import re
import signal as _signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

LINE_MAX = int(os.environ.get("VO_LOG_LINE_MAX", "8192"))
DEFAULT_LINES = 100
WHOLE = "/v1/log-whole"

# ── masking ──────────────────────────────────────────────────────────────────────────────────────────────────
MASK = "[masked]"
_KEY_WORDS = (r"(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret|secret|token|"
              r"access[_-]?token|refresh[_-]?token|auth[_-]?token|password|passwd|passphrase|pwd|"
              r"private[_-]?key|session[_-]?key|cookie)")
_MASKS: tuple[tuple[re.Pattern, str], ...] = (
    # Authorization: Bearer xyz / Basic xyz
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic|token)?\s*)([^\s\"',;]+)"), r"\1" + MASK),
    (re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"), r"\1" + MASK),
    # KEY=value, "api_key": "value", token: value, --password value (the name may carry a prefix: OPENAI_API_KEY)
    (re.compile(r"(?i)([\"']?[A-Za-z0-9_.-]*" + _KEY_WORDS + r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;&}]{4,})"),
     r"\1" + MASK),
    (re.compile(r"(?i)(--?[a-z0-9-]*" + _KEY_WORDS + r"[\s=]+)([^\s\"']{4,})"), r"\1" + MASK),
    # URL credentials and key query parameters
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)"), r"\1" + MASK + r"\3"),
    (re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token|sig|signature)=)([^&\s]+)"), r"\1" + MASK),
    # well-known key shapes, wherever they stand
    (re.compile(r"\b(sk-(?:ant-|proj-|or-)?[A-Za-z0-9_-]{16,})"), MASK),
    (re.compile(r"\b(xai-[A-Za-z0-9]{20,})"), MASK),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), MASK),
    (re.compile(r"\b(hf_[A-Za-z0-9]{20,})"), MASK),
    (re.compile(r"\b(xox[abposr]-[A-Za-z0-9-]{10,})"), MASK),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), MASK),
    (re.compile(r"\b(AIza[0-9A-Za-z_-]{30,})"), MASK),
    (re.compile(r"\b(glpat-[A-Za-z0-9_-]{20,})"), MASK),
    (re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), MASK),   # a JWT
    (re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
     r"\1 " + MASK + r" \2"),
)


def mask_on() -> bool:
    return os.environ.get("HOUSE_CP_LOG_MASK", "1").strip().lower() not in ("0", "false", "no", "off")


def mask(text: str) -> str:
    """Secrets out of one line (or a block) of log text. Errs toward masking: a log line loses a word, never
    a key. Kill: HOUSE_CP_LOG_MASK=0."""
    if not text or not mask_on():
        return text
    for rx, rep in _MASKS:
        text = rx.sub(rep, text)
    return text


# ── one line, never silently cut ─────────────────────────────────────────────────────────────────────────────
def cut_line(line: str, limit: int | None = None) -> str:
    """A line longer than `limit` keeps its head and SAYS it was cut; Whole log has it entire."""
    limit = LINE_MAX if limit is None else int(limit)
    if limit <= 0 or len(line) <= limit:
        return line
    return line[:limit] + f" … [{len(line) - limit:,} more characters on this line not shown · Whole log has it]"


def show(line: str) -> str:
    """What the panel shows for one raw log line: masked, then cut with its marker."""
    return cut_line(mask(line))


def lines_arg(v, default: int = DEFAULT_LINES) -> int | None:
    """?n=: a count, or None for every line ("all"). No upper cap: a person who asks for more gets more."""
    s = str(v if v is not None else "").strip().lower()
    if s == "all":
        return None
    try:
        n = int(s)
    except ValueError:
        return default
    return n if n > 0 else default


def size_words(n: int) -> str:
    x = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{int(x)} bytes" if unit == "bytes" else f"{x:.1f} {unit}"
        x /= 1024.0
    return str(n)


def journal_marker(n_shown: int, what: str) -> str:
    return (f"── earlier lines of {what} not shown ({n_shown} newest below) · "
            f"Earlier reads the page before · Whole log reads it all ──")


def file_marker(start: int, path) -> str:
    return (f"── earlier lines not shown: {size_words(start)} above in {Path(path).name} · "
            f"Earlier reads the page before · Whole log reads it all ──")


def since_marker(since: float) -> str:
    return (f"── showing lines since {time.strftime('%H:%M', time.localtime(since))} only (Render started then); "
            f"earlier lines not shown · Whole log reads it all ──")


# ── tokens ───────────────────────────────────────────────────────────────────────────────────────────────────
def token(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")


def untoken(t: str) -> dict:
    try:
        d = json.loads(base64.urlsafe_b64decode((t or "").encode() + b"==="))
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


# ── journals ─────────────────────────────────────────────────────────────────────────────────────────────────
Runner = Callable[..., "tuple[int, str, str]"]


def _default_run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def _iso(us) -> str:
    try:
        t = int(us) / 1e6
    except (TypeError, ValueError):
        return "?"
    s = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))
    return s[:-2] + ":" + s[-2:] if len(s) > 5 and s[-5] in "+-" else s


def _msg(e: dict) -> str:
    m = e.get("MESSAGE")
    if isinstance(m, list):                       # journald hands a binary message over as a byte list
        try:
            m = bytes(m).decode("utf-8", "replace")
        except (TypeError, ValueError):
            m = str(m)
    return "" if m is None else str(m)


def entry_lines(e: dict) -> list[str]:
    """One journal entry in journalctl's short-iso shape (a multi-line message keeps its continuation lines)."""
    ident = e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or ""
    pid = e.get("_PID") or e.get("SYSLOG_PID") or ""
    head = f"{_iso(e.get('__REALTIME_TIMESTAMP'))} {e.get('_HOSTNAME') or ''} {ident}{f'[{pid}]' if pid else ''}: "
    parts = _msg(e).split("\n")
    return [head + parts[0]] + ["    " + p for p in parts[1:]]


def _scope_page(scope: str, units: list[str], n: int | None, cursor: str, since: float | None,
                run: Runner) -> tuple[list[str], str, str]:
    """(lines oldest first, cursor for the page above or "", error)."""
    cmd = ["journalctl"] + (["--user"] if scope == "user" else []) + ["--no-pager", "-o", "json", "-r"]
    if n is not None:
        cmd += ["-n", str(n + 1)]
    if cursor:
        cmd += [f"--after-cursor={cursor}"]
    if since:
        cmd += [f"--since=@{int(since)}"]
    for u in units:
        cmd += ["-u", u]
    rc, out, err = run(cmd, timeout=60.0 if n is None or n > 2000 else 8.0)
    if rc != 0 and not out:
        return [], "", f"{scope}: {(err or f'journalctl rc {rc}')[:160]}"
    ents: list = []
    for raw in (out or "").splitlines():
        if not raw.strip() or raw.startswith("-- No entries"):
            continue
        try:
            ents.append(json.loads(raw))
        except ValueError:
            ents.append(raw)                       # an already formatted line (older journalctl, a test runner)
    more = n is not None and len(ents) > n
    if more:
        ents = ents[:n]
    ents.reverse()
    lines: list[str] = []
    for e in ents:
        lines += entry_lines(e) if isinstance(e, dict) else [str(e)]
    cur = ""
    if more and ents and isinstance(ents[0], dict):
        cur = str(ents[0].get("__CURSOR") or "")
    return lines, cur, ""


def journal_page(units: list[tuple[str, str]], n: int | None = DEFAULT_LINES, before: str = "",
                 since: float | None = None, what: str = "", run: Runner | None = None) -> dict:
    """[(unit, scope)] -> {ok, lines, earlier?, marker?, error?}. One journalctl per scope, merged by time.
    Lines come back masked and cut-with-marker; when more is above, lines[0] IS the marker."""
    run = run or _default_run
    by_scope: dict[str, list[str]] = {}
    for u, scope in units:
        by_scope.setdefault(scope or "user", [])
        if u not in by_scope[scope or "user"]:
            by_scope[scope or "user"].append(u)
    cur = untoken(before) if before else {}
    lines: list[str] = []
    errors: list[str] = []
    nxt: dict[str, str] = {}
    for scope, svc in by_scope.items():
        if before and scope not in cur:
            continue                               # that scope was read to its start already
        got, c, err = _scope_page(scope, svc, n, cur.get(scope, ""), since, run)
        if err:
            errors.append(err)
        lines += got
        if c:
            nxt[scope] = c
    if len(by_scope) > 1:
        lines.sort(key=lambda ln: ln[:25])
    shown = [show(ln) for ln in lines]
    out: dict = {"ok": not errors or bool(shown), "lines": shown}
    if nxt:
        out["earlier"] = token(nxt)
        out["marker"] = journal_marker(len(shown), what or "this journal")
        out["lines"] = [out["marker"]] + shown
    if since and not before:
        out["lines"] = [since_marker(since)] + out["lines"]
    if errors and not shown:
        out["error"] = "; ".join(errors)
    elif errors:
        out["warnings"] = errors
    return out


def whole_journal(units: list[tuple[str, str]], popen=subprocess.Popen) -> Iterator[str]:
    """Every line of the units' journals, oldest first, masked and NOT cut (the whole log is the place a long
    line is read in full). One journalctl per scope, streamed."""
    by_scope: dict[str, list[str]] = {}
    for u, scope in units:
        by_scope.setdefault(scope or "user", []).append(u)
    for scope, svc in by_scope.items():
        if len(by_scope) > 1:
            yield f"── {scope} journal: {', '.join(svc)} ──"
        cmd = ["journalctl"] + (["--user"] if scope == "user" else []) + ["--no-pager", "-o", "short-iso"]
        for u in svc:
            cmd += ["-u", u]
        try:
            p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
        except OSError as e:
            yield f"journalctl: {e}"
            continue
        try:
            for ln in p.stdout:
                yield mask(ln.rstrip("\n"))
        finally:
            try:
                p.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            p.wait()


# ── files ────────────────────────────────────────────────────────────────────────────────────────────────────
def file_page(path, n: int | None = DEFAULT_LINES, before=None, since: float | None = None,
              chunk: int = 65536) -> dict:
    """The last `n` lines of a file ending at byte `before` (EOF when None), read backwards. `n=None` = the whole
    file. When more is above, `earlier` is the byte offset that reads the page before and lines[0] is the
    marker. `since` (epoch) keeps stamped lines at or after it (continuation lines follow their stamp)."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "error": f"{p}: {e.strerror or type(e).__name__}", "lines": [], "file": str(p)}
    try:
        b = int(before) if before not in (None, "") else None
    except (TypeError, ValueError):
        b = None
    end = size if b is None else max(0, min(b, size))
    pos, data = end, b""
    try:
        with p.open("rb") as fh:
            if n is None:
                pos = 0
                data = fh.read(end)
            else:
                while pos > 0 and data.count(b"\n") <= n:
                    step = min(chunk, pos)
                    pos -= step
                    fh.seek(pos)
                    data = fh.read(step) + data
    except OSError as e:
        return {"ok": False, "error": f"{p}: {e.strerror or type(e).__name__}", "lines": [], "file": str(p)}
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    start = pos
    if pos > 0 and parts:                          # the first piece may be the tail of a line above it
        start += len(parts[0]) + 1
        parts = parts[1:]
    if n is not None and len(parts) > n:
        drop = parts[:len(parts) - n]
        start += sum(len(x) + 1 for x in drop)
        parts = parts[len(parts) - n:]
    raw = [x.decode("utf-8", "replace").rstrip("\r") for x in parts]
    if since is not None:
        raw = since_filter(raw, since)
    out = {"ok": True, "file": str(p), "size": size, "start": start, "end": end, "lines": [show(x) for x in raw]}
    if start > 0 and since is None:
        out["earlier"] = str(start)
        out["marker"] = file_marker(start, p)
        out["lines"] = [out["marker"]] + out["lines"]
    elif since is not None and not b:
        out["lines"] = [since_marker(since)] + out["lines"]
    return out


_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def since_filter(lines: list[str], since: float) -> list[str]:
    kept, keep = [], False
    for ln in lines:
        m = _STAMP.match(ln)
        if m:
            try:
                keep = time.mktime(time.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")) >= float(since)
            except (ValueError, OverflowError):
                keep = True
        if keep:
            kept.append(ln)
    return kept


def whole_file(path) -> Iterator[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                yield mask(ln.rstrip("\n"))
    except OSError as e:
        yield f"{path}: {e.strerror or type(e).__name__}"


def file_section(path, n: int = 40) -> list[str]:
    """A unit's own log file under its journal on a tile's tape: a header, the marker when the file has more,
    and its last `n` lines."""
    got = file_page(path, n)
    head = f"── {path} ──"
    if not got.get("ok"):
        return [head, "(empty or unreadable: " + str(got.get("error") or "") + ")"]
    body = got["lines"] or ["(empty)"]
    return [head] + body


# ── why it is down ───────────────────────────────────────────────────────────────────────────────────────────
_ERRLINE = re.compile(r"(Error|Exception|Traceback|FATAL|Fatal|fatal|CRITICAL|failed|Failed|FAILED|refus|denied|"
                      r"Killed|No such file|not found|cannot|can't|unable|Unable|rc=[1-9]|_rc=[1-9]|exit(?:ed)? "
                      r"(?:code|status)?=?[1-9])")
_SYSTEMD_NOISE = re.compile(r"^(?:\S+\.(?:service|timer): (?:Main process exited|Failed with result|Consumed|"
                            r"Scheduled restart|Deactivated)|Failed to start |Started |Starting |Stopped |Stopping )")


def pick_reason(lines: Iterable[str]) -> str:
    """Of some log lines, the one that names the trouble: the last error-looking line that is not systemd's own
    bookkeeping and not a traceback frame, else the last non-empty line."""
    clean = [ln.strip() for ln in lines if ln and ln.strip() and not ln.startswith("-- ")]
    for ln in reversed(clean):
        body = ln.split(": ", 1)[1] if re.match(r"^\d{4}-\d{2}-\d{2}T", ln) and ": " in ln else ln
        if _SYSTEMD_NOISE.match(body):
            continue
        if _ERRLINE.search(body) and not body.startswith(("File \"", "Traceback")):
            return mask(body)[:300]
    return mask(clean[-1])[:300] if clean else ""


RESULT_WORDS = {
    "exit-code": "exit {status}", "signal": "killed by {signal}", "core-dump": "crashed ({signal}, core dumped)",
    "timeout": "timed out", "watchdog": "stopped answering systemd's watchdog",
    "start-limit-hit": "gave up: restarted too often (start-limit-hit)", "oom-kill": "killed: out of memory",
    "resources": "could not start: a file, folder or user it needs is missing (resources)",
    "protocol": "broke systemd's start protocol", "exec-condition": "its start condition said no",
    "condition": "its start condition said no", "trigger-limit-hit": "gave up: triggered too often",
}


def _signame(n: str) -> str:
    try:
        return _signal.Signals(int(n)).name
    except (ValueError, TypeError):
        return f"signal {n}"


_REASONS: dict[tuple, str] = {}


def failure_reason(unit: str, scope: str, kv: dict, run: Runner | None = None) -> str:
    """A down unit's reason in words: its state, systemd's Result (exit code / signal / start limit / out of
    memory ...) and the journal line that says what went wrong. Cached per run of the unit, so a 5 s poll does
    not re-read the journal."""
    active, sub, result = kv.get("ActiveState", ""), kv.get("SubState", ""), kv.get("Result", "")
    key = (unit, scope, kv.get("InvocationID", ""), kv.get("InactiveEnterTimestampMonotonic", ""), active, result,
           kv.get("ExecMainStatus", ""))
    if key in _REASONS:
        return _REASONS[key]
    bits: list[str] = []
    if active == "failed":
        bits.append("failed")
    elif active in ("inactive", "deactivating") and result in ("", "success"):
        bits.append("stopped - nothing failed")
    elif active:
        bits.append(f"{active}/{sub}" if sub else active)
    if result and result != "success":
        code = str(kv.get("ExecMainStatus", "") or "")
        bits.append(RESULT_WORDS.get(result, result).format(
            status=code or "?", signal=_signame(code) if code else "a signal"))
    if active == "failed" or (result and result != "success"):
        name = unit if "." in unit.rsplit("/", 1)[-1] else unit + ".service"
        cmd = ["journalctl"] + (["--user"] if scope == "user" else []) + \
            ["--no-pager", "-o", "cat", "-n", "60", "-u", name]
        rc, out, _ = (run or _default_run)(cmd, timeout=6.0)
        if rc == 0 and out:
            line = pick_reason(out.splitlines())
            if line:
                bits.append(line)
    why = " · ".join(b for b in bits if b)
    if len(_REASONS) > 512:
        _REASONS.clear()
    _REASONS[key] = why
    return why
