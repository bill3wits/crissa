"""One switch for what leaves this machine: kill-search (the User 2026-10-05, "build kill-search first").

The claim it exists for (the User 2026-10-02, NOT to be stated until an audit on an installed pack passes):
"Within Crissa nothing goes out except search queries from the agents' browser, and one kill-search switch
stops even that." The inventory behind it is docs/CRISSA-EGRESS-INVENTORY.md.

The switch is `search.kill` in the network config (config/crissa-network.json, an install's own
<state home>/crissa-network.json replaces a key), the same file that says what the LAN is. `CRISSA_SEARCH_KILL`
=1/0 in the environment outranks the file, for a launcher or a test. OFF by default: then every function here
is a no-op and every caller behaves byte for byte as before.

While it is ON:
  - every URL a Crissa module opens must be on the LAN (`crissa_net.is_lan`, the private ranges + localhost,
    plus names the install lists in `lan_hosts`). A host NAME is never resolved to decide: a lookup would
    itself tell a resolver outside where we meant to go. A redirect is checked again.
  - the first-use installers (aligner, voice ASR, oracle models) refuse to start: they download from the
    internet. Their bundle-from-a-file route is the offline way in.
  - a Brave the packs start resolves no host name but the LAN's (`brave_flags`).
  - agents' search goes to the LAN relay (`search.relay_url`, a SearXNG `/search?format=json`) or nowhere,
    and page reads (firecrawl) go to `relay_extract_url` or nowhere (`search_route`).
    `search_route` is the table; Crissa ships no agent-framework hook that reads it (an agent's own
    add-on may, through the same file).
The Imagine link-out is exempt (the User: it goes out from our server, by the user's own click).

Self-contained on purpose: the voice and oracle modules ship a vendored copy beside crissa_net and
crissa_home (config/crissa-packages.json `vendor`), because each is its own install.
"""
from __future__ import annotations

import ipaddress
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_net  # noqa: E402

SEARCH_DEFAULTS = {"kill": False, "relay_url": "", "relay_extract_url": "", "timeout_s": 10}
ENV_KILL = "CRISSA_SEARCH_KILL"
_ON = ("1", "on", "true", "yes")
_OFF = ("0", "off", "false", "no")


class Refused(PermissionError):
    """kill-search is on and this call would leave the LAN. The message is for the person, never a key.
    An OSError on purpose: a caller that already treats an unreachable host as "not there" (a status mark,
    a probe) treats a refused one the same way, with no new except clause."""


def search(home: Path | None = None) -> dict:
    out = dict(SEARCH_DEFAULTS)
    got = crissa_net.config(home).get("search")
    if isinstance(got, dict):
        out.update({k: v for k, v in got.items() if not str(k).startswith("_")})
    return out


def killed(home: Path | None = None, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    v = str(e.get(ENV_KILL) or "").strip().lower()
    if v in _ON:
        return True
    if v in _OFF:
        return False
    k = search(home).get("kill")
    return k is True or str(k).strip().lower() in _ON


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def _refusal(url: str, what: str) -> str:
    h = host_of(url) or "an address"
    return "kill-search is on: %s would reach %s, outside your network" % (what or "this call", h)


def allowed(url: str, home: Path | None = None) -> bool:
    """Would kill-search let this URL out? (True for any URL while the switch is off.)"""
    if not killed(home):
        return True
    scheme = urllib.parse.urlsplit(str(url)).scheme.lower()
    if scheme in ("file", "data"):
        return True
    return crissa_net.is_lan(host_of(url), home=home, resolve=False)


def check(url: str, what: str = "", home: Path | None = None) -> None:
    """Raise Refused if kill-search is on and `url` is not on the LAN."""
    if not allowed(url, home):
        raise Refused(_refusal(url, what))


def refuse_install(what: str, home: Path | None = None) -> None:
    """A first-use installer downloads from the internet: refused while kill-search is on."""
    if killed(home):
        raise Refused("kill-search is on: %s downloads from the internet. Import its bundle from a file "
                      "instead, or turn kill-search off for the install." % what)


class _LanRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, what: str, home: Path | None):
        self.what, self.home = what, home

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, self.home):
            raise Refused(_refusal(newurl, self.what + " (a redirect)"))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _lan_proxies(home: Path | None = None) -> dict:
    """The environment's proxies that are themselves on the LAN. A LAN target checked by check()
    must not ride an off-LAN proxy (http_proxy with no no_proxy for 127.0.0.1): such a proxy is
    dropped and the call goes direct."""
    return {k: v for k, v in urllib.request.getproxies().items()
            if k != "no" and crissa_net.is_lan(host_of(v), home=home, resolve=False)}


def urlopen(req, timeout: float, what: str = "", home: Path | None = None):
    """urllib.request.urlopen with the switch in front. OFF: exactly urllib's own call."""
    if not killed(home):
        return urllib.request.urlopen(req, timeout=timeout)
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    check(url, what, home)
    return urllib.request.build_opener(urllib.request.ProxyHandler(_lan_proxies(home)),
                                       _LanRedirect(what, home)).open(req, timeout=timeout)


def ws_opts(url: str, what: str = "", home: Path | None = None) -> dict:
    """Check a websocket URL (ws://, wss://) like urlopen does; returns extra
    websocket.create_connection kwargs. ON: the host bypasses any env proxy."""
    if not killed(home):
        return {}
    check(url, what, home)
    h = host_of(url)
    return {"http_no_proxy": [h]} if h else {}


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def brave_flags(home: Path | None = None) -> list[str]:
    """Extra Brave flags while kill-search is on: no host name resolves but localhost, the install's LAN
    names and the relay's host. An IP-literal LAN address needs no lookup, so it still works."""
    if not killed(home):
        return []
    keep = ["localhost"]
    for h in crissa_net.config(home).get("lan_hosts") or []:
        h = str(h).strip().lower()
        if h and h not in keep:
            keep.append(h)
    s = search(home)
    for u in (s.get("relay_url"), s.get("relay_extract_url")):
        h = host_of(u or "")
        if h and h not in keep and not _is_ip(h) and crissa_net.is_lan(h, home=home):
            keep.append(h)
    return ["--host-resolver-rules=MAP * ~NOTFOUND, " + ", ".join("EXCLUDE " + h for h in keep)]


def search_route(home: Path | None = None) -> dict:
    """Where agents' search and page reads go (docs/CRISSA-EGRESS-INVENTORY.md, the kill x relay table).

    search:  "local" (this machine's own SearXNG), "relay" (the LAN relay) or "off".
    extract: "local" (this machine's own page reader, e.g. firecrawl), "relay" or "off".
    A relay URL that is not on the LAN is never used: under kill it is refused (off), with kill off it is
    reported in `refused` and the route falls back to local.
    """
    s = search(home)
    kill = killed(home)
    relay = str(s.get("relay_url") or "").strip()
    xrelay = str(s.get("relay_extract_url") or "").strip()
    refused = [u for u in (relay, xrelay) if u and not crissa_net.is_lan(host_of(u), home=home)]
    relay = "" if relay in refused else relay
    xrelay = "" if xrelay in refused else xrelay
    out = {"kill": kill, "refused": refused, "timeout_s": s.get("timeout_s")}
    if relay:
        out.update(search="relay", search_url=relay)
    else:
        out.update(search="off" if kill else "local", search_url="")
    if xrelay:
        out.update(extract="relay", extract_url=xrelay)
    else:
        out.update(extract="off" if kill else "local", extract_url="")
    return out
