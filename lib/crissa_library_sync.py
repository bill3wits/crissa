"""Library sync: the Wall pulls the user's finished packs from their Composer account (2026-10-01).

WHY. A pack made on the site reached the library only if the user carried the file into
the import folder by hand. This is the pull: the Wall asks the site what is ready for
this face, downloads each pack into the SAME drop folder, and the import path's
only-add rules do the rest. The site never reaches into the user's machine.

    link     the user pastes a library token from their account on the Connect page
             (<crissa home>/library.json, mode 0600; never shown back, never logged)
    list     GET {url}/v1/library/packs?face=<face>   Authorization: Bearer <token>
             -> {"packs": [{"id", "sha256", "bytes", "url"}]}
    fetch    each new pack to packs/incoming/<id>.zip: size capped, sha256 checked
    add      the user's click on the Connect page (crissa_import.apply). With
             CRISSA_LIBRARY_AUTO_ADD=1 a pack that needs no restart (tiles, playlists)
             is added at once; a pack with new loops always waits for the click.

FETCH IS NOT TRUST. A downloaded pack gets exactly the checks a hand-dropped one gets,
so a wrong or hostile site cannot overwrite or replace anything in the library.
One host only: https (http for a loopback address), the download link must be on the
same host as the list, and a redirect to any other host is refused. The token only
reads. Every failure is a quiet state for the status mark, never an error on screen.

OFF by default: CRISSA_LIBRARY_SYNC=1 turns it on, and only where the import path is on
(CRISSA_IMPORT=1, never the house). Off, the player has no library route, starts no
thread and makes no outbound call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import crissa_egress
import crissa_home
import crissa_import

ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SHA_OK = re.compile(r"^[0-9a-f]{64}$")
MAX_LISTED = 200
TOKEN_MAX = 512
LOOPBACK = ("127.0.0.1", "localhost", "::1")
_LOCK = threading.Lock()
_LAST: dict = {"at": 0.0, "state": "never", "listed": 0, "fetched": [], "added": [], "waiting": []}


class SyncError(ValueError):
    """Why a sync step did not happen. One plain line; never carries the token."""


def _on(env, name: str) -> bool:
    return str(env.get(name, "0")).strip().lower() in ("1", "on", "true", "yes")


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return _on(e, "CRISSA_LIBRARY_SYNC") and crissa_import.enabled(e)


def auto_add(env: dict | None = None) -> bool:
    return _on(os.environ if env is None else env, "CRISSA_LIBRARY_AUTO_ADD")


def every_s(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return max(60.0, float(e.get("CRISSA_LIBRARY_SYNC_EVERY_S", "900")))
    except ValueError:
        return 900.0


def path(home: Path | None = None) -> Path:
    return (home or crissa_home.HOME) / "library.json"


def _split(url: str):
    try:
        return urllib.parse.urlsplit(url)
    except ValueError:
        return None


def _host(url: str) -> str:
    u = _split(url)
    return (u.netloc or "").lower() if u else ""


def validate_url(url: str) -> str:
    """'' when the address may be used, else the reason."""
    u = _split(url)
    if not u or u.scheme not in ("http", "https") or not u.hostname:
        return "the library address must start with https://"
    if u.scheme == "http" and u.hostname.lower() not in LOOPBACK:
        return "the library address must use https"
    if u.username or u.password:
        return "the library address must not carry a name or password"
    return ""


def load(home: Path | None = None) -> dict:
    try:
        cfg = json.loads(path(home).read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def linked(cfg: dict) -> bool:
    return bool(str(cfg.get("url") or "").strip() and str(cfg.get("token") or "").strip())


def redacted(cfg: dict) -> dict:
    return {"url": str(cfg.get("url") or ""), "token_set": bool(cfg.get("token"))}


def save(update: dict, home: Path | None = None) -> dict:
    """Store the address and the token (0600). An empty token keeps the stored one, unless the
    host changed (then it is dropped: a token is never sent to a host it was not pasted for).
    `unlink: true` forgets both. Raises SyncError on a bad field."""
    p = path(home)
    cur = load(home)
    if update.get("unlink"):
        new: dict = {}
    else:
        url = str(update.get("url") if "url" in update else cur.get("url") or "").strip().rstrip("/")
        why = validate_url(url)
        if why:
            raise SyncError(why)
        token = str(update.get("token") or "").strip()
        if len(token) > TOKEN_MAX or any(c.isspace() for c in token):
            raise SyncError("that does not look like a library token")
        if not token and _host(url) == _host(str(cur.get("url") or "")):
            token = str(cur.get("token") or "")
        new = {"url": url}
        if token:
            new["token"] = token
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(new, f)
    os.replace(tmp, p)
    return new


class _SameHost(urllib.request.HTTPRedirectHandler):
    """A redirect may stay on the host it came from; anywhere else is refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _host(newurl) != _host(req.full_url) or validate_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect to another host refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, token: str, timeout: float):
    try:
        crissa_egress.check(url, "library sync")  # its own opener already keeps redirects on the same host
    except crissa_egress.Refused as exc:
        raise SyncError(str(exc)) from exc
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    return urllib.request.build_opener(_SameHost).open(req, timeout=timeout)


def list_remote(cfg: dict, face: str, timeout: float = 10.0) -> list[dict]:
    """The packs the account has ready for this face, each checked: a safe id, a sha256, a size
    a pack may be, a download link on the same host. Raises SyncError."""
    base = str(cfg.get("url") or "").rstrip("/")
    why = validate_url(base)
    if why or not cfg.get("token"):
        raise SyncError(why or "no library token")
    url = base + "/v1/library/packs?" + urllib.parse.urlencode({"face": face})
    try:
        with _open(url, str(cfg["token"]), timeout) as r:
            data = json.loads(r.read(1 << 20) or b"{}")
    except urllib.error.HTTPError as exc:
        raise SyncError("the library answered %d" % exc.code) from None
    except (OSError, ValueError):
        raise SyncError("the library is not reachable") from None
    rows = data.get("packs") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_LISTED:
        raise SyncError("the library's answer is not a pack list")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid, sha = str(row.get("id") or ""), str(row.get("sha256") or "").lower()
        size = row.get("bytes")
        link = urllib.parse.urljoin(base + "/", str(row.get("url") or ""))
        if (not ID_OK.match(pid) or not SHA_OK.match(sha) or isinstance(size, bool) or not isinstance(size, int)
                or not 0 < size <= crissa_import.MAX_BYTES or _host(link) != _host(base) or validate_url(link)):
            continue                                    # a row that is not a pack is skipped, not trusted
        out.append({"id": pid, "sha256": sha, "bytes": size, "url": link})
    return out


def have(pack_id: str, home: Path | None = None) -> str:
    """'' when this pack is new here; else where it already is (incoming | installed)."""
    root = crissa_import.root(home)
    if (root / "installed" / pack_id / "receipt.json").is_file():
        return "installed"
    if (root / "incoming" / pack_id).is_dir() or (root / "incoming" / (pack_id + ".zip")).is_file():
        return "incoming"
    return ""


def _pulled_path(home: Path | None = None) -> Path:
    return crissa_import.root(home) / "pulled.json"


def pulled(home: Path | None = None) -> list[str]:
    """Ids of the packs a pull brought here (a hand-dropped pack is not in this list)."""
    try:
        got = json.loads(_pulled_path(home).read_text(encoding="utf-8"))
        return [str(x) for x in got] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _note_pulled(pack_id: str, home: Path | None = None) -> None:
    ids = pulled(home)
    if pack_id in ids:
        return
    p = _pulled_path(home)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps((ids + [pack_id])[-500:]), encoding="utf-8")
    os.replace(tmp, p)


def waiting_now(home: Path | None = None) -> dict:
    """What sits in the import folder NOW, read from the folder itself: packs a pull brought, and
    packs somebody dropped there by hand. Never the last pull's count."""
    here, mine = crissa_import.incoming(home), set(pulled(home))
    return {"pulled": [p for p in here if p in mine], "dropped": [p for p in here if p not in mine]}


def fetch(row: dict, cfg: dict, home: Path | None = None, timeout: float = 60.0) -> Path:
    """Download one listed pack into the drop folder. The bytes must be the size and the sha256
    the list stated, or nothing is left behind. Raises SyncError."""
    dest = crissa_import.root(home) / "incoming" / (row["id"] + ".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h, n = hashlib.sha256(), 0
    try:
        with _open(row["url"], str(cfg["token"]), timeout) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > row["bytes"]:
                    raise SyncError("pack %s is larger than the library said" % row["id"])
                h.update(chunk)
                f.write(chunk)
        if n != row["bytes"] or h.hexdigest() != row["sha256"]:
            raise SyncError("pack %s is not the one the library listed" % row["id"])
        os.replace(tmp, dest)
    except SyncError:
        tmp.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise SyncError("the library answered %d for pack %s" % (exc.code, row["id"])) from None
    except (OSError, ValueError):
        tmp.unlink(missing_ok=True)
        raise SyncError("pack %s did not download" % row["id"]) from None
    return dest


def sync(pose_dir: Path, home: Path | None = None, speaking=lambda: False, now: float | None = None) -> dict:
    """One pull: list, fetch what is new, auto-add what may be. Never raises; the result is the
    state the page and the status mark show. One at a time."""
    global _LAST
    if not _LOCK.acquire(blocking=False):
        return dict(_LAST, busy=True)
    try:
        out = {"at": time.time() if now is None else now, "state": "ok", "listed": 0,
               "fetched": [], "added": [], "waiting": [], "skipped": []}
        cfg = load(home)
        if not linked(cfg):
            out["state"] = "not linked"
            return out
        try:
            face = str(json.loads((pose_dir / crissa_import.INVENTORY_REL).read_text(encoding="utf-8")).get("agent") or "")
            rows = list_remote(cfg, face)
        except (SyncError, OSError, ValueError) as exc:
            out["state"] = "unreachable"
            out["why"] = str(exc)[:120]
            return out
        out["listed"] = len(rows)
        for row in rows:
            if have(row["id"], home):
                continue
            try:
                fetch(row, cfg, home)
                _note_pulled(row["id"], home)
                out["fetched"].append(row["id"])
            except SyncError as exc:
                out["skipped"].append({"id": row["id"], "why": str(exc)[:120]})
        for pid in crissa_import.incoming(home):
            if auto_add() and pid in out["fetched"] and not speaking():
                try:
                    plan = crissa_import.plan(pid, pose_dir, home)
                    if not (plan["clips_add"] or plan["pools_add"]):     # new loops need a restart: the user's click
                        crissa_import.apply(pid, pose_dir, home)
                        out["added"].append(pid)
                        continue
                except crissa_import.ImportRefused:
                    pass                                                 # stays in the folder; the page says why
            out["waiting"].append(pid)
        return out
    finally:
        if "out" in locals():
            _LAST = out
        _LOCK.release()


def status(home: Path | None = None) -> dict:
    cfg = load(home)
    return {"on": enabled(), "linked": linked(cfg), "auto_add": auto_add(), **redacted(cfg), "last": dict(_LAST),
            "waiting_now": waiting_now(home)}


def start_timer(pose_dir: Path, speaking=lambda: False, reloaded=lambda added: None) -> "threading.Thread | None":
    """The background pull, only when the switch is on: one pull a little after boot, then every
    CRISSA_LIBRARY_SYNC_EVERY_S (900, floor 60). `reloaded` is told what an auto-add landed."""
    if not enabled():
        return None

    def loop() -> None:
        time.sleep(20.0)
        while True:
            try:
                got = sync(pose_dir, speaking=speaking)
                if got.get("added"):
                    reloaded(got["added"])
            except Exception:           # the timer never costs the player anything
                pass
            time.sleep(every_s())

    t = threading.Thread(target=loop, daemon=True, name="library-sync")
    t.start()
    return t
