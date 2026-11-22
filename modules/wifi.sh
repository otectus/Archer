#!/usr/bin/env bash
# Module: non-disruptive Wi-Fi/Bluetooth repair planning

MODULE_NAME="Targeted connectivity repair"
MODULE_ID="wifi"
MODULE_DESCRIPTION="Repair current firmware/rfkill faults without replacing the active network manager"

_connectivity_plan() {
    WIFI_REPAIR="none"
    WIFI_REASON="No current connectivity fault was found"
    read_kernel_log
    if command -v rfkill >/dev/null && rfkill list wifi 2>/dev/null | grep -qi 'Hard blocked: yes'; then
        WIFI_REPAIR="hardware-rfkill"
        WIFI_REASON="Wi-Fi is hardware blocked; software must not override it"
        return 0
    fi
    if command -v rfkill >/dev/null && rfkill list wifi 2>/dev/null | grep -qi 'Soft blocked: yes'; then
        WIFI_REPAIR="soft-rfkill"
        WIFI_REASON="Wi-Fi is currently software blocked"
        return 0
    fi
    if [[ "$KERNEL_LOG_AVAILABLE" -eq 1 ]] &&
       grep -qiE '(iwlwifi|mt76|mt79|ath[0-9a-z]*|rtw[0-9a-z]*).*(firmware.*(failed|error|missing)|no suitable firmware)' <<<"$_CACHED_DMESG"; then
        WIFI_REPAIR="firmware"
        WIFI_REASON="The current boot reports a wireless firmware load failure"
        return 0
    fi
    return 1
}

module_detect() { _connectivity_plan; }
module_check_installed() { return 1; }

module_install() {
    if ! _connectivity_plan; then
        log "$WIFI_REASON. The active network manager and device were left unchanged."
        MODULE_SKIPPED=1
        return 0
    fi
    log "Selected connectivity repair: $WIFI_REPAIR ($WIFI_REASON)"
    case "$WIFI_REPAIR" in
        hardware-rfkill)
            warn "Use the laptop radio switch, airplane-mode key, or firmware setting; Archer made no change."
            return 1
            ;;
        soft-rfkill)
            run_sudo rfkill unblock wifi || return 1
            ;;
        firmware)
            run_sudo pacman -S --needed --noconfirm linux-firmware || return 1
            INSTALLED_PACKAGES+=" linux-firmware"
            mark_reboot_required
            ;;
        *) return 1 ;;
    esac
    log "No network manager, active link, PCI device, or Bluetooth service was reset."
}

module_uninstall() {
    log "No persistent network-manager or device-reset configuration was installed."
    log "Standard firmware packages are retained because other devices may share them."
}

module_verify() {
    local path
    for path in /sys/class/net/*/wireless; do
        [[ -d "$path" ]] && return 0
    done
    warn "No wireless interface is currently enumerated."
    return 1
}
