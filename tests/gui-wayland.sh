#!/usr/bin/env bash
# Exercise real GTK layout on a private headless Wayland compositor.
set -euo pipefail
if [[ ${1:-} == --client ]]; then
    status=0
    GDK_BACKEND=wayland GSK_RENDERER=ngl python "$ARCHER_GUI_SUITE" --capture || status=$?
    printf '%s\n' "$status" >"$XDG_RUNTIME_DIR/archer-test-status"
    swaymsg exit >/dev/null 2>&1 || true
    exit "$status"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export XDG_RUNTIME_DIR="$work" XDG_CONFIG_HOME="$work/config"
export ARCHER_WAYLAND_TEST
ARCHER_WAYLAND_TEST=$(realpath "${BASH_SOURCE[0]}")
export ARCHER_GUI_SUITE="${ARCHER_WAYLAND_TEST%/*}/gui_test.py"
export ARCHER_CAPTURE_DIR=${ARCHER_CAPTURE_DIR:-/tmp/archer-wayland-captures}
mkdir -p "$XDG_CONFIG_HOME" "$ARCHER_CAPTURE_DIR"
cat >"$work/sway.conf" <<'CONFIG'
xwayland disable
output HEADLESS-1 resolution 1920x1200 position 0 0
default_border none
for_window [shell="xdg_shell"] floating enable
exec bash "$ARCHER_WAYLAND_TEST" --client
CONFIG
unset DISPLAY WAYLAND_DISPLAY SWAYSOCK DBUS_SESSION_BUS_ADDRESS
export WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
dbus-run-session -- timeout 300 sway -c "$work/sway.conf"
[[ -f "$work/archer-test-status" && $(<"$work/archer-test-status") == 0 ]]
