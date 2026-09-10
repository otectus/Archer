#!/usr/bin/env bats

setup() {
    REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
    source "$REPO_ROOT/modules/driver.sh"
    export TEST_DKMS_STATE="$BATS_TEST_TMPDIR/dkms-state"
    export TEST_DKMS_LOG="$BATS_TEST_TMPDIR/dkms-log"
    : > "$TEST_DKMS_STATE"
    : > "$TEST_DKMS_LOG"
    DRY_RUN=0
    INSTALLED_FILES="" INSTALLED_DKMS=""
    _DRIVER_SRC_ROOT="$BATS_TEST_TMPDIR/src"
    _DRIVER_ETC="$BATS_TEST_TMPDIR/etc"
    _DRIVER_MODULES_ROOT="$BATS_TEST_TMPDIR/modules"
    _DRIVER_PROFILE_CLASS="$BATS_TEST_TMPDIR/profiles"
    _DRIVER_PACKAGE="$BATS_TEST_TMPDIR/package"
    _DRIVER_MKINITCPIO="$BATS_TEST_TMPDIR/mkinitcpio"
    _DRIVER_DRACUT="$BATS_TEST_TMPDIR/dracut"
    has_cmd() { return 1; }
    run_sudo_timeout() { shift; run_sudo "$@"; }
    mkdir -p "$_DRIVER_SRC_ROOT" "$_DRIVER_PACKAGE" "$_DRIVER_MODULES_ROOT/7.0.11-zen/build"
    mock_headers 7.0.11-zen
    read_manifest_field() { echo 'linuwu-sense/1.0'; }
    cat > "$_DRIVER_PACKAGE/prepare.sh" <<'PREPARE'
#!/bin/bash
set -e
mkdir -p "$1/src"
for file in src/linuwu_sense.c Makefile archer-build.sh archer-kernel.sh archer-source dkms.conf; do
    echo validated-source > "$1/$file"
done
PREPARE
    log() { echo "$*"; }
    warn() { echo "$*"; }
    mark_reboot_required() { :; }
    dkms() { python3 "$REPO_ROOT/tests/helpers/dkms.py" "$@"; }
    run_sudo() {
        case "$1" in
            chown|systemctl) return 0 ;;
            *) "$@" ;;
        esac
    }
}

@test "driver uses a full revision and a distinct Archer DKMS version" {
    [[ "$DRIVER_REVISION" =~ ^[0-9a-f]{40}$ ]]
    [[ "$DKMS_VERSION" == 1.0.archer* ]]
    [ "$DKMS_VERSION" != 1.0 ]
}

@test "repeated installation is idempotent" {
    module_install
    module_install
    [ "$(grep -c '^add ' "$TEST_DKMS_LOG")" -eq 1 ]
    [ "$(grep -c ': installed' "$TEST_DKMS_STATE")" -eq 1 ]
    grep -qx linuwu_sense "$_DRIVER_ETC/modules-load.d/archer-linuwu-sense.conf"
    module_check_installed
}

@test "source preparation failure does not change DKMS or boot config" {
    echo 'exit 1' > "$_DRIVER_PACKAGE/prepare.sh"
    run module_install
    [ "$status" -ne 0 ]
    [ ! -s "$TEST_DKMS_LOG" ]
    [ ! -e "$_DRIVER_ETC/modprobe.d/blacklist-acer-wmi.conf" ]
}

@test "migrate legacy 1.0 after successful builds and clean old source" {
    echo 'linuwu-sense/1.0, 7.0.11-zen, x86_64: installed' > "$TEST_DKMS_STATE"
    mkdir -p "$_DRIVER_SRC_ROOT/linuwu-sense-1.0"
    module_install
    ! grep -q 'linuwu-sense/1.0,' "$TEST_DKMS_STATE"
    [ ! -d "$_DRIVER_SRC_ROOT/linuwu-sense-1.0" ]
    [[ "$(cat "$TEST_DKMS_LOG")" == *"build -m linuwu-sense -v $DKMS_VERSION -k 7.0.11-zen"* ]]
    [[ "$INSTALLED_DKMS" == *"linuwu-sense/$DKMS_VERSION"* ]]
}

@test "build failure retains old driver and configuration" {
    echo 'linuwu-sense/1.0, 7.0.11-zen, x86_64: installed' > "$TEST_DKMS_STATE"
    export TEST_FAIL_ACTION=build TEST_FAIL_VERSION="$DKMS_VERSION"
    run module_install
    [ "$status" -ne 0 ]
    grep -q 'linuwu-sense/1.0,.*installed' "$TEST_DKMS_STATE"
    ! grep -q '^remove -m linuwu-sense -v 1.0 --all' "$TEST_DKMS_LOG"
    [ ! -e "$_DRIVER_ETC/modprobe.d/blacklist-acer-wmi.conf" ]
}

@test "install failure rolls legacy version back" {
    echo 'linuwu-sense/1.0, 7.0.11-zen, x86_64: installed' > "$TEST_DKMS_STATE"
    mkdir -p "$_DRIVER_SRC_ROOT/linuwu-sense-1.0"
    export TEST_FAIL_ACTION=install TEST_FAIL_VERSION="$DKMS_VERSION"
    run module_install
    [ "$status" -ne 0 ]
    grep -q 'linuwu-sense/1.0,.*installed' "$TEST_DKMS_STATE"
    [ -d "$_DRIVER_SRC_ROOT/linuwu-sense-1.0" ]
    ! grep -q "linuwu-sense/$DKMS_VERSION" "$TEST_DKMS_STATE"
}

@test "reinstall refuses modified source instead of replacing registered tree" {
    module_install
    echo modified > "$_DRIVER_SRC_ROOT/linuwu-sense-$DKMS_VERSION/src/linuwu_sense.c"
    run module_install
    [ "$status" -ne 0 ]
    [ "$(cat "$_DRIVER_SRC_ROOT/linuwu-sense-$DKMS_VERSION/src/linuwu_sense.c")" = modified ]
}

@test "all installed kernels with headers get independent DKMS builds" {
    mkdir -p "$_DRIVER_MODULES_ROOT/7.1-custom/build"
    mock_headers 7.1-custom
    module_install
    [ "$(grep -c '^build ' "$TEST_DKMS_LOG")" -eq 2 ]
    [ "$(grep -c ': installed' "$TEST_DKMS_STATE")" -eq 2 ]
}

@test "no headers fails before preparing source" {
    rm "$_DRIVER_MODULES_ROOT/7.0.11-zen/build/Makefile"
    run module_install
    [ "$status" -ne 0 ]
    [ ! -s "$TEST_DKMS_LOG" ]
}

@test "external native platform-profile provider is preserved across reinstall" {
    mkdir -p "$_DRIVER_PROFILE_CLASS/platform-profile-0"
    echo amd-pmf > "$_DRIVER_PROFILE_CLASS/platform-profile-0/name"
    module_install
    grep -q 'native_platform_profile=1' "$_DRIVER_ETC/modprobe.d/archer-native-profile.conf"
    rm "$_DRIVER_PROFILE_CLASS/platform-profile-0/name"
    module_install
    grep -q 'native_platform_profile=1' "$_DRIVER_ETC/modprobe.d/archer-native-profile.conf"
}

@test "acer-wmi profile provider is replaced by Linuwu profile implementation" {
    mkdir -p "$_DRIVER_PROFILE_CLASS/platform-profile-0"
    echo acer-wmi > "$_DRIVER_PROFILE_CLASS/platform-profile-0/name"
    module_install
    [ ! -e "$_DRIVER_ETC/modprobe.d/archer-native-profile.conf" ]
}

@test "uninstall removes all Archer DKMS versions and boot files" {
    module_install
    mkdir -p "$_DRIVER_SRC_ROOT/linuwu-sense-1.0" "$_DRIVER_SRC_ROOT/linuwu-sense-unrelated"
    module_uninstall
    [ ! -e "$_DRIVER_SRC_ROOT/linuwu-sense-$DKMS_VERSION" ]
    [ ! -e "$_DRIVER_SRC_ROOT/linuwu-sense-1.0" ]
    [ -e "$_DRIVER_SRC_ROOT/linuwu-sense-unrelated" ]
    [ ! -e "$_DRIVER_ETC/modprobe.d/blacklist-acer-wmi.conf" ]
    [ ! -e "$_DRIVER_ETC/modules-load.d/archer-linuwu-sense.conf" ]
    ! module_check_installed
}

@test "migration requires headers for every installed kernel" {
    mkdir -p "$_DRIVER_MODULES_ROOT/7.1-custom/kernel"
    run module_install
    [ "$status" -ne 0 ]
    [ ! -s "$TEST_DKMS_LOG" ]
}

@test "thermal preserves existing profile and never removes driver blacklist" {
    source "$REPO_ROOT/modules/thermal.sh"
    _THERMAL_PROFILE="$BATS_TEST_TMPDIR/profile"
    _THERMAL_DRIVER_BLACKLIST="$BATS_TEST_TMPDIR/blacklist"
    SUPPORTS_THERMAL_PROFILES=1 MODEL_FAMILY=nitro
    echo balanced > "$_THERMAL_PROFILE"
    echo 'blacklist acer_wmi' > "$_THERMAL_DRIVER_BLACKLIST"
    run_sudo() { echo 'Unexpected thermal mutation' >&2; return 1; }
    module_install
    rm "$_THERMAL_PROFILE"
    module_install
    [ "$(cat "$_THERMAL_DRIVER_BLACKLIST")" = 'blacklist acer_wmi' ]
}

@test "mkinitcpio rebuilds all presets after boot configuration changes" {
    mkdir -p "$_DRIVER_ETC/mkinitcpio.d"
    touch "$_DRIVER_ETC/mkinitcpio.d/linux-zen.preset"
    printf '#!/bin/sh\necho "$*" >> "%s"\n' "$BATS_TEST_TMPDIR/initramfs.log" > "$_DRIVER_MKINITCPIO"
    chmod +x "$_DRIVER_MKINITCPIO"
    module_install
    module_uninstall
    [ "$(grep -c -- '-P' "$BATS_TEST_TMPDIR/initramfs.log")" -eq 2 ]
}

@test "dracut failures are propagated and retain retryable source" {
    printf '#!/bin/sh\nexit 1\n' > "$_DRIVER_DRACUT"
    chmod +x "$_DRIVER_DRACUT"
    run module_install
    [ "$status" -ne 0 ]
    [ -f "$_DRIVER_SRC_ROOT/linuwu-sense-$DKMS_VERSION/dkms.conf" ]
}

mock_headers() {
    mkdir -p "$_DRIVER_MODULES_ROOT/$1/build/include/config"
    touch "$_DRIVER_MODULES_ROOT/$1/build/Makefile" "$_DRIVER_MODULES_ROOT/$1/build/.config"
    echo "$1" > "$_DRIVER_MODULES_ROOT/$1/build/include/config/kernel.release"
}

@test "uninstall preserves unowned legacy DKMS source" {
    read_manifest_field() { echo ''; }
    mkdir -p "$_DRIVER_SRC_ROOT/linuwu-sense-1.0"
    echo 'linuwu-sense/1.0, 7.0.11-zen, x86_64: installed' > "$TEST_DKMS_STATE"
    module_uninstall
    [ -d "$_DRIVER_SRC_ROOT/linuwu-sense-1.0" ]
    grep -q 'linuwu-sense/1.0,' "$TEST_DKMS_STATE"
}

@test "mismatched build symlink fails before DKMS mutation" {
    echo 7.2.3-cachyos > "$_DRIVER_MODULES_ROOT/7.0.11-zen/build/include/config/kernel.release"
    run module_install
    [ "$status" -ne 0 ]
    [[ "$output" == *'never link another kernel'* ]]
    [ ! -s "$TEST_DKMS_LOG" ]
}

@test "driver verification requires every installed target" {
    module_install
    mock_headers 7.2.3-custom
    ! module_check_installed
    module_install
    module_check_installed
}
