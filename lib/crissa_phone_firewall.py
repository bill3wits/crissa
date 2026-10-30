"""Let the phone in through the firewall with one key, and take it out with another (the User 2026-10-26, on the phone
card's "If it does not open: sudo ufw allow ..." line: "Can we run that when they click it? Many will not know how";
2026-10-28: "An undo ... Measure the network instead of assuming /24 ... Confirm the new rule doesn't open Crissa's
other ports or the Control Panel to the network").

The phone card and Setup's last page get a key, "Allow my phone on this Wi-Fi", that runs ONE ufw rule through
pkexec, the desktop's own password prompt (the same login password Secure asks for):

    ufw allow in on <connection> from <home network> to any port <phone port> proto tcp

- The network and the connection are MEASURED (lib/crissa_homenet): the default route's interface and that
  interface's own network size. A VPN or tunnel is never home, a public or too-wide network is refused, and the key
  names the network it opens before it is pressed (a guest Wi-Fi cannot be told from a home one here; the person
  decides from the name).
- One port, one network, one connection, IPv4 only. Crissa's other ports and the Control Panel are not in it: they
  listen on this computer only (127.0.0.1), and the rule names the phone port alone. After a press `exposed()` lists
  what this user's own programs leave listening beyond loopback, so anything else open says so on the card.
- "Remove phone access" deletes exactly the rule the key recorded (the state file `phone-firewall.json`), with the
  same password prompt. A rule the person typed themselves is never touched: the key only removes its own.
- A rule recorded for another network (the computer moved, or a guest Wi-Fi) is shown as such, and Remove still
  takes it out.

When the key is offered: ufw installed and switched on (`ENABLED=yes` in /etc/ufw/ufw.conf, which anyone may read;
`ufw status` needs root, and asking for a password just to draw the card is not on), pkexec installed, a measured
home network. Whether ufw already holds the rule cannot be read without root; a press when it does is harmless (ufw
answers "Skipping adding existing rule", exit 0).

The player serves it at POST /v1/phone/allow and /v1/phone/remove, loopback only (the socket's peer and the page's
Origin); `{"dry": true}` returns the exact command and runs nothing. Kill: CRISSA_PHONE_FIREWALL_KEY=0 (the card shows
the CLI line alone, as before).
"""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import crissa_homenet

UFW_CONF = Path("/etc/ufw/ufw.conf")
STATE_NAME = "phone-firewall.json"
# How long the password prompt may stay open: a person reads it, finds the password and types it. Past this the press
# answers "no answer from the password prompt" and nothing is changed. A dial with its reason, never a hidden cap.
PROMPT_WAIT_S = 180.0
LABEL = "Allow my phone on this Wi-Fi"
REMOVE_LABEL = "Remove phone access"


def label(kind: str = "") -> str:
    """The key's words by the network measured (the User 2026-10-29: the key said "on this Wi-Fi" on a wired network)."""
    return {"Wi-Fi": LABEL, "wired network": "Allow my phone on this wired network"}.get(kind,
                                                                                         "Allow my phone on this network")
SS_WAIT_S = 3.0


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("CRISSA_PHONE_FIREWALL_KEY", "1")).strip().lower() not in ("0", "false", "no", "off")


def prompt_wait_s(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return max(10.0, float(e.get("CRISSA_PHONE_FIREWALL_WAIT_S", PROMPT_WAIT_S)))
    except ValueError:
        return PROMPT_WAIT_S


def rule_args(net: str, port: int, dev: str) -> list[str]:
    """The ufw arguments: one connection, one network, one port, tcp. Nothing wider."""
    return ["allow", "in", "on", str(dev), "from", net, "to", "any", "port", str(int(port)), "proto", "tcp"]


def rule_line(net: str, port: int, dev: str) -> str:
    """The rule as a person would type it (the card shows it, and it is the fallback)."""
    return "sudo ufw " + " ".join(rule_args(net, port, dev))


def remove_line(net: str, port: int, dev: str) -> str:
    return "sudo ufw delete " + " ".join(rule_args(net, port, dev))


def where(h: dict) -> str:
    """The network in words: its kind, its Wi-Fi name when known, the network and the connection it sits on."""
    name = (' "%s"' % h["name"]) if h.get("name") else ""
    return "%s%s (%s on %s)" % (h.get("kind") or "network", name, h.get("net"), h.get("dev"))


def ufw_on(conf: Path = UFW_CONF) -> bool:
    try:
        for line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "ENABLED":
                return v.strip().strip('"').lower() == "yes"
    except OSError:
        return False
    return False


def _state_path(home=None) -> Path:
    if home is None:
        import crissa_home
        home = crissa_home.HOME
    return Path(home) / STATE_NAME


def _sane(net: str, port: int, dev: str) -> bool:
    """A rule the key may run: a private IPv4 network no wider than the home limit, one real port, a plain device name."""
    try:
        n = ipaddress.IPv4Network(net, strict=True)
        port = int(port)
    except (ValueError, TypeError):
        return False
    if not crissa_homenet.is_home_range(n) or n.prefixlen < crissa_homenet.min_prefix() or n.prefixlen > 30:
        return False
    if not (0 < port < 65536):
        return False
    dev = str(dev or "")
    return bool(dev) and len(dev) <= 15 and all(c.isalnum() or c in "-_.@" for c in dev) and not dev.startswith("-")


def recorded(home=None) -> dict:
    """The rule this key added ({} = none): {"ok", "net", "port", "dev", "at"}."""
    try:
        d = json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not (isinstance(d, dict) and d.get("ok") and _sane(d.get("net"), d.get("port"), d.get("dev"))):
        return {}
    return d


def _tools(conf: Path, which) -> tuple[str, str, str]:
    """(ufw, pkexec, why-not); why-not '' = both here and ufw is on."""
    ufw = which("ufw") or ("/usr/sbin/ufw" if Path("/usr/sbin/ufw").is_file() else "")
    pk = which("pkexec")
    if not ufw or not ufw_on(conf):
        return ufw, pk or "", "the firewall (ufw) is not switched on here, so nothing blocks the phone"
    if not pk:
        return ufw, "", "no password prompt (pkexec) on this computer: run the line below in a terminal"
    return ufw, pk, ""


def plan(port: int, *, home=None, env: dict | None = None, conf: Path = UFW_CONF, which=shutil.which,
         net_home: dict | None = None) -> dict:
    """What the card and Setup offer:
    {"offer", "why", "rule", "net", "dev", "where", "port", "argv", "label",
     "remove": {"offer", "rule", "argv", "where", "here"}}.
    `argv` is what an Allow press runs; remove.argv what a Remove press runs. `remove.here` False = the recorded rule
    is for another network than the one this computer is on now."""
    h = crissa_homenet.home(env=env) if net_home is None else net_home
    net, dev = str(h.get("net") or ""), str(h.get("dev") or "")
    out = {"offer": False, "why": "", "rule": rule_line(net, port, dev) if h.get("ok") else "", "net": net,
           "dev": dev, "where": where(h) if h.get("ok") else "", "port": int(port), "argv": [],
           "label": label(str(h.get("kind") or "")),
           "remove": {"offer": False, "rule": "", "argv": [], "where": "", "here": False}}
    if not enabled(env):
        out["why"] = "switched off (CRISSA_PHONE_FIREWALL_KEY=0)"
        return out
    ufw, pk, why = _tools(conf, which)
    rec = recorded(home)
    if rec and not why:
        here = bool(h.get("ok")) and rec["net"] == net and rec["dev"] == dev and int(rec["port"]) == int(port)
        out["remove"] = {"offer": True, "rule": remove_line(rec["net"], int(rec["port"]), rec["dev"]),
                         "argv": [pk, ufw, "delete"] + rule_args(rec["net"], int(rec["port"]), rec["dev"]),
                         "where": "%s on %s" % (rec["net"], rec["dev"]), "here": here}
    if why:
        out["why"] = why
        return out
    if not h.get("ok"):
        out["why"] = h.get("why") or "this computer has no home network to let the phone in on"
        return out
    if not _sane(net, port, dev):
        out["why"] = "refused: %s on %s is not a home network rule" % (net, dev)
        return out
    if out["remove"]["here"]:
        out["why"] = "your phone is allowed on %s" % out["where"]
        return out
    out.update(offer=True, argv=[pk, ufw] + rule_args(net, port, dev))
    return out


def _run(argv: list[str], env, run) -> tuple[bool, str]:
    try:
        r = run(argv, capture_output=True, text=True, timeout=prompt_wait_s(env))
    except subprocess.TimeoutExpired:
        return False, "no answer from the password prompt; nothing was changed"
    except OSError as e:
        return False, "could not run pkexec (%s): run the line below in a terminal" % type(e).__name__
    said = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode in (126, 127):
        # pkexec: 126 = the prompt was dismissed or the password refused, 127 = not authorised / no agent
        return False, "not allowed: the password prompt was closed or refused (%s)" % (said or r.returncode)
    if r.returncode != 0:
        return False, "ufw said: %s" % (said or r.returncode)
    return True, said


def _write_state(home, d: dict | None) -> None:
    path = _state_path(home)
    try:
        if d is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def allow(p: dict, *, home=None, env: dict | None = None, run=subprocess.run, listening=None) -> dict:
    """Run the planned rule through pkexec. {"ok", "said", "exposed"}; the state file records a success."""
    if not p.get("offer") or not p.get("argv"):
        return {"ok": False, "said": p.get("why") or "nothing to do", "exposed": []}
    argv = list(p["argv"])
    net, dev, port = str(p.get("net") or ""), str(p.get("dev") or ""), p.get("port")
    # never a wider rule than the plan's own measured network, connection and port, whatever reached here
    if not _sane(net, port, dev) or argv[2:] != rule_args(net, port, dev):
        return {"ok": False, "said": "refused: the rule is not this computer's home network and the phone port",
                "exposed": []}
    ok, said = _run(argv, env, run)
    if not ok:
        return {"ok": False, "said": said, "exposed": []}
    _write_state(home, {"ok": True, "net": net, "port": int(port), "dev": dev,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ufw": said[:200]})
    return {"ok": True, "said": said or "rule added", "exposed": exposed(int(port), listening=listening)}


def remove(p: dict, *, home=None, env: dict | None = None, run=subprocess.run) -> dict:
    """Delete the rule this key recorded, through pkexec. {"ok", "said"}; the state file goes with it."""
    rm = (p or {}).get("remove") or {}
    rec = recorded(home)
    if not rm.get("offer") or not rm.get("argv") or not rec:
        return {"ok": False, "said": "nothing to remove: this key has added no rule"}
    argv = list(rm["argv"])
    if argv[2:] != ["delete"] + rule_args(rec["net"], int(rec["port"]), rec["dev"]):
        return {"ok": False, "said": "refused: that is not the rule this key added"}
    ok, said = _run(argv, env, run)
    if not ok:
        return {"ok": False, "said": said}
    _write_state(home, None)
    return {"ok": True, "said": said or "rule removed"}


def _ss_rows(run=subprocess.run, which=shutil.which) -> list[tuple[str, int, str]]:
    """(address, port, program) of every TCP listener whose program this user can see (`ss -ltnpH`: only the user's
    own programs carry a users:(( )) column without root)."""
    ss = which("ss")
    if not ss:
        return []
    try:
        r = run([ss, "-ltnpH"], capture_output=True, text=True, timeout=SS_WAIT_S)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or "users:((" not in line:
            continue                            # not one of this user's programs: not Crissa's
        addr, _, port = parts[3].rpartition(":")
        prog = line.split('users:(("', 1)[-1].split('"', 1)[0]
        try:
            rows.append((addr.strip("[]").split("%")[0], int(port), prog))
        except ValueError:
            continue
    return rows


def exposed(phone_port: int, *, listening=None, run=subprocess.run, which=shutil.which) -> list[dict]:
    """THE SECOND-ORDER CHECK: this user's programs listening beyond loopback, other than the phone port. The rule
    opens the phone port only; this says whether anything ELSE of ours waits on the network (an empty list = only the
    phone port). `listening` = test rows (address, port, program)."""
    rows = _ss_rows(run=run, which=which) if listening is None else listening
    out = []
    for addr, port, prog in rows:
        if int(port) == int(phone_port):
            continue
        try:
            if ipaddress.ip_address(addr).is_loopback:
                continue
        except ValueError:
            if addr not in ("*", ""):
                continue
        out.append({"address": addr or "*", "port": int(port), "program": prog})
    return out
