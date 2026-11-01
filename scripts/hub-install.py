#!/usr/bin/env python3
"""Install a finished store hub as its own avatar: scripts/hub-install.py LIBRARY --key K [--order ID] [--name N] [--replace].
The work is lib/crissa_hub_install.py; OFF unless CRISSA_HUB_INSTALL=1, never on the house."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import crissa_hub_install  # noqa: E402

if __name__ == "__main__":
    sys.exit(crissa_hub_install.main())
