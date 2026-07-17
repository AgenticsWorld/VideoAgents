#!/usr/bin/env bash
# Launch the user's own Google Chrome with CDP on 127.0.0.1:9222 for Xiaohongshu drafting.
#
# Usage:
#   launch_cdp_chrome.sh [PROFILE_DIR] [PORT]
#
# Browser  : the locally installed Google Chrome (the one the user normally uses),
#            NOT the project's built-in CDP Chromium. Override with $CHROME_PATH.
# Profile  : a DEDICATED automation profile (default:
#            ~/Google/Chrome/XiaohongshuProfiles/default — same dir the
#            XiaohongshuSkills account_manager uses on macOS/Linux), so:
#            - the user's daily Chrome profile is never touched;
#            - the XHS login session persists there after the first QR scan;
#            - it can run side-by-side with the user's normal Chrome windows.
#            NOTE: CDP cannot attach to an already-running Chrome that was not
#            started with a debug port, and one profile cannot be opened by two
#            instances — hence the dedicated profile + our own instance.
#
# Key flags:
#   --remote-allow-origins=*  : required, or Chrome 136+ (this machine: 150)
#                               rejects the CDP WebSocket handshake.
set -euo pipefail

PROFILE_DIR="${1:-$HOME/Google/Chrome/XiaohongshuProfiles/default}"
PORT="${2:-9222}"

find_chrome() {
  if [[ -n "${CHROME_PATH:-}" && -x "${CHROME_PATH:-}" ]]; then
    echo "$CHROME_PATH"; return 0
  fi
  local candidates=(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    "/usr/bin/google-chrome"
    "/usr/bin/google-chrome-stable"
  )
  local c
  for c in "${candidates[@]}"; do
    if [[ -x "$c" ]]; then echo "$c"; return 0; fi
  done
  return 1
}

EXE="$(find_chrome)" || { echo "ERROR: Google Chrome not found; set CHROME_PATH." >&2; exit 1; }

# Reuse if already up.
if curl -s --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "[launch] CDP already up on ${PORT}; reusing."
  exit 0
fi

if [[ -f "${PROFILE_DIR}/SingletonLock" ]]; then
  echo "[launch] WARNING: ${PROFILE_DIR}/SingletonLock exists — another instance may hold this profile." >&2
  echo "[launch]          If no such Chrome is running, delete the lock file and retry." >&2
fi

mkdir -p "$PROFILE_DIR"
echo "[launch] chrome  : $EXE"
echo "[launch] profile : $PROFILE_DIR"
echo "[launch] port    : $PORT"
nohup "$EXE" \
  --remote-debugging-port="${PORT}" \
  --user-data-dir="${PROFILE_DIR}" \
  '--remote-allow-origins=*' \
  --no-first-run --no-default-browser-check \
  --disable-blink-features=AutomationControlled \
  >/tmp/chrome_cdp_${PORT}.log 2>&1 &
echo "[launch] pid $!"

for i in $(seq 1 20); do
  if curl -s --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "[launch] CDP UP after ${i}s on 127.0.0.1:${PORT}"
    exit 0
  fi
  sleep 1
done
echo "[launch] WARNING: CDP port ${PORT} not responding after 20s; check /tmp/chrome_cdp_${PORT}.log" >&2
exit 1
