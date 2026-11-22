#!/usr/bin/env bash
# Module: pinned Linuwu-Sense DKMS driver. Never hot-swaps the active WMI owner.
MODULE_NAME="Linuwu-Sense Kernel Driver"
MODULE_ID="driver"
MODULE_DESCRIPTION="Linuwu-Sense kernel driver for fan/RGB hardware access (DKMS)"
_DRIVER_PACKAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../driver/linuwu-sense" && pwd)"
source "$_DRIVER_PACKAGE/source.conf"
source "$_DRIVER_PACKAGE/../../lib/dkms.sh"
_DRIVER_SRC_ROOT="/usr/src"
_DRIVER_MODULES_ROOT="/usr/lib/modules"
_DRIVER_ETC="/etc"
_DRIVER_PROFILE_CLASS="/sys/class/platform-profile"
_DRIVER_MKINITCPIO="/usr/bin/mkinitcpio"
_DRIVER_DRACUT="/usr/bin/dracut"
_DRIVER_BOOT_ROOT="/"
_DRIVER_BOOT_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/archer-boot.py"

_driver_native_fans() {
    local hwmon name owner
    for hwmon in "${KERNEL_SYS_ROOT:-/sys}"/class/hwmon/hwmon*; do
        [[ -r "$hwmon/name" ]] || continue
        read -r name < "$hwmon/name" || continue
        [[ "$name" == acer || "$name" == acer_wmi ]] || continue
        owner=$(readlink -f "$hwmon/device/driver/module") || continue
        [[ "${owner##*/}" == acer_wmi ]] || continue
        [[ -r "$hwmon/pwm1" && -w "$hwmon/pwm1" && -r "$hwmon/pwm1_enable" && -w "$hwmon/pwm1_enable" ]] && return 0
    done
    return 1
}

module_detect() {
    local sysroot=${KERNEL_SYS_ROOT:-/sys}
    [[ -d "$sysroot/bus/wmi/devices" || -d "$sysroot/devices/platform/acer-wmi" ]] || return 1
    return 0
}

module_check_installed() {
    # Report complete installed coverage, not success from one unrelated target.
    local path kernel found=0 status
    for path in "$_DRIVER_MODULES_ROOT"/*; do
        [[ -f "$path/vmlinuz" || -d "$path/kernel" || -f "$path/build/Makefile" ]] || continue
        kernel=${path##*/}
        status=$(dkms status -m "$DKMS_NAME" -v "$DKMS_VERSION" -k "$kernel") || return 1
        [[ "$status" == *': installed'* ]] || return 1
        found=1
    done
    [[ "$found" -eq 1 ]]
}

_driver_native_profile() {
    local path name
    for path in "$_DRIVER_PROFILE_CLASS"/*/name; do
        [[ -f "$path" ]] || continue
        name=$(cat "$path") || return 1
        if [[ -n "$name" && "$name" != "acer-wmi" ]]; then
            log "Preserving native platform-profile provider: $name"
            return 0
        fi
    done
    return 1
}

_driver_refresh_initramfs() {
    run_sudo /usr/bin/python3 "$_DRIVER_BOOT_HELPER" --root "$_DRIVER_BOOT_ROOT" rebuild
}

module_install() {
    if _driver_native_fans && [[ "${ARCHER_FORCE_EXTENSION:-0}" != 1 ]]; then
        log "Native acer-wmi hwmon fan controls are available; retaining the working kernel owner."
        log "Set ARCHER_FORCE_EXTENSION=1 only for a reviewed feature that native acer-wmi cannot provide."
        MODULE_SKIPPED=1
        return 0
    fi
    EXTERNAL_SRC_ROOT="$_DRIVER_SRC_ROOT" EXTERNAL_PACKAGE="$_DRIVER_PACKAGE"
    KERNEL_MODULES_ROOT="$_DRIVER_MODULES_ROOT"
    external_install_dkms || return 1
    [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
    run_sudo mkdir -p "$_DRIVER_ETC/modprobe.d" "$_DRIVER_ETC/modules-load.d" || return 1
    if _driver_native_profile; then
        archer_write_config "$_DRIVER_ETC/modprobe.d/archer-native-profile.conf" 'options linuwu_sense native_platform_profile=1' || return 1
    fi
    # Keep an existing native-provider setting across reinstalls while its provider
    # is temporarily unavailable. Uninstall removes this Archer-owned file.
    archer_write_config "$_DRIVER_ETC/modprobe.d/blacklist-acer-wmi.conf" '# Archer extension selected after native capability comparison
blacklist acer_wmi' || return 1
    archer_write_config "$_DRIVER_ETC/modules-load.d/archer-linuwu-sense.conf" "$DRIVER_MODULE" || return 1
    if ! _driver_refresh_initramfs; then
        warn "Initramfs refresh failed. Repair/retry it before rebooting; the new DKMS source and configuration are retained."
        return 1
    fi
    INSTALLED_DKMS+=" ${DKMS_NAME}/${DKMS_VERSION}"
    mark_reboot_required
    log "Reboot to activate the new driver. Native profiles remain independent of fan availability."
}

module_uninstall() {
    EXTERNAL_SRC_ROOT="$_DRIVER_SRC_ROOT"
    run_sudo systemctl stop archer-daemon.service 2>/dev/null || true
    external_uninstall_dkms || return 1
    archer_remove_config "$_DRIVER_ETC/modprobe.d/blacklist-acer-wmi.conf" \
        "$_DRIVER_ETC/modules-load.d/archer-linuwu-sense.conf" \
        "$_DRIVER_ETC/modprobe.d/archer-native-profile.conf" || return 1
    if ! _driver_refresh_initramfs; then
        warn "Driver removed, but initramfs cleanup failed. Rebuild the boot images before rebooting."
        return 1
    fi
    mark_reboot_required
}

module_verify() {
    if ! module_check_installed; then
        warn "$DKMS_NAME/$DKMS_VERSION is not installed for every installed kernel."
        return 1
    fi
    if [[ ! -d /sys/module/linuwu_sense ]]; then
        warn "Linuwu-Sense is installed but not loaded. Reboot; then run python3 scripts/archer-diagnose.py."
    elif [[ ! -e /sys/devices/platform/acer-wmi/nitro_sense/fan_speed && ! -e /sys/devices/platform/acer-wmi/predator_sense/fan_speed ]]; then
        warn "Module loaded without a fan interface. Run python3 scripts/archer-diagnose.py and inspect the kernel log."
    fi
}
