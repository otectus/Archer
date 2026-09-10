#!/usr/bin/env bash
# Module: Kernel Thermal Profiles
# Uses the platform-profile ABI exposed by the active provider.
# Uses an existing profile provider; never loads two Acer WMI owners.

MODULE_NAME="Kernel Thermal Profiles"
MODULE_ID="thermal"
MODULE_DESCRIPTION="Native kernel thermal profiles"

source "$(dirname "${BASH_SOURCE[0]}")/../lib/kernel.sh"
_THERMAL_PROFILE="/sys/firmware/acpi/platform_profile"
_THERMAL_DRIVER_BLACKLIST="/etc/modprobe.d/blacklist-acer-wmi.conf"
_THERMAL_CONF="/etc/modprobe.d/acer-thermal-profiles.conf"

module_detect() {
    # Only relevant when a gaming model exposes platform profiles
    if [[ "$SUPPORTS_THERMAL_PROFILES" -eq 1 ]]; then
        case "$MODEL_FAMILY" in
            nitro|predator|helios|triton) return 0 ;;
        esac
    fi
    return 1
}

module_check_installed() {
    kernel_profile_path >/dev/null
}

module_install() {
    # A working native provider needs no force-generation override.
    if [[ -f "$_THERMAL_PROFILE" ]] || kernel_profile_path >/dev/null; then
        log "Existing platform-profile provider is active; retaining its profiles."
        return 0
    fi
    if [[ -f "$_THERMAL_DRIVER_BLACKLIST" ]]; then
        log "Linuwu-Sense owns Acer WMI and supplies profiles on supported models."
        warn "No profile currently exposed. Reboot and run python3 scripts/archer-diagnose.py."
        return 0
    fi
    warn "No platform-profile interface exposed by this hardware/kernel. Update the kernel or check the driver probe log; no model-generation override was applied."
    return 1
}

module_uninstall() {
    log "Removing thermal profile configuration..."
    archer_remove_config "$_THERMAL_CONF" || return 1

    # Revert GRUB parameter
    remove_grub_params "acer_wmi.predator_v4=1"
}

module_verify() {
    local path profile
    if path=$(kernel_profile_path); then
        profile=$(cat "$path") || return 1
        log "Active thermal profile: $profile"
        return 0
    fi

    if [[ -f "$_THERMAL_CONF" ]]; then
        warn "Config written but platform_profile not yet available (reboot required)"
        return 0
    fi
    return 1
}
