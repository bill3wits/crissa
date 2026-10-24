"""Round 37 (.101 round 36: Setup WROTE +250 on a kit that says it never writes). Setup's first measurement runs in the
enroll process, which never had config/crissa-mouth.json's env (the launcher puts it on the player only). Here
from_setup runs in a process with NO lip-sync variable, on a tree shaped like a kit (no config/house-machine.json): it
must read the kit's file, keep +375 and write nothing."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, sys
sys.path.insert(0, "lib")
import crissa_lipsync as LS
from pathlib import Path
home = Path(sys.argv[1])
res = {"ok": True, "heard_ms": 202.6, "ear_ms": 182.6, "lines": 3, "of": 3}
rep = LS.from_setup(res, home=home, base_ms=104.167, glass={"who": "wall-tk", "median_ms": 282.3})
print(json.dumps({"outcome": rep["outcome"], "log": rep["log"], "file": LS.path(home).exists(),
                  "offset": LS.offset_ms(home), "writes": LS.setup_writes_on(), "start": LS.default_ms()}))
"""


def _kit_tree(tmp: Path, with_machine: bool = False) -> Path:
    tree = tmp / "tree"
    (tree / "lib").mkdir(parents=True)
    (tree / "config").mkdir()
    for name in ("crissa_lipsync.py", "crissa_home.py"):
        shutil.copy(ROOT / "lib" / name, tree / "lib" / name)
    shutil.copy(ROOT / "config" / "crissa-mouth.json", tree / "config" / "crissa-mouth.json")
    if with_machine:
        (tree / "config" / "house-machine.json").write_text("{}")
    return tree


def _run(tree: Path, home: Path, extra: dict | None = None) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "CRISSA_HOME": str(home)}
    env.update(extra or {})
    out = subprocess.run([sys.executable, "-c", PROBE, str(home)], cwd=str(tree), env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_process_without_the_env_reads_the_kit_file_and_writes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    got = _run(_kit_tree(tmp_path), home)
    assert got["start"] == 375.0 and got["writes"] is False
    assert got["outcome"] == "measured" and got["file"] is False and got["offset"] == 375.0
    assert got["log"].startswith("lip sync: Setup measured +") and ", keeping +375 ms (" in got["log"]


def test_the_source_tree_and_the_kill_keep_round_36(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    got = _run(_kit_tree(tmp_path, with_machine=True), home)          # the house's tree: the code defaults
    assert got["start"] == 0.0 and got["writes"] is True and got["outcome"] == "set"
    home2 = tmp_path / "home2"
    home2.mkdir()
    got = _run(_kit_tree(tmp_path / "k"), home2, {"CRISSA_LIPSYNC_KIT_FILE": "0"})
    assert got["start"] == 0.0 and got["writes"] is True and got["outcome"] == "set"
