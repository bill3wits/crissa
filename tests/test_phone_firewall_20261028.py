"""The phone's firewall keys (the User 2026-10-28): an undo, the network measured instead of a /24, IPv4 only, only the
phone port opened, and Secure / presence reading the phone the same way."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import crissa_homenet as hn  # noqa: E402
import crissa_phone_firewall as fw  # noqa: E402


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_ip(routes, addrs, links=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        a = argv[2:]
        if a[:3] == ["-4", "route", "show"]:
            return R(0, json.dumps(routes))
        if a[:3] == ["-4", "addr", "show"]:
            return R(0, json.dumps(addrs.get(a[-1], [])))
        if a[:3] == ["-d", "link", "show"]:
            return R(0, json.dumps((links or {}).get(a[-1], [{}])))
        return R(1)
    return run, calls


def sysnet(tmp_path, dev, typ="1", wireless=False, tun=False):
    d = tmp_path / "net" / dev
    d.mkdir(parents=True)
    (d / "type").write_text(typ)
    if wireless:
        (d / "wireless").mkdir()
    if tun:
        (d / "tun_flags").write_text("0x1001")
    return tmp_path / "net"


def which(name):
    return {"ip": "/sbin/ip", "ufw": "/usr/sbin/ufw", "pkexec": "/usr/bin/pkexec"}.get(name)


def addr(local, plen):
    return [{"addr_info": [{"family": "inet", "local": local, "prefixlen": plen, "scope": "global"}]}]


# ---- measure the network ----------------------------------------------------------------------------------------

def test_the_real_network_size_is_read_not_assumed(tmp_path):
    run, _ = fake_ip([{"dev": "wlp2s0", "metric": 600, "prefsrc": "172.16.4.100"}],
                     {"wlp2s0": addr("172.16.4.100", 22)})
    h = hn.home(sys_net=sysnet(tmp_path, "wlp2s0", wireless=True), run=run, which=which)
    assert h["ok"] and h["net"] == "172.16.4.0/22" and h["dev"] == "wlp2s0" and h["kind"] == "Wi-Fi"
    run, _ = fake_ip([{"dev": "eth0", "metric": 100}], {"eth0": addr("10.0.5.20", 24)})
    h = hn.home(sys_net=sysnet(tmp_path / "b", "eth0"), run=run, which=which)
    assert h["net"] == "10.0.5.0/24" and h["kind"] == "wired network"


def test_the_lowest_metric_default_route_is_home(tmp_path):
    net = sysnet(tmp_path, "eth0")
    (net / "wlan0").mkdir()
    (net / "wlan0" / "type").write_text("1")
    run, _ = fake_ip([{"dev": "wlan0", "metric": 600}, {"dev": "eth0", "metric": 100}],
                     {"eth0": addr("172.16.1.5", 24), "wlan0": addr("172.16.9.9", 24)})
    assert hn.home(sys_net=net, run=run, which=which)["dev"] == "eth0"


def test_a_vpn_is_never_home(tmp_path):
    for dev, typ, tun, kind in (("wg0", "65534", False, "wireguard"), ("tun0", "65534", True, "tun"),
                                ("ppp0", "512", False, ""), ("nordlynx", "1", False, "wireguard")):
        run, _ = fake_ip([{"dev": dev, "metric": 50}], {dev: addr("10.8.0.2", 24)},
                         {dev: [{"linkinfo": {"info_kind": kind}}] if kind else [{}]})
        h = hn.home(sys_net=sysnet(tmp_path / dev, dev, typ=typ, tun=tun), run=run, which=which)
        assert not h["ok"] and "VPN" in h["why"], (dev, h)
        p = fw.plan(8226, home=tmp_path, net_home=h, conf=ufw_conf(tmp_path), which=which)
        assert not p["offer"] and not p["argv"]


def test_public_too_wide_and_point_to_point_are_refused(tmp_path):
    for local, plen, word in (("203.0.113.7", 24, "public"), ("100.101.5.6", 24, "public"), ("10.1.2.3", 8, "wider"),
                              ("172.16.1.2", 31, "point")):
        run, _ = fake_ip([{"dev": "eth0", "metric": 1}], {"eth0": addr(local, plen)})
        h = hn.home(sys_net=sysnet(tmp_path / local, "eth0"), run=run, which=which)
        assert not h["ok"] and word in h["why"], h


def test_a_vpn_keeps_the_qr_it_had(monkeypatch):
    monkeypatch.setattr(hn, "home", lambda **kw: {"ok": False, "measured": True, "ip": "", "why": "wg0 is a VPN"})
    monkeypatch.setattr(hn, "route_source_ip", lambda: "172.16.1.9")
    assert hn.home_ip() == "172.16.1.9"


def test_no_ip_tool_falls_back_and_offers_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(hn, "route_source_ip", lambda: "172.16.1.9")
    h = hn.home(which=lambda n: None)
    assert h["ip"] == "172.16.1.9" and not h["ok"] and not h["measured"]
    assert not fw.plan(8226, home=tmp_path, net_home=h, conf=ufw_conf(tmp_path), which=which)["offer"]


# ---- the rule: one port, one network, one connection, IPv4 -------------------------------------------------------

def ufw_conf(tmp_path, on=True):
    p = tmp_path / "ufw.conf"
    p.write_text("ENABLED=%s\n" % ("yes" if on else "no"))
    return p


HOME = {"ok": True, "net": "172.16.4.0/22", "dev": "wlp2s0", "kind": "Wi-Fi", "name": "Home", "ip": "172.16.4.100",
        "measured": True}


def test_the_rule_names_the_phone_port_the_measured_network_and_its_connection_only(tmp_path):
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=ufw_conf(tmp_path), which=which)
    assert p["offer"]
    assert p["argv"] == ["/usr/bin/pkexec", "/usr/sbin/ufw", "allow", "in", "on", "wlp2s0", "from", "172.16.4.0/22",
                         "to", "any", "port", "8226", "proto", "tcp"]
    assert p["where"] == 'Wi-Fi "Home" (172.16.4.0/22 on wlp2s0)'
    assert ":" not in p["net"]                      # IPv4 only: ufw adds no IPv6 rule for an IPv4 source


def test_allow_refuses_anything_wider_than_its_plan(tmp_path):
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=ufw_conf(tmp_path), which=which)
    ran = []
    for bad in (dict(p, argv=p["argv"][:-2]), dict(p, net="0.0.0.0/0", argv=p["argv"][:7] + ["0.0.0.0/0"] + p["argv"][8:]),
                dict(p, dev="-x"), dict(p, argv=p["argv"] + ["port", "8199"])):
        assert not fw.allow(bad, home=tmp_path, run=lambda a, **k: ran.append(a) or R())["ok"]
    assert ran == []


def test_no_firewall_or_no_prompt_offers_no_key(tmp_path):
    assert not fw.plan(8226, home=tmp_path, net_home=HOME, conf=ufw_conf(tmp_path, on=False), which=which)["offer"]
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=ufw_conf(tmp_path),
                which=lambda n: None if n == "pkexec" else which(n))
    assert not p["offer"] and "terminal" in p["why"]
    assert not fw.plan(8226, home=tmp_path, net_home=HOME, conf=ufw_conf(tmp_path), which=which,
                       env={"CRISSA_PHONE_FIREWALL_KEY": "0"})["offer"]


# ---- the undo -------------------------------------------------------------------------------------------------------

def test_allow_then_remove_runs_the_same_rule_through_the_same_prompt(tmp_path):
    conf, ran = ufw_conf(tmp_path), []
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which)
    assert not p["remove"]["offer"]                 # nothing to undo before a press
    done = fw.allow(p, home=tmp_path, run=lambda a, **k: ran.append(a) or R(0, "Rule added"), listening=[])
    assert done["ok"] and done["exposed"] == []
    p2 = fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which)
    assert not p2["offer"] and p2["remove"]["offer"] and p2["remove"]["here"]
    assert fw.remove(p2, home=tmp_path, run=lambda a, **k: ran.append(a) or R(0, "Rule deleted"))["ok"]
    assert ran[1] == ran[0][:2] + ["delete"] + ran[0][2:]
    assert ran[1][0].endswith("pkexec")
    assert not (tmp_path / fw.STATE_NAME).exists()
    assert fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which)["offer"]


def test_remove_only_takes_its_own_rule_and_a_refused_prompt_keeps_it(tmp_path):
    conf = ufw_conf(tmp_path)
    assert not fw.remove(fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which), home=tmp_path,
                         run=lambda a, **k: R())["ok"]
    fw.allow(fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which), home=tmp_path,
             run=lambda a, **k: R(), listening=[])
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which)
    assert not fw.remove(dict(p, remove=dict(p["remove"], argv=p["remove"]["argv"][:3] + ["allow", "22"])),
                         home=tmp_path, run=lambda a, **k: R())["ok"]
    assert not fw.remove(p, home=tmp_path, run=lambda a, **k: R(126, "", "dismissed"))["ok"]
    assert (tmp_path / fw.STATE_NAME).exists()


def test_a_rule_for_another_network_is_named_and_still_removable(tmp_path):
    conf = ufw_conf(tmp_path)
    fw.allow(fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which), home=tmp_path,
             run=lambda a, **k: R(), listening=[])
    cafe = dict(HOME, net="10.20.0.0/24", dev="wlp2s0", name="Cafe")
    p = fw.plan(8226, home=tmp_path, net_home=cafe, conf=conf, which=which)
    assert p["offer"] and p["remove"]["offer"] and not p["remove"]["here"]
    assert "172.16.4.0/22" in p["remove"]["rule"]


# ---- second order: nothing else of Crissa's opens -------------------------------------------------------------------

def test_exposed_names_only_our_non_loopback_listeners_beyond_the_phone_port():
    rows = [("0.0.0.0", 8226, "python3"), ("127.0.0.1", 8199, "python3"), ("::1", 8219, "python3"),
            ("127.0.1.1", 39405, "x"), ("0.0.0.0", 8100, "python3"), ("*", 9000, "y")]
    assert fw.exposed(8226, listening=rows) == [{"address": "0.0.0.0", "port": 8100, "program": "python3"},
                                                {"address": "*", "port": 9000, "program": "y"}]


def test_crissas_other_servers_and_the_control_panel_listen_on_loopback_only():
    """The rule names the phone port alone; every other server the kit starts binds 127.0.0.1, so no rule can reach
    them. A new server binding the network must come here first."""
    src = {"scripts/house-conductor.py": 'ThreadingHTTPServer((HOST, PORT)', "scripts/voiceonly-home-server.py":
           'HOST = os.environ.get("VO_HOME_HOST", "127.0.0.1")', "scripts/door_stamp_server.py":
           'HOST = os.environ.get("DOOR_STAMP_HOST", "127.0.0.1")', "lib/crissa_presence.py":
           'ThreadingHTTPServer(("127.0.0.1", int(port_n))'}
    for f, needle in src.items():
        assert needle in (ROOT / f).read_text(encoding="utf-8"), f
    cond = (ROOT / "scripts/house-conductor.py").read_text(encoding="utf-8")
    assert '\nHOST, PORT = "127.0.0.1",' in cond
    assert '--host", default="0.0.0.0"' in (ROOT / "scripts/phone-app-server.py").read_text(encoding="utf-8")


def test_the_keys_are_this_machines_pages_only():
    cond = (ROOT / "scripts/house-conductor.py").read_text(encoding="utf-8")
    i = cond.index('("/v1/phone/allow", "/v1/phone/remove")')
    assert "_peer_is_loopback(self.client_address) or not connect_origin_ok(self.headers)" in cond[i:i + 900]


# ---- Secure and presence read the phone the same way ---------------------------------------------------------------

def test_the_phone_room_reads_the_session_not_the_address_or_the_firewall(tmp_path):
    import time
    import crissa_phone_room as room
    s = tmp_path / "phone-session.json"
    s.write_text(json.dumps({"active_until": time.time() + 30}))
    before = room.active(s)
    (tmp_path / fw.STATE_NAME).write_text(json.dumps({"ok": True, "net": "172.16.4.0/22", "port": 8226,
                                                      "dev": "wlp2s0"}))
    assert room.active(s) == before is True
    for f in ("lib/crissa_phone_room.py", "lib/phone_session.py", "lib/crissa_presence.py"):
        t = (ROOT / f).read_text(encoding="utf-8")
        assert "crissa_phone_firewall" not in t and "crissa_homenet" not in t, f


# ---- the card and Setup -----------------------------------------------------------------------------------------------

def test_the_card_names_the_network_and_shows_remove(tmp_path):
    import crissa_phone_card as card
    conf = ufw_conf(tmp_path)
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which)
    h = card.firewall_html(p)
    assert "fwkey" in h and "172.16.4.0/22 on wlp2s0" in h and "fwremove" not in h
    fw.allow(p, home=tmp_path, run=lambda a, **k: R(), listening=[])
    h = card.firewall_html(fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which))
    assert "fwremove" in h and fw.REMOVE_LABEL in h and "fwkey" not in h
    assert card.firewall_html({}) == ""


def test_setup_carries_the_mobile_interface_row():
    page = (ROOT / "display/crissa-init.html").read_text(encoding="utf-8")
    assert "phoneRow(d.phone)" in page and "To enable the Mobile interface, allow your phone on your " in page
    assert '"/v1/phone/remove"' in page
    init = (ROOT / "lib/crissa_init.py").read_text(encoding="utf-8")
    assert '"phone": None if person_only else phone_access_view()' in init


def test_this_machine_measures(tmp_path):
    """Live read on the machine the suite runs on: never raises, and a measured answer is a real network."""
    h = hn.home()
    if h["ok"]:
        import ipaddress
        n = ipaddress.IPv4Network(h["net"])
        assert n.is_private and n.prefixlen >= hn.MIN_PREFIX and h["dev"]
    subprocess.run(["true"])
