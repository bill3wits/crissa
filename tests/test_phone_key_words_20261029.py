"""The phone key's words follow the network measured, in one note (the User 2026-10-29, the first-time walk: the key said
"on this Wi-Fi" on a wired network, the card showed "netplan-enp113s0", and its two notes said one rule twice)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

import crissa_homenet as hn  # noqa: E402
import crissa_phone_card as card  # noqa: E402
import crissa_phone_firewall as fw  # noqa: E402
from test_phone_firewall_20261028 import HOME, addr, fake_ip, sysnet, ufw_conf, which  # noqa: E402

WIRED = dict(HOME, dev="enp113s0", kind="wired network", name="", net="172.16.8.0/24", ip="172.16.8.5")


def test_a_wired_network_carries_no_connection_name(tmp_path):
    run, calls = fake_ip([{"dev": "enp113s0", "metric": 100}], {"enp113s0": addr("172.16.8.5", 24)})
    h = hn.home(sys_net=sysnet(tmp_path, "enp113s0"), run=run,
                which=lambda n: "/usr/bin/nmcli" if n == "nmcli" else which(n))
    assert h["kind"] == "wired network" and h["name"] == ""
    assert not any("nmcli" in str(c) for c in calls)


def test_the_key_says_the_kind_of_network(tmp_path):
    conf = ufw_conf(tmp_path)
    assert fw.plan(8226, home=tmp_path, net_home=HOME, conf=conf, which=which)["label"] == fw.LABEL
    p = fw.plan(8226, home=tmp_path, net_home=WIRED, conf=conf, which=which)
    assert p["label"] == "Allow my phone on this wired network" and p["where"] == "wired network (172.16.8.0/24 on enp113s0)"
    h = card.firewall_html(p)
    assert "this wired network" in h and "Wi-Fi" not in h and "only to your <b id=fwwhere>wired network" in h


def test_the_card_says_the_rule_once(tmp_path):
    p = fw.plan(8226, home=tmp_path, net_home=HOME, conf=ufw_conf(tmp_path), which=which)
    h = card.firewall_html(p, p["rule"])
    assert h.count(p["rule"]) == 1 and "no password prompt here? run it in a terminal" in h and "id=ufw" not in h


def test_setup_says_the_network_it_measured():
    page = (ROOT / "display/crissa-init.html").read_text(encoding="utf-8")
    i = page.index("function phoneRow(ph)")
    body = page[i:page.index("\nfunction ", i + 5)]
    assert "on this Wi-Fi." not in body and "ph.label ||" in body
    init = (ROOT / "lib/crissa_init.py").read_text(encoding="utf-8")
    assert '"label": p.get("label") or crissa_phone_firewall.LABEL' in init
