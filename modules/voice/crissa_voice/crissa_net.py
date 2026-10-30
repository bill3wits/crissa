"""What counts as the LAN for Crissa (the User 2026-10-02).

The private address ranges (10/8, 172.16/12, 192.168/16) plus localhost (127/8, ::1, the name `localhost`),
from config/crissa-network.json, editable per install in <state home>/crissa-network.json - a key there
replaces the house default. The kill-search switch and the search relay (scoped, not built) will ask
`is_lan(host)` before any call leaves the machine. A host NAME other than the listed ones is not the LAN: it
is resolved only when `resolve=True`, and then every address it resolves to must be inside.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crissa_home  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config" / "crissa-network.json"
DEFAULTS = {"lan_ranges": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128"],
            "lan_hosts": ["localhost"]}


def config(home: Path | None = None) -> dict:
    out = dict(DEFAULTS)
    for p in (CONFIG, (home or crissa_home.HOME) / "crissa-network.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.update({k: v for k, v in d.items() if not k.startswith("_")})
    return out


def lan_networks(home: Path | None = None) -> list:
    nets = []
    for r in config(home).get("lan_ranges") or []:
        try:
            nets.append(ipaddress.ip_network(str(r), strict=False))
        except ValueError:
            continue                          # a bad row is ignored, never widens the LAN
    return nets


def is_lan(host: str, *, home: Path | None = None, resolve: bool = False) -> bool:
    """True when `host` (an address or a name) is inside the configured LAN."""
    h = str(host or "").strip().strip("[]").lower()
    if not h:
        return False
    cfg = config(home)
    if h in {str(x).lower() for x in cfg.get("lan_hosts") or []}:
        return True
    nets = lan_networks(home)
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        if not resolve:
            return False
        try:
            addrs = {i[4][0] for i in socket.getaddrinfo(h, None)}
        except OSError:
            return False
        return bool(addrs) and all(any(ipaddress.ip_address(a.split("%")[0]) in n for n in nets) for a in addrs)
    return any(addr in n for n in nets)
