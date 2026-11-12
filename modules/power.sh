#!/usr/bin/env bash
# Module: Power Management
# Installs TLP with Acer-optimized power configuration

MODULE_NAME="Power Management"
MODULE_ID="power"
MODULE_DESCRIPTION="TLP power management with Acer-optimized configuration"

_TLP_CONF="/etc/tlp.d/01-acer-optimize.conf"

module_detect() {
    [[ "$HAS_BATTERY" -eq 1 ]]
}

module_check_installed() {
    pacman -Qi tlp &>/dev/null
}

module_install() {
    # Check for conflicting service
    if pacman -Qi power-profiles-daemon &>/dev/null; then
        warn "power-profiles-daemon is installed and conflicts with TLP."
        if confirm "Switch from power-profiles-daemon to TLP?"; then
            module_service power-profiles-daemon.service mask --now || return 1
        else
            log "Skipping TLP installation to avoid conflicts."
            MODULE_SKIPPED=1
            return 0
        fi
    fi

    log "Installing TLP..."
    run_sudo pacman -S --needed --noconfirm tlp tlp-rdw || return 1

    INSTALLED_PACKAGES+=" tlp tlp-rdw"

    # Mask conflicting services
    module_service systemd-rfkill.service mask || return 1
    module_service systemd-rfkill.socket mask || return 1

    # Write Acer-optimized config
    log "Applying Acer-optimized power settings..."
    local config
    config=$(cat <<'TLP_EOF'
# Acer laptop optimizations - Archer Compatibility Suite
# CPU scaling
CPU_SCALING_GOVERNOR_ON_AC=performance
CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=balance_performance
CPU_ENERGY_PERF_POLICY_ON_BAT=power

# WiFi power saving
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=on

# Runtime PM for PCI devices (includes NVIDIA GPU)
RUNTIME_PM_ON_AC=auto
RUNTIME_PM_ON_BAT=auto

# USB autosuspend
USB_AUTOSUSPEND=1
TLP_EOF
)
    local choices
    choices=$(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null) || choices=""
    if [[ " $choices " == *" balanced "* ]]; then
        config+=$'\nPLATFORM_PROFILE_ON_AC=balanced'
    else
        warn "No balanced platform profile; leaving TLP's AC platform profile unset."
    fi
    if [[ " $choices " == *" low-power "* ]]; then
        config+=$'\nPLATFORM_PROFILE_ON_BAT=low-power'
    else
        warn "No low-power platform profile; leaving TLP's battery platform profile unset."
    fi
    module_config "$_TLP_CONF" "$config" || return 1
    module_service tlp.service enable --now || return 1
    run_sudo tlp start || return 1

    log "TLP installed and configured."
    log "View status: sudo tlp-stat -s"

    INSTALLED_FILES+=" $_TLP_CONF"
    INSTALLED_PACKAGES+=" tlp tlp-rdw"
}

module_uninstall() {
    module_remove_config "$_TLP_CONF" || return 1
    module_restore_services || return 1
    log "TLP configuration removed; prior service states restored. Packages retained."
}

module_verify() {
    if systemctl is-active --quiet tlp.service 2>/dev/null; then
        return 0
    fi
    warn "TLP service not active"
    return 1
}
