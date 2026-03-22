#!/usr/bin/env bats
# Tests for module interface compliance — all modules must implement required functions

MODULES_DIR="$BATS_TEST_DIRNAME/../modules"

setup() {
    DRY_RUN=1
    NO_CONFIRM=1
    VERBOSE=0
    LOG_FILE=""
    REBOOT_REQUIRED=0
    INSTALLED_FILES=""
    INSTALLED_DKMS=""
    INSTALLED_PACKAGES=""
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
    # Override error() to not exit
    error() { echo "ERROR: $*"; return 1; }
    # Stub out detection variables
    MODEL_FAMILY="nitro"
    HAS_BATTERY=1
    HAS_NVIDIA=1
    HAS_INTEL_IGPU=1
    SUPPORTS_THERMAL_PROFILES=1
    KERNEL_VERSION="6.12.1-arch1-1"
    IS_CLANG_KERNEL=0
    CLANG_BUILD_FLAGS=""
    AUR_HELPER=""
}

@test "driver.sh defines all required functions" {
    source "$MODULES_DIR/driver.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "battery.sh defines all required functions" {
    source "$MODULES_DIR/battery.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "gpu.sh defines all required functions" {
    source "$MODULES_DIR/gpu.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "touchpad.sh defines all required functions" {
    source "$MODULES_DIR/touchpad.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "audio.sh defines all required functions" {
    source "$MODULES_DIR/audio.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "wifi.sh defines all required functions" {
    source "$MODULES_DIR/wifi.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "power.sh defines all required functions" {
    source "$MODULES_DIR/power.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "thermal.sh defines all required functions" {
    source "$MODULES_DIR/thermal.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "gui.sh defines all required functions" {
    source "$MODULES_DIR/gui.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "gamemode.sh defines all required functions" {
    source "$MODULES_DIR/gamemode.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "audio-enhance.sh defines all required functions" {
    source "$MODULES_DIR/audio-enhance.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "camera-enhance.sh defines all required functions" {
    source "$MODULES_DIR/camera-enhance.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "firmware.sh defines all required functions" {
    source "$MODULES_DIR/firmware.sh"
    declare -f module_detect >/dev/null
    declare -f module_install >/dev/null
    declare -f module_uninstall >/dev/null
    declare -f module_verify >/dev/null
}

@test "driver module detects gaming models" {
    source "$MODULES_DIR/driver.sh"
    MODEL_FAMILY="nitro"
    module_detect
    MODEL_FAMILY="predator"
    module_detect
}

@test "driver module skips non-gaming models" {
    source "$MODULES_DIR/driver.sh"
    MODEL_FAMILY="swift"
    ! module_detect
}

@test "battery module detects when battery present" {
    source "$MODULES_DIR/battery.sh"
    HAS_BATTERY=1
    module_detect
}

@test "battery module skips when no battery" {
    source "$MODULES_DIR/battery.sh"
    HAS_BATTERY=0
    ! module_detect
}

@test "thermal module requires kernel 6.8+ and gaming model" {
    source "$MODULES_DIR/thermal.sh"
    SUPPORTS_THERMAL_PROFILES=1
    MODEL_FAMILY="nitro"
    module_detect
}

@test "thermal module skips on old kernel" {
    source "$MODULES_DIR/thermal.sh"
    SUPPORTS_THERMAL_PROFILES=0
    MODEL_FAMILY="nitro"
    ! module_detect
}
