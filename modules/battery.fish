#!/usr/bin/env fish
# Module: Battery Charge Limit
# Installs acer-wmi-battery DKMS module for 80% charge limiting

set -g MODULE_NAME "Battery Charge Limit"
set -g MODULE_ID "battery"
set -g MODULE_DESCRIPTION "Limits battery charging to 80% for health (acer-wmi-battery)"

set -g _BATTERY_DKMS_NAME "acer-wmi-battery"
set -g _BATTERY_DKMS_VERSION "0.1.0"
set -g _BATTERY_REPO "https://github.com/frederik-h/acer-wmi-battery.git"
set -g _BATTERY_HEALTH_PATH "/sys/bus/wmi/drivers/acer-wmi-battery/health_mode"
set -g _BATTERY_UDEV_RULE "/etc/udev/rules.d/99-acer-battery-health.rules"

function module_detect
    test "$HAS_BATTERY" = 1
end

function module_check_installed
    if test -f "$_BATTERY_HEALTH_PATH"
        return 0
    end
    if dkms status 2>/dev/null | grep -q "$_BATTERY_DKMS_NAME"
        return 0
    end
    return 1
end

function module_install
    # Check if the interface already exists natively
    if test -f "$_BATTERY_HEALTH_PATH"
        log "Battery health mode interface already available (native driver)."
        log "Enabling 80% charge limit..."
        echo 1 | run_sudo tee "$_BATTERY_HEALTH_PATH" > /dev/null
        _install_udev_rule
        return 0
    end

    # Install via AUR helper or manual DKMS
    if test -n "$AUR_HELPER"
        log "Installing acer-wmi-battery via $AUR_HELPER..."
        run $AUR_HELPER -S --needed --noconfirm acer-wmi-battery-dkms-git
    else
        log "No AUR helper found. Installing manually from GitHub..."
        set -l src_dir "/usr/src/$_BATTERY_DKMS_NAME-$_BATTERY_DKMS_VERSION"
        run_sudo rm -rf $src_dir
        run_sudo git clone $_BATTERY_REPO $src_dir

        # Inject Clang build flags into DKMS config if kernel was built with Clang
        if test "$IS_CLANG_KERNEL" = 1; and test -f "$src_dir/dkms.conf"
            log "Patching dkms.conf with Clang/LLVM build flags..."
            run_sudo sed -i "s|^MAKE\[0\]=.*|& $CLANG_BUILD_FLAGS|" "$src_dir/dkms.conf"
        end

        run_sudo dkms add -m $_BATTERY_DKMS_NAME -v $_BATTERY_DKMS_VERSION 2>/dev/null; or true
        run_sudo dkms install -m $_BATTERY_DKMS_NAME -v $_BATTERY_DKMS_VERSION
    end

    # Load the module
    run_sudo modprobe acer_wmi_battery 2>/dev/null; or true

    # Enable health mode
    if test -f "$_BATTERY_HEALTH_PATH"
        echo 1 | run_sudo tee "$_BATTERY_HEALTH_PATH" > /dev/null
        log "Battery health mode enabled (charge limit: 80%)"
    else
        warn "Battery health mode interface not found after module load."
        warn "This may require a reboot, or your model may not be supported."
    end

    _install_udev_rule

    set -ga INSTALLED_FILES $_BATTERY_UDEV_RULE
    set -ga INSTALLED_DKMS "$_BATTERY_DKMS_NAME/$_BATTERY_DKMS_VERSION"
    mark_reboot_required
end

function _install_udev_rule
    log "Creating udev rule for persistent battery health mode..."
    run_sudo tee "$_BATTERY_UDEV_RULE" > /dev/null <<'UDEV_EOF'
# Acer battery health mode - limit charge to 80%
# Installed by Archer Compatibility Suite
ACTION=="add", SUBSYSTEM=="wmi", DRIVER=="acer-wmi-battery", RUN+="/bin/sh -c 'echo 1 > /sys/bus/wmi/drivers/acer-wmi-battery/health_mode'"
UDEV_EOF
end

function module_uninstall
    log "Disabling battery health mode..."
    if test -f "$_BATTERY_HEALTH_PATH"
        echo 0 | sudo tee "$_BATTERY_HEALTH_PATH" > /dev/null 2>&1; or true
    end

    log "Removing udev rule..."
    sudo rm -f "$_BATTERY_UDEV_RULE"

    log "Removing acer-wmi-battery DKMS module..."
    sudo dkms remove -m $_BATTERY_DKMS_NAME -v $_BATTERY_DKMS_VERSION --all 2>/dev/null; or true
    sudo rm -rf /usr/src/$_BATTERY_DKMS_NAME-$_BATTERY_DKMS_VERSION
    sudo modprobe -r acer_wmi_battery 2>/dev/null; or true
end

function module_verify
    if test -f "$_BATTERY_HEALTH_PATH"
        set -l val (cat "$_BATTERY_HEALTH_PATH" 2>/dev/null)
        if test "$val" = 1
            return 0
        end
    end

    if dkms status 2>/dev/null | grep -q "$_BATTERY_DKMS_NAME.*installed"
        warn "DKMS module installed but health_mode not yet active (reboot required)"
        return 0
    end
    return 1
end
