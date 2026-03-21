#!/usr/bin/env fish
# Module: Touchpad Fix
# Fixes I2C HID touchpad detection failures

set -g MODULE_NAME "Touchpad Fix"
set -g MODULE_ID "touchpad"
set -g MODULE_DESCRIPTION "Fix I2C HID touchpad detection (module reload, kernel params, AMD pinctrl)"

set -g _TOUCHPAD_AMD_CONF "/etc/modprobe.d/touchpad-amd-fix.conf"
set -g _TOUCHPAD_SERVICE "/etc/systemd/system/touchpad-fix.service"

function module_detect
    test "$TOUCHPAD_ERRORS" = 1
end

function module_check_installed
    test -f "$_TOUCHPAD_SERVICE"; or test -f "$_TOUCHPAD_AMD_CONF"
end

function module_install
    set -l fix_count 0

    # Strategy 1: AMD pinctrl module ordering
    if test "$CPU_VENDOR" = "AuthenticAMD"
        log "AMD system detected. Applying pinctrl_amd load order fix..."
        run_sudo tee "$_TOUCHPAD_AMD_CONF" > /dev/null <<'EOF'
# Ensure pinctrl_amd loads before i2c_hid_acpi to fix touchpad detection
# Installed by Archer Compatibility Suite
softdep i2c_hid_acpi pre: pinctrl_amd
EOF
        set -ga INSTALLED_FILES $_TOUCHPAD_AMD_CONF
        set fix_count (math $fix_count + 1)
    end

    # Strategy 2: Module reload service
    log "Creating touchpad module reload service..."
    run_sudo tee "$_TOUCHPAD_SERVICE" > /dev/null <<'SERVICE_EOF'
[Unit]
Description=Reload I2C HID for touchpad detection
After=multi-user.target
ConditionPathExists=/sys/bus/i2c/devices

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'modprobe -r i2c_hid_acpi 2>/dev/null; modprobe -r i2c_hid 2>/dev/null; sleep 1; modprobe i2c_hid; modprobe i2c_hid_acpi'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    run_sudo systemctl daemon-reload
    run_sudo systemctl enable touchpad-fix.service
    set -ga INSTALLED_FILES $_TOUCHPAD_SERVICE
    set fix_count (math $fix_count + 1)

    # Strategy 3: GRUB kernel parameters
    add_grub_params "i8042.reset i8042.nomux"
    set fix_count (math $fix_count + 1)

    log "Applied $fix_count touchpad fix(es). A reboot is required."
    mark_reboot_required
end

function module_uninstall
    log "Removing touchpad fixes..."

    if test -f "$_TOUCHPAD_AMD_CONF"
        sudo rm -f "$_TOUCHPAD_AMD_CONF"
    end

    if test -f "$_TOUCHPAD_SERVICE"
        sudo systemctl disable touchpad-fix.service 2>/dev/null; or true
        sudo rm -f "$_TOUCHPAD_SERVICE"
        sudo systemctl daemon-reload
    end

    remove_grub_params "i8042.reset i8042.nomux"
end

function module_verify
    if grep -qiE "touchpad|ELAN|Synaptics" /proc/bus/input/devices 2>/dev/null
        return 0
    end
    warn "Touchpad not detected in input devices (may need reboot)"
    return 1
end
