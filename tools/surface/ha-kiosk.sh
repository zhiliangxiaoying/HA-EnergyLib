#!/bin/bash
# GNOME autostart launcher: open the current dashboard only once it is ready.
set -eu

HA_URL="${ENERGYLIB_KIOSK_URL:-http://localhost:8123/energylib-home/twin}"
LOCK_DIR="${XDG_RUNTIME_DIR:-/tmp}/energylib-kiosk-$(id -u)"
mkdir -p -- "$LOCK_DIR"
chmod 700 -- "$LOCK_DIR"
exec 9>"$LOCK_DIR/launcher.lock"
flock -n 9 || exit 0

printf 'EnergyLIB kiosk: waiting for the dashboard at %s\n' "$HA_URL"
# A working HA root page does not prove that a dashboard route is registered.
# Keep waiting on failures; never open a known error page after a fixed timeout.
until response="$(curl --noproxy '*' --fail --silent --connect-timeout 2 --max-time 5 "$HA_URL")" \
    && printf '%s' "$response" | grep -qi '<home-assistant'; do
    sleep 2
done

printf 'EnergyLIB kiosk: dashboard ready; opening Firefox\n'
exec firefox --kiosk "$HA_URL"
