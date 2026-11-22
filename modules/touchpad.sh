#!/usr/bin/env bash
# Module: evidence-matched input repair

MODULE_NAME="Targeted input repair"
MODULE_ID="touchpad"
MODULE_DESCRIPTION="Plan a transport-specific input repair from current-boot evidence"

_TOUCHPAD_AMD_CONF="/etc/modprobe.d/touchpad-amd-fix.conf"
_TOUCHPAD_SERVICE="/etc/systemd/system/touchpad-fix.service"
INPUT_DEVICES_FILE="${INPUT_DEVICES_FILE:-/proc/bus/input/devices}"
INPUT_SYS_ROOT="${INPUT_SYS_ROOT:-/sys}"

_has_i2c_touchpad_evidence() {
    local path value
    for path in "$INPUT_SYS_ROOT"/bus/i2c/devices/*/{name,modalias}; do
        [[ -r "$path" ]] || continue
        read -r value < "$path" || continue
        grep -qiE 'touchpad|trackpad|synaptics|elan|SYNA[0-9A-F]|ELAN[0-9A-F]' <<<"$value" && return 0
    done
    return 1
}

_has_ps2_pointing_evidence() {
    [[ -r "$INPUT_DEVICES_FILE" ]] || return 1
    awk 'BEGIN { RS=""; IGNORECASE=1 }
         /Bus=0011/ && /(touchpad|trackpad|mouse|synaptics|elan|pointing)/ { found=1 }
         END { exit !found }' "$INPUT_DEVICES_FILE"
}

_input_plan() {
    INPUT_REPAIR="none"
    INPUT_REASON="No current, transport-specific failure was found"
    read_kernel_log
    [[ "$KERNEL_LOG_AVAILABLE" -eq 1 ]] || return 1
    local devices=""
    [[ -r "$INPUT_DEVICES_FILE" ]] && devices=$(<"$INPUT_DEVICES_FILE")
    if grep -qiE 'touchpad|trackpad|synaptics|elan' <<<"$devices"; then
        INPUT_REASON="A touchpad is currently enumerated; no workaround is appropriate"
        return 1
    fi
    if [[ "${CPU_VENDOR:-}" == AuthenticAMD ]] && _has_i2c_touchpad_evidence &&
       grep -qiE 'i2c_hid_acpi.*(probe.*(failed|defer)|error -110|timeout)|pinctrl_amd.*(failed|error)' <<<"$_CACHED_DMESG"; then
        INPUT_REPAIR="amd-i2c-order"
        INPUT_REASON="Current boot reports an AMD pinctrl/I2C-HID probe failure and no touchpad is enumerated"
        return 0
    fi
    if _has_ps2_pointing_evidence &&
       grep -qiE '(i8042|serio[0-9]+).*(AUX|mouse|touchpad|serio1).*(timeout|failed|error)|(AUX|mouse|touchpad|serio1).*(timeout|failed|error)' <<<"$_CACHED_DMESG"; then
        INPUT_REPAIR="ps2-reset"
        INPUT_REASON="Current boot reports a PS/2 controller failure and no touchpad is enumerated"
        return 0
    fi
    return 1
}

module_detect() { _input_plan; }

module_check_installed() {
    [[ -f "$_TOUCHPAD_AMD_CONF" ]] || [[ -f "$_TOUCHPAD_SERVICE" ]] ||
        grep -qs 'i8042.reset' /etc/default/grub
}

_retire_legacy_bundle() {
    local selected="${1:-none}" rebuild=0
    # The old boot service unloaded shared HID modules. Remove it only through
    # Archer's ownership-aware state helper; administrator edits are retained.
    if [[ -e "$_TOUCHPAD_SERVICE" ]]; then
        module_restore_services || return 1
        module_remove_config "$_TOUCHPAD_SERVICE" || return 1
        [[ "$(detect_init_adapter)" != systemd ]] || run_sudo systemctl daemon-reload || return 1
    fi
    if [[ "$selected" != amd-i2c-order && -e "$_TOUCHPAD_AMD_CONF" ]]; then
        module_remove_config "$_TOUCHPAD_AMD_CONF" || return 1
        rebuild=1
    fi
    if [[ "$selected" != ps2-reset ]]; then
        remove_grub_params "i8042.reset i8042.nomux" || return 1
    fi
    [[ "$rebuild" -eq 0 ]] || rebuild_initramfs || return 1
}

module_install() {
    local planned=0
    _input_plan && planned=1
    _retire_legacy_bundle "$INPUT_REPAIR" || return 1
    if [[ "$planned" -eq 0 ]]; then
        log "$INPUT_REASON. No input workaround was installed."
        MODULE_SKIPPED=1
        return 0
    fi
    log "Selected input repair: $INPUT_REPAIR ($INPUT_REASON)"
    case "$INPUT_REPAIR" in
        amd-i2c-order)
            module_config "$_TOUCHPAD_AMD_CONF" $'# Installed by Archer Compatibility Suite\n# Current-boot AMD I2C-HID probe ordering repair\nsoftdep i2c_hid_acpi pre: pinctrl_amd' || return 1
            INSTALLED_FILES+=" $_TOUCHPAD_AMD_CONF"
            rebuild_initramfs || return 1
            ;;
        ps2-reset)
            add_grub_params "i8042.reset i8042.nomux" || return 1
            ;;
        *) return 1 ;;
    esac
    mark_reboot_required
}

module_uninstall() {
    _retire_legacy_bundle none
}

module_verify() {
    if grep -qiE 'touchpad|trackpad|synaptics|elan' "$INPUT_DEVICES_FILE" 2>/dev/null; then
        return 0
    fi
    warn "No touchpad is currently enumerated; the planned repair may require reboot and physical validation."
    return 1
}
