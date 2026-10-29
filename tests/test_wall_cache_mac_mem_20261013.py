"""A Mac has no /proc/meminfo (2026-10-13): the ASCII cache's mem_available() answered None there, and budget() gave
the kit's desk Wall no memory tier. On darwin it reads vm_stat now: (Pages free + Pages inactive) x page size. The
Linux path is unchanged. Kill: HOUSE_WALL_ASCII_CACHE_MAC_MEM=0.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import wall_ascii_cache as C  # noqa: E402

# A real-looking vm_stat from an Apple-silicon Mac (16384-byte pages).
VM_STAT_OUT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               41285.
Pages active:                            311642.
Pages inactive:                          296114.
Pages speculative:                         9327.
Pages throttled:                              0.
Pages wired down:                        150467.
Pages purgeable:                          10522.
"Translation faults":                1264317591.
Pages copy-on-write:                   41620937.
Pages zero filled:                    602938815.
Pages reactivated:                     19260153.
Pages purged:                           4416340.
File-backed pages:                       201356.
Anonymous pages:                         415727.
Pages stored in compressor:              743520.
Pages occupied by compressor:            237015.
Decompressions:                        42006231.
Compressions:                          57210441.
Pageins:                               11923650.
Pageouts:                                169231.
Swapins:                                3036166.
Swapouts:                               3546418.
"""
WANT = (41285 + 296114) * 16384          # free + inactive, x the page size
MB = 2 ** 20


@pytest.fixture(autouse=True)
def _fresh_reading():
    C._MAC_MEM.update(at=None, val=None)
    yield
    C._MAC_MEM.update(at=None, val=None)


def _no_meminfo(path, *a, **k):
    if str(path) == "/proc/meminfo":
        raise FileNotFoundError(2, "No such file or directory", str(path))
    return open(path, *a, **k)


@pytest.fixture
def mac(monkeypatch, tmp_path):
    """A darwin with no /proc/meminfo and a vm_stat at its absolute path; returns the list of argv vm_stat ran with."""
    exe = tmp_path / "vm_stat"
    exe.write_text("")
    monkeypatch.setattr(C, "VM_STAT", str(exe))
    monkeypatch.setattr(C.sys, "platform", "darwin")
    monkeypatch.setattr(C, "open", _no_meminfo, raising=False)
    monkeypatch.delenv("HOUSE_WALL_ASCII_CACHE_MAC_MEM", raising=False)
    calls: list = []

    def run(argv, **kw):
        calls.append((list(argv), kw))
        return SimpleNamespace(returncode=0, stdout=VM_STAT_OUT, stderr="")

    monkeypatch.setattr(C.subprocess, "run", run)
    return calls


def test_vm_stat_text_gives_free_plus_inactive_times_its_page_size():
    assert C.parse_vm_stat(VM_STAT_OUT) == WANT
    assert C.parse_vm_stat(VM_STAT_OUT.replace("16384", "4096")) == (41285 + 296114) * 4096   # an Intel Mac's pages
    assert C.parse_vm_stat("") is None
    assert C.parse_vm_stat("".join(VM_STAT_OUT.splitlines(True)[1:])) is None      # no page size line
    assert C.parse_vm_stat(VM_STAT_OUT.replace("Pages inactive:", "Pages lazy:")) is None


def test_on_a_mac_mem_available_is_vm_stat_and_the_call_is_spawn_shaped(mac):
    assert C.mem_available() == WANT
    (argv, kw), = mac
    assert argv == [C.VM_STAT] and Path(argv[0]).is_absolute()
    assert kw["timeout"] <= 1.0 and kw["close_fds"] is False and kw["capture_output"] is True


def test_one_reading_serves_a_few_seconds_and_the_next_budget_tick_reads_again(mac):
    assert C.mac_available(now=100.0) == WANT
    assert C.mac_available(now=100.0 + C.MAC_MEM_TTL_S - 0.1) == WANT
    assert len(mac) == 1                                             # held, not read again
    assert C.MAC_MEM_TTL_S < 5.0                                     # budget() is re-read every 5 s: a fresh one
    assert C.mac_available(now=105.0) == WANT and len(mac) == 2


def test_a_vm_stat_that_fails_gives_none_as_before(mac, monkeypatch):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    monkeypatch.setattr(C.subprocess, "run", boom)
    assert C.mem_available() is None
    C._MAC_MEM.update(at=None, val=None)
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="x"))
    assert C.mem_available() is None
    C._MAC_MEM.update(at=None, val=None)
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout="garbage\n"))
    assert C.mem_available() is None


def test_a_failed_reading_is_held_a_minute_so_a_hang_is_not_paid_every_tick(mac, monkeypatch):
    n = []

    def hang(argv, **kw):
        n.append(1)
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    monkeypatch.setattr(C.subprocess, "run", hang)
    assert C.mac_available(now=10.0) is None
    assert C.mac_available(now=10.0 + C.MAC_MEM_FAIL_S - 1) is None and len(n) == 1
    assert C.mac_available(now=10.0 + C.MAC_MEM_FAIL_S) is None and len(n) == 2


def test_no_vm_stat_anywhere_gives_none(mac, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "VM_STAT", str(tmp_path / "absent"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    assert C.mem_available() is None and mac == []


def test_vm_stat_from_path_when_not_at_its_usual_place(mac, monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "vm_stat"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(C, "VM_STAT", str(tmp_path / "absent"))
    monkeypatch.setenv("PATH", str(bindir))
    assert C.mem_available() == WANT and mac[0][0] == [str(exe)]


def test_the_kill_gives_back_none_on_a_mac(mac, monkeypatch):
    monkeypatch.setenv("HOUSE_WALL_ASCII_CACHE_MAC_MEM", "0")
    assert C.mem_available() is None and mac == []
    monkeypatch.delenv("HOUSE_WALL_ASCII_CACHE_MAC_MEM")
    assert C.mem_available({"HOUSE_WALL_ASCII_CACHE_MAC_MEM": "0"}) is None   # budget()'s own env is honoured
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_MAC_MEM": "0"}) == 0         # the kit's Wall: no memory tier, as before
    assert mac == []


def test_budget_on_a_mac_is_the_smaller_of_4096_mb_and_a_tenth_of_available(mac, monkeypatch):
    assert C.budget(0, {}) == int(min(4096 * MB, 0.10 * WANT))       # 5.1 GiB available: 527 MiB
    big = VM_STAT_OUT.replace("Pages inactive:                          296114.",
                              "Pages inactive:                         4000000.")
    monkeypatch.setattr(C.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout=big))
    C._MAC_MEM.update(at=None, val=None)
    assert C.budget(0, {}) == 4096 * MB                              # 66 GB available: the 4096 MB cap
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_MB": "256"}) == 256 * MB


def test_linux_reads_proc_meminfo_as_before_and_never_runs_vm_stat(monkeypatch):
    text = "MemTotal:       131072000 kB\nMemFree:         1000000 kB\nMemAvailable:   48000000 kB\n"
    seen = []

    def fake_open(path, *a, **k):
        seen.append(str(path))
        assert str(path) == "/proc/meminfo"
        return io.StringIO(text)

    def no_run(*a, **k):
        raise AssertionError("vm_stat must not run on Linux")

    monkeypatch.setattr(C, "open", fake_open, raising=False)
    monkeypatch.setattr(C.sys, "platform", "linux")
    monkeypatch.setattr(C.subprocess, "run", no_run)
    assert C.mem_available() == 48000000 * 1024 and seen == ["/proc/meminfo"]
    assert C.budget(0, {"HOUSE_WALL_ASCII_CACHE_RAM_SHARE": "0"}) == 4096 * MB
    assert C.budget(0, {}) == int(0.10 * 48000000 * 1024)             # round 26: 10 % of 125 GiB, held to 10 % of free
    monkeypatch.setattr(C, "open", lambda *a, **k: io.StringIO("MemTotal: 1 kB\n"), raising=False)
    assert C.mem_available() is None                                  # no MemAvailable line: None, as before
    monkeypatch.setattr(C, "open", _no_meminfo, raising=False)
    assert C.mem_available() is None and C.budget(0, {}) == 0          # no /proc/meminfo off a Mac: None, as before
