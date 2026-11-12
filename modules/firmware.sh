#!/usr/bin/env bash
# Module: Firmware Update Advisor
# Installs fwupd for firmware update detection and guidance

MODULE_NAME="Firmware Update Advisor"
MODULE_ID="firmware"
MODULE_DESCRIPTION="Firmware update advisor via fwupd"

module_detect() {
    return 0
}

module_check_installed() {
    has_cmd fwupdmgr
}

module_install() {
    log "Installing fwupd..."
    run_sudo pacman -S --needed --noconfirm fwupd || return 1

    log "Enabling fwupd service..."
    INSTALLED_PACKAGES+=" fwupd"
    module_service fwupd.service start || return 1

    log "Scanning for supported firmware devices..."
    run_sudo fwupdmgr get-devices --no-unreported-check 2>/dev/null || true

    local bios_ver
    bios_ver=$(cat /sys/class/dmi/id/bios_version 2>/dev/null || echo "unknown")
    log "Current BIOS version: $bios_ver"

    log "Firmware advisor installed. The Archer GUI will show firmware status."

    INSTALLED_PACKAGES+=" fwupd"
}

module_uninstall() {
    module_restore_services || return 1
    log "fwupd service state restored. Package retained."
}

module_verify() {
    has_cmd fwupdmgr && return 0
    warn "fwupdmgr not found"
    return 1
}
