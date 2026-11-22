#!/usr/bin/env bash
# Canonical module registry for Archer Compatibility Suite.
# Sourced by install.sh and uninstall.sh so both validate against the same
# allowlist and order. Adding a new module: append to MODULE_IDS *and*
# MODULE_LABELS at the same index.

MODULE_IDS=("driver" "battery" "gpu" "touchpad" "audio" "wifi" "power" "thermal" "gui" "gamemode" "audio-enhance" "camera-enhance" "firmware")
MODULE_LABELS=(
    "Linuwu-Sense Kernel Driver"
    "Battery Charge Policy"
    "Graphics Driver and Mode Plan"
    "Targeted Input Repair"
    "Audio Fix (SOF/ALSA)"
    "Targeted Connectivity Repair"
    "Power Policy Integration"
    "Kernel Thermal Profiles"
    "Archer GUI (Control Panel)"
    "Game Mode Support"
    "Audio Enhancement (Noise Suppression)"
    "Virtual Camera Output and Session Effects"
    "Firmware Update Advisor"
)

# Return 0 iff $1 is a valid, known module ID. Rejects path-traversal attempts
# and shell metacharacters before the allowlist check.
is_known_module() {
    local id="$1"
    [[ "$id" =~ ^[a-z][a-z0-9_-]+$ ]] || return 1
    local m
    for m in "${MODULE_IDS[@]}"; do
        [[ "$m" == "$id" ]] && return 0
    done
    return 1
}
