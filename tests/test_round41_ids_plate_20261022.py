"""Round 41 (.101 round 40, the User). D8: an IDs run's one exit and the corner tile read Cancel ("Just Cancel would be
simplest"; kill CRISSA_EXIT_SAYS_CANCEL=0). D6: an IDs run's done plate is the person's result only - no microphone key,
no Start Crissa when I log in (kill CRISSA_IDS_DONE_PERSON_ONLY=0)."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import init_v2_render as V  # noqa: E402
import crissa_init as zi  # noqa: E402

PAGE = (ROOT / "display" / "crissa-init.html").read_text(encoding="utf-8")


def _tile_words(monkeypatch):
    monkeypatch.setattr(V.R.crissa_home, "her_name", lambda paths=None: "Crissa")
    said = []
    real = V._key
    monkeypatch.setattr(V, "_key", lambda d, hits, name, x, y, label, *a, **k: said.append((name, label)) or real(
        d, hits, name, x, y, label, *a, **k))
    snap = {"status": "running", "step_at": 0, "walker": "crissa", "index": 0, "total": 6, "ids": {"who": "new"},
            "plate": {"kind": "machine", "title": "This computer", "eyebrow": "Setup"},
            "browser": {"led": True, "own": True, "window": {"up": False, "setup": False}}}
    V.render(types.SimpleNamespace(W=1920, H=1080, u=1920 / 88.0, page_left=8.0), snap, {})
    return dict(said)


def test_d8_the_one_exit_reads_cancel(monkeypatch):
    monkeypatch.delenv("CRISSA_EXIT_SAYS_CANCEL", raising=False)
    assert _tile_words(monkeypatch)["init2_ids_back"] == "CANCEL"
    assert zi.exit_says_cancel_on() and zi.exit_says_cancel_on({"CRISSA_EXIT_SAYS_CANCEL": "0"}) is False
    assert 'if (snap.ids) $("op-cancel").textContent = exitLabel(snap);' in PAGE
    assert '$("way-out-cancel").firstChild.textContent = snap.ids ? exitLabel(snap)' in PAGE
    assert 'return (snap && snap.exit_label) || "Back to the Wall";' in PAGE, "an older player: round 40's words"
    src = (ROOT / "lib" / "crissa_init.py").read_text(encoding="utf-8")
    assert '"exit_label": "Cancel" if exit_says_cancel_on() else "Back to the Wall",' in src
    monkeypatch.setenv("CRISSA_EXIT_SAYS_CANCEL", "0")
    assert _tile_words(monkeypatch)["init2_ids_back"] == "BACK TO THE WALL", "the kill"


def test_d6_an_ids_runs_done_plate_is_the_persons_result(monkeypatch):
    monkeypatch.setattr(zi, "mic_state", lambda: {"on": True})
    monkeypatch.setattr(zi, "login_view", lambda: {"on": False})
    monkeypatch.setattr(zi, "connect_record", lambda st: {"state": "tested"})
    monkeypatch.setattr(zi, "desk_present", lambda home=None: True)
    monkeypatch.delenv("CRISSA_IDS_DONE_PERSON_ONLY", raising=False)
    st = {"ids": {"who": "new"}, "register": {"state": "done", "display": "Ted", "voice": 4, "face": 4},
          "hours": {"state": "saved", "overnight": [21, 4]}, "room": {"state": "saved"}}
    v = zi.done_view(st)
    assert v["person_only"] is True and v["lines"] == ["Voice and Face ID: Ted, 4 voice prints, 4 face photos."]
    first = zi.done_view({k: v_ for k, v_ in st.items() if k != "ids"})
    assert first["person_only"] is False and any(ln.startswith("Agent:") for ln in first["lines"]), "Setup's plate as before"
    monkeypatch.setenv("CRISSA_IDS_DONE_PERSON_ONLY", "0")
    assert zi.done_view(st)["person_only"] is False, "the kill"
    plate = PAGE.split("function donePlate(snap) {", 1)[1].split("\n}\n", 1)[0]
    assert "d.person_only ? null : micRow," in plate and "d.person_only ? null : loginRow(d.login)," in plate
    assert 'd.person_only ? null : el("p", { id: "mic-state"' in plate
