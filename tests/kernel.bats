#!/usr/bin/env bats
setup() {
    export KERNEL_MODULES_ROOT="$BATS_TEST_TMPDIR/modules"
    export KERNEL_SYS_ROOT="$BATS_TEST_TMPDIR/sys"
    export KERNEL_PROC_VERSION="$BATS_TEST_TMPDIR/version"
    mkdir -p "$KERNEL_MODULES_ROOT" "$KERNEL_SYS_ROOT"
    echo 'Linux version (gcc)' > "$KERNEL_PROC_VERSION"
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    warn() { echo "$*"; }
    debug() { :; }
    pacman() { return 1; }
    uname() { echo "$TEST_RELEASE"; }
    TEST_RELEASE=7.2.3-arch1-1
}

@test "numeric releases cover 6.x 7.0 7.1 7.2 7.10 and 8.0 with all suffixes" {
    for spec in '6.12.63-arch1-1:6:12' '6.18.1-lts:6:18' '7.0.0-arch1-1:7:0' '7.1.8-zen:7:1' '7.2.3-cachyos:7:2' '7.10.2-cachyos-lts:7:10' '8.0.0-hardened:8:0'; do
        IFS=: read -r TEST_RELEASE major minor <<< "$spec"
        detect_kernel
        [ "$KERNEL_MAJOR" -eq "$major" ]
        [ "$KERNEL_MINOR" -eq "$minor" ]
        kernel_at_least "$TEST_RELEASE" "$major" "$minor"
    done
    kernel_at_least 8.0.0 7 10
    kernel_at_least 7.10.0 7 2
    ! kernel_at_least 7.1.99 7 2
    ! kernel_at_least 6.99.0 7 0
    ! kernel_at_least garbage 6 8
}

@test "future kernel does not imply a thermal capability" {
    TEST_RELEASE=8.0.0-custom
    detect_kernel
    [ "$SUPPORTS_THERMAL_PROFILES" -eq 0 ]
    mkdir -p "$KERNEL_SYS_ROOT/class/platform-profile/provider"
    echo balanced > "$KERNEL_SYS_ROOT/class/platform-profile/provider/profile"
    echo balanced > "$KERNEL_SYS_ROOT/class/platform-profile/provider/choices"
    TEST_RELEASE=6.6.1-backport
    detect_kernel
    [ "$SUPPORTS_THERMAL_PROFILES" -eq 1 ]
}

@test "headers follow exact pkgbase for Arch variants Manjaro and custom kernels" {
    for package in linux linux-lts linux-zen linux-hardened linux-cachyos linux-cachyos-bore linux-cachyos-lts linux-cachyos-eevdf linux612 linux72 my-custom-kernel; do
        mkdir -p "$KERNEL_MODULES_ROOT/$TEST_RELEASE"
        echo "$package" > "$KERNEL_MODULES_ROOT/$TEST_RELEASE/pkgbase"
        [ "$(kernel_header_package "$TEST_RELEASE")" = "$package-headers" ]
    done
}

@test "removed CachyOS kernel never selects first installed package" {
    TEST_RELEASE=7.2.0-cachyos
    pacman() { [[ "$1" == -Q ]] && echo 'linux-cachyos-lts 6.18'; return 1; }
    ! kernel_header_package "$TEST_RELEASE"
    run kernel_validate_headers "$TEST_RELEASE"
    [ "$status" -ne 0 ]
    [[ "$output" == *'reboot into the installed kernel'* ]]
}

@test "running kernel mismatch builds installed targets and requires reboot" {
    mkdir -p "$KERNEL_MODULES_ROOT/7.2.4-custom/build/include/config"
    touch "$KERNEL_MODULES_ROOT/7.2.4-custom/build/Makefile" "$KERNEL_MODULES_ROOT/7.2.4-custom/build/.config"
    echo 7.2.4-custom > "$KERNEL_MODULES_ROOT/7.2.4-custom/build/include/config/kernel.release"
    kernel_collect_targets
    [ "${KERNEL_TARGETS[*]}" = 7.2.4-custom ]
}

@test "missing header metadata and mismatched headers fail" {
    mkdir -p "$KERNEL_MODULES_ROOT/$TEST_RELEASE/build/include/config"
    touch "$KERNEL_MODULES_ROOT/$TEST_RELEASE/build/Makefile" "$KERNEL_MODULES_ROOT/$TEST_RELEASE/build/.config"
    ! kernel_validate_headers "$TEST_RELEASE"
    echo 7.1.9 > "$KERNEL_MODULES_ROOT/$TEST_RELEASE/build/include/config/kernel.release"
    ! kernel_validate_headers "$TEST_RELEASE"
    echo "$TEST_RELEASE" > "$KERNEL_MODULES_ROOT/$TEST_RELEASE/build/include/config/kernel.release"
    kernel_validate_headers "$TEST_RELEASE"
}

@test "battery uses native threshold without DKMS even on an old kernel" {
    source "$BATS_TEST_DIRNAME/../modules/battery.sh"
    local supply="$KERNEL_SYS_ROOT/class/power_supply/BAT2"
    mkdir -p "$supply"
    echo Battery > "$supply/type"
    echo 100 > "$supply/charge_control_end_threshold"
    _BATTERY_UDEV_RULE="$BATS_TEST_TMPDIR/rule"
    INSTALLED_FILES=''
    run_sudo() { if [[ "$1" == udevadm ]]; then return 0; fi; "$@"; }
    external_install_dkms() { echo 'Unexpected DKMS install'; return 1; }
    log() { :; }
    module_install
    [ "$(cat "$supply/charge_control_end_threshold")" = 80 ]
    [[ "$INSTALLED_FILES" == *"$_BATTERY_UDEV_RULE"* ]]
    module_verify
}

@test "battery DKMS failure propagates before loading or persistence" {
    source "$BATS_TEST_DIRNAME/../modules/battery.sh"
    _BATTERY_HEALTH_PATH="$BATS_TEST_TMPDIR/absent"
    _BATTERY_LOAD_CONF="$BATS_TEST_TMPDIR/load"
    external_install_dkms() { return 17; }
    run module_install
    [ "$status" -ne 0 ]
    [ ! -e "$_BATTERY_LOAD_CONF" ]
}

@test "compatibility series is selected in order and preparation is idempotent" {
    local root="$BATS_TEST_TMPDIR/project" upstream="$BATS_TEST_TMPDIR/upstream"
    mkdir -p "$root/driver/test/patches" "$root/lib" "$upstream"
    cp "$BATS_TEST_DIRNAME/../driver/prepare.sh" "$root/driver/"
    cp "$BATS_TEST_DIRNAME/../driver/build.sh" "$root/driver/"
    cp "$BATS_TEST_DIRNAME/../lib/kernel.sh" "$root/lib/"
    git -C "$upstream" init -q
    echo 'original' > "$upstream/test.c"
    git -C "$upstream" add test.c
    git -C "$upstream" -c user.email=test@example.invalid -c user.name=Test commit -qm baseline
    local revision
    revision=$(git -C "$upstream" rev-parse HEAD)
    cat > "$root/driver/test/source.conf" <<CONF
DRIVER_REVISION="$revision"
DKMS_NAME=test
DKMS_VERSION=1.archer1
DRIVER_MODULE=test
SOURCE_FILE=test.c
MODULE_LOCATION=.
CONF
    printf '%s\n' '--- a/test.c' '+++ b/test.c' '@@ -1 +1 @@' '-original' '+compatible' > "$root/driver/test/patches/fix.patch"
    echo fix.patch > "$root/driver/test/patches/series"
    bash "$root/driver/prepare.sh" "$root/driver/test" "$root/result" "$upstream"
    bash "$root/driver/prepare.sh" "$root/driver/test" "$root/result" "$upstream"
    [ "$(head -1 "$root/result/test.c")" = compatible ]
    echo changed >> "$root/result/test.c"
    run bash "$root/driver/prepare.sh" "$root/driver/test" "$root/result" "$upstream"
    [ "$status" -ne 0 ]
    echo missing.patch > "$root/driver/test/patches/series"
    run bash "$root/driver/prepare.sh" "$root/driver/test" "$root/failed" "$upstream"
    [ "$status" -ne 0 ]
    [ ! -e "$root/failed" ]
}

@test "camera DKMS failure does not write module-load configuration" {
    source "$BATS_TEST_DIRNAME/../modules/camera-enhance.sh"
    _CAMERA_DKMS_CONF="$BATS_TEST_TMPDIR/dkms/v4l2loopback.conf"
    _MODPROBE_CONF="$BATS_TEST_TMPDIR/modprobe.conf"
    local camera_fixture_config="$BATS_TEST_TMPDIR/dkms.conf"
    echo 'PACKAGE_VERSION="0.15.4"' > "$camera_fixture_config"
    DRY_RUN=0 INSTALLED_FILES='' INSTALLED_PACKAGES=''
    kernel_collect_targets() { KERNEL_TARGETS=(7.2.3-test); }
    log() { :; }
    pacman() { [[ "$1" != -Qlq ]] || echo "$camera_fixture_config"; }
    run_sudo() { "$@"; }
    dkms() { return 11; }
    run module_install
    [ "$status" -ne 0 ]
    [[ "$output" == *'v4l2loopback/0.15.4 failed for 7.2.3-test'* ]]
    [ ! -e "$_MODPROBE_CONF" ]
}

@test "v4l2loopback DKMS override selects tools per target on future rebuilds" {
    local kernel_source_dir="$BATS_TEST_TMPDIR/headers"
    mkdir -p "$kernel_source_dir"
    echo CONFIG_CC_IS_CLANG=y > "$kernel_source_dir/.config"
    source "$BATS_TEST_DIRNAME/../driver/v4l2loopback-dkms.conf"
    [[ "${MAKE[0]}" == *"KERNEL_DIR=$kernel_source_dir LLVM=1 CC=clang v4l2loopback" ]]
    echo CONFIG_CC_IS_GCC=y > "$kernel_source_dir/.config"
    source "$BATS_TEST_DIRNAME/../driver/v4l2loopback-dkms.conf"
    [[ "${MAKE[0]}" != *LLVM* ]]
}

@test "configuration ownership preserves user files" {
    local file="$BATS_TEST_TMPDIR/user.conf"
    echo 'user settings' > "$file"
    run_sudo() { "$@"; }
    log() { :; }
    INSTALLED_FILES=''
    ! archer_write_config "$file" replacement
    archer_remove_config "$file"
    [ "$(cat "$file")" = 'user settings' ]
    local managed="$BATS_TEST_TMPDIR/managed.conf"
    archer_write_config "$managed" 'blacklist acer_wmi'
    archer_write_config "$managed" 'blacklist acer_wmi'
    archer_remove_config "$managed"
    [ ! -e "$managed" ]
}

@test "DKMS ownership requires a literal version namespace" {
    source "$BATS_TEST_DIRNAME/../lib/dkms.sh"
    DKMS_VERSION_BASE=1.0
    external_owned_version 1.0.archer2
    ! external_owned_version 1x0.archer2
    ! external_owned_version 1.0.archer2-other
}
