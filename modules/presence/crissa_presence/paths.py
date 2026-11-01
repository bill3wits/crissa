"""Where the presence module keeps its state: under the Crissa state home, never a path spelled in code (2026-10-09).

The house ran this code from a checked-out repository with a `data/` folder beside `src/`, its records under one
machine's data disk and the identity store at a fixed path. A pack has none of those: every path comes from here.

    HOME       the Crissa state home (lib/crissa_home: CRISSA_HOME, else ~/.crissa)
    ROOT       HOME/presence              this module's own state
    DATA       ROOT/data                  what the repo's data/ held: camera stops, geometry, logs, caches
    CONFIG     the module's shipped config/ (product defaults: camera profile, register token)
    RECORDS    ROOT/records               the rolling raw store, strangers, unlock evidence (was the records disk)
    IDENTITY   HOME/identity              the identity store (HOUSE_ID_ROOT; people/<id>/face/*.json), minted fresh
    HF         HOME/hf                    model cache for the optional describers
    PACK       the install this module sits in (modules/presence/../..)

HOUSE_ID_ROOT still wins for IDENTITY, as it did in the house's units, so an install that keeps its identity store
elsewhere names it in the environment.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _home() -> Path:
    env = os.environ.get("CRISSA_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from . import crissa_home as H          # the copy a pack puts beside this file
        return Path(H.HOME)
    except ImportError:
        pass
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        import crissa_home as H                 # noqa: E402  the source tree's
        return Path(H.HOME)
    except ImportError:
        return Path.home() / ".crissa"


HOME = _home()
ROOT = HOME / "presence"
DATA = ROOT / "data"
RECORDS = ROOT / "records"
HF = HOME / "hf"
PACK = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).resolve().parents[1] / "config"
IDENTITY = Path(os.environ["HOUSE_ID_ROOT"]).expanduser() if os.environ.get("HOUSE_ID_ROOT", "").strip() else HOME / "identity"
MODE_FILE = HOME / "house-mode.json"          # {"auth": {"user_id": ...}} when a mode switch names the User
GPU_MODE_FILE = HOME / "gpu-mode"
AUTH_DAY = HOME / "auth_day"
ENROLL_FILE = HOME / "enroll.json"            # Init's store: {"owner", "faces": [[...], ...]} (numbers only)

for _d in (DATA, RECORDS):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def is_house() -> bool:
    """The house's own install (its avatar manifest names state_home); a pack's copy of crissa_home never does
    (as modules/id/crissa_id/paths.is_house, 2026-10-10)."""
    try:
        from . import crissa_home as H              # the copy a pack puts beside this file
    except ImportError:
        try:
            sys.path.insert(0, str(PACK / "lib"))
            import crissa_home as H                 # noqa: E402  the source tree's
        except ImportError:
            return False
    try:
        return bool(H.is_house())
    except Exception:  # noqa: BLE001
        return False


def cosmos_root() -> str:
    """The Cosmos reasoner's checkout, named in the environment; '' = the describer is not installed here."""
    return os.environ.get("COSMOS_FRAMEWORK_ROOT", "").strip()


def announce_path() -> Path:
    """The announcer helper, when the install carries one (the player's lib/); a missing file is a quiet no-op."""
    return PACK / "lib" / "house_announce.py"
