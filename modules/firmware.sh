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
    run_sudo pacman -S --needed --noconfirm fwupd

    log "Enabling fwupd service..."
    run_sudo systemctl enable fwupd.service
    run_sudo systemctl start fwupd.service 2>/dev/null || true

    log "Scanning for supported firmware devices..."
    run_sudo fwupdmgr get-devices --no-unreported-check 2>/dev/null || true

    local bios_ver
    bios_ver=$(cat /sys/class/dmi/id/bios_version 2>/dev/null || echo "unknown")
    log "Current BIOS version: $bios_ver"

    log "Firmware advisor installed. The Archer GUI will show firmware status."

    INSTALLED_PACKAGES+=" fwupd"
}

module_uninstall() {
    run_sudo systemctl disable --now fwupd.service 2>/dev/null || true
    log "fwupd disabled. Package retained."
}

module_verify() {
    has_cmd fwupdmgr && return 0
    warn "fwupdmgr not found"
    return 1
}
