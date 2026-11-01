"""The three faces' reference clips for the cloning voices (Chatterbox-Turbo, Multilingual V3 and Audio8), 2026-10-06.

The Wall speaks three faces: crissa, lila and the announcer. Pocket speaks them with its stock voices (crissa -> eve,
lila -> eve, announcer -> anna; crissa was jane until 2026-10-09, and alba until 2026-10-07, a man's pitch range).
Turbo, Multilingual and Audio8 clone a voice from a reference clip.

CRISSA'S OWN VOICE SHIPS (the User 2026-10-08): her clip is cut from the sound of two of her own Grok Imagine films
(xAI output, owned with the films; credit "Created with Grok"), so it is in the pack, next to this module:
modules/voice/faces/crissa-imagine.wav, its transcript (.txt) and its provenance (.json: the two source films' sha256,
prompts and times, and how the clip was cut). It is no real person's voice. Pocket cannot clone (that needs Kyutai's
gated weights, never used): there she stays eve. If her clip is missing or does not match its sha256, she speaks
from jane's clip (FACE_BACKUP) and the log says so.

LILA'S OWN VOICE SHIPS TOO (the User 2026-10-12: "I can't source Lila's current voice ... swap that so I can source it
properly in what ships"): modules/voice/faces/lila-imagine.wav, cut from the sound of one of the house's own Grok
Imagine films (the drill take behind Crissa's "heads-up harvest"; an English-accented woman's voice that measures far
from Crissa's: 0.16 on the voice-ID model, where two different stock speakers read 0.27-0.56), with its transcript
and provenance beside it. Pocket keeps eve for her; eve's VCTK clip is her backup on the voices that clone. The
announcer has no Imagine-sourced clip: it keeps the SAME speaker's source recording Pocket's anna was made from.

    crissa       crissa_imagine  her Grok Imagine films' sound, 9.18 s     modules/voice/faces/crissa-imagine.wav  Created with Grok
               (backup jane: VCTK speaker p339, sentence 23  kyutai/tts-voices vctk/p339_023_enhanced.wav  CC BY 4.0)
    lila       lila_imagine  a Grok Imagine film's sound, 9.46 s       modules/voice/faces/lila-imagine.wav  Created with Grok
               (backup eve: VCTK speaker p361, sentence 23  kyutai/tts-voices vctk/p361_023_enhanced.wav   CC BY 4.0)
    announcer  anna   VCTK speaker p228, sentence 23  kyutai/tts-voices vctk/p228_023_enhanced.wav    CC BY 4.0

Licences checked 2026-10-06 against the kyutai/tts-voices card: alba-mackenna/ and vctk/ are CC BY 4.0 (commercial
use allowed, with the credit in config/crissa-licences/CREDITS.md); the *_enhanced.wav files are ai-coustics'
cleaned copies of the same VCTK recordings, under the same licence. Expresso and EARS (CC BY-NC 4.0) are never used.
Nothing here comes from the house's own voice clips or any dev machine's voice.

Like the models, the stock clips are fetched on the person's own machine at a pinned revision and checked by sha256
into <crissa home>/voice-faces/; none of them is redistributed in the pack. Crissa's and Lila's own clips are the two
clips that ship, and each is read from the pack where it is, checked by sha256 too. A clip the person puts in an engine's own voices
folder (<face>.wav) wins over the stock one. A face with no clip speaks with the engine's built-in voice, and Init
and the server log say so.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

REPO = "kyutai/tts-voices"
REV = "323332d33f997de8394f24a193e1a76df720e01a"   # 2026-10-06
FACES = ("crissa", "lila", "announcer")
STOCK = {
    # crissa was alba (Alba MacKenna, "casual", alba-mackenna/casual.wav, sha256 46264e83...) until 2026-10-07: that clip
    # measured a median pitch of 136 Hz, a man's range, and .101 heard her as a man. Pocket speaks her as eve
    # (the User 2026-10-09). alba stays an allowed Pocket stock voice.
    # crissa was jane (VCTK p339, vctk/p339_023_enhanced.wav, sha256 2f12e7f1...) from 2026-10-07 to 2026-10-08; jane is
    # her backup now (FACE_BACKUP). Her own clip ships (SHIPPED: read from the pack, never fetched).
    "crissa": {"stock": "crissa_imagine", "shipped": "faces/crissa-imagine.wav", "bytes": 440780,
             "sha256": "bbf638c07031c14ed842351ebb434f96fa078c2b5fe1ef846f0f61e9d907d8fc",
             "text_file": "faces/crissa-imagine.txt", "provenance": "faces/crissa-imagine.json",
             "who": "Crissa's own voice, from the sound of two of her Grok Imagine films (xAI)",
             "licence": "Created with Grok (Crissa media, MEDIA-LICENSE.md)"},
    # lila was eve (VCTK p361, vctk/p361_023_enhanced.wav) until 2026-10-12; eve is her backup now (FACE_BACKUP). Her own
    # clip ships (the User 2026-10-12), cut from a Grok Imagine film's sound like Crissa's.
    "lila": {"stock": "lila_imagine", "shipped": "faces/lila-imagine.wav", "bytes": 454158,
             "sha256": "1af95b61e22ede6285fe4084e98f0b59e0ab36a077369ac09faa868ae2c6cc84",
             "text_file": "faces/lila-imagine.txt", "provenance": "faces/lila-imagine.json",
             "who": "Lila's own voice, from the sound of one of the house's Grok Imagine films (xAI)",
             "licence": "Created with Grok (Crissa media, MEDIA-LICENSE.md)"},
    "announcer": {"stock": "anna", "file": "vctk/p228_023_enhanced.wav", "bytes": 804630,
                  "sha256": "0a6de25cf12bf1540beb85979f306a92be81fecc051c547c5395e7e5237a3856",
                  "who": "VCTK corpus speaker p228, CSTR, University of Edinburgh (kyutai/tts-voices vctk, enhanced)",
                  "licence": "CC BY 4.0"},
}
# Extra clips some engines need. Audio8 registers a voice from a clip AND its transcript: crissa's backup is VCTK p339
# (jane) and lila's is VCTK p361 (eve), whose sentence 23 is the rainbow-passage line.
EXTRA = {
    "eve": {"stock": "eve", "file": "vctk/p361_023_enhanced.wav", "bytes": 671872,
            "sha256": "396e7cbd066b0f3fb6d67fa26e7904076958239d736d4390f15b5fe88feb14cd",
            "who": "VCTK corpus speaker p361, CSTR, University of Edinburgh (kyutai/tts-voices vctk, enhanced)",
            "licence": "CC BY 4.0"},
    "jane": {"stock": "jane", "file": "vctk/p339_023_enhanced.wav", "bytes": 759340,
             "sha256": "2f12e7f155eb3118f55425394f1b049e5b1b67bdc9b3932c8ba4521420aeb84a",
             "who": "VCTK corpus speaker p339, CSTR, University of Edinburgh (kyutai/tts-voices vctk, enhanced)",
             "licence": "CC BY 4.0"},
    # The announcer's proposed second voice (the tier table's announcer.audio8.voice_b_proposed; fetched only once
    # the person names it as voice_b): VCTK p283, a woman from Cork (VCTK's speaker table: F, 24, Irish, Cork).
    "vctk_p283": {"stock": "vctk_p283", "file": "vctk/p283_023_enhanced.wav", "bytes": 748150,
                  "sha256": "b1f3e39bd035da4f4dc400ee27feab100188267c73865a7a6fc5f00a645ed2a8",
                  "who": "VCTK corpus speaker p283 (Irish, Cork), CSTR, University of Edinburgh (kyutai/tts-voices "
                         "vctk, enhanced)", "licence": "CC BY 4.0"},
}
# A face whose own clip cannot be had speaks from this clip instead (before any built-in voice), and says so.
FACE_BACKUP = {"crissa": "jane", "lila": "eve"}
PACK_ROOT = Path(__file__).resolve().parents[1]     # modules/voice: where a SHIPPED clip is
DEFAULT_FACE = "crissa"     # a line with no face (or "default") speaks as crissa, like Pocket's default (jane)


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".crissa"


def folder() -> Path:
    return _home() / "voice-faces"


def _spec(face: str) -> dict:
    return STOCK.get(face) or EXTRA.get(face) or {}


def shipped(face: str) -> bool:
    """True for a clip that ships in the pack (Crissa's own) rather than being fetched."""
    return bool(_spec(face).get("shipped"))


def path(face: str) -> Path:
    s = _spec(face)
    if s.get("shipped"):
        return PACK_ROOT / s["shipped"]
    return folder() / (face + ".wav")


def transcript(face: str) -> str:
    """A shipped clip's transcript ('' for a fetched clip: its engine names its own)."""
    s = _spec(face)
    try:
        return (PACK_ROOT / s["text_file"]).read_text(encoding="utf-8").strip() if s.get("text_file") else ""
    except OSError:
        return ""


def switched_off(face: str) -> bool:
    """Lila's own clip can be switched off (CRISSA_LILA_OWN_VOICE=0, 2026-10-12): she speaks as eve again."""
    return face == "lila" and os.environ.get("CRISSA_LILA_OWN_VOICE", "").strip() == "0"


def _missing(face: str) -> str:
    if switched_off(face):
        return "%s's own clip is switched off (CRISSA_LILA_OWN_VOICE=0)" % face
    return ("%s's own clip is not at %s" % (face, path(face))) if shipped(face) \
        else "%s's reference clip is not in %s" % (face, folder())


def present(face: str) -> bool:
    p, s = path(face), _spec(face)
    if switched_off(face):
        return False
    if not (bool(s) and p.is_file() and p.stat().st_size == s["bytes"]):
        return False
    return not s.get("shipped") or _sha256(p) == s["sha256"]    # the pack's own clip: by its sha256 too


def url(face: str) -> str:
    return "https://huggingface.co/%s/resolve/%s/%s" % (REPO, REV, _spec(face)["file"])


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch_all(fetch, log=print, who: str = "voice", extra: tuple = ()) -> dict:
    """Each face's clip at REV, checked by sha256. {face: ""} on success, {face: why} for a clip that could not be
    had; a wrong download is removed. Never raises: a face without its clip falls back visibly, it never stops an
    install. A face's backup clip (FACE_BACKUP: jane for crissa, eve for lila) is fetched too. A SHIPPED clip (a face's own) is not
    fetched: it is checked where it is in the pack, and never removed."""
    out = {}
    want = list(extra) + [b for b in FACE_BACKUP.values() if b not in extra]
    for face, s in list(STOCK.items()) + [(e, EXTRA[e]) for e in want if e in EXTRA]:
        p = path(face)
        if s.get("shipped") and switched_off(face):
            out[face] = _missing(face)
            log("%s: %s; %s speaks from %s's clip" % (who, out[face], face, FACE_BACKUP.get(face, "the built-in")))
            continue
        if s.get("shipped"):
            try:
                got = _sha256(p)
                out[face] = "" if got == s["sha256"] else ("%s's own clip %s does not match its sha256 (got %s)"
                                                            % (face, p.name, got[:16]))
            except OSError as e:
                out[face] = "%s's own clip is not in the pack (%s: %s)" % (face, p, e)
            if out[face]:
                log("%s: %s; %s speaks from %s's clip" % (who, out[face], face, FACE_BACKUP.get(face, "the built-in")))
            else:
                log("%s: %s speaks with her own voice: %s (%s)" % (who, face, s["who"], s["licence"]))
            continue
        try:
            if p.is_file() and _sha256(p) == s["sha256"]:
                out[face] = ""
                continue
            log("%s: fetching %s's reference clip (%s, %s) …" % (who, face, s["who"], s["licence"]))
            fetch(url(face), p)
            got = _sha256(p)
            if got != s["sha256"]:
                p.unlink()
                raise RuntimeError("it does not match its pinned sha256 (got %s)" % got[:16])
            out[face] = ""
        except Exception as e:  # noqa: BLE001
            out[face] = "%s's reference clip could not be fetched from %s@%s: %s" % (face, REPO, REV[:8], e)
            log("%s: %s; %s speaks with the built-in voice" % (who, out[face], face))
    return out


def clips(user_dir: Path | None) -> dict:
    """{face: clip}: the stock clip of every face present, then every <face>.wav in the engine's own voices folder
    (the person's, which wins). "default" is crissa's."""
    out = {f: path(f) for f in FACES if present(f)}
    for f in FACES:                    # a face whose clip is an EXTRA's same file (fetched by Audio8)
        same = [e for e, x in EXTRA.items() if x.get("file") and x["file"] == STOCK[f].get("file") and present(e)]
        if f not in out and same:
            out[f] = path(same[0])
        if f not in out and FACE_BACKUP.get(f) and present(FACE_BACKUP[f]):   # a face without her own clip: her backup
            out[f] = path(FACE_BACKUP[f])
    if user_dir is not None and Path(user_dir).is_dir():
        for p in sorted(Path(user_dir).glob("*.wav")):
            out[p.stem.lower()] = p
    if "default" not in out and DEFAULT_FACE in out:
        out["default"] = out[DEFAULT_FACE]
    return out


def plan(user_dir: Path | None, builtin: str) -> dict:
    """{face: {"voice", "source", "fallback"}} for crissa, lila and announcer: what each face will speak with, and why
    when it is not its own clip."""
    user = {p.stem.lower(): p for p in sorted(Path(user_dir).glob("*.wav"))} if user_dir and Path(user_dir).is_dir() else {}
    out = {}
    for f in FACES:
        s = STOCK[f]
        if f in user:
            out[f] = {"voice": "your clip %s" % user[f].name, "source": str(user[f]), "fallback": ""}
        elif present(f):
            out[f] = {"voice": "%s's clip (%s, %s)" % (s["stock"], s["who"], s["licence"]), "source": str(path(f)),
                      "fallback": ""}
        elif s.get("file") and any(x.get("file") == s["file"] and present(e) for e, x in EXTRA.items()):
            e = next(e for e, x in EXTRA.items() if x.get("file") == s["file"] and present(e))
            out[f] = {"voice": "%s's clip (%s, %s)" % (s["stock"], s["who"], s["licence"]), "source": str(path(e)),
                      "fallback": ""}
        elif FACE_BACKUP.get(f) and present(FACE_BACKUP[f]):
            b = FACE_BACKUP[f]
            out[f] = {"voice": "%s's clip (%s, %s)" % (b, EXTRA[b]["who"], EXTRA[b]["licence"]), "source": str(path(b)),
                      "fallback": _missing(f)}
        else:
            out[f] = {"voice": builtin, "source": "",
                      "fallback": _missing(f)}
    return out
