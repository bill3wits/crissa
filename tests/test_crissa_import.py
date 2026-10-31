"""The import path (2026-10-01): off by default, never on the house, and a pack only ADDS.
Everything runs in a temp library and a temp state home."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import crissa_import as I  # noqa: E402


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def lib(tmp_path):
    """A one-loop library and an empty state home."""
    pose = tmp_path / "crissa-poses"
    (pose / "visemes" / "phoneme-map" / "tiles").mkdir(parents=True)
    (pose / "visemes" / "phoneme-map" / "tiles" / "old.png").write_bytes(b"old-tile")
    (pose / "clip-work.mp4").write_bytes(b"film")
    (pose / "visemes" / "phoneme-map" / "map.json").write_text(json.dumps(
        {"schema": "m", "n": 1, "entries": [{"tile": "tiles/old.png", "viseme": "AH"}]}))
    (pose / "inventory.json").write_text(json.dumps(
        {"agent": "crissa", "hubs": {"down": "hub-down.png"}, "child_paths": {},
         "pools": {"work": ["work"]}, "clips": {"work": {"file": "clip-work.mp4", "start": "down", "end": "down"}}}))
    return pose, tmp_path / "home"


def make_pack(home: Path, pid: str = "p1", *, files=None, entries=None, rows=None, face="crissa", meta=None):
    src = home / "packs" / "incoming" / pid
    files = {"clip-new.mp4": b"new-film", "visemes/phoneme-map/tiles/new.png": b"new-tile",
             "clip-new.playlist.json": b"{}"} if files is None else files
    for rel, blob in files.items():
        f = src / "files" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(blob)
    m = {"schema": I.SCHEMA, "id": pid, "face": face, "built_from": "test",
         "files": {rel: sha(blob) for rel, blob in files.items()}}
    if entries is not None:
        (src / "map-entries.json").write_text(json.dumps(entries))
        m["map_entries"] = "map-entries.json"
    if rows is not None:
        (src / "inventory-rows.json").write_text(json.dumps(rows))
        m["inventory"] = "inventory-rows.json"
    m.update(meta or {})
    src.mkdir(parents=True, exist_ok=True)
    (src / "pack.json").write_text(json.dumps(m))
    return src


def snapshot(pose: Path) -> dict:
    return {p.relative_to(pose).as_posix(): p.read_bytes() for p in sorted(pose.rglob("*")) if p.is_file()}


def test_off_by_default_and_never_on_the_house(monkeypatch):
    assert I.enabled({}) is False
    monkeypatch.setattr(I.crissa_home, "is_house", lambda *a, **k: True)
    assert I.enabled({"CRISSA_IMPORT": "1"}) is False, "the house's library is built by intake"
    monkeypatch.setattr(I.crissa_home, "is_house", lambda *a, **k: False)
    assert I.enabled({"CRISSA_IMPORT": "1"}) is True
    assert I.status()["on"] is False and I.status()["waiting"] == []


def test_a_pack_adds_files_entries_and_rows(lib):
    pose, home = lib
    make_pack(home, entries=[{"tile": "tiles/new.png", "viseme": "EE"}],
              rows={"clips": {"new": {"file": "clip-new.mp4", "start": "down", "end": "down"}},
                    "pools": {"work": ["new"]}})
    before = snapshot(pose)
    out = I.apply("p1", pose, home, now=1.0)
    assert out["ok"] and out["files_added"] == 3 and out["map_entries_added"] == 1
    assert out["clips_added"] == ["new"] and out["restart_needed"] is True
    assert (pose / "clip-new.mp4").read_bytes() == b"new-film"
    m = json.loads((pose / "visemes/phoneme-map/map.json").read_text())
    assert [e["tile"] for e in m["entries"]] == ["tiles/old.png", "tiles/new.png"] and m["n"] == 2
    inv = json.loads((pose / "inventory.json").read_text())
    assert inv["pools"]["work"] == ["work", "new"] and inv["clips"]["work"] == {"file": "clip-work.mp4", "start": "down", "end": "down"}
    kept = home / "packs" / "installed" / "p1"
    assert (kept / "map.before.json").read_bytes() == before["visemes/phoneme-map/map.json"]
    assert (kept / "inventory.before.json").read_bytes() == before["inventory.json"]
    assert json.loads((kept / "receipt.json").read_text())["add_files"]
    assert not (home / "packs/incoming/p1").exists() and (home / "packs/done/p1/pack.json").is_file()
    for rel, blob in before.items():                       # nothing she had was changed, but the two merges
        if rel not in ("inventory.json", "visemes/phoneme-map/map.json"):
            assert (pose / rel).read_bytes() == blob


def test_tiles_only_needs_a_reload_not_a_restart(lib):
    pose, home = lib
    make_pack(home, files={"visemes/phoneme-map/tiles/new.png": b"t"}, entries=[{"tile": "tiles/new.png"}])
    assert I.apply("p1", pose, home)["restart_needed"] is False


@pytest.mark.parametrize("why, kw", [
    ("leaves the library", {"files": {"../evil.png": b"x"}}),
    ("not a film", {"files": {"run.sh": b"x"}}),
    ("never replaces", {"files": {"inventory.json": b"{}"}}),
    ("never replaces", {"files": {"mouth-dials.json": b"{}"}}),
    ("already has a different", {"files": {"clip-work.mp4": b"other"}}),
    ("is for 'lila'", {"face": "lila"}),
    ("not the one pack.json states", {"meta": {"files": {"clip-new.mp4": "0" * 64}},
                                      "files": {"clip-new.mp4": b"new-film"}}),
    ("places tiles/old.png differently", {"entries": [{"tile": "tiles/old.png", "viseme": "OH"}]}),
    ("tile is not in the pack", {"entries": [{"tile": "tiles/ghost.png"}]}),
    ("a hub this library does not have", {"rows": {"clips": {"n": {"file": "clip-new.mp4", "start": "moon", "end": "down"}}}}),
    ("different clip work", {"rows": {"clips": {"work": {"file": "clip-new.mp4", "start": "down", "end": "down"}}}}),
    ("nothing else", {"rows": {"hubs": {"x": "y"}}}),
    ("no pool", {"rows": {"pools": {"nope": ["work"]}}}),
])
def test_a_refused_pack_writes_nothing(lib, why, kw):
    pose, home = lib
    make_pack(home, **kw)
    before = snapshot(pose)
    with pytest.raises(I.ImportRefused, match=why):
        I.apply("p1", pose, home)
    assert snapshot(pose) == before
    assert (home / "packs/incoming/p1/pack.json").is_file(), "a refused pack stays where it was dropped"


def test_an_unlisted_file_or_a_link_refuses(lib):
    pose, home = lib
    src = make_pack(home)
    (src / "files" / "stowaway.png").write_bytes(b"x")
    with pytest.raises(I.ImportRefused, match="does not list"):
        I.plan("p1", pose, home)
    (src / "files" / "stowaway.png").unlink()
    (src / "files" / "link.png").symlink_to(pose / "inventory.json")
    with pytest.raises(I.ImportRefused, match="link"):
        I.plan("p1", pose, home)


def test_an_identical_file_is_skipped_and_a_second_import_adds_nothing(lib):
    pose, home = lib
    make_pack(home, files={"clip-work.mp4": b"film", "clip-new.mp4": b"n"})
    out = I.apply("p1", pose, home)
    assert out["files_added"] == 1 and out["files_already_there"] == 1
    make_pack(home, "p2", files={"clip-new.mp4": b"n"})
    assert I.apply("p2", pose, home)["files_added"] == 0


def test_a_zip_is_unpacked_and_a_slipping_zip_is_refused(lib):
    pose, home = lib
    src = make_pack(home, "z1")
    arc = home / "packs" / "incoming" / "z2.zip"
    with zipfile.ZipFile(arc, "w") as z:
        for p in src.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    assert "z2" in I.incoming(home)
    assert I.plan("z2", pose, home)["add_files"]
    bad = home / "packs" / "incoming" / "z3.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../../escape.txt", "x")
    I.apply("z2", pose, home)
    assert "z2" not in I.incoming(home) and (home / "packs" / "done" / "z2.zip").is_file(), \
        "an added archive leaves the drop folder with its pack"
    with pytest.raises(I.ImportRefused, match="leaves its folder"):
        I.plan("z3", pose, home)
    assert not (home / "packs" / "escape.txt").exists()


def test_a_pack_id_cannot_walk(lib):
    pose, home = lib
    for pid in ("", "../x", ".hidden", "a/b"):
        with pytest.raises(I.ImportRefused):
            I.plan(pid, pose, home)


def test_the_player_gates_both_routes_on_the_switch_and_refuses_on_air():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/import" and crissa_import.enabled():') == 2, "GET and POST, both behind the switch"
    post = src.split('if head == "/v1/import" and crissa_import.enabled():')[2][:900]
    assert "connect_origin_ok" in post and 'STATE["mouth"].speaking' in post
    assert '"/v1/import"' not in src.split("AUTHORING_POST = frozenset({")[1].split("})")[0], \
        "the import route must work on a player with authoring closed"


def test_an_uploaded_file_lands_in_the_drop_folder_or_is_refused_plainly(lib, tmp_path):
    """THE UPLOAD (the User 2026-10-02): a .zip from the user's computer becomes a waiting pack after the same
    check Add runs; anything else is refused in plain words and leaves nothing behind. Local only."""
    pose, home = lib
    src = make_pack(home, "built")
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        for p in src.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    shutil.rmtree(src)

    def upload(path: Path, name: str):
        part = I.upload_part(home)
        part.write_bytes(path.read_bytes())
        try:
            return I.save_upload(part, name, pose, home)
        finally:
            assert not part.exists(), "the streamed part is always consumed"

    out = upload(good, "Lila Loops-2.zip".replace(" ", "_"))
    assert out["ok"] and out["id"] == "Lila_Loops-2" and out["files"] == 3
    assert "Lila_Loops-2" in I.incoming(home)
    I.apply("Lila_Loops-2", pose, home)                      # the existing Add flow takes over
    assert (pose / "clip-new.mp4").read_bytes() == b"new-film"

    drop = home / "packs" / "incoming"
    before = sorted(p.name for p in drop.iterdir())
    text = tmp_path / "notes.zip"
    text.write_text("not an archive")
    with pytest.raises(I.ImportRefused, match="not a loop pack: it is not a .zip archive"):
        upload(text, "notes.zip")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "hello")
    with pytest.raises(I.ImportRefused, match="not a loop pack this library can take: pack.json"):
        upload(empty, "empty.zip")
    with pytest.raises(I.ImportRefused, match="ending in .zip"):
        upload(good, "loops.rar")
    with pytest.raises(I.ImportRefused, match="rename the file"):
        upload(good, "..sneaky.zip")
    assert sorted(p.name for p in drop.iterdir()) == before, "a refused upload leaves nothing in the drop folder"
    upload(good, "again.zip")
    with pytest.raises(I.ImportRefused, match="already waiting"):
        upload(good, "again.zip")


def test_a_sale_fetch_adds_the_pack_and_remembers_only_its_own_loop(lib, tmp_path):
    pose, home = lib
    called = []

    def boom(url):
        called.append(url)
        raise AssertionError("opener called for " + url)

    sale = "/imagine/store/sale/sale-20261007-0123456789ab/download?k=tok"
    for bad in ("https://evil.example" + sale, "http://127.0.0.1" + sale):
        with pytest.raises(I.ImportRefused):
            I.take_sale(bad, "new", pose, home, opener=boom)
    assert called == []

    src = make_pack(home, "p1", rows={"clips": {"new": {"file": "clip-new.mp4", "start": "down", "end": "down"}},
                                      "pools": {"work": ["new"]}})
    blob = _zip_of(src, tmp_path / "p1.zip")
    shutil.rmtree(src)
    url = "https://store.zoeyos.dev" + sale

    def opener(got):
        called.append(got)
        return 200, "p1.zip", [blob]

    out = I.take_sale(url, "new", pose, home, opener=opener)
    assert called == [url] and out["play"] == "new" and out["clips_added"] == ["new"]
    assert (pose / "clip-new.mp4").read_bytes() == b"new-film"
    assert I.pending_play(home) == "new"

    src2 = make_pack(home, "p2", files={"clip-other.mp4": b"other"},
                     rows={"clips": {"other": {"file": "clip-other.mp4", "start": "down", "end": "down"}},
                           "pools": {"work": ["other"]}})
    blob2 = _zip_of(src2, tmp_path / "p2.zip")
    shutil.rmtree(src2)

    def opener2(got):
        return 200, "p2.zip", [blob2]

    skipped = I.take_sale(url, "ghost", pose, home, opener=opener2)
    assert skipped["clips_added"] == ["other"] and skipped["play"] == ""
    assert I.pending_play(home) == "new"


def _zip_of(src: Path, dest: Path) -> bytes:
    with zipfile.ZipFile(dest, "w") as z:
        for p in src.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    return dest.read_bytes()


def test_the_wall_names_create_and_learn_and_connect_fetches_the_sale():
    wall = (ROOT / "display" / "crissa-wall.html").read_text()
    assert 'id="create"' in wall and 'href="https://store.zoeyos.dev/store/"' in wall and ">Store</a>" in wall
    assert 'id="learn"' in wall and "https://www.skool.com/rocks-cry-out-labs-4621/about" in wall
    assert 'target="_blank"' in wall and 'rel="noopener"' in wall
    assert 'id="director"' not in wall
    assert "allowedTry" in wall and 'v.id = "try-loop"' in wall
    connect = (ROOT / "display" / "crissa-connect.html").read_text()
    assert "/v1/import/fetch" in connect and "It is not playing yet." in connect
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    assert src.count('if head == "/v1/import" and crissa_import.enabled():') == 2
    block = src.split('if head == "/v1/import/fetch" and crissa_import.enabled():', 1)[1].split(
        'if head == "/v1/import" and crissa_import.enabled():', 1)[0]
    assert "take_sale" in block and "sale pack %s play %s" in block
    assert 'body.get("url")' not in block.split("action(", 1)[1]


def test_the_upload_route_is_local_behind_the_import_switch_and_reads_the_file_before_json():
    src = (ROOT / "scripts" / "house-conductor.py").read_text()
    post = src.split("    def do_POST(self) -> None:")[1]
    assert 'upload = head == "/v1/import/upload"' in post
    assert "body = {} if upload else json.loads(self.rfile.read(n)" in post, "the file body is never read as JSON"
    assert post.index("if authoring_refused(head, body):") < post.index("if upload:"), "authoring is refused first"
    fn = src.split("    def _import_upload(self) -> None:")[1].split("\n    def ")[0]
    assert "crissa_import.enabled()" in fn and "connect_origin_ok(self.headers)" in fn
    assert "crissa_import.MAX_BYTES" in fn and "save_upload(part, name, POSE_DIR)" in fn
    for outbound in ("urlopen", "crissa_library_sync", "requests", "http.client"):
        assert outbound not in fn, outbound
    assert '"/v1/import/upload"' not in src.split("AUTHORING_POST = frozenset({")[1].split("})")[0]
    page = (ROOT / "display" / "crissa-connect.html").read_text()
    assert 'id="import-upload"' in page and 'fetch("/v1/import/upload"' in page and 'accept=".zip' in page


def test_a_map_tile_outside_the_library_is_refused(lib):
    pose, home = lib
    make_pack(home, entries=[{"tile": "/etc/passwd", "viseme": "AH"}])
    with pytest.raises(I.ImportRefused, match="leaves the library"):
        I.plan("p1", pose, home)


def test_a_clip_file_outside_the_library_is_refused(lib):
    pose, home = lib
    make_pack(home, rows={"clips": {"evil": {"file": "../secret.mp4", "start": "down", "end": "down"}}})
    with pytest.raises(I.ImportRefused, match="leaves the library"):
        I.plan("p1", pose, home)
