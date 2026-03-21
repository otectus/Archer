#!/usr/bin/env fish
# Module: Kernel Thermal Profiles
# Enables native acer_wmi thermal profile support on kernel 6.8+
# WARNING: Conflicts with core-damx (Linuwu-Sense blacklists acer_wmi)

set -g MODULE_NAME "Kernel Thermal Profiles"
set -g MODULE_ID "thermal"
set -g MODULE_DESCRIPTION "Native kernel thermal profiles via acer_wmi (requires kernel 6.8+)"

set -g _THERMAL_CONF "/etc/modprobe.d/acer-thermal-profiles.conf"

function module_detect
    if test "$SUPPORTS_THERMAL_PROFILES" = 1
        switch "$MODEL_FAMILY"
            case nitro predator helios triton
                return 0
        end
    end
    return 1
end

function module_check_installed
    test -f "$_THERMAL_CONF"
end

function module_install
    if test "$SUPPORTS_THERMAL_PROFILES" != 1
        warn "Kernel 6.8+ required for native thermal profile support. Current: $KERNEL_VERSION"
        return 1
    end

    # Conflict check
    if test -f /etc/modprobe.d/blacklist-acer-wmi.conf
        warn "acer_wmi is currently blacklisted (likely by Linuwu-Sense / DAMX)."
        warn "Native thermal profiles require acer_wmi to be loaded."
        warn "Using this module alongside DAMX may cause conflicts."
        if not confirm "Continue anyway?"
            log "Skipping thermal profile setup."
            return 0
        end
        log "Removing acer_wmi blacklist..."
        run_sudo rm -f /etc/modprobe.d/blacklist-acer-wmi.conf
    end

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

    set -ga INSTALLED_FILES $_THERMAL_CONF
    mark_reboot_required

    log "Thermal profiles configured. After reboot, use:"
    log "  cat /sys/firmware/acpi/platform_profile_choices  (list profiles)"
    log "  echo balanced | sudo tee /sys/firmware/acpi/platform_profile  (set profile)"
    log "  The mode button on your keyboard should now cycle through profiles."
end

function module_uninstall
    log "Removing thermal profile configuration..."
    sudo rm -f "$_THERMAL_CONF"

    remove_grub_params "acer_wmi.predator_v4=1"
end

function module_verify
    if test -f /sys/firmware/acpi/platform_profile
        set -l profile (cat /sys/firmware/acpi/platform_profile 2>/dev/null)
        log "Active thermal profile: $profile"
        return 0
    end

    if test -f "$_THERMAL_CONF"
        warn "Config written but platform_profile not yet available (reboot required)"
        return 0
    end
    return 1
end
