#!/usr/bin/env bash
# crissa-doc-window.sh — float a document over the wall, and get out of the way after.
# (Copied 2026-10-06 from the house's doc-window.sh, which stays as it is: the Control Panel server
#  scripts/crissa-panel-server.py runs this when a document is posted to /v1/screen and when it clears.)
#
# The wall's right side (dial, seat markers, where the speaker is) is the
# interesting part of the screen and nothing may cover it. So a document does
# not paint onto the wall at all: it opens here, in its own sheet-sized window
# on top, and closes itself when the doc clears.
#
#   crissa-doc-window.sh open            # open on the current /v1/doc payload
#   crissa-doc-window.sh close           # close it now
#   crissa-doc-window.sh status
#
# The page at /doc polls and calls window.close() when the doc goes away, so in
# normal use nothing has to call `close`. The pidfile is the backstop for when
# that does not happen — a stuck window is exactly the failure mode this whole
# change exists to prevent.
set -uo pipefail

# The panel hands its own address over (CRISSA_PANEL_URL); by hand, CRISSA_PORT_PANEL or the panel's default.
BASE="${CRISSA_PANEL_URL:-http://127.0.0.1:${CRISSA_PORT_PANEL:-8097}}"
STATE="${CRISSA_HOME:-${HOME}/.crissa}"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/crissa-doc-window-$(id -u).pid"
PROFILE="${STATE}/display-doc"

# 8.5x11 at a size that reads across the room without swallowing the wall.
WIDTH="${VO_DOC_W:-700}"
HEIGHT=$(( WIDTH * 11 / 85 * 10 ))   # 8.5:11 portrait

find_brave() {
  # Any Chromium-family browser with --app: Brave first (the wall's own), then the common ones.
  for c in "${CRISSA_DOC_BROWSER:-}" /opt/brave.com/brave-beta/brave /usr/bin/brave-browser \
           /opt/brave.com/brave/brave /usr/bin/chromium /usr/bin/chromium-browser /usr/bin/google-chrome; do
    [ -n "$c" ] || continue
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

running() {
  [ -f "$PIDFILE" ] || return 1
  local pid; pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

case "${1:-open}" in
  open)
    if running; then echo "doc window already open (pid $(cat "$PIDFILE"))"; exit 0; fi

    # Never open an empty sheet — that is just another thing sitting on the glass.
    if ! curl -sf --max-time 4 "${BASE}/v1/doc" | grep -q '"doc": *"'; then
      echo "no document posted — nothing to show" >&2
      exit 1
    fi

    brave=$(find_brave) || { echo "no Brave/Chromium found (set CRISSA_DOC_BROWSER)" >&2; exit 1; }
    # Its own profile dir: sharing the wall's would let a crash here take the
    # wall's window with it.
    "$brave" \
      --user-data-dir="$PROFILE" \
      --app="${BASE}/doc" \
      --window-size="${WIDTH},${HEIGHT}" \
      --no-first-run --no-default-browser-check \
      --password-store=basic --disable-features=Translate \
      >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    echo "doc window open (pid $(cat "$PIDFILE"), ${WIDTH}x${HEIGHT})"
    ;;

  close)
    if running; then
      pid=$(cat "$PIDFILE")
      kill "$pid" 2>/dev/null
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.3
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
      echo "doc window closed"
    else
      echo "no doc window open"
    fi
    rm -f "$PIDFILE"
    ;;

  status)
    if running; then echo "open (pid $(cat "$PIDFILE"))"; else echo "closed"; fi
    ;;

  *)
    echo "usage: $0 {open|close|status}" >&2
    exit 2
    ;;
esac
