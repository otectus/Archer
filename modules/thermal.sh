#!/usr/bin/env bash
# Module: Kernel Thermal Profiles
# Enables native acer_wmi thermal profile support on kernel 6.8+
# WARNING: Conflicts with driver module (Linuwu-Sense blacklists acer_wmi)

MODULE_NAME="Kernel Thermal Profiles"
MODULE_ID="thermal"
MODULE_DESCRIPTION="Native kernel thermal profiles via acer_wmi (requires kernel 6.8+)"

_THERMAL_CONF="/etc/modprobe.d/acer-thermal-profiles.conf"

module_detect() {
    # Only relevant for gaming models on kernel 6.8+
    if [ "$SUPPORTS_THERMAL_PROFILES" -eq 1 ]; then
        case "$MODEL_FAMILY" in
            nitro|predator|helios|triton) return 0 ;;
        esac
    fi
    return 1
}

module_check_installed() {
    [ -f "$_THERMAL_CONF" ]
}

module_install() {
    # Kernel version check
    if [ "$SUPPORTS_THERMAL_PROFILES" -ne 1 ]; then
        warn "Kernel 6.8+ required for native thermal profile support. Current: $KERNEL_VERSION"
        return 1
    fi

    # Conflict check
    if [ -f /etc/modprobe.d/blacklist-acer-wmi.conf ]; then
        warn "acer_wmi is currently blacklisted (by the Linuwu-Sense driver)."
        warn "Native thermal profiles require acer_wmi to be loaded."
        warn "Using this module alongside the Linuwu-Sense driver may cause conflicts."
        if ! confirm "Continue anyway?"; then
            log "Skipping thermal profile setup."
            return 0
        fi
        # Remove the blacklist to allow acer_wmi
        log "Removing acer_wmi blacklist..."
        run_sudo rm -f /etc/modprobe.d/blacklist-acer-wmi.conf
    fi

    # Write thermal profile configuration
    log "Enabling Predator Sense v4 thermal profile support..."
    run_sudo tee "$_THERMAL_CONF" > /dev/null <<'EOF'
# Acer thermal profile support - Archer Compatibility Suite
# Enable Predator Sense v4 thermal profiles
options acer_wmi predator_v4=1
# Enable thermal profile cycling with mode button
options acer_wmi cycle_gaming_thermal_profile=1
EOF

    # GRUB parameter injection
    add_grub_params "acer_wmi.predator_v4=1"

    INSTALLED_FILES+=" $_THERMAL_CONF"
    mark_reboot_required

    log "Thermal profiles configured. After reboot, use:"
    log "  cat /sys/firmware/acpi/platform_profile_choices  (list profiles)"
    log "  echo balanced | sudo tee /sys/firmware/acpi/platform_profile  (set profile)"
    log "  The mode button on your keyboard should now cycle through profiles."
}

module_uninstall() {
    log "Removing thermal profile configuration..."
    run_sudo rm -f "$_THERMAL_CONF"

    # Revert GRUB parameter
    remove_grub_params "acer_wmi.predator_v4=1"
}

module_verify() {
    if [ -f /sys/firmware/acpi/platform_profile ]; then
        local profile
        profile=$(cat /sys/firmware/acpi/platform_profile 2>/dev/null)
        log "Active thermal profile: $profile"
        return 0
    fi

    if [ -f "$_THERMAL_CONF" ]; then
        warn "Config written but platform_profile not yet available (reboot required)"
        return 0
    fi
    return 1
}
