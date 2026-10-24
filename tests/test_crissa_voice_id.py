"""Voice ID on a fresh install: one engine, one store, the User's print matched (the User 2026-10-10: "ship the voice
print, so a fresh install answers the person's spoken questions").

The engine is modules/ear/crissa_ear/wespeaker_onnx.py, byte for byte in the id and presence modules: WeSpeaker's
published ONNX of the house's ResNet221 checkpoint on onnxruntime, the kaldi fbank in numpy, the model pinned by
revision and sha256 and fetched at install. The Register coach (either copy) writes the User's prints with it into the
identity store (<state home>/identity/people/<id>/voice/), the ear matches with it from the same store, and the bridge
names the person Init made the User (person.json role "user").

The 95 MB model is not in the tree, so the chain below runs on a stand-in session (a fixed projection of the
features' per-bin spread) that separates two synthetic talkers; the real model's numbers are the sandbox proof's and
the clean-install trial's (docs/checkpoints/voice-id-print-20261010). Every chain step runs in its own process with a
scratch state home: the three packages compute their homes at import time. Nothing here reaches a network, a
microphone or the house."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = {
    "ear": ROOT / "modules" / "ear" / "crissa_ear" / "wespeaker_onnx.py",
    "id": ROOT / "modules" / "id" / "crissa_id" / "wespeaker_onnx.py",
    "presence": ROOT / "modules" / "presence" / "crissa_presence" / "wespeaker_onnx.py",
}
COPIES = {
    "id": (ROOT / "modules" / "id", "crissa_id", "crissa_id.house_enroll"),
    "presence": (ROOT / "modules" / "presence", "crissa_presence", "crissa_presence.enroll_panel"),
}


def _engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("voice_id_engine_under_test", ENGINE["ear"])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# A stand-in talker and a stand-in session, shared by the child processes (the prelude) and this one.
PRELUDE = r'''
import json, os, sys, wave
from pathlib import Path
import numpy as np
SR = 16000
def talker(seed, f0, formants, seconds):
    """Harmonic syllables at f0 (a small pitch contour) through formant peaks, short gaps between."""
    rng = np.random.default_rng(seed)
    out, t_total = [], 0.0
    while t_total < seconds:
        d, g = rng.uniform(0.14, 0.26), rng.uniform(0.03, 0.07)
        n = int(d * SR); t = np.arange(n) / SR
        f = f0 * (1 + 0.06 * np.sin(2 * np.pi * rng.uniform(1, 3) * t + rng.uniform(0, 6)))
        ph = 2 * np.pi * np.cumsum(f) / SR
        x = np.zeros(n)
        for k in range(1, int(4000 / f0)):
            amp = sum(np.exp(-((k * f0 - fm) / bw) ** 2) for fm, bw in formants) + 0.02
            x += amp / k ** 0.5 * np.sin(k * ph)
        out += [x * np.hanning(n), np.zeros(int(g * SR))]
        t_total += d + g
    w = np.concatenate(out)
    return (0.3 * w / np.max(np.abs(w))).astype(np.float32)
ANNA = (230, [(800, 120), (1400, 150), (3000, 200)])     # the enrolled talker
OTHER = (95, [(300, 100), (900, 150), (2000, 200)])      # a different one
def pcm(w):
    return (np.clip(w, -1, 1) * 32767).astype(np.int16).tobytes()
def write_wav(path, w):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(SR); f.writeframes(pcm(w))
P = np.random.default_rng(7).standard_normal((80, 256)).astype(np.float32)
class Fake:
    """The engine's session, stood in: a fixed projection of the features' per-bin spread (the real model is 95 MB)."""
    class _I:
        name = "feats"
    def get_inputs(self):
        return [self._I()]
    def run(self, _o, feed):
        s = feed["feats"][0].std(axis=0)
        s = (s - s.mean()) / (s.std() + 1e-6)
        return [(s @ P)[None, :]]
'''


def _child(code: str, home: Path, *paths: Path, env: dict | None = None) -> dict:
    """Run `code` (after the prelude) in a fresh interpreter with CRISSA_HOME=home and `paths` first on sys.path;
    its last stdout line is JSON."""
    pre = PRELUDE + "\n" + "".join("sys.path.insert(0, %r)\n" % str(p) for p in reversed(paths))
    e = {k: v for k, v in os.environ.items() if not k.startswith(("HOUSE_", "RAIL_", "CRISSA_", "ENROLL_"))}
    e.update(CRISSA_HOME=str(home), PYTHONDONTWRITEBYTECODE="1", **(env or {}))
    r = subprocess.run([sys.executable, "-I", "-c", pre + textwrap.dedent(code)], capture_output=True, text=True,
                       env=e, timeout=180, cwd=str(home.parent))
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


# ---- one engine ---------------------------------------------------------------------------------------------------
def test_one_engine_file_in_three_packages():
    a = ENGINE["ear"].read_bytes()
    for name, p in ENGINE.items():
        assert p.read_bytes() == a, "%s's copy of the engine drifted from the ear's" % name


def test_the_engine_needs_numpy_and_onnxruntime_only():
    tree = ast.parse(ENGINE["ear"].read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            assert n.level == 0, "the engine imports nothing package-relative (it is carried in three packages)"
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"__future__", "hashlib", "os", "shutil", "urllib", "pathlib", "typing", "numpy", "onnxruntime"}, mods
    assert "torch" not in mods and "wespeaker" not in mods


def test_the_pin_is_wespeakers_published_onnx_of_the_house_checkpoint():
    W = _engine()
    assert W.MODEL_REPO == "Wespeaker/wespeaker-voxceleb-resnet221-LM" and len(W.MODEL_REV) == 40
    assert W.MODEL_URL == ("https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet221-LM/resolve/"
                           "26732f43821f238bfb258c5ef54924a798e62118/voxceleb_resnet221_LM.onnx")
    assert W.MODEL_SHA256 == "e6b8c14c022288ca186de39f038a9bd9404be4f174ac8a6a3b8dbf229c8f607d"
    assert W.MODEL_BYTES == 95037357 and W.DIM == 256 and "CC BY 4.0" in W.LICENCE
    exp = (ROOT / "scripts" / "crissa-export.py").read_text(encoding="utf-8")
    assert 'VOICE_ID_SHA256 = "%s"' % W.MODEL_SHA256 in exp, "each package's proof checks the same pin"


def test_the_features_are_torchaudios_kaldi_fbank():
    """The house computes the features with torchaudio.compliance.kaldi.fbank; the pack in numpy. Same numbers."""
    torch = pytest.importorskip("torch")
    kaldi = pytest.importorskip("torchaudio.compliance.kaldi")
    W = _engine()
    ns: dict = {}
    exec(PRELUDE, ns)
    for seed, (f0, fm) in ((1, ns["ANNA"]), (2, ns["OTHER"])):
        w = ns["talker"](seed, f0, fm, 3.0)
        w = w + 0.001 * np.random.default_rng(seed).standard_normal(w.size).astype(np.float32)
        ref = kaldi.fbank(torch.from_numpy(w).float().unsqueeze(0), num_mel_bins=80, frame_length=25, frame_shift=10,
                          sample_frequency=16000, window_type="hamming")
        ref = (ref - ref.mean(dim=0)).numpy()
        got = W.fbank(w)
        assert got.shape == ref.shape and got.dtype == np.float32
        assert float(np.max(np.abs(got - ref))) < 2e-3


def test_the_print_is_unit_length_and_short_audio_makes_none():
    W = _engine()
    ns: dict = {}
    exec(PRELUDE, ns)
    W._sessions["stand-in"] = ns["Fake"]()
    v = W.embed_wav16k(ns["talker"](1, *ns["ANNA"], 2.0), "stand-in")
    assert v.shape == (256,) and abs(float(np.linalg.norm(v)) - 1.0) < 1e-5
    assert W.embed_wav16k(np.zeros(7000, dtype=np.float32), "stand-in") is None, "under half a second"
    assert W.embed_wav16k(np.zeros(16000, dtype=np.float32), str(Path("/nonexistent/model.onnx"))) is None
    assert "fetches it" in W.last_error("/nonexistent/model.onnx")


# ---- the fetch, pinned and checked --------------------------------------------------------------------------------
def test_the_fetch_checks_the_sha256_and_leaves_nothing_unchecked(tmp_path, monkeypatch):
    W = _engine()
    good, bad = b"model-bytes " * 100, b"tampered " * 100
    monkeypatch.setattr(W, "MODEL_SHA256", hashlib.sha256(good).hexdigest())
    monkeypatch.setattr(W, "MODEL_BYTES", len(good))
    monkeypatch.delenv("CRISSA_VOICE_ID_MODEL", raising=False)
    monkeypatch.delenv("HOUSE_VOICE_ORT_MODEL", raising=False)
    asked = []

    def fetch(payload):
        def f(url, dest):
            asked.append(url)
            Path(dest).write_bytes(payload)
        return f
    with pytest.raises(RuntimeError, match="does not match its pinned sha256"):
        W.fetch_model(tmp_path, fetch=fetch(bad), log=lambda m: None)
    dest = W.default_model(tmp_path)
    assert not dest.exists() and not list(dest.parent.glob("*.part")), "a bad download leaves no file"
    got = W.fetch_model(tmp_path, fetch=fetch(good), log=lambda m: None)
    assert got["ok"] and dest.read_bytes() == good and asked[-1] == W.MODEL_URL
    assert dest == tmp_path / "models" / "voice-id" / "voxceleb_resnet221_LM.onnx"
    n = len(asked)
    assert W.fetch_model(tmp_path, fetch=fetch(good), log=lambda m: None)["already"] and len(asked) == n
    named = tmp_path / "mine.onnx"
    named.write_bytes(b"x")
    monkeypatch.setenv("CRISSA_VOICE_ID_MODEL", str(named))
    got = W.fetch_model(tmp_path, fetch=fetch(bad), log=lambda m: None)
    assert got == {"ok": True, "path": str(named), "named": True} and len(asked) == n, "a named file is used as is"


@pytest.mark.parametrize("module", ["ear", "id", "presence"])
def test_each_modules_install_fetches_the_one_file_through_its_egress(tmp_path, module):
    """ear --install / --install-voice-id, the id module's --install and the presence module's --install all fetch
    the same file to <state home>/models/voice-id/; the second finds it there."""
    home = tmp_path / "home"
    home.mkdir()
    code = r'''
    import hashlib
    from pathlib import Path
    payload = b"stand-in model " * 64
    def fetch(url, dest):
        Path(dest).write_bytes(payload)
    MOD = %r
    if MOD == "ear":
        from crissa_ear import setup as S, wespeaker_onnx as W
        call = lambda: S.install_voice_id(log=lambda m: None, fetch=fetch)
    elif MOD == "id":
        from crissa_id import setup as S, wespeaker_onnx as W
        call = lambda: S.voice_id_model(log=lambda m: None, fetch=fetch)
    else:
        from crissa_presence import setup as S, wespeaker_onnx as W
        call = lambda: S.voice_id_model(log=lambda m: None, fetch=fetch)
    W.MODEL_SHA256 = hashlib.sha256(payload).hexdigest(); W.MODEL_BYTES = len(payload)
    first, second = call(), call()
    print(json.dumps({"first": first, "second": second, "file": str(W.default_model(os.environ["CRISSA_HOME"]))}))
    ''' % module
    mod_dir = {"ear": ROOT / "modules" / "ear", "id": ROOT / "modules" / "id", "presence": ROOT / "modules" / "presence"}
    got = _child(code, home, mod_dir[module], ROOT / "lib")
    assert got["first"]["ok"] and got["second"].get("already"), got
    assert got["file"] == str(home / "models" / "voice-id" / "voxceleb_resnet221_LM.onnx")
    assert Path(got["file"]).is_file()


def test_the_installers_fetch_the_model_at_install():
    """The model comes with each module's --install: no hand step, no `wespeaker` package, no export."""
    for rel, call in (("modules/ear/crissa_ear/setup.py", "vid = install_voice_id(log=log, fetch=fetch)"),
                      ("modules/id/crissa_id/setup.py", "ws = voice_id_model(log=log)"),
                      ("modules/presence/crissa_presence/setup.py", "vid = voice_id_model(log=log)")):
        src = (ROOT / rel).read_text(encoding="utf-8")
        body = src[src.index("def install("):]
        assert call in body, rel
        assert "wespeaker.load_model" not in src and "torch.onnx.export" not in src, rel


# ---- the id module's router, and the presence module's ----------------------------------------------------------
def test_the_id_router_loads_its_backend_as_a_module_of_its_package(tmp_path):
    """The house loaded wespeaker_embed.py by its path as a top-level module; in a package its `from . import paths`
    then failed ("attempted relative import with no known parent package"), so no fresh install had an encoder."""
    home = tmp_path / "home"
    home.mkdir()
    got = _child(r'''
    from crissa_id import voice_embed
    b = voice_embed.get_backend()
    st = voice_embed.status()
    print(json.dumps({"backend": getattr(b, "__name__", None), "dim": voice_embed.dim(), "status": st}))
    ''', home, ROOT / "modules" / "id", ROOT / "lib")
    assert got["backend"] == "crissa_id.wespeaker_embed" and got["dim"] == 256
    assert "relative import" not in str(got["status"].get("error")), got["status"]
    assert "voxceleb_resnet221_LM.onnx" in str(got["status"].get("error")), "no model yet: it says where it looks"


# ---- enrolment writes the print; the matcher reads it; the bridge names the User --------------------------------
ENROL = r'''
import importlib
MOD_DIR, TOP, PKG = %r, %r, %r
W = importlib.import_module(TOP + ".wespeaker_onnx")
paths = importlib.import_module(TOP + ".paths")
home = Path(os.environ["CRISSA_HOME"])
W._sessions[str(W.default_model(home))] = Fake()                  # the engine's file, stood in
vr = importlib.import_module(PKG + ".voice_register")
vs = importlib.import_module(PKG + ".voice_session")
person = importlib.import_module(PKG + ".person")
roles = importlib.import_module(PKG + ".roles")
identity = home / "identity"
vr.coach_emit = lambda **k: None
voice_dir = person.ensure_person("sam", root=identity) / "voice"
site = vr.Site(root=identity, site_id="crissa", display="This room", raw={"schema": "house_site/v1"})
written = []
for i, (seed, secs, take) in enumerate(((11, 6.0, "seat_front"), (12, 4.0, "seat_off"))):
    wav = voice_dir / ("take-%%d.wav" %% i)
    write_wav(wav, talker(seed, *ANNA, secs - 0.4))
    jp = vr._bank_wav(site, "sam", wav, slot=1 + i, seconds=secs, line="My name is Sam.", pose="front", angle="0",
                      take_id=take, station="desk", mic="@DEFAULT_SOURCE@", out_dir=voice_dir, retry=False,
                      min_active=(0.35 if take == "seat_off" else 0.45))
    written.append(str(jp) if jp else None)
roles.mark_role("sam", "user", root=identity, display="Sam")
recs = [json.loads(Path(p).read_text()) for p in written if p]
print(json.dumps({"written": written, "dims": [r["dim"] for r in recs], "models": [r["model"] for r in recs],
                  "meta": vs.live_encoder_meta(), "person": json.loads((identity / "people" / "sam" / "person.json").read_text())}))
'''
MATCH = r'''
from pathlib import Path
from crissa_ear import settings as ES, wespeaker_onnx as W, voice_embed, voice_bank, stamp, house_id
from crissa_bridge import settings as BS
ES.apply(); BS.apply()
from crissa_bridge import user_mode
home = Path(os.environ["CRISSA_HOME"])
W._sessions[str(voice_embed.ort_model())] = Fake()
text = "Hey Crissa, what is two plus two?"
out = {"identity": str(house_id.house_id_root()), "bank": {k: len(v) for k, v in voice_bank.load_voice_bank().items()},
       "user_ear": house_id.user_id(), "user_bridge": user_mode.user_id_from_mode({}),
       "match_sim": voice_bank.match_sim_for("sam")}
for name, (seed, (f0, fm)) in (("same", (21, ANNA)), ("other", (22, OTHER))):
    emb = voice_embed.embed_pcm16(pcm(talker(seed, f0, fm, 3.2)), sr=16000)
    st = stamp.stamp_turn(text, {}, voice_emb=emb, speech_duration_s=3.2)
    ev = user_mode.evaluate_turn({}, st, look_pending=True)       # "Hey Crissa" sets the look (A); B is the voice
    out[name] = {"person_id": st.get("person_id"), "source": st.get("source"), "sim": st.get("voice_emb_sim"),
                 "reason": ev["auth_reason"], "level": ev["level"], "tools": ev["tools_ok"], "user_voice": ev["user_voice"]}
print(json.dumps(out))
'''


@pytest.mark.parametrize("copy", sorted(COPIES))
def test_a_print_the_coach_writes_is_the_users_voice_to_the_ear(tmp_path, copy):
    """Init's Voice ID (either copy of the coach) banks two takes with the engine into the identity store and makes
    the person the User; the ear then matches the same talker as that User (BAU), and another talker is not the User
    (held: await_voice, as the ear's rules say for an unproven voice after the wake)."""
    mod_dir, top, pkg = COPIES[copy]
    home = tmp_path / "home"
    home.mkdir()
    env = {"CRISSA_NO_CAMERA": "1", "HOUSE_ENROLL_NO_BROWSER": "1"}
    got = _child(ENROL % (str(mod_dir), top, pkg), home, mod_dir, ROOT / "lib", env=env)
    assert all(got["written"]), got
    for p in got["written"]:
        assert Path(p).parent == home / "identity" / "people" / "sam" / "voice" and Path(p).name.startswith("ws_enroll_")
    assert got["dims"] == [256, 256] and all("wespeaker" in m for m in got["models"]), got
    assert got["meta"] == {"model": "wespeaker/english", "dim": 256, "family": "wespeaker"}
    assert got["person"].get("role") == "user"
    m = _child(MATCH, home, ROOT / "modules" / "ear", ROOT / "lib")
    assert m["identity"] == str(home / "identity"), "the ear reads the store the coach wrote"
    assert m["bank"] == {"sam": 2} and m["user_ear"] == "sam" and m["user_bridge"] == "sam"
    assert m["match_sim"] == 0.50
    same, other = m["same"], m["other"]
    assert same["person_id"] == "sam" and same["source"] == "voice_emb" and same["sim"] >= 0.50, same
    assert same["reason"] == "ok" and same["level"] == "bau" and same["tools"] and same["user_voice"], same
    assert other["person_id"] is None and other["sim"] < 0.50, other
    assert other["level"] in ("silent", "guest") and not other["tools"] and not other["user_voice"], other
    assert other["reason"] in ("await_voice", "wrong_voice") or other["reason"].startswith("not_user_voice"), other


def test_with_no_user_enrolled_the_owner_is_the_generic_user(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    got = _child(r'''
    from crissa_ear import settings as ES, house_id
    from crissa_bridge import settings as BS
    ES.apply(); BS.apply()
    from crissa_bridge import user_mode
    print(json.dumps({"env": os.environ.get("HOUSE_USER_ID"), "ear": house_id.user_id(),
                      "bridge": user_mode.user_id_from_mode({}), "root": str(house_id.house_id_root()),
                      "pinned": user_mode.user_id_from_mode({"auth": {"user_id": "Robin"}})}))
    ''', home, ROOT / "modules" / "ear", ROOT / "lib")
    assert got["env"] is None, "the settings no longer pin the owner to 'user'"
    assert got["ear"] == "user" and got["bridge"] == "user" and got["root"] == str(home / "identity")
    assert got["pinned"] == "robin", "house-mode.json still names the User first"


def test_the_threshold_is_the_houses_measured_floor():
    """match_sim 0.50 is the house's measured floor for this engine (871 genuine and 704 impostor trials: 0.35 let
    22.6 % of impostors through, 0.50 let 0.14 %); the bridge's own bar for "this turn is the User" stays 0.48."""
    es = (ROOT / "modules" / "ear" / "crissa_ear" / "settings.py").read_text(encoding="utf-8")
    assert '"match_sim": ("RAIL_VOICE_MATCH_SIM", "0.50"),' in es
    um = (ROOT / "modules" / "ear" / "crissa_bridge" / "user_mode.py").read_text(encoding="utf-8")
    assert 'USER_VOICE_MIN_SIM = float(os.environ.get("HOUSE_USER_VOICE_MIN_SIM", "0.48"))' in um


@pytest.mark.parametrize("copy", sorted(COPIES))
def test_register_starts_on_an_install_without_the_feed_module(tmp_path, copy):
    """vo_sound (the wall's initials) ships with the feed module. On an install without it, Voice ID's /start closed
    the connection ("No module named 'vo_sound'") instead of using the coach's own initials (the proof run,
    2026-10-10)."""
    mod_dir, top, pkg = COPIES[copy]
    home = tmp_path / "home"
    home.mkdir()
    bare = tmp_path / "install"
    (bare / "lib").mkdir(parents=True)
    got = _child(r'''
    import importlib
    person = importlib.import_module(%r + ".person")
    print(json.dumps({"initials": person._glass_initials("Sam Lee", kind="regular")}))
    ''' % pkg, home, mod_dir, env={"HOUSE_RAILS_ROOT": str(bare)})
    assert got["initials"] == "SL"


@pytest.mark.parametrize("module", ["presence", "id"])
def test_a_module_script_runs_in_its_own_env_even_when_both_pythons_are_one_file(tmp_path, module):
    """A venv's bin/python is a symlink to its base Python, so the player's .venv and a module env made from it
    resolve to the same file. reexec_in_env compared those and never re-ran anything: the Register coach ran in the
    player's .venv, which has no onnxruntime, and every voice print came back "EMBED fail" (the voice-print proof run,
    2026-10-10). It decides by sys.prefix now."""
    home = tmp_path / "home"
    home.mkdir()
    got = _child(r'''
    MOD = %r
    if MOD == "presence":
        from crissa_presence import setup as S
        flag = "CRISSA_PRESENCE_IN_ENV"
    else:
        from crissa_id import setup as S
        flag = "CRISSA_ID_IN_ENV"
    py = S.env_python()
    py.parent.mkdir(parents=True)
    py.symlink_to(os.path.realpath(sys.executable))       # the same file as the running Python, as uv's venvs are
    calls = []
    os.execve = lambda exe, argv, env: calls.append((exe, argv[1:], env.get(flag)))
    S.reexec_in_env(["script.py", "--port", "1"])
    first = list(calls)
    calls.clear()
    sys.prefix = str(S.ENV)                                # already in the env: nothing to do
    S.reexec_in_env(["script.py"])
    print(json.dumps({"same_file": os.path.realpath(py) == os.path.realpath(sys.executable), "first": first,
                      "inside": calls, "env": str(S.ENV)}))
    ''' % module, home, ROOT / "modules" / module, ROOT / "lib")
    assert got["same_file"] is True
    assert got["first"] == [[got["env"] + "/bin/python", ["script.py", "--port", "1"], "1"]], got
    assert got["inside"] == [], "inside its own env it does not re-run"
