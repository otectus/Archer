#!/usr/bin/env bash
# Module: Camera Enhancement (Virtual Camera)
# Sets up v4l2loopback for virtual camera with background effects

MODULE_NAME="Camera Enhancement (Virtual Camera)"
MODULE_ID="camera-enhance"
MODULE_DESCRIPTION="Virtual camera with background blur (v4l2loopback)"

source "$(dirname "${BASH_SOURCE[0]}")/../lib/kernel.sh"

_CAMERA_DKMS_CONF="/etc/dkms/v4l2loopback.conf"
_CAMERA_PACKAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../driver" && pwd)"
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
    kernel_collect_targets || return 1
    if [[ -f "$_CAMERA_DKMS_CONF" ]] && ! grep -q '^# Archer Compatibility Suite:' "$_CAMERA_DKMS_CONF"; then
        warn "Existing $_CAMERA_DKMS_CONF is user-managed. Merge the target compiler settings from $_CAMERA_PACKAGE/v4l2loopback-dkms.conf before retrying."
        return 1
    fi
    run_sudo mkdir -p "${_CAMERA_DKMS_CONF%/*}" || return 1
    run_sudo cp "$_CAMERA_PACKAGE/v4l2loopback-dkms.conf" "$_CAMERA_DKMS_CONF" || return 1
    INSTALLED_FILES+=" $_CAMERA_DKMS_CONF"
    run_sudo pacman -S --needed --noconfirm v4l2loopback-dkms python-opencv || return 1
    INSTALLED_PACKAGES+=" v4l2loopback-dkms python-opencv"
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
    archer_write_config "$_MODPROBE_CONF" 'options v4l2loopback devices=1 video_nr=10 card_label="Archer Camera" exclusive_caps=1' || return 1
    if [[ ! -d "$KERNEL_MODULES_ROOT/$(uname -r)/kernel" ]]; then
        mark_reboot_required
        warn "Reboot into an installed kernel before loading the virtual camera."
        return 0
    fi

    run_sudo modprobe v4l2loopback devices=1 video_nr=10 card_label="Archer Camera" exclusive_caps=1 || {
        warn "Cannot load v4l2loopback. Inspect journalctl -k for signing rejection or module errors."
        return 1
    }

    if [[ -e /dev/video10 ]]; then
        success "Virtual camera device created at /dev/video10"
    else
        mark_reboot_required
    fi

    log "Select 'Archer Camera' in application settings."

    INSTALLED_FILES+=" $_MODPROBE_CONF"
    # v4l2loopback is package-managed; do not claim ownership of its DKMS tree.
    INSTALLED_PACKAGES+=" v4l2loopback-dkms python-opencv"
}

module_uninstall() {
    if grep -qs '^# Archer Compatibility Suite:' "$_CAMERA_DKMS_CONF"; then
        run_sudo rm -f "$_CAMERA_DKMS_CONF" || return 1
    fi
    # Other applications may be using the package-managed module.
    archer_remove_config "$_MODPROBE_CONF" || return 1
}

module_verify() {
    pacman -Qi v4l2loopback-dkms &>/dev/null && return 0
    warn "v4l2loopback-dkms not installed"
    return 1
}
