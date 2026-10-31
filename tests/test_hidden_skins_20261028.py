"""The classic face stays in the box but off Choose skin (the User 2026-10-28: "User has Crissa v2 and crissa Mobile and
Lila and no others"; 1.0.1 hides it, 1.0.2 removes it)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import crissa_avatar as A  # noqa: E402


def _tree(tmp_path, hidden):
    for key, rel in A.SHIPPED.items():
        d = tmp_path / rel
        (d / "visemes" / "phoneme-map").mkdir(parents=True, exist_ok=True)
        (d / "inventory.json").write_text("{}")
        (d / "visemes" / "phoneme-map" / "map.json").write_text("{}")
    (tmp_path / A.DEFAULT_SKIN_FILE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / A.DEFAULT_SKIN_FILE).write_text(json.dumps({"key": "none", "hidden": hidden}))
    return tmp_path


def test_a_hidden_face_is_off_the_list_and_cannot_be_picked(tmp_path, monkeypatch):
    monkeypatch.delenv("CRISSA_SKIN_HIDDEN", raising=False)
    root = _tree(tmp_path, [A.DEFAULT_FACE])
    keys = [l["key"] for l in A.libraries(root, tmp_path / "home")]
    assert A.DEFAULT_FACE not in keys and "lila" in keys
    assert A.choose(A.DEFAULT_FACE, root, tmp_path / "home")["ok"] is False


def test_the_fallback_still_wears_it_and_the_kill_lists_it(tmp_path, monkeypatch):
    root = _tree(tmp_path, [A.DEFAULT_FACE])
    face, pose = A.pose_dir(root, tmp_path / "home", None, {})
    assert face == A.DEFAULT_FACE and pose.endswith(A.SHIPPED[A.DEFAULT_FACE])
    monkeypatch.setenv("CRISSA_SKIN_HIDDEN", "0")
    assert A.DEFAULT_FACE in [l["key"] for l in A.libraries(root, tmp_path / "home")]


def test_the_shipped_config_hides_the_classic_face():
    cfg = json.loads((ROOT / A.DEFAULT_SKIN_FILE).read_text())
    assert cfg.get("hidden") == [A.DEFAULT_FACE] and cfg.get("key") == "crissa-desk-v2"
