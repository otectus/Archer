#!/usr/bin/env bash
# Module: Loopback Camera Setup
# Sets up v4l2loopback; physical capture and optional effects run in the user session.

MODULE_NAME="Loopback Camera Setup"
MODULE_ID="camera-enhance"
MODULE_DESCRIPTION="Virtual output for a selected physical camera and session producer"

source "$(dirname "${BASH_SOURCE[0]}")/../lib/kernel.sh"

_CAMERA_DKMS_CONF="/etc/dkms/v4l2loopback.conf"
_CAMERA_PACKAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../driver" && pwd)"
_CAMERA_LOAD_CONF="/etc/modules-load.d/archer-camera.conf"
_MODPROBE_CONF="/etc/modprobe.d/archer-v4l2loopback.conf"

module_detect() {
    ls /dev/video* &>/dev/null
}

module_check_installed() {
    pacman -Qi v4l2loopback-dkms &>/dev/null
}

module_install() {
    log "Installing virtual camera dependencies..."
    [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0
    kernel_collect_targets "$KERNEL_MODULES_ROOT" || return 1
    if [[ -f "$_CAMERA_DKMS_CONF" ]] && ! grep -q '^# Archer Compatibility Suite:' "$_CAMERA_DKMS_CONF"; then
        warn "Existing $_CAMERA_DKMS_CONF is user-managed. Merge the target compiler settings from $_CAMERA_PACKAGE/v4l2loopback-dkms.conf before retrying."
        return 1
    fi
    run_sudo mkdir -p "${_CAMERA_DKMS_CONF%/*}" || return 1
    run_sudo cp "$_CAMERA_PACKAGE/v4l2loopback-dkms.conf" "$_CAMERA_DKMS_CONF" || return 1
    INSTALLED_FILES+=" $_CAMERA_DKMS_CONF"
    run_sudo pacman -S --needed --noconfirm v4l2loopback-dkms v4l-utils ffmpeg || return 1
    INSTALLED_PACKAGES+=" v4l2loopback-dkms v4l-utils ffmpeg"
    # pacman hooks can warn without failing pacman. Verify every target explicitly.
    local version kernel config
    config=$(pacman -Qlq v4l2loopback-dkms | grep '/dkms.conf$') || return 1
    [[ -f "$config" ]] || { warn "Package has no unique DKMS source config"; return 1; }
    version=$(sed -n 's/^PACKAGE_VERSION="\([^"]*\)"/\1/p' "$config")
    [[ "$version" =~ ^[a-zA-Z0-9._+-]+$ ]] || return 1
    for kernel in "${KERNEL_TARGETS[@]}"; do
        if ! run_sudo dkms install -m v4l2loopback -v "$version" -k "$kernel"; then
            kernel_dkms_diagnostics v4l2loopback "$version" "$kernel"
            return 1
        fi
    done

    log "Configuring v4l2loopback..."
    archer_write_config "$_MODPROBE_CONF" 'options v4l2loopback devices=1 card_label="Archer Camera" exclusive_caps=1' || return 1
    archer_write_config "$_CAMERA_LOAD_CONF" v4l2loopback || return 1
    if [[ ! -d "$KERNEL_MODULES_ROOT/$(uname -r)/kernel" ]]; then
        mark_reboot_required
        warn "Reboot into an installed kernel to create the loopback device."
        return 0
    fi
    if [[ -d /sys/module/v4l2loopback ]]; then
        if ! _camera_device; then
            warn "v4l2loopback is already loaded for another application. Reboot to apply Archer's configuration."
            mark_reboot_required
            return 0
        fi
    else
        run_sudo modprobe v4l2loopback || return 1
        _camera_device || { warn "Archer loopback camera identity could not be verified."; return 1; }
    fi
    log "Loopback setup complete. In Archer, open System → Camera, choose a physical source and start streaming before selecting Archer Camera in an application."

    # v4l2loopback is package-managed; do not claim ownership of its DKMS tree.
    INSTALLED_PACKAGES+=" v4l2loopback-dkms v4l-utils ffmpeg"
}

module_uninstall() {
    if grep -qs '^# Archer Compatibility Suite:' "$_CAMERA_DKMS_CONF"; then
        run_sudo rm -f "$_CAMERA_DKMS_CONF" || return 1
    fi
    # Other applications may be using the package-managed module.
    archer_remove_config "$_MODPROBE_CONF" "$_CAMERA_LOAD_CONF" || return 1
}

_camera_device() {
    local path
    for path in /sys/class/video4linux/video*; do
        [[ -f "$path/name" && "$(cat "$path/name")" == "Archer Camera" ]] || continue
        if v4l2-ctl --device "/dev/${path##*/}" --info 2>/dev/null | grep -qi 'Driver name.*v4l2 loopback'; then
            log "Archer loopback: /dev/${path##*/}"
            return 0
        fi
    done
    return 1
}

module_verify() {
    pacman -Qi v4l2loopback-dkms &>/dev/null && [[ -f "$_CAMERA_LOAD_CONF" ]] || return 1
    _camera_device && return 0
    if [[ "${REBOOT_REQUIRED:-0}" -eq 1 ]]; then
        warn "Camera runtime check pending reboot; boot configuration is installed."
        return 0
    fi
    return 1
}
