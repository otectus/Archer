#!/usr/bin/env fish
# Module: Power Management
# Installs TLP with Acer-optimized power configuration

set -g MODULE_NAME "Power Management"
set -g MODULE_ID "power"
set -g MODULE_DESCRIPTION "TLP power management with Acer-optimized configuration"

set -g _TLP_CONF "/etc/tlp.d/01-acer-optimize.conf"

function module_detect
    test "$HAS_BATTERY" = 1
end

function module_check_installed
    pacman -Qi tlp >/dev/null 2>&1
end

function module_install
    # Check for conflicting service
    if pacman -Qi power-profiles-daemon >/dev/null 2>&1
        warn "power-profiles-daemon is installed and conflicts with TLP."
        if confirm "Remove power-profiles-daemon and install TLP?"
            run_sudo pacman -Rns --noconfirm power-profiles-daemon
        else
            log "Skipping TLP installation to avoid conflicts."
            return 0
        end
    end

    log "Installing TLP..."
    run_sudo pacman -S --needed --noconfirm tlp tlp-rdw

    # Mask conflicting services
    run_sudo systemctl mask systemd-rfkill.service
    run_sudo systemctl mask systemd-rfkill.socket

    # Enable TLP
    run_sudo systemctl enable --now tlp.service

    # Write Acer-optimized config
    log "Applying Acer-optimized power settings..."
    run_sudo tee "$_TLP_CONF" > /dev/null <<'TLP_EOF'
# Acer laptop optimizations - Archer Compatibility Suite
# CPU scaling
CPU_SCALING_GOVERNOR_ON_AC=performance
CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=balance_performance
CPU_ENERGY_PERF_POLICY_ON_BAT=power

# Platform profile
PLATFORM_PROFILE_ON_AC=balanced
PLATFORM_PROFILE_ON_BAT=low-power

# WiFi power saving
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=on

# Runtime PM for PCI devices (includes NVIDIA GPU)
RUNTIME_PM_ON_AC=auto
RUNTIME_PM_ON_BAT=auto

# USB autosuspend
USB_AUTOSUSPEND=1
TLP_EOF

    log "TLP installed and configured."
    log "View status: sudo tlp-stat -s"

    set -ga INSTALLED_FILES $_TLP_CONF
    set -ga INSTALLED_PACKAGES tlp tlp-rdw
end

function module_uninstall
    log "Removing TLP configuration..."
    sudo rm -f "$_TLP_CONF"

    log "Disabling TLP..."
    sudo systemctl disable --now tlp.service 2>/dev/null; or true

    sudo systemctl unmask systemd-rfkill.service 2>/dev/null; or true
    sudo systemctl unmask systemd-rfkill.socket 2>/dev/null; or true

    log "TLP config removed. Package retained (remove manually with: sudo pacman -Rns tlp tlp-rdw)"
end

function module_verify
    if systemctl is-active --quiet tlp.service 2>/dev/null
        return 0
    end
    warn "TLP service not active"
    return 1
end
