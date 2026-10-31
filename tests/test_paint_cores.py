"""Her paint on cores of its own (lib/paint_cores, the User 2026-10-14)."""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import paint_cores  # noqa: E402

linux_only = pytest.mark.skipif(not hasattr(os, "sched_setaffinity"), reason="no CPU affinity here")


def _topo(tmp_path, n_cores, smt=2):
    """A fake /sys/devices/system/cpu with n_cores physical cores, logical CPU c and c+n_cores on core c."""
    for c in range(n_cores):
        sibs = [c + k * n_cores for k in range(smt)]
        for cpu in sibs:
            d = tmp_path / ("cpu%d" % cpu) / "topology"
            d.mkdir(parents=True, exist_ok=True)
            (d / "thread_siblings_list").write_text(",".join(map(str, sibs)) + "\n")
    return paint_cores.physical_cores(tmp_path)


def test_sixteen_cores_reserve_two_the_highest(tmp_path):
    cores = _topo(tmp_path, 16)
    p = paint_cores.plan({"HOUSE_PAINT_CORES": "auto"}, cores)
    assert p["reserved"] == 2 and p["player"] == {14, 30} and p["wall"] == {15, 31}
    assert p["rest"] == set(range(32)) - {14, 30, 15, 31}


def test_eight_cores_reserve_two_since_round_23(tmp_path):
    # .101 (8 physical cores, 2026-10-15): two paint cores took the composite from 21.9 to 7.3 ms p50
    cores = _topo(tmp_path, 8)
    p = paint_cores.plan({"HOUSE_PAINT_CORES": "auto"}, cores)
    assert p["reserved"] == 2 and p["player"] == {6, 14} and p["wall"] == {7, 15}
    assert len(p["rest"]) == 12


def test_six_cores_reserve_one_for_the_player(tmp_path):
    p = paint_cores.plan({"HOUSE_PAINT_CORES": "auto"}, _topo(tmp_path, 6))
    assert p["reserved"] == 1 and p["player"] == {5, 11} and p["wall"] is None


def test_a_small_machine_reserves_nothing(tmp_path):
    assert paint_cores.plan({"HOUSE_PAINT_CORES": "auto"}, _topo(tmp_path, 4)) is None


def test_a_count_by_hand_and_off(tmp_path):
    cores = _topo(tmp_path, 16)
    assert paint_cores.plan({"HOUSE_PAINT_CORES": "1"}, cores)["reserved"] == 1
    assert paint_cores.plan({"HOUSE_PAINT_CORES": "0"}, cores) is None
    assert paint_cores.plan({"HOUSE_PAINT_CORES": "off"}, cores) is None


def test_unset_is_off_on_the_house_and_auto_in_a_pack(monkeypatch):
    import crissa_home
    monkeypatch.setattr(crissa_home, "is_house", lambda: True)
    assert paint_cores.mode({}) == "off"
    monkeypatch.setattr(crissa_home, "is_house", lambda: False)
    assert paint_cores.mode({}) == "auto"
    assert paint_cores.mode({"HOUSE_PAINT_CORES": "0"}) == "off"


def test_every_lane_unit_runs_on_the_rest(monkeypatch, tmp_path):
    cores = _topo(tmp_path, 16)
    monkeypatch.setattr(paint_cores, "physical_cores", lambda root=None: cores)
    line = paint_cores.unit_line("asr", {"HOUSE_PAINT_CORES": "auto"})
    assert line.startswith("CPUAffinity=") and " 14 " not in (" " + line[12:].strip() + " ")
    assert paint_cores.unit_line("player", {"HOUSE_PAINT_CORES": "auto"}) == line
    assert paint_cores.unit_line("asr", {"HOUSE_PAINT_CORES": "0"}) == ""


def test_the_desk_unit_carries_it_only_when_on(monkeypatch, tmp_path):
    import crissa_service
    cores = _topo(tmp_path, 16)
    monkeypatch.setattr(paint_cores, "physical_cores", lambda root=None: cores)
    on = crissa_service.desk_unit_text("asr", ["/bin/true"], {"HOUSE_PAINT_CORES": "auto"}, home=tmp_path)
    off = crissa_service.desk_unit_text("asr", ["/bin/true"], {"HOUSE_PAINT_CORES": "0"}, home=tmp_path)
    assert "CPUAffinity=" in on and "CPUAffinity=" not in off
    def rest(t):
        return [ln for ln in t.splitlines() if not ln.startswith("CPUAffinity=") and "HOUSE_PAINT_CORES" not in ln]
    assert rest(on) == rest(off)


@linux_only
def test_pin_thread_moves_only_the_calling_thread(monkeypatch):
    every = os.sched_getaffinity(0)
    if len(every) < 4:
        pytest.skip("too few CPUs to reserve one")
    mine = set(sorted(every)[-1:])
    monkeypatch.setattr(paint_cores, "plan", lambda env=None: {"player": mine, "wall": None, "rest": every - mine,
                                                               "reserved": 1})
    seen = {}

    def run():
        seen["msg"] = paint_cores.pin_thread("player", log=None)
        seen["in"] = os.sched_getaffinity(0)
        child = threading.Thread(target=lambda: seen.__setitem__("child", os.sched_getaffinity(0)))
        child.start()
        child.join()
    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert seen["in"] == mine and seen["child"] == mine and seen["msg"]
    assert os.sched_getaffinity(0) == every                # the caller's other threads are untouched


def test_pin_thread_does_nothing_when_off():
    assert paint_cores.pin_thread("player", log=None, env={"HOUSE_PAINT_CORES": "0"}) == ""


def test_the_house_installer_writes_three_files_and_takes_them_back(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("paint_cores_cli", ROOT / "scripts" / "paint-cores.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cores = _topo(tmp_path / "sys", 16)
    monkeypatch.setattr(paint_cores, "physical_cores", lambda root=None: cores)
    monkeypatch.setattr(cli, "MANAGER_DROPIN", tmp_path / "user.conf.d" / "paint-cores.conf")
    monkeypatch.setattr(cli, "UNIT_DROPINS", [tmp_path / "u1.d" / "paint-cores.conf", tmp_path / "u2.d" / "paint-cores.conf"])
    assert cli.main(["install", "--dry"]) == 0 and not cli.MANAGER_DROPIN.exists()
    assert cli.main(["install"]) == 0
    assert "CPUAffinity=0 1 2" in cli.MANAGER_DROPIN.read_text() and " 14 " not in cli.MANAGER_DROPIN.read_text()
    assert all("HOUSE_PAINT_CORES=auto" in d.read_text() for d in cli.UNIT_DROPINS)
    assert cli.main(["uninstall"]) == 0
    assert not cli.MANAGER_DROPIN.exists() and not any(d.exists() for d in cli.UNIT_DROPINS)
