#!/bin/sh
# Container entrypoint.
#   1. (web page only) remove Chrome lock files left behind by an unclean stop.
#   2. start a virtual display, because the workflow drives a normal (headed) Chrome window.
#   3. hand over to the real command.
set -eu

PROFILE_ROOT="${WA_PROFILE_ROOT:-/data/browser_data}"

# Only the long-running web page cleans up. A one-off command started beside a running
# container must NOT delete the lock that protects the live Chrome profile.
case " $* " in
  *" --ui "*)
    for lock in "$PROFILE_ROOT"/*/SingletonLock "$PROFILE_ROOT"/*/SingletonCookie "$PROFILE_ROOT"/*/SingletonSocket; do
      if [ -e "$lock" ] || [ -L "$lock" ]; then
        rm -f -- "$lock"
        echo "entrypoint: removed a stale Chrome lock file ($(basename "$lock"))" >&2
      fi
    done
    ;;
esac

# Virtual display. Old display files survive a container restart, so clear them first.
export DISPLAY="${DISPLAY:-:99}"
NUMBER="${DISPLAY#:}"
rm -f "/tmp/.X${NUMBER}-lock" "/tmp/.X11-unix/X${NUMBER}"
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &

tries=0
while [ ! -e "/tmp/.X11-unix/X${NUMBER}" ]; do
  tries=$((tries + 1))
  if [ "$tries" -gt 100 ]; then
    echo "entrypoint: the virtual display did not start" >&2
    exit 1
  fi
  sleep 0.1
done

exec "$@"
