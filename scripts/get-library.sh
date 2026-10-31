#!/usr/bin/env bash
# Fetch her library (films, tiles, playlists, the voice clips) into this clone from the release that matches it, and
# check it. usage: scripts/get-library.sh VERSION   (e.g. 1.0.0; CRISSA_LIBRARY_URL overrides the download address)
set -euo pipefail
V=${1:?usage: scripts/get-library.sh VERSION}
cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$0")/."
name=crissa-library-$V.tar.gz
if [ -z "${CRISSA_LIBRARY_URL:-}" ]; then
  origin=$(git remote get-url origin)
  repo=$(printf '%s' "$origin" | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')
  CRISSA_LIBRARY_URL=https://github.com/$repo/releases/download/v$V/$name
fi
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fL --retry 3 -o "$tmp/$name" "$CRISSA_LIBRARY_URL"
curl -fL --retry 3 -o "$tmp/$name.sha256" "$CRISSA_LIBRARY_URL.sha256"
(cd "$tmp" && sha256sum -c "$name.sha256")
tar -xzf "$tmp/$name" -C .
echo "Her library is in place (display/crissa-poses, display/lila-poses, the voice clips). Next: ./install.sh"
