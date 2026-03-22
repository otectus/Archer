#!/usr/bin/env bash
# Module: WiFi/Bluetooth Troubleshooting
# Diagnoses and fixes wireless connectivity issues

MODULE_NAME="WiFi/Bluetooth Troubleshooting"
MODULE_ID="wifi"
MODULE_DESCRIPTION="Diagnose and fix WiFi/Bluetooth issues for various chipsets"

module_detect() {
    [[ "$WIFI_CHIPSET" = "mediatek" ]]
}

module_check_installed() {
    # This module is diagnostic/fix-based, not a persistent install
    return 1
}

module_install() {
    # Ensure firmware is up to date
    log "Installing/updating wireless firmware packages..."
    run_sudo pacman -S --needed --noconfirm linux-firmware

    case "$WIFI_CHIPSET" in
        mediatek)
            log "MediaTek chipset detected: $WIFI_DEVICE"
            _fix_mediatek
            ;;
        intel)
            log "Intel chipset detected: $WIFI_DEVICE"
            _fix_intel
            ;;
        realtek)
            log "Realtek chipset detected: $WIFI_DEVICE"
            _fix_realtek
            ;;
        qualcomm)
            log "Qualcomm/Atheros chipset detected: $WIFI_DEVICE"
            log "Qualcomm chipsets generally have good Linux support."
            log "If you experience issues, ensure linux-firmware is up to date."
            ;;
        *)
            log "WiFi chipset: $WIFI_DEVICE"
            log "No chipset-specific fixes available. Ensuring firmware is current."
            ;;
    esac

    # Common: ensure Bluetooth service is running
    _setup_bluetooth

    # Common: ensure NetworkManager is running
    if has_cmd nmcli; then
        if ! systemctl is-active --quiet NetworkManager.service 2>/dev/null; then
            log "Enabling NetworkManager..."
            run_sudo systemctl enable --now NetworkManager.service
        fi
    fi

    log "WiFi/Bluetooth configuration complete."
    INSTALLED_PACKAGES+=" linux-firmware"
}

_fix_mediatek() {
    # Check for firmware loading failures
    if dmesg 2>/dev/null | grep -qi "mt79.*firmware.*failed\|mt79.*firmware.*error"; then
        warn "MediaTek firmware loading failure detected."
        log "Attempting PCIe device reset..."

        local pci_addr
        pci_addr=$(lspci -D 2>/dev/null | grep -i "mediatek.*network\|mediatek.*wireless" | awk '{print $1}')
        if [[ -n "$pci_addr" ]]; then
            echo 1 | run_sudo tee "/sys/bus/pci/devices/$pci_addr/remove" > /dev/null 2>&1 || true
            sleep 2
            echo 1 | run_sudo tee /sys/bus/pci/rescan > /dev/null 2>&1 || true
            log "PCIe device reset completed."
        else
            warn "Could not find MediaTek PCI address for reset."
        fi
    fi

    # MT7921/MT7922 specific: ensure correct firmware files
    if echo "$WIFI_DEVICE" | grep -qi "MT7921\|MT7922"; then
        log "MT7921/MT7922 detected. These chipsets require kernel 5.12+ for basic support."
        log "Kernel 6.2+ recommended for stability."
        if [[ "$KERNEL_MAJOR" -lt 6 ]]; then
            warn "Your kernel ($KERNEL_VERSION) may not fully support this chipset."
        fi
    fi

    # MT7925e: known problematic
    if echo "$WIFI_DEVICE" | grep -qi "MT7925"; then
        warn "MT7925e detected. This chipset has limited Linux support."
        warn "Known issues: low signal reception, intermittent disconnects."
        warn "Consider replacing with an Intel AX210 for better Linux compatibility."
    fi
}

_fix_intel() {
    # Check rfkill status
    if rfkill list bluetooth 2>/dev/null | grep -qi "Soft blocked: yes"; then
        log "Bluetooth is soft-blocked. Unblocking..."
        run_sudo rfkill unblock bluetooth
    fi
    if rfkill list wifi 2>/dev/null | grep -qi "Soft blocked: yes"; then
        log "WiFi is soft-blocked. Unblocking..."
        run_sudo rfkill unblock wifi
    fi

    # Intel AX210/AX211/BE200 may need specific firmware
    if echo "$WIFI_DEVICE" | grep -qi "AX210\|AX211\|BE200"; then
        log "Intel AX210/AX211/BE200 detected. Checking firmware..."
        if dmesg 2>/dev/null | grep -qi "iwlwifi.*firmware.*error\|iwlwifi.*no suitable firmware"; then
            warn "Intel WiFi firmware issue detected."
            log "Try: sudo pacman -S linux-firmware (already done above)"
            mark_reboot_required
        fi
    fi
}

_fix_realtek() {
    log "Some Realtek adapters require out-of-tree drivers."
    log "Common AUR packages for Realtek WiFi:"
    log "  - rtw89-dkms-git (for RTL8852/RTL8922 series)"
    log "  - rtl8821cu-morrownr-dkms-git (for RTL8821CU USB)"

    if [[ -n "$AUR_HELPER" ]]; then
        log "You can install these with: $AUR_HELPER -S <package-name>"
    else
        log "Install an AUR helper (yay or paru) to install these packages."
    fi
}

_setup_bluetooth() {
    if ! pacman -Qi bluez &>/dev/null; then
        log "Installing Bluetooth packages..."
        run_sudo pacman -S --needed --noconfirm bluez bluez-utils
    fi

    if ! systemctl is-active --quiet bluetooth.service 2>/dev/null; then
        log "Enabling Bluetooth service..."
        run_sudo systemctl enable --now bluetooth.service
    fi
}

module_uninstall() {
    log "WiFi module: No persistent changes to remove."
    log "Firmware packages retained as they are standard system components."
}

module_verify() {
    # Check if a wireless interface exists and is up
    if ip link 2>/dev/null | grep -q "wl"; then
        return 0
    fi
    warn "No wireless interface detected"
    return 1
}
