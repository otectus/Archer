#!/usr/bin/env bash
# Module: Touchpad Fix
# Fixes I2C HID touchpad detection failures

MODULE_NAME="Touchpad Fix"
MODULE_ID="touchpad"
MODULE_DESCRIPTION="Fix I2C HID touchpad detection (module reload, kernel params, AMD pinctrl)"

_TOUCHPAD_AMD_CONF="/etc/modprobe.d/touchpad-amd-fix.conf"
_TOUCHPAD_SERVICE="/etc/systemd/system/touchpad-fix.service"

module_detect() {
    [ "$TOUCHPAD_ERRORS" -eq 1 ]
}

module_check_installed() {
    [ -f "$_TOUCHPAD_SERVICE" ] || [ -f "$_TOUCHPAD_AMD_CONF" ]
}

module_install() {
    local fix_count=0

    # Strategy 1: AMD pinctrl module ordering
    if [ "$CPU_VENDOR" = "AuthenticAMD" ]; then
        log "AMD system detected. Applying pinctrl_amd load order fix..."
        run_sudo tee "$_TOUCHPAD_AMD_CONF" > /dev/null <<'EOF'
# Ensure pinctrl_amd loads before i2c_hid_acpi to fix touchpad detection
# Installed by Archer Compatibility Suite
softdep i2c_hid_acpi pre: pinctrl_amd
EOF
        INSTALLED_FILES+=" $_TOUCHPAD_AMD_CONF"
        fix_count=$((fix_count + 1))
    fi

    # Strategy 2: Module reload service for intermittent failures
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
    INSTALLED_FILES+=" $_TOUCHPAD_SERVICE"
    fix_count=$((fix_count + 1))

    # Strategy 3: GRUB kernel parameters
    add_grub_params "i8042.reset i8042.nomux"
    fix_count=$((fix_count + 1))

    log "Applied $fix_count touchpad fix(es). A reboot is required."
    mark_reboot_required
}

module_uninstall() {
    log "Removing touchpad fixes..."

    if [ -f "$_TOUCHPAD_AMD_CONF" ]; then
        sudo rm -f "$_TOUCHPAD_AMD_CONF"
    fi

    if [ -f "$_TOUCHPAD_SERVICE" ]; then
        sudo systemctl disable touchpad-fix.service 2>/dev/null || true
        sudo rm -f "$_TOUCHPAD_SERVICE"
        sudo systemctl daemon-reload
    fi

    # Revert GRUB params
    remove_grub_params "i8042.reset i8042.nomux"
}

module_verify() {
    # Check if touchpad is in input devices
    if grep -qiE "touchpad|ELAN|Synaptics" /proc/bus/input/devices 2>/dev/null; then
        return 0
    fi
    warn "Touchpad not detected in input devices (may need reboot)"
    return 1
}
