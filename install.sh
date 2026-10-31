#!/usr/bin/env bash
# Install Crissa in this folder: a Python 3.12 environment (.venv), the Wall's and the modules' requirements, and each
# module's own first-use step (models are fetched on this machine and checked by sha256). Your settings live in
# ~/.crissa, never here. Fetches uv (https://docs.astral.sh/uv/) into ~/.local/bin when this computer has none.
set -euo pipefail
cd "$(dirname "$0")"
# No download caches left on your disk: pip and uv keep a copy of every wheel they fetch unless told not to
# (measured 2026-10-24: 5.6 GB after a GPU install). A re-run after a failure downloads again; nothing of yours is touched.
export PIP_NO_CACHE_DIR=1 UV_NO_CACHE=1
# uv: fetched here when this computer has none (the User 2026-10-29: "make it the first step in install script"), with
# uv's own installer, into ~/.local/bin - no sudo, and your shell files are left as they are. Only with no curl / wget or
# no network does the install stop, with the link.
if ! command -v uv >/dev/null; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null; then
  echo "Fetching uv (the installer Crissa uses) into ~/.local/bin ..."
  if command -v curl >/dev/null; then curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  elif command -v wget >/dev/null; then wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  fi
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || { echo "install.sh could not fetch uv; install it, then run ./install.sh again: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
fi
[ -f display/crissa-poses/inventory.json ] || { echo "Her library is missing: run scripts/get-library.sh <version> first (the release download already carries it)."; exit 1; }
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
for m in voice oracle feed; do uv pip install --python .venv/bin/python -r requirements/$m.txt; done
for s in modules/oracle/scripts/crissa-oracle.py modules/presence/scripts/crissa-presence.py modules/id/scripts/crissa-id.py \
         modules/ear/scripts/crissa-stt.py modules/voice/scripts/crissa-asr.py modules/voice/scripts/crissa-tts.py \
         scripts/crissa-voice-tier.py modules/render/scripts/crissa-render.py; do
  .venv/bin/python "$s" --install
done
if [ -d phone-packs ]; then                       # the phone module's loops (the library download carries them)
  mkdir -p "$HOME/.crissa/phone/packs" && cp -a phone-packs/. "$HOME/.crissa/phone/packs/"
  echo "Your phone: her phone loops are in ~/.crissa/phone/packs (the menu's Your phone shows the code to scan)."
fi
echo
echo "Crissa is installed. Start her (Setup runs on the first start):"
echo "    .venv/bin/python scripts/crissa.py"
