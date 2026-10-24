"""kill-search for the ear module (lib/crissa_egress.py; the User 2026-10-05).

A pack puts crissa_egress, crissa_net and crissa_home beside this file (config/crissa-packages.json
`vendor`), because this module is installed on its own; the source tree reads them from lib/.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from . import crissa_egress as E              # the copy the pack puts beside this file
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))   # modules/<m>/<pkg> -> lib
    import crissa_egress as E                     # noqa: E402

Refused = E.Refused
check = E.check
refuse_install = E.refuse_install
urlopen = E.urlopen
