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
    _battery_persist_fallback || return 1
    run_sudo systemctl start archer-battery-limit.service || return 1
}

_battery_persist_fallback() {
    run_sudo install -d -o root -g root -m 0755 /usr/lib/archer /etc/archer || return 1
    run_sudo install -o root -g root -m 0644 "$SCRIPT_DIR/gui/archer_battery.py" /usr/lib/archer/archer_battery.py || return 1
    INSTALLED_FILES+=" /usr/lib/archer/archer_battery.py"
    archer_write_config "$_BATTERY_SERVICE" '[Unit]
Description=Apply Archer saved battery charge policy
After=systemd-modules-load.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/lib/archer/archer_battery.py
ProtectSystem=full
ReadWritePaths=/etc/archer
[Install]
WantedBy=multi-user.target' || return 1
    archer_write_config "$_BATTERY_UDEV_RULE" 'ACTION=="add|change", SUBSYSTEM=="power_supply", RUN+="/usr/bin/systemctl --no-block start archer-battery-limit.service"' || return 1
    run_sudo udevadm control --reload-rules || return 1
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
        _battery_persist_fallback || return 1
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
    run_sudo rm -f /usr/lib/archer/archer_battery.py || return 1
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
    if [[ "$path" == */charge_control_end_threshold ]]; then [[ "$value" =~ ^(80|100)$ ]]; else [[ "$value" =~ ^[01]$ ]]; fi
}
