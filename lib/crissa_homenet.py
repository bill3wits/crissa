"""This computer's home network, MEASURED (the User 2026-10-28, on the phone's firewall key: "Measure the network
instead of assuming /24. Read the real home network's size and the right interface from the system. Plenty of home
routers aren't /24, and a guest Wi-Fi or VPN shouldn't count as home").

`home()` reads the system's own answer, nothing assumed:
- the connection is the IPv4 default route with the lowest metric (`ip -j -4 route show default`); a VPN that takes
  the default route is refused, so a computer whose way out is a VPN has no home network here;
- its address and the network's real size come from that connection's own address (`ip -j -4 addr show dev X`):
  a /22 router is a /22, a /24 is a /24;
- a VPN or tunnel is never home: an interface whose link type is not Ethernet/Wi-Fi (`/sys/class/net/X/type` != 1 -
  WireGuard, tun, ppp, sit read 65534 / 512 / 776), a tun/tap device (`tun_flags`), or a link the kernel names as a
  tunnel (`ip -d` info_kind wireguard / tun / vxlan / gre / ipip ...);
- the network must sit inside a home range (10/8, 172.16/12, 192.168/16) and be no wider than MIN_PREFIX.

What it cannot tell: a guest Wi-Fi from a home Wi-Fi. From this computer they are the same kind of network, so the
answer always NAMES the network (and the Wi-Fi's name when NetworkManager says it) and the person decides.

IPv4 only, on purpose: the phone opens the address the QR code carries, which is this IPv4 address, so it always
comes in over IPv4 even when it has IPv6; nothing is ever opened on IPv6 (a global IPv6 address is not a home range).

On a computer without `ip` (a Mac) the answer falls back to the route's source address, the old reading, with no
network size: the firewall key is then not offered. Stdlib only.
"""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

SYS_NET = Path("/sys/class/net")
# A home network wider than this is not one person's Wi-Fi: a /16 is 65,534 addresses, the most a home router hands
# out (10.0.0.0/16 on some mesh kits). A rule for anything wider would let a whole office or a provider's network reach
# the phone port. A dial with its reason (CRISSA_HOMENET_MIN_PREFIX), never a hidden cap.
MIN_PREFIX = 16
TUNNEL_KINDS = ("wireguard", "tun", "tap", "vxlan", "gre", "gretap", "ipip", "sit", "ip6tnl", "vti", "geneve",
                "ipvlan", "macsec", "ppp", "l2tp")
IP_WAIT_S = 3.0
# The home ranges (RFC 1918). Python's is_private also counts the documentation, benchmark and carrier/Tailscale
# ranges (100.64/10), none of which is a home Wi-Fi.
HOME_RANGES = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def is_home_range(net) -> bool:
    try:
        n = ipaddress.IPv4Network(str(net), strict=False)
    except ValueError:
        return False
    return any(n.subnet_of(r) for r in HOME_RANGES)


def min_prefix(env: dict | None = None) -> int:
    e = os.environ if env is None else env
    try:
        return max(8, min(30, int(e.get("CRISSA_HOMENET_MIN_PREFIX", MIN_PREFIX))))
    except ValueError:
        return MIN_PREFIX


def _ip_json(args: list[str], run=subprocess.run, which=shutil.which):
    ip = which("ip")
    if not ip:
        return None
    try:
        r = run([ip, "-j"] + args, capture_output=True, text=True, timeout=IP_WAIT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return None


def tunnel_why(dev: str, *, sys_net: Path = SYS_NET, run=subprocess.run, which=shutil.which) -> str:
    """Why `dev` is a VPN or tunnel ('' = it is an ordinary Ethernet or Wi-Fi connection)."""
    d = sys_net / dev
    try:
        t = (d / "type").read_text().strip()
    except OSError:
        t = ""
    if t and t != "1":
        return "%s is a VPN or tunnel (link type %s), not a home network" % (dev, t)
    if (d / "tun_flags").exists():
        return "%s is a VPN (tun/tap device), not a home network" % dev
    info = _ip_json(["-d", "link", "show", "dev", dev], run=run, which=which) or []
    for row in info if isinstance(info, list) else []:
        kind = str(((row or {}).get("linkinfo") or {}).get("info_kind") or "").lower()
        if kind in TUNNEL_KINDS:
            return "%s is a VPN or tunnel (%s), not a home network" % (dev, kind)
    return ""


def wifi_name(dev: str, *, run=subprocess.run, which=shutil.which) -> str:
    """The Wi-Fi's own name when NetworkManager says it ('' otherwise; never a reason to refuse)."""
    nm = which("nmcli")
    if not nm:
        return ""
    try:
        r = run([nm, "-t", "-g", "GENERAL.CONNECTION", "device", "show", dev], capture_output=True, text=True,
                timeout=IP_WAIT_S)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (r.stdout or "").strip().splitlines()[0][:64] if r.returncode == 0 and (r.stdout or "").strip() else ""


def route_source_ip() -> str:
    """The old reading: the source address of a route to a private address (nothing is sent). A VPN that routes 10/8
    answers here too, which is why it is only the fallback."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
            so.connect(("10.255.255.255", 9))
            ip = so.getsockname()[0]
    except OSError:
        return ""
    return "" if ip.startswith("127.") else ip


def home(*, env: dict | None = None, sys_net: Path = SYS_NET, run=subprocess.run, which=shutil.which) -> dict:
    """{"ok", "why", "ip", "net", "dev", "kind" ("Wi-Fi" | "wired network" | ""), "name", "measured"}.
    `measured` False = the fallback reading (no `ip` here): an address for the QR, no network for a firewall rule."""
    out = {"ok": False, "why": "", "ip": "", "net": "", "dev": "", "kind": "", "name": "", "measured": False}
    routes = _ip_json(["-4", "route", "show", "default"], run=run, which=which)
    if routes is None:
        ip = route_source_ip()
        out.update(ip=ip, why="" if ip else "this computer is on no network")
        return out
    out["measured"] = True
    routes = [r for r in routes if isinstance(r, dict) and r.get("dev")]
    if not routes:
        out["why"] = "this computer has no way out to a network (no default route)"
        return out
    best = min(routes, key=lambda r: int(r.get("metric") or 0))
    dev = str(best["dev"])
    out["dev"] = dev
    why = tunnel_why(dev, sys_net=sys_net, run=run, which=which)
    if why:
        out["why"] = why + "; connect to your home Wi-Fi without the VPN to let the phone in"
        return out
    addrs = _ip_json(["-4", "addr", "show", "dev", dev], run=run, which=which) or []
    cand = []
    for row in addrs if isinstance(addrs, list) else []:
        for a in (row or {}).get("addr_info") or []:
            if a.get("family") == "inet" and a.get("scope", "global") == "global" and a.get("local"):
                cand.append(a)
    pref = str(best.get("prefsrc") or "")
    cand.sort(key=lambda a: (a.get("local") != pref, bool(a.get("secondary"))))
    if not cand:
        out["why"] = "%s has no IPv4 address" % dev
        return out
    try:
        iface = ipaddress.IPv4Interface("%s/%s" % (cand[0]["local"], int(cand[0].get("prefixlen") or 32)))
    except (ValueError, TypeError):
        out["why"] = "%s has an address this computer cannot read" % dev
        return out
    out["ip"] = str(iface.ip)
    if iface.network.prefixlen >= 31:
        out["why"] = "%s has a point-to-point address, not a home network" % dev
        return out
    if iface.network.prefixlen < min_prefix(env):
        out["why"] = ("%s is wider than a home network (more than /%d); Crissa will not open the phone port to it"
                      % (iface.network, min_prefix(env)))
        return out
    if not is_home_range(iface.network):
        out["why"] = "%s is not a home network address (%s is public)" % (dev, iface.ip)
        return out
    out["net"] = str(iface.network)
    out["kind"] = "Wi-Fi" if (sys_net / dev / "wireless").exists() else "wired network"
    # a Wi-Fi's own name only (the User 2026-10-29): a wired connection's NetworkManager name ("netplan-enp113s0",
    # "Wired connection 1") repeats the device or says nothing, so a wired network is named by its kind alone
    out["name"] = wifi_name(dev, run=run, which=which) if out["kind"] == "Wi-Fi" else ""
    out["ok"] = True
    return out


def home_ip(**kw) -> str:
    """The address the phone opens: the measured home address, else the old route-source reading (a VPN or an
    unmeasured computer keeps the QR it had; only the firewall key needs a measured home)."""
    h = home(**kw)
    if h.get("ok") or (h.get("ip") and not h.get("measured")):
        return str(h["ip"])
    return route_source_ip()
