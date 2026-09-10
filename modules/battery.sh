#!/usr/bin/env bash
# Prefer equivalent charge-limit interfaces before adding a WMI fallback.
MODULE_NAME="Battery Charge Limit"
MODULE_ID="battery"
MODULE_DESCRIPTION="Persistent 80% battery charge limit"
_BATTERY_PACKAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../driver/acer-wmi-battery" && pwd)"
source "$_BATTERY_PACKAGE/source.conf"
source "$_BATTERY_PACKAGE/../../lib/dkms.sh"
_BATTERY_HEALTH_PATH="/sys/bus/wmi/drivers/acer-wmi-battery/health_mode"
_BATTERY_UDEV_RULE="/etc/udev/rules.d/99-acer-battery-health.rules"
_BATTERY_SERVICE="/etc/systemd/system/archer-battery-limit.service"
_BATTERY_LOAD_CONF="/etc/modules-load.d/archer-battery.conf"

module_detect() { [[ "$HAS_BATTERY" -eq 1 ]]; }

_battery_interface() {
    kernel_battery_limit_path "$_BATTERY_HEALTH_PATH"
}

_battery_enable() {
    local path="$1" value=1 rule
    if [[ "$path" == */charge_control_end_threshold ]]; then
        value=80
        rule='ACTION=="add|change", SUBSYSTEM=="power_supply", ATTR{type}=="Battery", TEST=="charge_control_end_threshold", ATTR{charge_control_end_threshold}="80"'
    else
        # Driver-level sysfs groups are not bound WMI device attributes.
        # Run after module initialization, rather than race the module add event.
        _battery_persist_fallback "$path" || return 1
        echo 1 | run_sudo tee "$path" >/dev/null || return 1
        return 0
    fi
    echo "$value" | run_sudo tee "$path" >/dev/null || return 1
    archer_write_config "$_BATTERY_UDEV_RULE" "$rule" || return 1
    run_sudo udevadm control --reload-rules || return 1
    INSTALLED_FILES+=" $_BATTERY_UDEV_RULE"
}

_battery_persist_fallback() {
    local path="$1"
    if [[ -e "$_BATTERY_SERVICE" ]] && ! archer_config_owned "$_BATTERY_SERVICE"; then
        warn "Refusing to overwrite user-managed $_BATTERY_SERVICE"
        return 1
    fi
    run_sudo tee "$_BATTERY_SERVICE" >/dev/null <<SERVICE || return 1
# Installed by Archer Compatibility Suite
[Unit]
Description=Archer battery charge limit
After=systemd-modules-load.service
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo 1 > $path'
[Install]
WantedBy=multi-user.target
SERVICE
    INSTALLED_FILES+=" $_BATTERY_SERVICE"
    run_sudo systemctl daemon-reload || return 1
    run_sudo systemctl enable archer-battery-limit.service || return 1
}

module_check_installed() { _battery_interface >/dev/null; }

module_install() {
    local path
    if path=$(_battery_interface); then
        log "Using existing battery charge-limit interface: $path"
        _battery_enable "$path" || return 1
        return 0
    fi
    EXTERNAL_SRC_ROOT="${EXTERNAL_SRC_ROOT:-/usr/src}" EXTERNAL_PACKAGE="$_BATTERY_PACKAGE"
    external_install_dkms || return 1
    [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
    archer_write_config "$_BATTERY_LOAD_CONF" acer_wmi_battery || return 1
    INSTALLED_FILES+=" $_BATTERY_LOAD_CONF"
    INSTALLED_DKMS+=" $DKMS_NAME/$DKMS_VERSION"
    if [[ ! -d "$KERNEL_MODULES_ROOT/$(uname -r)/kernel" ]]; then
        _battery_persist_fallback "$_BATTERY_HEALTH_PATH" || return 1
        mark_reboot_required
        warn "Running kernel was replaced. Reboot to load the installed module and apply the charge limit."
        return 0
    fi
    if ! run_sudo modprobe acer_wmi_battery; then
        warn "Battery module load failed. Inspect journalctl -k; key rejection requires DKMS Secure Boot key enrollment."
        return 1
    fi
    if ! path=$(_battery_interface); then
        warn "Module loaded without a supported battery limit interface. Check hardware support and journalctl -k."
        return 1
    fi
    _battery_enable "$path"
}

module_uninstall() {
    if [[ -f "$_BATTERY_SERVICE" ]] && archer_config_owned "$_BATTERY_SERVICE"; then
        run_sudo systemctl disable archer-battery-limit.service || return 1
        run_sudo rm -f "$_BATTERY_SERVICE" || return 1
        run_sudo systemctl daemon-reload || return 1
    fi
    # Retain current charge state and external package-managed drivers.
    archer_remove_config "$_BATTERY_UDEV_RULE" "$_BATTERY_LOAD_CONF" || return 1
    run_sudo udevadm control --reload-rules || return 1
    EXTERNAL_SRC_ROOT="${EXTERNAL_SRC_ROOT:-/usr/src}"
    external_uninstall_dkms || return 1
    mark_reboot_required
}

module_verify() {
    local path value
    path=$(_battery_interface) || return 1
    value=$(cat "$path") || return 1
    if [[ "$path" == */charge_control_end_threshold ]]; then [[ "$value" == 80 ]]; else [[ "$value" == 1 ]]; fi
}
