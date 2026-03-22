#!/usr/bin/env bash
# Module: Camera Enhancement (Virtual Camera)
# Sets up v4l2loopback for virtual camera with background effects

MODULE_NAME="Camera Enhancement (Virtual Camera)"
MODULE_ID="camera-enhance"
MODULE_DESCRIPTION="Virtual camera with background blur (v4l2loopback)"

_MODPROBE_CONF="/etc/modprobe.d/archer-v4l2loopback.conf"

module_detect() {
    ls /dev/video* &>/dev/null
}

module_check_installed() {
    pacman -Qi v4l2loopback-dkms &>/dev/null
}

module_install() {
    log "Installing virtual camera dependencies..."
    run_sudo pacman -S --needed --noconfirm v4l2loopback-dkms python-opencv

    log "Configuring v4l2loopback..."
    run_sudo tee "$_MODPROBE_CONF" > /dev/null <<'MODPROBE_EOF'
options v4l2loopback devices=1 video_nr=10 card_label="Archer Camera" exclusive_caps=1
MODPROBE_EOF

    run_sudo modprobe v4l2loopback devices=1 video_nr=10 card_label="Archer Camera" exclusive_caps=1 2>/dev/null || \
        warn "Could not load v4l2loopback (may require reboot)."

    if [ -e /dev/video10 ]; then
        success "Virtual camera device created at /dev/video10"
    else
        mark_reboot_required
    fi

    log "Select 'Archer Camera' in application settings."

    INSTALLED_FILES+=" $_MODPROBE_CONF"
    INSTALLED_DKMS+=" v4l2loopback/0.13.2"
    INSTALLED_PACKAGES+=" v4l2loopback-dkms python-opencv"
}

module_uninstall() {
    run_sudo modprobe -r v4l2loopback 2>/dev/null || true
    run_sudo rm -f "$_MODPROBE_CONF"
}

module_verify() {
    pacman -Qi v4l2loopback-dkms &>/dev/null && return 0
    warn "v4l2loopback-dkms not installed"
    return 1
}
