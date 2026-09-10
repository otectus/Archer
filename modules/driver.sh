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

module_detect() {
    case "$MODEL_FAMILY" in nitro|predator|helios|triton) return 0 ;; *) return 1 ;; esac
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
    kernel_refresh_initramfs "$_DRIVER_ETC" "$_DRIVER_MKINITCPIO" "$_DRIVER_DRACUT"
}

module_install() {
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
    archer_write_config "$_DRIVER_ETC/modprobe.d/blacklist-acer-wmi.conf" 'blacklist acer_wmi' || return 1
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
