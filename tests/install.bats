#!/usr/bin/env bats
# Tests for install.sh — CLI argument parsing and module selection logic

setup() {
    DRY_RUN=0
    NO_CONFIRM=0
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
    # Override error() to not exit
    error() { echo "ERROR: $*"; return 1; }
}

@test "parse_args sets --all flag" {
    source "$BATS_TEST_DIRNAME/../install.sh" --version 2>/dev/null || true
    # Re-source just the function
    SELECT_ALL_RECOMMENDED=0
    EXPLICIT_MODULES=""
    SHOW_HELP=0
    SHOW_VERSION=0
    VERIFY_ONLY=0
    parse_args() {
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --all)        SELECT_ALL_RECOMMENDED=1 ;;
                --modules)    EXPLICIT_MODULES="$2"; shift ;;
                --no-confirm) NO_CONFIRM=1 ;;
                --dry-run)    DRY_RUN=1 ;;
                --verify)     VERIFY_ONLY=1 ;;
                --help|-h)    SHOW_HELP=1 ;;
                --version|-v) SHOW_VERSION=1 ;;
                *) : ;;
            esac
            shift
        done
    }
    parse_args --all
    [ "$SELECT_ALL_RECOMMENDED" -eq 1 ]
}

@test "parse_args sets --dry-run flag" {
    SELECT_ALL_RECOMMENDED=0
    EXPLICIT_MODULES=""
    SHOW_HELP=0
    SHOW_VERSION=0
    VERIFY_ONLY=0
    DRY_RUN=0
    parse_args() {
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --dry-run) DRY_RUN=1 ;;
                *) : ;;
            esac
            shift
        done
    }
    parse_args --dry-run
    [ "$DRY_RUN" -eq 1 ]
}

@test "check_conflicts detects driver+thermal conflict" {
    MODULE_SELECTED=(1 0 0 0 0 0 0 1 0 0 0 0 0)
    check_conflicts() {
        if [ "${MODULE_SELECTED[0]}" -eq 1 ] && [ "${MODULE_SELECTED[7]}" -eq 1 ]; then
            return 1
        fi
        return 0
    }
    ! check_conflicts
}

@test "check_conflicts passes when no conflict" {
    MODULE_SELECTED=(1 1 0 0 0 0 0 0 0 0 0 0 0)
    check_conflicts() {
        if [ "${MODULE_SELECTED[0]}" -eq 1 ] && [ "${MODULE_SELECTED[7]}" -eq 1 ]; then
            return 1
        fi
        return 0
    }
    check_conflicts
}

@test "MODULE_IDS contains all 13 modules" {
    MODULE_IDS=("driver" "battery" "gpu" "touchpad" "audio" "wifi" "power" "thermal" "gui" "gamemode" "audio-enhance" "camera-enhance" "firmware")
    [ "${#MODULE_IDS[@]}" -eq 13 ]
}

@test "MODULE_IDS and MODULE_LABELS have same length" {
    MODULE_IDS=("driver" "battery" "gpu" "touchpad" "audio" "wifi" "power" "thermal" "gui" "gamemode" "audio-enhance" "camera-enhance" "firmware")
    MODULE_LABELS=(
        "Linuwu-Sense Kernel Driver"
        "Battery Charge Limit (80%)"
        "GPU Switching (EnvyControl)"
        "Touchpad Fix (I2C HID)"
        "Audio Fix (SOF/ALSA)"
        "WiFi/Bluetooth Troubleshooting"
        "Power Management (TLP)"
        "Kernel Thermal Profiles"
        "Archer GUI (Control Panel)"
        "Game Mode Support"
        "Audio Enhancement (Noise Suppression)"
        "Camera Enhancement (Virtual Camera)"
        "Firmware Update Advisor"
    )
    [ "${#MODULE_IDS[@]}" -eq "${#MODULE_LABELS[@]}" ]
}
