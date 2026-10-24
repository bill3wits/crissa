#!/usr/bin/env bash
# Install Crissa in this folder: a Python 3.12 environment (.venv), the Wall's and the modules' requirements, and each
# module's own first-use step (models are fetched on this machine and checked by sha256). Your settings live in
# ~/.crissa, never here. Needs uv (https://docs.astral.sh/uv/).
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "install.sh needs uv: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
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
echo "    CRISSA_INIT=1 .venv/bin/python scripts/crissa.py"
